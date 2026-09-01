from __future__ import annotations

from vllm.ci import managed_issue
from vllm.ci.managed_issue import (
    DASHBOARD_REPO,
    GitHubIssueClient,
    reconcile_managed_issue,
)


MARKER = "<!-- exact-managed-alert:v1 -->"
LABEL_SPECS = [
    ("automated", "123456", "Managed by automation"),
    ("workstream:dev", "654321", "Development work"),
]


class _Response:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


def _reconcile(
    state,
    client,
    *,
    active=True,
    fingerprint="fingerprint",
    content_fingerprint=None,
):
    return reconcile_managed_issue(
        state,
        active=active,
        fingerprint=fingerprint,
        content_fingerprint=content_fingerprint,
        title="Managed alert",
        body="Current evidence",
        ownership_marker=MARKER,
        recovery_body="Recovered",
        observed_at="2026-07-28T12:00:00Z",
        label_specs=LABEL_SPECS,
        client=client,
    )


def test_client_finds_exact_marker_across_paginated_open_issues(monkeypatch):
    first_page = [
        {
            "number": index + 1,
            "body": f"not an exact marker: {MARKER}",
        }
        for index in range(100)
    ]
    second_page = [
        {"number": 222, "body": f"heading\n{MARKER}\nbody"},
        {
            "number": 111,
            "body": f"{MARKER}\nolder duplicate",
        },
        {
            "number": 99,
            "body": MARKER,
            "pull_request": {"url": "https://api.github.test/pulls/99"},
        },
    ]
    calls = []

    def fake_get(url, *, headers, params, timeout):
        calls.append((url, params))
        payload = first_page if params["page"] == 1 else second_page
        return _Response(200, payload)

    monkeypatch.setattr(managed_issue.requests, "get", fake_get)
    client = GitHubIssueClient("token", DASHBOARD_REPO)

    assert client.find_open_issues(MARKER) == [111, 222]
    assert client.find_open_issue(MARKER) == 111
    assert client.find_open_issue(MARKER) == 111
    assert [params["page"] for _, params in calls] == [1, 2]
    assert all(params["state"] == "open" for _, params in calls)
    assert all(params["per_page"] == 100 for _, params in calls)


def test_client_issue_state_requires_marker_on_its_own_line(monkeypatch):
    payloads = [
        {"state": "open", "body": f"prose containing {MARKER} is not ownership"},
        {"state": "open", "body": f"heading\n{MARKER}\nbody"},
    ]

    def fake_get(url, *, headers, timeout):
        return _Response(200, payloads.pop(0))

    monkeypatch.setattr(managed_issue.requests, "get", fake_get)
    client = GitHubIssueClient("token", DASHBOARD_REPO)

    assert client.issue_state(41, MARKER) == "foreign"
    assert client.issue_state(42, MARKER) == "open"


def test_client_adds_managed_labels_without_replacing_existing_labels(monkeypatch):
    posts = []

    def fake_post(url, *, headers, json, timeout):
        posts.append((url, json))
        return _Response(200, [])

    monkeypatch.setattr(managed_issue.requests, "post", fake_post)
    client = GitHubIssueClient("token", DASHBOARD_REPO)
    monkeypatch.setattr(client, "ensure_label", lambda *args: True)

    assert client.ensure_issue_labels(42, LABEL_SPECS)
    assert posts == [
        (
            f"{managed_issue.GH_API}/repos/{DASHBOARD_REPO}/issues/42/labels",
            {"labels": ["automated", "workstream:dev"]},
        )
    ]


class _RecoveringClient:
    def __init__(self):
        self.events = []

    def find_open_issue(self, ownership_marker):
        self.events.append(("find", ownership_marker))
        return 42

    def ensure_issue_labels(self, number, label_specs):
        self.events.append(("labels", number, label_specs))
        return True

    def ensure_owner_assigned(self, number):
        self.events.append(("assign", number))
        return True

    def update_issue(self, number, title, body):
        self.events.append(("update", number, title, body))
        return True

    def open_issue(self, title, body, label_specs):
        raise AssertionError("recovery must happen before opening a duplicate")


class _TrackedClient:
    def __init__(self):
        self.labels = []
        self.updated = []

    def issue_state(self, number, ownership_marker):
        return "open"

    def ensure_issue_labels(self, number, label_specs):
        self.labels.append((number, label_specs))
        return True

    def ensure_owner_assigned(self, number):
        return True

    def update_issue(self, number, title, body):
        self.updated.append(number)
        return True


class _SiblingAwareClient(_TrackedClient):
    def __init__(self, open_numbers, *, states=None, close_result=True):
        super().__init__()
        self.open_numbers = open_numbers
        self.states = dict(states or {})
        self.close_result = close_result
        self.state_reads = []
        self.closed = []
        self.opened = []

    def find_open_issues(self, ownership_marker):
        return self.open_numbers

    def issue_state(self, number, ownership_marker):
        self.state_reads.append(number)
        return self.states.get(number, "open")

    def close_issue(self, number):
        self.closed.append(number)
        return self.close_result

    def open_issue(self, title, body, label_specs):
        self.opened.append((title, body, label_specs))
        return 99


class _FailedSiblingLookupClient(_SiblingAwareClient):
    def find_open_issues(self, ownership_marker):
        raise RuntimeError("temporary list failure")


class _RetryingUpdateClient(_TrackedClient):
    def __init__(self, *, recovered_number=None):
        super().__init__()
        self.recovered_number = recovered_number
        self.update_results = [False, True]
        self.finds = 0

    def find_open_issue(self, ownership_marker):
        self.finds += 1
        return self.recovered_number

    def update_issue(self, number, title, body):
        self.updated.append(number)
        return self.update_results.pop(0)


def test_reconcile_adds_label_specs_to_unchanged_tracked_issue():
    client = _TrackedClient()
    state = {
        "issue": {"number": 7, "opened_at": "2026-07-28T10:00:00Z"},
        "last_fingerprint": "fingerprint",
    }

    reconciled = _reconcile(state, client)

    assert reconciled["issue"]["number"] == 7
    assert client.labels == [(7, LABEL_SPECS)]
    assert client.updated == []


def test_reconcile_preserves_tracked_canonical_and_closes_verified_siblings():
    client = _SiblingAwareClient([4, 7, 9])
    state = {
        "issue": {"number": 7, "opened_at": "2026-07-28T10:00:00Z"},
        "last_fingerprint": "fingerprint",
    }

    reconciled = _reconcile(state, client)

    assert reconciled["issue"]["number"] == 7
    assert client.closed == [4, 9]
    assert client.state_reads == [7, 4, 9]
    assert client.labels == [(7, LABEL_SPECS)]
    assert client.opened == []


def test_reconcile_recovers_oldest_canonical_and_closes_newer_sibling():
    client = _SiblingAwareClient([43, 42])

    reconciled = _reconcile({}, client)

    assert reconciled["issue"]["number"] == 42
    assert client.closed == [43]
    assert client.state_reads == [43]
    assert client.updated == [42]
    assert client.opened == []


def test_reconcile_never_closes_sibling_that_lost_exact_marker():
    client = _SiblingAwareClient([7, 8], states={8: "foreign"})
    state = {
        "issue": {"number": 7, "opened_at": "2026-07-28T10:00:00Z"},
        "last_fingerprint": "fingerprint",
    }

    reconciled = _reconcile(state, client)

    assert reconciled["issue"]["number"] == 7
    assert client.closed == []
    assert client.state_reads == [7, 8]


def test_tracked_reconcile_fails_closed_when_sibling_lookup_fails():
    client = _FailedSiblingLookupClient([])
    state = {
        "issue": {"number": 7, "opened_at": "2026-07-28T10:00:00Z"},
        "last_fingerprint": "fingerprint",
    }

    reconciled = _reconcile(state, client)

    assert reconciled["issue"]["number"] == 7
    assert reconciled["last_run"] == ""
    assert client.labels == []
    assert client.updated == []
    assert client.closed == []


def test_failed_sibling_close_does_not_adopt_or_open_recovered_canonical():
    client = _SiblingAwareClient([42, 43], close_result=False)

    reconciled = _reconcile({}, client)

    assert reconciled["issue"] is None
    assert client.closed == [43]
    assert client.labels == []
    assert client.updated == []
    assert client.opened == []


def test_reconcile_refreshes_evidence_without_changing_signal_identity():
    client = _TrackedClient()
    state = {
        "issue": {"number": 7, "opened_at": "2026-07-28T10:00:00Z"},
        "last_fingerprint": "stable-signal",
        "last_content_fingerprint": "old-evidence",
    }

    reconciled = _reconcile(
        state,
        client,
        fingerprint="stable-signal",
        content_fingerprint="new-evidence",
    )

    assert client.updated == [7]
    assert reconciled["last_fingerprint"] == "stable-signal"
    assert reconciled["last_content_fingerprint"] == "new-evidence"


def test_reconcile_refreshes_open_issue_when_signal_changes_with_same_content():
    client = _TrackedClient()
    state = {
        "issue": {"number": 7, "opened_at": "2026-07-28T10:00:00Z"},
        "last_fingerprint": "old-generation",
        "last_content_fingerprint": "same-evidence",
    }

    reconciled = _reconcile(
        state,
        client,
        fingerprint="new-generation",
        content_fingerprint="same-evidence",
    )

    assert client.updated == [7]
    assert reconciled["last_fingerprint"] == "new-generation"
    assert reconciled["last_content_fingerprint"] == "same-evidence"


def test_failed_signal_refresh_keeps_fingerprints_dirty_for_retry():
    client = _RetryingUpdateClient()
    state = {
        "issue": {"number": 7, "opened_at": "2026-07-28T10:00:00Z"},
        "last_fingerprint": "old-generation",
        "last_content_fingerprint": "same-evidence",
    }

    failed = _reconcile(
        state,
        client,
        fingerprint="new-generation",
        content_fingerprint="same-evidence",
    )
    retried = _reconcile(
        failed,
        client,
        fingerprint="new-generation",
        content_fingerprint="same-evidence",
    )

    assert client.updated == [7, 7]
    assert failed["last_fingerprint"] == "old-generation"
    assert failed["last_content_fingerprint"] == "same-evidence"
    assert retried["last_fingerprint"] == "new-generation"


def test_failed_recovered_issue_refresh_retries_marker_lookup():
    client = _RetryingUpdateClient(recovered_number=42)
    state = {
        "last_fingerprint": "stable-signal",
        "last_content_fingerprint": "same-evidence",
    }

    failed = _reconcile(
        state,
        client,
        fingerprint="stable-signal",
        content_fingerprint="same-evidence",
    )
    retried = _reconcile(
        failed,
        client,
        fingerprint="stable-signal",
        content_fingerprint="same-evidence",
    )

    assert failed["issue"] is None
    assert retried["issue"]["number"] == 42
    assert client.finds == 2
    assert client.updated == [42, 42]


def test_reconcile_recovers_marker_owned_issue_before_opening_duplicate():
    client = _RecoveringClient()

    reconciled = _reconcile(
        {"last_fingerprint": "fingerprint"},
        client,
    )

    assert reconciled["issue"] == {
        "number": 42,
        "opened_at": "2026-07-28T12:00:00Z",
    }
    assert reconciled["last_fingerprint"] == "fingerprint"
    assert [event[0] for event in client.events] == [
        "find",
        "labels",
        "assign",
        "update",
    ]
    assert MARKER in client.events[-1][3]


class _FailedRecoveryClient:
    def __init__(self):
        self.opened = False

    def find_open_issue(self, ownership_marker):
        raise RuntimeError("temporary GitHub API failure")

    def open_issue(self, title, body, label_specs):
        self.opened = True
        return 99


def test_reconcile_refuses_to_open_when_recovery_lookup_fails():
    client = _FailedRecoveryClient()

    reconciled = _reconcile({}, client)

    assert reconciled["issue"] is None
    assert client.opened is False


class _SuppressionClient:
    def __init__(self):
        self.state = "open"
        self.next_number = 10
        self.opened = []

    def issue_state(self, number, ownership_marker):
        return self.state

    def find_open_issue(self, ownership_marker):
        return None

    def ensure_owner_assigned(self, number):
        return True

    def open_issue(self, title, body, label_specs):
        self.next_number += 1
        self.opened.append(self.next_number)
        self.state = "open"
        return self.next_number


def test_same_fingerprint_stays_suppressed_until_signal_recovery():
    client = _SuppressionClient()
    state = {
        "issue": {"number": 7, "opened_at": "2026-07-28T10:00:00Z"},
        "last_fingerprint": "same",
    }
    client.state = "closed"

    suppressed = _reconcile(state, client, fingerprint="same")
    assert suppressed["suppressed"] is True
    assert suppressed["suppressed_fingerprint"] == "same"

    still_suppressed = _reconcile(suppressed, client, fingerprint="same")
    assert still_suppressed["issue"] is None
    assert client.opened == []

    recovered = _reconcile(
        still_suppressed,
        client,
        active=False,
        fingerprint="",
    )
    assert recovered["suppressed"] is False
    assert recovered["suppressed_fingerprint"] == ""

    reopened = _reconcile(recovered, client, fingerprint="same")
    assert reopened["issue"]["number"] == 11


def test_new_evidence_does_not_reopen_a_manually_closed_signal():
    client = _SuppressionClient()
    state = {
        "issue": {"number": 7, "opened_at": "2026-07-28T10:00:00Z"},
        "last_fingerprint": "stable-signal",
        "last_content_fingerprint": "old-evidence",
    }
    client.state = "closed"

    suppressed = _reconcile(
        state,
        client,
        fingerprint="stable-signal",
        content_fingerprint="new-evidence",
    )
    still_suppressed = _reconcile(
        suppressed,
        client,
        fingerprint="stable-signal",
        content_fingerprint="newer-evidence",
    )

    assert still_suppressed["issue"] is None
    assert still_suppressed["suppressed"] is True
    assert still_suppressed["suppressed_fingerprint"] == "stable-signal"
    assert client.opened == []


def test_changed_fingerprint_automatically_clears_suppression():
    client = _SuppressionClient()
    suppressed = {
        "issue": None,
        "suppressed": True,
        "suppressed_fingerprint": "old",
        "last_fingerprint": "old",
    }

    reconciled = _reconcile(suppressed, client, fingerprint="new")

    assert reconciled["issue"]["number"] == 11
    assert reconciled["suppressed"] is False
    assert reconciled["suppressed_fingerprint"] == ""
    assert reconciled["last_fingerprint"] == "new"
