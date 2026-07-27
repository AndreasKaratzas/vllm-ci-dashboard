#!/usr/bin/env python3
"""Assemble the published static site from the shell in docs/ and JSON in data/."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from pathlib import Path, PurePosixPath

from vllm.build_operations_snapshot import write_snapshot_bundle


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA = ROOT / "data"
PUBLIC_DATA_MANIFEST = ROOT / "config" / "public_data_manifest.json"
CACHE_BUST_RE = re.compile(r"\?v=\d+")


def copy_tree_contents(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dest / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)


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
    if payload.get("schema_version") != 1:
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
    blocked = [
        value
        for value in public_exact_paths
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


def validate_public_data(
    site_data: Path,
    copied: set[str],
    manifest: dict,
) -> None:
    """Fail closed if assembly emits anything outside the publication contract."""
    generated = set(manifest["generated_files"])
    published = {
        path.relative_to(site_data).as_posix()
        for path in site_data.rglob("*")
        if path.is_file()
    }
    missing_generated = sorted(generated - published)
    if missing_generated:
        raise RuntimeError(
            f"Operations bundle did not generate declared public files: {missing_generated}"
        )

    unexpected = sorted(published - copied - generated)
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


def build_site(output_dir: Path, cache_bust: bool) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    copy_tree_contents(DOCS, output_dir)
    manifest = load_public_data_manifest(PUBLIC_DATA_MANIFEST)
    copied = copy_public_data(DATA, output_dir / "data", manifest)
    materialize_operations_bundle(DATA, output_dir / "data", manifest)
    validate_public_data(output_dir / "data", copied, manifest)
    (output_dir / ".nojekyll").write_text("")
    if cache_bust:
        cache_bust_index(output_dir / "index.html", str(int(time.time())))


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
