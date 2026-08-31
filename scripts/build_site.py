#!/usr/bin/env python3
"""Assemble the published static site from the shell in docs/ and JSON in data/."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from vllm.build_operations_snapshot import write_snapshot_bundle
from vllm.ci.public_analytics import (
    PUBLIC_ANALYTICS_PROJECTOR_ID,
    compact_public_analytics_json,
    project_public_analytics,
)


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA = ROOT / "data"
PUBLIC_DATA_MANIFEST = ROOT / "config" / "public_data_manifest.json"
CACHE_BUST_RE = re.compile(r"\?v=\d+")
PUBLICATION_STATE_INPUT = "vllm/ci/publication_state.json"
PUBLICATION_STATUS_OUTPUT = "vllm/ci/publication_status.json"
SITE_FILE_MAX_BYTES = 85 * 1024 * 1024
SITE_TOTAL_MAX_BYTES = 384 * 1024 * 1024
SITE_MAX_FILES = 10_000
PROJECTOR_SERIALIZERS: dict[str, Callable[[object], str]] = {
    PUBLIC_ANALYTICS_PROJECTOR_ID: compact_public_analytics_json,
}
PUBLICATION_MODES = frozenset({"current", "degraded", "fallback", "mixed", "blocked"})
PUBLICATION_SURFACE_LABELS = {
    "agent_health": "Agent health",
    "ci": "CI health",
    "ci_changes": "CI test changes",
    "ci_core": "CI core health",
    "ci_gating": "CI gating",
    "ci_hotness": "CI workload hotness",
    "dns_health": "DNS health",
    "github_home": "Project activity",
    "perf_eval": "Performance evaluation",
    "queue": "Queue health",
    "queue_lifecycle": "Queue lifecycle",
}


def copy_tree_contents(src: Path, dest: Path) -> None:
    """Copy a publication tree without following symlinks or special files."""
    if not src.is_dir() or src.is_symlink():
        raise ValueError(f"Site shell source must be a real directory: {src}")
    dest.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dest / child.name
        if child.is_symlink():
            raise ValueError(f"Site shell source cannot contain symlinks: {child}")
        if child.is_dir():
            copy_tree_contents(child, target)
        elif child.is_file():
            shutil.copy2(child, target)
        else:
            raise ValueError(f"Site shell source must be a regular file: {child}")


def cache_bust_index(index_path: Path, stamp: str) -> None:
    if not index_path.exists():
        return
    text = index_path.read_text()
    updated = CACHE_BUST_RE.sub(f"?v={stamp}", text)
    index_path.write_text(updated)


def _safe_manifest_path(value: object, field: str, *, glob: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} entries must be non-empty strings")
    if "\\" in value:
        raise ValueError(f"{field} entry must use POSIX separators: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"{field} entry must stay below data/: {value!r}")
    if not glob and any(char in value for char in "*?["):
        raise ValueError(f"{field} entries cannot contain globs: {value!r}")
    return path.as_posix()


def load_public_data_manifest(path: Path = PUBLIC_DATA_MANIFEST) -> dict:
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != 2:
        raise ValueError(f"Unsupported public data manifest schema in {path}")

    normalized = dict(payload)
    for field in (
        "required_files",
        "optional_files",
        "build_inputs",
        "generated_files",
    ):
        values = payload.get(field)
        if not isinstance(values, list):
            raise ValueError(f"{field} must be a list in {path}")
        normalized[field] = [
            _safe_manifest_path(value, field)
            for value in values
        ]
        if len(normalized[field]) != len(set(normalized[field])):
            raise ValueError(f"{field} contains duplicate paths in {path}")

    for field in ("optional_globs", "never_publish_patterns"):
        values = payload.get(field)
        if not isinstance(values, list):
            raise ValueError(f"{field} must be a list in {path}")
        normalized[field] = [
            _safe_manifest_path(value, field, glob=True)
            for value in values
        ]

    descriptors = payload.get("projected_files")
    if not isinstance(descriptors, list):
        raise ValueError(f"projected_files must be a list in {path}")
    normalized_descriptors = []
    for index, descriptor in enumerate(descriptors):
        field = f"projected_files[{index}]"
        if not isinstance(descriptor, dict):
            raise ValueError(f"{field} must be an object in {path}")
        expected_keys = {"path", "projector", "max_bytes"}
        if set(descriptor) != expected_keys:
            raise ValueError(
                f"{field} must contain exactly {sorted(expected_keys)} in {path}"
            )
        relative = _safe_manifest_path(descriptor.get("path"), f"{field}.path")
        projector = descriptor.get("projector")
        if not isinstance(projector, str) or projector not in PROJECTOR_SERIALIZERS:
            raise ValueError(f"{field} names an unknown projector in {path}: {projector!r}")
        max_bytes = descriptor.get("max_bytes")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError(f"{field}.max_bytes must be a positive integer in {path}")
        normalized_descriptors.append({
            "path": relative,
            "projector": projector,
            "max_bytes": max_bytes,
        })
    projected_paths = [descriptor["path"] for descriptor in normalized_descriptors]
    if len(projected_paths) != len(set(projected_paths)):
        raise ValueError(f"projected_files contains duplicate paths in {path}")
    normalized["projected_files"] = normalized_descriptors

    public_exact_paths = (
        normalized["required_files"]
        + normalized["optional_files"]
        + normalized["generated_files"]
    )
    build_inputs = set(normalized["build_inputs"])
    overlap = build_inputs & set(public_exact_paths)
    if overlap:
        raise ValueError(
            f"Build inputs cannot also be public outputs in {path}: {sorted(overlap)}"
        )
    undeclared_inputs = sorted(set(projected_paths) - build_inputs)
    if undeclared_inputs:
        raise ValueError(
            "Projected files must be declared as build inputs in "
            f"{path}: {undeclared_inputs}"
        )
    projected_direct_overlap = sorted(set(projected_paths) & set(public_exact_paths))
    if projected_direct_overlap:
        raise ValueError(
            "Projected files cannot also be direct public outputs in "
            f"{path}: {projected_direct_overlap}"
        )
    projected_glob_overlap = sorted(
        relative
        for relative in projected_paths
        if any(
            PurePosixPath(relative).match(pattern)
            for pattern in normalized["optional_globs"]
        )
    )
    if projected_glob_overlap:
        raise ValueError(
            "Projected files cannot also match direct public globs in "
            f"{path}: {projected_glob_overlap}"
        )
    blocked = [
        value
        for value in public_exact_paths + projected_paths
        if any(
            PurePosixPath(value).match(pattern)
            for pattern in normalized["never_publish_patterns"]
        )
    ]
    if blocked:
        raise ValueError(f"Public data manifest allowlists blocked paths: {blocked}")
    return normalized


def _copy_public_file(source_root: Path, dest_root: Path, relative: str) -> bool:
    source = source_root / relative
    if not source.exists():
        return False
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"Public data source must be a regular file: {source}")
    try:
        source.resolve().relative_to(source_root.resolve())
    except ValueError as exc:
        raise ValueError(f"Public data source escapes data/: {source}") from exc
    target = dest_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def copy_public_data(
    source_root: Path,
    dest_root: Path,
    manifest: dict,
) -> set[str]:
    """Copy the explicit public allowlist and return the paths that were copied."""
    copied: set[str] = set()
    missing: list[str] = []
    for relative in manifest["required_files"]:
        if _copy_public_file(source_root, dest_root, relative):
            copied.add(relative)
        else:
            missing.append(relative)
    if missing:
        raise FileNotFoundError(f"Required public data files are missing: {missing}")

    for relative in manifest["optional_files"]:
        if _copy_public_file(source_root, dest_root, relative):
            copied.add(relative)

    for pattern in manifest["optional_globs"]:
        for source in sorted(source_root.glob(pattern)):
            relative = source.relative_to(source_root).as_posix()
            if _copy_public_file(source_root, dest_root, relative):
                copied.add(relative)
    return copied


def materialize_operations_bundle(
    source_data: Path,
    site_data: Path,
    manifest: dict,
) -> None:
    relative = "vllm/ci/operations_v2.json"
    if relative not in manifest["build_inputs"]:
        raise RuntimeError(f"Operations source is not declared as a build input: {relative}")
    source = source_data / relative
    if not source.is_file() or source.is_symlink():
        raise FileNotFoundError(f"Operations build input is missing or unsafe: {source}")
    payload = json.loads(source.read_text())
    output = site_data / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    write_snapshot_bundle(output, payload, write_monolith=False, log=False)


def materialize_projected_files(
    source_data: Path,
    site_data: Path,
    manifest: dict,
) -> set[str]:
    """Project private build inputs into bounded same-path public outputs."""
    projected: set[str] = set()
    for descriptor in manifest["projected_files"]:
        relative = descriptor["path"]
        source = source_data / relative
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(
                f"Projected build input is missing or unsafe: {source}"
            )
        try:
            source.resolve().relative_to(source_data.resolve())
        except ValueError as exc:
            raise ValueError(
                f"Projected build input escapes data/: {source}"
            ) from exc

        payload = json.loads(source.read_text())
        encoded = PROJECTOR_SERIALIZERS[descriptor["projector"]](payload).encode()
        if len(encoded) > descriptor["max_bytes"]:
            raise RuntimeError(
                f"Projected public file {relative} is {len(encoded)} bytes; "
                f"limit is {descriptor['max_bytes']} bytes"
            )

        output = site_data / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(encoded)
        projected.add(relative)
    return projected


def _validated_public_timestamp(value: object) -> tuple[str | None, datetime | None]:
    """Return an innocuous, timezone-aware ISO timestamp or no public value."""
    if not isinstance(value, str) or not value or len(value) > 64:
        return None, None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, None
    if parsed.tzinfo is None:
        return None, None
    canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return canonical, parsed


def _safe_surface_labels(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({
        PUBLICATION_SURFACE_LABELS[surface]
        for surface in value
        if isinstance(surface, str) and surface in PUBLICATION_SURFACE_LABELS
    })


def project_publication_status(publication_state: object) -> dict:
    """Create the small public status projection from private selector state.

    Selector findings, repository refs, restored-file manifests, hashes, and
    paths are intentionally ignored. Only fixed enums, validated timestamps,
    and labels from the local surface allowlist can cross this boundary.
    """
    if not isinstance(publication_state, dict):
        raise ValueError("Publication state must be a JSON object")
    mode = publication_state.get("mode")
    if mode not in PUBLICATION_MODES:
        raise ValueError(f"Unsupported publication mode: {mode!r}")

    affected_labels = sorted(set(
        _safe_surface_labels(publication_state.get("degraded_surfaces"))
        + _safe_surface_labels(publication_state.get("fresh_degraded_surfaces"))
        + _safe_surface_labels(publication_state.get("fallback_surfaces"))
    ))
    fallback_labels = _safe_surface_labels(publication_state.get("fallback_surfaces"))
    if mode == "fallback" and not fallback_labels:
        fallback_labels = affected_labels
    fresh_labels = _safe_surface_labels(
        publication_state.get("fresh_degraded_surfaces")
    )
    if mode == "degraded" and not fresh_labels:
        fresh_labels = affected_labels

    generated_at, _ = _validated_public_timestamp(
        publication_state.get("generated_at")
    )
    degraded_candidates: list[tuple[datetime, str]] = []
    degraded_since = publication_state.get("degraded_since")
    if isinstance(degraded_since, dict):
        for surface, value in degraded_since.items():
            if surface not in PUBLICATION_SURFACE_LABELS:
                continue
            public_value, parsed = _validated_public_timestamp(value)
            if public_value is not None and parsed is not None:
                degraded_candidates.append((parsed, public_value))

    status = "healthy"
    if mode == "blocked":
        status = "blocked"
    elif mode != "current" or affected_labels:
        status = "degraded"

    return {
        "schema_version": 1,
        "status": status,
        "mode": mode,
        "generated_at": generated_at,
        "degraded_since": (
            min(degraded_candidates, key=lambda item: item[0])[1]
            if degraded_candidates
            else None
        ),
        "uses_fallback": mode in {"fallback", "mixed"},
        "publication_blocked": mode == "blocked",
        "affected_surfaces": affected_labels,
        "affected_surface_count": len(affected_labels),
        "fallback_surface_count": len(fallback_labels),
        "fresh_degraded_surface_count": len(fresh_labels),
    }


def materialize_publication_status(
    source_data: Path,
    site_data: Path,
    manifest: dict,
) -> None:
    if PUBLICATION_STATE_INPUT not in manifest["build_inputs"]:
        raise RuntimeError(
            "Publication state is not declared as a build input: "
            f"{PUBLICATION_STATE_INPUT}"
        )
    if PUBLICATION_STATUS_OUTPUT not in manifest["generated_files"]:
        raise RuntimeError(
            "Public publication status is not declared as a generated file: "
            f"{PUBLICATION_STATUS_OUTPUT}"
        )

    source = source_data / PUBLICATION_STATE_INPUT
    if not source.is_file() or source.is_symlink():
        raise FileNotFoundError(
            f"Publication-state build input is missing or unsafe: {source}"
        )
    try:
        source.resolve().relative_to(source_data.resolve())
    except ValueError as exc:
        raise ValueError(
            f"Publication-state build input escapes data/: {source}"
        ) from exc

    payload = project_publication_status(json.loads(source.read_text()))
    output = site_data / PUBLICATION_STATUS_OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def validate_public_data(
    site_data: Path,
    copied: set[str],
    manifest: dict,
) -> None:
    """Fail closed if assembly emits anything outside the publication contract."""
    generated = set(manifest["generated_files"])
    projected = {
        descriptor["path"]
        for descriptor in manifest["projected_files"]
    }
    published = {
        path.relative_to(site_data).as_posix()
        for path in site_data.rglob("*")
        if path.is_file()
    }
    missing_materialized = sorted((generated | projected) - published)
    if missing_materialized:
        raise RuntimeError(
            "Site assembly did not materialize declared public files: "
            f"{missing_materialized}"
        )

    unexpected = sorted(published - copied - generated - projected)
    if unexpected:
        raise RuntimeError(f"Site assembly emitted non-public data files: {unexpected}")

    blocked = sorted(
        relative
        for relative in published
        if any(
            PurePosixPath(relative).match(pattern)
            for pattern in manifest["never_publish_patterns"]
        )
    )
    if blocked:
        raise RuntimeError(f"Site assembly emitted blocked data files: {blocked}")


def validate_site_file_sizes(
    output_dir: Path,
    *,
    max_bytes: int = SITE_FILE_MAX_BYTES,
    max_total_bytes: int = SITE_TOTAL_MAX_BYTES,
    max_files: int = SITE_MAX_FILES,
) -> None:
    """Reject an oversized or pathological tree before Pages can commit it."""
    if max_bytes <= 0 or max_total_bytes <= 0 or max_files <= 0:
        raise ValueError("site size and file-count limits must be positive")
    files = [
        path
        for path in output_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]
    if len(files) > max_files:
        raise RuntimeError(
            f"Site assembly has {len(files)} files (max {max_files})"
        )
    total_bytes = sum(path.stat().st_size for path in files)
    if total_bytes > max_total_bytes:
        raise RuntimeError(
            "Site assembly exceeds the aggregate publication budget: "
            f"{total_bytes} bytes (max {max_total_bytes})"
        )
    oversized = sorted(
        (
            (path.relative_to(output_dir).as_posix(), path.stat().st_size)
            for path in files
            if path.stat().st_size > max_bytes
        ),
        key=lambda row: (-row[1], row[0]),
    )
    if oversized:
        details = ", ".join(f"{path} ({size} bytes)" for path, size in oversized)
        raise RuntimeError(
            f"Site assembly exceeds the {max_bytes}-byte per-file budget: {details}"
        )


def build_site(output_dir: Path, cache_bust: bool) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    copy_tree_contents(DOCS, output_dir)
    manifest = load_public_data_manifest(PUBLIC_DATA_MANIFEST)
    copied = copy_public_data(DATA, output_dir / "data", manifest)
    materialize_projected_files(DATA, output_dir / "data", manifest)
    materialize_operations_bundle(DATA, output_dir / "data", manifest)
    materialize_publication_status(DATA, output_dir / "data", manifest)
    validate_public_data(output_dir / "data", copied, manifest)
    (output_dir / ".nojekyll").write_text("")
    if cache_bust:
        cache_bust_index(output_dir / "index.html", str(int(time.time())))
    validate_site_file_sizes(output_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assemble the GitHub Pages site from docs/ and data/."
    )
    parser.add_argument(
        "--output",
        default="_site",
        help="Output directory relative to repo root (default: _site)",
    )
    parser.add_argument(
        "--cache-bust-index",
        action="store_true",
        help="Rewrite ?v=... asset query strings in index.html with the current Unix timestamp.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_site(ROOT / args.output, cache_bust=args.cache_bust_index)


if __name__ == "__main__":
    main()
