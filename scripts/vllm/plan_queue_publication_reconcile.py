#!/usr/bin/env python3
"""Decide whether durable queue evidence needs canonical publication repair."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Support direct execution as ``python scripts/vllm/plan_queue_...py``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vllm import plan_dns_publication_reconcile as _status_contract  # noqa: E402
from vllm import plan_publication_watchdog as _workflow_runs_contract  # noqa: E402


STATUS_MAX_BYTES = _status_contract.STATUS_MAX_BYTES
QUEUE_DATA_MAX_BYTES = 8 * 1024 * 1024
QUEUE_JOB_LIST_MAX_ITEMS = 50_000
PUBLICATION_MODES = _status_contract.PUBLICATION_MODES
PUBLICATION_STATUSES = _status_contract.PUBLICATION_STATUSES
PUBLICATION_SURFACE_LABELS = _status_contract.PUBLICATION_SURFACE_LABELS
PUBLICATION_REQUIRED_FIELDS = _status_contract.PUBLICATION_REQUIRED_FIELDS
CANONICAL_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
QUEUE_RECONCILIATION_ACTIVE_MAX_AGE_MINUTES = 75
INCIDENT_RECOVERY_MAX_PUBLICATION_AGE = timedelta(
    hours=_status_contract.DEFAULT_MAX_PUBLICATION_AGE_HOURS
)


@dataclass(frozen=True)
class ReconciliationDispatchDecision:
    required: bool
    reason: str
    recovery_key: str


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
    raw = value.get("metrics_observed_at") if "metrics_observed_at" in value else value.get("ts")
    return _canonical_timestamp(raw)


def queue_reconciliation_key(target_queue_generation: str) -> str:
    """Return the exact run-title identity for one queue publication target."""
    if _canonical_timestamp(target_queue_generation) is None:
        raise ValueError("target queue generation must be canonical UTC")
    material = f"queue-publication-reconciliation-v1\0{target_queue_generation}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def reconciliation_dispatch_decision(
    reconciliation_reason: str | None,
    workflow_runs_payload: object,
    *,
    target_queue_generation: str,
    now: datetime,
    active_run_max_age_minutes: int = QUEUE_RECONCILIATION_ACTIVE_MAX_AGE_MINUTES,
) -> ReconciliationDispatchDecision:
    """Suppress only a live run for the exact same queue generation.

    GitHub concurrency permits one active and one pending workflow in a group,
    so it cannot by itself deduplicate repeated dispatches.  The exact recovery
    key is embedded in the Data Collection run title and remains visible in the
    bounded Actions run-list response.  Invalid or unavailable run state fails
    open to dispatch: a duplicate repair is preferable to losing publication.
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if (
        isinstance(active_run_max_age_minutes, bool)
        or not isinstance(active_run_max_age_minutes, int)
        or active_run_max_age_minutes <= 0
    ):
        raise ValueError("active run max age must be a positive integer")
    now = now.astimezone(timezone.utc)
    target = _canonical_timestamp(target_queue_generation)
    if target is None or target > now:
        raise ValueError("target queue generation must be canonical and not future-dated")
    recovery_key = queue_reconciliation_key(target_queue_generation)
    if reconciliation_reason is None:
        return ReconciliationDispatchDecision(False, "target-current", recovery_key)
    if (
        not isinstance(reconciliation_reason, str)
        or not reconciliation_reason
        or len(reconciliation_reason) > 100
    ):
        raise ValueError("reconciliation reason is invalid")

    try:
        runs = _workflow_runs_contract.parse_workflow_runs(
            workflow_runs_payload,
            now=now,
        )
    except ValueError:
        return ReconciliationDispatchDecision(
            True,
            "actions-state-unavailable",
            recovery_key,
        )

    active_cutoff = now - timedelta(minutes=active_run_max_age_minutes)
    for run in runs:
        if run.recovery_key != recovery_key:
            continue
        if (
            run.status in _workflow_runs_contract.QUEUED_RUN_STATUSES
            and run.created_at >= active_cutoff
        ):
            return ReconciliationDispatchDecision(
                False,
                "exact-reconciliation-active",
                recovery_key,
            )
        if run.status == "in_progress":
            active_since = run.run_started_at or run.created_at
            if active_since >= active_cutoff:
                return ReconciliationDispatchDecision(
                    False,
                    "exact-reconciliation-active",
                    recovery_key,
                )
    return ReconciliationDispatchDecision(
        True,
        reconciliation_reason,
        recovery_key,
    )


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


def queue_incident_recovery_eligible(
    payload: object,
    *,
    target_queue_generation: str,
    now: datetime,
) -> bool:
    """Return whether exact queue evidence may credit a queue-only incident.

    A queue target can be current while an unrelated publication surface is
    degraded.  That is a successful queue no-op, but only the exact clean
    publication contract is eligible to mutate recovery state.
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    target = _canonical_timestamp(target_queue_generation)
    if (
        target is None
        or target > now
        or not isinstance(payload, dict)
        or set(payload) != PUBLICATION_REQUIRED_FIELDS
        or not _status_contract._has_valid_contract(payload, now=now)
    ):
        return False
    generated_at = _status_contract._parse_timestamp(payload.get("generated_at"))
    if generated_at is None:
        return False
    canonical_generated_at = generated_at.isoformat().replace("+00:00", "Z")
    if (
        payload.get("generated_at") != canonical_generated_at
        or generated_at < target
        or now - generated_at > INCIDENT_RECOVERY_MAX_PUBLICATION_AGE
    ):
        return False
    expected = {
        "schema_version": 1,
        "status": "healthy",
        "mode": "current",
        "degraded_since": None,
        "uses_fallback": False,
        "publication_blocked": False,
        "affected_surfaces": [],
        "affected_surface_count": 0,
        "fallback_surface_count": 0,
        "fresh_degraded_surface_count": 0,
    }
    return all(payload.get(key) == value for key, value in expected.items())


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


def _append_github_output(
    path: Path,
    *,
    required: bool,
    reason: str,
    dispatch: ReconciliationDispatchDecision | None = None,
) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"required={'true' if required else 'false'}\n")
        handle.write(f"reason={reason}\n")
        if dispatch is not None:
            handle.write(f"dispatch_required={'true' if dispatch.required else 'false'}\n")
            handle.write(f"dispatch_reason={dispatch.reason}\n")
            handle.write(f"recovery_key={dispatch.recovery_key}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--canonical-queue-data", type=Path)
    parser.add_argument("--target-queue-generation")
    parser.add_argument(
        "--workflow-runs",
        type=Path,
        help="bounded Actions run-list response used for exact dispatch deduplication",
    )
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
    if args.workflow_runs is not None and not target_mode:
        parser.error("--workflow-runs requires target reconciliation mode")
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
    dispatch = None
    if args.workflow_runs is not None:
        runs_loaded, runs_payload = _status_contract._load_bounded_json(
            args.workflow_runs,
            max_bytes=_workflow_runs_contract.WORKFLOW_RUNS_MAX_BYTES,
        )
        dispatch = reconciliation_dispatch_decision(
            reason,
            runs_payload if runs_loaded else None,
            target_queue_generation=args.target_queue_generation,
            now=now,
        )
    print(
        "Canonical queue publication reconciliation "
        f"{'required' if required else 'not required'}: {safe_reason}"
    )
    if dispatch is not None:
        print(
            "Canonical queue reconciliation dispatch "
            f"{'required' if dispatch.required else 'not required'}: "
            f"{dispatch.reason}"
        )
    if args.github_output:
        _append_github_output(
            args.github_output,
            required=required,
            reason=safe_reason,
            dispatch=dispatch,
        )
    return 1 if required and args.fail_if_required else 0


if __name__ == "__main__":
    raise SystemExit(main())
