"""Unit tests for CI dashboard JSON reporters."""

from __future__ import annotations

import json

from vllm.ci.models import BuildSummary, TestHealth
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


def test_write_ci_health_emits_explicit_assertion_pass_rate_semantics(tmp_path):
    summary = BuildSummary(
        pipeline="amd",
        build_number=123,
        build_url="https://buildkite.com/vllm/amd-ci/builds/123",
        branch="main",
        commit="abc123",
        created_at="2026-08-16T09:00:00Z",
        state="passed",
        total_tests=10,
        passed=2,
        failed=1,
        skipped=7,
        pass_rate=0.6667,
        has_test_results=True,
    )

    write_ci_health([summary], [], [], tmp_path)

    health = json.loads((tmp_path / "ci_health.json").read_text())
    assert health["pass_rate_contract_version"] == 1
    amd = health["amd"]
    rows = [
        amd["latest_build"],
        amd["latest_test_signal_build"],
        amd["latest_pipeline_build"],
        amd["builds"][0],
    ]
    for row in rows:
        assert row["pass_rate"] == 0.6667
        assert row["test_pass_rate_pct"] == 66.67
        assert row["test_pass_rate_basis"] == "pytest_assertions_excluding_skipped"


def test_write_ci_health_names_observed_unique_test_group_population(tmp_path):
    summary = BuildSummary(
        pipeline="amd",
        build_number=12275,
        build_url="https://buildkite.com/vllm/amd-ci/builds/12275",
        branch="main",
        commit="abc123",
        created_at="2026-08-20T08:04:00Z",
        state="passed",
        has_test_results=True,
        unique_test_groups=150,
    )

    write_ci_health([summary], [], [], tmp_path)

    health = json.loads((tmp_path / "ci_health.json").read_text())
    amd = health["amd"]
    rows = {
        "latest_build": amd["latest_build"],
        "latest_test_signal_build": amd["latest_test_signal_build"],
        "latest_pipeline_build": amd["latest_pipeline_build"],
        "builds[0]": amd["builds"][0],
    }
    for label, row in rows.items():
        assert row["unique_test_groups"] == 150, label
        assert row["observed_unique_test_groups"] == 150, label
        basis = row["observed_unique_test_groups_count_basis"]
        assert "observed in this build" in basis, label
        assert "hardware-specific executions" in basis, label
        assert "configured %N shard jobs" in basis, label
        assert "configured-definition inventories are separate" in basis, label
