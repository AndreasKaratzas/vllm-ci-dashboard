from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from vllm.ci.ownership import (
    build_ownership_status,
    evaluate_availability,
    infer_target_area,
    matrix_runtime_targets,
    parity_area_index,
    select_owner,
    validate_ownership_config,
)


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


def _config():
    return validate_ownership_config(
        {
            "schema_version": 1,
            "ci_lead": {
                "display_name": "CI Lead",
                "github_login": "ci-lead",
            },
            "project": {
                "id": "project-id",
                "number": 2,
                "title": "AMD CI Operations",
                "url": "https://github.com/users/example/projects/2",
                "repository": "AndreasKaratzas/vllm-ci-dashboard",
            },
            "owners": [
                {"display_name": "Primary", "github_login": "primary"},
                {"display_name": "Secondary", "github_login": "secondary"},
                {"display_name": "Tertiary", "github_login": "tertiary"},
                {"display_name": "CI Lead", "github_login": "ci-lead"},
            ],
            "areas": {
                "kernels.yaml": [
                    {"rank": 2, "github_login": "secondary"},
                    {"rank": 1, "github_login": "primary"},
                    {"rank": 3, "github_login": "tertiary"},
                ]
            },
        }
    )


def test_config_normalizes_area_and_orders_rank_chain():
    config = _config()

    assert set(config["areas"]) == {"kernels"}
    assert [row["rank"] for row in config["areas"]["kernels"]] == [1, 2, 3]
    assert config["areas"]["kernels"][0]["display_name"] == "Primary"


def test_committed_rotation_matches_the_31_requested_rank_chains():
    from vllm.ci.ownership import load_ownership_config

    config = load_ownership_config(ROOT / "config" / "vllm_ci_ownership.json")
    expected = {
        "attention": ["djramic", "aarushjain29", "peizhang56"],
        "basic_correctness": ["gchinora", "music-dino", "divakar-amd"],
        "benchmarks": ["gyohuangxin", "gchinora", "djramic"],
        "compile": ["charlifu", "stefankoncarevic", "mawong-amd"],
        "cuda": ["gchinora", "aarushjain29", "music-dino"],
        "disaggregated": ["divakar-amd", "stefankoncarevic", "djramic"],
        "distributed": ["charlifu", "gyohuangxin", "aarushjain29"],
        "docker": ["mawong-amd", "peizhang56", "divakar-amd"],
        "e2e_integration": ["music-dino", "aarushjain29", "mawong-amd"],
        "engine": ["micah-wil", "divakar-amd", "stefankoncarevic"],
        "entrypoints": ["AndreasKaratzas", "charlifu", "stefankoncarevic"],
        "expert_parallelism": ["divakar-amd", "charlifu", "gchinora"],
        "fault_tolerance": ["djramic", "gyohuangxin", "mawong-amd"],
        "kernels": ["stefankoncarevic", "micah-wil", "djramic"],
        "lm_eval": ["peizhang56", "fxmarty-amd", "music-dino"],
        "lora": ["divakar-amd", "music-dino", "gyohuangxin"],
        "misc": ["AndreasKaratzas", "micah-wil", "gchinora"],
        "model_executor": ["gchinora", "charlifu", "aarushjain29"],
        "model_runner_v2": ["gyohuangxin", "stefankoncarevic", "music-dino"],
        "models_basic": ["aarushjain29", "peizhang56", "micah-wil"],
        "models_distributed": ["aarushjain29", "gchinora", "charlifu"],
        "models_language": ["mawong-amd", "AndreasKaratzas", "stefankoncarevic"],
        "models_multimodal": ["mawong-amd", "AndreasKaratzas", "music-dino"],
        "plugins": ["peizhang56", "aarushjain29", "gyohuangxin"],
        "pytorch": ["charlifu", "djramic", "micah-wil"],
        "quantization": ["fxmarty-amd", "micah-wil", "AndreasKaratzas"],
        "ray_compat": ["divakar-amd", "music-dino", "AndreasKaratzas"],
        "rust_frontend": ["aarushjain29", "mawong-amd", "peizhang56"],
        "samplers": ["AndreasKaratzas", "divakar-amd", "djramic"],
        "spec_decode": ["AndreasKaratzas", "stefankoncarevic", "peizhang56"],
        "weight_loading": ["micah-wil", "gchinora", "charlifu"],
    }

    assert {
        area: [owner["github_login"] for owner in chain]
        for area, chain in config["areas"].items()
    } == expected


def test_config_requires_complete_distinct_rank_chain():
    payload = _config()
    payload["areas"]["kernels"] = payload["areas"]["kernels"][:2]

    with pytest.raises(ValueError, match="exactly ranks 1, 2, and 3"):
        validate_ownership_config(payload)


def test_missing_availability_fails_closed_to_ci_lead():
    config = _config()
    availability, source = evaluate_availability(None, config["owners"], now=NOW)

    selected = select_owner(
        config["areas"]["kernels"],
        availability,
        config["ci_lead"],
    )

    assert source["configured"] is False
    assert selected["owner"]["github_login"] == "ci-lead"
    assert selected["escalated_to_ci_lead"] is True
    assert all("availability" not in row for row in selected["chain"])


def test_selection_walks_ranks_and_stops_at_first_available():
    config = _config()
    raw = {
        "schema_version": 1,
        "generated_at": "2026-07-28T11:59:00Z",
        "owners": {
            "primary": {
                "available": False,
                "valid_until": "2026-07-28T13:00:00Z",
            },
            "secondary": {
                "available": True,
                "valid_until": "2026-07-28T13:00:00Z",
            },
            "tertiary": {
                "available": True,
                "valid_until": "2026-07-28T13:00:00Z",
            },
            "ci-lead": {
                "available": True,
                "valid_until": "2026-07-28T13:00:00Z",
            },
        },
    }
    availability, _ = evaluate_availability(raw, config["owners"], now=NOW)

    selected = select_owner(
        config["areas"]["kernels"],
        availability,
        config["ci_lead"],
    )

    assert selected["owner"]["github_login"] == "secondary"
    assert selected["reason"] == "rank_2_selected"
    assert [row["github_login"] for row in selected["chain"]] == [
        "primary",
        "secondary",
        "tertiary",
    ]
    assert all("availability" not in row for row in selected["chain"])


def test_working_hours_and_pto_are_evaluated_without_exposing_schedule():
    config = _config()
    raw = {
        "schema_version": 1,
        "generated_at": "2026-07-28T11:59:00Z",
        "owners": {
            "primary": {
                "timezone": "UTC",
                "working_hours": {
                    "weekdays": [0, 1, 2, 3, 4],
                    "start": "09:00",
                    "end": "17:00",
                },
                "pto": [
                    {
                        "start": "2026-07-28T00:00:00Z",
                        "end": "2026-07-29T00:00:00Z",
                    }
                ],
            },
        },
    }
    availability, source = evaluate_availability(raw, config["owners"], now=NOW)

    assert source["fresh"] is True
    assert availability["primary"] == {
        "status": "unavailable",
        "reason": "unavailable",
    }
    assert "pto" not in availability["primary"]
    assert "timezone" not in availability["primary"]


def test_stale_availability_is_never_treated_as_available():
    config = _config()
    raw = {
        "schema_version": 1,
        "generated_at": "2026-07-26T11:00:00Z",
        "owners": {
            "primary": {
                "available": True,
                "valid_until": "2026-07-30T00:00:00Z",
            }
        },
    }

    availability, source = evaluate_availability(raw, config["owners"], now=NOW)

    assert source["reason"] == "availability_stale"
    assert availability["primary"]["status"] == "unknown"


def test_target_area_prefers_commit_pinned_definition_source():
    config = _config()
    parity = {
        "matches": [
            {
                "amd_label": "AMD: Kernels Attention Test 1",
                "nvidia_label": "Kernels Attention Test %N",
                "nvidia_source": ".buildkite/test_areas/kernels.yaml",
            }
        ]
    }
    index = parity_area_index(parity, set(config["areas"]))
    target = {
        "label": "Kernels Attention Test %N",
        "area": "other",
        "runtime_resolution": {
            "amd_definition_labels": ["AMD: Kernels Attention Test 1"]
        },
    }

    assert infer_target_area(target, index, set(config["areas"])) == (
        "kernels",
        "definition_parity",
    )


def test_area_mapping_folds_shard_templates_and_hardware_counterparts():
    known = {"models_basic", "lm_eval"}
    parity = {
        "matches": [
            {
                "amd_label": "Basic Models Tests (Extra Initialization) %N",
                "nvidia_source": ".buildkite/test_areas/models_basic.yaml",
            },
            {
                "amd_label": "GPQA Eval (GPT-OSS) (2xH100-2xMI300)",
                "nvidia_source": ".buildkite/test_areas/lm_eval.yaml",
            },
        ]
    }
    index = parity_area_index(parity, known)

    assert infer_target_area(
        {"label": "Basic Models Tests (Extra Initialization)"},
        index,
        known,
    ) == ("models_basic", "definition_parity")
    assert infer_target_area(
        {"label": "GPQA Eval (GPT-OSS) (2xB200-2xMI355)"},
        index,
        known,
    ) == ("lm_eval", "definition_parity")


def test_area_alias_and_reviewed_override_are_explicit():
    known = {"rust_frontend", "e2e_integration"}
    index = parity_area_index(
        {
            "matches": [
                {
                    "amd_label": "Rust Frontend Cargo Tests",
                    "nvidia_source": ".buildkite/test_areas/rust_frontend_cargo.yaml",
                }
            ]
        },
        known,
        {"rust_frontend_cargo": "rust_frontend"},
    )

    assert infer_target_area(
        {"label": "Rust Frontend Cargo Tests"},
        index,
        known,
    ) == ("rust_frontend", "definition_parity")
    assert infer_target_area(
        {"label": "V1 e2e (4xH100-4xMI300)"},
        {},
        known,
        {"v1 e2e (4 gpus)": "e2e_integration"},
    ) == ("e2e_integration", "reviewed_area_override")


def test_ambiguous_area_attribution_does_not_guess():
    config = _config()
    index = {"same label": {"kernels", "engine"}}

    assert infer_target_area(
        {"label": "Same Label", "area": "other"},
        index,
        {"kernels", "engine"},
    ) == ("", "ambiguous_definition_area")


def test_matrix_category_is_not_used_as_a_lossy_area_fallback():
    assert infer_target_area(
        {"label": "Unmapped AMD-only target", "area": "kernels"},
        {},
        {"kernels"},
    ) == ("", "area_unmapped")


def test_status_groups_regressions_and_parity_gaps_by_area():
    config = _config()
    availability, availability_source = evaluate_availability(
        None,
        config["owners"],
        now=NOW,
    )
    parity = {
        "matches": [
            {
                "amd_label": "Kernels Attention Test 1",
                "nvidia_label": "Kernels Attention Test %N",
                "nvidia_source": ".buildkite/test_areas/kernels.yaml",
            }
        ],
        "nvidia_only": [
            {
                "label": "New upstream kernel test",
                "source": ".buildkite/test_areas/kernels.yaml",
                "source_url": "https://example.invalid/kernels.yaml",
            }
        ],
    }
    gating = {
        "active_target_groups": [
            {
                "id": 1,
                "label": "Kernels Attention Test %N",
                "area": "other",
                "latest_amd_result": {
                    "state": "soft",
                    "build_number": 11301,
                    "observed_at": "2026-07-27T23:00:00Z",
                    "evidence": [{"url": "https://example.invalid/job"}],
                },
                "runtime_resolution": {
                    "amd_definition_labels": ["Kernels Attention Test 1"]
                },
            }
        ]
    }

    status = build_ownership_status(
        gating,
        parity,
        config,
        availability,
        availability_source,
        generated_at="2026-07-28T12:00:00Z",
    )

    area = status["areas"][0]
    assert status["summary"]["areas_with_incidents"] == 1
    assert status["summary"]["soft"] == 1
    assert status["summary"]["upstream_parity_gaps"] == 1
    assert status["summary"]["unmapped_targets"] == 0
    assert area["counts"]["incidents"] == 1
    assert area["regressions"][0]["url"] == "https://example.invalid/job"
    assert area["selected_owner"]["github_login"] == "ci-lead"


def test_matrix_projection_keeps_every_definition_and_uses_worst_hardware_result():
    matrix = {
        "generated_at": "2026-07-28T12:00:00Z",
        "summary": {"latest_build_number": 11301},
        "rows": [
            {
                "id": "row-1",
                "title": "Kernels Attention Test %N",
                "area": "Kernels",
                "cells": {
                    "mi300": {
                        "exists": True,
                        "latest_state": "passed",
                        "latest_url": "https://example.invalid/pass",
                        "primary_label": "Kernels Attention Test 1",
                    },
                    "mi355": {
                        "exists": True,
                        "latest_state": "soft",
                        "latest_url": "https://example.invalid/soft",
                        "primary_label": "Kernels Attention Test 2",
                    },
                },
            },
            {
                "id": "row-2",
                "title": "Unobserved",
                "area": "Other",
                "cells": {"mi300": {"exists": True, "latest_state": "unknown"}},
            },
        ],
    }

    rows = matrix_runtime_targets(matrix)

    assert len(rows) == 2
    assert rows[0]["latest_amd_result"]["state"] == "soft"
    assert rows[0]["latest_amd_result"]["evidence"][0]["architecture"] == "mi355"
    assert rows[0]["runtime_resolution"]["amd_definition_labels"] == [
        "Kernels Attention Test 1",
        "Kernels Attention Test 2",
    ]
    assert rows[1]["latest_amd_result"]["state"] == "unknown"


def test_matrix_projection_disambiguates_duplicate_titles_by_signature():
    matrix = {
        "generated_at": "2026-07-28T12:00:00Z",
        "summary": {"latest_build_number": 11301},
        "rows": [
            {
                "id": "mi300",
                "title": "Quantization",
                "signature": "MI300",
                "cells": {"mi300": {"exists": True, "latest_state": "soft"}},
            },
            {
                "id": "mi355",
                "title": "Quantization",
                "signature": "MI355",
                "cells": {"mi355": {"exists": True, "latest_state": "soft"}},
            },
        ],
    }

    assert [row["label"] for row in matrix_runtime_targets(matrix)] == [
        "Quantization [MI300]",
        "Quantization [MI355]",
    ]
