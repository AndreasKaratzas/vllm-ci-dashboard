"""Contracts for nightlies that fail before any test command can run."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from collect_ci import (  # noqa: E402
    _compute_pipeline_summaries,
    _latest_signal_summary,
    _project_test_result_summary,
    _project_test_results_payload,
)
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


def test_build_summary_counts_skip_only_groups_as_observed():
    build = _build(
        11005,
        "2026-07-18T09:00:00Z",
        "passed",
        [_job("mi300_2: Expected Failure Group", "passed", "expected-failure")],
    )
    result = TestResult(
        test_id="group::__xfailed__",
        name="__xfailed__ (1)",
        classname="group",
        status="xfailed",
        duration_secs=0,
        failure_message="",
        job_name="mi300_2: Expected Failure Group",
        job_id="job",
        step_id="step",
        build_number=11005,
        pipeline="amd-ci",
        date="2026-07-18",
    )

    summary = compute_build_summary(build, [result], "amd")

    assert summary.unique_test_groups == 1
    assert summary.test_groups_passing_or == 0
    assert summary.test_groups_passing_all == 0
    assert summary.test_groups_partial == 0


def test_build_summary_test_pass_rate_excludes_skipped_assertions():
    build = _build(
        11006,
        "2026-07-19T09:00:00Z",
        "failed",
        [_job("mi300_2: Assertion Group", "failed", "assertions")],
    )
    passed = _result(11006)
    passed.name = "__passed__ (7)"
    failed = _result(11006)
    failed.test_id = "group::__failed__"
    failed.name = "__failed__ (1)"
    failed.status = "failed"
    skipped = _result(11006)
    skipped.test_id = "group::__skipped__"
    skipped.name = "__skipped__ (2)"
    skipped.status = "skipped"

    summary = compute_build_summary(build, [passed, failed, skipped], "amd")
    serialized = summary.to_dict()

    assert (summary.passed, summary.failed, summary.skipped) == (7, 1, 2)
    assert summary.pass_rate == 0.875
    assert serialized["pass_rate"] == 0.875
    assert serialized["test_pass_rate_pct"] == 87.5
    assert serialized["test_pass_rate_basis"] == "pytest_assertions_excluding_skipped"


def test_root_test_results_separates_assertion_rate_from_legacy_job_counts():
    summary = compute_build_summary(
        _build(
            11007,
            "2026-07-20T09:00:00Z",
            "failed",
            [
                _job("mi300_2: Passing Step", "passed", "pass-step"),
                _job("mi300_2: Failing Step", "failed", "fail-step"),
            ],
        ),
        [],
        "amd",
    )
    summary.total_tests = 10
    summary.passed = 7
    summary.failed = 1
    summary.skipped = 2
    summary.pass_rate = 0.875

    root_summary = _project_test_result_summary(summary)

    assert root_summary["passed"] == 1
    assert root_summary["failed"] == 1
    assert root_summary["pass_rate"] == 87.5
    assert root_summary["test_pass_rate_pct"] == 87.5
    assert root_summary["test_pass_rate_basis"] == (
        "pytest_assertions_excluding_skipped"
    )
    assert root_summary["test_assertions"] == {
        "total": 10,
        "passed": 7,
        "failed": 1,
        "skipped": 2,
    }

    payload = _project_test_results_payload(
        summary,
        collected_at="2026-07-20T12:00:00Z",
    )
    assert payload["pass_rate_contract_version"] == 1
    assert payload["rocm"]["summary"] == root_summary


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
