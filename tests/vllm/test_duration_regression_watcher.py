"""AMD main duration-slowdown watcher: signals, rules, issue and main_ci projection."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from vllm import amd_duration_regression_watcher as duration
from vllm import main_ci_section
from vllm.ci.reliability_history import build_all_main_reliability, validate_all_main_reliability
from vllm.ci.watcher_state import bounded_watcher_state

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
STEADY = [10.0, 10.2, 9.8, 10.1, 9.9] * 4
SLOW = [14.0, 14.2, 13.9, 14.1, 14.0, 13.8] * 5


class FakeIssueClient:
    def __init__(self):
        self.opened = []
        self.updated = []
        self.comments = []
        self.closed = []

    def issue_state(self, number, ownership_marker):
        return "open"

    def open_issue(self, title, body, label_specs):
        self.opened.append((title, body))
        return 900 + len(self.opened)

    def update_issue(self, number, title, body):
        self.updated.append((number, title, body))
        return True

    def ensure_owner_assigned(self, number):
        return True

    def comment_issue(self, number, body):
        self.comments.append(body)
        return True

    def close_issue(self, number):
        self.closed.append(number)
        return True


def _ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _job(job_id, name, minutes, at, *, step_key, queue="amd_mi300_1", state="passed"):
    started = at + timedelta(minutes=1)
    # Buildkite REST jobs carry the step key at the top level.
    job = {
        "id": job_id,
        "type": "script",
        "name": name,
        "state": state,
        "soft_failed": False,
        "runnable_at": _ts(at),
        "started_at": _ts(started),
        "finished_at": _ts(started + timedelta(minutes=minutes)),
        "agent_query_rules": [f"queue={queue}"],
        "step": {"id": f"step-{job_id}"},
    }
    if step_key:
        job["step_key"] = step_key
    return job


def _builds(series, *, end=NOW - timedelta(hours=1), name=":amd: (MI300) Sleep Mode", step_key="sleep",
            queue="amd_mi300_1", same_commit_at=None, every=timedelta(hours=2), first=5000):
    """One build every ``every`` ending at ``end``. Each entry is minutes, a list of
    shard minutes, or None in a list for a failed shard."""
    builds = []
    for index, minutes in enumerate(series):
        at = end - every * (len(series) - 1 - index)
        number = first + index
        shards = minutes if isinstance(minutes, list) else [minutes]
        jobs = [
            _job(
                f"j{number}-{shard}",
                f"{name} Shard {shard + 1}/{len(shards)}" if len(shards) > 1 else name,
                value if value is not None else 1.0,
                at,
                step_key=step_key,
                queue=queue,
                state="passed" if value is not None else "failed",
            )
            for shard, value in enumerate(shards)
        ]
        commit = number - 1 if same_commit_at == index else number
        builds.append({
            "number": number,
            "branch": "main",
            "state": "passed" if all(job["state"] == "passed" for job in jobs) else "failed",
            "commit": f"{commit:040x}",
            "message": f"commit {number}",
            "created_at": _ts(at),
            "started_at": _ts(at),
            "finished_at": _ts(at + timedelta(hours=1)),
            "web_url": f"https://buildkite.com/vllm/ci/builds/{number}",
            "jobs": jobs,
        })
    return builds


def _reliability(builds, generated_at=NOW):
    reliability = build_all_main_reliability(
        builds,
        pipeline_slug="ci",
        window_days=30,
        generated_at=_ts(generated_at),
        collection_provenance={"exhaustive": True},
    )
    assert validate_all_main_reliability(reliability, "ci")
    reliability["hardware_scope"] = "amd_mi_gpu"
    return reliability


def _evaluate(series, state=None, **kwargs):
    generated_at = kwargs.pop("generated_at", NOW)
    reliability = _reliability(_builds(series, **kwargs), generated_at)
    return duration.evaluate_slowdowns(reliability, state or duration._default_state())


def _only(active):
    (row,) = active.values()
    return row


def test_outcome_history_records_final_passing_wall_minutes():
    at = NOW - timedelta(hours=3)
    retried = _job("a1", ":amd: (MI300) Retried", 30, at, step_key="retried", state="failed")
    retried.update(retried=True, retried_in_job_id="a2")
    jobs = [
        retried,
        {**_job("a2", ":amd: (MI300) Retried", 12, at + timedelta(minutes=40), step_key="retried"), "retries_count": 1},
        _job("b", ":amd: (MI300) Broken", 7, at, step_key="broken", state="failed"),
    ]
    build = _builds([1])[0]
    build.update(jobs=jobs, state="failed")
    history = {group["name"]: group["outcome_history"] for group in _reliability([build])["groups"]}

    assert history[":amd: (MI300) Retried"]["results"] == "r"
    assert history[":amd: (MI300) Retried"]["wall_mins"] == [12.0]
    assert history[":amd: (MI300) Broken"]["wall_mins"] == [None]


def test_step_change_reports_levels_onset_range_and_context_samples():
    row = _only(_evaluate(STEADY + SLOW))

    assert row["status"] == "active"
    assert (row["baseline_mins"], row["baseline_runs"]) == (10.0, 20)
    assert (row["recent_mins"], row["recent_runs"]) == (14.0, 30)
    assert (row["last_good"]["build_number"], row["onset"]["build_number"]) == (5019, 5020)
    assert row["last_rise"] == row["onset"]
    assert row["likely_cause"] == "vllm_change"
    assert row["compare_url"] == f"https://github.com/vllm-project/vllm/compare/{5019:040x}...{5020:040x}"
    assert row["first_detected_at"] == _ts(NOW)
    builds = [sample[0] for sample in row["samples"]]
    assert builds == list(range(5000, 5050))
    assert row["samples"][-1] == [5049, row["latest"]["created_at"], 13.8]


def test_samples_keep_the_baseline_tail_and_thin_the_runs_since_the_onset():
    row = _only(_evaluate(STEADY * 2 + [14.0, 14.2, 13.9, 14.1] * 50, every=timedelta(hours=1)))

    builds = [sample[0] for sample in row["samples"]]
    onset = row["onset"]["build_number"]
    assert len(builds) == duration.SERIES_LIMIT
    assert builds == sorted(builds)
    assert sum(build < onset for build in builds) == duration.SERIES_LIMIT // 3
    assert onset in builds and builds[-1] == row["latest"]["build_number"]


# A baseline at 10 and a current level at 14 that qualifies; each case below
# breaks exactly one rule.
QUALIFYING = ([10.0, 10.1, 9.9, 10.0, 10.2, 9.8, 10.0, 10.1], [14.0, 14.1, 13.9, 14.0, 14.2, 13.8, 14.0, 14.1])


@pytest.mark.parametrize(
    ("baseline", "current", "noise"),
    [
        pytest.param([0.8] * 8, [1.4] * 8, 0.01, id="under-two-minutes"),
        pytest.param([30.0] * 8, [34.0] * 8, 0.1, id="under-twenty-percent"),
        pytest.param(QUALIFYING[0], QUALIFYING[1], 2.0, id="under-five-standard-errors"),
        pytest.param(QUALIFYING[0], QUALIFYING[1][:5] + [11.0] * 3, 0.1, id="current-level-not-held"),
        pytest.param(QUALIFYING[0][:6] + [13.0] * 2, QUALIFYING[1], 0.1, id="baseline-already-high"),
        pytest.param(QUALIFYING[0][:7], QUALIFYING[1], 0.1, id="short-baseline"),
    ],
)
def test_each_alert_rule_rejects_on_its_own(baseline, current, noise):
    assert duration._qualifies(list(QUALIFYING[0] + QUALIFYING[1]), [0, 8, 16], 1, 0.1, False)

    values = baseline + current
    assert not duration._qualifies(values, [0, len(baseline), len(values)], 1, noise, False)


def test_long_steps_alert_on_fifteen_minutes_below_twenty_percent():
    baseline, current = [200.0, 201.0, 199.0, 200.0] * 2, [218.0, 219.0, 217.0, 218.0] * 2
    assert duration._qualifies(baseline + current, [0, 8, 16], 1, 0.5, False)
    assert not duration._qualifies(baseline + [212.0] * 8, [0, 8, 16], 1, 0.5, False)


def test_a_level_back_at_the_baseline_ends_the_slowdown_from_that_onset():
    values = [10.0] * 8 + [14.0] * 8 + [10.2] * 8 + [14.0] * 8
    bounds = [0, 8, 16, 24, 32]

    assert not duration._qualifies(values, bounds, 1, 0.1, False)
    assert duration._qualifies(values, bounds, 3, 0.1, False)


def test_gradual_slowdown_alerts_from_the_level_it_left():
    # No single step reaches 20 percent; the climb from 12 to 15 does.
    ramp = [level + 0.1 * (index % 3) for level in (12.0, 13.0, 14.0, 15.0) for index in range(40)]
    row = _only(_evaluate(ramp, every=timedelta(hours=1)))

    assert (row["baseline_mins"], row["recent_mins"]) == (12.1, 15.1)
    assert row["onset"]["build_number"] == 5040
    assert row["last_rise"]["build_number"] == 5120


def test_staged_slowdown_reports_its_latest_qualifying_change():
    row = _only(_evaluate([10.0] * 20 + [13.0] * 20 + [17.0] * 30))

    assert row["onset"]["build_number"] == 5040
    assert (row["baseline_mins"], row["recent_mins"]) == (13.0, 17.0)


def test_small_relative_absolute_and_bimodal_changes_do_not_alert():
    assert _evaluate([0.8, 0.9, 0.8, 0.8, 0.9] * 4 + [1.0, 1.1, 1.0] * 10) == {}
    assert _evaluate(STEADY + [11.5, 11.6, 11.4] * 10) == {}
    bimodal = [10.0, 16.0, 10.2, 15.8, 9.9, 16.1, 10.1, 16.2] * 7
    assert _evaluate(bimodal) == {}


def test_a_new_level_opens_only_after_eight_runs_and_two_days():
    assert _evaluate(STEADY + [14.0] * 13, every=timedelta(hours=4)) != {}
    assert _evaluate(STEADY + [14.0] * 7, every=timedelta(hours=10)) == {}
    assert _evaluate(STEADY + [14.0] * 20, every=timedelta(hours=1)) == {}
    # A settled level that is only 36 hours old can still be an infrastructure event.
    assert _evaluate(STEADY + [14.0] * 13, every=timedelta(hours=3)) == {}


def test_an_unsettled_newest_level_opens_on_the_level_before_it():
    row = _only(_evaluate(STEADY + SLOW + [17.0] * 6))
    assert (row["baseline_mins"], row["recent_mins"]) == (10.0, 14.0)
    assert _evaluate(STEADY + SLOW + [10.0] * 6) == {}


def test_shards_are_one_signal_and_relayouts_are_not_slowdowns():
    even = [[10.0, 10.0, 10.0]] * 20
    assert _evaluate(even + [[20.0, 20.0]] * 30) == {}
    assert _evaluate(even + [[10.0, 10.0, 10.0, 10.0, 6.0]] * 30) == {}
    # The same job count before an intermediate layout is not comparable either.
    assert _evaluate(even + [[20.0, 20.0]] * 10 + [[16.0, 16.0, 16.0]] * 30) == {}

    row = _only(_evaluate(even + [[10.0, 10.0, 10.0, 10.0]] + [[14.0, 14.0, 14.0]] * 30))
    assert row["name"] == ":amd: (MI300) Sleep Mode"
    assert row["jobs"] == 3
    assert (row["baseline_mins"], row["recent_mins"]) == (30.0, 42.0)


def test_level_splits_need_five_standard_errors():
    noisy = [8.0, 12.0, 9.0, 11.0, 10.0] * 4
    shifted = [value + 1.0 for value in noisy]
    assert duration._change_points(noisy + shifted, duration._noise(noisy + shifted)) == []
    assert duration._change_points(STEADY + SLOW, duration._noise(STEADY + SLOW)) == [20]


def test_builds_with_another_job_count_between_regular_ones_are_skipped():
    mixed = [[10.0, 10.0]] * 4 + ([[10.0, 10.0, 10.0]] + [[10.0, 10.0]] * 4) * 4
    assert _evaluate(mixed + [[10.0, 10.0, 10.0]] * 30) == {}


def test_builds_with_a_failed_shard_are_not_compared():
    assert _evaluate([[10.0, 10.0, None]] * 20 + [[10.0, 10.0, 10.0]] * 30) == {}


def test_open_incident_holds_until_half_threshold_and_keeps_its_onset():
    opened = _evaluate(STEADY + [12.5] * 30)
    signal_id = next(iter(opened))
    state = {**duration._default_state(), "active": opened}

    assert _evaluate(STEADY + [11.6] * 30) == {}
    held = _evaluate(STEADY + [11.6] * 30, state=state)
    assert list(held) == [signal_id]
    assert held[signal_id]["first_detected_at"] == opened[signal_id]["first_detected_at"]
    assert _evaluate(STEADY + [10.8] * 30, state=state) == {}

    moved = {signal_id: {**opened[signal_id], "onset": {**opened[signal_id]["onset"], "build_number": 5022}}}
    kept = _evaluate(STEADY + [12.5] * 30, state={**duration._default_state(), "active": moved})
    assert (kept[signal_id]["last_good"]["build_number"], kept[signal_id]["onset"]["build_number"]) == (5021, 5022)


def test_slowdowns_settle_when_they_stop_growing_and_same_commit_points_to_the_environment():
    row = _only(_evaluate(STEADY + [14.0] * 200, same_commit_at=20))
    assert row["status"] == "settled"
    assert row["likely_cause"] == "environment"
    assert row["compare_url"] == ""

    grew = _only(_evaluate(STEADY + [14.0] * 200 + [16.0] * 30))
    assert grew["status"] == "active"
    assert grew["onset"]["build_number"] == 5020
    assert grew["last_rise"]["build_number"] == 5220


def test_stale_non_mi_and_keyless_signals_are_ignored():
    series = STEADY + SLOW
    assert _evaluate(series, end=NOW - timedelta(hours=80)) == {}
    assert _evaluate(series, name=":nvidia: (H100) Sleep Mode", queue="gpu_1_queue") == {}
    assert _evaluate(series, step_key="") == {}


def test_duration_history_needs_step_keys():
    keyed = _builds(STEADY + SLOW)
    keyless = _builds(STEADY + SLOW, step_key="", name=":amd: (MI300) Other", first=6000)
    assert duration._keyed_duration_history(_reliability(keyed + keyless[:5]))
    assert not duration._keyed_duration_history(_reliability(keyed + keyless[:8]))
    assert not duration._keyed_duration_history(_reliability(keyless))


def test_issue_lists_active_rows_and_the_body_changes_at_most_daily():
    active = _evaluate(STEADY + SLOW)
    settled = _evaluate([20.0, 20.2, 19.8, 20.1, 19.9] * 4 + [28.0] * 200, name=":amd: (MI300) Old", step_key="old")
    rows = {**active, **settled}

    body = duration._issue_body(rows, {"generated_at": _ts(NOW)}, "https://github.com/run", "AndreasKaratzas")

    assert duration.DASHBOARD_URL in body
    assert "Sleep Mode" in body and ":amd: (MI300) Old" not in body
    assert "1 settled slowdowns are listed on the dashboard." in body
    assert _only(active)["compare_url"] in body
    moved = {key: {**row, "recent_mins": 15.0, "recent_runs": 31} for key, row in rows.items()}
    later = _ts(NOW + timedelta(hours=5))
    assert duration._fingerprint(moved) == duration._fingerprint(rows) != duration._fingerprint(settled)
    assert duration._content_fingerprint(moved, later) == duration._content_fingerprint(rows, _ts(NOW))
    assert duration._content_fingerprint(rows, _ts(NOW + timedelta(days=1))) != duration._content_fingerprint(rows, _ts(NOW))
    onset = {key: {**row, "onset": {**row["onset"], "build_number": 1}} for key, row in rows.items()}
    assert duration._content_fingerprint(onset, _ts(NOW)) != duration._content_fingerprint(rows, _ts(NOW))


def test_only_settled_slowdowns_close_the_issue_saying_they_are_still_slower():
    settled = _evaluate(STEADY + [14.0] * 200)
    assert "still slower" in duration._recovery_body(settled, "run")
    assert "No AMD MI main test step is slower" in duration._recovery_body({}, "run")


def _run(monkeypatch, tmp_path, builds, now, client):
    reliability = _reliability(builds, generated_at=now)
    (tmp_path / "analytics.json").write_text(json.dumps({"ci": {"all_main_reliability": reliability}}))
    monkeypatch.setenv("GITHUB_TOKEN", "offline-test")
    monkeypatch.setenv("GITHUB_REPOSITORY", "andreaskaratzas/vllm-ci-dashboard")
    monkeypatch.setattr(duration, "ANALYTICS", tmp_path / "analytics.json")
    monkeypatch.setattr(duration, "STATE", tmp_path / "open_amd_duration_regression_issues.json")
    monkeypatch.setattr(duration, "GitHubIssueClient", lambda *_args: client)
    assert duration.run() == 0
    return reliability


def test_run_writes_state_and_main_ci_publishes_it(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    client = FakeIssueClient()
    reliability = _run(monkeypatch, tmp_path, _builds(STEADY + SLOW, end=now - timedelta(hours=1)), now, client)

    ((title, body),) = client.opened
    assert title == "AMD main duration: 1 test steps slower since a change"
    state = json.loads(duration.STATE.read_text())
    assert state["incident_state_version"] == duration.INCIDENT_STATE_VERSION
    section = main_ci_section.build_main_ci({"all_main_reliability": reliability}, {}, _ts(now), duration_state=state)
    slowdowns = section["slowdowns"]
    assert slowdowns["available"] is True
    assert slowdowns["issue"]["url"].endswith("/issues/901")
    assert slowdowns["rules"] == duration.RULES
    assert [row["signal_id"] for row in slowdowns["rows"]] == list(state["active"])
    assert section["summary"]["active_slowdowns"] == 1
    assert main_ci_section.main_ci_shell(section)["active_slowdowns"] == 1
    unpublished = main_ci_section.build_main_ci({"all_main_reliability": reliability}, {}, _ts(now))
    assert unpublished["slowdowns"]["available"] is False
    assert unpublished["summary"]["active_slowdowns"] is None
    for stale in ({**state, "last_run": _ts(now - timedelta(hours=4))}, {**state, "hardware_scope": "all"}):
        section = main_ci_section.build_main_ci({"all_main_reliability": reliability}, {}, _ts(now), duration_state=stale)
        assert section["slowdowns"]["available"] is False

    compact, _ = bounded_watcher_state(duration.STATE, state, max_bytes=len(json.dumps(state)) - 200)
    (row,) = compact["active"].values()
    assert "samples" not in row and row["onset"] == next(iter(state["active"].values()))["onset"]


def test_run_refuses_issue_mutations_without_step_keyed_history(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    client = FakeIssueClient()
    _run(monkeypatch, tmp_path, _builds(STEADY + SLOW, end=now - timedelta(hours=1), step_key=""), now, client)

    assert client.opened == client.updated == client.closed == []
    assert not duration.STATE.exists()


def test_version_one_state_rows_are_rederived(monkeypatch, tmp_path):
    path = tmp_path / "open_amd_duration_regression_issues.json"
    path.write_text(json.dumps({"schema_version": 1, "active": {"old-id": {"name": "x"}}, "issue": {"number": 553}}))
    monkeypatch.setattr(duration, "STATE", path)

    state = duration._read_state()

    assert state["active"] == {}
    assert state["issue"]["number"] == 553
    assert state["incident_state_version"] == duration.INCIDENT_STATE_VERSION
    old = main_ci_section._slowdowns({"schema_version": 1, "hardware_scope": "amd_mi_gpu", "last_run": _ts(NOW)}, NOW)
    assert old["available"] is False
