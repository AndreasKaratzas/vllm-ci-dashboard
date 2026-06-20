#!/usr/bin/env python3
"""React to dashboard signup issues and gate allowlist writes behind approval.

Invoked from ``.github/workflows/user-signup.yml`` when a dashboard visitor
opens an issue labeled ``signup-request`` via the entry gate, or when the
dashboard admin later labels that same issue ``signup-approved`` /
``signup-rejected``.

The issue body is an **audit record**::

    ```json
    {"email": "user@example.com", "requested_at": "2026-04-18T..."}
    ```

GitHub itself authenticates the issue author, so we trust
``github.event.issue.user`` for identity. The workflow is now two-stage:

    * ``signup-request`` validates the audit JSON and records the request as
      pending admin approval. It does **not** append to ``data/users.json``.
    * ``signup-approved`` may only be honored when the labeling actor matches
      ``admin_id`` from ``data/users.json``. Only then do we commit a new
      allowlist entry.
    * ``signup-rejected`` is also admin-only and closes the approval loop
      without modifying ``data/users.json``.

The issue stays open as the audit log; labels + comments capture the state
transition. No password material or PAT ever touches this workflow.

The old flow (PBKDF2 password hash committed by the browser) is gone. No
salt, no iteration count, no password material touches this workflow.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys

import requests

GH_API = "https://api.github.com"
RESEND_API = "https://api.resend.com/emails"
USERS_PATH = "data/users.json"
DEFAULT_ADMIN_ID = 42451412  # @AndreasKaratzas; falls back if users.json has no admin_id
REQUEST_LABEL = "signup-request"
PENDING_LABEL = "signup-pending"
APPROVED_LABEL = "signup-approved"
REJECTED_LABEL = "signup-rejected"
PROCESSED_LABEL = "signup-processed"

log = logging.getLogger("process_signup")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _comment(token: str, repo: str, number: int, comment: str) -> None:
    if not token:
        log.warning("No GITHUB_TOKEN; skipping issue mutations")
        return
    h = _gh_headers(token)
    requests.post(
        f"{GH_API}/repos/{repo}/issues/{number}/comments",
        headers=h, json={"body": comment}, timeout=20,
    )


def _add_labels(token: str, repo: str, number: int, labels: list[str]) -> None:
    if not token or not labels:
        return
    requests.post(
        f"{GH_API}/repos/{repo}/issues/{number}/labels",
        headers=_gh_headers(token),
        json={"labels": sorted(set(str(x) for x in labels if x))},
        timeout=20,
    )


def _remove_label(token: str, repo: str, number: int, label: str) -> None:
    if not token or not label:
        return
    try:
        requests.delete(
            f"{GH_API}/repos/{repo}/issues/{number}/labels/{label}",
            headers=_gh_headers(token),
            timeout=20,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Could not remove label %s from issue #%d: %s", label, number, e)


def _comment_and_sync_labels(
    token: str,
    repo: str,
    number: int,
    comment: str,
    *,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> None:
    """Comment, then reconcile the issue labels toward the desired state."""
    _comment(token, repo, number, comment)
    if add_labels:
        _add_labels(token, repo, number, add_labels)
    for label in remove_labels or []:
        _remove_label(token, repo, number, label)


def parse_signup_body(body: str) -> dict | None:
    """Extract + validate the audit JSON block from a signup-request body.

    Returns ``None`` if the block is missing or invalid. On success returns
    ``{"email": str, "requested_at": str}`` — only the two user-supplied
    audit fields. Identity (``github_id`` / ``github_login``) is pulled
    from ``github.event.issue.user``, not from the body.
    """
    if not body:
        return None
    m = JSON_BLOCK_RE.search(body)
    if not m:
        return None
    try:
        raw = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    if "email" not in raw:
        return None

    email = str(raw["email"]).strip().lower()
    if not EMAIL_RE.match(email):
        return None

    return {
        "email": email,
        "requested_at": str(raw.get("requested_at", "")).strip(),
    }


def _load_users_json(token: str, repo: str) -> tuple[dict, str]:
    """Return (db, sha) for the current users.json on main."""
    r = requests.get(
        f"{GH_API}/repos/{repo}/contents/{USERS_PATH}?ref=main",
        headers=_gh_headers(token),
        timeout=20,
    )
    r.raise_for_status()
    meta = r.json()
    raw = base64.b64decode(meta.get("content", "").replace("\n", "")).decode("utf-8")
    try:
        db = json.loads(raw)
    except json.JSONDecodeError:
        db = {}
    if not isinstance(db, dict):
        db = {}
    db.setdefault("admin_id", DEFAULT_ADMIN_ID)
    if not isinstance(db.get("users"), list):
        db["users"] = []
    return db, meta["sha"]


def _put_users_json(token: str, repo: str, db: dict, sha: str, message: str) -> None:
    payload = json.dumps(db, indent=2) + "\n"
    body = {
        "message": message,
        "content": base64.b64encode(payload.encode("utf-8")).decode("ascii"),
        "sha": sha,
        "branch": "main",
    }
    r = requests.put(
        f"{GH_API}/repos/{repo}/contents/{USERS_PATH}",
        headers=_gh_headers(token),
        json=body,
        timeout=30,
    )
    r.raise_for_status()


def send_email(to_addr: str, subject: str, html: str) -> bool:
    api_key = os.getenv("RESEND_API_KEY")
    sender = os.getenv("RESEND_FROM")
    if not api_key or not sender or not to_addr:
        return False
    try:
        r = requests.post(
            RESEND_API,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"from": sender, "to": [to_addr], "subject": subject, "html": html},
            timeout=15,
        )
        if r.status_code >= 300:
            log.warning("Resend failed (%s): %s", r.status_code, r.text[:160])
            return False
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Resend exception: %s", e)
        return False


def _parse_labels(raw: str) -> list[str]:
    """ISSUE_LABELS is a JSON-encoded array of label names (via ``toJson`` in
    the workflow). Fall back to an empty list for any parse error so a missing
    env var never masks the idempotency check with a hard failure."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(x) for x in parsed]


def _has_label(labels: list[str], label: str) -> bool:
    return label in set(labels or [])


def _admin_id(db: dict) -> int:
    try:
        return int(db.get("admin_id") or DEFAULT_ADMIN_ID)
    except Exception:  # noqa: BLE001
        return DEFAULT_ADMIN_ID


def _upsert_user(db: dict, author_id: int, parsed: dict) -> None:
    db["users"] = [u for u in db["users"] if int(u.get("github_id", 0)) != author_id]
    db["users"].append({
        "github_id": author_id,
        "email": parsed["email"],
        "requested_at": parsed["requested_at"],
    })
    db["users"].sort(key=lambda u: int(u.get("github_id", 0)))


def _already_allowlisted(db: dict, author_id: int) -> bool:
    return any(int(u.get("github_id", 0)) == author_id for u in db.get("users", []))


def _handle_request(
    *,
    token: str,
    repo: str,
    number: int,
    author_login: str,
    author_id: int,
    sender_login: str,
    sender_id: int,
    body: str,
    labels: list[str],
) -> int:
    parsed = parse_signup_body(body)
    if parsed is None:
        log.error("Could not parse signup JSON block")
        _comment_and_sync_labels(
            token,
            repo,
            number,
            ":x: Could not parse the signup JSON block. Submit the request from the dashboard entry gate so the audit record is well-formed.",
            add_labels=[REJECTED_LABEL, PROCESSED_LABEL],
            remove_labels=[REQUEST_LABEL, PENDING_LABEL],
        )
        return 0

    if sender_id != author_id:
        log.warning("Issue #%d signup-request was labeled by non-author %s (%d)", number, sender_login, sender_id)
        _comment_and_sync_labels(
            token,
            repo,
            number,
            (
                f":no_entry: Ignored this signup request because the `{REQUEST_LABEL}` label was "
                f"applied by @{sender_login}, not the issue author @{author_login}. "
                "No access was granted."
            ),
            add_labels=[REJECTED_LABEL, PROCESSED_LABEL],
            remove_labels=[REQUEST_LABEL, PENDING_LABEL],
        )
        return 0

    try:
        db, _ = _load_users_json(token, repo)
    except Exception as e:  # noqa: BLE001
        log.error("Failed to read %s: %s", USERS_PATH, e)
        _comment_and_sync_labels(
            token,
            repo,
            number,
            f":x: Could not read `{USERS_PATH}` (workflow error). Admin will retry.",
            add_labels=[REJECTED_LABEL],
            remove_labels=[REQUEST_LABEL],
        )
        return 1

    if _already_allowlisted(db, author_id):
        _comment_and_sync_labels(
            token,
            repo,
            number,
            (
                f":information_source: @{author_login} is already on the dashboard allowlist. "
                "You can sign in with a fresh PAT; no new approval was needed."
            ),
            add_labels=[PROCESSED_LABEL],
            remove_labels=[REQUEST_LABEL, PENDING_LABEL],
        )
        return 0

    log.info("Signup pending approval: login=%s id=%d email=%s", author_login, author_id, parsed["email"])
    admin_email = os.getenv("ADMIN_EMAIL", "").strip()
    if admin_email:
        send_email(
            admin_email,
            f"[dashboard] signup pending: {author_login}",
            (
                f"<p>Signup-request issue #{number} on <code>{repo}</code> is pending approval.</p>"
                f"<ul>"
                f"<li>Login: <code>{author_login}</code></li>"
                f"<li>Id: <code>{author_id}</code></li>"
                f"<li>Email: <code>{parsed['email']}</code></li>"
                f"<li>Requested at: <code>{parsed['requested_at']}</code></li>"
                f"</ul>"
            ),
        )

    _comment_and_sync_labels(
        token,
        repo,
        number,
        (
            f":hourglass_flowing_sand: Signup request recorded for @{author_login} "
            f"(id={author_id}). An admin still needs to approve this request before "
            f"you are added to `{USERS_PATH}`."
        ),
        add_labels=[PENDING_LABEL],
        remove_labels=[REQUEST_LABEL],
    )
    return 0


def _handle_approval(
    *,
    token: str,
    repo: str,
    number: int,
    author_login: str,
    author_id: int,
    sender_login: str,
    sender_id: int,
    body: str,
) -> int:
    parsed = parse_signup_body(body)
    if parsed is None:
        _comment_and_sync_labels(
            token,
            repo,
            number,
            ":x: Cannot approve this signup because the audit JSON block is invalid.",
            add_labels=[REJECTED_LABEL, PROCESSED_LABEL],
            remove_labels=[PENDING_LABEL, REQUEST_LABEL, APPROVED_LABEL],
        )
        return 0

    try:
        db, sha = _load_users_json(token, repo)
    except Exception as e:  # noqa: BLE001
        log.error("Failed to read %s: %s", USERS_PATH, e)
        _comment_and_sync_labels(
            token,
            repo,
            number,
            f":x: Could not read `{USERS_PATH}` (workflow error). Admin will retry.",
            add_labels=[REJECTED_LABEL],
            remove_labels=[APPROVED_LABEL],
        )
        return 1

    if sender_id != _admin_id(db):
        log.warning("Unauthorized signup approval attempt by %s (%d)", sender_login, sender_id)
        _comment_and_sync_labels(
            token,
            repo,
            number,
            (
                f":no_entry: Ignored `{APPROVED_LABEL}` because it was applied by "
                f"@{sender_login}, not the dashboard admin. The request is still pending."
            ),
            add_labels=[PENDING_LABEL],
            remove_labels=[APPROVED_LABEL],
        )
        return 0

    _upsert_user(db, author_id, parsed)
    try:
        _put_users_json(token, repo, db, sha, f"signup: approve @{author_login} (#{number})")
    except Exception as e:  # noqa: BLE001
        log.error("Failed to commit %s: %s", USERS_PATH, e)
        _comment_and_sync_labels(
            token,
            repo,
            number,
            f":x: Could not commit to `{USERS_PATH}`. Check workflow permissions (`contents: write`) and branch protection.",
            add_labels=[REJECTED_LABEL],
            remove_labels=[APPROVED_LABEL],
        )
        return 1

    _comment_and_sync_labels(
        token,
        repo,
        number,
        (
            f":white_check_mark: Signup approved by @{sender_login} for @{author_login} "
            f"(id={author_id}). The user has been added to `{USERS_PATH}` on main and can now sign in."
        ),
        add_labels=[APPROVED_LABEL, PROCESSED_LABEL],
        remove_labels=[PENDING_LABEL, REQUEST_LABEL],
    )
    return 0


def _handle_rejection(
    *,
    token: str,
    repo: str,
    number: int,
    author_login: str,
    sender_login: str,
    sender_id: int,
) -> int:
    try:
        db, _ = _load_users_json(token, repo)
    except Exception as e:  # noqa: BLE001
        log.error("Failed to read %s: %s", USERS_PATH, e)
        _comment_and_sync_labels(
            token,
            repo,
            number,
            f":x: Could not read `{USERS_PATH}` (workflow error). Admin will retry.",
            add_labels=[REJECTED_LABEL],
        )
        return 1

    if sender_id != _admin_id(db):
        log.warning("Unauthorized signup rejection attempt by %s (%d)", sender_login, sender_id)
        _comment_and_sync_labels(
            token,
            repo,
            number,
            (
                f":no_entry: Ignored `{REJECTED_LABEL}` because it was applied by "
                f"@{sender_login}, not the dashboard admin. The request is still pending."
            ),
            add_labels=[PENDING_LABEL],
            remove_labels=[REJECTED_LABEL],
        )
        return 0

    _comment_and_sync_labels(
        token,
        repo,
        number,
        (
            f":x: Signup rejected by @{sender_login} for @{author_login}. "
            f"No entry was added to `{USERS_PATH}`."
        ),
        add_labels=[REJECTED_LABEL, PROCESSED_LABEL],
        remove_labels=[PENDING_LABEL, REQUEST_LABEL],
    )
    return 0


def run() -> int:
    token = os.getenv("GITHUB_TOKEN", "")
    repo = os.getenv("GH_REPOSITORY", "")
    number_raw = os.getenv("ISSUE_NUMBER", "").strip()
    author_login = os.getenv("ISSUE_AUTHOR", "").strip()
    author_id_raw = os.getenv("ISSUE_AUTHOR_ID", "").strip()
    sender_login = os.getenv("ISSUE_SENDER", "").strip()
    sender_id_raw = os.getenv("ISSUE_SENDER_ID", "").strip()
    trigger_label = os.getenv("TRIGGER_LABEL", "").strip()
    body = os.getenv("ISSUE_BODY", "")
    labels = _parse_labels(os.getenv("ISSUE_LABELS", ""))

    try:
        number = int(number_raw)
    except ValueError:
        log.error("Bad ISSUE_NUMBER: %r", number_raw)
        return 1
    try:
        author_id = int(author_id_raw)
    except ValueError:
        log.error("Bad ISSUE_AUTHOR_ID: %r", author_id_raw)
        return 1
    try:
        sender_id = int(sender_id_raw)
    except ValueError:
        log.error("Bad ISSUE_SENDER_ID: %r", sender_id_raw)
        return 1

    if _has_label(labels, PROCESSED_LABEL) and trigger_label != APPROVED_LABEL and trigger_label != REJECTED_LABEL:
        log.info("Issue #%d already has %s; skipping", number, PROCESSED_LABEL)
        return 0

    if trigger_label == REQUEST_LABEL:
        return _handle_request(
            token=token,
            repo=repo,
            number=number,
            author_login=author_login,
            author_id=author_id,
            sender_login=sender_login,
            sender_id=sender_id,
            body=body,
            labels=labels,
        )
    if trigger_label == APPROVED_LABEL:
        return _handle_approval(
            token=token,
            repo=repo,
            number=number,
            author_login=author_login,
            author_id=author_id,
            sender_login=sender_login,
            sender_id=sender_id,
            body=body,
        )
    if trigger_label == REJECTED_LABEL:
        return _handle_rejection(
            token=token,
            repo=repo,
            number=number,
            author_login=author_login,
            sender_login=sender_login,
            sender_id=sender_id,
        )

    log.info("Ignoring unrelated trigger label %r on issue #%d", trigger_label, number)
    return 0


if __name__ == "__main__":
    sys.exit(run())
