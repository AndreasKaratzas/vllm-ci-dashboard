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
    conclusion=None,
    recovery_key=None,
    display_title=None,
):
    if status == "completed" and conclusion is None:
        conclusion = "failure"
    if display_title is None:
        display_title = "Data Collection"
        if recovery_key:
            display_title += f" [recovery:{recovery_key}]"
    row = {
        "id": run_id,
        "status": status,
        "created_at": (NOW - timedelta(minutes=age_minutes)).isoformat(),
        "run_started_at": None,
        "updated_at": (
            NOW
            - timedelta(minutes=age_minutes if updated_age_minutes is None else updated_age_minutes)
        ).isoformat(),
        "conclusion": conclusion,
        "display_title": display_title,
    }
    if started_age_minutes is not None:
        row["run_started_at"] = (NOW - timedelta(minutes=started_age_minutes)).isoformat()
    return row


def _decision(status, runs=(), *, target="collector"):
    observation = watchdog.observe_publication(status, now=NOW)
    parsed_runs = watchdog.parse_workflow_runs({"workflow_runs": list(runs)}, now=NOW)
    return watchdog.watchdog_decision(observation, parsed_runs, now=NOW, recovery_target=target)


def _key(status, *, target="collector"):
    observation = watchdog.observe_publication(status, now=NOW)
    assert observation.recovery_reason is not None
    return watchdog.recovery_key(
        target=target,
        reason=observation.recovery_reason,
        observed_generation=observation.generation,
    )


def test_current_publication_does_not_dispatch() -> None:
    decision = _decision(_status(age_minutes=94.99))

    assert decision.required is False
    assert decision.reason == "canonical-current"


def test_strict_dns_only_degradation_is_targetable() -> None:
    payload = _status(
        mode="degraded",
        status="degraded",
        degraded_since=(NOW - timedelta(hours=1)).isoformat(),
        uses_fallback=False,
        affected_surfaces=["DNS health"],
        affected_surface_count=1,
        fallback_surface_count=0,
        fresh_degraded_surface_count=1,
    )

    assert watchdog.is_dns_only_degraded(payload, now=NOW) is True

    payload.update(
        mode="fallback",
        uses_fallback=True,
        fallback_surface_count=1,
        fresh_degraded_surface_count=0,
    )
    assert watchdog.is_dns_only_degraded(payload, now=NOW) is True

    payload["affected_surfaces"] = ["CI health", "DNS health"]
    payload["affected_surface_count"] = 2
    payload["fallback_surface_count"] = 2
    assert watchdog.is_dns_only_degraded(payload, now=NOW) is False


def test_fresh_state_pages_mismatch_routes_to_deploy_only() -> None:
    assert (
        watchdog.state_pages_mismatch_target(
            (NOW - timedelta(minutes=94, seconds=59)).isoformat(), now=NOW
        )
        == "deploy-pages"
    )


def test_stale_state_pages_mismatch_repairs_availability_before_collection() -> None:
    assert (
        watchdog.state_pages_mismatch_target((NOW - timedelta(minutes=95)).isoformat(), now=NOW)
        == "deploy-pages"
    )


@pytest.mark.parametrize(
    "generated_at",
    [None, "not-a-time", (NOW + timedelta(minutes=6)).isoformat()],
)
def test_state_pages_mismatch_rejects_invalid_generation(generated_at) -> None:
    with pytest.raises(ValueError, match="state generated_at is invalid"):
        watchdog.state_pages_mismatch_target(generated_at, now=NOW)


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "blocked", "mode": "blocked", "publication_blocked": True},
        {"affected_surface_count": 2},
        {"affected_surfaces": ["dns_health"]},
        {"status": "healthy"},
    ],
)
def test_malformed_or_non_degraded_dns_status_is_not_targetable(overrides) -> None:
    payload = _status(
        mode="degraded",
        status="degraded",
        degraded_since=(NOW - timedelta(hours=1)).isoformat(),
        uses_fallback=False,
        affected_surfaces=["DNS health"],
        affected_surface_count=1,
        fallback_surface_count=0,
        fresh_degraded_surface_count=1,
    )
    payload.update(overrides)

    assert watchdog.is_dns_only_degraded(payload, now=NOW) is False


def test_95_minute_boundary_dispatches_with_canonical_generation() -> None:
    decision = _decision(_status(age_minutes=95))

    assert decision.required is True
    assert decision.reason == "publication-stale"
    assert decision.observed_generation == "2026-08-30T03:25:00Z"


@pytest.mark.parametrize("run_status", sorted(watchdog.QUEUED_RUN_STATUSES))
def test_every_queued_state_suppresses_until_active_age_limit(run_status) -> None:
    decision = _decision(
        _status(age_minutes=160),
        [_run(age_minutes=75, status=run_status)],
    )

    assert decision.required is False
    assert decision.reason == "collection-active"


@pytest.mark.parametrize("run_status", sorted(watchdog.QUEUED_RUN_STATUSES))
def test_orphaned_queued_state_does_not_block_recovery_forever(run_status) -> None:
    decision = _decision(
        _status(age_minutes=160),
        [_run(age_minutes=76, status=run_status, updated_age_minutes=5)],
    )

    assert decision.required is True
    assert decision.reason == "publication-stale"


def test_in_progress_age_uses_start_time_not_queue_time() -> None:
    decision = _decision(
        _status(age_minutes=160),
        [_run(age_minutes=100, status="in_progress", started_age_minutes=5)],
    )

    assert decision.reason == "collection-active"


def test_timed_out_api_zombie_does_not_block_recovery_forever() -> None:
    decision = _decision(
        _status(age_minutes=180),
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
    status = _status(age_minutes=160)
    decision = _decision(
        status,
        [
            _run(
                age_minutes=14,
                status="completed",
                recovery_key=_key(status),
            )
        ],
    )

    assert decision.required is False
    assert decision.reason == "recent-recovery-attempt"


def test_old_completed_attempt_allows_recovery() -> None:
    decision = _decision(
        _status(age_minutes=160),
        [_run(age_minutes=16, status="completed")],
    )

    assert decision.required is True


def test_completed_attempt_cooldown_uses_update_time() -> None:
    status = _status(age_minutes=160)
    decision = _decision(
        status,
        [
            _run(
                age_minutes=100,
                status="completed",
                updated_age_minutes=5,
                recovery_key=_key(status),
            )
        ],
    )

    assert decision.required is False
    assert decision.reason == "recent-recovery-attempt"


def test_recent_unrelated_failed_attempt_does_not_suppress_new_incident() -> None:
    status = _status(age_minutes=160)
    decision = _decision(
        status,
        [_run(age_minutes=5, recovery_key="1" * 64)],
    )

    assert decision.required is True
    assert decision.recovery_key == _key(status)


def test_successful_exact_key_run_does_not_hide_persistent_incident() -> None:
    status = _status(age_minutes=160)
    decision = _decision(
        status,
        [_run(age_minutes=5, recovery_key=_key(status), conclusion="success")],
    )

    assert decision.required is True


def test_recovery_key_binds_target_reason_and_generation() -> None:
    status = _status(age_minutes=160)
    key = _key(status)

    assert len(key) == 64
    assert key != _key(status, target="deploy-pages")
    newer = _status(age_minutes=159)
    assert key != _key(newer)


def test_recent_blocked_publication_waits_for_proactive_age_boundary() -> None:
    blocked = _status(
        age_minutes=30,
        mode="blocked",
        status="blocked",
        publication_blocked=True,
    )
    assert _decision(blocked).reason == "canonical-current"

    blocked["generated_at"] = (NOW - timedelta(minutes=95)).isoformat()
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
        {
            "id": True,
            "status": "completed",
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
            "run_started_at": None,
            "conclusion": "failure",
            "display_title": "Data Collection",
        },
        {
            "id": 1,
            "status": "completed",
            "created_at": "not-a-time",
            "updated_at": NOW.isoformat(),
            "run_started_at": None,
            "conclusion": "failure",
            "display_title": "Data Collection",
        },
        {
            "id": 1,
            "status": "in_progress",
            "created_at": NOW.isoformat(),
            "run_started_at": "not-a-time",
            "updated_at": NOW.isoformat(),
            "conclusion": None,
            "display_title": "Data Collection",
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
    observation = watchdog.observe_publication(_status(age_minutes=160), now=NOW)
    decision = watchdog.generation_preflight_decision(
        observation,
        expected_generation=(NOW - timedelta(hours=2)).isoformat(),
        now=NOW,
    )

    assert decision.required is True
    assert decision.reason == "publication-stale"


def test_preflight_keeps_same_generation_recovery() -> None:
    observation = watchdog.observe_publication(_status(age_minutes=160), now=NOW)
    decision = watchdog.generation_preflight_decision(
        observation,
        expected_generation=(NOW - timedelta(minutes=160)).isoformat(),
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
    observation = watchdog.observe_publication(_status(age_minutes=160), now=NOW)
    decision = watchdog.generation_preflight_decision(
        observation,
        expected_generation=watchdog.UNAVAILABLE_GENERATION,
        now=NOW,
    )

    assert decision.required is True
    assert decision.reason == "publication-stale"


def test_cadence_preflight_coalesces_recent_publication() -> None:
    observation = watchdog.observe_publication(
        _status(age_minutes=119),
        now=NOW,
        max_publication_age_minutes=120,
    )
    decision = watchdog.cadence_preflight_decision(observation)

    assert decision.required is False
    assert decision.reason == "canonical-recent"


def test_cadence_preflight_keeps_normally_due_schedule() -> None:
    observation = watchdog.observe_publication(
        _status(age_minutes=120),
        now=NOW,
        max_publication_age_minutes=120,
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
    status_path.write_text(json.dumps(_status(age_minutes=160)))
    runs_path.write_text(json.dumps({"workflow_runs": []}))

    result = watchdog.main(
        [
            "--status",
            str(status_path),
            "--workflow-runs",
            str(runs_path),
            "--recovery-target",
            "collector",
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
        "observed_generation=2026-08-30T02:20:00Z",
        f"recovery_key={_key(_status(age_minutes=160))}",
    ]


def test_forced_target_reuses_active_and_cooldown_suppression(tmp_path) -> None:
    status_path = tmp_path / "status.json"
    runs_path = tmp_path / "runs.json"
    output_path = tmp_path / "github-output"
    status_path.write_text(json.dumps(_status(age_minutes=10)))
    runs_path.write_text(json.dumps({"workflow_runs": [_run(age_minutes=5, status="in_progress")]}))

    assert (
        watchdog.main(
            [
                "--status",
                str(status_path),
                "--workflow-runs",
                str(runs_path),
                "--recovery-target",
                "dns-health",
                "--force-recovery-reason",
                "dns-only-degraded",
                "--now",
                NOW.isoformat(),
                "--github-output",
                str(output_path),
            ]
        )
        == 0
    )
    assert "required=false" in output_path.read_text()
    assert "reason=collection-active" in output_path.read_text()


def test_forced_target_dispatches_after_selected_workflow_cooldown(tmp_path) -> None:
    status_path = tmp_path / "status.json"
    runs_path = tmp_path / "runs.json"
    output_path = tmp_path / "github-output"
    status_path.write_text(json.dumps(_status(age_minutes=10)))
    runs_path.write_text(json.dumps({"workflow_runs": [_run(age_minutes=71, status="completed")]}))

    assert (
        watchdog.main(
            [
                "--status",
                str(status_path),
                "--workflow-runs",
                str(runs_path),
                "--recovery-target",
                "dns-health",
                "--force-recovery-reason",
                "dns-only-degraded",
                "--retry-cooldown-minutes",
                "70",
                "--now",
                NOW.isoformat(),
                "--github-output",
                str(output_path),
            ]
        )
        == 0
    )
    assert output_path.read_text().splitlines() == [
        "required=true",
        "reason=dns-only-degraded",
        "observed_generation=2026-08-30T04:50:00Z",
        "recovery_key="
        + watchdog.recovery_key(
            target="dns-health",
            reason="dns-only-degraded",
            observed_generation="2026-08-30T04:50:00Z",
        ),
    ]


def test_dns_success_with_same_key_is_cooled_down_to_avoid_gate_loop() -> None:
    status = _status(age_minutes=10)
    observation = watchdog.observe_publication(status, now=NOW)
    forced = watchdog.PublicationObservation(
        "dns-only-degraded", observation.generation, observation.generated_at
    )
    key = watchdog.recovery_key(
        target="dns-health",
        reason="dns-only-degraded",
        observed_generation=observation.generation,
    )
    runs = watchdog.parse_workflow_runs(
        {"workflow_runs": [_run(age_minutes=1, recovery_key=key, conclusion="success")]},
        now=NOW,
    )

    decision = watchdog.watchdog_decision(
        forced,
        runs,
        now=NOW,
        recovery_target="dns-health",
        retry_cooldown_minutes=70,
    )

    assert decision.required is False
    assert decision.reason == "recent-recovery-attempt"


def test_two_hour_cadence_and_recovery_budget_precede_three_hour_ui_slo() -> None:
    assert watchdog.AUTOMATED_COLLECTION_CADENCE_MINUTES == 120
    assert watchdog.DEFAULT_MAX_PUBLICATION_AGE_MINUTES == 95
    assert watchdog.WATCHDOG_OBSERVATION_INTERVAL_MINUTES == 15
    assert watchdog.DEFAULT_RETRY_COOLDOWN_MINUTES == 15

    first_attempt_bound = (
        watchdog.DEFAULT_MAX_PUBLICATION_AGE_MINUTES
        + watchdog.WATCHDOG_OBSERVATION_INTERVAL_MINUTES
        + watchdog.COLLECTION_TIMEOUT_MINUTES
    )
    normal_retry_bound = (
        watchdog.DEFAULT_MAX_PUBLICATION_AGE_MINUTES
        + watchdog.WATCHDOG_OBSERVATION_INTERVAL_MINUTES
        + watchdog.NORMAL_COLLECTION_RUNTIME_MINUTES
        + watchdog.DEFAULT_RETRY_COOLDOWN_MINUTES
        + watchdog.NORMAL_COLLECTION_RUNTIME_MINUTES
    )
    assert first_attempt_bound == 170
    assert normal_retry_bound == 175
    assert normal_retry_bound < first_attempt_bound + 10
    assert first_attempt_bound < watchdog.SITE_HEALTH_FRESHNESS_LIMIT_MINUTES
    assert normal_retry_bound < watchdog.SITE_HEALTH_FRESHNESS_LIMIT_MINUTES
