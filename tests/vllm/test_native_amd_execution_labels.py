"""Native AMD execution labels observed in main CI nightly build 93523."""

import pytest

from vllm.ci import analyzer
from vllm.ci.models import TestResult
from vllm import collect_analytics, collect_amd_test_matrix
from vllm.pipelines import is_amd_ci_job, is_upstream_cuda_ci_job


OBSERVED_NATIVE_LABELS = [
    ("AMD: :amd: (MI300) LM Eval Large Models (mi300_8)", "LM Eval Large Models", "mi300_8"),
    ("AMD: :amd: (MI300) LM Eval Large Models ROCm Harness (mi300_8)", "LM Eval Large Models ROCm Harness", "mi300_8"),
    ("AMD: :amd: (MI300) LM Eval Small Models Harness (mi300_1)", "LM Eval Small Models Harness", "mi300_1"),
    ("AMD: :amd: (MI355 DPX) FP8 MoE Kernels (mi355_dpx)", "FP8 MoE Kernels", "mi355_dpx"),
    ("AMD: :amd: (MI355 DPX) Kernels (mi355_dpx)", "Kernels", "mi355_dpx"),
    ("AMD: :amd: (MI355 DPX) Native Quantization Kernels (mi355_dpx)", "Native Quantization Kernels", "mi355_dpx"),
    ("AMD: :amd: (MI355) Fusion E2E TP2 AR-RMS (Dynamo Partition) (mi355_2)", "Fusion E2E TP2 AR-RMS (Dynamo Partition)", "mi355_2"),
    ("AMD: :amd: (MI355) Fusion E2E TP2 AR-RMS (Inductor Partition) (mi355_2)", "Fusion E2E TP2 AR-RMS (Inductor Partition)", "mi355_2"),
    ("AMD: :amd: (MI355) LM Eval Large Models Harness (mi355_8)", "LM Eval Large Models Harness", "mi355_8"),
    ("AMD: :amd: (MI355) Qwen3-Next-80B-A3B-Instruct MTP Async EPLB Accuracy (mi355_4)", "Qwen3-Next-80B-A3B-Instruct MTP Async EPLB Accuracy", "mi355_4"),
]


@pytest.mark.parametrize("raw,title,pool", OBSERVED_NATIVE_LABELS)
def test_observed_native_labels_share_runtime_group_hardware_and_route(raw, title, pool):
    hardware = pool.split("_", 1)[0]
    assert analyzer._normalize_job_name(raw) == title.lower()
    assert analyzer._parity_key_base(raw) == analyzer._parity_key_base(title)
    assert not analyzer._EXCLUDE_PATTERNS.match(analyzer._normalize_job_name(raw))
    assert analyzer._extract_hardware(raw) == hardware
    assert collect_analytics.normalize_job(raw) == title
    assert collect_analytics.queue_from_result_job_name(raw) == f"amd_{pool}"
    assert collect_amd_test_matrix._normalize_job_name(raw) == title.lower()
    assert is_amd_ci_job({"job_name": raw}) is True
    assert is_upstream_cuda_ci_job({"job_name": raw}) is False


def _result(name, identity):
    return TestResult(test_id=identity, name="__passed__ (1)", classname="group",
                      status="passed", duration_secs=1, failure_message="", job_name=name,
                      job_id=identity, step_id="", build_number=93523, pipeline="ci", date="2026-10-08")


def test_native_ci_groups_count_as_amd_hardware_and_can_match_cuda_counterparts():
    results = [_result(name, f"native-{index}") for index, (name, _title, _pool) in enumerate(OBSERVED_NATIVE_LABELS)]
    summary = analyzer.compute_build_summary({
        "number": 93523, "state": "passed", "branch": "main", "job_scope": "amd_gpu",
        "jobs": [{"type": "script", "name": row.job_name, "id": row.job_id, "state": "passed"} for row in results],
    }, results, "amd")
    assert summary.passed == summary.unique_test_groups == 10
    assert set(summary.by_hardware) == {"mi300", "mi355"}
    cuda = _result(":nvidia: (H100) FP8 MoE Kernels", "cuda-job")
    parity = analyzer.compute_parity(results, [cuda])
    row = next(row for row in parity["job_groups"] if row["name"] == "fp8 moe kernels")
    assert row["amd"]["passed"] == row["upstream"]["passed"] == 1
    assert row["amd_hardware"] == ["mi355"]


def test_native_execution_pool_removal_preserves_real_suite_and_gpu_count_tags():
    raw = "AMD: :amd: (MI355) Fusion Tests (2 GPUs) (Dynamo Partition) (mi355_2)"
    assert analyzer._normalize_job_name(raw) == "fusion tests (2 gpus) (dynamo partition)"
    assert analyzer._normalize_job_name("AMD: Unrecognized platform job") == "amd: unrecognized platform job"
    assert analyzer._extract_hardware("AMD: Unrecognized platform job") == "unknown"


def test_native_pool_resolves_only_the_exact_build_pinned_runtime_family(monkeypatch):
    commit = "a" * 40
    monkeypatch.setattr(analyzer, "_AMD_RUNTIME_GROUP_KEY_COMMIT", commit)
    monkeypatch.setattr(analyzer, "_AMD_RUNTIME_GROUP_KEYS", {
        ("fp8 moe kernels", "mi355_dpx"): "fp8 moe kernels (8 gpus)",
    })
    raw = "AMD: :amd: (MI355 DPX) FP8 MoE Kernels (mi355_dpx)"
    assert analyzer._amd_runtime_group_key(raw, commit) == "fp8 moe kernels (8 gpus)"
    assert analyzer._amd_runtime_group_key(raw, "b" * 40) == "fp8 moe kernels"
