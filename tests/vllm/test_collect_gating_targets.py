from __future__ import annotations

from collections import Counter
import json
import re

from vllm import collect_gating_targets as cgt


# vLLM #52659 changed only display labels for these reviewed definitions.
# Keep the retired spellings out so candidate matching follows current titles.
RETIRED_PRE_STANDARDIZATION_LABELS = {
    "Distributed Torchrun + Shutdown Tests (2 GPUs)",
    "Distributed Torchrun + Examples (4 GPUs)",
    "Pipeline + Context Parallelism (4 GPUs)",
    "RayExecutorV2 (4 GPUs)",
    "Kernels Core Operation Test",
    "Basic Models Tests (Initialization)",
    "Basic Models Tests (Other)",
    "Distributed Model Tests (2 GPUs)",
    "Language Models Tests (Extra Standard) %N",
    "DeepSeek V2-Lite Prefetch Offload Accuracy (H100)",
    "e2e Core (1 GPU)",
    "Elastic EP Scaling Test",
    "Kernels Attention Test %N",
    "Kernels MoE Test %N",
    "LoRA %N",
    "Basic Models Tests (Extra Initialization) %N",
    "Multi-Modal Models (Standard) 4: other + whisper",
    "PyTorch Compilation Passes Unit Tests",
    "Hybrid SSM NixlConnector PD prefix cache test (2 GPUs)",
    "MultiConnector (Nixl+Offloading) PD accuracy (2 GPUs)",
    "MultiConnector (Nixl+Offloading) PD edge cases (2 GPUs)",
    "Deepseek V4 Kernel Test (H100)",
    "Kernels Attention DiffKV Test (H100)",
    "Kernels FusedMoE Layer Test (2 H100s)",
    "2 Node Test (4 GPUs)",
    "Benchmarks CLI Test",
    "Attention Benchmarks Smoke Test (B200)",
    "Distributed Compile Unit Tests (2xH100)",
    "Fusion E2E Quick (H100)",
    "Fusion E2E Config Sweep (H100)",
    "Distributed DP Tests (2 GPUs)",
    "Distributed Compile + RPC Tests (2 GPUs)",
    "Distributed DP Tests (4 GPUs)",
    "Distributed Compile + Comm (4 GPUs)",
    "DeepSeek V2-Lite Sync EPLB Accuracy (4xH100)",
    "Qwen3-30B-A3B-FP8-block Sync EPLB Accuracy (4xH100)",
    "V1 e2e (2 GPUs)",
    "V1 e2e (4xH100)",
    "LM Eval Large Models (4xH100)",
    "GPQA Eval (GPT-OSS) (2xH100)",
    "Metrics, Tracing (2 GPUs)",
    "Batch Invariance (A100)",
    "Acceptance Length Test (Large Models)",
    "Language Models Test (Extended Generation)",
    "Language Models Test (PPL)",
    "Multi-Modal Accuracy Eval (Small Models)",
    "PyTorch Compilation Unit Tests",
    "Pytorch Nightly Dependency Override Check",
    "Weight Loading Multiple GPU",
    "Cudagraph",
    "Distributed NixlConnector PD accuracy (4 GPUs)",
    "DP EP Distributed NixlConnector PD accuracy tests (4 GPUs)",
    "CrossLayer KV layout Distributed NixlConnector PD accuracy tests (4 GPUs)",
    "Hybrid SSM NixlConnector PD accuracy tests (4 GPUs)",
    "NixlConnector PD + Spec Decode acceptance (2 GPUs)",
    "Entrypoints Unit Tests",
    "Kernels Mamba Test",
    "Kernels Helion Test",
    "Language Models Tests (Standard)",
    "Language Models Test (MTEB)",
    "Multi-Modal Processor",
    "Multi-Modal Models (Extended Generation 3)",
    "Plugin Tests (2 GPUs)",
    "Quantized Models Test",
    "V1 attention (H100-MI300)",
    "Engine (1 GPU)",
    "e2e Scheduling (1 GPU)",
    "Kernels Quantization Test %N",
    "Language Models Tests (Hybrid) %N",
    "Multi-Modal Models (Standard) 1: qwen2",
    "Multi-Modal Models (Standard) 2: qwen3 + gemma",
    "Multi-Modal Models (Standard) 3: llava + qwen2_vl",
    "Multi-Modal Models (Extended Generation 1)",
    "Multi-Modal Models (Extended Pooling)",
    "Samplers Test",
    "Spec Decode Ngram + Suffix",
    "Platform Tests (CUDA)",
}


def test_config_has_valid_canonical_targets() -> None:
    groups = cgt.load_targets()

    assert groups
    assert [row["id"] for row in groups] == list(range(1, len(groups) + 1))
    duplicates = [label for label, count in Counter(row["label"] for row in groups).items() if count > 1]
    assert duplicates == []


def test_config_does_not_use_nvidia_hardware_aliases_as_amd_targets() -> None:
    groups = cgt.load_targets()
    vendor_alias = re.compile(r":nvidia:|\b(?:A100|H100|H200|B200|DGX)\b|\(L4\)", re.I)

    assert [row["label"] for row in groups if vendor_alias.search(row["label"])] == []


def test_config_does_not_restore_retired_pre_standardization_labels() -> None:
    groups = cgt.load_targets()
    labels = {row["label"] for row in groups}

    assert len(RETIRED_PRE_STANDARDIZATION_LABELS) == 77
    assert labels.isdisjoint(RETIRED_PRE_STANDARDIZATION_LABELS)


def test_generated_payload_summarizes_targets() -> None:
    groups = cgt.load_targets()
    payload = cgt.build_payload(groups)

    assert payload["summary"]["target_group_count"] == len(groups)
    assert payload["groups"] == groups
    for row in payload["groups"]:
        assert row["gating_signal"] == row["source_signal"]
        assert row["pf_signal"] == row["readiness_signal"]
        assert row["assigned_signal"] == row["target_signal"]
    assert payload["summary"]["by_area"]
    assert payload["summary"]["by_gating_signal"]
    assert payload["summary"]["by_pf_signal"]
    assert payload["summary"]["by_assigned_signal"]


def test_bounded_payload_retains_actionable_whole_rows_with_exact_accounting() -> None:
    groups = []
    for index in range(1, 81):
        signal = "red" if index <= 30 else "green"
        groups.append({
            "id": index,
            "label": f"Target {index:03d}",
            "area": "models",
            "gating_signal": signal,
            "pf_signal": "yellow" if index <= 10 else signal,
            "assigned_signal": signal,
            "source_signal": signal,
            "readiness_signal": signal,
            "target_signal": signal,
            "owner": "rocm",
            "note": "detail-" + ("x" * 240),
        })
    source = cgt.build_payload(groups)

    bounded = cgt.bounded_payload(source, max_bytes=8_000)
    encoded = (json.dumps(bounded, indent=2) + "\n").encode()
    retention = bounded["publication_retention"]

    assert len(encoded) <= 8_000
    assert 0 < len(bounded["groups"]) < len(groups)
    assert retention["complete_relative_to_source"] is False
    assert retention["aggregate_summary_complete"] is True
    assert retention["groups"] == {
        "source": 80,
        "published": len(bounded["groups"]),
        "omitted": 80 - len(bounded["groups"]),
        "complete_relative_to_source": False,
    }
    assert sum(
        row["published"]
        for row in retention["by_gating_signal"].values()
    ) == len(bounded["groups"])
    assert all(row["gating_signal"] == "red" for row in bounded["groups"])


def test_bounded_payload_is_permutation_invariant() -> None:
    groups = cgt.load_targets()
    generated_at = "2026-09-01T00:00:00Z"
    forward = cgt.build_payload(groups)
    reverse = cgt.build_payload(list(reversed(groups)))
    forward["generated_at"] = generated_at
    reverse["generated_at"] = generated_at

    assert cgt.bounded_payload(forward, max_bytes=20_000) == cgt.bounded_payload(
        reverse,
        max_bytes=20_000,
    )
