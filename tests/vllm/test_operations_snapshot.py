"""Fixture-driven tests for the compact v2 operations snapshot."""

from __future__ import annotations

import json
from pathlib import Path

from vllm import build_operations_snapshot as ops
from vllm import collect_analytics as analytics


GENERATED_AT = "2026-04-22T12:00:00Z"


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload))


def _job(name: str, state: str, url: str, dur: float = 10.0, **extra) -> dict:
    return {
        "name": name,
        "raw_name": name,
        "state": state,
        "url": url,
        "dur": dur,
        "q": "amd_mi300_1",
        **extra,
    }


def _build(number: int, date: str, jobs: list[dict], pipeline: str = "amd-ci") -> dict:
    return {
        "number": number,
        "created_at": f"{date}T09:00:00Z",
        "state": "passed",
        "total_jobs": len(jobs),
        "jobs": jobs,
        "web_url": f"https://buildkite.com/vllm/{pipeline}/builds/{number}",
    }


def _fixture_data(tmp_path: Path) -> Path:
    previous = _build(102, "2026-04-21", [
        _job("Recurring", "soft_fail", "https://buildkite.com/vllm/amd-ci/builds/102/steps/recurring"),
        _job("Fixed", "failed", "https://buildkite.com/vllm/amd-ci/builds/102/steps/fixed"),
        _job("Mixed hard", "passed", "https://buildkite.com/vllm/amd-ci/builds/102/steps/mixed-hard"),
        _job("Mixed soft", "passed", "https://buildkite.com/vllm/amd-ci/builds/102/steps/mixed-soft"),
    ])
    latest = _build(103, "2026-04-22", [
        _job("Recurring", "failed", "https://buildkite.com/vllm/amd-ci/builds/103/steps/recurring", 31),
        _job("New hard", "failed", "https://buildkite.com/vllm/amd-ci/builds/103/steps/new-hard", 42),
        _job("New soft", "soft_fail", "https://buildkite.com/vllm/amd-ci/builds/103/steps/new-soft", 15),
        _job("Fixed", "passed", "https://buildkite.com/vllm/amd-ci/builds/103/steps/fixed", 8),
        _job(
            "Mixed hard",
            "failed",
            "https://buildkite.com/vllm/amd-ci/builds/103/steps/mixed-hard-failed",
            33,
            raw_name="mi300_1: Mixed hard",
            job_id="mixed-hard-failed",
            step_id="mixed-hard-step",
            retried=True,
            retried_in_job_id="mixed-hard-retry",
            retries_count=0,
            retry_source=None,
            retry_type=None,
            step_key="mixed-hard",
            tests=12,
            passed_tests=0,
            failed_tests=12,
            skipped_tests=0,
        ),
        _job(
            "Mixed hard",
            "passed",
            "https://buildkite.com/vllm/amd-ci/builds/103/steps/mixed-hard-retry",
            30,
            raw_name="mi300_1: Mixed hard",
            job_id="mixed-hard-retry",
            step_id="mixed-hard-step",
            retried=False,
            retried_in_job_id=None,
            retries_count=1,
            retry_source="manual",
            retry_type="manual",
            step_key="mixed-hard",
        ),
        _job("Mixed soft", "soft_fail", "https://buildkite.com/vllm/amd-ci/builds/103/steps/mixed-soft", 18),
    ])
    oldest = _build(101, "2026-04-20", [
        _job("Mixed soft", "skipped", "", 1),
    ])
    rankings = [
        {"name": "Mixed hard", "runs": 3, "passed": 2, "failed": 1, "soft_failed": 0, "fail_rate": 33.3},
        {"name": "Mixed soft", "runs": 3, "passed": 1, "failed": 0, "soft_failed": 1, "fail_rate": 33.3},
        {"name": "Always failing", "runs": 4, "passed": 0, "failed": 4, "soft_failed": 0, "fail_rate": 100.0},
        {"name": "Stable", "runs": 4, "passed": 4, "failed": 0, "soft_failed": 0, "fail_rate": 0.0},
    ]
    _write_json(tmp_path / "analytics.json", {
        "amd-ci": {
            "display_name": "AMD CI",
            "generated_at": "2026-04-22T10:00:00Z",
            "builds": [latest, previous, oldest],
            "failure_ranking": rankings,
            "duration_ranking": [
                {**rankings[0], "median_dur": 30, "p90_dur": 60, "max_dur": 70, "queues": ["amd_mi300_1"]},
                {**rankings[3], "median_dur": 10, "p90_dur": 12, "max_dur": 13, "queues": ["amd_mi300_1"]},
            ],
            "retry_analysis": {
                "summary": {
                    "builds_evaluated": 3,
                    "builds_with_retries": 1,
                    "retry_attempt_count": 1,
                    "failed_then_passed_recovery_count": 1,
                },
                "retry_attempts": [{
                    "build_number": 103,
                    "step": "mixed-hard",
                    "name": "mi300_1: Mixed hard",
                    "job_id": "mixed-hard-retry",
                }],
                "failed_then_passed_recoveries": [{
                    "build_number": 103,
                    "step": "mixed-hard",
                    "name": "mi300_1: Mixed hard",
                    "failed_job_id": "mixed-hard-failed",
                    "passed_job_id": "mixed-hard-retry",
                }],
            },
        },
        "ci": {
            "display_name": "Upstream CI",
            "generated_at": "2026-04-22T10:00:00Z",
            "builds": [_build(900, "2026-04-22", [], pipeline="ci")],
        },
    })
    _write_json(tmp_path / "ci_health.json", {
        "generated_at": "2026-04-22T10:01:00Z",
        "amd": {"builds": [{"build_number": 103, "created_at": latest["created_at"], "pass_rate": 0.9}]},
        "upstream": {"builds": []},
    })
    _write_json(tmp_path / "gating_targets.json", {
        "generated_at": "2026-04-22T10:02:00Z",
        "summary": {"target_group_count": 2, "by_target_signal": {"green": 1, "red": 1}},
        "groups": [{"id": 1, "label": "A"}, {"id": 2, "label": "B"}],
    })
    _write_json(tmp_path / "gating_target_candidates.json", {
        "generated_at": "2026-04-22T10:03:00Z",
        "summary": {"row_count": 3},
        "rows": [{}, {}, {}],
    })
    _write_json(tmp_path / "amd_test_matrix.json", {
        "generated_at": "2026-04-22T10:04:00Z",
        "summary": {"unique_groups": 2, "hardware_cells": 4, "passing_cells": 3, "failing_cells": 1},
        "rows": [{}, {}],
    })
    current_queue = {
        "ts": "2026-04-22T10:05:00Z",
        "total_waiting": 2,
        "total_running": 3,
        "total_zombie_waiting": 0,
        "total_zombie_running": 0,
        "queues": {
            "amd_mi300_1": {
                "waiting": 2,
                "running": 3,
                "p95_wait": 4.0,
                "waiting_by_workload": {"omni": 2},
                "running_by_workload": {"omni": 1},
            },
        },
        "sources": {"counts": "cluster_metrics", "waits": "scheduled_jobs"},
        "run_id": "current-run",
    }
    legacy_but_newer = {
        "ts": "2026-04-23T10:05:00Z",
        "total_waiting": 999,
        "total_running": 999,
        "queues": {},
    }
    (tmp_path / "queue_timeseries.jsonl").write_text(
        json.dumps(current_queue) + "\n" + json.dumps(legacy_but_newer) + "\n"
    )
    _write_json(tmp_path / "queue_jobs.json", {
        "ts": current_queue["ts"],
        "zombie_threshold_min": 240,
        "pending": [{
            "name": "Omni pending",
            "state": "scheduled",
            "workload": "omni",
            "source": "webhook",
            "analysis_excluded": False,
            "url": "https://buildkite.com/vllm/vllm-omni/builds/1/steps/pending",
        }],
        "running": [{
            "name": "Omni running",
            "state": "running",
            "workload": "omni",
            "source": "webhook",
            "analysis_excluded": False,
            "url": "https://buildkite.com/vllm/vllm-omni/builds/1/steps/running",
        }],
    })
    _write_json(tmp_path / "group_changes.json", {
        "generated_at": "2026-04-22T10:06:00Z",
        "days": 30,
        "total_changes": 1,
        "changes": [{"date": "2026-04-22", "message": "Add a group"}],
    })
    _write_json(tmp_path / "omni_surge_heuristic.json", {
        "generated_at": "2026-04-22T10:07:00Z",
        "healthy": 1,
        "trigger": 3,
        "dynamic_component": 3,
        "total_groups": 2,
    })
    _write_json(tmp_path / "open_omni_surge_issues.json", {
        "last_snapshot_ts": current_queue["ts"],
        "last_value": 2,
        "open": None,
    })
    return tmp_path


def test_v2_snapshot_transition_math_links_and_queue_provenance(tmp_path):
    payload = ops.build_snapshot(_fixture_data(tmp_path), generated_at=GENERATED_AT)

    assert payload["schema_version"] == 2
    assert payload["generated_at"] == GENERATED_AT
    assert payload["nightly"]["pipeline_order"] == ["amd-ci", "ci"]
    assert payload["nightly"]["pipelines"][0]["pipeline"] == "amd-ci"

    latest = payload["nightly"]["pipelines"][0]["builds"][0]
    assert [row["name"] for row in latest["failed_groups"]] == ["New hard", "Recurring", "mi300_1: Mixed hard"]
    assert [row["name"] for row in latest["soft_failed_groups"]] == ["Mixed soft", "New soft"]
    assert [row["name"] for row in latest["transitions"]["new"]] == [
        "Mixed soft",
        "New hard",
        "New soft",
        "mi300_1: Mixed hard",
    ]
    assert [row["name"] for row in latest["transitions"]["recurring"]] == ["Recurring"]
    assert [row["name"] for row in latest["transitions"]["fixed"]] == ["Fixed"]
    assert latest["transitions"]["preceding_build_number"] == 102
    new_hard = next(row for row in latest["transitions"]["new"] if row["name"] == "New hard")
    assert new_hard["url"].endswith("/steps/new-hard")
    assert latest["transitions"]["fixed"][0]["url"].endswith("/builds/102/steps/fixed")

    assert payload["queue"]["snapshot"]["run_id"] == "current-run"
    assert payload["queue"]["snapshot"]["total_waiting"] == 2
    assert payload["queue"]["provenance"]["snapshot"]["sources"]["counts"] == "cluster_metrics"
    assert payload["queue"]["provenance"]["jobs"]["source_counts"] == {"webhook": 2}
    assert payload["omni"]["status"] == "elevated"
    assert payload["omni"]["current"] == {
        "waiting": 2,
        "running": 1,
        "waiting_by_queue": {"amd_mi300_1": 2},
        "running_by_queue": {"amd_mi300_1": 1},
    }
    assert all("timestamp" in source for source in payload["sources"].values())


def test_reliability_only_marks_mixed_pass_failure_jobs_flaky(tmp_path):
    payload = ops.build_snapshot(_fixture_data(tmp_path), generated_at=GENERATED_AT)

    flaky = payload["reliability"]["flaky_candidates"]
    assert {row["name"] for row in flaky} == {"Mixed hard", "Mixed soft"}
    assert "Always failing" not in {row["name"] for row in flaky}
    assert "Stable" not in {row["name"] for row in flaky}
    assert {row["evidence_type"] for row in flaky} == {"mixed_outcome_history"}
    assert payload["reliability"]["latency_rankings"]["by_p90_duration"][0]["name"] == "Mixed hard"
    assert payload["gating"]["denominators"]["target_signal_counts"]["value"] == 2
    assert payload["gating"]["denominators"]["matrix_cell_states"]["value"] == 4


def test_mixed_outcome_candidates_include_each_nightly_observation(tmp_path):
    reliability = ops.build_snapshot(_fixture_data(tmp_path), generated_at=GENERATED_AT)["reliability"]
    candidates = {row["name"]: row for row in reliability["flaky_candidates"]}

    hard = candidates["Mixed hard"]
    assert hard["observation_count"] == hard["runs"] == 3
    assert hard["retry_evidence_observation_count"] == 2
    assert [row["state"] for row in hard["observations"]] == ["hard", "passed", "passed"]
    assert all(
        {"build_number", "build_url", "job_url", "state", "observed_at", "duration_mins"} <= row.keys()
        for row in hard["observations"]
    )
    failed = hard["observations"][0]
    assert failed["build_number"] == 103
    assert failed["build_url"] == "https://buildkite.com/vllm/amd-ci/builds/103"
    assert failed["job_url"].endswith("/steps/mixed-hard-failed")
    assert failed["observed_at"] == "2026-04-22T09:00:00Z"
    assert failed["duration_mins"] == 33
    assert failed["queue"] == "amd_mi300_1"
    assert failed["tests"] == 12
    assert failed["failed_tests"] == 12
    assert failed["retry_evidence"] == {
        "retried": True,
        "retried_in_job_id": "mixed-hard-retry",
        "retries_count": 0,
        "retry_source": None,
        "retry_type": None,
        "step_key": "mixed-hard",
        "job_id": "mixed-hard-failed",
        "step_id": "mixed-hard-step",
    }
    assert hard["observations"][1]["retry_evidence"] == {
        "retried": False,
        "retried_in_job_id": None,
        "retries_count": 1,
        "retry_source": "manual",
        "retry_type": "manual",
        "step_key": "mixed-hard",
        "job_id": "mixed-hard-retry",
        "step_id": "mixed-hard-step",
    }

    soft = candidates["Mixed soft"]
    assert soft["observation_count"] == soft["runs"] == 3
    assert [row["state"] for row in soft["observations"]] == ["soft", "passed", "unknown"]
    assert all("retry_evidence" not in row for row in soft["observations"])
    assert "job_url" not in soft["observations"][-1]

    assert reliability["retry_analysis"]["evidence_type"] == "explicit_retry_recovery"
    assert reliability["retry_analysis"]["summary"]["failed_then_passed_recovery_count"] == 1
    assert "not proof of a retry recovery" in reliability["evidence_definitions"]["mixed_outcome_history"]


def test_retry_analysis_and_collector_retry_fields(monkeypatch):
    raw_jobs = [
        {
            "type": "script",
            "id": "failed-job",
            "name": "mi300_1: Retry me",
            "state": "failed",
            "soft_failed": False,
            "retried": True,
            "retried_in_job_id": "passed-job",
            "retries_count": 0,
            "retry_source": None,
            "retry_type": None,
            "step_key": "retry-step",
            "step": {"id": "step-id"},
            "agent_query_rules": ["queue=amd_mi300_1"],
        },
        {
            "type": "script",
            "id": "passed-job",
            "name": "mi300_1: Retry me",
            "state": "passed",
            "soft_failed": False,
            "retried": False,
            "retried_in_job_id": None,
            "retries_count": 1,
            "retry_source": "manual",
            "retry_type": "manual",
            "step_key": "retry-step",
            "step": {"id": "step-id"},
            "agent_query_rules": ["queue=amd_mi300_1"],
        },
    ]
    monkeypatch.setattr(analytics, "bk_get", lambda path, token, params=None: [{
        "number": 77,
        "message": "AMD Full CI Run - nightly",
        "state": "passed",
        "created_at": "2026-04-22T09:00:00Z",
        "finished_at": "2026-04-22T10:00:00Z",
        "jobs": raw_jobs,
        "web_url": "https://buildkite.com/vllm/amd-ci/builds/77",
    }])

    builds = analytics.collect_pipeline("amd-ci", "token", 1)
    for key in analytics.RETRY_FIELDS:
        assert key in builds[0]["jobs"][0]
        assert key in builds[0]["jobs"][1]

    retry_analysis = analytics.compute_retry_analysis(builds)
    assert retry_analysis["summary"] == {
        "builds_evaluated": 1,
        "builds_with_retries": 1,
        "retry_attempt_count": 1,
        "failed_then_passed_recovery_count": 1,
    }
    assert retry_analysis["retry_attempts"][0]["job_id"] == "passed-job"
    recovery = retry_analysis["failed_then_passed_recoveries"][0]
    assert (recovery["build_number"], recovery["step"], recovery["name"]) == (
        77,
        "retry-step",
        "mi300_1: Retry me",
    )
    assert recovery["failed_job_id"] == "failed-job"
    assert recovery["passed_job_id"] == "passed-job"
