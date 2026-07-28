#!/usr/bin/env python3
"""Add open dashboard automation issues to the AMD CI Operations project.

The repository ``GITHUB_TOKEN`` is used only to discover eligible issues. A
separate Projects V2 token performs the single permitted mutation: adding an
issue to the configured project. The script never removes items, edits issues,
or touches another repository/project. Missing project credentials are a safe
no-op so collection and dashboard deployment continue.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.ci.managed_issue import DASHBOARD_REPO, validate_target_repo  # noqa: E402
from vllm.ci.ownership import load_ownership_config  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG = ROOT / "config" / "vllm_ci_ownership.json"
GH_API = "https://api.github.com"
GH_GRAPHQL = f"{GH_API}/graphql"
AUTOMATED_LABEL = "automated"
WORKSTREAM_PREFIX = "workstream:"
ALLOWED_WORKSTREAMS = {
    "workstream:infra",
    "workstream:dashboard-ci",
    "workstream:dev",
}
EXPECTED_PROJECT = {
    "id": "PVT_kwHOAofB1M4BepHY",
    "owner": "AndreasKaratzas",
    "number": 2,
    "title": "AMD CI Operations",
    "url": "https://github.com/users/AndreasKaratzas/projects/2",
}

PROJECT_ITEMS_QUERY = """
query($projectId: ID!, $cursor: String) {
  node(id: $projectId) {
    ... on ProjectV2 {
      id
      number
      title
      url
      public
      closed
      viewerCanUpdate
      owner {
        __typename
        ... on User { login }
        ... on Organization { login }
      }
      repositories(first: 10) {
        totalCount
        nodes { nameWithOwner }
      }
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          content {
            __typename
            ... on Issue {
              id
              number
              repository { nameWithOwner }
            }
          }
        }
      }
    }
  }
}
"""

ADD_PROJECT_ITEM_MUTATION = """
mutation($projectId: ID!, $contentId: ID!) {
  addProjectV2ItemById(input: {
    projectId: $projectId,
    contentId: $contentId
  }) {
    item { id }
  }
}
"""


def _headers(token: str) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _graphql(token: str, query: str, variables: dict[str, Any]) -> dict:
    response = requests.post(
        GH_GRAPHQL,
        headers=_headers(token),
        json={"query": query, "variables": variables},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json() or {}
    if payload.get("errors"):
        raise RuntimeError(f"GitHub GraphQL error: {payload['errors']}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("GitHub GraphQL response did not contain data")
    return data


def _label_names(issue: dict) -> set[str]:
    values = issue.get("labels") or []
    names: set[str] = set()
    for value in values:
        name = value.get("name") if isinstance(value, dict) else value
        normalized = str(name or "").strip().casefold()
        if normalized:
            names.add(normalized)
    return names


def eligible_issue(issue: Any) -> bool:
    if not isinstance(issue, dict) or "pull_request" in issue:
        return False
    if str(issue.get("state") or "").casefold() != "open":
        return False
    node_id = str(issue.get("node_id") or "").strip()
    if not node_id:
        return False
    labels = _label_names(issue)
    workstreams = {label for label in labels if label.startswith(WORKSTREAM_PREFIX)}
    return (
        AUTOMATED_LABEL in labels
        and len(workstreams) == 1
        and workstreams <= ALLOWED_WORKSTREAMS
    )


def fetch_eligible_issues(token: str, repo: str) -> list[dict]:
    validate_target_repo(repo)
    rows_by_id: dict[str, dict] = {}
    page = 1
    while True:
        response = requests.get(
            f"{GH_API}/repos/{repo}/issues",
            headers=_headers(token),
            params={
                "state": "open",
                "labels": AUTOMATED_LABEL,
                "per_page": 100,
                "page": page,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json() or []
        if not isinstance(payload, list):
            raise RuntimeError("GitHub issues response was not a list")
        for issue in payload:
            if eligible_issue(issue):
                rows_by_id[str(issue["node_id"])] = issue
        if len(payload) < 100:
            break
        page += 1
    rows = list(rows_by_id.values())
    rows.sort(key=lambda issue: int(issue.get("number") or 0))
    return rows


def validate_project(project: Any, project_id: str, repo: str) -> dict:
    if not isinstance(project, dict) or project.get("id") != project_id:
        raise RuntimeError("Configured AMD CI Operations project was not found")
    owner = project.get("owner") or {}
    repositories = project.get("repositories") or {}
    linked = {
        str(row.get("nameWithOwner") or "").casefold()
        for row in repositories.get("nodes") or []
        if isinstance(row, dict)
    }
    expected = {
        "id": project_id == EXPECTED_PROJECT["id"],
        "owner": str(owner.get("login") or "").casefold()
        == EXPECTED_PROJECT["owner"].casefold(),
        "number": project.get("number") == EXPECTED_PROJECT["number"],
        "title": project.get("title") == EXPECTED_PROJECT["title"],
        "url": project.get("url") == EXPECTED_PROJECT["url"],
        "public": project.get("public") is True,
        "open": project.get("closed") is False,
        "updateable": project.get("viewerCanUpdate") is True,
        "repository_count": repositories.get("totalCount") == 1,
        "repository": linked == {repo.casefold()},
    }
    failures = sorted(name for name, valid in expected.items() if not valid)
    if failures:
        raise RuntimeError(
            "Configured Project failed scope validation: " + ", ".join(failures)
        )
    return project


def fetch_project_issue_ids(token: str, project_id: str, repo: str) -> set[str]:
    validate_target_repo(repo)
    ids: set[str] = set()
    cursor: str | None = None
    while True:
        data = _graphql(
            token,
            PROJECT_ITEMS_QUERY,
            {"projectId": project_id, "cursor": cursor},
        )
        project = validate_project(data.get("node"), project_id, repo)
        connection = project.get("items") or {}
        for item in connection.get("nodes") or []:
            content = item.get("content") if isinstance(item, dict) else None
            if not isinstance(content, dict) or content.get("__typename") != "Issue":
                continue
            repository = content.get("repository") or {}
            if str(repository.get("nameWithOwner") or "").casefold() != repo.casefold():
                continue
            issue_id = str(content.get("id") or "").strip()
            if issue_id:
                ids.add(issue_id)
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return ids
        cursor = str(page_info.get("endCursor") or "").strip() or None
        if cursor is None:
            raise RuntimeError("Project pagination stopped without an end cursor")


def add_project_item(token: str, project_id: str, content_id: str) -> None:
    data = _graphql(
        token,
        ADD_PROJECT_ITEM_MUTATION,
        {"projectId": project_id, "contentId": content_id},
    )
    item = (data.get("addProjectV2ItemById") or {}).get("item") or {}
    if not str(item.get("id") or "").strip():
        raise RuntimeError("GitHub did not return the added project item")


def _write_step_summary(message: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if not path:
        return
    try:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(f"### AMD CI Operations Project sync\n\n{message}\n")
    except OSError:
        log.warning("Could not write the GitHub Actions step summary")


def run() -> int:
    repo = os.getenv("GITHUB_REPOSITORY") or DASHBOARD_REPO
    validate_target_repo(repo)
    config = load_ownership_config(CONFIG)
    project = config["project"]
    for key in ("id", "number", "title", "url"):
        expected = EXPECTED_PROJECT[key]
        if project.get(key) != expected:
            raise RuntimeError(f"Ownership config has unexpected Project {key}")
    if str(project.get("repository") or "").casefold() != repo.casefold():
        raise RuntimeError("Configured project is not restricted to the dashboard repository")

    project_token = os.getenv("PROJECTS_WRITE_TOKEN", "").strip()
    if not project_token:
        log.warning(
            "PROJECTS_WRITE_TOKEN is not configured; skipping AMD CI Operations project sync"
        )
        _write_step_summary(
            "Skipped safely because `PROJECTS_WRITE_TOKEN` is not configured."
        )
        return 0

    repo_token = os.getenv("GITHUB_TOKEN", "").strip()
    if not repo_token:
        log.warning("GITHUB_TOKEN is not configured; skipping Project sync")
        _write_step_summary("Skipped safely because `GITHUB_TOKEN` is not configured.")
        return 0
    issues = fetch_eligible_issues(repo_token, repo)
    existing = fetch_project_issue_ids(project_token, project["id"], repo)
    missing = [issue for issue in issues if str(issue["node_id"]) not in existing]
    for issue in missing:
        add_project_item(project_token, project["id"], str(issue["node_id"]))
        log.info("Added dashboard issue #%s to AMD CI Operations", issue.get("number"))
    log.info(
        "AMD CI Operations project sync complete: %d eligible, %d already present, %d added",
        len(issues),
        len(issues) - len(missing),
        len(missing),
    )
    _write_step_summary(
        f"Eligible issues: **{len(issues)}** · already present: "
        f"**{len(issues) - len(missing)}** · added: **{len(missing)}**."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as error:
        _write_step_summary(
            f"Sync failed closed before further mutations (`{type(error).__name__}`)."
        )
        raise
