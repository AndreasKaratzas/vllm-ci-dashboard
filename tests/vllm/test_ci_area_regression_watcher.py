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
    def __init__(self, assignable, unknown=()):
        self.assignable = {value.casefold() for value in assignable}
        self.unknown = {value.casefold() for value in unknown}

    def is_assignable(self, login):
        if login.casefold() in self.unknown:
            return None
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


def test_issue_body_names_owners_without_any_mention():
    body = watcher._issue_body(_area(), "https://example.invalid/run")

    assert "Hard group" in body
    assert "Soft group" in body
    assert "confirmed incidents" in body
    assert "Fix the confirmed incident" in body
    assert "Reduce test-group time to completion" in body
    assert "Restore parity with upstream definitions" in body
    assert "| availability |" not in body
    assert "GitHub assignee: **Primary (`primary`)** (rank 1)." in body
    assert "| 2 | Secondary (`secondary`) |" in body
    assert "| 3 | Tertiary (`tertiary`) |" in body
    assert "CC" not in body
    assert "@" not in body


def test_issue_body_names_fallback_assignee_and_sanitizes_untrusted_at_signs():
    area = _area()
    area["actual_assignee"] = {
        "display_name": "CI Lead @not-a-mention",
        "github_login": "ci-lead",
    }
    area["regressions"][0]["label"] = "Hard @outsider group"

    body = watcher._issue_body(area, "https://example.invalid/run")

    assert "GitHub assignee: **CI Lead &#64;not-a-mention (`ci-lead`)** (rank CI lead)." in body
    assert watcher.GITHUB_MENTION_RE.search(body) is None
    assert "@" not in body


def test_issue_title_and_body_ignore_counts_and_working_hours_selection():
    area = _area()
    title = watcher._issue_title(area)
    body = watcher._issue_body(area, "https://example.invalid/run")
    content = watcher._content_fingerprint(area)
    assert title == "AMD CI confirmed incident [kernels.yaml]"

    area["counts"]["incidents"] = 7
    assert watcher._issue_title(area) == title
    area = _area()
    area["selected_owner"] = {"rank": None, "display_name": "CI Lead", "github_login": "ci-lead"}
    area["escalated_to_ci_lead"] = True
    assert watcher._issue_body(area, "https://example.invalid/run") == body
    assert watcher._content_fingerprint(area) == content


def test_open_issue_keeps_its_ranked_assignee_across_working_hours_changes():
    area = _area()
    kept = {"github_login": "secondary", "reason": "ranked_owner_selected_and_assignable"}
    client = AssignabilityClient({"primary", "secondary", "ci-lead"})
    config = {**_config(), "owners": [*_config()["owners"], {"display_name": "Secondary", "github_login": "secondary"}]}

    actual, reason = watcher._actual_assignee(area, config, client, kept)
    assert (actual["github_login"], reason) == ("secondary", "ranked_owner_selected_and_assignable")

    area["selected_owner"] = {"rank": None, "display_name": "CI Lead", "github_login": "ci-lead"}
    area["escalated_to_ci_lead"] = True
    actual, reason = watcher._actual_assignee(area, config, client, kept)
    assert (actual["github_login"], reason) == ("secondary", "ranked_owner_selected_and_assignable")


def test_open_issue_reassigns_only_when_its_assignee_is_no_longer_a_valid_owner():
    kept = {"github_login": "secondary", "reason": "ranked_owner_selected_and_assignable"}
    actual, reason = watcher._actual_assignee(
        _area(), _config(), AssignabilityClient({"primary", "ci-lead"}), kept
    )
    assert (actual["github_login"], reason) == ("primary", "ranked_owner_selected_and_assignable")

    area = _area()
    area["owners"].pop(1)
    actual, _ = watcher._actual_assignee(
        area, _config(), AssignabilityClient({"primary", "secondary", "ci-lead"}), kept
    )
    assert actual["github_login"] == "primary"


def test_ci_lead_escalation_hands_off_once_to_an_available_ranked_owner():
    kept = {"github_login": "ci-lead", "reason": "ranked_chain_unavailable_ci_lead"}
    client = AssignabilityClient({"primary", "ci-lead"})
    escalated = _area()
    escalated["selected_owner"] = {"rank": None, "display_name": "CI Lead", "github_login": "ci-lead"}
    escalated["escalated_to_ci_lead"] = True

    actual, reason = watcher._actual_assignee(escalated, _config(), client, kept)
    assert (actual["github_login"], reason) == ("ci-lead", "ranked_chain_unavailable_ci_lead")
    actual, reason = watcher._actual_assignee(
        _area(), _config(), AssignabilityClient({"ci-lead"}), kept
    )
    assert (actual["github_login"], reason) == ("ci-lead", "ranked_chain_unavailable_ci_lead")
    actual, reason = watcher._actual_assignee(_area(), _config(), client, kept)
    assert (actual["github_login"], reason) == ("primary", "ranked_owner_selected_and_assignable")


def test_ci_lead_handoff_follows_the_stored_fallback_reason_when_lead_is_ranked():
    area = _area()
    area["owners"][1] = {"rank": 2, "display_name": "CI Lead", "github_login": "ci-lead"}
    client = AssignabilityClient({"primary", "ci-lead"})
    fallback = {"github_login": "ci-lead", "reason": "ranked_chain_unavailable_ci_lead"}

    actual, reason = watcher._actual_assignee(area, _config(), client, fallback)
    assert (actual["github_login"], reason) == ("primary", "ranked_owner_selected_and_assignable")

    ranked = {"github_login": "ci-lead", "reason": "ranked_owner_selected_and_assignable"}
    actual, reason = watcher._actual_assignee(area, _config(), client, ranked)
    assert (actual["github_login"], reason) == ("ci-lead", "ranked_owner_selected_and_assignable")


def test_unknown_assignability_keeps_the_stored_assignee():
    kept = {"github_login": "secondary", "reason": "ranked_owner_selected_and_assignable"}
    client = AssignabilityClient({"primary", "ci-lead"}, unknown={"secondary"})
    config = {**_config(), "owners": [*_config()["owners"], {"display_name": "Secondary", "github_login": "secondary"}]}
    actual, reason = watcher._actual_assignee(_area(), config, client, kept)
    assert (actual["github_login"], reason) == ("secondary", "ranked_owner_selected_and_assignable")

    fallback = {"github_login": "ci-lead", "reason": "ranked_chain_unavailable_ci_lead"}
    client = AssignabilityClient({"ci-lead"}, unknown={"primary"})
    actual, reason = watcher._actual_assignee(_area(), _config(), client, fallback)
    assert (actual["github_login"], reason) == ("ci-lead", "ranked_chain_unavailable_ci_lead")


def test_assignment_is_kept_only_for_an_open_tracked_issue():
    assignment = {"github_login": "secondary", "reason": "kept"}
    assert watcher._kept_assignment({"issue": {"number": 7}, "assignment": assignment}) == assignment
    assert watcher._kept_assignment({"issue": None, "assignment": assignment}) is None
    assert watcher._kept_assignment({"issue": {"number": 7}}) is None
    assert watcher._kept_assignment(None) is None


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


def _cuda_status(builds: dict, prior_signals: dict | None = None) -> tuple[dict, dict]:
    area = _area()
    area["counts"].update(confirmed_hard=1, confirmed_soft=1)
    for row in area["regressions"]:
        row["url"] = f"https://buildkite.com/vllm/ci/builds/11301/steps/canvas?jid=j{row['id']}&tab=output"
    status = {"areas": [area], "summary": {}}
    signals = {"kernels": {"1": {"status": "confirmed"}, "2": {"status": "confirmed"}}}
    prior = {"kernels": {"signals": prior_signals}} if prior_signals else {}
    watcher._apply_cuda_evidence(status, signals, prior, builds)
    return status, signals


def test_incidents_that_failed_on_cuda_or_build_wide_are_warnings_not_regressions():
    shared = {"kind": "shared_with_cuda", "build_number": 11301, "cuda_step_key": "kernels-hard"}
    status, signals = _cuda_status(
        {"11301": {"build_wide": False, "mirror_failed_jobs": {"j1": "kernels-hard"}}}
    )
    area = status["areas"][0]
    assert [row.get("cuda_evidence") for row in area["regressions"]] == [shared, None]
    assert signals["kernels"]["1"]["cuda_evidence"] == shared
    assert {key: area["counts"][key] for key in ("incidents", "confirmed_hard", "confirmed_soft")} == {
        "incidents": 1, "confirmed_hard": 0, "confirmed_soft": 1,
    }
    assert status["summary"] == {
        "areas_with_incidents": 1, "incidents": 1, "confirmed_hard": 0, "confirmed_soft": 1,
        "shared_incidents": 1,
    }
    assert watcher._issue_title(area) == "AMD CI confirmed incident [kernels.yaml]"
    assert "| warning (hard): CUDA step kernels-hard also failed in build #11301 |" in (
        watcher._issue_body(area, "https://example.invalid/run")
    )

    wide = {"build_wide": True, "jobs": 61, "failed": 56, "amd_jobs": 63, "amd_failed": 54}
    status, _ = _cuda_status({"11301": wide})
    area = status["areas"][0]
    assert (area["counts"]["incidents"], status["summary"]["areas_with_incidents"]) == (0, 0)
    assert "not AMD-specific (Hard group: build #11301 broke build-wide: 56 of 61 CUDA and 54 of 63" in (
        watcher._recovery_body(area, "https://example.invalid/run")
    )


def test_cuda_evidence_outlives_a_missing_handoff_only_for_its_own_build():
    shared = {"kind": "shared_with_cuda", "build_number": 11301, "cuda_step_key": "kernels-hard"}
    status, _ = _cuda_status({}, {"1": {"cuda_evidence": shared}, "2": {"cuda_evidence": {**shared, "build_number": 11300}}})
    assert [row.get("cuda_evidence") for row in status["areas"][0]["regressions"]] == [shared, None]
    status, _ = _cuda_status({"11301": {"build_wide": False, "mirror_failed_jobs": {}}}, {"1": {"cuda_evidence": shared}})
    assert status["summary"]["shared_incidents"] == 0


def test_cuda_shared_incident_does_not_reopen_a_manually_closed_issue():
    closed = _area()
    closed["regressions"].pop()
    suppressed = watcher._fingerprint(closed)
    status, _ = _cuda_status({"11301": {"build_wide": False, "mirror_failed_jobs": {"j2": "kernels-soft"}}})
    area = status["areas"][0]
    assert area["regressions"][1]["cuda_evidence"] and area["counts"]["incidents"] == 1
    assert watcher._fingerprint(area) == suppressed

    class NoMutationClient:
        def open_issue(self, *_args):
            raise AssertionError("a CUDA-shared incident must not reopen a manual close")

    reconciled = watcher.reconcile_managed_issue(
        {"suppressed": True, "suppressed_fingerprint": suppressed, "last_fingerprint": suppressed},
        active=True,
        fingerprint=watcher._fingerprint(area),
        title="t",
        body="b",
        ownership_marker="<!-- shared-suppression-test:v1 -->",
        recovery_body="r",
        observed_at="2026-07-28T00:00:00Z",
        label_specs=[],
        client=NoMutationClient(),
        assignees=["primary"],
    )
    assert reconciled["suppressed"] is True and reconciled["issue"] is None


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


def test_kept_assignment_survives_state_round_trip_and_budget_compaction(tmp_path, monkeypatch):
    from vllm.ci import watcher_state

    monkeypatch.setattr(watcher, "STATE", tmp_path / "area-state.json")
    assignment = {"github_login": "secondary", "reason": "ranked_owner_selected_and_assignable"}
    area = watcher._state_with_signals(
        {"issue": {"number": 766, "opened_at": "2026-10-21T08:55:06Z"}, "assignment": assignment}, {}
    )
    watcher._checkpoint_state({"engine": area}, "2026-10-22T00:00:00Z")
    loaded = watcher._read_state()["areas"]["engine"]
    assert watcher._kept_assignment(loaded) == assignment

    compact, _ = watcher_state._compact_area_state({"areas": {"engine": loaded}})
    assert compact["areas"]["engine"]["assignment"] == assignment


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


def _row_status(build_number: int, *rows: tuple[str, ...]) -> dict:
    status = _raw_status("unobserved", build_number)
    status["areas"][0]["targets"] = [
        {
            "id": f"matrix-row-{title}",
            "label": title,
            "result": result,
            "build_number": build_number,
            "observed_at": "2026-10-20T06:00:00Z",
            "url": f"https://example.invalid/{build_number}",
            "lineage": {
                "architectures": list(architectures or ["mi300"]),
                "definition_ids": [f"lora.yaml#{step_key}"],
                "command_fingerprint": f"commands-{step_key}",
            },
        }
        for title, step_key, result, *architectures in rows
    ]
    return status


@pytest.mark.parametrize(
    ("result", "status"),
    [("passed", "clear"), ("hard", "confirmed")],
)
def test_renamed_row_carries_its_incident_to_the_row_with_its_definition(
    result, status
):
    failing = _row_status(95663, ("LoRA TP (Distributed)", "lora-tp", "hard"))
    signals = watcher.apply_incident_hysteresis(failing, {})
    # The held run advances the watermark to a build where the old row is absent.
    held = _row_status(95829, ("LoRA", "lora", "passed"))
    signals = watcher.apply_incident_hysteresis(held, _prior_with_signals(signals))
    held_fingerprint = watcher._fingerprint(held["areas"][0])

    renamed = _row_status(
        96078,
        ("LoRA TP (Distributed) Shard", "lora-tp", result),
        ("LoRA", "lora", "passed"),
    )
    next_signals = watcher.apply_incident_hysteresis(
        renamed,
        _prior_with_signals(signals),
    )

    area = renamed["areas"][0]
    assert "matrix-row-LoRA TP (Distributed)" not in next_signals["kernels"]
    signal = next_signals["kernels"]["matrix-row-LoRA TP (Distributed) Shard"]
    assert signal["status"] == status
    assert [row["label"] for row in area["regressions"]] == (
        ["LoRA TP (Distributed) Shard"] if status == "confirmed" else []
    )
    if status == "confirmed":
        assert signal["incident_start_build_id"] == 95663
        assert signal["identity"]["label"] == "LoRA TP (Distributed) Shard"
        # A manual close of the issue stays suppressed across the rename.
        assert watcher._fingerprint(area) == held_fingerprint


@pytest.mark.parametrize(
    "current",
    [
        (("Draft A", "draft", "unobserved"),),
        (("Draft", "draft", "unobserved"),),
    ],
    ids=["existing-pending-sibling", "merged-successor"],
)
def test_vanished_hard_incident_is_never_shadowed_by_a_weaker_lineage_signal(
    current,
):
    failing = _row_status(
        95663,
        ("Draft A", "draft", "soft"),
        ("Draft B", "draft", "hard"),
    )
    signals = watcher.apply_incident_hysteresis(failing, {})

    unobserved = _row_status(95829, *current)
    signals = watcher.apply_incident_hysteresis(
        unobserved,
        _prior_with_signals(signals),
    )

    area = unobserved["areas"][0]
    assert area["counts"]["incidents"] == 1
    assert area["retired_incidents"] == []
    assert [row["incident_start_build_id"] for row in area["regressions"]] == [95663]


def test_confirmed_hard_incident_claims_a_merged_successor_before_confirmed_soft():
    rows = (("Draft A", "draft", "soft"), ("Draft B", "draft", "hard"))
    signals = watcher.apply_incident_hysteresis(_row_status(95663, *rows), {})
    signals = watcher.apply_incident_hysteresis(
        _row_status(95700, *rows),
        _prior_with_signals(signals),
    )
    assert signals["kernels"]["matrix-row-Draft A"]["status"] == "confirmed"

    merged = _row_status(95829, ("Draft", "draft", "unobserved"))
    signals = watcher.apply_incident_hysteresis(merged, _prior_with_signals(signals))

    assert signals["kernels"]["matrix-row-Draft"]["severity"] == "hard"


def test_absence_counts_only_builds_that_ran_the_signal_architecture():
    signals = watcher.apply_incident_hysteresis(
        _row_status(95663, ("Old", "old", "hard"), ("New", "new", "passed", "mi355")),
        {},
    )
    for build_number in (95700, 95800, 95900):
        mi355_only = _row_status(build_number, ("New", "new", "passed", "mi355"))
        signals = watcher.apply_incident_hysteresis(
            mi355_only,
            _prior_with_signals(signals),
        )

    area = mi355_only["areas"][0]
    assert area["retired_incidents"] == []
    assert [row["label"] for row in area["regressions"]] == ["Old"]
    assert "absent_streak" not in signals["kernels"]["matrix-row-Old"]


def test_absent_row_retires_after_its_area_runs_without_it_in_newer_builds():
    signals = watcher.apply_incident_hysteresis(_raw_status("hard", 95663), {})

    for build_number in (96078, 96078, 96247):
        area_ran = _row_status(build_number, ("LoRA", "lora", "passed"))
        signals = watcher.apply_incident_hysteresis(
            area_ran,
            _prior_with_signals(signals),
        )
        assert area_ran["areas"][0]["counts"]["incidents"] == 1
    not_run = _row_status(96300, ("LoRA", "lora", "unobserved"))
    signals = watcher.apply_incident_hysteresis(not_run, _prior_with_signals(signals))
    assert signals["kernels"]["target-1"]["absent_streak"] == 2

    retiring = _row_status(96400, ("LoRA", "lora", "passed"))
    signals = watcher.apply_incident_hysteresis(retiring, _prior_with_signals(signals))

    area = retiring["areas"][0]
    assert signals["kernels"]["target-1"]["status"] == "clear"
    assert area["counts"]["incidents"] == 0
    assert [row["id"] for row in area["retired_incidents"]] == ["target-1"]

    # A run that persists the retirement without closing the issue must not
    # lose it from the later closing comment.
    later = _row_status(96500, ("LoRA", "lora", "passed"))
    watcher.apply_incident_hysteresis(later, _prior_with_signals(signals))
    assert "(retired: Kernels target)" in watcher._recovery_body(
        later["areas"][0] | {"source_file": "kernels.yaml"},
        "https://example.invalid/run",
    )


def test_open_issue_body_reports_a_retired_incident_while_others_stay_open():
    signals = watcher.apply_incident_hysteresis(
        _row_status(95663, ("Old", "old", "hard"), ("Other", "other", "hard")),
        {},
    )
    for build_number in (95700, 95800, 95900, 96000):
        current = _row_status(build_number, ("Other", "other", "hard"))
        signals = watcher.apply_incident_hysteresis(
            current,
            _prior_with_signals(signals),
        )

    area = current["areas"][0]
    assert [row["label"] for row in area["regressions"]] == ["Other"]
    assert [row["label"] for row in area["retired_incidents"]] == ["Old"]
    body = watcher._issue_body(
        _area()
        | {
            "regressions": area["regressions"],
            "retired_incidents": area["retired_incidents"],
        },
        "https://example.invalid/run",
    )
    assert "### Retired target groups" in body
    assert "incidents were retired: Old." in body


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


def test_complete_area_evidence_includes_source_derived_areas_without_hiding_omissions():
    config = {"areas": {"kernels": []}}
    status = {
        "available": True,
        "sources": {"definition_areas": ["new_connector"]},
        "areas": [{"area": "kernels"}, {"area": "new_connector"}],
    }

    assert watcher._complete_current_area_keys(status, config) == {
        "kernels", "new_connector"
    }
    assert watcher._complete_current_area_keys({**status, "sources": {}}, config) is None
    for missing_area in ("kernels", "new_connector"):
        assert watcher._complete_current_area_keys(
            {
                **status,
                "areas": [row for row in status["areas"] if row["area"] != missing_area],
            },
            config,
        ) is None
    for invalid_inventory in ("new_connector", [""], ["new_connector", "new_connector"]):
        assert watcher._complete_current_area_keys(
            {**status, "sources": {"definition_areas": invalid_inventory}}, config
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
            "pipeline": "ci", "commit_sha": commit, "runtime_source_commit_sha": commit,
            "hardware_scope": "amd_mi_gpu",
            "yaml_url": (
                "https://raw.githubusercontent.com/vllm-project/vllm/"
                f"{commit}/.buildkite/ci_config.yaml"
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


def test_area_watcher_accepts_producer_source_scope_without_a_root_marker(monkeypatch):
    now = datetime(2026, 10, 9, 20, 38, tzinfo=timezone.utc)
    matrix, parity, ownership = _source_documents(now)
    monkeypatch.setattr(watcher, "_source_is_fresh", lambda _: True)
    assert "hardware_scope" not in matrix
    assert matrix["source"]["hardware_scope"] == "amd_mi_gpu"
    assert watcher._matrix_commit(matrix) == matrix["source"]["runtime_source_commit_sha"]
    assert watcher._source_validation_error(now, matrix, parity, ownership) == ""


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


@pytest.mark.parametrize("change", [
    "legacy_pipeline", "unmarked", "root_marker_only", "wrong_source_scope",
    "foreign_pin", "foreign_definition_pin", "short_pin", "legacy_source",
])
def test_area_alerts_refuse_unproved_or_foreign_runtime_matrix(monkeypatch, change):
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    matrix, parity, ownership = _source_documents(now)
    monkeypatch.setattr(watcher, "_source_is_fresh", lambda _: True)
    if change == "legacy_pipeline":
        matrix["source"]["pipeline"] = "amd-ci"
    elif change == "unmarked":
        del matrix["source"]["hardware_scope"]
    elif change == "root_marker_only":
        matrix["hardware_scope"] = matrix["source"].pop("hardware_scope")
    elif change == "wrong_source_scope":
        matrix["hardware_scope"] = "amd_mi_gpu"
        matrix["source"]["hardware_scope"] = "all_gpu"
    elif change == "foreign_pin":
        matrix["source"]["runtime_source_commit_sha"] = "b" * 40
    elif change == "foreign_definition_pin":
        matrix["source"]["commit_sha"] = "b" * 40
    elif change == "short_pin":
        matrix["source"]["commit_sha"] = matrix["source"]["runtime_source_commit_sha"] = "a" * 12
    else:
        matrix["source"]["yaml_url"] = matrix["source"]["yaml_url"].replace("ci_config.yaml", "test-amd.yaml")
    assert watcher._source_validation_error(now, matrix, parity, ownership) == "ownership_parity_commit_mismatch"


@pytest.mark.parametrize("source", [None, [], ["invalid"], "invalid"])
def test_matrix_scope_guard_rejects_malformed_source_records(source):
    assert watcher._matrix_commit({"source": source, "hardware_scope": "amd_mi_gpu"}) == ""



def test_area_history_migration_keeps_owned_issue_and_current_mi_signals_only():
    prior = {"schema_version": 1, "areas": {"kernels": {"issue_number": 540, "signals": {
        "mi-gpu": {"status": "confirmed"}, "cpu-on-mi": {"status": "confirmed"},
        "cuda": {"status": "confirmed"},
    }}}}
    migrated = watcher._mi_scoped_area_history(prior, [{"id": "mi-gpu"}])
    assert migrated["hardware_scope"] == "amd_mi_gpu"
    assert migrated["areas"]["kernels"]["issue_number"] == 540
    assert set(migrated["areas"]["kernels"]["signals"]) == {"mi-gpu"}
    assert set(prior["areas"]["kernels"]["signals"]) == {"mi-gpu", "cpu-on-mi", "cuda"}


def test_run_keeps_the_open_issue_assignee_and_body_when_working_hours_change(tmp_path, monkeypatch):
    calls = []

    def reconcile(state, **kwargs):
        calls.append(kwargs)
        return {**state, "issue": {"number": 766, "opened_at": "2026-10-21T08:55:06Z"}}

    selected = [_area()["selected_owner"]]

    def status(*_args, **_kwargs):
        area = _area()
        area["selected_owner"] = selected[0]
        return {"available": True, "areas": [area], "summary": {"incidents": 2}}

    config = {**_config(), "owners": [*_config()["owners"], {"display_name": "Secondary", "github_login": "secondary"}]}
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", watcher.DASHBOARD_REPO)
    monkeypatch.setattr(watcher, "STATE", tmp_path / "area-state.json")
    monkeypatch.setattr(watcher, "STATUS", tmp_path / "ci_ownership.json")
    monkeypatch.setattr(watcher, "_source_validation_error", lambda *_: "")
    monkeypatch.setattr(watcher, "matrix_runtime_targets", lambda _: [{"id": 1}])
    monkeypatch.setattr(watcher, "load_ownership_config", lambda _: config)
    monkeypatch.setattr(watcher, "evaluate_availability", lambda *_, **__: ({}, {}))
    monkeypatch.setattr(watcher, "build_ownership_status", status)
    monkeypatch.setattr(watcher, "_complete_current_area_keys", lambda *_: {"kernels"})
    monkeypatch.setattr(watcher, "apply_incident_hysteresis", lambda *_: {"kernels": {}})
    monkeypatch.setattr(watcher, "_apply_cuda_evidence", lambda *_: None)
    monkeypatch.setattr(watcher, "GitHubIssueClient", lambda *_: AssignabilityClient({"primary", "secondary", "ci-lead"}))
    monkeypatch.setattr(watcher, "reconcile_managed_issue", reconcile)
    monkeypatch.setattr(watcher, "_prune_retired_area_states", lambda *_: set())

    assert watcher.run() == 0
    selected[0] = {"rank": 2, "display_name": "Secondary", "github_login": "secondary"}
    assert watcher.run() == 0

    assert [call["assignees"] for call in calls] == [["primary"], ["primary"]]
    assert calls[0]["body"] == calls[1]["body"]
    assert calls[0]["content_fingerprint"] == calls[1]["content_fingerprint"]
    assert watcher._read_state()["areas"]["kernels"]["assignment"]["github_login"] == "primary"
