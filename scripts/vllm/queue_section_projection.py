"""Bounded, truthful projection shared by both Operations queue producers."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Callable

from vllm.operations_bundle_contract import OPERATIONS_CANARY_SECTION_MAX_BYTES


QUEUE_SECTION_MAX_BYTES = OPERATIONS_CANARY_SECTION_MAX_BYTES["queue"]
CANONICAL_AMD_QUEUE_RE = re.compile(r"^amd_mi(?:250|300|355)_(?:1|2|4|8)$")
FIXED_STRING_MAX_CHARS = 512
OMITTED_FIELD_EXAMPLE_LIMIT = 64


def encode_queue_section(payload: dict) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _finite_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    result = float(value)
    return result if math.isfinite(result) else 0.0


def _queue_matches_scope(name: str, scope: str) -> bool:
    normalized = name.strip().lower()
    if re.match(r"^amd_mi355b(?:_|$)", normalized):
        return False
    if scope == "all":
        return True
    if scope == "canonical":
        return CANONICAL_AMD_QUEUE_RE.fullmatch(normalized) is not None
    return normalized == "amd-cpu" or normalized.startswith("amd_")


def _scope_totals(rows: list[tuple[str, Any]]) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for scope in ("canonical", "amd", "all"):
        selected = [
            (name, row)
            for name, row in rows
            if _queue_matches_scope(name, scope)
        ]
        totals[scope] = {
            "queue_count": len(selected),
            "waiting": sum(
                _finite_number(row.get("waiting"))
                for _name, row in selected
                if isinstance(row, dict)
            ),
            "running": sum(
                _finite_number(row.get("running"))
                for _name, row in selected
                if isinstance(row, dict)
            ),
        }
    return totals


def _queue_row_priority(item: tuple[str, Any]) -> tuple[Any, ...]:
    name, row = item
    values = row if isinstance(row, dict) else {}
    active = any(
        _finite_number(values.get(key)) > 0
        for key in ("waiting", "running", "zombie_waiting", "zombie_running")
    )
    normalized = name.strip().lower()
    canonical = CANONICAL_AMD_QUEUE_RE.fullmatch(normalized) is not None
    amd = _queue_matches_scope(normalized, "amd")
    return (-int(active), -int(canonical), -int(amd), normalized, _stable_json(row))


def _job_row_priority(item: tuple[str, int, Any]) -> tuple[Any, ...]:
    state, _index, row = item
    values = row if isinstance(row, dict) else {}
    age_key = "wait_min" if state == "pending" else "run_min"
    return (
        -_finite_number(values.get(age_key)),
        0 if state == "pending" else 1,
        str(values.get("queue") or values.get("q") or "").lower(),
        str(values.get("id") or values.get("job_id") or ""),
        str(values.get("name") or ""),
        _stable_json(row),
    )


def _source_job_counts(queue_jobs: dict) -> tuple[dict[str, int], dict[str, bool]]:
    retention = queue_jobs.get("publication_retention") or {}
    if not isinstance(retention, dict):
        retention = {}
    top_complete = retention.get("complete_relative_to_source") is not False
    counts: dict[str, int] = {}
    complete: dict[str, bool] = {}
    for state in ("pending", "running"):
        input_count = len(queue_jobs.get(state) or [])
        block = retention.get(state) or {}
        if not isinstance(block, dict):
            block = {}
        declared = block.get("source")
        if isinstance(declared, bool) or not isinstance(declared, int):
            declared = input_count
        if declared < input_count:
            raise RuntimeError(
                f"queue_jobs {state} retention source count is below its input rows"
            )
        state_complete = (
            top_complete
            and block.get("complete") is not False
            and block.get("complete_relative_to_source") is not False
            and declared == input_count
        )
        if not state_complete and "source" not in block:
            raise RuntimeError(
                f"queue_jobs {state} retention is incomplete without an exact source count"
            )
        counts[state] = declared
        complete[state] = state_complete
    return counts, complete


def _compact_fixed_fields(
    source: dict,
    *,
    allowed: tuple[str, ...],
    prefix: str,
    omitted: list[str],
) -> dict:
    result: dict[str, Any] = {}
    allowed_set = set(allowed)
    for key, value in source.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if key not in allowed_set:
            omitted.append(path)
            continue
        if isinstance(value, str) and len(value) > FIXED_STRING_MAX_CHARS:
            omitted.append(path)
            continue
        if isinstance(value, float) and not math.isfinite(value):
            omitted.append(path)
            continue
        if value is not None and not isinstance(value, (str, int, float, bool)):
            omitted.append(path)
            continue
        result[key] = value
    return result


def _fixed_retention(omitted: list[str]) -> dict[str, Any]:
    unique = sorted(set(omitted))
    eligible = [path for path in unique if len(path) <= FIXED_STRING_MAX_CHARS]
    examples = eligible[:OMITTED_FIELD_EXAMPLE_LIMIT]
    return {
        "complete_relative_to_source": not unique,
        "omitted_field_count": len(unique),
        "omitted_field_examples": examples,
        "omitted_field_examples_complete": len(examples) == len(unique),
    }


def _max_fitting_candidate(
    total: int,
    build: Callable[[int], dict],
    *,
    max_bytes: int,
) -> dict | None:
    full = build(total)
    if len(encode_queue_section(full)) <= max_bytes:
        return full
    empty = build(0)
    if len(encode_queue_section(empty)) > max_bytes:
        return None
    low = 0
    high = total - 1
    best = empty
    while low <= high:
        middle = (low + high) // 2
        candidate = build(middle)
        if len(encode_queue_section(candidate)) <= max_bytes:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def compact_queue_section(
    queue: dict,
    *,
    max_bytes: int | None = None,
) -> dict:
    """Return a deterministic, truthful queue section within its public cap."""
    if max_bytes is None:
        max_bytes = QUEUE_SECTION_MAX_BYTES
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("queue-section byte budget must be a positive integer")

    source = dict(queue)
    source_snapshot = source.get("snapshot") or {}
    if not isinstance(source_snapshot, dict):
        source_snapshot = {}
    source_queue_jobs = source.get("queue_jobs") or {}
    if not isinstance(source_queue_jobs, dict):
        source_queue_jobs = {}
    source_pressure = source.get("pressure_baseline") or {}
    if not isinstance(source_pressure, dict):
        source_pressure = {}
    raw_queues = source_snapshot.get("queues") or {}
    if not isinstance(raw_queues, dict):
        raw_queues = {}

    queue_rows = sorted(
        ((str(name), row) for name, row in raw_queues.items()),
        key=_queue_row_priority,
    )
    pressure_rows = sorted(
        ((str(name), row) for name, row in source_pressure.items()),
        key=lambda item: (
            _queue_row_priority((item[0], raw_queues.get(item[0]))),
            _stable_json(item[1]),
        ),
    )
    job_rows = sorted(
        (
            (state, index, row)
            for state in ("pending", "running")
            for index, row in enumerate(source_queue_jobs.get(state) or [])
        ),
        key=_job_row_priority,
    )
    job_source_counts, job_source_complete = _source_job_counts(source_queue_jobs)
    exact_scope_totals = _scope_totals(queue_rows)

    def candidate(
        queue_count: int,
        pressure_count: int,
        job_count: int,
        *,
        compact_fixed: bool = False,
    ) -> dict:
        selected_queues = queue_rows[:queue_count]
        selected_queue_names = {name for name, _row in selected_queues}
        selected_jobs = job_rows[:job_count]
        omitted_fixed: list[str] = []
        if compact_fixed:
            snapshot = _compact_fixed_fields(
                {
                    key: value
                    for key, value in source_snapshot.items()
                    if key != "queues"
                },
                allowed=(
                    "ts",
                    "metrics_observed_at",
                    "details_observed_at",
                    "details_status",
                    "details_refresh_attempted_at",
                    "schema_version",
                    "history_mode",
                    "archive_bucket_start",
                    "total_waiting",
                    "total_running",
                    "total_zombie_waiting",
                    "total_zombie_running",
                    "tracked_queue_count",
                ),
                prefix="snapshot",
                omitted=omitted_fixed,
            )
        else:
            snapshot = dict(source_snapshot)
        snapshot["queues"] = dict(selected_queues)
        if compact_fixed:
            jobs = _compact_fixed_fields(
                {
                    key: value
                    for key, value in source_queue_jobs.items()
                    if key not in {"pending", "running", "publication_retention"}
                },
                allowed=(
                    "schema_version",
                    "ts",
                    "metrics_observed_at",
                    "details_observed_at",
                    "details_status",
                    "details_refresh_attempted_at",
                    "details_request_page_cap",
                    "zombie_threshold_min",
                ),
                prefix="queue_jobs",
                omitted=omitted_fixed,
            )
        else:
            jobs = {
                key: value
                for key, value in source_queue_jobs.items()
                if key not in {"pending", "running", "publication_retention"}
            }
        jobs["pending"] = [
            row for state, _index, row in selected_jobs if state == "pending"
        ]
        jobs["running"] = [
            row for state, _index, row in selected_jobs if state == "running"
        ]
        previous_retention_value = source_queue_jobs.get("publication_retention")
        previous_retention = (
            previous_retention_value
            if isinstance(previous_retention_value, dict)
            else {}
        )
        if compact_fixed:
            carried_retention_fields = {
                "policy",
                "max_bytes",
                "queue_snapshot_counts_complete",
                "pending",
                "running",
                "complete_relative_to_source",
            }
            if previous_retention_value and not isinstance(
                previous_retention_value, dict
            ):
                omitted_fixed.append("queue_jobs.publication_retention")
            else:
                omitted_fixed.extend(
                    f"queue_jobs.publication_retention.{key}"
                    for key in previous_retention
                    if key not in carried_retention_fields
                )
            jobs_retention = {}
        else:
            jobs_retention = dict(previous_retention)
        jobs_retention.update({
            "policy": "source_retention_then_priority_whole_job_rows_v1",
            "max_bytes": max_bytes,
            "queue_snapshot_counts_complete": True,
        })
        all_jobs_complete = True
        operation_job_retention: dict[str, dict[str, Any]] = {}
        for state in ("pending", "running"):
            input_count = len(source_queue_jobs.get(state) or [])
            published_count = len(jobs[state])
            source_count = job_source_counts[state]
            complete = job_source_complete[state] and published_count == input_count
            all_jobs_complete = all_jobs_complete and complete
            block = {
                "source": source_count,
                "input": input_count,
                "published": published_count,
                "omitted": source_count - published_count,
                "omitted_before_operations": source_count - input_count,
                "omitted_by_operations": input_count - published_count,
                "complete": complete,
                "complete_relative_to_source": complete,
            }
            jobs_retention[state] = block
            operation_job_retention[state] = dict(block)
        jobs_retention["complete_relative_to_source"] = all_jobs_complete
        jobs["publication_retention"] = jobs_retention

        scope_totals = {
            scope: dict(totals) | {
                "published_queue_count": sum(
                    name in selected_queue_names
                    for name, _row in queue_rows
                    if _queue_matches_scope(name, scope)
                )
            }
            for scope, totals in exact_scope_totals.items()
        }
        queue_rows_complete = queue_count == len(queue_rows)
        pressure_complete = pressure_count == len(pressure_rows)
        result: dict[str, Any]
        if compact_fixed:
            result = {"history": []}
            if source.get("history") not in (None, []):
                omitted_fixed.append("history")
            history_summary = source.get("history_summary") or {}
            if isinstance(history_summary, dict):
                result["history_summary"] = _compact_fixed_fields(
                    history_summary,
                    allowed=(
                        "snapshot_count",
                        "first_observed_at",
                        "last_observed_at",
                        "counts_only_snapshot_count",
                        "source_path",
                    ),
                    prefix="history_summary",
                    omitted=omitted_fixed,
                )
            elif history_summary:
                omitted_fixed.append("history_summary")
                result["history_summary"] = {}
            for key in source:
                if key not in {
                    "snapshot",
                    "queue_jobs",
                    "history",
                    "history_summary",
                    "pressure_baseline",
                    "operations_publication_retention",
                }:
                    omitted_fixed.append(str(key))
        else:
            result = {
                key: value
                for key, value in source.items()
                if key not in {
                    "snapshot",
                    "queue_jobs",
                    "pressure_baseline",
                    "operations_publication_retention",
                }
            }
        result["snapshot"] = snapshot
        result["queue_jobs"] = jobs
        result["pressure_baseline"] = dict(pressure_rows[:pressure_count])
        result["operations_publication_retention"] = {
            "policy": "current_queues_then_baselines_then_priority_whole_jobs_v1",
            "max_bytes": max_bytes,
            "aggregate_totals_complete": True,
            "scope_totals": scope_totals,
            "snapshot_queues": {
                "source": len(queue_rows),
                "published": queue_count,
                "omitted": len(queue_rows) - queue_count,
                "complete_relative_to_source": queue_rows_complete,
            },
            "pressure_baseline": {
                "source": len(pressure_rows),
                "published": pressure_count,
                "omitted": len(pressure_rows) - pressure_count,
                "complete_relative_to_source": pressure_complete,
            },
            "queue_jobs": operation_job_retention,
            "fixed_metadata": _fixed_retention(omitted_fixed),
            "complete_relative_to_source": (
                queue_rows_complete
                and pressure_complete
                and all_jobs_complete
                and not omitted_fixed
            ),
        }
        return {"queue": result}

    full = candidate(len(queue_rows), len(pressure_rows), len(job_rows))
    if len(encode_queue_section(full)) <= max_bytes:
        return full
    jobs_fit = _max_fitting_candidate(
        len(job_rows),
        lambda count: candidate(len(queue_rows), len(pressure_rows), count),
        max_bytes=max_bytes,
    )
    if jobs_fit is not None:
        return jobs_fit
    compact_jobs_fit = _max_fitting_candidate(
        len(job_rows),
        lambda count: candidate(
            len(queue_rows),
            len(pressure_rows),
            count,
            compact_fixed=True,
        ),
        max_bytes=max_bytes,
    )
    if compact_jobs_fit is not None:
        return compact_jobs_fit
    pressure_fit = _max_fitting_candidate(
        len(pressure_rows),
        lambda count: candidate(
            len(queue_rows), count, 0, compact_fixed=True
        ),
        max_bytes=max_bytes,
    )
    if pressure_fit is not None:
        return pressure_fit
    queues_fit = _max_fitting_candidate(
        len(queue_rows),
        lambda count: candidate(count, 0, 0, compact_fixed=True),
        max_bytes=max_bytes,
    )
    if queues_fit is not None:
        return queues_fit

    irreducible = candidate(0, 0, 0, compact_fixed=True)
    raise RuntimeError(
        "queue-section fixed metadata exceeds its byte budget; preserving the "
        f"last-known-good file: {len(encode_queue_section(irreducible))} > "
        f"{max_bytes} bytes"
    )
