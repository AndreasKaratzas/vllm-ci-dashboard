"""Tests for perf-eval webhook normalization (vllm.ci.perf_eval_webhook).

These exercise the real routing/AMD-filter/nightly logic that decides what
ends up in the event log — not trivial identities. The webhook is the trust
boundary: if it lets an NVIDIA row through or mislabels a regression's
provenance, the executive view lies.
"""

from __future__ import annotations

import json

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


def test_normalize_dispatches_eval_payload_via_header_router():
    ev = w.normalize({}, _eval_payload())
    assert ev["event"] == "accuracy_result"
    # round-trips through json cleanly (store is JSONL)
    assert json.loads(json.dumps(ev))["model"] == "MiniMaxAI/MiniMax-M2.5"
