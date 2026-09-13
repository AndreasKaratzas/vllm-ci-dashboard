#!/usr/bin/env python3
"""Collect exact-time lifecycle observations for twelve target AMD queues.

Buildkite's public GraphQL API does not expose the historical Cluster Insights
time series.  This collector builds an auditable local series from command-job
timestamps instead.  It deliberately keeps the three concepts separate:

* ``incoming`` is a direct ``runnable_at`` event;
* ``served`` is a direct ``started_at`` event; and
* ``completed`` is a direct ``finished_at`` event.

The compact daily, adaptively sub-sharded gzip job segments are published
atomically after a stable-ID merge and seven-day prune. Publishing is
fail-closed: incomplete query units,
unresolved target queues, missing job UUIDs, or malformed retained history
abort before either output is replaced. Guard-limited runs resume a frozen,
privacy-projected private checkpoint. Successful two-hour runs advance a
durable query watermark; bounded overlap and periodic full-retention scans
reconcile jobs attached to older parent builds.
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
import stat
import subprocess
import sys
import tempfile
import time as time_module
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow direct execution as ``python scripts/vllm/collect_queue_lifecycle.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.buildkite_request_guard import (  # noqa: E402
    BuildkiteRequestAllowanceExhausted,
    install_from_environment_or_exit,
)

install_from_environment_or_exit()

from vllm.ci.utils import parse_iso, percentile, queue_from_rules  # noqa: E402
from vllm.collect_workload_mapping import (  # noqa: E402
    BuildkiteRequestDeadlineExceeded,
    PER_PAGE as REST_PAGE_SIZE,
    _request_build_page,
)
from vllm.constants import (  # noqa: E402
    AMD_METRIC_TARGET_QUEUES,
    BK_CLUSTER_UUID,
    BK_ORG,
)
from vllm.dashboard_storage_budget import writer_max_bytes  # noqa: E402

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
# Successful live collections normally resume from their prior exclusive
# query horizon. Re-reading a bounded overlap absorbs delayed Buildkite index
# visibility without paying for the full retained window on every two-hour
# run. A daily full reconciliation remains the backstop for
# jobs dynamically added to older parent builds and for legacy/invalid state.
INCREMENTAL_OVERLAP_HOURS = 6
FULL_RECONCILIATION_INTERVAL_HOURS = 24
FULL_QUERY_MODE = "full_retention_cohort_union"
INCREMENTAL_QUERY_MODE = "incremental_overlap_cohort_union"
# Ten thousand organization builds per cohort is already far beyond the
# expected retained volume. Reaching this bound is an incomplete collection,
# never a reason to publish a truncated series.
REST_PAGE_SAFETY_CAP = 100
# The private parentless data branch must remain comfortably below GitHub's
# repository-size warning boundary even when it is composed with its summary
# and Git metadata.  The writer deterministically shortens the oldest retained
# event-time suffix before publishing rather than allowing this aggregate cap
# to become a permanent failure mode.
MAX_COMPRESSED_LEDGER_BYTES = 16 * 1024 * 1024
MAX_COMPRESSED_SEGMENT_BYTES = MAX_COMPRESSED_LEDGER_BYTES
MAX_UNCOMPRESSED_LEDGER_BYTES = 512 * 1024 * 1024
# One deployment briefly published the immediately preceding daily-only
# ledger schema with a 90 MiB declaration, although its actual writer ceiling
# was first reduced to 85 MiB.  Migration may recognize those two frozen
# declarations, but neither declaration is trusted as a read allowance: old
# bytes are admitted only inside the exact 85 MiB aggregate / 32 MiB segment
# envelope and are immediately rewritten under the current contract.
LEGACY_MIGRATION_MAX_COMPRESSED_LEDGER_BYTES = 85 * 1024 * 1024
LEGACY_MIGRATION_MAX_COMPRESSED_SEGMENT_BYTES = 32 * 1024 * 1024
LEGACY_MIGRATION_DECLARED_TOTAL_BYTES = frozenset(
    {
        LEGACY_MIGRATION_MAX_COMPRESSED_LEDGER_BYTES,
        90 * 1024 * 1024,
    }
)
MAX_SUMMARY_BYTES = writer_max_bytes("queue_lifecycle_summary")
CHECKPOINT_SCHEMA_VERSION = 1
CHECKPOINT_PRODUCER = "vllm_queue_lifecycle_wip"
MAX_CHECKPOINT_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_CHECKPOINT_UNCOMPRESSED_BYTES = 384 * 1024 * 1024
MAX_CHECKPOINT_OBSERVATIONS = 750_000
CHECKPOINT_WRITE_HEADROOM_BYTES = 2 * 1024 * 1024
# At most 750 accepted leaves plus 747 split probes and one queue-discovery
# request per retry fit within sixteen 100-start guarded attempts. This binds
# the resumable work tree to the durable 25-hour attempt-ledger capacity.
MAX_CHECKPOINT_QUERY_UNITS = 750
CHECKPOINT_GUARD_EXIT = 75
CHECKPOINT_WALL_CLOCK_EXIT = 76
# Stop API work early enough for the workflow to validate and persist the
# private checkpoint, report the exact request count, and run cache cleanup
# before the independent 50-minute job watchdog fires.
LIFECYCLE_WALL_CLOCK_SECONDS = 40 * 60
SEGMENT_FORMAT = "daily_deterministic_gzip_jsonl"
SEGMENT_NAMING = "utc_day_or_adaptive_part_v1"
LEDGER_RETENTION_SCHEMA_VERSION = 1
LEDGER_RETENTION_POLICY = "newest_latest_event_suffix_v1"
SEGMENT_PARTITIONING = "earliest_retained_event_utc_day_then_sorted_job_id_recursive_bisection"
_SEGMENT_PART_WIDTH = 9
_SEGMENT_NAME_RE = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2})"
    r"(?:\.part-(?P<part>\d{9})-of-(?P<total>\d{9}))?\.jsonl\.gz$"
)


class LifecycleWallClockYield(RuntimeError):
    """The bounded collector yielded after preserving resumable progress."""


class _LedgerAggregateLimitExceeded(RuntimeError):
    """A reducible candidate generation exceeds an aggregate byte ceiling."""


def _require_lifecycle_time(deadline_monotonic: float | None) -> None:
    if deadline_monotonic is not None and time_module.monotonic() >= deadline_monotonic:
        raise LifecycleWallClockYield(
            "queue lifecycle wall-clock budget ended with durable progress"
        )


def _lifecycle_page_fetcher(page_fetcher, *, deadline_monotonic: float | None):
    """Return a fetcher that cannot begin a page beyond the lifecycle deadline."""

    def fetch(path: str, token: str, params: dict) -> list[dict]:
        _require_lifecycle_time(deadline_monotonic)
        if page_fetcher is not None:
            return page_fetcher(path, token, params)
        try:
            return _request_build_page(
                path,
                token,
                params,
                deadline_monotonic=deadline_monotonic,
            )
        except BuildkiteRequestDeadlineExceeded as exc:
            raise LifecycleWallClockYield(
                "queue lifecycle REST request reached the wall-clock deadline"
            ) from exc

    return fetch


def _segment_names_valid(names: Iterable[str]) -> bool:
    """Accept legacy daily names or one canonical adaptive part set per day."""
    grouped: dict[str, list[tuple[int, int] | None]] = {}
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str) or name in seen:
            return False
        seen.add(name)
        match = _SEGMENT_NAME_RE.fullmatch(name)
        if match is None:
            return False
        part = match.group("part")
        grouped.setdefault(match.group("day"), []).append(
            (int(part), int(match.group("total"))) if part is not None else None
        )
    for parts in grouped.values():
        if None in parts:
            if parts != [None]:
                return False
            continue
        # A one-file day always uses the legacy daily name. Every adaptive
        # filename carries the common total so even a missing final blob is
        # detectable during manifest-free local recovery.
        typed_parts = [part for part in parts if part is not None]
        totals = {total for _, total in typed_parts}
        if len(totals) != 1:
            return False
        total = next(iter(totals))
        if total < 2 or len(typed_parts) != total:
            return False
        if sorted(part for part, _ in typed_parts) != list(range(1, total + 1)):
            return False
    return True


def _segment_generation_sha(segment_metadata: dict[str, dict]) -> str:
    return hashlib.sha256(
        "".join(
            f"{name}\0{metadata['sha256']}\0{metadata['compressed_bytes']}\n"
            for name, metadata in sorted(segment_metadata.items())
        ).encode("utf-8")
    ).hexdigest()


def _ledger_retention_complete(retention: object, *, job_observations: int) -> bool:
    """Validate the additive, exact retained-scope attestation.

    A missing attestation remains valid for legacy daily-only generations.
    Once present, all counts and completeness claims are fail-closed.
    """
    if not isinstance(retention, dict):
        return False
    if (
        retention.get("schema_version") != LEDGER_RETENTION_SCHEMA_VERSION
        or retention.get("policy") != LEDGER_RETENTION_POLICY
        or retention.get("configured_days") != RETENTION_DAYS
        or retention.get("max_compressed_bytes") != MAX_COMPRESSED_LEDGER_BYTES
    ):
        return False
    start = parse_iso(retention.get("configured_event_start"))
    end = parse_iso(retention.get("end_exclusive"))
    if (
        start is None
        or end is None
        or start.tzinfo is None
        or end.tzinfo is None
        or end.astimezone(timezone.utc) - start.astimezone(timezone.utc)
        != timedelta(days=RETENTION_DAYS)
    ):
        return False

    integer_fields = (
        "input_job_observations",
        "published_job_observations",
        "omitted_from_input_job_observations",
        "omitted_whole_day_job_observations",
        "partial_day_input_job_observations",
        "partial_day_published_job_observations",
    )
    if any(
        not isinstance(retention.get(field), int)
        or isinstance(retention.get(field), bool)
        or retention[field] < 0
        for field in integer_fields
    ):
        return False
    source_count = retention["input_job_observations"]
    published_count = retention["published_job_observations"]
    omitted_count = retention["omitted_from_input_job_observations"]
    if (
        published_count != job_observations
        or source_count != published_count + omitted_count
    ):
        return False

    def canonical_days(field: str) -> list[str] | None:
        values = retention.get(field)
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            return None
        if values != sorted(set(values)):
            return None
        if any(
            _retention_day_in_window(
                value,
                retention_start=start.astimezone(timezone.utc),
                end_exclusive=end.astimezone(timezone.utc),
            )
            is None
            for value in values
        ):
            return None
        return values

    omitted_days = canonical_days("omitted_whole_latest_event_days")
    carried_days = canonical_days("carried_forward_omitted_latest_event_days")
    published_days = canonical_days("published_latest_event_days")
    if omitted_days is None or carried_days is None or published_days is None:
        return False
    if set(omitted_days) & set(published_days):
        return False

    partial_day = retention.get("partial_latest_event_day")
    partial_input = retention["partial_day_input_job_observations"]
    partial_published = retention["partial_day_published_job_observations"]
    if partial_day is None:
        if partial_input or partial_published:
            return False
    elif (
        _retention_day_in_window(
            partial_day,
            retention_start=start.astimezone(timezone.utc),
            end_exclusive=end.astimezone(timezone.utc),
        )
        is None
        or partial_day not in published_days
        or partial_day in omitted_days
        or not (0 < partial_published < partial_input)
    ):
        return False
    if omitted_count != (
        retention["omitted_whole_day_job_observations"]
        + partial_input
        - partial_published
    ):
        return False

    boolean_fields = (
        "byte_limited",
        "complete_relative_to_input",
        "complete_relative_to_configured_window",
    )
    if any(type(retention.get(field)) is not bool for field in boolean_fields):
        return False
    current_limited = omitted_count > 0
    byte_limited = retention["byte_limited"]
    if (
        retention["complete_relative_to_input"] is not (not current_limited)
        or retention["complete_relative_to_configured_window"] is not (not byte_limited)
        or byte_limited is not bool(current_limited or carried_days)
    ):
        return False

    published_start = parse_iso(retention.get("published_latest_event_start"))
    published_end = parse_iso(retention.get("published_latest_event_end"))
    if published_count == 0:
        return not published_days and published_start is None and published_end is None
    return bool(
        published_days
        and published_start is not None
        and published_end is not None
        and published_start.tzinfo is not None
        and published_end.tzinfo is not None
        and start <= published_start <= published_end < end
    )


def _ledger_manifest_complete(ledger: object) -> bool:
    if not isinstance(ledger, dict) or ledger.get("format") != SEGMENT_FORMAT:
        return False
    segments = ledger.get("segments")
    if not isinstance(segments, dict) or ledger.get("segment_count") != len(segments):
        return False
    if not _segment_names_valid(segments):
        return False
    naming_fields_present = "segment_naming" in ledger or "partitioning" in ledger
    has_adaptive_parts = any(".part-" in name for name in segments)
    if naming_fields_present or has_adaptive_parts:
        if (
            ledger.get("segment_naming") != SEGMENT_NAMING
            or ledger.get("partitioning") != SEGMENT_PARTITIONING
        ):
            return False
    if any(
        not isinstance(row, dict)
        or not isinstance(row.get("compressed_bytes"), int)
        or not isinstance(row.get("uncompressed_bytes"), int)
        or not isinstance(row.get("job_observations"), int)
        or row["compressed_bytes"] < 0
        or row["uncompressed_bytes"] < 0
        or row["job_observations"] < 0
        or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256") or ""))
        for row in segments.values()
    ):
        return False
    volume_complete = bool(
        ledger.get("total_compressed_bytes")
        == sum(row["compressed_bytes"] for row in segments.values())
        and ledger.get("total_uncompressed_bytes")
        == sum(row["uncompressed_bytes"] for row in segments.values())
        and ledger.get("job_observations")
        == sum(row["job_observations"] for row in segments.values())
        and ledger.get("generation_sha256") == _segment_generation_sha(segments)
    )
    if not volume_complete:
        return False
    retention = ledger.get("retention")
    return retention is None or _ledger_retention_complete(
        retention,
        job_observations=ledger["job_observations"],
    )


_LEGACY_MIGRATION_LEDGER_FIELDS = frozenset(
    {
        "format",
        "generation_sha256",
        "job_observations",
        "max_segment_bytes",
        "max_total_bytes",
        "segment_count",
        "segments",
        "total_compressed_bytes",
        "total_uncompressed_bytes",
    }
)


def _remote_ledger_read_contract(ledger: dict | None) -> tuple[int, int, bool]:
    """Return bounded remote read limits and whether a one-hop migration applies.

    Remote generations without a manifest use only the current limits.  A
    manifest-bound generation must be either the current adaptive schema or
    the exact immediately preceding daily-only schema.  In particular, old
    self-declared maxima never raise the amount of data this process will
    read.
    """
    if ledger is None:
        return MAX_COMPRESSED_SEGMENT_BYTES, MAX_COMPRESSED_LEDGER_BYTES, False
    if not _ledger_manifest_complete(ledger):
        raise RuntimeError("lifecycle ledger manifest is incomplete")

    segments = ledger["segments"]
    legacy = (
        set(ledger) == _LEGACY_MIGRATION_LEDGER_FIELDS
        and all(".part-" not in name for name in segments)
        and ledger.get("max_segment_bytes")
        == LEGACY_MIGRATION_MAX_COMPRESSED_SEGMENT_BYTES
        and ledger.get("max_total_bytes")
        in LEGACY_MIGRATION_DECLARED_TOTAL_BYTES
    )
    if legacy:
        max_segment = LEGACY_MIGRATION_MAX_COMPRESSED_SEGMENT_BYTES
        max_total = LEGACY_MIGRATION_MAX_COMPRESSED_LEDGER_BYTES
    else:
        if (
            ledger.get("segment_naming") != SEGMENT_NAMING
            or ledger.get("partitioning") != SEGMENT_PARTITIONING
            or ledger.get("max_segment_bytes") != MAX_COMPRESSED_SEGMENT_BYTES
            or ledger.get("max_total_bytes") != MAX_COMPRESSED_LEDGER_BYTES
        ):
            raise RuntimeError("lifecycle ledger uses an unsupported storage contract")
        max_segment = MAX_COMPRESSED_SEGMENT_BYTES
        max_total = MAX_COMPRESSED_LEDGER_BYTES

    if any(row["compressed_bytes"] > max_segment for row in segments.values()):
        raise RuntimeError("lifecycle ledger manifest exceeds the per-file migration limit")
    if ledger["total_compressed_bytes"] > max_total:
        raise RuntimeError("lifecycle ledger manifest exceeds the total migration limit")
    if ledger["total_uncompressed_bytes"] > MAX_UNCOMPRESSED_LEDGER_BYTES:
        raise RuntimeError("lifecycle ledger manifest exceeds the uncompressed safety limit")
    return max_segment, max_total, legacy


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


def _lifecycle_cohorts(
    *,
    query_start: datetime,
    query_end: datetime,
    active_parent_start: datetime,
) -> tuple[tuple[str, dict], ...]:
    """Return pairwise-disjoint parent-build query partitions.

    Buildkite documents ``created_from`` as inclusive and ``created_to`` as
    exclusive.  Recent parents therefore need one all-state query, while the
    older parent interval is split into unfinished and finished states.  This
    retains late transitions from bounded older parents without downloading
    every recent build two or three times.
    """
    if active_parent_start > query_start or query_start >= query_end:
        raise ValueError("invalid lifecycle parent/event query horizon")
    active_states = ("creating", "scheduled", "running", "failing", "blocked", "canceling")
    cohorts: list[tuple[str, dict]] = [
        (
            "recent_created",
            {
                "created_from": _utc_iso(query_start),
                "created_to": _utc_iso(query_end),
            },
        )
    ]
    if active_parent_start < query_start:
        cohorts.extend(
            (
                (
                    "older_active",
                    {
                        "state[]": list(active_states),
                        "created_from": _utc_iso(active_parent_start),
                        "created_to": _utc_iso(query_start),
                    },
                ),
                (
                    "older_finished",
                    {
                        "finished_from": _utc_iso(query_start),
                        "created_from": _utc_iso(active_parent_start),
                        "created_to": _utc_iso(query_start),
                    },
                ),
            )
        )
    return tuple(cohorts)


def fetch_rest_lifecycle_jobs(
    token: str,
    *,
    query_start: datetime,
    query_end: datetime,
    active_created_from: datetime | None = None,
    queue_by_id: dict[str, str],
    max_pages: int = REST_PAGE_SAFETY_CAP,
    page_fetcher=None,
) -> tuple[dict[str, dict], dict]:
    """Union pairwise-disjoint recent, older-active, and older-finished cohorts."""
    active_parent_start = active_created_from or query_start
    if active_parent_start > query_start:
        raise ValueError("active parent horizon cannot be narrower than the event query")
    fetch_page = page_fetcher or _request_build_page
    path = f"/organizations/{BK_ORG}/builds"
    common = {
        "include_retried_jobs": "true",
        "include_paused": "true",
        "exclude_pipeline": "true",
        "per_page": REST_PAGE_SIZE,
    }
    cohorts = _lifecycle_cohorts(
        query_start=query_start,
        query_end=query_end,
        active_parent_start=active_parent_start,
    )
    active_states = ("creating", "scheduled", "running", "failing", "blocked", "canceling")
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
        "parent_build_query_start": _utc_iso(active_parent_start),
        "event_cohort_query_start": _utc_iso(query_start),
        "active_parent_query_start": _utc_iso(active_parent_start),
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
    include_unretained: bool = False,
) -> tuple[list[dict], dict]:
    """Materialize one privacy-minimized observation per stable job UUID."""
    observations: list[dict] = []
    timestamp_coverage = {
        "scope": "current_api_query_before_ledger_merge",
        "jobs": len(jobs),
        "with_runnable_at": 0,
        "with_started_at": 0,
        "with_finished_at": 0,
        "events_in_retention": {"incoming": 0, "served": 0, "completed": 0},
        "duration_samples_in_retention": {"queue_wait": 0, "runtime": 0},
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
        for key in ("created_at", "runnable_at", "started_at", "finished_at"):
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
        if (
            _timestamp_in_window(timestamps["started_at"], retention_start, end_exclusive)
            and queue_wait is not None
        ):
            timestamp_coverage["duration_samples_in_retention"]["queue_wait"] += 1
        if (
            _timestamp_in_window(timestamps["finished_at"], retention_start, end_exclusive)
            and runtime is not None
        ):
            timestamp_coverage["duration_samples_in_retention"]["runtime"] += 1
        if keep or include_unretained:
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
    if (
        type(retry["retries_count"]) is not int
        or not 0 <= retry["retries_count"] <= 1_000_000
    ):
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
    if any(not item.is_file() for item in segment_paths) or not _segment_names_valid(
        item.name for item in segment_paths
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
    max_compressed: int = MAX_COMPRESSED_SEGMENT_BYTES,
) -> tuple[list[dict], int]:
    if len(compressed) > max_compressed:
        raise RuntimeError(
            f"compressed queue lifecycle segment at {source} exceeds the safety limit"
        )
    return _decode_job_ledger_stream_with_size(
        io.BytesIO(compressed),
        compressed_size=len(compressed),
        source=source,
        max_uncompressed=max_uncompressed,
        max_compressed=max_compressed,
    )


def _decode_job_ledger_stream_with_size(
    compressed_stream,
    *,
    compressed_size: int,
    source: str,
    max_uncompressed: int = MAX_UNCOMPRESSED_LEDGER_BYTES,
    max_compressed: int = MAX_COMPRESSED_SEGMENT_BYTES,
) -> tuple[list[dict], int]:
    """Decode one bounded gzip stream without materializing its full text."""
    if compressed_size < 0 or compressed_size > max_compressed:
        raise RuntimeError(
            f"compressed queue lifecycle segment at {source} exceeds the safety limit"
        )
    rows: list[dict] = []
    by_id: dict[str, dict] = {}
    decoded_size = 0
    try:
        with gzip.GzipFile(fileobj=compressed_stream, mode="rb") as archive:
            line_number = 0
            while True:
                raw_line = archive.readline(max_uncompressed - decoded_size + 1)
                if not raw_line:
                    break
                decoded_size += len(raw_line)
                if decoded_size > max_uncompressed:
                    raise RuntimeError(
                        "uncompressed queue lifecycle ledger exceeds the safety limit"
                    )
                line_number += 1
                try:
                    text = raw_line.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise RuntimeError(
                        f"queue lifecycle ledger at {source} is not UTF-8"
                    ) from exc
                if any(marker in text for marker in ("<<<<<<<", "=======", ">>>>>>>")):
                    raise RuntimeError(f"{source} contains merge conflict markers")
                if not text.strip():
                    continue
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"malformed {source} JSON on line {line_number}: {exc}"
                    ) from exc
                row = _validate_observation(decoded, line=line_number)
                job_id = row["job_id"]
                previous = by_id.get(job_id)
                if previous is not None and previous != row:
                    raise RuntimeError(f"{source} contains conflicting duplicate job {job_id}")
                if previous is None:
                    by_id[job_id] = row
                    rows.append(row)
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise RuntimeError(f"unreadable compressed queue lifecycle ledger at {source}") from exc
    return rows, decoded_size


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


def _observation_line(row: dict) -> bytes:
    return (
        json.dumps(
            _validate_observation(row),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _encode_job_lines(lines: list[bytes]) -> tuple[bytes, int]:
    text = b"".join(lines)
    if len(text) > MAX_UNCOMPRESSED_LEDGER_BYTES:
        raise RuntimeError("uncompressed queue lifecycle ledger exceeds the safety limit")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0, compresslevel=9) as archive:
        archive.write(text)
    return buffer.getvalue(), len(text)


def encode_job_ledger(rows: Iterable[dict]) -> bytes:
    compressed, _ = _encode_job_lines([_observation_line(row) for row in rows])
    if len(compressed) > MAX_COMPRESSED_SEGMENT_BYTES:
        raise RuntimeError(
            f"compressed queue lifecycle segment is {len(compressed)} bytes; "
            f"limit is {MAX_COMPRESSED_SEGMENT_BYTES}"
        )
    return compressed


def _adaptive_day_payloads(segment_rows: list[dict]) -> list[tuple[bytes, int, int]]:
    """Encode a day, bisecting sorted IDs until every gzip blob fits.

    The actual deterministic gzip size decides every split. This does not
    assume compressed size is monotonic as rows are added, and a recursive
    leaf can fail only when its sole canonical observation is itself larger
    than the per-file bound.
    """
    ordered = sorted(segment_rows, key=lambda row: row["job_id"])
    lines = [_observation_line(row) for row in ordered]

    def encode_range(start: int, end: int) -> list[tuple[bytes, int, int]]:
        uncompressed = sum(len(line) for line in lines[start:end])
        if uncompressed > MAX_UNCOMPRESSED_LEDGER_BYTES:
            if end - start == 1:
                raise RuntimeError(
                    "single queue lifecycle observation cannot fit the uncompressed "
                    f"ledger safety limit of {MAX_UNCOMPRESSED_LEDGER_BYTES} bytes"
                )
            midpoint = start + (end - start) // 2
            return encode_range(start, midpoint) + encode_range(midpoint, end)
        payload, uncompressed = _encode_job_lines(lines[start:end])
        if len(payload) <= MAX_COMPRESSED_SEGMENT_BYTES:
            return [(payload, end - start, uncompressed)]
        if end - start == 1:
            raise RuntimeError(
                "single queue lifecycle observation cannot fit the compressed "
                f"per-file safety limit of {MAX_COMPRESSED_SEGMENT_BYTES} bytes"
            )
        midpoint = start + (end - start) // 2
        return encode_range(start, midpoint) + encode_range(midpoint, end)

    return encode_range(0, len(lines))


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


def _latest_retained_event(
    row: dict, retention_start: datetime, end_exclusive: datetime
) -> datetime:
    retained = [
        _require_datetime(row["timestamps"][key], key)
        for key in ("runnable_at", "started_at", "finished_at")
        if row["timestamps"].get(key)
        and retention_start <= _require_datetime(row["timestamps"][key], key) < end_exclusive
    ]
    if not retained:
        raise RuntimeError(f"job {row['job_id']} has no retained lifecycle event")
    return max(retained)


def _encode_job_segments_exact(
    rows: Iterable[dict], *, retention_start: datetime, end_exclusive: datetime
) -> tuple[dict[str, bytes], dict]:
    """Encode one candidate generation or signal that its aggregate is reducible."""
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
        day_uncompressed = sum(len(_observation_line(row)) for row in segment_rows)
        total_uncompressed += day_uncompressed
        if total_uncompressed > MAX_UNCOMPRESSED_LEDGER_BYTES:
            raise _LedgerAggregateLimitExceeded(
                "uncompressed queue lifecycle segments exceed the total safety limit"
            )
        day_payloads = _adaptive_day_payloads(segment_rows)
        part_total = len(day_payloads)
        for part_index, (payload, row_count, uncompressed) in enumerate(day_payloads, start=1):
            name = (
                f"{day}.jsonl.gz"
                if part_total == 1
                else (
                    f"{day}.part-{part_index:0{_SEGMENT_PART_WIDTH}d}"
                    f"-of-{part_total:0{_SEGMENT_PART_WIDTH}d}.jsonl.gz"
                )
            )
            total += len(payload)
            if total > MAX_COMPRESSED_LEDGER_BYTES:
                raise _LedgerAggregateLimitExceeded(
                    "compressed queue lifecycle segments exceed the total safety limit"
                )
            digest = hashlib.sha256(payload).hexdigest()
            payloads[name] = payload
            segment_metadata[name] = {
                "compressed_bytes": len(payload),
                "job_observations": row_count,
                "uncompressed_bytes": uncompressed,
                "sha256": digest,
            }
    if not _segment_names_valid(payloads):
        raise RuntimeError("adaptive queue lifecycle segment names are not canonical")
    generation = _segment_generation_sha(segment_metadata)
    metadata = {
        "format": SEGMENT_FORMAT,
        "segment_naming": SEGMENT_NAMING,
        "partitioning": SEGMENT_PARTITIONING,
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


def _retention_day_in_window(
    value: object, *, retention_start: datetime, end_exclusive: datetime
) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if retention_start.date() <= parsed.date() < end_exclusive.date() or (
        parsed.date() == end_exclusive.date() and end_exclusive.time() != datetime.min.time()
    ):
        return value
    return None


def _carried_byte_limited_days(
    prior_retention_scopes: Iterable[dict],
    *,
    retention_start: datetime,
    end_exclusive: datetime,
) -> list[str]:
    days: set[str] = set()
    for scope in prior_retention_scopes:
        if not isinstance(scope, dict) or scope.get("byte_limited") is not True:
            continue
        for field in (
            "omitted_whole_latest_event_days",
            "carried_forward_omitted_latest_event_days",
        ):
            values = scope.get(field)
            if not isinstance(values, list):
                continue
            for value in values:
                day = _retention_day_in_window(
                    value,
                    retention_start=retention_start,
                    end_exclusive=end_exclusive,
                )
                if day is not None:
                    days.add(day)
        partial = _retention_day_in_window(
            scope.get("partial_latest_event_day"),
            retention_start=retention_start,
            end_exclusive=end_exclusive,
        )
        if partial is not None:
            days.add(partial)
    return sorted(days)


def _ledger_retention_metadata(
    source_rows: list[dict],
    retained_rows: list[dict],
    *,
    retention_start: datetime,
    end_exclusive: datetime,
    prior_retention_scopes: Iterable[dict],
    reset_prior_incompleteness: bool,
) -> dict:
    source_by_day: dict[str, list[dict]] = {}
    retained_by_day: dict[str, list[dict]] = {}
    for row in source_rows:
        day = _latest_retained_event(row, retention_start, end_exclusive).date().isoformat()
        source_by_day.setdefault(day, []).append(row)
    for row in retained_rows:
        day = _latest_retained_event(row, retention_start, end_exclusive).date().isoformat()
        retained_by_day.setdefault(day, []).append(row)

    omitted_whole_days = sorted(set(source_by_day) - set(retained_by_day))
    partial_days = sorted(
        day
        for day in set(source_by_day) & set(retained_by_day)
        if len(retained_by_day[day]) < len(source_by_day[day])
    )
    if len(partial_days) > 1:
        raise RuntimeError("queue lifecycle retention produced multiple partial boundary days")
    partial_day = partial_days[0] if partial_days else None
    omitted_whole_observations = sum(len(source_by_day[day]) for day in omitted_whole_days)
    partial_input = len(source_by_day.get(partial_day, [])) if partial_day else 0
    partial_published = len(retained_by_day.get(partial_day, [])) if partial_day else 0
    current_omitted = len(source_rows) - len(retained_rows)
    if current_omitted != omitted_whole_observations + partial_input - partial_published:
        raise RuntimeError("queue lifecycle retention omission accounting is inconsistent")

    carried_days = (
        []
        if reset_prior_incompleteness
        else _carried_byte_limited_days(
            prior_retention_scopes,
            retention_start=retention_start,
            end_exclusive=end_exclusive,
        )
    )
    current_limited = current_omitted > 0
    byte_limited = bool(current_limited or carried_days)
    latest_events = [
        _latest_retained_event(row, retention_start, end_exclusive) for row in retained_rows
    ]
    return {
        "schema_version": LEDGER_RETENTION_SCHEMA_VERSION,
        "policy": LEDGER_RETENTION_POLICY,
        "configured_days": RETENTION_DAYS,
        "configured_event_start": _utc_iso(retention_start),
        "end_exclusive": _utc_iso(end_exclusive),
        "max_compressed_bytes": MAX_COMPRESSED_LEDGER_BYTES,
        "input_job_observations": len(source_rows),
        "published_job_observations": len(retained_rows),
        "omitted_from_input_job_observations": current_omitted,
        "omitted_whole_day_job_observations": omitted_whole_observations,
        "omitted_whole_latest_event_days": omitted_whole_days,
        "partial_latest_event_day": partial_day,
        "partial_day_input_job_observations": partial_input,
        "partial_day_published_job_observations": partial_published,
        "carried_forward_omitted_latest_event_days": carried_days,
        "byte_limited": byte_limited,
        "complete_relative_to_input": not current_limited,
        "complete_relative_to_configured_window": not byte_limited,
        "published_latest_event_days": sorted(retained_by_day),
        "published_latest_event_start": (
            _utc_iso(min(latest_events)) if latest_events else None
        ),
        "published_latest_event_end": (
            _utc_iso(max(latest_events)) if latest_events else None
        ),
    }


def _prepare_job_segments(
    rows: Iterable[dict],
    *,
    retention_start: datetime,
    end_exclusive: datetime,
    prior_retention_scopes: Iterable[dict] = (),
    reset_prior_incompleteness: bool = False,
) -> tuple[list[dict], dict[str, bytes], dict]:
    """Return the newest deterministic observation suffix that fits all caps."""
    source_rows = sorted(
        (_validate_observation(value) for value in rows),
        key=lambda row: row["job_id"],
    )

    def attempt(candidate: list[dict]) -> tuple[dict[str, bytes], dict] | None:
        try:
            return _encode_job_segments_exact(
                candidate,
                retention_start=retention_start,
                end_exclusive=end_exclusive,
            )
        except _LedgerAggregateLimitExceeded:
            return None

    encoded = attempt(source_rows)
    retained_rows = source_rows
    if encoded is None:
        by_latest_day: dict[str, list[dict]] = {}
        for row in source_rows:
            day = _latest_retained_event(
                row, retention_start, end_exclusive
            ).date().isoformat()
            by_latest_day.setdefault(day, []).append(row)
        ordered_days = sorted(by_latest_day)
        for first_retained_day in range(1, len(ordered_days)):
            retained_rows = sorted(
                (
                    row
                    for day in ordered_days[first_retained_day:]
                    for row in by_latest_day[day]
                ),
                key=lambda row: row["job_id"],
            )
            encoded = attempt(retained_rows)
            if encoded is not None:
                break

        if encoded is None:
            if not ordered_days:
                raise RuntimeError("empty queue lifecycle ledger cannot exceed its byte cap")
            boundary_rows = sorted(
                by_latest_day[ordered_days[-1]], key=lambda row: row["job_id"]
            )
            # The secondary stable sort retains ascending IDs for equal event
            # timestamps while selecting the latest observations first.
            boundary_rows.sort(
                key=lambda row: _latest_retained_event(
                    row, retention_start, end_exclusive
                ),
                reverse=True,
            )
            one = attempt(boundary_rows[:1])
            if one is None:
                raise RuntimeError(
                    "single queue lifecycle observation cannot fit the aggregate "
                    f"ledger safety limits ({MAX_COMPRESSED_LEDGER_BYTES} compressed, "
                    f"{MAX_UNCOMPRESSED_LEDGER_BYTES} uncompressed bytes)"
                )
            retained_rows = boundary_rows[:1]
            encoded = one
            low = 2
            high = len(boundary_rows)
            while low <= high:
                midpoint = (low + high) // 2
                candidate = boundary_rows[:midpoint]
                candidate_encoded = attempt(candidate)
                if candidate_encoded is None:
                    high = midpoint - 1
                else:
                    retained_rows = candidate
                    encoded = candidate_encoded
                    low = midpoint + 1

    if encoded is None:  # pragma: no cover - all branches above establish a fit
        raise RuntimeError("queue lifecycle retention did not produce a bounded generation")
    retained_rows = sorted(retained_rows, key=lambda row: row["job_id"])
    payloads, metadata = encoded
    metadata["retention"] = _ledger_retention_metadata(
        source_rows,
        retained_rows,
        retention_start=retention_start,
        end_exclusive=end_exclusive,
        prior_retention_scopes=prior_retention_scopes,
        reset_prior_incompleteness=reset_prior_incompleteness,
    )
    return retained_rows, payloads, metadata


def encode_job_segments(
    rows: Iterable[dict],
    *,
    retention_start: datetime,
    end_exclusive: datetime,
    prior_retention_scopes: Iterable[dict] = (),
    reset_prior_incompleteness: bool = False,
) -> tuple[dict[str, bytes], dict]:
    _, payloads, metadata = _prepare_job_segments(
        rows,
        retention_start=retention_start,
        end_exclusive=end_exclusive,
        prior_retention_scopes=prior_retention_scopes,
        reset_prior_incompleteness=reset_prior_incompleteness,
    )
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


def _baseline_sha256(observations: Iterable[dict]) -> str:
    digest = hashlib.sha256(b"queue-lifecycle-canonical-baseline-v1\0")
    for row in sorted(
        (_validate_observation(value) for value in observations),
        key=lambda value: value["job_id"],
    ):
        digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _queue_identity_sha256(queue_by_id: dict[str, str]) -> str:
    if set(queue_by_id.values()) != set(AMD_METRIC_TARGET_QUEUES):
        raise RuntimeError("target queue identity map has incomplete scope")
    if len(queue_by_id) != len(AMD_METRIC_TARGET_QUEUES) or any(
        not isinstance(queue_id, str) or not queue_id
        for queue_id in queue_by_id
    ):
        raise RuntimeError("target queue identity map is malformed")
    by_queue = {queue: queue_id for queue_id, queue in queue_by_id.items()}
    payload = "".join(
        f"{queue}\0{by_queue[queue]}\n" for queue in sorted(by_queue)
    ).encode("utf-8")
    return hashlib.sha256(b"buildkite-cluster-queue-map-v1\0" + payload).hexdigest()


def _checkpoint_query(previous: dict, *, query_end: datetime) -> dict:
    query_plan = _collection_query_plan(previous, now=query_end)
    active_parent_start = query_end - timedelta(
        days=RETENTION_DAYS + PARENT_BUILD_LOOKBACK_DAYS
    )
    return {
        "query_mode": query_plan["query_mode"],
        "query_start": _utc_iso(query_plan["query_start"]),
        "active_parent_query_start": _utc_iso(active_parent_start),
        "query_end_exclusive": _utc_iso(query_end),
        "selection_reason": query_plan["selection_reason"],
        "watermark_before": query_plan["watermark_before"],
        "last_full_reconciliation_end": query_plan["last_full_reconciliation_end"],
    }


def _checkpoint_units(query: dict) -> list[dict]:
    cohorts = _lifecycle_cohorts(
        query_start=_require_datetime(query["query_start"], "checkpoint query_start"),
        query_end=_require_datetime(
            query["query_end_exclusive"], "checkpoint query_end_exclusive"
        ),
        active_parent_start=_require_datetime(
            query["active_parent_query_start"],
            "checkpoint active_parent_query_start",
        ),
    )
    return [
        {
            "cohort": name,
            "created_from": filters["created_from"],
            "created_to": filters["created_to"],
            "complete": False,
            "builds": 0,
            "command_jobs": 0,
            "target_jobs": 0,
        }
        for name, filters in cohorts
    ]


def _new_checkpoint(
    *,
    baseline_sha256: str,
    baseline_ref: str,
    previous: dict,
    query_end: datetime,
    queue_by_id: dict[str, str],
    queue_discovery: dict,
) -> dict:
    query = _checkpoint_query(previous, query_end=query_end)
    state = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "producer": CHECKPOINT_PRODUCER,
        "content_sha256": "0" * 64,
        "baseline_sha256": baseline_sha256,
        "baseline_ref": baseline_ref,
        "query": query,
        # Raw Buildkite queue UUIDs are deliberately never cached. The digest
        # binds every resumed page to a freshly resolved exact mapping.
        "queue_identity_sha256": _queue_identity_sha256(queue_by_id),
        "queue_discovery": dict(queue_discovery),
        "split_requests": 0,
        "terminal_error": None,
        "units": _checkpoint_units(query),
        "observations": [],
    }
    _refresh_checkpoint_integrity(state)
    return state


def _strict_json(raw: bytes, *, source: str) -> object:
    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate key {key!r}")
            value[key] = item
        return value

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"{source} is not strict UTF-8 JSON: {exc}") from exc


def _checkpoint_json(state: dict) -> bytes:
    return (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _checkpoint_content_sha256(state: dict) -> str:
    content = {key: value for key, value in state.items() if key != "content_sha256"}
    return hashlib.sha256(
        b"queue-lifecycle-wip-content-v1\0"
        + json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _refresh_checkpoint_integrity(state: dict) -> None:
    state["content_sha256"] = _checkpoint_content_sha256(state)


def _bounded_regular_file(path: Path, *, max_bytes: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"could not open bounded checkpoint {path}: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(f"checkpoint is not a regular file: {path}")
        if metadata.st_size <= 0 or metadata.st_size > max_bytes:
            raise RuntimeError(f"checkpoint compressed size is outside its safety bound: {path}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = handle.read(max_bytes + 1)
        if len(payload) != metadata.st_size or len(payload) > max_bytes:
            raise RuntimeError(f"checkpoint changed while it was read: {path}")
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _decode_checkpoint_file(path: Path) -> dict:
    compressed = _bounded_regular_file(
        path, max_bytes=MAX_CHECKPOINT_COMPRESSED_BYTES
    )
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as archive:
            decoded = _read_decompressed_limited(
                archive, MAX_CHECKPOINT_UNCOMPRESSED_BYTES
            )
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise RuntimeError(f"queue lifecycle checkpoint is unreadable: {exc}") from exc
    state = _strict_json(decoded, source="queue lifecycle checkpoint")
    if not isinstance(state, dict):
        raise RuntimeError("queue lifecycle checkpoint is not an object")
    normalized = _validate_checkpoint_shape(state)
    if decoded != _checkpoint_json(normalized):
        raise RuntimeError("queue lifecycle checkpoint is not canonical JSON")
    return normalized


def _nonnegative_checkpoint_int(value: object, field: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise RuntimeError(f"queue lifecycle checkpoint has invalid {field}")
    return value


def _validate_checkpoint_shape(state: object) -> dict:
    if not isinstance(state, dict) or set(state) != {
        "schema_version",
        "producer",
        "content_sha256",
        "baseline_sha256",
        "baseline_ref",
        "query",
        "queue_identity_sha256",
        "queue_discovery",
        "split_requests",
        "terminal_error",
        "units",
        "observations",
    }:
        raise RuntimeError("queue lifecycle checkpoint top-level schema is invalid")
    if (
        state.get("schema_version") != CHECKPOINT_SCHEMA_VERSION
        or state.get("producer") != CHECKPOINT_PRODUCER
    ):
        raise RuntimeError("queue lifecycle checkpoint producer/schema is unsupported")
    if not re.fullmatch(r"[0-9a-f]{64}", str(state.get("content_sha256") or "")) or state[
        "content_sha256"
    ] != _checkpoint_content_sha256(state):
        raise RuntimeError("queue lifecycle checkpoint content digest is invalid")
    for field in ("baseline_sha256", "queue_identity_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(state.get(field) or "")):
            raise RuntimeError(f"queue lifecycle checkpoint has invalid {field}")
    if not re.fullmatch(
        r"(?:[0-9a-f]{40}|bootstrap|local-[0-9a-f]{64})",
        str(state.get("baseline_ref") or ""),
    ):
        raise RuntimeError("queue lifecycle checkpoint has invalid baseline_ref")
    query = state.get("query")
    if not isinstance(query, dict) or set(query) != {
        "query_mode",
        "query_start",
        "active_parent_query_start",
        "query_end_exclusive",
        "selection_reason",
        "watermark_before",
        "last_full_reconciliation_end",
    }:
        raise RuntimeError("queue lifecycle checkpoint query schema is invalid")
    if query.get("query_mode") not in {FULL_QUERY_MODE, INCREMENTAL_QUERY_MODE}:
        raise RuntimeError("queue lifecycle checkpoint query mode is invalid")
    for field in ("query_start", "active_parent_query_start", "query_end_exclusive"):
        if _canonical_timestamp(query.get(field), field) != query.get(field):
            raise RuntimeError(f"queue lifecycle checkpoint has noncanonical {field}")
    for field in ("watermark_before", "last_full_reconciliation_end"):
        value = query.get(field)
        if value is not None and _canonical_timestamp(value, field) != value:
            raise RuntimeError(f"queue lifecycle checkpoint has noncanonical {field}")
    if not isinstance(query.get("selection_reason"), str) or not query["selection_reason"]:
        raise RuntimeError("queue lifecycle checkpoint selection reason is invalid")

    discovery = state.get("queue_discovery")
    if not isinstance(discovery, dict) or set(discovery) != {
        "complete",
        "pages",
        "target_queue_count",
    }:
        raise RuntimeError("queue lifecycle checkpoint queue discovery schema is invalid")
    if discovery.get("complete") is not True:
        raise RuntimeError("queue lifecycle checkpoint queue discovery is incomplete")
    _nonnegative_checkpoint_int(
        discovery.get("pages"), "queue discovery pages", maximum=REST_PAGE_SAFETY_CAP
    )
    if discovery.get("pages") < 1 or discovery.get("target_queue_count") != len(
        AMD_METRIC_TARGET_QUEUES
    ):
        raise RuntimeError("queue lifecycle checkpoint queue discovery scope is invalid")

    expected_roots = _checkpoint_units(query)
    units = state.get("units")
    if (
        not isinstance(units, list)
        or not expected_roots
        or not len(expected_roots) <= len(units) <= MAX_CHECKPOINT_QUERY_UNITS
    ):
        raise RuntimeError("queue lifecycle checkpoint query units are incomplete")
    split_requests = _nonnegative_checkpoint_int(
        state.get("split_requests"),
        "split request count",
        maximum=MAX_CHECKPOINT_QUERY_UNITS,
    )
    if len(units) != len(expected_roots) + split_requests:
        raise RuntimeError("queue lifecycle checkpoint split accounting is inconsistent")
    if state.get("terminal_error") not in {
        None,
        "checkpoint_capacity",
        "dense_minimum_interval",
        "query_unit_limit",
    }:
        raise RuntimeError("queue lifecycle checkpoint terminal state is invalid")
    seen_pending = False
    root_index = 0
    prior_end: str | None = None
    for unit in units:
        if not isinstance(unit, dict) or set(unit) != {
            "cohort",
            "created_from",
            "created_to",
            "complete",
            "builds",
            "command_jobs",
            "target_jobs",
        }:
            raise RuntimeError("queue lifecycle checkpoint query unit schema is invalid")
        if root_index >= len(expected_roots):
            raise RuntimeError("queue lifecycle checkpoint has an extra cohort")
        root = expected_roots[root_index]
        cohort = unit.get("cohort")
        if cohort != root["cohort"] or not isinstance(unit.get("complete"), bool):
            raise RuntimeError("queue lifecycle checkpoint query unit identity is invalid")
        created_from = unit.get("created_from")
        created_to = unit.get("created_to")
        if (
            _canonical_timestamp(created_from, "checkpoint unit created_from") != created_from
            or _canonical_timestamp(created_to, "checkpoint unit created_to") != created_to
            or _require_datetime(created_from, "checkpoint unit created_from")
            >= _require_datetime(created_to, "checkpoint unit created_to")
        ):
            raise RuntimeError("queue lifecycle checkpoint unit interval is invalid")
        expected_start = prior_end or root["created_from"]
        if created_from != expected_start:
            raise RuntimeError("queue lifecycle checkpoint unit intervals have a gap or overlap")
        if _require_datetime(created_to, "checkpoint unit created_to") > _require_datetime(
            root["created_to"], "checkpoint root created_to"
        ):
            raise RuntimeError("queue lifecycle checkpoint unit escapes its cohort")
        prior_end = created_to
        if created_to == root["created_to"]:
            root_index += 1
            prior_end = None
        if seen_pending and unit["complete"]:
            raise RuntimeError("queue lifecycle checkpoint completed units are not a prefix")
        seen_pending = seen_pending or not unit["complete"]
        builds = _nonnegative_checkpoint_int(
            unit.get("builds"), f"{cohort} builds", maximum=REST_PAGE_SIZE - 1
        )
        if not unit["complete"] and builds != 0:
            raise RuntimeError("queue lifecycle checkpoint pending unit has accepted builds")
        for field in ("command_jobs", "target_jobs"):
            count = _nonnegative_checkpoint_int(
                unit.get(field), f"{cohort} {field}", maximum=100_000_000
            )
            if not unit["complete"] and count != 0:
                raise RuntimeError("queue lifecycle checkpoint pending unit has accepted jobs")
    if root_index != len(expected_roots) or prior_end is not None:
        raise RuntimeError("queue lifecycle checkpoint does not cover every cohort exactly")

    observations = state.get("observations")
    if not isinstance(observations, list) or len(observations) > MAX_CHECKPOINT_OBSERVATIONS:
        raise RuntimeError("queue lifecycle checkpoint observation count exceeds its bound")
    normalized_observations = [_validate_observation(row) for row in observations]
    ids = [row["job_id"] for row in normalized_observations]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise RuntimeError("queue lifecycle checkpoint observations are not unique and sorted")
    normalized = dict(state)
    normalized["observations"] = normalized_observations
    return normalized


def _validate_checkpoint_context(
    state: dict,
    *,
    baseline_sha256: str,
    baseline_ref: str,
    previous: dict,
    now: datetime,
) -> dict:
    state = _validate_checkpoint_shape(state)
    if state["baseline_sha256"] != baseline_sha256:
        raise RuntimeError("queue lifecycle checkpoint canonical baseline changed")
    if state["baseline_ref"] != baseline_ref:
        raise RuntimeError("queue lifecycle checkpoint canonical ref changed")
    frozen_end = _require_datetime(
        state["query"]["query_end_exclusive"], "checkpoint query_end_exclusive"
    )
    if frozen_end > now:
        raise RuntimeError("queue lifecycle checkpoint query horizon is future")
    if state["query"] != _checkpoint_query(previous, query_end=frozen_end):
        raise RuntimeError(
            "queue lifecycle checkpoint query is not bound to the canonical watermark"
        )
    return state


def _write_checkpoint(path: Path, state: dict) -> None:
    _refresh_checkpoint_integrity(state)
    state = _validate_checkpoint_shape(state)
    decoded = _checkpoint_json(state)
    if len(decoded) > MAX_CHECKPOINT_UNCOMPRESSED_BYTES:
        raise RuntimeError("queue lifecycle checkpoint exceeds its uncompressed safety limit")
    buffer = io.BytesIO()
    with gzip.GzipFile(
        fileobj=buffer,
        mode="wb",
        filename="",
        mtime=0,
        compresslevel=9,
    ) as archive:
        archive.write(decoded)
    compressed = buffer.getvalue()
    if len(compressed) > MAX_CHECKPOINT_COMPRESSED_BYTES:
        raise RuntimeError("queue lifecycle checkpoint exceeds its compressed safety limit")
    _atomic_write_bytes(path, compressed)


def _discard_checkpoint(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError(f"could not discard invalid lifecycle checkpoint {path}: {exc}") from exc


def _load_checkpoint(
    path: Path,
    *,
    baseline_sha256: str,
    baseline_ref: str,
    previous: dict,
    now: datetime,
) -> dict | None:
    if not path.exists():
        return None
    try:
        return _validate_checkpoint_context(
            _decode_checkpoint_file(path),
            baseline_sha256=baseline_sha256,
            baseline_ref=baseline_ref,
            previous=previous,
            now=now,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        log.warning("Discarding unusable private lifecycle checkpoint: %s", exc)
        _discard_checkpoint(path)
        return None


def _checkpoint_cache_marker(path: Path) -> Path:
    return path.parent / ".cleared"


def clear_lifecycle_checkpoint(path: Path) -> None:
    """Remove WIP after durable publication and leave a cache tombstone."""
    _discard_checkpoint(path)
    _atomic_write_text(
        _checkpoint_cache_marker(path),
        "queue-lifecycle-wip-cleared-v1\n",
    )


def prepare_lifecycle_checkpoint_cache(path: Path) -> None:
    """Validate cached WIP structurally, or cache only a safe tombstone."""
    if path.exists():
        try:
            _decode_checkpoint_file(path)
        except (OSError, RuntimeError, ValueError) as exc:
            log.warning("Discarding invalid lifecycle WIP before cache save: %s", exc)
            _discard_checkpoint(path)
    _atomic_write_text(
        _checkpoint_cache_marker(path),
        "queue-lifecycle-private-cache-v1\n",
    )


def _cohort_filter_map(query: dict) -> dict[str, dict]:
    return dict(
        _lifecycle_cohorts(
            query_start=_require_datetime(query["query_start"], "checkpoint query_start"),
            query_end=_require_datetime(
                query["query_end_exclusive"], "checkpoint query_end_exclusive"
            ),
            active_parent_start=_require_datetime(
                query["active_parent_query_start"],
                "checkpoint active_parent_query_start",
            ),
        )
    )


def _merge_checkpoint_observations(existing: list[dict], incoming: list[dict]) -> list[dict]:
    merged = {row["job_id"]: _validate_observation(row) for row in existing}
    for value in incoming:
        row = _validate_observation(value)
        previous = merged.get(row["job_id"])
        merged[row["job_id"]] = _merge_observation(previous, row) if previous else row
    if len(merged) > MAX_CHECKPOINT_OBSERVATIONS:
        raise RuntimeError("queue lifecycle checkpoint observation count exceeds its bound")
    return [merged[job_id] for job_id in sorted(merged)]


def _split_checkpoint_unit(state: dict, index: int) -> None:
    unit = state["units"][index]
    start = _require_datetime(unit["created_from"], "query unit created_from")
    end = _require_datetime(unit["created_to"], "query unit created_to")
    midpoint = start + (end - start) / 2
    if midpoint <= start or midpoint >= end:
        raise RuntimeError(
            f"REST build cohort {unit['cohort']} remains full at the minimum time interval"
        )
    if len(state["units"]) >= MAX_CHECKPOINT_QUERY_UNITS:
        raise RuntimeError("REST lifecycle query exceeded its bounded unit count")

    def child(child_start: datetime, child_end: datetime) -> dict:
        return {
            "cohort": unit["cohort"],
            "created_from": _utc_iso(child_start),
            "created_to": _utc_iso(child_end),
            "complete": False,
            "builds": 0,
            "command_jobs": 0,
            "target_jobs": 0,
        }

    state["units"][index : index + 1] = [
        child(start, midpoint),
        child(midpoint, end),
    ]
    state["split_requests"] += 1


def _resume_lifecycle_query_units(
    token: str,
    *,
    checkpoint_path: Path,
    state: dict,
    queue_by_id: dict[str, str],
    page_fetcher=None,
    deadline_monotonic: float | None = None,
) -> dict:
    """Advance exhaustive offset-free query leaves one API response at a time."""
    fetch_page = _lifecycle_page_fetcher(
        page_fetcher,
        deadline_monotonic=deadline_monotonic,
    )
    path = f"/organizations/{BK_ORG}/builds"
    common = {
        "include_retried_jobs": "true",
        "include_paused": "true",
        "exclude_pipeline": "true",
        "per_page": REST_PAGE_SIZE,
        # A leaf is accepted only when page one is short. Full leaves are
        # split by their exact parent-created interval and never persisted.
        "page": 1,
    }
    root_filters = _cohort_filter_map(state["query"])
    retention_start = _require_datetime(
        state["query"]["query_end_exclusive"], "checkpoint query end"
    ) - timedelta(days=RETENTION_DAYS)
    query_end = _require_datetime(
        state["query"]["query_end_exclusive"], "checkpoint query end"
    )

    while True:
        _require_lifecycle_time(deadline_monotonic)
        try:
            index = next(
                index
                for index, unit in enumerate(state["units"])
                if not unit["complete"]
            )
        except StopIteration:
            return state
        current_compressed_size = checkpoint_path.stat().st_size
        if (
            len(_checkpoint_json(state))
            > MAX_CHECKPOINT_UNCOMPRESSED_BYTES - CHECKPOINT_WRITE_HEADROOM_BYTES
            or current_compressed_size
            > MAX_CHECKPOINT_COMPRESSED_BYTES - CHECKPOINT_WRITE_HEADROOM_BYTES
        ):
            state["terminal_error"] = "checkpoint_capacity"
            _write_checkpoint(checkpoint_path, state)
            raise RuntimeError(
                "queue lifecycle checkpoint reached its bounded recovery capacity"
            )
        unit = state["units"][index]
        filters = {
            **root_filters[unit["cohort"]],
            "created_from": unit["created_from"],
            "created_to": unit["created_to"],
        }
        rows = fetch_page(path, token, {**common, **filters})
        if len(rows) > REST_PAGE_SIZE:
            raise RuntimeError("REST lifecycle query returned more than its requested page size")

        # Validate even an ambiguous full response, but retain none of it: a
        # child interval will be queried exhaustively and projected later.
        page_jobs: dict[str, dict] = {}
        commands, targets = _project_rest_builds(rows, queue_by_id, page_jobs)
        if len(rows) == REST_PAGE_SIZE:
            try:
                _split_checkpoint_unit(state, index)
            except RuntimeError as exc:
                state["terminal_error"] = (
                    "query_unit_limit"
                    if "unit count" in str(exc)
                    else "dense_minimum_interval"
                )
                _write_checkpoint(checkpoint_path, state)
                raise
            _write_checkpoint(checkpoint_path, state)
            _require_lifecycle_time(deadline_monotonic)
            log.info(
                "Split full lifecycle %s interval %s -> %s",
                unit["cohort"],
                unit["created_from"],
                unit["created_to"],
            )
            continue

        page_observations, _ = observations_from_jobs(
            page_jobs,
            retention_start=retention_start,
            end_exclusive=query_end,
            include_unretained=True,
        )
        state["observations"] = _merge_checkpoint_observations(
            state["observations"], page_observations
        )
        unit = state["units"][index]
        unit.update(
            complete=True,
            builds=len(rows),
            command_jobs=commands,
            target_jobs=targets,
        )
        # The cursor and the privacy projection become durable together. A
        # killed write cannot advance one without the other.
        _write_checkpoint(checkpoint_path, state)
        _require_lifecycle_time(deadline_monotonic)
        log.info(
            "Completed lifecycle %s interval %s -> %s (%d builds, %d target jobs)",
            unit["cohort"],
            unit["created_from"],
            unit["created_to"],
            len(rows),
            targets,
        )


def _checkpoint_source_coverage(state: dict) -> dict:
    filters = _cohort_filter_map(state["query"])
    cohort_coverage: dict[str, dict] = {}
    raw_builds = 0
    raw_commands = 0
    raw_targets = 0
    for cohort, cohort_filters in filters.items():
        leaves = [unit for unit in state["units"] if unit["cohort"] == cohort]
        builds = sum(unit["builds"] for unit in leaves)
        commands = sum(unit["command_jobs"] for unit in leaves)
        targets = sum(unit["target_jobs"] for unit in leaves)
        raw_builds += builds
        raw_commands += commands
        raw_targets += targets
        cohort_coverage[cohort] = {
            "complete": all(unit["complete"] for unit in leaves),
            "query_units": len(leaves),
            "builds": builds,
            "command_jobs": commands,
            "target_jobs": targets,
            "filters": cohort_filters,
        }
    active_states = ("creating", "scheduled", "running", "failing", "blocked", "canceling")
    return {
        "complete": all(unit["complete"] for unit in state["units"]),
        "source": "Buildkite REST organization builds",
        "organization_wide": True,
        "pagination_strategy": "exhaustive_disjoint_created_time_units_page_one",
        "cohorts": cohort_coverage,
        "split_probe_requests": state["split_requests"],
        "accepted_query_units": len(state["units"]),
        "active_build_states": list(active_states),
        "parent_build_query_start": state["query"]["active_parent_query_start"],
        "event_cohort_query_start": state["query"]["query_start"],
        "active_parent_query_start": state["query"]["active_parent_query_start"],
        "query_horizon_exclusive": state["query"]["query_end_exclusive"],
        "raw_builds": raw_builds,
        "raw_command_jobs": raw_commands,
        "raw_target_jobs": raw_targets,
        "unique_target_jobs": len(state["observations"]),
    }


def _checkpoint_timestamp_coverage(state: dict) -> dict:
    query_end = _require_datetime(
        state["query"]["query_end_exclusive"], "checkpoint query end"
    )
    coverage = _retained_timestamp_coverage(
        state["observations"],
        query_end - timedelta(days=RETENTION_DAYS),
        query_end,
    )
    coverage["scope"] = "current_api_query_before_ledger_merge"
    return coverage


def _collect_rest_lifecycle_resumable(
    token: str,
    *,
    checkpoint_path: Path,
    existing: list[dict],
    previous: dict,
    now: datetime,
    baseline_ref: str | None = None,
    page_fetcher=None,
    queue_page_fetcher=None,
    deadline_monotonic: float | None = None,
) -> tuple[dict, list[dict], dict, dict, dict]:
    baseline_sha256 = _baseline_sha256(existing)
    baseline_ref = baseline_ref or f"local-{baseline_sha256}"
    if not re.fullmatch(
        r"(?:[0-9a-f]{40}|bootstrap|local-[0-9a-f]{64})", baseline_ref
    ):
        raise RuntimeError("queue lifecycle canonical baseline ref is invalid")
    state = _load_checkpoint(
        checkpoint_path,
        baseline_sha256=baseline_sha256,
        baseline_ref=baseline_ref,
        previous=previous,
        now=now,
    )
    if state is not None and state["terminal_error"] is not None:
        raise RuntimeError(
            "queue lifecycle checkpoint records terminal bounded-query failure: "
            + state["terminal_error"]
        )
    if state is None or not all(unit["complete"] for unit in state["units"]):
        if queue_page_fetcher is None and deadline_monotonic is None:
            queue_by_id, queue_discovery = fetch_rest_target_queues(token)
        else:
            queue_by_id, queue_discovery = fetch_rest_target_queues(
                token,
                page_fetcher=_lifecycle_page_fetcher(
                    queue_page_fetcher,
                    deadline_monotonic=deadline_monotonic,
                ),
            )
        queue_identity = _queue_identity_sha256(queue_by_id)
        if state is not None and state["queue_identity_sha256"] != queue_identity:
            log.warning("Target queue identities changed; restarting the frozen lifecycle query")
            _discard_checkpoint(checkpoint_path)
            state = None
        if state is None:
            state = _new_checkpoint(
                baseline_sha256=baseline_sha256,
                baseline_ref=baseline_ref,
                previous=previous,
                query_end=now,
                queue_by_id=queue_by_id,
                queue_discovery=queue_discovery,
            )
        else:
            state["queue_discovery"] = queue_discovery
        _write_checkpoint(checkpoint_path, state)
        _require_lifecycle_time(deadline_monotonic)
        state = _resume_lifecycle_query_units(
            token,
            checkpoint_path=checkpoint_path,
            state=state,
            queue_by_id=queue_by_id,
            page_fetcher=page_fetcher,
            deadline_monotonic=deadline_monotonic,
        )

    if not all(unit["complete"] for unit in state["units"]):
        raise RuntimeError("queue lifecycle checkpoint remained incomplete")
    return (
        state,
        list(state["observations"]),
        dict(state["queue_discovery"]),
        _checkpoint_source_coverage(state),
        _checkpoint_timestamp_coverage(state),
    )


def _publish_generation(
    jobs_path: Path,
    segment_payloads: dict[str, bytes],
    summary_path: Path,
    summary_text: str,
) -> None:
    """Publish linked artifacts, preserving the old ledger on every failure."""
    summary_size = len(summary_text.encode("utf-8"))
    if summary_size > MAX_SUMMARY_BYTES:
        raise RuntimeError(
            f"queue lifecycle summary is {summary_size} bytes; "
            f"limit is {MAX_SUMMARY_BYTES}"
        )
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(dir=jobs_path.parent, prefix=f".{jobs_path.name}.stage."))
    backup: Path | None = None
    old_generation_moved = False
    new_generation_installed = False
    try:
        if not _segment_names_valid(segment_payloads):
            raise RuntimeError("invalid lifecycle segment name set")
        for name, payload in sorted(segment_payloads.items()):
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


def _retained_timestamp_coverage(
    observations: Iterable[dict],
    start: datetime,
    end_exclusive: datetime,
) -> dict:
    """Describe the exact retained-ledger scope used by every public aggregate."""
    coverage = {
        "scope": "retained_job_ledger",
        "jobs": 0,
        "with_runnable_at": 0,
        "with_started_at": 0,
        "with_finished_at": 0,
        "events_in_retention": {"incoming": 0, "served": 0, "completed": 0},
        "duration_samples_in_retention": {"queue_wait": 0, "runtime": 0},
    }
    event_fields = {
        "runnable_at": "incoming",
        "started_at": "served",
        "finished_at": "completed",
    }
    for row in observations:
        coverage["jobs"] += 1
        timestamps = row["timestamps"]
        for field, event in event_fields.items():
            value = timestamps.get(field)
            if value is not None:
                coverage[f"with_{field}"] += 1
            if _timestamp_in_window(value, start, end_exclusive):
                coverage["events_in_retention"][event] += 1
        durations = row.get("durations_seconds") or {}
        if (
            _timestamp_in_window(timestamps.get("started_at"), start, end_exclusive)
            and durations.get("queue_wait") is not None
        ):
            coverage["duration_samples_in_retention"]["queue_wait"] += 1
        if (
            _timestamp_in_window(timestamps.get("finished_at"), start, end_exclusive)
            and durations.get("runtime") is not None
        ):
            coverage["duration_samples_in_retention"]["runtime"] += 1
    return coverage


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


def _day_floor(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _daily_wait_times(
    observations: list[dict], retention_start: datetime, end_exclusive: datetime
) -> dict:
    """Return every observed served-job wait, partitioned by UTC start day.

    Queue wait becomes a complete observation at ``started_at``.  Ledger files
    cannot be used as the day boundary because they are partitioned by each
    job's earliest retained lifecycle event, which may be ``runnable_at`` on
    the preceding day.  Sorting the values makes this distribution vector
    deterministic without publishing job identities or event timestamps.
    """
    waits_by_day: dict[str, list[float]] = {}
    for row in observations:
        started_at = row["timestamps"].get("started_at")
        if not _timestamp_in_window(started_at, retention_start, end_exclusive):
            continue
        queue_wait = (row.get("durations_seconds") or {}).get("queue_wait")
        if queue_wait is None:
            continue
        day = _require_datetime(started_at, "started_at").date().isoformat()
        waits_by_day.setdefault(day, []).append(float(queue_wait))

    days: list[dict] = []
    cursor = _day_floor(retention_start)
    while cursor < end_exclusive:
        calendar_end = cursor + timedelta(days=1)
        observed_start = max(cursor, retention_start)
        observed_end = min(calendar_end, end_exclusive)
        waits = sorted(waits_by_day.get(cursor.date().isoformat(), []))
        days.append(
            {
                "date": cursor.date().isoformat(),
                "start": _utc_iso(observed_start),
                "end_exclusive": _utc_iso(observed_end),
                "partial": observed_start != cursor or observed_end != calendar_end,
                "sample_count": len(waits),
                "served_job_wait_seconds": waits,
            }
        )
        cursor = calendar_end

    return {
        "unit": "seconds",
        "day_timezone": "UTC",
        "attributed_by": "timestamps.started_at",
        "days": days,
    }


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
    items = sorted(path.iterdir())
    if any(not item.is_file() for item in items) or not _segment_names_valid(
        item.name for item in items
    ):
        return ""
    for item in items:
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


def validate_local_ledger_generation(*, jobs_path: Path, summary_path: Path) -> tuple[int, int]:
    """Validate that a local summary and ledger are one exact generation.

    A zero-segment manifest is a valid idle/bootstrap generation. Any files
    that do exist must match the manifest byte-for-byte and decode as complete
    lifecycle observations before the workflow may publish them.
    """
    if not summary_path.is_file():
        raise RuntimeError(f"queue lifecycle summary is missing: {summary_path}")
    summary_bytes = summary_path.read_bytes()
    if len(summary_bytes) > MAX_SUMMARY_BYTES:
        raise RuntimeError("queue lifecycle summary exceeds the safety limit")
    try:
        summary = json.loads(summary_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("queue lifecycle summary is not valid UTF-8 JSON") from exc
    ledger = _summary_provenance(summary).get("ledger")
    if not _ledger_manifest_complete(ledger):
        raise RuntimeError("queue lifecycle summary lacks a complete ledger manifest")

    if jobs_path.exists() and not jobs_path.is_dir():
        raise RuntimeError(f"queue lifecycle ledger path is not a directory: {jobs_path}")
    segment_paths = sorted(jobs_path.iterdir()) if jobs_path.exists() else []
    if any(not item.is_file() for item in segment_paths) or not _segment_names_valid(
        item.name for item in segment_paths
    ):
        raise RuntimeError(
            f"queue lifecycle ledger directory contains an unexpected entry: {jobs_path}"
        )
    expected_segments = ledger["segments"]
    actual_names = {item.name for item in segment_paths}
    if actual_names != set(expected_segments):
        raise RuntimeError("queue lifecycle ledger files do not match the summary manifest")

    actual_segments: dict[str, dict] = {}
    seen: set[str] = set()
    observations: list[dict] = []
    total_compressed = 0
    total_uncompressed = 0
    for segment_path in segment_paths:
        payload = segment_path.read_bytes()
        total_compressed += len(payload)
        if len(payload) > MAX_COMPRESSED_SEGMENT_BYTES:
            raise RuntimeError("compressed queue lifecycle segment exceeds the per-file safety limit")
        if total_compressed > MAX_COMPRESSED_LEDGER_BYTES:
            raise RuntimeError("compressed queue lifecycle segments exceed the total safety limit")
        segment_rows, uncompressed_bytes = _decode_job_ledger_with_size(
            payload,
            source=str(segment_path),
            max_uncompressed=MAX_UNCOMPRESSED_LEDGER_BYTES - total_uncompressed,
        )
        total_uncompressed += uncompressed_bytes
        for row in segment_rows:
            if row["job_id"] in seen:
                raise RuntimeError(
                    f"job {row['job_id']} occurs in multiple lifecycle segments"
                )
            seen.add(row["job_id"])
            observations.append(row)
        metadata = {
            "compressed_bytes": len(payload),
            "job_observations": len(segment_rows),
            "uncompressed_bytes": uncompressed_bytes,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        if any(
            expected_segments[segment_path.name].get(key) != value
            for key, value in metadata.items()
        ):
            raise RuntimeError(
                f"queue lifecycle segment does not match its manifest: {segment_path.name}"
            )
        actual_segments[segment_path.name] = metadata

    if (
        len(segment_paths) != ledger["segment_count"]
        or len(seen) != ledger["job_observations"]
        or total_compressed != ledger["total_compressed_bytes"]
        or total_uncompressed != ledger["total_uncompressed_bytes"]
        or _segment_generation_sha(actual_segments) != ledger["generation_sha256"]
    ):
        raise RuntimeError("queue lifecycle ledger volume does not match the summary manifest")

    retention = summary.get("retention")
    if not isinstance(retention, dict) or retention.get("days") != RETENTION_DAYS:
        raise RuntimeError("queue lifecycle summary has an invalid retention contract")
    retention_start = _require_datetime(retention.get("event_start"), "retention.event_start")
    retention_end = _require_datetime(
        retention.get("end_exclusive"), "retention.end_exclusive"
    )
    if retention_end - retention_start != timedelta(days=RETENTION_DAYS):
        raise RuntimeError("queue lifecycle summary retention window is inconsistent")
    ledger_retention = ledger.get("retention")
    if ledger_retention is not None:
        if (
            retention.get("ledger_scope") != ledger_retention
            or retention.get("byte_limited") is not ledger_retention["byte_limited"]
            or retention.get("actual_published_latest_event_start")
            != ledger_retention["published_latest_event_start"]
            or retention.get("actual_published_latest_event_end")
            != ledger_retention["published_latest_event_end"]
            or (summary.get("coverage") or {}).get("ledger_retention")
            != ledger_retention
        ):
            raise RuntimeError(
                "queue lifecycle summary retained scope does not match the ledger"
            )
        latest_events = [
            _latest_retained_event(row, retention_start, retention_end)
            for row in observations
        ]
        actual_days = sorted({value.date().isoformat() for value in latest_events})
        if (
            ledger_retention["published_job_observations"] != len(observations)
            or ledger_retention["published_latest_event_days"] != actual_days
            or ledger_retention["published_latest_event_start"]
            != (_utc_iso(min(latest_events)) if latest_events else None)
            or ledger_retention["published_latest_event_end"]
            != (_utc_iso(max(latest_events)) if latest_events else None)
        ):
            raise RuntimeError(
                "queue lifecycle ledger retained scope does not match its observations"
            )
    expected_daily = _daily_wait_times(observations, retention_start, retention_end)
    actual_daily = summary.get("daily_wait_times")
    if not isinstance(actual_daily, dict):
        raise RuntimeError("queue lifecycle summary has no daily wait evidence")
    for field in ("unit", "day_timezone", "attributed_by"):
        if actual_daily.get(field) != expected_daily[field]:
            raise RuntimeError("queue lifecycle daily wait metadata does not match the ledger")
    actual_days = actual_daily.get("days")
    if not isinstance(actual_days, list) or len(actual_days) != len(expected_daily["days"]):
        raise RuntimeError("queue lifecycle daily wait days do not match the ledger")
    compacted_dates: list[str] = []
    published_samples = 0
    observed_samples = 0
    for actual, expected in zip(actual_days, expected_daily["days"], strict=True):
        if not isinstance(actual, dict):
            raise RuntimeError("queue lifecycle daily wait row is invalid")
        observed_samples += expected["sample_count"]
        if actual.get("vector_complete") is not False:
            if actual != expected:
                raise RuntimeError("queue lifecycle daily wait vector does not match the ledger")
            published_samples += expected["sample_count"]
            continue
        waits = expected["served_job_wait_seconds"]
        compacted = {
            **{key: value for key, value in expected.items() if key != "served_job_wait_seconds"},
            "served_job_wait_seconds": [],
            "vector_complete": False,
            "published_sample_count": 0,
            "omitted_sample_count": len(waits),
            "distribution": _duration_summary(waits),
        }
        if not waits or actual != compacted:
            raise RuntimeError("queue lifecycle compacted wait evidence does not match the ledger")
        compacted_dates.append(expected["date"])
    expected_vector_coverage = (
        {
            "complete": False,
            "observed_sample_count": observed_samples,
            "published_sample_count": published_samples,
            "compacted_dates": compacted_dates,
            "method": "oldest_whole_day_vectors_replaced_by_exact_distribution_summary",
        }
        if compacted_dates
        else None
    )
    if actual_daily.get("vector_coverage") != expected_vector_coverage:
        raise RuntimeError("queue lifecycle daily vector coverage does not match the ledger")
    return len(segment_paths), len(seen)


def _provenance_datetime(value: object) -> datetime | None:
    """Return a trustworthy UTC provenance timestamp, or ``None``.

    Watermarks are control-plane state, so unlike Buildkite payload timestamps
    a timezone-less value is not safe to interpret using the runner's locale.
    Invalid state selects a full reconciliation rather than narrowing a query.
    """
    parsed = parse_iso(value if isinstance(value, str) else None)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _previous_full_reconciliation(previous_provenance: dict) -> datetime | None:
    """Read the full-reconciliation watermark, including schema-v1 migration.

    Existing published documents predate ``last_full_reconciliation_end`` but
    identify their last successful query as ``full_retention_cohort_union``.
    Treat that query end as the initial full-reconciliation watermark. This is
    safe only after ``_safe_previous_provenance`` has bound the summary to the
    exact retained ledger generation.
    """
    if "last_full_reconciliation_end" in previous_provenance:
        # Once the additive field exists, malformed or missing content is not a
        # legacy document and must fail safe to a new full reconciliation.
        return _provenance_datetime(
            previous_provenance.get("last_full_reconciliation_end")
        )
    if previous_provenance.get("last_successful_query_mode") == FULL_QUERY_MODE:
        return _provenance_datetime(previous_provenance.get("last_successful_query_end"))
    return None


def _collection_query_plan(previous_provenance: dict, *, now: datetime) -> dict:
    """Choose an incremental overlap or fail-safe full reconciliation."""
    full_start = now - timedelta(
        days=RETENTION_DAYS + PARENT_BUILD_LOOKBACK_DAYS
    )
    watermark = _provenance_datetime(
        previous_provenance.get("last_successful_query_end")
    )
    last_full = _previous_full_reconciliation(previous_provenance)
    reason = "incremental_watermark"

    if watermark is None:
        reason = "missing_or_invalid_watermark"
    elif watermark > now:
        reason = "future_watermark"
    elif last_full is None:
        reason = "missing_or_invalid_full_reconciliation_watermark"
    elif last_full > now or watermark < last_full:
        reason = "inconsistent_reconciliation_watermark"
    elif now - last_full >= timedelta(hours=FULL_RECONCILIATION_INTERVAL_HOURS):
        reason = "periodic_full_reconciliation"
    else:
        return {
            "query_start": max(
                full_start,
                watermark - timedelta(hours=INCREMENTAL_OVERLAP_HOURS),
            ),
            "query_mode": INCREMENTAL_QUERY_MODE,
            "selection_reason": reason,
            "watermark_before": _utc_iso(watermark),
            "last_full_reconciliation_end": _utc_iso(last_full),
        }

    return {
        "query_start": full_start,
        "query_mode": FULL_QUERY_MODE,
        "selection_reason": reason,
        "watermark_before": _utc_iso(watermark) if watermark is not None else None,
        "last_full_reconciliation_end": _utc_iso(now),
    }


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
    ledger_retention = (
        dict(ledger.get("retention"))
        if isinstance(ledger, dict) and isinstance(ledger.get("retention"), dict)
        else {}
    )
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
    previous_full_reconciliation = _previous_full_reconciliation(previous_provenance)
    if collection and query_mode == FULL_QUERY_MODE:
        last_full_reconciliation = _provenance_datetime(last_query_end)
    elif collection:
        last_full_reconciliation = _provenance_datetime(
            collection.get("last_full_reconciliation_end")
        ) or previous_full_reconciliation
    else:
        last_full_reconciliation = previous_full_reconciliation
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
    retained_timestamp_coverage = _retained_timestamp_coverage(
        observations,
        retention_start,
        now,
    )
    queue_discovery_complete = bool(
        collection and (collection.get("queue_discovery") or {}).get("complete")
    )
    source_complete = bool(collection and (collection.get("source_coverage") or {}).get("complete"))
    api_complete = bool(
        collection and collection.get("complete") and queue_discovery_complete and source_complete
    )
    # Every accepted created-time query unit is shorter than one API page, but
    # jobs can still be dynamically added to a build after its unit is read.
    # Parents older than the bounded horizon also remain unknowable.
    complete = False
    coverage_reason = (
        "All organization-wide disjoint query units and target queue IDs were collected, but "
        "jobs added after a unit completed and jobs belonging to parent builds before the "
        "bounded source horizon cannot be proven absent. Direct observed event "
        "timestamps remain exact."
        if api_complete
        else "No complete current API collection covers the rolling window."
    )
    if ledger_retention.get("byte_limited") is True:
        coverage_reason += (
            " The durable ledger is byte-limited; aggregates cover only the exact "
            "published latest-event suffix attested by retention.ledger_scope."
        )
    coverage = {
        "complete": complete,
        "status": "partial_observation",
        "reason": coverage_reason,
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
                    "direct jobs[].finished_at from exhaustive offset-free organization-wide "
                    "bounded disjoint parent-created query units"
                ),
                "limitation": (
                    "A job dynamically added after its unit was read, or belonging to a parent "
                    "build created before the bounded "
                    "source horizon can also escape all parent-build filters."
                ),
            },
            "incoming": {
                "complete": False,
                "exact_for_observed_events": True,
                "basis": (
                    "direct jobs[].runnable_at from exhaustive offset-free organization-wide "
                    "bounded disjoint parent-created query units"
                ),
                "limitation": (
                    "A job dynamically added after its unit was read, or belonging to a parent "
                    "build created before the bounded "
                    "source horizon can also escape all parent-build filters."
                ),
            },
            "served": {
                "complete": False,
                "exact_for_observed_events": True,
                "basis": (
                    "direct jobs[].started_at from exhaustive offset-free organization-wide "
                    "bounded disjoint parent-created query units"
                ),
                "limitation": (
                    "A job dynamically added after its unit was read, or belonging to a parent "
                    "build created before the bounded "
                    "source horizon can also escape all parent-build filters."
                ),
            },
        },
        "job_observation_count": len(observations),
        "event_count": len(observed_times),
        "observed_start": _utc_iso(min(observed_times)) if observed_times else None,
        "observed_end": _utc_iso(max(observed_times)) if observed_times else None,
        "timestamp_fields": retained_timestamp_coverage,
        "ledger_retention": ledger_retention,
    }

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
        "daily_wait_times": _daily_wait_times(observations, retention_start, now),
        "coverage": coverage,
        "provenance": {
            "provider": "Buildkite REST organization builds API",
            "source_field_contract": {
                "incoming": "builds[].jobs[].runnable_at",
                "served": "builds[].jobs[].started_at",
                "completed": "builds[].jobs[].finished_at",
                "queue_wait_seconds": "started_at - runnable_at; null unless both direct timestamps exist",
                "daily_wait_times": (
                    "every non-null queue_wait_seconds observation grouped by the UTC date "
                    "of started_at"
                ),
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
            "last_full_reconciliation_end": (
                _utc_iso(last_full_reconciliation)
                if last_full_reconciliation is not None
                else None
            ),
            "ledger": ledger or {},
            "collection": collection,
        },
        "retention": {
            "days": RETENTION_DAYS,
            "event_start": _utc_iso(retention_start),
            "end_exclusive": _utc_iso(now),
            "byte_limited": bool(ledger_retention.get("byte_limited")),
            "actual_published_latest_event_start": ledger_retention.get(
                "published_latest_event_start"
            ),
            "actual_published_latest_event_end": ledger_retention.get(
                "published_latest_event_end"
            ),
            "ledger_scope": ledger_retention,
        },
    }


def _encode_summary(summary: dict) -> str:
    def encode() -> tuple[str, int]:
        value = json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n"
        return value, len(value.encode("utf-8"))

    text, size = encode()
    if size <= MAX_SUMMARY_BYTES:
        return text

    daily = summary.get("daily_wait_times")
    days = daily.get("days") if isinstance(daily, dict) else None
    if isinstance(days, list):
        compacted_dates: list[str] = []
        # Remove only complete per-day vectors, oldest first. The exact count
        # and distribution summary remain public and the durable ledger keeps
        # every underlying observation in its attested retained scope. This
        # turns pathological job volume
        # into explicitly reduced detail instead of a permanent publication
        # failure once the JSON blob reaches its hard ceiling.
        for row in days:
            if not isinstance(row, dict):
                continue
            waits = row.get("served_job_wait_seconds")
            if not isinstance(waits, list) or not waits:
                continue
            row["served_job_wait_seconds"] = []
            row["vector_complete"] = False
            row["published_sample_count"] = 0
            row["omitted_sample_count"] = len(waits)
            row["distribution"] = _duration_summary(waits)
            compacted_dates.append(str(row.get("date") or ""))
            daily["vector_coverage"] = {
                "complete": False,
                "observed_sample_count": sum(
                    day.get("sample_count", 0) for day in days if isinstance(day, dict)
                ),
                "published_sample_count": sum(
                    len(day.get("served_job_wait_seconds") or [])
                    for day in days
                    if isinstance(day, dict)
                ),
                "compacted_dates": compacted_dates,
                "method": "oldest_whole_day_vectors_replaced_by_exact_distribution_summary",
            }
            text, size = encode()
            if size <= MAX_SUMMARY_BYTES:
                return text

    raise RuntimeError(
        f"queue lifecycle summary is {size} bytes after bounded vector compaction; "
        f"limit is {MAX_SUMMARY_BYTES}"
    )


def write_summary(path: Path, summary: dict) -> None:
    _atomic_write_text(path, _encode_summary(summary))


def collect_lifecycle(
    token: str,
    *,
    jobs_path: Path = JOBS_OUTPUT,
    summary_path: Path = SUMMARY_OUTPUT,
    now: datetime | None = None,
    checkpoint_path: Path | None = None,
    baseline_ref: str | None = None,
    page_fetcher=None,
    queue_page_fetcher=None,
    deadline_monotonic: float | None = None,
) -> dict:
    if not token.strip():
        raise RuntimeError("BUILDKITE_API_TOKEN is required")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    existing = read_job_directory(jobs_path)
    previous = _safe_previous_provenance(summary_path, jobs_path=jobs_path)
    if checkpoint_path is not None:
        (
            checkpoint,
            incoming,
            queue_discovery,
            source_coverage,
            timestamp_coverage,
        ) = _collect_rest_lifecycle_resumable(
            token,
            checkpoint_path=checkpoint_path,
            existing=existing,
            previous=previous,
            now=current,
            baseline_ref=baseline_ref,
            page_fetcher=page_fetcher,
            queue_page_fetcher=queue_page_fetcher,
            deadline_monotonic=deadline_monotonic,
        )
        query = checkpoint["query"]
        # A resumed generation describes only its originally frozen horizon.
        # Wall-clock retry time must not advance retention or provenance.
        current = _require_datetime(
            query["query_end_exclusive"], "checkpoint query_end_exclusive"
        )
        query_start = _require_datetime(query["query_start"], "checkpoint query_start")
        active_parent_query_start = _require_datetime(
            query["active_parent_query_start"],
            "checkpoint active_parent_query_start",
        )
        query_plan = {
            "query_mode": query["query_mode"],
            "selection_reason": query["selection_reason"],
            "watermark_before": query["watermark_before"],
            "last_full_reconciliation_end": query["last_full_reconciliation_end"],
        }
        unique_job_count = len(incoming)
    else:
        query_plan = _collection_query_plan(previous, now=current)
        query_start = query_plan["query_start"]
        active_parent_query_start = current - timedelta(
            days=RETENTION_DAYS + PARENT_BUILD_LOOKBACK_DAYS
        )
        if queue_page_fetcher is None and deadline_monotonic is None:
            queue_by_id, queue_discovery = fetch_rest_target_queues(token)
        else:
            queue_by_id, queue_discovery = fetch_rest_target_queues(
                token,
                page_fetcher=_lifecycle_page_fetcher(
                    queue_page_fetcher,
                    deadline_monotonic=deadline_monotonic,
                ),
            )
        fetch_kwargs = {
            "query_start": query_start,
            "query_end": current,
            "active_created_from": active_parent_query_start,
            "queue_by_id": queue_by_id,
        }
        if page_fetcher is not None or deadline_monotonic is not None:
            fetch_kwargs["page_fetcher"] = _lifecycle_page_fetcher(
                page_fetcher,
                deadline_monotonic=deadline_monotonic,
            )
        jobs, source_coverage = fetch_rest_lifecycle_jobs(token, **fetch_kwargs)
        unique_job_count = len(jobs)
        incoming, timestamp_coverage = observations_from_jobs(
            jobs,
            retention_start=current - timedelta(days=RETENTION_DAYS),
            end_exclusive=current,
        )
        del jobs
    _require_lifecycle_time(deadline_monotonic)
    retention_start = current - timedelta(days=RETENTION_DAYS)
    query_mode = query_plan["query_mode"]
    log.info(
        "Lifecycle query mode=%s event_start=%s active_parent_start=%s "
        "watermark=%s reason=%s",
        query_mode,
        _utc_iso(query_start),
        _utc_iso(active_parent_query_start),
        query_plan["watermark_before"],
        query_plan["selection_reason"],
    )
    merged = merge_and_prune_jobs(
        existing,
        incoming,
        retention_start=retention_start,
        end_exclusive=current,
    )
    del existing, incoming
    retained, segment_payloads, ledger = _prepare_job_segments(
        merged,
        retention_start=retention_start,
        end_exclusive=current,
        prior_retention_scopes=[
            ((previous.get("ledger") or {}).get("retention") or {})
        ],
        # A complete full-window query is the only collection that can replace
        # a carried byte-limited omission claim with fresh source evidence.
        reset_prior_incompleteness=query_mode == FULL_QUERY_MODE,
    )
    collection = {
        "complete": True,
        "query_mode": query_mode,
        "query_start": _utc_iso(query_start),
        "active_parent_query_start": _utc_iso(active_parent_query_start),
        "query_end_exclusive": _utc_iso(current),
        "queue_discovery": queue_discovery,
        "source_coverage": source_coverage,
        "organization_wide": True,
        "parent_build_lookback_days": PARENT_BUILD_LOOKBACK_DAYS,
        "incremental_overlap_hours": INCREMENTAL_OVERLAP_HOURS,
        "full_reconciliation_interval_hours": FULL_RECONCILIATION_INTERVAL_HOURS,
        "selection_reason": query_plan["selection_reason"],
        "watermark_before": query_plan["watermark_before"],
        "last_full_reconciliation_end": query_plan[
            "last_full_reconciliation_end"
        ],
        "unique_jobs": unique_job_count,
        "timestamp_coverage": timestamp_coverage,
        "target_queues": list(AMD_METRIC_TARGET_QUEUES),
    }
    summary = build_summary(
        retained,
        now=current,
        collection=collection,
        previous_provenance=previous,
        ledger=ledger,
    )
    # All network, validation, aggregation, and serialization work has
    # succeeded before either public artifact is replaced.
    summary_text = _encode_summary(summary)
    _require_lifecycle_time(deadline_monotonic)
    _publish_generation(jobs_path, segment_payloads, summary_path, summary_text)
    return summary


def _read_git_blob_bounded(
    object_name: str,
    *,
    expected_size: int,
    max_bytes: int,
):
    """Spool one Git blob under a hard byte cap and return it with its digest."""
    spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    process = subprocess.Popen(
        ["git", "show", object_name],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    digest = hashlib.sha256()
    total = 0
    try:
        if process.stdout is None:  # pragma: no cover - guaranteed by PIPE
            raise RuntimeError(f"could not read lifecycle segment at {object_name}")
        while True:
            chunk = process.stdout.read(min(1024 * 1024, max_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                process.kill()
                process.wait()
                raise RuntimeError(f"invalid lifecycle segment size at {object_name}")
            spool.write(chunk)
            digest.update(chunk)
        returncode = process.wait()
        if returncode != 0 or total != expected_size:
            raise RuntimeError(f"could not read complete lifecycle segment at {object_name}")
        spool.seek(0)
        return spool, digest.hexdigest()
    except Exception:
        if process.poll() is None:
            process.kill()
            process.wait()
        spool.close()
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()


def _git_ref_jobs(
    git_ref: str,
    *,
    required: bool = False,
    expected_ledger: dict | None = None,
) -> list[dict]:
    try:
        max_segment_bytes, max_total_bytes, _ = _remote_ledger_read_contract(
            expected_ledger
        )
    except RuntimeError as exc:
        raise RuntimeError(f"lifecycle ledger manifest at {git_ref} is invalid: {exc}") from exc
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
        if (
            expected_ledger is not None
            and expected_ledger.get("segment_count") == 0
            and expected_ledger.get("job_observations") == 0
        ):
            log.info("Lifecycle ledger at %s is a manifest-bound empty generation", git_ref)
            return []
        if required:
            raise RuntimeError(f"established lifecycle ledger is missing at {git_ref}")
        log.info("No lifecycle job ledger at %s; merge is a first-bootstrap no-op", git_ref)
        return []
    prefix = JOBS_REPO_PATH + "/"
    if any(
        not path.startswith(prefix)
        or "/" in path[len(prefix) :]
        for path in paths
    ):
        raise RuntimeError(f"lifecycle segment directory at {git_ref} contains an invalid path")
    names = [path[len(prefix) :] for path in paths]
    if not _segment_names_valid(names):
        raise RuntimeError(f"lifecycle segment directory at {git_ref} contains an invalid path")
    expected_segments = (expected_ledger or {}).get("segments") or {}
    if expected_ledger is not None and set(expected_segments) != set(names):
        raise RuntimeError(f"lifecycle segment manifest mismatch at {git_ref}")

    # Resolve and bind every object size before decoding the first byte. This
    # prevents a multi-segment legacy generation from consuming resources
    # before its aggregate is known to fit the one-hop migration envelope.
    segment_sizes: dict[str, int] = {}
    total_size = 0
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
        if size < 0 or size > max_segment_bytes:
            raise RuntimeError(f"invalid lifecycle segment size at {object_name}")
        expected = expected_segments.get(name) or {}
        if expected and expected.get("compressed_bytes") != size:
            raise RuntimeError(f"lifecycle segment generation mismatch at {object_name}")
        total_size += size
        if total_size > max_total_bytes:
            raise RuntimeError(f"lifecycle segments at {git_ref} exceed the total safety limit")
        segment_sizes[name] = size
    if expected_ledger and expected_ledger.get("total_compressed_bytes") != total_size:
        raise RuntimeError(f"lifecycle segment volume manifest mismatch at {git_ref}")

    rows: list[dict] = []
    seen: set[str] = set()
    actual_segments: dict[str, dict] = {}
    total_uncompressed = 0
    for path in sorted(paths):
        name = path[len(prefix) :]
        object_name = f"{git_ref}:{path}"
        size = segment_sizes[name]
        compressed_stream, digest = _read_git_blob_bounded(
            object_name,
            expected_size=size,
            max_bytes=max_segment_bytes,
        )
        try:
            expected = expected_segments.get(name) or {}
            if expected and expected.get("sha256") != digest:
                raise RuntimeError(f"lifecycle segment generation mismatch at {object_name}")
            segment_rows, uncompressed_size = _decode_job_ledger_stream_with_size(
                compressed_stream,
                compressed_size=size,
                source=object_name,
                max_uncompressed=MAX_UNCOMPRESSED_LEDGER_BYTES - total_uncompressed,
                max_compressed=max_segment_bytes,
            )
        finally:
            compressed_stream.close()
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
    prior_retention_scopes = [
        ((provenance.get("ledger") or {}).get("retention") or {})
        for provenance in (remote_provenance, local_provenance)
        if isinstance(provenance, dict)
    ]
    retained, segment_payloads, ledger = _prepare_job_segments(
        merged,
        retention_start=retention_start,
        end_exclusive=current,
        prior_retention_scopes=prior_retention_scopes,
    )
    summary = build_summary(
        retained,
        now=current,
        collection=None,
        previous_provenance=previous,
        ledger=ledger,
    )
    summary_text = _encode_summary(summary)
    _publish_generation(jobs_path, segment_payloads, summary_path, summary_text)
    return summary


def restore_exact_job_ledger(
    *,
    jobs_path: Path,
    summary_path: Path,
    git_ref: str,
) -> dict:
    """Materialize one remote canonical generation at its own frozen horizon."""
    remote_provenance = _git_ref_summary_provenance(git_ref)
    remote_ledger = remote_provenance.get("ledger")
    if not _ledger_manifest_complete(remote_ledger):
        raise RuntimeError(
            f"established lifecycle ref {git_ref} lacks a complete summary-bound ledger manifest"
        )
    try:
        _, _, legacy_migration = _remote_ledger_read_contract(remote_ledger)
    except RuntimeError as exc:
        raise RuntimeError(
            f"established lifecycle ref {git_ref} uses an unsupported ledger contract: {exc}"
        ) from exc
    migration_requires_rewrite = bool(
        legacy_migration
        and (
            remote_ledger["total_compressed_bytes"] > MAX_COMPRESSED_LEDGER_BYTES
            or any(
                segment["compressed_bytes"] > MAX_COMPRESSED_SEGMENT_BYTES
                for segment in remote_ledger["segments"].values()
            )
        )
    )
    current = _provenance_datetime(remote_provenance.get("last_successful_query_end"))
    if current is None:
        raise RuntimeError(f"established lifecycle ref {git_ref} has no safe query horizon")
    rows = _git_ref_jobs(
        git_ref,
        required=True,
        expected_ledger=remote_ledger,
    )
    retained = merge_and_prune_jobs(
        [],
        rows,
        retention_start=current - timedelta(days=RETENTION_DAYS),
        end_exclusive=current,
    )
    retained, segment_payloads, ledger = _prepare_job_segments(
        retained,
        retention_start=current - timedelta(days=RETENTION_DAYS),
        end_exclusive=current,
        prior_retention_scopes=[remote_ledger.get("retention") or {}],
    )
    if (
        ledger["generation_sha256"] != remote_ledger["generation_sha256"]
        and not migration_requires_rewrite
    ):
        raise RuntimeError(f"lifecycle generation at {git_ref} changes at its bound horizon")
    _, _, output_is_legacy = _remote_ledger_read_contract(ledger)
    if output_is_legacy:
        raise RuntimeError("lifecycle migration did not produce the current storage contract")
    summary = build_summary(
        retained,
        now=current,
        collection=(
            remote_provenance.get("collection")
            if isinstance(remote_provenance.get("collection"), dict)
            else None
        ),
        previous_provenance=remote_provenance,
        ledger=ledger,
    )
    _publish_generation(
        jobs_path,
        segment_payloads,
        summary_path,
        _encode_summary(summary),
    )
    return summary


def _validate_resumable_progress(
    *,
    checkpoint_path: Path | None,
    jobs_path: Path,
    summary_path: Path,
    baseline_ref: str | None,
    require_incomplete: bool,
    reason: str,
    now: datetime | None = None,
) -> dict:
    """Prove a bounded-progress exit left a context-bound usable checkpoint."""
    if checkpoint_path is None or baseline_ref is None:
        raise RuntimeError(f"{reason} has no bound lifecycle checkpoint context")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0
    )
    existing = read_job_directory(jobs_path)
    previous = _safe_previous_provenance(summary_path, jobs_path=jobs_path)
    state = _validate_checkpoint_context(
        _decode_checkpoint_file(checkpoint_path),
        baseline_sha256=_baseline_sha256(existing),
        baseline_ref=baseline_ref,
        previous=previous,
        now=current,
    )
    if state["terminal_error"] is not None:
        raise RuntimeError(f"{reason} left a terminal lifecycle checkpoint")
    if require_incomplete and all(unit["complete"] for unit in state["units"]):
        raise RuntimeError(f"{reason} did not leave resumable lifecycle work")
    return state


def validate_resumable_allowance_exhaustion(
    *,
    checkpoint_path: Path | None,
    jobs_path: Path,
    summary_path: Path,
    baseline_ref: str | None,
    now: datetime | None = None,
) -> dict:
    """Prove allowance exhaustion left one usable, incomplete checkpoint."""
    return _validate_resumable_progress(
        checkpoint_path=checkpoint_path,
        jobs_path=jobs_path,
        summary_path=summary_path,
        baseline_ref=baseline_ref,
        require_incomplete=True,
        reason="Buildkite allowance exhaustion",
        now=now,
    )


def validate_resumable_wall_clock_yield(
    *,
    checkpoint_path: Path | None,
    jobs_path: Path,
    summary_path: Path,
    baseline_ref: str | None,
    now: datetime | None = None,
) -> dict:
    """Prove a wall-clock yield left a usable incomplete or complete WIP."""
    return _validate_resumable_progress(
        checkpoint_path=checkpoint_path,
        jobs_path=jobs_path,
        summary_path=summary_path,
        baseline_ref=baseline_ref,
        require_incomplete=False,
        reason="Lifecycle wall-clock yield",
        now=now,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-output", type=Path, default=JOBS_OUTPUT)
    parser.add_argument("--output", type=Path, default=SUMMARY_OUTPUT)
    parser.add_argument(
        "--checkpoint",
        type=Path,
        help="Private gzip WIP checkpoint used only for bounded resumable collection",
    )
    parser.add_argument(
        "--baseline-ref",
        help="Exact 40-SHA canonical lifecycle ref, or bootstrap on the first generation",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--merge-jobs-git-ref",
        metavar="REF",
        help="Tokenlessly merge a retained compressed job ledger from REF, prune, and rebuild output",
    )
    modes.add_argument(
        "--restore-jobs-git-ref",
        metavar="REF",
        help="Tokenlessly restore REF at its exact published query horizon",
    )
    modes.add_argument(
        "--prune-jobs-only",
        action="store_true",
        help="Tokenlessly prune the local ledger and rebuild the derived output",
    )
    modes.add_argument(
        "--validate-ledger-only",
        action="store_true",
        help="Tokenlessly validate that the local summary exactly binds every ledger segment",
    )
    modes.add_argument(
        "--clear-checkpoint",
        action="store_true",
        help="Delete WIP after durable publication and leave a private cache tombstone",
    )
    modes.add_argument(
        "--prepare-checkpoint-cache",
        action="store_true",
        help="Structurally validate private WIP or replace it with a cache tombstone",
    )
    args = parser.parse_args()

    if (args.clear_checkpoint or args.prepare_checkpoint_cache) and args.checkpoint is None:
        parser.error("--checkpoint is required for checkpoint maintenance")
    if args.baseline_ref and args.checkpoint is None:
        parser.error("--baseline-ref requires --checkpoint")

    if args.clear_checkpoint:
        clear_lifecycle_checkpoint(args.checkpoint)
        return 0
    if args.prepare_checkpoint_cache:
        prepare_lifecycle_checkpoint_cache(args.checkpoint)
        return 0

    if args.validate_ledger_only:
        segment_count, job_count = validate_local_ledger_generation(
            jobs_path=args.jobs_output,
            summary_path=args.output,
        )
        log.info(
            "Validated lifecycle generation: %d segments, %d job observations",
            segment_count,
            job_count,
        )
        return 0
    if args.restore_jobs_git_ref:
        summary = restore_exact_job_ledger(
            jobs_path=args.jobs_output,
            summary_path=args.output,
            git_ref=args.restore_jobs_git_ref,
        )
    elif args.merge_jobs_git_ref or args.prune_jobs_only:
        summary = maintain_job_ledger(
            jobs_path=args.jobs_output,
            summary_path=args.output,
            git_ref=args.merge_jobs_git_ref,
        )
    else:
        deadline_monotonic = time_module.monotonic() + LIFECYCLE_WALL_CLOCK_SECONDS
        try:
            summary = collect_lifecycle(
                os.environ.get("BUILDKITE_API_TOKEN", ""),
                jobs_path=args.jobs_output,
                summary_path=args.output,
                checkpoint_path=args.checkpoint,
                baseline_ref=args.baseline_ref,
                deadline_monotonic=deadline_monotonic,
            )
        except BuildkiteRequestAllowanceExhausted as exc:
            validate_resumable_allowance_exhaustion(
                checkpoint_path=args.checkpoint,
                jobs_path=args.jobs_output,
                summary_path=args.output,
                baseline_ref=args.baseline_ref,
            )
            log.warning("Lifecycle request allowance ended with resumable WIP: %s", exc)
            return CHECKPOINT_GUARD_EXIT
        except LifecycleWallClockYield as exc:
            validate_resumable_wall_clock_yield(
                checkpoint_path=args.checkpoint,
                jobs_path=args.jobs_output,
                summary_path=args.output,
                baseline_ref=args.baseline_ref,
            )
            log.warning("Lifecycle wall-clock ended with validated WIP: %s", exc)
            return CHECKPOINT_WALL_CLOCK_EXIT
    log.info(
        "Wrote %d compact job observations; rolling %dh incoming=%d served=%d completed=%d",
        summary["coverage"]["job_observation_count"],
        ROLLING_WINDOW_HOURS,
        summary["totals"]["incoming"],
        summary["totals"]["served"],
        summary["totals"]["completed"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
