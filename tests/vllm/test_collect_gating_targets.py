from __future__ import annotations

from collections import Counter

from vllm import collect_gating_targets as cgt


def test_config_has_exact_canonical_target_count() -> None:
    groups = cgt.load_targets()

    assert len(groups) == 125
    assert [row["id"] for row in groups] == list(range(1, 126))
    duplicates = [label for label, count in Counter(row["label"] for row in groups).items() if count > 1]
    assert duplicates == []


def test_generated_payload_summarizes_targets() -> None:
    groups = cgt.load_targets()
    payload = cgt.build_payload(groups)

    assert payload["summary"]["target_group_count"] == 125
    assert payload["groups"][0]["label"] == "Distributed Torchrun + Shutdown Tests (2 GPUs)"
    assert payload["groups"][0]["gating_signal"] == payload["groups"][0]["source_signal"]
    assert payload["groups"][0]["pf_signal"] == payload["groups"][0]["readiness_signal"]
    assert payload["groups"][0]["assigned_signal"] == payload["groups"][0]["target_signal"]
    assert payload["groups"][-1]["label"] == "Spec Decode Draft Model"
    assert payload["summary"]["by_area"]
    assert payload["summary"]["by_gating_signal"]
    assert payload["summary"]["by_pf_signal"]
    assert payload["summary"]["by_assigned_signal"]
