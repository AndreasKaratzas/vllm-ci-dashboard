#!/usr/bin/env python3
"""Open one state-owned issue for AMD CI nodes that are failing jobs themselves.

The collector attributes every failing attempt to the node, the build, the test,
shared infrastructure, or an unknown cause (collect_agent_health._attribute_failures).
The watcher counts exactly what the dashboard's default "Hurting" verdict counts:
failing attempts attributed to an identified node, in builds created on the
collector's UTC day or the two days before it (the dashboard's RECENT_DAYS),
excluding canceled builds, and alerts on nodes with at least two from at least
two builds: node failures confined to one build only put the node on watch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.ci.managed_issue import (  # noqa: E402
    DASHBOARD_REPO,
    GitHubIssueClient,
    normalize_managed_state,
    reconcile_managed_issue,
    repo_owner,
    validate_target_repo,
)
from vllm.ci.watcher_state import write_watcher_state  # noqa: E402
from vllm.constants import amd_gpu_hardware  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_HEALTH = ROOT / "data" / "vllm" / "ci" / "agent_health.json"
STATE = ROOT / "data" / "vllm" / "ci" / "open_agent_health_issues.json"

# web/src/domain/agentHealth.ts RECENT_DAYS: the collector's UTC day and the two before it.
RECENT_DAYS = 3
LOOKBACK = timedelta(days=RECENT_DAYS)
MAX_DATA_AGE = timedelta(hours=3)
MIN_NODE_ATTRIBUTED = 2
MIN_NODE_BUILDS = 2
MAX_ISSUE_EVENTS = 12
MAX_RUNS_PER_EVENT = 12
OWNERSHIP_MARKER = "<!-- vllm-ci-dashboard:managed-alert:agent-health:v1 -->"
LABEL_SPECS = [
    ("ci-agent-health", "b60205", "Failures attributed to an AMD CI node"),
    ("automated", "6f42c1", "Managed by dashboard automation"),
    ("workstream:infra", "0e8a16", "AMD CI infrastructure and capacity"),
]
DASHBOARD_BASE = "https://andreaskaratzas.github.io/vllm-ci-dashboard/"


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


def _read_payload() -> dict | None:
    if not AGENT_HEALTH.exists():
        return None
    try:
        payload = json.loads(AGENT_HEALTH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _default_state() -> dict:
    return {
        "schema_version": 1,
        "issue": None,
        "suppressed": False,
        "last_fingerprint": "",
        "last_run": "",
    }


def _read_state() -> dict:
    if not STATE.exists():
        return _default_state()
    try:
        return normalize_managed_state(json.loads(STATE.read_text()))
    except (OSError, json.JSONDecodeError):
        return _default_state()


def _write_state(state: dict) -> None:
    write_watcher_state(STATE, state, state_filename="open_agent_health_issues.json")


def _is_fresh(payload: dict, now: datetime) -> bool:
    generated = _parse_ts(payload.get("generated_at"))
    if generated is None:
        return False
    age = now - generated
    return -timedelta(minutes=15) <= age <= MAX_DATA_AGE


def _current_main_scope(payload: dict) -> bool:
    """Require the published ci/main cohort before touching incident state."""
    retention = payload.get("retention")
    scope = retention.get("pipeline_scope") if isinstance(retention, dict) else None
    if (payload.get("hardware_scope") != "amd_mi_gpu"
            or payload.get("job_scope") != "amd_gpu"
            or payload.get("pipelines") != ["ci"]
            or payload.get("branches") != ["main"]
            or not isinstance(scope, dict)):
        return False
    if any(
        not isinstance(scope.get(field), str)
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|\+00:00)", scope[field])
        for field in ("collected_from", "collected_to")
    ):
        return False
    start = _parse_ts(scope.get("collected_from"))
    end = _parse_ts(scope.get("collected_to"))
    days = scope.get("requested_days")
    original = retention.get("original_day_count")
    retained = retention.get("retained_day_count")
    dropped = retention.get("dropped_oldest_day_count")
    failing = payload.get("failing_runs")
    evidence = retention.get("failure_evidence")
    if not isinstance(failing, list) or not isinstance(evidence, dict):
        return False
    if (evidence.get("complete_relative_to_source") is not True
            or type(evidence.get("omitted")) is not int or evidence["omitted"] != 0
            or any(type(value) is not int or value != len(failing) for value in (
                evidence.get("source"), evidence.get("published"),
                payload.get("infra_failure_count"), payload.get("published_failure_evidence_count"),
            ))):
        return False
    return bool(
        type(scope.get("version")) is int and scope["version"] == 3
        and scope.get("branch") == "main"
        and scope.get("basis") == "terminal_jobs_by_build_created_at"
        and scope.get("day_basis") == "build_created_at_utc"
        and scope.get("eligible_completion") == "current_ci_build_creation_cohort_with_provable_completion"
        and "finished_job_source" not in scope and "active_build_states" not in scope
        and isinstance(scope.get("discovery_legs"), dict)
        and set(scope["discovery_legs"]) == {"created"}
        and scope["discovery_legs"]["created"] is True
        and scope.get("exhaustive") is True
        and scope.get("attempt_policy") == "every_executed_attempt"
        and scope.get("terminal_time_policy") == "finished_at_or_terminal_build_bound_for_canceled"
        and type(days) is int and 1 <= days <= 60
        and scope.get("collected_to") == payload.get("generated_at")
        and start is not None and end is not None and start <= end - LOOKBACK
        and start == (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        and type(original) is int and type(retained) is int and type(dropped) is int
        and original >= 0 and retained >= 0 and dropped == 0 and original == retained
        and retention.get("byte_limited") is False
        and type(retention.get("configured_days")) is int and retention["configured_days"] == 60
        and type(payload.get("max_window_days")) is int and payload["max_window_days"] == 60
        and scope.get("complete_window") is (start <= end - timedelta(days=60))
    )


def _job_url(run: dict) -> str:
    pipeline = str(run.get("p") or "")
    build = run.get("b")
    job_id = str(run.get("j") or "")
    if not pipeline or not isinstance(build, int) or isinstance(build, bool) or build <= 0:
        return ""
    base = f"https://buildkite.com/vllm/{pipeline}/builds/{build}"
    return f"{base}/steps/canvas?jid={job_id}&tab=output" if job_id else base


def _window_runs(payload: dict) -> list[dict]:
    """Every scoped failing attempt of the live window, whatever its attribution."""
    end = _parse_ts(payload.get("generated_at"))
    if end is None:
        return []
    first_day = (end - timedelta(days=RECENT_DAYS - 1)).strftime("%Y-%m-%d")
    runs = []
    for raw in payload.get("failing_runs") or []:
        if not isinstance(raw, dict):
            continue
        node = str(raw.get("nd") or "")
        started = _parse_ts(raw.get("t") or raw.get("e"))
        finished = _parse_ts(raw.get("e")) or started
        state = str(raw.get("s") or "")
        if (
            raw.get("p") != "ci"
            or not amd_gpu_hardware(str(raw.get("q") or ""))
            or raw.get("bc") == 1
            or not node
            or node == "(unidentified)"
            or state not in {"hard", "soft"}
            or started is None
            or not isinstance(raw.get("d"), str)
            or raw["d"] < first_day
            or started > end + timedelta(minutes=15)
        ):
            continue
        runs.append({
            "node": node,
            "hardware": str(raw.get("h") or "unknown"),
            "pipeline": str(raw.get("p") or ""),
            "queue": str(raw.get("q") or ""),
            "group": str(raw.get("g") or "unknown"),
            "state": state,
            "build_number": raw.get("b"),
            "job_id": str(raw.get("j") or ""),
            "suspect": raw.get("i") == 1,
            "attribution": str(raw.get("a") or "unknown"),
            "reason": str(raw.get("ar") or "isolated"),
            "retried": raw.get("rt") == 1,
            "started_at": started,
            "finished_at": finished or started,
            "url": _job_url(raw),
        })
    return runs


def _node_event(node: str, runs: list[dict]) -> dict | None:
    # Every attempt counts, as on the dashboard: a node fault usually fails one
    # attempt and Buildkite retries it, so the retry chain is not one failure.
    runs = sorted(runs, key=lambda row: row["started_at"])
    attributed = [run for run in runs if run["attribution"] == "node"]
    builds = {(run["pipeline"], run["build_number"]) for run in attributed}
    if len(attributed) < MIN_NODE_ATTRIBUTED or len(builds) < MIN_NODE_BUILDS:
        return None
    hardware = next((run["hardware"] for run in runs if run["hardware"]), "unknown")
    pipelines = sorted({run["pipeline"] for run in runs if run["pipeline"]})
    return {
        "node": node,
        "hardware": hardware,
        "started_at": min(run["started_at"] for run in runs),
        "finished_at": max(run["finished_at"] for run in runs),
        "failure_count": len(runs),
        "node_attributed": len(attributed),
        "group_count": len({run["group"] for run in runs}),
        "hard": sum(run["state"] == "hard" for run in runs),
        "soft": sum(run["state"] == "soft" for run in runs),
        "pipelines": pipelines,
        "runs": runs,
    }


def find_alert_events(payload: dict) -> list[dict]:
    """One event per node with node-attributed failures, its unexplained ones alongside."""
    by_node: dict[str, list[dict]] = defaultdict(list)
    for run in _window_runs(payload):
        if run["suspect"]:
            by_node[run["node"]].append(run)
    events = [event for node, runs in by_node.items() if (event := _node_event(node, runs))]
    events.sort(
        key=lambda event: (
            event["node_attributed"],
            event["hard"],
            event["failure_count"],
            event["group_count"],
            event["finished_at"],
        ),
        reverse=True,
    )
    return events


def _fingerprint(events: list[dict]) -> str:
    compact = []
    for event in events:
        compact.append({
            "node": event["node"],
            "runs": [
                [
                    run["pipeline"],
                    run["build_number"],
                    run["group"],
                    run["job_id"],
                    run["state"],
                ]
                for run in event["runs"]
            ],
        })
    encoded = json.dumps(compact, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _content_fingerprint(events: list[dict], payload: dict, owner: str) -> str:
    """Hash the body without its collection time and run link, which change every run."""
    body = _issue_body(events, payload, "", owner)
    stable = [line for line in body.splitlines() if not line.startswith("Collected at ")]
    return hashlib.sha256("\n".join(stable).encode()).hexdigest()


def _dashboard_url(node: str = "") -> str:
    params = {
        "ops_analytics_view": "agent-health",
        "ops_agent_window": "3d",
        "ops_agent_signal": "infra",
        "ops_agent_excl_cancel": "1",
        "ops_agent_cofail": "180",
    }
    if node:
        params["ops_agent_node"] = node
    return f"{DASHBOARD_BASE}?{urlencode(params)}#ci-analytics"


def _md(value: Any) -> str:
    return str(value or "-").replace("|", "\\|").replace("\n", " ")


ISSUE_TITLE = "AMD CI agent health: nodes failing jobs themselves"


REASON_TEXT = {
    "agent_lost": "agent lost mid-job",
    "node_repeats": "different groups fail only on this node",
    "job_never_started": "containers never started, on this node alone",
    "isolated": "unexplained one-off",
}


def _attribution_summary(payload: dict) -> str:
    """How the window's other failures were explained, so the alert reads in context."""
    counts: dict[str, int] = defaultdict(int)
    for run in _window_runs(payload):
        counts[run["attribution"]] += 1
    total = sum(counts.values())
    return (
        f"Of {total} failing attempts in the window, {counts['node']} are attributed to a node, "
        f"{counts['build']} to build-wide breakage, {counts['test']} to their test group (it also fails "
        f"on other nodes or on CUDA, or passes nowhere else), {counts['infra']} to shared infrastructure or a passing outage, and "
        f"{counts['unknown']} are unexplained one-offs."
    )


def _issue_body(events: list[dict], payload: dict, run_url: str, owner: str) -> str:
    nodes = {event["node"] for event in events}
    failure_count = sum(event["failure_count"] for event in events)
    generated_at = str(payload.get("generated_at") or "unknown")
    lines = [
        "## AMD CI agent-health alert",
        "",
        f"**{len(nodes)} physical nodes have {failure_count} node-suspect failing attempts.**",
        "",
        _attribution_summary(payload),
        "",
        "Signal rules:",
        "",
        "- Live window: builds created on the collector's UTC day and the two days before it, as on the dashboard.",
        "- Attribution: a failure is explained elsewhere when it is build-wide (30% or more of both the build's CUDA jobs and its AMD jobs failed, outside torch-nightly builds, and the step never passed; or the build failed on four or more other nodes, shared infrastructure instead when the step passed on a retry), shared infrastructure (agents lost, or containers that never started, on three or more nodes within 30 minutes), or the test's (its retry failed on another node, the CUDA job running the same test failed on the same commit, outside torch-nightly builds, while the AMD job never passed, or its test group failed on another node within a day, or the group passed on no other node). A node that failed nearly every job it ran in a build while the jobs beside it passed is never explained by the build or by its test groups.",
        "- Node evidence: the agent was lost mid-job on this node alone, or unexplained failures (including containers that never started on this node alone) hit at least two test groups on the node within three days.",
        "- Scope: identified AMD MI GPU nodes from every terminal ci/main attempt, including attempts superseded by a retry, in the published UTC build-creation cohort; canceled builds are excluded.",
        "- Retry handling: every attempt counts, because a node fault usually fails one attempt and passes on the retry elsewhere.",
        "- Alert threshold: at least two failing attempts attributed to the node, from at least two builds (failures confined to one build only put the node on watch); its unexplained failures are listed alongside.",
        "",
        f"Collected at **{generated_at}**. [Open CI agent health]({_dashboard_url()}).",
        "",
        "| node | hardware | window | node-attributed / failures / groups | hard / soft | pipelines |",
        "|---|---|---|---:|---:|---|",
    ]
    for event in events[:MAX_ISSUE_EVENTS]:
        node_url = _dashboard_url(event["node"])
        window = (
            f"{event['started_at'].strftime('%m-%d %H:%M')} - "
            f"{event['finished_at'].strftime('%m-%d %H:%M')} UTC"
        )
        lines.append(
            f"| [{_md(event['node'])}]({node_url}) | {_md(event['hardware'])} "
            f"| {window} | {event['node_attributed']} / {event['failure_count']} / {event['group_count']} "
            f"| {event['hard']} / {event['soft']} | {_md(', '.join(event['pipelines']))} |"
        )

    for event in events[:MAX_ISSUE_EVENTS]:
        lines.extend([
            "",
            f"### {_md(event['node'])} ({_md(event['hardware'])})",
            "",
        ])
        for run in event["runs"][:MAX_RUNS_PER_EVENT]:
            build = run.get("build_number") or "?"
            label = f"{run['pipeline']} #{build}: {_md(run['group'])}"
            evidence = f"[{label}]({run['url']})" if run.get("url") else label
            cause = REASON_TEXT.get(run["reason"], run["reason"])
            retry = ", superseded by a retry" if run["retried"] else ""
            lines.append(
                f"- {evidence} - {run['state']}, {run['attribution']} ({cause}{retry}), "
                f"{_md(run['queue'])}, {run['started_at'].strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
        if len(event["runs"]) > MAX_RUNS_PER_EVENT:
            lines.append(f"- {len(event['runs']) - MAX_RUNS_PER_EVENT} additional failing attempts retained in state")

    if len(events) > MAX_ISSUE_EVENTS:
        lines.extend(["", f"{len(events) - MAX_ISSUE_EVENTS} additional nodes are retained in watcher state."])
    lines.extend([
        "",
        f"GitHub assignee: {owner}.",
        "",
        f"*Managed by agent_health_issue_watcher.py from {run_url}. Only this tracked umbrella issue can be updated or closed by the watcher.*",
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

    payload = _read_payload()
    if not payload:
        log.error("Agent-health payload is unavailable; refusing issue mutations")
        return 0
    if not _current_main_scope(payload):
        log.error("Agent-health payload has no proved current MI-only ci/main cohort; refusing issue mutations")
        return 0
    now = datetime.now(timezone.utc)
    if not _is_fresh(payload, now):
        log.error("Agent-health payload is stale or future-dated; refusing issue mutations")
        return 0
    if not token:
        log.warning("GITHUB_TOKEN not set; leaving issue state untouched")
        return 0

    events = find_alert_events(payload)
    observed_at = str(payload.get("generated_at") or now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    owner = repo_owner(repo)
    client = GitHubIssueClient(token, repo)
    reconciled = reconcile_managed_issue(
        _read_state(),
        active=bool(events),
        fingerprint=_fingerprint(events),
        content_fingerprint=_content_fingerprint(events, payload, owner),
        title=ISSUE_TITLE,
        body=_issue_body(events, payload, run_url, owner),
        ownership_marker=OWNERSHIP_MARKER,
        recovery_body=(
            "No identified AMD node currently has two failures from two builds attributed "
            "to it in the three-day window. Closing this tracked umbrella issue.\n\n"
            f"*{run_url}*"
        ),
        observed_at=observed_at,
        label_specs=LABEL_SPECS,
        client=client,
        discovery_label="ci-agent-health",
        recovery_labels=("automated", "workstream:infra"),
    )
    _write_state(reconciled)
    log.info(
        "Agent-health watcher found %d nodes failing jobs themselves; issue=%s suppressed=%s",
        len(events),
        (reconciled.get("issue") or {}).get("number"),
        reconciled.get("suppressed"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
