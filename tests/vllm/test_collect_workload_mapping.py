"""Tests for unique vLLM/Omni mappings onto the monitored AMD queues."""

from __future__ import annotations

from datetime import datetime, timezone

from vllm import collect_workload_mapping as cwm


NOW = datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc)


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


def _build(pipeline: str, jobs: list[dict], number: int = 1) -> dict:
    return {
        "number": number,
        "created_at": "2026-07-29T09:59:00Z",
        "pipeline": {"slug": pipeline},
        "jobs": jobs,
    }


def test_monitored_queues_is_an_exact_allowlist_and_excludes_perf_eval() -> None:
    queues = cwm.monitored_queues(_config())

    assert set(queues) == {"amd_mi250_1", "amd_mi300_4", "amd_mi325_8"}
    assert queues["amd_mi300_4"]["gpus_per_job"] == 4
    assert queues["amd_mi325_8"]["lifecycle"] == "retiring"


def test_collect_counts_unique_mappings_started_jobs_and_gpu_hours() -> None:
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

    def fetcher(path: str, _token: str, params: dict) -> list[dict]:
        if params["page"] > 1:
            return []
        slug = path.split("/pipelines/", 1)[1].split("/", 1)[0]
        return responses[slug]

    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        now=NOW,
        force_days=1,
        page_fetcher=fetcher,
    )

    assert payload["window"]["complete"] is False  # one collected day in a 14-day window
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


def test_missing_job_uuid_marks_replacement_as_lower_bound() -> None:
    responses = {
        "vllm-omni-amd-ci": [_build("vllm-omni-amd-ci", [_job(None, "amd_mi250_1")])],
        "amd-ci": [_build("amd-ci", [_job("main-1", "amd_mi250_1")])],
    }

    def fetcher(path: str, _token: str, params: dict) -> list[dict]:
        if params["page"] > 1:
            return []
        slug = path.split("/pipelines/", 1)[1].split("/", 1)[0]
        return responses[slug]

    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        now=NOW,
        force_days=1,
        page_fetcher=fetcher,
    )

    row = payload["daily"][-1]
    assert row["complete"] is False
    assert row["lower_bound"] is True
    assert payload["totals"]["omni"]["mapped_jobs"] == 0
    assert payload["query"]["diagnostics"]["omni"]["missing_job_ids"] == 1


def test_incremental_refresh_preserves_older_committed_days() -> None:
    existing = {
        "daily": [
            {
                **cwm._empty_day("2026-07-20"),
                "workloads": {
                    "omni": {
                        **cwm._empty_workload(),
                        "mapped_jobs": 7,
                    },
                    "main": cwm._empty_workload(),
                },
            },
            {
                **cwm._empty_day("2026-07-28"),
                "workloads": {
                    "omni": {
                        **cwm._empty_workload(),
                        "mapped_jobs": 99,
                    },
                    "main": cwm._empty_workload(),
                },
            },
        ]
    }

    def fetcher(path: str, _token: str, params: dict) -> list[dict]:
        if params["page"] > 1:
            return []
        slug = path.split("/pipelines/", 1)[1].split("/", 1)[0]
        return [
            _build(
                slug,
                [
                    _job(
                        f"{slug}-fresh",
                        "amd_mi250_1",
                        created_at="2026-07-28T10:00:00Z",
                    )
                ],
            )
        ]

    payload = cwm.collect_workload_mapping(
        "token",
        _config(),
        existing=existing,
        now=NOW,
        refresh_days=2,
        page_fetcher=fetcher,
    )
    by_day = {row["date"]: row for row in payload["daily"]}

    assert by_day["2026-07-20"]["workloads"]["omni"]["mapped_jobs"] == 7
    assert by_day["2026-07-28"]["workloads"]["omni"]["mapped_jobs"] == 1
    assert by_day["2026-07-28"]["workloads"]["main"]["mapped_jobs"] == 1
    assert by_day["2026-07-29"]["workloads"]["omni"]["mapped_jobs"] == 0


def test_pagination_cap_is_reported_as_incomplete() -> None:
    def fetcher(_path: str, _token: str, _params: dict) -> list[dict]:
        # A full page at the configured cap means another page may exist.
        return [{} for _ in range(cwm.PER_PAGE)]

    builds, source = cwm.fetch_pipeline_builds(
        "token",
        "amd-ci",
        datetime(2026, 7, 28, tzinfo=timezone.utc),
        NOW,
        max_pages=1,
        page_fetcher=fetcher,
    )

    assert len(builds) == cwm.PER_PAGE
    assert source["complete"] is False
    assert source["truncated"] is True
