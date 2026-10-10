#!/usr/bin/env python3
"""Fail before publication when a tracked Git blob approaches GitHub's limit."""

# cspell:ignore redef

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .publication_limits import (
        PUBLICATION_MAX_BLOB_BYTES,
        PUBLICATION_MAX_TREE_BYTES,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from publication_limits import (  # type: ignore[no-redef]
        PUBLICATION_MAX_BLOB_BYTES,
        PUBLICATION_MAX_TREE_BYTES,
    )


# Keep a deliberate cushion below the dashboard's 90 MB sync ceiling.  Using
# 85 MiB also stays below 90,000,000 decimal bytes, so the guard is safe no
# matter which unit a hosting/sync layer uses when it reports "90 MB".
DEFAULT_MAX_BYTES = PUBLICATION_MAX_BLOB_BYTES
DEFAULT_WARNING_BYTES = 75 * 1024 * 1024
DEFAULT_MAX_TREE_BYTES = PUBLICATION_MAX_TREE_BYTES


@dataclass(frozen=True)
class TrackedBlob:
    path: str
    object_id: str
    size: int


@dataclass(frozen=True)
class TrackedTreeSummary:
    file_count: int
    logical_bytes: int
    unique_blob_bytes: int
    largest_blob_bytes: int


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout


def tracked_blobs(root: Path) -> list[TrackedBlob]:
    """Return regular-file and symlink blobs from the current Git index."""
    rows: list[TrackedBlob] = []
    for entry in _git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        metadata, encoded_path = entry.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split()
        if stage != "0" or mode == "160000":
            continue
        size = int(_git(root, "cat-file", "-s", object_id).strip())
        rows.append(
            TrackedBlob(
                path=encoded_path.decode("utf-8", errors="surrogateescape"),
                object_id=object_id,
                size=size,
            )
        )
    return rows


def oversized_tracked_blobs(
    root: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> list[TrackedBlob]:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    return sorted(
        (blob for blob in tracked_blobs(root) if blob.size > max_bytes),
        key=lambda blob: (-blob.size, blob.path),
    )


def summarize_tracked_tree(blobs: list[TrackedBlob]) -> TrackedTreeSummary:
    unique = {blob.object_id: blob.size for blob in blobs}
    return TrackedTreeSummary(
        file_count=len(blobs),
        logical_bytes=sum(blob.size for blob in blobs),
        unique_blob_bytes=sum(unique.values()),
        largest_blob_bytes=max((blob.size for blob in blobs), default=0),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject tracked Git blobs that exceed the publication budget"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--warning-bytes", type=int, default=DEFAULT_WARNING_BYTES)
    parser.add_argument("--max-tree-bytes", type=int, default=DEFAULT_MAX_TREE_BYTES)
    args = parser.parse_args()

    if not 0 < args.warning_bytes < args.max_bytes:
        parser.error("--warning-bytes must be positive and below --max-bytes")
    if args.max_tree_bytes <= 0:
        parser.error("--max-tree-bytes must be positive")

    blobs = tracked_blobs(args.root.resolve())
    summary = summarize_tracked_tree(blobs)
    oversized = sorted(
        (blob for blob in blobs if blob.size > args.max_bytes),
        key=lambda blob: (-blob.size, blob.path),
    )
    warnings = sorted(
        (
            blob
            for blob in blobs
            if args.warning_bytes < blob.size <= args.max_bytes
        ),
        key=lambda blob: (-blob.size, blob.path),
    )

    for blob in oversized:
        print(
            "::error::Tracked Git blob exceeds the publication budget: "
            f"{blob.path} is {blob.size} bytes (max {args.max_bytes})"
        )
    for blob in warnings:
        print(
            "::warning::Tracked Git blob is approaching the publication budget: "
            f"{blob.path} is {blob.size} bytes "
            f"(warning {args.warning_bytes}, max {args.max_bytes})"
        )
    if summary.logical_bytes > args.max_tree_bytes:
        print(
            "::error::Tracked Git index exceeds the bounded tree budget: "
            f"{summary.logical_bytes} bytes (max {args.max_tree_bytes})"
        )
    print(
        "Tracked Git tree: "
        f"{summary.file_count} files, {summary.logical_bytes} logical bytes, "
        f"{summary.unique_blob_bytes} unique blob bytes, "
        f"largest {summary.largest_blob_bytes} bytes."
    )
    if oversized or summary.logical_bytes > args.max_tree_bytes:
        return 1
    print(f"Tracked Git blob budget passed (max {args.max_bytes} bytes).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
