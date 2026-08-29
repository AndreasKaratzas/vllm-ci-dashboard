#!/usr/bin/env python3
"""Decide whether a DNS publish should wake the canonical publisher."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_MAX_PUBLICATION_AGE_HOURS = 3.0
FUTURE_SKEW = timedelta(minutes=5)
STATUS_MAX_BYTES = 64 * 1024
DNS_DATA_MAX_BYTES = 8 * 1024 * 1024
PUBLICATION_MODES = frozenset({"current", "degraded", "fallback", "mixed", "blocked"})
PUBLICATION_STATUSES = frozenset({"healthy", "degraded", "blocked"})
PUBLICATION_SURFACE_LABELS = frozenset(
    {
        "Agent health",
        "CI core health",
        "CI gating",
        "CI health",
        "CI test changes",
        "CI workload hotness",
        "DNS health",
        "Performance evaluation",
        "Project activity",
        "Queue health",
        "Queue lifecycle",
    }
)
PUBLICATION_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "mode",
        "generated_at",
        "degraded_since",
        "uses_fallback",
        "publication_blocked",
        "affected_surfaces",
        "affected_surface_count",
        "fallback_surface_count",
        "fresh_degraded_surface_count",
    }
)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _has_valid_contract(payload: object, *, now: datetime) -> bool:
    if not isinstance(payload, dict) or not PUBLICATION_REQUIRED_FIELDS <= payload.keys():
        return False

    schema_version = payload.get("schema_version")
    mode = payload.get("mode")
    status = payload.get("status")
    blocked = payload.get("publication_blocked")
    uses_fallback = payload.get("uses_fallback")
    affected = payload.get("affected_surfaces")
    affected_count = payload.get("affected_surface_count")
    fallback_count = payload.get("fallback_surface_count")
    fresh_count = payload.get("fresh_degraded_surface_count")
    generated_at = _parse_timestamp(payload.get("generated_at"))
    degraded_since_value = payload.get("degraded_since")
    degraded_since = (
        _parse_timestamp(degraded_since_value) if degraded_since_value is not None else None
    )

    if (
        type(schema_version) is not int
        or schema_version != 1
        or not isinstance(mode, str)
        or mode not in PUBLICATION_MODES
        or not isinstance(status, str)
        or status not in PUBLICATION_STATUSES
        or not isinstance(blocked, bool)
        or not isinstance(uses_fallback, bool)
        or generated_at is None
        or generated_at > now + FUTURE_SKEW
        or (degraded_since_value is not None and degraded_since is None)
        or (degraded_since is not None and degraded_since > now + FUTURE_SKEW)
    ):
        return False

    surface_list_valid = (
        isinstance(affected, list)
        and all(
            isinstance(surface, str) and surface in PUBLICATION_SURFACE_LABELS
            for surface in affected
        )
        and affected == sorted(set(affected))
    )
    counts_valid = all(
        _is_nonnegative_int(value) for value in (affected_count, fallback_count, fresh_count)
    )
    if not surface_list_valid or not counts_valid:
        return False
    if (
        affected_count != len(affected)
        or fallback_count > affected_count
        or fresh_count > affected_count
    ):
        return False

    if mode != "blocked":
        counts_match_mode = {
            "current": affected_count == 0 and fallback_count == 0 and fresh_count == 0,
            "degraded": (
                affected_count > 0 and fallback_count == 0 and fresh_count == affected_count
            ),
            "fallback": (
                affected_count > 0 and fallback_count == affected_count and fresh_count == 0
            ),
            "mixed": (
                fallback_count > 0
                and fresh_count > 0
                and fallback_count + fresh_count == affected_count
            ),
        }[mode]
        if not counts_match_mode:
            return False

    expected_blocked = mode == "blocked"
    expected_status = (
        "blocked"
        if expected_blocked
        else "degraded"
        if mode != "current" or affected_count > 0
        else "healthy"
    )
    return (
        uses_fallback is (mode in {"fallback", "mixed"})
        and blocked is expected_blocked
        and status == expected_status
    )


def reconciliation_reason(
    payload: object,
    *,
    now: datetime,
    max_publication_age_hours: float = DEFAULT_MAX_PUBLICATION_AGE_HOURS,
) -> str | None:
    """Return a bounded reason when canonical publication should be dispatched."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not math.isfinite(max_publication_age_hours) or max_publication_age_hours <= 0:
        raise ValueError("max publication age must be a positive finite number")
    now = now.astimezone(timezone.utc)
    if not _has_valid_contract(payload, now=now):
        return "status-invalid"
    affected = payload.get("affected_surfaces")
    if "DNS health" in affected:
        return "dns-health-affected"
    if payload.get("mode") == "blocked":
        return None
    generated_at = _parse_timestamp(payload.get("generated_at"))
    assert generated_at is not None
    if now - generated_at > timedelta(hours=max_publication_age_hours):
        return "publication-stale"
    return None


def load_reconciliation_reason(
    path: Path,
    *,
    now: datetime,
    max_publication_age_hours: float = DEFAULT_MAX_PUBLICATION_AGE_HOURS,
) -> str | None:
    loaded, payload = _load_bounded_json(path, max_bytes=STATUS_MAX_BYTES)
    if not loaded:
        return "status-unavailable"
    return reconciliation_reason(
        payload,
        now=now,
        max_publication_age_hours=max_publication_age_hours,
    )


def target_reconciliation_reason(
    status_payload: object,
    canonical_dns_payload: object,
    *,
    target_dns_generation: str,
    now: datetime,
    max_publication_age_hours: float = DEFAULT_MAX_PUBLICATION_AGE_HOURS,
) -> str | None:
    """Return why a queued DNS reconciliation still needs a canonical run."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not math.isfinite(max_publication_age_hours) or max_publication_age_hours <= 0:
        raise ValueError("max publication age must be a positive finite number")
    now = now.astimezone(timezone.utc)
    target = _parse_timestamp(target_dns_generation)
    if target is None or target > now + FUTURE_SKEW:
        return "target-invalid"
    if not _has_valid_contract(status_payload, now=now):
        return "status-invalid"
    affected = status_payload.get("affected_surfaces")
    if "DNS health" in affected:
        return "dns-health-affected"
    publication_generated = _parse_timestamp(status_payload.get("generated_at"))
    assert publication_generated is not None
    if publication_generated < target:
        return "publication-before-target"
    if now - publication_generated > timedelta(hours=max_publication_age_hours):
        return "publication-stale"
    if not isinstance(canonical_dns_payload, dict):
        return "canonical-dns-invalid"
    canonical_generated = _parse_timestamp(canonical_dns_payload.get("generated_at"))
    if (
        type(canonical_dns_payload.get("schema_version")) is not int
        or canonical_dns_payload.get("schema_version") != 1
        or canonical_generated is None
        or canonical_generated > now + FUTURE_SKEW
    ):
        return "canonical-dns-invalid"
    if canonical_generated < target:
        return "dns-generation-pending"
    return None


def _load_bounded_json(path: Path, *, max_bytes: int) -> tuple[bool, object]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return False, None
        payload: Any = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        return False, None
    return True, payload


def load_target_reconciliation_reason(
    status_path: Path,
    canonical_dns_path: Path,
    *,
    target_dns_generation: str,
    now: datetime,
    max_publication_age_hours: float = DEFAULT_MAX_PUBLICATION_AGE_HOURS,
) -> str | None:
    status_loaded, status_payload = _load_bounded_json(
        status_path,
        max_bytes=STATUS_MAX_BYTES,
    )
    if not status_loaded:
        return "status-unavailable"
    dns_loaded, canonical_dns_payload = _load_bounded_json(
        canonical_dns_path,
        max_bytes=DNS_DATA_MAX_BYTES,
    )
    if not dns_loaded:
        return "canonical-dns-unavailable"
    return target_reconciliation_reason(
        status_payload,
        canonical_dns_payload,
        target_dns_generation=target_dns_generation,
        now=now,
        max_publication_age_hours=max_publication_age_hours,
    )


def _append_github_output(path: Path, *, required: bool, reason: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"required={'true' if required else 'false'}\n")
        handle.write(f"reason={reason}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--canonical-dns-data", type=Path)
    parser.add_argument("--target-dns-generation")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument(
        "--fail-if-required",
        action="store_true",
        help="exit nonzero when the requested reconciliation target is not satisfied",
    )
    parser.add_argument("--max-age-hours", type=float, default=3.0)
    parser.add_argument("--now", help="timezone-aware ISO-8601 test override")
    args = parser.parse_args()

    now = _parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        parser.error("--now must be a timezone-aware ISO-8601 timestamp")
    if not math.isfinite(args.max_age_hours) or args.max_age_hours <= 0:
        parser.error("--max-age-hours must be positive")

    target_mode = args.canonical_dns_data is not None or args.target_dns_generation is not None
    if target_mode and (args.canonical_dns_data is None or args.target_dns_generation is None):
        parser.error("--canonical-dns-data and --target-dns-generation must be provided together")
    if target_mode:
        reason = load_target_reconciliation_reason(
            args.status,
            args.canonical_dns_data,
            target_dns_generation=args.target_dns_generation,
            now=now,
            max_publication_age_hours=args.max_age_hours,
        )
    else:
        reason = load_reconciliation_reason(
            args.status,
            now=now,
            max_publication_age_hours=args.max_age_hours,
        )
    required = reason is not None
    safe_reason = reason or ("target-current" if target_mode else "canonical-current")
    print(
        "Canonical publication reconciliation "
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
