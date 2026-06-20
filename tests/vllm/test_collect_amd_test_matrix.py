"""Unit tests for ``scripts/vllm/collect_amd_test_matrix.py``."""
# cspell:ignore evals

from __future__ import annotations

from vllm.collect_amd_test_matrix import (
    aggregate_state,
    build_latest_job_index,
    build_matrix,
    build_parity_amd_index,
    canonical_title,
    latest_build_metadata,
    parse_steps,
    strip_shard_index,
)


SAMPLE_YAML = """
steps:
  - label: Kernels
    agent_pool: mi250_1
  - label: Kernels (B200-MI355)
    agent_pool: mi355_1
  - label: Distributed Tests (2xH100-2xMI250)
    agent_pool: mi300_2
  - label: Distributed Tests (2xH100-2xMI300)
    agent_pool: mi300_2
  - label: Distributed Tests (2xH100-2xMI355)
    agent_pool: mi355_1
  - label: Distributed Tests (2 GPUs)
    agent_pool: mi250_2
  - label: Distributed Tests (2 GPUs)
    agent_pool: mi355_2
  - label: LM Eval Small Models
    agent_pool: mi300_1
    working_dir: "/vllm-workspace/tests"
    commands:
      - pytest -s -v evals/gsm8k/test_gsm8k_correctness.py --config-list-file=configs/models-small.txt
  - label: LM Eval Small Models (MI300)
    agent_pool: mi300_1
    working_dir: "/vllm-workspace/.buildkite/lm-eval-harness"
    commands:
      - pytest -s -v test_lm_eval_correctness.py --config-list-file=configs/models-small-rocm.txt
  - label: Kernels MoE Test %N
    agent_pool: mi355_1
    parallelism: 4
"""


def _parity_row(amd_job_name, hw, url, failed=0):
    amd_hw = hw.lower()
    return {
        "amd_job_name": amd_job_name,
        "amd": {
            "total": 1,
            "passed": 0 if failed else 1,
            "failed": failed,
            "skipped": 0,
            "xfailed": 0,
            "xpassed": 0,
            "error": 0,
            "duration": 1.0,
        },
        "upstream": None,
        "hardware": [amd_hw],
        "hw_failures": {amd_hw: failed} if failed else None,
        "hw_canceled": None,
        "job_links": [
            {
                "hw": amd_hw,
                "url": url,
                "job_name": amd_job_name,
                "side": "amd",
            }
        ],
        "status": "amd_only",
        "backfilled": False,
        "hw_backfilled": {},
    }


def test_canonical_title_strips_hardware_suffix_only():
    assert canonical_title("Kernels (B200-MI355)") == "Kernels"
    assert canonical_title("LM Eval Small Models (2xB200-2xMI355)") == (
        "LM Eval Small Models (2xB200-2xMI)"
    )
    assert canonical_title("Distributed Tests (4xA100-4xMI300)") == (
        "Distributed Tests (4xA100-4xMI)"
    )
    assert canonical_title("Distributed Tests (2xH100-2xMI250)") == (
        "Distributed Tests (2xH100-2xMI)"
    )
    assert canonical_title("Distributed Tests (2xH100-2xMI355)") == (
        "Distributed Tests (2xH100-2xMI)"
    )
    assert canonical_title("LM Eval Small Models (MI300)") == "LM Eval Small Models"
    assert canonical_title("Distributed Tests (2 GPUs)") == "Distributed Tests (2 GPUs)"


def test_strip_shard_index_only_for_known_bases():
    shard_bases = ["kernels moe test"]
    assert strip_shard_index("Kernels MoE Test 2", shard_bases) == "kernels moe test"
    assert strip_shard_index("Entrypoints Integration (API Server 2)", shard_bases) == (
        "entrypoints integration (api server 2)"
    )


def test_aggregate_state_prioritizes_failures():
    assert aggregate_state(["passed", "failed"]) == "failed"
    assert aggregate_state(["passed", "soft_fail"]) == "soft_fail"
    assert aggregate_state(["scheduled", "passed"]) == "scheduled"


def test_latest_build_metadata_falls_back_to_ci_health_and_parity():
    meta = latest_build_metadata(
        None,
        {
            "amd": {
                "latest_build": {
                    "build_number": 8193,
                    "created_at": "2026-05-04T06:00:03Z",
                    "build_url": "https://buildkite.com/vllm/amd-ci/builds/8193",
                }
            }
        },
        {"amd_build": 8193, "amd_date": "2026-05-04"},
    )

    assert meta == {
        "number": 8193,
        "date": "2026-05-04",
        "web_url": "https://buildkite.com/vllm/amd-ci/builds/8193",
        "message": "AMD Full CI Run - nightly",
    }


def test_build_matrix_collapses_titles_and_matches_latest_nightly():
    steps, architectures = parse_steps(SAMPLE_YAML)
    analytics = {
        "amd-ci": {
            "builds": [
                {
                    "number": 7824,
                    "date": "2026-04-20",
                    "web_url": "https://buildkite.com/vllm/amd-ci/builds/7824",
                    "message": "AMD Full CI Run - nightly",
                    "jobs": [
                        {"name": "Kernels", "state": "passed", "q": "amd_mi250_1"},
                        {"name": "Kernels (B200-MI355)", "state": "failed", "q": "amd_mi355_1"},
                        {"name": "Distributed Tests (2 GPUs)", "state": "passed", "q": "amd_mi250_2"},
                        {"name": "Distributed Tests (2 GPUs)", "state": "passed", "q": "amd_mi355_2"},
                        {"name": "LM Eval Small Models", "state": "soft_fail", "q": "amd_mi300_1"},
                        {"name": "LM Eval Small Models (MI300)", "state": "failed", "q": "amd_mi300_1"},
                        {"name": "Kernels MoE Test 1", "state": "passed", "q": "amd_mi355_1"},
                        {"name": "Kernels MoE Test 2", "state": "passed", "q": "amd_mi355_1"},
                    ],
                }
            ]
        }
    }
    parity = {
        "job_groups": [
            _parity_row(
                "mi250_1: Kernels",
                "mi250",
                "https://buildkite.com/vllm/amd-ci/builds/7824/steps/canvas?sid=kernels-mi250&tab=output",
            ),
            _parity_row(
                "mi355_1: Kernels (B200-MI355)",
                "mi355",
                "https://buildkite.com/vllm/amd-ci/builds/7824/steps/canvas?sid=kernels-mi355&tab=output",
                failed=1,
            ),
            _parity_row(
                "mi250_2: Distributed Tests (2 GPUs)",
                "mi250",
                "https://buildkite.com/vllm/amd-ci/builds/7824/steps/canvas?sid=dist-mi250&tab=output",
            ),
            _parity_row(
                "mi355_2: Distributed Tests (2 GPUs)",
                "mi355",
                "https://buildkite.com/vllm/amd-ci/builds/7824/steps/canvas?sid=dist-mi355&tab=output",
            ),
            _parity_row(
                "mi300_2: Distributed Tests (2xH100-2xMI250)",
                "mi300",
                "https://buildkite.com/vllm/amd-ci/builds/7824/steps/canvas?sid=dist-mi300-mi250&tab=output",
            ),
            _parity_row(
                "mi300_2: Distributed Tests (2xH100-2xMI300)",
                "mi300",
                "https://buildkite.com/vllm/amd-ci/builds/7824/steps/canvas?sid=dist-mi300-mi300&tab=output",
                failed=1,
            ),
            _parity_row(
                "mi355_2: Distributed Tests (2xH100-2xMI355)",
                "mi355",
                "https://buildkite.com/vllm/amd-ci/builds/7824/steps/canvas?sid=dist-mi355-mi355&tab=output",
                failed=1,
            ),
            _parity_row(
                "mi300_1: LM Eval Small Models",
                "mi300",
                "https://buildkite.com/vllm/amd-ci/builds/7824/steps/canvas?sid=lm-eval-mi300&tab=output",
            ),
            _parity_row(
                "mi300_1: LM Eval Small Models (MI300)",
                "mi300",
                "https://buildkite.com/vllm/amd-ci/builds/7824/steps/canvas?sid=lm-eval-mi300-rocm&tab=output",
                failed=1,
            ),
            _parity_row(
                "mi355_1: Kernels MoE Test 1",
                "mi355",
                "https://buildkite.com/vllm/amd-ci/builds/7824/steps/canvas?sid=moe-1&tab=output",
            ),
        ]
    }
    shard_bases = ["kernels moe test"]
    latest_job_index, latest_build = build_latest_job_index(analytics, shard_bases)
    parity_exact_index, parity_norm_index = build_parity_amd_index(parity, shard_bases)
    matrix = build_matrix(
        steps=steps,
        architectures=architectures,
        latest_job_index=latest_job_index,
        latest_build=latest_build,
        parity_exact_index=parity_exact_index,
        parity_norm_index=parity_norm_index,
        shard_bases=shard_bases,
        yaml_url="https://example.invalid/test-amd.yaml",
    )

    assert [a["id"] for a in matrix["architectures"]] == ["mi250", "mi300", "mi355"]
    assert matrix["summary"]["unique_groups"] == 6
    assert matrix["summary"]["hardware_cells"] == 9
    assert matrix["summary"]["latest_matched_cells"] == 9
    assert matrix["summary"]["passing_cells"] == 4
    assert matrix["summary"]["failing_cells"] == 5

    rows = {row["title"]: row for row in matrix["rows"]}
    kernels = rows["Kernels"]
    assert kernels["coverage_count"] == 2
    assert kernels["cells"]["mi250"]["latest_state"] == "passed"
    assert kernels["cells"]["mi355"]["latest_state"] == "failed"
    assert kernels["cells"]["mi355"]["latest_url"].endswith("sid=kernels-mi355&tab=output")

    mirrored = rows["Distributed Tests (2xH100-2xMI)"]
    assert mirrored["coverage_count"] == 2
    assert mirrored["cells"]["mi300"]["variant_count"] == 1
    assert mirrored["cells"]["mi300"]["raw_variant_count"] == 2
    assert mirrored["cells"]["mi300"]["primary_label"] == "Distributed Tests (2xH100-2xMI300)"
    assert mirrored["cells"]["mi300"]["latest_state"] == "failed"
    mi300_entries = mirrored["cells"]["mi300"]["variants"][0]["entries"]
    assert {entry["label"] for entry in mi300_entries} == {
        "Distributed Tests (2xH100-2xMI250)",
        "Distributed Tests (2xH100-2xMI300)",
    }
    assert {
        entry["latest_url"] for entry in mi300_entries
    } == {
        "https://buildkite.com/vllm/amd-ci/builds/7824/steps/canvas?sid=dist-mi300-mi250&tab=output",
        "https://buildkite.com/vllm/amd-ci/builds/7824/steps/canvas?sid=dist-mi300-mi300&tab=output",
    }
    assert mirrored["cells"]["mi355"]["primary_label"] == "Distributed Tests (2xH100-2xMI355)"

    dist = rows["Distributed Tests (2 GPUs)"]
    assert dist["coverage_count"] == 2
    assert dist["nightly_coverage_count"] == 2

    lm_eval = rows["LM Eval Small Models"]
    assert lm_eval["coverage_count"] == 1
    assert lm_eval["cells"]["mi300"]["variant_count"] == 1
    assert lm_eval["cells"]["mi300"]["raw_variant_count"] == 1
    assert lm_eval["cells"]["mi300"]["primary_label"] == "LM Eval Small Models"
    assert lm_eval["cells"]["mi300"]["latest_state"] == "soft_fail"

    lm_eval_mi300 = rows["LM Eval Small Models (MI300)"]
    assert lm_eval_mi300["coverage_count"] == 1
    assert lm_eval_mi300["cells"]["mi300"]["variant_count"] == 1
    assert lm_eval_mi300["cells"]["mi300"]["raw_variant_count"] == 1
    assert lm_eval_mi300["cells"]["mi300"]["primary_label"] == "LM Eval Small Models (MI300)"
    assert lm_eval_mi300["cells"]["mi300"]["latest_state"] == "failed"
    assert lm_eval_mi300["cells"]["mi300"]["latest_url"].endswith("sid=lm-eval-mi300-rocm&tab=output")

    moe = rows["Kernels MoE Test"]
    assert moe["coverage_count"] == 1
    assert moe["cells"]["mi355"]["variant_count"] == 1
    assert moe["cells"]["mi355"]["variants"][0]["latest_match_count"] == 2
    assert moe["cells"]["mi355"]["latest_state"] == "passed"
    assert moe["cells"]["mi355"]["latest_url"].endswith("sid=moe-1&tab=output")
