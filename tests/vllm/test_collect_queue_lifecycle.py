"""Focused tests for the compact AMD queue lifecycle collector."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from vllm import collect_queue_lifecycle as lifecycle
from vllm.constants import AMD_METRIC_TARGET_QUEUES


NOW = datetime(2026, 8, 11, 20, 0, tzinfo=timezone.utc)


def _queue_ids() -> dict[str, str]:
    return {queue: f"id:{queue}" for queue in AMD_METRIC_TARGET_QUEUES}


def _queue_by_id() -> dict[str, str]:
    return {queue_id: queue for queue, queue_id in _queue_ids().items()}


def _rest_job(
    *,
    uuid: str = "job-1",
    queue: str = "amd_mi300_1",
    cluster_queue_id: str | None = None,
    state: str = "passed",
    runnable_at: str | None = "2026-08-11T18:10:00Z",
    started_at: str | None = "2026-08-11T18:20:00Z",
    finished_at: str | None = "2026-08-11T19:20:00Z",
    rules: list[str] | None = None,
) -> dict:
    return {
        "id": uuid,
        "type": "script",
        "state": state,
        "created_at": "2026-08-11T18:00:00Z",
        "runnable_at": runnable_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "cluster_queue_id": cluster_queue_id or f"id:{queue}",
        "agent_query_rules": rules if rules is not None else [f"queue={queue}"],
        "soft_failed": False,
        "retried": False,
        "retries_count": 0,
        "retry_type": None,
        "retry_source": None,
    }


def _job(
    *,
    uuid: str = "job-1",
    queue: str = "amd_mi300_1",
    created_at: str | None = "2026-08-11T18:00:00Z",
    runnable_at: str | None = "2026-08-11T18:10:00Z",
    started_at: str | None = "2026-08-11T18:20:00Z",
    finished_at: str | None = "2026-08-11T19:20:00Z",
    state: str = "FINISHED",
    passed: bool = True,
    soft_failed: bool = False,
    retried: bool = False,
    is_retry: bool = True,
) -> dict:
    return {
        "uuid": uuid,
        "state": state,
        "createdAt": created_at,
        "runnableAt": runnable_at,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "passed": passed,
        "softFailed": soft_failed,
        "exitStatus": "0" if passed else "1",
        "retried": retried,
        "retriesCount": 1 if is_retry else 0,
        "retryType": "MANUAL" if is_retry else None,
        "retrySource": {"uuid": "raw-predecessor-uuid"} if is_retry else None,
        "clusterQueue": {"id": f"id:{queue}", "uuid": f"uuid:{queue}", "key": queue},
        # Nullable Buildkite build/pipeline metadata is intentionally absent.
    }


def _observations_for(job: dict, *, start: datetime | None = None) -> list[dict]:
    copied = dict(job)
    copied["_cohorts"] = {"created", "finished:2026-08-11"}
    rows, _ = lifecycle.observations_from_jobs(
        {job["uuid"]: copied},
        retention_start=start or NOW - timedelta(days=7),
        end_exclusive=NOW,
    )
    return rows


def _observation(index: int = 1, **overrides) -> dict:
    row = _observations_for(_job(uuid=f"job-{index}"))[0]
    row.update(overrides)
    return row


def _write_previous_generation(
    jobs_path: Path,
    summary_path: Path,
    *,
    provenance: dict,
) -> None:
    segments, ledger = lifecycle.encode_job_segments(
        [_observation()], retention_start=NOW - timedelta(days=7), end_exclusive=NOW
    )
    jobs_path.mkdir()
    for name, payload in segments.items():
        (jobs_path / name).write_bytes(payload)
    summary_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scope": {"queues": list(AMD_METRIC_TARGET_QUEUES)},
                "provenance": {**provenance, "ledger": ledger},
            }
        )
    )


def test_canonical_scope_is_only_requested_families_and_widths():
    assert AMD_METRIC_TARGET_QUEUES == tuple(
        f"amd_mi{family}_{width}" for family in (250, 300, 355) for width in (1, 2, 4, 8)
    )
    assert "amd_mi325_1" not in AMD_METRIC_TARGET_QUEUES
    assert "amd_mi355b_1" not in AMD_METRIC_TARGET_QUEUES


def test_rest_queue_discovery_resolves_exact_target_ids_and_fails_on_missing():
    calls = []

    def complete(path, token, params):
        calls.append((path, token, params))
        return [{"id": f"id:{queue}", "key": queue} for queue in AMD_METRIC_TARGET_QUEUES]

    by_id, coverage = lifecycle.fetch_rest_target_queues("secret", page_fetcher=complete)
    assert by_id == _queue_by_id()
    assert coverage == {"complete": True, "pages": 1, "target_queue_count": 12}
    assert calls == [
        (
            f"/organizations/{lifecycle.BK_ORG}/clusters/{lifecycle.BK_CLUSTER_UUID}/queues",
            "secret",
            {"page": 1, "per_page": 100},
        )
    ]

    def incomplete(path, token, params):
        return [{"id": f"id:{queue}", "key": queue} for queue in AMD_METRIC_TARGET_QUEUES[:-1]]

    with pytest.raises(RuntimeError, match="missing target REST cluster queue IDs"):
        lifecycle.fetch_rest_target_queues("secret", page_fetcher=incomplete)


def test_rest_org_cohort_union_is_paginated_deduplicated_and_documented():
    calls = []
    target_build = {
        # An arbitrary pipeline proves collection is organization-wide rather
        # than constrained by the workload-mapping config.
        "pipeline": {"slug": "some-new-pipeline"},
        "jobs": [_rest_job()],
    }
    empty_builds = [{"jobs": []} for _ in range(99)]

    def fetch(path, token, params):
        calls.append((path, token, dict(params)))
        if "state[]" in params:
            return [target_build]
        if "finished_from" in params:
            return [target_build]
        if params["page"] == 1:
            return [target_build, *empty_builds]
        if params["page"] == 2:
            return [target_build]
        raise AssertionError(f"unexpected cohort request: {params}")

    jobs, coverage = lifecycle.fetch_rest_lifecycle_jobs(
        "secret",
        query_start=NOW - timedelta(days=10),
        query_end=NOW,
        queue_by_id=_queue_by_id(),
        page_fetcher=fetch,
    )

    assert list(jobs) == ["job-1"]
    assert coverage["complete"] is True
    assert coverage["organization_wide"] is True
    assert coverage["cohorts"]["created"]["pages"] == 2
    assert set(coverage["cohorts"]) == {"created", "active", "finished"}
    assert all(path == f"/organizations/{lifecycle.BK_ORG}/builds" for path, _, _ in calls)
    assert all(params["include_retried_jobs"] == "true" for _, _, params in calls)
    assert all(params["include_paused"] == "true" for _, _, params in calls)
    active = next(params for _, _, params in calls if "state[]" in params)
    assert active["state[]"] == [
        "creating",
        "scheduled",
        "running",
        "failing",
        "blocked",
        "canceling",
    ]
    assert active["created_from"] == "2026-08-01T20:00:00Z"
    assert active["created_to"] == "2026-08-11T20:00:00Z"
    finished = next(params for _, _, params in calls if "finished_from" in params)
    assert "finished_to" not in finished
    assert list(coverage["cohorts"])[-1] == "finished"


def test_rest_org_cohort_pagination_cap_fails_closed():
    full_page = [{"jobs": []} for _ in range(lifecycle.REST_PAGE_SIZE)]

    with pytest.raises(RuntimeError, match="created reached the pagination safety cap"):
        lifecycle.fetch_rest_lifecycle_jobs(
            "secret",
            query_start=NOW - timedelta(days=10),
            query_end=NOW,
            queue_by_id=_queue_by_id(),
            max_pages=2,
            page_fetcher=lambda path, token, params: full_page,
        )


def test_incremental_event_window_retains_full_active_parent_horizon():
    calls = []
    event_start = NOW - timedelta(hours=7)
    active_parent_start = NOW - timedelta(
        days=lifecycle.RETENTION_DAYS + lifecycle.PARENT_BUILD_LOOKBACK_DAYS
    )

    def fetch(path, token, params):
        calls.append(dict(params))
        return []

    _, coverage = lifecycle.fetch_rest_lifecycle_jobs(
        "secret",
        query_start=event_start,
        query_end=NOW,
        active_created_from=active_parent_start,
        queue_by_id=_queue_by_id(),
        page_fetcher=fetch,
    )

    created = next(params for params in calls if "created_from" in params and "state[]" not in params)
    active = next(params for params in calls if "state[]" in params)
    finished = next(params for params in calls if "finished_from" in params)
    assert created["created_from"] == lifecycle._utc_iso(event_start)
    assert finished["finished_from"] == lifecycle._utc_iso(event_start)
    assert active["created_from"] == lifecycle._utc_iso(active_parent_start)
    assert active["created_to"] == lifecycle._utc_iso(NOW)
    assert coverage["event_cohort_query_start"] == lifecycle._utc_iso(event_start)
    assert coverage["active_parent_query_start"] == lifecycle._utc_iso(active_parent_start)


def test_rest_active_cohort_is_time_bounded_and_fails_closed_at_cap():
    calls = []
    full_page = [{"jobs": []} for _ in range(lifecycle.REST_PAGE_SIZE)]

    def fetch(path, token, params):
        calls.append(dict(params))
        if "state[]" in params:
            return full_page
        return []

    with pytest.raises(RuntimeError, match="active reached the pagination safety cap"):
        lifecycle.fetch_rest_lifecycle_jobs(
            "secret",
            query_start=NOW - timedelta(days=10),
            query_end=NOW,
            queue_by_id=_queue_by_id(),
            max_pages=2,
            page_fetcher=fetch,
        )

    active_calls = [params for params in calls if "state[]" in params]
    assert len(active_calls) == 2
    assert [params["page"] for params in active_calls] == [1, 2]
    assert all(
        params["created_from"] == "2026-08-01T20:00:00Z"
        and params["created_to"] == "2026-08-11T20:00:00Z"
        for params in active_calls
    )


def test_rest_projection_requires_direct_target_queue_id_and_rejects_conflict():
    jobs = {}
    missing_id = _rest_job()
    missing_id["cluster_queue_id"] = None
    with pytest.raises(RuntimeError, match="lacks direct cluster_queue_id attribution"):
        lifecycle._project_rest_builds([{"jobs": [missing_id]}], _queue_by_id(), jobs)

    conflicting = _rest_job(
        queue="amd_mi300_1",
        cluster_queue_id="id:amd_mi250_1",
    )
    with pytest.raises(RuntimeError, match="conflicts with its explicit queue rule"):
        lifecycle._project_rest_builds([{"jobs": [conflicting]}], _queue_by_id(), jobs)


def test_observation_is_compact_private_and_uses_direct_durations():
    row = _observations_for(_job())[0]
    assert set(row) == {
        "schema_version",
        "job_id",
        "queue",
        "timestamps",
        "durations_seconds",
        "outcome",
        "retry",
    }
    encoded = json.dumps(row)
    for secret_value in (
        "job-1",
        "raw-predecessor-uuid",
        "label",
        "url",
        "branch",
        "commit",
        "pipeline",
        "build",
    ):
        assert secret_value not in encoded
    assert len(row["job_id"]) == 64
    assert row["durations_seconds"] == {"queue_wait": 600.0, "runtime": 3600.0}
    assert row["outcome"] == "passed"
    assert row["retry"] == {"retried": False, "is_retry": True, "retries_count": 1}


def test_ledger_rejects_any_unrecognized_identifying_field():
    leaked = {**_observation(), "label": "private job label"}
    payload = gzip.compress((json.dumps(leaked, separators=(",", ":")) + "\n").encode("utf-8"))
    with pytest.raises(RuntimeError, match="top-level schema is invalid"):
        lifecycle.decode_job_ledger(payload, source="restored branch segment")


def test_positive_rest_retry_count_is_direct_retry_evidence_without_source_or_type():
    rest = _rest_job()
    rest["retries_count"] = 1
    node = lifecycle._rest_job_node(rest, "amd_mi300_1")
    row = _observations_for(node)[0]
    assert row["retry"] == {"retried": False, "is_retry": True, "retries_count": 1}


def test_scheduled_job_is_incoming_without_being_served():
    row = _observations_for(
        _job(state="SCHEDULED", started_at=None, finished_at=None, passed=False)
    )[0]
    metrics = lifecycle._metric_block([row], NOW - timedelta(hours=2), NOW)
    assert metrics["incoming"] == 1
    assert metrics["served"] == 0
    assert metrics["completed"] == 0


def test_late_completion_refresh_merges_and_lands_only_in_completion_bucket():
    active = _observations_for(
        _job(finished_at=None, state="RUNNING", passed=False, is_retry=False)
    )[0]
    completed = _observations_for(
        _job(
            finished_at="2026-08-11T19:20:00Z",
            state="FINISHED",
            passed=False,
            is_retry=False,
        )
    )[0]
    merged = lifecycle.merge_and_prune_jobs(
        [active], [completed], retention_start=NOW - timedelta(days=7), end_exclusive=NOW
    )
    assert len(merged) == 1
    assert merged[0]["outcome"] == "failed"
    assert merged[0]["durations_seconds"]["runtime"] == 3600.0
    completion_hour = lifecycle._metric_block(
        merged,
        datetime(2026, 8, 11, 19, tzinfo=timezone.utc),
        datetime(2026, 8, 11, 20, tzinfo=timezone.utc),
    )
    assert completion_hour["incoming"] == 0
    assert completion_hour["served"] == 0
    assert completion_hour["completed"] == 1


def test_outcome_and_retry_aggregates_remain_completion_consistent():
    outcomes = ["CANCELED", "TIMED_OUT", "EXPIRED", "BROKEN", "SKIPPED"]
    rows = []
    for index, state in enumerate(outcomes, start=1):
        rows.extend(
            _observations_for(
                _job(uuid=f"terminal-{index}", state=state, passed=False, retried=True)
            )
        )
    metrics = lifecycle._metric_block(rows, NOW - timedelta(hours=2), NOW)
    assert metrics["completed"] == 5
    assert metrics["other_outcomes"] == 0
    assert [
        metrics[field] for field in ("canceled", "timed_out", "expired", "broken", "skipped")
    ] == [1] * 5
    assert metrics["retry_attempts_completed"] == 5
    assert metrics["retried_jobs_completed"] == 5


def test_retained_job_filters_each_event_independently_at_retention_boundary():
    retention_start = NOW - timedelta(days=7)
    row = _observations_for(
        _job(
            runnable_at="2026-08-04T19:59:59Z",
            started_at="2026-08-04T20:00:00Z",
            finished_at="2026-08-04T21:00:00Z",
        ),
        start=retention_start,
    )[0]
    summary = lifecycle.build_summary([row], now=NOW, collection=None, previous_provenance={})
    assert summary["coverage"]["event_count"] == 2
    assert summary["coverage"]["observed_start"] == "2026-08-04T20:00:00Z"
    first_hour = summary["hourly"][0]["totals"]
    assert first_hour["incoming"] == 0
    assert first_hour["served"] == 1


def test_partial_first_hour_does_not_count_events_before_retention_start():
    now = NOW + timedelta(minutes=30)
    retention_start = now - timedelta(days=7)
    job = _job(
        uuid="partial-boundary",
        created_at="2026-08-04T19:50:00Z",
        runnable_at="2026-08-04T20:29:59Z",
        started_at="2026-08-04T20:30:00Z",
        finished_at="2026-08-04T21:30:00Z",
    )
    rows, _ = lifecycle.observations_from_jobs(
        {job["uuid"]: job},
        retention_start=retention_start,
        end_exclusive=now,
    )
    summary = lifecycle.build_summary(rows, now=now, collection=None)
    first = summary["hourly"][0]
    assert first["partial"] is True
    assert first["totals"]["incoming"] == 0
    assert first["totals"]["served"] == 1


def test_daily_wait_vectors_cover_each_utc_date_and_bucket_by_started_at():
    retention_start = NOW - timedelta(days=7)
    rows = []
    for job in (
        _job(
            uuid="cross-midnight",
            runnable_at="2026-08-09T23:55:00Z",
            started_at="2026-08-10T00:05:00Z",
            finished_at="2026-08-10T01:05:00Z",
        ),
        _job(
            uuid="same-day",
            runnable_at="2026-08-10T12:00:00Z",
            started_at="2026-08-10T12:05:00Z",
            finished_at="2026-08-10T13:05:00Z",
        ),
        _job(
            uuid="missing-runnable",
            runnable_at=None,
            started_at="2026-08-10T14:00:00Z",
            finished_at="2026-08-10T15:00:00Z",
        ),
        _job(
            uuid="started-before-retention",
            runnable_at="2026-08-04T19:50:00Z",
            started_at="2026-08-04T19:59:00Z",
            finished_at="2026-08-04T20:30:00Z",
        ),
    ):
        rows.extend(_observations_for(job, start=retention_start))

    summary = lifecycle.build_summary(
        list(reversed(rows)), now=NOW, collection=None, previous_provenance={}
    )
    daily = summary["daily_wait_times"]
    assert set(daily) == {"unit", "day_timezone", "attributed_by", "days"}
    assert daily["unit"] == "seconds"
    assert daily["day_timezone"] == "UTC"
    assert daily["attributed_by"] == "timestamps.started_at"
    assert [row["date"] for row in daily["days"]] == [
        "2026-08-04",
        "2026-08-05",
        "2026-08-06",
        "2026-08-07",
        "2026-08-08",
        "2026-08-09",
        "2026-08-10",
        "2026-08-11",
    ]
    by_date = {row["date"]: row for row in daily["days"]}
    assert by_date["2026-08-10"]["served_job_wait_seconds"] == [300.0, 600.0]
    assert by_date["2026-08-10"]["sample_count"] == 2
    assert by_date["2026-08-09"]["served_job_wait_seconds"] == []
    assert by_date["2026-08-09"]["sample_count"] == 0
    assert by_date["2026-08-04"] == {
        "date": "2026-08-04",
        "start": "2026-08-04T20:00:00Z",
        "end_exclusive": "2026-08-05T00:00:00Z",
        "partial": True,
        "sample_count": 0,
        "served_job_wait_seconds": [],
    }
    assert by_date["2026-08-10"]["partial"] is False
    assert by_date["2026-08-11"]["start"] == "2026-08-11T00:00:00Z"
    assert by_date["2026-08-11"]["end_exclusive"] == "2026-08-11T20:00:00Z"
    assert by_date["2026-08-11"]["partial"] is True


def test_summary_separates_complete_api_collection_from_event_exhaustiveness():
    summary = lifecycle.build_summary(
        [_observation()],
        now=NOW,
        collection={
            "complete": True,
            "query_start": "2026-08-01T20:00:00Z",
            "query_end_exclusive": "2026-08-11T20:00:00Z",
            "query_mode": "full_retention_cohort_union",
            "queue_discovery": {"complete": True, "target_queue_count": 12},
            "source_coverage": {"complete": True},
            "timestamp_coverage": {},
        },
    )
    coverage = summary["coverage"]
    assert coverage["complete"] is False
    assert coverage["status"] == "partial_observation"
    assert coverage["api_complete"] is True
    assert coverage["target_queue_scope_complete"] is True
    assert coverage["exact_rolling_window_covered_by_current_query"] is True
    assert all(
        metric["complete"] is False and metric["exact_for_observed_events"] is True
        for metric in coverage["metric_exhaustiveness"].values()
    )
    fields = summary["provenance"]["source_field_contract"]
    assert fields["incoming"] == "builds[].jobs[].runnable_at"
    assert summary["provenance"]["provider"] == ("Buildkite REST organization builds API")
    assert "queues" not in summary["hourly"][0]


def test_deterministic_gzip_handles_measured_7d_volume_well_below_90mib():
    base = _observation()

    def rows():
        for index in range(128_003):
            row = dict(base)
            row["job_id"] = hashlib.sha256(f"volume-{index}".encode()).hexdigest()
            day = datetime(2026, 8, 5, 18, tzinfo=timezone.utc) + timedelta(days=index % 7)
            row["timestamps"] = {
                "created_at": lifecycle._utc_iso(day),
                "runnable_at": lifecycle._utc_iso(day + timedelta(minutes=10)),
                "started_at": lifecycle._utc_iso(day + timedelta(minutes=20)),
                "finished_at": lifecycle._utc_iso(day + timedelta(hours=1, minutes=20)),
            }
            yield row

    segments, metadata = lifecycle.encode_job_segments(
        rows(), retention_start=NOW - timedelta(days=7), end_exclusive=NOW
    )
    assert metadata["segment_count"] == 7
    assert metadata["job_observations"] == 128_003
    assert metadata["total_compressed_bytes"] < lifecycle.MAX_COMPRESSED_LEDGER_BYTES
    assert (
        sum(len(gzip.decompress(payload).splitlines()) for payload in segments.values()) == 128_003
    )


def test_unchanged_old_day_segment_is_byte_identical_when_current_day_changes():
    old = _observations_for(
        _job(
            uuid="old-day",
            runnable_at="2026-08-10T10:00:00Z",
            started_at="2026-08-10T10:10:00Z",
            finished_at="2026-08-10T11:00:00Z",
        )
    )[0]
    current = _observation(2)
    extra_current = _observation(3)
    before, _ = lifecycle.encode_job_segments(
        [old, current], retention_start=NOW - timedelta(days=7), end_exclusive=NOW
    )
    after, _ = lifecycle.encode_job_segments(
        [old, current, extra_current],
        retention_start=NOW - timedelta(days=7),
        end_exclusive=NOW,
    )
    assert before["2026-08-10.jsonl.gz"] == after["2026-08-10.jsonl.gz"]
    assert before["2026-08-11.jsonl.gz"] != after["2026-08-11.jsonl.gz"]


def test_compressed_size_guard_fails_before_publication(monkeypatch):
    monkeypatch.setattr(lifecycle, "MAX_COMPRESSED_LEDGER_BYTES", 64)
    with pytest.raises(RuntimeError, match="total safety limit"):
        lifecycle.encode_job_segments(
            [_observation()], retention_start=NOW - timedelta(days=7), end_exclusive=NOW
        )


def test_local_segments_enforce_cumulative_uncompressed_limit(monkeypatch, tmp_path):
    old = _observations_for(
        _job(
            uuid="old",
            runnable_at="2026-08-10T10:00:00Z",
            started_at="2026-08-10T10:10:00Z",
            finished_at="2026-08-10T11:00:00Z",
        )
    )[0]
    segments, _ = lifecycle.encode_job_segments(
        [old, _observation(2)],
        retention_start=NOW - timedelta(days=7),
        end_exclusive=NOW,
    )
    jobs_path = tmp_path / "jobs"
    jobs_path.mkdir()
    decoded_sizes = []
    for name, payload in segments.items():
        (jobs_path / name).write_bytes(payload)
        decoded_sizes.append(len(gzip.decompress(payload)))
    assert len(decoded_sizes) == 2
    monkeypatch.setattr(
        lifecycle,
        "MAX_UNCOMPRESSED_LEDGER_BYTES",
        max(decoded_sizes) + 1,
    )
    with pytest.raises(RuntimeError, match="uncompressed queue lifecycle ledger exceeds"):
        lifecycle.read_job_directory(jobs_path)


def test_current_full_document_migrates_to_incremental_window_with_overlap(
    monkeypatch, tmp_path
):
    jobs_path = tmp_path / "jobs"
    summary_path = tmp_path / "summary.json"
    prior_end = NOW - timedelta(hours=1)
    _write_previous_generation(
        jobs_path,
        summary_path,
        provenance={
            "last_successful_query_start": "2026-08-01T19:00:00Z",
            "last_successful_query_end": lifecycle._utc_iso(prior_end),
            # Current published documents do not yet have a dedicated
            # last-full field. Their existing mode makes migration unambiguous.
            "last_successful_query_mode": lifecycle.FULL_QUERY_MODE,
        },
    )
    observed = {}
    monkeypatch.setattr(
        lifecycle,
        "fetch_rest_target_queues",
        lambda token: (_queue_by_id(), {"complete": True, "pages": 1}),
    )

    def fetch_jobs(
        token, *, query_start, query_end, active_created_from, queue_by_id
    ):
        observed.update(
            start=query_start,
            active_start=active_created_from,
            end=query_end,
            queues=queue_by_id,
        )
        return {}, {"complete": True, "cohorts": {}}

    monkeypatch.setattr(lifecycle, "fetch_rest_lifecycle_jobs", fetch_jobs)
    summary = lifecycle.collect_lifecycle(
        "token", jobs_path=jobs_path, summary_path=summary_path, now=NOW
    )
    assert observed == {
        "start": prior_end - timedelta(hours=lifecycle.INCREMENTAL_OVERLAP_HOURS),
        "active_start": NOW - timedelta(
            days=lifecycle.RETENTION_DAYS + lifecycle.PARENT_BUILD_LOOKBACK_DAYS
        ),
        "end": NOW,
        "queues": _queue_by_id(),
    }
    provenance = summary["provenance"]
    assert provenance["last_successful_query_mode"] == lifecycle.INCREMENTAL_QUERY_MODE
    assert provenance["last_full_reconciliation_end"] == lifecycle._utc_iso(prior_end)
    assert provenance["collection"]["watermark_before"] == lifecycle._utc_iso(prior_end)
    assert provenance["collection"]["incremental_overlap_hours"] == 6
    assert provenance["collection"]["active_parent_query_start"] == lifecycle._utc_iso(
        NOW - timedelta(days=lifecycle.RETENTION_DAYS + lifecycle.PARENT_BUILD_LOOKBACK_DAYS)
    )


def test_periodic_full_reconciliation_replaces_incremental_window(monkeypatch, tmp_path):
    jobs_path = tmp_path / "jobs"
    summary_path = tmp_path / "summary.json"
    _write_previous_generation(
        jobs_path,
        summary_path,
        provenance={
            "last_successful_query_start": lifecycle._utc_iso(NOW - timedelta(hours=7)),
            "last_successful_query_end": lifecycle._utc_iso(NOW - timedelta(hours=1)),
            "last_successful_query_mode": lifecycle.INCREMENTAL_QUERY_MODE,
            "last_full_reconciliation_end": lifecycle._utc_iso(
                NOW - timedelta(hours=lifecycle.FULL_RECONCILIATION_INTERVAL_HOURS)
            ),
        },
    )
    observed = {}
    monkeypatch.setattr(
        lifecycle,
        "fetch_rest_target_queues",
        lambda token: (_queue_by_id(), {"complete": True, "pages": 1}),
    )

    def fetch_jobs(
        token, *, query_start, query_end, active_created_from, queue_by_id
    ):
        observed.update(
            start=query_start,
            active_start=active_created_from,
            end=query_end,
            queues=queue_by_id,
        )
        return {}, {"complete": True, "cohorts": {}}

    monkeypatch.setattr(lifecycle, "fetch_rest_lifecycle_jobs", fetch_jobs)
    summary = lifecycle.collect_lifecycle(
        "token", jobs_path=jobs_path, summary_path=summary_path, now=NOW
    )

    assert observed["start"] == NOW - timedelta(
        days=lifecycle.RETENTION_DAYS + lifecycle.PARENT_BUILD_LOOKBACK_DAYS
    )
    assert observed["active_start"] == observed["start"]
    provenance = summary["provenance"]
    assert provenance["last_successful_query_mode"] == lifecycle.FULL_QUERY_MODE
    assert provenance["last_full_reconciliation_end"] == lifecycle._utc_iso(NOW)
    assert provenance["collection"]["selection_reason"] == "periodic_full_reconciliation"


def test_unbound_legacy_watermark_falls_back_to_full_reconciliation(monkeypatch, tmp_path):
    jobs_path = tmp_path / "jobs"
    summary_path = tmp_path / "summary.json"
    # A summary without its exact ledger generation is not safe incremental
    # state, even if it contains a syntactically valid legacy query end.
    summary_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scope": {"queues": list(AMD_METRIC_TARGET_QUEUES)},
                "provenance": {
                    "last_successful_query_end": "2026-08-11T19:59:00Z",
                    "last_successful_query_mode": lifecycle.FULL_QUERY_MODE,
                },
            }
        )
    )
    observed = {}
    monkeypatch.setattr(
        lifecycle,
        "fetch_rest_target_queues",
        lambda token: (_queue_by_id(), {"complete": True, "pages": 1}),
    )

    def fetch_jobs(
        token, *, query_start, query_end, active_created_from, queue_by_id
    ):
        observed.update(
            start=query_start,
            active_start=active_created_from,
            end=query_end,
            queues=queue_by_id,
        )
        return {}, {"complete": True, "cohorts": {}}

    monkeypatch.setattr(lifecycle, "fetch_rest_lifecycle_jobs", fetch_jobs)
    summary = lifecycle.collect_lifecycle(
        "token", jobs_path=jobs_path, summary_path=summary_path, now=NOW
    )

    assert observed["start"] == NOW - timedelta(
        days=lifecycle.RETENTION_DAYS + lifecycle.PARENT_BUILD_LOOKBACK_DAYS
    )
    assert observed["active_start"] == observed["start"]
    assert summary["provenance"]["last_successful_query_mode"] == lifecycle.FULL_QUERY_MODE
    assert (
        summary["provenance"]["collection"]["selection_reason"]
        == "missing_or_invalid_watermark"
    )


def test_git_ref_absence_is_noop_but_unreadable_established_ledger_fails(monkeypatch):
    def missing(args, **kwargs):
        if args[1:3] == ["cat-file", "-e"]:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if args[1] == "ls-tree":
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        raise AssertionError(args)

    monkeypatch.setattr(lifecycle.subprocess, "run", missing)
    assert lifecycle._git_ref_jobs("origin/queue-lifecycle-data") == []
    with pytest.raises(RuntimeError, match="established lifecycle ledger is missing"):
        lifecycle._git_ref_jobs("origin/queue-lifecycle-data", required=True)

    def corrupt(args, **kwargs):
        if args[1:3] == ["cat-file", "-e"]:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if args[1] == "ls-tree":
            path = lifecycle.JOBS_REPO_PATH + "/2026-08-11.jsonl.gz\n"
            return SimpleNamespace(returncode=0, stdout=path.encode(), stderr=b"")
        if args[1:3] == ["cat-file", "-s"]:
            return SimpleNamespace(returncode=0, stdout=b"3\n", stderr=b"")
        return SimpleNamespace(returncode=0, stdout=b"bad", stderr=b"")

    monkeypatch.setattr(lifecycle.subprocess, "run", corrupt)
    with pytest.raises(RuntimeError, match="unreadable compressed"):
        lifecycle._git_ref_jobs("origin/queue-lifecycle-data")


def test_remote_summary_size_is_bounded_before_git_show(monkeypatch):
    calls = []

    def oversized(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["cat-file", "-s"]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"{lifecycle.MAX_SUMMARY_BYTES + 1}\n".encode(),
                stderr=b"",
            )
        raise AssertionError("git show must not run for an oversized summary")

    monkeypatch.setattr(lifecycle.subprocess, "run", oversized)
    with pytest.raises(RuntimeError, match="summary .* exceeds the safety limit"):
        lifecycle._git_ref_summary_provenance("origin/queue-lifecycle-data")
    assert len(calls) == 1


def test_merge_mode_requires_summary_bound_manifest_and_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(lifecycle, "_git_ref_summary_provenance", lambda ref: {})

    with pytest.raises(RuntimeError, match="lacks a complete summary-bound ledger manifest"):
        lifecycle.maintain_job_ledger(
            jobs_path=tmp_path / "jobs",
            summary_path=tmp_path / "summary.json",
            git_ref="origin/queue-lifecycle-data",
            now=NOW,
        )

    _, ledger = lifecycle.encode_job_segments(
        [_observation()], retention_start=NOW - timedelta(days=7), end_exclusive=NOW
    )
    monkeypatch.setattr(
        lifecycle,
        "_git_ref_summary_provenance",
        lambda ref: {"ledger": ledger},
    )

    def missing(ref, *, required=False, expected_ledger=None):
        assert required is True
        assert expected_ledger == ledger
        raise RuntimeError("established lifecycle ledger is missing")

    monkeypatch.setattr(lifecycle, "_git_ref_jobs", missing)
    with pytest.raises(RuntimeError, match="established lifecycle ledger is missing"):
        lifecycle.maintain_job_ledger(
            jobs_path=tmp_path / "jobs",
            summary_path=tmp_path / "summary.json",
            git_ref="origin/queue-lifecycle-data",
            now=NOW,
        )


@pytest.mark.parametrize(
    "remote_provenance",
    [
        {"last_successful_query_end": "2026-08-11T20:00:00Z"},
        {"ledger": {}},
        {
            "ledger": {
                "format": "daily_deterministic_gzip_jsonl",
                "segment_count": 1,
                "segments": {},
            }
        },
    ],
)
def test_merge_mode_rejects_missing_or_incomplete_remote_manifest(
    monkeypatch, tmp_path, remote_provenance
):
    monkeypatch.setattr(
        lifecycle,
        "_git_ref_summary_provenance",
        lambda ref: remote_provenance,
    )
    with pytest.raises(RuntimeError, match="lacks a complete summary-bound ledger manifest"):
        lifecycle.maintain_job_ledger(
            jobs_path=tmp_path / "jobs",
            summary_path=tmp_path / "summary.json",
            git_ref="origin/queue-lifecycle-data",
            now=NOW,
        )


def test_generation_summary_links_exact_compressed_ledger(tmp_path):
    jobs_path = tmp_path / "jobs"
    summary_path = tmp_path / "summary.json"
    segments, _ = lifecycle.encode_job_segments(
        [_observation()], retention_start=NOW - timedelta(days=7), end_exclusive=NOW
    )
    jobs_path.mkdir()
    for name, payload in segments.items():
        (jobs_path / name).write_bytes(payload)
    summary = lifecycle.maintain_job_ledger(jobs_path=jobs_path, summary_path=summary_path, now=NOW)
    ledger = summary["provenance"]["ledger"]
    assert ledger["generation_sha256"] == lifecycle._job_directory_generation(jobs_path)
    assert ledger["total_compressed_bytes"] == sum(
        path.stat().st_size for path in jobs_path.iterdir()
    )


def test_summary_write_failure_rolls_ledger_back_to_prior_generation(monkeypatch, tmp_path):
    jobs_path = tmp_path / "jobs"
    summary_path = tmp_path / "summary.json"
    old_payloads, _ = lifecycle.encode_job_segments(
        [_observation(1)], retention_start=NOW - timedelta(days=7), end_exclusive=NOW
    )
    new_payloads, _ = lifecycle.encode_job_segments(
        [_observation(2)], retention_start=NOW - timedelta(days=7), end_exclusive=NOW
    )
    jobs_path.mkdir()
    for name, payload in old_payloads.items():
        (jobs_path / name).write_bytes(payload)
    summary_path.write_text("old-summary")
    monkeypatch.setattr(
        lifecycle,
        "_atomic_write_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        lifecycle._publish_generation(jobs_path, new_payloads, summary_path, "new-summary")

    assert {path.name: path.read_bytes() for path in jobs_path.iterdir()} == old_payloads
    assert summary_path.read_text() == "old-summary"


def test_stage_install_failure_restores_prior_generation(monkeypatch, tmp_path):
    jobs_path = tmp_path / "jobs"
    summary_path = tmp_path / "summary.json"
    old_payloads, _ = lifecycle.encode_job_segments(
        [_observation(1)], retention_start=NOW - timedelta(days=7), end_exclusive=NOW
    )
    new_payloads, _ = lifecycle.encode_job_segments(
        [_observation(2)], retention_start=NOW - timedelta(days=7), end_exclusive=NOW
    )
    jobs_path.mkdir()
    for name, payload in old_payloads.items():
        (jobs_path / name).write_bytes(payload)
    summary_path.write_text("old-summary")
    real_replace = lifecycle.os.replace

    def fail_stage_install(source, target):
        if Path(source).name.startswith(".jobs.stage.") and Path(target) == jobs_path:
            raise OSError("stage install failed")
        return real_replace(source, target)

    monkeypatch.setattr(lifecycle.os, "replace", fail_stage_install)
    with pytest.raises(OSError, match="stage install failed"):
        lifecycle._publish_generation(jobs_path, new_payloads, summary_path, "new")
    assert {path.name: path.read_bytes() for path in jobs_path.iterdir()} == old_payloads
    assert not list(tmp_path.glob(".jobs.backup.*"))


def test_rollback_failure_preserves_only_prior_generation_backup(monkeypatch, tmp_path):
    jobs_path = tmp_path / "jobs"
    summary_path = tmp_path / "summary.json"
    old_payloads, _ = lifecycle.encode_job_segments(
        [_observation(1)], retention_start=NOW - timedelta(days=7), end_exclusive=NOW
    )
    new_payloads, _ = lifecycle.encode_job_segments(
        [_observation(2)], retention_start=NOW - timedelta(days=7), end_exclusive=NOW
    )
    jobs_path.mkdir()
    for name, payload in old_payloads.items():
        (jobs_path / name).write_bytes(payload)
    summary_path.write_text("old-summary")
    real_replace = lifecycle.os.replace

    def fail_backup_restore(source, target):
        if Path(source).name.startswith(".jobs.backup.") and Path(target) == jobs_path:
            raise OSError("restore failed")
        return real_replace(source, target)

    monkeypatch.setattr(lifecycle.os, "replace", fail_backup_restore)
    monkeypatch.setattr(
        lifecycle,
        "_atomic_write_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("summary failed")),
    )
    with pytest.raises(RuntimeError, match="prior ledger preserved at"):
        lifecycle._publish_generation(jobs_path, new_payloads, summary_path, "new")
    backups = list(tmp_path.glob(".jobs.backup.*"))
    assert len(backups) == 1
    assert {path.name: path.read_bytes() for path in backups[0].iterdir()} == old_payloads
    assert not jobs_path.exists()
    assert summary_path.read_text() == "old-summary"


def test_crash_generation_mismatch_discards_local_provenance(tmp_path):
    jobs_path = tmp_path / "jobs"
    summary_path = tmp_path / "summary.json"
    segments, _ = lifecycle.encode_job_segments(
        [_observation()], retention_start=NOW - timedelta(days=7), end_exclusive=NOW
    )
    jobs_path.mkdir()
    for name, payload in segments.items():
        (jobs_path / name).write_bytes(payload)
    summary_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scope": {"queues": list(AMD_METRIC_TARGET_QUEUES)},
                "provenance": {
                    "last_successful_query_end": "2026-08-11T19:59:00Z",
                    "ledger": {"generation_sha256": "0" * 64},
                },
            }
        )
    )

    assert lifecycle._safe_previous_provenance(summary_path, jobs_path=jobs_path) == {}


def test_collection_failure_never_replaces_outputs(monkeypatch, tmp_path):
    jobs_path = tmp_path / "jobs"
    summary_path = tmp_path / "summary.json"
    summary_path.write_text("sentinel")
    monkeypatch.setattr(
        lifecycle,
        "fetch_rest_target_queues",
        lambda token: (_ for _ in ()).throw(RuntimeError("incomplete pagination")),
    )
    with pytest.raises(RuntimeError, match="incomplete pagination"):
        lifecycle.collect_lifecycle(
            "token", jobs_path=jobs_path, summary_path=summary_path, now=NOW
        )
    assert not jobs_path.exists()
    assert summary_path.read_text() == "sentinel"
