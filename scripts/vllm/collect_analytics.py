#!/usr/bin/env python3
"""Collect per-build, per-job analytics from Buildkite for the rich CI dashboard.

Produces:
- data/vllm/ci/builds_analytics.json — per-build summary with job matrix
- data/vllm/ci/jobs_analytics.json — per-job failure/duration rankings

Usage:
    export BUILDKITE_TOKEN="bkua_..."
    python scripts/vllm/collect_analytics.py --days 14
"""

import argparse
import json
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.constants import BK_API_BASE, BK_ORG  # noqa: E402
from vllm.ci.utils import (  # noqa: E402
    duration_mins,
    parse_iso as parse_ts,
    percentile,
    queue_from_rules as _queue_from_rules,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

PIPELINES = {"amd-ci": "AMD CI", "ci": "Upstream CI"}
ANALYTICS_WINDOWS_DAYS = (1, 3, 7, 14)
DEFAULT_ANALYTICS_WINDOW_DAYS = 7

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT = ROOT / "data" / "vllm" / "ci"

RESULT_SUFFIX = {"amd-ci": "amd", "ci": "upstream"}
# Current vLLM nightly slots in UTC. Actual Buildkite ``created_at`` values win
# whenever they are available; these hours are only for JSONL-only fallbacks.
FALLBACK_CREATED_HOUR_UTC = {"amd-ci": 9, "ci": 6}


def _iso_from_nightly_date(date_str: str, pipeline_slug: str) -> str:
    """Best-effort timestamp for JSONL-only builds.

    The analytics UI needs a ``created_at`` value for window filtering. When a
    Buildkite list response is partial, the parsed test-result JSONL still has
    the nightly date and build number, so synthesize the current schedule hour.
    """
    if not date_str:
        return ""
    hour = FALLBACK_CREATED_HOUR_UTC.get(pipeline_slug, 12)
    return f"{date_str}T{hour:02d}:00:00Z"


def _result_count(row: dict) -> int:
    """Extract collapsed pytest count from rows like ``__passed__ (136)``."""
    name = str(row.get("name") or "")
    m = re.search(r"\((\d+)\)\s*$", name)
    return int(m.group(1)) if m else 1


def _result_status_to_job_state(statuses: list[str]) -> str:
    """Collapse one job's parsed test rows into a single analytics state."""
    lowered = {str(s or "").lower() for s in statuses}
    if lowered & {"soft_fail", "soft_failed"}:
        return "soft_fail"
    if lowered & {"failed", "error", "timed_out", "broken", "canceled"}:
        return "failed"
    if lowered & {"passed", "xpassed"}:
        return "passed"
    if lowered & {"skipped", "xfailed"}:
        return "skipped"
    return "unknown"


def nightly_date(iso_str):
    """Convert a UTC timestamp to the 'nightly date'.

    Boundary at 12:00 UTC so both pipelines align in the same column. Current
    scheduled runs are before noon UTC (upstream at ~06:00, AMD at ~09:00), so
    they keep the same calendar day. Older upstream runs after noon still map
    to the following nightly date.
    """
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.hour >= 12:
            dt += timedelta(days=1)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return iso_str[:10] if iso_str else ""


def bk_get(path, token, params=None):
    headers = {"Authorization": f"Bearer {token}"}
    results = []
    url = f"{BK_API_BASE}{path}"
    p = dict(params or {})
    while url:
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=headers, params=p if not results else None, timeout=30)
                if resp.status_code == 429:
                    import time
                    wait = int(resp.headers.get("Retry-After", 5 * (attempt + 1)))
                    log.warning("Rate limited, waiting %ds", wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                break
            except requests.exceptions.Timeout:
                if attempt < 2:
                    import time; time.sleep(3)
                    continue
                raise
        results.extend(resp.json() if isinstance(resp.json(), list) else [resp.json()])
        url = resp.links.get("next", {}).get("url")
    return results


def queue_from_rules(rules):
    """Analytics wants ``"unknown"`` when no queue rule is present (keeps
    the job-stats queue column non-null)."""
    return _queue_from_rules(rules) or "unknown"


def normalize_job(name):
    """Strip hardware prefix for cross-build comparison."""
    name = re.sub(r'^(mi\d+_\d+|gpu_\d+|amd_\w+):\s*', '', name, flags=re.IGNORECASE)
    return name.strip()


def queue_from_result_job_name(name):
    """Derive an AMD queue from a parsed JSONL job name when metadata is absent."""
    match = re.match(r"^(mi\d+_\d+):\s*", name or "", flags=re.IGNORECASE)
    if match:
        return "amd_" + match.group(1).lower()
    match = re.match(r"^(amd[-_\w]+):\s*", name or "", flags=re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return None


def job_metadata_keys(job):
    """Return identity keys from most-specific to most-general.

    ``name`` is normalized for cross-build rankings, but parsed JSONL can have
    the same normalized title on several hardware pools in one build. Keeping
    ``raw_name`` first prevents an MI300 failure from being attached to the
    MI355 row in the AMD hardware matrix.
    """
    raw = (job.get("raw_name") or job.get("job_name") or job.get("full_name") or "").strip()
    name = (job.get("name") or "").strip()
    keys = []
    for key in (raw, name, normalize_job(raw), normalize_job(name)):
        if key and key not in keys:
            keys.append(key)
    return keys


def _build_job_metadata(builds: list[dict]) -> dict[int, dict[str, dict]]:
    """Index existing per-job timing/queue metadata by build number and name."""
    meta: dict[int, dict[str, dict]] = {}
    for build in builds:
        by_name = meta.setdefault(int(build.get("number") or 0), {})
        for job in build.get("jobs") or []:
            payload = {
                k: job[k]
                for k in ("dur", "wait", "q", "state", "soft_failed")
                if k in job and job[k] is not None
            }
            for key in job_metadata_keys(job):
                by_name[key] = payload
    return meta


def _build_metadata(builds: list[dict]) -> dict[int, dict]:
    """Build-level metadata we can carry over when using parsed JSONL state."""
    return {int(b.get("number") or 0): b for b in builds if b.get("number") is not None}


def load_test_result_builds(output: Path, pipeline_slug: str, days: int, buildkite_builds: list[dict] | None = None,
                            previous_builds: list[dict] | None = None) -> list[dict]:
    """Build analytics rows from parsed CI test-result JSONL files.

    ``collect_ci.py`` runs immediately before this script in the scheduled
    workflow. Those JSONL files are the same parsed test source used by CI
    Health, so they are a better source for AMD failure/pass-rate analytics than
    Buildkite's soft-failed job state. Buildkite data, when present, is still
    used for wall-clock, queue, wait, and exact URLs.
    """
    suffix = RESULT_SUFFIX.get(pipeline_slug)
    if not suffix:
        return []

    results_dir = output / "test_results"
    if not results_dir.exists():
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    paths = sorted(results_dir.glob(f"*_{suffix}.jsonl"))
    paths = [p for p in paths if p.name.rsplit("_", 1)[0] >= cutoff]
    if not paths:
        return []

    bk_meta = _build_metadata(buildkite_builds or [])
    prev_meta = _build_metadata(previous_builds or [])
    job_meta = _build_job_metadata(previous_builds or [])
    for build_number, jobs in _build_job_metadata(buildkite_builds or []).items():
        job_meta.setdefault(build_number, {}).update(jobs)

    grouped: dict[int, dict] = {}
    for path in paths:
        fallback_date = path.name.rsplit("_", 1)[0]
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                log.warning("Skipping malformed analytics test-result row in %s", path)
                continue
            if row.get("pipeline") and row.get("pipeline") != pipeline_slug:
                continue
            build_number = int(row.get("build_number") or 0)
            if not build_number:
                continue
            raw_job_name = str(row.get("job_name") or row.get("classname") or "unknown").strip()
            job_name = normalize_job(raw_job_name)
            if not raw_job_name or not job_name:
                continue
            bucket = grouped.setdefault(build_number, {
                "date": row.get("date") or fallback_date,
                "jobs": {},
            })
            job = bucket["jobs"].setdefault(raw_job_name, {
                "name": job_name,
                "raw_name": raw_job_name,
                "statuses": [],
                "dur": 0.0,
                "tests": 0,
                "passed_tests": 0,
                "failed_tests": 0,
                "skipped_tests": 0,
            })
            status = str(row.get("status") or "unknown").lower()
            count = _result_count(row)
            job["statuses"].append(status)
            job["dur"] += float(row.get("duration_secs") or 0.0) / 60.0
            job["tests"] += count
            if status in ("passed", "xpassed"):
                job["passed_tests"] += count
            elif status in ("failed", "error", "timed_out", "broken", "canceled"):
                job["failed_tests"] += count
            elif status in ("skipped", "xfailed"):
                job["skipped_tests"] += count

    builds = []
    for build_number, bucket in grouped.items():
        meta = bk_meta.get(build_number) or prev_meta.get(build_number) or {}
        jobs = []
        passed = failed = soft = skipped = 0
        for raw_name, raw_job in sorted(bucket["jobs"].items()):
            metadata = (
                job_meta.get(build_number, {}).get(raw_name)
                or job_meta.get(build_number, {}).get(raw_job["name"])
                or {}
            )
            state = _result_status_to_job_state(raw_job["statuses"])
            if metadata.get("state") == "soft_fail" or metadata.get("soft_failed"):
                state = "soft_fail"
            elif state == "unknown" and metadata.get("state"):
                state = metadata["state"]

            if state == "passed":
                passed += 1
            elif state == "failed":
                failed += 1
            elif state == "soft_fail":
                soft += 1
            elif state == "skipped":
                skipped += 1

            entry = {
                "name": raw_job["name"],
                "raw_name": raw_job["raw_name"],
                "state": state,
                "dur": round(raw_job["dur"], 1),
                "tests": raw_job["tests"],
                "passed_tests": raw_job["passed_tests"],
                "failed_tests": raw_job["failed_tests"],
                "skipped_tests": raw_job["skipped_tests"],
            }
            queue = queue_from_result_job_name(raw_job["raw_name"])
            if queue:
                entry["q"] = queue
            for k, v in metadata.items():
                if k == "dur" and entry["dur"] > 0:
                    continue
                if k == "q" and entry.get("q"):
                    continue
                if k in ("state", "soft_failed"):
                    continue
                entry[k] = v
            jobs.append(entry)

        created = meta.get("created_at") or _iso_from_nightly_date(bucket["date"], pipeline_slug)
        build_state = meta.get("state") or ("failed" if failed else "passed")
        builds.append({
            "number": build_number,
            "state": build_state,
            "created_at": created,
            "date": bucket["date"] or nightly_date(created),
            "message": meta.get("message") or "nightly",
            "author": meta.get("author") or "",
            "wall_mins": meta.get("wall_mins"),
            "passed": passed,
            "failed": failed,
            "soft_failed": soft,
            "skipped": skipped,
            "total_jobs": len(jobs),
            "jobs": jobs,
            "web_url": meta.get("web_url") or f"https://buildkite.com/{BK_ORG}/{pipeline_slug}/builds/{build_number}",
            "source": "test_results",
        })

    builds.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return builds


def choose_analytics_builds(buildkite_builds: list[dict], result_builds: list[dict],
                            previous_builds: list[dict] | None = None, pipeline_slug: str = "") -> list[dict]:
    """Prefer parsed test-result builds, with guards against empty overwrites."""
    if result_builds:
        if buildkite_builds and len(result_builds) < max(2, len(buildkite_builds) // 2):
            log.warning(
                "%s has only %d parsed-result builds versus %d Buildkite builds; keeping Buildkite analytics",
                pipeline_slug, len(result_builds), len(buildkite_builds),
            )
            return buildkite_builds
        if len(result_builds) > len(buildkite_builds):
            log.info("  using %d parsed test-result builds for %s analytics", len(result_builds), pipeline_slug)
        return result_builds

    if previous_builds and not buildkite_builds:
        log.warning("  preserving previous %s analytics: fresh collection returned no builds", pipeline_slug)
        return previous_builds

    return buildkite_builds


def collect_pipeline(pipeline_slug, token, days, nightly_only=False, name_pattern=None):
    """Collect nightly builds and preserve per-job detail for later windows."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    log.info("Fetching %s builds (last %d days)...", pipeline_slug, days)

    builds_raw = bk_get(
        f"/organizations/{BK_ORG}/pipelines/{pipeline_slug}/builds",
        token, {"branch": "main", "created_from": since, "per_page": 100, "include_retried_jobs": "true"}
    )
    log.info("  %d total builds fetched", len(builds_raw))

    # Filter to nightly if requested
    if nightly_only and name_pattern:
        pat = re.compile(name_pattern, re.IGNORECASE)
        builds_raw = [b for b in builds_raw if pat.search(b.get("message", "") or "")]
        log.info("  %d nightly builds after filter", len(builds_raw))

    builds = []

    for b in builds_raw:
        build_num = b.get("number", 0)
        build_state = b.get("state", "")
        created = b.get("created_at", "")
        finished = b.get("finished_at", "")
        wall_mins = duration_mins(created, finished)
        message = (b.get("message") or "")[:100]
        author = (b.get("creator") or {}).get("name", "") or (b.get("author") or {}).get("name", "")

        jobs = [j for j in b.get("jobs", []) if j.get("type") == "script"]

        job_summaries = []
        passed = failed = soft = 0

        for j in jobs:
            name = j.get("name", "unknown")
            norm = normalize_job(name)
            state = j.get("state", "")
            sf = j.get("soft_failed", False)
            queue = queue_from_rules(j.get("agent_query_rules"))

            dur = duration_mins(j.get("started_at"), j.get("finished_at"))
            wait = duration_mins(j.get("runnable_at"), j.get("started_at"))

            if state == "passed":
                passed += 1
            elif sf:
                soft += 1
            elif state in ("failed", "timed_out", "broken"):
                failed += 1

            job_entry = {
                "name": norm,
                "raw_name": name,
                "state": "soft_fail" if sf else state,
                "dur": dur,
            }
            if wait is not None: job_entry["wait"] = round(wait, 1)
            if queue: job_entry["q"] = queue
            job_summaries.append(job_entry)

        builds.append({
            "number": build_num,
            "state": build_state,
            "created_at": created,
            "date": nightly_date(created),
            "message": message,
            "author": author,
            "wall_mins": wall_mins,
            "passed": passed,
            "failed": failed,
            "soft_failed": soft,
            "total_jobs": len(jobs),
            "jobs": job_summaries,
            "web_url": b.get("web_url", ""),
        })

    # Sort builds newest first
    builds.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return builds


def compute_job_rankings(builds):
    """Aggregate per-job rankings from the provided build slice."""
    job_stats = defaultdict(lambda: {"runs": 0, "passed": 0, "failed": 0, "soft_failed": 0,
                                     "durations": [], "wait_times": [], "queues": set()})

    for build in builds:
        for job in build.get("jobs", []):
            name = job.get("name", "unknown")
            state = job.get("state", "")
            queue = job.get("q")
            dur = job.get("dur")
            wait = job.get("wait")

            if state == "passed":
                job_stats[name]["passed"] += 1
            elif state == "soft_fail":
                job_stats[name]["soft_failed"] += 1
            elif state in ("failed", "timed_out", "broken"):
                job_stats[name]["failed"] += 1

            job_stats[name]["runs"] += 1
            if dur is not None:
                job_stats[name]["durations"].append(dur)
            if wait is not None:
                job_stats[name]["wait_times"].append(wait)
            if queue:
                job_stats[name]["queues"].add(queue)

    job_rankings = []
    for name, s in sorted(job_stats.items()):
        total = s["runs"]
        if total == 0:
            continue
        durs = sorted(s["durations"])
        waits = sorted(s["wait_times"])
        fail_rate = round((s["failed"] + s["soft_failed"]) / total * 100, 1)
        job_rankings.append({
            "name": name,
            "runs": total,
            "passed": s["passed"],
            "failed": s["failed"],
            "soft_failed": s["soft_failed"],
            "fail_rate": fail_rate,
            "is_soft_fail": s["failed"] == 0 and s["soft_failed"] > 0,
            "median_dur": round(median(durs), 1) if durs else None,
            "p90_dur": round(percentile(durs, 90), 1) if durs else None,
            "avg_dur": round(mean(durs), 1) if durs else None,
            "max_dur": round(max(durs), 1) if durs else None,
            "median_wait": round(median(waits), 1) if waits else None,
            "p90_wait": round(percentile(waits, 90), 1) if waits else None,
            "avg_wait": round(mean(waits), 1) if waits else None,
            "max_wait": round(max(waits), 1) if waits else None,
            "queues": sorted(s["queues"]),
        })
    return job_rankings


def compute_daily_stats(builds):
    """Aggregate pass/fail per day for stacked bar chart."""
    by_date = defaultdict(lambda: {"passed": 0, "failed": 0, "total": 0})
    for b in builds:
        d = b.get("date", "")
        if not d: continue
        if b["state"] in ("passed",):
            by_date[d]["passed"] += 1
        elif b["state"] in ("failed", "failing"):
            by_date[d]["failed"] += 1
        by_date[d]["total"] += 1
    return [{"date": k, **v} for k, v in sorted(by_date.items())]


def compute_queue_stats(job_rankings):
    """Aggregate wait times by queue."""
    by_queue = defaultdict(lambda: {"jobs": 0, "waits": []})
    for j in job_rankings:
        for q in j.get("queues", []):
            by_queue[q]["jobs"] += j["runs"]
            if j.get("median_wait") is not None:
                by_queue[q]["waits"].extend([j["median_wait"]] * j["runs"])

    queue_stats = []
    for q, d in sorted(by_queue.items()):
        waits = d["waits"]
        queue_stats.append({
            "queue": q,
            "jobs": d["jobs"],
            "median_wait": round(median(waits), 1) if waits else None,
            "p90_wait": round(sorted(waits)[int(len(waits) * 0.9)], 1) if len(waits) > 1 else None,
            "avg_wait": round(mean(waits), 1) if waits else None,
            "max_wait": round(max(waits), 1) if waits else None,
        })
    queue_stats.sort(key=lambda x: x.get("median_wait") or 0, reverse=True)
    return queue_stats


def compute_summary(builds, job_rankings):
    total_builds = len(builds)
    passed_builds = sum(1 for b in builds if b["state"] == "passed")
    failed_builds = sum(1 for b in builds if b["state"] in ("failed", "failing"))
    hard_failed_jobs = sum(1 for j in job_rankings if j["failed"] > 0)
    soft_failed_jobs = sum(1 for j in job_rankings if j["failed"] == 0 and j["soft_failed"] > 0)
    return {
        "total_builds": total_builds,
        "passed": passed_builds,
        "failed": failed_builds,
        "pass_rate": round(passed_builds / total_builds * 100, 1) if total_builds else 0,
        "total_jobs_tracked": len(job_rankings),
        "jobs_with_failures": hard_failed_jobs + soft_failed_jobs,
        "jobs_with_hard_failures": hard_failed_jobs,
        "jobs_with_soft_failures": soft_failed_jobs,
    }


def filter_builds_for_window(builds, window_days, now=None):
    if window_days <= 0:
        return []
    ref_now = now or datetime.now(timezone.utc)
    cutoff = ref_now - timedelta(days=window_days)
    return [
        build for build in builds
        if (parse_ts(build.get("created_at")) or cutoff) >= cutoff
    ]


def build_window_block(builds, window_days):
    job_rankings = compute_job_rankings(builds)
    failure_ranking = sorted(job_rankings, key=lambda x: x["fail_rate"], reverse=True)
    duration_ranking = sorted(job_rankings, key=lambda x: x.get("median_dur") or 0, reverse=True)
    return {
        "window_days": window_days,
        "build_count": len(builds),
        "summary": compute_summary(builds, job_rankings),
        "daily_stats": compute_daily_stats(builds),
        "builds": builds[:50],
        "nightly_builds": builds[:14],
        "failure_ranking": [j for j in failure_ranking if j["failed"] > 0 or j["soft_failed"] > 0],
        "duration_ranking": duration_ranking,
        "queue_stats": compute_queue_stats(job_rankings),
    }


def compute_window_blocks(builds, max_days, now=None):
    window_days = sorted({d for d in ANALYTICS_WINDOWS_DAYS if d <= max_days} | {max_days})
    return {
        f"{days}d": build_window_block(filter_builds_for_window(builds, days, now=now), days)
        for days in window_days
    }


def main():
    parser = argparse.ArgumentParser(description="Collect CI analytics for rich dashboard")
    parser.add_argument("--days", type=int, default=14, help="Days of history (default: 14)")
    parser.add_argument("--pipeline", choices=["amd-ci", "ci", "both"], default="both")
    parser.add_argument("--output", type=str, default=str(OUTPUT))
    args = parser.parse_args()

    token = os.getenv("BUILDKITE_TOKEN")
    if not token:
        log.warning("BUILDKITE_TOKEN not set; using parsed test_results and previous metadata only")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    previous_data = {}
    previous_path = output / "analytics.json"
    if previous_path.exists():
        try:
            previous_data = json.loads(previous_path.read_text())
        except json.JSONDecodeError:
            log.warning("Ignoring malformed previous analytics at %s", previous_path)

    pipelines = ["amd-ci", "ci"] if args.pipeline == "both" else [args.pipeline]
    nightly_patterns = {"amd-ci": r"AMD Full CI Run.*nightly", "ci": r"Full CI run.*nightly"}

    all_data = {}
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ref_now = datetime.now(timezone.utc)

    for slug in pipelines:
        log.info("=== %s ===", PIPELINES.get(slug, slug))

        # Collect nightly builds only for analytics
        previous_builds = (previous_data.get(slug) or {}).get("builds") or []
        buildkite_builds = (
            collect_pipeline(
                slug, token, args.days, nightly_only=True, name_pattern=nightly_patterns.get(slug)
            )
            if token
            else []
        )
        result_builds = load_test_result_builds(output, slug, args.days, buildkite_builds, previous_builds)
        builds = choose_analytics_builds(buildkite_builds, result_builds, previous_builds, slug)
        job_rankings = compute_job_rankings(builds)
        windows = compute_window_blocks(builds, args.days, now=ref_now)
        default_window_days = min(DEFAULT_ANALYTICS_WINDOW_DAYS, args.days)
        default_window_key = f"{default_window_days}d"
        if default_window_key not in windows:
            default_window_key = sorted(windows.keys(), key=lambda k: int(k[:-1]))[-1]

        daily = compute_daily_stats(builds)
        queues = compute_queue_stats(job_rankings)

        # Sort rankings
        failure_ranking = sorted(job_rankings, key=lambda x: x["fail_rate"], reverse=True)
        duration_ranking = sorted(job_rankings, key=lambda x: x.get("median_dur") or 0, reverse=True)

        all_data[slug] = {
            "pipeline": slug,
            "display_name": PIPELINES.get(slug, slug),
            "days": args.days,
            "generated_at": generated_at,
            "summary": compute_summary(builds, job_rankings),
            "daily_stats": daily,
            "builds": builds[:50],  # Last 50 builds for recent builds table
            "nightly_builds": builds[:14],  # Last 14 nightly builds
            "failure_ranking": [j for j in failure_ranking if j["failed"] > 0 or j["soft_failed"] > 0],
            "duration_ranking": duration_ranking,
            "queue_stats": queues,
            "default_window": default_window_key,
            "windows": windows,
        }

        log.info("  %d builds, %d jobs tracked, %d with failures",
                 len(builds), len(job_rankings),
                 sum(1 for j in job_rankings if j["failed"] > 0))

    # Write output
    out_path = output / "analytics.json"
    out_path.write_text(json.dumps(all_data, indent=2, default=str))
    log.info("Wrote %s", out_path)

    # Print summary
    for slug, d in all_data.items():
        s = d["summary"]
        print(f"\n{d['display_name']}: {s['total_builds']} builds, {s['pass_rate']}% pass rate, "
              f"{s['jobs_with_failures']} jobs with failures, {s['total_jobs_tracked']} jobs tracked")


if __name__ == "__main__":
    main()
