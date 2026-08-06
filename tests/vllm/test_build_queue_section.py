import json

from vllm import build_queue_section as bqs
from vllm import build_operations_snapshot as ops


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
