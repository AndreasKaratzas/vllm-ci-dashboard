import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vllm.ci import analytics_cache as cache
from vllm.pipelines import (
    UPSTREAM_NIGHTLY_NAME_PATTERN,
    upstream_scheduled_gating_kind,
)


NOW = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)

# cspell:ignore dailyish


def test_private_cache_retains_an_enforced_production_scale_cap():
    assert cache._MAX_CACHE_BYTES == 64 * 1024 * 1024
    assert cache._MAX_CACHE_TOTAL_BYTES == 256 * 1024 * 1024
    assert cache._MAX_CACHE_BYTES < 90_000_000


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Full CI run - nightly", "nightly"),
        ("full ci RUN - DAILY scheduled", "daily"),
        ("Full CI run-daily", "daily"),
        ("Full CI run - dailyish", None),
        ("prefix Full CI run - daily", None),
        ("Full CI run - weekly", None),
        ("AMD Full CI Run - nightly", None),
        (None, None),
    ],
)
def test_upstream_scheduled_gating_kind_is_strict(message, expected):
    assert upstream_scheduled_gating_kind(message) == expected
    if expected == "daily":
        assert re.search(UPSTREAM_NIGHTLY_NAME_PATTERN, message, re.IGNORECASE) is None


def _build(
    number=101,
    *,
    created_at=None,
    build_state="passed",
    job_state="passed",
    jobs=True,
):
    created_at = created_at or NOW - timedelta(days=1)
    row = {
        "number": number,
        "branch": "main",
        "state": build_state,
        "commit": f"{number:040x}"[-40:],
        "message": "Full CI run - nightly by private@example.com",
        "created_at": created_at.isoformat(),
        "started_at": (created_at + timedelta(minutes=1)).isoformat(),
        "finished_at": (
            (created_at + timedelta(hours=1)).isoformat()
            if build_state in cache.TERMINAL_BUILD_STATES
            else None
        ),
        "creator": {"name": "Private Person", "email": "private@example.com"},
        "author": {"name": "Private Author"},
        "web_url": "https://buildkite.example/private",
        "env": {"SECRET": "do-not-cache"},
    }
    if jobs:
        row["jobs"] = [
            {
                "id": f"job-{number}",
                "type": "script",
                "name": "GPU test",
                "state": job_state,
                "soft_failed": False,
                "runnable_at": (created_at + timedelta(minutes=1)).isoformat(),
                "started_at": (created_at + timedelta(minutes=2)).isoformat(),
                "finished_at": (
                    (created_at + timedelta(minutes=30)).isoformat()
                    if job_state in cache.TERMINAL_JOB_STATES
                    else None
                ),
                "agent_query_rules": ["queue=gpu_1_queue", "token=private"],
                "step": {"id": f"step-{number}", "key": "gpu-test", "label": "private"},
                "retried_in_job_id": f"retry-{number}",
                "retry_source": {"job_id": f"source-{number}", "creator": "private"},
                "command": "echo private",
                "agent": {"hostname": "private-host"},
                "env": {"TOKEN": "private"},
                "web_url": "https://buildkite.example/job/private",
            }
        ]
    return row


def _mi_scope(builds):
    for build in builds:
        build.update(hardware_scope="amd_mi_gpu", source_scope_commit=build["commit"], source_definition_tree_sha="a" * 40,
                     source_scope_index={"version": 1, "commit_sha": build["commit"],
                                         "definition_tree_sha": "a" * 40, "cpu_routes": []})
        for job in build.get("jobs") or []:
            job["agent_query_rules"] = ["queue=amd_mi300_1"]
    return builds


def _cache_dir(tmp_path):
    return tmp_path / cache.CACHE_DIR_NAME


def _write(tmp_path, builds=None, **overrides):
    kwargs = {
        "builds": builds if builds is not None else [_build()],
        "watermark": NOW,
        "window_days": 30,
        "last_full_at": NOW,
        "updated_at": NOW,
        "complete_from": NOW - timedelta(days=30),
    }
    kwargs.update(overrides)
    if kwargs.get("current_only"):
        kwargs["builds"] = _mi_scope(kwargs["builds"])
    return cache.write_build_cache(_cache_dir(tmp_path), "ci", **kwargs)


def _load(tmp_path, **overrides):
    kwargs = {
        "cutoff": NOW - timedelta(days=30),
        "window_days": 30,
        "ref_now": NOW,
    }
    kwargs.update(overrides)
    return cache.load_build_cache(_cache_dir(tmp_path), "ci", **kwargs)


def _reseal(payload):
    unsigned = dict(payload)
    unsigned.pop("integrity", None)
    canonical = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    payload["integrity"] = {
        "algorithm": "sha256",
        "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def _large_builds(count=6):
    builds = []
    for index in range(count):
        build = _build(
            200 + index,
            created_at=NOW - timedelta(hours=index + 1),
        )
        build["jobs"][0]["name"] = f"GPU test {index} " + ("x" * 900)
        builds.append(build)
    return builds


def _force_small_shards(monkeypatch):
    monkeypatch.setattr(cache, "_MAX_CACHE_BYTES", 2_500)
    monkeypatch.setattr(cache, "_MAX_CACHE_TOTAL_BYTES", 40_000)


def _cache_file_snapshot(cache_dir):
    return {
        path.relative_to(cache_dir).as_posix(): path.read_bytes()
        for path in cache_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_round_trip_has_version_identity_integrity_and_datetime_metadata(tmp_path):
    path = _write(tmp_path)
    payload = json.loads(path.read_text())

    assert payload["schema_version"] == cache.CACHE_SCHEMA_VERSION
    assert payload["cache_kind"] == cache.CACHE_KIND
    assert payload["pipeline"] == "ci"
    assert payload["query_identity"] == cache.DEFAULT_QUERY_IDENTITY
    assert payload["integrity"]["algorithm"] == "sha256"

    loaded = _load(tmp_path)
    assert loaded.valid is True
    assert loaded.status == "hit"
    assert loaded.reason == "ok"
    assert loaded.generated_at == NOW
    assert loaded.watermark == NOW
    assert loaded.last_full_at == NOW
    assert loaded.complete_from == NOW - timedelta(days=30)
    assert loaded.window_days == 30


def test_missing_cache_returns_diagnostic_miss(tmp_path):
    loaded = _load(tmp_path)
    assert loaded.valid is False
    assert loaded.status == "miss"
    assert loaded.reason == "not_found"
    assert loaded.builds == []


def test_tamper_is_rejected_without_returning_cached_rows(tmp_path):
    path = _write(tmp_path)
    payload = json.loads(path.read_text())
    payload["builds"][0]["state"] = "failed"
    path.write_text(json.dumps(payload))

    loaded = _load(tmp_path)
    assert loaded.valid is False
    assert loaded.reason == "integrity_mismatch"
    assert loaded.builds == []


def test_query_and_pipeline_identity_mismatches_are_diagnostic(tmp_path):
    path = _write(tmp_path)
    payload = json.loads(path.read_text())
    payload["query_identity"]["branch"] = "release"
    path.write_text(json.dumps(payload))
    assert _load(tmp_path).reason == "query_mismatch"

    amd_path = _cache_dir(tmp_path) / "amd-ci.json"
    amd_path.write_text(path.read_text())
    loaded = cache.load_build_cache(
        _cache_dir(tmp_path),
        "amd-ci",
        cutoff=NOW - timedelta(days=30),
        window_days=30,
        ref_now=NOW,
    )
    assert loaded.reason == "pipeline_mismatch"


@pytest.mark.parametrize(
    ("load_kwargs", "reason"),
    [
        ({"ref_now": NOW + timedelta(hours=49)}, "expired"),
        ({"window_days": 31}, "window_expansion"),
        ({"cutoff": NOW - timedelta(days=31)}, "coverage_gap"),
    ],
)
def test_expiry_and_window_coverage_fail_closed(tmp_path, load_kwargs, reason):
    _write(tmp_path)
    loaded = _load(tmp_path, **load_kwargs)
    assert loaded.valid is False
    assert loaded.reason == reason
    assert loaded.builds == []


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda payload: payload.update(window_days="30"), "malformed_types"),
        (lambda payload: payload["builds"][0].update(number="101"), "malformed_types"),
        (lambda payload: payload["builds"][0]["jobs"].append("not-an-object"), "malformed_types"),
        (lambda payload: payload["builds"][0].update(unexpected="private"), "noncanonical_projection"),
    ],
)
def test_resealed_malformed_payload_types_and_extra_fields_are_rejected(
    tmp_path, mutate, reason
):
    path = _write(tmp_path)
    payload = json.loads(path.read_text())
    mutate(payload)
    _reseal(payload)
    path.write_text(json.dumps(payload))

    loaded = _load(tmp_path)
    assert loaded.valid is False
    assert loaded.reason == reason
    assert loaded.builds == []


def test_projection_is_allowlisted_and_strips_pii(tmp_path):
    path = _write(tmp_path)
    raw = path.read_text()
    payload = json.loads(raw)
    build = payload["builds"][0]
    job = build["jobs"][0]

    assert build["canonical_nightly"] is True
    assert build["scheduled_gating_kind"] == "nightly"
    assert job["q"] == "gpu_1_queue"
    assert job["step"] == {"id": "step-101", "key": "gpu-test"}
    assert job["retry_source"] == {"job_id": "source-101"}
    for private_value in (
        "private@example.com",
        "Private Person",
        "Private Author",
        "private-host",
        "do-not-cache",
        "echo private",
        "token=private",
        "https://buildkite.example",
    ):
        assert private_value not in raw
    assert set(build) == {
        "number",
        "branch",
        "state",
        "commit",
        "created_at",
        "started_at",
        "finished_at",
        "canonical_nightly",
        "scheduled_gating_kind",
        "jobs_complete",
        "jobs",
    }


def test_upstream_daily_kind_round_trips_without_becoming_canonical_nightly(tmp_path):
    daily = _build()
    daily["message"] = "Full CI run - daily by private@example.com"

    path = _write(tmp_path, builds=[daily])
    projected = json.loads(path.read_text())["builds"][0]
    loaded = _load(tmp_path)

    assert projected["scheduled_gating_kind"] == "daily"
    assert projected["canonical_nightly"] is False
    assert "message" not in projected
    assert loaded.valid is True
    assert loaded.builds[0]["scheduled_gating_kind"] == "daily"


@pytest.mark.parametrize("kind", ["weekly", "Daily", "", None, 1])
def test_scheduled_gating_kind_rejects_values_outside_the_allowlist(tmp_path, kind):
    build = _build()
    build["scheduled_gating_kind"] = kind

    with pytest.raises(cache.CacheValidationError, match="allowlisted upstream kind"):
        _write(tmp_path, builds=[build])


def test_merge_is_fresh_wins_deduplicated_sorted_and_pruned():
    old = _build(1, created_at=NOW - timedelta(days=31), build_state="failed")
    cached = _build(2, created_at=NOW - timedelta(days=2), build_state="running")
    fresh = _build(2, created_at=NOW - timedelta(days=2), build_state="passed")
    newest = _build(3, created_at=NOW - timedelta(hours=1))

    merged = cache.merge_builds(
        [old, cached],
        [newest, fresh],
        cutoff=NOW - timedelta(days=30),
    )
    assert [row["number"] for row in merged] == [3, 2]
    assert merged[1]["state"] == "passed"


def test_write_prunes_builds_outside_requested_window(tmp_path):
    outside = _build(1, created_at=NOW - timedelta(days=31))
    boundary = _build(2, created_at=NOW - timedelta(days=30))
    recent = _build(3, created_at=NOW - timedelta(days=1))

    path = _write(
        tmp_path,
        builds=[outside, boundary, recent],
        complete_from=NOW - timedelta(days=60),
    )
    payload = json.loads(path.read_text())

    assert payload["complete_from"] == (NOW - timedelta(days=30)).isoformat().replace(
        "+00:00", "Z"
    )
    assert [build["number"] for build in payload["builds"]] == [3, 2]


def test_oversized_projection_uses_deterministic_bounded_shards(
    monkeypatch, tmp_path
):
    builds = _large_builds()
    _force_small_shards(monkeypatch)

    path = _write(tmp_path, builds=builds)
    manifest = json.loads(path.read_text())
    generation_dir = path.parent / "ci.shards" / manifest["generation"]
    first_files = {
        shard["name"]: (generation_dir / shard["name"]).read_bytes()
        for shard in manifest["shards"]
    }

    assert manifest["cache_kind"] == cache.CACHE_MANIFEST_KIND
    assert len(manifest["shards"]) > 1
    assert path.stat().st_size < cache._MAX_CACHE_BYTES
    assert all(
        shard["bytes"] == (generation_dir / shard["name"]).stat().st_size
        < cache._MAX_CACHE_BYTES
        for shard in manifest["shards"]
    )
    assert path.stat().st_size + sum(
        shard["bytes"] for shard in manifest["shards"]
    ) <= cache._MAX_CACHE_TOTAL_BYTES
    assert [build["number"] for build in _load(tmp_path).builds] == [
        build["number"] for build in cache.sanitize_builds(builds, "ci")
    ]

    # The content-derived generation and greedy partition are stable across
    # an identical rewrite, and the prior generation is pruned.
    _write(tmp_path, builds=builds)
    rewritten = json.loads(path.read_text())
    assert rewritten == manifest
    assert {
        shard["name"]: (generation_dir / shard["name"]).read_bytes()
        for shard in rewritten["shards"]
    } == first_files
    assert [child.name for child in (path.parent / "ci.shards").iterdir()] == [
        manifest["generation"]
    ]


def test_legacy_monolith_is_readable_then_safely_migrated_to_shards(
    monkeypatch, tmp_path
):
    builds = _large_builds()
    path = _write(tmp_path, builds=builds)
    legacy = json.loads(path.read_text())
    assert legacy["cache_kind"] == cache.CACHE_KIND

    _force_small_shards(monkeypatch)
    assert path.stat().st_size > cache._MAX_CACHE_BYTES
    assert _load(tmp_path).valid is True

    _write(tmp_path, builds=builds)
    manifest = json.loads(path.read_text())
    assert manifest["cache_kind"] == cache.CACHE_MANIFEST_KIND
    assert _load(tmp_path).valid is True


def test_fully_resealed_shard_tamper_is_rejected_by_generation_identity(
    monkeypatch, tmp_path
):
    _force_small_shards(monkeypatch)
    path = _write(tmp_path, builds=_large_builds())
    manifest = json.loads(path.read_text())
    descriptor = manifest["shards"][0]
    shard_path = (
        path.parent / "ci.shards" / manifest["generation"] / descriptor["name"]
    )
    shard = json.loads(shard_path.read_text())
    shard["builds"][0]["state"] = "failed"
    _reseal(shard)
    shard_raw = json.dumps(
        shard,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode() + b"\n"
    shard_path.write_bytes(shard_raw)
    descriptor["bytes"] = len(shard_raw)
    descriptor["file_sha256"] = hashlib.sha256(shard_raw).hexdigest()
    _reseal(manifest)
    path.write_text(json.dumps(manifest))

    loaded = _load(tmp_path)
    assert loaded.valid is False
    assert loaded.reason == "generation_mismatch"
    assert loaded.builds == []


def test_aggregate_directory_cap_counts_other_pipeline_and_preserves_cache(
    monkeypatch, tmp_path
):
    ci_path = _write(tmp_path)
    original = ci_path.read_bytes()
    amd_path = cache.write_build_cache(
        _cache_dir(tmp_path),
        "amd-ci",
        builds=[_build(301)],
        watermark=NOW,
        window_days=30,
        last_full_at=NOW,
        updated_at=NOW,
        complete_from=NOW - timedelta(days=30),
    )
    monkeypatch.setattr(
        cache,
        "_MAX_CACHE_TOTAL_BYTES",
        amd_path.stat().st_size + 100,
    )

    with pytest.raises(cache.CacheValidationError) as exc_info:
        _write(tmp_path, builds=[_build(302)])

    assert exc_info.value.reason == "oversize"
    assert ci_path.read_bytes() == original


def test_current_only_write_retires_large_legacy_partition_with_exact_ci_evidence(
    monkeypatch, tmp_path
):
    _force_small_shards(monkeypatch)
    legacy_builds = _large_builds(12)
    legacy_path = cache.write_build_cache(
        _cache_dir(tmp_path),
        "amd-ci",
        builds=legacy_builds,
        watermark=NOW,
        window_days=30,
        last_full_at=NOW,
        updated_at=NOW,
        complete_from=NOW - timedelta(days=30),
    )
    legacy_bytes = sum(len(value) for value in _cache_file_snapshot(_cache_dir(tmp_path)).values())
    assert legacy_path.exists()
    assert (legacy_path.parent / "amd-ci.shards").is_dir()
    monkeypatch.setattr(cache, "_MAX_CACHE_TOTAL_BYTES", legacy_bytes + 100)
    current = _large_builds(4)
    diagnostics = {}

    _write(tmp_path, builds=current, current_only=True, diagnostics=diagnostics)

    assert not legacy_path.exists()
    assert not (legacy_path.parent / "amd-ci.shards").exists()
    assert _load(tmp_path).builds == cache.sanitize_builds(current, "ci")
    assert diagnostics["retired_bytes_removed"] == legacy_bytes
    assert diagnostics["other_bytes"] == 0
    assert diagnostics["aggregate_bytes"] == diagnostics["active_bytes"]
    assert diagnostics["aggregate_bytes"] <= diagnostics["max_aggregate_bytes"]


def test_current_only_write_keeps_unknown_cache_entries_inside_byte_accounting(
    monkeypatch, tmp_path
):
    path = _write(tmp_path)
    original = path.read_bytes()
    unknown = path.parent / "unrecognized.json"
    unknown.write_bytes(b"x" * 10_000)
    monkeypatch.setattr(cache, "_MAX_CACHE_TOTAL_BYTES", 5_000)
    diagnostics = {}

    with pytest.raises(cache.CacheValidationError, match="aggregate cache"):
        _write(tmp_path, current_only=True, diagnostics=diagnostics)

    assert unknown.read_bytes() == b"x" * 10_000
    assert path.read_bytes() == original
    assert diagnostics["other_bytes"] == 10_000
    assert diagnostics["aggregate_bytes"] > diagnostics["max_aggregate_bytes"]


def test_bounded_current_cache_preserves_complete_creation_time_groups(
    monkeypatch, tmp_path
):
    _force_small_shards(monkeypatch)
    builds = _large_builds(10)
    builds[1]["created_at"] = builds[0]["created_at"]
    monkeypatch.setattr(cache, "_MAX_CACHE_TOTAL_BYTES", 12_000)
    diagnostics = {}

    _write(tmp_path, builds=builds, current_only=True, diagnostics=diagnostics)

    assert _load(tmp_path).reason == "coverage_gap"
    loaded = _load(tmp_path, allow_partial_coverage=True)
    assert loaded.valid is True
    assert loaded.reason == "partial_coverage"
    assert 2 <= len(loaded.builds) < len(builds)
    assert loaded.builds == cache.sanitize_builds(builds, "ci")[:len(loaded.builds)]
    assert loaded.complete_from == datetime.fromisoformat(loaded.builds[-1]["created_at"])
    assert all(
        datetime.fromisoformat(row["created_at"]) < loaded.complete_from
        for row in builds if row["number"] not in {build["number"] for build in loaded.builds}
    )
    assert diagnostics["original_active_bytes"] > diagnostics["max_aggregate_bytes"]
    assert diagnostics["retained_builds"] == len(loaded.builds)
    assert diagnostics["builds_removed"] == len(builds) - len(loaded.builds)
    assert sum(len(value) for value in _cache_file_snapshot(_cache_dir(tmp_path)).values()) <= cache._MAX_CACHE_TOTAL_BYTES


def test_current_cache_cannot_drop_part_of_one_oversized_timestamp_group(
    monkeypatch, tmp_path
):
    path = _write(tmp_path)
    original = path.read_bytes()
    builds = _large_builds(5)
    for build in builds:
        build["created_at"] = builds[0]["created_at"]
    monkeypatch.setattr(cache, "_MAX_CACHE_TOTAL_BYTES", 5_000)

    with pytest.raises(cache.CacheValidationError, match="aggregate cache"):
        _write(tmp_path, builds=builds, current_only=True)

    assert path.read_bytes() == original
    assert _load(tmp_path).valid is True


def test_sharded_manifest_replace_failure_keeps_legacy_cache_readable(
    monkeypatch, tmp_path
):
    builds = _large_builds()
    path = _write(tmp_path, builds=builds)
    original = path.read_bytes()
    _force_small_shards(monkeypatch)
    real_replace = cache.os.replace

    def fail_manifest_replace(source, target):
        if target == path:
            raise OSError("simulated manifest replace failure")
        real_replace(source, target)

    monkeypatch.setattr(cache.os, "replace", fail_manifest_replace)
    with pytest.raises(OSError, match="simulated manifest"):
        _write(tmp_path, builds=builds)

    assert path.read_bytes() == original
    assert _load(tmp_path).valid is True
    assert list(path.parent.rglob(".*.tmp")) == []
    assert not (path.parent / "ci.shards").exists()


@pytest.mark.parametrize("failure_point", ["shard", "manifest"])
def test_failed_sharded_refresh_rolls_back_only_uncommitted_generation(
    monkeypatch, tmp_path, failure_point
):
    _force_small_shards(monkeypatch)
    path = _write(tmp_path, builds=_large_builds())
    manifest = json.loads(path.read_text())
    old_generation = manifest["generation"]
    cache_dir = path.parent
    before = _cache_file_snapshot(cache_dir)
    before_bytes = sum(len(payload) for payload in before.values())
    stale_generation = "0" * 64 if old_generation != "0" * 64 else "1" * 64
    stale_dir = cache_dir / "ci.shards" / stale_generation
    stale_dir.mkdir()
    (stale_dir / "0000.json").write_bytes(b"unreferenced" * 1_000)
    assert sum(
        len(payload) for payload in _cache_file_snapshot(cache_dir).values()
    ) > before_bytes
    real_replace = cache.os.replace

    def fail_late_replace(source, target):
        target = Path(target)
        is_new_generation_shard = (
            target.parent.parent.name == "ci.shards"
            and target.parent.name != old_generation
            and target.name == "0001.json"
        )
        should_fail = (
            is_new_generation_shard if failure_point == "shard" else target == path
        )
        if should_fail:
            raise OSError(f"simulated {failure_point} replace failure")
        real_replace(source, target)

    monkeypatch.setattr(cache.os, "replace", fail_late_replace)
    next_now = NOW + timedelta(minutes=5)
    with pytest.raises(OSError, match=f"simulated {failure_point}"):
        _write(
            tmp_path,
            builds=_large_builds(),
            watermark=next_now,
            last_full_at=next_now,
            updated_at=next_now,
        )

    after = _cache_file_snapshot(cache_dir)
    assert after == before
    assert sum(len(payload) for payload in after.values()) == before_bytes
    assert before_bytes <= cache._MAX_CACHE_TOTAL_BYTES
    assert [child.name for child in (cache_dir / "ci.shards").iterdir()] == [
        old_generation
    ]
    assert _load(tmp_path).valid is True
    assert list(cache_dir.rglob(".*.tmp")) == []


def test_failed_same_generation_rewrite_never_deletes_active_generation(
    monkeypatch, tmp_path
):
    _force_small_shards(monkeypatch)
    path = _write(tmp_path, builds=_large_builds())
    manifest = json.loads(path.read_text())
    generation_dir = path.parent / "ci.shards" / manifest["generation"]
    before = _cache_file_snapshot(path.parent)
    real_replace = cache.os.replace

    def fail_active_shard_replace(source, target):
        target = Path(target)
        if target == generation_dir / "0000.json":
            raise OSError("simulated active shard replace failure")
        real_replace(source, target)

    monkeypatch.setattr(cache.os, "replace", fail_active_shard_replace)
    with pytest.raises(OSError, match="simulated active shard"):
        _write(tmp_path, builds=_large_builds())

    assert _cache_file_snapshot(path.parent) == before
    assert generation_dir.is_dir()
    assert _load(tmp_path).valid is True
    assert list(path.parent.rglob(".*.tmp")) == []


def test_post_commit_cleanup_failure_propagates_to_disable_actions_cache_save(
    monkeypatch, tmp_path
):
    _force_small_shards(monkeypatch)
    path = _write(tmp_path, builds=_large_builds())
    old_generation = json.loads(path.read_text())["generation"]
    real_cleanup = cache._cleanup_pipeline_shards
    cleanup_calls = 0

    def fail_post_commit_cleanup(*args, **kwargs):
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 2:
            raise OSError("simulated post-commit cleanup failure")
        return real_cleanup(*args, **kwargs)

    monkeypatch.setattr(cache, "_cleanup_pipeline_shards", fail_post_commit_cleanup)
    next_now = NOW + timedelta(minutes=5)
    with pytest.raises(OSError, match="post-commit cleanup"):
        _write(
            tmp_path,
            builds=_large_builds(),
            watermark=next_now,
            last_full_at=next_now,
            updated_at=next_now,
        )

    new_generation = json.loads(path.read_text())["generation"]
    assert new_generation != old_generation
    assert cleanup_calls == 2
    assert {child.name for child in (path.parent / "ci.shards").iterdir()} == {
        old_generation,
        new_generation,
    }
    # The exception is intentional: the collector converts it to
    # cache_written=false, and the workflow refuses to save this local tree.
    assert _load(
        tmp_path,
        ref_now=next_now,
        cutoff=next_now - timedelta(days=30),
    ).valid is True


def test_write_rejects_shard_over_size_cap_without_replacing_cache(
    monkeypatch, tmp_path
):
    path = _write(tmp_path)
    original = path.read_bytes()
    monkeypatch.setattr(cache, "_MAX_CACHE_BYTES", 128)

    with pytest.raises(cache.CacheValidationError) as exc_info:
        _write(tmp_path, builds=[_build(202)])

    assert exc_info.value.reason == "oversize"
    assert path.read_bytes() == original


def test_nonterminal_or_unknown_build_and_job_states_need_direct_refresh():
    terminal = cache.sanitize_builds([_build(1)], "ci")[0]
    running_build = cache.sanitize_builds(
        [_build(2, build_state="running", job_state="running")], "ci"
    )[0]
    running_job = cache.sanitize_builds([_build(3, job_state="running")], "ci")[0]
    unknown_job = cache.sanitize_builds([_build(4, job_state="future_state")], "ci")[0]
    missing_jobs = cache.sanitize_builds([_build(5, jobs=False)], "ci")[0]

    assert cache.builds_needing_refresh(
        [terminal, running_build, running_job, unknown_job, missing_jobs]
    ) == [2, 3, 4, 5]


def test_blocked_job_is_terminal_and_does_not_force_direct_refresh():
    row = _build(1, job_state="blocked")
    row["jobs"][0]["finished_at"] = None
    blocked = cache.sanitize_builds([row], "ci")[0]

    assert cache.builds_needing_refresh([blocked]) == []


def test_waiting_failed_job_is_terminal_and_does_not_force_direct_refresh():
    row = _build(1, build_state="failed", job_state="waiting_failed")
    row["jobs"][0]["finished_at"] = None
    waiting_failed = cache.sanitize_builds([row], "ci")[0]

    assert cache.builds_needing_refresh([waiting_failed]) == []


def test_terminal_build_without_finished_at_still_needs_direct_refresh():
    row = _build(1, job_state="blocked")
    row["finished_at"] = None
    unfinished = cache.sanitize_builds([row], "ci")[0]

    assert cache.builds_needing_refresh([unfinished]) == [1]


def test_finished_blocked_build_with_waiting_jobs_is_refresh_quiescent():
    row = _build(1, build_state="blocked", job_state="waiting")
    row["finished_at"] = NOW.isoformat()
    blocked = cache.sanitize_builds([row], "ci")[0]

    assert "blocked" not in cache.TERMINAL_BUILD_STATES
    assert cache.builds_needing_refresh([blocked]) == []


def test_blocked_build_without_finished_at_still_needs_direct_refresh():
    blocked = cache.sanitize_builds(
        [_build(1, build_state="blocked", job_state="waiting")],
        "ci",
    )[0]

    assert cache.builds_needing_refresh([blocked]) == [1]


@pytest.mark.parametrize("job_type", ["waiter", "manual", "trigger"])
def test_finished_terminal_build_ignores_non_script_waiting_jobs(job_type):
    row = _build(1, job_state="waiting")
    row["jobs"][0]["type"] = job_type
    finished = cache.sanitize_builds([row], "ci")[0]

    assert cache.builds_needing_refresh([finished]) == []


def test_finished_terminal_build_still_refreshes_running_script_job():
    running = cache.sanitize_builds([_build(1, job_state="running")], "ci")[0]

    assert cache.builds_needing_refresh([running]) == [1]


def test_unknown_job_with_finished_at_is_terminal_like_reliability_history():
    row = _build(1, job_state="future_state")
    row["jobs"][0]["finished_at"] = (NOW - timedelta(minutes=30)).isoformat()
    finished = cache.sanitize_builds([row], "ci")[0]

    assert cache.builds_needing_refresh([finished]) == []


def test_atomic_replace_failure_preserves_existing_cache(monkeypatch, tmp_path):
    path = _write(tmp_path)
    original = path.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(cache.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        _write(tmp_path, builds=[_build(202)])

    assert path.read_bytes() == original
    assert list(path.parent.glob(".*.tmp")) == []


def test_cache_path_is_private_versioned_directory_and_rejects_other_locations(tmp_path):
    path = _write(tmp_path)
    assert path == tmp_path / cache.CACHE_DIR_NAME / "ci.json"
    with pytest.raises(cache.CacheValidationError, match="must end"):
        cache.write_build_cache(
            tmp_path / "public",
            "ci",
            builds=[_build()],
            watermark=NOW,
            window_days=30,
            last_full_at=NOW,
            updated_at=NOW,
        )


@pytest.mark.parametrize("age_days", [0, 60])
def test_authenticated_immutable_source_index_survives_runtime_expiry(tmp_path, age_days):
    build = _mi_scope([_build()])[0]
    build["source_scope_index"]["cpu_routes"] = [
        {"key": "cpu-audit", "label": "ABI audit", "agent_pool": "mi300_1"},
    ]
    cache.write_build_cache(
        _cache_dir(tmp_path), "ci", builds=[build], watermark=NOW, window_days=30,
        last_full_at=NOW, updated_at=NOW, complete_from=NOW - timedelta(days=30),
        current_only=True,
    )
    clock = NOW + timedelta(days=age_days)
    indexes = cache.load_source_scope_indexes(_cache_dir(tmp_path), ref_now=clock)
    assert indexes == {build["commit"]: build["source_scope_index"]}
    assert all(set(index) == {"version", "commit_sha", "definition_tree_sha", "cpu_routes"}
               for index in indexes.values())
    if age_days:
        assert cache.load_build_cache(
            _cache_dir(tmp_path), "ci", cutoff=clock - timedelta(days=30), window_days=30,
            ref_now=clock,
        ).valid is False


def test_immutable_source_index_reader_authenticates_entire_cache(tmp_path):
    path = _write(tmp_path, current_only=True)
    payload = json.loads(path.read_text())
    payload["builds"][0]["source_scope_index"]["cpu_routes"] = [
        {"key": "forged", "label": "", "agent_pool": "mi300_1"},
    ]
    path.write_text(json.dumps(payload))
    assert cache.load_source_scope_indexes(_cache_dir(tmp_path), ref_now=NOW) == {}


@pytest.mark.parametrize("change", ["missing", "foreign_commit", "wrong_tree", "extra_field"])
def test_current_mi_cache_requires_exact_bounded_source_index(tmp_path, change):
    build = _mi_scope([_build()])[0]
    if change == "missing":
        del build["source_scope_index"]
    elif change == "foreign_commit":
        build["source_scope_index"]["commit_sha"] = "b" * 40
    elif change == "wrong_tree":
        build["source_scope_index"]["definition_tree_sha"] = "b" * 40
    else:
        build["source_scope_index"]["private_raw"] = "never retain"
    with pytest.raises(cache.CacheValidationError):
        cache.write_build_cache(
            _cache_dir(tmp_path), "ci", builds=[build], watermark=NOW, window_days=30,
            last_full_at=NOW, updated_at=NOW, complete_from=NOW - timedelta(days=30),
            current_only=True,
        )
    assert not (_cache_dir(tmp_path) / "ci.json").exists()



def _source_index(commit="a" * 40):
    return {"version": 1, "commit_sha": commit, "definition_tree_sha": "b" * 40,
            "cpu_routes": [{"key": "cpu", "label": "CPU audit", "agent_pool": "mi300_1"}]}


def test_standalone_source_checkpoint_has_no_runtime_or_private_observations(tmp_path):
    root = tmp_path / cache.RUNTIME_SOURCE_CACHE_DIR_NAME
    index = _source_index()
    cache.write_runtime_source_indexes(root, {index["commit_sha"]: index})
    assert cache.read_runtime_source_indexes(root) == {index["commit_sha"]: index}
    payload = json.loads((root / "index.json").read_text())
    assert set(payload) == {"schema_version", "cache_kind", "indexes", "integrity"}
    assert not set(payload) & {"generated_at", "watermark", "complete_from", "builds", "jobs"}
    assert cache.load_source_scope_indexes(tmp_path / cache.CACHE_DIR_NAME) == {index["commit_sha"]: index}


@pytest.mark.parametrize("failure", ["corruption", "noncanonical", "oversized", "symlink"])
def test_standalone_source_checkpoint_refuses_unsafe_cache_without_replacement(tmp_path, monkeypatch, failure):
    root = tmp_path / cache.RUNTIME_SOURCE_CACHE_DIR_NAME
    index = _source_index()
    cache.write_runtime_source_indexes(root, {index["commit_sha"]: index})
    path = root / "index.json"
    if failure == "corruption":
        payload = json.loads(path.read_text())
        payload["indexes"][index["commit_sha"]]["definition_tree_sha"] = "c" * 40
        path.write_text(json.dumps(payload))
    elif failure == "noncanonical":
        path.write_text(json.dumps(json.loads(path.read_text()), indent=2))
    elif failure == "oversized":
        monkeypatch.setattr(cache, "RUNTIME_SOURCE_CACHE_MAX_BYTES", 10)
    else:
        real = tmp_path / "actual-index.json"
        path.rename(real)
        path.symlink_to(real)
    before = path.read_bytes()
    with pytest.raises(cache.CacheValidationError):
        cache.read_runtime_source_indexes(root)
    with pytest.raises(cache.CacheValidationError):
        cache.write_runtime_source_indexes(root, {})
    assert path.read_bytes() == before


def test_standalone_source_checkpoint_bounds_and_failed_write_preserve_old_bytes(tmp_path, monkeypatch):
    root = tmp_path / cache.RUNTIME_SOURCE_CACHE_DIR_NAME
    first = _source_index()
    cache.write_runtime_source_indexes(root, {first["commit_sha"]: first})
    before = (root / "index.json").read_bytes()
    second = _source_index("c" * 40)
    both = {first["commit_sha"]: first, second["commit_sha"]: second}
    with monkeypatch.context() as bounded:
        bounded.setattr(cache, "RUNTIME_SOURCE_CACHE_MAX_PINS", 1)
        with pytest.raises(cache.CacheValidationError):
            cache.write_runtime_source_indexes(root, both)
    def fail(*_):
        raise OSError("simulated atomic failure")
    monkeypatch.setattr(cache, "_atomic_write", fail)
    with pytest.raises(OSError, match="atomic failure"):
        cache.write_runtime_source_indexes(root, both)
    assert (root / "index.json").read_bytes() == before



def test_reusable_source_proofs_rotate_at_cap_without_losing_active_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "RUNTIME_SOURCE_CACHE_MAX_PINS", 2)
    root = tmp_path / cache.RUNTIME_SOURCE_CACHE_DIR_NAME
    old = {letter * 40: _source_index(letter * 40) for letter in "ab"}
    cache.write_runtime_source_indexes(root, old)
    candidates = {**old, "c" * 40: _source_index("c" * 40)}
    with pytest.raises(cache.CacheValidationError):
        cache.write_runtime_source_indexes(root, candidates)
    retained = cache.retain_runtime_source_indexes(candidates, preferred_commits=["c" * 40, "b" * 40])
    cache.write_runtime_source_indexes(root, retained)
    assert list(retained) == ["c" * 40, "b" * 40]
    assert set(cache.read_runtime_source_indexes(root)) == {"b" * 40, "c" * 40}
    assert all(index["commit_sha"] == commit for commit, index in retained.items())
    # Never hide malformed source proof merely because it would be evicted.
    candidates["a" * 40]["definition_tree_sha"] = "invalid"
    with pytest.raises(cache.CacheValidationError):
        cache.retain_runtime_source_indexes(candidates, preferred_commits=["c" * 40, "b" * 40])


def test_source_index_union_prefers_authenticated_current_ci_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "RUNTIME_SOURCE_CACHE_MAX_PINS", 2)
    standalone = tmp_path / cache.RUNTIME_SOURCE_CACHE_DIR_NAME
    old = {letter * 40: _source_index(letter * 40) for letter in "ab"}
    cache.write_runtime_source_indexes(standalone, old)
    build = _mi_scope([_build()])[0]
    cache.write_build_cache(
        _cache_dir(tmp_path), "ci", builds=[build], watermark=NOW, window_days=30,
        last_full_at=NOW, updated_at=NOW, complete_from=NOW - timedelta(days=30), current_only=True,
    )
    indexes = cache.load_source_scope_indexes(_cache_dir(tmp_path), ref_now=NOW)
    assert len(indexes) == 2
    assert list(indexes)[0] == build["commit"]
    assert indexes[build["commit"]] == build["source_scope_index"]


def test_reusable_source_proofs_rotate_at_byte_cap_and_keep_new_pin(tmp_path, monkeypatch):
    root = tmp_path / cache.RUNTIME_SOURCE_CACHE_DIR_NAME
    old = {"a" * 40: _source_index("a" * 40)}
    cache.write_runtime_source_indexes(root, old)
    cap = (root / "index.json").stat().st_size
    monkeypatch.setattr(cache, "RUNTIME_SOURCE_CACHE_MAX_BYTES", cap)
    candidates = {**old, "c" * 40: _source_index("c" * 40)}
    with pytest.raises(cache.CacheValidationError, match="byte bound"):
        cache.write_runtime_source_indexes(root, candidates)
    retained = cache.retain_runtime_source_indexes(candidates, preferred_commits=["c" * 40])
    assert retained == {"c" * 40: candidates["c" * 40]}
    cache.write_runtime_source_indexes(root, retained)
    assert (root / "index.json").stat().st_size == cap
    assert cache.read_runtime_source_indexes(root) == retained
    # A single source proof that cannot fit must still fail rather than vanish.
    monkeypatch.setattr(cache, "RUNTIME_SOURCE_CACHE_MAX_BYTES", cap - 1)
    with pytest.raises(cache.CacheValidationError, match="active runtime source proof"):
        cache.retain_runtime_source_indexes(retained)
