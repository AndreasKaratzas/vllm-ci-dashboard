from __future__ import annotations

import json
from pathlib import Path

import pytest

from vllm.check_dashboard_state_storage_budget import summarize, violations
from vllm.check_git_blob_sizes import TrackedBlob, tracked_blobs
from vllm.dashboard_storage_budget import (
    DEFAULT_CONFIG_PATH,
    StorageBudgetError,
    load_storage_budget,
)


ROOT = Path(__file__).resolve().parents[2]


def test_allocations_compose_below_state_cap_with_headroom() -> None:
    budget = load_storage_budget()

    assert budget.allocated_bytes + budget.required_headroom_bytes <= budget.max_tree_bytes
    assert budget.required_headroom_bytes >= 16 * 1024 * 1024
    assert budget.allocated_bytes == 240 * 1024 * 1024

    groups = budget.groups
    writers = budget.writer_limits
    assert groups["analytics"].max_bytes == 64 * 1024 * 1024
    assert writers["analytics"].max_bytes == 56 * 1024 * 1024
    assert writers["dashboard_state_manifest"].max_bytes == 8 * 1024 * 1024
    assert (
        writers["analytics"].max_bytes
        + writers["dashboard_state_manifest"].max_bytes
        == groups["analytics"].max_bytes
    )
    assert (
        writers["queue_history"].max_bytes
        + writers["queue_history_chart"].max_bytes
        <= groups["queue"].max_bytes
    )
    assert writers["agent_health_generation"].max_bytes == groups["agent_health"].max_bytes
    assert (
        writers["test_result_store"].max_bytes
        + writers["test_result_retention"].max_bytes
        <= groups["test_results"].max_bytes
    )
    assert writers["test_result_shard"].max_bytes < writers["test_result_store"].max_bytes
    assert writers["gating_nightlies"].max_bytes == groups["gating_nightlies"].max_bytes
    assert (
        writers["perf_eval_events"].max_bytes
        + writers["perf_eval_summary"].max_bytes
        == groups["perf_eval"].max_bytes
    )
    assert writers["hotness"].max_bytes == groups["hotness"].max_bytes
    assert writers["workload_mapping"].max_bytes == groups["workload_mapping"].max_bytes
    assert writers["dns_failures"].max_bytes == groups["dns_failures"].max_bytes
    assert writers["queue_details"].max_bytes == groups["queue_details"].max_bytes
    assert (
        writers["queue_lifecycle_summary"].max_bytes
        == groups["queue_lifecycle_summary"].max_bytes
    )
    assert (
        writers["ci_health"].max_bytes
        + writers["ci_parity_pair"].max_bytes
        + writers["failure_trends"].max_bytes
        + writers["flaky_tests"].max_bytes
        <= groups["ci_derived"].max_bytes
    )
    assert (
        writers["amd_test_matrix"].max_bytes
        + writers["config_parity_pair"].max_bytes
        + writers["test_group_parity"].max_bytes
        <= groups["current_definitions"].max_bytes
    )
    assert writers["group_changes"].max_bytes == groups["ci_changes"].max_bytes
    assert (
        writers["gating_proposals"].max_bytes
        + writers["gating_target_candidates"].max_bytes
        + writers["gating_targets"].max_bytes
        == groups["ci_gating_control"].max_bytes
    )
    assert (
        writers["operations_manifest"].max_bytes
        + writers["org_summary"].max_bytes
        + writers["capacity_monitor"].max_bytes
        + writers["ci_ownership"].max_bytes
        == groups["operations_control"].max_bytes
    )
    watcher_state_writers = (
        "ci_main_failure_watcher_state",
        "amd_main_failure_watcher_state",
        "ci_area_regression_watcher_state",
        "amd_duration_regression_watcher_state",
        "agent_health_watcher_state",
        "omni_surge_watcher_state",
        "queue_issue_watcher_state",
        "queue_zombie_watcher_state",
    )
    assert sum(
        writers[name].max_bytes for name in watcher_state_writers
    ) == groups["watcher_state"].max_bytes
    github_home_writers = (
        "github_home_projects",
        "github_home_prs",
        "github_home_issues",
        "github_home_project_items",
        "github_home_releases",
    )
    assert sum(
        writers[name].max_bytes for name in github_home_writers
    ) == groups["github_home"].max_bytes
    operational_misc_writers = (
        "project_test_results",
        "shard_base_catalog",
        "parity_key_overrides",
        "publication_state",
        "shard_bases",
        "omni_surge_heuristic",
        "quarantine_report",
        "last_collected_at",
        "public_projection_attestation",
    )
    assert sum(
        writers[name].max_bytes for name in operational_misc_writers
    ) <= groups["operational_misc"].max_bytes
    assert (
        groups["operational_misc"].max_bytes
        - sum(writers[name].max_bytes for name in operational_misc_writers)
        >= 3 * 1024
    )
    assert budget.unmanaged_max_bytes == 16 * 1024 * 1024
    assert budget.max_files == 10_000


def test_current_staged_tree_fits_every_composition_envelope() -> None:
    budget = load_storage_budget()
    summary = summarize(tracked_blobs(ROOT), budget)

    assert violations(summary, budget) == []
    assert summary.total_bytes <= budget.allocated_bytes
    assert budget.unmanaged_max_bytes - summary.unmanaged_bytes >= 8 * 1024 * 1024


def test_every_current_generated_data_file_has_an_explicit_group() -> None:
    budget = load_storage_budget()
    generated = [
        blob.path
        for blob in tracked_blobs(ROOT)
        if blob.path.startswith("data/")
    ]

    assert generated
    assert {
        path: budget.matching_groups(path)
        for path in generated
        if len(budget.matching_groups(path)) != 1
    } == {}


@pytest.mark.parametrize(
    ("path", "expected_group"),
    [
        ("data/vllm/ci/dashboard_state.json", "analytics"),
        (
            "data/vllm/ci/public_projection_attestation.json",
            "operational_misc",
        ),
    ],
)
def test_generated_state_control_file_has_one_explicit_group(
    path: str,
    expected_group: str,
) -> None:
    budget = load_storage_budget()

    assert budget.matching_groups(path) == (expected_group,)


def test_runtime_writer_caps_match_the_shared_allocation() -> None:
    import collect_ci

    from vllm import (
        build_test_group_parity,
        build_operations_snapshot,
        ci_area_regression_watcher,
        collect_agent_health,
        collect_amd_test_matrix,
        collect_analytics,
        collect_capacity_monitor,
        collect_group_changes,
        collect_gating_proposals,
        collect_gating_target_candidates,
        collect_gating_targets,
        collect_hotness,
        collect_ownership_parity,
        collect_queue_lifecycle,
        collect_queue_snapshot,
        collect_workload_mapping,
        dashboard_state,
        github_home_bundle,
        omni_surge_watcher,
        public_projection,
        select_publication_surfaces,
        write_last_collected_at,
    )
    from vllm.ci import dns_failures, perf_eval_webhook, reporter
    from vllm.constants import QUEUE_HISTORY_MAX_BYTES
    from vllm.operations_bundle_contract import OPERATIONS_MANIFEST_MAX_BYTES

    budget = load_storage_budget()
    writers = budget.writer_limits
    groups = budget.groups

    assert collect_analytics.PRIVATE_ANALYTICS_TARGET_BYTES == writers["analytics"].max_bytes
    assert (
        dashboard_state.MAX_STATE_MANIFEST_BYTES
        == writers["dashboard_state_manifest"].max_bytes
    )
    assert (
        public_projection.MAX_ATTESTATION_BYTES
        == writers["public_projection_attestation"].max_bytes
    )
    assert collect_analytics.GATING_NIGHTLIES_MAX_BYTES == writers["gating_nightlies"].max_bytes
    assert reporter.TEST_RESULT_SHARD_MAX_BYTES == writers["test_result_shard"].max_bytes
    assert reporter.TEST_RESULT_STORE_MAX_BYTES == writers["test_result_store"].max_bytes
    assert (
        reporter.TEST_RESULT_RETENTION_MAX_BYTES
        == writers["test_result_retention"].max_bytes
    )
    assert QUEUE_HISTORY_MAX_BYTES == writers["queue_history"].max_bytes
    assert (
        collect_queue_lifecycle.MAX_SUMMARY_BYTES
        == writers["queue_lifecycle_summary"].max_bytes
    )
    assert (
        build_operations_snapshot.QUEUE_HISTORY_CHART_MAX_BYTES
        == writers["queue_history_chart"].max_bytes
    )
    assert (
        collect_agent_health.AGENT_HEALTH_MAX_GENERATION_BYTES
        == writers["agent_health_generation"].max_bytes
    )
    assert perf_eval_webhook.PERF_EVAL_MAX_BYTES == writers["perf_eval_events"].max_bytes
    assert perf_eval_webhook.PERF_EVAL_MAX_BYTES == writers["perf_eval_summary"].max_bytes
    assert collect_hotness.HOTNESS_MAX_BYTES == writers["hotness"].max_bytes
    assert (
        collect_workload_mapping.WORKLOAD_MAPPING_MAX_BYTES
        == writers["workload_mapping"].max_bytes
    )
    assert reporter.CI_HEALTH_MAX_BYTES == writers["ci_health"].max_bytes
    assert reporter.CI_PARITY_PAIR_MAX_BYTES == writers["ci_parity_pair"].max_bytes
    assert reporter.FAILURE_TRENDS_MAX_BYTES == writers["failure_trends"].max_bytes
    assert reporter.FLAKY_TESTS_MAX_BYTES == writers["flaky_tests"].max_bytes
    assert (
        collect_amd_test_matrix.AMD_TEST_MATRIX_MAX_BYTES
        == writers["amd_test_matrix"].max_bytes
    )
    assert (
        collect_ownership_parity.OWNERSHIP_CONFIG_PARITY_MAX_BYTES * 2
        == writers["config_parity_pair"].max_bytes
    )
    assert (
        build_test_group_parity.TEST_GROUP_PARITY_MAX_BYTES
        == writers["test_group_parity"].max_bytes
    )
    assert (
        collect_group_changes.GROUP_CHANGES_MAX_BYTES
        == writers["group_changes"].max_bytes
    )
    assert dns_failures.PUBLIC_OUTPUT_MAX_BYTES == writers["dns_failures"].max_bytes
    assert (
        collect_queue_snapshot.QUEUE_DETAILS_MAX_BYTES
        == writers["queue_details"].max_bytes
    )
    assert collect_gating_proposals.MAX_OUTPUT_BYTES == writers["gating_proposals"].max_bytes
    assert (
        collect_gating_target_candidates.CANDIDATES_MAX_BYTES
        == writers["gating_target_candidates"].max_bytes
    )
    assert (
        collect_gating_targets.GATING_TARGETS_MAX_BYTES
        == writers["gating_targets"].max_bytes
    )
    assert (
        build_operations_snapshot.ORG_SUMMARY_MAX_BYTES
        == writers["org_summary"].max_bytes
    )
    assert OPERATIONS_MANIFEST_MAX_BYTES == writers["operations_manifest"].max_bytes
    assert (
        collect_capacity_monitor.CAPACITY_MONITOR_MAX_BYTES
        == writers["capacity_monitor"].max_bytes
    )
    assert (
        ci_area_regression_watcher.CI_OWNERSHIP_MAX_BYTES
        == writers["ci_ownership"].max_bytes
    )
    assert (
        collect_ci.PROJECT_TEST_RESULTS_MAX_BYTES
        == writers["project_test_results"].max_bytes
    )
    assert (
        collect_ci.SHARD_BASE_CATALOG_MAX_BYTES
        == writers["shard_base_catalog"].max_bytes
    )
    assert (
        collect_ci.PARITY_KEY_OVERRIDES_MAX_BYTES
        == writers["parity_key_overrides"].max_bytes
    )
    assert collect_ci.SHARD_BASES_MAX_BYTES == writers["shard_bases"].max_bytes
    assert (
        select_publication_surfaces.PUBLICATION_STATE_MAX_BYTES
        == writers["publication_state"].max_bytes
    )
    assert (
        omni_surge_watcher.OMNI_HEURISTIC_MAX_BYTES
        == writers["omni_surge_heuristic"].max_bytes
    )
    assert (
        reporter.QUARANTINE_REPORT_MAX_BYTES
        == writers["quarantine_report"].max_bytes
    )
    assert (
        write_last_collected_at.LAST_COLLECTED_AT_MAX_BYTES
        == writers["last_collected_at"].max_bytes
    )
    assert github_home_bundle.HOME_BUNDLE_MAX_BYTES == groups["github_home"].max_bytes
    assert github_home_bundle.HOME_COMPONENT_MAX_BYTES == {
        "projects": writers["github_home_projects"].max_bytes,
        "prs": writers["github_home_prs"].max_bytes,
        "issues": writers["github_home_issues"].max_bytes,
        "project_items": writers["github_home_project_items"].max_bytes,
        "releases": writers["github_home_releases"].max_bytes,
    }


def test_group_and_unmanaged_overflow_are_reported_exactly() -> None:
    budget = load_storage_budget()
    analytics_limit = budget.groups["analytics"].max_bytes
    blobs = [
        TrackedBlob(
            path="data/vllm/ci/analytics.json",
            object_id="a" * 40,
            size=analytics_limit + 1,
        ),
        TrackedBlob(
            path="docs/unmanaged.bin",
            object_id="b" * 40,
            size=budget.unmanaged_max_bytes + 1,
        ),
    ]

    failures = violations(summarize(blobs, budget), budget)

    assert any("storage group analytics" in failure for failure in failures)
    assert any("unmanaged dashboard-state content" in failure for failure in failures)


def test_file_count_overflow_is_reported_exactly() -> None:
    budget = load_storage_budget()
    blob = TrackedBlob(
        path="docs/unmanaged.bin",
        object_id="b" * 40,
        size=1,
    )
    summary = summarize([blob] * (budget.max_files + 1), budget)

    assert any(
        "files" in failure and "max 10000" in failure
        for failure in violations(summary, budget)
    )


def test_config_rejects_allocations_that_consume_required_headroom(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    state = json.loads((ROOT / "config" / "dashboard_state.json").read_text())
    (config_dir / "dashboard_state.json").write_text(json.dumps(state))
    payload = json.loads(DEFAULT_CONFIG_PATH.read_text())
    payload["unmanaged_max_bytes"] += 1
    candidate = config_dir / "dashboard_state_storage_budget.json"
    candidate.write_text(json.dumps(payload))

    with pytest.raises(StorageBudgetError, match="required state-tree headroom"):
        load_storage_budget(candidate)
