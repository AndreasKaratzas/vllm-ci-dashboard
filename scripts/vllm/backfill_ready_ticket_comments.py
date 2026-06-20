#!/usr/bin/env python3
"""One-shot backfill for ready-ticket noise on project #39.

This is intentionally standalone so it can be run manually when the comment
renderer changes and we want to clean up already-open CI-failure tickets.

Scope is deliberately narrow:

* only scans issues that are still OPEN on ``vllm-project/projects/39``
* only considers comments authored by the allowlisted login(s)
* refreshes the issue body from ``data/vllm/ci/ready_tickets.json`` when a
  current ticket entry exists for that issue number
* deletes automation-generated failure comments so the issue body is the
  single source of truth for an open failure
* defaults to dry-run

Usage:

    export GITHUB_TOKEN=...
    python scripts/vllm/backfill_ready_ticket_comments.py \
      --author github-actions[bot]

    python scripts/vllm/backfill_ready_ticket_comments.py \
      --author AndreasKaratzas \
      --write
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import requests


GH_API = "https://api.github.com"
GH_GRAPHQL = "https://api.github.com/graphql"
PROJECT_ORG = "vllm-project"
PROJECT_NUMBER = 39
ISSUE_REPO = "vllm-project/vllm"
DEFAULT_BODY_SOURCE = "data/vllm/ci/ready_tickets.json"
DEFAULT_PROJECT_ITEMS_SOURCE = "data/vllm/ci/project_items.json"

PROJECT_ITEMS_Q = """
query($org: String!, $number: Int!, $cursor: String) {
  organization(login: $org) {
    projectV2(number: $number) {
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          content {
            __typename
            ... on Issue {
              number
              title
              state
              body
              url
              repository { nameWithOwner }
            }
          }
        }
      }
    }
  }
}
"""

_STILL_FAILING_RE = re.compile(r"^Still failing as of \d{4}-\d{2}-\d{2}\. Build\(s\): ", re.MULTILINE)
_SYNC_BODY_RE = re.compile(r"^## AMD nightly — failing test group\s*$", re.MULTILINE)
_AUTO_MANAGED_MARKER = "Auto-managed by `sync_ready_tickets.py`."


@dataclass(frozen=True)
class IssueRef:
    issue_number: int
    title: str
    body: str
    url: str
    repo: str


def _rest_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _graphql(token: str, query: str, variables: dict) -> dict:
    resp = requests.post(
        GH_GRAPHQL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"query": query, "variables": variables},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if "errors" in payload:
        raise RuntimeError(json.dumps(payload["errors"], indent=2))
    return payload["data"]


def _iter_open_project_issues(
    token: str,
    *,
    org: str,
    project_number: int,
    repo_full_name: str,
) -> list[IssueRef]:
    issues: list[IssueRef] = []
    cursor: str | None = None
    while True:
        data = _graphql(
            token,
            PROJECT_ITEMS_Q,
            {"org": org, "number": project_number, "cursor": cursor},
        )
        page = data["organization"]["projectV2"]["items"]
        for node in page["nodes"]:
            content = node.get("content") or {}
            if content.get("__typename") != "Issue":
                continue
            if content["repository"]["nameWithOwner"] != repo_full_name:
                continue
            if (content.get("state") or "").upper() != "OPEN":
                continue
            issues.append(
                IssueRef(
                    issue_number=int(content["number"]),
                    title=content["title"],
                    body=content.get("body") or "",
                    url=content["url"],
                    repo=content["repository"]["nameWithOwner"],
                )
            )
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return issues


def _iter_open_project_issues_from_snapshot(path: str, *, repo_full_name: str) -> list[IssueRef]:
    try:
        payload = json.loads(Path(path).read_text())
    except OSError:
        return []

    issues: list[IssueRef] = []
    for raw in (payload.get("items_by_number") or {}).values():
        if raw.get("repo") != repo_full_name:
            continue
        if (raw.get("issue_state") or "").upper() != "OPEN":
            continue
        issue_number = raw.get("issue_number")
        if issue_number is None:
            continue
        issue_number = int(issue_number)
        issues.append(
            IssueRef(
                issue_number=issue_number,
                title=raw.get("title") or f"Issue #{issue_number}",
                body="",
                url=raw.get("url") or f"https://github.com/{repo_full_name}/issues/{issue_number}",
                repo=repo_full_name,
            )
        )
    issues.sort(key=lambda item: item.issue_number)
    return issues


def _fetch_issue(token: str, repo_full_name: str, issue_number: int) -> IssueRef:
    resp = requests.get(
        f"{GH_API}/repos/{repo_full_name}/issues/{issue_number}",
        headers=_rest_headers(token),
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    return IssueRef(
        issue_number=int(payload["number"]),
        title=payload.get("title") or f"Issue #{issue_number}",
        body=payload.get("body") or "",
        url=payload.get("html_url") or f"https://github.com/{repo_full_name}/issues/{issue_number}",
        repo=repo_full_name,
    )


def _issue_comments(token: str, repo_full_name: str, issue_number: int) -> list[dict]:
    comments: list[dict] = []
    page = 1
    while True:
        resp = requests.get(
            f"{GH_API}/repos/{repo_full_name}/issues/{issue_number}/comments",
            headers=_rest_headers(token),
            params={"per_page": 100, "page": page},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        comments.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return comments


def _load_desired_issue_bodies(path: str) -> dict[int, str]:
    try:
        payload = json.loads(open(path).read())
    except OSError:
        return {}
    return {
        int(ticket["issue_number"]): ticket["body"]
        for ticket in payload.get("tickets", [])
        if ticket.get("issue_number") and ticket.get("body")
    }


def is_generated_ready_ticket_comment(body: str) -> bool:
    text = body or ""
    if _STILL_FAILING_RE.search(text):
        return True
    if _SYNC_BODY_RE.search(text) and _AUTO_MANAGED_MARKER in text:
        return True
    return False


def _update_issue_body(token: str, repo_full_name: str, issue_number: int, body: str) -> None:
    resp = requests.patch(
        f"{GH_API}/repos/{repo_full_name}/issues/{issue_number}",
        headers=_rest_headers(token),
        json={"body": body},
        timeout=30,
    )
    resp.raise_for_status()


def _delete_comment(token: str, repo_full_name: str, comment_id: int) -> None:
    resp = requests.delete(
        f"{GH_API}/repos/{repo_full_name}/issues/comments/{comment_id}",
        headers=_rest_headers(token),
        timeout=30,
    )
    resp.raise_for_status()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", default=os.getenv("GITHUB_TOKEN"))
    parser.add_argument("--org", default=PROJECT_ORG)
    parser.add_argument("--project-number", type=int, default=PROJECT_NUMBER)
    parser.add_argument("--repo", default=ISSUE_REPO)
    parser.add_argument(
        "--scan-source",
        choices=("project-items", "api"),
        default="project-items",
        help="How to discover the open project issues to scan. "
        "'project-items' uses the local snapshot; 'api' queries GitHub ProjectV2 directly.",
    )
    parser.add_argument(
        "--project-items-source",
        default=DEFAULT_PROJECT_ITEMS_SOURCE,
        help="Path to the project_items.json snapshot used when --scan-source=project-items.",
    )
    parser.add_argument("--body-source", default=DEFAULT_BODY_SOURCE)
    parser.add_argument(
        "--author",
        action="append",
        default=[],
        help="Allowed GitHub login whose generated comments may be deleted. Repeatable. Defaults to github-actions[bot].",
    )
    parser.add_argument("--limit", type=int, default=0, help="Only scan the first N open project issues.")
    parser.add_argument("--write", action="store_true", help="Apply updates instead of dry-run.")
    args = parser.parse_args(argv)

    if not args.token:
        print("error: set --token or GITHUB_TOKEN", file=sys.stderr)
        return 2

    authors = args.author or ["github-actions[bot]"]
    if args.scan_source == "project-items":
        issues = _iter_open_project_issues_from_snapshot(
            args.project_items_source,
            repo_full_name=args.repo,
        )
        if not issues:
            print(
                f"error: no open issues found in {args.project_items_source}; "
                "rerun after syncing project_items.json or use --scan-source api",
                file=sys.stderr,
            )
            return 2
    else:
        issues = _iter_open_project_issues(
            args.token,
            org=args.org,
            project_number=args.project_number,
            repo_full_name=args.repo,
        )
    if args.limit > 0:
        issues = issues[:args.limit]
    desired_bodies = _load_desired_issue_bodies(args.body_source)

    scanned_comments = 0
    candidate_comments = 0
    deleted_comments = 0
    candidate_bodies = 0
    updated_bodies = 0

    for issue_seed in issues:
        issue = _fetch_issue(args.token, issue_seed.repo, issue_seed.issue_number)
        desired_body = desired_bodies.get(issue.issue_number)
        if desired_body and desired_body != issue.body:
            candidate_bodies += 1
            status = "update-body" if args.write else "would-update-body"
            print(f"{status}: issue #{issue.issue_number} ({issue.title})")
            if args.write:
                _update_issue_body(args.token, issue.repo, issue.issue_number, desired_body)
                updated_bodies += 1
        comments = _issue_comments(args.token, issue.repo, issue.issue_number)
        for comment in comments:
            scanned_comments += 1
            author = ((comment.get("user") or {}).get("login") or "").strip()
            if author not in authors:
                continue
            if not is_generated_ready_ticket_comment(comment.get("body") or ""):
                continue
            candidate_comments += 1
            status = "delete-comment" if args.write else "would-delete-comment"
            print(
                f"{status}: issue #{issue.issue_number} comment {comment['id']} by {author} "
                f"({issue.title})"
            )
            if args.write:
                _delete_comment(args.token, issue.repo, int(comment["id"]))
                deleted_comments += 1

    print(
        f"scanned {len(issues)} open project issues, {scanned_comments} comments, "
        f"{candidate_bodies} candidate bodies, {updated_bodies} updated bodies, "
        f"{candidate_comments} candidate comments, {deleted_comments} deleted"
    )
    if not args.write:
        print("dry-run only; rerun with --write to apply updates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
