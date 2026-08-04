from __future__ import annotations

from collections import Counter

from vllm import collect_gating_targets as cgt


def test_config_has_valid_canonical_targets() -> None:
    groups = cgt.load_targets()

    assert groups
    assert [row["id"] for row in groups] == list(range(1, len(groups) + 1))
    duplicates = [label for label, count in Counter(row["label"] for row in groups).items() if count > 1]
    assert duplicates == []


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
