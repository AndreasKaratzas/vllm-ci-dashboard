"""Atomic data-surface ownership for last-known-good publication fallback.

The hourly collector produces many related files.  Restoring a single file
after a failed cross-view invariant can create a mixed-build snapshot, so each
entry below is an indivisible publication transaction.  Derived Operations v2
files are intentionally absent: they are rebuilt after source selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


@dataclass(frozen=True)
class SurfaceSpec:
    required_paths: tuple[str, ...]
    optional_paths: tuple[str, ...] = ()
    globs: tuple[str, ...] = ()


SURFACE_SPECS: dict[str, SurfaceSpec] = {
    "ci": SurfaceSpec(
        required_paths=(
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
        ),
        optional_paths=(
            "data/vllm/ci/ci_ownership.json",
            "data/vllm/ci/open_amd_duration_regression_issues.json",
            "data/vllm/ci/open_amd_main_failure_issues.json",
            "data/vllm/ci/open_ci_area_regression_issues.json",
            "data/vllm/ci/open_ci_main_failure_issues.json",
            "data/vllm/ci/parity_key_overrides.json",
            "data/vllm/ci/quarantine.json",
            "data/vllm/ci/shard_base_catalog.json",
        ),
        globs=("data/vllm/ci/test_results/*.jsonl",),
    ),
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
        optional_paths=("data/vllm/ci/open_agent_health_issues.json",),
        globs=("data/vllm/ci/agent_health/*.jsonl",),
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
    "analytics": "ci",
    "agent_health": "agent_health",
    "amd_test_signal": "ci",
    "ci_health": "ci",
    "config_parity": "ci",
    "gating_targets": "ci",
    "gating_target_candidates": "ci",
    "amd_test_matrix": "ci",
    "capacity_monitor": "queue",
    "queue_timeseries": "queue",
    "queue_jobs": "queue",
    "workload_mapping": "queue",
    "group_changes": "ci",
    "omni_heuristic": "queue",
    "omni_issue_state": "queue",
    "project_items": "ready",
    "ready_tickets": "ready",
    "ci_ownership": "ci",
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
    if code.startswith("operations-trajectory-"):
        return frozenset({"ci"})
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
        return frozenset({"ci"})

    prefix_routes = (
        (("ci-health-", "analytics-", "matrix-", "parity-matrix-", "definition-parity-", "gating-target-"), "ci"),
        (("queue-lifecycle-",), "queue_lifecycle"),
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
