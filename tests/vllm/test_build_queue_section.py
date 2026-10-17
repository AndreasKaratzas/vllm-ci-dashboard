import json

import pytest

from vllm import build_queue_section as bqs
from vllm import build_operations_snapshot as ops
from vllm.operations_bundle_contract import OPERATIONS_CANARY_SECTION_MAX_BYTES


def test_queue_section_builder_reads_only_queue_owned_inputs(tmp_path):
    snapshot = {
        "ts": "2026-08-04T18:30:00Z",
        "schema_version": 2,
        "queues": {
            "amd_mi300_1": {
                "waiting": 2,
                "running": 1,
                "p50_wait": 55.0,
                "p50_wait_source": "official_wait",
                "p95_wait": 65.0,
                "p95_wait_source": "official_wait",
                "p99_wait": 80.0,
                "p99_wait_source": "sample_wait",
                "official_wait": {"p50": 55.0, "p95": 65.0, "max": 90.0},
                "sample_wait": {
                    "available": True,
                    "count": 2,
                    "p50": 60.0,
                    "p95": 75.0,
                    "p99": 80.0,
                },
                "official_wait_source": "queue_native_metrics",
                "sample_wait_source": "cluster_queue_graphql",
                "wait_sample_count": 2,
                "wait_sample_expected_count": 2,
                "wait_sample_complete": True,
            }
        },
        "total_waiting": 2,
        "total_running": 1,
        "sources": {"waits": "scheduled_jobs"},
    }
    (tmp_path / "queue_timeseries.jsonl").write_text(json.dumps(snapshot) + "\n")
    (tmp_path / "queue_jobs.json").write_text(
        json.dumps({"ts": snapshot["ts"], "pending": [], "running": []})
    )
    # An unrelated malformed dashboard input must not affect this narrow build.
    (tmp_path / "analytics.json").write_text("not json")

    section = bqs.build_queue_section(tmp_path)

    queue = section["queue"]
    assert queue["snapshot"]["ts"] == snapshot["ts"]
    assert queue["history_summary"]["snapshot_count"] == 1
    assert queue["history"] == []
    assert queue["history_summary"]["source_path"] == "queue_timeseries.jsonl"
    assert queue["snapshot"]["queues"]["amd_mi300_1"]["p95_wait"] == 65.0

    chart = ops.build_queue_history_chart([snapshot], snapshot["ts"])
    assert chart["queue_names"] == ["amd_mi300_1"]
    assert chart["points"][0][0] == snapshot["ts"]
    values = chart["points"][0][1][0]
    assert values[:5] == [2, 1, 55.0, 65.0, 80.0]
    assert chart["wait_sources"][values[6]] == "official_wait"
    assert values[10:13] == [2, 2, 1]
    assert values[14] == [55.0, 65.0, 90.0]
    assert values[15] == [60.0, 75.0, 80.0]


def test_queue_history_chart_drops_only_oldest_whole_snapshots_to_fit() -> None:
    history = [
        {
            "ts": f"2026-08-04T{hour:02d}:00:00Z",
            "queues": {
                "amd_mi300_1": {
                    "waiting": hour,
                    "running": 1,
                    "p50_wait_source": "x" * 400,
                }
            },
        }
        for hour in range(10)
    ]
    two_row_bytes = len(
        ops._encoded_json(ops.build_queue_history_chart(history[-2:])).encode("utf-8")
    ) + 512

    chart, encoded = ops._bounded_queue_history_chart(
        history,
        history[-1]["ts"],
        max_bytes=two_row_bytes,
    )

    retention = chart["publication_retention"]
    assert len(encoded) <= two_row_bytes
    assert 0 < retention["published_snapshot_count"] < len(history)
    assert retention["omitted_oldest_snapshot_count"] == (
        len(history) - retention["published_snapshot_count"]
    )
    assert chart["points"][-1][0] == history[-1]["ts"]
    assert retention["retained_start"] == chart["points"][0][0]
    assert retention["complete_relative_to_source"] is False


def test_queue_section_uses_exact_central_operations_cap() -> None:
    assert bqs.QUEUE_SECTION_MAX_BYTES == 1024 * 1024
    assert bqs.QUEUE_SECTION_MAX_BYTES == OPERATIONS_CANARY_SECTION_MAX_BYTES["queue"]


def test_queue_section_compacts_priority_job_rows_deterministically() -> None:
    pending = [
        {
            "id": f"pending-{index}",
            "name": f"Pending {index}",
            "queue": "amd_mi300_1",
            "wait_min": index,
            "evidence": "p" * 4_000,
        }
        for index in range(8)
    ]
    running = [
        {
            "id": f"running-{index}",
            "name": f"Running {index}",
            "queue": "amd_mi300_1",
            "run_min": index,
            "evidence": "r" * 4_000,
        }
        for index in range(8)
    ]
    queue = {
        "snapshot": {
            "ts": "2026-09-01T00:00:00Z",
            "total_waiting": 8,
            "total_running": 8,
            "queues": {"amd_mi300_1": {"waiting": 8, "running": 8}},
        },
        "queue_jobs": {"pending": pending, "running": running},
        "history": [],
        "pressure_baseline": {
            "amd_mi300_1": {"median": 1, "p95": 2, "snapshot_count": 10}
        },
        "history_summary": {"snapshot_count": 10},
        "provenance": {},
    }

    first = bqs.compact_queue_section(queue, max_bytes=24_000)
    reversed_input = json.loads(json.dumps(queue))
    reversed_input["queue_jobs"]["pending"].reverse()
    reversed_input["queue_jobs"]["running"].reverse()
    second = bqs.compact_queue_section(reversed_input, max_bytes=24_000)

    assert first == second
    assert len(ops._encoded_json(first).encode("utf-8")) <= 24_000
    projected = first["queue"]
    retention = projected["operations_publication_retention"]
    assert retention["snapshot_queues"]["complete_relative_to_source"] is True
    assert retention["pressure_baseline"]["complete_relative_to_source"] is True
    assert retention["complete_relative_to_source"] is False
    assert retention["scope_totals"]["canonical"] == {
        "queue_count": 1,
        "waiting": 8.0,
        "running": 8.0,
        "published_queue_count": 1,
    }
    jobs = projected["queue_jobs"]
    published = len(jobs["pending"]) + len(jobs["running"])
    assert 0 < published < len(pending) + len(running)
    assert jobs["publication_retention"]["pending"]["source"] == len(pending)
    assert jobs["publication_retention"]["running"]["source"] == len(running)
    assert all(len(row["evidence"]) == 4_000 for row in jobs["pending"])
    assert all(len(row["evidence"]) == 4_000 for row in jobs["running"])


def test_full_and_live_operations_producers_share_exact_bounded_queue_projection() -> None:
    queue = {
        "snapshot": {
            "ts": "2026-09-01T00:00:00Z",
            "total_waiting": 160,
            "total_running": 0,
            "queues": {"amd_mi300_1": {"waiting": 160, "running": 0}},
        },
        "queue_jobs": {
            "pending": [
                {
                    "id": f"job-{index}",
                    "queue": "amd_mi300_1",
                    "wait_min": index,
                    "evidence": "x" * 8_000,
                }
                for index in range(160)
            ],
            "running": [],
        },
        "history": [{"ts": "2026-09-01T00:00:00Z", "queues": {}}],
        "pressure_baseline": {"amd_mi300_1": {"median": 1, "p95": 2}},
        "history_summary": {"snapshot_count": 1},
        "provenance": {},
    }

    live = bqs.compact_queue_section(ops._compact_queue(queue))
    full = ops._operation_sections({"queue": queue, "reliability": {}})["queue"]

    assert full == live
    assert len(ops._encoded_json(full).encode("utf-8")) <= (
        bqs.QUEUE_SECTION_MAX_BYTES
    )
    retention = full["queue"]["operations_publication_retention"]
    assert retention["max_bytes"] == bqs.QUEUE_SECTION_MAX_BYTES
    assert retention["complete_relative_to_source"] is False
    assert retention["queue_jobs"]["pending"]["omitted_by_operations"] > 0


def test_queue_section_compacts_baselines_then_current_rows_truthfully() -> None:
    queues = {
        f"external-{index:03d}": {
            "waiting": 1,
            "running": 0,
            "evidence": "q" * 2_000,
        }
        for index in range(20)
    }
    queue = {
        "snapshot": {
            "ts": "2026-09-01T00:00:00Z",
            "total_waiting": 20,
            "total_running": 0,
            "queues": queues,
        },
        "queue_jobs": {"pending": [], "running": []},
        "history": [],
        "pressure_baseline": {
            name: {"p95": 1, "evidence": "b" * 2_000} for name in queues
        },
        "history_summary": {"snapshot_count": 10},
        "provenance": {},
    }

    section = bqs.compact_queue_section(queue, max_bytes=12_000)

    assert len(ops._encoded_json(section).encode("utf-8")) <= 12_000
    projected = section["queue"]
    retention = projected["operations_publication_retention"]
    assert retention["pressure_baseline"]["published"] == 0
    assert 0 < retention["snapshot_queues"]["published"] < 20
    assert retention["snapshot_queues"]["source"] == 20
    assert retention["snapshot_queues"]["omitted"] == (
        20 - retention["snapshot_queues"]["published"]
    )
    assert retention["scope_totals"]["all"]["queue_count"] == 20
    assert retention["scope_totals"]["all"]["waiting"] == 20
    assert retention["aggregate_totals_complete"] is True
    assert retention["complete_relative_to_source"] is False


def test_queue_section_sanitizes_adversarial_fixed_metadata_without_wedging() -> None:
    queue = {
        "snapshot": {
            "ts": "2026-09-01T00:00:00Z",
            "total_waiting": 1,
            "total_running": 0,
            "queues": {"amd_mi300_1": {"waiting": 1, "running": 0}},
            "sources": {"unbounded": "s" * 200_000},
        },
        "queue_jobs": {
            "ts": "2026-09-01T00:00:00Z",
            "pending": [{"id": "job-1", "queue": "amd_mi300_1", "wait_min": 10}],
            "running": [],
            "future_unbounded_metadata": "j" * 200_000,
            "publication_retention": {
                "pending": {"source": 1},
                "running": {"source": 0},
                "future_unbounded_metadata": "r" * 200_000,
            },
        },
        "history": [],
        "pressure_baseline": {"amd_mi300_1": {"median": 1, "p95": 2}},
        "history_summary": {
            "snapshot_count": 1,
            "future_unbounded_metadata": "h" * 200_000,
        },
        "provenance": {"unbounded": "p" * 200_000},
    }

    section = bqs.compact_queue_section(queue, max_bytes=20_000)

    assert len(ops._encoded_json(section).encode("utf-8")) <= 20_000
    projected = section["queue"]
    assert set(projected["snapshot"]["queues"]) == {"amd_mi300_1"}
    assert [row["id"] for row in projected["queue_jobs"]["pending"]] == ["job-1"]
    fixed = projected["operations_publication_retention"]["fixed_metadata"]
    assert fixed["complete_relative_to_source"] is False
    assert fixed["omitted_field_count"] == 5
    assert fixed["omitted_field_examples_complete"] is True
    assert fixed["omitted_field_examples"] == [
        "history_summary.future_unbounded_metadata",
        "provenance",
        "queue_jobs.future_unbounded_metadata",
        "queue_jobs.publication_retention.future_unbounded_metadata",
        "snapshot.sources",
    ]
    assert (
        projected["operations_publication_retention"]["aggregate_totals_complete"]
        is True
    )


def test_queue_section_overflow_preserves_last_known_good_file(
    tmp_path, monkeypatch
) -> None:
    snapshot = {
        "ts": "2026-09-01T00:00:00Z",
        "queues": {"amd_mi300_1": {"waiting": 0, "running": 0}},
        "total_waiting": 0,
        "total_running": 0,
        "sources": {"counts": "test"},
    }
    (tmp_path / "queue_timeseries.jsonl").write_text(json.dumps(snapshot) + "\n")
    (tmp_path / "queue_jobs.json").write_text(
        json.dumps({"pending": [], "running": []})
    )
    output = tmp_path / "operations_v2" / "queue.json"
    output.parent.mkdir()
    output.write_text('{"last_known_good":true}\n')
    monkeypatch.setattr(bqs, "QUEUE_SECTION_MAX_BYTES", 1)

    with pytest.raises(RuntimeError, match="last-known-good"):
        bqs.main(["--input-dir", str(tmp_path), "--output", str(output)])

    assert output.read_text() == '{"last_known_good":true}\n'


def test_queue_section_validator_rejects_exact_cap_overflow(tmp_path) -> None:
    output = tmp_path / "queue.json"
    output.write_bytes(b"x" * (bqs.QUEUE_SECTION_MAX_BYTES + 1))

    with pytest.raises(RuntimeError, match="limit is 1048576 bytes"):
        bqs.validate_queue_section_file(output)
