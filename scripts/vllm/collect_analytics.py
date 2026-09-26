#!/usr/bin/env python3
"""Collect per-build, per-job analytics from Buildkite for the rich CI dashboard.

Produces:
- data/vllm/ci/builds_analytics.json — per-build summary with job matrix
- data/vllm/ci/jobs_analytics.json — per-job failure/duration rankings

Guarded workflow CLI form (a token without durable guard state exits 78):
    python scripts/vllm/collect_analytics.py --days 30
"""

import argparse
import json
import logging
import os
import re
import sys
import tempfile
import time
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.buildkite_request_guard import (  # noqa: E402
    BuildkiteRequestGuardError,
    install_from_environment_or_exit,
)

install_from_environment_or_exit()

from vllm.constants import BK_API_BASE, BK_ORG  # noqa: E402
from vllm.dashboard_storage_budget import writer_max_bytes  # noqa: E402
from vllm.ci.analytics_cache import (  # noqa: E402
    CACHE_DIR_NAME,
    CACHE_SCHEMA_VERSION,
    builds_needing_refresh,
    load_build_cache,
    merge_builds,
    sanitize_builds,
    write_build_cache,
)
from vllm.ci.analyzer import _parse_job_execution_label  # noqa: E402
from vllm.ci.incident_transitions import INCIDENT_TRANSITION_POLICY_ID  # noqa: E402
from vllm.ci.models import PASS_RATE_CONTRACT_VERSION  # noqa: E402
from vllm.ci.utils import (  # noqa: E402
    duration_mins,
    parse_iso as parse_ts,
    percentile,
    queue_from_rules as _queue_from_rules,
)
from vllm.ci.reliability_history import (  # noqa: E402
    BUILD_MESSAGE_MAX_CHARS,
    BUILD_FETCH_MAX_PAGES,
    BUILD_FETCH_PAGE_SIZE,
    LEGACY_OBSERVATION_DERIVED_FIELDS,
    OBSERVATION_LIMIT,
    SCHEMA_VERSION as RELIABILITY_SCHEMA_VERSION,
    buildkite_job_url_matches,
    build_all_main_reliability,
    compute_nightly_change_history,
    filter_reliability_builds,
    hydrate_reliability_observations,
    validate_all_main_reliability,
)
from vllm.pipelines import NIGHTLY_NAME_PATTERNS_BY_SLUG  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

PIPELINES = {"amd-ci": "AMD CI", "ci": "Upstream CI"}
ANALYTICS_WINDOWS_DAYS = (1, 3, 7, 14, 30)
BUILD_PASS_RATE_BASIS = "terminal_build_state_all_green"
TERMINAL_BUILD_STATES = frozenset({
    "passed",
    "failed",
    "canceled",
    "skipped",
    "not_run",
})
DEFAULT_ANALYTICS_WINDOW_DAYS = 30
ANALYTICS_BUILD_LIMIT = 120
ANALYTICS_NIGHTLY_LIMIT = 30
ANALYTICS_WINDOW_BUILD_LIMIT = 50
ANALYTICS_WINDOW_NIGHTLY_LIMIT = 30
GATING_NIGHTLY_LIMIT = 30
GATING_NIGHTLIES_MAX_BYTES = writer_max_bytes("gating_nightlies")
# GitHub rejects individual blobs at 100 MiB. Keep the private collector
# artifact below that boundary with enough headroom for byte/display-unit
# differences and fail before replacing the validated baseline.
GITHUB_BLOB_MAX_BYTES = 100 * 1024 * 1024
# Normal collection must fit comfortably below GitHub's blob limit.  The
# larger compatibility ceiling remains visible as an emergency invariant, but
# candidate monoliths are compacted to (or rejected above) this operating
# target before they can replace the baseline.
PRIVATE_ANALYTICS_TARGET_BYTES = writer_max_bytes("analytics")
PRIVATE_ANALYTICS_MAX_BYTES = 85 * 1024 * 1024
# Evidence is newest-first. These deterministic levels preserve recent popup
# history while giving the writer progressively stronger ways to stay inside
# the normal budget if upstream cardinality grows unexpectedly.
PRIVATE_ANALYTICS_OBSERVATION_CAPS = (48, 32, 24, 16, 12, 8, 4, 2, 1)
# Regular build titles retain their established behavior. Only pathological
# catalog values are bounded, far above the 100 characters rendered by the
# existing nightly-build popup path.
CATALOG_MESSAGE_MAX_CHARS = BUILD_MESSAGE_MAX_CHARS
# The AMD all-main ledger exists for the hourly live alert, not long-range
# browser analytics. Bounding it keeps analytics.json from growing needlessly.
AMD_MAIN_OBSERVATION_LIMIT = 24
BK_GET_MAX_ATTEMPTS = 5
BK_GET_BACKOFF_SECONDS = 2
BK_GET_MAX_BACKOFF_SECONDS = 60
BK_GET_CONNECT_TIMEOUT_SECONDS = 10
BK_GET_INITIAL_READ_TIMEOUT_SECONDS = 30
BK_GET_READ_TIMEOUT_STEP_SECONDS = 15
BK_GET_MAX_READ_TIMEOUT_SECONDS = 60
BK_GET_RETRY_STATUS_CODES = frozenset({500, 502, 503, 504, 520, 522, 524})
ANALYTICS_CACHE_OVERLAP = timedelta(hours=24)
ANALYTICS_CACHE_FULL_REFRESH_INTERVAL = timedelta(hours=24)
# A large one-run increase can indicate that an incremental cache merge
# materialized duplicate history. Reconcile it once from the exhaustive source
# before committing the cache. The absolute floor avoids retrying ordinary
# growth in busy pipelines.
ANALYTICS_CACHE_SUSPICIOUS_GROWTH_RATIO = 1.20
ANALYTICS_CACHE_SUSPICIOUS_GROWTH_MIN_BYTES = 8 * 1024 * 1024

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT = ROOT / "data" / "vllm" / "ci"

RESULT_SUFFIX = {"amd-ci": "amd", "ci": "upstream"}
# Current vLLM nightly slots in UTC. Actual Buildkite ``created_at`` values win
# whenever they are available; these hours are only for JSONL-only fallbacks.
FALLBACK_CREATED_HOUR_UTC = {"amd-ci": 9, "ci": 6}
CACHE_NIGHTLY_MESSAGE = {
    "amd-ci": "AMD Full CI Run - nightly",
    "ci": "Full CI run - nightly",
}
CACHE_SCHEDULED_GATING_MESSAGE = {
    "ci": {
        "nightly": CACHE_NIGHTLY_MESSAGE["ci"],
        "daily": "Full CI run - daily",
    },
}
RETRY_FIELDS = (
    "retried",
    "retried_in_job_id",
    "retries_count",
    "retry_source",
    "retry_type",
    "step_key",
)
FAILED_JOB_STATES = {"failed", "soft_fail", "soft_failed", "timed_out", "broken", "canceled", "expired"}

# ``main`` deliberately keeps the historical three-argument
# ``fetch_pipeline_builds`` call so downstream tests and collectors can still
# monkeypatch that seam. The real implementation reads this task-local context
# to opt into output-relative caching with the one frozen collection clock.
_FETCH_CONTEXT: ContextVar[tuple[Path, datetime] | None] = ContextVar(
    "analytics_fetch_context",
    default=None,
)


class IncompleteAnalyticsCollection(RuntimeError):
    """Raised when neither incremental nor full pagination is exhaustive."""

    def __init__(self, message: str, provenance: dict | None = None):
        super().__init__(message)
        self.provenance = provenance or {}


def _compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _compact_json_bytes(value: Any) -> int:
    return len(_compact_json(value).encode("utf-8"))


def _bounded_catalog_message(value: Any) -> str:
    """Mirror the reliability catalog's pathological-message bound."""
    message = str(value or "")
    if len(message) <= CATALOG_MESSAGE_MAX_CHARS:
        return message
    return message[:CATALOG_MESSAGE_MAX_CHARS - 1] + "…"


def buildkite_job_url(pipeline_slug: str, build_number: int, job_id: str = "", step_id: str = "") -> str:
    """Return the most specific Buildkite URL we can construct for a job."""
    if not build_number:
        return ""
    base = f"https://buildkite.com/{BK_ORG}/{pipeline_slug}/builds/{build_number}"
    if job_id:
        return f"{base}/steps/canvas?jid={job_id}&tab=output"
    if step_id:
        return f"{base}/steps/canvas?sid={step_id}&tab=output"
    return base


def _iso_from_nightly_date(date_str: str, pipeline_slug: str) -> str:
    """Best-effort timestamp for JSONL-only builds.

    The analytics UI needs a ``created_at`` value for window filtering. When a
    Buildkite list response is partial, the parsed test-result JSONL still has
    the nightly date and build number, so synthesize the current schedule hour.
    """
    if not date_str:
        return ""
    hour = FALLBACK_CREATED_HOUR_UTC.get(pipeline_slug, 12)
    return f"{date_str}T{hour:02d}:00:00Z"


def _result_count(row: dict) -> int:
    """Extract collapsed pytest count from rows like ``__passed__ (136)``."""
    name = str(row.get("name") or "")
    m = re.search(r"\((\d+)\)\s*$", name)
    return int(m.group(1)) if m else 1


def _result_status_to_job_state(statuses: list[str]) -> str:
    """Collapse one job's parsed test rows into a single analytics state."""
    lowered = {str(s or "").lower() for s in statuses}
    if lowered & {"soft_fail", "soft_failed"}:
        return "soft_fail"
    if lowered & {"failed", "error", "timed_out", "broken", "canceled"}:
        return "failed"
    if lowered & {"passed", "xpassed"}:
        return "passed"
    if lowered & {"skipped", "xfailed"}:
        return "skipped"
    return "unknown"


def nightly_date(iso_str):
    """Convert a UTC timestamp to the 'nightly date'.

    Boundary at 12:00 UTC so both pipelines align in the same column. Current
    scheduled runs are before noon UTC (upstream at ~06:00, AMD at ~09:00), so
    they keep the same calendar day. Older upstream runs after noon still map
    to the following nightly date.
    """
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.hour >= 12:
            dt += timedelta(days=1)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return iso_str[:10] if iso_str else ""


def _rate_limit_wait_seconds(headers, attempt):
    """Return a retry delay that clears Buildkite rate-limit windows."""
    reset_waits = []
    for name in ("RateLimit-Reset", "RateLimit-User-Reset"):
        try:
            reset_waits.append(max(0, int(float(headers.get(name, "")))) + 1)
        except (TypeError, ValueError):
            continue
    if reset_waits:
        return max(reset_waits)
    try:
        return max(0, int(float(headers.get("Retry-After", ""))))
    except (TypeError, ValueError):
        return 5 * (attempt + 1)


def _request_retry_wait_seconds(attempt):
    """Return a capped exponential delay for a zero-based request attempt."""
    return min(BK_GET_BACKOFF_SECONDS * (2**attempt), BK_GET_MAX_BACKOFF_SECONDS)


def _request_timeout(attempt):
    """Bound connect time while allowing a slow response more time on retry."""
    read_timeout = min(
        BK_GET_INITIAL_READ_TIMEOUT_SECONDS + BK_GET_READ_TIMEOUT_STEP_SECONDS * attempt,
        BK_GET_MAX_READ_TIMEOUT_SECONDS,
    )
    return BK_GET_CONNECT_TIMEOUT_SECONDS, read_timeout


def bk_get(path, token, params=None):
    """Fetch one Buildkite REST page with bounded transient-error retries."""
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{BK_API_BASE}{path}"
    p = dict(params or {})
    for attempt in range(BK_GET_MAX_ATTEMPTS):
        try:
            resp = requests.get(
                url,
                headers=headers,
                params=p,
                timeout=_request_timeout(attempt),
            )
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ) as exc:
            if attempt == BK_GET_MAX_ATTEMPTS - 1:
                raise
            wait = _request_retry_wait_seconds(attempt)
            log.warning(
                "Buildkite request %s page %s failed (%s), retry %d/%d in %ds",
                path,
                p.get("page", 1),
                type(exc).__name__,
                attempt + 1,
                BK_GET_MAX_ATTEMPTS,
                wait,
            )
            time.sleep(wait)
            continue

        if resp.status_code == 429:
            if attempt == BK_GET_MAX_ATTEMPTS - 1:
                resp.raise_for_status()
            wait = _rate_limit_wait_seconds(resp.headers, attempt)
            log.warning(
                "Buildkite request %s page %s rate limited, retry %d/%d in %ds",
                path,
                p.get("page", 1),
                attempt + 1,
                BK_GET_MAX_ATTEMPTS,
                wait,
            )
            time.sleep(wait)
            continue
        if resp.status_code in BK_GET_RETRY_STATUS_CODES:
            if attempt == BK_GET_MAX_ATTEMPTS - 1:
                resp.raise_for_status()
            wait = _request_retry_wait_seconds(attempt)
            log.warning(
                "Buildkite request %s page %s returned HTTP %d, retry %d/%d in %ds",
                path,
                p.get("page", 1),
                resp.status_code,
                attempt + 1,
                BK_GET_MAX_ATTEMPTS,
                wait,
            )
            time.sleep(wait)
            continue

        resp.raise_for_status()
        return resp.json()
    return []


def queue_from_rules(rules):
    """Analytics wants ``"unknown"`` when no queue rule is present (keeps
    the job-stats queue column non-null)."""
    return _queue_from_rules(rules) or "unknown"


def normalize_job(name):
    """Strip execution queue and platform decorator for build comparison."""
    logical_label, _, _ = _parse_job_execution_label(name)
    return logical_label.strip()


def queue_from_result_job_name(name):
    """Derive a hardware queue from a parsed result when metadata is absent."""
    # A concrete AMD queue includes the device width and is more specific than
    # the standardized decorator retained inside a nested result label.
    match = re.match(r"^(mi\d+_\d+):\s*", name or "", flags=re.IGNORECASE)
    if match:
        return "amd_" + match.group(1).lower()
    match = re.match(r"^(amd[-_\w]+):\s*", name or "", flags=re.IGNORECASE)
    if match:
        return match.group(1).lower()

    _, platform, hardware = _parse_job_execution_label(name)
    hardware_slug = hardware.replace(" ", "_")
    if platform == "amd" and hardware:
        return "amd_" + hardware_slug
    if platform == "nvidia" and hardware:
        return "nvidia_" + hardware_slug
    return None


def job_metadata_keys(job):
    """Return identity keys from most-specific to most-general.

    ``name`` is normalized for cross-build rankings, but parsed JSONL can have
    the same normalized title on several hardware pools in one build. Keeping
    ``raw_name`` first prevents an MI300 failure from being attached to the
    MI355 row in the AMD hardware matrix.
    """
    raw = (job.get("raw_name") or job.get("job_name") or job.get("full_name") or "").strip()
    name = (job.get("name") or "").strip()
    keys = []
    for key in (raw, name, normalize_job(raw), normalize_job(name)):
        if key and key not in keys:
            keys.append(key)
    return keys


def _build_job_metadata(builds: list[dict]) -> dict[int, dict[str, dict[str, dict]]]:
    """Index existing per-job metadata by build number, exact ID, and name.

    Buildkite retains superseded attempts when ``include_retried_jobs`` is
    enabled.  Those attempts commonly share a name, so the ID index is the
    authoritative join for parsed JSONL rows that carry a ``job_id``.  The
    name index remains a fallback for historical rows without job identity.
    """
    meta: dict[int, dict[str, dict[str, dict]]] = {}
    for build in builds:
        index = meta.setdefault(
            int(build.get("number") or 0),
            {"by_job_id": {}, "by_name": {}},
        )
        for job in build.get("jobs") or []:
            payload = {
                k: job[k]
                for k in (
                    "wait", "q", "state", "soft_failed", "job_id", "step_id", "url",
                    "started_at", "finished_at", "runnable_at", "wall_completion_mins",
                    "queue_wait_mins", "end_to_end_mins", "duration_source",
                )
                if k in job and job[k] is not None
            }
            # Historical parsed-result payloads used ``dur`` for summed pytest
            # time. Do not silently recycle that value as Buildkite wall time.
            duration_is_wall = (
                job.get("duration_source") == "buildkite_wall"
                or build.get("source") != "test_results"
            )
            if duration_is_wall and isinstance(job.get("dur"), (int, float)):
                payload["dur"] = job["dur"]
                payload.setdefault("wall_completion_mins", job["dur"])
                payload.setdefault("duration_source", "buildkite_wall")
            payload.update({k: job[k] for k in RETRY_FIELDS if k in job})
            job_id = str(job.get("job_id") or "")
            if job_id:
                index["by_job_id"][job_id] = payload
            for key in job_metadata_keys(job):
                index["by_name"][key] = payload
    return meta


def _merge_job_metadata(
    base: dict[int, dict[str, dict[str, dict]]],
    fresh: dict[int, dict[str, dict[str, dict]]],
) -> dict[int, dict[str, dict[str, dict]]]:
    """Merge metadata indexes, letting a fresh Buildkite read win by key."""
    for build_number, incoming in fresh.items():
        current = base.setdefault(build_number, {"by_job_id": {}, "by_name": {}})
        current["by_job_id"].update(incoming.get("by_job_id") or {})
        current["by_name"].update(incoming.get("by_name") or {})
    return base


def _job_metadata_for_result(
    index: dict[str, dict[str, dict]],
    result_job: dict,
) -> dict:
    """Resolve metadata for one parsed-result job without crossing attempts."""
    job_id = str(result_job.get("job_id") or "")
    if job_id:
        # An exact identity is authoritative. Falling back to a shared name
        # here can attach a manual retry's timestamps to the original attempt.
        return (index.get("by_job_id") or {}).get(job_id) or {}

    by_name = index.get("by_name") or {}
    for key in job_metadata_keys(result_job):
        if key in by_name:
            return by_name[key]
    return {}


def _build_metadata(builds: list[dict]) -> dict[int, dict]:
    """Build-level metadata we can carry over when using parsed JSONL state."""
    return {int(b.get("number") or 0): b for b in builds if b.get("number") is not None}


def load_test_result_builds(
    output: Path,
    pipeline_slug: str,
    days: int,
    buildkite_builds: list[dict] | None = None,
    previous_builds: list[dict] | None = None,
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Build analytics rows from parsed CI test-result JSONL files.

    ``collect_ci.py`` runs immediately before this script in the scheduled
    workflow. Those JSONL files are the same parsed test source used by CI
    Health, so they are a better source for AMD failure/pass-rate analytics than
    Buildkite's soft-failed job state. Buildkite data, when present, is still
    used for wall-clock, queue, wait, and exact URLs.
    """
    suffix = RESULT_SUFFIX.get(pipeline_slug)
    if not suffix:
        return []

    results_dir = output / "test_results"
    if not results_dir.exists():
        return []

    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).strftime(
        "%Y-%m-%d"
    )
    paths = sorted(results_dir.glob(f"*_{suffix}.jsonl"))
    paths = [p for p in paths if p.name.rsplit("_", 1)[0] >= cutoff]
    if not paths:
        return []

    bk_meta = _build_metadata(buildkite_builds or [])
    prev_meta = _build_metadata(previous_builds or [])
    job_meta = _build_job_metadata(previous_builds or [])
    _merge_job_metadata(job_meta, _build_job_metadata(buildkite_builds or []))

    grouped: dict[int, dict] = {}
    for path in paths:
        fallback_date = path.name.rsplit("_", 1)[0]
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                log.warning("Skipping malformed analytics test-result row in %s", path)
                continue
            if row.get("pipeline") and row.get("pipeline") != pipeline_slug:
                continue
            build_number = int(row.get("build_number") or 0)
            if not build_number:
                continue
            raw_job_name = str(row.get("job_name") or row.get("classname") or "unknown").strip()
            job_name = normalize_job(raw_job_name)
            if not raw_job_name or not job_name:
                continue
            bucket = grouped.setdefault(build_number, {
                "date": row.get("date") or fallback_date,
                "jobs": {},
            })
            job = bucket["jobs"].setdefault(raw_job_name, {
                "name": job_name,
                "raw_name": raw_job_name,
                "job_id": str(row.get("job_id") or ""),
                "step_id": str(row.get("step_id") or ""),
                "statuses": [],
                "dur": 0.0,
                "tests": 0,
                "passed_tests": 0,
                "failed_tests": 0,
                "skipped_tests": 0,
            })
            if not job.get("job_id") and row.get("job_id"):
                job["job_id"] = str(row.get("job_id") or "")
            if not job.get("step_id") and row.get("step_id"):
                job["step_id"] = str(row.get("step_id") or "")
            status = str(row.get("status") or "unknown").lower()
            count = _result_count(row)
            job["statuses"].append(status)
            job["dur"] += float(row.get("duration_secs") or 0.0) / 60.0
            job["tests"] += count
            if status in ("passed", "xpassed"):
                job["passed_tests"] += count
            elif status in ("failed", "error", "timed_out", "broken", "canceled"):
                job["failed_tests"] += count
            elif status in ("skipped", "xfailed"):
                job["skipped_tests"] += count

    builds = []
    for build_number, bucket in grouped.items():
        meta = bk_meta.get(build_number) or prev_meta.get(build_number) or {}
        jobs = []
        passed = failed = soft = skipped = 0
        for raw_name, raw_job in sorted(bucket["jobs"].items()):
            metadata = _job_metadata_for_result(
                job_meta.get(build_number, {}),
                raw_job,
            )
            state = _result_status_to_job_state(raw_job["statuses"])
            if metadata.get("state") == "soft_fail" or metadata.get("soft_failed"):
                state = "soft_fail"
            elif state == "unknown" and metadata.get("state"):
                state = metadata["state"]

            if state == "passed":
                passed += 1
            elif state == "failed":
                failed += 1
            elif state == "soft_fail":
                soft += 1
            elif state == "skipped":
                skipped += 1

            entry = {
                "name": raw_job["name"],
                "raw_name": raw_job["raw_name"],
                "state": state,
                "test_duration_mins": round(raw_job["dur"], 1),
                "tests": raw_job["tests"],
                "passed_tests": raw_job["passed_tests"],
                "failed_tests": raw_job["failed_tests"],
                "skipped_tests": raw_job["skipped_tests"],
            }
            job_id = str(raw_job.get("job_id") or metadata.get("job_id") or "")
            step_id = str(raw_job.get("step_id") or metadata.get("step_id") or "")
            job_url = buildkite_job_url(
                pipeline_slug,
                build_number,
                job_id,
                step_id,
            ) or str(metadata.get("url") or "")
            if job_url:
                entry["url"] = job_url
            if job_id:
                entry["job_id"] = job_id
            if step_id:
                entry["step_id"] = step_id
            queue = queue_from_result_job_name(raw_job["raw_name"])
            if queue:
                entry["q"] = queue
            for k, v in metadata.items():
                if k == "q" and entry.get("q"):
                    continue
                if k in ("state", "soft_failed", "job_id", "step_id", "url"):
                    continue
                entry[k] = v
            jobs.append(entry)

        created = meta.get("created_at") or _iso_from_nightly_date(bucket["date"], pipeline_slug)
        build_state = meta.get("state") or ("failed" if failed else "passed")
        builds.append({
            "number": build_number,
            "state": build_state,
            "created_at": created,
            "finished_at": meta.get("finished_at") or "",
            "date": bucket["date"] or nightly_date(created),
            "message": meta.get("message") or "nightly",
            "branch": meta.get("branch") or "main",
            "commit": meta.get("commit") or "",
            "author": meta.get("author") or "",
            "wall_mins": meta.get("wall_mins"),
            "passed": passed,
            "failed": failed,
            "soft_failed": soft,
            "skipped": skipped,
            "total_jobs": len(jobs),
            "jobs": jobs,
            "web_url": meta.get("web_url") or f"https://buildkite.com/{BK_ORG}/{pipeline_slug}/builds/{build_number}",
            "source": "test_results",
        })

    builds.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return builds


def choose_analytics_builds(buildkite_builds: list[dict], result_builds: list[dict],
                            previous_builds: list[dict] | None = None, pipeline_slug: str = "") -> list[dict]:
    """Prefer parsed test-result builds, with guards against empty overwrites."""
    if result_builds:
        if buildkite_builds and len(result_builds) < max(2, len(buildkite_builds) // 2):
            log.warning(
                "%s has only %d parsed-result builds versus %d Buildkite builds; keeping Buildkite analytics",
                pipeline_slug, len(result_builds), len(buildkite_builds),
            )
            return buildkite_builds
        if len(result_builds) > len(buildkite_builds):
            log.info("  using %d parsed test-result builds for %s analytics", len(result_builds), pipeline_slug)
        return result_builds

    if previous_builds and not buildkite_builds:
        log.warning("  preserving previous %s analytics: fresh collection returned no builds", pipeline_slug)
        return previous_builds

    return buildkite_builds


def _retry_group_key(job: dict) -> tuple[str, str]:
    step = str(job.get("step_key") or job.get("step_id") or "")
    name = str(job.get("raw_name") or job.get("name") or "unknown")
    return step or name, name


def _retry_attempt_summary(build: dict, job: dict) -> dict:
    observed_at = (
        job.get("finished_at")
        or job.get("started_at")
        or build.get("finished_at")
        or build.get("created_at")
        or ""
    )
    out = {
        "build_number": build.get("number"),
        "step": str(job.get("step_key") or job.get("step_id") or ""),
        "name": str(job.get("raw_name") or job.get("name") or "unknown"),
        "state": job.get("state") or "unknown",
        "observed_at": observed_at,
    }
    for key in ("job_id", "url", "retries_count", "retry_source", "retry_type"):
        if job.get(key) not in (None, ""):
            out[key] = job[key]
    return out


def compute_retry_analysis(builds: list[dict]) -> dict:
    """Summarize retry attempts and failed-then-passed job recoveries.

    Buildkite's explicit ``retried_in_job_id`` edge is authoritative when it
    is present. The step/name grouping also handles payloads where Buildkite
    retained retry counters but omitted the edge from a compact prior row.
    """
    retry_attempts: list[dict] = []
    recoveries: list[dict] = []
    builds_with_retries: set[int] = set()
    seen_attempts: set[tuple] = set()
    seen_recoveries: set[tuple] = set()

    for build in builds:
        build_number = int(build.get("number") or 0)
        jobs = list(build.get("jobs") or [])
        by_id = {str(job.get("job_id")): job for job in jobs if job.get("job_id")}
        retry_targets = {
            str(job.get("retried_in_job_id"))
            for job in jobs
            if job.get("retried_in_job_id")
        }
        grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for job in jobs:
            grouped[_retry_group_key(job)].append(job)

        for job in jobs:
            job_id = str(job.get("job_id") or "")
            is_attempt = (
                job_id in retry_targets
                or bool(job.get("retry_source"))
                or bool(job.get("retry_type"))
                or int(job.get("retries_count") or 0) > 0
            )
            if not is_attempt:
                continue
            attempt_key = (build_number, job_id or _retry_group_key(job))
            if attempt_key in seen_attempts:
                continue
            seen_attempts.add(attempt_key)
            retry_attempts.append(_retry_attempt_summary(build, job))
            builds_with_retries.add(build_number)

        for failed_job in jobs:
            target_id = str(failed_job.get("retried_in_job_id") or "")
            passed_job = by_id.get(target_id)
            if not passed_job:
                continue
            if failed_job.get("state") not in FAILED_JOB_STATES or passed_job.get("state") != "passed":
                continue
            recovery_key = (build_number, target_id, _retry_group_key(failed_job))
            if recovery_key in seen_recoveries:
                continue
            seen_recoveries.add(recovery_key)
            recoveries.append({
                "build_number": build_number,
                "step": str(failed_job.get("step_key") or failed_job.get("step_id") or ""),
                "name": str(failed_job.get("raw_name") or failed_job.get("name") or "unknown"),
                "observed_at": (
                    passed_job.get("finished_at")
                    or passed_job.get("started_at")
                    or failed_job.get("finished_at")
                    or build.get("finished_at")
                    or build.get("created_at")
                    or ""
                ),
                "failed_job_id": failed_job.get("job_id") or "",
                "passed_job_id": passed_job.get("job_id") or "",
                "failed_url": failed_job.get("url") or "",
                "passed_url": passed_job.get("url") or "",
            })

        for group_key, attempts in grouped.items():
            failed_jobs = [job for job in attempts if job.get("state") in FAILED_JOB_STATES]
            passed_retries = [
                job for job in attempts
                if job.get("state") == "passed"
                and (
                    str(job.get("job_id") or "") in retry_targets
                    or bool(job.get("retry_source"))
                    or bool(job.get("retry_type"))
                    or int(job.get("retries_count") or 0) > 0
                )
            ]
            if not failed_jobs:
                continue
            failed_job = failed_jobs[-1]
            for passed_job in passed_retries:
                passed_id = str(passed_job.get("job_id") or "")
                recovery_key = (build_number, passed_id, group_key)
                if recovery_key in seen_recoveries:
                    continue
                seen_recoveries.add(recovery_key)
                recoveries.append({
                    "build_number": build_number,
                    "step": group_key[0],
                    "name": group_key[1],
                    "observed_at": (
                        passed_job.get("finished_at")
                        or passed_job.get("started_at")
                        or failed_job.get("finished_at")
                        or build.get("finished_at")
                        or build.get("created_at")
                        or ""
                    ),
                    "failed_job_id": failed_job.get("job_id") or "",
                    "passed_job_id": passed_job.get("job_id") or "",
                    "failed_url": failed_job.get("url") or "",
                    "passed_url": passed_job.get("url") or "",
                })

    retry_attempts.sort(key=lambda row: (row.get("build_number") or 0, row.get("step", ""), row["name"]), reverse=True)
    recoveries.sort(key=lambda row: (row.get("build_number") or 0, row.get("step", ""), row["name"]), reverse=True)
    return {
        "summary": {
            "builds_evaluated": len(builds),
            "builds_with_retries": len(builds_with_retries),
            "retry_attempt_count": len(retry_attempts),
            "failed_then_passed_recovery_count": len(recoveries),
        },
        "retry_attempts": retry_attempts,
        "failed_then_passed_recoveries": recoveries,
    }


def attach_main_reliability(
    pipeline_data: dict,
    reliability: dict,
    retry_builds: list[dict] | None = None,
    retry_analysis: dict | None = None,
) -> None:
    """Attach the authoritative bounded all-main cohort and retry evidence."""
    pipeline_slug = str((reliability.get("cohort") or {}).get("pipeline") or "")
    if not pipeline_slug or not validate_all_main_reliability(reliability, pipeline_slug):
        raise ValueError("all-main reliability payload lacks strict exhaustive provenance")
    pipeline_data["all_main_reliability"] = reliability
    # These legacy fields duplicated every retained reliability observation.
    # No consumer trusts them; operations deliberately reads the authoritative
    # cohort above. Drop them if a caller reuses an existing pipeline mapping.
    pipeline_data.pop("main_builds", None)
    pipeline_data.pop("main_builds_provenance", None)
    eligible_numbers = {
        int(build.get("number") or 0)
        for build in reliability.get("builds") or []
        if int(build.get("number") or 0)
    }
    if retry_analysis is not None and validate_retry_analysis(
        retry_analysis,
        pipeline_slug,
        eligible_numbers,
    ):
        pipeline_data["main_retry_analysis"] = retry_analysis
        return
    if retry_builds is None:
        pipeline_data["main_retry_analysis"] = {
            "available": False,
            "summary": {
                "builds_evaluated": len(eligible_numbers),
                "builds_with_retries": 0,
                "retry_attempt_count": 0,
                "failed_then_passed_recovery_count": 0,
            },
            "retry_attempts": [],
            "failed_then_passed_recoveries": [],
            "provenance": {
                "source_pipeline": pipeline_slug,
                "complete": False,
                "reason": "complete raw retry attempts were unavailable; compacted history was not substituted",
            },
        }
        return
    complete_retry_builds = [
        build
        for build in retry_builds or []
        if int(build.get("number") or 0) in eligible_numbers
    ]
    analysis = compute_retry_analysis(complete_retry_builds)
    analysis["available"] = True
    analysis["provenance"] = {
        "source_pipeline": pipeline_slug,
        "complete": True,
        "scope": "same completed branch=main builds and test-job queue scope as all-main reliability",
        "cohort_build_numbers": sorted(eligible_numbers),
    }
    pipeline_data["main_retry_analysis"] = analysis


def validate_retry_analysis(
    payload: Any,
    pipeline_slug: str,
    cohort_build_numbers: set[int],
) -> bool:
    if not isinstance(payload, dict):
        return False
    provenance = payload.get("provenance")
    attempts = payload.get("retry_attempts")
    recoveries = payload.get("failed_then_passed_recoveries")
    provenance_builds = (
        provenance.get("cohort_build_numbers")
        if isinstance(provenance, dict)
        else None
    )
    if (
        payload.get("available") is not True
        or not isinstance(provenance, dict)
        or provenance.get("source_pipeline") != pipeline_slug
        or provenance.get("complete") is not True
        or not isinstance(provenance_builds, list)
        or any(not isinstance(number, int) or isinstance(number, bool) for number in provenance_builds)
        or set(provenance_builds) != cohort_build_numbers
        or not isinstance(attempts, list)
        or not isinstance(recoveries, list)
    ):
        return False
    for row in attempts:
        if not isinstance(row, dict):
            return False
        try:
            number = int(row.get("build_number") or 0)
        except (TypeError, ValueError):
            return False
        url = row.get("job_url") or row.get("url")
        if (
            number not in cohort_build_numbers
            or row.get("source_pipeline") not in (None, "", pipeline_slug)
            or not buildkite_job_url_matches(url, pipeline_slug, number)
        ):
            return False
    for row in recoveries:
        if not isinstance(row, dict):
            return False
        try:
            number = int(row.get("build_number") or 0)
        except (TypeError, ValueError):
            return False
        if (
            number not in cohort_build_numbers
            or row.get("source_pipeline") not in (None, "", pipeline_slug)
            or not buildkite_job_url_matches(
                row.get("failed_url") or row.get("failed_job_url"),
                pipeline_slug,
                number,
            )
            or not buildkite_job_url_matches(
                row.get("passed_url") or row.get("passed_job_url"),
                pipeline_slug,
                number,
            )
        ):
            return False
    return True


def _safe_build_number(build: Any) -> int:
    if not isinstance(build, dict):
        return 0
    value = build.get("number")
    if isinstance(value, bool):
        return 0
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _fetched_build_rank(build: dict) -> tuple:
    state = str(build.get("state") or "").lower()
    return (
        state in {"passed", "failed"} and bool(build.get("finished_at")),
        len(build.get("jobs") or []),
        str(build.get("finished_at") or ""),
        str(build.get("created_at") or ""),
    )


def _fetch_pipeline_build_leg(
    pipeline_slug: str,
    token: str,
    *,
    filter_name: str,
    since: str,
    max_pages: int | None = None,
) -> tuple[list[dict], dict]:
    """Fetch one exhaustive Buildkite list leg for a timestamp filter."""
    if filter_name not in {"created_from", "finished_from"}:
        raise ValueError(f"Unsupported Buildkite timestamp filter: {filter_name!r}")
    path = f"/organizations/{BK_ORG}/pipelines/{pipeline_slug}/builds"
    page_limit = max_pages if max_pages is not None else BUILD_FETCH_MAX_PAGES
    by_number: dict[int, dict] = {}
    termination_reason = "max_pages"
    exhaustive = False
    pages_fetched = 0
    for page in range(1, page_limit + 1):
        pages_fetched = page
        rows = bk_get(
            path,
            token,
            {
                "branch": "main",
                filter_name: since,
                "per_page": BUILD_FETCH_PAGE_SIZE,
                "page": page,
                "include_retried_jobs": "true",
            },
        )
        if not isinstance(rows, list):
            raise RuntimeError(
                f"Malformed Buildkite builds page for {pipeline_slug}: "
                "expected a JSON list"
            )
        if not rows:
            termination_reason = "empty_page"
            exhaustive = True
            break
        novel_numbers = 0
        for row_index, build in enumerate(rows, start=1):
            number = _safe_build_number(build)
            if not isinstance(build, dict) or not number:
                raise RuntimeError(
                    f"Malformed Buildkite builds page for {pipeline_slug}: "
                    f"invalid row {row_index} on page {page}"
                )
            existing = by_number.get(number)
            if existing is None:
                by_number[number] = build
                novel_numbers += 1
            elif _fetched_build_rank(build) > _fetched_build_rank(existing):
                by_number[number] = build
        if len(rows) < BUILD_FETCH_PAGE_SIZE:
            termination_reason = "short_page"
            exhaustive = True
            break
        if not novel_numbers:
            termination_reason = "duplicate_page"
            log.warning("  stopping pagination at page %d: no new build numbers", page)
            break
    if not exhaustive:
        log.warning(
            "  incomplete Buildkite pagination (%s after %d pages, %d builds)",
            termination_reason,
            pages_fetched,
            len(by_number),
        )
    builds_raw = sorted(
        by_number.values(),
        key=lambda build: (
            str(build.get("created_at") or ""),
            _safe_build_number(build),
        ),
        reverse=True,
    )
    log.info("  %d unique builds fetched from %s", len(builds_raw), filter_name)
    provenance = {
        filter_name: since,
        "filter": filter_name,
        "page_size": BUILD_FETCH_PAGE_SIZE,
        "max_pages": page_limit,
        "pages_fetched": pages_fetched,
        "termination_reason": termination_reason,
        "exhaustive": exhaustive,
    }
    return builds_raw, provenance


def _as_utc_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_ts(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _cache_diagnostics(
    cache,
    *,
    decision: str,
    ref_now: datetime,
    cutoff: datetime,
) -> dict:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "load_valid": cache.valid,
        "load_reason": cache.reason,
        "decision": decision,
        "window_days": cache.window_days,
        "cached_builds": len(cache.builds),
        "requested_cutoff": cutoff.isoformat(),
        "watermark": (
            value.isoformat()
            if (value := _as_utc_datetime(cache.watermark)) is not None
            else None
        ),
        "last_full_at": (
            value.isoformat()
            if (value := _as_utc_datetime(cache.last_full_at)) is not None
            else None
        ),
        "complete_from": (
            value.isoformat()
            if (value := _as_utc_datetime(cache.complete_from)) is not None
            else None
        ),
        "generated_at": (
            value.isoformat()
            if (value := _as_utc_datetime(cache.generated_at)) is not None
            else None
        ),
        "ref_now": ref_now.isoformat(),
    }


def _mark_cache_write_disabled(
    diagnostics: dict,
    pipeline_slug: str,
    exc: Exception,
) -> None:
    """Record a bounded public diagnostic while retaining detailed CI logs."""
    reason = getattr(exc, "reason", None)
    if not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", reason):
        reason = type(exc).__name__
    diagnostics.update({
        "cache_written": False,
        "cache_disabled": True,
        "cache_disabled_reason": f"write_{reason}",
    })
    log.warning(
        "  private analytics cache disabled for %s; continuing with fetched "
        "builds (%s: %s)",
        pipeline_slug,
        type(exc).__name__,
        exc,
    )


def _append_cache_save_output(path: Path, *, enabled: bool) -> None:
    """Expose one fail-closed boolean to the later Actions cache-save step."""
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(f"analytics_cache_save={'true' if enabled else 'false'}\n")


def _reliability_builds_with_cache_aliases(
    builds: list[dict],
    pipeline_slug: str,
    previous_reliability: dict | None = None,
) -> list[dict]:
    """Restore semantic messages that compact cache rows intentionally replace.

    The private cache records ``canonical_nightly`` instead of retaining every
    build message. It also records the allowlisted upstream scheduled gating
    kind. Prefer the exact bounded title from the previous validated catalog;
    the generic classified alias remains a fallback for uncataloged rows.
    """
    previous_messages = {}
    if validate_all_main_reliability(previous_reliability, pipeline_slug):
        previous_messages = {
            _safe_build_number(build): _bounded_catalog_message(
                build.get("message")
            )
            for build in previous_reliability.get("builds") or []
            if _safe_build_number(build)
        }
    compatible = []
    for build in builds:
        if not build.get("message"):
            restored_message = previous_messages.get(
                _safe_build_number(build),
                "",
            ) or _cache_compatibility_message(build, pipeline_slug)
            if restored_message:
                build = {**build, "message": restored_message}
        compatible.append(build)
    return compatible


def _cache_compatibility_message(build: dict, pipeline_slug: str) -> str:
    scheduled_kind = build.get("scheduled_gating_kind")
    scheduled_message = (
        CACHE_SCHEDULED_GATING_MESSAGE.get(pipeline_slug, {}).get(
            scheduled_kind,
            "",
        )
        if isinstance(scheduled_kind, str)
        else ""
    )
    if scheduled_message:
        return scheduled_message
    if build.get("canonical_nightly") is True:
        return CACHE_NIGHTLY_MESSAGE.get(pipeline_slug, "")
    return ""


def _full_cached_fetch(
    pipeline_slug: str,
    token: str,
    days: int,
    *,
    cache_dir: Path,
    cache,
    ref_now: datetime,
    cutoff: datetime,
    reason: str,
    max_pages: int | None,
    incremental_diagnostics: dict | None = None,
) -> tuple[list[dict], dict]:
    rows, leg = _fetch_pipeline_build_leg(
        pipeline_slug,
        token,
        filter_name="created_from",
        since=cutoff.isoformat(),
        max_pages=max_pages,
    )
    diagnostics = _cache_diagnostics(
        cache,
        decision=reason,
        ref_now=ref_now,
        cutoff=cutoff,
    )
    diagnostics.update(
        {
            "fetch_mode": "full",
            "returned_builds": len(rows),
            "cache_written": False,
        }
    )
    if incremental_diagnostics:
        diagnostics["incremental_attempt"] = incremental_diagnostics
    provenance = {
        **leg,
        "fetch_mode": (
            "full_after_incremental" if incremental_diagnostics else "full"
        ),
        "legs": {"created": leg},
        "cache": diagnostics,
    }
    if leg.get("exhaustive") is not True:
        raise IncompleteAnalyticsCollection(
            f"Incomplete full Buildkite collection for {pipeline_slug}: "
            f"{leg.get('termination_reason') or 'unknown'}",
            provenance,
        )

    builds = merge_builds([], rows, cutoff=cutoff)
    try:
        write_build_cache(
            cache_dir,
            pipeline_slug,
            builds=builds,
            watermark=ref_now,
            window_days=days,
            last_full_at=ref_now,
            updated_at=ref_now,
            complete_from=cutoff,
        )
    except Exception as exc:
        _mark_cache_write_disabled(diagnostics, pipeline_slug, exc)
    else:
        diagnostics["cache_written"] = True
    diagnostics["returned_builds"] = len(builds)
    diagnostics["watermark"] = ref_now.isoformat()
    diagnostics["last_full_at"] = ref_now.isoformat()
    diagnostics["complete_from"] = cutoff.isoformat()
    return builds, provenance


def _fetch_individual_build(
    pipeline_slug: str,
    token: str,
    build_number: int,
) -> dict:
    path = (
        f"/organizations/{BK_ORG}/pipelines/{pipeline_slug}/builds/"
        f"{build_number}"
    )
    build = bk_get(path, token, {"include_retried_jobs": "true"})
    if not isinstance(build, dict) or _safe_build_number(build) != build_number:
        raise RuntimeError(
            f"Individual Buildkite refresh for {pipeline_slug} build {build_number} "
            "did not return a matching JSON build object"
        )
    return build


def _incremental_cached_fetch(
    pipeline_slug: str,
    token: str,
    days: int,
    *,
    cache_dir: Path,
    cache,
    ref_now: datetime,
    cutoff: datetime,
    max_pages: int | None,
) -> tuple[list[dict], dict] | tuple[None, dict]:
    watermark = _as_utc_datetime(cache.watermark)
    if watermark is None:
        return None, {"failure": "cache_watermark_missing"}
    overlap_from = max(cutoff, watermark - ANALYTICS_CACHE_OVERLAP)
    overlap_iso = overlap_from.isoformat()
    diagnostics = _cache_diagnostics(
        cache,
        decision="incremental",
        ref_now=ref_now,
        cutoff=cutoff,
    )
    diagnostics.update(
        {
            "fetch_mode": "incremental",
            "overlap_from": overlap_iso,
            "overlap_hours": int(ANALYTICS_CACHE_OVERLAP.total_seconds() / 3600),
            "cache_written": False,
        }
    )
    try:
        created, created_leg = _fetch_pipeline_build_leg(
            pipeline_slug,
            token,
            filter_name="created_from",
            since=overlap_iso,
            max_pages=max_pages,
        )
        finished, finished_leg = _fetch_pipeline_build_leg(
            pipeline_slug,
            token,
            filter_name="finished_from",
            since=overlap_iso,
            max_pages=max_pages,
        )
        diagnostics["legs"] = {
            "created": created_leg,
            "finished": finished_leg,
        }
        if (
            created_leg.get("exhaustive") is not True
            or finished_leg.get("exhaustive") is not True
        ):
            diagnostics["failure"] = "incremental_pagination_incomplete"
            return None, diagnostics

        refresh_numbers = builds_needing_refresh(cache.builds)
        refreshed = [
            _fetch_individual_build(pipeline_slug, token, build_number)
            for build_number in refresh_numbers
        ]
        fresh = merge_builds(created, finished, cutoff=cutoff)
        fresh = merge_builds(fresh, refreshed, cutoff=cutoff)
        builds = merge_builds(cache.builds, fresh, cutoff=cutoff)
        last_full_at = _as_utc_datetime(cache.last_full_at)
        if last_full_at is None:
            diagnostics["failure"] = "cache_last_full_at_missing"
            return None, diagnostics
        # Compare like-for-like cache projections. Fresh Buildkite rows may
        # contain large fields that the private cache intentionally discards;
        # comparing those raw rows to the compact cache would manufacture a
        # false growth signal on every incremental run.
        cached_materialized_bytes = _compact_json_bytes(
            sanitize_builds(cache.builds, pipeline_slug)
        )
        merged_materialized_bytes = _compact_json_bytes(
            sanitize_builds(builds, pipeline_slug)
        )
        materialized_delta_bytes = (
            merged_materialized_bytes - cached_materialized_bytes
        )
        materialized_growth_ratio = (
            merged_materialized_bytes / cached_materialized_bytes
            if cached_materialized_bytes
            else None
        )
        diagnostics.update({
            "cached_materialized_bytes": cached_materialized_bytes,
            "merged_materialized_bytes": merged_materialized_bytes,
            "materialized_delta_bytes": materialized_delta_bytes,
            "materialized_growth_ratio": (
                round(materialized_growth_ratio, 4)
                if materialized_growth_ratio is not None
                else None
            ),
        })
        if (
            cached_materialized_bytes
            and materialized_delta_bytes
            >= ANALYTICS_CACHE_SUSPICIOUS_GROWTH_MIN_BYTES
            and materialized_growth_ratio
            >= ANALYTICS_CACHE_SUSPICIOUS_GROWTH_RATIO
        ):
            diagnostics["failure"] = "suspicious_incremental_materialization"
            log.warning(
                "  incremental %s materialization grew by %d bytes (%.1f%%); "
                "reconciling once with a full fetch",
                pipeline_slug,
                materialized_delta_bytes,
                (materialized_growth_ratio - 1) * 100,
            )
            # Do not persist the suspicious merge. The caller performs its
            # existing one-shot exhaustive fallback and only then replaces the
            # validated cache.
            return None, diagnostics
        cache_written = True
        try:
            write_build_cache(
                cache_dir,
                pipeline_slug,
                builds=builds,
                watermark=ref_now,
                window_days=days,
                last_full_at=last_full_at,
                updated_at=ref_now,
                complete_from=cutoff,
            )
        except Exception as exc:
            cache_written = False
            _mark_cache_write_disabled(diagnostics, pipeline_slug, exc)
    except BuildkiteRequestGuardError:
        raise
    except Exception as exc:
        diagnostics["failure"] = f"{type(exc).__name__}: {exc}"
        return None, diagnostics

    diagnostics.update(
        {
            "created_builds": len(created),
            "finished_builds": len(finished),
            "refreshed_builds": len(refreshed),
            "refresh_build_numbers": refresh_numbers,
            "fresh_builds": len(fresh),
            "returned_builds": len(builds),
            "cache_written": cache_written,
            "watermark": ref_now.isoformat(),
        }
    )
    pages_fetched = sum(
        int(leg.get("pages_fetched") or 0)
        for leg in (created_leg, finished_leg)
    )
    provenance = {
        # This is the completeness boundary of the merged cache, not the
        # narrower API overlap used by the two incremental legs below.
        "created_from": cutoff.isoformat(),
        "page_size": BUILD_FETCH_PAGE_SIZE,
        "max_pages": max_pages if max_pages is not None else BUILD_FETCH_MAX_PAGES,
        "pages_fetched": pages_fetched,
        "termination_reason": "incremental_complete",
        "exhaustive": True,
        "fetch_mode": "incremental",
        "legs": diagnostics["legs"],
        "cache": diagnostics,
    }
    return builds, provenance


def _fetch_pipeline_builds_cached(
    pipeline_slug: str,
    token: str,
    days: int,
    *,
    cache_dir: Path,
    ref_now: datetime,
    max_pages: int | None,
) -> tuple[list[dict], dict]:
    frozen_now = _as_utc_datetime(ref_now)
    if frozen_now is None:
        raise ValueError("ref_now must be a timezone-aware datetime")
    cutoff = frozen_now - timedelta(days=days)
    cache = load_build_cache(
        cache_dir,
        pipeline_slug,
        cutoff=cutoff,
        window_days=days,
        ref_now=frozen_now,
    )
    last_full_at = _as_utc_datetime(cache.last_full_at)
    cache_generated_at = _as_utc_datetime(cache.generated_at)
    if not cache.valid:
        full_reason = f"cache_{cache.reason or 'invalid'}"
    elif last_full_at is None:
        full_reason = "cache_last_full_at_missing"
    elif (
        cache_generated_at is None
        or cache_generated_at.date() != frozen_now.date()
    ):
        full_reason = "utc_day_reconciliation"
    elif frozen_now - last_full_at >= ANALYTICS_CACHE_FULL_REFRESH_INTERVAL:
        full_reason = "daily_reconciliation"
    else:
        builds, incremental = _incremental_cached_fetch(
            pipeline_slug,
            token,
            days,
            cache_dir=cache_dir,
            cache=cache,
            ref_now=frozen_now,
            cutoff=cutoff,
            max_pages=max_pages,
        )
        if builds is not None:
            return builds, incremental
        full_reason = "incremental_fallback_full"
        return _full_cached_fetch(
            pipeline_slug,
            token,
            days,
            cache_dir=cache_dir,
            cache=cache,
            ref_now=frozen_now,
            cutoff=cutoff,
            reason=full_reason,
            max_pages=max_pages,
            incremental_diagnostics=incremental,
        )

    return _full_cached_fetch(
        pipeline_slug,
        token,
        days,
        cache_dir=cache_dir,
        cache=cache,
        ref_now=frozen_now,
        cutoff=cutoff,
        reason=full_reason,
        max_pages=max_pages,
    )


def fetch_pipeline_builds(
    pipeline_slug,
    token,
    days,
    max_pages=None,
    *,
    ref_now=None,
    cache_dir=None,
):
    """Fetch Buildkite ``main`` builds exhaustively within the safety cap.

    ``ref_now`` is optional to preserve the historical call surface used by
    collectors and tests. Cached collection passes its one frozen reference
    time so the query boundary and downstream windows cannot drift apart.
    """
    context = _FETCH_CONTEXT.get()
    if cache_dir is None and ref_now is None and context is not None:
        cache_dir, ref_now = context
    frozen_now = ref_now or datetime.now(timezone.utc)
    if cache_dir is not None:
        return _fetch_pipeline_builds_cached(
            pipeline_slug,
            token,
            days,
            cache_dir=Path(cache_dir),
            ref_now=frozen_now,
            max_pages=max_pages,
        )
    since = (frozen_now - timedelta(days=days)).isoformat()
    log.info("Fetching %s builds (last %d days)...", pipeline_slug, days)
    return _fetch_pipeline_build_leg(
        pipeline_slug,
        token,
        filter_name="created_from",
        since=since,
        max_pages=max_pages,
    )


def summarize_pipeline_builds(pipeline_slug, builds_raw, nightly_only=False, name_pattern=None):
    """Normalize Buildkite builds while retaining per-attempt provenance."""
    builds_raw = list(builds_raw or [])

    # Filter to nightly if requested
    if nightly_only and name_pattern:
        pat = re.compile(name_pattern, re.IGNORECASE)
        builds_raw = [
            b
            for b in builds_raw
            if b.get("canonical_nightly") is True
            or pat.search(b.get("message", "") or "")
        ]
        log.info("  %d nightly builds after filter", len(builds_raw))

    builds = []

    for b in builds_raw:
        build_num = b.get("number", 0)
        build_state = b.get("state", "")
        created = b.get("created_at", "")
        finished = b.get("finished_at", "")
        wall_mins = duration_mins(created, finished)
        raw_message = b.get("message") or _cache_compatibility_message(
            b,
            pipeline_slug,
        )
        message = raw_message[:100]
        author = (b.get("creator") or {}).get("name", "") or (b.get("author") or {}).get("name", "")

        jobs = [j for j in b.get("jobs", []) if j.get("type") == "script"]

        job_summaries = []
        passed = failed = soft = 0

        for j in jobs:
            name = j.get("name", "unknown")
            norm = normalize_job(name)
            state = j.get("state", "")
            sf = j.get("soft_failed", False)
            queue = j.get("q") or queue_from_rules(j.get("agent_query_rules"))

            dur = duration_mins(j.get("started_at"), j.get("finished_at"))
            wait = duration_mins(j.get("runnable_at"), j.get("started_at"))
            end_to_end = duration_mins(j.get("runnable_at"), j.get("finished_at"))

            if state == "passed":
                passed += 1
            elif sf:
                soft += 1
            elif state in ("failed", "timed_out", "broken"):
                failed += 1

            job_id = str(j.get("id") or "")
            step_id = str((j.get("step") or {}).get("id") or "")
            job_entry = {
                "name": norm,
                "raw_name": name,
                "state": "soft_fail" if sf else state,
                "dur": dur,
                "wall_completion_mins": dur,
                "queue_wait_mins": wait,
                "end_to_end_mins": end_to_end,
                "duration_source": "buildkite_wall",
                "started_at": j.get("started_at") or "",
                "finished_at": j.get("finished_at") or "",
                "runnable_at": j.get("runnable_at") or "",
            }
            for key in RETRY_FIELDS:
                value = j.get(key, (j.get("step") or {}).get("key") if key == "step_key" else None)
                job_entry[key] = value
            if job_id:
                job_entry["job_id"] = job_id
            if step_id:
                job_entry["step_id"] = step_id
            job_url = buildkite_job_url(
                pipeline_slug,
                build_num,
                job_id,
                step_id,
            ) or j.get("web_url", "")
            if job_url:
                job_entry["url"] = job_url
            if wait is not None: job_entry["wait"] = round(wait, 1)
            if queue: job_entry["q"] = queue
            job_summaries.append(job_entry)

        builds.append({
            "number": build_num,
            "state": build_state,
            "created_at": created,
            "finished_at": finished,
            "date": nightly_date(created),
            "message": message,
            "branch": b.get("branch") or "",
            "commit": b.get("commit") or "",
            "author": author,
            "wall_mins": wall_mins,
            "passed": passed,
            "failed": failed,
            "soft_failed": soft,
            "total_jobs": len(jobs),
            "jobs": job_summaries,
            "web_url": b.get("web_url")
            or buildkite_job_url(pipeline_slug, build_num),
        })

    # Sort builds newest first
    builds.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return builds


def collect_pipeline(pipeline_slug, token, days, nightly_only=False, name_pattern=None, builds_raw=None):
    """Fetch and normalize builds, preserving the historical public API."""
    if builds_raw is None:
        builds_raw, _ = fetch_pipeline_builds(pipeline_slug, token, days)
    return summarize_pipeline_builds(pipeline_slug, builds_raw, nightly_only, name_pattern)


def compute_job_rankings(builds):
    """Aggregate per-job rankings from the provided build slice."""
    job_stats = defaultdict(lambda: {"runs": 0, "passed": 0, "failed": 0, "soft_failed": 0,
                                     "durations": [], "wait_times": [], "queues": set()})

    for build in builds:
        for job in build.get("jobs", []):
            name = job.get("name", "unknown")
            state = job.get("state", "")
            queue = job.get("q")
            dur = job.get("dur")
            wait = job.get("wait")

            if state == "passed":
                job_stats[name]["passed"] += 1
            elif state == "soft_fail":
                job_stats[name]["soft_failed"] += 1
            elif state in ("failed", "timed_out", "broken"):
                job_stats[name]["failed"] += 1

            job_stats[name]["runs"] += 1
            if dur is not None:
                job_stats[name]["durations"].append(dur)
            if wait is not None:
                job_stats[name]["wait_times"].append(wait)
            if queue:
                job_stats[name]["queues"].add(queue)

    job_rankings = []
    for name, s in sorted(job_stats.items()):
        total = s["runs"]
        if total == 0:
            continue
        durs = sorted(s["durations"])
        waits = sorted(s["wait_times"])
        fail_rate = round((s["failed"] + s["soft_failed"]) / total * 100, 1)
        job_rankings.append({
            "name": name,
            "runs": total,
            "passed": s["passed"],
            "failed": s["failed"],
            "soft_failed": s["soft_failed"],
            "fail_rate": fail_rate,
            "is_soft_fail": s["failed"] == 0 and s["soft_failed"] > 0,
            "median_dur": round(median(durs), 1) if durs else None,
            "p90_dur": round(percentile(durs, 90), 1) if durs else None,
            "avg_dur": round(mean(durs), 1) if durs else None,
            "max_dur": round(max(durs), 1) if durs else None,
            "median_wait": round(median(waits), 1) if waits else None,
            "p90_wait": round(percentile(waits, 90), 1) if waits else None,
            "avg_wait": round(mean(waits), 1) if waits else None,
            "max_wait": round(max(waits), 1) if waits else None,
            "queues": sorted(s["queues"]),
        })
    return job_rankings


def compute_daily_stats(builds):
    """Aggregate pass/fail per day for stacked bar chart."""
    by_date = defaultdict(lambda: {"passed": 0, "failed": 0, "total": 0})
    for b in builds:
        d = b.get("date", "")
        if not d: continue
        if b["state"] in ("passed",):
            by_date[d]["passed"] += 1
        elif b["state"] in ("failed", "failing"):
            by_date[d]["failed"] += 1
        by_date[d]["total"] += 1
    return [{"date": k, **v} for k, v in sorted(by_date.items())]


def compute_queue_stats(job_rankings):
    """Aggregate wait times by queue."""
    by_queue = defaultdict(lambda: {"jobs": 0, "waits": []})
    for j in job_rankings:
        for q in j.get("queues", []):
            by_queue[q]["jobs"] += j["runs"]
            if j.get("median_wait") is not None:
                by_queue[q]["waits"].extend([j["median_wait"]] * j["runs"])

    queue_stats = []
    for q, d in sorted(by_queue.items()):
        waits = d["waits"]
        queue_stats.append({
            "queue": q,
            "jobs": d["jobs"],
            "median_wait": round(median(waits), 1) if waits else None,
            "p90_wait": round(sorted(waits)[int(len(waits) * 0.9)], 1) if len(waits) > 1 else None,
            "avg_wait": round(mean(waits), 1) if waits else None,
            "max_wait": round(max(waits), 1) if waits else None,
        })
    queue_stats.sort(key=lambda x: x.get("median_wait") or 0, reverse=True)
    return queue_stats


def compute_summary(builds, job_rankings):
    total_builds = len(builds)
    passed_builds = sum(1 for b in builds if b["state"] == "passed")
    terminal_builds = sum(
        1 for build in builds
        if str(build.get("state") or "").lower() in TERMINAL_BUILD_STATES
    )
    failed_builds = terminal_builds - passed_builds
    build_pass_rate_pct = (
        round(passed_builds / terminal_builds * 100, 1)
        if terminal_builds else 0.0
    )
    hard_failed_jobs = sum(1 for j in job_rankings if j["failed"] > 0)
    soft_failed_jobs = sum(1 for j in job_rankings if j["failed"] == 0 and j["soft_failed"] > 0)
    return {
        "total_builds": total_builds,
        "terminal_builds": terminal_builds,
        "passed": passed_builds,
        "failed": failed_builds,
        "build_pass_rate_pct": build_pass_rate_pct,
        "build_pass_rate_basis": BUILD_PASS_RATE_BASIS,
        "pass_rate": build_pass_rate_pct,
        "total_jobs_tracked": len(job_rankings),
        "jobs_with_failures": hard_failed_jobs + soft_failed_jobs,
        "jobs_with_hard_failures": hard_failed_jobs,
        "jobs_with_soft_failures": soft_failed_jobs,
    }


def filter_builds_for_window(builds, window_days, now=None):
    if window_days <= 0:
        return []
    ref_now = now or datetime.now(timezone.utc)
    cutoff = ref_now - timedelta(days=window_days)
    return [
        build for build in builds
        if (parse_ts(build.get("created_at")) or cutoff) >= cutoff
    ]


def build_window_block(builds, window_days):
    job_rankings = compute_job_rankings(builds)
    failure_ranking = sorted(job_rankings, key=lambda x: x["fail_rate"], reverse=True)
    duration_ranking = sorted(job_rankings, key=lambda x: x.get("median_dur") or 0, reverse=True)
    return {
        "window_days": window_days,
        "build_count": len(builds),
        "summary": compute_summary(builds, job_rankings),
        "daily_stats": compute_daily_stats(builds),
        "builds": [chart_build_summary(build) for build in builds[:ANALYTICS_WINDOW_BUILD_LIMIT]],
        "nightly_builds": [chart_build_summary(build) for build in builds[:ANALYTICS_WINDOW_NIGHTLY_LIMIT]],
        "failure_ranking": [j for j in failure_ranking if j["failed"] > 0 or j["soft_failed"] > 0],
        "duration_ranking": duration_ranking,
        "queue_stats": compute_queue_stats(job_rankings),
    }


def compute_window_blocks(builds, max_days, now=None):
    window_days = sorted({d for d in ANALYTICS_WINDOWS_DAYS if d <= max_days} | {max_days})
    return {
        f"{days}d": build_window_block(filter_builds_for_window(builds, days, now=now), days)
        for days in window_days
    }


def chart_build_summary(build):
    """Return the per-build fields chart widgets need, without duplicating jobs."""
    return {key: value for key, value in build.items() if key != "jobs"}


def _buildkite_url_ids(url: str) -> dict[str, str]:
    """Extract compact Buildkite identifiers from an exact step URL."""
    if not url:
        return {}
    match = re.search(r"[?&](jid|sid)=([0-9a-fA-F-]+)", str(url))
    if not match:
        return {}
    key = "job_id" if match.group(1) == "jid" else "step_id"
    return {key: match.group(2)}


def gating_job_summary(job):
    """Return only fields needed by the AMD gating executive view."""
    keep = ("name", "raw_name", "state", "q", "job_id", "step_id")
    out = {key: job[key] for key in keep if key in job and job[key] not in (None, "")}
    if not out.get("job_id") and not out.get("step_id"):
        out.update(_buildkite_url_ids(str(job.get("url") or job.get("web_url") or "")))
    if not out.get("job_id") and not out.get("step_id") and (job.get("url") or job.get("web_url")):
        out["url"] = job.get("url") or job.get("web_url")
    return out


def gating_build_summary(build):
    """Slim nightly build payload for CI Health gating matching."""
    keep = ("number", "state", "created_at", "date", "message", "web_url")
    out = {key: build[key] for key in keep if key in build and build[key] not in (None, "")}
    out["jobs"] = [gating_job_summary(job) for job in build.get("jobs") or []]
    return out


def _atomic_write_text(out_path: Path, text: str) -> None:
    """Replace ``out_path`` only after a same-directory file is durable."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{out_path.name}.",
        suffix=".tmp",
        dir=out_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, out_path)
    except BaseException:
        # ``fd`` belongs to the context manager once ``fdopen`` succeeds. If
        # it failed before that point, closing it here is still safe.
        try:
            os.close(fd)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def write_gating_nightlies(output: Path, all_data: dict[str, dict[str, Any]], generated_at: str) -> None:
    payload = {
        "generated_at": generated_at,
        "source": "scripts/vllm/collect_analytics.py",
    }
    for slug in ("ci", "amd-ci"):
        block = all_data.get(slug) or {}
        source_builds = block.get("builds") or []
        selected_builds = [
            gating_build_summary(build)
            for build in source_builds[:GATING_NIGHTLY_LIMIT]
        ]
        payload[slug] = {
            "pipeline": slug,
            "display_name": block.get("display_name") or PIPELINES.get(slug, slug),
            "builds": selected_builds,
            "retention": {
                "policy": "drop_oldest_complete_builds",
                "configured_build_limit": GATING_NIGHTLY_LIMIT,
                "source_build_count": len(source_builds),
                "selected_build_count": len(selected_builds),
                "retained_build_count": len(selected_builds),
                "omitted_by_count_limit": max(0, len(source_builds) - len(selected_builds)),
                "omitted_by_byte_limit": 0,
                "byte_limited": False,
                "max_bytes": GATING_NIGHTLIES_MAX_BYTES,
            },
        }

    def serialized() -> str:
        return _compact_json(payload) + "\n"

    candidate = serialized()
    while len(candidate.encode("utf-8")) > GATING_NIGHTLIES_MAX_BYTES:
        # Each list is newest-first. Remove from the longer retained suffix so
        # both pipelines keep comparable recent coverage, and never publish a
        # partial build or discard the newest build of a nonempty pipeline.
        removable = [
            slug for slug in ("ci", "amd-ci") if len(payload[slug]["builds"]) > 1
        ]
        if not removable:
            required = len(candidate.encode("utf-8"))
            raise IncompleteAnalyticsCollection(
                "gating_nightlies.json cannot fit its byte budget while "
                "preserving the newest complete build for each pipeline: "
                f"{required} > {GATING_NIGHTLIES_MAX_BYTES} bytes",
                {
                    "collector": "ci_analytics",
                    "artifact": "gating_nightlies.json",
                    "reason_class": "payload-budget",
                    "serialized_bytes": required,
                    "max_bytes": GATING_NIGHTLIES_MAX_BYTES,
                },
            )
        slug = max(removable, key=lambda item: (len(payload[item]["builds"]), item))
        payload[slug]["builds"].pop()
        retention = payload[slug]["retention"]
        retention["retained_build_count"] = len(payload[slug]["builds"])
        retention["omitted_by_byte_limit"] += 1
        retention["byte_limited"] = True
        candidate = serialized()

    out_path = output / "gating_nightlies.json"
    _atomic_write_text(out_path, candidate)
    log.info("Wrote %s (%d bytes)", out_path, len(candidate.encode("utf-8")))


def _legacy_reliability_migration_error(
    slug: str,
    reason: str,
) -> IncompleteAnalyticsCollection:
    provenance = {
        "collector": "ci_analytics",
        "reason_class": "schema-drift",
        "failure_kind": "invalid-legacy-reliability",
        "pipeline": slug,
        "source_schema_version": 1,
        "target_schema_version": RELIABILITY_SCHEMA_VERSION,
        "migration_reason": reason,
    }
    log.error(
        "Private analytics reliability migration failed: %s",
        _compact_json(provenance),
    )
    return IncompleteAnalyticsCollection(
        f"Cannot losslessly migrate preserved {slug} reliability schema v1: {reason}",
        provenance,
    )


def _migrate_preserved_reliability_v1(payload: dict) -> tuple[dict, dict[str, Any]]:
    """Losslessly normalize validated schema-v1 reliability before budgeting.

    First-rollout fallback can preserve the prior monolith when Buildkite is
    unavailable. Schema v1 repeats catalog metadata and derivable URLs in every
    observation, so carrying it through the configured bounded writer would otherwise
    invoke emergency history retention. Normalize those validated blocks to
    the schema-v2 reference form first and verify canonical hydration parity.
    """
    migrated_payload = dict(payload)
    diagnostics: dict[str, Any] = {}
    for slug in PIPELINES:
        block = migrated_payload.get(slug)
        if not isinstance(block, dict):
            continue
        reliability = block.get("all_main_reliability")
        if not isinstance(reliability, dict):
            continue
        source_schema_version = reliability.get("schema_version")
        if source_schema_version not in {None, 1}:
            continue
        source_is_valid = validate_all_main_reliability(reliability, slug)
        if not source_is_valid and source_schema_version is None:
            # Small synthetic/partial payloads historically omitted a schema
            # marker. They are not eligible for the lossless migration, but
            # the generic writer still supports and budgets them. A real
            # legacy ledger without a marker validates as schema v1 and takes
            # the migration path below.
            continue
        if not source_is_valid:
            raise _legacy_reliability_migration_error(
                slug,
                "source payload failed strict validation",
            )

        migrated_builds = []
        for build in reliability.get("builds") or []:
            migrated_build = dict(build)
            for field in ("commit", "message", "created_at"):
                migrated_build[field] = str(build.get(field) or "")
            message = migrated_build["message"]
            if len(message) > CATALOG_MESSAGE_MAX_CHARS:
                # Schema v2 deliberately bounds catalog messages. Silently
                # changing a preserved v1 title here would violate hydration
                # parity, so leave the last-known-good monolith untouched.
                raise _legacy_reliability_migration_error(
                    slug,
                    "catalog message exceeds the schema-v2 bound",
                )
            migrated_builds.append(migrated_build)

        migrated_groups = []
        observation_count = 0
        for group in reliability.get("groups") or []:
            migrated_observations = []
            for observation in group.get("observations") or []:
                compact_observation = {
                    key: value
                    for key, value in observation.items()
                    if key not in LEGACY_OBSERVATION_DERIVED_FIELDS
                }
                retry = compact_observation.get("retry_evidence")
                if isinstance(retry, dict) and "retried_in_job_url" in retry:
                    compact_retry = dict(retry)
                    compact_retry.pop("retried_in_job_url", None)
                    compact_observation["retry_evidence"] = compact_retry
                migrated_observations.append(compact_observation)
            observation_count += len(migrated_observations)
            migrated_groups.append({
                **group,
                "observations": migrated_observations,
            })

        provenance = dict(reliability.get("provenance") or {})
        provenance.update({
            "observation_schema": (
                "normalized build_number/job_id/step_id references; "
                "hydrate from builds catalog"
            ),
            "build_catalog_authoritative_fields": [
                "url", "commit", "message", "created_at",
            ],
            "build_message_max_chars": CATALOG_MESSAGE_MAX_CHARS,
            "migrated_from_schema_version": 1,
        })
        migrated_reliability = {
            **reliability,
            "schema_version": RELIABILITY_SCHEMA_VERSION,
            "provenance": provenance,
            "builds": migrated_builds,
            "groups": migrated_groups,
        }
        if not validate_all_main_reliability(migrated_reliability, slug):
            raise _legacy_reliability_migration_error(
                slug,
                "normalized payload failed schema-v2 validation",
            )

        # Compare one bounded group at a time. This avoids retaining a second
        # production-sized hydrated ledger while still proving every removed
        # value can be reconstructed exactly.
        for source_group, migrated_group in zip(
            reliability.get("groups") or [],
            migrated_groups,
            strict=True,
        ):
            source_hydrated = hydrate_reliability_observations(
                reliability,
                source_group.get("observations") or [],
                pipeline_slug=slug,
            )
            migrated_hydrated = hydrate_reliability_observations(
                migrated_reliability,
                migrated_group.get("observations") or [],
                pipeline_slug=slug,
            )
            if source_hydrated != migrated_hydrated:
                raise _legacy_reliability_migration_error(
                    slug,
                    "canonical observation hydration changed",
                )

        migrated_payload[slug] = {
            **block,
            "all_main_reliability": migrated_reliability,
        }
        diagnostics[slug] = {
            "source_schema_version": 1,
            "target_schema_version": RELIABILITY_SCHEMA_VERSION,
            "builds": len(migrated_builds),
            "groups": len(migrated_groups),
            "observations": observation_count,
            "hydration_parity": True,
        }
    return migrated_payload, diagnostics


def _bounded_private_analytics_payload(payload: dict) -> tuple[dict, dict[str, int]]:
    """Remove legacy copies and guard only pathological catalog messages."""
    bounded_payload = dict(payload)
    message_diagnostics = {
        "catalog_messages_truncated": 0,
        "catalog_message_chars_removed": 0,
    }
    for slug in PIPELINES:
        block = bounded_payload.get(slug)
        if not isinstance(block, dict):
            continue
        bounded_block = dict(block)
        # Also migrate a preserved, non-refreshed pipeline from the legacy
        # duplicate shape during a targeted collection.
        bounded_block.pop("main_builds", None)
        bounded_block.pop("main_builds_provenance", None)

        reliability = bounded_block.get("all_main_reliability")
        if isinstance(reliability, dict):
            bounded_reliability = dict(reliability)
            catalog = []
            catalog_changed = False
            for build in reliability.get("builds") or []:
                if not isinstance(build, dict):
                    catalog.append(build)
                    continue
                message = build.get("message")
                if isinstance(message, str) and len(message) > CATALOG_MESSAGE_MAX_CHARS:
                    bounded_build = dict(build)
                    bounded_build["message"] = _bounded_catalog_message(message)
                    build = bounded_build
                    catalog_changed = True
                    message_diagnostics["catalog_messages_truncated"] += 1
                    message_diagnostics["catalog_message_chars_removed"] += (
                        len(message) - CATALOG_MESSAGE_MAX_CHARS
                    )
                catalog.append(build)
            if catalog_changed:
                bounded_reliability["builds"] = catalog

            # Schema v2 stores the message once in the catalog. This legacy
            # guard keeps a targeted refresh of a v1 block bounded without
            # changing ordinary popup-visible messages.
            groups = []
            groups_changed = False
            for group in reliability.get("groups") or []:
                if not isinstance(group, dict):
                    groups.append(group)
                    continue
                observations = []
                observations_changed = False
                for observation in group.get("observations") or []:
                    if not isinstance(observation, dict):
                        observations.append(observation)
                        continue
                    message = observation.get("build_message")
                    if (
                        isinstance(message, str)
                        and len(message) > CATALOG_MESSAGE_MAX_CHARS
                    ):
                        observation = {
                            **observation,
                            "build_message": _bounded_catalog_message(message),
                        }
                        observations_changed = True
                        message_diagnostics["catalog_messages_truncated"] += 1
                        message_diagnostics["catalog_message_chars_removed"] += (
                            len(message) - CATALOG_MESSAGE_MAX_CHARS
                        )
                    observations.append(observation)
                if observations_changed:
                    group = {**group, "observations": observations}
                    groups_changed = True
                groups.append(group)
            if groups_changed:
                bounded_reliability["groups"] = groups

            if catalog_changed or groups_changed:
                bounded_block["all_main_reliability"] = bounded_reliability
        bounded_payload[slug] = bounded_block
    return bounded_payload, message_diagnostics


def _reliability_observation_counts(payload: dict) -> dict[str, int]:
    counts = {}
    for slug in PIPELINES:
        reliability = ((payload.get(slug) or {}).get("all_main_reliability") or {})
        counts[slug] = sum(
            len(group.get("observations") or [])
            for group in reliability.get("groups") or []
            if isinstance(group, dict)
        )
    return counts


def _reliability_observation_rank(row: Any) -> tuple[str, int, str]:
    if not isinstance(row, dict):
        return ("", 0, "")
    try:
        build_number = int(row.get("build_number") or 0)
    except (TypeError, ValueError):
        build_number = 0
    return (
        str(row.get("observed_at") or ""),
        build_number,
        str(row.get("job_id") or ""),
    )


def _cap_reliability_observations(payload: dict, limit: int) -> tuple[dict, int]:
    """Retain eligible evidence first, then newest excluded context per group."""
    bounded_payload = dict(payload)
    removed = 0
    for slug in PIPELINES:
        block = bounded_payload.get(slug)
        if not isinstance(block, dict):
            continue
        reliability = block.get("all_main_reliability")
        if not isinstance(reliability, dict):
            continue
        groups = reliability.get("groups")
        if not isinstance(groups, list):
            continue
        bounded_groups = []
        changed = False
        for group in groups:
            if not isinstance(group, dict):
                bounded_groups.append(group)
                continue
            observations = group.get("observations")
            if not isinstance(observations, list) or len(observations) <= limit:
                bounded_groups.append(group)
                continue
            newest_first = sorted(
                observations,
                key=_reliability_observation_rank,
                reverse=True,
            )
            retained = [
                row
                for row in newest_first
                if isinstance(row, dict)
                and row.get("eligible_for_reliability") is True
            ][:limit]
            if len(retained) < limit:
                retained.extend(
                    row
                    for row in newest_first
                    if not (
                        isinstance(row, dict)
                        and row.get("eligible_for_reliability") is True
                    )
                )
                retained = retained[:limit]
            # Preserve the established newest-first presentation order after
            # applying the eligible-first retention priority.
            retained.sort(key=_reliability_observation_rank, reverse=True)
            bounded_group = dict(group)
            bounded_group["observations"] = retained
            bounded_group["retained_observation_count"] = len(retained)
            bounded_group["retained_eligible_observation_count"] = sum(
                bool(row.get("eligible_for_reliability"))
                for row in retained
                if isinstance(row, dict)
            )
            bounded_group["observations_truncated"] = True
            bounded_groups.append(bounded_group)
            removed += len(observations) - len(retained)
            changed = True
        if changed:
            bounded_reliability = {**reliability, "groups": bounded_groups}
            bounded_payload[slug] = {
                **block,
                "all_main_reliability": bounded_reliability,
            }
    return bounded_payload, removed


def _analytics_component_bytes(payload: dict) -> dict[str, dict[str, Any]]:
    diagnostics = {}
    for slug, block in payload.items():
        if not isinstance(block, dict):
            diagnostics[str(slug)] = {"bytes": _compact_json_bytes(block)}
            continue
        diagnostics[str(slug)] = {
            "bytes": _compact_json_bytes(block),
            "components": {
                str(key): _compact_json_bytes(value)
                for key, value in block.items()
            },
        }
    return diagnostics


def _prepare_private_analytics(
    out_path: Path,
    payload: dict,
) -> tuple[dict, str, dict[str, Any]]:
    """Bound a candidate to the normal budget and return storage diagnostics."""
    migrated_payload, migration_diagnostics = _migrate_preserved_reliability_v1(
        payload
    )
    bounded_payload, message_diagnostics = _bounded_private_analytics_payload(
        migrated_payload
    )
    original_observations = _reliability_observation_counts(bounded_payload)
    original_component_bytes = _analytics_component_bytes(bounded_payload)
    original_serialized_bytes = _compact_json_bytes(bounded_payload) + 1
    effective_target = min(
        PRIVATE_ANALYTICS_TARGET_BYTES,
        PRIVATE_ANALYTICS_MAX_BYTES,
    )
    applied_observation_cap = None
    observations_removed = 0
    serialized_bytes = original_serialized_bytes
    for observation_cap in PRIVATE_ANALYTICS_OBSERVATION_CAPS:
        if serialized_bytes <= effective_target:
            break
        candidate, removed = _cap_reliability_observations(
            bounded_payload,
            observation_cap,
        )
        if not removed:
            continue
        bounded_payload = candidate
        observations_removed += removed
        applied_observation_cap = observation_cap
        serialized_bytes = _compact_json_bytes(bounded_payload) + 1

    serialized = _compact_json(bounded_payload) + "\n"
    serialized_bytes = len(serialized.encode("utf-8"))
    previous_bytes = out_path.stat().st_size if out_path.exists() else 0
    retained_observations = _reliability_observation_counts(bounded_payload)
    diagnostics: dict[str, Any] = {
        "collector": "ci_analytics",
        "artifact": str(out_path),
        "serialized_bytes": serialized_bytes,
        "original_serialized_bytes": original_serialized_bytes,
        "previous_bytes": previous_bytes,
        "delta_bytes": serialized_bytes - previous_bytes,
        "target_bytes": PRIVATE_ANALYTICS_TARGET_BYTES,
        "effective_target_bytes": effective_target,
        "max_bytes": PRIVATE_ANALYTICS_MAX_BYTES,
        "github_blob_limit_bytes": GITHUB_BLOB_MAX_BYTES,
        "original_component_bytes": original_component_bytes,
        "component_bytes": _analytics_component_bytes(bounded_payload),
        "original_observations": original_observations,
        "retained_observations": retained_observations,
        "observations_removed": observations_removed,
        "applied_observation_cap": applied_observation_cap,
        "legacy_reliability_migrations": migration_diagnostics,
        **message_diagnostics,
    }
    if serialized_bytes > effective_target:
        failure_diagnostics = {
            **diagnostics,
            "reason_class": "payload-budget",
        }
        log.error(
            "Private analytics storage budget exceeded: %s",
            _compact_json(failure_diagnostics),
        )
        raise IncompleteAnalyticsCollection(
            "Private analytics payload exceeds the configured normal operating "
            f"budget ({effective_target} bytes) after deterministic compaction: "
            f"{serialized_bytes} > {effective_target} bytes",
            failure_diagnostics,
        )
    if serialized_bytes > PRIVATE_ANALYTICS_MAX_BYTES:
        # This should be unreachable because the effective target is never
        # larger than the compatibility ceiling. Keep it explicit so a future
        # configuration error still fails closed below GitHub's hard limit.
        failure_diagnostics = {
            **diagnostics,
            "reason_class": "payload-budget",
        }
        log.error(
            "Private analytics hard ceiling exceeded: %s",
            _compact_json(failure_diagnostics),
        )
        raise IncompleteAnalyticsCollection(
            "Private analytics payload exceeds the safe GitHub blob budget: "
            f"{serialized_bytes} > {PRIVATE_ANALYTICS_MAX_BYTES} bytes",
            failure_diagnostics,
        )
    return bounded_payload, serialized, diagnostics


def write_analytics(out_path: Path, payload: dict) -> dict[str, Any]:
    """Atomically replace the monolith only after it fits the normal budget."""
    out_path = Path(out_path)
    _, serialized, diagnostics = _prepare_private_analytics(
        out_path,
        payload,
    )
    log.info(
        "Private analytics storage: %s",
        _compact_json(diagnostics),
    )
    _atomic_write_text(out_path, serialized)
    return diagnostics


def main():
    parser = argparse.ArgumentParser(description="Collect CI analytics for rich dashboard")
    parser.add_argument("--days", type=int, default=90, help="Days of history (default: 90)")
    parser.add_argument("--pipeline", choices=["amd-ci", "ci", "both"], default="both")
    parser.add_argument("--output", type=str, default=str(OUTPUT))
    parser.add_argument(
        "--github-output",
        type=Path,
        help="append the private-cache save decision to this GitHub output file",
    )
    args = parser.parse_args()

    token = os.getenv("BUILDKITE_TOKEN")
    if not token:
        log.warning("BUILDKITE_TOKEN not set; using parsed test_results and previous metadata only")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    previous_data = {}
    previous_path = output / "analytics.json"
    if previous_path.exists():
        try:
            previous_data = json.loads(previous_path.read_text())
        except json.JSONDecodeError:
            log.warning("Ignoring malformed previous analytics at %s", previous_path)

    pipelines = ["amd-ci", "ci"] if args.pipeline == "both" else [args.pipeline]
    # A targeted refresh must not erase the other pipeline's analytics and
    # reliability history.
    all_data = {
        slug: block
        for slug, block in previous_data.items()
        if slug not in pipelines and isinstance(block, dict)
    }
    ref_now = datetime.now(timezone.utc)
    generated_at = ref_now.strftime("%Y-%m-%dT%H:%M:%SZ")
    cache_dir = output / ".cache" / CACHE_DIR_NAME
    analytics_cache_save = bool(token)

    for slug in pipelines:
        log.info("=== %s ===", PIPELINES.get(slug, slug))

        # Fetch branch=main once. Nightly regression streams remain pipeline
        # specific; strict test-group reliability is published for both pipelines.
        # Upstream CI remains the only source for flake and retry analysis.
        previous_pipeline_data = previous_data.get(slug) or {}
        previous_builds = previous_pipeline_data.get("builds") or []
        previous_all_main = previous_pipeline_data.get("all_main_reliability")
        previous_retry = previous_pipeline_data.get("main_retry_analysis")
        raw_builds = []
        collection_provenance = {}
        if token:
            context_token = _FETCH_CONTEXT.set((cache_dir, ref_now))
            try:
                # Keep this historical three-positional-argument call intact:
                # tests and sibling collectors monkeypatch it directly.
                raw_builds, collection_provenance = fetch_pipeline_builds(
                    slug,
                    token,
                    args.days,
                )
            finally:
                _FETCH_CONTEXT.reset(context_token)
        cache_diagnostics = collection_provenance.get("cache")
        if (
            not isinstance(cache_diagnostics, dict)
            or cache_diagnostics.get("cache_written") is not True
        ):
            analytics_cache_save = False
        reliability_raw_builds = _reliability_builds_with_cache_aliases(
            raw_builds,
            slug,
            previous_all_main,
        )
        buildkite_builds = (
            collect_pipeline(
                slug,
                token,
                args.days,
                nightly_only=True,
                name_pattern=NIGHTLY_NAME_PATTERNS_BY_SLUG.get(slug),
                builds_raw=reliability_raw_builds,
            )
            if token
            else []
        )
        result_builds = load_test_result_builds(
            output,
            slug,
            args.days,
            buildkite_builds,
            previous_builds,
            now=ref_now,
        )
        builds = choose_analytics_builds(buildkite_builds, result_builds, previous_builds, slug)
        job_rankings = compute_job_rankings(builds)
        windows = compute_window_blocks(builds, args.days, now=ref_now)
        default_window_days = min(DEFAULT_ANALYTICS_WINDOW_DAYS, args.days)
        default_window_key = f"{default_window_days}d"
        if default_window_key not in windows:
            default_window_key = sorted(windows.keys(), key=lambda k: int(k[:-1]))[-1]

        daily = compute_daily_stats(builds)
        queues = compute_queue_stats(job_rankings)

        # Sort rankings
        failure_ranking = sorted(job_rankings, key=lambda x: x["fail_rate"], reverse=True)
        duration_ranking = sorted(job_rankings, key=lambda x: x.get("median_dur") or 0, reverse=True)

        nightly_change_history = compute_nightly_change_history(
            builds,
            pipeline_slug=slug,
        )
        all_data[slug] = {
            "pipeline": slug,
            "display_name": PIPELINES.get(slug, slug),
            "days": args.days,
            "generated_at": generated_at,
            "pass_rate_contract_version": PASS_RATE_CONTRACT_VERSION,
            "transition_policy_id": INCIDENT_TRANSITION_POLICY_ID,
            "cohort": {
                "name": "canonical message-matched nightlies",
                "pipeline": slug,
                "branch": "main",
                "window_days": args.days,
                "build_count": len(builds),
                "name_pattern": NIGHTLY_NAME_PATTERNS_BY_SLUG.get(slug) or "",
            },
            "transition_basis": (
                "oldest-to-newest confirmed-incident replay: hard failures confirm "
                "immediately; soft failures require two distinct eligible completed "
                "builds; a current observed pass resolves; absence and indeterminate "
                "observations hold state"
            ),
            "nightly_change_history": nightly_change_history,
            "summary": compute_summary(builds, job_rankings),
            "daily_stats": daily,
            "builds": builds[:ANALYTICS_BUILD_LIMIT],  # Long enough for 3-month trend views
            "nightly_builds": [chart_build_summary(build) for build in builds[:ANALYTICS_NIGHTLY_LIMIT]],
            "failure_ranking": [j for j in failure_ranking if j["failed"] > 0 or j["soft_failed"] > 0],
            "duration_ranking": duration_ranking,
            "queue_stats": queues,
            "default_window": default_window_key,
            "windows": windows,
        }
        preserved_retry_analysis = None
        all_main_reliability = None
        complete_retry_builds = None
        if token and collection_provenance.get("exhaustive") is True:
            all_main_reliability = build_all_main_reliability(
                reliability_raw_builds,
                pipeline_slug=slug,
                window_days=args.days,
                generated_at=generated_at,
                nightly_pattern=NIGHTLY_NAME_PATTERNS_BY_SLUG.get(slug) or "",
                test_result_builds=result_builds,
                observation_limit=(
                    AMD_MAIN_OBSERVATION_LIMIT
                    if slug == "amd-ci"
                    else OBSERVATION_LIMIT
                ),
                collection_provenance=collection_provenance,
            )
            if slug == "ci":
                complete_retry_builds = summarize_pipeline_builds(
                    slug,
                    filter_reliability_builds(reliability_raw_builds),
                )
        elif validate_all_main_reliability(previous_all_main, slug):
            reason = (
                "Buildkite pagination was incomplete"
                if token
                else "BUILDKITE_TOKEN is unavailable"
            )
            log.warning("  preserving previous %s all-main reliability: %s", slug, reason)
            all_main_reliability = previous_all_main
            if slug == "ci":
                preserved_retry_analysis = previous_retry
        else:
            log.error("  strict %s all-main reliability is unavailable; refusing fallback data", slug)
        if all_main_reliability:
            if slug == "ci":
                attach_main_reliability(
                    all_data[slug],
                    all_main_reliability,
                    retry_builds=complete_retry_builds,
                    retry_analysis=preserved_retry_analysis,
                )
            else:
                # AMD reliability powers the live main-failure automation. Keep
                # upstream-only retry semantics out of the AMD block.
                all_data[slug]["all_main_reliability"] = all_main_reliability

        log.info("  %d builds, %d jobs tracked, %d with failures",
                 len(builds), len(job_rankings),
                 sum(1 for j in job_rankings if j["failed"] > 0))

    # Write output
    out_path = output / "analytics.json"
    # CI Health consumes this slim artifact independently. Publish it first so
    # an analytics storage-budget failure cannot withhold fresh gating data.
    write_gating_nightlies(output, all_data, generated_at)
    write_analytics(out_path, all_data)
    log.info("Wrote %s", out_path)
    if args.github_output:
        _append_cache_save_output(
            args.github_output,
            enabled=analytics_cache_save,
        )

    # Print summary
    for slug, d in all_data.items():
        s = d["summary"]
        print(f"\n{d['display_name']}: {s['total_builds']} builds "
              f"({s['terminal_builds']} terminal), {s['build_pass_rate_pct']}% "
              "build pass rate (terminal state all-green), "
              f"{s['jobs_with_failures']} jobs with failures, {s['total_jobs_tracked']} jobs tracked")


if __name__ == "__main__":
    main()
