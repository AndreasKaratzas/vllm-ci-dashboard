"""Unit tests for scripts/vllm/queue_issue_watcher.py.

The watcher opens / closes GitHub issues when queue p90 wait crosses the
hysteresis thresholds. These tests stub out the GitHub HTTP layer so we can
validate the decision logic — which queue flips to hot, which flips to
healthy, and what the persisted state file looks like — without hitting the
real API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vllm import queue_issue_watcher as qiw


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Point the watcher at a tmp snapshot file + tmp state file."""
    snaps = tmp_path / "queue_timeseries.jsonl"
    state = tmp_path / "open_queue_issues.json"
    monkeypatch.setattr(qiw, "SNAPSHOTS", snaps, raising=False)
    monkeypatch.setattr(qiw, "STATE", state, raising=False)
    return snaps, state


def _write_snapshot(snaps: Path, queues: dict, ts: str = "2026-04-18T12:00:00Z"):
    snaps.write_text(json.dumps({"ts": ts, "queues": queues}) + "\n")


class _ApiRecorder:
    """Capture every GitHub API call the watcher would have made.

    Patched onto ``qiw._open_issue`` / ``_close_issue`` / ``_comment_issue``
    so we can assert on the decision pattern without running ``requests``.
    """

    def __init__(self):
        self.opened = []
        self.closed = []
        self.commented = []
        self.assigned = []
        self._next_issue_num = 1000

    def open_issue(self, token, repo, queue, stats, run_url):
        num = self._next_issue_num
        self._next_issue_num += 1
        self.opened.append((queue, num, stats))
        return num

    def close_issue(self, token, repo, number):
        self.closed.append(number)

    def comment(self, token, repo, number, body):
        self.commented.append((number, body))

    def assign(self, token, repo, number):
        self.assigned.append(number)


@pytest.fixture
def api(monkeypatch):
    rec = _ApiRecorder()
    monkeypatch.setattr(qiw, "_open_issue", rec.open_issue)
    monkeypatch.setattr(qiw, "_close_issue", rec.close_issue)
    monkeypatch.setattr(qiw, "_comment_issue", rec.comment)
    monkeypatch.setattr(qiw, "_ensure_owner_assigned", rec.assign)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    return rec


class TestReadLastSnapshot:
    def test_returns_last_valid_line(self, tmp_path, monkeypatch):
        snaps = tmp_path / "queue_timeseries.jsonl"
        snaps.write_text(
            json.dumps({"ts": "T1", "queues": {}}) + "\n"
            + json.dumps({"ts": "T2", "queues": {"a": 1}}) + "\n"
        )
        monkeypatch.setattr(qiw, "SNAPSHOTS", snaps, raising=False)
        last = qiw._read_last_snapshot()
        assert last["ts"] == "T2"

    def test_skips_junk_lines(self, tmp_path, monkeypatch):
        snaps = tmp_path / "queue_timeseries.jsonl"
        snaps.write_text(
            "not-json-just-garbage\n"
            + json.dumps({"ts": "T2", "queues": {}}) + "\n"
            + "{}incomplete\n"
        )
        monkeypatch.setattr(qiw, "SNAPSHOTS", snaps, raising=False)
        assert qiw._read_last_snapshot()["ts"] == "T2"

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(qiw, "SNAPSHOTS", tmp_path / "nope.jsonl", raising=False)
        assert qiw._read_last_snapshot() is None


class TestStateIo:
    def test_writes_and_reads_round_trip(self, tmp_path, monkeypatch):
        state = tmp_path / "state.json"
        monkeypatch.setattr(qiw, "STATE", state, raising=False)
        entry = {"number": 42, "peak_p90": 33.3, "opened_ts": "T0", "last_status_ts": ""}
        qiw._write_state({"open": {"amd_mi250_1": entry}, "last_run": "T"})
        out = qiw._read_state()
        assert out["open"]["amd_mi250_1"] == entry
        assert out["last_run"] == "T"

    def test_legacy_int_entry_is_migrated_on_read(self, tmp_path, monkeypatch):
        # Older state files stored the bare issue number. On read we must
        # upgrade those to the new dict shape so downstream code can assume
        # every entry has ``peak_p90`` / ``opened_ts`` / ``last_status_ts``.
        state = tmp_path / "state.json"
        state.write_text(json.dumps({"open": {"amd_mi250_1": 777}, "last_run": "T"}))
        monkeypatch.setattr(qiw, "STATE", state, raising=False)
        out = qiw._read_state()
        entry = out["open"]["amd_mi250_1"]
        assert entry["number"] == 777
        assert "peak_p90" in entry
        assert "opened_ts" in entry
        assert "last_status_ts" in entry

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(qiw, "STATE", tmp_path / "nope.json", raising=False)
        assert qiw._read_state() == {"open": {}, "suppressed": {}}

    def test_junk_file_returns_empty(self, tmp_path, monkeypatch):
        state = tmp_path / "junk.json"
        state.write_text("not json")
        monkeypatch.setattr(qiw, "STATE", state, raising=False)
        assert qiw._read_state() == {"open": {}, "suppressed": {}}

    def test_legacy_suppressed_entry_is_migrated_on_read(self, tmp_path, monkeypatch):
        state = tmp_path / "state.json"
        state.write_text(json.dumps({
            "open": {},
            "suppressed": {"amd_mi250_1": "2026-04-18T00:00:00Z"},
            "last_run": "T",
        }))
        monkeypatch.setattr(qiw, "STATE", state, raising=False)
        out = qiw._read_state()
        entry = out["suppressed"]["amd_mi250_1"]
        assert entry["closed_ts"] == "2026-04-18T00:00:00Z"
        assert entry["last_number"] == 0


class TestIssueTemplates:
    def test_open_issue_body_uses_supported_wait_metrics_only(self):
        body = qiw._open_issue_body(
            "amd_mi355_4",
            {
                "waiting": 4,
                "running": 2,
                "p50_wait": 54.5,
                "p75_wait": 69.0,
                "p90_wait": 69.0,
                "p99_wait": 69.0,
                "avg_wait": 58.1,
                "max_wait": 69.0,
            },
            "https://example.invalid/run",
            "AndreasKaratzas",
        )
        assert "| p50 wait | 54.5m |" in body
        assert "| p90 wait | 69.0m |" in body
        assert "| p99 wait | 69.0m |" in body
        assert "cc @AndreasKaratzas for visibility." in body
        assert "p75 wait" not in body
        assert "avg wait" not in body
        assert "max wait" not in body

    def test_status_update_body_uses_supported_wait_metrics_only(self):
        body = qiw._status_update_body(
            "amd_mi355_4",
            {
                "waiting": 4,
                "running": 2,
                "p50_wait": 54.5,
                "p75_wait": 69.0,
                "p90_wait": 69.0,
                "p99_wait": 69.0,
                "avg_wait": 58.1,
                "max_wait": 69.0,
            },
            peak_p90=72.0,
            opened_ts="2026-04-22T00:00:00Z",
            snapshot_ts="2026-04-22T12:00:00Z",
            run_url="https://example.invalid/run",
        )
        assert "| p50 wait | 54.5m |" in body
        assert "| p90 wait (current) | 69.0m |" in body
        assert "| p90 wait (peak since open) | 72.0m |" in body
        assert "| p99 wait | 69.0m |" in body
        assert "p75 wait" not in body
        assert "avg wait" not in body
        assert "max wait" not in body


class TestRun:
    def test_opens_issue_when_queue_hot(self, isolated_state, api):
        snaps, state = isolated_state
        _write_snapshot(snaps, {
            "amd_mi250_1": {"p90_wait": 45.0, "waiting": 10, "running": 5},
        })
        rc = qiw.run()
        assert rc == 0
        assert len(api.opened) == 1
        assert api.opened[0][0] == "amd_mi250_1"
        persisted = json.loads(state.read_text())
        entry = persisted["open"]["amd_mi250_1"]
        assert entry["number"] == api.opened[0][1]
        assert entry["peak_p90"] == 45.0
        assert entry["opened_ts"] == "2026-04-18T12:00:00Z"

    def test_hysteresis_no_flap_when_between_thresholds(self, isolated_state, api):
        snaps, state = isolated_state
        _write_snapshot(snaps, {
            "amd_mi250_1": {"p90_wait": 20.0, "waiting": 10, "running": 5},
        })
        rc = qiw.run()
        assert rc == 0
        # 20m is above healthy (15) but below trigger (30) — no action
        assert api.opened == []
        assert api.closed == []

    def test_single_hot_job_below_min_waiting_skipped(self, isolated_state, api):
        snaps, state = isolated_state
        _write_snapshot(snaps, {
            "amd_mi250_1": {"p90_wait": 60.0, "waiting": 1, "running": 0},
        })
        rc = qiw.run()
        assert rc == 0
        # waiting=1 < MIN_WAITING_SAMPLES=3 → don't alert on noise
        assert api.opened == []

    def test_already_tracked_hot_queue_is_not_reopened(self, isolated_state, api):
        snaps, state = isolated_state
        # Seed state with an already-open issue for amd_mi250_1.
        state.write_text(json.dumps({"open": {"amd_mi250_1": {
            "number": 777, "peak_p90": 60.0, "opened_ts": "T0", "last_status_ts": "",
        }}, "last_run": ""}))
        _write_snapshot(snaps, {
            "amd_mi250_1": {"p90_wait": 60.0, "waiting": 10, "running": 0},
        })
        rc = qiw.run()
        assert rc == 0
        assert api.opened == []  # dedup — already tracked

    def test_hot_queue_posts_hourly_status_update(self, isolated_state, api):
        # The actual bug the user reported: an issue stays open for hours
        # with p90 > 30m but the watcher never comments, so there's no sign
        # the alert is still live. On every hourly run the watcher must drop
        # a status comment with the current metrics into the tracked issue.
        snaps, state = isolated_state
        state.write_text(json.dumps({"open": {"amd_mi325_2": {
            "number": 31, "peak_p90": 35.0, "opened_ts": "2026-04-18T04:00:00Z", "last_status_ts": "",
        }}, "last_run": ""}))
        _write_snapshot(snaps, {
            "amd_mi325_2": {"p90_wait": 37.9, "waiting": 8, "running": 7, "avg_wait": 20.3},
        }, ts="2026-04-18T12:00:00Z")
        rc = qiw.run()
        assert rc == 0
        # Exactly one comment to the tracked issue, not a new issue.
        assert api.opened == []
        assert api.closed == []
        assert len(api.commented) == 1
        assert api.assigned == [31]
        num, body = api.commented[0]
        assert num == 31
        # The comment must surface live metrics so readers can tell the
        # alert reflects the current snapshot, not stale opening data.
        assert "37.9" in body
        assert "Still elevated" in body
        # Peak rose from 35 → 37.9; state must record the new peak.
        persisted = json.loads(state.read_text())
        assert persisted["open"]["amd_mi325_2"]["peak_p90"] == 37.9
        assert persisted["open"]["amd_mi325_2"]["last_status_ts"] == "2026-04-18T12:00:00Z"

    def test_status_update_preserves_peak_when_p90_eases(self, isolated_state, api):
        # If current p90 is lower than the prior peak, state must KEEP the
        # higher peak so the comment can accurately report "easing" rather
        # than silently rewriting history.
        snaps, state = isolated_state
        state.write_text(json.dumps({"open": {"amd_mi325_2": {
            "number": 31, "peak_p90": 60.0, "opened_ts": "T0", "last_status_ts": "",
        }}, "last_run": ""}))
        _write_snapshot(snaps, {
            "amd_mi325_2": {"p90_wait": 45.0, "waiting": 5, "running": 4},
        })
        qiw.run()
        persisted = json.loads(state.read_text())
        assert persisted["open"]["amd_mi325_2"]["peak_p90"] == 60.0
        assert api.assigned == [31]
        # Comment body should call out easing + show both current and peak.
        assert len(api.commented) == 1
        body = api.commented[0][1]
        assert "45.0" in body
        assert "60.0" in body

    def test_legacy_int_state_still_posts_status_update(self, isolated_state, api):
        # A state file written before this change stored the bare issue
        # number. The first post-upgrade run must still post a status update
        # rather than silently skip because peak_p90 was missing.
        snaps, state = isolated_state
        state.write_text(json.dumps({"open": {"amd_mi325_2": 31}, "last_run": ""}))
        _write_snapshot(snaps, {
            "amd_mi325_2": {"p90_wait": 50.0, "waiting": 8, "running": 7},
        })
        qiw.run()
        assert api.assigned == [31]
        assert len(api.commented) == 1
        assert api.commented[0][0] == 31
        persisted = json.loads(state.read_text())
        assert persisted["open"]["amd_mi325_2"]["number"] == 31

    def test_closes_issue_when_queue_healthy(self, isolated_state, api):
        snaps, state = isolated_state
        state.write_text(json.dumps({"open": {"amd_mi250_1": {
            "number": 777, "peak_p90": 45.0, "opened_ts": "T0", "last_status_ts": "",
        }}, "suppressed": {}, "last_run": ""}))
        _write_snapshot(snaps, {
            "amd_mi250_1": {"p90_wait": 10.0, "waiting": 2, "running": 3},
        })
        rc = qiw.run()
        assert rc == 0
        assert api.assigned == [777]
        assert 777 in api.closed
        assert api.commented and api.commented[0][0] == 777
        persisted = json.loads(state.read_text())
        assert "amd_mi250_1" not in persisted["open"]

    def test_closes_stale_issue_after_24h_and_suppresses_reopen(self, isolated_state, api):
        snaps, state = isolated_state
        state.write_text(json.dumps({"open": {"amd_mi250_1": {
            "number": 777,
            "peak_p90": 45.0,
            "opened_ts": "2026-04-17T10:00:00Z",
            "last_status_ts": "",
        }}, "suppressed": {}, "last_run": ""}))
        _write_snapshot(snaps, {
            "amd_mi250_1": {"p90_wait": 50.0, "waiting": 10, "running": 3},
        }, ts="2026-04-18T12:30:00Z")
        rc = qiw.run()
        assert rc == 0
        assert api.opened == []
        assert api.assigned == [777]
        assert api.closed == [777]
        assert len(api.commented) == 1
        assert "Closing this queue-latency issue after 24h" in api.commented[0][1]
        persisted = json.loads(state.read_text())
        assert "amd_mi250_1" not in persisted["open"]
        assert persisted["suppressed"]["amd_mi250_1"]["last_number"] == 777

    def test_suppressed_queue_does_not_reopen_until_healthy(self, isolated_state, api):
        snaps, state = isolated_state
        state.write_text(json.dumps({
            "open": {},
            "suppressed": {"amd_mi250_1": {"closed_ts": "2026-04-18T12:30:00Z", "last_number": 777}},
            "last_run": "",
        }))
        _write_snapshot(snaps, {
            "amd_mi250_1": {"p90_wait": 55.0, "waiting": 10, "running": 1},
        }, ts="2026-04-18T13:30:00Z")
        qiw.run()
        assert api.opened == []
        assert api.closed == []
        persisted = json.loads(state.read_text())
        assert "amd_mi250_1" in persisted["suppressed"]

    def test_healthy_queue_clears_suppression(self, isolated_state, api):
        snaps, state = isolated_state
        state.write_text(json.dumps({
            "open": {},
            "suppressed": {"amd_mi250_1": {"closed_ts": "2026-04-18T12:30:00Z", "last_number": 777}},
            "last_run": "",
        }))
        _write_snapshot(snaps, {
            "amd_mi250_1": {"p90_wait": 10.0, "waiting": 1, "running": 2},
        }, ts="2026-04-18T15:00:00Z")
        qiw.run()
        persisted = json.loads(state.read_text())
        assert persisted["suppressed"] == {}

    def test_no_token_skips_mutations_but_persists_state(self, isolated_state, api, monkeypatch):
        snaps, state = isolated_state
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        _write_snapshot(snaps, {
            "amd_mi250_1": {"p90_wait": 60.0, "waiting": 10, "running": 0},
        })
        rc = qiw.run()
        assert rc == 0
        assert api.opened == []  # no token → no API call
        # state file should still exist
        assert state.exists()

    def test_missing_snapshot_returns_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.setattr(qiw, "SNAPSHOTS", tmp_path / "nope.jsonl", raising=False)
        monkeypatch.setattr(qiw, "STATE", tmp_path / "state.json", raising=False)
        monkeypatch.setenv("GITHUB_TOKEN", "fake")
        assert qiw.run() == 0
