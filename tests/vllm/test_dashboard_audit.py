"""Regression tests for the cross-surface dashboard data audit."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from vllm.audit_dashboard_data import DATA_SPECS, ROOT, run_audit


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
        "data/vllm/ci/queue_timeseries.jsonl",
    } <= covered


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


def test_hourly_workflow_runs_dashboard_audit_before_deploy():
    workflow = (ROOT / ".github/workflows/hourly-master.yml").read_text()
    audit_idx = workflow.index("name: Run dashboard data audit")
    deploy_idx = workflow.index("name: Assemble site")
    assert audit_idx < deploy_idx
