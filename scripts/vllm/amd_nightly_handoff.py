"""Atomic, bounded handoff of one exhaustive AMD nightly job roster."""

# cspell:ignore CLOEXEC closefd

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vllm.bounded_json import atomic_write_bytes
from vllm.private_ci_cache_budget import PRIVATE_CI_CACHE_BUDGET


AMD_NIGHTLY_HANDOFF_SCHEMA_VERSION = 2
AMD_NIGHTLY_HANDOFF_MAX_BYTES = (
    PRIVATE_CI_CACHE_BUDGET.amd_frozen_nightly_max_bytes
)
_BUILD_FIELDS = frozenset(
    {
        "number",
        "state",
        "branch",
        "commit",
        "created_at",
        "finished_at",
        "message",
        "web_url",
        "jobs",
    }
)
_JOB_FIELDS = frozenset(
    {
        "type",
        "id",
        "name",
        "state",
        "soft_failed",
        "retried_in_job_id",
        "web_url",
        "agent_query_rules",
        "step",
    }
)
_TEXT_BUILD_FIELDS = _BUILD_FIELDS - {"number", "jobs"}
_TEXT_JOB_FIELDS = _JOB_FIELDS - {"soft_failed", "agent_query_rules", "step"}


class AmdNightlyHandoffError(ValueError):
    """The frozen AMD nightly cannot be published or consumed exactly."""


def _validate_text_field(value: object, *, label: str) -> None:
    if not isinstance(value, str):
        raise AmdNightlyHandoffError(f"{label} must be a string")


def compact_amd_build_snapshot(build: dict[str, Any]) -> dict[str, Any]:
    """Return only the PII-free build/job fields required by the matrix."""
    if not isinstance(build, dict):
        raise AmdNightlyHandoffError("AMD frozen-nightly build must be an object")
    number = build.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise AmdNightlyHandoffError(
            "AMD frozen-nightly build number must be a positive integer"
        )
    raw_jobs = build.get("jobs")
    if not isinstance(raw_jobs, list):
        raise AmdNightlyHandoffError(
            "AMD frozen-nightly build must contain an exhaustive jobs list"
        )

    snapshot: dict[str, Any] = {"number": number}
    for key in sorted(_TEXT_BUILD_FIELDS):
        if key not in build or build[key] is None:
            continue
        _validate_text_field(build[key], label=f"AMD frozen-nightly build {key}")
        snapshot[key] = build[key]

    jobs: list[dict[str, Any]] = []
    for index, raw_job in enumerate(raw_jobs):
        if not isinstance(raw_job, dict):
            raise AmdNightlyHandoffError(
                f"AMD frozen-nightly jobs[{index}] must be an object"
            )
        job: dict[str, Any] = {}
        for key in sorted(_TEXT_JOB_FIELDS):
            if key not in raw_job or raw_job[key] is None:
                continue
            _validate_text_field(
                raw_job[key], label=f"AMD frozen-nightly jobs[{index}].{key}"
            )
            job[key] = raw_job[key]
        if "soft_failed" in raw_job and raw_job["soft_failed"] is not None:
            if not isinstance(raw_job["soft_failed"], bool):
                raise AmdNightlyHandoffError(
                    f"AMD frozen-nightly jobs[{index}].soft_failed must be boolean"
                )
            job["soft_failed"] = raw_job["soft_failed"]
        raw_rules = raw_job.get("agent_query_rules")
        if raw_rules is not None:
            if not isinstance(raw_rules, list) or any(
                not isinstance(rule, str) for rule in raw_rules
            ):
                raise AmdNightlyHandoffError(
                    "AMD frozen-nightly agent query rules must be a string list"
                )
            queue_rules = [
                rule for rule in raw_rules if rule.startswith("queue=")
            ]
            if queue_rules:
                job["agent_query_rules"] = queue_rules
        raw_step = raw_job.get("step")
        if raw_step is not None:
            if not isinstance(raw_step, dict):
                raise AmdNightlyHandoffError(
                    f"AMD frozen-nightly jobs[{index}].step must be an object"
                )
            step_id = raw_step.get("id")
            if step_id is not None:
                _validate_text_field(
                    step_id, label=f"AMD frozen-nightly jobs[{index}].step.id"
                )
                if step_id:
                    job["step"] = {"id": step_id}
        jobs.append(job)
    snapshot["jobs"] = jobs
    return snapshot


def _count_entry(source: int, published: int) -> dict[str, Any]:
    omitted = source - published
    return {
        "source": source,
        "published": published,
        "omitted": omitted,
        "complete_relative_to_source": omitted == 0,
    }


def build_amd_nightly_handoff_payload(
    build: dict[str, Any], *, generated_at: str | None = None, max_bytes: int
) -> dict[str, Any]:
    if generated_at is not None and (
        not isinstance(generated_at, str) or not generated_at
    ):
        raise AmdNightlyHandoffError(
            "AMD frozen-nightly generated_at must be a non-empty string"
        )
    compact = compact_amd_build_snapshot(build)
    source_jobs = build["jobs"]
    jobs = compact["jobs"]
    retention = {
        "policy": "exhaustive_allowlisted_frozen_roster_v2",
        "max_bytes": max_bytes,
        "job_rows": _count_entry(len(source_jobs), len(jobs)),
        "complete_relative_to_source": len(source_jobs) == len(jobs),
    }
    if retention["complete_relative_to_source"] is not True:
        raise AmdNightlyHandoffError(
            "AMD frozen-nightly projection omitted a job row"
        )
    return {
        "schema_version": AMD_NIGHTLY_HANDOFF_SCHEMA_VERSION,
        "generated_at": generated_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline": "amd-ci",
        "build": compact,
        "publication_retention": retention,
    }


def _encoded_payload(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def write_amd_nightly_snapshot(
    build: dict[str, Any],
    output_dir: Path,
    *,
    max_bytes: int = AMD_NIGHTLY_HANDOFF_MAX_BYTES,
) -> Path:
    """Atomically freeze an exhaustive roster without replacing LKG on overflow."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("AMD frozen-nightly byte budget must be positive")
    path = Path(output_dir) / ".cache" / "amd_nightly_snapshot.json"
    payload = build_amd_nightly_handoff_payload(build, max_bytes=max_bytes)
    encoded = _encoded_payload(payload)
    if len(encoded) > max_bytes:
        raise RuntimeError(
            "AMD frozen-nightly handoff exceeds its byte budget; preserving "
            f"the last-known-good file: {len(encoded)} > {max_bytes} bytes"
        )
    atomic_write_bytes(path, encoded)
    return path


def _read_bounded_regular_file(path: Path, *, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AmdNightlyHandoffError(
            f"Unable to open frozen AMD build snapshot {path}: {exc}"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AmdNightlyHandoffError(
                f"Frozen AMD build snapshot {path} must be a regular file"
            )
        if metadata.st_size <= 0 or metadata.st_size > max_bytes:
            raise AmdNightlyHandoffError(
                f"Frozen AMD build snapshot {path} exceeds its read bound: "
                f"{metadata.st_size} > {max_bytes} bytes"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(max_bytes + 1)
        if len(raw) != metadata.st_size or len(raw) > max_bytes:
            raise AmdNightlyHandoffError(
                f"Frozen AMD build snapshot {path} changed during its bounded read"
            )
        return raw
    finally:
        os.close(descriptor)


def load_frozen_build_snapshot(
    path: Path,
    expected_build_number: int | str | None,
    *,
    max_bytes: int = AMD_NIGHTLY_HANDOFF_MAX_BYTES,
) -> dict[str, Any] | None:
    """Read and validate the complete frozen roster without an unbounded read."""
    path = Path(path)
    if not path.exists():
        if expected_build_number not in (None, ""):
            raise AmdNightlyHandoffError(
                "Required frozen AMD build snapshot is missing for "
                f"build #{expected_build_number}: {path}"
            )
        return None
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("AMD frozen-nightly byte budget must be positive")
    raw = _read_bounded_regular_file(path, max_bytes=max_bytes)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AmdNightlyHandoffError(
            f"Unable to read frozen AMD build snapshot {path}: {exc}"
        ) from exc
    expected_keys = {
        "schema_version",
        "generated_at",
        "pipeline",
        "build",
        "publication_retention",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise AmdNightlyHandoffError(
            f"Frozen AMD build snapshot {path} has an invalid envelope"
        )
    if payload.get("schema_version") != AMD_NIGHTLY_HANDOFF_SCHEMA_VERSION:
        raise AmdNightlyHandoffError(
            f"Frozen AMD build snapshot {path} must use schema_version "
            f"{AMD_NIGHTLY_HANDOFF_SCHEMA_VERSION}"
        )
    if payload.get("pipeline") != "amd-ci":
        raise AmdNightlyHandoffError(
            f"Frozen AMD build snapshot {path} must identify amd-ci"
        )
    if not isinstance(payload.get("generated_at"), str) or not payload[
        "generated_at"
    ]:
        raise AmdNightlyHandoffError(
            f"Frozen AMD build snapshot {path} has an invalid generated_at"
        )
    retention = payload.get("publication_retention")
    build = payload.get("build")
    if not isinstance(build, dict) or set(build) - _BUILD_FIELDS:
        raise AmdNightlyHandoffError(
            f"Frozen AMD build snapshot {path} has an invalid build object"
        )
    if compact_amd_build_snapshot(build) != build:
        raise AmdNightlyHandoffError(
            f"Frozen AMD build snapshot {path} is not an allowlisted projection"
        )
    jobs = build["jobs"]
    if (
        not isinstance(retention, dict)
        or set(retention)
        != {
            "policy",
            "max_bytes",
            "job_rows",
            "complete_relative_to_source",
        }
        or retention.get("policy") != "exhaustive_allowlisted_frozen_roster_v2"
        or retention.get("max_bytes") != max_bytes
        or retention.get("complete_relative_to_source") is not True
    ):
        raise AmdNightlyHandoffError(
            f"Frozen AMD build snapshot {path} has invalid retention metadata"
        )
    job_rows = retention.get("job_rows")
    if (
        not isinstance(job_rows, dict)
        or set(job_rows)
        != {
            "source",
            "published",
            "omitted",
            "complete_relative_to_source",
        }
        or job_rows != _count_entry(len(jobs), len(jobs))
    ):
        raise AmdNightlyHandoffError(
            f"Frozen AMD build snapshot {path} is not an exhaustive job roster"
        )
    if (
        expected_build_number not in (None, "")
        and str(build.get("number")) != str(expected_build_number)
    ):
        raise AmdNightlyHandoffError(
            "Frozen AMD build snapshot mismatch: "
            f"expected #{expected_build_number}, found #{build.get('number')}"
        )
    return build
