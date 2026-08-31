"""Private DNS classification cache boundaries and CI/DNS hand-off tests."""

from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import collect_ci as core_collector
from vllm import collect_dns_failures as collector
from vllm.ci import buildkite_client as buildkite_cache
from vllm.ci import dns_classification_cache as cache_module
from vllm.ci import log_parser
from vllm.ci.dns_classification_cache import (
    DnsClassificationCache,
    DnsClassificationCacheError,
    load_optional_dns_classification_cache,
)
from vllm.ci.dns_failures import iso_timestamp, pending_record


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _uuid(index: int) -> str:
    return f"00000000-0000-4000-8000-{index:012x}"


def _job(index: int, *, finished_at: datetime = NOW) -> dict:
    return {
        "id": _uuid(index),
        "type": "script",
        "name": f"private test label {index}",
        "state": "passed",
        "started_at": iso_timestamp(finished_at - timedelta(hours=1)),
        "finished_at": iso_timestamp(finished_at),
        "agent_query_rules": ["queue=amd_mi300_1"],
        "raw_log_url": "https://api.buildkite.invalid/private/job/log",
        "env": {"TOKEN": "github_pat_should_never_be_written"},
    }


def _metadata(
    job: dict,
    *,
    build_number: int = 12001,
    node: str = "unidentified",
) -> dict:
    return {
        "pipeline": "amd-ci",
        "build_number": build_number,
        "job_id": job["id"],
        "queue": "amd_mi300_1",
        "node": node,
        "hardware": "MI300",
        "state": "passed",
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }


def test_core_log_parse_emits_cache_without_a_second_log_request(monkeypatch, tmp_path):
    job = _job(1)
    raw_log = (
        "github_pat_this_is_private\n"
        "Temporary failure in name resolution while reaching huggingface.co\n"
        "===== 1 passed in 1.0s =====\n"
    )
    fetches = []

    def fetch_once(received_job):
        fetches.append(received_job["id"])
        return raw_log

    monkeypatch.setattr(log_parser, "fetch_job_log", fetch_once)
    cache = DnsClassificationCache(tmp_path / "dns-cache", now=NOW)
    results = log_parser.parse_job_results(
        job,
        12001,
        "amd-ci",
        "2026-08-20",
        dns_classification_sink=cache.observe_job_log,
    )
    cache.flush()

    assert fetches == [job["id"]]
    assert results
    restored = DnsClassificationCache(tmp_path / "dns-cache", now=NOW)
    assert restored.classification_for(_metadata(job)).positive is True


def test_optional_dns_sink_failure_never_breaks_core_log_parsing():
    job = _job(11)

    def broken_sink(**_kwargs):
        raise RuntimeError("cache-only failure")

    results = log_parser.parse_job_results(
        job,
        12001,
        "amd-ci",
        "2026-08-20",
        log_text="===== 1 passed in 1.0s =====\n",
        dns_classification_sink=broken_sink,
    )

    assert results


def test_dns_cache_hit_is_consumed_before_any_job_log_get(tmp_path):
    job = _job(2)
    cache = DnsClassificationCache(tmp_path / "dns-cache", now=NOW)
    assert cache.observe_job_log(
        job=job,
        pipeline="amd-ci",
        build_number=12001,
        log_text="ordinary successful output with no DNS resolver errors",
    )
    cache.flush()
    restored = DnsClassificationCache(tmp_path / "dns-cache", now=NOW)

    class NoLogClient:
        calls = 0

        def fetch_job_log(self, metadata, *, deadline=None):
            self.calls += 1
            raise AssertionError("a validated shared classification must suppress this GET")

    client = NoLogClient()
    rows = collector.scan_records(
        [pending_record(_metadata(job, node="crsuse2-m2m-295"))],
        client=client,
        attempted_at=iso_timestamp(NOW),
        max_logs=1,
        classification_cache=restored,
    )

    assert client.calls == 0
    assert rows[0]["status"] == "negative"


def test_unidentified_node_bypasses_cache_so_log_can_recover_attribution(tmp_path):
    job = _job(12)
    cache = DnsClassificationCache(tmp_path / "dns-cache", now=NOW)
    assert cache.observe_job_log(
        job=job,
        pipeline="amd-ci",
        build_number=12001,
        log_text="ordinary successful output with no DNS resolver errors",
    )
    cache.flush()
    restored = DnsClassificationCache(tmp_path / "dns-cache", now=NOW)

    class RecoveryClient:
        calls = 0

        def fetch_job_log(self, metadata, *, deadline=None):
            self.calls += 1
            text = (
                "=== Pod: buildkite-amd-abc | Node: crsuse2-m2m-295 | now ===\n"
                "ordinary successful output with no DNS resolver errors"
            )
            return text, len(text.encode())

    client = RecoveryClient()
    rows = collector.scan_records(
        [pending_record(_metadata(job))],
        client=client,
        attempted_at=iso_timestamp(NOW),
        max_logs=1,
        classification_cache=restored,
    )

    assert client.calls == 1
    assert rows[0]["status"] == "negative"
    assert rows[0]["node"] == "crsuse2-m2m-295"


def test_metadata_mismatch_is_a_miss_and_falls_back_to_log_get(tmp_path):
    job = _job(8)
    cache = DnsClassificationCache(tmp_path / "dns-cache", now=NOW)
    assert cache.observe_job_log(
        job=job,
        pipeline="amd-ci",
        build_number=12001,
        log_text="ordinary successful output with no DNS resolver errors",
    )
    cache.flush()
    restored = DnsClassificationCache(tmp_path / "dns-cache", now=NOW)
    metadata = _metadata(job, build_number=12002)

    class LogClient:
        calls = 0

        def fetch_job_log(self, received, *, deadline=None):
            self.calls += 1
            assert received["job_id"] == metadata["job_id"]
            assert received["build_number"] == metadata["build_number"]
            return "curl: (6) Could not resolve host: github.com", None

    client = LogClient()
    rows = collector.scan_records(
        [pending_record(metadata)],
        client=client,
        attempted_at=iso_timestamp(NOW),
        max_logs=1,
        classification_cache=restored,
    )

    assert client.calls == 1
    assert rows[0]["status"] == "positive"


def test_fresh_log_replaces_stale_restored_classification(tmp_path):
    job = _job(9)
    cache_path = tmp_path / "dns-cache"
    initial = DnsClassificationCache(cache_path, now=NOW)
    assert initial.observe_job_log(
        job=job,
        pipeline="amd-ci",
        build_number=12001,
        log_text="curl: (6) Could not resolve host: github.com",
    )
    initial.flush()

    refreshed = DnsClassificationCache(cache_path, now=NOW)
    assert refreshed.observe_job_log(
        job=job,
        pipeline="amd-ci",
        build_number=12001,
        log_text="ordinary successful output with no DNS resolver errors",
    )
    refreshed.flush()

    restored = DnsClassificationCache(cache_path, now=NOW)
    assert restored.classification_for(_metadata(job)).positive is False


def test_shard_contains_only_minimized_classification_fields(tmp_path):
    job = _job(3)
    secret_log = (
        "Authorization: Bearer extremely-private-token\n"
        "curl: (6) Could not resolve host: github.com\n"
        "private@example.invalid\n"
    )
    cache = DnsClassificationCache(tmp_path / "dns-cache", now=NOW)
    assert cache.observe_job_log(
        job=job,
        pipeline="amd-ci",
        build_number=12001,
        log_text=secret_log,
    )
    stats = cache.flush()
    shard = next((tmp_path / "dns-cache").iterdir())
    decoded = gzip.decompress(shard.read_bytes()).decode("utf-8")
    row = json.loads(decoded)

    assert stats["compressed_bytes"] <= cache_module.MAX_COMPRESSED_TOTAL_BYTES
    assert shard.stat().st_size <= cache_module.MAX_COMPRESSED_SHARD_BYTES
    assert set(row) == cache_module._ROW_KEYS
    serialized = json.dumps(row)
    for forbidden in (
        "Authorization",
        "extremely-private-token",
        "private@example.invalid",
        job["name"],
        job["raw_log_url"],
        "github.com",
    ):
        assert forbidden not in serialized


def test_flush_prunes_beyond_35_days_and_enforces_shard_limit(monkeypatch, tmp_path):
    cache_path = tmp_path / "dns-cache"
    old_clock = NOW - timedelta(days=35)
    old_job = _job(4, finished_at=old_clock)
    old_cache = DnsClassificationCache(cache_path, now=old_clock)
    assert old_cache.observe_job_log(
        job=old_job,
        pipeline="amd-ci",
        build_number=12001,
        log_text="no resolver failure",
    )
    old_cache.flush()
    old_name = f"{old_clock.date().isoformat()}.jsonl.gz"
    assert (cache_path / old_name).exists()

    current_job = _job(5)
    current_cache = DnsClassificationCache(cache_path, now=NOW)
    assert current_cache.observe_job_log(
        job=current_job,
        pipeline="amd-ci",
        build_number=12002,
        log_text="no resolver failure",
    )
    current_cache.flush()
    assert not (cache_path / old_name).exists()
    assert {path.name for path in cache_path.iterdir()} == {
        f"{NOW.date().isoformat()}.jsonl.gz"
    }

    yesterday = NOW - timedelta(days=1)
    assert current_cache.observe_job_log(
        job=_job(7, finished_at=yesterday),
        pipeline="amd-ci",
        build_number=12004,
        log_text="no resolver failure",
    )
    current_cache.flush()
    shard_sizes = {path.name: path.stat().st_size for path in cache_path.iterdir()}
    total_limit = max(shard_sizes.values())
    monkeypatch.setattr(cache_module, "MAX_COMPRESSED_TOTAL_BYTES", total_limit)
    bounded = current_cache.flush()
    assert bounded["compressed_bytes"] <= total_limit
    assert {path.name for path in cache_path.iterdir()} == {
        f"{NOW.date().isoformat()}.jsonl.gz"
    }

    too_small = tmp_path / "too-small"
    limited = DnsClassificationCache(too_small, now=NOW)
    assert limited.observe_job_log(
        job=_job(6),
        pipeline="amd-ci",
        build_number=12003,
        log_text="no resolver failure",
    )
    monkeypatch.setattr(cache_module, "MAX_COMPRESSED_SHARD_BYTES", 32)
    pressure = limited.flush()
    assert pressure == {"shards": 0, "classifications": 0, "compressed_bytes": 0}
    assert list(too_small.iterdir()) == []


def test_malformed_optional_cache_is_discarded_before_dns_collection(tmp_path):
    cache_path = tmp_path / cache_module.CACHE_DIRECTORY_NAME
    cache_path.mkdir()
    (cache_path / "2026-08-20.jsonl.gz").write_bytes(b"raw log, not gzip")

    with pytest.raises(DnsClassificationCacheError, match="gzip"):
        DnsClassificationCache(cache_path, now=NOW)

    class Discovery:
        calls = 0

        def discover_builds(self, *args, **kwargs):
            self.calls += 1
            return []

    client = Discovery()
    payload = collector.collect(
        client=client,
        state_path=tmp_path / "state.json.gz",
        output_path=tmp_path / "dns.json",
        classification_cache_path=cache_path,
        now=NOW,
    )

    assert client.calls == 2
    assert payload["coverage"]["scanned_jobs"] == 0
    assert cache_path.is_dir()
    assert list(cache_path.iterdir()) == []


def test_interrupted_staged_shard_is_safely_reset_as_a_cache_miss(tmp_path):
    cache_path = tmp_path / cache_module.CACHE_DIRECTORY_NAME
    cache_path.mkdir()
    staged = cache_path / ".2026-08-20.jsonl.gz.12345.tmp"
    staged.write_bytes(b"partial private cache")

    cache, rejected = load_optional_dns_classification_cache(cache_path, now=NOW)

    assert rejected is True
    assert cache is not None
    assert len(cache) == 0
    assert list(cache_path.iterdir()) == []


def test_same_day_future_row_is_rejected_at_the_wall_clock_boundary(tmp_path):
    cache_path = tmp_path / cache_module.CACHE_DIRECTORY_NAME
    future = NOW + timedelta(hours=1)
    job = _job(13, finished_at=future)
    cache = DnsClassificationCache(cache_path, now=future)
    assert cache.observe_job_log(
        job=job,
        pipeline="amd-ci",
        build_number=12001,
        log_text="ordinary successful output with no DNS resolver errors",
    )
    cache.flush()

    with pytest.raises(DnsClassificationCacheError, match="future row"):
        DnsClassificationCache(cache_path, now=NOW)


def test_broken_dns_cache_root_symlink_is_rejected_without_deletion(tmp_path):
    target = tmp_path / "missing-private-cache"
    cache_path = tmp_path / cache_module.CACHE_DIRECTORY_NAME
    cache_path.symlink_to(target, target_is_directory=True)

    cache, rejected = load_optional_dns_classification_cache(cache_path, now=NOW)

    assert cache is None
    assert rejected is True
    assert cache_path.is_symlink()
    assert not target.exists()


def test_malformed_optional_cache_does_not_fail_core_collection(monkeypatch, tmp_path):
    cache_path = tmp_path / cache_module.CACHE_DIRECTORY_NAME
    github_output = tmp_path / "github-output.txt"
    cache_path.mkdir()
    (cache_path / "2026-08-20.jsonl.gz").write_bytes(b"raw log, not gzip")
    seen_caches = []

    def collect_without_network(
        pipeline,
        days,
        output_dir,
        dry_run=False,
        *,
        dns_classification_cache=None,
        roster_cache_errors=None,
    ):
        seen_caches.append(dns_classification_cache)
        return [], {}

    monkeypatch.setattr(core_collector, "collect_pipeline", collect_without_network)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_ci.py",
            "--output",
            str(tmp_path / "output"),
            "--pipeline",
            "amd",
            "--skip-analysis",
            "--dns-classification-cache",
            str(cache_path),
            "--github-output",
            str(github_output),
        ],
    )

    core_collector.main()

    assert len(seen_caches) == 1
    assert seen_caches[0] is not None
    assert len(seen_caches[0]) == 0
    assert cache_path.is_dir()
    assert list(cache_path.iterdir()) == []
    assert github_output.read_text().splitlines() == [
        "roster_cache_save=true",
        "dns_cache_save=true",
    ]


def test_unexpected_dns_cache_path_disables_upload_without_failing_core(
    monkeypatch, tmp_path
):
    cache_path = tmp_path / cache_module.CACHE_DIRECTORY_NAME
    cache_path.mkdir()
    marker = cache_path / "must-survive.txt"
    marker.write_text("not recognized cache state")
    github_output = tmp_path / "github-output.txt"
    seen_caches = []

    def collect_without_network(
        pipeline,
        days,
        output_dir,
        dry_run=False,
        *,
        dns_classification_cache=None,
        roster_cache_errors=None,
    ):
        seen_caches.append(dns_classification_cache)
        return [], {}

    monkeypatch.setattr(core_collector, "collect_pipeline", collect_without_network)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_ci.py",
            "--output",
            str(tmp_path / "output"),
            "--pipeline",
            "amd",
            "--skip-analysis",
            "--dns-classification-cache",
            str(cache_path),
            "--github-output",
            str(github_output),
        ],
    )

    core_collector.main()

    assert seen_caches == [None]
    assert marker.read_text() == "not recognized cache state"
    assert github_output.read_text().splitlines() == [
        "roster_cache_save=true",
        "dns_cache_save=false",
    ]


def test_dns_flush_failure_disables_upload_without_failing_core(monkeypatch, tmp_path):
    cache_path = tmp_path / cache_module.CACHE_DIRECTORY_NAME
    github_output = tmp_path / "github-output.txt"

    def collect_without_network(
        pipeline,
        days,
        output_dir,
        dry_run=False,
        *,
        dns_classification_cache=None,
        roster_cache_errors=None,
    ):
        return [], {}

    def fail_flush(self, *, now=None):
        raise OSError("simulated DNS cache flush failure")

    monkeypatch.setattr(core_collector, "collect_pipeline", collect_without_network)
    monkeypatch.setattr(DnsClassificationCache, "flush", fail_flush)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_ci.py",
            "--output",
            str(tmp_path / "output"),
            "--pipeline",
            "amd",
            "--skip-analysis",
            "--dns-classification-cache",
            str(cache_path),
            "--github-output",
            str(github_output),
        ],
    )

    core_collector.main()

    assert github_output.read_text().splitlines() == [
        "roster_cache_save=true",
        "dns_cache_save=false",
    ]


def test_roster_final_validation_disables_only_roster_upload(monkeypatch, tmp_path):
    output = tmp_path / "output"
    roster_root = (
        output
        / ".cache"
        / buildkite_cache.NIGHTLY_ROSTER_CACHE_DIR
    )
    roster_root.mkdir(parents=True)
    marker = roster_root / "unexpected.bin"
    marker.write_bytes(b"must not be uploaded")
    github_output = tmp_path / "github-output.txt"

    def collect_without_network(
        pipeline,
        days,
        output_dir,
        dry_run=False,
        *,
        dns_classification_cache=None,
        roster_cache_errors=None,
    ):
        return [], {}

    monkeypatch.setattr(core_collector, "collect_pipeline", collect_without_network)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_ci.py",
            "--output",
            str(output),
            "--pipeline",
            "amd",
            "--skip-analysis",
            "--github-output",
            str(github_output),
        ],
    )

    core_collector.main()

    assert marker.read_bytes() == b"must not be uploaded"
    assert github_output.read_text().splitlines() == [
        "roster_cache_save=false",
        "dns_cache_save=true",
    ]


def test_optional_cache_never_resets_root_or_a_broad_directory(tmp_path):
    cache, rejected = load_optional_dns_classification_cache(Path("/"), now=NOW)
    assert cache is None
    assert rejected is True

    broad = tmp_path / cache_module.CACHE_DIRECTORY_NAME
    broad.mkdir()
    marker = broad / "must-survive.txt"
    marker.write_text("not cache data")
    malformed = broad / "2026-08-20.jsonl.gz"
    malformed.write_bytes(b"not gzip")

    cache, rejected = load_optional_dns_classification_cache(broad, now=NOW)

    assert cache is None
    assert rejected is True
    assert marker.read_text() == "not cache data"
    assert malformed.read_bytes() == b"not gzip"
