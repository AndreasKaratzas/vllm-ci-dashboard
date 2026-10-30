"""Main pulse: minutes-latency AMD MI main failure detection between collections."""

from __future__ import annotations

import copy
import dataclasses
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from vllm import amd_main_failure_watcher as amd
from vllm import ci_main_failure_watcher as upstream
from vllm import main_pulse as pulse
from vllm.ci import main_pulse_overlay as overlay_lib
from vllm.ci.reliability_history import build_all_main_reliability

ROOT = Path(__file__).resolve().parents[2]
CONFIG = pulse.load_config()


def _ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _job(job_id, name, state, at, **extra):
    return {
        "id": job_id,
        "type": "script",
        "name": f"mi300_1: {name}",
        "state": state,
        "soft_failed": state == "soft_fail",
        "runnable_at": _ts(at),
        "started_at": _ts(at + timedelta(minutes=1)),
        "finished_at": _ts(at + timedelta(minutes=10)),
        "agent_query_rules": ["queue=amd_mi300_1"],
        "step": {"id": f"step-{job_id}", "key": name.lower().replace(" ", "-")},
        **extra,
    }


def _build(number, created_at, jobs):
    failed = any(job["state"] == "failed" and not job.get("retried") for job in jobs)
    return {
        "number": number,
        "branch": "main",
        "state": "failed" if failed else "passed",
        "commit": f"{number:040x}",
        "message": "main commit",
        "created_at": _ts(created_at),
        "started_at": _ts(created_at),
        "finished_at": _ts(created_at + timedelta(minutes=30)),
        "web_url": f"https://buildkite.com/vllm/ci/builds/{number}",
        "jobs": jobs,
    }


@pytest.fixture
def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _history(now):
    start = now - timedelta(hours=4)
    return [
        _build(number, start + timedelta(minutes=20 * index), [
            _job(f"k{number}", "Kernels", "passed", start + timedelta(minutes=20 * index)),
            _job(f"e{number}", "Entrypoints", "passed", start + timedelta(minutes=20 * index)),
        ])
        for index, number in enumerate((301, 302))
    ]


def _reliability(builds, generated_at):
    return build_all_main_reliability(
        builds, pipeline_slug="ci", window_days=30, generated_at=_ts(generated_at),
        nightly_pattern="nightly", collection_provenance={"exhaustive": True},
    )


def _authority(builds, now):
    """The collection watcher state after replaying ``builds``."""
    state = amd.advance_incidents(
        amd.mi_scoped_reliability(_reliability(builds, now)), amd._default_state(),
        track_commit_range=True, initialize_from_history=True,
    )
    state.update(hardware_scope="amd_mi_gpu", timeline=[])
    return json.loads(json.dumps(state))


def _step(authority, overlay, builds, now, *, since=None, exhaustive=True):
    return pulse.pulse_step(
        CONFIG, authority, overlay, builds,
        {"filter": "finished_from", "pages_fetched": 1, "termination_reason": "short_page", "exhaustive": exhaustive},
        since=since or pulse.fetch_window(CONFIG, overlay, now), fetched_at=now,
    )


def _failing(now, number=303, *, minutes=10, retried_pass=False):
    at = now - timedelta(minutes=minutes + 30)
    if retried_pass:
        kernels = [
            _job(f"k{number}a", "Kernels", "failed", at, retried=True, retried_in_job_id=f"k{number}b"),
            _job(f"k{number}b", "Kernels", "passed", at + timedelta(minutes=12), retries_count=1),
        ]
    else:
        kernels = [_job(f"k{number}", "Kernels", "failed", at)]
    build = _build(number, at, kernels + [_job(f"e{number}", "Entrypoints", "passed", at)])
    build["finished_at"] = _ts(now - timedelta(minutes=minutes))
    return build


def test_repository_budget_fits_the_interval_cadence():
    assert CONFIG.minimum_interval_minutes == 10
    assert CONFIG.reservation_request_starts == 5 <= pulse.MAX_RESERVATION_REQUEST_STARTS
    assert 150 * CONFIG.reservation_request_starts <= CONFIG.max_request_starts == 750
    assert CONFIG.max_pages == 1 and CONFIG.max_lookback_minutes == 180


def test_reservation_is_interval_gated_and_capacity_capped(now):
    outputs, ledger = pulse.plan_reservation(None, CONFIG, reservation_id="main-pulse-1-1", now=now)
    assert outputs["request_mode"] == "reserved" and outputs["reserved_request_starts"] == 5

    soon, unchanged = pulse.plan_reservation(ledger, CONFIG, reservation_id="main-pulse-2-1", now=now + timedelta(minutes=9))
    assert (soon["request_mode"], soon["reserved_request_starts"], unchanged) == ("interval_gated", 0, None)
    assert soon["available_at"] == _ts(now + timedelta(minutes=10))

    later, ledger = pulse.plan_reservation(ledger, CONFIG, reservation_id="main-pulse-3-1", now=now + timedelta(minutes=10))
    assert later["request_mode"] == "reserved" and len(ledger["reservations"]) == 2
    with pytest.raises(pulse.MainPulseError, match="permit reuse"):
        pulse.plan_reservation(ledger, CONFIG, reservation_id="main-pulse-3-1", now=now + timedelta(minutes=30))

    tight = dataclasses.replace(CONFIG, max_request_starts=10)
    full = {**ledger, "policy": pulse._ledger_policy(tight)}
    capped, unchanged = pulse.plan_reservation(full, tight, reservation_id="main-pulse-4-1", now=now + timedelta(minutes=40))
    assert (capped["request_mode"], capped["reserved_request_starts"], unchanged) == ("capacity_gated", 0, None)
    expired, _ = pulse.plan_reservation(full, tight, reservation_id="main-pulse-5-1", now=now + timedelta(hours=26))
    assert expired["request_mode"] == "reserved" and expired["rolling_reserved_starts"] == 5


def test_new_hard_failure_is_detected_without_a_github_token(monkeypatch, now):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    authority = _authority(_history(now), now - timedelta(hours=1))

    overlay, summary = _step(authority, None, [_failing(now)], now)

    assert summary["opened"] == 1 and summary["active"] == 1
    (row,) = [row for row in overlay["groups"].values() if row["status"] == "active"]
    assert row["entry"]["bad_build_number"] == 303
    assert row["entry"]["transition"]["status"] == "confirmed"
    # The good bound comes from the collection state's last observed pass.
    assert (row["entry"]["good_commit"], row["entry"]["commit_range_status"]) == (f"{302:040x}", "candidate")
    assert (overlay["issue"], overlay["issue_basis"]) == (None, "")
    assert overlay["updated_at"] == _ts(now)
    assert [event["source"] for event in overlay["timeline"]] == ["pulse"]


def test_cursor_advances_and_a_repeated_slice_is_idempotent(now):
    authority = _authority(_history(now), now - timedelta(hours=1))
    first, _ = _step(authority, None, [_failing(now)], now)
    assert first["cursor"] == _ts(now - timedelta(minutes=CONFIG.cursor_overlap_minutes))
    assert pulse.fetch_window(CONFIG, first, now + timedelta(minutes=10)) == now - timedelta(minutes=10)

    later = now + timedelta(minutes=10)
    second, summary = _step(authority, first, [_failing(now)], later)

    assert (summary["new_builds"], summary["opened"]) == (0, 0)
    assert second["groups"] == first["groups"]
    assert second["cursor"] == _ts(later - timedelta(minutes=10))
    # A stale cursor is clamped to the lookback; the collection covers older builds.
    stale = {**second, "cursor": _ts(now - timedelta(days=2))}
    assert pulse.fetch_window(CONFIG, stale, now) == now - timedelta(minutes=CONFIG.max_lookback_minutes)

    # A truncated page keeps its older, unseen builds in the next window.
    since = now - timedelta(minutes=40)
    truncated, summary = _step(authority, second, [_failing(now)], later, since=since, exhaustive=False)
    assert summary["exhaustive"] is False and truncated["cursor"] == _ts(since)


def test_retried_and_passed_job_does_not_open_an_incident(now):
    authority = _authority(_history(now), now - timedelta(hours=1))

    overlay, summary = _step(authority, None, [_failing(now, retried_pass=True)], now)

    assert summary["opened"] == 0
    assert {row["status"] for row in overlay["groups"].values()} == {"clear"}


def test_soft_failures_wait_for_confirmation(now):
    authority = _authority(_history(now), now - timedelta(hours=1))
    opened, _ = _step(authority, None, [_failing(now, minutes=20)], now)
    passing = _build(304, now - timedelta(minutes=35), [
        _job("k304", "Kernels", "passed", now - timedelta(minutes=35)),
        _job("e304", "Entrypoints", "soft_fail", now - timedelta(minutes=35)),
    ])
    passing["finished_at"] = _ts(now - timedelta(minutes=5))

    resolved, summary = _step(authority, opened, [passing], now + timedelta(minutes=10))

    assert summary["resolved"] == 1 and summary["active"] == 0 and summary["pending_soft"] == 1
    statuses = {row["entry"]["name"] if row["entry"] else row["status"]: row["status"] for row in resolved["groups"].values()}
    assert "pending_soft" in statuses.values() and "active" not in statuses.values()


def _write_analytics(path, reliability):
    path.write_text(json.dumps({"ci": {"all_main_reliability": reliability}}))


STATE_SHA = "a" * 40


def _collection_env(monkeypatch, tmp_path, overlay):
    """Collection watcher inputs; ``overlay=False`` models a failed export."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(amd, "ANALYTICS", tmp_path / "analytics.json")
    export_path = tmp_path / "main-pulse-state.json"
    export_path.unlink(missing_ok=True)
    if overlay is not False:
        export_path.write_bytes(
            overlay_lib.encode_export(CONFIG.state_branch, STATE_SHA if overlay else None, overlay)
        )
    monkeypatch.setenv("MAIN_PULSE_STATE_FILE", str(export_path))
    return dataclasses.replace(upstream.CONFIG, state=tmp_path / "open_ci_main_failure_issues.json")


def test_later_full_collection_agrees_with_the_pulse(monkeypatch, tmp_path, now):
    history = _history(now)
    authority = _authority(history, now - timedelta(hours=1))
    overlay, _ = _step(authority, None, [_failing(now)], now)
    (pulse_group,) = [gid for gid, row in overlay["groups"].items() if row["status"] == "active"]
    config = _collection_env(monkeypatch, tmp_path, overlay)

    # A collection whose cohort predates the pulse build keeps the incident open.
    config.state.write_text(json.dumps(authority))
    _write_analytics(tmp_path / "analytics.json", _reliability(history, now))
    assert amd.run_watcher(config) == 0
    stale_collection = json.loads(config.state.read_text())
    assert set(stale_collection["active"]) == {pulse_group}
    assert stale_collection["last_run"] == _ts(now) and stale_collection["issue"] is None

    # The next cohort contains the same build and confirms the same incident.
    config.state.write_text(json.dumps(authority))
    _write_analytics(tmp_path / "analytics.json", _reliability(history + [_failing(now)], now))
    assert amd.run_watcher(config) == 0
    collection = json.loads(config.state.read_text())
    (incident,) = collection["active"].values()
    pulse_entry = overlay["groups"][pulse_group]["entry"]
    for key in ("transition", "good_commit", "bad_commit", "bad_build_number", "commit_range_status"):
        assert incident[key] == pulse_entry[key]
    assert [(row["event"], row["build_number"]) for row in collection["timeline"]] == [("opened", 303)]

    # Once collection state catches up, the pulse carries nothing of its own.
    caught_up, _summary = _step(collection, overlay, [_failing(now)], now + timedelta(minutes=10))
    assert caught_up["groups"] == {} and caught_up["issue"] is None


def test_collection_state_wins_over_older_pulse_observations(now):
    authority = _authority(_history(now), now - timedelta(hours=1))
    overlay, _ = _step(authority, None, [_failing(now)], now)
    merged = overlay_lib.merge_overlay(authority, overlay)
    assert len(merged["active"]) == 1

    newer = copy.deepcopy(authority)
    for group_id, row in overlay["groups"].items():
        newer["group_watermarks"][group_id] = {**row["watermark"], "build_number": 999, "order_at": _ts(now)}
    assert overlay_lib.merge_overlay(newer, overlay)["active"] == {}


def test_collection_advances_without_a_readable_overlay(monkeypatch, tmp_path, now):
    history = _history(now)
    authority = {**_authority(history, now - timedelta(hours=1)), "issue": {"number": 650, "opened_at": _ts(now)}}
    config = _collection_env(monkeypatch, tmp_path, False)
    config.state.write_text(json.dumps(authority))
    _write_analytics(tmp_path / "analytics.json", _reliability(history, now))

    assert amd.run_watcher(config) == 0
    collection = json.loads(config.state.read_text())
    assert collection["last_run"] == _ts(now) and collection["hardware_scope"] == "amd_mi_gpu"
    assert collection["issue"] is None and collection["suppressed"] is False


def test_overlay_export_records_the_state_commit(remote_repo, tmp_path, now):
    output = tmp_path / "export.json"
    args = ["--root", str(remote_repo), "export-overlay", "--output", str(output)]
    assert pulse.main(args) == 0
    absent = overlay_lib.load_export_file(output)
    assert (absent["state_sha"], absent["overlay"]) == (None, None)

    overlay, _ = _step(_authority(_history(now), now - timedelta(hours=1)), None, [_failing(now)], now)
    sha = pulse.publish_branch_file(
        remote_repo, "origin", CONFIG.state_branch, CONFIG.state_path, overlay_lib.encode_overlay(overlay),
        expected_sha=None, message="state", at=_ts(now),
    )
    assert pulse.main(args) == 0
    exported = overlay_lib.load_export_file(output)
    assert (exported["state_sha"], exported["overlay"]) == (sha, overlay)

    with pytest.raises(ValueError):
        overlay_lib.normalize_export({**exported, "state_sha": None})


def test_overlay_rejects_malformed_payloads(now):
    authority = _authority(_history(now), now - timedelta(hours=1))
    overlay, _ = _step(authority, None, [_failing(now)], now)
    assert overlay_lib.decode_overlay(overlay_lib.encode_overlay(overlay)) == overlay
    for mutate in (
        lambda value: value.update(extra=1),
        lambda value: value.update(issue_basis="x"),
        lambda value: next(iter(value["groups"].values())).update(status="open"),
    ):
        broken = copy.deepcopy(overlay)
        mutate(broken)
        with pytest.raises(ValueError):
            overlay_lib.normalize_overlay(broken)
    with pytest.raises(pulse.MainPulseError, match="initialized"):
        _step({**authority, "initialized": False}, None, [], now)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def remote_repo(tmp_path: Path) -> Path:
    checkout = tmp_path / "repo"
    checkout.mkdir()
    _git(checkout, "init", "-q")
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "remote.git")], check=True)
    _git(checkout, "remote", "add", "origin", str(tmp_path / "remote.git"))
    return checkout


def test_branch_state_round_trips_with_a_lease_and_reserve_gates(remote_repo, now):
    first = pulse.reserve(remote_repo, CONFIG, reservation_id="main-pulse-1-1", now=now, remote="origin")
    assert first["request_mode"] == "reserved"
    gated = pulse.reserve(remote_repo, CONFIG, reservation_id="main-pulse-2-1", now=now + timedelta(minutes=3), remote="origin")
    assert gated["request_mode"] == "interval_gated" and "budget_sha" not in gated
    sha, payload = pulse.read_branch_file(
        remote_repo, "origin", CONFIG.budget_branch, CONFIG.budget_ledger_path, CONFIG.max_ledger_bytes
    )
    assert sha == first["budget_sha"] and len(json.loads(payload)["reservations"]) == 1
    assert _git(remote_repo, "rev-list", "--count", sha) == "1"

    assert pulse.read_branch_file(remote_repo, "origin", CONFIG.state_branch, CONFIG.state_path, 1024) == (None, None)
    written = pulse.publish_branch_file(
        remote_repo, "origin", CONFIG.state_branch, CONFIG.state_path, b"{}\n",
        expected_sha=None, message="state", at=_ts(now),
    )
    with pytest.raises(pulse.MainPulseError):
        pulse.publish_branch_file(
            remote_repo, "origin", CONFIG.state_branch, CONFIG.state_path, b"[]\n",
            expected_sha=None, message="stale writer", at=_ts(now),
        )
    assert pulse.read_branch_file(remote_repo, "origin", CONFIG.state_branch, CONFIG.state_path, 1024) == (written, b"{}\n")


def test_cli_uses_recorded_builds_and_a_local_state(monkeypatch, tmp_path, now):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    authority_path = tmp_path / "authority.json"
    authority_path.write_text(json.dumps(_authority(_history(now), now - timedelta(hours=1))))
    recorded = tmp_path / "builds.json"
    recorded.write_text(json.dumps([_failing(now), _failing(now, 290, minutes=400)]))
    state = tmp_path / "overlay.json"
    args = ["run", "--authority-state", str(authority_path), "--state-file", str(state),
            "--recorded-builds", str(recorded), "--now", _ts(now)]

    assert pulse.main(args) == 0
    overlay = overlay_lib.load_overlay_file(state)
    assert overlay["last_run"]["main_builds"] == 1 and overlay["last_run"]["opened"] == 1
    assert overlay["issue"] is None and overlay["issue_basis"] == ""
    assert pulse.main(args) == 0
    assert overlay_lib.load_overlay_file(state)["last_run"]["opened"] == 0


def _job_steps(workflow: str, job: str) -> tuple[dict, list[dict]]:
    data = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    return data["jobs"][job], data["jobs"][job]["steps"]


def test_queue_monitor_runs_the_gated_pulse():
    job, steps = _job_steps("queue-monitor.yml", "main-pulse")
    assert "needs" not in job and job["timeout-minutes"] <= 10 and job["continue-on-error"] is True
    assert job["permissions"] == {"contents": "write"}
    names = [step.get("name") for step in steps]
    reserve = steps[names.index("Reserve durable main pulse request budget")]
    assert "main_pulse.py reserve" in reserve["run"] and '--github-output "$OUTPUTS"' in reserve["run"]
    assert "buildkite_request_guard.py initialize" in reserve["run"]
    assert "interval_gated:0|capacity_gated:0|reserved:5)" in reserve["run"]
    token_steps = [step for step in steps if "BUILDKITE_TOKEN" in (step.get("env") or {})]
    assert [step["name"] for step in token_steps] == ["Detect new AMD MI main failures"]
    assert all("GITHUB_TOKEN" not in (step.get("env") or {}) for step in steps)
    for name in ("Read published main failure watcher state", "Detect new AMD MI main failures"):
        assert steps[names.index(name)]["if"] == "steps.pulse-budget.outputs.request_mode == 'reserved'"
    assert names.index("Read published main failure watcher state") < names.index("Detect new AMD MI main failures")
    report = steps[-1]
    assert "buildkite_request_guard.py report" in report["run"]
    assert report["if"] == "always() && steps.pulse-budget.outcome == 'success'"


def test_collection_watcher_reads_the_pulse_overlay_first():
    _job_def, steps = _job_steps("hourly-master.yml", "collect-and-deploy")
    names = [step.get("name") for step in steps]
    read = steps[names.index("Read main pulse observations")]
    watch = steps[names.index("Track AMD MI main CI test-group failures")]
    assert names.index("Read main pulse observations") + 1 == names.index(
        "Track AMD MI main CI test-group failures"
    )
    assert read["continue-on-error"] is True and read["if"] == watch["if"]
    assert "main_pulse.py export-overlay --output \"$RUNNER_TEMP/main-pulse-state.json\"" in read["run"]
    assert watch["env"]["MAIN_PULSE_STATE_FILE"] == "${{ runner.temp }}/main-pulse-state.json"
