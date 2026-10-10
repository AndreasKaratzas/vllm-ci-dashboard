"""Tests for privacy-safe vLLM/Omni mappings onto monitored AMD queues."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import requests
import pytest

from vllm import collect_workload_mapping as cwm
from vllm.bounded_json import pretty_json_bytes
from vllm.main_ci_definitions import (
    annotate_runtime_source_scope as exact_source_join,
    prewarm_runtime_snapshots as exact_source_prime,
)


NOW = datetime(2026, 7, 29, 18, 35, tzinfo=timezone.utc)
SOURCE_COMMIT = "a" * 40


@pytest.fixture(autouse=True)
def immutable_source_fixture(monkeypatch):
    """Use validated deterministic source indexes, never a GitHub transport."""
    from vllm import main_ci_definitions as source

    real_annotate = source.annotate_runtime_source_scope
    monkeypatch.setattr(cwm, "_SOURCE_SCOPE_INDEXES", {})
    monkeypatch.setattr(cwm, "_SOURCE_SCOPE_CACHE_DIR", None)
    monkeypatch.setattr(source, "prewarm_runtime_snapshots", lambda pins: None)

    def annotate(build, **kwargs):
        index = kwargs.get("scope_index") or {
            "version": 1, "commit_sha": build.get("commit"),
            "definition_tree_sha": "b" * 40, "cpu_routes": [],
        }
        return real_annotate(build, scope_index=index)

    monkeypatch.setattr(source, "annotate_runtime_source_scope", annotate)


def _config() -> dict:
    return {
        "schema_version": 1,
        "projection": {"target_groups": 160},
        "scope": {"excluded_queue_classes": ["perf_eval"]},
        "workload_pipelines": {
            "omni": ["vllm-omni-amd-ci"],
            "main": ["ci"],
        },
        "queues": [
            {
                "id": "amd_mi250_1",
                "label": "mi250_1",
                "family": "MI250",
                "gpus_per_job": 1,
                "max_concurrent_jobs": 78,
                "monitored": True,
                "capacity_eligible": True,
                "lifecycle": "active",
            },
            {
                "id": "amd_mi300_4",
                "label": "mi300_4",
                "family": "MI300",
                "gpus_per_job": 4,
                "max_concurrent_jobs": 29,
                "monitored": True,
                "capacity_eligible": True,
                "lifecycle": "active",
            },
            {
                "id": "amd_mi325_8",
                "label": "mi325_8",
                "family": "MI325",
                "gpus_per_job": 8,
                "max_concurrent_jobs": 0,
                "monitored": True,
                "capacity_eligible": False,
                "lifecycle": "retiring",
            },
            {
                "id": "amd_mi300_perf_eval",
                "label": "mi300_perf_eval",
                "family": "MI300",
                "gpus_per_job": 8,
                "max_concurrent_jobs": 1,
                "monitored": True,
                "capacity_eligible": False,
                "lifecycle": "separate",
            },
        ],
    }


def _job(
    job_id: str | None,
    queue: str,
    *,
    created_at: str = "2026-07-29T10:00:00Z",
    started_at: str | None = "2026-07-29T10:10:00Z",
    finished_at: str | None = "2026-07-29T10:40:00Z",
) -> dict:
    return {
        "id": job_id,
        "type": "script",
        "name": f"{queue}: test",
        "state": "passed",
        "created_at": created_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "agent_query_rules": [f"queue={queue}"],
    }


def _build(
    pipeline: str,
    jobs: list[dict],
    number: int = 1,
    *,
    created_at: str = "2026-07-29T09:59:00Z",
) -> dict:
    return {
        "number": number,
        "commit": SOURCE_COMMIT,
        "created_at": created_at,
        "pipeline": {"slug": pipeline},
        "jobs": jobs,
    }


def test_request_build_page_retries_rate_limit_without_losing_slice(monkeypatch):
    class Response:
        def __init__(self, status_code, payload, headers=None):
            self.status_code = status_code
            self._payload = payload
            self.headers = headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

        def json(self):
            return self._payload

    responses = iter([
        Response(429, [], {"Retry-After": "2"}),
        Response(200, [{"number": 1}]),
    ])
    sleeps = []
    monkeypatch.setattr(cwm.requests, "get", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(cwm.time_module, "sleep", sleeps.append)

    rows = cwm._request_build_page("/builds", "token", {"page": 1})

    assert rows == [{"number": 1}]
    assert sleeps == [2]


def test_request_build_page_honors_user_rate_limit_reset(monkeypatch):
    class Response:
        def __init__(self, status_code, headers=None):
            self.status_code = status_code
            self.headers = headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

        def json(self):
            return []

    responses = iter(
        [
            Response(429, {"RateLimit-Reset": "1", "RateLimit-User-Reset": "17"}),
            Response(200),
        ]
    )
    sleeps = []
    monkeypatch.setattr(cwm.requests, "get", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(cwm.time_module, "sleep", sleeps.append)

    cwm._request_build_page("/builds", "token", {"page": 1})

    assert sleeps == [17]


def test_request_build_page_does_not_retry_non_retryable_auth_error(monkeypatch):
    class Response:
        status_code = 401
        headers = {}

        def raise_for_status(self):
            raise requests.HTTPError("HTTP 401", response=self)

    sleeps = []
    monkeypatch.setattr(cwm.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(cwm.time_module, "sleep", sleeps.append)

    try:
        cwm._request_build_page("/builds", "bad-token", {"page": 1})
    except requests.HTTPError:
        pass
    else:
        raise AssertionError("401 response must fail without retry")

    assert sleeps == []


def test_request_build_page_expired_deadline_starts_no_transport(monkeypatch):
    calls = []
    monkeypatch.setattr(cwm.time_module, "monotonic", lambda: 10.0)
    monkeypatch.setattr(
        cwm.requests,
        "get",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(
        cwm.BuildkiteRequestDeadlineExceeded,
        match="before the next transport start",
    ):
        cwm._request_build_page(
            "/builds",
            "token",
            {"page": 1},
            deadline_monotonic=10.0,
        )

    assert calls == []


def test_request_build_page_caps_timeout_to_remaining_deadline(monkeypatch):
    class Response:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return [{"number": 1}]

    request_kwargs = []
    monkeypatch.setattr(cwm.time_module, "monotonic", lambda: 10.0)
    monkeypatch.setattr(
        cwm.requests,
        "get",
        lambda *args, **kwargs: request_kwargs.append(kwargs) or Response(),
    )

    rows = cwm._request_build_page(
        "/builds",
        "token",
        {"page": 1},
        deadline_monotonic=15.0,
    )

    assert rows == [{"number": 1}]
    assert request_kwargs[0]["timeout"] == 5.0


def test_request_build_page_does_not_sleep_or_retry_across_deadline(monkeypatch):
    class Response:
        status_code = 429
        headers = {"Retry-After": "10"}

        def raise_for_status(self):
            raise requests.HTTPError("HTTP 429", response=self)

    calls = []
    sleeps = []
    monkeypatch.setattr(cwm.time_module, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        cwm.requests,
        "get",
        lambda *args, **kwargs: calls.append(kwargs) or Response(),
    )
    monkeypatch.setattr(cwm.time_module, "sleep", sleeps.append)

    with pytest.raises(
        cwm.BuildkiteRequestDeadlineExceeded,
        match="retry delay would cross",
    ):
        cwm._request_build_page(
            "/builds",
            "token",
            {"page": 1},
            deadline_monotonic=5.0,
        )

    assert len(calls) == 1
    assert sleeps == []


def _slice_aware_fetcher(responses: dict[str, list[dict]]):
    """Return builds only when their created_at falls in the requested slice."""

    def fetcher(path: str, _token: str, params: dict) -> list[dict]:
        if params["page"] > 1:
            return []
        slug = path.split("/pipelines/", 1)[1].split("/", 1)[0]
        start = cwm.parse_iso(params["created_from"])
        end = cwm.parse_iso(params["created_to"])
        return [
            build
            for build in responses.get(slug, [])
            if start <= cwm.parse_iso(build["created_at"]) < end
        ]

    return fetcher


def test_monitored_queues_is_an_exact_allowlist_and_excludes_perf_eval() -> None:
    queues = cwm.monitored_queues(_config())

    assert set(queues) == {"amd_mi250_1", "amd_mi300_4", "amd_mi325_8"}
    assert queues["amd_mi300_4"]["gpus_per_job"] == 4
    assert queues["amd_mi325_8"]["lifecycle"] == "retiring"


def test_collect_has_exact_repository_labels_and_both_dimensions() -> None:
    responses = {
        "vllm-omni-amd-ci": [
            _build(
                "vllm-omni-amd-ci",
                [
                    _job("omni-1", "amd_mi300_4"),
                    _job("omni-1", "amd_mi300_4"),  # duplicate API evidence
                    _job("omni-2", "amd_mi250_1", started_at=None, finished_at=None),
                    _job("ignored-perf", "amd_mi300_perf_eval"),
                    _job("ignored-nvidia", "gpu_1_queue"),
                ],
            )
        ],
        "ci": [
            _build(
                "ci",
                [
                    _job("main-1", "amd_mi250_1"),
                    _job("main-2", "amd_mi325_8"),
                ],
                number=2,
            )
        ],
    }

    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        now=NOW,
        force_days=1,
        page_fetcher=_slice_aware_fetcher(responses),
    )

    assert payload["schema_version"] == 2
    assert payload["repositories"]["omni"]["label"] == "vllm-project/vllm-omni"
    assert payload["repositories"]["main"]["label"] == "vllm-project/vllm"
    assert payload["totals"]["omni"]["mapped_jobs"] == 2
    assert payload["totals"]["omni"]["started_jobs"] == 1
    assert payload["totals"]["omni"]["mapped_gpu_slots"] == 5
    assert payload["totals"]["omni"]["gpu_hours"] == 2.0
    assert payload["totals"]["main"]["mapped_jobs"] == 2
    assert payload["totals"]["main"]["mapped_gpu_slots"] == 9
    assert payload["totals"]["main"]["gpu_hours"] == 4.5
    assert payload["query"]["diagnostics"]["omni"]["duplicate_job_ids"] == 1
    assert set(payload["totals"]["omni"]["by_queue"]) == {
        "amd_mi250_1",
        "amd_mi300_4",
    }
    assert set(payload["totals"]["omni"]["by_pipeline"]) == {
        "vllm-omni-amd-ci",
    }

    ten_utc = next(row for row in payload["hourly"] if row["hour"] == "2026-07-29T10:00:00Z")
    assert ten_utc["workloads"]["omni"]["mapped_jobs"] == 2
    assert ten_utc["workloads"]["omni"]["by_pipeline"]["vllm-omni-amd-ci"]["mapped_jobs"] == 2


def test_open_hour_is_partial_but_not_a_collection_failure() -> None:
    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        now=NOW.replace(microsecond=987654),
        force_days=1,
        page_fetcher=_slice_aware_fetcher({}),
    )

    current = payload["hourly"][-1]
    assert current["hour"] == "2026-07-29T18:00:00Z"
    assert current["end_exclusive"] == "2026-07-29T19:00:00Z"
    assert current["observed_through"] == "2026-07-29T18:35:00Z"
    assert current["state"] == "open"
    assert current["open"] is True
    assert current["partial"] is True
    assert current["complete"] is False
    assert current["collection_complete"] is True
    assert current["lower_bound"] is False
    assert payload["coverage"]["hourly"]["has_open_bucket"] is True


def test_missing_job_uuid_marks_only_affected_bucket_as_lower_bound() -> None:
    responses = {
        "vllm-omni-amd-ci": [_build("vllm-omni-amd-ci", [_job(None, "amd_mi250_1")])],
        "ci": [_build("ci", [_job("main-1", "amd_mi250_1")])],
    }
    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        now=NOW,
        force_days=1,
        page_fetcher=_slice_aware_fetcher(responses),
    )

    ten_utc = next(row for row in payload["hourly"] if row["hour"] == "2026-07-29T10:00:00Z")
    eleven_utc = next(row for row in payload["hourly"] if row["hour"] == "2026-07-29T11:00:00Z")
    assert ten_utc["collection_complete"] is False
    assert ten_utc["lower_bound"] is True
    assert eleven_utc["collection_complete"] is True
    assert payload["totals"]["omni"]["mapped_jobs"] == 0
    assert payload["query"]["diagnostics"]["omni"]["missing_job_ids"] == 1


def test_incremental_refresh_preserves_old_daily_and_backfills_hourly() -> None:
    old_day = {
        **cwm._empty_day("2026-07-20"),
        "workloads": {
            "omni": {
                **cwm._empty_workload(),
                "mapped_jobs": 7,
            },
            "main": cwm._empty_workload(),
        },
    }
    existing = {
        "schema_version": 2, "hardware_scope": "amd_mi_gpu",
        "daily": [old_day],
        # Explicitly no hourly collection: it must be backfilled.
    }
    responses = {
        "vllm-omni-amd-ci": [
            _build(
                "vllm-omni-amd-ci",
                [
                    _job(
                        "omni-fresh",
                        "amd_mi250_1",
                        created_at="2026-07-28T10:00:00Z",
                    )
                ],
                created_at="2026-07-28T09:59:00Z",
            )
        ],
        "ci": [
            _build(
                "ci",
                [
                    _job(
                        "main-fresh",
                        "amd_mi250_1",
                        created_at="2026-07-28T10:00:00Z",
                    )
                ],
                created_at="2026-07-28T09:59:00Z",
            )
        ],
    }
    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        existing=existing,
        now=NOW,
        force_days=2,
        page_fetcher=_slice_aware_fetcher(responses),
    )
    by_day = {row["date"]: row for row in payload["daily"]}

    assert by_day["2026-07-20"]["workloads"]["omni"]["mapped_jobs"] == 7
    assert by_day["2026-07-28"]["workloads"]["omni"]["mapped_jobs"] == 1
    assert by_day["2026-07-28"]["workloads"]["main"]["mapped_jobs"] == 1
    assert payload["hourly"]
    assert payload["hourly"][0]["hour"] <= "2026-07-28T00:00:00Z"
    assert payload["coverage"]["hourly"]["resolution"] == "UTC hour"
    assert payload["coverage"]["daily"]["contiguous"] is False
    assert payload["coverage"]["daily"]["collection_complete"] is False


def test_incremental_without_force_fills_an_old_hourly_gap() -> None:
    hourly_start = cwm._hour_start(NOW) - timedelta(days=7)
    existing_hours = []
    missing = hourly_start + timedelta(hours=2)
    for hour in cwm._hour_range(hourly_start, cwm._hour_start(NOW)):
        if hour == missing:
            continue
        existing_hours.append(
            {
                "hour": cwm._utc_iso(hour),
                **cwm._bucket_status(
                    hour,
                    hour + timedelta(hours=1),
                    NOW,
                    True,
                ),
                "workloads": {
                    "omni": cwm._empty_workload(),
                    "main": cwm._empty_workload(),
                },
            }
        )
    daily_start = NOW.date() - timedelta(days=89)
    existing_daily = [
        cwm._empty_day(day.isoformat()) for day in cwm._date_range(daily_start, NOW.date())
    ]
    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        existing={
            "schema_version": 2, "hardware_scope": "amd_mi_gpu",
            "hourly": existing_hours,
            "daily": existing_daily,
        },
        now=NOW,
        page_fetcher=_slice_aware_fetcher({}),
    )

    assert cwm._utc_iso(missing) in {row["hour"] for row in payload["hourly"]}
    assert payload["query"]["start"] <= cwm._utc_iso(missing)


def test_incremental_retries_old_incomplete_daily_and_hourly_buckets() -> None:
    existing = cwm.collect_workload_mapping(
        "token",
        _config(),
        now=NOW,
        force_days=100,
        page_fetcher=_slice_aware_fetcher({}),
    )
    incomplete_day = (NOW.date() - timedelta(days=20)).isoformat()
    incomplete_hour = cwm._utc_iso(cwm._hour_start(NOW) - timedelta(days=5))
    for collection, key, target in (
        ("daily", "date", incomplete_day),
        ("hourly", "hour", incomplete_hour),
    ):
        row = next(item for item in existing[collection] if item[key] == target)
        row.update(
            {
                "state": "partial",
                "complete": False,
                "collection_complete": False,
                "lower_bound": True,
            }
        )

    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        existing=existing,
        now=NOW + timedelta(hours=1),
        page_fetcher=_slice_aware_fetcher({}),
    )

    assert payload["query"]["start"] <= f"{incomplete_day}T00:00:00Z"
    assert next(
        row for row in payload["daily"] if row["date"] == incomplete_day
    )["collection_complete"] is True
    assert next(
        row for row in payload["hourly"] if row["hour"] == incomplete_hour
    )["collection_complete"] is True


def test_fetch_uses_independent_bounded_slices_and_reports_local_truncation() -> None:
    requests: list[dict] = []

    def fetcher(_path: str, _token: str, params: dict) -> list[dict]:
        requests.append(params)
        if params["created_from"].startswith("2026-07-28"):
            return [{} for _ in range(cwm.PER_PAGE)]
        return []

    builds, source = cwm.fetch_pipeline_builds(
        "token",
        "ci",
        datetime(2026, 7, 27, 12, tzinfo=timezone.utc),
        datetime(2026, 7, 29, 18, tzinfo=timezone.utc),
        max_pages=1,
        page_fetcher=fetcher,
    )

    assert source["slice_count"] == 3
    assert len(requests) == 3
    assert len(builds) == cwm.PER_PAGE
    assert source["complete"] is False
    assert source["truncated"] is True
    assert source["slices"][0]["start"] == "2026-07-27T12:00:00Z"
    assert source["slices"][0]["end_exclusive"] == "2026-07-28T00:00:00Z"


def test_slice_fetches_are_concurrent_bounded_and_keep_exact_params() -> None:
    first_wave = threading.Barrier(cwm.MAX_SLICE_WORKERS)
    lock = threading.Lock()
    active = 0
    max_active = 0
    requests: list[dict] = []

    def fetcher(_path: str, _token: str, params: dict) -> list[dict]:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            requests.append(params)
        if params["created_from"] < "2026-07-28T00:00:00Z":
            first_wave.wait(timeout=2)
        with lock:
            active -= 1
        return []

    yielded = list(
        cwm._iter_pipeline_build_slices(
            "token",
            "ci",
            datetime(2026, 7, 25, tzinfo=timezone.utc),
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            page_fetcher=fetcher,
        )
    )

    assert max_active == cwm.MAX_SLICE_WORKERS
    assert len(requests) == 5
    assert all(
        set(params)
        == {
            "created_from",
            "created_to",
            "include_retried_jobs",
            "exclude_pipeline",
            "per_page",
            "page",
        }
        for params in requests
    )
    assert all(params["include_retried_jobs"] == "true" for params in requests)
    assert all(params["exclude_pipeline"] == "true" for params in requests)
    assert all(params["per_page"] == cwm.PER_PAGE for params in requests)
    assert all(params["page"] == 1 for params in requests)
    assert sorted(
        (params["created_from"], params["created_to"])
        for params in requests
    ) == [
        ("2026-07-25T00:00:00Z", "2026-07-26T00:00:00Z"),
        ("2026-07-26T00:00:00Z", "2026-07-27T00:00:00Z"),
        ("2026-07-27T00:00:00Z", "2026-07-28T00:00:00Z"),
        ("2026-07-28T00:00:00Z", "2026-07-29T00:00:00Z"),
        ("2026-07-29T00:00:00Z", "2026-07-30T00:00:00Z"),
    ]
    assert len(yielded) == 5


def test_slice_generator_retains_at_most_the_worker_cap(monkeypatch) -> None:
    lock = threading.Lock()
    live = 0
    max_live = 0

    class RawSlice(list):
        def __init__(self) -> None:
            nonlocal live, max_live
            super().__init__()
            with lock:
                live += 1
                max_live = max(max_live, live)

        def __del__(self) -> None:
            nonlocal live
            with lock:
                live -= 1

    def fetch_slice(
        _path,
        _token,
        _pipeline,
        start,
        end,
        *,
        max_pages,
        page_fetcher,
        mapping_start=None,
        mapping_end=None,
    ):
        return RawSlice(), {
            "start": cwm._utc_iso(start),
            "end_exclusive": cwm._utc_iso(end),
            "pages_fetched": 1,
            "builds_fetched": 0,
            "complete": True,
            "truncated": False,
            "error_type": None,
        }

    monkeypatch.setattr(cwm, "_fetch_pipeline_slice", fetch_slice)

    for rows, _source in cwm._iter_pipeline_build_slices(
        "token",
        "ci",
        datetime(2026, 7, 20, tzinfo=timezone.utc),
        datetime(2026, 7, 30, tzinfo=timezone.utc),
    ):
        del rows

    assert max_live <= cwm.MAX_SLICE_WORKERS
    assert live == 0


def test_global_uuid_dedup_survives_pipeline_and_slice_streaming() -> None:
    shared_id = "globally-shared-job-id"
    responses = {
        "vllm-omni-amd-ci": [
            _build("vllm-omni-amd-ci", [_job(shared_id, "amd_mi250_1")])
        ],
        "ci": [_build("ci", [_job(shared_id, "amd_mi250_1")])],
    }

    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        now=NOW,
        force_days=1,
        page_fetcher=_slice_aware_fetcher(responses),
    )

    assert payload["totals"]["omni"]["mapped_jobs"] == 1
    assert payload["totals"]["main"]["mapped_jobs"] == 0
    assert (
        payload["query"]["diagnostics"]["main"][
            "cross_pipeline_duplicate_job_ids"
        ]
        == 1
    )


def test_workload_completeness_is_reported_separately(monkeypatch) -> None:
    responses = {
        "vllm-omni-amd-ci": [
            _build("vllm-omni-amd-ci", [_job("omni-1", "amd_mi250_1")])
        ]
    }
    base_fetcher = _slice_aware_fetcher(responses)

    def fetcher(path, token, params):
        if "/pipelines/ci/" in path:
            raise requests.Timeout("main pipeline unavailable")
        return base_fetcher(path, token, params)

    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        now=NOW,
        force_days=1,
        page_fetcher=fetcher,
    )
    row = next(
        item for item in payload["hourly"]
        if item["hour"] == "2026-07-29T10:00:00Z"
    )

    assert row["collection_complete"] is False
    assert row["collection_complete_by_workload"] == {
        "omni": True,
        "main": False,
    }


def test_retention_and_coverage_publish_exact_ranges() -> None:
    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        now=NOW,
        force_days=100,
        retention_days=90,
        hourly_retention_days=7,
        page_fetcher=_slice_aware_fetcher({}),
    )

    assert len(payload["daily"]) == 90
    # Seven elapsed days plus the current open hour.
    assert len(payload["hourly"]) == 169
    assert payload["daily"][0]["date"] == "2026-05-01"
    assert payload["hourly"][0]["hour"] == "2026-07-22T18:00:00Z"
    assert payload["coverage"]["hourly"]["start"] == "2026-07-22T18:00:00Z"
    assert payload["coverage"]["hourly"]["observed_through"] == cwm._utc_iso(NOW)
    assert payload["retention"] == {"hourly_days": 7, "daily_days": 90}


def test_retention_cannot_expand_the_bounded_publication_window() -> None:
    with pytest.raises(ValueError, match="retention_days may not exceed"):
        cwm.collect_workload_mapping(
            "token",
            _config(),
            now=NOW,
            retention_days=91,
            page_fetcher=_slice_aware_fetcher({}),
        )


def test_workload_mapping_compaction_drops_oldest_whole_buckets() -> None:
    source = {
        "schema_version": 2, "hardware_scope": "amd_mi_gpu",
        "generated_at": "2026-09-01T00:00:00Z",
        "retention": {"hourly_days": 7, "daily_days": 90},
        "totals": {"omni": {"mapped_jobs": 99}, "main": {"mapped_jobs": 88}},
        "hourly": [
            {
                "hour": f"2026-08-31T0{index}:00:00Z",
                "padding": "h" * 1_000,
            }
            for index in range(5)
        ],
        "daily": [
            {"date": f"2026-08-{27 + index:02d}", "padding": "d" * 1_000}
            for index in range(5)
        ],
    }

    bounded = cwm.compact_workload_mapping_for_publication(
        source,
        max_bytes=5_000,
    )

    assert len(pretty_json_bytes(bounded)) <= 5_000
    retention = bounded["retention"]["publication"]
    assert retention["complete_relative_to_source"] is False
    assert retention["aggregate_scalars_complete"] is True
    assert retention["hourly"]["published"] == len(bounded["hourly"])
    assert retention["daily"]["published"] == len(bounded["daily"])
    assert bounded["hourly"] == source["hourly"][retention["hourly"]["omitted"]:]
    assert bounded["daily"] == source["daily"][retention["daily"]["omitted"]:]
    assert bounded["totals"] == source["totals"]


def test_workload_mapping_writer_preserves_lkg_on_irreducible_overflow(tmp_path) -> None:
    path = tmp_path / "workload_mapping.json"
    path.write_text('{"generation":"last-known-good"}\n')
    source = {
        "retention": {"hourly_days": 7, "daily_days": 90},
        "irreducible": "x" * 2_000,
        "hourly": [],
        "daily": [],
    }

    with pytest.raises(RuntimeError, match="fixed metadata exceeds"):
        cwm.write_workload_mapping(path, source, max_bytes=500)

    assert json.loads(path.read_text()) == {"generation": "last-known-good"}


def test_short_forced_query_does_not_claim_full_retention_coverage() -> None:
    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        now=NOW,
        force_days=1,
        page_fetcher=_slice_aware_fetcher({}),
    )

    coverage = payload["coverage"]["daily"]
    assert coverage["bucket_count"] == 1
    assert coverage["expected_bucket_count"] == 90
    assert coverage["missing_bucket_count"] == 89
    assert coverage["contiguous"] is False
    assert coverage["collection_complete"] is False


def test_parent_build_lookback_is_explicit_and_not_overclaimed() -> None:
    old_parent = _build(
        "vllm-omni-amd-ci",
        [
            _job(
                "delayed-job",
                "amd_mi250_1",
                created_at="2026-07-29T10:00:00Z",
            )
        ],
        created_at="2026-07-20T10:00:00Z",
    )
    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        now=NOW,
        force_days=1,
        parent_build_lookback_days=3,
        page_fetcher=_slice_aware_fetcher(
            {"vllm-omni-amd-ci": [old_parent]},
        ),
    )

    assert payload["totals"]["omni"]["mapped_jobs"] == 0
    assert payload["query"]["build_created_start"] == "2026-07-26T00:00:00Z"
    assert payload["query"]["job_created_range_exhaustive"] is False
    assert payload["window"]["job_created_range_exhaustive"] is False
    assert (
        payload["coverage"]["hourly"]["job_created_range_exhaustive"]
        is False
    )
    attribution = payload["scope"]["attribution"]
    assert attribution["parent_build_lookback_days"] == 3
    assert attribution["job_created_range_exhaustive"] is False
    assert attribution["exact_within_declared_source_window"] is True


def test_published_payload_contains_no_job_ids_or_raw_jobs() -> None:
    responses = {
        "vllm-omni-amd-ci": [_build("vllm-omni-amd-ci", [_job("secret-job-uuid", "amd_mi250_1")])]
    }
    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        now=NOW,
        force_days=1,
        page_fetcher=_slice_aware_fetcher(responses),
    )
    serialized = json.dumps(payload)

    assert "secret-job-uuid" not in serialized
    assert '"jobs"' not in serialized
    assert '"builds"' not in serialized


def test_mi_scope_filters_foreign_queues_and_explicit_cpu_without_filtering_suite_names():
    config = _config()
    for name in ("gpu_1_queue", "B200", "amd-cpu", "intel-gpu"):
        config["queues"].append({"id": name, "monitored": True, "gpus_per_job": 1})
    catalog = cwm.monitored_queues(config)
    assert all(cwm.amd_gpu_hardware(name) for name in catalog)
    jobs = [_job(str(index), "amd_mi250_1") for index in range(4)]
    jobs[0]["name"] = "CPU Offload with CUDA model preset"
    jobs[1]["name"] = ":computer: (CPU) Torch ABI"
    jobs[2]["no_gpu"] = True
    jobs[3]["agent_query_rules"] = ["queue=B200"]
    events, _ = cwm._events_from_builds(
        [_build("ci", jobs)], pipeline="ci", workload="main", queue_catalog=catalog,
        start=NOW - timedelta(days=1), end=NOW,
    )
    assert len(events) == 1
    assert events[0]["job_id"] == "0"


def test_legacy_mapping_aggregates_cannot_extend_new_mi_scope_history():
    old_day = cwm._empty_day("2026-07-20")
    old_day["workloads"]["main"]["mapped_jobs"] = 999
    payload = cwm.collect_workload_mapping(
        "token", _config(), existing={"schema_version": 2, "daily": [old_day]},
        now=NOW, force_days=1, page_fetcher=_slice_aware_fetcher({}),
    )
    assert payload["hardware_scope"] == "amd_mi_gpu"
    assert payload["scope"]["workload_pipelines"]["main"] == ["ci"]
    assert not any(row["workloads"]["main"]["mapped_jobs"] == 999 for row in payload["daily"])


def _mi_cpu_index(commit=SOURCE_COMMIT):
    return {"version": 1, "commit_sha": commit, "definition_tree_sha": "b" * 40,
            "cpu_routes": [{"key": "torch-abi", "label": "Torch ABI", "agent_pool": "mi250_1"}]}


def test_workload_source_cpu_exclusions_apply_before_gpu_slot_aggregation_and_ignore_title_words():
    cwm._SOURCE_SCOPE_INDEXES[SOURCE_COMMIT] = _mi_cpu_index()
    cpu = {**_job("cpu", "amd_mi250_1"), "name": "Misleading GPU name", "step": {"key": "torch-abi"}}
    gpu = {**_job("gpu", "amd_mi250_1"), "name": "CPU Offload with CUDA model preset", "step": {"key": "gpu"}}
    payload = cwm.collect_workload_mapping(
        "fake", _config(), now=NOW, force_days=1,
        page_fetcher=_slice_aware_fetcher({"ci": [_build("ci", [cpu, gpu])]}),
    )
    assert payload["totals"]["main"]["mapped_jobs"] == 1
    assert payload["totals"]["main"]["mapped_gpu_slots"] == 1
    assert payload["execution_scope_contract"] == cwm.EXECUTION_SCOPE_CONTRACT
    assert "cpu_routes" not in json.dumps(payload)
    assert "source_scope_index" not in json.dumps(payload)


@pytest.mark.parametrize("commit", [None, "", "abc123", "g" * 40])
def test_workload_ci_missing_full_source_pin_is_a_hard_failure(commit):
    build = {**_build("ci", [_job("gpu", "amd_mi250_1")]), "commit": commit}
    with pytest.raises(ValueError, match="exact full source commit"):
        cwm.collect_workload_mapping("fake", _config(), now=NOW, force_days=1,
                                    page_fetcher=_slice_aware_fetcher({"ci": [build]}))


def test_workload_wrong_cached_source_pin_is_rejected():
    cwm._SOURCE_SCOPE_INDEXES[SOURCE_COMMIT] = _mi_cpu_index("c" * 40)
    with pytest.raises(ValueError, match="commit"):
        cwm._events_from_builds([_build("ci", [_job("gpu", "amd_mi250_1")])],
                                workload="main", pipeline="ci", queue_catalog=cwm.monitored_queues(_config()),
                                start=NOW - timedelta(days=1), end=NOW)


def test_workload_source_failure_preserves_existing_output_and_is_not_query_complete(monkeypatch, tmp_path):
    from vllm import main_ci_definitions as source

    output = tmp_path / "mapping.json"
    original = b'{"generated_at":"2026-07-28T18:00:00Z","legacy":true}\n'
    output.write_bytes(original)
    monkeypatch.setattr(source, "prewarm_runtime_snapshots", lambda *args: (_ for _ in ()).throw(RuntimeError("source unavailable")))
    with pytest.raises(RuntimeError, match="source unavailable"):
        payload = cwm.collect_workload_mapping(
            "fake", _config(), now=NOW, force_days=1,
            page_fetcher=_slice_aware_fetcher({"ci": [_build("ci", [_job("gpu", "amd_mi250_1")])]}),
        )
        cwm.write_workload_mapping(output, payload)
    assert output.read_bytes() == original


def test_workload_scope_migration_preserves_omni_and_marks_old_main_incomplete_until_reconciled():
    old = cwm._empty_day("2026-07-20")
    old["workloads"]["omni"]["mapped_jobs"] = 7
    old["workloads"]["main"]["mapped_jobs"] = 999
    old["collection_complete_by_workload"] = {"omni": True, "main": True}
    old["collection_complete"] = True
    existing = {"schema_version": 2, "hardware_scope": "amd_mi_gpu",
                "generated_at": "2026-07-20T23:59:59Z", "daily": [old]}
    bounded = cwm.collect_workload_mapping("fake", _config(), existing=existing, now=NOW,
                                          force_days=2, page_fetcher=_slice_aware_fetcher({}))
    retained = next(row for row in bounded["daily"] if row["date"] == "2026-07-20")
    assert retained["workloads"]["omni"]["mapped_jobs"] == 7
    assert retained["workloads"]["main"]["mapped_jobs"] == 0
    assert retained["collection_complete_by_workload"] == {"omni": True, "main": False}
    assert retained["lower_bound"] is True
    assert bounded["window"]["collection_complete"] is False
    assert existing["daily"][0]["workloads"]["main"]["mapped_jobs"] == 999
    assert existing["generated_at"] == "2026-07-20T23:59:59Z"
    # Explicit reconciliation replaces the incomplete day with actual current
    # CI observations, not the old aggregate or an invented complete zero.
    job = _job("new", "amd_mi250_1", created_at="2026-07-20T10:00:00Z")
    build = _build("ci", [job], created_at="2026-07-20T09:59:00Z")
    reconciled = cwm.collect_workload_mapping("fake", _config(), existing=bounded, now=NOW,
                                             force_days=10, page_fetcher=_slice_aware_fetcher({"ci": [build]}))
    refreshed = next(row for row in reconciled["daily"] if row["date"] == "2026-07-20")
    assert refreshed["workloads"]["main"]["mapped_jobs"] == 1
    assert refreshed["collection_complete_by_workload"]["main"] is True


def test_workload_omni_uses_its_own_mi_routing_without_vllm_source_lookup(monkeypatch):
    from vllm import main_ci_definitions as source

    monkeypatch.setattr(source, "prewarm_runtime_snapshots", lambda *args: pytest.fail("Omni cannot use vLLM definitions"))
    omni = {**_build("vllm-omni-amd-ci", [_job("omni", "amd_mi250_1")]), "commit": None}
    payload = cwm.collect_workload_mapping("fake", _config(), now=NOW, force_days=1,
                                          page_fetcher=_slice_aware_fetcher({"vllm-omni-amd-ci": [omni]}))
    assert payload["totals"]["omni"]["mapped_jobs"] == 1
    assert payload["totals"]["main"]["mapped_jobs"] == 0


def test_workload_page_batches_exact_pins_before_event_projection(monkeypatch):
    from vllm import main_ci_definitions as source

    batches = []
    monkeypatch.setattr(source, "prewarm_runtime_snapshots", lambda pins: batches.append(pins))
    builds = [{**_build("ci", [_job(str(index), "amd_mi250_1")]), "commit": f"{index:040x}"}
              for index in range(1, 52)]
    calls = []
    def fetch_page(path, token, params):
        calls.append(params)
        return builds
    scoped, metadata = cwm._fetch_pipeline_slice("unused", "fake", "ci", NOW - timedelta(days=1), NOW,
                                                max_pages=12, page_fetcher=fetch_page)
    assert len(calls) == 1
    assert [len(batch) for batch in batches] == [50, 1]
    assert metadata["complete"] is True
    assert metadata["pages_fetched"] == 1
    assert all(build["source_scope_commit"] == build["commit"] for build in scoped)


@pytest.mark.parametrize("metadata", [["queue=B200"], {"queue": "B200"}])
def test_workload_actual_agent_overrides_requested_mi_before_source_and_aggregation(monkeypatch, metadata):
    from vllm import main_ci_definitions as source

    monkeypatch.setattr(source, "prewarm_runtime_snapshots", lambda *args: pytest.fail("foreign execution must avoid source lookup"))
    job = {**_job("foreign", "amd_mi250_1"), "agent": {"meta_data": metadata}}
    payload = cwm.collect_workload_mapping("fake", _config(), now=NOW, force_days=1,
                                          page_fetcher=_slice_aware_fetcher({"ci": [_build("ci", [job])]}))
    assert payload["totals"]["main"]["mapped_jobs"] == 0
    assert payload["totals"]["main"]["started_jobs"] == 0
    assert cwm._SOURCE_SCOPE_INDEXES == {}


def test_workload_actual_mi_agent_queue_is_used_for_gpu_slots_and_source_join():
    job = {**_job("actual-mi", "B200"), "agent": {"meta_data": ["queue=amd_mi250_1"]}}
    payload = cwm.collect_workload_mapping("fake", _config(), now=NOW, force_days=1,
                                          page_fetcher=_slice_aware_fetcher({"ci": [_build("ci", [job])]}))
    assert payload["totals"]["main"]["mapped_jobs"] == 1
    assert set(payload["totals"]["main"]["by_queue"]) == {"amd_mi250_1"}
    assert payload["totals"]["main"]["mapped_gpu_slots"] == 1


def test_workload_observed_cluster_route_also_drives_exact_source_cpu_join():
    cwm._SOURCE_SCOPE_INDEXES[SOURCE_COMMIT] = _mi_cpu_index()
    job = {**_job("cpu", "B200"), "step": {"key": "torch-abi"},
           "cluster_queue": {"key": "amd_mi250_1"}}
    payload = cwm.collect_workload_mapping("fake", _config(), now=NOW, force_days=1,
                                          page_fetcher=_slice_aware_fetcher({"ci": [_build("ci", [job])]}))
    assert payload["totals"]["main"]["mapped_jobs"] == 0


@pytest.mark.parametrize("cluster", [None, {}, {"key": None}, {"key": ""}])
def test_workload_unassigned_job_empty_cluster_retains_requested_queue(cluster):
    job = {**_job("pending", "amd_mi250_1", started_at=None, finished_at=None), "cluster_queue": cluster}
    payload = cwm.collect_workload_mapping("fake", _config(), now=NOW, force_days=1,
                                          page_fetcher=_slice_aware_fetcher({"ci": [_build("ci", [job])]}))
    assert payload["totals"]["main"]["mapped_jobs"] == 1
    assert payload["totals"]["main"]["started_jobs"] == 0


def test_workload_cluster_only_route_drives_source_cpu_exclusion():
    cwm._SOURCE_SCOPE_INDEXES[SOURCE_COMMIT] = _mi_cpu_index()
    job = {**_job("cpu", "amd_mi250_1"), "agent_query_rules": [],
           "step": {"key": "torch-abi"}, "cluster_queue": {"key": "amd_mi250_1"}}
    events, _ = cwm._events_from_builds([_build("ci", [job])], workload="main", pipeline="ci",
                                      queue_catalog=cwm.monitored_queues(_config()),
                                      start=NOW - timedelta(days=1), end=NOW)
    assert events == []


@pytest.fixture
def authenticated_source_recovery(monkeypatch):
    """Exercise production source acquisition with byte-verified Git fixtures."""
    from vllm import main_ci_definitions as source

    def blob(payload):
        oid = hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()
        return oid, {"sha": oid, "encoding": "base64", "size": len(payload),
                     "content": base64.b64encode(payload).decode()}

    def tree(rows):
        ordered = sorted(rows, key=lambda row: row["path"] + ("/" if row["type"] == "tree" else ""))
        payload = b"".join(row["mode"].lstrip("0").encode() + b" " + row["path"].encode()
                           + b"\0" + bytes.fromhex(row["sha"]) for row in ordered)
        oid = hashlib.sha1(f"tree {len(payload)}\0".encode() + payload).hexdigest()
        return oid, {"sha": oid, "truncated": False, "tree": rows}

    config_oid, config = blob(b"job_dirs: [.buildkite/test_areas]\n")
    definitions_oid, definitions = blob(
        b"steps:\n- key: torch-abi\n  label: Torch ABI\n  device: mi250_1\n  no_gpu: true\n"
        b"- key: gpu-offload\n  label: CPU Offload\n  device: mi250_1\n"
    )
    area_oid, area = tree([{"path": "testing.yaml", "sha": definitions_oid, "type": "blob", "mode": "100644"}])
    definition_tree, buildkite = tree([
        {"path": "ci_config.yaml", "sha": config_oid, "type": "blob", "mode": "100644"},
        {"path": "test_areas", "sha": area_oid, "type": "tree", "mode": "040000"},
    ])
    root_oid, root = tree([{"path": ".buildkite", "sha": definition_tree, "type": "tree", "mode": "040000"}])
    responses = {
        f"commits/{SOURCE_COMMIT}": {"sha": SOURCE_COMMIT, "tree": {"sha": root_oid}},
        f"trees/{root_oid}": root, f"trees/{definition_tree}": buildkite,
        f"trees/{area_oid}": area, f"blobs/{config_oid}": config,
        f"blobs/{definitions_oid}": definitions,
    }
    calls = []

    class Response:
        status_code = 200
        headers = {}
        def __init__(self, value):
            self.value = value
            self.content = json.dumps(value).encode()
        def raise_for_status(self):
            pass
        def json(self):
            return deepcopy(self.value)

    def post(url, **kwargs):
        assert url == "https://api.github.com/graphql"
        assert f'object(oid: "{SOURCE_COMMIT}")' in kwargs["json"]["query"]
        calls.append("graphql")
        # Error-bearing partial data is never a source of CPU classification.
        return Response({
            "errors": [{"type": "RATE_LIMITED", "message": "rate limited"}],
            "data": {"repository": {"nameWithOwner": source.REPOSITORY, "c0": {
                "__typename": "Commit", "oid": SOURCE_COMMIT,
                "tree": {"oid": "f" * 40, "entries": []},
            }}},
        })

    def get(url, **kwargs):
        path = url.removeprefix(source.API_BASE + "/git/")
        assert kwargs["headers"]["Authorization"] == "Bearer offline-source-token"
        calls.append(path)
        return Response(responses[path])

    monkeypatch.setenv("GITHUB_TOKEN", "offline-source-token")
    monkeypatch.setattr(source, "prewarm_runtime_snapshots", exact_source_prime)
    monkeypatch.setattr(source, "annotate_runtime_source_scope", exact_source_join)
    monkeypatch.setattr(source, "_RUNTIME_PRIMED_BUILDKITE_TREES", {})
    for attribute, value in (("_RUNTIME_SOURCE_STARTS", 0), ("_RUNTIME_SOURCE_STARTED_AT", None),
                             ("_RUNTIME_SOURCE_ACTIVE_SECONDS", 0.0), ("_RUNTIME_SOURCE_ACTIVE_DEPTH", 0),
                             ("_RUNTIME_SOURCE_GRAPHQL_FALLBACKS", 0)):
        monkeypatch.setattr(source, attribute, value)
    monkeypatch.setattr(source.requests, "post", post)
    monkeypatch.setattr(source.requests, "get", get)
    caches = [source.runtime_snapshot, source._runtime_commit_tree, source._runtime_tree,
              source._runtime_blob, source._runtime_definition_files]
    for cached in caches:
        cached.cache_clear()
    try:
        yield {"source": source, "calls": calls, "responses": responses, "definition_tree": definition_tree,
               "definitions": definitions, "definitions_oid": definitions_oid}
    finally:
        for cached in caches:
            cached.cache_clear()


def test_workload_graphql_error_recovers_exact_cpu_scope_and_reuses_verified_cache(
    monkeypatch, tmp_path, authenticated_source_recovery,
):
    from vllm.ci.analytics_cache import read_runtime_source_indexes

    fixture = authenticated_source_recovery
    monkeypatch.setattr(cwm, "_SOURCE_SCOPE_CACHE_DIR", tmp_path / "source-cache")
    cpu = {**_job("cpu", "amd_mi250_1"), "name": "Torch ABI", "step": {"key": "torch-abi"}}
    gpu = {**_job("gpu", "amd_mi250_1"), "name": "CPU Offload with CUDA model preset", "step": {"key": "gpu-offload"}}
    builds = {"ci": [_build("ci", [cpu, gpu])],
              "vllm-omni-amd-ci": [{**_build("vllm-omni-amd-ci", [_job("omni", "amd_mi250_1")]), "commit": None}]}
    fetcher = _slice_aware_fetcher(builds)
    payload = cwm.collect_workload_mapping("fake", _config(), now=NOW, force_days=1, page_fetcher=fetcher)
    assert payload["totals"]["main"]["mapped_jobs"] == payload["totals"]["main"]["started_jobs"] == 1
    assert payload["totals"]["omni"]["mapped_jobs"] == 1
    assert payload["generated_at"] == "2026-07-29T18:35:00Z"
    assert payload["execution_scope_contract"] == cwm.EXECUTION_SCOPE_CONTRACT
    index = read_runtime_source_indexes(cwm._SOURCE_SCOPE_CACHE_DIR)[SOURCE_COMMIT]
    assert index["definition_tree_sha"] == fixture["definition_tree"]
    assert index["cpu_routes"] == [{"key": "torch-abi", "label": "torch abi", "agent_pool": "mi250_1"}]
    assert "graphql" in fixture["calls"] and f"commits/{SOURCE_COMMIT}" in fixture["calls"]
    stats = fixture["source"].runtime_source_request_stats()
    assert stats["request_starts"] == len(fixture["calls"])
    assert stats["graphql_rest_fallbacks"] == 1
    assert stats["max_request_starts"] == 2400 and stats["max_active_source_seconds"] == 600
    starts = len(fixture["calls"])
    refreshed = cwm.collect_workload_mapping("fake", _config(), now=NOW, force_days=1, page_fetcher=fetcher)
    assert refreshed["totals"] == payload["totals"]
    assert len(fixture["calls"]) == starts


@pytest.mark.parametrize("damage", ["commit", "blob"])
def test_workload_graphql_error_never_promotes_invalid_rest_source_or_updates_output(
    tmp_path, authenticated_source_recovery, damage,
):
    fixture = authenticated_source_recovery
    if damage == "commit":
        fixture["responses"][f"commits/{SOURCE_COMMIT}"]["sha"] = "f" * 40
    else:
        fixture["definitions"]["content"] = base64.b64encode(b"x" * fixture["definitions"]["size"]).decode()
    output = tmp_path / "mapping.json"
    original = b'{"generated_at":"2026-07-28T18:00:00Z","legacy":true}\n'
    output.write_bytes(original)
    with pytest.raises(ValueError):
        payload = cwm.collect_workload_mapping(
            "fake", _config(), now=NOW, force_days=1,
            page_fetcher=_slice_aware_fetcher({"ci": [_build("ci", [_job("gpu", "amd_mi250_1")])]}),
        )
        cwm.write_workload_mapping(output, payload)
    assert output.read_bytes() == original
    assert cwm._SOURCE_SCOPE_INDEXES == {}
    assert "graphql" in fixture["calls"] and f"commits/{SOURCE_COMMIT}" in fixture["calls"]


@pytest.mark.parametrize("ambiguous_created", [None, "invalid timestamp"])
def test_mapping_window_avoids_irrelevant_lookback_pins_but_proves_late_and_ambiguous_jobs(
    monkeypatch, ambiguous_created,
):
    from vllm import main_ci_definitions as source

    old_pin, late_pin, ambiguous_pin = [letter * 40 for letter in "cde"]
    old_job = _job("old", "amd_mi250_1", created_at="2026-07-26T10:00:00Z")
    late_gpu = {**_job("late-gpu", "amd_mi250_1", created_at="2026-07-28T10:00:00Z"),
                "step": {"key": "gpu-offload"}}
    late_cpu = {**late_gpu, "id": "late-cpu", "step": {"key": "torch-abi"}}
    ambiguous = {**_job("ambiguous", "amd_mi250_1"), "created_at": ambiguous_created,
                 "runnable_at": "2026-07-29T10:00:00Z", "step": {"key": "gpu-offload"}}
    builds = {"ci": [
        {**_build("ci", [old_job], 1, created_at="2026-07-26T09:59:00Z"), "commit": old_pin},
        {**_build("ci", [late_cpu, late_gpu], 2, created_at="2026-07-27T09:59:00Z"), "commit": late_pin},
        {**_build("ci", [ambiguous], 3), "commit": ambiguous_pin},
    ]}
    source_calls = []
    def prime(pins):
        source_calls.extend(pins)
    def annotate(build, **kwargs):
        return exact_source_join(build, scope_index=kwargs.get("scope_index") or _mi_cpu_index(build["commit"]))
    monkeypatch.setattr(source, "prewarm_runtime_snapshots", prime)
    monkeypatch.setattr(source, "annotate_runtime_source_scope", annotate)
    scope = cwm._scope_ci_builds
    baseline_pages = []
    baseline_fetcher = _slice_aware_fetcher(builds)
    def baseline_page(path, token, params):
        baseline_pages.append((path, dict(params)))
        return baseline_fetcher(path, token, params)
    with monkeypatch.context() as unbounded:
        # Reproduce the original source acquisition domain as a reference;
        # its final published mappings already filtered the job-created range.
        unbounded.setattr(cwm, "_scope_ci_builds", lambda rows, pipeline="", **_: scope(rows, pipeline))
        baseline = cwm.collect_workload_mapping("fake", _config(), now=NOW, force_days=2,
                                                page_fetcher=baseline_page)
    assert set(source_calls) == {old_pin, late_pin, ambiguous_pin}

    monkeypatch.setattr(cwm, "_SOURCE_SCOPE_INDEXES", {})
    source_calls.clear()
    def available_prime(pins):
        assert old_pin not in pins, "An irrelevant expired fork must not require source acquisition"
        source_calls.extend(pins)
    monkeypatch.setattr(source, "prewarm_runtime_snapshots", available_prime)
    actual_pages = []
    actual_fetcher = _slice_aware_fetcher(builds)
    def actual_page(path, token, params):
        actual_pages.append((path, dict(params)))
        return actual_fetcher(path, token, params)
    current = cwm.collect_workload_mapping("fake", _config(), now=NOW, force_days=2,
                                           page_fetcher=actual_page)
    assert current == baseline
    assert current["totals"]["main"]["mapped_jobs"] == 2
    assert current["totals"]["main"]["started_jobs"] == 2
    assert set(source_calls) == {late_pin, ambiguous_pin}
    # The parent lookback, pagination, source completeness and clocks do not
    # change; only source proof for definitely irrelevant jobs is avoided.
    def encode(rows):
        return sorted(json.dumps(row, sort_keys=True) for row in rows)
    assert encode(actual_pages) == encode(baseline_pages)
    ci_requests = [params for path, params in actual_pages if "/ci/" in path]
    assert min(params["created_from"] for params in ci_requests) == "2026-07-25T00:00:00Z"
    assert current["query"]["end_exclusive"] == baseline["query"]["end_exclusive"]


@pytest.mark.parametrize("created", ["2026-07-26", "2026-07-26T10:00:00"])
def test_mapping_source_prefilter_keeps_timestamp_without_explicit_timezone_conservative(
    monkeypatch, created,
):
    from vllm import main_ci_definitions as source

    calls = []
    monkeypatch.setattr(source, "prewarm_runtime_snapshots", lambda pins: calls.extend(pins))
    job = {**_job("ambiguous", "amd_mi250_1"), "created_at": created}
    scoped = cwm._scope_ci_builds([_build("ci", [job])], "ci",
                                 mapping_start=NOW - timedelta(days=2), mapping_end=NOW)
    assert calls == [SOURCE_COMMIT]
    assert scoped[0]["source_scope_commit"] == SOURCE_COMMIT
