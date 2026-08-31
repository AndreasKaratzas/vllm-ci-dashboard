#!/usr/bin/env python3
"""Fail before publication when a tracked Git blob approaches GitHub's limit."""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from pathlib import Path


# Keep a deliberate cushion below the dashboard's 90 MB sync ceiling.  Using
# 85 MiB also stays below 90,000,000 decimal bytes, so the guard is safe no
# matter which unit a hosting/sync layer uses when it reports "90 MB".
DEFAULT_MAX_BYTES = 85 * 1024 * 1024


@dataclass(frozen=True)
class TrackedBlob:
    path: str
    object_id: str
    size: int


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject tracked Git blobs that exceed the publication budget"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args()

    oversized = oversized_tracked_blobs(
        args.root.resolve(),
        max_bytes=args.max_bytes,
    )
    if not oversized:
        print(f"Tracked Git blob budget passed (max {args.max_bytes} bytes).")
        return 0

    for blob in oversized:
        print(
            "::error::Tracked Git blob exceeds the publication budget: "
            f"{blob.path} is {blob.size} bytes (max {args.max_bytes})"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
