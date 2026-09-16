"""Fail-closed public projection for the private CI analytics artifact.

The collector's ``analytics.json`` is also a private reliability state store.
Only the small, explicitly allowlisted browser contract below may cross the
static-site publication boundary.  Unknown fields are intentionally ignored so
new collector ledgers cannot become public by default.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any


PUBLIC_ANALYTICS_PROJECTOR_ID = "public_analytics_v1"
PUBLIC_ANALYTICS_PIPELINES = frozenset({"amd-ci", "ci"})

_PIPELINE_STRING_FIELDS = (
    "display_name",
    "generated_at",
    "transition_policy_id",
    "default_window",
)
_PIPELINE_INTEGER_FIELDS = ("days", "pass_rate_contract_version")
_SUMMARY_INTEGER_FIELDS = (
    "total_builds",
    "terminal_builds",
    "passed",
    "failed",
    "total_jobs_tracked",
    "jobs_with_failures",
    "jobs_with_hard_failures",
    "jobs_with_soft_failures",
)
_SUMMARY_NUMBER_FIELDS = ("build_pass_rate_pct", "pass_rate")
_BUILD_STRING_FIELDS = ("state", "created_at", "date")
_BUILD_INTEGER_FIELDS = ("passed", "failed", "soft_failed", "skipped", "total_jobs")
_FAILURE_INTEGER_FIELDS = ("failed", "soft_failed")


def _require_object(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a JSON object")
    return value


def _require_list(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be a JSON array")
    return value


def _nullable_string(value: object, path: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{path} must be a string or null")
    return value


def _nullable_integer(value: object, path: str) -> int | None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValueError(f"{path} must be an integer or null")
    return value


def _nullable_number(value: object, path: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a finite number or null")
    if not math.isfinite(value):
        raise ValueError(f"{path} must be a finite number or null")
    return value


def _nullable_identifier(value: object, path: str) -> str | int | None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int))):
        raise ValueError(f"{path} must be a string, integer, or null")
    return value


def _copy_fields(
    source: dict[str, Any],
    output: dict[str, Any],
    fields: tuple[str, ...],
    validator: Callable[[object, str], Any],
    path: str,
) -> None:
    for field in fields:
        if field in source:
            output[field] = validator(source[field], f"{path}.{field}")


def _project_summary(value: object, path: str) -> dict[str, Any]:
    source = _require_object(value, path)
    output: dict[str, Any] = {}
    _copy_fields(source, output, _SUMMARY_INTEGER_FIELDS, _nullable_integer, path)
    _copy_fields(source, output, _SUMMARY_NUMBER_FIELDS, _nullable_number, path)
    if "build_pass_rate_basis" in source:
        output["build_pass_rate_basis"] = _nullable_string(
            source["build_pass_rate_basis"],
            f"{path}.build_pass_rate_basis",
        )
    return output


def _project_job(value: object, path: str) -> dict[str, Any]:
    source = _require_object(value, path)
    if "name" not in source or not isinstance(source["name"], str):
        raise ValueError(f"{path}.name must be a string")

    output: dict[str, Any] = {"name": source["name"]}
    for field in ("state", "q"):
        if field in source:
            output[field] = _nullable_string(source[field], f"{path}.{field}")
    if "wait" in source:
        output["wait"] = _nullable_number(source["wait"], f"{path}.wait")
    return output


def _project_build(value: object, path: str, *, include_jobs: bool) -> dict[str, Any]:
    source = _require_object(value, path)
    output: dict[str, Any] = {}
    if "number" in source:
        output["number"] = _nullable_identifier(source["number"], f"{path}.number")
    _copy_fields(source, output, _BUILD_STRING_FIELDS, _nullable_string, path)
    _copy_fields(source, output, _BUILD_INTEGER_FIELDS, _nullable_integer, path)
    if include_jobs and "jobs" in source:
        jobs = _require_list(source["jobs"], f"{path}.jobs")
        output["jobs"] = [
            _project_job(job, f"{path}.jobs[{index}]") for index, job in enumerate(jobs)
        ]
    return output


def _project_builds(value: object, path: str, *, include_jobs: bool) -> list[dict[str, Any]]:
    rows = _require_list(value, path)
    return [
        _project_build(row, f"{path}[{index}]", include_jobs=include_jobs)
        for index, row in enumerate(rows)
    ]


def _project_failure_row(value: object, path: str) -> dict[str, Any]:
    source = _require_object(value, path)
    if "name" not in source or not isinstance(source["name"], str):
        raise ValueError(f"{path}.name must be a string")

    output: dict[str, Any] = {"name": source["name"]}
    _copy_fields(source, output, _FAILURE_INTEGER_FIELDS, _nullable_integer, path)
    if "fail_rate" in source:
        output["fail_rate"] = _nullable_number(source["fail_rate"], f"{path}.fail_rate")
    return output


def _project_failure_ranking(value: object, path: str) -> list[dict[str, Any]]:
    rows = _require_list(value, path)
    return [_project_failure_row(row, f"{path}[{index}]") for index, row in enumerate(rows)]


def _project_duration_row(
    value: object,
    path: str,
    *,
    include_queue_fallback: bool,
) -> dict[str, Any]:
    source = _require_object(value, path)
    if "name" not in source or not isinstance(source["name"], str):
        raise ValueError(f"{path}.name must be a string")

    output: dict[str, Any] = {"name": source["name"]}
    if "median_dur" in source:
        output["median_dur"] = _nullable_number(
            source["median_dur"],
            f"{path}.median_dur",
        )
    if include_queue_fallback:
        if "queues" in source:
            queues = _require_list(source["queues"], f"{path}.queues")
            if not all(isinstance(queue, str) for queue in queues):
                raise ValueError(f"{path}.queues entries must be strings")
            output["queues"] = list(queues)
        if "median_wait" in source:
            output["median_wait"] = _nullable_number(
                source["median_wait"],
                f"{path}.median_wait",
            )
    return output


def _project_duration_ranking(
    value: object,
    path: str,
    *,
    include_queue_fallback: bool,
) -> list[dict[str, Any]]:
    rows = _require_list(value, path)
    return [
        _project_duration_row(
            row,
            f"{path}[{index}]",
            include_queue_fallback=include_queue_fallback,
        )
        for index, row in enumerate(rows)
    ]


def _project_window(value: object, path: str) -> dict[str, Any]:
    source = _require_object(value, path)
    output: dict[str, Any] = {}
    if "summary" in source:
        output["summary"] = _project_summary(source["summary"], f"{path}.summary")
    if "builds" in source:
        output["builds"] = _project_builds(
            source["builds"],
            f"{path}.builds",
            include_jobs=False,
        )
    if "failure_ranking" in source:
        output["failure_ranking"] = _project_failure_ranking(
            source["failure_ranking"],
            f"{path}.failure_ranking",
        )
    if "duration_ranking" in source:
        output["duration_ranking"] = _project_duration_ranking(
            source["duration_ranking"],
            f"{path}.duration_ranking",
            include_queue_fallback=False,
        )
    return output


def _project_windows(value: object, path: str) -> dict[str, dict[str, Any]]:
    source = _require_object(value, path)
    output: dict[str, dict[str, Any]] = {}
    for window_key, window in source.items():
        if not isinstance(window_key, str) or not window_key:
            raise ValueError(f"{path} keys must be non-empty strings")
        output[window_key] = _project_window(window, f"{path}[{window_key!r}]")
    return output


def _project_pipeline(value: object, path: str, pipeline_key: str) -> dict[str, Any]:
    source = _require_object(value, path)
    output: dict[str, Any] = {}

    if "pipeline" in source:
        pipeline = _nullable_string(source["pipeline"], f"{path}.pipeline")
        if pipeline != pipeline_key:
            raise ValueError(f"{path}.pipeline must match its top-level key {pipeline_key!r}")
        output["pipeline"] = pipeline
    _copy_fields(source, output, _PIPELINE_STRING_FIELDS, _nullable_string, path)
    _copy_fields(source, output, _PIPELINE_INTEGER_FIELDS, _nullable_integer, path)

    if "summary" in source:
        output["summary"] = _project_summary(source["summary"], f"{path}.summary")
    if "builds" in source:
        output["builds"] = _project_builds(
            source["builds"],
            f"{path}.builds",
            include_jobs=True,
        )
    if "failure_ranking" in source:
        output["failure_ranking"] = _project_failure_ranking(
            source["failure_ranking"],
            f"{path}.failure_ranking",
        )
    if "duration_ranking" in source:
        output["duration_ranking"] = _project_duration_ranking(
            source["duration_ranking"],
            f"{path}.duration_ranking",
            include_queue_fallback=True,
        )
    if "windows" in source:
        output["windows"] = _project_windows(source["windows"], f"{path}.windows")
    return output


def project_public_analytics(payload: object) -> dict[str, dict[str, Any]]:
    """Return the browser-safe analytics subset without mutating ``payload``.

    Structural fields are optional for compatibility with older or partial
    analytics payloads.  Whenever one is present, however, it must have its
    declared JSON shape; malformed allowlisted data aborts publication.
    """
    source = _require_object(payload, "analytics")
    pipeline_keys = set(source)
    if pipeline_keys != PUBLIC_ANALYTICS_PIPELINES:
        missing = sorted(PUBLIC_ANALYTICS_PIPELINES - pipeline_keys)
        unexpected = sorted(repr(key) for key in pipeline_keys - PUBLIC_ANALYTICS_PIPELINES)
        raise ValueError(
            "analytics must contain exactly the amd-ci and ci pipeline objects; "
            f"missing={missing}, unexpected={unexpected}"
        )
    output: dict[str, dict[str, Any]] = {}
    for pipeline_key, pipeline in source.items():
        if not isinstance(pipeline_key, str) or not pipeline_key:
            raise ValueError("analytics pipeline keys must be non-empty strings")
        output[pipeline_key] = _project_pipeline(
            pipeline,
            f"analytics[{pipeline_key!r}]",
            pipeline_key,
        )
    return output


def compact_public_analytics_json(payload: object) -> str:
    """Project and serialize analytics using the collector's compact format."""
    projected = project_public_analytics(payload)
    return (
        json.dumps(
            projected,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    )
