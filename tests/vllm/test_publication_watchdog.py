"""Tests for proactive, deduplicated canonical publication recovery."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from vllm import plan_publication_watchdog as watchdog


NOW = datetime(2026, 8, 30, 5, tzinfo=timezone.utc)


def _status(*, age_minutes=30, **overrides):
    payload = {
        "schema_version": 1,
        "mode": "current",
        "status": "healthy",
        "generated_at": (NOW - timedelta(minutes=age_minutes)).isoformat(),
        "degraded_since": None,
        "publication_blocked": False,
        "uses_fallback": False,
        "affected_surfaces": [],
        "affected_surface_count": 0,
        "fallback_surface_count": 0,
        "fresh_degraded_surface_count": 0,
    }
    payload.update(overrides)
    return payload


def _run(
    *,
    age_minutes,
    status="completed",
    started_age_minutes=None,
    updated_age_minutes=None,
    run_id=1,
):
    row = {
        "id": run_id,
        "status": status,
        "created_at": (NOW - timedelta(minutes=age_minutes)).isoformat(),
        "run_started_at": None,
        "updated_at": (
            NOW
            - timedelta(
                minutes=age_minutes if updated_age_minutes is None else updated_age_minutes
            )
        ).isoformat(),
    }
    if started_age_minutes is not None:
        row["run_started_at"] = (
            NOW - timedelta(minutes=started_age_minutes)
        ).isoformat()
    return row


def _decision(status, runs=()):
    observation = watchdog.observe_publication(status, now=NOW)
    parsed_runs = watchdog.parse_workflow_runs({"workflow_runs": list(runs)}, now=NOW)
    return watchdog.watchdog_decision(observation, parsed_runs, now=NOW)


def test_current_publication_does_not_dispatch() -> None:
    decision = _decision(_status(age_minutes=44.99))

    assert decision.required is False
    assert decision.reason == "canonical-current"


def test_45_minute_boundary_dispatches_with_canonical_generation() -> None:
    decision = _decision(_status(age_minutes=45))

    assert decision.required is True
    assert decision.reason == "publication-stale"
    assert decision.observed_generation == "2026-08-30T04:15:00Z"


@pytest.mark.parametrize("run_status", sorted(watchdog.QUEUED_RUN_STATUSES))
def test_every_queued_state_suppresses_until_active_age_limit(run_status) -> None:
    decision = _decision(
        _status(age_minutes=90),
        [_run(age_minutes=75, status=run_status)],
    )

    assert decision.required is False
    assert decision.reason == "collection-active"


@pytest.mark.parametrize("run_status", sorted(watchdog.QUEUED_RUN_STATUSES))
def test_orphaned_queued_state_does_not_block_recovery_forever(run_status) -> None:
    decision = _decision(
        _status(age_minutes=90),
        [_run(age_minutes=76, status=run_status, updated_age_minutes=5)],
    )

    assert decision.required is True
    assert decision.reason == "publication-stale"


def test_in_progress_age_uses_start_time_not_queue_time() -> None:
    decision = _decision(
        _status(age_minutes=90),
        [_run(age_minutes=100, status="in_progress", started_age_minutes=5)],
    )

    assert decision.reason == "collection-active"


def test_timed_out_api_zombie_does_not_block_recovery_forever() -> None:
    decision = _decision(
        _status(age_minutes=120),
        [
            _run(
                age_minutes=100,
                status="in_progress",
                started_age_minutes=80,
                updated_age_minutes=5,
            )
        ],
    )

    assert decision.required is True
    assert decision.reason == "publication-stale"


def test_recent_completed_attempt_enforces_retry_cooldown() -> None:
    decision = _decision(
        _status(age_minutes=90),
        [_run(age_minutes=29, status="completed")],
    )

    assert decision.required is False
    assert decision.reason == "recent-collection-attempt"


def test_old_completed_attempt_allows_recovery() -> None:
    decision = _decision(
        _status(age_minutes=90),
        [_run(age_minutes=31, status="completed")],
    )

    assert decision.required is True


def test_completed_attempt_cooldown_uses_update_time() -> None:
    decision = _decision(
        _status(age_minutes=90),
        [
            _run(
                age_minutes=100,
                status="completed",
                updated_age_minutes=5,
            )
        ],
    )

    assert decision.required is False
    assert decision.reason == "recent-collection-attempt"


def test_recent_blocked_publication_waits_for_proactive_age_boundary() -> None:
    blocked = _status(
        age_minutes=30,
        mode="blocked",
        status="blocked",
        publication_blocked=True,
    )
    assert _decision(blocked).reason == "canonical-current"

    blocked["generated_at"] = (NOW - timedelta(minutes=45)).isoformat()
    assert _decision(blocked).reason == "publication-blocked"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"schema_version": 1},
        _status(generated_at="not-a-time"),
        _status(generated_at=(NOW + timedelta(minutes=6)).isoformat()),
    ],
)
def test_invalid_publication_status_fails_open_to_recovery(payload) -> None:
    decision = _decision(payload)

    assert decision.required is True
    assert decision.reason == "status-invalid"
    assert decision.observed_generation == watchdog.UNAVAILABLE_GENERATION


@pytest.mark.parametrize(
    "row",
    [
        _run(age_minutes=10, status="unknown"),
        {"id": True, "status": "completed", "created_at": NOW.isoformat()},
        {"id": 1, "status": "completed", "created_at": "not-a-time"},
        {
            "id": 1,
            "status": "in_progress",
            "created_at": NOW.isoformat(),
            "run_started_at": "not-a-time",
        },
    ],
)
def test_untrusted_or_unknown_run_state_fails_closed(row) -> None:
    with pytest.raises(ValueError, match="invalid row"):
        watchdog.parse_workflow_runs({"workflow_runs": [row]}, now=NOW)


def test_preflight_skips_when_another_publication_advanced_generation() -> None:
    observation = watchdog.observe_publication(_status(age_minutes=30), now=NOW)
    decision = watchdog.generation_preflight_decision(
        observation,
        expected_generation=(NOW - timedelta(hours=2)).isoformat(),
        now=NOW,
    )

    assert decision.required is False
    assert decision.reason == "generation-advanced"


def test_preflight_keeps_recovery_when_advanced_generation_is_already_stale() -> None:
    observation = watchdog.observe_publication(_status(age_minutes=60), now=NOW)
    decision = watchdog.generation_preflight_decision(
        observation,
        expected_generation=(NOW - timedelta(hours=2)).isoformat(),
        now=NOW,
    )

    assert decision.required is True
    assert decision.reason == "publication-stale"


def test_preflight_keeps_same_generation_recovery() -> None:
    observation = watchdog.observe_publication(_status(age_minutes=60), now=NOW)
    decision = watchdog.generation_preflight_decision(
        observation,
        expected_generation=(NOW - timedelta(minutes=60)).isoformat(),
        now=NOW,
    )

    assert decision.required is True
    assert decision.reason == "publication-stale"


def test_preflight_treats_fresh_valid_status_as_recovery_from_unavailable() -> None:
    observation = watchdog.observe_publication(_status(age_minutes=30), now=NOW)
    decision = watchdog.generation_preflight_decision(
        observation,
        expected_generation=watchdog.UNAVAILABLE_GENERATION,
        now=NOW,
    )

    assert decision.required is False
    assert decision.reason == "publication-recovered"


def test_preflight_does_not_cancel_unavailable_recovery_with_stale_status() -> None:
    observation = watchdog.observe_publication(_status(age_minutes=60), now=NOW)
    decision = watchdog.generation_preflight_decision(
        observation,
        expected_generation=watchdog.UNAVAILABLE_GENERATION,
        now=NOW,
    )

    assert decision.required is True
    assert decision.reason == "publication-stale"


def test_cadence_preflight_coalesces_recent_publication() -> None:
    observation = watchdog.observe_publication(
        _status(age_minutes=29),
        now=NOW,
        max_publication_age_minutes=30,
    )
    decision = watchdog.cadence_preflight_decision(observation)

    assert decision.required is False
    assert decision.reason == "canonical-recent"


def test_cadence_preflight_keeps_normally_due_schedule() -> None:
    observation = watchdog.observe_publication(
        _status(age_minutes=30),
        now=NOW,
        max_publication_age_minutes=30,
    )
    decision = watchdog.cadence_preflight_decision(observation)

    assert decision.required is True
    assert decision.reason == "publication-stale"


def test_missing_status_file_requests_recovery(tmp_path) -> None:
    observation = watchdog.load_publication_observation(
        tmp_path / "missing.json",
        now=NOW,
    )
    decision = watchdog.watchdog_decision(observation, (), now=NOW)

    assert decision.required is True
    assert decision.reason == "status-unavailable"


def test_cli_writes_safe_generation_aware_outputs(tmp_path) -> None:
    status_path = tmp_path / "status.json"
    runs_path = tmp_path / "runs.json"
    output_path = tmp_path / "github-output"
    status_path.write_text(json.dumps(_status(age_minutes=60)))
    runs_path.write_text(json.dumps({"workflow_runs": []}))

    result = watchdog.main(
        [
            "--status",
            str(status_path),
            "--workflow-runs",
            str(runs_path),
            "--now",
            NOW.isoformat(),
            "--github-output",
            str(output_path),
        ]
    )

    assert result == 0
    assert output_path.read_text().splitlines() == [
        "required=true",
        "reason=publication-stale",
        "observed_generation=2026-08-30T04:00:00Z",
    ]


def test_recovery_budget_retains_margin_before_three_hour_ui_slo() -> None:
    # Detection plus the collector timeout leaves 75m for an independent tick
    # to arrive. GitHub cron itself has no bounded delivery guarantee.
    assert watchdog.DEFAULT_MAX_PUBLICATION_AGE_MINUTES + 60 < 180
