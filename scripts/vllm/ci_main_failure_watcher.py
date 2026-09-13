#!/usr/bin/env python3
"""Alert on unresolved upstream CI test-group failures on ``main``.

The source is ``ci.all_main_reliability`` from ``analytics.json``. The shared
watcher retains a last-known-good and first-known-bad vLLM commit for every
strict group incident. Those bounds are persisted for later ancestry validation
and automated ``git bisect`` execution.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm import amd_main_failure_watcher as shared  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE = "ci"
STATE = ROOT / "data" / "vllm" / "ci" / "open_ci_main_failure_issues.json"
OWNERSHIP_MARKER = "<!-- vllm-ci-dashboard:managed-alert:ci-main-failure:v1 -->"
LABEL_SPECS = [
    ("ci-main-failure", "d73a49", "Unresolved upstream CI test-group failure on origin/main"),
    ("automated", "6f42c1", "Managed by dashboard automation"),
    ("workstream:dev", "1d76db", "AMD CI test-area development"),
]
DASHBOARD_URL = (
    "https://andreaskaratzas.github.io/vllm-ci-dashboard/"
    "?ops_analytics_view=groups&ops_analytics_pipeline=ci#ci-analytics"
)
CONFIG = shared.WatcherConfig(
    pipeline=PIPELINE,
    state=STATE,
    ownership_marker=OWNERSHIP_MARKER,
    label_specs=tuple(LABEL_SPECS),
    dashboard_url=DASHBOARD_URL,
    title_prefix="CI main",
    heading="Upstream CI origin/main test-group alert",
    scope_name="upstream CI",
    script_name="ci_main_failure_watcher.py",
    track_commit_range=True,
    initialize_from_history=True,
)


_default_state = shared._default_state
_is_fresh = shared._is_fresh


def advance_incidents(reliability: dict, state: dict) -> dict:
    return shared.advance_incidents(
        reliability,
        state,
        track_commit_range=True,
        initialize_from_history=True,
    )


def _issue_title(active: dict[str, dict]) -> str:
    return shared._issue_title_for(active, CONFIG)


def _issue_body(
    active: dict[str, dict],
    reliability: dict,
    run_url: str,
    owner: str,
) -> str:
    return shared._issue_body_for(active, reliability, run_url, owner, CONFIG)


def run() -> int:
    return shared.run_watcher(CONFIG)


if __name__ == "__main__":
    raise SystemExit(run())
