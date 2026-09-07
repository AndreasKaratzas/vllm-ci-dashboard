#!/usr/bin/env python3
"""Seed ``data/vllm/perf_eval/events.jsonl`` with representative AMD nightlies.

The Perf Eval tab is fed by webhook pushes from the live ``vllm/perf-eval``
pipeline. Until those webhooks have run for a few nights, this script writes a
small, realistic sample event log so the dashboard renders trends end-to-end
and the test suite has meaningful fixtures.

The events here are **canonical** — exactly the shape
``vllm.ci.perf_eval_webhook`` produces — so the same ``collect_perf_eval.py``
path that processes live webhook data processes this seed unchanged. The
numbers are illustrative (clearly synthetic), modelled on the AMD-only
workloads in the perf-eval repo. Re-running the collector over a real event
log simply supersedes this seed.

Usage::

    python scripts/vllm/seed_perf_eval_events.py            # write the seed log
    python scripts/vllm/collect_perf_eval.py                # build perf_eval.json
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.ci.perf_eval_webhook import METRIC_META, write_events_atomic  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
STORE = ROOT / "data" / "vllm" / "perf_eval" / "events.jsonl"

# Three consecutive nightlies, each tagged with a distinct vLLM commit so the
# dashboard can show "which commit produced this" and chart commit-to-commit
# movement.
NIGHTLIES = [
    {"date": "2026-06-23", "commit": "a1b2c3d4e5f6", "build": 482},
    {"date": "2026-06-24", "commit": "b2c3d4e5f6a7", "build": 489},
    {"date": "2026-06-25", "commit": "c3d4e5f6a7b8", "build": 496},
]

# AMD-only workloads (MI355X / MI300X). Each carries a baseline perf profile and
# accuracy score, plus per-night multipliers that create an improving trend,
# a regression, and a flat line so the executive view has something to say.
WORKLOADS = [
    {
        "model": "MiniMaxAI/MiniMax-M2.5",
        "device": "mi355x",
        "tp": 8,
        "precision": "bf16",
        "isl": 8192, "osl": 1024, "conc": 128,
        "base": {"tput_per_gpu": 1180.0, "output_tput_per_gpu": 131.0, "mean_ttft": 0.420, "mean_tpot": 0.0185, "p99_ttft": 0.910},
        "tput_trend": [1.00, 1.03, 1.07],   # steady improvement
        "ttft_trend": [1.00, 0.97, 0.94],   # latency dropping (good)
        "gsm8k": [0.842, 0.846, 0.849],
    },
    {
        "model": "deepseek-ai/DeepSeek-V4-Pro",
        "device": "mi355x",
        "tp": 8,
        "precision": "fp8",
        "isl": 8192, "osl": 1024, "conc": 128,
        "base": {"tput_per_gpu": 1640.0, "output_tput_per_gpu": 182.0, "mean_ttft": 0.355, "mean_tpot": 0.0142, "p99_ttft": 0.770},
        "tput_trend": [1.00, 1.01, 0.93],   # regression on the latest night
        "ttft_trend": [1.00, 1.00, 1.06],   # latency creeping up (bad)
        "gsm8k": [0.905, 0.906, 0.873],     # accuracy regression on latest
    },
    {
        "model": "openai/gpt-oss-120b",
        "device": "mi355x",
        "tp": 1,
        "precision": "fp8",
        "isl": 8192, "osl": 1024, "conc": 128,
        "base": {"tput_per_gpu": 5400.0, "output_tput_per_gpu": 600.0, "mean_ttft": 0.180, "mean_tpot": 0.0098, "p99_ttft": 0.410},
        "tput_trend": [1.00, 1.005, 1.004],  # essentially flat
        "ttft_trend": [1.00, 0.99, 1.00],
        "gsm8k": [0.781, 0.783, 0.782],
    },
    {
        "model": "moonshotai/Kimi-K2.5",
        "device": "mi300x",
        "tp": 8,
        "precision": "bf16",
        "isl": 8192, "osl": 1024, "conc": 128,
        "base": {"tput_per_gpu": 910.0, "output_tput_per_gpu": 101.0, "mean_ttft": 0.560, "mean_tpot": 0.0231, "p99_ttft": 1.180},
        "tput_trend": [1.00, 1.06, 1.11],   # strong improvement
        "ttft_trend": [1.00, 0.95, 0.92],
        "gsm8k": [0.812, 0.818, 0.821],
    },
]

IMAGE_REPO = "vllm/vllm-openai-rocm"


def _image(commit: str) -> str:
    return f"{IMAGE_REPO}:nightly-{commit}"


def _build_url(build: int) -> str:
    return f"https://buildkite.com/vllm/perf-eval/builds/{build}"


def _round(value: float) -> float:
    return round(value, 4)


def build_events() -> list[dict]:
    events: list[dict] = []
    for i, night in enumerate(NIGHTLIES):
        commit = night["commit"]
        image = _image(commit)
        build_url = _build_url(night["build"])
        ts = f"{night['date']} 02:0{i}:00"
        for wl in WORKLOADS:
            base = wl["base"]
            throughput_mult = wl["tput_trend"][i]
            latency_mult = wl["ttft_trend"][i]
            metrics = {
                "tput_per_gpu": _round(base["tput_per_gpu"] * throughput_mult),
                "output_tput_per_gpu": _round(base["output_tput_per_gpu"] * throughput_mult),
                "input_tput_per_gpu": _round(
                    (base["tput_per_gpu"] - base["output_tput_per_gpu"]) * throughput_mult
                ),
                "mean_ttft": _round(base["mean_ttft"] * latency_mult),
                "p99_ttft": _round(base["p99_ttft"] * latency_mult),
                "mean_tpot": _round(base["mean_tpot"] * latency_mult),
                "mean_intvty": _round(1000.0 / (base["mean_tpot"] * latency_mult * 1000.0)),
            }
            # Drop any metric not in the shared registry (defensive).
            metrics = {k: v for k, v in metrics.items() if k in METRIC_META}
            events.append({
                "event": "perf_result",
                "received_at": f"{night['date']}T02:05:00Z",
                "nightly": True,
                "model": wl["model"],
                "device": wl["device"],
                "precision": wl["precision"],
                "tp": wl["tp"],
                "isl": wl["isl"],
                "osl": wl["osl"],
                "conc": wl["conc"],
                "date": ts,
                "build_number": night["build"],
                "build_url": build_url,
                "build_commit": commit,
                "branch": "main",
                "image": image,
                "vllm_commit": commit,
                "metrics": metrics,
            })
            events.append({
                "event": "accuracy_result",
                "received_at": f"{night['date']}T02:40:00Z",
                "nightly": True,
                "model": wl["model"],
                "workload": f"{wl['model'].split('/')[-1].lower().replace('.', '_').replace('-', '_')}-{wl['device']}",
                "task": "gsm8k",
                "device": wl["device"],
                "build_number": night["build"],
                "build_url": build_url,
                "build_commit": commit,
                "branch": "main",
                "image": image,
                "vllm_commit": commit,
                "results": [
                    {
                        "task": "gsm8k",
                        "metric": "exact_match,strict-match",
                        "value": wl["gsm8k"][i],
                        "primary": True,
                    }
                ],
            })
    return events


def main() -> int:
    events = build_events()
    write_events_atomic(STORE, events)
    print(f"Wrote {len(events)} seed events -> {STORE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
