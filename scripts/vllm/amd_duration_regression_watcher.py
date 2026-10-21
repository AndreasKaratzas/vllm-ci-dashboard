#!/usr/bin/env python3
"""Open one state-owned issue for AMD MI main test steps that became slower.

The source is ci.all_main_reliability from analytics.json, whose groups carry
the wall minutes of every final attempt over the 30-day main cohort. One
signal is one Buildkite step on one queue: per build, the summed wall minutes
of its jobs (the shards of a sharded step) when every job passed. Only builds
with the step's current job count are comparable, so automatic shard
re-layouts never look like slowdowns.

Each signal is segmented into level regimes by binary segmentation on
outlier-clipped values; a split needs half a minute, 3 percent and five
standard errors of the median difference (per-run noise from consecutive
differences, scaled by the sizes of both sides). A slowdown is the latest
level change whose prior level the current level exceeds by at least two
minutes and 20 percent (or 15 minutes for long steps) and five standard
errors, with at least 80 percent of the current runs above the midpoint, at
most 20 percent of the baseline runs above it, and no level since the change
back at the baseline. Opening needs the new level over 8 runs and 24 hours
and the slowdown 48 hours old, so self-recovering infrastructure events do
not alert. Queue wait is excluded; jobs without a Buildkite step key are not
signals.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
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
from vllm.ci.watcher_state import write_watcher_state  # noqa: E402
from vllm.ci.reliability_history import validate_all_main_reliability  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
ANALYTICS = ROOT / "data" / "vllm" / "ci" / "analytics.json"
STATE = ROOT / "data" / "vllm" / "ci" / "open_amd_duration_regression_issues.json"

PIPELINE = "ci"
INCIDENT_STATE_VERSION = 2
RULES = {
    "min_segment_runs": 5,
    "min_baseline_runs": 8,
    "min_recent_runs": 8,
    "min_level_hours": 24,
    "min_slowdown_hours": 48,
    "segment_min_mins": 0.5,
    "segment_min_fraction": 0.03,
    "sigmas": 5.0,
    "noise_floor_fraction": 0.03,
    "alert_min_mins": 2.0,
    "alert_min_fraction": 0.20,
    "alert_large_mins": 15.0,
    "consistency": 0.8,
    # An open incident stays open until its increase falls below half of the
    # alert thresholds, so a slowdown at the threshold does not flap.
    "resolve_scale": 0.5,
    "settle_after_days": 14,
    "stale_after_hours": 72,
}
MAX_DATA_AGE = timedelta(hours=3)
# Builds cached before step keys were kept lose them until the next full
# refresh; with that many observations without a key the history is incomplete.
MAX_KEYLESS_SHARE = 0.1
SERIES_LIMIT = 60
MAX_ISSUE_ROWS = 40
VLLM_REPO_URL = "https://github.com/vllm-project/vllm"
OWNERSHIP_MARKER = "<!-- vllm-ci-dashboard:managed-alert:amd-duration-regression:v1 -->"
LABEL_SPECS = [
    (
        "amd-duration-regression",
        "d93f0b",
        "AMD main test step is at least 20 percent and two minutes slower",
    ),
    ("automated", "6f42c1", "Managed by dashboard automation"),
    ("workstream:dev", "1d76db", "AMD CI test-area development"),
]
DASHBOARD_URL = "https://andreaskaratzas.github.io/vllm-ci-dashboard/#/analytics?view=slowdowns"
_SHARD_SUFFIX = re.compile(r"\s+shard\s*\d+(?:\s*/\s*\d+)?\s*$", re.IGNORECASE)


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


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _positive_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number > 0 else None


def _default_state() -> dict:
    return {
        "schema_version": 1,
        "incident_state_version": INCIDENT_STATE_VERSION,
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
    # Older rows were keyed by strict job identity; they are re-derived.
    current = isinstance(raw, dict) and raw.get("incident_state_version") == INCIDENT_STATE_VERSION
    normalized["incident_state_version"] = INCIDENT_STATE_VERSION
    normalized["active"] = {
        str(signal_id): dict(row)
        for signal_id, row in (raw_active.items() if current and isinstance(raw_active, dict) else [])
        if isinstance(row, dict)
    }
    return normalized


def _write_state(state: dict) -> None:
    write_watcher_state(
        STATE,
        state,
        state_filename="open_amd_duration_regression_issues.json",
    )


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


def _wall_history(group: dict) -> list[tuple[int, float | None]] | None:
    """(build, final passing wall minutes or None) per build, or None if absent."""
    history = group.get("outcome_history")
    if not isinstance(history, dict):
        return None
    numbers, walls = history.get("builds"), history.get("wall_mins")
    if not isinstance(numbers, list) or not isinstance(walls, list) or len(numbers) != len(walls):
        return None
    return [
        (number, _positive_float(wall))
        for number, wall in zip(numbers, walls)
        if isinstance(number, int) and not isinstance(number, bool)
    ]


def _keyed_duration_history(reliability: dict) -> bool:
    """AMD duration history exists and nearly all of it carries step keys."""
    from vllm.pipelines import is_amd_ci_job

    counts = Counter()
    for group in reliability.get("groups") or []:
        history = _wall_history(group) if isinstance(group, dict) and is_amd_ci_job(group) else None
        if history is not None:
            counts[_signal_key(group) is not None] += len(history)
    total = counts[True] + counts[False]
    return total > 0 and counts[False] <= MAX_KEYLESS_SHARE * total


def _signal_key(group: dict) -> tuple[str, str] | None:
    """(step key, queue), or None for a job without a Buildkite step key."""
    step_key = str(group.get("step_key") or "")
    return (step_key, str(group.get("queue") or "")) if step_key else None


def _signal_id(key: tuple[str, str]) -> str:
    encoded = json.dumps(["step", *key], separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:20]


def _display_name(groups: list[dict]) -> str:
    names = Counter(
        _SHARD_SUFFIX.sub("", str(group.get("name") or group.get("raw_name") or "")).strip()
        for group in groups
    )
    return min(names, key=lambda name: (-names[name], name)) if names else "unknown"


def _signal_runs(groups: list[dict], catalog: dict[int, dict]) -> list[dict]:
    """One row per cohort build: job count and summed wall when every job passed."""
    by_build: dict[int, list[float | None]] = defaultdict(list)
    for group in groups:
        for number, wall in _wall_history(group) or []:
            if number in catalog:
                by_build[number].append(wall)
    runs = []
    for number, walls in by_build.items():
        build = catalog[number]
        runs.append({
            "build_number": number,
            "created_at": str(build.get("created_at") or ""),
            "commit": str(build.get("commit") or ""),
            "build_url": str(build.get("url") or ""),
            "jobs": len(walls),
            "wall_mins": round(sum(walls), 1) if all(wall is not None for wall in walls) else None,
        })
    runs.sort(key=lambda row: (row["created_at"], row["build_number"]))
    return runs


def _current_layout(runs: list[dict]) -> list[dict]:
    """Passing runs with the step's current job count since its last re-layout.

    The current count is the most common one among the latest runs; a single
    build with a different count is skipped, while a sustained different count
    marks the start of the current layout.
    """
    if not runs:
        return []
    min_runs = RULES["min_segment_runs"]
    jobs = Counter(row["jobs"] for row in runs[-min_runs:]).most_common(1)[0][0]
    start, other = len(runs), 0
    while start > 0:
        if runs[start - 1]["jobs"] == jobs:
            other = 0
        else:
            other += 1
            if other >= min_runs:
                start += other - 1
                break
        start -= 1
    return [row for row in runs[start:] if row["jobs"] == jobs and row["wall_mins"] is not None]


def _noise(values: list[float]) -> float:
    """Per-run noise sigma from consecutive differences, insensitive to level changes."""
    steps = [abs(right - left) for left, right in zip(values, values[1:])]
    return 1.4826 * statistics.median(steps) / 2 ** 0.5 if steps else 0.0


def _standard_error(left: list[float], right: list[float], noise: float) -> float:
    """Standard error of the level difference between two samples of these sizes."""
    level = min(statistics.median(left), statistics.median(right))
    return max(noise, RULES["noise_floor_fraction"] * level) * (1 / len(left) + 1 / len(right)) ** 0.5


def _best_split(values: list[float], low: int, high: int) -> int:
    center = statistics.median(values)
    spread = max(1.4826 * statistics.median(abs(value - center) for value in values), 0.05)
    clipped = [min(max(value, center - 4 * spread), center + 4 * spread) for value in values]
    sums, squares = [0.0], [0.0]
    for value in clipped:
        sums.append(sums[-1] + value)
        squares.append(squares[-1] + value * value)

    def cost(left: int, right: int) -> float:
        total = sums[right] - sums[left]
        return squares[right] - squares[left] - total * total / (right - left)

    count = len(clipped)
    return min(range(low, high + 1), key=lambda split: (cost(0, split) + cost(split, count), split))


def _change_points(values: list[float], noise: float, offset: int = 0) -> list[int]:
    min_runs = RULES["min_segment_runs"]
    if len(values) < 2 * min_runs:
        return []
    split = _best_split(values, min_runs, len(values) - min_runs)
    left, right = values[:split], values[split:]
    step = abs(statistics.median(right) - statistics.median(left))
    if (
        step < RULES["segment_min_mins"]
        or step < RULES["segment_min_fraction"] * min(statistics.median(left), statistics.median(right))
        or step < RULES["sigmas"] * _standard_error(left, right, noise)
    ):
        return []
    return (
        _change_points(left, noise, offset)
        + [offset + split]
        + _change_points(right, noise, offset + split)
    )


def _qualifies(
    values: list[float],
    bounds: list[int],
    step: int,
    noise: float,
    held: bool,
) -> bool:
    """Whether the current regime is a slowdown against the regime before ``step``."""
    baseline, current = values[bounds[step - 1]:bounds[step]], values[bounds[-2]:]
    base, now = statistics.median(baseline), statistics.median(current)
    increase, midpoint = now - base, (base + now) / 2
    regimes = [statistics.median(values[left:right]) for left, right in zip(bounds, bounds[1:])]
    scale = RULES["resolve_scale"] if held else 1.0
    return not (
        len(baseline) < RULES["min_baseline_runs"]
        or increase < scale * RULES["alert_min_mins"]
        or increase < scale * min(RULES["alert_min_fraction"] * base, RULES["alert_large_mins"])
        or increase < scale * RULES["sigmas"] * _standard_error(baseline, current, noise)
        # The step never returned to its baseline level after the onset, and
        # the current level is held by most of its runs.
        or min(regimes[step:]) < base + max(RULES["segment_min_mins"], RULES["segment_min_fraction"] * base)
        or sum(value > midpoint for value in current) < RULES["consistency"] * len(current)
        or sum(value <= midpoint for value in baseline) < RULES["consistency"] * len(baseline)
    )


def detect_slowdown(
    values: list[float],
    times: list[datetime],
    *,
    held: bool = False,
    onset_index: int | None = None,
) -> dict | None:
    """The current slowdown in chronological wall minutes, or None.

    ``times`` holds the start time of every run up to the latest one, which may
    extend past ``values`` when an earlier level is evaluated.

    The onset is the latest level change whose prior level the current level
    exceeds by the alert rules without the step having returned to that level
    since, so a staged or gradual slowdown is reported from its newest
    qualifying change and a small last step (or a later partial speed-up) does
    not hide a large earlier one. ``held`` applies the hysteresis thresholds of
    an already open incident, whose recorded ``onset_index`` is kept while a
    qualifying change lies within one segment of it.
    """
    noise = _noise(values)
    bounds = [0, *_change_points(values, noise), len(values)]
    tail = bounds[-2]
    if not held and tail and (
        len(values) - tail < RULES["min_recent_runs"]
        or times[-1] - times[tail] < timedelta(hours=RULES["min_level_hours"])
    ):
        # The newest level is not established yet: open on the level before
        # it, provided the newest runs have not fallen back below the slowdown.
        earlier = detect_slowdown(values[:tail], times, onset_index=onset_index)
        if earlier is None or statistics.median(values[tail:]) <= earlier["baseline_mins"] + earlier["increase_mins"] / 2:
            return None
        return earlier
    medians = [statistics.median(values[left:right]) for left, right in zip(bounds, bounds[1:])]
    steps = [
        step
        for step in range(len(medians) - 1, 0, -1)
        if _qualifies(values, bounds, step, noise, held)
    ]
    # Opening needs the slowdown to have lasted, so self-recovering
    # infrastructure events (slow setup on a queue for a day) do not alert.
    if not steps or not held and times[-1] - times[bounds[steps[0]]] < timedelta(hours=RULES["min_slowdown_hours"]):
        return None
    step, reported = steps[0], bounds[steps[0]]
    near = [
        other
        for other in steps
        if onset_index is not None and abs(bounds[other] - onset_index) <= RULES["min_segment_runs"]
    ]
    if near:
        step, reported = near[0], onset_index
    base, recent = medians[step - 1], medians[-1]
    rises = [later for later in range(step, len(medians)) if medians[later] > medians[later - 1]]
    return {
        "onset_index": reported,
        "rise_index": max(reported, bounds[rises[-1]]),
        "baseline_mins": round(base, 1),
        "baseline_runs": bounds[step] - bounds[step - 1],
        "recent_mins": round(recent, 1),
        "recent_runs": len(values) - bounds[-2],
        "increase_mins": round(recent - base, 1),
        "increase_pct": round((recent - base) / base * 100, 1),
        "noise_mins": round(noise, 2),
    }


def _sample_runs(runs: list[dict], onset_index: int) -> list[dict]:
    """Up to SERIES_LIMIT runs around the change: the baseline's tail, then the
    runs since the onset thinned evenly, keeping the onset and the latest run."""
    before = runs[max(0, onset_index - SERIES_LIMIT // 3):onset_index]
    after = runs[onset_index:]
    room = SERIES_LIMIT - len(before)
    if len(after) > room:
        after = [after[round(index * (len(after) - 1) / (room - 1))] for index in range(room)]
    return before + after


def _build_ref(run: dict) -> dict:
    return {key: run[key] for key in ("build_number", "build_url", "commit", "created_at")}


def evaluate_slowdowns(reliability: dict, state: dict) -> dict[str, dict]:
    """Current slowdowns keyed by signal id; previously open ones use hysteresis."""
    from vllm.pipelines import is_amd_ci_job

    previous = state.get("active") if isinstance(state, dict) else {}
    previous = previous if isinstance(previous, dict) else {}
    generated = _parse_ts(reliability.get("generated_at"))
    if generated is None:
        return {}
    catalog = {
        build["number"]: build
        for build in reliability.get("builds") or []
        if isinstance(build, dict) and isinstance(build.get("number"), int)
    }
    signals: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for group in reliability.get("groups") or []:
        if isinstance(group, dict) and is_amd_ci_job(group) and _wall_history(group) is not None:
            key = _signal_key(group)
            if key is not None:
                signals[key].append(group)

    stale_before = generated - timedelta(hours=RULES["stale_after_hours"])
    settle_before = generated - timedelta(days=RULES["settle_after_days"])
    active: dict[str, dict] = {}
    for key, groups in signals.items():
        runs = _current_layout(_signal_runs(groups, catalog))
        latest_at = _parse_ts(runs[-1]["created_at"]) if runs else None
        if latest_at is None or latest_at < stale_before:
            continue
        signal_id = _signal_id(key)
        existing = previous.get(signal_id) if isinstance(previous.get(signal_id), dict) else {}
        recorded = (existing.get("onset") or {}).get("build_number")
        numbers = [row["build_number"] for row in runs]
        result = detect_slowdown(
            [row["wall_mins"] for row in runs],
            [_parse_ts(row["created_at"]) or generated for row in runs],
            held=bool(existing),
            onset_index=numbers.index(recorded) if recorded in numbers[1:] else None,
        )
        if result is None:
            continue
        onset_index = result.pop("onset_index")
        last_rise = runs[result.pop("rise_index")]
        onset, last_good = runs[onset_index], runs[onset_index - 1]
        same_commit = bool(onset["commit"]) and onset["commit"] == last_good["commit"]
        # Settled: the step has not become slower again for the settle period.
        rise_time = _parse_ts(last_rise["created_at"])
        first = groups[0]
        active[signal_id] = {
            "signal_id": signal_id,
            "name": _display_name(groups),
            "step_key": str(first.get("step_key") or ""),
            "hardware": str(first.get("hardware") or "unknown"),
            "queue": str(first.get("queue") or ""),
            "jobs": runs[-1]["jobs"],
            "status": "settled" if rise_time is not None and rise_time < settle_before else "active",
            **result,
            "onset": _build_ref(onset),
            "last_good": _build_ref(last_good),
            # The same commit on both sides means the change came from the
            # environment (image, node, driver), not from vLLM source.
            "likely_cause": "environment" if same_commit else "vllm_change",
            "compare_url": (
                ""
                if same_commit or not (onset["commit"] and last_good["commit"])
                else f"{VLLM_REPO_URL}/compare/{last_good['commit']}...{onset['commit']}"
            ),
            "last_rise": _build_ref(last_rise),
            "latest": _build_ref(runs[-1]),
            "first_detected_at": str(
                existing.get("first_detected_at") or reliability.get("generated_at") or ""
            ),
            "samples": [
                [row["build_number"], row["created_at"], row["wall_mins"]]
                for row in _sample_runs(runs, onset_index)
            ],
        }
    return active


def _issue_rows(active: dict[str, dict]) -> list[dict]:
    return sorted(
        (row for row in active.values() if row.get("status") == "active"),
        key=lambda row: (-float(row.get("increase_mins") or 0), str(row.get("name") or "")),
    )


def _fingerprint(active: dict[str, dict]) -> str:
    """The set of slowdowns in the issue; a manually closed issue reopens when it changes."""
    compact = sorted(row["signal_id"] for row in _issue_rows(active))
    return hashlib.sha256(json.dumps(compact, separators=(",", ":")).encode()).hexdigest()


def _content_fingerprint(active: dict[str, dict], generated_at: str) -> str:
    """Rewrite the body when a slowdown or its onset changes, and at most daily
    for the levels, which move a little with every new run."""
    compact = [
        str(generated_at)[:10],
        sorted([row["signal_id"], (row.get("onset") or {}).get("build_number")] for row in _issue_rows(active)),
    ]
    return hashlib.sha256(json.dumps(compact, separators=(",", ":")).encode()).hexdigest()


def _md(value: Any) -> str:
    return str(value or "-").replace("|", "\\|").replace("\n", " ")


def _minutes(value: Any) -> str:
    number = _positive_float(value)
    return f"{number:.1f}m" if number is not None else "-"


def _issue_title(rows: list[dict]) -> str:
    return f"AMD main duration: {len(rows)} test steps slower since a change"


def _change_md(row: dict) -> str:
    last_good, onset = row.get("last_good") or {}, row.get("onset") or {}
    builds = f"#{last_good.get('build_number')} -> [#{onset.get('build_number')}]({onset.get('build_url')})"
    if row.get("likely_cause") == "environment":
        return f"{builds}, same vLLM commit (environment)"
    compare = row.get("compare_url")
    commits = f"{str(last_good.get('commit'))[:10]}...{str(onset.get('commit'))[:10]}"
    return f"{builds}, [{commits}]({compare})" if compare else builds


def _issue_body(active: dict[str, dict], reliability: dict, run_url: str, owner: str) -> str:
    rows = _issue_rows(active)
    settled = sum(row.get("status") == "settled" for row in active.values())
    generated_at = str(reliability.get("generated_at") or "unknown")
    lines = [
        "## AMD origin/main test-step slowdowns",
        "",
        f"**{len(rows)} AMD MI test steps on main are sustainably slower than before a change.**",
        "",
        "Signal rules:",
        "",
        (
            "- One signal per Buildkite step and queue: per completed main build, the summed wall "
            "time of its jobs when all passed. Queue wait, failed and soft-failed runs are excluded."
        ),
        "- Only builds with the step's current shard count are compared, so shard re-layouts are not slowdowns.",
        (
            f"- Alert: the current level is at least {RULES['alert_min_mins']:.0f} minutes and "
            f"{RULES['alert_min_fraction'] * 100:.0f}% (or {RULES['alert_large_mins']:.0f} minutes) above "
            f"the level before the change and {RULES['sigmas']:.0f} standard errors, held by "
            f"{RULES['consistency'] * 100:.0f}% of its runs. The new level needs {RULES['min_recent_runs']} runs "
            f"over {RULES['min_level_hours']} hours, the slowdown {RULES['min_slowdown_hours']} hours, and the "
            f"level before it {RULES['min_baseline_runs']} runs."
        ),
        (
            f"- Resolution: the increase falls below half of those thresholds or the step stops running "
            f"for {RULES['stale_after_hours']} hours. A slowdown that has not grown for "
            f"{RULES['settle_after_days']} days is the new normal: it leaves this issue and stays listed "
            "on the dashboard as settled."
        ),
        "",
        f"Collected at **{generated_at}**. [Open AMD main slowdowns]({DASHBOARD_URL}).",
        "",
        "| test step | hardware / queue | before | now | increase | changed between |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in rows[:MAX_ISSUE_ROWS]:
        latest = row.get("latest") or {}
        name = _md(row.get("name"))
        if int(row.get("jobs") or 1) > 1:
            name = f"{name} ({row['jobs']} shards)"
        lines.append(
            f"| [{name}]({latest.get('build_url')}) | {_md(row.get('hardware'))} / {_md(row.get('queue'))} "
            f"| {_minutes(row.get('baseline_mins'))} ({row.get('baseline_runs')}) "
            f"| {_minutes(row.get('recent_mins'))} ({row.get('recent_runs')}) "
            f"| +{_minutes(row.get('increase_mins'))} (+{float(row.get('increase_pct') or 0):.0f}%) "
            f"| {_change_md(row)} |"
        )
    if len(rows) > MAX_ISSUE_ROWS:
        lines.extend(["", f"{len(rows) - MAX_ISSUE_ROWS} additional steps are on the dashboard."])
    if settled:
        lines.extend(["", f"{settled} settled slowdowns are listed on the dashboard."])
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


def _recovery_body(active: dict[str, dict], run_url: str) -> str:
    settled = len(active) - len(_issue_rows(active))
    state = (
        f"{settled} AMD MI main test steps are still slower than before their change, but none has "
        f"become slower within {RULES['settle_after_days']} days; they stay listed as settled."
        if settled
        else "No AMD MI main test step is slower than before a change by the alert thresholds."
    )
    return (
        f"{state} Closing this tracked umbrella issue.\n\n"
        f"[Open AMD main slowdowns]({DASHBOARD_URL}).\n\n*{run_url}*"
    )


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
    if (
        not reliability
        or reliability.get("hardware_scope") != "amd_mi_gpu"
        or not validate_all_main_reliability(reliability, PIPELINE)
        or not _keyed_duration_history(reliability)
    ):
        log.error("Step-keyed AMD main duration history is unavailable; refusing issue mutations")
        return 0
    now = datetime.now(timezone.utc)
    if not _is_fresh(reliability, now):
        log.error("AMD main reliability is stale or future-dated; refusing issue mutations")
        return 0
    if not token:
        log.warning("GITHUB_TOKEN not set; leaving issue state untouched")
        return 0

    state = _read_state()
    active = evaluate_slowdowns(reliability, state)
    state["active"] = active
    state["hardware_scope"] = "amd_mi_gpu"
    observed_at = str(reliability.get("generated_at") or _iso(now))
    rows = _issue_rows(active)
    client = GitHubIssueClient(token, repo)
    reconciled = reconcile_managed_issue(
        state,
        active=bool(rows),
        fingerprint=_fingerprint(active),
        content_fingerprint=_content_fingerprint(active, observed_at),
        title=_issue_title(rows),
        body=_issue_body(active, reliability, run_url, repo_owner(repo)),
        ownership_marker=OWNERSHIP_MARKER,
        recovery_body=_recovery_body(active, run_url),
        observed_at=observed_at,
        label_specs=LABEL_SPECS,
        client=client,
        discovery_label="amd-duration-regression",
        recovery_labels=("automated", "workstream:dev"),
    )
    _write_state(reconciled)
    log.info(
        "AMD duration watcher: %d active and %d settled slowdowns; issue=%s suppressed=%s",
        len(rows),
        len(active) - len(rows),
        (reconciled.get("issue") or {}).get("number"),
        reconciled.get("suppressed"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
