#!/usr/bin/env python3
"""Build the compact, authoritative v2 operations dashboard snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.constants import is_excluded_queue  # noqa: E402
from vllm.collect_gating_target_candidates import hardware_fold_key  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_INPUT = ROOT / "data" / "vllm" / "ci"
DEFAULT_OUTPUT_NAME = "operations_v2.json"
OPERATIONS_MANIFEST_NAME = "operations_v2_manifest.json"
OPERATIONS_BUNDLE_DIR_NAME = "operations_v2"
NIGHTLY_BUILD_LIMIT = 30
RANKING_LIMIT = 20
CHANGE_LIMIT = 20
GROUP_HISTORY_LIMIT = 60
AMD_TEST_HISTORY_LIMIT = 30
AMD_TEST_RESULTS_GLOB = "test_results/*_amd.jsonl"
AMD_TEST_PIPELINE = "amd-ci"
# Per-physical-agent (node) AMD GPU health is now collected and aggregated by
# scripts/vllm/collect_agent_health.py (all builds, all branches) and embedded
# verbatim from agent_health.json by _amd_agent_health below. See that collector
# for the rollup / infra-suspect model and the frontend for client-side
# aggregation + co-failure clustering.
FAILED_STATES = {"failed", "timed_out", "broken", "canceled"}
SOFT_FAILED_STATES = {"soft_fail", "soft_failed"}
TRUSTWORTHY_BUILD_STATES = {"passed", "failed"}
RETRY_EVIDENCE_FIELDS = (
    "retried",
    "retried_in_job_id",
    "retries_count",
    "retry_source",
    "retry_type",
    "step_key",
)

QUEUE_HISTORY_SHARD_FIELDS = {
    "waiting",
    "running",
    "scheduled",
    "total",
    "zombie_waiting",
    "zombie_running",
    "connected_agents",
    "connected_agents_available",
    "connected_agents_source",
    "count_source",
    "p50_wait",
    "p50_wait_source",
    "p95_wait",
    "p95_wait_source",
    "p99_wait",
    "p99_wait_source",
    "max_wait",
    "max_wait_source",
    "wait_source",
    "wait_sample_count",
    "official_wait_source",
    "sample_wait_source",
    "metrics_ts",
}

SOURCE_FILES = {
    "analytics": "analytics.json",
    "agent_health": "agent_health.json",
    "ci_health": "ci_health.json",
    "config_parity": "config_parity.json",
    "gating_targets": "gating_targets.json",
    "gating_target_candidates": "gating_target_candidates.json",
    "amd_test_matrix": "amd_test_matrix.json",
    "capacity_monitor": "capacity_monitor.json",
    "workload_mapping": "workload_mapping.json",
    "queue_timeseries": "queue_timeseries.jsonl",
    "queue_jobs": "queue_jobs.json",
    "group_changes": "group_changes.json",
    "omni_heuristic": "omni_surge_heuristic.json",
    "omni_issue_state": "open_omni_surge_issues.json",
    "project_items": "project_items.json",
    "ready_tickets": "ready_tickets.json",
    "ci_ownership": "ci_ownership.json",
}

MULTISPACE_RE = re.compile(r"\s+")
AMD_PREFIX_RE = re.compile(r"^AMD:\s*", re.IGNORECASE)
INTERNAL_AMD_PREFIX_RE = re.compile(r"^mi\d{3,4}b?_\d+:\s*", re.IGNORECASE)
AMD_DEVICE_SUFFIX_RE = re.compile(r"\s*\((mi\d{3,4}b?_\d+)\)\s*$", re.IGNORECASE)
SHARD_TEMPLATE_SUFFIX_RE = re.compile(r"\s*%N\s*$", re.IGNORECASE)
AMD_TARGET_SUFFIX_RE = re.compile(
    r"(?<=\d)(?:x)?mi\d{2,4}b?(?:[_-]\d+)?(?=\))",
    re.IGNORECASE,
)
AMD_TEST_JOB_PREFIX_RE = re.compile(
    r"^(?P<hardware_variant>mi\d{3}b?(?:_\d+)?):\s*(?P<display_name>.*)$",
    re.IGNORECASE,
)
AMD_TEST_INCIDENT_STATUSES = {"failed", "error"}
AMD_TEST_SOFT_STATES = {"soft", "soft_fail", "soft_failed"}
AMD_TEST_HARD_STATES = {"failed", "timed_out", "broken", "canceled"}
AMD_HARDWARE_RE = re.compile(r"^mi\d{3,4}b?$", re.IGNORECASE)
AMD_QUEUE_RE = re.compile(r"^amd_mi\d{3,4}b?(?:_|$)", re.IGNORECASE)
AMD_TARGET_ARCHITECTURES = ("mi250", "mi300", "mi355")
AMD_TARGET_DEFAULT_PREFERENCE = ("mi250", "mi355", "mi300")
AMD_TARGET_CURRENT_DEFINITION_PREFERENCE = ("mi250", "mi300", "mi355")
CUDA_HARDWARE = {"a100", "b200", "h100", "h200"}
CUDA_QUEUE_RE = re.compile(
    r"^(?:gpu_\d+_queue|a100_queue|b200(?:-|_)|h200(?:_|$)|mithril-h100-pool|gh200_queue|dgx-spark)$",
    re.IGNORECASE,
)
HARDWARE_WORD_RE = re.compile(r"(?:mi\d{3,4}b?|[abh]\d{3})", re.IGNORECASE)
GATING_CONFIG_URL = (
    "https://github.com/AndreasKaratzas/vllm-ci-dashboard/"
    "blob/main/config/vllm_amd_gating_targets.json"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _is_current_queue_snapshot(row: Any) -> bool:
    """Recognize the provenance-bearing queue schema used by the dashboard."""
    return (
        isinstance(row, dict)
        and isinstance(row.get("ts"), str)
        and isinstance(row.get("queues"), dict)
        and isinstance(row.get("total_waiting"), int)
        and isinstance(row.get("total_running"), int)
        and isinstance(row.get("sources") or row.get("provenance"), dict)
    )


def load_latest_queue_snapshot(path: Path) -> dict:
    latest: dict = {}
    if not path.exists():
        return latest
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _is_current_queue_snapshot(row) and row["ts"] >= latest.get("ts", ""):
            latest = row
    return latest


def load_queue_history(path: Path) -> list[dict]:
    """Load every timestamped snapshot, including migrated counts-only rows."""
    rows: dict[str, dict] = {}
    if not path.exists():
        return []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or not isinstance(row.get("ts"), str):
            continue
        if not isinstance(row.get("queues"), dict):
            continue
        rows[row["ts"]] = row
    return [rows[key] for key in sorted(rows)]


def _is_excluded_queue(value: Any) -> bool:
    """Defensive presentation filter; collectors enforce the same exclusion."""
    return is_excluded_queue(str(value or ""))


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _payload_timestamp(data: dict) -> str:
    for key in ("generated_at", "ts", "updated_at", "last_snapshot_ts"):
        if data.get(key):
            return str(data[key])
    nested = [
        str(value.get("generated_at"))
        for value in data.values()
        if isinstance(value, dict) and value.get("generated_at")
    ]
    return max(nested, default="")


def _source_record(path: Path, data: dict, timestamp: str = "") -> dict:
    payload_ts = timestamp or _payload_timestamp(data)
    if payload_ts:
        return {"path": path.name, "timestamp": payload_ts, "timestamp_source": "payload"}
    if path.exists():
        mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"path": path.name, "timestamp": mtime, "timestamp_source": "file_mtime"}
    return {"path": path.name, "timestamp": None, "timestamp_source": "missing"}


def _build_url(pipeline: str, build: dict) -> str:
    number = _strict_int(build.get("number") or build.get("build_number"))
    return f"https://buildkite.com/vllm/{pipeline}/builds/{number}" if number else ""


def _job_url(pipeline: str, build: dict, job: dict) -> str:
    base = _build_url(pipeline, build)
    if not base:
        return ""
    if job.get("job_id"):
        return f"{base}/steps/canvas?jid={job['job_id']}&tab=output"
    if job.get("step_id"):
        return f"{base}/steps/canvas?sid={job['step_id']}&tab=output"
    raw_url = job.get("url") or job.get("web_url")
    build_number = build.get("number") or build.get("build_number")
    return str(raw_url) if _pipeline_job_url_matches(
        raw_url, pipeline, build_number
    ) else ""


def _group_identity(job: dict) -> str:
    return str(job.get("raw_name") or job.get("name") or "unknown")


def _group_row(pipeline: str, build: dict, job: dict, state: str) -> dict:
    raw_name = _group_identity(job)
    row = {
        "name": raw_name,
        "state": state,
        "url": _job_url(pipeline, build, job),
        "source_pipeline": pipeline,
        "build_number": build.get("number") or build.get("build_number"),
    }
    display_name = str(job.get("name") or "")
    if display_name and display_name != raw_name:
        row["display_name"] = display_name
    if job.get("q"):
        row["queue"] = job["q"]
    return row


def _failed_group_maps(pipeline: str, build: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    hard: dict[str, dict] = {}
    soft: dict[str, dict] = {}
    for job in build.get("jobs") or []:
        state = str(job.get("state") or "").lower()
        key = _group_identity(job)
        if state in SOFT_FAILED_STATES or job.get("soft_failed"):
            soft[key] = _group_row(pipeline, build, job, "soft_failed")
        elif state in FAILED_STATES:
            hard[key] = _group_row(pipeline, build, job, "failed")
    return hard, soft


def _terminal_group_states(build: dict) -> dict[str, str]:
    """Return observed terminal outcomes without treating absence as success."""
    states: dict[str, str] = {}
    for job in build.get("jobs") or []:
        state = _historical_state(job)
        if state == "unknown":
            continue
        key = _group_identity(job)
        previous = states.get(key)
        if previous in {"hard", "soft"} and state == "passed":
            retry = _retry_evidence(job)
            states[key] = "passed" if retry else previous
        elif previous != "hard" or state == "hard":
            states[key] = state
    return states


def _nightly_pipeline(pipeline: str, analytics: dict, health: dict | None = None) -> dict:
    health = health or {}
    source_builds_by_number = {
        _strict_int(build.get("number") or build.get("build_number")): dict(build)
        for build in analytics.get("builds") or []
        if _strict_int(build.get("number") or build.get("build_number")) is not None
    }
    health_builds = {
        _strict_int(build.get("number") or build.get("build_number")): build
        for build in health.get("builds") or []
        if _strict_int(build.get("number") or build.get("build_number")) is not None
    }
    latest_pipeline = health.get("latest_pipeline_build") or {}
    latest_pipeline_number = _strict_int(
        latest_pipeline.get("number") or latest_pipeline.get("build_number")
    )
    if latest_pipeline_number is not None and latest_pipeline_number not in source_builds_by_number:
        source_builds_by_number[latest_pipeline_number] = {
            "number": latest_pipeline_number,
            "created_at": latest_pipeline.get("created_at") or "",
            "state": latest_pipeline.get("state") or "unknown",
            "commit": latest_pipeline.get("commit") or "",
            "message": latest_pipeline.get("message") or "",
            "total_jobs": latest_pipeline.get("job_count") or 0,
            "jobs": [],
        }
    source_builds = sorted(
        source_builds_by_number.values(),
        key=lambda build: str(build.get("created_at") or build.get("date") or ""),
        reverse=True,
    )
    rows = []
    for index, build in enumerate(source_builds[:NIGHTLY_BUILD_LIMIT]):
        build_number = _strict_int(build.get("number") or build.get("build_number"))
        health_build = health_builds.get(build_number) or {}
        has_test_results = bool(
            health_build.get("has_test_results")
            if "has_test_results" in health_build
            else build.get("jobs")
        )
        hard, soft = _failed_group_maps(pipeline, build)
        current = {**hard, **soft}
        current_states = _terminal_group_states(build)
        previous_build = source_builds[index + 1] if index + 1 < len(source_builds) else None
        previous: dict[str, dict] = {}
        if previous_build:
            previous_hard, previous_soft = _failed_group_maps(pipeline, previous_build)
            previous = {**previous_hard, **previous_soft}

        new_keys = sorted(current.keys() - previous.keys()) if previous_build else []
        recurring_keys = sorted(current.keys() & previous.keys()) if previous_build else []
        fixed_keys = sorted(
            key for key in previous
            if current_states.get(key) == "passed"
        ) if previous_build else []
        unobserved_keys = sorted(
            key for key in previous
            if key not in current and key not in fixed_keys
        ) if previous_build else []
        rows.append({
            "number": build_number,
            "source_pipeline": pipeline,
            "created_at": build.get("created_at") or "",
            "state": build.get("state") or "unknown",
            "url": _build_url(pipeline, build),
            "commit": build.get("commit") or build.get("commit_sha") or "",
            "message": build.get("message") or "",
            "total_groups": (
                build.get("total_jobs") or len(build.get("jobs") or [])
                if has_test_results else 0
            ),
            "has_test_results": has_test_results,
            "test_job_count": int(health_build.get("test_job_count") or 0),
            "test_jobs_blocked": int(health_build.get("test_jobs_blocked") or 0),
            "failed_groups": [hard[key] for key in sorted(hard)],
            "soft_failed_groups": [soft[key] for key in sorted(soft)],
            "transitions": {
                "preceding_build_number": (
                    previous_build.get("number") or previous_build.get("build_number")
                    if previous_build else None
                ),
                "new": [current[key] for key in new_keys],
                "recurring": [current[key] for key in recurring_keys],
                "fixed": [previous[key] for key in fixed_keys],
                "not_observed": [previous[key] for key in unobserved_keys],
            },
        })
    return {
        "pipeline": pipeline,
        "display_name": analytics.get("display_name") or pipeline,
        "role": "canonical_nightly_comparison" if pipeline == "amd-ci" else "upstream_parity",
        "history_window_days": min(int(analytics.get("days") or NIGHTLY_BUILD_LIMIT), NIGHTLY_BUILD_LIMIT),
        "history_limit": NIGHTLY_BUILD_LIMIT,
        "builds_available": len(source_builds),
        "builds": rows,
    }


def _aggregate_amd_jobs(builds: list[dict]) -> list[dict]:
    stats: dict[str, dict] = defaultdict(
        lambda: {"runs": 0, "passed": 0, "failed": 0, "soft_failed": 0, "durations": [], "queues": set()}
    )
    for build in builds:
        for job in build.get("jobs") or []:
            row = stats[str(job.get("name") or _group_identity(job))]
            row["runs"] += 1
            state = str(job.get("state") or "").lower()
            if state == "passed":
                row["passed"] += 1
            elif state in SOFT_FAILED_STATES or job.get("soft_failed"):
                row["soft_failed"] += 1
            elif state in FAILED_STATES:
                row["failed"] += 1
            if isinstance(job.get("dur"), (int, float)):
                row["durations"].append(float(job["dur"]))
            if job.get("q"):
                row["queues"].add(str(job["q"]))

    rows = []
    for name, values in stats.items():
        durations = sorted(values.pop("durations"))
        queues = sorted(values.pop("queues"))
        failures = values["failed"] + values["soft_failed"]
        rows.append({
            "name": name,
            **values,
            "fail_rate": round(failures / values["runs"] * 100, 1) if values["runs"] else 0,
            "median_dur": round(median(durations), 1) if durations else None,
            "p90_dur": _percentile(durations, 90),
            "max_dur": round(max(durations), 1) if durations else None,
            "queues": queues,
        })
    return rows


def _percentile(values: list[float], percent: int) -> float | None:
    if not values:
        return None
    index = (len(values) - 1) * percent / 100
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    value = values[lower] + (values[upper] - values[lower]) * (index - lower)
    return round(value, 1)


def _ranking_row(row: dict) -> dict:
    keys = (
        "name", "runs", "passed", "failed", "soft_failed", "fail_rate",
        "median_dur", "p90_dur", "avg_dur", "max_dur", "queues",
    )
    return {key: row[key] for key in keys if key in row}


def _historical_state(job: dict) -> str:
    state = str(job.get("state") or "").lower()
    if state in SOFT_FAILED_STATES or job.get("soft_failed"):
        return "soft"
    if state in FAILED_STATES or state == "expired":
        return "hard"
    if state == "passed":
        return "passed"
    return "unknown"


def _retry_evidence(job: dict) -> dict:
    retries_count = job.get("retries_count")
    has_explicit_signal = (
        bool(job.get("retried"))
        or bool(job.get("retried_in_job_id"))
        or retries_count not in (None, "", 0, "0")
        or bool(job.get("retry_source"))
        or bool(job.get("retry_type"))
    )
    if not has_explicit_signal:
        return {}
    evidence = {
        key: job.get(key)
        for key in RETRY_EVIDENCE_FIELDS
        if key in job
    }
    for key in ("job_id", "step_id"):
        if job.get(key):
            evidence[key] = job[key]
    return evidence


def _strict_group_label(value: Any) -> str:
    """Normalize decoration while preserving meaningful hardware wording."""
    text = MULTISPACE_RE.sub(" ", str(value or "").strip())
    text = AMD_PREFIX_RE.sub("", text)
    text = INTERNAL_AMD_PREFIX_RE.sub("", text)
    text = AMD_DEVICE_SUFFIX_RE.sub("", text)
    return MULTISPACE_RE.sub(" ", text).strip()


def _target_match_key(value: Any) -> str:
    """Join an AMD mirror label to its CUDA target without folding GPU variants."""
    text = _strict_group_label(value).lower()
    text = SHARD_TEMPLATE_SUFFIX_RE.sub("", text)
    text = re.sub(r"-\s*\d+x?mi\d{2,4}b?(?:[_-]\d+)?(?=\))", "", text)
    text = re.sub(r"-\s*\d*x?mi(?=\))", "", text)
    return MULTISPACE_RE.sub(" ", text).strip()


def _strict_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def _pipeline_url_parts(value: Any, pipeline_slug: str) -> tuple[int, list[str], dict] | None:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.netloc != "buildkite.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[:3] != ["vllm", pipeline_slug, "builds"]:
        return None
    build_number = _strict_int(parts[3])
    if build_number is None:
        return None
    return build_number, parts[4:], parse_qs(parsed.query)


def _pipeline_build_url_matches(
    value: Any,
    pipeline_slug: str,
    build_number: Any = None,
) -> bool:
    parsed = _pipeline_url_parts(value, pipeline_slug)
    expected = _strict_int(build_number)
    return bool(parsed and not parsed[1] and (expected is None or parsed[0] == expected))


def _pipeline_job_url_matches(
    value: Any,
    pipeline_slug: str,
    build_number: Any = None,
) -> bool:
    parsed = _pipeline_url_parts(value, pipeline_slug)
    expected = _strict_int(build_number)
    if not parsed or (expected is not None and parsed[0] != expected):
        return False
    suffix, query = parsed[1], parsed[2]
    if len(suffix) < 2 or suffix[0] != "steps":
        return False
    if suffix[1] == "canvas":
        return bool(query.get("jid") or query.get("sid"))
    return bool(suffix[1])


def _amd_test_group_id(exact_job_name: str, pipeline_slug: str = AMD_TEST_PIPELINE) -> str:
    identity = f"{pipeline_slug}:{exact_job_name}".encode("utf-8")
    return hashlib.sha1(identity).hexdigest()[:20]


def _amd_test_job_labels(exact_job_name: str) -> tuple[str, str, str, str]:
    match = AMD_TEST_JOB_PREFIX_RE.match(exact_job_name)
    if not match:
        return exact_job_name, "unknown", "unknown", ""
    hardware_variant = match.group("hardware_variant").lower()
    hardware = hardware_variant.split("_", 1)[0]
    display_name = match.group("display_name")
    return display_name, hardware, hardware_variant, f"amd_{hardware_variant}"


def _amd_test_result_count(row: dict) -> int:
    for key in ("test_count", "count"):
        value = row.get(key)
        if isinstance(value, bool):
            continue
        try:
            count = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if count > 0:
            return count
    match = re.search(r"\((\d+)\)\s*$", str(row.get("name") or ""))
    return int(match.group(1)) if match else 1


def _amd_test_job_state(job: dict) -> str:
    state = str(job.get("state") or "").strip().lower()
    if job.get("soft_failed") or state in AMD_TEST_SOFT_STATES:
        return "soft"
    if state in AMD_TEST_HARD_STATES:
        return "hard"
    if state == "passed":
        return "passed"
    return "unknown"


def _amd_test_observation_state(jobs: list[dict]) -> str:
    states = {_amd_test_job_state(job) for job in jobs}
    for state in ("hard", "soft", "passed"):
        if state in states:
            return state
    return "unknown"


def _amd_test_pass_rate(passed: int, incidents: int) -> float | None:
    known = passed + incidents
    return round(passed / known * 100, 1) if known else None


def _amd_test_metadata_builds(amd_analytics: Any) -> dict[int, dict]:
    if not isinstance(amd_analytics, dict):
        return {}
    result: dict[int, dict] = {}
    builds = amd_analytics.get("builds")
    if not isinstance(builds, list):
        return result
    for build in builds:
        if not isinstance(build, dict):
            continue
        number = _strict_int(build.get("number") or build.get("build_number"))
        if number is not None and number not in result:
            result[number] = build
    return result


def _amd_test_job_metadata(
    build: dict,
    evidence_rows: list[dict],
) -> list[dict]:
    jobs = build.get("jobs")
    if not isinstance(jobs, list):
        return []
    by_job_id = {
        str(job.get("job_id")): job
        for job in jobs
        if isinstance(job, dict) and job.get("job_id")
    }
    matches = []
    seen: set[str] = set()
    for evidence in evidence_rows:
        job_id = str(evidence.get("job_id") or "")
        if not job_id or job_id in seen or job_id not in by_job_id:
            continue
        seen.add(job_id)
        matches.append(by_job_id[job_id])
    return matches


def _amd_test_build_url(build_number: int, metadata: dict) -> str:
    raw_url = metadata.get("web_url") or metadata.get("url")
    if _pipeline_build_url_matches(raw_url, AMD_TEST_PIPELINE, build_number):
        return str(raw_url)
    return _build_url(AMD_TEST_PIPELINE, {"number": build_number})


def _amd_test_evidence_row(evidence_rows: list[dict], metadata: dict) -> dict:
    metadata_job_id = str(metadata.get("job_id") or "")
    selected = [
        row for row in evidence_rows
        if metadata_job_id and str(row.get("job_id") or "") == metadata_job_id
    ]
    candidates = selected or evidence_rows
    if not candidates:
        return {}
    return max(
        candidates,
        key=lambda row: (
            bool(row.get("job_id")),
            bool(row.get("step_id")),
            bool(row.get("url") or row.get("web_url")),
        ),
    )


def _amd_test_job_url(build_number: int, evidence: dict, metadata: dict) -> str:
    job = {
        "job_id": evidence.get("job_id") or metadata.get("job_id"),
        "step_id": evidence.get("step_id") or metadata.get("step_id"),
        "url": evidence.get("url") or evidence.get("web_url") or metadata.get("url"),
    }
    return _job_url(AMD_TEST_PIPELINE, {"number": build_number}, job)


def _load_amd_test_result_groups(data_dir: Path) -> tuple[dict[tuple[int, str], dict], dict]:
    try:
        paths = sorted((data_dir / "test_results").glob("*_amd.jsonl"))
    except OSError:
        paths = []
    grouped: dict[tuple[int, str], dict] = {}
    stats = {
        "files_discovered": len(paths),
        "files_read": 0,
        "files_with_valid_rows": 0,
        "unreadable_files": 0,
        "valid_rows": 0,
        "malformed_rows": 0,
        "ignored_rows": 0,
        "source_files": [],
    }
    for path in paths:
        try:
            relative_path = path.relative_to(data_dir).as_posix()
        except ValueError:
            relative_path = path.name
        stats["source_files"].append(relative_path)
        fallback_date = path.name.removesuffix("_amd.jsonl")
        file_valid_rows = 0
        try:
            with path.open(encoding="utf-8") as source:
                stats["files_read"] += 1
                for line in source:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        stats["malformed_rows"] += 1
                        continue
                    if not isinstance(row, dict):
                        stats["malformed_rows"] += 1
                        continue
                    if row.get("pipeline") not in (None, "", AMD_TEST_PIPELINE):
                        stats["ignored_rows"] += 1
                        continue
                    build_number = _strict_int(row.get("build_number"))
                    exact_job_name = row.get("job_name")
                    if build_number is None or not isinstance(exact_job_name, str) or not exact_job_name:
                        stats["malformed_rows"] += 1
                        continue
                    status = str(row.get("status") or "unknown").strip().lower() or "unknown"
                    count = _amd_test_result_count(row)
                    key = (build_number, exact_job_name)
                    bucket = grouped.setdefault(key, {
                        "build_number": build_number,
                        "exact_job_name": exact_job_name,
                        "dates": set(),
                        "status_counts": Counter(),
                        "status_row_counts": Counter(),
                        "test_duration_secs": 0.0,
                        "evidence_rows": [],
                    })
                    date = row.get("date") or fallback_date
                    if date:
                        bucket["dates"].add(str(date))
                    bucket["status_counts"][status] += count
                    bucket["status_row_counts"][status] += 1
                    duration = _number(row.get("duration_secs"))
                    if duration is not None and duration >= 0 and duration != float("inf"):
                        bucket["test_duration_secs"] += duration
                    bucket["evidence_rows"].append({
                        key: row.get(key)
                        for key in (
                            "status", "job_id", "step_id", "url", "web_url",
                            "observed_at", "finished_at", "started_at",
                        )
                        if row.get(key) not in (None, "")
                    } | {"status": status})
                    file_valid_rows += 1
                    stats["valid_rows"] += 1
        except (OSError, UnicodeError):
            stats["unreadable_files"] += 1
        if file_valid_rows:
            stats["files_with_valid_rows"] += 1
    return grouped, stats


def _amd_test_observation(bucket: dict, metadata: dict) -> dict:
    build_number = bucket["build_number"]
    exact_job_name = bucket["exact_job_name"]
    display_name, hardware, hardware_variant, queue = _amd_test_job_labels(exact_job_name)
    status_counts = Counter(bucket["status_counts"])
    job_metadata_rows = _amd_test_job_metadata(metadata, bucket["evidence_rows"])
    state = _amd_test_observation_state(job_metadata_rows)
    state_metadata = [
        job for job in job_metadata_rows
        if _amd_test_job_state(job) == state
    ]
    job_metadata = max(
        state_metadata or job_metadata_rows or [{}],
        key=lambda job: str(job.get("finished_at") or job.get("started_at") or ""),
    )
    evidence = _amd_test_evidence_row(bucket["evidence_rows"], job_metadata)
    build_url = _amd_test_build_url(build_number, metadata)
    job_url = _amd_test_job_url(build_number, evidence, job_metadata)
    observed_at = (
        job_metadata.get("finished_at")
        or job_metadata.get("started_at")
        or evidence.get("observed_at")
        or evidence.get("finished_at")
        or evidence.get("started_at")
        or metadata.get("created_at")
        or metadata.get("finished_at")
        or max(bucket["dates"], default="")
    )
    date = str(metadata.get("date") or max(bucket["dates"], default=""))
    tests = sum(status_counts.values())
    passed_tests = status_counts.get("passed", 0)
    failed_tests = sum(status_counts.get(status, 0) for status in AMD_TEST_INCIDENT_STATUSES)
    skipped_tests = status_counts.get("skipped", 0) + status_counts.get("xfailed", 0)
    unknown_tests = max(0, tests - passed_tests - failed_tests - skipped_tests)
    duration_secs = round(float(bucket["test_duration_secs"]), 2)
    row = {
        "source_pipeline": AMD_TEST_PIPELINE,
        "build_number": build_number,
        "state": state,
        "outcome_source": "analytics_job_state" if job_metadata_rows else "unavailable",
        "analytics_job_count": len(job_metadata_rows),
        "observed_at": str(observed_at or ""),
        "date": date or str(observed_at or "")[:10],
        "url": job_url,
        "job_url": job_url,
        "build_url": build_url,
        "hardware": hardware,
        "hardware_variant": hardware_variant,
        "queue": queue,
        "status_counts": dict(sorted(status_counts.items())),
        "status_row_counts": dict(sorted(bucket["status_row_counts"].items())),
        "tests": tests,
        "passed_tests": passed_tests,
        "failed_tests": failed_tests,
        "error_tests": status_counts.get("error", 0),
        "skipped_tests": skipped_tests,
        "unknown_tests": unknown_tests,
        "test_duration_secs": duration_secs,
        "test_duration_mins": round(duration_secs / 60, 2),
        "duration_mins": round(duration_secs / 60, 2),
        "duration_basis": "test_reported",
    }
    job_id = evidence.get("job_id") or job_metadata.get("job_id")
    step_id = evidence.get("step_id") or job_metadata.get("step_id")
    if job_id:
        row["job_id"] = str(job_id)
    if step_id:
        row["step_id"] = str(step_id)
    return row


def _amd_test_sort_key(row: dict) -> tuple[str, int]:
    return (
        str(row.get("observed_at") or row.get("date") or ""),
        _strict_int(row.get("build_number")) or 0,
    )


def _amd_test_health(data_dir: Path, amd_analytics: Any) -> dict:
    grouped, load_stats = _load_amd_test_result_groups(data_dir)
    metadata_by_build = _amd_test_metadata_builds(amd_analytics)
    observations_by_group: dict[str, list[dict]] = defaultdict(list)
    observations_by_build: dict[int, list[dict]] = defaultdict(list)
    for (build_number, exact_job_name), bucket in grouped.items():
        observation = _amd_test_observation(bucket, metadata_by_build.get(build_number) or {})
        observations_by_group[exact_job_name].append(observation)
        observations_by_build[build_number].append(observation)

    catalog = []
    for exact_job_name, source_observations in observations_by_group.items():
        source_observations.sort(key=_amd_test_sort_key)
        group_id = _amd_test_group_id(exact_job_name)
        observations = [
            {**row, "group_id": group_id}
            for row in source_observations[-AMD_TEST_HISTORY_LIMIT:]
        ]
        display_name, hardware, hardware_variant, queue = _amd_test_job_labels(exact_job_name)
        state_counts = Counter(row["state"] for row in source_observations)
        runs = len(source_observations)
        passed = state_counts["passed"]
        soft_failed = state_counts["soft"]
        hard_failed = state_counts["hard"]
        incidents = soft_failed + hard_failed
        unknown = state_counts["unknown"]
        latest = source_observations[-1]
        current_pass_streak = 0
        for observation in reversed(source_observations):
            if observation["state"] != "passed":
                break
            current_pass_streak += 1
        catalog.append({
            "source_pipeline": AMD_TEST_PIPELINE,
            "id": group_id,
            "name": display_name,
            "display_name": display_name,
            "job_name": exact_job_name,
            "exact_job_name": exact_job_name,
            "hardware": hardware,
            "hardware_variant": hardware_variant,
            "queue": queue,
            "queues": [queue] if queue else [],
            "runs": runs,
            "passed": passed,
            "soft_failed": soft_failed,
            "hard_failed": hard_failed,
            "incidents": incidents,
            "unknown": unknown,
            "pass_rate_pct": _amd_test_pass_rate(passed, incidents),
            "current_pass_streak": current_pass_streak,
            "latest_state": latest["state"],
            "latest_build_number": latest["build_number"],
            "latest_url": latest["job_url"] or latest["build_url"],
            "latest_observed_at": latest["observed_at"],
            "first_observed_at": source_observations[0]["observed_at"],
            "observation_count": runs,
            "retained_observation_count": len(observations),
            "history_truncated": runs > len(observations),
            "observations": observations,
        })
    catalog.sort(
        key=lambda row: (
            str(row["hardware"]),
            str(row["display_name"]).lower(),
            str(row["exact_job_name"]),
        )
    )

    builds = []
    for build_number, source_observations in observations_by_build.items():
        metadata = metadata_by_build.get(build_number) or {}
        source_observations.sort(key=lambda row: str(row.get("hardware_variant") or "") + row["job_url"])
        state_counts = Counter(row["state"] for row in source_observations)
        observed_at = (
            metadata.get("created_at")
            or metadata.get("finished_at")
            or max((row["observed_at"] for row in source_observations), default="")
        )
        date = str(
            metadata.get("date")
            or max((row["date"] for row in source_observations), default="")
            or str(observed_at or "")[:10]
        )
        passed = state_counts["passed"]
        soft_failed = state_counts["soft"]
        hard_failed = state_counts["hard"]
        incidents = soft_failed + hard_failed
        unknown = state_counts["unknown"]
        build_url = _amd_test_build_url(build_number, metadata)
        builds.append({
            "source_pipeline": AMD_TEST_PIPELINE,
            "number": build_number,
            "build_number": build_number,
            "date": date,
            "observed_at": str(observed_at or ""),
            "url": build_url,
            "build_url": build_url,
            "observed": len(source_observations),
            "passed": passed,
            "soft_failed": soft_failed,
            "hard_failed": hard_failed,
            "incidents": incidents,
            "unknown": unknown,
            "observed_groups": len(source_observations),
            "passed_groups": passed,
            "soft_failed_groups": soft_failed,
            "hard_failed_groups": hard_failed,
            "incident_groups": incidents,
            "unknown_groups": unknown,
            "pass_rate_pct": _amd_test_pass_rate(passed, incidents),
            "state_counts": {
                "passed": passed,
                "soft": soft_failed,
                "hard": hard_failed,
                "unknown": unknown,
            },
        })
    builds.sort(key=_amd_test_sort_key)

    latest = builds[-1] if builds else {}
    latest_counts = latest.get("state_counts") or {
        "passed": 0,
        "soft": 0,
        "hard": 0,
        "unknown": 0,
    }
    observation_state_counts = Counter(
        row["state"]
        for observations in observations_by_build.values()
        for row in observations
    )
    joined_observation_count = sum(
        bool(row.get("analytics_job_count"))
        for observations in observations_by_build.values()
        for row in observations
    )
    hardware_counts = Counter(
        row["hardware"] for row in catalog if row["hardware"] != "unknown"
    )
    hardware_variant_counts = Counter(
        row["hardware_variant"] for row in catalog if row["hardware_variant"] != "unknown"
    )
    latest_hardware_counts = Counter(
        row["hardware"]
        for row in observations_by_build.get(latest.get("build_number"), [])
        if row["hardware"] != "unknown"
    )
    summary = {
        "build_count": len(builds),
        # This is the historical union of exact Buildkite job names, not the
        # denominator for the latest nightly. Keep the older aliases for
        # compatibility, but give new clients an unambiguous field name.
        "retained_group_count": len(catalog),
        "group_count": len(catalog),
        "union_group_count": len(catalog),
        "latest_group_count": int(latest.get("observed") or 0),
        "latest_build_number": latest.get("build_number"),
        "latest_build_url": latest.get("build_url"),
        "latest_url": latest.get("url"),
        "latest_observed_at": latest.get("observed_at"),
        "latest_state_counts": latest_counts,
        "latest_passed_group_count": int(latest_counts.get("passed") or 0),
        "latest_soft_failed_group_count": int(latest_counts.get("soft") or 0),
        "latest_hard_failed_group_count": int(latest_counts.get("hard") or 0),
        "latest_incident_group_count": int(latest_counts.get("soft") or 0)
        + int(latest_counts.get("hard") or 0),
        "latest_unknown_group_count": int(latest_counts.get("unknown") or 0),
        "observation_state_counts": {
            "passed": observation_state_counts["passed"],
            "soft": observation_state_counts["soft"],
            "hard": observation_state_counts["hard"],
            "unknown": observation_state_counts["unknown"],
        },
        "passed_observation_count": observation_state_counts["passed"],
        "soft_failed_observation_count": observation_state_counts["soft"],
        "hard_failed_observation_count": observation_state_counts["hard"],
        "incident_observation_count": observation_state_counts["soft"]
        + observation_state_counts["hard"],
        "unknown_observation_count": observation_state_counts["unknown"],
        "mixed_outcome_group_count": sum(
            bool(row["passed"] and row["incidents"]) for row in catalog
        ),
        "stable_passing_group_count": sum(
            bool(row["passed"] and not row["incidents"] and not row["unknown"])
            for row in catalog
        ),
        "persistent_incident_group_count": sum(
            bool(row["incidents"] and not row["passed"]) for row in catalog
        ),
        "hardware_counts": dict(sorted(hardware_counts.items())),
        "hardware_variant_counts": dict(sorted(hardware_variant_counts.items())),
        "latest_hardware_counts": dict(sorted(latest_hardware_counts.items())),
    }
    return {
        "available": bool(builds),
        "source_pipeline": AMD_TEST_PIPELINE,
        "cohort": {
            "id": "amd-ci-retained-nightly-test-results",
            "available": bool(builds),
            "pipeline": AMD_TEST_PIPELINE,
            "label": "Retained AMD CI nightly parsed test results",
            "build_count": len(builds),
            "build_numbers": [row["build_number"] for row in builds],
            "first_observed_at": builds[0]["observed_at"] if builds else None,
            "latest_observed_at": latest.get("observed_at"),
            "history_limit_per_group": AMD_TEST_HISTORY_LIMIT,
            "aggregation_key": ["build_number", "exact_job_name"],
        },
        "summary": summary,
        "builds": builds,
        "group_catalog": catalog,
        "provenance": {
            "source_paths": {
                "test_results": AMD_TEST_RESULTS_GLOB,
                "nightly_metadata": SOURCE_FILES["analytics"],
            },
            "test_results": {
                "glob": AMD_TEST_RESULTS_GLOB,
                "role": "parsed test counts, statuses, and duration only",
                **load_stats,
            },
            "nightly_metadata": {
                "path": SOURCE_FILES["analytics"],
                "source_key": "amd-ci.builds",
                "retained_build_count": len(metadata_by_build),
                "job_join_key": ["build_number", "job_id"],
                "joined_group_observations": joined_observation_count,
                "unjoined_group_observations": sum(len(rows) for rows in observations_by_build.values())
                - joined_observation_count,
                "role": "authoritative Buildkite terminal job outcome and timing",
            },
            "classification": {
                "passed": "analytics job state is passed",
                "soft": "analytics job state is soft_fail/soft_failed or soft_failed is true",
                "hard": "analytics job state is failed, timed_out, broken, or canceled",
                "unknown": "analytics job state is missing, skipped, or non-terminal",
                "incidents": "soft plus hard group observations",
                "jsonl_status_role": "test-count enrichment only; never the terminal group outcome",
                "missing_groups": "not inferred",
            },
            "identity": {
                "algorithm": "sha1",
                "length": 20,
                "input": "source_pipeline + ':' + exact_job_name",
            },
        },
    }


def _strict_build_rows(rows: Any, pipeline_slug: str) -> tuple[bool, set[int]]:
    if not isinstance(rows, list):
        return False, set()
    build_numbers: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            return False, set()
        number = _strict_int(row.get("number"))
        if (
            number is None
            or row.get("branch") != "main"
            or str(row.get("state") or "").lower() not in TRUSTWORTHY_BUILD_STATES
            or not row.get("finished_at")
            or not _pipeline_build_url_matches(
                row.get("url") or row.get("web_url"),
                pipeline_slug,
                number,
            )
            or number in build_numbers
        ):
            return False, set()
        build_numbers.add(number)
    return True, build_numbers


def _collector_main_is_strict(payload: Any, pipeline_slug: str) -> bool:
    if not isinstance(payload, dict):
        return False
    cohort = payload.get("cohort")
    provenance = payload.get("provenance")
    builds = payload.get("builds")
    groups = payload.get("groups")
    if not isinstance(cohort, dict) or not isinstance(provenance, dict):
        return False
    if not isinstance(groups, list):
        return False
    query = provenance.get("query")
    collection = provenance.get("collection")
    build_states = cohort.get("build_states")
    strict_builds, build_numbers = _strict_build_rows(builds, pipeline_slug)
    if (
        not strict_builds
        or not isinstance(build_states, list)
        or any(not isinstance(state, str) for state in build_states)
        or set(build_states) != TRUSTWORTHY_BUILD_STATES
        or _strict_int(cohort.get("build_count")) != len(builds)
        or cohort.get("id") != f"{pipeline_slug}-main-completed-pass-fail"
        or cohort.get("pipeline") != pipeline_slug
        or cohort.get("branch") != "main"
        or cohort.get("exhaustive") is not True
        or provenance.get("pipeline") != pipeline_slug
        or not str(provenance.get("endpoint") or "").endswith(
            f"/pipelines/{pipeline_slug}/builds"
        )
        or not isinstance(query, dict)
        or query.get("branch") != "main"
        or not isinstance(collection, dict)
        or collection.get("exhaustive") is not True
    ):
        return False
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("observations"), list):
            return False
        numeric_fields = (
            "denominator", "passed", "failed", "soft_failed",
            "excluded_observations", "retry_evidence_observations",
        )
        if any(
            isinstance(group.get(field), bool)
            or not isinstance(group.get(field), int)
            or group.get(field) < 0
            for field in numeric_fields
        ):
            return False
        if not isinstance(group.get("duration"), dict):
            return False
        for observation in group["observations"]:
            if not isinstance(observation, dict):
                return False
            number = _strict_int(observation.get("build_number"))
            if (
                number not in build_numbers
                or observation.get("source_pipeline") != pipeline_slug
                or not _pipeline_build_url_matches(
                    observation.get("build_url"), pipeline_slug, number
                )
                or not _pipeline_job_url_matches(
                    observation.get("job_url"), pipeline_slug, number
                )
                or (
                    observation.get("step_url")
                    and not _pipeline_job_url_matches(
                        observation.get("step_url"), pipeline_slug, number
                    )
                )
            ):
                return False
    return True


def _build_kind(build: dict) -> str:
    explicit = str(build.get("build_kind") or build.get("kind") or "").lower()
    if explicit:
        return explicit
    message = str(build.get("message") or "").lower()
    return "nightly" if "nightly" in message else "main"


def _historical_observation(
    build: dict,
    job: dict,
    group_id: str = "",
    pipeline_slug: str = "amd-ci",
) -> dict:
    state = _historical_state(job)
    row = {
        "source_pipeline": pipeline_slug,
        "build_number": build.get("number") or build.get("build_number"),
        "build_url": _build_url(pipeline_slug, build),
        "build_kind": _build_kind(build),
        "state": state,
        "observed_at": (
            job.get("finished_at")
            or job.get("started_at")
            or build.get("finished_at")
            or build.get("created_at")
            or build.get("date")
            or ""
        ),
    }
    if group_id:
        row["group_id"] = group_id
    for key, source in (
        ("commit", build.get("commit") or build.get("commit_sha")),
        ("message", build.get("message")),
        ("raw_name", job.get("raw_name")),
        ("step_key", job.get("step_key")),
        ("queue", job.get("q") or job.get("queue")),
    ):
        if source not in (None, ""):
            row[key] = source
    if job.get("url") or job.get("web_url") or job.get("job_id") or job.get("step_id"):
        row["job_url"] = _job_url(pipeline_slug, build, job)

    wall = _number(
        job.get("wall_duration_mins")
        if job.get("wall_duration_mins") is not None
        else job.get("wall_mins")
    )
    test = _number(
        job.get("test_duration_mins")
        if job.get("test_duration_mins") is not None
        else job.get("reported_duration_mins")
    )
    if test is None:
        test = _number(job.get("dur"))
    wait = _number(job.get("wait_mins") if job.get("wait_mins") is not None else job.get("wait"))
    end_to_end = _number(job.get("end_to_end_mins"))
    if wall is not None:
        row["wall_duration_mins"] = round(wall, 2)
    if test is not None:
        row["test_duration_mins"] = round(test, 2)
    if wait is not None:
        row["wait_mins"] = round(wait, 2)
    if end_to_end is not None:
        row["end_to_end_mins"] = round(end_to_end, 2)
    preferred = wall if wall is not None else test
    if preferred is not None:
        row["duration_mins"] = round(preferred, 2)
        row["duration_basis"] = "job_wall" if wall is not None else "test_reported"

    for key in ("tests", "passed_tests", "failed_tests", "skipped_tests"):
        if isinstance(job.get(key), (int, float)):
            row[key] = job[key]
    retry_evidence = _retry_evidence(job)
    if retry_evidence:
        row["retry_evidence"] = retry_evidence
    return row


def _group_id(job: dict, label: str) -> str:
    explicit = job.get("group_id") or job.get("canonical_group_id")
    if explicit:
        return str(explicit)
    queue = str(job.get("q") or job.get("queue") or "")
    hardware = _resolved_hardware(job, queue)
    identity = {
        "label": _strict_group_label(label),
        "hardware": hardware,
        "queue": queue,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return f"legacy-{hashlib.sha256(encoded).hexdigest()[:20]}"


def _hardware_from_queue(queue: Any) -> str:
    value = str(queue or "")
    match = re.match(r"^amd_(mi\d{3,4}b?)(?:_|$)", value, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _resolved_hardware(job: dict, queue: Any) -> str:
    explicit = str(job.get("hardware") or "").strip().lower()
    queue_hardware = _hardware_from_queue(queue)
    if explicit in {"", "unknown"} and queue_hardware:
        return queue_hardware
    return explicit or queue_hardware or "unknown"


def _streak(observations: list[dict], build_kind: str | None = None) -> int:
    count = 0
    seen_builds: set[Any] = set()
    for row in observations:
        if build_kind and row.get("build_kind") != build_kind:
            continue
        build_number = row.get("build_number")
        if build_number in seen_builds:
            continue
        seen_builds.add(build_number)
        if row.get("state") != "passed":
            break
        count += 1
    return count


def _group_catalog(
    builds: list[dict],
    pipeline_slug: str = "amd-ci",
) -> tuple[list[dict], dict]:
    groups: dict[str, dict] = {}
    unknown_observations = 0
    source_builds = sorted(
        builds,
        key=lambda build: str(build.get("created_at") or build.get("date") or ""),
        reverse=True,
    )
    for build in source_builds:
        for job in build.get("jobs") or []:
            queue = job.get("q") or job.get("queue")
            if _is_excluded_queue(queue):
                continue
            label = _strict_group_label(job.get("name") or _group_identity(job))
            if not label:
                continue
            group_id = _group_id(job, label)
            state = _historical_state(job)
            if state == "unknown":
                unknown_observations += 1
                continue
            row = groups.setdefault(group_id, {
                "id": group_id,
                "name": label,
                "group_ids": set(),
                "raw_names": set(),
                "hardware": set(),
                "queues": set(),
                "builds": set(),
                "passed": 0,
                "failed": 0,
                "soft_failed": 0,
                "wall": [],
                "test": [],
                "wait": [],
                "end_to_end": [],
                "linked": 0,
                "retry_evidence": 0,
                "observations": [],
            })
            raw_name = str(job.get("raw_name") or "")
            row["group_ids"].add(group_id)
            if raw_name:
                row["raw_names"].add(raw_name)
            if queue:
                row["queues"].add(str(queue))
            hardware = _resolved_hardware(job, queue)
            if hardware:
                row["hardware"].add(hardware)
            row["builds"].add(build.get("number") or build.get("build_number"))
            if state == "passed":
                row["passed"] += 1
            elif state == "soft":
                row["soft_failed"] += 1
            else:
                row["failed"] += 1
            observation = _historical_observation(
                build,
                job,
                group_id,
                pipeline_slug=pipeline_slug,
            )
            if observation.get("job_url"):
                row["linked"] += 1
            if observation.get("retry_evidence"):
                row["retry_evidence"] += 1
            for source, key in (
                ("wall_duration_mins", "wall"),
                ("test_duration_mins", "test"),
                ("wait_mins", "wait"),
                ("end_to_end_mins", "end_to_end"),
            ):
                value = _number(observation.get(source))
                if value is not None:
                    row[key].append(value)
            if len(row["observations"]) < GROUP_HISTORY_LIMIT:
                row["observations"].append(observation)

    catalog = []
    terminal_observations = 0
    linked_observations = 0
    for row in groups.values():
        runs = row["passed"] + row["failed"] + row["soft_failed"]
        incidents = row["failed"] + row["soft_failed"]
        terminal_observations += runs
        linked_observations += row["linked"]
        observations = row["observations"]
        latest = observations[0] if observations else {}
        last_incident = next((item for item in observations if item.get("state") in {"hard", "soft"}), None)
        wall = sorted(row["wall"])
        test = sorted(row["test"])
        wait = sorted(row["wait"])
        end_to_end = sorted(row["end_to_end"])
        preferred = wall or test
        catalog.append({
            "source_pipeline": pipeline_slug,
            "id": row["id"],
            "group_ids": sorted(row["group_ids"]),
            "name": row["name"],
            "raw_names": sorted(row["raw_names"]),
            "hardware": (
                sorted(row["hardware"])[0]
                if len(row["hardware"]) == 1
                else ("mixed" if row["hardware"] else "unknown")
            ),
            "queues": sorted(row["queues"]),
            "build_count": len(row["builds"] - {None}),
            "runs": runs,
            "passed": row["passed"],
            "failed": row["failed"],
            "soft_failed": row["soft_failed"],
            "incident_count": incidents,
            "incident_rate_pct": round(incidents / runs * 100, 1) if runs else 0.0,
            "fail_rate": round(incidents / runs * 100, 1) if runs else 0.0,
            "mixed_outcomes": bool(row["passed"] and incidents),
            "latest_state": latest.get("state") or "unknown",
            "latest_observed_at": latest.get("observed_at"),
            "latest_url": latest.get("job_url") or latest.get("build_url"),
            "last_incident": last_incident,
            "green_streak": _streak(observations),
            "nightly_green_streak": _streak(observations, "nightly"),
            "median_wall_mins": round(median(wall), 1) if wall else None,
            "p90_wall_mins": _percentile(wall, 90),
            "max_wall_mins": round(max(wall), 1) if wall else None,
            "median_test_mins": round(median(test), 1) if test else None,
            "p90_test_mins": _percentile(test, 90),
            "max_test_mins": round(max(test), 1) if test else None,
            "median_wait_mins": round(median(wait), 1) if wait else None,
            "p90_wait_mins": _percentile(wait, 90),
            "max_wait_mins": round(max(wait), 1) if wait else None,
            "median_end_to_end_mins": round(median(end_to_end), 1) if end_to_end else None,
            "p90_end_to_end_mins": _percentile(end_to_end, 90),
            "max_end_to_end_mins": round(max(end_to_end), 1) if end_to_end else None,
            "median_dur": round(median(preferred), 1) if preferred else None,
            "p90_dur": _percentile(preferred, 90),
            "max_dur": round(max(preferred), 1) if preferred else None,
            "duration_basis": "job_wall" if wall else ("test_reported" if test else "unavailable"),
            "observation_count": runs,
            "retained_observation_count": len(observations),
            "history_truncated": runs > len(observations),
            "linked_observation_count": row["linked"],
            "retry_evidence_observation_count": row["retry_evidence"],
            "evidence_type": "mixed_outcome_history" if row["passed"] and incidents else "terminal_history",
            "observations": observations,
        })
    catalog.sort(key=lambda row: (str(row["name"]).lower(), str(row["id"])))
    return catalog, {
        "builds": len(source_builds),
        "terminal_observations": terminal_observations,
        "linked_observations": linked_observations,
        "unknown_observations_excluded": unknown_observations,
    }


def _collector_main_catalog(
    payload: dict,
    pipeline_slug: str = "amd-ci",
) -> tuple[list[dict], dict, dict]:
    """Adapt the collector's strict all-main variant catalog for the UI contract."""
    build_kind = {
        row.get("number"): "nightly" if row.get("is_canonical_nightly") else "main"
        for row in payload.get("builds") or []
    }
    catalog = []
    retry_attempts = []
    recoveries = []
    for source in payload.get("groups") or []:
        if _is_excluded_queue(source.get("queue")):
            continue
        observations = []
        by_job_id = {
            str(row.get("job_id")): row
            for row in source.get("observations") or []
            if row.get("job_id")
        }
        for raw in source.get("observations") or []:
            if not raw.get("eligible_for_reliability"):
                continue
            result = str(raw.get("result") or "")
            state = "soft" if result == "soft_fail" else ("hard" if result == "failed" else result)
            build_number = raw.get("build_number")
            build_url = raw.get("build_url") or _build_url(
                pipeline_slug,
                {"number": build_number},
            )
            job_url = raw.get("job_url") or ""
            if not job_url and (raw.get("job_id") or raw.get("step_id")):
                job_url = _job_url(
                    pipeline_slug,
                    {"number": build_number, "web_url": build_url},
                    {"job_id": raw.get("job_id"), "step_id": raw.get("step_id")},
                )
            row = {
                "source_pipeline": pipeline_slug,
                "group_id": source.get("group_id"),
                "build_number": build_number,
                "build_url": build_url,
                "build_kind": build_kind.get(build_number, "main"),
                "commit": raw.get("build_commit") or "",
                "message": raw.get("build_message") or "",
                "state": state,
                "terminal_state": raw.get("terminal_state") or "",
                "observed_at": raw.get("observed_at") or "",
                "job_url": job_url,
                "step_url": raw.get("step_url") or "",
                "job_id": raw.get("job_id") or "",
                "step_id": raw.get("step_id") or "",
                "queue": source.get("queue") or "",
                "wall_duration_mins": raw.get("wall_completion_mins"),
                "test_duration_mins": raw.get("test_duration_mins"),
                "wait_mins": raw.get("queue_wait_mins"),
                "end_to_end_mins": raw.get("end_to_end_mins"),
                "duration_basis": "job_wall" if raw.get("wall_completion_mins") is not None else (
                    "test_reported" if raw.get("test_duration_mins") is not None else "unavailable"
                ),
            }
            preferred = raw.get("wall_completion_mins")
            if preferred is None:
                preferred = raw.get("test_duration_mins")
            if preferred is not None:
                row["duration_mins"] = preferred
            for key in ("tests", "passed_tests", "failed_tests", "skipped_tests"):
                if isinstance(raw.get(key), (int, float)) and not isinstance(raw.get(key), bool):
                    row[key] = raw[key]
            retry = raw.get("retry_evidence") or {}
            if retry:
                row["retry_evidence"] = retry
                attempt = {
                    "source_pipeline": pipeline_slug,
                    "group_id": source.get("group_id"),
                    "name": source.get("name"),
                    "build_number": build_number,
                    "build_url": build_url,
                    "job_id": raw.get("job_id"),
                    "job_url": job_url,
                    "result": result,
                    "retry_evidence": retry,
                }
                retry_attempts.append(attempt)
                retried_in = str(retry.get("retried_in_job_id") or "")
                recovered = by_job_id.get(retried_in) if retried_in else None
                if recovered and recovered.get("result") == "passed":
                    recovered_job_url = recovered.get("job_url") or ""
                    if not recovered_job_url and (recovered.get("job_id") or recovered.get("step_id")):
                        recovered_job_url = _job_url(
                            pipeline_slug,
                            {"number": build_number, "web_url": build_url},
                            {
                                "job_id": recovered.get("job_id"),
                                "step_id": recovered.get("step_id"),
                            },
                        )
                    recoveries.append({
                        **attempt,
                        "failed_job_id": raw.get("job_id"),
                        "passed_job_id": recovered.get("job_id"),
                        "failed_job_url": job_url,
                        "passed_job_url": recovered_job_url,
                    })
            observations.append({key: value for key, value in row.items() if value not in (None, "")})

        observations.sort(
            key=lambda row: (str(row.get("observed_at") or ""), int(row.get("build_number") or 0)),
            reverse=True,
        )
        runs = int(source.get("denominator") or 0)
        passed = int(source.get("passed") or 0)
        failed = int(source.get("failed") or 0)
        soft_failed = int(source.get("soft_failed") or 0)
        incidents = failed + soft_failed
        latest = observations[0] if observations else {}
        last_incident = next((row for row in observations if row.get("state") in {"hard", "soft"}), None)
        duration = source.get("duration") or {}
        wall = duration.get("wall_completion") or {}
        test = duration.get("test_reported") or {}
        wait = duration.get("queue_wait") or {}
        end_to_end = duration.get("end_to_end") or {}
        preferred = wall if wall.get("samples") else test
        group_ids = sorted({
            str(group_id)
            for group_id in (source.get("group_ids") or [source.get("group_id")])
            if group_id
        })
        catalog.append({
            "source_pipeline": pipeline_slug,
            "id": source.get("group_id"),
            "group_ids": group_ids,
            "name": source.get("name") or source.get("raw_name") or "Unknown group",
            "raw_names": [source.get("raw_name")] if source.get("raw_name") else [],
            "step_key": source.get("step_key") or "",
            "hardware": _resolved_hardware(source, source.get("queue")),
            "queues": [source.get("queue")] if source.get("queue") else [],
            "build_count": len({row.get("build_number") for row in observations if row.get("build_number")}),
            "runs": runs,
            "passed": passed,
            "failed": failed,
            "soft_failed": soft_failed,
            "incident_count": incidents,
            "incident_rate_pct": source.get("incident_rate"),
            "fail_rate": source.get("incident_rate"),
            "mixed_outcomes": bool(passed and incidents),
            "latest_state": latest.get("state") or "unknown",
            "latest_observed_at": latest.get("observed_at"),
            "latest_url": latest.get("job_url") or latest.get("build_url"),
            "last_incident": last_incident,
            "green_streak": _streak(observations),
            "nightly_green_streak": _streak(observations, "nightly"),
            "median_wall_mins": wall.get("p50_mins"),
            "p90_wall_mins": wall.get("p90_mins"),
            "max_wall_mins": wall.get("max_mins"),
            "median_test_mins": test.get("p50_mins"),
            "p90_test_mins": test.get("p90_mins"),
            "max_test_mins": test.get("max_mins"),
            "median_wait_mins": wait.get("p50_mins"),
            "p90_wait_mins": wait.get("p90_mins"),
            "max_wait_mins": wait.get("max_mins"),
            "median_end_to_end_mins": end_to_end.get("p50_mins"),
            "p90_end_to_end_mins": end_to_end.get("p90_mins"),
            "max_end_to_end_mins": end_to_end.get("max_mins"),
            "median_dur": preferred.get("p50_mins"),
            "p90_dur": preferred.get("p90_mins"),
            "max_dur": preferred.get("max_mins"),
            "duration_basis": "job_wall" if wall.get("samples") else (
                "test_reported" if test.get("samples") else "unavailable"
            ),
            "observation_count": runs,
            "retained_observation_count": len(observations),
            "history_truncated": bool(source.get("observations_truncated")),
            "excluded_observation_count": int(source.get("excluded_observations") or 0),
            "linked_observation_count": sum(bool(row.get("job_url")) for row in observations),
            "retry_evidence_observation_count": int(source.get("retry_evidence_observations") or 0),
            "evidence_type": "mixed_outcome_history" if passed and incidents else "terminal_history",
            "observations": observations,
        })
    catalog.sort(key=lambda row: (str(row.get("name") or "").lower(), str(row.get("id") or "")))
    source_denominator = payload.get("denominator") or {}
    counts = {
        "builds": int(((payload.get("cohort") or {}).get("build_count")) or len(payload.get("builds") or [])),
        "terminal_observations": int(source_denominator.get("eligible_observations") or 0),
        "linked_observations": sum(row["linked_observation_count"] for row in catalog),
        "unknown_observations_excluded": int(source_denominator.get("excluded_observations") or 0),
    }
    retry_summary = payload.get("summary") or {}
    retry_analysis = {
        "summary": {
            "builds_evaluated": counts["builds"],
            "builds_with_retries": len({row.get("build_number") for row in retry_attempts}),
            "retry_attempt_count": int(retry_summary.get("retry_evidence_observations") or len(retry_attempts)),
            "failed_then_passed_recovery_count": len(recoveries),
        },
        "retry_attempts": retry_attempts,
        "failed_then_passed_recoveries": recoveries,
        "evidence_type": "explicit_retry_recovery",
    }
    return catalog, counts, retry_analysis


def _normalize_retry_analysis(
    source: Any,
    cohort_build_numbers: set[int],
    pipeline_slug: str = "ci",
    catalog: list[dict] | None = None,
    build_observed_at: dict[int, str] | None = None,
) -> dict:
    """Retain only explicit retry records that belong to the strict cohort."""
    selected = source if isinstance(source, dict) else {}
    source_provenance = selected.get("provenance")
    source_provenance = source_provenance if isinstance(source_provenance, dict) else {}
    if (
        selected.get("available") is not True
        or source_provenance.get("source_pipeline") != pipeline_slug
        or source_provenance.get("complete") is not True
    ):
        return {
            "available": False,
            "summary": {
                "builds_evaluated": len(cohort_build_numbers),
                "builds_with_retries": 0,
                "retry_attempt_count": 0,
                "failed_then_passed_recovery_count": 0,
                "linked_retry_attempt_count": 0,
                "linked_recovery_count": 0,
            },
            "retry_attempts": [],
            "failed_then_passed_recoveries": [],
            "evidence_type": "explicit_retry_recovery",
            "provenance": {
                "source_path": SOURCE_FILES["analytics"],
                "source_key": f"{pipeline_slug}.main_retry_analysis",
                "source_pipeline": pipeline_slug,
                "complete": False,
                "reason": source_provenance.get("reason") or (
                    "Complete explicit retry metadata is unavailable; retained group history was not substituted."
                ),
                "cohort_build_numbers": sorted(cohort_build_numbers),
            },
        }
    evidence_by_job: dict[str, dict] = {}
    for group in catalog or []:
        for observation in group.get("observations") or []:
            job_id = str(observation.get("job_id") or "")
            if not job_id:
                continue
            evidence_by_job[job_id] = {
                "observed_at": observation.get("observed_at"),
                "group_id": group.get("id"),
            }
    build_observed_at = build_observed_at or {}

    attempts = []
    for value in selected.get("retry_attempts") or []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        build_number = _strict_int(row.get("build_number"))
        job_url = row.get("job_url") or row.get("url") or ""
        if (
            build_number not in cohort_build_numbers
            or row.get("source_pipeline") not in (None, "", pipeline_slug)
            or not _pipeline_job_url_matches(job_url, pipeline_slug, build_number)
        ):
            continue
        row["build_url"] = _build_url(pipeline_slug, {"number": build_number})
        row["job_url"] = job_url
        row["url"] = job_url
        row["source_pipeline"] = pipeline_slug
        evidence = evidence_by_job.get(str(row.get("job_id") or "")) or {}
        if not row.get("observed_at"):
            if evidence.get("observed_at"):
                row["observed_at"] = evidence["observed_at"]
                row["timestamp_source"] = "terminal_job"
            elif build_observed_at.get(build_number):
                row["observed_at"] = build_observed_at[build_number]
                row["timestamp_source"] = "completed_build"
        if not row.get("group_id") and evidence.get("group_id"):
            row["group_id"] = evidence["group_id"]
        attempts.append(row)

    recoveries = []
    for value in selected.get("failed_then_passed_recoveries") or []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        build_number = _strict_int(row.get("build_number"))
        failed_url = row.get("failed_url") or row.get("failed_job_url") or ""
        passed_url = row.get("passed_url") or row.get("passed_job_url") or ""
        if (
            build_number not in cohort_build_numbers
            or row.get("source_pipeline") not in (None, "", pipeline_slug)
            or not _pipeline_job_url_matches(failed_url, pipeline_slug, build_number)
            or not _pipeline_job_url_matches(passed_url, pipeline_slug, build_number)
        ):
            continue
        row["build_url"] = _build_url(pipeline_slug, {"number": build_number})
        row["failed_url"] = failed_url
        row["passed_url"] = passed_url
        row["source_pipeline"] = pipeline_slug
        evidence = (
            evidence_by_job.get(str(row.get("passed_job_id") or ""))
            or evidence_by_job.get(str(row.get("failed_job_id") or ""))
            or {}
        )
        if not row.get("observed_at"):
            if evidence.get("observed_at"):
                row["observed_at"] = evidence["observed_at"]
                row["timestamp_source"] = "terminal_job"
            elif build_observed_at.get(build_number):
                row["observed_at"] = build_observed_at[build_number]
                row["timestamp_source"] = "completed_build"
        if not row.get("group_id") and evidence.get("group_id"):
            row["group_id"] = evidence["group_id"]
        recoveries.append(row)

    summary = dict(selected.get("summary") or {})
    summary.setdefault("builds_evaluated", 0)
    summary["retry_attempt_count"] = len(attempts)
    summary["failed_then_passed_recovery_count"] = len(recoveries)
    summary.setdefault(
        "builds_with_retries",
        len({row.get("build_number") for row in attempts if row.get("build_number")}),
    )
    summary["linked_retry_attempt_count"] = sum(bool(row.get("job_url")) for row in attempts)
    summary["linked_recovery_count"] = sum(
        bool(row.get("failed_url") and row.get("passed_url")) for row in recoveries
    )
    return {
        **selected,
        "available": True,
        "summary": summary,
        "retry_attempts": attempts,
        "failed_then_passed_recoveries": recoveries,
        "evidence_type": "explicit_retry_recovery",
        "provenance": {
            "source_path": SOURCE_FILES["analytics"],
            "source_key": f"{pipeline_slug}.main_retry_analysis",
            "source_pipeline": pipeline_slug,
            "complete": True,
            "cohort_build_numbers": sorted(cohort_build_numbers),
            "evidence_kind": "explicit Buildkite retry metadata retained by the collector",
        },
    }


def _cohort_composition(payload: dict, counts: dict, provenance: dict) -> dict:
    source = payload.get("cohort") or provenance.get("cohort") or provenance
    builds = payload.get("builds") or []
    total = int(source.get("build_count") or counts.get("builds") or len(builds))
    nightlies = source.get("canonical_nightly_build_count")
    if nightlies is None:
        nightlies = sum(bool(row.get("is_canonical_nightly")) for row in builds)
    nightlies = int(nightlies or 0)
    other_main = source.get("non_nightly_main_build_count")
    if other_main is None:
        other_main = max(0, total - nightlies)
    other_main = int(other_main or 0)
    return {
        "build_count": total,
        "canonical_nightly_build_count": nightlies,
        "non_nightly_main_build_count": other_main,
        "other_main_build_count": other_main,
        "composition": {
            "all_main_builds": total,
            "canonical_nightlies": nightlies,
            "other_main_builds": other_main,
        },
        "window_days": source.get("window_days"),
    }


def _comparison_platform(row: dict) -> str:
    name = str(row.get("name") or "")
    hardware = str(row.get("hardware") or "").lower()
    queues = [str(queue) for queue in row.get("queues") or []]
    if (
        AMD_PREFIX_RE.match(name)
        or AMD_HARDWARE_RE.match(hardware)
        or any(AMD_QUEUE_RE.match(queue) for queue in queues)
    ):
        return "amd"
    if hardware in CUDA_HARDWARE or any(CUDA_QUEUE_RE.match(queue) for queue in queues):
        return "cuda"
    return "other"


def _comparison_label(value: Any) -> str:
    return _strict_group_label(value)


def _comparison_key(value: Any) -> str:
    return _comparison_label(value).casefold()


def _comparison_variant(row: dict) -> dict:
    return {
        "group_id": row.get("id"),
        "name": row.get("name"),
        "hardware": row.get("hardware"),
        "queues": row.get("queues") or [],
        "runs": int(row.get("runs") or 0),
        "build_count": int(row.get("build_count") or 0),
        "passed": int(row.get("passed") or 0),
        "hard_failed": int(row.get("failed") or 0),
        "soft_failed": int(row.get("soft_failed") or 0),
        "incidents": int(row.get("incident_count") or 0),
        "incident_rate_pct": float(row.get("incident_rate_pct") or 0),
        "mixed_outcomes": bool(row.get("mixed_outcomes")),
        "latest_state": row.get("latest_state") or "unknown",
        "latest_observed_at": row.get("latest_observed_at"),
        "latest_url": row.get("latest_url"),
        "median_duration_mins": row.get("median_dur"),
        "p90_duration_mins": row.get("p90_dur"),
        "max_duration_mins": row.get("max_dur"),
        "duration_basis": row.get("duration_basis") or "unavailable",
        "evidence_ref": row.get("id"),
    }


def _cuda_reference_kind(row: dict) -> str:
    hardware = str(row.get("hardware") or "").lower()
    queues = {str(queue).lower() for queue in row.get("queues") or []}
    explicit = {
        "a100": {"a100_queue"},
        "b200": {"b200-k8s"},
        "h100": {"mithril-h100-pool"},
        "h200": {"h200", "gh200_queue", "h200_18gb", "h200_35gb"},
    }
    if hardware in explicit and len(queues) == 1 and queues <= explicit[hardware]:
        return "explicit_cuda"
    if hardware == "gpu" and len(queues) == 1 and all(
        re.match(r"^gpu_\d+_queue$", queue) for queue in queues
    ):
        return "generic_gpu_reference"
    return "unsupported_reference"


def _comparison_side(
    groups: list[dict],
    cohort_builds: int,
    child_retry_attempts: int,
    recoveries: int,
    retry_involved_attempts: int = 0,
) -> dict:
    runs = sum(int(row.get("runs") or 0) for row in groups)
    passed = sum(int(row.get("passed") or 0) for row in groups)
    hard_failed = sum(int(row.get("failed") or 0) for row in groups)
    soft_failed = sum(int(row.get("soft_failed") or 0) for row in groups)
    incidents = hard_failed + soft_failed
    duration_rows = [row for row in groups if _number(row.get("p90_dur")) is not None]
    slowest = max(
        duration_rows,
        key=lambda row: (float(row.get("p90_dur") or 0), str(row.get("name") or "")),
        default={},
    )
    variants = sorted(
        (_comparison_variant(row) for row in groups),
        key=lambda row: (
            str(row.get("hardware") or ""),
            str(row.get("name") or "").casefold(),
            str(row.get("group_id") or ""),
        ),
    )
    return {
        "variant_count": len(groups),
        "group_ids": [row["group_id"] for row in variants if row.get("group_id")],
        "hardware": sorted({
            str(row.get("hardware")) for row in groups if row.get("hardware")
        }),
        "queues": sorted({
            str(queue)
            for row in groups
            for queue in row.get("queues") or []
            if queue
        }),
        "runs": runs,
        "passed": passed,
        "hard_failed": hard_failed,
        "soft_failed": soft_failed,
        "incidents": incidents,
        "incident_rate_pct": round(incidents / runs * 100, 1) if runs else None,
        "attempts_per_100_builds": round(runs / cohort_builds * 100, 1) if cohort_builds else None,
        "mixed_outcome_variant_count": sum(bool(row.get("mixed_outcomes")) for row in groups),
        "retry_attempts": child_retry_attempts,
        "child_retry_attempts": child_retry_attempts,
        "retry_involved_attempts": retry_involved_attempts,
        "retry_frequency_pct": round(child_retry_attempts / runs * 100, 1) if runs else None,
        "recovered_chains": recoveries,
        "retry_recovery_rate_pct": round(recoveries / child_retry_attempts * 100, 1) if child_retry_attempts else None,
        "worst_p90_duration_mins": slowest.get("p90_dur"),
        "slowest_group_id": slowest.get("id"),
        "duration_basis": slowest.get("duration_basis") or "unavailable",
        "variants": variants,
    }


def _platform_comparison(
    catalog: list[dict],
    retry_analysis: dict,
    cohort_builds: int,
) -> dict:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    exact_identity: dict[str, tuple[str, str]] = {}
    for row in catalog:
        platform = _comparison_platform(row)
        if platform not in {"amd", "cuda"}:
            continue
        key = _comparison_key(row.get("name"))
        if not key:
            continue
        grouped[(platform, key)].append(row)
        for identity in [row.get("name"), *(row.get("raw_names") or [])]:
            if identity:
                exact_identity[str(identity).casefold()] = (platform, key)

    def retry_identity(row: dict) -> tuple[str, str] | None:
        name = str(row.get("name") or "")
        exact = exact_identity.get(name.casefold())
        if exact:
            return exact
        key = _comparison_key(name)
        platform = "amd" if AMD_PREFIX_RE.match(name) else "cuda"
        return (platform, key) if grouped.get((platform, key)) else None

    retry_involved_counts: Counter[tuple[str, str]] = Counter()
    child_retry_counts: Counter[tuple[str, str]] = Counter()
    recovery_counts: Counter[tuple[str, str]] = Counter()
    if retry_analysis.get("available") is True:
        for row in retry_analysis.get("retry_attempts") or []:
            if identity := retry_identity(row):
                retry_involved_counts[identity] += 1
                if row.get("retry_source"):
                    child_retry_counts[identity] += 1
        for row in retry_analysis.get("failed_then_passed_recoveries") or []:
            if identity := retry_identity(row):
                recovery_counts[identity] += 1

    amd_keys = sorted(key for platform, key in grouped if platform == "amd")
    rows = []
    for key in amd_keys:
        amd_groups = grouped[("amd", key)]
        cuda_groups = grouped.get(("cuda", key), [])
        amd = _comparison_side(
            amd_groups,
            cohort_builds,
            child_retry_counts[("amd", key)],
            recovery_counts[("amd", key)],
            retry_involved_counts[("amd", key)],
        )
        cuda = _comparison_side(
            cuda_groups,
            cohort_builds,
            child_retry_counts[("cuda", key)],
            recovery_counts[("cuda", key)],
            retry_involved_counts[("cuda", key)],
        )
        label = _comparison_label(amd_groups[0].get("name"))
        match_issues = []
        if not cuda_groups:
            match_issues.append("no_cuda_equivalent")
        if len(amd_groups) > 1:
            match_issues.append("shared_amd_base_label")
        if len(cuda_groups) > 1:
            match_issues.append("ambiguous_cuda_variants")
        if cuda_groups and any(
            _cuda_reference_kind(group) != "explicit_cuda" for group in cuda_groups
        ):
            match_issues.append("generic_or_unsupported_gpu_reference")
        if HARDWARE_WORD_RE.search(label):
            match_issues.append("hardware_specific_label")
        comparison_eligible = not match_issues
        match_status = "exact_cuda_pair" if comparison_eligible else match_issues[0]
        rows.append({
            "id": hashlib.sha1(f"ci-amd-cuda:{key}".encode()).hexdigest()[:20],
            "label": label,
            "comparison_key": key,
            "match_status": match_status,
            "match_issues": match_issues,
            "comparison_eligible": comparison_eligible,
            "amd": amd,
            "cuda": cuda,
            "incident_rate_delta_pp": (
                round(float(amd["incident_rate_pct"]) - float(cuda["incident_rate_pct"]), 1)
                if comparison_eligible and amd["incident_rate_pct"] is not None and cuda["incident_rate_pct"] is not None
                else None
            ),
            "retry_frequency_delta_pp": (
                round(float(amd["retry_frequency_pct"]) - float(cuda["retry_frequency_pct"]), 1)
                if comparison_eligible and amd["retry_frequency_pct"] is not None and cuda["retry_frequency_pct"] is not None
                else None
            ),
            "worst_p90_delta_mins": (
                round(float(amd["worst_p90_duration_mins"]) - float(cuda["worst_p90_duration_mins"]), 1)
                if comparison_eligible and amd["worst_p90_duration_mins"] is not None and cuda["worst_p90_duration_mins"] is not None
                else None
            ),
        })
    rows.sort(
        key=lambda row: (
            -(float(row["amd"].get("incident_rate_pct") or 0)),
            str(row.get("label") or "").casefold(),
        )
    )
    label_matched = [row for row in rows if row["cuda"]["variant_count"]]
    matched = [row for row in rows if row["comparison_eligible"]]
    amd_groups = [row for (platform, _), values in grouped.items() if platform == "amd" for row in values]
    matched_cuda_groups = [
        row
        for item in matched
        for row in grouped.get(("cuda", item["comparison_key"]), [])
    ]
    amd_child_retries = sum(child_retry_counts[("amd", key)] for key in amd_keys)
    amd_retry_involved = sum(retry_involved_counts[("amd", key)] for key in amd_keys)
    amd_recoveries = sum(recovery_counts[("amd", key)] for key in amd_keys)
    matched_keys = {row["comparison_key"] for row in matched}
    comparable_amd_groups = [
        row
        for item in matched
        for row in grouped.get(("amd", item["comparison_key"]), [])
    ]
    comparable_amd_child_retries = sum(
        child_retry_counts[("amd", key)] for key in matched_keys
    )
    comparable_amd_retry_involved = sum(
        retry_involved_counts[("amd", key)] for key in matched_keys
    )
    comparable_amd_recoveries = sum(
        recovery_counts[("amd", key)] for key in matched_keys
    )
    cuda_child_retries = sum(child_retry_counts[("cuda", key)] for key in matched_keys)
    cuda_retry_involved = sum(retry_involved_counts[("cuda", key)] for key in matched_keys)
    cuda_recoveries = sum(recovery_counts[("cuda", key)] for key in matched_keys)
    amd_totals = _comparison_side(
        amd_groups, cohort_builds, amd_child_retries, amd_recoveries, amd_retry_involved
    )
    comparable_amd_totals = _comparison_side(
        comparable_amd_groups,
        cohort_builds,
        comparable_amd_child_retries,
        comparable_amd_recoveries,
        comparable_amd_retry_involved,
    )
    cuda_totals = _comparison_side(
        matched_cuda_groups,
        cohort_builds,
        cuda_child_retries,
        cuda_recoveries,
        cuda_retry_involved,
    )
    for totals in (amd_totals, comparable_amd_totals, cuda_totals):
        totals.pop("group_ids", None)
        totals.pop("variants", None)
    return {
        "available": bool(rows),
        "source_pipeline": "ci",
        "cohort_build_count": cohort_builds,
        "summary": {
            "amd_base_group_count": len(rows),
            "amd_variant_count": len(amd_groups),
            "label_matched_base_group_count": len(label_matched),
            "matched_base_group_count": len(matched),
            "comparable_base_group_count": len(matched),
            "review_required_base_group_count": len(rows) - len(matched),
            "unmatched_amd_base_group_count": len(rows) - len(label_matched),
            "matched_cuda_variant_count": len(matched_cuda_groups),
            "amd": amd_totals,
            "comparable_amd": comparable_amd_totals,
            "matched_cuda": cuda_totals,
        },
        "matching": {
            "amd_rule": "AMD: prefix, MI hardware, or amd_mi* queue",
            "cuda_rule": "NVIDIA hardware or known CUDA queue; Intel GPU, CPU, NPU, and unknown groups excluded",
            "equivalence_rule": "case-insensitive exact label after removing only AMD:/mi*_n wrapper decoration; comparative deltas require one AMD variant, one explicit NVIDIA variant, and hardware-neutral wording",
            "scope": "completed upstream ci branch=main builds in the strict retained cohort",
            "frequency_unit": "terminal attempts per 100 cohort builds; child retry share uses retry_source rows over terminal attempts",
        },
        "rows": rows,
    }


def _reliability(pipeline_analytics: Any, pipeline_slug: str = "ci") -> dict:
    pipeline_analytics = pipeline_analytics if isinstance(pipeline_analytics, dict) else {}
    collector_payload = pipeline_analytics.get("all_main_reliability") or {}
    strict_available = False
    collector_present = isinstance(collector_payload, dict) and bool(collector_payload)
    if collector_present and _collector_main_is_strict(collector_payload, pipeline_slug):
        strict_available = True
        catalog, counts, _derived_retry_analysis = _collector_main_catalog(
            collector_payload,
            pipeline_slug=pipeline_slug,
        )
        retry_source = pipeline_analytics.get("main_retry_analysis") or {}
        cohort_provenance = {
            "cohort": collector_payload.get("cohort") or {},
            "denominator": collector_payload.get("denominator") or {},
            "provenance": collector_payload.get("provenance") or {},
        }
    else:
        catalog, counts = _group_catalog([], pipeline_slug=pipeline_slug)
        retry_source = {}
        cohort_provenance = {
            "unavailable": True,
            "invalid_collector_cohort": collector_present,
            "note": (
                "Collector all-main payload failed strict exhaustive pipeline, branch, state, cohort, or URL validation."
                if collector_present
                else "Collector did not expose an exhaustive strict all-main cohort; nightly data was not substituted."
            ),
        }
    def summary(row: dict) -> dict:
        return {
            key: value
            for key, value in row.items()
            if key not in {"observations", "raw_names", "last_incident"}
        } | {
            "evidence_ref": row["id"],
            "last_incident": row.get("last_incident"),
        }

    candidates = [summary(row) for row in catalog if row["mixed_outcomes"]]
    candidates.sort(
        key=lambda row: (row["incident_rate_pct"], row["incident_count"], row["runs"], row["name"]),
        reverse=True,
    )
    latency = [summary(row) for row in catalog if row.get("median_dur") is not None]
    by_median = sorted(latency, key=lambda row: (float(row.get("median_dur") or 0), row["name"]), reverse=True)
    by_p90 = sorted(latency, key=lambda row: (float(row.get("p90_dur") or 0), row["name"]), reverse=True)
    by_max = sorted(latency, key=lambda row: (float(row.get("max_dur") or 0), row["name"]), reverse=True)
    cohort_build_numbers = {
        number
        for row in (collector_payload.get("builds") or [])
        if isinstance(row, dict) and (number := _strict_int(row.get("number"))) is not None
    } if strict_available else set()
    cohort_build_observed_at = {
        number: str(
            row.get("finished_at")
            or row.get("started_at")
            or row.get("created_at")
            or ""
        )
        for row in (collector_payload.get("builds") or [])
        if isinstance(row, dict)
        and (number := _strict_int(row.get("number"))) is not None
    } if strict_available else {}
    retry_analysis = _normalize_retry_analysis(
        retry_source,
        cohort_build_numbers,
        pipeline_slug=pipeline_slug,
        catalog=catalog,
        build_observed_at=cohort_build_observed_at,
    )
    platform_comparison = _platform_comparison(
        catalog,
        retry_analysis,
        counts["builds"],
    ) if pipeline_slug == "ci" else {
        "available": False,
        "source_pipeline": pipeline_slug,
        "cohort_build_count": counts["builds"],
        "summary": {},
        "matching": {},
        "rows": [],
    }
    composition = _cohort_composition(
        collector_payload if strict_available else {},
        counts,
        cohort_provenance,
    )
    return {
        "available": strict_available,
        "source_pipeline": pipeline_slug,
        "cohort": {
            "id": "main",
            "available": strict_available,
            "label": (
                f"All completed {pipeline_slug} branch=main builds"
                if strict_available
                else f"Strict {pipeline_slug} branch=main reliability unavailable"
            ),
            **composition,
            "build_numbers": sorted(cohort_build_numbers),
            "provenance": cohort_provenance,
        },
        "evidence_definitions": {
            "mixed_outcome_history": (
                f"At least one passed and one incident observation in the all-main {pipeline_slug} cohort; "
                "this is a flaky candidate, not proof that a retry recovered."
            ),
            "explicit_retry_recovery": "Buildkite retry metadata linking a failed attempt to a passed retry.",
            "terminal_history": "Only passed, hard-failed, or soft-failed jobs count in the denominator.",
        },
        "denominator": {
            "unit": f"terminal {pipeline_slug} branch=main job observations",
            "builds": counts["builds"],
            "groups": len(catalog),
            "observations": counts["terminal_observations"],
            "linked_observations": counts["linked_observations"],
            "unknown_observations_excluded": counts["unknown_observations_excluded"],
        },
        "summary": {
            "group_count": len(catalog),
            "mixed_outcome_group_count": len(candidates),
            "stable_group_count": sum(row["incident_count"] == 0 for row in catalog),
            "persistent_incident_group_count": sum(row["passed"] == 0 and row["incident_count"] > 0 for row in catalog),
        },
        "group_catalog": catalog,
        "flaky_candidates": candidates,
        "latency_rankings": {
            "by_median_duration": by_median,
            "by_p90_duration": by_p90,
            "by_max_duration": by_max,
        },
        "retry_analysis": retry_analysis,
        "platform_comparison": platform_comparison,
    }


MATRIX_STATE_RANK = {"passed": 0, "unknown": 1, "soft": 2, "hard": 3}


def _matrix_evidence_item(
    matrix: dict,
    row: dict,
    architecture: str,
    cell: dict,
    *,
    definition: dict | None = None,
) -> dict:
    definition = definition or cell
    raw_state = definition.get("latest_state") or cell.get("latest_state") or "unknown"
    return {
        "architecture": architecture,
        "state": _historical_state({"state": raw_state}),
        "raw_state": raw_state,
        "build_number": (
            definition.get("latest_build_number")
            or cell.get("latest_build_number")
            or (matrix.get("source") or {}).get("latest_build_number")
        ),
        "url": definition.get("latest_url") or cell.get("latest_url") or "",
        "source": "amd_matrix",
        "source_pipeline": "amd-ci",
        "matrix_row_id": row.get("id"),
        "matrix_title": row.get("title") or row.get("canonical_title"),
        "definition_label": (
            definition.get("label")
            or cell.get("primary_label")
            or row.get("title")
            or row.get("canonical_title")
        ),
    }


def _merge_matrix_evidence(bundles: list[dict], observed_at: Any) -> dict:
    evidence_by_identity: dict[tuple[Any, ...], dict] = {}
    definition_labels = set()
    matrix_row_ids = set()
    alias_kinds = set()
    for bundle in bundles:
        definition_labels.update(bundle.get("_definition_labels") or [])
        matrix_row_ids.update(bundle.get("_matrix_row_ids") or [])
        alias_kinds.update(bundle.get("_alias_kinds") or [])
        for item in bundle.get("evidence") or []:
            url = str(item.get("url") or "")
            identity = (
                url,
                item.get("matrix_row_id"),
                item.get("architecture"),
                item.get("definition_label"),
            )
            previous = evidence_by_identity.get(identity)
            if (
                previous is None
                or MATRIX_STATE_RANK.get(str(item.get("state") or "unknown"), 1)
                > MATRIX_STATE_RANK.get(str(previous.get("state") or "unknown"), 1)
            ):
                evidence_by_identity[identity] = item
    evidence = sorted(
        evidence_by_identity.values(),
        key=lambda item: (
            -MATRIX_STATE_RANK.get(str(item.get("state") or "unknown"), 1),
            str(item.get("architecture") or ""),
            str(item.get("definition_label") or ""),
            str(item.get("url") or ""),
        ),
    )
    state = max(
        (str(item.get("state") or "unknown") for item in evidence),
        key=lambda value: MATRIX_STATE_RANK.get(value, 1),
        default="unknown",
    )
    build_numbers = [
        number
        for item in evidence
        if (number := _strict_int(item.get("build_number"))) is not None
    ]
    return {
        "state": state,
        "build_number": max(build_numbers, default=None),
        "observed_at": observed_at,
        "source_pipeline": "amd-ci",
        "evidence": evidence,
        "_definition_labels": sorted(
            str(label) for label in definition_labels if str(label or "").strip()
        ),
        "_matrix_row_ids": sorted(
            str(row_id) for row_id in matrix_row_ids if str(row_id or "").strip()
        ),
        "_alias_kinds": sorted(
            str(kind) for kind in alias_kinds if str(kind or "").strip()
        ),
    }


def _matrix_evidence(
    matrix: dict,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Index canonical rows and exact YAML aliases without losing collisions."""
    exact_bundles_by_key: dict[str, list[dict]] = defaultdict(list)
    canonical_bundles_by_key: dict[str, list[dict]] = defaultdict(list)

    def add_bundle(
        labels: set[str],
        evidence: list[dict],
        row: dict,
        alias_kind: str,
    ) -> None:
        if not evidence:
            return
        bundle = _merge_matrix_evidence(
            [{
                "evidence": evidence,
                "_definition_labels": {
                    item.get("definition_label") for item in evidence
                },
                "_matrix_row_ids": {row.get("id")},
                "_alias_kinds": {alias_kind},
            }],
            matrix.get("generated_at"),
        )
        for label in labels:
            key = _target_match_key(label)
            if key:
                destination = (
                    exact_bundles_by_key
                    if alias_kind == "yaml_label"
                    else canonical_bundles_by_key
                )
                destination[key].append(bundle)

    for row in matrix.get("rows") or []:
        canonical_evidence = []
        canonical_title = str(
            row.get("canonical_title") or row.get("title") or ""
        )
        row_title = str(row.get("title") or "")
        canonical_labels = {canonical_title}
        has_variant_labels = False
        for architecture, cell in (row.get("cells") or {}).items():
            if not isinstance(cell, dict) or not cell.get("exists"):
                continue
            canonical_evidence.append(
                _matrix_evidence_item(matrix, row, architecture, cell)
            )
            for variant in cell.get("variants") or []:
                if not isinstance(variant, dict):
                    continue
                has_variant_labels = True
                variant_evidence = [
                    _matrix_evidence_item(
                        matrix,
                        row,
                        architecture,
                        cell,
                        definition=variant,
                    )
                ]
                variant_labels = {
                    str(variant.get("label") or ""),
                    *(str(label or "") for label in variant.get("aliases") or []),
                }
                add_bundle(variant_labels, variant_evidence, row, "yaml_label")
                for entry in variant.get("entries") or []:
                    if not isinstance(entry, dict):
                        continue
                    entry_labels = {
                        str(entry.get("label") or ""),
                        *(str(label or "") for label in entry.get("aliases") or []),
                    }
                    add_bundle(
                        entry_labels,
                        [
                            _matrix_evidence_item(
                                matrix,
                                row,
                                architecture,
                                cell,
                                definition=entry,
                            )
                        ],
                        row,
                        "yaml_label",
                    )
        add_bundle(
            canonical_labels,
            canonical_evidence,
            row,
            "canonical_title",
        )
        legacy_title = row_title or canonical_title
        if not has_variant_labels and legacy_title:
            add_bundle(
                {legacy_title},
                canonical_evidence,
                row,
                "yaml_label",
            )

    return (
        {
            key: _merge_matrix_evidence(bundles, matrix.get("generated_at"))
            for key, bundles in exact_bundles_by_key.items()
        },
        {
            key: _merge_matrix_evidence(bundles, matrix.get("generated_at"))
            for key, bundles in canonical_bundles_by_key.items()
        },
    )


def _assessment(
    latest: dict,
    reliability: dict,
    runtime_resolution: dict | None = None,
) -> str:
    state = latest.get("state") or "unknown"
    if state == "hard":
        return "failing_now"
    if state == "soft":
        return "soft_failing_now"
    if state != "passed":
        resolution_status = (runtime_resolution or {}).get("status")
        if resolution_status == "no_amd_definition":
            return "no_matching_amd_definition"
        if resolution_status == "stale_target_alias":
            return "target_mapping_needs_review"
        if resolution_status == "ambiguous":
            return "ambiguous_amd_mapping"
        if resolution_status == "not_observed":
            return "no_recent_amd_observation"
        return "no_recent_amd_signal"
    if reliability.get("available") is not True or not int(reliability.get("runs") or 0):
        return "passed_without_history"
    if reliability.get("incident_count"):
        return "passed_with_incident_history"
    return "consistently_passing"


def _target_history_summary(histories: list[dict]) -> dict:
    """Aggregate matched variants by build, with incident precedence."""
    buckets: dict[tuple[str, Any], list[dict]] = defaultdict(list)
    for variant in histories:
        for observation in variant.get("observations") or []:
            if not isinstance(observation, dict):
                continue
            build_number = observation.get("build_number")
            key = (
                "build" if build_number not in (None, "") else "time",
                build_number if build_number not in (None, "") else observation.get("observed_at"),
            )
            buckets[key].append(observation)
    precedence = {"passed": 0, "unknown": 1, "soft": 2, "hard": 3}
    timeline = []
    for rows in buckets.values():
        representative = max(
            rows,
            key=lambda row: (
                precedence.get(str(row.get("state") or "unknown"), 1),
                str(row.get("observed_at") or ""),
            ),
        )
        state = str(representative.get("state") or "unknown")
        timeline.append({
            "state": state,
            "build_number": representative.get("build_number"),
            "build_kind": representative.get("build_kind") or "main",
            "observed_at": max(str(row.get("observed_at") or "") for row in rows),
            "job_url": representative.get("job_url"),
            "build_url": representative.get("build_url"),
        })
    timeline.sort(
        key=lambda row: (
            str(row.get("observed_at") or ""),
            _strict_int(row.get("build_number")) or 0,
        ),
        reverse=True,
    )

    def streak(build_kind: str | None = None) -> int:
        count = 0
        for row in timeline:
            if build_kind and row.get("build_kind") != build_kind:
                continue
            if row.get("state") != "passed":
                break
            count += 1
        return count

    latest = timeline[0] if timeline else {}
    return {
        "latest_state": latest.get("state"),
        "latest_observed_at": latest.get("observed_at"),
        "latest_url": latest.get("job_url") or latest.get("build_url"),
        "green_streak": streak(),
        "nightly_green_streak": streak("nightly"),
    }


def _definition_label_key(value: Any) -> str:
    return MULTISPACE_RE.sub(
        " ",
        str(value or "").strip().replace(r"\%N", "%N"),
    ).casefold()


def _commit_from_definition_url(value: Any) -> str:
    match = re.search(r"/([0-9a-f]{40})(?:/|$)", str(value or ""), re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _runtime_resolution_context(matrix: dict, definition_parity: dict) -> dict:
    matrix_url = str((matrix.get("source") or {}).get("yaml_url") or "")
    parity_source = definition_parity.get("source") or {}
    parity_url = str(
        parity_source.get("amd_definition_url")
        or parity_source.get("commit_url")
        or ""
    )
    matrix_commit = _commit_from_definition_url(matrix_url)
    parity_commit = str(parity_source.get("commit_sha") or "").lower()
    if matrix_commit and parity_commit:
        source_alignment = (
            "same_commit" if matrix_commit == parity_commit else "different_commits"
        )
    else:
        source_alignment = "unavailable"
    return {
        "source_commits": {
            "amd_matrix": matrix_commit,
            "definition_parity": parity_commit,
        },
        "source_alignment": source_alignment,
        "source_urls": {
            "amd_matrix": matrix_url,
            "definition_parity": parity_url,
        },
    }


def _public_matrix_evidence(bundle: dict) -> dict:
    return {
        key: value
        for key, value in bundle.items()
        if not str(key).startswith("_")
    }


def _candidate_source_labels(group: dict, candidates: dict) -> list[str]:
    target_id = str(group.get("id"))
    labels = []
    for row in candidates.get("rows") or []:
        if str(row.get("target_id")) != target_id:
            continue
        if row.get("decision") != "canonical":
            continue
        label = str(row.get("label") or "").strip()
        if label:
            labels.append(label)
        for shard in row.get("runtime_shards") or []:
            shard_label = str((shard or {}).get("label") or "").strip()
            if shard_label:
                labels.append(shard_label)
    reviewed_label = str(group.get("label") or "").strip()
    return list(dict.fromkeys([*labels, reviewed_label]))


def _parity_rows_for_labels(
    labels: list[str],
    parity_rows: list[dict],
    *,
    label_field: str,
) -> tuple[list[dict], list[dict]]:
    exact_index: dict[str, list[dict]] = defaultdict(list)
    folded_index: dict[str, list[dict]] = defaultdict(list)
    for row in parity_rows:
        if not isinstance(row, dict):
            continue
        label = row.get(label_field)
        if not label:
            continue
        exact_index[_definition_label_key(label)].append(row)
        folded_index[hardware_fold_key(label)].append(row)

    exact = []
    for label in labels:
        exact.extend(exact_index.get(_definition_label_key(label), []))
    if exact:
        return list({id(row): row for row in exact}.values()), []

    folded = []
    ambiguous = []
    for label in labels:
        matches = folded_index.get(hardware_fold_key(label), [])
        identities = {
            (
                str(row.get("identity_key") or ""),
                str(row.get("amd_label") or row.get("label") or ""),
            )
            for row in matches
        }
        if len(identities) == 1:
            folded.extend(matches)
        elif len(identities) > 1:
            ambiguous.extend(matches)
    return (
        list({id(row): row for row in folded}.values()),
        list({id(row): row for row in ambiguous}.values()),
    )


def _parity_match_shadowed_by_exact_commands(
    match: dict,
    definition_parity: dict,
) -> bool:
    """Reject a metadata identity that steals an exact command/title twin."""
    try:
        similarity = float(match.get("command_similarity"))
    except (TypeError, ValueError):
        return False
    if similarity >= 0.999999:
        return False
    amd_commands = tuple(str(command) for command in match.get("amd_commands") or [])
    amd_label = _definition_label_key(match.get("amd_label"))
    if not amd_commands or not amd_label:
        return False
    return any(
        _definition_label_key(row.get("label")) == amd_label
        and tuple(str(command) for command in row.get("commands") or [])
        == amd_commands
        for row in definition_parity.get("nvidia_only") or []
        if isinstance(row, dict)
    )


def _resolve_runtime_matrix(
    group: dict,
    candidates: dict,
    exact_matrix_by_key: dict[str, dict],
    canonical_matrix_by_key: dict[str, dict],
    matrix: dict,
    definition_parity: dict,
    context: dict,
) -> tuple[dict, dict]:
    label = str(group.get("label") or "")
    direct_key = _target_match_key(label)
    direct = exact_matrix_by_key.get(direct_key)
    canonical_candidate = canonical_matrix_by_key.get(direct_key)
    if direct:
        latest = _public_matrix_evidence(direct)
        status = "matched" if latest.get("state") != "unknown" else "not_observed"
        method = (
            "shard_template"
            if SHARD_TEMPLATE_SUFFIX_RE.search(_strict_group_label(label))
            else "exact_matrix_label"
        )
        resolution = {
            "status": status,
            "method": method,
            "reason": (
                "Matched the reviewed target to exact AMD nightly matrix evidence."
                if status == "matched"
                else "The AMD definition matched, but the latest matrix has no terminal result."
            ),
            "target_identity_key": direct_key,
            "amd_definition_labels": direct.get("_definition_labels") or [],
            "candidate_count": len(direct.get("_matrix_row_ids") or []),
            "mapping_quality": "exact_label",
            "command_similarity_pct": None,
            **context,
        }
        return latest, resolution

    source_labels = _candidate_source_labels(group, candidates)
    parity_matches, ambiguous_matches = _parity_rows_for_labels(
        source_labels,
        definition_parity.get("matches") or [],
        label_field="nvidia_label",
    )
    shadowed_parity_matches = [
        row
        for row in parity_matches
        if _parity_match_shadowed_by_exact_commands(row, definition_parity)
    ]
    parity_matches = [
        row for row in parity_matches if row not in shadowed_parity_matches
    ]
    resolved = []
    for parity_row in parity_matches:
        amd_label = str(parity_row.get("amd_label") or "")
        bundle = exact_matrix_by_key.get(_target_match_key(amd_label))
        if bundle:
            resolved.append((parity_row, bundle))
    if resolved:
        merged = _merge_matrix_evidence(
            [bundle for _row, bundle in resolved],
            matrix.get("generated_at"),
        )
        latest = _public_matrix_evidence(merged)
        identities = sorted({
            str(row.get("identity_key") or "")
            for row, _bundle in resolved
            if row.get("identity_key")
        })
        amd_labels = sorted({
            str(label)
            for row, bundle in resolved
            for label in [
                row.get("amd_label"),
                *(bundle.get("_definition_labels") or []),
            ]
            if str(label or "").strip()
        })
        matrix_row_ids = {
            str(row_id)
            for _row, bundle in resolved
            for row_id in (bundle.get("_matrix_row_ids") or [])
            if str(row_id or "").strip()
        }
        status = "matched" if latest.get("state") != "unknown" else "not_observed"
        similarities = []
        for row, _bundle in resolved:
            try:
                similarities.append(float(row.get("command_similarity")))
            except (TypeError, ValueError):
                continue
        minimum_similarity = min(similarities, default=None)
        exact_commands = (
            minimum_similarity is not None
            and minimum_similarity >= 0.999999
        )
        if exact_commands:
            matched_reason = (
                "Resolved through exact-command definition parity and linked "
                "to exact AMD nightly evidence."
            )
            mapping_quality = "exact_commands"
        elif minimum_similarity is not None:
            matched_reason = (
                "Resolved through definition identity and linked to exact AMD "
                "nightly evidence; the paired command lists are only partially "
                "equivalent."
            )
            mapping_quality = "partial_commands"
        else:
            matched_reason = (
                "Resolved through definition parity and linked to exact AMD "
                "nightly evidence; command similarity is unavailable."
            )
            mapping_quality = "unavailable"
        resolution = {
            "status": status,
            "method": "definition_parity",
            "reason": (
                matched_reason
                if status == "matched"
                else "Definition parity resolved the AMD step, but its latest "
                "matrix result is not terminal."
            ),
            "target_identity_key": ", ".join(identities),
            "amd_definition_labels": amd_labels,
            "candidate_count": len(matrix_row_ids),
            "mapping_quality": mapping_quality,
            "command_similarity_pct": (
                round(minimum_similarity * 100, 1)
                if minimum_similarity is not None
                else None
            ),
            **context,
        }
        return latest, resolution

    empty_latest = {
        "state": "unknown",
        "build_number": None,
        "observed_at": matrix.get("generated_at"),
        "source_pipeline": "amd-ci",
        "evidence": [],
    }
    if parity_matches:
        amd_labels = sorted({
            str(row.get("amd_label") or "")
            for row in parity_matches
            if row.get("amd_label")
        })
        return empty_latest, {
            "status": "stale_target_alias",
            "method": "definition_parity",
            "reason": (
                "A current definition-parity alias exists, but its AMD label is "
                "absent from the build-pinned nightly matrix."
            ),
            "target_identity_key": ", ".join(sorted({
                str(row.get("identity_key") or "")
                for row in parity_matches
                if row.get("identity_key")
            })),
            "amd_definition_labels": amd_labels,
            "candidate_count": len(amd_labels),
            **context,
        }
    if shadowed_parity_matches:
        return empty_latest, {
            "status": "no_amd_definition",
            "method": "definition_parity",
            "reason": (
                "The apparent AMD identity is reserved by an exact-command "
                "upstream definition; this target has no one-to-one AMD mapping."
            ),
            "target_identity_key": ", ".join(sorted({
                str(row.get("identity_key") or "")
                for row in shadowed_parity_matches
                if row.get("identity_key")
            })),
            "amd_definition_labels": [],
            "candidate_count": 0,
            **context,
        }
    if ambiguous_matches:
        return empty_latest, {
            "status": "ambiguous",
            "method": "definition_parity",
            "reason": (
                "Multiple definition identities match this reviewed label; "
                "no AMD result was selected."
            ),
            "target_identity_key": "",
            "amd_definition_labels": sorted({
                str(row.get("amd_label") or "")
                for row in ambiguous_matches
                if row.get("amd_label")
            }),
            "candidate_count": len(ambiguous_matches),
            **context,
        }

    nvidia_only, nvidia_only_ambiguous = _parity_rows_for_labels(
        source_labels,
        definition_parity.get("nvidia_only") or [],
        label_field="label",
    )
    if nvidia_only:
        identities = sorted({
            str(row.get("identity_key") or "")
            for row in nvidia_only
            if row.get("identity_key")
        })
        return empty_latest, {
            "status": "no_amd_definition",
            "method": "definition_parity",
            "reason": (
                "The current upstream definition has no one-to-one AMD "
                "definition in the parity snapshot."
            ),
            "target_identity_key": ", ".join(identities),
            "amd_definition_labels": [],
            "candidate_count": 0,
            **context,
        }
    if nvidia_only_ambiguous:
        return empty_latest, {
            "status": "ambiguous",
            "method": "definition_parity",
            "reason": (
                "Multiple upstream-only definitions match this reviewed label; "
                "the AMD mapping needs review."
            ),
            "target_identity_key": "",
            "amd_definition_labels": [],
            "candidate_count": len(nvidia_only_ambiguous),
            **context,
        }
    if not (
        definition_parity.get("matches")
        or definition_parity.get("nvidia_only")
    ):
        return empty_latest, {
            "status": "not_observed",
            "method": "unresolved",
            "reason": (
                "No matching AMD matrix evidence was published, and definition "
                "parity is unavailable."
            ),
            "target_identity_key": "",
            "amd_definition_labels": [],
            "candidate_count": 0,
            **context,
        }
    return empty_latest, {
        "status": "stale_target_alias",
        "method": "unresolved",
        "reason": (
            (
                "Only a lossy canonical matrix title matched; an exact YAML "
                "label or definition-parity identity is required."
            )
            if canonical_candidate
            else (
                "The reviewed label did not resolve to a current "
                "upstream-to-AMD definition identity."
            )
        ),
        "target_identity_key": "",
        "amd_definition_labels": [],
        "candidate_count": 0,
        **context,
    }


def _gating(
    targets: dict,
    candidates: dict,
    matrix: dict,
    capacity: dict,
    reliability: dict,
    definition_parity: dict | None = None,
) -> dict:
    definition_parity = definition_parity or {}
    groups = list(targets.get("groups") or [])
    target_summary = dict(targets.get("summary") or {})
    candidate_summary = dict(candidates.get("summary") or {})
    matrix_summary = dict(matrix.get("summary") or {})
    matrix_cells = int(matrix_summary.get("hardware_cells") or 0)
    exact_matrix_by_key, canonical_matrix_by_key = _matrix_evidence(matrix)
    resolution_context = _runtime_resolution_context(matrix, definition_parity)
    history_pipeline = str(reliability.get("source_pipeline") or "ci")
    catalog_by_key: dict[str, list[dict]] = defaultdict(list)
    numbered_catalog_by_base: dict[str, list[dict]] = defaultdict(list)
    for row in reliability.get("group_catalog") or []:
        catalog_key = _target_match_key(row.get("name"))
        catalog_by_key[catalog_key].append(row)
        numbered = re.fullmatch(r"(?P<base>.+)\s+(?P<shard>\d+)", catalog_key)
        if numbered:
            numbered_catalog_by_base[numbered.group("base")].append(row)
    parity_by_id: dict[Any, list[dict]] = defaultdict(list)
    for row in candidates.get("rows") or []:
        if row.get("target_id") and row.get("url"):
            parity_by_id[row["target_id"]].append({
                "label": row.get("label"),
                "state": row.get("state"),
                "url": row.get("url"),
                "source": "upstream_parity",
                "source_pipeline": "ci",
            })

    def enrich(group: dict, reviewed: bool) -> dict:
        key = _target_match_key(group.get("label"))
        histories = list(catalog_by_key.get(key) or [])
        if SHARD_TEMPLATE_SUFFIX_RE.search(
            _strict_group_label(group.get("label"))
        ):
            histories.extend(numbered_catalog_by_base.get(key) or [])
        histories = list({id(row): row for row in histories}.values())
        history = max(
            histories,
            key=lambda row: str(row.get("latest_observed_at") or ""),
            default={},
        )
        target_history = _target_history_summary(histories)
        runs = sum(int(row.get("runs") or 0) for row in histories)
        passed = sum(int(row.get("passed") or 0) for row in histories)
        failed = sum(int(row.get("failed") or 0) for row in histories)
        soft_failed = sum(int(row.get("soft_failed") or 0) for row in histories)
        incidents = failed + soft_failed
        history_available = (
            reliability.get("available") is True
            and bool(histories)
            and runs > 0
        )
        latest_incident = max(
            (row.get("last_incident") for row in histories if row.get("last_incident")),
            key=lambda row: str(row.get("observed_at") or ""),
            default=None,
        )
        latest, runtime_resolution = _resolve_runtime_matrix(
            group,
            candidates,
            exact_matrix_by_key,
            canonical_matrix_by_key,
            matrix,
            definition_parity,
            resolution_context,
        )
        aggregate_group_ids = sorted({
            str(group_id)
            for row in histories
            for group_id in (row.get("group_ids") or [row.get("id")])
            if group_id
        })
        reliability_summary = {
            "available": history_available,
            "source_pipeline": history_pipeline,
            "id": history.get("id"),
            "group_ids": aggregate_group_ids,
            "variant_count": len(histories),
            "runs": runs,
            "passed": passed,
            "failed": failed,
            "soft_failed": soft_failed,
            "incident_count": incidents,
            "incident_rate_pct": round(incidents / runs * 100, 1) if runs else None,
            "green_streak": target_history.get("green_streak") or 0,
            "nightly_green_streak": target_history.get("nightly_green_streak") or 0,
            "latest_state": target_history.get("latest_state"),
            "latest_observed_at": target_history.get("latest_observed_at"),
            "latest_url": target_history.get("latest_url"),
            "variants": [
                {
                    key: row.get(key)
                    for key in (
                        "id", "group_ids", "hardware", "queues", "runs",
                        "incident_rate_pct", "latest_state", "latest_url",
                    )
                    if row.get(key) not in (None, "", [])
                }
                for row in histories
            ],
        }
        evidence = list(parity_by_id.get(group.get("id"), []))
        linked_urls = {str(row.get("url")) for row in evidence if row.get("url")}
        for variant in histories:
            observation = (variant.get("observations") or [{}])[0]
            url = observation.get("job_url") or observation.get("build_url") or variant.get("latest_url")
            if not url or str(url) in linked_urls:
                continue
            linked_urls.add(str(url))
            evidence.append({
                "label": variant.get("name") or group.get("label"),
                "state": observation.get("state") or variant.get("latest_state") or "unknown",
                "build_number": observation.get("build_number"),
                "build_url": observation.get("build_url"),
                "observed_at": observation.get("observed_at") or variant.get("latest_observed_at"),
                "url": url,
                "source": "upstream_main_history",
                "source_pipeline": history_pipeline,
                "group_id": variant.get("id"),
            })
        return {
            "id": group.get("id"),
            "label": group.get("label") or "Unknown group",
            "area": group.get("area") or "other",
            "reviewed_plan": {
                "status": "included" if reviewed else "observed_outside_reviewed_plan",
                "label": "Reviewed target" if reviewed else "Outside reviewed target list",
                "source_path": "config/vllm_amd_gating_targets.json",
                "source_url": GATING_CONFIG_URL,
                "note": group.get("note") or "",
            },
            "latest_amd_result": latest,
            "runtime_resolution": runtime_resolution,
            "main_reliability": reliability_summary,
            "nightly_green_streak": target_history.get("nightly_green_streak") or 0,
            "last_incident": latest_incident,
            "assessment": _assessment(
                latest,
                reliability_summary,
                runtime_resolution,
            ),
            "evidence": evidence,
        }

    canonical_keys = {_target_match_key(row.get("label")) for row in groups}
    reviewed_groups = [enrich(group, True) for group in groups]
    active_extras = []
    seen_extra_keys = set()
    for group in capacity.get("groups") or []:
        if group.get("in_capacity_scope") is False:
            continue
        key = _target_match_key(group.get("label"))
        if not key or key in canonical_keys or key in seen_extra_keys:
            continue
        seen_extra_keys.add(key)
        active_extras.append(enrich({
            "id": f"active-{len(active_extras) + 1}",
            "label": group.get("label") or "Unknown active group",
            "area": group.get("area") or "other",
            "note": "Observed in AMD capacity configuration but not in the reviewed target list.",
        }, False))
    active_groups = reviewed_groups + active_extras
    assessments = Counter(str(row.get("assessment") or "unknown") for row in active_groups)
    observed_states = Counter(
        str((row.get("latest_amd_result") or {}).get("state") or "unknown")
        for row in active_groups
    )
    runtime_resolutions = Counter(
        str((row.get("runtime_resolution") or {}).get("status") or "unknown")
        for row in active_groups
    )
    return {
        "definitions": {
            "reviewed_plan": "Intent from the reviewed target configuration; not an ownership assignment.",
            "latest_amd_result": "Latest exact AMD matrix evidence resolved for this group.",
            "runtime_resolution": (
                "How the reviewed label resolved to AMD evidence, or why no "
                "one-to-one runtime result was selected."
            ),
            "main_reliability": "Terminal outcomes across all retained upstream ci branch=main builds.",
            "historical_evidence": "Reliability, streaks, incidents, and retained execution references come from upstream ci.",
            "upstream_parity": "Upstream ci evidence is the historical reliability reference.",
        },
        "denominators": {
            "reviewed_targets": {"value": len(groups), "unit": "reviewed target groups"},
            "active_targets": {"value": len(active_groups), "unit": "reviewed plus observed configured groups"},
            "candidate_decisions": {
                "value": len(candidates.get("rows") or []),
                "unit": "latest parity audit rows",
            },
            "matrix_group_counts": {
                "value": int(matrix_summary.get("unique_groups") or len(matrix.get("rows") or [])),
                "unit": "configured AMD groups",
            },
            "matrix_cell_states": {"value": matrix_cells, "unit": "configured AMD hardware cells"},
            # Compatibility alias retained for older clients; the unit is explicit.
            "target_signal_counts": {"value": len(groups), "unit": "reviewed target groups"},
        },
        "reviewed_config_summary": target_summary,
        "target_summary": target_summary,
        "target_groups": reviewed_groups,
        "active_target_summary": {
            "target_group_count": len(active_groups),
            "canonical_group_count": len(groups),
            "active_outside_canonical_count": len(active_extras),
            "by_assessment": dict(sorted(assessments.items())),
            "by_latest_amd_state": dict(sorted(observed_states.items())),
            "by_runtime_resolution": dict(sorted(runtime_resolutions.items())),
        },
        "active_target_groups": active_groups,
        "candidate_summary": candidate_summary,
        "matrix_summary": matrix_summary,
    }


def _job_source_counts(queue_jobs: dict) -> dict:
    return dict(sorted(Counter(
        str(job.get("source") or "unknown")
        for state in ("pending", "running")
        for job in queue_jobs.get(state) or []
    ).items()))


def _filter_queue_snapshot(snapshot: dict) -> dict:
    if not snapshot:
        return {}
    row = dict(snapshot)
    queues = {
        name: stats
        for name, stats in (snapshot.get("queues") or {}).items()
        if not _is_excluded_queue(name)
    }
    row["queues"] = queues
    for total, metric in (
        ("total_waiting", "waiting"),
        ("total_running", "running"),
        ("total_zombie_waiting", "zombie_waiting"),
        ("total_zombie_running", "zombie_running"),
    ):
        if total in row or metric in {"waiting", "running"}:
            row[total] = sum(int((stats or {}).get(metric) or 0) for stats in queues.values())
    return row


def _filter_queue_jobs(queue_jobs: dict) -> dict:
    result = dict(queue_jobs)
    for state in ("pending", "running"):
        result[state] = [
            job for job in queue_jobs.get(state) or []
            if not _is_excluded_queue(job.get("queue") or job.get("q"))
        ]
    return result


def _compact_history_snapshot(snapshot: dict) -> dict:
    """Project history to chart/detail fields without duplicating verbose contracts."""
    queues = {}
    for name, source in (snapshot.get("queues") or {}).items():
        if _is_excluded_queue(name) or not isinstance(source, dict):
            continue
        compact_fields = (
            "waiting", "running", "scheduled", "total",
            "zombie_waiting", "zombie_running",
            "connected_agents", "connected_agents_source",
            "count_source", "count_source_family",
            "p50_wait", "p50_wait_source", "p75_wait", "p75_wait_source",
            "p90_wait", "p90_wait_source", "p95_wait", "p95_wait_source",
            "p99_wait", "p99_wait_source", "avg_wait", "avg_wait_source",
            "max_wait", "max_wait_source", "wait_source", "wait_source_family",
            "wait_sample_count", "sample_count", "official_wait_source",
            "sample_wait_source", "metrics_ts", "current_wait",
        )
        row = {
            key: source[key]
            for key in compact_fields
            if key in source
        }
        for key, value in source.items():
            if (
                key.endswith("_source")
                or "source_family" in key
            ):
                row.setdefault(key, value)
        # Presence in the source queue map is itself an observation. Retain
        # idle rows so zero load remains distinct from an unobserved queue.
        queues[name] = row
    sources = snapshot.get("sources") or {}
    history_provenance = sources.get("history_provenance") or {}
    return {
        "ts": snapshot.get("ts"),
        "schema_version": snapshot.get("schema_version"),
        "total_waiting": snapshot.get("total_waiting", 0),
        "total_running": snapshot.get("total_running", 0),
        "total_zombie_waiting": snapshot.get("total_zombie_waiting", 0),
        "total_zombie_running": snapshot.get("total_zombie_running", 0),
        "tracked_queue_count": len(queues),
        "queues": queues,
        "sources": {
            key: sources.get(key)
            for key in ("counts", "agents", "official_wait", "sampled_wait", "waits")
            if sources.get(key) is not None
        } | ({"history_provenance": history_provenance} if history_provenance else {}),
    }


def _queue(snapshot: dict, queue_jobs: dict, history: list[dict]) -> dict:
    snapshot = _filter_queue_snapshot(snapshot)
    queue_jobs = _filter_queue_jobs(queue_jobs)
    history = [_compact_history_snapshot(_filter_queue_snapshot(row)) for row in history]
    counts_only = sum(
        (
            str((row.get("provenance") or {}).get("mode") or row.get("history_mode") or "")
            == "counts_only"
            or bool(((row.get("sources") or {}).get("history_provenance") or {}).get("migration"))
        )
        for row in history
    )
    return {
        "snapshot": snapshot,
        "queue_jobs": queue_jobs,
        "history": history,
        "history_summary": {
            "snapshot_count": len(history),
            "first_observed_at": history[0].get("ts") if history else None,
            "last_observed_at": history[-1].get("ts") if history else None,
            "counts_only_snapshot_count": counts_only,
            "source_path": SOURCE_FILES["queue_timeseries"],
        },
        "provenance": {
            "source_paths": {
                "history": SOURCE_FILES["queue_timeseries"],
                "jobs": SOURCE_FILES["queue_jobs"],
            },
            "snapshot": {
                "path": SOURCE_FILES["queue_timeseries"],
                "source_path": SOURCE_FILES["queue_timeseries"],
                "timestamp": snapshot.get("ts"),
                "run_id": snapshot.get("run_id"),
                "sources": snapshot.get("sources") or snapshot.get("provenance") or {},
                "evidence_kind": "published queue aggregate",
            },
            "history": {
                "path": SOURCE_FILES["queue_timeseries"],
                "source_path": SOURCE_FILES["queue_timeseries"],
                "snapshot_count": len(history),
                "counts_only_snapshot_count": counts_only,
                "evidence_kind": "published queue aggregate history",
            },
            "jobs": {
                "path": SOURCE_FILES["queue_jobs"],
                "source_path": SOURCE_FILES["queue_jobs"],
                "timestamp": queue_jobs.get("ts"),
                "source_counts": _job_source_counts(queue_jobs),
                "evidence_kind": "published retained job records",
            },
        },
    }


def _is_amd_queue(value: Any) -> bool:
    name = str(value or "").strip().lower()
    return (
        (name == "amd-cpu" or name.startswith("amd_"))
        and not _is_excluded_queue(name)
    )


def _nonnegative_count(value: Any) -> int:
    number = _number(value)
    return max(0, int(number)) if number is not None else 0


def _omni_history_scope(queue_rows: list[dict]) -> dict:
    waiting_total = 0
    running_total = 0
    waiting_attributed = 0
    running_attributed = 0
    waiting_observed = 0
    running_observed = 0
    waiting_supported = False
    running_supported = False

    for stats in queue_rows:
        waiting_total += _nonnegative_count(stats.get("waiting"))
        running_total += _nonnegative_count(stats.get("running"))
        waiting_split = stats.get("waiting_by_workload")
        running_split = stats.get("running_by_workload")
        if isinstance(waiting_split, dict):
            waiting_attributed += sum(
                _nonnegative_count(value) for value in waiting_split.values()
            )
            if "omni" in waiting_split:
                waiting_supported = True
                waiting_observed += _nonnegative_count(waiting_split.get("omni"))
        if isinstance(running_split, dict):
            running_attributed += sum(
                _nonnegative_count(value) for value in running_split.values()
            )
            if "omni" in running_split:
                running_supported = True
                running_observed += _nonnegative_count(running_split.get("omni"))

    waiting_status = "unavailable"
    running_status = "unavailable"
    if queue_rows and waiting_supported:
        waiting_status = "complete" if waiting_attributed == waiting_total else "partial"
    if queue_rows and running_supported:
        running_status = "complete" if running_attributed == running_total else "partial"
    return {
        "waiting_supported": waiting_supported,
        "running_supported": running_supported,
        "waiting_observed": waiting_observed,
        "running_observed": running_observed,
        "waiting_attributed": waiting_attributed,
        "running_attributed": running_attributed,
        "waiting_total": waiting_total,
        "running_total": running_total,
        "waiting_attribution": waiting_status,
        "running_attribution": running_status,
    }


def _omni_history(
    history: list[dict],
    allowed_queues: set[str] | None = None,
) -> dict:
    """Retain explicit Omni occupancy only for the configured AMD queues."""
    allowed_queues = allowed_queues or {
        str(name)
        for snapshot in history
        for name in (snapshot.get("queues") or {})
        if _is_amd_queue(name)
    }
    points = []
    for snapshot in history:
        amd_rows = [
            stats
            for name, stats in (snapshot.get("queues") or {}).items()
            if name in allowed_queues and isinstance(stats, dict)
        ]
        amd = _omni_history_scope(amd_rows)
        if not any((amd["waiting_supported"], amd["running_supported"])):
            continue
        points.append({
            "ts": snapshot.get("ts"),
            "amd": amd,
        })

    return {
        "points": points,
        "summary": {
            "snapshot_count": len(points),
            "first_observed_at": points[0]["ts"] if points else None,
            "last_observed_at": points[-1]["ts"] if points else None,
            "complete_waiting_snapshot_count": sum(
                point["amd"]["waiting_attribution"] == "complete"
                for point in points
            ),
            "complete_running_snapshot_count": sum(
                point["amd"]["running_attribution"] == "complete"
                for point in points
            ),
        },
        "provenance": {
            "source_path": SOURCE_FILES["queue_timeseries"],
            "count_semantics": (
                "Observed Omni workload counts only; partial attribution is a "
                "lower bound and is never inferred from aggregate queue totals."
            ),
            "scope": "configured standard AMD queues only; perf-eval queues are excluded",
            "queues": sorted(allowed_queues),
        },
    }


def _omni(
    queue_snapshot: dict,
    queue_jobs: dict,
    queue_history: list[dict],
    heuristic: dict,
    issue_state: dict,
    workload_mapping: dict | None = None,
    capacity: dict | None = None,
) -> dict:
    workload_mapping = workload_mapping or {}
    capacity = capacity or {}
    mapping_scope = workload_mapping.get("scope") or {}
    allowed_queues = {
        str(name)
        for name in mapping_scope.get("queues") or []
        if str(name) and not _is_excluded_queue(name)
    }
    if not allowed_queues:
        allowed_queues = {
            str(row.get("id"))
            for row in capacity.get("queues") or []
            if isinstance(row, dict)
            and row.get("monitored") is not False
            and row.get("id")
            and not _is_excluded_queue(row.get("id"))
        }
    omni_pipelines = {
        str(name)
        for name in (
            (mapping_scope.get("workload_pipelines") or {}).get("omni")
            or ["vllm-omni-amd-ci"]
        )
        if str(name)
    }
    jobs = {
        state: [
            job for job in queue_jobs.get(state) or []
            if str(job.get("pipeline") or "") in omni_pipelines
            and str(job.get("queue") or job.get("q") or "") in allowed_queues
        ]
        for state in ("pending", "running")
    }
    queue_rows = [
        stats
        for name, stats in (queue_snapshot.get("queues") or {}).items()
        if name in allowed_queues and isinstance(stats, dict)
    ]
    attribution = _omni_history_scope(queue_rows)
    waiting_by_queue: dict[str, int] = {}
    running_by_queue: dict[str, int] = {}
    for queue_name in sorted(allowed_queues):
        waiting = sum(
            not job.get("analysis_excluded")
            and str(job.get("queue") or job.get("q") or "") == queue_name
            for job in jobs["pending"]
        )
        running = sum(
            not job.get("analysis_excluded")
            and str(job.get("queue") or job.get("q") or "") == queue_name
            for job in jobs["running"]
        )
        if waiting:
            waiting_by_queue[queue_name] = waiting
        if running:
            running_by_queue[queue_name] = running

    ledger = {
        "waiting": sum(not job.get("analysis_excluded") for job in jobs["pending"]),
        "running": sum(not job.get("analysis_excluded") for job in jobs["running"]),
    }
    count_basis = {"waiting": "exact_pipeline_active_job_ledger", "running": "exact_pipeline_active_job_ledger"}
    waiting = ledger["waiting"]
    running = ledger["running"]

    trigger = int(heuristic.get("trigger") or 0)
    healthy = int(heuristic.get("healthy") or 0)
    if trigger and waiting >= trigger:
        status = "surge"
    elif waiting > healthy:
        status = "elevated"
    else:
        status = "healthy"
    return {
        "status": status,
        "current": {
            "waiting": waiting,
            "running": running,
            "waiting_by_queue": waiting_by_queue,
            "running_by_queue": running_by_queue,
            "ledger": ledger,
            "count_basis": count_basis,
            "attribution": attribution,
        },
        "heuristic_thresholds": heuristic,
        "current_jobs": jobs,
        "history": _omni_history(queue_history, allowed_queues),
        "mapping_history": workload_mapping,
        "scope": {
            "label": "Omni CI",
            "queues": sorted(allowed_queues),
            "pipelines": sorted(omni_pipelines),
            "excluded_queue_classes": mapping_scope.get("excluded_queue_classes") or ["perf_eval"],
            "count_semantics": "exact pipeline identity plus exact configured queue allowlist",
        },
        "issue_state": issue_state,
        "provenance": {
            "queue_snapshot_ts": queue_snapshot.get("ts"),
            "queue_jobs_ts": queue_jobs.get("ts"),
            "source_paths": {
                "queue_aggregates": SOURCE_FILES["queue_timeseries"],
                "queue_jobs": SOURCE_FILES["queue_jobs"],
                "heuristic": SOURCE_FILES["omni_heuristic"],
                "issue_state": SOURCE_FILES["omni_issue_state"],
                "mapping_history": SOURCE_FILES["workload_mapping"],
            },
            "sources": {
                "queue_aggregates": {
                    "path": SOURCE_FILES["queue_timeseries"],
                    "timestamp": queue_snapshot.get("ts"),
                    "evidence_kind": "published workload aggregate",
                },
                "queue_jobs": {
                    "path": SOURCE_FILES["queue_jobs"],
                    "timestamp": queue_jobs.get("ts"),
                    "source_counts": _job_source_counts(queue_jobs),
                    "evidence_kind": "published retained job records",
                },
                "heuristic": {
                    "path": SOURCE_FILES["omni_heuristic"],
                    "timestamp": heuristic.get("generated_at"),
                    "evidence_kind": "published threshold configuration",
                },
                "issue_state": {
                    "path": SOURCE_FILES["omni_issue_state"],
                    "timestamp": issue_state.get("last_snapshot_ts"),
                    "evidence_kind": "published issue watcher state",
                },
                "mapping_history": {
                    "path": SOURCE_FILES["workload_mapping"],
                    "timestamp": workload_mapping.get("generated_at"),
                    "evidence_kind": "published unique-job AMD mapping aggregate",
                },
            },
        },
    }


def _queue_capacity_catalog(capacity: dict) -> dict[str, dict]:
    """Normalize the central AMD queue catalog for projection joins."""
    catalog: dict[str, dict] = {}
    for raw in capacity.get("queues") or []:
        if not isinstance(raw, dict):
            continue
        queue_id = str(raw.get("id") or "").strip()
        if not queue_id:
            continue
        catalog[queue_id] = {
            "id": queue_id,
            "label": raw.get("label") or queue_id.removeprefix("amd_"),
            "family": raw.get("family") or "unknown",
            "provider": raw.get("provider"),
            "gpus_per_job": max(1, int(raw.get("gpus_per_job") or 1)),
            "max_concurrent_jobs": max(
                0,
                int(
                    raw.get("future_max_concurrent_jobs")
                    if raw.get("future_max_concurrent_jobs") is not None
                    else raw.get("max_concurrent_jobs")
                    or raw.get("max_agents")
                    or 0
                ),
            ),
            "gpu_capacity": max(
                0,
                int(
                    raw.get("future_gpu_capacity")
                    if raw.get("future_gpu_capacity") is not None
                    else raw.get("gpu_capacity")
                    or 0
                ),
            ),
            "monitored": raw.get("monitored") is not False,
            "capacity_eligible": raw.get("capacity_eligible") is not False,
            "lifecycle": raw.get("lifecycle") or "active",
        }
    return catalog


def _normalize_architecture_preference(
    architecture_preference: list[str] | tuple[str, ...] | None,
) -> tuple[str, ...]:
    """Return a complete, de-duplicated ordering of supported matrix cells."""
    requested = architecture_preference or AMD_TARGET_DEFAULT_PREFERENCE
    normalized = []
    for architecture in (*requested, *AMD_TARGET_DEFAULT_PREFERENCE):
        value = str(architecture or "").lower()
        if value in AMD_TARGET_ARCHITECTURES and value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _matrix_cell_queue_ids(cell: dict) -> list[str]:
    queue_ids = []
    for variant in cell.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        label = str(variant.get("agent_pool") or "").strip()
        if not label:
            continue
        queue_ids.append(label if label.startswith("amd_") else f"amd_{label}")
    return queue_ids


def _matrix_cell_is_feasible(cell: dict, queue_catalog: dict[str, dict]) -> bool:
    """Require an explicit cell whose every declared variant has an active queue."""
    queue_ids = _matrix_cell_queue_ids(cell)
    return bool(
        queue_ids
        and all(
            queue_id in queue_catalog
            and queue_catalog[queue_id].get("capacity_eligible") is True
            for queue_id in queue_ids
        )
    )


def _target_placement_demand(
    amd_test_matrix: dict,
    queue_catalog: dict[str, dict],
    architecture_preference: list[str] | tuple[str, ...] | None,
) -> dict:
    """Place each semantic group on the first feasible explicitly defined cell."""
    preference = _normalize_architecture_preference(architecture_preference)
    demand: dict[str, dict] = {
        queue_id: {
            **queue,
            "group_ids": set(),
            "jobs": 0,
            "gpu_slots": 0,
        }
        for queue_id, queue in queue_catalog.items()
        if queue.get("capacity_eligible") is True
    }
    definition_counts = Counter()
    feasible_definition_counts = Counter()
    selected_architectures = Counter()
    selected_groups = 0
    unassigned_groups = 0
    skipped_unsupported_cells = 0

    for index, row in enumerate(amd_test_matrix.get("rows") or []):
        if not isinstance(row, dict):
            continue
        cells = row.get("cells") or {}
        for architecture in AMD_TARGET_ARCHITECTURES:
            cell = cells.get(architecture)
            if not isinstance(cell, dict) or cell.get("exists") is not True:
                continue
            definition_counts[architecture] += 1
            if _matrix_cell_is_feasible(cell, queue_catalog):
                feasible_definition_counts[architecture] += 1

        selected_architecture = None
        selected_cell = None
        for architecture in preference:
            cell = cells.get(architecture)
            if not isinstance(cell, dict) or cell.get("exists") is not True:
                continue
            if not _matrix_cell_is_feasible(cell, queue_catalog):
                skipped_unsupported_cells += 1
                continue
            selected_architecture = architecture
            selected_cell = cell
            break
        if selected_cell is None or selected_architecture is None:
            unassigned_groups += 1
            continue

        group_id = str(row.get("id") or f"matrix-row-{index}")
        for variant in selected_cell.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            label = str(variant.get("agent_pool") or "").strip()
            queue_id = label if label.startswith("amd_") else f"amd_{label}"
            queue = demand[queue_id]
            try:
                jobs = max(1, int(variant.get("parallelism") or 1))
            except (TypeError, ValueError):
                jobs = 1
            queue["group_ids"].add(group_id)
            queue["jobs"] += jobs
            queue["gpu_slots"] += jobs * queue["gpus_per_job"]
        selected_groups += 1
        selected_architectures[selected_architecture] += 1

    matrix_group_count = sum(
        isinstance(row, dict) for row in amd_test_matrix.get("rows") or []
    )
    return {
        "architecture_preference": list(preference),
        "demand": demand,
        "selected_groups": selected_groups,
        "unassigned_groups": unassigned_groups,
        "coverage": {
            "matrix_group_count": matrix_group_count,
            "assigned_group_count": selected_groups,
            "unassigned_group_count": unassigned_groups,
            "complete": bool(
                matrix_group_count and selected_groups == matrix_group_count
            ),
            "architecture_definitions": {
                architecture: int(definition_counts[architecture])
                for architecture in AMD_TARGET_ARCHITECTURES
            },
            "feasible_architecture_definitions": {
                architecture: int(feasible_definition_counts[architecture])
                for architecture in AMD_TARGET_ARCHITECTURES
            },
            "selected_groups_by_architecture": {
                architecture: int(selected_architectures[architecture])
                for architecture in AMD_TARGET_ARCHITECTURES
            },
            "skipped_unsupported_cell_count": skipped_unsupported_cells,
        },
    }


def _placement_strategy_profile(
    strategy_id: str,
    label: str,
    placement: dict,
) -> dict:
    """Publish exact queue/family totals for a matrix-cell selection strategy."""
    queue_rows = []
    for queue_id, row in sorted((placement.get("demand") or {}).items()):
        groups = len(row.get("group_ids") or [])
        jobs = int(row.get("jobs") or 0)
        gpu_slots = int(row.get("gpu_slots") or 0)
        queue_rows.append({
            "id": queue_id,
            "label": row.get("label") or queue_id.removeprefix("amd_"),
            "family": row.get("family") or "unknown",
            "gpus_per_job": int(row.get("gpus_per_job") or 1),
            "groups": groups,
            "jobs": jobs,
            "gpu_slots": gpu_slots,
        })

    family_rows = []
    for architecture in AMD_TARGET_ARCHITECTURES:
        family_name = architecture.upper()
        rows = [row for row in queue_rows if row["family"] == family_name]
        family_rows.append({
            "family": family_name,
            "groups": sum(row["groups"] for row in rows),
            "jobs": sum(row["jobs"] for row in rows),
            "gpu_slots": sum(row["gpu_slots"] for row in rows),
        })
    totals = {
        "groups": int(placement.get("selected_groups") or 0),
        "jobs": sum(row["jobs"] for row in queue_rows),
        "gpu_slots": sum(row["gpu_slots"] for row in queue_rows),
    }
    coverage = dict(placement.get("coverage") or {})
    mi355_definitions = int(
        (coverage.get("architecture_definitions") or {}).get("mi355") or 0
    )
    return {
        "id": strategy_id,
        "label": label,
        "architecture_preference": list(
            placement.get("architecture_preference") or []
        ),
        "selection_method": "first_feasible_explicit_matrix_cell",
        "totals": totals,
        "queues": queue_rows,
        "families": family_rows,
        "coverage": coverage,
        "limitation": (
            f"Only {mi355_definitions}/{coverage.get('matrix_group_count') or 0} "
            "semantic groups publish an MI355 definition. Every placement uses "
            "an explicit matrix cell and its declared variants, parallelism, and "
            "queue widths; unsupported cells are skipped and no compatibility "
            "or cross-family migration is inferred."
        ),
    }


def _target_runtime_estimate(
    amd_test_matrix: dict,
    amd_analytics: dict,
    queue_catalog: dict[str, dict],
    *,
    window_days: int = 14,
    architecture_preference: list[str] | tuple[str, ...] | None = None,
) -> dict:
    """Estimate occupied work as the sum of per-command-job wall-time medians."""
    selected_step_ids: set[str] = set()
    preference = _normalize_architecture_preference(architecture_preference)
    for row in amd_test_matrix.get("rows") or []:
        cells = row.get("cells") or {}
        cell = next((
            cells[architecture]
            for architecture in preference
            if isinstance(cells.get(architecture), dict)
            and cells[architecture].get("exists") is True
            and _matrix_cell_is_feasible(cells[architecture], queue_catalog)
        ), None)
        for variant in (cell or {}).get("variants") or []:
            query = parse_qs(urlparse(str(variant.get("latest_url") or "")).query)
            step_id = str((query.get("sid") or [""])[0]).strip()
            if step_id:
                selected_step_ids.add(step_id)

    source = amd_test_matrix.get("source") or {}
    try:
        anchor_number = int(source.get("latest_build_number"))
    except (TypeError, ValueError):
        anchor_number = 0
    builds = [
        row for row in amd_analytics.get("builds") or []
        if isinstance(row, dict)
    ]
    anchor = next(
        (
            row for row in builds
            if int(row.get("number") or 0) == anchor_number
        ),
        None,
    )
    if not anchor or not selected_step_ids:
        return {
            "available": False,
            "window_days": window_days,
            "reason": "anchor build or semantic-matrix step IDs unavailable",
        }

    selected_jobs = [
        job for job in anchor.get("jobs") or []
        if str(job.get("step_id") or "") in selected_step_ids
        and str(job.get("q") or "") in queue_catalog
    ]
    selected_keys = {
        (str(job.get("name") or ""), str(job.get("q") or ""))
        for job in selected_jobs
        if job.get("name") and job.get("q")
    }
    try:
        window_end = datetime.fromisoformat(
            str(source.get("latest_build_date"))
        ).date()
    except (TypeError, ValueError):
        return {
            "available": False,
            "window_days": window_days,
            "reason": "matrix anchor date unavailable",
        }
    window_start = window_end - timedelta(days=max(1, window_days) - 1)
    window_builds = [
        build for build in builds
        if window_start.isoformat()
        <= str(build.get("date") or "")[:10]
        <= window_end.isoformat()
    ]

    samples: dict[tuple[str, str], list[float]] = defaultdict(list)
    missing_duration_observations = 0
    for build in window_builds:
        for job in build.get("jobs") or []:
            key = (str(job.get("name") or ""), str(job.get("q") or ""))
            if key not in selected_keys:
                continue
            raw_duration = job.get("wall_completion_mins")
            if raw_duration is None:
                raw_duration = job.get("dur")
            duration_value = _number(raw_duration)
            if duration_value is None or duration_value < 0:
                missing_duration_observations += 1
                continue
            samples[key].append(float(duration_value))

    per_queue: dict[str, dict] = defaultdict(
        lambda: {
            "jobs": 0,
            "sampled_jobs": 0,
            "median_agent_hours": 0.0,
            "median_gpu_hours": 0.0,
        }
    )
    sample_counts = []
    for name, queue_id in sorted(selected_keys):
        queue = queue_catalog[queue_id]
        row = per_queue[queue_id]
        row["jobs"] += 1
        durations = samples.get((name, queue_id)) or []
        if not durations:
            continue
        median_minutes = median(durations)
        row["sampled_jobs"] += 1
        row["median_agent_hours"] += median_minutes / 60
        row["median_gpu_hours"] += (
            median_minutes * int(queue["gpus_per_job"]) / 60
        )
        sample_counts.append(len(durations))

    normalized_queues = {
        queue_id: {
            **row,
            "median_agent_hours": round(row["median_agent_hours"], 2),
            "median_gpu_hours": round(row["median_gpu_hours"], 2),
        }
        for queue_id, row in sorted(per_queue.items())
    }
    return {
        "available": bool(sample_counts),
        "method": "sum_of_per_command_job_wall_time_medians",
        "window_days": window_days,
        "window_start_date": window_start.isoformat(),
        "window_end_date": window_end.isoformat(),
        "canonical_builds": len(window_builds),
        "selected_step_ids": len(selected_step_ids),
        "selected_jobs": len(selected_keys),
        "sampled_jobs": len(sample_counts),
        "missing_job_medians": len(selected_keys) - len(sample_counts),
        "missing_duration_observations": missing_duration_observations,
        "samples_per_job": {
            "minimum": min(sample_counts) if sample_counts else 0,
            "median": median(sample_counts) if sample_counts else 0,
            "maximum": max(sample_counts) if sample_counts else 0,
        },
        "median_agent_hours": round(
            sum(row["median_agent_hours"] for row in per_queue.values()),
            2,
        ),
        "median_gpu_hours": round(
            sum(row["median_gpu_hours"] for row in per_queue.values()),
            2,
        ),
        "queues": normalized_queues,
        "semantics": (
            "Each selected command-job key uses its median Buildkite "
            "finished_at-started_at wall time across canonical AMD nightlies. "
            "Queue wait and superseded retry attempts are excluded; timeouts "
            "and terminal failure states remain in the medians."
        ),
    }


def _historical_capacity_load(
    workload_mapping: dict,
    queue_catalog: dict[str, dict],
    future_capacity_gpus: int,
) -> dict:
    """Summarize completed GPU work without treating averages as burst demand."""
    window = workload_mapping.get("window") or {}
    totals = workload_mapping.get("totals") or {}
    try:
        window_start = datetime.fromisoformat(
            str(window.get("start_date"))
        ).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        window_start = None
    generated_at = _parse_dt(workload_mapping.get("generated_at"))
    elapsed_hours = (
        (generated_at - window_start).total_seconds() / 3600
        if generated_at and window_start and generated_at > window_start
        else float(int(window.get("days") or 0) * 24)
    )
    eligible_gpu_hours = 0.0
    retiring_gpu_hours = 0.0
    total_gpu_hours = 0.0
    for workload in ("omni", "main"):
        workload_row = totals.get(workload) or {}
        total_gpu_hours += float(workload_row.get("gpu_hours") or 0)
        for queue_id, queue_row in (workload_row.get("by_queue") or {}).items():
            gpu_hours = float((queue_row or {}).get("gpu_hours") or 0)
            queue = queue_catalog.get(queue_id) or {}
            if queue.get("capacity_eligible") is True:
                eligible_gpu_hours += gpu_hours
            else:
                retiring_gpu_hours += gpu_hours
    observed_average_gpus = total_gpu_hours / elapsed_hours if elapsed_hours > 0 else None
    eligible_average_gpus = eligible_gpu_hours / elapsed_hours if elapsed_hours > 0 else None
    return {
        "available": bool(elapsed_hours > 0 and workload_mapping.get("generated_at")),
        "window_days": window.get("days"),
        "window_start_date": window.get("start_date"),
        "window_end_date": window.get("end_date"),
        "elapsed_hours": round(elapsed_hours, 2),
        "complete": window.get("complete") is True,
        "total_completed_gpu_hours": round(total_gpu_hours, 2),
        "eligible_queue_gpu_hours": round(eligible_gpu_hours, 2),
        "retiring_queue_gpu_hours": round(retiring_gpu_hours, 2),
        "observed_average_gpus": round(observed_average_gpus, 1)
        if observed_average_gpus is not None
        else None,
        "eligible_queue_average_gpus": round(eligible_average_gpus, 1)
        if eligible_average_gpus is not None
        else None,
        "post_migration_average_utilization_pct": round(
            observed_average_gpus / future_capacity_gpus * 100,
            1,
        ) if observed_average_gpus is not None and future_capacity_gpus else None,
        "semantics": (
            "Completed started-to-finished GPU-hours for exactly attributed "
            "Omni and main-vLLM jobs. Unfinished jobs and records longer than "
            "24 hours are excluded. Average load does not measure burst "
            "concurrency or prove cross-hardware compatibility."
        ),
    }


def _mapping_elapsed_hours(workload_mapping: dict) -> float:
    """Return the observed mapping-window duration without inventing precision."""
    window = workload_mapping.get("window") or {}
    try:
        window_start = datetime.fromisoformat(
            str(window.get("start_date"))
        ).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        window_start = None
    generated_at = _parse_dt(workload_mapping.get("generated_at"))
    if generated_at and window_start and generated_at > window_start:
        return (generated_at - window_start).total_seconds() / 3600
    try:
        days = max(0, int(window.get("days") or 0))
    except (TypeError, ValueError):
        days = 0
    return float(days * 24)


def _utc_iso(value: datetime | None) -> str | None:
    """Serialize an aware timestamp with the repository's canonical UTC suffix."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _capacity_joint_history(
    queue_rows: list[dict],
    queue_history: list[dict],
) -> tuple[dict, dict[str, dict], list[dict]]:
    """Select coherent queue snapshots over the latest seven-day UTC window.

    The p50 and p95 presets rank whole snapshots by active-queue running plus
    waiting GPU-slot pressure.  This deliberately avoids combining marginal
    percentiles from queue observations that never occurred together.
    """
    specs = {
        str(row.get("id")): {
            "id": str(row.get("id")),
            "family": str(row.get("family") or "unknown"),
            "gpus_per_job": max(1, int(row.get("gpus_per_job") or 1)),
            "capacity_jobs": max(
                0,
                int(
                    row.get("capacity_jobs")
                    if row.get("capacity_jobs") is not None
                    else row.get("max_concurrent_jobs")
                    or 0
                ),
            ),
        }
        for row in queue_rows
        if isinstance(row, dict) and row.get("id")
    }
    timestamped = []
    for snapshot in queue_history:
        observed_at = _parse_dt(snapshot.get("ts"))
        if observed_at is None:
            continue
        timestamped.append((observed_at.astimezone(timezone.utc), snapshot))
    timestamped.sort(key=lambda item: item[0])
    latest_at = timestamped[-1][0] if timestamped else None
    window_start = latest_at - timedelta(days=7) if latest_at else None

    def observation(
        observed_at: datetime,
        snapshot: dict,
    ) -> tuple[dict | None, list[str]]:
        by_queue = {}
        missing = []
        for queue_id, spec in specs.items():
            raw = (snapshot.get("queues") or {}).get(queue_id)
            running = _number((raw or {}).get("running")) if isinstance(raw, dict) else None
            waiting = _number((raw or {}).get("waiting")) if isinstance(raw, dict) else None
            if (
                running is None
                or waiting is None
                or running < 0
                or waiting < 0
            ):
                missing.append(queue_id)
                continue
            width = int(spec["gpus_per_job"])
            by_queue[queue_id] = {
                "running": float(running),
                "waiting": float(waiting),
                "running_gpu_slots": float(running) * width,
                "waiting_gpu_slots": float(waiting) * width,
                "connected_agents": (
                    float(raw["connected_agents"])
                    if _number(raw.get("connected_agents")) is not None
                    and float(raw["connected_agents"]) >= 0
                    else None
                ),
                "connected_agents_source": raw.get("connected_agents_source"),
                "metrics_ts": raw.get("metrics_ts"),
                "reported_p50_wait_mins": _number(raw.get("p50_wait")),
                "reported_p95_wait_mins": _number(raw.get("p95_wait")),
            }
        if missing or not specs:
            return None, missing
        running_jobs = sum(row["running"] for row in by_queue.values())
        waiting_jobs = sum(row["waiting"] for row in by_queue.values())
        running_gpu_slots = sum(
            row["running_gpu_slots"] for row in by_queue.values()
        )
        waiting_gpu_slots = sum(
            row["waiting_gpu_slots"] for row in by_queue.values()
        )
        return {
            "observed_at": _utc_iso(observed_at),
            "source_timestamp": snapshot.get("ts"),
            "by_queue": by_queue,
            "running_jobs": running_jobs,
            "waiting_jobs": waiting_jobs,
            "running_gpu_slots": running_gpu_slots,
            "waiting_gpu_slots": waiting_gpu_slots,
            "total_pressure_gpu_slots": running_gpu_slots + waiting_gpu_slots,
        }, []

    current_observations = []
    window_observations = []
    incomplete = []
    weekend_snapshot_count = 0
    weekday_dates_observed: set[str] = set()
    for observed_at, snapshot in timestamped:
        current, missing = observation(observed_at, snapshot)
        if current is not None:
            current_observations.append(current)
        if window_start is None or not (window_start < observed_at <= latest_at):
            continue
        if observed_at.weekday() >= 5:
            weekend_snapshot_count += 1
            continue
        weekday_dates_observed.add(observed_at.date().isoformat())
        if current is None:
            incomplete.append({
                "observed_at": _utc_iso(observed_at),
                "missing_queue_count": len(missing),
                "missing_queues": sorted(missing),
            })
            continue
        window_observations.append(current)

    ranked = sorted(
        window_observations,
        key=lambda row: (
            float(row["total_pressure_gpu_slots"]),
            str(row["observed_at"]),
        ),
    )

    def nearest_rank(percentile: int) -> dict | None:
        if not ranked:
            return None
        rank = max(1, (percentile * len(ranked) + 99) // 100)
        return ranked[min(len(ranked), rank) - 1]

    selected = {
        "current": current_observations[-1] if current_observations else None,
        "typical": nearest_rank(50),
        "peak": nearest_rank(95),
        # Ranking by timestamp after pressure makes this the latest snapshot
        # when multiple observations share the maximum pressure.
        "stress": ranked[-1] if ranked else None,
    }
    weekday_dates_expected: set[str] = set()
    if window_start is not None and latest_at is not None:
        cursor = window_start.date()
        while cursor <= latest_at.date():
            if cursor.weekday() < 5:
                weekday_dates_expected.add(cursor.isoformat())
            cursor += timedelta(days=1)
    missing_weekday_dates = sorted(
        weekday_dates_expected - weekday_dates_observed
    )

    def selection_summary(
        preset: str,
        row: dict | None,
        percentile: int | None = None,
    ) -> dict:
        if row is None:
            return {
                "kind": (
                    "latest_joint_snapshot"
                    if preset == "current"
                    else f"joint_pressure_{preset}_snapshot"
                ),
                "available": False,
                "observed_at": None,
                "source_path": SOURCE_FILES["queue_timeseries"],
                "source_timestamp": latest_at and _utc_iso(latest_at),
            }
        result = {
            "kind": (
                "latest_joint_snapshot"
                if preset == "current"
                else f"joint_pressure_{preset}_snapshot"
            ),
            "available": True,
            "observed_at": row["observed_at"],
            "source_path": SOURCE_FILES["queue_timeseries"],
            "source_timestamp": row["source_timestamp"],
            "queue_count": len(row["by_queue"]),
            "running_jobs": round(float(row["running_jobs"]), 1),
            "waiting_jobs": round(float(row["waiting_jobs"]), 1),
            "running_gpu_slots": round(float(row["running_gpu_slots"]), 1),
            "waiting_gpu_slots": round(float(row["waiting_gpu_slots"]), 1),
            "total_pressure_gpu_slots": round(
                float(row["total_pressure_gpu_slots"]),
                1,
            ),
            "selection_metric": "eligible_queue_running_plus_waiting_gpu_slots",
        }
        if percentile is not None:
            result["percentile"] = percentile
            result["nearest_rank"] = (
                max(1, (percentile * len(ranked) + 99) // 100)
                if ranked
                else None
            )
        return result

    published = {
        "analysis_window": {
            "kind": "rolling_7x24h_weekday_snapshots",
            "calendar_days": 7,
            "duration_hours": 168,
            "expected_weekday_equivalent_days": 5,
            "expected_weekday_hours": 120,
            "timezone": "UTC",
            "weekends_excluded": True,
            "start_at": _utc_iso(window_start),
            "end_at": _utc_iso(latest_at),
            "latest_snapshot_at": _utc_iso(latest_at),
            "source_path": SOURCE_FILES["queue_timeseries"],
            "source_timestamp": _utc_iso(latest_at),
            "eligible_queue_count": len(specs),
            "candidate_weekday_snapshot_count": (
                len(window_observations) + len(incomplete)
            ),
            "complete_snapshot_count": len(window_observations),
            "incomplete_snapshot_count": len(incomplete),
            "incomplete_snapshots": incomplete,
            "weekend_snapshot_count_excluded": weekend_snapshot_count,
            "weekday_dates_intersecting_window": sorted(
                weekday_dates_expected
            ),
            "weekday_dates_observed": sorted(weekday_dates_observed),
            "missing_weekday_dates": missing_weekday_dates,
            "weekday_date_coverage_complete": bool(
                latest_at is not None and not missing_weekday_dates
            ),
            "selection_metric": "eligible_queue_running_plus_waiting_gpu_slots",
            "selection_rule": (
                "Sort complete real snapshots by total GPU-slot pressure then "
                "timestamp; select empirical nearest-rank p50/p95 and the latest "
                "timestamp among equal observed maxima."
            ),
        },
        "joint_baselines": {
            "current": selection_summary("current", selected["current"]),
            "typical": selection_summary("typical", selected["typical"], 50),
            "peak": selection_summary("peak", selected["peak"], 95),
            "stress": selection_summary("stress", selected["stress"]),
        },
    }
    return (
        published,
        {key: value for key, value in selected.items() if value is not None},
        window_observations,
    )


def _capacity_quota_integrity(
    queue_rows: list[dict],
    observations: list[dict],
    analysis_window: dict,
    *,
    current_snapshot: dict | None = None,
) -> dict:
    """Expose configured-quota drift without confusing waiting with occupancy."""
    specs = {
        str(row.get("id")): {
            "id": str(row.get("id")),
            "family": str(row.get("family") or "unknown"),
            "gpus_per_job": max(1, int(row.get("gpus_per_job") or 1)),
            "capacity_jobs": max(
                0,
                int(
                    row.get("capacity_jobs")
                    if row.get("capacity_jobs") is not None
                    else row.get("max_concurrent_jobs")
                    or 0
                ),
            ),
        }
        for row in queue_rows
        if isinstance(row, dict) and row.get("id")
    }
    queue_violations = []
    queue_violation_observations = 0
    for queue_id, spec in sorted(specs.items()):
        events = []
        for snapshot in observations:
            row = (snapshot.get("by_queue") or {}).get(queue_id)
            if not row or row["running"] <= spec["capacity_jobs"]:
                continue
            queue_violation_observations += 1
            events.append({
                "observed_at": snapshot["observed_at"],
                "running_jobs": row["running"],
                "waiting_jobs": row["waiting"],
                "running_gpu_slots": row["running_gpu_slots"],
                "waiting_gpu_slots": row["waiting_gpu_slots"],
                "excess_running_jobs": row["running"] - spec["capacity_jobs"],
                "excess_running_gpu_slots": (
                    row["running"] - spec["capacity_jobs"]
                ) * spec["gpus_per_job"],
            })
        if not events:
            continue
        maximum = max(
            events,
            key=lambda row: (
                row["excess_running_gpu_slots"],
                row["observed_at"],
            ),
        )
        queue_violations.append({
            "id": queue_id,
            "family": spec["family"],
            "gpus_per_job": spec["gpus_per_job"],
            "configured_capacity_jobs": spec["capacity_jobs"],
            "configured_capacity_gpus": (
                spec["capacity_jobs"] * spec["gpus_per_job"]
            ),
            "violation_snapshot_count": len(events),
            "first_observed_at": min(row["observed_at"] for row in events),
            "last_observed_at": max(row["observed_at"] for row in events),
            "maximum_observed_at": maximum["observed_at"],
            "maximum_running_occupancy_jobs": round(maximum["running_jobs"], 1),
            "waiting_demand_jobs_at_maximum": round(maximum["waiting_jobs"], 1),
            "maximum_running_occupancy_gpu_slots": round(
                maximum["running_gpu_slots"],
                1,
            ),
            "waiting_demand_gpu_slots_at_maximum": round(
                maximum["waiting_gpu_slots"],
                1,
            ),
            "maximum_excess_running_jobs": round(
                maximum["excess_running_jobs"],
                1,
            ),
            "maximum_excess_running_gpu_slots": round(
                maximum["excess_running_gpu_slots"],
                1,
            ),
        })

    family_specs: dict[str, dict] = defaultdict(
        lambda: {"capacity_jobs": 0, "capacity_gpus": 0, "queue_ids": []}
    )
    for queue_id, spec in specs.items():
        family = family_specs[spec["family"]]
        family["capacity_jobs"] += spec["capacity_jobs"]
        family["capacity_gpus"] += (
            spec["capacity_jobs"] * spec["gpus_per_job"]
        )
        family["queue_ids"].append(queue_id)

    family_violations = []
    family_violation_observations = 0
    for family_name, spec in sorted(family_specs.items()):
        events = []
        for snapshot in observations:
            queue_values = snapshot.get("by_queue") or {}
            running_jobs = sum(
                queue_values[queue_id]["running"]
                for queue_id in spec["queue_ids"]
            )
            waiting_jobs = sum(
                queue_values[queue_id]["waiting"]
                for queue_id in spec["queue_ids"]
            )
            running_gpu_slots = sum(
                queue_values[queue_id]["running_gpu_slots"]
                for queue_id in spec["queue_ids"]
            )
            waiting_gpu_slots = sum(
                queue_values[queue_id]["waiting_gpu_slots"]
                for queue_id in spec["queue_ids"]
            )
            if running_gpu_slots <= spec["capacity_gpus"]:
                continue
            family_violation_observations += 1
            events.append({
                "observed_at": snapshot["observed_at"],
                "running_jobs": running_jobs,
                "waiting_jobs": waiting_jobs,
                "running_gpu_slots": running_gpu_slots,
                "waiting_gpu_slots": waiting_gpu_slots,
                "excess_running_gpu_slots": (
                    running_gpu_slots - spec["capacity_gpus"]
                ),
            })
        if not events:
            continue
        maximum = max(
            events,
            key=lambda row: (
                row["excess_running_gpu_slots"],
                row["observed_at"],
            ),
        )
        family_violations.append({
            "family": family_name,
            "queue_count": len(spec["queue_ids"]),
            "configured_capacity_jobs": spec["capacity_jobs"],
            "configured_capacity_gpus": spec["capacity_gpus"],
            "violation_snapshot_count": len(events),
            "first_observed_at": min(row["observed_at"] for row in events),
            "last_observed_at": max(row["observed_at"] for row in events),
            "maximum_observed_at": maximum["observed_at"],
            "maximum_running_occupancy_jobs": round(maximum["running_jobs"], 1),
            "waiting_demand_jobs_at_maximum": round(maximum["waiting_jobs"], 1),
            "maximum_running_occupancy_gpu_slots": round(
                maximum["running_gpu_slots"],
                1,
            ),
            "waiting_demand_gpu_slots_at_maximum": round(
                maximum["waiting_gpu_slots"],
                1,
            ),
            "maximum_excess_running_gpu_slots": round(
                maximum["excess_running_gpu_slots"],
                1,
            ),
        })

    connected_agent_rows = []
    connected_sources = list(observations)
    if current_snapshot and not any(
        row.get("observed_at") == current_snapshot.get("observed_at")
        for row in connected_sources
    ):
        connected_sources.append(current_snapshot)
    connected_sources.sort(key=lambda row: str(row.get("observed_at") or ""))
    for queue_id, spec in sorted(specs.items()):
        queue_native = []
        for snapshot in connected_sources:
            raw = (snapshot.get("by_queue") or {}).get(queue_id) or {}
            connected = _number(raw.get("connected_agents"))
            source = str(raw.get("connected_agents_source") or "")
            if (
                connected is None
                or connected < 0
                or source != "queue_native_metrics"
            ):
                continue
            queue_native.append({
                "observed_at": snapshot.get("observed_at"),
                "connected_agents": float(connected),
                "source": source,
                "metrics_timestamp": raw.get("metrics_ts"),
            })
        latest = queue_native[-1] if queue_native else None
        window_values = [
            row
            for row in queue_native
            if any(
                observation.get("observed_at") == row.get("observed_at")
                for observation in observations
            )
        ]
        maximum = max(
            window_values,
            key=lambda row: (
                row["connected_agents"],
                str(row.get("observed_at") or ""),
            ),
        ) if window_values else None
        configured_jobs = int(spec["capacity_jobs"])
        latest_agents = (
            float(latest["connected_agents"]) if latest is not None else None
        )
        signed_delta = (
            latest_agents - configured_jobs
            if latest_agents is not None
            else None
        )
        if signed_delta is None:
            direction = "unavailable"
        elif signed_delta > 0:
            direction = "above_planning_quota"
        elif signed_delta < 0:
            direction = "below_planning_quota"
        else:
            direction = "matches_planning_quota"
        connected_agent_rows.append({
            "id": queue_id,
            "family": spec["family"],
            "configured_capacity_jobs": configured_jobs,
            "configured_capacity_source": SOURCE_FILES["capacity_monitor"],
            "planning_capacity_preserved": True,
            "available": latest is not None,
            "latest_connected_agents": (
                int(latest_agents)
                if latest_agents is not None and latest_agents.is_integer()
                else latest_agents
            ),
            "signed_delta_jobs": (
                int(signed_delta)
                if signed_delta is not None and signed_delta.is_integer()
                else signed_delta
            ),
            "direction": direction,
            "observed_at": latest.get("observed_at") if latest else None,
            "source": latest.get("source") if latest else None,
            "metrics_timestamp": latest.get("metrics_timestamp") if latest else None,
            "max_connected_agents_in_window": (
                int(maximum["connected_agents"])
                if maximum is not None
                and float(maximum["connected_agents"]).is_integer()
                else (
                    maximum["connected_agents"]
                    if maximum is not None
                    else None
                )
            ),
            "max_connected_agents_observed_at": (
                maximum.get("observed_at") if maximum else None
            ),
        })
    connected_available = [
        row for row in connected_agent_rows if row["available"] is True
    ]
    connected_mismatches = [
        row
        for row in connected_available
        if row["signed_delta_jobs"] != 0
    ]

    available = bool(specs and (observations or connected_available))
    drift = bool(queue_violations or family_violations)
    connected_mismatch = bool(connected_mismatches)
    return {
        "available": available,
        "status": (
            "warning"
            if drift or connected_mismatch
            else ("ok" if available else "unavailable")
        ),
        "quota_drift_detected": drift,
        "connected_agent_mismatch_detected": connected_mismatch,
        "source_path": SOURCE_FILES["queue_timeseries"],
        "source_timestamp": analysis_window.get("source_timestamp"),
        "window_start_at": analysis_window.get("start_at"),
        "window_end_at": analysis_window.get("end_at"),
        "observed_snapshot_count": len(observations),
        "queue": {
            "affected_queue_count": len(queue_violations),
            "violation_observation_count": queue_violation_observations,
            "violations": queue_violations,
        },
        "family": {
            "affected_family_count": len(family_violations),
            "violation_observation_count": family_violation_observations,
            "violations": family_violations,
        },
        "connected_agents": {
            "queue_count": len(connected_agent_rows),
            "available_queue_count": len(connected_available),
            "unavailable_queue_count": (
                len(connected_agent_rows) - len(connected_available)
            ),
            "mismatch_queue_count": len(connected_mismatches),
            "above_planning_quota_queue_count": sum(
                row["direction"] == "above_planning_quota"
                for row in connected_available
            ),
            "below_planning_quota_queue_count": sum(
                row["direction"] == "below_planning_quota"
                for row in connected_available
            ),
            "planning_capacity_preserved": True,
            "queues": connected_agent_rows,
            "semantics": (
                "Queue-native connected agents are an observed integrity signal, "
                "not a replacement for configured planning capacity. Signed delta "
                "is latest connected agents minus configured concurrent job slots; "
                "max uses the same weekday analysis window."
            ),
        },
        "semantics": (
            "Configured capacity is compared only with observed running "
            "occupancy. Waiting jobs are published separately as demand and do "
            "not by themselves imply quota drift. Drift can reflect quota "
            "changes or source/configuration mismatch."
        ),
    }


def _weekday_started_cohort_rates(
    workload_mapping: dict,
    analysis_window: dict,
    queue_ids: set[str],
) -> tuple[dict, dict[str, int]]:
    """Aggregate created-hour cohorts over the queue history's weekday window."""
    window_start = _parse_dt(analysis_window.get("start_at"))
    window_end = _parse_dt(analysis_window.get("end_at"))
    generated_at = _parse_dt(workload_mapping.get("generated_at"))
    started_by_queue = {queue_id: 0 for queue_id in queue_ids}
    elapsed_hours = 0.0
    included_buckets = 0
    partial_buckets = 0
    weekend_buckets = 0
    leading_boundary_buckets = 0
    lower_bound_buckets = 0
    observed_through = None

    for bucket in workload_mapping.get("hourly") or []:
        if not isinstance(bucket, dict):
            continue
        bucket_start = _parse_dt(bucket.get("hour"))
        if bucket_start is None or window_start is None or window_end is None:
            continue
        bucket_start = bucket_start.astimezone(timezone.utc)
        if bucket_start < window_start:
            bucket_end = _parse_dt(bucket.get("end_exclusive"))
            if bucket_end and bucket_end > window_start:
                # Counts cannot be split inside their created-at hour without
                # inventing an intra-hour arrival distribution.
                leading_boundary_buckets += 1
            continue
        if bucket_start >= window_end:
            continue
        if bucket_start.weekday() >= 5:
            weekend_buckets += 1
            continue
        nominal_end = _parse_dt(bucket.get("end_exclusive"))
        if nominal_end is None:
            nominal_end = bucket_start + timedelta(hours=1)
        bucket_observed_through = _parse_dt(bucket.get("observed_through"))
        usable_end = min(
            window_end,
            nominal_end,
            bucket_observed_through or nominal_end,
        )
        if usable_end <= bucket_start:
            continue
        duration_hours = (usable_end - bucket_start).total_seconds() / 3600
        elapsed_hours += duration_hours
        included_buckets += 1
        if duration_hours < 1 or bucket.get("partial") is True or bucket.get("open") is True:
            partial_buckets += 1
        if bucket.get("lower_bound") is True or bucket.get("collection_complete") is False:
            lower_bound_buckets += 1
        observed_through = max(observed_through, usable_end) if observed_through else usable_end
        for workload_name in ("main", "omni"):
            by_queue = (
                ((bucket.get("workloads") or {}).get(workload_name) or {}).get(
                    "by_queue"
                )
                or {}
            )
            for queue_id in queue_ids:
                started_by_queue[queue_id] += int(
                    (by_queue.get(queue_id) or {}).get("started_jobs") or 0
                )

    expected_weekday_hours = float(
        analysis_window.get("expected_weekday_hours") or 0
    )
    metadata = {
        "available": bool(included_buckets and elapsed_hours > 0),
        "metric": "weekday_started_cohort_rate_jobs_per_hour",
        "requested_start_at": analysis_window.get("start_at"),
        "requested_end_at": analysis_window.get("end_at"),
        "observed_through": _utc_iso(observed_through),
        "elapsed_weekday_hours": round(elapsed_hours, 4),
        "expected_weekday_hours": expected_weekday_hours,
        "coverage_pct": round(elapsed_hours / expected_weekday_hours * 100, 1)
        if expected_weekday_hours
        else None,
        "included_hour_bucket_count": included_buckets,
        "partial_hour_bucket_count": partial_buckets,
        "weekend_hour_bucket_count_excluded": weekend_buckets,
        "leading_boundary_bucket_count_excluded": leading_boundary_buckets,
        "lower_bound_bucket_count": lower_bound_buckets,
        "source_path": SOURCE_FILES["workload_mapping"],
        "source_timestamp": _utc_iso(generated_at),
        "timestamp_field": "job.created_at_hour",
        "semantics": (
            "Hourly started_jobs counts the job.created_at cohort that eventually "
            "started; it is not a count of started_at events in that hour. The "
            "rate divides those cohorts by covered weekday bucket hours. A leading "
            "partial created-at bucket is excluded because it cannot be split "
            "without assuming an intra-hour distribution; open trailing buckets "
            "use their published observed_through duration."
        ),
    }
    return metadata, started_by_queue


def _capacity_history_baseline(
    queue_id: str,
    max_concurrent_jobs: int,
    queue_history: list[dict],
    *,
    gpus_per_job: int = 1,
    joint_snapshots: dict[str, dict] | None = None,
    joint_observations: list[dict] | None = None,
) -> dict:
    """Build coherent queue baselines while retaining marginal diagnostics.

    Snapshots are deliberately weighted equally.  Collection intervals are not
    sufficiently regular to claim a time-weighted utilization distribution.
    Raw running counts are retained even when they exceed today's configured
    quota so quota drift remains visible to the consumer.
    """
    width = max(1, int(gpus_per_job or 1))
    joint_snapshots = joint_snapshots or {}
    observations = [
        {
            "ts": row.get("observed_at"),
            **((row.get("by_queue") or {}).get(queue_id) or {}),
        }
        for row in (joint_observations or [])
        if (row.get("by_queue") or {}).get(queue_id)
    ]
    if joint_observations is None:
        for snapshot in queue_history:
            raw = (snapshot.get("queues") or {}).get(queue_id)
            if not isinstance(raw, dict):
                continue
            running = _number(raw.get("running"))
            waiting = _number(raw.get("waiting"))
            if running is None or waiting is None or running < 0 or waiting < 0:
                continue
            observations.append({
                "ts": snapshot.get("ts"),
                "running": float(running),
                "waiting": float(waiting),
                "running_gpu_slots": float(running) * width,
                "waiting_gpu_slots": float(waiting) * width,
                "connected_agents": _number(raw.get("connected_agents")),
                "connected_agents_source": raw.get("connected_agents_source"),
                "metrics_ts": raw.get("metrics_ts"),
                "reported_p50_wait_mins": _number(raw.get("p50_wait")),
                "reported_p95_wait_mins": _number(raw.get("p95_wait")),
            })

    def finalize(row: dict, running: float, waiting: float) -> dict:
        row.update({
            "available_slots": round(max(0.0, max_concurrent_jobs - running), 1),
            "utilization_pct": round(running / max_concurrent_jobs * 100, 1)
            if max_concurrent_jobs
            else None,
            "saturated": bool(max_concurrent_jobs and running >= max_concurrent_jobs),
            "above_configured_capacity": bool(running > max_concurrent_jobs),
            "running_gpu_slots": round(running * width, 1),
            "waiting_gpu_slots": round(waiting * width, 1),
            "total_pressure_gpu_slots": round((running + waiting) * width, 1),
        })
        return row

    def unavailable(kind: str) -> dict:
        return {
            "kind": kind,
            "available": False,
            "running": None,
            "waiting": None,
            "available_slots": None,
            "utilization_pct": None,
            "saturated": None,
        }

    def marginal_baseline(kind: str, percentile: int) -> dict:
        if not observations:
            return unavailable(kind)
        running_values = sorted(row["running"] for row in observations)
        waiting_values = sorted(row["waiting"] for row in observations)
        running = float(_percentile(running_values, percentile) or 0)
        waiting = float(_percentile(waiting_values, percentile) or 0)
        reported_p50 = sorted(
            row["reported_p50_wait_mins"]
            for row in observations
            if row.get("reported_p50_wait_mins") is not None
        )
        reported_p95 = sorted(
            row["reported_p95_wait_mins"]
            for row in observations
            if row.get("reported_p95_wait_mins") is not None
        )
        return finalize({
            "kind": kind,
            "available": True,
            "percentile": percentile,
            "running": round(running, 1),
            "waiting": round(waiting, 1),
            "reported_p50_wait_mins": _percentile(reported_p50, percentile),
            "reported_p95_wait_mins": _percentile(reported_p95, percentile),
        }, running, waiting)

    def coherent_baseline(preset: str) -> dict:
        selected = joint_snapshots.get(preset)
        raw = ((selected or {}).get("by_queue") or {}).get(queue_id)
        kind = (
            "latest_joint_snapshot"
            if preset == "current"
            else f"joint_pressure_{preset}_snapshot"
        )
        if not raw:
            if preset == "current" and observations:
                raw = observations[-1]
                observed_at = raw.get("ts")
            else:
                return unavailable(kind)
        else:
            observed_at = selected.get("observed_at")
        running = float(raw["running"])
        waiting = float(raw["waiting"])
        row = {
            "kind": kind,
            "available": True,
            "observed_at": observed_at,
            "source_path": SOURCE_FILES["queue_timeseries"],
            "source_timestamp": (selected or {}).get("source_timestamp") or observed_at,
            "running": int(running) if running.is_integer() else round(running, 1),
            "waiting": int(waiting) if waiting.is_integer() else round(waiting, 1),
            "reported_p50_wait_mins": raw.get("reported_p50_wait_mins"),
            "reported_p95_wait_mins": raw.get("reported_p95_wait_mins"),
            "connected_agents": (
                int(raw["connected_agents"])
                if _number(raw.get("connected_agents")) is not None
                and float(raw["connected_agents"]).is_integer()
                else _number(raw.get("connected_agents"))
            ),
            "connected_agents_source": raw.get("connected_agents_source"),
            "metrics_timestamp": raw.get("metrics_ts"),
            "selection_metric": "eligible_queue_running_plus_waiting_gpu_slots",
        }
        if preset == "typical":
            row["percentile"] = 50
        elif preset == "peak":
            row["percentile"] = 95
        return finalize(row, running, waiting)

    if not observations and not joint_snapshots:
        current = unavailable("latest_joint_snapshot")
    else:
        current = coherent_baseline("current")
    marginal_typical = marginal_baseline("marginal_empirical_p50", 50)
    marginal_peak = marginal_baseline("marginal_empirical_p95", 95)
    first_observed_at = observations[0]["ts"] if observations else None
    last_observed_at = observations[-1]["ts"] if observations else None
    return {
        "sample_count": len(observations),
        "first_observed_at": first_observed_at,
        "last_observed_at": last_observed_at,
        "snapshots_above_configured_capacity": sum(
            observation["running"] > max_concurrent_jobs
            for observation in observations
        ),
        "current": current,
        "typical": coherent_baseline("typical"),
        "peak": coherent_baseline("peak"),
        "stress": coherent_baseline("stress"),
        "marginal": {
            "typical": marginal_typical,
            "peak": marginal_peak,
            "semantics": (
                "Diagnostics only: running and waiting are independent per-queue "
                "percentiles over the weekday window and need not have co-occurred."
            ),
        },
    }


def _unplaced_retiring_mi325_workload(
    capacity: dict,
    workload_mapping: dict,
    queue_history: list[dict],
    elapsed_hours: float,
) -> dict:
    """Publish retiring MI325 demand without guessing a compatible destination."""
    catalog = _queue_capacity_catalog(capacity)
    retiring = [
        queue
        for queue in catalog.values()
        if (
            str(queue.get("family") or "").upper() == "MI325"
            or str(queue.get("id") or "").startswith("amd_mi325_")
        )
        and queue.get("lifecycle") == "retiring"
    ]
    retiring_specs = [
        {
            "id": queue["id"],
            "family": "MI325",
            "gpus_per_job": int(queue.get("gpus_per_job") or 1),
            "max_concurrent_jobs": int(queue.get("max_concurrent_jobs") or 0),
        }
        for queue in retiring
    ]
    joint_history, joint_snapshots, joint_observations = _capacity_joint_history(
        retiring_specs,
        queue_history,
    )
    retiring_integrity = _capacity_quota_integrity(
        retiring_specs,
        joint_observations,
        joint_history["analysis_window"],
        current_snapshot=joint_snapshots.get("current"),
    )
    number_fields = (
        "mapped_jobs",
        "started_jobs",
        "finished_jobs",
        "mapped_gpu_slots",
    )
    mapping_totals = workload_mapping.get("totals") or {}

    def source_stats(workload: str, queue_id: str) -> dict:
        source = (
            ((mapping_totals.get(workload) or {}).get("by_queue") or {}).get(queue_id)
            or {}
        )
        return {
            **{field: int(source.get(field) or 0) for field in number_fields},
            "gpu_hours": round(float(source.get("gpu_hours") or 0), 2),
        }

    def add_stats(rows: list[dict]) -> dict:
        return {
            **{
                field: sum(int(row.get(field) or 0) for row in rows)
                for field in number_fields
            },
            "gpu_hours": round(
                sum(float(row.get("gpu_hours") or 0) for row in rows),
                2,
            ),
        }

    queue_rows = []
    for queue in sorted(retiring, key=lambda row: str(row.get("id") or "")):
        queue_id = str(queue["id"])
        workloads = {
            workload: source_stats(workload, queue_id)
            for workload in ("main", "omni")
        }
        queue_rows.append({
            "id": queue_id,
            "label": queue.get("label") or queue_id.removeprefix("amd_"),
            "family": "MI325",
            "gpus_per_job": int(queue.get("gpus_per_job") or 1),
            "current_capacity_jobs": int(queue.get("max_concurrent_jobs") or 0),
            "current_capacity_gpus": int(queue.get("gpu_capacity") or 0),
            "workloads": workloads,
            "totals": add_stats(list(workloads.values())),
            "history": _capacity_history_baseline(
                queue_id,
                int(queue.get("max_concurrent_jobs") or 0),
                queue_history,
                gpus_per_job=int(queue.get("gpus_per_job") or 1),
                joint_snapshots=joint_snapshots,
                joint_observations=joint_observations,
            ),
        })

    by_workload = {
        workload: add_stats([
            row["workloads"][workload]
            for row in queue_rows
        ])
        for workload in ("main", "omni")
    }
    totals = add_stats(list(by_workload.values()))
    totals["average_gpus"] = (
        round(float(totals["gpu_hours"]) / elapsed_hours, 2)
        if elapsed_hours > 0
        else None
    )

    def occupancy(preset: str) -> dict:
        selection = (
            (joint_history.get("joint_baselines") or {}).get(preset)
            or {}
        )
        observed = [
            (row, row["history"].get(preset) or {})
            for row in queue_rows
            if (row["history"].get(preset) or {}).get("available") is True
        ]
        if not observed:
            return {
                "available": False,
                "complete": False,
                "queue_count": 0,
                "running_jobs": None,
                "waiting_jobs": None,
                "running_gpu_slots": None,
                "waiting_gpu_slots": None,
                "observed_at": selection.get("observed_at"),
                "source_path": selection.get("source_path"),
                "source_timestamp": selection.get("source_timestamp"),
            }
        running_jobs = sum(float(baseline.get("running") or 0) for _, baseline in observed)
        waiting_jobs = sum(float(baseline.get("waiting") or 0) for _, baseline in observed)
        running_gpu_slots = sum(
            float(baseline.get("running") or 0) * int(row["gpus_per_job"])
            for row, baseline in observed
        )
        waiting_gpu_slots = sum(
            float(baseline.get("waiting") or 0) * int(row["gpus_per_job"])
            for row, baseline in observed
        )
        return {
            "available": True,
            "complete": len(observed) == len(queue_rows),
            "queue_count": len(observed),
            "running_jobs": round(running_jobs, 1),
            "waiting_jobs": round(waiting_jobs, 1),
            "running_gpu_slots": round(running_gpu_slots, 1),
            "waiting_gpu_slots": round(waiting_gpu_slots, 1),
            "total_pressure_gpu_slots": round(
                running_gpu_slots + waiting_gpu_slots,
                1,
            ),
            "observed_at": selection.get("observed_at"),
            "source_path": selection.get("source_path"),
            "source_timestamp": selection.get("source_timestamp"),
        }

    window = workload_mapping.get("window") or {}
    attribution = (workload_mapping.get("scope") or {}).get("attribution") or {}
    parent_build_lookback_days = attribution.get("parent_build_lookback_days")
    if parent_build_lookback_days is None:
        parent_build_lookback_days = (
            workload_mapping.get("query") or {}
        ).get("parent_build_lookback_days")
    return {
        "available": bool(queue_rows),
        "status": "unplaced",
        "family": "MI325",
        "compatibility": "unknown",
        "requires_manual_destination": True,
        "excluded_from_wait_and_headroom": True,
        "window": {
            "days": window.get("days"),
            "start_date": window.get("start_date"),
            "end_date": window.get("end_date"),
            "elapsed_hours": round(elapsed_hours, 2),
            "complete": window.get("complete") is True,
            "lower_bound": window.get("lower_bound") is True,
            "job_created_range_exhaustive": (
                window.get("job_created_range_exhaustive") is True
            ),
            "exact_within_declared_source_window": (
                attribution.get("exact_within_declared_source_window") is True
            ),
            "parent_build_lookback_days": parent_build_lookback_days,
            "source_limitation": attribution.get("limitation"),
        },
        "totals": totals,
        "by_workload": by_workload,
        "occupancy": {
            "current": occupancy("current"),
            "typical": occupancy("typical"),
            "peak": occupancy("peak"),
            "stress": occupancy("stress"),
            **joint_history,
            "semantics": (
                "Current, typical, peak, and stress each sum one coherent observed "
                "MI325 snapshot. Typical and peak are nearest-rank p50/p95 and "
                "stress is the observed maximum of MI325 running-plus-waiting "
                "GPU-slot pressure over the same strict seven-by-twenty-four-hour "
                "UTC weekday window."
            ),
        },
        "integrity": retiring_integrity,
        "queues": queue_rows,
        "reason": (
            "Retiring MI325 mappings and observed occupancy are excluded from "
            "the active-queue wait and headroom model until a user confirms a "
            "compatible destination. No cross-family or queue-width "
            "compatibility is inferred."
        ),
    }


def _capacity_simulation_profile(
    capacity: dict,
    queue_rows: list[dict],
    runtime_estimate: dict,
    workload_mapping: dict,
    queue_history: list[dict],
) -> dict:
    """Publish source-backed inputs for an interactive queue planning model.

    This intentionally does not publish a server-side wait forecast.  The
    browser can evaluate burst and steady-arrival scenarios from these inputs,
    while retaining enough provenance to label the result as a planning
    estimate rather than an observed SLA.
    """
    elapsed_hours = _mapping_elapsed_hours(workload_mapping)
    unplaced_retiring_workload = _unplaced_retiring_mi325_workload(
        capacity,
        workload_mapping,
        queue_history,
        elapsed_hours,
    )
    mapping_totals = workload_mapping.get("totals") or {}
    current_by_queue = {
        str(row.get("id")): row
        for row in capacity.get("queues") or []
        if isinstance(row, dict) and row.get("id")
    }
    joint_history, joint_snapshots, joint_observations = _capacity_joint_history(
        queue_rows,
        queue_history,
    )
    analysis_window = joint_history["analysis_window"]
    integrity = _capacity_quota_integrity(
        queue_rows,
        joint_observations,
        analysis_window,
        current_snapshot=joint_snapshots.get("current"),
    )
    weekday_rate_window, weekday_started_by_queue = (
        _weekday_started_cohort_rates(
            workload_mapping,
            analysis_window,
            {
                str(row.get("id"))
                for row in queue_rows
                if isinstance(row, dict) and row.get("id")
            },
        )
    )
    weekday_rate_hours = float(
        weekday_rate_window.get("elapsed_weekday_hours") or 0
    )
    global_runtime_service_minutes = None
    runtime_sampled_jobs = int(runtime_estimate.get("sampled_jobs") or 0)
    if runtime_sampled_jobs and _number(runtime_estimate.get("median_agent_hours")) is not None:
        global_runtime_service_minutes = round(
            float(runtime_estimate["median_agent_hours"]) * 60 / runtime_sampled_jobs,
            2,
        )

    profile_rows = []
    for target in queue_rows:
        queue_id = str(target["id"])
        gpus_per_job = max(1, int(target.get("gpus_per_job") or 1))
        max_concurrent_jobs = max(0, int(target.get("max_concurrent_jobs") or 0))
        current = current_by_queue.get(queue_id) or {}
        workload_counts = {
            "mapped_jobs": 0,
            "started_jobs": 0,
            "finished_jobs": 0,
            "mapped_gpu_slots": 0,
            "gpu_hours": 0.0,
        }
        for workload_name in ("main", "omni"):
            source = (
                ((mapping_totals.get(workload_name) or {}).get("by_queue") or {}).get(queue_id)
                or {}
            )
            for field in ("mapped_jobs", "started_jobs", "finished_jobs", "mapped_gpu_slots"):
                workload_counts[field] += int(source.get(field) or 0)
            workload_counts["gpu_hours"] += float(source.get("gpu_hours") or 0)

        observed_agent_hours = workload_counts["gpu_hours"] / gpus_per_job
        observed_service_minutes = (
            observed_agent_hours * 60 / workload_counts["finished_jobs"]
            if workload_counts["finished_jobs"] and observed_agent_hours > 0
            else None
        )
        runtime_row = (runtime_estimate.get("queues") or {}).get(queue_id) or {}
        runtime_jobs = int(runtime_row.get("sampled_jobs") or 0)
        runtime_service_minutes = (
            float(runtime_row.get("median_agent_hours") or 0) * 60 / runtime_jobs
            if runtime_jobs and _number(runtime_row.get("median_agent_hours")) is not None
            else None
        )
        if runtime_service_minutes is not None:
            service_minutes = runtime_service_minutes
            service_source = "target_command_job_median_average"
            service_is_proxy = False
        elif observed_service_minutes is not None:
            service_minutes = observed_service_minutes
            service_source = "completed_agent_minutes_per_finished_job_proxy_fallback"
            service_is_proxy = True
        elif global_runtime_service_minutes is not None:
            service_minutes = global_runtime_service_minutes
            service_source = "target_suite_global_median_average_fallback"
            service_is_proxy = False
        else:
            service_minutes = None
            service_source = "unavailable"
            service_is_proxy = None

        current_groups = int(current.get("gated_groups") or 0)
        current_jobs = int(current.get("gated_jobs") or 0)
        target_groups = int(target.get("groups") or 0)
        target_jobs = int(target.get("jobs") or 0)
        target_agent_minutes = (
            round(float(runtime_row.get("median_agent_hours") or 0) * 60, 2)
            if runtime_jobs
            else (
                round(target_jobs * service_minutes, 2)
                if service_minutes is not None
                else None
            )
        )
        current_agent_minutes = (
            round(current_jobs * service_minutes, 2)
            if service_minutes is not None
            else None
        )
        profile_rows.append({
            "id": queue_id,
            "label": target.get("label") or queue_id.removeprefix("amd_"),
            "family": target.get("family") or "unknown",
            "provider": target.get("provider"),
            "gpus_per_job": gpus_per_job,
            "capacity_jobs": max_concurrent_jobs,
            "capacity_gpus": max_concurrent_jobs * gpus_per_job,
            "history": _capacity_history_baseline(
                queue_id,
                max_concurrent_jobs,
                queue_history,
                gpus_per_job=gpus_per_job,
                joint_snapshots=joint_snapshots,
                joint_observations=joint_observations,
            ),
            "workload": {
                **workload_counts,
                "gpu_hours": round(workload_counts["gpu_hours"], 2),
                "observed_agent_hours": round(observed_agent_hours, 2),
                "weekday_started_cohort_jobs": int(
                    weekday_started_by_queue.get(queue_id) or 0
                ),
                "weekday_started_cohort_rate_jobs_per_hour": round(
                    float(weekday_started_by_queue.get(queue_id) or 0)
                    / weekday_rate_hours,
                    4,
                )
                if weekday_rate_window.get("available") is True
                and weekday_rate_hours
                else None,
                "mapped_arrival_rate_jobs_per_hour": round(
                    workload_counts["mapped_jobs"] / elapsed_hours,
                    4,
                ) if elapsed_hours else None,
                "started_arrival_rate_jobs_per_hour": round(
                    workload_counts["started_jobs"] / elapsed_hours,
                    4,
                ) if elapsed_hours else None,
                "finished_rate_jobs_per_hour": round(
                    workload_counts["finished_jobs"] / elapsed_hours,
                    4,
                ) if elapsed_hours else None,
                "observed_service_minutes": round(observed_service_minutes, 2)
                if observed_service_minutes is not None
                else None,
                "target_runtime_service_minutes": round(runtime_service_minutes, 2)
                if runtime_service_minutes is not None
                else None,
                "target_global_service_minutes": global_runtime_service_minutes,
                "runtime_fallback_service_minutes": round(runtime_service_minutes, 2)
                if runtime_service_minutes is not None
                else global_runtime_service_minutes,
                "service_minutes": round(service_minutes, 2)
                if service_minutes is not None
                else None,
                "service_minutes_source": service_source,
                "service_minutes_is_proxy": service_is_proxy,
            },
            "demand": {
                "current": {
                    "groups": current_groups,
                    "jobs": current_jobs,
                    "gpu_slots": current_jobs * gpus_per_job,
                    "agent_minutes": current_agent_minutes,
                },
                "target": {
                    "groups": target_groups,
                    "jobs": target_jobs,
                    "gpu_slots": target_jobs * gpus_per_job,
                    "agent_minutes": target_agent_minutes,
                },
                "delta": {
                    "groups": target_groups - current_groups,
                    "jobs": target_jobs - current_jobs,
                    "gpu_slots": (target_jobs - current_jobs) * gpus_per_job,
                    "agent_minutes": round(target_agent_minutes - current_agent_minutes, 2)
                    if target_agent_minutes is not None and current_agent_minutes is not None
                    else None,
                },
            },
        })

    current_totals = {
        "groups": int((capacity.get("summary") or {}).get("capacity_scoped_group_count") or 0),
        "jobs": sum(row["demand"]["current"]["jobs"] for row in profile_rows),
        "gpu_slots": sum(row["demand"]["current"]["gpu_slots"] for row in profile_rows),
        "agent_minutes": round(sum(
            float(row["demand"]["current"]["agent_minutes"] or 0)
            for row in profile_rows
        ), 2),
    }
    target_totals = {
        "groups": sum(row["demand"]["target"]["groups"] for row in profile_rows),
        "jobs": sum(row["demand"]["target"]["jobs"] for row in profile_rows),
        "gpu_slots": sum(row["demand"]["target"]["gpu_slots"] for row in profile_rows),
        "agent_minutes": round(sum(
            float(row["demand"]["target"]["agent_minutes"] or 0)
            for row in profile_rows
        ), 2),
    }
    return {
        "available": bool(profile_rows),
        "model": {
            "id": "amd_queue_planning_inputs_v2",
            "kind": "planning_estimate_inputs_not_sla",
            "burst_wait": (
                "Use FCFS list scheduling per queue as a planning estimate. Idle configured "
                "slots are available immediately; each history.<preset>.running job "
                "keeps a slot for one full workload.service_minutes estimate; "
                "history.<preset>.waiting jobs remain ahead of the simulated burst. "
                "Only the full-service residual assigned to already-running jobs is "
                "conservative. A finite wait is unavailable when quota, history, or "
                "service time is missing."
            ),
            "steady_wait": (
                "Optional Erlang-C planning approximation: offered load "
                "uses lambda=weekday_started_cohort_rate_jobs_per_hour+"
                "incremental_suites_per_hour*delta_jobs_per_suite, then "
                "A=lambda*service_minutes/60 and rho=A/c. Baseline running is not "
                "added to offered load. If rho>=1 the scenario is unstable. Otherwise compute "
                "Erlang-B recursively, P(wait)=B/(1-rho+rho*B), mean "
                "Wq=P(wait)*service_minutes/(c-A), and the conditional exponential "
                "tail for percentiles."
            ),
            "steady_wait_assumptions": (
                "Stationary Poisson arrivals, independent exponentially distributed "
                "service, homogeneous configured runners, FCFS dispatch, and no "
                "cross-queue migration. The published median-derived service time is "
                "used as a mean-service proxy, and the weekday created-cohort rate is "
                "only a proxy for actual started_at arrivals. These assumptions are "
                "not an SLA."
            ),
        },
        "defaults": {
            "baseline": "peak",
            "traffic_mode": "burst",
            "target_groups": target_totals["groups"],
            "simultaneous_suites": 1,
            "arrival_rate_jobs_field": (
                "weekday_started_cohort_rate_jobs_per_hour"
            ),
        },
        "topology": {
            "current": current_totals,
            "target": target_totals,
            "delta": {
                key: round(target_totals[key] - current_totals[key], 2)
                for key in ("groups", "jobs", "gpu_slots", "agent_minutes")
            },
            "interpolation": (
                "For totals between current and target, interpolate each queue's "
                "current-to-target demand. Below current use the current mix; above "
                "target use the exact target mix. Rounded jobs remain a planning mix, "
                "not an exact YAML topology."
            ),
        },
        "history": {
            "snapshot_count": len(queue_history),
            "first_observed_at": queue_history[0].get("ts") if queue_history else None,
            "last_observed_at": queue_history[-1].get("ts") if queue_history else None,
            "quantiles": {"typical": 50, "peak": 95, "stress": "observed_max"},
            "weighting": "one_equal_weight_per_collected_snapshot",
            **joint_history,
        },
        "workload_window": {
            "elapsed_hours": round(elapsed_hours, 2),
            "days": (workload_mapping.get("window") or {}).get("days"),
            "start_date": (workload_mapping.get("window") or {}).get("start_date"),
            "end_date": (workload_mapping.get("window") or {}).get("end_date"),
            "complete": (workload_mapping.get("window") or {}).get("complete") is True,
            "lower_bound": (workload_mapping.get("window") or {}).get("lower_bound") is True,
            "job_created_range_exhaustive": (
                (workload_mapping.get("window") or {}).get(
                    "job_created_range_exhaustive"
                )
                is True
            ),
            "parent_build_lookback_days": (
                ((workload_mapping.get("scope") or {}).get("attribution") or {}).get(
                    "parent_build_lookback_days"
                )
                or (workload_mapping.get("query") or {}).get(
                    "parent_build_lookback_days"
                )
            ),
            "weekday_started_cohort_rate": weekday_rate_window,
        },
        "integrity": integrity,
        "unplaced_retiring_workload": unplaced_retiring_workload,
        "assumptions": {
            "capacity": (
                "Configured future-eligible queue quotas are treated as concurrent "
                "job slots. MI325 and perf-eval queues remain excluded; amd-cpu is "
                "reserved for Docker builds and is not GPU gating capacity."
            ),
            "history": (
                "Current is the latest complete joint observation. Typical, peak, "
                "and stress are real coherent weekday snapshots selected by the "
                "eligible queues' combined running-plus-waiting GPU-slot pressure "
                "over the strict latest seven-by-twenty-four-hour UTC window. "
                "Independent per-queue percentiles remain diagnostic only under "
                "history.marginal. Raw observations are never capped to today's "
                "quota."
            ),
            "arrivals": (
                "Mapped and started rates divide unique daily Buildkite aggregate "
                "counts by the published window duration. They are historical average "
                "rates, not a fitted arrival distribution or peak forecast. The "
                "weekday started-cohort rate is preferred for sustained planning, "
                "but its hourly timestamp is job.created_at: it counts a created "
                "cohort that eventually started, not literal started_at events."
            ),
            "service": (
                "Per-queue target command-job median averages from the target-runtime "
                "estimate are the primary service-time input for auto-mix burst "
                "planning. When that target estimate is unavailable, observed service "
                "minutes divide completed agent-minutes by all finished jobs and are "
                "used only as a fallback; that proxy is downward biased when finished "
                "jobs lack a valid started-to-finished interval or exceed the 24-hour "
                "retention guard. The global target-suite median average is the final "
                "fallback."
            ),
            "burst_residual": (
                "Every job already running at the selected snapshot baseline is "
                "conservatively assigned one full service-time estimate before its "
                "slot becomes available. Actual residual runtimes are not observed."
            ),
            "compatibility": (
                "No queue or hardware-family migration is assumed. An auto-placement "
                "UI must constrain alternatives to user-confirmed compatible widths "
                "and families."
            ),
            "retiring_workload": unplaced_retiring_workload["reason"],
        },
        "provenance": {
            "capacity": SOURCE_FILES["capacity_monitor"],
            "target_topology": SOURCE_FILES["amd_test_matrix"],
            "target_runtime": SOURCE_FILES["analytics"],
            "queue_history": SOURCE_FILES["queue_timeseries"],
            "workload_mapping": SOURCE_FILES["workload_mapping"],
        },
        "queues": profile_rows,
    }


def _exact_target_topology(
    capacity: dict,
    amd_test_matrix: dict,
    amd_analytics: dict | None = None,
    workload_mapping: dict | None = None,
    queue_history: list[dict] | None = None,
    *,
    architecture_preference: list[str] | tuple[str, ...] | None = None,
) -> dict:
    """Project one full semantic AMD matrix onto its configured queue topology.

    Each semantic row is counted once. Architecture ordering is configurable,
    and selection is restricted to explicit matrix cells whose declared queues
    are active. Parallelism is expanded into command jobs, and queue width
    converts those jobs into simultaneous GPU slots.
    """
    catalog = _queue_capacity_catalog(capacity)
    preference = _normalize_architecture_preference(architecture_preference)
    placement = _target_placement_demand(
        amd_test_matrix,
        catalog,
        preference,
    )
    demand = placement["demand"]
    selected_groups = int(placement["selected_groups"])
    unassigned_groups = int(placement["unassigned_groups"])

    strategy_definitions = [
        (
            "mi355_preferred",
            "Prefer explicit MI355 definitions after MI250",
            AMD_TARGET_DEFAULT_PREFERENCE,
        ),
        (
            "current_definition_precedence",
            "Current definition precedence",
            AMD_TARGET_CURRENT_DEFINITION_PREFERENCE,
        ),
    ]
    default_strategy_id = next(
        (
            strategy_id
            for strategy_id, _, strategy_preference in strategy_definitions
            if tuple(preference) == tuple(strategy_preference)
        ),
        "configured_preference",
    )

    def strategy_profile(
        strategy_id: str,
        label: str,
        strategy_placement: dict,
        strategy_preference: list[str] | tuple[str, ...],
    ) -> dict:
        profile = _placement_strategy_profile(
            strategy_id,
            label,
            strategy_placement,
        )
        strategy_runtime = _target_runtime_estimate(
            amd_test_matrix,
            amd_analytics or {},
            catalog,
            architecture_preference=strategy_preference,
        )
        runtime_queues = strategy_runtime.get("queues") or {}
        for queue in profile["queues"]:
            runtime_queue = runtime_queues.get(queue["id"]) or {}
            sampled_jobs = int(runtime_queue.get("sampled_jobs") or 0)
            median_agent_hours = _number(
                runtime_queue.get("median_agent_hours")
            )
            queue["service_minutes"] = (
                round(float(median_agent_hours) * 60 / sampled_jobs, 2)
                if sampled_jobs and median_agent_hours is not None
                else None
            )
            queue["service_minutes_source"] = (
                "placement_strategy_target_command_job_median_average"
                if queue["service_minutes"] is not None
                else "unavailable"
            )
            queue["service_sampled_command_jobs"] = sampled_jobs
        profile["runtime_estimate"] = strategy_runtime
        return profile

    strategy_profiles = []
    if default_strategy_id == "configured_preference":
        strategy_profiles.append(strategy_profile(
            "configured_preference",
            "Configured architecture preference",
            placement,
            preference,
        ))
    for strategy_id, label, strategy_preference in strategy_definitions:
        strategy_placement = (
            placement
            if tuple(preference) == tuple(strategy_preference)
            else _target_placement_demand(
                amd_test_matrix,
                catalog,
                strategy_preference,
            )
        )
        strategy_profiles.append(strategy_profile(
            strategy_id,
            label,
            strategy_placement,
            strategy_preference,
        ))

    queue_rows = []
    current_capacity_queues = {
        str(row.get("id")): row
        for row in capacity.get("queues") or []
        if isinstance(row, dict) and row.get("id")
    }
    for queue_id in sorted(demand):
        queue = demand[queue_id]
        jobs = int(queue["jobs"])
        max_jobs = int(queue["max_concurrent_jobs"])
        gap_jobs = max(0, jobs - max_jobs)
        current_queue = current_capacity_queues.get(queue_id) or {}
        current_gated_jobs = int(current_queue.get("gated_jobs") or 0)
        queue_rows.append({
            key: value
            for key, value in queue.items()
            if key != "group_ids"
        } | {
            "groups": len(queue["group_ids"]),
            "current_gated_groups": int(current_queue.get("gated_groups") or 0),
            "current_gated_jobs": current_gated_jobs,
            "current_gated_gpu_slots": current_gated_jobs * int(queue["gpus_per_job"]),
            "capacity_ratio": round(jobs / max_jobs, 4) if max_jobs else (1.0 if not jobs else None),
            "gap_jobs": gap_jobs,
            "gap_gpus": gap_jobs * int(queue["gpus_per_job"]),
        })

    jobs = sum(row["jobs"] for row in queue_rows)
    gpu_slots = sum(row["gpu_slots"] for row in queue_rows)
    future_capacity = (
        (capacity.get("summary") or {}).get("capacity") or {}
    ).get("future_eligible") or (capacity.get("projection") or {}).get("future_capacity") or {}

    family_rows = []
    for family in sorted({
        str(row.get("family") or "unknown") for row in queue_rows
    }):
        family_queues = [row for row in queue_rows if row["family"] == family]
        family_rows.append({
            "family": family,
            "groups": sum(row["groups"] for row in family_queues),
            "jobs": sum(row["jobs"] for row in family_queues),
            "gpu_slots": sum(row["gpu_slots"] for row in family_queues),
            "gpu_capacity": sum(row["gpu_capacity"] for row in family_queues),
        })

    scenarios = []
    for suites in (1, 2):
        queue_gaps = []
        for row in queue_rows:
            demand_jobs = row["jobs"] * suites
            gap_jobs = max(0, demand_jobs - row["max_concurrent_jobs"])
            if gap_jobs:
                queue_gaps.append({
                    "id": row["id"],
                    "label": row["label"],
                    "family": row["family"],
                    "gpus_per_job": row["gpus_per_job"],
                    "demand_jobs": demand_jobs,
                    "capacity_jobs": row["max_concurrent_jobs"],
                    "gap_jobs": gap_jobs,
                    "gap_gpus": gap_jobs * row["gpus_per_job"],
                })
        family_gaps = []
        for family in family_rows:
            demand_gpus = int(family["gpu_slots"]) * suites
            gap_gpus = max(0, demand_gpus - int(family["gpu_capacity"]))
            if gap_gpus:
                family_gaps.append({
                    "family": family["family"],
                    "demand_gpus": demand_gpus,
                    "capacity_gpus": int(family["gpu_capacity"]),
                    "gap_gpus": gap_gpus,
                })
        scenario_gpu_slots = gpu_slots * suites
        capacity_gpus = int(future_capacity.get("gpus") or 0)
        scenarios.append({
            "full_suites": suites,
            "groups": selected_groups * suites,
            "jobs": jobs * suites,
            "gpu_slots": scenario_gpu_slots,
            "aggregate_capacity_gpus": capacity_gpus,
            "aggregate_utilization_pct": round(
                scenario_gpu_slots / capacity_gpus * 100,
                1,
            ) if capacity_gpus else None,
            "aggregate_gap_gpus": max(0, scenario_gpu_slots - capacity_gpus),
            "fits_aggregate_capacity": bool(capacity_gpus and scenario_gpu_slots <= capacity_gpus),
            "fits_family_capacity": not family_gaps,
            "fits_queue_shapes": not queue_gaps,
            "family_gaps": family_gaps,
            "family_gap_gpus": sum(row["gap_gpus"] for row in family_gaps),
            "queue_gaps": queue_gaps,
            "shape_gap_gpus": sum(row["gap_gpus"] for row in queue_gaps),
        })

    projection = capacity.get("projection") or {}
    declared_total = int(projection.get("declared_total_groups") or 0)
    if not declared_total:
        declared_total = int(projection.get("declared_existing_groups") or 0) + int(
            projection.get("declared_new_groups") or 0
        )
    one_suite = scenarios[0]
    queue_gaps = one_suite["queue_gaps"]
    gap_gpus_by_family: dict[str, int] = defaultdict(int)
    for gap in queue_gaps:
        gap_gpus_by_family[str(gap["family"])] += int(gap["gap_gpus"])
    spare_gpus_by_family: dict[str, int] = defaultdict(int)
    for row in queue_rows:
        spare_gpus_by_family[str(row["family"])] += max(
            0,
            int(row["gpu_capacity"]) - int(row["gpu_slots"]),
        )
    repartition_possible = bool(queue_gaps) and all(
        spare_gpus_by_family.get(family, 0) >= gap_gpus
        for family, gap_gpus in gap_gpus_by_family.items()
    )
    queue_reallocations = [
        {
            **gap,
            "family_spare_gpus": spare_gpus_by_family.get(str(gap["family"]), 0),
            "family_spare_semantics": (
                "gross_same_family_queue_surplus_before_deficit_reallocation"
            ),
        }
        for gap in queue_gaps
    ]
    largest_gap = max(
        queue_gaps,
        key=lambda gap: (int(gap["gap_gpus"]), int(gap["gap_jobs"])),
        default=None,
    )
    runtime_estimate = _target_runtime_estimate(
        amd_test_matrix,
        amd_analytics or {},
        catalog,
        architecture_preference=preference,
    )
    simulation_profile = _capacity_simulation_profile(
        capacity,
        queue_rows,
        runtime_estimate,
        workload_mapping or {},
        queue_history or [],
    )
    standalone_net_new_required = not (
        one_suite["fits_aggregate_capacity"]
        and one_suite["fits_family_capacity"]
        and (one_suite["fits_queue_shapes"] or repartition_possible)
    )
    if repartition_possible and len(queue_gaps) == 1:
        gap = queue_gaps[0]
        runner_suffix = "" if int(gap["gap_jobs"]) == 1 else "s"
        standalone_summary = (
            f"Repartition {gap['gap_gpus']} spare {gap['family']} GPUs "
            f"into {gap['gap_jobs']} additional {gap['label']} runner{runner_suffix}; "
            "the standalone target suite does not require net-new silicon."
        )
    elif repartition_possible:
        family_label = ", ".join(sorted(gap_gpus_by_family))
        actions = ", ".join(
            f"{gap['gap_jobs']} additional {gap['label']} "
            f"runner{'s' if int(gap['gap_jobs']) != 1 else ''} "
            f"({gap['gap_gpus']} GPUs)"
            for gap in queue_gaps
        )
        standalone_summary = (
            f"Repartition {one_suite['shape_gap_gpus']} spare GPUs within "
            f"{family_label} into {actions}; the standalone target suite does "
            "not require net-new silicon."
        )
    elif one_suite["fits_aggregate_capacity"] and one_suite["fits_queue_shapes"]:
        standalone_summary = (
            "The standalone target suite fits both aggregate capacity and every "
            "queue shape."
        )
    else:
        standalone_summary = (
            "The standalone target suite requires additional or migrated queue capacity."
        )
    unplaced_retiring = simulation_profile.get("unplaced_retiring_workload") or {}
    mi325_migration_unplaced = bool(
        unplaced_retiring.get("available") is True
        and unplaced_retiring.get("requires_manual_destination") is True
    )
    overall_requirement = (
        "indeterminate_until_mi325_destination_modeled"
        if mi325_migration_unplaced
        else (
            "net_new_hardware_required"
            if standalone_net_new_required
            else "no_net_new_hardware_required"
        )
    )
    return {
        "available": bool(selected_groups and queue_rows),
        "method": "exact_one_cell_per_semantic_matrix_row",
        "source_path": SOURCE_FILES["amd_test_matrix"],
        "architecture_precedence": list(preference),
        "placement_profiles": {
            "default_strategy_id": default_strategy_id,
            "configurable": True,
            "selection_method": "first_feasible_explicit_matrix_cell",
            "strategies": strategy_profiles,
        },
        "target_groups": int(projection.get("target_groups") or selected_groups),
        "declared_current_mirror_groups": int(
            projection.get("declared_current_mirror_groups") or 0
        ),
        "observed_current_mirror_groups": int(projection.get("base_groups") or 0),
        "declared_existing_groups": int(projection.get("declared_existing_groups") or 0),
        "declared_new_groups": int(projection.get("declared_new_groups") or 0),
        "declared_total_groups": declared_total,
        "planning_headroom_groups": max(
            0,
            int(projection.get("target_groups") or selected_groups) - declared_total,
        ),
        "groups": selected_groups,
        "unassigned_groups": unassigned_groups,
        "jobs": jobs,
        "gpu_slots": gpu_slots,
        "eight_gpu_node_equivalents": round(gpu_slots / 8, 2),
        "future_capacity": future_capacity,
        "retiring_capacity": (
            (capacity.get("summary") or {}).get("capacity") or {}
        ).get("retiring") or {},
        "queues": queue_rows,
        "families": family_rows,
        "runtime_estimate": runtime_estimate,
        "historical_load": _historical_capacity_load(
            workload_mapping or {},
            catalog,
            int(future_capacity.get("gpus") or 0),
        ),
        "simulation_profile": simulation_profile,
        "current_topology": simulation_profile["topology"]["current"],
        "scenarios": scenarios,
        "target_depends_on_retiring_capacity": any(
            row["jobs"] and row["lifecycle"] == "retiring"
            for row in queue_rows
        ),
        "recommendation": {
            "net_new_hardware_required_for_one_suite": (
                None if mi325_migration_unplaced else standalone_net_new_required
            ),
            "overall_hardware_requirement": overall_requirement,
            "mi325_migration_unplaced": mi325_migration_unplaced,
            "conditional_on_mi325_destination": mi325_migration_unplaced,
            "standalone_target_only": {
                "net_new_hardware_required": standalone_net_new_required,
                "fits_aggregate_capacity": one_suite["fits_aggregate_capacity"],
                "fits_family_capacity": one_suite["fits_family_capacity"],
                "fits_queue_shapes": one_suite["fits_queue_shapes"],
                "family_gap_gpus": one_suite["family_gap_gpus"],
                "shape_gap_gpus": one_suite["shape_gap_gpus"],
                "summary": standalone_summary,
            },
            "queue_shape_change_required": not one_suite["fits_queue_shapes"],
            "repartition_possible_within_family": repartition_possible,
            "bottleneck_queue": largest_gap["id"] if largest_gap else None,
            "additional_runner_jobs": sum(
                int(gap["gap_jobs"]) for gap in queue_gaps
            ),
            "additional_runner_gpus": sum(
                int(gap["gap_gpus"]) for gap in queue_gaps
            ),
            "queue_reallocations": queue_reallocations,
            "summary": (
                standalone_summary
                + " Overall hardware need is indeterminate until the retiring MI325 "
                "workload is assigned to user-confirmed compatible destinations and "
                "modeled with their queue widths."
                if mi325_migration_unplaced
                else standalone_summary
            ),
        },
        "linear_sensitivity": projection,
        "caveat": (
            "The linear sensitivity preserves the current mirror mix and is not the "
            "hardware answer. Exact matrix topology is used for the target because "
            "the expanded target is more multi-GPU-heavy."
        ),
    }


def _trajectory(
    reliability: dict,
    group_changes: dict,
    capacity: dict,
    amd_test_matrix: dict,
    amd_analytics: dict,
    workload_mapping: dict,
    queue_history: list[dict],
) -> dict:
    cohort = reliability.get("cohort") or {}
    denominator = reliability.get("denominator") or {}
    return {
        "source_pipeline": "ci",
        "available": reliability.get("available") is True,
        "pipeline_order": ["ci"],
        "pipelines": [{
            "pipeline": "ci",
            "source_path": SOURCE_FILES["analytics"],
            "source_key": "ci.all_main_reliability",
            "evidence_kind": "strict completed upstream branch=main job observations",
            "cohort": cohort,
            "groups": int(denominator.get("groups") or 0),
            "observations": int(denominator.get("observations") or 0),
        }],
        "group_changes": {
            "days": group_changes.get("days"),
            "total_changes": group_changes.get("total_changes") or len(group_changes.get("changes") or []),
            "recent": list(group_changes.get("changes") or [])[:CHANGE_LIMIT],
            "source_path": SOURCE_FILES["group_changes"],
        },
        "capacity_projection": _exact_target_topology(
            capacity,
            amd_test_matrix,
            amd_analytics,
            workload_mapping,
            queue_history,
        ),
        "provenance": {
            "source_paths": {
                "build_history": SOURCE_FILES["analytics"],
                "group_changes": SOURCE_FILES["group_changes"],
                "capacity": SOURCE_FILES["capacity_monitor"],
                "target_topology": SOURCE_FILES["amd_test_matrix"],
                "historical_load": SOURCE_FILES["workload_mapping"],
                "queue_history": SOURCE_FILES["queue_timeseries"],
            },
            "build_history": {
                "path": SOURCE_FILES["analytics"],
                "source_key": "ci.all_main_reliability",
                "source_pipeline": "ci",
                "evidence_kind": "strict completed upstream branch=main job observations",
            },
            "group_changes": {
                "path": SOURCE_FILES["group_changes"],
                "evidence_kind": "published repository change aggregate",
            },
            "capacity": {
                "path": SOURCE_FILES["capacity_monitor"],
                "evidence_kind": "published queue quota and mirror projection aggregate",
            },
            "target_topology": {
                "path": SOURCE_FILES["amd_test_matrix"],
                "evidence_kind": "published semantic AMD matrix topology",
            },
            "historical_load": {
                "path": SOURCE_FILES["workload_mapping"],
                "evidence_kind": "published completed GPU-hour aggregate",
            },
            "queue_history": {
                "path": SOURCE_FILES["queue_timeseries"],
                "evidence_kind": "published queue running and waiting history",
            },
        },
    }


def _attention(nightly: dict, reliability: dict, gating: dict, queue: dict, omni: dict) -> list[dict]:
    items = []
    amd_builds = (nightly.get("pipelines") or [{}])[0].get("builds") or []
    latest = amd_builds[0] if amd_builds else {}
    if latest.get("test_jobs_blocked"):
        items.append({
            "kind": "nightly_infrastructure_blocked",
            "severity": "critical",
            "count": int(latest["test_jobs_blocked"]),
        })
    # Current severity must come from the current outcome, not movement alone.
    # A newly soft-failing group is warning-level, while a recurring hard
    # failure must remain critical.
    if latest.get("failed_groups"):
        items.append({
            "kind": "nightly_hard_failures",
            "severity": "critical",
            "count": len(latest["failed_groups"]),
        })
    if latest.get("soft_failed_groups"):
        items.append({
            "kind": "nightly_soft_failures",
            "severity": "warning",
            "count": len(latest["soft_failed_groups"]),
        })
    snapshot = queue.get("snapshot") or {}
    zombies = int(snapshot.get("total_zombie_waiting") or 0) + int(snapshot.get("total_zombie_running") or 0)
    if zombies:
        items.append({"kind": "queue_zombies", "severity": "critical", "count": zombies})
    if int(snapshot.get("total_waiting") or 0):
        items.append({"kind": "queue_waiting", "severity": "warning", "count": snapshot["total_waiting"]})
    latest_states = (gating.get("active_target_summary") or {}).get("by_latest_amd_state") or {}
    target_incidents = int(latest_states.get("hard") or 0) + int(latest_states.get("soft") or 0)
    if target_incidents:
        items.append({
            "kind": "target_groups_with_current_incidents",
            "severity": "warning",
            "count": target_incidents,
        })
    if reliability.get("flaky_candidates"):
        items.append({
            "kind": "mixed_state_flaky_candidates",
            "severity": "info",
            "count": len(reliability["flaky_candidates"]),
        })
    if omni.get("status") != "healthy":
        items.append({
            "kind": "omni_waiting",
            "severity": "critical" if omni.get("status") == "surge" else "warning",
            "count": (omni.get("current") or {}).get("waiting", 0),
        })
    return items


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp (with trailing ``Z``) to an aware datetime."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _amd_agent_health(data_dir: Path) -> dict:
    """Load the pre-aggregated AMD agent-health block for the CI-agent-health view.

    All heavy lifting — walking every build across every branch in the AMD
    pipelines, computing per-node/day reliability rollups, and isolating
    infra-suspect failures (a failure whose test group otherwise passes that day,
    on another node) — is done by ``scripts/vllm/collect_agent_health.py`` and
    persisted to ``agent_health.json``. This snapshot simply embeds that payload;
    the frontend aggregates the reliability table and clusters co-failure events
    client-side, reactive to the window / GPU / node / co-failure-window /
    exclude-cancelled / nightly-only controls.
    """
    payload = _load_json(data_dir / "agent_health.json")
    return payload if isinstance(payload, dict) else {}


def build_snapshot(data_dir: Path | str, generated_at: str | None = None) -> dict:
    data_dir = Path(data_dir)
    paths = {name: data_dir / filename for name, filename in SOURCE_FILES.items()}
    loaded = {name: _load_json(path) for name, path in paths.items() if path.suffix == ".json"}
    queue_history = load_queue_history(paths["queue_timeseries"])
    queue_snapshot = _filter_queue_snapshot(load_latest_queue_snapshot(paths["queue_timeseries"]))

    analytics = loaded.get("analytics") or {}
    ci_health = loaded.get("ci_health") or {}
    amd_nightly = _nightly_pipeline(
        "amd-ci", analytics.get("amd-ci") or {}, ci_health.get("amd") or {},
    )
    upstream_parity = _nightly_pipeline(
        "ci", analytics.get("ci") or {}, ci_health.get("upstream") or {},
    )
    amd_test_health = _amd_test_health(data_dir, analytics.get("amd-ci") or {})
    amd_agent_health = _amd_agent_health(data_dir)
    pipeline_blocks = [amd_nightly, upstream_parity]
    nightly = {
        "primary_pipeline": "amd-ci",
        "pipeline_order": ["amd-ci", "ci"],
        "history_window_days": NIGHTLY_BUILD_LIMIT,
        "transition_basis": (
            "exact failed and soft-failed job variants versus the preceding nightly"
        ),
        "canonical_history": amd_nightly,
        "upstream_parity": upstream_parity,
        "pipelines": pipeline_blocks,
    }
    reliability = _reliability(analytics.get("ci") or {}, pipeline_slug="ci")
    definition_parity = loaded.get("config_parity") or {}
    gating = _gating(
        loaded.get("gating_targets") or {},
        loaded.get("gating_target_candidates") or {},
        loaded.get("amd_test_matrix") or {},
        loaded.get("capacity_monitor") or {},
        reliability,
        definition_parity,
    )
    ownership = loaded.get("ci_ownership") or {}
    ownership = (
        ownership
        if ownership.get("schema_version") == 1
        else {
            "schema_version": 1,
            "available": False,
            "unavailable_reason": "ownership_snapshot_unavailable",
            "areas": [],
            "summary": {},
        }
    )
    queue = _queue(queue_snapshot, loaded.get("queue_jobs") or {}, queue_history)
    omni = _omni(
        queue_snapshot,
        queue.get("queue_jobs") or {},
        queue_history,
        loaded.get("omni_heuristic") or {},
        loaded.get("omni_issue_state") or {},
        loaded.get("workload_mapping") or {},
        loaded.get("capacity_monitor") or {},
    )
    trajectory = _trajectory(
        reliability,
        loaded.get("group_changes") or {},
        loaded.get("capacity_monitor") or {},
        loaded.get("amd_test_matrix") or {},
        analytics.get("amd-ci") or {},
        loaded.get("workload_mapping") or {},
        queue_history,
    )
    attention = _attention(nightly, reliability, gating, queue, omni)
    status = "critical" if any(row["severity"] == "critical" for row in attention) else (
        "attention" if any(row["severity"] == "warning" for row in attention) else "healthy"
    )
    latest_amd = pipeline_blocks[0]["builds"][0] if pipeline_blocks[0]["builds"] else {}
    home = {
        "status": status,
        "attention_count": len(attention),
        "attention": attention,
        "latest_amd_nightly": {
            key: latest_amd.get(key)
            for key in ("number", "created_at", "state", "url")
            if latest_amd.get(key) not in (None, "")
        },
        "queue": {
            "waiting": queue_snapshot.get("total_waiting", 0),
            "running": queue_snapshot.get("total_running", 0),
        },
        "omni_status": omni["status"],
    }

    sources = {}
    for name, path in paths.items():
        data = queue_snapshot if name == "queue_timeseries" else loaded.get(name) or {}
        sources[name] = _source_record(path, data, queue_snapshot.get("ts", "") if name == "queue_timeseries" else "")
    for internal_source in ("agent_health", "omni_issue_state", "ci_ownership"):
        sources[internal_source]["published"] = False
    # The raw JSONL ledger is an internal build input, so diagnostics link to
    # the published analytics source while retaining the actual latest AMD
    # observation time rather than the wrapper regeneration time.
    sources["amd_test_signal"] = {
        "path": SOURCE_FILES["analytics"],
        "timestamp": (amd_test_health.get("summary") or {}).get("latest_observed_at"),
        "timestamp_source": "amd_test_health.summary.latest_observed_at",
        "published": True,
    }

    return {
        "schema_version": 2,
        "generated_at": generated_at or _utc_now(),
        "sources": sources,
        "home": home,
        "attention": attention,
        "nightly": nightly,
        "amd_test_health": amd_test_health,
        "amd_agent_health": amd_agent_health,
        "reliability": reliability,
        "definition_parity": definition_parity,
        "gating": gating,
        "ownership": ownership,
        "queue": queue,
        "trajectory": trajectory,
        "omni": omni,
    }


def _compact_nightly(nightly: dict, build_limit: int | None = None) -> dict:
    """Drop serialized compatibility aliases while retaining both pipelines."""
    compact = {
        key: value
        for key, value in nightly.items()
        if key not in {"canonical_history", "upstream_parity", "amd", "upstream", "pipelines"}
    }
    pipelines = []
    for pipeline in nightly.get("pipelines") or []:
        row = dict(pipeline)
        if build_limit is not None:
            row["builds"] = list(row.get("builds") or [])[:build_limit]
        pipelines.append(row)
    compact["pipelines"] = pipelines
    return compact


def _compact_queue_history(history: list[dict]) -> list[dict]:
    """Keep chart fields and omit repeated null-heavy collector metadata."""
    compact_history = []
    for snapshot in history:
        queues = {}
        for name, queue in (snapshot.get("queues") or {}).items():
            compact = {
                key: value
                for key, value in queue.items()
                if key in QUEUE_HISTORY_SHARD_FIELDS and value not in (None, "")
            }
            if compact:
                queues[name] = compact
        compact_history.append({
            key: value
            for key, value in {
                "ts": snapshot.get("ts"),
                "schema_version": snapshot.get("schema_version"),
                "total_waiting": snapshot.get("total_waiting"),
                "total_running": snapshot.get("total_running"),
                "tracked_queue_count": snapshot.get("tracked_queue_count"),
                "queues": queues,
                "sources": snapshot.get("sources"),
            }.items()
            if value not in (None, "")
        })
    return compact_history


def _compact_queue(queue: dict) -> dict:
    compact = dict(queue)
    compact["history"] = _compact_queue_history(list(queue.get("history") or []))
    return compact


def _diagnostic_section(payload: dict) -> dict:
    reliability = payload.get("reliability") or {}
    retry = reliability.get("retry_analysis") or {}
    amd_health = payload.get("amd_test_health") or {}
    queue = payload.get("queue") or {}
    return {
        "reliability": {
            key: reliability.get(key)
            for key in (
                "available",
                "source_pipeline",
                "cohort",
                "evidence_definitions",
                "denominator",
                "summary",
            )
        } | {
            "group_catalog": [
                {"id": row.get("id")}
                for row in reliability.get("group_catalog") or []
            ],
            "flaky_candidates": [
                {"id": row.get("id")}
                for row in reliability.get("flaky_candidates") or []
            ],
            "retry_analysis": {
                key: retry.get(key)
                for key in ("available", "summary", "provenance")
            },
        },
        "amd_test_health": {
            "summary": amd_health.get("summary") or {},
            "provenance": amd_health.get("provenance") or {},
        },
        "queue": {
            "history_summary": queue.get("history_summary") or {},
        },
    }


def _operations_shell(payload: dict) -> dict:
    nightly = _compact_nightly(payload.get("nightly") or {}, build_limit=7)
    nightly["pipelines"] = [
        row for row in nightly.get("pipelines") or []
        if row.get("pipeline") == AMD_TEST_PIPELINE
    ]
    amd_health = payload.get("amd_test_health") or {}
    gating = payload.get("gating") or {}
    definition_parity = payload.get("definition_parity") or {}
    queue = payload.get("queue") or {}
    return {
        key: payload.get(key)
        for key in ("schema_version", "generated_at", "sources", "home", "attention")
    } | {
        "nightly": nightly,
        "amd_test_health": {"summary": amd_health.get("summary") or {}},
        "gating": {"matrix_summary": gating.get("matrix_summary") or {}},
        "definition_parity": {
            "summary": definition_parity.get("summary") or {},
            "source": definition_parity.get("source") or {},
        },
        "queue": {
            "snapshot": queue.get("snapshot") or {},
            "history_summary": queue.get("history_summary") or {},
        },
    }


def _operation_sections(payload: dict) -> dict[str, dict]:
    return {
        "nightly": {"nightly": _compact_nightly(payload.get("nightly") or {})},
        "amd_test_health": {"amd_test_health": payload.get("amd_test_health") or {}},
        "amd_agent_health": {"amd_agent_health": payload.get("amd_agent_health") or {}},
        "reliability": {"reliability": payload.get("reliability") or {}},
        "definition_parity": {"definition_parity": payload.get("definition_parity") or {}},
        "gating": {"gating": payload.get("gating") or {}},
        "ownership": {"ownership": payload.get("ownership") or {}},
        "queue": {"queue": _compact_queue(payload.get("queue") or {})},
        "trajectory": {"trajectory": payload.get("trajectory") or {}},
        "omni": {"omni": payload.get("omni") or {}},
        "diagnostics": _diagnostic_section(payload),
    }


def _encoded_json(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n"


def write_snapshot_bundle(
    output: Path,
    payload: dict,
    *,
    write_monolith: bool = True,
    log: bool = True,
) -> dict:
    """Write the lazy frontend bundle and, by default, its source monolith."""
    output.parent.mkdir(parents=True, exist_ok=True)
    monolith = _encoded_json(payload)
    if write_monolith:
        output.write_text(monolith)

    bundle_dir = output.parent / OPERATIONS_BUNDLE_DIR_NAME
    bundle_dir.mkdir(parents=True, exist_ok=True)
    section_manifest = {}
    expected_paths = set()
    for name, section in _operation_sections(payload).items():
        path = bundle_dir / f"{name}.json"
        encoded = _encoded_json(section)
        path.write_text(encoded)
        expected_paths.add(path)
        section_manifest[name] = {
            "path": f"{OPERATIONS_BUNDLE_DIR_NAME}/{path.name}",
            "bytes": len(encoded.encode("utf-8")),
        }
    for stale in bundle_dir.glob("*.json"):
        if stale not in expected_paths:
            stale.unlink()

    manifest = {
        "schema_version": payload.get("schema_version"),
        "bundle_version": 1,
        "generated_at": payload.get("generated_at"),
        "monolith": output.name if write_monolith else None,
        "shell": _operations_shell(payload),
        "sections": section_manifest,
    }
    manifest_path = output.parent / OPERATIONS_MANIFEST_NAME
    manifest_encoded = _encoded_json(manifest)
    manifest_path.write_text(manifest_encoded)
    if log:
        if write_monolith:
            print(f"Wrote {output} ({len(monolith.encode('utf-8'))} bytes)")
        print(
            f"Wrote {manifest_path} ({len(manifest_encoded.encode('utf-8'))} bytes, "
            f"{len(section_manifest)} lazy sections)"
        )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", "--data-dir", dest="input_dir", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", help="Output path (default: INPUT_DIR/operations_v2.json)")
    parser.add_argument("--generated-at", help="Override generation timestamp for reproducible builds")
    args = parser.parse_args(argv)

    input_dir = Path(args.input_dir)
    output = Path(args.output) if args.output else input_dir / DEFAULT_OUTPUT_NAME
    payload = build_snapshot(input_dir, generated_at=args.generated_at)
    write_snapshot_bundle(output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
