"""Main-branch monitoring: per-build evidence, alerting and the main_ci section."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vllm import amd_main_failure_watcher as amd
from vllm import build_operations_snapshot as ops
from vllm import ci_flakiness_watcher as flaky
from vllm import ci_main_failure_watcher as upstream
from vllm import main_ci_section
from vllm.ci import main_flakiness
from vllm.ci.reliability_history import build_all_main_reliability, validate_all_main_reliability
from vllm.ci.watcher_state import bounded_watcher_state

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


class FakeIssueClient:
    def __init__(self):
        self.next_number = 700
        self.states = {}
        self.opened = []
        self.updated = []
        self.closed = []
        self.comments = []

    def issue_state(self, number, ownership_marker):
        return self.states.get(number, "open")

    def open_issue(self, title, body, label_specs):
        self.next_number += 1
        self.states[self.next_number] = "open"
        self.opened.append((self.next_number, title, body))
        return self.next_number

    def update_issue(self, number, title, body):
        self.updated.append((number, title, body))
        return True

    def ensure_owner_assigned(self, number):
        return True

    def comment_issue(self, number, body):
        self.comments.append((number, body))
        return True

    def close_issue(self, number):
        self.states[number] = "closed"
        self.closed.append(number)
        return True


def _ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _job(job_id, name, state, at, *, queue="amd_mi300_1", step="", **extra):
    return {
        "id": job_id,
        "type": "script",
        "name": f"mi300_1: {name}",
        "state": state,
        "soft_failed": state == "soft_fail",
        "runnable_at": _ts(at),
        "started_at": _ts(at + timedelta(minutes=1)),
        "finished_at": _ts(at + timedelta(minutes=10)),
        "agent_query_rules": [f"queue={queue}"],
        "step": {"id": f"step-{job_id}", "key": step or name.lower().replace(" ", "-")},
        **extra,
    }


def _build(number, created_at, jobs, *, message="main commit"):
    failed = any(job["state"] in {"failed", "timed_out", "broken"} for job in jobs)
    return {
        "number": number,
        "branch": "main",
        "state": "failed" if failed else "passed",
        "commit": f"{number:040x}",
        "message": message,
        "created_at": _ts(created_at),
        "started_at": _ts(created_at),
        "finished_at": _ts(created_at + timedelta(minutes=30)),
        "web_url": f"https://buildkite.com/vllm/ci/builds/{number}",
        "jobs": jobs,
    }


def _reliability(builds, generated_at=NOW):
    reliability = build_all_main_reliability(
        builds,
        pipeline_slug="ci",
        window_days=30,
        generated_at=_ts(generated_at),
        nightly_pattern=r"nightly",
        collection_provenance={"exhaustive": True},
    )
    assert validate_all_main_reliability(reliability, "ci")
    return reliability


def _group(reliability, name):
    return next(group for group in reliability["groups"] if group["name"].endswith(name))


def test_reliability_records_per_build_amd_counts_and_final_outcome_history():
    at = NOW - timedelta(hours=2)
    jobs = [
        _job("a", "Stable", "passed", at),
        _job("b1", "Retried", "failed", at, retried=True, retried_in_job_id="b2"),
        _job("b2", "Retried", "passed", at + timedelta(minutes=12), retry_type="automatic", retries_count=1),
        _job("c", "Soft", "soft_fail", at),
        _job("d", "Blocked", "blocked", at),
        _job("e", "Broken", "failed", at),
        _job("f", "Canceled", "canceled", at),
    ]
    reliability = _reliability([_build(101, at, jobs)])

    assert reliability["builds"][0]["amd_jobs"] == {
        "passed": 2, "soft": 1, "hard": 1, "unknown": 1, "not_run": 1, "retried": 1,
    }
    assert _group(reliability, "Retried")["outcome_history"] == {"builds": [101], "results": "r", "wall_mins": [9.0]}
    assert _group(reliability, "Stable")["outcome_history"]["results"] == "p"
    assert _group(reliability, "Broken")["outcome_history"]["results"] == "f"
    assert _group(reliability, "Soft")["outcome_history"]["results"] == "s"
    assert _group(reliability, "Blocked")["outcome_history"] == {"builds": [], "results": "", "wall_mins": []}


def _flaky_builds(start, count, *, outcome):
    """One build every six hours; ``outcome(index)`` picks each group's job state."""
    builds = []
    for index in range(count):
        at = start + timedelta(hours=6 * index)
        number = 1000 + index
        jobs = [_job(f"s{number}", "Steady", "passed", at)]
        flip = outcome(index)
        jobs.append(_job(f"f{number}", "Flipper", flip, at))
        if index % 2 == 0:
            jobs.append(_job(f"r{number}a", "Rescued", "failed", at, retried=True, retried_in_job_id=f"r{number}b"))
            jobs.append(_job(f"r{number}b", "Rescued", "passed", at + timedelta(minutes=12), retries_count=1))
        else:
            jobs.append(_job(f"r{number}a", "Rescued", "passed", at))
        builds.append(_build(number, at, jobs))
    return builds


def test_flakiness_window_counts_flips_recoveries_and_trend():
    builds = _flaky_builds(NOW - timedelta(days=6), 8, outcome=lambda i: "failed" if i % 2 else "passed")
    reliability = _reliability(builds)
    thresholds = {"window_days": 7, "min_outcome_flips": 3, "min_failed_then_passed": 3, "max_issue_rows": 50}

    rows = {row["name"]: row for row in main_flakiness.alerting_rows(reliability, NOW, thresholds)}

    assert set(rows) == {"Flipper", "Rescued"}
    assert rows["Flipper"]["current"]["flips"] == 7
    assert rows["Flipper"]["current"]["pass_rate"] == 50.0
    assert rows["Flipper"]["current"]["last_failure"]["build_number"] == 1007
    assert rows["Rescued"]["current"]["recovered"] == 4
    assert rows["Rescued"]["current"]["flips"] == 0
    assert rows["Flipper"]["trend"] == "new"


def test_repository_flakiness_thresholds_are_explicit():
    thresholds = main_flakiness.load_thresholds()
    assert thresholds == {
        "window_days": 7,
        "min_outcome_flips": 3,
        "min_failed_then_passed": 3,
        "max_issue_rows": 50,
    }


def _write_analytics(path: Path, reliability: dict) -> None:
    path.write_text(json.dumps({"ci": {"all_main_reliability": reliability}}))


def _alerting_env(monkeypatch, tmp_path, client):
    monkeypatch.setenv("GITHUB_TOKEN", "offline-test")
    monkeypatch.setenv("GITHUB_REPOSITORY", "andreaskaratzas/vllm-ci-dashboard")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setattr(amd, "ANALYTICS", tmp_path / "analytics.json")
    monkeypatch.setattr(amd, "GitHubIssueClient", lambda *_args: client)
    monkeypatch.setattr(flaky, "GitHubIssueClient", lambda *_args: client)
    monkeypatch.setattr(flaky, "STATE", tmp_path / "open_ci_flakiness_issues.json")


def test_new_hard_failure_on_main_opens_issue_in_the_same_run_and_reaches_main_ci(monkeypatch, tmp_path):
    client = FakeIssueClient()
    _alerting_env(monkeypatch, tmp_path, client)
    config = dataclasses.replace(upstream.CONFIG, state=tmp_path / "open_ci_main_failure_issues.json")
    start = datetime.now(timezone.utc) - timedelta(hours=2, minutes=30)

    def build(number, state, minutes):
        at = start + timedelta(minutes=minutes)
        return _build(number, at, [_job(f"j{number}", "Kernels", state, at)])

    history = [build(201, "passed", 0), build(202, "passed", 20)]
    _write_analytics(tmp_path / "analytics.json", _reliability(history, datetime.now(timezone.utc)))
    assert amd.run_watcher(config) == 0
    assert client.opened == []

    history.append(build(203, "failed", 40))
    _write_analytics(tmp_path / "analytics.json", _reliability(history, datetime.now(timezone.utc)))
    assert amd.run_watcher(config) == 0

    assert len(client.opened) == 1
    number, title, body = client.opened[0]
    assert title == "AMD MI main CI: 1 confirmed test-group failures (1 peak hard, 0 soft)"
    assert f"{202:040x}"[:12] in body and f"{203:040x}"[:12] in body
    state = json.loads(config.state.read_text())
    (incident,) = state["active"].values()
    assert (incident["good_commit"], incident["bad_commit"]) == (f"{202:040x}", f"{203:040x}")
    assert [(row["event"], row["build_number"]) for row in state["timeline"]] == [("opened", 203)]
    assert state["issue"]["number"] == number

    section = main_ci_section.build_main_ci(
        json.loads((tmp_path / "analytics.json").read_text())["ci"],
        state,
        _ts(datetime.now(timezone.utc)),
    )
    assert section["summary"]["active_incidents"] == 1
    (row,) = section["incidents"]["active"]
    assert row["bad_build_number"] == 203 and row["good_build_number"] == 202
    assert row["compare_url"].endswith(f"{202:040x}...{203:040x}")
    assert section["incidents"]["issue"]["number"] == number
    assert section["timeline"][0]["event"] == "opened"

    history.append(build(204, "failed", 50))
    _write_analytics(tmp_path / "analytics.json", _reliability(history, datetime.now(timezone.utc)))
    assert amd.run_watcher(config) == 0
    state = json.loads(config.state.read_text())
    assert len(client.opened) == 1
    assert [(row["event"], row["build_number"]) for row in state["timeline"]] == [("opened", 203)]

    history.append(build(205, "passed", 60))
    _write_analytics(tmp_path / "analytics.json", _reliability(history, datetime.now(timezone.utc)))
    assert amd.run_watcher(config) == 0
    state = json.loads(config.state.read_text())
    assert state["active"] == {}
    assert client.closed == [number]
    assert [row["event"] for row in state["timeline"]] == ["resolved", "opened"]


def test_flakiness_watcher_opens_then_resolves_one_managed_issue(monkeypatch, tmp_path):
    client = FakeIssueClient()
    _alerting_env(monkeypatch, tmp_path, client)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    builds = _flaky_builds(now - timedelta(days=6), 8, outcome=lambda i: "failed" if i % 2 else "passed")
    _write_analytics(tmp_path / "analytics.json", _reliability(builds, now))

    assert flaky.run(now=now) == 0

    (number, title, body), = client.opened
    assert title == "AMD MI main CI flakiness: 2 test groups unstable over 7 days"
    assert "Flipper" in body and "Rescued" in body and "Steady" not in body
    assert "https://buildkite.com/vllm/ci/builds/1007" in body
    state = json.loads(flaky.STATE.read_text())
    assert set(row["name"] for row in state["groups"].values()) == {"Flipper", "Rescued"}
    assert state["issue"]["number"] == number

    later = now + timedelta(days=8)
    calm = _flaky_builds(later - timedelta(hours=12), 2, outcome=lambda _i: "passed")
    for build in calm:
        for job in build["jobs"]:
            job.update(state="passed", retried=False, retried_in_job_id=None)
    _write_analytics(tmp_path / "analytics.json", _reliability(builds + calm, later))

    assert flaky.run(now=later) == 0

    assert client.closed == [number]
    assert json.loads(flaky.STATE.read_text())["groups"] == {}


def test_flakiness_thresholds_are_inclusive():
    thresholds = main_flakiness.load_thresholds()
    assert thresholds["min_outcome_flips"] == thresholds["min_failed_then_passed"] == 3
    # Five builds: Flipper p,f,p,f,f flips 3 times; Rescued is rescued in builds 0, 2 and 4.
    at_threshold = _flaky_builds(NOW - timedelta(days=6), 5, outcome=lambda i: "failed" if i in {1, 3, 4} else "passed")
    rows = {row["name"]: row["current"] for row in main_flakiness.alerting_rows(_reliability(at_threshold), NOW, thresholds)}
    assert (rows["Flipper"]["flips"], rows["Rescued"]["recovered"]) == (3, 3)

    # Four builds: Flipper p,f,p,p flips twice; Rescued is rescued twice.
    below = _flaky_builds(NOW - timedelta(days=6), 4, outcome=lambda i: "failed" if i == 1 else "passed")
    assert main_flakiness.alerting_rows(_reliability(below), NOW, thresholds) == []


def _flaky_issue_run(tmp_path, now, *, extra_group=False):
    builds = _flaky_builds(now - timedelta(days=6), 8, outcome=lambda i: "failed" if i % 2 else "passed")
    if extra_group:
        for index, build in enumerate(builds):
            at = datetime.fromisoformat(build["created_at"].replace("Z", "+00:00"))
            build["jobs"].append(_job(f"n{build['number']}", "Newcomer", "failed" if index % 2 else "passed", at))
    _write_analytics(tmp_path / "analytics.json", _reliability(builds, now))
    assert flaky.run(now=now) == 0
    return json.loads(flaky.STATE.read_text())


def test_flakiness_rerun_keeps_listed_since_and_issue(monkeypatch, tmp_path):
    client = FakeIssueClient()
    _alerting_env(monkeypatch, tmp_path, client)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    first = _flaky_issue_run(tmp_path, now)
    second = _flaky_issue_run(tmp_path, now + timedelta(hours=2), extra_group=True)

    assert len(client.opened) == 1 and len(client.updated) == 1
    assert {row["name"] for row in second["groups"].values()} == {"Flipper", "Rescued", "Newcomer"}
    for group_id, row in first["groups"].items():
        assert second["groups"][group_id]["listed_since"] == row["listed_since"] == _ts(now)
    newcomer = next(row for row in second["groups"].values() if row["name"] == "Newcomer")
    assert newcomer["listed_since"] == _ts(now + timedelta(hours=2))


def test_manual_close_holds_while_listed_groups_change(monkeypatch, tmp_path):
    client = FakeIssueClient()
    _alerting_env(monkeypatch, tmp_path, client)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _flaky_issue_run(tmp_path, now)
    (number, _title, _body), = client.opened
    client.states[number] = "closed"

    assert _flaky_issue_run(tmp_path, now + timedelta(hours=2))["suppressed"] is True
    state = _flaky_issue_run(tmp_path, now + timedelta(hours=4), extra_group=True)
    assert state["suppressed"] is True and state["issue"] is None
    assert [row[0] for row in client.opened] == [number]

    later = now + timedelta(days=8)
    calm = _flaky_builds(later - timedelta(hours=12), 2, outcome=lambda _i: "passed")
    for build in calm:
        for job in build["jobs"]:
            job.update(state="passed", retried=False, retried_in_job_id=None)
    _write_analytics(tmp_path / "analytics.json", _reliability(calm, later))
    assert flaky.run(now=later) == 0
    assert json.loads(flaky.STATE.read_text())["suppressed"] is False

    _flaky_issue_run(tmp_path, later + timedelta(hours=1))
    assert [row[0] for row in client.opened] == [number, number + 1]


def test_flakiness_state_keeps_only_the_groups_shown_in_the_issue(monkeypatch, tmp_path):
    client = FakeIssueClient()
    _alerting_env(monkeypatch, tmp_path, client)
    thresholds = {**main_flakiness.load_thresholds(), "max_issue_rows": 1}
    monkeypatch.setattr(main_flakiness, "load_thresholds", lambda *_args: thresholds)
    state = _flaky_issue_run(tmp_path, datetime.now(timezone.utc).replace(microsecond=0))

    (_number, title, body), = client.opened
    assert title.startswith("AMD MI main CI flakiness: 2 test groups")
    assert len(state["groups"]) == 1
    assert "1 additional groups are listed on the dashboard." in body


def test_flakiness_watcher_refuses_stale_or_history_free_evidence(monkeypatch, tmp_path):
    client = FakeIssueClient()
    _alerting_env(monkeypatch, tmp_path, client)
    now = datetime.now(timezone.utc)
    builds = _flaky_builds(now - timedelta(days=6), 8, outcome=lambda i: "failed" if i % 2 else "passed")
    _write_analytics(tmp_path / "analytics.json", _reliability(builds, now - timedelta(hours=4)))
    assert flaky.run(now=now) == 0

    legacy = _reliability(builds, now)
    for group in legacy["groups"]:
        group.pop("outcome_history")
    _write_analytics(tmp_path / "analytics.json", legacy)
    assert flaky.run(now=now) == 0

    assert client.opened == [] and not flaky.STATE.exists()


def test_main_ci_section_summarizes_every_main_build_and_series():
    builds = _flaky_builds(NOW - timedelta(days=6), 8, outcome=lambda i: "failed" if i % 2 else "passed")
    builds[-1]["message"] = "Full CI run - nightly\n\nbody"
    reliability = _reliability(builds)
    section = main_ci_section.build_main_ci(
        {"all_main_reliability": reliability, "main_retry_analysis": {"summary": {"retry_attempt_count": 4}}},
        {},
        _ts(NOW),
    )

    assert section["available"] is True
    assert [row["number"] for row in section["builds"]] == list(range(1007, 999, -1))
    latest = section["builds"][0]
    assert latest["nightly"] is True and latest["headline"] == "Full CI run - nightly"
    assert latest["amd"] == {"passed": 2, "soft": 0, "hard": 1, "unknown": 0, "not_run": 0, "retried": 0}
    assert latest["commit"] == f"{1007:040x}" and latest["branch"] == "main"
    assert section["incidents"]["available"] is False
    assert {row["name"] for row in section["top_flaky"]} == {"Flipper"}
    assert {row["name"] for row in section["retry_hotspots"]} == {"Rescued"}
    assert section["retry_hotspots"][0]["recovered_30d"] == 4
    assert section["summary"]["retry_attempts"] == 4
    daily = section["series"]["daily"]
    assert len(daily) == 30 and len(section["series"]["hourly"]) == 168
    assert sum(bucket["builds"] for bucket in daily) == 8
    assert sum(bucket["hard"] for bucket in daily) == 4
    assert all(bucket["pass_rate"] is None or 0 <= bucket["pass_rate"] <= 100 for bucket in daily)
    shell = main_ci_section.main_ci_shell(section)
    assert shell["latest_build"]["number"] == 1007 and shell["builds"] == 8


def test_main_ci_builds_name_their_failing_groups_and_days_meet_end_to_end():
    builds = _flaky_builds(NOW - timedelta(days=2), 8, outcome=lambda i: "failed" if i % 2 else "passed")
    # The 24 hours before the latest 24 end exactly where they start: a build on the boundary is the latest day's.
    builds.append(_build(1100, NOW - timedelta(days=1), [_job("b1100", "Steady", "passed", NOW - timedelta(days=1))]))
    builds.append(_build(1101, NOW - timedelta(days=2, seconds=1), [_job("b1101", "Steady", "passed", NOW - timedelta(days=2))]))
    reliability = _reliability(builds)
    section = main_ci_section.build_main_ci({"all_main_reliability": reliability}, {}, _ts(NOW))

    rows = {row["number"]: row for row in section["builds"]}
    flipper = _group(reliability, "Flipper")["group_id"]
    (failing,) = rows[1007]["failing"]
    assert failing == {
        "group_id": flipper,
        "severity": "hard",
        "job_url": "https://buildkite.com/vllm/ci/builds/1007/steps/canvas?jid=f1007&tab=output",
    }
    assert rows[1006]["failing"] == []
    assert section["group_catalog"][flipper]["name"].endswith("Flipper")
    assert set(section["group_catalog"]) == {flipper}

    last, previous = section["summary"]["last_24h"], section["summary"]["previous_24h"]
    # Builds every 6 hours from NOW-2d: four before NOW-1d, four after, plus #1100 on the boundary.
    assert (previous["builds"], last["builds"]) == (4, 5)
    assert previous["hard"] == last["hard"] == 2
    assert main_ci_section.main_ci_shell(section)["previous_24h"] == previous


def test_main_ci_flaky_and_retry_lists_alert_on_their_own_criterion():
    """A group with one flip and three recoveries alerts only in the retry list."""
    start = NOW - timedelta(days=3)
    builds = []
    for index in range(6):
        at = start + timedelta(hours=8 * index)
        number = 2000 + index
        if index == 0:
            jobs = [_job(f"m{number}", "Mixed", "failed", at)]
        elif index % 2:
            jobs = [
                _job(f"m{number}a", "Mixed", "failed", at, retried=True, retried_in_job_id=f"m{number}b"),
                _job(f"m{number}b", "Mixed", "passed", at + timedelta(minutes=12), retries_count=1),
            ]
        else:
            jobs = [_job(f"m{number}", "Mixed", "passed", at)]
        builds.append(_build(number, at, jobs))
    section = main_ci_section.build_main_ci({"all_main_reliability": _reliability(builds)}, {}, _ts(NOW))

    (flaky,) = section["top_flaky"]
    (retry,) = section["retry_hotspots"]
    assert (flaky["flips_window"], flaky["alerting"]) == (1, False)
    assert (retry["recovered_window"], retry["alerting"]) == (3, True)
    assert section["summary"]["alerting_flaky_groups"] == 0
    assert section["summary"]["alerting_retry_groups"] == 1


def test_main_ci_section_fails_closed_without_strict_reliability():
    section = main_ci_section.build_main_ci({"all_main_reliability": {"builds": []}}, {}, _ts(NOW))
    assert section["available"] is False
    assert section["unavailable_reason"] == "strict_all_main_reliability_unavailable"


def test_main_ci_section_stays_within_budget_for_a_dense_month():
    """About 60 main builds a day for 30 days with a 200-group MI matrix."""
    start = NOW - timedelta(days=30)
    builds = []
    for index in range(1800):
        at = start + timedelta(minutes=24 * index)
        jobs = [
            _job(f"{index}-{group}", f"Group {group}", "failed" if (index + group) % 17 == 0 else "passed", at)
            for group in range(0, 200, 10)
        ]
        builds.append(_build(50_000 + index, at, jobs, message="x" * 400))
    reliability = _reliability(builds)
    for group in reliability["groups"]:
        # Densify per-group history to the full matrix size without building every job.
        group["outcome_history"] = {
            "builds": [build["number"] for build in reversed(reliability["builds"])],
            "results": "".join("pf"[(index % 9) == 0] for index in range(len(reliability["builds"]))),
        }
    limit = ops.OPERATIONS_CANARY_SECTION_MAX_BYTES["main_ci"]
    section = main_ci_section.build_main_ci({"all_main_reliability": reliability}, {}, _ts(NOW))
    bounded = main_ci_section.bounded_main_ci(section, limit)

    assert limit == 1536 * 1024
    size = len(json.dumps({"main_ci": bounded}, sort_keys=True, separators=(",", ":")))
    assert size <= limit
    assert bounded["publication_retention"]["complete_relative_to_source"] is True

    tight = main_ci_section.bounded_main_ci(section, size // 2)
    retention = tight["publication_retention"]
    assert 0 < retention["builds"]["published"] < retention["builds"]["source"]
    assert tight["builds"] == section["builds"][: retention["builds"]["published"]]
    assert tight["summary"] == section["summary"]
    with pytest.raises(RuntimeError, match="main_ci aggregates exceed"):
        main_ci_section.bounded_main_ci(section, 1024)


def test_snapshot_publishes_main_ci_section_shell_and_its_own_source_clock(tmp_path):
    builds = _flaky_builds(NOW - timedelta(days=6), 8, outcome=lambda i: "failed" if i % 2 else "passed")
    reliability = _reliability(builds, NOW - timedelta(hours=5))
    (tmp_path / "analytics.json").write_text(
        json.dumps({"ci": {"generated_at": _ts(NOW), "all_main_reliability": reliability}})
    )
    payload = ops.build_snapshot(tmp_path, generated_at=_ts(NOW))

    assert payload["main_ci"]["available"] is True
    assert payload["sources"]["main_ci"]["timestamp"] == _ts(NOW - timedelta(hours=5))
    assert payload["sources"]["main_ci"]["timestamp"] != payload["sources"]["analytics"]["timestamp"]
    manifest = ops.write_snapshot_bundle(tmp_path / "operations_v2.json", payload, log=False)
    assert manifest["shell"]["main_ci"]["builds"] == 8
    published = json.loads((tmp_path / manifest["sections"]["main_ci"]["path"]).read_text())
    assert published["main_ci"]["summary"] == payload["main_ci"]["summary"]
    assert "unavailable_reason" not in payload["sources"]["main_ci"]

    (tmp_path / "analytics.json").write_text(json.dumps({"ci": {"generated_at": _ts(NOW)}}))
    payload = ops.build_snapshot(tmp_path, generated_at=_ts(NOW))
    assert payload["sources"]["main_ci"]["timestamp"] is None
    assert payload["sources"]["main_ci"]["unavailable_reason"] == "strict_all_main_reliability_unavailable"


def test_main_state_compaction_keeps_the_incident_timeline():
    state = {
        **amd._default_state(),
        "hardware_scope": "amd_mi_gpu",
        "timeline": [{"event": "opened", "group_id": "g", "build_number": 1, "at": _ts(NOW)}],
        "group_watermarks": {f"w{index}": {"build_number": index, "order_at": _ts(NOW)} for index in range(4000)},
    }
    published, encoded = bounded_watcher_state(
        Path("open_ci_main_failure_issues.json"), state, max_bytes=64 * 1024
    )
    assert len(encoded) <= 64 * 1024
    assert published["timeline"] == state["timeline"]


def test_timeline_is_bounded_by_age_and_deduplicated():
    events = [
        {"event": "opened", "group_id": "g", "build_number": 5, "at": _ts(NOW - timedelta(days=40))},
        {"event": "opened", "group_id": "g", "build_number": 9, "at": _ts(NOW - timedelta(days=1))},
        {"event": "opened", "group_id": "g", "build_number": 9, "at": _ts(NOW - timedelta(days=1))},
        {"event": "resolved", "group_id": "g", "build_number": 10, "at": _ts(NOW)},
    ]
    assert [row["build_number"] for row in amd.bounded_timeline(events, NOW)] == [10, 9]


def test_main_ci_timeline_includes_active_incidents_predating_the_recorded_timeline():
    builds = _flaky_builds(NOW - timedelta(days=6), 4, outcome=lambda i: "failed" if i == 3 else "passed")
    reliability = _reliability(builds)
    group_id = _group(reliability, "Flipper")["group_id"]
    state = {
        "hardware_scope": "amd_mi_gpu",
        "issue": {"number": 729},
        "active": {group_id: {
            "name": "Flipper", "result": "failed", "build_number": 1003,
            "good_commit": f"{1002:040x}", "bad_commit": f"{1003:040x}", "bad_build_number": 1003,
            "transition": {"status": "confirmed", "peak_severity": "hard"},
        }},
    }
    section = main_ci_section.build_main_ci({"all_main_reliability": reliability}, state, _ts(NOW))

    (event,) = section["timeline"]
    assert event["event"] == "opened" and event["source"] == "state"
    assert event["build_url"] == "https://buildkite.com/vllm/ci/builds/1003"
    assert event["at"] == reliability["builds"][0]["created_at"]
    assert section["incidents"]["issue"]["url"].endswith("/issues/729")
