from __future__ import annotations

import subprocess

import pytest

from vllm import check_git_blob_sizes as blob_sizes


def test_default_budget_stays_below_ninety_decimal_megabytes():
    assert blob_sizes.DEFAULT_MAX_BYTES == 85 * 1024 * 1024
    assert blob_sizes.DEFAULT_MAX_BYTES < 90_000_000


def _git(root, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_tracked_blob_budget_reads_the_index_and_sorts_failures(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "small.txt").write_text("ok")
    (tmp_path / "z-large.txt").write_text("z" * 12)
    (tmp_path / "a-large.txt").write_text("a" * 16)
    _git(tmp_path, "add", ".")

    oversized = blob_sizes.oversized_tracked_blobs(tmp_path, max_bytes=8)

    assert [(row.path, row.size) for row in oversized] == [
        ("a-large.txt", 16),
        ("z-large.txt", 12),
    ]


def test_tracked_blob_budget_uses_staged_bytes_not_untracked_worktree(tmp_path):
    _git(tmp_path, "init")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("small")
    _git(tmp_path, "add", "tracked.txt")
    tracked.write_text("x" * 64)
    (tmp_path / "untracked.txt").write_text("y" * 64)

    assert blob_sizes.oversized_tracked_blobs(tmp_path, max_bytes=8) == []


def test_tracked_blob_budget_rejects_nonpositive_limit(tmp_path):
    with pytest.raises(ValueError, match="positive"):
        blob_sizes.oversized_tracked_blobs(tmp_path, max_bytes=0)
