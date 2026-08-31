"""Unit tests for ``scripts/vllm/collect_analytics.py`` window handling."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from vllm import collect_analytics as ca
from vllm.ci.analytics_cache import CacheValidationError


NOW = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)


def test_standardized_platform_labels_normalize_and_preserve_queue_family():
    assert ca.normalize_job(":amd: (MI300) Attention Kernels") == (
        "Attention Kernels"
    )
    assert ca.normalize_job(":computer: (CPU) CPU Unit Tests") == "CPU Unit Tests"
    assert ca.queue_from_result_job_name(
        ":amd: (MI355) Attention Kernels"
    ) == "amd_mi355"
    assert ca.normalize_job(
        "mi300_1: :amd: (MI300) Attention Kernels"
    ) == "Attention Kernels"
    assert ca.queue_from_result_job_name(
        "mi300_1: :amd: (MI300) Attention Kernels"
    ) == "amd_mi300_1"
    assert ca.normalize_job(
        "gpu_1: :nvidia: (H200) Basic Correctness"
    ) == "Basic Correctness"
    assert ca.queue_from_result_job_name(
        "gpu_1: :nvidia: (H200) Basic Correctness"
    ) == "nvidia_h200"
    assert ca.normalize_job(
        ":nvidia: (L4) Distributed Models"
    ) == "Distributed Models"
    assert ca.queue_from_result_job_name(
        ":nvidia: (L4) Distributed Models"
    ) == "nvidia_l4"
    assert ca.normalize_job(
        "gpu_1: :nvidia: (H200 MIG 18GB) Basic Correctness"
    ) == "Basic Correctness"
    assert ca.queue_from_result_job_name(
        "gpu_1: :nvidia: (H200 MIG 18GB) Basic Correctness"
    ) == "nvidia_h200_mig_18gb"


def test_analytics_writer_uses_compact_json(tmp_path):
    output = tmp_path / "analytics.json"
    diagnostics = ca.write_analytics(output, {"pipeline": {"builds": [1, 2]}})

    assert output.read_text() == '{"pipeline":{"builds":[1,2]}}\n'
    assert diagnostics["serialized_bytes"] == output.stat().st_size
    assert diagnostics["previous_bytes"] == 0
    assert diagnostics["component_bytes"]["pipeline"]["components"]["builds"] == 5


def test_analytics_writer_removes_legacy_reliability_copy(tmp_path):
    output = tmp_path / "analytics.json"
    authoritative = {"groups": [{"observations": [{"job_id": "kept"}]}]}

    diagnostics = ca.write_analytics(output, {
        "ci": {
            "all_main_reliability": authoritative,
            "main_builds": [{"jobs": [{"job_id": "duplicate"}]}],
            "main_builds_provenance": {"authoritative_evidence_key": "all_main_reliability"},
            "main_retry_analysis": {"available": True},
        },
    })

    block = json.loads(output.read_text())["ci"]
    assert block["all_main_reliability"] == authoritative
    assert block["main_retry_analysis"] == {"available": True}
    assert "main_builds" not in block
    assert "main_builds_provenance" not in block
    assert "reason_class" not in diagnostics


def test_analytics_writer_rejects_over_budget_payload_without_replacing_baseline(
    monkeypatch, tmp_path
):
    output = tmp_path / "analytics.json"
    output.write_text('{"validated":"baseline"}\n')
    before = output.read_bytes()
    monkeypatch.setattr(ca, "PRIVATE_ANALYTICS_MAX_BYTES", 32)

    with pytest.raises(ca.IncompleteAnalyticsCollection) as exc_info:
        ca.write_analytics(output, {"ci": {"sentinel": "x" * 64}})

    assert output.read_bytes() == before
    assert exc_info.value.provenance["collector"] == "ci_analytics"
    assert exc_info.value.provenance["reason_class"] == "payload-budget"
    assert exc_info.value.provenance["serialized_bytes"] > 32
    assert exc_info.value.provenance["max_bytes"] == 32
    assert (
        exc_info.value.provenance["github_blob_limit_bytes"]
        == ca.GITHUB_BLOB_MAX_BYTES
    )
    assert exc_info.value.provenance["effective_target_bytes"] == 32


def test_private_analytics_budget_has_github_headroom():
    assert ca.PRIVATE_ANALYTICS_TARGET_BYTES == 64 * 1024 * 1024
    assert ca.PRIVATE_ANALYTICS_MAX_BYTES == 85 * 1024 * 1024
    assert ca.PRIVATE_ANALYTICS_MAX_BYTES < 90_000_000
    assert ca.PRIVATE_ANALYTICS_TARGET_BYTES < ca.PRIVATE_ANALYTICS_MAX_BYTES
    assert ca.PRIVATE_ANALYTICS_MAX_BYTES < ca.GITHUB_BLOB_MAX_BYTES


def test_analytics_atomic_replace_failure_preserves_monolith(
    monkeypatch, tmp_path
):
    output = tmp_path / "analytics.json"
    ca.write_analytics(output, {"ci": {"sentinel": "baseline"}})
    baseline = output.read_bytes()
    real_replace = ca.os.replace

    def fail_monolith_replace(source, destination):
        if ca.Path(destination) == output:
            raise OSError("injected atomic replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(ca.os, "replace", fail_monolith_replace)

    with pytest.raises(OSError, match="injected atomic replacement failure"):
        ca.write_analytics(output, {"ci": {"sentinel": "candidate"}})

    assert output.read_bytes() == baseline
    assert not list(tmp_path.glob(".analytics.json.*.tmp"))


def test_analytics_diagnostics_report_one_run_delta(monkeypatch, tmp_path):
    output = tmp_path / "analytics.json"
    output.write_text('{"ci":{"sentinel":"old"}}\n')
    previous_bytes = output.stat().st_size

    diagnostics = ca.write_analytics(output, {
        "ci": {"sentinel": "new", "builds": [1, 2, 3]},
    })

    assert diagnostics["previous_bytes"] == previous_bytes
    assert diagnostics["delta_bytes"] == output.stat().st_size - previous_bytes
    assert diagnostics["component_bytes"]["ci"]["bytes"] > 0
    assert diagnostics["component_bytes"]["ci"]["components"]["builds"] == 7


def test_pathological_catalog_messages_are_bounded_without_changing_normal_titles(
    tmp_path,
):
    normal_title = "n" * 300
    pathological_title = "p" * (ca.CATALOG_MESSAGE_MAX_CHARS + 50)
    payload = {
        "ci": {
            "all_main_reliability": {
                "builds": [
                    {"number": 1, "message": normal_title},
                    {"number": 2, "message": pathological_title},
                ],
                "groups": [],
            },
        },
    }

    diagnostics = ca.write_analytics(tmp_path / "analytics.json", payload)
    stored = json.loads((tmp_path / "analytics.json").read_text())

    assert stored["ci"]["all_main_reliability"]["builds"][0]["message"] == normal_title
    assert stored["ci"]["all_main_reliability"]["builds"][1]["message"] == (
        pathological_title[:ca.CATALOG_MESSAGE_MAX_CHARS - 1] + "…"
    )
    assert diagnostics["catalog_messages_truncated"] == 1


def test_production_scale_reliability_is_deterministically_compacted_to_target(
    monkeypatch, tmp_path
):
    shared_detail = "evidence-" + "x" * 160
    groups = []
    for group_number in range(840):
        observations = [
            {
                "build_number": observation_number + 1,
                "job_id": f"job-{group_number}-{observation_number}",
                "observed_at": f"2026-04-{(observation_number % 28) + 1:02d}T12:00:00Z",
                "eligible_for_reliability": True,
                "detail": shared_detail,
            }
            for observation_number in range(60)
        ]
        groups.append({
            "group_id": f"group-{group_number}",
            "observation_count": 60,
            "retained_observation_count": 60,
            "retained_eligible_observation_count": 60,
            "observations_truncated": False,
            "observations": observations,
        })
    payload = {
        "ci": {
            "all_main_reliability": {
                "builds": [],
                "groups": groups,
            },
        },
    }
    monkeypatch.setattr(ca, "PRIVATE_ANALYTICS_TARGET_BYTES", 4 * 1024 * 1024)

    bounded, serialized, diagnostics = ca._prepare_private_analytics(
        tmp_path / "analytics.json",
        payload,
    )

    retained = bounded["ci"]["all_main_reliability"]["groups"][0]
    assert diagnostics["original_observations"]["ci"] == 50_400
    assert 0 < diagnostics["retained_observations"]["ci"] < 50_400
    assert diagnostics["observations_removed"] == (
        50_400 - diagnostics["retained_observations"]["ci"]
    )
    assert diagnostics["serialized_bytes"] == len(serialized.encode("utf-8"))
    assert diagnostics["serialized_bytes"] <= 4 * 1024 * 1024
    assert len(retained["observations"]) == diagnostics["applied_observation_cap"]
    assert retained["observations_truncated"] is True
    assert len(payload["ci"]["all_main_reliability"]["groups"][0]["observations"]) == 60


def test_emergency_cap_prioritizes_eligible_evidence_before_newer_exclusions():
    observations = [
        {
            "build_number": 104,
            "job_id": "newest-excluded",
            "observed_at": "2026-04-24T12:00:00Z",
            "eligible_for_reliability": False,
        },
        {
            "build_number": 103,
            "job_id": "newer-eligible",
            "observed_at": "2026-04-23T12:00:00Z",
            "eligible_for_reliability": True,
        },
        {
            "build_number": 102,
            "job_id": "older-eligible",
            "observed_at": "2026-04-22T12:00:00Z",
            "eligible_for_reliability": True,
        },
    ]
    payload = {
        "ci": {
            "all_main_reliability": {
                "groups": [{
                    "group_id": "mixed-retention",
                    "observations": observations,
                }],
            },
        },
    }

    bounded, removed = ca._cap_reliability_observations(payload, 2)

    retained_group = bounded["ci"]["all_main_reliability"]["groups"][0]
    assert removed == 1
    assert [
        row["job_id"] for row in retained_group["observations"]
    ] == ["newer-eligible", "older-eligible"]
    assert retained_group["retained_eligible_observation_count"] == 2
    assert retained_group["observations_truncated"] is True
    assert payload["ci"]["all_main_reliability"]["groups"][0]["observations"] == (
        observations
    )


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _job(name: str, dur: float, wait: float = 0.2, state: str = "passed", queue: str = "amd_mi300_1"):
    row = {"name": name, "state": state, "dur": dur}
    if wait is not None:
        row["wait"] = wait
    if queue:
        row["q"] = queue
    return row


def _build(number: int, days_ago: float, jobs: list[dict], state: str = "passed"):
    created = NOW - timedelta(days=days_ago)
    return {
        "number": number,
        "state": state,
        "created_at": _iso(created),
        "date": ca.nightly_date(_iso(created)),
        "message": "nightly",
        "author": "",
        "wall_mins": 60.0,
        "passed": sum(1 for j in jobs if j.get("state") == "passed"),
        "failed": sum(1 for j in jobs if j.get("state") in ("failed", "timed_out", "broken")),
        "soft_failed": sum(1 for j in jobs if j.get("state") == "soft_fail"),
        "total_jobs": len(jobs),
        "jobs": jobs,
        "web_url": "",
    }


def _raw_api_build(
    number: int,
    *,
    created_at: datetime | None = None,
    state: str = "passed",
    job_state: str = "passed",
    marker: str = "cached",
) -> dict:
    created = created_at or (NOW - timedelta(days=2))
    build_finished = state in ca.TERMINAL_BUILD_STATES
    job_finished = job_state in {
        "passed",
        "failed",
        "canceled",
        "skipped",
        "not_run",
        "broken",
        "timed_out",
    }
    return {
        "number": number,
        "branch": "main",
        "state": state,
        "commit": f"commit-{number}-{marker}",
        "message": f"Full CI run - nightly ({marker})",
        "created_at": created.isoformat(),
        "started_at": (created + timedelta(minutes=1)).isoformat(),
        "finished_at": (
            (created + timedelta(hours=1)).isoformat() if build_finished else None
        ),
        "web_url": f"https://buildkite.com/vllm/ci/builds/{number}",
        "jobs": [
            {
                "id": f"job-{number}",
                "type": "script",
                "name": f"Job {number}",
                "state": job_state,
                "runnable_at": (created + timedelta(minutes=1)).isoformat(),
                "started_at": (created + timedelta(minutes=2)).isoformat(),
                "finished_at": (
                    (created + timedelta(minutes=30)).isoformat()
                    if job_finished
                    else None
                ),
                "agent_query_rules": ["queue=gpu_1_queue"],
                "step": {"id": f"step-{number}", "key": f"job-{number}"},
            }
        ],
    }


def _write_test_build_cache(
    tmp_path,
    *,
    builds: list[dict],
    pipeline: str = "ci",
    watermark: datetime | None = None,
    last_full_at: datetime | None = None,
    window_days: int = 30,
):
    cache_dir = tmp_path / ca.CACHE_DIR_NAME
    watermark = watermark or (NOW - timedelta(hours=1))
    last_full_at = last_full_at or (NOW - timedelta(hours=2))
    ca.write_build_cache(
        cache_dir,
        pipeline,
        builds=builds,
        watermark=watermark,
        window_days=window_days,
        last_full_at=last_full_at,
        updated_at=watermark,
        complete_from=watermark - timedelta(days=window_days),
    )
    return cache_dir


def _legacy_reliability_payload(observation_count: int = 1) -> dict:
    raw = _raw_api_build(901, marker="legacy-migration")
    normalized = ca.build_all_main_reliability(
        [raw],
        pipeline_slug="ci",
        window_days=30,
        collection_provenance={"exhaustive": True},
    )
    stored = normalized["groups"][0]["observations"][0]
    hydrated = ca.hydrate_reliability_observations(
        normalized,
        [stored],
        pipeline_slug="ci",
    )[0]
    legacy = copy.deepcopy(normalized)
    legacy["schema_version"] = 1
    legacy["groups"][0]["observations"] = [
        copy.deepcopy(hydrated)
        for _ in range(observation_count)
    ]
    return legacy


def test_oversized_preserved_v1_reliability_migrates_losslessly_before_budget(
    monkeypatch, tmp_path
):
    legacy = _legacy_reliability_payload(observation_count=2_000)
    payload = {"ci": {"all_main_reliability": legacy}}
    migrated_payload, _ = ca._migrate_preserved_reliability_v1(payload)
    migrated_bytes = ca._compact_json_bytes(migrated_payload) + 1
    legacy_bytes = ca._compact_json_bytes(payload) + 1
    assert migrated_bytes < legacy_bytes
    target_bytes = migrated_bytes + (legacy_bytes - migrated_bytes) // 2
    monkeypatch.setattr(ca, "PRIVATE_ANALYTICS_TARGET_BYTES", target_bytes)

    bounded, serialized, diagnostics = ca._prepare_private_analytics(
        tmp_path / "analytics.json",
        payload,
    )

    migrated = bounded["ci"]["all_main_reliability"]
    source_rows = legacy["groups"][0]["observations"]
    migrated_rows = migrated["groups"][0]["observations"]
    assert migrated["schema_version"] == ca.RELIABILITY_SCHEMA_VERSION == 2
    assert len(migrated_rows) == len(source_rows) == 2_000
    assert diagnostics["applied_observation_cap"] is None
    assert diagnostics["observations_removed"] == 0
    assert diagnostics["serialized_bytes"] == len(serialized.encode("utf-8"))
    assert diagnostics["serialized_bytes"] <= target_bytes
    assert diagnostics["legacy_reliability_migrations"]["ci"] == {
        "source_schema_version": 1,
        "target_schema_version": 2,
        "builds": 1,
        "groups": 1,
        "observations": 2_000,
        "hydration_parity": True,
    }
    assert ca.hydrate_reliability_observations(
        migrated,
        migrated_rows,
        pipeline_slug="ci",
    ) == ca.hydrate_reliability_observations(
        legacy,
        source_rows,
        pipeline_slug="ci",
    )
    assert payload["ci"]["all_main_reliability"]["schema_version"] == 1


def test_valid_unversioned_legacy_reliability_still_migrates_losslessly():
    legacy = _legacy_reliability_payload()
    legacy.pop("schema_version")

    migrated_payload, diagnostics = ca._migrate_preserved_reliability_v1({
        "ci": {"all_main_reliability": legacy},
    })

    migrated = migrated_payload["ci"]["all_main_reliability"]
    assert migrated["schema_version"] == ca.RELIABILITY_SCHEMA_VERSION
    assert diagnostics["ci"]["hydration_parity"] is True
    assert ca.hydrate_reliability_observations(
        migrated,
        migrated["groups"][0]["observations"],
        pipeline_slug="ci",
    ) == ca.hydrate_reliability_observations(
        legacy,
        legacy["groups"][0]["observations"],
        pipeline_slug="ci",
    )


def test_invalid_preserved_v1_reliability_fails_closed_without_trimming_baseline(
    tmp_path,
):
    legacy = _legacy_reliability_payload()
    legacy["groups"][0]["observations"][0]["build_message"] = "tampered"
    output = tmp_path / "analytics.json"
    output.write_text('{"validated":"baseline"}\n')
    before = output.read_bytes()

    with pytest.raises(ca.IncompleteAnalyticsCollection) as exc_info:
        ca.write_analytics(output, {"ci": {"all_main_reliability": legacy}})

    assert output.read_bytes() == before
    assert exc_info.value.provenance["reason_class"] == "schema-drift"
    assert exc_info.value.provenance["failure_kind"] == (
        "invalid-legacy-reliability"
    )
    assert exc_info.value.provenance["pipeline"] == "ci"


def test_bk_get_waits_for_longest_buildkite_rate_limit_reset(monkeypatch):
    responses = []
    limited = ca.requests.Response()
    limited.status_code = 429
    limited.headers.update({
        "RateLimit-Reset": "12",
        "RateLimit-User-Reset": "41",
    })
    responses.append(limited)

    success = ca.requests.Response()
    success.status_code = 200
    success._content = b"[]"
    responses.append(success)

    sleeps = []
    monkeypatch.setattr(ca.requests, "get", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(ca.time, "sleep", sleeps.append)

    assert ca.bk_get("/builds", "fake-token") == []
    assert sleeps == [42]


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(ca.requests.Timeout("read timed out"), id="timeout"),
        pytest.param(ca.requests.ConnectionError("connection reset"), id="connection"),
    ],
)
def test_bk_get_retries_transport_errors_with_exponential_backoff(monkeypatch, error):
    success = ca.requests.Response()
    success.status_code = 200
    success._content = b'[{"number": 42}]'
    responses = [error, error, success]
    sleeps = []
    timeouts = []

    def fake_get(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ca.requests, "get", fake_get)
    monkeypatch.setattr(ca.time, "sleep", sleeps.append)

    assert ca.bk_get("/builds", "fake-token") == [{"number": 42}]
    assert sleeps == [2, 4]
    assert timeouts == [(10, 30), (10, 45), (10, 60)]


def test_bk_get_preserves_single_object_response_shape(monkeypatch):
    success = ca.requests.Response()
    success.status_code = 200
    success._content = b'{"number": 42}'
    monkeypatch.setattr(ca.requests, "get", lambda *args, **kwargs: success)

    assert ca.bk_get("/builds/42", "fake-token") == {"number": 42}


def test_bk_get_read_timeout_growth_is_capped():
    assert ca._request_timeout(0) == (10, 30)
    assert ca._request_timeout(1) == (10, 45)
    assert ca._request_timeout(ca.BK_GET_MAX_ATTEMPTS - 1) == (10, 60)


def test_bk_get_retries_transient_http_status(monkeypatch):
    unavailable = ca.requests.Response()
    unavailable.status_code = 503
    unavailable.url = "https://api.buildkite.com/v2/builds"

    success = ca.requests.Response()
    success.status_code = 200
    success._content = b"[]"

    responses = [unavailable, success]
    sleeps = []
    monkeypatch.setattr(ca.requests, "get", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(ca.time, "sleep", sleeps.append)

    assert ca.bk_get("/builds", "fake-token") == []
    assert sleeps == [2]


def test_bk_get_exhausted_transient_http_retries_raise(monkeypatch):
    unavailable = ca.requests.Response()
    unavailable.status_code = 503
    unavailable.url = "https://api.buildkite.com/v2/builds"
    attempts = []
    sleeps = []

    def fail(*args, **kwargs):
        attempts.append(1)
        return unavailable

    monkeypatch.setattr(ca.requests, "get", fail)
    monkeypatch.setattr(ca.time, "sleep", sleeps.append)

    with pytest.raises(ca.requests.HTTPError):
        ca.bk_get("/builds", "fake-token")

    assert len(attempts) == ca.BK_GET_MAX_ATTEMPTS
    assert sleeps == [2, 4, 8, 16]


def test_bk_get_exhausted_transport_retries_raise_last_error(monkeypatch):
    attempts = []
    sleeps = []

    def fail(*args, **kwargs):
        attempts.append(1)
        raise ca.requests.Timeout("read timed out")

    monkeypatch.setattr(ca.requests, "get", fail)
    monkeypatch.setattr(ca.time, "sleep", sleeps.append)

    with pytest.raises(ca.requests.Timeout, match="read timed out"):
        ca.bk_get("/builds", "fake-token")

    assert len(attempts) == ca.BK_GET_MAX_ATTEMPTS
    assert sleeps == [2, 4, 8, 16]


def test_bk_get_does_not_retry_non_transient_http_error(monkeypatch):
    unauthorized = ca.requests.Response()
    unauthorized.status_code = 401
    unauthorized.url = "https://api.buildkite.com/v2/builds"
    attempts = []
    sleeps = []

    def fail(*args, **kwargs):
        attempts.append(1)
        return unauthorized

    monkeypatch.setattr(ca.requests, "get", fail)
    monkeypatch.setattr(ca.time, "sleep", sleeps.append)

    with pytest.raises(ca.requests.HTTPError):
        ca.bk_get("/builds", "fake-token")

    assert attempts == [1]
    assert sleeps == []


class TestWindowedAnalytics:
    def test_fetch_pipeline_builds_includes_page_two_and_deduplicates(self, monkeypatch):
        page_one = [
            {"number": number, "created_at": f"2026-04-20T09:{number % 60:02d}:00Z"}
            for number in range(1, 101)
        ]
        page_two = [
            {"number": number, "created_at": f"2026-04-21T09:{number % 60:02d}:00Z"}
            for number in range(100, 121)
        ]
        calls = []

        def fake_get(path, token, params=None):
            calls.append(dict(params or {}))
            return page_one if params["page"] == 1 else page_two

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds("amd-ci", "fake-token", 30)

        assert len(builds) == 120
        assert {build["number"] for build in builds} == set(range(1, 121))
        assert [call["page"] for call in calls] == [1, 2]
        assert all(call["per_page"] == 100 for call in calls)
        assert all(call["branch"] == "main" for call in calls)
        assert all(call["include_retried_jobs"] == "true" for call in calls)
        assert provenance["exhaustive"] is True
        assert provenance["termination_reason"] == "short_page"

    def test_fetch_pipeline_builds_stops_when_a_full_page_adds_no_builds(self, monkeypatch):
        repeated_page = [{"number": number} for number in range(1, 101)]
        pages = []

        def fake_get(path, token, params=None):
            pages.append(params["page"])
            return repeated_page

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds("amd-ci", "fake-token", 30)

        assert len(builds) == 100
        assert pages == [1, 2]
        assert provenance["exhaustive"] is False
        assert provenance["termination_reason"] == "duplicate_page"

    def test_upstream_fetch_includes_page_two(self, monkeypatch):
        pages = []

        def fake_get(path, token, params=None):
            pages.append(params["page"])
            if params["page"] == 1:
                return [{"number": number} for number in range(1, 101)]
            return [{"number": number} for number in range(101, 121)]

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds("ci", "fake-token", 30)

        assert pages == [1, 2]
        assert len(builds) == 120
        assert provenance["exhaustive"] is True

    def test_fetch_pipeline_builds_marks_the_safety_cap_incomplete(self, monkeypatch):
        monkeypatch.setattr(
            ca,
            "bk_get",
            lambda path, token, params=None: [
                {"number": number} for number in range(1, 101)
            ],
        )

        builds, provenance = ca.fetch_pipeline_builds(
            "ci", "fake-token", 30, max_pages=1
        )

        assert len(builds) == 100
        assert provenance["exhaustive"] is False
        assert provenance["termination_reason"] == "max_pages"

    def test_fetch_pipeline_builds_keeps_the_richer_duplicate(self, monkeypatch):
        page_one = [{"number": number, "jobs": []} for number in range(1, 101)]
        richer = {
            "number": 100,
            "state": "passed",
            "finished_at": "2026-04-21T12:00:00Z",
            "jobs": [{"id": "retained-job"}],
        }

        def fake_get(path, token, params=None):
            return page_one if params["page"] == 1 else [richer]

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds("ci", "fake-token", 30)

        retained = next(build for build in builds if build["number"] == 100)
        assert retained["jobs"] == [{"id": "retained-job"}]
        assert provenance["exhaustive"] is True
        assert provenance["termination_reason"] == "short_page"

    @pytest.mark.parametrize(
        "invalid_row",
        [
            pytest.param(None, id="not-an-object"),
            pytest.param({"number": "not-a-number"}, id="invalid-number"),
            pytest.param({"number": 0}, id="non-positive-number"),
            pytest.param({"number": True}, id="boolean-number"),
        ],
    )
    def test_fetch_pipeline_builds_fails_closed_on_invalid_rows(
        self, monkeypatch, invalid_row
    ):
        monkeypatch.setattr(
            ca,
            "bk_get",
            lambda path, token, params=None: [
                invalid_row,
                {"number": 42, "created_at": "2026-07-12T09:00:00Z"},
            ],
        )

        with pytest.raises(RuntimeError, match="invalid row 1 on page 1"):
            ca.fetch_pipeline_builds("ci", "fake-token", 30)

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(None, id="null"),
            pytest.param({}, id="object"),
            pytest.param("not-a-list", id="string"),
        ],
    )
    def test_fetch_pipeline_builds_fails_closed_on_non_list_page(
        self, monkeypatch, payload
    ):
        monkeypatch.setattr(ca, "bk_get", lambda path, token, params=None: payload)

        with pytest.raises(RuntimeError, match="expected a JSON list"):
            ca.fetch_pipeline_builds("ci", "fake-token", 30)

    def test_cached_aliases_preserve_nightly_filter_and_queue(self):
        cached = _raw_api_build(42)
        cached.pop("message")
        cached["canonical_nightly"] = True
        cached["jobs"][0].pop("agent_query_rules")
        cached["jobs"][0]["q"] = "gpu_1_queue"

        builds = ca.summarize_pipeline_builds(
            "ci",
            [cached],
            nightly_only=True,
            name_pattern=ca.NIGHTLY_NAME_PATTERNS_BY_SLUG["ci"],
        )

        assert [build["number"] for build in builds] == [42]
        assert builds[0]["message"] == ca.CACHE_NIGHTLY_MESSAGE["ci"]
        assert re.search(
            ca.NIGHTLY_NAME_PATTERNS_BY_SLUG["ci"],
            builds[0]["message"],
            re.IGNORECASE,
        )
        expected_build_url = "https://buildkite.com/vllm/ci/builds/42"
        assert builds[0]["web_url"] == expected_build_url
        assert ca.gating_build_summary(builds[0])["web_url"] == expected_build_url
        assert builds[0]["jobs"][0]["q"] == "gpu_1_queue"
        reliability = ca.build_all_main_reliability(
            ca._reliability_builds_with_cache_aliases([cached], "ci"),
            pipeline_slug="ci",
            window_days=30,
            nightly_pattern=ca.NIGHTLY_NAME_PATTERNS_BY_SLUG["ci"],
        )
        assert reliability["cohort"]["canonical_nightly_build_count"] == 1

    def test_cached_upstream_daily_restores_message_but_stays_out_of_nightly(self):
        cached = _raw_api_build(43)
        cached.pop("message")
        cached["canonical_nightly"] = False
        cached["scheduled_gating_kind"] = "daily"

        compatible = ca._reliability_builds_with_cache_aliases([cached], "ci")
        assert compatible[0]["message"] == "Full CI run - daily"
        assert "message" not in cached

        nightly = ca.summarize_pipeline_builds(
            "ci",
            compatible,
            nightly_only=True,
            name_pattern=ca.NIGHTLY_NAME_PATTERNS_BY_SLUG["ci"],
        )
        assert nightly == []

        all_main = ca.build_all_main_reliability(
            compatible,
            pipeline_slug="ci",
            window_days=30,
            nightly_pattern=ca.NIGHTLY_NAME_PATTERNS_BY_SLUG["ci"],
        )
        assert all_main["builds"][0]["message"] == "Full CI run - daily"
        assert all_main["builds"][0]["is_canonical_nightly"] is False


class TestIncrementalAnalyticsCache:
    def test_incremental_cache_restores_exact_previous_popup_title(
        self, monkeypatch, tmp_path
    ):
        exact_title = (
            "Full CI run - nightly — exact release qualification title "
            "with popup-visible context"
        )
        full_build = _raw_api_build(71, marker="title-parity")
        full_build["message"] = exact_title
        previous_reliability = ca.build_all_main_reliability(
            [full_build],
            pipeline_slug="ci",
            window_days=30,
            collection_provenance={"exhaustive": True},
        )
        cache_dir = _write_test_build_cache(
            tmp_path,
            builds=[full_build],
        )
        monkeypatch.setattr(ca, "bk_get", lambda *args, **kwargs: [])

        cached_builds, provenance = ca.fetch_pipeline_builds(
            "ci",
            "fake-token",
            30,
            cache_dir=cache_dir,
            ref_now=NOW,
        )

        assert "message" not in cached_builds[0]
        compatible = ca._reliability_builds_with_cache_aliases(
            cached_builds,
            "ci",
            previous_reliability,
        )
        assert compatible[0]["message"] == exact_title
        nightly = ca.summarize_pipeline_builds(
            "ci",
            compatible,
            nightly_only=True,
            name_pattern=ca.NIGHTLY_NAME_PATTERNS_BY_SLUG["ci"],
        )
        assert nightly[0]["message"] == exact_title
        refreshed = ca.build_all_main_reliability(
            compatible,
            pipeline_slug="ci",
            window_days=30,
            collection_provenance=provenance,
        )
        assert refreshed["builds"][0]["message"] == exact_title
        hydrated = ca.hydrate_reliability_observations(
            refreshed,
            refreshed["groups"][0]["observations"],
            pipeline_slug="ci",
        )
        assert hydrated[0]["build_message"] == exact_title

    def test_steady_state_uses_overlapping_created_and_finished_legs(
        self, monkeypatch, tmp_path
    ):
        watermark = NOW - timedelta(hours=1)
        cached = _raw_api_build(1)
        fresh = _raw_api_build(2, created_at=NOW - timedelta(minutes=30), marker="fresh")
        cache_dir = _write_test_build_cache(
            tmp_path,
            builds=[cached],
            watermark=watermark,
        )
        calls = []

        def fake_get(path, token, params=None):
            calls.append((path, dict(params or {})))
            if "created_from" in params:
                return [fresh]
            if "finished_from" in params:
                return []
            raise AssertionError(f"unexpected request: {path} {params}")

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds(
            "ci",
            "fake-token",
            30,
            cache_dir=cache_dir,
            ref_now=NOW,
        )

        assert [build["number"] for build in builds] == [2, 1]
        assert provenance["fetch_mode"] == "incremental"
        assert provenance["created_from"] == (NOW - timedelta(days=30)).isoformat()
        assert provenance["cache"]["cache_written"] is True
        overlap = (watermark - ca.ANALYTICS_CACHE_OVERLAP).isoformat()
        assert [params.get("created_from") for _, params in calls] == [overlap, None]
        assert [params.get("finished_from") for _, params in calls] == [None, overlap]
        assert all(params["include_retried_jobs"] == "true" for _, params in calls)

        reloaded = ca.load_build_cache(
            cache_dir,
            "ci",
            cutoff=NOW - timedelta(days=30),
            window_days=30,
            ref_now=NOW,
        )
        assert reloaded.valid is True
        assert _iso_or_datetime(reloaded.watermark) == NOW
        assert _iso_or_datetime(reloaded.complete_from) == NOW - timedelta(days=30)

    def test_incremental_cache_write_pressure_keeps_fetched_builds_without_full_refetch(
        self, monkeypatch, tmp_path, caplog
    ):
        cache_dir = _write_test_build_cache(
            tmp_path,
            builds=[_raw_api_build(1)],
        )
        calls = []

        def fake_get(path, token, params=None):
            calls.append(dict(params or {}))
            return []

        def reject_cache_write(*args, **kwargs):
            raise CacheValidationError("oversize")

        monkeypatch.setattr(ca, "bk_get", fake_get)
        monkeypatch.setattr(ca, "write_build_cache", reject_cache_write)

        builds, provenance = ca.fetch_pipeline_builds(
            "ci", "fake-token", 30, cache_dir=cache_dir, ref_now=NOW
        )

        assert [build["number"] for build in builds] == [1]
        assert provenance["fetch_mode"] == "incremental"
        assert provenance["exhaustive"] is True
        assert provenance["cache"]["cache_written"] is False
        assert provenance["cache"]["cache_disabled"] is True
        assert provenance["cache"]["cache_disabled_reason"] == "write_oversize"
        assert len(calls) == 2
        assert "continuing with fetched builds" in caplog.text

    def test_full_fetch_cache_write_pressure_keeps_complete_builds(
        self, monkeypatch, tmp_path, caplog
    ):
        cache_dir = tmp_path / ca.CACHE_DIR_NAME
        full = _raw_api_build(22, marker="cache-disabled")
        calls = []

        def fake_get(path, token, params=None):
            calls.append(dict(params or {}))
            return [full]

        def reject_cache_write(*args, **kwargs):
            raise CacheValidationError("oversize")

        monkeypatch.setattr(ca, "bk_get", fake_get)
        monkeypatch.setattr(ca, "write_build_cache", reject_cache_write)

        builds, provenance = ca.fetch_pipeline_builds(
            "ci", "fake-token", 30, cache_dir=cache_dir, ref_now=NOW
        )

        assert [build["number"] for build in builds] == [22]
        assert provenance["fetch_mode"] == "full"
        assert provenance["exhaustive"] is True
        assert provenance["cache"]["cache_written"] is False
        assert provenance["cache"]["cache_disabled"] is True
        assert provenance["cache"]["cache_disabled_reason"] == "write_oversize"
        assert len(calls) == 1
        assert "continuing with fetched builds" in caplog.text

    def test_running_cached_job_is_refreshed_from_individual_endpoint(
        self, monkeypatch, tmp_path
    ):
        running = _raw_api_build(7, state="running", job_state="running")
        completed = _raw_api_build(7, state="passed", job_state="passed", marker="complete")
        cache_dir = _write_test_build_cache(tmp_path, builds=[running])
        calls = []

        def fake_get(path, token, params=None):
            calls.append((path, dict(params or {})))
            if path.endswith("/builds"):
                return []
            if path.endswith("/builds/7"):
                return completed
            raise AssertionError(path)

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds(
            "ci", "fake-token", 30, cache_dir=cache_dir, ref_now=NOW
        )

        assert builds[0]["state"] == "passed"
        assert builds[0]["jobs"][0]["state"] == "passed"
        assert builds[0]["commit"].endswith("-complete")
        assert provenance["fetch_mode"] == "incremental"
        assert provenance["cache"]["refresh_build_numbers"] == [7]
        individual = next(call for call in calls if call[0].endswith("/builds/7"))
        assert individual[1] == {"include_retried_jobs": "true"}
        assert len(calls) == 3

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param([{"number": 7}], id="list-wrapper"),
            pytest.param({"number": 8}, id="wrong-build"),
            pytest.param(None, id="null"),
        ],
    )
    def test_individual_build_refresh_requires_matching_object(
        self, monkeypatch, payload
    ):
        monkeypatch.setattr(ca, "bk_get", lambda path, token, params=None: payload)

        with pytest.raises(RuntimeError, match="matching JSON build object"):
            ca._fetch_individual_build("ci", "fake-token", 7)

    def test_finished_leg_recovers_build_created_before_overlap(
        self, monkeypatch, tmp_path
    ):
        cache_dir = _write_test_build_cache(
            tmp_path,
            builds=[_raw_api_build(1)],
        )
        late = _raw_api_build(
            9,
            created_at=NOW - timedelta(days=10),
            marker="late-finished",
        )

        def fake_get(path, token, params=None):
            if "created_from" in params:
                return []
            if "finished_from" in params:
                return [late]
            raise AssertionError(path)

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds(
            "ci", "fake-token", 30, cache_dir=cache_dir, ref_now=NOW
        )

        recovered = next(build for build in builds if build["number"] == 9)
        assert recovered["commit"].endswith("-late-finished")
        assert provenance["cache"]["finished_builds"] == 1

    def test_duplicate_builds_merge_with_freshest_leg_winning(
        self, monkeypatch, tmp_path
    ):
        cache_dir = _write_test_build_cache(
            tmp_path,
            builds=[_raw_api_build(5, marker="cached")],
        )
        created = _raw_api_build(5, marker="created-leg")
        finished = _raw_api_build(5, marker="finished-leg")

        def fake_get(path, token, params=None):
            if "created_from" in params:
                return [created]
            if "finished_from" in params:
                return [finished]
            raise AssertionError(path)

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, _ = ca.fetch_pipeline_builds(
            "ci", "fake-token", 30, cache_dir=cache_dir, ref_now=NOW
        )

        assert len(builds) == 1
        assert builds[0]["commit"].endswith("-finished-leg")

    @pytest.mark.parametrize("cache_case", ["missing", "malformed", "tampered", "expanded"])
    def test_cache_miss_invalid_tamper_or_window_expansion_forces_full_fetch(
        self, monkeypatch, tmp_path, cache_case
    ):
        cache_dir = tmp_path / ca.CACHE_DIR_NAME
        if cache_case != "missing":
            cache_dir = _write_test_build_cache(
                tmp_path,
                builds=[_raw_api_build(1)],
                window_days=7 if cache_case == "expanded" else 30,
            )
        cache_path = cache_dir / "ci.json"
        if cache_case == "malformed":
            cache_path.write_text("{not json")
        elif cache_case == "tampered":
            payload = json.loads(cache_path.read_text())
            payload["builds"][0]["state"] = "failed"
            cache_path.write_text(json.dumps(payload))

        full = _raw_api_build(22, marker=f"full-{cache_case}")
        calls = []

        def fake_get(path, token, params=None):
            calls.append(dict(params or {}))
            return [full]

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds(
            "ci", "fake-token", 30, cache_dir=cache_dir, ref_now=NOW
        )

        assert [build["number"] for build in builds] == [22]
        assert provenance["fetch_mode"] == "full"
        assert provenance["cache"]["decision"].startswith("cache_")
        assert len(calls) == 1
        assert calls[0]["created_from"] == (NOW - timedelta(days=30)).isoformat()
        assert "finished_from" not in calls[0]

    def test_partial_incremental_retries_one_full_fetch(self, monkeypatch, tmp_path):
        watermark = NOW - timedelta(hours=1)
        cache_dir = _write_test_build_cache(
            tmp_path,
            builds=[_raw_api_build(1)],
            watermark=watermark,
        )
        overlap = (watermark - ca.ANALYTICS_CACHE_OVERLAP).isoformat()
        cutoff = (NOW - timedelta(days=30)).isoformat()
        full = _raw_api_build(40, marker="fallback-full")
        calls = []

        def fake_get(path, token, params=None):
            params = dict(params or {})
            calls.append(params)
            if params.get("created_from") == overlap:
                return [{"number": number} for number in range(1, 101)]
            if params.get("finished_from") == overlap:
                return []
            if params.get("created_from") == cutoff:
                return [full]
            raise AssertionError(params)

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds(
            "ci",
            "fake-token",
            30,
            max_pages=1,
            cache_dir=cache_dir,
            ref_now=NOW,
        )

        assert [build["number"] for build in builds] == [40]
        assert provenance["fetch_mode"] == "full_after_incremental"
        attempt = provenance["cache"]["incremental_attempt"]
        assert attempt["failure"] == "incremental_pagination_incomplete"
        assert [params.get("created_from") for params in calls] == [overlap, None, cutoff]

    def test_suspicious_incremental_growth_reconciles_once_before_cache_write(
        self, monkeypatch, tmp_path
    ):
        watermark = NOW - timedelta(hours=1)
        cache_dir = _write_test_build_cache(
            tmp_path,
            builds=[_raw_api_build(1)],
            watermark=watermark,
        )
        cache_path = cache_dir / "ci.json"
        before = cache_path.read_bytes()
        overlap = (watermark - ca.ANALYTICS_CACHE_OVERLAP).isoformat()
        cutoff = (NOW - timedelta(days=30)).isoformat()
        full = _raw_api_build(40, marker="reconciled-full")
        calls = []

        def fake_get(path, token, params=None):
            params = dict(params or {})
            calls.append(params)
            if params.get("created_from") == overlap:
                return [_raw_api_build(2), _raw_api_build(3)]
            if params.get("finished_from") == overlap:
                return []
            if params.get("created_from") == cutoff:
                return [full]
            raise AssertionError(params)

        monkeypatch.setattr(ca, "bk_get", fake_get)
        monkeypatch.setattr(
            ca,
            "ANALYTICS_CACHE_SUSPICIOUS_GROWTH_MIN_BYTES",
            1,
        )
        monkeypatch.setattr(
            ca,
            "ANALYTICS_CACHE_SUSPICIOUS_GROWTH_RATIO",
            1.01,
        )

        builds, provenance = ca.fetch_pipeline_builds(
            "ci",
            "fake-token",
            30,
            cache_dir=cache_dir,
            ref_now=NOW,
        )

        assert [build["number"] for build in builds] == [40]
        assert provenance["fetch_mode"] == "full_after_incremental"
        attempt = provenance["cache"]["incremental_attempt"]
        assert attempt["failure"] == "suspicious_incremental_materialization"
        assert attempt["materialized_delta_bytes"] > 0
        assert attempt["cache_written"] is False
        assert cache_path.read_bytes() != before
        reloaded = ca.load_build_cache(
            cache_dir,
            "ci",
            cutoff=NOW - timedelta(days=30),
            window_days=30,
            ref_now=NOW,
        )
        assert [build["number"] for build in reloaded.builds] == [40]
        assert [params.get("created_from") for params in calls] == [
            overlap,
            None,
            cutoff,
        ]

    def test_incomplete_full_fetch_raises_and_leaves_cache_unchanged(
        self, monkeypatch, tmp_path
    ):
        cache_dir = _write_test_build_cache(
            tmp_path,
            builds=[_raw_api_build(1)],
            watermark=NOW - timedelta(hours=1),
        )
        cache_path = cache_dir / "ci.json"
        before = cache_path.read_bytes()

        monkeypatch.setattr(
            ca,
            "bk_get",
            lambda path, token, params=None: [
                {"number": number} for number in range(1, 101)
            ],
        )

        with pytest.raises(ca.IncompleteAnalyticsCollection) as exc_info:
            ca.fetch_pipeline_builds(
                "ci",
                "fake-token",
                30,
                max_pages=1,
                cache_dir=cache_dir,
                ref_now=NOW,
            )

        assert exc_info.value.provenance["exhaustive"] is False
        assert exc_info.value.provenance["fetch_mode"] == "full_after_incremental"
        assert cache_path.read_bytes() == before

    def test_cache_miss_with_incomplete_full_fetch_fails_without_writing(
        self, monkeypatch, tmp_path
    ):
        cache_dir = tmp_path / ca.CACHE_DIR_NAME
        monkeypatch.setattr(
            ca,
            "bk_get",
            lambda path, token, params=None: [
                {"number": number} for number in range(1, 101)
            ],
        )

        with pytest.raises(ca.IncompleteAnalyticsCollection) as exc_info:
            ca.fetch_pipeline_builds(
                "ci",
                "fake-token",
                30,
                max_pages=1,
                cache_dir=cache_dir,
                ref_now=NOW,
            )

        assert exc_info.value.provenance["fetch_mode"] == "full"
        assert exc_info.value.provenance["exhaustive"] is False
        assert not (cache_dir / "ci.json").exists()

    def test_daily_reconciliation_uses_full_fetch_at_twenty_four_hours(
        self, monkeypatch, tmp_path
    ):
        cache_dir = _write_test_build_cache(
            tmp_path,
            builds=[_raw_api_build(1)],
            watermark=NOW - timedelta(hours=1),
            last_full_at=NOW - timedelta(hours=24),
        )
        calls = []

        def fake_get(path, token, params=None):
            calls.append(dict(params or {}))
            return [_raw_api_build(2, marker="daily-full")]

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds(
            "ci", "fake-token", 30, cache_dir=cache_dir, ref_now=NOW
        )

        assert [build["number"] for build in builds] == [2]
        assert provenance["fetch_mode"] == "full"
        assert provenance["cache"]["decision"] == "daily_reconciliation"
        assert len(calls) == 1
        assert calls[0]["created_from"] == (NOW - timedelta(days=30)).isoformat()

    def test_utc_date_rollover_forces_full_reconciliation_before_twenty_four_hours(
        self, monkeypatch, tmp_path
    ):
        ref_now = datetime(2026, 4, 21, 0, 30, tzinfo=timezone.utc)
        cache_dir = _write_test_build_cache(
            tmp_path,
            builds=[_raw_api_build(1)],
            watermark=datetime(2026, 4, 20, 23, 30, tzinfo=timezone.utc),
            last_full_at=datetime(2026, 4, 20, 22, 30, tzinfo=timezone.utc),
        )
        calls = []

        def fake_get(path, token, params=None):
            calls.append(dict(params or {}))
            return [_raw_api_build(2, marker="rollover-full")]

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds(
            "ci", "fake-token", 30, cache_dir=cache_dir, ref_now=ref_now
        )

        assert [build["number"] for build in builds] == [2]
        assert provenance["cache"]["decision"] == "utc_day_reconciliation"
        assert len(calls) == 1
        assert calls[0]["created_from"] == (
            ref_now - timedelta(days=30)
        ).isoformat()

    def test_main_freezes_one_clock_for_fetch_cache_results_and_windows(
        self, monkeypatch, tmp_path
    ):
        class MovingDatetime(datetime):
            calls = 0

            @classmethod
            def now(cls, tz=None):
                cls.calls += 1
                value = NOW + timedelta(hours=cls.calls - 1)
                return cls.fromtimestamp(value.timestamp(), tz=tz)

        fetch_params = []
        result_times = []

        def fake_get(path, token, params=None):
            fetch_params.append(dict(params or {}))
            return []

        def fake_results(*args, **kwargs):
            result_times.append(kwargs["now"])
            return []

        monkeypatch.setattr(ca, "datetime", MovingDatetime)
        monkeypatch.setattr(ca, "bk_get", fake_get)
        monkeypatch.setattr(ca, "load_test_result_builds", fake_results)
        monkeypatch.setenv("BUILDKITE_TOKEN", "fake-token")
        monkeypatch.setattr(
            ca.sys,
            "argv",
            [
                "collect_analytics.py",
                "--days",
                "30",
                "--pipeline",
                "both",
                "--output",
                str(tmp_path),
            ],
        )

        ca.main()

        assert MovingDatetime.calls == 1
        assert result_times == [NOW, NOW]
        created_filters = [
            params["created_from"]
            for params in fetch_params
            if "created_from" in params
        ]
        assert created_filters == [(NOW - timedelta(days=30)).isoformat()] * 2
        payload = json.loads((tmp_path / "analytics.json").read_text())
        assert {block["generated_at"] for block in payload.values()} == {
            "2026-04-20T12:00:00Z"
        }

    @pytest.mark.parametrize("cache_written", [True, False])
    def test_main_emits_fail_closed_actions_cache_save_decision(
        self, monkeypatch, tmp_path, cache_written
    ):
        github_output = tmp_path / "github-output.txt"

        def fake_fetch(_pipeline_slug, _token, _days):
            return [], {
                "exhaustive": True,
                "cache": {"cache_written": cache_written},
            }

        monkeypatch.setenv("BUILDKITE_TOKEN", "fake-token")
        monkeypatch.setattr(ca, "fetch_pipeline_builds", fake_fetch)
        monkeypatch.setattr(ca, "load_test_result_builds", lambda *args, **kwargs: [])
        monkeypatch.setattr(
            ca.sys,
            "argv",
            [
                "collect_analytics.py",
                "--days",
                "30",
                "--pipeline",
                "ci",
                "--output",
                str(tmp_path),
                "--github-output",
                str(github_output),
            ],
        )

        ca.main()

        expected = "true" if cache_written else "false"
        assert github_output.read_text() == f"analytics_cache_save={expected}\n"


def _iso_or_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return ca.parse_ts(value)


class TestWindowedAnalyticsMain:
    def test_targeted_pipeline_refresh_preserves_other_pipeline_block(
        self, monkeypatch, tmp_path
    ):
        preserved_amd = {
            "display_name": "AMD CI",
            "sentinel": "preserve-me",
            "summary": {
                "total_builds": 1,
                "terminal_builds": 1,
                "build_pass_rate_pct": 100.0,
                "jobs_with_failures": 0,
                "total_jobs_tracked": 1,
            },
        }
        (tmp_path / "analytics.json").write_text(
            json.dumps({"amd-ci": preserved_amd})
        )
        fresh = _raw_api_build(88, marker="targeted")

        def fake_fetch(pipeline_slug, token, days, max_pages=None):
            assert pipeline_slug == "ci"
            return [fresh], {
                "created_from": (NOW - timedelta(days=30)).isoformat(),
                "exhaustive": True,
            }

        monkeypatch.setenv("BUILDKITE_TOKEN", "fake-token")
        monkeypatch.setattr(ca, "fetch_pipeline_builds", fake_fetch)
        monkeypatch.setattr(ca, "load_test_result_builds", lambda *args, **kwargs: [])
        monkeypatch.setattr(
            ca.sys,
            "argv",
            [
                "collect_analytics.py",
                "--days",
                "30",
                "--pipeline",
                "ci",
                "--output",
                str(tmp_path),
            ],
        )

        ca.main()

        payload = json.loads((tmp_path / "analytics.json").read_text())
        assert payload["amd-ci"] == preserved_amd
        assert payload["ci"]["pipeline"] == "ci"
        assert payload["ci"]["builds"][0]["number"] == 88

    def test_gating_nightlies_publish_before_private_analytics_budget_failure(
        self, monkeypatch, tmp_path
    ):
        build = _build(88, 0.5, [_job("Fresh gating job", 10)])
        monkeypatch.delenv("BUILDKITE_TOKEN", raising=False)
        monkeypatch.setattr(
            ca,
            "load_test_result_builds",
            lambda *args, **kwargs: [build],
        )

        def fail_private_analytics(*args, **kwargs):
            raise ca.IncompleteAnalyticsCollection("injected payload budget failure")

        monkeypatch.setattr(ca, "write_analytics", fail_private_analytics)
        monkeypatch.setattr(ca.sys, "argv", [
            "collect_analytics.py",
            "--days", "30",
            "--pipeline", "ci",
            "--output", str(tmp_path),
        ])

        with pytest.raises(
            ca.IncompleteAnalyticsCollection,
            match="injected payload budget failure",
        ):
            ca.main()

        gating = json.loads((tmp_path / "gating_nightlies.json").read_text())
        assert gating["ci"]["builds"][0]["number"] == 88
        assert gating["ci"]["builds"][0]["jobs"][0]["name"] == "Fresh gating job"
        assert not (tmp_path / "analytics.json").exists()

    def test_main_emits_all_main_reliability_for_both_and_retries_only_upstream(self, monkeypatch, tmp_path):
        messages = {
            "amd-ci": "AMD Full CI Run - nightly",
            "ci": "Full CI run - nightly",
        }

        def fake_fetch(pipeline_slug, token, days, max_pages=None):
            number = 101 if pipeline_slug == "amd-ci" else 202
            return [{
                "number": number,
                "branch": "main",
                "state": "passed",
                "commit": f"commit-{number}",
                "message": messages[pipeline_slug],
                "created_at": "2026-07-12T09:00:00Z",
                "started_at": "2026-07-12T09:01:00Z",
                "finished_at": "2026-07-12T10:00:00Z",
                "web_url": f"https://buildkite.com/vllm/{pipeline_slug}/builds/{number}",
                "jobs": [
                    {
                        "id": f"failed-{number}",
                        "type": "script",
                        "name": "Retry group",
                        "state": "failed",
                        "retried_in_job_id": f"passed-{number}",
                        "runnable_at": "2026-07-12T09:01:00Z",
                        "started_at": "2026-07-12T09:02:00Z",
                        "finished_at": "2026-07-12T09:03:00Z",
                        "agent_query_rules": ["queue=gpu_1_queue"],
                        "step": {"id": f"step-{number}", "key": "retry-group"},
                    },
                    {
                        "id": f"passed-{number}",
                        "type": "script",
                        "name": "Retry group",
                        "state": "passed",
                        "retry_type": "automatic",
                        "runnable_at": "2026-07-12T09:03:00Z",
                        "started_at": "2026-07-12T09:04:00Z",
                        "finished_at": "2026-07-12T09:05:00Z",
                        "agent_query_rules": ["queue=gpu_1_queue"],
                        "step": {"id": f"step-{number}", "key": "retry-group"},
                    },
                ],
            }], {
                "created_from": "2026-06-12T00:00:00Z",
                "page_size": 100,
                "max_pages": 50,
                "pages_fetched": 1,
                "termination_reason": "short_page",
                "exhaustive": True,
            }

        monkeypatch.setenv("BUILDKITE_TOKEN", "fake-token")
        monkeypatch.setattr(ca, "fetch_pipeline_builds", fake_fetch)
        monkeypatch.setattr(ca, "load_test_result_builds", lambda *args, **kwargs: [])
        monkeypatch.setattr(ca.sys, "argv", [
            "collect_analytics.py",
            "--days", "30",
            "--pipeline", "both",
            "--output", str(tmp_path),
        ])

        ca.main()

        payload = json.loads((tmp_path / "analytics.json").read_text())
        amd_block = payload["amd-ci"]
        assert amd_block["pass_rate_contract_version"] == 1
        assert amd_block["transition_policy_id"] == "confirmed-incidents-v1"
        assert (
            amd_block["nightly_change_history"][0]["policy_id"]
            == "confirmed-incidents-v1"
        )
        assert amd_block["all_main_reliability"]["cohort"]["id"] == "amd-ci-main-completed-pass-fail"
        assert (
            amd_block["all_main_reliability"]["provenance"]["observation_limit_per_group"]
            == ca.AMD_MAIN_OBSERVATION_LIMIT
        )
        assert "main_retry_analysis" not in amd_block
        block = payload["ci"]
        assert block["pass_rate_contract_version"] == 1
        assert block["transition_policy_id"] == "confirmed-incidents-v1"
        assert block["nightly_change_history"][0]["policy_id"] == "confirmed-incidents-v1"
        reliability = block["all_main_reliability"]
        assert reliability["cohort"]["id"] == "ci-main-completed-pass-fail"
        assert reliability["cohort"]["pipeline"] == "ci"
        assert block["main_retry_analysis"]["summary"]["builds_evaluated"] == 1
        assert block["main_retry_analysis"]["summary"]["retry_attempt_count"] == 1
        assert block["main_retry_analysis"]["summary"]["failed_then_passed_recovery_count"] == 1
        assert "/vllm/ci/builds/" in block["main_retry_analysis"]["retry_attempts"][0]["url"]

    def test_tokenless_refresh_preserves_complete_main_retry_ledger(self, monkeypatch, tmp_path):
        previous_build = _build(202, 0.5, [_job("No retry in compact history", 10)])
        previous_build["web_url"] = "https://buildkite.com/vllm/ci/builds/202"
        reliability = {
            "schema_version": 1,
            "cohort": {
                "id": "ci-main-completed-pass-fail",
                "pipeline": "ci",
                "branch": "main",
                "build_states": ["failed", "passed"],
                "build_count": 1,
                "canonical_nightly_build_count": 1,
                "non_nightly_main_build_count": 0,
                "exhaustive": True,
            },
            "denominator": {"eligible_observations": 0},
            "provenance": {
                "pipeline": "ci",
                "endpoint": "/organizations/vllm/pipelines/ci/builds",
                "query": {"branch": "main"},
                "collection": {"exhaustive": True},
            },
            "builds": [{
                "number": 202,
                "branch": "main",
                "state": "passed",
                "finished_at": "2026-04-20T12:00:00Z",
                "url": "https://buildkite.com/vllm/ci/builds/202",
            }],
            "groups": [],
        }
        preserved_retry = {
            "available": True,
            "summary": {
                "builds_evaluated": 30,
                "builds_with_retries": 1,
                "retry_attempt_count": 1,
                "failed_then_passed_recovery_count": 0,
            },
            "retry_attempts": [{
                "build_number": 202,
                "job_id": "older-retry",
                "url": "https://buildkite.com/vllm/ci/builds/202/steps/canvas?jid=older-retry",
            }],
            "failed_then_passed_recoveries": [],
            "provenance": {
                "source_pipeline": "ci",
                "complete": True,
                "cohort_build_numbers": [202],
            },
        }
        (tmp_path / "analytics.json").write_text(json.dumps({
            "ci": {
                "display_name": "Upstream CI",
                "builds": [previous_build],
                "all_main_reliability": reliability,
                "main_retry_analysis": preserved_retry,
            },
        }))
        monkeypatch.delenv("BUILDKITE_TOKEN", raising=False)
        monkeypatch.setattr(ca, "load_test_result_builds", lambda *args, **kwargs: [])
        monkeypatch.setattr(ca.sys, "argv", [
            "collect_analytics.py",
            "--days", "30",
            "--pipeline", "ci",
            "--output", str(tmp_path),
        ])

        ca.main()

        refreshed = json.loads((tmp_path / "analytics.json").read_text())
        assert refreshed["ci"]["main_retry_analysis"] == preserved_retry

    def test_analytics_uses_exact_amd_nightly_pattern(self, monkeypatch):
        builds = [
            {
                "number": 9537,
                "message": "AMD Full CI Run - nightly",
                "state": "passed",
                "created_at": "2026-06-15T09:00:00Z",
                "finished_at": "2026-06-15T12:00:00Z",
                "jobs": [],
                "web_url": "https://buildkite.com/vllm/amd-ci/builds/9537",
            },
            {
                "number": 9542,
                "message": "AMD Full CI Run - TheRock nightly (2026-06-15, base 9872921c5)",
                "state": "running",
                "created_at": "2026-06-15T12:00:00Z",
                "finished_at": "",
                "jobs": [],
                "web_url": "https://buildkite.com/vllm/amd-ci/builds/9542",
            },
        ]
        monkeypatch.setattr(ca, "bk_get", lambda path, token, params=None: builds)

        out = ca.collect_pipeline(
            "amd-ci",
            token="fake-token",
            days=1,
            nightly_only=True,
            name_pattern=ca.NIGHTLY_NAME_PATTERNS_BY_SLUG["amd-ci"],
        )

        assert [build["number"] for build in out] == [9537]

    def test_emits_precomputed_windows(self):
        builds = [
            _build(1, 0.5, [_job("Recent", 40)]),
            _build(2, 2.0, [_job("Mid", 50)]),
            _build(3, 6.0, [_job("Week", 60)]),
            _build(4, 10.0, [_job("Old", 70)]),
        ]

        windows = ca.compute_window_blocks(builds, 30, now=NOW)

        assert set(windows) == {"1d", "3d", "7d", "14d", "30d"}
        assert windows["1d"]["build_count"] == 1
        assert windows["3d"]["build_count"] == 2
        assert windows["7d"]["build_count"] == 3
        assert windows["14d"]["build_count"] == 4
        assert windows["30d"]["build_count"] == 4
        assert "jobs" not in windows["30d"]["builds"][0]

    def test_shorter_windows_forget_older_jobs(self):
        builds = [
            _build(1, 10.0, [_job("Legacy MI325 bottleneck", 600, queue="amd_mi325_1")]),
            _build(2, 1.0, [_job("Current MI300 bottleneck", 45, queue="amd_mi300_1")]),
        ]

        windows = ca.compute_window_blocks(builds, 14, now=NOW)
        names_14d = [row["name"] for row in windows["14d"]["duration_ranking"]]
        names_3d = [row["name"] for row in windows["3d"]["duration_ranking"]]

        assert "Legacy MI325 bottleneck" in names_14d
        assert "Legacy MI325 bottleneck" not in names_3d
        assert names_3d == ["Current MI300 bottleneck"]

    def test_window_block_recomputes_summary_and_failures(self):
        builds = [
            _build(1, 8.0, [_job("Flaky", 30, state="failed")], state="failed"),
            _build(2, 0.5, [_job("Flaky", 32, state="passed"), _job("Stable", 20)], state="passed"),
        ]

        windows = ca.compute_window_blocks(builds, 14, now=NOW)

        assert windows["14d"]["summary"]["total_builds"] == 2
        assert windows["14d"]["summary"]["jobs_with_failures"] == 1
        assert windows["7d"]["summary"]["total_builds"] == 1
        assert windows["7d"]["summary"]["jobs_with_failures"] == 0

    def test_top_level_rankings_can_still_cover_full_span(self):
        builds = [
            _build(1, 10.0, [_job("Legacy MI325 bottleneck", 600, queue="amd_mi325_1")]),
            _build(2, 0.5, [_job("Current MI300 bottleneck", 45, queue="amd_mi300_1")]),
        ]

        rankings = ca.compute_job_rankings(builds)
        queues = {row["name"]: row["queues"] for row in rankings}

        assert sorted(queues["Legacy MI325 bottleneck"]) == ["amd_mi325_1"]
        assert sorted(queues["Current MI300 bottleneck"]) == ["amd_mi300_1"]

    def test_gating_nightlies_omit_heavy_job_fields(self, tmp_path):
        builds = [
            _build(1, 0.5, [{**_job("AMD: Samplers Test (mi325_1)", 40), "wait": 12, "extra": "drop"}]),
        ]
        all_data = {
            "ci": {"display_name": "Upstream CI", "builds": builds},
            "amd-ci": {"display_name": "AMD CI", "builds": builds},
        }

        ca.write_gating_nightlies(tmp_path, all_data, "2026-04-20T12:00:00Z")
        payload = json.loads((tmp_path / "gating_nightlies.json").read_text())
        job = payload["ci"]["builds"][0]["jobs"][0]

        assert "name" in job
        assert "state" in job
        assert "dur" not in job
        assert "wait" not in job
        assert "extra" not in job

    def test_gating_nightlies_keep_exact_job_link_fields(self, tmp_path):
        builds = [
            _build(1, 0.5, [{
                **_job("AMD: Samplers Test (mi325_1)", 40),
                "job_id": "019ed951-af8e-4dc8-9590-72a47f9fed96",
                "step_id": "019ed951-ad41-4cc1-8942-051077910be7",
                "url": "https://buildkite.com/vllm/ci/builds/1/steps/canvas?jid=019ed951-af8e-4dc8-9590-72a47f9fed96&tab=output",
            }]),
        ]
        all_data = {
            "ci": {"display_name": "Upstream CI", "builds": builds},
            "amd-ci": {"display_name": "AMD CI", "builds": builds},
        }

        ca.write_gating_nightlies(tmp_path, all_data, "2026-04-20T12:00:00Z")
        payload = json.loads((tmp_path / "gating_nightlies.json").read_text())
        job = payload["ci"]["builds"][0]["jobs"][0]

        assert job["job_id"] == "019ed951-af8e-4dc8-9590-72a47f9fed96"
        assert job["step_id"] == "019ed951-ad41-4cc1-8942-051077910be7"
        assert "url" not in job

    def test_gating_nightlies_parse_exact_ids_from_existing_urls(self, tmp_path):
        builds = [
            _build(1, 0.5, [{
                **_job("AMD: Samplers Test (mi325_1)", 40),
                "url": "https://buildkite.com/vllm/ci/builds/1/steps/canvas?jid=019ed951-af8e-4dc8-9590-72a47f9fed96&tab=output",
            }]),
            _build(2, 0.5, [{
                **_job("mi325_1: Samplers Test", 40),
                "url": "https://buildkite.com/vllm/amd-ci/builds/2/steps/canvas?sid=019ed951-ad41-4cc1-8942-051077910be7&tab=output",
            }]),
        ]
        all_data = {
            "ci": {"display_name": "Upstream CI", "builds": [builds[0]]},
            "amd-ci": {"display_name": "AMD CI", "builds": [builds[1]]},
        }

        ca.write_gating_nightlies(tmp_path, all_data, "2026-04-20T12:00:00Z")
        payload = json.loads((tmp_path / "gating_nightlies.json").read_text())

        assert payload["ci"]["builds"][0]["jobs"][0]["job_id"] == "019ed951-af8e-4dc8-9590-72a47f9fed96"
        assert payload["amd-ci"]["builds"][0]["jobs"][0]["step_id"] == "019ed951-ad41-4cc1-8942-051077910be7"

    def test_gating_nightlies_are_capped_and_compact(self, tmp_path):
        builds = [
            _build(i, i * 0.5, [_job(f"Job {i}", 40)])
            for i in range(ca.GATING_NIGHTLY_LIMIT + 5)
        ]
        all_data = {
            "ci": {"display_name": "Upstream CI", "builds": builds},
            "amd-ci": {"display_name": "AMD CI", "builds": builds},
        }

        ca.write_gating_nightlies(tmp_path, all_data, "2026-04-20T12:00:00Z")
        text = (tmp_path / "gating_nightlies.json").read_text()
        payload = json.loads(text)

        assert text.count("\n") == 1
        assert len(payload["ci"]["builds"]) == ca.GATING_NIGHTLY_LIMIT
        assert len(payload["amd-ci"]["builds"]) == ca.GATING_NIGHTLY_LIMIT
        assert payload["ci"]["builds"][-1]["number"] == ca.GATING_NIGHTLY_LIMIT - 1

    def test_summary_counts_soft_failed_jobs_as_failures(self):
        builds = [
            _build(1, 0.5, [_job("Accepted Failure", 20, state="soft_fail")]),
        ]

        rankings = ca.compute_job_rankings(builds)
        summary = ca.compute_summary(builds, rankings)

        assert summary["jobs_with_failures"] == 1
        assert summary["jobs_with_hard_failures"] == 0
        assert summary["jobs_with_soft_failures"] == 1
        assert summary["build_pass_rate_pct"] == 100.0
        assert summary["build_pass_rate_basis"] == "terminal_build_state_all_green"
        assert summary["pass_rate"] == summary["build_pass_rate_pct"]

    def test_build_pass_rate_excludes_nonterminal_builds_from_denominator(self):
        builds = [
            _build(1, 0.5, [_job("Passed", 10)], state="passed"),
            _build(2, 0.4, [_job("Failed", 10)], state="failed"),
            _build(3, 0.3, [_job("Still running", 10)], state="running"),
            _build(4, 0.2, [_job("Currently failing", 10)], state="failing"),
            _build(5, 0.1, [_job("Canceled", 10)], state="canceled"),
            _build(6, 0.1, [_job("Skipped", 10)], state="skipped"),
            _build(7, 0.1, [_job("Not run", 10)], state="not_run"),
        ]

        summary = ca.compute_summary(builds, ca.compute_job_rankings(builds))

        assert summary["total_builds"] == 7
        assert summary["terminal_builds"] == 5
        assert summary["passed"] == 1
        assert summary["failed"] == 4
        assert summary["build_pass_rate_pct"] == 20.0
        assert summary["build_pass_rate_basis"] == "terminal_build_state_all_green"
        assert summary["pass_rate"] == 20.0


class TestParsedResultFallback:
    def test_fallback_created_at_uses_current_nightly_schedule(self):
        assert ca._iso_from_nightly_date("2026-05-08", "ci") == "2026-05-08T06:00:00Z"
        assert ca._iso_from_nightly_date("2026-05-08", "amd-ci") == "2026-05-08T09:00:00Z"
        assert ca._iso_from_nightly_date("2026-05-08", "other") == "2026-05-08T12:00:00Z"

    def test_loads_amd_builds_from_test_result_jsonl(self, tmp_path):
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        result_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        rows = [
            {
                "name": "__passed__ (7)",
                "status": "passed",
                "duration_secs": 120.0,
                "job_name": "mi300_1: Passing Group",
                "build_number": 123,
                "pipeline": "amd-ci",
                "date": result_date,
            },
            {
                "name": "__failed__ (2)",
                "status": "failed",
                "duration_secs": 4.0,
                "job_name": "mi300_1: Broken Group",
                "build_number": 123,
                "pipeline": "amd-ci",
                "date": result_date,
            },
            {
                "name": "__skipped__ (5)",
                "status": "skipped",
                "duration_secs": 0.1,
                "job_name": "mi300_1: Skipped Group",
                "build_number": 123,
                "pipeline": "amd-ci",
                "date": result_date,
            },
        ]
        (results_dir / f"{result_date}_amd.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n"
        )

        builds = ca.load_test_result_builds(tmp_path, "amd-ci", 14, buildkite_builds=[], previous_builds=[])

        assert len(builds) == 1
        build = builds[0]
        assert build["number"] == 123
        assert build["source"] == "test_results"
        assert build["state"] == "failed"
        assert build["passed"] == 1
        assert build["failed"] == 1
        assert build["skipped"] == 1
        assert {job["name"]: job["state"] for job in build["jobs"]} == {
            "Passing Group": "passed",
            "Broken Group": "failed",
            "Skipped Group": "skipped",
        }
        passing = {job["name"]: job for job in build["jobs"]}["Passing Group"]
        assert passing["test_duration_mins"] == 2.0
        assert "dur" not in passing

    def test_buildkite_summary_labels_wall_queue_and_end_to_end_durations(self):
        raw = [{
            "number": 777,
            "branch": "main",
            "commit": "abc123",
            "message": "post-merge validation",
            "state": "passed",
            "created_at": "2026-04-20T09:00:00Z",
            "finished_at": "2026-04-20T09:30:00Z",
            "jobs": [{
                "id": "job-777",
                "type": "script",
                "name": "mi300_1: Duration Group",
                "state": "passed",
                "runnable_at": "2026-04-20T09:01:00Z",
                "started_at": "2026-04-20T09:06:00Z",
                "finished_at": "2026-04-20T09:26:00Z",
                "step": {"id": "step-777", "key": "duration-group"},
                "agent_query_rules": ["queue=amd_mi300_1"],
            }],
        }]

        build = ca.summarize_pipeline_builds("amd-ci", raw)[0]
        job = build["jobs"][0]

        assert build["branch"] == "main"
        assert build["commit"] == "abc123"
        assert job["dur"] == job["wall_completion_mins"] == 20.0
        assert job["queue_wait_mins"] == 5.0
        assert job["end_to_end_mins"] == 25.0
        assert job["duration_source"] == "buildkite_wall"
        assert "test_duration_mins" not in job

    def test_test_result_builds_emit_buildkite_job_urls(self, tmp_path):
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        result_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        rows = [
            {
                "name": "__passed__ (7)",
                "status": "passed",
                "duration_secs": 120.0,
                "job_name": "AMD: Passing Group (mi325_1)",
                "job_id": "019ed951-af8e-4dc8-9590-72a47f9fed96",
                "step_id": "019ed951-ad41-4cc1-8942-051077910be7",
                "build_number": 72843,
                "pipeline": "ci",
                "date": result_date,
            },
        ]
        (results_dir / f"{result_date}_upstream.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n"
        )

        builds = ca.load_test_result_builds(tmp_path, "ci", 14, buildkite_builds=[], previous_builds=[])

        assert len(builds) == 1
        job = builds[0]["jobs"][0]
        assert job["url"] == (
            "https://buildkite.com/vllm/ci/builds/72843/steps/canvas"
            "?jid=019ed951-af8e-4dc8-9590-72a47f9fed96&tab=output"
        )
        assert job["job_id"] == "019ed951-af8e-4dc8-9590-72a47f9fed96"
        assert job["step_id"] == "019ed951-ad41-4cc1-8942-051077910be7"

    def test_test_result_builds_inherit_exact_job_ids_from_buildkite_metadata(self, tmp_path):
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        result_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        rows = [
            {
                "name": "__passed__ (7)",
                "status": "passed",
                "duration_secs": 120.0,
                "job_name": "AMD: Passing Group (mi325_1)",
                "build_number": 72843,
                "pipeline": "ci",
                "date": result_date,
            },
        ]
        (results_dir / f"{result_date}_upstream.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n"
        )
        buildkite_builds = [
            {
                "number": 72843,
                "state": "passed",
                "finished_at": "2026-08-01T10:00:00Z",
                "jobs": [
                    {
                        "name": "Passing Group",
                        "raw_name": "AMD: Passing Group (mi325_1)",
                        "state": "passed",
                        "q": "gpu_1_queue",
                        "job_id": "019ed951-af8e-4dc8-9590-72a47f9fed96",
                        "step_id": "019ed951-ad41-4cc1-8942-051077910be7",
                    }
                ],
                "web_url": "https://buildkite.com/vllm/ci/builds/72843",
            }
        ]

        builds = ca.load_test_result_builds(tmp_path, "ci", 14, buildkite_builds=buildkite_builds, previous_builds=[])

        job = builds[0]["jobs"][0]
        assert builds[0]["state"] == "passed"
        assert builds[0]["finished_at"] == "2026-08-01T10:00:00Z"
        assert job["job_id"] == "019ed951-af8e-4dc8-9590-72a47f9fed96"
        assert job["step_id"] == "019ed951-ad41-4cc1-8942-051077910be7"
        assert job["url"] == (
            "https://buildkite.com/vllm/ci/builds/72843/steps/canvas"
            "?jid=019ed951-af8e-4dc8-9590-72a47f9fed96&tab=output"
        )

    def test_exact_job_id_keeps_manual_retry_metadata_off_original_attempt(self, tmp_path):
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        result_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        raw_name = "mi300_1: Retryable Group"
        (results_dir / f"{result_date}_amd.jsonl").write_text(json.dumps({
            "name": "__passed__ (4)",
            "status": "passed",
            "duration_secs": 30.0,
            "job_name": raw_name,
            "job_id": "original-job",
            "step_id": "retryable-step",
            "build_number": 11600,
            "pipeline": "amd-ci",
            "date": result_date,
        }) + "\n")
        buildkite_builds = [{
            "number": 11600,
            "jobs": [
                {
                    "name": "Retryable Group",
                    "raw_name": raw_name,
                    "state": "passed",
                    "job_id": "original-job",
                    "step_id": "retryable-step",
                    "dur": 12.0,
                    "started_at": "2026-08-01T09:10:00Z",
                    "finished_at": "2026-08-01T09:22:00Z",
                    "retried_in_job_id": "manual-retry-job",
                },
                {
                    "name": "Retryable Group",
                    "raw_name": raw_name,
                    "state": "passed",
                    "job_id": "manual-retry-job",
                    "step_id": "retryable-step",
                    "dur": 5.0,
                    "started_at": "2026-08-02T15:00:00Z",
                    "finished_at": "2026-08-02T15:05:00Z",
                    "retry_source": "manual",
                },
            ],
        }]

        builds = ca.load_test_result_builds(
            tmp_path,
            "amd-ci",
            14,
            buildkite_builds=buildkite_builds,
            previous_builds=[],
        )

        job = builds[0]["jobs"][0]
        assert job["job_id"] == "original-job"
        assert job["finished_at"] == "2026-08-01T09:22:00Z"
        assert job["wall_completion_mins"] == 12.0
        assert job["retried_in_job_id"] == "manual-retry-job"
        assert "retry_source" not in job

    def test_explicit_unknown_job_id_does_not_fall_back_to_same_name_retry(self, tmp_path):
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        result_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        raw_name = "mi300_1: Retryable Group"
        (results_dir / f"{result_date}_amd.jsonl").write_text(json.dumps({
            "name": "__passed__ (4)",
            "status": "passed",
            "duration_secs": 30.0,
            "job_name": raw_name,
            "job_id": "original-job-not-in-metadata",
            "step_id": "retryable-step",
            "build_number": 11600,
            "pipeline": "amd-ci",
            "date": result_date,
        }) + "\n")
        buildkite_builds = [{
            "number": 11600,
            "jobs": [{
                "name": "Retryable Group",
                "raw_name": raw_name,
                "state": "passed",
                "job_id": "same-name-manual-retry",
                "step_id": "retryable-step",
                "dur": 5.0,
                "started_at": "2026-08-02T15:00:00Z",
                "finished_at": "2026-08-02T15:05:00Z",
                "retry_source": "manual",
            }],
        }]

        builds = ca.load_test_result_builds(
            tmp_path,
            "amd-ci",
            14,
            buildkite_builds=buildkite_builds,
            previous_builds=[],
        )

        job = builds[0]["jobs"][0]
        assert job["job_id"] == "original-job-not-in-metadata"
        assert "finished_at" not in job
        assert "wall_completion_mins" not in job
        assert "retry_source" not in job

    def test_keeps_hardware_specific_result_jobs_separate(self, tmp_path):
        """Same title on MI300 and MI355 must not collapse into one job.

        The AMD matrix joins analytics rows by normalized title *and* queue.
        If parsed JSONL rows are grouped only by normalized title, a failure on
        MI300 can be rendered as an MI355 failure.
        """
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        result_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        rows = [
            {
                "name": "__failed__ (5)",
                "status": "failed",
                "duration_secs": 0.0,
                "job_name": "mi300_1: Entrypoints Integration (Pooling)",
                "build_number": 8193,
                "pipeline": "amd-ci",
                "date": result_date,
            },
            {
                "name": "__passed__ (306)",
                "status": "passed",
                "duration_secs": 1848.45,
                "job_name": "mi355_1: Entrypoints Integration (Pooling)",
                "build_number": 8193,
                "pipeline": "amd-ci",
                "date": result_date,
            },
        ]
        (results_dir / f"{result_date}_amd.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n"
        )

        builds = ca.load_test_result_builds(tmp_path, "amd-ci", 14, buildkite_builds=[], previous_builds=[])

        assert len(builds) == 1
        build = builds[0]
        assert build["passed"] == 1
        assert build["failed"] == 1
        jobs = sorted(build["jobs"], key=lambda row: row["q"])
        assert [(job["name"], job["q"], job["state"]) for job in jobs] == [
            ("Entrypoints Integration (Pooling)", "amd_mi300_1", "failed"),
            ("Entrypoints Integration (Pooling)", "amd_mi355_1", "passed"),
        ]

    def test_test_result_builds_preserve_buildkite_soft_fail_state(self, tmp_path):
        """Parsed JSONL failures should not turn Buildkite soft-fails hard-red.

        The current upstream nightly can have vendor hardware jobs that exit
        non-zero but are configured as ``soft_failed`` in Buildkite. The JSONL
        rows still contain failed pytest counts, so analytics must carry over
        the Buildkite job state when it is available.
        """
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        result_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        rows = [
            {
                "name": "__unidentified_failures__ (6)",
                "status": "failed",
                "duration_secs": 0.0,
                "job_name": "Intel GPU Test",
                "build_number": 65324,
                "pipeline": "ci",
                "date": result_date,
            },
        ]
        (results_dir / f"{result_date}_upstream.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n"
        )

        buildkite_builds = [
            _build(
                65324,
                0.5,
                [
                    {
                        "name": "Intel GPU Test",
                        "raw_name": "Intel GPU Test",
                        "state": "soft_fail",
                        "dur": 4.6,
                        "wait": 0.0,
                        "q": "intel-gpu",
                    }
                ],
                state="running",
            )
        ]

        builds = ca.load_test_result_builds(tmp_path, "ci", 14, buildkite_builds=buildkite_builds)

        assert len(builds) == 1
        build = builds[0]
        assert build["failed"] == 0
        assert build["soft_failed"] == 1
        assert build["jobs"][0]["state"] == "soft_fail"
        assert build["jobs"][0]["q"] == "intel-gpu"

    def test_choose_analytics_builds_preserves_previous_on_empty_collection(self):
        previous = [_build(42, 1.0, [_job("Known Good", 10)])]

        chosen = ca.choose_analytics_builds([], [], previous, "amd-ci")

        assert chosen == previous
