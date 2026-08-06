"""Project-wide constants for the vLLM CI dashboard.

Everything that used to be copy-pasted across ``collect_queue_snapshot.py``,
``collect_hotness.py``, ``collect_analytics.py``, ``queue_issue_watcher.py``
and friends lives here. Import from this module; do not redefine.

Kept importable without side effects — no I/O, no env reads beyond what's
already in ``scripts/vllm/ci/config`` (which owns framework-level retry /
state-machine constants).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Buildkite org + pipelines
# ---------------------------------------------------------------------------

BK_ORG = "vllm"
BK_CLUSTER_UUID = "9cecc6b1-94cd-43d1-a256-ab438083f4f5"

# Pipelines that land on AMD hardware. Used by hotness/analytics collectors
# to scope which builds to walk.
AMD_PIPELINES: tuple[str, ...] = ("amd-ci",)

# ---------------------------------------------------------------------------
# Queue taxonomy
# ---------------------------------------------------------------------------

# Queues in these families are outside the dashboard's operational scope.
# Keep the predicate central because
# Buildkite queue keys are case-sensitive strings while operators have used
# both ``mi355B`` and ``mi355b`` spellings, with several numeric suffixes.
EXCLUDED_QUEUE_PREFIXES: tuple[str, ...] = ("amd_mi355b",)


def is_excluded_queue(queue: str | None) -> bool:
    """Return whether ``queue`` belongs to an excluded queue family."""
    normalized = (queue or "").strip().casefold()
    return any(normalized.startswith(prefix) for prefix in EXCLUDED_QUEUE_PREFIXES)


def is_amd_queue(queue: str | None) -> bool:
    """Return whether ``queue`` belongs to the active AMD queue scope."""
    normalized = (queue or "").strip().casefold()
    return (normalized.startswith("amd_") or normalized == "amd-cpu") and not is_excluded_queue(
        normalized
    )


# ``amd_mi300_4`` -> "MI300". Physical-agent (node) health tracks AMD *GPU*
# nodes, so this deliberately excludes ``amd-cpu`` (returns "") — CPU bootstrap
# / docker steps are not GPU-box runs and would only add noise/volume.
_AMD_GPU_QUEUE_RE = re.compile(r"^amd_mi(\d{3,4})b?(?:_|$)", re.IGNORECASE)


def amd_gpu_hardware(queue: str | None) -> str:
    """Return the GPU model (``MI300``) for an AMD GPU queue, else "".

    Returns "" for non-AMD queues, ``amd-cpu``, and excluded queues. Use this
    to scope agent-health to physical GPU nodes.
    """
    normalized = (queue or "").strip()
    if is_excluded_queue(normalized):
        return ""
    match = _AMD_GPU_QUEUE_RE.match(normalized)
    return "MI" + match.group(1) if match else ""


# Every tracked queue is emitted in every snapshot — zero-filled if no jobs
# referenced it during the poll — so the queue timeseries chart never shows
# gaps for queues we actively monitor. Untracked queues that show up with
# activity are still recorded on the fly by the collector.
_TRACKED_QUEUE_NAMES = {
    # AMD MI250
    "amd_mi250_1",
    "amd_mi250_2",
    "amd_mi250_4",
    "amd_mi250_8",
    # AMD MI300 (legacy / partner agents, still active)
    "amd_mi300_1",
    "amd_mi300_2",
    "amd_mi300_4",
    "amd_mi300_8",
    # AMD MI325
    "amd_mi325_1",
    "amd_mi325_2",
    "amd_mi325_4",
    "amd_mi325_8",
    # AMD MI355
    "amd_mi355_1",
    "amd_mi355_2",
    "amd_mi355_4",
    "amd_mi355_8",
    # NVIDIA
    "gpu_1_queue",
    "gpu_4_queue",
    "B200",
    "H200",
    "a100_queue",
    "mithril-h100-pool",
    "nebius-h200",
    # CPU
    "cpu_queue_postmerge",
    "cpu_queue_premerge",
    "cpu_queue_postmerge_us_east_1",
    "cpu_queue_premerge_us_east_1",
    # Other hardware partners
    "intel-gpu",
    "intel-hpu",
    "intel-cpu",
    "arm-cpu",
    "ascend",
    # vLLM-Omni workload identifiers (same BK org / pipelines; separate queues)
    "intel-gpu-omni",
}

TRACKED_QUEUES: frozenset[str] = frozenset(
    queue for queue in _TRACKED_QUEUE_NAMES if not is_excluded_queue(queue)
)

AMD_QUEUE_PREFIX = "amd_"

# ---------------------------------------------------------------------------
# Workload classification (vllm vs vllm-omni)
# ---------------------------------------------------------------------------

# Dedicated Omni queues end with this suffix. When set, queue-level
# classification wins over branch-level classification.
OMNI_QUEUE_SUFFIX = "-omni"

# Branch / ref substrings that flag an Omni workload for builds that land
# on a shared queue. Treated case-insensitively.
OMNI_BRANCH_MARKERS: tuple[str, ...] = ("omni",)

# ---------------------------------------------------------------------------
# Queue-snapshot thresholds
# ---------------------------------------------------------------------------

# Queue jobs waiting or running longer than this are treated as zombie / stale
# and excluded from queue analytics. They are still surfaced separately for
# operator triage and issue alerting.
QUEUE_ZOMBIE_THRESHOLD_MIN = 240  # 4 hours

# Queue snapshots collected before this timestamp came from older logic that
# mixed current wait and historical pre-start wait. Treat them as invalid and
# prune them from history on every collector run.
QUEUE_HISTORY_RESET_TS = "2026-04-20T23:40:00Z"

# Keep the published queue history to the same one-month window offered by
# the dashboard controls.
QUEUE_HISTORY_RETENTION_DAYS = 30

# Frequent queue polling is retained at full resolution for incident response.
# Older observations retain one actual snapshot per UTC hour plus every
# queue's p50/p95/p99 peak value and exact observation time for that hour.
QUEUE_HISTORY_HIGH_RES_HOURS = 48
QUEUE_HISTORY_ARCHIVE_BUCKET_MINUTES = 60

# ---------------------------------------------------------------------------
# queue_issue_watcher thresholds
# ---------------------------------------------------------------------------

# p90 wait minute trigger / release points for the auto-issue watcher. The
# healthy point is strictly below the trigger so the watcher has hysteresis
# — a queue flapping around the threshold does not churn open/close events.
QUEUE_P90_TRIGGER_MIN = 30.0
QUEUE_P90_HEALTHY_MIN = 15.0

# Minimum waiting jobs before we open an issue — avoids alerting when a
# single hot job pushed p90 up in a sparsely-populated queue.
QUEUE_MIN_WAITING_SAMPLES = 3

# Queue latency alert issues are short-lived operational breadcrumbs, not a
# long-running incident tracker. Once a queue-latency issue has remained open
# for this many minutes, auto-close it and suppress re-opening until the queue
# first returns to healthy so stale alerts do not pile up.
QUEUE_ISSUE_MAX_AGE_MIN = 24 * 60

# ---------------------------------------------------------------------------
# Hotness window
# ---------------------------------------------------------------------------

HOTNESS_WINDOW_HOURS = 72

# Multiple windows we pre-compute so the dashboard can switch between them
# without re-fetching. Include the default so ``windows[f"{HOTNESS_WINDOW_HOURS}h"]``
# is always present.
HOTNESS_WINDOWS_HOURS: tuple[int, ...] = (1, 3, 24, 72)

# ---------------------------------------------------------------------------
# Omni surge detection
# ---------------------------------------------------------------------------

# Floor trigger — even a small omni test suite should alert if this many
# scheduled jobs pile up. Will be raised dynamically by the watcher based on
# the omni test YAMLs' group count. Healthy threshold derives as 70% of the
# active trigger to give hysteresis.
OMNI_SURGE_FLOOR_TRIGGER = 30
OMNI_SURGE_MULTIPLIER = 1.3  # dynamic trigger = ceil(multiplier * total_groups)
OMNI_SURGE_HEALTHY_RATIO = 0.7  # healthy threshold = floor(trigger * ratio)

OMNI_REPO = "vllm-project/vllm-omni"
OMNI_YAML_PATHS = (
    ".buildkite/amd/test-amd-ready.yml",
    ".buildkite/amd/test-amd-merge.yml",
)

# ---------------------------------------------------------------------------
# Derived / convenience values
# ---------------------------------------------------------------------------

BK_API_BASE = "https://api.buildkite.com/v2"
BK_GRAPHQL_URL = "https://graphql.buildkite.com/v1"


def queue_history_reset_datetime() -> datetime:
    """Return the UTC datetime that marks the start of trustworthy queue history."""
    return datetime.fromisoformat(QUEUE_HISTORY_RESET_TS.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )
