#!/usr/bin/env python3
"""Open one state-owned issue for concentrated AMD CI agent-health incidents.

The watcher reuses the CI Analytics infra-suspect signal: a failing group that
mostly passes that day and passes on another physical node. It excludes canceled
builds and unidentified nodes, then applies the dashboard's three-hour
co-failure clustering over a six-hour live window. An alert needs at least three
logical failures across at least two distinct test groups on one node.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
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


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_HEALTH = ROOT / "data" / "vllm" / "ci" / "agent_health.json"
STATE = ROOT / "data" / "vllm" / "ci" / "open_agent_health_issues.json"

LOOKBACK = timedelta(hours=6)
COFAILURE_WINDOW = timedelta(hours=3)
MAX_DATA_AGE = timedelta(hours=3)
MIN_LOGICAL_FAILURES = 3
MIN_DISTINCT_GROUPS = 2
MAX_ISSUE_EVENTS = 12
MAX_RUNS_PER_EVENT = 12
OWNERSHIP_MARKER = "<!-- vllm-ci-dashboard:managed-alert:agent-health:v1 -->"
LABEL_SPECS = [
    ("ci-agent-health", "b60205", "Concentrated infra-suspect failures on an AMD CI node"),
    ("automated", "6f42c1", "Managed by dashboard automation"),
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
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def _is_fresh(payload: dict, now: datetime) -> bool:
    generated = _parse_ts(payload.get("generated_at"))
    if generated is None:
        return False
    age = now - generated
    return -timedelta(minutes=15) <= age <= MAX_DATA_AGE


def _job_url(run: dict) -> str:
    pipeline = str(run.get("p") or "")
    build = run.get("b")
    job_id = str(run.get("j") or "")
    if not pipeline or not isinstance(build, int) or isinstance(build, bool) or build <= 0:
        return ""
    base = f"https://buildkite.com/vllm/{pipeline}/builds/{build}"
    return f"{base}/steps/canvas?jid={job_id}&tab=output" if job_id else base


def _normalized_runs(payload: dict) -> list[dict]:
    end = _parse_ts(payload.get("generated_at"))
    if end is None:
        return []
    start = end - LOOKBACK
    runs = []
    for raw in payload.get("failing_runs") or []:
        if not isinstance(raw, dict):
            continue
        node = str(raw.get("nd") or "")
        started = _parse_ts(raw.get("t") or raw.get("e"))
        finished = _parse_ts(raw.get("e")) or started
        state = str(raw.get("s") or "")
        if (
            raw.get("i") != 1
            or raw.get("bc") == 1
            or not node
            or node == "(unidentified)"
            or state not in {"hard", "soft"}
            or started is None
            or started < start
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
            "started_at": started,
            "finished_at": finished or started,
            "url": _job_url(raw),
        })
    return runs


def _event_from_cluster(node: str, cluster: list[dict]) -> dict | None:
    # Match the UI: retries of one pipeline/build/group are one logical failure,
    # and the latest attempt in the cluster is retained.
    logical: dict[tuple[str, Any, str], dict] = {}
    for run in cluster:
        key = (run["pipeline"], run["build_number"], run["group"])
        current = logical.get(key)
        if current is None or run["started_at"] > current["started_at"]:
            logical[key] = run
    runs = sorted(logical.values(), key=lambda row: row["started_at"])
    groups = {run["group"] for run in runs}
    if len(runs) < MIN_LOGICAL_FAILURES or len(groups) < MIN_DISTINCT_GROUPS:
        return None
    starts = [run["started_at"] for run in runs]
    ends = [run["finished_at"] for run in runs]
    hardware = next((run["hardware"] for run in runs if run["hardware"]), "unknown")
    pipelines = sorted({run["pipeline"] for run in runs if run["pipeline"]})
    return {
        "node": node,
        "hardware": hardware,
        "started_at": min(starts),
        "finished_at": max(ends),
        "failure_count": len(runs),
        "group_count": len(groups),
        "hard": sum(run["state"] == "hard" for run in runs),
        "soft": sum(run["state"] == "soft" for run in runs),
        "pipelines": pipelines,
        "cross_pipeline": len(pipelines) > 1,
        "runs": runs,
    }


def find_alert_events(payload: dict) -> list[dict]:
    by_node: dict[str, list[dict]] = defaultdict(list)
    for run in _normalized_runs(payload):
        by_node[run["node"]].append(run)

    events = []
    for node, runs in by_node.items():
        runs.sort(key=lambda row: row["started_at"])
        cluster: list[dict] = []
        cluster_end: datetime | None = None
        for run in runs:
            if cluster_end is not None and run["started_at"] - cluster_end > COFAILURE_WINDOW:
                event = _event_from_cluster(node, cluster)
                if event:
                    events.append(event)
                cluster = []
                cluster_end = None
            cluster.append(run)
            cluster_end = max(cluster_end, run["finished_at"]) if cluster_end else run["finished_at"]
        event = _event_from_cluster(node, cluster)
        if event:
            events.append(event)

    events.sort(
        key=lambda event: (
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


def _dashboard_url(node: str = "") -> str:
    params = {
        "ops_analytics_view": "agent-health",
        "ops_agent_window": "1d",
        "ops_agent_signal": "infra",
        "ops_agent_excl_cancel": "1",
        "ops_agent_cofail": "180",
    }
    if node:
        params["ops_agent_node"] = node
    return f"{DASHBOARD_BASE}?{urlencode(params)}#ci-analytics"


def _md(value: Any) -> str:
    return str(value or "-").replace("|", "\\|").replace("\n", " ")


def _issue_title(events: list[dict]) -> str:
    nodes = {event["node"] for event in events}
    return (
        f"AMD CI agent health: {len(nodes)} nodes with concentrated "
        f"infra-suspect failures"
    )


def _issue_body(events: list[dict], payload: dict, run_url: str, owner: str) -> str:
    nodes = {event["node"] for event in events}
    failure_count = sum(event["failure_count"] for event in events)
    generated_at = str(payload.get("generated_at") or "unknown")
    lines = [
        "## AMD CI agent-health alert",
        "",
        f"**{len(nodes)} physical nodes have {len(events)} qualifying events containing {failure_count} logical failures.**",
        "",
        "Signal rules:",
        "",
        "- Live window: six hours ending at the collector timestamp.",
        "- Signal: infra-suspect failures from CI Agent Health only.",
        "- Scope: identified AMD GPU nodes across amd-ci and upstream CI; canceled builds are excluded.",
        "- Clustering: consecutive failures on one node with gaps no larger than three hours.",
        "- Retry handling: one pipeline/build/test-group chain counts once.",
        "- Alert threshold: at least three logical failures across at least two distinct groups in a cluster.",
        "",
        f"Collected at **{generated_at}**. [Open CI agent health]({_dashboard_url()}).",
        "",
        "| node | hardware | window | failures / groups | hard / soft | pipelines |",
        "|---|---|---|---:|---:|---|",
    ]
    for event in events[:MAX_ISSUE_EVENTS]:
        node_url = _dashboard_url(event["node"])
        window = (
            f"{event['started_at'].strftime('%H:%M')} - "
            f"{event['finished_at'].strftime('%H:%M')} UTC"
        )
        lines.append(
            f"| [{_md(event['node'])}]({node_url}) | {_md(event['hardware'])} "
            f"| {window} | {event['failure_count']} / {event['group_count']} "
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
            lines.append(
                f"- {evidence} - {run['state']}, {_md(run['queue'])}, "
                f"{run['started_at'].strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
        if len(event["runs"]) > MAX_RUNS_PER_EVENT:
            lines.append(f"- {len(event['runs']) - MAX_RUNS_PER_EVENT} additional logical failures retained in state")

    if len(events) > MAX_ISSUE_EVENTS:
        lines.extend(["", f"{len(events) - MAX_ISSUE_EVENTS} additional qualifying events are retained in watcher state."])
    lines.extend([
        "",
        f"cc @{owner}",
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
    now = datetime.now(timezone.utc)
    if not _is_fresh(payload, now):
        log.error("Agent-health payload is stale or future-dated; refusing issue mutations")
        return 0
    if not token:
        log.warning("GITHUB_TOKEN not set; leaving issue state untouched")
        return 0

    events = find_alert_events(payload)
    observed_at = str(payload.get("generated_at") or now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    client = GitHubIssueClient(token, repo)
    reconciled = reconcile_managed_issue(
        _read_state(),
        active=bool(events),
        fingerprint=_fingerprint(events),
        title=_issue_title(events),
        body=_issue_body(events, payload, run_url, repo_owner(repo)),
        ownership_marker=OWNERSHIP_MARKER,
        recovery_body=(
            "No identified AMD node currently meets the six-hour concentrated "
            "infra-suspect failure rule. Closing this tracked umbrella issue.\n\n"
            f"*{run_url}*"
        ),
        observed_at=observed_at,
        label_specs=LABEL_SPECS,
        client=client,
    )
    _write_state(reconciled)
    log.info(
        "Agent-health watcher evaluated %d qualifying events across %d nodes; issue=%s suppressed=%s",
        len(events),
        len({event["node"] for event in events}),
        (reconciled.get("issue") or {}).get("number"),
        reconciled.get("suppressed"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
