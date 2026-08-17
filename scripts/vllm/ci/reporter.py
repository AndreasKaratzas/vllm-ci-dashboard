"""JSON/JSONL output generation for the CI dashboard."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    PASS_RATE_CONTRACT_VERSION,
    BuildSummary,
    TestHealth,
    TestResult,
)

log = logging.getLogger(__name__)

HEALTH_LABEL_BUCKETS = (
    "passing",
    "failing",
    "new_failure",
    "fixed",
    "flaky",
    "skipped",
    "new_test",
    "quarantined",
    "allowlisted",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Per-build test results (JSONL)
# ---------------------------------------------------------------------------

def write_test_results(
    results: list[TestResult],
    date: str,
    pipeline_key: str,
    output_dir: Path,
) -> Path:
    """Write per-test results as JSONL (one JSON object per line).

    Args:
        results: List of TestResult objects
        date: ISO date string (e.g. "2026-03-22")
        pipeline_key: "amd" or "upstream"
        output_dir: Directory to write into (e.g. data/vllm/ci/test_results/)

    Returns:
        Path to the written file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{date}_{pipeline_key}.jsonl"

    with open(path, "w") as f:
        for r in results:
            f.write(json.dumps(r.to_dict(), separators=(",", ":")) + "\n")

    log.info("Wrote %d test results to %s", len(results), path)
    return path


# ---------------------------------------------------------------------------
# CI health summary
# ---------------------------------------------------------------------------

def write_ci_health(
    amd_summaries: list[BuildSummary],
    upstream_summaries: list[BuildSummary],
    health_data: list[TestHealth],
    output_dir: Path,
) -> Path:
    """Write ci_health.json with overall dashboard data.

    Args:
        amd_summaries: AMD build summaries, newest-first
        upstream_summaries: Upstream build summaries, newest-first
        health_data: All test health labels
        output_dir: Output directory

    Returns:
        Path to the written file.
    """
    # Keep the output schema stable even when a bucket has no current tests.
    label_counts = {label: 0 for label in HEALTH_LABEL_BUCKETS}
    for h in health_data:
        label_counts[h.label] = label_counts.get(h.label, 0) + 1

    # Determine overall health direction
    def _health_direction(summaries: list[BuildSummary]) -> str:
        summaries = [summary for summary in summaries if summary.has_test_results]
        if len(summaries) < 3:
            return "unknown"
        recent = summaries[:3]
        older = summaries[3:6] if len(summaries) >= 6 else summaries[3:]
        if not older:
            return "stable"
        recent_avg = sum(s.pass_rate for s in recent) / len(recent)
        older_avg = sum(s.pass_rate for s in older) / len(older)
        diff = recent_avg - older_avg
        if diff > 0.02:
            return "improving"
        elif diff < -0.02:
            return "degrading"
        return "stable"

    def _build_section(summaries: list[BuildSummary]) -> dict:
        if not summaries:
            return {
                "latest_build": None,
                "latest_test_signal_build": None,
                "latest_pipeline_build": None,
                "builds": [],
                "trend": "unknown",
            }
        signal_summaries = [summary for summary in summaries if summary.has_test_results]
        latest_signal = signal_summaries[0] if signal_summaries else None
        latest_pipeline = summaries[0]
        return {
            # ``latest_build`` is retained as the test-evidence build for
            # compatibility with parity, matrix, and raw-JSONL audits.
            "latest_build": latest_signal.to_dict() if latest_signal else None,
            "latest_test_signal_build": latest_signal.to_dict() if latest_signal else None,
            "latest_pipeline_build": latest_pipeline.to_dict(),
            "latest_pipeline_build_has_test_results": latest_pipeline.has_test_results,
            "builds": [s.to_dict() for s in summaries],
            "trend": _health_direction(summaries),
        }

    data = {
        "pass_rate_contract_version": PASS_RATE_CONTRACT_VERSION,
        "generated_at": _now_iso(),
        "amd": _build_section(amd_summaries),
        "upstream": _build_section(upstream_summaries),
        "overall_health": _health_direction(amd_summaries),
        "test_counts": label_counts,
        "total_unique_tests": len(health_data),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "ci_health.json"
    path.write_text(json.dumps(data, indent=2))
    log.info("Wrote ci_health.json")
    return path


# ---------------------------------------------------------------------------
# Parity report
# ---------------------------------------------------------------------------

def write_parity_report(
    parity_data: dict,
    amd_date: str,
    upstream_date: str,
    output_dir: Path,
) -> Path:
    """Write parity_report.json."""
    report = {
        "generated_at": _now_iso(),
        "amd_date": amd_date,
        "upstream_date": upstream_date,
        **parity_data,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "parity_report.json"
    path.write_text(json.dumps(report, indent=2))
    log.info("Wrote parity_report.json (parity: %.1f%%)", parity_data.get("parity_pct", 0))
    return path


# ---------------------------------------------------------------------------
# Flaky tests
# ---------------------------------------------------------------------------

def write_flaky_tests(
    health_data: list[TestHealth],
    output_dir: Path,
) -> Path:
    """Write flaky_tests.json with all tests labeled as flaky."""
    flaky = [h for h in health_data if h.label == "flaky"]
    flaky.sort(key=lambda h: h.pass_rate)

    data = {
        "generated_at": _now_iso(),
        "window_builds": len(health_data[0].history) if health_data else 0,
        "total_flaky": len(flaky),
        "tests": [h.to_dict() for h in flaky],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "flaky_tests.json"
    path.write_text(json.dumps(data, indent=2))
    log.info("Wrote flaky_tests.json (%d flaky tests)", len(flaky))
    return path


# ---------------------------------------------------------------------------
# Failure trends
# ---------------------------------------------------------------------------

def write_failure_trends(
    trends_data: dict,
    output_dir: Path,
) -> Path:
    """Write failure_trends.json."""
    report = {
        "generated_at": _now_iso(),
        **trends_data,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "failure_trends.json"
    path.write_text(json.dumps(report, indent=2))
    log.info(
        "Wrote failure_trends.json (%d top offenders, %d new failures, %d fixed)",
        len(trends_data.get("top_offenders", [])),
        len(trends_data.get("new_failures", [])),
        len(trends_data.get("recently_fixed", [])),
    )
    return path


# ---------------------------------------------------------------------------
# Quarantine report
# ---------------------------------------------------------------------------

def write_quarantine_report(
    quarantine_report: dict,
    output_dir: Path,
) -> Path:
    """Write quarantine.json."""
    report = {
        "generated_at": _now_iso(),
        **quarantine_report,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "quarantine.json"
    path.write_text(json.dumps(report, indent=2))
    log.info("Wrote quarantine.json")
    return path


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def prune_old_results(results_dir: Path, max_days: int = 30):
    """Remove JSONL files older than max_days."""
    if not results_dir.exists():
        return

    cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from datetime import timedelta
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=max_days)).strftime("%Y-%m-%d")

    removed = 0
    for f in results_dir.glob("*.jsonl"):
        # Filename format: YYYY-MM-DD_pipeline.jsonl
        date_part = f.stem.rsplit("_", 1)[0]
        if date_part < cutoff_date:
            f.unlink()
            removed += 1

    if removed:
        log.info("Pruned %d JSONL files older than %d days", removed, max_days)
