"""Tests for the perf-eval aggregation (scripts/vllm/collect_perf_eval.py).

The collector turns a flat webhook event log into the per-model, per-metric
time series the executive view renders. These tests pin the behavior that
matters: AMD/nightly filtering, dynamic model/workload discovery, history
ordering, latest-vs-previous deltas, and the red/green status thresholds.
"""

from __future__ import annotations

import importlib

collect = importlib.import_module("vllm.collect_perf_eval")


def _perf_event(commit, value, *, model="org/M", device="mi355x", date=None, nightly=True,
                metric="tput_per_gpu", ttft=None):
    metrics = {metric: value}
    if ttft is not None:
        metrics["mean_ttft"] = ttft
    # A real NVIDIA run ships a non-ROCm image; mirror that so AMD filtering
    # has a true NVIDIA payload to reject.
    image = (
        f"vllm/vllm-openai-rocm:nightly-{commit}"
        if str(device).lower().startswith("mi")
        else f"vllm/vllm-openai:nightly-{commit}"
    )
    return {
        "event": "perf_result",
        "nightly": nightly,
        "model": model,
        "device": device,
        "isl": 8192, "osl": 1024, "conc": 128, "tp": 8, "precision": "bf16",
        "date": date or f"2026-06-2{commit[-1]} 02:00:00",
        "vllm_commit": commit,
        "build_url": f"https://buildkite.com/vllm/perf-eval/builds/{commit[-1]}",
        "build_number": int(commit[-1]),
        "image": image,
        "metrics": metrics,
    }


def _acc_event(commit, value, *, model="org/M", task="gsm8k"):
    return {
        "event": "accuracy_result",
        "nightly": True,
        "model": model,
        "workload": "m-mi355x",
        "device": "mi355x",
        "vllm_commit": commit,
        "build_url": f"https://buildkite.com/vllm/perf-eval/builds/{commit[-1]}",
        "build_number": int(commit[-1]),
        "image": f"vllm/vllm-openai-rocm:nightly-{commit}",
        "results": [{"task": task, "metric": "exact_match,strict-match", "value": value, "primary": True}],
    }


# ── status / delta logic ──────────────────────────────────────────────────

def test_status_higher_is_better_improvement_is_good():
    s = collect._status("higher", latest=110.0, previous=100.0, rel=True)
    assert s["status"] == "good"
    assert round(s["delta_pct"], 1) == 10.0


def test_status_higher_is_better_regression_is_bad():
    s = collect._status("higher", latest=90.0, previous=100.0, rel=True)
    assert s["status"] == "bad"


def test_status_lower_is_better_decrease_is_good():
    s = collect._status("lower", latest=0.40, previous=0.50, rel=True)
    assert s["status"] == "good"


def test_status_small_move_is_neutral_noise():
    # 1% move is below the 2% perf threshold.
    s = collect._status("higher", latest=101.0, previous=100.0, rel=True)
    assert s["status"] == "neutral"


def test_status_accuracy_uses_absolute_threshold():
    # +0.002 is below the 0.005 accuracy band -> neutral.
    assert collect._status("higher", 0.852, 0.850, rel=False)["status"] == "neutral"
    # -0.03 is a real regression.
    assert collect._status("higher", 0.820, 0.850, rel=False)["status"] == "bad"


def test_status_first_nightly_has_no_previous():
    s = collect._status("higher", latest=100.0, previous=None, rel=True)
    assert s["previous"] is None
    assert s["status"] == "neutral"
    assert s["delta"] is None


# ── aggregation ────────────────────────────────────────────────────────────

def test_aggregate_filters_non_nightly_and_nvidia():
    events = [
        _perf_event("aaaaaaaaaaa1", 100.0),                       # kept
        _perf_event("aaaaaaaaaaa2", 999.0, nightly=False),        # dropped: not nightly
        _perf_event("aaaaaaaaaaa3", 999.0, device="h200"),        # dropped: NVIDIA
    ]
    out = collect.aggregate(events)
    assert out["summary"]["models"] == 1
    series = out["models"][0]["perf_configs"][0]["metrics"]["tput_per_gpu"]["series"]
    assert [p["value"] for p in series] == [100.0]


def test_aggregate_orders_series_and_computes_latest_vs_previous():
    events = [
        _perf_event("deadbeef3", 120.0, date="2026-06-25 02:00:00"),
        _perf_event("deadbeef1", 100.0, date="2026-06-23 02:00:00"),
        _perf_event("deadbeef2", 110.0, date="2026-06-24 02:00:00"),
    ]
    out = collect.aggregate(events)
    block = out["models"][0]["perf_configs"][0]["metrics"]["tput_per_gpu"]
    # series sorted oldest -> newest by run timestamp
    assert [p["value"] for p in block["series"]] == [100.0, 110.0, 120.0]
    assert block["latest"] == 120.0
    assert block["previous"] == 110.0
    assert block["status"] == "good"
    # provenance is preserved on every point
    assert block["series"][-1]["vllm_commit"] == "deadbeef3"
    assert block["series"][-1]["build_url"].endswith("/builds/3")


def test_aggregate_discovers_models_dynamically():
    events = [
        _perf_event("aaaaaaaaaaa1", 100.0, model="org/Alpha"),
        _perf_event("aaaaaaaaaaa1", 200.0, model="org/Beta"),
    ]
    out = collect.aggregate(events)
    assert {m["model"] for m in out["models"]} == {"org/Alpha", "org/Beta"}


def test_aggregate_dedupes_same_nightly_keeping_last():
    # Two perf rows for the same commit (same nightly) — the later one wins,
    # so a re-posted result does not create a phantom second data point.
    events = [
        _perf_event("dupcommit1", 100.0, date="2026-06-23 02:00:00"),
        _perf_event("dupcommit1", 105.0, date="2026-06-23 09:00:00"),
    ]
    out = collect.aggregate(events)
    series = out["models"][0]["perf_configs"][0]["metrics"]["tput_per_gpu"]["series"]
    assert len(series) == 1
    assert series[0]["value"] == 105.0


def test_aggregate_accuracy_regression_flagged():
    events = [
        _acc_event("deadbeef1", 0.905),
        _acc_event("deadbeef2", 0.873),
    ]
    out = collect.aggregate(events)
    task = out["models"][0]["accuracy_tasks"][0]
    assert task["task"] == "gsm8k"
    assert task["latest"] == 0.873
    assert task["status"] == "bad"
    assert task["direction"] == "higher"


def test_aggregate_embeds_direction_metadata_for_frontend():
    out = collect.aggregate([_perf_event("aaaaaaaaaaa1", 100.0, ttft=0.4)])
    meta = out["metric_meta"]
    assert meta["tput_per_gpu"]["direction"] == "higher"
    assert meta["mean_ttft"]["direction"] == "lower"
    assert meta["accuracy"]["direction"] == "higher"


def test_aggregate_empty_log_is_safe():
    out = collect.aggregate([])
    assert out["models"] == []
    assert out["summary"]["models"] == 0
    assert "metric_meta" in out
