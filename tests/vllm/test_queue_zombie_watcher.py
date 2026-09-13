"""Unit tests for scripts/vllm/queue_zombie_watcher.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from vllm import queue_zombie_watcher as qzw


def test_issue_writes_are_restricted_to_dashboard_repo():
    qzw._validate_target_repo(qzw.DASHBOARD_REPO)
    with pytest.raises(RuntimeError, match="restricted"):
        qzw._validate_target_repo("vllm-project/vllm")


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
        self.owned = []
        self.lookup_ok = True
        self.update_ok = True
        self.close_ok = True
        self.lookups = 0
        self._next = 3000

    def open_issue(self, token, repo, title, body):
        number = self._next
        self._next += 1
        self.opened.append((number, title, body))
        return number

    def update_issue(self, token, repo, number, title, body):
        self.updated.append((number, title, body))
        return self.update_ok

    def close_issue(self, token, repo, number):
        self.closed.append(number)
        return self.close_ok

    def assign(self, token, repo, number):
        self.assigned.append(number)

    def list_owned(self, token, repo, **_kwargs):
        self.lookups += 1
        return list(self.owned) if self.lookup_ok else None


def _owned_remote(queue, number, *, legacy=False, created_at="2026-04-20T20:00:00Z"):
    return {
        "number": number,
        "queue": queue,
        "created_at": created_at,
        "labels": [{"name": name} for name in qzw.OWNED_LABELS],
        "legacy": legacy,
    }


def _raw_issue(
    queue="amd_mi250_1",
    number=77,
    *,
    marker=True,
    labels=None,
    title=None,
):
    jobs = [
        _job(
            queue,
            "scheduled",
            wait_min=300.0,
            analysis_excluded=True,
        )
    ]
    body = qzw._issue_body(
        queue,
        jobs,
        "2026-04-20T20:00:00Z",
        "2026-04-20T23:55:00Z",
        "https://github.test/run",
        "AndreasKaratzas",
    )
    if not marker:
        body = body.replace(f"{qzw.OWNERSHIP_MARKER}\n", "", 1)
    return {
        "number": number,
        "title": title or qzw._issue_title(queue, jobs),
        "body": body,
        "labels": (
            [{"name": name} for name in qzw.OWNED_LABELS]
            if labels is None
            else labels
        ),
        "created_at": "2026-04-20T20:00:00Z",
    }


def test_tracked_issue_lookup_uses_direct_number_and_zero_list_calls(monkeypatch):
    calls = []
    issue = {**_raw_issue(number=77), "state": "open"}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return issue

    def get(url, *, headers, timeout):
        calls.append(url)
        return Response()

    monkeypatch.setattr(qzw.requests, "get", get)

    rows = qzw._list_owned_open_issues(
        "token",
        qzw.DASHBOARD_REPO,
        include_recovery=False,
        tracked_numbers=(77,),
    )

    assert [row["number"] for row in rows] == [77]
    assert calls == [f"{qzw.GH_API}/repos/{qzw.DASHBOARD_REPO}/issues/77"]


@pytest.fixture
def api(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(qzw, "_open_issue", rec.open_issue)
    monkeypatch.setattr(qzw, "_update_issue", rec.update_issue)
    monkeypatch.setattr(qzw, "_close_issue", rec.close_issue)
    monkeypatch.setattr(qzw, "_ensure_owner_assigned", rec.assign)
    monkeypatch.setattr(qzw, "_list_owned_open_issues", rec.list_owned)
    monkeypatch.setattr(
        qzw,
        "_utc_now",
        lambda: datetime(2026, 4, 21, 0, 0, tzinfo=timezone.utc),
    )
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
        assert body.splitlines()[0] == qzw.OWNERSHIP_MARKER
        assert "GitHub assignee: AndreasKaratzas." in body
        assert "@AndreasKaratzas" not in body
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
        api.owned = [_owned_remote("amd_mi250_1", 77)]

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
        api.owned = [_owned_remote("amd_mi250_1", 77)]

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
        api.owned = [_owned_remote("amd_mi250_1", 77)]

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

    def test_recovers_legacy_issue_and_closes_marker_owned_duplicate(
        self, isolated_state, api
    ):
        jobs, state = isolated_state
        _write_jobs(
            jobs,
            pending=[
                _job(
                    "amd_mi250_1",
                    "scheduled",
                    wait_min=300.0,
                    analysis_excluded=True,
                )
            ],
        )
        api.owned = [
            _owned_remote("amd_mi250_1", 70, legacy=True),
            _owned_remote("amd_mi250_1", 71),
        ]

        assert qzw.run() == 0

        assert api.opened == []
        assert api.closed == [71]
        assert [row[0] for row in api.updated] == [70]
        assert api.updated[0][2].splitlines()[0] == qzw.OWNERSHIP_MARKER
        assert json.loads(state.read_text())["open"]["amd_mi250_1"]["number"] == 70

    def test_failed_update_does_not_advance_tracked_fingerprint(
        self, isolated_state, api
    ):
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
        _write_jobs(
            jobs,
            running=[
                _job(
                    "amd_mi250_1",
                    "running",
                    run_min=300.0,
                    analysis_excluded=True,
                )
            ],
        )
        api.owned = [_owned_remote("amd_mi250_1", 77)]
        api.update_ok = False

        assert qzw.run() == 0

        assert json.loads(state.read_text())["open"]["amd_mi250_1"] == {
            "number": 77,
            "opened_ts": "2026-04-20T20:00:00Z",
            "last_fingerprint": "old",
        }

    def test_failed_recovered_update_does_not_claim_issue_in_state(
        self, isolated_state, api
    ):
        jobs, state = isolated_state
        _write_jobs(
            jobs,
            running=[
                _job(
                    "amd_mi250_1",
                    "running",
                    run_min=300.0,
                    analysis_excluded=True,
                )
            ],
        )
        api.owned = [_owned_remote("amd_mi250_1", 77)]
        api.update_ok = False

        assert qzw.run() == 0

        assert json.loads(state.read_text())["open"] == {}

    def test_failed_close_keeps_tracked_issue_for_retry(self, isolated_state, api):
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
        api.owned = [_owned_remote("amd_mi250_1", 77)]
        api.close_ok = False

        assert qzw.run() == 0

        assert json.loads(state.read_text())["open"]["amd_mi250_1"]["number"] == 77

    def test_incomplete_recovery_lookup_refuses_all_mutations_and_state_change(
        self, isolated_state, api
    ):
        jobs, state = isolated_state
        _write_jobs(
            jobs,
            pending=[
                _job(
                    "amd_mi250_1",
                    "scheduled",
                    wait_min=300.0,
                    analysis_excluded=True,
                )
            ],
        )
        original = '{"open": {}, "last_run": "old"}\n'
        state.write_text(original)
        api.lookup_ok = False

        assert qzw.run() == 0

        assert api.opened == []
        assert api.updated == []
        assert api.closed == []
        assert state.read_text() == original

    @pytest.mark.parametrize(
        "snapshot_ts",
        ["2026-04-20T17:59:59Z", "2026-04-21T00:15:01Z", "invalid"],
        ids=["stale", "future", "invalid"],
    )
    def test_untrustworthy_snapshot_refuses_mutations_and_preserves_state(
        self, isolated_state, api, snapshot_ts
    ):
        jobs, state = isolated_state
        _write_jobs(
            jobs,
            ts=snapshot_ts,
            pending=[
                _job(
                    "amd_mi250_1",
                    "scheduled",
                    wait_min=300.0,
                    analysis_excluded=True,
                )
            ],
        )
        original = json.dumps({
            "open": {
                "amd_mi250_1": {
                    "number": 77,
                    "opened_ts": "2026-04-20T20:00:00Z",
                    "last_fingerprint": "old",
                }
            },
            "last_run": "old",
        })
        state.write_text(original)

        assert qzw.run() == 0

        assert api.lookups == 0
        assert api.opened == []
        assert api.updated == []
        assert api.closed == []
        assert state.read_text() == original


def test_owned_issue_requires_exact_marker_or_strict_legacy_identity():
    marked = _raw_issue(labels=[])
    normalized = qzw._owned_queue_issue(marked)
    assert normalized == {
        "number": 77,
        "queue": "amd_mi250_1",
        "created_at": "2026-04-20T20:00:00Z",
        "labels": [],
        "legacy": False,
    }

    legacy = _raw_issue(marker=False)
    assert qzw._owned_queue_issue(legacy)["legacy"] is True

    missing_label = _raw_issue(
        marker=False,
        labels=[{"name": qzw.LABEL}, {"name": qzw.AUTOMATED_LABEL}],
    )
    assert qzw._owned_queue_issue(missing_label) is None

    wrong_title = _raw_issue(marker=False, title="Unrelated queue issue")
    assert qzw._owned_queue_issue(wrong_title) is None

    embedded_marker = _raw_issue(labels=[])
    embedded_marker["body"] = embedded_marker["body"].replace(
        qzw.OWNERSHIP_MARKER,
        f"text {qzw.OWNERSHIP_MARKER}",
        1,
    )
    assert qzw._owned_queue_issue(embedded_marker) is None


def test_owned_issue_lookup_fails_closed_at_owner_label_page_cap(monkeypatch):
    calls = []
    page = [
        {"number": number, "title": "foreign", "body": ""}
        for number in range(1, 101)
    ]

    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    def fake_get(url, *, headers, params, timeout):
        calls.append(params)
        return Response(page)

    monkeypatch.setattr(qzw.requests, "get", fake_get)

    assert qzw._list_owned_open_issues("token", qzw.DASHBOARD_REPO) is None
    assert calls == [
        {
            "state": "open",
            "labels": qzw.LABEL,
            "sort": "updated",
            "direction": "desc",
            "per_page": 100,
            "page": 1,
        }
    ]


def test_state_write_uses_atomic_replace(isolated_state, monkeypatch):
    _, state = isolated_state
    replacements = []
    real_replace = qzw.os.replace

    def tracked_replace(source, destination):
        replacements.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(qzw.os, "replace", tracked_replace)
    qzw._write_state({"open": {}, "last_run": "now"})

    assert len(replacements) == 1
    assert replacements[0][1] == state
    persisted = json.loads(state.read_text())
    assert persisted["open"] == {}
    assert persisted["last_run"] == "now"
    assert persisted["publication_retention"]["complete_relative_to_source"] is True
    assert list(state.parent.glob(f".{state.name}.*")) == []
