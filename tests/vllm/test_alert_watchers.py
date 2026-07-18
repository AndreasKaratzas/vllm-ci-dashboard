from __future__ import annotations

from datetime import datetime, timezone

from vllm import agent_health_issue_watcher as agent
from vllm import amd_duration_regression_watcher as duration
from vllm import amd_main_failure_watcher as amd
from vllm.ci.managed_issue import reconcile_managed_issue, validate_target_repo


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
    suppressed = _reconcile(unchanged, client, active=True, fingerprint="new")
    assert suppressed["issue"] is None
    assert suppressed["suppressed"] is True
    assert len(client.opened) == 1

    healthy = _reconcile(suppressed, client, active=False, fingerprint="")
    assert healthy["suppressed"] is False

    reopened = _reconcile(healthy, client, active=True, fingerprint="later")
    assert reopened["issue"]["number"] == 102


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


def test_amd_watcher_initializes_from_latest_build_then_resolves_on_pass():
    reliability = _amd_reliability(
        [
            _amd_build(10, "2026-07-17T10:00:00Z"),
            _amd_build(11, "2026-07-17T11:00:00Z"),
        ],
        [
            _amd_group("old", [_amd_observation(10, "failed", "2026-07-17T09:50:00Z", "old-fail")]),
            _amd_group("current", [_amd_observation(11, "soft_fail", "2026-07-17T10:50:00Z", "current-soft")]),
        ],
    )

    initialized = amd.advance_incidents(reliability, amd._default_state())

    assert set(initialized["processed_build_numbers"]) == {10, 11}
    assert set(initialized["active"]) == {"current"}
    assert initialized["active"]["current"]["result"] == "soft_fail"

    reliability["builds"].append(_amd_build(12, "2026-07-17T12:00:00Z"))
    reliability["groups"][1]["observations"].insert(
        0,
        _amd_observation(12, "passed", "2026-07-17T11:50:00Z", "current-pass"),
    )
    resolved = amd.advance_incidents(reliability, initialized)

    assert resolved["active"] == {}
    assert set(resolved["processed_build_numbers"]) == {10, 11, 12}


def test_amd_watcher_latest_retry_attempt_wins_inside_build():
    reliability = _amd_reliability(
        [_amd_build(20, "2026-07-17T12:00:00Z")],
        [
            _amd_group(
                "retried",
                [
                    _amd_observation(20, "passed", "2026-07-17T11:50:00Z", "retry-pass"),
                    _amd_observation(20, "failed", "2026-07-17T11:50:00Z", "retry-fail", "retry-pass"),
                ],
            ),
        ],
    )

    state = amd.advance_incidents(reliability, amd._default_state())

    assert state["active"] == {}


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
    assert "72 hours" in body
    assert "cc @AndreasKaratzas" in body


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
    assert "cc @AndreasKaratzas" in body


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
