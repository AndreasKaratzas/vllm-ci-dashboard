"""Exercise current CI Health live checks with production-generated MI data."""

from __future__ import annotations

import json

import pytest

from tests.vllm import test_data_integrity, test_data_schemas
from vllm.ci.analyzer import compute_build_summary
from vllm.ci.models import TestResult
from vllm.ci.reporter import write_ci_health


@pytest.fixture
def current_health(tmp_path, monkeypatch):
    # The parent fails on foreign hardware while its MI execution passes.
    build = {
        "number": 1001,
        "web_url": "https://buildkite.com/vllm/ci/builds/1001",
        "branch": "main",
        "commit": "a" * 40,
        "created_at": "2026-10-09T00:00:00Z",
        "finished_at": "2026-10-09T00:10:00Z",
        "state": "failed",
        "job_scope": "amd_gpu",
        "hardware_scope": "amd_mi_gpu",
        "jobs": [
            {
                "id": "mi-job", "type": "script", "state": "passed",
                "name": "amd_mi300_1: Basic Models CPU offload",
                "agent_query_rules": ["queue=amd_mi300_1"],
            },
            {
                "id": "cuda-job", "type": "script", "state": "failed",
                "name": "gpu_h200: Basic Models",
                "agent_query_rules": ["queue=h200"],
            },
            {
                "id": "cpu-job", "type": "script", "state": "failed",
                "name": "amd_mi300_1: :computer: (CPU) Torch ABI",
                "agent_query_rules": ["queue=amd_mi300_1"], "source_no_gpu": True,
            },
        ],
    }
    results = [
        TestResult(
            test_id=f"test-{job['id']}-{status}", name=name, classname="tests.models",
            status=status, duration_secs=1, failure_message="",
            job_name=job["name"], job_id=job["id"], step_id=job["id"],
            build_number=1001, pipeline="ci", date="2026-10-09",
        )
        for job, status, name in (
            (build["jobs"][0], "passed", "__passed__ (2)"),
            (build["jobs"][0], "skipped", "__skipped__ (1)"),
            (build["jobs"][1], "failed", "__failed__ (3)"),
            (build["jobs"][2], "failed", "__failed__ (4)"),
        )
    ]
    summary = compute_build_summary(build, results, "amd")
    path = write_ci_health([summary], [], tmp_path)
    health = json.loads(path.read_text())
    monkeypatch.setattr(test_data_schemas, "_load_json_or_skip", lambda _: health)
    return health


def _check_current_health(health):
    test_data_integrity.TestCIHealthData().test_has_required_top_keys(health)
    checks = test_data_schemas.TestCiHealth()
    checks.test_top_level_keys()
    checks.test_pipeline_blocks_have_build_rows()
    checks.test_current_hardware_scopes_use_main_ci_source()
    checks.test_build_rows_have_explicit_assertion_pass_rates()


def test_live_checks_accept_mi_passes_under_failed_parent(current_health):
    latest = current_health["amd"]["latest_build"]
    assert latest["state"] == "passed"
    assert latest["source_state"] == "failed"
    assert latest["job_count"] == 1
    assert set(latest["by_hardware"]) == {"mi300"}
    assert (latest["passed"], latest["failed"], latest["skipped"]) == (2, 0, 1)
    assert latest["test_pass_rate_pct"] == 100
    _check_current_health(current_health)


@pytest.mark.parametrize(
    "location,field,value",
    [
        ("root", "upstream", {}),
        ("root", "source_pipeline", "amd-ci"),
        ("root", "hardware_scope", "all_gpu"),
        ("amd", "job_scope", "cuda_gpu"),
        ("amd", "builds", []),
        ("latest", "branch", "pull-request-1"),
        ("latest", "build_url", "https://buildkite.com/vllm/amd-ci/builds/1001"),
        ("latest", "pipeline", "upstream"),
        ("latest", "source_pipeline", "amd-ci"),
        ("latest", "source_state", None),
        ("signal", "by_hardware", {"h200": {}}),
        ("signal", "by_hardware", {"cpu": {}}),
        ("latest", "jobs_failed", 1),
        ("latest", "test_pass_rate_pct", 50),
    ],
)
def test_live_checks_reject_widened_or_incorrect_mi_contract(
    current_health, location, field, value,
):
    targets = {
        "root": current_health,
        "amd": current_health["amd"],
        "latest": current_health["amd"]["latest_build"],
        "signal": current_health["amd"]["latest_test_signal_build"],
    }
    targets[location][field] = value
    with pytest.raises(AssertionError):
        _check_current_health(current_health)
