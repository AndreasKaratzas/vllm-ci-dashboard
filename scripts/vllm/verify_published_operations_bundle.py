#!/usr/bin/env python3
"""Verify deployed Operations assets against the assembled public manifest."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any


MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ASSET_BYTES = 85 * 1024 * 1024
MAX_SECTIONS = 64
GIT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
SECTION_LABEL_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

AssetInfo = Callable[[str, Path], tuple[str, str, int, str, str]]


class BundleVerificationError(RuntimeError):
    """A bounded public bundle failed its manifest contract."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_bounded(path: Path) -> bytes:
    try:
        size = path.stat().st_size
        if size > MAX_MANIFEST_BYTES:
            raise BundleVerificationError(f"{path} exceeds 1 MiB")
        return path.read_bytes()
    except OSError as exc:
        raise BundleVerificationError(f"could not read {path}: {type(exc).__name__}") from exc


def _load_matching_manifest(assembled: Path, deployed: Path) -> dict[str, Any]:
    assembled_raw = _read_bounded(assembled)
    deployed_raw = _read_bounded(deployed)
    if assembled_raw != deployed_raw:
        raise BundleVerificationError(
            "published Operations manifest differs from the assembled site"
        )
    try:
        payload: Any = json.loads(
            deployed_raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise BundleVerificationError("published Operations manifest is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise BundleVerificationError("published Operations manifest must be an object")
    return payload


def _asset_descriptors(manifest: dict[str, Any]) -> list[tuple[str, str, int]]:
    sections = manifest.get("sections")
    organization_summary = manifest.get("organization_summary")
    if (
        type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 2
        or type(manifest.get("bundle_version")) is not int
        or manifest.get("bundle_version") != 1
        or manifest.get("monolith") is not None
        or not isinstance(sections, dict)
        or not 1 <= len(sections) <= MAX_SECTIONS
        or not isinstance(organization_summary, dict)
    ):
        raise BundleVerificationError("published Operations manifest contract is invalid")

    raw_descriptors: list[tuple[str, object]] = [("organization_summary", organization_summary)]
    raw_descriptors.extend(sorted(sections.items()))
    descriptors: list[tuple[str, str, int]] = []
    seen_paths: set[str] = set()
    for label, descriptor in raw_descriptors:
        if not isinstance(label, str) or not isinstance(descriptor, dict):
            raise BundleVerificationError("published Operations descriptor is invalid")
        relative = descriptor.get("path")
        expected_bytes = descriptor.get("bytes")
        if not isinstance(relative, str) or not relative:
            raise BundleVerificationError(f"Operations descriptor {label!r} has no path")
        path = PurePosixPath(relative)
        if (
            path.is_absolute()
            or ".." in path.parts
            or relative in seen_paths
            or type(expected_bytes) is not int
            or not 0 <= expected_bytes <= MAX_ASSET_BYTES
        ):
            raise BundleVerificationError(f"Operations descriptor {label!r} is unsafe")
        if label == "organization_summary":
            path_valid = relative == "org_summary.json"
        else:
            path_valid = (
                SECTION_LABEL_RE.fullmatch(label) is not None
                and len(path.parts) == 2
                and path.parts[0] == "operations_v2"
                and path.name == f"{label}.json"
            )
        if not path_valid:
            raise BundleVerificationError(f"Operations descriptor {label!r} has an invalid path")
        seen_paths.add(relative)
        descriptors.append((label, relative, expected_bytes))
    return descriptors


def _git_asset_info(
    repo_root: Path,
    git_ref: str,
    git_path: str,
    assembled_path: Path,
) -> tuple[str, str, int, str, str]:
    spec = f"{git_ref}:{git_path}"
    result = subprocess.run(
        [
            "git",
            "cat-file",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        input=spec + "\n",
    )
    fields = result.stdout.strip().split()
    try:
        remote_oid, object_type, raw_size = fields
        size = int(raw_size)
    except (ValueError, TypeError):
        remote_oid, object_type, size = "", "", -1
    tree_result = subprocess.run(
        ["git", "ls-tree", git_ref, "--", git_path],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    tree_fields = tree_result.stdout.partition("\t")[0].split()
    try:
        mode, tree_type, tree_oid = tree_fields
    except ValueError:
        mode, tree_type, tree_oid = "", "", ""
    hash_result = subprocess.run(
        ["git", "hash-object", "--", str(assembled_path)],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assembled_oid = hash_result.stdout.strip()
    if (
        result.returncode != 0
        or tree_result.returncode != 0
        or hash_result.returncode != 0
        or mode != "100644"
        or object_type != "blob"
        or tree_type != object_type
        or tree_oid != remote_oid
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", remote_oid) is None
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", assembled_oid) is None
        or size < 0
    ):
        raise BundleVerificationError(
            f"published Operations asset {git_path!r} is missing or is not a blob"
        )
    return mode, object_type, size, remote_oid, assembled_oid


def _assembled_asset_size(path: Path) -> int:
    try:
        if path.is_symlink() or not path.is_file():
            raise BundleVerificationError(f"assembled Operations asset {path} is not a file")
        return path.stat().st_size
    except OSError as exc:
        raise BundleVerificationError(
            f"could not inspect assembled Operations asset {path}: {type(exc).__name__}"
        ) from exc


def verify_published_bundle(
    assembled_manifest: Path,
    deployed_manifest: Path,
    *,
    git_ref: str,
    repo_root: Path | None = None,
    data_prefix: str = "data/vllm/ci",
    asset_info: AssetInfo | None = None,
) -> int:
    """Return the number of verified assets or raise on any inconsistency."""
    # Keep filesystem inspection and ``git hash-object`` pinned to the same
    # files even when callers provide a repository root that differs from the
    # process working directory. ``absolute()`` deliberately does not follow
    # symlinks; the regular-file check below must still see and reject them.
    assembled_manifest = assembled_manifest.absolute()
    deployed_manifest = deployed_manifest.absolute()
    if (
        GIT_REF_RE.fullmatch(git_ref) is None
        or ".." in PurePosixPath(git_ref).parts
        or ":" in git_ref
    ):
        raise BundleVerificationError("git ref is invalid")
    prefix = PurePosixPath(data_prefix)
    if prefix.is_absolute() or ".." in prefix.parts or not prefix.parts:
        raise BundleVerificationError("data prefix is invalid")

    manifest = _load_matching_manifest(assembled_manifest, deployed_manifest)
    descriptors = _asset_descriptors(manifest)
    resolved_repo_root = repo_root if repo_root is not None else Path.cwd()
    for _, relative, expected_bytes in descriptors:
        git_path = str(prefix / relative)
        assembled_path = assembled_manifest.parent / relative
        assembled_bytes = _assembled_asset_size(assembled_path)
        if assembled_bytes != expected_bytes:
            raise BundleVerificationError(
                f"Operations asset {relative!r} has local size "
                f"{assembled_bytes}; expected {expected_bytes}"
            )
        mode, object_type, actual_bytes, remote_oid, assembled_oid = (
            asset_info(git_path, assembled_path)
            if asset_info is not None
            else _git_asset_info(
                resolved_repo_root,
                git_ref,
                git_path,
                assembled_path,
            )
        )
        if (
            mode != "100644"
            or object_type != "blob"
            or type(actual_bytes) is not int
            or actual_bytes != expected_bytes
        ):
            raise BundleVerificationError(
                f"Operations asset {relative!r} has deployed size "
                f"{actual_bytes}; expected {expected_bytes}"
            )
        if (
            not isinstance(remote_oid, str)
            or not isinstance(assembled_oid, str)
            or remote_oid != assembled_oid
        ):
            raise BundleVerificationError(
                f"published Operations asset {relative!r} differs from the assembled site"
            )
    return len(descriptors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assembled-manifest", type=Path, required=True)
    parser.add_argument("--deployed-manifest", type=Path, required=True)
    parser.add_argument("--git-ref", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--data-prefix", default="data/vllm/ci")
    args = parser.parse_args()

    try:
        count = verify_published_bundle(
            args.assembled_manifest,
            args.deployed_manifest,
            git_ref=args.git_ref,
            repo_root=args.repo_root,
            data_prefix=args.data_prefix,
        )
    except BundleVerificationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Verified {count} published Operations assets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
