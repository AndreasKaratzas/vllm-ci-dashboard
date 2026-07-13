"""Compact, provenance-bearing reliability history for Buildkite main builds.

This module is intentionally collector-only.  It turns Buildkite build/job
payloads into a bounded static-site dataset without treating mixed outcomes as
retry evidence or combining hardware, queue, GPU-count, or shard variants.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any
from urllib.parse import parse_qs, urlparse

from vllm.ci.utils import duration_mins, hardware_from_job_name, percentile, queue_from_rules
from vllm.constants import BK_ORG, is_excluded_queue
from vllm.pipelines import SKIP_JOB_PATTERNS


SCHEMA_VERSION = 1
OBSERVATION_LIMIT = 60
BUILD_FETCH_PAGE_SIZE = 100
BUILD_FETCH_MAX_PAGES = 50
TRUSTWORTHY_BUILD_STATES = frozenset({"passed", "failed"})
ELIGIBLE_RESULTS = ("passed", "failed", "soft_fail")
FAILED_JOB_STATES = frozenset({"failed", "timed_out", "broken"})
KNOWN_TERMINAL_JOB_STATES = frozenset({
    "passed", "failed", "timed_out", "broken", "canceled", "cancelled",
    "expired", "skipped", "blocked", "soft_fail", "soft_failed",
})
RETRY_FIELDS = (
    "retried", "retried_in_job_id", "retries_count", "retry_source", "retry_type",
)

_HW_PREFIX_RE = re.compile(r"^(mi\d+[a-z]?_\d+|gpu_\d+|amd_\w+):\s*", re.IGNORECASE)
_UPSTREAM_HW_RE = re.compile(
    r"(?<![a-z0-9])(h100|h200|b100|b200|a100|l4|t4|cpu|npu|tpu)(?![a-z0-9])",
    re.IGNORECASE,
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _build_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _build_url(pipeline_slug: str, build: dict) -> str:
    number = _build_number(build.get("number"))
    return f"https://buildkite.com/{BK_ORG}/{pipeline_slug}/builds/{number}" if number else ""


def buildkite_build_url_matches(value: Any, pipeline_slug: str, build_number: Any = None) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    number = _build_number(build_number)
    prefix = f"/{BK_ORG}/{pipeline_slug}/builds/"
    if parsed.scheme != "https" or parsed.netloc != "buildkite.com" or not parsed.path.startswith(prefix):
        return False
    suffix = parsed.path[len(prefix):].strip("/")
    if not suffix.isdigit() or "/" in suffix:
        return False
    return number is None or int(suffix) == number


def buildkite_job_url_matches(value: Any, pipeline_slug: str, build_number: Any = None) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    number = _build_number(build_number)
    prefix = f"/{BK_ORG}/{pipeline_slug}/builds/"
    if parsed.scheme != "https" or parsed.netloc != "buildkite.com" or not parsed.path.startswith(prefix):
        return False
    suffix = parsed.path[len(prefix):].strip("/")
    parts = suffix.split("/")
    if len(parts) < 3 or not parts[0].isdigit() or parts[1] != "steps":
        return False
    if number is not None and int(parts[0]) != number:
        return False
    if parts[2] == "canvas":
        query = parse_qs(parsed.query)
        return bool(query.get("jid") or query.get("sid"))
    return bool(parts[2])


def validate_all_main_reliability(
    payload: Any,
    pipeline_slug: str,
    *,
    require_exhaustive: bool = True,
) -> bool:
    if not isinstance(payload, dict):
        return False
    cohort = payload.get("cohort")
    provenance = payload.get("provenance")
    builds = payload.get("builds")
    groups = payload.get("groups")
    if not all(isinstance(value, dict) for value in (cohort, provenance)):
        return False
    if not isinstance(builds, list) or not isinstance(groups, list):
        return False
    build_states = cohort.get("build_states")
    build_count = cohort.get("build_count")
    collection = provenance.get("collection")
    query = provenance.get("query")
    if (
        not isinstance(build_states, list)
        or any(not isinstance(state, str) for state in build_states)
        or set(build_states) != set(TRUSTWORTHY_BUILD_STATES)
        or not isinstance(build_count, int)
        or isinstance(build_count, bool)
        or build_count != len(builds)
        or cohort.get("id") != f"{pipeline_slug}-main-completed-pass-fail"
        or cohort.get("pipeline") != pipeline_slug
        or cohort.get("branch") != "main"
        or not isinstance(query, dict)
        or query.get("branch") != "main"
        or provenance.get("pipeline") != pipeline_slug
        or not str(provenance.get("endpoint") or "").endswith(f"/pipelines/{pipeline_slug}/builds")
        or not isinstance(collection, dict)
        or (require_exhaustive and collection.get("exhaustive") is not True)
        or (require_exhaustive and cohort.get("exhaustive") is not True)
    ):
        return False
    build_numbers: set[int] = set()
    for build in builds:
        if not isinstance(build, dict):
            return False
        number = _build_number(build.get("number"))
        if (
            number is None
            or build.get("branch") != "main"
            or str(build.get("state") or "").lower() not in TRUSTWORTHY_BUILD_STATES
            or not build.get("finished_at")
            or not buildkite_build_url_matches(build.get("url"), pipeline_slug, number)
        ):
            return False
        build_numbers.add(number)
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("observations"), list):
            return False
        for field in (
            "denominator", "passed", "failed", "soft_failed",
            "excluded_observations", "retry_evidence_observations",
        ):
            value = group.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return False
        if not isinstance(group.get("duration"), dict):
            return False
        for observation in group["observations"]:
            if not isinstance(observation, dict):
                return False
            number = _build_number(observation.get("build_number"))
            if (
                number not in build_numbers
                or observation.get("source_pipeline") != pipeline_slug
                or not buildkite_build_url_matches(observation.get("build_url"), pipeline_slug, number)
                or not buildkite_job_url_matches(observation.get("job_url"), pipeline_slug, number)
                or (
                    observation.get("step_url")
                    and not buildkite_job_url_matches(observation.get("step_url"), pipeline_slug, number)
                )
            ):
                return False
    return True


def filter_reliability_builds(builds: list[dict]) -> list[dict]:
    scoped = []
    for build in builds:
        if not _trusted_main_build(build):
            continue
        jobs = [
            job
            for job in build.get("jobs") or []
            if _is_test_job(job)
            and _is_terminal_job(job)
            and not is_excluded_queue(_queue(job))
        ]
        scoped.append({**build, "jobs": jobs})
    return scoped


def _job_urls(pipeline_slug: str, build: dict, job: dict) -> tuple[str, str]:
    """Return exact attempt and expanded-step URLs when identifiers exist."""
    base = _build_url(pipeline_slug, build)
    job_id = str(job.get("id") or job.get("job_id") or "")
    step_id = str((job.get("step") or {}).get("id") or job.get("step_id") or "")
    if base and job_id:
        attempt_url = f"{base}/steps/canvas?jid={job_id}&tab=output"
    else:
        attempt_url = str(job.get("web_url") or "")
    step_url = f"{base}/steps/canvas?sid={step_id}&tab=output" if base and step_id else ""
    if not attempt_url:
        attempt_url = step_url or base
    return attempt_url, step_url


def _canonical_label(raw_label: str) -> str:
    """Remove only the agent prefix; retain GPU counts and shard suffixes."""
    return re.sub(r"\s+", " ", _HW_PREFIX_RE.sub("", raw_label or "")).strip() or raw_label or "unknown"


def _step_key(job: dict) -> str:
    return str(job.get("step_key") or (job.get("step") or {}).get("key") or "")


def _queue(job: dict) -> str:
    return str(job.get("q") or queue_from_rules(job.get("agent_query_rules")) or "")


def _hardware(job_name: str, queue: str, pipeline_slug: str) -> str:
    if pipeline_slug != "ci":
        return hardware_from_job_name(job_name, queue)
    match = _UPSTREAM_HW_RE.search(job_name or "")
    if match:
        return match.group(1).lower()
    normalized_queue = (queue or "").lower()
    for token in ("h100", "h200", "b100", "b200", "a100", "l4", "t4"):
        if token in normalized_queue:
            return token
    if normalized_queue.startswith("gpu_") or "gpu" in normalized_queue:
        return "gpu"
    if "cpu" in normalized_queue:
        return "cpu"
    return "unknown"


def _identity(job: dict, pipeline_slug: str = "amd-ci") -> dict[str, str]:
    raw_label = str(job.get("raw_name") or job.get("name") or "unknown").strip()
    queue = _queue(job)
    return {
        "raw_label": raw_label,
        "canonical_label": _canonical_label(raw_label),
        "step_key": _step_key(job),
        "hardware": _hardware(raw_label, queue, pipeline_slug),
        "queue": queue,
    }


def _group_id(identity: dict[str, str]) -> str:
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:20]


def _is_test_job(job: dict) -> bool:
    if job.get("type") != "script":
        return False
    name = str(job.get("name") or "").lower()
    return not any(pattern in name for pattern in SKIP_JOB_PATTERNS)


def _is_terminal_job(job: dict) -> bool:
    state = str(job.get("state") or "").lower()
    return state in KNOWN_TERMINAL_JOB_STATES or bool(job.get("finished_at"))


def _result(job: dict) -> tuple[str, bool, str]:
    state = str(job.get("state") or "unknown").lower()
    soft_failed = bool(job.get("soft_failed")) or state in {"soft_fail", "soft_failed"}
    if soft_failed:
        return "soft_fail", True, ""
    if state == "passed":
        return "passed", True, ""
    if state in FAILED_JOB_STATES:
        return "failed", True, ""
    return "excluded", False, state or "unknown"


def _retry_evidence(job: dict) -> dict:
    evidence = {key: job.get(key) for key in RETRY_FIELDS if job.get(key) not in (None, "", False, 0, "0")}
    job_id = str(job.get("id") or job.get("job_id") or "")
    if evidence and job_id:
        evidence["job_id"] = job_id
    return evidence


def _test_duration_index(
    test_result_builds: list[dict] | None,
) -> dict[tuple[int, str, str], float]:
    index: dict[tuple[int, str, str], float] = {}
    step_attempts: dict[tuple[int, str, str], dict[str, float]] = defaultdict(dict)
    for build in test_result_builds or []:
        number = int(build.get("number") or build.get("build_number") or 0)
        for position, job in enumerate(build.get("jobs") or []):
            duration = job.get("test_duration_mins")
            if not isinstance(duration, (int, float)):
                continue
            job_id = str(job.get("job_id") or "")
            step_id = str(job.get("step_id") or "")
            if job_id:
                index[(number, "job_id", job_id)] = float(duration)
            if step_id:
                # A Buildkite step ID is shared by retry attempts. Use it only
                # when the parsed result identifies one unambiguous attempt.
                attempt_key = job_id or f"anonymous:{position}"
                step_attempts[(number, "step_id", step_id)][attempt_key] = float(duration)
    for key, attempts in step_attempts.items():
        if len(attempts) == 1:
            index[key] = next(iter(attempts.values()))
    return index


def _lookup_test_duration(index: dict[tuple[int, str, str], float], build_number: int, job: dict) -> float | None:
    job_id = job.get("id") or job.get("job_id")
    if job_id:
        value = index.get((build_number, "job_id", str(job_id)))
        return round(value, 1) if value is not None else None
    step_id = (job.get("step") or {}).get("id") or job.get("step_id")
    if step_id:
        value = index.get((build_number, "step_id", str(step_id)))
        return round(value, 1) if value is not None else None
    return None


def _duration_summary(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "p50_mins": None, "p90_mins": None, "max_mins": None}
    return {
        "samples": len(ordered),
        "p50_mins": round(median(ordered), 1),
        "p90_mins": round(percentile(ordered, 90), 1),
        "max_mins": round(max(ordered), 1),
    }


def _trusted_main_build(build: dict) -> bool:
    return (
        str(build.get("branch") or "") == "main"
        and str(build.get("state") or "").lower() in TRUSTWORTHY_BUILD_STATES
        and bool(build.get("finished_at"))
        and bool(build.get("number"))
    )


def _trusted_build_rank(build: dict) -> tuple:
    """Select one stable, most-complete row if paginated input overlaps."""
    return (
        str(build.get("finished_at") or ""),
        str(build.get("created_at") or ""),
        len(build.get("jobs") or []),
        str(build.get("commit") or ""),
        str(build.get("message") or ""),
        str(build.get("web_url") or ""),
    )


def _observation(pipeline_slug: str, build: dict, job: dict, test_durations: dict) -> dict:
    number = int(build.get("number") or 0)
    state = str(job.get("state") or "unknown").lower()
    result, eligible, exclusion_reason = _result(job)
    started_at = str(job.get("started_at") or "")
    finished_at = str(job.get("finished_at") or "")
    runnable_at = str(job.get("runnable_at") or "")
    wall = duration_mins(started_at, finished_at)
    wait = duration_mins(runnable_at, started_at)
    e2e = duration_mins(runnable_at, finished_at)
    attempt_url, step_url = _job_urls(pipeline_slug, build, job)
    row = {
        "source_pipeline": pipeline_slug,
        "build_number": number,
        "build_url": _build_url(pipeline_slug, build),
        "build_commit": str(build.get("commit") or ""),
        "build_message": str(build.get("message") or ""),
        "build_created_at": str(build.get("created_at") or ""),
        "job_id": str(job.get("id") or job.get("job_id") or ""),
        "step_id": str((job.get("step") or {}).get("id") or job.get("step_id") or ""),
        "job_url": attempt_url,
        "step_url": step_url,
        "observed_at": finished_at or started_at or str(build.get("finished_at") or build.get("created_at") or ""),
        "started_at": started_at,
        "finished_at": finished_at,
        "runnable_at": runnable_at,
        "terminal_state": state,
        "result": result,
        "eligible_for_reliability": eligible,
        "soft_failed": bool(job.get("soft_failed")) or result == "soft_fail",
        "wall_completion_mins": round(wall, 1) if wall is not None else None,
        "test_duration_mins": _lookup_test_duration(test_durations, number, job),
        "queue_wait_mins": round(wait, 1) if wait is not None else None,
        "end_to_end_mins": round(e2e, 1) if e2e is not None else None,
    }
    if exclusion_reason:
        row["exclusion_reason"] = exclusion_reason
    for key in ("tests", "passed_tests", "failed_tests", "skipped_tests"):
        if isinstance(job.get(key), (int, float)) and not isinstance(job.get(key), bool):
            row[key] = job[key]
    retry = _retry_evidence(job)
    if retry:
        retried_in = str(retry.get("retried_in_job_id") or "")
        build_url = _build_url(pipeline_slug, build)
        if retried_in and build_url:
            retry["retried_in_job_url"] = f"{build_url}/steps/canvas?jid={retried_in}&tab=output"
        row["retry_evidence"] = retry
    return row


def build_all_main_reliability(
    builds: list[dict],
    *,
    pipeline_slug: str = "amd-ci",
    window_days: int,
    generated_at: str | None = None,
    nightly_pattern: str = "",
    test_result_builds: list[dict] | None = None,
    observation_limit: int = OBSERVATION_LIMIT,
    collection_provenance: dict | None = None,
) -> dict:
    """Build an all-main attempt catalog separate from canonical nightlies."""
    generated_at = generated_at or _iso_now()
    nightly_re = re.compile(nightly_pattern, re.IGNORECASE) if nightly_pattern else None
    trusted_by_number: dict[int, dict] = {}
    for build in builds:
        if not _trusted_main_build(build):
            continue
        number = int(build["number"])
        existing = trusted_by_number.get(number)
        if existing is None or _trusted_build_rank(build) > _trusted_build_rank(existing):
            trusted_by_number[number] = build
    trusted = sorted(
        trusted_by_number.values(),
        key=lambda build: (str(build.get("created_at") or ""), int(build.get("number") or 0)),
        reverse=True,
    )
    test_durations = _test_duration_index(test_result_builds)
    build_catalog = []
    grouped: dict[str, dict] = {}
    excluded = Counter()
    excluded_queue_observations = 0
    eligible_total = 0
    retry_observations = 0

    for build in trusted:
        message = str(build.get("message") or "")
        is_nightly = bool(nightly_re and nightly_re.search(message))
        build_catalog.append({
            "number": int(build.get("number") or 0),
            "url": _build_url(pipeline_slug, build),
            "commit": str(build.get("commit") or ""),
            "message": message,
            "branch": "main",
            "state": str(build.get("state") or "unknown").lower(),
            "created_at": str(build.get("created_at") or ""),
            "started_at": str(build.get("started_at") or ""),
            "finished_at": str(build.get("finished_at") or ""),
            "is_canonical_nightly": is_nightly,
        })
        seen_jobs: set[str] = set()
        for job in build.get("jobs") or []:
            if not _is_test_job(job) or not _is_terminal_job(job):
                continue
            if is_excluded_queue(_queue(job)):
                excluded_queue_observations += 1
                continue
            job_id = str(job.get("id") or job.get("job_id") or "")
            dedupe_key = job_id or json.dumps(
                _identity(job, pipeline_slug), sort_keys=True
            ) + "|" + str(job.get("finished_at") or "")
            if dedupe_key in seen_jobs:
                continue
            seen_jobs.add(dedupe_key)
            identity = _identity(job, pipeline_slug)
            group_id = _group_id(identity)
            group = grouped.setdefault(group_id, {
                "group_id": group_id,
                "identity": identity,
                "observations": [],
                "result_counts": Counter(),
                "excluded_counts": Counter(),
                "durations": defaultdict(list),
                "retry_evidence_observations": 0,
            })
            observation = _observation(pipeline_slug, build, job, test_durations)
            group["observations"].append(observation)
            if observation["eligible_for_reliability"]:
                group["result_counts"][observation["result"]] += 1
                eligible_total += 1
            else:
                reason = observation.get("exclusion_reason") or observation["terminal_state"] or "unknown"
                group["excluded_counts"][reason] += 1
                excluded[reason] += 1
            if observation.get("retry_evidence"):
                group["retry_evidence_observations"] += 1
                retry_observations += 1
            for key in ("wall_completion_mins", "test_duration_mins", "queue_wait_mins", "end_to_end_mins"):
                if isinstance(observation.get(key), (int, float)):
                    group["durations"][key].append(float(observation[key]))

    groups = []
    for group in grouped.values():
        observations = sorted(
            group["observations"],
            key=lambda row: (str(row.get("observed_at") or ""), int(row.get("build_number") or 0), str(row.get("job_id") or "")),
            reverse=True,
        )
        counts = group["result_counts"]
        denominator = sum(counts.values())
        incidents = counts["failed"] + counts["soft_fail"]
        identity = group["identity"]
        limit = max(1, int(observation_limit))
        eligible_observations = [row for row in observations if row["eligible_for_reliability"]]
        retained = eligible_observations[:limit]
        if len(retained) < limit:
            excluded_observations = [row for row in observations if not row["eligible_for_reliability"]]
            retained.extend(excluded_observations[:limit - len(retained)])
            retained.sort(
                key=lambda row: (
                    str(row.get("observed_at") or ""),
                    int(row.get("build_number") or 0),
                    str(row.get("job_id") or ""),
                ),
                reverse=True,
            )
        groups.append({
            "group_id": group["group_id"],
            "name": identity["canonical_label"],
            "raw_name": identity["raw_label"],
            "step_key": identity["step_key"],
            "hardware": identity["hardware"],
            "queue": identity["queue"],
            "denominator": denominator,
            "passed": counts["passed"],
            "failed": counts["failed"],
            "soft_failed": counts["soft_fail"],
            "incident_rate": round(incidents / denominator * 100, 1) if denominator else None,
            "excluded_observations": sum(group["excluded_counts"].values()),
            "excluded_by_state": dict(sorted(group["excluded_counts"].items())),
            "retry_evidence_observations": group["retry_evidence_observations"],
            "duration": {
                "wall_completion": _duration_summary(group["durations"]["wall_completion_mins"]),
                "test_reported": _duration_summary(group["durations"]["test_duration_mins"]),
                "queue_wait": _duration_summary(group["durations"]["queue_wait_mins"]),
                "end_to_end": _duration_summary(group["durations"]["end_to_end_mins"]),
            },
            "observation_count": len(observations),
            "retained_observation_count": len(retained),
            "retained_eligible_observation_count": sum(
                bool(row["eligible_for_reliability"]) for row in retained
            ),
            "observations_truncated": len(observations) > len(retained),
            "observations": retained,
        })
    groups.sort(key=lambda row: (
        str(row["name"]).lower(), str(row["raw_name"]).lower(), row["step_key"], row["hardware"], row["queue"], row["group_id"],
    ))

    observed_times = [str(build.get("created_at") or "") for build in trusted if build.get("created_at")]
    requested_from = ""
    generated_dt = _parse_iso(generated_at)
    if generated_dt:
        requested_from = (generated_dt - timedelta(days=window_days)).isoformat().replace("+00:00", "Z")
    collection = dict(collection_provenance or {})
    collection.setdefault("created_from", requested_from)
    collection.setdefault("exhaustive", True)
    collection.setdefault("termination_reason", "provided_builds")
    nightly_count = sum(bool(build["is_canonical_nightly"]) for build in build_catalog)
    eligible_groups = sum(bool(group["denominator"]) for group in groups)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "cohort": {
            "id": f"{pipeline_slug}-main-completed-pass-fail",
            "name": f"{pipeline_slug} branch=main builds with state passed or failed and finished_at",
            "pipeline": pipeline_slug,
            "branch": "main",
            "window_days": window_days,
            "requested_from": requested_from,
            "observed_from": min(observed_times, default=""),
            "observed_to": max(observed_times, default=""),
            "build_states": sorted(TRUSTWORTHY_BUILD_STATES),
            "build_count": len(build_catalog),
            "canonical_nightly_build_count": nightly_count,
            "non_nightly_main_build_count": len(build_catalog) - nightly_count,
            "includes_canonical_nightlies": True,
            "exhaustive": collection["exhaustive"] is True,
            "selection": "branch=main, build state in [failed, passed], and finished_at present",
        },
        "denominator": {
            "unit": "terminal job attempts with passed, failed, or soft-fail outcomes",
            "eligible_results": list(ELIGIBLE_RESULTS),
            "eligible_observations": eligible_total,
            "excluded_observations": sum(excluded.values()),
            "excluded_by_state": dict(sorted(excluded.items())),
            "groups": eligible_groups,
            "catalog_groups": len(groups),
            "excluded_only_groups": len(groups) - eligible_groups,
            "out_of_scope_queue_observations": excluded_queue_observations,
        },
        "provenance": {
            "provider": "Buildkite REST API",
            "organization": BK_ORG,
            "pipeline": pipeline_slug,
            "endpoint": f"/organizations/{BK_ORG}/pipelines/{pipeline_slug}/builds",
            "query": {
                "branch": "main",
                "created_from": collection.get("created_from") or requested_from,
                "include_retried_jobs": True,
            },
            "collection": collection,
            "pagination": {
                "page_size": BUILD_FETCH_PAGE_SIZE,
                "max_pages": BUILD_FETCH_MAX_PAGES,
                "pages_fetched": collection.get("pages_fetched"),
                "termination_reason": collection.get("termination_reason"),
                "exhaustive": collection.get("exhaustive") is True,
                "stop_conditions": ["empty page", "short page", "page adds no build numbers"],
            },
            "build_state_source": "Buildkite build state",
            "job_result_source": "Buildkite terminal job state and soft_failed flag",
            "wall_completion_source": "job started_at to finished_at",
            "test_duration_source": "parsed test-result logs when exact job ID or unique step ID matches",
            "queue_wait_source": "job runnable_at to started_at",
            "end_to_end_source": "job runnable_at to finished_at",
            "retry_source": "explicit Buildkite retry fields only",
            "queue_scope_source": "vllm.constants.is_excluded_queue",
            "queue_scope_policy": "excluded queues are removed before group and denominator accounting",
            "observation_limit_per_group": max(1, int(observation_limit)),
            "observation_retention": (
                "newest eligible reliability observations first, then newest excluded observations"
            ),
        },
        "summary": {
            "builds": len(build_catalog),
            "groups": eligible_groups,
            "catalog_groups": len(groups),
            "eligible_observations": eligible_total,
            "retry_evidence_observations": retry_observations,
            "out_of_scope_queue_observations": excluded_queue_observations,
        },
        "builds": build_catalog,
        "groups": groups,
    }


def compact_main_builds(reliability: dict) -> list[dict]:
    """Adapt bounded evidence to the normalized build shape used downstream.

    The authoritative denominator remains in ``all_main_reliability``. This
    compatibility stream contains every cohort build, but only the retained
    (at most 60 per group by default) eligible observations, avoiding a second
    unbounded copy of the Buildkite payload in the static analytics artifact.
    """
    builds: dict[int, dict] = {}
    for source in reliability.get("builds") or []:
        number = int(source.get("number") or 0)
        if not number:
            continue
        builds[number] = {
            "number": number,
            "state": source.get("state") or "unknown",
            "created_at": source.get("created_at") or "",
            "started_at": source.get("started_at") or "",
            "finished_at": source.get("finished_at") or "",
            "message": source.get("message") or "",
            "branch": source.get("branch") or "main",
            "commit": source.get("commit") or "",
            "web_url": source.get("url") or "",
            "build_kind": "nightly" if source.get("is_canonical_nightly") else "main",
            "jobs": [],
        }

    for group in reliability.get("groups") or []:
        for observation in group.get("observations") or []:
            if not observation.get("eligible_for_reliability"):
                continue
            number = int(observation.get("build_number") or 0)
            if not number:
                continue
            build = builds.setdefault(number, {
                "number": number,
                "state": "unknown",
                "created_at": observation.get("build_created_at") or "",
                "started_at": "",
                "finished_at": "",
                "message": observation.get("build_message") or "",
                "branch": "main",
                "commit": observation.get("build_commit") or "",
                "web_url": observation.get("build_url") or "",
                "build_kind": "main",
                "jobs": [],
            })
            job = {
                "group_id": group.get("group_id") or "",
                "canonical_group_id": group.get("group_id") or "",
                "name": group.get("name") or "unknown",
                "raw_name": group.get("raw_name") or group.get("name") or "unknown",
                "step_key": group.get("step_key") or "",
                "hardware": group.get("hardware") or "unknown",
                "q": group.get("queue") or "",
                "state": observation.get("result") or "unknown",
                "terminal_state": observation.get("terminal_state") or "unknown",
                "soft_failed": bool(observation.get("soft_failed")),
                "job_id": observation.get("job_id") or "",
                "step_id": observation.get("step_id") or "",
                "url": observation.get("job_url") or observation.get("step_url") or "",
                "attempt_url": observation.get("job_url") or "",
                "step_url": observation.get("step_url") or "",
                "started_at": observation.get("started_at") or "",
                "finished_at": observation.get("finished_at") or "",
                "runnable_at": observation.get("runnable_at") or "",
                "wall_duration_mins": observation.get("wall_completion_mins"),
                "wall_completion_mins": observation.get("wall_completion_mins"),
                "test_duration_mins": observation.get("test_duration_mins"),
                "wait_mins": observation.get("queue_wait_mins"),
                "queue_wait_mins": observation.get("queue_wait_mins"),
                "end_to_end_mins": observation.get("end_to_end_mins"),
            }
            job.update(observation.get("retry_evidence") or {})
            build["jobs"].append({
                key: value
                for key, value in job.items()
                if value not in (None, "")
            })

    for build in builds.values():
        build["jobs"].sort(key=lambda job: (
            str(job.get("name") or "").lower(),
            str(job.get("raw_name") or "").lower(),
            str(job.get("group_id") or ""),
            str(job.get("job_id") or ""),
        ))
    return sorted(
        builds.values(),
        key=lambda build: (str(build.get("created_at") or ""), int(build.get("number") or 0)),
        reverse=True,
    )


def _nightly_job_state(job: dict) -> tuple[str, dict]:
    result, eligible, _ = _result(job)
    identity = _identity(job)
    group_id = _group_id(identity)
    url = str(job.get("url") or job.get("web_url") or "")
    return result if eligible else "indeterminate", {
        "group_id": group_id,
        "name": identity["canonical_label"],
        "raw_name": identity["raw_label"],
        "step_key": identity["step_key"],
        "hardware": identity["hardware"],
        "queue": identity["queue"],
        "state": result if eligible else str(job.get("state") or "unknown"),
        "url": url,
    }


def _nightly_state_map(build: dict) -> dict[str, tuple[str, dict]]:
    rows: dict[str, tuple[str, dict]] = {}
    for job in build.get("jobs") or []:
        state, ref = _nightly_job_state(job)
        rows[ref["group_id"]] = (state, ref)
    return rows


def compute_nightly_change_history(builds: list[dict]) -> list[dict]:
    """Compare canonical nightlies; absence never counts as a fixed group."""
    ordered = sorted(
        builds,
        key=lambda build: (str(build.get("created_at") or build.get("date") or ""), int(build.get("number") or 0)),
        reverse=True,
    )
    history = []
    for index, current in enumerate(ordered):
        previous = ordered[index + 1] if index + 1 < len(ordered) else None
        current_map = _nightly_state_map(current)
        previous_map = _nightly_state_map(previous) if previous else {}
        current_incidents = {key for key, (state, _) in current_map.items() if state in {"failed", "soft_fail"}}
        previous_incidents = {key for key, (state, _) in previous_map.items() if state in {"failed", "soft_fail"}}
        new_keys = sorted(current_incidents - previous_incidents) if previous else []
        recurring_keys = sorted(current_incidents & previous_incidents) if previous else []
        fixed_keys = sorted(
            key for key in previous_incidents
            if previous and key in current_map and current_map[key][0] == "passed"
        )
        absent_keys = sorted(key for key in previous_incidents if previous and key not in current_map)
        indeterminate_keys = sorted(
            key for key in previous_incidents
            if previous and key in current_map and current_map[key][0] == "indeterminate"
        )
        history.append({
            "build_number": current.get("number") or current.get("build_number"),
            "build_url": current.get("web_url") or "",
            "created_at": current.get("created_at") or "",
            "preceding_build_number": previous.get("number") if previous else None,
            "new": [current_map[key][1] for key in new_keys],
            "recurring": [current_map[key][1] for key in recurring_keys],
            "fixed": [
                {
                    **current_map[key][1],
                    "current_state": "passed",
                    "previous_state": previous_map[key][0],
                    "previous_url": previous_map[key][1].get("url") or "",
                }
                for key in fixed_keys
            ],
            "not_observed": [previous_map[key][1] for key in absent_keys],
            "indeterminate": [current_map[key][1] for key in indeterminate_keys],
        })
    return history
