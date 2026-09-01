#!/usr/bin/env python3
# cspell:ignore AKIA ASIA baprs bkua bxox gaierror github_pat pousr xapp xoxb
"""Audit the dashboard's generated data, frontend contracts, and deploy path.

The normal pytest suite has focused unit and schema checks. This script is the
cross-surface pass: it follows the user-facing dashboard numbers back to their
JSON files, verifies that related views agree on the same source of truth, and
checks the workflows that publish those files.

Usage:
    python scripts/vllm/audit_dashboard_data.py
    python scripts/vllm/audit_dashboard_data.py --format json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.publication_surfaces import (  # noqa: E402
    LEGACY_CI_SURFACE,
    LEGACY_CI_SURFACE_SPEC,
    LEGACY_SURFACE_ALIASES,
    PRE_ANALYTICS_CI_CORE_SURFACE_SPEC,
    PRE_ANALYTICS_CI_GATING_SURFACE_SPEC,
    SOURCE_SURFACES,
    SURFACE_CONTRACT_VERSION,
    SURFACE_SPECS,
    SurfaceSpec,
    fallback_dependency_closure,
    ignored_watcher_state_paths,
)
ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
VLLM = DATA / "vllm"
CI = VLLM / "ci"

AMD_FAILURE_STATES = {"failed", "timed_out", "broken", "soft_fail"}
AMD_WAITING_STATES = {"running", "scheduled", "assigned"}
RESULT_SUFFIXES = {"amd-ci": "amd", "ci": "upstream"}
OPERATIONS_SOURCE_MAX_AGE_HOURS = 6
OPERATIONS_FRESH_SOURCE_KEYS = frozenset({
    "analytics",
    "agent_health",
    "amd_test_signal",
    "ci_health",
    "config_parity",
    "test_group_parity",
    "gating_targets",
    "gating_target_candidates",
    "amd_test_matrix",
    "capacity_monitor",
    "queue_timeseries",
    "queue_jobs",
    "workload_mapping",
    "group_changes",
    "omni_heuristic",
    "project_items",
})
OPERATIONS_SOURCE_MAX_AGE_OVERRIDES = {
    # AMD nightlies run daily; this is source observation age, not collector age.
    "amd_test_signal": 36,
    # Project #39 is refreshed with the hourly GitHub Home collection.
    "project_items": 36,
}
PUBLICATION_FALLBACK_MAX_AGE_HOURS = 36
FULL_COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
SHARD_EVIDENCE_REQUIRED_KEYS = frozenset({
    "pipeline",
    "build_number",
    "build_commit",
    "build_state",
    "roster_complete",
    "result_file",
    "job_names",
})
SHARD_TERMINAL_BUILD_STATES = frozenset({
    "passed",
    "failed",
    "timed_out",
    "canceled",
    "broken",
    "blocked",
})
SHARD_RESULT_FILE_RE = re.compile(r"\d{4}-\d{2}-\d{2}_amd\.jsonl")
PUBLIC_FILE_WARN_BYTES = 64 * 1024 * 1024
PUBLIC_FILE_HARD_BYTES = 85 * 1024 * 1024
PUBLIC_SITE_WARN_BYTES = 250 * 1024 * 1024
DNS_FAILURES_DATA_PATH = "data/vllm/ci/dns_failures.json"
DNS_FAILURES_MAX_BYTES = 8 * 1024 * 1024
OPERATIONS_COMPARISON_MAX_BYTES = 1_500_000
OPERATIONS_COMPARISON_RETRY_EVIDENCE_MAX_BYTES = 6_000_000
DNS_EVIDENCE_MAX_ITEMS = 5000
DNS_MAX_FRESH_AGE_HOURS = 12
DNS_OUTCOME_CONTRACT = "dns-job-outcomes-v1"
DNS_WINDOW_OPTIONS = (
    ("1h", "Last hour", 1),
    ("3h", "Last 3 hours", 3),
    ("12h", "Last 12 hours", 12),
    ("24h", "Last day", 24),
    ("72h", "Last 3 days", 72),
    ("168h", "Last 7 days", 168),
    ("720h", "Last 30 days", 720),
)
DNS_TARGET_CATEGORIES = (
    "huggingface_hub",
    "vllm_public_assets",
    "aws_s3",
    "github",
    "pypi",
    "other_public",
    "unknown",
)
DNS_SIGNATURE_IDS = frozenset({
    "temporary_name_resolution",
    "name_or_service_unknown",
    "urllib3_name_resolution",
    "curl_could_not_resolve",
    "getaddrinfo_eai_again",
    "getaddrinfo_failed",
    "no_such_host",
    "nodename_not_known",
    "temporary_failure_resolving",
    "dns_resolution_failed",
})
DNS_COVERAGE_STATUSES = frozenset({"not_collected", "partial", "complete"})
DNS_TIME_BASES = frozenset({"log_timestamp", "job_finished_at"})
DNS_JOB_STATES = frozenset({"passed", "soft", "hard"})
DNS_OUTCOME_COUNT_FIELDS = (
    "passed_jobs",
    "soft_failed_jobs",
    "hard_failed_jobs",
)
DNS_OUTCOME_COUNT_FIELD_BY_STATE = {
    "passed": "passed_jobs",
    "soft": "soft_failed_jobs",
    "hard": "hard_failed_jobs",
}
DNS_PIPELINES = ("amd-ci", "ci")
DNS_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
)
DNS_UTC_SECOND_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
DNS_SAFE_COORD_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")
DNS_ARBITRARY_HOST_RE = re.compile(
    r"\b(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63}\b",
    re.IGNORECASE,
)
DNS_SECRET_OR_URL_RE = re.compile(
    r"(?:\b(?:xox[a-z]|xapp)-[A-Za-z0-9-]{12,}|"
    r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})|"
    r"\bbk[a-z]{1,6}_[A-Za-z0-9]{16,}|\bhf_[A-Za-z0-9]{16,}|"
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|"
    r"\bauthorization\s*[:=]|"
    r"\b(?:bearer|basic)\s+[A-Za-z0-9+/_.~=-]{12,}|"
    r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY(?: BLOCK)?-----|"
    r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
    r"password|secret|token)\b[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9+/_.~=-]{8,}|"
    r"(?:https?|s3)://|git@)",
    re.IGNORECASE,
)
DNS_RAW_LOG_RE = re.compile(
    r"(?:"
    r"temporary failure in name resolution|name or service not known|could not resolve host|"
    r"nameresolutionerror|getaddrinfo|socket\.gaierror|traceback|\\[nr])",
    re.IGNORECASE,
)
PRIVATE_ANALYTICS_PATH = "vllm/ci/analytics.json"
PRIVATE_ANALYTICS_DATA_PATH = f"data/{PRIVATE_ANALYTICS_PATH}"
PUBLIC_ANALYTICS_PROJECTOR_ID = "public_analytics_v1"
PUBLIC_ANALYTICS_BOUNDARY_MARKER = "PUBLIC-ANALYTICS-BOUNDARY"
PRIVATE_ANALYTICS_CACHE_VERSION = "analytics-builds-v1"
PRIVATE_ANALYTICS_CACHE_PATH = (
    f"data/vllm/ci/.cache/{PRIVATE_ANALYTICS_CACHE_VERSION}"
)
PRIVATE_ANALYTICS_CACHE_MANIFEST_PATH = PRIVATE_ANALYTICS_CACHE_PATH.removeprefix(
    "data/"
)
PRIVATE_ANALYTICS_CACHE_SAMPLE = (
    f"{PRIVATE_ANALYTICS_CACHE_MANIFEST_PATH}/amd-ci.json"
)
PRIVATE_ANALYTICS_CACHE_BOUNDARY_MARKER = "PRIVATE-ANALYTICS-CACHE-BOUNDARY"
PUBLICATION_STATE_RELATIVE = Path("data/vllm/ci/publication_state.json")
DECLARED_PUBLICATION_SURFACE_NAMES = frozenset(SURFACE_SPECS)
PUBLICATION_SURFACE_REQUIRED_KEYS = {
    "data/vllm/ci/shard_base_catalog.json": {
        "schema_version",
        "source",
        "normalization_bases",
        "pipelines",
        "definitions",
        "evidence",
    },
    "data/vllm/ci/failure_trends.json": {
        "generated_at",
        "new_failures",
        "recently_fixed",
        "top_offenders",
        "pass_rate_trend",
        "mttf",
        "degrading_modules",
    },
    "data/vllm/ci/flaky_tests.json": {
        "generated_at",
        "tests",
        "total_flaky",
        "window_builds",
    },
    "data/vllm/ci/hotness.json": {
        "generated_at",
        "window_hours",
        "builds_examined",
        "test_groups",
        "branches",
        "queues",
    },
    "data/vllm/releases.json": {"collected_at", "releases"},
}


def _uses_declared_publication_domain() -> bool:
    """Avoid applying production aliases to monkeypatched test-local specs."""
    return set(SURFACE_SPECS) == DECLARED_PUBLICATION_SURFACE_NAMES


def _publication_legacy_aliases() -> dict[str, frozenset[str]]:
    if not _uses_declared_publication_domain():
        return {}
    return LEGACY_SURFACE_ALIASES


def _publication_fallback_closure(surfaces: set[str]) -> set[str]:
    if not _uses_declared_publication_domain():
        return set(surfaces)
    return set(fallback_dependency_closure(surfaces))


def _publication_spec_owns_path(spec: SurfaceSpec, relative: str) -> bool:
    return relative in {*spec.required_paths, *spec.optional_paths} or any(
        Path(relative).match(pattern) for pattern in spec.globs
    )


def _publication_expected_paths(root: Path, spec: SurfaceSpec) -> set[str]:
    expected = set(spec.required_paths)
    expected.update(
        relative
        for relative in spec.optional_paths
        if (root / relative).is_file()
    )
    expected.update(
        candidate.relative_to(root).as_posix()
        for pattern in spec.globs
        for candidate in root.glob(pattern)
        if candidate.is_file()
    )
    return expected


def _publication_head_descriptor(
    root: Path,
    relative: str,
) -> dict[str, object] | None:
    """Describe a path only when the worktree still matches committed HEAD."""
    try:
        committed = subprocess.run(
            ["git", "show", f"HEAD:{relative}"],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
        current = (root / relative).read_bytes()
    except (OSError, subprocess.CalledProcessError):
        return None
    if current != committed:
        return None
    return {
        "bytes": len(committed),
        "sha256": hashlib.sha256(committed).hexdigest(),
    }


def _migrated_publication_manifest_entries(
    surface: str,
    entries: object,
) -> object:
    if not isinstance(entries, dict):
        return entries
    ignored = ignored_watcher_state_paths(surface)
    return {
        relative: descriptor
        for relative, descriptor in entries.items()
        if relative not in ignored
    }


def _migrated_publication_restored_paths(
    surface: str,
    paths: object,
) -> object:
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        return paths
    ignored = ignored_watcher_state_paths(surface)
    return sorted(path for path in paths if path not in ignored)


def _publication_surface_expansions(
    surfaces: list[str],
    aliases: dict[str, frozenset[str]],
) -> dict[str, frozenset[str]]:
    expansions: dict[str, frozenset[str]] = {}
    expanded: set[str] = set()
    for surface in surfaces:
        targets = aliases.get(surface, frozenset({surface}))
        if not targets or not set(targets) <= set(SURFACE_SPECS):
            raise ValueError(f"publication surface {surface!r} cannot be migrated")
        if expanded & set(targets):
            raise ValueError("publication surface aliases overlap active surfaces")
        expansions[surface] = targets
        expanded.update(targets)
    return expansions


def _partition_publication_manifest(
    root: Path,
    manifest: dict[str, dict],
    expansions: dict[str, frozenset[str]],
) -> dict[str, dict]:
    partitioned: dict[str, dict] = {}
    for surface, targets in expansions.items():
        entries = _migrated_publication_manifest_entries(
            surface, manifest[surface]
        )
        if not isinstance(entries, dict):
            raise ValueError("publication fallback manifest entries must be objects")
        if targets == frozenset({surface}):
            partitioned[surface] = dict(entries)
            continue
        child_entries = {target: {} for target in targets}
        for relative, descriptor in entries.items():
            owners = [
                target
                for target in targets
                if _publication_spec_owns_path(SURFACE_SPECS[target], relative)
            ]
            if len(owners) != 1:
                raise ValueError(
                    "legacy fallback manifest path lacks one active owner"
                )
            child_entries[owners[0]][relative] = descriptor
        for target, entries_for_target in child_entries.items():
            if set(entries_for_target) != _publication_expected_paths(
                root, SURFACE_SPECS[target]
            ):
                raise ValueError(
                    f"legacy fallback manifest partition for {target} is incomplete"
                )
            partitioned[target] = entries_for_target
    return partitioned


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list:
    return value if isinstance(value, list) else []


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _strict_positive_int_set(value: Any) -> set[int] | None:
    if not isinstance(value, list):
        return None
    if any(
        not isinstance(item, int) or isinstance(item, bool) or item <= 0
        for item in value
    ):
        return None
    return set(value)


def _buildkite_url_matches(
    value: Any,
    pipeline: str,
    build_number: Any = None,
    *,
    require_job: bool = False,
) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.netloc != "buildkite.com":
        return False
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[:3] != ["vllm", pipeline, "builds"]:
        return False
    actual = _safe_int(parts[3], -1)
    expected = _safe_int(build_number, -1) if build_number not in (None, "") else None
    if actual <= 0 or (expected is not None and actual != expected):
        return False
    suffix = parts[4:]
    if not require_job:
        return not suffix
    if len(suffix) < 2 or suffix[0] != "steps":
        return False
    if suffix[1] == "canvas":
        query = parse_qs(parsed.query)
        return bool(query.get("jid") or query.get("sid"))
    return bool(suffix[1])


@dataclass(frozen=True)
class DataSpec:
    relpath: str
    producers: tuple[str, ...]
    consumers: tuple[str, ...]
    required_keys: tuple[str, ...] = ()
    description: str = ""


DATA_SPECS: tuple[DataSpec, ...] = (
    DataSpec(
        "data/site/projects.json",
        ("scripts/render.py",),
        ("config/public_data_manifest.json",),
        ("projects",),
        "Project selector/config shell",
    ),
    DataSpec(
        "data/vllm/prs.json",
        ("scripts/collect.py",),
        ("docs/assets/js/ops-v2.js",),
        ("collected_at", "prs"),
        "Home PR list and top PR counters",
    ),
    DataSpec(
        "data/vllm/issues.json",
        ("scripts/collect.py",),
        ("docs/assets/js/ops-v2.js",),
        ("collected_at", "issues"),
        "Home project #39 issue list and issue counter",
    ),
    DataSpec(
        "data/vllm/test_results.json",
        ("scripts/collect_ci.py",),
        ("config/public_data_manifest.json",),
        ("collected_at", "source", "rocm"),
        "Home test-result summary with assertion-based pass rates",
    ),
    DataSpec(
        "data/vllm/ci/ci_health.json",
        ("scripts/collect_ci.py", "scripts/vllm/ci/reporter.py"),
        ("docs/assets/js/utils.js", "scripts/vllm/build_operations_snapshot.py"),
        ("generated_at", "amd", "upstream"),
        "CI Health cards and hardware test-count breakdown",
    ),
    DataSpec(
        "data/vllm/ci/parity_report.json",
        ("scripts/collect_ci.py", "scripts/vllm/ci/reporter.py"),
        ("docs/assets/js/utils.js",),
        ("generated_at", "job_groups", "amd_build", "upstream_build"),
        "ROCm/CUDA parity and Home AMD hardware breakdown",
    ),
    DataSpec(
        "data/vllm/ci/config_parity.json",
        ("scripts/collect_ci.py",),
        ("scripts/vllm/build_operations_snapshot.py",),
        ("summary", "matches", "amd_only", "nvidia_only"),
        "Commit-pinned vLLM AMD/upstream CI source-definition parity",
    ),
    DataSpec(
        "data/vllm/ci/test_group_parity.json",
        (
            "scripts/collect_ci.py",
            "scripts/vllm/build_test_group_parity.py",
        ),
        ("scripts/vllm/build_operations_snapshot.py",),
        (
            "schema_version",
            "generated_at",
            "reviewed_at",
            "source",
            "scope",
            "summary",
            "rocm_inventory",
            "areas",
            "groups",
        ),
        "Reviewed upstream CUDA-to-ROCm logical test-group inventory",
    ),
    DataSpec(
        DNS_FAILURES_DATA_PATH,
        ("scripts/vllm/collect_dns_failures.py",),
        ("docs/assets/js/ops-v2.js",),
        (
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
        ),
        "Observed DNS resolver signatures by AMD queue and physical node",
    ),
    DataSpec(
        "data/vllm/ci/ownership_config_parity.json",
        ("scripts/vllm/collect_ownership_parity.py",),
        ("scripts/vllm/ci_area_regression_watcher.py",),
        ("generated_at", "source", "summary", "matches", "amd_only", "nvidia_only"),
        "Ownership attribution pinned to the exact latest AMD nightly commit",
    ),
    DataSpec(
        "data/vllm/ci/analytics.json",
        ("scripts/vllm/collect_analytics.py",),
        (
            "scripts/vllm/build_operations_snapshot.py",
            "scripts/vllm/amd_main_failure_watcher.py",
            "scripts/vllm/ci_main_failure_watcher.py",
        ),
        ("amd-ci", "ci"),
        "Nightly comparison plus all-main reliability evidence",
    ),
    DataSpec(
        "data/vllm/ci/gating_nightlies.json",
        ("scripts/vllm/collect_analytics.py",),
        ("config/public_data_manifest.json",),
        ("generated_at", "ci", "amd-ci"),
        "Slim nightly Buildkite job signal for the AMD gating executive view",
    ),
    DataSpec(
        "data/vllm/ci/gating_targets.json",
        ("scripts/vllm/collect_gating_targets.py",),
        ("scripts/vllm/build_operations_snapshot.py",),
        ("generated_at", "summary", "groups"),
        "Canonical AMD gating target list used for still-to-gate tracking",
    ),
    DataSpec(
        "data/vllm/ci/amd_test_matrix.json",
        ("scripts/vllm/collect_amd_test_matrix.py",),
        (
            "docs/assets/js/ops-v2.js",
            "scripts/vllm/build_operations_snapshot.py",
        ),
        (
            "generated_at",
            "source",
            "summary",
            "architectures",
            "best_hardware_policy",
            "health_groups",
            "rows",
        ),
        "AMD hardware matrix, best-hardware test-group health, and cross-view counts",
    ),
    DataSpec(
        "data/vllm/ci/gating_proposals.json",
        ("scripts/vllm/collect_gating_proposals.py",),
        ("scripts/vllm/collect_gating_target_candidates.py",),
        ("generated_at", "source_repo", "tracked_authors", "summary", "pull_requests"),
        "Open PRs from tracked engineers that propose new AMD mirror gating",
    ),
    DataSpec(
        "data/vllm/ci/gating_target_candidates.json",
        ("scripts/vllm/collect_gating_target_candidates.py",),
        ("scripts/vllm/build_operations_snapshot.py",),
        ("generated_at", "source", "heuristics", "summary", "rows"),
        "Review-only daily audit for maintaining the canonical AMD gating target list",
    ),
    DataSpec(
        "data/vllm/ci/queue_timeseries.jsonl",
        ("scripts/vllm/collect_queue_snapshot.py",),
        ("docs/assets/js/ops-v2.js", "scripts/vllm/build_operations_snapshot.py"),
        (),
        "Queue charts and wait/running workload trend",
    ),
    DataSpec(
        "data/vllm/ci/queue_jobs.json",
        ("scripts/vllm/collect_queue_snapshot.py",),
        ("scripts/vllm/build_operations_snapshot.py",),
        ("ts", "pending", "running"),
        "Queue job overlays and admin triage",
    ),
    DataSpec(
        "data/vllm/ci/queue_lifecycle.json",
        ("scripts/vllm/collect_queue_lifecycle.py",),
        (
            "scripts/vllm/build_operations_snapshot.py",
            "docs/assets/js/ops-v2.js",
        ),
        (
            "schema_version",
            "generated_at",
            "window",
            "scope",
            "totals",
            "queues",
            "hourly",
            "coverage",
            "provenance",
        ),
        "Direct observed event-time lifecycle metrics for the twelve canonical AMD queues",
    ),
    DataSpec(
        "data/vllm/ci/workload_mapping.json",
        ("scripts/vllm/collect_workload_mapping.py",),
        ("scripts/vllm/build_operations_snapshot.py",),
        (
            "schema_version",
            "generated_at",
            "collection_start",
            "coverage",
            "repositories",
            "window",
            "scope",
            "semantics",
            "totals",
            "hourly",
            "daily",
        ),
        "Hourly and daily Omni CI versus main vLLM mappings onto monitored AMD queues",
    ),
    DataSpec(
        "data/vllm/ci/group_changes.json",
        ("scripts/vllm/collect_group_changes.py",),
        ("scripts/vllm/build_operations_snapshot.py",),
        ("generated_at", "changes"),
        "Test-group trend PR attribution",
    ),
    DataSpec(
        "data/vllm/ci/operations_v2.json",
        ("scripts/vllm/build_operations_snapshot.py",),
        ("docs/assets/js/ops-v2.js",),
        (
            "schema_version", "generated_at", "nightly", "reliability",
            "gating", "queue", "amd_agent_health",
        ),
        "Versioned AMD current-signal and upstream reliability read model",
    ),
    DataSpec(
        "data/vllm/ci/operations_v2_manifest.json",
        ("scripts/vllm/build_operations_snapshot.py",),
        ("docs/assets/js/ops-v2.js",),
        (
            "schema_version",
            "bundle_version",
            "generated_at",
            "shell",
            "organization_summary",
            "sections",
        ),
        "Fast operational shell and lazy evidence-section manifest",
    ),
    DataSpec(
        "data/vllm/ci/org_summary.json",
        ("scripts/vllm/build_operations_snapshot.py",),
        ("README.md",),
        (
            "schema_id",
            "schema_version",
            "generated_at",
            "project",
            "test_groups",
            "test_group_parity",
            "health_checks",
            "scheduled_cohorts",
            "parity_targets",
            "queues",
            "definitions",
            "sources",
        ),
        "Stable compact CI contract for organization-wide OSS rollups",
    ),
    DataSpec(
        "data/vllm/ci/project_items.json",
        ("scripts/collect.py",),
        ("scripts/collect.py",),
        ("generated_at", "items_by_number", "project", "project_url"),
        "Read-only GitHub Projects status and issue-state evidence",
    ),
    DataSpec(
        "data/vllm/ci/omni_surge_heuristic.json",
        ("scripts/vllm/omni_surge_watcher.py",),
        ("scripts/vllm/build_operations_snapshot.py",),
        ("generated_at", "source_status", "total_groups", "trigger", "healthy"),
        "Omni queue threshold derived from every current pipeline definition",
    ),
    DataSpec(
        "data/vllm/perf_eval/perf_eval.json",
        ("scripts/vllm/collect_perf_eval.py",),
        ("docs/assets/js/ops-v2.js",),
        ("generated_at", "pipeline", "metric_meta", "models", "summary"),
        "Webhook-fed AMD performance and accuracy series",
    ),
)


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    path: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = {"severity": self.severity, "code": self.code, "message": self.message}
        if self.path:
            out["path"] = self.path
        if self.context:
            out["context"] = self.context
        return out


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def degradations(self) -> list[Finding]:
        """Fresh, publishable defects that still require operator attention."""
        return [f for f in self.findings if f.severity == "degradation"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "errors": [f.as_dict() for f in self.errors],
            "degradations": [f.as_dict() for f in self.degradations],
            "warnings": [f.as_dict() for f in self.warnings],
            "metrics": self.metrics,
        }


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def result_count(row: dict[str, Any]) -> int:
    match = re.search(r"\((\d+)\)\s*$", str(row.get("name") or ""))
    return int(match.group(1)) if match else 1


def normalize_job_name(name: str) -> str:
    text = re.sub(r"^(mi\d+_\d+|gpu_\d+|amd_\w+):\s*", "", name or "", flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def is_amd_queue(name: str) -> bool:
    return str(name or "").startswith("amd_") or str(name or "") == "amd-cpu"


def is_retired_queue(name: str) -> bool:
    normalized = str(name or "").strip().casefold()
    return "mi355b" in normalized


def is_mi355b_queue(name: str) -> bool:
    return "mi355b" in str(name or "").casefold()


def same_repo(ref_repo: str | None, default_repo: str) -> bool:
    return (ref_repo or default_repo).lower() == default_repo.lower()


class DashboardAudit:
    def __init__(
        self,
        root: Path = ROOT,
        *,
        allow_publication_fallback: bool = True,
        publication_state_path: Path | None = None,
    ):
        self.root = root
        self.report = AuditReport()
        self._json_cache: dict[Path, Any] = {}
        self.allow_publication_fallback = allow_publication_fallback
        self.publication_state_path = (
            publication_state_path
            if publication_state_path is not None
            else self.root / PUBLICATION_STATE_RELATIVE
        )
        self._fallback_surfaces_cache: frozenset[str] | None = None

    def run(self) -> AuditReport:
        # Validate persisted fallback attestation even when no Operations source
        # happens to be stale enough to consult it during source-age checks.
        self.fallback_surfaces()
        self.audit_publication_surface_files()
        self.audit_data_inventory()
        self.audit_publication_size()
        self.audit_operations_v2()
        self.audit_operations_bundle()
        self.audit_home_pr_issue_data()
        self.audit_ci_health()
        self.audit_root_test_results()
        self.audit_shard_bases()
        self.audit_gating_target_candidates()
        self.audit_analytics()
        self.audit_amd_matrix()
        self.audit_queue_data(validate_derived=True)
        self.audit_queue_lifecycle()
        self.audit_dns_failures()
        self.audit_frontend_contracts()
        self.audit_workflows()
        return self.report

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        path: str | Path = "",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.report.findings.append(
            Finding(severity, code, message, str(path), context or {})
        )

    def error(
        self,
        code: str,
        message: str,
        path: str | Path = "",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.add("error", code, message, path, context=context)

    def warning(
        self,
        code: str,
        message: str,
        path: str | Path = "",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.add("warning", code, message, path, context=context)

    def degradation(
        self,
        code: str,
        message: str,
        path: str | Path = "",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record a fresh, publishable defect without making the audit fail."""
        self.add("degradation", code, message, path, context=context)

    def report_cross_surface_build_mismatch(
        self,
        code: str,
        message: str,
        path: str | Path,
        *,
        left_surface: str,
        left_build: object,
        right_surface: str,
        right_build: object,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Allow only build skew proven by one directionally older LKG surface.

        A split publication can intentionally combine a validated older surface
        with a newer current surface.  The restore manifest is verified by
        ``fallback_surfaces`` before this exception is considered.  Both-current,
        both-restored, non-monotonic, and unparseable mismatches remain errors.
        """
        left_number = _safe_int(left_build, -1)
        right_number = _safe_int(right_build, -1)
        fallback = self.fallback_surfaces()
        left_restored = left_surface in fallback
        right_restored = right_surface in fallback
        expected_skew = (
            left_number > 0
            and right_number > 0
            and left_restored != right_restored
            and (
                (left_restored and left_number < right_number)
                or (right_restored and right_number < left_number)
            )
        )
        if not expected_skew:
            self.error(code, message, path, context=context)
            return

        fallback_surface = left_surface if left_restored else right_surface
        fallback_build = left_number if left_restored else right_number
        current_surface = right_surface if left_restored else left_surface
        current_build = right_number if left_restored else left_number
        self.warning(
            f"{code}-fallback-skew",
            (
                f"{message}; accepted because publication state attests "
                f"{fallback_surface} build #{fallback_build} as last-known-good "
                f"while {current_surface} advanced to build #{current_build}"
            ),
            path,
            context={
                **(context or {}),
                "fallback_surface": fallback_surface,
                "fallback_build_number": fallback_build,
                "current_surface": current_surface,
                "current_build_number": current_build,
            },
        )

    def fallback_surfaces(self) -> frozenset[str]:
        if self._fallback_surfaces_cache is not None:
            return self._fallback_surfaces_cache
        if not self.allow_publication_fallback:
            self._fallback_surfaces_cache = frozenset()
            return self._fallback_surfaces_cache
        path = self.publication_state_path
        if not path.exists():
            self._fallback_surfaces_cache = frozenset()
            return self._fallback_surfaces_cache
        try:
            state = json.loads(path.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self.error(
                "publication-state-invalid",
                f"publication fallback state is unreadable: {exc}",
                self.rel(path),
            )
            self._fallback_surfaces_cache = frozenset()
            return self._fallback_surfaces_cache
        if not isinstance(state, dict):
            self.error(
                "publication-state-invalid",
                "publication state must be a JSON object",
                self.rel(path),
            )
            self._fallback_surfaces_cache = frozenset()
            return self._fallback_surfaces_cache

        def reject(message: str, *, code: str = "publication-state-invalid"):
            self.error(code, message, self.rel(path))
            self._fallback_surfaces_cache = frozenset()
            return self._fallback_surfaces_cache

        def valid_surface_list(value: object, allowed: set[str]) -> bool:
            return (
                isinstance(value, list)
                and all(isinstance(surface, str) for surface in value)
                and all(surface in allowed for surface in value)
                and len(set(value)) == len(value)
            )

        def verify_manifest(
            surface: str,
            spec: SurfaceSpec,
            entries: object,
        ) -> bool:
            entries = _migrated_publication_manifest_entries(surface, entries)
            expected_paths = (
                _publication_expected_paths(self.root, spec)
                - ignored_watcher_state_paths(surface)
            )
            if not isinstance(entries, dict) or set(entries) != expected_paths:
                self.error(
                    "publication-fallback-manifest-mismatch",
                    f"{surface} restored path set no longer matches publication state",
                    self.rel(path),
                    context={"surface": surface},
                )
                return False
            valid = True
            for relative, descriptor in entries.items():
                target = self.root / relative
                expected_size = (
                    descriptor.get("bytes") if isinstance(descriptor, dict) else None
                )
                expected_sha = (
                    descriptor.get("sha256")
                    if isinstance(descriptor, dict)
                    else None
                )
                if (
                    not target.is_file()
                    or not isinstance(expected_size, int)
                    or not re.fullmatch(r"[0-9a-f]{64}", str(expected_sha or ""))
                    or target.stat().st_size != expected_size
                    or hashlib.sha256(target.read_bytes()).hexdigest() != expected_sha
                ):
                    self.error(
                        "publication-fallback-manifest-mismatch",
                        f"{surface} restored content changed at {relative}",
                        self.rel(path),
                        context={"surface": surface, "path": relative},
                    )
                    valid = False
            return valid

        def verify_restored_paths(
            surface: str,
            entries: object,
            restored: object,
        ) -> bool:
            if not isinstance(entries, dict):
                return False
            migrated = _migrated_publication_restored_paths(surface, restored)
            if migrated == sorted(entries):
                return True
            self.error(
                "publication-fallback-manifest-mismatch",
                f"{surface} restored path list no longer matches publication state",
                self.rel(path),
                context={"surface": surface},
            )
            return False

        schema_version = state.get("schema_version")
        mode = state.get("mode")
        if (
            schema_version not in {1, 2}
            or not re.fullmatch(r"[0-9a-f]{40}", str(state.get("baseline_ref") or ""))
            or _parse_timestamp(state.get("generated_at")) is None
            or state.get("fallback_max_age_hours") != PUBLICATION_FALLBACK_MAX_AGE_HOURS
        ):
            return reject(
                "publication state has an invalid schema or common metadata"
            )

        degraded_raw = state.get("degraded_surfaces")
        degraded_since = state.get("degraded_since")
        manifest = state.get("restored_manifest")
        restored_paths = state.get("restored_paths")

        if schema_version == 1:
            aliases = _publication_legacy_aliases()
            allowed_v1 = set(SURFACE_SPECS) | set(aliases)
            if (
                mode not in {"current", "fallback", "blocked"}
                or not valid_surface_list(degraded_raw, allowed_v1)
            ):
                return reject(
                    "schema-v1 publication state has an invalid mode or surface list",
                )
            if mode == "blocked":
                return reject(
                    "publication selector state is blocked",
                    code="publication-state-blocked",
                )
            if mode == "current":
                if (
                    degraded_raw
                    or degraded_since not in ({}, None)
                    or manifest not in ({}, None)
                    or restored_paths not in ({}, None)
                ):
                    return reject(
                        "current publication state cannot declare degraded or "
                        "restored surfaces"
                    )
                self._fallback_surfaces_cache = frozenset()
                return self._fallback_surfaces_cache
            if (
                not degraded_raw
                or not isinstance(degraded_since, dict)
                or set(degraded_since) != set(degraded_raw)
                or any(
                    _parse_timestamp(value) is None
                    for value in degraded_since.values()
                )
                or not isinstance(manifest, dict)
                or set(manifest) != set(degraded_raw)
                or (
                    restored_paths is not None
                    and (
                        not isinstance(restored_paths, dict)
                        or set(restored_paths) != set(degraded_raw)
                    )
                )
            ):
                return reject(
                    "publication state lacks complete degradation or fallback "
                    "attestations"
                )

            # Verify the committed schema-v1 transaction as one monolith
            # before translating its proof and clock to active child surfaces.
            raw_valid = True
            for surface in degraded_raw:
                spec = (
                    LEGACY_CI_SURFACE_SPEC
                    if surface == LEGACY_CI_SURFACE and surface in aliases
                    else SURFACE_SPECS[surface]
                )
                migrated_entries = _migrated_publication_manifest_entries(
                    surface, manifest[surface]
                )
                raw_valid = verify_manifest(
                    surface, spec, manifest[surface]
                ) and raw_valid
                if restored_paths is not None:
                    raw_valid = verify_restored_paths(
                        surface,
                        migrated_entries,
                        restored_paths.get(surface),
                    ) and raw_valid
            if not raw_valid:
                self._fallback_surfaces_cache = frozenset()
                return self._fallback_surfaces_cache
            try:
                expansions = _publication_surface_expansions(degraded_raw, aliases)
                partitioned_manifest = _partition_publication_manifest(
                    self.root, manifest, expansions
                )
            except ValueError as exc:
                return reject(str(exc))
            fallback_surfaces = {
                target for targets in expansions.values() for target in targets
            }
            if _publication_fallback_closure(fallback_surfaces) != fallback_surfaces:
                return reject(
                    "schema-v1 fallback omits a required dependent surface"
                )
            fallback_since = {
                target: degraded_since[surface]
                for surface, targets in expansions.items()
                for target in targets
            }
            manifest = partitioned_manifest
        else:
            fresh_raw = state.get("fresh_degraded_surfaces")
            fallback_raw = state.get("fallback_surfaces")
            fallback_since = state.get("fallback_since")
            allowed_v2 = set(SURFACE_SPECS)
            if (
                mode not in {"current", "degraded", "fallback", "mixed", "blocked"}
                or not valid_surface_list(degraded_raw, allowed_v2)
                or not valid_surface_list(fresh_raw, allowed_v2)
                or not valid_surface_list(fallback_raw, allowed_v2)
            ):
                return reject(
                    "schema-v2 publication state has an invalid mode or surface list",
                )

            degraded_set = set(degraded_raw)
            fresh_set = set(fresh_raw)
            fallback_set = set(fallback_raw)
            mode_sets_are_valid = {
                "current": not fresh_set and not fallback_set,
                "degraded": bool(fresh_set) and not fallback_set,
                "fallback": not fresh_set and bool(fallback_set),
                "mixed": bool(fresh_set) and bool(fallback_set),
                # A blocked selector may have stopped before producing complete
                # timing or manifest attestations, but its surface union must
                # still be internally consistent.
                "blocked": True,
            }
            if (
                fresh_set & fallback_set
                or degraded_set != fresh_set | fallback_set
                or not mode_sets_are_valid[mode]
            ):
                return reject(
                    "schema-v2 degraded surfaces do not match their selection modes",
                )
            if mode == "blocked":
                return reject(
                    "publication selector state is blocked",
                    code="publication-state-blocked",
                )
            if (
                mode == "current"
                and (
                    degraded_set
                    or degraded_since not in ({}, None)
                    or fallback_since not in ({}, None)
                    or manifest not in ({}, None)
                    or restored_paths not in ({}, None)
                )
            ):
                return reject(
                    "current publication state cannot declare degraded or restored "
                    "surfaces"
                )
            if (
                not isinstance(degraded_since, dict)
                or set(degraded_since) != degraded_set
                or any(
                    _parse_timestamp(value) is None
                    for value in degraded_since.values()
                )
                or not isinstance(fallback_since, dict)
                or set(fallback_since) != fallback_set
                or any(
                    _parse_timestamp(value) is None
                    for value in fallback_since.values()
                )
                or (
                    fallback_set
                    and (not isinstance(manifest, dict) or set(manifest) != fallback_set)
                )
                or (not fallback_set and manifest not in ({}, None))
                or (
                    fallback_set
                    and restored_paths is not None
                    and (
                        not isinstance(restored_paths, dict)
                        or set(restored_paths) != fallback_set
                    )
                )
                or (not fallback_set and restored_paths not in ({}, None))
            ):
                return reject(
                    "publication state lacks complete degradation or fallback "
                    "attestations"
                )
            pre_analytics_contract = (
                state.get("surface_contract_version")
                != SURFACE_CONTRACT_VERSION
                and bool({"ci_core", "ci_gating"} & degraded_set)
            )
            if pre_analytics_contract and fallback_set:
                if "ci_core" in fallback_set and "ci_gating" not in fallback_set:
                    return reject(
                        "pre-analytics ci_core fallback omits its legacy gating dependent"
                    )

                # Verify every byte against the exact pre-split ownership
                # contract before repartitioning its proof.
                verified_entries: dict[str, dict] = {}
                raw_valid = True
                for surface in sorted(fallback_set):
                    if surface == "ci_core":
                        spec = PRE_ANALYTICS_CI_CORE_SURFACE_SPEC
                    elif surface == "ci_gating":
                        spec = PRE_ANALYTICS_CI_GATING_SURFACE_SPEC
                    else:
                        spec = SURFACE_SPECS[surface]
                    entries = _migrated_publication_manifest_entries(
                        surface, manifest[surface]
                    )
                    raw_valid = verify_manifest(
                        surface, spec, manifest[surface]
                    ) and raw_valid
                    if restored_paths is not None:
                        raw_valid = verify_restored_paths(
                            surface,
                            entries,
                            restored_paths.get(surface),
                        ) and raw_valid
                    if isinstance(entries, dict):
                        verified_entries[surface] = entries
                if not raw_valid:
                    self._fallback_surfaces_cache = frozenset()
                    return self._fallback_surfaces_cache

                partitioned: dict[str, dict] = {}
                expanded_since: dict[str, str] = {}
                for surface, entries in verified_entries.items():
                    targets = (
                        frozenset({"ci_core", "ci_analytics", "ci_gating"})
                        if surface == "ci_core"
                        else frozenset({surface})
                    )
                    source_since = fallback_since[surface]
                    for target in targets:
                        existing_since = expanded_since.get(target)
                        if (
                            existing_since is None
                            or _parse_timestamp(source_since)
                            < _parse_timestamp(existing_since)
                        ):
                            expanded_since[target] = source_since
                    for relative, descriptor in entries.items():
                        owners = [
                            target
                            for target in targets
                            if _publication_spec_owns_path(
                                SURFACE_SPECS[target], relative
                            )
                        ]
                        if len(owners) != 1:
                            return reject(
                                "pre-analytics fallback path lacks one active owner"
                            )
                        owner = owners[0]
                        target_entries = partitioned.setdefault(owner, {})
                        if relative in target_entries:
                            return reject(
                                "pre-analytics fallback path is duplicated"
                            )
                        target_entries[relative] = descriptor

                expanded_fallback = set(expanded_since)
                for surface in sorted(expanded_fallback):
                    entries = partitioned.get(surface, {})
                    expected = (
                        _publication_expected_paths(
                            self.root, SURFACE_SPECS[surface]
                        )
                        - ignored_watcher_state_paths(surface)
                    )
                    missing = expected - set(entries)
                    if surface == "ci_gating" and missing == {
                        "data/vllm/ci/gating_nightlies.json"
                    }:
                        # Early schema-v2 gating-only states predate nightly's
                        # move into this surface. Add that one proof only when
                        # the deployed bytes still match immutable HEAD.
                        descriptor = _publication_head_descriptor(
                            self.root,
                            "data/vllm/ci/gating_nightlies.json",
                        )
                        if descriptor is not None:
                            entries["data/vllm/ci/gating_nightlies.json"] = (
                                descriptor
                            )
                    if set(entries) != expected:
                        return reject(
                            f"pre-analytics fallback partition for {surface} is incomplete",
                            code="publication-fallback-manifest-mismatch",
                        )
                fallback_surfaces = expanded_fallback
                fallback_since = expanded_since
                manifest = partitioned
                restored_paths = {
                    surface: sorted(entries)
                    for surface, entries in partitioned.items()
                }
            else:
                fallback_surfaces = fallback_set
            if _publication_fallback_closure(fallback_surfaces) != fallback_surfaces:
                return reject(
                    "schema-v2 fallback omits a required dependent surface"
                )
            if not fallback_surfaces:
                self._fallback_surfaces_cache = frozenset()
                return self._fallback_surfaces_cache
            valid = True
            for surface in sorted(fallback_surfaces):
                migrated_entries = _migrated_publication_manifest_entries(
                    surface, manifest[surface]
                )
                valid = verify_manifest(
                    surface, SURFACE_SPECS[surface], manifest[surface]
                ) and valid
                if restored_paths is not None:
                    valid = verify_restored_paths(
                        surface,
                        migrated_entries,
                        restored_paths.get(surface),
                    ) and valid
            if not valid:
                self._fallback_surfaces_cache = frozenset()
                return self._fallback_surfaces_cache

        now = datetime.now(timezone.utc)
        valid = True
        for surface in sorted(fallback_surfaces):
            since = _parse_timestamp(fallback_since[surface])
            age_hours = (now - since).total_seconds() / 3600 if since else float("inf")
            if age_hours > PUBLICATION_FALLBACK_MAX_AGE_HOURS:
                self.error(
                    "publication-fallback-expired",
                    (
                        f"{surface} has used last-known-good data for {age_hours:.1f}h; "
                        f"the hard limit is {PUBLICATION_FALLBACK_MAX_AGE_HOURS}h"
                    ),
                    self.rel(path),
                    context={"surface": surface},
                )
                valid = False
        self._fallback_surfaces_cache = (
            frozenset(fallback_surfaces) if valid else frozenset()
        )
        return self._fallback_surfaces_cache

    def audit_publication_surface_files(self) -> None:
        """Parse every atomic source input before it can be selected as current."""
        inspected: set[str] = set()
        for spec in SURFACE_SPECS.values():
            paths: list[tuple[str, bool]] = [
                *((relative, True) for relative in spec.required_paths),
                *((relative, False) for relative in spec.optional_paths),
            ]
            paths.extend(
                (candidate.relative_to(self.root).as_posix(), False)
                for pattern in spec.globs
                for candidate in self.root.glob(pattern)
                if candidate.is_file()
            )
            for relative, required in paths:
                if relative in inspected:
                    continue
                inspected.add(relative)
                path = self.root / relative
                if not path.is_file():
                    if required:
                        self.error(
                            "publication-source-missing",
                            f"atomic publication source {relative} is missing",
                            relative,
                        )
                    continue
                if relative.endswith(".json"):
                    payload = self.load_json(relative, None)
                    if payload is None:
                        continue
                    required_keys = PUBLICATION_SURFACE_REQUIRED_KEYS.get(relative)
                    if relative == "data/vllm/ci/shard_bases.json" and (
                        not isinstance(payload, list)
                        or not payload
                        or any(
                            not isinstance(base, str) or not base.strip()
                            for base in payload
                        )
                    ):
                        self.error(
                            "publication-source-shape",
                            "shard_bases.json must be a non-empty list of labels",
                            relative,
                        )
                    if required_keys:
                        if not isinstance(payload, dict):
                            self.error(
                                "publication-source-shape",
                                f"{relative} must be a JSON object",
                                relative,
                            )
                        elif missing := required_keys - set(payload):
                            self.error(
                                "publication-source-shape",
                                f"{relative} is missing required keys {sorted(missing)}",
                                relative,
                            )
                elif relative.endswith(".jsonl"):
                    self.load_jsonl(relative)

    def _validate_shard_evidence(
        self,
        catalog: dict[str, Any],
        catalog_path: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Validate complete, provisional, and unavailable evidence states."""
        evidence = catalog.get("evidence")

        def invalid(message: str) -> tuple[None, None]:
            self.error(
                "publication-source-shape",
                f"{catalog_path} evidence {message}",
                catalog_path,
            )
            return None, None

        if not isinstance(evidence, dict):
            return invalid("must be a JSON object")
        if missing := SHARD_EVIDENCE_REQUIRED_KEYS - set(evidence):
            return invalid(f"is missing required keys {sorted(missing)}")

        pipeline = evidence.get("pipeline")
        build_number = evidence.get("build_number")
        build_commit = evidence.get("build_commit")
        build_state = evidence.get("build_state")
        roster_complete = evidence.get("roster_complete")
        result_file = evidence.get("result_file")
        job_names = evidence.get("job_names")
        if pipeline != "amd":
            return invalid("must identify the amd pipeline")
        if (
            isinstance(build_number, bool)
            or not isinstance(build_number, int)
            or build_number < 0
        ):
            return invalid("build_number must be a non-negative integer")
        if not isinstance(build_commit, str):
            return invalid("build_commit must be a string")
        if not isinstance(build_state, str) or not build_state:
            return invalid("build_state must be a non-empty string")
        if type(roster_complete) is not bool:
            return invalid("roster_complete must be a boolean")
        if not isinstance(result_file, str):
            return invalid("result_file must be a string")
        if (
            not isinstance(job_names, list)
            or any(not isinstance(name, str) or not name for name in job_names)
            or job_names != sorted(set(job_names))
        ):
            return invalid("job_names must be a sorted list of unique non-empty strings")

        if build_number == 0:
            if (
                build_commit
                or build_state != "unavailable"
                or roster_complete
                or result_file
                or job_names
            ):
                return invalid(
                    "build #0 must use the exact unavailable sentinel state"
                )
            return evidence, "unavailable"

        if not FULL_COMMIT_SHA_RE.fullmatch(build_commit.casefold()):
            return invalid("build_commit must be a full 40-character SHA")
        if not roster_complete:
            if result_file:
                return invalid("provisional evidence must not reference a result_file")
            return evidence, "provisional"

        if build_state.casefold() not in SHARD_TERMINAL_BUILD_STATES:
            return invalid("complete evidence must identify a terminal build state")
        if (
            not result_file
            or Path(result_file).name != result_file
            or not SHARD_RESULT_FILE_RE.fullmatch(result_file)
        ):
            return invalid(
                "complete evidence result_file must be a canonical AMD JSONL basename"
            )
        if not job_names:
            return invalid("complete evidence must include at least one job name")

        source = catalog.get("source")
        source_commit = str(
            source.get("commit_sha") if isinstance(source, dict) else ""
        ).casefold()
        if (
            not FULL_COMMIT_SHA_RE.fullmatch(source_commit)
            or source_commit != build_commit.casefold()
        ):
            self.error(
                "shard-config-evidence-mismatch",
                (
                    "shard definitions are not pinned to the AMD evidence "
                    f"commit ({source_commit or 'missing'} != "
                    f"{build_commit.casefold() or 'missing'})"
                ),
                catalog_path,
            )
            return None, None
        return evidence, "complete"

    def audit_shard_bases(self) -> None:
        """Keep AMD-owned shard normalization aligned with AMD evidence."""
        relpath = "data/vllm/ci/shard_bases.json"
        bases = self.load_json(relpath, [])
        catalog_path = "data/vllm/ci/shard_base_catalog.json"
        catalog = (
            self.load_json(catalog_path, {})
            if (self.root / catalog_path).exists()
            else {}
        )
        if not isinstance(catalog, dict) or not catalog:
            self.warning(
                "shard-base-catalog-missing",
                "pipeline shard provenance is unavailable; skipping runtime absence audit",
                catalog_path,
            )
            return
        evidence, evidence_state = self._validate_shard_evidence(
            catalog,
            catalog_path,
        )
        if evidence is None:
            return
        if evidence_state == "unavailable":
            self.warning(
                "shard-evidence-unavailable",
                "AMD shard evidence is unavailable; skipping runtime absence checks",
                catalog_path,
            )
            return
        if evidence_state == "provisional":
            self.warning(
                "shard-evidence-provisional",
                "AMD shard roster is incomplete; absence checks are provisional",
                catalog_path,
            )
            return
        latest = self.latest_result_file("amd")
        if not isinstance(bases, list) or not bases or latest is None:
            return
        audited_bases = bases
        if isinstance(catalog, dict):
            pipelines = catalog.get("pipelines")
            if isinstance(pipelines, dict) and isinstance(pipelines.get("amd"), list):
                audited_bases = pipelines["amd"]
            result_file = str(evidence.get("result_file") or "")
            latest = self.root / "data/vllm/ci/test_results" / result_file
        from vllm.ci import analyzer

        previous = list(analyzer._SHARD_BASES)
        try:
            analyzer.set_shard_bases(bases)
            roster_names = evidence.get("job_names")
            if isinstance(roster_names, list):
                normalized = {
                    analyzer._normalize_job_name(str(name or ""))
                    for name in roster_names
                }
            else:
                normalized = {
                    analyzer._normalize_job_name(str(row.get("job_name") or ""))
                    for row in self.load_jsonl(self.rel(latest))
                }
        finally:
            analyzer.set_shard_bases(previous)
        unused = sorted(
            str(base).casefold()
            for base in audited_bases
            if not any(name.startswith(str(base).casefold()) for name in normalized)
        )
        definitions = catalog.get("definitions") if isinstance(catalog, dict) else []
        optional_bases = {
            str(row.get("base") or "").casefold()
            for row in definitions or []
            if isinstance(row, dict)
            and row.get("pipeline") == "amd"
            and row.get("optional") is True
        }
        required_bases = {
            str(row.get("base") or "").casefold()
            for row in definitions or []
            if isinstance(row, dict)
            and row.get("pipeline") == "amd"
            and row.get("optional") is not True
        }
        optional_bases -= required_bases
        optional_unused = sorted(set(unused) & optional_bases)
        unused = sorted(set(unused) - optional_bases)
        if optional_unused:
            self.warning(
                "shard-bases-optional-unobserved",
                (
                    f"{len(optional_unused)} optional AMD shard bases are absent "
                    f"from completed AMD evidence: {optional_unused}"
                ),
                relpath,
            )
        if unused:
            self.degradation(
                "shard-bases-unused",
                (
                    f"{len(unused)} AMD shard bases are absent from the latest AMD test "
                    f"evidence: {unused}"
                ),
                relpath,
            )

    def load_json(self, relpath: str, default: Any = None) -> Any:
        path = self.root / relpath
        if path in self._json_cache:
            return self._json_cache[path]
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            self.error("missing-json", f"{relpath} is missing", relpath)
            return default
        except json.JSONDecodeError as exc:
            self.error("invalid-json", f"{relpath} is not valid JSON: {exc}", relpath)
            return default
        self._json_cache[path] = data
        return data

    def load_jsonl(self, relpath: str) -> list[dict[str, Any]]:
        path = self.root / relpath
        rows: list[dict[str, Any]] = []
        try:
            lines = path.read_text().splitlines()
        except FileNotFoundError:
            self.error("missing-jsonl", f"{relpath} is missing", relpath)
            return rows
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                self.error(
                    "invalid-jsonl-row",
                    f"{relpath}:{line_no} is not valid JSON: {exc}",
                    relpath,
                )
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                self.error(
                    "invalid-jsonl-row",
                    f"{relpath}:{line_no} is {type(row).__name__}, expected object",
                    relpath,
                )
        return rows

    def latest_result_file(self, suffix: str) -> Path | None:
        paths = sorted((self.root / "data/vllm/ci/test_results").glob(f"*_{suffix}.jsonl"))
        return paths[-1] if paths else None

    def build_numbers_in_jsonl(self, path: Path | None) -> set[int]:
        if path is None:
            return set()
        numbers: set[int] = set()
        for raw in path.read_text().splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            try:
                numbers.add(int(row.get("build_number") or 0))
            except (TypeError, ValueError):
                continue
        return {n for n in numbers if n}

    def audit_data_inventory(self) -> None:
        inventory: list[dict[str, Any]] = []
        for spec in DATA_SPECS:
            path = self.root / spec.relpath
            exists = path.exists()
            inventory.append(
                {
                    "path": spec.relpath,
                    "exists": exists,
                    "description": spec.description,
                    "producers": spec.producers,
                    "consumers": spec.consumers,
                }
            )
            if not exists:
                self.error("missing-data-file", f"{spec.relpath} is missing", spec.relpath)
                continue

            if spec.relpath.endswith(".json"):
                payload = self.load_json(spec.relpath, {})
                if isinstance(payload, dict):
                    missing = set(spec.required_keys) - set(payload.keys())
                    if missing:
                        self.error(
                            "missing-data-keys",
                            f"{spec.relpath} missing required keys {sorted(missing)}",
                            spec.relpath,
                        )
                else:
                    self.error(
                        "data-shape",
                        f"{spec.relpath} is {type(payload).__name__}, expected object",
                        spec.relpath,
                    )
            elif spec.relpath.endswith(".jsonl"):
                if not self.load_jsonl(spec.relpath):
                    self.error("empty-jsonl", f"{spec.relpath} has no valid rows", spec.relpath)

            basename = Path(spec.relpath).name
            producer_mentions = False
            for producer in spec.producers:
                producer_path = self.root / producer
                if not producer_path.exists():
                    self.error("missing-producer", f"Producer {producer} is missing", producer)
                    continue
                if basename in producer_path.read_text(errors="ignore"):
                    producer_mentions = True
            if not producer_mentions:
                self.warning(
                    "producer-lineage",
                    f"No listed producer mentions {basename}; lineage may be stale",
                    spec.relpath,
                )
            for consumer in spec.consumers:
                consumer_path = self.root / consumer
                if not consumer_path.exists():
                    self.error("missing-consumer", f"Consumer {consumer} is missing", consumer)
                    continue
                if basename not in consumer_path.read_text(errors="ignore"):
                    self.warning(
                        "consumer-lineage",
                        f"{consumer} does not mention {basename}; frontend contract may be stale",
                        consumer,
                    )
        self.report.metrics["data_inventory"] = inventory

    def audit_publication_size(self) -> None:
        """Keep the static publication below explicit per-file and total budgets."""
        manifest_path = self.root / "config/public_data_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            self.error(
                "public-manifest-invalid",
                f"Could not read the public data manifest: {exc}",
                self.rel(manifest_path),
            )
            return

        data_root = self.root / "data"
        published_sizes: dict[str, int] = {}
        for manifest_field in ("required_files", "optional_files"):
            for relative in manifest.get(manifest_field) or []:
                path = data_root / str(relative)
                if path.is_file():
                    published_sizes[str(relative)] = path.stat().st_size
        for pattern in manifest.get("optional_globs") or []:
            for path in data_root.glob(str(pattern)):
                if path.is_file():
                    published_sizes[path.relative_to(data_root).as_posix()] = (
                        path.stat().st_size
                    )

        projected_budgets: dict[str, int] = {}
        projected_files = manifest.get("projected_files") or []
        if not isinstance(projected_files, list):
            self.error(
                "public-manifest-projections",
                "public_data_manifest.json projected_files must be a list",
                "config/public_data_manifest.json",
            )
            projected_files = []
        for index, descriptor in enumerate(projected_files):
            if not isinstance(descriptor, dict):
                self.error(
                    "public-manifest-projection",
                    f"projected_files[{index}] must be an object",
                    "config/public_data_manifest.json",
                )
                continue
            relative = descriptor.get("path")
            maximum = descriptor.get("max_bytes")
            safe_path = (
                isinstance(relative, str)
                and bool(relative)
                and not PurePosixPath(relative).is_absolute()
                and ".." not in PurePosixPath(relative).parts
            )
            if not safe_path or (
                isinstance(maximum, bool)
                or not isinstance(maximum, int)
                or maximum <= 0
            ):
                self.error(
                    "public-manifest-projection",
                    (
                        f"projected_files[{index}] needs a safe path and positive "
                        "integer max_bytes"
                    ),
                    "config/public_data_manifest.json",
                )
                continue
            # The source at data/<path> is the full private build input. It may
            # be much larger than the bounded artifact written to _site, so use
            # the projection contract's ceiling for the public-site estimate.
            published_sizes[relative] = maximum
            projected_budgets[relative] = maximum

        operations_manifest_path = (
            self.root / "data/vllm/ci/operations_v2_manifest.json"
        )
        operations_manifest = (
            self.load_json("data/vllm/ci/operations_v2_manifest.json", {})
            if operations_manifest_path.exists()
            else {}
        )
        if isinstance(operations_manifest, dict):
            for descriptor in _mapping(operations_manifest.get("sections")).values():
                if not isinstance(descriptor, dict) or not descriptor.get("path"):
                    continue
                relative = f"vllm/ci/{descriptor['path']}"
                published_sizes[relative] = _safe_int(descriptor.get("bytes"))
            org_descriptor = _mapping(
                operations_manifest.get("organization_summary")
            )
            if org_descriptor.get("path"):
                relative = f"vllm/ci/{org_descriptor['path']}"
                published_sizes[relative] = _safe_int(org_descriptor.get("bytes"))

        for relative, size in sorted(published_sizes.items()):
            if size > PUBLIC_FILE_HARD_BYTES:
                self.error(
                    "public-file-budget",
                    (
                        f"{relative} is {size} bytes; the public-file hard budget is "
                        f"{PUBLIC_FILE_HARD_BYTES} bytes"
                    ),
                    f"data/{relative}",
                )
            elif size > PUBLIC_FILE_WARN_BYTES:
                self.warning(
                    "public-file-near-budget",
                    (
                        f"{relative} is {size} bytes; warning budget is "
                        f"{PUBLIC_FILE_WARN_BYTES} bytes"
                    ),
                    f"data/{relative}",
                )

        total = sum(published_sizes.values())
        if total > PUBLIC_SITE_WARN_BYTES:
            self.warning(
                "public-site-payload",
                (
                    f"Allowlisted public data is approximately {total} bytes; "
                    f"warning budget is {PUBLIC_SITE_WARN_BYTES} bytes"
                ),
                "config/public_data_manifest.json",
            )
        self.report.metrics["publication_size"] = {
            "estimated_bytes": total,
            "projected_budget_bytes": sum(projected_budgets.values()),
            "projected_files": projected_budgets,
            "largest_files": dict(
                sorted(
                    published_sizes.items(),
                    key=lambda item: (-item[1], item[0]),
                )[:10]
            ),
        }

    def audit_operations_v2(self) -> None:
        relpath = "data/vllm/ci/operations_v2.json"
        payload = self.load_json(relpath, {})
        if not isinstance(payload, dict):
            return
        if payload.get("schema_version") != 2:
            self.error("operations-schema", "operations_v2.json must use schema_version 2", relpath)

        generated_at = _parse_timestamp(payload.get("generated_at"))
        source_ages: dict[str, float] = {}
        if generated_at is not None:
            sources = _mapping(payload.get("sources"))
            for source_name in sorted(OPERATIONS_FRESH_SOURCE_KEYS):
                source = _mapping(sources.get(source_name))
                source_at = _parse_timestamp(source.get("timestamp"))
                if source_at is None:
                    self.error(
                        "operations-source-timestamp",
                        f"operations source {source_name} has no valid timestamp",
                        relpath,
                        context={"source": source_name},
                    )
                    continue
                if source.get("timestamp_source") in {
                    None,
                    "",
                    "file_mtime",
                    "missing",
                }:
                    self.error(
                        "operations-source-provenance",
                        (
                            f"operations source {source_name} freshness comes from "
                            f"{source.get('timestamp_source') or 'an unspecified source'}; "
                            "a payload observation timestamp is required"
                        ),
                        relpath,
                        context={"source": source_name},
                    )
                age_hours = (generated_at - source_at).total_seconds() / 3600
                source_ages[source_name] = round(age_hours, 2)
                if age_hours < -1:
                    self.error(
                        "operations-source-from-future",
                        (
                            f"operations source {source_name} is {-age_hours:.1f}h newer "
                            "than the snapshot that embeds it"
                        ),
                        relpath,
                        context={"source": source_name},
                    )
                max_age_hours = OPERATIONS_SOURCE_MAX_AGE_OVERRIDES.get(
                    source_name, OPERATIONS_SOURCE_MAX_AGE_HOURS
                )
                if age_hours >= -1 and age_hours > max_age_hours:
                    source_surface = SOURCE_SURFACES.get(source_name)
                    context = {"source": source_name}
                    if (
                        source_surface in self.fallback_surfaces()
                        and age_hours <= PUBLICATION_FALLBACK_MAX_AGE_HOURS
                    ):
                        self.warning(
                            "operations-stale-source-fallback",
                            (
                                f"operations source {source_name} is {age_hours:.1f}h older "
                                "than the snapshot because its source surface is using "
                                "last-known-good data"
                            ),
                            relpath,
                            context=context,
                        )
                    else:
                        self.error(
                            "operations-stale-source",
                            (
                                f"operations source {source_name} is {age_hours:.1f}h older "
                                f"than the snapshot; maximum is {max_age_hours}h"
                            ),
                            relpath,
                            context=context,
                        )

        amd_test_health = _mapping(payload.get("amd_test_health"))
        amd_health_summary = _mapping(amd_test_health.get("summary"))
        amd_latest_logical_counts = _mapping(
            amd_health_summary.get("latest_test_group_counts")
        )
        amd_health_builds = _rows(amd_test_health.get("builds"))
        amd_health_catalog = _rows(amd_test_health.get("group_catalog"))
        if amd_test_health:
            retained_count = _safe_int(
                amd_health_summary.get("retained_group_count")
                or amd_health_summary.get("union_group_count")
                or amd_health_summary.get("group_count")
            )
            declared_catalog_counts = {
                "retained_group_count": _safe_int(
                    amd_health_summary.get("retained_group_count"), retained_count
                ),
                "group_count": _safe_int(amd_health_summary.get("group_count")),
                "union_group_count": _safe_int(
                    amd_health_summary.get("union_group_count")
                ),
            }
            for field_name, declared in declared_catalog_counts.items():
                if declared != len(amd_health_catalog):
                    self.error(
                        "operations-amd-retained-count",
                        (
                            f"amd_test_health.summary.{field_name}={declared} but "
                            f"the retained catalog has {len(amd_health_catalog)} rows"
                        ),
                        relpath,
                    )
            if (
                "retained_job_variant_count" in amd_health_summary
                and _safe_int(
                    amd_health_summary.get("retained_job_variant_count")
                ) != retained_count
            ):
                self.error(
                    "operations-amd-retained-job-variant-alias",
                    "retained job-variant count disagrees with the legacy catalog count",
                    relpath,
                )

            if _safe_int(amd_health_summary.get("build_count")) != len(amd_health_builds):
                self.error(
                    "operations-amd-build-count",
                    (
                        f"amd_test_health.summary.build_count="
                        f"{amd_health_summary.get('build_count')} but "
                        f"{len(amd_health_builds)} build rows are published"
                    ),
                    relpath,
                )

            latest_counts = _mapping(amd_health_summary.get("latest_state_counts"))
            latest_count = _safe_int(amd_health_summary.get("latest_group_count"))
            latest_job_variant_count = _safe_int(
                amd_health_summary.get("latest_job_variant_count"),
                latest_count,
            )
            latest_job_variant_counts = _mapping(
                amd_health_summary.get("latest_job_variant_state_counts")
            )
            if latest_job_variant_count != latest_count:
                self.error(
                    "operations-amd-latest-job-variant-alias",
                    "latest job-variant count disagrees with latest_group_count",
                    relpath,
                )
            if latest_job_variant_counts and any(
                _safe_int(latest_job_variant_counts.get(state))
                != _safe_int(latest_counts.get(state))
                for state in ("passed", "soft", "hard", "unknown")
            ):
                self.error(
                    "operations-amd-latest-job-variant-state-alias",
                    "latest job-variant state counts disagree with legacy state counts",
                    relpath,
                )
            latest_state_total = sum(
                _safe_int(latest_counts.get(state))
                for state in ("passed", "soft", "hard", "unknown")
            )
            if latest_count != latest_state_total:
                self.error(
                    "operations-amd-latest-state-count",
                    (
                        f"latest_group_count={latest_count} but latest state counts "
                        f"sum to {latest_state_total}"
                    ),
                    relpath,
                )

            latest_build_number = _safe_int(
                amd_health_summary.get("latest_build_number")
            )
            logical_counts = amd_latest_logical_counts
            if logical_counts:
                required_logical_fields = {
                    "available",
                    "build_number",
                    "job_variant_build_number",
                    "test_signal_build_number",
                    "total",
                    "passing",
                    "non_passing",
                    "passing_all",
                    "partial",
                    "pass_percentage",
                    "source",
                    "reason",
                }
                missing_logical_fields = required_logical_fields - set(logical_counts)
                if missing_logical_fields:
                    self.error(
                        "operations-amd-logical-group-shape",
                        (
                            "latest logical test-group counts omit "
                            f"{sorted(missing_logical_fields)}"
                        ),
                        relpath,
                    )
                if logical_counts.get("source") != (
                    "ci_health.amd.latest_test_signal_build"
                ):
                    self.error(
                        "operations-amd-logical-group-source",
                        "latest logical test groups must come from the test-signal build",
                        relpath,
                    )
                if _safe_int(
                    logical_counts.get("job_variant_build_number")
                ) != latest_build_number:
                    self.error(
                        "operations-amd-logical-group-job-build",
                        "logical group counts do not identify the latest job-variant build",
                        relpath,
                    )
                if logical_counts.get("available") is True:
                    logical_build = _safe_int(logical_counts.get("build_number"))
                    signal_build = _safe_int(
                        logical_counts.get("test_signal_build_number")
                    )
                    if not (
                        logical_build
                        == signal_build
                        == latest_build_number
                    ):
                        self.error(
                            "operations-amd-logical-group-build-mismatch",
                            (
                                "available logical test-group counts must match the "
                                "latest exact job-variant build"
                            ),
                            relpath,
                        )
                    logical_total = _safe_int(logical_counts.get("total"), -1)
                    logical_passing = _safe_int(logical_counts.get("passing"), -1)
                    logical_non_passing = _safe_int(
                        logical_counts.get("non_passing"), -1
                    )
                    logical_passing_all = _safe_int(
                        logical_counts.get("passing_all"), -1
                    )
                    logical_partial = _safe_int(
                        logical_counts.get("partial"), -1
                    )
                    if not (
                        0 <= logical_passing_all <= logical_passing <= logical_total
                        and logical_non_passing == logical_total - logical_passing
                        and logical_passing_all + logical_partial == logical_passing
                    ):
                        self.error(
                            "operations-amd-logical-group-counts",
                            "latest logical test-group counts are internally inconsistent",
                            relpath,
                        )
                    if logical_total > latest_job_variant_count:
                        self.error(
                            "operations-amd-logical-groups-exceed-job-variants",
                            (
                                f"logical test groups={logical_total} exceed exact "
                                f"job variants={latest_job_variant_count}"
                            ),
                            relpath,
                        )
                    expected_percentage = (
                        round(logical_passing / logical_total * 100, 1)
                        if logical_total > 0
                        else None
                    )
                    declared_percentage = logical_counts.get("pass_percentage")
                    if (
                        expected_percentage is None
                        and declared_percentage is not None
                    ) or (
                        expected_percentage is not None
                        and not math.isclose(
                            _safe_float(declared_percentage, -1.0),
                            expected_percentage,
                            abs_tol=0.05,
                        )
                    ):
                        self.error(
                            "operations-amd-logical-group-percentage",
                            "logical test-group pass percentage disagrees with its counts",
                            relpath,
                        )
                elif logical_counts.get("build_number") is not None:
                    self.error(
                        "operations-amd-logical-group-unavailable-build",
                        "unavailable logical test-group counts must not claim a build",
                        relpath,
                    )
            current_catalog_rows = sum(
                _safe_int(_mapping(row).get("latest_build_number"))
                == latest_build_number
                for row in amd_health_catalog
            )
            if latest_build_number and current_catalog_rows != latest_count:
                self.error(
                    "operations-amd-latest-catalog-count",
                    (
                        f"latest build #{latest_build_number} has "
                        f"{current_catalog_rows} current catalog rows but summary "
                        f"reports {latest_count}"
                    ),
                    relpath,
                )

            latest_build = next(
                (
                    _mapping(row)
                    for row in amd_health_builds
                    if _safe_int(_mapping(row).get("build_number"))
                    == latest_build_number
                ),
                {},
            )
            for raw_build in amd_health_builds:
                build = _mapping(raw_build)
                state_counts = _mapping(build.get("state_counts"))
                state_total = sum(
                    _safe_int(state_counts.get(state))
                    for state in ("passed", "soft", "hard", "unknown")
                )
                observed = _safe_int(
                    build.get("observed") or build.get("observed_groups")
                )
                if (
                    "observed_job_variants" in build
                    and _safe_int(build.get("observed_job_variants")) != observed
                ):
                    self.error(
                        "operations-amd-build-job-variant-alias",
                        (
                            f"AMD build #{build.get('build_number')} job-variant "
                            "count disagrees with its legacy observed count"
                        ),
                        relpath,
                    )
                job_variant_state_counts = _mapping(
                    build.get("job_variant_state_counts")
                )
                if job_variant_state_counts and any(
                    _safe_int(job_variant_state_counts.get(state))
                    != _safe_int(state_counts.get(state))
                    for state in ("passed", "soft", "hard", "unknown")
                ):
                    self.error(
                        "operations-amd-build-job-variant-state-alias",
                        (
                            f"AMD build #{build.get('build_number')} job-variant "
                            "state counts disagree with legacy state counts"
                        ),
                        relpath,
                    )
                if observed != state_total:
                    self.error(
                        "operations-amd-build-state-count",
                        (
                            f"AMD build #{build.get('build_number')} reports "
                            f"{observed} observed variants but state counts sum to "
                            f"{state_total}"
                        ),
                        relpath,
                    )
            if latest_build:
                build_latest_counts = _mapping(latest_build.get("state_counts"))
                if any(
                    _safe_int(build_latest_counts.get(state))
                    != _safe_int(latest_counts.get(state))
                    for state in ("passed", "soft", "hard", "unknown")
                ):
                    self.error(
                        "operations-amd-latest-build-count",
                        "latest AMD build state counts do not match the summary",
                        relpath,
                    )

        definition_parity = _mapping(payload.get("definition_parity"))
        if definition_parity:
            parity_summary = _mapping(definition_parity.get("summary"))
            parity_matches = _rows(definition_parity.get("matches"))
            parity_inline_mirror_variants = _rows(
                definition_parity.get("inline_mirror_variants")
            )
            parity_additional_variants = _rows(
                definition_parity.get("additional_variants")
            )
            parity_amd_only = _rows(definition_parity.get("amd_only"))
            parity_upstream_only = _rows(definition_parity.get("nvidia_only"))
            parity_mirrors = _rows(definition_parity.get("mirrors"))
            expected_counts = {
                "matched": len(parity_matches),
                "direct_matches": len(parity_matches),
                "inline_mirror_variants": len(
                    parity_inline_mirror_variants
                ),
                "additional_variants": len(
                    parity_additional_variants
                ),
                "covered": (
                    len(parity_matches)
                    + len(parity_inline_mirror_variants)
                    + len(parity_additional_variants)
                ),
                "amd_only": len(parity_amd_only),
                "nvidia_only": len(parity_upstream_only),
                "mirrors": len(parity_mirrors),
                "command_twins": sum(
                    _mapping(row).get("match_method") == "command_twin"
                    for row in parity_matches
                ),
            }
            for key, expected in expected_counts.items():
                if _safe_int(parity_summary.get(key)) != expected:
                    self.error(
                        "definition-parity-count",
                        f"definition_parity.summary.{key}={parity_summary.get(key)} but rows imply {expected}",
                        relpath,
                    )
            # Identity families are a second, deliberately coarser
            # conservation layer above collision-safe AMD definition nodes.
            # Reconstruct them only from the family keys published on the
            # rows. Do not infer them from identity_key, definition IDs, or
            # total_amd_steps: those are separate contracts and a lossy family
            # normalizer must not be able to hide a dropped definition node.
            covered_family_rows = [
                *parity_matches,
                *parity_inline_mirror_variants,
                *parity_additional_variants,
            ]
            all_family_rows = [
                *covered_family_rows,
                *parity_amd_only,
            ]

            def published_family_keys(rows: list) -> tuple[set[str], int]:
                keys: set[str] = set()
                invalid = 0
                for raw_row in rows:
                    value = _mapping(raw_row).get("amd_identity_family_key")
                    if not isinstance(value, str) or not value.strip():
                        invalid += 1
                        continue
                    keys.add(value.strip())
                return keys, invalid

            covered_family_keys, invalid_covered_family_keys = (
                published_family_keys(covered_family_rows)
            )
            amd_only_member_family_keys, invalid_amd_only_family_keys = (
                published_family_keys(parity_amd_only)
            )
            all_family_keys = (
                covered_family_keys | amd_only_member_family_keys
            )
            invalid_family_keys = (
                invalid_covered_family_keys
                + invalid_amd_only_family_keys
            )
            if invalid_family_keys:
                self.error(
                    "definition-parity-identity-family-key",
                    (
                        f"{invalid_family_keys} AMD definition rows lack a "
                        "non-empty amd_identity_family_key"
                    ),
                    relpath,
                )

            partially_covered_family_keys = (
                covered_family_keys & amd_only_member_family_keys
            )
            exclusively_amd_only_family_keys = (
                amd_only_member_family_keys - covered_family_keys
            )
            expected_family_counts = {
                "amd_identity_families": len(all_family_keys),
                "covered_identity_families": len(covered_family_keys),
                "amd_only_identity_families": len(
                    exclusively_amd_only_family_keys
                ),
                "partially_covered_identity_families": len(
                    partially_covered_family_keys
                ),
                "identity_family_replica_rows": (
                    len(all_family_rows) - len(all_family_keys)
                ),
            }
            missing_family_summary_fields = (
                set(expected_family_counts) | {"identity_family_coverage_rate_pct"}
            ) - set(parity_summary)
            if missing_family_summary_fields:
                self.error(
                    "definition-parity-identity-family-schema",
                    (
                        "definition_parity.summary is missing identity-family "
                        f"fields {sorted(missing_family_summary_fields)}"
                    ),
                    relpath,
                )
            for key, expected in expected_family_counts.items():
                if _safe_int(parity_summary.get(key), -1) != expected:
                    self.error(
                        "definition-parity-identity-family-count",
                        (
                            f"definition_parity.summary.{key}="
                            f"{parity_summary.get(key)} but published "
                            f"amd_identity_family_key values imply {expected}"
                        ),
                        relpath,
                    )
            expected_family_coverage_rate = (
                round(
                    len(covered_family_keys) / len(all_family_keys) * 100,
                    1,
                )
                if all_family_keys
                else 0.0
            )
            if (
                abs(
                    _safe_float(
                        parity_summary.get("identity_family_coverage_rate_pct"),
                        -1.0,
                    )
                    - expected_family_coverage_rate
                )
                > 0.05
            ):
                self.error(
                    "definition-parity-identity-family-coverage",
                    (
                        "definition_parity.summary."
                        "identity_family_coverage_rate_pct="
                        f"{parity_summary.get('identity_family_coverage_rate_pct')} "
                        "but published amd_identity_family_key values imply "
                        f"{expected_family_coverage_rate}"
                    ),
                    relpath,
                )
            covered_plus_gaps = (
                expected_counts["covered"]
                + expected_counts["amd_only"]
            )
            if (
                _safe_int(parity_summary.get("total_amd_steps"))
                != covered_plus_gaps
            ):
                self.error(
                    "definition-parity-amd-total",
                    (
                        "definition_parity covered + AMD-only rows do not "
                        "equal total_amd_steps"
                    ),
                    relpath,
                )
            mirror_relationships = (
                "effective_command_duplicate",
                "same_hardware_command_variant",
                "hardware_variant",
            )
            expected_mirror_kinds = {
                relationship: sum(
                    _mapping(row).get("mirror_relationship")
                    == relationship
                    for row in parity_inline_mirror_variants
                )
                for relationship in mirror_relationships
            }
            published_mirror_kinds = _mapping(
                parity_summary.get("inline_mirror_variant_kinds")
            )
            if any(
                _safe_int(published_mirror_kinds.get(relationship))
                != expected
                for relationship, expected in expected_mirror_kinds.items()
            ) or sum(expected_mirror_kinds.values()) != len(
                parity_inline_mirror_variants
            ):
                self.error(
                    "definition-parity-mirror-subtypes",
                    (
                        "inline mirror subtype counts do not partition the "
                        "published mirror-linked variants"
                    ),
                    relpath,
                )
            upstream_definition_ids = [
                *(
                    str(_mapping(row).get("nvidia_definition_id") or "")
                    for row in parity_matches
                ),
                *(
                    str(_mapping(row).get("nvidia_definition_id") or "")
                    for row in parity_mirrors
                ),
                *(
                    str(_mapping(row).get("definition_id") or "")
                    for row in parity_upstream_only
                ),
            ]
            if (
                not all(upstream_definition_ids)
                or len(upstream_definition_ids)
                != _safe_int(parity_summary.get("total_nvidia_steps"))
                or len(set(upstream_definition_ids))
                != len(upstream_definition_ids)
            ):
                self.error(
                    "definition-parity-upstream-conservation",
                    (
                        "direct matches, inline mirrors, and upstream-only "
                        "rows do not uniquely partition upstream definitions"
                    ),
                    relpath,
                )
            amd_relationship_rows = [
                *parity_matches,
                *parity_inline_mirror_variants,
                *parity_additional_variants,
            ]
            logical_amd_ids = [
                *(
                    str(_mapping(row).get("amd_definition_id") or "")
                    for row in amd_relationship_rows
                ),
                *(
                    str(_mapping(row).get("definition_id") or "")
                    for row in parity_amd_only
                ),
            ]
            physical_amd_ids = [
                *(
                    str(definition_id or "")
                    for row in amd_relationship_rows
                    for definition_id in (
                        _mapping(row).get("amd_member_definition_ids")
                        or []
                    )
                ),
                *(
                    str(definition_id or "")
                    for row in parity_amd_only
                    for definition_id in (
                        _mapping(row).get("member_definition_ids")
                        or []
                    )
                ),
            ]
            if (
                not all(logical_amd_ids)
                or len(logical_amd_ids)
                != _safe_int(parity_summary.get("total_amd_steps"))
                or len(set(logical_amd_ids)) != len(logical_amd_ids)
            ):
                self.error(
                    "definition-parity-amd-logical-conservation",
                    (
                        "covered and AMD-only rows do not uniquely partition "
                        "logical AMD definition IDs"
                    ),
                    relpath,
                )
            if (
                not all(physical_amd_ids)
                or len(physical_amd_ids)
                != _safe_int(parity_summary.get("raw_amd_steps"))
                or len(set(physical_amd_ids)) != len(physical_amd_ids)
            ):
                self.error(
                    "definition-parity-amd-physical-conservation",
                    (
                        "logical AMD rows do not uniquely conserve all "
                        "physical member definition IDs"
                    ),
                    relpath,
                )
            invalid_inline_variants = [
                _mapping(row).get("amd_label")
                for row in parity_inline_mirror_variants
                if (
                    _mapping(row).get("match_method")
                    != "inline_mirror_variant"
                    or not _mapping(row).get("nvidia_label")
                    or not _mapping(row).get("nvidia_definition_id")
                )
            ]
            if invalid_inline_variants:
                self.error(
                    "definition-parity-inline-mirror",
                    (
                        f"{len(invalid_inline_variants)} inline mirror "
                        "variants lack their upstream relationship"
                    ),
                    relpath,
                )
            invalid_additional_variants = [
                _mapping(row).get("amd_label")
                for row in parity_additional_variants
                if (
                    _mapping(row).get("match_method")
                    != "additional_variant"
                    or not _mapping(row).get("nvidia_label")
                    or not _mapping(row).get("nvidia_definition_id")
                )
            ]
            if invalid_additional_variants:
                self.error(
                    "definition-parity-additional-variant",
                    (
                        f"{len(invalid_additional_variants)} additional "
                        "variants lack their upstream relationship"
                    ),
                    relpath,
                )
            source = _mapping(definition_parity.get("source"))
            commit_sha = str(source.get("commit_sha") or "")
            if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
                self.error(
                    "definition-parity-commit",
                    "definition_parity must identify one full vLLM commit SHA",
                    relpath,
                )
            invalid_twins = [
                _mapping(row).get("amd_label")
                for row in parity_matches
                if _mapping(row).get("match_method") == "command_twin"
                and (
                    _safe_float(_mapping(row).get("command_similarity")) < 0.999999
                    or _safe_float(_mapping(row).get("title_similarity")) < 0.65
                )
            ]
            if invalid_twins:
                self.error(
                    "definition-parity-twin",
                    f"{len(invalid_twins)} command twins violate the exact-command/title threshold",
                    relpath,
                )

        canonical_history = _mapping(
            _mapping(payload.get("nightly")).get("canonical_history")
        )
        nightly_builds = _rows(canonical_history.get("builds"))
        health_payload = self.load_json("data/vllm/ci/ci_health.json", {})
        health_amd = _mapping(_mapping(health_payload).get("amd"))
        latest_pipeline = _mapping(
            health_amd.get("latest_pipeline_build") or health_amd.get("latest_build")
        )
        if nightly_builds and latest_pipeline:
            operations_number = _safe_int(_mapping(nightly_builds[0]).get("number"))
            health_number = _safe_int(
                latest_pipeline.get("build_number") or latest_pipeline.get("number")
            )
            if operations_number != health_number:
                alignment = _mapping(canonical_history.get("head_alignment"))
                analytics_payload = self.load_json(
                    "data/vllm/ci/analytics.json", {}
                )
                analytics_build_numbers = {
                    _safe_int(
                        _mapping(row).get("number")
                        or _mapping(row).get("build_number"),
                        -1,
                    )
                    for row in _rows(
                        _mapping(_mapping(analytics_payload).get("amd-ci")).get(
                            "builds"
                        )
                    )
                }
                analytics_build_numbers.discard(-1)
                expected_ahead = sorted(
                    (
                        number
                        for number in analytics_build_numbers
                        if number > health_number
                    ),
                    reverse=True,
                )
                nightly_numbers = {
                    _safe_int(_mapping(row).get("number"), -1)
                    for row in nightly_builds
                }
                proven_analytics_ahead = (
                    health_number > 0
                    and alignment.get("status")
                    == "analytics_ahead_of_ci_health"
                    and alignment.get("canonical_build_number")
                    == operations_number
                    and alignment.get("ci_health_build_number") == health_number
                    and alignment.get("analytics_ahead_build_numbers")
                    == expected_ahead
                    and bool(expected_ahead)
                    and operations_number == expected_ahead[0]
                    and health_number in nightly_numbers
                )
                if proven_analytics_ahead:
                    self.warning(
                        "operations-latest-nightly-ahead",
                        (
                            f"analytics observed AMD build #{operations_number} "
                            f"after ci_health stopped at #{health_number}; both "
                            "history rows remain published until core catches up"
                        ),
                        relpath,
                    )
                else:
                    self.error(
                        "operations-latest-nightly",
                        (
                            f"operations latest AMD nightly #{operations_number} "
                            "does not match ci_health pipeline build "
                            f"#{health_number}"
                        ),
                        relpath,
                    )

        gating = _mapping(payload.get("gating"))
        reviewed_targets = _rows(gating.get("target_groups"))
        active = _rows(gating.get("active_target_groups"))
        active_summary = _mapping(gating.get("active_target_summary"))
        expected_active = _safe_int(active_summary.get("target_group_count"))
        expected_canonical = _safe_int(
            active_summary.get("canonical_group_count")
        )
        expected_outside_canonical = _safe_int(
            active_summary.get("active_outside_canonical_count")
        )
        if len(active) != expected_active:
            self.error(
                "operations-active-target-count",
                f"active target rows={len(active)} but summary target_group_count={expected_active}",
                relpath,
            )
        if len(reviewed_targets) != expected_canonical:
            self.error(
                "operations-canonical-target-count",
                (
                    f"reviewed target rows={len(reviewed_targets)} but summary "
                    f"canonical_group_count={expected_canonical}"
                ),
                relpath,
            )
        if expected_active != expected_canonical + expected_outside_canonical:
            self.error(
                "operations-active-target-summary",
                (
                    f"active target count={expected_active} but canonical "
                    f"({expected_canonical}) + outside canonical "
                    f"({expected_outside_canonical}) do not reconcile"
                ),
                relpath,
            )

        unsupported_owners = [
            _mapping(row).get("label")
            for row in active
            if _mapping(row).get("owner") or "owner" in _mapping(row)
        ]
        if unsupported_owners:
            self.error(
                "operations-unsupported-owners",
                f"{len(unsupported_owners)} gating rows publish an unsupported owner field",
                relpath,
            )

        linked_gating = 0
        observed_gating = 0
        wrong_latest_pipeline = []
        wrong_latest_urls = []
        wrong_history_pipeline = []
        wrong_history_evidence = []
        wrong_history_urls = []
        invalid_runtime_resolutions = []
        runtime_resolution_counts: dict[str, int] = {}
        allowed_runtime_resolutions = {
            "matched",
            "no_amd_definition",
            "stale_target_alias",
            "ambiguous",
            "not_observed",
        }
        for raw_row in active:
            row = _mapping(raw_row)
            latest = _mapping(row.get("latest_amd_result"))
            resolution = _mapping(row.get("runtime_resolution"))
            resolution_status = str(resolution.get("status") or "")
            runtime_resolution_counts[resolution_status] = (
                runtime_resolution_counts.get(resolution_status, 0) + 1
            )
            latest_state = str(latest.get("state") or "unknown")
            resolution_invalid = (
                resolution_status not in allowed_runtime_resolutions
                or (
                    latest_state in {"passed", "soft", "hard"}
                    and resolution_status != "matched"
                )
                or (
                    resolution_status
                    in {"no_amd_definition", "stale_target_alias", "ambiguous"}
                    and (
                        latest_state != "unknown"
                        or bool(_rows(latest.get("evidence")))
                    )
                )
                or (
                    resolution_status == "not_observed"
                    and latest_state != "unknown"
                )
            )
            if resolution_invalid:
                invalid_runtime_resolutions.append(row.get("label"))
            if latest.get("source_pipeline") != "amd-ci":
                wrong_latest_pipeline.append(row.get("label"))
            latest_evidence = _rows(latest.get("evidence"))
            if any(
                not isinstance(item, dict)
                or item.get("source_pipeline") != "amd-ci"
                for item in latest_evidence
            ):
                wrong_latest_pipeline.append(row.get("label"))
            latest_links = [
                (
                    item.get("url") or item.get("job_url") or item.get("build_url"),
                    item.get("build_number") or latest.get("build_number"),
                )
                for item in latest_evidence
                if isinstance(item, dict)
                if item.get("url") or item.get("job_url") or item.get("build_url")
            ]
            if any(
                not _buildkite_url_matches(
                    url,
                    "amd-ci",
                    build_number,
                    require_job=True,
                )
                for url, build_number in latest_links
            ):
                wrong_latest_urls.append(row.get("label"))
            history = _mapping(row.get("main_reliability"))
            if history.get("source_pipeline") != "ci":
                wrong_history_pipeline.append(row.get("label"))
            history_evidence = _rows(row.get("evidence"))
            if any(
                not isinstance(item, dict) or item.get("source_pipeline") != "ci"
                for item in history_evidence
            ):
                wrong_history_evidence.append(row.get("label"))
            bad_history_links = any(
                not _buildkite_url_matches(
                    item.get("url") or item.get("job_url"),
                    "ci",
                    item.get("build_number"),
                    require_job=True,
                )
                for item in history_evidence
                if isinstance(item, dict)
            )
            incident = _mapping(row.get("last_incident"))
            if incident and (
                incident.get("source_pipeline") != "ci"
                or not _buildkite_url_matches(
                    incident.get("job_url"),
                    "ci",
                    incident.get("build_number"),
                    require_job=True,
                )
            ):
                bad_history_links = True
            if history.get("latest_url") and not _buildkite_url_matches(
                history.get("latest_url"), "ci", require_job=True
            ):
                bad_history_links = True
            if bad_history_links:
                wrong_history_urls.append(row.get("label"))
            if latest.get("state") in {"passed", "soft", "hard"}:
                observed_gating += 1
                if any(item.get("url") for item in latest_evidence if isinstance(item, dict)):
                    linked_gating += 1
        if observed_gating != linked_gating:
            self.error(
                "operations-gating-missing-links",
                f"{observed_gating - linked_gating} of {observed_gating} observed gating rows lack exact AMD evidence",
                relpath,
            )
        if wrong_latest_pipeline:
            self.error(
                "operations-gating-latest-source-pipeline",
                f"{len(wrong_latest_pipeline)} gating rows do not source latest results from amd-ci",
                relpath,
            )
        if wrong_latest_urls:
            self.error(
                "operations-gating-latest-source-url",
                f"{len(wrong_latest_urls)} gating rows contain non-AMD links in latest AMD evidence",
                relpath,
            )
        if wrong_history_pipeline or wrong_history_evidence or wrong_history_urls:
            self.error(
                "operations-gating-history-source-pipeline",
                (
                    f"{len(wrong_history_pipeline)} reliability summaries and "
                    f"{len(wrong_history_evidence)} evidence lists do not source history from ci; "
                    f"{len(wrong_history_urls)} rows contain non-ci history links"
                ),
                relpath,
            )
        if invalid_runtime_resolutions:
            self.error(
                "operations-gating-runtime-resolution",
                (
                    f"{len(invalid_runtime_resolutions)} gating rows have a "
                    "runtime-resolution status inconsistent with AMD evidence"
                ),
                relpath,
            )
        declared_resolution_counts = _mapping(
            active_summary.get("by_runtime_resolution")
        )
        if declared_resolution_counts and declared_resolution_counts != dict(
            sorted(runtime_resolution_counts.items())
        ):
            self.error(
                "operations-gating-runtime-resolution-count",
                (
                    "active_target_summary.by_runtime_resolution does not match "
                    "the runtime target rows"
                ),
                relpath,
            )

        reliability = _mapping(payload.get("reliability"))
        if reliability.get("source_pipeline") != "ci":
            self.error(
                "operations-reliability-source-pipeline",
                (
                    "Canonical operations_v2.reliability must publish "
                    f"source_pipeline='ci', found {reliability.get('source_pipeline')!r}"
                ),
                relpath,
            )
        if reliability.get("available") is not True:
            self.error(
                "operations-reliability-unavailable",
                "Canonical upstream reliability must fail closed until a strict ci all-main cohort is present",
                relpath,
            )
        if "amd_reliability" in payload:
            self.error(
                "operations-amd-historical-reliability",
                "AMD historical reliability must not be published; AMD is the latest gating signal only",
                relpath,
            )
        cohort = _mapping(reliability.get("cohort"))
        if cohort.get("id") != "main":
            self.error(
                "operations-reliability-cohort",
                "Reliability must identify the all-main cohort separately from nightlies",
                relpath,
            )
        composition = _mapping(cohort.get("composition"))
        cohort_builds = _safe_int(composition.get("all_main_builds") or cohort.get("build_count"))
        cohort_nightlies = _safe_int(
            composition.get("canonical_nightlies")
            or cohort.get("canonical_nightly_build_count")
        )
        cohort_other_main = _safe_int(
            composition.get("other_main_builds")
            or cohort.get("other_main_build_count")
            or cohort.get("non_nightly_main_build_count")
        )
        if cohort_builds != cohort_nightlies + cohort_other_main:
            self.error(
                "operations-reliability-cohort-composition",
                (
                    f"all-main builds={cohort_builds} but canonical nightlies + other main "
                    f"builds={cohort_nightlies + cohort_other_main}"
                ),
                relpath,
            )

        nightly = _mapping(payload.get("nightly"))
        canonical = _mapping(nightly.get("canonical_history")) or next(
            (
                row for row in _rows(nightly.get("pipelines"))
                if isinstance(row, dict) and row.get("pipeline") == "amd-ci"
            ),
            {},
        )
        canonical_rows = _rows(canonical.get("builds"))
        if amd_latest_logical_counts.get("available") is True:
            logical_build_number = _safe_int(
                amd_latest_logical_counts.get("build_number")
            )
            logical_nightly = next(
                (
                    _mapping(row)
                    for row in canonical_rows
                    if _safe_int(_mapping(row).get("number"))
                    == logical_build_number
                ),
                {},
            )
            if not logical_nightly:
                self.error(
                    "operations-amd-logical-group-nightly-missing",
                    (
                        f"logical test-group build #{logical_build_number} is absent "
                        "from canonical AMD nightly history"
                    ),
                    relpath,
                )
            else:
                logical_nightly_fields = {
                    "unique_test_groups": "total",
                    "test_groups_passing_or": "passing",
                    "test_groups_passing_all": "passing_all",
                    "test_groups_partial": "partial",
                }
                if any(
                    _safe_int(logical_nightly.get(nightly_field), -1)
                    != _safe_int(
                        amd_latest_logical_counts.get(logical_field), -1
                    )
                    for nightly_field, logical_field
                    in logical_nightly_fields.items()
                ):
                    self.error(
                        "operations-amd-logical-group-nightly-counts",
                        (
                            "logical test-group summary disagrees with the same "
                            "canonical AMD nightly build"
                        ),
                        relpath,
                    )
        expected_nightlies = min(30, _safe_int(canonical.get("builds_available")))
        if len(canonical_rows) != expected_nightlies:
            self.error(
                "operations-nightly-retention",
                f"canonical AMD history retains {len(canonical_rows)} builds, expected {expected_nightlies}",
                relpath,
            )
        upstream_parity = _mapping(nightly.get("upstream_parity"))
        if upstream_parity.get("pipeline") != "ci" or canonical.get("pipeline") != "amd-ci":
            self.error(
                "operations-upstream-parity-scope",
                "Canonical AMD nightly history and upstream parity must be published separately",
                relpath,
            )
        if "branch=main" not in str((reliability.get("denominator") or {}).get("unit") or ""):
            self.error(
                "operations-reliability-denominator",
                "Reliability denominator must explicitly describe ci branch=main observations",
                relpath,
            )
        cohort_provenance = _mapping(cohort.get("provenance"))
        source_cohort = _mapping(cohort_provenance.get("cohort"))
        if source_cohort.get("pipeline") != "ci" or cohort_provenance.get("fallback"):
            self.error(
                "operations-reliability-cohort-source",
                "Canonical reliability must be backed by the strict ci all-main collector cohort",
                relpath,
            )

        catalog = _rows(reliability.get("group_catalog"))
        catalog_by_id = {
            row.get("id"): row
            for row in catalog
            if isinstance(row, dict) and row.get("id")
        }
        comparison = _mapping(reliability.get("platform_comparison"))
        comparison_rows = [
            row
            for row in _rows(comparison.get("rows"))
            if isinstance(row, dict)
        ]
        comparison_summary = _mapping(comparison.get("summary"))
        comparison_keys = {
            str(row.get("comparison_key") or "")
            for row in comparison_rows
            if row.get("comparison_key")
        }
        eligible_comparison_rows = [
            row for row in comparison_rows if row.get("comparison_eligible") is True
        ]
        matched_comparison_keys = {
            str(row.get("comparison_key") or "")
            for row in eligible_comparison_rows
            if row.get("comparison_key")
        }
        label_matched_keys = {
            str(row.get("comparison_key") or "")
            for row in comparison_rows
            if row.get("comparison_key")
            and _safe_int(_mapping(row.get("cuda")).get("variant_count")) > 0
        }
        comparison_amd_ids = {
            str(group_id)
            for row in comparison_rows
            for group_id in _rows(_mapping(row.get("amd")).get("group_ids"))
            if group_id
        }
        matched_cuda_ids = {
            str(group_id)
            for row in eligible_comparison_rows
            for group_id in _rows(_mapping(row.get("cuda")).get("group_ids"))
            if group_id
        }
        comparison_counts = {
            "amd_base_group_count": len(comparison_keys),
            "amd_variant_count": len(comparison_amd_ids),
            "label_matched_base_group_count": len(label_matched_keys),
            "matched_base_group_count": len(matched_comparison_keys),
            "comparable_base_group_count": len(matched_comparison_keys),
            "review_required_base_group_count": (
                len(comparison_keys) - len(matched_comparison_keys)
            ),
            "unmatched_amd_base_group_count": (
                len(comparison_keys) - len(label_matched_keys)
            ),
            "matched_cuda_variant_count": len(matched_cuda_ids),
        }
        if comparison:
            mismatched_counts = {
                key: (comparison_summary.get(key), expected)
                for key, expected in comparison_counts.items()
                if _safe_int(comparison_summary.get(key), -1) != expected
            }
            if "amd_comparison_row_count" in comparison_summary and _safe_int(
                comparison_summary.get("amd_comparison_row_count"), -1
            ) != len(comparison_rows):
                mismatched_counts["amd_comparison_row_count"] = (
                    comparison_summary.get("amd_comparison_row_count"),
                    len(comparison_rows),
                )
            if "comparable_variant_pair_count" in comparison_summary and _safe_int(
                comparison_summary.get("comparable_variant_pair_count"), -1
            ) != len(eligible_comparison_rows):
                mismatched_counts["comparable_variant_pair_count"] = (
                    comparison_summary.get("comparable_variant_pair_count"),
                    len(eligible_comparison_rows),
                )
            if mismatched_counts:
                self.error(
                    "operations-platform-comparison-counts",
                    (
                        "platform_comparison summary does not reconcile with its "
                        f"published rows: {mismatched_counts}"
                    ),
                    relpath,
                )
            invalid_pairs = [
                row
                for row in comparison_rows
                if (
                    row.get("comparison_eligible") is True
                    and (
                        row.get("match_status") != "exact_cuda_pair"
                        or _safe_int(_mapping(row.get("amd")).get("variant_count")) != 1
                        or _safe_int(_mapping(row.get("cuda")).get("variant_count")) != 1
                    )
                )
                or (
                    row.get("comparison_eligible") is not True
                    and row.get("match_status") == "exact_cuda_pair"
                )
            ]
            missing_catalog_ids = (
                comparison_amd_ids | matched_cuda_ids
            ) - set(catalog_by_id)
            if invalid_pairs or missing_catalog_ids:
                self.error(
                    "operations-platform-comparison-eligibility",
                    (
                        f"{len(invalid_pairs)} comparison rows violate exact-pair "
                        f"eligibility and {len(missing_catalog_ids)} referenced group "
                        "IDs are absent from the reliability catalog"
                    ),
                    relpath,
                )
        candidates = _rows(reliability.get("flaky_candidates"))
        cohort_build_numbers = {
            _safe_int(number, -1)
            for number in _rows(cohort.get("build_numbers"))
            if _safe_int(number, -1) > 0
        }
        if reliability.get("available") is True and len(cohort_build_numbers) != cohort_builds:
            self.error(
                "operations-reliability-cohort-build-numbers",
                (
                    f"cohort declares {cohort_builds} builds but publishes "
                    f"{len(cohort_build_numbers)} unique build-number foreign keys"
                ),
                relpath,
            )
        observations = 0
        linked_observations = 0
        denominator_observations = 0
        for raw_group in catalog:
            group = _mapping(raw_group)
            rows = _rows(group.get("observations"))
            if group.get("source_pipeline") != "ci":
                self.error(
                    "operations-reliability-group-source",
                    f"{group.get('name')}: group source_pipeline is not ci",
                    relpath,
                )
            wrong_source_rows = [
                row for row in rows
                if not isinstance(row, dict)
                or row.get("source_pipeline") != "ci"
                or _safe_int(row.get("build_number"), -1) not in cohort_build_numbers
                or not _buildkite_url_matches(
                    row.get("build_url"), "ci", row.get("build_number")
                )
                or not _buildkite_url_matches(
                    row.get("job_url"), "ci", row.get("build_number"), require_job=True
                )
                or (
                    row.get("step_url")
                    and not _buildkite_url_matches(
                        row.get("step_url"), "ci", row.get("build_number"), require_job=True
                    )
                )
            ]
            if wrong_source_rows:
                self.error(
                    "operations-reliability-observation-source",
                    f"{group.get('name')}: {len(wrong_source_rows)} retained observations are not upstream ci evidence",
                    relpath,
                )
            observations += len(rows)
            linked_observations += sum(
                bool(row.get("job_url")) for row in rows if isinstance(row, dict)
            )
            runs = _safe_int(group.get("runs"))
            denominator_observations += runs
            retained = _safe_int(group.get("retained_observation_count"))
            if len(rows) != retained or retained > runs:
                self.error(
                    "operations-reliability-evidence-count",
                    f"{group.get('name')}: runs={runs}, retained={retained}, rows={len(rows)}",
                    relpath,
                )
            group_ids = group.get("group_ids") or []
            if not group.get("id") or group.get("id") not in group_ids:
                self.error(
                    "operations-reliability-group-identity",
                    f"{group.get('name')}: strict id is absent from aggregate group_ids",
                    relpath,
                )
            if "hardware" not in group or "queues" not in group:
                self.error(
                    "operations-reliability-hardware-identity",
                    f"{group.get('name')}: hardware/queue identity is missing",
                    relpath,
                )
            if group.get("median_dur") is not None and group.get("max_dur") is None:
                self.error(
                    "operations-reliability-max-duration",
                    f"{group.get('name')}: median duration is published without a maximum",
                    relpath,
                )
        expected_denominator = _safe_int(
            _mapping(reliability.get("denominator")).get("observations")
        )
        if denominator_observations != expected_denominator:
            self.error(
                "operations-reliability-denominator-sum",
                f"catalog runs={denominator_observations} but denominator observations={expected_denominator}",
                relpath,
            )
        for raw_candidate in candidates:
            candidate = _mapping(raw_candidate)
            if candidate.get("source_pipeline") != "ci":
                self.error(
                    "operations-flaky-candidate-source",
                    f"{candidate.get('name')}: flaky candidate source_pipeline is not ci",
                    relpath,
                )
            evidence_ref = candidate.get("evidence_ref") or candidate.get("id")
            if evidence_ref not in catalog_by_id:
                self.error(
                    "operations-reliability-evidence-ref",
                    f"{candidate.get('name')}: missing catalog evidence reference {evidence_ref!r}",
                    relpath,
                )
            if candidate.get("evidence_type") != "mixed_outcome_history":
                self.error(
                    "operations-reliability-evidence-type",
                    f"{candidate.get('name')}: missing mixed_outcome_history classification",
                    relpath,
                )
        if observations and linked_observations != observations:
            self.error(
                "operations-reliability-missing-links",
                f"{observations - linked_observations} of {observations} retained observations lack an exact job URL",
                relpath,
            )

        latency = _rows(_mapping(reliability.get("latency_rankings")).get("by_p90_duration"))
        wrong_latency_source = [
            _mapping(row).get("name")
            for row in latency
            if _mapping(row).get("source_pipeline") != "ci"
        ]
        if wrong_latency_source:
            self.error(
                "operations-latency-source",
                f"{len(wrong_latency_source)} latency rows are not sourced from upstream ci",
                relpath,
            )
        missing_latency_max = [
            _mapping(row).get("name")
            for row in latency
            if _mapping(row).get("max_dur") is None
        ]
        if missing_latency_max:
            self.error(
                "operations-latency-max-duration",
                f"{len(missing_latency_max)} latency rows omit max_dur",
                relpath,
            )

        retry = _mapping(reliability.get("retry_analysis"))
        retry_summary = _mapping(retry.get("summary"))
        retry_attempts = _rows(retry.get("retry_attempts"))
        recoveries = _rows(retry.get("failed_then_passed_recoveries"))
        if _safe_int(retry_summary.get("retry_attempt_count")) != len(retry_attempts):
            self.error(
                "operations-retry-attempt-count",
                (
                    f"retry summary={retry_summary.get('retry_attempt_count')} but "
                    f"{len(retry_attempts)} attempt rows are published"
                ),
                relpath,
            )
        if _safe_int(retry_summary.get("failed_then_passed_recovery_count")) != len(recoveries):
            self.error(
                "operations-retry-recovery-count",
                (
                    f"recovery summary={retry_summary.get('failed_then_passed_recovery_count')} but "
                    f"{len(recoveries)} chains are published"
                ),
                relpath,
            )
        missing_retry_urls = sum(
            not isinstance(row, dict) or not (row.get("job_url") or row.get("url"))
            for row in retry_attempts
        )
        missing_recovery_urls = sum(
            not isinstance(row, dict) or not (row.get("failed_url") and row.get("passed_url"))
            for row in recoveries
        )
        if missing_retry_urls or missing_recovery_urls:
            self.error(
                "operations-retry-links",
                (
                    f"{missing_retry_urls} retry attempts and {missing_recovery_urls} recovered chains "
                    "lack exact retained URLs"
                ),
                relpath,
            )
        wrong_retry_sources = sum(
            not isinstance(row, dict)
            or row.get("source_pipeline") != "ci"
            or _safe_int(row.get("build_number"), -1) not in cohort_build_numbers
            or not _buildkite_url_matches(
                row.get("job_url") or row.get("url"),
                "ci",
                row.get("build_number"),
                require_job=True,
            )
            for row in retry_attempts
        )
        wrong_recovery_sources = sum(
            not isinstance(row, dict)
            or row.get("source_pipeline") != "ci"
            or _safe_int(row.get("build_number"), -1) not in cohort_build_numbers
            or not _buildkite_url_matches(
                row.get("failed_url"), "ci", row.get("build_number"), require_job=True
            )
            or not _buildkite_url_matches(
                row.get("passed_url"), "ci", row.get("build_number"), require_job=True
            )
            for row in recoveries
        )
        if wrong_retry_sources or wrong_recovery_sources:
            self.error(
                "operations-retry-source",
                (
                    f"{wrong_retry_sources} retry attempts and {wrong_recovery_sources} recoveries "
                    "are not exact upstream ci evidence"
                ),
                relpath,
            )

        queue_block = _mapping(payload.get("queue"))
        queue_provenance = _mapping(queue_block.get("provenance"))
        trajectory_provenance = _mapping(_mapping(payload.get("trajectory")).get("provenance"))
        omni_provenance = _mapping(_mapping(payload.get("omni")).get("provenance"))
        expected_source_paths = {
            "queue history": (
                _mapping(queue_provenance.get("source_paths"))
            ).get("history"),
            "trajectory builds": (
                _mapping(trajectory_provenance.get("source_paths"))
            ).get("build_history"),
            "trajectory changes": (
                _mapping(trajectory_provenance.get("source_paths"))
            ).get("group_changes"),
            "Omni aggregates": (
                _mapping(omni_provenance.get("source_paths"))
            ).get("queue_aggregates"),
        }
        required_source_paths = {
            "queue history": "queue_timeseries.jsonl",
            "trajectory builds": "analytics.json",
            "trajectory changes": "group_changes.json",
            "Omni aggregates": "queue_timeseries.jsonl",
        }
        mismatched_paths = {
            label: (expected_source_paths.get(label), expected)
            for label, expected in required_source_paths.items()
            if expected_source_paths.get(label) != expected
        }
        if mismatched_paths:
            self.error(
                "operations-aggregate-provenance",
                f"Published aggregate source paths are incomplete or incorrect: {mismatched_paths}",
                relpath,
            )

        trajectory = _mapping(payload.get("trajectory"))
        trajectory_pipelines = _rows(trajectory.get("pipelines"))
        trajectory_pipeline = _mapping(trajectory_pipelines[0]) if trajectory_pipelines else {}
        trajectory_history = _mapping(
            _mapping(trajectory.get("provenance")).get("build_history")
        )
        if (
            trajectory.get("source_pipeline") != "ci"
            or trajectory.get("available") is not (reliability.get("available") is True)
            or trajectory.get("pipeline_order") != ["ci"]
            or len(trajectory_pipelines) != 1
            or trajectory_pipeline.get("pipeline") != "ci"
            or trajectory_pipeline.get("source_key") != "ci.all_main_reliability"
            or trajectory_pipeline.get("groups")
            != _safe_int(_mapping(reliability.get("denominator")).get("groups"))
            or trajectory_pipeline.get("observations")
            != _safe_int(_mapping(reliability.get("denominator")).get("observations"))
            or trajectory_pipeline.get("cohort") != cohort
            or trajectory_history.get("source_pipeline") != "ci"
            or trajectory_history.get("source_key") != "ci.all_main_reliability"
        ):
            self.error(
                "operations-trajectory-scope",
                "Workload trajectory must use only the strict upstream ci all-main cohort",
                relpath,
            )

        queue_history = _rows(queue_block.get("history"))
        if len(queue_history) < 2:
            self.error(
                "operations-queue-history",
                "Queue history must retain more than the current snapshot",
                relpath,
            )
        if "mi355b" in json.dumps(payload, sort_keys=True).lower():
            self.error(
                "operations-retired-mi355b",
                "operations_v2.json still contains retired amd_mi355B queues",
                relpath,
            )

        self.audit_agent_health(payload, relpath)

        self.report.metrics["operations_v2"] = {
            "active_targets": len(active),
            "canonical_targets": expected_canonical,
            "active_targets_outside_canonical": expected_outside_canonical,
            "amd_latest_job_variants": _safe_int(
                amd_health_summary.get("latest_job_variant_count")
                or amd_health_summary.get("latest_group_count")
            ),
            "amd_retained_job_variants": len(amd_health_catalog),
            "amd_latest_test_groups": _safe_int(
                _mapping(
                    amd_health_summary.get("latest_test_group_counts")
                ).get("total")
            ),
            "linked_active_targets": linked_gating,
            "mixed_outcome_candidates": len(candidates),
            "reliability_groups": len(catalog),
            "reliability_observations": observations,
            "linked_reliability_observations": linked_observations,
            "queue_history_snapshots": len(queue_history),
            "canonical_nightlies": len(canonical_rows),
            "all_main_builds": cohort_builds,
            "all_main_nightlies": cohort_nightlies,
            "other_main_builds": cohort_other_main,
            "retry_attempts": len(retry_attempts),
            "retry_recoveries": len(recoveries),
            "platform_comparison_base_groups": len(comparison_keys),
            "platform_comparison_variant_pairs": len(eligible_comparison_rows),
            "source_age_hours": source_ages,
        }

    def audit_agent_health(self, payload: dict, relpath: str) -> None:
        """Cross-check the pre-aggregated AMD CI agent-health block.

        collect_agent_health.py walks every build across all branches in the AMD
        pipelines and ships compact per-node/day reliability rollups plus the raw
        infra-suspect failing runs; the frontend aggregates the table and clusters
        co-failure events client-side. This audits the aggregated block's meta,
        rollup shape, and the AMD scoping of the shipped failing runs.
        """
        agent_health = _mapping(payload.get("amd_agent_health"))
        if not agent_health:
            self.error(
                "operations-agent-health-missing",
                "operations_v2.json omits the amd_agent_health block",
                relpath,
            )
            return
        for key in ("window_options", "cofailure_window_options"):
            if not _rows(agent_health.get(key)):
                self.error(
                    "operations-agent-health-options",
                    f"amd_agent_health must publish {key} for the control presets",
                    relpath,
                )
        if _safe_int(agent_health.get("default_cofailure_window_mins")) <= 0:
            self.error(
                "operations-agent-health-cofail-default",
                "amd_agent_health must publish a positive default_cofailure_window_mins",
                relpath,
            )

        def _is_amd_gpu_queue(queue: str) -> bool:
            # GPU-only scope (amd_mi<model>), excluding the retired amd_mi355b family.
            q = str(queue or "").casefold()
            return q.startswith("amd_mi") and not q.startswith("amd_mi355b")

        # Per-node/day rollup shape: [runs, soft, hard, canceled], soft+hard+canceled
        # never exceed runs, and every row carries an identifiable day + node.
        node_days = _rows(agent_health.get("node_days"))
        bad_rollups = 0
        for raw_row in node_days:
            row = _mapping(raw_row)
            if not row.get("d") or not row.get("nd"):
                bad_rollups += 1
                continue
            for bucket_key in ("a", "n"):
                bucket = _rows(row.get(bucket_key))
                if len(bucket) != 4:
                    bad_rollups += 1
                    break
                runs, soft, hard, canceled = (_safe_int(v) for v in bucket)
                if soft + hard + canceled > runs or min(runs, soft, hard, canceled) < 0:
                    bad_rollups += 1
                    break
        if bad_rollups:
            self.error(
                "operations-agent-health-rollup-shape",
                f"{bad_rollups} node_days rollup rows are malformed (bad bucket / day / node)",
                relpath,
            )

        # Every shipped failing run must be an infra-suspect AMD GPU failure.
        failing = _rows(agent_health.get("failing_runs"))
        non_amd = [
            _mapping(run).get("q")
            for run in failing
            if not _is_amd_gpu_queue(_mapping(run).get("q"))
        ]
        if non_amd:
            self.error(
                "operations-agent-health-queue-scope",
                f"{len(non_amd)} agent-health failing runs are on non-AMD-GPU queues, e.g. {non_amd[:3]}",
                relpath,
            )
        bad_state = [
            _mapping(run).get("s")
            for run in failing
            if _mapping(run).get("s") not in ("hard", "soft")
        ]
        if bad_state:
            self.error(
                "operations-agent-health-failing-state",
                f"{len(bad_state)} failing runs have a non-failure state, e.g. {bad_state[:3]}",
                relpath,
            )
        # The signal toggle needs an infra-suspect flag (1/0) on every failing run.
        bad_flag = [
            _mapping(run).get("i")
            for run in failing
            if _mapping(run).get("i") not in (0, 1)
        ]
        if bad_flag:
            self.error(
                "operations-agent-health-infra-flag",
                f"{len(bad_flag)} failing runs have a bad infra-suspect flag (i), e.g. {bad_flag[:3]}",
                relpath,
            )

    def audit_operations_bundle(self) -> None:
        # Keep optional full-audit dependencies out of the DNS-only entrypoint.
        # The isolated DNS publisher deliberately validates under ``python -S``
        # so unrelated dashboard dependencies cannot stop its durable updates.
        from vllm.build_operations_snapshot import (
            ORG_SUMMARY_MAX_BYTES,
            ORG_SUMMARY_SCHEMA_VERSION,
            QUEUE_LIFECYCLE_NAME,
            build_org_summary,
        )

        relpath = "data/vllm/ci/operations_v2_manifest.json"
        manifest_path = self.root / relpath
        manifest = self.load_json(relpath, {})
        if not isinstance(manifest, dict):
            return
        if manifest.get("schema_version") != 2 or manifest.get("bundle_version") != 1:
            self.error(
                "operations-bundle-schema",
                "operations manifest must use schema_version=2 and bundle_version=1",
                relpath,
            )
        shell = _mapping(manifest.get("shell"))
        monolith = self.load_json("data/vllm/ci/operations_v2.json", {})
        if shell.get("generated_at") != _mapping(monolith).get("generated_at"):
            self.error(
                "operations-bundle-freshness",
                "operations shell and compatibility snapshot have different generation timestamps",
                relpath,
            )

        summary_descriptor = _mapping(manifest.get("organization_summary"))
        summary_relative = str(summary_descriptor.get("path") or "")
        summary_path = manifest_path.parent / summary_relative
        if summary_relative != "org_summary.json":
            self.error(
                "operations-bundle-org-summary-descriptor",
                "operations manifest must point to org_summary.json",
                relpath,
            )
        elif not summary_path.exists():
            self.error(
                "operations-bundle-org-summary-missing",
                "organization summary is missing",
                relpath,
            )
        else:
            summary = self.load_json(self.rel(summary_path), {})
            lifecycle = self.load_json(
                "data/vllm/ci/queue_lifecycle.json",
                {},
            )
            expected_summary = build_org_summary(
                _mapping(monolith),
                _mapping(lifecycle),
            )
            if summary != expected_summary:
                self.error(
                    "operations-bundle-org-summary-projection",
                    (
                        "org_summary.json does not match the authoritative "
                        "Operations and queue-lifecycle inputs"
                    ),
                    relpath,
                )
            summary_mapping = _mapping(summary)
            scheduled = _mapping(
                _mapping(summary_mapping.get("scheduled_cohorts")).get(
                    "upstream_nightly"
                )
            )
            scheduled_count_fields = (
                "configured",
                "observed",
                "green",
                "non_green",
                "failing",
                "soft_failing",
                "pending",
                "missing",
                "queues_configured",
                "queues_with_observed_work",
            )
            scheduled_counts = {
                key: scheduled.get(key) for key in scheduled_count_fields
            }
            scheduled_available = scheduled.get("available")
            scheduled_invalid = type(scheduled_available) is not bool
            if scheduled_available is True:
                scheduled_invalid = scheduled_invalid or any(
                    type(value) is not int or value < 0
                    for value in scheduled_counts.values()
                )
                if not scheduled_invalid:
                    scheduled_invalid = (
                        scheduled_counts["configured"]
                        != scheduled_counts["observed"]
                        + scheduled_counts["missing"]
                        or scheduled_counts["observed"]
                        != sum(
                            scheduled_counts[key]
                            for key in (
                                "green",
                                "failing",
                                "soft_failing",
                                "pending",
                            )
                        )
                        or scheduled_counts["non_green"]
                        != scheduled_counts["observed"]
                        - scheduled_counts["green"]
                    )
            elif scheduled_available is False:
                # No retained nightly is a valid stable-API state. Keep it
                # distinguishable from an observed zero-sized cohort: every
                # unavailable denominator must remain explicitly null.
                scheduled_invalid = scheduled_invalid or any(
                    value is not None for value in scheduled_counts.values()
                )
            if scheduled_invalid:
                self.error(
                    "operations-bundle-org-summary-scheduled-denominators",
                    (
                        "organization summary upstream nightly denominators must "
                        "be non-negative and reconcile when available, or all be "
                        "null when the cohort is unavailable"
                    ),
                    relpath,
                )
            daily_waits = _mapping(
                _mapping(summary_mapping.get("queues")).get(
                    "daily_served_job_waits"
                )
            )
            wait_source = _mapping(daily_waits.get("source"))
            lifecycle_days = _rows(
                _mapping(_mapping(lifecycle).get("daily_wait_times")).get("days")
            )
            indexed_days = _rows(daily_waits.get("days"))
            if daily_waits.get("available") is True:
                expected_index = [
                    {
                        key: day.get(key)
                        for key in (
                            "date",
                            "start",
                            "end_exclusive",
                            "partial",
                            "sample_count",
                        )
                    }
                    for day in lifecycle_days
                    if isinstance(day, dict)
                ]
                lifecycle_sample_count = sum(
                    _safe_int(_mapping(day).get("sample_count"))
                    for day in lifecycle_days
                )
                if (
                    summary_mapping.get("schema_version")
                    != ORG_SUMMARY_SCHEMA_VERSION
                    or wait_source.get("path") != QUEUE_LIFECYCLE_NAME
                    or wait_source.get("schema_version")
                    != _mapping(lifecycle).get("schema_version")
                    or wait_source.get("key") != "daily_wait_times.days"
                    or wait_source.get("vector_key")
                    != "served_job_wait_seconds"
                    or daily_waits.get("source_generated_at")
                    != _mapping(lifecycle).get("generated_at")
                    or _safe_int(daily_waits.get("sample_count"))
                    != lifecycle_sample_count
                    or indexed_days != expected_index
                ):
                    self.error(
                        "operations-bundle-org-summary-source",
                        (
                            "organization summary daily waits must reference the "
                            "exact lifecycle vectors with matching schema, generation, "
                            "sample count, and UTC day bounds"
                        ),
                        relpath,
                    )
            summary_size = summary_path.stat().st_size
            if _safe_int(summary_descriptor.get("bytes")) != summary_size:
                self.error(
                    "operations-bundle-org-summary-size",
                    (
                        "organization summary reports "
                        f"{summary_descriptor.get('bytes')} bytes but file size is "
                        f"{summary_size}"
                    ),
                    relpath,
                )
            if summary_size > ORG_SUMMARY_MAX_BYTES:
                self.error(
                    "operations-bundle-org-summary-budget",
                    (
                        f"organization summary is {summary_size} bytes; "
                        f"budget is {ORG_SUMMARY_MAX_BYTES} bytes"
                    ),
                    relpath,
                )

        expected = {
            "nightly",
            "amd_test_health",
            "amd_agent_health",
            "comparison",
            "comparison_retry_evidence",
            "reliability",
            "definition_parity",
            "gating",
            "queue",
            "trajectory",
            "omni",
            "diagnostics",
        }
        sections = _mapping(manifest.get("sections"))
        missing = expected - set(sections)
        if missing:
            self.error(
                "operations-bundle-sections",
                f"operations manifest is missing lazy sections: {sorted(missing)}",
                relpath,
            )

        section_sizes: dict[str, int] = {}
        root = manifest_path.parent.resolve()
        for name, raw_descriptor in sections.items():
            descriptor = _mapping(raw_descriptor)
            relative = str(descriptor.get("path") or "")
            path = (manifest_path.parent / relative).resolve()
            if not relative or not path.is_relative_to(root):
                self.error(
                    "operations-bundle-path",
                    f"section {name} has an unsafe path: {relative!r}",
                    relpath,
                )
                continue
            if not path.exists():
                declared_size = _safe_int(descriptor.get("bytes"))
                section_sizes[name] = declared_size
                continue
            size = path.stat().st_size
            section_sizes[name] = size
            if _safe_int(descriptor.get("bytes")) != size:
                self.error(
                    "operations-bundle-size",
                    f"section {name} reports {descriptor.get('bytes')} bytes but file size is {size}",
                    relpath,
                )
            try:
                section = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError, UnicodeError) as exc:
                self.error(
                    "operations-bundle-json",
                    f"section {name} is not valid JSON: {exc}",
                    self.rel(path),
                )
                continue
            if not isinstance(section, dict):
                self.error(
                    "operations-bundle-shape",
                    f"section {name} must contain a JSON object",
                    self.rel(path),
                )

        manifest_size = manifest_path.stat().st_size if manifest_path.exists() else 0
        home_budget = 2_000_000
        health_budget = 12_000_000
        queue_budget = 6_000_000
        health_initial = manifest_size + section_sizes.get("nightly", 0) + section_sizes.get("amd_test_health", 0)
        if manifest_size > home_budget:
            self.error(
                "operations-home-payload-budget",
                f"first-render shell is {manifest_size} bytes; budget is {home_budget}",
                relpath,
            )
        if health_initial > health_budget:
            self.error(
                "operations-health-payload-budget",
                f"CI Health overview payload is {health_initial} bytes; budget is {health_budget}",
                relpath,
            )
        if section_sizes.get("queue", 0) > queue_budget:
            self.error(
                "operations-queue-payload-budget",
                f"queue section is {section_sizes['queue']} bytes; budget is {queue_budget}",
                relpath,
            )
        if section_sizes.get("comparison", 0) > OPERATIONS_COMPARISON_MAX_BYTES:
            self.error(
                "operations-comparison-payload-budget",
                (
                    "flake/retry/latency comparison section is "
                    f"{section_sizes['comparison']} bytes; budget is "
                    f"{OPERATIONS_COMPARISON_MAX_BYTES}"
                ),
                relpath,
            )
        retry_evidence_size = section_sizes.get("comparison_retry_evidence", 0)
        if retry_evidence_size > OPERATIONS_COMPARISON_RETRY_EVIDENCE_MAX_BYTES:
            self.error(
                "operations-comparison-retry-evidence-payload-budget",
                (
                    "deferred flake/retry evidence section is "
                    f"{retry_evidence_size} bytes; budget is "
                    f"{OPERATIONS_COMPARISON_RETRY_EVIDENCE_MAX_BYTES}"
                ),
                relpath,
            )
        self.report.metrics["operations_bundle"] = {
            "manifest_bytes": manifest_size,
            "health_overview_bytes": health_initial,
            "section_bytes": section_sizes,
        }

    def audit_home_pr_issue_data(self) -> None:
        prs_payload = self.load_json("data/vllm/prs.json", {})
        issues_payload = self.load_json("data/vllm/issues.json", {})
        prs = prs_payload.get("prs") if isinstance(prs_payload, dict) else []
        issues = issues_payload.get("issues") if isinstance(issues_payload, dict) else []
        if not isinstance(prs, list) or not isinstance(issues, list):
            self.error("home-shape", "prs.json/issues.json must contain list payloads")
            return

        repo = "vllm-project/vllm"
        pr_by_number = {p.get("number"): p for p in prs if isinstance(p, dict)}
        issue_by_number = {i.get("number"): i for i in issues if isinstance(i, dict)}
        linked_refs = 0

        for issue in issues:
            if (issue.get("state") or "").lower() != "open":
                self.error(
                    "project-issue-not-open",
                    f"Issue #{issue.get('number')} is in issues.json but is not open",
                    "data/vllm/issues.json",
                )
            if issue.get("repo") and issue.get("repo") != repo:
                self.error(
                    "project-issue-repo",
                    f"Issue #{issue.get('number')} belongs to {issue.get('repo')}, expected {repo}",
                    "data/vllm/issues.json",
                )
            if "projects/39" not in (issue.get("project_url") or ""):
                self.error(
                    "project-issue-source",
                    f"Issue #{issue.get('number')} is missing the project #39 source URL",
                    "data/vllm/issues.json",
                )
            for ref in issue.get("linked_prs") or []:
                if not same_repo(ref.get("repo"), repo):
                    continue
                number = ref.get("number")
                linked_refs += 1
                if number not in pr_by_number:
                    self.error(
                        "linked-ci-pr-missing",
                        f"Issue #{issue.get('number')} links PR #{number}, but prs.json does not include it",
                        "data/vllm/prs.json",
                    )
                    continue
                pr = pr_by_number[number]
                if not pr.get("is_ci_pr"):
                    self.error(
                        "linked-ci-pr-untagged",
                        f"PR #{number} is linked from issue #{issue.get('number')} but is_ci_pr is false",
                        "data/vllm/prs.json",
                    )
                if issue.get("number") not in (pr.get("ci_issue_numbers") or []):
                    self.error(
                        "ci-pr-issue-backlink",
                        f"PR #{number} lacks ci_issue_numbers backlink to issue #{issue.get('number')}",
                        "data/vllm/prs.json",
                    )

        for pr in prs:
            number = pr.get("number")
            labels = {str(label).lower() for label in pr.get("labels") or []}
            expected_rocm = "rocm" in labels
            if bool(pr.get("is_rocm_pr")) != expected_rocm:
                self.error(
                    "rocm-pr-tag",
                    f"PR #{number} has is_rocm_pr={pr.get('is_rocm_pr')} but labels={sorted(labels)}",
                    "data/vllm/prs.json",
                )
            ci_issue_numbers = pr.get("ci_issue_numbers") or []
            if bool(pr.get("is_ci_pr")) != bool(ci_issue_numbers):
                self.error(
                    "ci-pr-tag",
                    f"PR #{number} has inconsistent is_ci_pr and ci_issue_numbers",
                    "data/vllm/prs.json",
                )
            for issue_number in ci_issue_numbers:
                if issue_number not in issue_by_number:
                    self.error(
                        "ci-pr-issue-missing",
                        f"PR #{number} points at issue #{issue_number}, but issues.json does not include it",
                        "data/vllm/issues.json",
                    )
            tags = pr.get("custom_tags") or []
            if pr.get("is_ci_pr") and "CI" not in tags:
                self.error("ci-custom-tag", f"PR #{number} is CI but missing custom CI tag")
            if pr.get("is_rocm_pr") and "ROCm" not in tags:
                self.error("rocm-custom-tag", f"PR #{number} is ROCm but missing custom ROCm tag")

        open_prs = [p for p in prs if (p.get("state") or "").lower() == "open"]
        self.report.metrics["home"] = {
            "prs": len(prs),
            "open_prs": len(open_prs),
            "ci_prs": sum(1 for p in open_prs if p.get("is_ci_pr")),
            "rocm_prs": sum(1 for p in open_prs if p.get("is_rocm_pr")),
            "project_issues": len(issues),
            "linked_issue_pr_refs": linked_refs,
        }

    def _pass_rate_contract_enabled(
        self,
        version: Any,
        *,
        label: str,
        path: str,
        code_prefix: str,
    ) -> bool:
        if version is None:
            self.warning(
                f"{code_prefix}-legacy",
                (
                    f"{label} is an unversioned legacy payload; explicit pass-rate "
                    "fields will be required after its next producer collection"
                ),
                path,
            )
            return False
        if not _is_nonnegative_int(version) or version != 1:
            self.error(
                f"{code_prefix}-version",
                f"{label} pass_rate_contract_version={version!r}, expected 1",
                path,
            )
            return False
        return True

    def _audit_percentage_rate(
        self,
        record: dict,
        *,
        label: str,
        path: str,
        code_prefix: str,
        percentage_field: str,
        basis_field: str,
        expected_basis: str,
        expected_percentage: float | None,
        decimal_places: int,
        legacy_is_ratio: bool,
    ) -> None:
        basis = record.get(basis_field)
        if basis != expected_basis:
            self.error(
                f"{code_prefix}-basis",
                f"{label} {basis_field}={basis!r}, expected {expected_basis!r}",
                path,
            )

        percentage = record.get(percentage_field)
        percentage_valid = _is_finite_number(percentage) and 0 <= percentage <= 100
        if not percentage_valid:
            self.error(
                f"{code_prefix}-pct",
                f"{label} {percentage_field}={percentage!r}, expected a finite value from 0 to 100",
                path,
            )

        legacy = record.get("pass_rate")
        legacy_upper_bound = 1 if legacy_is_ratio else 100
        legacy_valid = (
            _is_finite_number(legacy)
            and 0 <= legacy <= legacy_upper_bound
        )
        if not legacy_valid:
            unit = "0 to 1 ratio" if legacy_is_ratio else "0 to 100 percentage"
            self.error(
                f"{code_prefix}-alias",
                f"{label} legacy pass_rate={legacy!r}, expected a finite {unit}",
                path,
            )

        tolerance = (10 ** -decimal_places) / 2 + 1e-12
        if (
            percentage_valid
            and expected_percentage is not None
            and not math.isclose(
                float(percentage),
                expected_percentage,
                rel_tol=0,
                abs_tol=tolerance,
            )
        ):
            self.error(
                f"{code_prefix}-math",
                f"{label} {percentage_field}={percentage!r}, expected {expected_percentage}",
                path,
            )

        if legacy_valid:
            normalized_legacy = float(legacy) * (100 if legacy_is_ratio else 1)
            disagrees_with_percentage = (
                percentage_valid
                and not math.isclose(
                    float(percentage),
                    normalized_legacy,
                    rel_tol=0,
                    abs_tol=tolerance,
                )
            )
            disagrees_with_math = (
                expected_percentage is not None
                and not math.isclose(
                    normalized_legacy,
                    expected_percentage,
                    rel_tol=0,
                    abs_tol=tolerance,
                )
            )
            if disagrees_with_percentage or disagrees_with_math:
                self.error(
                    f"{code_prefix}-alias",
                    (
                        f"{label} {percentage_field}={percentage!r} disagrees with "
                        f"legacy pass_rate={legacy!r}"
                    ),
                    path,
                )

    def _audit_ci_health_build_rate(self, build: dict, label: str) -> None:
        path = "data/vllm/ci/ci_health.json"
        passed = build.get("passed")
        failed = build.get("failed")
        expected_percentage = None
        if not _is_nonnegative_int(passed) or not _is_nonnegative_int(failed):
            self.error(
                "ci-health-test-pass-rate-counts",
                f"{label} passed/failed assertion counts must be non-negative integers",
                path,
            )
        else:
            ran = passed + failed
            expected_percentage = round(passed / ran * 100, 2) if ran else 0.0
        self._audit_percentage_rate(
            build,
            label=label,
            path=path,
            code_prefix="ci-health-test-pass-rate",
            percentage_field="test_pass_rate_pct",
            basis_field="test_pass_rate_basis",
            expected_basis="pytest_assertions_excluding_skipped",
            expected_percentage=expected_percentage,
            decimal_places=2,
            legacy_is_ratio=True,
        )

    def audit_ci_health(self) -> None:
        health = self.load_json("data/vllm/ci/ci_health.json", {})
        if not isinstance(health, dict):
            return

        rate_contract_enabled = self._pass_rate_contract_enabled(
            health.get("pass_rate_contract_version"),
            label="ci_health.json",
            path="data/vllm/ci/ci_health.json",
            code_prefix="ci-health-pass-rate-contract",
        )

        metrics: dict[str, Any] = {}
        for side, suffix in (("amd", "amd"), ("upstream", "upstream")):
            latest = ((health.get(side) or {}).get("latest_build") or {})
            if not latest:
                self.error("ci-health-latest", f"ci_health.json lacks {side}.latest_build")
                continue
            path = self.latest_result_file(suffix)
            result_numbers = self.build_numbers_in_jsonl(path)
            build_number = latest.get("build_number") or latest.get("number")
            if result_numbers and build_number not in result_numbers:
                self.error(
                    "ci-health-jsonl-build-mismatch",
                    f"{side} ci_health latest build #{build_number} does not match {path.name} build numbers {sorted(result_numbers)}",
                    "data/vllm/ci/ci_health.json",
                    context={"pipeline": side},
                )
            total = latest.get("total_tests", 0)
            counted = latest.get("passed", 0) + latest.get("failed", 0) + latest.get("skipped", 0)
            if total != counted:
                self.error(
                    "ci-health-total",
                    f"{side} total_tests={total} but passed+failed+skipped={counted}",
                    "data/vllm/ci/ci_health.json",
                )
            if rate_contract_enabled:
                seen_rows: set[int] = set()
                for key in (
                    "latest_build",
                    "latest_test_signal_build",
                    "latest_pipeline_build",
                ):
                    row = _mapping(_mapping(health.get(side)).get(key))
                    if row and id(row) not in seen_rows:
                        seen_rows.add(id(row))
                        self._audit_ci_health_build_rate(row, f"{side}.{key}")
                for index, raw_row in enumerate(
                    _rows(_mapping(health.get(side)).get("builds"))
                ):
                    row = _mapping(raw_row)
                    if row and id(row) not in seen_rows:
                        seen_rows.add(id(row))
                        self._audit_ci_health_build_rate(
                            row,
                            f"{side}.builds[{index}]",
                        )
            metrics[side] = {
                "build_number": build_number,
                "total_tests": total,
                "groups": latest.get("unique_test_groups"),
                "by_hardware": {
                    hw: row.get("groups")
                    for hw, row in (latest.get("by_hardware") or {}).items()
                    if str(hw).startswith("mi")
                },
            }
        self.report.metrics["ci_health"] = metrics

    def audit_root_test_results(self) -> None:
        path = "data/vllm/test_results.json"
        payload = self.load_json(path, {})
        if not isinstance(payload, dict):
            return

        rate_contract_enabled = self._pass_rate_contract_enabled(
            payload.get("pass_rate_contract_version"),
            label="test_results.json",
            path=path,
            code_prefix="root-test-results-pass-rate-contract",
        )

        metrics: dict[str, Any] = {}
        for platform in ("rocm", "cuda"):
            block = payload.get(platform)
            if block is None:
                continue
            if not isinstance(block, dict):
                self.error(
                    "root-test-results-platform",
                    f"test_results.json {platform} must be an object",
                    path,
                )
                continue
            summary = _mapping(block.get("summary"))
            if not rate_contract_enabled:
                continue
            assertions = _mapping(summary.get("test_assertions"))
            required_counts = ("total", "passed", "failed", "skipped")
            counts_valid = all(
                _is_nonnegative_int(assertions.get(key)) for key in required_counts
            )
            expected_percentage = None
            if not counts_valid:
                self.error(
                    "root-test-results-test-pass-rate-counts",
                    (
                        f"{platform}.summary.test_assertions must contain non-negative "
                        "integer total/passed/failed/skipped counts"
                    ),
                    path,
                )
            else:
                counted = (
                    assertions["passed"]
                    + assertions["failed"]
                    + assertions["skipped"]
                )
                if assertions["total"] != counted:
                    self.error(
                        "root-test-results-test-pass-rate-counts",
                        (
                            f"{platform}.summary.test_assertions.total={assertions['total']} "
                            f"but passed+failed+skipped={counted}"
                        ),
                        path,
                    )
                ran = assertions["passed"] + assertions["failed"]
                expected_percentage = (
                    round(assertions["passed"] / ran * 100, 1) if ran else 0.0
                )
            self._audit_percentage_rate(
                summary,
                label=f"{platform}.summary",
                path=path,
                code_prefix="root-test-results-test-pass-rate",
                percentage_field="test_pass_rate_pct",
                basis_field="test_pass_rate_basis",
                expected_basis="pytest_assertions_excluding_skipped",
                expected_percentage=expected_percentage,
                decimal_places=1,
                legacy_is_ratio=False,
            )
            metrics[platform] = {
                "test_assertions": assertions,
                "test_pass_rate_pct": summary.get("test_pass_rate_pct"),
            }
        self.report.metrics["root_test_results"] = metrics

    def audit_gating_target_candidates(self) -> None:
        payload = self.load_json("data/vllm/ci/gating_target_candidates.json", {})
        if not isinstance(payload, dict):
            return
        rows = payload.get("rows") or []
        if not isinstance(rows, list):
            self.error(
                "gating-target-candidates-shape",
                "gating_target_candidates.json rows must be a list",
                "data/vllm/ci/gating_target_candidates.json",
            )
            return
        default_gpu_offenders = [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("decision") == "excluded"
            and "not_gpu_like" in (row.get("exclusion_reasons") or [])
            and re.search(r"(^|[^a-z0-9])gpu_", str(row.get("queue") or ""), re.IGNORECASE)
        ]
        if default_gpu_offenders:
            examples = "; ".join(
                f"{row.get('label')} on {row.get('queue')}"
                for row in default_gpu_offenders[:5]
            )
            self.error(
                "gating-target-gpu-queue-excluded",
                "Default Buildkite GPU queue rows must not be excluded as not_gpu_like. "
                f"Examples: {examples}",
                "data/vllm/ci/gating_target_candidates.json",
            )
        self.report.metrics["gating_target_candidates"] = {
            "rows": len(rows),
            "default_gpu_not_gpu_like_exclusions": len(default_gpu_offenders),
            "summary": payload.get("summary") or {},
        }

    def _audit_analytics_build_rate(self, summary: dict, label: str) -> None:
        path = "data/vllm/ci/analytics.json"
        passed = summary.get("passed")
        failed = summary.get("failed")
        terminal_builds = summary.get("terminal_builds")
        expected_percentage = None
        if (
            not _is_nonnegative_int(passed)
            or not _is_nonnegative_int(failed)
            or not _is_nonnegative_int(terminal_builds)
            or terminal_builds != passed + failed
        ):
            self.error(
                "analytics-build-pass-rate-counts",
                (
                    f"{label} passed/failed/terminal_builds must be non-negative "
                    "integers with terminal_builds = passed + failed"
                ),
                path,
            )
        else:
            expected_percentage = (
                round(passed / terminal_builds * 100, 1)
                if terminal_builds
                else 0.0
            )
        self._audit_percentage_rate(
            summary,
            label=label,
            path=path,
            code_prefix="analytics-build-pass-rate",
            percentage_field="build_pass_rate_pct",
            basis_field="build_pass_rate_basis",
            expected_basis="terminal_build_state_all_green",
            expected_percentage=expected_percentage,
            decimal_places=1,
            legacy_is_ratio=False,
        )

    def audit_analytics(self) -> None:
        # Imported only for the complete dashboard audit; DNS-only validation
        # must remain independent of analytics packages and their dependencies.
        from vllm.ci.reliability_history import (
            hydrate_reliability_observations,
            validate_all_main_reliability,
        )

        analytics = self.load_json("data/vllm/ci/analytics.json", {})
        if not isinstance(analytics, dict):
            return
        metrics: dict[str, Any] = {}

        for slug in ("amd-ci", "ci"):
            block = analytics.get(slug)
            if not isinstance(block, dict):
                self.error("analytics-pipeline-missing", f"analytics.json missing {slug}")
                continue
            builds = _rows(block.get("builds"))
            rate_contract_enabled = self._pass_rate_contract_enabled(
                block.get("pass_rate_contract_version"),
                label=f"analytics.json[{slug}]",
                path="data/vllm/ci/analytics.json",
                code_prefix="analytics-pass-rate-contract",
            )
            if not builds:
                self.error("analytics-empty-builds", f"{slug} analytics has no builds")
                continue

            if rate_contract_enabled:
                self._audit_analytics_build_rate(
                    _mapping(block.get("summary")),
                    f"{slug}.summary",
                )

            suffix = RESULT_SUFFIXES[slug]
            latest_results = self.latest_result_file(suffix)
            result_numbers = self.build_numbers_in_jsonl(latest_results)
            latest = _mapping(builds[0])
            if result_numbers and latest.get("number") not in result_numbers:
                self.report_cross_surface_build_mismatch(
                    "analytics-jsonl-build-mismatch",
                    f"{slug} latest analytics build #{latest.get('number')} does not match {latest_results.name} build numbers {sorted(result_numbers)}",
                    "data/vllm/ci/analytics.json",
                    left_surface="ci_analytics",
                    left_build=latest.get("number"),
                    right_surface="ci_core",
                    right_build=max(result_numbers),
                    context={
                        "pipeline": "amd" if slug == "amd-ci" else "upstream"
                    },
                )
            if result_numbers and latest.get("source") != "test_results":
                self.warning(
                    "analytics-source",
                    f"{slug} latest build is not sourced from parsed test_results",
                    "data/vllm/ci/analytics.json",
                )

            windows = _mapping(block.get("windows"))
            default_window = block.get("default_window")
            if default_window not in windows:
                self.error(
                    "analytics-default-window",
                    f"{slug} default_window={default_window!r} is absent from windows",
                    "data/vllm/ci/analytics.json",
                )
            for key in ("1d", "3d", "7d", "14d", "30d"):
                if key not in windows:
                    self.error(
                        "analytics-window-missing",
                        f"{slug} missing precomputed {key} window",
                        "data/vllm/ci/analytics.json",
                    )
            if rate_contract_enabled:
                for window_name, raw_window in windows.items():
                    window = _mapping(raw_window)
                    if "summary" in window:
                        self._audit_analytics_build_rate(
                            _mapping(window.get("summary")),
                            f"{slug}.windows[{window_name!r}].summary",
                        )

            chartable_builds = [
                b
                for b in _rows(_mapping(windows.get(default_window)).get("builds")) or builds
                if isinstance(b, dict) and _safe_int(b.get("total_jobs")) > 10
            ]
            if len(chartable_builds) < 2:
                self.error(
                    "analytics-chart-empty",
                    f"{slug} default window has fewer than two chartable builds",
                    "data/vllm/ci/analytics.json",
                )

            for raw_build in builds[:20]:
                build = _mapping(raw_build)
                jobs = _rows(build.get("jobs"))
                if build.get("total_jobs") != len(jobs):
                    self.error(
                        "analytics-total-jobs",
                        f"{slug} build #{build.get('number')} total_jobs={build.get('total_jobs')} but has {len(jobs)} jobs",
                        "data/vllm/ci/analytics.json",
                    )
                state_counts = {
                    "passed": sum(
                        1 for j in jobs
                        if isinstance(j, dict) and j.get("state") == "passed"
                    ),
                    "failed": sum(
                        1
                        for j in jobs
                        if isinstance(j, dict)
                        and j.get("state") in {"failed", "timed_out", "broken"}
                    ),
                    "soft_failed": sum(
                        1 for j in jobs
                        if isinstance(j, dict) and j.get("state") == "soft_fail"
                    ),
                    "skipped": sum(
                        1 for j in jobs
                        if isinstance(j, dict) and j.get("state") == "skipped"
                    ),
                }
                for key, expected in state_counts.items():
                    if build.get(key, 0) != expected:
                        self.error(
                            "analytics-job-counts",
                            f"{slug} build #{build.get('number')} {key}={build.get(key)} but jobs imply {expected}",
                            "data/vllm/ci/analytics.json",
                        )

            rankings = _rows(block.get("duration_ranking"))
            too_long = [
                row
                for row in rankings
                if isinstance(row, dict)
                and isinstance(row.get("median_dur"), (int, float))
                and row["median_dur"] > 360
            ]
            if too_long:
                self.warning(
                    "analytics-duration-units",
                    f"{slug} has median job durations over 6h; check seconds/minutes conversion",
                    "data/vllm/ci/analytics.json",
                )
            all_main_metrics = None
            all_main = block.get("all_main_reliability") or {}
            if slug == "amd-ci" and block.get("main_retry_analysis"):
                self.error(
                    "analytics-amd-retry-analysis",
                    (
                        "AMD analytics may publish strict main reliability, "
                        "but not upstream flake/retry analysis"
                    ),
                    "data/vllm/ci/analytics.json",
                )
            if not all_main:
                if slug == "ci":
                    self.error(
                        "analytics-all-main-missing",
                        f"{slug} analytics must retain a separate all-main reliability cohort",
                        "data/vllm/ci/analytics.json",
                    )
            elif not isinstance(all_main, dict) or not isinstance(all_main.get("groups"), list):
                self.error(
                    "analytics-all-main-missing",
                    f"{slug} all-main reliability cohort is malformed",
                    "data/vllm/ci/analytics.json",
                )
            else:
                if not validate_all_main_reliability(all_main, slug):
                    self.error(
                        "analytics-all-main-schema",
                        f"{slug} all-main reliability cohort fails its versioned schema contract",
                        "data/vllm/ci/analytics.json",
                    )
                cohort = _mapping(all_main.get("cohort"))
                denominator = _mapping(all_main.get("denominator"))
                groups = _rows(all_main.get("groups"))
                provenance = _mapping(all_main.get("provenance"))
                collection = _mapping(provenance.get("collection"))
                cohort_builds_rows = _rows(all_main.get("builds"))
                build_states = cohort.get("build_states")
                expected_endpoint = f"/organizations/vllm/pipelines/{slug}/builds"
                if (
                    cohort.get("id") != f"{slug}-main-completed-pass-fail"
                    or cohort.get("branch") != "main"
                    or cohort.get("pipeline") != slug
                    or not isinstance(build_states, list)
                    or any(not isinstance(state, str) for state in build_states)
                    or set(build_states) != {"failed", "passed"}
                    or provenance.get("pipeline") != slug
                    or _mapping(provenance.get("query")).get("branch") != "main"
                    or not str(provenance.get("endpoint") or "").endswith(expected_endpoint)
                    or cohort.get("exhaustive") is not True
                    or collection.get("exhaustive") is not True
                ):
                    self.error(
                        "analytics-all-main-branch",
                        (
                            f"{slug} all-main cohort does not prove the strict completed branch=main "
                            "Buildkite query"
                        ),
                        "data/vllm/ci/analytics.json",
                    )
                malformed_builds = [
                    row
                    for row in cohort_builds_rows
                    if not isinstance(row, dict)
                    or row.get("branch") != "main"
                    or str(row.get("state") or "").lower() not in {"failed", "passed"}
                    or not row.get("finished_at")
                    or not _buildkite_url_matches(row.get("url"), slug, row.get("number"))
                ]
                if malformed_builds or len(cohort_builds_rows) != _safe_int(cohort.get("build_count")):
                    self.error(
                        "analytics-all-main-build-provenance",
                        (
                            f"{slug} strict cohort has {len(malformed_builds)} malformed builds and "
                            f"{len(cohort_builds_rows)} rows for declared count {cohort.get('build_count')}"
                        ),
                        "data/vllm/ci/analytics.json",
                    )
                cohort_builds = _safe_int(cohort.get("build_count"))
                cohort_nightlies = _safe_int(cohort.get("canonical_nightly_build_count"))
                cohort_other_main = _safe_int(cohort.get("non_nightly_main_build_count"))
                if cohort_builds != cohort_nightlies + cohort_other_main:
                    self.error(
                        "analytics-all-main-cohort-composition",
                        (
                            f"all-main builds={cohort_builds}, canonical nightlies={cohort_nightlies}, "
                            f"other main={cohort_other_main}"
                        ),
                        "data/vllm/ci/analytics.json",
                    )
                eligible = sum(
                    _safe_int(_mapping(group).get("denominator")) for group in groups
                )
                expected_eligible = _safe_int(denominator.get("eligible_observations"))
                if eligible != expected_eligible:
                    self.error(
                        "analytics-all-main-denominator",
                        f"group denominators sum to {eligible}, cohort reports {expected_eligible}",
                        "data/vllm/ci/analytics.json",
                    )
                raw_observations = [
                    observation
                    for group in groups
                    for observation in _rows(_mapping(group).get("observations"))
                    if isinstance(observation, dict)
                ]
                try:
                    all_observations = hydrate_reliability_observations(
                        all_main,
                        raw_observations,
                        pipeline_slug=slug,
                    )
                except (KeyError, TypeError, ValueError):
                    all_observations = []
                    self.error(
                        "analytics-all-main-hydration",
                        f"{slug} has observations that cannot be rehydrated",
                        "data/vllm/ci/analytics.json",
                    )
                retained = [
                    observation
                    for observation in all_observations
                    if observation.get("eligible_for_reliability")
                ]
                cohort_build_numbers = {
                    _safe_int(_mapping(row).get("number"), -1)
                    for row in cohort_builds_rows
                    if _safe_int(_mapping(row).get("number"), -1) > 0
                }
                missing_links = sum(not observation.get("job_url") for observation in all_observations)
                wrong_pipeline_links = sum(
                    _safe_int(observation.get("build_number"), -1) not in cohort_build_numbers
                    or not _buildkite_url_matches(
                        observation.get("build_url"), slug, observation.get("build_number")
                    )
                    or not _buildkite_url_matches(
                        observation.get("job_url"),
                        slug,
                        observation.get("build_number"),
                        require_job=True,
                    )
                    for observation in all_observations
                )
                if missing_links or wrong_pipeline_links:
                    self.error(
                        "analytics-all-main-links",
                        (
                            f"{missing_links} retained observations lack an exact job URL and "
                            f"{wrong_pipeline_links} link to a different pipeline"
                        ),
                        "data/vllm/ci/analytics.json",
                    )
                retry = _mapping(block.get("main_retry_analysis"))
                retry_provenance = _mapping(retry.get("provenance"))
                attempts = _rows(retry.get("retry_attempts"))
                recoveries = _rows(retry.get("failed_then_passed_recoveries"))
                if slug == "ci" and retry.get("available") is True:
                    retry_cohort = _strict_positive_int_set(
                        retry_provenance.get("cohort_build_numbers")
                    )
                    invalid_retry = (
                        retry_provenance.get("source_pipeline") != "ci"
                        or retry_provenance.get("complete") is not True
                        or retry_cohort != cohort_build_numbers
                        or any(
                            not isinstance(row, dict)
                            or _safe_int(row.get("build_number"), -1) not in cohort_build_numbers
                            or not _buildkite_url_matches(
                                row.get("job_url") or row.get("url"),
                                "ci",
                                row.get("build_number"),
                                require_job=True,
                            )
                            for row in attempts
                        )
                        or any(
                            not isinstance(row, dict)
                            or _safe_int(row.get("build_number"), -1) not in cohort_build_numbers
                            or not _buildkite_url_matches(
                                row.get("failed_url"),
                                "ci",
                                row.get("build_number"),
                                require_job=True,
                            )
                            or not _buildkite_url_matches(
                                row.get("passed_url"),
                                "ci",
                                row.get("build_number"),
                                require_job=True,
                            )
                            for row in recoveries
                        )
                    )
                    if invalid_retry:
                        self.error(
                            "analytics-main-retry-provenance",
                            "CI retry evidence is not transitively bound to the exhaustive upstream cohort",
                            "data/vllm/ci/analytics.json",
                        )
                elif slug == "ci" and (attempts or recoveries):
                    self.error(
                        "analytics-main-retry-unavailable-rows",
                        "Unavailable retry analysis must not publish partial attempt rows",
                        "data/vllm/ci/analytics.json",
                    )
                retired = [
                    _mapping(group).get("name")
                    for group in groups
                    if is_retired_queue(_mapping(group).get("queue"))
                ]
                if retired:
                    self.error(
                        "analytics-retired-queue",
                        f"all-main reliability contains {len(retired)} retired queue variants",
                        "data/vllm/ci/analytics.json",
                    )
                all_main_metrics = {
                    "builds": cohort.get("build_count"),
                    "nightlies": cohort.get("canonical_nightly_build_count"),
                    "non_nightly_main": cohort.get("non_nightly_main_build_count"),
                    "groups": len(groups),
                    "eligible_observations": eligible,
                    "retained_linked_observations": len(all_observations) - missing_links,
                }
            metrics[slug] = {
                "builds": len(builds),
                "latest_build": latest.get("number"),
                "latest_source": latest.get("source"),
                "default_window": default_window,
                "failure_rankings": len(_rows(block.get("failure_ranking"))),
                "duration_rankings": len(_rows(block.get("duration_ranking"))),
            }
            if all_main_metrics is not None:
                metrics[slug]["all_main"] = all_main_metrics
        self.report.metrics["analytics"] = metrics

    def matrix_cell_stats(self, matrix: dict[str, Any]) -> dict[str, Any]:
        architectures = [a.get("id") for a in matrix.get("architectures") or [] if a.get("id")]
        rows = matrix.get("rows") or []
        stats: dict[str, Any] = {
            "unique_groups": len(rows),
            "architecture_count": len(architectures),
            "hardware_cells": 0,
            "latest_matched_cells": 0,
            "passing_cells": 0,
            "failing_cells": 0,
            "waiting_cells": 0,
            "unknown_cells": 0,
            "fully_shared_groups": 0,
            "single_arch_groups": 0,
            "multi_variant_cells": 0,
            "attention_families": 0,
            "by_arch": {
                arch: {"total": 0, "matched": 0, "passing": 0, "failing": 0, "waiting": 0, "unknown": 0}
                for arch in architectures
            },
        }

        for row in rows:
            row_coverage = 0
            row_nightly = 0
            row_attention = False
            for arch in architectures:
                cell = ((row.get("cells") or {}).get(arch) or {})
                if not cell.get("exists"):
                    continue
                row_coverage += 1
                stats["hardware_cells"] += 1
                stats["by_arch"][arch]["total"] += 1

                raw_variant_count = cell.get("raw_variant_count", cell.get("variant_count", 0))
                if raw_variant_count > 1:
                    stats["multi_variant_cells"] += 1

                if cell.get("latest_matched"):
                    row_nightly += 1
                    stats["latest_matched_cells"] += 1
                    stats["by_arch"][arch]["matched"] += 1
                else:
                    row_attention = True

                state = cell.get("latest_state")
                if state == "passed":
                    stats["passing_cells"] += 1
                    stats["by_arch"][arch]["passing"] += 1
                elif state in AMD_FAILURE_STATES:
                    row_attention = True
                    stats["failing_cells"] += 1
                    stats["by_arch"][arch]["failing"] += 1
                elif state in AMD_WAITING_STATES:
                    row_attention = True
                    stats["waiting_cells"] += 1
                    stats["by_arch"][arch]["waiting"] += 1
                else:
                    stats["unknown_cells"] += 1
                    stats["by_arch"][arch]["unknown"] += 1

            if row_coverage == len(architectures):
                stats["fully_shared_groups"] += 1
            if row_coverage == 1:
                stats["single_arch_groups"] += 1
            if row_attention:
                stats["attention_families"] += 1
            if row.get("coverage_count") != row_coverage:
                self.error(
                    "matrix-row-coverage",
                    f"{row.get('title')} coverage_count={row.get('coverage_count')} but cells imply {row_coverage}",
                    "data/vllm/ci/amd_test_matrix.json",
                )
            if row.get("nightly_coverage_count") != row_nightly:
                self.error(
                    "matrix-row-nightly-coverage",
                    f"{row.get('title')} nightly_coverage_count={row.get('nightly_coverage_count')} but cells imply {row_nightly}",
                    "data/vllm/ci/amd_test_matrix.json",
                )
        return stats

    def matrix_health_policy_stats(
        self,
        rows: list[dict[str, Any]],
        *,
        reduce_duplicates: bool,
        ignore_mi355_only: bool,
    ) -> dict[str, Any]:
        components: dict[str, list[dict[str, Any]]] = {}
        for index, row in enumerate(rows):
            group_id = str(
                row.get("duplicate_group_id")
                or row.get("id")
                or f"legacy-row-{index}"
            )
            components.setdefault(group_id, []).append(row)

        if reduce_duplicates:
            candidates = [
                (members, members)
                for members in components.values()
            ]
        else:
            candidates = [
                ([row], components[str(
                    row.get("duplicate_group_id")
                    or row.get("id")
                    or f"legacy-row-{index}"
                )])
                for index, row in enumerate(rows)
            ]

        def cells(
            selected_rows: list[dict[str, Any]],
            architectures: set[str],
        ) -> list[dict[str, Any]]:
            return [
                cell
                for row in selected_rows
                for arch, cell in (row.get("cells") or {}).items()
                if arch in architectures and cell.get("exists")
            ]

        counts = {
            "passing_groups": 0,
            "failed_only_groups": 0,
            "mixed_groups": 0,
            "waiting_groups": 0,
            "unknown_groups": 0,
            "ignored_mi355_only_groups": 0,
            "inherited_mi355_groups": 0,
        }
        core_architectures = {"mi250", "mi300", "mi325"}
        for candidate_rows, component_rows in candidates:
            candidate_core = cells(candidate_rows, core_architectures)
            candidate_mi355 = cells(candidate_rows, {"mi355"})
            component_core = cells(component_rows, core_architectures)
            if candidate_core:
                signal_cells = candidate_core
                if candidate_mi355:
                    counts["inherited_mi355_groups"] += 1
            elif component_core:
                signal_cells = component_core
                counts["inherited_mi355_groups"] += 1
            elif ignore_mi355_only:
                counts["ignored_mi355_only_groups"] += 1
                continue
            else:
                signal_cells = candidate_mi355

            states = {
                str(cell.get("latest_state") or "").casefold()
                for cell in signal_cells
            }
            has_pass = "passed" in states
            has_incident = bool(states & AMD_FAILURE_STATES)
            if has_pass and has_incident:
                counts["mixed_groups"] += 1
            elif has_pass:
                counts["passing_groups"] += 1
            elif has_incident:
                counts["failed_only_groups"] += 1
            elif states & AMD_WAITING_STATES:
                counts["waiting_groups"] += 1
            else:
                counts["unknown_groups"] += 1

        counts["failing_groups"] = (
            counts["failed_only_groups"] + counts["mixed_groups"]
        )
        counts["resolved_groups"] = (
            counts["passing_groups"] + counts["failing_groups"]
        )
        counts["included_groups"] = (
            counts["resolved_groups"]
            + counts["waiting_groups"]
            + counts["unknown_groups"]
        )
        counts["pass_percentage"] = round(
            counts["passing_groups"] / counts["resolved_groups"] * 100,
            1,
        ) if counts["resolved_groups"] else None
        counts["reduce_duplicates"] = reduce_duplicates
        counts["ignore_mi355_only"] = ignore_mi355_only
        return counts

    def audit_best_hardware_health_groups(
        self,
        matrix: dict[str, Any],
        rows: list[dict[str, Any]],
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Reconcile the best-hardware test-group inventory with every matrix cell."""
        relpath = "data/vllm/ci/amd_test_matrix.json"
        health_policies = _mapping(summary.get("health_policies"))
        raw_groups = matrix.get("health_groups")
        raw_policy = matrix.get("best_hardware_policy")
        raw_summary = health_policies.get("best_hardware")
        contract_present = any(
            value is not None for value in (raw_groups, raw_policy, raw_summary)
        )
        if not contract_present:
            return {"available": False}
        if not isinstance(raw_groups, list):
            self.error(
                "matrix-best-hardware-schema",
                "health_groups must be an array",
                relpath,
            )
        if not isinstance(raw_policy, dict):
            self.error(
                "matrix-best-hardware-schema",
                "best_hardware_policy must be an object",
                relpath,
            )
        if not isinstance(raw_summary, dict):
            self.error(
                "matrix-best-hardware-schema",
                "summary.health_policies.best_hardware must be an object",
                relpath,
            )
        if not all(
            isinstance(value, expected)
            for value, expected in (
                (raw_groups, list),
                (raw_policy, dict),
                (raw_summary, dict),
            )
        ):
            return {"available": False}

        groups: list[Any] = raw_groups
        policy: dict[str, Any] = raw_policy
        best_summary: dict[str, Any] = raw_summary
        row_by_id: dict[str, dict[str, Any]] = {}
        expected_cells: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
        for raw_row in rows:
            row = _mapping(raw_row)
            row_id = row.get("id")
            if not isinstance(row_id, str) or not row_id:
                self.error(
                    "matrix-best-hardware-row-id",
                    "every matrix row must have a non-empty ID",
                    relpath,
                )
                continue
            if row_id in row_by_id:
                self.error(
                    "matrix-best-hardware-row-id",
                    f"duplicate matrix row ID {row_id}",
                    relpath,
                )
            row_by_id[row_id] = row
            for arch, raw_cell in _mapping(row.get("cells")).items():
                cell = _mapping(raw_cell)
                if cell.get("exists"):
                    expected_cells[(row_id, str(arch))] = (row, cell)

        allowed_statuses = {"passing", "failed", "waiting", "unknown"}
        allowed_kinds = {"generic_best_hardware", "mi355_sensitive"}
        group_by_id: dict[str, dict[str, Any]] = {}
        ownership: dict[tuple[str, str], list[str]] = {}
        observed_statuses = {status: 0 for status in allowed_statuses}
        observed_kinds = {kind: 0 for kind in allowed_kinds}

        def expected_status(states: list[str]) -> str:
            normalized = {str(state or "").casefold() for state in states}
            if "passed" in normalized:
                return "passing"
            if normalized & AMD_FAILURE_STATES:
                return "failed"
            if normalized & AMD_WAITING_STATES:
                return "waiting"
            return "unknown"

        for index, raw_group in enumerate(groups):
            if not isinstance(raw_group, dict):
                self.error(
                    "matrix-best-hardware-group-shape",
                    f"health_groups[{index}] must be an object",
                    relpath,
                )
                continue
            group = raw_group
            group_id = group.get("id")
            title = group.get("title")
            status = group.get("status")
            gate_kind = group.get("gate_kind")
            reason = group.get("classification_reason")
            members = group.get("members")
            if not isinstance(group_id, str) or not group_id:
                self.error(
                    "matrix-best-hardware-group-shape",
                    f"health_groups[{index}] has no non-empty ID",
                    relpath,
                )
                continue
            if group_id in group_by_id:
                self.error(
                    "matrix-best-hardware-group-id",
                    f"duplicate health-group ID {group_id}",
                    relpath,
                )
            group_by_id[group_id] = group
            if not isinstance(title, str) or not title.strip():
                self.error(
                    "matrix-best-hardware-group-shape",
                    f"health group {group_id} has no title",
                    relpath,
                )
            if status not in allowed_statuses:
                self.error(
                    "matrix-best-hardware-status",
                    f"health group {group_id} has invalid status {status!r}",
                    relpath,
                )
            else:
                observed_statuses[status] += 1
            if gate_kind not in allowed_kinds:
                self.error(
                    "matrix-best-hardware-gate-kind",
                    f"health group {group_id} has invalid gate_kind {gate_kind!r}",
                    relpath,
                )
            else:
                observed_kinds[gate_kind] += 1
            if not isinstance(reason, str) or not reason.strip():
                self.error(
                    "matrix-best-hardware-reason",
                    f"health group {group_id} has no classification reason",
                    relpath,
                )
            if not isinstance(members, list) or not members:
                self.error(
                    "matrix-best-hardware-member-shape",
                    f"health group {group_id} must own at least one member",
                    relpath,
                )
                continue

            member_keys: list[tuple[str, str]] = []
            member_states: list[str] = []
            for member_index, raw_member in enumerate(members):
                if not isinstance(raw_member, dict):
                    self.error(
                        "matrix-best-hardware-member-shape",
                        f"health group {group_id} member {member_index} is not an object",
                        relpath,
                    )
                    continue
                member = raw_member
                row_id = member.get("row_id")
                arch = member.get("architecture")
                if not isinstance(row_id, str) or not isinstance(arch, str):
                    self.error(
                        "matrix-best-hardware-member-shape",
                        f"health group {group_id} member {member_index} lacks row_id or architecture",
                        relpath,
                    )
                    continue
                key = (row_id, arch)
                member_keys.append(key)
                ownership.setdefault(key, []).append(group_id)
                source = expected_cells.get(key)
                if source is None:
                    self.error(
                        "matrix-best-hardware-cell-ownership",
                        f"health group {group_id} owns nonexistent cell {row_id}/{arch}",
                        relpath,
                    )
                    continue
                row, cell = source
                member_state = member.get("state")
                member_states.append(str(member_state or ""))
                if member_state != cell.get("latest_state"):
                    self.error(
                        "matrix-best-hardware-member-source",
                        f"member {row_id}/{arch} state does not match its matrix cell",
                        relpath,
                    )
                if member.get("latest_matched") is not bool(cell.get("latest_matched")):
                    self.error(
                        "matrix-best-hardware-member-source",
                        f"member {row_id}/{arch} latest_matched does not match its matrix cell",
                        relpath,
                    )
                if member.get("url") != cell.get("latest_url"):
                    self.error(
                        "matrix-best-hardware-member-source",
                        f"member {row_id}/{arch} URL does not match its matrix cell",
                        relpath,
                    )
                if member.get("latest_url") != cell.get("latest_url"):
                    self.error(
                        "matrix-best-hardware-member-source",
                        f"member {row_id}/{arch} latest_url does not match its matrix cell",
                        relpath,
                    )
                if member.get("build_number") != cell.get("latest_build_number"):
                    self.error(
                        "matrix-best-hardware-member-source",
                        f"member {row_id}/{arch} build number does not match its matrix cell",
                        relpath,
                    )
                if member.get("label") != cell.get("primary_label"):
                    self.error(
                        "matrix-best-hardware-member-source",
                        f"member {row_id}/{arch} label does not match its matrix cell",
                        relpath,
                    )
                if member.get("title") != row.get("title"):
                    self.error(
                        "matrix-best-hardware-member-source",
                        f"member {row_id}/{arch} title does not match its matrix row",
                        relpath,
                    )
                if member.get("command_fingerprint") != row.get("command_fingerprint"):
                    self.error(
                        "matrix-best-hardware-member-source",
                        f"member {row_id}/{arch} command fingerprint does not match its row",
                        relpath,
                    )
                commands = member.get("commands")
                if not isinstance(commands, list) or commands != (row.get("commands") or []):
                    self.error(
                        "matrix-best-hardware-member-source",
                        f"member {row_id}/{arch} commands do not match its row",
                        relpath,
                    )
                if (
                    not isinstance(member.get("source_url"), str)
                    or member.get("source_url") != _mapping(matrix.get("source")).get("yaml_url")
                ):
                    self.error(
                        "matrix-best-hardware-member-source",
                        f"member {row_id}/{arch} lacks the exact matrix YAML source",
                        relpath,
                    )
                agent_pools = member.get("agent_pools")
                if (
                    not isinstance(agent_pools, list)
                    or not agent_pools
                    or any(not isinstance(pool, str) or not pool for pool in agent_pools)
                ):
                    self.error(
                        "matrix-best-hardware-member-shape",
                        f"member {row_id}/{arch} has invalid agent_pools",
                        relpath,
                    )
                elif member.get("agent_pool") != ", ".join(agent_pools):
                    self.error(
                        "matrix-best-hardware-member-shape",
                        f"member {row_id}/{arch} agent_pool does not reconcile with agent_pools",
                        relpath,
                    )
                variants = member.get("variants")
                if not isinstance(variants, list) or not variants:
                    self.error(
                        "matrix-best-hardware-member-shape",
                        f"member {row_id}/{arch} variants must be a non-empty array",
                        relpath,
                    )
                else:
                    required_variant_keys = {
                        "label",
                        "agent_pool",
                        "optional",
                        "parallelism",
                        "state",
                        "url",
                    }
                    for variant_index, variant in enumerate(variants):
                        if (
                            not isinstance(variant, dict)
                            or not required_variant_keys <= set(variant)
                        ):
                            self.error(
                                "matrix-best-hardware-member-shape",
                                f"member {row_id}/{arch} variant {variant_index} has an invalid shape",
                                relpath,
                            )

            expected_member_rows = sorted({row_id for row_id, _ in member_keys})
            if sorted(group.get("member_row_ids") or []) != expected_member_rows:
                self.error(
                    "matrix-best-hardware-group-source",
                    f"health group {group_id} member_row_ids do not reconcile",
                    relpath,
                )
            expected_arches = sorted({arch for _, arch in member_keys})
            if sorted(group.get("architectures") or []) != expected_arches:
                self.error(
                    "matrix-best-hardware-group-source",
                    f"health group {group_id} architectures do not reconcile",
                    relpath,
                )
            calculated_status = expected_status(member_states)
            if status in allowed_statuses and status != calculated_status:
                self.error(
                    "matrix-best-hardware-status",
                    f"health group {group_id} status={status} but members imply {calculated_status}",
                    relpath,
                )
            if group.get("is_passing") is not (calculated_status == "passing"):
                self.error(
                    "matrix-best-hardware-status",
                    f"health group {group_id} is_passing disagrees with its members",
                    relpath,
                )
            if gate_kind == "mi355_sensitive" and (
                len(member_keys) != 1 or member_keys[0][1] != "mi355"
            ):
                self.error(
                    "matrix-best-hardware-sensitive-scope",
                    f"MI355-sensitive group {group_id} must own exactly one MI355 cell",
                    relpath,
                )

        missing_cells = sorted(set(expected_cells) - set(ownership))
        extra_cells = sorted(set(ownership) - set(expected_cells))
        duplicate_cells = sorted(
            key for key, owners in ownership.items() if len(owners) != 1
        )
        if missing_cells or extra_cells or duplicate_cells:
            self.error(
                "matrix-best-hardware-cell-ownership",
                (
                    "health groups must own every matrix cell exactly once; "
                    f"missing={len(missing_cells)}, extra={len(extra_cells)}, "
                    f"duplicate={len(duplicate_cells)}"
                ),
                relpath,
                context={
                    "missing": missing_cells,
                    "extra": extra_cells,
                    "duplicate": duplicate_cells,
                },
            )

        for key, (row, _) in expected_cells.items():
            group_ids = ownership.get(key) or []
            expected_group_id = group_ids[0] if len(group_ids) == 1 else None
            memberships = _mapping(row.get("health_memberships"))
            if memberships.get(key[1]) != expected_group_id:
                self.error(
                    "matrix-best-hardware-backref",
                    f"matrix row {key[0]} has an invalid {key[1]} health membership",
                    relpath,
                )

        expected_counts = {
            "health_group_count": len(group_by_id),
            "included_groups": len(group_by_id),
            "passing_groups": observed_statuses["passing"],
            "failed_only_groups": observed_statuses["failed"],
            "mixed_groups": 0,
            "failing_groups": observed_statuses["failed"],
            "waiting_groups": observed_statuses["waiting"],
            "unknown_groups": observed_statuses["unknown"],
            "resolved_groups": (
                observed_statuses["passing"] + observed_statuses["failed"]
            ),
            "generic_groups": observed_kinds["generic_best_hardware"],
            "generic_group_count": observed_kinds["generic_best_hardware"],
            "mi355_sensitive_groups": observed_kinds["mi355_sensitive"],
            "mi355_sensitive_group_count": observed_kinds["mi355_sensitive"],
        }
        for field_name, expected in expected_counts.items():
            if best_summary.get(field_name) != expected:
                self.error(
                    "matrix-best-hardware-summary",
                    f"best_hardware.{field_name}={best_summary.get(field_name)} but groups imply {expected}",
                    relpath,
                )
        if summary.get("health_group_count") != len(group_by_id):
            self.error(
                "matrix-best-hardware-summary",
                f"summary.health_group_count={summary.get('health_group_count')} but {len(group_by_id)} groups are published",
                relpath,
            )
        expected_percentage = (
            round(observed_statuses["passing"] / len(group_by_id) * 100, 1)
            if group_by_id
            else None
        )
        if best_summary.get("pass_percentage") != expected_percentage:
            self.error(
                "matrix-best-hardware-summary",
                f"best_hardware.pass_percentage={best_summary.get('pass_percentage')} but groups imply {expected_percentage}",
                relpath,
            )
        published_group_ids = [
            group.get("id") for group in groups if isinstance(group, dict)
        ]
        if best_summary.get("group_ids") != published_group_ids:
            self.error(
                "matrix-best-hardware-summary",
                "best_hardware.group_ids do not match the published group order",
                relpath,
            )

        classifications = policy.get("mi355_classification")
        if not isinstance(classifications, list):
            self.error(
                "matrix-best-hardware-classification",
                "best_hardware_policy.mi355_classification must be an array",
                relpath,
            )
            classifications = []
        classification_by_cell: dict[tuple[str, str], dict[str, Any]] = {}
        allowed_classifications = {"separate_gate", "generic_replica"}
        for index, raw_classification in enumerate(classifications):
            if not isinstance(raw_classification, dict):
                self.error(
                    "matrix-best-hardware-classification",
                    f"MI355 classification {index} is not an object",
                    relpath,
                )
                continue
            classification = raw_classification
            key = (str(classification.get("row_id") or ""), "mi355")
            if key in classification_by_cell:
                self.error(
                    "matrix-best-hardware-classification",
                    f"duplicate MI355 classification for {key[0]}",
                    relpath,
                )
            classification_by_cell[key] = classification
            classification_kind = classification.get("classification")
            if classification_kind not in allowed_classifications:
                self.error(
                    "matrix-best-hardware-classification",
                    f"MI355 classification {index} has invalid kind {classification_kind!r}",
                    relpath,
                )
            group_id = classification.get("health_group_id")
            group = group_by_id.get(group_id)
            if group is None or group_id not in ownership.get(key, []):
                self.error(
                    "matrix-best-hardware-classification",
                    f"MI355 classification {index} does not reference its owning gate",
                    relpath,
                )
                continue
            expected_kind = (
                "separate_gate"
                if group.get("gate_kind") == "mi355_sensitive"
                else "generic_replica"
            )
            if classification_kind != expected_kind:
                self.error(
                    "matrix-best-hardware-classification",
                    f"MI355 classification {index} disagrees with gate {group_id}",
                    relpath,
                )
            source = expected_cells.get(key)
            if source:
                row, cell = source
                if classification.get("title") != row.get("title"):
                    self.error(
                        "matrix-best-hardware-classification",
                        f"MI355 classification {index} title does not match its row",
                        relpath,
                    )
                if classification.get("label") != cell.get("primary_label"):
                    self.error(
                        "matrix-best-hardware-classification",
                        f"MI355 classification {index} label does not match its cell",
                        relpath,
                    )
            if classification.get("reason") != group.get("classification_reason"):
                self.error(
                    "matrix-best-hardware-classification",
                    f"MI355 classification {index} reason does not match its gate",
                    relpath,
                )

        expected_mi355_cells = {
            key for key in expected_cells if key[1] == "mi355"
        }
        if set(classification_by_cell) != expected_mi355_cells:
            self.error(
                "matrix-best-hardware-classification",
                (
                    "MI355 classification must cover every MI355 matrix cell exactly "
                    f"once; expected={len(expected_mi355_cells)}, "
                    f"published={len(classification_by_cell)}"
                ),
                relpath,
            )
        separate_count = sum(
            row.get("classification") == "separate_gate"
            for row in classification_by_cell.values()
        )
        if separate_count != observed_kinds["mi355_sensitive"]:
            self.error(
                "matrix-best-hardware-classification",
                f"{separate_count} separate MI355 classifications do not reconcile with {observed_kinds['mi355_sensitive']} sensitive gates",
                relpath,
            )

        raw_sensitive_rules = policy.get("mi355_sensitive_rules")
        raw_alias_rules = policy.get("generic_alias_rules")
        if not isinstance(raw_sensitive_rules, list) or not isinstance(
            raw_alias_rules, list
        ):
            self.error(
                "matrix-best-hardware-policy-rules",
                "best-hardware policy must publish sensitive and generic-alias rule arrays",
                relpath,
            )
        else:
            sensitive_rules = {
                str(rule.get("title") or ""): str(rule.get("reason") or "")
                for rule in raw_sensitive_rules
                if isinstance(rule, dict)
            }
            if (
                len(sensitive_rules) != len(raw_sensitive_rules)
                or any(not title or not reason for title, reason in sensitive_rules.items())
            ):
                self.error(
                    "matrix-best-hardware-policy-rules",
                    "MI355-sensitive rules must have unique non-empty titles and reasons",
                    relpath,
                )
            materialized_sensitive_titles = {
                str(expected_cells[key][0].get("canonical_title") or "")
                for key, classification in classification_by_cell.items()
                if classification.get("classification") == "separate_gate"
                and key in expected_cells
            }
            if set(sensitive_rules) != materialized_sensitive_titles:
                self.error(
                    "matrix-best-hardware-policy-rules",
                    "published MI355-sensitive rules do not exactly match the separate test groups",
                    relpath,
                    context={
                        "rules": sorted(sensitive_rules),
                        "materialized": sorted(materialized_sensitive_titles),
                    },
                )

            alias_rules = {
                str(rule.get("title") or ""): str(rule.get("reason") or "")
                for rule in raw_alias_rules
                if isinstance(rule, dict)
            }
            if (
                len(alias_rules) != len(raw_alias_rules)
                or any(not title or not reason for title, reason in alias_rules.items())
            ):
                self.error(
                    "matrix-best-hardware-policy-rules",
                    "generic alias rules must have unique non-empty titles and reasons",
                    relpath,
                )
            for alias_title, alias_reason in alias_rules.items():
                matches = [
                    (key, classification)
                    for key, classification in classification_by_cell.items()
                    if key in expected_cells
                    and expected_cells[key][0].get("canonical_title") == alias_title
                ]
                valid = len(matches) == 1
                if valid:
                    _, classification = matches[0]
                    group = group_by_id.get(classification.get("health_group_id"), {})
                    architectures = set(group.get("architectures") or [])
                    valid = (
                        classification.get("classification") == "generic_replica"
                        and "mi355" in architectures
                        and bool(architectures & {"mi250", "mi300", "mi325"})
                        and group.get("classification_reason") == alias_reason
                    )
                if not valid:
                    self.error(
                        "matrix-best-hardware-policy-rules",
                        f"generic alias {alias_title!r} did not collapse into a core best-hardware family",
                        relpath,
                    )

        return {
            "available": True,
            "health_groups": len(group_by_id),
            "passing_groups": observed_statuses["passing"],
            "failing_groups": observed_statuses["failed"],
            "waiting_groups": observed_statuses["waiting"],
            "unknown_groups": observed_statuses["unknown"],
            "generic_groups": observed_kinds["generic_best_hardware"],
            "mi355_sensitive_groups": observed_kinds["mi355_sensitive"],
            "classified_mi355_cells": len(classification_by_cell),
            "owned_hardware_cells": len(ownership),
            "pass_percentage": expected_percentage,
        }

    def audit_amd_matrix(self) -> None:
        matrix = self.load_json("data/vllm/ci/amd_test_matrix.json", {})
        if not isinstance(matrix, dict):
            return
        rows = matrix.get("rows") or []
        if not rows:
            self.error("matrix-empty", "amd_test_matrix.json has no rows")
            return

        stats = self.matrix_cell_stats(matrix)
        summary = matrix.get("summary") or {}
        for key in (
            "unique_groups",
            "architecture_count",
            "hardware_cells",
            "latest_matched_cells",
            "passing_cells",
            "failing_cells",
            "waiting_cells",
            "unknown_cells",
            "fully_shared_groups",
            "single_arch_groups",
            "multi_variant_cells",
        ):
            if summary.get(key) != stats[key]:
                self.error(
                    "matrix-summary",
                    f"summary.{key}={summary.get(key)} but rows imply {stats[key]}",
                    "data/vllm/ci/amd_test_matrix.json",
                )

        health_policies = summary.get("health_policies") or {}
        if health_policies:
            row_by_id = {row.get("id"): row for row in rows}
            group_ids = {
                row.get("duplicate_group_id")
                for row in rows
                if row.get("duplicate_group_id")
            }
            duplicate_groups = matrix.get("duplicate_groups") or []
            duplicate_rows = 0
            for group in duplicate_groups:
                member_ids = group.get("member_ids") or []
                duplicate_rows += len(member_ids)
                members = [row_by_id.get(member_id) for member_id in member_ids]
                if len(member_ids) < 2 or any(member is None for member in members):
                    self.error(
                        "matrix-duplicate-members",
                        f"duplicate group {group.get('id')} has invalid members",
                        "data/vllm/ci/amd_test_matrix.json",
                    )
                    continue
                fingerprints = {
                    member.get("command_fingerprint")
                    for member in members
                    if member
                }
                if len(fingerprints) != 1:
                    self.error(
                        "matrix-duplicate-commands",
                        f"duplicate group {group.get('id')} spans command fingerprints",
                        "data/vllm/ci/amd_test_matrix.json",
                    )
                if any(
                    len(str(match.get("shared_substring") or "")) < 2
                    for match in group.get("pair_matches") or []
                ):
                    self.error(
                        "matrix-duplicate-title-rule",
                        f"duplicate group {group.get('id')} contains a title match shorter than 2",
                        "data/vllm/ci/amd_test_matrix.json",
                    )

            expected_summary = {
                "definition_rows": len(rows),
                "reduced_unique_groups": len(group_ids),
                "duplicate_clusters": len(duplicate_groups),
                "duplicate_definition_rows": duplicate_rows,
            }
            for key, expected in expected_summary.items():
                if summary.get(key) != expected:
                    self.error(
                        "matrix-unique-summary",
                        f"summary.{key}={summary.get(key)} but rows imply {expected}",
                        "data/vllm/ci/amd_test_matrix.json",
                    )

            policy_specs = {
                "reduced_ignore_mi355": (True, True),
                "reduced_include_mi355": (True, False),
                "definitions_ignore_mi355": (False, True),
                "definitions_include_mi355": (False, False),
            }
            for key, flags in policy_specs.items():
                expected = self.matrix_health_policy_stats(
                    rows,
                    reduce_duplicates=flags[0],
                    ignore_mi355_only=flags[1],
                )
                if health_policies.get(key) != expected:
                    self.error(
                        "matrix-health-policy",
                        f"summary.health_policies.{key} does not reconcile with matrix rows",
                        "data/vllm/ci/amd_test_matrix.json",
                    )

        best_hardware_metrics = self.audit_best_hardware_health_groups(
            matrix,
            rows,
            summary,
        )

        source = matrix.get("source") or {}
        source_build = source.get("latest_build_number")
        analytics = self.load_json("data/vllm/ci/analytics.json", {})
        health = self.load_json("data/vllm/ci/ci_health.json", {})
        analytics_build = (((analytics.get("amd-ci") or {}).get("builds") or [{}])[0]).get("number")
        health_build = ((health.get("amd") or {}).get("latest_build") or {}).get("build_number")
        if analytics_build and source_build != analytics_build:
            self.report_cross_surface_build_mismatch(
                "matrix-analytics-build",
                f"matrix source build #{source_build} does not match analytics AMD latest #{analytics_build}",
                "data/vllm/ci/amd_test_matrix.json",
                left_surface="ci_core",
                left_build=source_build,
                right_surface="ci_analytics",
                right_build=analytics_build,
                context={"pipeline": "amd"},
            )
        if health_build and source_build != health_build:
            self.error(
                "matrix-health-build",
                f"matrix source build #{source_build} does not match ci_health AMD latest #{health_build}",
                "data/vllm/ci/amd_test_matrix.json",
                context={"pipeline": "amd"},
            )

        arch_counts = {a.get("id"): a for a in matrix.get("architectures") or []}
        for arch, arch_stats in stats["by_arch"].items():
            record = arch_counts.get(arch) or {}
            if record.get("group_count") != arch_stats["total"]:
                self.error(
                    "matrix-arch-group-count",
                    f"{arch} group_count={record.get('group_count')} but rows imply {arch_stats['total']}",
                    "data/vllm/ci/amd_test_matrix.json",
                )
            if record.get("nightly_match_count") != arch_stats["matched"]:
                self.error(
                    "matrix-arch-nightly-count",
                    f"{arch} nightly_match_count={record.get('nightly_match_count')} but rows imply {arch_stats['matched']}",
                    "data/vllm/ci/amd_test_matrix.json",
                )
            health_groups = (
                ((health.get("amd") or {}).get("latest_build") or {})
                .get("by_hardware", {})
                .get(arch, {})
                .get("groups")
            )
            # ci_health counts groups observed in the latest build. The matrix
            # total also includes configured cells that were not present in that
            # build, so its like-for-like denominator is the matched cell count.
            observed_groups = arch_stats["matched"]
            if health_groups is not None and health_groups != observed_groups:
                terminal_observed = observed_groups - arch_stats["waiting"]
                if terminal_observed <= health_groups <= observed_groups:
                    self.warning(
                        "matrix-health-hardware-count-in-progress",
                        f"{arch} matrix matched groups={observed_groups} including "
                        f"{arch_stats['waiting']} waiting cells; ci_health by_hardware "
                        f"currently reports {health_groups} observed terminal groups",
                        "data/vllm/ci/amd_test_matrix.json",
                    )
                else:
                    self.error(
                        "matrix-health-hardware-count",
                        f"{arch} matrix matched groups={observed_groups} but ci_health "
                        f"by_hardware groups={health_groups}",
                        "data/vllm/ci/amd_test_matrix.json",
                    )

        latest_url_build_re = re.compile(r"/builds/(\d+)")
        stale_urls: list[str] = []
        for row in rows:
            for cell in (row.get("cells") or {}).values():
                if not cell.get("exists"):
                    continue
                candidates = [cell.get("latest_url")]
                for variant in cell.get("variants") or []:
                    candidates.append(variant.get("latest_url"))
                    for entry in variant.get("entries") or []:
                        candidates.append(entry.get("latest_url"))
                for url in candidates:
                    if not url:
                        continue
                    match = latest_url_build_re.search(str(url))
                    if match and source_build and int(match.group(1)) != int(source_build):
                        stale_urls.append(str(url))
        if stale_urls:
            self.error(
                "matrix-stale-build-link",
                f"{len(stale_urls)} AMD matrix links point at a build other than #{source_build}",
                "data/vllm/ci/amd_test_matrix.json",
            )

        self.audit_parity_hardware_matches_matrix(matrix, stats)
        self.report.metrics["amd_matrix"] = {
            **{k: v for k, v in stats.items() if k != "by_arch"},
            "by_arch": stats["by_arch"],
            "latest_build": source_build,
            "best_hardware": best_hardware_metrics,
        }

    def audit_parity_hardware_matches_matrix(
        self,
        matrix: dict[str, Any],
        matrix_stats: dict[str, Any],
    ) -> None:
        parity = self.load_json("data/vllm/ci/parity_report.json", {})
        if not isinstance(parity, dict):
            return
        parity_stats: dict[str, dict[str, int]] = {}
        for group in parity.get("job_groups") or []:
            amd_hardware = group.get("amd_hardware")
            hardware = (
                amd_hardware
                if isinstance(amd_hardware, list)
                else (group.get("hardware") or [])
            )
            amd_hw_failures = group.get("amd_hw_failures")
            hw_failures = (
                amd_hw_failures
                if isinstance(amd_hw_failures, dict)
                else (group.get("hw_failures") or {})
            )
            amd_hw_canceled = group.get("amd_hw_canceled")
            hw_canceled = (
                amd_hw_canceled
                if isinstance(amd_hw_canceled, dict)
                else (group.get("hw_canceled") or {})
            )
            for hw in hardware:
                if not re.match(r"^mi\d+", str(hw), flags=re.I):
                    continue
                stats = parity_stats.setdefault(
                    hw,
                    {"passing": 0, "failing": 0, "pending": 0, "canceled": 0, "total": 0},
                )
                pending = bool(group.get("backfilled") or (group.get("hw_backfilled") or {}).get(hw))
                failed = hw_failures.get(hw, 0) > 0
                canceled = hw_canceled.get(hw, 0) > 0 and not failed
                if pending:
                    stats["pending"] += 1
                elif failed:
                    stats["failing"] += 1
                elif canceled:
                    stats["canceled"] += 1
                else:
                    stats["passing"] += 1
                stats["total"] += 1

        for arch, mstats in matrix_stats["by_arch"].items():
            pstats = parity_stats.get(arch, {})
            totals_match = pstats.get("total") == mstats["total"]
            if not totals_match:
                self.error(
                    "parity-matrix-hardware-total",
                    f"{arch} parity hardware total={pstats.get('total')} but AMD matrix total={mstats['total']}",
                    "data/vllm/ci/parity_report.json",
                )
            parity_failing = pstats.get("failing")
            if parity_failing != mstats["failing"]:
                diff = abs((parity_failing or 0) - mstats["failing"])
                if mstats.get("waiting", 0) and diff <= mstats["waiting"]:
                    self.warning(
                        "parity-matrix-hardware-failing-in-progress",
                        f"{arch} parity failing groups={parity_failing} and AMD matrix "
                        f"failing cells={mstats['failing']} differ by {diff} while "
                        f"{mstats['waiting']} matrix cells are still waiting",
                        "data/vllm/ci/parity_report.json",
                    )
                elif totals_match:
                    self.warning(
                        "parity-matrix-hardware-failing-final-state-drift",
                        f"{arch} parity retained test-result failing groups={parity_failing} "
                        f"but AMD matrix final-job failing cells={mstats['failing']}; "
                        f"hardware totals agree at {mstats['total']} and a retry can "
                        "change the final Buildkite state",
                        "data/vllm/ci/parity_report.json",
                    )
                else:
                    self.error(
                        "parity-matrix-hardware-failing",
                        f"{arch} parity failing groups={parity_failing} but AMD matrix failing cells={mstats['failing']}",
                        "data/vllm/ci/parity_report.json",
                    )
        self.report.metrics["parity_hardware"] = parity_stats

    def audit_queue_data(self, *, validate_derived: bool = False) -> None:
        rows = self.load_jsonl("data/vllm/ci/queue_timeseries.jsonl")
        if not rows:
            self.error(
                "queue-history-empty",
                "Queue timeseries must contain at least one valid snapshot",
                "data/vllm/ci/queue_timeseries.jsonl",
            )
            return
        if len(rows) < 2:
            # A new durable branch necessarily starts with one row. Treat that
            # as an honest bootstrap state so the next successful poll can add
            # history instead of deadlocking the producer forever.
            self.warning(
                "queue-history-bootstrap",
                "Queue timeseries contains its first valid snapshot",
                "data/vllm/ci/queue_timeseries.jsonl",
            )
        latest = rows[-1]
        latest_ts = parse_iso(latest.get("ts"))
        if latest_ts and latest_ts.tzinfo is not None:
            age_hours = (datetime.now(timezone.utc) - latest_ts).total_seconds() / 3600
            if age_hours > 6:
                self.warning(
                    "queue-stale",
                    f"latest queue snapshot is {age_hours:.1f}h old",
                    "data/vllm/ci/queue_timeseries.jsonl",
                )

        workload_mismatches: list[str] = []
        retired_queue_rows = 0
        previous_ts: datetime | None = None
        for idx, row in enumerate(rows, 1):
            row_ts = parse_iso(row.get("ts"))
            canonical_ts = (
                isinstance(row.get("ts"), str)
                and row["ts"].endswith("Z")
                and row_ts is not None
                and row_ts.tzinfo is not None
                and row_ts.utcoffset() == timedelta(0)
            )
            if not canonical_ts:
                self.error(
                    "queue-timestamp",
                    f"queue_timeseries row {idx} must use a UTC timestamp ending in Z",
                    "data/vllm/ci/queue_timeseries.jsonl",
                )
            elif previous_ts is not None and row_ts <= previous_ts:
                self.error(
                    "queue-timestamp-order",
                    f"queue_timeseries row {idx} is not strictly newer than the prior row",
                    "data/vllm/ci/queue_timeseries.jsonl",
                )
            if canonical_ts:
                previous_ts = row_ts

            queues = row.get("queues")
            if not isinstance(queues, dict):
                self.error(
                    "queue-row-shape",
                    f"queue_timeseries row {idx} queues must be an object",
                    "data/vllm/ci/queue_timeseries.jsonl",
                )
                continue
            if not isinstance(row.get("sources") or row.get("provenance"), dict):
                self.error(
                    "queue-row-provenance",
                    f"queue_timeseries row {idx} must identify its source coverage",
                    "data/vllm/ci/queue_timeseries.jsonl",
                )
            retired_queue_rows += sum(is_mi355b_queue(queue) for queue in queues)
            total_waiting = 0
            total_running = 0
            for queue, queue_row in queues.items():
                if not isinstance(queue, str) or not isinstance(queue_row, dict):
                    self.error(
                        "queue-row-shape",
                        f"queue_timeseries row {idx} contains an invalid queue entry",
                        "data/vllm/ci/queue_timeseries.jsonl",
                    )
                    continue
                for count_name in ("waiting", "running"):
                    count = queue_row.get(count_name)
                    if (
                        not isinstance(count, int)
                        or isinstance(count, bool)
                        or count < 0
                    ):
                        self.error(
                            "queue-count-shape",
                            f"row {idx} {queue}.{count_name} must be a non-negative integer",
                            "data/vllm/ci/queue_timeseries.jsonl",
                        )
                        continue
                    if count_name == "waiting":
                        total_waiting += count
                    else:
                        total_running += count
            for total_name in ("total_waiting", "total_running"):
                total_value = row.get(total_name)
                if (
                    not isinstance(total_value, int)
                    or isinstance(total_value, bool)
                    or total_value < 0
                ):
                    self.error(
                        "queue-total-shape",
                        f"queue_timeseries row {idx} {total_name} must be a non-negative integer",
                        "data/vllm/ci/queue_timeseries.jsonl",
                    )
            if row.get("total_waiting") != total_waiting:
                self.error(
                    "queue-total-waiting",
                    f"queue_timeseries row {idx} total_waiting={row.get('total_waiting')} but queues sum to {total_waiting}",
                    "data/vllm/ci/queue_timeseries.jsonl",
                )
            if row.get("total_running") != total_running:
                self.error(
                    "queue-total-running",
                    f"queue_timeseries row {idx} total_running={row.get('total_running')} but queues sum to {total_running}",
                    "data/vllm/ci/queue_timeseries.jsonl",
                )
            for queue, queue_row in queues.items():
                if not isinstance(queue_row, dict):
                    continue
                for key in ("waiting_by_workload", "running_by_workload"):
                    split = queue_row.get(key)
                    if not isinstance(split, dict):
                        continue
                    base_key = key.replace("_by_workload", "")
                    split_total = sum((v or 0) for v in split.values())
                    if split_total > (queue_row.get(base_key) or 0):
                        workload_mismatches.append(
                            f"row {idx} {queue}.{key}={split_total} above {base_key}={queue_row.get(base_key)}"
                        )
        if workload_mismatches:
            examples = "; ".join(workload_mismatches[:3])
            self.warning(
                "queue-workload-split-drift",
                f"{len(workload_mismatches)} queue workload split rows exceed their metric snapshot; likely API timing drift. Examples: {examples}",
                "data/vllm/ci/queue_timeseries.jsonl",
            )
        if retired_queue_rows:
            self.error(
                "queue-retired-mi355b",
                f"Queue history contains {retired_queue_rows} retired amd_mi355B queue rows",
                "data/vllm/ci/queue_timeseries.jsonl",
            )
        cutoff = (
            latest_ts.timestamp() - 72 * 3600
            if latest_ts and latest_ts.tzinfo is not None
            else None
        )
        recent_rows = [
            row
            for row in rows
            if cutoff is None
            or ((parse_iso(row.get("ts")) or datetime.fromtimestamp(0, timezone.utc)).timestamp() >= cutoff)
        ]
        amd_workload = 0
        for row in recent_rows:
            for queue, queue_row in (row.get("queues") or {}).items():
                if is_amd_queue(queue) and not is_retired_queue(queue):
                    amd_workload += (queue_row.get("waiting") or 0) + (queue_row.get("running") or 0)
        # Zero is a legitimate observation (for example during a quiet fleet
        # window). Availability and source coverage are explicit in each row;
        # traffic volume must never be used as a proxy for collector success.

        jobs = self.load_json("data/vllm/ci/queue_jobs.json", {})
        jobs_ts = parse_iso(jobs.get("ts")) if isinstance(jobs, dict) else None
        if (
            jobs_ts is None
            or jobs_ts.tzinfo is None
            or jobs_ts.utcoffset() != timedelta(0)
            or not isinstance(jobs.get("ts"), str)
            or not jobs["ts"].endswith("Z")
        ):
            self.error(
                "queue-jobs-timestamp",
                "queue_jobs.json must contain a valid snapshot timestamp",
                "data/vllm/ci/queue_jobs.json",
            )
        elif jobs.get("ts") != latest.get("ts"):
            self.error(
                "queue-jobs-generation-mismatch",
                "queue_jobs.json and the latest queue_timeseries row must be one generation",
                "data/vllm/ci/queue_jobs.json",
            )
        pending = jobs.get("pending") if isinstance(jobs, dict) else []
        running = jobs.get("running") if isinstance(jobs, dict) else []
        if not isinstance(pending, list) or not isinstance(running, list):
            self.error("queue-jobs-shape", "queue_jobs.json pending/running must be lists")
        else:
            for kind, job_rows in (("pending", pending), ("running", running)):
                for job in job_rows[:100]:
                    missing = {"name", "queue", "url"} - set(job.keys())
                    if kind == "pending":
                        missing -= {"url"} if job.get("analysis_excluded") else set()
                        missing |= {"wait_min"} - set(job.keys())
                    if missing:
                        self.error(
                            "queue-job-row",
                            f"{kind} job row missing {sorted(missing)}",
                            "data/vllm/ci/queue_jobs.json",
                        )

        self.report.metrics["queue"] = {
            "snapshots": len(rows),
            "latest_ts": latest.get("ts"),
            "latest_total_waiting": latest.get("total_waiting"),
            "latest_total_running": latest.get("total_running"),
            "amd_workload_72h": amd_workload,
            "pending_jobs": len(pending) if isinstance(pending, list) else None,
            "running_jobs": len(running) if isinstance(running, list) else None,
        }
        if validate_derived:
            self.audit_queue_derived_projections()

    def audit_queue_derived_projections(self) -> None:
        """Recompute the two queue-owned browser projections and compare exactly."""
        history_relpath = "data/vllm/ci/queue_timeseries.jsonl"
        section_relpath = "data/vllm/ci/operations_v2/queue.json"
        chart_relpath = "data/vllm/ci/queue_history_chart.json"
        data_dir = self.root / "data/vllm/ci"
        try:
            from vllm.build_operations_snapshot import (
                _filter_queue_snapshot,
                build_queue_history_chart,
                load_queue_history,
            )
            from vllm.build_queue_section import build_queue_section

            history = load_queue_history(data_dir / "queue_timeseries.jsonl")
            expected_section = build_queue_section(data_dir)
            expected_chart = build_queue_history_chart(
                [_filter_queue_snapshot(row) for row in history],
                history[-1].get("ts") if history else None,
            )
        except Exception as exc:
            self.error(
                "queue-derived-rebuild",
                f"Queue browser projections could not be recomputed: {exc}",
                history_relpath,
            )
            return

        section = self.load_json(section_relpath, {})
        if section != expected_section:
            self.error(
                "queue-section-projection",
                "operations_v2/queue.json does not exactly match the validated queue inputs",
                section_relpath,
            )
        chart = self.load_json(chart_relpath, {})
        if chart != expected_chart:
            self.error(
                "queue-history-chart-projection",
                "queue_history_chart.json does not exactly match the validated queue history",
                chart_relpath,
            )

    def audit_queue_lifecycle(
        self,
        source_path: Path | None = None,
        *,
        require_current_scope: bool = False,
    ) -> None:
        path_obj = source_path or (self.root / "data/vllm/ci/queue_lifecycle.json")
        path = self.rel(path_obj)
        if not path_obj.is_file() or path_obj.is_symlink():
            self.error(
                "queue-lifecycle-missing",
                "queue_lifecycle.json must be a regular file",
                path,
            )
            return
        try:
            payload = json.loads(path_obj.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self.error(
                "queue-lifecycle-json",
                f"queue lifecycle data is unreadable: {exc}",
                path,
            )
            return
        if not isinstance(payload, dict) or not payload:
            self.error(
                "queue-lifecycle-shape",
                "queue_lifecycle.json must contain a non-empty object",
                path,
            )
            return

        expected_queues = [
            f"amd_mi{family}_{width}"
            for family in (250, 300, 355)
            for width in (1, 2, 4, 8)
        ]
        scope = _mapping(payload.get("scope"))
        if scope.get("queues") != expected_queues:
            self.error(
                "queue-lifecycle-scope",
                "queue lifecycle scope must be exactly the twelve canonical MI250/MI300/MI355 queues",
                path,
            )
        if scope.get("families") != ["MI250", "MI300", "MI355"]:
            self.error(
                "queue-lifecycle-families",
                f"queue lifecycle families are {scope.get('families')!r}",
                path,
            )

        window = _mapping(payload.get("window"))
        window_start = _parse_timestamp(window.get("start"))
        window_end = _parse_timestamp(window.get("end_exclusive"))
        if window.get("hours") != 2 or not window_start or not window_end:
            self.error(
                "queue-lifecycle-window",
                "queue lifecycle must publish a parseable exact two-hour half-open window",
                path,
            )
        elif abs((window_end - window_start).total_seconds() - 7200) > 1:
            self.error(
                "queue-lifecycle-window-duration",
                f"queue lifecycle window spans {(window_end - window_start).total_seconds()} seconds",
                path,
            )

        generated_at = _parse_timestamp(payload.get("generated_at"))
        if not generated_at:
            self.error("queue-lifecycle-generated-at", "invalid generated_at", path)
        else:
            age_hours = (datetime.now(timezone.utc) - generated_at).total_seconds() / 3600
            if age_hours > 6:
                self.warning(
                    "queue-lifecycle-stale",
                    f"queue lifecycle aggregate is {age_hours:.1f}h old",
                    path,
                )

        coverage = _mapping(payload.get("coverage"))
        if not isinstance(coverage.get("complete"), bool):
            self.error(
                "queue-lifecycle-coverage-shape",
                "queue lifecycle coverage.complete must be boolean",
                path,
            )
        elif coverage.get("complete") is False:
            self.warning(
                "queue-lifecycle-incomplete",
                str(coverage.get("reason") or coverage.get("status") or "collection incomplete"),
                path,
            )

        def nonnegative_int(value: object) -> bool:
            return isinstance(value, int) and not isinstance(value, bool) and value >= 0

        timestamp_fields = coverage.get("timestamp_fields")
        retained_events: dict[str, int] = {}
        retained_durations: dict[str, int] = {}
        legacy_timestamp_keys = {
            "jobs",
            "with_runnable_at",
            "with_started_at",
            "with_finished_at",
            "events_in_retention",
        }
        legacy_timestamp_contract = (
            payload.get("schema_version") == 1
            and isinstance(timestamp_fields, dict)
            and set(timestamp_fields) == legacy_timestamp_keys
        )
        if legacy_timestamp_contract:
            self.warning(
                "queue-lifecycle-legacy-timestamp-coverage",
                (
                    "schema v1 timestamp coverage describes only the current API query; "
                    "retained-ledger duration coverage will be added by the next collection"
                ),
                path,
            )
            if require_current_scope:
                self.error(
                    "queue-lifecycle-current-scope-required",
                    "producer validation requires explicit retained-ledger timestamp coverage",
                    path,
                )
        if not isinstance(timestamp_fields, dict):
            self.error(
                "queue-lifecycle-timestamp-coverage",
                "coverage.timestamp_fields must describe the retained job ledger",
                path,
            )
        else:
            expected_retained_keys = {
                "scope",
                "jobs",
                "with_runnable_at",
                "with_started_at",
                "with_finished_at",
                "events_in_retention",
                "duration_samples_in_retention",
            }
            if (
                set(timestamp_fields) != expected_retained_keys
                and not legacy_timestamp_contract
            ):
                self.error(
                    "queue-lifecycle-timestamp-coverage",
                    "retained timestamp coverage has an invalid schema",
                    path,
                )
            if (
                not legacy_timestamp_contract
                and timestamp_fields.get("scope") != "retained_job_ledger"
            ):
                self.error(
                    "queue-lifecycle-timestamp-scope",
                    "coverage.timestamp_fields.scope must be retained_job_ledger",
                    path,
                )
            retained_jobs = timestamp_fields.get("jobs")
            observation_count = coverage.get("job_observation_count")
            if (
                not nonnegative_int(retained_jobs)
                or not nonnegative_int(observation_count)
                or (
                    not legacy_timestamp_contract
                    and retained_jobs != observation_count
                )
            ):
                self.error(
                    "queue-lifecycle-timestamp-jobs",
                    (
                        "retained timestamp coverage jobs must equal "
                        "coverage.job_observation_count"
                    ),
                    path,
                )
            for field in ("with_runnable_at", "with_started_at", "with_finished_at"):
                value = timestamp_fields.get(field)
                if not nonnegative_int(value) or (
                    nonnegative_int(retained_jobs) and value > retained_jobs
                ):
                    self.error(
                        "queue-lifecycle-timestamp-presence",
                        f"coverage.timestamp_fields.{field} is outside the retained scope",
                        path,
                    )

            events = timestamp_fields.get("events_in_retention")
            if not isinstance(events, dict) or set(events) != {
                "incoming",
                "served",
                "completed",
            } or any(not nonnegative_int(value) for value in events.values()):
                self.error(
                    "queue-lifecycle-timestamp-events",
                    "retained timestamp event counts are malformed",
                    path,
                )
            else:
                retained_events = events
                if coverage.get("event_count") != sum(events.values()):
                    self.error(
                        "queue-lifecycle-timestamp-event-total",
                        "retained timestamp event counts do not equal coverage.event_count",
                        path,
                    )
                if nonnegative_int(retained_jobs) and any(
                    value > retained_jobs for value in events.values()
                ):
                    self.error(
                        "queue-lifecycle-timestamp-event-scope",
                        "retained timestamp event counts exceed retained jobs",
                        path,
                    )

            if not legacy_timestamp_contract:
                durations = timestamp_fields.get("duration_samples_in_retention")
                if not isinstance(durations, dict) or set(durations) != {
                    "queue_wait",
                    "runtime",
                } or any(not nonnegative_int(value) for value in durations.values()):
                    self.error(
                        "queue-lifecycle-duration-samples",
                        "retained duration sample counts are malformed",
                        path,
                    )
                else:
                    retained_durations = durations
                    if retained_events and (
                        durations["queue_wait"] > retained_events["served"]
                        or durations["runtime"] > retained_events["completed"]
                    ):
                        self.error(
                            "queue-lifecycle-duration-scope",
                            "retained duration samples exceed their lifecycle event cohorts",
                            path,
                        )

        count_fields = (
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
        )
        distribution_fields = ("queue_wait_seconds", "runtime_seconds")

        def audit_metric_block(label: str, block: Any) -> dict[str, int] | None:
            if not isinstance(block, dict):
                self.error("queue-lifecycle-metric-block", f"{label} is not an object", path)
                return None
            counts: dict[str, int] = {}
            for field in count_fields:
                value = block.get(field)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    self.error(
                        "queue-lifecycle-count",
                        f"{label}.{field} must be a non-negative integer, got {value!r}",
                        path,
                    )
                    return None
                counts[field] = value
            outcomes = sum(
                counts[field]
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
            if outcomes != counts["completed"]:
                self.error(
                    "queue-lifecycle-outcomes",
                    f"{label} completion outcomes sum to {outcomes}, completed={counts['completed']}",
                    path,
                )
            if counts["retried_jobs_completed"] > counts["completed"]:
                self.error(
                    "queue-lifecycle-retried-jobs",
                    f"{label}.retried_jobs_completed exceeds completed jobs",
                    path,
                )
            for field in distribution_fields:
                distribution = block.get(field)
                if not isinstance(distribution, dict):
                    self.error(
                        "queue-lifecycle-distribution",
                        f"{label}.{field} is not an object",
                        path,
                    )
                    continue
                sample_count = distribution.get("count")
                if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count < 0:
                    self.error(
                        "queue-lifecycle-distribution-count",
                        f"{label}.{field}.count must be a non-negative integer",
                        path,
                    )
                for statistic in ("min", "p50", "p95", "max", "avg"):
                    value = distribution.get(statistic)
                    if value is not None and (
                        isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
                    ):
                        self.error(
                            "queue-lifecycle-distribution-value",
                            f"{label}.{field}.{statistic} must be null or non-negative",
                            path,
                        )
            return counts

        totals = audit_metric_block("totals", payload.get("totals"))
        queues = payload.get("queues")
        if not isinstance(queues, dict) or set(queues) != set(expected_queues):
            self.error(
                "queue-lifecycle-queue-map",
                "queue lifecycle queue map must contain only the ordered canonical queue cohort",
                path,
            )
            queues = queues if isinstance(queues, dict) else {}
        queue_counts = {
            queue: audit_metric_block(f"queues.{queue}", queues.get(queue))
            for queue in expected_queues
        }
        if totals is not None and all(value is not None for value in queue_counts.values()):
            for field in count_fields:
                queue_sum = sum((value or {})[field] for value in queue_counts.values())
                if queue_sum != totals[field]:
                    self.error(
                        "queue-lifecycle-total",
                        f"totals.{field}={totals[field]} but queue rows sum to {queue_sum}",
                        path,
                    )

        hourly = payload.get("hourly")
        if not isinstance(hourly, list) or not hourly:
            self.error("queue-lifecycle-hourly", "queue lifecycle hourly rows are missing", path)
        else:
            for index, bucket in enumerate(hourly):
                if not isinstance(bucket, dict):
                    self.error(
                        "queue-lifecycle-hourly-row",
                        f"hourly[{index}] is not an object",
                        path,
                    )
                    continue
                start = _parse_timestamp(bucket.get("start"))
                end = _parse_timestamp(bucket.get("end_exclusive"))
                if not start or not end or start >= end:
                    self.error(
                        "queue-lifecycle-hourly-window",
                        f"hourly[{index}] has invalid half-open timestamps",
                        path,
                    )
                audit_metric_block(f"hourly[{index}].totals", bucket.get("totals"))

        retention = _mapping(payload.get("retention"))
        if retention.get("days") != 7:
            self.error(
                "queue-lifecycle-retention",
                f"queue lifecycle retention days must be 7, got {retention.get('days')!r}",
                path,
            )
        retention_start = _parse_timestamp(retention.get("event_start"))
        retention_end = _parse_timestamp(retention.get("end_exclusive"))
        if not retention_start or not retention_end or retention_start >= retention_end:
            self.error(
                "queue-lifecycle-retention-window",
                "queue lifecycle retention must have valid increasing half-open timestamps",
                path,
            )

        daily_wait_times = payload.get("daily_wait_times")
        if not isinstance(daily_wait_times, dict):
            self.error(
                "queue-lifecycle-daily-waits-shape",
                "daily_wait_times must be an object",
                path,
            )
        else:
                expected_metadata = {
                    "unit": "seconds",
                    "day_timezone": "UTC",
                    "attributed_by": "timestamps.started_at",
                }
                for field, expected in expected_metadata.items():
                    if daily_wait_times.get(field) != expected:
                        self.error(
                            "queue-lifecycle-daily-waits-metadata",
                            f"daily_wait_times.{field} must be {expected!r}",
                            path,
                        )
                day_rows = daily_wait_times.get("days")
                if not isinstance(day_rows, list) or not day_rows:
                    self.error(
                        "queue-lifecycle-daily-waits-days",
                        "daily_wait_times.days must be a non-empty list",
                        path,
                    )
                elif retention_start and retention_end and retention_start < retention_end:
                    cursor = retention_start.replace(hour=0, minute=0, second=0, microsecond=0)
                    expected_dates = []
                    while cursor < retention_end:
                        expected_dates.append(cursor.date().isoformat())
                        cursor += timedelta(days=1)
                    actual_dates = [
                        row.get("date") if isinstance(row, dict) else None for row in day_rows
                    ]
                    if actual_dates != expected_dates:
                        self.error(
                            "queue-lifecycle-daily-waits-dates",
                            "daily_wait_times.days must contain every intersecting UTC date in order",
                            path,
                        )

                    cursor = retention_start.replace(hour=0, minute=0, second=0, microsecond=0)
                    total_wait_samples = 0
                    published_wait_samples = 0
                    compacted_dates: list[str] = []
                    for index, row in enumerate(day_rows):
                        if not isinstance(row, dict):
                            self.error(
                                "queue-lifecycle-daily-waits-row",
                                f"daily_wait_times.days[{index}] is not an object",
                                path,
                            )
                            cursor += timedelta(days=1)
                            continue
                        base_keys = {
                            "date",
                            "start",
                            "end_exclusive",
                            "partial",
                            "sample_count",
                            "served_job_wait_seconds",
                        }
                        compacted = row.get("vector_complete") is False
                        expected_keys = (
                            base_keys
                            | {
                                "vector_complete",
                                "published_sample_count",
                                "omitted_sample_count",
                                "distribution",
                            }
                            if compacted
                            else base_keys
                        )
                        if set(row) != expected_keys:
                            self.error(
                                "queue-lifecycle-daily-waits-row",
                                f"daily_wait_times.days[{index}] has an invalid schema",
                                path,
                            )
                        calendar_end = cursor + timedelta(days=1)
                        expected_start = max(cursor, retention_start)
                        expected_end = min(calendar_end, retention_end)
                        row_start = _parse_timestamp(row.get("start"))
                        row_end = _parse_timestamp(row.get("end_exclusive"))
                        if row_start != expected_start or row_end != expected_end:
                            self.error(
                                "queue-lifecycle-daily-waits-window",
                                f"daily_wait_times.days[{index}] has incorrect observed bounds",
                                path,
                            )
                        expected_partial = (
                            expected_start != cursor or expected_end != calendar_end
                        )
                        if row.get("partial") is not expected_partial:
                            self.error(
                                "queue-lifecycle-daily-waits-partial",
                                f"daily_wait_times.days[{index}].partial is incorrect",
                                path,
                            )
                        waits = row.get("served_job_wait_seconds")
                        valid_waits = isinstance(waits, list) and all(
                            isinstance(value, (int, float))
                            and not isinstance(value, bool)
                            and math.isfinite(value)
                            and value >= 0
                            for value in (waits or [])
                        )
                        if not valid_waits:
                            self.error(
                                "queue-lifecycle-daily-waits-vector",
                                f"daily_wait_times.days[{index}] has invalid wait values",
                                path,
                            )
                        elif waits != sorted(waits):
                            self.error(
                                "queue-lifecycle-daily-waits-order",
                                f"daily_wait_times.days[{index}] wait vector is not sorted",
                                path,
                            )
                        sample_count = row.get("sample_count")
                        if not nonnegative_int(sample_count) or not isinstance(waits, list):
                            self.error(
                                "queue-lifecycle-daily-waits-count",
                                f"daily_wait_times.days[{index}].sample_count is invalid",
                                path,
                            )
                        elif compacted:
                            published_count = row.get("published_sample_count")
                            omitted_count = row.get("omitted_sample_count")
                            distribution = row.get("distribution")
                            distribution_values = [
                                _mapping(distribution).get(field)
                                for field in ("min", "p50", "p95", "avg", "max")
                            ]
                            distribution_valid = bool(
                                isinstance(distribution, dict)
                                and set(distribution)
                                == {"count", "min", "p50", "p95", "max", "avg"}
                                and distribution.get("count") == sample_count
                                and sample_count > 0
                                and all(
                                    isinstance(value, (int, float))
                                    and not isinstance(value, bool)
                                    and math.isfinite(value)
                                    and value >= 0
                                    for value in distribution_values
                                )
                                and distribution["min"]
                                <= distribution["p50"]
                                <= distribution["p95"]
                                <= distribution["max"]
                                and distribution["min"]
                                <= distribution["avg"]
                                <= distribution["max"]
                            )
                            if (
                                not nonnegative_int(published_count)
                                or published_count != len(waits)
                                or not nonnegative_int(omitted_count)
                                or published_count + omitted_count != sample_count
                                or omitted_count == 0
                                or not valid_waits
                                or not distribution_valid
                            ):
                                self.error(
                                    "queue-lifecycle-daily-waits-compaction",
                                    f"daily_wait_times.days[{index}] has invalid bounded-vector metadata",
                                    path,
                                )
                            total_wait_samples += sample_count
                            published_wait_samples += len(waits)
                            compacted_dates.append(str(row.get("date") or ""))
                        elif sample_count != len(waits):
                            self.error(
                                "queue-lifecycle-daily-waits-count",
                                f"daily_wait_times.days[{index}].sample_count does not match its vector",
                                path,
                            )
                        elif valid_waits:
                            total_wait_samples += sample_count
                            published_wait_samples += sample_count
                        cursor = calendar_end

                    vector_coverage = daily_wait_times.get("vector_coverage")
                    if compacted_dates:
                        expected_vector_coverage = {
                            "complete": False,
                            "observed_sample_count": total_wait_samples,
                            "published_sample_count": published_wait_samples,
                            "compacted_dates": compacted_dates,
                            "method": "oldest_whole_day_vectors_replaced_by_exact_distribution_summary",
                        }
                        if vector_coverage != expected_vector_coverage:
                            self.error(
                                "queue-lifecycle-daily-waits-vector-coverage",
                                "daily_wait_times.vector_coverage does not reconcile compacted days",
                                path,
                            )
                    elif vector_coverage is not None:
                        self.error(
                            "queue-lifecycle-daily-waits-vector-coverage",
                            "daily_wait_times.vector_coverage is present without compacted days",
                            path,
                        )

                    served_events = _mapping(
                        _mapping(coverage.get("timestamp_fields")).get("events_in_retention")
                    ).get("served")
                    if (
                        isinstance(served_events, int)
                        and not isinstance(served_events, bool)
                        and total_wait_samples > served_events
                    ):
                        self.error(
                            "queue-lifecycle-daily-waits-total",
                            "daily wait samples exceed observed served events",
                            path,
                        )
                    retained_wait_samples = retained_durations.get("queue_wait")
                    if (
                        nonnegative_int(retained_wait_samples)
                        and total_wait_samples != retained_wait_samples
                    ):
                        self.error(
                            "queue-lifecycle-daily-waits-coverage-reconciliation",
                            (
                                f"daily wait vectors contain {total_wait_samples} samples, "
                                "but retained-ledger timestamp coverage reports "
                                f"{retained_wait_samples}"
                            ),
                            path,
                        )
                    hourly_wait_samples = (
                        sum(
                            _safe_int(
                                _mapping(
                                    _mapping(_mapping(bucket).get("totals")).get(
                                        "queue_wait_seconds"
                                    )
                                ).get("count")
                            )
                            for bucket in hourly
                            if isinstance(bucket, dict)
                        )
                        if isinstance(hourly, list)
                        else 0
                    )
                    if total_wait_samples != hourly_wait_samples:
                        self.error(
                            "queue-lifecycle-daily-waits-hourly-reconciliation",
                            (
                                f"daily wait vectors contain {total_wait_samples} samples, "
                                f"but UTC hourly buckets contain {hourly_wait_samples}"
                            ),
                            path,
                        )
        provenance = payload.get("provenance")
        if not isinstance(provenance, dict):
            self.error("queue-lifecycle-provenance", "provenance must be an object", path)
        else:
            collection = provenance.get("collection")
            if not isinstance(collection, dict):
                self.error(
                    "queue-lifecycle-query-coverage",
                    "provenance.collection must describe the current API query",
                    path,
                )
            else:
                query_fields = collection.get("timestamp_coverage")
                expected_query_keys = {
                    "scope",
                    "jobs",
                    "with_runnable_at",
                    "with_started_at",
                    "with_finished_at",
                    "events_in_retention",
                    "duration_samples_in_retention",
                }
                if legacy_timestamp_contract:
                    legacy_jobs = (
                        query_fields.get("jobs")
                        if isinstance(query_fields, dict)
                        else None
                    )
                    if (
                        not isinstance(query_fields, dict)
                        or set(query_fields) != legacy_timestamp_keys
                        or query_fields != timestamp_fields
                        or not nonnegative_int(legacy_jobs)
                        or not nonnegative_int(collection.get("unique_jobs"))
                        or legacy_jobs != collection.get("unique_jobs")
                    ):
                        self.error(
                            "queue-lifecycle-query-coverage",
                            (
                                "legacy current-query timestamp coverage must reconcile "
                                "with coverage.timestamp_fields and collection.unique_jobs"
                            ),
                            path,
                        )
                elif (
                    not isinstance(query_fields, dict)
                    or set(query_fields) != expected_query_keys
                ):
                    self.error(
                        "queue-lifecycle-query-coverage",
                        "current-query timestamp coverage has an invalid schema",
                        path,
                    )
                else:
                    query_jobs = query_fields.get("jobs")
                    unique_jobs = collection.get("unique_jobs")
                    if (
                        query_fields.get("scope")
                        != "current_api_query_before_ledger_merge"
                        or not nonnegative_int(query_jobs)
                        or not nonnegative_int(unique_jobs)
                        or query_jobs != unique_jobs
                    ):
                        self.error(
                            "queue-lifecycle-query-scope",
                            (
                                "current-query timestamp coverage must use the query scope "
                                "and reconcile with collection.unique_jobs"
                            ),
                            path,
                        )
                    if nonnegative_int(query_jobs):
                        for field in (
                            "with_runnable_at",
                            "with_started_at",
                            "with_finished_at",
                        ):
                            value = query_fields.get(field)
                            if not nonnegative_int(value) or value > query_jobs:
                                self.error(
                                    "queue-lifecycle-query-presence",
                                    f"current-query {field} is outside the query scope",
                                    path,
                                )
                    query_events = query_fields.get("events_in_retention")
                    query_durations = query_fields.get("duration_samples_in_retention")
                    if (
                        not isinstance(query_events, dict)
                        or set(query_events) != {"incoming", "served", "completed"}
                        or any(not nonnegative_int(value) for value in query_events.values())
                        or (
                            nonnegative_int(query_jobs)
                            and any(value > query_jobs for value in query_events.values())
                        )
                    ):
                        self.error(
                            "queue-lifecycle-query-events",
                            "current-query event coverage is malformed or outside its scope",
                            path,
                        )
                    if (
                        not isinstance(query_durations, dict)
                        or set(query_durations) != {"queue_wait", "runtime"}
                        or any(not nonnegative_int(value) for value in query_durations.values())
                    ):
                        self.error(
                            "queue-lifecycle-query-durations",
                            "current-query duration coverage is malformed",
                            path,
                        )
                    elif isinstance(query_events, dict) and all(
                        nonnegative_int(query_events.get(field))
                        for field in ("served", "completed")
                    ) and (
                        query_durations["queue_wait"] > query_events["served"]
                        or query_durations["runtime"] > query_events["completed"]
                    ):
                        self.error(
                            "queue-lifecycle-query-duration-scope",
                            "current-query duration samples exceed their event cohorts",
                            path,
                        )

        self.report.metrics["queue_lifecycle"] = {
            "generated_at": payload.get("generated_at"),
            "coverage_complete": coverage.get("complete"),
            "window": window,
            "totals": payload.get("totals"),
        }

    def audit_dns_failures(self, source_path: Path | None = None) -> None:
        """Validate the bounded, privacy-minimized DNS observability dataset."""
        path_obj = source_path or (self.root / DNS_FAILURES_DATA_PATH)
        path = self.rel(path_obj)
        if not path_obj.is_file() or path_obj.is_symlink():
            self.error(
                "dns-health-missing",
                "dns_failures.json must be a regular file",
                path,
            )
            return
        try:
            raw = path_obj.read_text()
            payload = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self.error("dns-health-json", f"DNS health data is unreadable: {exc}", path)
            return

        size = len(raw.encode("utf-8"))
        if size > DNS_FAILURES_MAX_BYTES:
            self.error(
                "dns-health-payload-budget",
                f"dns_failures.json is {size} bytes; limit is {DNS_FAILURES_MAX_BYTES}",
                path,
            )
        # Fixed classifier enums such as ``getaddrinfo_eai_again`` are safe
        # public metadata even though they contain words found in raw error
        # text. Scan the complete payload only for credentials and URLs; raw
        # log prose is rejected below at every free-form display field.
        if DNS_SECRET_OR_URL_RE.search(raw):
            self.error(
                "dns-health-sensitive-content",
                "DNS health data contains a URL, secret marker, or raw-log text",
                path,
            )
        if not isinstance(payload, dict):
            self.error("dns-health-shape", "DNS health data must be an object", path)
            return

        def exact_keys(value: Any, expected: set[str], label: str) -> dict:
            if not isinstance(value, dict):
                self.error(
                    "dns-health-shape",
                    f"{label} must be an object",
                    path,
                )
                return {}
            if set(value) != expected:
                self.error(
                    "dns-health-schema",
                    f"{label} must contain exactly {sorted(expected)}, got {sorted(value)}",
                    path,
                )
            return value

        def utc_timestamp(value: Any, label: str) -> datetime | None:
            if not isinstance(value, str) or not value.endswith("Z"):
                self.error(
                    "dns-health-timestamp",
                    f"{label} must be a UTC timestamp ending in Z",
                    path,
                )
                return None
            parsed = _parse_timestamp(value)
            if parsed is None:
                self.error(
                    "dns-health-timestamp",
                    f"{label} is not a valid timestamp: {value!r}",
                    path,
                )
            elif DNS_UTC_SECOND_RE.fullmatch(value) is None:
                self.error(
                    "dns-health-timestamp",
                    f"{label} must use canonical whole-second UTC form",
                    path,
                )
            return parsed

        def safe_coordinate(value: Any, label: str) -> bool:
            valid = (
                isinstance(value, str)
                and DNS_SAFE_COORD_RE.fullmatch(value) is not None
                and DNS_SECRET_OR_URL_RE.search(value) is None
                and DNS_RAW_LOG_RE.search(value) is None
            )
            if not valid:
                self.error(
                    "dns-health-coordinate",
                    f"{label} is not a bounded safe display coordinate",
                    path,
                )
            return valid

        def safe_hardware(value: Any, label: str) -> bool:
            valid = isinstance(value, str) and re.fullmatch(r"MI[0-9]{3,4}", value) is not None
            if not valid:
                self.error(
                    "dns-health-hardware",
                    f"{label} must be a canonical MI hardware family",
                    path,
                )
            return valid

        def queue_matches_hardware(queue: Any, hardware: Any, label: str) -> bool:
            match = re.match(r"^amd_mi([0-9]{3,4})(?:_|$)", str(queue or ""), re.IGNORECASE)
            valid = bool(match and hardware == f"MI{match.group(1)}")
            if not valid:
                self.error(
                    "dns-health-queue-hardware",
                    f"{label} queue and hardware family disagree",
                    path,
                )
            return valid

        top_keys = {
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
        has_outcome_contract = "outcome_contract" in payload
        if has_outcome_contract:
            top_keys.add("outcome_contract")
        exact_keys(payload, top_keys, "dns_failures.json")
        if payload.get("schema_version") != 1:
            self.error(
                "dns-health-schema-version",
                f"DNS health schema_version must be 1, got {payload.get('schema_version')!r}",
                path,
            )
        if has_outcome_contract:
            if payload.get("outcome_contract") != DNS_OUTCOME_CONTRACT:
                self.error(
                    "dns-health-outcome-contract",
                    f"DNS health outcome_contract must be {DNS_OUTCOME_CONTRACT!r}",
                    path,
                )
        else:
            self.warning(
                "dns-health-outcome-contract-legacy",
                "DNS health payload predates exact passed/soft-failed/hard-failed outcome rollups",
                path,
            )

        expected_options = [
            {"id": option_id, "label": label, "hours": hours}
            for option_id, label, hours in DNS_WINDOW_OPTIONS
        ]
        if payload.get("window_options") != expected_options:
            self.error(
                "dns-health-window-options",
                "DNS health window_options must be the canonical ordered seven presets",
                path,
            )
        if payload.get("default_window") != "24h":
            self.error(
                "dns-health-default-window",
                "DNS health default_window must be '24h'",
                path,
            )
        if payload.get("count_basis") != (
            "distinct_buildkite_job_attempts_with_strong_dns_evidence"
        ):
            self.error(
                "dns-health-count-basis",
                "DNS health count_basis changed from the distinct-job-attempt contract",
                path,
            )

        scope = exact_keys(
            payload.get("scope"),
            {
                "organization",
                "pipelines",
                "branches",
                "job_types",
                "states",
                "queue_scope",
                "retried_jobs",
            },
            "scope",
        )
        expected_scope = {
            "organization": "vllm",
            "pipelines": list(DNS_PIPELINES),
            "branches": "all",
            "job_types": ["script"],
            "states": ["passed", "soft", "hard"],
            "queue_scope": "active_amd_gpu",
            "retried_jobs": "included",
        }
        if scope != expected_scope:
            self.error(
                "dns-health-scope",
                "DNS health scope must remain all branches, terminal script attempts, and active AMD GPU queues",
                path,
            )

        classifier = exact_keys(
            payload.get("classifier"),
            {"id", "episode_gap_seconds", "max_log_bytes", "target_categories"},
            "classifier",
        )
        if classifier != {
            "id": "dns-v1",
            "episode_gap_seconds": 5,
            "max_log_bytes": 16 * 1024 * 1024,
            "target_categories": list(DNS_TARGET_CATEGORIES),
        }:
            self.error(
                "dns-health-classifier",
                "DNS health classifier metadata does not match the dns-v1 contract",
                path,
            )

        generated_at = utc_timestamp(payload.get("generated_at"), "generated_at")
        retention = exact_keys(
            payload.get("retention"),
            {"start", "end_exclusive", "hours"},
            "retention",
        )
        retention_start = utc_timestamp(retention.get("start"), "retention.start")
        retention_end = utc_timestamp(
            retention.get("end_exclusive"), "retention.end_exclusive"
        )
        if retention.get("hours") != 720:
            self.error(
                "dns-health-retention",
                f"DNS health retention.hours must be 720, got {retention.get('hours')!r}",
                path,
            )
        if (
            retention_start is not None
            and retention_end is not None
            and retention_end - retention_start != timedelta(hours=720)
        ):
            self.error(
                "dns-health-retention",
                "DNS health retention must be an exact half-open 720-hour interval",
                path,
            )
        if generated_at is not None and generated_at > datetime.now(timezone.utc) + timedelta(minutes=10):
            self.error(
                "dns-health-future",
                "DNS health generated_at is more than ten minutes in the future",
                path,
            )
        if generated_at is not None and retention_end is not None and generated_at != retention_end:
            self.error(
                "dns-health-retention",
                "generated_at must equal retention.end_exclusive",
                path,
            )

        coverage_count_fields = (
            "eligible_jobs",
            "scanned_jobs",
            "positive_jobs",
            "negative_jobs",
            "pending_jobs",
            "unavailable_jobs",
            "oversize_jobs",
        )

        def validate_coverage(value: Any, label: str, *, top_level: bool) -> dict:
            expected = {
                "status",
                "complete",
                "discovery_complete",
                *coverage_count_fields,
            }
            if top_level:
                expected |= {"discovery_start", "discovery_end_exclusive"}
            block = exact_keys(value, expected, label)
            status = block.get("status")
            complete = block.get("complete")
            discovery_complete = block.get("discovery_complete")
            if status not in DNS_COVERAGE_STATUSES:
                self.error(
                    "dns-health-coverage-status",
                    f"{label}.status has an unknown value: {status!r}",
                    path,
                )
            if not isinstance(complete, bool) or not isinstance(discovery_complete, bool):
                self.error(
                    "dns-health-coverage-flags",
                    f"{label} completeness fields must be booleans",
                    path,
                )
            if isinstance(complete, bool) and complete != (status == "complete"):
                self.error(
                    "dns-health-coverage-status",
                    f"{label}.complete disagrees with status={status!r}",
                    path,
                )
            counts: dict[str, int] = {}
            for count_field in coverage_count_fields:
                raw_count = block.get(count_field)
                if not _is_nonnegative_int(raw_count):
                    self.error(
                        "dns-health-coverage-count",
                        f"{label}.{count_field} must be a non-negative integer",
                        path,
                    )
                else:
                    counts[count_field] = raw_count
            if len(counts) == len(coverage_count_fields):
                classified = sum(
                    counts[count_field]
                    for count_field in (
                        "positive_jobs",
                        "negative_jobs",
                        "pending_jobs",
                        "unavailable_jobs",
                        "oversize_jobs",
                    )
                )
                if classified != counts["eligible_jobs"]:
                    self.error(
                        "dns-health-coverage-reconciliation",
                        f"{label} job-state counts sum to {classified}, eligible_jobs={counts['eligible_jobs']}",
                        path,
                    )
                if counts["scanned_jobs"] != counts["positive_jobs"] + counts["negative_jobs"]:
                    self.error(
                        "dns-health-coverage-reconciliation",
                        f"{label}.scanned_jobs must equal positive+negative jobs",
                        path,
                    )
                gaps = sum(
                    counts[count_field]
                    for count_field in ("pending_jobs", "unavailable_jobs", "oversize_jobs")
                )
                expected_complete = discovery_complete is True and gaps == 0
                if (
                    status in {"partial", "complete"}
                    and isinstance(complete, bool)
                    and complete != expected_complete
                ):
                    self.error(
                        "dns-health-false-complete",
                        f"{label} completeness disagrees with discovery and gap counts",
                        path,
                    )
                if status == "not_collected" and (
                    complete is not False
                    or discovery_complete is not False
                    or any(counts.values())
                ):
                    self.error(
                        "dns-health-seed",
                        f"{label} not_collected state must be incomplete with zero observations",
                        path,
                    )
            if top_level:
                discovery_start = utc_timestamp(
                    block.get("discovery_start"), f"{label}.discovery_start"
                )
                discovery_end = utc_timestamp(
                    block.get("discovery_end_exclusive"),
                    f"{label}.discovery_end_exclusive",
                )
                if (
                    discovery_start is not None
                    and discovery_end is not None
                    and discovery_start >= discovery_end
                ):
                    self.error(
                        "dns-health-discovery-window",
                        f"{label} discovery interval must be non-empty and half-open",
                        path,
                    )
                if (
                    discovery_end is not None
                    and retention_end is not None
                    and discovery_end != retention_end
                ):
                    self.error(
                        "dns-health-discovery-window",
                        f"{label}.discovery_end_exclusive must equal generated_at",
                        path,
                    )
                if (
                    discovery_start is not None
                    and retention_start is not None
                    and discovery_start < retention_start
                ):
                    self.error(
                        "dns-health-discovery-window",
                        f"{label}.discovery_start cannot precede retained data",
                        path,
                    )
                if (
                    complete is True
                    and retention_start is not None
                    and retention_end is not None
                    and discovery_start is not None
                    and discovery_end is not None
                    and (discovery_start > retention_start or discovery_end < retention_end)
                ):
                    self.error(
                        "dns-health-false-complete",
                        "complete DNS coverage does not span the retained interval",
                        path,
                    )
            return block

        coverage = validate_coverage(payload.get("coverage"), "coverage", top_level=True)
        coverage_status = coverage.get("status")
        coverage_discovery_start = (
            _parse_timestamp(coverage.get("discovery_start"))
            if isinstance(coverage.get("discovery_start"), str)
            else None
        )
        coverage_discovery_end = (
            _parse_timestamp(coverage.get("discovery_end_exclusive"))
            if isinstance(coverage.get("discovery_end_exclusive"), str)
            else None
        )
        if (
            coverage_status in {"partial", "complete"}
            and retention_start is not None
            and retention_end is not None
            and coverage_discovery_start is not None
            and coverage_discovery_end is not None
        ):
            expected_discovery_complete = (
                coverage_discovery_start <= retention_start
                and coverage_discovery_end >= retention_end
            )
            if coverage.get("discovery_complete") != expected_discovery_complete:
                self.error(
                    "dns-health-discovery-window",
                    "coverage.discovery_complete disagrees with the discovery interval",
                    path,
                )

        windows = exact_keys(
            payload.get("windows"),
            {option_id for option_id, _, _ in DNS_WINDOW_OPTIONS},
            "windows",
        )
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
        if has_outcome_contract:
            totals_keys.update(DNS_OUTCOME_COUNT_FIELDS)
            row_keys.update(DNS_OUTCOME_COUNT_FIELDS)
        window_blocks: dict[str, dict[str, Any]] = {}
        previous_totals: dict[str, int] | None = None
        for option_id, _, hours in DNS_WINDOW_OPTIONS:
            block = exact_keys(
                windows.get(option_id),
                {"start", "end_exclusive", "coverage", "totals", "rows"},
                f"windows.{option_id}",
            )
            start = utc_timestamp(block.get("start"), f"windows.{option_id}.start")
            end = utc_timestamp(
                block.get("end_exclusive"), f"windows.{option_id}.end_exclusive"
            )
            if start is not None and end is not None and end - start != timedelta(hours=hours):
                self.error(
                    "dns-health-window-boundary",
                    f"windows.{option_id} must span exactly {hours} hours",
                    path,
                )
            if retention_end is not None and end is not None and end != retention_end:
                self.error(
                    "dns-health-window-boundary",
                    f"windows.{option_id}.end_exclusive must equal retention.end_exclusive",
                    path,
                )
            window_coverage = validate_coverage(
                block.get("coverage"), f"windows.{option_id}.coverage", top_level=False
            )
            if coverage_status == "not_collected" and window_coverage.get("status") != "not_collected":
                self.error(
                    "dns-health-seed",
                    f"windows.{option_id} must remain not_collected with the structural seed",
                    path,
                )
            elif (
                coverage_status in {"partial", "complete"}
                and window_coverage.get("status") == "not_collected"
            ):
                self.error(
                    "dns-health-coverage-status",
                    f"windows.{option_id} cannot be not_collected after collection",
                    path,
                )
            if (
                coverage_status in {"partial", "complete"}
                and start is not None
                and end is not None
                and coverage_discovery_start is not None
                and coverage_discovery_end is not None
            ):
                expected_discovery_complete = (
                    coverage_discovery_start <= start
                    and coverage_discovery_end >= end
                )
                if (
                    window_coverage.get("discovery_complete")
                    != expected_discovery_complete
                ):
                    self.error(
                        "dns-health-discovery-window",
                        f"windows.{option_id}.coverage.discovery_complete disagrees with discovery bounds",
                        path,
                    )

            totals = exact_keys(block.get("totals"), totals_keys, f"windows.{option_id}.totals")
            numeric_totals: dict[str, int] = {}
            for total_field in totals_keys:
                value = totals.get(total_field)
                if not _is_nonnegative_int(value):
                    self.error(
                        "dns-health-total",
                        f"windows.{option_id}.totals.{total_field} must be a non-negative integer",
                        path,
                    )
                else:
                    numeric_totals[total_field] = value
            if len(numeric_totals) == len(totals_keys):
                if numeric_totals["huggingface_affected_jobs"] > numeric_totals["affected_jobs"]:
                    self.error(
                        "dns-health-total",
                        f"windows.{option_id} Hugging Face jobs exceed all affected jobs",
                        path,
                    )
                if numeric_totals["evidence_total"] != numeric_totals["affected_jobs"]:
                    self.error(
                        "dns-health-evidence-reconciliation",
                        f"windows.{option_id} evidence_total must equal affected_jobs",
                        path,
                    )
                if has_outcome_contract:
                    outcome_jobs = sum(
                        numeric_totals[field] for field in DNS_OUTCOME_COUNT_FIELDS
                    )
                    if outcome_jobs != numeric_totals["affected_jobs"]:
                        self.error(
                            "dns-health-outcome-reconciliation",
                            f"windows.{option_id} outcome jobs sum to {outcome_jobs}, affected_jobs={numeric_totals['affected_jobs']}",
                            path,
                        )
                if _is_nonnegative_int(window_coverage.get("positive_jobs")) and (
                    window_coverage["positive_jobs"] != numeric_totals["affected_jobs"]
                ):
                    self.error(
                        "dns-health-window-reconciliation",
                        f"windows.{option_id} positive_jobs disagrees with affected_jobs",
                        path,
                    )
                if previous_totals is not None:
                    monotonic_fields = (
                        "affected_jobs",
                        "episodes",
                        "huggingface_affected_jobs",
                        "evidence_total",
                    )
                    if has_outcome_contract:
                        monotonic_fields += DNS_OUTCOME_COUNT_FIELDS
                    for total_field in monotonic_fields:
                        if numeric_totals[total_field] < previous_totals[total_field]:
                            self.error(
                                "dns-health-window-monotonicity",
                                f"windows.{option_id}.{total_field} shrinks in a wider window",
                                path,
                            )
                previous_totals = numeric_totals

            rows = block.get("rows")
            if not isinstance(rows, list):
                self.error(
                    "dns-health-rows",
                    f"windows.{option_id}.rows must be a list",
                    path,
                )
                rows = []
            coordinates: list[tuple[str, str]] = []
            row_sums = {
                "affected_jobs": 0,
                "episodes": 0,
                "huggingface_affected_jobs": 0,
                "evidence_total": 0,
            }
            if has_outcome_contract:
                row_sums.update({field: 0 for field in DNS_OUTCOME_COUNT_FIELDS})
            row_lookup: dict[tuple[str, str], dict] = {}
            for index, raw_row in enumerate(rows):
                row = exact_keys(raw_row, row_keys, f"windows.{option_id}.rows[{index}]")
                queue = row.get("queue")
                node = row.get("node")
                safe_coordinate(queue, f"windows.{option_id}.rows[{index}].queue")
                safe_coordinate(node, f"windows.{option_id}.rows[{index}].node")
                safe_hardware(row.get("hardware"), f"windows.{option_id}.rows[{index}].hardware")
                queue_matches_hardware(
                    queue,
                    row.get("hardware"),
                    f"windows.{option_id}.rows[{index}]",
                )
                coordinate = (str(queue), str(node))
                coordinates.append(coordinate)
                if coordinate in row_lookup:
                    self.error(
                        "dns-health-row-duplicate",
                        f"windows.{option_id} repeats queue/node {coordinate!r}",
                        path,
                    )
                row_lookup[coordinate] = row
                valid_counts = True
                for count_field in row_sums:
                    value = row.get(count_field)
                    if not _is_nonnegative_int(value):
                        valid_counts = False
                        self.error(
                            "dns-health-row-count",
                            f"windows.{option_id}.rows[{index}].{count_field} must be non-negative",
                            path,
                        )
                    else:
                        row_sums[count_field] += value
                if valid_counts:
                    if row["affected_jobs"] <= 0 or row["episodes"] < row["affected_jobs"]:
                        self.error(
                            "dns-health-row-count",
                            f"windows.{option_id}.rows[{index}] must describe at least one DNS episode/job",
                            path,
                        )
                    if row["huggingface_affected_jobs"] > row["affected_jobs"]:
                        self.error(
                            "dns-health-row-count",
                            f"windows.{option_id}.rows[{index}] has too many Hugging Face jobs",
                            path,
                        )
                    if row["evidence_total"] != row["affected_jobs"]:
                        self.error(
                            "dns-health-evidence-reconciliation",
                            f"windows.{option_id}.rows[{index}] evidence_total must equal affected_jobs",
                            path,
                        )
                    if has_outcome_contract:
                        outcome_jobs = sum(
                            row[field] for field in DNS_OUTCOME_COUNT_FIELDS
                        )
                        if outcome_jobs != row["affected_jobs"]:
                            self.error(
                                "dns-health-outcome-reconciliation",
                                f"windows.{option_id}.rows[{index}] outcome jobs sum to {outcome_jobs}, affected_jobs={row['affected_jobs']}",
                                path,
                            )
            if coordinates != sorted(coordinates) or len(coordinates) != len(set(coordinates)):
                self.error(
                    "dns-health-row-order",
                    f"windows.{option_id} rows must be uniquely sorted by queue and node",
                    path,
                )
            if len(numeric_totals) == len(totals_keys):
                for total_field, row_sum in row_sums.items():
                    if numeric_totals[total_field] != row_sum:
                        self.error(
                            "dns-health-window-reconciliation",
                            f"windows.{option_id}.totals.{total_field}={numeric_totals[total_field]} but rows sum to {row_sum}",
                            path,
                        )
                if numeric_totals["queues"] != len({queue for queue, _ in coordinates}):
                    self.error(
                        "dns-health-window-reconciliation",
                        f"windows.{option_id}.totals.queues disagrees with rows",
                        path,
                    )
                if numeric_totals["nodes"] != len({node for _, node in coordinates}):
                    self.error(
                        "dns-health-window-reconciliation",
                        f"windows.{option_id}.totals.nodes disagrees with rows",
                        path,
                    )
            window_blocks[option_id] = {
                "start": start,
                "end": end,
                "totals": numeric_totals,
                "rows": row_lookup,
                "coverage": window_coverage,
            }

        retained_coverage = window_blocks.get("720h", {}).get("coverage") or {}
        for coverage_field in (
            "status",
            "complete",
            "discovery_complete",
            *coverage_count_fields,
        ):
            if coverage.get(coverage_field) != retained_coverage.get(coverage_field):
                self.error(
                    "dns-health-coverage-reconciliation",
                    f"coverage.{coverage_field} disagrees with windows.720h.coverage.{coverage_field}",
                    path,
                )

        evidence = exact_keys(
            payload.get("evidence"),
            {"evidence_total", "shown", "truncated", "items"},
            "evidence",
        )
        evidence_total = evidence.get("evidence_total")
        shown = evidence.get("shown")
        truncated = evidence.get("truncated")
        items = evidence.get("items")
        for count_field, value in (("evidence_total", evidence_total), ("shown", shown)):
            if not _is_nonnegative_int(value):
                self.error(
                    "dns-health-evidence-count",
                    f"evidence.{count_field} must be a non-negative integer",
                    path,
                )
        if not isinstance(truncated, bool):
            self.error(
                "dns-health-evidence-count",
                "evidence.truncated must be a boolean",
                path,
            )
        if not isinstance(items, list):
            self.error("dns-health-evidence-items", "evidence.items must be a list", path)
            items = []
        if _is_nonnegative_int(shown) and shown != len(items):
            self.error(
                "dns-health-evidence-count",
                f"evidence.shown={shown!r} but items contains {len(items)} rows",
                path,
            )
        if _is_nonnegative_int(shown) and shown > DNS_EVIDENCE_MAX_ITEMS:
            self.error(
                "dns-health-evidence-count",
                f"evidence.shown exceeds the {DNS_EVIDENCE_MAX_ITEMS}-item public cap",
                path,
            )
        if _is_nonnegative_int(evidence_total) and _is_nonnegative_int(shown):
            if shown > evidence_total or truncated != (shown < evidence_total):
                self.error(
                    "dns-health-evidence-count",
                    "evidence shown/total/truncated fields are inconsistent",
                    path,
                )
            retained_total = (
                window_blocks.get("720h", {}).get("totals", {}).get("evidence_total")
            )
            if retained_total is not None and evidence_total != retained_total:
                self.error(
                    "dns-health-evidence-reconciliation",
                    "top-level evidence_total disagrees with the 720h window",
                    path,
                )

        item_keys = {
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
        seen_ids: set[str] = set()
        seen_jobs: set[tuple[str, str]] = set()
        evidence_order: list[tuple[str, str, str, int, str]] = []
        shown_by_window: dict[str, dict[tuple[str, str], dict[str, int]]] = {
            option_id: {} for option_id, _, _ in DNS_WINDOW_OPTIONS
        }
        for index, raw_item in enumerate(items):
            item = exact_keys(raw_item, item_keys, f"evidence.items[{index}]")
            item_id = item.get("id")
            if not isinstance(item_id, str) or re.fullmatch(r"[0-9a-f]{64}", item_id) is None:
                self.error(
                    "dns-health-evidence-id",
                    f"evidence.items[{index}].id must be a bounded lowercase hash",
                    path,
                )
            elif item_id in seen_ids:
                self.error(
                    "dns-health-evidence-duplicate",
                    f"evidence repeats id {item_id!r}",
                    path,
                )
            else:
                seen_ids.add(item_id)
            job_id = item.get("job_id")
            if not isinstance(job_id, str) or DNS_UUID_RE.fullmatch(job_id) is None:
                self.error(
                    "dns-health-job-id",
                    f"evidence.items[{index}].job_id must be a Buildkite UUID",
                    path,
                )
            first_at = utc_timestamp(item.get("first_at"), f"evidence.items[{index}].first_at")
            last_at = utc_timestamp(item.get("last_at"), f"evidence.items[{index}].last_at")
            if first_at is not None and last_at is not None:
                if first_at > last_at:
                    self.error(
                        "dns-health-evidence-time",
                        f"evidence.items[{index}] first_at is after last_at",
                        path,
                    )
                if (
                    retention_start is not None
                    and retention_end is not None
                    and (first_at < retention_start or last_at >= retention_end)
                ):
                    self.error(
                        "dns-health-evidence-time",
                        f"evidence.items[{index}] lies outside retained bounds",
                        path,
                    )
            if item.get("time_basis") not in DNS_TIME_BASES:
                self.error(
                    "dns-health-evidence-enum",
                    f"evidence.items[{index}].time_basis is unknown",
                    path,
                )
            pipeline = item.get("pipeline")
            if pipeline not in DNS_PIPELINES:
                self.error(
                    "dns-health-evidence-enum",
                    f"evidence.items[{index}].pipeline is unknown",
                    path,
                )
            if (
                isinstance(pipeline, str)
                and pipeline in DNS_PIPELINES
                and isinstance(job_id, str)
                and DNS_UUID_RE.fullmatch(job_id) is not None
            ):
                identity = (pipeline, job_id)
                if identity in seen_jobs:
                    self.error(
                        "dns-health-evidence-duplicate",
                        f"evidence repeats Buildkite job identity {identity!r}",
                        path,
                    )
                else:
                    seen_jobs.add(identity)
                expected_id = hashlib.sha256(
                    f"dns-evidence-v1\0{pipeline}\0{job_id}".encode()
                ).hexdigest()
                if item_id != expected_id:
                    self.error(
                        "dns-health-evidence-id",
                        f"evidence.items[{index}].id does not match its versioned job identity",
                        path,
                    )
            for coordinate_field in ("queue", "node"):
                safe_coordinate(
                    item.get(coordinate_field),
                    f"evidence.items[{index}].{coordinate_field}",
                )
            safe_hardware(item.get("hardware"), f"evidence.items[{index}].hardware")
            queue_matches_hardware(
                item.get("queue"),
                item.get("hardware"),
                f"evidence.items[{index}]",
            )
            build_number = item.get("build_number")
            if not _is_nonnegative_int(build_number) or build_number <= 0:
                self.error(
                    "dns-health-build-number",
                    f"evidence.items[{index}].build_number must be positive",
                    path,
                )
            if item.get("state") not in DNS_JOB_STATES:
                self.error(
                    "dns-health-evidence-enum",
                    f"evidence.items[{index}].state is unknown",
                    path,
                )
            if (
                isinstance(item.get("last_at"), str)
                and isinstance(item.get("first_at"), str)
                and isinstance(item.get("pipeline"), str)
                and _is_nonnegative_int(build_number)
                and isinstance(job_id, str)
            ):
                evidence_order.append(
                    (
                        item["last_at"],
                        item["first_at"],
                        item["pipeline"],
                        build_number,
                        job_id,
                    )
                )
            episodes = item.get("episodes")
            match_count = item.get("match_count")
            if (
                not _is_nonnegative_int(episodes)
                or episodes <= 0
                or not _is_nonnegative_int(match_count)
                or match_count < episodes
            ):
                self.error(
                    "dns-health-evidence-count",
                    f"evidence.items[{index}] must have match_count >= episodes > 0",
                    path,
                )

            def validate_enum_list(
                container: dict,
                field: str,
                allowed: set[str] | frozenset[str],
                *,
                label: str,
                require_order: tuple[str, ...] | None = None,
            ) -> list[str]:
                value = container.get(field)
                valid = (
                    isinstance(value, list)
                    and bool(value)
                    and all(isinstance(entry, str) and entry in allowed for entry in value)
                    and len(value) == len(set(value))
                )
                if valid and require_order is not None:
                    valid = value == [entry for entry in require_order if entry in value]
                if not valid:
                    self.error(
                        "dns-health-evidence-enum",
                        f"{label}.{field} is not a unique canonical enum list",
                        path,
                    )
                    return []
                return value

            item_label = f"evidence.items[{index}]"
            signatures = validate_enum_list(
                item,
                "signature_ids",
                DNS_SIGNATURE_IDS,
                label=item_label,
            )
            categories = validate_enum_list(
                item,
                "target_categories",
                frozenset(DNS_TARGET_CATEGORIES),
                label=item_label,
                require_order=DNS_TARGET_CATEGORIES,
            )
            option_order = tuple(option_id for option_id, _, _ in DNS_WINDOW_OPTIONS)
            window_ids = validate_enum_list(
                item,
                "window_ids",
                frozenset(option_order),
                label=item_label,
                require_order=option_order,
            )
            if signatures and signatures != sorted(signatures):
                self.error(
                    "dns-health-evidence-enum",
                    f"evidence.items[{index}].signature_ids must be sorted",
                    path,
                )
            if window_ids and "720h" not in window_ids:
                self.error(
                    "dns-health-evidence-window",
                    f"evidence.items[{index}] is retained but omits the 720h window",
                    path,
                )
            window_metrics = exact_keys(
                item.get("window_metrics"),
                set(window_ids),
                f"evidence.items[{index}].window_metrics",
            )
            if list(window_metrics) != window_ids:
                self.error(
                    "dns-health-evidence-window",
                    f"evidence.items[{index}].window_metrics keys must follow window_ids order",
                    path,
                )
            normalized_window_metrics: dict[str, dict[str, Any]] = {}
            metric_keys = {
                "first_at",
                "last_at",
                "episodes",
                "match_count",
                "signature_ids",
                "target_categories",
            }
            for window_id in window_ids:
                metric_label = f"evidence.items[{index}].window_metrics.{window_id}"
                metric = exact_keys(
                    window_metrics.get(window_id),
                    metric_keys,
                    metric_label,
                )
                metric_first = utc_timestamp(metric.get("first_at"), f"{metric_label}.first_at")
                metric_last = utc_timestamp(metric.get("last_at"), f"{metric_label}.last_at")
                if (
                    metric_first is not None
                    and metric_last is not None
                    and metric_first > metric_last
                ):
                    self.error(
                        "dns-health-evidence-time",
                        f"{metric_label}.first_at is after last_at",
                        path,
                    )
                metric_episodes = metric.get("episodes")
                metric_matches = metric.get("match_count")
                if (
                    not _is_nonnegative_int(metric_episodes)
                    or metric_episodes <= 0
                    or not _is_nonnegative_int(metric_matches)
                    or metric_matches < metric_episodes
                ):
                    self.error(
                        "dns-health-evidence-count",
                        f"{metric_label} must have match_count >= episodes > 0",
                        path,
                    )
                metric_signatures = validate_enum_list(
                    metric,
                    "signature_ids",
                    DNS_SIGNATURE_IDS,
                    label=metric_label,
                )
                metric_categories = validate_enum_list(
                    metric,
                    "target_categories",
                    frozenset(DNS_TARGET_CATEGORIES),
                    label=metric_label,
                    require_order=DNS_TARGET_CATEGORIES,
                )
                if metric_signatures and metric_signatures != sorted(metric_signatures):
                    self.error(
                        "dns-health-evidence-enum",
                        f"{metric_label}.signature_ids must be sorted",
                        path,
                    )
                window = window_blocks.get(window_id) or {}
                window_start = window.get("start")
                window_end = window.get("end")
                if (
                    metric_first is not None
                    and metric_last is not None
                    and window_start is not None
                    and window_end is not None
                    and not (
                        window_start <= metric_first <= metric_last < window_end
                    )
                ):
                    self.error(
                        "dns-health-evidence-window",
                        f"{metric_label} lies outside its selected window",
                        path,
                    )
                normalized_window_metrics[window_id] = {
                    "first_at": metric_first,
                    "last_at": metric_last,
                    "episodes": metric_episodes,
                    "match_count": metric_matches,
                    "signature_ids": metric_signatures,
                    "target_categories": metric_categories,
                }
            retained_metric = normalized_window_metrics.get("720h")
            if retained_metric is not None:
                retained_contract = {
                    "first_at": first_at,
                    "last_at": last_at,
                    "episodes": episodes,
                    "match_count": match_count,
                    "signature_ids": signatures,
                    "target_categories": categories,
                }
                if retained_metric != retained_contract:
                    self.error(
                        "dns-health-evidence-reconciliation",
                        f"evidence.items[{index}] top-level metrics must equal window_metrics.720h",
                        path,
                    )
            if last_at is not None and window_ids:
                expected_window_ids = [
                    window_id
                    for window_id in option_order
                    if (
                        window_blocks.get(window_id, {}).get("start") is not None
                        and window_blocks.get(window_id, {}).get("end") is not None
                        and window_blocks[window_id]["start"]
                        <= last_at
                        < window_blocks[window_id]["end"]
                    )
                ]
                if window_ids != expected_window_ids:
                    self.error(
                        "dns-health-evidence-window",
                        f"evidence.items[{index}].window_ids are not the exact retained-window subset",
                        path,
                    )
            coordinate = (str(item.get("queue")), str(item.get("node")))
            for window_id in window_ids:
                window = window_blocks.get(window_id) or {}
                lookup = window.get("rows") or {}
                if coordinate not in lookup:
                    self.error(
                        "dns-health-evidence-window",
                        f"evidence.items[{index}] has no {window_id} queue/node rollup",
                        path,
                    )
                counts = shown_by_window[window_id]
                cell = counts.setdefault(
                    coordinate,
                    {
                        "affected_jobs": 0,
                        **{field: 0 for field in DNS_OUTCOME_COUNT_FIELDS},
                        "episodes": 0,
                        "huggingface_affected_jobs": 0,
                        "evidence_total": 0,
                    },
                )
                metric = normalized_window_metrics.get(window_id) or {}
                cell["affected_jobs"] += 1
                cell["evidence_total"] += 1
                outcome_field = DNS_OUTCOME_COUNT_FIELD_BY_STATE.get(item.get("state"))
                if outcome_field is not None:
                    cell[outcome_field] += 1
                if _is_nonnegative_int(metric.get("episodes")):
                    cell["episodes"] += metric["episodes"]
                cell["huggingface_affected_jobs"] += int(
                    "huggingface_hub" in (metric.get("target_categories") or [])
                )

        if evidence_order != sorted(evidence_order, reverse=True):
            self.error(
                "dns-health-evidence-order",
                "DNS evidence items must be ordered newest-first with deterministic tie-breaks",
                path,
            )

        for window_id, cells in shown_by_window.items():
            rows = window_blocks.get(window_id, {}).get("rows") or {}
            for coordinate, row in rows.items():
                visible = cells.get(
                    coordinate,
                    {
                        "affected_jobs": 0,
                        **{field: 0 for field in DNS_OUTCOME_COUNT_FIELDS},
                        "episodes": 0,
                        "huggingface_affected_jobs": 0,
                        "evidence_total": 0,
                    },
                )
                visible_count_fields = (
                    "affected_jobs",
                    "episodes",
                    "huggingface_affected_jobs",
                    "evidence_total",
                )
                if has_outcome_contract:
                    visible_count_fields += DNS_OUTCOME_COUNT_FIELDS
                for count_field in visible_count_fields:
                    expected = row.get(count_field)
                    observed = visible[count_field]
                    if _is_nonnegative_int(expected) and observed > expected:
                        self.error(
                            "dns-health-evidence-reconciliation",
                            f"{window_id} visible {count_field} exceeds rollup for {coordinate!r}",
                            path,
                        )
                    if (
                        truncated is False
                        and _is_nonnegative_int(expected)
                        and observed != expected
                    ):
                        self.error(
                            "dns-health-evidence-reconciliation",
                            f"{window_id} {count_field} does not match untruncated evidence for {coordinate!r}",
                            path,
                        )

        if coverage_status == "not_collected":
            seed_windows_valid = all(
                block.get("coverage", {}).get("status") == "not_collected"
                and not block.get("rows")
                and not any((block.get("totals") or {}).values())
                for block in window_blocks.values()
            )
            if (
                not seed_windows_valid
                or evidence_total != 0
                or shown != 0
                or truncated is not False
                or items
            ):
                self.error(
                    "dns-health-seed",
                    "not_collected structural seed must contain no observations or evidence",
                    path,
                )
            else:
                self.degradation(
                    "dns-health-not-collected",
                    "DNS health collection has not completed yet; counts are unavailable, not zero",
                    path,
                )
        elif coverage_status in {"partial", "complete"}:
            if generated_at is not None:
                age_hours = (datetime.now(timezone.utc) - generated_at).total_seconds() / 3600
                if age_hours > DNS_MAX_FRESH_AGE_HOURS:
                    if "dns_health" in self.fallback_surfaces():
                        self.warning(
                            "dns-health-stale-fallback",
                            f"DNS health fallback is {age_hours:.1f}h old",
                            path,
                        )
                    else:
                        self.degradation(
                            "dns-health-stale",
                            f"DNS health source is {age_hours:.1f}h old; maximum is {DNS_MAX_FRESH_AGE_HOURS}h",
                            path,
                        )
            if coverage_status == "partial":
                # The scanner is deliberately bounded and retains unvisited
                # work as pending.  That makes partial coverage an honest
                # property of an otherwise current, valid dataset rather than
                # a publication incident.  The DNS panel exposes the pending
                # counts and renders every aggregate as a lower bound.  Keep
                # stale, not-collected, malformed, and inconsistent payloads
                # on their stricter paths above.
                self.warning(
                    "dns-health-partial",
                    "DNS health coverage is partial; incomplete windows must not be interpreted as zero",
                    path,
                )

        self.report.metrics["dns_health"] = {
            "bytes": size,
            "generated_at": payload.get("generated_at"),
            "outcome_contract": payload.get("outcome_contract"),
            "outcome_breakdown_complete": (
                payload.get("outcome_contract") == DNS_OUTCOME_CONTRACT
            ),
            "coverage_status": coverage_status,
            "coverage_complete": coverage.get("complete"),
            "retention_hours": retention.get("hours"),
            "evidence_total": evidence_total,
            "evidence_shown": shown,
        }

    def audit_frontend_contracts(self) -> None:
        active = (
            "docs/assets/js/utils.js",
            "docs/assets/js/publication-status.js",
            "docs/assets/js/dashboard-nav.js",
            "docs/assets/js/ops-v2.js",
        )
        retired = (
            "docs/assets/js/dashboard.js",
            "docs/assets/js/ci-health.js",
            "docs/assets/js/ci-analytics.js",
            "docs/assets/js/ci-perf-eval.js",
            "docs/assets/js/ci-queue.js",
            "docs/assets/js/ci-hotness.js",
            "docs/assets/js/ci-omni.js",
        )
        metrics: dict[str, bool] = {}
        for relpath in active:
            ok = (self.root / relpath).is_file()
            metrics[f"active:{Path(relpath).name}"] = ok
            if not ok:
                self.error(
                    "missing-active-frontend-asset",
                    f"Active frontend asset {relpath} is missing",
                    relpath,
                )
        for relpath in retired:
            ok = not (self.root / relpath).exists()
            metrics[f"retired:{Path(relpath).name}"] = ok
            if not ok:
                self.error(
                    "retired-frontend-asset",
                    f"Retired frontend asset {relpath} is still published",
                    relpath,
                )
        self.report.metrics["frontend_contracts"] = metrics

    def audit_workflows(self) -> None:
        workflows = sorted((self.root / ".github/workflows").glob("*.yml"))
        gh_pages_workflows: list[str] = []
        cache_busting_build_commands = (
            "python scripts/build_site.py --cache-bust-index",
            # The privileged PR preview runs the same trusted base-branch
            # assembler from an isolated checkout instead of executing the
            # pull request's copy of the script.
            "trusted-base/scripts/build_site.py --cache-bust-index",
        )
        for path in workflows:
            text = path.read_text(errors="ignore")
            if "peaceiris/actions-gh-pages" not in text:
                continue
            gh_pages_workflows.append(path.name)
            if "group: gh-pages-deploy" not in text:
                self.error(
                    "workflow-gh-pages-concurrency",
                    f"{path.name} deploys to gh-pages without the shared concurrency group",
                    self.rel(path),
                )
            if "cancel-in-progress: false" not in text:
                self.error(
                    "workflow-gh-pages-cancel",
                    f"{path.name} deploys to gh-pages without cancel-in-progress: false",
                    self.rel(path),
                )
            if not any(command in text for command in cache_busting_build_commands):
                self.error(
                    "workflow-cache-bust",
                    f"{path.name} deploys Pages without cache-busting index.html",
                    self.rel(path),
                )

        hourly = self.root / ".github/workflows/hourly-master.yml"
        text = hourly.read_text(errors="ignore") if hourly.exists() else ""

        def workflow_step_block(name: str) -> str:
            marker = f"      - name: {name}"
            start = text.find(marker)
            if start < 0:
                return ""
            candidates = [
                index
                for index in (
                    text.find("\n      - name:", start + len(marker)),
                    text.find("\n      - uses:", start + len(marker)),
                )
                if index >= 0
            ]
            end = min(candidates) if candidates else len(text)
            return text[start:end]

        ordered_tokens = [
            "name: Sync CI data from gh-pages",
            "name: Collect AMD gating target list",
            "name: Collect CI data",
            "name: Prepare private analytics cache key",
            "name: Restore private analytics build cache",
            "name: Collect CI analytics",
            "name: Save private analytics build cache",
            "name: Collect test group changes",
            "name: Collect AMD test matrix",
            "name: Collect AMD gating proposals",
            "name: Live publication audit",
            "name: Run test suite",
            "name: Enforce publication validation results",
            "python scripts/build_site.py --cache-bust-index",
        ]
        last = -1
        for token in ordered_tokens:
            idx = text.find(token)
            if idx < 0:
                self.error(
                    "workflow-hourly-step-missing",
                    f"hourly-master.yml missing {token!r}",
                    ".github/workflows/hourly-master.yml",
                )
                continue
            if idx <= last:
                self.error(
                    "workflow-hourly-step-order",
                    f"hourly-master.yml step {token!r} is out of order",
                    ".github/workflows/hourly-master.yml",
                )
            last = idx

        sync_start = text.find("name: Sync CI data from gh-pages")
        sync_end = text.find("\n      - name:", sync_start + 1)
        sync_block = (
            text[sync_start : sync_end if sync_end >= 0 else len(text)]
            if sync_start >= 0
            else ""
        )
        sync_commands = "\n".join(
            line
            for line in sync_block.splitlines()
            if not line.lstrip().startswith("#")
        )
        analytics_feedback_blocked = not bool(
            re.search(r"\banalytics\.json\b", sync_commands)
        )
        if not analytics_feedback_blocked:
            self.error(
                "workflow-public-analytics-feedback",
                (
                    "hourly-master.yml restores analytics.json from gh-pages; "
                    "the bounded public projection must never replace the full "
                    "private reliability input"
                ),
                ".github/workflows/hourly-master.yml",
            )
        if PUBLIC_ANALYTICS_BOUNDARY_MARKER not in sync_block:
            self.error(
                "workflow-public-analytics-boundary",
                (
                    "hourly-master.yml must document the one-way private-to-public "
                    "analytics projection boundary"
                ),
                ".github/workflows/hourly-master.yml",
            )

        analytics_owner = next(
            (
                surface
                for surface, spec in SURFACE_SPECS.items()
                if _publication_spec_owns_path(spec, PRIVATE_ANALYTICS_DATA_PATH)
            ),
            None,
        )
        analytics_spec = next(
            (spec for spec in DATA_SPECS if spec.relpath == PRIVATE_ANALYTICS_DATA_PATH),
            None,
        )
        private_lineage_ok = (
            analytics_owner == "ci_analytics"
            and analytics_spec is not None
            and "scripts/vllm/collect_analytics.py" in analytics_spec.producers
        )
        if not private_lineage_ok:
            self.error(
                "private-analytics-lineage",
                (
                    "full analytics.json must remain selector-owned by ci_analytics and "
                    "covered by the private data audit"
                ),
                PRIVATE_ANALYTICS_DATA_PATH,
            )

        manifest_path = self.root / "config/public_data_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            manifest = {}
        if not isinstance(manifest, dict):
            manifest = {}
        build_inputs = manifest.get("build_inputs")
        if not isinstance(build_inputs, list):
            build_inputs = []
        projected_files = manifest.get("projected_files")
        if not isinstance(projected_files, list):
            projected_files = []
        analytics_projection = next(
            (
                descriptor
                for descriptor in projected_files
                if isinstance(descriptor, dict)
                and descriptor.get("path") == PRIVATE_ANALYTICS_PATH
            ),
            None,
        )
        direct_public_files = {
            relative
            for field in ("required_files", "optional_files")
            for relative in (
                manifest.get(field) if isinstance(manifest.get(field), list) else []
            )
            if isinstance(relative, str)
        }
        projection_declared = (
            manifest.get("schema_version") == 2
            and PRIVATE_ANALYTICS_PATH in build_inputs
            and PRIVATE_ANALYTICS_PATH not in direct_public_files
            and isinstance(analytics_projection, dict)
            and analytics_projection.get("projector")
            == PUBLIC_ANALYTICS_PROJECTOR_ID
            and isinstance(analytics_projection.get("max_bytes"), int)
            and not isinstance(analytics_projection.get("max_bytes"), bool)
            and analytics_projection["max_bytes"] > 0
        )
        if not projection_declared:
            self.error(
                "public-analytics-projection",
                (
                    "public_data_manifest.json must retain analytics.json as a "
                    "private build input and declare its bounded public projection"
                ),
                "config/public_data_manifest.json",
            )

        build_site_path = self.root / "scripts/build_site.py"
        build_site_text = (
            build_site_path.read_text(errors="ignore")
            if build_site_path.exists()
            else ""
        )
        materialization_ok = (
            re.search(
                r"PUBLIC_ANALYTICS_PROJECTOR_ID\s*:\s*"
                r"compact_public_analytics_json",
                build_site_text,
            )
            is not None
            and re.search(
                r"materialize_projected_files\s*\(\s*DATA\s*,\s*"
                r"output_dir\s*/\s*['\"]data['\"]\s*,\s*manifest\s*,?\s*\)",
                build_site_text,
            )
            is not None
        )
        if not materialization_ok:
            self.error(
                "public-analytics-materialization",
                (
                    "build_site.py must materialize the declared analytics "
                    "projection through the registered projector"
                ),
                "scripts/build_site.py",
            )

        cache_prepare = workflow_step_block("Prepare private analytics cache key")
        cache_restore = workflow_step_block("Restore private analytics build cache")
        analytics_collect = workflow_step_block("Collect CI analytics")
        cache_save = workflow_step_block("Save private analytics build cache")
        cache_steps = (cache_prepare, cache_restore, analytics_collect, cache_save)
        cache_step_indexes = [text.index(block) for block in cache_steps] if all(
            cache_steps
        ) else []
        cache_ordered = bool(cache_step_indexes) and cache_step_indexes == sorted(
            cache_step_indexes
        )
        if not cache_ordered:
            self.error(
                "workflow-private-analytics-cache-order",
                (
                    "private analytics cache key/restore must precede analytics "
                    "collection and cache save must follow it"
                ),
                ".github/workflows/hourly-master.yml",
            )

        cache_key_ok = all(
            token in cache_prepare
            for token in (
                "id: analytics-cache-key",
                "CACHE_DAY=$(date -u +%Y-%m-%d)",
                "PRIOR_CACHE_DAY=$(date -u -d '1 day ago' +%Y-%m-%d)",
                f'CACHE_NAMESPACE="{PRIVATE_ANALYTICS_CACHE_VERSION}-${{{{ runner.os }}}}"',
                'echo "key=$CACHE_NAMESPACE-$CACHE_DAY-${{ github.run_id }}-'
                '${{ github.run_attempt }}"',
                'echo "current_day_prefix=$CACHE_NAMESPACE-$CACHE_DAY-"',
                'echo "prior_day_prefix=$CACHE_NAMESPACE-$PRIOR_CACHE_DAY-"',
            )
        )
        if not cache_key_ok:
            self.error(
                "workflow-private-analytics-cache-key",
                (
                    "private analytics cache needs a unique immutable key per "
                    "successful run plus current/prior UTC-day restore prefixes"
                ),
                ".github/workflows/hourly-master.yml",
            )

        cache_restore_ok = all(
            token in cache_restore
            for token in (
                "id: analytics-cache-restore",
                "continue-on-error: true",
                "uses: actions/cache/restore@v4",
                f"path: {PRIVATE_ANALYTICS_CACHE_PATH}",
                "key: ${{ steps.analytics-cache-key.outputs.key }}",
                "${{ steps.analytics-cache-key.outputs.current_day_prefix }}",
                "${{ steps.analytics-cache-key.outputs.prior_day_prefix }}",
            )
        )
        if not cache_restore_ok:
            self.error(
                "workflow-private-analytics-cache-restore",
                (
                    "private analytics cache restore must use actions/cache@v4, "
                    "the exact private path, and current/prior UTC-day prefixes"
                ),
                ".github/workflows/hourly-master.yml",
            )

        analytics_cache_signal_ok = all(
            token in analytics_collect
            for token in (
                "id: collect-analytics",
                "surface_is_current ci_analytics",
                'echo "cache_save=true"',
                'echo "cache_save=false"',
            )
        )
        cache_save_ok = analytics_cache_signal_ok and all(
            token in cache_save
            for token in (
                "steps.collect-analytics.outputs.cache_save == 'true'",
                "continue-on-error: true",
                "uses: actions/cache/save@v4",
                f"path: {PRIVATE_ANALYTICS_CACHE_PATH}",
                "key: ${{ steps.analytics-cache-key.outputs.key }}",
            )
        )
        if not cache_save_ok:
            self.error(
                "workflow-private-analytics-cache-save",
                (
                    "private analytics cache may be saved only after successful "
                    "analytics collection under that run's unique immutable key"
                ),
                ".github/workflows/hourly-master.yml",
            )

        if PRIVATE_ANALYTICS_CACHE_BOUNDARY_MARKER not in text:
            self.error(
                "workflow-private-analytics-cache-boundary",
                "hourly-master.yml must document the private analytics cache boundary",
                ".github/workflows/hourly-master.yml",
            )
        gh_pages_seed_blocks = [
            block
            for block in re.split(r"(?=^      - (?:name|uses):)", text, flags=re.MULTILINE)
            if "origin/gh-pages" in block
        ]
        cache_feedback_blocked = not any(
            token in "\n".join(
                line
                for line in block.splitlines()
                if not line.lstrip().startswith("#")
            )
            for block in gh_pages_seed_blocks
            for token in (
                PRIVATE_ANALYTICS_CACHE_PATH,
                PRIVATE_ANALYTICS_CACHE_VERSION,
            )
        )
        if not cache_feedback_blocked:
            self.error(
                "workflow-private-analytics-cache-feedback",
                (
                    "the private analytics cache must never be restored from the "
                    "public gh-pages branch"
                ),
                ".github/workflows/hourly-master.yml",
            )

        workflow_commands = "\n".join(
            line
            for line in text.splitlines()
            if not line.lstrip().startswith("#")
        )
        cache_staging_blocked = (
            re.search(
                r"\bgit\s+add\s+(?:[^\n]*\s)?(?:-f|--force)\b",
                workflow_commands,
            )
            is None
            and re.search(
                r"\bgit\s+add\b[^\n]*"
                + re.escape(PRIVATE_ANALYTICS_CACHE_PATH),
                workflow_commands,
            )
            is None
            and re.search(
                r"\bgit\s+add\s+\\[\s\S]{0,2000}?"
                + re.escape(PRIVATE_ANALYTICS_CACHE_PATH),
                workflow_commands,
            )
            is None
        )
        if not cache_staging_blocked:
            self.error(
                "workflow-private-analytics-cache-staging",
                "hourly-master.yml must never force-add or explicitly stage the cache",
                ".github/workflows/hourly-master.yml",
            )

        gitignore_path = self.root / ".gitignore"
        gitignore_lines = {
            line.strip()
            for line in (
                gitignore_path.read_text(errors="ignore").splitlines()
                if gitignore_path.exists()
                else []
            )
            if line.strip() and not line.lstrip().startswith("#")
        }
        cache_ignored = "data/vllm/ci/.cache/" in gitignore_lines
        if not cache_ignored:
            self.error(
                "private-analytics-cache-ignore",
                "the private analytics cache directory must remain gitignored",
                ".gitignore",
            )

        manifest_exact_paths = {
            relative
            for field in (
                "required_files",
                "optional_files",
                "build_inputs",
                "generated_files",
            )
            for relative in (
                manifest.get(field) if isinstance(manifest.get(field), list) else []
            )
            if isinstance(relative, str)
        }
        manifest_exact_paths.update(
            descriptor["path"]
            for descriptor in projected_files
            if isinstance(descriptor, dict) and isinstance(descriptor.get("path"), str)
        )
        cache_manifest_exposed = any(
            relative == PRIVATE_ANALYTICS_CACHE_MANIFEST_PATH
            or relative.startswith(f"{PRIVATE_ANALYTICS_CACHE_MANIFEST_PATH}/")
            or PRIVATE_ANALYTICS_CACHE_MANIFEST_PATH.startswith(
                f"{relative.rstrip('/')}/"
            )
            for relative in manifest_exact_paths
        ) or any(
            PurePosixPath(PRIVATE_ANALYTICS_CACHE_SAMPLE).match(pattern)
            for pattern in (
                manifest.get("optional_globs")
                if isinstance(manifest.get("optional_globs"), list)
                else []
            )
            if isinstance(pattern, str)
        )
        cache_never_published = any(
            PurePosixPath(PRIVATE_ANALYTICS_CACHE_SAMPLE).match(pattern)
            for pattern in (
                manifest.get("never_publish_patterns")
                if isinstance(manifest.get("never_publish_patterns"), list)
                else []
            )
            if isinstance(pattern, str)
        )
        if cache_manifest_exposed or not cache_never_published:
            self.error(
                "private-analytics-cache-publication",
                (
                    "the analytics cache must be absent from public/build inputs "
                    "and covered by an effective never-publish pattern"
                ),
                "config/public_data_manifest.json",
            )

        deploy_pages = self.root / ".github/workflows/deploy-pages.yml"
        deploy_text = deploy_pages.read_text(errors="ignore") if deploy_pages.exists() else ""
        forbidden_ci_writes = [
            "ci_health.json",
            "parity_report.json",
            "analytics.json",
            "amd_test_matrix.json",
            "group_changes.json",
        ]
        for name in forbidden_ci_writes:
            if re.search(r">\s*data/vllm/ci/" + re.escape(name), deploy_text):
                self.error(
                    "workflow-stale-gh-pages-sync",
                    f"deploy-pages.yml can overwrite {name} from gh-pages",
                    ".github/workflows/deploy-pages.yml",
                )

        self.report.metrics["workflows"] = {
            "workflow_count": len(workflows),
            "gh_pages_workflows": gh_pages_workflows,
            "private_analytics_surface": analytics_owner,
            "public_analytics_projection_declared": projection_declared,
            "public_analytics_feedback_blocked": analytics_feedback_blocked,
            "private_analytics_cache_ordered": cache_ordered,
            "private_analytics_cache_key_valid": cache_key_ok,
            "private_analytics_cache_feedback_blocked": cache_feedback_blocked,
            "private_analytics_cache_gitignored": cache_ignored,
            "private_analytics_cache_never_published": cache_never_published,
        }


def run_audit(root: Path = ROOT) -> AuditReport:
    return DashboardAudit(root).run()


def format_text(report: AuditReport) -> str:
    lines = [
        "Dashboard data audit",
        f"Errors: {len(report.errors)}",
        f"Degradations: {len(report.degradations)}",
        f"Warnings: {len(report.warnings)}",
    ]
    for severity, findings in (
        ("ERROR", report.errors),
        ("DEGRADATION", report.degradations),
        ("WARN", report.warnings),
    ):
        if not findings:
            continue
        lines.append("")
        lines.append(severity)
        for finding in findings:
            path = f" [{finding.path}]" if finding.path else ""
            lines.append(f"- {finding.code}{path}: {finding.message}")
    lines.append("")
    lines.append("Key metrics")
    for key in sorted(report.metrics):
        lines.append(f"- {key}: {json.dumps(report.metrics[key], sort_keys=True, default=str)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit generated dashboard data and contracts")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    focused = parser.add_mutually_exclusive_group()
    focused.add_argument(
        "--dns-only",
        action="store_true",
        help="Validate only the DNS health aggregate",
    )
    focused.add_argument(
        "--queue-lifecycle-only",
        action="store_true",
        help="Validate only the retained queue lifecycle aggregate",
    )
    focused.add_argument(
        "--queue-only",
        action="store_true",
        help="Validate only the live queue history and job evidence",
    )
    parser.add_argument(
        "--dns-path",
        type=Path,
        help="DNS aggregate path for --dns-only (defaults to the repository dataset)",
    )
    parser.add_argument(
        "--queue-lifecycle-path",
        type=Path,
        help=(
            "queue lifecycle aggregate path for --queue-lifecycle-only "
            "(defaults to the repository dataset)"
        ),
    )
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="Return nonzero when warnings are present",
    )
    args = parser.parse_args(argv)

    if args.dns_path is not None and not args.dns_only:
        parser.error("--dns-path requires --dns-only")
    if args.queue_lifecycle_path is not None and not args.queue_lifecycle_only:
        parser.error("--queue-lifecycle-path requires --queue-lifecycle-only")
    if args.dns_only:
        audit = DashboardAudit(ROOT)
        audit.audit_dns_failures(args.dns_path)
        report = audit.report
    elif args.queue_lifecycle_only:
        audit = DashboardAudit(ROOT)
        audit.audit_queue_lifecycle(
            args.queue_lifecycle_path,
            require_current_scope=True,
        )
        report = audit.report
    elif args.queue_only:
        audit = DashboardAudit(ROOT)
        audit.audit_queue_data(validate_derived=True)
        report = audit.report
    else:
        report = run_audit(ROOT)
    if args.format == "json":
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True, default=str))
    else:
        print(format_text(report))

    if report.errors or (args.strict_warnings and report.warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
