#!/usr/bin/env python3
"""Maintain one managed issue for flaky AMD MI test groups on ``main``.

The source is ``ci.all_main_reliability`` from ``analytics.json``, the same
exhaustive completed branch=main cohort as the main-failure watcher. A group is
listed while, inside the configured window, its final per-build outcome flips
between pass and failure at least ``min_outcome_flips`` times or a retry
rescues a hard-failed attempt in at least ``min_failed_then_passed`` builds
(``config/ci_flakiness_thresholds.json``). The issue closes once no group
crosses either threshold.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm import amd_main_failure_watcher as main_watcher  # noqa: E402
from vllm.ci import main_flakiness  # noqa: E402
from vllm.ci_main_failure_watcher import DASHBOARD_URL  # noqa: E402
from vllm.ci.managed_issue import (  # noqa: E402
    DASHBOARD_REPO,
    GitHubIssueClient,
    normalize_managed_state,
    reconcile_managed_issue,
    validate_target_repo,
)
from vllm.ci.reliability_history import validate_all_main_reliability  # noqa: E402
from vllm.ci.watcher_state import write_watcher_state  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
STATE = ROOT / "data" / "vllm" / "ci" / "open_ci_flakiness_issues.json"
PIPELINE = "ci"
BODY_SCHEMA_VERSION = 1
OWNERSHIP_MARKER = "<!-- vllm-ci-dashboard:managed-alert:ci-flakiness:v1 -->"
LABEL_SPECS = [
    ("ci-flakiness", "d4a72c", "AMD MI test groups flipping or rescued by retries on origin/main"),
    ("automated", "6f42c1", "Managed by dashboard automation"),
    ("workstream:dev", "1d76db", "AMD CI test-area development"),
]


def _default_state() -> dict:
    return {
        "schema_version": 1,
        "hardware_scope": "amd_mi_gpu",
        "groups": {},
        "issue": None,
        "suppressed": False,
        "last_fingerprint": "",
        "last_content_fingerprint": "",
        "last_run": "",
    }


def _read_state(path: Path | None = None) -> dict:
    path = path or STATE
    if not path.exists():
        return _default_state()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return _default_state()
    state = normalize_managed_state(raw)
    state["schema_version"] = 1
    state["hardware_scope"] = "amd_mi_gpu"
    groups = raw.get("groups") if isinstance(raw, dict) else None
    state["groups"] = {
        str(group_id): dict(row)
        for group_id, row in (groups.items() if isinstance(groups, dict) else [])
        if isinstance(row, dict)
    }
    return state


def _write_state(state: dict, path: Path | None = None) -> None:
    write_watcher_state(path or STATE, state, state_filename="open_ci_flakiness_issues.json")


def _md(value: Any) -> str:
    return str(value or "-").replace("|", "\\|").replace("\n", " ")


def _fingerprint(rows: list[dict]) -> str:
    encoded = json.dumps(sorted(row["group_id"] for row in rows)).encode()
    return hashlib.sha256(encoded).hexdigest()


def _content_fingerprint(rows: list[dict]) -> str:
    encoded = json.dumps(
        {"body_schema_version": BODY_SCHEMA_VERSION, "rows": rows},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def tracked_groups(rows: list[dict], previous: dict, observed_at: str) -> dict:
    """Listed groups with the time each first crossed a threshold."""
    tracked = {}
    for row in rows:
        prior = previous.get(row["group_id"]) or {}
        current = row["current"]
        tracked[row["group_id"]] = {
            "name": row["name"],
            "hardware": row["hardware"],
            "queue": row["queue"],
            "listed_since": str(prior.get("listed_since") or observed_at),
            "flips": current["flips"],
            "recovered": current["recovered"],
            "runs": current["runs"],
            "trend": row["trend"],
        }
    return tracked


def _issue_title(rows: list[dict], thresholds: dict[str, int]) -> str:
    return (
        f"AMD MI main CI flakiness: {len(rows)} test groups unstable over "
        f"{thresholds['window_days']} days"
    )


def _issue_body(
    rows: list[dict],
    tracked: dict,
    reliability: dict,
    thresholds: dict[str, int],
    run_url: str,
) -> str:
    window = thresholds["window_days"]
    lines = [
        "## AMD MI GPU origin/main flakiness and retry alert",
        "",
        (
            f"**{len(rows)} strict AMD MI test groups crossed a flakiness threshold "
            f"in the last {window} days.**"
        ),
        "",
        "Signal rules:",
        "",
        (
            "- Source: exhaustive completed ci builds on branch=main with build state "
            "passed or failed, AMD MI GPU jobs only."
        ),
        "- Outcome: the final attempt of the strict group in each build (retries collapsed).",
        (
            f"- Flip: consecutive builds where the group ran and the final outcome changed "
            f"between pass and failure; listed at >= {thresholds['min_outcome_flips']} flips."
        ),
        (
            "- Retry recovery: a hard-failed attempt whose retry passed in the same build; "
            f"listed at >= {thresholds['min_failed_then_passed']} recovered builds."
        ),
        (
            f"- Trend compares against the preceding {window} days. A group leaves the list, "
            "and the issue closes once none remain, when both counts fall below threshold."
        ),
        "",
        (
            f"Collected at **{reliability.get('generated_at') or 'unknown'}**. "
            f"[Open main CI on the dashboard]({DASHBOARD_URL})."
        ),
        "",
        "| test group | hardware / queue | flips | recovered | runs | pass rate | trend | last failure | listed since |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    limit = thresholds["max_issue_rows"]
    for row in rows[:limit]:
        current, previous = row["current"], row["previous"]
        failure = current["last_failure"] or current["last_recovery"]
        failure_md = (
            f"[#{failure['build_number']}]({failure['url']})" if failure and failure["url"] else "-"
        )
        rate = "-" if current["pass_rate"] is None else f"{current['pass_rate']}%"
        lines.append(
            f"| {_md(row['name'])} | {_md(row['hardware'])} / {_md(row['queue'])} "
            f"| {current['flips']} (was {previous['flips']}) "
            f"| {current['recovered']} (was {previous['recovered']}) "
            f"| {current['runs']} | {rate} | {row['trend']} | {failure_md} "
            f"| {_md((tracked.get(row['group_id']) or {}).get('listed_since'))} |"
        )
    if len(rows) > limit:
        lines.extend(["", f"{len(rows) - limit} additional groups are retained in `{STATE.name}`."])
    lines.extend(
        [
            "",
            (
                f"*Managed by ci_flakiness_watcher.py from {run_url}. Only this tracked "
                "umbrella issue can be updated or closed by the watcher.*"
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def run(now: datetime | None = None) -> int:
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY") or DASHBOARD_REPO
    validate_target_repo(repo)
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_url = (
        f"https://github.com/{repo}/actions/runs/{run_id}"
        if run_id
        else f"https://github.com/{repo}"
    )
    reliability = main_watcher._read_reliability(PIPELINE)
    if not reliability or not validate_all_main_reliability(reliability, PIPELINE):
        log.error("Strict exhaustive ci main reliability is unavailable; refusing issue mutations")
        return 0
    now = now or datetime.now(timezone.utc)
    if not main_watcher._is_fresh(reliability, now):
        log.error("ci main reliability is stale or future-dated; refusing issue mutations")
        return 0
    if reliability.get("hardware_scope") != "amd_mi_gpu":
        log.error("MI-only reliability is unavailable; refusing issue mutations")
        return 0
    if not main_flakiness.has_outcome_history(reliability):
        log.error("ci main reliability predates per-build outcome history; refusing issue mutations")
        return 0
    if not token:
        log.warning("GITHUB_TOKEN not set; leaving issue state untouched")
        return 0

    reliability = main_watcher.mi_scoped_reliability(reliability)
    thresholds = main_flakiness.load_thresholds()
    rows = main_flakiness.alerting_rows(reliability, now, thresholds)
    observed_at = str(reliability.get("generated_at") or now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    state = _read_state()
    tracked = tracked_groups(rows, state.get("groups") or {}, observed_at)
    reconciled = reconcile_managed_issue(
        state,
        active=bool(rows),
        fingerprint=_fingerprint(rows),
        content_fingerprint=_content_fingerprint(rows),
        title=_issue_title(rows, thresholds),
        body=_issue_body(rows, tracked, reliability, thresholds, run_url),
        ownership_marker=OWNERSHIP_MARKER,
        recovery_body=(
            "No strict AMD MI GPU test group crosses the configured flip or retry-recovery "
            f"thresholds any more. Closing this tracked umbrella issue.\n\n*{run_url}*"
        ),
        observed_at=observed_at,
        label_specs=LABEL_SPECS,
        client=GitHubIssueClient(token, repo),
        discovery_label=LABEL_SPECS[0][0],
        recovery_labels=("automated", "workstream:dev"),
    )
    reconciled["schema_version"] = 1
    reconciled["hardware_scope"] = "amd_mi_gpu"
    reconciled["groups"] = tracked
    _write_state(reconciled)
    log.info(
        "ci flakiness watcher listed %d groups; issue=%s suppressed=%s",
        len(rows),
        (reconciled.get("issue") or {}).get("number"),
        reconciled.get("suppressed"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
