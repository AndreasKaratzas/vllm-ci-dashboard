"""Reviewed policy must survive renames without inheriting unrelated scope."""

from copy import deepcopy

import pytest

from vllm.reviewed_definition_labels import (
    execution_sha256,
    resolve_reviewed_definition_labels,
    validate_definition_ids,
)


KEY = ".buildkite/test_areas/entrypoints.yaml#completion"
SHA = "a" * 40


def test_keyed_rename_uses_current_multiword_hardware_label_and_keeps_review():
    rows = [{"id": 1, "label": "Old name", "source_signal": "red", "upstream_definition_ids": [KEY]}]
    report = {"source": {"commit_sha": SHA}, "mirrors": [{
        "nvidia_definition_id": KEY,
        "nvidia_label": ":nvidia: (H200 MIG 35GB) New name",
    }]}

    result = resolve_reviewed_definition_labels(rows, report, label_field="label")[0]

    assert result["label"] == "New name"
    assert result["reviewed_label"] == "Old name"
    assert result["source_signal"] == "red"
    assert result["definition_resolution"]["status"] == "resolved"
    assert rows[0]["label"] == "Old name"


@pytest.mark.parametrize("collection", ["matches", "mirrors", "inline_mirror_variants", "additional_variants", "nvidia_only"])
def test_all_upstream_report_collections_supply_exact_key_evidence(collection):
    definition = {"nvidia_definition_id": KEY, "nvidia_label": ":nvidia: (H100) Current"}
    if collection == "nvidia_only":
        definition = {"definition_id": KEY, "label": ":nvidia: (H100) Current"}
    report = {"source": {"commit_sha": SHA}, collection: [definition]}
    row = {"title": "Reviewed", "upstream_definition_ids": [KEY]}
    assert resolve_reviewed_definition_labels([row], report, label_field="title")[0]["title"] == "Current"


def test_removed_key_keeps_review_and_publishes_successors_without_approval():
    successor = ".buildkite/test_areas/entrypoints.yaml#replacement"
    row = {"title": "Reviewed scope", "state": "existing", "upstream_definition_ids": [KEY], "successor_definition_ids": [successor]}
    report = {"source": {"commit_sha": SHA}, "nvidia_only": [{"definition_id": successor, "label": ":nvidia: (H100) Replacement"}]}
    result = resolve_reviewed_definition_labels([row], report, label_field="title")[0]
    assert result["title"] == "Reviewed scope"
    assert result["state"] == "existing"
    assert result["definition_resolution"]["status"] == "unresolved"
    assert result["definition_resolution"]["successor_labels"] == [":nvidia: (H100) Replacement"]


def test_diverging_keyed_replicas_remain_ambiguous():
    other = ".buildkite/test_areas/entrypoints.yaml#other"
    row = {"label": "Reviewed", "upstream_definition_ids": [KEY, other]}
    report = {"source": {"commit_sha": SHA}, "nvidia_only": [
        {"definition_id": KEY, "label": ":nvidia: (H100) Original"},
        {"definition_id": other, "label": ":nvidia: (B200) Different workload"},
    ]}
    result = resolve_reviewed_definition_labels([row], report, label_field="label")[0]
    assert result["label"] == "Reviewed"
    assert result["definition_resolution"]["status"] == "ambiguous"


def test_positional_keys_are_never_reviewed_identity():
    row = {"upstream_definition_ids": [".buildkite/test_areas/entrypoints.yaml#17"]}
    with pytest.raises(ValueError, match="explicit upstream YAML keys"):
        validate_definition_ids(row, context="review")


EXECUTION = {
    "commands": ["pytest test.py"], "source_file": ".buildkite/test-amd.yaml",
    "working_dir": "tests/first", "agent_pool": "mi300_1", "num_gpus": 1,
    "parallelism": 2, "definition_fingerprint": "dependency-evidence",
}


@pytest.mark.parametrize("field,value", [
    ("commands", ["pytest other.py"]), ("working_dir", "tests/second"),
    ("agent_pool", "mi300_2"), ("num_gpus", 2), ("parallelism", 3),
    ("source_file", ".buildkite/another.yaml"), ("definition_fingerprint", "changed-dependencies"),
])
def test_execution_identity_detects_route_changes(field, value):
    assert execution_sha256(EXECUTION) != execution_sha256({**EXECUTION, field: value})
    assert execution_sha256(EXECUTION) == execution_sha256({**EXECUTION, "label": "Renamed", "definition_id": "reordered#9"})


def test_exact_execution_rename_requires_complete_unique_physical_catalog():
    signature = execution_sha256(EXECUTION)
    row = {"label": "Reviewed", "amd_execution_sha256s": [signature]}
    report = {"source": {"commit_sha": SHA}, "amd_execution_definitions": [{
        "definition_id": ".buildkite/test-amd.yaml#100", "label": ":amd: (MI300) New name", "execution_sha256": signature,
    }]}
    result = resolve_reviewed_definition_labels([row], report, label_field="label")[0]
    assert result["label"] == "New name"
    assert result["definition_resolution"]["source_kind"] == "amd_execution"

    ambiguous = deepcopy(report)
    ambiguous["amd_execution_definitions"].append({**report["amd_execution_definitions"][0], "definition_id": ".buildkite/test-amd.yaml#101"})
    assert resolve_reviewed_definition_labels([row], ambiguous, label_field="label")[0]["definition_resolution"]["status"] == "ambiguous"

    partial = {**report, "publication_retention": {"collections": {"amd_execution_definitions": {"complete_relative_to_source": False}}}}
    assert resolve_reviewed_definition_labels([row], partial, label_field="label")[0]["definition_resolution"]["status"] == "unresolved"
    assert resolve_reviewed_definition_labels([row], {"source": {"commit_sha": SHA}}, label_field="label")[0]["definition_resolution"]["status"] == "unavailable"


def test_compacted_missing_keys_are_unavailable_not_reported_removed():
    row = {"title": "Reviewed", "upstream_definition_ids": [KEY]}
    report = {"source": {"commit_sha": SHA}, "publication_retention": {"complete_relative_to_source": False}}
    result = resolve_reviewed_definition_labels([row], report, label_field="title")[0]
    assert result["definition_resolution"]["status"] == "unavailable"


def test_execution_identity_cannot_override_missing_reviewed_upstream_key():
    signature = execution_sha256(EXECUTION)
    row = {"label": "Reviewed", "upstream_definition_ids": [KEY], "amd_execution_sha256s": [signature]}
    with pytest.raises(ValueError, match="cannot mix"):
        validate_definition_ids(row, context="review")
    report = {"source": {"commit_sha": SHA}, "amd_execution_definitions": [{
        "label": ":amd: (MI300) Different", "execution_sha256": signature,
    }]}
    assert resolve_reviewed_definition_labels([row], report, label_field="label")[0]["definition_resolution"]["status"] == "ambiguous"
