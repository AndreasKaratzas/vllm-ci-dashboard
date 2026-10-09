# cspell:ignore kwdefaults
"""Fixture-driven tests for the compact v2 operations snapshot."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from vllm import build_operations_snapshot as ops
from vllm import collect_analytics as analytics
from vllm.ci.reliability_history import (
    hydrate_reliability_observations,
    validate_all_main_reliability,
)


GENERATED_AT = "2026-04-22T12:00:00Z"


def _write_json(path: Path, payload: dict) -> None:
    if path.name == "analytics.json":
        for block in payload.values():
            for build in block.get("builds") or []:
                build.setdefault("branch", "main")
                build.setdefault("web_url", f"https://buildkite.com/vllm/ci/builds/{build['number']}")
    if path.name == "analytics.json":
        for block in payload.values():
            collector = block.get("all_main_reliability") or {}
            collector.setdefault("hardware_scope", "amd_mi_gpu")
            collector.setdefault("job_scope", "amd_gpu")
    if path.name == "workload_mapping.json":
        payload.setdefault("hardware_scope", "amd_mi_gpu")
        payload.setdefault("execution_scope_contract", ops.EXECUTION_SCOPE_CONTRACT)
    if path.name == "ci_health.json":
        for side in ("amd", "upstream"):
            block = payload.get(side) or {}
            if side == "amd":
                block.setdefault("hardware_scope", "amd_mi_gpu")
                block.setdefault("job_scope", "amd_gpu")
            references = [*(block.get("builds") or [])]
            references.extend(block.get(key) for key in ("latest_build", "latest_pipeline_build", "latest_test_signal_build"))
            for build in references:
                if isinstance(build, dict) and build.get("build_number"):
                    build.setdefault("branch", "main")
                    build.setdefault("build_url", f"https://buildkite.com/vllm/ci/builds/{build['build_number']}")
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


def _build(number: int, date: str, jobs: list[dict], pipeline: str = "ci") -> dict:
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
            job["url"] = str(job["url"]).replace("/ci/", f"/{pipeline}/")
        job_id = job.get("job_id") or str(job.get("url") or "").rstrip("/").split("/")[-1]
        job["id"] = job_id
        job["job_id"] = job_id
        job["type"] = "script"
        job["step"] = {
            "id": job.get("step_id") or f"step-{job_id}",
            "key": job.get("step_key") or job_id,
        }
        job["test_duration_mins"] = job.get("dur")
        job["q"] = "amd_mi300_1"
    return row


def _fixture_data(tmp_path: Path) -> Path:
    previous = _build(102, "2026-04-21", [
        _job("Recurring", "soft_fail", "https://buildkite.com/vllm/ci/builds/102/steps/recurring"),
        _job("Fixed", "failed", "https://buildkite.com/vllm/ci/builds/102/steps/fixed"),
        _job(
            "Mixed hard",
            "passed",
            "https://buildkite.com/vllm/ci/builds/102/steps/mixed-hard",
            raw_name="mi300_1: Mixed hard",
        ),
        _job("Mixed soft", "passed", "https://buildkite.com/vllm/ci/builds/102/steps/mixed-soft"),
    ])
    latest = _build(103, "2026-04-22", [
        _job("Recurring", "failed", "https://buildkite.com/vllm/ci/builds/103/steps/recurring", 31),
        _job("New hard", "failed", "https://buildkite.com/vllm/ci/builds/103/steps/new-hard", 42),
        _job("New soft", "soft_fail", "https://buildkite.com/vllm/ci/builds/103/steps/new-soft", 15),
        _job("Fixed", "passed", "https://buildkite.com/vllm/ci/builds/103/steps/fixed", 8),
        _job(
            "Mixed hard",
            "failed",
            "https://buildkite.com/vllm/ci/builds/103/steps/mixed-hard-failed",
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
            "https://buildkite.com/vllm/ci/builds/103/steps/mixed-hard-retry",
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
        _job("Mixed soft", "soft_fail", "https://buildkite.com/vllm/ci/builds/103/steps/mixed-soft", 18),
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
        "summary": {
            "unique_groups": 2,
            "definition_rows": 2,
            "reduced_unique_groups": 2,
            "configured_definition_cases": 2,
            "deduplicated_configured_cases": 2,
            "hardware_cells": 4,
            "passing_cells": 3,
            "failing_cells": 1,
        },
        "rows": [
            {
                "canonical_title": "Fixed",
                "cells": {"mi300": {
                    "exists": True,
                    "latest_state": "failed",
                    "latest_build_number": 103,
                    "latest_url": "https://buildkite.com/vllm/ci/builds/103/steps/fixed",
                }},
            },
            {
                "canonical_title": "Mixed soft",
                "cells": {"mi300": {
                    "exists": True,
                    "latest_state": "passed",
                    "latest_build_number": 103,
                    "latest_url": "https://buildkite.com/vllm/ci/builds/103/steps/mixed-soft",
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
                "main": ["ci"],
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
    assert "gating" not in payload
    assert payload["sources"]["ci_ownership"]["published"] is False


def test_current_ci_snapshot_rejects_legacy_amd_evidence_and_latency_fallback(tmp_path):
    data_dir = _fixture_data(tmp_path)
    _write_jsonl(data_dir / "test_results" / "2026-04-22_amd.jsonl", [{
        "pipeline": "amd-ci", "build_number": 103, "job_name": "mi300_1: Legacy failure",
        "status": "failed", "name": "test_legacy", "date": "2026-04-22",
    }])
    payload = ops.build_snapshot(data_dir, generated_at=GENERATED_AT)
    assert payload["nightly"]["primary_pipeline"] == "ci"
    assert payload["nightly"]["primary_cohort"] == "ci-amd"
    assert {row["cohort_id"] for row in payload["nightly"]["pipelines"]} == {"ci-amd"}
    assert all(row["source_pipeline"] == "ci" for row in payload["nightly"]["pipelines"])
    assert payload["amd_test_health"]["source_pipeline"] == "ci"
    assert payload["amd_test_health"]["job_scope"] == "amd_gpu"
    assert payload["amd_test_health"]["hardware_scope"] == "amd_mi_gpu"
    assert payload["amd_test_health"]["provenance"]["nightly_metadata"]["source_key"] == "ci.builds"
    assert payload["amd_test_health"]["available"] is False
    assert payload["amd_test_health"]["group_catalog"] == []
    assert payload["latency"]["available"] is False
    assert payload["latency"]["rows"] == []
    assert not {"gating", "trajectory"} & payload.keys()


def test_amd_test_health_uses_authoritative_job_states_and_preserves_evidence(tmp_path):
    alpha = "mi300_1: Alpha tests"
    beta = "mi355_2: Beta tests"
    unknown = "mi325_4: Unknown tests"
    stable = "mi300_2: Stable tests"
    latest_only = "mi250_1: Latest only"
    _write_json(tmp_path / "analytics.json", {
        "ci": {
            "generated_at": GENERATED_AT,
            "builds": [
                {
                    "number": 301,
                    "date": "2026-04-22",
                    "created_at": "2026-04-22T09:00:01Z",
                    "web_url": "https://buildkite.com/vllm/ci/builds/301",
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
                    "web_url": "https://buildkite.com/vllm/ci/builds/300",
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
    _write_json(tmp_path / "ci_health.json", {
        "amd": {
            "latest_test_signal_build": {
                "build_number": 301,
                "unique_test_groups": 3,
                "test_groups_passing_or": 2,
                "test_groups_passing_all": 2,
                "test_groups_partial": 0,
            },
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
            "pipeline": "ci",
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
            "pipeline": "ci",
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
            "pipeline": "ci",
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
            "pipeline": "ci",
            "date": "2026-04-21",
        },
        {
            "name": "test_unknown",
            "status": "xfailed",
            "duration_secs": 1,
            "job_name": unknown,
            "step_id": "unknown-step-300",
            "build_number": 300,
            "pipeline": "ci",
            "date": "2026-04-21",
        },
        {
            "name": "test_stable",
            "status": "passed",
            "duration_secs": 2,
            "job_name": stable,
            "job_id": "stable-300",
            "build_number": 300,
            "pipeline": "ci",
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
            "pipeline": "ci",
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
            "pipeline": "ci",
            "date": "2026-04-22",
        },
        {
            "name": "test_stable",
            "status": "passed",
            "duration_secs": 2,
            "job_name": stable,
            "job_id": "stable-301",
            "build_number": 301,
            "pipeline": "ci",
            "date": "2026-04-22",
        },
        {
            "name": "test_latest",
            "status": "passed",
            "duration_secs": 2,
            "job_name": latest_only,
            "job_id": "latest-hard-301",
            "build_number": 301,
            "pipeline": "ci",
            "date": "2026-04-22",
        },
    ], trailing="\n")

    payload = ops.build_snapshot(tmp_path, generated_at=GENERATED_AT)
    health = payload["amd_test_health"]
    groups = {row["exact_job_name"]: row for row in health["group_catalog"]}

    assert payload["schema_version"] == 2
    assert health["available"] is True
    assert health["source_pipeline"] == "ci"
    assert health["cohort"]["aggregation_key"] == ["build_number", "exact_job_name"]
    assert health["summary"] == {
        "build_count": 2,
        "retained_group_count": 5,
        "group_count": 5,
        "union_group_count": 5,
        "retained_job_variant_count": 5,
        "latest_group_count": 3,
        "latest_job_variant_count": 3,
        "latest_build_number": 301,
        "latest_build_url": "https://buildkite.com/vllm/ci/builds/301",
        "latest_url": "https://buildkite.com/vllm/ci/builds/301",
        "latest_observed_at": "2026-04-22T09:00:01Z",
        "latest_state_counts": {
            "passed": 1,
            "soft": 1,
            "hard": 1,
            "unknown": 0,
        },
        "latest_job_variant_state_counts": {
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
        "latest_passed_job_variant_count": 1,
        "latest_soft_failed_job_variant_count": 1,
        "latest_hard_failed_job_variant_count": 1,
        "latest_incident_job_variant_count": 2,
        "latest_unknown_job_variant_count": 0,
        "latest_test_group_counts": {
            "available": True,
            "build_number": 301,
            "job_variant_build_number": 301,
            "test_signal_build_number": 301,
            "total": 3,
            "passing": 2,
            "non_passing": 1,
            "passing_all": 2,
            "partial": 0,
            "pass_percentage": 66.7,
            "pass_rate_pct": 66.7,
            "source": "ci_health.amd.latest_test_signal_build",
            "passing_policy": "passes_on_any_observed_hardware",
            "count_basis": (
                "unique logical test-group identities observed in this build; "
                "when its commit matches the pinned AMD definitions, normalized "
                "label plus agent pool resolves the configuration identity family, "
                "preserving topology-distinct routes; hardware-specific executions "
                "in one family and configured %N shard jobs count once per family; "
                "without an aligned map they fall back to the normalized group; "
                "configured-definition inventories are separate"
            ),
            "reason": None,
        },
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
        "hardware_counts": {"mi250": 1, "mi300": 2, "mi325": 1, "mi355": 1},
        "hardware_variant_counts": {
            "mi250_1": 1,
            "mi300_1": 1,
            "mi300_2": 1,
            "mi325_4": 1,
            "mi355_2": 1,
        },
        "latest_hardware_counts": {"mi250": 1, "mi300": 2},
    }
    assert sum(health["summary"]["latest_state_counts"].values()) == 3

    alpha_group = groups[alpha]
    assert alpha_group["id"] == hashlib.sha1(f"ci:{alpha}".encode()).hexdigest()[:20]
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
        "https://buildkite.com/vllm/ci/builds/301/steps/canvas"
        "?jid=alpha-soft-301&tab=output"
    )
    assert latest_alpha["build_url"] == "https://buildkite.com/vllm/ci/builds/301"

    beta_group = groups[beta]
    assert beta_group["latest_state"] == "hard"
    assert beta_group["soft_failed"] == 0
    assert beta_group["hard_failed"] == 1
    assert beta_group["latest_url"].endswith("?jid=beta-hard-300&tab=output")
    assert beta_group["hardware"] == "mi355"
    assert beta_group["hardware_variant"] == "mi355_2"
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
    assert latest_build["observed_job_variants"] == 3
    assert latest_build["passed_job_variants"] == 1
    assert latest_build["soft_failed_job_variants"] == 1
    assert latest_build["hard_failed_job_variants"] == 1
    assert latest_build["incident_job_variants"] == 2
    assert latest_build["unknown_job_variants"] == 0
    assert latest_build["job_variant_state_counts"] == latest_build["state_counts"]
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
        "ci": {
            "generated_at": GENERATED_AT,
            "builds": [
                {
                    "number": 11651,
                    "date": "2026-08-04",
                    "created_at": "2026-08-04T09:00:00Z",
                    "web_url": "https://buildkite.com/vllm/ci/builds/11651",
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
                    "web_url": "https://buildkite.com/vllm/ci/builds/11591",
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
        "pipeline": "ci",
        "date": "2026-08-03",
    }], trailing="\n")
    _write_jsonl(tmp_path / "test_results" / "2026-08-04_amd.jsonl", [{
        "name": "test_retry_sensitive",
        "status": "passed",
        "duration_secs": 1,
        "job_name": group_name,
        "job_id": "nightly-job",
        "build_number": 11651,
        "pipeline": "ci",
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


@pytest.mark.parametrize("prefix", ["", "mi355_dpx: "])
def test_amd_test_health_preserves_named_pool_with_current_ci_metadata(
    tmp_path, prefix
):
    exact_name = prefix + ":amd: (MI355 DPX) Attention Kernels Shard 2"
    _write_jsonl(tmp_path / "test_results" / "2026-04-22_amd.jsonl", [{
        "name": "test_attention",
        "status": "passed",
        "job_name": exact_name,
        "queue": "amd_mi355_dpx",
        "build_number": 400,
        "pipeline": "ci",
        "date": "2026-04-22",
    }], trailing="\n")

    _write_json(tmp_path / "analytics.json", {"ci": {"builds": [
        _build(400, "2026-04-22", [_job(exact_name, "passed", "https://buildkite.com/vllm/ci/builds/400#job")])
    ]}})

    health = ops.build_snapshot(tmp_path, generated_at=GENERATED_AT)["amd_test_health"]
    group = health["group_catalog"][0]

    assert group["exact_job_name"] == exact_name
    assert group["name"] == "Attention Kernels Shard 2"
    assert group["hardware"] == "mi355"
    assert group["hardware_variant"] == "mi355_dpx"
    assert group["queue"] == "amd_mi355_dpx"


def test_amd_test_health_requires_same_build_for_logical_group_counts(tmp_path):
    group_name = ":amd: (MI355) Attention Kernels Shard 2"
    assert ops._amd_test_job_labels(":computer: (CPU) CPU Unit Tests") == (
        "CPU Unit Tests",
        "cpu",
        "cpu",
        "cpu",
    )
    assert ops._amd_test_job_labels(
        "mi355_2: :amd: (MI355) Attention Kernels Shard 1"
    ) == (
        "Attention Kernels Shard 1",
        "mi355",
        "mi355_2",
        "amd_mi355_2",
    )
    _write_json(tmp_path / "analytics.json", {
        "ci": {
            "builds": [{
                "number": 400,
                "created_at": "2026-08-05T09:00:00Z",
                "web_url": "https://buildkite.com/vllm/ci/builds/400",
                "jobs": [{
                    "raw_name": group_name,
                    "job_id": "standard-label-job",
                    "state": "passed",
                    "q": "amd_mi355_1",
                }],
            }],
        },
    })
    _write_json(tmp_path / "ci_health.json", {
        "amd": {
            "latest_test_signal_build": {
                "build_number": 399,
                "unique_test_groups": 157,
                "test_groups_passing_or": 156,
                "test_groups_passing_all": 155,
                "test_groups_partial": 1,
            },
        },
    })
    _write_jsonl(tmp_path / "test_results" / "2026-08-05_amd.jsonl", [{
        "name": "__passed__ (1)",
        "status": "passed",
        "job_name": group_name,
        "job_id": "standard-label-job",
        "build_number": 400,
        "pipeline": "ci",
        "date": "2026-08-05",
    }])

    health = ops.build_snapshot(tmp_path, generated_at=GENERATED_AT)["amd_test_health"]
    counts = health["summary"]["latest_test_group_counts"]
    group = health["group_catalog"][0]

    assert counts["available"] is False
    assert counts["reason"] == "build_mismatch"
    assert counts["job_variant_build_number"] == 400
    assert counts["test_signal_build_number"] == 399
    assert counts["total"] is None
    assert health["summary"]["latest_job_variant_count"] == 1
    assert group["exact_job_name"] == group_name
    assert group["display_name"] == "Attention Kernels Shard 2"
    assert group["hardware"] == "mi355"
    assert group["hardware_variant"] == "mi355_1"
    assert group["queue"] == "amd_mi355_1"
    inventory = health["latest_logical_test_groups"]
    assert inventory["available"] is False
    assert inventory["reason"] == "latest_test_group_counts_unavailable"
    assert inventory["rows"] == []

    _write_json(tmp_path / "ci_health.json", {
        "amd": {
            "latest_test_signal_build": {
                "build_number": 400,
                "unique_test_groups": 157,
                "test_groups_passing_or": 156,
                "test_groups_passing_all": 155,
                "test_groups_partial": 1,
            },
        },
    })
    health = ops.build_snapshot(
        tmp_path,
        generated_at=GENERATED_AT,
    )["amd_test_health"]
    counts = health["summary"]["latest_test_group_counts"]

    assert counts["available"] is True
    assert counts["build_number"] == 400
    assert counts["total"] == 157
    assert counts["passing"] == 156
    assert counts["non_passing"] == 1
    assert counts["pass_percentage"] == 99.4
    inventory = health["latest_logical_test_groups"]
    assert inventory["available"] is False
    assert inventory["reason"] == "logical_group_reconciliation_failed"
    assert inventory["rows"] == []
    assert inventory["reconciliation"][
        "matches_latest_test_group_counts"
    ] is False


def test_amd_test_health_publishes_reconciled_logical_group_inventory(tmp_path):
    commit = "a" * 40
    variants = [
        (
            "mi300_1: :amd: (MI300) Routed Alpha",
            "routed-mi300",
            "passed",
            "passed",
        ),
        (
            "mi355_1: :amd: (MI355) Routed Beta",
            "routed-mi355",
            "failed",
            "failed",
        ),
        (
            "mi300_1: :amd: (MI300) Attention Kernels Shard 1",
            "attention-1",
            "passed",
            "passed",
        ),
        (
            "mi300_1: :amd: (MI300) Attention Kernels Shard 2",
            "attention-2",
            "passed",
            "passed",
        ),
        (
            "mi355_1: :amd: (MI355) Broken Group",
            "broken",
            "failed",
            "error",
        ),
    ]
    _write_json(tmp_path / "analytics.json", {
        "ci": {
            "builds": [{
                "number": 500,
                "commit": commit,
                "created_at": "2026-08-06T09:00:00Z",
                "web_url": "https://buildkite.com/vllm/ci/builds/500",
                "jobs": [
                    {
                        "raw_name": name,
                        "job_id": job_id,
                        "state": terminal_state,
                        "q": (
                            "amd_mi355_1"
                            if name.startswith("mi355")
                            else "amd_mi300_1"
                        ),
                        "finished_at": "2026-08-06T10:00:00Z",
                    }
                    for name, job_id, terminal_state, _ in variants
                ],
            }],
        },
    })
    _write_json(tmp_path / "ci_health.json", {
        "amd": {
            "latest_test_signal_build": {
                "build_number": 500,
                "unique_test_groups": 3,
                "test_groups_passing_or": 2,
                "test_groups_passing_all": 1,
                "test_groups_partial": 1,
            },
        },
    })
    _write_json(tmp_path / "config_parity.json", {
        "source": {"commit_sha": commit},
        "matches": [{
            "amd_identity_family_key": "routed family (2 gpus)",
            "amd_member_labels": [
                ":amd: (MI300) Routed Alpha",
                ":amd: (MI355) Routed Beta",
            ],
            "amd_member_agent_pools": ["mi300_1", "mi355_1"],
        }],
    })
    _write_json(tmp_path / "shard_bases.json", [
        "attention kernels shard",
    ])
    _write_jsonl(tmp_path / "test_results" / "2026-08-06_amd.jsonl", [
        {
            "name": f"test_{job_id}",
            "status": test_status,
            "job_name": name,
            "job_id": job_id,
            "build_number": 500,
            "pipeline": "ci",
            "date": "2026-08-06",
        }
        for name, job_id, _, test_status in variants
    ])

    inventory = ops.build_snapshot(
        tmp_path,
        generated_at=GENERATED_AT,
    )["amd_test_health"]["latest_logical_test_groups"]

    assert inventory["available"] is True
    assert inventory["reason"] is None
    assert inventory["build_number"] == 500
    assert inventory["build_commit"] == commit
    assert inventory["definition_commit"] == commit
    assert inventory["route_map_aligned"] is True
    assert inventory["shard_base_count"] == 1
    assert inventory["summary"] == {
        "total": 3,
        "passing": 2,
        "passing_all": 1,
        "partial": 1,
        "non_passing": 1,
        "job_variant_count": 5,
        "state_counts": {
            "passing_all": 1,
            "partial": 1,
            "non_passing": 1,
        },
    }
    assert inventory["reconciliation"] == {
        "matches_latest_test_group_counts": True,
        "expected": {
            "total": 3,
            "passing": 2,
            "passing_all": 1,
            "partial": 1,
            "non_passing": 1,
        },
        "derived": {
            "total": 3,
            "passing": 2,
            "passing_all": 1,
            "partial": 1,
            "non_passing": 1,
        },
    }
    rows = {row["logical_key"]: row for row in inventory["rows"]}
    assert set(rows) == {
        "attention kernels shard",
        "broken group",
        "routed family (2 gpus)",
    }
    routed = rows["routed family (2 gpus)"]
    assert routed["label"] == "Routed family (2 gpus)"
    assert routed["state"] == "partial"
    assert routed["passing"] is True
    assert routed["hardware_count"] == 2
    assert routed["job_variant_count"] == 2
    assert routed["hardware_states"] == [
        {"hardware": "mi300", "state": "passing"},
        {"hardware": "mi355", "state": "failing"},
    ]
    evidence = {row["exact_job_name"]: row for row in routed["job_variants"]}
    assert evidence[variants[0][0]]["job_id"] == "routed-mi300"
    assert evidence[variants[0][0]]["terminal_state"] == "passed"
    assert evidence[variants[0][0]]["test_signal_state"] == "passing"
    assert evidence[variants[1][0]]["terminal_state"] == "hard"
    assert evidence[variants[1][0]]["test_signal_state"] == "failing"
    assert evidence[variants[1][0]]["job_url"].endswith(
        "?jid=routed-mi355&tab=output"
    )
    attention = rows["attention kernels shard"]
    assert attention["state"] == "passing_all"
    assert attention["job_variant_count"] == 2


def test_amd_test_health_is_unavailable_for_missing_or_corrupt_results(tmp_path):
    missing = ops.build_snapshot(tmp_path, generated_at=GENERATED_AT)["amd_test_health"]

    assert missing["available"] is False
    assert missing["source_pipeline"] == "ci"
    assert missing["summary"]["build_count"] == 0
    assert missing["summary"]["retained_group_count"] == 0
    assert missing["summary"]["group_count"] == 0
    assert missing["summary"]["latest_build_number"] is None
    assert missing["summary"]["latest_job_variant_count"] == 0
    assert missing["summary"]["latest_test_group_counts"]["available"] is False
    assert missing["summary"]["latest_test_group_counts"]["reason"] == (
        "latest_job_variant_build_unavailable"
    )
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
    assert corrupt["provenance"]["test_results"]["malformed_rows"] == 2
    assert corrupt["provenance"]["test_results"]["ignored_rows"] == 1


def test_latest_infrastructure_blocked_nightly_is_not_dropped_or_given_stale_results(tmp_path):
    data_dir = _fixture_data(tmp_path)
    health_path = data_dir / "ci_health.json"
    health = json.loads(health_path.read_text())
    blocked = {
        "branch": "main",
        "build_number": 104,
        "build_url": "https://buildkite.com/vllm/ci/builds/104",
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


def test_ci_health_publication_retention_is_propagated_to_nightly(tmp_path):
    data_dir = _fixture_data(tmp_path)
    health_path = data_dir / "ci_health.json"
    health = json.loads(health_path.read_text())
    health["publication_retention"] = {
        "policy": "retain_newest_whole_build_summaries",
        "max_bytes": 1_048_576,
        "complete_relative_to_source": False,
        "aggregate_scalars_complete": True,
        "builds": {
            "amd": {"source": 10, "published": 4, "omitted": 6, "complete": False},
            "upstream": {"source": 8, "published": 8, "omitted": 0, "complete": True},
        },
    }
    health_path.write_text(json.dumps(health))

    payload = ops.build_snapshot(data_dir, generated_at=GENERATED_AT)

    amd_retention = payload["nightly"]["canonical_history"][
        "ci_health_publication_retention"
    ]
    assert amd_retention["complete_relative_to_source"] is False
    assert amd_retention["aggregate_scalars_complete"] is True
    assert amd_retention["builds"] == {
        "source": 10,
        "published": 4,
        "omitted": 6,
        "complete": False,
    }
    assert "upstream_parity" not in payload["nightly"]


def test_nightly_pipeline_projects_newer_core_references_over_analytics_lkg():
    analytics_build = _build(100, "2026-04-20", [])
    latest_signal = {
        "build_number": 101,
        "created_at": "2026-04-21T09:00:00Z",
        "finished_at": "2026-04-21T10:00:00Z",
        "state": "passed",
        "has_test_results": True,
        "unique_test_groups": 3,
        "test_groups_passing_or": 2,
        "test_groups_passing_all": 1,
        "test_groups_partial": 1,
    }
    latest_pipeline = {
        "build_number": 102,
        "created_at": "2026-04-22T09:00:00Z",
        "state": "failed",
        "has_test_results": False,
        "test_jobs_blocked": 4,
    }

    pipeline = ops._nightly_pipeline(
        "ci",
        {"builds": [analytics_build]},
        {
            "builds": [latest_pipeline, latest_signal],
            "latest_pipeline_build": latest_pipeline,
            "latest_test_signal_build": latest_signal,
        },
    )

    assert [row["number"] for row in pipeline["builds"]] == [102, 101, 100]
    assert pipeline["builds"][0]["test_jobs_blocked"] == 4
    signal_row = pipeline["builds"][1]
    assert signal_row["unique_test_groups"] == 3
    assert signal_row["test_groups_passing_or"] == 2
    assert signal_row["test_groups_passing_all"] == 1
    assert signal_row["test_groups_partial"] == 1


def test_nightly_pipeline_marks_analytics_head_ahead_of_older_core():
    core_head = {
        "build_number": 102,
        "created_at": "2026-04-21T09:00:00Z",
        "finished_at": "2026-04-21T10:00:00Z",
        "state": "passed",
        "has_test_results": True,
    }
    analytics_head = _build(
        103,
        "2026-04-22",
        [
            _job(
                "New analytics result",
                "failed",
                "https://buildkite.com/vllm/ci/builds/103/steps/new-result",
            )
        ],
    )

    pipeline = ops._nightly_pipeline(
        "ci",
        {"builds": [analytics_head, _build(102, "2026-04-21", [])]},
        {
            "builds": [core_head],
            "latest_pipeline_build": core_head,
            "latest_test_signal_build": core_head,
        },
    )

    assert [row["number"] for row in pipeline["builds"]] == [103, 102]
    assert pipeline["head_alignment"] == {
        "status": "analytics_ahead_of_ci_health",
        "canonical_build_number": 103,
        "ci_health_build_number": 102,
        "analytics_ahead_build_numbers": [103],
    }
    [popup_row] = pipeline["builds"][0]["failed_groups"]
    assert popup_row["url"].endswith("/builds/103/steps/new-result")


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


def test_attention_uses_reconciled_logical_amd_runtime_groups():
    amd_health = {
        "summary": {
            "latest_test_group_counts": {
                "available": True,
                "build_number": 123,
                "total": 4,
            },
        },
        "latest_logical_test_groups": {
            "available": True,
            "build_number": 123,
            "summary": {
                "passing_all": 0,
                "partial": 2,
                "non_passing": 2,
            },
            "rows": [
                {"state": "partial"},
                {"state": "partial"},
                {"state": "non_passing"},
                {"state": "non_passing"},
            ],
            "reconciliation": {
                "matches_latest_test_group_counts": True,
            },
        },
    }

    attention = ops._attention(
        {"pipelines": [{"builds": []}]},
        {},
        {"snapshot": {}},
        {"status": "healthy", "current": {}},
        amd_health,
    )

    assert attention == [{
        "kind": "amd_logical_groups_not_fully_passing",
        "severity": "warning",
        "count": 4,
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
    assert payload["nightly"]["pipeline_order"] == ["ci-amd"]
    assert payload["nightly"]["transition_policy_id"] == "confirmed-incidents-v1"
    assert (
        payload["nightly"]["failure_movement_policy_id"]
        == "observed-failure-movement-v1"
    )
    assert payload["nightly"]["pipelines"][0]["pipeline"] == "ci"
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
    assert new_hard["url"].endswith("/steps/canvas?jid=new-hard&tab=output")
    assert latest["transitions"]["fixed"][0]["url"].endswith("/builds/102/steps/canvas?jid=fixed&tab=output")
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
    assert "trajectory" not in payload
    assert all("timestamp" in source for source in payload["sources"].values())


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
            "hardware_scope": "amd_mi_gpu",
            "execution_scope_contract": ops.EXECUTION_SCOPE_CONTRACT,
            "scope": {
                "queues": ["amd_mi300_1"],
                "workload_pipelines": {"omni": ["vllm-omni-amd-ci"], "main": ["ci"]},
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
            "hardware_scope": "amd_mi_gpu",
            "execution_scope_contract": ops.EXECUTION_SCOPE_CONTRACT,
            "scope": {
                "queues": ["amd_mi300_1"],
                "workload_pipelines": {"omni": ["vllm-omni-amd-ci"], "main": ["ci"]},
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


def test_group_catalog_retains_linked_terminal_main_observations(tmp_path):
    reliability = ops.build_snapshot(_fixture_data(tmp_path), generated_at=GENERATED_AT)["reliability"]
    candidates = {row["name"]: row for row in reliability["group_catalog"] if row["observation_count"] > 0}

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
    assert failed["queue"] == "amd_mi300_1"
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
            "job_id": "mi325",
            "step_id": "mi325-step",
            "build_url": "https://buildkite.com/vllm/ci/builds/104",
            "job_url": (
                "https://buildkite.com/vllm/ci/builds/104/steps/canvas"
                "?jid=mi325&tab=output"
            ),
            "observed_at": "2026-04-22T10:00:00Z",
        }],
    }

    catalog, _, _ = ops._collector_main_catalog(
        {
            "schema_version": 2,
            "builds": [{
                "number": 104,
                "url": "https://buildkite.com/vllm/ci/builds/104",
                "commit": "abc104",
                "message": "Full CI run - nightly",
                "branch": "main",
                "state": "passed",
                "created_at": "2026-04-22T09:00:00Z",
                "started_at": "2026-04-22T09:01:00Z",
                "finished_at": "2026-04-22T10:00:00Z",
                "is_canonical_nightly": True,
            }],
            "groups": [source],
        },
        pipeline_slug="ci",
    )

    assert catalog[0]["hardware"] == "mi325"
    assert catalog[0]["queues"] == ["amd_mi325_1"]


def test_normalized_reliability_produces_identical_popup_catalog_to_legacy():
    build = _retarget_build(
        _build(
            105,
            "2026-04-22",
            [
                _job(
                    "mi300_1: Popup parity",
                    "failed",
                    "https://buildkite.com/vllm/ci/builds/105/steps/popup-parity",
                    job_id="popup-attempt",
                    step_id="popup-step",
                    step_key="popup-parity",
                    q="amd_mi300_1",
                )
            ],
            pipeline="ci",
        ),
        "ci",
    )
    build["commit"] = "0123456789abcdef"
    build["message"] = "Full CI run - nightly popup parity"
    normalized = analytics.build_all_main_reliability(
        [build],
        pipeline_slug="ci",
        window_days=30,
        generated_at=GENERATED_AT,
        nightly_pattern="nightly",
    )
    legacy = json.loads(json.dumps(normalized))
    legacy["schema_version"] = 1
    for group in legacy["groups"]:
        group["observations"] = hydrate_reliability_observations(
            normalized,
            group["observations"],
            pipeline_slug="ci",
        )

    assert validate_all_main_reliability(normalized, "ci")
    assert validate_all_main_reliability(legacy, "ci")
    normalized_catalog = ops._collector_main_catalog(normalized, pipeline_slug="ci")
    legacy_catalog = ops._collector_main_catalog(legacy, pipeline_slug="ci")

    assert normalized_catalog == legacy_catalog
    popup_observation = normalized_catalog[0][0]["observations"][0]
    assert popup_observation["message"] == build["message"]
    assert popup_observation["commit"] == build["commit"]
    assert popup_observation["build_url"].endswith("/ci/builds/105")
    assert "jid=popup-attempt" in popup_observation["job_url"]
    assert "sid=popup-step" in popup_observation["step_url"]


def test_nightly_fixed_requires_an_observed_pass():
    previous = _build(10, "2026-04-20", [
        _job("Missing now", "failed", "https://buildkite.com/vllm/ci/builds/10/steps/missing"),
        _job("Actually fixed", "failed", "https://buildkite.com/vllm/ci/builds/10/steps/fixed"),
        _job("Held evidence", "failed", "https://buildkite.com/vllm/ci/builds/10/steps/held"),
    ])
    current = _build(11, "2026-04-21", [
        _job("Actually fixed", "passed", "https://buildkite.com/vllm/ci/builds/11/steps/fixed"),
        _job("Held evidence", "skipped", "https://buildkite.com/vllm/ci/builds/11/steps/held"),
    ])

    row = ops._nightly_pipeline("ci", {"builds": [current, previous]})["builds"][0]

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
        "https://buildkite.com/vllm/ci/builds/15/steps/original",
        job_id="retry-original",
        step_key="linked-retry",
        retried_in_job_id="retry-final",
    )
    final = _job(
        "mi300_1: Linked retry",
        "passed",
        "https://buildkite.com/vllm/ci/builds/15/steps/final",
        job_id="retry-final",
        step_key="linked-retry",
    )

    for attempts in ([original, final], [final, original]):
        build = _build(15, "2026-04-20", attempts)
        observations = ops._nightly_group_observations("ci", build)
        assert len(observations) == 1
        outcome, evidence = next(iter(observations.values()))
        assert outcome == "passed"
        assert evidence["url"].endswith("?jid=retry-final&tab=output")

        latest = ops._nightly_pipeline(
            "ci", {"builds": [build]}
        )["builds"][0]
        assert latest["transitions"]["new"] == []
        assert latest["transitions"]["pending_soft"] == []


def test_operations_and_analytics_share_strict_nightly_signal_ids():
    jobs = [
        _job(
            "mi300_1: Strict signal",
            "failed",
            "https://buildkite.com/vllm/ci/builds/16/steps/base",
            job_id="strict-base",
            step_key="strict-step",
        ),
        _job(
            "mi300_1: Strict signal 2/2",
            "failed",
            "https://buildkite.com/vllm/ci/builds/16/steps/raw",
            job_id="strict-raw",
            step_key="strict-step",
        ),
        _job(
            "mi300_1: Strict signal",
            "failed",
            "https://buildkite.com/vllm/ci/builds/16/steps/step",
            job_id="strict-step",
            step_key="other-step",
        ),
        _job(
            "mi300_1: Strict signal",
            "failed",
            "https://buildkite.com/vllm/ci/builds/16/steps/queue",
            job_id="strict-queue",
            step_key="strict-step",
            q="amd_mi300_2",
        ),
        _job(
            "mi355_1: Strict signal",
            "failed",
            "https://buildkite.com/vllm/ci/builds/16/steps/hardware",
            job_id="strict-hardware",
            step_key="strict-step",
            q="amd_mi355_1",
        ),
    ]
    build = _build(16, "2026-04-20", jobs)

    operations_ids = set(ops._nightly_group_observations("ci", build))
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
            f"https://buildkite.com/vllm/ci/builds/{number}/steps/{slug}",
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
            "ci", {"builds": [current, previous]}
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
                f"https://buildkite.com/vllm/ci/builds/{number}/steps/hold",
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
        "ci", {"builds": [final, unfinished, running, first]}
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
                f"https://buildkite.com/vllm/ci/builds/{number}/steps/policy",
                soft_failed=state == "soft_fail",
            )
        ]
        return _build(number, date, jobs)

    pipeline = ops._nightly_pipeline("ci", {"builds": [
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
            "hardware": "mi300",
            "queue": "amd_mi300_4",
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
    assert group["hardware"] == "mi300"
    assert group["queues"] == ["amd_mi300_4"]
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


def test_snapshot_retains_thirty_amd_nightlies_without_foreign_runtime_groups(tmp_path):
    data_dir = _fixture_data(tmp_path)
    analytics_payload = json.loads((data_dir / "analytics.json").read_text())
    start = datetime(2026, 3, 1)
    amd_builds = [
        _build(
            1000 + index,
            (start + timedelta(days=index)).strftime("%Y-%m-%d"),
            [_job(f"Group {index}", "passed", f"https://buildkite.com/vllm/ci/builds/{1000 + index}")],
        )
        for index in range(35)
    ]
    for index, build in enumerate(amd_builds):
        if index >= 31:
            build["jobs"].append(_job("CUDA group", "passed", build["web_url"] + "#cuda", q="gpu_1"))
    analytics_payload["ci"].update({"days": 30, "builds": amd_builds})
    _write_json(data_dir / "analytics.json", analytics_payload)

    nightly = ops.build_snapshot(data_dir, generated_at=GENERATED_AT)["nightly"]

    canonical = nightly["canonical_history"]
    assert canonical["pipeline"] == "ci"
    assert canonical["role"] == "canonical_amd_nightly"
    assert canonical["builds_available"] == 35
    assert len(canonical["builds"]) == 30
    assert [row["number"] for row in canonical["builds"][:2]] == [1034, 1033]
    assert canonical["builds"][-1]["number"] == 1005
    assert nightly["pipelines"][0]["builds"] == canonical["builds"]

    assert "upstream_parity" not in nightly
    assert nightly["pipeline_order"] == ["ci-amd"]
    assert all(build["total_groups"] == 1 for build in canonical["builds"])
    assert "CUDA group" not in json.dumps(canonical)


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
            "hardware_scope": "amd_mi_gpu", "job_scope": "amd_gpu",
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
        "web_url": "https://buildkite.com/vllm/ci/builds/77",
    }])

    builds = analytics.collect_pipeline("ci", "token", 1)
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
    history_generated_at = "2026-04-22T12:30:00Z"
    payload["queue"]["history"] = [{
        "ts": history_generated_at,
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
    assert manifest["bundle_version"] == ops.OPERATIONS_PRODUCER_BUNDLE_VERSION
    assert manifest["generated_at"] == GENERATED_AT
    assert set(manifest["sections"]) == {
        "nightly",
        "amd_test_health",
        "amd_agent_health",
        "comparison",
        "reliability",
        "definition_parity",
        "test_group_parity",
        "ownership",
        "queue",
        "omni",
        "diagnostics",
    }
    assert "reliability" not in manifest["shell"]
    assert "amd_agent_health" not in manifest["shell"]
    assert "ownership" not in manifest["shell"]
    assert len(manifest["shell"]["nightly"]["pipelines"]) == 1
    assert manifest["shell"]["nightly"]["pipelines"][0]["pipeline"] == "ci"
    assert len(manifest["shell"]["nightly"]["pipelines"][0]["builds"]) <= 7

    manifest_path = output.parent / ops.OPERATIONS_MANIFEST_NAME
    assert json.loads(manifest_path.read_text()) == manifest
    for descriptor in manifest["sections"].values():
        section_path = output.parent / descriptor["path"]
        assert section_path.exists()
        assert section_path.stat().st_size == descriptor["bytes"]

    comparison = json.loads(
        (output.parent / manifest["sections"]["comparison"]["path"]).read_text()
    )
    assert comparison == {"latency": payload["latency"]}
    assert not {"gating", "trajectory", "comparison_retry_evidence"} & manifest["sections"].keys()

    nightly = json.loads(
        (output.parent / manifest["sections"]["nightly"]["path"]).read_text()
    )["nightly"]
    assert "canonical_history" not in nightly
    assert "upstream_parity" not in nightly
    assert {row["cohort_id"] for row in nightly["pipelines"]} == {"ci-amd"}

    queue = json.loads(
        (output.parent / manifest["sections"]["queue"]["path"]).read_text()
    )["queue"]
    assert queue["history"] == []
    assert queue["history_summary"] == payload["queue"]["history_summary"]
    assert queue["history_summary"]["source_path"] == "queue_timeseries.jsonl"
    chart = json.loads((output.parent / ops.QUEUE_HISTORY_CHART_NAME).read_text())
    assert chart["generated_at"] == history_generated_at
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

    ownership = json.loads(
        (output.parent / manifest["sections"]["ownership"]["path"]).read_text()
    )["ownership"]
    assert {
        key: value
        for key, value in ownership.items()
        if key != "operations_publication_retention"
    } == payload["ownership"]
    assert ownership["operations_publication_retention"][
        "complete_relative_to_source"
    ] is True

    omni = json.loads(
        (output.parent / manifest["sections"]["omni"]["path"]).read_text()
    )["omni"]
    assert omni["history"]["summary"]["snapshot_count"] == 1
    assert omni["history"]["points"][0]["amd"]["waiting_observed"] == 2
    assert (
        omni["history"]["provenance"]["source_path"]
        == "queue_timeseries.jsonl"
    )


def _retention_observation(
    build_number: int,
    state: str,
    *,
    padding: int = 0,
) -> dict:
    return {
        "source_pipeline": "ci",
        "group_id": "group",
        "build_number": build_number,
        "state": state,
        "observed_at": f"2026-04-{build_number:02d}T12:00:00Z",
        "job_url": (
            f"https://buildkite.com/vllm/ci/builds/{build_number}/steps/job"
        ),
        "padding": "x" * padding,
    }


def _retention_group(
    group_id: str,
    observations: list[dict],
    *,
    latest_state: str | None = None,
) -> dict:
    incidents = sum(ops._reliability_incident(row) for row in observations)
    newest = max(observations, key=lambda row: row["observed_at"])
    return {
        "source_pipeline": "ci",
        "id": group_id,
        "name": f"Group {group_id}",
        "hardware": "gpu",
        "queues": ["gpu_queue"],
        "runs": len(observations),
        "passed": len(observations) - incidents,
        "failed": incidents,
        "soft_failed": 0,
        "incident_count": incidents,
        "incident_rate_pct": incidents / len(observations) * 100,
        "mixed_outcomes": bool(incidents and incidents < len(observations)),
        "latest_state": latest_state or newest["state"],
        "latest_observed_at": newest["observed_at"],
        "observation_count": len(observations),
        "retained_observation_count": len(observations),
        "history_truncated": False,
        "linked_observation_count": len(observations),
        "observations": observations,
    }


def test_bounded_retry_evidence_is_permutation_invariant(monkeypatch):
    monkeypatch.setattr(ops, "OPERATIONS_RETRY_EVIDENCE_MAX_BYTES", 4_000)
    attempts = [
        {
            **_retention_observation(10, "passed"),
            "job_id": f"job-{letter}",
            "name": "retry-" + letter + "x" * 600,
        }
        for letter in "ABCDEFGH"
    ]

    forward = ops._bounded_public_retry_analysis({
        "retry_attempts": attempts,
        "failed_then_passed_recoveries": [],
    })
    reverse = ops._bounded_public_retry_analysis({
        "retry_attempts": list(reversed(attempts)),
        "failed_then_passed_recoveries": [],
    })

    assert forward == reverse


def test_oversized_retry_projection_preserves_comparison_routing(monkeypatch):
    monkeypatch.setattr(ops, "OPERATIONS_RETRY_EVIDENCE_MAX_BYTES", 12_000)
    monkeypatch.setattr(ops, "OPERATIONS_RELIABILITY_ROW_MAX_BYTES", 2_000)
    attempts = [
        {
            **_retention_observation(number, "passed"),
            "padding": "x" * 4_000,
            "comparison_row_ids": [f"comparison-{number}"],
            "comparison_eligible_row_ids": [f"eligible-{number}"],
        }
        for number in range(1, 5)
    ]
    bounded = ops._bounded_public_retry_analysis({
        "retry_attempts": attempts,
        "failed_then_passed_recoveries": [],
    })

    assert ops._json_bytes(bounded) <= 12_000
    retained_ids = {
        comparison_id
        for row in bounded["retry_attempts"]
        for key in ("comparison_row_ids", "comparison_eligible_row_ids")
        for comparison_id in row[key]
    }
    assert retained_ids == {
        *(f"comparison-{number}" for number in range(1, 5)),
        *(f"eligible-{number}" for number in range(1, 5)),
    }
    assert bounded["publication_retention"]["comparison_groups"] == {
        "source": 8,
        "published": 8,
        "omitted": 0,
    }


def test_full_reliability_keeps_retry_rows_while_total_section_fits(monkeypatch):
    monkeypatch.setattr(ops, "OPERATIONS_RETRY_EVIDENCE_MAX_BYTES", 2000)
    attempts = [
        {
            **_retention_observation(number, "passed"),
            "name": "retry-" + "x" * 300,
        }
        for number in range(1, 10)
    ]
    source = {
        "group_catalog": [],
        "flaky_candidates": [],
        "latency_rankings": {},
        "retry_analysis": {
            "summary": {"retry_attempt_count": len(attempts)},
            "retry_attempts": attempts,
            "failed_then_passed_recoveries": [],
        },
        "platform_comparison": {"rows": []},
    }

    public = ops._bounded_public_reliability(source, max_bytes=100_000)

    assert public["retry_analysis"] == source["retry_analysis"]
    assert public["publication_retention"]["complete_relative_to_source"] is True


def test_group_catalog_keeps_current_incidents_before_older_groups():
    groups = [
        _retention_group(
            "stable-new",
            [_retention_observation(8, "passed", padding=50_000)],
        ),
        _retention_group(
            "current-incident",
            [_retention_observation(7, "hard", padding=50_000)],
        ),
        _retention_group(
            "historical-incident",
            [_retention_observation(6, "soft", padding=50_000)],
            latest_state="passed",
        ),
    ]

    catalog, stats = ops._bounded_public_group_catalog(
        groups,
        max_bytes=1024 * 1024 + 110_000,
    )

    assert ops._json_bytes(catalog) <= 1024 * 1024 + 110_000
    assert len(catalog) == 2
    assert "current-incident" in {row["id"] for row in catalog}
    assert stats["groups"] == {"source": 3, "published": 2, "omitted": 1}


def test_group_catalog_is_permutation_invariant():
    groups = [
        _retention_group(
            f"group-{letter}",
            [_retention_observation(4, "passed", padding=20_000)],
        )
        for letter in "ABCD"
    ]

    forward, forward_stats = ops._bounded_public_group_catalog(
        groups,
        max_bytes=1024 * 1024 + 55_000,
    )
    reverse, reverse_stats = ops._bounded_public_group_catalog(
        list(reversed(groups)),
        max_bytes=1024 * 1024 + 55_000,
    )

    assert forward == reverse
    assert forward_stats == reverse_stats


def test_group_observations_are_permutation_invariant():
    observations = [
        _retention_observation(number, "hard" if number == 2 else "passed")
        for number in range(1, 5)
    ]

    forward, forward_stats = ops._bounded_public_group_catalog([
        _retention_group("stable", observations)
    ])
    reverse, reverse_stats = ops._bounded_public_group_catalog([
        _retention_group("stable", list(reversed(observations)))
    ])

    assert forward == reverse
    assert forward_stats == reverse_stats


def test_group_catalog_empty_and_observed_ties_share_comparable_sort_keys():
    observed = _retention_group(
        "observed",
        [{
            **_retention_observation(0, "passed"),
            "observed_at": "2026-04-01T12:00:00Z",
        }],
    )
    empty = {
        "source_pipeline": "ci",
        "id": "empty",
        "name": "Group empty",
        "latest_state": "unknown",
        "latest_observed_at": "2026-04-01T12:00:00Z",
        "incident_count": 0,
        "observations": [],
    }

    catalog, stats = ops._bounded_public_group_catalog([empty, observed])

    assert {row["id"] for row in catalog} == {"empty", "observed"}
    assert stats["groups"]["omitted"] == 0


def test_unpublishable_group_still_counts_its_omitted_observations():
    observations = [
        _retention_observation(1, "hard"),
        _retention_observation(2, "passed"),
    ]

    catalog, stats = ops._bounded_public_group_catalog([
        {"observations": observations}
    ])

    assert catalog == []
    assert stats["groups"] == {"source": 1, "published": 0, "omitted": 1}
    assert stats["observations"] == {"source": 2, "published": 0, "omitted": 2}
    assert stats["incident_observations"] == {
        "source": 1,
        "published": 0,
        "omitted": 1,
    }


def test_group_catalog_retains_newest_then_incident_and_marks_history_incomplete():
    group = _retention_group(
        "priority",
        [
            _retention_observation(9, "passed", padding=20_000),
            _retention_observation(8, "passed", padding=20_000),
            _retention_observation(7, "hard", padding=20_000),
        ],
    )

    catalog, stats = ops._bounded_public_group_catalog(
        [group],
        max_bytes=1024 * 1024 + 50_000,
    )

    retained = catalog[0]
    assert {row["build_number"] for row in retained["observations"]} == {7, 9}
    assert retained["history_truncated"] is True
    assert retained["publication_history_complete"] is False
    assert retained["source_retained_observation_count"] == 3
    assert retained["retained_observation_count"] == 2
    assert stats["incident_observations"]["published"] == 1


def test_group_catalog_never_launders_source_truncation_as_complete():
    group = _retention_group(
        "source-truncated",
        [
            _retention_observation(8, "passed"),
            _retention_observation(9, "hard"),
        ],
    )
    # The source retained only two exact rows for five aggregate observations;
    # one additional observation was excluded before publication.
    group.update({
        "observation_count": 5,
        "history_truncated": True,
        "excluded_observation_count": 1,
    })

    catalog, stats = ops._bounded_public_group_catalog([group])

    assert stats["groups"]["omitted"] == 0
    retained = catalog[0]
    assert retained["runs"] == 2
    assert retained["observation_count"] == 5
    assert retained["source_retained_observation_count"] == 2
    assert retained["retained_observation_count"] == 2
    assert retained["excluded_observation_count"] == 1
    assert retained["history_truncated"] is True
    assert retained["publication_history_complete"] is False


def test_pathological_observation_is_projected_instead_of_wedging(monkeypatch):
    monkeypatch.setattr(ops, "OPERATIONS_RELIABILITY_ROW_MAX_BYTES", 1024)
    observation = _retention_observation(9, "hard")
    observation["message"] = "pathological" * 100_000
    group = _retention_group("pathological", [observation])

    catalog, stats = ops._bounded_public_group_catalog(
        [group],
        max_bytes=2 * 1024 * 1024,
    )

    assert len(catalog) == 1
    retained = catalog[0]["observations"][0]
    assert retained["state"] == "hard"
    assert retained["build_number"] == 9
    assert retained["publication_fields_truncated"] is True
    assert stats["sanitized_observation_count"] == 1
    assert ops._json_bytes(catalog) <= 2 * 1024 * 1024


def test_public_reliability_exact_bound_and_honest_omission_counts():
    groups = [
        _retention_group(
            str(index),
            [
                _retention_observation(number, state, padding=20_000)
                for number, state in ((9, "passed"), (8, "passed"), (7, "hard"))
            ],
        )
        for index in range(20)
    ]
    source = {
        "available": True,
        "cohort": {"id": "main", "build_numbers": list(range(100))},
        "denominator": {"groups": 20, "observations": 60},
        "summary": {"group_count": 20},
        "group_catalog": groups,
        "flaky_candidates": [],
        "latency_rankings": {
            "by_median_duration": [],
            "by_p90_duration": [],
            "by_max_duration": [],
        },
        "retry_analysis": {
            "retry_attempts": [],
            "failed_then_passed_recoveries": [],
        },
        "platform_comparison": {"available": False, "rows": []},
    }
    max_bytes = 400_000

    first = ops._bounded_public_reliability(source, max_bytes=max_bytes)
    second = ops._bounded_public_reliability(source, max_bytes=max_bytes)

    assert first == second
    assert ops._json_bytes({"reliability": first}) <= max_bytes
    retention = first["publication_retention"]
    assert retention["compacted"] is True
    assert retention["complete_relative_to_source"] is False
    assert retention["groups"]["source"] == 20
    assert retention["groups"]["published"] < 20
    assert retention["groups"]["omitted"] == (
        20 - retention["groups"]["published"]
    )


def test_reliability_bound_failure_preserves_existing_generation(
    tmp_path,
    monkeypatch,
):
    payload = ops.build_snapshot(_fixture_data(tmp_path), generated_at=GENERATED_AT)
    output = tmp_path / "published" / "operations_v2.json"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"previous-generation")
    monkeypatch.setattr(ops._bounded_public_reliability, "__kwdefaults__", {"max_bytes": 1})

    with pytest.raises(RuntimeError, match="bounded public reliability section"):
        ops.write_snapshot_bundle(output, payload, log=False)

    assert output.read_bytes() == b"previous-generation"


def _oversized_agent_health() -> dict:
    node_days = []
    failing_runs = []
    for index in range(120):
        day = f"2026-08-{1 + index // 6:02d}"
        node_days.append({
            "d": day,
            "nd": f"node-{index:04d}",
            "h": "MI300",
            "a": [10, 1, 1, 0],
            "n": [2, 0, 1, 0],
            "padding": "n" * 400,
        })
    for index in range(240):
        day = f"2026-08-{1 + index // 12:02d}"
        failing_runs.append({
            "d": day,
            "nd": f"node-{index % 120:04d}",
            "h": "MI300",
            "s": "hard" if index % 2 else "soft",
            "i": index % 3 == 0,
            "ng": index % 4 == 0,
            "bc": False,
            "g": "group-" + "g" * 500,
            "j": str(index),
            "t": day + "T12:00:00Z",
        })
    accounting = ops._agent_health_failure_accounting(failing_runs)
    return {
        "generated_at": GENERATED_AT,
        "infra_failure_count": len(failing_runs),
        "node_days": node_days,
        "failing_runs": failing_runs,
        "failure_accounting": accounting,
        "retention": {
            "failure_evidence": {
                "source": len(failing_runs),
                "published": len(failing_runs),
                "omitted": 0,
                "complete_relative_to_source": True,
            },
            "failure_accounting": {"complete_relative_to_source": True},
        },
    }


def _oversized_amd_test_health() -> dict:
    groups = [
        {
            "id": f"group-{index:04d}",
            "name": f"Group {index}",
            "latest_build_number": 999 if index < 20 else 998,
            "latest_state": "hard" if index == 0 else "passed",
            "latest_observed_at": f"2026-08-31T12:{index % 60:02d}:00Z",
            "runs": 30,
            "passed": 29,
            "hard_failed": 1,
            "padding": "g" * 900,
        }
        for index in range(200)
    ]
    builds = [
        {"number": index, "observed_at": f"2026-08-{index % 28 + 1:02d}T12:00:00Z", "padding": "b" * 600}
        for index in range(100)
    ]
    logical = [
        {
            "id": f"logical-{index}",
            "label": f"Logical {index}",
            "state": "non_passing" if index == 0 else "passing_all",
            "padding": "l" * 700,
        }
        for index in range(100)
    ]
    return {
        "available": True,
        "summary": {"latest_build_number": 999, "latest_group_count": 200},
        "group_catalog": groups,
        "builds": builds,
        "latest_logical_test_groups": {"available": True, "rows": logical},
    }


def test_operations_amd_test_health_is_bounded_with_honest_catalog_accounting():
    source = _oversized_amd_test_health()

    first = ops._bounded_operations_amd_test_health(source, max_bytes=40_000)
    second = ops._bounded_operations_amd_test_health(source, max_bytes=40_000)

    assert first == second
    assert ops._json_bytes({"amd_test_health": first}) <= 40_000
    assert first["summary"] == source["summary"]
    retention = first["operations_publication_retention"]
    assert retention["complete_relative_to_amd_test_health"] is False
    assert retention["aggregate_scalars_complete"] is True
    for key in ("group_catalog", "builds", "latest_logical_test_groups"):
        assert retention[key]["source"] == (
            retention[key]["published"] + retention[key]["omitted"]
        )
        assert retention[key]["omitted"] > 0
    assert any(row["id"] == "group-0000" for row in first["group_catalog"])
    assert any(row["id"] == "logical-0" for row in first["latest_logical_test_groups"]["rows"])


def test_operations_agent_health_compacts_detail_but_preserves_exact_cubes():
    source = _oversized_agent_health()

    first = ops._bounded_operations_agent_health(source, max_bytes=20_000)
    second = ops._bounded_operations_agent_health(source, max_bytes=20_000)

    assert first == second
    assert ops._json_bytes({"amd_agent_health": first}) <= 20_000
    retention = first["operations_publication_retention"]
    assert retention["complete_relative_to_agent_health"] is False
    assert retention["aggregate_accounting_complete"] is True
    assert retention["node_days"]["omitted"] > 0
    assert retention["failure_evidence"]["omitted_from_ledger"] > 0
    assert sum(row["a"][0] for row in first["node_accounting_totals"]) == sum(
        row["a"][0] for row in source["node_days"]
    )
    assert sum(row["c"] for row in first["failure_accounting_totals"]) == len(
        source["failing_runs"]
    )
    assert first["retention"]["failure_evidence"] == {
        "source": 240,
        "published": 0,
        "omitted": 240,
        "complete_relative_to_source": False,
        "selection": "source_priority_then_newest_operations_suffix",
    }


def test_operation_sections_apply_agent_health_route_budget(monkeypatch):
    monkeypatch.setattr(ops, "OPERATIONS_AGENT_HEALTH_SECTION_MAX_BYTES", 20_000)

    section = ops._operation_sections({
        "amd_agent_health": _oversized_agent_health(),
        "reliability": {},
    })["amd_agent_health"]

    assert ops._json_bytes(section) <= 20_000
    assert (
        section["amd_agent_health"]["operations_publication_retention"]
        ["complete_relative_to_agent_health"]
        is False
    )


def test_operations_collection_sections_compact_every_legal_growing_catalog() -> None:
    bulky = "x" * 12_000
    definition_rows = [{"label": f"definition-{index}", "detail": bulky} for index in range(300)]
    group_rows = [
        {"id": index, "state": "action" if index % 3 == 0 else "existing", "detail": bulky}
        for index in range(180)
    ]
    gating_rows = [
        {"id": index, "target_signal": "red" if index % 4 == 0 else "green", "detail": bulky}
        for index in range(300)
    ]
    ownership_rows = [
        {
            "area": f"area-{index}",
            "counts": {"incidents": index % 5 == 0, "pending_soft": 0},
            "regressions": [{"label": bulky}],
            "targets": [{"label": bulky}],
        }
        for index in range(100)
    ]
    sections = ops._operation_sections(
        {
            "definition_parity": {
                "summary": {"total": len(definition_rows)},
                "nvidia_only": definition_rows,
            },
            "test_group_parity": {
                "summary": {"upstream_logical_groups": len(group_rows)},
                "areas": [],
                "groups": group_rows,
            },
            "gating": {
                "target_summary": {"target_group_count": len(gating_rows)},
                "target_groups": gating_rows,
                "active_target_groups": gating_rows,
            },
            "ownership": {
                "schema_version": 1,
                "available": True,
                "summary": {"areas": len(ownership_rows)},
                "areas": ownership_rows,
                "unmapped_targets": [],
            },
            "reliability": {},
        }
    )

    for name in ("definition_parity", "test_group_parity", "ownership"):
        assert ops._json_bytes(sections[name]) <= (
            ops.OPERATIONS_CANARY_SECTION_MAX_BYTES[name]
        )
        retention = sections[name][name]["operations_publication_retention"]
        assert retention["complete_relative_to_source"] is False
        assert retention["aggregate_summaries_complete"] is True


def test_snapshot_amd_scope_rejects_mixed_aggregates_and_recounts_raw_queues(tmp_path):
    data_dir = _fixture_data(tmp_path)
    analytics_path = data_dir / "analytics.json"
    source = json.loads(analytics_path.read_text())
    strict = source["ci"]["all_main_reliability"]
    assert ops._reliability(source["ci"], "ci")["available"] is True
    for hardware_scope in (None, "all_ci_gpu", "cuda_gpu"):
        mixed = json.loads(json.dumps(source["ci"]))
        mixed["all_main_reliability"]["hardware_scope"] = hardware_scope
        assert ops._reliability(mixed, "ci")["available"] is False
    foreign = json.loads(json.dumps(strict))
    foreign["groups"][0]["queue"] = "gpu_1_queue"
    assert ops._collector_main_is_strict(foreign, "ci") is False
    foreign["groups"][0]["queue"] = "amd_mi300_1"
    foreign["groups"][0]["hardware"] = "b200"
    assert ops._collector_main_is_strict(foreign, "ci") is False
    cpu = json.loads(json.dumps(strict))
    cpu["groups"][0]["no_gpu"] = True
    assert ops._collector_main_is_strict(cpu, "ci") is False
    queues = ops._filter_queue_snapshot({
        "queues": {name: {"waiting": count, "running": count * 2, "count_source": "cluster_metrics"}
                   for name, count in (("amd_mi300_1", 2), ("amd-cpu", 100), ("B200", 100), ("intel-gpu", 100))},
        "scope_totals": {"all": {"waiting": 302, "running": 604}},
    })
    assert set(queues["queues"]) == {"amd_mi300_1"}
    assert queues["total_waiting"] == 2
    assert queues["total_running"] == 4
    assert queues["scope_totals"]["all"]["waiting"] == 2
    jobs = ops._filter_queue_jobs({"pending": [
        {"pipeline": "vllm-omni-amd-ci", "queue": "amd_mi300_1", "name": "CPU Offload with CUDA model preset"},
        {"queue": "amd_mi300_1", "name": ":computer: (CPU) Torch ABI"},
        {"queue": "amd_mi300_1", "no_gpu": True},
        {"queue": "H200"},
    ]})
    assert len(jobs["pending"]) == 1
    assert jobs["pending"][0]["name"] == "CPU Offload with CUDA model preset"


def test_amd_result_routing_uses_job_label_or_exact_mi_roster_and_excludes_cpu(tmp_path):
    rows = [
        {"name": "test_gpu", "job_name": "amd_mi300_1: CPU Offload", "job_id": "gpu", "pipeline": "ci", "build_number": 7, "status": "passed"},
        {"name": "test_native", "job_name": ":amd: (MI355 DPX) Native test", "job_id": "native", "pipeline": "ci", "build_number": 7, "status": "passed"},
        {"name": "test_unverified", "job_name": ":amd: (MI355) Unverified", "pipeline": "ci", "build_number": 7, "status": "passed"},
        {"name": "test_cpu", "job_name": "amd_mi300_1: :computer: (CPU) Torch ABI", "pipeline": "ci", "build_number": 7, "status": "passed"},
        {"name": "test_no_gpu", "job_name": "amd_mi300_1: Explicit CPU route", "no_gpu": True, "pipeline": "ci", "build_number": 7, "status": "passed"},
        {"name": "test_cuda", "job_name": "gpu_1: :nvidia: (H100) CUDA test", "pipeline": "ci", "build_number": 7, "status": "passed"},
    ]
    _write_jsonl(tmp_path / "test_results" / "2026-04-22_amd.jsonl", rows)
    roster = {7: {"jobs": [{"job_id": "native", "q": "amd_mi355_dpx", "raw_name": rows[1]["job_name"]}]}}
    grouped, stats = ops._load_amd_test_result_groups(tmp_path, roster)
    assert {name for _, name in grouped} == {rows[0]["job_name"], rows[1]["job_name"]}
    assert stats["ignored_rows"] == 4


def test_queue_projection_requires_exact_current_ci_source_proof_and_keeps_physical_counts():
    commit = "9" * 40
    proof = {"version": 1, "source_commit": commit, "definition_tree": "a" * 40,
             "classification": "amd_mi_gpu"}
    gpu = {"pipeline": "ci", "workload": "vllm", "queue": "amd_mi300_1", "commit": commit[:12],
           "name": "CPU Offload", "execution_proof": proof}
    legacy_cpu = {"pipeline": "ci", "queue": "amd_mi300_1", "commit": commit[:12],
                  "name": ":amd: (MI250) Torch Stable ABI Audit"}
    omni = {"pipeline": "vllm-omni-amd-ci", "workload": "omni", "queue": "amd_mi300_1", "name": "Omni GPU"}
    jobs = {"hardware_scope": "amd_mi_gpu", "execution_scope_contract": ops.EXECUTION_SCOPE_CONTRACT,
            "pending": [gpu, legacy_cpu, omni], "running": []}
    snapshot = {"queues": {"amd_mi300_1": {"waiting": 3, "running": 0,
                                           "count_source": "cluster_metrics"}}}

    result = ops._queue(snapshot, jobs, [snapshot])
    assert result["queue_jobs"]["pending"] == [gpu, omni]
    assert result["snapshot"]["total_waiting"] == 3
    assert result["history"][0]["queues"]["amd_mi300_1"]["waiting"] == 3

    for marker in (None, "physical_mi_only"):
        legacy = {**jobs, "execution_scope_contract": marker}
        assert ops._filter_queue_jobs(legacy)["pending"] == [omni]


@pytest.mark.parametrize("invalid", [
    {"version": True}, {"source_commit": "b" * 40}, {"source_commit": "9" * 12},
    {"definition_tree": "A" * 40}, {"classification": "excluded_cpu"}, {"extra": True},
])
def test_queue_projection_rejects_invalid_or_contradictory_ci_execution_proofs(invalid):
    proof = {"version": 1, "source_commit": "9" * 40, "definition_tree": "a" * 40,
             "classification": "amd_mi_gpu", **invalid}
    row = {"pipeline": "ci", "queue": "amd_mi300_1", "commit": "9" * 12,
           "execution_proof": proof}
    result = ops._filter_queue_jobs({"hardware_scope": "amd_mi_gpu",
        "execution_scope_contract": ops.EXECUTION_SCOPE_CONTRACT, "pending": [row], "running": []})
    assert result["pending"] == []


def test_omni_mapping_requires_new_ci_aggregate_scope_without_reclassifying_omni_jobs():
    snapshot = {"queues": {"amd_mi300_1": {"waiting": 2, "running": 0}}}
    jobs = {"pending": [{"pipeline": "vllm-omni-amd-ci", "queue": "amd_mi300_1",
                         "name": "Omni GPU"}], "running": []}
    mapping = {"hardware_scope": "amd_mi_gpu", "generated_at": GENERATED_AT,
        "scope": {"queues": ["amd_mi300_1"],
                  "workload_pipelines": {"main": ["ci"], "omni": ["vllm-omni-amd-ci"]}},
        "totals": {"main": {"mapped_jobs": 10}, "omni": {"mapped_jobs": 2}}}
    capacity = {"queues": [{"id": "amd_mi300_1", "monitored": True}]}
    for marker in (None, "physical_mi_only"):
        old = {**mapping, "execution_scope_contract": marker}
        result = ops._omni(snapshot, jobs, [], {}, {}, old, capacity)
        assert result["mapping_history"] == {}
        assert result["provenance"]["sources"]["mapping_history"]["timestamp"] is None
        assert result["current"]["waiting"] == 1
    current = {**mapping, "execution_scope_contract": ops.EXECUTION_SCOPE_CONTRACT}
    result = ops._omni(snapshot, jobs, [], {}, {}, current, capacity)
    assert result["mapping_history"] == current
    assert result["current"]["waiting"] == 1
    wrong_pipeline = {**current, "scope": {**current["scope"],
        "workload_pipelines": {"main": ["amd-ci"], "omni": ["vllm-omni-amd-ci"]}}}
    assert ops._omni(snapshot, jobs, [], {}, {}, wrong_pipeline, capacity)["mapping_history"] == {}


def test_current_queue_projection_removes_legacy_pipelines_and_attributes_exact_job_identity():
    proof = {"version": 1, "source_commit": "9" * 40, "definition_tree": "a" * 40,
             "classification": "amd_mi_gpu"}
    ci = {"pipeline": "ci", "queue": "amd_mi300_1", "commit": "9" * 12,
          "branch": "fix/omni-regression", "name": "Omni model GPU test", "workload": "omni",
          "execution_proof": proof}
    omni = {"pipeline": "vllm-omni-amd-ci", "queue": "amd_mi300_1", "name": "Main branch model test",
            "branch": "main", "workload": "vllm"}
    legacy = {"pipeline": "amd-ci", "queue": "amd_mi300_1", "name": "Legacy AMD", "workload": "vllm"}
    source = {"hardware_scope": "amd_mi_gpu", "execution_scope_contract": ops.EXECUTION_SCOPE_CONTRACT,
              "pending": [], "running": [*[ci.copy() for _ in range(69)],
                  *[omni.copy() for _ in range(89)], legacy, legacy.copy()]}
    snapshot = {"queues": {"amd_mi300_1": {"waiting": 0, "running": 160,
                                           "count_source": "cluster_metrics"}}}
    result = ops._queue(snapshot, source, [snapshot])
    jobs = result["queue_jobs"]["running"]
    assert len(jobs) == 158
    assert sum(row["workload"] == "vllm" for row in jobs) == 69
    assert sum(row["workload"] == "omni" for row in jobs) == 89
    assert {row["pipeline"] for row in jobs} == {"ci", "vllm-omni-amd-ci"}
    assert result["snapshot"]["total_running"] == 160
    assert result["history"][0]["queues"]["amd_mi300_1"]["running"] == 160
    assert source["running"][0]["workload"] == "omni"


@pytest.mark.parametrize("pipeline", ["amd-ci", "vllm", "perf-eval", "foreign", "", None, ["ci"], {"slug": "ci"}])
def test_current_queue_projection_rejects_unapproved_or_missing_pipeline(pipeline):
    row = {"pipeline": pipeline, "queue": "amd_mi300_1", "name": "AMD GPU test"}
    result = ops._filter_queue_jobs({"hardware_scope": "amd_mi_gpu",
        "execution_scope_contract": ops.EXECUTION_SCOPE_CONTRACT, "pending": [row], "running": []})
    assert result["pending"] == []
