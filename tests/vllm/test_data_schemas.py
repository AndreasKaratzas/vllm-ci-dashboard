"""Schema contract tests for committed data files in ``data/vllm/ci/``.

The dashboard JS relies on specific top-level keys and row shapes. If a
collector silently drops a field, these tests fail before the change
hits the dashboard. Files that don't yet exist (e.g., hotness.json on
a fresh clone) are skipped rather than failing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "vllm" / "ci"


def _load_json_or_skip(name: str):
    path = DATA / name
    if not path.exists():
        pytest.skip(f"{name} not present in this checkout")
    return json.loads(path.read_text())


def _assert_has_keys(obj: dict, required: set, path: str):
    missing = required - set(obj.keys())
    assert not missing, f"{path} missing required keys: {sorted(missing)}"


class TestCiHealth:
    def test_top_level_keys(self):
        d = _load_json_or_skip("ci_health.json")
        _assert_has_keys(
            d, {"generated_at", "amd", "upstream", "overall_health", "test_counts"},
            "ci_health.json",
        )

    def test_test_counts_buckets(self):
        d = _load_json_or_skip("ci_health.json")
        _assert_has_keys(
            d["test_counts"],
            {"passing", "failing", "flaky", "skipped", "fixed", "new_test"},
            "ci_health.json.test_counts",
        )

    def test_pipeline_blocks_have_build_rows(self):
        d = _load_json_or_skip("ci_health.json")
        for side in ("amd", "upstream"):
            block = d[side]
            _assert_has_keys(block, {"builds", "latest_build", "trend"}, f"ci_health.json.{side}")


class TestParityReport:
    def test_top_level_keys(self):
        d = _load_json_or_skip("parity_report.json")
        _assert_has_keys(
            d,
            {"generated_at", "summary", "job_groups", "by_module", "parity_pct",
             "total_tests", "amd_build", "upstream_build"},
            "parity_report.json",
        )

    def test_summary_splits_amd_vs_upstream(self):
        d = _load_json_or_skip("parity_report.json")
        _assert_has_keys(d["summary"], {"amd_only", "upstream_only"}, "parity_report.json.summary")

    def test_job_group_row_shape(self):
        d = _load_json_or_skip("parity_report.json")
        groups = d.get("job_groups", [])
        if not groups:
            pytest.skip("no job_groups present")
        # ``delta`` is only present when both sides ran — it's optional.
        required = {"name", "status", "amd", "upstream", "hardware"}
        for g in groups:
            missing = required - set(g.keys())
            assert not missing, f"job_group {g.get('name')!r} missing {sorted(missing)}"


class TestAnalytics:
    def test_pipelines_present(self):
        d = _load_json_or_skip("analytics.json")
        # Must have at least one of the known pipeline keys.
        assert set(d.keys()) & {"amd-ci", "ci"}, (
            f"analytics.json should have known pipeline slugs, got {list(d.keys())}"
        )

    def test_pipeline_block_schema(self):
        d = _load_json_or_skip("analytics.json")
        for slug, block in d.items():
            _assert_has_keys(
                block,
                {"pipeline", "generated_at", "days", "summary", "builds",
                 "daily_stats", "queue_stats"},
                f"analytics.json[{slug}]",
            )

    def test_build_rows_carry_wall_mins(self):
        d = _load_json_or_skip("analytics.json")
        for slug, block in d.items():
            builds = block.get("builds", [])
            if not builds:
                continue
            row = builds[0]
            for field in ("number", "state", "created_at", "total_jobs", "passed", "failed"):
                assert field in row, f"analytics.json[{slug}].builds[0] missing {field!r}"


class TestGatingExecutiveData:
    def test_gating_nightlies_schema(self):
        d = _load_json_or_skip("gating_nightlies.json")
        _assert_has_keys(d, {"generated_at", "source", "ci", "amd-ci"}, "gating_nightlies.json")
        for slug in ("ci", "amd-ci"):
            block = d[slug]
            _assert_has_keys(block, {"pipeline", "display_name", "builds"}, f"gating_nightlies.json[{slug}]")
            if block["builds"]:
                row = block["builds"][0]
                _assert_has_keys(row, {"number", "created_at", "jobs"}, f"gating_nightlies.json[{slug}].builds[0]")

    def test_gating_targets_schema(self):
        d = _load_json_or_skip("gating_targets.json")
        _assert_has_keys(d, {"generated_at", "source", "summary", "groups"}, "gating_targets.json")
        assert d["summary"]["target_group_count"] == 125
        assert len(d["groups"]) == 125
        first = d["groups"][0]
        _assert_has_keys(
            first,
            {
                "id",
                "label",
                "area",
                "gating_signal",
                "pf_signal",
                "assigned_signal",
                "source_signal",
                "readiness_signal",
                "target_signal",
                "owner",
                "note",
            },
            "gating_targets.json.groups[0]",
        )

    def test_gating_target_candidates_schema(self):
        d = _load_json_or_skip("gating_target_candidates.json")
        _assert_has_keys(
            d,
            {"generated_at", "source", "heuristics", "summary", "rows"},
            "gating_target_candidates.json",
        )
        _assert_has_keys(
            d["summary"],
            {
                "upstream_build",
                "amd_ci_build",
                "canonical_target_count",
                "row_count",
                "canonical_match_count",
                "likely_duplicate_count",
                "new_candidate_count",
                "excluded_count",
                "missing_from_upstream_count",
                "by_decision",
            },
            "gating_target_candidates.json.summary",
        )
        if d["rows"]:
            _assert_has_keys(
                d["rows"][0],
                {"decision", "label", "canonical_key"},
                "gating_target_candidates.json.rows[0]",
            )

    def test_gating_target_candidates_do_not_exclude_default_gpu_queues_as_non_gpu(self):
        d = _load_json_or_skip("gating_target_candidates.json")
        offenders = [
            row
            for row in d.get("rows", [])
            if row.get("decision") == "excluded"
            and "not_gpu_like" in (row.get("exclusion_reasons") or [])
            and re.search(r"(^|[^a-z0-9])gpu_", str(row.get("queue") or ""), re.IGNORECASE)
        ]
        assert offenders == []


class TestAmdTestMatrix:
    def test_top_level_keys(self):
        d = _load_json_or_skip("amd_test_matrix.json")
        _assert_has_keys(
            d,
            {"generated_at", "source", "summary", "architectures", "areas", "rows"},
            "amd_test_matrix.json",
        )

    def test_architecture_row_shape(self):
        d = _load_json_or_skip("amd_test_matrix.json")
        arches = d.get("architectures", [])
        if not arches:
            pytest.skip("amd_test_matrix.json has no architectures")
        _assert_has_keys(
            arches[0],
            {"id", "label", "group_count", "nightly_match_count"},
            "amd_test_matrix.json.architectures[0]",
        )

    def test_summary_has_operational_cell_counts(self):
        d = _load_json_or_skip("amd_test_matrix.json")
        summary = d.get("summary", {})
        for field in (
            "hardware_cells",
            "latest_matched_cells",
            "passing_cells",
            "failing_cells",
            "waiting_cells",
            "unknown_cells",
        ):
            assert field in summary, f"amd_test_matrix.json.summary missing {field!r}"

    def test_group_row_shape(self):
        d = _load_json_or_skip("amd_test_matrix.json")
        rows = d.get("rows", [])
        if not rows:
            pytest.skip("amd_test_matrix.json has no rows")
        _assert_has_keys(
            rows[0],
            {"title", "area", "yaml_order", "coverage_count", "nightly_coverage_count", "cells"},
            "amd_test_matrix.json.rows[0]",
        )


class TestGatingProposals:
    def test_top_level_keys(self):
        d = _load_json_or_skip("gating_proposals.json")
        _assert_has_keys(
            d,
            {"generated_at", "source_repo", "tracked_authors", "summary", "collection", "pull_requests"},
            "gating_proposals.json",
        )

    def test_summary_keys(self):
        d = _load_json_or_skip("gating_proposals.json")
        _assert_has_keys(
            d["summary"],
            {
                "tracked_author_count",
                "scanned_pr_count",
                "proposal_pr_count",
                "proposed_group_count",
                "by_device",
                "by_author",
            },
            "gating_proposals.json.summary",
        )

    def test_candidate_cache_keys(self):
        d = _load_json_or_skip("gating_proposals.json")
        collection = d.get("collection") or {}
        _assert_has_keys(
            collection,
            {"complete", "error_count", "errors", "candidate_cache"},
            "gating_proposals.json.collection",
        )
        cache = collection.get("candidate_cache") or {}
        _assert_has_keys(
            cache,
            {"generated_at", "pr_count", "proposal_pr_numbers", "pull_requests"},
            "gating_proposals.json.collection.candidate_cache",
        )
        if cache["pull_requests"]:
            _assert_has_keys(
                cache["pull_requests"][0],
                {"number", "checked_at", "has_new_mirrors", "new_mirror_count"},
                "gating_proposals.json.collection.candidate_cache.pull_requests[0]",
            )

    def test_pr_and_mirror_rows_if_populated(self):
        d = _load_json_or_skip("gating_proposals.json")
        prs = d.get("pull_requests", [])
        if not prs:
            return
        pr = prs[0]
        _assert_has_keys(
            pr,
            {"number", "title", "url", "author", "head_ref", "updated_at", "new_mirror_count", "new_mirrors"},
            "gating_proposals.json.pull_requests[0]",
        )
        if pr["new_mirrors"]:
            _assert_has_keys(
                pr["new_mirrors"][0],
                {"label", "area", "yaml_file", "device", "source_file_dependencies"},
                "gating_proposals.json.pull_requests[0].new_mirrors[0]",
            )


class TestQueueTimeseries:
    """Append-only JSONL — each line is one queue snapshot."""

    def test_every_line_has_required_keys(self):
        path = DATA / "queue_timeseries.jsonl"
        if not path.exists():
            pytest.skip("queue_timeseries.jsonl not present")
        required = {"ts", "queues", "total_waiting", "total_running"}
        line_count = 0
        with path.open() as f:
            for i, line in enumerate(f, 1):
                if not line.strip():
                    continue
                line_count += 1
                obj = json.loads(line)
                missing = required - set(obj.keys())
                assert not missing, f"line {i} missing keys: {sorted(missing)}"
                assert obj["ts"].endswith("Z"), f"line {i}: ts must be UTC ISO"
        assert line_count > 0, "queue_timeseries.jsonl has no rows"

    def test_populated_queue_rows_have_wait_percentiles(self):
        path = DATA / "queue_timeseries.jsonl"
        if not path.exists():
            pytest.skip("queue_timeseries.jsonl not present")
        required_row = {"waiting", "running", "p50_wait", "p90_wait", "max_wait", "avg_wait"}
        found_populated = False
        with path.open() as f:
            for line in f:
                if not line.strip():
                    continue
                obj = json.loads(line)
                for q, row in obj.get("queues", {}).items():
                    found_populated = True
                    missing = required_row - set(row.keys())
                    assert not missing, f"queue {q!r} row missing {sorted(missing)}"
                if found_populated:
                    break
        # Fresh repos may have only zero-filled snapshots — that's fine.


class TestQueueJobs:
    def test_top_level_keys(self):
        d = _load_json_or_skip("queue_jobs.json")
        _assert_has_keys(d, {"ts", "pending", "running"}, "queue_jobs.json")

    def test_pending_and_running_are_lists(self):
        d = _load_json_or_skip("queue_jobs.json")
        assert isinstance(d["pending"], list)
        assert isinstance(d["running"], list)

    def test_job_row_schema_if_populated(self):
        d = _load_json_or_skip("queue_jobs.json")
        # The dashboard reads name/queue/url; pending rows also need wait_min.
        # workload/branch/commit are forward-compat fields added by the new
        # collector — not yet required until old data ages out.
        required_both = {"name", "queue", "url"}
        for j in d.get("pending", []):
            missing = (required_both | {"wait_min"}) - set(j.keys())
            assert not missing, f"pending job missing {sorted(missing)}: {j.get('name')}"
        for j in d.get("running", []):
            missing = required_both - set(j.keys())
            assert not missing, f"running job missing {sorted(missing)}: {j.get('name')}"


class TestOpenQueueIssues:
    def test_top_level_keys(self):
        d = _load_json_or_skip("open_queue_issues.json")
        _assert_has_keys(d, {"open"}, "open_queue_issues.json")
        assert isinstance(d["open"], dict)

    def test_open_values_are_issue_numbers_or_entries(self):
        d = _load_json_or_skip("open_queue_issues.json")
        for queue, entry in d["open"].items():
            assert isinstance(entry, (int, dict)), (
                f"open_queue_issues.json['open'][{queue!r}] must be an int or dict, got {type(entry).__name__}"
            )
            if isinstance(entry, dict):
                assert isinstance(entry.get("number"), int), (
                    f"open_queue_issues.json['open'][{queue!r}].number must be an int"
                )


class TestOpenQueueZombieIssues:
    def test_top_level_keys(self):
        d = _load_json_or_skip("open_queue_zombie_issues.json")
        _assert_has_keys(d, {"open"}, "open_queue_zombie_issues.json")
        assert isinstance(d["open"], dict)

    def test_open_values_are_issue_numbers_or_entries(self):
        d = _load_json_or_skip("open_queue_zombie_issues.json")
        for queue, entry in d["open"].items():
            assert isinstance(entry, (int, dict)), (
                f"open_queue_zombie_issues.json['open'][{queue!r}] must be an int or dict, got {type(entry).__name__}"
            )
            if isinstance(entry, dict):
                assert isinstance(entry.get("number"), int), (
                    f"open_queue_zombie_issues.json['open'][{queue!r}].number must be an int"
                )


class TestOpenAmdMainFailureIssues:
    def test_state_schema(self):
        d = _load_json_or_skip("open_amd_main_failure_issues.json")
        _assert_has_keys(
            d,
            {
                "schema_version",
                "initialized",
                "processed_build_numbers",
                "active",
                "issue",
                "suppressed",
                "last_fingerprint",
                "last_run",
            },
            "open_amd_main_failure_issues.json",
        )
        assert isinstance(d["initialized"], bool)
        assert isinstance(d["processed_build_numbers"], list)
        assert all(isinstance(number, int) for number in d["processed_build_numbers"])
        assert isinstance(d["active"], dict)
        assert d["issue"] is None or isinstance(d["issue"], dict)


class TestOpenAmdDurationRegressionIssues:
    def test_state_schema(self):
        d = _load_json_or_skip("open_amd_duration_regression_issues.json")
        _assert_has_keys(
            d,
            {
                "schema_version",
                "active",
                "issue",
                "suppressed",
                "last_fingerprint",
                "last_run",
            },
            "open_amd_duration_regression_issues.json",
        )
        assert isinstance(d["active"], dict)
        assert d["issue"] is None or isinstance(d["issue"], dict)


class TestOpenAgentHealthIssues:
    def test_state_schema(self):
        d = _load_json_or_skip("open_agent_health_issues.json")
        _assert_has_keys(
            d,
            {
                "schema_version",
                "issue",
                "suppressed",
                "last_fingerprint",
                "last_run",
            },
            "open_agent_health_issues.json",
        )
        assert d["issue"] is None or isinstance(d["issue"], dict)


class TestConfigParity:
    def test_top_level_keys(self):
        d = _load_json_or_skip("config_parity.json")
        _assert_has_keys(
            d,
            {"summary", "matches", "amd_only", "nvidia_only", "mirrors"},
            "config_parity.json",
        )


class TestFailureTrends:
    def test_top_level_keys(self):
        d = _load_json_or_skip("failure_trends.json")
        _assert_has_keys(
            d,
            {"generated_at", "new_failures", "recently_fixed", "top_offenders",
             "pass_rate_trend", "mttf", "degrading_modules"},
            "failure_trends.json",
        )


class TestFlakyTests:
    def test_top_level_keys(self):
        d = _load_json_or_skip("flaky_tests.json")
        _assert_has_keys(d, {"generated_at", "tests", "total_flaky", "window_builds"}, "flaky_tests.json")


class TestHotness:
    def test_top_level_keys(self):
        d = _load_json_or_skip("hotness.json")
        _assert_has_keys(
            d,
            {"generated_at", "window_hours", "builds_examined", "test_groups", "branches", "queues"},
            "hotness.json",
        )


class TestAllJsonIsValid:
    """Catch-all: every committed *.json in data/vllm/ci/ must parse cleanly."""

    def test_no_corrupt_files(self):
        for path in DATA.glob("*.json"):
            try:
                json.loads(path.read_text())
            except json.JSONDecodeError as e:
                pytest.fail(f"{path.name} is not valid JSON: {e}")
