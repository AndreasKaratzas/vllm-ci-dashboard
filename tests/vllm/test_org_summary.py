"""Contract tests for the organization-wide OSS CI summary."""

from __future__ import annotations

import json

import pytest

from vllm import build_operations_snapshot as ops
from vllm.audit_dashboard_data import DashboardAudit


GENERATED_AT = "2026-08-20T22:00:00Z"


def _payload() -> dict:
    return {
        "schema_version": 2,
        "generated_at": GENERATED_AT,
        "sources": {
            "ci_health": {"path": "ci_health.json", "timestamp": GENERATED_AT},
            "amd_test_matrix": {
                "path": "amd_test_matrix.json",
                "timestamp": GENERATED_AT,
            },
            "gating_targets": {
                "path": "gating_targets.json",
                "timestamp": GENERATED_AT,
            },
            "capacity_monitor": {
                "path": "capacity_monitor.json",
                "timestamp": GENERATED_AT,
            },
            "queue_timeseries": {
                "path": "queue_timeseries.jsonl",
                "timestamp": GENERATED_AT,
            },
        },
        "amd_test_health": {
            "summary": {
                "latest_build_number": 12275,
                "latest_build_url": (
                    "https://buildkite.com/vllm/amd-ci/builds/12275"
                ),
                "latest_job_variant_count": 236,
                "latest_job_variant_state_counts": {
                    "passed": 232,
                    "soft": 4,
                    "hard": 0,
                    "unknown": 0,
                },
                "latest_test_group_counts": {
                    "available": True,
                    "build_number": 12275,
                    "job_variant_build_number": 12275,
                    "test_signal_build_number": 12275,
                    "total": 150,
                    "passing": 148,
                    "non_passing": 2,
                    "passing_all": 147,
                    "partial": 1,
                    "pass_rate_pct": 98.7,
                    "reason": None,
                },
            },
        },
        "gating": {
            "matrix_summary": {
                "latest_build_number": 12275,
                "definition_rows": 166,
                "reduced_unique_groups": 161,
                "duplicate_clusters": 5,
                "health_policies": {
                    "best_hardware": {
                        "included_groups": 166,
                        "passing_groups": 163,
                        "failing_groups": 3,
                        "waiting_groups": 0,
                        "unknown_groups": 0,
                        "pass_percentage": 98.2,
                        "status_rule": "pass when any owned hardware cell passes",
                        "denominator_rule": "all expected health groups",
                    },
                },
            },
            "target_summary": {
                "target_group_count": 125,
                "by_gating_signal": {"green": 45, "red": 80},
                "by_target_signal": {"green": 108, "red": 14, "gray": 3},
                "by_pf_signal": {"green": 80, "red": 31, "yellow": 13, "purple": 1},
            },
            "upstream_scheduled": {
                "latest": {"kind": "daily", "number": 90000},
                "latest_by_kind": {
                    "nightly": {
                        "kind": "nightly",
                        "number": 84753,
                        "url": "https://buildkite.com/vllm/ci/builds/84753",
                        "build_state": "failed",
                        "commit": "abcdef",
                        "finished_at": "2026-08-20T21:30:00Z",
                        "summary": {
                            "total": 73,
                            "gated": 73,
                            "passing": 73,
                            "failing": 0,
                            "soft_failing": 0,
                            "pending": 0,
                            "missing": 0,
                            "queue_count": 7,
                            "configured_queue_count": 7,
                        },
                        "queue_wait_mins": {"p50": 0.4, "p95": 0.5, "max": 1.9},
                    },
                    "daily": {"kind": "daily", "number": 90000},
                },
            },
        },
        "queue": {
            "snapshot": {
                "ts": "2026-08-20T21:35:45Z",
                "total_waiting": 163,
                "total_running": 794,
                "scope_totals": {
                    "target": {
                        "queue_count": 2,
                        "waiting": 25,
                        "running": 422,
                        "count_source": "cluster_metrics",
                    },
                },
                "target_queue_scope": {
                    "id": "amd_mi250_mi300_mi355",
                    "queue_count": 2,
                    "families": ["MI250", "MI300", "MI355"],
                    "gpu_widths": [1, 2, 4, 8],
                    "queue_ids": ["amd_mi300_1", "amd_mi355_1"],
                    "all_rows_present": True,
                },
                "queues": {
                    "amd_mi300_1": {
                        "waiting": 20,
                        "running": 400,
                        "zombie_waiting": 2,
                        "zombie_running": 1,
                        "p50_wait": 1.2,
                        "p95_wait": 5.6,
                        "max_wait": 7.0,
                        "count_source": "cluster_metrics",
                        "wait_source": "cluster_metrics",
                    },
                    "amd_mi355_1": {
                        "waiting": 5,
                        "running": 22,
                        "zombie_waiting": 0,
                        "zombie_running": 0,
                        "p50_wait": 0.4,
                        "p95_wait": 1.0,
                        "max_wait": 1.5,
                        "count_source": "cluster_metrics",
                        "wait_source": "cluster_metrics",
                    },
                },
            },
        },
    }


def _lifecycle() -> dict:
    return {
        "generated_at": GENERATED_AT,
        "window": {
            "start": "2026-08-20T20:00:00Z",
            "end_exclusive": "2026-08-20T22:00:00Z",
            "hours": 2,
        },
        "totals": {
            "incoming": 971,
            "served": 970,
            "completed": 800,
            "passed": 691,
            "pass_rate_pct": 86.7,
            "queue_wait_seconds": {
                "count": 970,
                "p50": 21.737,
                "p95": 160.863,
                "avg": 51.884,
                "max": 889.759,
            },
        },
        "coverage": {
            "status": "partial_observation",
            "complete": False,
            "reason": "Direct event timestamps are exact but not provably exhaustive.",
        },
    }


def test_org_summary_projects_distinct_counts_and_latest_nightly() -> None:
    summary = ops.build_org_summary(_payload(), _lifecycle())

    assert summary["schema_id"] == "oss-project-ci-summary"
    assert summary["schema_version"] == 1
    assert summary["project"]["id"] == "vllm"

    logical = summary["test_groups"]["observed_latest_amd"]
    assert (logical["total"], logical["green"], logical["non_green"]) == (
        150,
        148,
        2,
    )
    variants = summary["test_groups"]["exact_job_variants_latest_amd"]
    assert (variants["total"], variants["green"], variants["non_green"]) == (
        236,
        232,
        4,
    )
    configured = summary["test_groups"]["configured_amd_definitions"]
    assert configured["total"] == 166

    best = summary["gating"]["best_hardware_runtime"]
    assert (best["total"], best["green"], best["non_green"]) == (166, 163, 3)
    nightly = summary["gating"]["upstream_scheduled_nightly"]
    assert nightly["build_number"] == 84753
    assert (nightly["configured"], nightly["gated"], nightly["green"]) == (
        73,
        73,
        73,
    )
    assert summary["gating"]["reviewed_targets"]["total"] == 125


def test_org_summary_uses_target_queue_scope_and_lifecycle_wait() -> None:
    summary = ops.build_org_summary(_payload(), _lifecycle())

    current = summary["queues"]["current"]
    assert current["waiting_jobs"] == 25
    assert current["running_jobs"] == 422
    assert current["queues_with_waiting_jobs"] == 2
    assert current["zombie_waiting_jobs"] == 2
    assert current["maximum_across_queues_wait_minutes"] == {
        "p50": 1.2,
        "p95": 5.6,
        "max": 7.0,
    }
    assert len(summary["queues"]["by_queue"]) == 2

    recent = summary["queues"]["recent_completed_window"]
    assert recent["coverage"]["status"] == "partial_observation"
    assert recent["served_job_wait_minutes"] == {
        "sample_count": 970,
        "p50": 0.362,
        "p95": 2.681,
        "average": 0.865,
        "max": 14.829,
    }


def test_org_summary_fails_closed_when_amd_builds_do_not_align() -> None:
    payload = _payload()
    logical = payload["amd_test_health"]["summary"]["latest_test_group_counts"]
    logical["test_signal_build_number"] = 12274

    summary = ops.build_org_summary(payload, _lifecycle())

    observed = summary["test_groups"]["observed_latest_amd"]
    assert observed["available"] is False
    assert observed["reason"] == "build_mismatch"
    assert observed["total"] is None
    configured = summary["test_groups"]["configured_amd_definitions"]
    assert configured["available"] is False
    assert configured["total"] is None
    best = summary["gating"]["best_hardware_runtime"]
    assert best["available"] is False
    assert best["green"] is None


def test_org_summary_fails_closed_when_target_queue_scope_is_incomplete() -> None:
    payload = _payload()
    payload["queue"]["snapshot"]["target_queue_scope"]["all_rows_present"] = False

    current = ops.build_org_summary(payload, _lifecycle())["queues"]["current"]

    assert current["available"] is False
    assert current["waiting_jobs"] is None
    assert current["running_jobs"] is None


def test_snapshot_bundle_writes_small_discoverable_org_summary(tmp_path) -> None:
    output = tmp_path / "operations_v2.json"
    (tmp_path / ops.QUEUE_LIFECYCLE_NAME).write_text(json.dumps(_lifecycle()))

    manifest = ops.write_snapshot_bundle(output, _payload(), log=False)

    descriptor = manifest["organization_summary"]
    assert descriptor["path"] == ops.ORG_SUMMARY_NAME
    path = tmp_path / descriptor["path"]
    summary = json.loads(path.read_text())
    assert summary["generated_at"] == GENERATED_AT
    assert path.stat().st_size == descriptor["bytes"]
    assert path.stat().st_size < 64 * 1024
    assert "groups" not in summary["gating"]["upstream_scheduled_nightly"]


def test_dashboard_audit_rejects_a_drifted_org_summary(tmp_path) -> None:
    data_dir = tmp_path / "data" / "vllm" / "ci"
    data_dir.mkdir(parents=True)
    output = data_dir / "operations_v2.json"
    (data_dir / ops.QUEUE_LIFECYCLE_NAME).write_text(json.dumps(_lifecycle()))
    ops.write_snapshot_bundle(output, _payload(), log=False)

    valid = DashboardAudit(tmp_path)
    valid.audit_operations_bundle()
    assert not {
        finding.code
        for finding in valid.report.findings
        if "org-summary" in finding.code
    }

    path = data_dir / ops.ORG_SUMMARY_NAME
    summary = json.loads(path.read_text())
    summary["test_groups"]["observed_latest_amd"]["total"] = 236
    path.write_text(json.dumps(summary, indent=2) + "\n")

    invalid = DashboardAudit(tmp_path)
    invalid.audit_operations_bundle()
    assert "operations-bundle-org-summary-projection" in {
        finding.code for finding in invalid.report.findings
    }


@pytest.mark.live_data
def test_published_org_summary_has_consistent_denominators() -> None:
    path = ops.ROOT / "data" / "vllm" / "ci" / ops.ORG_SUMMARY_NAME
    summary = json.loads(path.read_text())

    logical = summary["test_groups"]["observed_latest_amd"]
    variants = summary["test_groups"]["exact_job_variants_latest_amd"]
    assert logical["available"] is True
    assert logical["green_on_all_observed_hardware"] <= logical["green"] <= logical["total"]
    assert logical["green_on_all_observed_hardware"] + logical["mixed_by_hardware"] == logical["green"]
    assert logical["non_green"] == logical["total"] - logical["green"]
    assert variants["build_number"] == logical["build_number"]
    assert variants["total"] >= logical["total"]

    best = summary["gating"]["best_hardware_runtime"]
    assert best["available"] is True
    assert best["build_number"] == logical["build_number"]
    assert best["non_green"] == best["total"] - best["green"]

    scheduled = summary["gating"]["upstream_scheduled_nightly"]
    assert scheduled["configured"] == scheduled["gated"] + scheduled["missing"]
    assert scheduled["gated"] == sum(
        scheduled[key] for key in ("green", "failing", "soft_failing", "pending")
    )

    current = summary["queues"]["current"]
    queue_rows = summary["queues"]["by_queue"]
    assert current["available"] is True
    assert current["waiting_jobs"] == sum(row["waiting_jobs"] for row in queue_rows)
    assert current["running_jobs"] == sum(row["running_jobs"] for row in queue_rows)

    wait = summary["queues"]["recent_completed_window"][
        "served_job_wait_minutes"
    ]
    assert 0 <= wait["p50"] <= wait["p95"] <= wait["max"]
    assert path.stat().st_size < 64 * 1024
