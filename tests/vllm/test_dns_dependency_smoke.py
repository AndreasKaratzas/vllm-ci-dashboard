"""DNS dependency checks must run before, and independently of, data migration."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from vllm import audit_dashboard_data as auditor
from vllm.ci.dns_failures import RETENTION_HOURS, build_public_output, empty_state


ROOT = Path(__file__).resolve().parents[2]


def test_dns_dependency_smoke_runs_without_site_packages_or_runtime_artifacts(tmp_path):
    result = subprocess.run(
        [sys.executable, "-S", str(ROOT / "scripts/vllm/audit_dashboard_data.py"),
         "--dns-dependency-smoke", "--format", "json"],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(result.stdout)
    assert report["errors"] == []
    assert report["degradations"] == []
    assert set(report["metrics"]) == {"dns_dependency_smoke"}
    assert list(tmp_path.iterdir()) == []


def test_obsolete_baseline_cannot_block_smoke_and_actual_validation_remains_strict(tmp_path, monkeypatch, capsys):
    clock = datetime.now(timezone.utc).replace(microsecond=0)
    payload = build_public_output(empty_state(clock, clock - timedelta(hours=RETENTION_HOURS)))
    del payload["scope"]["hardware_scope"]
    payload["scope"]["pipelines"] = ["ci", "amd-ci"]
    path = tmp_path / "data/vllm/ci/dns_failures.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    monkeypatch.setattr(auditor, "ROOT", tmp_path)

    assert auditor.main(["--dns-dependency-smoke"]) == 0
    capsys.readouterr()
    assert auditor.main(["--dns-only"]) == 1
    actual = capsys.readouterr().out
    assert "dns-health-schema" in actual
    assert "dns-health-scope" in actual
    assert path.read_bytes() == before


def test_dns_workflow_uses_smoke_before_restore_and_strict_actual_audit_after_collection():
    workflow = yaml.safe_load((ROOT / ".github/workflows/dns-health.yml").read_text())
    steps = workflow["jobs"]["collect"]["steps"]
    positions = {step.get("name"): index for index, step in enumerate(steps)}
    preflight = steps[positions["Preflight DNS-only validator"]]["run"]
    assert "python -S scripts/vllm/audit_dashboard_data.py --dns-dependency-smoke" in preflight
    assert "--dns-only" not in preflight
    actual = steps[positions["Validate bounded DNS artifacts"]]["run"]
    assert "--dns-only" in actual and "--dns-dependency-smoke" not in actual
    assert positions["Preflight DNS-only validator"] < positions["Resolve durable DNS scanner state"]
    assert positions["Collect DNS failure observations"] < positions["Validate bounded DNS artifacts"]
