"""Tests for queue-to-canonical publication reconciliation decisions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from vllm import check_site_health as health
from vllm import plan_queue_publication_reconcile as reconcile


NOW = datetime(2026, 8, 29, tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _status(**overrides):
    payload = {
        "schema_version": 1,
        "mode": "current",
        "status": "healthy",
        "generated_at": _iso(NOW - timedelta(minutes=30)),
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


def _queue(
    ts: str | None = None,
    *,
    metrics_observed_at: str | None = None,
    **overrides,
):
    payload = {
        "schema_version": 2,
        "ts": ts or _iso(NOW - timedelta(minutes=30)),
        "pending": [],
        "running": [],
    }
    if metrics_observed_at is not None:
        payload["metrics_observed_at"] = metrics_observed_at
    payload.update(overrides)
    return payload


def _queue_affected_status():
    return _status(
        mode="fallback",
        status="degraded",
        uses_fallback=True,
        affected_surfaces=["Queue health"],
        affected_surface_count=1,
        fallback_surface_count=1,
    )


def _write_canonical(path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _workflow_run(
    target: str,
    *,
    status: str = "pending",
    age_minutes: int = 5,
    started_age_minutes: int | None = None,
    recovery_key: str | None = None,
):
    key = recovery_key or reconcile.queue_reconciliation_key(target)
    return {
        "id": 123,
        "head_branch": "main",
        "status": status,
        "created_at": _iso(NOW - timedelta(minutes=age_minutes)),
        "run_started_at": (
            _iso(NOW - timedelta(minutes=started_age_minutes))
            if started_age_minutes is not None
            else None
        ),
        "updated_at": _iso(NOW - timedelta(minutes=age_minutes)),
        "conclusion": None,
        "display_title": f"Data Collection [recovery:{key}]",
    }


def test_initial_planner_dispatches_only_for_invalid_or_queue_affected_status():
    assert reconcile.reconciliation_reason(_status(), now=NOW) is None
    assert (
        reconcile.reconciliation_reason(_queue_affected_status(), now=NOW)
        == "queue-health-affected"
    )
    assert reconcile.reconciliation_reason({}, now=NOW) == "status-invalid"


@pytest.mark.parametrize(
    "status",
    [
        _status(generated_at=_iso(NOW - timedelta(days=3))),
        _status(
            mode="blocked",
            status="blocked",
            publication_blocked=True,
            generated_at=_iso(NOW - timedelta(days=3)),
        ),
        _status(
            mode="degraded",
            status="degraded",
            affected_surfaces=["DNS health"],
            affected_surface_count=1,
            fresh_degraded_surface_count=1,
        ),
    ],
)
def test_initial_planner_ignores_age_and_unrelated_degradation(status):
    assert reconcile.reconciliation_reason(status, now=NOW) is None


def test_status_contract_exactly_tracks_the_synthetic_health_checker():
    assert reconcile.PUBLICATION_SURFACE_LABELS == health.PUBLICATION_SURFACE_LABELS
    assert reconcile.PUBLICATION_MODES == health.PUBLICATION_MODES
    assert reconcile.PUBLICATION_STATUSES == health.PUBLICATION_STATUSES


@pytest.mark.parametrize(
    ("status", "queue", "target", "expected"),
    [
        (
            _queue_affected_status(),
            _queue(_iso(NOW)),
            _iso(NOW - timedelta(minutes=15)),
            "queue-health-affected",
        ),
        (
            _status(generated_at=_iso(NOW - timedelta(hours=1))),
            _queue(_iso(NOW)),
            _iso(NOW - timedelta(minutes=30)),
            "publication-before-target",
        ),
        (
            _status(generated_at=_iso(NOW)),
            _queue(_iso(NOW - timedelta(hours=1))),
            _iso(NOW - timedelta(minutes=30)),
            "queue-generation-pending",
        ),
        (
            _status(generated_at=_iso(NOW)),
            {"ts": _iso(NOW), "pending": {}, "running": []},
            _iso(NOW - timedelta(minutes=30)),
            "canonical-queue-invalid",
        ),
        (
            {},
            _queue(_iso(NOW)),
            _iso(NOW - timedelta(minutes=30)),
            "status-invalid",
        ),
    ],
)
def test_target_planner_requires_every_exact_queue_postcondition(status, queue, target, expected):
    assert (
        reconcile.target_reconciliation_reason(
            status,
            queue,
            target_queue_generation=target,
            now=NOW,
        )
        == expected
    )


def test_target_planner_skips_an_exact_acknowledged_generation():
    target = _iso(NOW - timedelta(minutes=30))
    assert (
        reconcile.target_reconciliation_reason(
            _status(generated_at=_iso(NOW)),
            _queue(_iso(NOW)),
            target_queue_generation=target,
            now=NOW,
        )
        is None
    )


def test_exact_current_queue_with_unrelated_degradation_is_a_non_recovery_noop():
    target = _iso(NOW - timedelta(minutes=30))
    status = _status(
        generated_at=_iso(NOW),
        mode="fallback",
        status="degraded",
        degraded_since=_iso(NOW - timedelta(hours=1)),
        uses_fallback=True,
        affected_surfaces=["CI core health"],
        affected_surface_count=1,
        fallback_surface_count=1,
    )

    assert (
        reconcile.target_reconciliation_reason(
            status,
            _queue(_iso(NOW)),
            target_queue_generation=target,
            now=NOW,
        )
        is None
    )
    assert (
        reconcile.queue_incident_recovery_eligible(
            status,
            target_queue_generation=target,
            now=NOW,
        )
        is False
    )


def test_exact_healthy_current_queue_status_is_incident_recovery_eligible():
    target = _iso(NOW - timedelta(minutes=30))
    status = _status(generated_at=_iso(NOW))

    assert (
        reconcile.queue_incident_recovery_eligible(
            status,
            target_queue_generation=target,
            now=NOW,
        )
        is True
    )
    assert (
        reconcile.queue_incident_recovery_eligible(
            {**status, "degraded_since": _iso(NOW - timedelta(hours=1))},
            target_queue_generation=target,
            now=NOW,
        )
        is False
    )
    assert (
        reconcile.queue_incident_recovery_eligible(
            {**status, "unexpected": "field"},
            target_queue_generation=target,
            now=NOW,
        )
        is False
    )


def test_queue_incident_recovery_evidence_is_target_bound_and_fresh():
    assert (
        reconcile.queue_incident_recovery_eligible(
            _status(generated_at=_iso(NOW - timedelta(minutes=31))),
            target_queue_generation=_iso(NOW - timedelta(minutes=30)),
            now=NOW,
        )
        is False
    )
    assert (
        reconcile.queue_incident_recovery_eligible(
            _status(generated_at=_iso(NOW - timedelta(hours=4))),
            target_queue_generation=_iso(NOW - timedelta(hours=5)),
            now=NOW,
        )
        is False
    )


def test_queue_reconciliation_key_is_stable_and_generation_scoped():
    target = _iso(NOW - timedelta(minutes=1))

    first = reconcile.queue_reconciliation_key(target)

    assert len(first) == 64
    assert first == reconcile.queue_reconciliation_key(target)
    assert first != reconcile.queue_reconciliation_key(_iso(NOW - timedelta(minutes=2)))


@pytest.mark.parametrize(
    "run_status",
    sorted(reconcile._workflow_runs_contract.QUEUED_RUN_STATUSES),
)
def test_exact_live_queued_reconciliation_suppresses_duplicate_dispatch(run_status):
    target = _iso(NOW - timedelta(minutes=1))
    payload = {"workflow_runs": [_workflow_run(target, status=run_status)]}

    decision = reconcile.reconciliation_dispatch_decision(
        "publication-before-target",
        payload,
        target_queue_generation=target,
        now=NOW,
    )

    assert decision.required is False
    assert decision.reason == "exact-reconciliation-active"
    assert decision.recovery_key == reconcile.queue_reconciliation_key(target)


def test_exact_live_in_progress_reconciliation_suppresses_duplicate_dispatch():
    target = _iso(NOW - timedelta(minutes=1))
    payload = {
        "workflow_runs": [
            _workflow_run(
                target,
                status="in_progress",
                age_minutes=70,
                started_age_minutes=5,
            )
        ]
    }

    decision = reconcile.reconciliation_dispatch_decision(
        "publication-before-target",
        payload,
        target_queue_generation=target,
        now=NOW,
    )

    assert decision.required is False
    assert decision.reason == "exact-reconciliation-active"


def test_unrelated_or_expired_reconciliation_does_not_suppress_target_forever():
    target = _iso(NOW - timedelta(minutes=1))
    payload = {
        "workflow_runs": [
            _workflow_run(target, age_minutes=76),
            _workflow_run(target, recovery_key="1" * 64),
        ]
    }

    decision = reconcile.reconciliation_dispatch_decision(
        "publication-before-target",
        payload,
        target_queue_generation=target,
        now=NOW,
    )

    assert decision.required is True
    assert decision.reason == "publication-before-target"


def test_unavailable_actions_state_fails_open_but_current_target_stays_quiet():
    target = _iso(NOW - timedelta(minutes=1))

    required = reconcile.reconciliation_dispatch_decision(
        "publication-before-target",
        None,
        target_queue_generation=target,
        now=NOW,
    )
    current = reconcile.reconciliation_dispatch_decision(
        None,
        None,
        target_queue_generation=target,
        now=NOW,
    )

    assert required.required is True
    assert required.reason == "actions-state-unavailable"
    assert current.required is False
    assert current.reason == "target-current"


def test_target_planner_uses_metrics_generation_with_retained_older_details():
    target = _iso(NOW - timedelta(minutes=5))
    retained = _queue(
        _iso(NOW - timedelta(hours=1)),
        metrics_observed_at=target,
        details_observed_at=_iso(NOW - timedelta(hours=1)),
        details_status="retained_not_refreshed",
    )
    assert (
        reconcile.target_reconciliation_reason(
            _status(generated_at=_iso(NOW)),
            retained,
            target_queue_generation=target,
            now=NOW,
        )
        is None
    )
    assert reconcile._queue_metrics_generation(retained) == NOW - timedelta(minutes=5)


def test_target_planner_rejects_details_newer_than_metrics_generation():
    payload = _queue(
        _iso(NOW - timedelta(minutes=5)),
        metrics_observed_at=_iso(NOW - timedelta(hours=1)),
    )
    assert (
        reconcile.target_reconciliation_reason(
            _status(generated_at=_iso(NOW)),
            payload,
            target_queue_generation=_iso(NOW - timedelta(hours=2)),
            now=NOW,
        )
        == "canonical-queue-invalid"
    )


def test_legacy_queue_payload_uses_ts_as_its_metrics_generation():
    payload = _queue(_iso(NOW - timedelta(minutes=10)))
    assert reconcile._queue_metrics_generation(payload) == NOW - timedelta(minutes=10)


@pytest.mark.parametrize(
    "target",
    [
        "2026-08-28T23:30:00+00:00",
        "2026-08-28T23:30:00.000000Z",
        "2026-08-28T23:30:00",
        "not-a-time",
        _iso(NOW + timedelta(seconds=1)),
    ],
)
def test_target_generation_must_be_whole_second_canonical_utc_and_not_future(target):
    assert (
        reconcile.target_reconciliation_reason(
            _status(generated_at=_iso(NOW)),
            _queue(_iso(NOW)),
            target_queue_generation=target,
            now=NOW,
        )
        == "target-invalid"
    )


@pytest.mark.parametrize(
    "queue",
    [
        _queue("2026-08-29T00:00:00+00:00"),
        _queue("2026-08-29T00:00:00.000000Z"),
        _queue(_iso(NOW + timedelta(seconds=1))),
        {**_queue(), "metrics_observed_at": None},
        _queue(metrics_observed_at=_iso(NOW + timedelta(seconds=1))),
        _queue(pending={}),
        _queue(running=["not-an-object"]),
        {"ts": _iso(NOW), "pending": []},
    ],
)
def test_canonical_queue_contract_rejects_ambiguous_or_future_payloads(queue):
    assert (
        reconcile.target_reconciliation_reason(
            _status(generated_at=_iso(NOW)),
            queue,
            target_queue_generation=_iso(NOW - timedelta(minutes=1)),
            now=NOW,
        )
        == "canonical-queue-invalid"
    )


def test_loaders_distinguish_unavailable_and_invalid_canonical_queue(tmp_path):
    status = tmp_path / "status.json"
    missing = tmp_path / "missing.json"
    noncanonical = tmp_path / "noncanonical.json"
    duplicate = tmp_path / "duplicate.json"
    oversized = tmp_path / "oversized.json"
    status.write_text(json.dumps(_status(generated_at=_iso(NOW))))
    noncanonical.write_text(json.dumps(_queue(_iso(NOW))))
    duplicate.write_text(
        '{"pending":[],"running":[],"ts":"2026-08-29T00:00:00Z","ts":"2026-08-29T00:00:00Z"}\n'
    )
    oversized.write_bytes(b" " * (reconcile.QUEUE_DATA_MAX_BYTES + 1))
    target = _iso(NOW - timedelta(minutes=1))

    assert (
        reconcile.load_target_reconciliation_reason(
            status,
            missing,
            target_queue_generation=target,
            now=NOW,
        )
        == "canonical-queue-unavailable"
    )
    for invalid in (noncanonical, duplicate, oversized):
        assert (
            reconcile.load_target_reconciliation_reason(
                status,
                invalid,
                target_queue_generation=target,
                now=NOW,
            )
            == "canonical-queue-invalid"
        )


def test_load_target_accepts_exact_bounded_canonical_queue_json(tmp_path):
    status = tmp_path / "status.json"
    queue = tmp_path / "queue.json"
    status.write_text(json.dumps(_status(generated_at=_iso(NOW))))
    _write_canonical(queue, _queue(_iso(NOW)))

    assert (
        reconcile.load_target_reconciliation_reason(
            status,
            queue,
            target_queue_generation=_iso(NOW - timedelta(minutes=1)),
            now=NOW,
        )
        is None
    )


def test_missing_or_malformed_status_is_unavailable_at_the_file_boundary(tmp_path):
    missing = tmp_path / "missing.json"
    malformed = tmp_path / "malformed.json"
    malformed.write_text("not json")
    assert reconcile.load_reconciliation_reason(missing, now=NOW) == "status-unavailable"
    assert reconcile.load_reconciliation_reason(malformed, now=NOW) == ("status-unavailable")


def test_cli_writes_bounded_github_outputs_and_can_enforce_repair(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    output = tmp_path / "github-output"
    status.write_text(json.dumps(_queue_affected_status()))
    monkeypatch.setattr(
        "sys.argv",
        [
            "plan_queue_publication_reconcile.py",
            "--status",
            str(status),
            "--now",
            _iso(NOW),
            "--github-output",
            str(output),
            "--fail-if-required",
        ],
    )

    assert reconcile.main() == 1
    assert output.read_text() == ("required=true\nreason=queue-health-affected\n")


def test_cli_target_mode_reports_an_exact_current_generation(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    queue = tmp_path / "queue.json"
    output = tmp_path / "github-output"
    status.write_text(json.dumps(_status(generated_at=_iso(NOW))))
    _write_canonical(queue, _queue(_iso(NOW)))
    monkeypatch.setattr(
        "sys.argv",
        [
            "plan_queue_publication_reconcile.py",
            "--status",
            str(status),
            "--canonical-queue-data",
            str(queue),
            "--target-queue-generation",
            _iso(NOW - timedelta(minutes=1)),
            "--now",
            _iso(NOW),
            "--github-output",
            str(output),
            "--fail-if-required",
        ],
    )

    assert reconcile.main() == 0
    assert output.read_text() == "required=false\nreason=target-current\n"


def test_cli_suppresses_an_exact_active_reconciliation_dispatch(tmp_path, monkeypatch):
    target = _iso(NOW - timedelta(minutes=5))
    status = tmp_path / "status.json"
    queue = tmp_path / "queue.json"
    runs = tmp_path / "runs.json"
    output = tmp_path / "github-output"
    status.write_text(json.dumps(_status(generated_at=_iso(NOW - timedelta(minutes=10)))))
    _write_canonical(queue, _queue(_iso(NOW)))
    runs.write_text(json.dumps({"workflow_runs": [_workflow_run(target)]}))
    monkeypatch.setattr(
        "sys.argv",
        [
            "plan_queue_publication_reconcile.py",
            "--status",
            str(status),
            "--canonical-queue-data",
            str(queue),
            "--target-queue-generation",
            target,
            "--workflow-runs",
            str(runs),
            "--now",
            _iso(NOW),
            "--github-output",
            str(output),
        ],
    )

    assert reconcile.main() == 0
    assert output.read_text() == (
        "required=true\n"
        "reason=publication-before-target\n"
        "dispatch_required=false\n"
        "dispatch_reason=exact-reconciliation-active\n"
        f"recovery_key={reconcile.queue_reconciliation_key(target)}\n"
    )
