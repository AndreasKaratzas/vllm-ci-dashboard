"""GitHub helpers for state-owned dashboard alert issues.

Each watcher stores the one issue number it owns. Reconciliation never searches
for issues by label and therefore can update or close only that tracked issue.
A manually closed issue stays suppressed until the underlying signal recovers.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import requests


GH_API = "https://api.github.com"
DASHBOARD_REPO = "AndreasKaratzas/vllm-ci-dashboard"

log = logging.getLogger(__name__)


def validate_target_repo(repo: str) -> None:
    if repo.strip().lower() != DASHBOARD_REPO.lower():
        raise RuntimeError(f"Issue automation is restricted to {DASHBOARD_REPO}")


def repo_owner(repo: str) -> str:
    owner = repo.split("/", 1)[0] if "/" in repo else repo
    return (owner or "AndreasKaratzas").strip() or "AndreasKaratzas"


def normalize_managed_state(state: Any) -> dict:
    data = dict(state) if isinstance(state, dict) else {}
    issue = data.get("issue")
    if isinstance(issue, int) and not isinstance(issue, bool):
        issue = {"number": issue, "opened_at": ""}
    elif isinstance(issue, dict):
        number = issue.get("number")
        issue = {
            "number": int(number) if isinstance(number, int) and not isinstance(number, bool) else 0,
            "opened_at": str(issue.get("opened_at") or ""),
        }
        if issue["number"] <= 0:
            issue = None
    else:
        issue = None
    return {
        **data,
        "schema_version": 1,
        "issue": issue,
        "suppressed": bool(data.get("suppressed")),
        "last_fingerprint": str(data.get("last_fingerprint") or ""),
        "last_run": str(data.get("last_run") or ""),
    }


class GitHubIssueClient:
    def __init__(self, token: str, repo: str):
        validate_target_repo(repo)
        self.token = token
        self.repo = repo
        self.owner = repo_owner(repo)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def issue_state(self, number: int, ownership_marker: str) -> str | None:
        response = requests.get(
            f"{GH_API}/repos/{self.repo}/issues/{number}",
            headers=self.headers,
            timeout=30,
        )
        if response.status_code == 404:
            return "closed"
        if response.status_code >= 300:
            log.warning("Read issue #%d failed: %d", number, response.status_code)
            return None
        payload = response.json() or {}
        if ownership_marker not in str(payload.get("body") or ""):
            log.error("Issue #%d lacks the expected ownership marker", number)
            return "foreign"
        state = str(payload.get("state") or "").lower()
        return state if state in {"open", "closed"} else None

    def ensure_label(self, name: str, color: str, description: str) -> bool:
        encoded = quote(name, safe="")
        response = requests.get(
            f"{GH_API}/repos/{self.repo}/labels/{encoded}",
            headers=self.headers,
            timeout=30,
        )
        if response.status_code == 200:
            return True
        if response.status_code != 404:
            log.warning("Read label %s failed: %d", name, response.status_code)
            return False
        response = requests.post(
            f"{GH_API}/repos/{self.repo}/labels",
            headers=self.headers,
            json={"name": name, "color": color, "description": description},
            timeout=30,
        )
        if response.status_code not in {200, 201}:
            log.warning("Create label %s failed: %d", name, response.status_code)
            return False
        return True

    def open_issue(
        self,
        title: str,
        body: str,
        label_specs: list[tuple[str, str, str]],
    ) -> int | None:
        labels = [
            name
            for name, color, description in label_specs
            if self.ensure_label(name, color, description)
        ]
        response = requests.post(
            f"{GH_API}/repos/{self.repo}/issues",
            headers=self.headers,
            json={
                "title": title,
                "body": body,
                "labels": labels,
                "assignees": [self.owner],
            },
            timeout=30,
        )
        if response.status_code >= 300:
            log.error("Open managed issue failed: %d %s", response.status_code, response.text[:200])
            return None
        number = (response.json() or {}).get("number")
        return int(number) if isinstance(number, int) and not isinstance(number, bool) else None

    def update_issue(self, number: int, title: str, body: str) -> bool:
        response = requests.patch(
            f"{GH_API}/repos/{self.repo}/issues/{number}",
            headers=self.headers,
            json={"title": title, "body": body},
            timeout=30,
        )
        if response.status_code >= 300:
            log.warning("Update issue #%d failed: %d", number, response.status_code)
            return False
        return True

    def ensure_owner_assigned(self, number: int) -> bool:
        response = requests.post(
            f"{GH_API}/repos/{self.repo}/issues/{number}/assignees",
            headers=self.headers,
            json={"assignees": [self.owner]},
            timeout=30,
        )
        if response.status_code not in {200, 201}:
            log.warning("Assign owner on issue #%d failed: %d", number, response.status_code)
            return False
        return True

    def comment_issue(self, number: int, body: str) -> bool:
        response = requests.post(
            f"{GH_API}/repos/{self.repo}/issues/{number}/comments",
            headers=self.headers,
            json={"body": body},
            timeout=30,
        )
        if response.status_code >= 300:
            log.warning("Comment on issue #%d failed: %d", number, response.status_code)
            return False
        return True

    def close_issue(self, number: int) -> bool:
        response = requests.patch(
            f"{GH_API}/repos/{self.repo}/issues/{number}",
            headers=self.headers,
            json={"state": "closed", "state_reason": "completed"},
            timeout=30,
        )
        if response.status_code >= 300:
            log.warning("Close issue #%d failed: %d", number, response.status_code)
            return False
        return True


def reconcile_managed_issue(
    state: dict,
    *,
    active: bool,
    fingerprint: str,
    title: str,
    body: str,
    ownership_marker: str,
    recovery_body: str,
    observed_at: str,
    label_specs: list[tuple[str, str, str]],
    client: GitHubIssueClient,
) -> dict:
    """Reconcile one state-owned umbrella issue with an alert signal."""
    normalized = normalize_managed_state(state)
    if not ownership_marker.startswith("<!--") or not ownership_marker.endswith("-->"):
        raise ValueError("ownership_marker must be an HTML comment")
    managed_body = (
        body
        if ownership_marker in body
        else f"{ownership_marker}\n{body}"
    )
    issue = normalized.get("issue")
    number = int((issue or {}).get("number") or 0)

    if number:
        remote_state = client.issue_state(number, ownership_marker)
        if remote_state in {None, "foreign"}:
            log.warning("Issue #%d ownership/state is unverified; preserving local state", number)
            return normalized
        if remote_state == "closed":
            normalized["issue"] = None
            number = 0
            if active:
                normalized["suppressed"] = True
                log.info("Issue was manually closed; suppressing until the signal recovers")

    if active:
        if normalized["suppressed"]:
            normalized["last_fingerprint"] = fingerprint
        elif number:
            client.ensure_owner_assigned(number)
            if normalized["last_fingerprint"] != fingerprint:
                if client.update_issue(number, title, managed_body):
                    normalized["last_fingerprint"] = fingerprint
        else:
            opened_number = client.open_issue(title, managed_body, label_specs)
            if opened_number:
                normalized["issue"] = {
                    "number": opened_number,
                    "opened_at": observed_at,
                }
                normalized["last_fingerprint"] = fingerprint
    else:
        if number:
            client.ensure_owner_assigned(number)
            client.comment_issue(number, recovery_body)
            if client.close_issue(number):
                normalized["issue"] = None
                number = 0
        if not number:
            normalized["suppressed"] = False
            normalized["last_fingerprint"] = ""

    normalized["last_run"] = observed_at
    return normalized
