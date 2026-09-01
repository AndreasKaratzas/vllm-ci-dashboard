"""Tests for perf-eval webhook normalization (vllm.ci.perf_eval_webhook).

These exercise the real routing/AMD-filter/nightly logic that decides what
ends up in the event log — not trivial identities. The webhook is the trust
boundary: if it lets an NVIDIA row through or mislabels a regression's
provenance, the executive view lies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from vllm.ci import perf_eval_webhook as w


# ── AMD vs NVIDIA classification ──────────────────────────────────────────

def test_amd_devices_match_mi_only():
    assert w.is_amd_device("mi355x")
    assert w.is_amd_device("MI300X")
    assert not w.is_amd_device("h200")
    assert not w.is_amd_device("b200")
    assert not w.is_amd_device("")


def test_amd_workload_detected_by_suffix_image_or_device():
    assert w.is_amd_workload(workload="minimax_m2_5-mi355x")
    assert w.is_amd_workload(image="vllm/vllm-openai-rocm:nightly-abc123")
    assert w.is_amd_workload(device="mi300x")
    assert not w.is_amd_workload(workload="qwen3_5-h200", image="vllm/vllm-openai:nightly")


def test_commit_extracted_from_rocm_nightly_tag():
    assert w.commit_from_image("vllm/vllm-openai-rocm:nightly-a1b2c3d4e5f6") == "a1b2c3d4e5f6"
    assert w.commit_from_image("vllm/vllm-openai:latest") == ""


# ── Perf payload normalization ────────────────────────────────────────────

def _perf_payload(**over):
    base = {
        "date": "2026-06-25 02:00:00",
        "device": "mi355x",
        "model": "MiniMaxAI/MiniMax-M2.5",
        "image": "vllm/vllm-openai-rocm:nightly-a1b2c3d4e5f6",
        "isl": 8192, "osl": 1024, "conc": 128, "tp": 8, "precision": "bf16",
        "tput_per_gpu": 1180.0,
        "mean_ttft": 0.42,
        "mean_tpot": 0.0185,
        "nightly": True,
    }
    base.update(over)
    return base


def test_normalize_perf_keeps_amd_and_derives_commit():
    ev = w.normalize_perf_payload(_perf_payload())
    assert ev is not None
    assert ev["event"] == "perf_result"
    assert ev["model"] == "MiniMaxAI/MiniMax-M2.5"
    # commit comes from the image tag even though no explicit vllm_commit field
    assert ev["vllm_commit"] == "a1b2c3d4e5f6"
    assert ev["metrics"]["tput_per_gpu"] == 1180.0
    assert ev["nightly"] is True


def test_normalize_perf_drops_nvidia():
    assert w.normalize_perf_payload(_perf_payload(device="h200", image="vllm/vllm-openai:nightly")) is None


def test_normalize_perf_drops_payload_without_metrics():
    payload = _perf_payload()
    for k in ("tput_per_gpu", "mean_ttft", "mean_tpot"):
        payload.pop(k, None)
    assert w.normalize_perf_payload(payload) is None


def test_perf_metrics_ignores_non_metric_and_bad_values():
    metrics = w.perf_metrics({
        "tput_per_gpu": 100.0,
        "conc": 128,            # not a metric
        "mean_ttft": "0.4",     # string-coercible
        "p99_ttft": "n/a",      # bad value dropped
    })
    assert metrics == {"tput_per_gpu": 100.0, "mean_ttft": 0.4}


# ── Accuracy payload normalization ────────────────────────────────────────

def _eval_payload(**over):
    base = {
        "kind": "results",
        "workload": "minimax_m2_5-mi355x",
        "task": "gsm8k",
        "image": "vllm/vllm-openai-rocm:nightly-a1b2c3d4e5f6",
        "vllm_commit": "a1b2c3d4e5f6",
        "buildkite_build_url": "https://buildkite.com/vllm/perf-eval/builds/496",
        "buildkite_build_number": 496,
        "nightly": True,
        "data": {
            "config": {"model_name": "MiniMaxAI/MiniMax-M2.5"},
            "results": {
                "gsm8k": {
                    "alias": "gsm8k",
                    "exact_match,strict-match": 0.849,
                    "exact_match_stderr,strict-match": 0.01,
                },
            },
        },
    }
    base.update(over)
    return base


def test_normalize_eval_flattens_results_and_skips_stderr():
    ev = w.normalize_eval_payload(_eval_payload())
    assert ev is not None
    assert ev["model"] == "MiniMaxAI/MiniMax-M2.5"
    assert ev["build_number"] == 496
    assert len(ev["results"]) == 1
    row = ev["results"][0]
    assert row["task"] == "gsm8k"
    assert row["metric"] == "exact_match,strict-match"
    assert row["value"] == 0.849
    assert row["primary"] is True


def test_normalize_eval_drops_nvidia_workload():
    payload = _eval_payload(workload="qwen3_5-h200", image="vllm/vllm-openai:nightly")
    assert w.normalize_eval_payload(payload) is None


def test_model_from_eval_falls_back_to_pretrained_arg():
    payload = _eval_payload()
    payload["data"]["config"] = {"model_args": "pretrained=org/Model-X,dtype=bfloat16"}
    assert w.model_from_eval(payload) == "org/Model-X"


# ── Payload classification + dispatch ─────────────────────────────────────

def test_classify_distinguishes_payload_kinds():
    assert w.classify_payload({"X-Buildkite-Event": "build.finished"}, {}) == "buildkite"
    assert w.classify_payload({}, {"kind": "results"}) == "eval"
    assert w.classify_payload({}, _perf_payload()) == "perf"
    assert w.classify_payload({}, {"hello": "world"}) == "unknown"


def test_normalize_build_event_only_for_perf_eval_pipeline():
    body = {
        "pipeline": {"slug": "perf-eval"},
        "build": {
            "number": 496, "state": "passed", "web_url": "https://buildkite.com/vllm/perf-eval/builds/496",
            "commit": "deadbeef", "branch": "main", "message": "AMD nightly",
            "env": {"VLLM_IMAGE": "vllm/vllm-openai-rocm:nightly-a1b2c3d4e5f6", "NIGHTLY": "1"},
        },
    }
    ev = w.normalize_build_event("build.finished", body)
    assert ev is not None
    assert ev["build_number"] == 496
    assert ev["vllm_commit"] == "a1b2c3d4e5f6"
    assert ev["nightly"] is True

    other = {**body, "pipeline": {"slug": "amd-ci"}}
    assert w.normalize_build_event("build.finished", other) is None


def test_is_nightly_signals():
    assert w.is_nightly({"nightly": True})
    assert w.is_nightly({"env": {"NIGHTLY": "1"}})
    assert w.is_nightly({"message": "AMD Full nightly run"})
    assert not w.is_nightly({"message": "ad-hoc perf check"})


# ── Durable event store round-trip ────────────────────────────────────────

def test_append_and_read_events_round_trip_and_skip_malformed(tmp_path):
    store = tmp_path / "events.jsonl"
    w.append_event(store, {"event": "perf_result", "model": "m"})
    w.append_event(store, {"event": "accuracy_result", "model": "m"})
    # Inject a malformed line the reader must tolerate.
    with store.open("a", encoding="utf-8") as fh:
        fh.write("{not json}\n")
    events = w.read_events(store)
    assert [e["event"] for e in events] == ["perf_result", "accuracy_result"]


def _store_perf_event(
    commit: str,
    observed_at: datetime,
    *,
    model: str = "org/model",
    padding: str = "",
    artifact_id: str = "",
) -> dict:
    return {
        "event": "perf_result",
        "nightly": True,
        "date": observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vllm_commit": commit,
        "model": model,
        "device": "mi355x",
        "tp": 1,
        "precision": "bf16",
        "isl": 8,
        "osl": 8,
        "conc": 1,
        "metrics": {"tput_per_gpu": 1.0},
        "padding": padding,
        "buildkite_artifact_id": artifact_id,
    }


def test_store_limits_leave_headroom_and_cover_artifact_lookback():
    assert w.PERF_EVAL_MAX_BYTES == 4 * 1024 * 1024
    assert w.PERF_EVAL_MAX_BYTES < 64 * 1024 * 1024
    assert w.PERF_EVAL_MAX_BYTES < 90_000_000
    assert w.enforced_byte_budget(100 * 1024 * 1024) == w.PERF_EVAL_MAX_BYTES
    assert (
        w.PERF_EVAL_ARTIFACT_IDENTITY_DAYS
        > w.PERF_EVAL_MAX_ARTIFACT_LOOKBACK_DAYS
    )


def test_compaction_merges_duplicate_results_without_losing_metrics_or_tasks():
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    perf_first = _store_perf_event(
        "same-commit", now - timedelta(hours=2), artifact_id="artifact-first"
    )
    perf_first["metrics"] = {"tput_per_gpu": 10.0}
    perf_second = _store_perf_event(
        "same-commit", now - timedelta(hours=1), artifact_id="artifact-second"
    )
    perf_second["metrics"] = {"mean_ttft": 0.4}
    accuracy_first = {
        **_store_perf_event("same-commit", now - timedelta(minutes=45)),
        "event": "accuracy_result",
        "workload": "model-mi355x",
        "results": [
            {"task": "gsm8k", "metric": "exact_match", "value": 0.8}
        ],
    }
    accuracy_second = {
        **accuracy_first,
        "date": (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "results": [{"task": "mmlu", "metric": "acc", "value": 0.7}],
    }

    compacted = w.compact_events(
        [perf_first, perf_second, accuracy_first, accuracy_second], now=now
    )
    perf = next(row for row in compacted if row.get("event") == "perf_result")
    accuracy = next(
        row for row in compacted if row.get("event") == "accuracy_result"
    )
    index = next(row for row in compacted if row.get("event") == w.ARTIFACT_INDEX_EVENT)

    assert perf["metrics"] == {"tput_per_gpu": 10.0, "mean_ttft": 0.4}
    assert {(row["task"], row["metric"]) for row in accuracy["results"]} == {
        ("gsm8k", "exact_match"),
        ("mmlu", "acc"),
    }
    assert w.artifact_keys_from_event(index) == (("id", "artifact-first"),)


def test_compaction_prunes_whole_oldest_nightlies_to_exact_byte_budget(tmp_path):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    events = []
    for nightly in range(4):
        observed_at = datetime(2025, 1, nightly + 1, tzinfo=timezone.utc)
        for model in ("org/a", "org/b"):
            events.append(
                _store_perf_event(
                    f"commit-{nightly}",
                    observed_at,
                    model=model,
                    padding=str(nightly) * 2_000,
                )
            )

    latest_two = w._compact_events_once(
        events,
        now,
        history_days=14,
        min_nightlies=2,
        auxiliary_days=14,
    )
    budget = len(w._encoded_events(latest_two))
    store = tmp_path / "events.jsonl"
    w.write_events_atomic(store, events, now=now, max_bytes=budget)
    compacted = w.read_events(store)

    result_rows = [row for row in compacted if row["event"] == "perf_result"]
    assert {row["vllm_commit"] for row in result_rows} == {"commit-2", "commit-3"}
    assert {
        commit: sum(row["vllm_commit"] == commit for row in result_rows)
        for commit in {"commit-2", "commit-3"}
    } == {"commit-2": 2, "commit-3": 2}
    assert store.stat().st_size == len(w._encoded_events(compacted))
    assert store.stat().st_size <= budget


def test_artifact_index_keeps_only_exact_identities_inside_dedup_horizon():
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    events = [
        {
            "event": w.ARTIFACT_MARKER_EVENT,
            "received_at": (now - timedelta(days=44)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "buildkite_artifact_id": "still-queryable",
        },
        {
            "event": w.ARTIFACT_MARKER_EVENT,
            "received_at": (now - timedelta(days=46)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "buildkite_artifact_id": "expired",
        },
    ]

    compacted = w.compact_events(events, now=now)
    assert len(compacted) == 1
    assert compacted[0]["event"] == w.ARTIFACT_INDEX_EVENT
    assert w.artifact_keys_from_event(compacted[0]) == (("id", "still-queryable"),)


def test_oversized_store_candidate_preserves_previous_file(tmp_path):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    store = tmp_path / "events.jsonl"
    original = b'{"known":"good"}\n'
    store.write_bytes(original)
    events = [
        _store_perf_event(
            f"commit-{nightly}",
            now - timedelta(days=nightly),
            padding="x" * 4_000,
        )
        for nightly in range(2)
    ]

    with pytest.raises(RuntimeError, match="cannot fit the byte budget"):
        w.write_events_atomic(store, events, now=now, max_bytes=64)

    assert store.read_bytes() == original
    assert list(tmp_path.glob(".events.jsonl.*.tmp")) == []


def test_atomic_replace_failure_preserves_previous_file_and_cleans_temp(
    tmp_path, monkeypatch
):
    output = tmp_path / "perf_eval.json"
    original = b'{"known":"good"}\n'
    output.write_bytes(original)

    def fail_replace(_source, _destination):
        raise OSError("injected replace failure")

    monkeypatch.setattr(w.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        w.write_json_atomic(output, {"new": "candidate"}, max_bytes=1_024)

    assert output.read_bytes() == original
    assert list(tmp_path.glob(".perf_eval.json.*.tmp")) == []


def test_append_rejects_corrupt_existing_store_without_replacement(tmp_path):
    store = tmp_path / "events.jsonl"
    original = b'{"event":"perf_result"}\n{not-json}\n'
    store.write_bytes(original)

    with pytest.raises(ValueError, match="invalid perf-eval JSONL"):
        w.append_event(store, {"event": "perf_result", "model": "new"})

    assert store.read_bytes() == original
    assert list(tmp_path.glob(".events.jsonl.*.tmp")) == []


def test_normalize_dispatches_eval_payload_via_header_router():
    ev = w.normalize({}, _eval_payload())
    assert ev["event"] == "accuracy_result"
    # round-trips through json cleanly (store is JSONL)
    assert json.loads(json.dumps(ev))["model"] == "MiniMaxAI/MiniMax-M2.5"
