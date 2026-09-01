#!/usr/bin/env python3
"""Collect project data from GitHub API and write to data/ as JSON."""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "projects.yaml"
DATA = ROOT / "data"
PROJECT_ORG = "vllm-project"
PROJECT_NUMBER = 39
PROJECT_URL = f"https://github.com/orgs/{PROJECT_ORG}/projects/{PROJECT_NUMBER}"

# Every multi-page GitHub read has a fixed request ceiling.  These limits are
# deliberately larger than the Home UI's useful working set while preventing
# repository growth from making collection cost unbounded.
REST_PAGE_SIZE = 100
MAX_OPEN_ITEM_PAGES = 5
MAX_LABEL_SEARCH_PAGES = 3
MAX_PROJECT_OPEN_ISSUES = 100
MAX_ISSUE_COMMENT_PAGES = 2
MAX_LINKED_PRS_PER_ISSUE = 20
MAX_DIRECT_LINKED_PR_LOOKUPS = 100
MAX_COPYBARA_AUTHOR_LOOKUPS = 50
GH_TRANSIENT_ATTEMPTS = 2


class GitHubAPIError(RuntimeError):
    """A GitHub response that must not be mistaken for an empty result."""


_SOURCE_QUERY_COVERAGE = []
_PROJECT_QUERY_USABLE = False

_PULL_URL_RE = re.compile(
    r"https?://github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)",
    re.IGNORECASE,
)
_BUILDKITE_BUILD_URL_RE = re.compile(
    r"https?://buildkite\.com/[^/\s)]+/[^/\s)]+/builds/(\d+)",
    re.IGNORECASE,
)
_PR_CONTEXT_REF_RE = re.compile(
    r"(?i)\b(?:pr|pull request|pull)\b[^\n#]{0,160}#(\d+)"
)


def _transient_gh_failure(stderr):
    message = str(stderr or "").lower()
    return any(
        token in message
        for token in (
            "http 429",
            "http 500",
            "http 502",
            "http 503",
            "http 504",
            "connection reset",
            "temporary failure",
            "timed out",
            "timeout",
            "unexpected eof",
        )
    )


def gh_api(endpoint, method="GET", *, fail_closed=False):
    """Call GitHub API via gh CLI with one bounded transient retry."""
    cmd = ["gh", "api", endpoint, "--method", method]
    for attempt in range(1, GH_TRANSIENT_ATTEMPTS + 1):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            if attempt < GH_TRANSIENT_ATTEMPTS and _transient_gh_failure(e.stderr):
                print(
                    f"  WARNING: transient gh api failure for {endpoint}; "
                    "retrying once",
                    file=sys.stderr,
                )
                continue
            detail = str(e.stderr or "").strip()
            print(f"  WARNING: gh api {endpoint} failed: {detail}", file=sys.stderr)
            if fail_closed:
                raise GitHubAPIError(f"GitHub API request failed: {endpoint}") from e
            return []
        try:
            if not result.stdout.strip():
                raise json.JSONDecodeError("empty GitHub response", "", 0)
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            if attempt < GH_TRANSIENT_ATTEMPTS:
                print(
                    f"  WARNING: invalid gh api response for {endpoint}; "
                    "retrying once",
                    file=sys.stderr,
                )
                continue
            print(
                f"  WARNING: could not parse response for {endpoint}", file=sys.stderr
            )
            if fail_closed:
                raise GitHubAPIError(
                    f"GitHub API returned invalid JSON: {endpoint}"
                ) from e
            return []
    raise AssertionError("bounded GitHub retry loop exhausted unexpectedly")


def _reset_source_coverage():
    global _PROJECT_QUERY_USABLE
    _SOURCE_QUERY_COVERAGE.clear()
    _PROJECT_QUERY_USABLE = False


def _record_source_query(**coverage):
    _SOURCE_QUERY_COVERAGE.append(coverage)


def _source_coverage_snapshot():
    queries = [dict(query) for query in _SOURCE_QUERY_COVERAGE]
    complete = all(query.get("complete") is True for query in queries)
    authoritative_complete = all(
        query.get("complete") is True
        for query in queries
        if query.get("authoritative", True)
    )
    return {
        "complete": complete,
        "authoritative_complete": authoritative_complete,
        "population_semantics": (
            "complete" if authoritative_complete else "lower_bound"
        ),
        "truncated": any(query.get("truncated") is True for query in queries),
        "queries": queries,
    }


def _page_endpoint(endpoint, page, per_page=REST_PAGE_SIZE):
    """Return an endpoint with an explicit, non-overridable page boundary."""
    if re.search(r"(?:^|[?&])page=", endpoint):
        raise ValueError("bounded GitHub endpoint must not supply its own page")
    if re.search(r"(?:^|[?&])per_page=", endpoint):
        endpoint = re.sub(
            r"([?&]per_page=)\d+", rf"\g<1>{per_page}", endpoint, count=1
        )
    else:
        endpoint += ("&" if "?" in endpoint else "?") + f"per_page={per_page}"
    return endpoint + f"&page={page}"


def _bounded_rest_items(
    endpoint,
    *,
    query_name,
    scope,
    max_pages,
    item_key=None,
    stop_when=None,
    authoritative=True,
    allow_partial=False,
    allow_errors=False,
):
    """Fetch a finite REST working set and publish honest coverage metadata.

    ``stop_when`` may prove a sorted time-window query complete before the page
    cap.  A full final allowed page without such proof is explicitly truncated.
    Authoritative queries then raise instead of publishing a false exhaustive
    population. Optional enrichment callers must opt into partial/error results.
    """
    if not isinstance(max_pages, int) or max_pages <= 0:
        raise ValueError("max_pages must be a positive integer")

    items = []
    pages_fetched = 0
    total_count_hint = None
    provider_incomplete = False
    complete = False
    completion_reason = "page_cap"
    try:
        for page_number in range(1, max_pages + 1):
            response = gh_api(
                _page_endpoint(endpoint, page_number),
                fail_closed=True,
            )
            pages_fetched += 1
            if item_key is None:
                if not isinstance(response, list):
                    raise GitHubAPIError(
                        f"GitHub {query_name} response was not a list"
                    )
                page_items = response
            else:
                if not isinstance(response, dict) or not isinstance(
                    response.get(item_key), list
                ):
                    raise GitHubAPIError(
                        f"GitHub {query_name} response lacked {item_key!r}"
                    )
                page_items = response[item_key]
                raw_total = response.get("total_count")
                if isinstance(raw_total, int) and raw_total >= 0:
                    total_count_hint = raw_total
                # GitHub Search can return HTTP 200 with a deliberately
                # incomplete result.  That is usable as a lower bound, but it
                # must never satisfy the exhaustive-population contract.
                if response.get("incomplete_results") is True:
                    provider_incomplete = True

            items.extend(page_items)
            if provider_incomplete:
                completion_reason = "provider_incomplete"
                break
            if stop_when is not None and any(stop_when(item) for item in page_items):
                complete = True
                completion_reason = "scope_boundary"
                break
            if total_count_hint is not None and len(items) >= total_count_hint:
                complete = True
                completion_reason = "reported_total"
                break
            if len(page_items) < REST_PAGE_SIZE:
                complete = True
                completion_reason = "short_page"
                break
    except GitHubAPIError:
        _record_source_query(
            name=query_name,
            scope=scope,
            complete=False,
            truncated=False,
            error=True,
            authoritative=authoritative,
            pages_fetched=pages_fetched,
            max_pages=max_pages,
            page_size=REST_PAGE_SIZE,
            items_observed=len(items),
            completion_reason="api_error",
        )
        if allow_errors:
            return items
        raise

    _record_source_query(
        name=query_name,
        scope=scope,
        complete=complete,
        truncated=not complete,
        error=False,
        authoritative=authoritative,
        pages_fetched=pages_fetched,
        max_pages=max_pages,
        page_size=REST_PAGE_SIZE,
        items_observed=len(items),
        total_count_hint=total_count_hint,
        provider_incomplete=provider_incomplete,
        completion_reason=completion_reason,
    )
    if not complete and not allow_partial:
        raise GitHubAPIError(
            f"GitHub {query_name} reached its authoritative {max_pages}-page cap"
        )
    return items


def gh_graphql(query, variables=None, *, fail_closed=False):
    """Call a read-only GitHub GraphQL query with one transient retry."""
    if re.search(r"\bmutation\b", query, flags=re.IGNORECASE):
        raise ValueError("scripts/collect.py does not permit GraphQL mutations")
    variables = variables or {}
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            continue
        cmd.extend(["-F", f"{key}={value}"])
    env = os.environ.copy()
    if os.getenv("PROJECTS_READ_TOKEN"):
        env["GH_TOKEN"] = os.getenv("PROJECTS_READ_TOKEN")
    for attempt in range(1, GH_TRANSIENT_ATTEMPTS + 1):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True, env=env
            )
        except subprocess.CalledProcessError as e:
            if attempt < GH_TRANSIENT_ATTEMPTS and _transient_gh_failure(e.stderr):
                print(
                    "  WARNING: transient gh graphql failure; retrying once",
                    file=sys.stderr,
                )
                continue
            detail = str(e.stderr or "").strip()
            print(f"  WARNING: gh graphql failed: {detail}", file=sys.stderr)
            if fail_closed:
                raise GitHubAPIError("GitHub GraphQL request failed") from e
            return {}
        try:
            if not result.stdout.strip():
                raise json.JSONDecodeError("empty GitHub response", "", 0)
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            if attempt < GH_TRANSIENT_ATTEMPTS:
                print(
                    "  WARNING: invalid gh graphql response; retrying once",
                    file=sys.stderr,
                )
                continue
            print("  WARNING: could not parse GraphQL response", file=sys.stderr)
            if fail_closed:
                raise GitHubAPIError("GitHub GraphQL returned invalid JSON") from e
            return {}
        if fail_closed and (
            not isinstance(payload, dict) or payload.get("errors") or not payload
        ):
            raise GitHubAPIError("GitHub GraphQL returned errors or an empty payload")
        return payload
    raise AssertionError("bounded GitHub GraphQL retry loop exhausted unexpectedly")


def discover_email_domain_authors(repo, email_domains, max_pages=3):
    """Discover GitHub usernames whose commit emails match given domains."""
    authors = set()
    commits = _bounded_rest_items(
        f"/repos/{repo}/commits?per_page=100",
        query_name="email_domain_authors",
        scope=f"newest {REST_PAGE_SIZE * max_pages} commits in {repo}",
        max_pages=max_pages,
        allow_partial=True,
    )
    for commit in commits:
        email = commit.get("commit", {}).get("author", {}).get("email", "")
        for domain in email_domains:
            if email.endswith(f"@{domain}"):
                login = commit.get("author")
                if login and login.get("login"):
                    authors.add(login["login"])
    return list(authors)


def fetch_prs(repo, authors, labels, keywords, keyword_scope=""):
    """Fetch open and recently merged PRs matching filters."""
    prs = []

    # Fetch PRs by author (all states)
    if authors:
        tracked_authors = set(authors)
        items = gh_api(
            f"/repos/{repo}/pulls?state=all&sort=updated&direction=desc&per_page=30",
            fail_closed=True,
        )
        for pr in items:
            if pr.get("user", {}).get("login") in tracked_authors:
                prs.append(pr)

    # Fetch PRs by label (open + closed/merged)
    tracked_labels = {label.lower() for label in labels}
    if tracked_labels:
        for state in ["open", "closed"]:
            items = gh_api(
                f"/repos/{repo}/pulls?state={state}&sort=updated&direction=desc&per_page=30",
                fail_closed=True,
            )
            for pr in items:
                pr_labels = [l["name"].lower() for l in pr.get("labels", [])]
                if tracked_labels.intersection(pr_labels) and not any(
                    p["number"] == pr["number"] for p in prs
                ):
                    prs.append(pr)

    # Search PRs by keyword (open + merged)
    scope = f"+in:{keyword_scope}" if keyword_scope else ""
    for kw in keywords:
        for pr_filter in ["is:open", "is:merged"]:
            search_results = gh_api(
                f"/search/issues?q={kw}{scope}+repo:{repo}+is:pr+{pr_filter}&sort=updated&per_page=30",
                fail_closed=True,
            )
            if isinstance(search_results, list):
                search_results = {}
            for pr in search_results.get("items", []):
                if not any(p["number"] == pr["number"] for p in prs):
                    prs.append(pr)

    # Deduplicate by number + drop anything that isn't actually a PR. The
    # /search/issues endpoint, even with is:pr, occasionally returns plain
    # issues when item types change; and callers of /repos/:r/pulls that
    # post-filter by label can drift similarly. html_url is the unambiguous
    # discriminator: PRs live under /pull/<n>, issues under /issues/<n>.
    seen = set()
    unique = []
    for pr in prs:
        num = pr["number"]
        if num in seen:
            continue
        html_url = pr.get("html_url", "") or ""
        is_pr = (
            "/pull/" in html_url
            or pr.get("pull_request") is not None  # /search/issues shape
        )
        if not is_pr:
            continue
        seen.add(num)
        unique.append(normalize_pr(pr))
    return sorted(unique, key=lambda p: p["updated_at"], reverse=True)


def normalize_pr(pr):
    """Extract relevant PR fields."""
    # Truncated body so the dashboard can detect ``fixes #N`` / ``closes #N``
    # linked-issue references without blowing up the JSON size. Those
    # references always appear near the top of the PR body (the GitHub
    # "Linked issues" parser only recognizes keywords at the start of a line),
    # so a 2 kB slice is enough.
    body = pr.get("body") or ""
    return {
        "number": pr["number"],
        "title": pr.get("title", ""),
        "author": pr.get("user", {}).get("login", ""),
        "state": pr.get("state", ""),
        "merged": pr.get("merged_at") is not None
        or pr.get("pull_request", {}).get("merged_at") is not None,
        "created_at": pr.get("created_at", ""),
        "updated_at": pr.get("updated_at", ""),
        "html_url": pr.get("html_url", ""),
        "labels": [l["name"] for l in pr.get("labels", [])],
        "draft": pr.get("draft", False),
        "body_head": body[:2000],
    }


def fetch_open_label_prs(repo, labels):
    """Fetch all open PRs that carry any of ``labels``.

    GitHub's Pulls REST endpoint does not support server-side label filters,
    so use the search/issues API with ``is:pr`` and validate the returned
    shape before normalizing.
    """
    prs = []
    seen = set()
    for label in labels:
        items = _bounded_rest_items(
            f"/search/issues?q=repo:{repo}+is:pr+is:open+label:{quote(label)}"
            "&sort=updated&order=desc&per_page=100",
            query_name=f"open_prs_label:{label}",
            scope=f"open PRs in {repo} with label {label!r}; newest 300 maximum",
            max_pages=MAX_LABEL_SEARCH_PAGES,
            item_key="items",
            allow_partial=True,
        )
        for item in items:
            number = item.get("number")
            if not number or number in seen:
                continue
            html_url = item.get("html_url", "") or ""
            is_pr = "/pull/" in html_url or item.get("pull_request") is not None
            if not is_pr:
                continue
            seen.add(number)
            prs.append(normalize_pr(item))
    return sorted(prs, key=lambda p: p["updated_at"], reverse=True)


def _project_items_path() -> Path:
    return DATA / "vllm" / "ci" / "project_items.json"


def load_project_issue_numbers(repo: str, open_only: bool = False):
    """Return issue numbers present in the project snapshot for ``repo``."""
    path = _project_items_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    out = set()
    items = payload.get("items_by_number") or {}
    for number, meta in items.items():
        if meta.get("repo") and meta.get("repo") != repo:
            continue
        issue_state = (meta.get("issue_state") or "").upper()
        if open_only and issue_state and issue_state != "OPEN":
            continue
        try:
            out.add(int(meta.get("issue_number") or number))
        except (TypeError, ValueError):
            continue
    return sorted(out)


PROJECT_ITEMS_OPEN_ISSUES_Q = """
query($org: String!, $number: Int!, $itemQuery: String!) {
  organization(login: $org) {
    projectV2(number: $number) {
      url
      items(first: 100, query: $itemQuery) {
        pageInfo { hasNextPage endCursor }
        nodes {
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          content {
            __typename
            ... on Issue {
              number
              title
              state
              url
              body
              createdAt
              updatedAt
              author { login }
              repository { nameWithOwner }
              labels(first: 50) { nodes { name } }
              assignees(first: 20) { nodes { login } }
            }
          }
        }
      }
    }
  }
}
"""


def _normalize_graphql_issue(issue, project_status=""):
    """Normalize a GraphQL ProjectV2 Issue node."""
    body = issue.get("body") or ""
    repo = (issue.get("repository") or {}).get("nameWithOwner", "")
    out = {
        "number": issue["number"],
        "title": issue.get("title", ""),
        "author": (issue.get("author") or {}).get("login", ""),
        "state": (issue.get("state") or "").lower(),
        "created_at": issue.get("createdAt", ""),
        "updated_at": issue.get("updatedAt", ""),
        "html_url": issue.get("url", ""),
        "labels": [
            l.get("name")
            for l in ((issue.get("labels") or {}).get("nodes") or [])
            if l.get("name")
        ],
        "assignees": [
            a.get("login")
            for a in ((issue.get("assignees") or {}).get("nodes") or [])
            if a.get("login")
        ],
        "project_status": project_status or "",
        "project_url": PROJECT_URL,
        "repo": repo,
    }
    if body:
        out["body_head"] = body[:8000]
        # Link discovery must inspect the complete Project issue body even
        # though the published preview remains deliberately bounded.  The
        # private key is removed by ``enrich_project_issues_with_linked_prs``
        # before any dashboard payload is written.
        out["_link_source_body"] = body
    return out


def fetch_project_open_issues(repo):
    """Fetch one server-filtered, bounded Project #39 issue working set.

    ProjectV2's ``query`` argument keeps this read independent of the
    project's lifetime item count.  More than 100 matching open issues is
    published as a truthful 100-item lower bound.  Transport and response-shape
    failures still abort before any Home file is changed.
    """
    global _PROJECT_QUERY_USABLE
    issues = []
    item_query = f"is:issue is:open repo:{repo}"
    try:
        data = gh_graphql(
            PROJECT_ITEMS_OPEN_ISSUES_Q,
            {
                "org": PROJECT_ORG,
                "number": PROJECT_NUMBER,
                "itemQuery": item_query,
            },
            fail_closed=True,
        )
    except GitHubAPIError:
        _record_source_query(
            name="project_open_issues",
            scope=item_query,
            complete=False,
            truncated=False,
            error=True,
            authoritative=True,
            pages_fetched=0,
            max_pages=1,
            page_size=REST_PAGE_SIZE,
            items_observed=0,
            completion_reason="api_error",
        )
        raise

    project = (
        ((data.get("data") or {}).get("organization") or {}).get("projectV2")
        if isinstance(data, dict)
        else None
    )
    # ``gh api graphql`` returns the GraphQL payload directly (data/errors).
    if project is None:
        project = ((data.get("organization") or {}).get("projectV2") or {})
    page = (project or {}).get("items") or {}
    nodes = page.get("nodes")
    info = page.get("pageInfo")
    if (
        not project
        or not isinstance(nodes, list)
        or not isinstance(info, dict)
        or type(info.get("hasNextPage")) is not bool
    ):
        _record_source_query(
            name="project_open_issues",
            scope=item_query,
            complete=False,
            truncated=False,
            error=True,
            authoritative=True,
            pages_fetched=1,
            max_pages=1,
            page_size=REST_PAGE_SIZE,
            items_observed=0,
            completion_reason="invalid_shape",
        )
        raise GitHubAPIError("Project #39 open-issue query returned an invalid shape")

    for item in nodes:
        content = item.get("content") or {}
        if content.get("__typename") != "Issue":
            continue
        if (content.get("repository") or {}).get("nameWithOwner") != repo:
            continue
        if (content.get("state") or "").upper() != "OPEN":
            continue
        status = (item.get("fieldValueByName") or {}).get("name") or ""
        issues.append(_normalize_graphql_issue(content, status))

    truncated = info.get("hasNextPage") is True
    _record_source_query(
        name="project_open_issues",
        scope=item_query,
        complete=not truncated,
        truncated=truncated,
        error=False,
        authoritative=True,
        pages_fetched=1,
        max_pages=1,
        page_size=REST_PAGE_SIZE,
        items_observed=len(nodes),
        completion_reason="page_cap" if truncated else "server_filtered_complete",
    )
    if truncated:
        print(
            "  WARNING: Project #39 open-issue query exceeded its 100-item cap; "
            "publishing a bounded lower-bound working set",
            file=sys.stderr,
        )

    seen = set()
    unique = []
    for issue in issues:
        if issue["number"] in seen:
            continue
        seen.add(issue["number"])
        unique.append(issue)
    _PROJECT_QUERY_USABLE = True
    return sorted(unique, key=lambda i: i["updated_at"], reverse=True)[
        :MAX_PROJECT_OPEN_ISSUES
    ]


def write_project_items_snapshot(issues, source_coverage=None):
    """Persist the read-only Project #39 fallback used by the Home collector."""
    items = {}
    for issue in issues:
        number = issue.get("number")
        if not isinstance(number, int):
            continue
        items[str(number)] = {
            "issue_number": number,
            "issue_state": str(issue.get("state") or "").upper(),
            "repo": issue.get("repo") or "",
            "status": issue.get("project_status") or "",
            "title": issue.get("title") or "",
            "url": issue.get("html_url") or "",
        }
    path = _project_items_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    coverage = source_coverage or _source_coverage_snapshot()
    path.write_text(
        json.dumps(
            {
                "generated_at": now_iso(),
                "items_by_number": items,
                "project": f"{PROJECT_ORG}/projects/{PROJECT_NUMBER}",
                "project_url": PROJECT_URL,
                "count_semantics": coverage["population_semantics"],
                "source_coverage": coverage,
            },
            indent=2,
        )
        + "\n"
    )


def fetch_project_open_issues_from_snapshot(repo):
    """Fallback to the committed project snapshot when live GraphQL is unavailable."""
    path = _project_items_path()
    try:
        payload = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    items = payload.get("items_by_number") or {}
    issues = []
    issue_numbers = load_project_issue_numbers(repo, open_only=True)
    snapshot_coverage = payload.get("source_coverage")
    snapshot_complete = (
        isinstance(snapshot_coverage, dict)
        and snapshot_coverage.get(
            "authoritative_complete", snapshot_coverage.get("complete")
        )
        is True
    )
    item_cap_reached = len(issue_numbers) > MAX_PROJECT_OPEN_ISSUES
    truncated = item_cap_reached or not snapshot_complete
    _record_source_query(
        name="project_snapshot_fallback",
        scope=f"at most {MAX_PROJECT_OPEN_ISSUES} open issues from prior snapshot",
        complete=not truncated,
        truncated=truncated,
        error=False,
        authoritative=True,
        pages_fetched=0,
        max_pages=0,
        page_size=0,
        items_observed=min(len(issue_numbers), MAX_PROJECT_OPEN_ISSUES),
        completion_reason=(
            "snapshot_cap"
            if item_cap_reached
            else "upstream_lower_bound"
            if not snapshot_complete
            else "snapshot_complete"
        ),
    )
    for number in issue_numbers[:MAX_PROJECT_OPEN_ISSUES]:
        issue = fetch_issue_by_number(repo, number, fail_closed=True)
        if issue:
            meta = items.get(str(number)) or {}
            issue["project_status"] = meta.get("status") or ""
            issue["project_url"] = payload.get("project_url") or PROJECT_URL
            issue["repo"] = meta.get("repo") or repo
            issues.append(issue)
    return sorted(issues, key=lambda i: i["updated_at"], reverse=True)


def fetch_pr_by_number(repo, number):
    """Fetch one PR directly by number and normalize it."""
    pr = gh_api(f"/repos/{repo}/pulls/{number}")
    if not isinstance(pr, dict) or not pr.get("number"):
        return None
    html_url = pr.get("html_url", "") or ""
    is_pr = "/pull/" in html_url or pr.get("pull_request") is not None
    if not is_pr:
        return None
    return normalize_pr(pr)


def fetch_issue_by_number(repo, number, *, fail_closed=False):
    """Fetch one issue directly by number and normalize it."""
    issue = gh_api(
        f"/repos/{repo}/issues/{number}", fail_closed=fail_closed
    )
    if not isinstance(issue, dict) or not issue.get("number"):
        return None
    html_url = issue.get("html_url", "") or ""
    is_issue = "/issues/" in html_url and issue.get("pull_request") is None
    if not is_issue:
        return None
    return normalize_issue(issue)


def _is_copybara(author):
    """Check if a PR author is Google's Copybara sync bot."""
    if not author:
        return False
    return "copybara" in author.lower()


def resolve_copybara_authors(repo, prs):
    """For Copybara-authored merged PRs, resolve the original author.

    Google's Copybara bot syncs internal PRs to GitHub with titles like
    "PR #NNNN: [ROCm] actual title".  We extract the original PR number,
    look it up in our data or via API, and set ``original_author``.
    """
    import re

    # Build map of PR# -> author from non-bot PRs we already have
    pr_map = {p["number"]: p["author"] for p in prs if not _is_copybara(p["author"])}
    resolved = 0
    direct_lookups = 0
    unresolved_lookups = 0
    lookup_cap_reached = False

    for pr in prs:
        if not _is_copybara(pr["author"]) or not pr.get("merged"):
            continue
        m = re.match(r"PR #(\d+):", pr["title"])
        if not m:
            continue
        orig_num = int(m.group(1))
        if orig_num in pr_map:
            pr["original_author"] = pr_map[orig_num]
            resolved += 1
        else:
            if direct_lookups >= MAX_COPYBARA_AUTHOR_LOOKUPS:
                lookup_cap_reached = True
                continue
            direct_lookups += 1
            # Single API call to look up the original PR
            orig_pr = gh_api(f"/repos/{repo}/pulls/{orig_num}")
            if isinstance(orig_pr, dict) and orig_pr.get("user"):
                orig_author = orig_pr["user"].get("login", "")
                if orig_author:
                    pr["original_author"] = orig_author
                    pr_map[orig_num] = orig_author
                    resolved += 1
                else:
                    unresolved_lookups += 1
            else:
                unresolved_lookups += 1

    if resolved:
        print(f"  Resolved {resolved} Copybara PRs to original authors")
    _record_source_query(
        name="copybara_original_authors",
        scope=f"at most {MAX_COPYBARA_AUTHOR_LOOKUPS} optional direct PR lookups",
        complete=not lookup_cap_reached and unresolved_lookups == 0,
        truncated=lookup_cap_reached,
        error=unresolved_lookups > 0,
        authoritative=False,
        pages_fetched=0,
        max_pages=0,
        page_size=0,
        items_observed=direct_lookups,
        unresolved_items=unresolved_lookups,
        completion_reason=(
            "direct_lookup_cap"
            if lookup_cap_reached
            else "unresolved_optional_lookups"
            if unresolved_lookups
            else "all_optional_lookups_resolved"
        ),
    )


def fetch_issues(repo, labels, keywords, keyword_scope=""):
    """Fetch open issues matching filters."""
    issues = []

    for label in labels:
        items = gh_api(
            f"/repos/{repo}/issues?state=open&labels={quote(label)}&sort=updated&direction=desc&per_page=30",
            fail_closed=True,
        )
        # Filter out pull requests (GitHub API returns PRs as issues too)
        for item in items:
            if "pull_request" not in item:
                issues.append(item)

    scope = f"+in:{keyword_scope}" if keyword_scope else ""
    for kw in keywords:
        search_results = gh_api(
            f"/search/issues?q={kw}{scope}+repo:{repo}+is:issue+is:open&sort=updated&per_page=30",
            fail_closed=True,
        )
        if isinstance(search_results, list):
            search_results = {}
        for item in search_results.get("items", []):
            if not any(i["number"] == item["number"] for i in issues):
                issues.append(item)

    seen = set()
    unique = []
    for issue in issues:
        num = issue["number"]
        if num not in seen:
            seen.add(num)
            unique.append(normalize_issue(issue))
    return sorted(unique, key=lambda i: i["updated_at"], reverse=True)


def normalize_issue(issue):
    """Extract relevant issue fields."""
    body = issue.get("body") or ""
    out = {
        "number": issue["number"],
        "title": issue.get("title", ""),
        "author": issue.get("user", {}).get("login", ""),
        "state": issue.get("state", ""),
        "created_at": issue.get("created_at", ""),
        "updated_at": issue.get("updated_at", ""),
        "html_url": issue.get("html_url", ""),
        "labels": [l["name"] for l in issue.get("labels", [])],
        "assignees": [
            a.get("login")
            for a in (issue.get("assignees") or [])
            if a.get("login")
        ],
    }
    if body:
        out["body_head"] = body[:8000]
    if issue.get("project_status"):
        out["project_status"] = issue.get("project_status")
    if issue.get("project_url"):
        out["project_url"] = issue.get("project_url")
    if issue.get("repo"):
        out["repo"] = issue.get("repo")
    if issue.get("linked_prs"):
        out["linked_prs"] = issue.get("linked_prs")
    return out


def fetch_issue_comments(repo, number):
    """Fetch at most 200 comment bodies for one project issue."""
    comments = _bounded_rest_items(
        f"/repos/{repo}/issues/{number}/comments?per_page=100",
        query_name=f"project_issue_comments:{number}",
        scope=f"first {REST_PAGE_SIZE * MAX_ISSUE_COMMENT_PAGES} comments",
        max_pages=MAX_ISSUE_COMMENT_PAGES,
        # These comments contribute PRs to the Home population.  A bounded
        # result remains usable, but its PR count is necessarily a lower bound.
        authoritative=True,
        allow_partial=True,
        allow_errors=True,
    )
    out = []
    for comment in comments:
        if isinstance(comment, dict):
            out.append(comment.get("body") or "")
    return out


def extract_pr_refs(text, default_repo):
    """Extract GitHub PR references from issue text/comment text."""
    body = text or ""
    refs = []
    seen = set()
    buildkite_build_numbers = {
        int(match.group(1)) for match in _BUILDKITE_BUILD_URL_RE.finditer(body)
    }
    for match in _PULL_URL_RE.finditer(body):
        repo = match.group(1)
        number = int(match.group(2))
        key = (repo.lower(), number)
        if key in seen:
            continue
        seen.add(key)
        refs.append({
            "repo": repo,
            "number": number,
            "url": f"https://github.com/{repo}/pull/{number}",
        })
    for match in _PR_CONTEXT_REF_RE.finditer(body):
        number = int(match.group(1))
        # CI issue prose sometimes calls a Buildkite run "PR #<build>".  An
        # explicit GitHub pull URL above remains authoritative, but a bare
        # heuristic reference must not turn a demonstrated Buildkite build ID
        # into a fabricated same-repository pull request.
        if number in buildkite_build_numbers:
            continue
        key = (default_repo.lower(), number)
        if key in seen:
            continue
        seen.add(key)
        refs.append({
            "repo": default_repo,
            "number": number,
            "url": f"https://github.com/{default_repo}/pull/{number}",
        })
    return refs


def resolve_project_issue_pr_refs(repo, issues, prs):
    """Retain same-repo issue links only when GitHub confirms the PR exists.

    Textual ``PR #N`` references are necessarily heuristic.  A missing or
    non-PR target should make that optional Home-page relationship disappear
    for this collection, not leave ``issues.json`` inconsistent with
    ``prs.json`` and freeze publication of unrelated CI-health data.
    """
    pr_by_number = {
        pr.get("number"): pr
        for pr in prs
        if isinstance(pr, dict) and isinstance(pr.get("number"), int)
    }
    repo_norm = repo.lower()
    direct_lookups = 0
    unresolved_lookups = 0
    lookup_cap_reached = False

    for issue in issues:
        valid_refs = []
        for ref in issue.get("linked_prs") or []:
            if not isinstance(ref, dict):
                continue
            ref_repo = (ref.get("repo") or repo).lower()
            if ref_repo != repo_norm:
                valid_refs.append(ref)
                continue
            number = ref.get("number")
            if not isinstance(number, int):
                continue
            if number not in pr_by_number:
                if direct_lookups >= MAX_DIRECT_LINKED_PR_LOOKUPS:
                    lookup_cap_reached = True
                    continue
                direct_lookups += 1
                pr = fetch_pr_by_number(repo, number)
                if not pr:
                    unresolved_lookups += 1
                    print(
                        f"  WARNING: Ignoring unresolved PR reference #{number} "
                        f"from project issue #{issue.get('number')}"
                    )
                    continue
                prs.append(pr)
                pr_by_number[number] = pr
            valid_refs.append(ref)
        issue["linked_prs"] = valid_refs

    _record_source_query(
        name="project_linked_pr_validation",
        scope=(
            f"at most {MAX_DIRECT_LINKED_PR_LOOKUPS} direct PR validations "
            "for retained Project #39 issues"
        ),
        complete=not lookup_cap_reached and unresolved_lookups == 0,
        truncated=lookup_cap_reached,
        error=unresolved_lookups > 0,
        # Failed/capped validation can omit an otherwise valid linked PR from
        # ``prs.json``; expose that population as a lower bound.
        authoritative=True,
        pages_fetched=0,
        max_pages=0,
        page_size=0,
        items_observed=direct_lookups,
        unresolved_items=unresolved_lookups,
        completion_reason=(
            "direct_lookup_cap"
            if lookup_cap_reached
            else "unresolved_optional_lookups"
            if unresolved_lookups
            else "all_refs_validated"
        ),
    )


def enrich_project_issues_with_linked_prs(repo, issues):
    """Attach PR references discovered in each project issue body/comments."""
    enriched = []
    for issue in issues:
        issue = dict(issue)
        refs = []
        seen = set()
        refs_truncated = False
        chunks = [issue.pop("_link_source_body", issue.get("body_head") or "")]
        chunks.extend(fetch_issue_comments(repo, issue["number"]))
        for chunk in chunks:
            for ref in extract_pr_refs(chunk, repo):
                key = (ref["repo"].lower(), ref["number"])
                if key in seen:
                    continue
                seen.add(key)
                if len(refs) >= MAX_LINKED_PRS_PER_ISSUE:
                    refs_truncated = True
                    continue
                refs.append(ref)
        _record_source_query(
            name=f"project_issue_link_refs:{issue['number']}",
            scope=f"first {MAX_LINKED_PRS_PER_ISSUE} unique PR references",
            complete=not refs_truncated,
            truncated=refs_truncated,
            error=False,
            # Dropped references can drop PRs from the Home population.
            authoritative=True,
            pages_fetched=0,
            max_pages=0,
            page_size=0,
            items_observed=len(refs),
            completion_reason="ref_cap" if refs_truncated else "all_refs_retained",
        )
        issue["linked_prs"] = refs
        enriched.append(issue)
    return enriched


def apply_pr_tags(prs, project_issues, repo):
    """Annotate PRs with dashboard-level CI/ROCm tag metadata."""
    issue_nums_by_pr = {}
    for issue in project_issues:
        for ref in issue.get("linked_prs") or []:
            if (ref.get("repo") or repo).lower() != repo.lower():
                continue
            number = ref.get("number")
            if not isinstance(number, int):
                continue
            issue_nums_by_pr.setdefault(number, set()).add(issue["number"])

    for pr in prs:
        labels = pr.get("labels") or []
        lower_labels = {str(label).lower() for label in labels}
        ci_issue_numbers = sorted(issue_nums_by_pr.get(pr["number"], set()))
        is_ci = bool(ci_issue_numbers)
        is_rocm = "rocm" in lower_labels
        other_tags = [
            label
            for label in labels
            if str(label).lower() not in {"rocm"}
        ]
        pr["is_ci_pr"] = is_ci
        pr["is_rocm_pr"] = is_rocm
        pr["ci_issue_numbers"] = ci_issue_numbers
        pr["custom_tags"] = (["CI"] if is_ci else []) + (["ROCm"] if is_rocm else [])
        pr["other_tags"] = other_tags
    return prs


def fetch_releases(repo):
    """Fetch latest releases."""
    releases = gh_api(f"/repos/{repo}/releases?per_page=5", fail_closed=True)
    if not releases:
        # Fallback to tags
        tags = gh_api(f"/repos/{repo}/tags?per_page=3", fail_closed=True)
        return [
            {"tag_name": t["name"], "published_at": "", "html_url": ""}
            for t in tags[:3]
        ]
    return [
        {
            "tag_name": r["tag_name"],
            "name": r.get("name", ""),
            "published_at": r.get("published_at", ""),
            "html_url": r.get("html_url", ""),
            "prerelease": r.get("prerelease", False),
        }
        for r in releases[:5]
    ]


def fetch_fork_prs(fork_repo, upstream_repo, authors):
    """Fetch PRs from fork to upstream (our PRs to upstream)."""
    prs = []
    if authors:
        tracked_authors = set(authors)
        items = gh_api(
            f"/repos/{upstream_repo}/pulls?state=all&sort=updated&direction=desc&per_page=30",
            fail_closed=True,
        )
        for pr in items:
            if pr.get("user", {}).get("login") in tracked_authors:
                prs.append(normalize_pr(pr))

    seen = set()
    unique = []
    for pr in prs:
        if pr["number"] not in seen:
            seen.add(pr["number"])
            unique.append(pr)
    return sorted(unique, key=lambda p: p["updated_at"], reverse=True)


def fetch_all_open_prs(repo):
    """Fetch a bounded open-PR working set for an active-dev project."""
    items = _bounded_rest_items(
        f"/repos/{repo}/pulls?state=open&sort=updated&direction=desc&per_page=100",
        query_name="active_dev_open_prs",
        scope=f"newest {REST_PAGE_SIZE * MAX_OPEN_ITEM_PAGES} open PRs in {repo}",
        max_pages=MAX_OPEN_ITEM_PAGES,
        allow_partial=True,
    )
    return [normalize_pr(pr) for pr in items]


def fetch_recently_merged_prs(repo):
    """Fetch recently merged PRs (most recent 100 closed, filtered to merged)."""
    items = gh_api(
        f"/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100",
        fail_closed=True,
    )
    if not isinstance(items, list):
        items = []
    return [normalize_pr(pr) for pr in items if pr.get("merged_at")]


def fetch_all_open_issues(repo):
    """Fetch a bounded open-issue working set for an active-dev project."""
    items = _bounded_rest_items(
        f"/repos/{repo}/issues?state=open&sort=updated&direction=desc&per_page=100",
        query_name="active_dev_open_issues",
        scope=f"newest {REST_PAGE_SIZE * MAX_OPEN_ITEM_PAGES} open issues in {repo}",
        max_pages=MAX_OPEN_ITEM_PAGES,
        allow_partial=True,
    )
    # Filter out pull requests (GitHub API returns PRs as issues too)
    return [normalize_issue(i) for i in items if "pull_request" not in i]


def collect_project(name, cfg):
    """Collect all data for a single project."""
    _reset_source_coverage()
    print(f"Collecting {name} ({cfg['repo']})...")
    project_dir = DATA / name
    project_dir.mkdir(parents=True, exist_ok=True)

    repo = cfg["repo"]
    role = cfg.get("role", "upstream_watch")
    authors = cfg.get("track_authors", [])
    labels = cfg.get("track_labels", [])
    keywords = cfg.get("track_keywords", [])
    keyword_scope = cfg.get("keyword_scope", "")

    email_domains = cfg.get("track_email_domains", [])
    if email_domains:
        print(f"  Discovering authors by email domain: {email_domains}")
        domain_authors = discover_email_domain_authors(repo, email_domains)
        print(f"  Found {len(domain_authors)} authors: {domain_authors}")
        authors = list(set(authors + domain_authors))

    project_issues = []
    if repo == "vllm-project/vllm":
        project_issues = fetch_project_open_issues(repo)
        if not _PROJECT_QUERY_USABLE:
            raise GitHubAPIError(
                "Project #39 query did not produce a complete usable result"
            )
        if project_issues:
            project_issues = enrich_project_issues_with_linked_prs(repo, project_issues)

    # Collect PRs
    if repo == "vllm-project/vllm":
        # Home is scoped to currently open ROCm work plus PRs linked from
        # project #39 issue threads. The CI tag below is custom dashboard
        # metadata; it does not require a GitHub label on the PR.
        prs = fetch_open_label_prs(repo, ["rocm"])
    elif role == "active_dev":
        # For our own projects, fetch ALL open PRs + recently merged
        prs = fetch_all_open_prs(repo)
        merged_prs = fetch_recently_merged_prs(repo)
        existing_nums = {p["number"] for p in prs}
        for mp in merged_prs:
            if mp["number"] not in existing_nums:
                prs.append(mp)
    else:
        # For upstream projects, fetch filtered PRs + recently merged by our authors
        prs = fetch_prs(repo, authors, labels, keywords, keyword_scope)
        if authors:
            merged_prs = fetch_recently_merged_prs(repo)
            existing_nums = {p["number"] for p in prs}
            for mp in merged_prs:
                if mp["number"] not in existing_nums and mp["author"] in authors:
                    prs.append(mp)

    # If there's a fork, also collect our PRs to upstream
    fork = cfg.get("fork")
    if fork and authors:
        fork_prs = fetch_fork_prs(fork, repo, authors)
        existing_nums = {p["number"] for p in prs}
        for fp in fork_prs:
            if fp["number"] not in existing_nums:
                prs.append(fp)

    # Resolve heuristic references from project #39 issue prose.  Only
    # GitHub-confirmed same-repo PRs survive into issues.json, and every
    # retained PR is present in prs.json for the cross-surface audit.
    if project_issues:
        resolve_project_issue_pr_refs(repo, project_issues, prs)

    apply_pr_tags(prs, project_issues, repo)
    prs = sorted(prs, key=lambda p: p["updated_at"], reverse=True)

    # Resolve Copybara-authored PRs to original authors
    if any(_is_copybara(p.get("author", "")) for p in prs):
        resolve_copybara_authors(repo, prs)

    # Home's issue scope is Project #39 even when the complete live result is
    # empty.  Never resurrect a stale snapshot or broaden into keyword search
    # merely because there are currently zero matching project issues.
    if repo == "vllm-project/vllm":
        issues = project_issues
    elif role == "active_dev":
        # For our own projects, fetch ALL open issues
        issues = fetch_all_open_issues(repo)
    else:
        # For upstream projects, only fetch issues matching filters
        issues = fetch_issues(repo, labels, keywords, keyword_scope)
    project_issue_numbers = (
        []
        if repo == "vllm-project/vllm"
        else load_project_issue_numbers(repo)
    )
    project_snapshot_truncated = len(project_issue_numbers) > MAX_PROJECT_OPEN_ISSUES
    if project_snapshot_truncated:
        _record_source_query(
            name="project_snapshot_issue_numbers",
            scope=f"first {MAX_PROJECT_OPEN_ISSUES} project snapshot issues",
            complete=False,
            truncated=True,
            error=False,
            authoritative=True,
            pages_fetched=0,
            max_pages=0,
            page_size=0,
            items_observed=MAX_PROJECT_OPEN_ISSUES,
            completion_reason="snapshot_cap",
        )
        project_issue_numbers = project_issue_numbers[:MAX_PROJECT_OPEN_ISSUES]
    if project_issue_numbers:
        existing_issue_nums = {i["number"] for i in issues}
        for number in project_issue_numbers:
            if number in existing_issue_nums:
                continue
            issue = fetch_issue_by_number(repo, number, fail_closed=True)
            if issue:
                issues.append(issue)
                existing_issue_nums.add(number)
    # Collect releases
    releases = fetch_releases(repo)

    # Do not mutate any Home output until every authoritative GitHub read and
    # normalization step has succeeded. A transport error or authoritative cap
    # therefore leaves the prior complete surface byte-for-byte intact for the
    # publication selector to retain.
    collected_at = now_iso()
    source_coverage = _source_coverage_snapshot()
    if repo == "vllm-project/vllm":
        write_project_items_snapshot(project_issues, source_coverage)
    with open(project_dir / "prs.json", "w") as f:
        json.dump(
            {
                "collected_at": collected_at,
                "prs": prs,
                "count_semantics": source_coverage["population_semantics"],
                "source_coverage": source_coverage,
            },
            f,
            indent=2,
        )
    with open(project_dir / "issues.json", "w") as f:
        json.dump(
            {
                "collected_at": collected_at,
                "issues": sorted(
                    issues, key=lambda i: i["updated_at"], reverse=True
                ),
                "count_semantics": source_coverage["population_semantics"],
                "source_coverage": source_coverage,
            },
            f,
            indent=2,
        )
    with open(project_dir / "releases.json", "w") as f:
        json.dump({"collected_at": collected_at, "releases": releases}, f, indent=2)

    print(f"  {len(prs)} PRs, {len(issues)} issues, {len(releases)} releases")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    with open(CONFIG) as f:
        config = yaml.safe_load(f)

    for name, cfg in config["projects"].items():
        if name != "vllm":
            print(f"Skipping {name} (test-parity only)")
            continue
        collect_project(name, cfg)

    print("Collection complete.")


if __name__ == "__main__":
    main()
