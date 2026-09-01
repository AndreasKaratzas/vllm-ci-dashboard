#!/usr/bin/env python3
"""Decide whether durable queue evidence needs canonical publication repair."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Support direct execution as ``python scripts/vllm/plan_queue_...py``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vllm import plan_dns_publication_reconcile as _status_contract  # noqa: E402


STATUS_MAX_BYTES = _status_contract.STATUS_MAX_BYTES
QUEUE_DATA_MAX_BYTES = 8 * 1024 * 1024
QUEUE_JOB_LIST_MAX_ITEMS = 50_000
PUBLICATION_MODES = _status_contract.PUBLICATION_MODES
PUBLICATION_STATUSES = _status_contract.PUBLICATION_STATUSES
PUBLICATION_SURFACE_LABELS = _status_contract.PUBLICATION_SURFACE_LABELS
PUBLICATION_REQUIRED_FIELDS = _status_contract.PUBLICATION_REQUIRED_FIELDS
CANONICAL_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _canonical_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or CANONICAL_UTC_RE.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        return None
    return parsed


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _valid_queue_payload(value: object, *, now: datetime) -> bool:
    if not isinstance(value, dict) or not {"ts", "pending", "running"} <= value.keys():
        return False
    details_generated_at = _canonical_timestamp(value.get("ts"))
    metrics_generated_at = _queue_metrics_generation(value)
    if (
        details_generated_at is None
        or details_generated_at > now
        or metrics_generated_at is None
        or metrics_generated_at > now
        or details_generated_at > metrics_generated_at
    ):
        return False
    for field in ("pending", "running"):
        rows = value.get(field)
        if (
            not isinstance(rows, list)
            or len(rows) > QUEUE_JOB_LIST_MAX_ITEMS
            or any(not isinstance(row, dict) for row in rows)
        ):
            return False
    return True


def _queue_metrics_generation(value: object) -> datetime | None:
    """Return the live metrics generation, accepting the legacy ``ts`` shape."""
    if not isinstance(value, dict):
        return None
    raw = (
        value.get("metrics_observed_at")
        if "metrics_observed_at" in value
        else value.get("ts")
    )
    return _canonical_timestamp(raw)


def _load_canonical_queue_json(path: Path) -> tuple[str, object | None]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(QUEUE_DATA_MAX_BYTES + 1)
    except OSError:
        return "unavailable", None
    if len(raw) > QUEUE_DATA_MAX_BYTES:
        return "invalid", None
    try:
        value: Any = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
        if raw != _canonical_json(value):
            return "invalid", None
    except (
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
        OverflowError,
    ):
        return "invalid", None
    return "loaded", value


def reconciliation_reason(payload: object, *, now: datetime) -> str | None:
    """Return why a queue-owned wake-up should run the canonical publisher."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    if not _status_contract._has_valid_contract(payload, now=now):
        return "status-invalid"
    if "Queue health" in payload.get("affected_surfaces", []):
        return "queue-health-affected"
    # Queue wake-ups are deliberately narrower than DNS recovery. Publication
    # age, blocked mode, and unrelated degraded surfaces belong to the normal
    # collection/watchdog lanes and must not duplicate them here.
    return None


def load_reconciliation_reason(path: Path, *, now: datetime) -> str | None:
    loaded, payload = _status_contract._load_bounded_json(
        path,
        max_bytes=STATUS_MAX_BYTES,
    )
    if not loaded:
        return "status-unavailable"
    return reconciliation_reason(payload, now=now)


def target_reconciliation_reason(
    status_payload: object,
    canonical_queue_payload: object,
    *,
    target_queue_generation: str,
    now: datetime,
) -> str | None:
    """Return why one exact queue generation has not reached canonical Pages."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    target = _canonical_timestamp(target_queue_generation)
    if target is None or target > now:
        return "target-invalid"
    if not _status_contract._has_valid_contract(status_payload, now=now):
        return "status-invalid"
    if "Queue health" in status_payload.get("affected_surfaces", []):
        return "queue-health-affected"
    publication_generated = _status_contract._parse_timestamp(status_payload.get("generated_at"))
    assert publication_generated is not None
    if publication_generated < target:
        return "publication-before-target"
    if not _valid_queue_payload(canonical_queue_payload, now=now):
        return "canonical-queue-invalid"
    canonical_generated = _queue_metrics_generation(canonical_queue_payload)
    assert canonical_generated is not None
    if canonical_generated < target:
        return "queue-generation-pending"
    return None


def load_target_reconciliation_reason(
    status_path: Path,
    canonical_queue_path: Path,
    *,
    target_queue_generation: str,
    now: datetime,
) -> str | None:
    status_loaded, status_payload = _status_contract._load_bounded_json(
        status_path,
        max_bytes=STATUS_MAX_BYTES,
    )
    if not status_loaded:
        return "status-unavailable"
    queue_load_status, canonical_queue_payload = _load_canonical_queue_json(canonical_queue_path)
    if queue_load_status == "unavailable":
        return "canonical-queue-unavailable"
    if queue_load_status != "loaded":
        return "canonical-queue-invalid"
    return target_reconciliation_reason(
        status_payload,
        canonical_queue_payload,
        target_queue_generation=target_queue_generation,
        now=now,
    )


def _append_github_output(path: Path, *, required: bool, reason: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"required={'true' if required else 'false'}\n")
        handle.write(f"reason={reason}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--canonical-queue-data", type=Path)
    parser.add_argument("--target-queue-generation")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument(
        "--fail-if-required",
        action="store_true",
        help="exit nonzero when the requested queue reconciliation is still required",
    )
    parser.add_argument("--now", help="timezone-aware ISO-8601 test override")
    args = parser.parse_args()

    now = _status_contract._parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        parser.error("--now must be a timezone-aware ISO-8601 timestamp")

    target_mode = args.canonical_queue_data is not None or args.target_queue_generation is not None
    if target_mode and (args.canonical_queue_data is None or args.target_queue_generation is None):
        parser.error(
            "--canonical-queue-data and --target-queue-generation must be provided together"
        )
    if target_mode:
        reason = load_target_reconciliation_reason(
            args.status,
            args.canonical_queue_data,
            target_queue_generation=args.target_queue_generation,
            now=now,
        )
    else:
        reason = load_reconciliation_reason(args.status, now=now)
    required = reason is not None
    safe_reason = reason or ("target-current" if target_mode else "canonical-current")
    print(
        "Canonical queue publication reconciliation "
        f"{'required' if required else 'not required'}: {safe_reason}"
    )
    if args.github_output:
        _append_github_output(
            args.github_output,
            required=required,
            reason=safe_reason,
        )
    return 1 if required and args.fail_if_required else 0


if __name__ == "__main__":
    raise SystemExit(main())
