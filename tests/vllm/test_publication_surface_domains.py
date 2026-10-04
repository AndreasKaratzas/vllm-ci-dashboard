"""Contracts for independently publishable CI data domains."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vllm import publication_surfaces as surfaces_module
from vllm.publication_surfaces import (
    CI_CORE_WATCHER_STATE_PATHS,
    FALLBACK_DEPENDENCIES,
    GLOBAL_DATA_PATHS,
    INDEPENDENT_WATCHER_STATE_PATHS,
    LEGACY_CI_SURFACE,
    LEGACY_CI_SURFACE_SPEC,
    LEGACY_SURFACE_ALIASES,
    CONTEXTUAL_OPERATIONS_FINDING_CODES,
    OPERATIONS_FINDING_SURFACES,
    SOURCE_SURFACES,
    SURFACE_SPECS,
    fallback_dependency_closure,
    finding_surfaces,
    public_manifest_ownership_path,
    surface_for_path,
)


ROOT = Path(__file__).resolve().parents[2]
CI_DOMAINS = frozenset({
    "ci_core",
    "ci_analytics",
    "ci_gating",
    "ci_changes",
    "ci_hotness",
})


def test_every_operations_audit_code_has_explicit_source_lineage() -> None:
    tree = ast.parse(
        (ROOT / "scripts/vllm/audit_dashboard_data.py").read_text()
    )
    audit_codes = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("operations-")
    }

    assert audit_codes == (
        set(OPERATIONS_FINDING_SURFACES)
        | set(CONTEXTUAL_OPERATIONS_FINDING_CODES)
    )
    assert {
        surface
        for surfaces in OPERATIONS_FINDING_SURFACES.values()
        for surface in surfaces
    } <= set(SURFACE_SPECS)


def _finding(
    code: str,
    *,
    path: str = "",
    source: str | None = None,
) -> SimpleNamespace:
    context = {"source": source} if source else {}
    return SimpleNamespace(code=code, path=path, context=context)


def test_active_surfaces_have_unique_ownership_and_cover_public_manifest() -> None:
    assert LEGACY_CI_SURFACE not in SURFACE_SPECS
    assert CI_DOMAINS <= set(SURFACE_SPECS)

    exact_owners: dict[str, str] = {}
    glob_owners: dict[str, str] = {}
    for surface, spec in SURFACE_SPECS.items():
        for path in (*spec.required_paths, *spec.optional_paths):
            assert path not in exact_owners, (
                f"{path} is owned by both {exact_owners[path]} and {surface}"
            )
            exact_owners[path] = surface
            assert surface_for_path(path) == surface
        for pattern in spec.globs:
            assert pattern not in glob_owners, (
                f"{pattern} is owned by both {glob_owners[pattern]} and {surface}"
            )
            glob_owners[pattern] = surface
            assert surface_for_path(pattern.replace("*", "domain-contract")) == surface

    manifest = json.loads((ROOT / "config/public_data_manifest.json").read_text())
    manifest_paths = {
        public_manifest_ownership_path(relative)
        for key in ("required_files", "optional_files", "build_inputs")
        for relative in manifest[key]
    }
    assert {
        path
        for path in manifest_paths
        if surface_for_path(path) is None and path not in GLOBAL_DATA_PATHS
    } == set()
    assert set(SOURCE_SURFACES.values()) <= set(SURFACE_SPECS)

    assert surface_for_path("data/vllm/ci/analytics.json") == "ci_analytics"
    assert surface_for_path("data/vllm/ci/gating_nightlies.json") == "ci_gating"
    assert surface_for_path("data/vllm/ci/gating_targets.json") == "ci_gating"
    assert surface_for_path("data/vllm/ci/group_changes.json") == "ci_changes"
    assert surface_for_path("data/vllm/ci/hotness.json") == "ci_hotness"
    assert surface_for_path("data/vllm/ci/dns_failures.json") == "dns_health"
    assert (
        surface_for_path("data/vllm/ci/test_results/domain-contract.jsonl")
        == "ci_core"
    )
    assert surface_for_path("data/vllm/ci/test_results/retention.json") == "ci_core"


def test_legacy_monolithic_ci_contract_is_exactly_partitioned() -> None:
    expected_required = {
        "data/vllm/ci/amd_test_matrix.json",
        "data/vllm/ci/analytics.json",
        "data/vllm/ci/ci_health.json",
        "data/vllm/ci/config_parity.json",
        "data/vllm/ci/failure_trends.json",
        "data/vllm/ci/flaky_tests.json",
        "data/vllm/ci/gating_nightlies.json",
        "data/vllm/ci/gating_proposals.json",
        "data/vllm/ci/gating_target_candidates.json",
        "data/vllm/ci/gating_targets.json",
        "data/vllm/ci/group_changes.json",
        "data/vllm/ci/hotness.json",
        "data/vllm/ci/ownership_config_parity.json",
        "data/vllm/ci/parity_report.json",
        "data/vllm/ci/shard_bases.json",
        "data/vllm/ci/test_group_parity.json",
        "data/vllm/parity_report.json",
        "data/vllm/test_results.json",
    }
    expected_optional = {
        "data/vllm/ci/ci_ownership.json",
        "data/vllm/ci/open_amd_duration_regression_issues.json",
        "data/vllm/ci/open_amd_main_failure_issues.json",
        "data/vllm/ci/open_ci_area_regression_issues.json",
        "data/vllm/ci/open_ci_main_failure_issues.json",
        "data/vllm/ci/parity_key_overrides.json",
        "data/vllm/ci/quarantine.json",
        "data/vllm/ci/shard_base_catalog.json",
        "data/vllm/ci/test_results/retention.json",
    }

    assert LEGACY_CI_SURFACE == "ci"
    assert LEGACY_SURFACE_ALIASES == {"ci": CI_DOMAINS}
    assert set(LEGACY_CI_SURFACE_SPEC.required_paths) == expected_required
    assert set(LEGACY_CI_SURFACE_SPEC.optional_paths) == expected_optional
    assert LEGACY_CI_SURFACE_SPEC.globs == ("data/vllm/ci/test_results/*.jsonl",)

    partitioned_required = {
        path
        for surface in CI_DOMAINS
        for path in SURFACE_SPECS[surface].required_paths
    }
    partitioned_optional = {
        path
        for surface in CI_DOMAINS
        for path in SURFACE_SPECS[surface].optional_paths
    }
    partitioned_globs = {
        pattern for surface in CI_DOMAINS for pattern in SURFACE_SPECS[surface].globs
    }
    assert partitioned_required == expected_required
    assert partitioned_optional == expected_optional - set(
        CI_CORE_WATCHER_STATE_PATHS
    )
    assert partitioned_optional | set(CI_CORE_WATCHER_STATE_PATHS) == expected_optional
    assert partitioned_globs == set(LEGACY_CI_SURFACE_SPEC.globs)
    assert {
        surface_for_path(path)
        for path in expected_required | partitioned_optional
    } <= CI_DOMAINS


def test_private_watcher_ledgers_have_no_publication_surface_owner() -> None:
    assert INDEPENDENT_WATCHER_STATE_PATHS == {
        "data/vllm/ci/open_agent_health_issues.json",
        "data/vllm/ci/open_amd_duration_regression_issues.json",
        "data/vllm/ci/open_amd_main_failure_issues.json",
        "data/vllm/ci/open_ci_area_regression_issues.json",
        "data/vllm/ci/open_ci_main_failure_issues.json",
    }
    assert all(
        surface_for_path(path) is None
        for path in INDEPENDENT_WATCHER_STATE_PATHS
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        ("data/vllm/ci/ci_health.json", {"ci_core"}),
        ("data/vllm/ci/gating_targets.json", {"ci_gating"}),
        ("data/vllm/ci/group_changes.json", {"ci_changes"}),
        ("data/vllm/ci/hotness.json", {"ci_hotness"}),
        ("data/vllm/ci/queue_timeseries.jsonl", {"queue"}),
        ("data/vllm/ci/capacity_monitor.json", {"queue_capacity"}),
        ("data/vllm/ci/workload_mapping.json", {"queue_workload"}),
        ("data/vllm/ci/omni_surge_heuristic.json", {"queue_omni"}),
        ("data/vllm/ci/dns_failures.json", {"dns_health"}),
    ),
)
def test_path_specific_findings_route_to_the_owning_domain(
    path: str,
    expected: set[str],
) -> None:
    finding = _finding("operations-gating-invalid", path=path, source="analytics")
    assert finding_surfaces(finding) == frozenset(expected)


@pytest.mark.parametrize(
    ("code", "source", "expected"),
    (
        ("operations-stale-source", "analytics", {"ci_analytics"}),
        ("operations-stale-source", "gating_targets", {"ci_gating"}),
        ("operations-stale-source", "queue_timeseries", {"queue"}),
        ("operations-stale-source", "queue_jobs", {"queue"}),
        ("operations-stale-source", "capacity_monitor", {"queue_capacity"}),
        ("operations-stale-source", "workload_mapping", {"queue_workload"}),
        ("operations-stale-source", "omni_heuristic", {"queue_omni"}),
        ("operations-stale-source", "omni_issue_state", {"queue_omni"}),
        ("operations-source-schema", "group_changes", {"ci_changes"}),
        (
            "operations-gating-runtime-resolution",
            None,
            {"ci_core", "ci_gating", "queue_capacity"},
        ),
        (
            "operations-gating-history-source-pipeline",
            None,
            {"ci_analytics", "ci_gating", "queue_capacity"},
        ),
        (
            "operations-active-target-count",
            None,
            {"ci_gating", "queue_capacity"},
        ),
        ("operations-canonical-target-count", None, {"ci_gating"}),
        ("operations-trajectory-scope", None, {"ci_analytics"}),
        (
            "operations-latest-nightly",
            None,
            {"ci_analytics", "ci_core"},
        ),
        ("operations-latest-nightly-ahead", None, {"ci_core"}),
        ("operations-reliability-unavailable", None, {"ci_analytics"}),
        (
            "operations-nightly-retention",
            None,
            {"ci_analytics", "ci_core"},
        ),
        ("operations-platform-comparison-counts", None, {"ci_analytics"}),
        ("operations-platform-comparison-eligibility", None, {"ci_analytics"}),
        ("operations-retry-attempt-count", None, {"ci_analytics"}),
        ("operations-retry-recovery-count", None, {"ci_analytics"}),
        ("operations-retry-links", None, {"ci_analytics"}),
        ("operations-retry-source", None, {"ci_analytics"}),
        ("operations-amd-retained-count", None, {"ci_core"}),
        (
            "operations-bundle-org-summary-source",
            None,
            {"queue_lifecycle"},
        ),
        (
            "operations-bundle-org-summary-scheduled-denominators",
            None,
            {"ci_analytics"},
        ),
        ("definition-parity-command", None, {"ci_core"}),
        ("matrix-summary-mismatch", None, {"ci_core"}),
        ("gating-target-invalid", None, {"ci_gating"}),
        ("analytics-invalid", None, {"ci_analytics"}),
        ("dns-health-invalid", None, {"dns_health"}),
    ),
)
def test_generic_findings_route_to_their_consuming_domains(
    code: str,
    source: str | None,
    expected: set[str],
) -> None:
    assert finding_surfaces(_finding(code, source=source)) == frozenset(expected)


@pytest.mark.parametrize(
    ("code", "expected"),
    (
        (
            "operations-home-payload-budget",
            {
                "ci_analytics",
                "ci_core",
                "queue",
                "queue_capacity",
                "queue_omni",
                "queue_workload",
            },
        ),
        (
            "operations-health-payload-budget",
            {
                "ci_analytics",
                "ci_core",
                "queue",
                "queue_capacity",
                "queue_omni",
                "queue_workload",
            },
        ),
        ("operations-queue-payload-budget", {"queue"}),
        (
            "operations-bundle-org-summary-budget",
            {
                "ci_analytics",
                "ci_core",
                "ci_gating",
                "queue",
                "queue_capacity",
                "queue_lifecycle",
            },
        ),
        (
            "operations-comparison-retry-evidence-payload-budget",
            {"ci_analytics"},
        ),
    ),
)
def test_operations_manifest_budget_findings_reach_their_source_routes(
    code: str,
    expected: set[str],
) -> None:
    finding = _finding(
        code,
        path="data/vllm/ci/operations_v2_manifest.json",
    )
    assert finding_surfaces(finding) == frozenset(expected)


def test_non_data_path_remains_a_global_failure() -> None:
    assert finding_surfaces(
        _finding(
            "operations-gating-inconsistent",
            path="docs/assets/js/ops-v2.js",
        )
    ) == frozenset()


def test_analytics_core_and_gating_fallbacks_are_independent() -> None:
    assert FALLBACK_DEPENDENCIES == {}
    assert fallback_dependency_closure(set()) == frozenset()
    assert fallback_dependency_closure("ci_core") == frozenset({"ci_core"})
    assert fallback_dependency_closure("ci_analytics") == frozenset(
        {"ci_analytics"}
    )
    assert fallback_dependency_closure({"ci_gating"}) == frozenset({"ci_gating"})
    assert fallback_dependency_closure({"ci_changes", "ci_hotness"}) == frozenset(
        {"ci_changes", "ci_hotness"}
    )
    assert fallback_dependency_closure(
        fallback_dependency_closure({"ci_core"})
    ) == frozenset({"ci_core"})


def test_fallback_dependency_closure_supports_multi_hop_graphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        surfaces_module,
        "FALLBACK_DEPENDENCIES",
        {
            "ci_core": frozenset({"ci_gating"}),
            "ci_gating": frozenset({"ci_changes"}),
            "ci_changes": frozenset({"ci_hotness"}),
            "ci_hotness": frozenset({"ci_analytics"}),
            "ci_analytics": frozenset({"ci_core"}),
        },
    )
    assert fallback_dependency_closure({"ci_core"}) == CI_DOMAINS


def test_fallback_dependency_closure_fails_closed_on_unknown_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="unknown publication surfaces:.*ci"):
        fallback_dependency_closure({LEGACY_CI_SURFACE})

    monkeypatch.setattr(
        surfaces_module,
        "FALLBACK_DEPENDENCIES",
        {"ci_core": frozenset({"not_a_surface"})},
    )
    with pytest.raises(ValueError, match="reference unknown surfaces:.*not_a_surface"):
        fallback_dependency_closure({"ci_core"})
