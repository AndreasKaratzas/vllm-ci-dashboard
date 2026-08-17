#!/usr/bin/env python3
"""Collect CI test data from Buildkite and generate dashboard JSON files.

Usage:
    export BUILDKITE_TOKEN="bkua_..."
    python scripts/collect_ci.py --days 8 --output data/vllm/ci/
    python scripts/collect_ci.py --days 1                    # daily incremental
    python scripts/collect_ci.py --dry-run                   # preview what would be fetched
    python scripts/collect_ci.py --pipeline amd --days 3     # single pipeline
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add scripts/ to path so ci/ package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from vllm.ci import config as cfg
from vllm.ci.buildkite_client import (
    fetch_build_detail,
    fetch_build_jobs,
    fetch_nightly_builds,
)
from vllm.ci.log_parser import parse_job_results
from vllm.ci.analyzer import (
    _EXCLUDE_PATTERNS,
    apply_quarantine,
    compute_all_test_health,
    compute_build_summary,
    compute_parity,
    compute_trends,
    load_quarantine,
)
from vllm.ci.reporter import (
    prune_old_results,
    write_ci_health,
    write_failure_trends,
    write_flaky_tests,
    write_parity_report,
    write_quarantine_report,
    write_test_results,
)
from vllm.ci.models import PASS_RATE_CONTRACT_VERSION, BuildSummary, TestResult
from vllm.pipelines import PIPELINES as VLLM_PIPELINES, BK_ORG as VLLM_ORG, SKIP_JOB_PATTERNS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "data" / "vllm" / "ci"
QUARANTINE_PATH = ROOT / "config" / "quarantine.yaml"
AMD_NIGHTLY_SNAPSHOT = Path(".cache") / "amd_nightly_snapshot.json"
COMPLETE_JOB_STATES = frozenset(
    set(cfg.TERMINAL_STATES)
    | set(cfg.BLOCKED_JOB_STATES)
    | {"expired", "not_run", "skipped"}
)
FULL_COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")

# Configure CI framework with vLLM-specific settings
cfg.configure(VLLM_ORG, VLLM_PIPELINES)


def _is_parity_excluded_group(norm: str) -> bool:
    """Return whether a normalized job group should stay out of parity data."""
    return bool(_EXCLUDE_PATTERNS.match(norm.strip()))


def _find_false_normalization_merges(
    results: list[TestResult],
) -> list[tuple[str, str, set[str]]]:
    """Return accidental same-hardware merges in one parity input cohort.

    Identically named tests on different hardware are expected to normalize
    together. Multiple raw names on one hardware are only valid when the name
    is a configured ``%N`` shard base.
    """
    from vllm.ci.analyzer import (
        _SHARD_BASES,
        _extract_hardware,
        _normalize_job_name,
    )

    hw_norm_to_raw: dict[tuple[str, str], set[str]] = {}
    for result in results:
        norm = _normalize_job_name(result.job_name)
        hw = _extract_hardware(result.job_name)
        hw_norm_to_raw.setdefault((hw, norm), set()).add(result.job_name)

    false_merges = []
    for (hw, norm), raw_names in hw_norm_to_raw.items():
        if len(raw_names) <= 1:
            continue
        if not any(norm.startswith(base) for base in _SHARD_BASES):
            false_merges.append((hw, norm, raw_names))
    return sorted(false_merges, key=lambda row: (row[0], row[1]))


def _find_missing_parity_groups(
    current_results: list[TestResult],
    parity: dict,
) -> list[str]:
    """Return current AMD groups absent from a computed parity payload."""
    from vllm.ci.analyzer import _normalize_job_name

    current_names = {_normalize_job_name(result.job_name) for result in current_results}
    parity_names = {group["name"] for group in parity.get("job_groups", [])}
    return sorted(
        name
        for name in current_names - parity_names
        if not _is_parity_excluded_group(name)
    )


def nightly_date(iso_str: str) -> str:
    """Convert UTC timestamp to 'nightly date' — the date the results represent.

    The nightly cycle boundary is 12:00 UTC:
    - Current runs before noon UTC (upstream at ~06:00, AMD at ~09:00) keep
      the same calendar day.
    - Older runs after noon UTC (for example the historical upstream 21:00
      slot) map to the next calendar day.

    This groups both pipelines into the same date column:
      upstream 2026-05-08 06:00 UTC → '2026-05-08'
      AMD      2026-05-08 09:00 UTC → '2026-05-08'
    Both represent the same nightly cycle.
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


def load_existing_results(results_dir: Path) -> list[tuple[int, str, list[TestResult]]]:
    """Load existing JSONL test results from disk.

    Returns:
        List of (build_number, date, results) tuples sorted oldest-first.
    """
    entries = []
    if not results_dir.exists():
        return entries

    for jsonl_file in sorted(results_dir.glob("*.jsonl")):
        results = []
        # Parse filename: YYYY-MM-DD_pipeline.jsonl
        stem = jsonl_file.stem
        parts = stem.rsplit("_", 1)
        if len(parts) != 2:
            continue
        date = parts[0]

        with open(jsonl_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                d.setdefault("step_id", "")
                results.append(TestResult(**d))

        if results:
            build_num = results[0].build_number
            entries.append((build_num, date, results))

    entries.sort(key=lambda x: x[1])  # sort by date
    return entries


def _load_cached_results(jsonl_path: Path) -> list[TestResult]:
    """Load cached TestResult rows from one JSONL file."""
    loaded = []
    if not jsonl_path.exists():
        return loaded
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if line:
                d = json.loads(line)
                d.setdefault("step_id", "")
                loaded.append(TestResult(**d))
    return loaded


def _cached_job_names(jsonl_path: Path, build_num: int) -> set[str]:
    """Return distinct ``job_name`` values already recorded for ``build_num``.

    Reads the on-disk jsonl for the date+pipeline and collects the job_name
    fields whose build_number matches. Used to decide whether a terminal
    build's cache is complete enough to skip re-fetching — see
    ``_cache_covers_all_jobs`` for the full contract.
    """
    if not jsonl_path.exists():
        return set()
    names: set[str] = set()
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("build_number") != build_num:
                # Defensive: multiple builds could in principle share a date
                # (e.g., a retry). Only count jobs that belong to the build
                # we're considering skipping.
                continue
            name = d.get("job_name")
            if name:
                names.add(name)
    return names


def _should_verify_cache_coverage(
    build_num: int,
    latest_build_num: int,
    latest_terminal_build_num: int = 0,
) -> bool:
    """Refresh the newest build and newest terminal candidate.

    When today's nightly is still running, yesterday's terminal nightly is
    the publication candidate and must still be checked for late soft-fail
    jobs before its cached JSONL is trusted.
    """
    return build_num in {latest_build_num, latest_terminal_build_num}


def _nightly_test_jobs(build: dict) -> list[dict]:
    """Return non-superseded test jobs from a Buildkite nightly roster."""
    return [
        job
        for job in build.get("jobs") or []
        if job.get("type") == "script"
        and not job.get("retried_in_job_id")
        and not any(
            skip in str(job.get("name") or "").lower()
            for skip in SKIP_JOB_PATTERNS
        )
    ]


def _is_complete_nightly_build(build: dict) -> bool:
    """Return whether a nightly has a terminal build and test-job roster."""
    if build.get("state") not in cfg.TERMINAL_STATES:
        return False
    test_jobs = _nightly_test_jobs(build)
    return bool(test_jobs) and all(
        str(job.get("state") or "").casefold() in COMPLETE_JOB_STATES
        for job in test_jobs
    )


def _select_latest_complete_evidence_build(
    builds: list[dict],
    results_by_build: dict[int, list[TestResult]],
) -> dict | None:
    """Select the newest verified-complete build with parsed test evidence."""
    ordered = sorted(
        builds,
        key=lambda build: (
            str(build.get("created_at") or ""),
            int(build.get("number") or 0),
        ),
        reverse=True,
    )
    return next(
        (
            build
            for build in ordered
            if results_by_build.get(int(build.get("number") or 0))
            and _is_complete_nightly_build(build)
        ),
        None,
    )


def _completed_result_entries(
    entries: list[tuple[int, str, list[TestResult]]],
    fetched_builds: list[dict],
) -> list[tuple[int, str, list[TestResult]]]:
    """Exclude fetched nonterminal/incomplete builds from canonical analysis."""
    builds_by_number = {
        int(build.get("number") or 0): build
        for build in fetched_builds
        if build.get("number")
    }
    return [
        entry
        for entry in entries
        if entry[0] not in builds_by_number
        or _is_complete_nightly_build(builds_by_number[entry[0]])
    ]


def _cache_covers_all_jobs(
    build: dict,
    jsonl_path: Path,
    pipeline_key: str,
    build_num: int,
) -> bool:
    """True iff the cached jsonl has at least one record for every test job
    currently visible in the build.

    This is the guard that prevents the "soft-fail timeout bug": the AMD
    nightly build can flip to ``passed`` while a ``soft_fail: true`` job is
    still running (the build doesn't wait on soft-fail jobs to block it).
    If a previous collector pass ran in that window and wrote a partial
    jsonl, a naive cache-skip would permanently omit that job's results —
    which then shows up as ``amd=None`` in the parity report and drops
    the group from the "Failing Tests" UI count.

    Implementation: compare the set of test-job names currently in the
    build against the set of ``job_name`` values in the cached jsonl. If
    any current job is missing from the cache, return False so the caller
    re-fetches and overwrites. Only counts jobs that actually ran — the
    ``fetch_build_jobs`` filter already excludes superseded retries and
    non-terminal jobs, which is the correct behavior here.
    """
    # Need the full build detail (with ``jobs`` populated) to enumerate
    # current jobs. The nightly list endpoint may return builds with only a
    # summary, so fetch detail when jobs is missing/empty.
    if "jobs" not in build or not build.get("jobs"):
        try:
            detail = fetch_build_detail(pipeline_key, build_num)
            # Keep this exact response on the shared build object. Downstream
            # summaries, parity, and the frozen AMD matrix roster must all see
            # the same point-in-time job set used for this cache decision.
            build.clear()
            build.update(detail)
        except Exception as e:
            # If the API is flaky at the moment, be conservative and trust
            # the cache. Next cron tick will try again.
            log.warning(
                "  Build #%d: couldn't fetch detail to verify cache "
                "coverage (%s) — assuming cache is complete",
                build_num, e,
            )
            return True

    current_jobs = fetch_build_jobs(build)
    current_names = {
        j.get("name", "")
        for j in current_jobs
        if not any(skip in j.get("name", "").lower() for skip in SKIP_JOB_PATTERNS)
    }
    current_names.discard("")
    if not current_names:
        # Nothing to cover — treat as covered so we don't thrash.
        return True

    cached_names = _cached_job_names(jsonl_path, build_num)
    missing = current_names - cached_names
    if missing:
        # Log a sample so the operator can see why we re-fetched. The list
        # can be long (50+) so cap at 3.
        sample = sorted(missing)[:3]
        log.info(
            "  Build #%d: %d job(s) missing from cache (e.g. %s)",
            build_num, len(missing),
            ", ".join(repr(n) for n in sample),
        )
        return False
    return True


def collect_pipeline(
    pipeline_key: str,
    days: int,
    output_dir: Path,
    dry_run: bool = False,
) -> tuple[list[dict], dict[int, list[TestResult]]]:
    """Collect test data for a single pipeline.

    Returns:
        Tuple of (nightly_builds, results_by_build_number)
    """
    log.info("=== Collecting %s pipeline ===", pipeline_key)

    cache_dir = output_dir / ".cache"
    builds = fetch_nightly_builds(pipeline_key, days=days, cache_dir=cache_dir)

    if not builds:
        log.warning("No nightly builds found for %s in the last %d days", pipeline_key, days)
        return [], {}

    log.info("Found %d nightly builds for %s", len(builds), pipeline_key)

    if dry_run:
        for b in builds:
            log.info(
                "  Build #%d: %s — %s (%s)",
                b.get("number", 0),
                b.get("message", "")[:60],
                b.get("state", ""),
                b.get("created_at", "")[:10],
            )
        return builds, {}

    # Check which builds we already have results for
    results_dir = output_dir / "test_results"
    existing_dates = set()
    for f in results_dir.glob("*.jsonl"):
        if f.stem.endswith(f"_{pipeline_key}"):
            existing_dates.add(f.stem.rsplit("_", 1)[0])

    results_by_build: dict[int, list[TestResult]] = {}
    slug = cfg.PIPELINES[pipeline_key]["slug"]
    latest_build_num = max((b.get("number", 0) for b in builds), default=0)
    latest_terminal_build_num = max(
        (
            int(build.get("number") or 0)
            for build in builds
            if build.get("state") in cfg.TERMINAL_STATES
        ),
        default=0,
    )

    for build in builds:
        build_num = build.get("number", 0)
        created = build.get("created_at", "")
        date = nightly_date(created)
        state = build.get("state", "")

        verify_candidate = _should_verify_cache_coverage(
            build_num,
            latest_build_num,
            latest_terminal_build_num,
        )
        if verify_candidate and state in cfg.TERMINAL_STATES:
            try:
                detail = fetch_build_detail(pipeline_key, build_num)
                build.clear()
                build.update(detail)
                state = build.get("state", state)
            except Exception as exc:
                log.warning(
                    "  Build #%d: couldn't refresh terminal roster (%s); "
                    "it will not be promoted unless the cached roster is complete",
                    build_num,
                    exc,
                )

        # Cache-skip eligibility: date is already on disk AND build is terminal.
        # But "build terminal" is not enough on its own — a soft-fail job can
        # finish HOURS after the build's overall state flips to ``passed``
        # (the build only waits for non-soft-fail jobs to stop blocking it).
        # If a previous collector run captured the partial jsonl while that
        # job was still running, a naive cache-skip here would permanently
        # omit the soft-fail result. Verify coverage before trusting cache.
        if date in existing_dates and state in cfg.TERMINAL_STATES:
            jsonl_path = results_dir / f"{date}_{pipeline_key}.jsonl"
            if not verify_candidate:
                log.info("  Build #%d (%s): cached historical build, skipping", build_num, date)
                loaded = _load_cached_results(jsonl_path)
                if loaded:
                    results_by_build[build_num] = loaded
                continue
            if _cache_covers_all_jobs(build, jsonl_path, pipeline_key, build_num):
                log.info("  Build #%d (%s): cached, skipping", build_num, date)
                loaded = _load_cached_results(jsonl_path)
                if loaded:
                    results_by_build[build_num] = loaded
                continue
            # Fall through to re-fetch. The cached jsonl will be overwritten
            # with a superset (all jobs that existed at the time of this run).
            log.info(
                "  Build #%d (%s): cache incomplete — re-fetching to pick up "
                "jobs that finished after the previous collector pass",
                build_num, date,
            )

        # Hydrate the roster before deciding whether this build is eligible for
        # canonical test evidence.  Buildkite can expose hundreds of completed
        # jobs while the nightly itself is still ``running``/``failing`` (and a
        # terminal build can still have late soft-fail jobs in flight).  Writing
        # those partial rows to the date-keyed JSONL would replace the previous
        # complete cohort even though analysis correctly excludes the build.
        # Keep provisional builds visible through build metadata, then retry
        # their logs on the next collection pass.
        is_running = state not in cfg.TERMINAL_STATES

        log.info("  Build #%d (%s): fetching test results...%s",
                 build_num, date, f" (build still {state})" if is_running else "")

        # Fetch full build detail if jobs not included or build still running
        if "jobs" not in build or not build["jobs"] or is_running:
            detail = fetch_build_detail(pipeline_key, build_num)
            # Keep the fetched detail in ``builds`` as well as this loop
            # variable. Later reporting must be able to see blocked jobs even
            # when there are no test-result rows for the build.
            build.clear()
            build.update(detail)

        if not _is_complete_nightly_build(build):
            log.info(
                "  Build #%d (%s): provisional roster; skipping canonical "
                "test-result publication",
                build_num,
                date,
            )
            continue

        jobs = fetch_build_jobs(build)
        # Filter to test jobs (skip bootstrap, docker build, etc.)
        test_jobs = [
            j for j in jobs
            if not any(skip in j.get("name", "").lower() for skip in SKIP_JOB_PATTERNS)
        ]
        total_jobs = len([j for j in build.get("jobs", []) if j.get("type") == "script"])
        log.info("    %d/%d jobs finished (%d test jobs)",
                 len(jobs), total_jobs, len(test_jobs))

        build_results = []
        jobs_parsed = 0

        # Parallelize log fetching — each job log is an independent HTTP request
        from concurrent.futures import ThreadPoolExecutor, as_completed
        def _parse_one(job):
            return parse_job_results(job, build_num, slug, date)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_parse_one, job): job for job in test_jobs}
            done = 0
            for future in as_completed(futures):
                done += 1
                results = future.result()
                build_results.extend(results)
                if results:
                    jobs_parsed += 1
                if done % 50 == 0:
                    log.info("    ... %d/%d jobs processed", done, len(test_jobs))

        log.info(
            "    %d jobs parsed, %d test results",
            jobs_parsed, len(build_results),
        )

        if build_results:
            results_by_build[build_num] = build_results
            write_test_results(build_results, date, pipeline_key, results_dir)

    return builds, results_by_build


def _compute_pipeline_summaries(
    pipeline_key: str,
    pipeline_results: list[tuple[int, str, list[TestResult]]],
    fetched_builds: list[dict],
) -> list:
    """Return newest-first summaries for every observed nightly build.

    Result JSONL files remain the source of test health. Buildkite build
    metadata is a separate source and may contain a terminal nightly where no
    test command ever ran. Taking the union keeps that pipeline event visible
    without inventing test outcomes for it.
    """
    results_by_number = {
        int(build_number): (date, results)
        for build_number, date, results in pipeline_results
    }
    builds_by_number = {
        int(build.get("number") or 0): build
        for build in fetched_builds
        if build.get("number")
    }
    build_numbers = set(results_by_number) | set(builds_by_number)
    slug = cfg.PIPELINES[pipeline_key]["slug"]

    def _build_for(number: int) -> dict:
        if number in builds_by_number:
            return builds_by_number[number]
        date, _ = results_by_number[number]
        return {
            "number": number,
            "created_at": date,
            "state": "unknown",
            "branch": "main",
            "jobs": [],
            "web_url": f"https://buildkite.com/{cfg.BK_ORG}/{slug}/builds/{number}",
        }

    ordered = sorted(
        build_numbers,
        key=lambda number: (
            str(_build_for(number).get("created_at") or results_by_number.get(number, ("", []))[0]),
            number,
        ),
    )
    summaries = []
    previous_signal = None
    for number in ordered:
        _, results = results_by_number.get(number, ("", []))
        summary = compute_build_summary(
            _build_for(number),
            results,
            pipeline_key,
            previous_signal if results else None,
            skip_job_patterns=SKIP_JOB_PATTERNS,
        )
        summaries.append(summary)
        if results:
            previous_signal = summary
    summaries.reverse()
    return summaries


def _latest_signal_summary(summaries: list):
    """Return the newest summary backed by parsed test evidence."""
    return next((summary for summary in summaries if summary.has_test_results), None)


def _project_test_result_summary(summary: BuildSummary) -> dict:
    """Return the legacy root summary plus explicit assertion-rate semantics."""
    assertions_run = summary.passed + summary.failed
    test_pass_rate_pct = (
        round(summary.passed / assertions_run * 100, 1)
        if assertions_run else 0.0
    )
    return {
        "total_jobs": summary.job_count,
        "passed": summary.jobs_passed,
        "failed": summary.jobs_failed,
        "skipped": 0,
        # Legacy alias retained for one compatibility cycle. It has always
        # represented parsed pytest assertions, not the adjacent job counts.
        "pass_rate": test_pass_rate_pct,
        "test_pass_rate_pct": test_pass_rate_pct,
        "test_pass_rate_basis": summary.test_pass_rate_basis,
        "test_assertions": {
            "total": summary.total_tests,
            "passed": summary.passed,
            "failed": summary.failed,
            "skipped": summary.skipped,
        },
    }


def _project_test_results_payload(
    latest_amd: BuildSummary,
    latest_upstream: BuildSummary | None = None,
    *,
    collected_at: str | None = None,
) -> dict:
    """Return the compatibility root payload with an explicit rate contract."""
    payload = {
        "pass_rate_contract_version": PASS_RATE_CONTRACT_VERSION,
        "collected_at": collected_at
        or datetime.now(timezone.utc).isoformat()[:19] + "Z",
        "source": "buildkite",
        "rocm": {
            "workflow_name": "AMD Nightly (Buildkite)",
            "run_url": latest_amd.build_url,
            "run_date": latest_amd.created_at,
            "conclusion": (
                "success" if latest_amd.pass_rate >= 0.95 else "failure"
            ),
            "summary": _project_test_result_summary(latest_amd),
        },
    }
    if latest_upstream:
        payload["cuda"] = {
            "workflow_name": "Upstream Nightly (Buildkite)",
            "run_url": latest_upstream.build_url,
            "run_date": latest_upstream.created_at,
            "conclusion": (
                "success" if latest_upstream.pass_rate >= 0.95 else "failure"
            ),
            "summary": _project_test_result_summary(latest_upstream),
        }
    return payload


def _merge_with_previous(
    by_build: list[tuple[int, str, list[TestResult]]],
) -> tuple[list[TestResult], str, int, set[str]]:
    """Select the latest result build and fill missing jobs from its predecessor."""
    if len(by_build) < 2:
        entry = max(by_build, key=lambda x: (x[1], len(x[2]))) if by_build else None
        return (
            entry[2] if entry else [],
            entry[1] if entry else "",
            entry[0] if entry else 0,
            set(),
        )

    sorted_builds = sorted(by_build, key=lambda x: (x[1], len(x[2])), reverse=True)
    latest = sorted_builds[0]
    latest_jobs = {result.job_name for result in latest[2]}
    merged = list(latest[2])
    backfilled = set()
    for previous in sorted_builds[1:]:
        if previous[0] == latest[0]:
            continue
        for result in previous[2]:
            if result.job_name not in latest_jobs:
                merged.append(result)
                latest_jobs.add(result.job_name)
                backfilled.add(result.job_name)
        break
    return merged, latest[1], latest[0], backfilled


def _compact_amd_build_snapshot(build: dict) -> dict:
    """Return the PII-free AMD build fields needed by the matrix collector."""
    build_fields = (
        "number",
        "state",
        "branch",
        "commit",
        "created_at",
        "finished_at",
        "message",
        "web_url",
    )
    job_fields = (
        "type",
        "id",
        "name",
        "state",
        "soft_failed",
        "retried_in_job_id",
        "web_url",
    )
    snapshot = {
        key: build[key]
        for key in build_fields
        if key in build and build[key] is not None
    }
    jobs = []
    for raw_job in build.get("jobs") or []:
        if not isinstance(raw_job, dict):
            continue
        job = {
            key: raw_job[key]
            for key in job_fields
            if key in raw_job and raw_job[key] is not None
        }
        queue_rules = [
            str(rule)
            for rule in raw_job.get("agent_query_rules") or []
            if str(rule).startswith("queue=")
        ]
        if queue_rules:
            job["agent_query_rules"] = queue_rules
        raw_step = raw_job.get("step") or {}
        step_id = raw_step.get("id") if isinstance(raw_step, dict) else None
        if step_id:
            job["step"] = {"id": step_id}
        jobs.append(job)
    snapshot["jobs"] = jobs
    return snapshot


def write_amd_nightly_snapshot(build: dict, output_dir: Path) -> Path:
    """Freeze the selected AMD nightly roster for downstream collectors."""
    path = output_dir / AMD_NIGHTLY_SNAPSHOT
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline": "amd-ci",
        "build": _compact_amd_build_snapshot(build),
    }
    path.write_text(json.dumps(payload, indent=2))
    log.info(
        "Wrote frozen AMD nightly snapshot %s for build #%s with %d jobs",
        path,
        payload["build"].get("number"),
        len(payload["build"]["jobs"]),
    )
    return path


def main():
    parser = argparse.ArgumentParser(description="Collect vLLM CI test data from Buildkite")
    parser.add_argument("--days", type=int, default=8, help="Days of history (8 = covers collection lag and retries)")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT), help="Output directory")
    parser.add_argument("--pipeline", choices=["amd", "upstream", "both"], default="both",
                        help="Which pipeline(s) to collect")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fetched")
    parser.add_argument("--skip-analysis", action="store_true",
                        help="Skip analysis, only collect raw data")
    parser.add_argument("--skip-config-parity", action="store_true",
                        help="Skip YAML config parity analysis")
    args = parser.parse_args()

    output_dir = Path(args.output)
    results_dir = output_dir / "test_results"

    pipelines = ["amd", "upstream"] if args.pipeline == "both" else [args.pipeline]

    # Phase 1: Collect data from Buildkite
    all_builds: dict[str, list[dict]] = {}
    all_results: dict[str, dict[int, list[TestResult]]] = {}

    for pk in pipelines:
        builds, results = collect_pipeline(pk, args.days, output_dir, args.dry_run)
        all_builds[pk] = builds
        all_results[pk] = results

    if args.dry_run:
        log.info("Dry run complete.")
        return

    if args.skip_analysis:
        log.info("Data collection complete (analysis skipped).")
        return

    evidence_build = _select_latest_complete_evidence_build(
        all_builds.get("amd", []),
        all_results.get("amd", {}),
    )
    evidence_commit = str((evidence_build or {}).get("commit") or "").casefold()
    if FULL_COMMIT_SHA_RE.fullmatch(evidence_commit):
        os.environ["VLLM_CONFIG_SHA"] = evidence_commit
        log.info(
            "Pinned CI definitions to completed AMD build #%s commit %s",
            evidence_build.get("number"),
            evidence_commit,
        )
    else:
        log.warning(
            "No completed AMD evidence build with a full commit SHA; "
            "the publication audit will reject unaligned shard metadata"
        )

    # Extract shard bases from upstream YAML (needed for correct group normalization)
    if not args.skip_config_parity:
        log.info("Extracting shard bases from upstream YAML...")
        from vllm.config_parity import (
            extract_parity_key_overrides,
            extract_shard_base_catalog,
        )
        shard_catalog = extract_shard_base_catalog()
        if evidence_build:
            evidence_date = nightly_date(str(evidence_build.get("created_at") or ""))
            shard_catalog["evidence"] = {
                "pipeline": "amd",
                "build_number": int(evidence_build.get("number") or 0),
                "build_commit": evidence_commit,
                "build_state": str(evidence_build.get("state") or ""),
                "roster_complete": _is_complete_nightly_build(evidence_build),
                "result_file": f"{evidence_date}_amd.jsonl",
                "job_names": sorted(
                    {
                        str(job.get("name") or "")
                        for job in _nightly_test_jobs(evidence_build)
                        if str(job.get("name") or "")
                    }
                ),
            }
        shard_bases = shard_catalog.get("normalization_bases", [])
        shard_path = output_dir / "shard_bases.json"
        shard_path.write_text(json.dumps(shard_bases, indent=2))
        log.info("Wrote shard_bases.json (%d bases: %s)", len(shard_bases), shard_bases)
        shard_catalog_path = output_dir / "shard_base_catalog.json"
        shard_catalog_path.write_text(json.dumps(shard_catalog, indent=2))
        log.info(
            "Wrote shard_base_catalog.json (%d definitions)",
            len(shard_catalog.get("definitions", [])),
        )
        parity_key_overrides = extract_parity_key_overrides()
        override_path = output_dir / "parity_key_overrides.json"
        override_path.write_text(json.dumps(parity_key_overrides, indent=2))
        log.info("Wrote parity_key_overrides.json (%d overrides)", len(parity_key_overrides))
        # Update the analyzer's YAML-derived normalization knobs for this run.
        from vllm.ci.analyzer import set_parity_key_overrides, set_shard_bases
        set_shard_bases(shard_bases)
        set_parity_key_overrides(parity_key_overrides)

    # Phase 2: Load all results (existing + new) for analysis
    log.info("=== Running analysis ===")

    # For each pipeline, build results_by_build tuples sorted oldest-first
    for pk in pipelines:
        existing = load_existing_results(results_dir)
        # Filter to this pipeline
        pipeline_slug = cfg.PIPELINES[pk]["slug"]
        pipeline_results = [
            (bn, d, rs) for bn, d, rs in existing
            if rs and rs[0].pipeline == pipeline_slug
        ]

        # Merge with newly collected (avoid duplicates by build_number)
        existing_build_nums = {bn for bn, _, _ in pipeline_results}
        for bn, results in all_results.get(pk, {}).items():
            if bn not in existing_build_nums and results:
                date = results[0].date
                pipeline_results.append((bn, date, results))

        pipeline_results.sort(key=lambda x: x[1])
        pipeline_results = _completed_result_entries(
            pipeline_results,
            all_builds.get(pk, []),
        )

        if pk == "amd":
            amd_by_build = pipeline_results
        else:
            upstream_by_build = pipeline_results

    latest_amd: list[TestResult] = []
    amd_date = ""
    amd_build_num = 0
    amd_backfilled: set[str] = set()
    if "amd" in pipelines:
        latest_amd, amd_date, amd_build_num, amd_backfilled = _merge_with_previous(
            amd_by_build
        )

        # Freeze the exact roster that this collection pass selected. The
        # matrix collector consumes this file instead of making a later API
        # request after more jobs may have finished.
        amd_snapshot_build = next(
            (
                build
                for build in all_builds.get("amd", [])
                if build.get("number") == amd_build_num
            ),
            None,
        )
        if amd_snapshot_build and not amd_snapshot_build.get("jobs"):
            try:
                detail = fetch_build_detail("amd", amd_build_num)
                amd_snapshot_build.clear()
                amd_snapshot_build.update(detail)
            except Exception as exc:
                log.warning(
                    "Could not hydrate frozen AMD build #%s roster: %s",
                    amd_build_num,
                    exc,
                )
        if amd_snapshot_build and amd_snapshot_build.get("jobs"):
            write_amd_nightly_snapshot(amd_snapshot_build, output_dir)
        elif amd_snapshot_build:
            log.warning(
                "AMD build #%s has no hydrated job roster; leaving the frozen "
                "snapshot absent so downstream collection uses existing analytics",
                amd_build_num,
            )

    latest_upstream: list[TestResult] = []
    up_date = ""
    up_build_num = 0
    up_backfilled: set[str] = set()
    if "upstream" in pipelines:
        latest_upstream, up_date, up_build_num, up_backfilled = _merge_with_previous(
            upstream_by_build
        )

    # Compute health for AMD tests (primary focus)
    amd_health = []
    amd_summaries = []
    if "amd" in pipelines:
        if amd_by_build:
            amd_health = compute_all_test_health(amd_by_build)
            log.info("Computed health for %d AMD tests", len(amd_health))
        amd_summaries = _compute_pipeline_summaries(
            "amd", amd_by_build, all_builds.get("amd", []),
        )

    upstream_health = []
    upstream_summaries = []
    if "upstream" in pipelines:
        if upstream_by_build:
            upstream_health = compute_all_test_health(upstream_by_build)
            log.info("Computed health for %d upstream tests", len(upstream_health))
        upstream_summaries = _compute_pipeline_summaries(
            "upstream", upstream_by_build, all_builds.get("upstream", []),
        )

    # Apply quarantine
    quarantine_config = load_quarantine(str(QUARANTINE_PATH))
    if amd_health:
        amd_health, quarantine_report = apply_quarantine(amd_health, quarantine_config)
        write_quarantine_report(quarantine_report, output_dir)

    # Phase 3: Generate reports
    log.info("=== Generating reports ===")

    # CI Health
    write_ci_health(amd_summaries, upstream_summaries, amd_health, output_dir)

    # Parity (if both pipelines collected)
    if "amd" in pipelines and "upstream" in pipelines:
        # Use the most recent build, but backfill missing job groups from
        # the previous build. This handles jobs still running in the latest
        # build (e.g., Transformers Nightly Models which runs for hours).
        if latest_amd and latest_upstream:
            # Only pass CURRENT-build results to compute_parity.
            # Backfilled results have stale failure data from previous builds
            # and should NOT inflate AMD regression counts.
            current_amd = [r for r in latest_amd if r.job_name not in amd_backfilled]
            current_upstream = [r for r in latest_upstream if r.job_name not in up_backfilled]
            parity = compute_parity(current_amd, current_upstream)
            # Tag backfilled groups so the frontend can show PENDING status.
            # Track per-HW: a group is only fully backfilled if ALL its
            # results came from previous builds. Per-HW pending is tracked
            # in hw_backfilled so the frontend can show per-HW status.
            from vllm.ci.analyzer import _normalize_job_name, _extract_hardware, _parity_key, _parity_family_name
            amd_current_norms = set()
            amd_current_hw: dict[str, set] = {}  # norm -> set of HW with current data
            amd_backfilled_hw: dict[str, set] = {}  # norm -> set of HW only from backfill
            for r in latest_amd:
                norm = _normalize_job_name(r.job_name)
                hw = _extract_hardware(r.job_name)
                if r.job_name in amd_backfilled:
                    amd_backfilled_hw.setdefault(norm, set()).add(hw)
                else:
                    amd_current_norms.add(norm)
                    amd_current_hw.setdefault(norm, set()).add(hw)
            up_backfilled_norms = {_normalize_job_name(j) for j in up_backfilled}
            up_current_norms = {
                _normalize_job_name(r.job_name) for r in latest_upstream
                if r.job_name not in up_backfilled
            }
            for g in parity.get("job_groups", []):
                name = g["name"]
                # Group is fully backfilled only if the AMD side has NO current-build results.
                # Upstream pending should NOT make the AMD hardware overlay show PENDING.
                amd_fully_bf = name in amd_backfilled_hw and name not in amd_current_norms
                g["backfilled"] = amd_fully_bf
                # Per-HW backfill: which HW only have backfilled (previous build) data
                bf_hw = amd_backfilled_hw.get(name, set()) - amd_current_hw.get(name, set())
                if bf_hw:
                    g["hw_backfilled"] = {hw: True for hw in bf_hw}
            # Phase 3b: Add pending groups for scheduled/waiting jobs
            # that have no test results yet (never completed in any build).
            # This ensures all groups from the current nightly appear in the
            # parity report, even if their jobs haven't started running.
            amd_latest_build = next(
                (b for b in all_builds.get("amd", []) if b.get("number") == amd_build_num),
                None,
            )
            # The selected build was hydrated and frozen above. Reuse that
            # exact response so parity and the matrix share one job roster.
            if amd_latest_build and not amd_latest_build.get("jobs"):
                log.warning(
                    "Frozen AMD build #%s has no job roster; pending parity "
                    "groups will remain unavailable until the next collection",
                    amd_build_num,
                )
            if amd_latest_build:
                all_script_jobs = [
                    j for j in amd_latest_build.get("jobs", [])
                    if j.get("type") == "script"
                    and not any(skip in j.get("name", "").lower() for skip in SKIP_JOB_PATTERNS)
                ]
                # Find jobs that are NOT terminal (scheduled, waiting, running, etc.)
                non_terminal_jobs = [
                    j for j in all_script_jobs
                    if j.get("state") not in cfg.TERMINAL_STATES
                ]
                # Normalized names already present in the parity report.
                # Check both exact names AND parity keys to avoid creating
                # phantom groups (e.g., "lm eval large models (h200)" when
                # the parity report already has "lm eval large models (h200-mi325)")

                existing_groups = {g["name"] for g in parity.get("job_groups", [])}
                existing_parity_keys = {_parity_key(g["name"]) for g in parity.get("job_groups", [])}
                existing_hw = {}
                for g in parity.get("job_groups", []):
                    existing_hw[g["name"]] = set(g.get("hardware") or [])

                scheduled_groups: dict[str, set] = {}  # norm -> set of HW
                for j in non_terminal_jobs:
                    norm = _normalize_job_name(j.get("name", ""))
                    if _is_parity_excluded_group(norm):
                        continue
                    hw = _extract_hardware(j.get("name", ""))
                    scheduled_groups.setdefault(norm, set()).add(hw)

                # Add entirely new groups that don't exist in parity yet.
                # A group "exists" if its exact name OR its parity key matches.
                for norm, hw_set in scheduled_groups.items():
                    pk = _parity_key(norm)
                    family_name = _parity_family_name(norm)
                    if norm not in existing_groups and pk not in existing_parity_keys:
                        parity["job_groups"].append({
                            "name": norm,
                            "family_key": pk,
                            "family_name": family_name,
                            "amd_job_name": None,
                            "upstream_job_name": None,
                            "amd": None,
                            "upstream": None,
                            "hardware": sorted(hw_set),
                            "hw_failures": None,
                            "hw_canceled": None,
                            "failure_tests": [],
                            "job_links": [],
                            "delta": None,
                            "status": "amd_only",
                            "backfilled": True,
                            "hw_backfilled": {hw: True for hw in hw_set},
                        })
                    else:
                        # Group exists but may be missing some HW — add scheduled HW as pending.
                        # Match by exact name first, then fall back to parity key so that
                        # multi-HW-tagged groups like (B200-MI355) find their sibling
                        # (B200-MI325) when the exact name doesn't exist.
                        target = None
                        for g in parity["job_groups"]:
                            if g["name"] == norm:
                                target = g
                                break
                        if target is None:
                            for g in parity["job_groups"]:
                                if _parity_key(g["name"]) == pk:
                                    target = g
                                    break
                        if target is not None:
                            current_hw = set(target.get("hardware") or [])
                            new_hw = hw_set - current_hw
                            if new_hw:
                                target["hardware"] = sorted(current_hw | new_hw)
                                hw_bf = target.get("hw_backfilled") or {}
                                for hw in new_hw:
                                    hw_bf[hw] = True
                                target["hw_backfilled"] = hw_bf

                if non_terminal_jobs:
                    log.info("  Added %d scheduled groups (%d new, %d extended) from %d non-terminal jobs",
                             len(scheduled_groups),
                             len(scheduled_groups) - len(scheduled_groups.keys() & existing_groups),
                             len(scheduled_groups.keys() & existing_groups),
                             len(non_terminal_jobs))

            # Also do the same for upstream
            up_latest_build = next(
                (b for b in all_builds.get("upstream", []) if b.get("number") == up_build_num),
                None,
            )
            if up_latest_build and not up_latest_build.get("jobs"):
                try:
                    up_latest_build = fetch_build_detail("upstream", up_build_num)
                except Exception:
                    pass
            if up_latest_build:
                up_all_script_jobs = [
                    j for j in up_latest_build.get("jobs", [])
                    if j.get("type") == "script"
                    and not any(skip in j.get("name", "").lower() for skip in SKIP_JOB_PATTERNS)
                ]
                up_non_terminal = [
                    j for j in up_all_script_jobs
                    if j.get("state") not in cfg.TERMINAL_STATES
                ]
                existing_groups = {g["name"] for g in parity.get("job_groups", [])}
                existing_pks = {_parity_key(g["name"]) for g in parity.get("job_groups", [])}
                for j in up_non_terminal:
                    norm = _normalize_job_name(j.get("name", ""))
                    if _is_parity_excluded_group(norm):
                        continue
                    hw = _extract_hardware(j.get("name", ""))
                    pk = _parity_key(norm)
                    family_name = _parity_family_name(norm)
                    if norm not in existing_groups and pk not in existing_pks:
                        parity["job_groups"].append({
                            "name": norm,
                            "family_key": pk,
                            "family_name": family_name,
                            "amd_job_name": None,
                            "upstream_job_name": None,
                            "amd": None,
                            "upstream": None,
                            "hardware": [hw],
                            "hw_failures": None,
                            "hw_canceled": None,
                            "failure_tests": [],
                            "job_links": [],
                            "delta": None,
                            "status": "upstream_only",
                            "backfilled": True,
                        })
                        existing_groups.add(norm)
                    else:
                        for g in parity["job_groups"]:
                            if g["name"] == norm:
                                current_hw = set(g.get("hardware") or [])
                                if hw not in current_hw:
                                    g["hardware"] = sorted(current_hw | {hw})
                                break

            parity["amd_build"] = amd_build_num
            parity["upstream_build"] = up_build_num

            # ── Validation: verify no false merges ──
            # Multiple hardware variants are expected to share one normalized
            # name. Only multiple raw names on the same hardware can indicate
            # an accidental merge, and only current-build rows participate in
            # the parity payload being validated here.
            false_merges = _find_false_normalization_merges(current_amd)
            if false_merges:
                log.warning(
                    "  VALIDATION: %d possible false merges detected! "
                    "These same-hardware groups absorb multiple raw jobs but "
                    "are NOT shard bases:",
                    len(false_merges),
                )
                for hw, norm, raws in false_merges[:5]:
                    log.warning("    [%s] '%s' <- %s", hw, norm, sorted(raws))

            # ── Validation: verify parity key doesn't drop groups ──
            from vllm.ci.analyzer import _parity_key
            lost = _find_missing_parity_groups(current_amd, parity)
            if lost:
                log.warning(
                    "  VALIDATION: %d AMD groups lost in parity matching! "
                    "Parity key collision may be dropping groups:",
                    len(lost),
                )
                for n in lost[:5]:
                    log.warning("    '%s' (parity_key='%s')", n, _parity_key(n))

            write_parity_report(parity, amd_date, up_date, output_dir)

    # Flaky tests
    if amd_health:
        write_flaky_tests(amd_health, output_dir)

    # Failure trends
    if amd_health:
        trends = compute_trends(
            [summary for summary in amd_summaries if summary.has_test_results],
            amd_health,
        )
        write_failure_trends(trends, output_dir)

    # YAML config parity (fetches from upstream GitHub)
    if not args.skip_config_parity:
        log.info("Running YAML config parity analysis (fetching from upstream)...")
        from vllm.config_parity import build_config_parity
        config_parity = build_config_parity()
        if "error" not in config_parity:
            config_parity_path = output_dir / "config_parity.json"
            config_parity_path.write_text(json.dumps(config_parity, indent=2))
            log.info(
                "Wrote config_parity.json (family coverage: %.1f%%, "
                "parity-node coverage: %.1f%%, avg similarity: %.1f%%)",
                config_parity.get("summary", {}).get(
                    "identity_family_coverage_rate_pct",
                    0,
                ),
                config_parity.get("summary", {}).get("coverage_rate_pct", 0),
                config_parity.get("summary", {}).get(
                    "covered_avg_command_similarity_pct",
                    0,
                ),
            )
        else:
            log.warning("Config parity failed: %s", config_parity["error"])

    # Prune old JSONL files
    prune_old_results(results_dir, max_days=cfg.HISTORY_DAYS)

    # Sync CI data to standard project-level files for compatibility
    # (CONTRIBUTING.md expects data/vllm/test_results.json and data/vllm/parity_report.json)
    project_dir = output_dir.parent  # data/vllm/
    latest_amd_signal = _latest_signal_summary(amd_summaries)
    if latest_amd_signal:
        latest_upstream_signal = _latest_signal_summary(upstream_summaries)
        test_results = _project_test_results_payload(
            latest_amd_signal,
            latest_upstream_signal,
        )
        tr_path = project_dir / "test_results.json"
        tr_path.write_text(json.dumps(test_results, indent=2))
        log.info(
            "Wrote %s (synced from CI data; pass rate uses pytest assertions, "
            "excluding skipped)",
            tr_path,
        )

    # Copy parity_report.json to project root for compatibility
    ci_parity = output_dir / "parity_report.json"
    proj_parity = project_dir / "parity_report.json"
    if ci_parity.exists():
        import shutil
        shutil.copy2(ci_parity, proj_parity)
        log.info("Synced parity_report.json to %s", proj_parity)

    # Print summary
    _print_summary(amd_summaries, upstream_summaries, amd_health)

    log.info("=== Done ===")


def _print_summary(
    amd_summaries: list,
    upstream_summaries: list,
    health_data: list,
):
    """Print a human-readable summary to stdout."""
    print("\n" + "=" * 60)
    print("CI DASHBOARD SUMMARY")
    print("=" * 60)

    if amd_summaries:
        pipeline_latest = amd_summaries[0]
        latest = _latest_signal_summary(amd_summaries) or pipeline_latest
        if pipeline_latest.build_number != latest.build_number:
            print(
                f"\nAMD Latest Pipeline Build (#{pipeline_latest.build_number}): "
                f"{pipeline_latest.state}; {pipeline_latest.test_jobs_blocked} "
                "test steps blocked before execution"
            )
        print(f"\nAMD Latest (Build #{latest.build_number}):")
        print(f"  Tests: {latest.total_tests} | Pass: {latest.passed} | Fail: {latest.failed} | Skip: {latest.skipped}")
        print(f"  Test Pass Rate (pytest assertions, skipped excluded): {latest.pass_rate:.1%}")
        print(f"  Jobs: {latest.job_count} ({latest.jobs_passed} passed, {latest.jobs_failed} failed)")
        if latest.delta_vs_previous:
            d = latest.delta_vs_previous
            print(f"  Delta: tests {d.get('total', 0):+d}, pass rate {d.get('pass_rate', 0):+.2%}")

    if upstream_summaries:
        latest = _latest_signal_summary(upstream_summaries) or upstream_summaries[0]
        print(f"\nUpstream Latest (Build #{latest.build_number}):")
        print(f"  Tests: {latest.total_tests} | Pass: {latest.passed} | Fail: {latest.failed} | Skip: {latest.skipped}")
        print(f"  Test Pass Rate (pytest assertions, skipped excluded): {latest.pass_rate:.1%}")

    if health_data:
        labels = {}
        for h in health_data:
            labels[h.label] = labels.get(h.label, 0) + 1
        print(f"\nTest Health ({len(health_data)} unique tests):")
        for label in ["passing", "failing", "new_failure", "fixed", "flaky", "skipped", "new_test", "quarantined", "allowlisted"]:
            count = labels.get(label, 0)
            if count > 0:
                print(f"  {label}: {count}")

    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
