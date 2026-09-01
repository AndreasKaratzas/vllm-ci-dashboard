from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from vllm import ci_area_regression_watcher as watcher


def test_dashboard_link_targets_active_incident_health_view() -> None:
    assert "ops_health_view=targets" in watcher.DASHBOARD_URL
    assert "ops_health_result=non_passing" in watcher.DASHBOARD_URL
    assert watcher.DASHBOARD_URL.endswith("#ci-health")
    assert "ci-ready" not in watcher.DASHBOARD_URL


class AssignabilityClient:
    def __init__(self, assignable):
        self.assignable = {value.casefold() for value in assignable}

    def is_assignable(self, login):
        return login.casefold() in self.assignable


def _config():
    return {
        "ci_lead": {
            "display_name": "CI Lead",
            "github_login": "ci-lead",
        },
        "owners": [
            {"display_name": "Primary", "github_login": "primary"},
            {"display_name": "CI Lead", "github_login": "ci-lead"},
        ],
    }


def test_watcher_has_no_private_availability_input():
    assert not hasattr(watcher, "_read_availability")
    assert not hasattr(watcher, "MALFORMED_AVAILABILITY")


def _area():
    return {
        "area": "kernels",
        "source_file": "kernels.yaml",
        "owners": [
            {
                "rank": 1,
                "display_name": "Primary",
                "github_login": "primary",
                "availability": "available",
            },
            {
                "rank": 2,
                "display_name": "Secondary",
                "github_login": "secondary",
                "availability": "unavailable",
            },
            {
                "rank": 3,
                "display_name": "Tertiary",
                "github_login": "tertiary",
                "availability": "unknown",
            },
        ],
        "selected_owner": {
            "rank": 1,
            "display_name": "Primary",
            "github_login": "primary",
        },
        "escalated_to_ci_lead": False,
        "counts": {
            "targets": 2,
            "incidents": 2,
            "hard": 1,
            "soft": 1,
            "unobserved": 0,
            "passed": 0,
            "upstream_parity_gaps": 1,
        },
        "regressions": [
            {
                "id": 1,
                "label": "Hard group",
                "result": "hard",
                "raw_result": "hard",
                "incident_severity": "hard",
                "incident_peak_severity": "hard",
                "incident_start_build_id": 11301,
                "build_number": 11301,
                "observed_at": "2026-07-27T23:00:00Z",
                "url": "https://example.invalid/hard",
            },
            {
                "id": 2,
                "label": "Soft group",
                "result": "soft",
                "raw_result": "soft",
                "incident_severity": "soft",
                "incident_peak_severity": "soft",
                "incident_start_build_id": 11301,
                "build_number": 11301,
                "observed_at": "2026-07-27T23:00:00Z",
                "url": "https://example.invalid/soft",
            },
        ],
        "upstream_parity_gaps": [
            {
                "label": "New upstream test",
                "url": "https://example.invalid/parity",
            }
        ],
        "actual_assignee": {
            "display_name": "Primary",
            "github_login": "primary",
        },
        "assignment_reason": "ranked_owner_available_and_assignable",
    }


def test_issue_body_tags_owner_and_assignee_and_ccs_ranked_owners_once():
    body = watcher._issue_body(_area(), "https://example.invalid/run")

    assert "Hard group" in body
    assert "Soft group" in body
    assert "confirmed incidents" in body
    assert "Fix the confirmed incident" in body
    assert "Reduce test-group time to completion" in body
    assert "Restore parity with upstream definitions" in body
    assert "Primary" in body
    assert "| availability |" not in body
    assert "Selected owner and GitHub assignee: @primary" in body
    assert "CC (remaining ranked area owners): @secondary @tertiary" in body
    for login in ("primary", "secondary", "tertiary"):
        assert body.count(f"@{login}") == 1


def test_issue_body_mentions_fallback_assignee_and_sanitizes_untrusted_at_signs():
    area = _area()
    area["actual_assignee"] = {
        "display_name": "CI Lead @not-a-mention",
        "github_login": "ci-lead",
    }
    area["regressions"][0]["label"] = "Hard @outsider group"

    body = watcher._issue_body(area, "https://example.invalid/run")

    assert "- Selected owner: @primary" in body
    assert "- GitHub assignee: @ci-lead" in body
    assert "CC (remaining ranked area owners): @secondary @tertiary" in body
    assert "@not-a-mention" not in body
    assert "@outsider" not in body
    for login in ("primary", "ci-lead", "secondary", "tertiary"):
        assert body.count(f"@{login}") == 1


def test_selected_available_owner_is_used_when_assignable():
    actual, reason = watcher._actual_assignee(
        _area(),
        _config(),
        AssignabilityClient({"primary", "ci-lead"}),
    )

    assert actual["github_login"] == "primary"
    assert reason == "ranked_owner_selected_and_assignable"


def test_unassignable_selected_owner_falls_back_to_ci_lead():
    actual, reason = watcher._actual_assignee(
        _area(),
        _config(),
        AssignabilityClient({"ci-lead"}),
    )

    assert actual["github_login"] == "ci-lead"
    assert reason == "selected_owner_not_assignable_ci_lead"


def test_no_assignable_account_leaves_issue_unassigned():
    actual, reason = watcher._actual_assignee(
        _area(),
        _config(),
        AssignabilityClient(set()),
    )

    assert actual == {}
    assert reason == "no_assignable_owner"
    assert watcher._can_mutate_area(True, actual) is False
    assert watcher._can_mutate_area(False, actual) is True


def test_signal_fingerprint_ignores_routing_and_build_evidence():
    area = _area()
    baseline = watcher._fingerprint(area)
    baseline_content = watcher._content_fingerprint(area)

    area["owners"][1]["github_login"] = "new-secondary"
    assert watcher._fingerprint(area) == baseline
    assert watcher._content_fingerprint(area) != baseline_content
    area = _area()

    area["actual_assignee"] = {
        "display_name": "CI Lead",
        "github_login": "ci-lead",
    }
    assert watcher._fingerprint(area) == baseline

    changed_content = watcher._content_fingerprint(area)
    area["regressions"][0]["build_number"] = 11302
    assert watcher._fingerprint(area) == baseline
    assert watcher._content_fingerprint(area) != changed_content


def test_held_issue_body_and_content_hash_use_the_same_retained_evidence():
    area = _area()
    row = area["regressions"][0]
    row["raw_result"] = "unobserved"
    row["last_failure_evidence"] = {
        "build_number": 11299,
        "observed_at": "2026-07-26T23:00:00Z",
        "url": "https://example.invalid/retained-hard",
    }
    baseline_body = watcher._issue_body(area, "https://example.invalid/run")
    baseline_content = watcher._content_fingerprint(area)

    row.update(
        {
            "build_number": 11302,
            "observed_at": "2026-07-28T23:00:00Z",
            "url": "https://example.invalid/current-unobserved",
        }
    )
    assert watcher._issue_body(area, "https://example.invalid/run") == baseline_body
    assert watcher._content_fingerprint(area) == baseline_content

    row["last_failure_evidence"]["build_number"] = 11300
    assert watcher._issue_body(area, "https://example.invalid/run") != baseline_body
    assert watcher._content_fingerprint(area) != baseline_content


def test_signal_fingerprint_changes_on_membership_or_peak_escalation():
    area = _area()
    baseline = watcher._fingerprint(area)

    area["regressions"][1]["incident_peak_severity"] = "hard"
    assert watcher._fingerprint(area) != baseline

    area = _area()
    area["regressions"].pop()
    assert watcher._fingerprint(area) != baseline


def test_notification_revision_preserves_unchanged_manual_close_suppression():
    area = _area()
    fingerprint = watcher._fingerprint(area)
    legacy_fingerprint = watcher._legacy_fingerprint(area)
    migrated = watcher._migrate_body_schema_state(
        {
            "suppressed": True,
            "suppressed_fingerprint": legacy_fingerprint,
            "last_fingerprint": legacy_fingerprint,
            "body_schema_version": watcher.ISSUE_BODY_SCHEMA_VERSION - 1,
        },
        fingerprint=fingerprint,
        legacy_fingerprint=legacy_fingerprint,
    )

    assert migrated["suppressed"] is True
    assert migrated["suppressed_fingerprint"] == fingerprint
    assert migrated["last_fingerprint"] == fingerprint
    assert migrated["body_schema_version"] == watcher.ISSUE_BODY_SCHEMA_VERSION


def test_fingerprint_migration_keeps_manual_close_suppressed_across_new_build():
    prior_area = _area()
    prior_fingerprint = watcher._legacy_fingerprint(prior_area)
    current_area = _area()
    current_area["regressions"][0]["build_number"] += 1
    current_fingerprint = watcher._fingerprint(current_area)
    migrated = watcher._migrate_body_schema_state(
        {
            "suppressed": True,
            "suppressed_fingerprint": prior_fingerprint,
            "last_fingerprint": prior_fingerprint,
        },
        fingerprint=current_fingerprint,
        legacy_fingerprint=watcher._legacy_fingerprint(current_area),
    )

    assert migrated["suppressed_fingerprint"] == current_fingerprint
    assert migrated["signal_fingerprint_version"] == watcher.SIGNAL_FINGERPRINT_VERSION

    class NoMutationClient:
        def find_open_issue(self, _marker):
            raise AssertionError("suppressed signal must not search for an issue")

        def open_issue(self, _title, _body, _labels, _assignees):
            raise AssertionError("suppressed signal must not reopen an issue")

    reconciled = watcher.reconcile_managed_issue(
        migrated,
        active=True,
        fingerprint=current_fingerprint,
        title="changed regression",
        body="changed evidence",
        ownership_marker="<!-- changed-signal-test:v1 -->",
        recovery_body="recovered",
        observed_at="2026-07-28T00:00:00Z",
        label_specs=[],
        client=NoMutationClient(),
        assignees=["primary"],
    )
    assert reconciled["suppressed"] is True
    assert reconciled["issue"] is None


def test_area_state_round_trip_preserves_transition_and_schema_extensions(
    tmp_path, monkeypatch
):
    state_path = tmp_path / "area-state.json"
    monkeypatch.setattr(watcher, "STATE", state_path)
    signal = {
        "status": "pending_soft",
        "severity": "soft",
        "peak_severity": "soft",
        "soft_streak": 1,
        "last_eligible_build_id": 11301,
        "incident_start_build_id": 11301,
        "confirmed_build_id": None,
        "build_watermark": 11301,
        "evidence": {"build_number": 11301},
        "identity": {"id": "target-1", "label": "Kernels target"},
    }
    area = watcher._state_with_signals(
        {
            "last_fingerprint": "stable-signal",
            "body_schema_version": watcher.ISSUE_BODY_SCHEMA_VERSION,
            "signal_fingerprint_version": watcher.SIGNAL_FINGERPRINT_VERSION,
        },
        {"target-1": signal},
    )

    watcher._checkpoint_state({"kernels": area}, "2026-07-28T12:00:00Z")
    loaded = watcher._read_state()["areas"]["kernels"]

    assert loaded["signals"] == {"target-1": signal}
    assert loaded["body_schema_version"] == watcher.ISSUE_BODY_SCHEMA_VERSION
    assert loaded["signal_fingerprint_version"] == watcher.SIGNAL_FINGERPRINT_VERSION


def test_atomic_state_replace_failure_preserves_previous_file(tmp_path, monkeypatch):
    state_path = tmp_path / "area-state.json"
    original = b'{"areas":{"kernels":{"issue":{"number":123}}}}\n'
    state_path.write_bytes(original)

    def fail_replace(source, destination):
        assert destination == state_path
        assert source.parent == state_path.parent
        raise OSError("simulated replace failure")

    monkeypatch.setattr(watcher.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        watcher._write_json(state_path, {"areas": {}})

    assert state_path.read_bytes() == original
    assert list(tmp_path.glob(f".{state_path.name}.*.tmp")) == []


def test_ownership_status_overflow_preserves_previous_file(tmp_path, monkeypatch):
    status_path = tmp_path / "ci_ownership.json"
    status_path.write_text("existing-ownership")
    monkeypatch.setattr(watcher, "STATUS", status_path)
    monkeypatch.setattr(watcher, "CI_OWNERSHIP_MAX_BYTES", 1)

    with pytest.raises(RuntimeError, match="CI ownership fixed aggregates exceed"):
        watcher._write_json(status_path, {"areas": []})

    assert status_path.read_text() == "existing-ownership"
    assert list(tmp_path.glob(f".{status_path.name}.*.tmp")) == []


def test_ownership_status_compacts_rows_and_keeps_exact_summary() -> None:
    areas = [
        {
            "area": f"area-{index:04d}",
            "counts": {
                "incidents": 1 if index == 499 else 0,
                "pending_soft": 0,
                "upstream_parity_gaps": 0,
            },
            "targets": [{"label": "x" * 2_000}],
            "regressions": [{"label": "x" * 2_000}] if index == 499 else [],
            "pending_soft_observations": [],
            "upstream_parity_gaps": [],
        }
        for index in range(500)
    ]
    source = {
        "schema_version": 1,
        "available": True,
        "summary": {"areas": len(areas), "incidents": 1},
        "areas": areas,
        "unmapped_targets": [],
    }

    bounded = watcher._bounded_ownership_status(source, max_bytes=50_000)

    encoded = (json.dumps(bounded, indent=2, sort_keys=True) + "\n").encode()
    assert len(encoded) <= 50_000
    assert bounded["summary"] == source["summary"]
    retention = bounded["publication_retention"]
    assert retention["aggregate_summary_complete"] is True
    assert retention["area_rows"]["omitted"] > 0
    assert bounded["areas"][0]["area"] == "area-0499"


def test_peak_escalation_clears_manual_close_suppression():
    prior_area = _area()
    prior_fingerprint = watcher._fingerprint(prior_area)
    escalated_area = _area()
    escalated_area["regressions"][1]["incident_peak_severity"] = "hard"
    escalated_fingerprint = watcher._fingerprint(escalated_area)

    class ReopenClient:
        def find_open_issue(self, _marker):
            return None

        def open_issue(self, _title, _body, _labels, _assignees):
            return 456

    reconciled = watcher.reconcile_managed_issue(
        {
            "suppressed": True,
            "suppressed_fingerprint": prior_fingerprint,
            "last_fingerprint": prior_fingerprint,
        },
        active=True,
        fingerprint=escalated_fingerprint,
        content_fingerprint=watcher._content_fingerprint(escalated_area),
        title="escalated incident",
        body="escalated evidence",
        ownership_marker="<!-- escalated-signal-test:v1 -->",
        recovery_body="recovered",
        observed_at="2026-07-28T00:00:00Z",
        label_specs=[],
        client=ReopenClient(),
        assignees=["primary"],
    )

    assert reconciled["suppressed"] is False
    assert reconciled["issue"]["number"] == 456


def test_notification_revision_forces_exactly_one_open_issue_body_update():
    area = _area()
    fingerprint = watcher._fingerprint(area)
    legacy = watcher._migrate_body_schema_state(
        {
            "issue": {"number": 123, "opened_at": "2026-07-27T23:00:00Z"},
            "last_fingerprint": watcher._legacy_fingerprint(area),
        },
        fingerprint=fingerprint,
        legacy_fingerprint=watcher._legacy_fingerprint(area),
    )

    assert legacy["last_fingerprint"] == fingerprint
    assert legacy["last_content_fingerprint"] == ""
    legacy["last_content_fingerprint"] = "refreshed-content"
    current = watcher._migrate_body_schema_state(
        legacy,
        fingerprint=fingerprint,
        legacy_fingerprint=watcher._legacy_fingerprint(area),
    )
    assert current["last_fingerprint"] == fingerprint
    assert current["last_content_fingerprint"] == "refreshed-content"


def _raw_status(result: str, build_number: int) -> dict:
    target = {
        "id": "target-1",
        "label": "Kernels target",
        "result": result,
        "build_number": build_number,
        "observed_at": "2026-07-28T00:00:00Z",
        "url": "https://example.invalid/job",
    }
    return {
        "policy": {},
        "summary": {},
        "areas": [
            {
                "area": "kernels",
                "counts": {
                    "targets": 1,
                    "hard": int(result == "hard"),
                    "soft": int(result == "soft"),
                    "passed": int(result == "passed"),
                    "unobserved": int(result == "unobserved"),
                },
                "targets": [target],
                "regressions": [target] if result in {"hard", "soft"} else [],
            }
        ],
    }


def _prior_with_signals(signals: dict[str, dict]) -> dict:
    return {"kernels": {"signals": signals["kernels"]}}


def test_soft_observation_requires_two_distinct_completed_builds():
    first = _raw_status("soft", 11301)
    first_signals = watcher.apply_incident_hysteresis(first, {})
    first_area = first["areas"][0]

    assert first_area["counts"]["incidents"] == 0
    assert first_area["counts"]["pending_soft"] == 1
    assert first_area["counts"]["raw_results"]["soft"] == 1
    assert sum(
        first_area["counts"][key]
        for key in ("confirmed_hard", "confirmed_soft", "pending_soft")
    ) == first_area["counts"]["targets"]
    assert first_area["targets"][0]["raw_result"] == "soft"
    assert first_area["pending_soft_observations"][0]["soft_streak"] == 1

    duplicate = _raw_status("soft", 11301)
    duplicate_signals = watcher.apply_incident_hysteresis(
        duplicate,
        _prior_with_signals(first_signals),
    )
    assert duplicate["areas"][0]["counts"]["pending_soft"] == 1
    assert duplicate["areas"][0]["pending_soft_observations"][0]["soft_streak"] == 1

    second = _raw_status("soft", 11302)
    watcher.apply_incident_hysteresis(
        second,
        _prior_with_signals(duplicate_signals),
    )
    second_area = second["areas"][0]
    assert second_area["counts"]["incidents"] == 1
    assert second_area["counts"]["confirmed_soft"] == 1
    assert second_area["counts"]["pending_soft"] == 0
    assert second_area["regressions"][0]["incident_classification"] == "new"


def test_older_soft_and_pass_observations_cannot_advance_or_resolve_state():
    first = _raw_status("soft", 11302)
    first_signals = watcher.apply_incident_hysteresis(first, {})

    older_soft = _raw_status("soft", 11301)
    held_pending = watcher.apply_incident_hysteresis(
        older_soft,
        _prior_with_signals(first_signals),
    )
    pending_row = older_soft["areas"][0]["pending_soft_observations"][0]
    assert pending_row["soft_streak"] == 1
    assert pending_row["incident_observation_eligible"] is False
    assert held_pending["kernels"]["target-1"]["build_watermark"] == 11302

    confirmed = _raw_status("soft", 11303)
    confirmed_signals = watcher.apply_incident_hysteresis(
        confirmed,
        _prior_with_signals(held_pending),
    )
    assert confirmed["areas"][0]["counts"]["incidents"] == 1

    older_pass = _raw_status("passed", 11302)
    held_confirmed = watcher.apply_incident_hysteresis(
        older_pass,
        _prior_with_signals(confirmed_signals),
    )
    held_row = older_pass["areas"][0]["regressions"][0]
    assert held_row["raw_result"] == "passed"
    assert held_row["incident_observation_eligible"] is False
    assert held_row["incident_status"] == "confirmed"
    assert watcher._displayed_failure_evidence(held_row)["build_number"] == 11303
    assert "passed (ignored older build)" in watcher._issue_body(
        older_pass["areas"][0] | {
            "source_file": "kernels.yaml",
            "owners": _area()["owners"],
            "selected_owner": _area()["selected_owner"],
            "actual_assignee": _area()["actual_assignee"],
            "assignment_reason": "test",
            "upstream_parity_gaps": [],
        },
        "https://example.invalid/run",
    )
    assert held_confirmed["kernels"]["target-1"]["build_watermark"] == 11303


def test_hard_confirms_immediately_then_soft_recurs_and_pass_resolves():
    hard = _raw_status("hard", 11301)
    hard_signals = watcher.apply_incident_hysteresis(hard, {})
    hard_row = hard["areas"][0]["regressions"][0]
    assert hard_row["incident_severity"] == "hard"
    assert hard_row["incident_peak_severity"] == "hard"

    soft = _raw_status("soft", 11302)
    soft_signals = watcher.apply_incident_hysteresis(
        soft,
        _prior_with_signals(hard_signals),
    )
    soft_row = soft["areas"][0]["regressions"][0]
    assert soft_row["incident_classification"] == "recurring"
    assert soft_row["incident_change"] == "deescalated"
    assert soft_row["incident_severity"] == "soft"
    assert soft_row["incident_peak_severity"] == "hard"

    absent = _raw_status("unobserved", 11303)
    absent_signals = watcher.apply_incident_hysteresis(
        absent,
        _prior_with_signals(soft_signals),
    )
    assert absent["areas"][0]["counts"]["incidents"] == 1
    assert absent["areas"][0]["counts"]["unobserved"] == 1
    assert absent["areas"][0]["counts"]["raw_results"]["unobserved"] == 1
    held_row = absent["areas"][0]["regressions"][0]
    assert held_row["raw_result"] == "unobserved"
    assert held_row["last_failure_evidence"] == {
        "build_number": 11302,
        "observed_at": "2026-07-28T00:00:00Z",
        "url": "https://example.invalid/job",
    }
    assert absent_signals["kernels"]["target-1"]["evidence"] == (
        held_row["last_failure_evidence"]
    )

    passed = _raw_status("passed", 11304)
    watcher.apply_incident_hysteresis(
        passed,
        _prior_with_signals(absent_signals),
    )
    assert passed["areas"][0]["counts"]["incidents"] == 0
    assert passed["areas"][0]["regressions"] == []


def test_missing_targets_hold_pending_and_confirmed_identity_and_evidence():
    pending = _raw_status("soft", 11301)
    pending_signals = watcher.apply_incident_hysteresis(pending, {})
    pending_missing = _raw_status("unobserved", 11302)
    pending_missing["areas"][0]["targets"] = []
    watcher.apply_incident_hysteresis(
        pending_missing,
        _prior_with_signals(pending_signals),
    )
    pending_row = pending_missing["areas"][0]["pending_soft_observations"][0]
    assert pending_row["id"] == "target-1"
    assert pending_row["label"] == "Kernels target"
    assert pending_row["target_disappeared"] is True
    assert pending_row["last_failure_evidence"]["build_number"] == 11301

    confirmed = _raw_status("hard", 11301)
    confirmed_signals = watcher.apply_incident_hysteresis(confirmed, {})
    confirmed_missing = _raw_status("unobserved", 11302)
    confirmed_missing["areas"][0]["targets"] = []
    next_signals = watcher.apply_incident_hysteresis(
        confirmed_missing,
        _prior_with_signals(confirmed_signals),
    )
    area = confirmed_missing["areas"][0]
    held_row = area["regressions"][0]
    assert area["counts"]["incidents"] == 1
    assert area["counts"]["unobserved"] == 1
    assert area["counts"]["targets"] == 1
    assert held_row["id"] == "target-1"
    assert held_row["label"] == "Kernels target"
    assert held_row["last_failure_evidence"]["url"] == "https://example.invalid/job"
    assert next_signals["kernels"]["target-1"]["identity"] == {
        "id": "target-1",
        "label": "Kernels target",
    }


def test_prior_area_missing_from_current_status_keeps_all_signal_state():
    confirmed = _raw_status("hard", 11301)
    confirmed_signals = watcher.apply_incident_hysteresis(confirmed, {})
    prior = {
        "retired-area": {
            "issue": {"number": 123},
            "signals": confirmed_signals["kernels"],
        }
    }
    status = {"policy": {}, "summary": {}, "areas": []}

    next_signals = watcher.apply_incident_hysteresis(status, prior)

    assert next_signals["retired-area"] == prior["retired-area"]["signals"]
    preserved = watcher._preserved_missing_area_states(
        prior,
        set(),
        next_signals,
    )
    assert preserved["retired-area"]["issue"]["number"] == 123
    assert preserved["retired-area"]["signals"] == prior["retired-area"]["signals"]


def test_retirement_streak_advances_only_for_complete_evidence_and_is_bounded():
    prior = {
        "retired-area": {
            "issue": {"number": 123},
            "signals": {"target-1": {"status": "confirmed"}},
            "retirement_streak": 1,
        }
    }
    next_signals = {"retired-area": prior["retired-area"]["signals"]}

    incomplete = watcher._preserved_missing_area_states(
        prior,
        set(),
        next_signals,
        complete_evidence=False,
    )
    assert incomplete["retired-area"]["retirement_streak"] == 1

    complete = watcher._preserved_missing_area_states(
        prior,
        set(),
        next_signals,
        complete_evidence=True,
    )
    assert complete["retired-area"]["retirement_streak"] == 2

    prior["retired-area"]["retirement_streak"] = 999
    bounded = watcher._preserved_missing_area_states(
        prior,
        set(),
        next_signals,
        complete_evidence=True,
    )
    assert (
        bounded["retired-area"]["retirement_streak"]
        == watcher.AREA_RETIREMENT_STREAK_REQUIRED
    )


def test_current_area_clears_prior_retirement_streak():
    current = watcher._current_area_state(
        {"retirement_streak": 2, "issue": {"number": 123}},
        {},
    )
    assert "retirement_streak" not in current
    assert current["issue"]["number"] == 123


def test_complete_area_evidence_requires_every_configured_area_exactly_once():
    config = {"areas": {"kernels": [], "models": []}}
    status = {
        "available": True,
        "areas": [{"area": "kernels"}, {"area": "models"}],
    }
    assert watcher._complete_current_area_keys(status, config) == {
        "kernels",
        "models",
    }

    assert watcher._complete_current_area_keys(
        {**status, "available": False}, config
    ) is None
    assert watcher._complete_current_area_keys(
        {**status, "areas": [{"area": "kernels"}]}, config
    ) is None
    assert watcher._complete_current_area_keys(
        {**status, "areas": [{"area": "kernels"}, {"area": "kernels"}]},
        config,
    ) is None


class _RetirementClient:
    def __init__(self, marker, *, open_numbers=(), states=None, lookup_error=None):
        self.marker = marker
        self.open_numbers = list(open_numbers)
        self.states = dict(states or {})
        self.lookup_error = lookup_error
        self.verified = []
        self.commented = []
        self.closed = []

    def find_open_issues(self, marker):
        assert marker == self.marker
        if self.lookup_error:
            raise self.lookup_error
        return self.open_numbers

    def issue_state(self, number, marker):
        assert marker == self.marker
        self.verified.append(number)
        return self.states.get(number, "open")

    def comment_issue(self, number, body):
        self.commented.append((number, body))
        return True

    def close_issue(self, number):
        self.closed.append(number)
        return True


def test_confirmed_retirement_closes_exact_marker_issues_then_prunes_state():
    area_key = "retired-area"
    marker = f"<!-- {watcher.OWNERSHIP_MARKER_PREFIX}:{area_key}:v1 -->"
    client = _RetirementClient(marker, open_numbers=[124])
    states = {
        area_key: {
            "issue": {"number": 123},
            "retirement_streak": watcher.AREA_RETIREMENT_STREAK_REQUIRED,
        }
    }

    retired = watcher._prune_retired_area_states(
        states,
        client,
        "https://example.invalid/run",
    )

    assert retired == {area_key}
    assert states == {}
    assert client.verified == [123, 124]
    assert client.closed == [123, 124]
    assert all("consecutive complete" in body for _, body in client.commented)


def test_retirement_lookup_or_exact_marker_failure_preserves_state():
    area_key = "retired-area"
    marker = f"<!-- {watcher.OWNERSHIP_MARKER_PREFIX}:{area_key}:v1 -->"
    state = {
        area_key: {
            "issue": {"number": 123},
            "retirement_streak": watcher.AREA_RETIREMENT_STREAK_REQUIRED,
        }
    }
    lookup_failure = _RetirementClient(
        marker,
        lookup_error=RuntimeError("temporary lookup failure"),
    )
    assert watcher._prune_retired_area_states(
        state,
        lookup_failure,
        "https://example.invalid/run",
    ) == set()
    assert area_key in state

    foreign_tracked = _RetirementClient(
        marker,
        open_numbers=[123],
        states={123: "foreign"},
    )
    assert watcher._prune_retired_area_states(
        state,
        foreign_tracked,
        "https://example.invalid/run",
    ) == set()
    assert area_key in state
    assert foreign_tracked.closed == []


def test_legacy_soft_area_issue_is_grandfathered_as_confirmed():
    status = _raw_status("soft", 11301)
    next_signals = watcher.apply_incident_hysteresis(
        status,
        {"kernels": {"issue": {"number": 123}}},
    )

    area = status["areas"][0]
    assert area["counts"]["incidents"] == 1
    assert area["counts"]["pending_soft"] == 0
    assert area["regressions"][0]["incident_classification"] == "recurring"
    assert next_signals["kernels"]["target-1"]["status"] == "confirmed"


def test_matrix_projection_uses_stable_build_timestamp_across_collector_refreshes():
    matrix = {
        "generated_at": "2026-07-28T12:00:00Z",
        "source": {"latest_build_created_at": "2026-07-28T00:00:00Z"},
        "summary": {"latest_build_number": 11301},
        "rows": [
            {
                "id": "target-1",
                "title": "Kernels target",
                "area": "kernels",
                "cells": {
                    "mi300": {
                        "exists": True,
                        "latest_state": "failed",
                        "latest_url": "https://example.invalid/job",
                    }
                },
            }
        ],
    }
    first = watcher.matrix_runtime_targets(matrix)[0]["latest_amd_result"]
    matrix["generated_at"] = "2026-07-28T13:00:00Z"
    second = watcher.matrix_runtime_targets(matrix)[0]["latest_amd_result"]

    assert first["observed_at"] == "2026-07-28T00:00:00Z"
    assert second["observed_at"] == first["observed_at"]
    assert second == first


def _source_documents(now):
    timestamp = watcher.isoformat_z(now)
    commit = "a" * 40
    matrix = {
        "generated_at": timestamp,
        "source": {
            "yaml_url": (
                "https://raw.githubusercontent.com/vllm-project/vllm/"
                f"{commit}/.buildkite/test-amd.yaml"
            ),
            "latest_build_created_at": timestamp,
        },
        "summary": {"definition_rows": 1},
        "rows": [{"id": "row-1"}],
    }
    parity = {"generated_at": timestamp, "source": {"commit_sha": "b" * 40}}
    ownership_parity = {
        "generated_at": timestamp,
        "source": {"commit_sha": commit},
    }
    return matrix, parity, ownership_parity


def test_source_validation_requires_fresh_build_pinned_inputs(monkeypatch):
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    matrix, parity, ownership_parity = _source_documents(now)
    monkeypatch.setattr(watcher, "_source_is_fresh", lambda _now: True)

    assert watcher._source_validation_error(
        now,
        matrix,
        parity,
        ownership_parity,
    ) == ""

    ownership_parity["source"]["commit_sha"] = "c" * 40
    assert watcher._source_validation_error(
        now,
        matrix,
        parity,
        ownership_parity,
    ) == "ownership_parity_commit_mismatch"


def test_source_validation_rejects_stale_matrix_or_nightly(monkeypatch):
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    matrix, parity, ownership_parity = _source_documents(now)
    monkeypatch.setattr(watcher, "_source_is_fresh", lambda _now: True)

    matrix["generated_at"] = "2026-07-28T08:00:00Z"
    assert watcher._source_validation_error(
        now,
        matrix,
        parity,
        ownership_parity,
    ) == "amd_test_matrix_stale"

    matrix["generated_at"] = watcher.isoformat_z(now)
    matrix["source"]["latest_build_created_at"] = "2026-07-26T00:00:00Z"
    assert watcher._source_validation_error(
        now,
        matrix,
        parity,
        ownership_parity,
    ) == "amd_nightly_signal_stale"
