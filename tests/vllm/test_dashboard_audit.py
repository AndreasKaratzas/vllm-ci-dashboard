"""Regression tests for the cross-surface dashboard data audit."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from vllm import audit_dashboard_data as audit_module
from vllm.audit_dashboard_data import (
    DATA_SPECS,
    ROOT,
    DashboardAudit,
    _buildkite_url_matches,
    run_audit,
)


def test_dashboard_audit_current_data_has_no_errors():
    report = run_audit(ROOT)
    assert not report.errors, "\n".join(
        f"{finding.code}: {finding.message}" for finding in report.errors
    )


def test_dashboard_audit_covers_core_user_facing_data_files():
    covered = {spec.relpath for spec in DATA_SPECS}
    assert {
        "data/vllm/prs.json",
        "data/vllm/issues.json",
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
    assert "amd_matrix" in payload["metrics"]


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


def test_hourly_workflow_runs_dashboard_audit_before_deploy():
    workflow = (ROOT / ".github/workflows/hourly-master.yml").read_text()
    audit_idx = workflow.index("name: Run dashboard data audit")
    deploy_idx = workflow.index("name: Assemble site")
    assert audit_idx < deploy_idx
