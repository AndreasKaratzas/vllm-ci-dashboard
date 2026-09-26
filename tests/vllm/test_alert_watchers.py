from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from vllm import agent_health_issue_watcher as agent
from vllm import amd_duration_regression_watcher as duration
from vllm import amd_main_failure_watcher as amd
from vllm import ci_main_failure_watcher as upstream
from vllm.ci.managed_issue import reconcile_managed_issue, validate_target_repo
from vllm.ci.reliability_history import (
    LEGACY_OBSERVATION_DERIVED_FIELDS,
    build_all_main_reliability,
    hydrate_reliability_observations,
    validate_all_main_reliability,
)


class FakeIssueClient:
    def __init__(self):
        self.next_number = 100
        self.states = {}
        self.opened = []
        self.updated = []
        self.assigned = []
        self.comments = []
        self.closed = []

    def issue_state(self, number, ownership_marker):
        return self.states.get(number, "open")

    def open_issue(self, title, body, label_specs):
        self.next_number += 1
        number = self.next_number
        self.states[number] = "open"
        self.opened.append((number, title, body, label_specs))
        return number

    def update_issue(self, number, title, body):
        self.updated.append((number, title, body))
        return True

    def ensure_owner_assigned(self, number):
        self.assigned.append(number)
        return True

    def comment_issue(self, number, body):
        self.comments.append((number, body))
        return True

    def close_issue(self, number):
        self.states[number] = "closed"
        self.closed.append(number)
        return True


def _reconcile(state, client, *, active, fingerprint="fp"):
    return reconcile_managed_issue(
        state,
        active=active,
        fingerprint=fingerprint,
        title="alert title",
        body="alert body",
        ownership_marker="<!-- test-managed-alert:v1 -->",
        recovery_body="recovered",
        observed_at="2026-07-17T12:00:00Z",
        label_specs=[("automated", "123456", "test")],
        client=client,
    )


def test_managed_issue_owns_one_issue_and_respects_manual_close():
    client = FakeIssueClient()

    opened = _reconcile({}, client, active=True)
    number = opened["issue"]["number"]
    assert number == 101
    assert len(client.opened) == 1
    assert "<!-- test-managed-alert:v1 -->" in client.opened[0][2]

    unchanged = _reconcile(opened, client, active=True)
    assert client.updated == []
    assert unchanged["issue"]["number"] == number

    client.states[number] = "closed"
    suppressed = _reconcile(unchanged, client, active=True, fingerprint="fp")
    assert suppressed["issue"] is None
    assert suppressed["suppressed"] is True
    assert suppressed["suppressed_fingerprint"] == "fp"
    assert len(client.opened) == 1

    still_suppressed = _reconcile(suppressed, client, active=True, fingerprint="fp")
    assert still_suppressed["issue"] is None
    assert len(client.opened) == 1

    reopened = _reconcile(still_suppressed, client, active=True, fingerprint="later")
    assert reopened["issue"]["number"] == 102
    assert reopened["suppressed"] is False
    assert reopened["suppressed_fingerprint"] == ""


def test_managed_issue_closes_only_the_tracked_issue_on_recovery():
    client = FakeIssueClient()
    opened = _reconcile({}, client, active=True)
    number = opened["issue"]["number"]

    healthy = _reconcile(opened, client, active=False, fingerprint="")

    assert client.comments == [(number, "recovered")]
    assert client.closed == [number]
    assert healthy["issue"] is None


def test_managed_issue_refuses_to_touch_a_foreign_tracked_number():
    client = FakeIssueClient()
    client.states[48510] = "foreign"
    state = {
        "issue": {"number": 48510, "opened_at": "2026-07-17T10:00:00Z"},
        "last_fingerprint": "old",
    }

    preserved = _reconcile(state, client, active=False, fingerprint="")

    assert preserved["issue"]["number"] == 48510
    assert client.updated == []
    assert client.comments == []
    assert client.closed == []


def test_managed_issue_restricts_target_repository():
    validate_target_repo("AndreasKaratzas/vllm-ci-dashboard")

    try:
        validate_target_repo("somebody/another-repo")
    except RuntimeError as error:
        assert "restricted" in str(error)
    else:
        raise AssertionError("unexpected repository was accepted")


def _amd_build(number, finished_at):
    return {
        "number": number,
        "finished_at": finished_at,
        "created_at": finished_at,
    }


def _amd_observation(number, result, observed_at, job_id, retried_in=""):
    build_url = f"https://buildkite.com/vllm/amd-ci/builds/{number}"
    row = {
        "source_pipeline": "amd-ci",
        "build_number": number,
        "build_url": build_url,
        "job_id": job_id,
        "job_url": f"{build_url}/steps/canvas?jid={job_id}&tab=output",
        "observed_at": observed_at,
        "started_at": observed_at,
        "finished_at": observed_at,
        "result": result,
        "eligible_for_reliability": True,
    }
    if retried_in:
        row["retry_evidence"] = {"retried_in_job_id": retried_in}
    return row


def _amd_group(group_id, observations, name="AMD group"):
    return {
        "group_id": group_id,
        "name": name,
        "raw_name": f"mi300_1: {name}",
        "step_key": group_id,
        "hardware": "mi300",
        "queue": "amd_mi300_1",
        "observations": observations,
    }


def _amd_reliability(builds, groups, generated_at="2026-07-17T12:10:00Z"):
    return {
        "generated_at": generated_at,
        "builds": builds,
        "groups": groups,
    }


def _raw_amd_watcher_build(
    number,
    created_at,
    *,
    result="passed",
    wall_completion_mins=20,
    message="normalized watcher evidence",
):
    runnable_at = created_at + timedelta(minutes=1)
    started_at = created_at + timedelta(minutes=5)
    finished_at = started_at + timedelta(minutes=wall_completion_mins)

    def timestamp(value):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")

    failed = result in {"failed", "soft_fail"}
    return {
        "number": number,
        "branch": "main",
        "state": "failed" if failed else "passed",
        "commit": f"{number:040x}",
        "message": message,
        "created_at": timestamp(created_at),
        "started_at": timestamp(runnable_at),
        "finished_at": timestamp(finished_at + timedelta(minutes=1)),
        "web_url": f"https://buildkite.com/vllm/amd-ci/builds/{number}",
        "jobs": [
            {
                "id": f"normalized-job-{number}",
                "type": "script",
                "name": "mi300_1: Normalized watcher group",
                "state": "failed" if failed else "passed",
                "soft_failed": result == "soft_fail",
                "runnable_at": timestamp(runnable_at),
                "started_at": timestamp(started_at),
                "finished_at": timestamp(finished_at),
                "agent_query_rules": ["queue=amd_mi300_1"],
                "step": {
                    "id": f"normalized-step-{number}",
                    "key": "normalized-watcher-step",
                },
            }
        ],
    }


def _normalized_watcher_reliability(builds):
    return build_all_main_reliability(
        builds,
        pipeline_slug="amd-ci",
        window_days=30,
        generated_at="2026-07-17T12:00:00Z",
    )


def _legacy_watcher_reliability(normalized):
    legacy = copy.deepcopy(normalized)
    legacy["schema_version"] = 1
    for normalized_group, legacy_group in zip(
        normalized["groups"], legacy["groups"], strict=True
    ):
        legacy_group["observations"] = hydrate_reliability_observations(
            normalized,
            normalized_group["observations"],
            pipeline_slug="amd-ci",
        )
    return legacy


def test_amd_watcher_schema_v2_matches_legacy_hydrated_evidence_and_links():
    message = "Merge queue: preserve normalized watcher popup evidence"
    normalized = _normalized_watcher_reliability(
        [
            _raw_amd_watcher_build(
                610,
                datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc),
                result="failed",
                message=message,
            )
        ]
    )
    legacy = _legacy_watcher_reliability(normalized)

    assert validate_all_main_reliability(normalized, "amd-ci")
    assert validate_all_main_reliability(legacy, "amd-ci")
    stored = normalized["groups"][0]["observations"][0]
    assert not (set(LEGACY_OBSERVATION_DERIVED_FIELDS) & stored.keys())

    normalized_evidence = amd._observations_by_build(normalized)
    legacy_evidence = amd._observations_by_build(legacy)
    assert normalized_evidence == legacy_evidence

    normalized_state = amd.advance_incidents(normalized, amd._default_state())
    legacy_state = amd.advance_incidents(legacy, amd._default_state())
    assert normalized_state == legacy_state

    incident = next(iter(normalized_state["active"].values()))
    build_url = "https://buildkite.com/vllm/amd-ci/builds/610"
    job_url = f"{build_url}/steps/canvas?jid=normalized-job-610&tab=output"
    assert incident["build_url"] == build_url
    assert incident["job_url"] == job_url
    assert incident["build_commit"] == f"{610:040x}"
    assert incident["build_message"] == message

    normalized_body = amd._issue_body(
        normalized_state["active"],
        normalized,
        "https://github.com/run",
        "AndreasKaratzas",
    )
    legacy_body = amd._issue_body(
        legacy_state["active"],
        legacy,
        "https://github.com/run",
        "AndreasKaratzas",
    )
    assert normalized_body == legacy_body
    assert f"[Normalized watcher group]({job_url})" in normalized_body
    assert f"[#610]({build_url})" in normalized_body


def test_amd_watcher_initializes_from_latest_build_then_resolves_on_pass():
    reliability = _amd_reliability(
        [
            _amd_build(10, "2026-07-17T10:00:00Z"),
            _amd_build(11, "2026-07-17T11:00:00Z"),
        ],
        [
            _amd_group("old", [_amd_observation(10, "failed", "2026-07-17T09:50:00Z", "old-fail")]),
            _amd_group(
                "current",
                [_amd_observation(11, "soft_fail", "2026-07-17T10:50:00Z", "current-soft")],
            ),
        ],
    )

    initialized = amd.advance_incidents(reliability, amd._default_state())

    assert set(initialized["processed_build_numbers"]) == {10, 11}
    assert initialized["schema_version"] == 2
    assert initialized["active"] == {}
    assert set(initialized["pending_soft"]) == {"current"}
    assert initialized["pending_soft"]["current"]["transition"]["soft_streak"] == 1

    reliability["builds"].append(_amd_build(12, "2026-07-17T12:00:00Z"))
    reliability["groups"][1]["observations"].insert(
        0,
        _amd_observation(12, "soft_fail", "2026-07-17T11:50:00Z", "current-soft-2"),
    )
    confirmed = amd.advance_incidents(reliability, initialized)

    assert set(confirmed["active"]) == {"current"}
    transition = confirmed["active"]["current"]["transition"]
    assert transition["status"] == "confirmed"
    assert transition["soft_streak"] == 2
    assert confirmed["pending_soft"] == {}

    reliability["builds"].append(_amd_build(13, "2026-07-17T13:00:00Z"))
    reliability["groups"][1]["observations"].insert(
        0,
        _amd_observation(13, "passed", "2026-07-17T12:50:00Z", "current-pass"),
    )
    resolved = amd.advance_incidents(reliability, confirmed)

    assert resolved["active"] == {}
    assert resolved["pending_soft"] == {}
    assert set(resolved["processed_build_numbers"]) == {10, 11, 12, 13}


def test_amd_watcher_latest_retry_attempt_wins_inside_build():
    reliability = _amd_reliability(
        [_amd_build(20, "2026-07-17T12:00:00Z")],
        [
            _amd_group(
                "retried",
                [
                    _amd_observation(20, "passed", "2026-07-17T11:50:00Z", "retry-pass"),
                    _amd_observation(
                        20, "failed", "2026-07-17T11:50:00Z", "retry-fail", "retry-pass"
                    ),
                ],
            ),
        ],
    )

    state = amd.advance_incidents(reliability, amd._default_state())

    assert state["active"] == {}
    assert state["pending_soft"] == {}


def test_amd_watcher_hard_failure_confirms_immediately_and_absence_holds():
    reliability = _amd_reliability(
        [_amd_build(21, "2026-07-17T11:00:00Z")],
        [
            _amd_group(
                "hard-group",
                [_amd_observation(21, "failed", "2026-07-17T10:50:00Z", "hard-21")],
            )
        ],
    )

    confirmed = amd.advance_incidents(reliability, amd._default_state())
    assert confirmed["active"]["hard-group"]["transition"] == {
        "status": "confirmed",
        "severity": "hard",
        "peak_severity": "hard",
        "soft_streak": 0,
        "last_eligible_build_id": "21",
        "incident_start_build_id": "21",
        "confirmed_build_id": "21",
    }

    reliability["builds"].append(_amd_build(22, "2026-07-17T12:00:00Z"))
    held = amd.advance_incidents(reliability, confirmed)

    assert held["active"] == confirmed["active"]
    assert set(held["processed_build_numbers"]) == {21, 22}


def test_amd_watcher_pass_clears_one_soft_without_confirming_issue():
    reliability = _amd_reliability(
        [_amd_build(25, "2026-07-17T11:00:00Z")],
        [
            _amd_group(
                "soft-then-pass",
                [_amd_observation(25, "soft_fail", "2026-07-17T10:50:00Z", "soft-25")],
            )
        ],
    )
    pending = amd.advance_incidents(reliability, amd._default_state())
    assert set(pending["pending_soft"]) == {"soft-then-pass"}
    assert pending["active"] == {}

    reliability["builds"].append(_amd_build(26, "2026-07-17T12:00:00Z"))
    reliability["groups"][0]["observations"].insert(
        0,
        _amd_observation(26, "passed", "2026-07-17T11:50:00Z", "pass-26"),
    )
    cleared = amd.advance_incidents(reliability, pending)

    assert cleared["pending_soft"] == {}
    assert cleared["active"] == {}


def test_amd_watcher_sorts_unprocessed_builds_before_soft_confirmation():
    reliability = _amd_reliability(
        [
            _amd_build(32, "2026-07-17T12:00:00Z"),
            _amd_build(31, "2026-07-17T11:00:00Z"),
        ],
        [
            _amd_group(
                "out-of-list-order",
                [
                    _amd_observation(32, "soft_fail", "2026-07-17T11:50:00Z", "soft-32"),
                    _amd_observation(31, "soft_fail", "2026-07-17T10:50:00Z", "soft-31"),
                ],
            )
        ],
    )
    already_initialized = amd._default_state() | {"initialized": True}

    confirmed = amd.advance_incidents(reliability, already_initialized)

    transition = confirmed["active"]["out-of-list-order"]["transition"]
    assert transition["soft_streak"] == 2
    assert transition["incident_start_build_id"] == "31"
    assert transition["confirmed_build_id"] == "32"

    duplicate = amd.advance_incidents(reliability, confirmed)
    assert duplicate["active"] == confirmed["active"]


def test_amd_watcher_ignores_older_build_discovered_after_newer_result():
    reliability = _amd_reliability(
        [_amd_build(40, "2026-07-17T12:00:00Z")],
        [
            _amd_group(
                "late-old-build",
                [_amd_observation(40, "soft_fail", "2026-07-17T11:50:00Z", "soft-40")],
            )
        ],
    )
    pending = amd.advance_incidents(reliability, amd._default_state())

    late_old_build = _amd_build(39, "2026-07-17T13:00:00Z")
    late_old_build["created_at"] = "2026-07-17T11:00:00Z"
    reliability["builds"].append(late_old_build)
    reliability["groups"][0]["observations"].append(
        _amd_observation(39, "soft_fail", "2026-07-17T10:50:00Z", "late-soft-39")
    )
    held = amd.advance_incidents(reliability, pending)

    row = held["pending_soft"]["late-old-build"]
    assert row["build_number"] == 40
    assert row["transition"]["soft_streak"] == 1
    assert held["group_watermarks"]["late-old-build"]["build_number"] == 40


def test_amd_watcher_prunes_order_fences_older_than_retained_catalog():
    state = amd._default_state()
    state["initialized"] = True
    state["group_watermarks"] = {
        "retired-group": {
            "build_number": 1,
            "order_at": "2026-06-01T00:00:00Z",
            "created_at": "2026-06-01T00:00:00Z",
            "finished_at": "2026-06-01T01:00:00Z",
            "result": "passed",
            "commit": "",
        }
    }
    reliability = _amd_reliability(
        [_amd_build(40, "2026-07-17T12:00:00Z")],
        [],
    )

    updated = amd.advance_incidents(reliability, state)

    assert updated["group_watermarks"] == {}


def test_amd_watcher_migrates_v1_order_fence_before_processing_late_build():
    reliability = _amd_reliability(
        [
            _amd_build(50, "2026-07-17T12:00:00Z"),
            _amd_build(49, "2026-07-17T11:00:00Z"),
        ],
        [
            _amd_group(
                "resolved-before-migration",
                [
                    _amd_observation(50, "passed", "2026-07-17T11:50:00Z", "pass-50"),
                    _amd_observation(49, "failed", "2026-07-17T10:50:00Z", "late-fail-49"),
                ],
            )
        ],
    )
    schema_v1_state = {
        "schema_version": 1,
        "initialized": True,
        "processed_build_numbers": [50],
        "active": {},
        "group_watermarks": {},
    }

    migrated = amd.advance_incidents(reliability, schema_v1_state)

    assert migrated["active"] == {}
    assert migrated["pending_soft"] == {}
    assert migrated["group_watermarks"]["resolved-before-migration"]["build_number"] == 50


def test_amd_watcher_migrates_legacy_active_rows_as_confirmed():
    reliability = _amd_reliability(
        [_amd_build(24, "2026-07-17T12:00:00Z")],
        [],
    )
    legacy = {
        "schema_version": 1,
        "initialized": True,
        "processed_build_numbers": [24],
        "active": {
            "legacy-soft": _amd_group(
                "legacy-soft",
                [],
            )
            | {
                "result": "soft_fail",
                "build_number": 24,
                "observed_at": "2026-07-17T11:50:00Z",
            }
        },
    }

    migrated = amd.advance_incidents(reliability, legacy)

    transition = migrated["active"]["legacy-soft"]["transition"]
    assert migrated["schema_version"] == 2
    assert transition["status"] == "confirmed"
    assert transition["peak_severity"] == "soft"
    assert migrated["signal_fingerprint_version"] == 1


def test_amd_issue_body_contains_exact_job_evidence_and_rule():
    row = {
        "name": "Model tests",
        "hardware": "mi300",
        "queue": "amd_mi300_1",
        "result": "failed",
        "build_number": 42,
        "build_url": "https://buildkite.com/vllm/amd-ci/builds/42",
        "job_url": "https://buildkite.com/vllm/amd-ci/builds/42/steps/canvas?jid=job-42&tab=output",
        "observed_at": "2026-07-17T11:00:00Z",
    }

    body = amd._issue_body(
        {"group-42": row},
        {"generated_at": "2026-07-17T12:00:00Z"},
        "https://github.com/run",
        "AndreasKaratzas",
    )

    assert "job-42" in body
    assert "latest eligible attempt" in body
    assert "two distinct eligible builds" in body
    assert "only an explicit pass" in body
    assert "GitHub assignee: AndreasKaratzas." in body
    assert "@AndreasKaratzas" not in body


def test_main_watcher_signal_fingerprint_ignores_recurring_build_evidence():
    transition = {
        "status": "confirmed",
        "severity": "hard",
        "peak_severity": "hard",
        "soft_streak": 0,
        "last_eligible_build_id": "42",
        "incident_start_build_id": "40",
        "confirmed_build_id": "40",
    }
    first = {
        "group": {
            "result": "failed",
            "build_number": 42,
            "job_id": "job-42",
            "transition": transition,
        }
    }
    recurring = {
        "group": {
            "result": "soft_fail",
            "build_number": 43,
            "job_id": "job-43",
            "transition": transition | {"severity": "soft", "last_eligible_build_id": "43"},
        }
    }

    assert amd._fingerprint(first) == amd._fingerprint(recurring)
    assert amd._content_fingerprint(first) != amd._content_fingerprint(recurring)
    assert amd._fingerprint(first) != amd._fingerprint(
        {
            "group": recurring["group"]
            | {"transition": recurring["group"]["transition"] | {"incident_start_build_id": "43"}}
        }
    )
    assert amd._fingerprint(first) != amd._fingerprint(
        {
            "group": recurring["group"]
            | {"transition": recurring["group"]["transition"] | {"peak_severity": "soft"}}
        }
    )


def test_main_watcher_fingerprint_migration_preserves_manual_suppression():
    active = {
        "group": {
            "result": "soft_fail",
            "build_number": 42,
        }
    }
    state = {
        "signal_fingerprint_version": 1,
        "last_fingerprint": "legacy-evidence-fingerprint",
        "suppressed": True,
        "suppressed_fingerprint": "legacy-evidence-fingerprint",
    }

    fingerprint = amd._migrate_signal_fingerprint(state, active)

    assert state["signal_fingerprint_version"] == 2
    assert state["last_fingerprint"] == fingerprint
    assert state["suppressed_fingerprint"] == fingerprint


def _ci_build(number, finished_at, commit, *, created_at=None):
    return {
        "number": number,
        "finished_at": finished_at,
        "created_at": created_at or finished_at,
        "commit": commit,
    }


def _ci_observation(number, result, observed_at, job_id, commit):
    build_url = f"https://buildkite.com/vllm/ci/builds/{number}"
    return {
        "source_pipeline": "ci",
        "build_number": number,
        "build_url": build_url,
        "build_commit": commit,
        "job_id": job_id,
        "job_url": f"{build_url}/steps/canvas?jid={job_id}&tab=output",
        "observed_at": observed_at,
        "started_at": observed_at,
        "finished_at": observed_at,
        "result": result,
        "eligible_for_reliability": True,
    }


def _ci_group(group_id, observations):
    return {
        "group_id": group_id,
        "name": "Upstream group",
        "raw_name": "gpu_1: Upstream group",
        "step_key": "upstream-step",
        "hardware": "h100",
        "queue": "h100",
        "observations": observations,
    }


def test_upstream_watcher_retains_last_good_and_first_bad_commit():
    good = "a" * 40
    first_bad = "b" * 40
    later_bad = "c" * 40
    recovered_commit = "d" * 40
    reliability = {
        "generated_at": "2026-07-17T12:10:00Z",
        "builds": [
            _ci_build(30, "2026-07-17T10:00:00Z", good),
            _ci_build(31, "2026-07-17T11:00:00Z", first_bad),
        ],
        "groups": [
            _ci_group(
                "upstream-group",
                [
                    _ci_observation(31, "failed", "2026-07-17T10:50:00Z", "bad-31", first_bad),
                    _ci_observation(30, "passed", "2026-07-17T09:50:00Z", "good-30", good),
                ],
            )
        ],
    }

    initialized = upstream.advance_incidents(reliability, upstream._default_state())
    incident = initialized["active"]["upstream-group"]

    assert incident["good_commit"] == good
    assert incident["good_build_number"] == 30
    assert incident["bad_commit"] == first_bad
    assert incident["bad_build_number"] == 31
    assert incident["commit_range_status"] == "candidate"
    assert incident["compare_url"].endswith(f"/compare/{good}...{first_bad}")
    assert incident["bisect_command"] == f"git bisect start {first_bad} {good}"

    reliability["generated_at"] = "2026-07-17T13:10:00Z"
    reliability["builds"].append(_ci_build(32, "2026-07-17T12:00:00Z", later_bad))
    reliability["groups"][0]["observations"].insert(
        0,
        _ci_observation(32, "soft_fail", "2026-07-17T11:50:00Z", "bad-32", later_bad),
    )
    continued = upstream.advance_incidents(reliability, initialized)
    incident = continued["active"]["upstream-group"]
    assert incident["good_commit"] == good
    assert incident["bad_commit"] == first_bad
    assert incident["latest_bad_commit"] == later_bad
    assert incident["transition"]["severity"] == "soft"
    assert incident["transition"]["peak_severity"] == "hard"
    assert incident["transition"]["incident_start_build_id"] == "31"

    reliability["generated_at"] = "2026-07-17T14:10:00Z"
    reliability["builds"].append(_ci_build(33, "2026-07-17T13:00:00Z", recovered_commit))
    reliability["groups"][0]["observations"].insert(
        0,
        _ci_observation(
            33,
            "passed",
            "2026-07-17T12:50:00Z",
            "good-33",
            recovered_commit,
        ),
    )
    recovered = upstream.advance_incidents(reliability, continued)
    assert recovered["active"] == {}


def test_upstream_watcher_confirms_soft_on_second_build_and_keeps_first_bad():
    good = "a" * 40
    first_soft = "b" * 40
    second_soft = "c" * 40
    reliability = {
        "generated_at": "2026-07-17T12:10:00Z",
        "builds": [
            _ci_build(60, "2026-07-17T09:00:00Z", good),
            _ci_build(61, "2026-07-17T10:00:00Z", first_soft),
        ],
        "groups": [
            _ci_group(
                "soft-upstream-group",
                [
                    _ci_observation(
                        61,
                        "soft_fail",
                        "2026-07-17T09:50:00Z",
                        "soft-61",
                        first_soft,
                    ),
                    _ci_observation(60, "passed", "2026-07-17T08:50:00Z", "good-60", good),
                ],
            )
        ],
    }

    pending = upstream.advance_incidents(reliability, upstream._default_state())

    assert pending["active"] == {}
    pending_row = pending["pending_soft"]["soft-upstream-group"]
    assert pending_row["transition"]["soft_streak"] == 1
    assert pending_row["bad_commit"] == first_soft
    assert pending_row["good_commit"] == good

    reliability["builds"].append(_ci_build(62, "2026-07-17T11:00:00Z", second_soft))
    reliability["groups"][0]["observations"].insert(
        0,
        _ci_observation(
            62,
            "soft_fail",
            "2026-07-17T10:50:00Z",
            "soft-62",
            second_soft,
        ),
    )
    confirmed = upstream.advance_incidents(reliability, pending)

    incident = confirmed["active"]["soft-upstream-group"]
    assert confirmed["pending_soft"] == {}
    assert incident["bad_commit"] == first_soft
    assert incident["latest_bad_commit"] == second_soft
    assert incident["transition"]["incident_start_build_id"] == "61"
    assert incident["transition"]["confirmed_build_id"] == "62"


def test_upstream_issue_body_contains_bisect_candidate():
    good = "a" * 40
    bad = "b" * 40
    row = {
        "group_id": "upstream-group",
        "name": "Upstream group",
        "hardware": "h100",
        "queue": "h100",
        "result": "failed",
        "build_number": 31,
        "build_url": "https://buildkite.com/vllm/ci/builds/31",
        "job_url": "https://buildkite.com/vllm/ci/builds/31/steps/canvas?jid=bad-31&tab=output",
        "observed_at": "2026-07-17T10:50:00Z",
        "good_commit": good,
        "bad_commit": bad,
        "commit_range_status": "candidate",
        "compare_url": f"https://github.com/vllm-project/vllm/compare/{good}...{bad}",
        "bisect_command": f"git bisect start {bad} {good}",
    }

    body = upstream._issue_body(
        {"upstream-group": row},
        {"generated_at": "2026-07-17T12:00:00Z"},
        "https://github.com/run",
        "AndreasKaratzas",
    )

    assert "Upstream CI origin/main test-group alert" in body
    assert f"/compare/{good}...{bad}" in body
    assert f"git bisect start {bad} {good}" in body
    assert "ancestry must be verified" in body


def test_upstream_watcher_orders_commit_range_by_build_creation():
    good = "1" * 40
    bad = "2" * 40
    reliability = {
        "generated_at": "2026-07-17T13:10:00Z",
        "builds": [
            _ci_build(
                40,
                "2026-07-17T12:30:00Z",
                good,
                created_at="2026-07-17T10:00:00Z",
            ),
            _ci_build(
                41,
                "2026-07-17T11:30:00Z",
                bad,
                created_at="2026-07-17T11:00:00Z",
            ),
        ],
        "groups": [
            _ci_group(
                "out-of-order-group",
                [
                    _ci_observation(41, "failed", "2026-07-17T11:20:00Z", "bad-41", bad),
                    _ci_observation(40, "passed", "2026-07-17T12:20:00Z", "good-40", good),
                ],
            )
        ],
    }

    state = upstream.advance_incidents(reliability, upstream._default_state())

    incident = state["active"]["out-of-order-group"]
    assert incident["good_commit"] == good
    assert incident["bad_commit"] == bad


def test_upstream_watcher_initializes_from_each_groups_latest_outcome():
    bad = "1" * 40
    recovered = "2" * 40
    reliability = {
        "generated_at": "2026-07-17T13:10:00Z",
        "builds": [
            _ci_build(45, "2026-07-17T11:30:00Z", bad),
            _ci_build(46, "2026-07-17T12:30:00Z", recovered),
        ],
        "groups": [
            _ci_group(
                "recovered-before-enable-group",
                [
                    _ci_observation(46, "passed", "2026-07-17T12:20:00Z", "good-46", recovered),
                    _ci_observation(45, "failed", "2026-07-17T11:20:00Z", "bad-45", bad),
                ],
            )
        ],
    }

    state = upstream.advance_incidents(reliability, upstream._default_state())

    assert state["active"] == {}
    assert state["group_watermarks"]["recovered-before-enable-group"]["build_number"] == 46


def test_upstream_watcher_ignores_older_build_that_finishes_late():
    bad = "3" * 40
    old_good = "2" * 40
    recovered_commit = "4" * 40
    reliability = {
        "generated_at": "2026-07-17T12:10:00Z",
        "builds": [
            _ci_build(
                51,
                "2026-07-17T11:30:00Z",
                bad,
                created_at="2026-07-17T11:00:00Z",
            )
        ],
        "groups": [
            _ci_group(
                "late-build-group",
                [_ci_observation(51, "failed", "2026-07-17T11:20:00Z", "bad-51", bad)],
            )
        ],
    }
    initial = upstream.advance_incidents(reliability, upstream._default_state())
    assert "late-build-group" in initial["active"]

    reliability["generated_at"] = "2026-07-17T13:10:00Z"
    reliability["builds"].append(
        _ci_build(
            50,
            "2026-07-17T12:30:00Z",
            old_good,
            created_at="2026-07-17T10:00:00Z",
        )
    )
    reliability["groups"][0]["observations"].append(
        _ci_observation(50, "passed", "2026-07-17T12:20:00Z", "old-good-50", old_good)
    )
    after_late_pass = upstream.advance_incidents(reliability, initial)
    assert "late-build-group" in after_late_pass["active"]
    incident = after_late_pass["active"]["late-build-group"]
    assert incident["good_commit"] == old_good
    assert incident["bad_commit"] == bad

    reliability["generated_at"] = "2026-07-17T14:10:00Z"
    reliability["builds"].append(
        _ci_build(
            52,
            "2026-07-17T13:30:00Z",
            recovered_commit,
            created_at="2026-07-17T12:00:00Z",
        )
    )
    reliability["groups"][0]["observations"].insert(
        0,
        _ci_observation(
            52,
            "passed",
            "2026-07-17T13:20:00Z",
            "recovered-52",
            recovered_commit,
        ),
    )
    recovered = upstream.advance_incidents(reliability, after_late_pass)
    assert recovered["active"] == {}


def _duration_reliability(recent, baseline):
    observations = []
    for index, minutes in enumerate(list(recent) + list(baseline)):
        number = 200 - index
        day = 17 - index
        row = _amd_observation(
            number,
            "passed",
            f"2026-07-{day:02d}T11:00:00Z",
            f"duration-{number}",
        )
        row["wall_completion_mins"] = minutes
        observations.append(row)
    return _amd_reliability(
        [],
        [_amd_group("duration-group", observations, "Duration group")],
        generated_at="2026-07-17T12:00:00Z",
    )


def test_duration_watcher_schema_v2_matches_legacy_hydrated_evidence_and_links():
    first_created_at = datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc)
    normalized = _normalized_watcher_reliability(
        [
            _raw_amd_watcher_build(
                700 + index,
                first_created_at + timedelta(hours=index),
                wall_completion_mins=minutes,
            )
            for index, minutes in enumerate([100] * 6 + [120] * 3)
        ]
    )
    legacy = _legacy_watcher_reliability(normalized)

    assert validate_all_main_reliability(normalized, "amd-ci")
    assert validate_all_main_reliability(legacy, "amd-ci")
    assert all(
        not (set(LEGACY_OBSERVATION_DERIVED_FIELDS) & observation.keys())
        for observation in normalized["groups"][0]["observations"]
    )

    normalized_active = duration.evaluate_regressions(
        normalized,
        duration._default_state(),
    )
    legacy_active = duration.evaluate_regressions(
        legacy,
        duration._default_state(),
    )
    assert normalized_active == legacy_active

    regression = next(iter(normalized_active.values()))
    build_url = "https://buildkite.com/vllm/amd-ci/builds/708"
    job_url = f"{build_url}/steps/canvas?jid=normalized-job-708&tab=output"
    assert regression["baseline_mins"] == 100
    assert regression["recent_median_mins"] == 120
    assert regression["latest_build_url"] == build_url
    assert regression["latest_job_url"] == job_url
    assert [row["build_number"] for row in regression["recent_evidence"]] == [
        708,
        707,
        706,
    ]
    assert all(row["build_url"] and row["job_url"] for row in regression["recent_evidence"])

    normalized_body = duration._issue_body(
        normalized_active,
        normalized,
        "https://github.com/run",
        "AndreasKaratzas",
    )
    legacy_body = duration._issue_body(
        legacy_active,
        legacy,
        "https://github.com/run",
        "AndreasKaratzas",
    )
    assert normalized_body == legacy_body
    assert f"[Normalized watcher group]({job_url})" in normalized_body
    assert f"[#708]({build_url})" in normalized_body
    assert "normalized-job-707" in normalized_body
    assert "normalized-job-706" in normalized_body


def test_duration_watcher_requires_three_recent_and_six_baseline_runs():
    exact = _duration_reliability([115, 115, 115], [100] * 6)
    active = duration.evaluate_regressions(exact, duration._default_state())

    assert set(active) == {"duration-group"}
    assert active["duration-group"]["baseline_mins"] == 100
    assert active["duration-group"]["recent_median_mins"] == 115
    assert active["duration-group"]["increase_pct"] == 15

    below = _duration_reliability([114.9, 114.9, 114.9], [100] * 6)
    assert duration.evaluate_regressions(below, duration._default_state()) == {}

    short_recent = _duration_reliability([120, 120], [])
    assert duration.evaluate_regressions(short_recent, duration._default_state()) == {}

    short_baseline = _duration_reliability([120] * 3, [100] * 5)
    assert duration.evaluate_regressions(short_baseline, duration._default_state()) == {}


def test_duration_watcher_holds_fixed_baseline_until_recent_median_recovers():
    initial = duration.evaluate_regressions(
        _duration_reliability([120] * 3, [100] * 12),
        duration._default_state(),
    )
    assert initial["duration-group"]["baseline_mins"] == 100

    still_slow = duration.evaluate_regressions(
        _duration_reliability([118] * 3, [118] * 12),
        {"active": initial},
    )
    assert still_slow["duration-group"]["baseline_mins"] == 100
    assert still_slow["duration-group"]["recent_median_mins"] == 118

    recovered = duration.evaluate_regressions(
        _duration_reliability([114.9] * 3, [118] * 12),
        {"active": still_slow},
    )
    assert recovered == {}


def test_duration_issue_body_has_exact_evidence_and_auto_close_rule():
    reliability = _duration_reliability([120] * 3, [100] * 12)
    active = duration.evaluate_regressions(
        reliability,
        duration._default_state(),
    )

    body = duration._issue_body(
        active,
        reliability,
        "https://github.com/run",
        "AndreasKaratzas",
    )

    assert "steps/canvas?jid=duration-200&tab=output" in body
    assert "Queue wait is excluded" in body
    assert "baseline is fixed" in body
    assert "Resolution: recent median below baseline" in body
    assert "GitHub assignee: AndreasKaratzas." in body
    assert "@AndreasKaratzas" not in body


def _agent_row(
    node,
    group,
    started,
    *,
    build=100,
    job="job",
    state="soft",
    infra=1,
    canceled=0,
    pipeline="amd-ci",
):
    return {
        "nd": node,
        "h": "mi300",
        "p": pipeline,
        "q": "amd_mi300_1",
        "g": group,
        "s": state,
        "ng": False,
        "i": infra,
        "bc": canceled,
        "b": build,
        "j": job,
        "t": started,
        "e": started,
        "d": started[:10],
    }


def test_agent_health_alert_requires_three_logical_failures_and_two_groups():
    payload = {
        "generated_at": "2026-07-17T12:00:00Z",
        "failing_runs": [
            _agent_row("gpu1", "group-a", "2026-07-17T10:00:00Z", build=1, job="a1"),
            _agent_row("gpu1", "group-a", "2026-07-17T10:05:00Z", build=1, job="a2"),
            _agent_row("gpu1", "group-b", "2026-07-17T10:10:00Z", build=2, job="b"),
            _agent_row("gpu1", "group-c", "2026-07-17T10:20:00Z", build=3, job="c"),
            _agent_row("gpu1", "canceled", "2026-07-17T10:30:00Z", build=4, canceled=1),
            _agent_row("(unidentified)", "unknown", "2026-07-17T10:40:00Z", build=5),
            _agent_row("gpu2", "one", "2026-07-17T10:00:00Z", build=6),
            _agent_row("gpu2", "two", "2026-07-17T10:10:00Z", build=7),
        ],
    }

    events = agent.find_alert_events(payload)

    assert len(events) == 1
    event = events[0]
    assert event["node"] == "gpu1"
    assert event["failure_count"] == 3
    assert event["group_count"] == 3
    assert {run["job_id"] for run in event["runs"]} == {"a2", "b", "c"}


def test_agent_health_alert_uses_six_hour_window_and_infra_signal_only():
    payload = {
        "generated_at": "2026-07-17T12:00:00Z",
        "failing_runs": [
            _agent_row("gpu1", "old", "2026-07-17T05:59:00Z", build=1),
            _agent_row("gpu1", "not-infra", "2026-07-17T10:00:00Z", build=2, infra=0),
            _agent_row("gpu1", "one", "2026-07-17T10:05:00Z", build=3),
            _agent_row("gpu1", "two", "2026-07-17T10:10:00Z", build=4),
        ],
    }

    assert agent.find_alert_events(payload) == []


def test_agent_health_issue_body_links_each_exact_buildkite_attempt():
    payload = {
        "generated_at": "2026-07-17T12:00:00Z",
        "failing_runs": [
            _agent_row("gpu-chi-1", "a", "2026-07-17T10:00:00Z", build=1, job="job-a"),
            _agent_row("gpu-chi-1", "b", "2026-07-17T10:05:00Z", build=2, job="job-b"),
            _agent_row("gpu-chi-1", "c", "2026-07-17T10:10:00Z", build=3, job="job-c"),
        ],
    }
    events = agent.find_alert_events(payload)

    body = agent._issue_body(
        events,
        payload,
        "https://github.com/run",
        "AndreasKaratzas",
    )

    assert "steps/canvas?jid=job-a&tab=output" in body
    assert "ops_analytics_view=agent-health" in body
    assert "ops_agent_node=gpu-chi-1" in body
    assert "at least three logical failures" in body


def test_alert_payload_freshness_fails_closed():
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)

    assert agent._is_fresh({"generated_at": "2026-07-17T10:00:00Z"}, now)
    assert not agent._is_fresh({"generated_at": "2026-07-17T08:00:00Z"}, now)
    assert amd._is_fresh({"generated_at": "2026-07-17T10:00:00Z"}, now)
    assert not amd._is_fresh({"generated_at": "2026-07-17T08:00:00Z"}, now)
    assert duration._is_fresh({"generated_at": "2026-07-17T10:00:00Z"}, now)
    assert not duration._is_fresh({"generated_at": "2026-07-17T08:00:00Z"}, now)
    assert upstream._is_fresh({"generated_at": "2026-07-17T10:00:00Z"}, now)
    assert not upstream._is_fresh({"generated_at": "2026-07-17T08:00:00Z"}, now)
