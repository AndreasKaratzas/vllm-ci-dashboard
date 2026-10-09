#!/usr/bin/env python3
"""Build the compact, authoritative v2 operations dashboard snapshot."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.bounded_json import atomic_write_bytes  # noqa: E402
from vllm.constants import amd_gpu_hardware, is_excluded_queue  # noqa: E402
from vllm.dashboard_storage_budget import writer_max_bytes  # noqa: E402
from vllm.operations_bundle_contract import (  # noqa: E402
    OPERATIONS_CANARY_SECTION_MAX_BYTES,
    OPERATIONS_PRODUCER_BUNDLE_VERSION,
    OperationsBundleContractError,
    validate_operations_canary_budget,
)
from vllm.ci.incident_transitions import (  # noqa: E402
    INCIDENT_TRANSITION_POLICY_ID,
    SOFT_CONFIRMATION_BUILDS,
    advance_incident,
    completed_build_eligibility,
)
from vllm.ci import analyzer as ci_analyzer  # noqa: E402
from vllm.ci.models import (  # noqa: E402
    AMD_OBSERVED_UNIQUE_TEST_GROUPS_COUNT_BASIS,
)
from vllm.ci.reliability_history import (  # noqa: E402
    OBSERVED_FAILURE_MOVEMENT_ID,
    collapse_nightly_attempts,
    compare_nightly_failures,
    hydrate_reliability_observations,
    validate_all_main_reliability,
)
from vllm.config_parity import (  # noqa: E402
    extract_amd_runtime_group_key_map_from_report,
)
from vllm.pipelines import is_amd_ci_job, is_cpu_only_job  # noqa: E402
from vllm.ci.nightly_latency import (  # noqa: E402
    build_current_nightly_latency, project_public_nightly_latency,
)
from vllm.queue_section_projection import compact_queue_section  # noqa: E402
from vllm.reviewed_definition_labels import resolve_reviewed_definition_labels  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_INPUT = ROOT / "data" / "vllm" / "ci"
DEFAULT_OUTPUT_NAME = "operations_v2.json.gz"
OPERATIONS_RAW_SUFFIX = ".json"
OPERATIONS_GZIP_SUFFIX = ".json.gz"
# The private source is intentionally stricter than GitHub's large-file warning.
# Compressed snapshots stay well below that boundary, while decompression is
# bounded independently so a small gzip bomb cannot consume an unbounded runner.
OPERATIONS_GZIP_MAX_BYTES = 64 * 1024 * 1024
OPERATIONS_RAW_WRITE_MAX_BYTES = 85 * 1024 * 1024
OPERATIONS_DECOMPRESSED_MAX_BYTES = 256 * 1024 * 1024
# Keep the largest public reliability asset comfortably below both the site
# assembler's 85 MiB ceiling and GitHub's large-file warning.  The subordinate
# budgets are used only after the complete source no longer fits; together they
# leave several MiB for fixed metadata and JSON framing.
OPERATIONS_RELIABILITY_SECTION_MAX_BYTES = 64 * 1024 * 1024
OPERATIONS_RELIABILITY_CATALOG_MAX_BYTES = 48 * 1024 * 1024
OPERATIONS_RETRY_EVIDENCE_MAX_BYTES = 4 * 1024 * 1024
OPERATIONS_RELIABILITY_DERIVED_MAX_BYTES = 2 * 1024 * 1024
OPERATIONS_RELIABILITY_ROW_MAX_BYTES = 256 * 1024
OPERATIONS_RELIABILITY_GROUP_MAX_BYTES = 768 * 1024
# The source agent-health generation is allowed to use 16 MiB.  Its public
# Operations route shares a 32 MiB eagerly parsed canary budget with every
# non-reliability route, so it needs a smaller independent projection.
OPERATIONS_AGENT_HEALTH_SECTION_MAX_BYTES = 8 * 1024 * 1024
OPERATIONS_AMD_TEST_HEALTH_SECTION_MAX_BYTES = 8 * 1024 * 1024
OPERATIONS_MANIFEST_NAME = "operations_v2_manifest.json"
OPERATIONS_BUNDLE_DIR_NAME = "operations_v2"
QUEUE_HISTORY_CHART_NAME = "queue_history_chart.json"
QUEUE_HISTORY_CHART_MAX_BYTES = writer_max_bytes("queue_history_chart")
ORG_SUMMARY_NAME = "org_summary.json"
ORG_SUMMARY_MAX_BYTES = writer_max_bytes("org_summary")
ORG_SUMMARY_SCHEMA_VERSION = 7
QUEUE_LIFECYCLE_NAME = "queue_lifecycle.json"
NIGHTLY_BUILD_LIMIT = 30
RANKING_LIMIT = 20
GROUP_HISTORY_LIMIT = 60
AMD_TEST_HISTORY_LIMIT = 30
AMD_TEST_RESULTS_GLOB = "test_results/*_amd.jsonl"
AMD_TEST_PIPELINE = "ci"
# Per-physical-agent (node) AMD GPU health is now collected and aggregated by
# scripts/vllm/collect_agent_health.py (all builds, all branches) and embedded
# verbatim from agent_health.json by _amd_agent_health below. See that collector
# for the rollup / infra-suspect model and the frontend for client-side
# aggregation + co-failure clustering.
FAILED_STATES = {"failed", "timed_out", "broken", "canceled"}
SOFT_FAILED_STATES = {"soft_fail", "soft_failed"}
TRUSTWORTHY_BUILD_STATES = {"passed", "failed"}
RETRY_EVIDENCE_FIELDS = (
    "retried",
    "retried_in_job_id",
    "retries_count",
    "retry_source",
    "retry_type",
    "step_key",
)

QUEUE_HISTORY_SHARD_FIELDS = {
    "waiting",
    "running",
    "scheduled",
    "total",
    "zombie_waiting",
    "zombie_running",
    "connected_agents",
    "connected_agents_available",
    "connected_agents_source",
    "count_source",
    "p50_wait",
    "p50_wait_source",
    "p95_wait",
    "p95_wait_source",
    "p99_wait",
    "p99_wait_source",
    "max_wait",
    "max_wait_source",
    "wait_source",
    "wait_sample_count",
    "wait_sample_expected_count",
    "wait_sample_complete",
    "official_wait",
    "sample_wait",
    "official_wait_source",
    "sample_wait_source",
    "archive_wait_peaks",
    "archive_sample_wait_peaks",
    "history_observation_only",
    "metrics_ts",
}

SOURCE_FILES = {
    "analytics": "analytics.json",
    "agent_health": "agent_health.json",
    "ci_health": "ci_health.json",
    "config_parity": "config_parity.json",
    "test_group_parity": "test_group_parity.json",
    "amd_test_matrix": "amd_test_matrix.json",
    "capacity_monitor": "capacity_monitor.json",
    "workload_mapping": "workload_mapping.json",
    "queue_timeseries": "queue_timeseries.jsonl",
    "queue_jobs": "queue_jobs.json",
    "omni_heuristic": "omni_surge_heuristic.json",
    "omni_issue_state": "open_omni_surge_issues.json",
    "project_items": "project_items.json",
    "ci_ownership": "ci_ownership.json",
}

MULTISPACE_RE = re.compile(r"\s+")
AMD_PREFIX_RE = re.compile(r"^AMD:\s*", re.IGNORECASE)
INTERNAL_AMD_PREFIX_RE = re.compile(
    r"^mi\d{3,4}b?_[a-z0-9][a-z0-9_-]*:\s*", re.IGNORECASE
)
STANDARD_PLATFORM_PREFIX_RE = re.compile(
    r"^:(?:amd|computer):\s*"
    r"\(\s*(?P<hardware>mi\d{3,4}b?(?: +[a-z0-9][a-z0-9._-]*)*|cpu)\s*\)\s*",
    re.IGNORECASE,
)
AMD_DEVICE_SUFFIX_RE = re.compile(r"\s*\((mi\d{3,4}b?_\d+)\)\s*$", re.IGNORECASE)
AMD_TARGET_SUFFIX_RE = re.compile(
    r"(?<=\d)(?:x)?mi\d{2,4}b?(?:[_-]\d+)?(?=\))",
    re.IGNORECASE,
)
AMD_TEST_JOB_PREFIX_RE = re.compile(
    r"^(?P<hardware_variant>mi\d{3}b?(?:_[a-z0-9][a-z0-9_-]*)?):\s*"
    r"(?P<display_name>.*)$",
    re.IGNORECASE,
)
AMD_TEST_INCIDENT_STATUSES = {"failed", "error"}
AMD_TEST_SOFT_STATES = {"soft", "soft_fail", "soft_failed"}
AMD_TEST_HARD_STATES = {"failed", "timed_out", "broken", "canceled"}
AMD_QUEUE_RE = re.compile(r"^amd_mi\d{3,4}b?(?:_|$)", re.IGNORECASE)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _is_current_queue_snapshot(row: Any) -> bool:
    """Recognize the provenance-bearing queue schema used by the dashboard."""
    return (
        isinstance(row, dict)
        and isinstance(row.get("ts"), str)
        and isinstance(row.get("queues"), dict)
        and isinstance(row.get("total_waiting"), int)
        and isinstance(row.get("total_running"), int)
        and isinstance(row.get("sources") or row.get("provenance"), dict)
    )


def load_latest_queue_snapshot(path: Path) -> dict:
    latest: dict = {}
    if not path.exists():
        return latest
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _is_current_queue_snapshot(row) and row["ts"] >= latest.get("ts", ""):
            latest = row
    return latest


def load_queue_history(path: Path) -> list[dict]:
    """Load every timestamped snapshot, including migrated counts-only rows."""
    rows: dict[str, dict] = {}
    if not path.exists():
        return []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or not isinstance(row.get("ts"), str):
            continue
        if not isinstance(row.get("queues"), dict):
            continue
        rows[row["ts"]] = row
    return [rows[key] for key in sorted(rows)]


def _is_excluded_queue(value: Any) -> bool:
    """Defensive presentation filter; collectors enforce the same exclusion."""
    return is_excluded_queue(str(value or ""))


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _payload_timestamp(data: dict) -> str:
    for key in ("generated_at", "ts", "updated_at", "last_snapshot_ts"):
        if data.get(key):
            return str(data[key])
    nested = [
        str(value.get("generated_at"))
        for value in data.values()
        if isinstance(value, dict) and value.get("generated_at")
    ]
    return max(nested, default="")


def _source_record(path: Path, data: dict, timestamp: str = "") -> dict:
    payload_ts = timestamp or _payload_timestamp(data)
    if payload_ts:
        return {"path": path.name, "timestamp": payload_ts, "timestamp_source": "payload"}
    if path.exists():
        mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"path": path.name, "timestamp": mtime, "timestamp_source": "file_mtime"}
    return {"path": path.name, "timestamp": None, "timestamp_source": "missing"}


def _build_url(pipeline: str, build: dict) -> str:
    number = _strict_int(build.get("number") or build.get("build_number"))
    return f"https://buildkite.com/vllm/{pipeline}/builds/{number}" if number else ""


def _job_url(pipeline: str, build: dict, job: dict) -> str:
    base = _build_url(pipeline, build)
    if not base:
        return ""
    if job.get("job_id"):
        return f"{base}/steps/canvas?jid={job['job_id']}&tab=output"
    if job.get("step_id"):
        return f"{base}/steps/canvas?sid={job['step_id']}&tab=output"
    raw_url = job.get("url") or job.get("web_url")
    build_number = build.get("number") or build.get("build_number")
    return str(raw_url) if _pipeline_job_url_matches(
        raw_url, pipeline, build_number
    ) else ""


def _group_identity(job: dict) -> str:
    return str(job.get("raw_name") or job.get("name") or "unknown")


def _group_row(
    pipeline: str,
    build: dict,
    job: dict,
    state: str,
    identity: dict[str, str],
    group_id: str,
) -> dict:
    raw_name = _group_identity(job)
    row = {
        "group_id": group_id,
        "name": raw_name,
        "state": state,
        "url": _job_url(pipeline, build, job),
        "source_pipeline": pipeline,
        "build_number": build.get("number") or build.get("build_number"),
        "step_key": identity["step_key"],
        "hardware": identity["hardware"],
        "queue": identity["queue"],
    }
    display_name = str(job.get("name") or "")
    if display_name and display_name != raw_name:
        row["display_name"] = display_name
    return row


def _nightly_group_observations(
    pipeline: str,
    build: dict,
) -> dict[str, tuple[str, dict]]:
    """Collapse terminal attempts to one eligible outcome per strict group."""
    observations: dict[str, tuple[str, dict]] = {}
    for group_id, selected in collapse_nightly_attempts(
        build.get("jobs") or [], pipeline
    ).items():
        outcome = selected["outcome"]
        display_state = {
            "hard": "failed",
            "soft": "soft_failed",
            "passed": "passed",
            "indeterminate": str(selected["job"].get("state") or "indeterminate"),
        }[outcome]
        observations[group_id] = (
            outcome,
            _group_row(
                pipeline,
                build,
                selected["job"],
                display_state,
                selected["identity"],
                group_id,
            ),
        )
    return observations


def _scoped_ci_analytics(analytics: dict) -> dict:
    builds = []
    for build in analytics.get("builds") or []:
        number = _strict_int(build.get("number"))
        if number is None or build.get("branch") != "main" or not _pipeline_build_url_matches(build.get("web_url"), "ci", number):
            continue
        jobs = [job for job in build.get("jobs") or [] if is_amd_ci_job(job)]
        if jobs:
            builds.append({**build, "jobs": jobs, "total_jobs": len(jobs)})
    return {**analytics, "pipeline": "ci", "builds": builds,
            "job_scope": "amd_gpu", "hardware_scope": "amd_mi_gpu", "display_name": "AMD MI main CI"}


def _current_ci_health(health: dict) -> dict:
    if health.get("hardware_scope") != "amd_mi_gpu" or health.get("job_scope") != "amd_gpu":
        return {}
    def valid(build: dict) -> bool:
        number = _strict_int(build.get("number") or build.get("build_number"))
        return number is not None and build.get("branch") == "main" and _pipeline_build_url_matches(build.get("build_url") or build.get("url"), "ci", number)
    result = {**health, "builds": [build for build in health.get("builds") or [] if valid(build)]}
    for key in ("latest_build", "latest_pipeline_build", "latest_test_signal_build"):
        reference = health.get(key) or {}
        result[key] = reference if valid(reference) else None
    return result


def _nightly_pipeline(pipeline: str, analytics: dict, health: dict | None = None, *, job_scope: str = "") -> dict:
    health = health or {}
    source_builds_by_number = {
        _strict_int(build.get("number") or build.get("build_number")): dict(build)
        for build in analytics.get("builds") or []
        if _strict_int(build.get("number") or build.get("build_number")) is not None
    }
    analytics_build_numbers = frozenset(source_builds_by_number)
    health_builds = {
        _strict_int(build.get("number") or build.get("build_number")): build
        for build in health.get("builds") or []
        if _strict_int(build.get("number") or build.get("build_number")) is not None
    }
    latest_pipeline = health.get("latest_pipeline_build") or {}
    latest_pipeline_number = _strict_int(
        latest_pipeline.get("number") or latest_pipeline.get("build_number")
    )
    # Analytics can intentionally be an older last-known-good transaction while
    # ci_health remains current. Preserve both current core references in the
    # derived nightly history so cross-generation rebuilds stay internally
    # coherent without weakening the audit or rolling back ci_core.
    for reference_name in (
        "latest_pipeline_build",
        "latest_test_signal_build",
    ):
        reference = health.get(reference_name) or {}
        reference_number = _strict_int(
            reference.get("number") or reference.get("build_number")
        )
        if reference_number is None:
            continue
        health_builds[reference_number] = {
            **(health_builds.get(reference_number) or {}),
            **reference,
        }
        if reference_number not in source_builds_by_number:
            source_builds_by_number[reference_number] = {
                "number": reference_number,
                "created_at": reference.get("created_at") or "",
                "finished_at": reference.get("finished_at") or "",
                "state": reference.get("state") or "unknown",
                "commit": reference.get("commit") or "",
                "message": reference.get("message") or "",
                "total_jobs": reference.get("job_count") or 0,
                "jobs": [],
            }
    source_builds = sorted(
        source_builds_by_number.values(),
        key=lambda build: (
            str(build.get("created_at") or build.get("date") or ""),
            int(build.get("number") or build.get("build_number") or 0),
        ),
        reverse=True,
    )
    rows: list[dict] = []
    incident_states: dict[str, dict] = {}
    incident_refs: dict[str, dict] = {}
    previous_eligible_build: dict | None = None
    previous_movement_build: dict | None = None
    previous_movement_observations: dict[str, tuple[str, dict]] | None = None
    for build in reversed(source_builds):
        build_number = _strict_int(build.get("number") or build.get("build_number"))
        transition_eligible, ineligible_reason = completed_build_eligibility(build)
        health_build = health_builds.get(build_number) or {}
        has_test_results = bool(
            health_build.get("has_test_results")
            if "has_test_results" in health_build
            else build.get("jobs")
        )
        observations = _nightly_group_observations(pipeline, build)
        movement_available = transition_eligible and has_test_results and any(
            outcome in {"hard", "soft", "passed"}
            for outcome, _ in observations.values()
        )
        transition_preceding_build_number = (
            previous_eligible_build.get("number")
            or previous_eligible_build.get("build_number")
            if previous_eligible_build
            else None
        )
        movement_preceding_build_number = (
            previous_movement_build.get("number")
            or previous_movement_build.get("build_number")
            if previous_movement_build
            else None
        )
        failure_movement = compare_nightly_failures(
            observations,
            previous_movement_observations,
            preceding_build_number=movement_preceding_build_number,
            eligible=movement_available,
        )
        hard = {
            key: ref for key, (outcome, ref) in observations.items()
            if outcome == "hard"
        }
        soft = {
            key: ref for key, (outcome, ref) in observations.items()
            if outcome == "soft"
        }
        buckets: dict[str, list[dict]] = {
            "new": [],
            "recurring": [],
            "fixed": [],
            "pending_soft": [],
            "not_observed": [],
            "indeterminate": [],
        }
        active_keys = {
            key for key, state in incident_states.items()
            if state.get("status") in {"pending_soft", "confirmed"}
        }
        for key in sorted(set(observations) | active_keys):
            observed_outcome, current_ref = observations.get(key, ("absent", {}))
            outcome = observed_outcome if transition_eligible else "indeterminate"
            previous_ref = incident_refs.get(key) or {}
            decision = advance_incident(
                incident_states.get(key), outcome, build_number
            )
            next_state = decision["state"]
            classification = decision["classification"]

            if classification == "none":
                pass
            elif classification == "fixed":
                buckets[classification].append({
                    **previous_ref,
                    "current_state": "passed",
                    "current_url": current_ref.get("url") or "",
                    "transition_change": decision["change"],
                    "transition_eligible": transition_eligible,
                })
            else:
                held_indeterminate = (
                    decision["change"] == "held"
                    and decision["outcome"] == "indeterminate"
                )
                ref = previous_ref if held_indeterminate else (
                    current_ref if current_ref else previous_ref
                )
                row = {
                    **ref,
                    "incident_status": next_state["status"],
                    "current_severity": next_state["severity"],
                    "peak_severity": next_state["peak_severity"],
                    "soft_streak": next_state["soft_streak"],
                    "confirmation_threshold": SOFT_CONFIRMATION_BUILDS,
                    "transition_change": decision["change"],
                    "transition_eligible": transition_eligible,
                }
                if held_indeterminate and current_ref:
                    row["current_indeterminate_evidence"] = dict(current_ref)
                if not current_ref:
                    row["observed_in_current_build"] = False
                if ineligible_reason:
                    row["transition_ineligible_reason"] = ineligible_reason
                buckets[classification].append(row)

            if next_state["status"] == "clear":
                incident_states.pop(key, None)
                incident_refs.pop(key, None)
            else:
                incident_states[key] = next_state
                if transition_eligible and observed_outcome in {"hard", "soft"}:
                    incident_refs[key] = current_ref

        for transition_rows in buckets.values():
            transition_rows.sort(key=lambda row: (
                str(row.get("name") or "").casefold(),
                str(row.get("hardware") or ""),
                str(row.get("queue") or ""),
                str(row.get("group_id") or ""),
            ))
        rows.append({
            "number": build_number,
            "source_pipeline": pipeline,
            "created_at": build.get("created_at") or "",
            "state": build.get("state") or "unknown",
            "url": _build_url(pipeline, build),
            "commit": build.get("commit") or build.get("commit_sha") or "",
            "message": build.get("message") or "",
            "total_groups": (
                build.get("total_jobs") or len(build.get("jobs") or [])
                if has_test_results else 0
            ),
            "has_test_results": has_test_results,
            "transition_eligible": transition_eligible,
            "transition_ineligible_reason": ineligible_reason or None,
            "test_job_count": int(health_build.get("test_job_count") or 0),
            "test_jobs_blocked": int(health_build.get("test_jobs_blocked") or 0),
            "unique_test_groups": int(
                health_build.get("unique_test_groups") or 0
            ),
            "test_groups_passing_or": int(
                health_build.get("test_groups_passing_or") or 0
            ),
            "test_groups_passing_all": int(
                health_build.get("test_groups_passing_all") or 0
            ),
            "test_groups_partial": int(
                health_build.get("test_groups_partial") or 0
            ),
            "failed_groups": sorted(
                hard.values(),
                key=lambda row: (
                    str(row.get("name") or "").casefold(),
                    str(row.get("hardware") or ""),
                    str(row.get("queue") or ""),
                    str(row.get("group_id") or ""),
                ),
            ),
            "soft_failed_groups": sorted(
                soft.values(),
                key=lambda row: (
                    str(row.get("name") or "").casefold(),
                    str(row.get("hardware") or ""),
                    str(row.get("queue") or ""),
                    str(row.get("group_id") or ""),
                ),
            ),
            "failure_movement": failure_movement,
            "transitions": {
                "policy_id": INCIDENT_TRANSITION_POLICY_ID,
                "preceding_build_number": transition_preceding_build_number,
                **buckets,
            },
        })
        if transition_eligible:
            previous_eligible_build = build
        if movement_available:
            previous_movement_build = build
            previous_movement_observations = observations
    rows.reverse()
    retained_rows = rows[:NIGHTLY_BUILD_LIMIT]
    canonical_build_number = (
        _strict_int(retained_rows[0].get("number")) if retained_rows else None
    )
    analytics_ahead_build_numbers = sorted(
        (
            number
            for number in analytics_build_numbers
            if latest_pipeline_number is not None
            and number > latest_pipeline_number
        ),
        reverse=True,
    )
    if latest_pipeline_number is None:
        head_alignment_status = "ci_health_reference_unavailable"
    elif canonical_build_number == latest_pipeline_number:
        head_alignment_status = "aligned"
    elif (
        canonical_build_number in analytics_ahead_build_numbers
        and canonical_build_number is not None
        and canonical_build_number > latest_pipeline_number
    ):
        head_alignment_status = "analytics_ahead_of_ci_health"
    else:
        head_alignment_status = "inconsistent"
    return {
        "pipeline": pipeline,
        "source_pipeline": pipeline,
        "job_scope": job_scope,
        "hardware_scope": "amd_mi_gpu",
        "cohort_id": "ci-amd",
        "transition_policy_id": INCIDENT_TRANSITION_POLICY_ID,
        "failure_movement_policy_id": OBSERVED_FAILURE_MOVEMENT_ID,
        "display_name": analytics.get("display_name") or pipeline,
        "role": "canonical_amd_nightly",
        "history_window_days": min(int(analytics.get("days") or NIGHTLY_BUILD_LIMIT), NIGHTLY_BUILD_LIMIT),
        "history_limit": NIGHTLY_BUILD_LIMIT,
        "builds_available": len(source_builds),
        "builds": retained_rows,
        "head_alignment": {
            "status": head_alignment_status,
            "canonical_build_number": canonical_build_number,
            "ci_health_build_number": latest_pipeline_number,
            "analytics_ahead_build_numbers": analytics_ahead_build_numbers,
        },
    }


def _aggregate_amd_jobs(builds: list[dict]) -> list[dict]:
    stats: dict[str, dict] = defaultdict(
        lambda: {"runs": 0, "passed": 0, "failed": 0, "soft_failed": 0, "durations": [], "queues": set()}
    )
    for build in builds:
        for job in build.get("jobs") or []:
            row = stats[str(job.get("name") or _group_identity(job))]
            row["runs"] += 1
            state = str(job.get("state") or "").lower()
            if state == "passed":
                row["passed"] += 1
            elif state in SOFT_FAILED_STATES or job.get("soft_failed"):
                row["soft_failed"] += 1
            elif state in FAILED_STATES:
                row["failed"] += 1
            if isinstance(job.get("dur"), (int, float)):
                row["durations"].append(float(job["dur"]))
            if job.get("q"):
                row["queues"].add(str(job["q"]))

    rows = []
    for name, values in stats.items():
        durations = sorted(values.pop("durations"))
        queues = sorted(values.pop("queues"))
        failures = values["failed"] + values["soft_failed"]
        rows.append({
            "name": name,
            **values,
            "fail_rate": round(failures / values["runs"] * 100, 1) if values["runs"] else 0,
            "median_dur": round(median(durations), 1) if durations else None,
            "p90_dur": _percentile(durations, 90),
            "max_dur": round(max(durations), 1) if durations else None,
            "queues": queues,
        })
    return rows


def _percentile(values: list[float], percent: int) -> float | None:
    if not values:
        return None
    index = (len(values) - 1) * percent / 100
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    value = values[lower] + (values[upper] - values[lower]) * (index - lower)
    return round(value, 1)


def _ranking_row(row: dict) -> dict:
    keys = (
        "name", "runs", "passed", "failed", "soft_failed", "fail_rate",
        "median_dur", "p90_dur", "avg_dur", "max_dur", "queues",
    )
    return {key: row[key] for key in keys if key in row}


def _historical_state(job: dict) -> str:
    state = str(job.get("state") or "").lower()
    if state in SOFT_FAILED_STATES or job.get("soft_failed"):
        return "soft"
    if state in FAILED_STATES or state == "expired":
        return "hard"
    if state == "passed":
        return "passed"
    return "unknown"


def _retry_evidence(job: dict) -> dict:
    retries_count = job.get("retries_count")
    has_explicit_signal = (
        bool(job.get("retried"))
        or bool(job.get("retried_in_job_id"))
        or retries_count not in (None, "", 0, "0")
        or bool(job.get("retry_source"))
        or bool(job.get("retry_type"))
    )
    if not has_explicit_signal:
        return {}
    evidence = {
        key: job.get(key)
        for key in RETRY_EVIDENCE_FIELDS
        if key in job
    }
    for key in ("job_id", "step_id"):
        if job.get(key):
            evidence[key] = job[key]
    return evidence


def _strict_group_label(value: Any) -> str:
    """Normalize decoration while preserving meaningful hardware wording."""
    text = MULTISPACE_RE.sub(" ", str(value or "").strip())
    text = AMD_PREFIX_RE.sub("", text)
    text = INTERNAL_AMD_PREFIX_RE.sub("", text)
    text = STANDARD_PLATFORM_PREFIX_RE.sub("", text)
    text = AMD_DEVICE_SUFFIX_RE.sub("", text)
    return MULTISPACE_RE.sub(" ", text).strip()


def _strict_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def _strict_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _pipeline_url_parts(value: Any, pipeline_slug: str) -> tuple[int, list[str], dict] | None:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.netloc != "buildkite.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[:3] != ["vllm", pipeline_slug, "builds"]:
        return None
    build_number = _strict_int(parts[3])
    if build_number is None:
        return None
    return build_number, parts[4:], parse_qs(parsed.query)


def _pipeline_build_url_matches(
    value: Any,
    pipeline_slug: str,
    build_number: Any = None,
) -> bool:
    parsed = _pipeline_url_parts(value, pipeline_slug)
    expected = _strict_int(build_number)
    return bool(parsed and not parsed[1] and (expected is None or parsed[0] == expected))


def _pipeline_job_url_matches(
    value: Any,
    pipeline_slug: str,
    build_number: Any = None,
) -> bool:
    parsed = _pipeline_url_parts(value, pipeline_slug)
    expected = _strict_int(build_number)
    if not parsed or (expected is not None and parsed[0] != expected):
        return False
    suffix, query = parsed[1], parsed[2]
    if len(suffix) < 2 or suffix[0] != "steps":
        return False
    if suffix[1] == "canvas":
        return bool(query.get("jid") or query.get("sid"))
    return bool(suffix[1])


def _amd_test_group_id(exact_job_name: str, pipeline_slug: str = AMD_TEST_PIPELINE) -> str:
    identity = f"{pipeline_slug}:{exact_job_name}".encode("utf-8")
    return hashlib.sha1(identity).hexdigest()[:20]


def _amd_test_job_labels(exact_job_name: str) -> tuple[str, str, str, str]:
    match = AMD_TEST_JOB_PREFIX_RE.match(exact_job_name)
    if match:
        hardware_variant = match.group("hardware_variant").lower()
        hardware = hardware_variant.split("_", 1)[0]
        # Parsed test-result names may preserve the standardized YAML label
        # inside the Buildkite queue prefix.  Keep the exact name as identity,
        # but do not leak the nested decorator into the dashboard label.
        display_name = STANDARD_PLATFORM_PREFIX_RE.sub(
            "", match.group("display_name"), count=1
        ).strip()
        return display_name, hardware, hardware_variant, f"amd_{hardware_variant}"
    standard = STANDARD_PLATFORM_PREFIX_RE.match(exact_job_name)
    if standard:
        hardware_variant = "_".join(standard.group("hardware").lower().split())
        hardware = hardware_variant.split("_", 1)[0]
        display_name = exact_job_name[standard.end():].strip()
        queue = "cpu" if hardware == "cpu" else f"amd_{hardware_variant}"
        return display_name, hardware, hardware_variant, queue
    return exact_job_name, "unknown", "unknown", ""


def _amd_test_result_count(row: dict) -> int:
    for key in ("test_count", "count"):
        value = row.get(key)
        if isinstance(value, bool):
            continue
        try:
            count = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if count > 0:
            return count
    match = re.search(r"\((\d+)\)\s*$", str(row.get("name") or ""))
    return int(match.group(1)) if match else 1


def _amd_test_job_state(job: dict) -> str:
    state = str(job.get("state") or "").strip().lower()
    if job.get("soft_failed") or state in AMD_TEST_SOFT_STATES:
        return "soft"
    if state in AMD_TEST_HARD_STATES:
        return "hard"
    if state == "passed":
        return "passed"
    return "unknown"


def _amd_test_observation_state(jobs: list[dict]) -> str:
    states = {_amd_test_job_state(job) for job in jobs}
    for state in ("hard", "soft", "passed"):
        if state in states:
            return state
    return "unknown"


def _amd_test_pass_rate(passed: int, incidents: int) -> float | None:
    known = passed + incidents
    return round(passed / known * 100, 1) if known else None


def _amd_test_metadata_builds(amd_analytics: Any) -> dict[int, dict]:
    if not isinstance(amd_analytics, dict):
        return {}
    result: dict[int, dict] = {}
    builds = amd_analytics.get("builds")
    if not isinstance(builds, list):
        return result
    for build in builds:
        if not isinstance(build, dict):
            continue
        number = _strict_int(build.get("number") or build.get("build_number"))
        if number is not None and number not in result:
            result[number] = build
    return result


def _amd_test_job_metadata(
    build: dict,
    evidence_rows: list[dict],
) -> list[dict]:
    jobs = build.get("jobs")
    if not isinstance(jobs, list):
        return []
    by_job_id = {
        str(job.get("job_id")): job
        for job in jobs
        if isinstance(job, dict) and job.get("job_id")
    }
    matches = []
    seen: set[str] = set()
    for evidence in evidence_rows:
        job_id = str(evidence.get("job_id") or "")
        if not job_id or job_id in seen or job_id not in by_job_id:
            continue
        seen.add(job_id)
        matches.append(by_job_id[job_id])
    return matches


def _amd_test_build_url(build_number: int, metadata: dict) -> str:
    raw_url = metadata.get("web_url") or metadata.get("url")
    if _pipeline_build_url_matches(raw_url, AMD_TEST_PIPELINE, build_number):
        return str(raw_url)
    return _build_url(AMD_TEST_PIPELINE, {"number": build_number})


def _amd_test_evidence_row(evidence_rows: list[dict], metadata: dict) -> dict:
    metadata_job_id = str(metadata.get("job_id") or "")
    selected = [
        row for row in evidence_rows
        if metadata_job_id and str(row.get("job_id") or "") == metadata_job_id
    ]
    candidates = selected or evidence_rows
    if not candidates:
        return {}
    return max(
        candidates,
        key=lambda row: (
            bool(row.get("job_id")),
            bool(row.get("step_id")),
            bool(row.get("url") or row.get("web_url")),
        ),
    )


def _amd_test_job_url(build_number: int, evidence: dict, metadata: dict) -> str:
    job = {
        "job_id": evidence.get("job_id") or metadata.get("job_id"),
        "step_id": evidence.get("step_id") or metadata.get("step_id"),
        "url": evidence.get("url") or evidence.get("web_url") or metadata.get("url"),
    }
    return _job_url(AMD_TEST_PIPELINE, {"number": build_number}, job)


def _load_amd_test_result_groups(data_dir: Path, metadata_by_build: dict[int, dict] | None = None) -> tuple[dict[tuple[int, str], dict], dict]:
    try:
        paths = sorted((data_dir / "test_results").glob("*_amd.jsonl"))
    except OSError:
        paths = []
    grouped: dict[tuple[int, str], dict] = {}
    stats = {
        "files_discovered": len(paths),
        "files_read": 0,
        "files_with_valid_rows": 0,
        "unreadable_files": 0,
        "valid_rows": 0,
        "malformed_rows": 0,
        "ignored_rows": 0,
        "source_files": [],
    }
    for path in paths:
        try:
            relative_path = path.relative_to(data_dir).as_posix()
        except ValueError:
            relative_path = path.name
        stats["source_files"].append(relative_path)
        fallback_date = path.name.removesuffix("_amd.jsonl")
        file_valid_rows = 0
        try:
            with path.open(encoding="utf-8") as source:
                stats["files_read"] += 1
                for line in source:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        stats["malformed_rows"] += 1
                        continue
                    if not isinstance(row, dict):
                        stats["malformed_rows"] += 1
                        continue
                    number = _strict_int(row.get("build_number"))
                    roster = ((metadata_by_build or {}).get(number) if number is not None else None) or {}
                    matched = next((job for job in roster.get("jobs") or [] if row.get("job_id") and job.get("job_id") == row["job_id"]), {})
                    routing = {**matched, **row, "raw_name": row.get("job_name")}
                    if row.get("pipeline") != AMD_TEST_PIPELINE or is_cpu_only_job({**row, "raw_name": row.get("job_name")}) or not is_amd_ci_job(routing):
                        stats["ignored_rows"] += 1
                        continue
                    build_number = _strict_int(row.get("build_number"))
                    exact_job_name = row.get("job_name")
                    if build_number is None or not isinstance(exact_job_name, str) or not exact_job_name:
                        stats["malformed_rows"] += 1
                        continue
                    status = str(row.get("status") or "unknown").strip().lower() or "unknown"
                    count = _amd_test_result_count(row)
                    key = (build_number, exact_job_name)
                    bucket = grouped.setdefault(key, {
                        "build_number": build_number,
                        "exact_job_name": exact_job_name,
                        "dates": set(),
                        "status_counts": Counter(),
                        "status_row_counts": Counter(),
                        "test_duration_secs": 0.0,
                        "evidence_rows": [],
                    })
                    date = row.get("date") or fallback_date
                    if date:
                        bucket["dates"].add(str(date))
                    bucket["status_counts"][status] += count
                    bucket["status_row_counts"][status] += 1
                    duration = _number(row.get("duration_secs"))
                    if duration is not None and duration >= 0 and duration != float("inf"):
                        bucket["test_duration_secs"] += duration
                    bucket["evidence_rows"].append({
                        key: row.get(key)
                        for key in (
                            "status", "job_id", "step_id", "url", "web_url",
                            "observed_at", "finished_at", "started_at",
                        )
                        if row.get(key) not in (None, "")
                    } | {"status": status})
                    file_valid_rows += 1
                    stats["valid_rows"] += 1
        except (OSError, UnicodeError):
            stats["unreadable_files"] += 1
        if file_valid_rows:
            stats["files_with_valid_rows"] += 1
    return grouped, stats


def _amd_test_observation(bucket: dict, metadata: dict) -> dict:
    build_number = bucket["build_number"]
    exact_job_name = bucket["exact_job_name"]
    display_name, hardware, hardware_variant, queue = _amd_test_job_labels(exact_job_name)
    status_counts = Counter(bucket["status_counts"])
    job_metadata_rows = _amd_test_job_metadata(metadata, bucket["evidence_rows"])
    state = _amd_test_observation_state(job_metadata_rows)
    state_metadata = [
        job for job in job_metadata_rows
        if _amd_test_job_state(job) == state
    ]
    job_metadata = max(
        state_metadata or job_metadata_rows or [{}],
        key=lambda job: str(job.get("finished_at") or job.get("started_at") or ""),
    )
    metadata_queue = str(job_metadata.get("q") or "").strip().lower()
    if AMD_QUEUE_RE.match(metadata_queue):
        queue = metadata_queue
        hardware_variant = metadata_queue.removeprefix("amd_")
        hardware = hardware_variant.split("_", 1)[0]
    evidence = _amd_test_evidence_row(bucket["evidence_rows"], job_metadata)
    build_url = _amd_test_build_url(build_number, metadata)
    job_url = _amd_test_job_url(build_number, evidence, job_metadata)
    observed_at = (
        job_metadata.get("finished_at")
        or job_metadata.get("started_at")
        or evidence.get("observed_at")
        or evidence.get("finished_at")
        or evidence.get("started_at")
        or metadata.get("created_at")
        or metadata.get("finished_at")
        or max(bucket["dates"], default="")
    )
    date = str(metadata.get("date") or max(bucket["dates"], default=""))
    tests = sum(status_counts.values())
    passed_tests = status_counts.get("passed", 0)
    failed_tests = sum(status_counts.get(status, 0) for status in AMD_TEST_INCIDENT_STATUSES)
    skipped_tests = status_counts.get("skipped", 0) + status_counts.get("xfailed", 0)
    unknown_tests = max(0, tests - passed_tests - failed_tests - skipped_tests)
    duration_secs = round(float(bucket["test_duration_secs"]), 2)
    row = {
        "source_pipeline": AMD_TEST_PIPELINE,
        "build_number": build_number,
        "state": state,
        "outcome_source": "analytics_job_state" if job_metadata_rows else "unavailable",
        "analytics_job_count": len(job_metadata_rows),
        "observed_at": str(observed_at or ""),
        "date": date or str(observed_at or "")[:10],
        "url": job_url,
        "job_url": job_url,
        "build_url": build_url,
        "hardware": hardware,
        "hardware_variant": hardware_variant,
        "queue": queue,
        "status_counts": dict(sorted(status_counts.items())),
        "status_row_counts": dict(sorted(bucket["status_row_counts"].items())),
        "tests": tests,
        "passed_tests": passed_tests,
        "failed_tests": failed_tests,
        "error_tests": status_counts.get("error", 0),
        "skipped_tests": skipped_tests,
        "unknown_tests": unknown_tests,
        "test_duration_secs": duration_secs,
        "test_duration_mins": round(duration_secs / 60, 2),
        "duration_mins": round(duration_secs / 60, 2),
        "duration_basis": "test_reported",
    }
    job_id = evidence.get("job_id") or job_metadata.get("job_id")
    step_id = evidence.get("step_id") or job_metadata.get("step_id")
    if job_id:
        row["job_id"] = str(job_id)
    if step_id:
        row["step_id"] = str(step_id)
    return row


def _amd_test_sort_key(row: dict) -> tuple[int, str]:
    """Order AMD observations by nightly cycle, then attempt timestamp.

    An older Buildkite build can be retried after a newer nightly completes.
    Completion time therefore cannot define which nightly is current.
    """
    return (
        _strict_int(row.get("build_number")) or 0,
        str(row.get("observed_at") or row.get("date") or ""),
    )


def _latest_amd_test_group_counts(
    latest_job_variant_build: dict,
    amd_ci_health: Any,
) -> dict:
    """Project logical test-group counts for the exact same nightly build.

    Parsed result rows are keyed by exact Buildkite job name and therefore
    count hardware and shard variants.  ``ci_health`` is the authoritative
    logical-group aggregation.  Never combine those independently moving
    sources unless their build numbers match.
    """
    source = "ci_health.amd.latest_test_signal_build"
    job_variant_build_number = _strict_int(
        latest_job_variant_build.get("build_number")
        or latest_job_variant_build.get("number")
    )
    signal = (
        amd_ci_health.get("latest_test_signal_build")
        if isinstance(amd_ci_health, dict)
        else None
    )
    signal = signal if isinstance(signal, dict) else {}
    signal_build_number = _strict_int(
        signal.get("build_number") or signal.get("number")
    )

    base = {
        "available": False,
        "build_number": None,
        "job_variant_build_number": job_variant_build_number,
        "test_signal_build_number": signal_build_number,
        "total": None,
        "passing": None,
        "non_passing": None,
        "passing_all": None,
        "partial": None,
        "pass_percentage": None,
        "pass_rate_pct": None,
        "source": source,
        "passing_policy": "passes_on_any_observed_hardware",
        "count_basis": str(
            signal.get("observed_unique_test_groups_count_basis")
            or AMD_OBSERVED_UNIQUE_TEST_GROUPS_COUNT_BASIS
        ),
    }
    if job_variant_build_number is None:
        return {**base, "reason": "latest_job_variant_build_unavailable"}
    if signal_build_number is None:
        return {**base, "reason": "latest_test_signal_build_unavailable"}
    if signal_build_number != job_variant_build_number:
        return {**base, "reason": "build_mismatch"}

    values = {
        "total": _strict_nonnegative_int(signal.get("unique_test_groups")),
        "passing": _strict_nonnegative_int(signal.get("test_groups_passing_or")),
        "passing_all": _strict_nonnegative_int(
            signal.get("test_groups_passing_all")
        ),
        "partial": _strict_nonnegative_int(signal.get("test_groups_partial")),
    }
    if any(value is None for value in values.values()):
        return {**base, "reason": "invalid_group_counts"}

    total = int(values["total"])
    passing = int(values["passing"])
    passing_all = int(values["passing_all"])
    partial = int(values["partial"])
    if not (
        0 <= passing_all <= passing <= total
        and 0 <= partial <= passing
        and passing_all + partial == passing
    ):
        return {**base, "reason": "inconsistent_group_counts"}
    pass_percentage = round(passing / total * 100, 1) if total else None
    return {
        **base,
        "available": True,
        "reason": None,
        "build_number": job_variant_build_number,
        "total": total,
        "passing": passing,
        "non_passing": total - passing,
        "passing_all": passing_all,
        "partial": partial,
        "pass_percentage": pass_percentage,
        "pass_rate_pct": pass_percentage,
    }


def _amd_test_shard_bases(data_dir: Path) -> list[str]:
    """Load the exact shard-normalization inputs used by the CI analyzer."""
    path = data_dir / "shard_bases.json"
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeError):
        return []
    if not isinstance(payload, list):
        return []
    return sorted({
        str(value).strip().casefold()
        for value in payload
        if isinstance(value, str) and value.strip()
    })


def _amd_test_signal_value(status_counts: dict[str, int]) -> bool | None:
    """Collapse one exact job variant with analyzer-compatible precedence."""
    statuses = {
        str(status).strip().casefold()
        for status, count in status_counts.items()
        if count
    }
    if statuses & AMD_TEST_INCIDENT_STATUSES:
        return False
    if statuses & {"passed", "xpassed"}:
        return True
    if statuses & {"canceled", "skipped", "xfailed"}:
        return None
    return None


def _amd_test_signal_state(value: bool | None) -> str:
    if value is True:
        return "passing"
    if value is False:
        return "failing"
    return "no_pass_signal"


def _merge_amd_test_signal(
    current: bool | None,
    incoming: bool | None,
) -> bool | None:
    """Mirror analyzer precedence: failure, then pass, then no signal."""
    if current is False or incoming is False:
        return False
    if current is True or incoming is True:
        return True
    return None


def _friendly_amd_logical_label(logical_key: str, variants: list[dict]) -> str:
    """Preserve Buildkite casing while retaining route-aware key suffixes."""
    candidates = sorted({
        str(row.get("display_name") or "").strip()
        for row in variants
        if str(row.get("display_name") or "").strip()
    }, key=lambda value: (len(value), value.casefold(), value))
    for candidate in candidates:
        normalized = ci_analyzer._normalize_job_name(candidate).strip()
        if normalized == logical_key:
            return candidate
        if normalized.startswith(logical_key):
            return candidate[:len(logical_key)].rstrip()
        if logical_key.startswith(normalized):
            return f"{candidate}{logical_key[len(normalized):]}"
    return logical_key[:1].upper() + logical_key[1:]


def _latest_logical_amd_test_groups(
    data_dir: Path,
    latest_counts: dict,
    metadata: dict,
    grouped: dict[tuple[int, str], dict],
    observations: dict[str, dict],
    definition_parity: Any,
) -> dict:
    """Publish an exact, auditable inventory behind the latest group totals.

    Identity and per-hardware state intentionally use the same private analyzer
    helpers as ``compute_build_summary``.  The inventory is withheld unless
    its rows reconcile exactly to the already-published, build-pinned totals.
    """
    build_number = _strict_int(latest_counts.get("build_number"))
    build_commit = str(
        metadata.get("commit") or metadata.get("commit_sha") or ""
    ).strip().casefold()
    count_basis = str(
        latest_counts.get("count_basis")
        or AMD_OBSERVED_UNIQUE_TEST_GROUPS_COUNT_BASIS
    )
    passing_policy = str(
        latest_counts.get("passing_policy")
        or "passes_on_any_observed_hardware"
    )
    base = {
        "schema_version": 1,
        "available": False,
        "reason": None,
        "build_number": build_number,
        "build_url": (
            _amd_test_build_url(build_number, metadata)
            if build_number is not None
            else ""
        ),
        "build_commit": build_commit or None,
        "definition_commit": None,
        "route_map_aligned": False,
        "shard_base_count": 0,
        "count_basis": count_basis,
        "passing_policy": passing_policy,
        "summary": {},
        "rows": [],
        "reconciliation": {
            "matches_latest_test_group_counts": False,
            "expected": {},
            "derived": {},
        },
        "provenance": {
            "test_results": AMD_TEST_RESULTS_GLOB,
            "metadata": SOURCE_FILES["analytics"],
            "definitions": SOURCE_FILES["config_parity"],
            "shard_bases": "shard_bases.json",
            "identity_function": "vllm.ci.analyzer._amd_runtime_group_key",
            "hardware_function": "vllm.ci.analyzer._extract_hardware",
            "state_semantics": "vllm.ci.analyzer.compute_build_summary",
        },
    }
    if latest_counts.get("available") is not True or build_number is None:
        return {**base, "reason": "latest_test_group_counts_unavailable"}

    shard_bases = _amd_test_shard_bases(data_dir)
    definition_report = definition_parity if isinstance(definition_parity, dict) else {}
    try:
        definition_commit, route_map = (
            extract_amd_runtime_group_key_map_from_report(definition_report)
        )
    except (TypeError, ValueError):
        return {
            **base,
            "reason": "invalid_definition_route_map",
            "shard_base_count": len(shard_bases),
        }
    definition_commit = str(definition_commit or "").strip().casefold()
    route_map_aligned = bool(
        re.fullmatch(r"[0-9a-f]{40}", build_commit)
        and build_commit == definition_commit
    )
    base.update({
        "definition_commit": definition_commit or None,
        "route_map_aligned": route_map_aligned,
        "shard_base_count": len(shard_bases),
    })

    previous_shard_bases = list(ci_analyzer._SHARD_BASES)
    previous_route_commit = ci_analyzer._AMD_RUNTIME_GROUP_KEY_COMMIT
    previous_route_keys = dict(ci_analyzer._AMD_RUNTIME_GROUP_KEYS)
    logical: dict[str, dict] = {}
    try:
        ci_analyzer.set_shard_bases(shard_bases)
        ci_analyzer.set_amd_runtime_group_key_map(definition_commit, route_map)
        for (row_build_number, exact_job_name), bucket in sorted(grouped.items()):
            if row_build_number != build_number:
                continue
            status_counts = {
                str(status): int(count)
                for status, count in bucket.get("status_counts", {}).items()
            }
            recognized = {
                "passed", "xpassed", "failed", "error", "canceled",
                "skipped", "xfailed",
            }
            if not recognized.intersection(status_counts):
                continue
            logical_key = ci_analyzer._amd_runtime_group_key(
                exact_job_name,
                build_commit,
            ).strip()
            if not logical_key:
                continue
            hardware = ci_analyzer._extract_hardware(exact_job_name)
            signal_value = _amd_test_signal_value(status_counts)
            observation = observations.get(exact_job_name) or {}
            display_name, _, hardware_variant, queue = (
                _amd_test_job_labels(exact_job_name)
            )
            evidence = {
                "id": _amd_test_group_id(exact_job_name),
                "exact_job_name": exact_job_name,
                "display_name": display_name,
                "hardware": hardware,
                "hardware_variant": str(
                    observation.get("hardware_variant") or hardware_variant
                ),
                "queue": str(observation.get("queue") or queue),
                "test_signal_state": _amd_test_signal_state(signal_value),
                "terminal_state": str(observation.get("state") or "unknown"),
                "status_counts": dict(sorted(status_counts.items())),
                "status_row_counts": dict(sorted(
                    (str(status), int(count))
                    for status, count in bucket.get(
                        "status_row_counts", {}
                    ).items()
                )),
                "tests": int(observation.get("tests") or 0),
                "passed_tests": int(observation.get("passed_tests") or 0),
                "failed_tests": int(observation.get("failed_tests") or 0),
                "skipped_tests": int(observation.get("skipped_tests") or 0),
                "observed_at": str(observation.get("observed_at") or ""),
                "job_url": str(observation.get("job_url") or ""),
                "build_url": str(observation.get("build_url") or ""),
            }
            for field in ("job_id", "step_id"):
                if observation.get(field):
                    evidence[field] = str(observation[field])
            entry = logical.setdefault(logical_key, {
                "hardware_states": {},
                "job_variants": [],
            })
            entry["hardware_states"][hardware] = _merge_amd_test_signal(
                entry["hardware_states"].get(hardware),
                signal_value,
            )
            entry["job_variants"].append(evidence)
    finally:
        ci_analyzer.set_shard_bases(previous_shard_bases)
        ci_analyzer.set_amd_runtime_group_key_map(
            previous_route_commit,
            previous_route_keys,
        )

    rows = []
    state_counts: Counter[str] = Counter()
    for logical_key, entry in sorted(logical.items()):
        hardware_states = entry["hardware_states"]
        values = list(hardware_states.values())
        any_pass = any(value is True for value in values)
        all_pass = all(value is True for value in values)
        state = (
            "passing_all"
            if all_pass
            else "partial"
            if any_pass
            else "non_passing"
        )
        state_counts[state] += 1
        variants = sorted(
            entry["job_variants"],
            key=lambda row: (
                str(row.get("hardware")),
                str(row.get("hardware_variant")),
                str(row.get("exact_job_name")),
            ),
        )
        rows.append({
            "id": hashlib.sha1(
                f"ci-amd-logical:{logical_key}".encode()
            ).hexdigest()[:20],
            "logical_key": logical_key,
            "label": _friendly_amd_logical_label(logical_key, variants),
            "state": state,
            "passing": any_pass,
            "hardware_count": len(hardware_states),
            "job_variant_count": len(variants),
            "hardware_states": [
                {
                    "hardware": hardware,
                    "state": _amd_test_signal_state(value),
                }
                for hardware, value in sorted(hardware_states.items())
            ],
            "job_variants": variants,
        })

    derived = {
        "total": len(rows),
        "passing": state_counts["passing_all"] + state_counts["partial"],
        "passing_all": state_counts["passing_all"],
        "partial": state_counts["partial"],
        "non_passing": state_counts["non_passing"],
    }
    expected = {
        key: int(latest_counts[key])
        for key in derived
        if latest_counts.get(key) is not None
    }
    reconciles = expected == derived
    reconciliation = {
        "matches_latest_test_group_counts": reconciles,
        "expected": expected,
        "derived": derived,
    }
    if not reconciles:
        return {
            **base,
            "reason": "logical_group_reconciliation_failed",
            "reconciliation": reconciliation,
        }

    return {
        **base,
        "available": True,
        "reason": None,
        "summary": {
            **derived,
            "job_variant_count": sum(
                row["job_variant_count"] for row in rows
            ),
            "state_counts": {
                state: state_counts[state]
                for state in ("passing_all", "partial", "non_passing")
            },
        },
        "rows": rows,
        "reconciliation": reconciliation,
    }


def _amd_test_health(
    data_dir: Path,
    amd_analytics: Any,
    amd_ci_health: Any = None,
    definition_parity: Any = None,
) -> dict:
    metadata_by_build = _amd_test_metadata_builds(amd_analytics)
    grouped, load_stats = _load_amd_test_result_groups(data_dir, metadata_by_build)
    observations_by_group: dict[str, list[dict]] = defaultdict(list)
    observations_by_build: dict[int, list[dict]] = defaultdict(list)
    for (build_number, exact_job_name), bucket in grouped.items():
        if build_number not in metadata_by_build:
            continue
        observation = _amd_test_observation(bucket, metadata_by_build.get(build_number) or {})
        observations_by_group[exact_job_name].append(observation)
        observations_by_build[build_number].append(observation)

    catalog = []
    for exact_job_name, source_observations in observations_by_group.items():
        source_observations.sort(key=_amd_test_sort_key)
        group_id = _amd_test_group_id(exact_job_name)
        observations = [
            {**row, "group_id": group_id}
            for row in source_observations[-AMD_TEST_HISTORY_LIMIT:]
        ]
        display_name, hardware, hardware_variant, queue = _amd_test_job_labels(exact_job_name)
        state_counts = Counter(row["state"] for row in source_observations)
        runs = len(source_observations)
        passed = state_counts["passed"]
        soft_failed = state_counts["soft"]
        hard_failed = state_counts["hard"]
        incidents = soft_failed + hard_failed
        unknown = state_counts["unknown"]
        latest = source_observations[-1]
        hardware = str(latest.get("hardware") or hardware)
        hardware_variant = str(latest.get("hardware_variant") or hardware_variant)
        queue = str(latest.get("queue") or queue)
        queues = sorted({
            str(observation.get("queue"))
            for observation in source_observations
            if observation.get("queue")
        })
        current_pass_streak = 0
        for observation in reversed(source_observations):
            if observation["state"] != "passed":
                break
            current_pass_streak += 1
        catalog.append({
            "source_pipeline": AMD_TEST_PIPELINE,
            "id": group_id,
            "name": display_name,
            "display_name": display_name,
            "job_name": exact_job_name,
            "exact_job_name": exact_job_name,
            "hardware": hardware,
            "hardware_variant": hardware_variant,
            "queue": queue,
            "queues": queues or ([queue] if queue else []),
            "runs": runs,
            "passed": passed,
            "soft_failed": soft_failed,
            "hard_failed": hard_failed,
            "incidents": incidents,
            "unknown": unknown,
            "pass_rate_pct": _amd_test_pass_rate(passed, incidents),
            "current_pass_streak": current_pass_streak,
            "latest_state": latest["state"],
            "latest_build_number": latest["build_number"],
            "latest_url": latest["job_url"] or latest["build_url"],
            "latest_observed_at": latest["observed_at"],
            "first_observed_at": source_observations[0]["observed_at"],
            "observation_count": runs,
            "retained_observation_count": len(observations),
            "history_truncated": runs > len(observations),
            "observations": observations,
        })
    catalog.sort(
        key=lambda row: (
            str(row["hardware"]),
            str(row["display_name"]).lower(),
            str(row["exact_job_name"]),
        )
    )

    builds = []
    for build_number, source_observations in observations_by_build.items():
        metadata = metadata_by_build.get(build_number) or {}
        source_observations.sort(key=lambda row: str(row.get("hardware_variant") or "") + row["job_url"])
        state_counts = Counter(row["state"] for row in source_observations)
        observed_at = (
            metadata.get("created_at")
            or metadata.get("finished_at")
            or max((row["observed_at"] for row in source_observations), default="")
        )
        date = str(
            metadata.get("date")
            or max((row["date"] for row in source_observations), default="")
            or str(observed_at or "")[:10]
        )
        passed = state_counts["passed"]
        soft_failed = state_counts["soft"]
        hard_failed = state_counts["hard"]
        incidents = soft_failed + hard_failed
        unknown = state_counts["unknown"]
        build_url = _amd_test_build_url(build_number, metadata)
        builds.append({
            "source_pipeline": AMD_TEST_PIPELINE,
            "number": build_number,
            "build_number": build_number,
            "date": date,
            "observed_at": str(observed_at or ""),
            "url": build_url,
            "build_url": build_url,
            "observed": len(source_observations),
            "passed": passed,
            "soft_failed": soft_failed,
            "hard_failed": hard_failed,
            "incidents": incidents,
            "unknown": unknown,
            # Explicit names for clients that need exact Buildkite job
            # variants.  Keep the historical ``*_groups`` aliases below for
            # compatibility with already-published snapshots.
            "observed_job_variants": len(source_observations),
            "passed_job_variants": passed,
            "soft_failed_job_variants": soft_failed,
            "hard_failed_job_variants": hard_failed,
            "incident_job_variants": incidents,
            "unknown_job_variants": unknown,
            "observed_groups": len(source_observations),
            "passed_groups": passed,
            "soft_failed_groups": soft_failed,
            "hard_failed_groups": hard_failed,
            "incident_groups": incidents,
            "unknown_groups": unknown,
            "pass_rate_pct": _amd_test_pass_rate(passed, incidents),
            "state_counts": {
                "passed": passed,
                "soft": soft_failed,
                "hard": hard_failed,
                "unknown": unknown,
            },
            "job_variant_state_counts": {
                "passed": passed,
                "soft": soft_failed,
                "hard": hard_failed,
                "unknown": unknown,
            },
        })
    builds.sort(key=_amd_test_sort_key)

    latest = builds[-1] if builds else {}
    latest_counts = latest.get("state_counts") or {
        "passed": 0,
        "soft": 0,
        "hard": 0,
        "unknown": 0,
    }
    observation_state_counts = Counter(
        row["state"]
        for observations in observations_by_build.values()
        for row in observations
    )
    joined_observation_count = sum(
        bool(row.get("analytics_job_count"))
        for observations in observations_by_build.values()
        for row in observations
    )
    hardware_counts = Counter(
        row["hardware"] for row in catalog if row["hardware"] != "unknown"
    )
    hardware_variant_counts = Counter(
        row["hardware_variant"] for row in catalog if row["hardware_variant"] != "unknown"
    )
    latest_hardware_counts = Counter(
        row["hardware"]
        for row in observations_by_build.get(latest.get("build_number"), [])
        if row["hardware"] != "unknown"
    )
    latest_test_group_counts = _latest_amd_test_group_counts(
        latest,
        amd_ci_health,
    )
    latest_build_number = _strict_int(latest.get("build_number"))
    latest_observations = {
        exact_job_name: observation
        for exact_job_name, source_observations in observations_by_group.items()
        for observation in source_observations
        if observation.get("build_number") == latest_build_number
    }
    latest_logical_test_groups = _latest_logical_amd_test_groups(
        data_dir,
        latest_test_group_counts,
        metadata_by_build.get(latest_build_number) or {},
        grouped,
        latest_observations,
        definition_parity,
    )
    summary = {
        "build_count": len(builds),
        # This is the historical union of exact Buildkite job names, not the
        # denominator for the latest nightly. Keep the older aliases for
        # compatibility, but give new clients an unambiguous field name.
        "retained_group_count": len(catalog),
        "group_count": len(catalog),
        "union_group_count": len(catalog),
        "retained_job_variant_count": len(catalog),
        "latest_group_count": int(latest.get("observed") or 0),
        "latest_job_variant_count": int(latest.get("observed") or 0),
        "latest_build_number": latest.get("build_number"),
        "latest_build_url": latest.get("build_url"),
        "latest_url": latest.get("url"),
        "latest_observed_at": latest.get("observed_at"),
        "latest_state_counts": latest_counts,
        "latest_job_variant_state_counts": latest_counts,
        "latest_passed_group_count": int(latest_counts.get("passed") or 0),
        "latest_soft_failed_group_count": int(latest_counts.get("soft") or 0),
        "latest_hard_failed_group_count": int(latest_counts.get("hard") or 0),
        "latest_incident_group_count": int(latest_counts.get("soft") or 0)
        + int(latest_counts.get("hard") or 0),
        "latest_unknown_group_count": int(latest_counts.get("unknown") or 0),
        "latest_passed_job_variant_count": int(latest_counts.get("passed") or 0),
        "latest_soft_failed_job_variant_count": int(
            latest_counts.get("soft") or 0
        ),
        "latest_hard_failed_job_variant_count": int(
            latest_counts.get("hard") or 0
        ),
        "latest_incident_job_variant_count": int(latest_counts.get("soft") or 0)
        + int(latest_counts.get("hard") or 0),
        "latest_unknown_job_variant_count": int(
            latest_counts.get("unknown") or 0
        ),
        "latest_test_group_counts": latest_test_group_counts,
        "observation_state_counts": {
            "passed": observation_state_counts["passed"],
            "soft": observation_state_counts["soft"],
            "hard": observation_state_counts["hard"],
            "unknown": observation_state_counts["unknown"],
        },
        "passed_observation_count": observation_state_counts["passed"],
        "soft_failed_observation_count": observation_state_counts["soft"],
        "hard_failed_observation_count": observation_state_counts["hard"],
        "incident_observation_count": observation_state_counts["soft"]
        + observation_state_counts["hard"],
        "unknown_observation_count": observation_state_counts["unknown"],
        "mixed_outcome_group_count": sum(
            bool(row["passed"] and row["incidents"]) for row in catalog
        ),
        "stable_passing_group_count": sum(
            bool(row["passed"] and not row["incidents"] and not row["unknown"])
            for row in catalog
        ),
        "persistent_incident_group_count": sum(
            bool(row["incidents"] and not row["passed"]) for row in catalog
        ),
        "hardware_counts": dict(sorted(hardware_counts.items())),
        "hardware_variant_counts": dict(sorted(hardware_variant_counts.items())),
        "latest_hardware_counts": dict(sorted(latest_hardware_counts.items())),
    }
    return {
        "available": bool(builds),
        "source_pipeline": AMD_TEST_PIPELINE,
        "job_scope": "amd_gpu",
        "hardware_scope": "amd_mi_gpu",
        "cohort": {
            "id": "ci-amd-retained-nightly-test-results",
            "available": bool(builds),
            "pipeline": AMD_TEST_PIPELINE,
            "label": "Retained main CI nightly AMD GPU parsed test results",
            "build_count": len(builds),
            "build_numbers": [row["build_number"] for row in builds],
            "first_observed_at": builds[0]["observed_at"] if builds else None,
            "latest_observed_at": latest.get("observed_at"),
            "history_limit_per_group": AMD_TEST_HISTORY_LIMIT,
            "aggregation_key": ["build_number", "exact_job_name"],
        },
        "summary": summary,
        "latest_logical_test_groups": latest_logical_test_groups,
        "builds": builds,
        "group_catalog": catalog,
        "provenance": {
            "source_paths": {
                "test_results": AMD_TEST_RESULTS_GLOB,
                "nightly_metadata": SOURCE_FILES["analytics"],
                "logical_test_groups": SOURCE_FILES["ci_health"],
            },
            "test_results": {
                "glob": AMD_TEST_RESULTS_GLOB,
                "role": "parsed test counts, statuses, and duration only",
                **load_stats,
            },
            "nightly_metadata": {
                "path": SOURCE_FILES["analytics"],
                "source_key": "ci.builds",
                "retained_build_count": len(metadata_by_build),
                "job_join_key": ["build_number", "job_id"],
                "joined_group_observations": joined_observation_count,
                "unjoined_group_observations": sum(len(rows) for rows in observations_by_build.values())
                - joined_observation_count,
                "role": "authoritative Buildkite terminal job outcome and timing",
            },
            "logical_test_groups": {
                "path": SOURCE_FILES["ci_health"],
                "source_key": "amd.latest_test_signal_build",
                "joined": bool(latest_test_group_counts.get("available")),
                "join_key": "build_number",
                "reason": latest_test_group_counts.get("reason"),
                "role": (
                    "same-build normalized logical test-group totals and "
                    "any-hardware passing counts"
                ),
            },
            "classification": {
                "passed": "analytics job state is passed",
                "soft": "analytics job state is soft_fail/soft_failed or soft_failed is true",
                "hard": "analytics job state is failed, timed_out, broken, or canceled",
                "unknown": "analytics job state is missing, skipped, or non-terminal",
                "incidents": "soft plus hard group observations",
                "jsonl_status_role": "test-count enrichment only; never the terminal group outcome",
                "missing_groups": "not inferred",
            },
            "identity": {
                "algorithm": "sha1",
                "length": 20,
                "input": "source_pipeline + ':' + exact_job_name",
            },
        },
    }


def _strict_build_rows(rows: Any, pipeline_slug: str) -> tuple[bool, set[int]]:
    if not isinstance(rows, list):
        return False, set()
    build_numbers: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            return False, set()
        number = _strict_int(row.get("number"))
        if (
            number is None
            or row.get("branch") != "main"
            or str(row.get("state") or "").lower() not in TRUSTWORTHY_BUILD_STATES
            or not row.get("finished_at")
            or not _pipeline_build_url_matches(
                row.get("url") or row.get("web_url"),
                pipeline_slug,
                number,
            )
            or number in build_numbers
        ):
            return False, set()
        build_numbers.add(number)
    return True, build_numbers


def _collector_main_is_strict(payload: Any, pipeline_slug: str) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("hardware_scope") != "amd_mi_gpu":
        return False
    cohort = payload.get("cohort")
    provenance = payload.get("provenance")
    builds = payload.get("builds")
    groups = payload.get("groups")
    if not isinstance(cohort, dict) or not isinstance(provenance, dict):
        return False
    if not isinstance(groups, list):
        return False
    query = provenance.get("query")
    collection = provenance.get("collection")
    build_states = cohort.get("build_states")
    strict_builds, build_numbers = _strict_build_rows(builds, pipeline_slug)
    if (
        not strict_builds
        or not isinstance(build_states, list)
        or any(not isinstance(state, str) for state in build_states)
        or set(build_states) != TRUSTWORTHY_BUILD_STATES
        or _strict_int(cohort.get("build_count")) != len(builds)
        or cohort.get("id") != f"{pipeline_slug}-main-completed-pass-fail"
        or cohort.get("pipeline") != pipeline_slug
        or cohort.get("branch") != "main"
        or cohort.get("exhaustive") is not True
        or provenance.get("pipeline") != pipeline_slug
        or not str(provenance.get("endpoint") or "").endswith(
            f"/pipelines/{pipeline_slug}/builds"
        )
        or not isinstance(query, dict)
        or query.get("branch") != "main"
        or not isinstance(collection, dict)
        or collection.get("exhaustive") is not True
    ):
        return False
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("observations"), list):
            return False
        if not is_amd_ci_job(group) or not re.fullmatch(r"mi\d{3,4}", str(group.get("hardware") or "").lower()):
            return False
        numeric_fields = (
            "denominator", "passed", "failed", "soft_failed",
            "excluded_observations", "retry_evidence_observations",
        )
        if any(
            isinstance(group.get(field), bool)
            or not isinstance(group.get(field), int)
            or group.get(field) < 0
            for field in numeric_fields
        ):
            return False
        if not isinstance(group.get("duration"), dict):
            return False
        observations = group["observations"]
        if payload.get("schema_version") == 2:
            try:
                observations = hydrate_reliability_observations(
                    payload,
                    observations,
                    pipeline_slug=pipeline_slug,
                )
            except (KeyError, TypeError, ValueError):
                return False
        for observation in observations:
            if not isinstance(observation, dict):
                return False
            number = _strict_int(observation.get("build_number"))
            if (
                number not in build_numbers
                or observation.get("source_pipeline") != pipeline_slug
                or not _pipeline_build_url_matches(
                    observation.get("build_url"), pipeline_slug, number
                )
                or not _pipeline_job_url_matches(
                    observation.get("job_url"), pipeline_slug, number
                )
                or (
                    observation.get("step_url")
                    and not _pipeline_job_url_matches(
                        observation.get("step_url"), pipeline_slug, number
                    )
                )
            ):
                return False
    return True


def _build_kind(build: dict) -> str:
    explicit = str(build.get("build_kind") or build.get("kind") or "").lower()
    if explicit:
        return explicit
    message = str(build.get("message") or "").lower()
    return "nightly" if "nightly" in message else "main"


def _historical_observation(
    build: dict,
    job: dict,
    group_id: str = "",
    pipeline_slug: str = "ci",
) -> dict:
    state = _historical_state(job)
    row = {
        "source_pipeline": pipeline_slug,
        "build_number": build.get("number") or build.get("build_number"),
        "build_url": _build_url(pipeline_slug, build),
        "build_kind": _build_kind(build),
        "state": state,
        "observed_at": (
            job.get("finished_at")
            or job.get("started_at")
            or build.get("finished_at")
            or build.get("created_at")
            or build.get("date")
            or ""
        ),
    }
    if group_id:
        row["group_id"] = group_id
    for key, source in (
        ("commit", build.get("commit") or build.get("commit_sha")),
        ("message", build.get("message")),
        ("raw_name", job.get("raw_name")),
        ("step_key", job.get("step_key")),
        ("queue", job.get("q") or job.get("queue")),
    ):
        if source not in (None, ""):
            row[key] = source
    if job.get("url") or job.get("web_url") or job.get("job_id") or job.get("step_id"):
        row["job_url"] = _job_url(pipeline_slug, build, job)

    wall = _number(
        job.get("wall_duration_mins")
        if job.get("wall_duration_mins") is not None
        else job.get("wall_mins")
    )
    test = _number(
        job.get("test_duration_mins")
        if job.get("test_duration_mins") is not None
        else job.get("reported_duration_mins")
    )
    if test is None:
        test = _number(job.get("dur"))
    wait = _number(job.get("wait_mins") if job.get("wait_mins") is not None else job.get("wait"))
    end_to_end = _number(job.get("end_to_end_mins"))
    if wall is not None:
        row["wall_duration_mins"] = round(wall, 2)
    if test is not None:
        row["test_duration_mins"] = round(test, 2)
    if wait is not None:
        row["wait_mins"] = round(wait, 2)
    if end_to_end is not None:
        row["end_to_end_mins"] = round(end_to_end, 2)
    preferred = wall if wall is not None else test
    if preferred is not None:
        row["duration_mins"] = round(preferred, 2)
        row["duration_basis"] = "job_wall" if wall is not None else "test_reported"

    for key in ("tests", "passed_tests", "failed_tests", "skipped_tests"):
        if isinstance(job.get(key), (int, float)):
            row[key] = job[key]
    retry_evidence = _retry_evidence(job)
    if retry_evidence:
        row["retry_evidence"] = retry_evidence
    return row


def _group_id(job: dict, label: str) -> str:
    explicit = job.get("group_id") or job.get("canonical_group_id")
    if explicit:
        return str(explicit)
    queue = str(job.get("q") or job.get("queue") or "")
    hardware = _resolved_hardware(job, queue)
    identity = {
        "label": _strict_group_label(label),
        "hardware": hardware,
        "queue": queue,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return f"legacy-{hashlib.sha256(encoded).hexdigest()[:20]}"


def _hardware_from_queue(queue: Any) -> str:
    value = str(queue or "")
    match = re.match(r"^amd_(mi\d{3,4}b?)(?:_|$)", value, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _resolved_hardware(job: dict, queue: Any) -> str:
    explicit = str(job.get("hardware") or "").strip().lower()
    queue_hardware = _hardware_from_queue(queue)
    if explicit in {"", "unknown"} and queue_hardware:
        return queue_hardware
    return explicit or queue_hardware or "unknown"


def _streak(observations: list[dict], build_kind: str | None = None) -> int:
    count = 0
    seen_builds: set[Any] = set()
    for row in observations:
        if build_kind and row.get("build_kind") != build_kind:
            continue
        build_number = row.get("build_number")
        if build_number in seen_builds:
            continue
        seen_builds.add(build_number)
        if row.get("state") != "passed":
            break
        count += 1
    return count


def _group_catalog(
    builds: list[dict],
    pipeline_slug: str = "ci",
) -> tuple[list[dict], dict]:
    groups: dict[str, dict] = {}
    unknown_observations = 0
    source_builds = sorted(
        builds,
        key=lambda build: str(build.get("created_at") or build.get("date") or ""),
        reverse=True,
    )
    for build in source_builds:
        for job in build.get("jobs") or []:
            queue = job.get("q") or job.get("queue")
            if not is_amd_ci_job(job) or _is_excluded_queue(queue):
                continue
            label = _strict_group_label(job.get("name") or _group_identity(job))
            if not label:
                continue
            group_id = _group_id(job, label)
            state = _historical_state(job)
            if state == "unknown":
                unknown_observations += 1
                continue
            row = groups.setdefault(group_id, {
                "id": group_id,
                "name": label,
                "group_ids": set(),
                "raw_names": set(),
                "hardware": set(),
                "queues": set(),
                "builds": set(),
                "passed": 0,
                "failed": 0,
                "soft_failed": 0,
                "wall": [],
                "test": [],
                "wait": [],
                "end_to_end": [],
                "linked": 0,
                "retry_evidence": 0,
                "observations": [],
            })
            raw_name = str(job.get("raw_name") or "")
            row["group_ids"].add(group_id)
            if raw_name:
                row["raw_names"].add(raw_name)
            if queue:
                row["queues"].add(str(queue))
            hardware = _resolved_hardware(job, queue)
            if hardware:
                row["hardware"].add(hardware)
            row["builds"].add(build.get("number") or build.get("build_number"))
            if state == "passed":
                row["passed"] += 1
            elif state == "soft":
                row["soft_failed"] += 1
            else:
                row["failed"] += 1
            observation = _historical_observation(
                build,
                job,
                group_id,
                pipeline_slug=pipeline_slug,
            )
            if observation.get("job_url"):
                row["linked"] += 1
            if observation.get("retry_evidence"):
                row["retry_evidence"] += 1
            for source, key in (
                ("wall_duration_mins", "wall"),
                ("test_duration_mins", "test"),
                ("wait_mins", "wait"),
                ("end_to_end_mins", "end_to_end"),
            ):
                value = _number(observation.get(source))
                if value is not None:
                    row[key].append(value)
            if len(row["observations"]) < GROUP_HISTORY_LIMIT:
                row["observations"].append(observation)

    catalog = []
    terminal_observations = 0
    linked_observations = 0
    for row in groups.values():
        runs = row["passed"] + row["failed"] + row["soft_failed"]
        incidents = row["failed"] + row["soft_failed"]
        terminal_observations += runs
        linked_observations += row["linked"]
        observations = row["observations"]
        latest = observations[0] if observations else {}
        last_incident = next((item for item in observations if item.get("state") in {"hard", "soft"}), None)
        wall = sorted(row["wall"])
        test = sorted(row["test"])
        wait = sorted(row["wait"])
        end_to_end = sorted(row["end_to_end"])
        preferred = wall or test
        catalog.append({
            "source_pipeline": pipeline_slug,
            "id": row["id"],
            "group_ids": sorted(row["group_ids"]),
            "name": row["name"],
            "raw_names": sorted(row["raw_names"]),
            "hardware": (
                sorted(row["hardware"])[0]
                if len(row["hardware"]) == 1
                else ("mixed" if row["hardware"] else "unknown")
            ),
            "queues": sorted(row["queues"]),
            "build_count": len(row["builds"] - {None}),
            "runs": runs,
            "passed": row["passed"],
            "failed": row["failed"],
            "soft_failed": row["soft_failed"],
            "incident_count": incidents,
            "incident_rate_pct": round(incidents / runs * 100, 1) if runs else 0.0,
            "fail_rate": round(incidents / runs * 100, 1) if runs else 0.0,
            "mixed_outcomes": bool(row["passed"] and incidents),
            "latest_state": latest.get("state") or "unknown",
            "latest_observed_at": latest.get("observed_at"),
            "latest_url": latest.get("job_url") or latest.get("build_url"),
            "last_incident": last_incident,
            "green_streak": _streak(observations),
            "nightly_green_streak": _streak(observations, "nightly"),
            "median_wall_mins": round(median(wall), 1) if wall else None,
            "p90_wall_mins": _percentile(wall, 90),
            "max_wall_mins": round(max(wall), 1) if wall else None,
            "median_test_mins": round(median(test), 1) if test else None,
            "p90_test_mins": _percentile(test, 90),
            "max_test_mins": round(max(test), 1) if test else None,
            "median_wait_mins": round(median(wait), 1) if wait else None,
            "p90_wait_mins": _percentile(wait, 90),
            "max_wait_mins": round(max(wait), 1) if wait else None,
            "median_end_to_end_mins": round(median(end_to_end), 1) if end_to_end else None,
            "p90_end_to_end_mins": _percentile(end_to_end, 90),
            "max_end_to_end_mins": round(max(end_to_end), 1) if end_to_end else None,
            "median_dur": round(median(preferred), 1) if preferred else None,
            "p90_dur": _percentile(preferred, 90),
            "max_dur": round(max(preferred), 1) if preferred else None,
            "duration_basis": "job_wall" if wall else ("test_reported" if test else "unavailable"),
            "observation_count": runs,
            "retained_observation_count": len(observations),
            "history_truncated": runs > len(observations),
            "linked_observation_count": row["linked"],
            "retry_evidence_observation_count": row["retry_evidence"],
            "evidence_type": "mixed_outcome_history" if row["passed"] and incidents else "terminal_history",
            "observations": observations,
        })
    catalog.sort(key=lambda row: (str(row["name"]).lower(), str(row["id"])))
    return catalog, {
        "builds": len(source_builds),
        "terminal_observations": terminal_observations,
        "linked_observations": linked_observations,
        "unknown_observations_excluded": unknown_observations,
    }


def _collector_main_catalog(
    payload: dict,
    pipeline_slug: str = "ci",
) -> tuple[list[dict], dict, dict]:
    """Adapt the collector's strict all-main variant catalog for the UI contract."""
    build_kind = {
        row.get("number"): "nightly" if row.get("is_canonical_nightly") else "main"
        for row in payload.get("builds") or []
    }
    catalog = []
    retry_attempts = []
    recoveries = []
    for source in payload.get("groups") or []:
        if not is_amd_ci_job(source) or _is_excluded_queue(source.get("queue")):
            continue
        source_observations = [
            row
            for row in source.get("observations") or []
            if isinstance(row, dict)
        ]
        if payload.get("schema_version") == 2:
            source_observations = hydrate_reliability_observations(
                payload,
                source_observations,
                pipeline_slug=pipeline_slug,
            )
        observations = []
        by_job_id = {
            str(row.get("job_id")): row
            for row in source_observations
            if row.get("job_id")
        }
        for raw in source_observations:
            if not raw.get("eligible_for_reliability"):
                continue
            result = str(raw.get("result") or "")
            state = "soft" if result == "soft_fail" else ("hard" if result == "failed" else result)
            build_number = raw.get("build_number")
            build_url = raw.get("build_url") or _build_url(
                pipeline_slug,
                {"number": build_number},
            )
            job_url = raw.get("job_url") or ""
            if not job_url and (raw.get("job_id") or raw.get("step_id")):
                job_url = _job_url(
                    pipeline_slug,
                    {"number": build_number, "web_url": build_url},
                    {"job_id": raw.get("job_id"), "step_id": raw.get("step_id")},
                )
            row = {
                "source_pipeline": pipeline_slug,
                "group_id": source.get("group_id"),
                "build_number": build_number,
                "build_url": build_url,
                "build_kind": build_kind.get(build_number, "main"),
                "commit": raw.get("build_commit") or "",
                "message": raw.get("build_message") or "",
                "state": state,
                "terminal_state": raw.get("terminal_state") or "",
                "observed_at": raw.get("observed_at") or "",
                "job_url": job_url,
                "step_url": raw.get("step_url") or "",
                "job_id": raw.get("job_id") or "",
                "step_id": raw.get("step_id") or "",
                "queue": source.get("queue") or "",
                "wall_duration_mins": raw.get("wall_completion_mins"),
                "test_duration_mins": raw.get("test_duration_mins"),
                "wait_mins": raw.get("queue_wait_mins"),
                "end_to_end_mins": raw.get("end_to_end_mins"),
                "duration_basis": "job_wall" if raw.get("wall_completion_mins") is not None else (
                    "test_reported" if raw.get("test_duration_mins") is not None else "unavailable"
                ),
            }
            preferred = raw.get("wall_completion_mins")
            if preferred is None:
                preferred = raw.get("test_duration_mins")
            if preferred is not None:
                row["duration_mins"] = preferred
            for key in ("tests", "passed_tests", "failed_tests", "skipped_tests"):
                if isinstance(raw.get(key), (int, float)) and not isinstance(raw.get(key), bool):
                    row[key] = raw[key]
            retry = raw.get("retry_evidence") or {}
            if retry:
                row["retry_evidence"] = retry
                attempt = {
                    "source_pipeline": pipeline_slug,
                    "group_id": source.get("group_id"),
                    "name": source.get("name"),
                    "build_number": build_number,
                    "build_url": build_url,
                    "job_id": raw.get("job_id"),
                    "job_url": job_url,
                    "result": result,
                    "retry_evidence": retry,
                }
                retry_attempts.append(attempt)
                retried_in = str(retry.get("retried_in_job_id") or "")
                recovered = by_job_id.get(retried_in) if retried_in else None
                if recovered and recovered.get("result") == "passed":
                    recovered_job_url = recovered.get("job_url") or ""
                    if not recovered_job_url and (recovered.get("job_id") or recovered.get("step_id")):
                        recovered_job_url = _job_url(
                            pipeline_slug,
                            {"number": build_number, "web_url": build_url},
                            {
                                "job_id": recovered.get("job_id"),
                                "step_id": recovered.get("step_id"),
                            },
                        )
                    recoveries.append({
                        **attempt,
                        "failed_job_id": raw.get("job_id"),
                        "passed_job_id": recovered.get("job_id"),
                        "failed_job_url": job_url,
                        "passed_job_url": recovered_job_url,
                    })
            observations.append({key: value for key, value in row.items() if value not in (None, "")})

        observations.sort(
            key=lambda row: (str(row.get("observed_at") or ""), int(row.get("build_number") or 0)),
            reverse=True,
        )
        runs = int(source.get("denominator") or 0)
        passed = int(source.get("passed") or 0)
        failed = int(source.get("failed") or 0)
        soft_failed = int(source.get("soft_failed") or 0)
        incidents = failed + soft_failed
        latest = observations[0] if observations else {}
        last_incident = next((row for row in observations if row.get("state") in {"hard", "soft"}), None)
        duration = source.get("duration") or {}
        wall = duration.get("wall_completion") or {}
        test = duration.get("test_reported") or {}
        wait = duration.get("queue_wait") or {}
        end_to_end = duration.get("end_to_end") or {}
        preferred = wall if wall.get("samples") else test
        group_ids = sorted({
            str(group_id)
            for group_id in (source.get("group_ids") or [source.get("group_id")])
            if group_id
        })
        catalog.append({
            "source_pipeline": pipeline_slug,
            "id": source.get("group_id"),
            "group_ids": group_ids,
            "name": source.get("name") or source.get("raw_name") or "Unknown group",
            "raw_names": [source.get("raw_name")] if source.get("raw_name") else [],
            "step_key": source.get("step_key") or "",
            "hardware": _resolved_hardware(source, source.get("queue")),
            "queues": [source.get("queue")] if source.get("queue") else [],
            "build_count": len({row.get("build_number") for row in observations if row.get("build_number")}),
            "runs": runs,
            "passed": passed,
            "failed": failed,
            "soft_failed": soft_failed,
            "incident_count": incidents,
            "incident_rate_pct": source.get("incident_rate"),
            "fail_rate": source.get("incident_rate"),
            "mixed_outcomes": bool(passed and incidents),
            "latest_state": latest.get("state") or "unknown",
            "latest_observed_at": latest.get("observed_at"),
            "latest_url": latest.get("job_url") or latest.get("build_url"),
            "last_incident": last_incident,
            "green_streak": _streak(observations),
            "nightly_green_streak": _streak(observations, "nightly"),
            "median_wall_mins": wall.get("p50_mins"),
            "p90_wall_mins": wall.get("p90_mins"),
            "max_wall_mins": wall.get("max_mins"),
            "median_test_mins": test.get("p50_mins"),
            "p90_test_mins": test.get("p90_mins"),
            "max_test_mins": test.get("max_mins"),
            "median_wait_mins": wait.get("p50_mins"),
            "p90_wait_mins": wait.get("p90_mins"),
            "max_wait_mins": wait.get("max_mins"),
            "median_end_to_end_mins": end_to_end.get("p50_mins"),
            "p90_end_to_end_mins": end_to_end.get("p90_mins"),
            "max_end_to_end_mins": end_to_end.get("max_mins"),
            "median_dur": preferred.get("p50_mins"),
            "p90_dur": preferred.get("p90_mins"),
            "max_dur": preferred.get("max_mins"),
            "duration_basis": "job_wall" if wall.get("samples") else (
                "test_reported" if test.get("samples") else "unavailable"
            ),
            "observation_count": runs,
            "retained_observation_count": len(observations),
            "history_truncated": bool(source.get("observations_truncated")),
            "excluded_observation_count": int(source.get("excluded_observations") or 0),
            "linked_observation_count": sum(bool(row.get("job_url")) for row in observations),
            "retry_evidence_observation_count": int(source.get("retry_evidence_observations") or 0),
            "evidence_type": "mixed_outcome_history" if passed and incidents else "terminal_history",
            "observations": observations,
        })
    catalog.sort(key=lambda row: (str(row.get("name") or "").lower(), str(row.get("id") or "")))
    source_denominator = payload.get("denominator") or {}
    counts = {
        "builds": int(((payload.get("cohort") or {}).get("build_count")) or len(payload.get("builds") or [])),
        "terminal_observations": int(source_denominator.get("eligible_observations") or 0),
        "linked_observations": sum(row["linked_observation_count"] for row in catalog),
        "unknown_observations_excluded": int(source_denominator.get("excluded_observations") or 0),
    }
    retry_summary = payload.get("summary") or {}
    retry_analysis = {
        "summary": {
            "builds_evaluated": counts["builds"],
            "builds_with_retries": len({row.get("build_number") for row in retry_attempts}),
            "retry_attempt_count": int(retry_summary.get("retry_evidence_observations") or len(retry_attempts)),
            "failed_then_passed_recovery_count": len(recoveries),
        },
        "retry_attempts": retry_attempts,
        "failed_then_passed_recoveries": recoveries,
        "evidence_type": "explicit_retry_recovery",
    }
    return catalog, counts, retry_analysis


def _normalize_retry_analysis(
    source: Any,
    cohort_build_numbers: set[int],
    pipeline_slug: str = "ci",
    catalog: list[dict] | None = None,
    build_observed_at: dict[int, str] | None = None,
) -> dict:
    """Retain only explicit retry records that belong to the strict cohort."""
    selected = source if isinstance(source, dict) else {}
    source_provenance = selected.get("provenance")
    source_provenance = source_provenance if isinstance(source_provenance, dict) else {}
    if (
        selected.get("available") is not True
        or source_provenance.get("source_pipeline") != pipeline_slug
        or source_provenance.get("complete") is not True
    ):
        return {
            "available": False,
            "summary": {
                "builds_evaluated": len(cohort_build_numbers),
                "builds_with_retries": 0,
                "retry_attempt_count": 0,
                "failed_then_passed_recovery_count": 0,
                "linked_retry_attempt_count": 0,
                "linked_recovery_count": 0,
            },
            "retry_attempts": [],
            "failed_then_passed_recoveries": [],
            "evidence_type": "explicit_retry_recovery",
            "provenance": {
                "source_path": SOURCE_FILES["analytics"],
                "source_key": f"{pipeline_slug}.main_retry_analysis",
                "source_pipeline": pipeline_slug,
                "complete": False,
                "reason": source_provenance.get("reason") or (
                    "Complete explicit retry metadata is unavailable; retained group history was not substituted."
                ),
                "cohort_build_numbers": sorted(cohort_build_numbers),
            },
        }
    evidence_by_job: dict[str, dict] = {}
    for group in catalog or []:
        for observation in group.get("observations") or []:
            job_id = str(observation.get("job_id") or "")
            if not job_id:
                continue
            evidence_by_job[job_id] = {
                "observed_at": observation.get("observed_at"),
                "group_id": group.get("id"),
            }
    build_observed_at = build_observed_at or {}

    attempts = []
    for value in selected.get("retry_attempts") or []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        build_number = _strict_int(row.get("build_number"))
        job_url = row.get("job_url") or row.get("url") or ""
        if (
            build_number not in cohort_build_numbers
            or row.get("source_pipeline") not in (None, "", pipeline_slug)
            or not _pipeline_job_url_matches(job_url, pipeline_slug, build_number)
        ):
            continue
        row["build_url"] = _build_url(pipeline_slug, {"number": build_number})
        row["job_url"] = job_url
        row["url"] = job_url
        row["source_pipeline"] = pipeline_slug
        evidence = evidence_by_job.get(str(row.get("job_id") or "")) or {}
        if not row.get("observed_at"):
            if evidence.get("observed_at"):
                row["observed_at"] = evidence["observed_at"]
                row["timestamp_source"] = "terminal_job"
            elif build_observed_at.get(build_number):
                row["observed_at"] = build_observed_at[build_number]
                row["timestamp_source"] = "completed_build"
        if not row.get("group_id") and evidence.get("group_id"):
            row["group_id"] = evidence["group_id"]
        attempts.append(row)

    recoveries = []
    for value in selected.get("failed_then_passed_recoveries") or []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        build_number = _strict_int(row.get("build_number"))
        failed_url = row.get("failed_url") or row.get("failed_job_url") or ""
        passed_url = row.get("passed_url") or row.get("passed_job_url") or ""
        if (
            build_number not in cohort_build_numbers
            or row.get("source_pipeline") not in (None, "", pipeline_slug)
            or not _pipeline_job_url_matches(failed_url, pipeline_slug, build_number)
            or not _pipeline_job_url_matches(passed_url, pipeline_slug, build_number)
        ):
            continue
        row["build_url"] = _build_url(pipeline_slug, {"number": build_number})
        row["failed_url"] = failed_url
        row["passed_url"] = passed_url
        row["source_pipeline"] = pipeline_slug
        evidence = (
            evidence_by_job.get(str(row.get("passed_job_id") or ""))
            or evidence_by_job.get(str(row.get("failed_job_id") or ""))
            or {}
        )
        if not row.get("observed_at"):
            if evidence.get("observed_at"):
                row["observed_at"] = evidence["observed_at"]
                row["timestamp_source"] = "terminal_job"
            elif build_observed_at.get(build_number):
                row["observed_at"] = build_observed_at[build_number]
                row["timestamp_source"] = "completed_build"
        if not row.get("group_id") and evidence.get("group_id"):
            row["group_id"] = evidence["group_id"]
        recoveries.append(row)

    summary = dict(selected.get("summary") or {})
    summary.setdefault("builds_evaluated", 0)
    summary["retry_attempt_count"] = len(attempts)
    summary["failed_then_passed_recovery_count"] = len(recoveries)
    summary.setdefault(
        "builds_with_retries",
        len({row.get("build_number") for row in attempts if row.get("build_number")}),
    )
    summary["linked_retry_attempt_count"] = sum(bool(row.get("job_url")) for row in attempts)
    summary["linked_recovery_count"] = sum(
        bool(row.get("failed_url") and row.get("passed_url")) for row in recoveries
    )
    return {
        **selected,
        "available": True,
        "summary": summary,
        "retry_attempts": attempts,
        "failed_then_passed_recoveries": recoveries,
        "evidence_type": "explicit_retry_recovery",
        "provenance": {
            "source_path": SOURCE_FILES["analytics"],
            "source_key": f"{pipeline_slug}.main_retry_analysis",
            "source_pipeline": pipeline_slug,
            "complete": True,
            "cohort_build_numbers": sorted(cohort_build_numbers),
            "evidence_kind": "explicit Buildkite retry metadata retained by the collector",
        },
    }


def _cohort_composition(payload: dict, counts: dict, provenance: dict) -> dict:
    source = payload.get("cohort") or provenance.get("cohort") or provenance
    builds = payload.get("builds") or []
    total = int(source.get("build_count") or counts.get("builds") or len(builds))
    nightlies = source.get("canonical_nightly_build_count")
    if nightlies is None:
        nightlies = sum(bool(row.get("is_canonical_nightly")) for row in builds)
    nightlies = int(nightlies or 0)
    other_main = source.get("non_nightly_main_build_count")
    if other_main is None:
        other_main = max(0, total - nightlies)
    other_main = int(other_main or 0)
    return {
        "build_count": total,
        "canonical_nightly_build_count": nightlies,
        "non_nightly_main_build_count": other_main,
        "other_main_build_count": other_main,
        "composition": {
            "all_main_builds": total,
            "canonical_nightlies": nightlies,
            "other_main_builds": other_main,
        },
        "window_days": source.get("window_days"),
    }


def _reliability(pipeline_analytics: Any, pipeline_slug: str = "ci") -> dict:
    pipeline_analytics = pipeline_analytics if isinstance(pipeline_analytics, dict) else {}
    collector_payload = pipeline_analytics.get("all_main_reliability") or {}
    strict_available = False
    collector_present = isinstance(collector_payload, dict) and bool(collector_payload)
    if collector_present and _collector_main_is_strict(collector_payload, pipeline_slug):
        strict_available = True
        catalog, counts, _derived_retry_analysis = _collector_main_catalog(
            collector_payload,
            pipeline_slug=pipeline_slug,
        )
        retry_source = pipeline_analytics.get("main_retry_analysis") or {}
        cohort_provenance = {
            "cohort": collector_payload.get("cohort") or {},
            "denominator": collector_payload.get("denominator") or {},
            "provenance": collector_payload.get("provenance") or {},
        }
    else:
        catalog, counts = _group_catalog([], pipeline_slug=pipeline_slug)
        retry_source = {}
        cohort_provenance = {
            "unavailable": True,
            "invalid_collector_cohort": collector_present,
            "note": (
                "Collector all-main payload failed strict exhaustive pipeline, branch, state, cohort, or URL validation."
                if collector_present
                else "Collector did not expose an exhaustive strict all-main cohort; nightly data was not substituted."
            ),
        }
    def summary(row: dict) -> dict:
        return {
            key: value
            for key, value in row.items()
            if key not in {"observations", "raw_names", "last_incident"}
        } | {
            "evidence_ref": row["id"],
            "last_incident": row.get("last_incident"),
        }

    candidates = [summary(row) for row in catalog if row["mixed_outcomes"]]
    candidates.sort(
        key=lambda row: (row["incident_rate_pct"], row["incident_count"], row["runs"], row["name"]),
        reverse=True,
    )
    latency = [summary(row) for row in catalog if row.get("median_dur") is not None]
    by_median = sorted(latency, key=lambda row: (float(row.get("median_dur") or 0), row["name"]), reverse=True)
    by_p90 = sorted(latency, key=lambda row: (float(row.get("p90_dur") or 0), row["name"]), reverse=True)
    by_max = sorted(latency, key=lambda row: (float(row.get("max_dur") or 0), row["name"]), reverse=True)
    cohort_build_numbers = {
        number
        for row in (collector_payload.get("builds") or [])
        if isinstance(row, dict) and (number := _strict_int(row.get("number"))) is not None
    } if strict_available else set()
    cohort_build_observed_at = {
        number: str(
            row.get("finished_at")
            or row.get("started_at")
            or row.get("created_at")
            or ""
        )
        for row in (collector_payload.get("builds") or [])
        if isinstance(row, dict)
        and (number := _strict_int(row.get("number"))) is not None
    } if strict_available else {}
    retry_analysis = _normalize_retry_analysis(
        retry_source,
        cohort_build_numbers,
        pipeline_slug=pipeline_slug,
        catalog=catalog,
        build_observed_at=cohort_build_observed_at,
    )
    composition = _cohort_composition(
        collector_payload if strict_available else {},
        counts,
        cohort_provenance,
    )
    return {
        "available": strict_available,
        "source_pipeline": pipeline_slug,
        "job_scope": "amd_gpu",
        "hardware_scope": "amd_mi_gpu",
        "cohort": {
            "id": "main",
            "available": strict_available,
            "label": (
                f"All completed {pipeline_slug} branch=main builds"
                if strict_available
                else f"Strict {pipeline_slug} branch=main reliability unavailable"
            ),
            **composition,
            "build_numbers": sorted(cohort_build_numbers),
            "provenance": cohort_provenance,
        },
        "evidence_definitions": {
            "mixed_outcome_history": (
                f"At least one passed and one incident observation in the all-main {pipeline_slug} cohort; "
                "this is a flaky candidate, not proof that a retry recovered."
            ),
            "explicit_retry_recovery": "Buildkite retry metadata linking a failed attempt to a passed retry.",
            "terminal_history": "Only passed, hard-failed, or soft-failed jobs count in the denominator.",
        },
        "denominator": {
            "unit": f"terminal {pipeline_slug} branch=main job observations",
            "builds": counts["builds"],
            "groups": len(catalog),
            "observations": counts["terminal_observations"],
            "linked_observations": counts["linked_observations"],
            "unknown_observations_excluded": counts["unknown_observations_excluded"],
        },
        "summary": {
            "group_count": len(catalog),
            "mixed_outcome_group_count": len(candidates),
            "stable_group_count": sum(row["incident_count"] == 0 for row in catalog),
            "persistent_incident_group_count": sum(row["passed"] == 0 and row["incident_count"] > 0 for row in catalog),
        },
        "group_catalog": catalog,
        "flaky_candidates": candidates,
        "latency_rankings": {
            "by_median_duration": by_median,
            "by_p90_duration": by_p90,
            "by_max_duration": by_max,
        },
        "retry_analysis": retry_analysis,
    }


def _job_source_counts(queue_jobs: dict) -> dict:
    return dict(sorted(Counter(
        str(job.get("source") or "unknown")
        for state in ("pending", "running")
        for job in queue_jobs.get(state) or []
    ).items()))


def _filter_queue_snapshot(snapshot: dict) -> dict:
    if not snapshot:
        return {}
    row = dict(snapshot)
    queues = {
        name: stats
        for name, stats in (snapshot.get("queues") or {}).items()
        if _is_amd_queue(name)
    }
    row["queues"] = queues
    row["hardware_scope"] = "amd_mi_gpu"
    if "scope_totals" in row:
        def totals_for(selected: dict) -> dict:
            sources = sorted({str((stats or {}).get("count_source") or "unknown") for stats in selected.values()})
            return {"waiting": sum(int((stats or {}).get("waiting") or 0) for stats in selected.values()),
                    "running": sum(int((stats or {}).get("running") or 0) for stats in selected.values()),
                    "queue_count": len(selected), "count_sources": sources,
                    "count_source": sources[0] if len(sources) == 1 else "mixed" if sources else "unavailable"}
        row["scope_totals"] = {"all": totals_for(queues), "amd": totals_for(queues)}
        target_ids = set((row.get("target_queue_scope") or {}).get("queue_ids") or [])
        if target_ids:
            row["scope_totals"]["target"] = totals_for({name: stats for name, stats in queues.items() if name in target_ids})
    for total, metric in (
        ("total_waiting", "waiting"),
        ("total_running", "running"),
        ("total_zombie_waiting", "zombie_waiting"),
        ("total_zombie_running", "zombie_running"),
    ):
        if total in row or metric in {"waiting", "running"}:
            row[total] = sum(int((stats or {}).get(metric) or 0) for stats in queues.values())
    return row


def _filter_queue_jobs(queue_jobs: dict) -> dict:
    result = dict(queue_jobs)
    for state in ("pending", "running"):
        result[state] = [
            job for job in queue_jobs.get(state) or []
            if _is_amd_queue(job.get("queue") or job.get("q")) and not is_cpu_only_job(job)
        ]
    return result


def _compact_history_snapshot(snapshot: dict) -> dict:
    """Project history to chart/detail fields without duplicating verbose contracts."""
    queues = {}
    for name, source in (snapshot.get("queues") or {}).items():
        if not _is_amd_queue(name) or not isinstance(source, dict):
            continue
        compact_fields = (
            "waiting", "running", "scheduled", "total",
            "zombie_waiting", "zombie_running",
            "connected_agents", "connected_agents_source",
            "count_source", "count_source_family",
            "p50_wait", "p50_wait_source", "p75_wait", "p75_wait_source",
            "p90_wait", "p90_wait_source", "p95_wait", "p95_wait_source",
            "p99_wait", "p99_wait_source", "avg_wait", "avg_wait_source",
            "max_wait", "max_wait_source", "wait_source", "wait_source_family",
            "wait_sample_count", "wait_sample_expected_count", "wait_sample_complete",
            "sample_count", "official_wait_source",
            "sample_wait_source", "metrics_ts", "current_wait",
            "official_wait", "sample_wait", "archive_wait_peaks",
            "archive_sample_wait_peaks", "history_observation_only",
        )
        row = {
            key: source[key]
            for key in compact_fields
            if key in source
        }
        for key, value in source.items():
            if (
                key.endswith("_source")
                or "source_family" in key
            ):
                row.setdefault(key, value)
        # Presence in the source queue map is itself an observation. Retain
        # idle rows so zero load remains distinct from an unobserved queue.
        queues[name] = row
    sources = snapshot.get("sources") or {}
    history_provenance = sources.get("history_provenance") or {}
    return {
        "ts": snapshot.get("ts"),
        "metrics_observed_at": snapshot.get("metrics_observed_at"),
        "details_observed_at": snapshot.get("details_observed_at"),
        "details_status": snapshot.get("details_status"),
        "schema_version": snapshot.get("schema_version"),
        "history_mode": snapshot.get("history_mode"),
        "archive_bucket_start": snapshot.get("archive_bucket_start"),
        "total_waiting": snapshot.get("total_waiting", 0),
        "total_running": snapshot.get("total_running", 0),
        "total_zombie_waiting": snapshot.get("total_zombie_waiting", 0),
        "total_zombie_running": snapshot.get("total_zombie_running", 0),
        "tracked_queue_count": len(queues),
        "queues": queues,
        "sources": {
            key: sources.get(key)
            for key in ("counts", "agents", "official_wait", "sampled_wait", "waits")
            if sources.get(key) is not None
        } | ({"history_provenance": history_provenance} if history_provenance else {}),
    }


def _queue_pressure_baseline(history: list[dict]) -> dict:
    queue_names = sorted({
        name for snapshot in history for name in (snapshot.get("queues") or {})
    })
    baseline = {}
    for name in queue_names:
        loads = sorted(
            float((row.get("running") or 0) + (row.get("waiting") or 0))
            for snapshot in history
            if isinstance((row := (snapshot.get("queues") or {}).get(name)), dict)
        )
        if not loads:
            continue
        baseline[name] = {
            "median": _percentile(loads, 50),
            "p95": _percentile(loads, 95),
            "snapshot_count": len(loads),
        }
    return baseline


def _queue(snapshot: dict, queue_jobs: dict, history: list[dict]) -> dict:
    snapshot = _filter_queue_snapshot(snapshot)
    queue_jobs = _filter_queue_jobs(queue_jobs)
    history = [_compact_history_snapshot(_filter_queue_snapshot(row)) for row in history]
    counts_only = sum(
        (
            str((row.get("provenance") or {}).get("mode") or row.get("history_mode") or "")
            == "counts_only"
            or bool(((row.get("sources") or {}).get("history_provenance") or {}).get("migration"))
        )
        for row in history
    )
    return {
        "snapshot": snapshot,
        "queue_jobs": queue_jobs,
        "history": history,
        "pressure_baseline": _queue_pressure_baseline(history),
        "history_summary": {
            "snapshot_count": len(history),
            "first_observed_at": history[0].get("ts") if history else None,
            "last_observed_at": history[-1].get("ts") if history else None,
            "counts_only_snapshot_count": counts_only,
            "source_path": SOURCE_FILES["queue_timeseries"],
        },
        "provenance": {
            "source_paths": {
                "history": SOURCE_FILES["queue_timeseries"],
                "jobs": SOURCE_FILES["queue_jobs"],
            },
            "snapshot": {
                "path": SOURCE_FILES["queue_timeseries"],
                "source_path": SOURCE_FILES["queue_timeseries"],
                "timestamp": snapshot.get("ts"),
                "run_id": snapshot.get("run_id"),
                "sources": snapshot.get("sources") or snapshot.get("provenance") or {},
                "evidence_kind": "published queue aggregate",
            },
            "history": {
                "path": SOURCE_FILES["queue_timeseries"],
                "source_path": SOURCE_FILES["queue_timeseries"],
                "snapshot_count": len(history),
                "counts_only_snapshot_count": counts_only,
                "evidence_kind": "published queue aggregate history",
            },
            "jobs": {
                "path": SOURCE_FILES["queue_jobs"],
                "source_path": SOURCE_FILES["queue_jobs"],
                "timestamp": queue_jobs.get("ts"),
                "source_counts": _job_source_counts(queue_jobs),
                "evidence_kind": "published retained job records",
            } | (
                {
                    "details_observed_at": queue_jobs.get("details_observed_at"),
                    "details_status": queue_jobs.get("details_status"),
                    "details_refresh_attempted_at": queue_jobs.get(
                        "details_refresh_attempted_at"
                    ),
                }
                if "details_status" in queue_jobs
                else {}
            ),
        },
    }


def _is_amd_queue(value: Any) -> bool:
    name = str(value or "").strip().lower()
    return bool(amd_gpu_hardware(name)) and not _is_excluded_queue(name)


def _nonnegative_count(value: Any) -> int:
    number = _number(value)
    return max(0, int(number)) if number is not None else 0


def _omni_history_scope(queue_rows: list[dict]) -> dict:
    waiting_total = 0
    running_total = 0
    waiting_attributed = 0
    running_attributed = 0
    waiting_observed = 0
    running_observed = 0
    waiting_supported = False
    running_supported = False

    for stats in queue_rows:
        waiting_total += _nonnegative_count(stats.get("waiting"))
        running_total += _nonnegative_count(stats.get("running"))
        waiting_split = stats.get("waiting_by_workload")
        running_split = stats.get("running_by_workload")
        if isinstance(waiting_split, dict):
            waiting_attributed += sum(
                _nonnegative_count(value) for value in waiting_split.values()
            )
            if "omni" in waiting_split:
                waiting_supported = True
                waiting_observed += _nonnegative_count(waiting_split.get("omni"))
        if isinstance(running_split, dict):
            running_attributed += sum(
                _nonnegative_count(value) for value in running_split.values()
            )
            if "omni" in running_split:
                running_supported = True
                running_observed += _nonnegative_count(running_split.get("omni"))

    waiting_status = "unavailable"
    running_status = "unavailable"
    if queue_rows and waiting_supported:
        waiting_status = "complete" if waiting_attributed == waiting_total else "partial"
    if queue_rows and running_supported:
        running_status = "complete" if running_attributed == running_total else "partial"
    return {
        "waiting_supported": waiting_supported,
        "running_supported": running_supported,
        "waiting_observed": waiting_observed,
        "running_observed": running_observed,
        "waiting_attributed": waiting_attributed,
        "running_attributed": running_attributed,
        "waiting_total": waiting_total,
        "running_total": running_total,
        "waiting_attribution": waiting_status,
        "running_attribution": running_status,
    }


def _omni_history(
    history: list[dict],
    allowed_queues: set[str] | None = None,
) -> dict:
    """Retain explicit Omni occupancy only for the configured AMD queues."""
    allowed_queues = allowed_queues or {
        str(name)
        for snapshot in history
        for name in (snapshot.get("queues") or {})
        if _is_amd_queue(name)
    }
    points = []
    for snapshot in history:
        amd_rows = [
            stats
            for name, stats in (snapshot.get("queues") or {}).items()
            if name in allowed_queues and isinstance(stats, dict)
        ]
        amd = _omni_history_scope(amd_rows)
        if not any((amd["waiting_supported"], amd["running_supported"])):
            continue
        points.append({
            "ts": snapshot.get("ts"),
            "amd": amd,
        })

    return {
        "points": points,
        "summary": {
            "snapshot_count": len(points),
            "first_observed_at": points[0]["ts"] if points else None,
            "last_observed_at": points[-1]["ts"] if points else None,
            "complete_waiting_snapshot_count": sum(
                point["amd"]["waiting_attribution"] == "complete"
                for point in points
            ),
            "complete_running_snapshot_count": sum(
                point["amd"]["running_attribution"] == "complete"
                for point in points
            ),
        },
        "provenance": {
            "source_path": SOURCE_FILES["queue_timeseries"],
            "count_semantics": (
                "Observed Omni workload counts only; partial attribution is a "
                "lower bound and is never inferred from aggregate queue totals."
            ),
            "scope": "configured standard AMD queues only; perf-eval queues are excluded",
            "queues": sorted(allowed_queues),
        },
    }


def _omni(
    queue_snapshot: dict,
    queue_jobs: dict,
    queue_history: list[dict],
    heuristic: dict,
    issue_state: dict,
    workload_mapping: dict | None = None,
    capacity: dict | None = None,
) -> dict:
    workload_mapping = workload_mapping if ((workload_mapping or {}).get("hardware_scope") == "amd_mi_gpu"
        and (((workload_mapping or {}).get("scope") or {}).get("workload_pipelines") or {}).get("main") == ["ci"]) else {}
    capacity = capacity or {}
    mapping_scope = workload_mapping.get("scope") or {}
    allowed_queues = {
        str(name)
        for name in mapping_scope.get("queues") or []
        if _is_amd_queue(name)
    }
    if not allowed_queues:
        allowed_queues = {
            str(row.get("id"))
            for row in capacity.get("queues") or []
            if isinstance(row, dict)
            and row.get("monitored") is not False
            and row.get("id")
            and _is_amd_queue(row.get("id"))
        }
    omni_pipelines = {
        str(name)
        for name in (
            (mapping_scope.get("workload_pipelines") or {}).get("omni")
            or ["vllm-omni-amd-ci"]
        )
        if str(name)
    }
    jobs = {
        state: [
            job for job in queue_jobs.get(state) or []
            if str(job.get("pipeline") or "") in omni_pipelines
            and is_amd_ci_job(job)
            and str(job.get("queue") or job.get("q") or "") in allowed_queues
        ]
        for state in ("pending", "running")
    }
    queue_rows = [
        stats
        for name, stats in (queue_snapshot.get("queues") or {}).items()
        if name in allowed_queues and isinstance(stats, dict)
    ]
    attribution = _omni_history_scope(queue_rows)
    waiting_by_queue: dict[str, int] = {}
    running_by_queue: dict[str, int] = {}
    for queue_name in sorted(allowed_queues):
        waiting = sum(
            not job.get("analysis_excluded")
            and str(job.get("queue") or job.get("q") or "") == queue_name
            for job in jobs["pending"]
        )
        running = sum(
            not job.get("analysis_excluded")
            and str(job.get("queue") or job.get("q") or "") == queue_name
            for job in jobs["running"]
        )
        if waiting:
            waiting_by_queue[queue_name] = waiting
        if running:
            running_by_queue[queue_name] = running

    ledger = {
        "waiting": sum(not job.get("analysis_excluded") for job in jobs["pending"]),
        "running": sum(not job.get("analysis_excluded") for job in jobs["running"]),
    }
    count_basis = {"waiting": "exact_pipeline_active_job_ledger", "running": "exact_pipeline_active_job_ledger"}
    waiting = ledger["waiting"]
    running = ledger["running"]

    trigger = int(heuristic.get("trigger") or 0)
    healthy = int(heuristic.get("healthy") or 0)
    if trigger and waiting >= trigger:
        status = "surge"
    elif waiting > healthy:
        status = "elevated"
    else:
        status = "healthy"
    return {
        "status": status,
        "hardware_scope": "amd_mi_gpu",
        "current": {
            "waiting": waiting,
            "running": running,
            "waiting_by_queue": waiting_by_queue,
            "running_by_queue": running_by_queue,
            "ledger": ledger,
            "count_basis": count_basis,
            "attribution": attribution,
        },
        "heuristic_thresholds": heuristic,
        "current_jobs": jobs,
        "history": _omni_history(queue_history, allowed_queues),
        "mapping_history": workload_mapping,
        "scope": {
            "label": "Omni CI",
            "queues": sorted(allowed_queues),
            "pipelines": sorted(omni_pipelines),
            "excluded_queue_classes": mapping_scope.get("excluded_queue_classes") or ["perf_eval"],
            "count_semantics": "exact pipeline identity plus exact configured queue allowlist",
        },
        "issue_state": issue_state,
        "provenance": {
            "queue_snapshot_ts": queue_snapshot.get("ts"),
            "queue_jobs_ts": queue_jobs.get("ts"),
            "source_paths": {
                "queue_aggregates": SOURCE_FILES["queue_timeseries"],
                "queue_jobs": SOURCE_FILES["queue_jobs"],
                "heuristic": SOURCE_FILES["omni_heuristic"],
                "issue_state": SOURCE_FILES["omni_issue_state"],
                "mapping_history": SOURCE_FILES["workload_mapping"],
            },
            "sources": {
                "queue_aggregates": {
                    "path": SOURCE_FILES["queue_timeseries"],
                    "timestamp": queue_snapshot.get("ts"),
                    "evidence_kind": "published workload aggregate",
                },
                "queue_jobs": {
                    "path": SOURCE_FILES["queue_jobs"],
                    "timestamp": queue_jobs.get("ts"),
                    "source_counts": _job_source_counts(queue_jobs),
                    "evidence_kind": "published retained job records",
                },
                "heuristic": {
                    "path": SOURCE_FILES["omni_heuristic"],
                    "timestamp": heuristic.get("generated_at"),
                    "evidence_kind": "published threshold configuration",
                },
                "issue_state": {
                    "path": SOURCE_FILES["omni_issue_state"],
                    "timestamp": issue_state.get("last_snapshot_ts"),
                    "evidence_kind": "published issue watcher state",
                },
                "mapping_history": {
                    "path": SOURCE_FILES["workload_mapping"],
                    "timestamp": workload_mapping.get("generated_at"),
                    "evidence_kind": "published unique-job AMD mapping aggregate",
                },
            },
        },
    }


def _utc_iso(value: datetime | None) -> str | None:
    """Serialize an aware timestamp with the repository's canonical UTC suffix."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _attention(
    nightly: dict,
    reliability: dict,
    queue: dict,
    omni: dict,
    amd_test_health: dict | None = None,
) -> list[dict]:
    items = []
    amd_builds = (nightly.get("pipelines") or [{}])[0].get("builds") or []
    latest = amd_builds[0] if amd_builds else {}
    if latest.get("test_jobs_blocked"):
        items.append({
            "kind": "nightly_infrastructure_blocked",
            "severity": "critical",
            "count": int(latest["test_jobs_blocked"]),
        })
    # Current severity must come from the current outcome, not movement alone.
    # A newly soft-failing group is warning-level, while a recurring hard
    # failure must remain critical.
    if latest.get("failed_groups"):
        items.append({
            "kind": "nightly_hard_failures",
            "severity": "critical",
            "count": len(latest["failed_groups"]),
        })
    if latest.get("soft_failed_groups"):
        items.append({
            "kind": "nightly_soft_failures",
            "severity": "warning",
            "count": len(latest["soft_failed_groups"]),
        })
    snapshot = queue.get("snapshot") or {}
    zombies = int(snapshot.get("total_zombie_waiting") or 0) + int(snapshot.get("total_zombie_running") or 0)
    if zombies:
        items.append({"kind": "queue_zombies", "severity": "critical", "count": zombies})
    if int(snapshot.get("total_waiting") or 0):
        items.append({"kind": "queue_waiting", "severity": "warning", "count": snapshot["total_waiting"]})
    logical_inventory = (
        (amd_test_health or {}).get("latest_logical_test_groups") or {}
    )
    logical_counts = (
        ((amd_test_health or {}).get("summary") or {}).get(
            "latest_test_group_counts"
        )
        or {}
    )
    logical_summary = logical_inventory.get("summary") or {}
    logical_rows = logical_inventory.get("rows")
    logical_inventory_eligible = (
        logical_inventory.get("available") is True
        and (logical_inventory.get("reconciliation") or {}).get(
            "matches_latest_test_group_counts"
        ) is True
        and logical_counts.get("available") is True
        and _strict_int(logical_inventory.get("build_number")) is not None
        and _strict_int(logical_inventory.get("build_number"))
        == _strict_int(logical_counts.get("build_number"))
        and isinstance(logical_rows, list)
        and len(logical_rows) == _strict_int(logical_counts.get("total"))
    )
    logical_not_fully_passing = (
        int(logical_summary.get("partial") or 0)
        + int(logical_summary.get("non_passing") or 0)
        if logical_inventory_eligible
        else 0
    )
    if logical_not_fully_passing:
        items.append({
            "kind": "amd_logical_groups_not_fully_passing",
            "severity": "warning",
            "count": logical_not_fully_passing,
        })
    if omni.get("status") != "healthy":
        items.append({
            "kind": "omni_waiting",
            "severity": "critical" if omni.get("status") == "surge" else "warning",
            "count": (omni.get("current") or {}).get("waiting", 0),
        })
    return items


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp (with trailing ``Z``) to an aware datetime."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _amd_agent_health(data_dir: Path) -> dict:
    """Load the pre-aggregated AMD agent-health block for the CI-agent-health view.

    All heavy lifting — walking every build across every branch in the AMD
    pipelines, computing per-node/day reliability rollups, and isolating
    infra-suspect failures (a failure whose test group otherwise passes that day,
    on another node) — is done by ``scripts/vllm/collect_agent_health.py`` and
    persisted to ``agent_health.json``. This snapshot simply embeds that payload;
    the frontend aggregates the reliability table and clusters co-failure events
    client-side, reactive to the window / GPU / node / co-failure-window /
    exclude-cancelled / nightly-only controls.
    """
    payload = _load_json(data_dir / "agent_health.json")
    if not isinstance(payload, dict) or payload.get("hardware_scope") != "amd_mi_gpu" or payload.get("pipelines") != ["ci"]:
        return {}
    for key in ("node_days", "failing_runs", "failure_accounting", "node_accounting_totals", "failure_accounting_totals"):
        if any(not isinstance(row, dict) or not re.fullmatch(r"mi\d{3,4}", str(row.get("h") or ""), flags=re.I) for row in payload.get(key) or []):
            return {}
    if any(row.get("p") != "ci" or not is_amd_ci_job({"queue": row.get("q"), "name": row.get("g")}) for row in payload.get("failing_runs") or []):
        return {}
    return payload


def _agent_health_row_key(row: dict) -> tuple[str, str, str]:
    return (
        str(row.get("d") or ""),
        str(row.get("nd") or ""),
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    )


def _agent_health_node_accounting(node_days: list[dict]) -> list[dict]:
    """Build exact date/hardware run totals without retaining node identities."""
    totals: dict[tuple[str, str, bool], list[list[int]]] = {}
    for row in node_days:
        if not isinstance(row, dict):
            continue
        key = (
            str(row.get("d") or ""),
            str(row.get("h") or ""),
            str(row.get("nd") or "") != "(unidentified)",
        )
        buckets = totals.setdefault(key, [[0, 0, 0, 0], [0, 0, 0, 0]])
        for bucket_index, field in enumerate(("a", "n")):
            values = row.get(field)
            if not isinstance(values, list):
                continue
            # Preserve the collector's modern [runs, soft, hard, cancelled]
            # representation while accepting its legacy three-value rows.
            normalized = [0, 0, 0, 0]
            for index, value in enumerate(values[:4]):
                if isinstance(value, int) and not isinstance(value, bool):
                    normalized[index] = value
            if len(values) == 3:
                normalized = [normalized[0], 0, normalized[1], normalized[2]]
            for index, value in enumerate(normalized):
                buckets[bucket_index][index] += value
    return [
        {"d": day, "h": hardware, "id": identified, "a": values[0], "n": values[1]}
        for (day, hardware, identified), values in sorted(totals.items())
    ]


def _agent_health_failure_accounting(failing_runs: list[dict]) -> list[dict]:
    counts: Counter = Counter()
    for row in failing_runs:
        if not isinstance(row, dict):
            continue
        counts[(
            str(row.get("d") or ""),
            str(row.get("nd") or ""),
            str(row.get("h") or ""),
            str(row.get("s") or ""),
            bool(row.get("i")),
            bool(row.get("ng")),
            bool(row.get("bc")),
        )] += 1
    return [
        {
            "d": day,
            "nd": node,
            "h": hardware,
            "s": state,
            "i": int(infra),
            "ng": nightly,
            "bc": cancelled,
            "c": count,
        }
        for (
            day, node, hardware, state, infra, nightly, cancelled,
        ), count in sorted(counts.items())
    ]


def _agent_health_failure_totals(accounting: list[dict]) -> list[dict]:
    """Collapse per-node accounting to a fixed-cardinality exact filter cube."""
    counts: Counter = Counter()
    for row in accounting:
        if not isinstance(row, dict):
            continue
        count = row.get("c")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            continue
        counts[(
            str(row.get("d") or ""),
            str(row.get("h") or ""),
            str(row.get("s") or ""),
            bool(row.get("i")),
            bool(row.get("ng")),
            bool(row.get("bc")),
        )] += count
    return [
        {
            "d": day,
            "h": hardware,
            "s": state,
            "i": int(infra),
            "ng": nightly,
            "bc": cancelled,
            "c": count,
        }
        for (day, hardware, state, infra, nightly, cancelled), count
        in sorted(counts.items())
    ]


def _bounded_operations_agent_health(
    value: Any,
    *,
    max_bytes: int | None = None,
) -> dict:
    """Project agent health into its public route without losing ledger totals."""
    if max_bytes is None:
        max_bytes = OPERATIONS_AGENT_HEALTH_SECTION_MAX_BYTES
    if max_bytes <= 0:
        raise ValueError("Operations agent-health byte budget must be positive")
    source = value if isinstance(value, dict) else {}
    node_days = sorted(
        (row for row in source.get("node_days") or [] if isinstance(row, dict)),
        key=_agent_health_row_key,
    )
    failing_runs = sorted(
        (row for row in source.get("failing_runs") or [] if isinstance(row, dict)),
        key=lambda row: (
            str(row.get("d") or ""),
            str(row.get("t") or ""),
            str(row.get("j") or ""),
            _agent_health_row_key(row),
        ),
    )
    raw_accounting = source.get("failure_accounting")
    accounting = sorted(
        (
            row for row in raw_accounting or []
            if isinstance(row, dict)
        ),
        key=_agent_health_row_key,
    )
    source_evidence_retention = (
        (source.get("retention") or {}).get("failure_evidence") or {}
    )
    source_failure_total = source_evidence_retention.get("source")
    if not isinstance(source_failure_total, int) or isinstance(source_failure_total, bool):
        source_failure_total = source.get("infra_failure_count")
    if not isinstance(source_failure_total, int) or isinstance(source_failure_total, bool):
        source_failure_total = len(failing_runs)
    source_failure_total = max(source_failure_total, len(failing_runs))

    if not accounting and source_failure_total == len(failing_runs):
        accounting = _agent_health_failure_accounting(failing_runs)
    accounted_total = sum(
        row.get("c", 0)
        for row in accounting
        if isinstance(row.get("c"), int) and not isinstance(row.get("c"), bool)
    )
    source_accounting_retention = (
        (source.get("retention") or {}).get("failure_accounting") or {}
    )
    accounting_complete = (
        accounted_total == source_failure_total
        and source_accounting_retention.get("complete_relative_to_source") is not False
    )

    node_totals = _agent_health_node_accounting(node_days)
    failure_totals = _agent_health_failure_totals(accounting)
    fixed = {
        key: item
        for key, item in source.items()
        if key not in {
            "node_days",
            "failing_runs",
            "failure_accounting",
            "node_accounting_totals",
            "failure_accounting_totals",
            "operations_publication_retention",
        }
    }

    def candidate(node_count: int, failure_count: int, accounting_count: int) -> dict:
        published_node_days = node_days[-node_count:] if node_count else []
        published_failures = failing_runs[-failure_count:] if failure_count else []
        published_accounting = accounting[-accounting_count:] if accounting_count else []
        evidence_complete = (
            source_evidence_retention.get("complete_relative_to_source") is not False
            and failure_count == len(failing_runs)
            and source_failure_total == len(failing_runs)
        )
        result = dict(fixed)
        result["node_days"] = published_node_days
        result["failing_runs"] = published_failures
        result["failure_accounting"] = published_accounting
        result["node_accounting_totals"] = node_totals
        result["failure_accounting_totals"] = failure_totals
        result["published_failure_evidence_count"] = len(published_failures)
        retention = dict(result.get("retention") or {})
        retention["failure_evidence"] = {
            "source": source_failure_total,
            "published": len(published_failures),
            "omitted": source_failure_total - len(published_failures),
            "complete_relative_to_source": evidence_complete,
            "selection": "source_priority_then_newest_operations_suffix",
        }
        result["retention"] = retention
        result["operations_publication_retention"] = {
            "policy": "retain_exact_aggregate_accounting_then_newest_whole_rows",
            "max_bytes": max_bytes,
            "complete_relative_to_agent_health": (
                node_count == len(node_days)
                and failure_count == len(failing_runs)
                and accounting_count == len(accounting)
            ),
            "aggregate_accounting_complete": accounting_complete,
            "node_days": {
                "source": len(node_days),
                "published": len(published_node_days),
                "omitted": len(node_days) - len(published_node_days),
                "complete": node_count == len(node_days),
            },
            "failure_evidence": {
                "source_agent_health": len(failing_runs),
                "source_ledger": source_failure_total,
                "published": len(published_failures),
                "omitted_from_agent_health": len(failing_runs) - len(published_failures),
                "omitted_from_ledger": source_failure_total - len(published_failures),
                "complete": evidence_complete,
            },
            "failure_accounting": {
                "source": len(accounting),
                "published": len(published_accounting),
                "omitted": len(accounting) - len(published_accounting),
                "complete": accounting_count == len(accounting),
            },
        }
        return result

    full = candidate(len(node_days), len(failing_runs), len(accounting))
    if _json_bytes({"amd_agent_health": full}) <= max_bytes:
        return full

    # Link evidence is the least compact and has complete count cubes behind it.
    low = 0
    high = len(failing_runs)
    best: dict | None = None
    while low <= high:
        keep = (low + high) // 2
        attempt = candidate(len(node_days), keep, len(accounting))
        if _json_bytes({"amd_agent_health": attempt}) <= max_bytes:
            best = attempt
            low = keep + 1
        else:
            high = keep - 1
    if best is not None:
        return best

    # If node-granular ledgers themselves are too large, keep aligned newest
    # suffixes only as drill-down evidence. The aggregate cubes above remain
    # exact for date/hardware/window totals and the UI suppresses node rates.
    low = 0
    high = 1_000_000
    best = None
    while low <= high:
        ratio = (low + high) // 2
        node_count = len(node_days) * ratio // 1_000_000
        accounting_count = len(accounting) * ratio // 1_000_000
        attempt = candidate(node_count, 0, accounting_count)
        if _json_bytes({"amd_agent_health": attempt}) <= max_bytes:
            best = attempt
            low = ratio + 1
        else:
            high = ratio - 1
    if best is None:
        raise RuntimeError(
            "Operations agent-health exact aggregate accounting cannot fit its "
            f"{max_bytes}-byte public section budget"
        )
    return best


def _bounded_operations_amd_test_health(
    value: Any,
    *,
    max_bytes: int | None = None,
) -> dict:
    """Bound AMD drill-down catalogs while preserving source summary scalars."""
    if max_bytes is None:
        max_bytes = OPERATIONS_AMD_TEST_HEALTH_SECTION_MAX_BYTES
    if max_bytes <= 0:
        raise ValueError("Operations AMD test-health byte budget must be positive")
    source = dict(value) if isinstance(value, dict) else {}
    source_groups = list(source.get("group_catalog") or [])
    source_builds = list(source.get("builds") or [])
    inventory = dict(source.get("latest_logical_test_groups") or {})
    source_logical = list(inventory.get("rows") or [])
    latest_build = int((source.get("summary") or {}).get("latest_build_number") or 0)
    incident_states = {
        "soft", "soft_fail", "soft_failed", "hard", "incident", "error",
        "failed", "timed_out", "broken", "canceled",
    }
    group_priority = sorted(
        range(len(source_groups)),
        key=lambda index: (
            int(source_groups[index].get("latest_build_number") or 0) == latest_build,
            str(source_groups[index].get("latest_state") or "").lower()
            in incident_states,
            str(source_groups[index].get("latest_observed_at") or ""),
            str(source_groups[index].get("id") or source_groups[index].get("name") or ""),
        ),
        reverse=True,
    )
    build_priority = sorted(
        range(len(source_builds)),
        key=lambda index: (
            str(source_builds[index].get("observed_at") or ""),
            int(source_builds[index].get("number") or 0),
        ),
        reverse=True,
    )
    logical_priority = sorted(
        range(len(source_logical)),
        key=lambda index: (
            str(source_logical[index].get("state") or "")
            in {"non_passing", "partial", "unresolved"},
            str(source_logical[index].get("label") or source_logical[index].get("logical_key") or ""),
        ),
        reverse=True,
    )

    def selected_rows(rows: list, priority: list[int], ratio: int) -> list:
        count = len(rows) * ratio // 1_000_000
        selected = set(priority[:count])
        return [row for index, row in enumerate(rows) if index in selected]

    def candidate(ratio: int) -> dict:
        groups = selected_rows(source_groups, group_priority, ratio)
        builds = selected_rows(source_builds, build_priority, ratio)
        logical = selected_rows(source_logical, logical_priority, ratio)
        result = {
            key: item
            for key, item in source.items()
            if key not in {
                "group_catalog",
                "builds",
                "latest_logical_test_groups",
                "operations_publication_retention",
            }
        }
        result["group_catalog"] = groups
        result["builds"] = builds
        result["latest_logical_test_groups"] = {**inventory, "rows": logical}
        accounting = {
            "group_catalog": {
                "source": len(source_groups),
                "published": len(groups),
                "omitted": len(source_groups) - len(groups),
                "complete": len(groups) == len(source_groups),
            },
            "builds": {
                "source": len(source_builds),
                "published": len(builds),
                "omitted": len(source_builds) - len(builds),
                "complete": len(builds) == len(source_builds),
            },
            "latest_logical_test_groups": {
                "source": len(source_logical),
                "published": len(logical),
                "omitted": len(source_logical) - len(logical),
                "complete": len(logical) == len(source_logical),
            },
        }
        result["operations_publication_retention"] = {
            "policy": "retain_current_failures_and_newest_whole_rows",
            "max_bytes": max_bytes,
            "complete_relative_to_amd_test_health": all(
                row["complete"] for row in accounting.values()
            ),
            "aggregate_scalars_complete": True,
            **accounting,
        }
        return result

    full = candidate(1_000_000)
    if _json_bytes({"amd_test_health": full}) <= max_bytes:
        return full
    low = 0
    high = 999_999
    best: dict | None = None
    while low <= high:
        ratio = (low + high) // 2
        attempt = candidate(ratio)
        if _json_bytes({"amd_test_health": attempt}) <= max_bytes:
            best = attempt
            low = ratio + 1
        else:
            high = ratio - 1
    if best is None:
        raise RuntimeError(
            "Unable to build bounded Operations AMD test-health section while "
            "preserving aggregate scalars"
        )
    return best


def build_snapshot(data_dir: Path | str, generated_at: str | None = None) -> dict:
    data_dir = Path(data_dir)
    paths = {name: data_dir / filename for name, filename in SOURCE_FILES.items()}
    loaded = {name: _load_json(path) for name, path in paths.items() if path.suffix == ".json"}
    queue_history = load_queue_history(paths["queue_timeseries"])
    queue_snapshot = _filter_queue_snapshot(load_latest_queue_snapshot(paths["queue_timeseries"]))

    analytics = loaded.get("analytics") or {}
    ci_health = loaded.get("ci_health") or {}
    ci_analytics = analytics.get("ci") or {}
    amd_analytics = _scoped_ci_analytics(ci_analytics)
    amd_health = _current_ci_health(ci_health.get("amd") or {})
    amd_nightly = _nightly_pipeline(
        "ci", amd_analytics, amd_health, job_scope="amd_gpu",
    )
    ci_health_retention = ci_health.get("publication_retention") or {}
    ci_health_build_retention = ci_health_retention.get("builds") or {}
    if isinstance(ci_health_retention, dict) and ci_health_retention:
        for nightly, side in ((amd_nightly, "amd"),):
            nightly["ci_health_publication_retention"] = {
                "policy": ci_health_retention.get("policy"),
                "max_bytes": ci_health_retention.get("max_bytes"),
                "complete_relative_to_source": (
                    (ci_health_build_retention.get(side) or {}).get("complete")
                    is not False
                ),
                "aggregate_scalars_complete": (
                    ci_health_retention.get("aggregate_scalars_complete") is True
                ),
                "builds": ci_health_build_retention.get(side) or {},
            }
    definition_parity = loaded.get("config_parity") or {}
    amd_test_health = _amd_test_health(
        data_dir,
        amd_analytics,
        amd_health,
        definition_parity,
    )
    runtime_matrix = loaded.get("amd_test_matrix") or {}
    runtime_source = runtime_matrix.get("source") or {}
    matrix_is_current_ci = (
        runtime_source.get("pipeline") == "ci"
        and runtime_source.get("hardware_scope") == "amd_mi_gpu"
        and runtime_source.get("definition_source") == "main_ci_inline_and_native_amd"
        and _pipeline_build_url_matches(runtime_source.get("latest_build_url"), "ci", _strict_int(runtime_source.get("latest_build_number")))
    )
    amd_test_health["runtime_matrix_summary"] = runtime_matrix.get("summary") or {} if matrix_is_current_ci else {}
    amd_test_health["runtime_matrix_source"] = runtime_source if matrix_is_current_ci else {}
    amd_agent_health = _amd_agent_health(data_dir)
    pipeline_blocks = [amd_nightly]
    nightly = {
        "primary_pipeline": "ci",
        "primary_cohort": "ci-amd",
        "transition_policy_id": INCIDENT_TRANSITION_POLICY_ID,
        "failure_movement_policy_id": OBSERVED_FAILURE_MOVEMENT_ID,
        "hardware_scope": "amd_mi_gpu",
        "pipeline_order": ["ci-amd"],
        "history_window_days": NIGHTLY_BUILD_LIMIT,
        "failure_movement_basis": (
            "current versus preceding eligible completed nightly with usable test "
            "results: every observed hard or soft failure is new or recurring; an "
            "observed pass after a failure is fixed; missing and indeterminate "
            "identities are omitted"
        ),
        "transition_basis": (
            "oldest-to-newest confirmed-incident replay: hard failures confirm "
            "immediately; soft failures confirm after two distinct eligible completed "
            "builds; passes resolve; absent and indeterminate observations hold state"
        ),
        "canonical_history": amd_nightly,
        "pipelines": pipeline_blocks,
    }
    reliability = _reliability(analytics.get("ci") or {}, pipeline_slug="ci")
    test_group_parity = loaded.get("test_group_parity") or {}
    if test_group_parity:
        test_group_parity = {
            **test_group_parity,
            "groups": resolve_reviewed_definition_labels(
                test_group_parity.get("groups") or [], definition_parity, label_field="title"
            ),
        }
    latency = ci_analytics.get("current_nightly_latency")
    if not isinstance(latency, dict) or latency.get("schema_version") != 2 or latency.get("hardware_scope") != "amd_mi_gpu" or latency.get("source_pipeline") != "ci" or latency.get("build_limit") != 5:
        latency = build_current_nightly_latency([], generated_at=generated_at or _utc_now(), source_available=False)
    ownership = loaded.get("ci_ownership") or {}
    ownership = (
        ownership
        if ownership.get("schema_version") == 1
        else {
            "schema_version": 1,
            "available": False,
            "unavailable_reason": "ownership_snapshot_unavailable",
            "areas": [],
            "summary": {},
        }
    )
    queue = _queue(queue_snapshot, loaded.get("queue_jobs") or {}, queue_history)
    omni = _omni(
        queue_snapshot,
        queue.get("queue_jobs") or {},
        queue_history,
        loaded.get("omni_heuristic") or {},
        loaded.get("omni_issue_state") or {},
        loaded.get("workload_mapping") or {},
        loaded.get("capacity_monitor") or {},
    )
    attention = _attention(
        nightly,
        reliability,
        queue,
        omni,
        amd_test_health,
    )
    status = "critical" if any(row["severity"] == "critical" for row in attention) else (
        "attention" if any(row["severity"] == "warning" for row in attention) else "healthy"
    )
    latest_amd = pipeline_blocks[0]["builds"][0] if pipeline_blocks[0]["builds"] else {}
    home = {
        "status": status,
        "attention_count": len(attention),
        "attention": attention,
        "latest_amd_nightly": {
            key: latest_amd.get(key)
            for key in ("number", "created_at", "state", "url")
            if latest_amd.get(key) not in (None, "")
        },
        "queue": {
            "waiting": queue_snapshot.get("total_waiting", 0),
            "running": queue_snapshot.get("total_running", 0),
        },
        "omni_status": omni["status"],
    }

    sources = {}
    for name, path in paths.items():
        data = queue_snapshot if name == "queue_timeseries" else loaded.get(name) or {}
        sources[name] = _source_record(path, data, queue_snapshot.get("ts", "") if name == "queue_timeseries" else "")
    for internal_source in ("agent_health", "omni_issue_state", "ci_ownership"):
        sources[internal_source]["published"] = False
    # The raw JSONL ledger is an internal build input, so diagnostics link to
    # the published analytics source while retaining the actual latest AMD
    # observation time rather than the wrapper regeneration time.
    sources["amd_test_signal"] = {
        "path": SOURCE_FILES["analytics"],
        "timestamp": (amd_test_health.get("summary") or {}).get("latest_observed_at"),
        "timestamp_source": "amd_test_health.summary.latest_observed_at",
        "published": True,
    }

    return {
        "schema_version": 2,
        "generated_at": generated_at or _utc_now(),
        "sources": sources,
        "home": home,
        "attention": attention,
        "nightly": nightly,
        "amd_test_health": amd_test_health,
        "amd_agent_health": amd_agent_health,
        "reliability": reliability,
        "definition_parity": definition_parity,
        "test_group_parity": test_group_parity,
        "latency": latency,
        "ownership": ownership,
        "queue": queue,
        "omni": omni,
    }


def _compact_nightly(nightly: dict, build_limit: int | None = None) -> dict:
    """Drop compatibility aliases and retain only the AMD MI CI cohort."""
    compact = {
        key: value
        for key, value in nightly.items()
        if key not in {"canonical_history", "upstream_parity", "amd", "upstream", "pipelines"}
    }
    pipelines = []
    for pipeline in nightly.get("pipelines") or []:
        if pipeline.get("cohort_id") != "ci-amd" or pipeline.get("hardware_scope") != "amd_mi_gpu":
            continue
        row = dict(pipeline)
        if build_limit is not None:
            row["builds"] = list(row.get("builds") or [])[:build_limit]
        pipelines.append(row)
    compact["pipelines"] = pipelines
    return compact


def _compact_queue_history(history: list[dict]) -> list[dict]:
    """Keep chart fields and omit repeated null-heavy collector metadata."""
    compact_history = []
    for snapshot in history:
        queues = {}
        for name, queue in (snapshot.get("queues") or {}).items():
            compact = {
                key: value
                for key, value in queue.items()
                if key in QUEUE_HISTORY_SHARD_FIELDS and value not in (None, "")
            }
            if compact:
                queues[name] = compact
        compact_history.append({
            key: value
            for key, value in {
                "ts": snapshot.get("ts"),
                "schema_version": snapshot.get("schema_version"),
                "total_waiting": snapshot.get("total_waiting"),
                "total_running": snapshot.get("total_running"),
                "tracked_queue_count": snapshot.get("tracked_queue_count"),
                "queues": queues,
                "sources": snapshot.get("sources"),
            }.items()
            if value not in (None, "")
        })
    return compact_history


def _compact_queue(queue: dict) -> dict:
    """Publish current state; history stays in its lazy JSONL asset.

    At a ten-minute cadence, repeating every history row here would exhaust the
    section's 1 MiB publication budget. The shared section projector
    separately bounds current queue, baseline, and active-job rows.
    """
    compact = dict(queue)
    compact["history"] = []
    return compact


def build_queue_history_chart(history: list[dict], generated_at: str | None = None) -> dict:
    """Encode queue chart history without repeating field names per queue/poll."""
    queue_names = sorted({
        name
        for snapshot in history
        for name in (snapshot.get("queues") or {})
        if not _is_excluded_queue(name)
    })
    wait_sources: list[str | None] = [None]
    wait_providers: list[str | None] = [None]

    def table_index(table: list[str | None], value: object) -> int:
        normalized = str(value) if value not in (None, "") else None
        if normalized not in table:
            table.append(normalized)
        return table.index(normalized)

    points = []
    for snapshot in history:
        queues = snapshot.get("queues") or {}
        encoded_queues = []
        for name in queue_names:
            row = queues.get(name)
            if not isinstance(row, dict):
                encoded_queues.append(None)
                continue
            sample_complete = row.get("wait_sample_complete")
            archived_peaks = []
            archived_sample_peaks = []
            for metric in ("p50", "p95", "p99"):
                peak = (row.get("archive_wait_peaks") or {}).get(metric)
                archived_peaks.append(
                    [
                        peak.get("value"),
                        table_index(wait_sources, peak.get("source")),
                        table_index(wait_providers, peak.get("provider")),
                        peak.get("sample_count"),
                        peak.get("observed_at"),
                        peak.get("sample_expected"),
                        peak.get("sample_complete"),
                    ]
                    if isinstance(peak, dict)
                    else None
                )
                sample_peak = (row.get("archive_sample_wait_peaks") or {}).get(metric)
                archived_sample_peaks.append(
                    [
                        sample_peak.get("value"),
                        table_index(wait_sources, sample_peak.get("source")),
                        table_index(wait_providers, sample_peak.get("provider")),
                        sample_peak.get("sample_count"),
                        sample_peak.get("observed_at"),
                        sample_peak.get("sample_expected"),
                        sample_peak.get("sample_complete"),
                    ]
                    if isinstance(sample_peak, dict)
                    else None
                )
            official_wait = row.get("official_wait") or {}
            sample_wait = row.get("sample_wait") or {}
            encoded_queues.append([
                int(row.get("waiting") or 0),
                int(row.get("running") or 0),
                row.get("p50_wait"),
                row.get("p95_wait"),
                row.get("p99_wait"),
                table_index(wait_sources, row.get("p50_wait_source")),
                table_index(wait_sources, row.get("p95_wait_source")),
                table_index(wait_sources, row.get("p99_wait_source")),
                table_index(wait_providers, row.get("official_wait_source")),
                table_index(wait_providers, row.get("sample_wait_source")),
                row.get("wait_sample_count"),
                row.get("wait_sample_expected_count"),
                None if sample_complete is None else int(bool(sample_complete)),
                archived_peaks if any(archived_peaks) else None,
                [
                    official_wait.get("p50"),
                    official_wait.get("p95"),
                    official_wait.get("max"),
                ] if any(official_wait.get(metric) is not None for metric in ("p50", "p95", "max")) else None,
                [
                    sample_wait.get("p50"),
                    sample_wait.get("p95"),
                    sample_wait.get("p99"),
                ] if any(sample_wait.get(metric) is not None for metric in ("p50", "p95", "p99")) else None,
                archived_sample_peaks if any(archived_sample_peaks) else None,
                1 if row.get("history_observation_only") else None,
            ])
        points.append([snapshot.get("ts"), encoded_queues])

    return {
        "schema_version": 1,
        "generated_at": generated_at or (history[-1].get("ts") if history else None),
        "queue_names": queue_names,
        "wait_sources": wait_sources,
        "wait_providers": wait_providers,
        "row_fields": [
            "waiting", "running", "p50_wait", "p95_wait", "p99_wait",
            "p50_source", "p95_source", "p99_source", "official_provider",
            "sample_provider", "sample_count", "sample_expected", "sample_complete",
            "archive_wait_peaks", "official_wait", "sample_wait",
            "archive_sample_wait_peaks", "history_observation_only",
        ],
        "points": points,
    }


def _bounded_queue_history_chart(
    history: list[dict],
    generated_at: str | None = None,
    *,
    max_bytes: int = QUEUE_HISTORY_CHART_MAX_BYTES,
) -> tuple[dict, bytes]:
    """Keep the largest newest whole-snapshot suffix that fits the chart budget."""
    source = list(history)

    def candidate(retained_count: int) -> tuple[dict, bytes]:
        retained = source[len(source) - retained_count :] if retained_count else []
        payload = build_queue_history_chart(retained, generated_at)
        payload["publication_retention"] = {
            "policy": "newest_whole_snapshots_within_byte_budget_v1",
            "max_bytes": max_bytes,
            "source_snapshot_count": len(source),
            "published_snapshot_count": len(retained),
            "omitted_oldest_snapshot_count": len(source) - len(retained),
            "complete_relative_to_source": len(retained) == len(source),
            "retained_start": retained[0].get("ts") if retained else None,
            "retained_end": retained[-1].get("ts") if retained else None,
        }
        encoded = _encoded_json(payload).encode("utf-8")
        return payload, encoded

    low = 0
    high = len(source)
    best: tuple[dict, bytes] | None = None
    while low <= high:
        middle = (low + high) // 2
        payload, encoded = candidate(middle)
        if len(encoded) <= max_bytes:
            best = payload, encoded
            low = middle + 1
        else:
            high = middle - 1
    if best is None:
        _payload, encoded = candidate(0)
        raise RuntimeError(
            "Queue history chart metadata exceeds its byte budget; preserving "
            "the last-known-good file: "
            f"{len(encoded)} > {max_bytes} bytes"
        )
    return best


def write_queue_history_chart(
    path: Path,
    history: list[dict],
    generated_at: str | None = None,
) -> None:
    _payload, encoded = _bounded_queue_history_chart(history, generated_at)
    atomic_write_bytes(path, encoded)


def _diagnostic_section(payload: dict) -> dict:
    reliability = payload.get("reliability") or {}
    retry = reliability.get("retry_analysis") or {}
    amd_health = payload.get("amd_test_health") or {}
    queue = payload.get("queue") or {}
    return {
        "reliability": {
            key: reliability.get(key)
            for key in (
                "available",
                "source_pipeline",
                "cohort",
                "evidence_definitions",
                "denominator",
                "summary",
            )
        } | {
            "group_catalog": [
                {"id": row.get("id")}
                for row in reliability.get("group_catalog") or []
            ],
            "flaky_candidates": [
                {"id": row.get("id")}
                for row in reliability.get("flaky_candidates") or []
            ],
            "retry_analysis": {
                key: retry.get(key)
                for key in ("available", "summary", "provenance")
            },
        },
        "amd_test_health": {
            "summary": amd_health.get("summary") or {},
            "provenance": amd_health.get("provenance") or {},
        },
        "queue": {
            "history_summary": queue.get("history_summary") or {},
        },
    }


def _operations_shell(payload: dict) -> dict:
    nightly = _compact_nightly(payload.get("nightly") or {}, build_limit=7)
    nightly["pipelines"] = [
        row for row in nightly.get("pipelines") or []
        if row.get("pipeline") == AMD_TEST_PIPELINE and row.get("job_scope") == "amd_gpu" and row.get("hardware_scope") == "amd_mi_gpu"
    ]
    amd_health = payload.get("amd_test_health") or {}
    definition_parity = payload.get("definition_parity") or {}
    test_group_parity = payload.get("test_group_parity") or {}
    queue = payload.get("queue") or {}
    return {
        key: payload.get(key)
        for key in ("schema_version", "generated_at", "sources", "home", "attention")
    } | {
        "nightly": nightly,
        "amd_test_health": {"summary": amd_health.get("summary") or {}},
        "definition_parity": {
            "summary": definition_parity.get("summary") or {},
            "source": definition_parity.get("source") or {},
        },
        "test_group_parity": {
            key: test_group_parity.get(key)
            for key in (
                "schema_version",
                "generated_at",
                "reviewed_at",
                "source",
                "scope",
                "summary",
                "rocm_inventory",
            )
        },
        "queue": {
            "snapshot": queue.get("snapshot") or {},
            "history_summary": queue.get("history_summary") or {},
        },
    }


_RELIABILITY_INCIDENT_STATES = {
    "hard", "soft", "incident", "error", "failed", "failing", "soft_fail",
    "soft_failed", "timed_out", "broken", "canceled", "expired",
}
_RELIABILITY_OBSERVATION_KEYS = (
    "source_pipeline", "group_id", "build_number", "build_url", "build_kind",
    "commit", "message", "state", "result", "status", "terminal_state",
    "observed_at", "finished_at", "created_at", "date", "job_url", "url",
    "step_url", "job_id", "step_id", "queue", "wall_duration_mins",
    "test_duration_mins", "wait_mins", "end_to_end_mins", "duration_basis",
    "duration_mins", "tests", "passed_tests", "failed_tests", "skipped_tests",
)
_RELIABILITY_GROUP_SUMMARY_KEYS = (
    "source_pipeline", "id", "group_ids", "name", "raw_names", "step_key",
    "hardware", "queues", "build_count", "runs", "passed", "failed",
    "soft_failed", "incident_count", "incident_rate_pct", "fail_rate",
    "mixed_outcomes", "latest_state", "latest_observed_at", "latest_url",
    "last_incident", "green_streak", "nightly_green_streak",
    "median_wall_mins", "p90_wall_mins", "max_wall_mins", "median_test_mins",
    "p90_test_mins", "max_test_mins", "median_wait_mins", "p90_wait_mins",
    "max_wait_mins", "median_end_to_end_mins", "p90_end_to_end_mins",
    "max_end_to_end_mins", "median_dur", "p90_dur", "max_dur",
    "duration_basis", "observation_count", "retained_observation_count",
    "history_truncated", "excluded_observation_count",
    "linked_observation_count", "retry_evidence_observation_count",
    "evidence_type",
)
_RELIABILITY_RETRY_ROW_KEYS = (
    "source_pipeline", "group_id", "build_number", "step", "name", "state",
    "observed_at", "timestamp_source", "job_id", "url", "retry_type",
    "retry_source", "build_url", "job_url", "failed_job_id", "passed_job_id",
    "failed_url", "passed_url", "failed_job_url", "passed_job_url",
    "comparison_platform", "comparison_key", "comparison_group_id",
    "comparison_identity_method", "comparison_row_ids",
    "comparison_eligible_row_ids",
)


def _json_bytes(payload: Any) -> int:
    """Return the exact byte count used by public compact JSON files."""
    return len(_encoded_json(payload).encode("utf-8"))


def _bounded_public_text(value: Any, limit: int = 4096) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "\u2026"


def _reliability_row_state(row: dict) -> str:
    return str(
        row.get("state")
        or row.get("result")
        or row.get("status")
        or row.get("latest_state")
        or "unknown"
    ).lower()


def _reliability_incident(row: dict) -> bool:
    return _reliability_row_state(row) in _RELIABILITY_INCIDENT_STATES


def _reliability_row_stable_id(row: dict) -> str:
    return hashlib.sha256(_encoded_json(row).encode("utf-8")).hexdigest()


def _reliability_row_sort_key(row: dict, index: int = 0) -> tuple[str, int, str]:
    observed_at = str(
        row.get("observed_at")
        or row.get("finished_at")
        or row.get("created_at")
        or row.get("date")
        or row.get("latest_observed_at")
        or ""
    )
    build_number = row.get("build_number")
    if not isinstance(build_number, int) or isinstance(build_number, bool):
        build_number = 0
    # Never use source-list position as a cap-selection tie-break. Upstream
    # pagination and dictionary assembly can reorder otherwise identical
    # evidence, which must not churn the bounded public projection.
    return observed_at, build_number, _reliability_row_stable_id(row)


def _project_reliability_row(
    row: Any,
    keys: tuple[str, ...],
) -> tuple[dict | None, bool]:
    """Bound a single evidence row without allowing it to wedge publication."""
    if not isinstance(row, dict):
        return None, False
    if _json_bytes(row) <= OPERATIONS_RELIABILITY_ROW_MAX_BYTES:
        return row, False
    projected: dict[str, Any] = {}
    string_limit = max(
        32,
        min(4096, OPERATIONS_RELIABILITY_ROW_MAX_BYTES // 16),
    )
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            projected[key] = _bounded_public_text(value, string_limit)
        elif isinstance(value, (int, float, bool)):
            projected[key] = value
        elif key in {
            "comparison_row_ids", "comparison_eligible_row_ids",
        } and isinstance(value, list):
            projected[key] = [
                _bounded_public_text(item, 512)
                for item in value[:128]
                if isinstance(item, (str, int)) and not isinstance(item, bool)
            ]
    projected["publication_fields_truncated"] = True
    if _json_bytes(projected) > OPERATIONS_RELIABILITY_ROW_MAX_BYTES:
        return None, True
    return projected, True


def _project_group_summary(group: dict) -> tuple[dict | None, bool]:
    summary = {key: value for key, value in group.items() if key != "observations"}
    if _json_bytes(summary) <= OPERATIONS_RELIABILITY_GROUP_MAX_BYTES:
        return summary, False

    projected: dict[str, Any] = {}
    for key in _RELIABILITY_GROUP_SUMMARY_KEYS:
        value = group.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            projected[key] = _bounded_public_text(value)
        elif isinstance(value, (int, float, bool)):
            projected[key] = value
        elif key in {"group_ids", "raw_names", "queues"} and isinstance(value, list):
            projected[key] = [
                _bounded_public_text(item, 512)
                for item in value[:64]
                if isinstance(item, (str, int))
            ]
        elif key == "last_incident":
            incident, _truncated = _project_reliability_row(
                value,
                _RELIABILITY_OBSERVATION_KEYS,
            )
            if incident is not None:
                projected[key] = incident
    projected["publication_fields_truncated"] = True
    if _json_bytes(projected) > OPERATIONS_RELIABILITY_GROUP_MAX_BYTES:
        return None, True
    return projected, True


def _bounded_public_cohort(
    value: Any,
    *,
    max_bytes: int = 1024 * 1024,
) -> tuple[dict, bool]:
    cohort = value if isinstance(value, dict) else {}
    if _json_bytes(cohort) <= max_bytes:
        return cohort, False
    scalar_keys = (
        "id", "available", "label", "build_count",
        "canonical_nightly_build_count", "non_nightly_main_build_count",
        "other_main_build_count", "window_days", "observed_from", "observed_to",
        "selection",
    )
    projected: dict[str, Any] = {}
    for key in scalar_keys:
        item = cohort.get(key)
        if isinstance(item, str):
            projected[key] = _bounded_public_text(item)
        elif isinstance(item, (int, float, bool)) or item is None:
            projected[key] = item
    if isinstance(cohort.get("composition"), dict):
        projected["composition"] = {
            key: item
            for key, item in cohort["composition"].items()
            if isinstance(item, (int, float, bool)) or item is None
        }
    build_numbers = [
        item for item in cohort.get("build_numbers") or []
        if isinstance(item, int) and not isinstance(item, bool)
    ]
    projected["build_numbers"] = build_numbers[-8192:]
    projected["publication_build_numbers_truncated"] = (
        len(projected["build_numbers"]) < len(build_numbers)
    )
    while _json_bytes(projected) > max_bytes and projected["build_numbers"]:
        projected["build_numbers"] = projected["build_numbers"][
            len(projected["build_numbers"]) // 2:
        ]
        projected["publication_build_numbers_truncated"] = True
    if _json_bytes(projected) > max_bytes:
        projected.pop("build_numbers", None)
        projected["publication_build_numbers_truncated"] = True
    return projected, True


def _bounded_retry_provenance(value: Any) -> tuple[dict, bool]:
    provenance = value if isinstance(value, dict) else {}
    if _json_bytes(provenance) <= 512 * 1024:
        return provenance, False
    projected: dict[str, Any] = {}
    for key in (
        "source_path", "source_key", "source_pipeline", "complete", "reason",
        "evidence_kind",
    ):
        item = provenance.get(key)
        if isinstance(item, str):
            projected[key] = _bounded_public_text(item)
        elif isinstance(item, (int, float, bool)) or item is None:
            projected[key] = item
    numbers = [
        item for item in provenance.get("cohort_build_numbers") or []
        if isinstance(item, int) and not isinstance(item, bool)
    ]
    projected["cohort_build_numbers"] = numbers[-4096:]
    projected["publication_build_numbers_truncated"] = (
        len(projected["cohort_build_numbers"]) < len(numbers)
    )
    while (
        _json_bytes(projected) > 512 * 1024
        and projected["cohort_build_numbers"]
    ):
        retained = projected["cohort_build_numbers"]
        projected["cohort_build_numbers"] = retained[len(retained) // 2:]
        projected["publication_build_numbers_truncated"] = True
    return projected, True


def _retry_retention_metadata(
    source: dict,
    attempts: list[dict],
    recoveries: list[dict],
    *,
    source_bytes: int,
    sanitized_rows: int,
    provenance_compacted: bool,
) -> dict:
    source_attempts = source.get("retry_attempts") or []
    source_recoveries = source.get("failed_then_passed_recoveries") or []
    source_attempt_count = len(source_attempts) if isinstance(source_attempts, list) else 0
    source_recovery_count = (
        len(source_recoveries) if isinstance(source_recoveries, list) else 0
    )
    source_comparison_ids = {
        str(comparison_id)
        for row in (*source_attempts, *source_recoveries)
        if isinstance(row, dict)
        for key in ("comparison_row_ids", "comparison_eligible_row_ids")
        for comparison_id in (
            row.get(key) if isinstance(row.get(key), list) else []
        )
        if comparison_id not in (None, "")
    }
    published_comparison_ids = {
        str(comparison_id)
        for row in (*attempts, *recoveries)
        for key in ("comparison_row_ids", "comparison_eligible_row_ids")
        for comparison_id in (
            row.get(key) if isinstance(row.get(key), list) else []
        )
        if comparison_id not in (None, "")
    }
    complete = (
        len(attempts) == source_attempt_count
        and len(recoveries) == source_recovery_count
        and sanitized_rows == 0
        and not provenance_compacted
    )
    return {
        "schema_version": 1,
        "strategy": "newest-explicit-retry-evidence-v1",
        "max_bytes": OPERATIONS_RETRY_EVIDENCE_MAX_BYTES,
        "source_bytes": source_bytes,
        "complete_relative_to_source": complete,
        "retry_attempts": {
            "source": source_attempt_count,
            "published": len(attempts),
            "omitted": max(0, source_attempt_count - len(attempts)),
        },
        "recoveries": {
            "source": source_recovery_count,
            "published": len(recoveries),
            "omitted": max(0, source_recovery_count - len(recoveries)),
        },
        "comparison_groups": {
            "source": len(source_comparison_ids),
            "published": len(published_comparison_ids),
            "omitted": max(
                0,
                len(source_comparison_ids - published_comparison_ids),
            ),
        },
        "source_summary": {
            key: (source.get("summary") or {}).get(key)
            for key in (
                "builds_evaluated", "builds_with_retries", "retry_attempt_count",
                "failed_then_passed_recovery_count", "linked_retry_attempt_count",
                "linked_recovery_count",
            )
            if key in (source.get("summary") or {})
        },
        "sanitized_row_count": sanitized_rows,
        "provenance_compacted": provenance_compacted,
    }


def _bounded_public_retry_analysis(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    source_bytes = _json_bytes(source)
    source_attempts = source.get("retry_attempts") or []
    source_recoveries = source.get("failed_then_passed_recoveries") or []
    source_attempts = source_attempts if isinstance(source_attempts, list) else []
    source_recoveries = source_recoveries if isinstance(source_recoveries, list) else []
    complete_candidate = dict(source)
    complete_candidate["publication_retention"] = _retry_retention_metadata(
        source,
        source_attempts,
        source_recoveries,
        source_bytes=source_bytes,
        sanitized_rows=0,
        provenance_compacted=False,
    )
    wrapper_reserve = min(
        16 * 1024,
        max(128, OPERATIONS_RETRY_EVIDENCE_MAX_BYTES // 20),
    )
    payload_max_bytes = max(
        1,
        OPERATIONS_RETRY_EVIDENCE_MAX_BYTES - wrapper_reserve,
    )
    if _json_bytes(complete_candidate) <= payload_max_bytes:
        return complete_candidate

    provenance, provenance_compacted = _bounded_retry_provenance(
        source.get("provenance")
    )
    base = {
        key: source.get(key)
        for key in (
            "schema_version", "generated_at", "available", "evidence_type",
            "summary",
        )
        if key in source
    }
    base["provenance"] = provenance
    prepared: dict[str, list[dict]] = {"attempt": [], "recovery": []}
    for kind, rows in (
        ("attempt", source_attempts),
        ("recovery", source_recoveries),
    ):
        for index, row in enumerate(rows):
            projected, sanitized = _project_reliability_row(
                row,
                _RELIABILITY_RETRY_ROW_KEYS,
            )
            if projected is None:
                continue
            prepared[kind].append({
                "index": index,
                "row": projected,
                "sanitized": sanitized,
                "sort_key": _reliability_row_sort_key(projected, index),
                "comparison_ids": {
                    str(comparison_id)
                    for key in (
                        "comparison_row_ids", "comparison_eligible_row_ids",
                    )
                    for comparison_id in (
                        projected.get(key)
                        if isinstance(projected.get(key), list)
                        else []
                    )
                    if comparison_id not in (None, "")
                },
            })

    selected: set[tuple[str, int]] = set()
    addition_order: list[tuple[str, int]] = []
    empty_meta = _retry_retention_metadata(
        source,
        [],
        [],
        source_bytes=source_bytes,
        sanitized_rows=0,
        provenance_compacted=provenance_compacted,
    )
    empty_candidate = {
        **base,
        "retry_attempts": [],
        "failed_then_passed_recoveries": [],
        "publication_retention": empty_meta,
    }
    used = _json_bytes(empty_candidate)
    selection_limit = max(0, payload_max_bytes - wrapper_reserve)

    def add(item: dict, kind: str) -> None:
        nonlocal used
        identity = (kind, int(item["index"]))
        if identity in selected:
            return
        increment = _json_bytes(item["row"]) + 1
        if used + increment > selection_limit:
            return
        selected.add(identity)
        addition_order.append(identity)
        used += increment

    # Keep a current signal from each evidence kind before filling the shared
    # budget by recency. Recoveries win only an exact timestamp tie.
    for kind in ("recovery", "attempt"):
        if prepared[kind]:
            add(max(prepared[kind], key=lambda item: item["sort_key"]), kind)
    comparison_winners: dict[str, tuple[dict, str]] = {}
    for kind in ("recovery", "attempt"):
        for item in prepared[kind]:
            for comparison_id in item["comparison_ids"]:
                current = comparison_winners.get(comparison_id)
                if current is None or item["sort_key"] > current[0]["sort_key"]:
                    comparison_winners[comparison_id] = (item, kind)
    pinned = sorted(
        comparison_winners.values(),
        key=lambda pair: (pair[0]["sort_key"], pair[1] == "recovery"),
        reverse=True,
    )
    for item, kind in pinned:
        add(item, kind)
    optional = [
        (item, kind)
        for kind in ("recovery", "attempt")
        for item in prepared[kind]
        if (kind, int(item["index"])) not in selected
    ]
    optional.sort(key=lambda pair: (pair[1], pair[0]["index"]))
    optional.sort(
        key=lambda pair: (pair[0]["sort_key"], pair[1] == "recovery"),
        reverse=True,
    )
    for item, kind in optional:
        add(item, kind)

    def build_candidate() -> dict:
        selected_attempts = [
            item for item in prepared["attempt"]
            if ("attempt", int(item["index"])) in selected
        ]
        selected_recoveries = [
            item for item in prepared["recovery"]
            if ("recovery", int(item["index"])) in selected
        ]
        attempts = [
            item["row"]
            for item in sorted(
                selected_attempts,
                key=lambda item: item["sort_key"],
                reverse=True,
            )
        ]
        recoveries = [
            item["row"]
            for item in sorted(
                selected_recoveries,
                key=lambda item: item["sort_key"],
                reverse=True,
            )
        ]
        published_sanitized = sum(
            int(item["sanitized"])
            for kind in ("attempt", "recovery")
            for item in prepared[kind]
            if (kind, int(item["index"])) in selected
        )
        published_summary = dict(base.get("summary") or {})
        published_summary.update({
            "builds_with_retries": len({
                row.get("build_number")
                for row in attempts
                if row.get("build_number")
            }),
            "retry_attempt_count": len(attempts),
            "failed_then_passed_recovery_count": len(recoveries),
            "linked_retry_attempt_count": sum(
                bool(row.get("job_url")) for row in attempts
            ),
            "linked_recovery_count": sum(
                bool(row.get("failed_url") and row.get("passed_url"))
                for row in recoveries
            ),
        })
        return {
            **base,
            "summary": published_summary,
            "retry_attempts": attempts,
            "failed_then_passed_recoveries": recoveries,
            "publication_retention": _retry_retention_metadata(
                source,
                attempts,
                recoveries,
                source_bytes=source_bytes,
                sanitized_rows=published_sanitized,
                provenance_compacted=provenance_compacted,
            ),
        }

    candidate = build_candidate()
    while _json_bytes(candidate) > payload_max_bytes and addition_order:
        selected.remove(addition_order.pop())
        candidate = build_candidate()
    if _json_bytes(candidate) > payload_max_bytes:
        raise RuntimeError("Unable to build bounded public retry evidence")
    return candidate


def _published_group_row(descriptor: dict) -> dict:
    observations = [
        item["row"]
        for item in sorted(
            descriptor["observations"],
            key=lambda item: item["sort_key"],
        )
        if item["index"] in descriptor["selected"]
    ]
    source_count = descriptor["source_observation_count"]
    sanitized_count = sum(
        int(item["sanitized"])
        for item in descriptor["observations"]
        if item["index"] in descriptor["selected"]
    )
    row = dict(descriptor["summary"])
    source_history_truncated = bool(row.get("history_truncated"))
    excluded_observation_count = max(
        0, int(row.get("excluded_observation_count") or 0)
    )
    aggregate_observation_count = max(
        0, int(row.get("observation_count") or source_count)
    )
    source_history_complete = (
        not source_history_truncated
        and excluded_observation_count == 0
        and aggregate_observation_count == source_count
    )
    row.update({
        "retained_observation_count": len(observations),
        "history_truncated": source_history_truncated
        or len(observations) < source_count
        or sanitized_count > 0,
        "linked_observation_count": sum(
            bool(observation.get("job_url")) for observation in observations
        ),
        "observations": observations,
        "source_retained_observation_count": source_count,
        "publication_history_complete": (
            source_history_complete
            and len(observations) == source_count
            and sanitized_count == 0
        ),
        "publication_observation_fields_truncated_count": sanitized_count,
    })
    return row


def _bounded_public_group_catalog(
    value: Any,
    *,
    max_bytes: int = OPERATIONS_RELIABILITY_CATALOG_MAX_BYTES,
) -> tuple[list[dict], dict]:
    source_groups = value if isinstance(value, list) else []
    descriptors: list[dict] = []
    source_observations = 0
    source_incidents = 0
    unavailable_observations = 0
    unavailable_groups = 0
    for group_index, group in enumerate(source_groups):
        if not isinstance(group, dict):
            unavailable_groups += 1
            continue
        raw_observations = group.get("observations") or []
        raw_observations = (
            raw_observations if isinstance(raw_observations, list) else []
        )
        source_observations += len(raw_observations)
        source_incidents += sum(
            _reliability_incident(row)
            for row in raw_observations
            if isinstance(row, dict)
        )
        identity_value = group.get("id") or group.get("name")
        if identity_value in (None, ""):
            unavailable_groups += 1
            continue
        identity = str(identity_value)
        summary, summary_sanitized = _project_group_summary(group)
        if summary is None:
            unavailable_groups += 1
            continue
        observations = []
        for observation_index, observation in enumerate(raw_observations):
            projected, sanitized = _project_reliability_row(
                observation,
                _RELIABILITY_OBSERVATION_KEYS,
            )
            if projected is None:
                unavailable_observations += 1
                continue
            observations.append({
                "index": observation_index,
                "row": projected,
                "sanitized": sanitized,
                "incident": _reliability_incident(projected),
                "sort_key": _reliability_row_sort_key(
                    projected,
                    observation_index,
                ),
            })
        newest = max(observations, key=lambda item: item["sort_key"], default=None)
        selected = {int(newest["index"])} if newest is not None else set()
        canonical_key = (
            identity,
            hashlib.sha256(
                (
                    _encoded_json(summary)
                    + "|"
                    + "|".join(sorted(
                        _reliability_row_stable_id(item["row"])
                        for item in observations
                    ))
                ).encode("utf-8")
            ).hexdigest(),
        )
        descriptors.append({
            "index": group_index,
            "summary": summary,
            "summary_sanitized": summary_sanitized,
            "observations": observations,
            "selected": selected,
            "newest_index": int(newest["index"]) if newest is not None else None,
            "latest_sort_key": newest["sort_key"] if newest is not None else (
                str(group.get("latest_observed_at") or ""), 0, canonical_key[1]
            ),
            "current_incident": bool(
                newest and newest["incident"]
            ) or str(group.get("latest_state") or "").lower() in (
                _RELIABILITY_INCIDENT_STATES
            ),
            "has_incidents": bool(group.get("incident_count"))
            or any(item["incident"] for item in observations),
            "incident_count": int(group.get("incident_count") or 0),
            "identity": identity,
            "canonical_key": canonical_key,
            "source_observation_count": len(raw_observations),
        })

    # Stable identity ordering supplies deterministic ties; the second stable
    # sort makes current failures and recent groups survive first under pressure.
    descriptors.sort(key=lambda item: item["canonical_key"])
    descriptors.sort(
        key=lambda item: (
            item["current_incident"],
            item["has_incidents"],
            item["latest_sort_key"],
            item["incident_count"],
        ),
        reverse=True,
    )
    selected_groups: list[dict] = []
    selection_limit = max(0, max_bytes - 1024 * 1024)
    used = 3
    for descriptor in descriptors:
        row = _published_group_row(descriptor)
        row_bytes = _json_bytes(row)
        if row_bytes > OPERATIONS_RELIABILITY_GROUP_MAX_BYTES:
            unavailable_groups += 1
            continue
        increment = row_bytes + int(bool(selected_groups))
        if used + increment > selection_limit:
            continue
        selected_groups.append(descriptor)
        used += increment

    optional: list[tuple[dict, dict]] = []
    for descriptor in selected_groups:
        optional.extend(
            (descriptor, item)
            for item in descriptor["observations"]
            if item["index"] != descriptor["newest_index"]
        )
    optional.sort(
        key=lambda pair: (pair[0]["identity"], pair[1]["index"])
    )
    optional.sort(
        key=lambda pair: (pair[1]["incident"], pair[1]["sort_key"]),
        reverse=True,
    )
    addition_order: list[tuple[dict, int]] = []
    for descriptor, item in optional:
        increment = _json_bytes(item["row"]) + 1
        if used + increment > selection_limit:
            continue
        descriptor["selected"].add(int(item["index"]))
        addition_order.append((descriptor, int(item["index"])))
        used += increment

    def build_catalog() -> list[dict]:
        return [
            _published_group_row(descriptor)
            for descriptor in sorted(
                selected_groups,
                key=lambda item: item["canonical_key"],
            )
        ]

    catalog = build_catalog()
    while _json_bytes(catalog) > max_bytes and addition_order:
        descriptor, observation_index = addition_order.pop()
        descriptor["selected"].remove(observation_index)
        catalog = build_catalog()
    while _json_bytes(catalog) > max_bytes and selected_groups:
        selected_groups.pop()
        catalog = build_catalog()
    if _json_bytes(catalog) > max_bytes:
        raise RuntimeError("Unable to build bounded public reliability catalog")

    published_observations = sum(len(row.get("observations") or []) for row in catalog)
    published_incidents = sum(
        _reliability_incident(observation)
        for row in catalog
        for observation in row.get("observations") or []
    )
    sanitized_groups = sum(bool(item["summary_sanitized"]) for item in selected_groups)
    sanitized_observations = sum(
        int(item["sanitized"])
        for descriptor in selected_groups
        for item in descriptor["observations"]
        if item["index"] in descriptor["selected"]
    )
    return catalog, {
        "groups": {
            "source": len(source_groups),
            "published": len(catalog),
            "omitted": max(0, len(source_groups) - len(catalog)),
        },
        "observations": {
            "source": source_observations,
            "published": published_observations,
            "omitted": max(0, source_observations - published_observations),
        },
        "incident_observations": {
            "source": source_incidents,
            "published": published_incidents,
            "omitted": max(0, source_incidents - published_incidents),
        },
        "sanitized_group_count": sanitized_groups,
        "sanitized_observation_count": sanitized_observations,
        "unpublishable_group_count": unavailable_groups,
        "unpublishable_observation_count": unavailable_observations,
    }


def _reliability_group_summary(row: dict) -> dict:
    return {
        key: value
        for key, value in row.items()
        if key not in {
            "observations", "raw_names", "last_incident",
            "source_retained_observation_count", "publication_history_complete",
            "publication_observation_fields_truncated_count",
        }
    } | {
        "evidence_ref": row.get("id"),
        "last_incident": row.get("last_incident"),
    }


def _bounded_public_derived_rows(
    catalog: list[dict],
    source: dict,
) -> tuple[list[dict], dict, dict]:
    summaries = [_reliability_group_summary(row) for row in catalog]
    candidates = [row for row in summaries if row.get("mixed_outcomes")]
    candidates.sort(
        key=lambda row: (
            float(row.get("incident_rate_pct") or 0),
            int(row.get("incident_count") or 0),
            int(row.get("runs") or 0),
            str(row.get("name") or ""),
        ),
        reverse=True,
    )
    latency = [row for row in summaries if row.get("median_dur") is not None]
    lists = {
        "flaky_candidates": candidates,
        "by_median_duration": sorted(
            latency,
            key=lambda row: (
                float(row.get("median_dur") or 0), str(row.get("name") or "")
            ),
            reverse=True,
        ),
        "by_p90_duration": sorted(
            latency,
            key=lambda row: (
                float(row.get("p90_dur") or 0), str(row.get("name") or "")
            ),
            reverse=True,
        ),
        "by_max_duration": sorted(
            latency,
            key=lambda row: (
                float(row.get("max_dur") or 0), str(row.get("name") or "")
            ),
            reverse=True,
        ),
    }
    per_list_budget = OPERATIONS_RELIABILITY_DERIVED_MAX_BYTES // len(lists) - 4096
    published: dict[str, list[dict]] = {}
    for name, rows in lists.items():
        selected: list[dict] = []
        used = 3
        for row in rows:
            row_bytes = _json_bytes(row)
            if row_bytes > OPERATIONS_RELIABILITY_ROW_MAX_BYTES:
                continue
            increment = row_bytes + int(bool(selected))
            if used + increment > per_list_budget:
                continue
            selected.append(row)
            used += increment
        published[name] = selected
    latency_source = source.get("latency_rankings") or {}
    latency_source = latency_source if isinstance(latency_source, dict) else {}
    source_counts = {
        "flaky_candidates": len(source.get("flaky_candidates") or []),
        **{
            key: len(latency_source.get(key) or [])
            for key in (
                "by_median_duration", "by_p90_duration", "by_max_duration",
            )
        },
    }
    stats = {
        key: {
            "source": source_counts[key],
            "published": len(published[key]),
            "omitted": max(0, source_counts[key] - len(published[key])),
        }
        for key in source_counts
    }
    return (
        published["flaky_candidates"],
        {
            key: published[key]
            for key in (
                "by_median_duration", "by_p90_duration", "by_max_duration",
            )
        },
        stats,
    )


def _reliability_source_counts(reliability: dict) -> dict:
    groups = reliability.get("group_catalog") or []
    groups = groups if isinstance(groups, list) else []
    observations = [
        observation
        for group in groups
        if isinstance(group, dict)
        for observation in (
            group.get("observations")
            if isinstance(group.get("observations"), list)
            else []
        )
        if isinstance(observation, dict)
    ]
    return {
        "groups": len(groups),
        "observations": len(observations),
        "incident_observations": sum(
            _reliability_incident(observation) for observation in observations
        ),
    }


def _bounded_public_reliability(
    value: Any,
    *,
    max_bytes: int = OPERATIONS_RELIABILITY_SECTION_MAX_BYTES,
) -> dict:
    """Return an honest, deterministic, byte-bounded browser projection.

    Aggregate rates and denominators stay source-derived. Only exact evidence
    rows are reduced, and every reduction is declared both globally and on an
    affected group. The caller encodes every section before writing, so a hard
    failure here preserves the prior published generation.
    """
    source = value if isinstance(value, dict) else {}
    source_section_bytes = _json_bytes({"reliability": source})
    source_counts = _reliability_source_counts(source)
    source_retry = source.get("retry_analysis") or {}
    source_attempts = (
        source_retry.get("retry_attempts")
        if isinstance(source_retry, dict)
        and isinstance(source_retry.get("retry_attempts"), list)
        else []
    )
    source_recoveries = (
        source_retry.get("failed_then_passed_recoveries")
        if isinstance(source_retry, dict)
        and isinstance(source_retry.get("failed_then_passed_recoveries"), list)
        else []
    )
    complete_retry_retention = _retry_retention_metadata(
        source_retry if isinstance(source_retry, dict) else {},
        source_attempts,
        source_recoveries,
        source_bytes=_json_bytes(source_retry),
        sanitized_rows=0,
        provenance_compacted=False,
    )
    full = dict(source)
    full["publication_retention"] = {
        "schema_version": 1,
        "strategy": "bounded-browser-reliability-v1",
        "max_section_bytes": max_bytes,
        "source_section_bytes": source_section_bytes,
        "complete_relative_to_source": True,
        "compacted": False,
        "groups": {
            "source": source_counts["groups"],
            "published": source_counts["groups"],
            "omitted": 0,
        },
        "observations": {
            "source": source_counts["observations"],
            "published": source_counts["observations"],
            "omitted": 0,
        },
        "incident_observations": {
            "source": source_counts["incident_observations"],
            "published": source_counts["incident_observations"],
            "omitted": 0,
        },
        "retry_evidence": complete_retry_retention,
    }
    if _json_bytes({"reliability": full}) <= max_bytes:
        return full

    retry = _bounded_public_retry_analysis(source_retry)
    retry_retention = retry.get("publication_retention") or {}
    cohort, cohort_compacted = _bounded_public_cohort(source.get("cohort"))
    fixed = {
        key: source.get(key)
        for key in (
            "available", "source_pipeline", "evidence_definitions", "denominator",
            "summary",
        )
        if key in source
    }
    fixed["cohort"] = cohort
    catalog_budgets = (
        min(OPERATIONS_RELIABILITY_CATALOG_MAX_BYTES, max_bytes // 4 * 3),
        min(40 * 1024 * 1024, max_bytes // 8 * 5),
        min(24 * 1024 * 1024, max_bytes // 2),
        min(8 * 1024 * 1024, max_bytes // 4),
        min(2 * 1024 * 1024, max_bytes // 8),
    )
    last_size = 0
    for catalog_budget in dict.fromkeys(budget for budget in catalog_budgets if budget > 0):
        catalog, catalog_stats = _bounded_public_group_catalog(
            source.get("group_catalog"),
            max_bytes=catalog_budget,
        )
        flaky, latency, derived_stats = _bounded_public_derived_rows(catalog, source)
        complete = (
            catalog_stats["groups"]["omitted"] == 0
            and catalog_stats["observations"]["omitted"] == 0
            and catalog_stats["sanitized_group_count"] == 0
            and catalog_stats["sanitized_observation_count"] == 0
            and all(item["omitted"] == 0 for item in derived_stats.values())
            and bool(retry_retention.get("complete_relative_to_source", True))
            and not cohort_compacted
        )
        retention = {
            "schema_version": 1,
            "strategy": (
                "current-incident-groups-then-newest-incidents-then-newest-v1"
            ),
            "max_section_bytes": max_bytes,
            "catalog_max_bytes": catalog_budget,
            "source_section_bytes": source_section_bytes,
            "complete_relative_to_source": complete,
            "compacted": True,
            **catalog_stats,
            "derived_rows": derived_stats,
            "retry_evidence": retry_retention,
            "cohort_compacted": cohort_compacted,
        }
        candidate = {
            **fixed,
            "group_catalog": catalog,
            "flaky_candidates": flaky,
            "latency_rankings": latency,
            "retry_analysis": retry,
            "publication_retention": retention,
        }
        last_size = _json_bytes({"reliability": candidate})
        if last_size <= max_bytes:
            return candidate
    raise RuntimeError(
        "Unable to build bounded public reliability section: "
        f"{last_size} bytes exceeds {max_bytes} bytes"
    )


def _bounded_operations_collection_payload(
    value: Any,
    *,
    section_name: str,
    payload_key: str,
    collection_keys: tuple[str, ...],
    row_priority: Any,
    row_transform: Any = None,
) -> dict:
    """Bound independently growing row catalogs while preserving exact summaries."""
    source = value if isinstance(value, dict) else {}
    active_collection_keys = tuple(key for key in collection_keys if key in source)
    source_lists = {
        key: [row for row in source.get(key) or []]
        for key in active_collection_keys
    }
    tagged = [
        (key, index, row)
        for key in active_collection_keys
        for index, row in enumerate(source_lists[key])
    ]
    tagged.sort(
        key=lambda item: (
            row_priority(item[0], item[2]),
            item[0],
            item[1],
        )
    )
    source_retention = source.get("publication_retention") or {}
    source_complete = source_retention.get("complete_relative_to_source") is not False
    max_bytes = OPERATIONS_CANARY_SECTION_MAX_BYTES[section_name]

    def candidate(retained_count: int) -> tuple[dict, int]:
        selected = {(key, index) for key, index, _row in tagged[:retained_count]}
        result = dict(source)
        collections: dict[str, dict[str, Any]] = {}
        for key in active_collection_keys:
            rows = [
                (row_transform(key, row) if row_transform else row)
                for index, row in enumerate(source_lists[key])
                if (key, index) in selected
            ]
            result[key] = rows
            collections[key] = {
                "source": len(source_lists[key]),
                "published": len(rows),
                "omitted": len(source_lists[key]) - len(rows),
                "complete_relative_to_source": len(rows) == len(source_lists[key]),
            }
        result["operations_publication_retention"] = {
            "policy": "priority_whole_rows_with_exact_source_aggregates_v1",
            "max_bytes": max_bytes,
            "source_publication_complete": source_complete,
            "aggregate_summaries_complete": True,
            "collections": collections,
            "complete_relative_to_source": (
                source_complete and retained_count == len(tagged)
            ),
        }
        section = {payload_key: result}
        return result, _json_bytes(section)

    complete, size = candidate(len(tagged))
    if size <= max_bytes:
        return complete
    low = 0
    high = len(tagged)
    best: dict | None = None
    while low <= high:
        middle = (low + high) // 2
        current, current_size = candidate(middle)
        if current_size <= max_bytes:
            best = current
            low = middle + 1
        else:
            high = middle - 1
    if best is None:
        _empty, irreducible_size = candidate(0)
        raise RuntimeError(
            f"Operations {section_name} fixed aggregates exceed their section "
            f"budget: {irreducible_size} > {max_bytes} bytes"
        )
    return best


def _definition_row_priority(collection: str, row: Any) -> tuple[int, str]:
    priority = {
        "amd_only": 0,
        "nvidia_only": 0,
        "additional_variants": 1,
        "inline_mirror_variants": 2,
        "mirrors": 3,
        "matches": 4,
    }.get(collection, 5)
    return priority, json.dumps(row, sort_keys=True, separators=(",", ":"))


def _test_group_row_priority(collection: str, row: Any) -> tuple[int, str]:
    state = str((row or {}).get("state") or "") if isinstance(row, dict) else ""
    priority = -1 if collection == "areas" else {
        "action": 0,
        "unsupported": 1,
        "existing": 2,
    }.get(state, 3)
    return priority, json.dumps(row, sort_keys=True, separators=(",", ":"))


def _ownership_row_priority(collection: str, row: Any) -> tuple[int, str]:
    item = row if isinstance(row, dict) else {}
    counts = item.get("counts") or {}
    if collection == "unmapped_targets":
        priority = 2
    elif int(counts.get("incidents") or 0):
        priority = 0
    elif int(counts.get("pending_soft") or 0):
        priority = 1
    elif int(counts.get("upstream_parity_gaps") or 0):
        priority = 2
    else:
        priority = 3
    return priority, json.dumps(row, sort_keys=True, separators=(",", ":"))


def _compact_operations_ownership_row(collection: str, row: Any) -> Any:
    if collection != "areas" or not isinstance(row, dict):
        return row
    compact = dict(row)
    # ``targets`` duplicates the exact counts and the incident/pending lists;
    # no Operations consumer reads it.
    compact.pop("targets", None)
    return compact


def _operation_sections(payload: dict) -> dict[str, dict]:
    reliability = payload.get("reliability") or {}
    public_reliability = _bounded_public_reliability(reliability)
    definition_parity = _bounded_operations_collection_payload(
        payload.get("definition_parity") or {},
        section_name="definition_parity",
        payload_key="definition_parity",
        collection_keys=(
            "matches",
            "inline_mirror_variants",
            "additional_variants",
            "amd_only",
            "nvidia_only",
            "mirrors",
            "amd_execution_definitions",
        ),
        row_priority=_definition_row_priority,
    )
    test_group_parity = _bounded_operations_collection_payload(
        payload.get("test_group_parity") or {},
        section_name="test_group_parity",
        payload_key="test_group_parity",
        collection_keys=("areas", "groups"),
        row_priority=_test_group_row_priority,
    )
    ownership = _bounded_operations_collection_payload(
        payload.get("ownership") or {},
        section_name="ownership",
        payload_key="ownership",
        collection_keys=("areas", "unmapped_targets"),
        row_priority=_ownership_row_priority,
        row_transform=_compact_operations_ownership_row,
    )
    queue = compact_queue_section(_compact_queue(payload.get("queue") or {}))
    return {
        "nightly": {"nightly": _compact_nightly(payload.get("nightly") or {})},
        "amd_test_health": {
            "amd_test_health": _bounded_operations_amd_test_health(
                payload.get("amd_test_health") or {}
            )
        },
        "amd_agent_health": {
            "amd_agent_health": _bounded_operations_agent_health(
                payload.get("amd_agent_health") or {}
            )
        },
        "reliability": {"reliability": public_reliability},
        "comparison": {"latency": project_public_nightly_latency(
            payload.get("latency") or {},
            max_bytes=OPERATIONS_CANARY_SECTION_MAX_BYTES["comparison"],
        )},
        "definition_parity": {"definition_parity": definition_parity},
        "test_group_parity": {"test_group_parity": test_group_parity},
        "ownership": {"ownership": ownership},
        "queue": queue,
        "omni": {"omni": payload.get("omni") or {}},
        "diagnostics": _diagnostic_section(payload),
    }


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _encoded_json(payload: Any) -> str:
    # Browser JSON.parse rejects the non-standard NaN/Infinity constants that
    # Python's encoder otherwise emits by default. Fail before any generation
    # files are mutated so the last browser-consumable bundle remains intact.
    return (
        json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    )


def _snapshot_format(path: Path) -> str:
    if path.name.endswith(OPERATIONS_GZIP_SUFFIX):
        return "gzip"
    if path.name.endswith(OPERATIONS_RAW_SUFFIX):
        return "raw"
    raise ValueError(
        "Operations snapshot path must end in .json or .json.gz: "
        f"{path}"
    )


def _deterministic_gzip(raw: bytes) -> bytes:
    """Return a reproducible gzip member with no path or wall-clock metadata."""
    buffer = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        compresslevel=9,
        fileobj=buffer,
        mtime=0,
    ) as stream:
        stream.write(raw)
    return buffer.getvalue()


def load_snapshot_payload(path: Path) -> dict:
    """Load one bounded raw or gzip Operations source.

    The read limit applies to the uncompressed JSON and the gzip input has a
    separate on-disk ceiling. Reading at most ``limit + 1`` bytes lets us reject
    oversized streams without materializing the rest of a gzip bomb.
    """
    snapshot_format = _snapshot_format(path)
    if path.is_symlink():
        raise ValueError(f"Operations snapshot cannot be a symlink: {path}")
    size = path.stat().st_size
    if snapshot_format == "gzip":
        if size > OPERATIONS_GZIP_MAX_BYTES:
            raise RuntimeError(
                f"Compressed Operations snapshot is {size} bytes; limit is "
                f"{OPERATIONS_GZIP_MAX_BYTES} bytes"
            )
        with gzip.open(path, "rb") as stream:
            raw = stream.read(OPERATIONS_DECOMPRESSED_MAX_BYTES + 1)
    else:
        if size > OPERATIONS_DECOMPRESSED_MAX_BYTES:
            raise RuntimeError(
                f"Raw Operations snapshot is {size} bytes; read limit is "
                f"{OPERATIONS_DECOMPRESSED_MAX_BYTES} bytes"
            )
        raw = path.read_bytes()

    if len(raw) > OPERATIONS_DECOMPRESSED_MAX_BYTES:
        raise RuntimeError(
            "Operations snapshot expands to more than "
            f"{OPERATIONS_DECOMPRESSED_MAX_BYTES} bytes"
        )
    payload = json.loads(
        raw.decode("utf-8"),
        parse_constant=_reject_nonfinite_json_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("Operations snapshot must contain a JSON object")
    return payload


def _alternate_snapshot_path(path: Path) -> Path:
    if path.name.endswith(OPERATIONS_GZIP_SUFFIX):
        return path.with_name(path.name.removesuffix(".gz"))
    return path.with_name(f"{path.name}.gz")


def _org_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _org_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _org_source(payload: dict, name: str) -> dict:
    source = ((payload.get("sources") or {}).get(name) or {})
    return {
        "path": source.get("path"),
        "generated_at": source.get("timestamp"),
    }


def _org_daily_wait_days(
    value: Any,
    retention_start: datetime | None,
    retention_end: datetime | None,
) -> list[dict] | None:
    """Validate and project the lifecycle collector's per-day wait vectors."""
    if (
        not isinstance(value, list)
        or not value
        or retention_start is None
        or retention_end is None
        or retention_start >= retention_end
    ):
        return None

    retention_start = retention_start.astimezone(timezone.utc)
    retention_end = retention_end.astimezone(timezone.utc)
    cursor = retention_start.replace(hour=0, minute=0, second=0, microsecond=0)
    projected: list[dict] = []
    for raw in value:
        if cursor >= retention_end or not isinstance(raw, dict):
            return None
        day = str(raw.get("date") or "")
        start = str(raw.get("start") or "")
        end_exclusive = str(raw.get("end_exclusive") or "")
        partial = raw.get("partial")
        sample_count = raw.get("sample_count")
        raw_waits = raw.get("served_job_wait_seconds")
        vector_complete = raw.get("vector_complete", True)
        calendar_end = cursor + timedelta(days=1)
        expected_start = max(cursor, retention_start)
        expected_end = min(calendar_end, retention_end)
        if (
            day != cursor.date().isoformat()
            or start != _utc_iso(expected_start)
            or end_exclusive != _utc_iso(expected_end)
            or not isinstance(partial, bool)
            or partial != (expected_start != cursor or expected_end != calendar_end)
            or type(sample_count) is not int
            or sample_count < 0
            or not isinstance(raw_waits, list)
            or not isinstance(vector_complete, bool)
        ):
            return None
        waits = [float(wait) for wait in raw_waits if type(wait) in (int, float)]
        if (
            len(waits) != len(raw_waits)
            or any(not math.isfinite(wait) or wait < 0 for wait in waits)
            or waits != sorted(waits)
        ):
            return None
        projected_row = {
            "date": day,
            "start": start,
            "end_exclusive": end_exclusive,
            "partial": partial,
            "sample_count": sample_count,
            "served_job_wait_seconds": waits,
        }
        if vector_complete:
            if sample_count != len(waits):
                return None
        else:
            published_count = raw.get("published_sample_count")
            omitted_count = raw.get("omitted_sample_count")
            distribution = raw.get("distribution")
            if (
                type(published_count) is not int
                or published_count < 0
                or published_count != len(waits)
                or type(omitted_count) is not int
                or omitted_count <= 0
                or published_count + omitted_count != sample_count
                or not isinstance(distribution, dict)
                or distribution.get("count") != sample_count
            ):
                return None
            distribution_values = [
                distribution.get(field) for field in ("min", "p50", "p95", "avg", "max")
            ]
            if (
                any(type(value) not in (int, float) for value in distribution_values)
                or any(
                    not math.isfinite(float(value)) or float(value) < 0
                    for value in distribution_values
                )
                or not distribution["min"]
                <= distribution["p50"]
                <= distribution["p95"]
                <= distribution["max"]
                or not distribution["min"] <= distribution["avg"] <= distribution["max"]
            ):
                return None
            projected_row.update(
                {
                    "vector_complete": False,
                    "published_sample_count": published_count,
                    "omitted_sample_count": omitted_count,
                    "distribution": distribution,
                }
            )
        projected.append(projected_row)
        cursor = calendar_end
    return projected if cursor >= retention_end else None


def build_org_summary(payload: dict, queue_lifecycle: dict | None = None) -> dict:
    """Build the stable, compact CI contract used by organization rollups.

    Current main configuration coverage and observed AMD runtime groups are
    separate populations. Runtime health uses AMD GPU jobs from ci, with the
    same logical-group denominator as the dashboard.
    """
    queue_lifecycle = queue_lifecycle or {}
    amd_summary = ((payload.get("amd_test_health") or {}).get("summary") or {})
    logical = amd_summary.get("latest_test_group_counts") or {}
    logical_build = _org_int(logical.get("build_number"))
    latest_variant_build = _org_int(amd_summary.get("latest_build_number"))
    job_variant_build = _org_int(logical.get("job_variant_build_number"))
    test_signal_build = _org_int(logical.get("test_signal_build_number"))
    aligned_logical_builds = {
        value
        for value in (
            logical_build,
            latest_variant_build,
            job_variant_build,
            test_signal_build,
        )
        if value is not None
    }
    logical_available = (
        (payload.get("amd_test_health") or {}).get("source_pipeline") == "ci"
        and (payload.get("amd_test_health") or {}).get("hardware_scope") == "amd_mi_gpu"
        and _pipeline_build_url_matches(amd_summary.get("latest_build_url"), "ci", logical_build)
        and bool(logical.get("available"))
    ) and (
        len(aligned_logical_builds) == 1
        and None not in (
            logical_build,
            latest_variant_build,
            job_variant_build,
            test_signal_build,
        )
    )
    logical_reason = logical.get("reason")
    if not logical_available and not logical_reason:
        logical_reason = "build_mismatch" if len(aligned_logical_builds) > 1 else "unavailable"
    job_states = amd_summary.get("latest_job_variant_state_counts") or {}
    test_group_parity = payload.get("test_group_parity") or {}
    test_group_parity_summary = test_group_parity.get("summary") or {}

    queue_snapshot = ((payload.get("queue") or {}).get("snapshot") or {})
    target_scope = queue_snapshot.get("target_queue_scope") or {}
    target_totals = ((queue_snapshot.get("scope_totals") or {}).get("target") or {})
    queue_map = queue_snapshot.get("queues") or {}
    queue_ids = sorted(str(name) for name in (target_scope.get("queue_ids") or []))
    queue_rows = []
    for queue_id in queue_ids:
        if not _is_amd_queue(queue_id):
            continue
        row = queue_map.get(queue_id) or {}
        queue_rows.append({
            "queue": queue_id,
            "waiting_jobs": _org_int(row.get("waiting")),
            "running_jobs": _org_int(row.get("running")),
            "zombie_waiting_jobs": _org_int(row.get("zombie_waiting")),
            "zombie_running_jobs": _org_int(row.get("zombie_running")),
            "current_wait_minutes": {
                "p50": _org_float(row.get("p50_wait")),
                "p95": _org_float(row.get("p95_wait")),
                "max": _org_float(row.get("max_wait")),
            },
            "count_source": row.get("count_source"),
            "wait_source": row.get("wait_source"),
        })

    def maximum(field: str) -> float | None:
        values = [
            value
            for row in queue_rows
            if (value := _org_float(row["current_wait_minutes"].get(field)))
            is not None
        ]
        return max(values) if values else None

    lifecycle_totals = queue_lifecycle.get("totals") or {}
    lifecycle_window = queue_lifecycle.get("window") or {}
    lifecycle_coverage = queue_lifecycle.get("coverage") or {}
    lifecycle_retention = queue_lifecycle.get("retention") or {}
    lifecycle_ledger_scope = lifecycle_retention.get("ledger_scope")
    if not isinstance(lifecycle_ledger_scope, dict):
        lifecycle_ledger_scope = None
    lifecycle_scope = queue_lifecycle.get("scope") or {}
    lifecycle_daily_waits = queue_lifecycle.get("daily_wait_times") or {}
    lifecycle_retention_days = (
        lifecycle_retention.get("days")
        if type(lifecycle_retention.get("days")) is int
        else None
    )
    lifecycle_retention_start = _parse_dt(lifecycle_retention.get("event_start"))
    lifecycle_retention_end = _parse_dt(lifecycle_retention.get("end_exclusive"))
    lifecycle_generated_at = _parse_dt(queue_lifecycle.get("generated_at"))
    daily_wait_days = _org_daily_wait_days(
        lifecycle_daily_waits.get("days"),
        lifecycle_retention_start,
        lifecycle_retention_end,
    )
    daily_wait_sample_count = (
        sum(day["sample_count"] for day in daily_wait_days)
        if daily_wait_days is not None
        else None
    )
    # The independently bounded queue_lifecycle.json source owns daily detail.
    # Its normal rows contain exact vectors; pathological-volume rows expose
    # exact distribution summaries plus explicit omitted-vector coverage while
    # the manifest-bound ledger retains the underlying observations. Keep only
    # the validated day index here. Duplicating vectors in this compact contract
    # previously allowed the organization summary to cross its 2 MiB ceiling.
    daily_wait_day_index = (
        [
            ({
                key: day[key]
                for key in (
                    "date",
                    "start",
                    "end_exclusive",
                    "partial",
                    "sample_count",
                )
            } | ({
                key: day[key]
                for key in (
                    "vector_complete",
                    "published_sample_count",
                    "omitted_sample_count",
                    "distribution",
                )
            } if day.get("vector_complete") is False else {}))
            for day in daily_wait_days
        ]
        if daily_wait_days is not None
        else None
    )
    hourly_wait_counts: list[int] = []
    lifecycle_hourly = queue_lifecycle.get("hourly")
    if isinstance(lifecycle_hourly, list) and lifecycle_hourly:
        for bucket in lifecycle_hourly:
            count = (((bucket or {}).get("totals") or {}).get("queue_wait_seconds") or {}).get(
                "count"
            ) if isinstance(bucket, dict) else None
            if type(count) is not int or count < 0:
                hourly_wait_counts = []
                break
            hourly_wait_counts.append(count)
    hourly_wait_sample_count = sum(hourly_wait_counts) if hourly_wait_counts else None
    served_samples_exact = (
        (((lifecycle_coverage.get("metric_exhaustiveness") or {}).get("served") or {}).get(
            "exact_for_observed_events"
        ))
        is True
    )
    lifecycle_scope_queues = sorted(
        str(queue_id) for queue_id in (lifecycle_scope.get("queues") or [])
    )
    daily_wait_metadata_valid = (
        lifecycle_daily_waits.get("unit") == "seconds"
        and lifecycle_daily_waits.get("day_timezone") == "UTC"
        and lifecycle_daily_waits.get("attributed_by") == "timestamps.started_at"
        and lifecycle_retention_days is not None
        and lifecycle_retention_days > 0
        and lifecycle_retention_start is not None
        and lifecycle_retention_end is not None
        and lifecycle_retention_start < lifecycle_retention_end
        and lifecycle_generated_at == lifecycle_retention_end
        and lifecycle_retention_end - lifecycle_retention_start
        == timedelta(days=lifecycle_retention_days)
        and daily_wait_sample_count == hourly_wait_sample_count
        and served_samples_exact
    )
    daily_wait_available = bool(
        queue_lifecycle.get("generated_at")
        and queue_ids
        and lifecycle_scope_queues == queue_ids
        and daily_wait_metadata_valid
        and daily_wait_days is not None
    )
    if not lifecycle_daily_waits:
        daily_wait_reason = "daily_wait_times_unavailable"
    elif not queue_lifecycle.get("generated_at"):
        daily_wait_reason = "queue_lifecycle_source_unavailable"
    elif not queue_ids or lifecycle_scope_queues != queue_ids:
        daily_wait_reason = "queue_scope_mismatch"
    elif not daily_wait_metadata_valid or daily_wait_days is None:
        daily_wait_reason = "invalid_daily_wait_times"
    else:
        daily_wait_reason = None
    lifecycle_available = bool(lifecycle_window and lifecycle_totals)
    current_queue_available = bool(
        queue_snapshot.get("ts")
        and queue_ids
        and target_scope.get("all_rows_present") is True
        and _org_int(target_totals.get("waiting")) is not None
        and _org_int(target_totals.get("running")) is not None
    )

    exact_total = _org_int(amd_summary.get("latest_job_variant_count"))
    exact_passing = _org_int(job_states.get("passed"))
    exact_non_passing = (
        exact_total - exact_passing
        if exact_total is not None and exact_passing is not None
        else None
    )
    result = {
        "schema_id": "oss-project-ci-summary",
        "schema_version": ORG_SUMMARY_SCHEMA_VERSION,
        "generated_at": payload.get("generated_at"),
        "project": {
            "id": "vllm",
            "name": "vLLM",
            "repository": "https://github.com/vllm-project/vllm",
            "dashboard": "https://andreaskaratzas.github.io/vllm-ci-dashboard/",
            "summary_url": (
                "https://andreaskaratzas.github.io/vllm-ci-dashboard/"
                "data/vllm/ci/org_summary.json"
            ),
        },
        "test_groups": {
            "observed_latest_amd": {
                "available": logical_available,
                "reason": None if logical_available else logical_reason,
                "pipeline": "ci",
                "job_scope": "amd_gpu",
                "build_number": logical_build,
                "build_url": amd_summary.get("latest_build_url"),
                "total": _org_int(logical.get("total")) if logical_available else None,
                "green": _org_int(logical.get("passing")) if logical_available else None,
                "non_green": (
                    _org_int(logical.get("non_passing"))
                    if logical_available
                    else None
                ),
                "green_on_all_observed_hardware": (
                    _org_int(logical.get("passing_all"))
                    if logical_available
                    else None
                ),
                "mixed_by_hardware": (
                    _org_int(logical.get("partial")) if logical_available else None
                ),
                "green_rate_pct": (
                    _org_float(logical.get("pass_rate_pct"))
                    if logical_available
                    else None
                ),
                "green_policy": "passes_on_any_observed_amd_hardware_route",
                "count_basis": str(
                    logical.get("count_basis")
                    or AMD_OBSERVED_UNIQUE_TEST_GROUPS_COUNT_BASIS
                ),
            },
            "exact_job_variants_latest_amd": {
                "available": exact_total is not None,
                "build_number": _org_int(amd_summary.get("latest_build_number")),
                "total": exact_total,
                "green": exact_passing,
                "non_green": exact_non_passing,
                "state_counts": {
                    "passed": exact_passing,
                    "soft_failed": _org_int(job_states.get("soft")),
                    "hard_failed": _org_int(job_states.get("hard")),
                    "unknown": _org_int(job_states.get("unknown")),
                },
                "count_basis": "exact Buildkite job name",
            },
        },
        "test_group_parity": {
            "available": bool(test_group_parity_summary),
            "schema_version": (
                _org_int(test_group_parity.get("schema_version"))
                if test_group_parity_summary
                else None
            ),
            "reviewed_at": test_group_parity.get("reviewed_at"),
            "summary": test_group_parity_summary,
            "rocm_inventory": test_group_parity.get("rocm_inventory") or {},
            "source": test_group_parity.get("source") or {},
            "scope": test_group_parity.get("scope") or {},
        },
        "health_checks": {
            "best_hardware": {
                "available": logical_available,
                "reason": None if logical_available else logical_reason,
                "pipeline": "ci",
                "job_scope": "amd_gpu",
                "build_number": logical_build,
                "build_url": amd_summary.get("latest_build_url"),
                "total": _org_int(logical.get("total")) if logical_available else None,
                "green": _org_int(logical.get("passing")) if logical_available else None,
                "non_green": _org_int(logical.get("non_passing")) if logical_available else None,
                "green_rate_pct": _org_float(logical.get("pass_rate_pct")) if logical_available else None,
                "green_policy": "passes_on_any_observed_amd_hardware_route",
                "denominator_policy": "logical_groups_observed_on_amd_gpu_routes_in_ci",
            },
        },
        "queues": {
            "scope": {
                "id": target_scope.get("id"),
                "queue_count": _org_int(target_scope.get("queue_count")),
                "families": target_scope.get("families") or [],
                "gpu_widths": target_scope.get("gpu_widths") or [],
                "queue_ids": queue_ids,
            },
            "current": {
                "available": current_queue_available,
                "reason": None if current_queue_available else "target_scope_unavailable",
                "observed_at": queue_snapshot.get("ts"),
                "waiting_jobs": (
                    _org_int(target_totals.get("waiting"))
                    if current_queue_available
                    else None
                ),
                "running_jobs": (
                    _org_int(target_totals.get("running"))
                    if current_queue_available
                    else None
                ),
                "queues_with_waiting_jobs": (
                    sum(1 for row in queue_rows if (row.get("waiting_jobs") or 0) > 0)
                    if current_queue_available
                    else None
                ),
                "zombie_waiting_jobs": (
                    sum(row.get("zombie_waiting_jobs") or 0 for row in queue_rows)
                    if current_queue_available
                    else None
                ),
                "zombie_running_jobs": (
                    sum(row.get("zombie_running_jobs") or 0 for row in queue_rows)
                    if current_queue_available
                    else None
                ),
                "count_source": (
                    target_totals.get("count_source")
                    if current_queue_available
                    else None
                ),
                "maximum_across_queues_wait_minutes": {
                    "p50": maximum("p50") if current_queue_available else None,
                    "p95": maximum("p95") if current_queue_available else None,
                    "max": maximum("max") if current_queue_available else None,
                },
            },
            "by_queue": queue_rows,
            "recent_completed_window": {
                "available": lifecycle_available,
                "generated_at": queue_lifecycle.get("generated_at"),
                "start": lifecycle_window.get("start"),
                "end_exclusive": lifecycle_window.get("end_exclusive"),
                "hours": _org_int(lifecycle_window.get("hours")),
                "incoming_jobs": _org_int(lifecycle_totals.get("incoming")),
                "served_jobs": _org_int(lifecycle_totals.get("served")),
                "completed_jobs": _org_int(lifecycle_totals.get("completed")),
                "green_jobs": _org_int(lifecycle_totals.get("passed")),
                "green_rate_pct": _org_float(lifecycle_totals.get("pass_rate_pct")),
                "coverage": {
                    "status": lifecycle_coverage.get("status"),
                    "complete": bool(lifecycle_coverage.get("complete")),
                    "reason": lifecycle_coverage.get("reason"),
                } | ({
                    "byte_limited": lifecycle_ledger_scope.get("byte_limited") is True,
                    "complete_relative_to_configured_window": (
                        lifecycle_ledger_scope.get(
                            "complete_relative_to_configured_window"
                        )
                        is True
                    ),
                    "actual_published_latest_event_start": lifecycle_ledger_scope.get(
                        "published_latest_event_start"
                    ),
                    "actual_published_latest_event_end": lifecycle_ledger_scope.get(
                        "published_latest_event_end"
                    ),
                } if lifecycle_ledger_scope is not None else {}),
            },
            "daily_served_job_waits": {
                "available": daily_wait_available,
                "reason": None if daily_wait_available else daily_wait_reason,
                "source_generated_at": queue_lifecycle.get("generated_at"),
                "timezone": "UTC",
                "unit": "seconds",
                "sample_order": "ascending",
                "sample_definition": (
                    "started_at - runnable_at; no sample is emitted unless both "
                    "direct Buildkite timestamps exist"
                ),
                "population": (
                    "one observed Buildkite job attempt in queues.scope, assigned "
                    "to the UTC date containing started_at; retries remain separate"
                ),
                "retention": {
                    "kind": "rolling",
                    "days": lifecycle_retention_days,
                    "start": lifecycle_retention.get("event_start"),
                    "end_exclusive": lifecycle_retention.get("end_exclusive"),
                } | ({
                    "byte_limited": lifecycle_ledger_scope.get("byte_limited") is True,
                    "complete_relative_to_configured_window": (
                        lifecycle_ledger_scope.get(
                            "complete_relative_to_configured_window"
                        )
                        is True
                    ),
                    "actual_published_latest_event_start": lifecycle_ledger_scope.get(
                        "published_latest_event_start"
                    ),
                    "actual_published_latest_event_end": lifecycle_ledger_scope.get(
                        "published_latest_event_end"
                    ),
                    "omitted_whole_latest_event_days": lifecycle_ledger_scope.get(
                        "omitted_whole_latest_event_days"
                    ),
                    "partial_latest_event_day": lifecycle_ledger_scope.get(
                        "partial_latest_event_day"
                    ),
                    "ledger_scope": lifecycle_ledger_scope,
                } if lifecycle_ledger_scope is not None else {}),
                "coverage": {
                    "status": lifecycle_coverage.get("status"),
                    "complete": bool(lifecycle_coverage.get("complete")),
                    "exact_for_observed_samples": bool(
                        (((lifecycle_coverage.get("metric_exhaustiveness") or {}).get(
                            "served"
                        ) or {}).get("exact_for_observed_events"))
                    ),
                    "reason": lifecycle_coverage.get("reason"),
                } | ({
                    "byte_limited": lifecycle_ledger_scope.get("byte_limited") is True,
                    "complete_relative_to_configured_window": (
                        lifecycle_ledger_scope.get(
                            "complete_relative_to_configured_window"
                        )
                        is True
                    ),
                } if lifecycle_ledger_scope is not None else {}),
                "sample_count": (
                    daily_wait_sample_count if daily_wait_available else None
                ),
                "days": daily_wait_day_index if daily_wait_available else [],
                "source": {
                    "path": QUEUE_LIFECYCLE_NAME,
                    "schema_version": queue_lifecycle.get("schema_version"),
                    "key": "daily_wait_times.days",
                    "vector_key": "served_job_wait_seconds",
                },
            },
        },
        "definitions": {
            "test_group": (
                "A unique logical test-group identity observed in a run. "
                "On a commit-aligned AMD run, its normalized label and agent pool "
                "resolve the source identity family, so topology-distinct routes "
                "remain separate while equivalent hardware routes and configured "
                "%N shards collapse into one group."
            ),
            "job_variant": (
                "One exact Buildkite job name; replicas and shards remain separate."
            ),
            "upstream_test_group_parity": (
                "Logical CUDA groups derived from the immutable current main CI "
                "configuration. AMD route coverage, required blocking gates, "
                "optional routes, unsupported work, and gaps remain separate."
            ),
            "health_check": (
                "One logical group observed on AMD GPU hardware in ci. It is green "
                "when any complete observed hardware route passes."
            ),
            "queue_waiting_job": "A Buildkite job in the SCHEDULED state.",
            "queue_running_job": (
                "A Buildkite job assigned, accepted, running, canceling, or timing out."
            ),
            "served_job_wait_sample": (
                "One observed job attempt's started_at minus runnable_at duration, "
                "assigned to the UTC date of started_at. Normal lifecycle days retain "
                "every sample and duplicate value in an exact vector. A byte-bounded "
                "day declares vector_complete=false and instead publishes an exact, "
                "ledger-reconciled distribution plus its omitted-sample count."
            ),
        },
        "sources": {
            "operations": {
                "path": "operations_v2_manifest.json",
                "generated_at": payload.get("generated_at"),
            },
            "ci_health": _org_source(payload, "ci_health"),
            "amd_test_matrix": _org_source(payload, "amd_test_matrix"),
            "test_group_parity": _org_source(payload, "test_group_parity"),
            "capacity_monitor": _org_source(payload, "capacity_monitor"),
            "queue_timeseries": _org_source(payload, "queue_timeseries"),
            "queue_lifecycle": {
                "path": QUEUE_LIFECYCLE_NAME,
                "generated_at": queue_lifecycle.get("generated_at"),
            },
        },
    }
    return _bounded_org_summary(result)


def _bounded_org_summary(
    value: dict,
    *,
    max_bytes: int = ORG_SUMMARY_MAX_BYTES,
) -> dict:
    """Retain exact rollups and the largest deterministic queue-detail subset."""
    if _json_bytes(value) <= max_bytes:
        return value
    queues = value.get("queues") or {}
    source_rows = sorted(
        (dict(row) for row in queues.get("by_queue") or [] if isinstance(row, dict)),
        key=lambda row: (
            -int(
                any(
                    int(row.get(field) or 0) > 0
                    for field in (
                        "waiting_jobs",
                        "running_jobs",
                        "zombie_waiting_jobs",
                        "zombie_running_jobs",
                    )
                )
            ),
            str(row.get("queue") or ""),
        ),
    )

    def candidate(retained_count: int, *, compact_maps: bool) -> dict:
        result = dict(value)
        queue_block = dict(queues)
        rows = source_rows[:retained_count]
        queue_block["by_queue"] = rows
        scope = dict(queue_block.get("scope") or {})
        scope["queue_ids"] = [str(row.get("queue") or "") for row in rows]
        queue_block["scope"] = scope
        result["queues"] = queue_block
        omitted_maps: list[str] = []
        if compact_maps:
            parity = dict(result.get("test_group_parity") or {})
            summary = dict(parity.get("summary") or {})
            for key in list(summary):
                if isinstance(summary[key], (dict, list)):
                    summary.pop(key)
                    omitted_maps.append(f"test_group_parity.summary.{key}")
            parity["summary"] = summary
            parity["rocm_inventory"] = {}
            result["test_group_parity"] = parity
        result["publication_retention"] = {
            "policy": "exact_aggregates_priority_queue_rows_v1",
            "max_bytes": max_bytes,
            "aggregate_totals_complete": True,
            "queue_rows": {
                "source": len(source_rows),
                "published": len(rows),
                "omitted": len(source_rows) - len(rows),
                "complete_relative_to_source": len(rows) == len(source_rows),
            },
            "omitted_classification_maps": omitted_maps,
            "complete_relative_to_source": (
                len(rows) == len(source_rows) and not omitted_maps
            ),
        }
        return result

    for compact_maps in (False, True):
        low = 0
        high = len(source_rows)
        best: dict | None = None
        while low <= high:
            middle = (low + high) // 2
            current = candidate(middle, compact_maps=compact_maps)
            if _json_bytes(current) <= max_bytes:
                best = current
                low = middle + 1
            else:
                high = middle - 1
        if best is not None:
            return best
    irreducible = candidate(0, compact_maps=True)
    raise RuntimeError(
        "Operations organization summary fixed aggregates exceed their byte "
        f"budget: {_json_bytes(irreducible)} > {max_bytes} bytes"
    )


def write_snapshot_bundle(
    output: Path,
    payload: dict,
    *,
    write_monolith: bool = True,
    log: bool = True,
) -> dict:
    """Write the lazy frontend bundle and, by default, its source monolith."""
    output_format = _snapshot_format(output)
    alternate = _alternate_snapshot_path(output)
    if write_monolith:
        if output.is_symlink():
            raise ValueError(f"Operations snapshot cannot be a symlink: {output}")
        if alternate.is_symlink() or (
            alternate.exists() and not alternate.is_file()
        ):
            raise ValueError(
                f"Alternate Operations snapshot is not a regular file: {alternate}"
            )
    monolith = _encoded_json(payload).encode("utf-8")
    if len(monolith) > OPERATIONS_DECOMPRESSED_MAX_BYTES:
        raise RuntimeError(
            f"Operations snapshot is {len(monolith)} bytes; logical limit is "
            f"{OPERATIONS_DECOMPRESSED_MAX_BYTES} bytes"
        )
    encoded_output: bytes | None = None
    if write_monolith:
        if output_format == "raw":
            if len(monolith) > OPERATIONS_RAW_WRITE_MAX_BYTES:
                raise RuntimeError(
                    f"Raw Operations snapshot is {len(monolith)} bytes; write limit is "
                    f"{OPERATIONS_RAW_WRITE_MAX_BYTES} bytes; use .json.gz"
                )
            encoded_output = monolith
        else:
            encoded_output = _deterministic_gzip(monolith)
            if len(encoded_output) > OPERATIONS_GZIP_MAX_BYTES:
                raise RuntimeError(
                    "Compressed Operations snapshot is "
                    f"{len(encoded_output)} bytes; limit is "
                    f"{OPERATIONS_GZIP_MAX_BYTES} bytes"
                )

    # Build and bound every default-route asset before mutating an existing
    # generation. A growing evidence catalog can therefore preserve the last
    # known-good publication instead of producing a partially writable bundle.
    encoded_sections = {
        name: _encoded_json(section)
        for name, section in _operation_sections(payload).items()
    }
    section_manifest = {
        name: {
            "path": f"{OPERATIONS_BUNDLE_DIR_NAME}/{name}.json",
            "bytes": len(encoded.encode("utf-8")),
        }
        for name, encoded in encoded_sections.items()
    }
    expected_paths = {
        output.parent / descriptor["path"]
        for descriptor in section_manifest.values()
    }

    org_summary = build_org_summary(
        payload,
        _load_json(output.parent / QUEUE_LIFECYCLE_NAME),
    )
    # This is a machine-consumed exchange contract. Schema v6 keeps only the
    # validated daily index here and references the exact vectors in the public
    # lifecycle source, avoiding a second large copy in every publication.
    org_summary_encoded = _encoded_json(org_summary)
    org_summary_bytes = len(org_summary_encoded.encode("utf-8"))
    if org_summary_bytes > ORG_SUMMARY_MAX_BYTES:
        raise RuntimeError(
            "Operations organization summary exceeds its byte budget; preserving "
            "the last-known-good generation: "
            f"{org_summary_bytes} > {ORG_SUMMARY_MAX_BYTES} bytes"
        )

    manifest = {
        "schema_version": payload.get("schema_version"),
        "bundle_version": OPERATIONS_PRODUCER_BUNDLE_VERSION,
        "generated_at": payload.get("generated_at"),
        "monolith": output.name if write_monolith else None,
        "shell": _operations_shell(payload),
        "organization_summary": {
            "path": ORG_SUMMARY_NAME,
            "bytes": org_summary_bytes,
            "schema_version": org_summary["schema_version"],
        },
        "sections": section_manifest,
    }
    manifest_path = output.parent / OPERATIONS_MANIFEST_NAME
    manifest_encoded = _encoded_json(manifest)
    try:
        validate_operations_canary_budget(
            manifest_bytes=len(manifest_encoded.encode("utf-8")),
            section_bytes={
                name: descriptor["bytes"]
                for name, descriptor in section_manifest.items()
            },
        )
    except OperationsBundleContractError as exc:
        raise RuntimeError(str(exc)) from exc

    queue_history = list((payload.get("queue") or {}).get("history") or [])
    _queue_chart, queue_chart_encoded = _bounded_queue_history_chart(
        queue_history,
        str(queue_history[-1].get("ts") or "") if queue_history else None,
    )

    # All monolith and browser-consumability checks happen before any output is
    # mutated. The remaining writes describe the exact precomputed generation.
    output.parent.mkdir(parents=True, exist_ok=True)
    if encoded_output is not None:
        output.write_bytes(encoded_output)
    bundle_dir = output.parent / OPERATIONS_BUNDLE_DIR_NAME
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for name, encoded in encoded_sections.items():
        (bundle_dir / f"{name}.json").write_text(encoded)
    for stale in bundle_dir.glob("*.json"):
        if stale not in expected_paths:
            stale.unlink()
    org_summary_path = output.parent / ORG_SUMMARY_NAME
    atomic_write_bytes(org_summary_path, org_summary_encoded.encode("utf-8"))
    atomic_write_bytes(manifest_path, manifest_encoded.encode("utf-8"))
    chart_path = output.parent / QUEUE_HISTORY_CHART_NAME
    atomic_write_bytes(chart_path, queue_chart_encoded)
    if log:
        if write_monolith:
            print(
                f"Wrote {output} ({len(encoded_output or b'')} bytes on disk; "
                f"{len(monolith)} bytes uncompressed)"
            )
        print(
            f"Wrote {manifest_path} ({len(manifest_encoded.encode('utf-8'))} bytes, "
            f"{len(section_manifest)} lazy sections)"
        )
        print(
            f"Wrote {org_summary_path} "
            f"({len(org_summary_encoded.encode('utf-8'))} bytes)"
        )
    if write_monolith:
        # Both names are generated private outputs. Keep exactly one source so
        # later assembly/audit cannot accidentally consume a stale generation.
        if alternate.is_file():
            alternate.unlink()
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", "--data-dir", dest="input_dir", default=str(DEFAULT_INPUT))
    parser.add_argument(
        "--output",
        help="Output path (default: INPUT_DIR/operations_v2.json.gz)",
    )
    parser.add_argument("--generated-at", help="Override generation timestamp for reproducible builds")
    args = parser.parse_args(argv)

    input_dir = Path(args.input_dir)
    output = Path(args.output) if args.output else input_dir / DEFAULT_OUTPUT_NAME
    payload = build_snapshot(input_dir, generated_at=args.generated_at)
    write_snapshot_bundle(output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
