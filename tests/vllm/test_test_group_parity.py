"""Contracts for the reviewed upstream-to-ROCm test-group inventory."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from vllm import build_test_group_parity as parity
from vllm import build_operations_snapshot as operations


GENERATED_AT = "2026-08-22T04:00:00Z"


def test_reviewed_inventory_publishes_expected_counts_and_rates() -> None:
    review = parity.load_review()
    payload = parity.build_payload(review, generated_at=GENERATED_AT)

    assert payload["generated_at"] == GENERATED_AT
    assert payload["source"]["config_path"] == (
        "config/vllm_upstream_test_group_parity.json"
    )
    assert payload["summary"] == {
        "upstream_physical_definitions": 201,
        "upstream_logical_groups": 191,
        "applicable_groups": 163,
        "existing_groups": 139,
        "proposed_groups": 12,
        "published_pr_additions": 10,
        "local_candidate_additions": 2,
        "published_pr_complete_groups": 149,
        "local_candidate_complete_groups": 151,
        "unsupported_groups": 28,
        "action_groups": 12,
        "strict_rate_pct": 72.8,
        "applicable_rate_pct": 85.3,
        "published_pr_strict_rate_pct": 78.0,
        "published_pr_applicable_rate_pct": 91.4,
        "local_candidate_strict_rate_pct": 79.1,
        "local_candidate_applicable_rate_pct": 92.6,
    }
    assert payload["rocm_inventory"] == {
        "before_pr": 143,
        "published_pr": 152,
        "local_candidate": 155,
        "count_basis": (
            "ROCm logical groups; this is an inventory, not an "
            "upstream-parity numerator"
        ),
    }
    assert len(payload["areas"]) == 31
    assert sum(row["total"] for row in payload["areas"]) == 191
    assert len(payload["groups"]) == 191
    assert [row["id"] for row in payload["groups"]] == list(range(1, 192))
    assert Counter(row["state"] for row in payload["groups"]) == {
        "existing": 139,
        "proposed": 12,
        "unsupported": 28,
        "action": 12,
    }


def test_review_validator_rejects_duplicate_group_ids(tmp_path: Path) -> None:
    review = json.loads(parity.CONFIG.read_text())
    review["groups"][0]["id"] = review["groups"][1]["id"]
    config_path = tmp_path / "parity.json"
    config_path.write_text(json.dumps(review))

    with pytest.raises(ValueError, match="duplicate group id"):
        parity.load_review(config_path)


def test_review_validator_rejects_area_state_drift(tmp_path: Path) -> None:
    review = json.loads(parity.CONFIG.read_text())
    existing = next(row for row in review["groups"] if row["state"] == "existing")
    existing["state"] = "action"
    config_path = tmp_path / "parity.json"
    config_path.write_text(json.dumps(review))

    with pytest.raises(ValueError, match="detailed existing groups"):
        parity.load_review(config_path)


def test_publish_writes_validated_payload(tmp_path: Path) -> None:
    output_path, payload = parity.publish(
        output_dir=tmp_path,
        generated_at=GENERATED_AT,
    )

    assert output_path == tmp_path / "test_group_parity.json"
    assert json.loads(output_path.read_text()) == payload
    assert output_path.read_text().endswith("\n")


def test_operations_views_publish_compact_and_full_parity_projections() -> None:
    parity_payload = parity.build_payload(
        parity.load_review(), generated_at=GENERATED_AT
    )
    operations_payload = {
        "schema_version": 2,
        "generated_at": GENERATED_AT,
        "sources": {
            "test_group_parity": {
                "path": "test_group_parity.json",
                "timestamp": GENERATED_AT,
            }
        },
        "test_group_parity": parity_payload,
    }

    shell = operations._operations_shell(operations_payload)
    assert shell["test_group_parity"]["summary"] == parity_payload["summary"]
    assert "groups" not in shell["test_group_parity"]
    section = operations._operation_sections(operations_payload)[
        "test_group_parity"
    ]["test_group_parity"]
    assert len(section["groups"]) == 191

    org_summary = operations.build_org_summary(operations_payload)
    assert org_summary["test_group_parity"] == {
        "available": True,
        "reviewed_at": "2026-08-22",
        "summary": parity_payload["summary"],
        "rocm_inventory": parity_payload["rocm_inventory"],
        "source": parity_payload["source"],
        "scope": parity_payload["scope"],
    }
    assert org_summary["sources"]["test_group_parity"] == {
        "path": "test_group_parity.json",
        "generated_at": GENERATED_AT,
    }
