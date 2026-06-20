"""Unit tests for the review-only AMD gating target candidate audit."""

from __future__ import annotations

from vllm import collect_gating_target_candidates as collector


def test_hardware_fold_key_collapses_hardware_only_duplicates() -> None:
    assert collector.hardware_fold_key("V1 attention (B200)") == collector.hardware_fold_key(
        "V1 attention (H100-MI300)"
    )
    assert collector.hardware_fold_key("Batch Invariance (H100)") == collector.hardware_fold_key(
        "Batch Invariance (A100)"
    )
    assert collector.hardware_fold_key("Distributed Tests (2xH100-2xMI300)") == collector.hardware_fold_key(
        "Distributed Tests (2 GPUs)(B200)"
    )


def test_hardware_fold_key_preserves_gpu_counts() -> None:
    one_gpu = collector.hardware_fold_key("Kernels FP8 MoE Test (1 H100)")
    two_gpus = collector.hardware_fold_key("Kernels FP8 MoE Test (2 H100s)")

    assert one_gpu != two_gpus
    assert "(1 gpus)" in one_gpu
    assert "(2 gpus)" in two_gpus


def test_gpu_like_detection_understands_default_buildkite_gpu_queues() -> None:
    assert collector.is_gpu_like_job({"raw_name": "Entrypoints Integration", "q": "gpu_1_queue"})
    assert collector.is_gpu_like_job({"raw_name": "Distributed Comm Ops", "q": "gpu_4_queue"})
    assert collector.is_gpu_like_job({"raw_name": "Cudagraph", "q": "gpu_1_queue"})
    assert not collector.is_gpu_like_job({"raw_name": "CPU Smoke Test", "q": "gpu_1_queue"})
    assert not collector.is_gpu_like_job({"raw_name": "Ascend NPU Test", "q": "gpu_1_queue"})


def test_build_job_url_prefers_exact_ids_over_broad_urls() -> None:
    build = {"web_url": "https://buildkite.com/vllm/ci/builds/100"}

    assert collector.build_job_url(build, {
        "url": "https://buildkite.com/vllm/ci/builds/999",
        "job_id": "job-uuid",
        "step_id": "step-uuid",
    }) == "https://buildkite.com/vllm/ci/builds/100/steps/canvas?jid=job-uuid&tab=output"


def test_collect_upstream_candidates_classifies_review_buckets() -> None:
    targets = {
        "groups": [
            {
                "id": 1,
                "label": "V1 attention (H100-MI300)",
                "area": "attention",
                "gating_signal": "green",
                "pf_signal": "green",
                "assigned_signal": "green",
            },
            {
                "id": 2,
                "label": "Kernels FP8 MoE Test (1 H100)",
                "area": "kernels",
                "gating_signal": "red",
                "pf_signal": "red",
                "assigned_signal": "assigned",
            },
            {
                "id": 3,
                "label": "2 Node Test (4 GPUs)",
                "area": "distributed",
                "gating_signal": "red",
                "pf_signal": "purple",
                "assigned_signal": "green",
            },
        ]
    }
    upstream_build = {
        "number": 100,
        "web_url": "https://buildkite.com/vllm/ci/builds/100",
        "jobs": [
            {"raw_name": "V1 attention (H100-MI300)", "q": "gpu", "state": "passed", "job_id": "j1"},
            {"raw_name": "V1 attention (B200)", "q": "gpu", "state": "passed", "job_id": "j2"},
            {"raw_name": "Brand New GPU Eval (H100)", "q": "gpu", "state": "passed", "job_id": "j3"},
            {"raw_name": "Default Queue GPU Eval", "q": "gpu_1_queue", "state": "passed", "job_id": "j8"},
            {"raw_name": "2 Node Test (4 GPUs)", "q": "large", "state": "passed", "job_id": "j7"},
            {"raw_name": "CPU Smoke Test", "q": "cpu", "state": "passed", "job_id": "j4"},
            {"raw_name": "Model Runner V2 Selection (H100)", "q": "gpu", "state": "passed", "job_id": "j5"},
            {"raw_name": "AMD: Existing Mirror (mi325_1)", "q": "mi325_1", "state": "passed", "job_id": "j6"},
        ],
    }
    internal_build = {
        "number": 200,
        "web_url": "https://buildkite.com/vllm/amd-ci/builds/200",
        "jobs": [
            {
                "raw_name": "mi325_1: Brand New GPU Eval (mi325_1)",
                "q": "mi325_1",
                "state": "passed",
                "job_id": "a1",
            }
        ],
    }
    proposals = {
        "pull_requests": [
            {
                "number": 44969,
                "url": "https://github.com/vllm-project/vllm/pull/44969",
                "title": "Gate more ROCm tests",
                "author": "AndreasKaratzas",
                "new_mirrors": [
                    {
                        "label": "Brand New GPU Eval",
                        "device": "mi325_1",
                        "yaml_file": ".buildkite/test_areas/misc.yaml",
                    }
                ],
            }
        ]
    }

    rows, summary = collector.collect_upstream_candidates(
        upstream_build,
        internal_build,
        targets,
        proposals,
    )
    by_decision = summary["by_decision"]

    assert summary["canonical_match_count"] == 2
    assert summary["likely_duplicate_count"] == 1
    assert summary["new_candidate_count"] == 2
    assert summary["missing_from_upstream_count"] == 1
    assert by_decision["excluded"] == 2

    duplicate = next(row for row in rows if row["decision"] == "likely_duplicate")
    assert duplicate["label"] == "V1 attention (B200)"
    assert duplicate["duplicate_of"] == "V1 attention (H100-MI300)"

    candidate = next(row for row in rows if row["decision"] == "new_candidate")
    assert candidate["label"] == "Brand New GPU Eval (H100)"
    assert candidate["readiness_signal"] == "green"
    assert candidate["internal_signal"]["state"] == "passed"
    assert candidate["proposal_matches"][0]["pr"] == 44969
    assert any(
        row["decision"] == "new_candidate" and row["label"] == "Default Queue GPU Eval"
        for row in rows
    )

    exclusions = {row["label"]: row["exclusion_reasons"] for row in rows if row["decision"] == "excluded"}
    assert "cpu_or_non_gpu" in exclusions["CPU Smoke Test"]
    assert "experimental_model_runner_v2" in exclusions["Model Runner V2 Selection (H100)"]
    assert all("Existing Mirror" not in row["label"] for row in rows)


def test_build_payload_exposes_review_only_schema() -> None:
    payload = collector.build_payload(
        {"groups": []},
        {"ci": {"builds": []}, "amd-ci": {"builds": []}},
        {"pull_requests": []},
    )

    assert payload["source"]["canonical_targets"] == "data/vllm/ci/gating_targets.json"
    assert payload["heuristics"]["safety"].startswith("This artifact is review-only")
    assert payload["summary"]["canonical_target_count"] == 0
    assert payload["rows"] == []
