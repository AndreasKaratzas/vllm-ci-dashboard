#!/usr/bin/env python3
"""Poll registered test builds, fetch results, compare against nightlies.

Reads ``data/vllm/ci/test_builds/index.json`` and for each non-terminal entry:
    1. Refresh state from Buildkite.
    2. Once terminal, parse test results from job logs (same pipeline as the
       nightly collector).
    3. Save per-build JSONL under ``data/vllm/ci/test_builds/<id>/results.jsonl``.
    4. Compute a comparison against the matching nightly (or the most recent
       prior nightly, if the build commit predates today's).
    5. Write ``comparison.json`` with common/new pass/fail counts and per-group
       timing deltas.

Safe to run every 30 minutes; terminal builds with a computed comparison are
skipped.
"""

from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from vllm.ci import buildkite_client, config as cfg  # noqa: E402
from vllm.ci.log_parser import parse_job_results  # noqa: E402
from vllm.ci.models import TestResult  # noqa: E402
from vllm.constants import BK_ORG  # noqa: E402
from vllm.pipelines import SKIP_JOB_PATTERNS  # noqa: E402

log = logging.getLogger("collect_test_builds")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

PIPELINE_SLUG = "amd-ci"
PIPELINE_KEY = "amd"  # registry key in PIPELINES dict for read helpers
REGISTRY_DIR = REPO_ROOT / "data" / "vllm" / "ci" / "test_builds"
REGISTRY_FILE = REGISTRY_DIR / "index.json"
NIGHTLY_RESULTS_DIR = REPO_ROOT / "data" / "vllm" / "ci" / "test_results"


def _load_registry() -> list[dict]:
    if not REGISTRY_FILE.exists():
        return []
    try:
        data = json.loads(REGISTRY_FILE.read_text())
        return data if isinstance(data, list) else []
    except Exception as e:
        log.warning("Registry parse failed: %s", e)
        return []


def _save_registry(rows: list[dict]) -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")


def _commit_date(iso_str: str) -> str:
    if not iso_str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return iso_str[:10]


def _find_nightly_results(commit_date: str) -> tuple[str, list[TestResult]]:
    """Return (resolved_date, results) — falling back to the most recent prior
    AMD nightly if there's no file for ``commit_date``."""
    candidates = sorted(NIGHTLY_RESULTS_DIR.glob("*_amd.jsonl"))
    if not candidates:
        return "", []
    target = f"{commit_date}_amd.jsonl"
    chosen: Path | None = None
    for p in candidates:
        if p.name == target:
            chosen = p
            break
    if chosen is None:
        # Take the most recent nightly on or before commit_date.
        earlier = [p for p in candidates if p.name.split("_")[0] <= commit_date]
        if not earlier:
            return "", []
        chosen = earlier[-1]
    results: list[TestResult] = []
    with open(chosen) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                d.setdefault("step_id", "")
                results.append(TestResult(**d))
            except Exception:
                continue
    return chosen.stem.split("_")[0], results


def _status_bucket(status: str) -> str:
    s = (status or "").lower()
    if s in ("passed", "xpassed"):
        return "pass"
    if s in ("failed", "error", "broken", "timed_out", "xfailed"):
        return "fail"
    return "other"


def _group_name(t: TestResult) -> str:
    # Group by job_name minus the hardware prefix ("mi250_1: X" → "X").
    name = t.job_name or t.classname or ""
    if ": " in name:
        name = name.split(": ", 1)[1]
    return name.strip() or "unknown"


def _compare(
    test_results: list[TestResult],
    nightly_results: list[TestResult],
) -> dict:
    """Compare test build results against nightly baseline.

    Keyed by ``test_id`` (classname::name) — same scheme used everywhere else.
    Returns per-test categorization plus per-group pass/fail totals and
    duration deltas so the dashboard can show a summary table.
    """
    cur = {t.test_id: t for t in test_results}
    base = {t.test_id: t for t in nightly_results}

    common_pass: list[str] = []
    common_fail: list[str] = []
    new_fail: list[str] = []
    new_pass: list[str] = []  # passed here but failed on nightly (regressions fixed)
    only_in_test: list[str] = []
    only_in_nightly: list[str] = []

    for tid, t in cur.items():
        bucket_cur = _status_bucket(t.status)
        if tid not in base:
            only_in_test.append(tid)
            continue
        bucket_base = _status_bucket(base[tid].status)
        if bucket_cur == "pass" and bucket_base == "pass":
            common_pass.append(tid)
        elif bucket_cur == "fail" and bucket_base == "fail":
            common_fail.append(tid)
        elif bucket_cur == "fail" and bucket_base == "pass":
            new_fail.append(tid)
        elif bucket_cur == "pass" and bucket_base == "fail":
            new_pass.append(tid)

    for tid in base:
        if tid not in cur:
            only_in_nightly.append(tid)

    # Per-group breakdown.
    group_stats: dict[str, dict] = defaultdict(
        lambda: {
            "test_pass": 0, "test_fail": 0, "test_total": 0,
            "nightly_pass": 0, "nightly_fail": 0, "nightly_total": 0,
            "test_duration": 0.0, "nightly_duration": 0.0,
            "new_fail": 0, "new_pass": 0,
        }
    )
    for t in test_results:
        g = _group_name(t)
        s = _status_bucket(t.status)
        group_stats[g]["test_total"] += 1
        group_stats[g]["test_duration"] += t.duration_secs or 0.0
        if s == "pass":
            group_stats[g]["test_pass"] += 1
        elif s == "fail":
            group_stats[g]["test_fail"] += 1
    for t in nightly_results:
        g = _group_name(t)
        s = _status_bucket(t.status)
        group_stats[g]["nightly_total"] += 1
        group_stats[g]["nightly_duration"] += t.duration_secs or 0.0
        if s == "pass":
            group_stats[g]["nightly_pass"] += 1
        elif s == "fail":
            group_stats[g]["nightly_fail"] += 1

    new_fail_set = set(new_fail)
    new_pass_set = set(new_pass)
    for tid in new_fail_set:
        group_stats[_group_name(cur[tid])]["new_fail"] += 1
    for tid in new_pass_set:
        group_stats[_group_name(cur[tid])]["new_pass"] += 1

    groups_list = []
    for g, stats in sorted(group_stats.items()):
        entry = {"group": g, **stats}
        entry["duration_delta"] = stats["test_duration"] - stats["nightly_duration"]
        groups_list.append(entry)

    return {
        "summary": {
            "common_pass": len(common_pass),
            "common_fail": len(common_fail),
            "new_fail": len(new_fail),
            "new_pass": len(new_pass),
            "only_in_test": len(only_in_test),
            "only_in_nightly": len(only_in_nightly),
            "test_total": len(cur),
            "nightly_total": len(base),
        },
        "new_fail_tests": sorted(new_fail)[:200],
        "new_pass_tests": sorted(new_pass)[:200],
        "common_fail_tests": sorted(common_fail)[:200],
        "groups": groups_list,
    }


def _collect_build_results(build_number: int) -> list[TestResult]:
    """Fetch and parse all test results for a single build."""
    build = buildkite_client.fetch_build_detail(PIPELINE_KEY, build_number)
    jobs = buildkite_client.fetch_build_jobs(build)
    test_jobs = [
        j for j in jobs
        if not any(skip in j.get("name", "").lower() for skip in SKIP_JOB_PATTERNS)
    ]
    created = build.get("created_at", "") or ""
    date = created[:10] if created else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _parse(job):
        return parse_job_results(job, build_number, PIPELINE_SLUG, date)

    results: list[TestResult] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_parse, j) for j in test_jobs]
        for fut in as_completed(futures):
            results.extend(fut.result())
    return results


def _save_results(entry_id: str, results: list[TestResult]) -> Path:
    build_dir = REGISTRY_DIR / entry_id
    build_dir.mkdir(parents=True, exist_ok=True)
    out = build_dir / "results.jsonl"
    with open(out, "w") as fh:
        for r in results:
            fh.write(json.dumps(r.to_dict()) + "\n")
    return out


def _save_comparison(entry_id: str, comparison: dict) -> None:
    build_dir = REGISTRY_DIR / entry_id
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "comparison.json").write_text(json.dumps(comparison, indent=2) + "\n")


def _should_cleanup(cleanup_mode: str, state: str) -> bool:
    m = (cleanup_mode or "").lower()
    if m == "always":
        return state in cfg.TERMINAL_STATES
    if m == "on_success":
        return state == "passed"
    return False


def main() -> int:
    from vllm.pipelines import PIPELINES as VLLM_PIPELINES
    cfg.configure(BK_ORG, VLLM_PIPELINES)

    rows = _load_registry()
    if not rows:
        log.info("No registered test builds — nothing to do.")
        return 0

    changed = False
    for entry in rows:
        eid = entry.get("id") or f"{PIPELINE_SLUG}-{entry.get('build_number')}"
        bn = entry.get("build_number")
        if not bn:
            continue

        prev_state = entry.get("state", "")
        already_done = entry.get("results_fetched") and entry.get("comparison") is not None

        # Always refresh state for non-terminal builds; refresh terminal builds
        # only if we haven't captured results yet.
        if already_done and prev_state in cfg.TERMINAL_STATES:
            continue

        try:
            build = buildkite_client.fetch_build_detail(PIPELINE_KEY, bn)
        except Exception as e:
            log.warning("Failed to fetch build #%s: %s", bn, e)
            continue

        state = build.get("state", prev_state)
        entry["state"] = state
        entry["web_url"] = build.get("web_url", entry.get("web_url", ""))
        entry["finished_at"] = build.get("finished_at") or entry.get("finished_at")
        changed = True

        if state not in cfg.TERMINAL_STATES:
            log.info("#%s still %s — skipping results", bn, state)
            continue

        if not entry.get("results_fetched"):
            log.info("#%s terminal (%s) — parsing results", bn, state)
            try:
                results = _collect_build_results(bn)
            except Exception as e:
                log.error("Failed to parse results for #%s: %s", bn, e)
                continue
            _save_results(eid, results)
            entry["results_fetched"] = True
            entry["test_total"] = len(results)

            commit_date = _commit_date(build.get("created_at") or entry.get("created_at"))
            baseline_date, nightly = _find_nightly_results(commit_date)
            comparison = _compare(results, nightly)
            comparison["baseline_date"] = baseline_date
            comparison["build_date"] = commit_date
            _save_comparison(eid, comparison)
            entry["comparison"] = comparison["summary"]
            entry["baseline_date"] = baseline_date

        if _should_cleanup(entry.get("cleanup_mode", "never"), state) and entry.get("fork_repo"):
            # Server-side cleanup would need a PAT for the fork, which we don't
            # have. Flag it so the dashboard tab can do the DELETE from the
            # browser on next load when the user's PAT is available.
            entry["pending_cleanup"] = True

    if changed:
        _save_registry(rows)
        log.info("Registry updated (%d entries).", len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
