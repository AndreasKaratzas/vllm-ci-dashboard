"""Tests for the Buildkite-artifact perf-eval collector.

These cover the pure logic that turns raw Buildkite builds + ``vllm bench serve``
/ lm-eval artifacts into the canonical events the aggregator consumes: nightly
detection, artifact-path classification, the per-GPU transform, workload-recipe
parsing, AMD-only filtering, and dedup identity. No network is touched.
"""

from __future__ import annotations

import importlib

art = importlib.import_module("vllm.collect_perf_eval_artifacts")


# ── workload recipe parsing ────────────────────────────────────────────────

def test_workload_entry_derives_tp_device_precision():
    data = {
        "name": "minimax_m2_5-mi355x",
        "gpu": "MI355X",
        "vllm": {"model": "MiniMaxAI/MiniMax-M2.5", "serve_args": "--tensor-parallel-size 8 --trust-remote-code"},
        "vllm_bench": {"configs": [
            {"name": "8k-in-1k-out-conc-128", "input_len": 8192, "output_len": 1024, "max_concurrency": 128},
        ]},
    }
    entry, configs = art.workload_entry(data)
    assert entry["device"] == "mi355x"
    assert entry["tp"] == 8
    assert entry["precision"] == "bf16"  # no precision marker in model id
    assert configs["8k-in-1k-out-conc-128"] == {"isl": 8192, "osl": 1024, "conc": 128}


def test_workload_entry_prefers_explicit_metadata():
    data = {
        "name": "m", "gpu": "MI300X",
        "vllm": {"model": "x/fp8-model", "serve_args": "--tensor-parallel-size 2 --data-parallel-size 2"},
        "vllm_bench": {"metadata": {"tp": 3, "device": "mi300x", "precision": "fp4"}, "configs": []},
    }
    entry, _ = art.workload_entry(data)
    assert entry["tp"] == 3 and entry["device"] == "mi300x" and entry["precision"] == "fp4"


def test_parse_tp_multiplies_tp_and_dp():
    assert art.parse_tp("--tensor-parallel-size 4 --data-parallel-size 2") == 8
    assert art.parse_tp("-tp=8") == 8
    assert art.parse_tp("") == 1


def test_precision_inferred_from_model_name():
    assert art.precision_from_model("org/Model-FP8") == "fp8"
    assert art.precision_from_model("org/plain") == "bf16"


# ── nightly detection ──────────────────────────────────────────────────────

def test_nightly_from_structured_message_gives_date_and_commit():
    build = {
        "branch": "main",
        "message": "Nightly run 2026-06-30: commit 93d8f834dd8acf33eb0e2a75b2711b628cb6e226",
    }
    info = art.nightly_info(build)
    assert info["date"] == "2026-06-30"
    assert info["vllm_commit"] == "93d8f834dd8acf33eb0e2a75b2711b628cb6e226"


def test_nightly_from_env_flag_on_main():
    build = {"branch": "main", "message": "manual run", "env": {"NIGHTLY": "1", "VLLM_COMMIT": "abcdef123456"}}
    info = art.nightly_info(build)
    assert info is not None and info["vllm_commit"] == "abcdef123456"


def test_nightly_from_scheduled_source():
    build = {"branch": "main", "source": "schedule", "message": "nightly sweep"}
    assert art.nightly_info(build) is not None


def test_non_nightly_returns_none():
    assert art.nightly_info({"branch": "main", "message": "fix a bug"}) is None


def test_env_flag_off_main_is_not_nightly():
    # NIGHTLY on a feature branch must not be treated as a tracked nightly.
    build = {"branch": "dev/foo", "message": "x", "env": {"NIGHTLY": "1"}}
    assert art.nightly_info(build) is None


def test_commit_falls_back_to_image_tag():
    build = {"branch": "main", "message": "nightly", "source": "schedule",
             "env": {"VLLM_IMAGE": "vllm/vllm-openai-rocm:nightly-deadbeef1234"}}
    info = art.nightly_info(build)
    assert info["vllm_commit"] == "deadbeef1234"


# ── artifact classification ────────────────────────────────────────────────

def test_classify_perf_artifact():
    assert art.classify_artifact("results/minimax_m2_5-mi355x/bench-8k-in-1k-out-conc-128.json") == (
        "perf", "minimax_m2_5-mi355x", "8k-in-1k-out-conc-128",
    )


def test_classify_accuracy_artifact():
    assert art.classify_artifact("results/minimax_m2_5-mi355x/gsm8k/results_2026-06-30.json") == (
        "accuracy", "minimax_m2_5-mi355x", "gsm8k",
    )


def test_samples_and_unknown_artifacts_ignored():
    assert art.classify_artifact("results/m-mi355x/gsm8k/samples_2026.jsonl") is None
    assert art.classify_artifact("results/m-mi355x/notes.txt") is None
    assert art.classify_artifact("random/path.json") is None


# ── per-GPU transform ──────────────────────────────────────────────────────

def test_transform_perf_divides_by_tp_and_converts_latencies():
    raw = {
        "total_token_throughput": 8000.0,
        "output_throughput": 800.0,
        "mean_ttft_ms": 420.0,
        "p99_ttft_ms": 910.0,
        "mean_tpot_ms": 18.5,
        "not_a_metric_ms": 5.0,  # base not in registry -> dropped
    }
    m = art.transform_perf(raw, tp=8)
    assert m["tput_per_gpu"] == 1000.0
    assert m["output_tput_per_gpu"] == 100.0
    assert m["input_tput_per_gpu"] == 900.0
    assert m["mean_ttft"] == 0.42
    assert m["p99_ttft"] == 0.91
    assert m["mean_tpot"] == 0.0185
    # interactivity derived from tpot (1000 / tpot_ms)
    assert round(m["mean_intvty"], 2) == 54.05
    assert "not_a_metric" not in m


def test_transform_perf_tp_zero_is_safe():
    m = art.transform_perf({"total_token_throughput": 100.0}, tp=0)
    assert m["tput_per_gpu"] == 100.0  # treated as tp=1


# ── event construction + AMD filtering ─────────────────────────────────────

_IDENTITY = {
    "build_number": 501,
    "build_url": "https://buildkite.com/vllm/perf-eval/builds/501",
    "build_commit": "1111111111ab",
    "branch": "main",
    "vllm_commit": "93d8f834dd8a",
    "date": "2026-06-30T02:11:00Z",
    "image": "vllm/vllm-openai-rocm:nightly-93d8f834dd8a",
}


def test_perf_event_kept_for_amd():
    entry = {"name": "minimax_m2_5-mi355x", "device": "mi355x", "tp": 8, "precision": "bf16", "model": "MiniMaxAI/MiniMax-M2.5"}
    ev = art.perf_event(
        {"model_id": "MiniMaxAI/MiniMax-M2.5", "total_token_throughput": 8000.0, "output_throughput": 800.0, "max_concurrency": 128},
        entry=entry, config={"isl": 8192, "osl": 1024, "conc": 128}, identity=_IDENTITY,
    )
    assert ev["event"] == "perf_result"
    assert ev["nightly"] is True
    assert ev["device"] == "mi355x" and ev["tp"] == 8
    assert ev["vllm_commit"] == "93d8f834dd8a"
    assert ev["metrics"]["tput_per_gpu"] == 1000.0
    assert ev["build_url"].endswith("/builds/501")


def test_perf_event_dropped_for_nvidia():
    entry = {"name": "qwen-h200", "device": "h200", "tp": 8, "precision": "fp8", "model": "Qwen"}
    ident = {**_IDENTITY, "image": "vllm/vllm-openai:nightly-93d8f834dd8a"}
    ev = art.perf_event(
        {"total_token_throughput": 8000.0, "output_throughput": 800.0},
        entry=entry, config={"isl": 1, "osl": 1, "conc": 1}, identity=ident,
    )
    assert ev is None


def test_perf_event_conc_falls_back_to_raw():
    entry = {"name": "m-mi355x", "device": "mi355x", "tp": 1, "precision": "bf16", "model": "m"}
    ev = art.perf_event(
        {"total_token_throughput": 10.0, "max_concurrency": 64},
        entry=entry, config={"isl": 8, "osl": 8, "conc": None}, identity=_IDENTITY,
    )
    assert ev["conc"] == 64


def test_accuracy_event_flattens_lm_eval_results():
    results_json = {
        "results": {"gsm8k": {
            "exact_match,strict-match": 0.842,
            "exact_match_stderr,strict-match": 0.01,
            "alias": "gsm8k",
        }},
        "config": {"model": "MiniMaxAI/MiniMax-M2.5"},
    }
    entry = {"name": "minimax_m2_5-mi355x", "device": "mi355x", "model": "MiniMaxAI/MiniMax-M2.5"}
    ev = art.accuracy_event(results_json, workload="minimax_m2_5-mi355x", task="gsm8k", entry=entry, identity=_IDENTITY)
    assert ev["event"] == "accuracy_result"
    assert ev["nightly"] is True
    assert ev["model"] == "MiniMaxAI/MiniMax-M2.5"
    rows = ev["results"]
    assert any(r["metric"] == "exact_match,strict-match" and r["value"] == 0.842 and r["primary"] for r in rows)
    assert all("stderr" not in r["metric"] for r in rows)


def test_accuracy_event_dropped_for_nvidia_workload():
    results_json = {"results": {"gsm8k": {"exact_match,strict-match": 0.8}}, "config": {"model": "x"}}
    entry = {"name": "x-h200", "device": "h200", "model": "x"}
    ident = {**_IDENTITY, "image": "vllm/vllm-openai:nightly-abc"}
    assert art.accuracy_event(results_json, workload="x-h200", task="gsm8k", entry=entry, identity=ident) is None


# ── dedup identity ─────────────────────────────────────────────────────────

def test_event_key_distinguishes_configs_and_is_stable():
    entry = {"name": "m-mi355x", "device": "mi355x", "tp": 8, "precision": "bf16", "model": "m"}
    raw = {"total_token_throughput": 8000.0, "output_throughput": 800.0}
    a = art.perf_event(raw, entry=entry, config={"isl": 8192, "osl": 1024, "conc": 128}, identity=_IDENTITY)
    b = art.perf_event(raw, entry=entry, config={"isl": 8192, "osl": 1024, "conc": 256}, identity=_IDENTITY)
    assert art.event_key(a) == art.event_key(a)  # stable
    assert art.event_key(a) != art.event_key(b)  # concurrency distinguishes


def test_event_key_perf_vs_accuracy_differ():
    entry = {"name": "m-mi355x", "device": "mi355x", "tp": 1, "precision": "bf16", "model": "m"}
    p = art.perf_event({"total_token_throughput": 1.0}, entry=entry, config={"isl": 1, "osl": 1, "conc": 1}, identity=_IDENTITY)
    a = art.accuracy_event(
        {"results": {"gsm8k": {"exact_match,strict-match": 0.8}}, "config": {"model": "m"}},
        workload="m-mi355x", task="gsm8k", entry=entry, identity=_IDENTITY,
    )
    assert art.event_key(p)[0] == "perf" and art.event_key(a)[0] == "accuracy"
