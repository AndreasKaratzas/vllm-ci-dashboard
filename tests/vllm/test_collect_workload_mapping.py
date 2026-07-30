"""Tests for privacy-safe vLLM/Omni mappings onto monitored AMD queues."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import requests

from vllm import collect_workload_mapping as cwm


NOW = datetime(2026, 7, 29, 18, 35, tzinfo=timezone.utc)


def _config() -> dict:
    return {
        "schema_version": 1,
        "projection": {"target_groups": 160},
        "scope": {"excluded_queue_classes": ["perf_eval"]},
        "workload_pipelines": {
            "omni": ["vllm-omni-amd-ci"],
            "main": ["amd-ci"],
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
        "amd-ci": [
            _build(
                "amd-ci",
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
        "amd-ci": [_build("amd-ci", [_job("main-1", "amd_mi250_1")])],
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
        "schema_version": 2,
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
        "amd-ci": [
            _build(
                "amd-ci",
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
            "schema_version": 2,
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
        "amd-ci",
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


def test_production_collection_releases_each_raw_slice_before_fetching_next(
    monkeypatch,
) -> None:
    released: list[int] = []
    calls = 0

    class RawSlice(list):
        def __init__(self, marker):
            super().__init__()
            self.marker = marker

        def __del__(self):
            released.append(self.marker)

    def fetch_slice(
        _path,
        _token,
        _pipeline,
        start,
        end,
        *,
        max_pages,
        page_fetcher,
    ):
        nonlocal calls
        if calls:
            assert calls - 1 in released
        marker = calls
        calls += 1
        return RawSlice(marker), {
            "start": cwm._utc_iso(start),
            "end_exclusive": cwm._utc_iso(end),
            "pages_fetched": 1,
            "builds_fetched": 0,
            "complete": True,
            "truncated": False,
            "error_type": None,
        }

    monkeypatch.setattr(cwm, "_fetch_pipeline_slice", fetch_slice)
    monkeypatch.setattr(
        cwm,
        "fetch_pipeline_builds",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("collector must not materialize the full build range")
        ),
    )

    cwm.collect_workload_mapping(
        "token",
        _config(),
        now=NOW,
        force_days=3,
    )

    assert calls > 2
    assert set(released) == set(range(calls))


def test_global_uuid_dedup_survives_pipeline_and_slice_streaming() -> None:
    shared_id = "globally-shared-job-id"
    responses = {
        "vllm-omni-amd-ci": [
            _build("vllm-omni-amd-ci", [_job(shared_id, "amd_mi250_1")])
        ],
        "amd-ci": [_build("amd-ci", [_job(shared_id, "amd_mi250_1")])],
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
        if "/pipelines/amd-ci/" in path:
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
