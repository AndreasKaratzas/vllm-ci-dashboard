from __future__ import annotations

import json
from pathlib import Path

import pytest

from vllm import buildkite_request_guard as guard
from vllm.ci import backfill_checkpoint as checkpoint


def write_shard(
    path: Path,
    *,
    build_number: int,
    pipeline: str = "amd",
    rows: int = 2,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slug = "amd-ci" if pipeline == "amd" else "ci"
    payload = "".join(
        json.dumps(
            {
                "pipeline": slug,
                "build_number": build_number,
                "job_id": f"job-{build_number}-{index}",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
        for index in range(rows)
    )
    path.write_text(payload, encoding="utf-8")


def test_checkpoint_is_integrity_validated_bounded_and_restorable(tmp_path: Path) -> None:
    root = tmp_path / "checkpoint"
    source = tmp_path / "results" / "2026-09-01_amd.jsonl"
    write_shard(source, build_number=101)

    descriptor = checkpoint.record_complete_shard(root, source)
    assert descriptor["build_number"] == 101
    assert descriptor["bytes"] < checkpoint.MAX_SHARD_BYTES < 90 * 1024 * 1024
    assert checkpoint.validate(root) == {"shards": 1, "bytes": descriptor["bytes"]}

    restored_dir = tmp_path / "restored"
    assert checkpoint.restore_complete_shards(root, restored_dir) == 1
    assert (restored_dir / source.name).read_bytes() == source.read_bytes()
    assert checkpoint.restore_complete_shards(root, restored_dir) == 0


def test_progress_never_regresses_and_corrupt_restore_is_reset(tmp_path: Path) -> None:
    root = tmp_path / "checkpoint"
    shard = tmp_path / "results" / "2026-09-01_amd.jsonl"
    write_shard(shard, build_number=200)
    checkpoint.record_complete_shard(root, shard)

    write_shard(shard, build_number=199)
    with pytest.raises(checkpoint.BackfillCheckpointError, match="may not regress"):
        checkpoint.record_complete_shard(root, shard)
    assert checkpoint.validate(root)["shards"] == 1

    (root / checkpoint.MANIFEST_NAME).write_text(
        '{"schema_version":1,"schema_version":1}\n', encoding="utf-8"
    )
    reset = checkpoint.load_or_reset(root)
    assert reset["shards"] == {}
    assert checkpoint.validate(root) == {"shards": 0, "bytes": 0}


def test_partial_or_wrong_pipeline_shard_is_never_checkpointed(tmp_path: Path) -> None:
    root = tmp_path / "checkpoint"
    checkpoint.load_or_reset(root)
    partial = tmp_path / "results" / "2026-09-01_amd.jsonl"
    partial.parent.mkdir(parents=True)
    partial.write_text(
        json.dumps({"pipeline": "ci", "build_number": 1}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(checkpoint.BackfillCheckpointError, match="wrong pipeline"):
        checkpoint.record_complete_shard(root, partial)
    assert checkpoint.validate(root) == {"shards": 0, "bytes": 0}


def test_repeated_guard_exhaustion_makes_finite_monotonic_backfill_progress(
    tmp_path: Path,
) -> None:
    root = tmp_path / "private-cache"
    canonical_public = tmp_path / "public-ci.json"
    canonical_public.write_text('{"generation":"last-known-complete"}\n', encoding="utf-8")
    build_names = [f"2026-08-{day:02d}_amd.jsonl" for day in range(27, 32)]
    progress: list[int] = []

    for attempt_number in range(1, 5):
        results = tmp_path / f"attempt-{attempt_number}"
        checkpoint.restore_complete_shards(root, results)
        guard_path = tmp_path / f"guard-{attempt_number}.json"
        attempt_id = f"data-{attempt_number}-1"
        guard.initialize(guard_path, attempt_id=attempt_id, allowance=4)
        try:
            for build_number, name in enumerate(build_names, start=100):
                if (results / name).exists():
                    continue
                # Model two log starts followed by an atomic complete-nightly
                # shard. Allowance exhaustion happens before any partial shard
                # can be recorded.
                guard.consume(guard_path, attempt_id=attempt_id, allowance=4)
                guard.consume(guard_path, attempt_id=attempt_id, allowance=4)
                shard = results / name
                write_shard(shard, build_number=build_number)
                checkpoint.record_complete_shard(root, shard)
        except guard.BuildkiteRequestGuardError:
            pass
        progress.append(checkpoint.validate(root)["shards"])
        assert canonical_public.read_text(encoding="utf-8") == (
            '{"generation":"last-known-complete"}\n'
        )
        if progress[-1] == len(build_names):
            break

    assert progress == [2, 4, 5]
    final_results = tmp_path / "final"
    assert checkpoint.restore_complete_shards(root, final_results) == 5
    assert sorted(path.name for path in final_results.glob("*.jsonl")) == build_names
