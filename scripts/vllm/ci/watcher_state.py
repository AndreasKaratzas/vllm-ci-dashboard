"""Bounded, atomic persistence for the dashboard's issue-watcher ledgers.

The eight ledgers are independently updated by different workflows.  Giving
each one a fixed envelope is what makes their aggregate three-MiB allocation a
real invariant rather than a best-effort final publication check.  Compaction
never removes an open issue mapping or a confirmed/pending incident identity.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from vllm.dashboard_storage_budget import group_max_bytes, writer_max_bytes


ROOT = Path(__file__).resolve().parents[3]
STATE_DIRECTORY = ROOT / "data" / "vllm" / "ci"
RETENTION_KEY = "publication_retention"

WATCHER_STATE_WRITERS = {
    "open_ci_main_failure_issues.json": "ci_main_failure_watcher_state",
    "open_amd_main_failure_issues.json": "amd_main_failure_watcher_state",
    "open_ci_area_regression_issues.json": "ci_area_regression_watcher_state",
    "open_amd_duration_regression_issues.json": "amd_duration_regression_watcher_state",
    "open_agent_health_issues.json": "agent_health_watcher_state",
    "open_omni_surge_issues.json": "omni_surge_watcher_state",
    "open_queue_issues.json": "queue_issue_watcher_state",
    "open_queue_zombie_issues.json": "queue_zombie_watcher_state",
}


class WatcherStateBudgetError(RuntimeError):
    """A protected watcher ledger cannot fit its assigned byte envelope."""


def watcher_state_max_bytes(path: Path | str) -> int:
    name = Path(path).name
    try:
        writer = WATCHER_STATE_WRITERS[name]
    except KeyError as error:
        raise ValueError(f"unrecognized watcher state path: {name}") from error
    return writer_max_bytes(writer)


def watcher_state_allocated_bytes() -> int:
    return sum(writer_max_bytes(name) for name in WATCHER_STATE_WRITERS.values())


def _encoded(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _clean_state(state: dict) -> dict:
    cleaned = copy.deepcopy(state)
    cleaned.pop(RETENTION_KEY, None)
    return cleaned


def _clip(value: Any, limit: int) -> Any:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _field_count(value: Any) -> int:
    """Count nested JSON object fields for exact detail-omission accounting."""
    if isinstance(value, dict):
        return sum(1 + _field_count(child) for child in value.values())
    if isinstance(value, list):
        return sum(_field_count(child) for child in value)
    return 0


def _managed_fields(source: dict) -> dict:
    fields = (
        "schema_version",
        "issue",
        "suppressed",
        "suppressed_fingerprint",
        "last_fingerprint",
        "last_content_fingerprint",
        "last_run",
        "signal_fingerprint_version",
        "body_schema_version",
        "incident_state_version",
        "retirement_streak",
    )
    return {key: copy.deepcopy(source[key]) for key in fields if key in source}


def _main_incident_row(group_id: str, row: dict) -> dict:
    """Keep transition/range state and bounded issue-body presentation only."""
    keys = (
        "transition",
        "build_number",
        "build_url",
        "job_url",
        "observed_at",
        "result",
        "name",
        "hardware",
        "queue",
        "good_commit",
        "good_build_number",
        "bad_commit",
        "bad_build_number",
        "latest_bad_commit",
        "latest_bad_build_number",
        "commit_range_status",
        "compare_url",
        "bisect_command",
    )
    compact = {key: copy.deepcopy(row[key]) for key in keys if key in row}
    compact["group_id"] = group_id
    transition = compact.get("transition")
    if isinstance(transition, dict):
        compact["transition"] = {
            key: copy.deepcopy(transition[key])
            for key in (
                "status",
                "severity",
                "peak_severity",
                "soft_streak",
                "last_eligible_build_id",
                "incident_start_build_id",
                "confirmed_build_id",
            )
            if key in transition
        }
    for key, limit in (
        ("name", 512),
        ("hardware", 128),
        ("queue", 256),
        ("result", 32),
        ("observed_at", 80),
        ("build_url", 2048),
        ("job_url", 2048),
        ("compare_url", 2048),
        ("bisect_command", 512),
        ("good_commit", 64),
        ("bad_commit", 64),
        ("latest_bad_commit", 64),
    ):
        if key in compact:
            compact[key] = _clip(compact[key], limit)
    return compact


def _watermark_row(row: dict) -> dict:
    # Only these values participate in _watermark_rank.  Result, commit and the
    # duplicate timestamps are refetchable presentation detail.
    compact = {}
    if "build_number" in row:
        compact["build_number"] = row["build_number"]
    order_at = row.get("order_at") or row.get("created_at") or row.get("finished_at")
    if order_at not in (None, ""):
        compact["order_at"] = _clip(order_at, 80)
    return compact


def _watermark_rank(item: tuple[str, dict]) -> tuple[str, int, str]:
    group_id, row = item
    number = row.get("build_number")
    return (
        str(row.get("order_at") or row.get("created_at") or row.get("finished_at") or ""),
        int(number) if isinstance(number, int) and not isinstance(number, bool) else 0,
        group_id,
    )


def _compact_main_state(
    source: dict,
    keep_inactive_watermarks: int,
    keep_processed_builds: int,
) -> tuple[dict, dict]:
    active = {
        str(key): _main_incident_row(str(key), row)
        for key, row in (source.get("active") or {}).items()
        if isinstance(row, dict)
    }
    pending = {
        str(key): _main_incident_row(str(key), row)
        for key, row in (source.get("pending_soft") or {}).items()
        if isinstance(row, dict)
    }
    protected = set(active) | set(pending)
    raw_watermarks = {
        str(key): _watermark_row(row)
        for key, row in (source.get("group_watermarks") or {}).items()
        if isinstance(row, dict)
    }
    protected_watermarks = {
        key: value for key, value in raw_watermarks.items() if key in protected
    }
    inactive = sorted(
        ((key, value) for key, value in raw_watermarks.items() if key not in protected),
        key=_watermark_rank,
        reverse=True,
    )
    retained_inactive = inactive[:keep_inactive_watermarks]
    watermarks = {**protected_watermarks, **dict(retained_inactive)}
    raw_processed = [
        number
        for number in source.get("processed_build_numbers") or []
        if isinstance(number, int) and not isinstance(number, bool)
    ]
    processed = sorted(set(raw_processed), reverse=True)[:keep_processed_builds]
    processed.sort()
    compact = _managed_fields(source)
    compact.update(
        {
            "schema_version": source.get("schema_version", 2),
            "initialized": bool(source.get("initialized")),
            "processed_build_numbers": processed,
            "processed_through": {
                key: _clip((source.get("processed_through") or {})[key], 80)
                for key in ("number", "created_at", "finished_at")
                if key in (source.get("processed_through") or {})
            },
            "active": active,
            "pending_soft": pending,
            "group_watermarks": watermarks,
        }
    )
    source_detail_fields = sum(
        _field_count(row)
        for collection in (source.get("active") or {}, source.get("pending_soft") or {})
        for row in collection.values()
        if isinstance(row, dict)
    ) + sum(
        _field_count(row)
        for row in (source.get("group_watermarks") or {}).values()
        if isinstance(row, dict)
    )
    published_detail_fields = sum(_field_count(row) for row in active.values()) + sum(
        _field_count(row) for row in pending.values()
    ) + sum(_field_count(row) for row in watermarks.values())
    return compact, {
        "active": (len(source.get("active") or {}), len(active)),
        "pending_soft": (len(source.get("pending_soft") or {}), len(pending)),
        "group_watermarks": (len(raw_watermarks), len(watermarks)),
        "processed_build_numbers": (
            len(raw_processed),
            len(compact["processed_build_numbers"]),
        ),
        "detail_fields": (source_detail_fields, published_detail_fields),
    }


def _duration_row(row: dict) -> dict:
    keys = (
        "baseline_count",
        "baseline_mins",
        "first_detected_at",
        "hardware",
        "increase_mins",
        "increase_pct",
        "latest_build_number",
        "latest_build_url",
        "latest_job_url",
        "latest_observed_at",
        "name",
        "queue",
        "recent_count",
        "recent_median_mins",
    )
    compact = {key: copy.deepcopy(row[key]) for key in keys if key in row}
    for key, limit in (
        ("name", 512),
        ("hardware", 128),
        ("queue", 256),
        ("latest_observed_at", 80),
        ("first_detected_at", 80),
        ("latest_build_url", 2048),
        ("latest_job_url", 2048),
    ):
        if key in compact:
            compact[key] = _clip(compact[key], limit)
    return compact


def _compact_duration_state(source: dict) -> tuple[dict, dict]:
    raw_active = source.get("active") or {}
    active = {
        str(key): _duration_row(row)
        for key, row in raw_active.items()
        if isinstance(row, dict)
    }
    compact = _managed_fields(source)
    compact["active"] = active
    return compact, {
        "active": (len(raw_active), len(active)),
        "detail_fields": (
            sum(_field_count(row) for row in raw_active.values() if isinstance(row, dict)),
            sum(_field_count(row) for row in active.values()),
        ),
    }


def _compact_signal(signal_key: str, signal: dict) -> dict:
    keys = (
        "status",
        "severity",
        "peak_severity",
        "soft_streak",
        "last_eligible_build_id",
        "incident_start_build_id",
        "confirmed_build_id",
        "build_watermark",
        "identity",
        "evidence",
    )
    compact = {key: copy.deepcopy(signal[key]) for key in keys if key in signal}
    identity = compact.get("identity")
    if isinstance(identity, dict):
        identity = {
            key: copy.deepcopy(identity[key])
            for key in ("id", "label", "area_method")
            if key in identity
        }
        if identity.get("id") == signal_key:
            identity.pop("id", None)
        if "label" in identity:
            identity["label"] = _clip(identity["label"], 512)
        if "area_method" in identity:
            identity["area_method"] = _clip(identity["area_method"], 128)
        compact["identity"] = identity
    evidence = compact.get("evidence")
    if isinstance(evidence, dict):
        evidence = {
            key: copy.deepcopy(evidence[key])
            for key in ("build_number", "observed_at", "url")
            if key in evidence
        }
        if "url" in evidence:
            evidence["url"] = _clip(evidence["url"], 2048)
        compact["evidence"] = evidence
    return compact


def _area_is_protected(area: dict, signals: dict[str, dict]) -> bool:
    return bool(
        area.get("issue")
        or area.get("suppressed")
        or area.get("retirement_streak")
        or any(
            signal.get("status") in {"confirmed", "pending_soft"}
            for signal in signals.values()
        )
    )


def _compact_area_state(source: dict) -> tuple[dict, dict]:
    raw_areas = source.get("areas") or {}
    areas: dict[str, dict] = {}
    source_signals = 0
    published_signals = 0
    source_detail_fields = 0
    published_detail_fields = 0
    for raw_key, raw_area in raw_areas.items():
        if not isinstance(raw_area, dict):
            continue
        key = str(raw_key)
        raw_signals = raw_area.get("signals") or {}
        source_signals += len(raw_signals) if isinstance(raw_signals, dict) else 0
        source_detail_fields += sum(
            _field_count(signal)
            for signal in raw_signals.values()
            if isinstance(signal, dict)
        )
        signals = {
            str(signal_key): _compact_signal(str(signal_key), signal)
            for signal_key, signal in (
                raw_signals.items() if isinstance(raw_signals, dict) else []
            )
            if isinstance(signal, dict)
            and signal.get("status") in {"confirmed", "pending_soft"}
        }
        if (
            not signals
            and (raw_area.get("issue") or raw_area.get("suppressed"))
            and isinstance(raw_signals, dict)
        ):
            # A current-schema managed area with no live signals must remain
            # distinguishable from the pre-transition legacy schema.  The
            # hysteresis migrator intentionally grandfathers a legacy soft
            # issue, so retain one deterministic clear-state sentinel rather
            # than accidentally confirming the next unrelated soft result.
            clear_rows = [
                (str(signal_key), signal)
                for signal_key, signal in raw_signals.items()
                if isinstance(signal, dict) and signal.get("status") == "clear"
            ]
            if clear_rows:
                signal_key, signal = max(
                    clear_rows,
                    key=lambda item: (
                        int(item[1].get("build_watermark") or 0),
                        item[0],
                    ),
                )
                signals[signal_key] = _compact_signal(signal_key, signal)
        if not _area_is_protected(raw_area, signals):
            continue
        area = _managed_fields(raw_area)
        area["signals"] = signals
        areas[key] = area
        published_signals += len(signals)
        published_detail_fields += sum(_field_count(signal) for signal in signals.values())
    return {
        "schema_version": source.get("schema_version", 1),
        "areas": areas,
        "last_run": str(source.get("last_run") or ""),
    }, {
        "areas": (len(raw_areas), len(areas)),
        "signals": (source_signals, published_signals),
        "detail_fields": (source_detail_fields, published_detail_fields),
    }


def _compact_queue_state(source: dict) -> tuple[dict, dict]:
    raw_open = source.get("open") or {}
    open_rows = {}
    detail_source = 0
    detail_published = 0
    for queue, row in raw_open.items():
        if not isinstance(row, dict):
            row = {"number": row}
        compact = {
            key: copy.deepcopy(row[key])
            for key in ("number", "peak_p90", "opened_ts", "last_status_ts")
            if key in row
        }
        open_rows[str(queue)] = compact
        detail_source += _field_count(row)
        detail_published += _field_count(compact)
    raw_suppressed = source.get("suppressed") or {}
    suppressed = {}
    for queue, row in raw_suppressed.items():
        if not isinstance(row, dict):
            row = {"closed_ts": row}
        compact = {
            key: copy.deepcopy(row[key])
            for key in ("closed_ts", "last_number")
            if key in row
        }
        suppressed[str(queue)] = compact
        detail_source += _field_count(row)
        detail_published += _field_count(compact)
    compact = {
        "open": open_rows,
        "suppressed": suppressed,
        "last_run": str(source.get("last_run") or ""),
    }
    return compact, {
        "open": (len(raw_open), len(open_rows)),
        "suppressed": (len(raw_suppressed), len(suppressed)),
        "detail_fields": (detail_source, detail_published),
    }


def _compact_zombie_state(source: dict) -> tuple[dict, dict]:
    raw_open = source.get("open") or {}
    open_rows = {}
    source_fields = 0
    published_fields = 0
    for queue, row in raw_open.items():
        if not isinstance(row, dict):
            row = {"number": row}
        compact = {
            key: copy.deepcopy(row[key])
            for key in ("number", "opened_ts", "last_fingerprint")
            if key in row
        }
        open_rows[str(queue)] = compact
        source_fields += _field_count(row)
        published_fields += _field_count(compact)
    return {
        "open": open_rows,
        "last_run": str(source.get("last_run") or ""),
    }, {
        "open": (len(raw_open), len(open_rows)),
        "detail_fields": (source_fields, published_fields),
    }


def _collection_stats(source: dict, published: dict) -> dict:
    stats = {}
    for name in sorted(set(source) | set(published)):
        source_count, published_count = source.get(name, 0), published.get(name, 0)
        stats[name] = {
            "source": source_count,
            "published": published_count,
            "omitted": max(0, source_count - published_count),
        }
    return stats


def _with_retention(
    payload: dict,
    *,
    source_bytes: int,
    counts: dict[str, tuple[int, int]],
    protected_source: int,
    protected_published: int,
) -> tuple[dict, bytes]:
    published = copy.deepcopy(payload)
    source_counts = {name: values[0] for name, values in counts.items()}
    published_counts = {name: values[1] for name, values in counts.items()}
    collections = _collection_stats(source_counts, published_counts)
    complete = all(row["omitted"] == 0 for row in collections.values())
    retention = {
        "schema_version": 1,
        "complete_relative_to_source": complete,
        "source_bytes": source_bytes,
        "published_bytes": 0,
        "protected_mappings": {
            "source": protected_source,
            "published": protected_published,
            "omitted": max(0, protected_source - protected_published),
        },
        "collections": collections,
    }
    published[RETENTION_KEY] = retention
    for _ in range(8):
        encoded = _encoded(published)
        if retention["published_bytes"] == len(encoded):
            return published, encoded
        retention["published_bytes"] = len(encoded)
    encoded = _encoded(published)
    return published, encoded


def _protected_mapping_count(name: str, source: dict) -> int:
    if name in {
        "open_ci_main_failure_issues.json",
        "open_amd_main_failure_issues.json",
    }:
        return len(source.get("active") or {}) + len(source.get("pending_soft") or {}) + int(
            bool(source.get("issue"))
        )
    if name == "open_amd_duration_regression_issues.json":
        return len(source.get("active") or {}) + int(bool(source.get("issue")))
    if name == "open_ci_area_regression_issues.json":
        total = 0
        for area in (source.get("areas") or {}).values():
            if not isinstance(area, dict):
                continue
            total += int(bool(area.get("issue") or area.get("suppressed")))
            total += sum(
                isinstance(signal, dict)
                and signal.get("status") in {"confirmed", "pending_soft"}
                for signal in (area.get("signals") or {}).values()
            )
        return total
    if name == "open_queue_issues.json":
        return len(source.get("open") or {}) + len(source.get("suppressed") or {})
    if name == "open_queue_zombie_issues.json":
        return len(source.get("open") or {})
    if name == "open_omni_surge_issues.json":
        return int(bool(source.get("open")))
    if name == "open_agent_health_issues.json":
        return int(bool(source.get("issue")))
    return 0


def _full_counts(name: str, source: dict) -> dict[str, tuple[int, int]]:
    names: tuple[str, ...]
    if name in {
        "open_ci_main_failure_issues.json",
        "open_amd_main_failure_issues.json",
    }:
        names = ("active", "pending_soft", "group_watermarks", "processed_build_numbers")
    elif name == "open_amd_duration_regression_issues.json":
        names = ("active",)
    elif name == "open_ci_area_regression_issues.json":
        signal_count = sum(
            len(area.get("signals") or {})
            for area in (source.get("areas") or {}).values()
            if isinstance(area, dict)
        )
        return {
            "areas": (len(source.get("areas") or {}), len(source.get("areas") or {})),
            "signals": (signal_count, signal_count),
        }
    elif name == "open_queue_issues.json":
        names = ("open", "suppressed")
    elif name == "open_queue_zombie_issues.json":
        names = ("open",)
    else:
        names = ()
    return {
        key: (len(source.get(key) or {}), len(source.get(key) or {}))
        for key in names
    }


def bounded_watcher_state(
    path: Path | str,
    state: dict,
    *,
    max_bytes: int | None = None,
    state_filename: str | None = None,
) -> tuple[dict, bytes]:
    """Return a deterministic legal ledger while preserving actionable state."""
    target = Path(path)
    name = state_filename or target.name
    if name not in WATCHER_STATE_WRITERS:
        raise ValueError(f"unrecognized watcher state path: {name}")
    if not isinstance(state, dict):
        raise TypeError("watcher state must be an object")
    limit = watcher_state_max_bytes(name) if max_bytes is None else max_bytes
    source = _clean_state(state)
    source_bytes = len(_encoded(source))
    protected = _protected_mapping_count(name, source)

    full, full_encoded = _with_retention(
        source,
        source_bytes=source_bytes,
        counts=_full_counts(name, source),
        protected_source=protected,
        protected_published=protected,
    )
    if len(full_encoded) <= limit:
        return full, full_encoded

    def candidate(payload: dict, counts: dict[str, tuple[int, int]]) -> tuple[dict, bytes]:
        return _with_retention(
            payload,
            source_bytes=source_bytes,
            counts=counts,
            protected_source=protected,
            protected_published=_protected_mapping_count(name, payload),
        )

    if name in {
        "open_ci_main_failure_issues.json",
        "open_amd_main_failure_issues.json",
    }:
        raw_watermarks = source.get("group_watermarks") or {}
        protected_keys = set(source.get("active") or {}) | set(source.get("pending_soft") or {})
        inactive_count = sum(str(key) not in protected_keys for key in raw_watermarks)

        processed_count = len(source.get("processed_build_numbers") or [])
        can_prune_processed = bool((source.get("processed_through") or {}).get("number"))

        def main_attempt(keep: int, keep_processed: int) -> tuple[dict, bytes]:
            payload, counts = _compact_main_state(source, keep, keep_processed)
            return candidate(payload, counts)

        compact, compact_encoded = main_attempt(inactive_count, processed_count)
        if len(compact_encoded) <= limit:
            return compact, compact_encoded
        retained_processed = processed_count
        if can_prune_processed:
            low, high = 0, processed_count
            processed_best: tuple[dict, bytes] | None = None
            while low <= high:
                middle = (low + high) // 2
                attempt, encoded = main_attempt(inactive_count, middle)
                if len(encoded) <= limit:
                    processed_best = (attempt, encoded)
                    low = middle + 1
                else:
                    high = middle - 1
            if processed_best is not None:
                return processed_best
            retained_processed = 0
        low, high = 0, inactive_count
        watermark_best: tuple[dict, bytes] | None = None
        while low <= high:
            middle = (low + high) // 2
            attempt, encoded = main_attempt(middle, retained_processed)
            if len(encoded) <= limit:
                watermark_best = (attempt, encoded)
                low = middle + 1
            else:
                high = middle - 1
        if watermark_best is not None:
            return watermark_best
        compact, compact_encoded = main_attempt(0, retained_processed)
    elif name == "open_amd_duration_regression_issues.json":
        payload, counts = _compact_duration_state(source)
        compact, compact_encoded = candidate(payload, counts)
    elif name == "open_ci_area_regression_issues.json":
        payload, counts = _compact_area_state(source)
        compact, compact_encoded = candidate(payload, counts)
    elif name == "open_queue_issues.json":
        payload, counts = _compact_queue_state(source)
        compact, compact_encoded = candidate(payload, counts)
    elif name == "open_queue_zombie_issues.json":
        payload, counts = _compact_zombie_state(source)
        compact, compact_encoded = candidate(payload, counts)
    else:
        compact, compact_encoded = full, full_encoded

    if len(compact_encoded) <= limit:
        return compact, compact_encoded
    if _protected_mapping_count(name, compact) != protected:
        raise AssertionError(f"watcher state compaction dropped a protected mapping: {name}")
    raise WatcherStateBudgetError(
        f"{name} protected state cannot fit its {limit}-byte allocation; "
        f"source={source_bytes} compact={len(compact_encoded)} bytes; preserving LKG"
    )


def _aggregate_bytes(path: Path, replacement_size: int) -> int | None:
    try:
        if path.resolve().parent != STATE_DIRECTORY.resolve():
            return None
    except OSError:
        return None
    total = 0
    for basename in WATCHER_STATE_WRITERS:
        candidate = STATE_DIRECTORY / basename
        if candidate.resolve() == path.resolve():
            total += replacement_size
        elif candidate.exists():
            total += candidate.stat().st_size
    return total


def write_watcher_state(
    path: Path | str,
    state: dict,
    *,
    state_filename: str | None = None,
) -> dict:
    """Compact, validate, and atomically replace one watcher ledger."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    published, encoded = bounded_watcher_state(
        target,
        state,
        state_filename=state_filename,
    )
    aggregate = _aggregate_bytes(target, len(encoded))
    aggregate_limit = group_max_bytes("watcher_state")
    if aggregate is not None and aggregate > aggregate_limit:
        raise WatcherStateBudgetError(
            f"watcher state aggregate would exceed {aggregate_limit} bytes: "
            f"{aggregate}; preserving LKG"
        )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return published


if watcher_state_allocated_bytes() > group_max_bytes("watcher_state"):
    raise RuntimeError("watcher state writer allocations exceed their aggregate group")
