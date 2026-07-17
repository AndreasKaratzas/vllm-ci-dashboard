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
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

import requests
import yaml


log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT = ROOT / "data" / "vllm" / "ci"

GITHUB_REPO = "vllm-project/vllm"
GITHUB_REF = "main"
TEST_AREAS_DIR = ".buildkite/test_areas"
DEFAULT_THEORETICAL_GROUPS = 125

# Capacity rows from the Buildkite cluster screenshot. Keep this list scoped to
# AMD queues owned by this dashboard view; other queues remain visible in the
# live Queue Monitor but are intentionally excluded from capacity projections.
CAPACITY_QUEUES: tuple[dict[str, Any], ...] = (
    {"id": "amd_mi250_1", "label": "mi250_1", "max_agents": 78},
    {"id": "amd_mi250_2", "label": "mi250_2", "max_agents": 24},
    {"id": "amd_mi250_4", "label": "mi250_4", "max_agents": 16},
    {"id": "amd_mi300_1", "label": "mi300_1", "max_agents": 264},
    {"id": "amd_mi300_2", "label": "mi300_2", "max_agents": 40},
    {"id": "amd_mi300_4", "label": "mi300_4", "max_agents": 30},
    {"id": "amd_mi300_8", "label": "mi300_8", "max_agents": 3},
    {"id": "amd_mi325_1", "label": "mi325_1", "max_agents": 180},
    {"id": "amd_mi325_2", "label": "mi325_2", "max_agents": 8},
    {"id": "amd_mi325_4", "label": "mi325_4", "max_agents": 4},
    {"id": "amd_mi325_8", "label": "mi325_8", "max_agents": 1},
    {"id": "amd_mi355_1", "label": "mi355_1", "max_agents": 39},
    {"id": "amd_mi355_2", "label": "mi355_2", "max_agents": 20},
    {"id": "amd_mi355_4", "label": "mi355_4", "max_agents": 3},
    {"id": "amd_mi355_8", "label": "mi355_8", "max_agents": 1},
)
CAPACITY_BY_QUEUE = {row["id"]: row for row in CAPACITY_QUEUES}

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


def _github_headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


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
            label = clean_text(step.get("label")) or clean_text(step.get("key")) or f"{area} #{idx + 1}"
            key = clean_text(step.get("key")) or slugify(label)
            device = clean_text(amd.get("device") or step.get("device"))
            queue = queue_from_device(device)
            dependencies = normalize_dependency_list(
                amd.get("source_file_dependencies") or step.get("source_file_dependencies")
            )
            scope = dependency_scope(repo_root, dependencies)
            parallelism = max(1, _safe_int(amd.get("parallelism") or step.get("parallelism"), 1))
            timeout = _safe_int(amd.get("timeout_in_minutes") or step.get("timeout_in_minutes"), 0)
            groups.append({
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
            })
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


def _summary(groups: list[dict[str, Any]], queue_rollups: dict[str, dict[str, Any]]) -> dict[str, Any]:
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
    return {
        "queue_count": len(CAPACITY_QUEUES),
        "total_capacity": sum(int(q["max_agents"]) for q in CAPACITY_QUEUES),
        "gated_group_count": gated_group_count,
        "capacity_scoped_group_count": len(capacity_groups),
        "gated_job_count": sum(int(group.get("parallelism") or 1) for group in capacity_groups),
        "total_dependency_files": total_dependency_files,
        "total_dependency_lines": total_dependency_lines,
        "unique_dependency_files": len(all_dependency_files),
        "average_dependency_files_per_group": round(total_dependency_files / gated_group_count, 1)
        if gated_group_count else 0,
        "average_dependency_lines_per_group": round(total_dependency_lines / gated_group_count, 1)
        if gated_group_count else 0,
        "queues_with_gated_work": sum(1 for row in queue_rollups.values() if row["gated_groups"] > 0),
    }


def _projection(
    summary: dict[str, Any],
    queue_rollups: dict[str, dict[str, Any]],
    theoretical_groups: int,
) -> dict[str, Any]:
    base_groups = max(1, int(summary.get("capacity_scoped_group_count") or summary.get("gated_group_count") or 1))
    scale = theoretical_groups / base_groups
    queue_rows = []
    for queue_id, row in queue_rollups.items():
        max_agents = int(row.get("max_agents") or 0)
        projected_jobs = round(float(row.get("gated_jobs") or 0) * scale, 1)
        projected_lines = round(float(row.get("dependency_lines") or 0) * scale)
        queue_rows.append({
            "id": queue_id,
            "label": row["label"],
            "max_agents": max_agents,
            "projected_jobs": projected_jobs,
            "projected_dependency_lines": projected_lines,
            "projected_capacity_ratio": round(projected_jobs / max_agents, 4) if max_agents else 0,
        })
    bottleneck = max(queue_rows, key=lambda row: row["projected_capacity_ratio"], default=None)
    projected_total_jobs = round(sum(row["projected_jobs"] for row in queue_rows), 1)
    total_capacity = int(summary.get("total_capacity") or 0)
    return {
        "theoretical_groups": theoretical_groups,
        "scale": round(scale, 4),
        "projected_total_jobs": projected_total_jobs,
        "projected_dependency_lines": round(float(summary.get("total_dependency_lines") or 0) * scale),
        "projected_capacity_ratio": round(projected_total_jobs / total_capacity, 4) if total_capacity else 0,
        "bottleneck_queue": bottleneck["id"] if bottleneck else "",
        "queues": queue_rows,
    }


def build_capacity_payload(
    repo_root: Path,
    *,
    source_kind: str = "local",
    github_repo: str = GITHUB_REPO,
    ref: str = GITHUB_REF,
    theoretical_groups: int | None = None,
) -> dict[str, Any]:
    groups = parse_amd_mirror_groups(repo_root)
    queue_rollups = _queue_rollups(groups)
    summary = _summary(groups, queue_rollups)
    theoretical = theoretical_groups or DEFAULT_THEORETICAL_GROUPS
    public_groups = []
    for group in groups:
        clean_group = {k: v for k, v in group.items() if not k.startswith("_")}
        public_groups.append(clean_group)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "kind": source_kind,
            "github_repo": github_repo,
            "ref": ref,
            "test_areas_path": TEST_AREAS_DIR,
        },
        "assumptions": {
            "capacity_basis": "Buildkite connected-agent peak capacity from the AMD queue screenshot",
            "projection_model": (
                "Naive scale-up of current mirror.amd group count, configured "
                "parallelism, and source_file_dependencies line scope. The "
                "frontend also scales observed queue peaks from the live "
                "queue history for pressure estimates."
            ),
            "default_theoretical_groups": theoretical,
        },
        "summary": summary,
        "queues": list(queue_rollups.values()),
        "groups": sorted(public_groups, key=lambda g: (g["yaml_file"], g["yaml_index"], g["label"].lower())),
        "projection": _projection(summary, queue_rollups, theoretical),
    }


def _candidate_repo_roots(explicit: str | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_root = os.getenv("VLLM_REPO_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root))
    candidates.extend([
        ROOT.parent / "vllm",
        Path("/app/vllm"),
    ])
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
) -> Iterator[tuple[Path, str]]:
    for candidate in _candidate_repo_roots(explicit_repo_root):
        if _looks_like_vllm_repo(candidate):
            yield candidate.resolve(), "local"
            return

    with tempfile.TemporaryDirectory(prefix="vllm-capacity-") as tmp:
        url = _archive_url(github_repo, ref)
        log.info("Fetching %s", url)
        resp = requests.get(url, headers=_github_headers(), timeout=60)
        resp.raise_for_status()
        repo_root = _extract_archive(resp.content, Path(tmp) / "repo")
        if not _looks_like_vllm_repo(repo_root):
            raise RuntimeError(f"Downloaded archive did not contain {TEST_AREAS_DIR}")
        yield repo_root, "github_archive"


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

    with repo_root_context(
        args.repo_root,
        github_repo=args.github_repo,
        ref=args.ref,
    ) as (repo_root, source_kind):
        payload = build_capacity_payload(
            repo_root,
            source_kind=source_kind,
            github_repo=args.github_repo,
            ref=args.ref,
            theoretical_groups=args.theoretical_groups,
        )

    out_path = output / "capacity_monitor.json"
    out_path.write_text(json.dumps(payload, indent=2))
    log.info(
        "Wrote %s with %d AMD mirror groups across %d capacity queues",
        out_path,
        payload["summary"]["gated_group_count"],
        payload["summary"]["queue_count"],
    )


if __name__ == "__main__":
    main()
