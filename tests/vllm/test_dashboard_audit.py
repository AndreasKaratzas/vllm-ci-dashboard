"""Regression tests for the cross-surface dashboard data audit."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vllm import audit_dashboard_data as audit_module
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
from vllm.publication_surfaces import LEGACY_CI_SURFACE_SPEC, SurfaceSpec


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
                    "build_commit": "a" * 40,
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
        {"ci_core", "ci_gating", "ci_changes", "ci_hotness"}
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


def test_schema_v2_rejects_unclosed_core_fallback_dependency(tmp_path):
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
