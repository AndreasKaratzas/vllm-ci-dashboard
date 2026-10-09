"""Deployment retention protects serving/in-flight records and bounded authority."""

from __future__ import annotations

import json
import stat
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
import yaml

from vllm import prune_deployment_history as retention

NOW = datetime(2026, 10, 9, 23, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]
SITE = "https://andreaskaratzas.github.io/vllm-ci-dashboard/"


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def response(status, value=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(value).encode() if value is not None else b""
    result.headers.update(headers or {})
    return result


def deployments(count=22, *, native=False):
    return [{
        "id": identifier,
        "created_at": (NOW - timedelta(days=1, minutes=count - identifier)).isoformat(),
        "environment": "github-pages" if native else "test",
        "sha": f"{identifier:040x}", "ref": "gh-pages", "task": "deploy",
        "transient_environment": False, "production_environment": False,
        "creator": {"login": "github-pages[bot]", "type": "Bot"},
    } for identifier in range(1, count + 1)]


class Session:
    def __init__(self, records=None, *, native=False, clock=None):
        self.records = {row["id"]: row for row in (records or deployments(native=native))}
        self.clock = clock or Clock()
        self.statuses = {
            identifier: [{
                "id": identifier * 100, "created_at": (NOW - timedelta(hours=2)).isoformat(),
                "state": "success" if native else "inactive",
                "environment": "github-pages" if native else "test",
                "environment_url": SITE,
            }] for identifier in self.records
        }
        self.head = self.records[max(self.records)]["sha"]
        self.calls = []
        self.before_request = None
        self.delete_responses = {}
        self.post_response = None

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path.split("/repos/owner/repo", 1)[1]
        self.calls.append((method, path, deepcopy(kwargs), self.clock.value))
        if self.before_request is not None:
            intercepted = self.before_request(method, path, kwargs)
            if intercepted is not None:
                return intercepted
        if path == "/git/ref/heads/gh-pages":
            return response(200, {"ref": "refs/heads/gh-pages", "object": {"type": "commit", "sha": self.head}})
        if method == "GET":
            page = int(parse_qs(urlsplit(url).query)["page"][0])
            if path == "/deployments":
                rows = sorted(self.records.values(), key=lambda row: row["id"], reverse=True)
            else:
                identifier = int(path.split("/")[2])
                if identifier not in self.records:
                    return response(404)
                rows = self.statuses[identifier]
            return response(200, rows[(page - 1) * 100:page * 100])
        identifier = int(path.split("/")[2])
        if method == "POST":
            if self.post_response is not None:
                return self.post_response
            latest = self.statuses[identifier][-1]
            self.statuses[identifier].append({
                **latest, "id": latest["id"] + 1, "state": "inactive",
                "created_at": NOW.isoformat(),
            })
            return response(201, self.statuses[identifier][-1])
        status = self.delete_responses.get(identifier, 204)
        if status == 204:
            del self.records[identifier]
        return response(status)


def run(tmp_path, *, apply=False, records=None, native=False, session=None, **kwargs):
    session = session or Session(records, native=native)
    github = retention.GitHub(
        "owner/repo", "private-token", session=session,
        monotonic=session.clock.monotonic, sleep=session.clock.sleep,
    )
    journal = retention.Journal(tmp_path / "journal.jsonl")
    try:
        report = retention.run_retention(github, journal, apply=apply, started_at=NOW, **kwargs)
    finally:
        journal.close()
    return report, session


def test_default_preview_keeps_newest_twenty_and_never_writes(tmp_path):
    report, session = run(tmp_path)
    assert report["counts"] == {"would_delete": 2}
    assert report["newest_kept_ids"] == list(range(22, 2, -1))
    assert report["remaining_count"] == 22
    assert all(method == "GET" for method, *_ in session.calls)
    journal = tmp_path / "journal.jsonl"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    assert events[0]["event"] == "inventory"
    assert events[-1]["event"] == "summary"
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    assert "private-token" not in journal.read_text()


def test_inactive_old_records_deleted_and_every_recent_id_retained(tmp_path):
    report, session = run(tmp_path, apply=True)
    assert report["counts"] == {"deleted": 2}
    assert set(session.records) == set(range(3, 23))
    writes = [(method, path, timestamp) for method, path, _, timestamp in session.calls if method != "GET"]
    assert [path for _, path, _ in writes] == ["/deployments/2", "/deployments/1"]
    assert writes[1][2] - writes[0][2] >= 1


@pytest.mark.parametrize("state", ["success", "queued", "pending", "in_progress", "failure", "error", "mystery", None])
def test_active_inflight_and_unknown_states_are_not_mutated(tmp_path, state):
    session = Session()
    for identifier in (1, 2):
        session.statuses[identifier][0]["state"] = state
    report, session = run(tmp_path, apply=True, session=session)
    assert report["remaining_count"] == 22
    assert all(method == "GET" for method, *_ in session.calls)


def test_superseded_native_pages_success_retired_with_current_serving_proof(tmp_path):
    report, session = run(tmp_path, apply=True, native=True)
    assert report["counts"] == {"deleted": 2, "retired": 2}
    assert report["remaining_count"] == 20
    writes = [(method, path, kwargs["json"], when) for method, path, kwargs, when in session.calls if method != "GET"]
    assert [(method, path) for method, path, _, _ in writes] == [
        ("POST", "/deployments/2/statuses"), ("DELETE", "/deployments/2"),
        ("POST", "/deployments/1/statuses"), ("DELETE", "/deployments/1"),
    ]
    assert all(b[3] - a[3] >= 1 for a, b in zip(writes, writes[1:]))
    assert all(payload["auto_inactive"] is False and payload["state"] == "inactive"
               for method, _, payload, _ in writes if method == "POST")
    assert session.statuses[22][0]["state"] == "success"
    assert len(session.statuses[22]) == 1
    assert all("/22/statuses" != path for method, path, *_ in writes)
    events = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    intents = [event for event in events if event["event"] == "retire_intent"]
    assert all(event["serving_id"] == 22 and event["serving_sha"] == session.head for event in intents)


def test_superseded_native_pages_preview_is_completely_read_only(tmp_path):
    report, session = run(tmp_path, native=True)
    assert report["counts"] == {"would_delete": 2, "would_retire": 2}
    assert all(method == "GET" for method, *_ in session.calls)


@pytest.mark.parametrize("mutation", [
    "wrong_ref", "production", "transient", "other_creator",
    "other_site", "other_environment", "head_changed", "serving_pending",
])
def test_retirement_fails_closed_when_serving_or_candidate_proof_is_wrong(tmp_path, mutation):
    session = Session(native=True)
    for identifier in (1, 2):
        row = session.records[identifier]
        status = session.statuses[identifier][0]
        if mutation == "wrong_ref": row["ref"] = "main"
        elif mutation == "production": row["production_environment"] = True
        elif mutation == "transient": row["transient_environment"] = True
        elif mutation == "other_creator": row["creator"]["login"] = "someone"
        elif mutation == "other_site": status["environment_url"] = "https://other.invalid/"
        elif mutation == "other_environment": status["environment"] = "production"
    if mutation == "head_changed": session.head = "f" * 40
    if mutation == "serving_pending": session.statuses[22][0]["state"] = "pending"
    report, session = run(tmp_path, apply=True, session=session)
    assert report["remaining_count"] == 22
    assert all(method == "GET" for method, *_ in session.calls)


@pytest.mark.parametrize("changed", ["head", "serving", "candidate"])
def test_mutation_after_initial_proof_prevents_retirement(tmp_path, changed):
    session = Session(native=True)
    count = 0
    def intercept(method, path, kwargs):
        nonlocal count
        if path == "/git/ref/heads/gh-pages":
            count += 1
            if count == 2:
                if changed == "head": session.head = "e" * 40
                elif changed == "serving": session.statuses[22][0]["state"] = "pending"
                else:
                    for identifier in (1, 2):
                        session.statuses[identifier][0]["state"] = "in_progress"
        return None
    session.before_request = intercept
    report, session = run(tmp_path, apply=True, session=session)
    assert report["remaining_count"] == 22
    assert all(method == "GET" for method, *_ in session.calls)


def test_new_records_never_enter_original_candidates(tmp_path):
    session = Session()
    def intercept(method, path, kwargs):
        if method == "DELETE" and 23 not in session.records:
            session.records[23] = {**deepcopy(session.records[22]), "id": 23, "created_at": NOW.isoformat()}
        return None
    session.before_request = intercept
    report, session = run(tmp_path, apply=True, session=session)
    assert report["remaining_count"] == 21
    assert set(session.records) == set(range(3, 24))


def test_new_records_outside_twenty_still_protected(tmp_path):
    records = deployments(22)
    for row in records:
        row["created_at"] = NOW.isoformat()
    report, session = run(tmp_path, apply=True, records=records)
    assert report["counts"] == {"new": 2}
    assert all(method == "GET" for method, *_ in session.calls)


def test_old_current_success_outside_recent_twenty_remains_active(tmp_path):
    session = Session(native=True)
    session.head = session.records[1]["sha"]
    report, session = run(tmp_path, apply=True, session=session)
    assert report["remaining_count"] == 22
    assert all(method == "GET" for method, *_ in session.calls)


def test_same_commit_redeployment_can_retire_distinct_strictly_older_ids(tmp_path):
    session = Session(native=True)
    for identifier in (1, 2):
        session.records[identifier]["sha"] = session.head
    report, session = run(tmp_path, apply=True, session=session)
    assert report["counts"] == {"deleted": 2, "retired": 2}
    assert len(session.records) == 20
    assert session.statuses[22][0]["state"] == "success"
    assert all(not (method == "POST" and path == "/deployments/22/statuses")
               for method, path, *_ in session.calls)


@pytest.mark.parametrize("terminal_state", ["failure", "error"])
def test_terminal_failed_pages_records_retire_only_with_newer_serving_success(tmp_path, terminal_state):
    session = Session(native=True)
    for identifier in (1, 2):
        session.statuses[identifier][0]["state"] = terminal_state
    report, session = run(tmp_path, apply=True, session=session)
    assert report["counts"] == {"deleted": 2, "retired": 2}
    assert len(session.records) == 20
    assert session.statuses[22][0]["state"] == "success"
    posts = [call for call in session.calls if call[0] == "POST"]
    assert {call[1] for call in posts} == {"/deployments/1/statuses", "/deployments/2/statuses"}
    assert all(call[2]["json"]["auto_inactive"] is False for call in posts)


@pytest.mark.parametrize("terminal_state", ["failure", "error"])
def test_terminal_failed_pages_records_stay_when_serving_proof_does_not_match(tmp_path, terminal_state):
    session = Session(native=True)
    session.head = "f" * 40
    for identifier in (1, 2):
        session.statuses[identifier][0]["state"] = terminal_state
    report, session = run(tmp_path, apply=True, session=session)
    assert report["remaining_count"] == 22
    assert report["counts"] == {f"protected_{terminal_state}": 2}
    assert all(method == "GET" for method, *_ in session.calls)


@pytest.mark.parametrize("state", ["queued", "pending", "in_progress", "unknown"])
def test_newer_serving_success_never_grants_retirement_of_nonterminal_pages(tmp_path, state):
    session = Session(native=True)
    for identifier in (1, 2):
        session.statuses[identifier][0]["state"] = state
    report, session = run(tmp_path, apply=True, session=session)
    assert report["remaining_count"] == 22
    assert all(method == "GET" for method, *_ in session.calls)


@pytest.mark.parametrize("http_status,outcome", [(404, "already_absent"), (422, "protected_delete_rejected")])
def test_delete_absence_and_active_rejection_never_grant_status_writes(tmp_path, http_status, outcome):
    session = Session()
    session.delete_responses = {1: http_status, 2: http_status}
    report, session = run(tmp_path, apply=True, session=session)
    assert report["counts"] == {outcome: 2}
    assert all(method != "POST" for method, *_ in session.calls)


def test_write_bound_defers_remaining_original_candidates(tmp_path):
    report, session = run(tmp_path, apply=True, records=deployments(30), max_deletions=2)
    assert report["counts"] == {"deleted": 2, "deferred": 8}
    assert len(session.records) == 28


def test_full_inventory_pagination_preserves_twenty_across_page_boundary(tmp_path):
    report, session = run(tmp_path, records=deployments(205), max_deletions=1)
    assert report["initial_count"] == 205
    assert report["newest_kept_ids"] == list(range(205, 185, -1))
    assert report["counts"] == {"would_delete": 1, "deferred": 184}
    inventory_calls = [call for call in session.calls if call[1] == "/deployments"]
    assert len(inventory_calls) == 6


def test_duplicate_pagination_stops_before_any_mutation(tmp_path):
    session = Session(deployments(101))
    # Deliberately place an identifier on both pages through the list response.
    original = session.request
    def request(method, url, **kwargs):
        if method == "GET" and url.endswith("page=2"):
            return response(200, [session.records[101]])
        return original(method, url, **kwargs)
    session.request = request
    with pytest.raises(retention.RetentionError, match="pagination changed"):
        run(tmp_path, apply=True, session=session)
    assert all(method == "GET" for method, *_ in session.calls)


def test_rate_limit_wait_is_bounded_and_delete_retries_remain_serial():
    clock = Clock()
    session = Session(clock=clock)
    replies = [response(429, headers={"Retry-After": "2"}), response(204)]
    session.before_request = lambda *_: replies.pop(0)
    github = retention.GitHub("owner/repo", "secret", session=session,
                              monotonic=clock.monotonic, sleep=clock.sleep)
    assert github.request("DELETE", "/deployments/1").status_code == 204
    assert clock.value == 2
    assert session.calls[1][3] - session.calls[0][3] >= 1
    assert all(call[2]["allow_redirects"] is False for call in session.calls)


def test_rate_limit_larger_than_deadline_stops_without_retry():
    clock = Clock()
    session = Session(clock=clock)
    session.before_request = lambda *_: response(429, headers={"Retry-After": "31"})
    github = retention.GitHub("owner/repo", "secret", timeout_seconds=30, session=session,
                              monotonic=clock.monotonic, sleep=clock.sleep)
    with pytest.raises(retention.RetentionError, match="time budget"):
        github.request("DELETE", "/deployments/1")
    assert len(session.calls) == 1


def test_status_transport_ambiguity_never_retries_post():
    session = Session()
    def forbidden(*_):
        raise requests.Timeout("private-token must never enter an error")
    session.before_request = forbidden
    github = retention.GitHub("owner/repo", "private-token", session=session)
    with pytest.raises(retention.RetentionError, match="ambiguous") as caught:
        github.request("POST", "/deployments/1/statuses", payload={
            "state": "inactive", "auto_inactive": False, "environment": "github-pages",
            "description": "Superseded Pages deployment outside the newest 20 records",
        })
    assert "private-token" not in str(caught.value)
    assert len(session.calls) == 1


@pytest.mark.parametrize("repository", ["owner/repo/other", "owner/repo?token=x", "../repo", "https://host/repo", "owner/ repo"])
def test_repository_cannot_escape_api_authority(repository):
    with pytest.raises(retention.RetentionError):
        retention.normalize_repository(repository)


def test_arbitrary_status_writes_and_repository_requests_are_forbidden():
    github = retention.GitHub("owner/repo", "secret", session=Session())
    for method, path, payload in [
        ("POST", "/deployments/1/statuses", {"state": "inactive", "auto_inactive": True}),
        ("POST", "/deployments/1/statuses", {"state": "success"}),
        ("DELETE", "/git/ref/heads/gh-pages", None),
        ("GET", "/deployments/../contents", None),
    ]:
        with pytest.raises(retention.RetentionError, match="authority"):
            github.request(method, path, payload=payload)


def test_journal_cannot_overwrite_or_follow_symlink(tmp_path):
    original = tmp_path / "existing"
    original.write_text("preserve")
    symlink = tmp_path / "link"
    symlink.symlink_to(original)
    for path in (original, symlink, ROOT / "retention.jsonl"):
        with pytest.raises(retention.RetentionError):
            retention.Journal(path)
    assert original.read_text() == "preserve"


def test_workflow_has_main_only_daily_serial_bounded_minimal_authority():
    workflow = yaml.safe_load((ROOT / ".github/workflows/deployment-retention.yml").read_text())
    trigger = workflow.get("on", workflow.get(True))
    assert trigger["schedule"] == [{"cron": "47 4 * * *"}]
    assert trigger["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    assert trigger["workflow_dispatch"]["inputs"]["dry_run"]["type"] == "boolean"
    assert workflow["permissions"] == {"contents": "read", "deployments": "write"}
    assert workflow["concurrency"] == {"group": "deployment-record-retention", "cancel-in-progress": False}
    job = workflow["jobs"]["retain"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["timeout-minutes"] == 25
    checkout = job["steps"][0]
    assert checkout["uses"] == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"  # pinned action revision
    assert checkout["with"]["persist-credentials"] is False
    prune = job["steps"][-1]
    assert prune["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "DRY_RUN": "${{ github.event_name == 'workflow_dispatch' && inputs.dry_run }}",
    }
    assert 'args=()' in prune["run"]
    assert 'if [[ "$DRY_RUN" != \'true\' ]]; then' in prune["run"]
    assert 'args+=(--apply)' in prune["run"]
    text = (ROOT / ".github/workflows/deployment-retention.yml").read_text()
    assert "--max-deletions 250 --timeout-seconds 1200" in text
    assert "BUILDKITE" not in text
    assert "upload-artifact" not in text
    assert "contents: write" not in text
