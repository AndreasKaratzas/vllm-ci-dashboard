from __future__ import annotations

import json
import hashlib
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
    parser_version: int | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slug = "ci"
    version = {"parser_version": parser_version} if parser_version is not None else {}
    payload = "".join(
        json.dumps(
            {
                "pipeline": slug,
                "build_number": build_number,
                "job_id": f"job-{build_number}-{index}",
                **version,
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


def test_candidate_lookup_exposes_new_attempts_without_replacing_public_baseline(tmp_path):
    root = tmp_path / "checkpoint"
    source = tmp_path / "parsed" / "2026-09-01_amd.jsonl"
    write_shard(source, build_number=200, parser_version=1)
    checkpoint.record_complete_shard(root, source)
    public = tmp_path / "public"
    destination = public / source.name
    write_shard(destination, build_number=200, parser_version=1, rows=1)
    previous = destination.read_bytes()

    assert checkpoint.restore_complete_shards(root, public) == 0
    candidate = checkpoint.find_complete_shard(root, source.name, build_number=200)
    assert candidate == root / checkpoint.SHARD_DIR / source.name
    assert candidate.read_bytes() == source.read_bytes()
    assert destination.read_bytes() == previous
    assert checkpoint.find_complete_shard(root, source.name, build_number=201) is None

    candidate.write_bytes(candidate.read_bytes() + b"\n")
    with pytest.raises(checkpoint.BackfillCheckpointError):
        checkpoint.find_complete_shard(root, source.name, build_number=200)
    assert destination.read_bytes() == previous


def test_candidate_lookup_is_read_only_and_rejects_unsafe_identity(tmp_path):
    root = tmp_path / "missing"
    assert checkpoint.find_complete_shard(
        root, "2026-09-01_amd.jsonl", build_number=1,
    ) is None
    assert not root.exists()
    for name, number in [("../2026-09-01_amd.jsonl", 1), ("2026-09-01_amd.jsonl", True)]:
        with pytest.raises(checkpoint.BackfillCheckpointError, match="identity"):
            checkpoint.find_complete_shard(root, name, build_number=number)
    target = tmp_path / "target"
    checkpoint.load_or_reset(target)
    root.symlink_to(target, target_is_directory=True)
    with pytest.raises(checkpoint.BackfillCheckpointError, match="unsafe"):
        checkpoint.find_complete_shard(root, "2026-09-01_amd.jsonl", build_number=1)


@pytest.mark.parametrize(
    ("existing_build", "existing_parser", "checkpoint_parser", "restored"),
    [
        (200, None, 1, 1),
        (200, 1, None, 0),
        (200, 1, 1, 0),
        (201, None, 1, 0),
        (199, 1, None, 1),
    ],
)
def test_restore_preserves_parser_migration_progress_without_regressing_builds(
    tmp_path: Path, existing_build, existing_parser, checkpoint_parser, restored,
) -> None:
    root = tmp_path / "checkpoint"
    source = tmp_path / "parsed" / "2026-09-01_amd.jsonl"
    write_shard(source, build_number=200, parser_version=checkpoint_parser)
    checkpoint.record_complete_shard(root, source)
    results_dir = tmp_path / "restored-public"
    destination = results_dir / source.name
    write_shard(
        destination, build_number=existing_build, parser_version=existing_parser,
    )
    previous = destination.read_bytes()

    assert checkpoint.restore_complete_shards(root, results_dir) == restored
    assert destination.read_bytes() == (source.read_bytes() if restored else previous)


@pytest.mark.parametrize(
    ("versions", "expected"),
    [([2, 1], 1), ([1, None], 0), ([1, "1"], 0), ([1, True], 0)],
)
def test_parser_generation_requires_every_cached_row(
    tmp_path: Path, versions, expected,
):
    shard = tmp_path / "results.jsonl"
    shard.write_text("".join(
        json.dumps({"parser_version": version} if version is not None else {}) + "\n"
        for version in versions
    ))
    assert checkpoint.cached_result_parser_version(shard) == expected


def test_partial_or_wrong_pipeline_shard_is_never_checkpointed(tmp_path: Path) -> None:
    root = tmp_path / "checkpoint"
    checkpoint.load_or_reset(root)
    partial = tmp_path / "results" / "2026-09-01_amd.jsonl"
    partial.parent.mkdir(parents=True)
    partial.write_text(
        json.dumps({"pipeline": "amd-ci", "build_number": 1}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(checkpoint.BackfillCheckpointError, match="wrong pipeline"):
        checkpoint.record_complete_shard(root, partial)
    assert checkpoint.validate(root) == {"shards": 0, "bytes": 0}


def test_integrity_valid_legacy_amd_checkpoint_cannot_replace_current_ci_results(tmp_path: Path) -> None:
    root = tmp_path / "checkpoint"
    source = tmp_path / "parsed" / "2026-09-01_amd.jsonl"
    write_shard(source, build_number=101, parser_version=1)
    checkpoint.record_complete_shard(root, source)
    stored = root / checkpoint.SHARD_DIR / source.name
    rows = [json.loads(line) for line in stored.read_text().splitlines()]
    stored.write_text("".join(json.dumps({**row, "pipeline": "amd-ci"}) + "\n" for row in rows))
    manifest_path = root / checkpoint.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["shards"][source.name].update(bytes=stored.stat().st_size,
                                         sha256=hashlib.sha256(stored.read_bytes()).hexdigest())
    manifest_path.write_bytes(checkpoint._canonical(manifest))
    public = tmp_path / "current-results"
    destination = public / source.name
    write_shard(destination, build_number=93523, parser_version=1)
    current_bytes = destination.read_bytes()
    assert checkpoint.restore_complete_shards(root, public) == 0
    assert destination.read_bytes() == current_bytes
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


def test_checkpoint_repeatedly_drops_oldest_whole_days_at_byte_cap(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "checkpoint"
    source_dir = tmp_path / "results"
    probe = source_dir / "2026-08-01_amd.jsonl"
    write_shard(probe, build_number=1, rows=4)
    shard_bytes = probe.stat().st_size
    monkeypatch.setattr(checkpoint, "MAX_TOTAL_BYTES", shard_bytes * 2 + 16)

    for day in range(1, 7):
        for pipeline in ("amd", "upstream"):
            shard = source_dir / f"2026-08-{day:02d}_{pipeline}.jsonl"
            write_shard(
                shard,
                build_number=day * 10 + (pipeline == "upstream"),
                pipeline=pipeline,
                rows=4,
            )
            checkpoint.record_complete_shard(root, shard)
            stats = checkpoint.validate(root)
            assert stats["bytes"] <= checkpoint.MAX_TOTAL_BYTES

    restored = tmp_path / "restored"
    restored_count = checkpoint.restore_complete_shards(root, restored)
    retained = sorted(path.name for path in restored.glob("*.jsonl"))
    assert restored_count == 2
    assert retained == ["2026-08-06_amd.jsonl", "2026-08-06_upstream.jsonl"]
