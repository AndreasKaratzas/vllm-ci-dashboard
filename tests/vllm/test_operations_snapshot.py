"""Fixture-driven tests for the compact v2 operations snapshot."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from vllm import build_operations_snapshot as ops
from vllm import collect_analytics as analytics


GENERATED_AT = "2026-04-22T12:00:00Z"


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload))


def _write_jsonl(path: Path, rows: list[dict], trailing: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + trailing)


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
    _write_json(tmp_path / "capacity_monitor.json", {
        "schema_version": 2,
        "generated_at": "2026-04-22T10:04:30Z",
        "projection": {
            "target_groups": 160,
            "declared_existing_groups": 147,
            "declared_new_groups": 10,
            "declared_total_groups": 157,
            "base_groups": 54,
            "projected_total_gpus": 269,
        },
        "summary": {
            "capacity": {
                "future_eligible": {
                    "queue_count": 1,
                    "concurrent_jobs": 232,
                    "gpus": 232,
                    "eight_gpu_node_equivalents": 29,
                },
                "retiring": {
                    "queue_count": 0,
                    "concurrent_jobs": 0,
                    "gpus": 0,
                    "eight_gpu_node_equivalents": 0,
                },
            },
        },
        "queues": [{
            "id": "amd_mi300_1",
            "label": "mi300_1",
            "family": "MI300",
            "gpus_per_job": 1,
            "max_concurrent_jobs": 232,
            "future_max_concurrent_jobs": 232,
            "gpu_capacity": 232,
            "future_gpu_capacity": 232,
            "monitored": True,
            "capacity_eligible": True,
            "lifecycle": "active",
        }],
    })
    _write_json(tmp_path / "workload_mapping.json", {
        "schema_version": 1,
        "generated_at": "2026-04-22T10:04:45Z",
        "window": {
            "days": 14,
            "start_date": "2026-04-09",
            "end_date": "2026-04-22",
            "complete": True,
            "lower_bound": False,
        },
        "scope": {
            "queues": ["amd_mi300_1"],
            "excluded_queue_classes": ["perf_eval"],
            "workload_pipelines": {
                "omni": ["vllm-omni-amd-ci"],
                "main": ["ci", "amd-ci", "amd-distributed-inference-ci"],
            },
        },
        "totals": {
            "omni": {"mapped_jobs": 2, "started_jobs": 2, "mapped_gpu_slots": 2},
            "main": {"mapped_jobs": 10, "started_jobs": 8, "mapped_gpu_slots": 10},
        },
        "daily": [],
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
            "pipeline": "vllm-omni-amd-ci",
            "queue": "amd_mi300_1",
            "source": "webhook",
            "analysis_excluded": False,
            "url": "https://buildkite.com/vllm/vllm-omni/builds/1/steps/pending",
        }],
        "running": [{
            "name": "Omni running",
            "state": "running",
            "workload": "omni",
            "pipeline": "vllm-omni-amd-ci",
            "queue": "amd_mi300_1",
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


def test_ci_ownership_snapshot_is_top_level_but_raw_source_is_private(tmp_path):
    data_dir = _fixture_data(tmp_path)
    ownership = {
        "schema_version": 1,
        "generated_at": GENERATED_AT,
        "available": True,
        "summary": {"areas": 25, "areas_with_incidents": 2},
        "areas": [{"area": "kernels", "counts": {"incidents": 1}}],
    }
    _write_json(data_dir / "ci_ownership.json", ownership)

    payload = ops.build_snapshot(data_dir, generated_at=GENERATED_AT)

    assert payload["ownership"] == ownership
    assert "ownership" not in payload["gating"]
    assert payload["sources"]["ci_ownership"]["published"] is False


def test_amd_test_health_uses_authoritative_job_states_and_preserves_evidence(tmp_path):
    alpha = "mi300_1: Alpha tests"
    beta = "mi355b_2: Beta tests"
    unknown = "mi325_4: Unknown tests"
    stable = "mi300_2: Stable tests"
    latest_only = "mi250_1: Latest only"
    _write_json(tmp_path / "analytics.json", {
        "amd-ci": {
            "generated_at": GENERATED_AT,
            "builds": [
                {
                    "number": 301,
                    "date": "2026-04-22",
                    "created_at": "2026-04-22T09:00:01Z",
                    "web_url": "https://buildkite.com/vllm/amd-ci/builds/301",
                    "jobs": [
                        {
                            "raw_name": alpha,
                            "job_id": "alpha-soft-301",
                            "step_id": "alpha-step-301",
                            "state": "soft_fail",
                            "soft_failed": True,
                            "finished_at": "2026-04-22T10:05:00Z",
                        },
                        {
                            "raw_name": stable,
                            "job_id": "stable-301",
                            "state": "passed",
                            "finished_at": "2026-04-22T10:06:00Z",
                        },
                        {
                            "raw_name": latest_only,
                            "job_id": "latest-hard-301",
                            "state": "failed",
                            "finished_at": "2026-04-22T10:07:00Z",
                        },
                    ],
                },
                {
                    "number": 300,
                    "date": "2026-04-21",
                    "created_at": "2026-04-21T09:00:01Z",
                    "web_url": "https://buildkite.com/vllm/amd-ci/builds/300",
                    "jobs": [
                        {
                            "raw_name": alpha,
                            "job_id": "alpha-pass-300",
                            "step_id": "alpha-step-300",
                            "state": "passed",
                            "finished_at": "2026-04-21T10:00:00Z",
                        },
                        {
                            "raw_name": beta,
                            "job_id": "beta-hard-300",
                            "step_id": "beta-fail-step-300",
                            "state": "failed",
                            "finished_at": "2026-04-21T10:01:00Z",
                        },
                        {
                            "raw_name": stable,
                            "job_id": "stable-300",
                            "state": "passed",
                            "finished_at": "2026-04-21T10:02:00Z",
                        },
                    ],
                },
            ],
        },
    })
    _write_jsonl(tmp_path / "test_results" / "2026-04-21_amd.jsonl", [
        {
            "name": "__passed__ (3)",
            "status": "passed",
            "duration_secs": 12.5,
            "job_name": alpha,
            "job_id": "alpha-pass-300",
            "step_id": "alpha-step-300",
            "build_number": 300,
            "pipeline": "amd-ci",
            "date": "2026-04-21",
        },
        {
            "name": "__skipped__ (2)",
            "status": "skipped",
            "duration_secs": 0,
            "job_name": alpha,
            "job_id": "alpha-pass-300",
            "step_id": "alpha-step-300",
            "build_number": 300,
            "pipeline": "amd-ci",
            "date": "2026-04-21",
        },
        {
            "name": "test_beta_failure",
            "status": "failed",
            "duration_secs": 4,
            "job_name": beta,
            "job_id": "beta-hard-300",
            "step_id": "beta-fail-step-300",
            "build_number": 300,
            "pipeline": "amd-ci",
            "date": "2026-04-21",
        },
        {
            "name": "test_beta_pass",
            "status": "passed",
            "duration_secs": 3,
            "job_name": beta,
            "job_id": "beta-hard-300",
            "step_id": "beta-fail-step-300",
            "build_number": 300,
            "pipeline": "amd-ci",
            "date": "2026-04-21",
        },
        {
            "name": "test_unknown",
            "status": "xfailed",
            "duration_secs": 1,
            "job_name": unknown,
            "step_id": "unknown-step-300",
            "build_number": 300,
            "pipeline": "amd-ci",
            "date": "2026-04-21",
        },
        {
            "name": "test_stable",
            "status": "passed",
            "duration_secs": 2,
            "job_name": stable,
            "job_id": "stable-300",
            "build_number": 300,
            "pipeline": "amd-ci",
            "date": "2026-04-21",
        },
    ], trailing="\n")
    _write_jsonl(tmp_path / "test_results" / "2026-04-22_amd.jsonl", [
        {
            "name": "__passed__ (1)",
            "status": "passed",
            "duration_secs": 2,
            "job_name": alpha,
            "job_id": "alpha-soft-301",
            "step_id": "alpha-step-301",
            "build_number": 301,
            "pipeline": "amd-ci",
            "date": "2026-04-22",
        },
        {
            "name": "__errors__ (2)",
            "status": "error",
            "duration_secs": 8,
            "job_name": alpha,
            "job_id": "alpha-soft-301",
            "step_id": "alpha-step-301",
            "build_number": 301,
            "pipeline": "amd-ci",
            "date": "2026-04-22",
        },
        {
            "name": "test_stable",
            "status": "passed",
            "duration_secs": 2,
            "job_name": stable,
            "job_id": "stable-301",
            "build_number": 301,
            "pipeline": "amd-ci",
            "date": "2026-04-22",
        },
        {
            "name": "test_latest",
            "status": "passed",
            "duration_secs": 2,
            "job_name": latest_only,
            "job_id": "latest-hard-301",
            "build_number": 301,
            "pipeline": "amd-ci",
            "date": "2026-04-22",
        },
    ], trailing="\n")

    payload = ops.build_snapshot(tmp_path, generated_at=GENERATED_AT)
    health = payload["amd_test_health"]
    groups = {row["exact_job_name"]: row for row in health["group_catalog"]}

    assert payload["schema_version"] == 2
    assert health["available"] is True
    assert health["source_pipeline"] == "amd-ci"
    assert health["cohort"]["aggregation_key"] == ["build_number", "exact_job_name"]
    assert health["summary"] == {
        "build_count": 2,
        "retained_group_count": 5,
        "group_count": 5,
        "union_group_count": 5,
        "latest_group_count": 3,
        "latest_build_number": 301,
        "latest_build_url": "https://buildkite.com/vllm/amd-ci/builds/301",
        "latest_url": "https://buildkite.com/vllm/amd-ci/builds/301",
        "latest_observed_at": "2026-04-22T09:00:01Z",
        "latest_state_counts": {
            "passed": 1,
            "soft": 1,
            "hard": 1,
            "unknown": 0,
        },
        "latest_passed_group_count": 1,
        "latest_soft_failed_group_count": 1,
        "latest_hard_failed_group_count": 1,
        "latest_incident_group_count": 2,
        "latest_unknown_group_count": 0,
        "observation_state_counts": {
            "passed": 3,
            "soft": 1,
            "hard": 2,
            "unknown": 1,
        },
        "passed_observation_count": 3,
        "soft_failed_observation_count": 1,
        "hard_failed_observation_count": 2,
        "incident_observation_count": 3,
        "unknown_observation_count": 1,
        "mixed_outcome_group_count": 1,
        "stable_passing_group_count": 1,
        "persistent_incident_group_count": 2,
        "hardware_counts": {"mi250": 1, "mi300": 2, "mi325": 1, "mi355b": 1},
        "hardware_variant_counts": {
            "mi250_1": 1,
            "mi300_1": 1,
            "mi300_2": 1,
            "mi325_4": 1,
            "mi355b_2": 1,
        },
        "latest_hardware_counts": {"mi250": 1, "mi300": 2},
    }
    assert sum(health["summary"]["latest_state_counts"].values()) == 3

    alpha_group = groups[alpha]
    assert alpha_group["id"] == hashlib.sha1(f"amd-ci:{alpha}".encode()).hexdigest()[:20]
    assert len(alpha_group["id"]) == 20
    assert alpha_group["name"] == alpha_group["display_name"] == "Alpha tests"
    assert alpha_group["job_name"] == alpha_group["exact_job_name"] == alpha
    assert alpha_group["hardware"] == "mi300"
    assert alpha_group["hardware_variant"] == "mi300_1"
    assert alpha_group["queue"] == "amd_mi300_1"
    assert alpha_group["queues"] == ["amd_mi300_1"]
    assert (alpha_group["runs"], alpha_group["passed"], alpha_group["incidents"]) == (2, 1, 1)
    assert alpha_group["soft_failed"] == 1
    assert alpha_group["hard_failed"] == 0
    assert alpha_group["unknown"] == 0
    assert alpha_group["pass_rate_pct"] == 50.0
    assert alpha_group["current_pass_streak"] == 0
    assert alpha_group["latest_state"] == "soft"
    assert alpha_group["latest_build_number"] == 301
    assert alpha_group["latest_observed_at"] == "2026-04-22T10:05:00Z"
    assert alpha_group["latest_url"].endswith("?jid=alpha-soft-301&tab=output")
    assert [row["build_number"] for row in alpha_group["observations"]] == [300, 301]
    assert [row["state"] for row in alpha_group["observations"]] == ["passed", "soft"]
    first_alpha = alpha_group["observations"][0]
    assert first_alpha["status_counts"] == {"passed": 3, "skipped": 2}
    assert first_alpha["tests"] == 5
    assert first_alpha["passed_tests"] == 3
    assert first_alpha["skipped_tests"] == 2
    assert first_alpha["test_duration_secs"] == 12.5
    latest_alpha = alpha_group["observations"][1]
    assert latest_alpha["state"] == "soft"
    assert latest_alpha["outcome_source"] == "analytics_job_state"
    assert latest_alpha["status_counts"] == {"error": 2, "passed": 1}
    assert latest_alpha["failed_tests"] == latest_alpha["error_tests"] == 2
    assert latest_alpha["job_url"] == (
        "https://buildkite.com/vllm/amd-ci/builds/301/steps/canvas"
        "?jid=alpha-soft-301&tab=output"
    )
    assert latest_alpha["build_url"] == "https://buildkite.com/vllm/amd-ci/builds/301"

    beta_group = groups[beta]
    assert beta_group["latest_state"] == "hard"
    assert beta_group["soft_failed"] == 0
    assert beta_group["hard_failed"] == 1
    assert beta_group["latest_url"].endswith("?jid=beta-hard-300&tab=output")
    assert beta_group["hardware"] == "mi355b"
    assert beta_group["hardware_variant"] == "mi355b_2"
    assert beta_group["pass_rate_pct"] == 0.0

    unknown_group = groups[unknown]
    assert unknown_group["latest_state"] == "unknown"
    assert unknown_group["pass_rate_pct"] is None
    assert unknown_group["unknown"] == 1
    assert unknown_group["latest_url"].endswith("?sid=unknown-step-300&tab=output")
    assert unknown_group["observations"][0]["outcome_source"] == "unavailable"

    assert groups[stable]["current_pass_streak"] == 2
    assert groups[latest_only]["latest_state"] == "hard"
    assert groups[latest_only]["hard_failed"] == 1
    assert groups[latest_only]["observations"][0]["status_counts"] == {"passed": 1}
    assert [row["build_number"] for row in beta_group["observations"]] == [300]
    latest_build = next(row for row in health["builds"] if row["build_number"] == 301)
    assert latest_build["number"] == 301
    assert latest_build["observed"] == 3
    assert latest_build["passed"] == 1
    assert latest_build["soft_failed"] == 1
    assert latest_build["hard_failed"] == 1
    assert latest_build["incidents"] == 2
    assert latest_build["unknown"] == 0
    assert latest_build["observed_groups"] == 3
    assert latest_build["passed_groups"] == 1
    assert latest_build["soft_failed_groups"] == 1
    assert latest_build["hard_failed_groups"] == 1
    assert latest_build["incident_groups"] == 2
    assert latest_build["unknown_groups"] == 0
    assert latest_build["pass_rate_pct"] == 33.3
    assert latest_build["observed"] == (
        latest_build["passed"]
        + latest_build["soft_failed"]
        + latest_build["hard_failed"]
        + latest_build["unknown"]
    )
    assert all(row["build_number"] != 301 for row in beta_group["observations"])
    assert health["provenance"]["nightly_metadata"]["joined_group_observations"] == 6
    assert health["provenance"]["nightly_metadata"]["unjoined_group_observations"] == 1


def test_amd_test_catalog_prefers_newer_build_over_late_retry_of_older_build(tmp_path):
    group_name = "mi300_1: Retry-sensitive tests"
    _write_json(tmp_path / "analytics.json", {
        "amd-ci": {
            "generated_at": GENERATED_AT,
            "builds": [
                {
                    "number": 11651,
                    "date": "2026-08-04",
                    "created_at": "2026-08-04T09:00:00Z",
                    "web_url": "https://buildkite.com/vllm/amd-ci/builds/11651",
                    "jobs": [{
                        "raw_name": group_name,
                        "job_id": "nightly-job",
                        "state": "passed",
                        "finished_at": "2026-08-04T10:00:00Z",
                    }],
                },
                {
                    "number": 11591,
                    "date": "2026-08-03",
                    "created_at": "2026-08-03T09:00:00Z",
                    "web_url": "https://buildkite.com/vllm/amd-ci/builds/11591",
                    "jobs": [{
                        "raw_name": group_name,
                        "job_id": "late-retry-job",
                        "state": "passed",
                        "finished_at": "2026-08-04T15:22:00Z",
                    }],
                },
            ],
        },
    })
    _write_jsonl(tmp_path / "test_results" / "2026-08-03_amd.jsonl", [{
        "name": "test_retry_sensitive",
        "status": "passed",
        "duration_secs": 1,
        "job_name": group_name,
        "job_id": "late-retry-job",
        "build_number": 11591,
        "pipeline": "amd-ci",
        "date": "2026-08-03",
    }], trailing="\n")
    _write_jsonl(tmp_path / "test_results" / "2026-08-04_amd.jsonl", [{
        "name": "test_retry_sensitive",
        "status": "passed",
        "duration_secs": 1,
        "job_name": group_name,
        "job_id": "nightly-job",
        "build_number": 11651,
        "pipeline": "amd-ci",
        "date": "2026-08-04",
    }], trailing="\n")

    health = ops.build_snapshot(tmp_path, generated_at=GENERATED_AT)["amd_test_health"]
    group = health["group_catalog"][0]

    assert health["summary"]["latest_build_number"] == 11651
    assert health["summary"]["latest_group_count"] == 1
    assert group["latest_build_number"] == 11651
    assert [row["build_number"] for row in group["observations"]] == [11591, 11651]
    assert sum(
        row["latest_build_number"] == health["summary"]["latest_build_number"]
        for row in health["group_catalog"]
    ) == health["summary"]["latest_group_count"]


def test_amd_test_health_is_unavailable_for_missing_or_corrupt_results(tmp_path):
    missing = ops.build_snapshot(tmp_path, generated_at=GENERATED_AT)["amd_test_health"]

    assert missing["available"] is False
    assert missing["source_pipeline"] == "amd-ci"
    assert missing["summary"]["build_count"] == 0
    assert missing["summary"]["retained_group_count"] == 0
    assert missing["summary"]["group_count"] == 0
    assert missing["summary"]["latest_build_number"] is None
    assert missing["builds"] == []
    assert missing["group_catalog"] == []

    results = tmp_path / "test_results"
    results.mkdir()
    (results / "2026-04-22_amd.jsonl").write_text(
        "not-json\n[]\n{\"pipeline\":\"amd-ci\",\"build_number\":0}\n"
    )
    corrupt = ops.build_snapshot(tmp_path, generated_at=GENERATED_AT)["amd_test_health"]

    assert corrupt["available"] is False
    assert corrupt["builds"] == []
    assert corrupt["group_catalog"] == []
    assert corrupt["provenance"]["test_results"]["files_read"] == 1
    assert corrupt["provenance"]["test_results"]["malformed_rows"] == 3


def test_latest_infrastructure_blocked_nightly_is_not_dropped_or_given_stale_results(tmp_path):
    data_dir = _fixture_data(tmp_path)
    health_path = data_dir / "ci_health.json"
    health = json.loads(health_path.read_text())
    blocked = {
        "build_number": 104,
        "build_url": "https://buildkite.com/vllm/amd-ci/builds/104",
        "created_at": "2026-04-23T09:00:00Z",
        "finished_at": "2026-04-23T10:00:00Z",
        "state": "failed",
        "job_count": 7,
        "test_job_count": 6,
        "test_jobs_blocked": 6,
        "has_test_results": False,
    }
    health["amd"]["latest_build"] = health["amd"]["builds"][0]
    health["amd"]["latest_pipeline_build"] = blocked
    health["amd"]["latest_pipeline_build_has_test_results"] = False
    health["amd"]["latest_test_signal_build"] = health["amd"]["latest_build"]
    health["amd"]["builds"].insert(0, blocked)
    health_path.write_text(json.dumps(health))

    payload = ops.build_snapshot(data_dir, generated_at=GENERATED_AT)
    latest = payload["nightly"]["canonical_history"]["builds"][0]

    assert latest["number"] == 104
    assert latest["state"] == "failed"
    assert latest["has_test_results"] is False
    assert latest["test_job_count"] == latest["test_jobs_blocked"] == 6
    assert latest["failed_groups"] == []
    assert latest["soft_failed_groups"] == []
    assert latest["failure_movement"]["available"] is False
    assert latest["failure_movement"]["new"] == []
    assert latest["failure_movement"]["recurring"] == []
    assert latest["failure_movement"]["fixed"] == []
    assert latest["transitions"]["fixed"] == []
    assert latest["transitions"]["not_observed"]
    assert payload["home"]["latest_amd_nightly"]["number"] == 104
    assert payload["attention"][0] == {
        "kind": "nightly_infrastructure_blocked",
        "severity": "critical",
        "count": 6,
    }


def test_attention_uses_current_hardness_instead_of_newness():
    soft_only = {
        "pipelines": [{
            "builds": [{
                "failed_groups": [],
                "soft_failed_groups": [{"name": "new soft"}],
                "transitions": {
                    "new": [{"name": "new soft", "state": "soft_failed"}],
                },
            }],
        }],
    }
    shared = (
        {},
        {"active_target_summary": {"by_latest_amd_state": {}}},
        {"snapshot": {}},
        {"status": "healthy", "current": {}},
    )
    soft_attention = ops._attention(soft_only, *shared)

    assert soft_attention == [{
        "kind": "nightly_soft_failures",
        "severity": "warning",
        "count": 1,
    }]

    recurring_hard = {
        "pipelines": [{
            "builds": [{
                "failed_groups": [{"name": "recurring hard"}],
                "soft_failed_groups": [],
                "transitions": {
                    "new": [],
                    "recurring": [{"name": "recurring hard", "state": "failed"}],
                },
            }],
        }],
    }
    hard_attention = ops._attention(recurring_hard, *shared)

    assert hard_attention == [{
        "kind": "nightly_hard_failures",
        "severity": "critical",
        "count": 1,
    }]


def test_compact_queue_history_retains_observed_idle_rows_and_wait_provenance():
    compact = ops._compact_history_snapshot({
        "ts": GENERATED_AT,
        "schema_version": 2,
        "history_mode": "hourly_queue_wait_peaks",
        "archive_bucket_start": "2026-04-22T12:00:00Z",
        "total_waiting": 0,
        "total_running": 0,
        "queues": {
            "amd_mi300_1": {
                "waiting": 0,
                "running": 0,
                "zombie_waiting": 0,
                "zombie_running": 0,
                "wait_sample_count": 0,
                "sample_count": 0,
                "official_wait_source": None,
                "sample_wait_source": "scheduled_job_scan",
                "metrics_ts": "2026-04-22T11:59:00Z",
                "current_wait": {
                    "p50": {"value": 0.0, "source": "official_wait"},
                    "p95": {"value": 0.0, "source": "official_wait"},
                },
                "count_source_family": "queue_native",
                "wait_source_family": "queue_native",
                "p95_wait": 0.0,
                "p95_wait_source": "official_wait",
                "archive_wait_peaks": {
                    "p95": {
                        "value": 75.0,
                        "observed_at": "2026-04-22T12:25:00Z",
                        "source": "sample_wait",
                    }
                },
            },
            "amd_mi355b_1": {"waiting": 0, "running": 0},
        },
        "sources": {"counts": "queue_native"},
    })

    assert "amd_mi300_1" in compact["queues"]
    assert "unobserved_queue" not in compact["queues"]
    assert "amd_mi355b_1" not in compact["queues"]
    assert compact["tracked_queue_count"] == 1
    assert compact["history_mode"] == "hourly_queue_wait_peaks"
    assert compact["archive_bucket_start"] == "2026-04-22T12:00:00Z"
    idle = compact["queues"]["amd_mi300_1"]
    assert idle["waiting"] == idle["running"] == 0
    assert idle["wait_sample_count"] == idle["sample_count"] == 0
    assert idle["official_wait_source"] is None
    assert idle["archive_wait_peaks"]["p95"]["value"] == 75.0
    assert idle["sample_wait_source"] == "scheduled_job_scan"
    assert idle["metrics_ts"] == "2026-04-22T11:59:00Z"
    assert idle["current_wait"]["p95"] == {"value": 0.0, "source": "official_wait"}
    assert idle["count_source_family"] == "queue_native"
    assert idle["wait_source_family"] == "queue_native"
    assert idle["p95_wait"] == 0.0
    assert idle["p95_wait_source"] == "official_wait"


def test_v2_snapshot_transition_math_links_and_queue_provenance(tmp_path):
    payload = ops.build_snapshot(_fixture_data(tmp_path), generated_at=GENERATED_AT)

    assert payload["schema_version"] == 2
    assert payload["generated_at"] == GENERATED_AT
    assert payload["nightly"]["pipeline_order"] == ["amd-ci", "ci"]
    assert payload["nightly"]["transition_policy_id"] == "confirmed-incidents-v1"
    assert (
        payload["nightly"]["failure_movement_policy_id"]
        == "observed-failure-movement-v1"
    )
    assert payload["nightly"]["pipelines"][0]["pipeline"] == "amd-ci"
    assert (
        payload["nightly"]["pipelines"][0]["transition_policy_id"]
        == "confirmed-incidents-v1"
    )
    assert (
        payload["nightly"]["pipelines"][0]["failure_movement_policy_id"]
        == "observed-failure-movement-v1"
    )

    latest = payload["nightly"]["pipelines"][0]["builds"][0]
    assert latest["transitions"]["policy_id"] == "confirmed-incidents-v1"
    assert [row["name"] for row in latest["failed_groups"]] == ["New hard", "Recurring"]
    assert [row["name"] for row in latest["soft_failed_groups"]] == ["Mixed soft", "New soft"]
    assert [row["name"] for row in latest["transitions"]["new"]] == [
        "New hard",
        "Recurring",
    ]
    assert latest["transitions"]["recurring"] == []
    assert [row["name"] for row in latest["transitions"]["pending_soft"]] == [
        "Mixed soft",
        "New soft",
    ]
    assert all(row["soft_streak"] == 1 for row in latest["transitions"]["pending_soft"])
    assert [row["name"] for row in latest["transitions"]["fixed"]] == ["Fixed"]
    assert latest["transitions"]["preceding_build_number"] == 102
    movement = latest["failure_movement"]
    assert movement["available"] is True
    assert movement["preceding_build_number"] == 102
    assert [row["name"] for row in movement["new"]] == [
        "Mixed soft",
        "New hard",
        "New soft",
    ]
    assert [row["name"] for row in movement["recurring"]] == ["Recurring"]
    assert [row["name"] for row in movement["fixed"]] == ["Fixed"]
    assert len(movement["new"]) + len(movement["recurring"]) == (
        len(latest["failed_groups"]) + len(latest["soft_failed_groups"])
    )
    new_hard = next(row for row in latest["transitions"]["new"] if row["name"] == "New hard")
    assert new_hard["url"].endswith("/steps/new-hard")
    assert latest["transitions"]["fixed"][0]["url"].endswith("/builds/102/steps/fixed")
    assert "soft failures confirm after two distinct eligible completed builds" in (
        payload["nightly"]["transition_basis"]
    )
    assert "missing and indeterminate identities are omitted" in (
        payload["nightly"]["failure_movement_basis"]
    )

    assert payload["queue"]["snapshot"]["run_id"] == "current-run"
    assert payload["queue"]["snapshot"]["total_waiting"] == 2
    assert payload["queue"]["provenance"]["snapshot"]["sources"]["counts"] == "cluster_metrics"
    assert payload["queue"]["provenance"]["jobs"]["source_counts"] == {"webhook": 2}
    assert payload["queue"]["history_summary"]["source_path"] == "queue_timeseries.jsonl"
    assert payload["queue"]["provenance"]["source_paths"] == {
        "history": "queue_timeseries.jsonl",
        "jobs": "queue_jobs.json",
    }
    assert payload["omni"]["status"] == "healthy"
    assert payload["omni"]["current"] == {
        "waiting": 1,
        "running": 1,
        "waiting_by_queue": {"amd_mi300_1": 1},
        "running_by_queue": {"amd_mi300_1": 1},
        "ledger": {"waiting": 1, "running": 1},
        "count_basis": {
            "waiting": "exact_pipeline_active_job_ledger",
            "running": "exact_pipeline_active_job_ledger",
        },
        "attribution": {
            "waiting_supported": True,
            "running_supported": True,
            "waiting_observed": 2,
            "running_observed": 1,
            "waiting_attributed": 2,
            "running_attributed": 1,
            "waiting_total": 2,
            "running_total": 3,
            "waiting_attribution": "complete",
            "running_attribution": "partial",
        },
    }
    omni_history = payload["omni"]["history"]
    assert omni_history["summary"] == {
        "snapshot_count": 1,
        "first_observed_at": "2026-04-22T10:05:00Z",
        "last_observed_at": "2026-04-22T10:05:00Z",
        "complete_waiting_snapshot_count": 1,
        "complete_running_snapshot_count": 0,
    }
    assert omni_history["points"][0]["amd"] == {
        "waiting_supported": True,
        "running_supported": True,
        "waiting_observed": 2,
        "running_observed": 1,
        "waiting_attributed": 2,
        "running_attributed": 1,
        "waiting_total": 2,
        "running_total": 3,
        "waiting_attribution": "complete",
        "running_attribution": "partial",
    }
    assert payload["omni"]["provenance"]["source_paths"] == {
        "queue_aggregates": "queue_timeseries.jsonl",
        "queue_jobs": "queue_jobs.json",
        "heuristic": "omni_surge_heuristic.json",
        "issue_state": "open_omni_surge_issues.json",
        "mapping_history": "workload_mapping.json",
    }
    assert payload["trajectory"]["provenance"]["source_paths"] == {
        "build_history": "analytics.json",
        "group_changes": "group_changes.json",
        "capacity": "capacity_monitor.json",
        "target_topology": "amd_test_matrix.json",
        "historical_load": "workload_mapping.json",
        "queue_history": "queue_timeseries.jsonl",
    }
    assert payload["trajectory"]["source_pipeline"] == "ci"
    assert payload["trajectory"]["pipeline_order"] == ["ci"]
    assert [row["pipeline"] for row in payload["trajectory"]["pipelines"]] == ["ci"]
    assert payload["trajectory"]["pipelines"][0]["source_key"] == "ci.all_main_reliability"
    assert all("timestamp" in source for source in payload["sources"].values())


def test_exact_capacity_projection_expands_parallelism_and_queue_width():
    capacity = {
        "projection": {
            "target_groups": 2,
            "declared_existing_groups": 1,
            "declared_new_groups": 1,
            "projected_total_gpus": 4,
        },
        "summary": {
            "capacity_scoped_group_count": 1,
            "capacity": {
                "future_eligible": {
                    "queue_count": 2,
                    "concurrent_jobs": 21,
                    "gpus": 28,
                    "eight_gpu_node_equivalents": 3.5,
                },
                "retiring": {"gpus": 220},
            },
        },
        "queues": [
            {
                "id": "amd_mi300_1",
                "label": "mi300_1",
                "family": "MI300",
                "gpus_per_job": 1,
                "future_max_concurrent_jobs": 20,
                "future_gpu_capacity": 20,
                "capacity_eligible": True,
                "gated_groups": 1,
                "gated_jobs": 2,
            },
            {
                "id": "amd_mi300_8",
                "label": "mi300_8",
                "family": "MI300",
                "gpus_per_job": 8,
                "future_max_concurrent_jobs": 1,
                "future_gpu_capacity": 8,
                "capacity_eligible": True,
                "gated_groups": 0,
                "gated_jobs": 0,
            },
            {
                "id": "amd_mi325_1",
                "label": "mi325_1",
                "family": "MI325",
                "gpus_per_job": 1,
                "max_concurrent_jobs": 188,
                "gpu_capacity": 188,
                "capacity_eligible": False,
                "lifecycle": "retiring",
                "gated_groups": 0,
                "gated_jobs": 0,
            },
        ],
    }
    matrix = {
        "source": {
            "latest_build_number": 7,
            "latest_build_date": "2026-04-22",
        },
        "rows": [
            {
                "id": "one",
                "cells": {
                    "mi300": {
                        "exists": True,
                        "variants": [{
                            "agent_pool": "mi300_1",
                            "parallelism": 2,
                            "latest_url": "https://buildkite.com/vllm/amd-ci/builds/7/steps/canvas?sid=step-one",
                        }],
                    },
                },
            },
            {
                "id": "eight",
                "cells": {
                    "mi300": {
                        "exists": True,
                        "variants": [{
                            "agent_pool": "mi300_8",
                            "parallelism": 2,
                            "latest_url": "https://buildkite.com/vllm/amd-ci/builds/7/steps/canvas?sid=step-eight",
                        }],
                    },
                },
            },
        ],
    }
    amd_analytics = {
        "builds": [{
            "number": 7,
            "date": "2026-04-22",
            "jobs": [
                {"name": "one-1", "q": "amd_mi300_1", "step_id": "step-one", "wall_completion_mins": 10},
                {"name": "one-2", "q": "amd_mi300_1", "step_id": "step-one", "wall_completion_mins": 20},
                {"name": "eight-1", "q": "amd_mi300_8", "step_id": "step-eight", "wall_completion_mins": 30},
                {"name": "eight-2", "q": "amd_mi300_8", "step_id": "step-eight", "wall_completion_mins": 40},
            ],
        }],
    }

    projection = ops._exact_target_topology(capacity, matrix, amd_analytics)

    assert projection["groups"] == 2
    assert projection["jobs"] == 4
    assert projection["gpu_slots"] == 18
    assert projection["scenarios"][0]["shape_gap_gpus"] == 8
    assert projection["scenarios"][0]["queue_gaps"][0]["id"] == "amd_mi300_8"
    assert projection["scenarios"][1]["family_gap_gpus"] == 8
    recommendation = projection["recommendation"]
    assert recommendation["net_new_hardware_required_for_one_suite"] is None
    assert (
        recommendation["overall_hardware_requirement"]
        == "indeterminate_until_mi325_destination_modeled"
    )
    assert recommendation["mi325_migration_unplaced"] is True
    assert recommendation["conditional_on_mi325_destination"] is True
    assert (
        recommendation["standalone_target_only"]["net_new_hardware_required"]
        is False
    )
    assert recommendation["standalone_target_only"]["shape_gap_gpus"] == 8
    assert "standalone target suite does not require net-new silicon" in (
        recommendation["standalone_target_only"]["summary"]
    )
    assert "Overall hardware need is indeterminate" in recommendation["summary"]
    assert projection["recommendation"]["repartition_possible_within_family"] is True
    assert projection["runtime_estimate"]["selected_jobs"] == 4
    assert projection["runtime_estimate"]["median_agent_hours"] == 1.67
    assert projection["runtime_estimate"]["median_gpu_hours"] == 9.83
    strategy = {
        row["id"]: row
        for row in projection["placement_profiles"]["strategies"]
    }["mi355_preferred"]
    strategy_queues = {row["id"]: row for row in strategy["queues"]}
    assert strategy_queues["amd_mi300_1"]["service_minutes"] == 15
    assert strategy_queues["amd_mi300_8"]["service_minutes"] == 35.1
    assert (
        strategy_queues["amd_mi300_1"]["service_minutes_source"]
        == "placement_strategy_target_command_job_median_average"
    )
    assert projection["current_topology"] == {
        "groups": 1,
        "jobs": 2,
        "gpu_slots": 2,
        "agent_minutes": 30.0,
    }
    projected_queues = {row["id"]: row for row in projection["queues"]}
    assert projected_queues["amd_mi300_1"]["current_gated_groups"] == 1
    assert projected_queues["amd_mi300_1"]["current_gated_jobs"] == 2
    assert projected_queues["amd_mi300_1"]["current_gated_gpu_slots"] == 2
    assert projected_queues["amd_mi300_8"]["current_gated_gpu_slots"] == 0


def test_capacity_projection_reallocates_multiple_same_family_queue_gaps():
    capacity = {
        "projection": {"target_groups": 2},
        "summary": {
            "capacity_scoped_group_count": 0,
            "capacity": {
                "future_eligible": {
                    "queue_count": 3,
                    "concurrent_jobs": 14,
                    "gpus": 24,
                    "eight_gpu_node_equivalents": 3,
                },
                "retiring": {"gpus": 0},
            },
        },
        "queues": [
            {
                "id": "amd_mi300_1",
                "label": "mi300_1",
                "family": "MI300",
                "gpus_per_job": 1,
                "future_max_concurrent_jobs": 12,
                "future_gpu_capacity": 12,
                "capacity_eligible": True,
            },
            {
                "id": "amd_mi300_4",
                "label": "mi300_4",
                "family": "MI300",
                "gpus_per_job": 4,
                "future_max_concurrent_jobs": 1,
                "future_gpu_capacity": 4,
                "capacity_eligible": True,
            },
            {
                "id": "amd_mi300_8",
                "label": "mi300_8",
                "family": "MI300",
                "gpus_per_job": 8,
                "future_max_concurrent_jobs": 1,
                "future_gpu_capacity": 8,
                "capacity_eligible": True,
            },
        ],
    }
    matrix = {
        "rows": [
            {
                "id": "four",
                "cells": {
                    "mi300": {
                        "exists": True,
                        "variants": [{"agent_pool": "mi300_4", "parallelism": 2}],
                    },
                },
            },
            {
                "id": "eight",
                "cells": {
                    "mi300": {
                        "exists": True,
                        "variants": [{"agent_pool": "mi300_8", "parallelism": 2}],
                    },
                },
            },
        ],
    }

    projection = ops._exact_target_topology(capacity, matrix)

    one_suite = projection["scenarios"][0]
    assert one_suite["fits_aggregate_capacity"] is True
    assert one_suite["fits_family_capacity"] is True
    assert one_suite["fits_queue_shapes"] is False
    assert one_suite["shape_gap_gpus"] == 12
    assert len(one_suite["queue_gaps"]) == 2
    recommendation = projection["recommendation"]
    assert recommendation["net_new_hardware_required_for_one_suite"] is False
    assert recommendation["repartition_possible_within_family"] is True
    assert recommendation["additional_runner_jobs"] == 2
    assert recommendation["additional_runner_gpus"] == 12
    assert len(recommendation["queue_reallocations"]) == 2
    assert {
        row["family_spare_gpus"]
        for row in recommendation["queue_reallocations"]
    } == {12}
    assert "does not require net-new silicon" in recommendation["summary"]


def test_capacity_projection_publishes_exact_mi355_preferred_placement():
    queues = []
    for architecture, widths in {
        "mi250": (1, 4),
        "mi300": (1, 4, 8),
        "mi355": (1, 4),
    }.items():
        for width in widths:
            queues.append({
                "id": f"amd_{architecture}_{width}",
                "label": f"{architecture}_{width}",
                "family": architecture.upper(),
                "gpus_per_job": width,
                "max_concurrent_jobs": 1000,
                "gpu_capacity": 1000 * width,
                "capacity_eligible": True,
            })
    capacity = {
        "summary": {
            "capacity_scoped_group_count": 0,
            "capacity": {
                "future_eligible": {
                    "concurrent_jobs": 7000,
                    "gpus": 21000,
                },
            },
        },
        "queues": queues,
    }

    def cell(pool: str, parallelism: int = 1) -> dict:
        return {
            "exists": True,
            "variants": [{
                "agent_pool": pool,
                "parallelism": parallelism,
            }],
        }

    matrix_rows = []
    for index in range(21):
        pool = "mi250_4" if index == 20 else "mi250_1"
        parallelism = 2 if index < 5 else 1
        matrix_rows.append({
            "id": f"mi250-{index}",
            "cells": {"mi250": cell(pool, parallelism)},
        })
    for index in range(38):
        if index < 4:
            preferred = cell("mi355_4")
        elif index < 11:
            preferred = cell("mi355_1", 2)
        else:
            preferred = cell("mi355_1")
        matrix_rows.append({
            "id": f"mi355-{index}",
            "cells": {
                "mi355": preferred,
                "mi300": cell("mi300_1"),
            },
        })
    for index in range(101):
        if index < 14:
            selected = cell("mi300_8")
        elif index == 14:
            selected = cell("mi300_4")
        else:
            selected = cell("mi300_1", 2 if index < 39 else 1)
        matrix_rows.append({
            "id": f"mi300-{index}",
            "cells": {"mi300": selected},
        })
    matrix = {"rows": matrix_rows}

    projection = ops._exact_target_topology(capacity, matrix)

    assert projection["architecture_precedence"] == ["mi250", "mi355", "mi300"]
    profiles = projection["placement_profiles"]
    assert profiles["default_strategy_id"] == "mi355_preferred"
    assert profiles["configurable"] is True
    strategies = {row["id"]: row for row in profiles["strategies"]}
    preferred = strategies["mi355_preferred"]
    assert preferred["totals"] == {
        "groups": 160,
        "jobs": 196,
        "gpu_slots": 312,
    }
    assert [
        (row["family"], row["groups"], row["jobs"], row["gpu_slots"])
        for row in preferred["families"]
    ] == [
        ("MI250", 21, 26, 29),
        ("MI300", 101, 125, 226),
        ("MI355", 38, 45, 57),
    ]
    assert preferred["coverage"]["architecture_definitions"]["mi355"] == 38
    assert preferred["coverage"]["complete"] is True
    assert "Only 38/160 semantic groups" in preferred["limitation"]
    current = strategies["current_definition_precedence"]
    assert current["coverage"]["selected_groups_by_architecture"] == {
        "mi250": 21,
        "mi300": 139,
        "mi355": 0,
    }
    assert [
        (row["family"], row["groups"], row["jobs"], row["gpu_slots"])
        for row in projection["families"]
    ] == [
        ("MI250", 21, 26, 29),
        ("MI300", 101, 125, 226),
        ("MI355", 38, 45, 57),
    ]

    legacy_projection = ops._exact_target_topology(
        capacity,
        matrix,
        architecture_preference=["mi250", "mi300", "mi355"],
    )
    assert (
        legacy_projection["placement_profiles"]["default_strategy_id"]
        == "current_definition_precedence"
    )
    assert legacy_projection["families"][1]["groups"] == 139


def test_target_runtime_estimate_uses_configured_architecture_preference():
    catalog = {
        "amd_mi300_1": {
            "id": "amd_mi300_1",
            "gpus_per_job": 1,
            "capacity_eligible": True,
        },
        "amd_mi355_1": {
            "id": "amd_mi355_1",
            "gpus_per_job": 1,
            "capacity_eligible": True,
        },
    }
    matrix = {
        "source": {
            "latest_build_number": 7,
            "latest_build_date": "2026-04-22",
        },
        "rows": [{
            "id": "dual-defined",
            "cells": {
                "mi300": {
                    "exists": True,
                    "variants": [{
                        "agent_pool": "mi300_1",
                        "latest_url": (
                            "https://buildkite.com/vllm/amd-ci/builds/7/"
                            "steps/canvas?sid=step-mi300"
                        ),
                    }],
                },
                "mi355": {
                    "exists": True,
                    "variants": [{
                        "agent_pool": "mi355_1",
                        "latest_url": (
                            "https://buildkite.com/vllm/amd-ci/builds/7/"
                            "steps/canvas?sid=step-mi355"
                        ),
                    }],
                },
            },
        }],
    }
    analytics_payload = {
        "builds": [{
            "number": 7,
            "date": "2026-04-22",
            "jobs": [{
                "name": "mi300 job",
                "q": "amd_mi300_1",
                "step_id": "step-mi300",
                "wall_completion_mins": 30,
            }, {
                "name": "mi355 job",
                "q": "amd_mi355_1",
                "step_id": "step-mi355",
                "wall_completion_mins": 10,
            }],
        }],
    }

    preferred = ops._target_runtime_estimate(
        matrix,
        analytics_payload,
        catalog,
    )
    legacy = ops._target_runtime_estimate(
        matrix,
        analytics_payload,
        catalog,
        architecture_preference=["mi250", "mi300", "mi355"],
    )

    assert list(preferred["queues"]) == ["amd_mi355_1"]
    assert preferred["median_agent_hours"] == 0.17
    assert list(legacy["queues"]) == ["amd_mi300_1"]
    assert legacy["median_agent_hours"] == 0.5


def test_capacity_simulation_profile_publishes_source_backed_wait_inputs():
    capacity = {
        "summary": {"capacity_scoped_group_count": 1},
        "queues": [
            {
                "id": "amd_mi300_1",
                "gated_groups": 1,
                "gated_jobs": 2,
            },
            {
                "id": "amd_mi300_8",
                "gated_groups": 0,
                "gated_jobs": 0,
            },
        ],
    }
    target_queues = [
        {
            "id": "amd_mi300_1",
            "label": "mi300_1",
            "family": "MI300",
            "provider": "Example provider",
            "gpus_per_job": 1,
            "max_concurrent_jobs": 20,
            "groups": 1,
            "jobs": 2,
        },
        {
            "id": "amd_mi300_8",
            "label": "mi300_8",
            "family": "MI300",
            "gpus_per_job": 8,
            "max_concurrent_jobs": 1,
            "groups": 1,
            "jobs": 2,
        },
    ]
    runtime = {
        "sampled_jobs": 4,
        "median_agent_hours": 2,
        "queues": {
            "amd_mi300_1": {
                "sampled_jobs": 2,
                "median_agent_hours": 1,
            },
            "amd_mi300_8": {
                "sampled_jobs": 2,
                "median_agent_hours": 1,
            },
        },
    }
    mapping = {
        "generated_at": "2026-04-22T12:00:00Z",
        "window": {
            "days": 1,
            "start_date": "2026-04-22",
            "end_date": "2026-04-22",
            "complete": True,
            "lower_bound": False,
        },
        "totals": {
            "main": {
                "by_queue": {
                    "amd_mi300_1": {
                        "mapped_jobs": 12,
                        "started_jobs": 6,
                        "finished_jobs": 6,
                        "mapped_gpu_slots": 12,
                        "gpu_hours": 6,
                    },
                },
            },
            "omni": {"by_queue": {}},
        },
        "hourly": [
            {
                "hour": "2026-04-22T10:00:00Z",
                "end_exclusive": "2026-04-22T11:00:00Z",
                "observed_through": "2026-04-22T11:00:00Z",
                "workloads": {
                    "main": {
                        "by_queue": {
                            "amd_mi300_1": {"started_jobs": 4},
                        },
                    },
                },
            },
            {
                "hour": "2026-04-22T11:00:00Z",
                "end_exclusive": "2026-04-22T12:00:00Z",
                "observed_through": "2026-04-22T12:00:00Z",
                "workloads": {
                    "omni": {
                        "by_queue": {
                            "amd_mi300_1": {"started_jobs": 2},
                        },
                    },
                },
            },
        ],
    }
    history = [
        {
            "ts": "2026-04-22T10:00:00Z",
            "queues": {
                "amd_mi300_1": {"running": 1, "waiting": 0},
                "amd_mi300_8": {"running": 0, "waiting": 0},
            },
        },
        {
            "ts": "2026-04-22T11:00:00Z",
            "queues": {
                "amd_mi300_1": {"running": 3, "waiting": 2},
                "amd_mi300_8": {"running": 0, "waiting": 0},
            },
        },
        {
            "ts": "2026-04-22T12:00:00Z",
            "queues": {
                "amd_mi300_1": {"running": 25, "waiting": 10},
                "amd_mi300_8": {"running": 0, "waiting": 0},
            },
        },
    ]

    profile = ops._capacity_simulation_profile(
        capacity,
        target_queues,
        runtime,
        mapping,
        history,
    )

    assert profile["defaults"]["baseline"] == "peak"
    assert profile["workload_window"]["elapsed_hours"] == 12
    assert profile["topology"]["current"] == {
        "groups": 1,
        "jobs": 2,
        "gpu_slots": 2,
        "agent_minutes": 60.0,
    }
    assert profile["topology"]["target"] == {
        "groups": 2,
        "jobs": 4,
        "gpu_slots": 18,
        "agent_minutes": 120.0,
    }
    assert profile["topology"]["delta"]["gpu_slots"] == 16
    rows = {row["id"]: row for row in profile["queues"]}
    one_gpu = rows["amd_mi300_1"]
    assert one_gpu["provider"] == "Example provider"
    assert "amd-cpu" in profile["assumptions"]["capacity"]
    assert "Docker builds" in profile["assumptions"]["capacity"]
    assert one_gpu["history"]["sample_count"] == 3
    assert one_gpu["history"]["current"]["running"] == 25
    assert one_gpu["history"]["current"]["waiting"] == 10
    assert one_gpu["history"]["current"]["available_slots"] == 0
    assert one_gpu["history"]["current"]["above_configured_capacity"] is True
    assert one_gpu["history"]["snapshots_above_configured_capacity"] == 1
    assert one_gpu["history"]["typical"]["running"] == 3
    assert one_gpu["history"]["typical"]["waiting"] == 2
    assert one_gpu["history"]["peak"]["running"] == 25
    assert one_gpu["history"]["peak"]["waiting"] == 10
    assert one_gpu["history"]["stress"]["observed_at"] == "2026-04-22T12:00:00Z"
    assert one_gpu["history"]["marginal"]["peak"]["running"] == 22.8
    assert one_gpu["history"]["marginal"]["peak"]["waiting"] == 9.2
    assert (
        profile["history"]["joint_baselines"]["peak"]["observed_at"]
        == one_gpu["history"]["peak"]["observed_at"]
    )
    assert profile["integrity"]["quota_drift_detected"] is True
    assert profile["integrity"]["queue"]["affected_queue_count"] == 1
    assert one_gpu["workload"]["mapped_arrival_rate_jobs_per_hour"] == 1
    assert one_gpu["workload"]["started_arrival_rate_jobs_per_hour"] == 0.5
    assert one_gpu["workload"]["weekday_started_cohort_jobs"] == 6
    assert (
        one_gpu["workload"]["weekday_started_cohort_rate_jobs_per_hour"]
        == 3
    )
    assert (
        profile["defaults"]["arrival_rate_jobs_field"]
        == "weekday_started_cohort_rate_jobs_per_hour"
    )
    assert one_gpu["workload"]["observed_service_minutes"] == 60
    assert one_gpu["workload"]["target_runtime_service_minutes"] == 30
    assert one_gpu["workload"]["service_minutes"] == 30
    assert (
        one_gpu["workload"]["service_minutes_source"]
        == "target_command_job_median_average"
    )
    assert one_gpu["workload"]["service_minutes_is_proxy"] is False
    eight_gpu = rows["amd_mi300_8"]
    assert eight_gpu["history"]["current"]["available"] is True
    assert eight_gpu["history"]["current"]["running"] == 0
    assert eight_gpu["history"]["current"]["waiting"] == 0
    assert eight_gpu["workload"]["observed_service_minutes"] is None
    assert eight_gpu["workload"]["service_minutes"] == 30
    assert (
        eight_gpu["workload"]["service_minutes_source"]
        == "target_command_job_median_average"
    )
    assert profile["model"]["kind"] == "planning_estimate_inputs_not_sla"
    assert "FCFS list scheduling per queue as a planning estimate" in (
        profile["model"]["burst_wait"]
    )
    assert "Only the full-service residual" in profile["model"]["burst_wait"]
    assert "conservative FCFS" not in profile["model"]["burst_wait"]
    assert "rho>=1" in profile["model"]["steady_wait"]
    assert "not an SLA" in profile["model"]["steady_wait_assumptions"]
    assert "primary service-time input" in profile["assumptions"]["service"]
    assert "used only as a fallback" in profile["assumptions"]["service"]
    assert profile["provenance"]["queue_history"] == "queue_timeseries.jsonl"


def test_capacity_joint_history_uses_real_weekday_snapshots_and_observed_stress():
    queue_rows = [
        {
            "id": "amd_mi300_1",
            "family": "MI300",
            "gpus_per_job": 1,
            "capacity_jobs": 5,
        },
        {
            "id": "amd_mi300_4",
            "family": "MI300",
            "gpus_per_job": 4,
            "capacity_jobs": 1,
        },
    ]
    history = [{
        "ts": "2026-04-04T12:00:00Z",
        "queues": {
            "amd_mi300_1": {"running": 1000, "waiting": 0},
            "amd_mi300_4": {"running": 0, "waiting": 0},
        },
    }, {
        "ts": "2026-04-08T12:00:00Z",
        "queues": {
            "amd_mi300_1": {"running": 1, "waiting": 0},
        },
    }]
    for pressure in range(1, 21):
        history.append({
            "ts": f"2026-04-09T{pressure - 1:02d}:00:00Z",
            "queues": {
                "amd_mi300_1": {
                    "running": min(pressure, 5),
                    "waiting": max(0, pressure - 5),
                    "connected_agents": pressure,
                    "connected_agents_source": "queue_native_metrics",
                    "metrics_ts": f"2026-04-09T{pressure - 1:02d}:00:00Z",
                },
                "amd_mi300_4": {"running": 0, "waiting": 0},
            },
        })
    history.extend([{
        "ts": "2026-04-10T20:00:00Z",
        "queues": {
            "amd_mi300_1": {
                "running": 4,
                "waiting": 46,
                "connected_agents": 7,
                "connected_agents_source": "queue_native_metrics",
                "metrics_ts": "2026-04-10T20:00:00Z",
            },
            "amd_mi300_4": {"running": 0, "waiting": 0},
        },
    }, {
        "ts": "2026-04-10T23:00:00Z",
        "queues": {
            "amd_mi300_1": {
                "running": 6,
                "waiting": 94,
                "connected_agents": 3,
                "connected_agents_source": "queue_native_metrics",
                "metrics_ts": "2026-04-10T22:59:00Z",
            },
            "amd_mi300_4": {"running": 1, "waiting": 0},
        },
    }])

    published, selected, observations = ops._capacity_joint_history(
        queue_rows,
        history,
    )

    window = published["analysis_window"]
    assert window["start_at"] == "2026-04-03T23:00:00Z"
    assert window["end_at"] == "2026-04-10T23:00:00Z"
    assert window["expected_weekday_hours"] == 120
    assert window["weekend_snapshot_count_excluded"] == 1
    assert window["incomplete_snapshot_count"] == 1
    assert window["complete_snapshot_count"] == 22
    assert window["missing_weekday_dates"] == [
        "2026-04-03",
        "2026-04-06",
        "2026-04-07",
    ]
    assert window["weekday_date_coverage_complete"] is False
    baselines = published["joint_baselines"]
    assert baselines["typical"]["total_pressure_gpu_slots"] == 11
    assert baselines["typical"]["observed_at"] == "2026-04-09T10:00:00Z"
    assert baselines["peak"]["total_pressure_gpu_slots"] == 50
    assert baselines["peak"]["observed_at"] == "2026-04-10T20:00:00Z"
    assert baselines["stress"]["total_pressure_gpu_slots"] == 104
    assert baselines["stress"]["observed_at"] == "2026-04-10T23:00:00Z"
    assert (
        selected["stress"]["by_queue"]["amd_mi300_1"]["connected_agents_source"]
        == "queue_native_metrics"
    )

    queue_history = ops._capacity_history_baseline(
        "amd_mi300_1",
        5,
        history,
        joint_snapshots=selected,
        joint_observations=observations,
    )
    assert queue_history["typical"]["observed_at"] == baselines["typical"]["observed_at"]
    assert queue_history["peak"]["running"] == 4
    assert queue_history["peak"]["waiting"] == 46
    assert queue_history["stress"]["running"] == 6
    assert queue_history["stress"]["waiting"] == 94
    assert queue_history["stress"]["connected_agents"] == 3
    assert (
        queue_history["stress"]["connected_agents_source"]
        == "queue_native_metrics"
    )
    assert queue_history["stress"]["metrics_timestamp"] == "2026-04-10T22:59:00Z"

    integrity = ops._capacity_quota_integrity(
        queue_rows,
        observations,
        window,
    )
    assert integrity["quota_drift_detected"] is True
    assert integrity["queue"]["affected_queue_count"] == 1
    assert integrity["family"]["affected_family_count"] == 1
    queue_violation = integrity["queue"]["violations"][0]
    assert queue_violation["maximum_running_occupancy_gpu_slots"] == 6
    assert queue_violation["waiting_demand_gpu_slots_at_maximum"] == 94
    family_violation = integrity["family"]["violations"][0]
    assert family_violation["maximum_running_occupancy_gpu_slots"] == 10
    assert family_violation["waiting_demand_gpu_slots_at_maximum"] == 94
    connected = integrity["connected_agents"]
    assert connected["queue_count"] == 2
    assert connected["available_queue_count"] == 1
    assert connected["unavailable_queue_count"] == 1
    assert connected["mismatch_queue_count"] == 1
    connected_rows = {row["id"]: row for row in connected["queues"]}
    one_gpu_agents = connected_rows["amd_mi300_1"]
    assert one_gpu_agents["configured_capacity_jobs"] == 5
    assert one_gpu_agents["latest_connected_agents"] == 3
    assert one_gpu_agents["signed_delta_jobs"] == -2
    assert one_gpu_agents["direction"] == "below_planning_quota"
    assert one_gpu_agents["source"] == "queue_native_metrics"
    assert one_gpu_agents["metrics_timestamp"] == "2026-04-10T22:59:00Z"
    assert one_gpu_agents["max_connected_agents_in_window"] == 20
    assert one_gpu_agents["planning_capacity_preserved"] is True
    assert connected_rows["amd_mi300_4"]["available"] is False


def test_weekday_started_cohort_rate_handles_weekend_and_partial_hour():
    workload_mapping = {
        "generated_at": "2026-04-22T12:31:00Z",
        "hourly": [{
            "hour": "2026-04-15T12:00:00Z",
            "end_exclusive": "2026-04-15T13:00:00Z",
            "observed_through": "2026-04-15T13:00:00Z",
            "workloads": {
                "main": {
                    "by_queue": {"amd_mi300_1": {"started_jobs": 99}},
                },
            },
        }, {
            "hour": "2026-04-16T10:00:00Z",
            "end_exclusive": "2026-04-16T11:00:00Z",
            "observed_through": "2026-04-16T11:00:00Z",
            "workloads": {
                "main": {
                    "by_queue": {"amd_mi300_1": {"started_jobs": 3}},
                },
                "omni": {
                    "by_queue": {"amd_mi300_1": {"started_jobs": 1}},
                },
            },
        }, {
            "hour": "2026-04-18T10:00:00Z",
            "end_exclusive": "2026-04-18T11:00:00Z",
            "observed_through": "2026-04-18T11:00:00Z",
            "workloads": {
                "main": {
                    "by_queue": {"amd_mi300_1": {"started_jobs": 100}},
                },
            },
        }, {
            "hour": "2026-04-22T12:00:00Z",
            "end_exclusive": "2026-04-22T13:00:00Z",
            "observed_through": "2026-04-22T12:30:00Z",
            "open": True,
            "partial": True,
            "workloads": {
                "main": {
                    "by_queue": {"amd_mi300_1": {"started_jobs": 1}},
                },
            },
        }],
    }
    metadata, counts = ops._weekday_started_cohort_rates(
        workload_mapping,
        {
            "start_at": "2026-04-15T12:30:00Z",
            "end_at": "2026-04-22T12:30:00Z",
            "expected_weekday_hours": 120,
        },
        {"amd_mi300_1"},
    )

    assert counts["amd_mi300_1"] == 5
    assert metadata["elapsed_weekday_hours"] == 1.5
    assert metadata["leading_boundary_bucket_count_excluded"] == 1
    assert metadata["weekend_hour_bucket_count_excluded"] == 1
    assert metadata["partial_hour_bucket_count"] == 1
    assert metadata["timestamp_field"] == "job.created_at_hour"
    assert "not a count of started_at events" in metadata["semantics"]
    assert round(counts["amd_mi300_1"] / metadata["elapsed_weekday_hours"], 4) == 3.3333


def test_capacity_simulation_profile_keeps_missing_inputs_explicit():
    profile = ops._capacity_simulation_profile(
        {"queues": [], "summary": {}},
        [{
            "id": "amd_mi355_8",
            "label": "mi355_8",
            "family": "MI355",
            "gpus_per_job": 8,
            "max_concurrent_jobs": 1,
            "groups": 0,
            "jobs": 0,
        }],
        {},
        {},
        [],
    )

    row = profile["queues"][0]
    assert profile["history"]["snapshot_count"] == 0
    assert profile["workload_window"]["elapsed_hours"] == 0
    assert row["history"]["current"] == {
        "kind": "latest_joint_snapshot",
        "available": False,
        "running": None,
        "waiting": None,
        "available_slots": None,
        "utilization_pct": None,
        "saturated": None,
    }
    assert row["history"]["typical"]["available"] is False
    assert row["history"]["peak"]["available"] is False
    assert row["workload"]["mapped_arrival_rate_jobs_per_hour"] is None
    assert row["workload"]["started_arrival_rate_jobs_per_hour"] is None
    assert row["workload"]["observed_service_minutes"] is None
    assert row["workload"]["target_runtime_service_minutes"] is None
    assert row["workload"]["target_global_service_minutes"] is None
    assert row["workload"]["runtime_fallback_service_minutes"] is None
    assert row["workload"]["service_minutes"] is None
    assert row["workload"]["service_minutes_source"] == "unavailable"
    assert row["workload"]["service_minutes_is_proxy"] is None


def test_capacity_service_uses_completed_mapping_proxy_only_as_fallback():
    profile = ops._capacity_simulation_profile(
        {
            "summary": {"capacity_scoped_group_count": 0},
            "queues": [{"id": "amd_mi355_1"}],
        },
        [{
            "id": "amd_mi355_1",
            "label": "mi355_1",
            "family": "MI355",
            "gpus_per_job": 1,
            "max_concurrent_jobs": 48,
            "groups": 1,
            "jobs": 1,
        }],
        {},
        {
            "generated_at": "2026-04-22T12:00:00Z",
            "window": {"days": 1, "start_date": "2026-04-22"},
            "totals": {
                "main": {
                    "by_queue": {
                        "amd_mi355_1": {
                            "finished_jobs": 2,
                            "gpu_hours": 4,
                        },
                    },
                },
            },
        },
        [],
    )

    workload = profile["queues"][0]["workload"]
    assert workload["observed_service_minutes"] == 120
    assert workload["service_minutes"] == 120
    assert (
        workload["service_minutes_source"]
        == "completed_agent_minutes_per_finished_job_proxy_fallback"
    )
    assert workload["service_minutes_is_proxy"] is True


def test_capacity_profile_publishes_mi325_as_unplaced_without_inference():
    capacity = {
        "summary": {"capacity_scoped_group_count": 1},
        "queues": [
            {
                "id": "amd_mi300_1",
                "family": "MI300",
                "gpus_per_job": 1,
                "max_concurrent_jobs": 20,
                "capacity_eligible": True,
                "lifecycle": "active",
                "gated_groups": 1,
                "gated_jobs": 1,
            },
            {
                "id": "amd_mi325_2",
                "label": "mi325_2",
                "family": "MI325",
                "gpus_per_job": 2,
                "max_concurrent_jobs": 8,
                "gpu_capacity": 16,
                "capacity_eligible": False,
                "lifecycle": "retiring",
            },
        ],
    }
    mapping = {
        "generated_at": "2026-04-23T00:00:00Z",
        "window": {
            "days": 14,
            "start_date": "2026-04-09",
            "end_date": "2026-04-22",
            "complete": True,
            "lower_bound": False,
            "job_created_range_exhaustive": False,
        },
        "scope": {
            "attribution": {
                "parent_build_lookback_days": 3,
                "exact_within_declared_source_window": True,
                "limitation": (
                    "Jobs added to parent builds older than the lookback can be absent."
                ),
            },
        },
        "totals": {
            "main": {
                "by_queue": {
                    "amd_mi325_2": {
                        "mapped_jobs": 3,
                        "started_jobs": 2,
                        "finished_jobs": 2,
                        "mapped_gpu_slots": 6,
                        "gpu_hours": 10,
                    },
                },
            },
            "omni": {
                "by_queue": {
                    "amd_mi325_2": {
                        "mapped_jobs": 1,
                        "started_jobs": 1,
                        "finished_jobs": 1,
                        "mapped_gpu_slots": 2,
                        "gpu_hours": 2,
                    },
                },
            },
        },
    }
    history = [
        {
            "ts": "2026-04-22T10:00:00Z",
            "queues": {"amd_mi325_2": {"running": 1, "waiting": 1}},
        },
        {
            "ts": "2026-04-22T11:00:00Z",
            "queues": {"amd_mi325_2": {"running": 2, "waiting": 2}},
        },
        {
            "ts": "2026-04-22T12:00:00Z",
            "queues": {"amd_mi325_2": {"running": 4, "waiting": 3}},
        },
    ]
    profile = ops._capacity_simulation_profile(
        capacity,
        [{
            "id": "amd_mi300_1",
            "label": "mi300_1",
            "family": "MI300",
            "gpus_per_job": 1,
            "max_concurrent_jobs": 20,
            "groups": 1,
            "jobs": 1,
        }],
        {},
        mapping,
        history,
    )

    unplaced = profile["unplaced_retiring_workload"]
    assert unplaced["status"] == "unplaced"
    assert unplaced["compatibility"] == "unknown"
    assert unplaced["requires_manual_destination"] is True
    assert unplaced["excluded_from_wait_and_headroom"] is True
    assert unplaced["window"] == {
        "days": 14,
        "start_date": "2026-04-09",
        "end_date": "2026-04-22",
        "elapsed_hours": 336.0,
        "complete": True,
        "lower_bound": False,
        "job_created_range_exhaustive": False,
        "exact_within_declared_source_window": True,
        "parent_build_lookback_days": 3,
        "source_limitation": (
            "Jobs added to parent builds older than the lookback can be absent."
        ),
    }
    assert unplaced["totals"] == {
        "mapped_jobs": 4,
        "started_jobs": 3,
        "finished_jobs": 3,
        "mapped_gpu_slots": 8,
        "gpu_hours": 12.0,
        "average_gpus": 0.04,
    }
    assert unplaced["by_workload"]["main"]["mapped_jobs"] == 3
    assert unplaced["by_workload"]["omni"]["mapped_jobs"] == 1
    assert unplaced["occupancy"]["current"]["running_gpu_slots"] == 8.0
    assert unplaced["occupancy"]["current"]["waiting_gpu_slots"] == 6.0
    assert unplaced["occupancy"]["typical"]["running_gpu_slots"] == 4.0
    assert unplaced["occupancy"]["peak"]["running_gpu_slots"] == 8.0
    assert unplaced["occupancy"]["stress"]["running_gpu_slots"] == 8.0
    assert (
        unplaced["occupancy"]["peak"]["observed_at"]
        == unplaced["occupancy"]["joint_baselines"]["peak"]["observed_at"]
    )
    assert unplaced["occupancy"]["peak"]["waiting_gpu_slots"] == 6.0
    assert "No cross-family or queue-width compatibility is inferred" in unplaced["reason"]


def test_omni_history_keeps_observed_counts_and_coverage_without_inference():
    history = [{
        "ts": "2026-04-22T10:00:00Z",
        "queues": {
            "amd_mi300_1": {
                "waiting": 3,
                "running": 2,
                "waiting_by_workload": {"omni": 2, "vllm": 1},
                "running_by_workload": {"omni": 1},
            },
            "gpu_4_queue": {
                "waiting": 5,
                "running": 1,
                "waiting_by_workload": {"omni": 1, "vllm": 2},
                "running_by_workload": {"omni": 0, "vllm": 1},
            },
            "amd_mi355b_1": {
                "waiting": 99,
                "running": 99,
                "waiting_by_workload": {"omni": 99},
                "running_by_workload": {"omni": 99},
            },
        },
    }, {
        "ts": "2026-04-22T11:00:00Z",
        "queues": {
            "gpu_4_queue": {"waiting": 8, "running": 4},
        },
    }]

    block = ops._omni_history(history, {"amd_mi300_1"})

    assert block["summary"]["snapshot_count"] == 1
    point = block["points"][0]
    assert point["amd"] == {
        "waiting_supported": True,
        "running_supported": True,
        "waiting_observed": 2,
        "running_observed": 1,
        "waiting_attributed": 3,
        "running_attributed": 1,
        "waiting_total": 3,
        "running_total": 2,
        "waiting_attribution": "complete",
        "running_attribution": "partial",
    }
    assert "lower bound" in block["provenance"]["count_semantics"]


def test_omni_keeps_partial_aggregate_and_exact_job_ledger_distinct():
    queue_snapshot = {
        "ts": "2026-04-22T12:00:00Z",
        "queues": {
            "amd_mi300_1": {
                "waiting": 8,
                "running": 10,
                "waiting_by_workload": {"omni": 1, "vllm": 3},
                "running_by_workload": {"omni": 2, "vllm": 3},
            },
        },
    }
    queue_jobs = {
        "ts": queue_snapshot["ts"],
        "pending": [
            {
                "workload": "omni",
                "pipeline": "vllm-omni-amd-ci",
                "queue": "amd_mi300_1",
                "analysis_excluded": False,
            }
            for _ in range(3)
        ],
        "running": [
            {
                "workload": "omni",
                "pipeline": "vllm-omni-amd-ci",
                "queue": "amd_mi300_1",
                "analysis_excluded": False,
            }
            for _ in range(4)
        ],
    }

    omni = ops._omni(
        queue_snapshot,
        queue_jobs,
        [queue_snapshot],
        {"healthy": 1, "trigger": 3},
        {},
        {
            "scope": {
                "queues": ["amd_mi300_1"],
                "workload_pipelines": {"omni": ["vllm-omni-amd-ci"]},
            },
        },
        {},
    )

    assert omni["current"]["waiting"] == 3
    assert omni["current"]["running"] == 4
    assert omni["current"]["ledger"] == {"waiting": 3, "running": 4}
    assert omni["current"]["count_basis"] == {
        "waiting": "exact_pipeline_active_job_ledger",
        "running": "exact_pipeline_active_job_ledger",
    }
    assert omni["current"]["attribution"]["waiting_attribution"] == "partial"
    assert omni["current"]["attribution"]["running_attribution"] == "partial"


def test_omni_uses_job_ledger_when_workload_aggregate_is_unavailable():
    queue_snapshot = {
        "ts": "2026-04-22T12:00:00Z",
        "queues": {"amd_mi300_1": {"waiting": 3, "running": 2}},
    }
    queue_jobs = {
        "ts": queue_snapshot["ts"],
        "pending": [{
            "workload": "omni",
            "pipeline": "vllm-omni-amd-ci",
            "queue": "amd_mi300_1",
            "analysis_excluded": False,
        }],
        "running": [],
    }

    omni = ops._omni(
        queue_snapshot,
        queue_jobs,
        [queue_snapshot],
        {},
        {},
        {
            "scope": {
                "queues": ["amd_mi300_1"],
                "workload_pipelines": {"omni": ["vllm-omni-amd-ci"]},
            },
        },
        {},
    )

    assert omni["current"]["waiting"] == 1
    assert omni["current"]["running"] == 0
    assert omni["current"]["count_basis"] == {
        "waiting": "exact_pipeline_active_job_ledger",
        "running": "exact_pipeline_active_job_ledger",
    }
    assert omni["current"]["attribution"]["waiting_attribution"] == "unavailable"
    assert omni["current"]["attribution"]["running_attribution"] == "unavailable"
    assert omni["history"]["points"] == []


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


def test_platform_comparison_is_amd_first_and_matches_only_exact_cuda_labels():
    def group(
        group_id,
        name,
        hardware,
        queue,
        *,
        runs,
        passed,
        failed=0,
        soft_failed=0,
        p90=10,
    ):
        incidents = failed + soft_failed
        return {
            "id": group_id,
            "name": name,
            "raw_names": [name],
            "hardware": hardware,
            "queues": [queue],
            "runs": runs,
            "build_count": min(runs, 100),
            "passed": passed,
            "failed": failed,
            "soft_failed": soft_failed,
            "incident_count": incidents,
            "incident_rate_pct": round(incidents / runs * 100, 1),
            "mixed_outcomes": bool(passed and incidents),
            "latest_state": "passed" if passed else "hard",
            "latest_observed_at": "2026-07-14T00:00:00Z",
            "latest_url": f"https://buildkite.com/vllm/ci/builds/1/steps/canvas?jid={group_id}",
            "median_dur": p90 / 2,
            "p90_dur": p90,
            "max_dur": p90 + 1,
            "duration_basis": "job_wall",
        }

    catalog = [
        group("amd-samplers", "AMD: Samplers Test (mi325_1)", "mi325", "amd_mi325_1", runs=50, passed=40, soft_failed=10, p90=20),
        group("cuda-samplers", "Samplers Test", "h200", "h200_35gb", runs=80, passed=76, failed=4, p90=12),
        group("intel-samplers", "Samplers Test", "gpu", "intel-gpu", runs=90, passed=1, failed=89, p90=99),
        group("amd-unmatched", "AMD: Exact Name (mi300_1)", "mi300", "amd_mi300_1", runs=10, passed=10),
        group("cuda-fuzzy", "Exact Names", "h100", "mithril-h100-pool", runs=10, passed=10),
    ]
    retry = {
        "available": True,
        "retry_attempts": [
            {"name": "AMD: Samplers Test (mi325_1)", "retry_source": {"job_id": "a"}},
            {"name": "AMD: Samplers Test (mi325_1)", "retry_source": {"job_id": "b"}},
            {"name": "Samplers Test", "retry_source": {"job_id": "c"}},
        ],
        "failed_then_passed_recoveries": [
            {"name": "AMD: Samplers Test (mi325_1)"},
        ],
    }

    comparison = ops._platform_comparison(catalog, retry, cohort_builds=100)

    assert comparison["available"] is True
    assert comparison["summary"]["amd_base_group_count"] == 2
    assert comparison["summary"]["matched_base_group_count"] == 1
    assert comparison["summary"]["unmatched_amd_base_group_count"] == 1
    samplers = next(row for row in comparison["rows"] if row["label"] == "Samplers Test")
    assert samplers["match_status"] == "exact_cuda_pair"
    assert samplers["comparison_eligible"] is True
    assert samplers["amd"]["group_ids"] == ["amd-samplers"]
    assert samplers["cuda"]["group_ids"] == ["cuda-samplers"]
    assert samplers["amd"]["incident_rate_pct"] == 20.0
    assert samplers["cuda"]["incident_rate_pct"] == 5.0
    assert samplers["incident_rate_delta_pp"] == 15.0
    assert samplers["amd"]["attempts_per_100_builds"] == 50.0
    assert samplers["amd"]["retry_attempts"] == 2
    assert samplers["amd"]["retry_frequency_pct"] == 4.0
    assert samplers["amd"]["retry_recovery_rate_pct"] == 50.0
    assert samplers["worst_p90_delta_mins"] == 8.0
    unmatched = next(row for row in comparison["rows"] if row["label"] == "Exact Name")
    assert unmatched["match_status"] == "no_cuda_equivalent"
    assert unmatched["cuda"]["variant_count"] == 0


def test_platform_comparison_pairs_each_amd_variant_with_one_cuda_reference():
    def group(group_id, name, hardware, queue, runs=10):
        return {
            "id": group_id,
            "name": name,
            "raw_names": [name],
            "hardware": hardware,
            "queues": [queue],
            "runs": runs,
            "build_count": runs,
            "passed": runs,
            "failed": 0,
            "soft_failed": 0,
            "incident_count": 0,
            "incident_rate_pct": 0,
            "mixed_outcomes": False,
            "latest_state": "passed",
            "latest_observed_at": "2026-08-07T00:00:00Z",
            "latest_url": (
                "https://buildkite.com/vllm/ci/builds/1/steps/canvas"
                f"?jid={group_id}"
            ),
            "median_dur": 5,
            "p90_dur": 10,
            "max_dur": 11,
            "duration_basis": "job_wall",
        }

    comparison = ops._platform_comparison(
        [
            group(
                "amd-mi300",
                "AMD: Shared Test (mi300_1)",
                "mi300",
                "amd_mi300_1",
            ),
            group(
                "amd-mi355",
                "AMD: Shared Test (mi355_1)",
                "mi355",
                "amd_mi355_1",
            ),
            group("cuda-h200", "Shared Test", "h200", "h200_35gb"),
        ],
        {
            "available": True,
            "retry_attempts": [
                {
                    "group_id": "amd-mi300",
                    "name": "AMD: Shared Test (mi300_1)",
                    "retry_source": {"job_id": "original"},
                }
            ],
            "failed_then_passed_recoveries": [],
        },
        cohort_builds=10,
    )

    assert comparison["summary"]["amd_base_group_count"] == 1
    assert comparison["summary"]["amd_comparison_row_count"] == 2
    assert comparison["summary"]["matched_base_group_count"] == 1
    assert comparison["summary"]["comparable_variant_pair_count"] == 2
    assert comparison["summary"]["matched_cuda_variant_count"] == 1
    assert len(comparison["rows"]) == 2
    assert all(row["comparison_eligible"] for row in comparison["rows"])
    assert all(row["match_status"] == "exact_cuda_pair" for row in comparison["rows"])
    assert all(row["amd"]["variant_count"] == 1 for row in comparison["rows"])
    assert all(row["cuda"]["variant_count"] == 1 for row in comparison["rows"])
    assert {row["amd"]["group_ids"][0] for row in comparison["rows"]} == {
        "amd-mi300",
        "amd-mi355",
    }
    assert comparison["summary"]["matched_cuda"]["runs"] == 10
    assert sum(row["amd"]["child_retry_attempts"] for row in comparison["rows"]) == 1


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


def test_upstream_scheduled_gating_exact_cohort_retry_shards_and_queue_waits():
    def scheduled_build(number: int, message: str, jobs: list[dict]) -> dict:
        return {
            "number": number,
            "branch": "main",
            "state": "failed" if any(job.get("state") == "failed" for job in jobs) else "passed",
            "message": message,
            "created_at": f"2026-04-{number - 880:02d}T09:00:00Z",
            "finished_at": f"2026-04-{number - 880:02d}T10:30:00Z",
            "web_url": f"https://buildkite.com/vllm/ci/builds/{number}",
            "jobs": jobs,
        }

    def job(
        job_id: str,
        name: str,
        step_key: str,
        state: str,
        queue: str,
        wait: float,
        **extra,
    ) -> dict:
        return {
            "type": "script",
            "id": job_id,
            "job_id": job_id,
            "raw_name": name,
            "name": name,
            "step_key": step_key,
            "state": state,
            "q": queue,
            "queue_wait_mins": wait,
            "runnable_at": "2026-04-21T09:00:00Z",
            "started_at": f"2026-04-21T09:{int(wait):02d}:00Z",
            "finished_at": f"2026-04-21T10:{len(job_id):02d}:00Z",
            **extra,
        }

    nightly = scheduled_build(901, "Full CI run - nightly", [
        job(
            "alpha-old",
            "Alpha shard 1/2",
            "amd-alpha",
            "failed",
            "queue-a",
            9,
            retried=True,
            retried_in_job_id="alpha-new",
        ),
        job(
            "alpha-new",
            "Alpha shard 1/2",
            "amd-alpha",
            "passed",
            "queue-a",
            5,
            retries_count=1,
            retry_source="manual",
        ),
        job("alpha-two", "Alpha shard 2/2", "amd-alpha", "passed", "queue-a", 15),
        job("beta", "Beta", "amd-beta", "failed", "queue-a", 20),
        job("unconfigured", "Not configured", "amd-extra", "failed", "queue-z", 99),
    ])
    nightly["jobs"].append({"step": "malformed-unconfigured-step"})
    daily = scheduled_build(900, "Full CI run - daily", [
        job("alpha-daily", "Alpha", "amd-alpha", "soft_fail", "queue-a", 2),
        job("beta-daily", "Beta", "amd-beta", "running", "queue-a", 3),
        job("gamma-daily", "Gamma", "amd-gamma", "passed", "queue-b", 4),
    ])
    lookalikes = [
        scheduled_build(904, "Full CI run torch nightly", []),
        scheduled_build(903, "Full CI run - weekly", []),
        scheduled_build(902, "Full CI run - nightly-ish", []),
        scheduled_build(899, "Prefix Full CI run - daily", []),
    ]
    unobserved_scheduled = scheduled_build(905, "Full CI run - daily", [])
    builds = [
        unobserved_scheduled, lookalikes[0], nightly, lookalikes[1], daily,
        *lookalikes[2:],
    ]
    collector = analytics.build_all_main_reliability(
        builds,
        pipeline_slug="ci",
        window_days=30,
        generated_at="2026-04-25T12:00:00Z",
        nightly_pattern="nightly",
        observation_limit=60,
        collection_provenance={
            "created_from": "2026-03-26T12:00:00Z",
            "pages_fetched": 1,
            "termination_reason": "short_page",
            "exhaustive": True,
        },
    )
    pipeline_analytics = {
        "all_main_reliability": collector,
    }
    capacity = {"groups": [
        {
            "key": "alpha",
            "label": "Alpha",
            "area": "A",
            "queue": "queue-a",
            "in_capacity_scope": True,
        },
        {
            "key": "alpha",
            "label": "Duplicate Alpha",
            "queue": "queue-a",
            "in_capacity_scope": True,
        },
        {
            "key": "beta",
            "label": "Beta",
            "area": "B",
            "queue": "queue-a",
            "in_capacity_scope": True,
        },
        {
            "key": "gamma",
            "label": "Gamma",
            "area": "C",
            "queue": "queue-b",
            "in_capacity_scope": True,
        },
        {
            "key": "not-in-scope",
            "label": "Excluded",
            "queue": "queue-z",
            "in_capacity_scope": False,
        },
    ]}

    result = ops._upstream_scheduled_gating(pipeline_analytics, capacity)

    assert result["available"] is True
    assert result["source"]["accepted"] is True
    assert result["source"]["builds_key"] == "ci.all_main_reliability.builds"
    assert result["source"]["observations_key"] == (
        "ci.all_main_reliability.groups[].observations"
    )
    assert result["query"] == {
        "url": "https://buildkite.com/vllm/ci/builds?query=full+ci+run+-+",
        "buildkite_query": "full ci run - ",
        "exact_message_pattern": ops.UPSTREAM_SCHEDULED_GATING_NAME_PATTERN,
        "exact_messages": {
            "nightly": "Full CI run - nightly",
            "daily": "Full CI run - daily",
        },
    }
    assert result["scope"]["configured_group_count"] == 3
    assert result["scope"]["configured_queue_count"] == 2
    assert result["provenance"][
        "matching_builds_without_retained_observations"
    ] == 1
    assert result["latest_by_kind"]["nightly"]["number"] == 901
    assert result["latest_by_kind"]["daily"]["number"] == 900
    assert [run["number"] for run in result["recent"]] == [901, 900]

    latest = result["latest"]
    assert latest["number"] == 901
    assert latest["summary"] == {
        "gated": 2,
        "total": 3,
        "passing": 1,
        "failing": 1,
        "soft_failing": 0,
        "pending": 0,
        "missing": 1,
        "job_attempts": 4,
        "selected_jobs": 3,
        "queue_count": 1,
        "configured_queue_count": 2,
    }
    assert latest["queue_wait_mins"] == {
        "p50": 15.0,
        "p95": 19.5,
        "max": 20.0,
        "sample_count": 3,
    }
    groups = {row["key"]: row for row in latest["groups"]}
    assert set(groups) == {"alpha", "beta", "gamma"}
    assert groups["alpha"]["state"] == "passing"
    assert groups["alpha"]["job_attempts"] == 3
    assert groups["alpha"]["selected_jobs"] == 2
    assert {row["job_id"] for row in groups["alpha"]["jobs"]} == {
        "alpha-new", "alpha-two",
    }
    assert groups["beta"]["state"] == "failing"
    assert groups["beta"]["url"].endswith("?jid=beta&tab=output")
    assert groups["gamma"]["state"] == "missing"
    queues = {row["queue"]: row for row in latest["queues"]}
    assert queues["queue-a"]["gated"] == 2
    assert queues["queue-a"]["total"] == 2
    assert queues["queue-a"]["selected_jobs"] == 3
    assert queues["queue-a"]["used"] is True
    assert queues["queue-a"]["queue_wait_mins"] == latest["queue_wait_mins"]
    assert queues["queue-b"]["missing"] == 1
    assert queues["queue-b"]["used"] is False
    assert queues["queue-b"]["queue_wait_mins"]["sample_count"] == 0
    assert result["latest_by_kind"]["daily"]["summary"] == {
        "gated": 2,
        "total": 3,
        "passing": 1,
        "failing": 0,
        "soft_failing": 1,
        "pending": 0,
        "missing": 1,
        "job_attempts": 2,
        "selected_jobs": 2,
        "queue_count": 2,
        "configured_queue_count": 2,
    }


def test_upstream_scheduled_gating_fails_closed_and_shell_omits_detail():
    capacity = {"groups": [{
        "key": "alpha",
        "label": "Alpha",
        "queue": "queue-a",
        "in_capacity_scope": True,
    }]}
    valid_empty_collector = analytics.build_all_main_reliability(
        [],
        pipeline_slug="ci",
        window_days=30,
        generated_at=GENERATED_AT,
        nightly_pattern="nightly",
        collection_provenance={
            "created_from": "2026-03-23T12:00:00Z",
            "pages_fetched": 0,
            "termination_reason": "empty_page",
            "exhaustive": True,
        },
    )
    invalid_branch_collector = json.loads(json.dumps(valid_empty_collector))
    invalid_branch_collector["provenance"]["query"]["branch"] = "feature"
    incomplete_retry_collector = json.loads(json.dumps(valid_empty_collector))
    incomplete_retry_collector["provenance"]["query"][
        "include_retried_jobs"
    ] = False
    malformed_sources = [
        None,
        {"all_main_reliability": "not-an-object"},
        {"all_main_reliability": invalid_branch_collector},
        {"all_main_reliability": incomplete_retry_collector},
    ]
    for source in malformed_sources:
        result = ops._upstream_scheduled_gating(source, capacity)
        assert result["available"] is False
        assert result["source"]["accepted"] is False
        assert result["latest"] is None
        assert result["latest_by_kind"] == {"nightly": None, "daily": None}
        assert result["recent"] == []
        assert result["scope"]["configured_group_count"] == 1

    valid_empty_source = {"all_main_reliability": valid_empty_collector}
    missing_groups = ops._upstream_scheduled_gating(valid_empty_source, {})
    assert missing_groups["available"] is False
    assert missing_groups["unavailable_reason"] == "configured_gating_groups_missing"
    assert missing_groups["source"]["accepted"] is True
    assert missing_groups["latest"] is None

    detailed = {
        "available": True,
        "unavailable_reason": None,
        "scope": {"configured_group_count": 1},
        "query": {"url": ops.UPSTREAM_SCHEDULED_QUERY_URL},
        "source": {"accepted": True},
        "latest": {
            "kind": "daily",
            "number": 901,
            "summary": {"gated": 1, "total": 1, "passing": 1},
            "queue_wait_mins": {"p50": 2.0, "p95": 2.0, "max": 2.0, "sample_count": 1},
            "queues": [{"queue": "queue-a", "gated": 1, "total": 1}],
            "groups": [{"key": "alpha"}],
        },
        "latest_by_kind": {
            "nightly": None,
            "daily": {
                "kind": "daily",
                "number": 901,
                "summary": {"gated": 1, "total": 1, "passing": 1},
                "queue_wait_mins": {
                    "p50": 2.0, "p95": 2.0, "max": 2.0, "sample_count": 1,
                },
                "queues": [{"queue": "queue-a", "gated": 1, "total": 1}],
                "groups": [{"key": "alpha"}],
            },
        },
        "recent": [{"number": 901}],
    }
    shell = ops._operations_shell({
        "gating": {"matrix_summary": {}, "upstream_scheduled": detailed},
    })["gating"]["upstream_scheduled"]

    assert shell["latest"]["summary"]["gated"] == 1
    assert shell["latest"]["queues"][0]["queue"] == "queue-a"
    assert shell["latest_by_kind"]["daily"]["number"] == 901
    assert "groups" not in shell["latest"]
    assert "groups" not in shell["latest_by_kind"]["daily"]
    assert "recent" not in shell


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


def test_gating_resolves_percent_n_matrix_and_numbered_history_variants():
    targets = {"groups": [{"id": 22, "label": "Kernels Attention Test %N"}]}
    matrix = {
        "generated_at": GENERATED_AT,
        "source": {
            "latest_build_number": 11301,
            "yaml_url": (
                "https://raw.githubusercontent.com/vllm-project/vllm/"
                "7f599d78546819948c32f2b23d913507bbb38875/.buildkite/test-amd.yaml"  # commit SHA
            ),
        },
        "rows": [{
            "id": "attention",
            "canonical_title": "Kernels Attention Test",
            "cells": {
                "mi300": {
                    "exists": True,
                    "latest_state": "passed",
                    "latest_build_number": 11301,
                    "latest_url": (
                        "https://buildkite.com/vllm/amd-ci/builds/11301/steps/"
                        "canvas?sid=019fa2cd-a62d-46b6-a4e2-0df8be229132"
                    ),
                },
                "mi355": {
                    "exists": True,
                    "latest_state": "soft_fail",
                    "latest_build_number": 11301,
                    "latest_url": (
                        "https://buildkite.com/vllm/amd-ci/builds/11301/steps/"
                        "canvas?sid=mi355-soft"
                    ),
                },
            },
        }],
    }
    reliability = {
        "available": True,
        "source_pipeline": "ci",
        "group_catalog": [
            {
                "id": "attention-1",
                "name": "Kernels Attention Test 1",
                "runs": 2,
                "passed": 2,
                "failed": 0,
                "soft_failed": 0,
                "observations": [],
            },
            {
                "id": "attention-2",
                "name": "Kernels Attention Test 2",
                "runs": 2,
                "passed": 2,
                "failed": 0,
                "soft_failed": 0,
                "observations": [],
            },
        ],
    }

    row = ops._gating(
        targets,
        {"rows": []},
        matrix,
        {},
        reliability,
    )["active_target_groups"][0]

    assert row["latest_amd_result"]["state"] == "soft"
    assert row["latest_amd_result"]["build_number"] == 11301
    assert len(row["latest_amd_result"]["evidence"]) == 2
    assert any(
        "sid=019fa2cd-a62d-46b6-a4e2-0df8be229132" in evidence["url"]
        for evidence in row["latest_amd_result"]["evidence"]
    )
    assert row["runtime_resolution"]["status"] == "matched"
    assert row["runtime_resolution"]["method"] == "shard_template"
    assert row["main_reliability"]["variant_count"] == 2
    assert row["main_reliability"]["runs"] == 4


def test_matrix_collision_merge_is_order_independent_and_incident_first():
    rows = [
        {
            "id": "distributed-mi300",
            "canonical_title": "Distributed Tests (2xH100-2xMI)",
            "cells": {"mi300": {
                "exists": True,
                "latest_state": "passed",
                "latest_build_number": 11301,
                "latest_url": (
                    "https://buildkite.com/vllm/amd-ci/builds/11301/steps/"
                    "canvas?sid=shared-step"
                ),
            }},
        },
        {
            "id": "distributed-mi355",
            "canonical_title": "Distributed Tests (2xH100-2xMI)",
            "cells": {"mi355": {
                "exists": True,
                "latest_state": "soft_fail",
                "latest_build_number": 11301,
                "latest_url": (
                    "https://buildkite.com/vllm/amd-ci/builds/11301/steps/"
                    "canvas?sid=shared-step"
                ),
            }},
        },
    ]

    outputs = []
    for ordered_rows in (rows, list(reversed(rows))):
        gating = ops._gating(
            {"groups": [{"id": 87, "label": "Distributed Tests (2xH100)"}]},
            {"rows": []},
            {
                "generated_at": GENERATED_AT,
                "source": {"latest_build_number": 11301},
                "rows": ordered_rows,
            },
            {},
            {},
        )
        outputs.append(gating["active_target_groups"][0])

    assert [row["latest_amd_result"]["state"] for row in outputs] == ["soft", "soft"]
    assert [
        [
            (evidence["architecture"], evidence["url"])
            for evidence in row["latest_amd_result"]["evidence"]
        ]
        for row in outputs
    ] == [[
        (
            "mi355",
            "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=shared-step",
        ),
        (
            "mi300",
            "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=shared-step",
        ),
    ]] * 2


def test_exact_matrix_alias_does_not_fold_h100_target_into_b200_variant():
    matrix = {
        "generated_at": GENERATED_AT,
        "source": {"latest_build_number": 11301},
        "rows": [{
            "id": "v1-attention",
            "canonical_title": "V1 attention",
            "cells": {
                "mi300": {
                    "exists": True,
                    "latest_state": "passed",
                    "latest_url": "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=h100",
                    "variants": [{
                        "label": "V1 attention (H100-MI300)",
                        "latest_state": "passed",
                        "latest_url": "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=h100",
                        "aliases": ["V1 attention (H100-MI300)"],
                    }],
                },
                "mi355": {
                    "exists": True,
                    "latest_state": "soft_fail",
                    "latest_url": "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=b200",
                    "variants": [{
                        "label": "V1 attention (B200-MI355)",
                        "latest_state": "soft_fail",
                        "latest_url": "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=b200",
                        "aliases": ["V1 attention (B200-MI355)"],
                    }],
                },
            },
        }],
    }

    row = ops._gating(
        {"groups": [{"id": 103, "label": "V1 attention (H100-MI300)"}]},
        {"rows": []},
        matrix,
        {},
        {},
    )["active_target_groups"][0]

    assert row["latest_amd_result"]["state"] == "passed"
    assert [item["architecture"] for item in row["latest_amd_result"]["evidence"]] == [
        "mi300"
    ]
    assert row["runtime_resolution"]["method"] == "exact_matrix_label"


def test_exact_yaml_alias_does_not_absorb_lossy_canonical_sibling():
    matrix = {
        "generated_at": GENERATED_AT,
        "source": {"latest_build_number": 11301},
        "rows": [
            {
                "id": "small-models",
                "title": "LM Eval Small Models",
                "canonical_title": "LM Eval Small Models",
                "cells": {"mi300": {
                    "exists": True,
                    "latest_state": "passed",
                    "latest_url": "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=base",
                    "variants": [{
                        "label": "LM Eval Small Models",
                        "latest_state": "passed",
                        "latest_url": "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=base",
                        "aliases": ["LM Eval Small Models"],
                    }],
                }},
            },
            {
                "id": "small-models-rocm",
                "title": "LM Eval Small Models (MI300)",
                "canonical_title": "LM Eval Small Models",
                "cells": {"mi300": {
                    "exists": True,
                    "latest_state": "soft_fail",
                    "latest_url": "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=rocm",
                    "variants": [{
                        "label": "LM Eval Small Models (MI300)",
                        "latest_state": "soft_fail",
                        "latest_url": "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=rocm",
                        "aliases": ["LM Eval Small Models (MI300)"],
                    }],
                }},
            },
        ],
    }

    row = ops._gating(
        {"groups": [{"id": 113, "label": "LM Eval Small Models"}]},
        {"rows": []},
        matrix,
        {},
        {},
    )["active_target_groups"][0]

    assert row["latest_amd_result"]["state"] == "passed"
    assert [item["url"] for item in row["latest_amd_result"]["evidence"]] == [
        "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=base"
    ]


def test_lossy_canonical_title_does_not_rescue_stale_hardwareless_target():
    matrix = {
        "generated_at": GENERATED_AT,
        "source": {"latest_build_number": 11301},
        "rows": [{
            "id": "qwen-b200",
            "canonical_title": "Qwen Sync Accuracy",
            "cells": {"mi355": {
                "exists": True,
                "latest_state": "soft_fail",
                "latest_url": "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=b200",
                "variants": [{
                    "label": "Qwen Sync Accuracy (B200-MI355)",
                    "latest_state": "soft_fail",
                    "latest_url": "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=b200",
                    "aliases": ["Qwen Sync Accuracy (B200-MI355)"],
                }],
            }},
        }],
    }
    parity = {"matches": [
        {
            "identity_key": "qwen sync accuracy (4 gpus)",
            "nvidia_label": "Qwen Sync Accuracy (4xH100)",
            "amd_label": "Qwen Sync Accuracy (4xH100-4xMI300)",
        },
        {
            "identity_key": "qwen sync accuracy (2 gpus)",
            "nvidia_label": "Qwen Sync Accuracy (2xB200)",
            "amd_label": "Qwen Sync Accuracy (B200-MI355)",
        },
    ]}

    row = ops._gating(
        {"groups": [{"id": 59, "label": "Qwen Sync Accuracy"}]},
        {"rows": [{
            "target_id": 59,
            "decision": "missing_from_upstream",
            "label": "Qwen Sync Accuracy",
        }]},
        matrix,
        {},
        {},
        parity,
    )["active_target_groups"][0]

    assert row["latest_amd_result"]["state"] == "unknown"
    assert row["latest_amd_result"]["evidence"] == []
    assert row["runtime_resolution"]["status"] == "stale_target_alias"
    assert "lossy canonical matrix title" in row["runtime_resolution"]["reason"]


def test_definition_parity_resolves_non_syntactic_amd_alias():
    target = {"id": 70, "label": "Batch Invariance (A100)"}
    matrix = {
        "generated_at": GENERATED_AT,
        "source": {"latest_build_number": 11301},
        "rows": [{
            "id": "batch-mi250",
            "canonical_title": "Batch Invariance",
            "cells": {"mi250": {
                "exists": True,
                "latest_state": "passed",
                "latest_url": "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=batch",
                "variants": [{
                    "label": "Batch Invariance (H100-MI250)",
                    "latest_state": "passed",
                    "latest_url": "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=batch",
                    "aliases": ["Batch Invariance (H100-MI250)"],
                }],
            }},
        }],
    }
    parity = {
        "source": {"commit_sha": "a" * 40},
        "matches": [{
            "identity_key": "batch invariance",
            "nvidia_label": "Batch Invariance (A100)",
            "amd_label": "Batch Invariance (H100-MI250)",
            "command_similarity": 0.6698,
        }],
    }

    row = ops._gating(
        {"groups": [target]},
        {"rows": []},
        matrix,
        {},
        {},
        parity,
    )["active_target_groups"][0]

    assert row["latest_amd_result"]["state"] == "passed"
    assert row["runtime_resolution"]["status"] == "matched"
    assert row["runtime_resolution"]["method"] == "definition_parity"
    assert row["runtime_resolution"]["target_identity_key"] == "batch invariance"
    assert row["runtime_resolution"]["amd_definition_labels"] == [
        "Batch Invariance (H100-MI250)"
    ]
    assert row["runtime_resolution"]["mapping_quality"] == "partial_commands"
    assert row["runtime_resolution"]["command_similarity_pct"] == 67.0


def test_definition_parity_merges_additional_variant_in_same_identity_family():
    target = {"id": 87, "label": "Distributed Tests (2 GPUs)(H100)"}
    matrix = {
        "generated_at": GENERATED_AT,
        "source": {"latest_build_number": 11301},
        "rows": [
            {
                "id": "distributed-mi300",
                "canonical_title": "Distributed Tests",
                "cells": {"mi300": {
                    "exists": True,
                    "latest_state": "passed",
                    "latest_url": (
                        "https://buildkite.com/vllm/amd-ci/builds/11301/"
                        "steps/canvas?sid=distributed-mi300"
                    ),
                    "variants": [{
                        "label": (
                            "Distributed Tests (2xH100-2xMI300)"
                        ),
                        "latest_state": "passed",
                        "latest_url": (
                            "https://buildkite.com/vllm/amd-ci/builds/11301/"
                            "steps/canvas?sid=distributed-mi300"
                        ),
                    }],
                }},
            },
            {
                "id": "distributed-mi355",
                "canonical_title": "Distributed Tests",
                "cells": {"mi355": {
                    "exists": True,
                    "latest_state": "passed",
                    "latest_url": (
                        "https://buildkite.com/vllm/amd-ci/builds/11301/"
                        "steps/canvas?sid=distributed-mi355"
                    ),
                    "variants": [{
                        "label": (
                            "Distributed Tests (2xH100-2xMI355)"
                        ),
                        "latest_state": "passed",
                        "latest_url": (
                            "https://buildkite.com/vllm/amd-ci/builds/11301/"
                            "steps/canvas?sid=distributed-mi355"
                        ),
                    }],
                }},
            },
        ],
    }
    identity = "distributed tests (2 gpus)"
    parity = {
        "matches": [{
            "identity_key": identity,
            "nvidia_label": "Distributed Tests (2xH100)",
            "amd_label": "Distributed Tests (2xH100-2xMI300)",
            "command_similarity": 1.0,
        }],
        "additional_variants": [{
            "identity_key": identity,
            "nvidia_label": "Distributed Tests (2xH100)",
            "amd_label": "Distributed Tests (2xH100-2xMI355)",
            "command_similarity": 0.8,
        }],
    }

    row = ops._gating(
        {"groups": [target]},
        {"rows": []},
        matrix,
        {},
        {},
        parity,
    )["active_target_groups"][0]

    assert row["runtime_resolution"]["status"] == "matched"
    assert row["runtime_resolution"]["candidate_count"] == 2
    assert row["runtime_resolution"]["target_identity_key"] == identity
    assert row["runtime_resolution"]["amd_definition_labels"] == [
        "Distributed Tests (2xH100-2xMI300)",
        "Distributed Tests (2xH100-2xMI355)",
    ]


def test_parity_metadata_cannot_steal_an_exact_command_twin():
    matrix = {
        "generated_at": GENERATED_AT,
        "source": {"latest_build_number": 11301},
        "rows": [{
            "id": "extract-hidden-states",
            "canonical_title": "Extract Hidden States Integration",
            "cells": {"mi300": {
                "exists": True,
                "latest_state": "passed",
                "latest_url": "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=extract",
                "variants": [{
                    "label": "Extract Hidden States Integration",
                    "latest_state": "passed",
                    "latest_url": "https://buildkite.com/vllm/amd-ci/builds/11301/steps/canvas?sid=extract",
                    "aliases": ["Extract Hidden States Integration"],
                }],
            }},
        }],
    }
    commands = ["pytest tests/extract_hidden_states"]
    parity = {
        "matches": [{
            "identity_key": "extract hidden states integration (2 gpus)",
            "nvidia_label": "Extract Hidden States Integration (2 GPUs)",
            "amd_label": "Extract Hidden States Integration",
            "command_similarity": 0.8794,
            "amd_commands": commands,
        }],
        "nvidia_only": [{
            "identity_key": "extract hidden states integration",
            "label": "Extract Hidden States Integration",
            "commands": commands,
        }],
    }

    row = ops._gating(
        {"groups": [{
            "id": 42,
            "label": "Extract Hidden States Integration (2 GPUs)",
        }]},
        {"rows": []},
        matrix,
        {},
        {},
        parity,
    )["active_target_groups"][0]

    assert row["latest_amd_result"]["state"] == "unknown"
    assert row["runtime_resolution"]["status"] == "no_amd_definition"
    assert row["assessment"] == "no_matching_amd_definition"


def test_unresolved_runtime_target_distinguishes_no_definition_from_stale_alias():
    parity = {
        "matches": [{
            "identity_key": "gpqa eval (gpt-oss) (2 gpus)",
            "nvidia_label": "GPQA Eval (GPT-OSS) (2xH100)",
            "amd_label": "GPQA Eval (GPT-OSS) (2xH100-2xMI300)",
        }],
        "nvidia_only": [{
            "identity_key": "lm eval turboquant kv cache",
            "label": "LM Eval TurboQuant KV Cache",
        }],
    }
    result = ops._gating(
        {"groups": [
            {"id": 40, "label": "LM Eval TurboQuant KV Cache"},
            {"id": 65, "label": "GPQA Eval (GPT-OSS) (H100)"},
        ]},
        {"rows": []},
        {"generated_at": GENERATED_AT, "rows": []},
        {},
        {},
        parity,
    )
    by_id = {row["id"]: row for row in result["active_target_groups"]}

    assert by_id[40]["runtime_resolution"]["status"] == "no_amd_definition"
    assert by_id[40]["assessment"] == "no_matching_amd_definition"
    assert by_id[65]["runtime_resolution"]["status"] == "stale_target_alias"
    assert by_id[65]["assessment"] == "target_mapping_needs_review"


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
    retry_attempt = reliability["retry_analysis"]["retry_attempts"][0]
    assert retry_attempt["job_url"].startswith(
        "https://buildkite.com/vllm/ci/"
    )
    assert retry_attempt["observed_at"] == "2026-04-22T10:00:00Z"
    assert retry_attempt["group_id"] == hard["id"]
    recovery = reliability["retry_analysis"]["failed_then_passed_recoveries"][0]
    assert recovery["observed_at"] == "2026-04-22T10:00:00Z"
    assert recovery["group_id"] == hard["id"]
    assert "not proof that a retry recovered" in reliability["evidence_definitions"]["mixed_outcome_history"]


def test_group_catalog_recovers_amd_hardware_from_queue_when_source_is_unknown():
    build = _build(104, "2026-04-22", [
        _job(
            "AMD: Samplers Test (mi325_1)",
            "passed",
            "https://buildkite.com/vllm/ci/builds/104/steps/canvas?jid=mi325&tab=output",
            q="amd_mi325_1",
            hardware="unknown",
            group_id="retained-mi325-group",
        ),
    ], pipeline="ci")

    catalog, _ = ops._group_catalog([build], pipeline_slug="ci")

    assert len(catalog) == 1
    assert catalog[0]["hardware"] == "mi325"
    assert catalog[0]["id"] == "retained-mi325-group"


def test_collector_catalog_recovers_amd_hardware_from_queue_when_source_is_unknown():
    source = {
        "group_id": "collector-mi325-group",
        "name": "AMD: Samplers Test (mi325_1)",
        "raw_name": "AMD: Samplers Test (mi325_1)",
        "hardware": "unknown",
        "queue": "amd_mi325_1",
        "denominator": 1,
        "passed": 1,
        "failed": 0,
        "soft_failed": 0,
        "duration": {},
        "observations": [{
            "eligible_for_reliability": True,
            "result": "passed",
            "build_number": 104,
            "build_url": "https://buildkite.com/vllm/ci/builds/104",
            "job_url": (
                "https://buildkite.com/vllm/ci/builds/104/steps/canvas"
                "?jid=mi325&tab=output"
            ),
            "observed_at": "2026-04-22T10:00:00Z",
        }],
    }

    catalog, _, _ = ops._collector_main_catalog(
        {"groups": [source]},
        pipeline_slug="ci",
    )

    assert catalog[0]["hardware"] == "mi325"
    assert catalog[0]["queues"] == ["amd_mi325_1"]


def test_nightly_fixed_requires_an_observed_pass():
    previous = _build(10, "2026-04-20", [
        _job("Missing now", "failed", "https://buildkite.com/vllm/amd-ci/builds/10/steps/missing"),
        _job("Actually fixed", "failed", "https://buildkite.com/vllm/amd-ci/builds/10/steps/fixed"),
        _job("Held evidence", "failed", "https://buildkite.com/vllm/amd-ci/builds/10/steps/held"),
    ])
    current = _build(11, "2026-04-21", [
        _job("Actually fixed", "passed", "https://buildkite.com/vllm/amd-ci/builds/11/steps/fixed"),
        _job("Held evidence", "skipped", "https://buildkite.com/vllm/amd-ci/builds/11/steps/held"),
    ])

    row = ops._nightly_pipeline("amd-ci", {"builds": [current, previous]})["builds"][0]

    assert [item["name"] for item in row["transitions"]["fixed"]] == ["Actually fixed"]
    assert [item["name"] for item in row["transitions"]["not_observed"]] == ["Missing now"]
    held = row["transitions"]["indeterminate"][0]
    assert held["name"] == "Held evidence"
    assert held["state"] == "failed"
    assert held["build_number"] == 10
    assert held["url"].endswith("/builds/10/steps/held")
    assert held["current_indeterminate_evidence"]["state"] == "skipped"
    assert held["current_indeterminate_evidence"]["build_number"] == 11
    assert held["current_indeterminate_evidence"]["url"].endswith(
        "/builds/11/steps/held"
    )
    movement = row["failure_movement"]
    assert movement["new"] == []
    assert movement["recurring"] == []
    assert [item["name"] for item in movement["fixed"]] == ["Actually fixed"]


def test_nightly_retry_collapse_is_order_independent_with_original_only_linkage():
    original = _job(
        "mi300_1: Linked retry",
        "failed",
        "https://buildkite.com/vllm/amd-ci/builds/15/steps/original",
        job_id="retry-original",
        step_key="linked-retry",
        retried_in_job_id="retry-final",
    )
    final = _job(
        "mi300_1: Linked retry",
        "passed",
        "https://buildkite.com/vllm/amd-ci/builds/15/steps/final",
        job_id="retry-final",
        step_key="linked-retry",
    )

    for attempts in ([original, final], [final, original]):
        build = _build(15, "2026-04-20", attempts)
        observations = ops._nightly_group_observations("amd-ci", build)
        assert len(observations) == 1
        outcome, evidence = next(iter(observations.values()))
        assert outcome == "passed"
        assert evidence["url"].endswith("?jid=retry-final&tab=output")

        latest = ops._nightly_pipeline(
            "amd-ci", {"builds": [build]}
        )["builds"][0]
        assert latest["transitions"]["new"] == []
        assert latest["transitions"]["pending_soft"] == []


def test_operations_and_analytics_share_strict_nightly_signal_ids():
    jobs = [
        _job(
            "mi300_1: Strict signal",
            "failed",
            "https://buildkite.com/vllm/amd-ci/builds/16/steps/base",
            job_id="strict-base",
            step_key="strict-step",
        ),
        _job(
            "mi300_1: Strict signal 2/2",
            "failed",
            "https://buildkite.com/vllm/amd-ci/builds/16/steps/raw",
            job_id="strict-raw",
            step_key="strict-step",
        ),
        _job(
            "mi300_1: Strict signal",
            "failed",
            "https://buildkite.com/vllm/amd-ci/builds/16/steps/step",
            job_id="strict-step",
            step_key="other-step",
        ),
        _job(
            "mi300_1: Strict signal",
            "failed",
            "https://buildkite.com/vllm/amd-ci/builds/16/steps/queue",
            job_id="strict-queue",
            step_key="strict-step",
            q="amd_mi300_2",
        ),
        _job(
            "mi355_1: Strict signal",
            "failed",
            "https://buildkite.com/vllm/amd-ci/builds/16/steps/hardware",
            job_id="strict-hardware",
            step_key="strict-step",
            q="amd_mi355_1",
        ),
    ]
    build = _build(16, "2026-04-20", jobs)

    operations_ids = set(ops._nightly_group_observations("amd-ci", build))
    analytics_ids = {
        row["group_id"]
        for row in analytics.compute_nightly_change_history([build])[0]["new"]
    }

    assert len(operations_ids) == 5
    assert analytics_ids == operations_ids


def test_observed_failure_movement_matches_reliability_history():
    def job(number: int, name: str, state: str, *, soft_failed: bool = False) -> dict:
        slug = name.lower().replace(" ", "-")
        return _job(
            name,
            state,
            f"https://buildkite.com/vllm/amd-ci/builds/{number}/steps/{slug}",
            job_id=f"{number}-{slug}",
            step_key=slug,
            soft_failed=soft_failed,
        )

    previous = _build(27, "2026-04-20", [
        job(27, "mi300_1: Soft recurring", "soft_fail", soft_failed=True),
        job(27, "mi300_1: Fixed hard", "failed"),
        job(27, "mi300_1: Missing now", "failed"),
    ])
    current = _build(28, "2026-04-21", [
        job(28, "mi300_1: Soft recurring", "soft_fail", soft_failed=True),
        job(28, "mi300_1: Fixed hard", "passed"),
        job(28, "mi300_1: New hard", "failed"),
    ])

    operations_rows = {
        row["number"]: row["failure_movement"]
        for row in ops._nightly_pipeline(
            "amd-ci", {"builds": [current, previous]}
        )["builds"]
    }
    analytics_rows = {
        row["build_number"]: row["failure_movement"]
        for row in analytics.compute_nightly_change_history([current, previous])
    }

    assert operations_rows[27]["available"] is False
    assert analytics_rows[27]["available"] is False
    for bucket in ("new", "recurring", "fixed"):
        assert {
            row["group_id"] for row in operations_rows[28][bucket]
        } == {
            row["group_id"] for row in analytics_rows[28][bucket]
        }
    assert [row["name"] for row in operations_rows[28]["new"]] == [
        "mi300_1: New hard"
    ]
    assert [row["name"] for row in operations_rows[28]["recurring"]] == [
        "mi300_1: Soft recurring"
    ]
    assert [row["name"] for row in operations_rows[28]["fixed"]] == [
        "mi300_1: Fixed hard"
    ]


def test_nightly_nonterminal_builds_hold_state_without_advancing_streak():
    name = "mi300_1: Eligibility hold"

    def soft_build(number: int, date: str) -> dict:
        return _build(number, date, [
            _job(
                name,
                "soft_fail",
                f"https://buildkite.com/vllm/amd-ci/builds/{number}/steps/hold",
                job_id=f"hold-{number}",
                step_key="eligibility-hold",
                soft_failed=True,
            )
        ])

    first = soft_build(17, "2026-04-20")
    running = soft_build(18, "2026-04-21")
    running["state"] = "running"
    unfinished = soft_build(19, "2026-04-22")
    unfinished["finished_at"] = ""
    final = soft_build(20, "2026-04-23")

    pipeline = ops._nightly_pipeline(
        "amd-ci", {"builds": [final, unfinished, running, first]}
    )
    rows = {row["number"]: row for row in pipeline["builds"]}

    running_row = rows[18]
    assert running_row["transition_eligible"] is False
    assert running_row["transition_ineligible_reason"] == "build_state_not_completed"
    assert running_row["transitions"]["preceding_build_number"] == 17
    running_pending = running_row["transitions"]["pending_soft"][0]
    assert running_pending["soft_streak"] == 1
    assert running_pending["build_number"] == 17
    assert running_pending["state"] == "soft_failed"
    assert running_pending["current_indeterminate_evidence"]["build_number"] == 18
    assert running_row["failure_movement"]["available"] is False
    assert running_row["failure_movement"]["new"] == []
    assert running_row["failure_movement"]["recurring"] == []
    assert running_row["failure_movement"]["fixed"] == []

    unfinished_row = rows[19]
    assert unfinished_row["transition_eligible"] is False
    assert unfinished_row["transition_ineligible_reason"] == "finished_at_missing"
    assert unfinished_row["transitions"]["preceding_build_number"] == 17
    assert unfinished_row["transitions"]["pending_soft"][0]["soft_streak"] == 1
    assert unfinished_row["transitions"]["pending_soft"][0]["build_number"] == 17
    assert (
        unfinished_row["transitions"]["pending_soft"][0][
            "current_indeterminate_evidence"
        ]["build_number"]
        == 19
    )
    assert unfinished_row["failure_movement"]["available"] is False
    assert unfinished_row["failure_movement"]["new"] == []
    assert unfinished_row["failure_movement"]["recurring"] == []
    assert unfinished_row["failure_movement"]["fixed"] == []

    assert rows[20]["transitions"]["new"][0]["soft_streak"] == 2
    assert rows[20]["transitions"]["new"][0]["transition_change"] == "confirmed"
    assert rows[20]["transitions"]["preceding_build_number"] == 17
    assert rows[20]["failure_movement"]["preceding_build_number"] == 17
    assert [row["name"] for row in rows[20]["failure_movement"]["recurring"]] == [
        name
    ]


def test_nightly_pipeline_replays_soft_hysteresis_and_severity_changes():
    name = "mi300_1: Transition policy"

    def build(number: int, date: str, state: str | None) -> dict:
        jobs = [] if state is None else [
            _job(
                name,
                state,
                f"https://buildkite.com/vllm/amd-ci/builds/{number}/steps/policy",
                soft_failed=state == "soft_fail",
            )
        ]
        return _build(number, date, jobs)

    pipeline = ops._nightly_pipeline("amd-ci", {"builds": [
        build(26, "2026-04-26", "passed"),
        build(25, "2026-04-25", "soft_fail"),
        build(24, "2026-04-24", "failed"),
        build(23, "2026-04-23", "soft_fail"),
        build(22, "2026-04-22", None),
        build(21, "2026-04-21", "soft_fail"),
    ]})
    rows = {row["number"]: row["transitions"] for row in pipeline["builds"]}
    movement = {row["number"]: row["failure_movement"] for row in pipeline["builds"]}

    assert rows[21]["pending_soft"][0]["soft_streak"] == 1
    assert movement[21]["available"] is False
    assert movement[21]["new"] == []
    assert rows[22]["pending_soft"][0]["observed_in_current_build"] is False
    assert movement[22]["available"] is False
    assert movement[22]["new"] == []
    assert movement[22]["recurring"] == []
    assert movement[22]["fixed"] == []
    assert rows[23]["new"][0]["transition_change"] == "confirmed"
    assert movement[23]["preceding_build_number"] == 21
    assert [row["name"] for row in movement[23]["recurring"]] == [name]
    assert rows[24]["recurring"][0]["transition_change"] == "escalated"
    assert [row["name"] for row in movement[24]["recurring"]] == [name]
    assert rows[25]["recurring"][0]["transition_change"] == "deescalated"
    assert rows[25]["recurring"][0]["peak_severity"] == "hard"
    assert rows[26]["fixed"][0]["current_state"] == "passed"
    assert [row["name"] for row in movement[26]["fixed"]] == [name]


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
    assert group["assessment"] == "no_recent_amd_observation"
    assert group["runtime_resolution"]["status"] == "not_observed"
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
    assert all(row["observed_at"] == "2026-04-22T12:00:00Z" for row in retry["retry_attempts"])
    assert all(row["timestamp_source"] == "completed_build" for row in retry["retry_attempts"])
    assert all(row["observed_at"] == "2026-04-22T12:00:00Z" for row in retry["failed_then_passed_recoveries"])
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
    assert retry_analysis["retry_attempts"][0]["observed_at"] == "2026-04-22T10:00:00Z"
    recovery = retry_analysis["failed_then_passed_recoveries"][0]
    assert (recovery["build_number"], recovery["step"], recovery["name"]) == (
        77,
        "retry-step",
        "mi300_1: Retry me",
    )
    assert recovery["failed_job_id"] == "failed-job"
    assert recovery["passed_job_id"] == "passed-job"
    assert recovery["observed_at"] == "2026-04-22T10:00:00Z"


def test_snapshot_bundle_publishes_fast_shell_and_lazy_sections(tmp_path):
    payload = ops.build_snapshot(_fixture_data(tmp_path), generated_at=GENERATED_AT)
    payload["queue"]["history"] = [{
        "ts": GENERATED_AT,
        "schema_version": 2,
        "history_mode": "hourly_queue_wait_peaks",
        "queues": {
            "amd_mi300_1": {
                "waiting": 0,
                "running": 1,
                "p50_wait": None,
                "p99_wait": 2.5,
                "p99_wait_source": "sample_wait",
                "official_wait": {"p50": 1.5, "p95": 12.0, "max": 20.0},
                "sample_wait": {
                    "available": True,
                    "count": 4,
                    "p50": 5.0,
                    "p95": 75.0,
                    "p99": 2.5,
                },
                "wait_sample_count": 4,
                "wait_sample_expected_count": 4,
                "wait_sample_complete": True,
                "current_wait": {"p99": {"value": 2.5, "source": "sample_wait"}},
                "archive_wait_peaks": {
                    "p95": {
                        "value": 12.0,
                        "observed_at": "2026-04-22T12:15:00Z",
                        "source": "official_wait",
                        "provider": "queue_native_metrics",
                    }
                },
                "archive_sample_wait_peaks": {
                    "p95": {
                        "value": 75.0,
                        "observed_at": "2026-04-22T12:25:00Z",
                        "source": "sample_wait",
                        "provider": "scheduled_job_scan",
                        "sample_count": 4,
                        "sample_expected": 4,
                        "sample_complete": True,
                    }
                },
                "unused_collector_field": "not shipped",
            },
        },
    }]
    output = tmp_path / "published" / "operations_v2.json"

    manifest = ops.write_snapshot_bundle(output, payload)

    assert json.loads(output.read_text()) == payload
    assert manifest["bundle_version"] == 1
    assert manifest["generated_at"] == GENERATED_AT
    assert set(manifest["sections"]) == {
        "nightly",
        "amd_test_health",
        "amd_agent_health",
        "reliability",
        "definition_parity",
        "gating",
        "ownership",
        "queue",
        "trajectory",
        "omni",
        "diagnostics",
    }
    assert "reliability" not in manifest["shell"]
    assert "amd_agent_health" not in manifest["shell"]
    assert "ownership" not in manifest["shell"]
    assert len(manifest["shell"]["nightly"]["pipelines"]) == 1
    assert manifest["shell"]["nightly"]["pipelines"][0]["pipeline"] == "amd-ci"
    assert len(manifest["shell"]["nightly"]["pipelines"][0]["builds"]) <= 7

    manifest_path = output.parent / ops.OPERATIONS_MANIFEST_NAME
    assert json.loads(manifest_path.read_text()) == manifest
    for descriptor in manifest["sections"].values():
        section_path = output.parent / descriptor["path"]
        assert section_path.exists()
        assert section_path.stat().st_size == descriptor["bytes"]

    nightly = json.loads(
        (output.parent / manifest["sections"]["nightly"]["path"]).read_text()
    )["nightly"]
    assert "canonical_history" not in nightly
    assert "upstream_parity" not in nightly
    assert {row["pipeline"] for row in nightly["pipelines"]} == {"amd-ci", "ci"}

    queue = json.loads(
        (output.parent / manifest["sections"]["queue"]["path"]).read_text()
    )["queue"]
    assert queue["history"] == []
    assert queue["history_summary"] == payload["queue"]["history_summary"]
    assert queue["history_summary"]["source_path"] == "queue_timeseries.jsonl"
    chart = json.loads((output.parent / ops.QUEUE_HISTORY_CHART_NAME).read_text())
    encoded_row = chart["points"][0][1][0]
    encoded_peak = encoded_row[13][1]
    assert encoded_peak[0] == 12.0
    assert chart["wait_sources"][encoded_peak[1]] == "official_wait"
    assert chart["wait_providers"][encoded_peak[2]] == "queue_native_metrics"
    assert encoded_row[14] == [1.5, 12.0, 20.0]
    assert encoded_row[15] == [5.0, 75.0, 2.5]
    encoded_sample_peak = encoded_row[16][1]
    assert encoded_sample_peak[0] == 75.0
    assert chart["wait_sources"][encoded_sample_peak[1]] == "sample_wait"
    assert chart["wait_providers"][encoded_sample_peak[2]] == "scheduled_job_scan"
    assert encoded_sample_peak[3:] == [4, "2026-04-22T12:25:00Z", 4, True]

    gating = json.loads(
        (output.parent / manifest["sections"]["gating"]["path"]).read_text()
    )["gating"]
    ownership = json.loads(
        (output.parent / manifest["sections"]["ownership"]["path"]).read_text()
    )["ownership"]
    assert "ownership" not in gating
    assert ownership == payload["ownership"]

    omni = json.loads(
        (output.parent / manifest["sections"]["omni"]["path"]).read_text()
    )["omni"]
    assert omni["history"]["summary"]["snapshot_count"] == 1
    assert omni["history"]["points"][0]["amd"]["waiting_observed"] == 2
    assert (
        omni["history"]["provenance"]["source_path"]
        == "queue_timeseries.jsonl"
    )
