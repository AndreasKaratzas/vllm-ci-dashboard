"""Unit tests for CI dashboard JSON reporters."""

from __future__ import annotations

import json

from vllm.ci.models import TestHealth
from vllm.ci.reporter import HEALTH_LABEL_BUCKETS, write_ci_health


def test_write_ci_health_emits_zero_count_health_buckets(tmp_path):
    write_ci_health(
        amd_summaries=[],
        upstream_summaries=[],
        health_data=[
            TestHealth(
                test_id="tests/example.py::test_passes",
                label="passing",
                pass_rate=1.0,
                appearances=1,
                last_seen="2026-05-20",
            )
        ],
        output_dir=tmp_path,
    )

    health = json.loads((tmp_path / "ci_health.json").read_text())
    assert set(HEALTH_LABEL_BUCKETS) <= set(health["test_counts"])
    assert health["test_counts"]["passing"] == 1
    assert health["test_counts"]["flaky"] == 0
