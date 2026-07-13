"""Fixture-driven tests for the compact v2 operations snapshot."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
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
        "finished_at": f"{date}T10:00:00Z",
        "branch": "main",
        "state": "passed",
        "total_jobs": len(jobs),
        "jobs": jobs,
        "web_url": f"https://buildkite.com/vllm/{pipeline}/builds/{number}",
    }


def _retarget_build(build: dict, pipeline: str) -> dict:
    row = json.loads(json.dumps(build))
    row["web_url"] = f"https://buildkite.com/vllm/{pipeline}/builds/{row['number']}"
    row["message"] = "nightly"
    for job in row.get("jobs") or []:
        if job.get("url"):
            job["url"] = str(job["url"]).replace("/amd-ci/", f"/{pipeline}/")
        job_id = job.get("job_id") or str(job.get("url") or "").rstrip("/").split("/")[-1]
        job["id"] = job_id
        job["job_id"] = job_id
        job["type"] = "script"
        job["step"] = {
            "id": job.get("step_id") or f"step-{job_id}",
            "key": job.get("step_key") or job_id,
        }
        job["test_duration_mins"] = job.get("dur")
        job["q"] = "gpu_1_queue"
    return row


def _fixture_data(tmp_path: Path) -> Path:
    previous = _build(102, "2026-04-21", [
        _job("Recurring", "soft_fail", "https://buildkite.com/vllm/amd-ci/builds/102/steps/recurring"),
        _job("Fixed", "failed", "https://buildkite.com/vllm/amd-ci/builds/102/steps/fixed"),
        _job(
            "Mixed hard",
            "passed",
            "https://buildkite.com/vllm/amd-ci/builds/102/steps/mixed-hard",
            raw_name="mi300_1: Mixed hard",
        ),
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
    retry_analysis = {
        "available": True,
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
            "url": "https://buildkite.com/vllm/ci/builds/103/steps/canvas?jid=mixed-hard-retry",
        }],
        "failed_then_passed_recoveries": [{
            "build_number": 103,
            "step": "mixed-hard",
            "name": "mi300_1: Mixed hard",
            "failed_job_id": "mixed-hard-failed",
            "passed_job_id": "mixed-hard-retry",
            "failed_url": "https://buildkite.com/vllm/ci/builds/103/steps/canvas?jid=mixed-hard-failed",
            "passed_url": "https://buildkite.com/vllm/ci/builds/103/steps/canvas?jid=mixed-hard-retry",
        }],
        "provenance": {
            "source_pipeline": "ci",
            "complete": True,
            "cohort_build_numbers": [101, 102, 103],
        },
    }
    amd_main_builds = [latest, previous, oldest]
    upstream_main_builds = [
        _retarget_build(latest, "ci"),
        _retarget_build(previous, "ci"),
        _retarget_build(oldest, "ci"),
    ]
    upstream_reliability = analytics.build_all_main_reliability(
        upstream_main_builds,
        pipeline_slug="ci",
        window_days=30,
        generated_at=GENERATED_AT,
        nightly_pattern="nightly",
        test_result_builds=upstream_main_builds,
        collection_provenance={
            "created_from": "2026-03-23T12:00:00Z",
            "pages_fetched": 1,
            "termination_reason": "short_page",
            "exhaustive": True,
        },
    )
    _write_json(tmp_path / "analytics.json", {
        "amd-ci": {
            "display_name": "AMD CI",
            "generated_at": "2026-04-22T10:00:00Z",
            "builds": amd_main_builds,
            "failure_ranking": rankings,
            "duration_ranking": [
                {**rankings[0], "median_dur": 30, "p90_dur": 60, "max_dur": 70, "queues": ["amd_mi300_1"]},
                {**rankings[3], "median_dur": 10, "p90_dur": 12, "max_dur": 13, "queues": ["amd_mi300_1"]},
            ],
            "retry_analysis": retry_analysis,
        },
        "ci": {
            "display_name": "Upstream CI",
            "generated_at": "2026-04-22T10:00:00Z",
            "builds": upstream_main_builds,
            "all_main_reliability": upstream_reliability,
            "main_retry_analysis": retry_analysis,
            "retry_analysis": retry_analysis,
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
        "groups": [{"id": 1, "label": "Fixed"}, {"id": 2, "label": "Mixed soft"}],
    })
    _write_json(tmp_path / "gating_target_candidates.json", {
        "generated_at": "2026-04-22T10:03:00Z",
        "summary": {"row_count": 3},
        "rows": [
            {
                "target_id": 1,
                "label": "Fixed",
                "state": "passed",
                "url": "https://buildkite.com/vllm/ci/builds/103/steps/fixed",
            },
            {
                "target_id": 2,
                "label": "Mixed soft",
                "state": "soft_fail",
                "url": "https://buildkite.com/vllm/ci/builds/103/steps/mixed-soft",
            },
            {},
        ],
    })
    _write_json(tmp_path / "amd_test_matrix.json", {
        "generated_at": "2026-04-22T10:04:00Z",
        "summary": {"unique_groups": 2, "hardware_cells": 4, "passing_cells": 3, "failing_cells": 1},
        "rows": [
            {
                "canonical_title": "Fixed",
                "cells": {"mi300": {
                    "exists": True,
                    "latest_state": "failed",
                    "latest_build_number": 103,
                    "latest_url": "https://buildkite.com/vllm/amd-ci/builds/103/steps/fixed",
                }},
            },
            {
                "canonical_title": "Mixed soft",
                "cells": {"mi300": {
                    "exists": True,
                    "latest_state": "passed",
                    "latest_build_number": 103,
                    "latest_url": "https://buildkite.com/vllm/amd-ci/builds/103/steps/mixed-soft",
                }},
            },
        ],
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
    assert payload["queue"]["history_summary"]["source_path"] == "queue_timeseries.jsonl"
    assert payload["queue"]["provenance"]["source_paths"] == {
        "history": "queue_timeseries.jsonl",
        "jobs": "queue_jobs.json",
    }
    assert payload["omni"]["status"] == "elevated"
    assert payload["omni"]["current"] == {
        "waiting": 2,
        "running": 1,
        "waiting_by_queue": {"amd_mi300_1": 2},
        "running_by_queue": {"amd_mi300_1": 1},
    }
    assert payload["omni"]["provenance"]["source_paths"] == {
        "queue_aggregates": "queue_timeseries.jsonl",
        "queue_jobs": "queue_jobs.json",
        "heuristic": "omni_surge_heuristic.json",
        "issue_state": "open_omni_surge_issues.json",
    }
    assert payload["trajectory"]["provenance"]["source_paths"] == {
        "build_history": "analytics.json",
        "group_changes": "group_changes.json",
    }
    assert payload["trajectory"]["source_pipeline"] == "ci"
    assert payload["trajectory"]["pipeline_order"] == ["ci"]
    assert [row["pipeline"] for row in payload["trajectory"]["pipelines"]] == ["ci"]
    assert payload["trajectory"]["pipelines"][0]["source_key"] == "ci.all_main_reliability"
    assert all("timestamp" in source for source in payload["sources"].values())


def test_reliability_only_marks_mixed_pass_failure_jobs_flaky(tmp_path):
    payload = ops.build_snapshot(_fixture_data(tmp_path), generated_at=GENERATED_AT)

    flaky = payload["reliability"]["flaky_candidates"]
    assert payload["reliability"]["source_pipeline"] == "ci"
    assert "amd_reliability" not in payload
    assert {row["name"] for row in flaky} == {"Fixed", "Mixed hard", "Mixed soft"}
    assert "Always failing" not in {row["name"] for row in flaky}
    assert "Stable" not in {row["name"] for row in flaky}
    assert {row["evidence_type"] for row in flaky} == {"mixed_outcome_history"}
    assert payload["reliability"]["latency_rankings"]["by_p90_duration"][0]["name"] == "New hard"
    assert payload["reliability"]["denominator"]["unit"] == (
        "terminal ci branch=main job observations"
    )
    assert payload["reliability"]["denominator"]["unknown_observations_excluded"] == 1
    assert payload["gating"]["denominators"]["target_signal_counts"]["value"] == 2
    assert payload["gating"]["denominators"]["matrix_cell_states"]["value"] == 4
    assert all("owner" not in row for row in payload["gating"]["active_target_groups"])

    gating = {row["label"]: row for row in payload["gating"]["active_target_groups"]}
    fixed = gating["Fixed"]
    assert fixed["latest_amd_result"]["state"] == "hard"
    assert fixed["latest_amd_result"]["source_pipeline"] == "amd-ci"
    assert fixed["latest_amd_result"]["evidence"][0]["url"].startswith(
        "https://buildkite.com/vllm/amd-ci/"
    )
    assert fixed["main_reliability"]["source_pipeline"] == "ci"
    assert fixed["main_reliability"]["latest_url"].startswith(
        "https://buildkite.com/vllm/ci/"
    )
    assert fixed["nightly_green_streak"] == 1
    assert fixed["last_incident"]["source_pipeline"] == "ci"
    assert fixed["last_incident"]["job_url"].startswith("https://buildkite.com/vllm/ci/")
    assert fixed["evidence"]
    assert {row["source_pipeline"] for row in fixed["evidence"]} == {"ci"}
    assert all(row["url"].startswith("https://buildkite.com/vllm/ci/") for row in fixed["evidence"])


def test_upstream_reliability_fails_closed_without_a_strict_main_cohort():
    payload = ops._reliability(
        {
            "builds": [_build(900, "2026-04-22", [
                _job("Nightly only", "passed", "https://buildkite.com/vllm/ci/builds/900/steps/nightly"),
            ], pipeline="ci")],
            "retry_analysis": {
                "summary": {"retry_attempt_count": 1},
                "retry_attempts": [{"build_number": 900, "job_id": "retry"}],
            },
        },
        pipeline_slug="ci",
    )

    assert payload["available"] is False
    assert payload["cohort"]["available"] is False
    assert payload["group_catalog"] == []
    assert payload["flaky_candidates"] == []
    assert payload["retry_analysis"]["retry_attempts"] == []
    assert payload["denominator"]["observations"] == 0


def test_upstream_reliability_rejects_malformed_present_cohorts():
    collector = {
        "cohort": {
            "id": "ci-main-completed-pass-fail",
            "pipeline": "ci",
            "branch": "main",
            "build_states": ["failed", "passed"],
            "build_count": 1,
            "exhaustive": True,
        },
        "provenance": {
            "pipeline": "ci",
            "endpoint": "/organizations/vllm/pipelines/ci/builds",
            "query": {"branch": "main"},
            "collection": {"exhaustive": True},
        },
        "builds": [{
            "number": 700,
            "branch": "main",
            "state": "passed",
            "finished_at": "2026-04-22T12:00:00Z",
            "url": "https://buildkite.com/vllm/ci/builds/700",
        }],
        "groups": [],
    }
    malformed = []
    for path, value in (
        (("cohort", "pipeline"), "amd-ci"),
        (("cohort", "branch"), "feature"),
        (("provenance", "query", "branch"), "feature"),
        (("builds", 0, "state"), "running"),
        (("builds", 0, "url"), "https://buildkite.com/vllm/amd-ci/builds/700"),
    ):
        payload = json.loads(json.dumps(collector))
        target = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        malformed.append(payload)

    for collector_payload in malformed:
        reliability = ops._reliability(
            {"all_main_reliability": collector_payload},
            pipeline_slug="ci",
        )
        assert reliability["available"] is False
        assert reliability["group_catalog"] == []
        assert reliability["flaky_candidates"] == []
        assert reliability["retry_analysis"]["retry_attempts"] == []


def test_upstream_reliability_fails_closed_on_malformed_json_types():
    malformed_payloads = [
        [],
        {"all_main_reliability": "not-an-object"},
        {
            "all_main_reliability": {
                "cohort": {"build_states": [{"not": "hashable"}]},
                "provenance": [],
                "builds": "not-a-list",
                "groups": ["not-a-group"],
            }
        },
    ]

    for payload in malformed_payloads:
        reliability = ops._reliability(payload, pipeline_slug="ci")
        assert reliability["available"] is False
        assert reliability["group_catalog"] == []
        assert reliability["retry_analysis"]["available"] is False


def test_upstream_reliability_rejects_untrusted_legacy_main_builds():
    provenance = {
        "cohort": {"pipeline": "ci", "branch": "main"},
        "authoritative_evidence_key": "all_main_reliability",
    }
    untrusted = [{
        "number": 701,
        "branch": "feature",
        "state": "running",
        "finished_at": "",
        "web_url": "https://buildkite.com/vllm/ci/builds/701",
        "jobs": [],
    }]

    reliability = ops._reliability(
        {"main_builds": untrusted, "main_builds_provenance": provenance},
        pipeline_slug="ci",
    )

    assert reliability["available"] is False
    assert reliability["denominator"]["builds"] == 0


def test_gating_pass_without_upstream_history_is_not_consistently_passing():
    targets = {"groups": [{"id": 1, "label": "Upstream absent"}]}
    matrix = {
        "generated_at": GENERATED_AT,
        "rows": [{
            "canonical_title": "Upstream absent",
            "cells": {"mi300": {
                "exists": True,
                "latest_state": "passed",
                "latest_build_number": 800,
                "latest_url": "https://buildkite.com/vllm/amd-ci/builds/800/steps/canvas?sid=amd",
            }},
        }],
    }
    reliability = ops._reliability({}, pipeline_slug="ci")

    row = ops._gating(targets, {}, matrix, {}, reliability)["active_target_groups"][0]

    assert row["latest_amd_result"]["state"] == "passed"
    assert row["main_reliability"]["available"] is False
    assert row["assessment"] == "passed_without_history"


def test_group_catalog_retains_linked_terminal_main_observations(tmp_path):
    reliability = ops.build_snapshot(_fixture_data(tmp_path), generated_at=GENERATED_AT)["reliability"]
    candidates = {row["name"]: row for row in reliability["group_catalog"]}

    hard = candidates["Mixed hard"]
    assert hard["observation_count"] == hard["runs"] == 3
    assert hard["retry_evidence_observation_count"] == 2
    assert [row["state"] for row in hard["observations"]] == ["passed", "hard", "passed"]
    assert all(
        {"build_number", "build_url", "job_url", "state", "observed_at", "duration_mins"} <= row.keys()
        for row in hard["observations"]
    )
    failed = next(row for row in hard["observations"] if row["state"] == "hard")
    assert failed["source_pipeline"] == "ci"
    assert failed["build_number"] == 103
    assert failed["build_url"] == "https://buildkite.com/vllm/ci/builds/103"
    assert failed["job_url"].endswith("?jid=mixed-hard-failed&tab=output")
    assert failed["observed_at"] == "2026-04-22T10:00:00Z"
    assert failed["duration_mins"] == 33
    assert failed["queue"] == "gpu_1_queue"
    assert failed["tests"] == 12
    assert failed["failed_tests"] == 12
    assert failed["retry_evidence"] == {
        "retried": True,
        "retried_in_job_id": "mixed-hard-retry",
        "job_id": "mixed-hard-failed",
        "retried_in_job_url": (
            "https://buildkite.com/vllm/ci/builds/103/steps/canvas"
            "?jid=mixed-hard-retry&tab=output"
        ),
    }
    passed_retry = next(
        row for row in hard["observations"]
        if row["state"] == "passed" and row["build_number"] == 103
    )
    assert passed_retry["retry_evidence"] == {
        "retries_count": 1,
        "retry_source": "manual",
        "retry_type": "manual",
        "job_id": "mixed-hard-retry",
    }

    soft = candidates["Mixed soft"]
    assert soft["observation_count"] == soft["runs"] == 2
    assert [row["state"] for row in soft["observations"]] == ["soft", "passed"]
    assert all("retry_evidence" not in row for row in soft["observations"])

    assert reliability["retry_analysis"]["evidence_type"] == "explicit_retry_recovery"
    assert reliability["retry_analysis"]["summary"]["failed_then_passed_recovery_count"] == 1
    assert reliability["retry_analysis"]["retry_attempts"][0]["job_url"].startswith(
        "https://buildkite.com/vllm/ci/"
    )
    assert "not proof that a retry recovered" in reliability["evidence_definitions"]["mixed_outcome_history"]


def test_nightly_fixed_requires_an_observed_pass():
    previous = _build(10, "2026-04-20", [
        _job("Missing now", "failed", "https://buildkite.com/vllm/amd-ci/builds/10/steps/missing"),
        _job("Actually fixed", "failed", "https://buildkite.com/vllm/amd-ci/builds/10/steps/fixed"),
    ])
    current = _build(11, "2026-04-21", [
        _job("Actually fixed", "passed", "https://buildkite.com/vllm/amd-ci/builds/11/steps/fixed"),
    ])

    row = ops._nightly_pipeline("amd-ci", {"builds": [current, previous]})["builds"][0]

    assert [item["name"] for item in row["transitions"]["fixed"]] == ["Actually fixed"]
    assert [item["name"] for item in row["transitions"]["not_observed"]] == ["Missing now"]


def test_gating_keeps_four_gpu_and_h100_mirror_evidence_distinct():
    targets = {
        "summary": {"target_group_count": 2},
        "groups": [
            {"id": 1, "label": "V1 e2e (4 GPUs)", "area": "engine"},
            {"id": 2, "label": "V1 e2e (4xH100)", "area": "engine"},
        ],
    }
    matrix = {
        "generated_at": GENERATED_AT,
        "source": {"latest_build_number": 10649},
        "summary": {"unique_groups": 2, "hardware_cells": 2},
        "rows": [
            {
                "title": "V1 e2e (4 GPUs)",
                "cells": {"mi300": {
                    "exists": True,
                    "latest_state": "soft_fail",
                    "latest_build_number": 10649,
                    "latest_url": "https://buildkite.com/vllm/amd-ci/builds/10649/steps/canvas?sid=soft",
                }},
            },
            {
                "title": "V1 e2e (4xH100-4xMI300)",
                "cells": {"mi300": {
                    "exists": True,
                    "latest_state": "passed",
                    "latest_build_number": 10649,
                    "latest_url": "https://buildkite.com/vllm/amd-ci/builds/10649/steps/canvas?sid=passed",
                }},
            },
        ],
    }

    reliability = {"source_pipeline": "ci", "group_catalog": [
        {
            "id": "four-gpu-mi300",
            "group_ids": ["four-gpu-mi300"],
            "name": "V1 e2e (4 GPUs)",
            "hardware": "mi300",
            "queues": ["amd_mi300_4"],
            "runs": 2,
            "passed": 1,
            "failed": 1,
            "soft_failed": 0,
        },
        {
            "id": "four-gpu-mi325",
            "group_ids": ["four-gpu-mi325"],
            "name": "V1 e2e (4 GPUs)",
            "hardware": "mi325",
            "queues": ["amd_mi325_4"],
            "runs": 1,
            "passed": 1,
            "failed": 0,
            "soft_failed": 0,
        },
    ]}
    result = ops._gating(targets, {"rows": []}, matrix, {}, reliability)
    by_label = {row["label"]: row for row in result["active_target_groups"]}

    assert by_label["V1 e2e (4 GPUs)"]["latest_amd_result"]["state"] == "soft"
    assert by_label["V1 e2e (4 GPUs)"]["latest_amd_result"]["evidence"][0]["url"].endswith(
        "sid=soft"
    )
    assert by_label["V1 e2e (4xH100)"]["latest_amd_result"]["state"] == "passed"
    assert by_label["V1 e2e (4xH100)"]["latest_amd_result"]["evidence"][0]["url"].endswith(
        "sid=passed"
    )
    assert by_label["V1 e2e (4 GPUs)"]["main_reliability"]["source_pipeline"] == "ci"
    assert by_label["V1 e2e (4 GPUs)"]["main_reliability"]["group_ids"] == [
        "four-gpu-mi300",
        "four-gpu-mi325",
    ]
    assert {
        (row["hardware"], tuple(row["queues"]))
        for row in by_label["V1 e2e (4 GPUs)"]["main_reliability"]["variants"]
    } == {
        ("mi300", ("amd_mi300_4",)),
        ("mi325", ("amd_mi325_4",)),
    }


def test_gating_never_promotes_upstream_history_to_latest_amd_result():
    reliability = {
        "source_pipeline": "ci",
        "group_catalog": [{
            "id": "upstream-group",
            "group_ids": ["upstream-group"],
            "name": "Upstream group",
            "runs": 1,
            "passed": 1,
            "failed": 0,
            "soft_failed": 0,
            "latest_state": "passed",
            "latest_observed_at": GENERATED_AT,
            "latest_url": "https://buildkite.com/vllm/ci/builds/900/steps/upstream",
            "green_streak": 1,
            "nightly_green_streak": 1,
            "observations": [{
                "source_pipeline": "ci",
                "state": "passed",
                "build_number": 900,
                "build_kind": "nightly",
                "build_url": "https://buildkite.com/vllm/ci/builds/900",
                "job_url": "https://buildkite.com/vllm/ci/builds/900/steps/upstream",
                "observed_at": GENERATED_AT,
            }],
        }],
    }

    gating = ops._gating(
        {"groups": [{"id": 1, "label": "Upstream group"}]},
        {"rows": []},
        {"generated_at": GENERATED_AT, "rows": []},
        {},
        reliability,
    )
    group = gating["active_target_groups"][0]

    assert group["latest_amd_result"] == {
        "state": "unknown",
        "build_number": None,
        "observed_at": GENERATED_AT,
        "source_pipeline": "amd-ci",
        "evidence": [],
    }
    assert group["assessment"] == "no_recent_amd_signal"
    assert group["main_reliability"]["latest_state"] == "passed"
    assert group["nightly_green_streak"] == 1
    assert {row["source_pipeline"] for row in group["evidence"]} == {"ci"}


def test_gating_variant_aggregation_is_order_independent_and_incident_first():
    def variant(identifier: str, state: str) -> dict:
        observation = {
            "source_pipeline": "ci",
            "state": state,
            "build_number": 901,
            "build_kind": "nightly",
            "build_url": "https://buildkite.com/vllm/ci/builds/901",
            "job_url": (
                "https://buildkite.com/vllm/ci/builds/901/steps/canvas"
                f"?jid={identifier}"
            ),
            "observed_at": GENERATED_AT,
        }
        return {
            "id": identifier,
            "group_ids": [identifier],
            "name": "Order independent target",
            "hardware": identifier,
            "queues": [f"gpu_{identifier}"],
            "runs": 1,
            "passed": int(state == "passed"),
            "failed": int(state == "hard"),
            "soft_failed": 0,
            "latest_state": state,
            "latest_observed_at": GENERATED_AT,
            "latest_url": observation["job_url"],
            "last_incident": observation if state == "hard" else None,
            "observations": [observation],
        }

    outputs = []
    variants = [variant("h100", "passed"), variant("h200", "hard")]
    for catalog in (variants, list(reversed(variants))):
        reliability = {
            "available": True,
            "source_pipeline": "ci",
            "group_catalog": catalog,
        }
        outputs.append(ops._gating(
            {"groups": [{"id": 1, "label": "Order independent target"}]},
            {"rows": []},
            {"generated_at": GENERATED_AT, "rows": []},
            {},
            reliability,
        )["active_target_groups"][0])

    assert [row["main_reliability"]["latest_state"] for row in outputs] == ["hard", "hard"]
    assert [row["nightly_green_streak"] for row in outputs] == [0, 0]
    assert [row["main_reliability"]["variant_count"] for row in outputs] == [2, 2]


def test_snapshot_prefers_collector_all_main_variant_catalog(tmp_path):
    data_dir = _fixture_data(tmp_path)
    analytics_payload = json.loads((data_dir / "analytics.json").read_text())
    analytics_payload["ci"]["all_main_reliability"] = {
        "cohort": {
            "id": "ci-main-completed-pass-fail",
            "pipeline": "ci",
            "branch": "main",
            "build_states": ["failed", "passed"],
            "build_count": 2,
            "canonical_nightly_build_count": 1,
            "non_nightly_main_build_count": 1,
            "window_days": 30,
            "exhaustive": True,
        },
        "denominator": {"eligible_observations": 2, "excluded_observations": 1},
        "provenance": {
            "pipeline": "ci",
            "endpoint": "/organizations/vllm/pipelines/ci/builds",
            "query": {"branch": "main"},
            "collection": {"exhaustive": True},
        },
        "summary": {"retry_evidence_observations": 0},
        "builds": [
            {
                "number": 201,
                "branch": "main",
                "state": "failed",
                "finished_at": "2026-04-22T12:00:00Z",
                "url": "https://buildkite.com/vllm/ci/builds/201",
                "is_canonical_nightly": False,
            },
            {
                "number": 200,
                "branch": "main",
                "state": "passed",
                "finished_at": "2026-04-21T12:00:00Z",
                "url": "https://buildkite.com/vllm/ci/builds/200",
                "is_canonical_nightly": True,
            },
        ],
        "groups": [{
            "group_id": "strict-variant-id",
            "name": "Non-nightly main group (4 GPUs)",
            "raw_name": "mi300_4: Non-nightly main group (4 GPUs)",
            "step_key": "strict-step",
            "hardware": "h100",
            "queue": "gpu_4_queue",
            "denominator": 2,
            "passed": 1,
            "failed": 1,
            "soft_failed": 0,
            "incident_rate": 50.0,
            "excluded_observations": 1,
            "retry_evidence_observations": 0,
            "duration": {
                "wall_completion": {
                    "samples": 2, "p50_mins": 12.0, "p90_mins": 14.0, "max_mins": 16.0,
                },
                "test_reported": {
                    "samples": 2, "p50_mins": 7.0, "p90_mins": 8.0, "max_mins": 9.0,
                },
                "queue_wait": {
                    "samples": 2, "p50_mins": 3.0, "p90_mins": 4.0, "max_mins": 5.0,
                },
                "end_to_end": {
                    "samples": 2, "p50_mins": 15.0, "p90_mins": 18.0, "max_mins": 21.0,
                },
            },
            "observations_truncated": False,
            "observations": [
                {
                    "source_pipeline": "ci",
                    "build_number": 201,
                    "build_url": "https://buildkite.com/vllm/ci/builds/201",
                    "build_commit": "abc",
                    "build_message": "regular main change",
                    "job_id": "job-201",
                    "job_url": "https://buildkite.com/vllm/ci/builds/201/steps/canvas?jid=job-201",
                    "observed_at": "2026-04-22T12:00:00Z",
                    "result": "failed",
                    "terminal_state": "failed",
                    "eligible_for_reliability": True,
                    "wall_completion_mins": 14.0,
                    "queue_wait_mins": 4.0,
                },
                {
                    "source_pipeline": "ci",
                    "build_number": 200,
                    "build_url": "https://buildkite.com/vllm/ci/builds/200",
                    "build_commit": "def",
                    "build_message": "nightly",
                    "job_id": "job-200",
                    "job_url": "https://buildkite.com/vllm/ci/builds/200/steps/canvas?jid=job-200",
                    "observed_at": "2026-04-21T12:00:00Z",
                    "result": "passed",
                    "terminal_state": "passed",
                    "eligible_for_reliability": True,
                    "wall_completion_mins": 10.0,
                    "queue_wait_mins": 2.0,
                },
            ],
        }],
    }
    _write_json(data_dir / "analytics.json", analytics_payload)

    reliability = ops.build_snapshot(data_dir, generated_at=GENERATED_AT)["reliability"]

    assert reliability["source_pipeline"] == "ci"
    assert reliability["denominator"]["builds"] == 2
    assert reliability["denominator"]["observations"] == 2
    assert reliability["denominator"]["unknown_observations_excluded"] == 1
    assert [row["name"] for row in reliability["group_catalog"]] == [
        "Non-nightly main group (4 GPUs)"
    ]
    group = reliability["group_catalog"][0]
    assert group["id"] == "strict-variant-id"
    assert group["group_ids"] == ["strict-variant-id"]
    assert group["hardware"] == "h100"
    assert group["queues"] == ["gpu_4_queue"]
    assert group["duration_basis"] == "job_wall"
    assert group["max_wall_mins"] == 16.0
    assert group["max_test_mins"] == 9.0
    assert group["max_wait_mins"] == 5.0
    assert group["max_end_to_end_mins"] == 21.0
    assert group["max_dur"] == 16.0
    assert group["observations"][0]["build_kind"] == "main"
    assert group["observations"][0]["source_pipeline"] == "ci"
    assert group["observations"][0]["job_url"].endswith("jid=job-201")
    assert reliability["latency_rankings"]["by_p90_duration"][0]["max_dur"] == 16.0
    assert reliability["latency_rankings"]["by_max_duration"][0]["id"] == "strict-variant-id"
    assert reliability["cohort"]["composition"] == {
        "all_main_builds": 2,
        "canonical_nightlies": 1,
        "other_main_builds": 1,
    }


def test_snapshot_retains_thirty_amd_nightlies_and_separates_upstream_parity(tmp_path):
    data_dir = _fixture_data(tmp_path)
    analytics_payload = json.loads((data_dir / "analytics.json").read_text())
    start = datetime(2026, 3, 1)
    amd_builds = [
        _build(
            1000 + index,
            (start + timedelta(days=index)).strftime("%Y-%m-%d"),
            [_job(f"Group {index}", "passed", f"https://buildkite.com/vllm/amd-ci/builds/{1000 + index}")],
        )
        for index in range(35)
    ]
    upstream_builds = [
        _build(
            2000 + index,
            (start + timedelta(days=index)).strftime("%Y-%m-%d"),
            [],
            pipeline="ci",
        )
        for index in range(4)
    ]
    analytics_payload["amd-ci"].update({"days": 30, "builds": amd_builds})
    analytics_payload["ci"].update({"days": 30, "builds": upstream_builds})
    _write_json(data_dir / "analytics.json", analytics_payload)

    nightly = ops.build_snapshot(data_dir, generated_at=GENERATED_AT)["nightly"]

    canonical = nightly["canonical_history"]
    assert canonical["pipeline"] == "amd-ci"
    assert canonical["role"] == "canonical_nightly_comparison"
    assert canonical["builds_available"] == 35
    assert len(canonical["builds"]) == 30
    assert [row["number"] for row in canonical["builds"][:2]] == [1034, 1033]
    assert canonical["builds"][-1]["number"] == 1005
    assert nightly["pipelines"][0]["builds"] == canonical["builds"]

    parity = nightly["upstream_parity"]
    assert parity["pipeline"] == "ci"
    assert parity["role"] == "upstream_parity"
    assert len(parity["builds"]) == 4


def test_retry_analysis_retains_all_attempts_recoveries_and_exact_urls():
    attempts = [
        {
            "build_number": 5000 + (index % 34),
            "name": f"Retry group {index}",
            "job_id": f"retry-{index}",
            "url": (
                f"https://buildkite.com/vllm/ci/builds/{5000 + (index % 34)}"
                f"/steps/canvas?jid=retry-{index}"
            ),
        }
        for index in range(80)
    ]
    recoveries = [
        {
            "build_number": 5000 + (index % 34),
            "name": f"Recovered group {index}",
            "failed_job_id": f"failed-{index}",
            "passed_job_id": f"passed-{index}",
            "failed_url": (
                f"https://buildkite.com/vllm/ci/builds/{5000 + (index % 34)}"
                f"/steps/canvas?jid=failed-{index}"
            ),
            "passed_url": (
                f"https://buildkite.com/vllm/ci/builds/{5000 + (index % 34)}"
                f"/steps/canvas?jid=passed-{index}"
            ),
        }
        for index in range(21)
    ]
    analytics_payload = {
        "all_main_reliability": {
            "cohort": {
                "id": "ci-main-completed-pass-fail",
                "pipeline": "ci",
                "branch": "main",
                "build_states": ["failed", "passed"],
                "build_count": 34,
                "canonical_nightly_build_count": 30,
                "non_nightly_main_build_count": 4,
                "window_days": 30,
                "exhaustive": True,
            },
            "denominator": {"eligible_observations": 0, "excluded_observations": 0},
            "provenance": {
                "pipeline": "ci",
                "endpoint": "/organizations/vllm/pipelines/ci/builds",
                "query": {"branch": "main"},
                "collection": {"exhaustive": True},
            },
            "summary": {"retry_evidence_observations": 80},
            "builds": [
                {
                    "number": 5000 + index,
                    "branch": "main",
                    "state": "passed",
                    "finished_at": "2026-04-22T12:00:00Z",
                    "url": f"https://buildkite.com/vllm/ci/builds/{5000 + index}",
                }
                for index in range(34)
            ],
            "groups": [],
        },
        "main_retry_analysis": {
            "available": True,
            "summary": {
                "builds_evaluated": 34,
                "builds_with_retries": 11,
                "retry_attempt_count": 80,
                "failed_then_passed_recovery_count": 21,
            },
            "retry_attempts": attempts,
            "failed_then_passed_recoveries": recoveries,
            "provenance": {
                "source_pipeline": "ci",
                "complete": True,
                "cohort_build_numbers": [5000 + index for index in range(34)],
            },
        },
    }

    reliability = ops._reliability(analytics_payload, pipeline_slug="ci")
    retry = reliability["retry_analysis"]

    assert reliability["source_pipeline"] == "ci"
    assert retry["summary"]["retry_attempt_count"] == len(retry["retry_attempts"]) == 80
    assert retry["summary"]["failed_then_passed_recovery_count"] == 21
    assert len(retry["failed_then_passed_recoveries"]) == 21
    assert retry["summary"]["linked_retry_attempt_count"] == 80
    assert retry["summary"]["linked_recovery_count"] == 21
    assert all(
        "/vllm/ci/builds/" in row["job_url"] and "?jid=retry-" in row["job_url"]
        for row in retry["retry_attempts"]
    )
    assert all(
        "/vllm/ci/builds/" in row["failed_url"]
        and "/vllm/ci/builds/" in row["passed_url"]
        and "?jid=failed-" in row["failed_url"]
        and "?jid=passed-" in row["passed_url"]
        for row in retry["failed_then_passed_recoveries"]
    )
    assert retry["provenance"]["source_path"] == "analytics.json"
    assert retry["provenance"]["source_key"] == "ci.main_retry_analysis"
    assert reliability["cohort"]["composition"] == {
        "all_main_builds": 34,
        "canonical_nightlies": 30,
        "other_main_builds": 4,
    }


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
