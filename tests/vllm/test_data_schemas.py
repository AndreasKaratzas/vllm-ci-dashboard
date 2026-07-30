"""Schema contract tests for committed data files in ``data/vllm/ci/``.

The dashboard JS relies on specific top-level keys and row shapes. If a
collector silently drops a field, these tests fail before the change
hits the dashboard. Files that don't yet exist (e.g., hotness.json on
a fresh clone) are skipped rather than failing.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
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


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, f"{value!r} must include a timezone"
    return parsed.astimezone(timezone.utc)


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


class TestWorkloadMapping:
    INTEGER_FIELDS = (
        "mapped_jobs", "started_jobs", "finished_jobs", "mapped_gpu_slots",
    )
    REPOSITORY_LABELS = {
        "omni": "vllm-project/vllm-omni",
        "main": "vllm-project/vllm",
    }

    def _assert_aggregate(self, aggregate, path, queues, pipelines):
        _assert_has_keys(
            aggregate,
            {*self.INTEGER_FIELDS, "gpu_hours", "by_queue", "by_pipeline"},
            path,
        )
        for field in self.INTEGER_FIELDS:
            assert isinstance(aggregate[field], int) and aggregate[field] >= 0
        assert aggregate["started_jobs"] <= aggregate["mapped_jobs"]
        assert aggregate["finished_jobs"] <= aggregate["mapped_jobs"]
        assert aggregate["mapped_gpu_slots"] >= aggregate["mapped_jobs"]
        assert isinstance(aggregate["gpu_hours"], (int, float))
        assert aggregate["gpu_hours"] >= 0

        for dimension, allowlist in (
            ("by_queue", queues),
            ("by_pipeline", pipelines),
        ):
            breakdown = aggregate[dimension]
            assert isinstance(breakdown, dict)
            assert set(breakdown) <= allowlist
            for name, stats in breakdown.items():
                _assert_has_keys(
                    stats,
                    {*self.INTEGER_FIELDS, "gpu_hours"},
                    f"{path}.{dimension}[{name}]",
                )
            for field in self.INTEGER_FIELDS:
                assert aggregate[field] == sum(
                    int(stats[field]) for stats in breakdown.values()
                ), f"{path}.{dimension} does not sum to {field}"

    def _assert_rows(self, d, collection, key):
        rows = d[collection]
        assert isinstance(rows, list) and rows
        keys = [row[key] for row in rows]
        assert keys == sorted(set(keys))
        queues = set(d["scope"]["queues"])
        pipeline_map = d["scope"]["workload_pipelines"]

        for row in rows:
            path = f"workload_mapping.json.{collection}[{row.get(key)}]"
            _assert_has_keys(
                row,
                {
                    key, "end_exclusive", "observed_through", "state",
                    "open", "partial", "complete", "collection_complete",
                    "lower_bound", "workloads",
                },
                path,
            )
            assert row["state"] in {"open", "closed", "partial"}
            assert row["lower_bound"] is (not row["collection_complete"])
            assert row["complete"] is (
                not row["open"] and row["collection_complete"]
            )
            assert row["partial"] is (
                row["open"] or not row["collection_complete"]
            )
            expected_state = (
                "open" if row["open"]
                else ("closed" if row["collection_complete"] else "partial")
            )
            assert row["state"] == expected_state
            assert _parse_utc(row["observed_through"]) <= _parse_utc(
                row["end_exclusive"]
            )
            assert set(row["workloads"]) == {"omni", "main"}
            for workload in ("omni", "main"):
                self._assert_aggregate(
                    row["workloads"][workload],
                    f"{path}.workloads.{workload}",
                    queues,
                    set(pipeline_map[workload]),
                )
        return rows

    def test_top_level_and_scope_schema(self):
        d = _load_json_or_skip("workload_mapping.json")
        _assert_has_keys(
            d,
            {
                "schema_version",
                "generated_at",
                "collection_start",
                "timezone",
                "repositories",
                "retention",
                "coverage",
                "window",
                "scope",
                "semantics",
                "query",
                "totals",
                "hourly",
                "daily",
            },
            "workload_mapping.json",
        )
        assert d["schema_version"] == 2
        assert d["timezone"] == "UTC"
        assert d["retention"]["hourly_days"] >= 7
        assert d["retention"]["daily_days"] >= 90
        assert set(d["repositories"]) == {"omni", "main"}
        _assert_has_keys(
            d["scope"],
            {"queues", "excluded_queue_classes", "workload_pipelines", "repositories"},
            "workload_mapping.json.scope",
        )
        queues = d["scope"]["queues"]
        assert isinstance(queues, list) and queues
        assert len(queues) == len(set(queues))
        assert all(queue.startswith("amd_") for queue in queues)
        assert not any("perf_eval" in queue for queue in queues)
        assert "perf_eval" in d["scope"]["excluded_queue_classes"]
        pipelines = d["scope"]["workload_pipelines"]
        assert all(
            isinstance(pipelines.get(workload), list) and pipelines[workload]
            for workload in ("omni", "main")
        )
        for workload, label in self.REPOSITORY_LABELS.items():
            repository = d["repositories"][workload]
            _assert_has_keys(
                repository, {"label", "pipelines"},
                f"workload_mapping.json.repositories.{workload}",
            )
            assert repository["label"] == label
            assert repository["pipelines"] == pipelines[workload]
            assert d["scope"]["repositories"][workload] == repository

    def test_hourly_and_daily_ranges_match_declared_coverage(self):
        d = _load_json_or_skip("workload_mapping.json")
        generated = _parse_utc(d["generated_at"])
        hourly = self._assert_rows(d, "hourly", "hour")
        daily = self._assert_rows(d, "daily", "date")

        assert len(hourly) >= d["retention"]["hourly_days"] * 24 + 1
        assert len(daily) >= d["retention"]["daily_days"]
        current_hour = generated.replace(minute=0, second=0, microsecond=0)
        assert _parse_utc(hourly[-1]["hour"]) == current_hour
        assert daily[-1]["date"] == generated.date().isoformat()
        assert hourly[-1]["state"] == daily[-1]["state"] == "open"
        assert hourly[-1]["observed_through"] == d["generated_at"]
        assert daily[-1]["observed_through"] == d["generated_at"]

        for collection, rows, key in (
            ("hourly", hourly, "hour"),
            ("daily", daily, "date"),
        ):
            coverage = d["coverage"][collection]
            _assert_has_keys(
                coverage,
                {
                    "resolution", "retention_days", "start", "end_exclusive",
                    "observed_through", "bucket_count", "expected_bucket_count",
                    "missing_bucket_count", "contiguous",
                    "collection_complete", "has_open_bucket",
                },
                f"workload_mapping.json.coverage.{collection}",
            )
            first_start = (
                rows[0][key] if key == "hour"
                else f"{rows[0][key]}T00:00:00Z"
            )
            assert coverage["start"] == first_start
            assert coverage["end_exclusive"] == rows[-1]["end_exclusive"]
            assert coverage["observed_through"] == rows[-1]["observed_through"]
            assert coverage["bucket_count"] == len(rows)
            missing = coverage["expected_bucket_count"] - len(rows)
            assert coverage["missing_bucket_count"] == missing
            assert coverage["contiguous"] is (missing == 0)
            assert coverage["collection_complete"] is (
                coverage["contiguous"]
                and all(row["collection_complete"] for row in rows)
            )
            assert coverage["has_open_bucket"] is any(row["open"] for row in rows)

        assert (
            _parse_utc(hourly[-1]["end_exclusive"])
            - _parse_utc(hourly[0]["hour"])
            >= timedelta(days=d["retention"]["hourly_days"])
        )

    def test_window_truth_is_independent_of_open_daily_bucket(self):
        d = _load_json_or_skip("workload_mapping.json")
        window = d["window"]
        _assert_has_keys(
            window,
            {
                "days", "start_date", "end_date", "start",
                "observed_through", "state", "complete",
                "collection_complete", "lower_bound",
            },
            "workload_mapping.json.window",
        )
        assert window["days"] == 14
        assert window["state"] == "open"
        assert window["observed_through"] == d["generated_at"]
        assert window["lower_bound"] is (not window["collection_complete"])
        assert window["complete"] is window["collection_complete"]
        rows = [
            row for row in d["daily"]
            if window["start_date"] <= row["date"] <= window["end_date"]
        ]
        assert len(rows) == window["days"]
        assert window["collection_complete"] is all(
            row["collection_complete"] for row in rows
        )
        assert rows[-1]["open"] is True
        assert rows[-1]["complete"] is False

    def test_totals_match_the_declared_window(self):
        d = _load_json_or_skip("workload_mapping.json")
        window = d["window"]
        rows = [
            row
            for row in d["daily"]
            if window["start_date"] <= row["date"] <= window["end_date"]
        ]
        queue_allowlist = set(d["scope"]["queues"])
        pipelines = d["scope"]["workload_pipelines"]

        for workload in ("omni", "main"):
            total = d["totals"][workload]
            self._assert_aggregate(
                total, f"workload_mapping.json.totals.{workload}",
                queue_allowlist, set(pipelines[workload]),
            )
            for field in self.INTEGER_FIELDS:
                assert total[field] == sum(
                    int(row["workloads"][workload][field]) for row in rows
                )
            assert total["gpu_hours"] == pytest.approx(
                sum(float(row["workloads"][workload]["gpu_hours"]) for row in rows),
                abs=0.02,
            )

    def test_query_is_bounded_and_contains_no_raw_records(self):
        d = _load_json_or_skip("workload_mapping.json")
        query = d["query"]
        _assert_has_keys(
            query,
            {
                "start", "end_exclusive", "build_created_start",
                "bounded_slice", "pipeline_sources", "diagnostics",
            },
            "workload_mapping.json.query",
        )
        assert query["bounded_slice"] == "UTC day"
        assert query["end_exclusive"] == d["generated_at"]
        assert query["pipeline_sources"]

        for source in query["pipeline_sources"]:
            _assert_has_keys(
                source,
                {
                    "pipeline", "workload", "repository", "start",
                    "end_exclusive", "bounded_slice", "slice_count",
                    "complete", "truncated", "error_types", "slices",
                },
                "workload_mapping.json.query.pipeline_sources[]",
            )
            workload = source["workload"]
            assert source["repository"] == self.REPOSITORY_LABELS[workload]
            assert source["pipeline"] in d["scope"]["workload_pipelines"][workload]
            assert source["slice_count"] == len(source["slices"])
            assert source["complete"] is all(
                row["complete"] for row in source["slices"]
            )
            for row in source["slices"]:
                assert (
                    _parse_utc(row["end_exclusive"]) - _parse_utc(row["start"])
                    <= timedelta(days=1)
                )

        serialized = json.dumps(d)
        assert '"jobs":' not in serialized
        assert '"job_id":' not in serialized


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


class TestCiOwnership:
    def test_status_schema(self):
        d = _load_json_or_skip("ci_ownership.json")
        _assert_has_keys(
            d,
            {
                "schema_version",
                "generated_at",
                "available",
                "policy",
                "availability",
                "ci_lead",
                "summary",
                "areas",
                "unmapped_targets",
            },
            "ci_ownership.json",
        )
        assert d["schema_version"] == 1
        assert isinstance(d["areas"], list)
        assert all(
            {"area", "source_file", "owners", "counts", "regressions", "issue"}
            <= set(row)
            for row in d["areas"]
        )
        assert all(len(row["owners"]) == 3 for row in d["areas"])
        assert not any(
            "availability" in owner
            for row in d["areas"]
            for owner in row["owners"]
        )
        assert not any("@" in json.dumps(row) for row in d["areas"])

    def test_managed_issue_state_schema(self):
        d = _load_json_or_skip("open_ci_area_regression_issues.json")
        _assert_has_keys(
            d,
            {"schema_version", "areas", "last_run"},
            "open_ci_area_regression_issues.json",
        )
        assert d["schema_version"] == 1
        assert isinstance(d["areas"], dict)


class TestConfigParity:
    def test_top_level_keys(self):
        d = _load_json_or_skip("config_parity.json")
        _assert_has_keys(
            d,
            {
                "summary",
                "matches",
                "inline_mirror_variants",
                "additional_variants",
                "amd_only",
                "nvidia_only",
                "mirrors",
            },
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
