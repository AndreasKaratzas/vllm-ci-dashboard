"""Tests for strict, bounded perf-eval history reconciliation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import json

import pytest

from vllm.ci import perf_eval_webhook as store

merge = importlib.import_module("vllm.merge_perf_eval_events")


def _result(commit: str, observed_at: datetime, value: float, artifact_id: str) -> dict:
    return {
        "event": "perf_result",
        "nightly": True,
        "date": observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vllm_commit": commit,
        "model": "org/model",
        "device": "mi355x",
        "tp": 1,
        "precision": "bf16",
        "isl": 8,
        "osl": 8,
        "conc": 1,
        "metrics": {"tput_per_gpu": value},
        "buildkite_artifact_id": artifact_id,
    }


def _write_jsonl(path, events):
    path.write_text("".join(json.dumps(event) + "\n" for event in events))


def test_remote_compacted_store_merges_even_when_it_has_fewer_lines(tmp_path):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    local_path = tmp_path / "local.jsonl"
    remote_path = tmp_path / "remote.jsonl"
    local_events = [
        _result("shared", now - timedelta(days=3), 1.0, "local-source"),
        _result("local-one", now - timedelta(days=2), 2.0, "local-one-source"),
        _result("local-two", now - timedelta(days=1), 3.0, "local-two-source"),
    ]
    remote_shared = _result(
        "shared", now - timedelta(days=3) + timedelta(hours=1), 4.0, "remote-source"
    )
    remote_shared["metrics"]["mean_ttft"] = 0.4
    remote_events = [
        remote_shared,
        {
            "event": store.ARTIFACT_INDEX_EVENT,
            "schema_version": store.ARTIFACT_INDEX_SCHEMA_VERSION,
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "retention_days": store.PERF_EVAL_ARTIFACT_IDENTITY_DAYS,
            "identities": [
                [
                    "id",
                    "remote-tombstone",
                    (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                ]
            ],
        },
    ]
    assert len(remote_events) < len(local_events)
    _write_jsonl(local_path, local_events)
    _write_jsonl(remote_path, remote_events)

    count = merge.merge_event_files(local_path, remote_path, now=now)
    merged = store.read_events_strict(local_path)

    assert count == len(merged)
    assert local_path.stat().st_size <= store.PERF_EVAL_MAX_BYTES
    results = [row for row in merged if row["event"] == "perf_result"]
    assert {row["vllm_commit"] for row in results} == {
        "shared",
        "local-one",
        "local-two",
    }
    shared = next(row for row in results if row["vllm_commit"] == "shared")
    assert shared["metrics"] == {"tput_per_gpu": 4.0, "mean_ttft": 0.4}
    assert shared["buildkite_artifact_id"] == "remote-source"
    known_artifacts = {
        key for row in merged for key in store.artifact_keys_from_event(row)
    }
    assert ("id", "local-source") in known_artifacts
    assert ("id", "remote-source") in known_artifacts
    assert ("id", "remote-tombstone") in known_artifacts


def test_newer_local_generation_wins_over_older_remote_observation(tmp_path):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    local_path = tmp_path / "local.jsonl"
    remote_path = tmp_path / "remote.jsonl"
    run_at = now - timedelta(days=1)
    local = _result("shared", run_at, 9.0, "local-newer")
    local["received_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    remote = _result("shared", run_at, 1.0, "remote-older")
    remote["received_at"] = (now - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    _write_jsonl(local_path, [local])
    _write_jsonl(remote_path, [remote])

    merge.merge_event_files(local_path, remote_path, now=now)
    merged = store.read_events_strict(local_path)
    result = next(row for row in merged if row["event"] == "perf_result")

    assert result["metrics"]["tput_per_gpu"] == 9.0
    assert result["buildkite_artifact_id"] == "local-newer"
    assert {key for row in merged for key in store.artifact_keys_from_event(row)} >= {
        ("id", "local-newer"),
        ("id", "remote-older"),
    }


def test_equal_generation_disjoint_metrics_are_unioned(tmp_path):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    local_path = tmp_path / "local.jsonl"
    remote_path = tmp_path / "remote.jsonl"
    local = _result("shared", now, 9.0, "local-source")
    remote = _result("shared", now, 0.0, "remote-source")
    remote["metrics"] = {"mean_ttft": 0.4}
    _write_jsonl(local_path, [local])
    _write_jsonl(remote_path, [remote])

    merge.merge_event_files(local_path, remote_path, now=now)
    result = next(
        row
        for row in store.read_events_strict(local_path)
        if row["event"] == "perf_result"
    )

    assert result["metrics"] == {"tput_per_gpu": 9.0, "mean_ttft": 0.4}


def test_equal_generation_disjoint_accuracy_tasks_are_unioned(tmp_path):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    local_path = tmp_path / "local.jsonl"
    remote_path = tmp_path / "remote.jsonl"

    def accuracy(task, metric, value, artifact_id):
        return {
            "event": "accuracy_result",
            "nightly": True,
            "date": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "vllm_commit": "shared",
            "model": "org/model",
            "workload": "model-mi355x",
            "device": "mi355x",
            "task": task,
            "results": [{"task": task, "metric": metric, "value": value}],
            "buildkite_artifact_id": artifact_id,
        }

    _write_jsonl(local_path, [accuracy("gsm8k", "exact_match", 0.8, "local")])
    _write_jsonl(remote_path, [accuracy("mmlu", "acc", 0.7, "remote")])

    merge.merge_event_files(local_path, remote_path, now=now)
    result = next(
        row
        for row in store.read_events_strict(local_path)
        if row["event"] == "accuracy_result"
    )

    assert {(row["task"], row["metric"]) for row in result["results"]} == {
        ("gsm8k", "exact_match"),
        ("mmlu", "acc"),
    }


@pytest.mark.parametrize("conflict", ["metric", "immutable"])
def test_equal_generation_conflicts_fail_before_local_replacement(
    tmp_path, conflict
):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    local_path = tmp_path / "local.jsonl"
    remote_path = tmp_path / "remote.jsonl"
    local = _result("shared", now, 1.0, "local-source")
    remote = _result("shared", now, 1.0, "remote-source")
    if conflict == "metric":
        remote["metrics"]["tput_per_gpu"] = 2.0
    else:
        local["build_number"] = 100
        remote["build_number"] = 200
    _write_jsonl(local_path, [local])
    original = local_path.read_bytes()
    _write_jsonl(remote_path, [remote])

    with pytest.raises(ValueError, match="equal-timestamp perf-eval conflict"):
        merge.merge_event_files(local_path, remote_path, now=now)

    assert local_path.read_bytes() == original


@pytest.mark.parametrize(
    "remote_payload",
    [
        "{not-json}\n",
        "[1, 2, 3]\n",
        '{"event":"buildkite_artifact_identity_index",'
        '"schema_version":999,"identities":[]}\n',
    ],
)
def test_invalid_remote_store_fails_before_local_replacement(
    tmp_path, remote_payload
):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    local_path = tmp_path / "local.jsonl"
    remote_path = tmp_path / "remote.jsonl"
    _write_jsonl(local_path, [_result("local", now, 1.0, "local-source")])
    original = local_path.read_bytes()
    remote_path.write_text(remote_payload)

    with pytest.raises(ValueError, match="invalid perf-eval JSONL"):
        merge.merge_event_files(local_path, remote_path, now=now)

    assert local_path.read_bytes() == original
    assert list(tmp_path.glob(".local.jsonl.*.tmp")) == []


def test_empty_established_remote_store_fails_before_local_replacement(tmp_path):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    local_path = tmp_path / "local.jsonl"
    remote_path = tmp_path / "remote.jsonl"
    _write_jsonl(local_path, [_result("local", now, 1.0, "local-source")])
    original = local_path.read_bytes()
    remote_path.write_text("")

    with pytest.raises(ValueError, match="invalid perf-eval remote store.*no events"):
        merge.merge_event_files(local_path, remote_path, now=now)

    assert local_path.read_bytes() == original
