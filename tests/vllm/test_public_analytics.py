"""Tests for the fail-closed public analytics publication boundary."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from vllm.ci.public_analytics import (
    PUBLIC_ANALYTICS_PROJECTOR_ID,
    compact_public_analytics_json,
    project_public_analytics,
)


ROOT = Path(__file__).resolve().parents[2]
ANALYTICS = ROOT / "data/vllm/ci/analytics.json"


def _summary(*, explicit: bool = True) -> dict:
    summary = {
        "total_builds": 8,
        "passed": 6,
        "failed": 2,
        "pass_rate": 75.0,
        "total_jobs_tracked": 40,
        "jobs_with_failures": 4,
        "jobs_with_hard_failures": 3,
        "jobs_with_soft_failures": 1,
        "private_summary": "LEAK_ME_SUMMARY",
    }
    if explicit:
        summary.update(
            {
                "terminal_builds": 8,
                "build_pass_rate_pct": 75.0,
                "build_pass_rate_basis": "terminal_build_state_all_green",
            }
        )
    return summary


def _build(*, include_jobs: bool = True) -> dict:
    build = {
        "number": 123,
        "state": "failed",
        "created_at": "2026-08-16T09:00:00Z",
        "date": "2026-08-16",
        "passed": 17,
        "failed": 1,
        "soft_failed": 1,
        "skipped": 2,
        "total_jobs": 21,
        "web_url": "https://private.example/build/123",
        "commit": "LEAK_ME_COMMIT",
        "author": "LEAK_ME_AUTHOR",
        "private_build": {"sentinel": "LEAK_ME_BUILD"},
    }
    if include_jobs:
        build["jobs"] = [
            {
                "name": "models test",
                "state": "soft_fail",
                "q": "amd_mi300_1",
                "wait": 3.5,
                "url": "https://private.example/job/456",
                "job_id": "LEAK_ME_JOB_ID",
                "step_id": "LEAK_ME_STEP_ID",
                "started_at": "LEAK_ME_STARTED_AT",
                "retry_type": "LEAK_ME_RETRY",
                "private_job": {"sentinel": "LEAK_ME_JOB"},
            }
        ]
    return build


def _failure_row() -> dict:
    return {
        "name": "models test",
        "failed": 2,
        "soft_failed": 1,
        "fail_rate": 37.5,
        "runs": 8,
        "queues": ["LEAK_ME_FAILURE_QUEUE"],
        "private_failure": "LEAK_ME_FAILURE",
    }


def _duration_row() -> dict:
    return {
        "name": "models test",
        "median_dur": 42.5,
        "queues": ["amd_mi300_1"],
        "median_wait": 3.5,
        "p90_wait": 12.0,
        "max_wait": 30.0,
        "private_duration": "LEAK_ME_DURATION",
    }


def _full_payload() -> dict:
    window_build = _build(include_jobs=False)
    # Window rows never need job details. If a producer accidentally adds them,
    # the projector must still leave them behind.
    window_build["jobs"] = [{"name": "LEAK_ME_WINDOW_JOB", "state": "failed"}]
    window_duration = _duration_row()
    return {
        "amd-ci": {
            "pipeline": "amd-ci",
            "display_name": "AMD CI",
            "days": 90,
            "generated_at": "2026-08-17T00:00:00Z",
            "pass_rate_contract_version": 1,
            "transition_policy_id": "confirmed-incidents-v1",
            "default_window": "7d",
            "summary": _summary(),
            "builds": [_build()],
            "failure_ranking": [_failure_row()],
            "duration_ranking": [_duration_row()],
            "windows": {
                "1d": {
                    "summary": _summary(),
                    "builds": [window_build],
                    "failure_ranking": [_failure_row()],
                    "duration_ranking": [window_duration],
                    "daily_stats": [{"sentinel": "LEAK_ME_WINDOW_DAILY"}],
                    "nightly_builds": [{"sentinel": "LEAK_ME_WINDOW_NIGHTLY"}],
                    "queue_stats": [{"sentinel": "LEAK_ME_WINDOW_QUEUE"}],
                    "private_window": "LEAK_ME_WINDOW",
                },
                "7d": {
                    "summary": _summary(),
                    "builds": [],
                    "failure_ranking": [],
                    "duration_ranking": [],
                },
            },
            "cohort": {"sentinel": "LEAK_ME_COHORT"},
            "transition_basis": "LEAK_ME_TRANSITION_BASIS",
            "nightly_change_history": [{"sentinel": "LEAK_ME_CHANGE"}],
            "daily_stats": [{"sentinel": "LEAK_ME_DAILY"}],
            "nightly_builds": [{"sentinel": "LEAK_ME_NIGHTLY"}],
            "queue_stats": [{"sentinel": "LEAK_ME_QUEUE"}],
            "all_main_reliability": {"sentinel": "LEAK_ME_RELIABILITY"},
            "private_pipeline": {"sentinel": "LEAK_ME_PIPELINE"},
        },
        # This deliberately omits windows and every new explicit pass-rate
        # field. It exercises the renderer's top-level legacy fallbacks.
        "ci": {
            "pipeline": "ci",
            "display_name": "Upstream CI",
            "days": 14,
            "summary": _summary(explicit=False),
            "builds": [
                {
                    "number": "321",
                    "state": "passed",
                    "created_at": "2026-08-15T09:00:00Z",
                    "passed": 20,
                    "failed": 0,
                    "soft_failed": 0,
                    "skipped": 0,
                    "total_jobs": 20,
                    "jobs": [{"name": "engine test", "state": "passed"}],
                }
            ],
            "failure_ranking": [
                {"name": "engine test", "failed": 1, "soft_failed": 0, "fail_rate": 5}
            ],
            "duration_ranking": [
                {
                    "name": "engine test",
                    "median_dur": 10,
                    "queues": ["gpu_1_queue"],
                    "median_wait": 2,
                }
            ],
            "all_main_reliability": {"sentinel": "LEAK_ME_UPSTREAM_RELIABILITY"},
            "main_builds": [{"sentinel": "LEAK_ME_MAIN_BUILDS"}],
            "main_builds_provenance": {"sentinel": "LEAK_ME_PROVENANCE"},
            "main_retry_analysis": {"sentinel": "LEAK_ME_RETRIES"},
        },
    }


def test_projection_preserves_exact_browser_contract_and_legacy_fallbacks() -> None:
    projected = project_public_analytics(_full_payload())

    assert PUBLIC_ANALYTICS_PROJECTOR_ID == "public_analytics_v1"
    assert set(projected) == {"amd-ci", "ci"}
    amd = projected["amd-ci"]
    assert set(amd) == {
        "pipeline",
        "display_name",
        "days",
        "generated_at",
        "pass_rate_contract_version",
        "transition_policy_id",
        "default_window",
        "summary",
        "builds",
        "failure_ranking",
        "duration_ranking",
        "windows",
    }
    assert amd["summary"] == {
        "total_builds": 8,
        "terminal_builds": 8,
        "passed": 6,
        "failed": 2,
        "total_jobs_tracked": 40,
        "jobs_with_failures": 4,
        "jobs_with_hard_failures": 3,
        "jobs_with_soft_failures": 1,
        "build_pass_rate_pct": 75.0,
        "pass_rate": 75.0,
        "build_pass_rate_basis": "terminal_build_state_all_green",
    }
    assert amd["builds"] == [
        {
            "number": 123,
            "state": "failed",
            "created_at": "2026-08-16T09:00:00Z",
            "date": "2026-08-16",
            "passed": 17,
            "failed": 1,
            "soft_failed": 1,
            "skipped": 2,
            "total_jobs": 21,
            "jobs": [
                {
                    "name": "models test",
                    "state": "soft_fail",
                    "q": "amd_mi300_1",
                    "wait": 3.5,
                }
            ],
        }
    ]
    assert amd["failure_ranking"] == [
        {"name": "models test", "failed": 2, "soft_failed": 1, "fail_rate": 37.5}
    ]
    assert amd["duration_ranking"] == [
        {
            "name": "models test",
            "median_dur": 42.5,
            "queues": ["amd_mi300_1"],
            "median_wait": 3.5,
        }
    ]
    assert list(amd["windows"]) == ["1d", "7d"]
    assert amd["windows"]["1d"] == {
        "summary": amd["summary"],
        "builds": [
            {
                "number": 123,
                "state": "failed",
                "created_at": "2026-08-16T09:00:00Z",
                "date": "2026-08-16",
                "passed": 17,
                "failed": 1,
                "soft_failed": 1,
                "skipped": 2,
                "total_jobs": 21,
            }
        ],
        "failure_ranking": [
            {"name": "models test", "failed": 2, "soft_failed": 1, "fail_rate": 37.5}
        ],
        "duration_ranking": [{"name": "models test", "median_dur": 42.5}],
    }

    legacy = projected["ci"]
    assert "windows" not in legacy
    assert "default_window" not in legacy
    assert "terminal_builds" not in legacy["summary"]
    assert "build_pass_rate_pct" not in legacy["summary"]
    assert "build_pass_rate_basis" not in legacy["summary"]
    assert legacy["summary"]["pass_rate"] == 75.0
    assert legacy["builds"][0]["created_at"] == "2026-08-15T09:00:00Z"
    assert "date" not in legacy["builds"][0]
    assert legacy["builds"][0]["jobs"] == [{"name": "engine test", "state": "passed"}]
    assert legacy["duration_ranking"][0] == {
        "name": "engine test",
        "median_dur": 10,
        "queues": ["gpu_1_queue"],
        "median_wait": 2,
    }


def test_projection_strips_private_and_unknown_fields_at_every_nested_level() -> None:
    projected = project_public_analytics(_full_payload())
    encoded = json.dumps(projected, sort_keys=True)

    assert "LEAK_ME" not in encoded
    for private_key in (
        "all_main_reliability",
        "main_builds",
        "main_builds_provenance",
        "main_retry_analysis",
        "nightly_change_history",
        "cohort",
        "web_url",
        "job_id",
        "step_id",
        "retry_type",
        "started_at",
        "p90_wait",
    ):
        assert f'"{private_key}"' not in encoded


def test_projection_preserves_absent_fields_and_explicit_empty_structures() -> None:
    payload = {
        "amd-ci": {},
        "ci": {
            "summary": {},
            "builds": [],
            "failure_ranking": [],
            "duration_ranking": [],
            "windows": {},
        },
    }

    assert project_public_analytics(payload) == payload


def test_projection_is_immutable_and_exactly_idempotent() -> None:
    source = _full_payload()
    before = copy.deepcopy(source)

    first = project_public_analytics(source)
    second = project_public_analytics(first)

    assert source == before
    assert second == first
    assert compact_public_analytics_json(first) == compact_public_analytics_json(second)
    assert compact_public_analytics_json(source).endswith("\n")
    assert "\n" not in compact_public_analytics_json(source)[:-1]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "analytics must be a JSON object"),
        ({}, "must contain exactly"),
        ({"amd-ci": {}, "ci": {}, "secret": {}}, "unexpected"),
        ({"amd-ci": {}, "ci": []}, "analytics\\['ci'\\] must be a JSON object"),
        (
            {"amd-ci": {"pipeline": "ci"}, "ci": {}},
            "pipeline must match its top-level key",
        ),
        ({"amd-ci": {"summary": []}, "ci": {}}, "summary must be a JSON object"),
        ({"amd-ci": {"builds": {}}, "ci": {}}, "builds must be a JSON array"),
        ({"amd-ci": {"builds": [[]]}, "ci": {}}, "builds\\[0\\] must be a JSON object"),
        (
            {"amd-ci": {"builds": [{"jobs": {}}]}, "ci": {}},
            "jobs must be a JSON array",
        ),
        (
            {"amd-ci": {"builds": [{"jobs": [{}]}]}, "ci": {}},
            "jobs\\[0\\]\\.name must be a string",
        ),
        ({"amd-ci": {"windows": []}, "ci": {}}, "windows must be a JSON object"),
        (
            {"amd-ci": {"windows": {"7d": []}}, "ci": {}},
            "windows\\['7d'\\] must be a JSON object",
        ),
        (
            {"amd-ci": {"failure_ranking": [{}]}, "ci": {}},
            "failure_ranking\\[0\\]\\.name must be a string",
        ),
        (
            {"amd-ci": {"duration_ranking": [{"name": "job", "queues": {}}]}, "ci": {}},
            "queues must be a JSON array",
        ),
        (
            {"amd-ci": {"duration_ranking": [{"name": "job", "queues": [{}]}]}, "ci": {}},
            "queues entries must be strings",
        ),
        (
            {"amd-ci": {"summary": {"pass_rate": float("nan")}}, "ci": {}},
            "pass_rate must be a finite number",
        ),
        (
            {"amd-ci": {"display_name": {"sentinel": "private"}}, "ci": {}},
            "display_name must be a string or null",
        ),
    ],
)
def test_projection_rejects_malformed_or_out_of_domain_payloads(
    payload: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        project_public_analytics(payload)


def test_tracked_public_projection_is_bounded_and_at_least_ninety_percent_smaller() -> None:
    raw_size = ANALYTICS.stat().st_size
    payload = json.loads(ANALYTICS.read_text())

    projected_bytes = compact_public_analytics_json(payload).encode("utf-8")

    assert len(projected_bytes) < 8 * 1024 * 1024
    assert len(projected_bytes) <= raw_size * 0.10
