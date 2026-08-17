import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from vllm.ci import analytics_cache as cache


NOW = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)


def test_private_cache_retains_an_enforced_production_scale_cap():
    assert cache._MAX_CACHE_BYTES == 256 * 1024 * 1024


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
        "jobs_complete",
        "jobs",
    }


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
