"""The GitHub-native clock wakes fixed profiles and always hands off its successor."""

from collections import Counter
from pathlib import Path

import pytest
import yaml

from vllm import external_recovery_tick as recovery


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/dashboard-clock.yml"
REPOSITORY = recovery.DEFAULT_REPOSITORY


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert 0 < seconds <= recovery.ACTIONS_LOOP_POLL_SECONDS
        self.now += seconds


def _run(duration, dispatch):
    clock = FakeClock()
    result = recovery.run_actions_loop(
        REPOSITORY, "secret", duration,
        monotonic=clock.monotonic, sleep=clock.sleep, dispatch=dispatch,
    )
    return result, clock


def test_two_hour_loop_dispatches_each_profile_on_its_interval_and_exits():
    calls = []

    def dispatch(repository, token, workflow):
        calls.append((repository, token, workflow))
        return len(calls)

    result, clock = _run(7200, dispatch)

    assert result == 0
    assert clock.now == 1000.0 + 7200
    assert {(repository, token) for repository, token, _ in calls} == {(REPOSITORY, "secret")}
    assert Counter(workflow for *_, workflow in calls) == {
        "publication-watchdog.yml": 6,
        "queue-monitor.yml": 12,
        "queue-lifecycle.yml": 4,
        "docker-builds.yml": 4,
        "dns-health.yml": 2,
        "health-check.yml": 2,
    }


def test_failed_dispatch_warns_and_retries_next_pass_without_aborting(capsys):
    attempts = Counter()

    def dispatch(repository, token, workflow):
        attempts[workflow] += 1
        if workflow == "queue-monitor.yml" and attempts[workflow] <= 2:
            raise recovery.RecoveryTickError("GitHub dispatch returned HTTP 502")
        return 1

    result, _ = _run(1200, dispatch)

    assert result == 0
    assert capsys.readouterr().out.count("::warning::queue-monitor.yml dispatch failed") == 2
    # Retries at 360s and 420s, then the next due slot at 900s.
    assert attempts["queue-monitor.yml"] == 4
    assert attempts["publication-watchdog.yml"] == 1


def test_loop_refuses_to_start_without_a_valid_token_or_repository():
    with pytest.raises(recovery.RecoveryTickError, match="token"):
        recovery.run_actions_loop(REPOSITORY, "", 60, dispatch=pytest.fail)
    with pytest.raises(recovery.RecoveryTickError, match="repository"):
        recovery.run_actions_loop("", "secret", 60, dispatch=pytest.fail)


def test_loop_never_wakes_daily_or_weekly_profiles():
    profiles = {workflow for workflow, _, _ in recovery.ACTIONS_LOOP_PROFILES}
    assert profiles <= set(recovery.WORKFLOW_INTERVALS)
    assert not profiles & {"deployment-retention.yml", "scheduler-activity.yml"}


def test_workflow_is_a_single_self_dispatching_chain_with_an_hourly_reviver():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    triggers = workflow[True]
    assert triggers["schedule"] == [{"cron": "7 * * * *"}]
    assert "workflow_dispatch" in triggers
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert workflow["concurrency"] == {"group": "dashboard-clock", "cancel-in-progress": False}
    assert workflow["permissions"] == {}

    (job,) = workflow["jobs"].values()
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["permissions"] == {"actions": "write", "contents": "read"}
    loop, handoff = job["steps"][-2:]
    assert "--github-actions-loop --duration-seconds 19800" in loop["run"]
    assert loop["timeout-minutes"] * 60 > 19800
    assert job["timeout-minutes"] > loop["timeout-minutes"]
    assert handoff["if"] == "always()"
    assert job["steps"][0]["run"] == 'echo "CLOCK_STARTED=$(date +%s)" >> "$GITHUB_ENV"'
    assert handoff["env"]["LOOP_OUTCOME"] == "${{ steps.loop.outcome }}"
    assert loop["id"] == "loop"
    assert "CLOCK_STARTED + 1200" in handoff["run"]
    assert "actions/workflows/dashboard-clock.yml/dispatches" in handoff["run"]
    assert "-f ref=main" in handoff["run"]
    assert handoff["run"].rstrip().endswith("exit 1")
