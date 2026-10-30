"""Windowed flakiness and retry statistics over the all-main reliability cohort.

Each reliability group carries ``outcome_history``: the final outcome of the
group in every main build where it ran, oldest first (``p`` pass, ``r`` pass
only after a hard-failed attempt was retried, ``f`` hard failure, ``s`` soft
failure). The dashboard section and the flakiness watcher both read these
statistics from here so their numbers always agree.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path
from typing import Any


THRESHOLDS_PATH = Path(__file__).resolve().parents[3] / "config" / "ci_flakiness_thresholds.json"
PASS_CODES = frozenset({"p", "r"})
OUTCOME_CODES = frozenset({"p", "r", "f", "s"})


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _positive_int(payload: dict, key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"flakiness threshold {key!r} must be a positive integer")
    return value


@cache
def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict[str, int]:
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("flakiness thresholds have an unsupported schema")
    return {key: _positive_int(payload, key) for key in ("window_days", "min_outcome_flips", "min_failed_then_passed")}


def build_times(reliability: dict) -> dict[int, tuple[datetime, dict]]:
    """Creation time and catalog row for every cohort build."""
    times: dict[int, tuple[datetime, dict]] = {}
    for build in reliability.get("builds") or []:
        if not isinstance(build, dict):
            continue
        number = build.get("number")
        created = parse_ts(build.get("created_at") or build.get("finished_at"))
        if isinstance(number, int) and not isinstance(number, bool) and created:
            times[number] = (created, build)
    return times


def _history(group: dict) -> list[tuple[int, str]]:
    history = group.get("outcome_history")
    if not isinstance(history, dict):
        return []
    numbers = history.get("builds")
    results = history.get("results")
    if not isinstance(numbers, list) or not isinstance(results, str) or len(numbers) != len(results):
        return []
    return [
        (number, code)
        for number, code in zip(numbers, results)
        if isinstance(number, int) and not isinstance(number, bool) and code in OUTCOME_CODES
    ]


def has_outcome_history(reliability: dict) -> bool:
    return any(
        isinstance(group, dict) and isinstance(group.get("outcome_history"), dict)
        for group in reliability.get("groups") or []
    )


def step_runs(reliability: dict) -> dict[tuple[str, str, str], dict[int, dict[str, str]]]:
    """Outcome codes of each step key, hardware and queue by build and group."""
    runs: dict[tuple[str, str, str], dict[int, dict[str, str]]] = {}
    for group in reliability.get("groups") or []:
        if not isinstance(group, dict) or not group.get("step_key") or not group.get("group_id"):
            continue
        step = (str(group["step_key"]), str(group.get("hardware") or ""), str(group.get("queue") or ""))
        for number, code in _history(group):
            runs.setdefault(step, {}).setdefault(number, {})[str(group["group_id"])] = code
    return runs


def superseded_groups(reliability: dict) -> set[str]:
    """Groups whose step's newest run had none of the labels of the group's last run.

    A strict group is keyed by its exact label, so renaming or resharding a step
    leaves the old groups unobserved forever.
    """
    times = build_times(reliability)
    superseded: set[str] = set()
    for builds in step_runs(reliability).values():
        ordered = sorted((number for number in builds if number in times), key=lambda number: (times[number][0], number))
        newest = set(builds[ordered[-1]]) if ordered else set()
        seen: set[str] = set()
        for number in reversed(ordered):
            labels = set(builds[number])
            if not labels & newest:
                superseded |= labels - seen
            seen |= labels
    return superseded


def window_stats(
    group: dict,
    times: dict[int, tuple[datetime, dict]],
    start: datetime,
    end: datetime,
) -> dict:
    """Final-outcome counts, pass/fail flips and retry recoveries in [start, end)."""
    entries = [
        (number, code)
        for number, code in _history(group)
        if number in times and start <= times[number][0] < end
    ]
    flips = sum(
        (left in PASS_CODES) != (right in PASS_CODES)
        for (_, left), (_, right) in zip(entries, entries[1:])
    )
    counts = {code: sum(1 for _, value in entries if value == code) for code in OUTCOME_CODES}
    runs = len(entries)
    passed = counts["p"] + counts["r"]

    def latest(codes: str) -> dict | None:
        for number, code in reversed(entries):
            if code in codes:
                created, build = times[number]
                return {
                    "build_number": number,
                    "at": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "url": str(build.get("url") or ""),
                }
        return None

    return {
        "runs": runs,
        "passed": passed,
        "hard": counts["f"],
        "soft": counts["s"],
        "recovered": counts["r"],
        "flips": flips,
        "pass_rate": round(passed / runs * 100, 1) if runs else None,
        "last_failure": latest("fs"),
        "last_recovery": latest("r"),
    }


def trend(current: dict, previous: dict) -> str:
    now = current["flips"] + current["recovered"]
    before = previous["flips"] + previous["recovered"]
    if not before:
        return "new" if now else "steady"
    if now > before:
        return "rising"
    if now < before:
        return "falling"
    return "steady"


def group_rows(reliability: dict, now: datetime, window_days: int) -> list[dict]:
    """Current and preceding window statistics for every group that still runs."""
    times = build_times(reliability)
    window = timedelta(days=window_days)
    superseded = superseded_groups(reliability)
    rows = []
    for group in reliability.get("groups") or []:
        if not isinstance(group, dict) or not _history(group) or group.get("group_id") in superseded:
            continue
        current = window_stats(group, times, now - window, now + timedelta(minutes=15))
        previous = window_stats(group, times, now - 2 * window, now - window)
        rows.append(
            {
                "group_id": str(group.get("group_id") or ""),
                "name": str(group.get("name") or group.get("raw_name") or "unknown"),
                "hardware": str(group.get("hardware") or ""),
                "queue": str(group.get("queue") or ""),
                "current": current,
                "previous": previous,
                "trend": trend(current, previous),
            }
        )
    return rows


def flips_alert(row: dict, thresholds: dict[str, int]) -> bool:
    return row["current"]["flips"] >= thresholds["min_outcome_flips"]


def recoveries_alert(row: dict, thresholds: dict[str, int]) -> bool:
    return row["current"]["recovered"] >= thresholds["min_failed_then_passed"]


def crosses(row: dict, thresholds: dict[str, int]) -> bool:
    return flips_alert(row, thresholds) or recoveries_alert(row, thresholds)


def alerting_rows(reliability: dict, now: datetime, thresholds: dict[str, int]) -> list[dict]:
    """Groups crossing either threshold, most unstable first."""
    rows = [
        row
        for row in group_rows(reliability, now, thresholds["window_days"])
        if crosses(row, thresholds)
    ]
    return sorted(
        rows,
        key=lambda row: (
            -(row["current"]["flips"] + row["current"]["recovered"]),
            -row["current"]["flips"],
            row["name"].lower(),
            row["group_id"],
        ),
    )
