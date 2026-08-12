#!/usr/bin/env python3
"""Collect exact-time lifecycle observations for twelve target AMD queues.

Buildkite's public GraphQL API does not expose the historical Cluster Insights
time series.  This collector builds an auditable local series from command-job
timestamps instead.  It deliberately keeps the three concepts separate:

* ``incoming`` is a direct ``runnable_at`` event;
* ``served`` is a direct ``started_at`` event; and
* ``completed`` is a direct ``finished_at`` event.

The compact daily gzip job segments are published atomically after a stable-ID
merge and seven-day prune. Publishing is fail-closed: incomplete pagination,
unresolved target queues, missing job UUIDs, or malformed retained history
abort before either output is replaced.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow direct execution as ``python scripts/vllm/collect_queue_lifecycle.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.ci.utils import parse_iso, percentile, queue_from_rules  # noqa: E402
from vllm.collect_workload_mapping import (  # noqa: E402
    PER_PAGE as REST_PAGE_SIZE,
    _request_build_page,
)
from vllm.constants import (  # noqa: E402
    AMD_METRIC_TARGET_QUEUES,
    BK_CLUSTER_UUID,
    BK_ORG,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
JOBS_OUTPUT = REPO_ROOT / "data" / "vllm" / "ci" / "queue_lifecycle_jobs"
SUMMARY_OUTPUT = REPO_ROOT / "data" / "vllm" / "ci" / "queue_lifecycle.json"
JOBS_REPO_PATH = "data/vllm/ci/queue_lifecycle_jobs"
SUMMARY_REPO_PATH = "data/vllm/ci/queue_lifecycle.json"

SCHEMA_VERSION = 1
RETENTION_DAYS = 7
ROLLING_WINDOW_HOURS = 2
PARENT_BUILD_LOOKBACK_DAYS = 3
# Ten thousand organization builds per cohort is already far beyond the
# expected retained volume. Reaching this bound is an incomplete collection,
# never a reason to publish a truncated series.
REST_PAGE_SAFETY_CAP = 100
MAX_COMPRESSED_LEDGER_BYTES = 90 * 1024 * 1024
MAX_COMPRESSED_SEGMENT_BYTES = 32 * 1024 * 1024
MAX_UNCOMPRESSED_LEDGER_BYTES = 512 * 1024 * 1024
MAX_SUMMARY_BYTES = 5 * 1024 * 1024
_SEGMENT_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl\.gz$")


def _segment_generation_sha(segment_metadata: dict[str, dict]) -> str:
    return hashlib.sha256(
        "".join(
            f"{name}\0{metadata['sha256']}\0{metadata['compressed_bytes']}\n"
            for name, metadata in sorted(segment_metadata.items())
        ).encode("utf-8")
    ).hexdigest()


def _ledger_manifest_complete(ledger: object) -> bool:
    if not isinstance(ledger, dict) or ledger.get("format") != "daily_deterministic_gzip_jsonl":
        return False
    segments = ledger.get("segments")
    if not isinstance(segments, dict) or ledger.get("segment_count") != len(segments):
        return False
    if any(
        not _SEGMENT_NAME_RE.fullmatch(str(name))
        or not isinstance(row, dict)
        or not isinstance(row.get("compressed_bytes"), int)
        or not isinstance(row.get("uncompressed_bytes"), int)
        or not isinstance(row.get("job_observations"), int)
        or row["compressed_bytes"] < 0
        or row["uncompressed_bytes"] < 0
        or row["job_observations"] < 0
        or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256") or ""))
        for name, row in segments.items()
    ):
        return False
    return bool(
        ledger.get("total_compressed_bytes")
        == sum(row["compressed_bytes"] for row in segments.values())
        and ledger.get("total_uncompressed_bytes")
        == sum(row["uncompressed_bytes"] for row in segments.values())
        and ledger.get("job_observations")
        == sum(row["job_observations"] for row in segments.values())
        and ledger.get("generation_sha256") == _segment_generation_sha(segments)
    )


def _utc_iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    timespec = "microseconds" if normalized.microsecond else "seconds"
    return normalized.isoformat(timespec=timespec).replace("+00:00", "Z")


def _require_datetime(value: object, field: str) -> datetime:
    parsed = parse_iso(str(value or ""))
    if parsed is None:
        raise RuntimeError(f"invalid or missing {field}: {value!r}")
    return parsed.astimezone(timezone.utc)


def _canonical_timestamp(value: object, field: str) -> str:
    return _utc_iso(_require_datetime(value, field))


def _sha256(namespace: str, value: str) -> str:
    return hashlib.sha256(f"{namespace}\0{value}".encode("utf-8")).hexdigest()


def _job_id(job_uuid: str) -> str:
    return _sha256("buildkite-job-v1", job_uuid)


def _meaningful(value: object) -> bool:
    return value is not None and value != ""


def _merge_nested(existing: object, incoming: object) -> object:
    if not isinstance(existing, dict) or not isinstance(incoming, dict):
        return incoming if _meaningful(incoming) else existing
    merged = dict(existing)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_nested(merged[key], value)
        elif _meaningful(value) or key not in merged:
            merged[key] = value
    return merged


def _optional_timestamp(value: object, field: str) -> str | None:
    if value is None or value == "":
        return None
    return _canonical_timestamp(value, field)


def _duration_seconds(start: str | None, end: str | None, label: str) -> float | None:
    if not start or not end:
        return None
    seconds = (
        _require_datetime(end, f"{label} end") - _require_datetime(start, f"{label} start")
    ).total_seconds()
    if seconds < 0:
        raise RuntimeError(f"negative {label}: {start} -> {end}")
    return round(seconds, 3)


def _terminal_outcome(state: str, passed: bool, soft_failed: bool) -> str:
    if soft_failed:
        return "soft_failed"
    if passed:
        return "passed"
    mapped = {
        "FAILED": "failed",
        "CANCELED": "canceled",
        "TIMED_OUT": "timed_out",
        "EXPIRED": "expired",
        "BROKEN": "broken",
        "SKIPPED": "skipped",
    }
    return mapped.get(state.upper(), "failed" if state.upper() == "FINISHED" else "other")


def _rest_job_node(job: dict, queue: str) -> dict:
    state = str(job.get("state") or "").strip().upper()
    retry_source = job.get("retry_source")
    return {
        "uuid": str(job.get("id") or "").strip(),
        "state": state,
        "createdAt": job.get("created_at"),
        "runnableAt": job.get("runnable_at"),
        "startedAt": job.get("started_at"),
        "finishedAt": job.get("finished_at"),
        "passed": state == "PASSED",
        "softFailed": bool(job.get("soft_failed")),
        "retried": bool(job.get("retried") or job.get("retried_in_job_id")),
        "retriesCount": job.get("retries_count") or 0,
        "retryType": job.get("retry_type"),
        # Only presence is used and persisted as a boolean. Never retain the
        # potentially identifying retry source object or job UUID.
        "retrySource": {"uuid": "present"} if retry_source else None,
        "clusterQueue": {"key": queue},
    }


def fetch_rest_target_queues(token: str, *, page_fetcher=None) -> tuple[dict[str, str], dict]:
    fetch_page = page_fetcher or _request_build_page
    path = f"/organizations/{BK_ORG}/clusters/{BK_CLUSTER_UUID}/queues"
    discovered: dict[str, str] = {}
    pages = 0
    for page in range(1, REST_PAGE_SAFETY_CAP + 1):
        rows = fetch_page(path, token, {"page": page, "per_page": REST_PAGE_SIZE})
        pages = page
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError("REST cluster queue response contains a malformed row")
            key = str(row.get("key") or "").strip()
            if key not in AMD_METRIC_TARGET_QUEUES:
                continue
            queue_id = str(row.get("id") or row.get("uuid") or "").strip()
            if not queue_id:
                raise RuntimeError(f"target REST cluster queue {key} has no stable ID")
            if key in discovered and discovered[key] != queue_id:
                raise RuntimeError(f"target REST cluster queue {key} has conflicting IDs")
            discovered[key] = queue_id
        if len(rows) < REST_PAGE_SIZE:
            break
    else:
        raise RuntimeError("REST cluster queue discovery reached the pagination safety cap")
    missing = [queue for queue in AMD_METRIC_TARGET_QUEUES if queue not in discovered]
    if missing:
        raise RuntimeError("missing target REST cluster queue IDs: " + ", ".join(missing))
    by_id = {queue_id: queue for queue, queue_id in discovered.items()}
    if len(by_id) != len(discovered):
        raise RuntimeError("target REST cluster queues share a queue ID")
    return by_id, {"complete": True, "pages": pages, "target_queue_count": len(by_id)}


def _project_rest_builds(
    builds: list[dict],
    queue_by_id: dict[str, str],
    jobs: dict[str, dict],
) -> tuple[int, int]:
    command_jobs = 0
    target_jobs = 0
    for build in builds:
        if not isinstance(build, dict) or not isinstance(build.get("jobs"), list):
            raise RuntimeError("REST organization build response omitted its jobs list")
        for job in build["jobs"]:
            if not isinstance(job, dict):
                raise RuntimeError("REST organization build contains a malformed job")
            job_type = str(job.get("type") or "").strip()
            if not job_type:
                raise RuntimeError("REST organization build job has no type")
            if job_type not in {"script", "command"}:
                continue
            command_jobs += 1
            queue_id = str(job.get("cluster_queue_id") or "").strip()
            queue = queue_by_id.get(queue_id)
            rule_queue = queue_from_rules(job.get("agent_query_rules"))
            if queue is None:
                if rule_queue in AMD_METRIC_TARGET_QUEUES:
                    raise RuntimeError(
                        "target queue REST job lacks direct cluster_queue_id attribution"
                    )
                continue
            if rule_queue in AMD_METRIC_TARGET_QUEUES and rule_queue != queue:
                raise RuntimeError(
                    "REST job cluster queue ID conflicts with its explicit queue rule"
                )
            target_jobs += 1
            node = _rest_job_node(job, queue)
            job_uuid = node["uuid"]
            if not job_uuid:
                raise RuntimeError(f"target queue REST job on {queue} has no stable job ID")
            previous = jobs.get(job_uuid)
            if previous is None:
                jobs[job_uuid] = node
                continue
            if (previous.get("clusterQueue") or {}).get("key") != queue:
                raise RuntimeError(f"REST job {job_uuid} has conflicting queue identity")
            merged = _merge_nested(previous, node)
            if not isinstance(merged, dict):
                raise RuntimeError(f"failed to merge duplicate REST job {job_uuid}")
            jobs[job_uuid] = merged
    return command_jobs, target_jobs


def fetch_rest_lifecycle_jobs(
    token: str,
    *,
    query_start: datetime,
    query_end: datetime,
    queue_by_id: dict[str, str],
    max_pages: int = REST_PAGE_SAFETY_CAP,
    page_fetcher=None,
) -> tuple[dict[str, dict], dict]:
    """Union newly-created, active, and closing-finished organization cohorts."""
    fetch_page = page_fetcher or _request_build_page
    path = f"/organizations/{BK_ORG}/builds"
    common = {
        "include_retried_jobs": "true",
        "include_paused": "true",
        "exclude_pipeline": "true",
        "per_page": REST_PAGE_SIZE,
    }
    active_states = ("creating", "scheduled", "running", "failing", "blocked", "canceling")
    cohorts: list[tuple[str, dict]] = [
        (
            "created",
            {"created_from": _utc_iso(query_start), "created_to": _utc_iso(query_end)},
        ),
        (
            "active",
            # Requests encodes a list-valued parameter as repeated state[]
            # values, matching the documented organization Builds API. Keep
            # this cohort inside the same parent-build horizon as the created
            # cohort: an unbounded active scan can exhaust the pagination cap
            # on organizations with a large historical blocked-build backlog.
            {
                "state[]": list(active_states),
                "created_from": _utc_iso(query_start),
                "created_to": _utc_iso(query_end),
            },
        ),
        (
            # Run the terminal sweep last so an active build that finishes
            # during collection is represented by at least one cohort.
            "finished",
            # The organization Builds API documents ``finished_from`` but no
            # upper-bound companion. Direct job timestamps are independently
            # clipped to ``query_end`` while materializing observations.
            {
                "finished_from": _utc_iso(query_start),
                "created_to": _utc_iso(query_end),
            },
        ),
    ]
    jobs: dict[str, dict] = {}
    cohort_coverage: dict[str, dict] = {}
    raw_builds = 0
    raw_command_jobs = 0
    raw_target_jobs = 0
    for cohort, filters in cohorts:
        cohort_builds = 0
        cohort_commands = 0
        cohort_targets = 0
        for page in range(1, max_pages + 1):
            rows = fetch_page(path, token, {**common, **filters, "page": page})
            cohort_builds += len(rows)
            raw_builds += len(rows)
            commands, targets = _project_rest_builds(rows, queue_by_id, jobs)
            cohort_commands += commands
            cohort_targets += targets
            raw_command_jobs += commands
            raw_target_jobs += targets
            if len(rows) < REST_PAGE_SIZE:
                cohort_coverage[cohort] = {
                    "complete": True,
                    "pages": page,
                    "builds": cohort_builds,
                    "command_jobs": cohort_commands,
                    "target_jobs": cohort_targets,
                    "filters": filters,
                }
                break
        else:
            raise RuntimeError(f"REST build cohort {cohort} reached the pagination safety cap")
    return jobs, {
        "complete": True,
        "source": "Buildkite REST organization builds",
        "organization_wide": True,
        "cohorts": cohort_coverage,
        "active_build_states": list(active_states),
        "parent_build_query_start": _utc_iso(query_start),
        "query_horizon_exclusive": _utc_iso(query_end),
        "raw_builds": raw_builds,
        "raw_command_jobs": raw_command_jobs,
        "raw_target_jobs": raw_target_jobs,
        "unique_target_jobs": len(jobs),
    }


def observations_from_jobs(
    jobs: dict[str, dict],
    *,
    retention_start: datetime,
    end_exclusive: datetime,
) -> tuple[list[dict], dict]:
    """Materialize one privacy-minimized observation per stable job UUID."""
    observations: list[dict] = []
    timestamp_coverage = {
        "jobs": len(jobs),
        "with_runnable_at": 0,
        "with_started_at": 0,
        "with_finished_at": 0,
        "events_in_retention": {"incoming": 0, "served": 0, "completed": 0},
    }

    for job_uuid, node in sorted(jobs.items()):
        queue = node.get("clusterQueue")
        if not isinstance(queue, dict):
            raise RuntimeError(f"Buildkite job {job_uuid} lacks queue metadata")
        queue_key = str(queue.get("key") or "").strip()
        if queue_key not in AMD_METRIC_TARGET_QUEUES:
            raise RuntimeError(f"Buildkite job {job_uuid} has out-of-scope queue {queue_key!r}")

        timestamps = {
            "created_at": _optional_timestamp(node.get("createdAt"), "createdAt"),
            "runnable_at": _optional_timestamp(node.get("runnableAt"), "runnableAt"),
            "started_at": _optional_timestamp(node.get("startedAt"), "startedAt"),
            "finished_at": _optional_timestamp(node.get("finishedAt"), "finishedAt"),
        }
        # Active-state queries can observe a transition a few seconds after the
        # fixed query horizon. Defer those timestamps to the next run rather
        # than placing future evidence in this generation.
        for key in ("runnable_at", "started_at", "finished_at"):
            if timestamps[key] and _require_datetime(timestamps[key], key) >= end_exclusive:
                timestamps[key] = None
        if timestamps["runnable_at"]:
            timestamp_coverage["with_runnable_at"] += 1
        if timestamps["started_at"]:
            timestamp_coverage["with_started_at"] += 1
        if timestamps["finished_at"]:
            timestamp_coverage["with_finished_at"] += 1

        queue_wait = _duration_seconds(
            timestamps["runnable_at"], timestamps["started_at"], "queue wait"
        )
        runtime = _duration_seconds(timestamps["started_at"], timestamps["finished_at"], "runtime")
        retry_source = node.get("retrySource")
        if retry_source is not None and not isinstance(retry_source, dict):
            raise RuntimeError(f"Buildkite job {job_uuid} has malformed retrySource")
        if isinstance(retry_source, dict) and not str(retry_source.get("uuid") or "").strip():
            raise RuntimeError(f"Buildkite job {job_uuid} has retrySource without UUID")

        state = str(node.get("state") or "").strip().upper()
        if not state:
            raise RuntimeError(f"Buildkite job {job_uuid} has no state")
        passed = bool(node.get("passed"))
        soft_failed = bool(node.get("softFailed"))
        retries_count = node.get("retriesCount")
        try:
            retries_count = int(retries_count) if retries_count is not None else 0
        except (TypeError, ValueError):
            raise RuntimeError(f"Buildkite job {job_uuid} has invalid retriesCount") from None
        if retries_count < 0:
            raise RuntimeError(f"Buildkite job {job_uuid} has negative retriesCount")
        observation = {
            "schema_version": SCHEMA_VERSION,
            "job_id": _job_id(job_uuid),
            "queue": queue_key,
            "timestamps": timestamps,
            "durations_seconds": {
                "queue_wait": queue_wait,
                "runtime": runtime,
            },
            "outcome": (
                _terminal_outcome(state, passed, soft_failed) if timestamps["finished_at"] else None
            ),
            "retry": {
                "retried": bool(node.get("retried")),
                "is_retry": bool(retry_source) or bool(node.get("retryType")) or retries_count > 0,
                "retries_count": retries_count,
            },
        }
        retained_events = {
            "incoming": timestamps["runnable_at"],
            "served": timestamps["started_at"],
            "completed": timestamps["finished_at"],
        }
        keep = False
        for event_type, event_at in retained_events.items():
            if (
                event_at
                and retention_start <= _require_datetime(event_at, event_type) < end_exclusive
            ):
                timestamp_coverage["events_in_retention"][event_type] += 1
                keep = True
        if keep:
            observations.append(observation)

    return observations, timestamp_coverage


def _validate_observation(row: object, *, line: int | None = None) -> dict:
    where = f" on line {line}" if line is not None else ""
    if not isinstance(row, dict):
        raise RuntimeError(f"queue lifecycle job observation is not an object{where}")
    expected_keys = {
        "schema_version",
        "job_id",
        "queue",
        "timestamps",
        "durations_seconds",
        "outcome",
        "retry",
    }
    if set(row) != expected_keys:
        raise RuntimeError(f"queue lifecycle observation top-level schema is invalid{where}")
    if type(row.get("schema_version")) is not int or row["schema_version"] != SCHEMA_VERSION:
        raise RuntimeError(f"unsupported queue lifecycle observation schema{where}")
    if row.get("queue") not in AMD_METRIC_TARGET_QUEUES:
        raise RuntimeError(f"out-of-scope queue lifecycle observation{where}")
    job_id = row.get("job_id")
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{64}", job_id):
        raise RuntimeError(f"invalid hashed queue lifecycle identity{where}")
    row = dict(row)
    timestamps = row.get("timestamps")
    if not isinstance(timestamps, dict):
        raise RuntimeError(f"queue lifecycle observation lacks timestamps{where}")
    allowed_timestamp_keys = {"created_at", "runnable_at", "started_at", "finished_at"}
    if set(timestamps) != allowed_timestamp_keys:
        raise RuntimeError(f"queue lifecycle observation timestamp schema is invalid{where}")
    normalized_timestamps = {
        key: _canonical_timestamp(value, key) if value else None
        for key, value in timestamps.items()
    }
    row["timestamps"] = normalized_timestamps
    durations = row.get("durations_seconds")
    if not isinstance(durations, dict) or set(durations) != {"queue_wait", "runtime"}:
        raise RuntimeError(f"queue lifecycle observation duration schema is invalid{where}")
    expected_wait = _duration_seconds(
        normalized_timestamps["runnable_at"], normalized_timestamps["started_at"], "queue wait"
    )
    expected_runtime = _duration_seconds(
        normalized_timestamps["started_at"], normalized_timestamps["finished_at"], "runtime"
    )
    if durations.get("queue_wait") != expected_wait or durations.get("runtime") != expected_runtime:
        raise RuntimeError(f"queue lifecycle observation has inconsistent durations{where}")
    outcome = row.get("outcome")
    valid_outcomes = {
        None,
        "passed",
        "failed",
        "soft_failed",
        "canceled",
        "timed_out",
        "expired",
        "broken",
        "skipped",
        "other",
    }
    if outcome not in valid_outcomes or (outcome is not None) != bool(
        normalized_timestamps["finished_at"]
    ):
        raise RuntimeError(f"queue lifecycle observation has invalid outcome{where}")
    retry = row.get("retry")
    if not isinstance(retry, dict) or set(retry) != {"retried", "is_retry", "retries_count"}:
        raise RuntimeError(f"queue lifecycle observation retry schema is invalid{where}")
    if not isinstance(retry["retried"], bool) or not isinstance(retry["is_retry"], bool):
        raise RuntimeError(f"queue lifecycle observation retry flags are invalid{where}")
    if type(retry["retries_count"]) is not int or retry["retries_count"] < 0:
        raise RuntimeError(f"queue lifecycle observation retry count is invalid{where}")
    return row


def read_job_text(text: str, *, source: str = "job ledger") -> list[dict]:
    if any(marker in text for marker in ("<<<<<<<", "=======", ">>>>>>>")):
        raise RuntimeError(f"{source} contains merge conflict markers")
    rows: list[dict] = []
    by_id: dict[str, dict] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            decoded = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"malformed {source} JSON on line {line_number}: {exc}") from exc
        row = _validate_observation(decoded, line=line_number)
        job_id = row["job_id"]
        previous = by_id.get(job_id)
        if previous is not None and previous != row:
            raise RuntimeError(f"{source} contains conflicting duplicate job {job_id}")
        if previous is None:
            by_id[job_id] = row
            rows.append(row)
    return rows


def read_job_directory(path: Path) -> list[dict]:
    if not path.exists():
        return []
    if not path.is_dir():
        raise RuntimeError(f"queue lifecycle ledger path is not a directory: {path}")
    segment_paths = sorted(path.iterdir())
    if any(
        not item.is_file() or not _SEGMENT_NAME_RE.fullmatch(item.name) for item in segment_paths
    ):
        raise RuntimeError(f"queue lifecycle ledger directory contains an unexpected entry: {path}")
    sizes = [item.stat().st_size for item in segment_paths]
    if any(size > MAX_COMPRESSED_SEGMENT_BYTES for size in sizes):
        raise RuntimeError("compressed queue lifecycle segment exceeds the per-file safety limit")
    if sum(sizes) > MAX_COMPRESSED_LEDGER_BYTES:
        raise RuntimeError("compressed queue lifecycle segments exceed the total safety limit")
    rows: list[dict] = []
    seen: set[str] = set()
    remaining_uncompressed = MAX_UNCOMPRESSED_LEDGER_BYTES
    for segment_path in segment_paths:
        segment_rows, decoded_size = _decode_job_ledger_with_size(
            segment_path.read_bytes(),
            source=str(segment_path),
            max_uncompressed=remaining_uncompressed,
        )
        remaining_uncompressed -= decoded_size
        for row in segment_rows:
            if row["job_id"] in seen:
                raise RuntimeError(f"job {row['job_id']} occurs in multiple lifecycle segments")
            seen.add(row["job_id"])
            rows.append(row)
    return rows


def _read_decompressed_limited(archive, limit: int = MAX_UNCOMPRESSED_LEDGER_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = archive.read(min(1024 * 1024, limit - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise RuntimeError("uncompressed queue lifecycle ledger exceeds the safety limit")
        chunks.append(chunk)


def _decode_job_ledger_with_size(
    compressed: bytes,
    *,
    source: str,
    max_uncompressed: int = MAX_UNCOMPRESSED_LEDGER_BYTES,
) -> tuple[list[dict], int]:
    if len(compressed) > MAX_COMPRESSED_SEGMENT_BYTES:
        raise RuntimeError(
            f"compressed queue lifecycle segment at {source} exceeds the safety limit"
        )
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as archive:
            decoded = _read_decompressed_limited(archive, max_uncompressed)
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise RuntimeError(f"unreadable compressed queue lifecycle ledger at {source}") from exc
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"queue lifecycle ledger at {source} is not UTF-8") from exc
    return read_job_text(text, source=source), len(decoded)


def decode_job_ledger(compressed: bytes, *, source: str) -> list[dict]:
    rows, _ = _decode_job_ledger_with_size(compressed, source=source)
    return rows


def _merge_observation(previous: dict, incoming: dict) -> dict:
    if previous["job_id"] != incoming["job_id"] or previous["queue"] != incoming["queue"]:
        raise RuntimeError(f"conflicting queue lifecycle job {incoming['job_id']}")
    timestamps = {}
    for key in previous["timestamps"]:
        old_value = previous["timestamps"].get(key)
        new_value = incoming["timestamps"].get(key)
        if old_value and new_value and old_value != new_value:
            raise RuntimeError(f"job {incoming['job_id']} has conflicting {key}")
        timestamps[key] = new_value or old_value
    outcome = incoming.get("outcome") or previous.get("outcome")
    if (
        previous.get("outcome")
        and incoming.get("outcome")
        and previous["outcome"] != incoming["outcome"]
    ):
        raise RuntimeError(f"job {incoming['job_id']} has conflicting outcome")
    merged = {
        "schema_version": SCHEMA_VERSION,
        "job_id": incoming["job_id"],
        "queue": incoming["queue"],
        "timestamps": timestamps,
        "durations_seconds": {
            "queue_wait": _duration_seconds(
                timestamps["runnable_at"], timestamps["started_at"], "queue wait"
            ),
            "runtime": _duration_seconds(
                timestamps["started_at"], timestamps["finished_at"], "runtime"
            ),
        },
        "outcome": outcome,
        "retry": {
            "retried": bool(previous["retry"]["retried"] or incoming["retry"]["retried"]),
            "is_retry": bool(previous["retry"]["is_retry"] or incoming["retry"]["is_retry"]),
            "retries_count": max(
                previous["retry"]["retries_count"], incoming["retry"]["retries_count"]
            ),
        },
    }
    return _validate_observation(merged)


def merge_and_prune_jobs(
    existing: Iterable[dict],
    incoming: Iterable[dict],
    *,
    retention_start: datetime,
    end_exclusive: datetime,
) -> list[dict]:
    """Merge stable hashed job IDs and retain jobs with any event in range."""
    merged: dict[str, dict] = {}
    for rows in (existing, incoming):
        for value in rows:
            row = _validate_observation(value)
            previous = merged.get(row["job_id"])
            merged[row["job_id"]] = _merge_observation(previous, row) if previous else row
    retained = []
    for row in merged.values():
        event_times = [
            _require_datetime(row["timestamps"][key], key)
            for key in ("runnable_at", "started_at", "finished_at")
            if row["timestamps"].get(key)
        ]
        if any(retention_start <= value < end_exclusive for value in event_times):
            retained.append(row)
    return sorted(retained, key=lambda row: row["job_id"])


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def encode_job_ledger(rows: Iterable[dict]) -> bytes:
    text = "".join(
        json.dumps(_validate_observation(row), sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")
    if len(text) > MAX_UNCOMPRESSED_LEDGER_BYTES:
        raise RuntimeError("uncompressed queue lifecycle ledger exceeds the safety limit")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0, compresslevel=9) as archive:
        archive.write(text)
    compressed = buffer.getvalue()
    if len(compressed) > MAX_COMPRESSED_SEGMENT_BYTES:
        raise RuntimeError(
            f"compressed queue lifecycle segment is {len(compressed)} bytes; "
            f"limit is {MAX_COMPRESSED_SEGMENT_BYTES}"
        )
    return compressed


def _segment_day(row: dict, retention_start: datetime, end_exclusive: datetime) -> str:
    retained = [
        _require_datetime(row["timestamps"][key], key)
        for key in ("runnable_at", "started_at", "finished_at")
        if row["timestamps"].get(key)
        and retention_start <= _require_datetime(row["timestamps"][key], key) < end_exclusive
    ]
    if not retained:
        raise RuntimeError(f"job {row['job_id']} has no retained lifecycle event")
    return min(retained).date().isoformat()


def encode_job_segments(
    rows: Iterable[dict], *, retention_start: datetime, end_exclusive: datetime
) -> tuple[dict[str, bytes], dict]:
    partitioned: dict[str, list[dict]] = {}
    for value in rows:
        row = _validate_observation(value)
        day = _segment_day(row, retention_start, end_exclusive)
        partitioned.setdefault(day, []).append(row)
    payloads: dict[str, bytes] = {}
    segment_metadata: dict[str, dict] = {}
    total = 0
    total_uncompressed = 0
    for day, segment_rows in sorted(partitioned.items()):
        name = f"{day}.jsonl.gz"
        segment_uncompressed = sum(
            len(
                (
                    json.dumps(
                        _validate_observation(row),
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            )
            for row in segment_rows
        )
        total_uncompressed += segment_uncompressed
        if total_uncompressed > MAX_UNCOMPRESSED_LEDGER_BYTES:
            raise RuntimeError(
                "uncompressed queue lifecycle segments exceed the total safety limit"
            )
        payload = encode_job_ledger(sorted(segment_rows, key=lambda row: row["job_id"]))
        total += len(payload)
        if total > MAX_COMPRESSED_LEDGER_BYTES:
            raise RuntimeError("compressed queue lifecycle segments exceed the total safety limit")
        digest = hashlib.sha256(payload).hexdigest()
        payloads[name] = payload
        segment_metadata[name] = {
            "compressed_bytes": len(payload),
            "job_observations": len(segment_rows),
            "uncompressed_bytes": segment_uncompressed,
            "sha256": digest,
        }
    generation = _segment_generation_sha(segment_metadata)
    metadata = {
        "format": "daily_deterministic_gzip_jsonl",
        "segment_count": len(payloads),
        "job_observations": sum(row["job_observations"] for row in segment_metadata.values()),
        "total_compressed_bytes": total,
        "total_uncompressed_bytes": total_uncompressed,
        "generation_sha256": generation,
        "max_segment_bytes": MAX_COMPRESSED_SEGMENT_BYTES,
        "max_total_bytes": MAX_COMPRESSED_LEDGER_BYTES,
        "segments": segment_metadata,
    }
    return payloads, metadata


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _publish_generation(
    jobs_path: Path,
    segment_payloads: dict[str, bytes],
    summary_path: Path,
    summary_text: str,
) -> None:
    """Publish linked artifacts, preserving the old ledger on every failure."""
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(dir=jobs_path.parent, prefix=f".{jobs_path.name}.stage."))
    backup: Path | None = None
    old_generation_moved = False
    new_generation_installed = False
    try:
        for name, payload in sorted(segment_payloads.items()):
            if not _SEGMENT_NAME_RE.fullmatch(name):
                raise RuntimeError(f"invalid lifecycle segment name {name!r}")
            _atomic_write_bytes(stage / name, payload)
        if jobs_path.exists():
            if not jobs_path.is_dir():
                raise RuntimeError(f"lifecycle jobs output is not a directory: {jobs_path}")
            backup = Path(
                tempfile.mkdtemp(dir=jobs_path.parent, prefix=f".{jobs_path.name}.backup.")
            )
            backup.rmdir()
            os.replace(jobs_path, backup)
            old_generation_moved = True
        os.replace(stage, jobs_path)
        new_generation_installed = True
        _atomic_write_text(summary_path, summary_text)
        if backup is not None:
            try:
                shutil.rmtree(backup)
            except OSError as cleanup_error:
                log.warning(
                    "Published lifecycle generation but could not remove old backup %s: %s",
                    backup,
                    cleanup_error,
                )
            backup = None
    except Exception as publish_error:
        # If the old directory was moved, make a best-effort rollback for both
        # stage-install and summary-write failures. Crucially, never delete the
        # sole backup when restoration itself fails.
        if old_generation_moved and backup is not None and backup.exists():
            try:
                if new_generation_installed and jobs_path.exists():
                    shutil.rmtree(jobs_path)
                os.replace(backup, jobs_path)
                backup = None
            except Exception as rollback_error:
                log.error(
                    "Lifecycle publish rollback failed; prior ledger preserved at %s: %s",
                    backup,
                    rollback_error,
                )
                raise RuntimeError(
                    f"lifecycle publish failed and rollback failed; prior ledger preserved at {backup}"
                ) from publish_error
        elif new_generation_installed and jobs_path.exists():
            # There was no previous generation. Restore the prior absence when
            # the summary replacement fails.
            shutil.rmtree(jobs_path)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _duration_summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "min": None, "p50": None, "p95": None, "max": None, "avg": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": round(ordered[0], 3),
        "p50": round(percentile(ordered, 50), 3),
        "p95": round(percentile(ordered, 95), 3),
        "max": round(ordered[-1], 3),
        "avg": round(sum(ordered) / len(ordered), 3),
    }


def _timestamp_in_window(value: str | None, start: datetime, end_exclusive: datetime) -> bool:
    return bool(value and start <= _require_datetime(value, "lifecycle timestamp") < end_exclusive)


def _metric_block(observations: Iterable[dict], start: datetime, end_exclusive: datetime) -> dict:
    metrics = {
        "incoming": 0,
        "served": 0,
        "completed": 0,
        "passed": 0,
        "failed": 0,
        "soft_failed": 0,
        "other_outcomes": 0,
        "canceled": 0,
        "timed_out": 0,
        "expired": 0,
        "broken": 0,
        "skipped": 0,
        "retry_attempts_completed": 0,
        "retried_jobs_completed": 0,
    }
    waits: list[float] = []
    runtimes: list[float] = []
    for row in observations:
        timestamps = row["timestamps"]
        if _timestamp_in_window(timestamps["runnable_at"], start, end_exclusive):
            metrics["incoming"] += 1
        if _timestamp_in_window(timestamps["started_at"], start, end_exclusive):
            metrics["served"] += 1
            queue_wait = (row.get("durations_seconds") or {}).get("queue_wait")
            if queue_wait is not None:
                waits.append(float(queue_wait))
        if _timestamp_in_window(timestamps["finished_at"], start, end_exclusive):
            metrics["completed"] += 1
            outcome = row.get("outcome")
            if outcome == "passed":
                metrics["passed"] += 1
            elif outcome == "failed":
                metrics["failed"] += 1
            elif outcome == "soft_failed":
                metrics["soft_failed"] += 1
            elif outcome in {"canceled", "timed_out", "expired", "broken", "skipped"}:
                metrics[outcome] += 1
            else:
                metrics["other_outcomes"] += 1
            runtime = (row.get("durations_seconds") or {}).get("runtime")
            if runtime is not None:
                runtimes.append(float(runtime))
            retry = row.get("retry") or {}
            metrics["retry_attempts_completed"] += int(bool(retry.get("is_retry")))
            metrics["retried_jobs_completed"] += int(bool(retry.get("retried")))
    classified = metrics["passed"] + metrics["failed"] + metrics["soft_failed"]
    metrics["pass_rate_pct"] = (
        round(metrics["passed"] / classified * 100, 3) if classified else None
    )
    metrics["queue_wait_seconds"] = _duration_summary(waits)
    metrics["runtime_seconds"] = _duration_summary(runtimes)
    return metrics


def _scoped_metrics(
    observations: list[dict], start: datetime, end_exclusive: datetime
) -> tuple[dict, dict[str, dict]]:
    return (
        _metric_block(observations, start, end_exclusive),
        {
            queue: _metric_block(
                (row for row in observations if row["queue"] == queue),
                start,
                end_exclusive,
            )
            for queue in AMD_METRIC_TARGET_QUEUES
        },
    )


def _hour_floor(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _hourly_buckets(
    observations: list[dict], retention_start: datetime, end_exclusive: datetime
) -> list[dict]:
    # Index each retained job into only the hours containing one of its three
    # lifecycle events. This keeps seven-day aggregation linear in observed
    # events instead of rescanning the full ~128k-job ledger for every hour.
    observations_by_hour: dict[datetime, dict[str, dict]] = {}
    for row in observations:
        for key in ("runnable_at", "started_at", "finished_at"):
            value = row["timestamps"].get(key)
            if not value:
                continue
            event_at = _require_datetime(value, key)
            if retention_start <= event_at < end_exclusive:
                observations_by_hour.setdefault(_hour_floor(event_at), {})[row["job_id"]] = row

    buckets: list[dict] = []
    cursor = _hour_floor(retention_start)
    while cursor < end_exclusive:
        bucket_end = cursor + timedelta(hours=1)
        metric_start = max(cursor, retention_start)
        metric_end = min(bucket_end, end_exclusive)
        bucket_observations = list((observations_by_hour.get(cursor) or {}).values())
        totals = _metric_block(bucket_observations, metric_start, metric_end)
        buckets.append(
            {
                "start": _utc_iso(cursor),
                "end_exclusive": _utc_iso(bucket_end),
                "partial": cursor < retention_start or bucket_end > end_exclusive,
                "totals": totals,
            }
        )
        cursor = bucket_end
    return buckets


def _summary_provenance(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return {}
    scope = payload.get("scope")
    if not isinstance(scope, dict) or scope.get("queues") != list(AMD_METRIC_TARGET_QUEUES):
        return {}
    provenance = payload.get("provenance")
    return dict(provenance) if isinstance(provenance, dict) else {}


def _job_directory_generation(path: Path) -> str:
    if not path.is_dir():
        return ""
    metadata: dict[str, dict] = {}
    for item in sorted(path.iterdir()):
        if not item.is_file() or not _SEGMENT_NAME_RE.fullmatch(item.name):
            return ""
        size = item.stat().st_size
        metadata[item.name] = {
            "compressed_bytes": size,
            "sha256": hashlib.sha256(item.read_bytes()).hexdigest(),
        }
    return _segment_generation_sha(metadata)


def _safe_previous_provenance(path: Path, *, jobs_path: Path | None = None) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Ignoring invalid derived lifecycle summary for watermarking: %s", exc)
        return {}
    provenance = _summary_provenance(payload)
    if jobs_path is not None:
        if not jobs_path.exists():
            log.warning("Ignoring lifecycle watermark because its local ledger is absent")
            return {}
        ledger = provenance.get("ledger") or {}
        if not _ledger_manifest_complete(ledger):
            log.warning("Ignoring lifecycle watermark with an incomplete ledger manifest")
            return {}
        linked_generation = str(ledger["generation_sha256"])
        if not linked_generation or _job_directory_generation(jobs_path) != linked_generation:
            log.warning("Ignoring lifecycle watermark from a mismatched ledger generation")
            return {}
    return provenance


def build_summary(
    observations: list[dict],
    *,
    now: datetime,
    collection: dict | None,
    previous_provenance: dict | None = None,
    ledger: dict | None = None,
) -> dict:
    retention_start = now - timedelta(days=RETENTION_DAYS)
    window_start = now - timedelta(hours=ROLLING_WINDOW_HOURS)
    totals, queues = _scoped_metrics(observations, window_start, now)
    previous_provenance = previous_provenance or {}
    last_query_end = (
        collection.get("query_end_exclusive")
        if collection
        else previous_provenance.get("last_successful_query_end")
    )
    query_start = (
        collection.get("query_start")
        if collection
        else previous_provenance.get("last_successful_query_start")
    )
    query_mode = (
        collection.get("query_mode")
        if collection
        else previous_provenance.get("last_successful_query_mode")
    )
    query_start_dt = parse_iso(str(query_start or ""))
    query_end_dt = parse_iso(str(last_query_end or ""))
    query_covers_window = bool(
        query_start_dt and query_end_dt and query_start_dt <= window_start and query_end_dt >= now
    )

    observed_times = [
        _require_datetime(row["timestamps"][key], key)
        for row in observations
        for key in ("runnable_at", "started_at", "finished_at")
        if row["timestamps"].get(key)
        and retention_start <= _require_datetime(row["timestamps"][key], key) < now
    ]
    queue_discovery_complete = bool(
        collection and (collection.get("queue_discovery") or {}).get("complete")
    )
    source_complete = bool(collection and (collection.get("source_coverage") or {}).get("complete"))
    api_complete = bool(
        collection and collection.get("complete") and queue_discovery_complete and source_complete
    )
    # The REST cohort union covers every documented parent-build state, but
    # page-number pagination is not a transactional snapshot. Concurrent page
    # drift and jobs dynamically added to, or still running from, a parent
    # created before the bounded parent-build horizon prevent an unconditional
    # exhaustiveness claim.
    complete = False
    coverage = {
        "complete": complete,
        "status": "partial_observation",
        "reason": (
            "All organization-wide cohort pages and target queue IDs were collected, but "
            "page-number drift and jobs belonging to parent builds created before the "
            "bounded source horizon cannot be proven absent. Direct observed event "
            "timestamps remain exact."
            if api_complete
            else "No complete current API collection covers the rolling window."
        ),
        "api_complete": api_complete,
        "api_collection_performed": collection is not None,
        "target_queue_scope_complete": bool(queue_discovery_complete and source_complete),
        "pagination_complete": source_complete,
        "exact_rolling_window_covered_by_current_query": bool(
            collection is not None and query_covers_window
        ),
        "metric_exhaustiveness": {
            "completed": {
                "complete": False,
                "exact_for_observed_events": True,
                "basis": (
                    "direct jobs[].finished_at from the exhaustively paginated organization-wide "
                    "bounded finished/active/created build cohort union"
                ),
                "limitation": (
                    "REST page-number pagination is not a transactional snapshot; a job dynamically "
                    "added to, or still running from, a parent build created before the bounded "
                    "source horizon can also escape all parent-build filters."
                ),
            },
            "incoming": {
                "complete": False,
                "exact_for_observed_events": True,
                "basis": (
                    "direct jobs[].runnable_at from the exhaustively paginated organization-wide "
                    "bounded finished/active/created build cohort union"
                ),
                "limitation": (
                    "REST page-number pagination is not a transactional snapshot; a job dynamically "
                    "added to, or still running from, a parent build created before the bounded "
                    "source horizon can also escape all parent-build filters."
                ),
            },
            "served": {
                "complete": False,
                "exact_for_observed_events": True,
                "basis": (
                    "direct jobs[].started_at from the exhaustively paginated organization-wide "
                    "bounded finished/active/created build cohort union"
                ),
                "limitation": (
                    "REST page-number pagination is not a transactional snapshot; a job dynamically "
                    "added to, or still running from, a parent build created before the bounded "
                    "source horizon can also escape all parent-build filters."
                ),
            },
        },
        "job_observation_count": len(observations),
        "event_count": len(observed_times),
        "observed_start": _utc_iso(min(observed_times)) if observed_times else None,
        "observed_end": _utc_iso(max(observed_times)) if observed_times else None,
    }
    if collection:
        coverage["timestamp_fields"] = collection.get("timestamp_coverage") or {}

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_iso(now),
        "window": {
            "start": _utc_iso(window_start),
            "end_exclusive": _utc_iso(now),
            "hours": ROLLING_WINDOW_HOURS,
        },
        "scope": {
            "queues": list(AMD_METRIC_TARGET_QUEUES),
            "families": ["MI250", "MI300", "MI355"],
        },
        "totals": totals,
        "queues": queues,
        "hourly": _hourly_buckets(observations, retention_start, now),
        "coverage": coverage,
        "provenance": {
            "provider": "Buildkite REST organization builds API",
            "source_field_contract": {
                "incoming": "builds[].jobs[].runnable_at",
                "served": "builds[].jobs[].started_at",
                "completed": "builds[].jobs[].finished_at",
                "queue_wait_seconds": "started_at - runnable_at; null unless both direct timestamps exist",
                "runtime_seconds": "finished_at - started_at; null unless both direct timestamps exist",
                "queue": "builds[].jobs[].cluster_queue_id resolved through the cluster queues endpoint",
            },
            "retry_semantics": (
                "Every hashed job UUID is one attempt. is_retry means retry_source, retry_type, "
                "or a positive retries_count was present; retried marks an attempt superseded by "
                "another retry. Throughput counts attempts, not retry chains."
            ),
            "last_successful_query_start": query_start,
            "last_successful_query_end": last_query_end,
            "last_successful_query_mode": query_mode,
            "ledger": ledger or {},
            "collection": collection,
        },
        "retention": {
            "days": RETENTION_DAYS,
            "event_start": _utc_iso(retention_start),
            "end_exclusive": _utc_iso(now),
        },
    }


def write_summary(path: Path, summary: dict) -> None:
    _atomic_write_text(
        path,
        json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n",
    )


def collect_lifecycle(
    token: str,
    *,
    jobs_path: Path = JOBS_OUTPUT,
    summary_path: Path = SUMMARY_OUTPUT,
    now: datetime | None = None,
) -> dict:
    if not token.strip():
        raise RuntimeError("BUILDKITE_API_TOKEN is required")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    retention_start = current - timedelta(days=RETENTION_DAYS)
    existing = read_job_directory(jobs_path)
    previous = _safe_previous_provenance(summary_path, jobs_path=jobs_path)
    # Re-scan the retained event window plus a small parent-build lookback.
    # This catches ordinary dynamic-pipeline delay without claiming that REST
    # parent-build filters can discover jobs added to arbitrarily old builds.
    query_start = retention_start - timedelta(days=PARENT_BUILD_LOOKBACK_DAYS)
    query_mode = "full_retention_cohort_union"
    queue_by_id, queue_discovery = fetch_rest_target_queues(token)
    jobs, source_coverage = fetch_rest_lifecycle_jobs(
        token,
        query_start=query_start,
        query_end=current,
        queue_by_id=queue_by_id,
    )
    unique_job_count = len(jobs)
    incoming, timestamp_coverage = observations_from_jobs(
        jobs,
        retention_start=retention_start,
        end_exclusive=current,
    )
    del jobs
    merged = merge_and_prune_jobs(
        existing,
        incoming,
        retention_start=retention_start,
        end_exclusive=current,
    )
    del existing, incoming
    segment_payloads, ledger = encode_job_segments(
        merged,
        retention_start=retention_start,
        end_exclusive=current,
    )
    collection = {
        "complete": True,
        "query_mode": query_mode,
        "query_start": _utc_iso(query_start),
        "query_end_exclusive": _utc_iso(current),
        "queue_discovery": queue_discovery,
        "source_coverage": source_coverage,
        "organization_wide": True,
        "parent_build_lookback_days": PARENT_BUILD_LOOKBACK_DAYS,
        "unique_jobs": unique_job_count,
        "timestamp_coverage": timestamp_coverage,
        "target_queues": list(AMD_METRIC_TARGET_QUEUES),
    }
    summary = build_summary(
        merged,
        now=current,
        collection=collection,
        previous_provenance=previous,
        ledger=ledger,
    )
    # All network, validation, aggregation, and serialization work has
    # succeeded before either public artifact is replaced.
    summary_text = json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n"
    _publish_generation(jobs_path, segment_payloads, summary_path, summary_text)
    return summary


def _git_ref_jobs(
    git_ref: str,
    *,
    required: bool = False,
    expected_ledger: dict | None = None,
) -> list[dict]:
    if expected_ledger is not None and not _ledger_manifest_complete(expected_ledger):
        raise RuntimeError(f"lifecycle ledger manifest at {git_ref} is incomplete")
    ref_exists = subprocess.run(
        ["git", "cat-file", "-e", f"{git_ref}^{{commit}}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if ref_exists.returncode != 0:
        raise RuntimeError(f"could not resolve lifecycle data ref {git_ref}")
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", git_ref, "--", JOBS_REPO_PATH],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if listing.returncode != 0:
        raise RuntimeError(f"could not list lifecycle segments at {git_ref}")
    paths = [line.decode("utf-8") for line in listing.stdout.splitlines() if line]
    if not paths:
        if required:
            raise RuntimeError(f"established lifecycle ledger is missing at {git_ref}")
        log.info("No lifecycle job ledger at %s; merge is a first-bootstrap no-op", git_ref)
        return []
    prefix = JOBS_REPO_PATH + "/"
    if any(
        not path.startswith(prefix)
        or "/" in path[len(prefix) :]
        or not _SEGMENT_NAME_RE.fullmatch(path[len(prefix) :])
        for path in paths
    ):
        raise RuntimeError(f"lifecycle segment directory at {git_ref} contains an invalid path")
    expected_segments = (expected_ledger or {}).get("segments") or {}
    if expected_segments and set(expected_segments) != {path[len(prefix) :] for path in paths}:
        raise RuntimeError(f"lifecycle segment manifest mismatch at {git_ref}")

    rows: list[dict] = []
    seen: set[str] = set()
    actual_segments: dict[str, dict] = {}
    total_size = 0
    total_uncompressed = 0
    for path in sorted(paths):
        name = path[len(prefix) :]
        object_name = f"{git_ref}:{path}"
        size_result = subprocess.run(
            ["git", "cat-file", "-s", object_name],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
        try:
            size = int(size_result.stdout.strip()) if size_result.returncode == 0 else -1
        except ValueError:
            size = -1
        if size < 0 or size > MAX_COMPRESSED_SEGMENT_BYTES:
            raise RuntimeError(f"invalid lifecycle segment size at {object_name}")
        total_size += size
        if total_size > MAX_COMPRESSED_LEDGER_BYTES:
            raise RuntimeError(f"lifecycle segments at {git_ref} exceed the total safety limit")
        result = subprocess.run(
            ["git", "show", object_name],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 or len(result.stdout) != size:
            raise RuntimeError(f"could not read complete lifecycle segment at {object_name}")
        digest = hashlib.sha256(result.stdout).hexdigest()
        expected = expected_segments.get(name) or {}
        if expected and (
            expected.get("sha256") != digest or expected.get("compressed_bytes") != size
        ):
            raise RuntimeError(f"lifecycle segment generation mismatch at {object_name}")
        segment_rows, uncompressed_size = _decode_job_ledger_with_size(
            result.stdout,
            source=object_name,
            max_uncompressed=MAX_UNCOMPRESSED_LEDGER_BYTES - total_uncompressed,
        )
        total_uncompressed += uncompressed_size
        for row in segment_rows:
            if row["job_id"] in seen:
                raise RuntimeError(f"job {row['job_id']} occurs in multiple remote segments")
            seen.add(row["job_id"])
            rows.append(row)
        actual_segments[name] = {
            "sha256": digest,
            "compressed_bytes": size,
            "uncompressed_bytes": uncompressed_size,
            "job_observations": len(segment_rows),
        }
    expected_generation = str((expected_ledger or {}).get("generation_sha256") or "")
    if expected_generation and _segment_generation_sha(actual_segments) != expected_generation:
        raise RuntimeError(f"lifecycle segment generation mismatch at {git_ref}")
    if expected_ledger and (
        expected_ledger.get("segment_count") != len(actual_segments)
        or expected_ledger.get("total_compressed_bytes") != total_size
        or expected_ledger.get("total_uncompressed_bytes") != total_uncompressed
        or expected_ledger.get("job_observations") != len(rows)
        or any(
            (expected_segments.get(name) or {}).get("job_observations")
            != metadata["job_observations"]
            or (expected_segments.get(name) or {}).get("uncompressed_bytes")
            != metadata["uncompressed_bytes"]
            for name, metadata in actual_segments.items()
        )
    ):
        raise RuntimeError(f"lifecycle segment volume manifest mismatch at {git_ref}")
    return rows


def _git_ref_summary_provenance(git_ref: str) -> dict:
    """Read the live branch watermark without treating derived JSON as evidence."""
    object_name = f"{git_ref}:{SUMMARY_REPO_PATH}"
    size_result = subprocess.run(
        ["git", "cat-file", "-s", object_name],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    try:
        size = int(size_result.stdout.strip()) if size_result.returncode == 0 else -1
    except ValueError:
        size = -1
    if size < 0:
        return {}
    if size > MAX_SUMMARY_BYTES:
        raise RuntimeError(f"lifecycle summary at {git_ref} exceeds the safety limit")
    result = subprocess.run(
        ["git", "show", object_name],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) != size:
        return {}
    if any(marker in result.stdout for marker in ("<<<<<<<", "=======", ">>>>>>>")):
        log.warning("Ignoring conflicted derived lifecycle summary at %s", git_ref)
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        log.warning("Ignoring malformed derived lifecycle summary at %s: %s", git_ref, exc)
        return {}
    return _summary_provenance(payload)


def maintain_job_ledger(
    *,
    jobs_path: Path,
    summary_path: Path,
    git_ref: str | None = None,
    now: datetime | None = None,
) -> dict:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    retention_start = current - timedelta(days=RETENTION_DAYS)
    local = read_job_directory(jobs_path)
    remote_provenance = _git_ref_summary_provenance(git_ref) if git_ref else {}
    remote_ledger = remote_provenance.get("ledger") if git_ref else None
    remote_manifest_bound = _ledger_manifest_complete(remote_ledger)
    if git_ref and not remote_manifest_bound:
        raise RuntimeError(
            f"established lifecycle ref {git_ref} lacks a complete summary-bound ledger manifest"
        )
    incoming = (
        _git_ref_jobs(
            git_ref,
            # The merge flag is only used after the workflow proves the
            # independent data branch exists. A missing ledger at that point
            # is history loss, never a first-bootstrap no-op.
            required=True,
            expected_ledger=remote_ledger,
        )
        if git_ref
        else []
    )
    merged = merge_and_prune_jobs(
        incoming,
        local,  # local rows win equal IDs, matching queue-history merge semantics
        retention_start=retention_start,
        end_exclusive=current,
    )
    local_provenance = _safe_previous_provenance(summary_path, jobs_path=jobs_path)
    # A restored remote ledger and its summary are one generation. Never pair
    # it with an unrelated newer summary from main. The checks above reject a
    # missing, malformed, incomplete, or generation-mismatched remote summary.
    previous = remote_provenance if git_ref else local_provenance
    segment_payloads, ledger = encode_job_segments(
        merged,
        retention_start=retention_start,
        end_exclusive=current,
    )
    summary = build_summary(
        merged,
        now=current,
        collection=None,
        previous_provenance=previous,
        ledger=ledger,
    )
    summary_text = json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n"
    _publish_generation(jobs_path, segment_payloads, summary_path, summary_text)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-output", type=Path, default=JOBS_OUTPUT)
    parser.add_argument("--output", type=Path, default=SUMMARY_OUTPUT)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--merge-jobs-git-ref",
        metavar="REF",
        help="Tokenlessly merge a retained compressed job ledger from REF, prune, and rebuild output",
    )
    modes.add_argument(
        "--prune-jobs-only",
        action="store_true",
        help="Tokenlessly prune the local ledger and rebuild the derived output",
    )
    args = parser.parse_args()

    if args.merge_jobs_git_ref or args.prune_jobs_only:
        summary = maintain_job_ledger(
            jobs_path=args.jobs_output,
            summary_path=args.output,
            git_ref=args.merge_jobs_git_ref,
        )
    else:
        summary = collect_lifecycle(
            os.environ.get("BUILDKITE_API_TOKEN", ""),
            jobs_path=args.jobs_output,
            summary_path=args.output,
        )
    log.info(
        "Wrote %d compact job observations; rolling %dh incoming=%d served=%d completed=%d",
        summary["coverage"]["job_observation_count"],
        ROLLING_WINDOW_HOURS,
        summary["totals"]["incoming"],
        summary["totals"]["served"],
        summary["totals"]["completed"],
    )


if __name__ == "__main__":
    main()
