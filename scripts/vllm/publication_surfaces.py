"""Atomic data-surface ownership for last-known-good publication fallback.

The hourly collector produces many related files.  Restoring a single file
after a failed cross-view invariant can create a mixed-build snapshot, so each
entry below is an indivisible publication transaction.  Derived Operations v2
files are intentionally absent: they are rebuilt after source selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable


SURFACE_CONTRACT_VERSION = 4


@dataclass(frozen=True)
class SurfaceSpec:
    required_paths: tuple[str, ...]
    optional_paths: tuple[str, ...] = ()
    globs: tuple[str, ...] = ()


CI_CORE_WATCHER_STATE_PATHS = (
    "data/vllm/ci/open_amd_duration_regression_issues.json",
    "data/vllm/ci/open_amd_main_failure_issues.json",
    "data/vllm/ci/open_ci_area_regression_issues.json",
    "data/vllm/ci/open_ci_main_failure_issues.json",
)
AGENT_HEALTH_WATCHER_STATE_PATHS = (
    "data/vllm/ci/open_agent_health_issues.json",
)
INDEPENDENT_WATCHER_STATE_PATHS = frozenset(
    (*CI_CORE_WATCHER_STATE_PATHS, *AGENT_HEALTH_WATCHER_STATE_PATHS)
)


CI_CORE_SURFACE_SPEC = SurfaceSpec(
    required_paths=(
        "data/vllm/ci/amd_test_matrix.json",
        "data/vllm/ci/ci_health.json",
        "data/vllm/ci/config_parity.json",
        "data/vllm/ci/failure_trends.json",
        "data/vllm/ci/flaky_tests.json",
        "data/vllm/ci/ownership_config_parity.json",
        "data/vllm/ci/parity_report.json",
        "data/vllm/ci/shard_bases.json",
        "data/vllm/ci/test_group_parity.json",
        "data/vllm/parity_report.json",
        "data/vllm/test_results.json",
    ),
    optional_paths=(
        "data/vllm/ci/ci_ownership.json",
        "data/vllm/ci/parity_key_overrides.json",
        "data/vllm/ci/quarantine.json",
        "data/vllm/ci/shard_base_catalog.json",
    ),
    globs=("data/vllm/ci/test_results/*.jsonl",),
)

CI_ANALYTICS_SURFACE_SPEC = SurfaceSpec(
    required_paths=("data/vllm/ci/analytics.json",),
)

CI_GATING_SURFACE_SPEC = SurfaceSpec(
    required_paths=(
        "data/vllm/ci/gating_nightlies.json",
        "data/vllm/ci/gating_proposals.json",
        "data/vllm/ci/gating_target_candidates.json",
        "data/vllm/ci/gating_targets.json",
    ),
)

# Schema-v1 and early schema-v2 publication state predate the analytics blast-
# radius split. Keep their exact ownership contract available so a currently
# degraded baseline can be validated in full before its proof is repartitioned.
PRE_ANALYTICS_CI_CORE_SURFACE_SPEC = SurfaceSpec(
    required_paths=(
        *CI_CORE_SURFACE_SPEC.required_paths,
        "data/vllm/ci/analytics.json",
        "data/vllm/ci/gating_nightlies.json",
    ),
    optional_paths=CI_CORE_SURFACE_SPEC.optional_paths,
    globs=CI_CORE_SURFACE_SPEC.globs,
)
PRE_ANALYTICS_CI_GATING_SURFACE_SPEC = SurfaceSpec(
    required_paths=tuple(
        path
        for path in CI_GATING_SURFACE_SPEC.required_paths
        if path != "data/vllm/ci/gating_nightlies.json"
    ),
)

CI_CHANGES_SURFACE_SPEC = SurfaceSpec(
    required_paths=("data/vllm/ci/group_changes.json",),
)

CI_HOTNESS_SURFACE_SPEC = SurfaceSpec(
    required_paths=("data/vllm/ci/hotness.json",),
)


SURFACE_SPECS: dict[str, SurfaceSpec] = {
    "ci_core": CI_CORE_SURFACE_SPEC,
    "ci_analytics": CI_ANALYTICS_SURFACE_SPEC,
    "ci_gating": CI_GATING_SURFACE_SPEC,
    "ci_changes": CI_CHANGES_SURFACE_SPEC,
    "ci_hotness": CI_HOTNESS_SURFACE_SPEC,
    "queue": SurfaceSpec(
        required_paths=(
            "data/vllm/ci/capacity_monitor.json",
            "data/vllm/ci/omni_surge_heuristic.json",
            "data/vllm/ci/queue_jobs.json",
            "data/vllm/ci/queue_timeseries.jsonl",
            "data/vllm/ci/workload_mapping.json",
        ),
        optional_paths=(
            "data/vllm/ci/open_omni_surge_issues.json",
            "data/vllm/ci/open_queue_issues.json",
            "data/vllm/ci/open_queue_zombie_issues.json",
        ),
    ),
    "queue_lifecycle": SurfaceSpec(
        required_paths=("data/vllm/ci/queue_lifecycle.json",),
    ),
    "agent_health": SurfaceSpec(
        required_paths=("data/vllm/ci/agent_health.json",),
        globs=("data/vllm/ci/agent_health/*.jsonl",),
    ),
    "dns_health": SurfaceSpec(
        required_paths=("data/vllm/ci/dns_failures.json",),
    ),
    "github_home": SurfaceSpec(
        required_paths=(
            "data/vllm/ci/project_items.json",
            "data/vllm/issues.json",
            "data/vllm/prs.json",
            "data/vllm/releases.json",
        ),
    ),
    "perf_eval": SurfaceSpec(
        required_paths=(
            "data/vllm/perf_eval/events.jsonl",
            "data/vllm/perf_eval/perf_eval.json",
        ),
    ),
}


# Schema-v1 publication state used one monolithic ``ci`` transaction.  Keep
# that contract out of the active ownership map while exposing enough metadata
# for state readers to partition and validate an already-committed manifest.
LEGACY_CI_SURFACE = "ci"
LEGACY_CI_SURFACE_SPEC = SurfaceSpec(
    required_paths=tuple(
        path
        for spec in (
            PRE_ANALYTICS_CI_CORE_SURFACE_SPEC,
            PRE_ANALYTICS_CI_GATING_SURFACE_SPEC,
            CI_CHANGES_SURFACE_SPEC,
            CI_HOTNESS_SURFACE_SPEC,
        )
        for path in spec.required_paths
    ),
    # Schema-v1 manifests predate the private watcher-state boundary. Keep
    # recognizing those entries during migration, but never restore them into
    # an active publication surface.
    optional_paths=(
        *PRE_ANALYTICS_CI_CORE_SURFACE_SPEC.optional_paths,
        *CI_CORE_WATCHER_STATE_PATHS,
    ),
    globs=PRE_ANALYTICS_CI_CORE_SURFACE_SPEC.globs,
)
LEGACY_SURFACE_SPECS = {LEGACY_CI_SURFACE: LEGACY_CI_SURFACE_SPEC}
LEGACY_SURFACE_ALIASES = {
    LEGACY_CI_SURFACE: frozenset(
        {"ci_core", "ci_analytics", "ci_gating", "ci_changes", "ci_hotness"}
    ),
}


def ignored_watcher_state_paths(surface: str) -> frozenset[str]:
    """Return historical private-ledger entries accepted for one surface.

    These files are independently mutable automation state, not dashboard
    publication bytes. The allowlist is deliberately surface-specific so an
    unrelated or misplaced manifest entry still fails closed.
    """
    if surface in {LEGACY_CI_SURFACE, "ci_core"}:
        return frozenset(CI_CORE_WATCHER_STATE_PATHS)
    if surface == "agent_health":
        return frozenset(AGENT_HEALTH_WATCHER_STATE_PATHS)
    return frozenset()


# These are invalidation edges, not ordinary data-flow dependencies: if a
# source surface falls back, each listed dependent must fall back too. Gating's
# nightly evidence now belongs to the same ci_gating transaction as its targets,
# and private analytics can therefore fall back without invalidating CI health,
# matrix, ownership, parity, or gating publication.
FALLBACK_DEPENDENCIES: dict[str, frozenset[str]] = {}


def fallback_dependency_closure(surfaces: Iterable[str]) -> frozenset[str]:
    """Return the transitive active-surface fallback closure.

    Unknown inputs and dangling dependency edges fail closed instead of being
    silently omitted from an atomic restore.
    """
    requested = (surfaces,) if isinstance(surfaces, str) else surfaces
    closure = {str(surface).strip() for surface in requested if str(surface).strip()}
    unknown = closure - set(SURFACE_SPECS)
    if unknown:
        raise ValueError(f"unknown publication surfaces: {sorted(unknown)}")

    pending = list(closure)
    while pending:
        surface = pending.pop()
        dependents = set(FALLBACK_DEPENDENCIES.get(surface, ()))
        dangling = dependents - set(SURFACE_SPECS)
        if dangling:
            raise ValueError(
                f"fallback dependencies for {surface} reference unknown surfaces: "
                f"{sorted(dangling)}"
            )
        for dependent in dependents - closure:
            closure.add(dependent)
            pending.append(dependent)
    return frozenset(closure)


# Static/publication-contract files that are deliberately not eligible for a
# data fallback.  A defect in one of these remains a global hard failure.
GLOBAL_DATA_PATHS = frozenset({
    "data/site/projects.json",
    "data/vllm/ci/operations_v2.json",
    "data/vllm/ci/operations_v2.json.gz",
    "data/vllm/ci/operations_v2_manifest.json",
    "data/vllm/ci/publication_state.json",
})


SOURCE_SURFACES = {
    "analytics": "ci_analytics",
    "agent_health": "agent_health",
    # The logical AMD test-health signal is primarily the ci_core-owned parsed
    # test-result ledger; analytics contributes only supplementary metadata.
    "amd_test_signal": "ci_core",
    "ci_health": "ci_core",
    "config_parity": "ci_core",
    "test_group_parity": "ci_core",
    "gating_targets": "ci_gating",
    "gating_target_candidates": "ci_gating",
    "amd_test_matrix": "ci_core",
    "capacity_monitor": "queue",
    "queue_timeseries": "queue",
    "queue_jobs": "queue",
    "workload_mapping": "queue",
    "group_changes": "ci_changes",
    "omni_heuristic": "queue",
    "omni_issue_state": "queue",
    "project_items": "github_home",
    "ci_ownership": "ci_core",
}

# Operations is a derived read model, so its findings must roll back the source
# transactions that can actually produce each invariant. Keep this exhaustive:
# silently defaulting a new code to ci_core can leave the bad source current and
# turn a recoverable data problem into a blocked deployment.
_OPS_GLOBAL = frozenset()
_OPS_ANALYTICS = frozenset({"ci_analytics"})
_OPS_CORE = frozenset({"ci_core"})
_OPS_GATING = frozenset({"ci_gating"})
_OPS_QUEUE = frozenset({"queue"})
_OPS_AGENT_HEALTH = frozenset({"agent_health"})
_OPS_ANALYTICS_CORE = frozenset({"ci_analytics", "ci_core"})
_OPS_ANALYTICS_CORE_QUEUE = frozenset({"ci_analytics", "ci_core", "queue"})
_OPS_GATING_QUEUE = frozenset({"ci_gating", "queue"})
_OPS_CORE_GATING_QUEUE = frozenset({"ci_core", "ci_gating", "queue"})
_OPS_ANALYTICS_GATING_QUEUE = frozenset({"ci_analytics", "ci_gating", "queue"})
_OPS_ORG_SUMMARY_PRODUCERS = frozenset({
    "ci_analytics",
    "ci_core",
    "ci_gating",
    "queue",
    "queue_lifecycle",
})
_OPS_RETIRED_QUEUE_PRODUCERS = frozenset({
    "agent_health",
    "ci_analytics",
    "ci_changes",
    "ci_core",
    "ci_gating",
    "queue",
})

CONTEXTUAL_OPERATIONS_FINDING_CODES = frozenset({
    "operations-source-from-future",
    "operations-source-provenance",
    "operations-source-timestamp",
    "operations-stale-source",
    "operations-stale-source-fallback",
})

OPERATIONS_FINDING_SURFACES: dict[str, frozenset[str]] = {
    # Ambiguous private inputs are a global publication-boundary failure, not
    # a defect that can be hidden by rolling back one source surface.
    "operations-source-ambiguous": _OPS_GLOBAL,
    # Agent-health section.
    "operations-agent-health-cofail-default": _OPS_AGENT_HEALTH,
    "operations-agent-health-failing-state": _OPS_AGENT_HEALTH,
    "operations-agent-health-infra-flag": _OPS_AGENT_HEALTH,
    "operations-agent-health-missing": _OPS_AGENT_HEALTH,
    "operations-agent-health-options": _OPS_AGENT_HEALTH,
    "operations-agent-health-queue-scope": _OPS_AGENT_HEALTH,
    "operations-agent-health-rollup-shape": _OPS_AGENT_HEALTH,
    # AMD test-health rows: membership comes from core test results; the
    # state-bearing variants also consume analytics job state.
    "operations-amd-build-count": _OPS_CORE,
    "operations-amd-build-job-variant-alias": _OPS_CORE,
    "operations-amd-build-job-variant-state-alias": _OPS_ANALYTICS_CORE,
    "operations-amd-build-state-count": _OPS_ANALYTICS_CORE,
    "operations-amd-historical-reliability": _OPS_ANALYTICS,
    "operations-amd-latest-build-count": _OPS_ANALYTICS_CORE,
    "operations-amd-latest-catalog-count": _OPS_CORE,
    "operations-amd-latest-job-variant-alias": _OPS_CORE,
    "operations-amd-latest-job-variant-state-alias": _OPS_ANALYTICS_CORE,
    "operations-amd-latest-state-count": _OPS_ANALYTICS_CORE,
    "operations-amd-logical-group-build-mismatch": _OPS_CORE,
    "operations-amd-logical-group-counts": _OPS_CORE,
    "operations-amd-logical-group-job-build": _OPS_CORE,
    "operations-amd-logical-group-nightly-counts": _OPS_ANALYTICS_CORE,
    "operations-amd-logical-group-nightly-missing": _OPS_ANALYTICS_CORE,
    "operations-amd-logical-group-percentage": _OPS_CORE,
    "operations-amd-logical-group-shape": _OPS_CORE,
    "operations-amd-logical-group-source": _OPS_CORE,
    "operations-amd-logical-group-unavailable-build": _OPS_CORE,
    "operations-amd-logical-groups-exceed-job-variants": _OPS_CORE,
    "operations-amd-retained-count": _OPS_CORE,
    "operations-amd-retained-job-variant-alias": _OPS_CORE,
    # Gating combines reviewed configuration, queue capacity, and selected
    # core/analytics evidence depending on the exact projection.
    "operations-active-target-count": _OPS_GATING_QUEUE,
    "operations-active-target-summary": _OPS_GATING_QUEUE,
    "operations-canonical-target-count": _OPS_GATING,
    "operations-gating-history-source-pipeline": _OPS_ANALYTICS_GATING_QUEUE,
    "operations-gating-latest-source-pipeline": _OPS_CORE_GATING_QUEUE,
    "operations-gating-latest-source-url": _OPS_CORE_GATING_QUEUE,
    "operations-gating-missing-links": _OPS_CORE_GATING_QUEUE,
    "operations-gating-runtime-resolution": _OPS_CORE_GATING_QUEUE,
    "operations-gating-runtime-resolution-count": _OPS_CORE_GATING_QUEUE,
    # Private analytics and normalized reliability projections.
    "operations-flaky-candidate-source": _OPS_ANALYTICS,
    "operations-latency-max-duration": _OPS_ANALYTICS,
    "operations-latency-source": _OPS_ANALYTICS,
    "operations-platform-comparison-counts": _OPS_ANALYTICS,
    "operations-platform-comparison-eligibility": _OPS_ANALYTICS,
    "operations-reliability-cohort": _OPS_ANALYTICS,
    "operations-reliability-cohort-build-numbers": _OPS_ANALYTICS,
    "operations-reliability-cohort-composition": _OPS_ANALYTICS,
    "operations-reliability-cohort-source": _OPS_ANALYTICS,
    "operations-reliability-denominator": _OPS_ANALYTICS,
    "operations-reliability-denominator-sum": _OPS_ANALYTICS,
    "operations-reliability-evidence-count": _OPS_ANALYTICS,
    "operations-reliability-evidence-ref": _OPS_ANALYTICS,
    "operations-reliability-evidence-type": _OPS_ANALYTICS,
    "operations-reliability-group-identity": _OPS_ANALYTICS,
    "operations-reliability-group-source": _OPS_ANALYTICS,
    "operations-reliability-hardware-identity": _OPS_ANALYTICS,
    "operations-reliability-max-duration": _OPS_ANALYTICS,
    "operations-reliability-missing-links": _OPS_ANALYTICS,
    "operations-reliability-observation-source": _OPS_ANALYTICS,
    "operations-reliability-source-pipeline": _OPS_ANALYTICS,
    "operations-reliability-unavailable": _OPS_ANALYTICS,
    "operations-retry-attempt-count": _OPS_ANALYTICS,
    "operations-retry-links": _OPS_ANALYTICS,
    "operations-retry-recovery-count": _OPS_ANALYTICS,
    "operations-retry-source": _OPS_ANALYTICS,
    "operations-comparison-payload-budget": _OPS_ANALYTICS,
    "operations-comparison-retry-evidence-payload-budget": _OPS_ANALYTICS,
    "operations-trajectory-scope": _OPS_ANALYTICS,
    # Nightly history combines analytics history with current core health.
    "operations-latest-nightly": _OPS_ANALYTICS_CORE,
    "operations-latest-nightly-ahead": _OPS_CORE,
    "operations-nightly-retention": _OPS_ANALYTICS_CORE,
    # Queue-only projections and one legacy whole-bundle sentinel.
    "operations-queue-history": _OPS_QUEUE,
    "operations-queue-payload-budget": _OPS_QUEUE,
    "operations-retired-mi355b": _OPS_RETIRED_QUEUE_PRODUCERS,
    # Size guards are data-dependent across the compact shell producers.
    "operations-health-payload-budget": _OPS_ANALYTICS_CORE_QUEUE,
    "operations-home-payload-budget": _OPS_ANALYTICS_CORE_QUEUE,
    # These prove code/bundle contracts rather than one replaceable input.
    "operations-aggregate-provenance": _OPS_GLOBAL,
    "operations-bundle-freshness": _OPS_GLOBAL,
    "operations-bundle-json": _OPS_GLOBAL,
    "operations-bundle-org-summary-budget": _OPS_ORG_SUMMARY_PRODUCERS,
    "operations-bundle-org-summary-descriptor": _OPS_GLOBAL,
    "operations-bundle-org-summary-missing": _OPS_GLOBAL,
    "operations-bundle-org-summary-projection": _OPS_GLOBAL,
    "operations-bundle-org-summary-scheduled-denominators": _OPS_ANALYTICS,
    "operations-bundle-org-summary-size": _OPS_GLOBAL,
    "operations-bundle-org-summary-source": frozenset({"queue_lifecycle"}),
    "operations-bundle-path": _OPS_GLOBAL,
    "operations-bundle-schema": _OPS_GLOBAL,
    "operations-bundle-sections": _OPS_GLOBAL,
    "operations-bundle-shape": _OPS_GLOBAL,
    "operations-bundle-size": _OPS_GLOBAL,
    "operations-schema": _OPS_GLOBAL,
    "operations-unsupported-owners": _OPS_GLOBAL,
    "operations-upstream-parity-scope": _OPS_GLOBAL,
}


def _matches(path: str, pattern: str) -> bool:
    return PurePosixPath(path).match(pattern)


def surface_for_path(path: str) -> str | None:
    """Return the unique owning surface for a repository-relative path."""
    normalized = PurePosixPath(str(path or "")).as_posix()
    owners = []
    for name, spec in SURFACE_SPECS.items():
        if normalized in {*spec.required_paths, *spec.optional_paths} or any(
            _matches(normalized, pattern) for pattern in spec.globs
        ):
            owners.append(name)
    if len(owners) > 1:
        raise ValueError(f"publication path {normalized!r} has multiple owners: {owners}")
    return owners[0] if owners else None


def finding_surfaces(finding: Any) -> frozenset[str]:
    """Route an audit finding to source transactions, or return a hard stop.

    An empty result means the finding is global or unknown and must not be
    hidden by restoring data.  Paths under code/config/docs always win over a
    coincidentally similar error-code prefix.
    """
    path = str(getattr(finding, "path", "") or "")
    code = str(getattr(finding, "code", "") or "")
    context = getattr(finding, "context", {}) or {}

    if path:
        owner = surface_for_path(path)
        if owner:
            return frozenset({owner})
        if path.startswith(("docs/", ".github/", "scripts/", "config/")):
            return frozenset()
        if path.startswith("data/") and path not in GLOBAL_DATA_PATHS:
            # Unknown data has no safe atomic rollback contract.
            return frozenset()

    if code in CONTEXTUAL_OPERATIONS_FINDING_CODES or code.startswith(
        "operations-source-"
    ):
        owner = SOURCE_SURFACES.get(str(context.get("source") or ""))
        return frozenset({owner}) if owner else frozenset()
    if code.startswith("operations-"):
        # Unknown derived codes are global hard stops until their lineage is
        # explicitly added above and covered by the exhaustiveness test.
        return OPERATIONS_FINDING_SURFACES.get(code, frozenset())

    prefix_routes = (
        (
            (
                "ci-health-",
                "matrix-",
                "parity-matrix-",
                "definition-parity-",
                "shard-",
            ),
            "ci_core",
        ),
        (("analytics-",), "ci_analytics"),
        (("gating-target-",), "ci_gating"),
        (("queue-lifecycle-",), "queue_lifecycle"),
        (("dns-health-",), "dns_health"),
        (("queue-",), "queue"),
        (("ci-pr-", "linked-ci-pr-", "rocm-pr-", "ci-custom-tag", "rocm-custom-tag", "home-", "project-issue-"), "github_home"),
    )
    for prefixes, owner in prefix_routes:
        if any(code.startswith(prefix) for prefix in prefixes):
            return frozenset({owner})
    return frozenset()


def public_manifest_ownership_path(relative: str) -> str:
    """Translate a path relative to data/ into the repository namespace."""
    return f"data/{PurePosixPath(relative).as_posix()}"
