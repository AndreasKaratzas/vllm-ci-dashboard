"""Unit tests for ``scripts/vllm/collect_amd_test_matrix.py``."""
# cspell:ignore evals

from __future__ import annotations

import json

import pytest

from vllm.collect_amd_test_matrix import (
    aggregate_state,
    build_buildkite_job_index,
    build_hotness_job_index,
    build_latest_job_index,
    build_matrix,
    build_parity_amd_index,
    canonical_title,
    frozen_or_analytics_job_index,
    latest_build_metadata,
    load_frozen_build_snapshot,
    longest_shared_title_substring,
    merge_latest_job_indexes,
    _parity_state_for_arch,
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


def test_frozen_snapshot_is_authoritative_over_later_analytics_roster(tmp_path):
    snapshot_path = tmp_path / "amd_nightly_snapshot.json"
    snapshot_path.write_text(json.dumps({
        "schema_version": 1,
        "pipeline": "amd-ci",
        "build": {
            "number": 10972,
            "commit": "a" * 40,
            "jobs": [
                {
                    "id": "passed",
                    "type": "script",
                    "name": "mi300_1: Passed Group",
                    "state": "passed",
                    "agent_query_rules": ["queue=amd_mi300_1"],
                },
                {
                    "id": "running",
                    "type": "script",
                    "name": "mi300_1: Running Group",
                    "state": "running",
                    "agent_query_rules": ["queue=amd_mi300_1"],
                },
            ],
        },
    }))
    analytics = {
        "amd-ci": {
            "builds": [{
                "number": 10972,
                "jobs": [
                    {"name": "Passed Group", "state": "passed", "q": "amd_mi300_1"},
                    {"name": "Running Group", "state": "passed", "q": "amd_mi300_1"},
                    {"name": "Finished Later", "state": "passed", "q": "amd_mi300_1"},
                ],
            }]
        }
    }
    analytics_index, _ = build_latest_job_index(analytics, [])

    frozen = load_frozen_build_snapshot(snapshot_path, 10972)
    selected = frozen_or_analytics_job_index(analytics_index, frozen, [])

    assert set(selected["mi300"]) == {"passed group", "running group"}
    assert selected["mi300"]["running group"][0]["state"] == "running"
    assert "finished later" not in selected["mi300"]


def test_frozen_snapshot_rejects_wrong_build(tmp_path):
    snapshot_path = tmp_path / "amd_nightly_snapshot.json"
    snapshot_path.write_text(json.dumps({
        "schema_version": 1,
        "pipeline": "amd-ci",
        "build": {"number": 10971, "jobs": []},
    }))

    with pytest.raises(ValueError, match=r"expected #10972, found #10971"):
        load_frozen_build_snapshot(snapshot_path, 10972)


def test_missing_frozen_snapshot_uses_collected_analytics_without_network(tmp_path):
    analytics_index = {"mi300": {"engine": [{"state": "passed"}]}}

    assert load_frozen_build_snapshot(tmp_path / "missing.json", 10972) is None
    assert frozen_or_analytics_job_index(analytics_index, None, []) is analytics_index


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


def test_canonical_title_strips_device_prefix_and_hardware_suffix():
    assert canonical_title(":amd: (MI355) Attention Kernels Shard %N") == (
        "Attention Kernels Shard"
    )
    assert canonical_title(":computer: (CPU) Basic Models Other") == (
        "Basic Models Other"
    )
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


def test_strip_shard_index_unifies_template_and_numeric_runtime_labels():
    shard_bases = ["kernels moe test"]
    assert strip_shard_index("Kernels MoE Test %N", shard_bases) == "kernels moe test"
    assert strip_shard_index("Kernels MoE Test 2", shard_bases) == "kernels moe test"
    assert strip_shard_index("Entrypoints Integration (API Server 2)", shard_bases) == (
        "entrypoints integration (api server 2)"
    )


def test_strip_shard_index_unifies_nested_runtime_prefix_and_decorated_template():
    shard_bases = ["attention kernels shard"]

    assert strip_shard_index(
        ":amd: (MI300) Attention Kernels Shard %N", shard_bases
    ) == "attention kernels shard"
    assert strip_shard_index(
        "mi300_1: :amd: (MI300) Attention Kernels Shard 2", shard_bases
    ) == "attention kernels shard"


def test_matrix_matches_nested_runtime_prefix_for_decorated_shards():
    steps, architectures = parse_steps("""
steps:
  - label: ":amd: (MI300) Attention Kernels Shard %N"
    agent_pool: mi300_1
    parallelism: 2
""")
    build = {
        "number": 12275,
        "web_url": "https://buildkite.com/vllm/amd-ci/builds/12275",
        "jobs": [
            {
                "id": f"mi300-attention-{shard}",
                "type": "script",
                "name": (
                    "mi300_1: :amd: (MI300) "
                    f"Attention Kernels Shard {shard}"
                ),
                "state": "passed",
                "agent_query_rules": ["queue=amd_mi300_1"],
            }
            for shard in range(2)
        ],
    }
    shard_bases = ["attention kernels shard"]
    latest_job_index = build_buildkite_job_index(build, shard_bases)

    matrix = build_matrix(
        steps=steps,
        architectures=architectures,
        latest_job_index=latest_job_index,
        latest_build=build,
        parity_exact_index={},
        parity_norm_index={},
        shard_bases=shard_bases,
        yaml_url="https://example.invalid/test-amd.yaml",
    )

    cell = matrix["rows"][0]["cells"]["mi300"]
    assert cell["latest_matched"] is True
    assert cell["variants"][0]["latest_match_count"] == 2
    assert matrix["summary"]["hardware_cells"] == 1
    assert matrix["summary"]["latest_matched_cells"] == 1


def test_matrix_matches_literal_percent_n_jobs_for_mi300_and_mi355():
    steps, architectures = parse_steps("""
steps:
  - label: Kernels Quantization Test %N
    agent_pool: mi300_1
    parallelism: 2
  - label: Kernels Quantization Test %N
    agent_pool: mi355_1
    parallelism: 2
""")
    build = {
        "number": 11962,
        "web_url": "https://buildkite.com/vllm/amd-ci/builds/11962",
        "jobs": [
            {
                "id": "mi300-quantization",
                "type": "script",
                "name": "mi300_1: Kernels Quantization Test %N",
                "state": "passed",
                "agent_query_rules": ["queue=amd_mi300_1"],
            },
            {
                "id": "mi355-quantization",
                "type": "script",
                "name": "mi355_1: Kernels Quantization Test %N",
                "state": "passed",
                "agent_query_rules": ["queue=amd_mi355_1"],
            },
        ],
    }
    shard_bases = ["kernels quantization test"]
    latest_job_index = build_buildkite_job_index(build, shard_bases)

    matrix = build_matrix(
        steps=steps,
        architectures=architectures,
        latest_job_index=latest_job_index,
        latest_build=build,
        parity_exact_index={},
        parity_norm_index={},
        shard_bases=shard_bases,
        yaml_url="https://example.invalid/test-amd.yaml",
    )

    row = matrix["rows"][0]
    assert row["cells"]["mi300"]["latest_matched"] is True
    assert row["cells"]["mi355"]["latest_matched"] is True
    assert matrix["summary"]["hardware_cells"] == 2
    assert matrix["summary"]["latest_matched_cells"] == 2
    assert matrix["summary"]["unknown_cells"] == 0


def test_aggregate_state_prioritizes_failures():
    assert aggregate_state(["passed", "failed"]) == "failed"
    assert aggregate_state(["passed", "soft_fail"]) == "soft_fail"
    assert aggregate_state(["running", "soft_failed"]) == "soft_failed"
    assert aggregate_state(["scheduled", "passed"]) == "scheduled"


def test_parity_state_ignores_upstream_incidents_on_same_amd_hardware():
    row = {
        "amd": {"total": 1, "passed": 1},
        "hardware": ["mi300"],
        "amd_hardware": ["mi300"],
        "upstream_hardware": ["mi300"],
        "hw_failures": {"mi300": 2},
        "amd_hw_failures": {},
        "upstream_hw_failures": {"mi300": 2},
        "hw_canceled": {"mi300": 1},
        "amd_hw_canceled": {},
        "upstream_hw_canceled": {"mi300": 1},
    }

    assert _parity_state_for_arch(row, "mi300", None) == "passed"

    legacy = {
        "amd": {"total": 1, "failed": 1},
        "hardware": ["mi300"],
        "hw_failures": {"mi300": 1},
    }
    assert _parity_state_for_arch(legacy, "mi300", None) == "failed"


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
        "created_at": "2026-05-04T06:00:03Z",
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


def test_best_hardware_policy_splits_sensitive_mi355_and_uses_best_generic_status():
    steps, architectures = parse_steps("""
steps:
  - label: ":amd: (MI300) Attention Kernels Shard"
    agent_pool: mi300_1
    commands: [pytest -v -s kernels/attention]
  - label: ":amd: (MI355) Attention Kernels Shard"
    agent_pool: mi355_1
    commands: [pytest -v -s kernels/attention]
  - label: Generic Test
    agent_pool: mi300_1
    commands: [pytest -v -s tests/generic]
  - label: Generic Test
    agent_pool: mi355_1
    commands: [pytest -v -s tests/generic]
""")
    analytics = {
        "amd-ci": {
            "builds": [{
                "number": 11994,
                "jobs": [
                    {"name": ":amd: (MI300) Attention Kernels Shard", "state": "passed", "q": "amd_mi300_1"},
                    {"name": ":amd: (MI355) Attention Kernels Shard", "state": "failed", "q": "amd_mi355_1"},
                    {"name": "Generic Test", "state": "failed", "q": "amd_mi300_1"},
                    {"name": "Generic Test", "state": "passed", "q": "amd_mi355_1"},
                ],
            }]
        }
    }
    index, latest = build_latest_job_index(analytics, [])
    matrix = build_matrix(
        steps, architectures, index, latest, {}, {},
        [], "https://example.invalid/test-amd.yaml",
    )

    groups = matrix["health_groups"]
    assert len(groups) == 3
    assert matrix["summary"]["health_group_count"] == 3
    policy = matrix["summary"]["health_policies"]["best_hardware"]
    assert policy["passing_groups"] == 2
    assert policy["failing_groups"] == 1
    assert policy["pass_percentage"] == 66.7
    sensitive = next(group for group in groups if group["gate_kind"] == "mi355_sensitive")
    assert sensitive["status"] == "failed"
    assert sensitive["architectures"] == ["mi355"]
    generic_attention = next(
        group for group in groups
        if group["title"] == "Attention Kernels Shard"
        and group["gate_kind"] == "generic_best_hardware"
    )
    assert generic_attention["status"] == "passing"
    generic = next(group for group in groups if group["title"] == "Generic Test")
    assert generic["status"] == "passing"
    assert generic["architectures"] == ["mi300", "mi355"]

    owned = [
        (member["row_id"], member["architecture"])
        for group in groups
        for member in group["members"]
    ]
    source_cells = [
        (row["id"], arch)
        for row in matrix["rows"]
        for arch, cell in row["cells"].items()
        if cell.get("exists")
    ]
    assert sorted(owned) == sorted(source_cells)
    assert len(owned) == len(set(owned))


def test_best_hardware_policy_semantically_collapses_explicit_generic_aliases():
    steps, architectures = parse_steps("""
steps:
  - label: ":amd: (MI300) Entrypoints Integration (API Server OpenAI - Part 1)"
    agent_pool: mi300_1
    commands: [pytest entrypoints/openai/]
  - label: ":amd: (MI355) Entrypoints Integration (API Server OpenAI - Part 1)"
    agent_pool: mi355_1
    commands: [pytest entrypoints/openai]
  - label: ":amd: (MI300) Language Models (Extended Generation)"
    agent_pool: mi300_1
    commands: [install mamba-old, pytest models/language/generation]
  - label: ":amd: (MI355) Language Models (Extended Generation)"
    agent_pool: mi355_1
    commands: [install mamba-new, pytest models/language/generation]
""")
    matrix = build_matrix(
        steps, architectures, {}, None, {}, {}, [],
        "https://example.invalid/test-amd.yaml",
    )

    assert len(matrix["rows"]) == 4
    assert len(matrix["health_groups"]) == 2
    assert all(len(group["members"]) == 2 for group in matrix["health_groups"])
    assert all(group["gate_kind"] == "generic_best_hardware" for group in matrix["health_groups"])
    assert all("same test" in group["classification_reason"] for group in matrix["health_groups"])
    classification = matrix["best_hardware_policy"]["mi355_classification"]
    assert len(classification) == 2
    assert {item["classification"] for item in classification} == {"generic_replica"}
    assert all(member["commands"] for group in matrix["health_groups"] for member in group["members"])
    assert all(
        member["source_url"] == "https://example.invalid/test-amd.yaml"
        for group in matrix["health_groups"]
        for member in group["members"]
    )


def test_best_hardware_policy_declares_exactly_fifteen_mi355_sensitive_rules():
    from vllm.collect_amd_test_matrix import MI355_SENSITIVE_RULES

    expected_titles = {
        "Attention Benchmark Smoke",
        "Distributed Features",
        "GPQA Eval (GPT-OSS)",
        "LM Eval Qwen3-5 Models",
        "Qwen3-30B-A3B-FP8-block Sync EPLB Accuracy",
        "LM Eval Large Models",
        "Kernels",
        "MLA Kernels",
        "Attention Kernels Shard",
        "MoE Kernels Shard",
        "Quantization Kernels",
        "DeepEP FP8 MoE Kernels",
        "Quantized Models",
        "Quantization",
        "V1 Attention Shard",
    }
    assert len(MI355_SENSITIVE_RULES) == 15
    assert {title for title, _ in MI355_SENSITIVE_RULES} == expected_titles
    assert all(reason for _, reason in MI355_SENSITIVE_RULES)


def test_current_decorated_labels_materialize_every_sensitive_rule_and_alias():
    sensitive_titles = (
        "Attention Benchmark Smoke",
        "Distributed Features",
        "GPQA Eval (GPT-OSS)",
        "LM Eval Qwen3-5 Models",
        "Qwen3-30B-A3B-FP8-block Sync EPLB Accuracy",
        "LM Eval Large Models",
        "Kernels",
        "MLA Kernels",
        "Attention Kernels Shard",
        "MoE Kernels Shard",
        "Quantization Kernels",
        "DeepEP FP8 MoE Kernels",
        "Quantized Models",
        "Quantization",
        "V1 Attention Shard",
    )
    lines = ["steps:"]
    for index, title in enumerate(sensitive_titles):
        for architecture in ("MI300", "MI355"):
            lines.extend((
                f"  - label: {json.dumps(f':amd: ({architecture}) {title}')}",
                f"    agent_pool: {architecture.lower()}_1",
                f"    commands: [pytest tests/current_sensitive_gate_{index}_{architecture.lower()}.py]",
            ))
    lines.extend((
        '  - label: ":amd: (MI300) Entrypoints Integration (API Server OpenAI - Part 1)"',
        "    agent_pool: mi300_1",
        "    commands: [pytest entrypoints/openai/]",
        '  - label: ":amd: (MI355) Entrypoints Integration (API Server OpenAI - Part 1)"',
        "    agent_pool: mi355_1",
        "    commands: [pytest entrypoints/openai]",
        '  - label: ":amd: (MI300) Language Models (Extended Generation)"',
        "    agent_pool: mi300_1",
        "    commands: [install mamba-old, pytest models/language/generation]",
        '  - label: ":amd: (MI355) Language Models (Extended Generation)"',
        "    agent_pool: mi355_1",
        "    commands: [install mamba-new, pytest models/language/generation]",
    ))

    steps, architectures = parse_steps("\n".join(lines))
    matrix = build_matrix(
        steps, architectures, {}, None, {}, {}, [],
        "https://example.invalid/current-test-amd.yaml",
    )
    classification = matrix["best_hardware_policy"]["mi355_classification"]
    rows_by_id = {row["id"]: row for row in matrix["rows"]}
    separate = {
        rows_by_id[item["row_id"]]["canonical_title"]
        for item in classification
        if item["classification"] == "separate_gate"
    }
    assert separate == set(sensitive_titles)
    assert all(
        item["label"].startswith(":amd: (MI355) ")
        for item in classification
    )

    aliases = {
        "Entrypoints Integration (API Server OpenAI - Part 1)",
        "Language Models (Extended Generation)",
    }
    alias_groups = [
        group for group in matrix["health_groups"]
        if group["title"] in aliases
    ]
    assert {group["title"] for group in alias_groups} == aliases
    assert all(group["architectures"] == ["mi300", "mi355"] for group in alias_groups)
    assert all(group["gate_kind"] == "generic_best_hardware" for group in alias_groups)
