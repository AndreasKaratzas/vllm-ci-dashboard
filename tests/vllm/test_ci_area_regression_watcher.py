from __future__ import annotations

from datetime import datetime, timezone

from vllm import ci_area_regression_watcher as watcher


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
                "build_number": 11301,
                "observed_at": "2026-07-27T23:00:00Z",
                "url": "https://example.invalid/hard",
            },
            {
                "id": 2,
                "label": "Soft group",
                "result": "soft",
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
    assert "Fix the active regression" in body
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


def test_fingerprint_changes_with_assignment_owner_chain_or_runtime_result():
    area = _area()
    baseline = watcher._fingerprint(area)

    area["owners"][1]["github_login"] = "new-secondary"
    assert watcher._fingerprint(area) != baseline
    area = _area()

    area["actual_assignee"] = {
        "display_name": "CI Lead",
        "github_login": "ci-lead",
    }
    assert watcher._fingerprint(area) != baseline

    changed_assignment = watcher._fingerprint(area)
    area["regressions"][0]["build_number"] = 11302
    assert watcher._fingerprint(area) != changed_assignment


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


def test_notification_revision_does_not_mask_changed_suppressed_signal():
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

    assert migrated["suppressed_fingerprint"] == prior_fingerprint
    assert migrated["suppressed_fingerprint"] != current_fingerprint

    class ReopenClient:
        def find_open_issue(self, _marker):
            return None

        def open_issue(self, _title, _body, _labels, _assignees):
            return 456

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

    assert legacy["last_fingerprint"] == ""
    legacy["last_fingerprint"] = fingerprint
    current = watcher._migrate_body_schema_state(
        legacy,
        fingerprint=fingerprint,
        legacy_fingerprint=watcher._legacy_fingerprint(area),
    )
    assert current["last_fingerprint"] == fingerprint


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
