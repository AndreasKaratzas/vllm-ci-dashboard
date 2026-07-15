"""Contracts for nightlies that fail before any test command can run."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from collect_ci import _compute_pipeline_summaries, _latest_signal_summary  # noqa: E402
from vllm.ci.analyzer import compute_build_summary  # noqa: E402
from vllm.ci.models import TestResult  # noqa: E402
from vllm.ci.reporter import write_ci_health  # noqa: E402
from vllm.pipelines import SKIP_JOB_PATTERNS  # noqa: E402


def _job(name: str, state: str, step: str) -> dict:
    return {
        "type": "script",
        "name": name,
        "state": state,
        "step_key": step,
    }


def _build(number: int, created_at: str, state: str, jobs: list[dict]) -> dict:
    return {
        "number": number,
        "created_at": created_at,
        "finished_at": created_at,
        "state": state,
        "branch": "main",
        "web_url": f"https://buildkite.com/vllm/amd-ci/builds/{number}",
        "jobs": jobs,
    }


def _result(build_number: int) -> TestResult:
    return TestResult(
        test_id="group::__passed__",
        name="__passed__ (1)",
        classname="group",
        status="passed",
        duration_secs=1,
        failure_message="",
        job_name="mi300_1: Group",
        job_id="job",
        step_id="step",
        build_number=build_number,
        pipeline="amd-ci",
        date="2026-07-14",
    )


def test_build_summary_counts_dependency_blocked_test_steps_only():
    build = _build(10880, "2026-07-15T09:00:00Z", "failed", [
        _job("AMD: Docker build test image and artifacts", "failed", "image"),
        _job("mi300_1: Group A", "waiting_failed", "group-a"),
        _job("mi300_1: Group B shard 0", "waiting_failed", "group-b"),
        _job("mi300_1: Group B shard 1", "waiting_failed", "group-b"),
    ])

    summary = compute_build_summary(
        build,
        [],
        "amd",
        skip_job_patterns=SKIP_JOB_PATTERNS,
    )

    assert summary.job_count == 3
    assert summary.test_job_count == 2
    assert summary.test_jobs_blocked == 2
    assert summary.has_test_results is False


def test_pipeline_summary_keeps_blocked_nightly_separate_from_test_signal(tmp_path):
    signal_build = _build(
        10836,
        "2026-07-14T09:00:00Z",
        "passed",
        [_job("mi300_1: Group", "passed", "group")],
    )
    blocked_build = _build(
        10880,
        "2026-07-15T09:00:00Z",
        "failed",
        [_job("mi300_1: Group", "waiting_failed", "group")],
    )
    summaries = _compute_pipeline_summaries(
        "amd",
        [(10836, "2026-07-14", [_result(10836)])],
        [blocked_build, signal_build],
    )

    assert [summary.build_number for summary in summaries[:2]] == [10880, 10836]
    assert summaries[0].test_jobs_blocked == 1
    assert _latest_signal_summary(summaries).build_number == 10836

    write_ci_health(summaries, [], [], tmp_path)
    health = json.loads((tmp_path / "ci_health.json").read_text())
    amd = health["amd"]
    assert amd["latest_pipeline_build"]["build_number"] == 10880
    assert amd["latest_pipeline_build_has_test_results"] is False
    assert amd["latest_build"]["build_number"] == 10836
    assert amd["latest_test_signal_build"]["build_number"] == 10836
