"""Tests for exact-baseline queue projection restoration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vllm import restore_queue_projections as restore


QUEUE_PATH = Path("data/vllm/ci/operations_v2/queue.json")
CHART_PATH = Path("data/vllm/ci/queue_history_chart.json")
SOURCE_PATHS = (
    Path("data/vllm/ci/queue_jobs.json"),
    Path("data/vllm/ci/queue_timeseries.jsonl"),
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path, present: tuple[bool, bool]) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "queue-projection-test")
    _git(root, "config", "user.email", "queue-projection-test@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_text(
        "data/vllm/ci/operations_v2/\n", encoding="utf-8"
    )
    (root / "README.md").write_text("baseline\n", encoding="utf-8")
    baseline_content = {
        QUEUE_PATH: b'{"baseline":"queue"}\n',
        CHART_PATH: b'{"baseline":"chart"}\n',
    }
    for should_exist, (relative, content) in zip(present, baseline_content.items()):
        if should_exist:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    _git(root, "add", ".gitignore", "README.md")
    for should_exist, relative in zip(present, baseline_content):
        if should_exist:
            _git(root, "add", "-f", str(relative))
    _git(root, "commit", "-m", "projection baseline")
    baseline_sha = _git(root, "rev-parse", "HEAD")

    for relative, content in (
        (QUEUE_PATH, b'{"candidate":"queue"}\n'),
        (CHART_PATH, b'{"candidate":"chart"}\n'),
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    for relative in SOURCE_PATHS:
        (root / relative).write_bytes(f"source:{relative.name}\n".encode())
    return root, baseline_sha


@pytest.mark.parametrize(
    "present",
    ((False, False), (False, True), (True, False), (True, True)),
)
def test_mirrors_all_baseline_presence_combinations(tmp_path, present):
    root, baseline_sha = _repository(tmp_path, present)
    original_sources = {
        relative: (root / relative).read_bytes() for relative in SOURCE_PATHS
    }

    restore.mirror_queue_projections(root, baseline_sha)

    expected = {
        QUEUE_PATH: b'{"baseline":"queue"}\n',
        CHART_PATH: b'{"baseline":"chart"}\n',
    }
    for should_exist, (relative, content) in zip(present, expected.items()):
        destination = root / relative
        assert destination.exists() is should_exist
        if should_exist:
            assert destination.read_bytes() == content
            assert destination.stat().st_mode & 0o777 == 0o644
    for relative, content in original_sources.items():
        assert (root / relative).read_bytes() == content


def test_preflight_failure_does_not_mutate_either_projection(tmp_path):
    root, baseline_sha = _repository(tmp_path, (True, True))
    _git(root, "rm", "-f", str(CHART_PATH))
    (root / CHART_PATH).symlink_to("not-a-regular-projection")
    _git(root, "add", str(CHART_PATH))
    _git(root, "commit", "-m", "malformed projection baseline")
    malformed_sha = _git(root, "rev-parse", "HEAD")
    (root / CHART_PATH).unlink()
    candidates = {
        QUEUE_PATH: b'{"candidate":"queue-stays"}\n',
        CHART_PATH: b'{"candidate":"chart-stays"}\n',
    }
    for relative, content in candidates.items():
        (root / relative).write_bytes(content)

    with pytest.raises(restore.ProjectionRestoreError, match="not a regular"):
        restore.mirror_queue_projections(root, malformed_sha)

    for relative, content in candidates.items():
        assert (root / relative).read_bytes() == content
    assert baseline_sha != malformed_sha


def test_apply_failure_rolls_back_the_original_pair(tmp_path, monkeypatch):
    root, baseline_sha = _repository(tmp_path, (True, True))
    original = {
        relative: (root / relative).read_bytes()
        for relative in (QUEUE_PATH, CHART_PATH)
    }
    real_apply = restore._apply_projection
    calls = 0

    def fail_second_apply(destination, content, mode=0o644):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second projection failure")
        return real_apply(destination, content, mode)

    monkeypatch.setattr(restore, "_apply_projection", fail_second_apply)
    with pytest.raises(restore.ProjectionRestoreError, match="original pair was restored"):
        restore.mirror_queue_projections(root, baseline_sha)

    for relative, content in original.items():
        assert (root / relative).read_bytes() == content


def test_missing_candidate_projection_fails_before_mutation(tmp_path):
    root, baseline_sha = _repository(tmp_path, (True, True))
    chart_before = (root / CHART_PATH).read_bytes()
    (root / QUEUE_PATH).unlink()

    with pytest.raises(restore.ProjectionRestoreError, match="candidate.*missing"):
        restore.mirror_queue_projections(root, baseline_sha)

    assert not (root / QUEUE_PATH).exists()
    assert (root / CHART_PATH).read_bytes() == chart_before


def test_oversized_baseline_blob_fails_before_mutation(tmp_path, monkeypatch):
    root, baseline_sha = _repository(tmp_path, (True, True))
    candidates = {
        relative: (root / relative).read_bytes()
        for relative in (QUEUE_PATH, CHART_PATH)
    }
    monkeypatch.setattr(restore, "MAX_PROJECTION_BYTES", 8)

    with pytest.raises(restore.ProjectionRestoreError, match="hard limit"):
        restore.mirror_queue_projections(root, baseline_sha)

    for relative, content in candidates.items():
        assert (root / relative).read_bytes() == content


def test_oversized_candidate_fails_before_read_or_mutation(tmp_path, monkeypatch):
    root, baseline_sha = _repository(tmp_path, (True, True))
    oversized = b"x" * 65
    (root / CHART_PATH).write_bytes(oversized)
    queue_before = (root / QUEUE_PATH).read_bytes()
    monkeypatch.setattr(restore, "MAX_PROJECTION_BYTES", 64)

    with pytest.raises(
        restore.ProjectionRestoreError, match="candidate.*hard limit"
    ):
        restore.mirror_queue_projections(root, baseline_sha)

    assert (root / QUEUE_PATH).read_bytes() == queue_before
    assert (root / CHART_PATH).read_bytes() == oversized
