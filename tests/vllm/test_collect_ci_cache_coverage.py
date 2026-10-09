"""Regression tests for the cache-coverage check in collect_ci.py.

The AMD nightly build can flip to ``state="passed"`` while one or more
``soft_fail: true`` jobs are still running — the build doesn't wait on
soft-fail jobs to block completion. Concrete incident that motivated this:

    build 7791, job "mi250_1: Basic Models Tests (Other)":
        state=timed_out  soft_failed=true  retries_count=2
        started 12:30 UTC, finished 15:31 UTC (timeout at 3h)

    The previous collector pass ran at ~04:46 UTC (before the job even
    started). It wrote a partial jsonl without this job. When the next
    collector pass eventually ran (with build now state=passed), the old
    cache-skip logic saw ``date in existing_dates and state in
    TERMINAL_STATES`` and skipped. Result: the timed-out job was
    permanently missing, parity_report.json recorded ``amd=None`` for
    ``basic models tests (other)``, and the dashboard's "Failing Tests"
    filter (which requires ``g.amd.failed > 0``) dropped the group
    from the count — 9 shown, 10 actually soft-failed on Buildkite.

These tests exercise the ``_cache_covers_all_jobs`` / ``_cached_job_names``
helpers directly, without hitting Buildkite. The rule being locked in:
for the newest nightly, *cache-skip is only valid if every test job currently
visible in the build has at least one record in the cached jsonl*. Historical
cached builds are trusted; re-fetching old complete Buildkite logs is slow and
can rate-limit hard enough to block publication of the latest snapshot.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from collect_ci import (  # noqa: E402
    _cache_covers_all_jobs,
    _cached_build_numbers,
    _cached_job_ids,
    _cached_job_names,
    _load_cached_results,
    _compact_amd_build_snapshot,
    _completed_result_entries,
    _find_false_normalization_merges,
    _find_missing_parity_groups,
    _extend_parity_side_hardware,
    _is_complete_nightly_build,
    _select_shard_evidence_build,
    _select_latest_complete_evidence_build,
    _shard_catalog_evidence,
    _is_parity_excluded_group,
    _should_verify_cache_coverage,
    _current_scope_results,
    _scope_nightly_build,
    _fetch_build_detail_with_routing_diagnostics,
    _log_ci_routing_conflicts,
    _scoped_result_entries,
    collect_pipeline,
    load_existing_results,
    write_amd_nightly_snapshot,
)
from vllm.ci.models import TEST_RESULT_PARSER_VERSION, TestResult  # noqa: E402
from vllm.ci import reporter as reporter_module  # noqa: E402
from vllm.ci.reporter import prune_old_results  # noqa: E402
from vllm.ci.analyzer import compute_build_summary, _normalize_job_name  # noqa: E402
from vllm.ci import backfill_checkpoint as checkpoint_module  # noqa: E402


def _job(name: str, state: str = "passed", soft_failed: bool = False) -> dict:
    return {
        "type": "script",
        "name": name,
        "state": state,
        "soft_failed": soft_failed,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _record(job_name: str, build_num: int = 7791, job_id: str = "") -> dict:
    return {
        "test_id": f"{job_name}::__passed__",
        "name": "__passed__ (1)",
        "classname": job_name,
        "status": "passed",
        "duration_secs": 1.0,
        "failure_message": "",
        "job_name": job_name,
        "job_id": job_id,
        "step_id": "",
        "build_number": build_num,
        "pipeline": "ci",
        "date": "2026-04-18",
        "parser_version": TEST_RESULT_PARSER_VERSION,
    }


def test_same_ci_cache_is_scoped_before_coverage_and_denominators(tmp_path):
    amd_name, cuda_name = ":amd: (MI300) Current group", ":nvidia: (H100) Current group"
    records = [_record(amd_name, job_id="amd-job"), _record(cuda_name, job_id="cuda-job")]
    path = tmp_path / "2026-04-18_amd.jsonl"
    _write_jsonl(path, records)
    build = {"number": 7791, "state": "passed", "jobs": [
        {**_job(amd_name), "id": "amd-job"},
        {**_job(cuda_name), "id": "cuda-job"},
    ]}
    assert _cache_covers_all_jobs(build, path, "amd", 7791) is True
    rows = _current_scope_results(_load_cached_results(path), "amd")
    assert [row.job_id for row in rows] == ["amd-job"]
    summary = compute_build_summary(build, _load_cached_results(path), "amd")
    assert summary.job_count == summary.unique_test_groups == summary.passed == 1
    assert set(summary.by_hardware) == {"mi300"}


def test_legacy_amd_ci_cache_cannot_satisfy_current_ci_roster(tmp_path):
    name = "mi300_1: Current group"
    record = {**_record(name, job_id="same-job"), "pipeline": "amd-ci"}
    path = tmp_path / "2026-04-18_amd.jsonl"
    _write_jsonl(path, [record])
    build = {"number": 7791, "state": "passed", "jobs": [{**_job(name), "id": "same-job"}]}
    assert _cache_covers_all_jobs(build, path, "amd", 7791) is False
    assert _current_scope_results(_load_cached_results(path), "amd") == []


def test_shared_historical_ci_rows_are_not_double_counted_across_role_shards():
    row = TestResult(**_record(":amd: (MI300) Current group", job_id="amd-job"))
    cuda = TestResult(**_record(":nvidia: (H100) Current group", job_id="cuda-job"))
    entries = [(7791, "2026-04-18", [row]), (7791, "2026-04-18", [row, cuda])]
    assert _scoped_result_entries(entries, "amd") == [(7791, "2026-04-18", [row])]
    assert _scoped_result_entries(entries, "upstream") == [(7791, "2026-04-18", [cuda])]


def test_new_amd_role_reuses_verified_ci_shard_without_refetching_logs(tmp_path):
    amd_name, cuda_name = ":amd: (MI300) Current group", ":nvidia: (H100) Current group"
    results_dir = tmp_path / "test_results"
    _write_jsonl(results_dir / "2026-04-18_upstream.jsonl", [
        _record(amd_name, job_id="amd-job"), _record(cuda_name, job_id="cuda-job"),
    ])
    # The date-keyed old AMD source is ineligible even if its number collides.
    _write_jsonl(results_dir / "2026-04-18_amd.jsonl", [
        {**_record(amd_name, job_id="legacy-job"), "pipeline": "amd-ci"},
    ])
    build = {"number": 7791, "state": "passed", "branch": "main", "created_at": "2026-04-18T06:00:00Z", "jobs": [
        {**_job(amd_name), "id": "amd-job"}, {**_job(cuda_name), "id": "cuda-job"},
    ]}
    source_build = json.loads(json.dumps(build))
    with patch("collect_ci.fetch_nightly_builds", return_value=[build]), patch("collect_ci.fetch_build_detail", return_value=json.loads(json.dumps(build))), patch("collect_ci.parse_job_results") as parser:
        _, results = collect_pipeline("amd", 8, tmp_path, now=datetime(2026, 4, 19, tzinfo=timezone.utc))
    parser.assert_not_called()
    assert [row.job_id for row in results[7791]] == ["amd-job"]
    assert [row.pipeline for row in _load_cached_results(results_dir / "2026-04-18_amd.jsonl")] == ["ci"]
    # Main collection migrates AMD first, retaining the shared source until its
    # exact AMD attempts have been copied; CUDA then cleans its physical shard.
    upstream = results_dir / "2026-04-18_upstream.jsonl"
    assert {row.job_id for row in _load_cached_results(upstream)} == {"amd-job", "cuda-job"}
    with patch("collect_ci.fetch_nightly_builds", return_value=[json.loads(json.dumps(source_build))]), patch("collect_ci.fetch_build_detail", return_value=json.loads(json.dumps(source_build))), patch("collect_ci.parse_job_results") as parser:
        _, results = collect_pipeline("upstream", 8, tmp_path, now=datetime(2026, 4, 19, tzinfo=timezone.utc))
    parser.assert_not_called()
    assert [row.job_id for row in results[7791]] == ["cuda-job"]
    assert [row.job_id for row in _load_cached_results(upstream)] == ["cuda-job"]
    assert [row.job_id for row in _load_cached_results(results_dir / "2026-04-18_amd.jsonl")] == ["amd-job"]


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("side,expected_job", [("amd", "amd-job"), ("upstream", "cuda-job")])
def test_warm_mixed_ci_cache_is_persisted_as_exact_role_without_log_refetch(
    tmp_path, historical, side, expected_job,
):
    names = {"amd-job": ":amd: (MI300) Current group", "cuda-job": ":nvidia: (H100) Current group",
             "cpu-job": ":computer: (CPU) CPU tests"}
    records = [_record(name, job_id=job_id) for job_id, name in names.items()]
    # Repeated test observations in one exact attempt must survive projection.
    repeated = {**next(row for row in records if row["job_id"] == expected_job), "name": "case[param]", "test_id": "case[param]"}
    records.extend([repeated, dict(repeated)])
    expected = [TestResult(**row).to_dict() for row in records if row["job_id"] == expected_job]
    results_dir = tmp_path / "test_results"
    path = results_dir / f"2026-04-18_{side}.jsonl"
    _write_jsonl(path, records)
    build = {"number": 7791, "state": "passed", "branch": "main", "created_at": "2026-04-18T06:00:00Z",
             "jobs": [{**_job(name), "id": job_id} for job_id, name in names.items()]}
    builds = [build]
    if historical:
        newer = {**build, "number": 7792, "created_at": "2026-04-19T06:00:00Z"}
        builds.append(newer)
        _write_jsonl(results_dir / f"2026-04-19_{side}.jsonl", [
            {**_record(names[expected_job], build_num=7792, job_id=expected_job), "date": "2026-04-19"},
        ])
    clock = datetime(2026, 4, 20, tzinfo=timezone.utc)
    prune_old_results(results_dir, max_days=90, now=clock)
    old_retention = (results_dir / "retention.json").read_bytes()
    def detail(_side, number):
        return json.loads(json.dumps(next(row for row in builds if row["number"] == number)))
    with patch("collect_ci.fetch_nightly_builds", return_value=builds), patch("collect_ci.fetch_build_detail", side_effect=detail), patch("collect_ci.parse_job_results") as parser:
        _, results = collect_pipeline(side, 8, tmp_path, now=clock,
                                      backfill_checkpoint_dir=tmp_path / "checkpoint")
    parser.assert_not_called()
    assert [row.to_dict() for row in results[7791]] == expected
    assert [json.loads(line) for line in path.read_text().splitlines()] == expected
    assert (results_dir / "retention.json").read_bytes() != old_retention
    reporter_module.validate_result_retention(results_dir)
    checkpoint = tmp_path / "checkpoint" / "test_results" / path.name
    assert checkpoint.read_bytes() == path.read_bytes()


@pytest.mark.parametrize("side,expected_job", [("amd", "amd-job"), ("upstream", "cuda-job")])
def test_shared_ci_build_logs_are_collected_for_one_hardware_side(tmp_path, side, expected_job):
    names = {"amd-job": ":amd: (MI300) Current group", "cuda-job": ":nvidia: (H100) Current group", "cpu-job": ":computer: (CPU) CPU tests"}
    build = {"number": 7791, "state": "passed", "branch": "main", "created_at": "2026-04-18T06:00:00Z",
             "jobs": [{**_job(name), "id": job_id, "raw_log_url": "https://example.invalid/log"} for job_id, name in names.items()]}
    def parse(job, number, pipeline, date):
        record = _record(job["name"], build_num=number, job_id=job["id"])
        record.update(pipeline=pipeline, date=date)
        return [TestResult(**record)]
    with patch("collect_ci.fetch_nightly_builds", return_value=[build]), patch("collect_ci.fetch_build_detail", return_value=json.loads(json.dumps(build))), patch("collect_ci.parse_job_results", side_effect=parse) as parser:
        builds, results = collect_pipeline(side, 8, tmp_path, now=datetime(2026, 4, 19, tzinfo=timezone.utc))
    assert parser.call_count == 1
    assert [row.job_id for row in results[7791]] == [expected_job]
    assert [job["id"] for job in builds[0]["jobs"]] == [expected_job]
    assert all(row.pipeline == "ci" for row in results[7791])


@pytest.mark.parametrize("side,job_count", [("amd", 293), ("upstream", 317)])
def test_failed_publication_checkpoint_reuses_exact_current_retry_roster(
    tmp_path, side, job_count,
):
    prefix = ":amd: (MI300)" if side == "amd" else ":nvidia: (H100)"
    queue = "amd_mi300_1" if side == "amd" else "nvidia_h100"
    records = [_record(f"{prefix} Group {index}", job_id=f"current-{index}")
               for index in range(job_count)]
    previous = [{**row, "job_id": f"old-{index}"} for index, row in enumerate(records)]
    results_dir = tmp_path / "test_results"
    path = results_dir / f"2026-04-18_{side}.jsonl"
    _write_jsonl(path, previous)
    clock = datetime(2026, 4, 20, tzinfo=timezone.utc)
    prune_old_results(results_dir, max_days=90, now=clock)
    old_bytes = path.read_bytes()
    private = tmp_path / "checkpoint"
    source = tmp_path / "parsed" / path.name
    _write_jsonl(source, records)
    checkpoint_module.record_complete_shard(private, source)
    # Restore must preserve the current public generation until job identities
    # have been checked against a freshly fetched Buildkite roster.
    assert checkpoint_module.restore_complete_shards(private, results_dir) == 0
    assert path.read_bytes() == old_bytes
    build = {
        "number": 7791, "state": "passed", "branch": "main",
        "created_at": "2026-04-18T06:00:00Z", "commit": "a" * 40,
        "jobs": [{**_job(row["job_name"]), "id": row["job_id"], "agent_queue": queue}
                 for row in records],
    }
    with (
        patch("collect_ci.fetch_nightly_builds", return_value=[json.loads(json.dumps(build))]),
        patch("collect_ci.fetch_build_detail", return_value=json.loads(json.dumps(build))) as detail,
        patch("collect_ci.parse_job_results", side_effect=AssertionError("logs must be reused")) as parser,
    ):
        _, collected = collect_pipeline(side, 8, tmp_path, now=clock,
                                        backfill_checkpoint_dir=private)
    detail.assert_called_once_with(side, 7791)
    parser.assert_not_called()
    expected_ids = {row["job_id"] for row in records}
    assert {row.job_id for row in collected[7791]} == expected_ids
    assert {row.job_id for row in _load_cached_results(path)} == expected_ids
    assert path.read_bytes() != old_bytes
    reporter_module.validate_result_retention(results_dir)
    assert checkpoint_module.validate(private)["shards"] == 1
    assert (private / checkpoint_module.SHARD_DIR / path.name).read_bytes() == path.read_bytes()


@pytest.mark.parametrize("mismatch", ["attempt", "parser", "build", "checksum", "missing_id"])
def test_private_checkpoint_mismatch_refetches_instead_of_promoting(tmp_path, mismatch):
    name = ":amd: (MI300) Engine tests"
    old = _record(name, job_id="old-attempt")
    current = _record(name, job_id="current-attempt")
    results_dir = tmp_path / "test_results"
    path = results_dir / "2026-04-18_amd.jsonl"
    _write_jsonl(path, [old])
    clock = datetime(2026, 4, 20, tzinfo=timezone.utc)
    prune_old_results(results_dir, max_days=90, now=clock)
    candidate = dict(current)
    if mismatch == "attempt":
        candidate["job_id"] = "stale-private-attempt"
    elif mismatch == "parser":
        candidate["parser_version"] = TEST_RESULT_PARSER_VERSION - 1
    elif mismatch == "build":
        candidate["build_number"] -= 1
    elif mismatch == "missing_id":
        candidate["job_id"] = ""
    private = tmp_path / "checkpoint"
    source = tmp_path / "parsed" / path.name
    _write_jsonl(source, [candidate])
    checkpoint_module.record_complete_shard(private, source)
    if mismatch == "checksum":
        cached = private / checkpoint_module.SHARD_DIR / path.name
        cached.write_bytes(cached.read_bytes() + b"\n")
    build = {
        "number": 7791, "state": "passed", "branch": "main",
        "created_at": "2026-04-18T06:00:00Z",
        "jobs": [{**_job(name), "id": "current-attempt", "agent_queue": "amd_mi300_1"}],
    }
    with (
        patch("collect_ci.fetch_nightly_builds", return_value=[json.loads(json.dumps(build))]),
        patch("collect_ci.fetch_build_detail", return_value=json.loads(json.dumps(build))),
        patch("collect_ci.parse_job_results", return_value=[TestResult(**current)]) as parser,
    ):
        _, collected = collect_pipeline("amd", 8, tmp_path, now=clock,
                                        backfill_checkpoint_dir=private)
    parser.assert_called_once()
    assert [row.job_id for row in collected[7791]] == ["current-attempt"]
    assert [row.job_id for row in _load_cached_results(path)] == ["current-attempt"]
    reporter_module.validate_result_retention(results_dir)


def test_private_checkpoint_requires_fresh_roster_before_superseding_public_attempts(tmp_path):
    name = "amd_mi300_1: :amd: (MI300) Engine tests"
    old = _record(name, job_id="old-attempt")
    path = tmp_path / "test_results" / "2026-04-18_amd.jsonl"
    _write_jsonl(path, [old])
    clock = datetime(2026, 4, 20, tzinfo=timezone.utc)
    prune_old_results(path.parent, max_days=90, now=clock)
    private = tmp_path / "checkpoint"
    source = tmp_path / "parsed" / path.name
    _write_jsonl(source, [{**old, "job_id": "private-attempt"}])
    checkpoint_module.record_complete_shard(private, source)
    private_bytes = (private / checkpoint_module.SHARD_DIR / path.name).read_bytes()
    # A populated restored metadata roster alone cannot authorize replacing
    # the published attempt with a different private parsed generation.
    build = {"number": 7791, "state": "passed", "branch": "main",
             "created_at": "2026-04-18T06:00:00Z", "jobs": [
                 {**_job(name), "id": "old-attempt", "agent_queue": "amd_mi300_1"},
             ]}
    with (
        patch("collect_ci.fetch_nightly_builds", return_value=[json.loads(json.dumps(build))]),
        patch("collect_ci.fetch_build_detail", side_effect=RuntimeError("metadata unavailable")),
        patch("collect_ci.parse_job_results") as parser,
    ):
        _, collected = collect_pipeline("amd", 8, tmp_path, now=clock,
                                        backfill_checkpoint_dir=private)
    parser.assert_not_called()
    assert [row.job_id for row in collected[7791]] == ["old-attempt"]
    assert [row.job_id for row in _load_cached_results(path)] == ["old-attempt"]
    assert (private / checkpoint_module.SHARD_DIR / path.name).read_bytes() == private_bytes


def test_private_completed_checkpoint_cannot_promote_current_running_retry(tmp_path):
    name = ":amd: (MI300) Engine tests"
    records = [_record(name, job_id="old-attempt")]
    path = tmp_path / "test_results" / "2026-04-18_amd.jsonl"
    _write_jsonl(path, records)
    clock = datetime(2026, 4, 20, tzinfo=timezone.utc)
    prune_old_results(path.parent, max_days=90, now=clock)
    private = tmp_path / "checkpoint"
    checkpoint_module.record_complete_shard(private, path)
    previous_private = (private / checkpoint_module.SHARD_DIR / path.name).read_bytes()
    build = {"number": 7791, "state": "running", "branch": "main",
             "created_at": "2026-04-18T06:00:00Z", "jobs": [
                 {**_job(name, state="running"), "id": "running-retry", "agent_queue": "amd_mi300_1"},
             ]}
    with (
        patch("collect_ci.fetch_nightly_builds", return_value=[json.loads(json.dumps(build))]),
        patch("collect_ci.fetch_build_detail", return_value=json.loads(json.dumps(build))),
        patch("collect_ci.parse_job_results") as parser,
    ):
        _, collected = collect_pipeline("amd", 8, tmp_path, now=clock,
                                        backfill_checkpoint_dir=private)
    assert collected == {}
    parser.assert_not_called()
    assert not path.exists()
    assert (private / checkpoint_module.SHARD_DIR / path.name).read_bytes() == previous_private


@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("decorated,observed", [("MI250", "mi300_1"), ("MI300", "mi250_1")])
def test_exact_observed_queue_controls_cold_and_warm_runtime_hardware(
    tmp_path, warm, decorated, observed,
):
    # The first pair reproduces the conflicting Torch ABI display decorator
    # from current nightly 93523; routing comes only from this exact roster.
    name = f":amd: ({decorated}) Torch Stable ABI Audit"
    record = _record(name, job_id="01a11a1e-c3f9-442e-ab12-5c2cb2104127")
    result = TestResult(**record)
    queue = f"amd_{observed}"
    job = {**_job(name), "id": result.job_id, "raw_log_url": "https://example.invalid/log",
           "agent": {"meta_data": [f"queue={queue}"]},
           "agent_query_rules": [f"queue=amd_{decorated.lower()}_1"]}
    build = {"number": 7791, "state": "passed", "branch": "main",
             "created_at": "2026-04-18T06:00:00Z", "jobs": [job]}
    results_dir = tmp_path / "test_results"
    path = results_dir / "2026-04-18_amd.jsonl"
    clock = datetime(2026, 4, 19, tzinfo=timezone.utc)
    if warm:
        _write_jsonl(path, [record])
        prune_old_results(results_dir, max_days=90, now=clock)
    with (
        patch("collect_ci.fetch_nightly_builds", return_value=[build]),
        patch("collect_ci.fetch_build_detail", return_value=json.loads(json.dumps(build))) as detail,
        patch("collect_ci.parse_job_results", return_value=[result]) as parser,
    ):
        builds, rows = collect_pipeline("amd", 8, tmp_path, now=clock,
                                       backfill_checkpoint_dir=tmp_path / "checkpoint")
    assert detail.call_count == 1
    assert parser.call_count == (0 if warm else 1)
    expected = {**result.to_dict(), "job_name": f"{queue}: {name}"}
    assert [row.to_dict() for row in rows[7791]] == [expected]
    assert [row.to_dict() for row in _load_cached_results(path)] == [expected]
    reporter_module.validate_result_retention(results_dir)
    assert (tmp_path / "checkpoint" / "test_results" / path.name).read_bytes() == path.read_bytes()
    summary = compute_build_summary(builds[0], rows[7791], "amd")
    assert summary.unique_test_groups == summary.test_groups_passing_or == 1
    assert summary.pass_rate == 1
    assert set(summary.by_hardware) == {observed.split("_", 1)[0]}
    assert summary.by_hardware[observed.split("_", 1)[0]]["groups"] == 1


def test_verified_cpu_attempt_is_removed_from_warm_amd_cache_without_log_refetch(tmp_path):
    gpu_name = ":amd: (MI300) GPU group"
    stale_cpu_label = ":amd: (MI250) Torch Stable ABI Audit"
    path = tmp_path / "test_results" / "2026-04-18_amd.jsonl"
    _write_jsonl(path, [_record(gpu_name, job_id="gpu"), _record(stale_cpu_label, job_id="cpu")])
    build = {"number": 7791, "state": "passed", "branch": "main",
             "created_at": "2026-04-18T06:00:00Z", "jobs": [
                 {**_job(gpu_name), "id": "gpu", "agent_query_rules": ["queue=amd_mi300_1"]},
                 {**_job(stale_cpu_label), "id": "cpu", "agent_query_rules": ["queue=amd-cpu"]},
             ]}
    with (
        patch("collect_ci.fetch_nightly_builds", return_value=[build]),
        patch("collect_ci.fetch_build_detail", return_value=json.loads(json.dumps(build))) as detail,
        patch("collect_ci.parse_job_results") as parser,
    ):
        builds, results = collect_pipeline("amd", 8, tmp_path,
                                          now=datetime(2026, 4, 19, tzinfo=timezone.utc))
    assert detail.call_count == 1
    parser.assert_not_called()
    assert [row.job_id for row in results[7791]] == ["gpu"]
    assert [row.job_id for row in _load_cached_results(path)] == ["gpu"]
    summary = compute_build_summary(builds[0], results[7791], "amd")
    assert summary.unique_test_groups == summary.passed == 1
    assert set(summary.by_hardware) == {"mi300"}


@pytest.mark.parametrize("warm", [False, True])
def test_exact_gh200_route_preserves_cold_and_warm_failure_evidence(tmp_path, warm):
    # Current CI93523 has this undecorated CUDA label on gh200_queue. Label
    # scope alone used to drop its parsed result after every successful fetch.
    name = "GH200 Test"
    record = {**_record(name, job_id="current-gh200"), "status": "failed"}
    original = TestResult(**record)
    build = {"number": 7791, "state": "failed", "branch": "main",
             "created_at": "2026-04-18T06:00:00Z", "jobs": [
                 {**_job(name, state="failed", soft_failed=True), "id": original.job_id,
                  "agent": {"meta_data": ["queue=gh200_queue"]},
                  "raw_log_url": "https://example.invalid/log"},
             ]}
    results_dir = tmp_path / "test_results"
    path = results_dir / "2026-04-18_upstream.jsonl"
    clock = datetime(2026, 4, 19, tzinfo=timezone.utc)
    if warm:
        _write_jsonl(path, [record])
        prune_old_results(results_dir, max_days=90, now=clock)
    private = tmp_path / "checkpoint"
    with (
        patch("collect_ci.fetch_nightly_builds", return_value=[json.loads(json.dumps(build))]),
        patch("collect_ci.fetch_build_detail", return_value=json.loads(json.dumps(build))) as detail,
        patch("collect_ci.parse_job_results", return_value=[original]) as parser,
    ):
        builds, rows = collect_pipeline("upstream", 8, tmp_path, now=clock,
                                       backfill_checkpoint_dir=private)
    detail.assert_called_once_with("upstream", 7791)
    assert parser.call_count == (0 if warm else 1)
    expected = {**original.to_dict(), "job_name": ":nvidia: (GH200) GH200 Test"}
    assert [row.to_dict() for row in rows[7791]] == [expected]
    assert [row.to_dict() for row in _load_cached_results(path)] == [expected]
    assert _normalize_job_name(rows[7791][0].job_name) == _normalize_job_name(name)
    assert _current_scope_results(rows[7791], "upstream", builds[0]) == rows[7791]
    assert _scoped_result_entries([(7791, "2026-04-18", rows[7791])], "upstream") == [
        (7791, "2026-04-18", rows[7791]),
    ]
    summary = compute_build_summary(builds[0], rows[7791], "upstream")
    assert summary.unique_test_groups == summary.failed == 1
    assert summary.test_groups_passing_or == summary.test_groups_passing_all == 0
    assert summary.pass_rate == 0
    assert set(summary.by_hardware) == {"gh200"}
    assert summary.by_hardware["gh200"]["groups"] == 1
    assert summary.by_hardware["gh200"]["failed"] == 1
    reporter_module.validate_result_retention(results_dir)
    assert (private / checkpoint_module.SHARD_DIR / path.name).read_bytes() == path.read_bytes()
    assert _cache_covers_all_jobs(builds[0], path, "upstream", 7791)

    # A failed publication restores the old public attempt next time. The
    # complete private GH200 row must now pass strict exact-attempt coverage
    # and avoid re-fetching every CUDA log in that nightly.
    _write_jsonl(path, [{**record, "job_id": "old-gh200"}])
    prune_old_results(results_dir, max_days=90, now=clock, allow_generation_change=True)
    with (
        patch("collect_ci.fetch_nightly_builds", return_value=[json.loads(json.dumps(build))]),
        patch("collect_ci.fetch_build_detail", return_value=json.loads(json.dumps(build))) as detail,
        patch("collect_ci.parse_job_results", side_effect=AssertionError("logs must be reused")) as parser,
    ):
        _, reused = collect_pipeline("upstream", 8, tmp_path, now=clock,
                                    backfill_checkpoint_dir=private)
    detail.assert_called_once_with("upstream", 7791)
    parser.assert_not_called()
    assert [row.to_dict() for row in reused[7791]] == [expected]
    assert _cache_covers_all_jobs(builds[0], path, "upstream", 7791)


@pytest.mark.parametrize("queue", ["amd_mi300_1", "intel-cpu", "future-unrecognized-pool"])
def test_non_cuda_route_cannot_promote_undecorated_gh200_label(queue):
    row = TestResult(**_record("GH200 Test", job_id="job"))
    build = {"number": 7791, "jobs": [
        {**_job(row.job_name), "id": row.job_id, "agent_queue": queue},
    ]}
    _scope_nightly_build(build, "upstream")
    assert _current_scope_results([row], "upstream", build) == []


@pytest.mark.parametrize("foreign", ["job_id", "build_number", "pipeline"])
def test_gh200_route_cannot_be_borrowed_by_foreign_evidence(foreign):
    record = _record("GH200 Test", job_id="current-gh200")
    build = {"number": 7791, "jobs": [
        {**_job(record["job_name"]), "id": record["job_id"], "agent_queue": "gh200_queue"},
    ]}
    record[foreign] = {"job_id": "other-attempt", "build_number": 7792,
                       "pipeline": "amd-ci"}[foreign]
    _scope_nightly_build(build, "upstream")
    assert _current_scope_results([TestResult(**record)], "upstream", build) == []


def test_unrecognized_observed_queue_cannot_invent_a_runtime_hardware_prefix(tmp_path):
    name = ":amd: (MI250) Current group"
    record = _record(name, job_id="job")
    path = tmp_path / "test_results" / "2026-04-18_amd.jsonl"
    _write_jsonl(path, [record])
    build = {"number": 7791, "state": "passed", "branch": "main",
             "created_at": "2026-04-18T06:00:00Z", "jobs": [
                 {**_job(name), "id": "job", "agent_query_rules": ["queue=future-unrecognized-pool"]},
             ]}
    with (
        patch("collect_ci.fetch_nightly_builds", return_value=[build]),
        patch("collect_ci.fetch_build_detail", return_value=json.loads(json.dumps(build))),
        patch("collect_ci.parse_job_results") as parser,
    ):
        _, results = collect_pipeline("amd", 8, tmp_path,
                                     now=datetime(2026, 4, 19, tzinfo=timezone.utc))
    parser.assert_not_called()
    assert [row.job_name for row in results[7791]] == [name]


def test_observed_route_change_replaces_old_prefix_and_is_idempotent():
    name = "mi250_1: AMD: :amd: (MI250) Native group (mi250_1)"
    original = TestResult(**_record(name, job_id="job"))
    build = {"number": 7791, "jobs": [
        {**_job(name), "id": "job", "agent_query_rules": ["queue=amd_mi300_1"]},
    ]}
    _scope_nightly_build(build, "amd")
    once = _current_scope_results([original], "amd", build)
    assert once[0].job_name == "amd_mi300_1: AMD: :amd: (MI250) Native group (mi250_1)"
    assert original.job_name == name
    assert _current_scope_results(once, "amd", build) == once
    assert once[0].test_id == original.test_id
    assert once[0].classname == original.classname


def test_parity_side_hardware_extends_even_when_merged_hardware_already_exists():
    group = {
        "hardware": ["mi300"],
        "amd_hardware": ["mi300"],
        "upstream_hardware": [],
    }

    added = _extend_parity_side_hardware(group, "upstream", {"mi300"})

    assert added == {"mi300"}
    assert group["amd_hardware"] == ["mi300"]
    assert group["upstream_hardware"] == ["mi300"]
    assert group["hardware"] == ["mi300"]


class TestCachedJobNames:
    def test_legacy_results_keep_their_parser_provenance(self, tmp_path):
        path = tmp_path / "2026-04-18_amd.jsonl"
        record = _record("mi300_2: Model Runner V2 Distributed")
        record.pop("parser_version")
        _write_jsonl(path, [record])

        assert _load_cached_results(path)[0].parser_version == 0
        assert load_existing_results(tmp_path)[0][2][0].parser_version == 0

    def test_empty_when_file_missing(self, tmp_path):
        # No cache file means no coverage — the collector must re-fetch.
        names = _cached_job_names(tmp_path / "missing.jsonl", 7791)
        assert names == set()

    def test_returns_distinct_job_names(self, tmp_path):
        path = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(path, [
            _record("mi250_1: LoRA"),
            _record("mi250_1: LoRA"),           # duplicate row — dedupes
            _record("mi250_1: OpenAI API correctness"),
            _record("mi250_1: V1 Sample + Logits"),
        ])
        assert _cached_job_names(path, 7791) == {
            "mi250_1: LoRA",
            "mi250_1: OpenAI API correctness",
            "mi250_1: V1 Sample + Logits",
        }

    def test_ignores_other_build_numbers(self, tmp_path):
        # A date collision between builds must not make the current build
        # look more covered than it actually is.
        path = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(path, [
            _record("mi250_1: LoRA", build_num=7791),
            _record("mi250_1: SomethingElse", build_num=7777),  # different build
        ])
        assert _cached_job_names(path, 7791) == {"mi250_1: LoRA"}

    def test_skips_malformed_lines(self, tmp_path):
        path = tmp_path / "2026-04-18_amd.jsonl"
        path.write_text(
            json.dumps(_record("mi250_1: LoRA")) + "\n"
            "not valid json\n"
            "\n"
            + json.dumps(_record("mi250_1: OpenAI API")) + "\n"
        )
        assert _cached_job_names(path, 7791) == {
            "mi250_1: LoRA",
            "mi250_1: OpenAI API",
        }

    def test_returns_exact_job_attempt_ids(self, tmp_path):
        path = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(path, [
            _record("mi250_1: LoRA", job_id="attempt-a"),
            _record("mi250_1: LoRA", job_id="attempt-a"),
            _record("mi250_1: LoRA", job_id="attempt-b"),
            _record("mi250_1: Other build", build_num=7777, job_id="other"),
        ])

        assert _cached_job_ids(path, 7791) == {"attempt-a", "attempt-b"}

    def test_returns_positive_build_numbers(self, tmp_path):
        path = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(path, [
            _record("mi250_1: LoRA", build_num=7791),
            _record("mi250_1: Other", build_num=7777),
            {"build_number": "bad"},
        ])

        assert _cached_build_numbers(path) == {7777, 7791}


class TestParityCandidateExclusions:
    def test_amd_prefixed_upstream_control_jobs_are_not_parity_groups(self):
        assert _is_parity_excluded_group("amd: engine (1 gpu) (mi325_1)")

    def test_real_gpu_queue_groups_remain_parity_candidates(self):
        assert not _is_parity_excluded_group("mi325_1: engine (1 gpu)")


class TestParityCollectorValidation:
    @staticmethod
    def _result(job_name: str):
        return type("Result", (), {"job_name": job_name})()

    def test_cross_hardware_variants_are_not_false_merges(self):
        results = [
            self._result("mi250_1: Engine (1 GPU)"),
            self._result("mi300_1: Engine (1 GPU)"),
        ]
        assert _find_false_normalization_merges(results) == []

    def test_same_hardware_non_shard_merge_is_reported(self):
        results = [
            self._result("mi250_1: Engine (1 GPU)"),
            self._result("mi250_1: Engine (1 GPU) # duplicate"),
        ]
        false_merges = _find_false_normalization_merges(results)
        assert len(false_merges) == 1
        assert false_merges[0][0:2] == ("mi250", "engine (1 gpu)")

    def test_configured_same_hardware_shards_are_not_false_merges(self):
        results = [
            self._result("mi300_1: LoRA 0"),
            self._result("mi300_1: LoRA 1"),
        ]
        assert _find_false_normalization_merges(results) == []

    def test_missing_group_check_uses_only_supplied_current_cohort(self):
        current = [self._result("mi300_1: Current Group")]
        parity = {"job_groups": [{"name": "current group"}]}
        assert _find_missing_parity_groups(current, parity) == []
        assert _find_missing_parity_groups(current, {"job_groups": []}) == [
            "current group",
        ]


class TestCacheCoversAllJobs:
    def test_only_latest_build_forces_cache_coverage_verification(self):
        assert _should_verify_cache_coverage(8193, 8193) is True
        assert _should_verify_cache_coverage(64187, 64258) is False
        assert _should_verify_cache_coverage(64187, 64258, 64187) is True


    @staticmethod
    def _build(number: int, state: str, job_state: str, commit: str = "a" * 40):
        return {
            "number": number,
            "state": state,
            "commit": commit,
            "created_at": f"2026-08-{number - 100:02d}T09:00:00Z",
            "jobs": [_job("mi300_1: Model tests", state=job_state)],
        }

    def test_soft_job_still_running_makes_terminal_build_provisional(self):
        build = self._build(112, "passed", "running")
        assert not _is_complete_nightly_build(build)

    def test_selects_previous_complete_build_when_latest_is_running(self):
        latest = self._build(112, "running", "running")
        previous = self._build(111, "passed", "passed")
        results = {112: [object()], 111: [object()]}

        selected = _select_latest_complete_evidence_build(
            [latest, previous], results
        )

        assert selected is previous

    def test_shard_catalog_uses_latest_build_as_explicit_provisional_evidence(self):
        running = self._build(112, "running", "running")

        selected, verified_complete = _select_shard_evidence_build([running], {})
        evidence = _shard_catalog_evidence(
            selected,
            verified_complete=verified_complete,
        )

        assert selected is running
        assert evidence == {
            "pipeline": "amd",
            "build_number": 112,
            "build_commit": "a" * 40,
            "build_state": "running",
            "roster_complete": False,
            "result_file": "",
            "job_names": ["mi300_1: Model tests"],
        }

    def test_shard_catalog_evidence_is_present_when_no_build_is_available(self):
        selected, verified_complete = _select_shard_evidence_build([], {})

        assert selected is None
        assert _shard_catalog_evidence(
            selected,
            verified_complete=verified_complete,
        ) == {
            "pipeline": "amd",
            "build_number": 0,
            "build_commit": "",
            "build_state": "unavailable",
            "roster_complete": False,
            "result_file": "",
            "job_names": [],
        }

    def test_shard_catalog_prefers_verified_complete_evidence(self):
        latest = self._build(112, "running", "running")
        previous = self._build(111, "passed", "passed")

        selected, verified_complete = _select_shard_evidence_build(
            [latest, previous],
            {111: [object()]},
        )

        assert selected is previous
        assert verified_complete is True
        assert _shard_catalog_evidence(
            selected,
            verified_complete=verified_complete,
        )["result_file"] == "2026-08-11_amd.jsonl"

    def test_nonterminal_results_are_excluded_from_canonical_analysis(self):
        latest = self._build(112, "running", "running")
        previous = self._build(111, "passed", "passed")
        entries = [
            (111, "2026-08-11", [object()]),
            (112, "2026-08-12", [object()]),
        ]

        assert _completed_result_entries(entries, [latest, previous]) == [entries[0]]

    def test_cache_complete_skips(self, tmp_path):
        # All 3 current jobs are in the cache → cache is complete → True.
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [
            _record("mi250_1: LoRA"),
            _record("mi250_1: OpenAI API correctness"),
            _record("mi250_1: Basic Models Tests (Other)"),
        ])
        build = {
            "state": "passed",
            "jobs": [
                _job("mi250_1: LoRA"),
                _job("mi250_1: OpenAI API correctness"),
                _job("mi250_1: Basic Models Tests (Other)",
                     state="timed_out", soft_failed=True),
            ],
        }
        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is True

    def test_same_name_new_retry_attempt_triggers_refetch(self, tmp_path):
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [
            _record("mi250_1: LoRA", job_id="original-attempt"),
        ])
        build = {
            "number": 7791,
            "state": "passed",
            "jobs": [
                {
                    **_job("mi250_1: LoRA"),
                    "id": "retry-attempt",
                }
            ],
        }

        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is False

    def test_exact_job_attempt_id_allows_cache_skip(self, tmp_path):
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [
            _record("mi250_1: LoRA", job_id="current-attempt"),
        ])
        build = {
            "number": 7791,
            "state": "passed",
            "jobs": [
                {
                    **_job("mi250_1: LoRA"),
                    "id": "current-attempt",
                }
            ],
        }

        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is True

    @pytest.mark.parametrize(
        "retry_state",
        ["expired", "not_run", "skipped", "waiting_failed", "blocked"],
    )
    def test_nonparseable_terminal_retry_evicts_superseded_attempt(
        self,
        tmp_path,
        retry_state,
    ):
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [
            _record("mi250_1: LoRA", job_id="original-attempt"),
        ])
        build = {
            "number": 7791,
            "state": "passed",
            "jobs": [
                {
                    **_job("mi250_1: LoRA", state="failed"),
                    "id": "original-attempt",
                    "retried_in_job_id": "retry-attempt",
                },
                {
                    **_job("mi250_1: LoRA", state=retry_state),
                    "id": "retry-attempt",
                },
            ],
        }

        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is False

        # After the refresh removes the superseded row, a nonparseable
        # terminal attempt is covered without forcing another refetch.
        _write_jsonl(jsonl, [])
        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is True

    def test_cache_missing_soft_fail_timeout_triggers_refetch(self, tmp_path):
        # Exact shape of the build-7791 incident: the cache has the jobs
        # that finished before the cache was written, but NOT the soft-fail
        # that timed out hours later. Must return False so collector
        # re-fetches.
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [
            _record("mi250_1: LoRA"),
            _record("mi250_1: OpenAI API correctness"),
            # NB: "Basic Models Tests (Other)" is absent here
        ])
        build = {
            "state": "passed",
            "jobs": [
                _job("mi250_1: LoRA"),
                _job("mi250_1: OpenAI API correctness"),
                _job("mi250_1: Basic Models Tests (Other)",
                     state="timed_out", soft_failed=True),
            ],
        }
        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is False

    def test_skip_patterns_not_counted(self, tmp_path):
        # bootstrap / docker / build image / upload jobs are filtered from
        # the collector's parse path, so they must not count as "missing"
        # from the cache either — otherwise every cached build would look
        # incomplete and we'd re-fetch on every cron tick.
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [
            _record("mi250_1: LoRA"),
        ])
        build = {
            "state": "passed",
            "jobs": [
                _job("mi250_1: LoRA"),
                _job("bootstrap"),           # skipped
                _job("docker build image"),  # skipped
                _job("upload artifacts"),    # skipped
            ],
        }
        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is True

    def test_active_nonterminal_retry_makes_cache_provisional(self, tmp_path):
        # ``fetch_build_jobs`` excludes superseded and nonterminal attempts
        # from parsing, but the full roster must still be complete before a
        # cached canonical result can be reused.
        from vllm.ci.buildkite_client import fetch_build_jobs

        build = {
            "jobs": [
                {"type": "script", "name": "mi250_1: LoRA",
                 "state": "passed"},
                # Superseded retry — fetch_build_jobs must drop this.
                {"type": "script", "name": "mi250_1: Superseded",
                 "state": "failed",
                 "retried_in_job_id": "abc-123"},
                # Still running — fetch_build_jobs must drop this too.
                {"type": "script", "name": "mi250_1: StillRunning",
                 "state": "running"},
            ],
        }
        surviving = {j["name"] for j in fetch_build_jobs(build)}
        assert surviving == {"mi250_1: LoRA"}

        # Cache coverage alone is insufficient while an active retry remains
        # nonterminal: the canonical JSONL must be invalidated until it ends.
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [_record("mi250_1: LoRA")])
        build["state"] = "passed"
        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is False

    def test_empty_cache_is_incomplete_when_jobs_exist(self, tmp_path):
        jsonl = tmp_path / "2026-04-18_amd.jsonl"  # not created
        build = {"jobs": [_job("mi250_1: LoRA")]}
        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is False

    def test_empty_build_jobs_trusts_cache(self, tmp_path):
        # Pathological but defensive: if the build has no test jobs at all
        # (e.g. a pipeline-upload-only build) the cache trivially covers
        # it. Must not thrash by returning False on an empty set diff.
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [])
        build = {"jobs": []}
        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is True

    def test_fetches_detail_when_jobs_missing_from_summary(self, tmp_path):
        # ``fetch_nightly_builds`` sometimes returns summaries without the
        # ``jobs`` array. The helper must fetch full build detail in that
        # case rather than silently treating "no jobs visible" as covered.
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [_record("mi250_1: LoRA")])
        summary_only_build = {"number": 7791}  # no "jobs" key
        full_detail = {
            "number": 7791,
            "jobs": [
                _job("mi250_1: LoRA"),
                _job("mi250_1: Basic Models Tests (Other)",
                     state="timed_out", soft_failed=True),
            ],
        }
        with patch("collect_ci.fetch_build_detail", return_value=full_detail) as m:
            assert _cache_covers_all_jobs(
                summary_only_build, jsonl, "amd", 7791
            ) is False
            m.assert_called_once_with("amd", 7791)
        assert summary_only_build == {**full_detail, "job_scope": "amd_gpu", "source_pipeline": "ci"}

    def test_api_failure_on_detail_falls_back_to_trusting_cache(self, tmp_path):
        # If Buildkite is flaky we must not make collection fail outright
        # for the rest of the pipeline — next cron tick retries. The helper
        # logs a warning and returns True so the caller uses the cache.
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [_record("mi250_1: LoRA")])
        summary_only_build = {"number": 7791}
        with patch("collect_ci.fetch_build_detail",
                   side_effect=RuntimeError("503 upstream")):
            assert _cache_covers_all_jobs(
                summary_only_build, jsonl, "amd", 7791
            ) is True


class TestCanonicalResultPublication:
    @staticmethod
    def _build(*, state: str, job_state: str) -> dict:
        return {
            "number": 84160,
            "state": state,
            "commit": "a" * 40,
            "created_at": "2026-08-17T06:00:00Z",
            "jobs": [
                {
                    "type": "script",
                    "id": "job-1",
                    "name": "H100: Engine tests",
                    "state": job_state,
                    "retried_in_job_id": None,
                }
            ],
        }

    def test_running_build_does_not_write_partial_daily_jsonl(self, tmp_path):
        summary = self._build(state="failing", job_state="passed")
        detail = json.loads(json.dumps(summary))

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results") as parse_results,
        ):
            builds, results = collect_pipeline("upstream", 8, tmp_path)

        assert builds[0]["number"] == 84160
        assert results == {}
        assert not (tmp_path / "test_results" / "2026-08-17_upstream.jsonl").exists()
        parse_results.assert_not_called()

    def test_byte_limited_floor_skips_old_build_details_on_repeated_runs(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(reporter_module, "TEST_RESULT_STORE_MAX_BYTES", 80)
        monkeypatch.setattr(reporter_module, "TEST_RESULT_SHARD_MAX_BYTES", 100)
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        (results_dir / "2026-08-17_upstream.jsonl").write_bytes(b"x" * 80)
        (results_dir / "2026-08-18_upstream.jsonl").write_bytes(b"x" * 80)
        assert prune_old_results(
            results_dir,
            max_days=365,
            max_total_bytes=80,
            max_shard_bytes=100,
        ) == 1
        summary = self._build(state="passed", job_state="passed")

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch(
                "collect_ci.fetch_build_detail",
                side_effect=AssertionError("old retained-out build was re-fetched"),
            ) as fetch_detail,
            patch("collect_ci.parse_job_results") as parse_results,
        ):
            for _ in range(2):
                _, results = collect_pipeline("upstream", 8, tmp_path)
                assert results == {}

        fetch_detail.assert_not_called()
        parse_results.assert_not_called()

    def test_running_retry_invalidates_its_cached_canonical_jsonl(self, tmp_path):
        summary = self._build(state="running", job_state="running")
        detail = json.loads(json.dumps(summary))
        cached_path = tmp_path / "test_results" / "2026-08-17_upstream.jsonl"
        cached = _record(
            "H100: Engine tests",
            build_num=84160,
            job_id="original-attempt",
        )
        cached["pipeline"] = "ci"
        cached["date"] = "2026-08-17"
        _write_jsonl(cached_path, [cached])

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results") as parse_results,
        ):
            _, results = collect_pipeline("upstream", 8, tmp_path)

        assert results == {}
        assert not cached_path.exists()
        parse_results.assert_not_called()

    def test_terminal_nonparseable_retry_removes_superseded_cache(self, tmp_path):
        summary = self._build(state="passed", job_state="expired")
        summary["jobs"][0]["id"] = "retry-attempt"
        detail = json.loads(json.dumps(summary))
        cached_path = tmp_path / "test_results" / "2026-08-17_upstream.jsonl"
        cached = _record(
            "H100: Engine tests",
            build_num=84160,
            job_id="original-attempt",
        )
        cached["pipeline"] = "ci"
        cached["date"] = "2026-08-17"
        _write_jsonl(cached_path, [cached])

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results") as parse_results,
        ):
            _, results = collect_pipeline("upstream", 8, tmp_path)

        assert results == {}
        assert not cached_path.exists()
        parse_results.assert_not_called()

    def test_terminal_build_with_running_soft_job_stays_provisional(self, tmp_path):
        summary = self._build(state="passed", job_state="running")
        detail = json.loads(json.dumps(summary))

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results") as parse_results,
        ):
            _, results = collect_pipeline("upstream", 8, tmp_path)

        assert results == {}
        assert not (tmp_path / "test_results" / "2026-08-17_upstream.jsonl").exists()
        parse_results.assert_not_called()

    def test_metadata_only_summary_persists_hydrated_roster(self, tmp_path):
        # This starts one second before the roster's last retained UTC day.
        # Discovery and the final hydrated write must use the same frozen
        # clock even when the real test process (or production collection)
        # continues past midnight.
        collection_clock = datetime(
            2026, 9, 1, 23, 59, 59, tzinfo=timezone.utc
        )
        summary = self._build(state="failing", job_state="passed")
        summary.pop("jobs")
        detail = self._build(state="failing", job_state="passed")
        detail["creator"] = {
            "name": "Public Buildkite Name",
            "email": "private@example.invalid",
        }
        detail["env"] = {"BUILD_SECRET": "do-not-cache"}
        detail["meta_data"] = {"tenant": "do-not-cache"}
        detail["jobs"][0].update({
            "agent": {"name": "private-agent", "meta_data": ["host=private"]},
            "command": "export JOB_SECRET=do-not-cache",
            "env": {"JOB_SECRET": "do-not-cache"},
            "future_api_field": {"private": True},
        })

        with (
            patch(
                "collect_ci.fetch_nightly_builds", return_value=[summary]
            ) as fetch_builds,
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results") as parse_results,
        ):
            collect_pipeline(
                "upstream",
                8,
                tmp_path,
                now=collection_clock,
            )

        fetch_builds.assert_called_once_with(
            "upstream",
            days=8,
            cache_dir=tmp_path / ".cache",
            cache_errors=None,
            now=collection_clock,
            advance_cache_clock=False,
        )

        cache_path = (
            tmp_path
            / ".cache"
            / "nightly-rosters-v2"
            / "upstream"
            / "2026-08-17_84160.json"
        )
        payload = json.loads(cache_path.read_text())
        assert payload == {
            "schema_version": 2,
            "build": {
                "number": 84160,
                "created_at": "2026-08-17T06:00:00Z",
                "jobs": [{
                    "type": "script",
                    "id": "job-1",
                    "name": "H100: Engine tests",
                    "state": "passed",
                }],
            },
        }
        serialized = cache_path.read_text()
        for forbidden in (
            "creator", "email", "env", "meta_data", "agent", "command",
            "future_api_field", "do-not-cache", "private-agent",
        ):
            assert forbidden not in serialized
        parse_results.assert_not_called()

    def test_hydrated_roster_write_admits_build_created_during_collection(
        self, tmp_path
    ):
        started_at = datetime(2026, 9, 2, 0, 0, 0, tzinfo=timezone.utc)
        completed_at = datetime(2026, 9, 2, 0, 0, 1, tzinfo=timezone.utc)
        clocks = iter((started_at, completed_at))

        class SequencedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                assert tz is not None
                value = next(clocks)
                return cls.fromtimestamp(value.timestamp(), tz=tz)

        summary = self._build(state="failing", job_state="passed")
        summary.pop("jobs")
        summary["created_at"] = "2026-09-02T00:00:00.500000Z"
        detail = self._build(state="failing", job_state="passed")
        detail["created_at"] = summary["created_at"]

        with (
            patch(
                "collect_ci.fetch_nightly_builds", return_value=[summary]
            ) as fetch_builds,
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results") as parse_results,
            patch("collect_ci.datetime", SequencedDateTime),
        ):
            collect_pipeline("upstream", 8, tmp_path)

        fetch_builds.assert_called_once_with(
            "upstream",
            days=8,
            cache_dir=tmp_path / ".cache",
            cache_errors=None,
            now=started_at,
            advance_cache_clock=True,
        )
        assert (
            tmp_path
            / ".cache"
            / "nightly-rosters-v2"
            / "upstream"
            / "2026-09-02_84160.json"
        ).is_file()
        parse_results.assert_not_called()

    def test_latest_terminal_summary_hydrates_before_cache_coverage(self, tmp_path):
        summary = self._build(state="passed", job_state="passed")
        detail = json.loads(json.dumps(summary))
        detail["jobs"].append(
            {
                "type": "script",
                "id": "job-2",
                "name": "H100: Late soft failure",
                "state": "timed_out",
                "soft_failed": True,
                "retried_in_job_id": None,
            }
        )

        cached_path = tmp_path / "test_results" / "2026-08-17_upstream.jsonl"
        cached = _record("H100: Engine tests", build_num=84160)
        cached["pipeline"] = "ci"
        cached["date"] = "2026-08-17"
        _write_jsonl(cached_path, [cached])

        parsed_by_job = {}
        for job in detail["jobs"]:
            row = _record(job["name"], build_num=84160)
            row["pipeline"] = "ci"
            row["date"] = "2026-08-17"
            if job["id"] == "job-2":
                row["status"] = "failed"
            parsed_by_job[job["id"]] = TestResult(**row)

        def parse_job(job, *_args):
            return [parsed_by_job[job["id"]]]

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch("collect_ci.fetch_build_detail", return_value=detail) as fetch_detail,
            patch("collect_ci.parse_job_results", side_effect=parse_job) as parse_results,
        ):
            _, results = collect_pipeline("upstream", 8, tmp_path)

        fetch_detail.assert_called_once_with("upstream", 84160)
        assert parse_results.call_count == 2
        assert {result.job_name for result in results[84160]} == {
            "H100: Engine tests",
            "H100: Late soft failure",
        }
        published = [json.loads(line) for line in cached_path.read_text().splitlines()]
        assert {row["job_name"] for row in published} == {
            "H100: Engine tests",
            "H100: Late soft failure",
        }

    def test_metadata_only_historical_build_keeps_canonical_evidence(self, tmp_path):
        latest = self._build(state="passed", job_state="passed")
        latest["number"] = 84161
        latest["created_at"] = "2026-08-18T06:00:00Z"
        historical = self._build(state="passed", job_state="passed")

        details = {
            int(latest["number"]): json.loads(json.dumps(latest)),
            int(historical["number"]): json.loads(json.dumps(historical)),
        }
        summaries = []
        for build in (latest, historical):
            summary = json.loads(json.dumps(build))
            summary.pop("jobs")
            summaries.append(summary)

            date = summary["created_at"][:10]
            row = _record(
                "H100: Engine tests",
                build_num=summary["number"],
                job_id="job-1",
            )
            row["pipeline"] = "ci"
            row["date"] = date
            _write_jsonl(
                tmp_path / "test_results" / f"{date}_upstream.jsonl",
                [row],
            )

        def fetch_detail(_pipeline, build_number):
            return details[build_number]

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=summaries),
            patch("collect_ci.fetch_build_detail", side_effect=fetch_detail) as detail_fetch,
            patch("collect_ci.parse_job_results") as parse_results,
        ):
            builds, results = collect_pipeline("upstream", 8, tmp_path)

        assert detail_fetch.call_count == 2
        parse_results.assert_not_called()
        assert set(results) == {84160, 84161}
        assert all(build.get("jobs") for build in builds)
        entries = [
            (build_number, rows[0].date, rows)
            for build_number, rows in sorted(results.items())
        ]
        assert _completed_result_entries(entries, builds) == entries

    def test_terminal_retry_with_same_name_replaces_cached_attempt(self, tmp_path):
        summary = self._build(state="passed", job_state="passed")
        detail = json.loads(json.dumps(summary))
        detail["jobs"][0]["id"] = "retry-attempt"
        cached_path = tmp_path / "test_results" / "2026-08-17_upstream.jsonl"
        cached = _record(
            "H100: Engine tests",
            build_num=84160,
            job_id="original-attempt",
        )
        cached["pipeline"] = "ci"
        cached["date"] = "2026-08-17"
        _write_jsonl(cached_path, [cached])

        replacement = _record(
            "H100: Engine tests",
            build_num=84160,
            job_id="retry-attempt",
        )
        replacement["pipeline"] = "ci"
        replacement["date"] = "2026-08-17"
        parsed = TestResult(**replacement)

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results", return_value=[parsed]) as parse_results,
        ):
            _, results = collect_pipeline("upstream", 8, tmp_path)

        assert results == {84160: [parsed]}
        parse_results.assert_called_once()
        published = json.loads(cached_path.read_text())
        assert published["job_id"] == "retry-attempt"

    def test_parser_upgrade_replaces_cached_results_once(self, tmp_path):
        """Unchanged job IDs must not preserve results that omitted XPASS."""
        from vllm.ci.log_parser import parse_job_results

        build = self._build(state="passed", job_state="passed")
        cached_path = tmp_path / "test_results" / "2026-08-17_upstream.jsonl"
        cached = _record("H100: Engine tests", build_num=84160, job_id="job-1")
        cached.update(pipeline="ci", date="2026-08-17", status="xfailed")
        cached["name"] = "__xfailed__ (1)"
        cached.pop("parser_version")
        _write_jsonl(cached_path, [cached])
        parsed = parse_job_results(
            build["jobs"][0], 84160, "ci", "2026-08-17",
            log_text="=== 1 xfailed, 2 xpassed, 34 warnings in 354.38s ===",
        )

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[build]),
            patch("collect_ci.fetch_build_detail", return_value=build.copy()),
            patch("collect_ci.parse_job_results", return_value=parsed) as parse_results,
        ):
            _, first = collect_pipeline("upstream", 8, tmp_path)
            _, second = collect_pipeline("upstream", 8, tmp_path)

        parse_results.assert_called_once()
        assert first == second == {84160: parsed}
        assert {row.status for row in second[84160]} == {"xpassed", "xfailed"}
        assert all(
            json.loads(line)["parser_version"] == TEST_RESULT_PARSER_VERSION
            for line in cached_path.read_text().splitlines()
        )

    def test_terminal_running_terminal_lifecycle_publishes_only_new_attempt(self, tmp_path):
        cached_path = tmp_path / "test_results" / "2026-08-17_upstream.jsonl"

        def collect(build: dict, parsed_job_id: str | None = None):
            detail = json.loads(json.dumps(build))
            parsed_rows = []
            if parsed_job_id is not None:
                row = _record(
                    "H100: Engine tests",
                    build_num=84160,
                    job_id=parsed_job_id,
                )
                row["pipeline"] = "ci"
                row["date"] = "2026-08-17"
                parsed_rows = [TestResult(**row)]
            with (
                patch("collect_ci.fetch_nightly_builds", return_value=[build]),
                patch("collect_ci.fetch_build_detail", return_value=detail),
                patch("collect_ci.parse_job_results", return_value=parsed_rows),
            ):
                return collect_pipeline("upstream", 8, tmp_path)

        terminal_a = self._build(state="passed", job_state="passed")
        terminal_a["jobs"][0]["id"] = "attempt-a"
        _, first_results = collect(terminal_a, "attempt-a")
        assert first_results[84160][0].job_id == "attempt-a"
        assert json.loads(cached_path.read_text())["job_id"] == "attempt-a"

        running_b = self._build(state="running", job_state="running")
        running_b["jobs"][0]["id"] = "attempt-b"
        _, provisional_results = collect(running_b)
        assert provisional_results == {}
        assert not cached_path.exists()

        terminal_b = self._build(state="passed", job_state="passed")
        terminal_b["jobs"][0]["id"] = "attempt-b"
        _, final_results = collect(terminal_b, "attempt-b")
        assert final_results[84160][0].job_id == "attempt-b"
        assert json.loads(cached_path.read_text())["job_id"] == "attempt-b"

    def test_complete_build_promotes_results_to_daily_jsonl(self, tmp_path):
        summary = self._build(state="passed", job_state="passed")
        detail = json.loads(json.dumps(summary))
        row = _record("H100: Engine tests", build_num=84160)
        row["pipeline"] = "ci"
        row["date"] = "2026-08-17"
        parsed = TestResult(**row)

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results", return_value=[parsed]),
        ):
            _, results = collect_pipeline("upstream", 8, tmp_path)

        assert results == {84160: [parsed]}
        path = tmp_path / "test_results" / "2026-08-17_upstream.jsonl"
        assert path.exists()
        assert json.loads(path.read_text())["build_number"] == 84160


class TestFrozenAmdNightlySnapshot:
    def test_snapshot_keeps_only_matrix_fields_and_strips_pii(self, tmp_path):
        build = {
            "number": 7791,
            "state": "running",
            "branch": "main",
            "commit": "a" * 40,
            "created_at": "2026-04-18T09:00:00Z",
            "message": "Full CI run - nightly",
            "web_url": "https://buildkite.com/vllm/ci/builds/7791",
            "creator": {"name": "Private User", "email": "private@example.com"},
            "jobs": [
                {
                    "type": "script",
                    "id": "job-1",
                    "name": "mi300_1: Engine",
                    "state": "running",
                    "soft_failed": False,
                    "agent_query_rules": [
                        "queue=amd_mi300_1",
                        "agent-name=private-agent",
                    ],
                    "step": {"id": "engine", "key": "private-key"},
                    "agent": {"name": "private-agent"},
                    "raw_log_url": "https://example.invalid/private",
                }
            ],
        }

        compact = _compact_amd_build_snapshot(build)
        assert compact["number"] == 7791
        assert compact["jobs"] == [
            {
                "type": "script",
                "id": "job-1",
                "name": "mi300_1: Engine",
                "state": "running",
                "soft_failed": False,
                "agent_queue": "amd_mi300_1",
                "agent_query_rules": ["queue=amd_mi300_1"],
                "step": {"id": "engine"},
            }
        ]
        serialized = json.dumps(compact)
        assert "private@example.com" not in serialized
        assert "private-agent" not in serialized
        assert "raw_log_url" not in serialized

        path = write_amd_nightly_snapshot(build, tmp_path)
        assert path == tmp_path / ".cache" / "amd_nightly_snapshot.json"
        payload = json.loads(path.read_text())
        assert payload["schema_version"] == 3
        assert payload["pipeline"] == "ci"
        assert payload["build"] == compact
        assert payload["publication_retention"]["job_rows"] == {
            "source": 1,
            "published": 1,
            "omitted": 0,
            "complete_relative_to_source": True,
        }
        assert (
            payload["publication_retention"]["complete_relative_to_source"]
            is True
        )


def _warm_old_queue_roster(tmp_path):
    from vllm.ci import buildkite_client as bk

    name = ":amd: (MI250) Torch Stable ABI Audit"
    old = {
        "number": 7791, "state": "passed", "branch": "main",
        "message": "Full CI run - nightly",
        "created_at": "2026-04-18T06:00:00Z",
        "jobs": [{**_job(name), "id": "torch-job"}],
    }
    latest = {
        "number": 7792, "state": "passed", "branch": "main",
        "message": "Full CI run - nightly",
        "created_at": "2026-04-19T06:00:00Z",
        "jobs": [{**_job(":amd: (MI300) Latest group"), "id": "latest-job",
                  "agent_queue": "amd_mi300_1"}],
    }
    clock = datetime(2026, 4, 20, tzinfo=timezone.utc)
    cache_dir = tmp_path / ".cache"
    bk.write_nightly_build_cache("amd", [old, latest], cache_dir, now=clock)
    cached = bk._load_nightly_build_cache("amd", cache_dir, now=clock)
    assert "agent_queue" not in cached[7791]["jobs"][0]
    results_dir = tmp_path / "test_results"
    _write_jsonl(results_dir / "2026-04-18_amd.jsonl", [_record(name, job_id="torch-job")])
    _write_jsonl(results_dir / "2026-04-19_amd.jsonl", [
        {**_record(latest["jobs"][0]["name"], build_num=7792, job_id="latest-job"),
         "date": "2026-04-19"},
    ])
    prune_old_results(results_dir, max_days=90, now=clock)
    return bk, old, latest, clock


def test_old_queue_incomplete_historical_roster_refreshes_once_without_log_refetch(tmp_path):
    bk, old, latest, clock = _warm_old_queue_roster(tmp_path)
    summaries = [{key: value for key, value in build.items() if key != "jobs"}
                 for build in (latest, old)]
    refreshed = json.loads(json.dumps(old))
    refreshed["jobs"][0].update(
        agent={"meta_data": ["queue=amd_mi300_1"]},
        agent_query_rules=["queue=amd_mi250_1"],
    )

    def detail(_side, number):
        return json.loads(json.dumps(refreshed if number == 7791 else latest))

    with (
        patch.object(bk, "_paginate", side_effect=lambda *_args: json.loads(json.dumps(summaries))),
        patch("collect_ci.fetch_build_detail", side_effect=detail) as hydrate,
        patch("collect_ci.parse_job_results") as parser,
    ):
        builds, rows = collect_pipeline("amd", 8, tmp_path, now=clock,
                                       backfill_checkpoint_dir=tmp_path / "checkpoint")
        assert [call.args[1] for call in hydrate.call_args_list] == [7792, 7791]
        hydrate.reset_mock()
        _, repeated = collect_pipeline("amd", 8, tmp_path, now=clock)
        assert [call.args[1] for call in hydrate.call_args_list] == [7792]
    parser.assert_not_called()
    assert rows[7791] == repeated[7791]
    assert rows[7791][0].job_name == "amd_mi300_1: :amd: (MI250) Torch Stable ABI Audit"
    summary = compute_build_summary(next(build for build in builds if build["number"] == 7791),
                                    rows[7791], "amd")
    assert set(summary.by_hardware) == {"mi300"}
    assert summary.by_hardware["mi300"]["groups"] == 1
    cached = bk._load_nightly_build_cache("amd", tmp_path / ".cache", now=clock)
    assert cached[7791]["jobs"][0]["agent_queue"] == "amd_mi300_1"
    assert "agent" not in cached[7791]["jobs"][0]
    reporter_module.validate_result_retention(tmp_path / "test_results")
    shard = tmp_path / "test_results" / "2026-04-18_amd.jsonl"
    assert (tmp_path / "checkpoint" / "test_results" / shard.name).read_bytes() == shard.read_bytes()


def test_old_queue_incomplete_roster_refresh_failure_preserves_warm_evidence(tmp_path):
    bk, old, latest, clock = _warm_old_queue_roster(tmp_path)
    summaries = [{key: value for key, value in build.items() if key != "jobs"}
                 for build in (latest, old)]
    shard = tmp_path / "test_results" / "2026-04-18_amd.jsonl"
    previous = shard.read_bytes()

    def detail(_side, number):
        if number == 7791:
            raise RuntimeError("metadata unavailable")
        return json.loads(json.dumps(latest))

    with (
        patch.object(bk, "_paginate", side_effect=lambda *_args: json.loads(json.dumps(summaries))),
        patch("collect_ci.fetch_build_detail", side_effect=detail) as hydrate,
        patch("collect_ci.parse_job_results") as parser,
        pytest.raises(RuntimeError, match="queue-incomplete current CI roster.*7791"),
    ):
        collect_pipeline("amd", 8, tmp_path, now=clock)
    assert [call.args[1] for call in hydrate.call_args_list] == [7792, 7791]
    parser.assert_not_called()
    assert shard.read_bytes() == previous


@pytest.mark.parametrize("metadata,explicit,expected_source,expected_queue", [
    ({"queue": "AMD_MI300_1"}, {}, "agent.meta_data.mapping", "amd_mi300_1"),
    (["queue=amd_mi300_1"], {}, "agent.meta_data.tags", "amd_mi300_1"),
    (["queue=amd_mi300_1"], {"agent_queue": "amd_mi355_1"}, "agent_queue", "amd_mi355_1"),
    (["queue=private-routing-secret"], {}, "agent.meta_data.tags", "unrecognized"),
])
def test_fresh_routing_conflict_diagnostic_is_safe_and_preserves_precedence(
    metadata, explicit, expected_source, expected_queue, caplog,
):
    identity = "01a11a1e-c3f9-442e-ab12-5c2cb2104127"
    build = {"number": 93523, "jobs": [{
        "type": "script", "id": identity, "name": "private-label-secret",
        "agent": {"name": "private-agent-secret", "meta_data": metadata},
        "agent_query_rules": ["queue=amd_mi250_1", "hostname=private-host-secret"],
        "env": {"TOKEN": "private-env-secret"}, "raw_log_url": "private-log-secret",
        **explicit,
    }]}
    previous = json.loads(json.dumps(build))
    with patch("collect_ci.fetch_build_detail", return_value=build) as detail:
        returned = _fetch_build_detail_with_routing_diagnostics("amd", 93523)
    detail.assert_called_once_with("amd", 93523)
    assert returned is build and build == previous
    records = [record.message for record in caplog.records
               if record.message.startswith("CI routing ambiguity: ")]
    assert len(records) == 1
    diagnostic = json.loads(records[0].split(": ", 1)[1])
    assert diagnostic["build_number"] == 93523
    assert diagnostic["job_id"] == identity
    assert diagnostic["selected_queue"] == expected_queue
    assert diagnostic["selected_queue_source"] == expected_source
    assert diagnostic["requested_queue"] == "amd_mi250_1"
    assert diagnostic["assigned_requested_conflict"] is True
    assert "private-" not in caplog.text


def test_routing_diagnostic_bounds_multiple_assigned_tags_and_job_records(caplog):
    from collect_ci import _ROUTING_DIAGNOSTIC_LIMIT, _ROUTING_QUEUE_LIST_LIMIT

    queues = [f"amd_mi{family}_{width}" for family in (250, 300, 325, 355) for width in (1, 2, 4, 8)]
    tags = [f"queue={queue}" for queue in queues] + [
        "queue=private-routing-secret", "hostname=private-host-secret",
    ]
    build = {"number": 93523, "jobs": [{
        "type": "script", "id": f"00000000-0000-4000-8000-{index:012x}",
        "agent": {"meta_data": tags}, "agent_query_rules": ["queue=amd_mi250_1"],
    } for index in range(_ROUTING_DIAGNOSTIC_LIMIT + 3)]}
    _log_ci_routing_conflicts(build, "amd")
    records = [record.message for record in caplog.records
               if record.message.startswith("CI routing ambiguity: ")]
    assert len(records) == _ROUTING_DIAGNOSTIC_LIMIT
    for message in records:
        assert len(message) < 1024
        diagnostic = json.loads(message.split(": ", 1)[1])
        assert diagnostic["assigned_queue_tag_count"] == len(queues) + 1
        assert diagnostic["assigned_queues"] == queues[:_ROUTING_QUEUE_LIST_LIMIT]
        assert diagnostic["assigned_queue_list_truncated"] is True
        assert diagnostic["assigned_requested_conflict"] is False
        assert diagnostic["selected_queue"] == "amd_mi250_1"
    assert "private-" not in caplog.text


@pytest.mark.parametrize("number,identity,metadata,requested", [
    (True, "01a11a1e-c3f9-442e-ab12-5c2cb2104127", ["queue=amd_mi300_1"], "amd_mi250_1"),
    (-1, "01a11a1e-c3f9-442e-ab12-5c2cb2104127", ["queue=amd_mi300_1"], "amd_mi250_1"),
    (10**12, "01a11a1e-c3f9-442e-ab12-5c2cb2104127", ["queue=amd_mi300_1"], "amd_mi250_1"),
    (93523, "private-job-secret", ["queue=amd_mi300_1"], "amd_mi250_1"),
    (93523, "01a11a1e-c3f9-442e-ab12-5c2cb2104127", ["queue=amd_mi300_1"], "amd_mi300_1"),
    (93523, "01a11a1e-c3f9-442e-ab12-5c2cb2104127", None, "amd_mi300_1"),
])
def test_routing_diagnostic_ignores_invalid_ids_and_unambiguous_or_cache_only_jobs(
    number, identity, metadata, requested, caplog,
):
    _log_ci_routing_conflicts({"number": number, "jobs": [{
        "type": "script", "id": identity, "agent": {"meta_data": metadata},
        "agent_queue": "amd_mi300_1", "agent_query_rules": [f"queue={requested}"],
    }]}, "amd")
    assert "CI routing ambiguity" not in caplog.text
