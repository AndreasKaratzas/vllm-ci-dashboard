#!/usr/bin/env python3
"""Publish current main-CI AMD coverage from one immutable source commit.

Coverage means an AMD route is configured for a logical CUDA workload in the
authoritative ``ci`` definitions. It does not claim runtime success, identical
commands, or blocking gates for optional/soft-fail routes. Legacy ``amd-ci``
definitions never contribute to these percentages.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.bounded_json import pretty_json_bytes, write_pretty_json_lkg  # noqa: E402
from vllm.dashboard_storage_budget import writer_max_bytes  # noqa: E402
from vllm.main_ci_definitions import (  # noqa: E402
    FULL_SHA,
    MainCISnapshot,
    amd_source_steps,
    is_cuda_definition,
    load_snapshot,
    logical_title,
    route_is_required,
    source_steps,
)
from vllm.reviewed_definition_labels import (  # noqa: E402
    KEYED_DEFINITION_RE,
    flatten_execution_commands,
    load_definition_parity,
)

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG = ROOT / "config" / "vllm_upstream_test_group_parity.json"
OUTPUT = ROOT / "data" / "vllm" / "ci"
SCHEMA_VERSION = 3
TEST_GROUP_PARITY_MAX_BYTES = writer_max_bytes("test_group_parity")


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 1) if denominator else 0.0


def load_review(path: Path = CONFIG) -> dict[str, Any]:
    """Load classification policy; population and coverage come from CI YAML."""
    try:
        policy = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"Unable to read main CI parity policy {path}: {exc}") from exc
    if not isinstance(policy, dict) or policy.get("schema_version") != 4:
        raise ValueError("main CI parity policy schema_version must be 4")
    if policy.get("pipeline") != "ci":
        raise ValueError("main CI parity policy must use the authoritative ci pipeline")
    exclusions = policy.get("not_applicable")
    if not isinstance(exclusions, list):
        raise ValueError("not_applicable must be a list of exact definition keys and reasons")
    seen = set()
    for item in exclusions:
        if not isinstance(item, dict):
            raise ValueError("not_applicable entries must be objects")
        identity, reason = item.get("definition_id"), item.get("reason")
        if not isinstance(identity, str) or not KEYED_DEFINITION_RE.fullmatch(identity):
            raise ValueError("not_applicable entries require an explicit upstream YAML key")
        if identity in seen:
            raise ValueError(f"duplicate not-applicable definition: {identity}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("not_applicable entries require a non-empty reason")
        seen.add(identity)
    return policy


def _commands(step: dict[str, Any]) -> list[str]:
    raw = [step["command"]] if "command" in step else step.get("commands", [])
    return flatten_execution_commands(raw)


def build_payload(
    review: dict[str, Any],
    config_path: Path = CONFIG,
    *,
    generated_at: str | None = None,
    definition_parity: dict[str, Any] | None = None,
    snapshot: MainCISnapshot | None = None,
) -> dict[str, Any]:
    """Derive counts from current definitions; policy only marks applicability."""
    snapshot = snapshot or load_snapshot()
    steps = source_steps(snapshot)
    cuda = [row for row in steps if is_cuda_definition(row)]
    routes = amd_source_steps(snapshot)
    mirrors = {
        row["upstream_definition_id"]: row
        for row in routes
        if row["source_kind"] == "inline_mirror"
    }
    excluded = {row["definition_id"]: row["reason"] for row in review["not_applicable"]}
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in cuda:
        title = logical_title(row)
        if not title:
            raise ValueError(
                f"CUDA definition has no logical workload title: {row['definition_id']}"
            )
        by_group[(row["area"], title.casefold())].append(row)
    groups = []
    for group_id, ((area, _), definitions) in enumerate(sorted(by_group.items()), 1):
        ids = sorted(row["definition_id"] for row in definitions)
        amd = [mirrors[identity] for identity in ids if identity in mirrors]
        required = any(route_is_required(row) for row in amd)
        optional = any(bool(row.get("optional")) and not bool(row.get("soft_fail")) for row in amd)
        unsupported = not amd and all(identity in excluded for identity in ids)
        state = "existing" if amd else "unsupported" if unsupported else "action"
        gate_kind = (
            ("required" if required else "optional" if optional else "soft_fail")
            if amd
            else ("not_applicable" if unsupported else "missing")
        )
        assessment = (
            "AMD route configured in main ci; flags and runtime results are reported separately."
            if amd
            else "; ".join(sorted({excluded[identity] for identity in ids}))
            if unsupported
            else "No inline AMD route is configured for this current main ci workload."
        )
        groups.append(
            {
                "id": group_id,
                "area": area,
                "title": logical_title(definitions[0]),
                "state": state,
                "gate_kind": gate_kind,
                "gate_required": required,
                "upstream_definition_ids": ids,
                "cuda_variants": ", ".join(
                    sorted({str(row.get("device") or "CUDA") for row in definitions})
                ),
                "amd_definition_ids": [row["key"] for row in amd],
                "amd_routes": len(amd),
                "assessment": assessment,
                "definition_resolution": {
                    "status": "resolved",
                    "source_kind": "upstream_keys",
                    "commit_sha": snapshot.commit_sha,
                    "definition_ids": ids,
                    "labels": [str(row.get("label") or "") for row in definitions],
                },
            }
        )
    totals = Counter(row["state"] for row in groups)
    gate_counts = Counter(row["gate_kind"] for row in groups)
    applicable = len(groups) - totals["unsupported"]
    areas = []
    for area in sorted({row["area"] for row in groups}):
        rows = [row for row in groups if row["area"] == area]
        counts = Counter(row["state"] for row in rows)
        areas.append(
            {
                "area": area,
                "total": len(rows),
                **{key: counts[key] for key in ("existing", "unsupported", "action")},
                "applicable": len(rows) - counts["unsupported"],
                "complete_on_main": counts["existing"],
                "missing_on_main": counts["action"],
            }
        )
    try:
        policy_path = config_path.relative_to(ROOT).as_posix()
    except ValueError:
        policy_path = str(config_path)
    runtime_source = (definition_parity or {}).get("source") or {}
    runtime_commit = str(runtime_source.get("commit_sha") or "")
    runtime_commit = runtime_commit if FULL_SHA.fullmatch(runtime_commit) else None
    source = {
        "repository": "vllm-project/vllm",
        "pipeline": "ci",
        "hardware_scope": "amd_mi_gpu",
        "upstream_scope": "cuda_gpu_configuration_benchmark",
        "main_commit": snapshot.commit_sha,
        "current_definition_commit_sha": snapshot.commit_sha,
        "runtime_source_commit_sha": runtime_commit,
        "config_path": policy_path,
        "fetched_at": snapshot.fetched_at,
        "url": f"https://github.com/vllm-project/vllm/tree/{snapshot.commit_sha}/.buildkite/test_areas",
    }
    mirror_rows = []
    step_index = {row["definition_id"]: row for row in steps}
    for identity, route in sorted(mirrors.items()):
        upstream = step_index.get(identity)
        mirror_rows.append(
            {
                "definition_id": identity,
                "nvidia_definition_id": identity,
                "source_file": route["source_file"],
                "upstream_label": upstream["label"],
                "nvidia_label": upstream["label"],
                "amd_label": route["label"],
                "device": route.get("device"),
                "amd_device": route.get("device"),
                "num_devices": route.get("num_devices"),
                "optional": bool(route.get("optional")),
                "soft_fail": bool(route.get("soft_fail")),
                "required": route_is_required(route),
                "gate_kind": "required"
                if route_is_required(route)
                else "soft_fail"
                if route.get("soft_fail")
                else "optional",
                "commands_match": _commands(upstream) == _commands(route),
                "source_url": f"https://github.com/vllm-project/vllm/blob/{snapshot.commit_sha}/{route['source_file']}",
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reviewed_at": snapshot.fetched_at[:10],
        "source": source,
        "scope": {
            "count_basis": "One logical CUDA workload per source area and hardware-free label; hardware replicas and parallel shards count once.",
            "coverage_basis": "At least one inline AMD mirror in current main ci; native AMD and legacy amd-ci never inflate the CUDA coverage numerator.",
            "gate_basis": "Required means the configured AMD route has neither optional nor soft_fail; this is source configuration, not proof of runtime success or blocking behavior.",
            "excluded": [
                "CPU, no_gpu, and other accelerator definitions",
                "Explicit NVIDIA/backend-specific or temporary monitoring workloads listed in classification policy",
            ],
            "included_but_classified": [
                "Current CUDA workloads without an AMD route remain missing unless every replica is explicitly not applicable."
            ],
            "upstream_physical_definitions": len(cuda),
        },
        "summary": {
            "upstream_physical_definitions": len(cuda),
            "upstream_logical_groups": len(groups),
            "applicable_groups": applicable,
            "main_complete_groups": totals["existing"],
            "unsupported_groups": totals["unsupported"],
            "action_groups": totals["action"],
            "main_missing_groups": totals["action"],
            "main_applicable_rate_pct": _rate(totals["existing"], applicable),
            "main_required_groups": gate_counts["required"],
            "main_required_rate_pct": _rate(gate_counts["required"], applicable),
            "main_optional_only_groups": gate_counts["optional"],
            "main_soft_fail_only_groups": gate_counts["soft_fail"],
        },
        "rocm_inventory": {
            "main": {
                "physical_definitions": len(routes),
                "logical_groups": len(
                    {(row["area"], logical_title(row).casefold()) for row in routes}
                ),
                "inline_mirror_definitions": len(mirror_rows),
                "native_amd_definitions": sum(row["source_kind"] == "native_amd" for row in routes),
            },
            "count_basis": "AMD routes in main ci only; native AMD workloads do not add to CUDA-to-AMD coverage.",
        },
        "mirror_inventory": {
            "source": source,
            "summary": {
                "total": len(mirror_rows),
                "required": sum(row["required"] for row in mirror_rows),
                "optional": sum(row["optional"] for row in mirror_rows),
                "soft_fail": sum(row["soft_fail"] for row in mirror_rows),
            },
            "rows": mirror_rows,
        },
        "areas": areas,
        "groups": groups,
    }


def bounded_payload(
    payload: dict[str, Any], *, max_bytes: int = TEST_GROUP_PARITY_MAX_BYTES
) -> dict[str, Any]:
    """Bound detail rows while preserving complete aggregate counts."""
    if max_bytes <= 0:
        raise ValueError("test-group parity byte budget must be positive")
    source_groups = sorted(
        (dict(row) for row in payload.get("groups") or []), key=lambda row: row["id"]
    )
    priority = {"action": 3, "unsupported": 2, "existing": 1}
    prioritized = sorted(source_groups, key=lambda row: (-priority.get(row["state"], 0), row["id"]))
    source_counts = Counter(row["state"] for row in source_groups)

    def candidate(count: int) -> dict[str, Any]:
        ids = {row["id"] for row in prioritized[:count]}
        published = [row for row in source_groups if row["id"] in ids]
        counts = Counter(row["state"] for row in published)
        complete = len(published) == len(source_groups)
        return {
            **payload,
            "groups": published,
            "publication_retention": {
                "policy": "action_then_unsupported_then_existing_whole_rows_v1",
                "max_bytes": max_bytes,
                "complete_relative_to_source": complete,
                "aggregate_summary_complete": True,
                "area_rollups_complete": True,
                "groups": {
                    "source": len(source_groups),
                    "published": len(published),
                    "omitted": len(source_groups) - len(published),
                    "complete_relative_to_source": complete,
                },
                "by_state": {
                    key: {"source": value, "published": counts[key], "omitted": value - counts[key]}
                    for key, value in sorted(source_counts.items())
                },
            },
        }

    low, high, best = 0, len(source_groups), None
    while low <= high:
        keep = (low + high) // 2
        attempt = candidate(keep)
        if len(pretty_json_bytes(attempt)) <= max_bytes:
            best, low = attempt, keep + 1
        else:
            high = keep - 1
    if best is None:
        raise RuntimeError(
            "test-group parity fixed metadata exceeds its byte budget; preserving the last-known-good file"
        )
    return best


def publish(
    config_path: Path = CONFIG,
    output_dir: Path = OUTPUT,
    *,
    generated_at: str | None = None,
    definition_parity: dict[str, Any] | None = None,
    source_commit: str | None = None,
    snapshot: MainCISnapshot | None = None,
) -> tuple[Path, dict[str, Any]]:
    payload = bounded_payload(
        build_payload(
            load_review(config_path),
            config_path,
            generated_at=generated_at,
            definition_parity=definition_parity
            if definition_parity is not None
            else load_definition_parity(output_dir / "config_parity.json"),
            snapshot=snapshot or load_snapshot(source_commit),
        ),
        max_bytes=TEST_GROUP_PARITY_MAX_BYTES,
    )
    output_path = output_dir / "test_group_parity.json"
    write_pretty_json_lkg(
        output_path,
        payload,
        max_bytes=TEST_GROUP_PARITY_MAX_BYTES,
        label="main CI test-group parity snapshot",
    )
    return output_path, payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish current main ci AMD coverage from pinned definitions"
    )
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--source-commit", default=None)
    args = parser.parse_args()
    path, payload = publish(
        args.config, args.output, generated_at=args.generated_at, source_commit=args.source_commit
    )
    print(
        f"Wrote {path}: {payload['summary']['main_complete_groups']}/{payload['summary']['applicable_groups']} applicable main-ci groups have AMD routes"
    )


if __name__ == "__main__":
    main()
