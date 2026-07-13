#!/usr/bin/env python3
"""Build the compact, authoritative v2 operations dashboard snapshot."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_INPUT = ROOT / "data" / "vllm" / "ci"
DEFAULT_OUTPUT_NAME = "operations_v2.json"
NIGHTLY_BUILD_LIMIT = 14
RANKING_LIMIT = 20
CHANGE_LIMIT = 20
FAILED_STATES = {"failed", "timed_out", "broken", "canceled"}
SOFT_FAILED_STATES = {"soft_fail", "soft_failed"}
RETRY_EVIDENCE_FIELDS = (
    "retried",
    "retried_in_job_id",
    "retries_count",
    "retry_source",
    "retry_type",
    "step_key",
)

SOURCE_FILES = {
    "analytics": "analytics.json",
    "ci_health": "ci_health.json",
    "gating_targets": "gating_targets.json",
    "gating_target_candidates": "gating_target_candidates.json",
    "amd_test_matrix": "amd_test_matrix.json",
    "capacity_monitor": "capacity_monitor.json",
    "queue_timeseries": "queue_timeseries.jsonl",
    "queue_jobs": "queue_jobs.json",
    "group_changes": "group_changes.json",
    "omni_heuristic": "omni_surge_heuristic.json",
    "omni_issue_state": "open_omni_surge_issues.json",
}

MULTISPACE_RE = re.compile(r"\s+")
AMD_PREFIX_RE = re.compile(r"^AMD:\s*", re.IGNORECASE)
INTERNAL_AMD_PREFIX_RE = re.compile(r"^mi\d{3,4}b?_\d+:\s*", re.IGNORECASE)
AMD_DEVICE_SUFFIX_RE = re.compile(r"\s*\((mi\d{3,4}b?_\d+)\)\s*$", re.IGNORECASE)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _is_current_queue_snapshot(row: Any) -> bool:
    """Recognize the provenance-bearing queue schema used by the dashboard."""
    return (
        isinstance(row, dict)
        and isinstance(row.get("ts"), str)
        and isinstance(row.get("queues"), dict)
        and isinstance(row.get("sources"), dict)
        and isinstance(row.get("total_waiting"), int)
        and isinstance(row.get("total_running"), int)
        and all(
            isinstance(stats, dict) and "p95_wait" in stats
            for stats in row["queues"].values()
        )
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
    if build.get("web_url"):
        return str(build["web_url"])
    number = build.get("number") or build.get("build_number")
    return f"https://buildkite.com/vllm/{pipeline}/builds/{number}" if number else ""


def _job_url(pipeline: str, build: dict, job: dict) -> str:
    if job.get("url") or job.get("web_url"):
        return str(job.get("url") or job.get("web_url"))
    base = _build_url(pipeline, build)
    if not base:
        return ""
    if pipeline == "amd-ci" and job.get("step_id"):
        return f"{base}/steps/canvas?sid={job['step_id']}&tab=output"
    if job.get("job_id"):
        return f"{base}/steps/canvas?jid={job['job_id']}&tab=output"
    if job.get("step_id"):
        return f"{base}/steps/canvas?sid={job['step_id']}&tab=output"
    return base


def _group_identity(job: dict) -> str:
    return str(job.get("raw_name") or job.get("name") or "unknown")


def _group_row(pipeline: str, build: dict, job: dict, state: str) -> dict:
    raw_name = _group_identity(job)
    row = {"name": raw_name, "state": state, "url": _job_url(pipeline, build, job)}
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


def _nightly_pipeline(pipeline: str, analytics: dict) -> dict:
    source_builds = sorted(
        list(analytics.get("builds") or []),
        key=lambda build: str(build.get("created_at") or build.get("date") or ""),
        reverse=True,
    )
    rows = []
    for index, build in enumerate(source_builds[:NIGHTLY_BUILD_LIMIT]):
        hard, soft = _failed_group_maps(pipeline, build)
        current = {**hard, **soft}
        previous_build = source_builds[index + 1] if index + 1 < len(source_builds) else None
        previous: dict[str, dict] = {}
        if previous_build:
            previous_hard, previous_soft = _failed_group_maps(pipeline, previous_build)
            previous = {**previous_hard, **previous_soft}

        new_keys = sorted(current.keys() - previous.keys()) if previous_build else []
        recurring_keys = sorted(current.keys() & previous.keys()) if previous_build else []
        fixed_keys = sorted(previous.keys() - current.keys()) if previous_build else []
        rows.append({
            "number": build.get("number") or build.get("build_number"),
            "created_at": build.get("created_at") or "",
            "state": build.get("state") or "unknown",
            "url": _build_url(pipeline, build),
            "total_groups": build.get("total_jobs") or len(build.get("jobs") or []),
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
            },
        })
    return {
        "pipeline": pipeline,
        "display_name": analytics.get("display_name") or pipeline,
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


def _historical_observation(build: dict, job: dict) -> dict:
    row = {
        "build_number": build.get("number") or build.get("build_number"),
        "build_url": _build_url("amd-ci", build),
        "state": _historical_state(job),
        "observed_at": (
            job.get("finished_at")
            or job.get("started_at")
            or build.get("created_at")
            or build.get("date")
            or ""
        ),
    }
    if job.get("url") or job.get("web_url") or job.get("job_id") or job.get("step_id"):
        row["job_url"] = _job_url("amd-ci", build, job)
    if isinstance(job.get("dur"), (int, float)):
        row["duration_mins"] = job["dur"]
    if job.get("q"):
        row["queue"] = job["q"]
    for key in ("tests", "passed_tests", "failed_tests", "skipped_tests"):
        if isinstance(job.get(key), (int, float)):
            row[key] = job[key]
    retry_evidence = _retry_evidence(job)
    if retry_evidence:
        row["retry_evidence"] = retry_evidence
    return row


def _amd_observations(builds: list[dict]) -> dict[str, list[dict]]:
    observations: dict[str, list[dict]] = defaultdict(list)
    source_builds = sorted(
        builds,
        key=lambda build: str(build.get("created_at") or build.get("date") or ""),
        reverse=True,
    )
    for build in source_builds:
        for job in build.get("jobs") or []:
            name = str(job.get("name") or _group_identity(job))
            observations[name].append(_historical_observation(build, job))
    return observations


def _mixed_outcome_candidate(row: dict, observations: list[dict]) -> dict:
    return {
        **_ranking_row(row),
        "evidence_type": "mixed_outcome_history",
        "observation_count": len(observations),
        "retry_evidence_observation_count": sum("retry_evidence" in item for item in observations),
        "observations": observations,
    }


def _hardware_fold_key(value: Any) -> str:
    """Match the reviewed target audit's hardware-folded label identity."""
    text = MULTISPACE_RE.sub(" ", str(value or "").strip())
    text = AMD_PREFIX_RE.sub("", text)
    text = INTERNAL_AMD_PREFIX_RE.sub("", text)
    text = AMD_DEVICE_SUFFIX_RE.sub("", text).lower().replace("%n", "%N")
    text = re.sub(r"\s+nightly\s+b200\b", "", text)
    text = re.sub(
        r"\((\d+)x(?:h100|h200|a100|b200|gh200)(?:\s*-\s*\d+xmi\d{3,4}b?)?\)",
        r"(\1 gpus)",
        text,
    )
    text = re.sub(r"\((\d+)\s*(?:h100s?|h200s?|a100s?|b200s?|gh200s?)\)", r"(\1 gpus)", text)
    text = re.sub(r"\((?:h100|h200|a100|b200|gh200|cuda|mi\d{3,4}b?)\)", "", text)
    text = re.sub(r"\btests\b", "test", text)
    return MULTISPACE_RE.sub(" ", text).strip()


def _reliability(amd_analytics: dict) -> dict:
    builds = list(amd_analytics.get("builds") or [])
    aggregate = _aggregate_amd_jobs(builds)
    observations = _amd_observations(builds)
    failure_rows = list(amd_analytics.get("failure_ranking") or aggregate)
    duration_rows = list(amd_analytics.get("duration_ranking") or aggregate)
    mixed = [
        row for row in failure_rows
        if int(row.get("passed") or 0) > 0
        and int(row.get("failed") or 0) + int(row.get("soft_failed") or 0) > 0
    ]
    mixed.sort(
        key=lambda row: (
            int(row.get("failed") or 0) + int(row.get("soft_failed") or 0),
            float(row.get("fail_rate") or 0),
            str(row.get("name") or ""),
        ),
        reverse=True,
    )
    latency = [_ranking_row(row) for row in duration_rows if row.get("median_dur") is not None]
    by_median = sorted(latency, key=lambda row: float(row.get("median_dur") or 0), reverse=True)
    by_p90 = sorted(latency, key=lambda row: float(row.get("p90_dur") or 0), reverse=True)
    candidates = [
        _mixed_outcome_candidate(row, observations.get(str(row.get("name") or ""), []))
        for row in mixed[:RANKING_LIMIT]
    ]
    retry_analysis = dict(amd_analytics.get("retry_analysis") or {})
    retry_analysis.setdefault("summary", {
        "builds_evaluated": 0,
        "builds_with_retries": 0,
        "retry_attempt_count": 0,
        "failed_then_passed_recovery_count": 0,
    })
    retry_analysis.setdefault("retry_attempts", [])
    retry_analysis.setdefault("failed_then_passed_recoveries", [])
    retry_analysis["evidence_type"] = "explicit_retry_recovery"
    return {
        "source_pipeline": "amd-ci",
        "evidence_definitions": {
            "mixed_outcome_history": (
                "Passed and failing observations across AMD nightlies; not proof of a retry recovery."
            ),
            "explicit_retry_recovery": "Buildkite retry metadata linking a failed attempt to a passed retry.",
        },
        "denominator": {
            "unit": "nightly job runs",
            "builds": len(builds),
            "jobs_ranked": len(failure_rows),
        },
        "flaky_candidates": candidates,
        "latency_rankings": {
            "by_median_duration": by_median[:RANKING_LIMIT],
            "by_p90_duration": by_p90[:RANKING_LIMIT],
        },
        "retry_analysis": retry_analysis,
    }


def _gating(targets: dict, candidates: dict, matrix: dict, capacity: dict, analytics: dict) -> dict:
    groups = list(targets.get("groups") or [])
    target_summary = dict(targets.get("summary") or {})
    candidate_summary = dict(candidates.get("summary") or {})
    matrix_summary = dict(matrix.get("summary") or {})
    matrix_cells = int(matrix_summary.get("hardware_cells") or 0)
    canonical_keys = {_hardware_fold_key(row.get("label")) for row in groups}
    source_builds = (analytics.get("ci") or {}).get("builds") or []
    source_jobs = source_builds[0].get("jobs") or [] if source_builds else []
    jobs_by_key: dict[str, list[dict]] = defaultdict(list)
    for job in source_jobs:
        jobs_by_key[_hardware_fold_key(job.get("raw_name") or job.get("name"))].append(job)

    active_extras = []
    seen_extra_keys = set()
    for group in capacity.get("groups") or []:
        if group.get("in_capacity_scope") is False:
            continue
        key = _hardware_fold_key(group.get("label"))
        if not key or key in canonical_keys or key in seen_extra_keys:
            continue
        seen_extra_keys.add(key)
        matches = jobs_by_key.get(key) or []
        states = {str(job.get("state") or "").lower() for job in matches}
        if states and states <= {"passed"}:
            signal = "green"
        elif states & (FAILED_STATES | SOFT_FAILED_STATES):
            signal = "red"
        else:
            signal = "gray"
        active_extras.append({
            "id": f"active-{len(active_extras) + 1}",
            "label": group.get("label") or "Unknown active group",
            "area": group.get("area") or "other",
            "target_signal": signal,
            "readiness_signal": signal,
            "owner": "",
            "note": "Currently gated outside the reviewed canonical target list.",
            "target_origin": "active_outside_canonical",
        })
    active_groups = [{**row, "target_origin": "canonical"} for row in groups] + active_extras
    active_signals = Counter(str(row.get("target_signal") or "gray") for row in active_groups)
    return {
        "denominators": {
            "target_signal_counts": {
                "value": len(groups),
                "unit": "canonical target groups",
            },
            "candidate_decisions": {
                "value": len(candidates.get("rows") or []),
                "unit": "latest candidate rows",
            },
            "matrix_group_counts": {
                "value": int(matrix_summary.get("unique_groups") or len(matrix.get("rows") or [])),
                "unit": "configured AMD groups",
            },
            "matrix_cell_states": {
                "value": matrix_cells,
                "unit": "configured AMD hardware cells",
            },
        },
        "target_summary": target_summary,
        "target_groups": groups,
        "active_target_summary": {
            "target_group_count": len(active_groups),
            "canonical_group_count": len(groups),
            "active_outside_canonical_count": len(active_extras),
            "by_target_signal": dict(sorted(active_signals.items())),
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


def _queue(snapshot: dict, queue_jobs: dict) -> dict:
    return {
        "snapshot": snapshot,
        "queue_jobs": queue_jobs,
        "provenance": {
            "snapshot": {
                "path": SOURCE_FILES["queue_timeseries"],
                "timestamp": snapshot.get("ts"),
                "run_id": snapshot.get("run_id"),
                "sources": snapshot.get("sources") or {},
            },
            "jobs": {
                "path": SOURCE_FILES["queue_jobs"],
                "timestamp": queue_jobs.get("ts"),
                "source_counts": _job_source_counts(queue_jobs),
            },
        },
    }


def _omni(queue_snapshot: dict, queue_jobs: dict, heuristic: dict, issue_state: dict) -> dict:
    jobs = {
        state: [job for job in queue_jobs.get(state) or [] if str(job.get("workload") or "").lower() == "omni"]
        for state in ("pending", "running")
    }
    waiting_by_queue: dict[str, int] = {}
    running_by_queue: dict[str, int] = {}
    for queue_name, stats in (queue_snapshot.get("queues") or {}).items():
        waiting = int((stats.get("waiting_by_workload") or {}).get("omni") or 0)
        running = int((stats.get("running_by_workload") or {}).get("omni") or 0)
        if waiting:
            waiting_by_queue[queue_name] = waiting
        if running:
            running_by_queue[queue_name] = running

    waiting = sum(waiting_by_queue.values())
    running = sum(running_by_queue.values())
    if not waiting_by_queue:
        waiting = sum(not job.get("analysis_excluded") for job in jobs["pending"])
    if not running_by_queue:
        running = sum(not job.get("analysis_excluded") for job in jobs["running"])

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
        },
        "heuristic_thresholds": heuristic,
        "current_jobs": jobs,
        "issue_state": issue_state,
        "provenance": {
            "queue_snapshot_ts": queue_snapshot.get("ts"),
            "queue_jobs_ts": queue_jobs.get("ts"),
        },
    }


def _trajectory(ci_health: dict, group_changes: dict) -> dict:
    pipelines = []
    for source_key, pipeline in (("amd", "amd-ci"), ("upstream", "ci")):
        block = ci_health.get(source_key) or {}
        builds = []
        for build in block.get("builds") or []:
            keys = (
                "build_number", "created_at", "state", "pass_rate", "failed", "errors",
                "jobs_failed", "jobs_soft_failed", "test_groups_passing_all", "unique_test_groups",
            )
            builds.append({key: build[key] for key in keys if key in build})
        pipelines.append({"pipeline": pipeline, "builds": builds})
    return {
        "pipeline_order": ["amd-ci", "ci"],
        "pipelines": pipelines,
        "group_changes": {
            "days": group_changes.get("days"),
            "total_changes": group_changes.get("total_changes") or len(group_changes.get("changes") or []),
            "recent": list(group_changes.get("changes") or [])[:CHANGE_LIMIT],
        },
    }


def _attention(nightly: dict, reliability: dict, gating: dict, queue: dict, omni: dict) -> list[dict]:
    items = []
    amd_builds = (nightly.get("pipelines") or [{}])[0].get("builds") or []
    latest = amd_builds[0] if amd_builds else {}
    new_groups = (latest.get("transitions") or {}).get("new") or []
    if new_groups:
        items.append({"kind": "nightly_new_failures", "severity": "critical", "count": len(new_groups)})
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
    target_red = int(((gating.get("target_summary") or {}).get("by_target_signal") or {}).get("red") or 0)
    if target_red:
        items.append({"kind": "gating_red_targets", "severity": "warning", "count": target_red})
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


def build_snapshot(data_dir: Path | str, generated_at: str | None = None) -> dict:
    data_dir = Path(data_dir)
    paths = {name: data_dir / filename for name, filename in SOURCE_FILES.items()}
    loaded = {name: _load_json(path) for name, path in paths.items() if path.suffix == ".json"}
    queue_snapshot = load_latest_queue_snapshot(paths["queue_timeseries"])

    analytics = loaded.get("analytics") or {}
    pipeline_blocks = [
        _nightly_pipeline("amd-ci", analytics.get("amd-ci") or {}),
        _nightly_pipeline("ci", analytics.get("ci") or {}),
    ]
    nightly = {
        "primary_pipeline": "amd-ci",
        "pipeline_order": ["amd-ci", "ci"],
        "transition_basis": "failed and soft-failed groups versus the preceding nightly",
        "pipelines": pipeline_blocks,
    }
    reliability = _reliability(analytics.get("amd-ci") or {})
    gating = _gating(
        loaded.get("gating_targets") or {},
        loaded.get("gating_target_candidates") or {},
        loaded.get("amd_test_matrix") or {},
        loaded.get("capacity_monitor") or {},
        analytics,
    )
    queue = _queue(queue_snapshot, loaded.get("queue_jobs") or {})
    omni = _omni(
        queue_snapshot,
        loaded.get("queue_jobs") or {},
        loaded.get("omni_heuristic") or {},
        loaded.get("omni_issue_state") or {},
    )
    trajectory = _trajectory(loaded.get("ci_health") or {}, loaded.get("group_changes") or {})
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

    return {
        "schema_version": 2,
        "generated_at": generated_at or _utc_now(),
        "sources": sources,
        "home": home,
        "attention": attention,
        "nightly": nightly,
        "reliability": reliability,
        "gating": gating,
        "queue": queue,
        "trajectory": trajectory,
        "omni": omni,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", "--data-dir", dest="input_dir", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", help="Output path (default: INPUT_DIR/operations_v2.json)")
    parser.add_argument("--generated-at", help="Override generation timestamp for reproducible builds")
    args = parser.parse_args(argv)

    input_dir = Path(args.input_dir)
    output = Path(args.output) if args.output else input_dir / DEFAULT_OUTPUT_NAME
    payload = build_snapshot(input_dir, generated_at=args.generated_at)
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(encoded)
    print(f"Wrote {output} ({len(encoded.encode('utf-8'))} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
