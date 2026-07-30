"""Unit contracts for commit-pinned CI definition matching."""

from __future__ import annotations

import io
import tarfile

from vllm import config_parity
from vllm.config_parity import ConfigStep, _match_config_steps


def _step(
    label: str,
    identity: str,
    commands: list[str],
    source: str,
    *,
    definition_id: str = "",
    group: str = "test",
    agent_pool: str = "",
    semantic_title: str = "",
    fingerprint: str = "",
    num_gpus: int | None = None,
    working_dir: str = "",
) -> ConfigStep:
    return ConfigStep(
        label=label,
        normalized_label=config_parity._normalize_job_name(label),
        identity_key=identity,
        source_file=source,
        group=group,
        commands=commands,
        definition_id=definition_id,
        agent_pool=agent_pool,
        semantic_title=semantic_title,
        definition_fingerprint=fingerprint,
        member_definition_ids=(definition_id,) if definition_id else (),
        member_labels=(label,),
        member_groups=(group,),
        member_agent_pools=(agent_pool,),
        num_gpus=num_gpus,
        working_dir=working_dir,
    )


def _snapshot_archive() -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        files = {
            "vllm-deadbeef/.buildkite/test-amd.yaml": b"steps:\n  - label: AMD test\n    commands: [pytest amd]\n",
            "vllm-deadbeef/.buildkite/test_areas/basic.yaml": b"group: basic\nsteps:\n  - label: Upstream test\n    commands: [pytest upstream]\n",
            "vllm-deadbeef/README.md": b"not part of the parity snapshot\n",
        }
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def test_source_snapshot_resolves_main_once_and_pins_archive_to_commit(monkeypatch):
    calls = []
    archive = _snapshot_archive()

    class Response:
        def __init__(self, payload=None, content=b""):
            self._payload = payload
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        calls.append(url)
        if url.endswith("/commits/main"):
            return Response({"sha": "deadbeef"})
        assert url.endswith("/tarball/deadbeef")
        return Response(content=archive)

    monkeypatch.setattr(config_parity, "_SOURCE_SNAPSHOT", None)
    monkeypatch.setattr(config_parity.requests, "get", fake_get)

    first = config_parity._load_source_snapshot()
    second = config_parity._load_source_snapshot()

    assert first is second
    assert first.commit_sha == "deadbeef"
    assert sorted(first.files) == [
        ".buildkite/test-amd.yaml",
        ".buildkite/test_areas/basic.yaml",
    ]
    assert calls == [
        "https://api.github.com/repos/vllm-project/vllm/commits/main",
        "https://api.github.com/repos/vllm-project/vllm/tarball/deadbeef",
    ]


def test_platform_target_suite_selector_is_command_metadata():
    upstream = [
        "export VLLM_WORKER_MULTIPROC_METHOD=spawn",
        "pytest -v -s basic_correctness/test_mem.py",
        "pytest -v -s basic_correctness/test_basic_correctness.py",
        "pytest -v -s basic_correctness/test_cpu_offload.py",
    ]
    amd = [
        "export VLLM_WORKER_MULTIPROC_METHOD=spawn",
        "pytest -v -s basic_correctness/test_mem.py",
        (
            "VLLM_TARGET_TEST_SUITE=MI300 pytest -v -s "
            "basic_correctness/test_basic_correctness.py"
        ),
        "pytest -v -s basic_correctness/test_cpu_offload.py",
    ]

    assert config_parity.commands_similarity(amd, upstream) == 1.0
    assert config_parity.commands_similarity(
        ["TARGET_TEST_SUITE=A100 pytest tests/basic.py"],
        ["TARGET_TEST_SUITE=MI300 pytest tests/basic.py"],
    ) == 1.0
    assert config_parity.commands_similarity(
        ["DP_EP=1 pytest tests/distributed.py"],
        ["DP_EP=0 pytest tests/distributed.py"],
    ) < 1.0


def test_live_hardware_aliases_share_intended_identity_families():
    small_models = "lm eval small models (hardware variants)"
    assert config_parity._config_identity_key(
        "LM Eval Small Models (1xB200)",
        None,
    ) == small_models
    assert config_parity._config_identity_key(
        "LM Eval Small Models (MI300)",
        None,
    ) == small_models
    assert config_parity._config_identity_key(
        "LM Eval Small Models (2xB200-2xMI300)",
        None,
    ) == small_models

    distributed = "distributed tests (2 gpus)"
    assert config_parity._config_identity_key(
        "Distributed Tests (2xB200)",
        None,
    ) == distributed
    assert config_parity._config_identity_key(
        "Distributed Tests (2xH100-2xMI300)",
        None,
    ) == distributed


def test_exact_commands_and_platform_neutral_titles_form_a_twin():
    amd = _step(
        "Docker Build Metadata (ROCm)",
        "docker build metadata (rocm)",
        ["python tools/check.py"],
        ".buildkite/test-amd.yaml",
    )
    upstream = _step(
        "Docker Build Metadata",
        "docker build metadata",
        ["python tools/check.py"],
        ".buildkite/test_areas/basic.yaml",
    )

    matches, amd_only, upstream_only = _match_config_steps([amd], [upstream], [])

    assert not amd_only
    assert not upstream_only
    assert len(matches) == 1
    assert matches[0].match_method == "command_twin"
    assert matches[0].command_similarity == 1.0


def test_unique_exact_command_twin_overrides_conflicting_gpu_metadata_identity():
    amd = _step(
        "Extract Hidden States Integration",
        "extract hidden states integration (2 gpus)",
        ["pytest tests/extract_hidden_states"],
        ".buildkite/test-amd.yaml",
    )
    topology_match = _step(
        "Extract Hidden States Integration (2 GPUs)",
        "extract hidden states integration (2 gpus)",
        ["pytest -m distributed tests/extract_hidden_states"],
        ".buildkite/test_areas/misc.yaml",
    )
    command_match = _step(
        "Extract Hidden States Integration",
        "extract hidden states integration",
        ["pytest tests/extract_hidden_states"],
        ".buildkite/test_areas/misc.yaml",
    )

    matches, amd_only, upstream_only = _match_config_steps(
        [amd],
        [topology_match, command_match],
        [],
    )

    assert not amd_only
    assert len(matches) == 1
    assert matches[0].match_method == "command_twin"
    assert matches[0].amd_step is amd
    assert matches[0].nvidia_step is command_match
    assert upstream_only == [topology_match]


def test_command_twin_override_is_exported_for_every_coalesced_member(
    monkeypatch,
):
    identity = "extract hidden states integration (2 gpus)"
    amd_mi300 = _step(
        "Extract Hidden States Integration (MI300)",
        identity,
        ["pytest tests/extract_hidden_states"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#extract-mi300",
        semantic_title="Extract Hidden States Integration",
        fingerprint="extract",
        num_gpus=2,
    )
    amd_mi355 = _step(
        "Extract Hidden States Integration (MI355)",
        identity,
        ["pytest tests/extract_hidden_states"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#extract-mi355",
        semantic_title="Extract Hidden States Integration",
        fingerprint="extract",
        num_gpus=2,
    )
    upstream = _step(
        "Extract Hidden States Integration",
        identity,
        ["pytest tests/extract_hidden_states"],
        ".buildkite/test_areas/misc.yaml",
        definition_id="upstream#extract",
        num_gpus=2,
    )
    monkeypatch.setattr(
        config_parity,
        "_load_config_steps",
        lambda: ([amd_mi355, amd_mi300], [upstream], []),
    )

    overrides = config_parity.extract_parity_key_overrides()

    assert overrides[amd_mi300.normalized_label] == identity
    assert overrides[amd_mi355.normalized_label] == identity


def test_command_twin_requires_nonempty_commands():
    amd = _step("Same title", "amd-key", [], ".buildkite/test-amd.yaml")
    upstream = _step("Same title", "up-key", [], ".buildkite/test_areas/basic.yaml")

    matches, amd_only, upstream_only = _match_config_steps([amd], [upstream], [])

    assert not matches
    assert amd_only == [amd]
    assert upstream_only == [upstream]


def test_ambiguous_exact_command_candidates_remain_unmatched():
    amd = _step("Model correctness", "amd-key", ["pytest tests/models"], ".buildkite/test-amd.yaml")
    upstream_a = _step("Model correctness CUDA", "up-a", ["pytest tests/models"], ".buildkite/test_areas/a.yaml")
    upstream_b = _step("Model correctness H100", "up-b", ["pytest tests/models"], ".buildkite/test_areas/b.yaml")

    matches, amd_only, upstream_only = _match_config_steps(
        [amd], [upstream_a, upstream_b], [],
    )

    assert not matches
    assert amd_only == [amd]
    assert {step.identity_key for step in upstream_only} == {"up-a", "up-b"}


def test_shared_gpu_identity_preserves_distinct_v1_definitions():
    identity = "v1 e2e (4 gpus)"
    amd_generic = _step(
        "V1 e2e (4 GPUs)",
        identity,
        ["pytest tests/v1/e2e.py"],
        ".buildkite/test-amd.yaml",
    )
    amd_h100 = _step(
        "V1 e2e (4xH100-4xMI300)",
        identity,
        ["pytest tests/v1/e2e.py"],
        ".buildkite/test-amd.yaml",
    )
    upstream_generic = _step(
        "V1 e2e (4 GPUs)",
        identity,
        ["pytest tests/v1/e2e.py"],
        ".buildkite/test_areas/engine.yaml",
    )
    upstream_h100 = _step(
        "V1 e2e (4xH100)",
        identity,
        ["pytest tests/v1/e2e.py"],
        ".buildkite/test_areas/engine.yaml",
    )

    matches, amd_only, upstream_only = _match_config_steps(
        [amd_h100, amd_generic],
        [upstream_generic, upstream_h100],
        [],
    )

    assert not amd_only
    assert not upstream_only
    assert {
        (match.amd_step.label, match.nvidia_step.label)
        for match in matches
    } == {
        ("V1 e2e (4 GPUs)", "V1 e2e (4 GPUs)"),
        ("V1 e2e (4xH100-4xMI300)", "V1 e2e (4xH100)"),
    }

    reversed_matches, reversed_amd_only, reversed_upstream_only = (
        _match_config_steps(
            [amd_generic, amd_h100],
            [upstream_h100, upstream_generic],
            [],
        )
    )
    assert not reversed_amd_only
    assert not reversed_upstream_only
    assert {
        (match.amd_step.label, match.nvidia_step.label)
        for match in reversed_matches
    } == {
        (match.amd_step.label, match.nvidia_step.label)
        for match in matches
    }


def test_identity_assignment_prefers_counterparts_over_crossed_commands():
    identity = "workload (4 gpus)"
    amd_generic = _step(
        "Workload (4 GPUs)",
        identity,
        ["pytest workload --hardware=h100"],
        ".buildkite/test-amd.yaml",
    )
    amd_h100 = _step(
        "Workload (4xH100-4xMI300)",
        identity,
        ["pytest workload --hardware=generic"],
        ".buildkite/test-amd.yaml",
    )
    upstream_generic = _step(
        "Workload (4 GPUs)",
        identity,
        ["pytest workload --hardware=generic"],
        ".buildkite/test_areas/basic.yaml",
    )
    upstream_h100 = _step(
        "Workload (4xH100)",
        identity,
        ["pytest workload --hardware=h100"],
        ".buildkite/test_areas/basic.yaml",
    )

    matches, amd_only, upstream_only = _match_config_steps(
        [amd_generic, amd_h100],
        [upstream_h100, upstream_generic],
        [],
    )

    assert not amd_only
    assert not upstream_only
    assert {
        (match.amd_step.label, match.nvidia_step.label)
        for match in matches
    } == {
        ("Workload (4 GPUs)", "Workload (4 GPUs)"),
        ("Workload (4xH100-4xMI300)", "Workload (4xH100)"),
    }


def test_lm_eval_coalesces_h100_replicas_and_retains_a100_definition():
    identity = "lm eval large models (4 gpus)"
    amd_a100 = _step(
        "LM Eval Large Models (4xA100-4xMI300)",
        identity,
        ["pytest eval.py --models ampere"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#a100-mi300",
        group="mi300",
        semantic_title="LM Eval Large Models (4xA100-4xMI)",
        fingerprint="ampere",
    )
    amd_h100_mi300 = _step(
        "LM Eval Large Models (4xH100-4xMI300)",
        identity,
        ["pytest eval.py --models hopper"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#h100-mi300",
        group="mi300",
        agent_pool="mi300",
        semantic_title="LM Eval Large Models (4xH100-4xMI)",
        fingerprint="hopper",
    )
    amd_h100_mi355 = _step(
        "LM Eval Large Models (4xH100-4xMI355)",
        identity,
        ["pytest eval.py --models hopper"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#h100-mi355",
        group="mi355",
        agent_pool="mi355",
        semantic_title="LM Eval Large Models (4xH100-4xMI)",
        fingerprint="hopper",
    )
    upstream_h100 = _step(
        "LM Eval Large Models (4xH100)",
        identity,
        ["pytest eval.py --models hopper"],
        ".buildkite/test_areas/lm_eval.yaml",
    )

    matches, amd_only, upstream_only = _match_config_steps(
        [amd_h100_mi355, amd_a100, amd_h100_mi300],
        [upstream_h100],
        [],
    )

    assert not upstream_only
    assert amd_only == [amd_a100]
    assert len(matches) == 1
    assert matches[0].nvidia_step is upstream_h100
    assert matches[0].amd_step.label == (
        "LM Eval Large Models (4xH100-4xMI300)"
    )
    assert matches[0].amd_step.physical_member_count == 2
    assert set(matches[0].amd_step.member_definition_ids) == {
        "amd#h100-mi300",
        "amd#h100-mi355",
    }
    assert set(matches[0].amd_step.member_groups) == {"mi300", "mi355"}
    assert set(matches[0].amd_step.member_agent_pools) == {"mi300", "mi355"}

    reversed_matches, reversed_amd_only, reversed_upstream_only = (
        _match_config_steps(
            [amd_h100_mi300, amd_a100, amd_h100_mi355],
            [upstream_h100],
            [],
        )
    )
    assert not reversed_upstream_only
    assert reversed_amd_only == [amd_a100]
    assert {
        (match.amd_step.label, match.nvidia_step.label)
        for match in reversed_matches
    } == {
        (match.amd_step.label, match.nvidia_step.label)
        for match in matches
    }


def test_batch_invariance_maps_h100_without_consuming_a100_or_b200():
    identity = "batch invariance"
    amd = _step(
        "Batch Invariance (H100-MI250)",
        identity,
        ["pytest tests/batch_invariance"],
        ".buildkite/test-amd.yaml",
    )
    upstream_a100 = _step(
        "Batch Invariance (A100)",
        identity,
        ["pytest tests/batch_invariance"],
        ".buildkite/test_areas/models.yaml",
    )
    upstream_h100 = _step(
        "Batch Invariance (H100)",
        identity,
        ["pytest tests/batch_invariance"],
        ".buildkite/test_areas/models.yaml",
    )
    upstream_b200 = _step(
        "Batch Invariance (B200)",
        identity,
        ["pytest tests/batch_invariance"],
        ".buildkite/test_areas/models.yaml",
    )

    matches, amd_only, upstream_only = _match_config_steps(
        [amd],
        [upstream_b200, upstream_a100, upstream_h100],
        [],
    )

    assert not amd_only
    assert len(matches) == 1
    assert matches[0].nvidia_step is upstream_h100
    assert {step.label for step in upstream_only} == {
        "Batch Invariance (A100)",
        "Batch Invariance (B200)",
    }


def test_distributed_variants_do_not_cross_h100_and_b200_hardware():
    identity = "distributed tests (2 gpus)"
    amd_mi300 = _step(
        "Distributed Tests (2xH100-2xMI300)",
        identity,
        ["pytest tests/distributed --mode upstream"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#distributed-mi300",
        semantic_title="Distributed Tests (2xH100-2xMI)",
        fingerprint="upstream-shape",
    )
    amd_mi355 = _step(
        "Distributed Tests (2xH100-2xMI355)",
        identity,
        ["pytest tests/distributed --mode amd-only"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#distributed-mi355",
        semantic_title="Distributed Tests (2xH100-2xMI)",
        fingerprint="different-shape",
    )
    upstream_h100 = _step(
        "Distributed Tests (2xH100)",
        identity,
        ["pytest tests/distributed --mode upstream"],
        ".buildkite/test_areas/distributed.yaml",
    )
    upstream_b200 = _step(
        "Distributed Tests (2xB200)",
        identity,
        ["pytest tests/distributed --mode amd-only"],
        ".buildkite/test_areas/distributed.yaml",
    )

    matches, amd_only, upstream_only = _match_config_steps(
        [amd_mi355, amd_mi300],
        [upstream_b200, upstream_h100],
        [],
    )

    assert {
        (match.amd_step.label, match.nvidia_step.label)
        for match in matches
    } == {
        (
            "Distributed Tests (2xH100-2xMI300)",
            "Distributed Tests (2xH100)",
        )
    }
    assert amd_only == [amd_mi355]
    assert upstream_only == [upstream_b200]


def test_gpqa_identical_commands_still_pair_by_reference_hardware():
    identity = "gpqa eval (gpt-oss)"
    amd_h100 = _step(
        "GPQA Eval (GPT-OSS) (H100-MI300)",
        identity,
        ["python benchmarks/gpqa.py"],
        ".buildkite/test-amd.yaml",
    )
    amd_b200 = _step(
        "GPQA Eval (GPT-OSS) (B200-MI355)",
        identity,
        ["python benchmarks/gpqa.py"],
        ".buildkite/test-amd.yaml",
    )
    upstream_h100 = _step(
        "GPQA Eval (GPT-OSS) (H100)",
        identity,
        ["python benchmarks/gpqa.py"],
        ".buildkite/test_areas/lm_eval.yaml",
    )
    upstream_b200 = _step(
        "GPQA Eval (GPT-OSS) (B200)",
        identity,
        ["python benchmarks/gpqa.py"],
        ".buildkite/test_areas/lm_eval.yaml",
    )

    matches, amd_only, upstream_only = _match_config_steps(
        [amd_b200, amd_h100],
        [upstream_h100, upstream_b200],
        [],
    )

    assert not amd_only
    assert not upstream_only
    assert {
        (match.amd_step.label, match.nvidia_step.label)
        for match in matches
    } == {
        (
            "GPQA Eval (GPT-OSS) (H100-MI300)",
            "GPQA Eval (GPT-OSS) (H100)",
        ),
        (
            "GPQA Eval (GPT-OSS) (B200-MI355)",
            "GPQA Eval (GPT-OSS) (B200)",
        ),
    }


def test_plural_hardware_labels_do_not_cross_h100_and_b200():
    identity = "kernels fusedmoe layer test (2 gpus)"
    amd = _step(
        "Kernels FusedMoE Layer Test (2xH100-2xMI300)",
        identity,
        ["pytest tests/kernels/fused_moe"],
        ".buildkite/test-amd.yaml",
    )
    upstream_h100 = _step(
        "Kernels FusedMoE Layer Test (2 H100s)",
        identity,
        ["pytest tests/kernels/fused_moe"],
        ".buildkite/test_areas/kernels.yaml",
    )
    upstream_b200 = _step(
        "Kernels FusedMoE Layer Test (2 B200s)",
        identity,
        ["pytest tests/kernels/fused_moe"],
        ".buildkite/test_areas/kernels.yaml",
    )

    matches, amd_only, upstream_only = _match_config_steps(
        [amd],
        [upstream_b200, upstream_h100],
        [],
    )

    assert not amd_only
    assert len(matches) == 1
    assert matches[0].nvidia_step is upstream_h100
    assert upstream_only == [upstream_b200]


def test_reference_hardware_splits_matrix_equivalent_sharded_definitions():
    identity = "v1 attention"
    semantic_title = "V1 attention"
    fingerprint = "same-execution"
    amd_h100 = _step(
        "V1 attention (H100-MI300) %N",
        identity,
        ["pytest v1/attention --shard=$$BUILDKITE_PARALLEL_JOB"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#v1-attention-h100",
        semantic_title=semantic_title,
        fingerprint=fingerprint,
    )
    amd_b200 = _step(
        "V1 attention (B200-MI355) %N",
        identity,
        ["pytest v1/attention --shard=$$BUILDKITE_PARALLEL_JOB"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#v1-attention-b200",
        semantic_title=semantic_title,
        fingerprint=fingerprint,
    )
    upstream_h100_mirror = _step(
        "V1 attention (H100-MI300)",
        identity,
        ["pytest v1/attention --shard=$$BUILDKITE_PARALLEL_JOB"],
        ".buildkite/test_areas/attention.yaml",
        definition_id="upstream#v1-attention-h100",
    )
    upstream_b200 = _step(
        "V1 attention (B200)",
        identity,
        ["pytest v1/attention --shard=$$BUILDKITE_PARALLEL_JOB"],
        ".buildkite/test_areas/attention.yaml",
        definition_id="upstream#v1-attention-b200",
    )
    mirrors = [{
        "nvidia_label": upstream_h100_mirror.label,
        "identity_key": identity,
        "source_file": upstream_h100_mirror.source_file,
        "nvidia_definition_id": upstream_h100_mirror.definition_id,
    }]

    matches, amd_only, upstream_only = _match_config_steps(
        [amd_h100, amd_b200],
        [upstream_h100_mirror, upstream_b200],
        mirrors,
    )

    assert not upstream_only
    assert len(matches) == 1
    assert matches[0].amd_step is amd_b200
    assert matches[0].nvidia_step is upstream_b200
    assert matches[0].amd_step.physical_member_count == 1
    assert amd_only == [amd_h100]
    assert config_parity._matrix_semantic_amd_count(
        [amd_h100, amd_b200]
    ) == 1
    assert len(config_parity._semantic_amd_steps(
        [amd_h100, amd_b200]
    )) == 2


def test_identical_collision_rows_are_reported_instead_of_dropped():
    first = _step(
        "Quantization",
        "quantization",
        ["pytest tests/quantization"],
        ".buildkite/test-amd.yaml",
    )
    second = _step(
        "Quantization",
        "quantization",
        ["pytest tests/quantization"],
        ".buildkite/test-amd.yaml",
    )
    upstream = _step(
        "Quantization",
        "quantization",
        ["pytest tests/quantization"],
        ".buildkite/test_areas/quantization.yaml",
    )

    matches, amd_only, upstream_only = _match_config_steps(
        [first, second],
        [upstream],
        [],
    )

    assert len(matches) == 1
    assert len(amd_only) == 1
    assert amd_only[0] is first or amd_only[0] is second
    assert not upstream_only


def test_report_counts_every_parsed_definition_and_documents_collision_policy(
    monkeypatch,
):
    amd = [
        _step(
            "Shared A (MI300)",
            "shared",
            ["pytest a"],
            ".buildkite/test-amd.yaml",
            definition_id="amd#a-mi300",
            group="mi300",
            agent_pool="pool-mi300",
            semantic_title="Shared A",
            fingerprint="a",
        ),
        _step(
            "Shared A (MI355)",
            "shared",
            ["pytest a"],
            ".buildkite/test-amd.yaml",
            definition_id="amd#a-mi355",
            group="mi355",
            agent_pool="pool-mi355",
            semantic_title="Shared A",
            fingerprint="a",
        ),
        _step(
            "Shared B",
            "shared",
            ["pytest b"],
            ".buildkite/test-amd.yaml",
            definition_id="amd#b",
            semantic_title="Shared B",
            fingerprint="b",
        ),
    ]
    upstream = [
        _step(
            "Shared A (H100)",
            "shared",
            ["pytest a"],
            ".buildkite/test_areas/basic.yaml",
            definition_id="upstream#a",
        ),
        _step(
            "Shared B",
            "shared",
            ["pytest b"],
            ".buildkite/test_areas/basic.yaml",
            definition_id="upstream#b",
        ),
    ]
    provenance = {
        "commit_sha": "a" * 40,
        "fetched_at": "2026-07-28T00:00:00Z",
        "matching_rules": [
            "Retain provenance for every parsed YAML definition.",
            "Use a deterministic maximum-cardinality one-to-one assignment.",
        ],
    }
    monkeypatch.setattr(
        config_parity,
        "_load_config_steps",
        lambda: (amd, upstream, []),
    )
    monkeypatch.setattr(config_parity, "_source_provenance", lambda: provenance)

    report = config_parity.build_config_parity()

    assert report["summary"]["total_amd_steps"] == 2
    assert report["summary"]["amd_parity_nodes"] == 2
    assert report["summary"]["amd_matrix_semantic_rows"] == 2
    assert report["summary"]["amd_hardware_split_rows"] == 0
    assert report["summary"]["raw_amd_steps"] == 3
    assert report["summary"]["amd_execution_replica_rows"] == 1
    assert report["summary"]["amd_matrix_replica_rows"] == 1
    assert report["summary"]["total_nvidia_steps"] == 2
    assert report["summary"]["unique_amd_identities"] == 1
    assert report["summary"]["unique_nvidia_identities"] == 1
    assert report["summary"]["amd_identity_families"] == 2
    assert report["summary"]["covered_identity_families"] == 2
    assert report["summary"]["amd_only_identity_families"] == 0
    assert report["summary"]["partially_covered_identity_families"] == 0
    assert report["summary"]["identity_family_replica_rows"] == 0
    assert report["summary"]["identity_family_coverage_rate_pct"] == 100.0
    assert report["summary"]["amd_identity_collision_rows"] == 1
    assert report["summary"]["nvidia_identity_collision_rows"] == 1
    assert report["summary"]["matched"] == 2
    assert report["summary"]["amd_only"] == 0
    assert report["summary"]["nvidia_only"] == 0
    assert "one-to-one" in " ".join(report["source"]["matching_rules"])
    shared_a = next(
        match
        for match in report["matches"]
        if match["nvidia_label"] == "Shared A (H100)"
    )
    assert shared_a["amd_physical_member_count"] == 2
    assert set(shared_a["amd_member_definition_ids"]) == {
        "amd#a-mi300",
        "amd#a-mi355",
    }
    assert set(shared_a["amd_member_agent_pools"]) == {
        "pool-mi300",
        "pool-mi355",
    }
    assert all(
        match["amd_identity_family_key"]
        for match in report["matches"]
    )


def test_amd_identity_families_preserve_same_architecture_workloads():
    steps = [
        _step(
            "V1 e2e (4 GPUs)",
            "v1 e2e (4 gpus)",
            [
                "pytest -v -s "
                "v1/e2e/spec_decode/test_spec_decode.py "
                '-k "eagle_correctness_heavy"',
            ],
            ".buildkite/test-amd.yaml",
            definition_id="amd#v1-generic",
            agent_pool="mi300_4",
            semantic_title="V1 e2e (4 GPUs)",
            working_dir="/vllm-workspace/tests",
        ),
        _step(
            "V1 e2e (4xH100-4xMI300)",
            "v1 e2e (4 gpus)",
            ["pytest -v -s v1/e2e/test_hybrid_chunked_prefill.py"],
            ".buildkite/test-amd.yaml",
            definition_id="amd#v1-h100",
            agent_pool="mi300_4",
            semantic_title="V1 e2e (4xH100-4xMI)",
        ),
        _step(
            "LM Eval Large Models (4xA100-4xMI300)",
            "lm eval large models (4 gpus)",
            [
                "pytest -s -v test_lm_eval_correctness.py "
                "--config-list-file=configs/models-large.txt",
            ],
            ".buildkite/test-amd.yaml",
            definition_id="amd#large-a100-mi300",
            agent_pool="mi300_4",
            semantic_title="LM Eval Large Models (4xA100-4xMI)",
            working_dir="/vllm-workspace/.buildkite/lm-eval-harness",
        ),
        _step(
            "LM Eval Large Models (4xH100-4xMI300)",
            "lm eval large models (4 gpus)",
            [
                "pytest -s -v test_lm_eval_correctness.py "
                "--config-list-file=configs/models-large-rocm-fp8.txt",
            ],
            ".buildkite/test-amd.yaml",
            definition_id="amd#large-h100-mi300",
            agent_pool="mi300_4",
            semantic_title="LM Eval Large Models (4xH100-4xMI)",
            working_dir="/vllm-workspace/.buildkite/lm-eval-harness",
        ),
        _step(
            "LM Eval Large Models (4xH100-4xMI355)",
            "lm eval large models (4 gpus)",
            [
                "pytest -s -v test_lm_eval_correctness.py "
                "--config-list-file=configs/models-large-rocm-fp8.txt",
            ],
            ".buildkite/test-amd.yaml",
            definition_id="amd#large-h100-mi355",
            agent_pool="mi355_4",
            semantic_title="LM Eval Large Models (4xH100-4xMI)",
            working_dir="/vllm-workspace/.buildkite/lm-eval-harness",
        ),
    ]

    families, family_by_definition = (
        config_parity._amd_identity_family_keys(steps)
    )

    assert len(families) == 4
    assert (
        family_by_definition["amd#v1-generic"]
        != family_by_definition["amd#v1-h100"]
    )
    assert (
        family_by_definition["amd#large-a100-mi300"]
        != family_by_definition["amd#large-h100-mi300"]
    )
    assert (
        family_by_definition["amd#large-h100-mi300"]
        == family_by_definition["amd#large-h100-mi355"]
    )


def test_amd_identity_families_treat_pool_sizes_as_one_architecture():
    steps = [
        _step(
            "Aliased workload (1 GPU)",
            "aliased workload",
            ["pytest -v -s tests/aliased/test_workload.py"],
            ".buildkite/test-amd.yaml",
            definition_id="amd#aliased-mi300-1",
            agent_pool="mi300_1",
            semantic_title="Aliased workload",
        ),
        _step(
            "Aliased workload (2 GPUs)",
            "aliased workload",
            ["pytest -v -s tests/aliased/test_workload.py"],
            ".buildkite/test-amd.yaml",
            definition_id="amd#aliased-mi300-2",
            agent_pool="mi300_2",
            semantic_title="Aliased workload",
        ),
    ]

    families, family_by_definition = (
        config_parity._amd_identity_family_keys(steps)
    )

    assert len(families) == 2
    assert (
        family_by_definition["amd#aliased-mi300-1"]
        != family_by_definition["amd#aliased-mi300-2"]
    )


def test_amd_identity_families_merge_cross_architecture_yaml_replicas():
    steps = [
        _step(
            "Distributed Tests (2xH100-2xMI300)",
            "distributed tests (2 gpus)",
            [
                "pytest -v -s tests/distributed/test_context_parallel.py",
                "pytest -v -s tests/v1/distributed/test_dbo.py",
            ],
            ".buildkite/test-amd.yaml",
            definition_id="amd#distributed-mi300",
            agent_pool="mi300_2",
            semantic_title="Distributed Tests (2xH100-2xMI)",
        ),
        _step(
            "Distributed Tests (2xH100-2xMI355)",
            "distributed tests (2 gpus)",
            [
                "pytest -v -s tests/distributed/test_context_parallel.py",
                "pytest -v -s tests/distributed/test_rocm_quick_reduce.py",
            ],
            ".buildkite/test-amd.yaml",
            definition_id="amd#distributed-mi355",
            agent_pool="mi355_2",
            semantic_title="Distributed Tests (2xH100-2xMI)",
        ),
        _step(
            "GPQA Eval (GPT-OSS) (2xH100-2xMI300)",
            "gpqa eval (gpt-oss) (2 gpus)",
            [
                "pytest -s -v evals/gpt_oss/test_gpqa_correctness.py "
                "--config-list-file=configs/models-gfx942.txt",
            ],
            ".buildkite/test-amd.yaml",
            definition_id="amd#gpqa-mi300",
            agent_pool="mi300_2",
            semantic_title="GPQA Eval (GPT-OSS) (2xH100-2xMI)",
        ),
        _step(
            "GPQA Eval (GPT-OSS) (2xB200-2xMI355)",
            "gpqa eval (gpt-oss) (2 gpus)",
            [
                "pytest -s -v evals/gpt_oss/test_gpqa_correctness.py "
                "--config-list-file=configs/models-gfx950.txt",
            ],
            ".buildkite/test-amd.yaml",
            definition_id="amd#gpqa-mi355",
            agent_pool="mi355_2",
            semantic_title="GPQA Eval (GPT-OSS) (2xB200-2xMI)",
        ),
    ]

    families, family_by_definition = (
        config_parity._amd_identity_family_keys(steps)
    )

    assert len(families) == 2
    assert (
        family_by_definition["amd#distributed-mi300"]
        == family_by_definition["amd#distributed-mi355"]
    )
    assert (
        family_by_definition["amd#gpqa-mi300"]
        == family_by_definition["amd#gpqa-mi355"]
    )


def test_inline_mirror_excludes_only_its_exact_collision_row():
    identity = "v1 e2e (4 gpus)"
    amd = _step(
        "V1 e2e (4 GPUs)",
        identity,
        ["pytest available"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#generic",
    )
    mirrored = _step(
        "V1 e2e (4xH100)",
        identity,
        ["pytest mirrored"],
        ".buildkite/test_areas/basic.yaml",
        definition_id="upstream#h100",
    )
    available = _step(
        "V1 e2e (4 GPUs)",
        identity,
        ["pytest available"],
        ".buildkite/test_areas/basic.yaml",
        definition_id="upstream#generic",
    )
    mirrors = [{
        "nvidia_label": mirrored.label,
        "normalized": mirrored.normalized_label,
        "identity_key": identity,
        "source_file": mirrored.source_file,
        "nvidia_definition_id": mirrored.definition_id,
    }]

    matches, amd_only, upstream_only = _match_config_steps(
        [amd],
        [available, mirrored],
        mirrors,
    )

    assert not amd_only
    assert not upstream_only
    assert len(matches) == 1
    assert matches[0].nvidia_step is available


def test_inline_mirror_standalone_variant_is_covered_not_amd_only(
    monkeypatch,
):
    identity = "basic correctness"
    amd = _step(
        "Basic Correctness",
        identity,
        [
            "export VLLM_WORKER_MULTIPROC_METHOD=spawn",
            "pytest -v -s basic_correctness/test_mem.py",
            (
                "VLLM_TARGET_TEST_SUITE=MI300 pytest -v -s "
                "basic_correctness/test_basic_correctness.py"
            ),
            "pytest -v -s basic_correctness/test_cpu_offload.py",
        ],
        ".buildkite/test-amd.yaml",
        definition_id="amd#basic-correctness",
        agent_pool="mi300_1",
    )
    upstream_commands = [
        "export VLLM_WORKER_MULTIPROC_METHOD=spawn",
        "pytest -v -s basic_correctness/test_mem.py",
        "pytest -v -s basic_correctness/test_basic_correctness.py",
        "pytest -v -s basic_correctness/test_cpu_offload.py",
    ]
    mirrored = _step(
        "Basic Correctness",
        identity,
        upstream_commands,
        ".buildkite/test_areas/basic_correctness.yaml",
        definition_id="upstream#basic-correctness",
    )
    mirrors = [{
        "nvidia_label": mirrored.label,
        "normalized": mirrored.normalized_label,
        "identity_key": identity,
        "source_file": mirrored.source_file,
        "nvidia_definition_id": mirrored.definition_id,
        "nvidia_commands": upstream_commands,
        "amd_commands": upstream_commands,
        "commands_overridden": False,
        "command_similarity": 1.0,
        "amd_device": "mi300_1",
    }]
    monkeypatch.setattr(
        config_parity,
        "_load_config_steps",
        lambda: ([amd], [mirrored], mirrors),
    )
    monkeypatch.setattr(
        config_parity,
        "_source_provenance",
        lambda: {
            "commit_sha": "a" * 40,
            "fetched_at": "2026-07-30T00:00:00Z",
            "matching_rules": [],
        },
    )

    report = config_parity.build_config_parity()

    assert report["summary"]["matched"] == 0
    assert report["summary"]["direct_matches"] == 0
    assert report["summary"]["inline_mirror_variants"] == 1
    assert report["summary"]["covered"] == 1
    assert report["summary"]["amd_only"] == 0
    assert report["summary"]["coverage_rate_pct"] == 100.0
    assert report["amd_only"] == []
    variant = report["inline_mirror_variants"][0]
    assert variant["match_method"] == "inline_mirror_variant"
    assert variant["mirror_relationship"] == "effective_command_duplicate"
    assert variant["amd_label"] == "Basic Correctness"
    assert variant["nvidia_label"] == "Basic Correctness"
    assert variant["command_similarity"] == 1.0
    assert variant["inline_mirror_command_similarity"] == 1.0
    assert variant["amd_route_similarity"] == 1.0
    assert variant["inline_mirror_amd_device"] == "mi300_1"


# cspell:ignore torchao evals
def test_excess_same_identity_definition_is_an_additional_variant(
    monkeypatch,
):
    identity = "quantization"
    amd_primary = _step(
        "Quantization",
        identity,
        ["pytest -v -s quantization"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#quantization-mi300",
        agent_pool="mi300_1",
    )
    amd_additional = _step(
        "Quantization",
        identity,
        [
            "uv pip install --system torchao",
            "pytest -v -s quantization",
        ],
        ".buildkite/test-amd.yaml",
        definition_id="amd#quantization-mi355",
        agent_pool="mi355_1",
    )
    upstream = _step(
        "Quantization",
        identity,
        ["pytest -v -s quantization"],
        ".buildkite/test_areas/quantization.yaml",
        definition_id="upstream#quantization",
    )
    monkeypatch.setattr(
        config_parity,
        "_load_config_steps",
        lambda: ([amd_primary, amd_additional], [upstream], []),
    )
    monkeypatch.setattr(
        config_parity,
        "_source_provenance",
        lambda: {
            "commit_sha": "a" * 40,
            "fetched_at": "2026-07-30T00:00:00Z",
            "matching_rules": [],
        },
    )

    report = config_parity.build_config_parity()

    assert report["summary"]["matched"] == 1
    assert report["summary"]["additional_variants"] == 1
    assert report["summary"]["covered"] == 2
    assert report["summary"]["amd_only"] == 0
    assert report["summary"]["coverage_rate_pct"] == 100.0
    assert report["amd_only"] == []
    variant = report["additional_variants"][0]
    assert variant["match_method"] == "additional_variant"
    assert variant["amd_definition_id"] == "amd#quantization-mi355"
    assert variant["nvidia_definition_id"] == "upstream#quantization"
    assert variant["command_similarity"] < 1.0


def test_excess_same_identity_hardware_variant_is_source_covered(
    monkeypatch,
):
    identity = "lm eval large models (4 gpus)"
    amd_b200 = _step(
        "LM Eval Large Models (4xB200-4xMI355)",
        identity,
        ["pytest evals/large-b200"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#large-b200-mi355",
        agent_pool="mi355_4",
    )
    amd_a100 = _step(
        "LM Eval Large Models (4xA100-4xMI300)",
        identity,
        ["pytest evals/large-a100"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#large-a100-mi300",
        agent_pool="mi300_4",
    )
    upstream_b200 = _step(
        "LM Eval Large Models (4xB200)",
        identity,
        ["pytest evals/large-b200"],
        ".buildkite/test_areas/evals.yaml",
        definition_id="upstream#large-b200",
    )
    monkeypatch.setattr(
        config_parity,
        "_load_config_steps",
        lambda: ([amd_a100, amd_b200], [upstream_b200], []),
    )
    monkeypatch.setattr(
        config_parity,
        "_source_provenance",
        lambda: {
            "commit_sha": "a" * 40,
            "fetched_at": "2026-07-30T00:00:00Z",
            "matching_rules": [],
        },
    )

    report = config_parity.build_config_parity()

    assert report["summary"]["covered"] == 2
    assert report["summary"]["additional_variants"] == 1
    assert report["summary"]["amd_only"] == 0
    variant = report["additional_variants"][0]
    assert variant["amd_definition_id"] == "amd#large-a100-mi300"
    assert variant["variant_relationship"] == "additional_hardware_variant"


def test_unmatched_hardware_collision_is_not_an_additional_variant(
    monkeypatch,
):
    identity = "lm eval large models (4 gpus)"
    amd_step = _step(
        "LM Eval Large Models (4xA100-4xMI300)",
        identity,
        ["pytest evals/large-a100"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#large-a100-mi300",
        agent_pool="mi300_4",
    )
    upstream_step = _step(
        "LM Eval Large Models (4xB200)",
        identity,
        ["pytest evals/large-b200"],
        ".buildkite/test_areas/evals.yaml",
        definition_id="upstream#large-b200",
    )
    monkeypatch.setattr(
        config_parity,
        "_load_config_steps",
        lambda: ([amd_step], [upstream_step], []),
    )
    monkeypatch.setattr(
        config_parity,
        "_source_provenance",
        lambda: {
            "commit_sha": "a" * 40,
            "fetched_at": "2026-07-30T00:00:00Z",
            "matching_rules": [],
        },
    )

    report = config_parity.build_config_parity()

    assert report["summary"]["matched"] == 0
    assert report["summary"]["additional_variants"] == 0
    assert report["summary"]["covered"] == 0
    assert report["summary"]["amd_only"] == 1
    assert report["summary"]["nvidia_only"] == 1
    assert report["additional_variants"] == []


def test_inline_mirror_blocks_cross_identity_nightly_command_twin():
    amd = _step(
        "Spec Decode Draft Model",
        "spec decode draft model",
        ["pytest tests/spec_decode/draft_model"],
        ".buildkite/test-amd.yaml",
        definition_id="amd#spec-decode-draft",
    )
    mirrored = _step(
        "Spec Decode Draft Model",
        "spec decode draft model",
        ["pytest tests/spec_decode/draft_model"],
        ".buildkite/test_areas/spec_decode.yaml",
        definition_id="upstream#spec-decode-draft",
    )
    nightly_b200 = _step(
        "Spec Decode Draft Model Nightly B200",
        "spec decode draft model nightly b200",
        ["pytest tests/spec_decode/draft_model"],
        ".buildkite/test_areas/spec_decode.yaml",
        definition_id="upstream#spec-decode-draft-b200",
    )
    mirrors = [{
        "nvidia_label": mirrored.label,
        "identity_key": mirrored.identity_key,
        "source_file": mirrored.source_file,
        "nvidia_definition_id": mirrored.definition_id,
    }]

    matches, amd_only, upstream_only = _match_config_steps(
        [amd],
        [nightly_b200, mirrored],
        mirrors,
    )

    assert not matches
    assert amd_only == [amd]
    assert upstream_only == [nightly_b200]


def test_source_provenance_describes_collision_safe_matching(monkeypatch):
    snapshot = config_parity.ConfigSourceSnapshot(
        commit_sha="b" * 40,
        files={},
        fetched_at="2026-07-28T00:00:00Z",
    )
    monkeypatch.setattr(
        config_parity,
        "_load_source_snapshot",
        lambda: snapshot,
    )

    rules = config_parity._source_provenance()["matching_rules"]
    joined = " ".join(rules)

    assert "every parsed YAML definition" in joined
    assert "physical AMD count separately" in joined
    assert "same matrix canonical title, execution fingerprint" in joined
    assert "projected reference hardware, and GPU count" in joined
    assert "total_amd_steps as collision-safe parity nodes" in joined
    assert "amd_matrix_semantic_rows separately" in joined
    assert "exact upstream definition ID" in joined
    assert "mirror-linked variants instead of AMD-only gaps" in joined
    assert "additional execution or hardware variants" in joined
    assert "including an inline mirror, blocks this fallback" in joined
    assert "reference-hardware or GPU-count mismatches" in joined
    assert "maximum-cardinality one-to-one assignment" in joined
    assert "exact YAML label" in joined
    assert "platform target-suite selector" in joined
    assert "Ambiguous command matches remain unmatched" in joined
