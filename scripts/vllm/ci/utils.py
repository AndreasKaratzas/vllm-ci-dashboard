"""Shared helpers for the vLLM CI collectors.

The helpers here are the small primitives that used to live as private
``_foo`` functions inside each collector (``collect_queue_snapshot``,
``collect_hotness``, ``collect_analytics``, ``queue_issue_watcher``).
Centralizing them means one place to fix bugs — and one place to cover
with tests.

This module has no runtime side effects and must stay framework-agnostic
(no Buildkite client imports, no HTTP). It consumes plain dicts / lists
that the collectors hand off from parsed Buildkite JSON.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Sequence

from ..constants import AMD_QUEUE_PREFIX, OMNI_BRANCH_MARKERS, OMNI_QUEUE_SUFFIX

# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

def parse_iso(s: str | None) -> datetime | None:
    """Parse a Buildkite ISO8601 timestamp, tolerating the trailing ``Z``.

    Returns ``None`` on any parse failure — collectors always want the
    "missing timestamp" fallback rather than an exception.
    """
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def duration_mins(start: str | None, end: str | None) -> float | None:
    """Return ``round((end - start) / 60, 1)`` in minutes, or ``None``.

    Used by analytics to compute per-job durations and wait times from
    the Buildkite ``started_at`` / ``finished_at`` / ``runnable_at`` pairs.
    """
    s, e = parse_iso(start), parse_iso(end)
    if s is None or e is None:
        return None
    return round((e - s).total_seconds() / 60, 1)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def percentile(sorted_values: Sequence[float], pct: float) -> float:
    """Index-based percentile (Buildkite-style, not linear interpolation).

    Keeps the exact behavior the legacy snapshot and hotness collectors
    already emit — rewriting to a proper interpolated percentile would
    silently shift the historical timeseries.
    """
    if not sorted_values:
        return 0.0
    idx = int(len(sorted_values) * pct / 100)
    return sorted_values[min(idx, len(sorted_values) - 1)]


# ---------------------------------------------------------------------------
# Buildkite field extraction
# ---------------------------------------------------------------------------

def queue_from_rules(rules: Iterable[str] | None) -> str | None:
    """Extract the ``queue=<name>`` agent rule, or ``None`` if not present."""
    for r in rules or ():
        if isinstance(r, str) and r.startswith("queue="):
            return r.split("=", 1)[1]
    return None


# ---------------------------------------------------------------------------
# Workload classification (vllm vs vllm-omni)
# ---------------------------------------------------------------------------

def classify_workload(pipeline_slug: str, branch: str, queue: str = "") -> str:
    """Return ``"omni"`` for vllm-Omni traffic, else ``"vllm"``.

    Precedence:
      1. Dedicated Omni queue (``<name>-omni``) — hardest signal, always wins.
      2. Branch or pipeline slug contains an ``OMNI_BRANCH_MARKERS`` substring
         (case-insensitive) — catches builds that land on a shared queue.
    """
    if queue and queue.endswith(OMNI_QUEUE_SUFFIX):
        return "omni"
    ref = (branch or "").lower()
    slug = (pipeline_slug or "").lower()
    for marker in OMNI_BRANCH_MARKERS:
        if marker in ref or marker in slug:
            return "omni"
    return "vllm"


# ---------------------------------------------------------------------------
# Hardware inference (AMD queue taxonomy)
# ---------------------------------------------------------------------------

# Matches a job-name prefix like "mi325_4: V1 e2e" or "mi355B_8: foo".
_HW_IN_NAME = re.compile(r"^(mi\d+[a-zA-Z]?)_\d+\s*:", re.IGNORECASE)


def hardware_from_job_name(job_name: str, queue: str | None = None) -> str:
    """Derive the AMD hardware tag (``mi325``, ``mi355b``, ...) or ``"unknown"``.

    Job names win when they carry a prefix, since jobs can be mis-queued;
    the queue name is the fallback for unprefixed job names.
    """
    m = _HW_IN_NAME.match(job_name or "")
    if m:
        return m.group(1).lower()
    if queue and queue.startswith(AMD_QUEUE_PREFIX):
        rest = queue[len(AMD_QUEUE_PREFIX):]
        return rest.split("_", 1)[0]
    return "unknown"
