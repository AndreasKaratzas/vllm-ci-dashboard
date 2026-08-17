"""Schema contract tests for committed data files in ``data/vllm/ci/``.

The dashboard JS relies on specific top-level keys and row shapes. If a
collector silently drops a field, these tests fail before the change
hits the dashboard. Files that don't yet exist (e.g., hotness.json on
a fresh clone) are skipped rather than failing.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.live_data

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "vllm" / "ci"
PROJECT_DATA = ROOT / "data" / "vllm"


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


def _assert_percentage(value, path: str) -> float:
    assert isinstance(value, (int, float)) and not isinstance(value, bool), (
        f"{path} must be numeric"
    )
    result = float(value)
    assert math.isfinite(result), f"{path} must be finite"
    assert 0 <= result <= 100, f"{path} must be between 0 and 100"
    return result


def _require_pass_rate_contract_v1(version, path: str) -> None:
    if version is None:
        pytest.skip(f"{path} is an unversioned legacy payload")
    assert version == 1, f"{path}.pass_rate_contract_version must be 1"


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

    def test_build_rows_have_explicit_assertion_pass_rates(self):
        d = _load_json_or_skip("ci_health.json")
        _require_pass_rate_contract_v1(
            d.get("pass_rate_contract_version"),
            "ci_health.json",
        )
        for side in ("amd", "upstream"):
            block = d[side]
            rows = [
                (key, block.get(key))
                for key in (
                    "latest_build",
                    "latest_test_signal_build",
                    "latest_pipeline_build",
                )
            ]
            rows.extend(
                (f"builds[{index}]", row)
                for index, row in enumerate(block.get("builds", []))
            )
            for name, row in rows:
                if not row:
                    continue
                path = f"ci_health.json.{side}.{name}"
                _assert_has_keys(
                    row,
                    {
                        "passed",
                        "failed",
                        "skipped",
                        "pass_rate",
                        "test_pass_rate_pct",
                        "test_pass_rate_basis",
                    },
                    path,
                )
                pct = _assert_percentage(row["test_pass_rate_pct"], f"{path}.test_pass_rate_pct")
                assert row["test_pass_rate_basis"] == "pytest_assertions_excluding_skipped"
                ran = row["passed"] + row["failed"]
                expected = round(row["passed"] / ran * 100, 2) if ran else 0.0
                assert pct == expected, f"{path} pass rate must exclude skipped assertions"
                assert pct == pytest.approx(row["pass_rate"] * 100, abs=0.0050001), (
                    f"{path} explicit percentage disagrees with legacy ratio"
                )


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

    def test_summaries_have_explicit_terminal_build_pass_rates(self):
        d = _load_json_or_skip("analytics.json")
        checked = 0
        for slug, block in d.items():
            version = block.get("pass_rate_contract_version")
            if version is None:
                continue
            assert version == 1, (
                f"analytics.json[{slug}].pass_rate_contract_version must be 1"
            )
            checked += 1
            summaries = [("summary", block["summary"])]
            summaries.extend(
                (f"windows[{window!r}].summary", window_data["summary"])
                for window, window_data in block.get("windows", {}).items()
                if "summary" in window_data
            )
            for name, summary in summaries:
                path = f"analytics.json[{slug}].{name}"
                _assert_has_keys(
                    summary,
                    {
                        "passed",
                        "terminal_builds",
                        "pass_rate",
                        "build_pass_rate_pct",
                        "build_pass_rate_basis",
                    },
                    path,
                )
                pct = _assert_percentage(
                    summary["build_pass_rate_pct"],
                    f"{path}.build_pass_rate_pct",
                )
                assert summary["build_pass_rate_basis"] == "terminal_build_state_all_green"
                terminal = summary["terminal_builds"]
                expected = round(summary["passed"] / terminal * 100, 1) if terminal else 0.0
                assert pct == expected
                assert pct == summary["pass_rate"], (
                    f"{path} explicit percentage disagrees with legacy percentage"
                )
        if not checked:
            pytest.skip("analytics.json contains only unversioned legacy payloads")


class TestProjectTestResults:
    def test_platform_summaries_have_explicit_assertion_pass_rates(self):
        path = PROJECT_DATA / "test_results.json"
        if not path.exists():
            pytest.skip("test_results.json not present in this checkout")
        data = json.loads(path.read_text())
        _require_pass_rate_contract_v1(
            data.get("pass_rate_contract_version"),
            "test_results.json",
        )
        for platform in ("rocm", "cuda"):
            if platform not in data:
                continue
            summary = data[platform]["summary"]
            label = f"test_results.json.{platform}.summary"
            _assert_has_keys(
                summary,
                {
                    "pass_rate",
                    "test_assertions",
                    "test_pass_rate_pct",
                    "test_pass_rate_basis",
                },
                label,
            )
            assertions = summary["test_assertions"]
            _assert_has_keys(
                assertions,
                {"total", "passed", "failed", "skipped"},
                f"{label}.test_assertions",
            )
            assert assertions["total"] == (
                assertions["passed"] + assertions["failed"] + assertions["skipped"]
            )
            pct = _assert_percentage(
                summary["test_pass_rate_pct"],
                f"{label}.test_pass_rate_pct",
            )
            assert summary["test_pass_rate_basis"] == "pytest_assertions_excluding_skipped"
            ran = assertions["passed"] + assertions["failed"]
            expected = round(assertions["passed"] / ran * 100, 1) if ran else 0.0
            assert pct == expected
            assert pct == summary["pass_rate"], (
                f"{label} explicit percentage disagrees with legacy percentage"
            )


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
        groups = d["groups"]
        assert groups
        assert d["summary"]["target_group_count"] == len(groups)
        assert len({row["id"] for row in groups}) == len(groups)
        first = groups[0]
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
            {
                "generated_at",
                "source",
                "summary",
                "architectures",
                "areas",
                "best_hardware_policy",
                "health_groups",
                "rows",
            },
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

    def test_best_hardware_health_group_contract(self):
        d = _load_json_or_skip("amd_test_matrix.json")
        summary = d["summary"]
        policy = summary["health_policies"]["best_hardware"]
        groups = d["health_groups"]
        _assert_has_keys(
            policy,
            {
                "health_group_count",
                "included_groups",
                "passing_groups",
                "failing_groups",
                "waiting_groups",
                "unknown_groups",
                "generic_group_count",
                "mi355_sensitive_group_count",
                "pass_percentage",
                "group_ids",
                "status_rule",
                "denominator_rule",
            },
            "amd_test_matrix.json.summary.health_policies.best_hardware",
        )
        assert summary["health_group_count"] == len(groups)
        assert policy["health_group_count"] == len(groups)
        assert policy["included_groups"] == len(groups)
        assert policy["group_ids"] == [group["id"] for group in groups]
        assert len(set(policy["group_ids"])) == len(groups)

        status_counts = {
            status: sum(group["status"] == status for group in groups)
            for status in ("passing", "failed", "waiting", "unknown")
        }
        assert policy["passing_groups"] == status_counts["passing"]
        assert policy["failing_groups"] == status_counts["failed"]
        assert policy["waiting_groups"] == status_counts["waiting"]
        assert policy["unknown_groups"] == status_counts["unknown"]
        assert policy["generic_group_count"] == sum(
            group["gate_kind"] == "generic_best_hardware" for group in groups
        )
        assert policy["mi355_sensitive_group_count"] == sum(
            group["gate_kind"] == "mi355_sensitive" for group in groups
        )

        for index, group in enumerate(groups):
            _assert_has_keys(
                group,
                {
                    "id",
                    "title",
                    "status",
                    "is_passing",
                    "gate_kind",
                    "classification_reason",
                    "architectures",
                    "member_row_ids",
                    "members",
                },
                f"amd_test_matrix.json.health_groups[{index}]",
            )
            assert group["members"]
            for member_index, member in enumerate(group["members"]):
                _assert_has_keys(
                    member,
                    {
                        "row_id",
                        "title",
                        "architecture",
                        "label",
                        "state",
                        "optional",
                        "agent_pool",
                        "agent_pools",
                        "command_fingerprint",
                        "commands",
                        "source_url",
                        "url",
                        "latest_url",
                        "latest_matched",
                        "build_number",
                        "variants",
                    },
                    (
                        "amd_test_matrix.json.health_groups"
                        f"[{index}].members[{member_index}]"
                    ),
                )

    def test_mi355_gate_classification_covers_the_source_inventory(self):
        d = _load_json_or_skip("amd_test_matrix.json")
        policy = d["best_hardware_policy"]
        classifications = policy["mi355_classification"]
        mi355 = next(
            architecture
            for architecture in d["architectures"]
            if architecture["id"] == "mi355"
        )
        assert len(classifications) == mi355["group_count"]
        assert len({row["row_id"] for row in classifications}) == len(
            classifications
        )
        counts = {
            kind: sum(row["classification"] == kind for row in classifications)
            for kind in ("separate_gate", "generic_replica")
        }
        best = d["summary"]["health_policies"]["best_hardware"]
        assert counts["separate_gate"] == best["mi355_sensitive_group_count"]
        assert sum(counts.values()) == len(classifications)

        if d["source"].get("latest_build_number") == 11994:
            assert (best["passing_groups"], best["included_groups"]) == (156, 161)
            assert counts == {"separate_gate": 15, "generic_replica": 25}
            by_label = {row["label"]: row["classification"] for row in classifications}
            for label in (
                "Quantized Models Test",
                "Quantization",
                "Kernels MLA (MI355)",
                "Kernels Attention Test",
                "Kernels MoE Test",
                "Kernels Quantization Test",
                "Kernels FP8 MoE Test (2xH100-2xMI355)",
                "V1 attention (B200-MI355)",
            ):
                assert by_label[label] == "separate_gate"
            for label in (
                "Entrypoints Integration (API Server OpenAI - Part 1)",
                "LM Eval Small Models (2xB200-2xMI355)",
                "Language Models Test (Extended Generation)",
            ):
                assert by_label[label] == "generic_replica"


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


class TestQueueLifecycle:
    TARGET_QUEUES = [
        f"amd_mi{family}_{width}"
        for family in (250, 300, 355)
        for width in (1, 2, 4, 8)
    ]

    def test_top_level_and_exact_scope(self):
        d = _load_json_or_skip("queue_lifecycle.json")
        _assert_has_keys(
            d,
            {
                "schema_version",
                "generated_at",
                "window",
                "scope",
                "totals",
                "queues",
                "hourly",
                "coverage",
                "provenance",
                "retention",
            },
            "queue_lifecycle.json",
        )
        assert d["schema_version"] == 1
        assert d["scope"]["queues"] == self.TARGET_QUEUES
        assert set(d["queues"]) == set(self.TARGET_QUEUES)
        assert isinstance(d["coverage"].get("complete"), bool)

    def test_event_counts_and_distributions_are_typed(self):
        d = _load_json_or_skip("queue_lifecycle.json")
        blocks = [d["totals"], *d["queues"].values()]
        count_fields = {
            "incoming",
            "served",
            "completed",
            "passed",
            "failed",
            "soft_failed",
            "canceled",
            "timed_out",
            "expired",
            "broken",
            "skipped",
            "other_outcomes",
            "retry_attempts_completed",
            "retried_jobs_completed",
        }
        for index, block in enumerate(blocks):
            _assert_has_keys(
                block,
                count_fields | {"queue_wait_seconds", "runtime_seconds"},
                f"queue_lifecycle metric block {index}",
            )
            for field in count_fields:
                assert isinstance(block[field], int) and block[field] >= 0
            assert block["completed"] == sum(
                block[field]
                for field in (
                    "passed",
                    "failed",
                    "soft_failed",
                    "canceled",
                    "timed_out",
                    "expired",
                    "broken",
                    "skipped",
                    "other_outcomes",
                )
            )
            for field in ("queue_wait_seconds", "runtime_seconds"):
                distribution = block[field]
                _assert_has_keys(
                    distribution,
                    {"count", "min", "p50", "p95", "max", "avg"},
                    f"queue_lifecycle metric block {index}.{field}",
                )
                assert isinstance(distribution["count"], int)
                assert distribution["count"] >= 0

    def test_two_hour_window_is_exact(self):
        d = _load_json_or_skip("queue_lifecycle.json")
        window = d["window"]
        assert window["hours"] == 2
        start = _parse_utc(window["start"])
        end = _parse_utc(window["end_exclusive"])
        assert end - start == timedelta(hours=2)


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


class TestOpenCiMainFailureIssues:
    def test_state_schema(self):
        d = _load_json_or_skip("open_ci_main_failure_issues.json")
        _assert_has_keys(
            d,
            {
                "schema_version",
                "initialized",
                "processed_build_numbers",
                "group_watermarks",
                "active",
                "issue",
                "suppressed",
                "last_fingerprint",
                "last_run",
            },
            "open_ci_main_failure_issues.json",
        )
        assert isinstance(d["initialized"], bool)
        assert isinstance(d["processed_build_numbers"], list)
        assert all(isinstance(number, int) for number in d["processed_build_numbers"])
        assert isinstance(d["group_watermarks"], dict)
        assert isinstance(d["active"], dict)
        assert d["issue"] is None or isinstance(d["issue"], dict)
        for group_id, row in d["active"].items():
            assert isinstance(group_id, str) and group_id
            _assert_has_keys(
                row,
                {
                    "good_commit",
                    "bad_commit",
                    "commit_range_status",
                    "compare_url",
                    "bisect_command",
                },
                f"open_ci_main_failure_issues.json.active[{group_id!r}]",
            )


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

    def test_identity_family_summary_and_rows_reconcile(self):
        d = _load_json_or_skip("config_parity.json")
        summary = d.get("summary", {})
        family_summary_fields = {
            "amd_identity_families",
            "covered_identity_families",
            "amd_only_identity_families",
            "partially_covered_identity_families",
            "identity_family_replica_rows",
            "identity_family_coverage_rate_pct",
        }
        _assert_has_keys(
            summary,
            family_summary_fields,
            "config_parity.json.summary",
        )

        covered_rows = [
            *d.get("matches", []),
            *d.get("inline_mirror_variants", []),
            *d.get("additional_variants", []),
        ]
        amd_only_rows = d.get("amd_only", [])
        all_rows = [*covered_rows, *amd_only_rows]
        for index, row in enumerate(all_rows):
            key = row.get("amd_identity_family_key")
            assert isinstance(key, str) and key.strip(), (
                f"config_parity.json AMD row {index} lacks a non-empty "
                "amd_identity_family_key"
            )

        covered_keys = {
            row["amd_identity_family_key"].strip()
            for row in covered_rows
        }
        amd_only_member_keys = {
            row["amd_identity_family_key"].strip()
            for row in amd_only_rows
        }
        all_keys = covered_keys | amd_only_member_keys
        expected_counts = {
            "amd_identity_families": len(all_keys),
            "covered_identity_families": len(covered_keys),
            "amd_only_identity_families": len(amd_only_member_keys - covered_keys),
            "partially_covered_identity_families": len(
                covered_keys & amd_only_member_keys
            ),
            "identity_family_replica_rows": len(all_rows) - len(all_keys),
        }
        for field, expected in expected_counts.items():
            assert summary[field] == expected, (
                f"config_parity.json.summary.{field}={summary[field]!r}; "
                f"published family keys imply {expected}"
            )

        expected_rate = (
            round(len(covered_keys) / len(all_keys) * 100, 1)
            if all_keys
            else 0.0
        )
        assert summary["identity_family_coverage_rate_pct"] == pytest.approx(
            expected_rate,
            abs=0.05,
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


class TestDnsFailures:
    def test_exact_top_level_and_window_contract(self):
        d = _load_json_or_skip("dns_failures.json")
        top_level_keys = {
            "schema_version",
            "generated_at",
            "retention",
            "default_window",
            "window_options",
            "count_basis",
            "scope",
            "classifier",
            "coverage",
            "windows",
            "evidence",
        }
        outcome_contract = d.get("outcome_contract")
        if outcome_contract is None:
            assert set(d) == top_level_keys
        else:
            assert outcome_contract == "dns-job-outcomes-v1"
            assert set(d) == top_level_keys | {"outcome_contract"}
        assert d["schema_version"] == 1
        assert d["retention"]["hours"] == 720
        assert d["default_window"] == "24h"
        expected_ids = ["1h", "3h", "12h", "24h", "72h", "168h", "720h"]
        assert [option["id"] for option in d["window_options"]] == expected_ids
        assert set(d["windows"]) == set(expected_ids)
        assert d["coverage"]["status"] in {
            "not_collected",
            "partial",
            "complete",
        }
        totals_keys = {
            "affected_jobs",
            "episodes",
            "huggingface_affected_jobs",
            "queues",
            "nodes",
            "evidence_total",
        }
        row_keys = {
            "queue",
            "node",
            "hardware",
            "affected_jobs",
            "episodes",
            "huggingface_affected_jobs",
            "evidence_total",
        }
        outcome_fields = {
            "passed_jobs",
            "soft_failed_jobs",
            "hard_failed_jobs",
        }
        if outcome_contract is not None:
            totals_keys |= outcome_fields
            row_keys |= outcome_fields
        for window_id in expected_ids:
            window = d["windows"][window_id]
            assert set(window) == {
                "start",
                "end_exclusive",
                "coverage",
                "totals",
                "rows",
            }
            assert window["coverage"]["status"] in {
                "not_collected",
                "partial",
                "complete",
            }
            totals = window["totals"]
            assert set(totals) == totals_keys
            for field in totals_keys:
                assert type(totals[field]) is int and totals[field] >= 0
            assert isinstance(window["rows"], list)
            for row in window["rows"]:
                assert set(row) == row_keys
                for field in totals_keys - {"queues", "nodes"}:
                    assert type(row[field]) is int and row[field] >= 0
            if outcome_contract is not None:
                assert sum(totals[field] for field in outcome_fields) == totals[
                    "affected_jobs"
                ]
                assert all(
                    sum(row[field] for field in outcome_fields)
                    == row["affected_jobs"]
                    for row in window["rows"]
                )
                assert all(
                    totals[field] == sum(row[field] for row in window["rows"])
                    for field in outcome_fields
                )

    def test_public_payload_has_no_log_or_url_fields(self):
        d = _load_json_or_skip("dns_failures.json")
        serialized = json.dumps(d).lower()
        for forbidden in (
            "raw_log",
            "log_url",
            "log_snippet",
            "authorization",
            "bearer ",
            "https://",
            "http://",
            "bkua_",
        ):
            assert forbidden not in serialized
        assert set(d["evidence"]) == {
            "evidence_total",
            "shown",
            "truncated",
            "items",
        }
        for item in d["evidence"]["items"]:
            assert set(item) == {
                "id",
                "first_at",
                "last_at",
                "time_basis",
                "pipeline",
                "queue",
                "node",
                "hardware",
                "build_number",
                "job_id",
                "state",
                "episodes",
                "match_count",
                "signature_ids",
                "target_categories",
                "window_ids",
                "window_metrics",
            }
            assert list(item["window_metrics"]) == item["window_ids"]
            for metric in item["window_metrics"].values():
                assert set(metric) == {
                    "first_at",
                    "last_at",
                    "episodes",
                    "match_count",
                    "signature_ids",
                    "target_categories",
                }


class TestAllJsonIsValid:
    """Catch-all: every committed *.json in data/vllm/ci/ must parse cleanly."""

    def test_no_corrupt_files(self):
        for path in DATA.glob("*.json"):
            try:
                json.loads(path.read_text())
            except json.JSONDecodeError as e:
                pytest.fail(f"{path.name} is not valid JSON: {e}")
