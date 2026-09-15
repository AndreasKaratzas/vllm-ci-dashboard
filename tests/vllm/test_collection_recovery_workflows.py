"""Exercise recovery decisions in the actual workflow scripts without network."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _step(workflow: str, job: str, name: str) -> dict:
    value = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    return next(step for step in value["jobs"][job]["steps"] if step.get("name") == name)


def _execute(script: str, root: Path, **environment: str) -> dict[str, str]:
    output = root / "outputs"
    env = {**os.environ, "GITHUB_OUTPUT": str(output), **environment}
    subprocess.run(["bash", "-eu", "-o", "pipefail", "-c", script], cwd=root, env=env,
                   capture_output=True, text=True, check=True, timeout=10)
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


@pytest.mark.parametrize(("surfaces", "mode", "expected"), [
    ("perf_eval", "reserved", "true"),
    ("ci_gating,perf_eval", "reserved", "true"),
    ("github_home", "reserved", "false"),
    ("perf_eval", "retry_gated", "false"),
    ("perf_eval", "cap_gated", "false"),
    ("", "reserved", "false"),
])
def test_watchdog_retries_failed_performance_collection_only_with_reservation(
    tmp_path: Path, surfaces: str, mode: str, expected: str,
) -> None:
    step = _step("hourly-master.yml", "collect-and-deploy", "Decide whether to regenerate perf-eval")
    script = step["run"].replace("${{ github.event_name }}", "workflow_dispatch")
    result = _execute(script, tmp_path, RETRY_SURFACES=surfaces, REQUEST_MODE=mode,
                      HOURLY_DNS_GENERATION_INPUT="", HOURLY_WATCHDOG_GENERATION_INPUT="generation",
                      DISPATCH_TYPE="")
    assert result["run_perf"] == expected
    assert "steps.request-attempt.outputs.retry_surfaces" in step["env"]["RETRY_SURFACES"]


@pytest.mark.parametrize(("surfaces", "mode", "expected"), [
    ("github_home", "reserved", "true"),
    ("ci_gating,github_home", "reserved", "true"),
    ("github_home", "success_gated", "false"),
    ("ci_gating", "reserved", "false"),
    ("", "reserved", "false"),
])
def test_failed_home_collection_cannot_be_skipped_by_fresh_prior_pr_timestamp(
    tmp_path: Path, surfaces: str, mode: str, expected: str,
) -> None:
    data = tmp_path / "data/vllm"
    data.mkdir(parents=True)
    (data / "prs.json").write_text(json.dumps({"collected_at": datetime.now(timezone.utc).isoformat()}))
    step = _step("hourly-master.yml", "collect-and-deploy", "Check GitHub data freshness")
    result = _execute(step["run"], tmp_path, RETRY_SURFACES=surfaces, REQUEST_MODE=mode)
    assert result["stale"] == expected
    assert ("collector_retry" in result["reason"]) == (expected == "true")
    assert "steps.request-attempt.outputs.retry_surfaces" in step["env"]["RETRY_SURFACES"]


def test_durable_success_requires_collection_evidence() -> None:
    step = _step("hourly-master.yml", "collect-and-deploy", "Mark durable Data Collection success")
    assert "--require-collection-evidence" in step["run"]
    assert "steps.publication-commit.outputs.state_sha" in step["env"]["DURABLE_STATE_SHA"]
