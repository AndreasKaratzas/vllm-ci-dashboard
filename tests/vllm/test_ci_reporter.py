"""Unit tests for CI dashboard JSON reporters."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from vllm.ci import reporter
from vllm.ci.models import BuildSummary, TestHealth
from vllm.ci.reporter import HEALTH_LABEL_BUCKETS, write_ci_health


def _result(*, date: str = "2026-09-01", failure_message: str = ""):
    return reporter.TestResult(
        test_id="tests/example.py::test_case",
        name="test_case",
        classname="tests.example",
        status="failed" if failure_message else "passed",
        duration_secs=1.0,
        failure_message=failure_message,
        job_name="Tests",
        job_id="job-1",
        step_id="step-1",
        build_number=1,
        pipeline="amd-ci",
        date=date,
    )


def test_test_result_writer_rejects_oversize_before_replacing_lkg(
    tmp_path, monkeypatch
):
    path = tmp_path / "2026-09-01_amd.jsonl"
    path.write_text('{"generation":"last-known-good"}\n')
    monkeypatch.setattr(reporter, "TEST_RESULT_SHARD_MAX_BYTES", 256)
    monkeypatch.setattr(reporter, "TEST_RESULT_STORE_MAX_BYTES", 1024)

    with pytest.raises(RuntimeError, match="complete test-result shard exceeds"):
        reporter.write_test_results(
            [_result(failure_message="x" * 512)],
            "2026-09-01",
            "amd",
            tmp_path,
        )

    assert path.read_text() == '{"generation":"last-known-good"}\n'


def test_test_result_store_drops_oldest_complete_utc_days(tmp_path):
    for date in ("2026-08-29", "2026-08-30", "2026-08-31"):
        for pipeline in ("amd", "upstream"):
            (tmp_path / f"{date}_{pipeline}.jsonl").write_bytes(b"x" * 40)

    removed = reporter.prune_old_results(
        tmp_path,
        max_days=30,
        max_total_bytes=160,
        max_shard_bytes=100,
        now=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    assert removed == 2
    assert not list(tmp_path.glob("2026-08-29_*.jsonl"))
    assert len(list(tmp_path.glob("*.jsonl"))) == 4
    assert sum(path.stat().st_size for path in tmp_path.glob("*.jsonl")) == 160


def test_test_result_store_preserves_every_file_when_newest_day_cannot_fit(tmp_path):
    paths = []
    for date, size in (("2026-08-30", 40), ("2026-08-31", 60)):
        for pipeline in ("amd", "upstream"):
            path = tmp_path / f"{date}_{pipeline}.jsonl"
            path.write_bytes(b"x" * size)
            paths.append(path)

    with pytest.raises(RuntimeError, match="newest complete test-result day cannot fit"):
        reporter.prune_old_results(
            tmp_path,
            max_days=30,
            max_total_bytes=100,
            max_shard_bytes=100,
            now=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )

    assert all(path.exists() for path in paths)


def test_old_complete_shard_is_omitted_repeatably_below_durable_floor(
    tmp_path, monkeypatch
):
    newest = tmp_path / "2026-09-01_upstream.jsonl"
    newest.write_bytes(b"x" * 800)
    monkeypatch.setattr(reporter, "TEST_RESULT_SHARD_MAX_BYTES", 2_000)
    monkeypatch.setattr(reporter, "TEST_RESULT_STORE_MAX_BYTES", 900)

    outcomes = [
        reporter.write_test_results(
            [_result(date="2026-08-31")],
            "2026-08-31",
            "amd",
            tmp_path,
        )
        for _ in range(2)
    ]

    assert outcomes == [None, None]
    assert newest.read_bytes() == b"x" * 800
    assert not (tmp_path / "2026-08-31_amd.jsonl").exists()
    assert reporter.retained_result_start(tmp_path) == "2026-09-01"


def test_retention_floor_rejects_shard_or_policy_tampering(tmp_path):
    shard = tmp_path / "2026-09-01_amd.jsonl"
    shard.write_bytes(b'{"row":1}\n')
    reporter.prune_old_results(tmp_path, max_days=365)

    shard.write_bytes(b'{"row":2}\n')
    with pytest.raises(RuntimeError, match="disagrees with exact shards"):
        reporter.retained_result_start(tmp_path)
    with pytest.raises(RuntimeError, match="disagrees with exact shards"):
        reporter.prune_old_results(tmp_path, max_days=365)
    assert shard.read_bytes() == b'{"row":2}\n'

    # Regenerate a valid marker, then prove a stale policy limit also fails
    # closed before it can suppress any Buildkite fetch.
    (tmp_path / reporter.TEST_RESULT_RETENTION_FILE).unlink()
    reporter.prune_old_results(tmp_path, max_days=365)
    marker_path = tmp_path / reporter.TEST_RESULT_RETENTION_FILE
    marker = json.loads(marker_path.read_text())
    marker["max_total_bytes"] -= 1
    marker_path.write_text(json.dumps(marker))
    with pytest.raises(RuntimeError, match="stale byte limits"):
        reporter.retained_result_start(tmp_path)


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


def test_build_summary_uses_pipeline_specific_group_count_basis():
    amd = BuildSummary(
        pipeline="amd",
        build_number=1,
        build_url="",
        branch="main",
        commit="abc123",
        created_at="2026-08-21T00:00:00Z",
        state="passed",
    ).to_dict()
    upstream = BuildSummary(
        pipeline="upstream",
        build_number=2,
        build_url="",
        branch="main",
        commit="abc123",
        created_at="2026-08-21T00:00:00Z",
        state="passed",
    ).to_dict()

    assert "normalized label plus agent pool" in amd[
        "observed_unique_test_groups_count_basis"
    ]
    assert "normalized label plus agent pool" not in upstream[
        "observed_unique_test_groups_count_basis"
    ]
    assert "once per normalized group" in upstream[
        "observed_unique_test_groups_count_basis"
    ]


def test_ci_health_compacts_oldest_whole_builds_with_exact_accounting(
    tmp_path, monkeypatch
):
    summaries = [
        BuildSummary(
            pipeline="amd",
            build_number=number,
            build_url=f"https://buildkite.com/vllm/amd-ci/builds/{number}",
            branch="main",
            commit=f"{number:040x}",
            created_at=f"2026-08-{number:02d}T00:00:00Z",
            state="passed",
            has_test_results=True,
            by_hardware={"mi300x": {"padding": "x" * 300}},
        )
        for number in range(10, 0, -1)
    ]
    monkeypatch.setattr(reporter, "CI_HEALTH_MAX_BYTES", 9_000)

    reporter.write_ci_health(summaries, [], [], tmp_path)

    payload = json.loads((tmp_path / "ci_health.json").read_text())
    retention = payload["publication_retention"]
    assert retention["complete_relative_to_source"] is False
    assert retention["builds"]["amd"]["source"] == 10
    assert retention["builds"]["amd"]["published"] == len(
        payload["amd"]["builds"]
    )
    assert [row["build_number"] for row in payload["amd"]["builds"]] == [
        row.build_number
        for row in summaries[: retention["builds"]["amd"]["published"]]
    ]
    assert payload["amd"]["latest_build"]["build_number"] == 10
    assert (tmp_path / "ci_health.json").stat().st_size <= 9_000


def test_ci_health_irreducible_overflow_preserves_lkg(tmp_path, monkeypatch):
    path = tmp_path / "ci_health.json"
    path.write_text('{"generation":"last-known-good"}\n')
    summary = BuildSummary(
        pipeline="amd",
        build_number=1,
        build_url="https://buildkite.com/vllm/amd-ci/builds/1",
        branch="main",
        commit="a" * 40,
        created_at="2026-08-01T00:00:00Z",
        state="passed",
        has_test_results=True,
        by_hardware={"mi300x": {"padding": "x" * 5_000}},
    )
    monkeypatch.setattr(reporter, "CI_HEALTH_MAX_BYTES", 500)

    with pytest.raises(RuntimeError, match="fixed/latest metadata exceeds"):
        reporter.write_ci_health([summary], [], [], tmp_path)

    assert json.loads(path.read_text()) == {"generation": "last-known-good"}


def test_parity_writer_compacts_whole_rows_with_pair_budget(tmp_path, monkeypatch):
    rows = [{"name": f"group-{index}", "padding": "x" * 500} for index in range(10)]
    monkeypatch.setattr(reporter, "CI_PARITY_PAIR_MAX_BYTES", 5_000)

    reporter.write_parity_report(
        {
            "parity_pct": 99.0,
            "summary": {"amd_only": 10, "upstream_only": 0},
            "by_module": {f"module-{index}": row for index, row in enumerate(rows)},
            "job_groups": rows,
            "details": rows,
        },
        "2026-08-01",
        "2026-08-01",
        tmp_path,
    )

    payload = json.loads((tmp_path / "parity_report.json").read_text())
    retention = payload["publication_retention"]
    assert retention["complete_relative_to_source"] is False
    assert payload["parity_pct"] == 99.0
    for field in ("by_module", "job_groups", "details"):
        counts = retention["collections"][field]
        assert counts["source"] == 10
        assert counts["published"] == len(payload[field])
        assert counts["omitted"] == 10 - len(payload[field])
    assert (tmp_path / "parity_report.json").stat().st_size <= 2_500
