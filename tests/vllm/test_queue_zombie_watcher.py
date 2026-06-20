"""Unit tests for scripts/vllm/queue_zombie_watcher.py."""

from __future__ import annotations

import json

import pytest

from vllm import queue_zombie_watcher as qzw


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    jobs = tmp_path / "queue_jobs.json"
    state = tmp_path / "open_queue_zombie_issues.json"
    monkeypatch.setattr(qzw, "JOBS", jobs, raising=False)
    monkeypatch.setattr(qzw, "STATE", state, raising=False)
    return jobs, state


def _write_jobs(path, *, ts="2026-04-20T23:55:00Z", pending=None, running=None):
    path.write_text(json.dumps({
        "ts": ts,
        "zombie_threshold_min": 240,
        "pending": pending or [],
        "running": running or [],
    }))


def _job(queue, state, *, wait_min=None, run_min=None, analysis_excluded=False, build=100):
    return {
        "queue": queue,
        "state": state,
        "wait_min": wait_min,
        "run_min": run_min,
        "analysis_excluded": analysis_excluded,
        "pipeline": "amd-ci",
        "build": build,
        "branch": "main",
        "name": "mi250_1: zombie",
        "url": f"https://buildkite.com/vllm/amd-ci/builds/{build}",
    }


class _Recorder:
    def __init__(self):
        self.opened = []
        self.updated = []
        self.closed = []
        self.assigned = []
        self._next = 3000

    def open_issue(self, token, repo, title, body):
        number = self._next
        self._next += 1
        self.opened.append((number, title, body))
        return number

    def update_issue(self, token, repo, number, title, body):
        self.updated.append((number, title, body))

    def close_issue(self, token, repo, number):
        self.closed.append(number)

    def assign(self, token, repo, number):
        self.assigned.append(number)


@pytest.fixture
def api(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(qzw, "_open_issue", rec.open_issue)
    monkeypatch.setattr(qzw, "_update_issue", rec.update_issue)
    monkeypatch.setattr(qzw, "_close_issue", rec.close_issue)
    monkeypatch.setattr(qzw, "_ensure_owner_assigned", rec.assign)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    return rec


class TestRun:
    def test_opens_issue_for_amd_zombie_jobs(self, isolated_state, api):
        jobs, state = isolated_state
        _write_jobs(jobs, pending=[_job("amd_mi250_1", "scheduled", wait_min=275.0, analysis_excluded=True)])

        assert qzw.run() == 0
        assert len(api.opened) == 1
        number, title, body = api.opened[0]
        assert "amd_mi250_1" in title
        assert "275.0m" in body
        assert "cc @AndreasKaratzas for visibility." in body
        persisted = json.loads(state.read_text())
        assert persisted["open"]["amd_mi250_1"]["number"] == number

    def test_updates_existing_issue_without_spam_when_fingerprint_changes(self, isolated_state, api):
        jobs, state = isolated_state
        state.write_text(json.dumps({
            "open": {
                "amd_mi250_1": {
                    "number": 77,
                    "opened_ts": "2026-04-20T20:00:00Z",
                    "last_fingerprint": "old",
                }
            },
            "last_run": "",
        }))
        _write_jobs(jobs, running=[_job("amd_mi250_1", "running", run_min=300.0, analysis_excluded=True, build=123)])

        assert qzw.run() == 0
        assert api.opened == []
        assert len(api.updated) == 1
        assert api.assigned == [77]
        assert api.updated[0][0] == 77

    def test_skips_update_when_issue_body_would_be_identical(self, isolated_state, api):
        jobs, state = isolated_state
        payload = {"running": [_job("amd_mi250_1", "running", run_min=300.0, analysis_excluded=True, build=123)]}
        _write_jobs(jobs, **payload)
        fingerprint = qzw._fingerprint("amd_mi250_1", payload["running"], "2026-04-20T23:55:00Z")
        state.write_text(json.dumps({
            "open": {
                "amd_mi250_1": {
                    "number": 77,
                    "opened_ts": "2026-04-20T20:00:00Z",
                    "last_fingerprint": fingerprint,
                }
            },
            "last_run": "",
        }))

        assert qzw.run() == 0
        assert api.updated == []
        assert api.opened == []

    def test_closes_issue_when_queue_clears(self, isolated_state, api):
        jobs, state = isolated_state
        state.write_text(json.dumps({
            "open": {
                "amd_mi250_1": {
                    "number": 77,
                    "opened_ts": "2026-04-20T20:00:00Z",
                    "last_fingerprint": "old",
                }
            },
            "last_run": "",
        }))
        _write_jobs(jobs)

        assert qzw.run() == 0
        assert api.assigned == [77]
        assert api.closed == [77]
        persisted = json.loads(state.read_text())
        assert persisted["open"] == {}

    def test_ignores_non_amd_queues_and_subthreshold_jobs(self, isolated_state, api):
        jobs, state = isolated_state
        _write_jobs(
            jobs,
            pending=[
                _job("gpu_1_queue", "scheduled", wait_min=300.0, analysis_excluded=True),
                _job("amd_mi250_1", "scheduled", wait_min=100.0, analysis_excluded=False),
            ],
        )

        assert qzw.run() == 0
        assert api.opened == []
