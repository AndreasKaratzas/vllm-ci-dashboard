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
        "schema_version": 1,
        "generated_at": GENERATED_AT,
        "scope": {
            "queues": ["amd_mi300_1", "amd_mi355_1"],
            "families": ["MI300", "MI355"],
        },
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
        "daily_wait_times": {
            "unit": "seconds",
            "day_timezone": "UTC",
            "attributed_by": "timestamps.started_at",
            "days": [
                {
                    "date": "2026-08-19",
                    "start": "2026-08-19T22:00:00Z",
                    "end_exclusive": "2026-08-20T00:00:00Z",
                    "partial": True,
                    "sample_count": 1,
                    "served_job_wait_seconds": [21.737],
                },
                {
                    "date": "2026-08-20",
                    "start": "2026-08-20T00:00:00Z",
                    "end_exclusive": "2026-08-20T22:00:00Z",
                    "partial": True,
                    "sample_count": 3,
                    "served_job_wait_seconds": [51.884, 160.863, 889.759],
                },
            ],
        },
        "hourly": [
            {
                "start": "2026-08-19T22:00:00Z",
                "end_exclusive": "2026-08-20T22:00:00Z",
                "partial": True,
                "totals": {"queue_wait_seconds": {"count": 4}},
            },
        ],
        "coverage": {
            "status": "partial_observation",
            "complete": False,
            "reason": "Direct event timestamps are exact but not provably exhaustive.",
            "metric_exhaustiveness": {
                "served": {
                    "complete": False,
                    "exact_for_observed_events": True,
                },
            },
        },
        "retention": {
            "days": 1,
            "event_start": "2026-08-19T22:00:00Z",
            "end_exclusive": "2026-08-20T22:00:00Z",
        },
    }


def test_org_summary_projects_distinct_counts_and_latest_nightly() -> None:
    summary = ops.build_org_summary(_payload(), _lifecycle())

    assert summary["schema_id"] == "oss-project-ci-summary"
    assert summary["schema_version"] == ops.ORG_SUMMARY_SCHEMA_VERSION == 6
    assert summary["project"]["id"] == "vllm"

    logical = summary["test_groups"]["observed_latest_amd"]
    assert (logical["total"], logical["green"], logical["non_green"]) == (
        150,
        148,
        2,
    )
    assert "configured %N shard jobs" in logical["count_basis"]
    assert "topology-distinct routes remain separate" in summary["definitions"][
        "test_group"
    ]
    assert "ledger-reconciled distribution" in summary["definitions"][
        "served_job_wait_sample"
    ]
    variants = summary["test_groups"]["exact_job_variants_latest_amd"]
    assert (variants["total"], variants["green"], variants["non_green"]) == (
        236,
        232,
        4,
    )
    assert "configured_amd_definitions" not in summary["test_groups"]

    best = summary["health_checks"]["best_hardware"]
    assert (best["total"], best["green"], best["non_green"]) == (166, 163, 3)
    nightly = summary["scheduled_cohorts"]["upstream_nightly"]
    assert nightly["build_number"] == 84753
    assert (nightly["configured"], nightly["observed"], nightly["green"]) == (
        73,
        73,
        73,
    )
    assert summary["parity_targets"]["reviewed"]["total"] == 125
    assert "gating" not in summary
    assert {
        "health_check",
        "scheduled_mirror_group",
        "parity_target",
    } <= set(summary["definitions"])
    assert {
        "runtime_gate",
        "scheduled_gating_group",
        "gating_target",
    }.isdisjoint(summary["definitions"])


def test_org_summary_exposes_missing_nightly_as_available_false(tmp_path) -> None:
    payload = _payload()
    payload["gating"]["upstream_scheduled"]["latest_by_kind"]["nightly"] = None

    scheduled = ops.build_org_summary(payload, _lifecycle())[
        "scheduled_cohorts"
    ]["upstream_nightly"]

    assert scheduled["available"] is False
    assert all(
        scheduled[key] is None
        for key in (
            "build_number",
            "configured",
            "observed",
            "green",
            "non_green",
            "failing",
            "soft_failing",
            "pending",
            "missing",
            "queues_configured",
            "queues_with_observed_work",
        )
    )

    data_dir = tmp_path / "data" / "vllm" / "ci"
    data_dir.mkdir(parents=True)
    (data_dir / ops.QUEUE_LIFECYCLE_NAME).write_text(json.dumps(_lifecycle()))
    ops.write_snapshot_bundle(data_dir / "operations_v2.json", payload, log=False)
    checked = DashboardAudit(tmp_path)
    checked.audit_operations_bundle()
    assert "operations-bundle-org-summary-scheduled-denominators" not in {
        finding.code for finding in checked.report.findings
    }


def test_org_summary_projects_daily_wait_vectors_and_rolling_counts() -> None:
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
    assert (recent["incoming_jobs"], recent["served_jobs"]) == (971, 970)
    assert "served_job_wait_minutes" not in recent

    daily = summary["queues"]["daily_served_job_waits"]
    assert daily == {
        "available": True,
        "reason": None,
        "source_generated_at": GENERATED_AT,
        "timezone": "UTC",
        "unit": "seconds",
        "sample_order": "ascending",
        "sample_definition": (
            "started_at - runnable_at; no sample is emitted unless both direct "
            "Buildkite timestamps exist"
        ),
        "population": (
            "one observed Buildkite job attempt in queues.scope, assigned to the "
            "UTC date containing started_at; retries remain separate"
        ),
        "retention": {
            "kind": "rolling",
            "days": 1,
            "start": "2026-08-19T22:00:00Z",
            "end_exclusive": "2026-08-20T22:00:00Z",
        },
        "coverage": {
            "status": "partial_observation",
            "complete": False,
            "exact_for_observed_samples": True,
            "reason": (
                "Direct event timestamps are exact but not provably exhaustive."
            ),
        },
        "sample_count": 4,
        "days": [
            {
                key: day[key]
                for key in (
                    "date",
                    "start",
                    "end_exclusive",
                    "partial",
                    "sample_count",
                )
            }
            for day in _lifecycle()["daily_wait_times"]["days"]
        ],
        "source": {
            "path": ops.QUEUE_LIFECYCLE_NAME,
            "schema_version": 1,
            "key": "daily_wait_times.days",
            "vector_key": "served_job_wait_seconds",
        },
    }
    assert all("served_job_wait_seconds" not in day for day in daily["days"])

    # The schema-v4 index remains lossless: its stable source pointer resolves
    # every exact value (including duplicates) from the public lifecycle file.
    source = _lifecycle()
    assert daily["source"]["key"] == "daily_wait_times.days"
    assert daily["source"]["vector_key"] == "served_job_wait_seconds"
    assert sum(
        len(day[daily["source"]["vector_key"]])
        for day in source["daily_wait_times"]["days"]
    ) == daily["sample_count"]


def test_org_summary_preserves_structured_byte_limited_lifecycle_scope() -> None:
    lifecycle = _lifecycle()
    ledger_scope = {
        "schema_version": 1,
        "policy": "newest_latest_event_suffix_v1",
        "configured_days": 1,
        "configured_event_start": "2026-08-19T22:00:00Z",
        "end_exclusive": "2026-08-20T22:00:00Z",
        "max_compressed_bytes": 16 * 1024 * 1024,
        "input_job_observations": 6,
        "published_job_observations": 4,
        "omitted_from_input_job_observations": 2,
        "omitted_whole_day_job_observations": 1,
        "omitted_whole_latest_event_days": ["2026-08-19"],
        "partial_latest_event_day": "2026-08-20",
        "partial_day_input_job_observations": 5,
        "partial_day_published_job_observations": 4,
        "carried_forward_omitted_latest_event_days": [],
        "byte_limited": True,
        "complete_relative_to_input": False,
        "complete_relative_to_configured_window": False,
        "published_latest_event_days": ["2026-08-20"],
        "published_latest_event_start": "2026-08-20T20:10:00Z",
        "published_latest_event_end": "2026-08-20T21:55:00Z",
    }
    lifecycle["retention"].update(
        {
            "byte_limited": True,
            "actual_published_latest_event_start": ledger_scope[
                "published_latest_event_start"
            ],
            "actual_published_latest_event_end": ledger_scope[
                "published_latest_event_end"
            ],
            "ledger_scope": ledger_scope,
        }
    )
    lifecycle["coverage"]["reason"] += " The durable ledger is byte-limited."

    queues = ops.build_org_summary(_payload(), lifecycle)["queues"]
    daily = queues["daily_served_job_waits"]
    assert daily["available"] is True
    assert daily["retention"] == {
        "kind": "rolling",
        "days": 1,
        "start": "2026-08-19T22:00:00Z",
        "end_exclusive": "2026-08-20T22:00:00Z",
        "byte_limited": True,
        "complete_relative_to_configured_window": False,
        "actual_published_latest_event_start": "2026-08-20T20:10:00Z",
        "actual_published_latest_event_end": "2026-08-20T21:55:00Z",
        "omitted_whole_latest_event_days": ["2026-08-19"],
        "partial_latest_event_day": "2026-08-20",
        "ledger_scope": ledger_scope,
    }
    assert daily["coverage"]["byte_limited"] is True
    assert daily["coverage"]["complete_relative_to_configured_window"] is False
    assert queues["recent_completed_window"]["coverage"] == {
        "status": "partial_observation",
        "complete": False,
        "reason": lifecycle["coverage"]["reason"],
        "byte_limited": True,
        "complete_relative_to_configured_window": False,
        "actual_published_latest_event_start": "2026-08-20T20:10:00Z",
        "actual_published_latest_event_end": "2026-08-20T21:55:00Z",
    }


def test_org_summary_marks_daily_waits_unavailable_without_dropping_rolling_counts() -> None:
    lifecycle = _lifecycle()
    lifecycle.pop("daily_wait_times")

    queues = ops.build_org_summary(_payload(), lifecycle)["queues"]

    assert queues["daily_served_job_waits"]["available"] is False
    assert queues["daily_served_job_waits"]["reason"] == (
        "daily_wait_times_unavailable"
    )
    assert queues["daily_served_job_waits"]["days"] == []
    assert queues["daily_served_job_waits"]["sample_count"] is None
    assert queues["recent_completed_window"]["served_jobs"] == 970


def test_org_summary_preserves_explicit_bounded_daily_wait_evidence() -> None:
    lifecycle = _lifecycle()
    day = lifecycle["daily_wait_times"]["days"][1]
    waits = day["served_job_wait_seconds"]
    day.update(
        {
            "served_job_wait_seconds": [],
            "vector_complete": False,
            "published_sample_count": 0,
            "omitted_sample_count": len(waits),
            "distribution": {
                "count": len(waits),
                "min": min(waits),
                "p50": waits[1],
                "p95": max(waits),
                "max": max(waits),
                "avg": sum(waits) / len(waits),
            },
        }
    )

    daily = ops.build_org_summary(_payload(), lifecycle)["queues"][
        "daily_served_job_waits"
    ]

    assert daily["available"] is True
    assert daily["sample_count"] == 4
    bounded = daily["days"][1]
    assert bounded["vector_complete"] is False
    assert bounded["omitted_sample_count"] == len(waits)
    assert bounded["distribution"]["count"] == len(waits)


def test_org_summary_rejects_noncontiguous_daily_wait_vectors() -> None:
    lifecycle = _lifecycle()
    lifecycle["daily_wait_times"]["days"][1]["date"] = "2026-08-21"

    waits = ops.build_org_summary(_payload(), lifecycle)["queues"][
        "daily_served_job_waits"
    ]

    assert waits["available"] is False
    assert waits["reason"] == "invalid_daily_wait_times"
    assert waits["days"] == []


def test_org_summary_rejects_a_malformed_daily_wait_vector() -> None:
    lifecycle = _lifecycle()
    day = lifecycle["daily_wait_times"]["days"][1]
    day["served_job_wait_seconds"] = list(
        reversed(day["served_job_wait_seconds"])
    )

    daily = ops.build_org_summary(_payload(), lifecycle)["queues"][
        "daily_served_job_waits"
    ]

    assert daily["available"] is False
    assert daily["reason"] == "invalid_daily_wait_times"
    assert daily["days"] == []


@pytest.mark.parametrize(
    "case",
    (
        "numeric_string",
        "wrong_retention_days",
        "noncanonical_timestamp",
        "extra_day",
        "hourly_count_mismatch",
        "inexact_observed_samples",
    ),
)
def test_org_summary_daily_wait_projection_fails_closed(case: str) -> None:
    lifecycle = _lifecycle()
    if case == "numeric_string":
        lifecycle["daily_wait_times"]["days"][0]["served_job_wait_seconds"] = [
            "21.737"
        ]
    elif case == "wrong_retention_days":
        lifecycle["retention"]["days"] = 999
    elif case == "noncanonical_timestamp":
        lifecycle["daily_wait_times"]["days"][0]["start"] = (
            "2026-08-19T22:00:00"
        )
    elif case == "extra_day":
        lifecycle["daily_wait_times"]["days"].append({
            "date": "2026-08-21",
            "start": "2026-08-21T00:00:00Z",
            "end_exclusive": "2026-08-20T22:00:00Z",
            "partial": True,
            "sample_count": 0,
            "served_job_wait_seconds": [],
        })
    elif case == "hourly_count_mismatch":
        lifecycle["hourly"][0]["totals"]["queue_wait_seconds"]["count"] = 3
    else:
        lifecycle["coverage"]["metric_exhaustiveness"]["served"][
            "exact_for_observed_events"
        ] = False

    daily = ops.build_org_summary(_payload(), lifecycle)["queues"][
        "daily_served_job_waits"
    ]

    assert daily["available"] is False
    assert daily["reason"] == "invalid_daily_wait_times"
    assert daily["days"] == []


def test_org_summary_fails_closed_when_amd_builds_do_not_align() -> None:
    payload = _payload()
    logical = payload["amd_test_health"]["summary"]["latest_test_group_counts"]
    logical["test_signal_build_number"] = 12274

    summary = ops.build_org_summary(payload, _lifecycle())

    observed = summary["test_groups"]["observed_latest_amd"]
    assert observed["available"] is False
    assert observed["reason"] == "build_mismatch"
    assert observed["total"] is None
    best = summary["health_checks"]["best_hardware"]
    assert best["available"] is False
    assert best["green"] is None


def test_org_summary_fails_closed_when_target_queue_scope_is_incomplete() -> None:
    payload = _payload()
    payload["queue"]["snapshot"]["target_queue_scope"]["all_rows_present"] = False

    current = ops.build_org_summary(payload, _lifecycle())["queues"]["current"]

    assert current["available"] is False
    assert current["waiting_jobs"] is None
    assert current["running_jobs"] is None


def test_snapshot_bundle_writes_bounded_discoverable_org_summary(tmp_path) -> None:
    output = tmp_path / "operations_v2.json"
    (tmp_path / ops.QUEUE_LIFECYCLE_NAME).write_text(json.dumps(_lifecycle()))

    manifest = ops.write_snapshot_bundle(output, _payload(), log=False)

    descriptor = manifest["organization_summary"]
    assert descriptor["path"] == ops.ORG_SUMMARY_NAME
    path = tmp_path / descriptor["path"]
    summary = json.loads(path.read_text())
    assert summary["generated_at"] == GENERATED_AT
    assert path.stat().st_size == descriptor["bytes"]
    assert path.stat().st_size < ops.ORG_SUMMARY_MAX_BYTES
    assert descriptor["schema_version"] == ops.ORG_SUMMARY_SCHEMA_VERSION == 6
    assert "groups" not in summary["scheduled_cohorts"]["upstream_nightly"]


def test_snapshot_bundle_references_oversized_exact_wait_vectors_without_duplication(
    tmp_path,
) -> None:
    output = tmp_path / "operations_v2.json"
    lifecycle = _lifecycle()
    waits = [index / 1_000 for index in range(300_000)]
    first, second = lifecycle["daily_wait_times"]["days"]
    first["sample_count"] = 0
    first["served_job_wait_seconds"] = []
    second["sample_count"] = len(waits)
    second["served_job_wait_seconds"] = waits
    lifecycle["hourly"][0]["totals"]["queue_wait_seconds"]["count"] = len(waits)
    source_path = tmp_path / ops.QUEUE_LIFECYCLE_NAME
    source_path.write_text(json.dumps(lifecycle, separators=(",", ":")))
    assert source_path.stat().st_size > ops.ORG_SUMMARY_MAX_BYTES

    manifest = ops.write_snapshot_bundle(output, _payload(), log=False)

    summary_path = tmp_path / manifest["organization_summary"]["path"]
    summary = json.loads(summary_path.read_text())
    daily = summary["queues"]["daily_served_job_waits"]
    assert summary_path.stat().st_size < ops.ORG_SUMMARY_MAX_BYTES
    assert daily["sample_count"] == len(waits)
    assert daily["source"]["path"] == ops.QUEUE_LIFECYCLE_NAME
    assert all("served_job_wait_seconds" not in day for day in daily["days"])

    referenced = json.loads(source_path.read_text())
    exact = referenced["daily_wait_times"]["days"][1][
        daily["source"]["vector_key"]
    ]
    assert exact == waits


def test_snapshot_bundle_preserves_lkg_when_org_summary_exceeds_budget(
    tmp_path, monkeypatch
) -> None:
    data_dir = tmp_path / "data" / "vllm" / "ci"
    data_dir.mkdir(parents=True)
    output = data_dir / "operations_v2.json"
    (data_dir / ops.QUEUE_LIFECYCLE_NAME).write_text(json.dumps(_lifecycle()))
    summary_path = data_dir / ops.ORG_SUMMARY_NAME
    manifest_path = data_dir / ops.OPERATIONS_MANIFEST_NAME
    summary_path.write_text("existing-summary")
    manifest_path.write_text("existing-manifest")
    monkeypatch.setattr(ops, "ORG_SUMMARY_MAX_BYTES", 1)

    with pytest.raises(RuntimeError, match="organization summary exceeds"):
        ops.write_snapshot_bundle(output, _payload(), log=False)

    assert summary_path.read_text() == "existing-summary"
    assert manifest_path.read_text() == "existing-manifest"
    assert not output.exists()


def test_org_summary_compacts_queue_rows_but_preserves_exact_totals() -> None:
    rows = [
        {
            "queue": f"queue-{index:04d}-" + "x" * 500,
            "waiting_jobs": index % 2,
            "running_jobs": 1,
            "wait_source": "y" * 500,
        }
        for index in range(200)
    ]
    source = {
        "schema_version": ops.ORG_SUMMARY_SCHEMA_VERSION,
        "queues": {
            "scope": {"queue_count": len(rows), "queue_ids": [row["queue"] for row in rows]},
            "current": {"waiting_jobs": 100, "running_jobs": 200},
            "by_queue": rows,
        },
        "test_group_parity": {"summary": {"total": 10}},
        "parity_targets": {"reviewed": {"total": 20}},
    }

    bounded = ops._bounded_org_summary(source, max_bytes=20_000)

    assert ops._json_bytes(bounded) <= 20_000
    assert bounded["queues"]["current"] == source["queues"]["current"]
    retention = bounded["publication_retention"]
    assert retention["aggregate_totals_complete"] is True
    assert retention["queue_rows"]["published"] < len(rows)
    assert retention["queue_rows"]["omitted"] > 0
    assert retention["complete_relative_to_source"] is False


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
    assert summary["schema_version"] == ops.ORG_SUMMARY_SCHEMA_VERSION == 6
    summary["test_groups"]["observed_latest_amd"]["total"] = 236
    path.write_text(json.dumps(summary, indent=2) + "\n")

    invalid = DashboardAudit(tmp_path)
    invalid.audit_operations_bundle()
    assert "operations-bundle-org-summary-projection" in {
        finding.code for finding in invalid.report.findings
    }


def test_dashboard_audit_rejects_invalid_available_nightly_denominators(
    tmp_path,
) -> None:
    data_dir = tmp_path / "data" / "vllm" / "ci"
    data_dir.mkdir(parents=True)
    output = data_dir / "operations_v2.json"
    (data_dir / ops.QUEUE_LIFECYCLE_NAME).write_text(json.dumps(_lifecycle()))
    payload = _payload()
    payload["gating"]["upstream_scheduled"]["latest_by_kind"]["nightly"][
        "summary"
    ]["gated"] = None
    ops.write_snapshot_bundle(output, payload, log=False)

    invalid = DashboardAudit(tmp_path)
    invalid.audit_operations_bundle()

    assert "operations-bundle-org-summary-scheduled-denominators" in {
        finding.code for finding in invalid.report.findings
    }


@pytest.mark.parametrize("case", ("path", "key", "sample_count", "day_bounds"))
def test_dashboard_audit_rejects_a_drifted_org_summary_wait_reference(
    tmp_path, case: str
) -> None:
    data_dir = tmp_path / "data" / "vllm" / "ci"
    data_dir.mkdir(parents=True)
    output = data_dir / "operations_v2.json"
    (data_dir / ops.QUEUE_LIFECYCLE_NAME).write_text(json.dumps(_lifecycle()))
    ops.write_snapshot_bundle(output, _payload(), log=False)

    path = data_dir / ops.ORG_SUMMARY_NAME
    summary = json.loads(path.read_text())
    waits = summary["queues"]["daily_served_job_waits"]
    if case == "path":
        waits["source"]["path"] = "other.json"
    elif case == "key":
        waits["source"]["key"] = "daily_wait_times.other"
    elif case == "sample_count":
        waits["sample_count"] += 1
    else:
        waits["days"][0]["start"] = "2026-08-19T23:00:00Z"
    path.write_text(json.dumps(summary, separators=(",", ":")) + "\n")

    invalid = DashboardAudit(tmp_path)
    invalid.audit_operations_bundle()
    assert "operations-bundle-org-summary-source" in {
        finding.code for finding in invalid.report.findings
    }


@pytest.mark.live_data
def test_published_org_summary_has_consistent_denominators() -> None:
    path = ops.ROOT / "data" / "vllm" / "ci" / ops.ORG_SUMMARY_NAME
    summary = json.loads(path.read_text())

    logical = summary["test_groups"]["observed_latest_amd"]
    variants = summary["test_groups"]["exact_job_variants_latest_amd"]
    assert "configured_amd_definitions" not in summary["test_groups"]
    assert logical["available"] is True
    assert logical["green_on_all_observed_hardware"] <= logical["green"] <= logical["total"]
    assert logical["green_on_all_observed_hardware"] + logical["mixed_by_hardware"] == logical["green"]
    assert logical["non_green"] == logical["total"] - logical["green"]
    assert variants["build_number"] == logical["build_number"]
    assert variants["total"] >= logical["total"]

    best = summary["health_checks"]["best_hardware"]
    assert best["available"] is True
    assert best["build_number"] == logical["build_number"]
    assert best["non_green"] == best["total"] - best["green"]

    scheduled = summary["scheduled_cohorts"]["upstream_nightly"]
    scheduled_count_fields = (
        "configured",
        "observed",
        "green",
        "non_green",
        "failing",
        "soft_failing",
        "pending",
        "missing",
        "queues_configured",
        "queues_with_observed_work",
    )
    if scheduled["available"] is False:
        assert all(scheduled[key] is None for key in scheduled_count_fields)
    else:
        assert scheduled["available"] is True
        assert all(
            type(scheduled[key]) is int and scheduled[key] >= 0
            for key in scheduled_count_fields
        )
        assert scheduled["configured"] == (
            scheduled["observed"] + scheduled["missing"]
        )
        assert scheduled["observed"] == sum(
            scheduled[key]
            for key in ("green", "failing", "soft_failing", "pending")
        )
        assert scheduled["non_green"] == (
            scheduled["observed"] - scheduled["green"]
        )

    current = summary["queues"]["current"]
    queue_rows = summary["queues"]["by_queue"]
    assert current["available"] is True
    assert current["waiting_jobs"] == sum(row["waiting_jobs"] for row in queue_rows)
    assert current["running_jobs"] == sum(row["running_jobs"] for row in queue_rows)

    wait = summary["queues"]["daily_served_job_waits"]
    assert wait["available"] is True
    assert wait["unit"] == "seconds"
    assert wait["sample_order"] == "ascending"
    assert wait["days"]
    source_path = path.parent / wait["source"]["path"]
    source = json.loads(source_path.read_text())
    assert wait["source"] == {
        "path": ops.QUEUE_LIFECYCLE_NAME,
        "schema_version": source["schema_version"],
        "key": "daily_wait_times.days",
        "vector_key": "served_job_wait_seconds",
    }
    source_days = source["daily_wait_times"]["days"]
    assert [day["sample_count"] for day in wait["days"]] == [
        day["sample_count"] for day in source_days
    ]
    for day, source_day in zip(wait["days"], source_days, strict=True):
        values = source_day[wait["source"]["vector_key"]]
        assert values == sorted(values)
        assert all(value >= 0 for value in values)
        if source_day.get("vector_complete") is False:
            assert day["vector_complete"] is False
            assert day["published_sample_count"] == len(values)
            assert day["published_sample_count"] + day["omitted_sample_count"] == day[
                "sample_count"
            ]
            assert day["distribution"]["count"] == day["sample_count"]
        else:
            assert day["sample_count"] == len(values)
    assert wait["sample_count"] == sum(day["sample_count"] for day in wait["days"])
    assert path.stat().st_size < ops.ORG_SUMMARY_MAX_BYTES
