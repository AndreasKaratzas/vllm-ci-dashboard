"""Unit tests for ``scripts/vllm/collect_amd_test_matrix.py``."""
# cspell:ignore evals

from __future__ import annotations

from vllm.collect_amd_test_matrix import (
    aggregate_state,
    build_buildkite_job_index,
    build_hotness_job_index,
    build_latest_job_index,
    build_matrix,
    build_parity_amd_index,
    canonical_title,
    latest_build_metadata,
    longest_shared_title_substring,
    merge_latest_job_indexes,
    parse_steps,
    strip_shard_index,
    yaml_url_for_build,
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


def test_default_yaml_url_is_pinned_to_the_observed_build_commit():
    commit = "7f599d78546819948c32f2b23d913507bbb38875"

    assert yaml_url_for_build({"commit": commit}) == (
        "https://raw.githubusercontent.com/vllm-project/vllm/"
        f"{commit}/.buildkite/test-amd.yaml"
    )
    assert yaml_url_for_build(
        {"commit": commit}, "https://example.invalid/override.yml"
    ) == "https://example.invalid/override.yml"


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


def test_build_matrix_trusts_passed_buildkite_state_over_stale_hw_failures():
    steps, architectures = parse_steps("""
steps:
  - label: V1 e2e (4xH100-4xMI300)
    agent_pool: mi300_4
""")
    analytics = {
        "amd-ci": {
            "builds": [
                {
                    "number": 10649,
                    "date": "2026-07-09",
                    "web_url": "https://buildkite.com/vllm/amd-ci/builds/10649",
                    "message": "AMD Full CI Run - nightly",
                    "jobs": [
                        {
                            "name": "V1 e2e (4xH100-4xMI300)",
                            "state": "passed",
                            "q": "amd_mi300_4",
                        }
                    ],
                }
            ]
        }
    }
    parity = {
        "job_groups": [
            {
                "amd_job_name": "mi300_4: V1 e2e (4xH100-4xMI300)",
                "amd": {
                    "total": 4,
                    "passed": 4,
                    "failed": 0,
                    "skipped": 0,
                    "xfailed": 0,
                    "xpassed": 0,
                    "error": 0,
                    "duration": 1.0,
                },
                "upstream": None,
                "hardware": ["mi300"],
                "hw_failures": {"mi300": 2},
                "hw_canceled": None,
                "job_links": [
                    {
                        "hw": "mi300",
                        "url": "https://buildkite.com/vllm/amd-ci/builds/10649/steps/canvas?sid=v1-e2e&tab=output",
                        "job_name": "mi300_4: V1 e2e (4xH100-4xMI300)",
                        "side": "amd",
                    }
                ],
                "status": "both",
                "backfilled": False,
                "hw_backfilled": {},
            }
        ]
    }
    latest_job_index, latest_build = build_latest_job_index(analytics, [])
    parity_exact_index, parity_norm_index = build_parity_amd_index(parity, [])

    matrix = build_matrix(
        steps=steps,
        architectures=architectures,
        latest_job_index=latest_job_index,
        latest_build=latest_build,
        parity_exact_index=parity_exact_index,
        parity_norm_index=parity_norm_index,
        shard_bases=[],
        yaml_url="https://example.invalid/test-amd.yaml",
    )

    row = matrix["rows"][0]
    assert row["cells"]["mi300"]["latest_state"] == "passed"
    assert matrix["summary"]["passing_cells"] == 1
    assert matrix["summary"]["failing_cells"] == 0


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


def test_buildkite_detail_is_authoritative_for_non_pytest_matrix_jobs():
    steps, architectures = parse_steps("""
steps:
  - label: Docker Build Metadata (ROCm)
    agent_pool: mi250_1
""")
    analytics = {
        "amd-ci": {
            "builds": [
                {
                    "number": 10972,
                    "date": "2026-07-17",
                    "web_url": "https://buildkite.com/vllm/amd-ci/builds/10972",
                    "message": "AMD Full CI Run - nightly",
                    "jobs": [
                        {
                            "name": "Docker Build Metadata (ROCm)",
                            "state": "failed",
                            "q": "amd_mi250_1",
                        }
                    ],
                }
            ]
        }
    }
    detail = {
        "number": 10972,
        "jobs": [
            {
                "id": "current-job",
                "type": "script",
                "name": "mi250_1: Docker Build Metadata (ROCm)",
                "state": "passed",
                "soft_failed": False,
                "agent_query_rules": ["queue=amd_mi250_1"],
                "step": {"id": "docker-metadata-step"},
            },
            {
                "id": "superseded-job",
                "type": "script",
                "name": "mi250_1: Docker Build Metadata (ROCm)",
                "state": "failed",
                "retried_in_job_id": "current-job",
                "agent_query_rules": ["queue=amd_mi250_1"],
            },
            {
                "id": "group-row",
                "type": "group",
                "name": "mi250_1: Docker Build Metadata (ROCm)",
            },
        ],
    }
    analytics_index, latest_build = build_latest_job_index(analytics, [])
    buildkite_index = build_buildkite_job_index(detail, [])
    latest_job_index = merge_latest_job_indexes(buildkite_index, analytics_index)

    matrix = build_matrix(
        steps=steps,
        architectures=architectures,
        latest_job_index=latest_job_index,
        latest_build=latest_build,
        parity_exact_index={},
        parity_norm_index={},
        shard_bases=[],
        yaml_url="https://example.invalid/test-amd.yaml",
    )

    cell = matrix["rows"][0]["cells"]["mi250"]
    assert cell["latest_state"] == "passed"
    assert cell["variants"][0]["latest_match_count"] == 1
    assert cell["latest_url"] == (
        "https://buildkite.com/vllm/amd-ci/builds/10972/steps/canvas"
        "?jid=current-job&tab=output"
    )


def test_hotness_fills_latest_build_job_missing_from_parsed_analytics():
    steps, architectures = parse_steps("""
steps:
  - label: Docker Build Metadata (ROCm)
    agent_pool: mi250_1
""")
    analytics = {
        "amd-ci": {
            "builds": [
                {
                    "number": 10972,
                    "date": "2026-07-17",
                    "web_url": "https://buildkite.com/vllm/amd-ci/builds/10972",
                    "message": "AMD Full CI Run - nightly",
                    "jobs": [],
                }
            ]
        }
    }
    hotness_url = (
        "https://buildkite.com/vllm/amd-ci/builds/10972"
        "#019f6f4e-00eb-43b1-8087-433d6a711d28"
    )
    exact_url = (
        "https://buildkite.com/vllm/amd-ci/builds/10972/steps/canvas"
        "?jid=019f6f4e-00eb-43b1-8087-433d6a711d28&tab=output"
    )
    hotness = {
        "test_groups": [
            {
                "group": "Docker Build Metadata (ROCm)",
                "hw": "mi250",
                "latest_evidence": {
                    "pipeline": "amd-ci",
                    "build_number": 10972,
                    "job_name": "mi250_1: Docker Build Metadata (ROCm)",
                    "job_id": "019f6f4e-00eb-43b1-8087-433d6a711d28",
                    "job_url": hotness_url,
                    "state": "passed",
                },
            }
        ]
    }
    latest_job_index, latest_build = build_latest_job_index(analytics, [])
    fallback = build_hotness_job_index(hotness, latest_build["number"], [])
    merge_latest_job_indexes(latest_job_index, fallback)

    matrix = build_matrix(
        steps=steps,
        architectures=architectures,
        latest_job_index=latest_job_index,
        latest_build=latest_build,
        parity_exact_index={},
        parity_norm_index={},
        shard_bases=[],
        yaml_url="https://example.invalid/test-amd.yaml",
    )

    cell = matrix["rows"][0]["cells"]["mi250"]
    assert cell["latest_matched"] is True
    assert cell["latest_state"] == "passed"
    assert cell["latest_url"] == exact_url


def test_hotness_fallback_rejects_evidence_from_another_build():
    hotness = {
        "test_groups": [
            {
                "group": "Docker Build Metadata (ROCm)",
                "hw": "mi250",
                "latest_evidence": {
                    "pipeline": "amd-ci",
                    "build_number": 10971,
                    "job_name": "mi250_1: Docker Build Metadata (ROCm)",
                    "state": "passed",
                },
            }
        ]
    }

    assert not build_hotness_job_index(hotness, 10972, [])


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


def test_duplicate_policy_uses_equal_commands_and_shared_title_substring():
    yaml_text = """
steps:
  - label: Core Check MI250
    agent_pool: mi250_1
    commands: [pytest tests/core.py]
  - label: Core Check MI300
    agent_pool: mi300_1
    commands: [pytest tests/core.py]
  - label: Core Check MI355
    agent_pool: mi355_1
    commands: [pytest tests/core.py]
  - label: QZX
    agent_pool: mi325_1
    commands: [pytest tests/core.py]
  - label: MI355 Standalone
    agent_pool: mi355_1
    commands: [pytest tests/standalone.py]
"""
    steps, architectures = parse_steps(yaml_text)
    analytics = {
        "amd-ci": {
            "builds": [
                {
                    "number": 9001,
                    "date": "2026-07-17",
                    "web_url": "https://buildkite.com/vllm/amd-ci/builds/9001",
                    "message": "AMD Full CI Run - nightly",
                    "jobs": [
                        {"name": "Core Check MI250", "state": "passed", "q": "amd_mi250_1"},
                        {"name": "Core Check MI300", "state": "failed", "q": "amd_mi300_1"},
                        {"name": "Core Check MI355", "state": "failed", "q": "amd_mi355_1"},
                        {"name": "QZX", "state": "passed", "q": "amd_mi325_1"},
                        {"name": "MI355 Standalone", "state": "passed", "q": "amd_mi355_1"},
                    ],
                }
            ]
        }
    }
    latest_job_index, latest_build = build_latest_job_index(analytics, [])
    matrix = build_matrix(
        steps=steps,
        architectures=architectures,
        latest_job_index=latest_job_index,
        latest_build=latest_build,
        parity_exact_index={},
        parity_norm_index={},
        shard_bases=[],
        yaml_url="https://example.invalid/test-amd.yaml",
    )

    assert len(matrix["duplicate_groups"]) == 1
    duplicate = matrix["duplicate_groups"][0]
    assert duplicate["member_titles"] == [
        "Core Check MI250",
        "Core Check MI300",
        "Core Check MI355",
    ]
    assert all(len(match["shared_substring"]) >= 2 for match in duplicate["pair_matches"])
    assert matrix["summary"]["latest_build_number"] == 9001
    assert matrix["summary"]["definition_rows"] == 5
    assert matrix["summary"]["reduced_unique_groups"] == 3

    policies = matrix["summary"]["health_policies"]
    assert policies["reduced_ignore_mi355"] == {
        "passing_groups": 1,
        "failed_only_groups": 0,
        "mixed_groups": 1,
        "waiting_groups": 0,
        "unknown_groups": 0,
        "ignored_mi355_only_groups": 1,
        "inherited_mi355_groups": 1,
        "failing_groups": 1,
        "resolved_groups": 2,
        "included_groups": 2,
        "pass_percentage": 50.0,
        "reduce_duplicates": True,
        "ignore_mi355_only": True,
    }
    assert policies["reduced_include_mi355"]["passing_groups"] == 2
    assert policies["reduced_include_mi355"]["pass_percentage"] == 66.7
    assert policies["definitions_ignore_mi355"]["passing_groups"] == 2
    assert policies["definitions_ignore_mi355"]["failing_groups"] == 2
    assert policies["definitions_ignore_mi355"]["inherited_mi355_groups"] == 1
    assert policies["definitions_include_mi355"]["passing_groups"] == 3


def test_duplicate_policy_requires_two_shared_title_characters_and_commands():
    assert longest_shared_title_substring("Core Check MI250", "Core Check MI300")
    assert len(longest_shared_title_substring("AB", "AX")) < 2

    steps, architectures = parse_steps("""
steps:
  - label: Alpha
    agent_pool: mi250_1
    commands: [pytest tests/a.py]
  - label: Alpha variant
    agent_pool: mi300_1
    commands: [pytest tests/b.py]
  - label: QZX
    agent_pool: mi325_1
    commands: [pytest tests/a.py]
  - label: Empty One
    agent_pool: mi250_1
  - label: Empty Two
    agent_pool: mi300_1
""")
    matrix = build_matrix(
        steps=steps,
        architectures=architectures,
        latest_job_index={},
        latest_build=None,
        parity_exact_index={},
        parity_norm_index={},
        shard_bases=[],
        yaml_url="https://example.invalid/test-amd.yaml",
    )
    assert matrix["duplicate_groups"] == []
    assert matrix["summary"]["reduced_unique_groups"] == 5
