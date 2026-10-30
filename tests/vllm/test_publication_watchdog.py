"""Tests for proactive, deduplicated canonical publication recovery."""

import inspect
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from vllm import plan_publication_watchdog as watchdog
from vllm import plan_stuck_run_cancellation as janitor


NOW = datetime(2026, 8, 30, 5, tzinfo=timezone.utc)
CODE_SHA = "a" * 40
OLD_CODE_SHA = "b" * 40


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
    tag="recovery",
    display_title=None,
    head_branch="main",
    head_sha=CODE_SHA,
):
    if status == "completed" and conclusion is None:
        conclusion = "failure"
    if display_title is None:
        display_title = "Data Collection"
        if recovery_key:
            display_title += f" [{tag}:{recovery_key}]"
    row = {
        "id": run_id,
        "head_branch": head_branch,
        "head_sha": head_sha,
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


def _decision(status, runs=(), *, target="collector", current_code_sha=None):
    observation = watchdog.observe_publication(status, now=NOW)
    parsed_runs = watchdog.parse_workflow_runs({"workflow_runs": list(runs)}, now=NOW)
    return watchdog.watchdog_decision(
        observation, parsed_runs, now=NOW, recovery_target=target,
        current_code_sha=current_code_sha,
    )


def _key(status, *, target="collector"):
    observation = watchdog.observe_publication(status, now=NOW)
    assert observation.recovery_reason is not None
    return watchdog.recovery_key(
        target=target,
        reason=observation.recovery_reason,
        observed_generation=observation.generation,
    )


def test_current_publication_does_not_dispatch() -> None:
    decision = _decision(_status(age_minutes=74.99))

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


def test_75_minute_boundary_dispatches_with_canonical_generation() -> None:
    decision = _decision(_status(age_minutes=75))

    assert decision.required is True
    assert decision.reason == "publication-stale"
    assert decision.observed_generation == "2026-08-30T03:45:00Z"


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


@pytest.mark.parametrize("head_branch", [None, "feature/runtime-test"])
def test_workflow_run_evidence_requires_exact_main_branch(head_branch) -> None:
    row = _run(age_minutes=10, status="in_progress", head_branch=head_branch)
    if head_branch is None:
        row.pop("head_branch")

    with pytest.raises(ValueError, match="invalid row"):
        watchdog.parse_workflow_runs({"workflow_runs": [row]}, now=NOW)


FULL_KEY = "1" * 64
QUEUE_KEY = "2" * 64


@pytest.mark.parametrize(("title", "mode", "key"), [
    ("Data Collection", "routine", None),
    (f"Data Collection [full:{FULL_KEY}]", "full", FULL_KEY),
    ("Data Collection [full:]", "full", None),
    (f"Data Collection [queue:{QUEUE_KEY}]", "queue", QUEUE_KEY),
    ("Data Collection [dns:2026-08-30T04:00:00Z]", "dns", None),
    (f"DNS Health Monitor [recovery:{FULL_KEY}]", "recovery", FULL_KEY),
])
def test_run_title_tag_sets_mode_and_recovery_key(title, mode, key) -> None:
    (run,) = watchdog.parse_workflow_runs(
        {"workflow_runs": [_run(age_minutes=5, display_title=title)]}, now=NOW,
    )

    assert (run.mode, run.recovery_key) == (mode, key)


def test_run_title_with_two_mode_tags_fails_closed() -> None:
    title = f"Data Collection [full:{FULL_KEY}] [queue:{QUEUE_KEY}]"
    with pytest.raises(ValueError, match="multiple run tags"):
        watchdog.parse_workflow_runs(
            {"workflow_runs": [_run(age_minutes=5, display_title=title)]}, now=NOW,
        )


@pytest.mark.parametrize("run_status", ["queued", "in_progress"])
@pytest.mark.parametrize(("title", "active"), [
    ("Data Collection", True),
    (f"Data Collection [full:{FULL_KEY}]", True),
    (f"Data Collection [queue:{QUEUE_KEY}]", False),
    ("Data Collection [dns:2026-08-30T04:00:00Z]", False),
])
def test_only_full_and_routine_runs_count_as_collection_active(run_status, title, active) -> None:
    decision = _decision(
        _status(age_minutes=160),
        [_run(age_minutes=5, status=run_status, started_age_minutes=5, display_title=title)],
    )

    assert decision.required is not active
    assert decision.reason == ("collection-active" if active else "publication-stale")


def test_every_data_collection_run_name_tag_is_classified() -> None:
    root = Path(__file__).resolve().parents[2]
    run_name = yaml.safe_load((root / ".github/workflows/hourly-master.yml").read_text())["run-name"]
    tags = set(re.findall(r"format\(' \[(\w+):\{0\}\]'", run_name))

    assert tags == {"full", "queue", "dns"}
    assert tags - {"full"} == watchdog.RECONCILIATION_MODES


def test_full_run_title_key_drives_collector_retry_cooldown() -> None:
    status = _status(age_minutes=160)
    decision = _decision(status, [_run(age_minutes=5, recovery_key=_key(status), tag="full")])

    assert decision.reason == "recent-recovery-attempt"


TARGETS = watchdog.CoreTargets(("analytics", "ci_health", "amd_test_matrix"), 135)


def write_operations_manifest(path: Path, clocks: dict[str, str | None]) -> Path:
    """Write a hydrated operations manifest with these ``shell.sources`` clocks."""
    path.write_text(json.dumps({"shell": {"sources": {
        key: {"timestamp": clock} for key, clock in clocks.items()
    }}}))
    return path


def _manifest(tmp_path: Path, ages: dict[str, float | None]) -> Path:
    """Core clocks ``ages`` minutes before NOW (None = unreadable)."""
    return write_operations_manifest(tmp_path / "operations.json", {
        key: None if age is None else watchdog._canonical_timestamp(NOW - timedelta(minutes=age))
        for key, age in ages.items()
    })


@pytest.mark.parametrize(("ages", "source", "stale"), [
    ({"analytics": 30, "ci_health": 134.9, "amd_test_matrix": 60, "queue_jobs": 900}, "ci_health", False),
    ({"analytics": 30, "ci_health": 60, "amd_test_matrix": 135}, "amd_test_matrix", True),
    ({"analytics": 300, "ci_health": 60, "amd_test_matrix": 20}, "analytics", True),
])
def test_core_age_is_the_oldest_core_source_clock(tmp_path, ages, source, stale) -> None:
    age = watchdog.core_data_age(_manifest(tmp_path, ages), TARGETS, now=NOW)

    assert age == watchdog.CoreDataAge(source, NOW - timedelta(minutes=ages[source]), stale)


@pytest.mark.parametrize(("ages", "source"), [
    ({"analytics": 30, "amd_test_matrix": 20}, "ci_health"),
    ({"analytics": 30, "ci_health": 30, "amd_test_matrix": None}, "amd_test_matrix"),
    ({"analytics": -10, "ci_health": 30, "amd_test_matrix": 20}, "analytics"),
])
def test_missing_unreadable_or_future_core_clock_counts_as_stale(tmp_path, ages, source) -> None:
    age = watchdog.core_data_age(_manifest(tmp_path, ages), TARGETS, now=NOW)

    assert age == watchdog.CoreDataAge(source, None, True)


def test_absent_duplicate_or_oversized_manifest_counts_as_stale(tmp_path, monkeypatch) -> None:
    fresh = _manifest(tmp_path, dict.fromkeys(TARGETS.sources, 10))
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"shell": {}, "shell": ' + json.dumps(json.loads(fresh.read_text())["shell"]) + "}")
    unknown = watchdog.CoreDataAge("analytics", None, True)
    assert watchdog.core_data_age(fresh, TARGETS, now=NOW).collected_at is not None
    for path in (tmp_path / "absent.json", duplicate):
        assert watchdog.core_data_age(path, TARGETS, now=NOW) == unknown
    monkeypatch.setattr(watchdog, "OPERATIONS_MANIFEST_MAX_BYTES", fresh.stat().st_size - 1)
    assert watchdog.core_data_age(fresh, TARGETS, now=NOW) == unknown


def test_repository_core_targets_are_the_manifest_clocked_core_sources() -> None:
    config = json.loads(watchdog.FRESHNESS_TARGETS.read_text())
    targets = watchdog.load_core_targets()

    assert targets.sources == tuple(
        key
        for key in (*config["core"]["sources"], *config["core"]["main_derived_sources"])
        if "path" not in config["sources"][key]
    )
    assert "perf_eval" in config["core"]["sources"] and "perf_eval" not in targets.sources
    assert "main_ci" in targets.sources
    assert targets.target_minutes == config["core"]["target_minutes"]


@pytest.mark.parametrize(("stale", "reason"), [
    (False, "buildkite-collection-due"), (True, "core-data-stale"),
])
def test_open_admission_promotes_current_publication(stale, reason) -> None:
    current = watchdog.observe_publication(_status(age_minutes=10), now=NOW)
    core_age = watchdog.CoreDataAge("ci_health", NOW - timedelta(minutes=100), stale)
    promoted = watchdog.admitted_observation(current, core_age)

    assert promoted == watchdog.PublicationObservation(reason, current.generation, current.generated_at)
    assert watchdog.watchdog_decision(promoted, (), now=NOW).required is True


def test_open_admission_keeps_an_existing_recovery_reason() -> None:
    stale = watchdog.observe_publication(_status(age_minutes=160), now=NOW)
    core_age = watchdog.CoreDataAge("ci_health", None, True)

    assert watchdog.admitted_observation(stale, core_age) == stale


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


@pytest.mark.parametrize(
    ("due", "runs", "required", "reason"),
    [
        ("false", [], "false", "docker-builds-not-due"),
        ("true", [_run(age_minutes=10, status="in_progress")], "false", "docker-builds-active"),
        ("true", [_run(age_minutes=50, status="queued")], "true", "docker-builds-due"),
        ("true", [_run(age_minutes=5, conclusion="success")], "true", "docker-builds-due"),
    ],
    ids=["not-due", "active", "orphaned", "completed"],
)
def test_cli_starts_docker_build_monitor_only_when_due_and_idle(
    tmp_path, due, runs, required, reason,
) -> None:
    runs_path = tmp_path / "runs.json"
    output_path = tmp_path / "github-output"
    runs_path.write_text(json.dumps({"workflow_runs": runs}))

    assert watchdog.main([
        "--workflow-runs", str(runs_path),
        "--recovery-target", "docker-builds",
        "--docker-builds-due", due,
        "--now", NOW.isoformat(),
        "--github-output", str(output_path),
    ]) == 0
    assert output_path.read_text().splitlines()[:2] == [
        f"required={required}",
        f"reason={reason}",
    ]


def test_cli_docker_target_requires_its_ledger_decision_and_no_status(tmp_path) -> None:
    runs_path = tmp_path / "runs.json"
    runs_path.write_text(json.dumps({"workflow_runs": []}))
    base = ["--workflow-runs", str(runs_path), "--recovery-target", "docker-builds"]

    with pytest.raises(SystemExit):
        watchdog.main(base)
    with pytest.raises(SystemExit):
        watchdog.main([*base, "--docker-builds-due", "true", "--status", str(runs_path)])


def test_docker_build_monitor_cadence_cannot_fail_the_recovery_job() -> None:
    """Site Health reads a red watchdog as a recovery failure; docker cadence is not recovery."""
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/publication-watchdog.yml").read_text())
    steps = [
        step for step in workflow["jobs"]["recover"]["steps"]
        if "docker-builds.yml" in step.get("run", "")
    ]
    assert len(steps) == 2
    assert all(step.get("continue-on-error") is True for step in steps)


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
                "--current-code-sha",
                CODE_SHA,
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
                "--current-code-sha",
                CODE_SHA,
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


@pytest.mark.parametrize("active", [False, True])
def test_collection_retry_due_ignores_recent_publication_but_respects_active_run(tmp_path, active):
    status_path = tmp_path / "status.json"
    runs_path = tmp_path / "runs.json"
    output_path = tmp_path / "github-output"
    status_path.write_text(json.dumps(_status(age_minutes=1)))
    runs_path.write_text(json.dumps({"workflow_runs": [
        _run(age_minutes=5, status="in_progress")
    ] if active else []}))
    assert watchdog.main([
        "--status", str(status_path), "--workflow-runs", str(runs_path),
        "--recovery-target", "collector", "--force-recovery-reason", "collection-retry-due",
        "--now", NOW.isoformat(), "--github-output", str(output_path),
    ]) == 0
    output = output_path.read_text()
    assert f"required={'false' if active else 'true'}" in output
    assert f"reason={'collection-active' if active else 'collection-retry-due'}" in output


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
        current_code_sha=CODE_SHA,
    )

    assert decision.required is False
    assert decision.reason == "recent-recovery-attempt"


@pytest.mark.parametrize("conclusion", sorted(watchdog.FAILED_ATTEMPT_CONCLUSIONS))
def test_normal_dns_failed_attempt_observes_same_code_cooldown(conclusion) -> None:
    status = _status(age_minutes=160)
    runs = watchdog.parse_workflow_runs(
        {"workflow_runs": [_run(age_minutes=100, updated_age_minutes=5, conclusion=conclusion)]},
        now=NOW,
    )
    decision = watchdog.watchdog_decision(
        watchdog.observe_publication(status, now=NOW), runs, now=NOW,
        recovery_target="dns-health", retry_cooldown_minutes=70, current_code_sha=CODE_SHA,
    )

    assert decision.required is False
    assert decision.reason == "recent-recovery-attempt"


@pytest.mark.parametrize("age_minutes, required", [(70, False), (70.001, True)])
def test_normal_dns_failed_attempt_has_bounded_70_minute_cooldown(age_minutes, required) -> None:
    runs = watchdog.parse_workflow_runs(
        {"workflow_runs": [_run(age_minutes=age_minutes)]}, now=NOW,
    )
    decision = watchdog.watchdog_decision(
        watchdog.observe_publication(_status(age_minutes=160), now=NOW), runs, now=NOW,
        recovery_target="dns-health", retry_cooldown_minutes=70, current_code_sha=CODE_SHA,
    )

    assert decision.required is required


@pytest.mark.parametrize("keyed", [False, True])
def test_new_dns_code_can_recover_after_old_code_completed_attempt(keyed) -> None:
    status = _status(age_minutes=160)
    decision = _decision(
        status,
        [_run(age_minutes=5, head_sha=OLD_CODE_SHA,
              recovery_key=_key(status, target="dns-health") if keyed else None)],
        target="dns-health", current_code_sha=CODE_SHA,
    )

    assert decision.required is True
    assert decision.reason == "publication-stale"


@pytest.mark.parametrize("conclusion", ["success", "neutral", "skipped"])
def test_normal_dns_nonfailure_does_not_hide_persistent_incident(conclusion) -> None:
    decision = _decision(
        _status(age_minutes=160), [_run(age_minutes=5, conclusion=conclusion)],
        target="dns-health", current_code_sha=CODE_SHA,
    )

    assert decision.required is True


def test_unrelated_keyed_dns_incident_keeps_incident_scoped_deduplication() -> None:
    decision = _decision(
        _status(age_minutes=160), [_run(age_minutes=5, recovery_key="1" * 64)],
        target="dns-health", current_code_sha=CODE_SHA,
    )

    assert decision.required is True


def test_new_dns_code_still_waits_for_keyed_old_code_active_run() -> None:
    status = _status(age_minutes=160)
    decision = _decision(
        status,
        [_run(age_minutes=5, status="in_progress", head_sha=OLD_CODE_SHA,
              recovery_key=_key(status, target="dns-health"))],
        target="dns-health", current_code_sha=CODE_SHA,
    )

    assert decision.required is False
    assert decision.reason == "collection-active"


@pytest.mark.parametrize("target", ["collector", "deploy-pages"])
def test_normal_failure_does_not_extend_other_recovery_target_cooldowns(target) -> None:
    decision = _decision(_status(age_minutes=160), [_run(age_minutes=5)], target=target)

    assert decision.required is True


@pytest.mark.parametrize("status", sorted(watchdog.QUEUED_RUN_STATUSES | {"in_progress"}))
@pytest.mark.parametrize("age_minutes, required", [(75, False), (76, True)])
def test_normal_dns_active_run_keeps_existing_age_bound_across_code_changes(
    status, age_minutes, required,
) -> None:
    decision = _decision(
        _status(age_minutes=160),
        [_run(age_minutes=age_minutes, status=status, head_sha=OLD_CODE_SHA,
              updated_age_minutes=1)],
        target="dns-health", current_code_sha=CODE_SHA,
    )

    assert decision.required is required
    if not required:
        assert decision.reason == "collection-active"


@pytest.mark.parametrize("current_code_sha", [None, "", "a" * 39, "a" * 41, "A" * 40, True])
def test_dns_code_aware_recovery_requires_valid_current_code_identity(current_code_sha) -> None:
    with pytest.raises(ValueError, match="exact current code SHA"):
        _decision(_status(age_minutes=160), target="dns-health", current_code_sha=current_code_sha)


def test_dns_code_aware_recovery_fails_closed_on_missing_run_code_identity() -> None:
    row = _run(age_minutes=5)
    row.pop("head_sha")

    with pytest.raises(ValueError, match="missing an exact head SHA"):
        _decision(_status(age_minutes=160), [row], target="dns-health", current_code_sha=CODE_SHA)


@pytest.mark.parametrize("head_sha", ["", "a" * 39, "a" * 41, "A" * 40, True, []])
def test_malformed_run_code_identity_fails_closed(head_sha) -> None:
    with pytest.raises(ValueError, match="invalid row"):
        watchdog.parse_workflow_runs(
            {"workflow_runs": [_run(age_minutes=5, head_sha=head_sha)]}, now=NOW,
        )


@pytest.mark.parametrize("target, extra_args", [
    ("dns-health", []),
    ("collector", ["--current-code-sha", CODE_SHA]),
    ("deploy-pages", ["--current-code-sha", CODE_SHA]),
])
def test_dns_code_cli_argument_is_required_only_for_dns_workflow_recovery(
    tmp_path, target, extra_args,
) -> None:
    output_path = tmp_path / "outputs"
    with pytest.raises(SystemExit) as error:
        watchdog.main([
            "--status", str(tmp_path / "status.json"),
            "--workflow-runs", str(tmp_path / "runs.json"),
            "--recovery-target", target, "--github-output", str(output_path),
            *extra_args,
        ])

    assert error.value.code == 2
    assert not output_path.exists()


def test_actual_dns_watchdog_plan_uses_trusted_checkout_for_normal_attempt_cooldown(tmp_path) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/publication-watchdog.yml").read_text())
    steps = workflow["jobs"]["recover"]["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "main"
    plan = next(step for step in steps if step.get("id") == "recovery-plan")["run"]
    head_sha = subprocess.check_output(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=root, text=True,
    ).strip()
    (tmp_path / "publication-status.json").write_text(json.dumps(_status(age_minutes=10)))
    (tmp_path / "selected-recovery-runs.json").write_text(json.dumps({"workflow_runs": [
        _run(age_minutes=5, head_sha=head_sha, conclusion="cancelled"),
    ]}))
    output_path = tmp_path / "outputs"
    env = dict(os.environ, RUNNER_TEMP=str(tmp_path), GITHUB_OUTPUT=str(output_path),
               RECOVERY_TARGET="dns-health", FORCE_REASON="dns-only-degraded",
               TEST_PYTHON=sys.executable, TEST_NOW=NOW.isoformat())
    script = 'python() { "$TEST_PYTHON" "$@" --now "$TEST_NOW"; }\n' + plan
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script], cwd=root, env=env,
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "required=false" in output_path.read_text().splitlines()
    assert "reason=recent-recovery-attempt" in output_path.read_text().splitlines()


def test_actual_recovery_limits_do_not_claim_an_unconditional_three_hour_guarantee() -> None:
    """Read the real bounds, including the watchdog's own proof runtime."""
    root = Path(__file__).resolve().parents[2]
    hourly = yaml.safe_load((root / ".github/workflows/hourly-master.yml").read_text())
    recovery = yaml.safe_load((root / ".github/workflows/publication-watchdog.yml").read_text())
    policy = json.loads((root / "config/data_collection_attempt_budget.json").read_text())
    collector_timeout = hourly["jobs"]["collect-and-deploy"]["timeout-minutes"]
    watchdog_timeout = recovery["jobs"]["recover"]["timeout-minutes"]
    assert collector_timeout == 75
    assert watchdog_timeout == 40
    assert policy["failed_retry_interval_minutes"] == 30
    assert policy["success_interval_minutes"] == 120

    # PyYAML's YAML 1.1 loader parses the unquoted GitHub Actions `on` as True.
    events = recovery.get("on", recovery.get(True))
    cron = events["schedule"][0]["cron"]
    minutes, hours, *_ = cron.split()
    assert hours == "*"
    ticks = sorted(int(value) for value in minutes.split(","))
    tick_intervals = [end - start for start, end in zip(ticks, ticks[1:] + [ticks[0] + 60])]
    assert set(tick_intervals) == {15}
    observation_interval = max(tick_intervals)
    steps = recovery["jobs"]["recover"]["steps"]
    plan = next(step for step in steps if step.get("id") == "recovery-plan")
    trigger = float(re.search(r"--max-age-minutes (\d+)", plan["run"]).group(1))
    assert trigger == watchdog.DEFAULT_MAX_PUBLICATION_AGE_MINUTES == 75

    from vllm.check_site_health import DEFAULT_MAX_PUBLICATION_AGE_HOURS

    freshness_limit = DEFAULT_MAX_PUBLICATION_AGE_HOURS * 60
    detection_and_collection = trigger + observation_interval + collector_timeout
    assert freshness_limit - detection_and_collection >= 15
    complete_first_attempt_bound = detection_and_collection + watchdog_timeout
    assert complete_first_attempt_bound == 205
    assert complete_first_attempt_bound > freshness_limit
    # Retry eligibility is start-to-start. A timed-out first attempt already
    # outlasts the 30-minute durable cooldown; even then, a second full timeout
    # cannot be guaranteed within the freshness limit. Caps remain authoritative.
    retry_bound = (
        trigger + observation_interval + watchdog_timeout
        + max(collector_timeout, policy["failed_retry_interval_minutes"])
        + collector_timeout
    )
    assert retry_bound > freshness_limit


@pytest.mark.parametrize("failure", ["stale", "projection", "malformed"])
def test_actual_health_recovery_step_dispatches_the_validated_lane(tmp_path, failure) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/publication-watchdog.yml").read_text())
    step = next(
        step for step in workflow["jobs"]["recover"]["steps"]
        if step.get("id") == "recovery-route"
    )["run"]
    start = step.index("if HEALTH_TARGET=")
    end = step.index('echo "::error::The failed Site Health run', start)
    full = failure != "projection"
    code = "publication-stale" if full else "projection-file-digest"
    report = {
        "healthy": False, "overall_status": "confirmed_unhealthy",
        "reasons": [
            {"code": code, "message": "Source-shaped health failure."},
            {"code": "confirmation-quorum", "message": "Zero healthy probes."},
            {"code": "projection-generation-quorum" if full else "complete-projection-required",
             "message": "The required healthy projection quorum failed."},
        ],
        "confirmation": {
            "confirmed": True, "strategy": "2-of-3-quorum",
            "max_attempts": 3, "attempted": 3, "required_healthy": 2,
            "healthy_count": 0, "unhealthy_count": 3,
            "complete_projection_verified": full,
            "complete_projection_attempt": 1 if full else None,
            "matching_projection_healthy_count": 0,
            "required_matching_projection_healthy": 2,
            "probes": [
                {"healthy": False, "complete_projection": full and index == 0,
                 "matches_complete_projection": full, "reason_codes": [code]}
                for index in range(3)
            ],
        },
    }
    if failure == "malformed":
        report["confirmation"]["healthy_count"] = True
    health_path = tmp_path / "health.json"
    health_path.write_text(json.dumps(report))
    output_path = tmp_path / "outputs"
    status_path = tmp_path / "status.json"
    # Execute the actual workflow branch and validator; the only Git read is
    # replaced by the exact observed status. No GitHub or Buildkite call runs.
    script = (
        'git() { [ "$*" = "show origin/gh-pages:data/vllm/ci/publication_status.json" ] '
        '|| return 97; printf "%s" "$TEST_OBSERVED_STATUS"; }\n'
        + step[start:end] + "exit 1\n"
    )
    env = dict(os.environ, HEALTH_REPORT=str(health_path), GITHUB_OUTPUT=str(output_path),
               STATUS_PATH=str(status_path), TEST_OBSERVED_STATUS=json.dumps(_status(age_minutes=190)))
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script],
                            cwd=root, env=env, capture_output=True, text=True)
    if failure == "malformed":
        assert result.returncode == 1
        assert not output_path.exists()
        assert not status_path.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert output_path.read_text().splitlines() == (
            ["target=collector", "force_reason=site-health-stale"] if failure == "stale"
            else ["target=deploy-pages", "force_reason=site-health-failed"]
        )
        assert status_path.exists() is (failure == "stale")
        if failure == "stale":
            assert json.loads(status_path.read_text()) == _status(age_minutes=190)


def test_preserved_main_cohort_makes_core_stale_while_analytics_is_fresh(tmp_path) -> None:
    targets = watchdog.load_core_targets()
    ages = dict.fromkeys(targets.sources, 30)
    ages["main_ci"] = targets.target_minutes + 1
    age = watchdog.core_data_age(_manifest(tmp_path, ages), targets, now=NOW)

    assert age == watchdog.CoreDataAge(
        "main_ci", NOW - timedelta(minutes=targets.target_minutes + 1), True
    )


def test_manifest_predating_main_ci_is_judged_by_its_core_clocks_only(tmp_path) -> None:
    targets = watchdog.load_core_targets()
    ages = {key: 30 for key in targets.sources if key not in targets.main_derived}
    age = watchdog.core_data_age(_manifest(tmp_path, ages), targets, now=NOW)
    assert age.stale is False and age.source != "main_ci"

    undated = watchdog.core_data_age(_manifest(tmp_path, {**ages, "main_ci": None}), targets, now=NOW)
    assert undated == watchdog.CoreDataAge("main_ci", None, True)


@pytest.mark.parametrize("workflow_file", ["publication-watchdog.yml", "queue-monitor.yml"])
def test_workflow_run_state_is_projected_to_the_fields_the_parser_reads(workflow_file) -> None:
    """Raw Actions rows carry head_commit messages; 100 of them outgrew the byte bound."""
    root = Path(__file__).resolve().parents[2]
    text = (root / ".github/workflows" / workflow_file).read_text()
    projection = re.search(
        r"actions/workflows/\S+/runs\?\S+ \\\n\s+--jq '\{workflow_runs: \[\.workflow_runs\[\] \| \{([a-z_, ]+)\}\]\}'",
        text,
    )
    assert projection, f"{workflow_file} must project the workflow run state"
    projected = {field.strip() for field in projection.group(1).split(",")}
    parser_source = inspect.getsource(watchdog.parse_workflow_runs)
    assert projected == set(re.findall(r'row\.get\("([a-z_]+)"\)', parser_source))


JANITOR_NOW = datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc)
HOURLY = ".github/workflows/hourly-master.yml"


def _active(run_id, *, hours, status="in_progress", path=HOURLY, started_hours=None):
    created = JANITOR_NOW - timedelta(hours=hours)
    started = created if started_hours is None else JANITOR_NOW - timedelta(hours=started_hours)
    return {"id": run_id, "name": "Data Collection", "path": path, "status": status,
            "created_at": created.isoformat(), "run_started_at": started.isoformat()}


def _stuck(*rows, exclude=None):
    selected = janitor.select_stuck_runs(
        [{"workflow_runs": list(rows)}], janitor.load_policy(), now=JANITOR_NOW,
        exclude_run_id=exclude,
    )
    return [run.run_id for run in selected]


def test_run_janitor_bounds_are_each_workflows_job_timeouts_plus_six_hours() -> None:
    """A stale bound would cancel a run that is still within its own timeouts."""
    root = Path(__file__).resolve().parents[2]
    policy = janitor.load_policy()
    expected = {}
    for path in sorted((root / ".github/workflows").glob("*.yml")):
        jobs = yaml.safe_load(path.read_text())["jobs"].values()
        expected[f".github/workflows/{path.name}"] = sum(
            int(job.get("timeout-minutes", policy.default_timeout_minutes)) for job in jobs
        )
    assert policy.workflow_timeout_minutes == expected
    assert policy.margin_minutes == 360
    assert policy.limit(HOURLY) == timedelta(minutes=135 + 360)


def test_run_janitor_cancels_only_runs_past_their_bound() -> None:
    bound = (135 + 360) / 60
    assert _stuck(_active(1, hours=bound)) == []
    assert _stuck(_active(1, hours=bound + 0.01)) == [1]
    assert _stuck(_active(1, hours=bound + 1, status="queued")) == [1]
    # A re-run attempt restarts the clock.
    assert _stuck(_active(1, hours=48, started_hours=1)) == []
    # Unknown workflows (e.g. GitHub's dynamic Pages build) get the 6 h default job bound.
    assert _stuck(_active(1, hours=11.9, path="dynamic/pages/pages-build-deployment")) == []
    assert _stuck(_active(1, hours=12.1, path="dynamic/pages/pages-build-deployment")) == [1]
    assert _stuck(_active(7, hours=240), exclude=7) == []


def test_run_janitor_takes_the_oldest_runs_up_to_its_cap(monkeypatch) -> None:
    policy = janitor.load_policy()
    monkeypatch.setattr(janitor, "load_policy", lambda: janitor.JanitorPolicy(
        policy.margin_minutes, policy.default_timeout_minutes, 2, policy.workflow_timeout_minutes,
    ))
    assert _stuck(_active(1, hours=20), _active(2, hours=90), _active(3, hours=50)) == [2, 3]


@pytest.mark.parametrize("patch", [
    {"id": "1"}, {"id": 0}, {"status": "completed"}, {"path": None},
    {"created_at": "yesterday"}, {"run_started_at": "soon"},
    {"created_at": (JANITOR_NOW + timedelta(hours=1)).isoformat()},
])
def test_run_janitor_cancels_nothing_on_an_unreadable_listing(patch) -> None:
    with pytest.raises(ValueError, match="invalid row"):
        _stuck(_active(1, hours=240), {**_active(2, hours=240), **patch})


def _run_janitor_step(tmp_path, listings, *, failing_id=None):
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/publication-watchdog.yml").read_text())
    step = next(s for s in workflow["jobs"]["recover"]["steps"] if s.get("id") == "run-janitor")
    assert step["if"] == "${{ !cancelled() }}"
    assert workflow["jobs"]["recover"]["permissions"]["actions"] == "write"
    for status, rows in listings.items():
        (tmp_path / f"fixture-{status}.json").write_text(json.dumps({"workflow_runs": rows}))
    calls = tmp_path / "calls"
    # The fake gh serves the projected listings and records every POST; nothing
    # reaches GitHub.
    fake = r'''
      gh() {
        local url=""
        for arg in "$@"; do case "$arg" in repos/*) url="$arg" ;; esac; done
        if [[ " $* " == *" --method POST "* ]]; then
          echo "POST $url" >> "$TEST_CALLS"
          [[ "$url" != */"$TEST_FAILING_ID"/force-cancel ]]
          return
        fi
        local status="${url##*status=}"
        status="${status%%&*}"
        cat "$TEST_FIXTURES/fixture-$status.json" 2>/dev/null || echo '{"workflow_runs": []}'
      }
      python() { "$TEST_PYTHON" "$@" --now "$TEST_NOW"; }
    '''
    env = dict(os.environ, RUNNER_TEMP=str(tmp_path), GITHUB_OUTPUT=str(tmp_path / "out"),
               GITHUB_STEP_SUMMARY=str(tmp_path / "summary"), GITHUB_REPOSITORY="o/r",
               GITHUB_RUN_ID="41030000000", TEST_CALLS=str(calls), TEST_FIXTURES=str(tmp_path),
               TEST_FAILING_ID=str(failing_id or "none"), TEST_PYTHON=sys.executable,
               TEST_NOW=JANITOR_NOW.isoformat())
    result = subprocess.run(["bash", "-c", fake + step["run"]], cwd=root, env=env,
                            capture_output=True, text=True)
    posts = calls.read_text().splitlines() if calls.exists() else []
    return result, posts


def test_watchdog_force_cancels_the_stuck_runs_seen_live(tmp_path) -> None:
    """Live on 2026-10-30: 37655509945 queued since 10-07, 39462175700 in progress since 10-20."""
    listings = {
        "queued": [_active(37655509945, hours=543, status="queued",
                           path=".github/workflows/publication-watchdog.yml")],
        "in_progress": [_active(41023173701, hours=0.1), _active(39462175700, hours=231),
                        _active(41030000000, hours=999,
                                path=".github/workflows/publication-watchdog.yml")],
    }
    result, posts = _run_janitor_step(tmp_path, listings)

    assert result.returncode == 0, result.stderr
    assert posts == [
        "POST repos/o/r/actions/runs/37655509945/force-cancel",
        "POST repos/o/r/actions/runs/39462175700/force-cancel",
    ]
    assert (tmp_path / "out").read_text() == "cancelled=2\n"
    assert "Force-cancelled 2 stuck workflow run(s); 0 failed." in (tmp_path / "summary").read_text()


def test_watchdog_janitor_reports_a_failed_cancel(tmp_path) -> None:
    listings = {"in_progress": [_active(1, hours=100), _active(2, hours=90)]}
    result, posts = _run_janitor_step(tmp_path, listings, failing_id=1)

    assert result.returncode != 0
    assert "Could not force-cancel stuck workflow run 1" in result.stdout
    assert len(posts) == 2
    assert (tmp_path / "out").read_text() == "cancelled=1\n"
    assert "Force-cancelled 1 stuck workflow run(s); 1 failed." in (tmp_path / "summary").read_text()


def test_janitor_listing_projects_the_fields_the_planner_reads() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / ".github/workflows/publication-watchdog.yml").read_text()
    projection = re.search(
        r"actions/runs\?status=\$run_status&per_page=100\" \\\n\s+--jq "
        r"'\{workflow_runs: \[\.workflow_runs\[\] \| \{([a-z_, ]+)\}\]\}'",
        text,
    )
    assert projection
    projected = {field.strip() for field in projection.group(1).split(",")}
    source = inspect.getsource(janitor.select_stuck_runs)
    assert projected == set(re.findall(r'row\.get\("([a-z_]+)"\)', source))
