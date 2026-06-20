"""Generic CI analysis constants and thresholds.

Project-specific pipeline definitions should go in scripts/<project>/pipelines.py.
This module is for framework-level configuration only.
"""

import os

# Buildkite API
BK_API_BASE = "https://api.buildkite.com/v2"
BK_TOKEN = os.getenv("BUILDKITE_TOKEN", "")

# These are set by the project-specific entry point
BK_ORG = ""
PIPELINES = {}

# Job state classification
WAITING_STATES = frozenset({"scheduled", "limited", "waiting", "assigned"})
RUNNING_STATES = frozenset({"running", "canceling"})
TERMINAL_STATES = frozenset({"passed", "failed", "timed_out", "canceled", "broken", "blocked"})
FAILURE_STATES = frozenset({"failed", "timed_out", "broken"})

# Analysis thresholds
FLAKY_WINDOW = 10         # number of builds to consider for flaky detection
FLAKY_MIN_RATE = 0.20     # below this = "failing"
FLAKY_MAX_RATE = 0.80     # above this = "passing"
NEW_FAILURE_WINDOW = 3    # builds to look back for new_failure detection
HISTORY_DAYS = 30         # detailed JSONL retention
TREND_DAYS = 90           # aggregated trend retention

# API retry settings
MAX_RETRIES = 5
RETRY_BACKOFF = 10        # seconds
RETRY_CODES = {502, 503, 504, 520, 522, 524}

# Cache
STALE_WINDOW_HOURS = 6


def configure(org: str, pipelines: dict):
    """Set project-specific configuration at runtime.

    Called by the project entry point (e.g., scripts/collect_ci.py) to
    configure which Buildkite org and pipelines to monitor.
    """
    global BK_ORG, PIPELINES
    BK_ORG = org
    PIPELINES = pipelines
