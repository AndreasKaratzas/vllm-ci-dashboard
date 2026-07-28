#!/usr/bin/env python3
"""Open one state-owned issue for unresolved AMD test-group failures on main.

The source is amd-ci.all_main_reliability from analytics.json: an exhaustive
Buildkite branch=main cohort of completed passed/failed builds with exact
terminal job evidence. A strict group is identified by label, step key,
hardware, and queue. Within one build the latest attempt wins, so a successful
retry does not create an incident.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
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
from vllm.ci.reliability_history import validate_all_main_reliability  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
ANALYTICS = ROOT / "data" / "vllm" / "ci" / "analytics.json"
STATE = ROOT / "data" / "vllm" / "ci" / "open_amd_main_failure_issues.json"

PIPELINE = "amd-ci"
MAX_DATA_AGE = timedelta(hours=3)
INCIDENT_MAX_AGE = timedelta(hours=72)
MAX_ISSUE_ROWS = 50
OWNERSHIP_MARKER = "<!-- vllm-ci-dashboard:managed-alert:amd-main-failure:v1 -->"
LABEL_SPECS = [
    ("amd-main-failure", "d73a49", "Unresolved AMD test-group failure on origin/main"),
    ("automated", "6f42c1", "Managed by dashboard automation"),
    ("workstream:dev", "1d76db", "AMD CI test-area development"),
]
DASHBOARD_URL = (
    "https://andreaskaratzas.github.io/vllm-ci-dashboard/"
    "?ops_analytics_view=groups#ci-analytics"
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


def _default_state() -> dict:
    return {
        "schema_version": 1,
        "initialized": False,
        "processed_build_numbers": [],
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
    state = normalize_managed_state(raw)
    state["initialized"] = bool(raw.get("initialized"))
    state["processed_build_numbers"] = [
        int(number)
        for number in raw.get("processed_build_numbers") or []
        if isinstance(number, int) and not isinstance(number, bool) and number > 0
    ]
    state["active"] = {
        str(group_id): dict(row)
        for group_id, row in (raw.get("active") or {}).items()
        if isinstance(row, dict)
    }
    return state


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


def _observations_by_build(reliability: dict) -> dict[int, dict[str, dict]]:
    candidates: dict[int, dict[str, list[dict]]] = {}
    for group in reliability.get("groups") or []:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id") or "")
        if not group_id:
            continue
        for observation in group.get("observations") or []:
            if not isinstance(observation, dict):
                continue
            result = str(observation.get("result") or "")
            number = observation.get("build_number")
            if (
                not isinstance(number, int)
                or isinstance(number, bool)
                or result not in {"passed", "failed", "soft_fail"}
                or observation.get("eligible_for_reliability") is not True
            ):
                continue
            row = {
                "group_id": group_id,
                "name": str(group.get("name") or group.get("raw_name") or "unknown"),
                "raw_name": str(group.get("raw_name") or group.get("name") or "unknown"),
                "step_key": str(group.get("step_key") or ""),
                "hardware": str(group.get("hardware") or "unknown"),
                "queue": str(group.get("queue") or ""),
                "result": result,
                "build_number": number,
                "build_url": str(observation.get("build_url") or ""),
                "job_id": str(observation.get("job_id") or ""),
                "job_url": str(observation.get("job_url") or ""),
                "observed_at": str(observation.get("observed_at") or ""),
                "started_at": str(observation.get("started_at") or ""),
                "finished_at": str(observation.get("finished_at") or ""),
                "retry_evidence": dict(observation.get("retry_evidence") or {}),
            }
            candidates.setdefault(number, {}).setdefault(group_id, []).append(row)

    by_build: dict[int, dict[str, dict]] = {}
    for number, groups in candidates.items():
        for group_id, rows in groups.items():
            predecessor = {
                str((row.get("retry_evidence") or {}).get("retried_in_job_id") or ""): row["job_id"]
                for row in rows
                if (row.get("retry_evidence") or {}).get("retried_in_job_id")
            }

            def retry_depth(row: dict) -> int:
                depth = 0
                job_id = row.get("job_id") or ""
                seen = set()
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
            by_build.setdefault(number, {})[group_id] = final
    return by_build


def _build_rank(build: dict) -> tuple[str, int]:
    number = build.get("number")
    return (
        str(build.get("finished_at") or build.get("created_at") or ""),
        int(number) if isinstance(number, int) and not isinstance(number, bool) else 0,
    )


def _readable_state_copy(state: dict) -> dict:
    normalized = normalize_managed_state(state)
    normalized["initialized"] = bool(state.get("initialized"))
    normalized["processed_build_numbers"] = list(state.get("processed_build_numbers") or [])
    normalized["active"] = {
        str(group_id): dict(row)
        for group_id, row in (state.get("active") or {}).items()
        if isinstance(row, dict)
    }
    return normalized


def advance_incidents(reliability: dict, state: dict) -> dict:
    """Apply newly completed builds to unresolved strict-group incidents."""
    updated = _readable_state_copy(state)
    catalog = [
        build
        for build in reliability.get("builds") or []
        if isinstance(build, dict)
        and isinstance(build.get("number"), int)
        and not isinstance(build.get("number"), bool)
    ]
    catalog_by_number = {int(build["number"]): build for build in catalog}
    cohort_numbers = set(catalog_by_number)
    observations = _observations_by_build(reliability)
    processed = set(updated.get("processed_build_numbers") or [])
    active = dict(updated.get("active") or {})

    if not updated.get("initialized"):
        newest = max(catalog, key=_build_rank, default=None)
        to_process = [int(newest["number"])] if newest else []
        processed = set(cohort_numbers)
        updated["initialized"] = True
    else:
        to_process = sorted(
            cohort_numbers - processed,
            key=lambda number: _build_rank(catalog_by_number[number]),
        )
        processed |= cohort_numbers

    for number in to_process:
        for group_id, row in observations.get(number, {}).items():
            if row["result"] == "passed":
                active.pop(group_id, None)
            else:
                active[group_id] = row

    generated = _parse_ts(reliability.get("generated_at"))
    if generated is not None:
        cutoff = generated - INCIDENT_MAX_AGE
        active = {
            group_id: row
            for group_id, row in active.items()
            if (_parse_ts(row.get("observed_at")) or generated) >= cutoff
        }

    updated["active"] = active
    updated["processed_build_numbers"] = sorted(processed & cohort_numbers)
    return updated


def _fingerprint(active: dict[str, dict]) -> str:
    compact = [
        {
            "group_id": group_id,
            "result": row.get("result"),
            "build_number": row.get("build_number"),
            "job_id": row.get("job_id"),
        }
        for group_id, row in sorted(active.items())
    ]
    encoded = json.dumps(compact, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _md(value: Any) -> str:
    return str(value or "-").replace("|", "\\|").replace("\n", " ")


def _issue_title(active: dict[str, dict]) -> str:
    hard = sum(row.get("result") == "failed" for row in active.values())
    soft = sum(row.get("result") == "soft_fail" for row in active.values())
    return f"AMD main: {len(active)} unresolved test-group failures ({hard} hard, {soft} soft)"


def _issue_body(active: dict[str, dict], reliability: dict, run_url: str, owner: str) -> str:
    hard = sum(row.get("result") == "failed" for row in active.values())
    soft = sum(row.get("result") == "soft_fail" for row in active.values())
    generated_at = str(reliability.get("generated_at") or "unknown")
    rows = sorted(
        active.values(),
        key=lambda row: (
            row.get("result") != "failed",
            str(row.get("observed_at") or ""),
            str(row.get("name") or "").lower(),
        ),
    )
    lines = [
        "## AMD origin/main test-group alert",
        "",
        f"**{len(active)} strict AMD test groups are unresolved: {hard} hard, {soft} soft.**",
        "",
        "Signal rules:",
        "",
        "- Source: exhaustive completed amd-ci builds on branch=main with build state passed or failed.",
        "- Identity: exact test label + Buildkite step key + hardware + queue.",
        "- Retry handling: the latest eligible attempt for the same strict group inside a build wins.",
        "- Resolution: the same strict group passes, or its last incident receives no new failure for 72 hours.",
        "- Soft-failed jobs count because they represent a failing AMD test command even when Buildkite permits the build to continue.",
        "",
        f"Collected at **{generated_at}**. [Open AMD test health]({DASHBOARD_URL}).",
        "",
        "| test group | hardware / queue | result | latest build | observed |",
        "|---|---|---|---|---|",
    ]
    for row in rows[:MAX_ISSUE_ROWS]:
        job_url = row.get("job_url") or row.get("build_url") or ""
        group = _md(row.get("name"))
        group_md = f"[{group}]({job_url})" if job_url else group
        build_number = int(row.get("build_number") or 0)
        build_url = row.get("build_url") or job_url
        build_md = f"[#{build_number}]({build_url})" if build_url else f"#{build_number}"
        lines.append(
            f"| {group_md} | {_md(row.get('hardware'))} / {_md(row.get('queue'))} "
            f"| {_md(row.get('result'))} | {build_md} | {_md(row.get('observed_at'))} |"
        )
    if len(rows) > MAX_ISSUE_ROWS:
        lines.extend(["", f"{len(rows) - MAX_ISSUE_ROWS} additional groups are retained in watcher state."])
    lines.extend([
        "",
        f"GitHub assignee: {owner}.",
        "",
        f"*Managed by amd_main_failure_watcher.py from {run_url}. Only this tracked umbrella issue can be updated or closed by the watcher.*",
    ])
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

    state = advance_incidents(reliability, _read_state())
    active = state.get("active") or {}
    observed_at = str(reliability.get("generated_at") or now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    client = GitHubIssueClient(token, repo)
    reconciled = reconcile_managed_issue(
        state,
        active=bool(active),
        fingerprint=_fingerprint(active),
        title=_issue_title(active),
        body=_issue_body(active, reliability, run_url, repo_owner(repo)),
        ownership_marker=OWNERSHIP_MARKER,
        recovery_body=(
            "No strict AMD origin/main test-group incidents remain under the "
            "watcher's 72-hour live-signal rule. Closing this tracked umbrella issue.\n\n"
            f"*{run_url}*"
        ),
        observed_at=observed_at,
        label_specs=LABEL_SPECS,
        client=client,
    )
    _write_state(reconciled)
    log.info(
        "AMD main watcher evaluated %d unresolved strict groups; issue=%s suppressed=%s",
        len(active),
        (reconciled.get("issue") or {}).get("number"),
        reconciled.get("suppressed"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
