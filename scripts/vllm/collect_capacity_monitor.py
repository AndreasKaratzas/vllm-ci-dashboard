#!/usr/bin/env python3
"""Collect static queue-capacity and AMD mirror workload projection data.

The dashboard's live queue snapshot answers "what is running right now?".
This collector answers the slower-moving companion question: "how much work
could our AMD mirror test surface create relative to the queues we own?"

It scans upstream ``.buildkite/test_areas/*.yaml`` files for ``mirror.amd``
definitions, counts their dependency scope, and writes a compact JSON payload
for the Queue Monitor's Capacity Monitor subview.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

import requests
import yaml


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.bounded_json import pretty_json_bytes, write_pretty_json_lkg  # noqa: E402
from vllm.dashboard_storage_budget import writer_max_bytes  # noqa: E402


log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT = ROOT / "data" / "vllm" / "ci"
CAPACITY_CONFIG_PATH = ROOT / "config" / "vllm_amd_queue_capacity.json"
CAPACITY_MONITOR_MAX_BYTES = writer_max_bytes("capacity_monitor")

CAPACITY_GROUP_INDEX_FIELDS = (
    "key",
    "label",
    "area",
    "yaml_file",
    "yaml_index",
    "device",
    "queue",
    "in_capacity_scope",
    "parallelism",
    "timeout_in_minutes",
    "optional",
    "dependency_file_count",
    "dependency_lines",
)

GITHUB_REPO = "vllm-project/vllm"
GITHUB_REF = "main"
GITHUB_API_BASE = "https://api.github.com/repos"
TEST_AREAS_DIR = ".buildkite/test_areas"

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
}
BINARY_EXTS = {
    ".bin",
    ".bz2",
    ".ckpt",
    ".gif",
    ".gz",
    ".jpeg",
    ".jpg",
    ".npy",
    ".onnx",
    ".pdf",
    ".png",
    ".pt",
    ".safetensors",
    ".so",
    ".tar",
    ".whl",
    ".zip",
}
MULTISPACE_RE = re.compile(r"\s+")
FULL_COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")


def load_capacity_config(path: Path = CAPACITY_CONFIG_PATH) -> dict[str, Any]:
    """Load and validate the authoritative AMD queue-capacity configuration."""

    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read AMD queue capacity config {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if payload.get("schema_version") != 1:
        raise ValueError(f"{path} must use schema_version 1")

    projection = payload.get("projection")
    if not isinstance(projection, dict):
        raise ValueError(f"{path} must contain a projection object")
    for field in (
        "target_groups",
        "declared_current_mirror_groups",
        "declared_existing_groups",
        "declared_new_groups",
    ):
        value = projection.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{path} projection.{field} must be a non-negative integer")
    if projection["target_groups"] <= 0:
        raise ValueError(f"{path} projection.target_groups must be positive")
    if not str(projection.get("note") or "").strip():
        raise ValueError(f"{path} projection.note must be non-empty")

    workload_pipelines = payload.get("workload_pipelines")
    if not isinstance(workload_pipelines, dict):
        raise ValueError(f"{path} must contain a workload_pipelines object")
    for workload in ("omni", "main"):
        pipelines = workload_pipelines.get(workload)
        if (
            not isinstance(pipelines, list)
            or not pipelines
            or any(not isinstance(item, str) or not item.strip() for item in pipelines)
        ):
            raise ValueError(
                f"{path} workload_pipelines.{workload} must be a non-empty string list"
            )

    scope = payload.get("scope")
    if not isinstance(scope, dict):
        raise ValueError(f"{path} must contain a scope object")
    excluded_classes = scope.get("excluded_queue_classes")
    if not isinstance(excluded_classes, list) or "perf_eval" not in {
        str(item).strip().lower() for item in excluded_classes
    }:
        raise ValueError(f"{path} scope.excluded_queue_classes must include perf_eval")
    non_gating_queues = scope.get("non_gating_queues")
    if not isinstance(non_gating_queues, list) or not non_gating_queues:
        raise ValueError(f"{path} scope.non_gating_queues must be a non-empty list")
    normalized_non_gating: list[dict[str, Any]] = []
    seen_non_gating_ids: set[str] = set()
    for index, raw in enumerate(non_gating_queues):
        if not isinstance(raw, dict):
            raise ValueError(f"{path} scope.non_gating_queues[{index}] must be an object")
        queue_id = str(raw.get("id") or "").strip().lower()
        purpose = str(raw.get("purpose") or "").strip().lower()
        max_concurrent_jobs = raw.get("max_concurrent_jobs")
        node_equivalents = raw.get("node_equivalents")
        if (
            queue_id != "amd-cpu"
            and not queue_id.startswith("amd_")
        ) or queue_id in seen_non_gating_ids:
            raise ValueError(
                f"{path} scope.non_gating_queues[{index}].id must be a unique amd_ queue"
            )
        if purpose not in {"docker_builds_only", "perf_eval"}:
            raise ValueError(
                f"{path} scope.non_gating_queues[{index}].purpose is unsupported"
            )
        if (
            not isinstance(max_concurrent_jobs, int)
            or isinstance(max_concurrent_jobs, bool)
            or max_concurrent_jobs < 0
        ):
            raise ValueError(
                f"{path} scope.non_gating_queues[{index}].max_concurrent_jobs "
                "must be a non-negative integer"
            )
        if (
            not isinstance(node_equivalents, (int, float))
            or isinstance(node_equivalents, bool)
            or node_equivalents < 0
        ):
            raise ValueError(
                f"{path} scope.non_gating_queues[{index}].node_equivalents "
                "must be a non-negative number"
            )
        seen_non_gating_ids.add(queue_id)
        normalized_non_gating.append({
            **raw,
            "id": queue_id,
            "purpose": purpose,
            "max_concurrent_jobs": max_concurrent_jobs,
            "node_equivalents": float(node_equivalents),
        })
    if "amd-cpu" not in seen_non_gating_ids:
        raise ValueError(f"{path} scope.non_gating_queues must include amd-cpu")
    scope = dict(scope)
    scope["non_gating_queues"] = normalized_non_gating

    raw_queues = payload.get("queues")
    if not isinstance(raw_queues, list) or not raw_queues:
        raise ValueError(f"{path} must contain a non-empty queues list")
    normalized_queues: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_queues):
        if not isinstance(raw, dict):
            raise ValueError(f"{path} queues[{index}] must be an object")
        queue_id = str(raw.get("id") or "").strip().lower()
        label = str(raw.get("label") or "").strip()
        family = str(raw.get("family") or "").strip().upper()
        provider = str(raw.get("provider") or "").strip()
        lifecycle = str(raw.get("lifecycle") or "").strip().lower()
        gpus_per_job = raw.get("gpus_per_job")
        max_concurrent_jobs = raw.get("max_concurrent_jobs")
        monitored = raw.get("monitored")
        capacity_eligible = raw.get("capacity_eligible")
        if not queue_id.startswith("amd_") or "perf_eval" in queue_id:
            raise ValueError(
                f"{path} queues[{index}].id must be a standard amd_ queue, not perf_eval"
            )
        if queue_id in seen_ids:
            raise ValueError(f"{path} has duplicate queue id {queue_id}")
        if not label or not family:
            raise ValueError(f"{path} queues[{index}] must have non-empty label and family")
        if (
            not isinstance(gpus_per_job, int)
            or isinstance(gpus_per_job, bool)
            or gpus_per_job not in {1, 2, 4, 8}
        ):
            raise ValueError(f"{path} queues[{index}].gpus_per_job must be 1, 2, 4, or 8")
        if (
            not isinstance(max_concurrent_jobs, int)
            or isinstance(max_concurrent_jobs, bool)
            or max_concurrent_jobs < 0
        ):
            raise ValueError(
                f"{path} queues[{index}].max_concurrent_jobs must be a non-negative integer"
            )
        if not isinstance(monitored, bool) or not isinstance(capacity_eligible, bool):
            raise ValueError(
                f"{path} queues[{index}] monitored and capacity_eligible must be booleans"
            )
        if lifecycle not in {"active", "retiring"}:
            raise ValueError(f"{path} queues[{index}].lifecycle must be active or retiring")
        if lifecycle == "retiring" and capacity_eligible:
            raise ValueError(f"{path} queues[{index}] cannot be retiring and capacity eligible")
        seen_ids.add(queue_id)
        normalized_queues.append(
            {
                "id": queue_id,
                "label": label,
                "family": family,
                "provider": provider or None,
                "gpus_per_job": gpus_per_job,
                "max_concurrent_jobs": max_concurrent_jobs,
                "monitored": monitored,
                "capacity_eligible": capacity_eligible,
                "lifecycle": lifecycle,
            }
        )

    normalized = dict(payload)
    normalized["scope"] = scope
    normalized["queues"] = normalized_queues
    return normalized


def _configured_queue_rows(config: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for queue in config["queues"]:
        if not queue["monitored"]:
            continue
        max_concurrent_jobs = int(queue["max_concurrent_jobs"])
        gpus_per_job = int(queue["gpus_per_job"])
        gpu_capacity = max_concurrent_jobs * gpus_per_job
        rows.append(
            {
                **queue,
                # Keep max_agents as a compatibility alias for the legacy queue
                # view while making the actual unit explicit in the new schema.
                "max_agents": max_concurrent_jobs,
                "gpu_capacity": gpu_capacity,
                "eight_gpu_node_equivalents": round(gpu_capacity / 8, 2),
            }
        )
    return tuple(rows)


CAPACITY_CONFIG = load_capacity_config()
PROJECTION_CONFIG = CAPACITY_CONFIG["projection"]
DEFAULT_THEORETICAL_GROUPS = int(PROJECTION_CONFIG["target_groups"])
CAPACITY_QUEUES = _configured_queue_rows(CAPACITY_CONFIG)
CAPACITY_BY_QUEUE = {row["id"]: row for row in CAPACITY_QUEUES}


def _github_headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _requested_config_ref(fallback: str = GITHUB_REF) -> str:
    """Return the workflow-pinned config ref, falling back to semantic main."""

    return os.getenv("VLLM_CONFIG_SHA", "").strip() or fallback


def _resolve_commit_sha(repo: str, requested_ref: str) -> str:
    """Resolve a branch or requested SHA to one immutable full commit SHA."""

    normalized_ref = requested_ref.strip().lower()
    if FULL_COMMIT_SHA_RE.fullmatch(normalized_ref):
        return normalized_ref
    response = requests.get(
        f"{GITHUB_API_BASE}/{repo}/commits/{requested_ref}",
        headers=_github_headers(),
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    commit_sha = str(
        payload.get("sha") if isinstance(payload, dict) else ""
    ).strip().lower()
    if not FULL_COMMIT_SHA_RE.fullmatch(commit_sha):
        raise ValueError(
            f"GitHub did not resolve {repo}@{requested_ref} to a full 40-hex SHA"
        )
    return commit_sha


def clean_text(value: Any) -> str:
    return MULTISPACE_RE.sub(" ", str(value or "").strip()).strip()


def slugify(value: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return out or "unknown"


def queue_from_device(device: str) -> str:
    normalized = clean_text(device).lower().replace("-", "_")
    if normalized.startswith("amd_"):
        return normalized
    return f"amd_{normalized}" if normalized else ""


def normalize_dependency_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    deps: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        dep = item.strip()
        if not dep or "$" in dep:
            continue
        deps.append(dep.lstrip("./"))
    return deps


def _path_within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _should_skip_file(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTS:
        return True
    return any(part in SKIP_DIRS for part in path.parts)


def _count_text_lines(path: Path) -> int:
    if _should_skip_file(path) or not path.is_file() or path.is_symlink():
        return 0
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    if b"\0" in data[:8192]:
        return 0
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _iter_dependency_files(repo_root: Path, dependency: str) -> Iterator[Path]:
    dep = dependency.strip().lstrip("/")
    if not dep:
        return
    path = repo_root / dep
    if not _path_within(repo_root, path):
        return
    if path.is_file():
        yield path
        return
    if not path.is_dir():
        return
    for child in path.rglob("*"):
        if not child.is_file() or child.is_symlink():
            continue
        rel_parts = child.relative_to(path).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        yield child


def dependency_scope(repo_root: Path, dependencies: list[str]) -> dict[str, Any]:
    files: dict[str, int] = {}
    missing: list[str] = []
    for dep in dependencies:
        before = len(files)
        for path in _iter_dependency_files(repo_root, dep):
            if _should_skip_file(path):
                continue
            rel = path.relative_to(repo_root).as_posix()
            if rel not in files:
                files[rel] = _count_text_lines(path)
        if len(files) == before and not (repo_root / dep).exists():
            missing.append(dep)
    return {
        "files": files,
        "file_count": len(files),
        "line_count": sum(files.values()),
        "missing_dependencies": missing,
    }


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_amd_mirror_groups(repo_root: Path) -> list[dict[str, Any]]:
    test_area_root = repo_root / TEST_AREAS_DIR
    groups: list[dict[str, Any]] = []
    for yaml_path in sorted(test_area_root.glob("*.y*ml")):
        try:
            parsed = yaml.safe_load(yaml_path.read_text()) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.warning("Skipping unreadable YAML %s: %s", yaml_path, exc)
            continue
        if not isinstance(parsed, dict):
            continue
        area = clean_text(parsed.get("group")) or yaml_path.stem.replace("_", " ").title()
        steps = parsed.get("steps") or []
        if not isinstance(steps, list):
            continue
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            mirror = step.get("mirror")
            if not isinstance(mirror, dict) or "amd" not in mirror:
                continue
            amd = mirror.get("amd") or {}
            if not isinstance(amd, dict):
                amd = {}
            label = (
                clean_text(step.get("label")) or clean_text(step.get("key")) or f"{area} #{idx + 1}"
            )
            key = clean_text(step.get("key")) or slugify(label)
            device = clean_text(amd.get("device") or step.get("device"))
            queue = queue_from_device(device)
            dependencies = normalize_dependency_list(
                amd.get("source_file_dependencies") or step.get("source_file_dependencies")
            )
            scope = dependency_scope(repo_root, dependencies)
            parallelism = max(1, _safe_int(amd.get("parallelism") or step.get("parallelism"), 1))
            timeout = _safe_int(amd.get("timeout_in_minutes") or step.get("timeout_in_minutes"), 0)
            groups.append(
                {
                    "key": key,
                    "label": label,
                    "area": area,
                    "yaml_file": yaml_path.relative_to(repo_root).as_posix(),
                    "yaml_index": idx,
                    "device": device,
                    "queue": queue,
                    "in_capacity_scope": queue in CAPACITY_BY_QUEUE,
                    "parallelism": parallelism,
                    "timeout_in_minutes": timeout,
                    "optional": bool(step.get("optional") or amd.get("optional")),
                    "source_file_dependencies": dependencies,
                    "dependency_file_count": scope["file_count"],
                    "dependency_lines": scope["line_count"],
                    "missing_dependencies": scope["missing_dependencies"],
                    "_dependency_files": sorted(scope["files"]),
                }
            )
    return groups


def _queue_rollups(groups: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rollups: dict[str, dict[str, Any]] = {}
    for queue in CAPACITY_QUEUES:
        rollups[queue["id"]] = {
            **queue,
            "gated_groups": 0,
            "gated_jobs": 0,
            "dependency_file_count": 0,
            "dependency_lines": 0,
            "max_group_dependency_lines": 0,
        }

    for group in groups:
        queue = group.get("queue")
        if queue not in rollups:
            continue
        rollup = rollups[queue]
        rollup["gated_groups"] += 1
        rollup["gated_jobs"] += int(group.get("parallelism") or 1)
        rollup["dependency_file_count"] += int(group.get("dependency_file_count") or 0)
        rollup["dependency_lines"] += int(group.get("dependency_lines") or 0)
        rollup["max_group_dependency_lines"] = max(
            rollup["max_group_dependency_lines"],
            int(group.get("dependency_lines") or 0),
        )

    return rollups


def _capacity_totals(rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, Any]:
    concurrent_jobs = sum(int(row.get("max_concurrent_jobs") or 0) for row in rows)
    gpus = sum(int(row.get("gpu_capacity") or 0) for row in rows)
    return {
        "queue_count": len(rows),
        "concurrent_jobs": concurrent_jobs,
        "gpus": gpus,
        "eight_gpu_node_equivalents": round(gpus / 8, 2),
    }


def _family_rollups(queue_rollups: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in queue_rollups.values():
        families[str(row["family"])].append(row)

    output: list[dict[str, Any]] = []
    for family, rows in families.items():
        eligible = [row for row in rows if row.get("capacity_eligible")]
        retiring = [row for row in rows if row.get("lifecycle") == "retiring"]
        monitored_capacity = _capacity_totals(rows)
        future_capacity = _capacity_totals(eligible)
        output.append(
            {
                "family": family,
                "lifecycle": "retiring" if len(retiring) == len(rows) else "active",
                "queue_count": len(rows),
                "capacity_eligible_queue_count": len(eligible),
                "max_concurrent_jobs": monitored_capacity["concurrent_jobs"],
                "gpu_capacity": monitored_capacity["gpus"],
                "eight_gpu_node_equivalents": monitored_capacity["eight_gpu_node_equivalents"],
                "future_max_concurrent_jobs": future_capacity["concurrent_jobs"],
                "future_gpu_capacity": future_capacity["gpus"],
                "future_eight_gpu_node_equivalents": future_capacity["eight_gpu_node_equivalents"],
                "gated_groups": sum(int(row.get("gated_groups") or 0) for row in rows),
                "gated_jobs": sum(int(row.get("gated_jobs") or 0) for row in rows),
                "gated_gpu_demand": sum(
                    int(row.get("gated_jobs") or 0) * int(row.get("gpus_per_job") or 0)
                    for row in rows
                ),
            }
        )
    return output


def _summary(
    groups: list[dict[str, Any]], queue_rollups: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    capacity_groups = [group for group in groups if group.get("in_capacity_scope")]
    all_dependency_files: dict[str, int] = {}
    for group in groups:
        # Dependency line totals are summed per group because that is the work
        # projection surface. Unique files are separately tracked to show code
        # coverage breadth without double-counting shared directories.
        for rel in group.get("_dependency_files") or group.get("dependency_files") or []:
            all_dependency_files.setdefault(rel, 0)

    total_dependency_lines = sum(int(group.get("dependency_lines") or 0) for group in groups)
    total_dependency_files = sum(int(group.get("dependency_file_count") or 0) for group in groups)
    gated_group_count = len(groups)
    monitored_rows = list(queue_rollups.values())
    eligible_rows = [row for row in monitored_rows if row.get("capacity_eligible")]
    retiring_rows = [row for row in monitored_rows if row.get("lifecycle") == "retiring"]
    monitored_capacity = _capacity_totals(monitored_rows)
    future_capacity = _capacity_totals(eligible_rows)
    retiring_capacity = _capacity_totals(retiring_rows)
    return {
        "queue_count": len(CAPACITY_QUEUES),
        "monitored_queue_count": len(monitored_rows),
        "capacity_eligible_queue_count": len(eligible_rows),
        # Legacy aliases retained for the current Capacity Monitor frontend.
        "total_capacity": monitored_capacity["concurrent_jobs"],
        "future_eligible_capacity": future_capacity["concurrent_jobs"],
        "total_gpu_capacity": monitored_capacity["gpus"],
        "future_eligible_gpu_capacity": future_capacity["gpus"],
        "total_eight_gpu_node_equivalents": monitored_capacity["eight_gpu_node_equivalents"],
        "future_eligible_eight_gpu_node_equivalents": future_capacity["eight_gpu_node_equivalents"],
        "capacity": {
            "monitored": monitored_capacity,
            "future_eligible": future_capacity,
            "retiring": retiring_capacity,
        },
        "gated_group_count": gated_group_count,
        "capacity_scoped_group_count": len(capacity_groups),
        "gated_job_count": sum(int(group.get("parallelism") or 1) for group in capacity_groups),
        "gated_gpu_demand": sum(
            int(group.get("parallelism") or 1)
            * int(CAPACITY_BY_QUEUE.get(str(group.get("queue")), {}).get("gpus_per_job") or 0)
            for group in capacity_groups
        ),
        "total_dependency_files": total_dependency_files,
        "total_dependency_lines": total_dependency_lines,
        "unique_dependency_files": len(all_dependency_files),
        "average_dependency_files_per_group": round(total_dependency_files / gated_group_count, 1)
        if gated_group_count
        else 0,
        "average_dependency_lines_per_group": round(total_dependency_lines / gated_group_count, 1)
        if gated_group_count
        else 0,
        "queues_with_gated_work": sum(
            1 for row in queue_rollups.values() if row["gated_groups"] > 0
        ),
    }


def _projection(
    summary: dict[str, Any],
    queue_rollups: dict[str, dict[str, Any]],
    theoretical_groups: int,
) -> dict[str, Any]:
    base_groups = max(
        1, int(summary.get("capacity_scoped_group_count") or summary.get("gated_group_count") or 1)
    )
    scale = theoretical_groups / base_groups
    queue_rows = []
    for queue_id, row in queue_rollups.items():
        max_concurrent_jobs = int(row.get("max_concurrent_jobs") or 0)
        gpus_per_job = int(row.get("gpus_per_job") or 0)
        capacity_eligible = bool(row.get("capacity_eligible"))
        future_max_concurrent_jobs = max_concurrent_jobs if capacity_eligible else 0
        raw_projected_jobs = float(row.get("gated_jobs") or 0) * scale
        projected_jobs = round(raw_projected_jobs, 1)
        projected_gpu_demand = round(raw_projected_jobs * gpus_per_job, 1)
        projected_lines = round(float(row.get("dependency_lines") or 0) * scale)
        current_ratio = (
            round(raw_projected_jobs / max_concurrent_jobs, 4)
            if max_concurrent_jobs
            else (None if raw_projected_jobs else 0)
        )
        future_ratio = current_ratio if capacity_eligible else (None if raw_projected_jobs else 0)
        projected_gap_jobs = round(
            max(0.0, raw_projected_jobs - future_max_concurrent_jobs),
            1,
        )
        queue_rows.append(
            {
                "id": queue_id,
                "label": row["label"],
                "family": row["family"],
                "gpus_per_job": gpus_per_job,
                "lifecycle": row["lifecycle"],
                "monitored": bool(row.get("monitored")),
                "capacity_eligible": capacity_eligible,
                "max_agents": max_concurrent_jobs,
                "max_concurrent_jobs": max_concurrent_jobs,
                "gpu_capacity": int(row.get("gpu_capacity") or 0),
                "eight_gpu_node_equivalents": float(row.get("eight_gpu_node_equivalents") or 0),
                "future_max_concurrent_jobs": future_max_concurrent_jobs,
                "future_gpu_capacity": future_max_concurrent_jobs * gpus_per_job,
                "projected_jobs": projected_jobs,
                "projected_gpu_demand": projected_gpu_demand,
                "projected_eight_gpu_node_equivalents": round(
                    projected_gpu_demand / 8,
                    2,
                ),
                "projected_dependency_lines": projected_lines,
                "projected_capacity_ratio": current_ratio,
                "projected_future_capacity_ratio": future_ratio,
                "projected_gap_jobs": projected_gap_jobs,
                "projected_gap_gpus": round(projected_gap_jobs * gpus_per_job, 1),
                "requires_migration": bool(not capacity_eligible and raw_projected_jobs > 0),
            }
        )
    eligible_demand_rows = [
        row for row in queue_rows if row["capacity_eligible"] and float(row["projected_jobs"]) > 0
    ]
    bottleneck = max(
        eligible_demand_rows,
        key=lambda row: float(row["projected_future_capacity_ratio"] or 0),
        default=None,
    )
    projected_total_jobs = round(sum(row["projected_jobs"] for row in queue_rows), 1)
    projected_total_gpus = round(sum(row["projected_gpu_demand"] for row in queue_rows), 1)
    monitored_capacity = summary["capacity"]["monitored"]
    future_capacity = summary["capacity"]["future_eligible"]
    total_capacity = int(monitored_capacity["concurrent_jobs"])
    future_concurrent_jobs = int(future_capacity["concurrent_jobs"])
    future_gpus = int(future_capacity["gpus"])
    configured_target = int(PROJECTION_CONFIG["target_groups"])
    declared_existing = int(PROJECTION_CONFIG["declared_existing_groups"])
    declared_new = int(PROJECTION_CONFIG["declared_new_groups"])
    return {
        "model": "linear_configured_parallelism_sensitivity",
        "target_groups": theoretical_groups,
        "theoretical_groups": theoretical_groups,
        "configured_target_groups": configured_target,
        "declared_current_mirror_groups": int(
            PROJECTION_CONFIG["declared_current_mirror_groups"]
        ),
        "declared_existing_groups": declared_existing,
        "declared_new_groups": declared_new,
        "declared_total_groups": declared_existing + declared_new,
        "planning_headroom_groups": configured_target - declared_existing - declared_new,
        "note": str(PROJECTION_CONFIG["note"]),
        "base_groups": base_groups,
        "scale": round(scale, 4),
        "projected_total_jobs": projected_total_jobs,
        "projected_total_gpus": projected_total_gpus,
        "projected_eight_gpu_node_equivalents": round(projected_total_gpus / 8, 2),
        "projected_dependency_lines": round(
            float(summary.get("total_dependency_lines") or 0) * scale
        ),
        "projected_capacity_ratio": round(projected_total_jobs / total_capacity, 4)
        if total_capacity
        else 0,
        "future_slot_capacity_ratio": (
            round(projected_total_jobs / future_concurrent_jobs, 4) if future_concurrent_jobs else 0
        ),
        "future_gpu_capacity_ratio": (
            round(projected_total_gpus / future_gpus, 4) if future_gpus else 0
        ),
        "future_capacity": future_capacity,
        "bottleneck_queue": bottleneck["id"] if bottleneck else "",
        "bottleneck_capacity_ratio": (
            bottleneck["projected_future_capacity_ratio"] if bottleneck else 0
        ),
        "queues_over_capacity": [
            row["id"]
            for row in queue_rows
            if row["capacity_eligible"] and float(row["projected_gap_jobs"]) > 0
        ],
        "queues_requiring_migration": [
            row["id"] for row in queue_rows if row["requires_migration"]
        ],
        "projected_gap_jobs": round(
            sum(float(row["projected_gap_jobs"]) for row in queue_rows),
            1,
        ),
        "projected_gap_gpus": round(
            sum(float(row["projected_gap_gpus"]) for row in queue_rows),
            1,
        ),
        "queues": queue_rows,
    }


def build_capacity_payload(
    repo_root: Path,
    *,
    source_kind: str = "local",
    github_repo: str = GITHUB_REPO,
    ref: str = GITHUB_REF,
    requested_ref: str | None = None,
    commit_sha: str = "",
    theoretical_groups: int | None = None,
) -> dict[str, Any]:
    groups = parse_amd_mirror_groups(repo_root)
    queue_rollups = _queue_rollups(groups)
    summary = _summary(groups, queue_rollups)
    theoretical = (
        DEFAULT_THEORETICAL_GROUPS if theoretical_groups is None else int(theoretical_groups)
    )
    if theoretical <= 0:
        raise ValueError("theoretical_groups must be positive")
    public_groups = []
    for group in groups:
        clean_group = {k: v for k, v in group.items() if not k.startswith("_")}
        public_groups.append(clean_group)
    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "kind": source_kind,
            "github_repo": github_repo,
            "ref": ref,
            "branch": GITHUB_REF,
            "requested_ref": requested_ref or ref,
            "commit_sha": commit_sha,
            "commit_url": (
                f"https://github.com/{github_repo}/commit/{commit_sha}"
                if commit_sha
                else ""
            ),
            "test_areas_path": TEST_AREAS_DIR,
            "capacity_config_path": CAPACITY_CONFIG_PATH.relative_to(ROOT).as_posix(),
            "capacity_config_schema_version": CAPACITY_CONFIG["schema_version"],
        },
        "scope": {
            **CAPACITY_CONFIG["scope"],
            "monitored_queues": [row["id"] for row in CAPACITY_QUEUES],
            "workload_pipelines": CAPACITY_CONFIG["workload_pipelines"],
        },
        "assumptions": {
            "capacity_basis": (
                "User-supplied maximum concurrent jobs for standard AMD queues. "
                "perf_eval queues are excluded, amd-cpu is reserved for Docker builds "
                "and excluded, and MI325 is monitored but excluded from future eligible "
                "capacity because it is retiring."
            ),
            "projection_model": (
                "Linear sensitivity scaling of current mirror.amd group count, "
                "configured parallelism, GPU demand, and source dependency scope. "
                "It is a planning scenario, not a forecast of simultaneous demand."
            ),
            "default_theoretical_groups": theoretical,
            "configured_target_groups": DEFAULT_THEORETICAL_GROUPS,
        },
        "summary": summary,
        "families": _family_rollups(queue_rollups),
        "queues": list(queue_rollups.values()),
        "groups": sorted(
            public_groups, key=lambda g: (g["yaml_file"], g["yaml_index"], g["label"].lower())
        ),
        "projection": _projection(summary, queue_rollups, theoretical),
    }


def bounded_capacity_payload(
    payload: dict[str, Any],
    *,
    max_bytes: int = CAPACITY_MONITOR_MAX_BYTES,
) -> dict[str, Any]:
    """Bound the publication while preserving exact aggregate capacity totals.

    Full group rows are preferred. Under byte pressure, refetchable dependency
    path lists are removed first; only then are whole group-index rows omitted.
    The summary, queue/family rollups, and projection remain exact for the full
    source definition set, and retention metadata distinguishes those aggregates
    from the bounded group-detail index.
    """
    source_groups = sorted(
        (dict(row) for row in payload.get("groups") or [] if isinstance(row, dict)),
        key=lambda row: (
            str(row.get("yaml_file") or ""),
            int(row.get("yaml_index") or 0),
            str(row.get("key") or ""),
        ),
    )

    def candidate(*, published_count: int, compact_details: bool) -> dict[str, Any]:
        rows = source_groups[:published_count]
        if compact_details:
            rows = [
                {key: row.get(key) for key in CAPACITY_GROUP_INDEX_FIELDS if key in row}
                for row in rows
            ]
        result = dict(payload)
        result["groups"] = rows
        result["publication_retention"] = {
            "policy": "aggregate_first_deterministic_group_index_v1",
            "max_bytes": max_bytes,
            "aggregate_summaries_complete": True,
            "group_index": {
                "source": len(source_groups),
                "published": len(rows),
                "omitted": len(source_groups) - len(rows),
                "complete_relative_to_source": len(rows) == len(source_groups),
            },
            "group_details": {
                "source": len(source_groups),
                "published_full": 0 if compact_details else len(rows),
                "compacted": len(rows) if compact_details else 0,
                "complete_relative_to_source": (
                    not compact_details and len(rows) == len(source_groups)
                ),
            },
            "complete_relative_to_source": (
                not compact_details and len(rows) == len(source_groups)
            ),
        }
        return result

    complete = candidate(
        published_count=len(source_groups),
        compact_details=False,
    )
    if len(pretty_json_bytes(complete)) <= max_bytes:
        return complete

    compact = candidate(
        published_count=len(source_groups),
        compact_details=True,
    )
    if len(pretty_json_bytes(compact)) <= max_bytes:
        return compact

    low = 0
    high = len(source_groups)
    best: dict[str, Any] | None = None
    while low <= high:
        middle = (low + high) // 2
        current = candidate(published_count=middle, compact_details=True)
        if len(pretty_json_bytes(current)) <= max_bytes:
            best = current
            low = middle + 1
        else:
            high = middle - 1
    if best is None:
        irreducible = candidate(published_count=0, compact_details=True)
        raise RuntimeError(
            "capacity monitor fixed aggregates exceed their byte budget; preserving "
            "the last-known-good file: "
            f"{len(pretty_json_bytes(irreducible))} > {max_bytes} bytes"
        )
    return best


def _candidate_repo_roots(explicit: str | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_root = os.getenv("VLLM_REPO_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root))
    candidates.extend(
        [
            ROOT.parent / "vllm",
            Path("/app/vllm"),
        ]
    )
    out: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            out.append(path)
            seen.add(key)
    return out


def _looks_like_vllm_repo(path: Path) -> bool:
    return (path / TEST_AREAS_DIR).is_dir()


def _archive_url(repo: str, ref: str) -> str:
    return f"https://codeload.github.com/{repo}/tar.gz/{ref}"


def _extract_archive(content: bytes, destination: Path) -> Path:
    tarfile_module = __import__("tarfile")
    with tarfile_module.open(fileobj=BytesIO(content), mode="r:gz") as archive:
        for member in archive.getmembers():
            parts = PurePosixPath(member.name).parts
            if len(parts) < 2:
                continue
            rel = Path(*parts[1:])
            target = destination / rel
            if not _path_within(destination, target):
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                src = archive.extractfile(member)
                if src is not None:
                    target.write_bytes(src.read())
    return destination


@contextmanager
def repo_root_context(
    explicit_repo_root: str | None,
    *,
    github_repo: str,
    ref: str,
) -> Iterator[tuple[Path, str, str]]:
    requested_ref = _requested_config_ref(ref)
    pinned_by_workflow = bool(os.getenv("VLLM_CONFIG_SHA", "").strip())
    if not pinned_by_workflow and requested_ref == GITHUB_REF:
        for candidate in _candidate_repo_roots(explicit_repo_root):
            if _looks_like_vllm_repo(candidate):
                yield candidate.resolve(), "local", ""
                return

    with tempfile.TemporaryDirectory(prefix="vllm-capacity-") as tmp:
        commit_sha = _resolve_commit_sha(github_repo, requested_ref)
        url = _archive_url(github_repo, commit_sha)
        log.info("Fetching %s", url)
        resp = requests.get(url, headers=_github_headers(), timeout=60)
        resp.raise_for_status()
        repo_root = _extract_archive(resp.content, Path(tmp) / "repo")
        if not _looks_like_vllm_repo(repo_root):
            raise RuntimeError(f"Downloaded archive did not contain {TEST_AREAS_DIR}")
        yield repo_root, "github_archive", commit_sha


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect queue capacity monitor data")
    parser.add_argument("--output", type=str, default=str(OUTPUT))
    parser.add_argument("--repo-root", type=str, default=None)
    parser.add_argument("--github-repo", type=str, default=GITHUB_REPO)
    parser.add_argument("--ref", type=str, default=GITHUB_REF)
    parser.add_argument("--theoretical-groups", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    requested_ref = _requested_config_ref(args.ref)

    with repo_root_context(
        args.repo_root,
        github_repo=args.github_repo,
        ref=requested_ref,
    ) as (repo_root, source_kind, commit_sha):
        payload = build_capacity_payload(
            repo_root,
            source_kind=source_kind,
            github_repo=args.github_repo,
            ref=GITHUB_REF,
            requested_ref=requested_ref,
            commit_sha=commit_sha,
            theoretical_groups=args.theoretical_groups,
        )

    out_path = output / "capacity_monitor.json"
    published = bounded_capacity_payload(
        payload,
        max_bytes=CAPACITY_MONITOR_MAX_BYTES,
    )
    write_pretty_json_lkg(
        out_path,
        published,
        max_bytes=CAPACITY_MONITOR_MAX_BYTES,
        label="capacity monitor snapshot",
    )
    log.info(
        "Wrote %s with %d AMD mirror groups across %d capacity queues",
        out_path,
        payload["summary"]["gated_group_count"],
        payload["summary"]["queue_count"],
    )


if __name__ == "__main__":
    main()
