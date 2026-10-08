"""The current comparison cannot import retired AMD nightly history."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID

import pytest

from vllm.ci.nightly_latency import (
    JOB_COLUMNS, build_current_nightly_latency, project_public_nightly_latency,
)
from vllm.collect_analytics import summarize_pipeline_builds

NOW = datetime(2026, 10, 8, 20, tzinfo=timezone.utc)


def _iso(clock):
    return clock.isoformat().replace("+00:00", "Z")


def _job(number, platform, minutes, *, group="Basic Models (Other)", shard=None):
    created = NOW - timedelta(days=1000 - number, hours=4)
    job_id = f"{platform}-{number}-{shard if shard is not None else 'single'}"
    name = f":amd: (MI300) {group}" if platform == "amd" else f":nvidia: (H100) {group}"
    job = {
        "type": "script", "id": job_id, "name": name,
        "state": "passed", "started_at": _iso(created),
        "finished_at": _iso(created + timedelta(minutes=minutes)),
        "agent_query_rules": ["queue=amd_mi300_1" if platform == "amd" else "queue=gpu_1"],
    }
    if shard is not None:
        job.update(name=f"{name} {shard}", parallel_group_index=shard, parallel_group_total=2)
    return job


def _build(number, jobs):
    created = NOW - timedelta(days=1000 - number, hours=4)
    return {
        "number": number, "message": "Full CI run - nightly", "branch": "main",
        "state": "passed", "created_at": _iso(created),
        "finished_at": _iso(created + timedelta(hours=3)),
        "web_url": f"https://buildkite.com/vllm/ci/builds/{number}", "jobs": jobs,
    }


def _comparison(raw, **kwargs):
    return build_current_nightly_latency(
        summarize_pipeline_builds("ci", raw), generated_at=_iso(NOW),
        source_available=True, **kwargs,
    )


def test_latest_five_global_nightlies_never_backfill_missing_group():
    builds = [_build(number, [_job(number, "amd", 10), _job(number, "cuda", 5)])
              for number in range(993, 1001)]
    # This group is absent in four of the newest five, but exists in older runs.
    for build in builds:
        if 996 <= build["number"] < 1000:
            build["jobs"] = [_job(build["number"], "amd", 99, group="Other current group")]
    old = _build(900, [_job(900, "amd", 1000)])
    old["web_url"] = "https://buildkite.com/vllm/amd-ci/builds/900"
    old["message"] = "AMD Full CI Run - nightly"
    result = _comparison([old, *builds])
    assert [row["number"] for row in result["cohort"]["nightlies"]] == [1000, 999, 998, 997, 996]
    row = next(row for row in result["rows"] if row["id"] == "basic models (other)")
    assert row["amd"]["sample_count"] == row["upstream"]["sample_count"] == 1
    assert row["amd"]["median_duration_mins"] == 10
    assert row["ratio"] == 2
    assert row["amd"]["samples"][0]["build_number"] == 1000
    assert row["amd"]["interval"]["start"] == _iso(NOW - timedelta(hours=4))


def test_shards_contribute_one_maximum_per_nightly_before_median():
    builds = [
        _build(1000, [_job(1000, "amd", 10, shard=0), _job(1000, "amd", 30, shard=1), _job(1000, "cuda", 20)]),
        _build(999, [_job(999, "amd", 40, shard=0), _job(999, "amd", 60, shard=1), _job(999, "cuda", 10)]),
    ]
    result = _comparison(builds)
    row = result["rows"][0]
    assert row["amd"]["sample_count"] == 2
    assert row["amd"]["median_duration_mins"] == 45  # median(30, 60), not pooled shard median
    assert row["upstream"]["median_duration_mins"] == 15
    assert row["ratio"] == 3
    assert len(row["amd"]["samples"][0]["jobs"]) == 2
    assert all("/ci/builds/" in job["url"] for sample in row["amd"]["samples"] for job in sample["jobs"])


def test_cpu_legacy_and_superseded_jobs_cannot_enter_current_comparison():
    amd = _job(1000, "amd", 5)
    superseded = deepcopy(amd)
    superseded.update(id="superseded", retried_in_job_id=amd["id"], finished_at=_iso(NOW + timedelta(minutes=200)))
    cpu = _job(1000, "cuda", 100)
    cpu.update(name=":computer: (CPU) Basic Models (Other)", agent_query_rules=["queue=cpu_1"])
    result = _comparison([_build(1000, [amd, superseded, cpu])])
    row = result["rows"][0]
    assert row["amd"]["median_duration_mins"] == 5
    assert row["upstream"]["sample_count"] == 0
    assert row["match_status"] == "unmatched"
    assert row["match_reason"]


def test_incomplete_shard_excludes_the_whole_group_nightly_sample():
    jobs = [_job(1000, "amd", 10, shard=0), _job(1000, "amd", 60, shard=1)]
    jobs[1].update(state="running", finished_at=None)
    result = _comparison([_build(1000, jobs)])
    assert result["cohort"]["build_count"] == 1
    assert result["available"] is False
    assert result["rows"] == []


def test_unavailable_fresh_metadata_never_falls_back_to_old_results():
    builds = summarize_pipeline_builds("ci", [_build(1000, [_job(1000, "amd", 10)])])
    result = build_current_nightly_latency(builds, generated_at=_iso(NOW), source_available=False)
    assert result["available"] is False
    assert result["rows"] == []
    assert result["cohort"]["nightlies"] == []


def test_old_nightlies_are_explicitly_stale_even_if_collection_is_fresh():
    result = _comparison([_build(990, [_job(990, "amd", 10)])])
    assert result["available"] is False
    assert result["unavailable_reason"] == "latest_completed_ci_nightly_unavailable_or_stale"
    assert result["rows"] == []


def test_observed_agent_queue_takes_priority_over_requested_hardware():
    job = _job(1000, "cuda", 10)
    job["agent"] = {"meta_data": ["queue=amd_mi300_1"]}
    result = _comparison([_build(1000, [job])])
    assert result["rows"][0]["amd"]["samples"][0]["jobs"][0]["queue"] == "amd_mi300_1"
    assert result["rows"][0]["upstream"]["sample_count"] == 0


def test_named_shards_need_a_shared_exact_step_key_to_join():
    first = _job(1000, "cuda", 10, group="Basic Models (Extra Initialization) Shard 1")
    last = _job(1000, "cuda", 40, group="Basic Models (Extra Initialization) Shard 2")
    first["step"] = last["step"] = {"key": "basic-extra"}
    amd = _job(1000, "amd", 20, group="Basic Models (Extra Initialization)")
    row = _comparison([_build(1000, [first, last, amd])])["rows"][0]
    assert row["match_status"] == "matched"
    assert row["upstream"]["sample_count"] == 1
    assert row["upstream"]["median_duration_mins"] == 40
    assert len(row["upstream"]["samples"][0]["jobs"]) == 2
    # Similar numeric labels from independent steps remain separate groups.
    last["step"] = {"key": "different-step"}
    row = _comparison([_build(1000, [first, last, amd])])["rows"][0]
    assert row["match_status"] == "unmatched"


def test_full_225_group_five_nightly_eight_shard_evidence_fits_losslessly():
    builds = []
    for number in range(996, 1001):
        jobs = []
        for group_index in range(225):
            label = f"Current model group {group_index:03d} with complete parallel shard wall evidence"
            for platform_index, platform in enumerate(("amd", "cuda")):
                for shard in range(8):
                    job = _job(number, platform, 10 + shard, group=label, shard=shard)
                    identity = ((number * 225 + group_index) * 2 + platform_index) * 8 + shard
                    job.update(id=str(UUID(int=identity)), parallel_group_total=8,
                               step={"id": str(UUID(int=number * 450 + group_index * 2 + platform_index))})
                    jobs.append(job)
        builds.append(_build(number, jobs))
    private = _comparison(builds)
    retained = deepcopy(private)
    limit = 7 * 1024 * 1024
    def size(payload):
        return len((json.dumps({"latency": payload}, separators=(",", ":"), ensure_ascii=True) + "\n").encode())
    assert size(private) > limit
    public = project_public_nightly_latency(private, max_bytes=limit)
    assert size(public) <= limit
    assert public["job_columns"] == list(JOB_COLUMNS)
    assert len(public["rows"]) == 225
    assert public["cohort"]["build_count"] == 5
    expanded = deepcopy(public)
    expanded.pop("job_columns")
    for row in expanded["rows"]:
        for side in ("amd", "upstream"):
            assert row[side]["sample_count"] == len(row[side]["samples"]) == 5
            for sample in row[side]["samples"]:
                assert len(sample["jobs"]) == 8
                sample["jobs"] = [dict(zip(JOB_COLUMNS, vector, strict=True)) for vector in sample["jobs"]]
    assert expanded == private == retained


def test_small_latency_keeps_readable_job_evidence_and_oversized_fails_closed():
    private = _comparison([_build(1000, [_job(1000, "amd", 10)])])
    public = project_public_nightly_latency(private, max_bytes=6 * 1024 * 1024)
    assert public == private
    assert "job_columns" not in public
    assert isinstance(public["rows"][0]["amd"]["samples"][0]["jobs"][0], dict)
    retained = deepcopy(private)
    with pytest.raises(RuntimeError, match="preserving the last-known-good generation"):
        project_public_nightly_latency(private, max_bytes=100)
    assert private == retained


def test_native_amd_execution_wrapper_matches_current_cuda_latency_group():
    native = _job(1000, "amd", 20)
    raw_name = "AMD: :amd: (MI355 DPX) FP8 MoE Kernels (mi355_dpx)"
    native.update(name=raw_name, agent_query_rules=["queue=amd_mi355_dpx"])
    result = _comparison([_build(1000, [native, _job(1000, "cuda", 10, group="FP8 MoE Kernels")])])
    row = result["rows"][0]
    assert row["id"] == "fp8 moe kernels"
    assert row["match_status"] == "matched"
    assert row["amd"]["sample_count"] == row["upstream"]["sample_count"] == 1
    assert row["ratio"] == 2
    job = row["amd"]["samples"][0]["jobs"][0]
    assert job["raw_name"] == raw_name
    assert job["queue"] == "amd_mi355_dpx"
    assert job["hardware"] == "mi355"
