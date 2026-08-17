"""Private, integrity-checked cache for Buildkite analytics collection.

The cache is deliberately a projection rather than a copy of Buildkite API
responses.  Only fields needed by the analytics/reliability producers are
retained, and user-authored or otherwise identifying metadata is discarded.
This module has no public-data writer; callers must place it below
``data/vllm/ci/.cache/analytics-builds-v1``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from vllm.ci.utils import queue_from_rules
from vllm.pipelines import NIGHTLY_NAME_PATTERNS_BY_SLUG

CACHE_SCHEMA_VERSION = 1
CACHE_KIND = "vllm-ci-analytics-build-cache"
CACHE_DIR_NAME = "analytics-builds-v1"
DEFAULT_QUERY_IDENTITY = {"branch": "main", "include_retried_jobs": True}

CACHE_MAX_AGE = timedelta(hours=48)
_FUTURE_SKEW = timedelta(minutes=5)
_MAX_CACHE_BYTES = 256 * 1024 * 1024
_PIPELINES = frozenset(NIGHTLY_NAME_PATTERNS_BY_SLUG)

TERMINAL_BUILD_STATES = frozenset({
    "passed",
    "failed",
    "canceled",
    "skipped",
    "not_run",
})
TERMINAL_JOB_STATES = frozenset({
    "passed",
    "failed",
    "timed_out",
    "canceled",
    "cancelled",
    "skipped",
    "broken",
    "expired",
    "not_run",
    "soft_fail",
    "soft_failed",
})

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:@/+\-=]{1,512}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_ENVELOPE_KEYS = frozenset({
    "schema_version",
    "cache_kind",
    "pipeline",
    "query_identity",
    "generated_at",
    "watermark",
    "last_full_at",
    "window_days",
    "complete_from",
    "builds",
    "integrity",
})


class CacheValidationError(ValueError):
    """A cache payload or projected Buildkite row violates the contract."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason


@dataclass(frozen=True)
class CacheLoad:
    """Fail-closed cache lookup result with machine-readable diagnostics."""

    status: Literal["hit", "miss", "invalid"]
    reason: str
    builds: list[dict]
    watermark: datetime | None = None
    last_full_at: datetime | None = None
    window_days: int | None = None
    complete_from: datetime | None = None
    generated_at: datetime | None = None
    path: Path | None = None

    @property
    def valid(self) -> bool:
        return self.status == "hit"


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CacheValidationError("invalid_argument", f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: object, field: str, *, required: bool) -> datetime | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise CacheValidationError("malformed_types", f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CacheValidationError("malformed_types", f"invalid {field}") from exc
    if parsed.tzinfo is None:
        raise CacheValidationError("malformed_types", f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _timestamp(value: object, field: str, *, required: bool = False) -> str | None:
    if isinstance(value, datetime):
        parsed = _utc(value, field)
    else:
        parsed = _parse_timestamp(value, field, required=required)
    if parsed is None:
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def _cache_path(cache_dir: Path, pipeline: str) -> Path:
    if pipeline not in _PIPELINES:
        raise CacheValidationError("pipeline_mismatch", f"unsupported pipeline: {pipeline!r}")
    cache_dir = Path(cache_dir)
    if cache_dir.name != CACHE_DIR_NAME:
        raise CacheValidationError(
            "unsafe_cache_path",
            f"cache directory must end in {CACHE_DIR_NAME!r}",
        )
    return cache_dir / f"{pipeline}.json"


def _text(value: object, field: str, *, required: bool = False, limit: int = 512) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value):
        raise CacheValidationError("malformed_types", f"{field} must be a string")
    if len(value) > limit or any(ord(char) < 32 for char in value):
        raise CacheValidationError("malformed_types", f"unsafe {field}")
    return value


def _token(value: object, field: str, *, required: bool = False) -> str | None:
    value = _text(value, field, required=required)
    if value is not None and value and not _TOKEN_RE.fullmatch(value):
        raise CacheValidationError("malformed_types", f"invalid {field}")
    return value


def _optional_bool(value: object, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise CacheValidationError("malformed_types", f"{field} must be boolean")
    return value


def _optional_nonnegative_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CacheValidationError("malformed_types", f"{field} must be a non-negative integer")
    return value


def _sanitize_job(job: object, build_number: int) -> dict:
    if not isinstance(job, dict):
        raise CacheValidationError("malformed_types", "job must be an object")

    row: dict = {}
    for key in ("id", "type", "state"):
        value = _token(job.get(key), f"build {build_number} job.{key}", required=key == "state")
        if value:
            row[key] = value
    name = _text(job.get("name"), f"build {build_number} job.name", limit=1024)
    if name:
        row["name"] = name

    soft_failed = _optional_bool(job.get("soft_failed"), "job.soft_failed")
    if soft_failed is not None:
        row["soft_failed"] = soft_failed

    for key in ("runnable_at", "started_at", "finished_at"):
        value = _timestamp(job.get(key), f"job.{key}")
        if value is not None:
            row[key] = value

    queue = job.get("q")
    if queue is None:
        rules = job.get("agent_query_rules")
        if rules is not None and (
            not isinstance(rules, list) or not all(isinstance(rule, str) for rule in rules)
        ):
            raise CacheValidationError("malformed_types", "job.agent_query_rules must be strings")
        queue = queue_from_rules(rules)
    queue = _token(queue, "job.q")
    if queue:
        row["q"] = queue

    step = job.get("step")
    if step is not None:
        if not isinstance(step, dict):
            raise CacheValidationError("malformed_types", "job.step must be an object")
        clean_step = {}
        for key in ("id", "key"):
            value = _token(step.get(key), f"job.step.{key}")
            if value:
                clean_step[key] = value
        if clean_step:
            row["step"] = clean_step

    retried = _optional_bool(job.get("retried"), "job.retried")
    if retried is not None:
        row["retried"] = retried
    retried_in = _token(job.get("retried_in_job_id"), "job.retried_in_job_id")
    if retried_in:
        row["retried_in_job_id"] = retried_in
    retries_count = _optional_nonnegative_int(job.get("retries_count"), "job.retries_count")
    if retries_count is not None:
        row["retries_count"] = retries_count
    retry_type = _token(job.get("retry_type"), "job.retry_type")
    if retry_type:
        row["retry_type"] = retry_type

    retry_source = job.get("retry_source")
    if retry_source is not None:
        if isinstance(retry_source, str):
            source_id = _token(retry_source, "job.retry_source")
        elif isinstance(retry_source, dict):
            source_id = _token(retry_source.get("job_id"), "job.retry_source.job_id")
        else:
            raise CacheValidationError("malformed_types", "job.retry_source must identify a job")
        if source_id:
            row["retry_source"] = {"job_id": source_id}
    return row


def _sanitize_build(build: object, pipeline: str, nightly_pattern: str) -> dict:
    if not isinstance(build, dict):
        raise CacheValidationError("malformed_types", "build must be an object")
    number = build.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise CacheValidationError("malformed_types", "build.number must be a positive integer")
    branch = build.get("branch")
    if branch != DEFAULT_QUERY_IDENTITY["branch"]:
        raise CacheValidationError("query_mismatch", f"build {number} is not on main")

    state = _token(build.get("state"), f"build {number}.state", required=True)
    created_at = _timestamp(build.get("created_at"), f"build {number}.created_at", required=True)
    row = {
        "number": number,
        "branch": branch,
        "state": state,
        "created_at": created_at,
    }
    commit = build.get("commit")
    if commit is not None:
        if not isinstance(commit, str):
            raise CacheValidationError("malformed_types", "build.commit must be a string")
        if _SHA_RE.fullmatch(commit):
            row["commit"] = commit.lower()

    for key in ("started_at", "finished_at"):
        value = _timestamp(build.get(key), f"build {number}.{key}")
        if value is not None:
            row[key] = value

    if "canonical_nightly" in build:
        canonical_nightly = build.get("canonical_nightly")
        if not isinstance(canonical_nightly, bool):
            raise CacheValidationError(
                "malformed_types",
                "canonical_nightly must be boolean",
            )
    else:
        message = build.get("message")
        if message is not None and not isinstance(message, str):
            raise CacheValidationError("malformed_types", "build.message must be a string")
        canonical_nightly = bool(
            nightly_pattern and re.search(nightly_pattern, message or "", re.IGNORECASE)
        )
    row["canonical_nightly"] = canonical_nightly

    raw_jobs = build.get("jobs")
    if raw_jobs is None:
        raw_jobs = []
        jobs_complete = False
    elif not isinstance(raw_jobs, list):
        raise CacheValidationError("malformed_types", "build.jobs must be a list")
    else:
        jobs_complete = build.get("jobs_complete", True)
        if not isinstance(jobs_complete, bool):
            raise CacheValidationError("malformed_types", "build.jobs_complete must be boolean")
    row["jobs_complete"] = jobs_complete
    row["jobs"] = [_sanitize_job(job, number) for job in raw_jobs]
    return row


def sanitize_builds(
    rows: object,
    pipeline: str,
    nightly_pattern: str = "",
) -> list[dict]:
    """Return the deterministic, PII-minimized cache projection."""
    if pipeline not in _PIPELINES:
        raise CacheValidationError("pipeline_mismatch", f"unsupported pipeline: {pipeline!r}")
    if not isinstance(rows, list):
        raise CacheValidationError("malformed_types", "builds must be a list")
    pattern = nightly_pattern or NIGHTLY_NAME_PATTERNS_BY_SLUG[pipeline]
    builds = [_sanitize_build(build, pipeline, pattern) for build in rows]
    numbers = [build["number"] for build in builds]
    if len(numbers) != len(set(numbers)):
        raise CacheValidationError("duplicate_build", "build numbers must be unique")
    builds.sort(key=lambda build: (build["created_at"], build["number"]), reverse=True)
    return builds


def merge_builds(cached: object, fresh: object, *, cutoff: datetime) -> list[dict]:
    """Merge by build number, with fresh rows winning, and prune the window."""
    cutoff = _utc(cutoff, "cutoff")
    if not isinstance(cached, list) or not isinstance(fresh, list):
        raise CacheValidationError("malformed_types", "cached and fresh builds must be lists")
    merged: dict[int, dict] = {}
    for source in (cached, fresh):
        for build in source:
            if not isinstance(build, dict):
                raise CacheValidationError("malformed_types", "build must be an object")
            number = build.get("number")
            if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
                raise CacheValidationError("malformed_types", "build.number must be positive")
            created_at = _parse_timestamp(build.get("created_at"), "build.created_at", required=True)
            assert created_at is not None
            if created_at >= cutoff:
                merged[number] = build
    return sorted(
        merged.values(),
        key=lambda build: (_parse_timestamp(build["created_at"], "build.created_at", required=True), build["number"]),
        reverse=True,
    )


def builds_needing_refresh(builds: object) -> list[int]:
    """Return cached build numbers whose terminal state cannot be trusted."""
    if not isinstance(builds, list):
        raise CacheValidationError("malformed_types", "builds must be a list")
    refresh = set()
    for build in builds:
        if not isinstance(build, dict):
            raise CacheValidationError("malformed_types", "build must be an object")
        number = build.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise CacheValidationError("malformed_types", "build.number must be positive")
        jobs = build.get("jobs")
        if (
            build.get("state") not in TERMINAL_BUILD_STATES
            or build.get("jobs_complete") is not True
            or not isinstance(jobs, list)
            or any(
                not isinstance(job, dict) or job.get("state") not in TERMINAL_JOB_STATES
                for job in (jobs if isinstance(jobs, list) else [])
            )
        ):
            refresh.add(number)
    return sorted(refresh)


def _canonical_json(payload: object) -> bytes:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CacheValidationError("malformed_types", "payload is not canonical JSON") from exc


def _digest(payload: dict) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def write_build_cache(
    cache_dir: Path,
    pipeline: str,
    *,
    builds: list[dict],
    watermark: datetime,
    window_days: int,
    last_full_at: datetime,
    updated_at: datetime,
    complete_from: datetime | None = None,
) -> Path:
    """Atomically write one validated private pipeline cache."""
    path = _cache_path(cache_dir, pipeline)
    watermark = _utc(watermark, "watermark")
    last_full_at = _utc(last_full_at, "last_full_at")
    updated_at = _utc(updated_at, "updated_at")
    if isinstance(window_days, bool) or not isinstance(window_days, int) or window_days <= 0:
        raise CacheValidationError("invalid_argument", "window_days must be positive")
    if complete_from is None:
        complete_from = updated_at - timedelta(days=window_days)
    complete_from = _utc(complete_from, "complete_from")
    if complete_from > watermark or watermark > updated_at or last_full_at > updated_at:
        raise CacheValidationError("invalid_metadata", "cache timestamps are inconsistent")

    projected = sanitize_builds(builds, pipeline)
    projected = merge_builds([], projected, cutoff=complete_from)
    envelope = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "cache_kind": CACHE_KIND,
        "pipeline": pipeline,
        "query_identity": dict(DEFAULT_QUERY_IDENTITY),
        "generated_at": _timestamp(updated_at, "updated_at", required=True),
        "watermark": _timestamp(watermark, "watermark", required=True),
        "last_full_at": _timestamp(last_full_at, "last_full_at", required=True),
        "window_days": window_days,
        "complete_from": _timestamp(complete_from, "complete_from", required=True),
        "builds": projected,
    }
    envelope["integrity"] = {"algorithm": "sha256", "canonical_sha256": _digest(envelope)}
    serialized = _canonical_json(envelope) + b"\n"
    if len(serialized) > _MAX_CACHE_BYTES:
        raise CacheValidationError("oversize", "cache exceeds safety limit")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CacheValidationError("malformed_json", f"duplicate key: {key}")
        result[key] = value
    return result


def _invalid(path: Path, reason: str) -> CacheLoad:
    return CacheLoad(status="invalid", reason=reason, builds=[], path=path)


def load_build_cache(
    cache_dir: Path,
    pipeline: str,
    *,
    cutoff: datetime,
    window_days: int,
    ref_now: datetime,
) -> CacheLoad:
    """Load a cache hit, or return a miss/invalid diagnostic without data."""
    cutoff = _utc(cutoff, "cutoff")
    ref_now = _utc(ref_now, "ref_now")
    try:
        path = _cache_path(cache_dir, pipeline)
    except CacheValidationError as exc:
        return CacheLoad(status="invalid", reason=exc.reason, builds=[])
    if not path.exists():
        return CacheLoad(status="miss", reason="not_found", builds=[], path=path)
    if path.is_symlink() or not path.is_file():
        return _invalid(path, "unsafe_file")
    try:
        if path.stat().st_size > _MAX_CACHE_BYTES:
            return _invalid(path, "oversize")
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
        if not isinstance(payload, dict) or frozenset(payload) != _ENVELOPE_KEYS:
            raise CacheValidationError("malformed_schema")
        if payload.get("schema_version") != CACHE_SCHEMA_VERSION or payload.get("cache_kind") != CACHE_KIND:
            raise CacheValidationError("schema_mismatch")
        if payload.get("pipeline") != pipeline:
            raise CacheValidationError("pipeline_mismatch")
        if payload.get("query_identity") != DEFAULT_QUERY_IDENTITY:
            raise CacheValidationError("query_mismatch")
        integrity = payload.get("integrity")
        if not isinstance(integrity, dict) or frozenset(integrity) != {"algorithm", "canonical_sha256"}:
            raise CacheValidationError("malformed_integrity")
        expected = integrity.get("canonical_sha256")
        if integrity.get("algorithm") != "sha256" or not isinstance(expected, str):
            raise CacheValidationError("malformed_integrity")
        unsigned = dict(payload)
        del unsigned["integrity"]
        if not hmac.compare_digest(expected, _digest(unsigned)):
            raise CacheValidationError("integrity_mismatch")

        generated_at = _parse_timestamp(payload.get("generated_at"), "generated_at", required=True)
        watermark = _parse_timestamp(payload.get("watermark"), "watermark", required=True)
        last_full_at = _parse_timestamp(payload.get("last_full_at"), "last_full_at", required=True)
        complete_from = _parse_timestamp(payload.get("complete_from"), "complete_from", required=True)
        assert generated_at and watermark and last_full_at and complete_from
        stored_window = payload.get("window_days")
        if isinstance(stored_window, bool) or not isinstance(stored_window, int) or stored_window <= 0:
            raise CacheValidationError("malformed_types")
        if isinstance(window_days, bool) or not isinstance(window_days, int) or window_days <= 0:
            raise CacheValidationError("invalid_argument")
        if complete_from > watermark or watermark > generated_at or last_full_at > generated_at:
            raise CacheValidationError("invalid_metadata")
        if any(value > ref_now + _FUTURE_SKEW for value in (generated_at, watermark, last_full_at)):
            raise CacheValidationError("future_metadata")
        if ref_now - watermark > CACHE_MAX_AGE:
            raise CacheValidationError("expired")
        if window_days > stored_window:
            raise CacheValidationError("window_expansion")
        if cutoff < complete_from:
            raise CacheValidationError("coverage_gap")

        builds = sanitize_builds(payload.get("builds"), pipeline)
        if builds != payload.get("builds"):
            raise CacheValidationError("noncanonical_projection")
        if any(
            (_parse_timestamp(build["created_at"], "build.created_at", required=True) or complete_from)
            < complete_from
            for build in builds
        ):
            raise CacheValidationError("coverage_violation")
        return CacheLoad(
            status="hit",
            reason="ok",
            builds=builds,
            watermark=watermark,
            last_full_at=last_full_at,
            window_days=stored_window,
            complete_from=complete_from,
            generated_at=generated_at,
            path=path,
        )
    except CacheValidationError as exc:
        return _invalid(path, exc.reason)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return _invalid(path, "malformed_json")
