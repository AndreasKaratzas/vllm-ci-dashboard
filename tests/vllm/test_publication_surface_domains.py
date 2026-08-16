"""Contracts for independently publishable CI data domains."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vllm import publication_surfaces as surfaces_module
from vllm.publication_surfaces import (
    FALLBACK_DEPENDENCIES,
    GLOBAL_DATA_PATHS,
    LEGACY_CI_SURFACE,
    LEGACY_CI_SURFACE_SPEC,
    LEGACY_SURFACE_ALIASES,
    SOURCE_SURFACES,
    SURFACE_SPECS,
    fallback_dependency_closure,
    finding_surfaces,
    public_manifest_ownership_path,
    surface_for_path,
)


ROOT = Path(__file__).resolve().parents[2]
CI_DOMAINS = frozenset({"ci_core", "ci_gating", "ci_changes", "ci_hotness"})


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

    assert surface_for_path("data/vllm/ci/analytics.json") == "ci_core"
    assert surface_for_path("data/vllm/ci/gating_targets.json") == "ci_gating"
    assert surface_for_path("data/vllm/ci/group_changes.json") == "ci_changes"
    assert surface_for_path("data/vllm/ci/hotness.json") == "ci_hotness"
    assert (
        surface_for_path("data/vllm/ci/test_results/domain-contract.jsonl")
        == "ci_core"
    )


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
    assert partitioned_optional == expected_optional
    assert partitioned_globs == set(LEGACY_CI_SURFACE_SPEC.globs)
    assert {surface_for_path(path) for path in expected_required | expected_optional} <= (
        CI_DOMAINS
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        ("data/vllm/ci/ci_health.json", {"ci_core"}),
        ("data/vllm/ci/gating_targets.json", {"ci_gating"}),
        ("data/vllm/ci/group_changes.json", {"ci_changes"}),
        ("data/vllm/ci/hotness.json", {"ci_hotness"}),
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
        ("operations-stale-source", "analytics", {"ci_core"}),
        ("operations-stale-source", "gating_targets", {"ci_gating"}),
        ("operations-source-schema", "group_changes", {"ci_changes"}),
        ("operations-gating-inconsistent", None, {"ci_core", "ci_gating"}),
        ("operations-gating-inconsistent", "gating_targets", {"ci_gating"}),
        ("operations-trajectory-invalid", None, {"ci_core", "ci_changes"}),
        ("operations-trajectory-invalid", "group_changes", {"ci_changes"}),
        ("operations-reliability-invalid", None, {"ci_core"}),
        ("operations-nightly-invalid", None, {"ci_core"}),
        ("operations-test-health-invalid", None, {"ci_core"}),
        ("operations-definition-invalid", None, {"ci_core"}),
        ("operations-ownership-invalid", None, {"ci_core"}),
        ("gating-target-invalid", None, {"ci_gating"}),
        ("analytics-invalid", None, {"ci_core"}),
    ),
)
def test_generic_findings_route_to_their_consuming_domains(
    code: str,
    source: str | None,
    expected: set[str],
) -> None:
    assert finding_surfaces(_finding(code, source=source)) == frozenset(expected)


def test_non_data_path_remains_a_global_failure() -> None:
    assert finding_surfaces(
        _finding(
            "operations-gating-inconsistent",
            path="docs/assets/js/ci-health.js",
        )
    ) == frozenset()


def test_fallback_dependency_closure_invalidates_gating_transitively() -> None:
    assert FALLBACK_DEPENDENCIES == {"ci_core": frozenset({"ci_gating"})}
    assert fallback_dependency_closure(set()) == frozenset()
    assert fallback_dependency_closure("ci_core") == frozenset(
        {"ci_core", "ci_gating"}
    )
    assert fallback_dependency_closure({"ci_gating"}) == frozenset({"ci_gating"})
    assert fallback_dependency_closure({"ci_changes", "ci_hotness"}) == frozenset(
        {"ci_changes", "ci_hotness"}
    )
    assert fallback_dependency_closure(
        fallback_dependency_closure({"ci_core"})
    ) == frozenset({"ci_core", "ci_gating"})


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
            "ci_hotness": frozenset({"ci_core"}),
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
