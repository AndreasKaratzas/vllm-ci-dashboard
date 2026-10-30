#!/usr/bin/env python3
"""Track unresolved MI GPU CI test-group failures on ``main``.

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
CONFIG = shared.WatcherConfig(
    pipeline=PIPELINE,
    state=STATE,
    track_commit_range=True,
    initialize_from_history=True,
    job_scope="amd_gpu",
)


_default_state = shared._default_state
_is_fresh = shared._is_fresh


def advance_incidents(reliability: dict, state: dict) -> dict:
    return shared.advance_incidents(
        shared.mi_scoped_reliability(reliability),
        shared.mi_scoped_state(state, reliability),
        track_commit_range=True,
        initialize_from_history=True,
    )


def run() -> int:
    return shared.run_watcher(CONFIG)


if __name__ == "__main__":
    raise SystemExit(run())
