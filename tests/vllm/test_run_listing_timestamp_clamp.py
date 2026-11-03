"""GitHub run listings with future times must not stop the watchdog, janitor or DNS gate.

On 2026-11-01 (US DST fall-back) GitHub stamped runs created during the repeated
hour one hour ahead. Every parser rejected the whole listing and recovery stopped
for about 70 minutes.
"""

from datetime import datetime, timedelta, timezone

import pytest

from vllm import dns_request_budget as budget
from vllm import plan_publication_watchdog as watchdog
from vllm import plan_stuck_run_cancellation as janitor


NOW = datetime(2026, 11, 1, 8, 35, 9, tzinfo=timezone.utc)
CODE_SHA = "a" * 40


def _row(run_id, *, created, updated, status="completed", conclusion="success", title):
    return {
        "id": run_id, "head_branch": "main", "head_sha": CODE_SHA, "status": status,
        "created_at": created, "run_started_at": created, "updated_at": updated,
        "conclusion": conclusion, "display_title": title,
        "path": budget.DNS_WORKFLOW_PATH, "repository": {"full_name": budget.DNS_REPOSITORY},
        "name": "Data Collection",
    }


def _listing(title):
    return {"total_count": 3, "workflow_runs": [
        _row(41289437754, created="2026-11-01T09:34:35Z", updated="2026-11-01T09:34:40Z",
             status="in_progress", conclusion=None, title=title),
        _row(41287983576, created="2026-11-01T09:34:35Z", updated="2026-11-01T09:34:52Z",
             conclusion="failure", title=title),
        _row(41280000000, created="2026-11-01T06:36:02Z", updated="2026-11-01T06:58:44Z",
             title=title),
    ]}


def test_watchdog_counts_a_future_stamped_active_run_as_active() -> None:
    runs = watchdog.parse_workflow_runs(_listing("Data Collection"), now=NOW)
    assert all(run.created_at <= NOW and run.updated_at <= NOW for run in runs)

    stale = {
        "schema_version": 1, "mode": "current", "status": "healthy",
        "generated_at": (NOW - timedelta(minutes=160)).isoformat(), "degraded_since": None,
        "publication_blocked": False, "uses_fallback": False, "affected_surfaces": [],
        "affected_surface_count": 0, "fallback_surface_count": 0,
        "fresh_degraded_surface_count": 0,
    }
    decision = watchdog.watchdog_decision(
        watchdog.observe_publication(stale, now=NOW), runs, now=NOW,
    )
    assert decision.required is False
    assert decision.reason == "collection-active"


def test_janitor_never_cancels_a_future_stamped_run() -> None:
    listing = _listing("Data Collection")
    listing["workflow_runs"] = [row for row in listing["workflow_runs"]
                                if row["status"] == "in_progress"]
    assert janitor.select_stuck_runs([listing], janitor.load_policy(), now=NOW) == []


def test_dns_retry_gate_reads_a_future_stamped_failure_as_just_finished() -> None:
    available = budget.dns_retry_available_at(
        _listing("DNS Health Monitor"), current_code_sha=CODE_SHA, now=NOW,
    )
    assert available == NOW + timedelta(minutes=budget.DNS_FAILED_RETRY_MINUTES, seconds=1)


def test_run_times_beyond_the_skew_bound_are_rejected() -> None:
    listing = _listing("Data Collection")
    listing["workflow_runs"][0]["run_started_at"] = "2026-11-01T11:00:00Z"
    with pytest.raises(ValueError):
        watchdog.parse_workflow_runs(listing, now=NOW)
    listing["workflow_runs"] = listing["workflow_runs"][:1]
    with pytest.raises(ValueError):
        janitor.select_stuck_runs([listing], janitor.load_policy(), now=NOW)
