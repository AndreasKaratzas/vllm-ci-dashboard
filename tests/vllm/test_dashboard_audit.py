"""Regression tests for the cross-surface dashboard data audit."""

# cspell:ignore xoxb

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vllm import audit_dashboard_data as audit_module
from vllm import build_operations_snapshot as operations_module
from vllm import collect_queue_lifecycle as queue_lifecycle
from vllm.audit_dashboard_data import (
    DATA_SPECS,
    ROOT,
    AuditReport,
    DashboardAudit,
    Finding,
    _buildkite_url_matches,
    format_text,
    run_audit,
)
from vllm.publication_surfaces import (
    CI_GATING_SURFACE_SPEC,
    LEGACY_CI_SURFACE_SPEC,
    PRE_ANALYTICS_CI_CORE_SURFACE_SPEC,
    PRE_ANALYTICS_CI_GATING_SURFACE_SPEC,
    SurfaceSpec,
)


def _best_hardware_audit_fixture():
    source_url = "https://example.invalid/test-amd.yaml"

    def cell(label, state, arch):
        return {
            "exists": True,
            "latest_matched": True,
            "latest_state": state,
            "latest_url": None,
            "latest_build_number": 123,
            "primary_label": label,
            "variants": [{
                "label": label,
                "agent_pool": f"{arch}_1",
                "entries": [],
            }],
        }

    generic_row = {
        "id": "row-generic",
        "title": "Generic Test",
        "canonical_title": "Generic Test",
        "command_fingerprint": "commands-generic",
        "commands": ["pytest tests/generic"],
        "health_memberships": {
            "mi300": "gate-generic",
            "mi355": "gate-generic",
        },
        "cells": {
            "mi300": cell("Generic Test", "failed", "mi300"),
            "mi355": cell("Generic Test", "passed", "mi355"),
        },
    }
    kernel_row = {
        "id": "row-kernel",
        "title": "Kernels Attention Test",
        "canonical_title": "Kernels Attention Test",
        "command_fingerprint": "commands-kernel",
        "commands": ["pytest kernels/attention"],
        "health_memberships": {
            "mi300": "gate-kernel-base",
            "mi355": "gate-kernel-mi355",
        },
        "cells": {
            "mi300": cell("Kernels Attention Test", "passed", "mi300"),
            "mi355": cell("Kernels Attention Test", "soft_fail", "mi355"),
        },
    }
    rows = [generic_row, kernel_row]

    def member(row, arch):
        source = row["cells"][arch]
        return {
            "row_id": row["id"],
            "title": row["title"],
            "architecture": arch,
            "label": source["primary_label"],
            "state": source["latest_state"],
            "optional": False,
            "agent_pool": f"{arch}_1",
            "agent_pools": [f"{arch}_1"],
            "command_fingerprint": row["command_fingerprint"],
            "commands": row["commands"],
            "source_url": source_url,
            "url": source["latest_url"],
            "latest_url": source["latest_url"],
            "latest_matched": True,
            "build_number": 123,
            "variants": [{
                "label": source["primary_label"],
                "agent_pool": f"{arch}_1",
                "optional": False,
                "parallelism": 1,
                "state": source["latest_state"],
                "url": source["latest_url"],
            }],
        }

    health_groups = [
        {
            "id": "gate-generic",
            "title": "Generic Test",
            "status": "passing",
            "is_passing": True,
            "gate_kind": "generic_best_hardware",
            "classification_reason": "generic family; best route passes",
            "architectures": ["mi300", "mi355"],
            "member_row_ids": ["row-generic"],
            "members": [
                member(generic_row, "mi300"),
                member(generic_row, "mi355"),
            ],
        },
        {
            "id": "gate-kernel-base",
            "title": "Kernels Attention Test",
            "status": "passing",
            "is_passing": True,
            "gate_kind": "generic_best_hardware",
            "classification_reason": "generic base gate",
            "architectures": ["mi300"],
            "member_row_ids": ["row-kernel"],
            "members": [member(kernel_row, "mi300")],
        },
        {
            "id": "gate-kernel-mi355",
            "title": "Kernels Attention Test — MI355",
            "status": "failed",
            "is_passing": False,
            "gate_kind": "mi355_sensitive",
            "classification_reason": "architecture-sensitive kernel gate",
            "architectures": ["mi355"],
            "member_row_ids": ["row-kernel"],
            "members": [member(kernel_row, "mi355")],
        },
    ]
    best_summary = {
        "health_group_count": 3,
        "included_groups": 3,
        "passing_groups": 2,
        "failed_only_groups": 1,
        "mixed_groups": 0,
        "failing_groups": 1,
        "waiting_groups": 0,
        "unknown_groups": 0,
        "resolved_groups": 3,
        "generic_groups": 2,
        "generic_group_count": 2,
        "mi355_sensitive_groups": 1,
        "mi355_sensitive_group_count": 1,
        "pass_percentage": 66.7,
        "group_ids": [group["id"] for group in health_groups],
    }
    matrix = {
        "source": {"yaml_url": source_url},
        "summary": {
            "health_group_count": 3,
            "health_policies": {"best_hardware": best_summary},
        },
        "health_groups": health_groups,
        "best_hardware_policy": {
            "mi355_sensitive_rules": [{
                "title": "Kernels Attention Test",
                "reason": "architecture-sensitive kernel gate",
            }],
            "generic_alias_rules": [],
            "mi355_classification": [
                {
                    "row_id": "row-generic",
                    "title": "Generic Test",
                    "label": "Generic Test",
                    "classification": "generic_replica",
                    "reason": "generic family; best route passes",
                    "health_group_id": "gate-generic",
                },
                {
                    "row_id": "row-kernel",
                    "title": "Kernels Attention Test",
                    "label": "Kernels Attention Test",
                    "classification": "separate_gate",
                    "reason": "architecture-sensitive kernel gate",
                    "health_group_id": "gate-kernel-mi355",
                },
            ],
        },
        "rows": rows,
    }
    return matrix


@pytest.mark.live_data
def test_dashboard_audit_current_data_has_no_errors():
    report = run_audit(ROOT)
    assert not report.errors, "\n".join(
        f"{finding.code}: {finding.message}" for finding in report.errors
    )


def test_best_hardware_audit_reconciles_best_of_and_sensitive_gates(tmp_path):
    matrix = _best_hardware_audit_fixture()
    audit = DashboardAudit(tmp_path)

    metrics = audit.audit_best_hardware_health_groups(
        matrix,
        matrix["rows"],
        matrix["summary"],
    )

    assert not audit.report.errors
    assert metrics == {
        "available": True,
        "health_groups": 3,
        "passing_groups": 2,
        "failing_groups": 1,
        "waiting_groups": 0,
        "unknown_groups": 0,
        "generic_groups": 2,
        "mi355_sensitive_groups": 1,
        "classified_mi355_cells": 2,
        "owned_hardware_cells": 4,
        "pass_percentage": 66.7,
    }


def test_best_hardware_audit_rejects_duplicate_or_missing_cell_ownership(tmp_path):
    matrix = _best_hardware_audit_fixture()
    duplicate = copy.deepcopy(matrix["health_groups"][0]["members"][0])
    matrix["health_groups"][1]["members"].append(duplicate)
    audit = DashboardAudit(tmp_path)

    audit.audit_best_hardware_health_groups(
        matrix,
        matrix["rows"],
        matrix["summary"],
    )

    assert "matrix-best-hardware-cell-ownership" in {
        finding.code for finding in audit.report.errors
    }


def test_best_hardware_audit_rejects_summary_source_and_classifier_drift(tmp_path):
    matrix = _best_hardware_audit_fixture()
    matrix["summary"]["health_policies"]["best_hardware"]["passing_groups"] = 3
    matrix["health_groups"][0]["members"][0]["commands"] = ["pytest wrong"]
    matrix["best_hardware_policy"]["mi355_classification"].pop()
    audit = DashboardAudit(tmp_path)

    audit.audit_best_hardware_health_groups(
        matrix,
        matrix["rows"],
        matrix["summary"],
    )

    codes = {finding.code for finding in audit.report.errors}
    assert {
        "matrix-best-hardware-summary",
        "matrix-best-hardware-member-source",
        "matrix-best-hardware-classification",
    } <= codes


def test_dashboard_audit_covers_core_user_facing_data_files():
    covered = {spec.relpath for spec in DATA_SPECS}
    assert {
        "data/vllm/prs.json",
        "data/vllm/issues.json",
        "data/vllm/test_results.json",
        "data/vllm/ci/ci_health.json",
        "data/vllm/ci/dns_failures.json",
        "data/vllm/ci/parity_report.json",
        "data/vllm/ci/analytics.json",
        "data/vllm/ci/amd_test_matrix.json",
        "data/vllm/ci/gating_proposals.json",
        "data/vllm/ci/queue_lifecycle.json",
        "data/vllm/ci/queue_timeseries.jsonl",
        "data/vllm/ci/workload_mapping.json",
        "data/vllm/ci/operations_v2.json",
        "data/vllm/ci/project_items.json",
        "data/vllm/ci/omni_surge_heuristic.json",
        "data/vllm/perf_eval/perf_eval.json",
    } <= covered


def test_queue_lifecycle_audit_validates_daily_wait_vector_counts(tmp_path):
    payload = queue_lifecycle.build_summary(
        [],
        now=datetime.now(timezone.utc).replace(microsecond=0),
        collection=None,
        previous_provenance={},
    )
    output = tmp_path / "data" / "vllm" / "ci" / "queue_lifecycle.json"
    output.parent.mkdir(parents=True)
    output.write_text(json.dumps(payload))

    valid = DashboardAudit(tmp_path)
    valid.audit_queue_lifecycle()
    assert not {
        finding.code
        for finding in valid.report.errors
        if finding.code.startswith("queue-lifecycle-daily-waits")
    }

    payload["daily_wait_times"]["days"][0]["sample_count"] = 1
    output.write_text(json.dumps(payload))
    invalid = DashboardAudit(tmp_path)
    invalid.audit_queue_lifecycle()
    assert "queue-lifecycle-daily-waits-count" in {
        finding.code for finding in invalid.report.errors
    }

    payload = queue_lifecycle.build_summary(
        [],
        now=datetime.now(timezone.utc).replace(microsecond=0),
        collection=None,
        previous_provenance={},
    )
    payload.pop("daily_wait_times")
    output.write_text(json.dumps(payload))
    missing = DashboardAudit(tmp_path)
    missing.audit_queue_lifecycle()
    assert "queue-lifecycle-daily-waits-shape" in {
        finding.code for finding in missing.report.errors
    }


def _dns_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _dns_audit_payload(now: datetime | None = None) -> dict:
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    retention_start = now - timedelta(hours=720)
    first_at = now - timedelta(minutes=30)
    job_id = "00000000-0000-4000-8000-000000000001"
    option_hours = {
        "1h": 1,
        "3h": 3,
        "12h": 12,
        "24h": 24,
        "72h": 72,
        "168h": 168,
        "720h": 720,
    }
    coverage = {
        "status": "complete",
        "complete": True,
        "discovery_complete": True,
        "eligible_jobs": 1,
        "scanned_jobs": 1,
        "positive_jobs": 1,
        "negative_jobs": 0,
        "pending_jobs": 0,
        "unavailable_jobs": 0,
        "oversize_jobs": 0,
    }
    row = {
        "queue": "amd_mi300_1",
        "node": "node-1",
        "hardware": "MI300",
        "affected_jobs": 1,
        "passed_jobs": 1,
        "soft_failed_jobs": 0,
        "hard_failed_jobs": 0,
        "episodes": 1,
        "huggingface_affected_jobs": 1,
        "evidence_total": 1,
    }
    windows = {}
    for window_id, hours in option_hours.items():
        windows[window_id] = {
            "start": _dns_iso(now - timedelta(hours=hours)),
            "end_exclusive": _dns_iso(now),
            "coverage": copy.deepcopy(coverage),
            "totals": {
                "affected_jobs": 1,
                "passed_jobs": 1,
                "soft_failed_jobs": 0,
                "hard_failed_jobs": 0,
                "episodes": 1,
                "huggingface_affected_jobs": 1,
                "queues": 1,
                "nodes": 1,
                "evidence_total": 1,
            },
            "rows": [copy.deepcopy(row)],
        }
    return {
        "schema_version": 1,
        "outcome_contract": "dns-job-outcomes-v1",
        "generated_at": _dns_iso(now),
        "retention": {
            "start": _dns_iso(retention_start),
            "end_exclusive": _dns_iso(now),
            "hours": 720,
        },
        "default_window": "24h",
        "window_options": [
            {"id": "1h", "label": "Last hour", "hours": 1},
            {"id": "3h", "label": "Last 3 hours", "hours": 3},
            {"id": "12h", "label": "Last 12 hours", "hours": 12},
            {"id": "24h", "label": "Last day", "hours": 24},
            {"id": "72h", "label": "Last 3 days", "hours": 72},
            {"id": "168h", "label": "Last 7 days", "hours": 168},
            {"id": "720h", "label": "Last 30 days", "hours": 720},
        ],
        "count_basis": "distinct_buildkite_job_attempts_with_strong_dns_evidence",
        "scope": {
            "organization": "vllm",
            "pipelines": ["amd-ci", "ci"],
            "branches": "all",
            "job_types": ["script"],
            "states": ["passed", "soft", "hard"],
            "queue_scope": "active_amd_gpu",
            "retried_jobs": "included",
        },
        "classifier": {
            "id": "dns-v1",
            "episode_gap_seconds": 5,
            "max_log_bytes": 16777216,
            "target_categories": [
                "huggingface_hub",
                "vllm_public_assets",
                "aws_s3",
                "github",
                "pypi",
                "other_public",
                "unknown",
            ],
        },
        "coverage": {
            **coverage,
            "discovery_start": _dns_iso(retention_start),
            "discovery_end_exclusive": _dns_iso(now),
        },
        "windows": windows,
        "evidence": {
            "evidence_total": 1,
            "shown": 1,
            "truncated": False,
            "items": [
                {
                    "id": hashlib.sha256(
                        f"dns-evidence-v1\0amd-ci\0{job_id}".encode()
                    ).hexdigest(),
                    "first_at": _dns_iso(first_at),
                    "last_at": _dns_iso(first_at),
                    "time_basis": "log_timestamp",
                    "pipeline": "amd-ci",
                    "queue": "amd_mi300_1",
                    "node": "node-1",
                    "hardware": "MI300",
                    "build_number": 123,
                    "job_id": job_id,
                    "state": "passed",
                    "episodes": 1,
                    "match_count": 1,
                    "signature_ids": ["temporary_name_resolution"],
                    "target_categories": ["huggingface_hub"],
                    "window_ids": list(option_hours),
                    "window_metrics": {
                        window_id: {
                            "first_at": _dns_iso(first_at),
                            "last_at": _dns_iso(first_at),
                            "episodes": 1,
                            "match_count": 1,
                            "signature_ids": ["temporary_name_resolution"],
                            "target_categories": ["huggingface_hub"],
                        }
                        for window_id in option_hours
                    },
                }
            ],
        },
    }


def _dns_not_collected_payload(now: datetime | None = None) -> dict:
    """Build an isolated structural seed without reading mutable repo data."""
    payload = _dns_audit_payload(now)
    zero_coverage = {
        "status": "not_collected",
        "complete": False,
        "discovery_complete": False,
        "eligible_jobs": 0,
        "scanned_jobs": 0,
        "positive_jobs": 0,
        "negative_jobs": 0,
        "pending_jobs": 0,
        "unavailable_jobs": 0,
        "oversize_jobs": 0,
    }
    payload["coverage"].update(zero_coverage)
    for block in payload["windows"].values():
        block["coverage"] = copy.deepcopy(zero_coverage)
        block["totals"] = {key: 0 for key in block["totals"]}
        block["rows"] = []
    payload["evidence"] = {
        "evidence_total": 0,
        "shown": 0,
        "truncated": False,
        "items": [],
    }
    return payload


def _write_dns_audit_payload(root: Path, payload: dict) -> Path:
    path = root / "data/vllm/ci/dns_failures.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def test_dns_audit_accepts_a_reconciled_complete_payload(tmp_path):
    _write_dns_audit_payload(tmp_path, _dns_audit_payload())
    audit = DashboardAudit(tmp_path)

    audit.audit_dns_failures()

    assert audit.report.errors == []
    assert audit.report.degradations == []
    assert audit.report.metrics["dns_health"]["coverage_status"] == "complete"
    assert audit.report.metrics["dns_health"]["outcome_breakdown_complete"] is True


def test_dns_only_entrypoint_runs_without_site_packages(tmp_path):
    payload_path = _write_dns_audit_payload(tmp_path, _dns_audit_payload())

    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            str(Path(audit_module.__file__).resolve()),
            "--dns-only",
            "--dns-path",
            str(payload_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Errors: 0" in completed.stdout


def test_dns_audit_accepts_legacy_payload_with_outcome_warning(tmp_path):
    payload = _dns_audit_payload()
    payload.pop("outcome_contract")
    outcome_fields = {
        "passed_jobs",
        "soft_failed_jobs",
        "hard_failed_jobs",
    }
    for block in payload["windows"].values():
        for field in outcome_fields:
            block["totals"].pop(field)
        for row in block["rows"]:
            for field in outcome_fields:
                row.pop(field)
    _write_dns_audit_payload(tmp_path, payload)
    audit = DashboardAudit(tmp_path)

    audit.audit_dns_failures()

    assert audit.report.errors == []
    assert {finding.code for finding in audit.report.warnings} == {
        "dns-health-outcome-contract-legacy"
    }
    assert audit.report.metrics["dns_health"]["outcome_breakdown_complete"] is False


def test_dns_audit_rejects_unreconciled_outcome_breakdown(tmp_path):
    payload = _dns_audit_payload()
    payload["windows"]["1h"]["totals"]["passed_jobs"] = 0
    payload["windows"]["1h"]["rows"][0]["passed_jobs"] = 0
    _write_dns_audit_payload(tmp_path, payload)
    audit = DashboardAudit(tmp_path)

    audit.audit_dns_failures()

    assert "dns-health-outcome-reconciliation" in {
        finding.code for finding in audit.report.errors
    }


def test_dns_audit_keeps_honest_partial_coverage_as_a_local_warning(tmp_path):
    payload = _dns_audit_payload()
    now = datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))
    discovery_start = now - timedelta(hours=24)
    payload["coverage"].update(
        {
            "status": "partial",
            "complete": False,
            "discovery_complete": False,
            "discovery_start": _dns_iso(discovery_start),
        }
    )
    for option in payload["window_options"]:
        coverage = payload["windows"][option["id"]]["coverage"]
        complete = option["hours"] <= 24
        coverage.update(
            {
                "status": "complete" if complete else "partial",
                "complete": complete,
                "discovery_complete": complete,
            }
        )

    _write_dns_audit_payload(tmp_path, payload)
    audit = DashboardAudit(tmp_path)

    audit.audit_dns_failures()

    assert audit.report.errors == []
    assert audit.report.degradations == []
    assert [finding.code for finding in audit.report.warnings] == [
        "dns-health-partial"
    ]
    assert audit.report.metrics["dns_health"]["coverage_status"] == "partial"


def test_dns_audit_accepts_fixed_getaddrinfo_signature_enums(tmp_path):
    payload = _dns_audit_payload()
    item = payload["evidence"]["items"][0]
    item["signature_ids"] = ["getaddrinfo_eai_again", "getaddrinfo_failed"]
    for metric in item["window_metrics"].values():
        metric["signature_ids"] = ["getaddrinfo_eai_again", "getaddrinfo_failed"]
    _write_dns_audit_payload(tmp_path, payload)
    audit = DashboardAudit(tmp_path)

    audit.audit_dns_failures()

    assert audit.report.errors == []


def test_dns_audit_rejects_selected_window_evidence_metric_drift(tmp_path):
    payload = _dns_audit_payload()
    metric = payload["evidence"]["items"][0]["window_metrics"]["1h"]
    metric["episodes"] = 2
    metric["match_count"] = 2
    metric["target_categories"] = ["github"]
    _write_dns_audit_payload(tmp_path, payload)
    audit = DashboardAudit(tmp_path)

    audit.audit_dns_failures()

    assert "dns-health-evidence-reconciliation" in {
        finding.code for finding in audit.report.errors
    }


def test_dns_audit_rejects_out_of_order_window_metrics(tmp_path):
    payload = _dns_audit_payload()
    item = payload["evidence"]["items"][0]
    item["window_metrics"] = dict(reversed(item["window_metrics"].items()))
    _write_dns_audit_payload(tmp_path, payload)
    audit = DashboardAudit(tmp_path)

    audit.audit_dns_failures()

    assert "dns-health-evidence-window" in {
        finding.code for finding in audit.report.errors
    }


def test_dns_audit_accepts_the_backend_public_projection(tmp_path):
    from vllm.ci.dns_failures import (
        DnsClassification,
        build_public_output,
        empty_state,
        iso_timestamp,
        scan_record,
    )

    now = datetime.now(timezone.utc).replace(microsecond=0)
    state = empty_state(now, now - timedelta(hours=720))
    observed_at = iso_timestamp(now - timedelta(minutes=15))
    state["jobs"] = [
        scan_record(
            {
                "pipeline": "amd-ci",
                "build_number": 456,
                "job_id": "00000000-0000-4000-8000-000000000002",
                "queue": "amd_mi300_1",
                "node": "node-2",
                "hardware": "MI300",
                "state": "passed",
                "started_at": iso_timestamp(now - timedelta(minutes=30)),
                "finished_at": iso_timestamp(now - timedelta(minutes=10)),
            },
            DnsClassification(
                match_count=1,
                episode_times=(observed_at,),
                signature_ids=("temporary_name_resolution",),
                target_categories=("huggingface_hub",),
                time_basis="log_timestamp",
            ),
            attempted_at=iso_timestamp(now),
        )
    ]
    _write_dns_audit_payload(tmp_path, build_public_output(state))
    audit = DashboardAudit(tmp_path)

    audit.audit_dns_failures()

    assert audit.report.errors == []
    assert audit.report.degradations == []


def test_dns_backend_window_coverage_tracks_window_relative_positives(tmp_path):
    from vllm.ci.dns_failures import (
        DnsClassification,
        build_public_output,
        empty_state,
        iso_timestamp,
        scan_record,
    )

    now = datetime.now(timezone.utc).replace(microsecond=0)
    state = empty_state(now, now - timedelta(hours=720))
    state["jobs"] = [
        scan_record(
            {
                "pipeline": "amd-ci",
                "build_number": 457,
                "job_id": "00000000-0000-4000-8000-000000000003",
                "queue": "amd_mi300_1",
                "node": "node-3",
                "hardware": "MI300",
                "state": "passed",
                "started_at": iso_timestamp(now - timedelta(hours=3)),
                "finished_at": iso_timestamp(now - timedelta(minutes=10)),
            },
            DnsClassification(
                match_count=1,
                episode_times=(iso_timestamp(now - timedelta(hours=2)),),
                signature_ids=("temporary_name_resolution",),
                target_categories=("huggingface_hub",),
                time_basis="log_timestamp",
            ),
            attempted_at=iso_timestamp(now),
        )
    ]
    payload = build_public_output(state)
    assert payload["windows"]["1h"]["coverage"]["positive_jobs"] == 0
    assert payload["windows"]["1h"]["coverage"]["negative_jobs"] == 1
    assert payload["windows"]["1h"]["totals"]["affected_jobs"] == 0
    _write_dns_audit_payload(tmp_path, payload)
    audit = DashboardAudit(tmp_path)

    audit.audit_dns_failures()

    assert audit.report.errors == []


def test_dns_audit_accepts_the_structural_seed_as_fresh_degradation(tmp_path):
    _write_dns_audit_payload(tmp_path, _dns_not_collected_payload())
    audit = DashboardAudit(tmp_path)

    audit.audit_dns_failures()

    assert audit.report.errors == []
    assert {finding.code for finding in audit.report.degradations} == {
        "dns-health-not-collected"
    }


def test_dns_audit_rejects_sensitive_unknown_and_unreconciled_data(tmp_path):
    payload = _dns_audit_payload()
    payload["unexpected"] = "private"
    payload["evidence"]["items"][0]["job_name"] = "xoxb-" + "1" * 32
    payload["evidence"]["items"][0]["job_id"] = (
        "00000000-0000-4000-8000-00000000000A"
    )
    payload["windows"]["1h"]["totals"]["episodes"] = 2
    _write_dns_audit_payload(tmp_path, payload)
    audit = DashboardAudit(tmp_path)

    audit.audit_dns_failures()

    codes = {finding.code for finding in audit.report.errors}
    assert {
        "dns-health-schema",
        "dns-health-sensitive-content",
        "dns-health-job-id",
        "dns-health-window-reconciliation",
    } <= codes


def test_dns_audit_rejects_false_complete_and_degrades_stale_partial_data(tmp_path):
    stale = _dns_audit_payload(datetime.now(timezone.utc) - timedelta(hours=4))
    coverage_blocks = [stale["coverage"]] + [
        window["coverage"] for window in stale["windows"].values()
    ]
    for coverage in coverage_blocks:
        coverage.update(
            status="partial",
            complete=False,
            eligible_jobs=2,
            pending_jobs=1,
        )
    _write_dns_audit_payload(tmp_path, stale)
    stale_audit = DashboardAudit(tmp_path)
    stale_audit.audit_dns_failures()
    assert "dns-health-stale" in {
        finding.code for finding in stale_audit.report.degradations
    }
    assert "dns-health-stale" not in {
        finding.code for finding in stale_audit.report.errors
    }
    assert "dns-health-partial" in {
        finding.code for finding in stale_audit.report.warnings
    }

    invalid = _dns_audit_payload()
    invalid["coverage"].update(eligible_jobs=2, pending_jobs=1)
    _write_dns_audit_payload(tmp_path, invalid)
    invalid_audit = DashboardAudit(tmp_path)
    invalid_audit.audit_dns_failures()
    assert "dns-health-false-complete" in {
        finding.code for finding in invalid_audit.report.errors
    }


def test_dns_audit_rejects_boundary_identity_and_membership_drift(tmp_path):
    payload = _dns_audit_payload()
    payload["coverage"]["discovery_end_exclusive"] = _dns_iso(
        datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    payload["evidence"]["items"][0]["id"] = "b" * 64
    payload["evidence"]["items"][0]["window_ids"] = ["720h"]
    _write_dns_audit_payload(tmp_path, payload)
    audit = DashboardAudit(tmp_path)

    audit.audit_dns_failures()

    codes = {finding.code for finding in audit.report.errors}
    assert {
        "dns-health-discovery-window",
        "dns-health-evidence-id",
        "dns-health-evidence-window",
    } <= codes


def test_dns_audit_enforces_the_public_payload_budget(tmp_path, monkeypatch):
    _write_dns_audit_payload(tmp_path, _dns_audit_payload())
    monkeypatch.setattr(audit_module, "DNS_FAILURES_MAX_BYTES", 1)
    audit = DashboardAudit(tmp_path)

    audit.audit_dns_failures()

    assert "dns-health-payload-budget" in {
        finding.code for finding in audit.report.errors
    }


def _ci_health_rate_build(*, passed=8, failed=2, skipped=3):
    ran = passed + failed
    ratio = round(passed / ran, 4) if ran else 0.0
    return {
        "build_number": 123,
        "total_tests": passed + failed + skipped,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "pass_rate": ratio,
        "test_pass_rate_pct": round(passed / ran * 100, 2) if ran else 0.0,
        "test_pass_rate_basis": "pytest_assertions_excluding_skipped",
    }


def _analytics_rate_summary(*, passed=1, terminal=2):
    pct = round(passed / terminal * 100, 1) if terminal else 0.0
    return {
        "total_builds": terminal,
        "terminal_builds": terminal,
        "passed": passed,
        "failed": terminal - passed,
        "pass_rate": pct,
        "build_pass_rate_pct": pct,
        "build_pass_rate_basis": "terminal_build_state_all_green",
    }


def _write_rate_contract_fixtures(tmp_path):
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)
    health = {"pass_rate_contract_version": 1}
    for side in ("amd", "upstream"):
        health[side] = {
            "latest_build": _ci_health_rate_build(),
            "latest_test_signal_build": _ci_health_rate_build(),
            "latest_pipeline_build": _ci_health_rate_build(
                passed=0,
                failed=0,
                skipped=0,
            ),
            "builds": [_ci_health_rate_build()],
        }
    (ci / "ci_health.json").write_text(json.dumps(health))

    analytics = {}
    for slug in ("amd-ci", "ci"):
        analytics[slug] = {
            "pass_rate_contract_version": 1,
            "summary": _analytics_rate_summary(),
            "builds": [{"number": 123, "total_jobs": 0, "jobs": []}],
            "default_window": "1d",
            "windows": {
                "1d": {
                    "summary": _analytics_rate_summary(passed=0, terminal=0),
                    "builds": [],
                }
            },
        }
    (ci / "analytics.json").write_text(json.dumps(analytics))

    project = tmp_path / "data/vllm"
    assertions = {"total": 13, "passed": 8, "failed": 2, "skipped": 3}
    platform = {
        "summary": {
            "pass_rate": 80.0,
            "test_assertions": assertions,
            "test_pass_rate_pct": 80.0,
            "test_pass_rate_basis": "pytest_assertions_excluding_skipped",
        }
    }
    (project / "test_results.json").write_text(
        json.dumps(
            {
                "pass_rate_contract_version": 1,
                "rocm": platform,
                "cuda": copy.deepcopy(platform),
            }
        )
    )
    return health, analytics


def test_dashboard_audit_accepts_explicit_pass_rate_contracts(tmp_path):
    _write_rate_contract_fixtures(tmp_path)
    audit = DashboardAudit(tmp_path)

    audit.audit_ci_health()
    audit.audit_analytics()
    audit.audit_root_test_results()

    assert not [
        finding
        for finding in audit.report.errors
        if "pass-rate" in finding.code
    ]


def test_dashboard_audit_rejects_pass_rate_contract_drift(tmp_path):
    health, analytics = _write_rate_contract_fixtures(tmp_path)
    health["amd"]["builds"][0]["test_pass_rate_pct"] = 70.0
    (tmp_path / "data/vllm/ci/ci_health.json").write_text(json.dumps(health))

    analytics["amd-ci"]["summary"]["build_pass_rate_basis"] = "all_builds"
    analytics["amd-ci"]["summary"]["pass_rate"] = 49.0
    analytics["ci"]["summary"]["build_pass_rate_pct"] = 101.0
    analytics["ci"]["windows"]["1d"]["summary"] = _analytics_rate_summary()
    analytics["ci"]["windows"]["1d"]["summary"]["build_pass_rate_pct"] = 40.0
    analytics["ci"]["windows"]["1d"]["summary"]["pass_rate"] = 40.0
    (tmp_path / "data/vllm/ci/analytics.json").write_text(json.dumps(analytics))

    test_results_path = tmp_path / "data/vllm/test_results.json"
    test_results = json.loads(test_results_path.read_text())
    summary = test_results["rocm"]["summary"]
    summary["test_assertions"]["total"] = 12
    summary["test_pass_rate_basis"] = "job_outcomes"
    summary["test_pass_rate_pct"] = 75.0
    test_results_path.write_text(json.dumps(test_results))

    audit = DashboardAudit(tmp_path)
    audit.audit_ci_health()
    audit.audit_analytics()
    audit.audit_root_test_results()
    codes = {finding.code for finding in audit.report.errors}

    assert {
        "ci-health-test-pass-rate-math",
        "ci-health-test-pass-rate-alias",
        "analytics-build-pass-rate-basis",
        "analytics-build-pass-rate-pct",
        "analytics-build-pass-rate-math",
        "analytics-build-pass-rate-alias",
        "root-test-results-test-pass-rate-counts",
        "root-test-results-test-pass-rate-basis",
        "root-test-results-test-pass-rate-math",
        "root-test-results-test-pass-rate-alias",
    } <= codes


def test_dashboard_audit_warns_but_accepts_unversioned_pass_rate_payloads(
    tmp_path,
):
    health, analytics = _write_rate_contract_fixtures(tmp_path)
    health.pop("pass_rate_contract_version")
    for block in (health["amd"], health["upstream"]):
        rows = [
            block["latest_build"],
            block["latest_test_signal_build"],
            block["latest_pipeline_build"],
            *block["builds"],
        ]
        for row in rows:
            row.pop("test_pass_rate_pct")
            row.pop("test_pass_rate_basis")
    for block in analytics.values():
        block.pop("pass_rate_contract_version")
        block["summary"].pop("build_pass_rate_pct")
        block["summary"].pop("build_pass_rate_basis")
    (tmp_path / "data/vllm/ci/ci_health.json").write_text(json.dumps(health))
    (tmp_path / "data/vllm/ci/analytics.json").write_text(json.dumps(analytics))

    test_results_path = tmp_path / "data/vllm/test_results.json"
    test_results = json.loads(test_results_path.read_text())
    test_results.pop("pass_rate_contract_version")
    for platform in ("rocm", "cuda"):
        summary = test_results[platform]["summary"]
        summary.pop("test_pass_rate_pct")
        summary.pop("test_pass_rate_basis")
        summary.pop("test_assertions")
    test_results_path.write_text(json.dumps(test_results))

    audit = DashboardAudit(tmp_path)
    audit.audit_ci_health()
    audit.audit_analytics()
    audit.audit_root_test_results()

    assert not [
        finding for finding in audit.report.errors if "pass-rate" in finding.code
    ]
    warning_codes = [finding.code for finding in audit.report.warnings]
    assert warning_codes.count("ci-health-pass-rate-contract-legacy") == 1
    assert warning_codes.count("analytics-pass-rate-contract-legacy") == 2
    assert warning_codes.count("root-test-results-pass-rate-contract-legacy") == 1


def test_dashboard_audit_rejects_unknown_pass_rate_contract_versions(tmp_path):
    health, analytics = _write_rate_contract_fixtures(tmp_path)
    health["pass_rate_contract_version"] = 2
    for block in analytics.values():
        block["pass_rate_contract_version"] = 2
    (tmp_path / "data/vllm/ci/ci_health.json").write_text(json.dumps(health))
    (tmp_path / "data/vllm/ci/analytics.json").write_text(json.dumps(analytics))

    test_results_path = tmp_path / "data/vllm/test_results.json"
    test_results = json.loads(test_results_path.read_text())
    test_results["pass_rate_contract_version"] = 2
    test_results_path.write_text(json.dumps(test_results))

    audit = DashboardAudit(tmp_path)
    audit.audit_ci_health()
    audit.audit_analytics()
    audit.audit_root_test_results()
    error_codes = [finding.code for finding in audit.report.errors]

    assert error_codes.count("ci-health-pass-rate-contract-version") == 1
    assert error_codes.count("analytics-pass-rate-contract-version") == 2
    assert error_codes.count("root-test-results-pass-rate-contract-version") == 1


def test_dashboard_audit_requires_workload_mapping_v2_ranges():
    spec = next(
        item for item in DATA_SPECS
        if item.relpath == "data/vllm/ci/workload_mapping.json"
    )
    assert {
        "schema_version",
        "generated_at",
        "coverage",
        "repositories",
        "window",
        "scope",
        "totals",
        "hourly",
        "daily",
    } <= set(spec.required_keys)
    assert "scripts/vllm/collect_workload_mapping.py" in spec.producers


@pytest.mark.live_data
def test_dashboard_audit_validates_v2_evidence_and_targets():
    report = run_audit(ROOT)
    metrics = report.metrics["operations_v2"]
    assert metrics["active_targets"] > 0
    assert metrics["active_targets"] == (
        metrics["canonical_targets"]
        + metrics["active_targets_outside_canonical"]
    )
    assert metrics["mixed_outcome_candidates"] > 0
    assert metrics["reliability_observations"] == metrics["linked_reliability_observations"]


@pytest.mark.live_data
def test_dashboard_audit_json_cli_is_parseable():
    result = subprocess.run(
        [sys.executable, "scripts/vllm/audit_dashboard_data.py", "--format", "json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["errors"] == []
    assert "degradations" in payload
    assert "amd_matrix" in payload["metrics"]


def test_degradation_is_reported_but_does_not_fail_the_cli(monkeypatch, capsys):
    finding = Finding(
        "degradation",
        "fresh-data-incomplete",
        "fresh data remains useful but needs attention",
        "data/example.json",
    )
    report = AuditReport(findings=[finding])

    assert report.errors == []
    assert report.warnings == []
    assert report.degradations == [finding]
    assert report.as_dict()["degradations"] == [finding.as_dict()]
    rendered = format_text(report)
    assert "Degradations: 1" in rendered
    assert "DEGRADATION" in rendered
    assert "fresh-data-incomplete" in rendered

    monkeypatch.setattr(audit_module, "run_audit", lambda _root: report)
    assert audit_module.main([]) == 0
    assert audit_module.main(["--strict-warnings"]) == 0
    assert "Degradations: 1" in capsys.readouterr().out


def test_complete_same_commit_unused_shard_base_is_a_degradation(tmp_path):
    ci = tmp_path / "data/vllm/ci"
    results = ci / "test_results"
    results.mkdir(parents=True)
    result_name = "2026-08-12_amd.jsonl"
    (results / result_name).write_text(
        json.dumps({"job_name": "mi300_1: Observed Group"}) + "\n"
    )
    (ci / "shard_bases.json").write_text(json.dumps(["required sharded group"]))
    (ci / "shard_base_catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"commit_sha": "a" * 40},
                "normalization_bases": ["required sharded group"],
                "pipelines": {"amd": ["required sharded group"], "upstream": []},
                "evidence": {
                    "pipeline": "amd",
                    "build_number": 100,
                    "build_commit": "a" * 40,
                    "build_state": "passed",
                    "result_file": result_name,
                    "roster_complete": True,
                    "job_names": ["mi300_1: Observed Group"],
                },
                "definitions": [
                    {
                        "base": "required sharded group",
                        "pipeline": "amd",
                        "optional": False,
                    }
                ],
            }
        )
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_shard_bases()

    assert audit.report.errors == []
    assert [finding.code for finding in audit.report.degradations] == [
        "shard-bases-unused"
    ]


def test_nested_decorated_runtime_shard_satisfies_shard_base_audit(tmp_path):
    ci = tmp_path / "data/vllm/ci"
    results = ci / "test_results"
    results.mkdir(parents=True)
    result_name = "2026-08-20_amd.jsonl"
    runtime_name = (
        "mi300_1: :amd: (MI300) Attention Kernels Shard 1"
    )
    (results / result_name).write_text(
        json.dumps({"job_name": runtime_name}) + "\n"
    )
    (ci / "shard_bases.json").write_text(
        json.dumps(["attention kernels shard"])
    )
    (ci / "shard_base_catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"commit_sha": "a" * 40},
                "normalization_bases": ["attention kernels shard"],
                "pipelines": {
                    "amd": ["attention kernels shard"],
                    "upstream": [],
                },
                "evidence": {
                    "pipeline": "amd",
                    "build_number": 12275,
                    "build_commit": "a" * 40,
                    "build_state": "passed",
                    "result_file": result_name,
                    "roster_complete": True,
                    "job_names": [runtime_name],
                },
                "definitions": [
                    {
                        "base": "attention kernels shard",
                        "pipeline": "amd",
                        "optional": False,
                    }
                ],
            }
        )
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_shard_bases()

    assert "shard-bases-unused" not in {
        finding.code for finding in audit.report.findings
    }


def _manifest_descriptor(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_legacy_ci_manifest(root: Path) -> dict[str, dict[str, object]]:
    manifest = {}
    for relative in LEGACY_CI_SURFACE_SPEC.required_paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"selection": "legacy", "path": relative}) + "\n")
        manifest[relative] = _manifest_descriptor(path)
    return manifest


def _write_publication_state(root: Path, payload: dict) -> Path:
    path = root / "data/vllm/ci/publication_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def _write_attested_split_fallback_state(
    root: Path,
    fallback_surface: str | tuple[str, ...],
    specs: dict[str, SurfaceSpec],
) -> Path:
    fallback_surfaces = (
        (fallback_surface,)
        if isinstance(fallback_surface, str)
        else fallback_surface
    )
    manifests = {}
    for surface in fallback_surfaces:
        spec = specs[surface]
        paths = set(spec.required_paths)
        paths.update(
            relative
            for relative in spec.optional_paths
            if (root / relative).is_file()
        )
        paths.update(
            candidate.relative_to(root).as_posix()
            for pattern in spec.globs
            for candidate in root.glob(pattern)
            if candidate.is_file()
        )
        manifests[surface] = {
            relative: _manifest_descriptor(root / relative)
            for relative in sorted(paths)
        }
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _write_publication_state(
        root,
        {
            "schema_version": 2,
            "surface_contract_version": audit_module.SURFACE_CONTRACT_VERSION,
            "generated_at": now,
            "baseline_ref": "0" * 40,
            "mode": "fallback",
            "degraded_surfaces": list(fallback_surfaces),
            "fresh_degraded_surfaces": [],
            "fallback_surfaces": list(fallback_surfaces),
            "degraded_since": {
                surface: now for surface in fallback_surfaces
            },
            "fallback_since": {
                surface: now for surface in fallback_surfaces
            },
            "fallback_max_age_hours": 36,
            "restored_manifest": manifests,
            "restored_paths": {
                surface: sorted(entries)
                for surface, entries in manifests.items()
            },
        },
    )


def _write_split_build_alignment_fixtures(
    root: Path,
    *,
    analytics_build: int,
    core_build: int,
) -> dict[str, SurfaceSpec]:
    ci = root / "data/vllm/ci"
    results = ci / "test_results"
    results.mkdir(parents=True)

    jobs = [{"state": "passed"} for _ in range(12)]

    def analytics_row(number: int) -> dict:
        return {
            "number": number,
            "source": "test_results",
            "total_jobs": len(jobs),
            "passed": len(jobs),
            "failed": 0,
            "soft_failed": 0,
            "skipped": 0,
            "jobs": jobs,
        }

    builds = [analytics_row(analytics_build), analytics_row(analytics_build - 1)]
    windows = {
        name: {"builds": builds}
        for name in ("1d", "3d", "7d", "14d", "30d")
    }
    (ci / "analytics.json").write_text(
        json.dumps({
            slug: {
                "builds": builds,
                "default_window": "1d",
                "windows": windows,
            }
            for slug in ("amd-ci", "ci")
        })
    )
    for suffix in ("amd", "upstream"):
        (results / f"2026-08-21_{suffix}.jsonl").write_text(
            json.dumps({"build_number": core_build, "job_name": "smoke"})
            + "\n"
        )

    (ci / "ci_health.json").write_text(
        json.dumps({
            "amd": {
                "latest_build": {
                    "build_number": core_build,
                    "by_hardware": {"mi300": {"groups": 1}},
                }
            }
        })
    )
    (ci / "parity_report.json").write_text(
        json.dumps({
            "job_groups": [{
                "name": "smoke",
                "hardware": ["mi300"],
                "amd": {"total": 1},
                "hw_failures": {},
            }]
        })
    )
    (ci / "amd_test_matrix.json").write_text(
        json.dumps({
            "source": {"latest_build_number": core_build},
            "summary": {
                "unique_groups": 1,
                "architecture_count": 1,
                "hardware_cells": 1,
                "latest_matched_cells": 1,
                "passing_cells": 1,
                "failing_cells": 0,
                "waiting_cells": 0,
                "unknown_cells": 0,
                "fully_shared_groups": 1,
                "single_arch_groups": 1,
                "multi_variant_cells": 0,
            },
            "architectures": [{
                "id": "mi300",
                "label": "MI300",
                "group_count": 1,
                "nightly_match_count": 1,
            }],
            "rows": [{
                "title": "smoke",
                "coverage_count": 1,
                "nightly_coverage_count": 1,
                "cells": {
                    "mi300": {
                        "exists": True,
                        "latest_matched": True,
                        "latest_state": "passed",
                    }
                },
            }],
        })
    )

    return {
        "ci_analytics": SurfaceSpec(
            required_paths=("data/vllm/ci/analytics.json",),
        ),
        "ci_core": SurfaceSpec(
            required_paths=(
                "data/vllm/ci/amd_test_matrix.json",
                "data/vllm/ci/ci_health.json",
                "data/vllm/ci/parity_report.json",
            ),
            globs=("data/vllm/ci/test_results/*.jsonl",),
        ),
    }


@pytest.mark.parametrize(
    ("fallback_surface", "analytics_build", "core_build"),
    (
        ("ci_analytics", 100, 101),
        ("ci_core", 101, 100),
    ),
)
def test_attested_split_fallback_allows_directional_build_skew(
    tmp_path,
    monkeypatch,
    fallback_surface,
    analytics_build,
    core_build,
):
    specs = _write_split_build_alignment_fixtures(
        tmp_path,
        analytics_build=analytics_build,
        core_build=core_build,
    )
    monkeypatch.setattr(audit_module, "SURFACE_SPECS", specs)
    state_path = _write_attested_split_fallback_state(
        tmp_path,
        fallback_surface,
        specs,
    )

    audit = DashboardAudit(tmp_path, publication_state_path=state_path)
    audit.audit_analytics()
    audit.audit_amd_matrix()

    target_errors = {
        finding.code
        for finding in audit.report.errors
        if finding.code in {
            "analytics-jsonl-build-mismatch",
            "matrix-analytics-build",
        }
    }
    skew_warnings = [
        finding
        for finding in audit.report.warnings
        if finding.code.endswith("-fallback-skew")
    ]
    assert target_errors == set()
    assert [finding.code for finding in skew_warnings].count(
        "analytics-jsonl-build-mismatch-fallback-skew"
    ) == 2
    assert [finding.code for finding in skew_warnings].count(
        "matrix-analytics-build-fallback-skew"
    ) == 1
    assert {
        finding.context["fallback_surface"] for finding in skew_warnings
    } == {fallback_surface}


@pytest.mark.parametrize("state_kind", ("missing", "tampered", "both"))
def test_split_build_mismatch_requires_valid_restore_attestation(
    tmp_path,
    monkeypatch,
    state_kind,
):
    specs = _write_split_build_alignment_fixtures(
        tmp_path,
        analytics_build=100,
        core_build=101,
    )
    monkeypatch.setattr(audit_module, "SURFACE_SPECS", specs)
    state_path = tmp_path / "data/vllm/ci/publication_state.json"
    if state_kind == "tampered":
        state_path = _write_attested_split_fallback_state(
            tmp_path,
            "ci_analytics",
            specs,
        )
        analytics_path = tmp_path / "data/vllm/ci/analytics.json"
        analytics_path.write_text(analytics_path.read_text() + "\n")
    elif state_kind == "both":
        state_path = _write_attested_split_fallback_state(
            tmp_path,
            ("ci_analytics", "ci_core"),
            specs,
        )

    audit = DashboardAudit(tmp_path, publication_state_path=state_path)
    audit.audit_analytics()
    audit.audit_amd_matrix()
    error_codes = [finding.code for finding in audit.report.errors]

    assert error_codes.count("analytics-jsonl-build-mismatch") == 2
    assert error_codes.count("matrix-analytics-build") == 1
    if state_kind == "tampered":
        assert "publication-fallback-manifest-mismatch" in error_codes


def _write_pre_analytics_gating_only_repo(
    root: Path,
) -> tuple[Path, Path]:
    entries = {}
    nightly = root / "data/vllm/ci/gating_nightlies.json"
    for relative in CI_GATING_SURFACE_SPEC.required_paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"surface": "ci_gating", "path": relative}) + "\n")
        if relative in PRE_ANALYTICS_CI_GATING_SURFACE_SPEC.required_paths:
            entries[relative] = _manifest_descriptor(path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path = _write_publication_state(
        root,
        {
            "schema_version": 2,
            "generated_at": now,
            "baseline_ref": "0" * 40,
            "mode": "fallback",
            "degraded_surfaces": ["ci_gating"],
            "fresh_degraded_surfaces": [],
            "fallback_surfaces": ["ci_gating"],
            "degraded_since": {"ci_gating": now},
            "fallback_since": {"ci_gating": now},
            "fallback_max_age_hours": 36,
            "restored_manifest": {"ci_gating": entries},
            "restored_paths": {"ci_gating": sorted(entries)},
        },
    )
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "audit-test@example.com"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Audit Test"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "pre-analytics gating-only fallback"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return state_path, nightly


def test_schema_v2_mixed_state_expires_only_fallback_surfaces(
    tmp_path, monkeypatch
):
    fresh_path = tmp_path / "data/fresh.json"
    fallback_path = tmp_path / "data/fallback.json"
    fresh_path.parent.mkdir(parents=True)
    fresh_path.write_text('{"selection":"fresh"}\n')
    fallback_path.write_text('{"selection":"fallback"}\n')
    monkeypatch.setattr(
        audit_module,
        "SURFACE_SPECS",
        {
            "fresh": SurfaceSpec(required_paths=("data/fresh.json",)),
            "fallback": SurfaceSpec(required_paths=("data/fallback.json",)),
        },
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path = _write_publication_state(
        tmp_path,
        {
            "schema_version": 2,
            "generated_at": now,
            "baseline_ref": "0" * 40,
            "mode": "mixed",
            "degraded_surfaces": ["fallback", "fresh"],
            "fresh_degraded_surfaces": ["fresh"],
            "fallback_surfaces": ["fallback"],
            # A long-running fresh degradation remains visible but does not
            # consume the bounded last-known-good fallback budget.
            "degraded_since": {
                "fallback": now,
                "fresh": "2000-01-01T00:00:00Z",
            },
            "fallback_since": {"fallback": now},
            "fallback_max_age_hours": 36,
            "restored_manifest": {
                "fallback": {
                    "data/fallback.json": _manifest_descriptor(fallback_path),
                }
            },
        },
    )

    audit = DashboardAudit(tmp_path, publication_state_path=state_path)

    assert audit.fallback_surfaces() == frozenset({"fallback"})
    assert "publication-fallback-expired" not in {
        finding.code for finding in audit.report.errors
    }


def test_schema_v2_rejects_an_inconsistent_degraded_surface_union(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        audit_module,
        "SURFACE_SPECS",
        {"ci": SurfaceSpec(required_paths=("data/source.json",))},
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path = _write_publication_state(
        tmp_path,
        {
            "schema_version": 2,
            "generated_at": now,
            "baseline_ref": "0" * 40,
            "mode": "degraded",
            "degraded_surfaces": ["ci"],
            "fresh_degraded_surfaces": [],
            "fallback_surfaces": [],
            "degraded_since": {"ci": now},
            "fallback_since": {},
            "fallback_max_age_hours": 36,
            "restored_manifest": {},
        },
    )

    audit = DashboardAudit(tmp_path, publication_state_path=state_path)

    assert audit.fallback_surfaces() == frozenset()
    assert [finding.code for finding in audit.report.errors] == [
        "publication-state-invalid"
    ]


def test_schema_v2_stale_source_waiver_applies_only_to_fallback(
    tmp_path, monkeypatch
):
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)
    analytics = ci / "analytics.json"
    analytics.write_text("{}\n")
    (ci / "operations_v2.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": "2026-08-12T12:00:00Z",
                "sources": {
                    "analytics": {
                        "path": "analytics.json",
                        "timestamp": "2026-08-11T12:00:00Z",
                        "timestamp_source": "generated_at",
                    }
                },
            }
        )
    )
    monkeypatch.setattr(
        audit_module,
        "SURFACE_SPECS",
        {"ci": SurfaceSpec(required_paths=("data/vllm/ci/analytics.json",))},
    )
    monkeypatch.setattr(audit_module, "SOURCE_SURFACES", {"analytics": "ci"})
    monkeypatch.setattr(
        audit_module,
        "OPERATIONS_FRESH_SOURCE_KEYS",
        frozenset({"analytics"}),
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def audit_with_selection(selection: str) -> DashboardAudit:
        is_fallback = selection == "fallback"
        state_path = _write_publication_state(
            tmp_path,
            {
                "schema_version": 2,
                "generated_at": now,
                "baseline_ref": "0" * 40,
                "mode": selection,
                "degraded_surfaces": ["ci"],
                "fresh_degraded_surfaces": [] if is_fallback else ["ci"],
                "fallback_surfaces": ["ci"] if is_fallback else [],
                "degraded_since": {"ci": now},
                "fallback_since": {"ci": now} if is_fallback else {},
                "fallback_max_age_hours": 36,
                "restored_manifest": (
                    {
                        "ci": {
                            "data/vllm/ci/analytics.json": _manifest_descriptor(
                                analytics
                            )
                        }
                    }
                    if is_fallback
                    else {}
                ),
            },
        )
        audit = DashboardAudit(tmp_path, publication_state_path=state_path)
        audit.audit_operations_v2()
        return audit

    fresh = audit_with_selection("degraded")
    fallback = audit_with_selection("fallback")

    assert "operations-stale-source" in {
        finding.code for finding in fresh.report.errors
    }
    assert "operations-stale-source-fallback" not in {
        finding.code for finding in fresh.report.warnings
    }
    assert "operations-stale-source" not in {
        finding.code for finding in fallback.report.errors
    }
    assert "operations-stale-source-fallback" in {
        finding.code for finding in fallback.report.warnings
    }


def test_schema_v1_legacy_ci_manifest_returns_split_child_fallbacks(tmp_path):
    manifest = _write_legacy_ci_manifest(tmp_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path = _write_publication_state(
        tmp_path,
        {
            "schema_version": 1,
            "generated_at": now,
            "baseline_ref": "0" * 40,
            "mode": "fallback",
            "degraded_surfaces": ["ci"],
            "degraded_since": {"ci": now},
            "fallback_max_age_hours": 36,
            "restored_manifest": {"ci": manifest},
        },
    )

    audit = DashboardAudit(tmp_path, publication_state_path=state_path)

    assert audit.fallback_surfaces() == frozenset(
        {"ci_core", "ci_analytics", "ci_gating", "ci_changes", "ci_hotness"}
    )
    assert audit.report.errors == []


def test_schema_v1_legacy_ci_manifest_is_verified_before_partition(tmp_path):
    manifest = _write_legacy_ci_manifest(tmp_path)
    changed = tmp_path / LEGACY_CI_SURFACE_SPEC.required_paths[0]
    changed.write_text('{"selection":"tampered"}\n')
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path = _write_publication_state(
        tmp_path,
        {
            "schema_version": 1,
            "generated_at": now,
            "baseline_ref": "0" * 40,
            "mode": "fallback",
            "degraded_surfaces": ["ci"],
            "degraded_since": {"ci": now},
            "fallback_max_age_hours": 36,
            "restored_manifest": {"ci": manifest},
        },
    )

    audit = DashboardAudit(tmp_path, publication_state_path=state_path)

    assert audit.fallback_surfaces() == frozenset()
    assert "publication-fallback-manifest-mismatch" in {
        finding.code for finding in audit.report.errors
    }


def test_schema_v2_rejects_legacy_ci_alias(tmp_path):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path = _write_publication_state(
        tmp_path,
        {
            "schema_version": 2,
            "generated_at": now,
            "baseline_ref": "0" * 40,
            "mode": "fallback",
            "degraded_surfaces": ["ci"],
            "fresh_degraded_surfaces": [],
            "fallback_surfaces": ["ci"],
            "degraded_since": {"ci": now},
            "fallback_since": {"ci": now},
            "fallback_max_age_hours": 36,
            "restored_manifest": {"ci": {}},
        },
    )

    audit = DashboardAudit(tmp_path, publication_state_path=state_path)

    assert audit.fallback_surfaces() == frozenset()
    assert [finding.code for finding in audit.report.errors] == [
        "publication-state-invalid"
    ]


def test_pre_analytics_schema_v2_manifest_is_verified_then_split(tmp_path):
    manifests = {}
    for surface, spec in (
        ("ci_core", PRE_ANALYTICS_CI_CORE_SURFACE_SPEC),
        ("ci_gating", PRE_ANALYTICS_CI_GATING_SURFACE_SPEC),
    ):
        entries = {}
        for relative in spec.required_paths:
            path = tmp_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"surface": surface, "path": relative}) + "\n")
            entries[relative] = _manifest_descriptor(path)
        manifests[surface] = entries
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path = _write_publication_state(
        tmp_path,
        {
            "schema_version": 2,
            "generated_at": now,
            "baseline_ref": "0" * 40,
            "mode": "fallback",
            "degraded_surfaces": ["ci_core", "ci_gating"],
            "fresh_degraded_surfaces": [],
            "fallback_surfaces": ["ci_core", "ci_gating"],
            "degraded_since": {"ci_core": now, "ci_gating": now},
            "fallback_since": {"ci_core": now, "ci_gating": now},
            "fallback_max_age_hours": 36,
            "restored_manifest": manifests,
            "restored_paths": {
                surface: sorted(entries)
                for surface, entries in manifests.items()
            },
        },
    )

    audit = DashboardAudit(tmp_path, publication_state_path=state_path)

    assert audit.fallback_surfaces() == frozenset(
        {"ci_core", "ci_analytics", "ci_gating"}
    )
    assert audit.report.errors == []


def test_pre_analytics_gating_only_schema_v2_uses_clean_head_nightly(tmp_path):
    state_path, _nightly = _write_pre_analytics_gating_only_repo(tmp_path)

    audit = DashboardAudit(tmp_path, publication_state_path=state_path)

    assert audit.fallback_surfaces() == frozenset({"ci_gating"})
    assert audit.report.errors == []


@pytest.mark.parametrize("mutation", ["modified", "missing"])
def test_pre_analytics_gating_only_schema_v2_rejects_unproven_nightly(
    tmp_path,
    mutation,
):
    state_path, nightly = _write_pre_analytics_gating_only_repo(tmp_path)
    if mutation == "modified":
        nightly.write_text('{"selection":"modified"}\n')
    else:
        nightly.unlink()

    audit = DashboardAudit(tmp_path, publication_state_path=state_path)

    assert audit.fallback_surfaces() == frozenset()
    assert "publication-fallback-manifest-mismatch" in {
        finding.code for finding in audit.report.errors
    }


def test_pre_analytics_schema_v2_rejects_unclosed_core_dependency(tmp_path):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path = _write_publication_state(
        tmp_path,
        {
            "schema_version": 2,
            "generated_at": now,
            "baseline_ref": "0" * 40,
            "mode": "fallback",
            "degraded_surfaces": ["ci_core"],
            "fresh_degraded_surfaces": [],
            "fallback_surfaces": ["ci_core"],
            "degraded_since": {"ci_core": now},
            "fallback_since": {"ci_core": now},
            "fallback_max_age_hours": 36,
            "restored_manifest": {"ci_core": {}},
        },
    )

    audit = DashboardAudit(tmp_path, publication_state_path=state_path)

    assert audit.fallback_surfaces() == frozenset()
    assert [finding.code for finding in audit.report.errors] == [
        "publication-state-invalid"
    ]


def test_schema_v1_fallback_state_remains_supported(tmp_path, monkeypatch):
    source = tmp_path / "data/source.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}\n")
    monkeypatch.setattr(
        audit_module,
        "SURFACE_SPECS",
        {"ci": SurfaceSpec(required_paths=("data/source.json",))},
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path = _write_publication_state(
        tmp_path,
        {
            "schema_version": 1,
            "generated_at": now,
            "baseline_ref": "0" * 40,
            "mode": "fallback",
            "degraded_surfaces": ["ci"],
            "degraded_since": {"ci": now},
            "fallback_max_age_hours": 36,
            "restored_manifest": {
                "ci": {"data/source.json": _manifest_descriptor(source)}
            },
        },
    )

    audit = DashboardAudit(tmp_path, publication_state_path=state_path)

    assert audit.fallback_surfaces() == frozenset({"ci"})
    assert audit.report.errors == []


def test_publication_budget_rejects_an_oversized_file(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data/public.json").write_text("{}")
    (tmp_path / "config/public_data_manifest.json").write_text(json.dumps({
        "required_files": ["public.json"],
        "optional_files": [],
        "optional_globs": [],
    }))
    monkeypatch.setattr(audit_module, "PUBLIC_FILE_WARN_BYTES", 1)
    monkeypatch.setattr(audit_module, "PUBLIC_FILE_HARD_BYTES", 1)
    monkeypatch.setattr(audit_module, "PUBLIC_SITE_WARN_BYTES", 100)

    audit = DashboardAudit(tmp_path)
    audit.audit_publication_size()

    assert {finding.code for finding in audit.report.errors} == {
        "public-file-budget"
    }


def test_publication_budget_counts_projection_ceiling_not_private_source(tmp_path):
    config = tmp_path / "config"
    data = tmp_path / "data"
    analytics = data / "vllm/ci/analytics.json"
    config.mkdir()
    analytics.parent.mkdir(parents=True)
    (data / "public.json").write_text("{}")
    analytics.write_text("x" * 500)
    (config / "public_data_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "required_files": ["public.json"],
                "optional_files": [],
                "optional_globs": [],
                "build_inputs": ["vllm/ci/analytics.json"],
                "projected_files": [
                    {
                        "path": "vllm/ci/analytics.json",
                        "projector": "public_analytics_v1",
                        "max_bytes": 17,
                    }
                ],
            }
        )
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_publication_size()

    metrics = audit.report.metrics["publication_size"]
    assert metrics["estimated_bytes"] == 19
    assert metrics["projected_budget_bytes"] == 17
    assert metrics["projected_files"] == {"vllm/ci/analytics.json": 17}
    assert metrics["largest_files"]["vllm/ci/analytics.json"] == 17
    assert not audit.report.errors


def test_operations_audit_rejects_cross_pipeline_links_and_trajectory(tmp_path):
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)
    (ci / "operations_v2.json").write_text(json.dumps({
        "schema_version": 2,
        "gating": {
            "active_target_summary": {"target_group_count": 1},
            "active_target_groups": [{
                "label": "Cross-pipeline evidence",
                "latest_amd_result": {
                    "state": "passed",
                    "source_pipeline": "amd-ci",
                    "evidence": [{
                        "source_pipeline": "amd-ci",
                        "url": "https://buildkite.com/vllm/ci/builds/10",
                    }],
                },
                "main_reliability": {
                    "source_pipeline": "ci",
                    "latest_url": "https://buildkite.com/vllm/amd-ci/builds/20",
                },
                "evidence": [{
                    "source_pipeline": "ci",
                    "url": "https://buildkite.com/vllm/amd-ci/builds/20",
                }],
            }],
        },
        "reliability": {
            "available": True,
            "source_pipeline": "ci",
            "cohort": {
                "id": "main",
                "available": True,
                "build_count": 0,
                "canonical_nightly_build_count": 0,
                "non_nightly_main_build_count": 0,
                "provenance": {"cohort": {"pipeline": "ci"}},
            },
            "denominator": {"unit": "terminal ci branch=main job observations", "observations": 0},
            "group_catalog": [],
            "flaky_candidates": [],
            "latency_rankings": {"by_p90_duration": []},
            "retry_analysis": {
                "summary": {"retry_attempt_count": 0, "failed_then_passed_recovery_count": 0},
                "retry_attempts": [],
                "failed_then_passed_recoveries": [],
            },
        },
        "amd_reliability": {"source_pipeline": "amd-ci"},
        "nightly": {
            "canonical_history": {"pipeline": "amd-ci", "builds_available": 0, "builds": []},
            "upstream_parity": {"pipeline": "ci"},
        },
        "trajectory": {
            "source_pipeline": "amd-ci",
            "pipeline_order": ["amd-ci", "ci"],
            "pipelines": [{"pipeline": "amd-ci"}, {"pipeline": "ci"}],
            "provenance": {
                "source_paths": {"build_history": "ci_health.json", "group_changes": "group_changes.json"},
                "build_history": {"source_pipeline": "amd-ci", "source_key": "amd-ci.builds"},
            },
        },
        "queue": {
            "history": [{"ts": "1"}, {"ts": "2"}],
            "provenance": {"source_paths": {"history": "queue_timeseries.jsonl"}},
        },
        "omni": {"provenance": {"source_paths": {"queue_aggregates": "queue_timeseries.jsonl"}}},
    }))

    audit = DashboardAudit(tmp_path)
    audit.audit_operations_v2()
    codes = {finding.code for finding in audit.report.errors}

    assert "operations-gating-latest-source-url" in codes
    assert "operations-gating-history-source-pipeline" in codes
    assert "operations-gating-runtime-resolution" in codes
    assert "operations-trajectory-scope" in codes


def test_buildkite_audit_links_require_the_exact_host_pipeline_build_and_job():
    exact_job = "https://buildkite.com/vllm/ci/builds/42/steps/canvas?jid=job-42"
    assert _buildkite_url_matches(exact_job, "ci", 42, require_job=True)
    assert not _buildkite_url_matches(
        "https://buildkite.com.evil/vllm/ci/builds/42/steps/canvas?jid=job-42",
        "ci",
        42,
        require_job=True,
    )
    assert not _buildkite_url_matches(
        "https://buildkite.com/vllm/ci/builds/42",
        "ci",
        42,
        require_job=True,
    )
    assert not _buildkite_url_matches(exact_job, "amd-ci", 42, require_job=True)
    assert not _buildkite_url_matches(exact_job, "ci", 43, require_job=True)


def test_operations_audit_handles_malformed_nested_types_without_crashing(tmp_path):
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)
    (ci / "operations_v2.json").write_text(json.dumps({
        "schema_version": 2,
        "gating": {
            "active_target_summary": "not-an-object",
            "active_target_groups": ["not-a-row"],
        },
        "reliability": {
            "available": True,
            "source_pipeline": "ci",
            "cohort": {"build_numbers": ["bad"]},
            "group_catalog": ["not-a-group"],
            "flaky_candidates": ["not-a-candidate"],
            "retry_analysis": {"summary": "bad", "retry_attempts": ["bad"]},
        },
        "nightly": "not-an-object",
        "trajectory": "not-an-object",
        "queue": "not-an-object",
        "omni": "not-an-object",
    }))

    audit = DashboardAudit(tmp_path)
    audit.audit_operations_v2()

    assert audit.report.errors


def test_operations_audit_rejects_mixed_latest_and_retained_amd_counts(tmp_path):
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)
    (ci / "operations_v2.json").write_text(json.dumps({
        "schema_version": 2,
        "amd_test_health": {
            "available": True,
            "summary": {
                "build_count": 1,
                "retained_group_count": 3,
                "group_count": 3,
                "union_group_count": 3,
                "latest_group_count": 3,
                "latest_build_number": 10,
                "latest_state_counts": {
                    "passed": 1,
                    "soft": 1,
                    "hard": 0,
                    "unknown": 0,
                },
            },
            "builds": [{
                "build_number": 10,
                "observed": 2,
                "state_counts": {
                    "passed": 1,
                    "soft": 1,
                    "hard": 0,
                    "unknown": 0,
                },
            }],
            "group_catalog": [
                {"id": "current-pass", "latest_build_number": 10},
                {"id": "current-soft", "latest_build_number": 10},
                {"id": "historical-only", "latest_build_number": 9},
            ],
        },
    }))

    audit = DashboardAudit(tmp_path)
    audit.audit_operations_v2()
    codes = {finding.code for finding in audit.report.errors}

    assert "operations-amd-latest-state-count" in codes
    assert "operations-amd-latest-catalog-count" in codes


def test_operations_audit_rejects_cross_build_logical_group_counts(tmp_path):
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)
    (ci / "operations_v2.json").write_text(json.dumps({
        "schema_version": 2,
        "amd_test_health": {
            "available": True,
            "summary": {
                "build_count": 1,
                "retained_group_count": 2,
                "group_count": 2,
                "union_group_count": 2,
                "retained_job_variant_count": 2,
                "latest_group_count": 2,
                "latest_job_variant_count": 2,
                "latest_build_number": 10,
                "latest_state_counts": {
                    "passed": 2,
                    "soft": 0,
                    "hard": 0,
                    "unknown": 0,
                },
                "latest_job_variant_state_counts": {
                    "passed": 2,
                    "soft": 0,
                    "hard": 0,
                    "unknown": 0,
                },
                "latest_test_group_counts": {
                    "available": True,
                    "build_number": 9,
                    "job_variant_build_number": 10,
                    "test_signal_build_number": 9,
                    "total": 3,
                    "passing": 3,
                    "non_passing": 1,
                    "passing_all": 2,
                    "partial": 1,
                    "pass_percentage": 100.0,
                    "source": "ci_health.amd.latest_test_signal_build",
                    "reason": None,
                },
            },
            "builds": [{
                "build_number": 10,
                "observed": 2,
                "observed_job_variants": 2,
                "state_counts": {
                    "passed": 2,
                    "soft": 0,
                    "hard": 0,
                    "unknown": 0,
                },
                "job_variant_state_counts": {
                    "passed": 2,
                    "soft": 0,
                    "hard": 0,
                    "unknown": 0,
                },
            }],
            "group_catalog": [
                {"id": "current-a", "latest_build_number": 10},
                {"id": "current-b", "latest_build_number": 10},
            ],
        },
    }))

    audit = DashboardAudit(tmp_path)
    audit.audit_operations_v2()
    codes = {finding.code for finding in audit.report.errors}

    assert "operations-amd-logical-group-build-mismatch" in codes
    assert "operations-amd-logical-group-counts" in codes
    assert "operations-amd-logical-groups-exceed-job-variants" in codes


def test_operations_audit_reconciles_platform_comparison_counts(tmp_path):
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)
    (ci / "operations_v2.json").write_text(json.dumps({
        "schema_version": 2,
        "reliability": {
            "available": True,
            "source_pipeline": "ci",
            "group_catalog": [
                {"id": "amd-1"},
                {"id": "amd-2"},
                {"id": "cuda-1"},
            ],
            "platform_comparison": {
                "available": True,
                "source_pipeline": "ci",
                "summary": {
                    "amd_base_group_count": 99,
                    "amd_comparison_row_count": 1,
                    "amd_variant_count": 2,
                    "label_matched_base_group_count": 1,
                    "matched_base_group_count": 1,
                    "comparable_base_group_count": 1,
                    "comparable_variant_pair_count": 1,
                    "review_required_base_group_count": 0,
                    "unmatched_amd_base_group_count": 0,
                    "matched_cuda_variant_count": 1,
                },
                "rows": [{
                    "comparison_key": "shared test",
                    "comparison_eligible": True,
                    "match_status": "exact_cuda_pair",
                    "amd": {
                        "variant_count": 1,
                        "group_ids": ["amd-1"],
                    },
                    "cuda": {
                        "variant_count": 1,
                        "group_ids": ["cuda-1"],
                    },
                }],
            },
        },
    }))

    audit = DashboardAudit(tmp_path)
    audit.audit_operations_v2()

    assert "operations-platform-comparison-counts" in {
        finding.code for finding in audit.report.errors
    }


def _write_analytics_ahead_operations_fixture(tmp_path: Path) -> tuple[Path, dict]:
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)
    analytics_builds = [
        {
            "number": 103,
            "created_at": "2026-04-22T09:00:00Z",
            "finished_at": "2026-04-22T10:00:00Z",
            "state": "passed",
            "jobs": [],
        },
        {
            "number": 102,
            "created_at": "2026-04-21T09:00:00Z",
            "finished_at": "2026-04-21T10:00:00Z",
            "state": "passed",
            "jobs": [],
        },
    ]
    core_head = {
        "build_number": 102,
        "created_at": "2026-04-21T09:00:00Z",
        "finished_at": "2026-04-21T10:00:00Z",
        "state": "passed",
        "has_test_results": True,
    }
    analytics = {"amd-ci": {"builds": analytics_builds}}
    health = {
        "amd": {
            "builds": [core_head],
            "latest_pipeline_build": core_head,
            "latest_test_signal_build": core_head,
        }
    }
    canonical = operations_module._nightly_pipeline(
        "amd-ci",
        analytics["amd-ci"],
        health["amd"],
    )
    (ci / "analytics.json").write_text(json.dumps(analytics))
    (ci / "ci_health.json").write_text(json.dumps(health))
    (ci / "operations_v2.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "nightly": {"canonical_history": canonical},
            }
        )
    )
    return ci, health


def test_operations_audit_accepts_proven_analytics_head_ahead_of_core(tmp_path):
    _write_analytics_ahead_operations_fixture(tmp_path)

    audit = DashboardAudit(tmp_path)
    audit.audit_operations_v2()

    assert "operations-latest-nightly" not in {
        finding.code for finding in audit.report.errors
    }
    assert "operations-latest-nightly-ahead" in {
        finding.code for finding in audit.report.warnings
    }


def test_operations_audit_rejects_unproven_analytics_head_alignment(tmp_path):
    ci, health = _write_analytics_ahead_operations_fixture(tmp_path)
    health["amd"]["latest_pipeline_build"]["build_number"] = 101
    (ci / "ci_health.json").write_text(json.dumps(health))

    audit = DashboardAudit(tmp_path)
    audit.audit_operations_v2()

    assert "operations-latest-nightly" in {
        finding.code for finding in audit.report.errors
    }


def test_operations_audit_reconciles_identity_families_independently(
    tmp_path,
):
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)
    (ci / "ci_health.json").write_text("{}")
    (ci / "operations_v2.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "definition_parity": {
                    "source": {"commit_sha": "a" * 40},
                    "summary": {
                        "matched": 2,
                        "direct_matches": 2,
                        "inline_mirror_variants": 0,
                        "additional_variants": 0,
                        "covered": 2,
                        "amd_only": 2,
                        "nvidia_only": 0,
                        "mirrors": 0,
                        "command_twins": 0,
                        "inline_mirror_variant_kinds": {
                            "effective_command_duplicate": 0,
                            "same_hardware_command_variant": 0,
                            "hardware_variant": 0,
                        },
                        "total_amd_steps": 4,
                        "raw_amd_steps": 4,
                        "total_nvidia_steps": 2,
                        # The rows below publish three families. Deliberately corrupt
                        # only this family-layer count while keeping node and physical
                        # definition conservation valid.
                        "amd_identity_families": 4,
                        "covered_identity_families": 2,
                        "amd_only_identity_families": 1,
                        "partially_covered_identity_families": 1,
                        "identity_family_replica_rows": 1,
                        "identity_family_coverage_rate_pct": 66.7,
                    },
                    "matches": [
                        {
                            "amd_definition_id": "amd#1",
                            "amd_member_definition_ids": ["amd#1"],
                            "amd_identity_family_key": "family-a",
                            "nvidia_definition_id": "nvidia#1",
                            "match_method": "identity",
                        },
                        {
                            "amd_definition_id": "amd#2",
                            "amd_member_definition_ids": ["amd#2"],
                            "amd_identity_family_key": "family-b",
                            "nvidia_definition_id": "nvidia#2",
                            "match_method": "identity",
                        },
                    ],
                    "inline_mirror_variants": [],
                    "additional_variants": [],
                    "amd_only": [
                        {
                            "definition_id": "amd#3",
                            "member_definition_ids": ["amd#3"],
                            "amd_identity_family_key": "family-b",
                        },
                        {
                            "definition_id": "amd#4",
                            "member_definition_ids": ["amd#4"],
                            "amd_identity_family_key": "family-c",
                        },
                    ],
                    "nvidia_only": [],
                    "mirrors": [],
                },
            }
        )
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_operations_v2()
    codes = {finding.code for finding in audit.report.errors}

    assert "definition-parity-identity-family-count" in codes
    assert "definition-parity-amd-logical-conservation" not in codes
    assert "definition-parity-amd-physical-conservation" not in codes


def test_operations_audit_rejects_file_mtime_as_source_freshness(
    tmp_path, monkeypatch
):
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)
    (ci / "operations_v2.json").write_text(json.dumps({
        "schema_version": 2,
        "generated_at": "2026-07-27T12:00:00Z",
        "sources": {
            "analytics": {
                "path": "analytics.json",
                "timestamp": "2026-07-27T11:00:00Z",
                "timestamp_source": "file_mtime",
            }
        },
    }))
    monkeypatch.setattr(
        audit_module,
        "OPERATIONS_FRESH_SOURCE_KEYS",
        frozenset({"analytics"}),
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_operations_v2()

    assert "operations-source-provenance" in {
        finding.code for finding in audit.report.errors
    }


def test_dashboard_audit_allows_in_progress_hardware_count_drift(tmp_path):
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)

    (ci / "analytics.json").write_text(
        json.dumps({"amd-ci": {"builds": [{"number": 123}]}})
    )
    (ci / "ci_health.json").write_text(
        json.dumps(
            {
                "amd": {
                    "latest_build": {
                        "build_number": 123,
                        "by_hardware": {"mi300": {"groups": 1}},
                    }
                }
            }
        )
    )
    (ci / "parity_report.json").write_text(
        json.dumps(
            {
                "job_groups": [
                    {
                        "name": "passing",
                        "hardware": ["mi300"],
                        "amd": {"total": 1},
                        "hw_failures": {},
                    },
                    {
                        "name": "waiting",
                        "hardware": ["mi300"],
                        "amd": {"total": 1},
                        "hw_failures": {"mi300": 1},
                    },
                ]
            }
        )
    )
    (ci / "amd_test_matrix.json").write_text(
        json.dumps(
            {
                "source": {"latest_build_number": 123},
                "summary": {
                    "unique_groups": 2,
                    "architecture_count": 1,
                    "hardware_cells": 2,
                    "latest_matched_cells": 2,
                    "passing_cells": 1,
                    "failing_cells": 0,
                    "waiting_cells": 1,
                    "unknown_cells": 0,
                    "fully_shared_groups": 2,
                    "single_arch_groups": 2,
                    "multi_variant_cells": 0,
                },
                "architectures": [
                    {"id": "mi300", "label": "MI300", "group_count": 2, "nightly_match_count": 2}
                ],
                "rows": [
                    {
                        "title": "passing",
                        "coverage_count": 1,
                        "nightly_coverage_count": 1,
                        "cells": {"mi300": {"exists": True, "latest_matched": True, "latest_state": "passed"}},
                    },
                    {
                        "title": "waiting",
                        "coverage_count": 1,
                        "nightly_coverage_count": 1,
                        "cells": {"mi300": {"exists": True, "latest_matched": True, "latest_state": "running"}},
                    },
                ],
            }
        )
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_amd_matrix()
    assert not audit.report.errors
    warning_codes = {finding.code for finding in audit.report.warnings}
    assert "matrix-health-hardware-count-in-progress" in warning_codes
    assert "parity-matrix-hardware-failing-in-progress" in warning_codes


def test_dashboard_audit_allows_retry_recovery_final_state_drift(tmp_path):
    """Retained failed tests may precede a passed final Buildkite retry."""
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)

    (ci / "analytics.json").write_text(
        json.dumps({"amd-ci": {"builds": [{"number": 123}]}})
    )
    (ci / "ci_health.json").write_text(
        json.dumps(
            {
                "amd": {
                    "latest_build": {
                        "build_number": 123,
                        "by_hardware": {"mi300": {"groups": 1}},
                    }
                }
            }
        )
    )
    (ci / "parity_report.json").write_text(
        json.dumps(
            {
                "job_groups": [
                    {
                        "name": "recovered by retry",
                        "hardware": ["mi300"],
                        "amd": {"total": 1, "failed": 1},
                        "hw_failures": {"mi300": 1},
                    }
                ]
            }
        )
    )
    (ci / "amd_test_matrix.json").write_text(
        json.dumps(
            {
                "source": {"latest_build_number": 123},
                "summary": {
                    "unique_groups": 1,
                    "architecture_count": 1,
                    "hardware_cells": 1,
                    "latest_matched_cells": 1,
                    "passing_cells": 1,
                    "failing_cells": 0,
                    "waiting_cells": 0,
                    "unknown_cells": 0,
                    "fully_shared_groups": 1,
                    "single_arch_groups": 1,
                    "multi_variant_cells": 0,
                },
                "architectures": [
                    {
                        "id": "mi300",
                        "label": "MI300",
                        "group_count": 1,
                        "nightly_match_count": 1,
                    }
                ],
                "rows": [
                    {
                        "title": "recovered by retry",
                        "coverage_count": 1,
                        "nightly_coverage_count": 1,
                        "cells": {
                            "mi300": {
                                "exists": True,
                                "latest_matched": True,
                                "latest_state": "passed",
                            }
                        },
                    }
                ],
            }
        )
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_amd_matrix()

    assert not audit.report.errors
    assert {
        finding.code for finding in audit.report.warnings
    } == {"parity-matrix-hardware-failing-final-state-drift"}


def test_dashboard_audit_rejects_one_group_cross_view_hardware_drift(tmp_path):
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)

    (ci / "analytics.json").write_text(
        json.dumps({"amd-ci": {"builds": [{"number": 123}]}})
    )
    (ci / "ci_health.json").write_text(
        json.dumps(
            {
                "amd": {
                    "latest_build": {
                        "build_number": 123,
                        "by_hardware": {"mi300": {"groups": 2}},
                    }
                }
            }
        )
    )
    (ci / "parity_report.json").write_text(
        json.dumps(
            {
                "job_groups": [
                    {
                        "name": "passing",
                        "hardware": ["mi300"],
                        "amd": {"total": 1},
                        "hw_failures": {},
                    },
                    {
                        "name": "extra normalized label",
                        "hardware": ["mi300"],
                        "amd": {"total": 1},
                        "hw_failures": {"mi300": 1},
                    },
                ]
            }
        )
    )
    (ci / "amd_test_matrix.json").write_text(
        json.dumps(
            {
                "source": {"latest_build_number": 123},
                "summary": {
                    "unique_groups": 1,
                    "architecture_count": 1,
                    "hardware_cells": 1,
                    "latest_matched_cells": 1,
                    "passing_cells": 1,
                    "failing_cells": 0,
                    "waiting_cells": 0,
                    "unknown_cells": 0,
                    "fully_shared_groups": 1,
                    "single_arch_groups": 1,
                    "multi_variant_cells": 0,
                },
                "architectures": [
                    {"id": "mi300", "label": "MI300", "group_count": 1, "nightly_match_count": 1}
                ],
                "rows": [
                    {
                        "title": "passing",
                        "coverage_count": 1,
                        "nightly_coverage_count": 1,
                        "cells": {"mi300": {"exists": True, "latest_matched": True, "latest_state": "passed"}},
                    }
                ],
            }
        )
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_amd_matrix()
    error_codes = {finding.code for finding in audit.report.errors}
    assert "matrix-health-hardware-count" in error_codes
    assert "parity-matrix-hardware-total" in error_codes
    assert "parity-matrix-hardware-failing" in error_codes


def test_dashboard_audit_uses_only_amd_side_parity_hardware_and_incidents(
    tmp_path,
):
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)
    (ci / "parity_report.json").write_text(
        json.dumps(
            {
                "job_groups": [
                    {
                        "name": "shared same-hardware group",
                        "hardware": ["mi300"],
                        "amd_hardware": ["mi300"],
                        "upstream_hardware": ["mi300"],
                        "amd": {"total": 1, "passed": 1},
                        "upstream": {"total": 1, "failed": 1},
                        "hw_failures": {"mi300": 1},
                        "amd_hw_failures": {},
                        "upstream_hw_failures": {"mi300": 1},
                        "hw_canceled": {"mi300": 1},
                        "amd_hw_canceled": {},
                        "upstream_hw_canceled": {"mi300": 1},
                    },
                    {
                        "name": "upstream AMD mirror only",
                        "hardware": ["mi355"],
                        "amd_hardware": [],
                        "upstream_hardware": ["mi355"],
                        "amd": None,
                        "upstream": {"total": 1, "passed": 1},
                        "hw_failures": {},
                        "amd_hw_failures": {},
                        "upstream_hw_failures": {},
                        "hw_canceled": {},
                        "amd_hw_canceled": {},
                        "upstream_hw_canceled": {},
                    },
                ]
            }
        )
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_parity_hardware_matches_matrix(
        {},
        {
            "by_arch": {
                "mi300": {
                    "total": 1,
                    "passing": 1,
                    "failing": 0,
                    "waiting": 0,
                    "unknown": 0,
                    "matched": 1,
                }
            }
        },
    )

    assert not audit.report.errors
    assert audit.report.metrics["parity_hardware"] == {
        "mi300": {
            "passing": 1,
            "failing": 0,
            "pending": 0,
            "canceled": 0,
            "total": 1,
        }
    }


def test_dashboard_audit_compares_health_with_observed_matrix_cells(tmp_path):
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)

    (ci / "analytics.json").write_text(
        json.dumps({"amd-ci": {"builds": [{"number": 123}]}})
    )
    (ci / "ci_health.json").write_text(
        json.dumps(
            {
                "amd": {
                    "latest_build": {
                        "build_number": 123,
                        "by_hardware": {"mi300": {"groups": 1}},
                    }
                }
            }
        )
    )
    (ci / "parity_report.json").write_text(
        json.dumps(
            {
                "job_groups": [
                    {
                        "name": "observed",
                        "hardware": ["mi300"],
                        "amd": {"total": 1},
                        "hw_failures": {},
                    },
                    {
                        "name": "configured only",
                        "hardware": ["mi300"],
                        "amd": {"total": 0},
                        "hw_failures": {},
                    },
                ]
            }
        )
    )
    (ci / "amd_test_matrix.json").write_text(
        json.dumps(
            {
                "source": {"latest_build_number": 123},
                "summary": {
                    "unique_groups": 2,
                    "architecture_count": 1,
                    "hardware_cells": 2,
                    "latest_matched_cells": 1,
                    "passing_cells": 1,
                    "failing_cells": 0,
                    "waiting_cells": 0,
                    "unknown_cells": 1,
                    "fully_shared_groups": 2,
                    "single_arch_groups": 2,
                    "multi_variant_cells": 0,
                },
                "architectures": [
                    {
                        "id": "mi300",
                        "label": "MI300",
                        "group_count": 2,
                        "nightly_match_count": 1,
                    }
                ],
                "rows": [
                    {
                        "title": "observed",
                        "coverage_count": 1,
                        "nightly_coverage_count": 1,
                        "cells": {
                            "mi300": {
                                "exists": True,
                                "latest_matched": True,
                                "latest_state": "passed",
                            }
                        },
                    },
                    {
                        "title": "configured only",
                        "coverage_count": 1,
                        "nightly_coverage_count": 0,
                        "cells": {
                            "mi300": {
                                "exists": True,
                                "latest_matched": False,
                                "latest_state": "unknown",
                            }
                        },
                    },
                ],
            }
        )
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_amd_matrix()
    assert not audit.report.errors
    assert not {
        finding.code
        for finding in audit.report.warnings
        if finding.code.startswith("matrix-health-hardware-count")
    }


def test_hourly_workflow_orders_live_audit_tests_and_enforcement(tmp_path):
    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    ordered_steps = [
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
    hourly = workflows / "hourly-master.yml"
    hourly.write_text("\n".join(ordered_steps))

    valid = DashboardAudit(tmp_path)
    valid.audit_workflows()

    assert not {
        finding.code
        for finding in valid.report.errors
        if finding.code.startswith("workflow-hourly-step-")
    }

    reordered = ordered_steps.copy()
    test_idx = reordered.index("name: Run test suite")
    enforce_idx = reordered.index("name: Enforce publication validation results")
    reordered[test_idx], reordered[enforce_idx] = (
        reordered[enforce_idx],
        reordered[test_idx],
    )
    hourly.write_text("\n".join(reordered))

    invalid = DashboardAudit(tmp_path)
    invalid.audit_workflows()

    assert "workflow-hourly-step-order" in {
        finding.code for finding in invalid.report.errors
    }


def test_workflow_audit_enforces_one_way_analytics_projection(tmp_path):
    for relative in (
        ".github/workflows/hourly-master.yml",
        "config/public_data_manifest.json",
        "scripts/build_site.py",
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((ROOT / relative).read_text())

    valid = DashboardAudit(tmp_path)
    valid.audit_workflows()
    projection_codes = {
        "workflow-public-analytics-feedback",
        "workflow-public-analytics-boundary",
        "private-analytics-lineage",
        "public-analytics-projection",
        "public-analytics-materialization",
    }
    assert not projection_codes & {
        finding.code for finding in valid.report.errors
    }

    hourly = tmp_path / ".github/workflows/hourly-master.yml"
    hourly.write_text(
        hourly.read_text().replace(
            "ci_health.json parity_report.json config_parity.json",
            "ci_health.json analytics.json parity_report.json config_parity.json",
            1,
        )
    )
    feedback = DashboardAudit(tmp_path)
    feedback.audit_workflows()
    assert "workflow-public-analytics-feedback" in {
        finding.code for finding in feedback.report.errors
    }

    manifest_path = tmp_path / "config/public_data_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["projected_files"] = []
    manifest_path.write_text(json.dumps(manifest))
    missing_projection = DashboardAudit(tmp_path)
    missing_projection.audit_workflows()
    assert "public-analytics-projection" in {
        finding.code for finding in missing_projection.report.errors
    }

    build_site = tmp_path / "scripts/build_site.py"
    build_site.write_text(
        build_site.read_text().replace(
            "PUBLIC_ANALYTICS_PROJECTOR_ID: compact_public_analytics_json",
            "PUBLIC_ANALYTICS_PROJECTOR_ID: object",
            1,
        )
    )
    broken_materialization = DashboardAudit(tmp_path)
    broken_materialization.audit_workflows()
    assert "public-analytics-materialization" in {
        finding.code for finding in broken_materialization.report.errors
    }


def test_workflow_audit_enforces_private_analytics_cache_boundary(tmp_path):
    for relative in (
        ".github/workflows/hourly-master.yml",
        ".gitignore",
        "config/public_data_manifest.json",
        "scripts/build_site.py",
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((ROOT / relative).read_text())

    cache_codes = {
        "workflow-private-analytics-cache-order",
        "workflow-private-analytics-cache-key",
        "workflow-private-analytics-cache-restore",
        "workflow-private-analytics-cache-save",
        "workflow-private-analytics-cache-boundary",
        "workflow-private-analytics-cache-feedback",
        "workflow-private-analytics-cache-staging",
        "private-analytics-cache-ignore",
        "private-analytics-cache-publication",
    }
    valid = DashboardAudit(tmp_path)
    valid.audit_workflows()
    assert not cache_codes & {finding.code for finding in valid.report.errors}

    hourly = tmp_path / ".github/workflows/hourly-master.yml"
    hourly.write_text(
        hourly.read_text().replace(
            "CACHE_DAY=$(date -u +%Y-%m-%d)",
            "CACHE_DAY=$(date +%Y-%m-%d)",
            1,
        )
    )
    bad_key = DashboardAudit(tmp_path)
    bad_key.audit_workflows()
    assert "workflow-private-analytics-cache-key" in {
        finding.code for finding in bad_key.report.errors
    }

    hourly.write_text(
        hourly.read_text().replace(
            "flaky_tests.json failure_trends.json quarantine.json",
            (
                "flaky_tests.json failure_trends.json quarantine.json "
                "analytics-builds-v1"
            ),
            1,
        )
    )
    feedback = DashboardAudit(tmp_path)
    feedback.audit_workflows()
    assert "workflow-private-analytics-cache-feedback" in {
        finding.code for finding in feedback.report.errors
    }

    hourly.write_text(
        hourly.read_text().replace("git add data/", "git add -f data/", 1)
    )
    staged = DashboardAudit(tmp_path)
    staged.audit_workflows()
    assert "workflow-private-analytics-cache-staging" in {
        finding.code for finding in staged.report.errors
    }

    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(
        gitignore.read_text().replace("data/vllm/ci/.cache/", "", 1)
    )
    unignored = DashboardAudit(tmp_path)
    unignored.audit_workflows()
    assert "private-analytics-cache-ignore" in {
        finding.code for finding in unignored.report.errors
    }

    manifest_path = tmp_path / "config/public_data_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["build_inputs"].append("vllm/ci/.cache/analytics-builds-v1")
    manifest_path.write_text(json.dumps(manifest))
    exposed = DashboardAudit(tmp_path)
    exposed.audit_workflows()
    assert "private-analytics-cache-publication" in {
        finding.code for finding in exposed.report.errors
    }
