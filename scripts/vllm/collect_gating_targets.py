#!/usr/bin/env python3
"""Publish the canonical AMD gating target list for the executive CI view."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG = ROOT / "config" / "vllm_amd_gating_targets.json"
OUTPUT = ROOT / "data" / "vllm" / "ci"

AREA_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("distributed", ("distributed", "torchrun", "pipeline", "rayexecutor", "context parallel", "comm ops", "2 node")),
    ("kernels", ("kernel", "cudagraph", "helion", "mamba", "fusedmoe", "kda")),
    ("entrypoints", ("entrypoints", "openai api", "api server", "responses api")),
    ("models-language", ("language models", "basic models", "model executor", "acceptance length")),
    ("models-multimodal", ("multi-modal", "multimodal", "processor")),
    ("spec-decode", ("spec decode", "speculators", "ngram", "draft model", "eagle")),
    ("lm-eval", ("lm eval", "gpqa", "mrcr")),
    ("pytorch", ("pytorch", "fullgraph", "compilation")),
    ("fusion", ("fusion", "quantized fusions")),
    ("quantization", ("quantization", "quantized models", "turboquant")),
    ("lora", ("lora",)),
    ("engine", ("engine", "eplb", "v1 core", "v1 e2e", "v1 sample", "v1 attention")),
    ("misc", ("examples", "regression", "platform", "plugin", "python-only", "metrics", "weight loading", "samplers")),
)


def infer_area(label: str) -> str:
    lowered = label.lower()
    for area, needles in AREA_KEYWORDS:
        if any(needle in lowered for needle in needles):
            return area
    return "other"


def load_targets(path: Path = CONFIG) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    groups = data.get("groups") or []
    if not isinstance(groups, list) or not groups:
        raise ValueError(f"{path} must contain a non-empty groups list")

    ids = [int(row.get("id") or 0) for row in groups]
    expected = list(range(1, len(groups) + 1))
    if ids != expected:
        raise ValueError(f"{path} ids must be contiguous 1..{len(groups)}")

    labels = [str(row.get("label") or "").strip() for row in groups]
    duplicates = [label for label, count in Counter(labels).items() if count > 1]
    if duplicates:
        raise ValueError(f"{path} has duplicate target labels: {duplicates}")
    if any(not label for label in labels):
        raise ValueError(f"{path} has blank target labels")

    normalized = []
    for row in groups:
        label = str(row.get("label") or "").strip()
        gating_signal = str(row.get("gating_signal") or row.get("source_signal") or "unknown")
        pf_signal = str(row.get("pf_signal") or row.get("readiness_signal") or "unknown")
        assigned_signal = str(row.get("assigned_signal") or row.get("target_signal") or "unknown")
        normalized.append({
            "id": int(row["id"]),
            "label": label,
            "area": str(row.get("area") or infer_area(label)),
            "gating_signal": gating_signal,
            "pf_signal": pf_signal,
            "assigned_signal": assigned_signal,
            "source_signal": gating_signal,
            "readiness_signal": pf_signal,
            "target_signal": assigned_signal,
            "owner": str(row.get("owner") or ""),
            "note": str(row.get("note") or ""),
        })
    return normalized


def build_payload(groups: list[dict[str, Any]], config_path: Path = CONFIG) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "config_path": config_path.relative_to(ROOT).as_posix(),
            "description": "Canonical AMD gating target list supplied by the ROCm CI owners.",
        },
        "summary": {
            "target_group_count": len(groups),
            "by_area": dict(sorted(Counter(row["area"] for row in groups).items())),
            "by_gating_signal": dict(sorted(Counter(row["gating_signal"] for row in groups).items())),
            "by_pf_signal": dict(sorted(Counter(row["pf_signal"] for row in groups).items())),
            "by_assigned_signal": dict(sorted(Counter(row["assigned_signal"] for row in groups).items())),
            "by_target_signal": dict(sorted(Counter(row["target_signal"] for row in groups).items())),
        },
        "groups": groups,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish AMD gating target list")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    groups = load_targets(args.config)
    payload = build_payload(groups, args.config)
    args.output.mkdir(parents=True, exist_ok=True)
    out_path = args.output / "gating_targets.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {out_path} with {len(groups)} target groups")


if __name__ == "__main__":
    main()
