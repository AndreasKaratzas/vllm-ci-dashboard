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
        "data/vllm/ci/analytics.json",
        "data/vllm/ci/ci_health.json",
        "data/vllm/ci/config_parity.json",
        "data/vllm/ci/failure_trends.json",
        "data/vllm/ci/flaky_tests.json",
        "data/vllm/ci/gating_nightlies.json",
        "data/vllm/ci/ownership_config_parity.json",
        "data/vllm/ci/parity_report.json",
        "data/vllm/ci/shard_bases.json",
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

CI_GATING_SURFACE_SPEC = SurfaceSpec(
    required_paths=(
        "data/vllm/ci/gating_proposals.json",
        "data/vllm/ci/gating_target_candidates.json",
        "data/vllm/ci/gating_targets.json",
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
            "data/vllm/issues.json",
            "data/vllm/prs.json",
            "data/vllm/releases.json",
        ),
    ),
    "ready": SurfaceSpec(
        required_paths=(
            "data/vllm/ci/project_items.json",
            "data/vllm/ci/ready_tickets.json",
        ),
        optional_paths=("data/vllm/ci/ready_tickets_state.json",),
    ),
    "perf_eval": SurfaceSpec(
        required_paths=(
            "data/vllm/perf_eval/events.jsonl",
            "data/vllm/perf_eval/perf_eval.json",
        ),
    ),
    "test_builds": SurfaceSpec(
        required_paths=("data/vllm/ci/test_builds/index.json",),
        globs=("data/vllm/ci/test_builds/*/*.json", "data/vllm/ci/test_builds/*/*.jsonl"),
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
            CI_CORE_SURFACE_SPEC,
            CI_GATING_SURFACE_SPEC,
            CI_CHANGES_SURFACE_SPEC,
            CI_HOTNESS_SURFACE_SPEC,
        )
        for path in spec.required_paths
    ),
    # Schema-v1 manifests predate the private watcher-state boundary. Keep
    # recognizing those entries during migration, but never restore them into
    # an active publication surface.
    optional_paths=(
        *CI_CORE_SURFACE_SPEC.optional_paths,
        *CI_CORE_WATCHER_STATE_PATHS,
    ),
    globs=CI_CORE_SURFACE_SPEC.globs,
)
LEGACY_SURFACE_SPECS = {LEGACY_CI_SURFACE: LEGACY_CI_SURFACE_SPEC}
LEGACY_SURFACE_ALIASES = {
    LEGACY_CI_SURFACE: frozenset(
        {"ci_core", "ci_gating", "ci_changes", "ci_hotness"}
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


# These are invalidation edges, not data-flow dependencies: if a source surface
# falls back, each listed dependent must fall back too.  Candidate gating
# targets consume core-owned gating_nightlies.json, so retaining current gating
# output beside a restored core would mix source cohorts.
FALLBACK_DEPENDENCIES: dict[str, frozenset[str]] = {
    "ci_core": frozenset({"ci_gating"}),
}


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
    "data/users.json",
    "data/vllm/ci/engineers.enc.json",
    "data/vllm/ci/kill_auth.enc.json",
    "data/vllm/ci/operations_v2.json",
    "data/vllm/ci/publication_state.json",
})


SOURCE_SURFACES = {
    "analytics": "ci_core",
    "agent_health": "agent_health",
    "amd_test_signal": "ci_core",
    "ci_health": "ci_core",
    "config_parity": "ci_core",
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
    "project_items": "ready",
    "ready_tickets": "ready",
    "ci_ownership": "ci_core",
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

    if code.startswith("operations-source-") or code in {
        "operations-stale-source",
        "operations-stale-source-fallback",
    }:
        owner = SOURCE_SURFACES.get(str(context.get("source") or ""))
        return frozenset({owner}) if owner else frozenset()
    if code.startswith("operations-agent-health-"):
        return frozenset({"agent_health"})
    if code.startswith("operations-queue-") or code == "operations-retired-mi355b":
        return frozenset({"queue"})
    if code.startswith("operations-"):
        global_operations = (
            "operations-schema",
            "operations-aggregate-provenance",
            "operations-bundle-",
            "operations-home-payload-budget",
            "operations-health-payload-budget",
        )
        if code == "operations-schema" or any(
            code.startswith(prefix) for prefix in global_operations[1:]
        ):
            return frozenset()
        contextual_owner = SOURCE_SURFACES.get(str(context.get("source") or ""))
        if contextual_owner:
            return frozenset({contextual_owner})
        if code.startswith("operations-gating-"):
            return frozenset({"ci_core", "ci_gating"})
        if code.startswith("operations-trajectory-"):
            return frozenset({"ci_core", "ci_changes"})
        return frozenset({"ci_core"})

    prefix_routes = (
        (
            (
                "ci-health-",
                "analytics-",
                "matrix-",
                "parity-matrix-",
                "definition-parity-",
                "shard-",
            ),
            "ci_core",
        ),
        (("gating-target-",), "ci_gating"),
        (("queue-lifecycle-",), "queue_lifecycle"),
        (("dns-health-",), "dns_health"),
        (("queue-",), "queue"),
        (("ready-ticket-",), "ready"),
        (("ci-pr-", "linked-ci-pr-", "rocm-pr-", "ci-custom-tag", "rocm-custom-tag", "home-", "project-issue-"), "github_home"),
    )
    for prefixes, owner in prefix_routes:
        if any(code.startswith(prefix) for prefix in prefixes):
            return frozenset({owner})
    return frozenset()


def public_manifest_ownership_path(relative: str) -> str:
    """Translate a path relative to data/ into the repository namespace."""
    return f"data/{PurePosixPath(relative).as_posix()}"
