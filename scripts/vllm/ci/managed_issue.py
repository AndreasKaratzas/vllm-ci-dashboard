"""GitHub helpers for state-owned dashboard alert issues.

Each watcher stores the one issue number it owns. When local state is missing,
reconciliation can recover an open issue only by its exact HTML ownership marker.
A manually closed issue stays suppressed until the signal recovers or changes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import requests


GH_API = "https://api.github.com"
DASHBOARD_REPO = "AndreasKaratzas/vllm-ci-dashboard"
ISSUE_LOOKUP_PAGE_SIZE = 100
MAX_DIRECT_ISSUE_LOOKUPS = 100

log = logging.getLogger(__name__)


class IssueLookupError(RuntimeError):
    """A bounded GitHub issue lookup that cannot prove a complete result."""


def fetch_open_issue_candidate(
    token: str,
    repo: str,
    number: int,
    *,
    request_get=None,
) -> dict[str, Any] | None:
    """Read one durable issue number; ``None`` authoritatively means not open."""
    validate_target_repo(repo)
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise ValueError("issue number must be a positive integer")
    get = request_get or requests.get
    try:
        response = get(
            f"{GH_API}/repos/{repo}/issues/{number}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )
    except requests.RequestException as error:
        raise IssueLookupError(f"read issue #{number} failed") from error
    if response.status_code == 404:
        return None
    if response.status_code >= 300:
        raise IssueLookupError(
            f"read issue #{number} failed: HTTP {response.status_code}"
        )
    try:
        issue = response.json()
    except ValueError as error:
        raise IssueLookupError(f"read issue #{number} returned invalid JSON") from error
    if not isinstance(issue, dict) or issue.get("number") != number:
        raise IssueLookupError(f"read issue #{number} returned an invalid object")
    if "pull_request" in issue:
        raise IssueLookupError(f"tracked issue #{number} resolved to a pull request")
    state = str(issue.get("state") or "").casefold()
    if state == "closed":
        return None
    if state != "open":
        raise IssueLookupError(f"tracked issue #{number} returned an unknown state")
    return issue


def _html_ownership_markers(body: str) -> set[str]:
    return {
        line.strip()
        for line in str(body or "").splitlines()
        if line.strip().startswith("<!--") and line.strip().endswith("-->")
    }


def validate_target_repo(repo: str) -> None:
    if repo.strip().lower() != DASHBOARD_REPO.lower():
        raise RuntimeError(f"Issue automation is restricted to {DASHBOARD_REPO}")


def repo_owner(repo: str) -> str:
    owner = repo.split("/", 1)[0] if "/" in repo else repo
    return (owner or "AndreasKaratzas").strip() or "AndreasKaratzas"


def bounded_open_issue_candidates(
    token: str,
    repo: str,
    *,
    durable_label: str,
    recovery_labels: tuple[str, ...] = (),
    include_recovery: bool = True,
    exact_candidate: Callable[[dict[str, Any]], bool] | None = None,
    request_get=None,
) -> list[dict[str, Any]]:
    """Return a finite candidate set without treating a capped page as complete.

    The watcher-specific label is the primary durable index. The optional
    recent page exists only for recovery after local state and that label were
    both lost. Exact body-marker checks remain the caller's authority.
    """
    validate_target_repo(repo)
    label = str(durable_label or "").strip()
    if not label:
        raise ValueError("durable_label must not be empty")
    get = request_get or requests.get
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    def page(labels: tuple[str, ...], purpose: str) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "state": "open",
            "labels": ",".join(labels),
            "sort": "updated",
            "direction": "desc",
            "per_page": ISSUE_LOOKUP_PAGE_SIZE,
            "page": 1,
        }
        try:
            response = get(
                f"{GH_API}/repos/{repo}/issues",
                headers=headers,
                params=params,
                timeout=30,
            )
        except requests.RequestException as error:
            raise IssueLookupError(f"{purpose} lookup failed") from error
        if response.status_code >= 300:
            raise IssueLookupError(
                f"{purpose} lookup failed: HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise IssueLookupError(f"{purpose} lookup returned invalid JSON") from error
        if not isinstance(payload, list):
            raise IssueLookupError(f"{purpose} lookup returned a non-list payload")
        if len(payload) >= ISSUE_LOOKUP_PAGE_SIZE:
            raise IssueLookupError(
                f"{purpose} lookup reached its bounded ambiguity limit"
            )
        return [
            issue
            for issue in payload
            if isinstance(issue, dict) and "pull_request" not in issue
        ]

    candidates = page((label,), "managed-issue owner-label")
    owner_index_resolved = exact_candidate is not None and any(
        exact_candidate(issue) for issue in candidates
    )
    if include_recovery and not owner_index_resolved:
        normalized_recovery = tuple(
            dict.fromkeys(
                str(value or "").strip()
                for value in recovery_labels
                if str(value or "").strip()
                and str(value or "").strip() != label
            )
        )
        if not normalized_recovery:
            raise ValueError(
                "bounded managed-issue recovery requires at least one shared label"
            )
        candidates.extend(page(normalized_recovery, "managed-issue recent recovery"))

    by_number: dict[int, dict[str, Any]] = {}
    for issue in candidates:
        number = issue.get("number")
        if isinstance(number, int) and not isinstance(number, bool) and number > 0:
            by_number[number] = issue
    return [by_number[number] for number in sorted(by_number)]


def repair_issue_labels(
    token: str,
    repo: str,
    issue: dict[str, Any],
    required_labels: set[str] | frozenset[str],
    *,
    request_post=None,
) -> bool:
    """Idempotently restore missing durable labels on an exact-owned issue."""
    validate_target_repo(repo)
    number = issue.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        return False
    existing = {
        str(label.get("name") if isinstance(label, dict) else label).strip()
        for label in issue.get("labels") or []
    }
    missing = sorted(str(label) for label in required_labels if label not in existing)
    if not missing:
        return True
    post = request_post or requests.post
    try:
        response = post(
            f"{GH_API}/repos/{repo}/issues/{number}/labels",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"labels": missing},
            timeout=30,
        )
    except requests.RequestException:
        return False
    return response.status_code < 300


def normalize_managed_state(state: Any) -> dict:
    data = dict(state) if isinstance(state, dict) else {}
    suppressed = bool(data.get("suppressed"))
    last_fingerprint = str(data.get("last_fingerprint") or "")
    last_content_fingerprint = (
        str(data.get("last_content_fingerprint") or "")
        if "last_content_fingerprint" in data
        else last_fingerprint
    )
    suppressed_fingerprint = str(data.get("suppressed_fingerprint") or "")
    if suppressed and not suppressed_fingerprint:
        # Backward-compatible recovery for states written before the dedicated
        # suppression fingerprint was introduced.
        suppressed_fingerprint = last_fingerprint
    issue = data.get("issue")
    if isinstance(issue, int) and not isinstance(issue, bool):
        issue = {"number": issue, "opened_at": ""}
    elif isinstance(issue, dict):
        number = issue.get("number")
        issue = {
            "number": int(number)
            if isinstance(number, int) and not isinstance(number, bool)
            else 0,
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
        "suppressed": suppressed,
        "suppressed_fingerprint": suppressed_fingerprint,
        "last_fingerprint": last_fingerprint,
        "last_content_fingerprint": last_content_fingerprint,
        "last_run": str(data.get("last_run") or ""),
    }


class GitHubIssueClient:
    def __init__(self, token: str, repo: str):
        validate_target_repo(repo)
        self.token = token
        self.repo = repo
        self.owner = repo_owner(repo)
        self._open_issues_by_lookup: dict[
            tuple[str, str, tuple[str, ...]], list[int]
        ] = {}

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def issue_state(self, number: int, ownership_marker: str) -> str | None:
        try:
            response = requests.get(
                f"{GH_API}/repos/{self.repo}/issues/{number}",
                headers=self.headers,
                timeout=30,
            )
        except requests.RequestException as error:
            raise IssueLookupError(f"read managed issue #{number} failed") from error
        if response.status_code == 404:
            return "closed"
        if response.status_code >= 300:
            raise IssueLookupError(
                f"read managed issue #{number} failed: HTTP {response.status_code}"
            )
        try:
            payload = response.json() or {}
        except ValueError as error:
            raise IssueLookupError(
                f"read managed issue #{number} returned invalid JSON"
            ) from error
        if not isinstance(payload, dict):
            raise IssueLookupError(
                f"read managed issue #{number} returned a non-object payload"
            )
        if "pull_request" in payload:
            log.error("Tracked issue #%d resolved to a pull request", number)
            return "foreign"
        if ownership_marker not in _html_ownership_markers(str(payload.get("body") or "")):
            log.error("Issue #%d lacks the expected ownership marker", number)
            return "foreign"
        state = str(payload.get("state") or "").lower()
        if state not in {"open", "closed"}:
            raise IssueLookupError(
                f"read managed issue #{number} returned an unknown state"
            )
        return state

    def find_open_issues(
        self,
        ownership_marker: str,
        *,
        durable_label: str,
        recovery_labels: tuple[str, ...] = (),
    ) -> list[int]:
        """Find exact-marker issues through two bounded durable indexes."""
        key = (ownership_marker, durable_label, tuple(recovery_labels))
        if key not in self._open_issues_by_lookup:
            candidates = bounded_open_issue_candidates(
                self.token,
                self.repo,
                durable_label=durable_label,
                recovery_labels=tuple(recovery_labels),
                exact_candidate=lambda issue: ownership_marker
                in _html_ownership_markers(str(issue.get("body") or "")),
            )
            matches: list[int] = []
            for issue in candidates:
                number = issue.get("number")
                if ownership_marker not in _html_ownership_markers(
                    str(issue.get("body") or "")
                ):
                    continue
                if (
                    isinstance(number, int)
                    and not isinstance(number, bool)
                    and number > 0
                ):
                    matches.append(number)
            self._open_issues_by_lookup[key] = sorted(set(matches))
        return list(self._open_issues_by_lookup[key])

    def find_open_issue(
        self,
        ownership_marker: str,
        *,
        durable_label: str,
        recovery_labels: tuple[str, ...] = (),
    ) -> int | None:
        """Find the oldest open issue containing the exact marker on its own line."""
        matches = self.find_open_issues(
            ownership_marker,
            durable_label=durable_label,
            recovery_labels=recovery_labels,
        )
        if len(matches) > 1:
            log.error(
                "Found %d open issues with ownership marker %s; recovering the oldest",
                len(matches),
                ownership_marker,
            )
        return matches[0] if matches else None

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

    def ensure_issue_labels(
        self,
        number: int,
        label_specs: list[tuple[str, str, str]],
    ) -> bool:
        """Add managed labels without replacing unrelated labels on the issue."""
        labels = [
            name
            for name, color, description in label_specs
            if self.ensure_label(name, color, description)
        ]
        if not labels:
            return True
        response = requests.post(
            f"{GH_API}/repos/{self.repo}/issues/{number}/labels",
            headers=self.headers,
            json={"labels": labels},
            timeout=30,
        )
        if response.status_code not in {200, 201}:
            log.warning("Add labels to issue #%d failed: %d", number, response.status_code)
            return False
        return True

    def open_issue(
        self,
        title: str,
        body: str,
        label_specs: list[tuple[str, str, str]],
        assignees: list[str] | None = None,
    ) -> int | None:
        labels = [
            name
            for name, color, description in label_specs
            if self.ensure_label(name, color, description)
        ]
        desired_assignees = self._normalized_assignees(assignees)
        response = requests.post(
            f"{GH_API}/repos/{self.repo}/issues",
            headers=self.headers,
            json={
                "title": title,
                "body": body,
                "labels": labels,
                "assignees": desired_assignees,
            },
            timeout=30,
        )
        if response.status_code >= 300:
            log.error("Open managed issue failed: %d %s", response.status_code, response.text[:200])
            return None
        number = (response.json() or {}).get("number")
        if not isinstance(number, int) or isinstance(number, bool):
            return None
        # Creation changes every recovery lookup that could contain the new
        # marker, so invalidate the small per-run cache rather than maintaining
        # stale marker/label combinations.
        self._open_issues_by_lookup.clear()
        return int(number)

    def update_issue(
        self,
        number: int,
        title: str,
        body: str,
        assignees: list[str] | None = None,
    ) -> bool:
        payload: dict[str, Any] = {"title": title, "body": body}
        if assignees is not None:
            payload["assignees"] = self._normalized_assignees(assignees)
        response = requests.patch(
            f"{GH_API}/repos/{self.repo}/issues/{number}",
            headers=self.headers,
            json=payload,
            timeout=30,
        )
        if response.status_code >= 300:
            log.warning("Update issue #%d failed: %d", number, response.status_code)
            return False
        return True

    def _normalized_assignees(self, assignees: list[str] | None) -> list[str]:
        requested = [self.owner] if assignees is None else assignees
        normalized: list[str] = []
        seen: set[str] = set()
        for value in requested:
            login = str(value or "").strip()
            folded = login.lower()
            if not login or folded in seen:
                continue
            seen.add(folded)
            normalized.append(login)
        return normalized

    def is_assignable(self, login: str) -> bool:
        encoded = quote(str(login or "").strip(), safe="")
        if not encoded:
            return False
        response = requests.get(
            f"{GH_API}/repos/{self.repo}/assignees/{encoded}",
            headers=self.headers,
            timeout=30,
        )
        if response.status_code == 204:
            return True
        if response.status_code != 404:
            log.warning(
                "Check whether %s is assignable failed: %d",
                login,
                response.status_code,
            )
        return False

    def set_assignees(self, number: int, assignees: list[str] | None = None) -> bool:
        response = requests.patch(
            f"{GH_API}/repos/{self.repo}/issues/{number}",
            headers=self.headers,
            json={"assignees": self._normalized_assignees(assignees)},
            timeout=30,
        )
        if response.status_code >= 300:
            log.warning("Set assignees on issue #%d failed: %d", number, response.status_code)
            return False
        return True

    def ensure_owner_assigned(self, number: int) -> bool:
        return self.set_assignees(number, [self.owner])

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


def _normalized_issue_numbers(value: object) -> list[int] | None:
    if not isinstance(value, (list, tuple, set)):
        return None
    numbers: set[int] = set()
    for number in value:
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            return None
        numbers.add(number)
    return sorted(numbers)


def _close_verified_open_siblings(
    client: GitHubIssueClient,
    ownership_marker: str,
    open_numbers: list[int],
    canonical_number: int,
) -> bool:
    """Close exact-marker siblings while preserving ``canonical_number``.

    Every sibling is read again before mutation so a stale list response or a
    marker removed by a human cannot authorize a close. Any uncertain read or
    failed close stops reconciliation; the next run can safely retry.
    """
    for sibling_number in open_numbers:
        if sibling_number == canonical_number:
            continue
        try:
            sibling_state = client.issue_state(sibling_number, ownership_marker)
        except Exception as error:
            if isinstance(client, GitHubIssueClient):
                raise
            log.warning(
                "Verify managed sibling #%d failed; refusing further mutation: %s",
                sibling_number,
                error,
            )
            return False
        if sibling_state == "foreign":
            log.warning(
                "Managed sibling candidate #%d no longer has the exact marker; preserving it",
                sibling_number,
            )
            continue
        if sibling_state == "closed":
            continue
        if sibling_state != "open":
            log.warning(
                "Managed sibling #%d state is unverified; refusing further mutation",
                sibling_number,
            )
            return False
        try:
            closed = client.close_issue(sibling_number)
        except Exception as error:
            log.warning("Close managed sibling #%d failed: %s", sibling_number, error)
            return False
        if not closed:
            log.warning("Close managed sibling #%d failed; reconciliation will retry", sibling_number)
            return False
        log.info(
            "Closed duplicate managed issue #%d; canonical issue is #%d",
            sibling_number,
            canonical_number,
        )
    return True


def reconcile_managed_issue(
    state: dict,
    *,
    active: bool,
    fingerprint: str,
    content_fingerprint: str | None = None,
    title: str,
    body: str,
    ownership_marker: str,
    recovery_body: str,
    observed_at: str,
    label_specs: list[tuple[str, str, str]],
    client: GitHubIssueClient,
    assignees: list[str] | None = None,
    discovery_label: str | None = None,
    recovery_labels: tuple[str, ...] | None = None,
) -> dict:
    """Reconcile one state-owned umbrella issue with an alert signal.

    ``fingerprint`` identifies the stable alert signal and controls whether a
    manually closed issue may reopen. ``content_fingerprint`` identifies the
    mutable evidence rendered into an open issue. Callers that do not need the
    distinction retain the legacy behavior because content defaults to signal.
    """
    normalized = normalize_managed_state(state)
    desired_content_fingerprint = (
        fingerprint if content_fingerprint is None else content_fingerprint
    )
    if not ownership_marker.startswith("<!--") or not ownership_marker.endswith("-->"):
        raise ValueError("ownership_marker must be an HTML comment")
    managed_body = body if ownership_marker in body else f"{ownership_marker}\n{body}"
    issue = normalized.get("issue")
    number = int((issue or {}).get("number") or 0)
    recovered = False
    recovered_opened_at = ""

    if number:
        try:
            remote_state = client.issue_state(number, ownership_marker)
        except Exception as error:
            if isinstance(client, GitHubIssueClient):
                raise
            log.warning("Read managed issue #%d failed; preserving local state: %s", number, error)
            return normalized
        if remote_state in {None, "foreign"}:
            log.warning("Issue #%d ownership/state is unverified; preserving local state", number)
            return normalized
        if remote_state == "closed":
            normalized["issue"] = None
            number = 0
            if active:
                normalized["suppressed"] = True
                normalized["suppressed_fingerprint"] = normalized["last_fingerprint"] or fingerprint
                log.info("Issue was manually closed; suppressing until the signal recovers")

    if active:
        if normalized["suppressed"]:
            suppressed_fingerprint = normalized["suppressed_fingerprint"]
            if fingerprint != suppressed_fingerprint:
                normalized["suppressed"] = False
                normalized["suppressed_fingerprint"] = ""
                log.info("Managed signal changed; clearing manual-close suppression")
            else:
                normalized["last_run"] = observed_at
                return normalized

    names = [str(spec[0] or "").strip() for spec in label_specs if spec]
    durable_label = str(discovery_label or "").strip()
    if not durable_label:
        durable_label = next(
            (
                name
                for name in names
                if name
                and name != "automated"
                and not name.startswith("workstream:")
            ),
            names[0] if names else "",
        )
    if recovery_labels is None:
        recovery_labels = tuple(
            name
            for name in names
            if name
            and name != durable_label
            and (name == "automated" or name.startswith("workstream:"))
        )

    # A verified state-owned number is authoritative and avoids every list
    # request. Recovery discovery is needed only when no canonical number is
    # durably known: the crash window in which remote issue creation may have
    # succeeded before the state file was published.
    open_marker_numbers: list[int] | None = None
    if not number and not normalized["suppressed"]:
        find_open_issues = getattr(client, "find_open_issues", None)
        if callable(find_open_issues):
            try:
                if isinstance(client, GitHubIssueClient):
                    if not durable_label:
                        raise ValueError(
                            "managed issue recovery requires a durable discovery label"
                        )
                    raw_open_numbers = find_open_issues(
                        ownership_marker,
                        durable_label=durable_label,
                        recovery_labels=tuple(recovery_labels),
                    )
                else:
                    # Preserve the minimal fake/dry-run client protocol. Real
                    # API clients always use the bounded method above.
                    raw_open_numbers = find_open_issues(ownership_marker)
            except Exception as error:
                if isinstance(client, GitHubIssueClient):
                    raise
                log.warning(
                    "Managed-issue recovery lookup failed; refusing mutation: %s",
                    error,
                )
                return normalized
            open_marker_numbers = _normalized_issue_numbers(raw_open_numbers)
            if open_marker_numbers is None:
                log.warning("Managed-issue recovery lookup returned invalid issue numbers")
                return normalized

    if not number and not normalized["suppressed"]:
        if open_marker_numbers is not None:
            recovered_number = open_marker_numbers[0] if open_marker_numbers else None
        else:
            find_open_issue = getattr(client, "find_open_issue", None)
            recovered_number = None
            if callable(find_open_issue):
                try:
                    if isinstance(client, GitHubIssueClient):
                        recovered_number = find_open_issue(
                            ownership_marker,
                            durable_label=durable_label,
                            recovery_labels=tuple(recovery_labels),
                        )
                    else:
                        recovered_number = find_open_issue(ownership_marker)
                except Exception as error:
                    if isinstance(client, GitHubIssueClient):
                        raise
                    log.warning("Managed-issue recovery lookup failed; refusing mutation: %s", error)
                    return normalized
        if isinstance(recovered_number, int) and not isinstance(recovered_number, bool):
            if recovered_number > 0:
                number = recovered_number
                recovered_opened_at = str((issue or {}).get("opened_at") or observed_at)
                recovered = True

    if open_marker_numbers is not None and not _close_verified_open_siblings(
        client,
        ownership_marker,
        open_marker_numbers,
        number,
    ):
        return normalized

    if recovered:
        normalized["issue"] = {
            "number": number,
            "opened_at": recovered_opened_at,
        }
        log.info("Recovered open managed issue #%d from its ownership marker", number)

    if active:
        if number:
            ensure_issue_labels = getattr(client, "ensure_issue_labels", None)
            if callable(ensure_issue_labels):
                if not ensure_issue_labels(number, label_specs):
                    log.warning(
                        "Managed issue #%d labels could not be verified; preserving state",
                        number,
                    )
                    return normalized
            if assignees is None:
                client.ensure_owner_assigned(number)
            else:
                client.set_assignees(number, assignees)
            if (
                recovered
                or normalized["last_fingerprint"] != fingerprint
                or normalized["last_content_fingerprint"] != desired_content_fingerprint
            ):
                updated = (
                    client.update_issue(number, title, managed_body)
                    if assignees is None
                    else client.update_issue(number, title, managed_body, assignees)
                )
                if updated:
                    normalized["last_fingerprint"] = fingerprint
                    normalized["last_content_fingerprint"] = desired_content_fingerprint
                elif recovered:
                    # Do not adopt a recovered issue locally until its managed
                    # title/body have been refreshed successfully. Otherwise a
                    # transient PATCH failure with already-matching hashes makes
                    # the forced recovery refresh impossible to retry.
                    normalized["issue"] = None
                    number = 0
        else:
            opened_number = (
                client.open_issue(title, managed_body, label_specs)
                if assignees is None
                else client.open_issue(
                    title,
                    managed_body,
                    label_specs,
                    assignees,
                )
            )
            if opened_number:
                normalized["issue"] = {
                    "number": opened_number,
                    "opened_at": observed_at,
                }
                normalized["suppressed_fingerprint"] = ""
                normalized["last_fingerprint"] = fingerprint
                normalized["last_content_fingerprint"] = desired_content_fingerprint
    else:
        if number:
            ensure_issue_labels = getattr(client, "ensure_issue_labels", None)
            if callable(ensure_issue_labels):
                if not ensure_issue_labels(number, label_specs):
                    log.warning(
                        "Managed issue #%d labels could not be verified; preserving state",
                        number,
                    )
                    return normalized
            if assignees is None:
                client.ensure_owner_assigned(number)
            else:
                client.set_assignees(number, assignees)
            client.comment_issue(number, recovery_body)
            if client.close_issue(number):
                normalized["issue"] = None
                number = 0
        if not number:
            normalized["suppressed"] = False
            normalized["suppressed_fingerprint"] = ""
            normalized["last_fingerprint"] = ""
            normalized["last_content_fingerprint"] = ""

    normalized["last_run"] = observed_at
    return normalized
