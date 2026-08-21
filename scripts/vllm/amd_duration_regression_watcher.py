#!/usr/bin/env python3
"""Open one state-owned issue for AMD main test groups that become slower.

The source is amd-ci.all_main_reliability from analytics.json. For each strict
label + step + hardware + queue identity, the watcher compares the median wall
completion time of the latest three successful final attempts with the median
of the preceding six to twelve successful attempts. Queue wait is excluded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.ci.managed_issue import (  # noqa: E402
    DASHBOARD_REPO,
    GitHubIssueClient,
    normalize_managed_state,
    reconcile_managed_issue,
    repo_owner,
    validate_target_repo,
)
from vllm.ci.reliability_history import (  # noqa: E402
    hydrate_reliability_observations,
    validate_all_main_reliability,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
ANALYTICS = ROOT / "data" / "vllm" / "ci" / "analytics.json"
STATE = ROOT / "data" / "vllm" / "ci" / "open_amd_duration_regression_issues.json"

PIPELINE = "amd-ci"
RECENT_RUNS = 3
MIN_BASELINE_RUNS = 6
MAX_BASELINE_RUNS = 12
REGRESSION_THRESHOLD = 0.15
MAX_DATA_AGE = timedelta(hours=3)
INCIDENT_MAX_AGE = timedelta(hours=72)
MAX_ISSUE_ROWS = 40
MAX_EVIDENCE_GROUPS = 12
OWNERSHIP_MARKER = "<!-- vllm-ci-dashboard:managed-alert:amd-duration-regression:v1 -->"
LABEL_SPECS = [
    (
        "amd-duration-regression",
        "d93f0b",
        "AMD main test-group completion time is at least 15 percent slower",
    ),
    ("automated", "6f42c1", "Managed by dashboard automation"),
    ("workstream:dev", "1d76db", "AMD CI test-area development"),
]
DASHBOARD_URL = (
    "https://andreaskaratzas.github.io/vllm-ci-dashboard/"
    "?ops_analytics_view=latency#ci-analytics"
)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _positive_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number > 0 else None


def _default_state() -> dict:
    return {
        "schema_version": 1,
        "active": {},
        "issue": None,
        "suppressed": False,
        "last_fingerprint": "",
        "last_run": "",
    }


def _read_state() -> dict:
    if not STATE.exists():
        return _default_state()
    try:
        raw = json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return _default_state()
    normalized = normalize_managed_state(raw)
    raw_active = raw.get("active") if isinstance(raw, dict) else {}
    normalized["active"] = {
        str(group_id): dict(row)
        for group_id, row in (
            raw_active.items() if isinstance(raw_active, dict) else []
        )
        if isinstance(row, dict)
    }
    return normalized


def _write_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def _read_reliability() -> dict | None:
    if not ANALYTICS.exists():
        return None
    try:
        analytics = json.loads(ANALYTICS.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    block = analytics.get(PIPELINE) if isinstance(analytics, dict) else None
    reliability = block.get("all_main_reliability") if isinstance(block, dict) else None
    return reliability if isinstance(reliability, dict) else None


def _is_fresh(reliability: dict, now: datetime) -> bool:
    generated = _parse_ts(reliability.get("generated_at"))
    if generated is None:
        return False
    age = now - generated
    return -timedelta(minutes=15) <= age <= MAX_DATA_AGE


def _observation_rank(row: dict) -> tuple[str, str, str]:
    return (
        str(row.get("observed_at") or ""),
        str(row.get("finished_at") or ""),
        str(row.get("started_at") or ""),
    )


def _final_successful_runs(group: dict, reliability: dict | None = None) -> list[dict]:
    """Return one final successful attempt per build, newest first."""
    by_build: dict[int, list[dict]] = {}
    observations = [
        row
        for row in group.get("observations") or []
        if isinstance(row, dict)
    ]
    if reliability is not None and reliability.get("schema_version") == 2:
        observations = hydrate_reliability_observations(
            reliability,
            observations,
            pipeline_slug=PIPELINE,
        )
    for observation in observations:
        build_number = observation.get("build_number")
        result = str(observation.get("result") or "")
        if (
            not isinstance(build_number, int)
            or isinstance(build_number, bool)
            or observation.get("eligible_for_reliability") is not True
            or result not in {"passed", "failed", "soft_fail"}
        ):
            continue
        row = {
            "build_number": build_number,
            "build_url": str(observation.get("build_url") or ""),
            "job_id": str(observation.get("job_id") or ""),
            "job_url": str(observation.get("job_url") or ""),
            "observed_at": str(observation.get("observed_at") or ""),
            "started_at": str(observation.get("started_at") or ""),
            "finished_at": str(observation.get("finished_at") or ""),
            "result": result,
            "wall_completion_mins": _positive_float(
                observation.get("wall_completion_mins")
            ),
            "retry_evidence": dict(observation.get("retry_evidence") or {}),
        }
        by_build.setdefault(build_number, []).append(row)

    final_runs: list[dict] = []
    for rows in by_build.values():
        predecessor: dict[str, str] = {}
        for row in rows:
            retry = row.get("retry_evidence") or {}
            retried_in = str(retry.get("retried_in_job_id") or "")
            retry_source = retry.get("retry_source") or {}
            source_job = (
                str(retry_source.get("job_id") or "")
                if isinstance(retry_source, dict)
                else ""
            )
            if retried_in and row.get("job_id"):
                predecessor[retried_in] = str(row["job_id"])
            if source_job and row.get("job_id"):
                predecessor[str(row["job_id"])] = source_job

        def retry_depth(row: dict) -> int:
            depth = 0
            job_id = str(row.get("job_id") or "")
            seen: set[str] = set()
            while job_id in predecessor and job_id not in seen:
                seen.add(job_id)
                depth += 1
                job_id = predecessor[job_id]
            return depth

        final = max(
            rows,
            key=lambda row: (
                retry_depth(row),
                _observation_rank(row),
                row.get("result") == "passed",
                str(row.get("job_id") or ""),
            ),
        )
        if (
            final.get("result") == "passed"
            and _positive_float(final.get("wall_completion_mins")) is not None
        ):
            final_runs.append(final)

    return sorted(final_runs, key=_observation_rank, reverse=True)


def _recent_evidence(runs: list[dict]) -> list[dict]:
    return [
        {
            "build_number": run.get("build_number"),
            "build_url": run.get("build_url"),
            "job_id": run.get("job_id"),
            "job_url": run.get("job_url"),
            "observed_at": run.get("observed_at"),
            "wall_completion_mins": run.get("wall_completion_mins"),
        }
        for run in runs[:RECENT_RUNS]
    ]


def evaluate_regressions(reliability: dict, state: dict) -> dict[str, dict]:
    """Evaluate current regressions while retaining each incident's baseline."""
    previous = state.get("active") if isinstance(state, dict) else {}
    previous = previous if isinstance(previous, dict) else {}
    generated = _parse_ts(reliability.get("generated_at"))
    cutoff = (
        generated - INCIDENT_MAX_AGE
        if generated is not None
        else datetime.min.replace(tzinfo=timezone.utc)
    )
    active: dict[str, dict] = {}
    evaluated: set[str] = set()

    for group in reliability.get("groups") or []:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id") or "")
        if not group_id:
            continue
        runs = _final_successful_runs(group, reliability)
        recent = runs[:RECENT_RUNS]
        existing = previous.get(group_id)
        existing = existing if isinstance(existing, dict) else {}
        if len(recent) < RECENT_RUNS:
            continue

        baseline_runs = runs[
            RECENT_RUNS : RECENT_RUNS + MAX_BASELINE_RUNS
        ]
        baseline_mins = _positive_float(existing.get("baseline_mins"))
        if baseline_mins is None:
            if len(baseline_runs) < MIN_BASELINE_RUNS:
                continue
            baseline_mins = statistics.median(
                float(run["wall_completion_mins"]) for run in baseline_runs
            )
            baseline_count = len(baseline_runs)
        else:
            baseline_count = int(existing.get("baseline_count") or 0)
            if baseline_count <= 0:
                baseline_count = len(baseline_runs)

        evaluated.add(group_id)
        latest_observed_at = str(recent[0].get("observed_at") or "")
        latest_at = _parse_ts(latest_observed_at)
        if latest_at is None or latest_at < cutoff:
            continue

        recent_median = statistics.median(
            float(run["wall_completion_mins"]) for run in recent
        )
        threshold_mins = baseline_mins * (1.0 + REGRESSION_THRESHOLD)
        if recent_median + 1e-9 < threshold_mins:
            continue

        increase_mins = recent_median - baseline_mins
        increase_pct = increase_mins / baseline_mins * 100.0
        active[group_id] = {
            "group_id": group_id,
            "name": str(group.get("name") or group.get("raw_name") or "unknown"),
            "raw_name": str(group.get("raw_name") or group.get("name") or "unknown"),
            "step_key": str(group.get("step_key") or ""),
            "hardware": str(group.get("hardware") or "unknown"),
            "queue": str(group.get("queue") or ""),
            "baseline_mins": round(baseline_mins, 3),
            "baseline_count": baseline_count,
            "recent_median_mins": round(recent_median, 3),
            "recent_count": RECENT_RUNS,
            "increase_mins": round(increase_mins, 3),
            "increase_pct": round(increase_pct, 3),
            "latest_build_number": recent[0].get("build_number"),
            "latest_build_url": recent[0].get("build_url"),
            "latest_job_url": recent[0].get("job_url"),
            "latest_observed_at": latest_observed_at,
            "first_detected_at": str(
                existing.get("first_detected_at")
                or reliability.get("generated_at")
                or latest_observed_at
            ),
            "recent_evidence": _recent_evidence(recent),
        }

    for group_id, row in previous.items():
        if group_id in active or group_id in evaluated or not isinstance(row, dict):
            continue
        last_seen = _parse_ts(row.get("latest_observed_at"))
        if last_seen is not None and last_seen >= cutoff:
            active[str(group_id)] = dict(row)

    return active


def _fingerprint(active: dict[str, dict]) -> str:
    compact = [
        {
            "group_id": group_id,
            "baseline_mins": row.get("baseline_mins"),
            "recent_median_mins": row.get("recent_median_mins"),
            "latest_build_number": row.get("latest_build_number"),
        }
        for group_id, row in sorted(active.items())
    ]
    encoded = json.dumps(compact, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _md(value: Any) -> str:
    return str(value or "-").replace("|", "\\|").replace("\n", " ")


def _minutes(value: Any) -> str:
    number = _positive_float(value)
    return f"{number:.1f}m" if number is not None else "-"


def _issue_title(active: dict[str, dict]) -> str:
    return (
        f"AMD main duration: {len(active)} test groups at least "
        f"{REGRESSION_THRESHOLD * 100:.0f}% slower"
    )


def _issue_body(active: dict[str, dict], reliability: dict, run_url: str, owner: str) -> str:
    rows = sorted(
        active.values(),
        key=lambda row: (
            -float(row.get("increase_pct") or 0),
            str(row.get("name") or ""),
        ),
    )
    generated_at = str(reliability.get("generated_at") or "unknown")
    lines = [
        "## AMD origin/main completion-time regression alert",
        "",
        (
            f"**{len(rows)} strict AMD test groups have a latest-three-run median "
            f"at least {REGRESSION_THRESHOLD * 100:.0f}% above their retained baseline.**"
        ),
        "",
        "Signal rules:",
        "",
        (
            f"- Recent signal: median wall completion time of the latest "
            f"{RECENT_RUNS} successful final attempts."
        ),
        (
            f"- Baseline: median of the preceding {MIN_BASELINE_RUNS}-"
            f"{MAX_BASELINE_RUNS} successful attempts."
        ),
        "- Scope: exhaustive completed amd-ci builds on branch=main.",
        "- Identity: exact test label + Buildkite step key + hardware + queue.",
        "- Queue wait is excluded; this alert measures job start-to-finish time.",
        "- The baseline is fixed while an incident is open so a slowdown cannot normalize itself.",
        (
            f"- Resolution: recent median below baseline + "
            f"{REGRESSION_THRESHOLD * 100:.0f}%, or no fresh observation for 72 hours."
        ),
        "",
        f"Collected at **{generated_at}**. [Open AMD completion analytics]({DASHBOARD_URL}).",
        "",
        "| test group | hardware / queue | baseline | recent median | increase | latest |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in rows[:MAX_ISSUE_ROWS]:
        job_url = str(row.get("latest_job_url") or row.get("latest_build_url") or "")
        group = _md(row.get("name"))
        group_md = f"[{group}]({job_url})" if job_url else group
        increase = (
            f"+{_minutes(row.get('increase_mins'))} "
            f"(+{float(row.get('increase_pct') or 0):.1f}%)"
        )
        latest_build = int(row.get("latest_build_number") or 0)
        latest_url = str(row.get("latest_build_url") or job_url)
        latest_md = (
            f"[#{latest_build}]({latest_url})"
            if latest_build and latest_url
            else f"#{latest_build}" if latest_build else "-"
        )
        lines.append(
            f"| {group_md} | {_md(row.get('hardware'))} / {_md(row.get('queue'))} "
            f"| {_minutes(row.get('baseline_mins'))} "
            f"| {_minutes(row.get('recent_median_mins'))} | {increase} | {latest_md} |"
        )
    if len(rows) > MAX_ISSUE_ROWS:
        lines.extend(
            [
                "",
                f"{len(rows) - MAX_ISSUE_ROWS} additional groups are retained in watcher state.",
            ]
        )

    for row in rows[:MAX_EVIDENCE_GROUPS]:
        lines.extend(["", f"### {_md(row.get('name'))}"])
        for evidence in row.get("recent_evidence") or []:
            build = int(evidence.get("build_number") or 0)
            url = str(evidence.get("job_url") or evidence.get("build_url") or "")
            label = f"amd-ci #{build}" if build else "AMD observation"
            link = f"[{label}]({url})" if url else label
            lines.append(
                f"- {link}: {_minutes(evidence.get('wall_completion_mins'))}, "
                f"{_md(evidence.get('observed_at'))}"
            )

    lines.extend(
        [
            "",
            f"GitHub assignee: {owner}.",
            "",
            (
                f"*Managed by amd_duration_regression_watcher.py from {run_url}. "
                "Only this tracked umbrella issue can be updated or closed by the watcher.*"
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def run() -> int:
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY") or DASHBOARD_REPO
    validate_target_repo(repo)
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_url = (
        f"https://github.com/{repo}/actions/runs/{run_id}"
        if run_id
        else f"https://github.com/{repo}"
    )

    reliability = _read_reliability()
    if not reliability or not validate_all_main_reliability(reliability, PIPELINE):
        log.error("Strict exhaustive AMD main reliability is unavailable; refusing issue mutations")
        return 0
    now = datetime.now(timezone.utc)
    if not _is_fresh(reliability, now):
        log.error("AMD main reliability is stale or future-dated; refusing issue mutations")
        return 0
    if not token:
        log.warning("GITHUB_TOKEN not set; leaving issue state untouched")
        return 0

    state = _read_state()
    active = evaluate_regressions(reliability, state)
    state["active"] = active
    observed_at = str(
        reliability.get("generated_at") or now.strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    client = GitHubIssueClient(token, repo)
    reconciled = reconcile_managed_issue(
        state,
        active=bool(active),
        fingerprint=_fingerprint(active),
        title=_issue_title(active),
        body=_issue_body(active, reliability, run_url, repo_owner(repo)),
        ownership_marker=OWNERSHIP_MARKER,
        recovery_body=(
            "No strict AMD origin/main test group currently has a latest-three-run "
            f"median at least {REGRESSION_THRESHOLD * 100:.0f}% above its fixed "
            "completion-time baseline. Closing this tracked umbrella issue.\n\n"
            f"*{run_url}*"
        ),
        observed_at=observed_at,
        label_specs=LABEL_SPECS,
        client=client,
    )
    _write_state(reconciled)
    log.info(
        "AMD duration watcher evaluated %d active regressions; issue=%s suppressed=%s",
        len(active),
        (reconciled.get("issue") or {}).get("number"),
        reconciled.get("suppressed"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
