#!/usr/bin/env python3
"""Build the compact, authoritative v2 operations dashboard snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_INPUT = ROOT / "data" / "vllm" / "ci"
DEFAULT_OUTPUT_NAME = "operations_v2.json"
NIGHTLY_BUILD_LIMIT = 30
RANKING_LIMIT = 20
CHANGE_LIMIT = 20
GROUP_HISTORY_LIMIT = 60
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
AMD_TARGET_SUFFIX_RE = re.compile(
    r"(?<=\d)(?:x)?mi\d{2,4}b?(?:[_-]\d+)?(?=\))",
    re.IGNORECASE,
)
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
    except (json.JSONDecodeError, OSError):
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
    return "mi355b" in str(value or "").lower()


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
            "number": build.get("number") or build.get("build_number"),
            "source_pipeline": pipeline,
            "created_at": build.get("created_at") or "",
            "state": build.get("state") or "unknown",
            "url": _build_url(pipeline, build),
            "commit": build.get("commit") or build.get("commit_sha") or "",
            "message": build.get("message") or "",
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
    text = re.sub(r"-\s*\d+x?mi\d{2,4}b?(?:[_-]\d+)?(?=\))", "", text)
    text = re.sub(r"-\s*\d*x?mi(?=\))", "", text)
    return MULTISPACE_RE.sub(" ", text).strip()


def _strict_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
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
    retry_analysis = _normalize_retry_analysis(
        retry_source,
        cohort_build_numbers,
        pipeline_slug=pipeline_slug,
    )
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
    }


def _matrix_evidence(matrix: dict) -> dict[str, dict]:
    evidence_by_key: dict[str, dict] = {}
    for row in matrix.get("rows") or []:
        evidence = []
        for architecture, cell in (row.get("cells") or {}).items():
            if not cell.get("exists"):
                continue
            raw_state = cell.get("latest_state") or "unknown"
            evidence.append({
                "architecture": architecture,
                "state": _historical_state({"state": raw_state}),
                "raw_state": raw_state,
                "build_number": cell.get("latest_build_number") or (matrix.get("source") or {}).get("latest_build_number"),
                "url": cell.get("latest_url") or "",
                "source": "amd_matrix",
                "source_pipeline": "amd-ci",
            })
        if not evidence:
            continue
        states = {item["state"] for item in evidence}
        if "hard" in states:
            state = "hard"
        elif "soft" in states:
            state = "soft"
        elif states == {"passed"}:
            state = "passed"
        else:
            state = "unknown"
        evidence_by_key[_target_match_key(row.get("canonical_title") or row.get("title"))] = {
            "state": state,
            "build_number": max((item.get("build_number") or 0 for item in evidence), default=None),
            "observed_at": matrix.get("generated_at"),
            "source_pipeline": "amd-ci",
            "evidence": evidence,
        }
    return evidence_by_key


def _assessment(latest: dict, reliability: dict) -> str:
    state = latest.get("state") or "unknown"
    if state == "hard":
        return "failing_now"
    if state == "soft":
        return "soft_failing_now"
    if state != "passed":
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


def _gating(targets: dict, candidates: dict, matrix: dict, capacity: dict, reliability: dict) -> dict:
    groups = list(targets.get("groups") or [])
    target_summary = dict(targets.get("summary") or {})
    candidate_summary = dict(candidates.get("summary") or {})
    matrix_summary = dict(matrix.get("summary") or {})
    matrix_cells = int(matrix_summary.get("hardware_cells") or 0)
    matrix_by_key = _matrix_evidence(matrix)
    history_pipeline = str(reliability.get("source_pipeline") or "ci")
    catalog_by_key: dict[str, list[dict]] = defaultdict(list)
    for row in reliability.get("group_catalog") or []:
        catalog_by_key[_target_match_key(row.get("name"))].append(row)
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
        histories = catalog_by_key.get(key) or []
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
        latest = matrix_by_key.get(key) or {
            "state": "unknown",
            "build_number": None,
            "observed_at": matrix.get("generated_at"),
            "source_pipeline": "amd-ci",
            "evidence": [],
        }
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
            "main_reliability": reliability_summary,
            "nightly_green_streak": target_history.get("nightly_green_streak") or 0,
            "last_incident": latest_incident,
            "assessment": _assessment(latest, reliability_summary),
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
    return {
        "definitions": {
            "reviewed_plan": "Intent from the reviewed target configuration; not an ownership assignment.",
            "latest_amd_result": "Latest exact AMD matrix evidence for this group.",
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
        if _is_excluded_queue(name):
            continue
        row = {
            key: source.get(key)
            for key in (
                "waiting", "running", "zombie_waiting", "zombie_running",
                "connected_agents", "count_source", "p50_wait", "p50_wait_source",
                "p95_wait", "p95_wait_source", "p99_wait", "p99_wait_source",
            )
            if source.get(key) is not None
        }
        has_activity = any(int(row.get(key) or 0) for key in ("waiting", "running", "zombie_waiting", "zombie_running"))
        has_measurement = any(row.get(key) is not None for key in ("p50_wait", "p95_wait", "p99_wait", "connected_agents"))
        if has_activity or has_measurement:
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
        "tracked_queue_count": len(snapshot.get("queues") or {}),
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


def _omni(queue_snapshot: dict, queue_jobs: dict, heuristic: dict, issue_state: dict) -> dict:
    jobs = {
        state: [
            job for job in queue_jobs.get(state) or []
            if str(job.get("workload") or "").lower() == "omni"
            and not _is_excluded_queue(job.get("queue") or job.get("q"))
        ]
        for state in ("pending", "running")
    }
    waiting_by_queue: dict[str, int] = {}
    running_by_queue: dict[str, int] = {}
    for queue_name, stats in (queue_snapshot.get("queues") or {}).items():
        if _is_excluded_queue(queue_name):
            continue
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
            "source_paths": {
                "queue_aggregates": SOURCE_FILES["queue_timeseries"],
                "queue_jobs": SOURCE_FILES["queue_jobs"],
                "heuristic": SOURCE_FILES["omni_heuristic"],
                "issue_state": SOURCE_FILES["omni_issue_state"],
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
            },
        },
    }


def _trajectory(reliability: dict, group_changes: dict) -> dict:
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
        "provenance": {
            "source_paths": {
                "build_history": SOURCE_FILES["analytics"],
                "group_changes": SOURCE_FILES["group_changes"],
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


def build_snapshot(data_dir: Path | str, generated_at: str | None = None) -> dict:
    data_dir = Path(data_dir)
    paths = {name: data_dir / filename for name, filename in SOURCE_FILES.items()}
    loaded = {name: _load_json(path) for name, path in paths.items() if path.suffix == ".json"}
    queue_history = load_queue_history(paths["queue_timeseries"])
    queue_snapshot = _filter_queue_snapshot(load_latest_queue_snapshot(paths["queue_timeseries"]))

    analytics = loaded.get("analytics") or {}
    amd_nightly = _nightly_pipeline("amd-ci", analytics.get("amd-ci") or {})
    upstream_parity = _nightly_pipeline("ci", analytics.get("ci") or {})
    pipeline_blocks = [amd_nightly, upstream_parity]
    nightly = {
        "primary_pipeline": "amd-ci",
        "pipeline_order": ["amd-ci", "ci"],
        "history_window_days": NIGHTLY_BUILD_LIMIT,
        "transition_basis": "failed and soft-failed groups versus the preceding nightly",
        "canonical_history": amd_nightly,
        "upstream_parity": upstream_parity,
        "pipelines": pipeline_blocks,
    }
    reliability = _reliability(analytics.get("ci") or {}, pipeline_slug="ci")
    gating = _gating(
        loaded.get("gating_targets") or {},
        loaded.get("gating_target_candidates") or {},
        loaded.get("amd_test_matrix") or {},
        loaded.get("capacity_monitor") or {},
        reliability,
    )
    queue = _queue(queue_snapshot, loaded.get("queue_jobs") or {}, queue_history)
    omni = _omni(
        queue_snapshot,
        queue.get("queue_jobs") or {},
        loaded.get("omni_heuristic") or {},
        loaded.get("omni_issue_state") or {},
    )
    trajectory = _trajectory(reliability, loaded.get("group_changes") or {})
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
