from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vllm import check_git_blob_sizes as blob_sizes


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_default_budget_stays_below_ninety_decimal_megabytes():
    assert blob_sizes.DEFAULT_MAX_BYTES == 85 * 1024 * 1024
    assert blob_sizes.DEFAULT_MAX_BYTES < 90_000_000
    assert blob_sizes.DEFAULT_WARNING_BYTES == 75 * 1024 * 1024
    assert blob_sizes.DEFAULT_WARNING_BYTES < blob_sizes.DEFAULT_MAX_BYTES
    assert blob_sizes.DEFAULT_MAX_TREE_BYTES == 256 * 1024 * 1024


def test_repository_index_has_no_blob_over_the_guardrail():
    assert blob_sizes.oversized_tracked_blobs(REPO_ROOT) == []
    summary = blob_sizes.summarize_tracked_tree(blob_sizes.tracked_blobs(REPO_ROOT))
    assert summary.logical_bytes <= blob_sizes.DEFAULT_MAX_TREE_BYTES


def test_private_operations_monolith_and_shards_cannot_enter_the_index():
    tracked = subprocess.run(
        [
            "git",
            "ls-files",
            "--",
            "data/vllm/ci/operations_v2.json",
            "data/vllm/ci/operations_v2",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert tracked.stdout == ""
    for relative in (
        "data/vllm/ci/operations_v2.json",
        "data/vllm/ci/operations_v2/reliability.json",
    ):
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", relative],
            cwd=REPO_ROOT,
            check=False,
        )
        assert ignored.returncode == 0


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


def test_tree_summary_reports_logical_and_unique_blob_bytes(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "one.txt").write_text("same")
    (tmp_path / "two.txt").write_text("same")
    (tmp_path / "three.txt").write_text("different")
    _git(tmp_path, "add", ".")

    summary = blob_sizes.summarize_tracked_tree(blob_sizes.tracked_blobs(tmp_path))

    assert summary.file_count == 3
    assert summary.logical_bytes == len("same") * 2 + len("different")
    assert summary.unique_blob_bytes == len("same") + len("different")
    assert summary.largest_blob_bytes == len("different")


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
