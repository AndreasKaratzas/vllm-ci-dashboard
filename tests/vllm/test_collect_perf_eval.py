"""Tests for the perf-eval aggregation (scripts/vllm/collect_perf_eval.py).

The collector turns a flat webhook event log into the per-model, per-metric
time series the executive view renders. These tests pin the behavior that
matters: AMD/nightly filtering, dynamic model/workload discovery, history
ordering, latest-vs-previous deltas, and the red/green status thresholds.
"""

from __future__ import annotations

from datetime import datetime, timezone
import importlib

import pytest

from vllm.ci import perf_eval_webhook as event_store

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


def test_bounded_aggregate_prunes_complete_nightlies_and_fits_exact_cap(tmp_path):
    events = []
    for nightly in range(10):
        event = _perf_event(
            f"abcdef00000{nightly}",
            float(nightly),
            date=f"2026-08-{nightly + 1:02d} 02:00:00",
        )
        event["metrics"] = {
            f"wide_metric_{metric}": float(nightly * 100 + metric)
            for metric in range(12)
        }
        event["build_url"] = "https://buildkite.example/builds/" + ("x" * 800)
        events.append(event)

    generated_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    # Compute a stable cap that admits the latest-two candidate (including the
    # cap value stored in its own metadata) but is far below wider candidates.
    budget = 1_000_000
    for _ in range(3):
        latest_two = collect.aggregate(events[-2:], generated_at=generated_at)
        latest_two["retention"] = {
            "event_history_days": collect.PERF_EVAL_HISTORY_DAYS,
            "artifact_identity_days": collect.PERF_EVAL_ARTIFACT_IDENTITY_DAYS,
            "max_bytes": budget,
            "nightly_limit": 2,
            "adaptive": True,
        }
        budget = len(collect.encoded_json(latest_two)) + 16

    payload = collect.bounded_aggregate(
        events,
        generated_at=generated_at,
        max_bytes=budget,
    )
    output = tmp_path / "perf_eval.json"
    collect.write_json_atomic(output, payload, max_bytes=budget)

    assert payload["retention"]["adaptive"] is True
    assert payload["retention"]["nightly_limit"] == 2
    assert payload["summary"]["nightlies"] == 2
    assert all(
        len(metric["series"]) == 2
        for model in payload["models"]
        for config in model["perf_configs"]
        for metric in config["metrics"].values()
    )
    assert output.stat().st_size == len(collect.encoded_json(payload))
    assert output.stat().st_size <= budget


def test_store_compaction_preserves_consumer_payload_for_retained_history():
    # Legacy input order is not always chronological. Compaction may select by
    # timestamp, but must not reorder retained rows and silently alter config
    # metadata chosen by the existing consumer.
    events = [
        _perf_event(
            "later0000001",
            101.0,
            date="2026-08-02 02:00:00",
        ),
        _perf_event(
            "earlier00001",
            100.0,
            date="2026-08-01 02:00:00",
        ),
    ]
    events[0]["precision"] = "fp8"
    events[1]["precision"] = "bf16"
    generated_at = datetime(2026, 9, 1, tzinfo=timezone.utc)

    compacted = event_store.compact_events(events, now=generated_at)

    assert collect.aggregate(
        compacted, generated_at=generated_at
    ) == collect.aggregate(events, generated_at=generated_at)


def test_derived_collector_rejects_corrupt_store_without_replacing_output(
    tmp_path, monkeypatch
):
    source = tmp_path / "events.jsonl"
    output = tmp_path / "perf_eval.json"
    source.write_text('{"event":"perf_result"}\nnull\n')
    original = b'{"known":"good"}\n'
    output.write_bytes(original)
    monkeypatch.setattr(
        collect.sys,
        "argv",
        [
            "collect_perf_eval.py",
            "--store",
            str(source),
            "--output",
            str(output),
        ],
    )

    with pytest.raises(ValueError, match="event must be a JSON object"):
        collect.main()

    assert output.read_bytes() == original
