"""Tests for ``scripts/vllm/process_signup.py``.

The signup processor runs in ``.github/workflows/user-signup.yml`` when a
dashboard visitor opens a ``signup-request`` issue via the entry gate.

Current flow: the issue body is an **audit record** carrying
``{email, requested_at}``. Identity (``github_id`` + ``github_login``) is
pulled from ``github.event.issue.user``, which GitHub itself authenticates.
The workflow is two-stage:

    * ``signup-request`` validates the JSON and marks the issue pending.
      It must **not** append to ``data/users.json``.
    * ``signup-approved`` may only be honored when the labeling actor is
      the dashboard admin. Only that path commits a new users.json row.
    * ``signup-rejected`` is also admin-only and never writes users.json.

Tests exercise the parser happy path and every rejection path, and verify
run() only commits on the explicit approval path and never closes the issue.
"""

from __future__ import annotations

import base64
import json

import pytest

from vllm import process_signup as ps


VALID_LOGIN = "SomeUser-01"
VALID_ID = 987654321
VALID_EMAIL = "someuser@amd.com"


def _build_body(**overrides):
    payload = {
        "email": VALID_EMAIL,
        "requested_at": "2026-04-18T10:00:00Z",
    }
    payload.update(overrides)
    return f"some preamble\n\n```json\n{json.dumps(payload)}\n```\n\nafterword"


# ---------------------------------------------------------------------------
# parse_signup_body
# ---------------------------------------------------------------------------

class TestParseSignupBody:
    def test_happy_path(self):
        out = ps.parse_signup_body(_build_body())
        assert out is not None
        assert out["email"] == VALID_EMAIL
        assert out["requested_at"] == "2026-04-18T10:00:00Z"

    def test_missing_json_block(self):
        assert ps.parse_signup_body("no block here") is None

    def test_invalid_json(self):
        body = "```json\n{not valid json}\n```"
        assert ps.parse_signup_body(body) is None

    def test_missing_email(self):
        payload = {"requested_at": "2026-04-18T10:00:00Z"}
        body = f"```json\n{json.dumps(payload)}\n```"
        assert ps.parse_signup_body(body) is None

    def test_rejects_invalid_email(self):
        assert ps.parse_signup_body(_build_body(email="not-an-email")) is None
        assert ps.parse_signup_body(_build_body(email="a@b")) is None
        assert ps.parse_signup_body(_build_body(email="")) is None

    def test_normalizes_email_to_lowercase(self):
        out = ps.parse_signup_body(_build_body(email="MixedCase@Example.Com"))
        assert out is not None
        assert out["email"] == "mixedcase@example.com"

    def test_drops_extra_fields(self):
        # Defence in depth: legacy clients may include salt/hash/password.
        # The parser returns only {email, requested_at}.
        payload = {
            "email": VALID_EMAIL,
            "requested_at": "2026-04-18T10:00:00Z",
            "github_login": "spoofed",
            "github_id": 1,
            "salt": "a" * 32,
            "iterations": 200000,
            "password_hash": "b" * 64,
            "password": "PLAINTEXT-SHOULD-BE-DROPPED",  # noqa: S105
            "pat": "ghp_secret_should_be_dropped",
        }
        body = f"```json\n{json.dumps(payload)}\n```"
        out = ps.parse_signup_body(body)
        assert out is not None
        assert set(out.keys()) == {"email", "requested_at"}


# ---------------------------------------------------------------------------
# _parse_labels
# ---------------------------------------------------------------------------

class TestParseLabels:
    def test_happy_path(self):
        assert ps._parse_labels('["signup-request","signup-processed"]') == [
            "signup-request",
            "signup-processed",
        ]

    def test_empty_env_returns_empty_list(self):
        assert ps._parse_labels("") == []

    def test_malformed_json_returns_empty_list(self):
        # A bad env var must NOT raise — we'd rather skip the idempotency
        # check than crash a signup because the workflow mis-quoted the
        # toJson output.
        assert ps._parse_labels("not json") == []

    def test_non_array_returns_empty_list(self):
        assert ps._parse_labels('{"signup-request": true}') == []


# ---------------------------------------------------------------------------
# run() — full workflow
# ---------------------------------------------------------------------------

def _stub_requests(monkeypatch, *, initial_db=None):
    """Replace every outbound HTTP call with a recorder + fake Contents API.

    GETs against ``/contents/data/users.json`` return a base64-encoded
    representation of ``initial_db``. PUTs against the same path capture
    the new body so tests can assert on what would be committed.
    """
    if initial_db is None:
        initial_db = {"admin_id": ps.DEFAULT_ADMIN_ID, "users": []}

    calls = []
    state = {"db": dict(initial_db), "sha": "sha-initial"}

    class _Resp:
        def __init__(self, status_code=200, payload=None, text=""):
            self.status_code = status_code
            self.ok = 200 <= status_code < 300
            self._payload = payload if payload is not None else {}
            self.text = text

        def json(self):
            return self._payload

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError(f"HTTP {self.status_code}")

    def _get(url, *a, **kw):
        calls.append(("GET", url, kw))
        if "/contents/" in url and "users.json" in url:
            raw = json.dumps(state["db"]).encode("utf-8")
            return _Resp(
                200,
                {
                    "content": base64.b64encode(raw).decode("ascii"),
                    "sha": state["sha"],
                },
            )
        return _Resp(200, {})

    def _post(url, *a, **kw):
        calls.append(("POST", url, kw))
        return _Resp(201, {})

    def _put(url, *a, **kw):
        calls.append(("PUT", url, kw))
        if "/contents/" in url and "users.json" in url:
            body = kw.get("json", {})
            new_raw = base64.b64decode(body["content"]).decode("utf-8")
            state["db"] = json.loads(new_raw)
            state["sha"] = "sha-next"
        return _Resp(200, {})

    def _patch(url, *a, **kw):
        calls.append(("PATCH", url, kw))
        return _Resp(200, {})

    def _delete(url, *a, **kw):
        calls.append(("DELETE", url, kw))
        return _Resp(204, {})

    import requests

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(requests, "put", _put)
    monkeypatch.setattr(requests, "patch", _patch)
    monkeypatch.setattr(requests, "delete", _delete)
    return calls, state


def _set_issue_env(
    monkeypatch,
    *,
    number=42,
    author=VALID_LOGIN,
    author_id=VALID_ID,
    sender=None,
    sender_id=None,
    trigger_label=ps.REQUEST_LABEL,
    body=None,
    labels=None,
):
    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    monkeypatch.setenv("GH_REPOSITORY", "AndreasKaratzas/vllm-ci-dashboard")
    monkeypatch.setenv("ISSUE_NUMBER", str(number))
    monkeypatch.setenv("ISSUE_AUTHOR", author)
    monkeypatch.setenv("ISSUE_AUTHOR_ID", str(author_id))
    monkeypatch.setenv("ISSUE_SENDER", sender if sender is not None else author)
    monkeypatch.setenv("ISSUE_SENDER_ID", str(sender_id if sender_id is not None else author_id))
    monkeypatch.setenv("TRIGGER_LABEL", trigger_label)
    monkeypatch.setenv("ISSUE_BODY", body if body is not None else _build_body())
    # Workflow serializes labels via ``toJson(github.event.issue.labels.*.name)``.
    monkeypatch.setenv(
        "ISSUE_LABELS",
        json.dumps(labels if labels is not None else [ps.REQUEST_LABEL]),
    )
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_FROM", raising=False)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)


class TestRun:
    def test_request_path_marks_issue_pending_without_commit(self, monkeypatch):
        calls, state = _stub_requests(monkeypatch)
        _set_issue_env(monkeypatch)
        rc = ps.run()
        assert rc == 0

        assert not any(c[0] == "PATCH" for c in calls)
        assert not any(c[0] == "PUT" for c in calls)
        assert state["db"]["users"] == []
        posted = [c for c in calls if c[0] == "POST"]
        assert any("/comments" in c[1] for c in posted)
        assert any("/labels" in c[1] and ps.PENDING_LABEL in c[2].get("json", {}).get("labels", []) for c in posted)
        assert any(c[0] == "DELETE" and c[1].endswith("/labels/" + ps.REQUEST_LABEL) for c in calls)

    def test_approval_path_appends_user_and_commits(self, monkeypatch):
        calls, state = _stub_requests(monkeypatch)
        _set_issue_env(
            monkeypatch,
            sender="AndreasKaratzas",
            sender_id=ps.DEFAULT_ADMIN_ID,
            trigger_label=ps.APPROVED_LABEL,
            labels=[ps.PENDING_LABEL, ps.APPROVED_LABEL],
        )
        rc = ps.run()
        assert rc == 0
        puts = [c for c in calls if c[0] == "PUT" and "users.json" in c[1]]
        assert len(puts) == 1
        assert state["db"]["users"] == [{
            "github_id": VALID_ID,
            "email": VALID_EMAIL,
            "requested_at": "2026-04-18T10:00:00Z",
        }]
        posted = [c for c in calls if c[0] == "POST"]
        assert any("/labels" in c[1] and ps.PROCESSED_LABEL in c[2].get("json", {}).get("labels", []) for c in posted)
        assert any(c[0] == "DELETE" and c[1].endswith("/labels/" + ps.PENDING_LABEL) for c in calls)

    def test_approval_replaces_existing_entry_for_same_user(self, monkeypatch):
        initial = {
            "admin_id": ps.DEFAULT_ADMIN_ID,
            "users": [
                {
                    "github_id": VALID_ID,
                    "email": "old@example.com",
                    "requested_at": "2026-01-01T00:00:00Z",
                },
            ],
        }
        calls, state = _stub_requests(monkeypatch, initial_db=initial)
        _set_issue_env(
            monkeypatch,
            sender="AndreasKaratzas",
            sender_id=ps.DEFAULT_ADMIN_ID,
            trigger_label=ps.APPROVED_LABEL,
            labels=[ps.PENDING_LABEL, ps.APPROVED_LABEL],
        )
        assert ps.run() == 0
        users = state["db"]["users"]
        assert len(users) == 1
        assert users[0]["email"] == VALID_EMAIL

    def test_approval_preserves_admin_id_and_other_users(self, monkeypatch):
        other = {
            "github_id": 111,
            "email": "other@example.com",
            "requested_at": "2026-02-02T00:00:00Z",
        }
        initial = {"admin_id": 42451412, "users": [other]}
        _, state = _stub_requests(monkeypatch, initial_db=initial)
        _set_issue_env(
            monkeypatch,
            sender="AndreasKaratzas",
            sender_id=ps.DEFAULT_ADMIN_ID,
            trigger_label=ps.APPROVED_LABEL,
            labels=[ps.PENDING_LABEL, ps.APPROVED_LABEL],
        )
        assert ps.run() == 0
        assert state["db"]["admin_id"] == 42451412
        ids = sorted(u["github_id"] for u in state["db"]["users"])
        assert ids == [111, VALID_ID]

    def test_bad_request_body_rejects_without_commit(self, monkeypatch):
        calls, state = _stub_requests(monkeypatch)
        _set_issue_env(monkeypatch, body="no json block here")
        rc = ps.run()
        assert rc == 0
        assert not any(c[0] == "PUT" for c in calls)
        assert state["db"]["users"] == []
        posted = [c for c in calls if c[0] == "POST"]
        assert any("/labels" in c[1] and ps.REJECTED_LABEL in c[2].get("json", {}).get("labels", []) for c in posted)

    def test_invalid_issue_number_returns_error(self, monkeypatch):
        _stub_requests(monkeypatch)
        monkeypatch.setenv("ISSUE_NUMBER", "not-a-number")
        monkeypatch.setenv("ISSUE_AUTHOR", VALID_LOGIN)
        monkeypatch.setenv("ISSUE_AUTHOR_ID", str(VALID_ID))
        monkeypatch.setenv("ISSUE_SENDER", VALID_LOGIN)
        monkeypatch.setenv("ISSUE_SENDER_ID", str(VALID_ID))
        monkeypatch.setenv("TRIGGER_LABEL", ps.REQUEST_LABEL)
        monkeypatch.setenv("ISSUE_BODY", _build_body())
        monkeypatch.setenv("GITHUB_TOKEN", "fake")
        monkeypatch.setenv("GH_REPOSITORY", "x/y")
        assert ps.run() == 1

    def test_invalid_issue_author_id_returns_error(self, monkeypatch):
        _stub_requests(monkeypatch)
        _set_issue_env(monkeypatch)
        monkeypatch.setenv("ISSUE_AUTHOR_ID", "not-a-number")
        assert ps.run() == 1

    def test_invalid_issue_sender_id_returns_error(self, monkeypatch):
        _stub_requests(monkeypatch)
        _set_issue_env(monkeypatch)
        monkeypatch.setenv("ISSUE_SENDER_ID", "not-a-number")
        assert ps.run() == 1

    def test_skips_when_signup_processed_label_already_present(self, monkeypatch):
        # Duplicate-delivery guard: a replay of the same ``labeled`` event (or
        # a human re-adding ``signup-request`` after the script ran) must not
        # produce a second commit + confirmation comment — the exact failure
        # mode that caused duplicate ":white_check_mark: Signup processed"
        # comments on issue #36.
        calls, state = _stub_requests(monkeypatch)
        _set_issue_env(
            monkeypatch,
            labels=[ps.REQUEST_LABEL, ps.PROCESSED_LABEL],
        )
        rc = ps.run()
        assert rc == 0
        # No contents GET/PUT, no comment POST, no label POST.
        assert not any(c[0] == "PUT" for c in calls)
        assert not any(
            "/contents/" in c[1] and c[0] == "GET" for c in calls
        )
        assert not any("/comments" in c[1] for c in calls)
        assert state["db"]["users"] == []

    def test_first_run_processes_even_without_processed_label(self, monkeypatch):
        # The first signup-request should move the issue into the pending state.
        calls, state = _stub_requests(monkeypatch)
        _set_issue_env(monkeypatch, labels=[ps.REQUEST_LABEL])
        rc = ps.run()
        assert rc == 0
        assert len(state["db"]["users"]) == 0
        assert any("/comments" in c[1] for c in calls if c[0] == "POST")

    def test_request_by_non_author_is_rejected(self, monkeypatch):
        calls, state = _stub_requests(monkeypatch)
        _set_issue_env(
            monkeypatch,
            sender="Maintainer",
            sender_id=111,
            labels=[ps.REQUEST_LABEL],
        )
        rc = ps.run()
        assert rc == 0
        assert state["db"]["users"] == []
        assert not any(c[0] == "PUT" for c in calls)
        assert any("/labels" in c[1] and ps.REJECTED_LABEL in c[2].get("json", {}).get("labels", []) for c in calls if c[0] == "POST")

    def test_approval_by_non_admin_is_ignored(self, monkeypatch):
        calls, state = _stub_requests(monkeypatch)
        _set_issue_env(
            monkeypatch,
            sender="NotAdmin",
            sender_id=111,
            trigger_label=ps.APPROVED_LABEL,
            labels=[ps.PENDING_LABEL, ps.APPROVED_LABEL],
        )
        rc = ps.run()
        assert rc == 0
        assert state["db"]["users"] == []
        assert not any(c[0] == "PUT" for c in calls)
        assert any(c[0] == "DELETE" and c[1].endswith("/labels/" + ps.APPROVED_LABEL) for c in calls)

    def test_rejection_by_admin_does_not_commit(self, monkeypatch):
        calls, state = _stub_requests(monkeypatch)
        _set_issue_env(
            monkeypatch,
            sender="AndreasKaratzas",
            sender_id=ps.DEFAULT_ADMIN_ID,
            trigger_label=ps.REJECTED_LABEL,
            labels=[ps.PENDING_LABEL, ps.REJECTED_LABEL],
        )
        rc = ps.run()
        assert rc == 0
        assert state["db"]["users"] == []
        assert not any(c[0] == "PUT" for c in calls)
        assert any("/labels" in c[1] and ps.PROCESSED_LABEL in c[2].get("json", {}).get("labels", []) for c in calls if c[0] == "POST")

    def test_run_never_patches_issue_state(self, monkeypatch):
        calls, _ = _stub_requests(monkeypatch)
        _set_issue_env(monkeypatch)
        ps.run()
        _set_issue_env(monkeypatch, body="garbage")
        ps.run()
        assert all(
            "state" not in c[2].get("json", {}) for c in calls
        )


class TestEmail:
    def test_email_disabled_without_api_key(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        monkeypatch.delenv("RESEND_FROM", raising=False)
        assert ps.send_email("x@y.com", "s", "<p>h</p>") is False

    def test_email_disabled_without_sender(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_fake")
        monkeypatch.delenv("RESEND_FROM", raising=False)
        assert ps.send_email("x@y.com", "s", "<p>h</p>") is False

    def test_email_disabled_without_recipient(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_fake")
        monkeypatch.setenv("RESEND_FROM", "bot@example.com")
        assert ps.send_email("", "s", "<p>h</p>") is False
