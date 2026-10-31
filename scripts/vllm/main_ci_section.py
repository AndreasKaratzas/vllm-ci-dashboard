"""Compact read model of every completed AMD MI ``ci`` build on branch=main.

The nightly section answers "how did the 06:00 run go". This section treats
main as the source of truth: one summary row per completed main build, the
main-failure watcher's confirmed incidents and their bisect ranges, the
incident timeline, flaky groups, retry hotspots and pass-rate series. It is
derived from the same cohort as the large ``reliability`` drill-down but is
bounded small enough for headline views.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from vllm.ci import main_flakiness
from vllm.ci.reliability_history import observation_job_url, validate_all_main_reliability


PIPELINE = "ci"
DAILY_SERIES_DAYS = 30
HOURLY_SERIES_HOURS = 7 * 24
TOP_FLAKY_LIMIT = 50
RETRY_HOTSPOT_LIMIT = 30
HEADLINE_MAX_CHARS = 160
AMD_COUNT_KEYS = ("passed", "soft", "hard", "unknown", "not_run", "retried")
FAILING_CODES = {"f": "hard", "s": "soft"}
# A window's hard jobs are concentrated when at most CONCENTRATED_MAX_BUILDS
# builds carry at least CONCENTRATED_MIN_SHARE of them and the newest
# CONCENTRATED_GREEN_BUILDS AMD builds have no hard job and every group that
# hard-failed in those builds has passed since: a contained event, not
# sustained breakage.
CONCENTRATED_MAX_BUILDS = 3
CONCENTRATED_MIN_SHARE = 0.8
CONCENTRATED_GREEN_BUILDS = 10


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _headline(message: Any) -> str:
    first = str(message or "").strip().splitlines()[0] if str(message or "").strip() else ""
    if len(first) <= HEADLINE_MAX_CHARS:
        return first
    return first[: HEADLINE_MAX_CHARS - 3] + "..."


def _amd_counts(build: dict) -> dict | None:
    counts = build.get("amd_jobs")
    if not isinstance(counts, dict):
        return None
    result = {}
    for key in AMD_COUNT_KEYS:
        value = counts.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        result[key] = value
    return result


def _final_attempt_url(group: dict, number: int, build_url: str) -> str:
    """The job page of the group's final attempt in one build, while its observations still hold it."""
    attempts = [
        row
        for row in group.get("observations") or []
        if isinstance(row, dict) and row.get("build_number") == number
    ]
    finals = [row for row in attempts if not (row.get("retry_evidence") or {}).get("retried")]
    if not finals or not build_url:
        return ""
    return observation_job_url(build_url, max(finals, key=lambda row: str(row.get("observed_at") or "")))


def _failing_groups(reliability: dict) -> tuple[dict[int, list[dict]], dict[str, dict]]:
    """Per build, the groups whose final outcome was a hard or soft failure, and their names."""
    urls = {
        build.get("number"): str(build.get("url") or "")
        for build in reliability.get("builds") or []
        if isinstance(build, dict)
    }
    failing: dict[int, list[dict]] = defaultdict(list)
    catalog: dict[str, dict] = {}
    for group in reliability.get("groups") or []:
        if not isinstance(group, dict):
            continue
        history = group.get("outcome_history") or {}
        numbers, results = history.get("builds"), history.get("results")
        if not isinstance(numbers, list) or not isinstance(results, str) or len(numbers) != len(results):
            continue
        group_id = str(group.get("group_id") or "")
        for number, code in zip(numbers, results):
            if code not in FAILING_CODES:
                continue
            row = {"group_id": group_id, "severity": FAILING_CODES[code]}
            job_url = _final_attempt_url(group, number, urls.get(number, ""))
            if job_url:
                row["job_url"] = job_url
            failing[number].append(row)
            catalog[group_id] = {
                "name": str(group.get("name") or group.get("raw_name") or ""),
                "hardware": str(group.get("hardware") or ""),
                "queue": str(group.get("queue") or ""),
            }
    for rows in failing.values():
        rows.sort(key=lambda row: (row["severity"] != "hard", catalog[row["group_id"]]["name"].lower()))
    return failing, catalog


def _build_rows(reliability: dict, failing: dict[int, list[dict]]) -> list[dict]:
    rows = []
    for build in reliability.get("builds") or []:
        if not isinstance(build, dict):
            continue
        rows.append(
            {
                "number": build.get("number"),
                "created_at": str(build.get("created_at") or ""),
                "commit": str(build.get("commit") or ""),
                "headline": _headline(build.get("message")),
                "branch": str(build.get("branch") or "main"),
                "nightly": build.get("is_canonical_nightly") is True,
                "state": str(build.get("state") or "unknown"),
                "amd": _amd_counts(build),
                "url": str(build.get("url") or ""),
                "failing": failing.get(build.get("number"), []),
            }
        )
    rows.sort(key=lambda row: (row["created_at"], int(row["number"] or 0)), reverse=True)
    return rows


def _recovered_groups(reliability: dict) -> set[str]:
    """Groups whose newest pass or hard failure in the cohort is a pass."""
    recovered = set()
    for group in reliability.get("groups") or []:
        if not isinstance(group, dict):
            continue
        codes = [code for _, code in main_flakiness._history(group) if code != "s"]
        if codes and codes[-1] in main_flakiness.PASS_CODES:
            recovered.add(str(group.get("group_id") or ""))
    return recovered


def _day_totals(builds: list[dict], start: datetime, end: datetime, recovered: set[str] | None = None) -> dict:
    """AMD job totals of the builds created in [start, end)."""
    day = [
        row
        for row in builds
        if (created := main_flakiness.parse_ts(row["created_at"])) is not None and start <= created < end
    ]
    counted = [row for row in day if row["amd"]]
    jobs = defaultdict(int)
    for row in counted:
        for key in ("passed", "soft", "hard"):
            jobs[key] += row["amd"][key]
    total = jobs["passed"] + jobs["soft"] + jobs["hard"]
    return {
        "builds": len(day),
        "amd_builds": sum(bool(row["amd"]["passed"] + row["amd"]["soft"] + row["amd"]["hard"]) for row in counted),
        "green_builds": sum(
            row["amd"]["hard"] == 0 and bool(row["amd"]["passed"] + row["amd"]["soft"]) for row in counted
        ),
        "passed": jobs["passed"],
        "soft": jobs["soft"],
        "hard": jobs["hard"],
        "pass_rate": round(jobs["passed"] / total * 100, 2) if total else None,
        "hard_concentration": _hard_concentration(counted, jobs, recovered) if recovered is not None else None,
    }


def _hard_concentration(counted: list[dict], jobs: dict, recovered: set[str]) -> dict | None:
    """The few builds holding a window's hard jobs, when they are concentrated and every hard group passed since."""
    verdicts = [row for row in counted if row["amd"]["passed"] + row["amd"]["soft"] + row["amd"]["hard"]]
    top = sorted((row for row in verdicts if row["amd"]["hard"]), key=lambda row: -row["amd"]["hard"])
    top = top[:CONCENTRATED_MAX_BUILDS]
    hard = sum(row["amd"]["hard"] for row in top)
    newest = verdicts[:CONCENTRATED_GREEN_BUILDS]
    if (
        not hard
        or hard < CONCENTRATED_MIN_SHARE * jobs["hard"]
        or len(newest) < CONCENTRATED_GREEN_BUILDS
        or any(row["amd"]["hard"] for row in newest)
        or any(
            failing["group_id"] not in recovered
            for row in counted
            for failing in row["failing"]
            if failing["severity"] == "hard"
        )
    ):
        return None
    passed = jobs["passed"] - sum(row["amd"]["passed"] for row in top)
    rest = passed + jobs["soft"] - sum(row["amd"]["soft"] for row in top) + jobs["hard"] - hard
    return {
        "builds": sorted(row["number"] for row in top),
        "hard": hard,
        "pass_rate_without": round(passed / rest * 100, 2) if rest else None,
    }


def _bucket(rows: list[dict], start: datetime, step: timedelta, count: int) -> list[dict]:
    buckets = [
        {
            "t": _iso(start + step * index),
            "builds": 0,
            "amd_builds": 0,
            "green_builds": 0,
            "passed": 0,
            "soft": 0,
            "hard": 0,
            "pass_rate": None,
        }
        for index in range(count)
    ]
    for row in rows:
        created = main_flakiness.parse_ts(row["created_at"])
        if created is None or created < start:
            continue
        index = int((created - start) / step)
        if index >= count:
            continue
        bucket = buckets[index]
        bucket["builds"] += 1
        amd = row["amd"]
        if not amd or not (amd["passed"] + amd["soft"] + amd["hard"]):
            continue
        bucket["amd_builds"] += 1
        bucket["green_builds"] += int(amd["hard"] == 0)
        for key in ("passed", "soft", "hard"):
            bucket[key] += amd[key]
    for bucket in buckets:
        total = bucket["passed"] + bucket["soft"] + bucket["hard"]
        bucket["pass_rate"] = round(bucket["passed"] / total * 100, 2) if total else None
    return buckets


def _series(rows: list[dict], now: datetime) -> dict:
    day_end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    hour_end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return {
        "basis": (
            "final AMD MI job attempt per strict group in each completed main build, "
            "bucketed by build creation time (UTC); pass_rate = passed / (passed + soft + hard)"
        ),
        "daily": _bucket(rows, day_end - timedelta(days=DAILY_SERIES_DAYS), timedelta(days=1), DAILY_SERIES_DAYS),
        "hourly": _bucket(
            rows, hour_end - timedelta(hours=HOURLY_SERIES_HOURS), timedelta(hours=1), HOURLY_SERIES_HOURS
        ),
    }


def _incident_row(group_id: str, row: dict) -> dict:
    transition = row.get("transition") if isinstance(row.get("transition"), dict) else {}
    return {
        "group_id": group_id,
        "name": str(row.get("name") or ""),
        "hardware": str(row.get("hardware") or ""),
        "queue": str(row.get("queue") or ""),
        "result": str(row.get("result") or ""),
        "severity": str(transition.get("peak_severity") or transition.get("severity") or ""),
        "status": str(transition.get("status") or ""),
        "latest_build_number": row.get("build_number"),
        "latest_build_url": str(row.get("build_url") or ""),
        "job_url": str(row.get("job_url") or ""),
        "observed_at": str(row.get("observed_at") or ""),
        "good_commit": str(row.get("good_commit") or ""),
        "good_build_number": row.get("good_build_number"),
        "bad_commit": str(row.get("bad_commit") or ""),
        "bad_build_number": row.get("bad_build_number"),
        "commit_range_status": str(row.get("commit_range_status") or ""),
        "compare_url": str(row.get("compare_url") or ""),
        "bisect_command": str(row.get("bisect_command") or ""),
    }


def _incidents(state: dict, scoped: dict) -> tuple[dict, list[dict]]:
    """Confirmed incidents and timeline exactly as the main-failure watcher holds them."""
    if not isinstance(state, dict) or state.get("hardware_scope") != "amd_mi_gpu":
        return {
            "available": False,
            "reason": "main_failure_watcher_state_unavailable",
            "active": [],
            "pending_soft": [],
        }, []
    active = [
        _incident_row(str(group_id), row)
        for group_id, row in (state.get("active") or {}).items()
        if isinstance(row, dict)
    ]
    pending = [
        _incident_row(str(group_id), row)
        for group_id, row in (state.get("pending_soft") or {}).items()
        if isinstance(row, dict)
    ]

    def order(row: dict) -> tuple:
        return row["severity"] != "hard", row["observed_at"], row["name"].lower()

    active.sort(key=order)
    pending.sort(key=order)
    catalog = {build_number: build for build_number, (_, build) in main_flakiness.build_times(scoped).items()}
    timeline = [dict(row) for row in state.get("timeline") or [] if isinstance(row, dict)]
    opened = {(row.get("group_id"), row.get("build_number")) for row in timeline if row.get("event") == "opened"}
    for row in active:
        build_number = row["bad_build_number"] or row["latest_build_number"]
        if (row["group_id"], build_number) in opened:
            continue
        timeline.append(
            {
                "event": "opened",
                "group_id": row["group_id"],
                "name": row["name"],
                "hardware": row["hardware"],
                "queue": row["queue"],
                "build_number": build_number,
                "build_url": str((catalog.get(build_number) or {}).get("url") or ""),
                "at": str((catalog.get(build_number) or {}).get("created_at") or row["observed_at"]),
                "severity": row["severity"],
                "good_commit": row["good_commit"],
                "bad_commit": row["bad_commit"],
                "compare_url": row["compare_url"],
                "source": "state",
            }
        )
    timeline.sort(key=lambda row: (str(row.get("at") or ""), int(row.get("build_number") or 0)), reverse=True)
    return {
        "available": True,
        "watcher_last_run": str(state.get("last_run") or ""),
        "issue": None,
        "suppressed": False,
        "active": active,
        "pending_soft": pending,
    }, timeline


def _flakiness(scoped: dict, now: datetime, thresholds: dict[str, int]) -> tuple[list[dict], list[dict]]:
    times = main_flakiness.build_times(scoped)
    window = timedelta(days=thresholds["window_days"])
    month = timedelta(days=DAILY_SERIES_DAYS)
    horizon = now + timedelta(minutes=15)
    rows = main_flakiness.group_rows(scoped, now, thresholds["window_days"])
    by_id = {
        str(group.get("group_id") or ""): group
        for group in scoped.get("groups") or []
        if isinstance(group, dict)
    }
    flaky, hotspots = [], []
    for row in rows:
        recent, previous = row["current"], row["previous"]
        month_stats = main_flakiness.window_stats(by_id[row["group_id"]], times, now - month, horizon)
        base = {
            "group_id": row["group_id"],
            "name": row["name"],
            "hardware": row["hardware"],
            "queue": row["queue"],
            "trend": row["trend"],
        }
        # Each list alerts on its own criterion.
        if month_stats["flips"]:
            flaky.append(
                {
                    **base,
                    "alerting": main_flakiness.flips_alert(row, thresholds),
                    "flips_window": recent["flips"],
                    "flips_previous_window": previous["flips"],
                    "flips_30d": month_stats["flips"],
                    "runs_30d": month_stats["runs"],
                    "hard_30d": month_stats["hard"],
                    "soft_30d": month_stats["soft"],
                    "pass_rate_window": recent["pass_rate"],
                    "pass_rate_30d": month_stats["pass_rate"],
                    "last_failure": month_stats["last_failure"],
                }
            )
        if month_stats["recovered"]:
            hotspots.append(
                {
                    **base,
                    "alerting": main_flakiness.recoveries_alert(row, thresholds),
                    "recovered_window": recent["recovered"],
                    "recovered_previous_window": previous["recovered"],
                    "recovered_30d": month_stats["recovered"],
                    "runs_30d": month_stats["runs"],
                    "last_recovery": month_stats["last_recovery"],
                }
            )
    flaky.sort(key=lambda row: (not row["alerting"], -row["flips_window"], -row["flips_30d"], row["name"].lower()))
    hotspots.sort(
        key=lambda row: (not row["alerting"], -row["recovered_window"], -row["recovered_30d"], row["name"].lower())
    )
    return flaky, hotspots


def _slowdowns(state: dict, now: datetime) -> dict:
    """Test-step slowdowns exactly as the duration watcher holds them, provided
    the watcher evaluated this same cohort (it runs right before the snapshot)."""
    from vllm.amd_duration_regression_watcher import INCIDENT_STATE_VERSION, MAX_DATA_AGE, RULES

    if (
        not isinstance(state, dict)
        or state.get("incident_state_version") != INCIDENT_STATE_VERSION
        or state.get("hardware_scope") != "amd_mi_gpu"
    ):
        return {"available": False, "reason": "duration_watcher_state_unavailable", "rows": []}
    last_run = main_flakiness.parse_ts(state.get("last_run"))
    if last_run is None or abs(now - last_run) > MAX_DATA_AGE:
        return {"available": False, "reason": "duration_watcher_state_stale", "rows": []}
    rows = sorted(
        (dict(row) for row in (state.get("active") or {}).values() if isinstance(row, dict)),
        key=lambda row: (
            row.get("status") != "active",
            -float(row.get("increase_mins") or 0),
            str(row.get("name") or "").lower(),
        ),
    )
    return {
        "available": True,
        "watcher_last_run": str(state.get("last_run") or ""),
        "issue": None,
        "suppressed": False,
        "rules": dict(RULES),
        "rows": rows,
    }


def unavailable(reason: str, generated_at: str) -> dict:
    return {
        "available": False,
        "unavailable_reason": reason,
        "source_pipeline": PIPELINE,
        "branch": "main",
        "hardware_scope": "amd_mi_gpu",
        "generated_at": generated_at,
        "summary": {},
        "builds": [],
        "incidents": {"available": False, "active": [], "pending_soft": []},
        "slowdowns": {"available": False, "reason": reason, "rows": []},
        "timeline": [],
        "top_flaky": [],
        "retry_hotspots": [],
        "series": {"daily": [], "hourly": []},
    }


def build_main_ci(
    analytics_ci: dict,
    watcher_state: dict,
    generated_at: str,
    duration_state: dict | None = None,
) -> dict:
    reliability = (analytics_ci or {}).get("all_main_reliability")
    if not validate_all_main_reliability(reliability, PIPELINE):
        return unavailable("strict_all_main_reliability_unavailable", generated_at)
    if reliability.get("hardware_scope") != "amd_mi_gpu":
        return unavailable("mi_scoped_reliability_unavailable", generated_at)
    from vllm.amd_main_failure_watcher import mi_scoped_reliability

    scoped = mi_scoped_reliability(reliability)
    now = main_flakiness.parse_ts(reliability.get("generated_at"))
    if now is None:
        return unavailable("reliability_generated_at_unavailable", generated_at)
    thresholds = main_flakiness.load_thresholds()
    failing, group_catalog = _failing_groups(scoped)
    builds = _build_rows(scoped, failing)
    incidents, timeline = _incidents(watcher_state, scoped)
    slowdowns = _slowdowns(duration_state or {}, now)
    has_history = main_flakiness.has_outcome_history(scoped)
    flaky, hotspots = _flakiness(scoped, now, thresholds) if has_history else ([], [])
    retry = (analytics_ci or {}).get("main_retry_analysis") or {}
    retry_summary = retry.get("summary") if isinstance(retry.get("summary"), dict) else {}
    day = timedelta(days=1)
    recovered = _recovered_groups(scoped)
    latest = builds[0] if builds else None
    cohort = reliability.get("cohort") or {}
    return {
        "available": True,
        "source_pipeline": PIPELINE,
        "branch": "main",
        "hardware_scope": "amd_mi_gpu",
        "generated_at": generated_at,
        "source_generated_at": str(reliability.get("generated_at") or ""),
        "cohort": {
            key: cohort.get(key)
            for key in (
                "id",
                "window_days",
                "observed_from",
                "observed_to",
                "build_count",
                "canonical_nightly_build_count",
                "non_nightly_main_build_count",
                "exhaustive",
            )
        },
        "thresholds": thresholds,
        "outcome_history_available": has_history,
        "summary": {
            "builds": len(builds),
            "nightly_builds": sum(row["nightly"] for row in builds),
            "builds_with_amd_counts": sum(bool(row["amd"]) for row in builds),
            "latest_build": (
                {key: latest[key] for key in ("number", "created_at", "state", "nightly", "amd", "url")}
                if latest
                else None
            ),
            "last_24h": _day_totals(builds, now - day, now, recovered),
            "previous_24h": _day_totals(builds, now - 2 * day, now - day),
            "active_incidents": len(incidents["active"]),
            "hard_incidents": sum(row["severity"] == "hard" for row in incidents["active"]),
            "pending_soft": len(incidents["pending_soft"]),
            **{
                f"{status}_slowdowns": (
                    sum(row.get("status") == status for row in slowdowns["rows"])
                    if slowdowns["available"]
                    else None
                )
                for status in ("active", "settled")
            },
            "flaky_groups": len(flaky),
            "alerting_flaky_groups": sum(row["alerting"] for row in flaky),
            "retry_hotspot_groups": len(hotspots),
            "alerting_retry_groups": sum(row["alerting"] for row in hotspots),
            "retry_attempts": retry_summary.get("retry_attempt_count"),
            "failed_then_passed_recoveries": retry_summary.get("failed_then_passed_recovery_count"),
        },
        "incidents": incidents,
        "slowdowns": slowdowns,
        "timeline": timeline,
        "top_flaky": flaky[:TOP_FLAKY_LIMIT],
        "retry_hotspots": hotspots[:RETRY_HOTSPOT_LIMIT],
        "series": _series(builds, now),
        "group_catalog": group_catalog,
        "builds": builds,
    }


def bounded_main_ci(section: dict, max_bytes: int) -> dict:
    """Retain newest build rows first; every aggregate stays exact."""
    builds = list(section.get("builds") or [])

    def candidate(count: int) -> dict:
        result = dict(section)
        result["builds"] = builds[:count]
        result["publication_retention"] = {
            "policy": "newest_build_rows_with_exact_aggregates_v1",
            "max_bytes": max_bytes,
            "builds": {"source": len(builds), "published": count, "omitted": len(builds) - count},
            "complete_relative_to_source": count == len(builds),
        }
        return result

    full = candidate(len(builds))
    if _json_bytes({"main_ci": full}) <= max_bytes:
        return full
    low, high, best = 0, len(builds), None
    while low <= high:
        middle = (low + high) // 2
        current = candidate(middle)
        if _json_bytes({"main_ci": current}) <= max_bytes:
            best, low = current, middle + 1
        else:
            high = middle - 1
    if best is None:
        raise RuntimeError(
            f"Operations main_ci aggregates exceed their section budget: "
            f"{_json_bytes({'main_ci': candidate(0)})} > {max_bytes} bytes"
        )
    return best


def main_ci_shell(section: dict) -> dict:
    """Headline numbers for the manifest shell."""
    summary = section.get("summary") or {}
    return {
        "available": section.get("available") is True,
        "source_generated_at": section.get("source_generated_at"),
        **{
            key: summary.get(key)
            for key in (
                "builds",
                "latest_build",
                "last_24h",
                "previous_24h",
                "active_incidents",
                "hard_incidents",
                "active_slowdowns",
                "alerting_flaky_groups",
                "alerting_retry_groups",
            )
        },
    }
