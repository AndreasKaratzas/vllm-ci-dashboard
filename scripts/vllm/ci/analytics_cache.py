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
from vllm.pipelines import (
    NIGHTLY_NAME_PATTERNS_BY_SLUG,
    SCHEDULED_GATING_KINDS,
    upstream_scheduled_gating_kind,
)

CACHE_SCHEMA_VERSION = 1
CACHE_KIND = "vllm-ci-analytics-build-cache"
CACHE_MANIFEST_KIND = f"{CACHE_KIND}-manifest"
CACHE_SHARD_KIND = f"{CACHE_KIND}-shard"
CACHE_DIR_NAME = "analytics-builds-v1"
DEFAULT_QUERY_IDENTITY = {"branch": "main", "include_retried_jobs": True}

CACHE_MAX_AGE = timedelta(hours=48)
_FUTURE_SKEW = timedelta(minutes=5)
# Keep each per-pipeline shard comfortably below GitHub's 90 MB repository
# file ceiling. Newly written manifests, monoliths, and shards must each be
# strictly smaller than this bound. A larger legacy monolith remains readable
# up to the aggregate bound so that the next successful write can migrate it.
_MAX_CACHE_BYTES = 64 * 1024 * 1024
_MAX_CACHE_TOTAL_BYTES = 256 * 1024 * 1024
_MAX_CACHE_SHARDS = 4096
_PIPELINES = frozenset(NIGHTLY_NAME_PATTERNS_BY_SLUG)

TERMINAL_BUILD_STATES = frozenset({
    "passed",
    "failed",
    "canceled",
    "skipped",
    "not_run",
})
# Buildkite's Builds API ``finished`` shortcut includes blocked builds. They
# are not pass/fail terminal observations, but a blocked row with a real
# ``finished_at`` is quiescent for cache refresh: a later completion is picked
# up by the collector's exhaustive ``finished_from`` leg.
REFRESH_STABLE_BUILD_STATES = TERMINAL_BUILD_STATES | {"blocked"}
TERMINAL_JOB_STATES = frozenset({
    "passed",
    "failed",
    "timed_out",
    "canceled",
    "cancelled",
    "skipped",
    "blocked",
    "broken",
    "expired",
    "not_run",
    "soft_fail",
    "soft_failed",
    "waiting_failed",
})

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:@/+\-=]{1,512}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHARD_NAME_RE = re.compile(r"^[0-9]{4}\.json$")
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
_MANIFEST_KEYS = frozenset({
    "schema_version",
    "cache_kind",
    "pipeline",
    "query_identity",
    "generated_at",
    "watermark",
    "last_full_at",
    "window_days",
    "complete_from",
    "generation",
    "build_count",
    "shards",
    "integrity",
})
_SHARD_KEYS = frozenset({
    "schema_version",
    "cache_kind",
    "pipeline",
    "generation",
    "index",
    "builds",
    "integrity",
})
_SHARD_DESCRIPTOR_KEYS = frozenset({
    "index",
    "name",
    "bytes",
    "build_count",
    "file_sha256",
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
    parsed: datetime | None
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

    message = build.get("message")
    if message is not None and not isinstance(message, str):
        raise CacheValidationError("malformed_types", "build.message must be a string")

    if "canonical_nightly" in build:
        canonical_nightly = build.get("canonical_nightly")
        if not isinstance(canonical_nightly, bool):
            raise CacheValidationError(
                "malformed_types",
                "canonical_nightly must be boolean",
            )
    else:
        canonical_nightly = bool(
            nightly_pattern and re.search(nightly_pattern, message or "", re.IGNORECASE)
        )
    row["canonical_nightly"] = canonical_nightly

    scheduled_gating_kind = None
    if "scheduled_gating_kind" in build:
        scheduled_gating_kind = build.get("scheduled_gating_kind")
        if (
            pipeline != "ci"
            or not isinstance(scheduled_gating_kind, str)
            or scheduled_gating_kind not in SCHEDULED_GATING_KINDS
        ):
            raise CacheValidationError(
                "malformed_types",
                "scheduled_gating_kind must be an allowlisted upstream kind",
            )
    elif pipeline == "ci":
        scheduled_gating_kind = upstream_scheduled_gating_kind(message)
    if scheduled_gating_kind:
        row["scheduled_gating_kind"] = scheduled_gating_kind

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
        try:
            build_finished = _parse_timestamp(
                build.get("finished_at"),
                "build.finished_at",
                required=False,
            )
        except CacheValidationError:
            build_finished = None

        def job_is_terminal(job: object) -> bool:
            if not isinstance(job, dict):
                return False
            # Only command jobs can contribute the per-job result and retry
            # evidence consumed downstream. Wait, block, and trigger jobs may
            # retain structural waiting states after a build has finished.
            if job.get("type") != "script":
                return True
            if job.get("state") in TERMINAL_JOB_STATES:
                return True
            try:
                return _parse_timestamp(
                    job.get("finished_at"),
                    "job.finished_at",
                    required=False,
                ) is not None
            except CacheValidationError:
                return False

        state = build.get("state")
        blocked_quiescent = state == "blocked" and build_finished is not None
        # A state alone is not a stable terminal boundary: Buildkite can
        # retain it while a blocked continuation is still unresolved. A
        # finished blocked build may contain waiting jobs indefinitely; it is
        # intentionally excluded from pass/fail reliability and needs no
        # detail refresh unless Buildkite later returns it via finished_from.
        if (
            state not in REFRESH_STABLE_BUILD_STATES
            or build_finished is None
            or build.get("jobs_complete") is not True
            or not isinstance(jobs, list)
            or (
                not blocked_quiescent
                and any(
                    not job_is_terminal(job)
                    for job in (jobs if isinstance(jobs, list) else [])
                )
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


def _sealed(payload: dict, *, canonical_sha256: str | None = None) -> dict:
    sealed = dict(payload)
    sealed["integrity"] = {
        "algorithm": "sha256",
        "canonical_sha256": canonical_sha256 or _digest(payload),
    }
    return sealed


def _serialized(payload: dict, *, canonical_sha256: str | None = None) -> bytes:
    return _canonical_json(
        _sealed(payload, canonical_sha256=canonical_sha256)
    ) + b"\n"


def _logical_envelope(
    pipeline: str,
    builds: list[dict],
    *,
    generated_at: str,
    watermark: str,
    last_full_at: str,
    window_days: int,
    complete_from: str,
) -> dict:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "cache_kind": CACHE_KIND,
        "pipeline": pipeline,
        "query_identity": dict(DEFAULT_QUERY_IDENTITY),
        "generated_at": generated_at,
        "watermark": watermark,
        "last_full_at": last_full_at,
        "window_days": window_days,
        "complete_from": complete_from,
        "builds": builds,
    }


def _shard_root(cache_dir: Path, pipeline: str) -> Path:
    path = _cache_path(cache_dir, pipeline)
    return path.parent / f"{pipeline}.shards"


def _generation_dir(cache_dir: Path, pipeline: str, generation: str) -> Path:
    if not _SHA256_RE.fullmatch(generation):
        raise CacheValidationError("malformed_schema", "invalid cache generation")
    return _shard_root(cache_dir, pipeline) / generation


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise CacheValidationError("unsafe_cache_path", "cache parent is not a directory")
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
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _tree_bytes(path: Path) -> int:
    """Return physical file bytes below a cache path, rejecting links/devices."""
    if path.is_symlink():
        raise CacheValidationError("unsafe_file", "cache paths must not be symlinks")
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        raise CacheValidationError("unsafe_file", "cache contains a non-file entry")
    return sum(_tree_bytes(child) for child in path.iterdir())


def _other_pipeline_bytes(cache_dir: Path, pipeline: str) -> int:
    """Count everything the Actions cache would save except this pipeline."""
    cache_dir = Path(cache_dir)
    if not cache_dir.exists():
        return 0
    if cache_dir.is_symlink() or not cache_dir.is_dir():
        raise CacheValidationError("unsafe_cache_path", "cache directory is unsafe")
    owned_names = {f"{pipeline}.json", f"{pipeline}.shards"}
    return sum(
        _tree_bytes(child)
        for child in cache_dir.iterdir()
        if child.name not in owned_names
    )


def _remove_cache_tree(path: Path) -> None:
    """Remove an exact, validated private-cache subtree without following links."""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if not path.exists():
        return
    if not path.is_dir():
        raise CacheValidationError("unsafe_file", "cache contains a non-file entry")
    for child in path.iterdir():
        _remove_cache_tree(child)
    path.rmdir()


def _rollback_uncommitted_generation(
    cache_dir: Path,
    pipeline: str,
    generation: str,
) -> None:
    """Remove only the exact content-addressed generation from a failed write.

    The caller invokes this only before the manifest commit point and only for
    a generation that was not referenced by the previous manifest.  Keeping
    the cleanup this narrow preserves the last readable cache and avoids
    turning an interrupted cache refresh into an ever-growing Actions cache.
    """
    root = _shard_root(cache_dir, pipeline)
    generation_dir = _generation_dir(cache_dir, pipeline, generation)
    if generation_dir.parent != root:
        raise CacheValidationError("unsafe_cache_path", "cache generation escaped shard root")
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise CacheValidationError("unsafe_file", "pipeline shard root is unsafe")
    if generation_dir.is_symlink() or generation_dir.exists():
        _remove_cache_tree(generation_dir)
    # Remove a root created solely for the failed generation, but never touch
    # sibling generations (including the one referenced by the old manifest).
    if root.exists() and not any(root.iterdir()):
        root.rmdir()


def _recognized_manifest_generation(path: Path, pipeline: str) -> str | None:
    """Return the integrity-checked generation referenced by ``path``.

    Invalid and legacy monoliths have no recognized shard generation.  This is
    deliberately narrower than a full cache load: timestamp freshness does not
    affect whether a manifest still owns a generation that rollback must keep.
    """
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise CacheValidationError("unsafe_file", "cache manifest is unsafe")
    if not path.exists() or path.stat().st_size >= _MAX_CACHE_BYTES:
        return None
    try:
        payload = _decode_json(path.read_bytes())
        if not isinstance(payload, dict) or payload.get("cache_kind") != CACHE_MANIFEST_KIND:
            return None
        manifest = _verify_cache_container(
            payload,
            keys=_MANIFEST_KEYS,
            kind=CACHE_MANIFEST_KIND,
            pipeline=pipeline,
            query_identity=True,
        )
    except (CacheValidationError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    generation = manifest.get("generation")
    if not isinstance(generation, str) or not _SHA256_RE.fullmatch(generation):
        return None
    return generation


def _cleanup_pipeline_shards(
    cache_dir: Path,
    pipeline: str,
    *,
    keep_generation: str | None,
    expected_names: set[str] | None = None,
) -> None:
    root = _shard_root(cache_dir, pipeline)
    if not root.exists():
        return
    if root.is_symlink() or not root.is_dir():
        raise CacheValidationError("unsafe_file", "pipeline shard root is unsafe")
    if keep_generation is None:
        _remove_cache_tree(root)
        return
    keep = _generation_dir(cache_dir, pipeline, keep_generation)
    for child in list(root.iterdir()):
        if child != keep:
            _remove_cache_tree(child)
    if expected_names is not None:
        for child in list(keep.iterdir()):
            if child.name not in expected_names:
                _remove_cache_tree(child)


def _shard_bytes(
    pipeline: str,
    generation: str,
    index: int,
    builds: list[dict],
) -> bytes:
    return _serialized({
        "schema_version": CACHE_SCHEMA_VERSION,
        "cache_kind": CACHE_SHARD_KIND,
        "pipeline": pipeline,
        "generation": generation,
        "index": index,
        "builds": builds,
    })


def _partition_shards(
    pipeline: str,
    generation: str,
    builds: list[dict],
) -> list[tuple[list[dict], bytes]]:
    """Greedily partition canonical rows using their exact encoded sizes."""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_content_bytes = 0

    for build in builds:
        encoded_bytes = len(_canonical_json(build))
        index = len(chunks)
        empty_bytes = len(_shard_bytes(pipeline, generation, index, []))
        separator_bytes = 1 if current else 0
        if empty_bytes + current_content_bytes + separator_bytes + encoded_bytes >= _MAX_CACHE_BYTES:
            if not current:
                raise CacheValidationError(
                    "oversize",
                    "one projected build cannot fit in a cache shard",
                )
            chunks.append(current)
            if len(chunks) >= _MAX_CACHE_SHARDS:
                raise CacheValidationError("oversize", "cache has too many shards")
            current = []
            current_content_bytes = 0
            index = len(chunks)
            empty_bytes = len(_shard_bytes(pipeline, generation, index, []))
            if empty_bytes + encoded_bytes >= _MAX_CACHE_BYTES:
                raise CacheValidationError(
                    "oversize",
                    "one projected build cannot fit in a cache shard",
                )
            separator_bytes = 0
        current.append(build)
        current_content_bytes += separator_bytes + encoded_bytes

    if current:
        chunks.append(current)
    if not chunks or len(chunks) > _MAX_CACHE_SHARDS:
        raise CacheValidationError("oversize", "cache cannot be represented as shards")

    serialized_chunks = [
        (chunk, _shard_bytes(pipeline, generation, index, chunk))
        for index, chunk in enumerate(chunks)
    ]
    if any(len(payload) >= _MAX_CACHE_BYTES for _, payload in serialized_chunks):
        raise CacheValidationError("oversize", "cache shard exceeds safety limit")
    return serialized_chunks


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
    """Atomically write one validated private pipeline cache.

    Small projections retain the original single-file representation. Larger
    projections use content-addressed generation shards and publish their
    manifest last, so a failed write cannot invalidate the prior generation.
    """
    path = _cache_path(cache_dir, pipeline)
    watermark = _utc(watermark, "watermark")
    last_full_at = _utc(last_full_at, "last_full_at")
    updated_at = _utc(updated_at, "updated_at")
    if isinstance(window_days, bool) or not isinstance(window_days, int) or window_days <= 0:
        raise CacheValidationError("invalid_argument", "window_days must be positive")
    requested_from = updated_at - timedelta(days=window_days)
    if complete_from is None:
        complete_from = requested_from
    else:
        complete_from = max(_utc(complete_from, "complete_from"), requested_from)
    if complete_from > watermark or watermark > updated_at or last_full_at > updated_at:
        raise CacheValidationError("invalid_metadata", "cache timestamps are inconsistent")

    projected = sanitize_builds(builds, pipeline)
    projected = merge_builds([], projected, cutoff=complete_from)
    generated_at_text = _timestamp(updated_at, "updated_at", required=True)
    watermark_text = _timestamp(watermark, "watermark", required=True)
    last_full_at_text = _timestamp(last_full_at, "last_full_at", required=True)
    complete_from_text = _timestamp(complete_from, "complete_from", required=True)
    assert generated_at_text and watermark_text and last_full_at_text and complete_from_text
    envelope = _logical_envelope(
        pipeline,
        projected,
        generated_at=generated_at_text,
        watermark=watermark_text,
        last_full_at=last_full_at_text,
        window_days=window_days,
        complete_from=complete_from_text,
    )
    generation = _digest(envelope)
    monolith = _serialized(envelope, canonical_sha256=generation)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise CacheValidationError("unsafe_cache_path", "cache directory is unsafe")

    other_bytes = _other_pipeline_bytes(path.parent, pipeline)
    if len(monolith) < _MAX_CACHE_BYTES:
        if other_bytes + len(monolith) > _MAX_CACHE_TOTAL_BYTES:
            raise CacheValidationError("oversize", "aggregate cache exceeds safety limit")
        _atomic_write(path, monolith)
        _cleanup_pipeline_shards(path.parent, pipeline, keep_generation=None)
        return path

    # The oversized legacy serialization is no longer needed. Releasing it
    # before materializing shards keeps peak memory bounded on production-size
    # caches.
    del monolith
    chunks = _partition_shards(pipeline, generation, projected)
    descriptors = []
    for index, (chunk, serialized_chunk) in enumerate(chunks):
        descriptors.append({
            "index": index,
            "name": f"{index:04d}.json",
            "bytes": len(serialized_chunk),
            "build_count": len(chunk),
            "file_sha256": hashlib.sha256(serialized_chunk).hexdigest(),
        })
    manifest = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "cache_kind": CACHE_MANIFEST_KIND,
        "pipeline": pipeline,
        "query_identity": dict(DEFAULT_QUERY_IDENTITY),
        "generated_at": generated_at_text,
        "watermark": watermark_text,
        "last_full_at": last_full_at_text,
        "window_days": window_days,
        "complete_from": complete_from_text,
        "generation": generation,
        "build_count": len(projected),
        "shards": descriptors,
    }
    serialized_manifest = _serialized(manifest)
    if len(serialized_manifest) >= _MAX_CACHE_BYTES:
        raise CacheValidationError("oversize", "cache manifest exceeds safety limit")
    active_bytes = len(serialized_manifest) + sum(
        len(serialized_chunk) for _, serialized_chunk in chunks
    )
    if active_bytes > _MAX_CACHE_TOTAL_BYTES or other_bytes + active_bytes > _MAX_CACHE_TOTAL_BYTES:
        raise CacheValidationError("oversize", "aggregate cache exceeds safety limit")

    previous_generation = _recognized_manifest_generation(path, pipeline)
    # Discard generations left unreferenced by an older interrupted writer
    # before allocating this refresh. The integrity-checked manifest is the
    # sole ownership record, so its generation is the only one preserved.
    _cleanup_pipeline_shards(
        path.parent,
        pipeline,
        keep_generation=previous_generation,
    )
    generation_dir = _generation_dir(path.parent, pipeline, generation)
    shard_root = generation_dir.parent
    shard_root.mkdir(parents=True, exist_ok=True)
    if shard_root.is_symlink() or not shard_root.is_dir():
        raise CacheValidationError("unsafe_cache_path", "pipeline shard root is unsafe")
    # A same-digest directory left by an older interrupted write is not owned
    # by the current manifest. Replace that exact directory before retrying so
    # it cannot carry partial or unexpected files into this generation.
    rollback_generation = generation != previous_generation
    try:
        if rollback_generation and (
            generation_dir.is_symlink() or generation_dir.exists()
        ):
            _remove_cache_tree(generation_dir)
        generation_dir.mkdir(exist_ok=True)
        if generation_dir.is_symlink() or not generation_dir.is_dir():
            raise CacheValidationError("unsafe_cache_path", "cache generation is unsafe")
        for index, (_, serialized_chunk) in enumerate(chunks):
            _atomic_write(generation_dir / f"{index:04d}.json", serialized_chunk)
        # Publishing the manifest is the commit point. Until this replace, a
        # reader continues to see the previous monolith or complete generation.
        _atomic_write(path, serialized_manifest)
    except BaseException:
        if rollback_generation:
            _rollback_uncommitted_generation(path.parent, pipeline, generation)
        raise
    _cleanup_pipeline_shards(
        path.parent,
        pipeline,
        keep_generation=generation,
        expected_names={f"{index:04d}.json" for index in range(len(descriptors))},
    )
    return path


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CacheValidationError("malformed_json", f"duplicate key: {key}")
        result[key] = value
    return result


def _decode_json(raw: bytes) -> object:
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )


def _verify_integrity(payload: dict) -> None:
    integrity = payload.get("integrity")
    if (
        not isinstance(integrity, dict)
        or frozenset(integrity) != {"algorithm", "canonical_sha256"}
    ):
        raise CacheValidationError("malformed_integrity")
    expected = integrity.get("canonical_sha256")
    if (
        integrity.get("algorithm") != "sha256"
        or not isinstance(expected, str)
        or not _SHA256_RE.fullmatch(expected)
    ):
        raise CacheValidationError("malformed_integrity")
    unsigned = dict(payload)
    del unsigned["integrity"]
    if not hmac.compare_digest(expected, _digest(unsigned)):
        raise CacheValidationError("integrity_mismatch")


def _verify_cache_container(
    payload: object,
    *,
    keys: frozenset[str],
    kind: str,
    pipeline: str,
    query_identity: bool,
) -> dict:
    if not isinstance(payload, dict) or frozenset(payload) != keys:
        raise CacheValidationError("malformed_schema")
    if type(payload.get("schema_version")) is not int:
        raise CacheValidationError("schema_mismatch")
    if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise CacheValidationError("schema_mismatch")
    if payload.get("cache_kind") != kind:
        raise CacheValidationError("schema_mismatch")
    if payload.get("pipeline") != pipeline:
        raise CacheValidationError("pipeline_mismatch")
    if query_identity and payload.get("query_identity") != DEFAULT_QUERY_IDENTITY:
        raise CacheValidationError("query_mismatch")
    _verify_integrity(payload)
    return payload


def _load_manifest_builds(
    cache_dir: Path,
    pipeline: str,
    manifest: dict,
    *,
    manifest_bytes: int,
) -> list[dict]:
    generation = manifest.get("generation")
    if not isinstance(generation, str) or not _SHA256_RE.fullmatch(generation):
        raise CacheValidationError("malformed_schema", "invalid cache generation")
    build_count = manifest.get("build_count")
    if isinstance(build_count, bool) or not isinstance(build_count, int) or build_count < 0:
        raise CacheValidationError("malformed_types", "invalid manifest build count")
    descriptors = manifest.get("shards")
    if (
        not isinstance(descriptors, list)
        or not descriptors
        or len(descriptors) > _MAX_CACHE_SHARDS
    ):
        raise CacheValidationError("malformed_schema", "invalid shard list")

    shard_root = _shard_root(cache_dir, pipeline)
    if not shard_root.exists() or shard_root.is_symlink() or not shard_root.is_dir():
        raise CacheValidationError("missing_shard")
    for child in shard_root.iterdir():
        if (
            child.is_symlink()
            or not child.is_dir()
            or not _SHA256_RE.fullmatch(child.name)
        ):
            raise CacheValidationError("unsafe_file", "pipeline shard root is unsafe")

    generation_dir = _generation_dir(cache_dir, pipeline, generation)
    if (
        not generation_dir.exists()
        or generation_dir.is_symlink()
        or not generation_dir.is_dir()
    ):
        raise CacheValidationError("missing_shard")

    expected_names: set[str] = set()
    builds: list[dict] = []
    active_bytes = manifest_bytes
    for expected_index, descriptor in enumerate(descriptors):
        if (
            not isinstance(descriptor, dict)
            or frozenset(descriptor) != _SHARD_DESCRIPTOR_KEYS
        ):
            raise CacheValidationError("malformed_schema", "invalid shard descriptor")
        index = descriptor.get("index")
        name = descriptor.get("name")
        size = descriptor.get("bytes")
        descriptor_build_count = descriptor.get("build_count")
        file_sha256 = descriptor.get("file_sha256")
        if isinstance(index, bool) or index != expected_index:
            raise CacheValidationError("malformed_schema", "non-contiguous shard index")
        expected_name = f"{expected_index:04d}.json"
        if (
            not isinstance(name, str)
            or not _SHARD_NAME_RE.fullmatch(name)
            or name != expected_name
            or name in expected_names
        ):
            raise CacheValidationError("malformed_schema", "invalid shard name")
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
            or size >= _MAX_CACHE_BYTES
        ):
            raise CacheValidationError("oversize", "invalid shard size")
        if (
            isinstance(descriptor_build_count, bool)
            or not isinstance(descriptor_build_count, int)
            or descriptor_build_count <= 0
        ):
            raise CacheValidationError("malformed_types", "invalid shard build count")
        if not isinstance(file_sha256, str) or not _SHA256_RE.fullmatch(file_sha256):
            raise CacheValidationError("malformed_schema", "invalid shard digest")

        expected_names.add(name)
        shard_path = generation_dir / name
        if shard_path.is_symlink() or not shard_path.is_file():
            raise CacheValidationError("missing_shard")
        if shard_path.stat().st_size != size:
            raise CacheValidationError("shard_size_mismatch")
        raw = shard_path.read_bytes()
        if len(raw) != size or len(raw) >= _MAX_CACHE_BYTES:
            raise CacheValidationError("shard_size_mismatch")
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), file_sha256):
            raise CacheValidationError("shard_hash_mismatch")
        shard = _verify_cache_container(
            _decode_json(raw),
            keys=_SHARD_KEYS,
            kind=CACHE_SHARD_KIND,
            pipeline=pipeline,
            query_identity=False,
        )
        if shard.get("generation") != generation or shard.get("index") != expected_index:
            raise CacheValidationError("generation_mismatch")
        shard_builds = shard.get("builds")
        if not isinstance(shard_builds, list):
            raise CacheValidationError("malformed_types", "shard builds must be a list")
        if len(shard_builds) != descriptor_build_count:
            raise CacheValidationError("build_count_mismatch")
        builds.extend(shard_builds)
        active_bytes += len(raw)
        if active_bytes > _MAX_CACHE_TOTAL_BYTES:
            raise CacheValidationError("oversize", "aggregate cache exceeds safety limit")

    actual_names = {child.name for child in generation_dir.iterdir()}
    if actual_names != expected_names:
        raise CacheValidationError("malformed_schema", "cache generation has extra files")
    if len(builds) != build_count:
        raise CacheValidationError("build_count_mismatch")
    return builds


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
    if path.parent.is_symlink() or not path.parent.is_dir():
        return _invalid(path, "unsafe_cache_path")
    if path.is_symlink() or not path.is_file():
        return _invalid(path, "unsafe_file")
    try:
        main_size = path.stat().st_size
        if main_size > _MAX_CACHE_TOTAL_BYTES:
            return _invalid(path, "oversize")
        raw = path.read_bytes()
        if len(raw) != main_size or len(raw) > _MAX_CACHE_TOTAL_BYTES:
            return _invalid(path, "oversize")
        decoded = _decode_json(raw)
        if not isinstance(decoded, dict):
            raise CacheValidationError("malformed_schema")
        cache_kind = decoded.get("cache_kind")
        manifest_generation: str | None = None
        if cache_kind == CACHE_KIND:
            payload = _verify_cache_container(
                decoded,
                keys=_ENVELOPE_KEYS,
                kind=CACHE_KIND,
                pipeline=pipeline,
                query_identity=True,
            )
            build_rows = payload.get("builds")
        elif cache_kind == CACHE_MANIFEST_KIND:
            if len(raw) >= _MAX_CACHE_BYTES:
                raise CacheValidationError("oversize")
            payload = _verify_cache_container(
                decoded,
                keys=_MANIFEST_KEYS,
                kind=CACHE_MANIFEST_KIND,
                pipeline=pipeline,
                query_identity=True,
            )
            manifest_generation = payload.get("generation")
            build_rows = _load_manifest_builds(
                path.parent,
                pipeline,
                payload,
                manifest_bytes=len(raw),
            )
        else:
            raise CacheValidationError("schema_mismatch")

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

        builds = sanitize_builds(build_rows, pipeline)
        if builds != build_rows:
            raise CacheValidationError("noncanonical_projection")
        if manifest_generation is not None:
            logical = _logical_envelope(
                pipeline,
                builds,
                generated_at=payload["generated_at"],
                watermark=payload["watermark"],
                last_full_at=payload["last_full_at"],
                window_days=stored_window,
                complete_from=payload["complete_from"],
            )
            if not hmac.compare_digest(manifest_generation, _digest(logical)):
                raise CacheValidationError("generation_mismatch")
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
