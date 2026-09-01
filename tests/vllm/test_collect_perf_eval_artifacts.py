"""Tests for the Buildkite-artifact perf-eval collector.

These cover the pure logic that turns raw Buildkite builds + ``vllm bench serve``
/ lm-eval artifacts into the canonical events the aggregator consumes: nightly
detection, artifact-path classification, the per-GPU transform, workload-recipe
parsing, AMD-only filtering, and dedup identity. No network is touched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import json

import pytest
import requests

from vllm.ci import perf_eval_webhook as event_store

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


# ── Buildkite request / pagination resilience ──────────────────────────────

class _Response:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = [] if payload is None else payload
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_bk_get_retries_429_then_returns_complete_page(monkeypatch):
    responses = iter(
        [
            _Response(429, headers={"Retry-After": "0"}),
            _Response(200, [{"number": 501}]),
        ]
    )
    sleeps = []
    monkeypatch.setattr(art.requests, "get", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(art.time, "sleep", sleeps.append)

    assert art._bk_get("/builds", "fake-token") == [{"number": 501}]
    assert sleeps == [0]


def test_bk_get_429_retry_exhaustion_raises_instead_of_empty_page(monkeypatch):
    calls = []

    def rate_limited(*args, **kwargs):
        calls.append(1)
        return _Response(429, headers={"Retry-After": "0"})

    monkeypatch.setattr(art, "BK_GET_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(art.requests, "get", rate_limited)
    monkeypatch.setattr(art.time, "sleep", lambda seconds: None)

    with pytest.raises(requests.HTTPError, match="429"):
        art._bk_get("/builds", "fake-token")

    assert len(calls) == 2


def test_bk_paginate_fails_closed_when_last_allowed_page_is_full(monkeypatch):
    requested_pages = []

    def full_page(path, token, params=None):
        requested_pages.append(params["page"])
        return [{"number": params["page"]}] * 100

    monkeypatch.setattr(art, "_bk_get", full_page)

    with pytest.raises(RuntimeError, match="pagination safety cap"):
        art._bk_paginate("/builds", "fake-token", max_pages=2)

    assert requested_pages == [1, 2]


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


# ── pre-download artifact dedup ──────────────────────────────────────────────────

_BUILD = {
    "number": 501,
    "branch": "main",
    "message": "Nightly run 2026-06-30: commit 93d8f834dd8a",
    "created_at": "2026-06-30T02:00:00Z",
    "finished_at": "2026-06-30T03:00:00Z",
    "commit": "1111111111ab",
    "web_url": "https://buildkite.com/vllm/perf-eval/builds/501",
    "env": {"VLLM_IMAGE": "vllm/vllm-openai-rocm:nightly-93d8f834dd8a"},
}
_ENTRY = {
    "name": "m-mi355x",
    "device": "mi355x",
    "tp": 1,
    "precision": "bf16",
    "model": "m",
}
_CONFIGS = {"8-in-8-out-conc-1": {"isl": 8, "osl": 8, "conc": 1}}


def _stub_collection(monkeypatch, artifacts, downloads, *, build=None):
    selected_build = build or _BUILD
    monkeypatch.setattr(
        art,
        "fetch_workload_map",
        lambda _token: {"m-mi355x": (_ENTRY, _CONFIGS)},
    )

    def paginate(path, _token, params=None, max_pages=10):
        if path.endswith("/builds"):
            return [selected_build]
        if path.endswith(f"/builds/{selected_build['number']}/artifacts"):
            return artifacts
        raise AssertionError(path)

    monkeypatch.setattr(art, "_bk_paginate", paginate)

    def download(url, _token):
        downloads.append(url)
        return {
            "model_id": "m",
            "total_token_throughput": 10.0,
            "output_throughput": 1.0,
        }

    monkeypatch.setattr(art, "_bk_download_json", download)


def test_collect_skips_known_artifacts_before_download(tmp_path, monkeypatch):
    store = tmp_path / "events.jsonl"
    known = [
        {
            "event": "perf_result",
            "build_number": 501,
            "buildkite_artifact_id": "artifact-perf",
        },
        {
            "event": art.ARTIFACT_MARKER_EVENT,
            "build_number": 501,
            "buildkite_artifact_id": "artifact-accuracy",
        },
    ]
    store.write_text("".join(json.dumps(row) + "\n" for row in known))
    artifacts = [
        {
            "id": "artifact-perf",
            "path": "results/m-mi355x/bench-8-in-8-out-conc-1.json",
            "download_url": "https://example.test/perf",
        },
        {
            "id": "artifact-accuracy",
            "path": "results/m-mi355x/gsm8k/results_2026-06-30.json",
            "download_url": "https://example.test/accuracy",
        },
    ]
    downloads = []
    _stub_collection(monkeypatch, artifacts, downloads)

    assert art.collect(store, days=14, bk_token="bk", gh_token="gh") == 0
    assert downloads == []


@pytest.mark.parametrize("days", [0, 31])
def test_collect_rejects_lookback_outside_identity_horizon_before_requests(
    tmp_path, monkeypatch, days
):
    requested = []
    monkeypatch.setattr(
        art,
        "fetch_workload_map",
        lambda _token: requested.append("github"),
    )
    monkeypatch.setattr(
        art,
        "_bk_paginate",
        lambda *_args, **_kwargs: requested.append("buildkite"),
    )

    with pytest.raises(ValueError, match="between 1 and 30 days"):
        art.collect(tmp_path / "events.jsonl", days=days, bk_token="bk", gh_token="gh")

    assert requested == []


def test_collect_rejects_corrupt_store_before_requests(tmp_path, monkeypatch):
    store = tmp_path / "events.jsonl"
    original = b'{"event":"perf_result"}\n["not", "an", "object"]\n'
    store.write_bytes(original)
    requested = []
    monkeypatch.setattr(
        art,
        "fetch_workload_map",
        lambda _token: requested.append("github"),
    )
    monkeypatch.setattr(
        art,
        "_bk_paginate",
        lambda *_args, **_kwargs: requested.append("buildkite"),
    )

    with pytest.raises(ValueError, match="event must be a JSON object"):
        art.collect(store, days=14, bk_token="bk", gh_token="gh")

    assert requested == []
    assert store.read_bytes() == original


def test_new_event_carries_artifact_identity_and_next_run_skips_download(
    tmp_path, monkeypatch
):
    store = tmp_path / "events.jsonl"
    artifacts = [
        {
            "id": "artifact-perf",
            "job_id": "job-1",
            "path": "results/m-mi355x/bench-8-in-8-out-conc-1.json",
            "sha1sum": "ABC123",
            "download_url": "https://example.test/perf",
        }
    ]
    downloads = []
    _stub_collection(monkeypatch, artifacts, downloads)

    assert art.collect(store, days=14, bk_token="bk", gh_token="gh") == 1
    assert art.collect(store, days=14, bk_token="bk", gh_token="gh") == 0
    assert downloads == ["https://example.test/perf"]
    result = json.loads(store.read_text().splitlines()[0])
    assert result["buildkite_artifact_id"] == "artifact-perf"
    assert result["buildkite_artifact_job_id"] == "job-1"
    assert result["buildkite_artifact_path"] == (
        "results/m-mi355x/bench-8-in-8-out-conc-1.json"
    )
    assert result["buildkite_artifact_sha1"] == "abc123"


def test_legacy_duplicate_gets_marker_then_skips_future_downloads(
    tmp_path, monkeypatch
):
    store = tmp_path / "events.jsonl"
    legacy = {
        "event": "perf_result",
        "build_number": 501,
        "model": "m",
        "device": "mi355x",
        "isl": 8,
        "osl": 8,
        "conc": 1,
    }
    store.write_text(json.dumps(legacy) + "\n")
    artifacts = [
        {
            "id": "artifact-perf",
            "path": "results/m-mi355x/bench-8-in-8-out-conc-1.json",
            "download_url": "https://example.test/perf",
        }
    ]
    downloads = []
    _stub_collection(monkeypatch, artifacts, downloads)

    assert art.collect(store, days=14, bk_token="bk", gh_token="gh") == 0
    assert art.collect(store, days=14, bk_token="bk", gh_token="gh") == 0
    assert downloads == ["https://example.test/perf"]
    rows = [json.loads(line) for line in store.read_text().splitlines()]
    assert [row["event"] for row in rows] == [
        "perf_result",
        art.ARTIFACT_INDEX_EVENT,
    ]
    assert art.artifact_keys_from_event(rows[1]) == (("id", "artifact-perf"),)


def test_pruned_result_identity_prevents_repeated_artifact_downloads(
    tmp_path, monkeypatch
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    old_at = now - timedelta(days=20)
    old_build = {
        **_BUILD,
        "number": 601,
        "message": f"Nightly run {old_at:%Y-%m-%d}: commit deadbeef123456",
        "created_at": old_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished_at": (old_at + timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }

    def result(commit, observed_at, *, artifact_id="", padding=""):
        return {
            "event": "perf_result",
            "nightly": True,
            "date": observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "vllm_commit": commit,
            "build_number": 601 if artifact_id else int(commit[-1]),
            "model": "m",
            "device": "mi355x",
            "tp": 1,
            "precision": "bf16",
            "isl": 8,
            "osl": 8,
            "conc": 1,
            "metrics": {"tput_per_gpu": 1.0},
            "buildkite_artifact_id": artifact_id,
            "padding": padding,
        }

    history = [
        result(
            "deadbeef123456",
            old_at,
            artifact_id="artifact-pruned",
            padding="old" * 2_000,
        ),
        *[
            result(
                f"abcdef00000{index}",
                now - timedelta(days=4 - index),
                padding=str(index) * 2_000,
            )
            for index in range(1, 4)
        ],
    ]
    smallest = event_store._compact_events_once(
        history,
        now,
        history_days=14,
        min_nightlies=2,
        auxiliary_days=14,
    )
    budget = len(event_store._encoded_events(smallest))
    store = tmp_path / "events.jsonl"
    event_store.write_events_atomic(store, history, now=now, max_bytes=budget)

    compacted = event_store.read_events(store)
    assert all(
        row.get("buildkite_artifact_id") != "artifact-pruned"
        for row in compacted
        if row.get("event") == "perf_result"
    )
    assert ("id", "artifact-pruned") in {
        key for row in compacted for key in art.artifact_keys_from_event(row)
    }

    artifacts = [
        {
            "id": "artifact-pruned",
            "path": "results/m-mi355x/bench-8-in-8-out-conc-1.json",
            "download_url": "https://example.test/pruned",
        }
    ]
    downloads = []
    _stub_collection(
        monkeypatch,
        artifacts,
        downloads,
        build=old_build,
    )

    assert art.collect(store, days=30, bk_token="bk", gh_token="gh") == 0
    assert art.collect(store, days=30, bk_token="bk", gh_token="gh") == 0
    assert downloads == []
