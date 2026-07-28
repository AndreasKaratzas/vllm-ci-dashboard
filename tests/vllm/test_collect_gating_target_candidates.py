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


def test_percent_n_targets_aggregate_only_their_numbered_runtime_shards() -> None:
    targets = {
        "groups": [
            {
                "id": 1,
                "label": "Kernels MoE Test %N",
                "gating_signal": "green",
                "pf_signal": "green",
                "assigned_signal": "green",
            },
            {
                "id": 2,
                "label": "Kernels MoE Test 2",
                "gating_signal": "red",
                "pf_signal": "red",
                "assigned_signal": "red",
            },
        ]
    }
    upstream_build = {
        "number": 100,
        "web_url": "https://buildkite.com/vllm/ci/builds/100",
        "jobs": [
            {
                "raw_name": "Kernels MoE Test 1",
                "q": "gpu_1_queue",
                "state": "passed",
                "job_id": "shard-1",
            },
            {
                "raw_name": "Kernels MoE Test 2",
                "q": "gpu_1_queue",
                "state": "failed",
                "job_id": "exact-2",
            },
            {
                "raw_name": "Kernels MoE Test 3",
                "q": "gpu_1_queue",
                "state": "soft_fail",
                "job_id": "shard-3",
            },
            {
                "raw_name": "Unrelated GPU Test 7",
                "q": "gpu_1_queue",
                "state": "passed",
                "job_id": "unrelated-7",
            },
        ],
    }

    rows, summary = collector.collect_upstream_candidates(
        upstream_build,
        None,
        targets,
        {"pull_requests": []},
    )

    template = next(row for row in rows if row.get("target_id") == 1)
    assert template["decision"] == "canonical"
    assert template["label"] == "Kernels MoE Test %N"
    assert template["canonical_key"] == collector.hardware_fold_key("Kernels MoE Test %N")
    assert template["shard_count"] == 2
    assert template["shard_states"] == {"passed": 1, "soft_fail": 1}
    assert template["state"] == "soft_fail"
    assert [
        (row["label"], row["shard_index"])
        for row in template["runtime_shards"]
    ] == [
        ("Kernels MoE Test 1", 1),
        ("Kernels MoE Test 3", 3),
    ]

    exact_numeric = next(row for row in rows if row.get("target_id") == 2)
    assert exact_numeric["decision"] == "canonical"
    assert exact_numeric["label"] == "Kernels MoE Test 2"
    assert "runtime_shards" not in exact_numeric

    new_rows = [row for row in rows if row["decision"] == "new_candidate"]
    assert [row["label"] for row in new_rows] == ["Unrelated GPU Test 7"]
    assert summary["canonical_match_count"] == 2
    assert summary["new_candidate_count"] == 1
    assert summary["missing_from_upstream_count"] == 0


def test_percent_n_aggregation_preserves_hardware_counts() -> None:
    targets = {
        "groups": [
            {"id": 1, "label": "FP8 MoE (1 H100) %N"},
            {"id": 2, "label": "FP8 MoE (2 H100s) %N"},
        ]
    }
    upstream_build = {
        "number": 100,
        "web_url": "https://buildkite.com/vllm/ci/builds/100",
        "jobs": [
            {
                "raw_name": "FP8 MoE (1 H100) 1",
                "q": "gpu_1_queue",
                "state": "passed",
                "job_id": "one-gpu",
            },
            {
                "raw_name": "FP8 MoE (2 H100s) 1",
                "q": "gpu_2_queue",
                "state": "passed",
                "job_id": "two-gpu",
            },
        ],
    }

    rows, summary = collector.collect_upstream_candidates(
        upstream_build,
        None,
        targets,
        {"pull_requests": []},
    )

    canonical = {
        row["target_id"]: row
        for row in rows
        if row["decision"] == "canonical"
    }
    assert canonical[1]["runtime_shards"][0]["label"] == "FP8 MoE (1 H100) 1"
    assert canonical[2]["runtime_shards"][0]["label"] == "FP8 MoE (2 H100s) 1"
    assert canonical[1]["canonical_key"] != canonical[2]["canonical_key"]
    assert summary["canonical_match_count"] == 2
    assert summary["new_candidate_count"] == 0
    assert summary["missing_from_upstream_count"] == 0


def test_distinct_exact_targets_survive_shared_hardware_fold_key() -> None:
    targets = {
        "groups": [
            {"id": 1, "label": "V1 e2e (4 GPUs)"},
            {"id": 2, "label": "V1 e2e (4xH100)"},
        ]
    }
    upstream_build = {
        "number": 100,
        "web_url": "https://buildkite.com/vllm/ci/builds/100",
        "jobs": [
            {
                "raw_name": "V1 e2e (4 GPUs)",
                "q": "gpu_4_queue",
                "state": "passed",
                "job_id": "generic-four",
            },
            {
                "raw_name": "V1 e2e (4xH100)",
                "q": "gpu_4_queue",
                "state": "passed",
                "job_id": "h100-four",
            },
        ],
    }

    rows, summary = collector.collect_upstream_candidates(
        upstream_build,
        None,
        targets,
        {"pull_requests": []},
    )

    canonical = [row for row in rows if row["decision"] == "canonical"]
    assert [(row["target_id"], row["label"]) for row in canonical] == [
        (1, "V1 e2e (4 GPUs)"),
        (2, "V1 e2e (4xH100)"),
    ]
    assert summary["canonical_match_count"] == 2
    assert summary["missing_from_upstream_count"] == 0


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
