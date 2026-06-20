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

_PULL_URL_RE = re.compile(
    r"https?://github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)",
    re.IGNORECASE,
)
_PR_CONTEXT_REF_RE = re.compile(
    r"(?i)\b(?:pr|pull request|pull)\b[^\n#]{0,160}#(\d+)"
)


def gh_api(endpoint, method="GET", paginate=False):
    """Call GitHub API via gh CLI."""
    cmd = ["gh", "api", endpoint, "--method", method]
    if paginate:
        cmd.append("--paginate")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout) if result.stdout.strip() else []
    except subprocess.CalledProcessError as e:
        print(
            f"  WARNING: gh api {endpoint} failed: {e.stderr.strip()}", file=sys.stderr
        )
        return []
    except json.JSONDecodeError:
        # --paginate can return concatenated JSON arrays or objects.
        raw = result.stdout.strip()
        if raw.startswith("[") and "][" in raw:
            raw = raw.replace("][", ",")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        try:
            decoder = json.JSONDecoder()
            idx = 0
            values = []
            while idx < len(raw):
                value, end = decoder.raw_decode(raw, idx)
                values.append(value)
                idx = end
                while idx < len(raw) and raw[idx].isspace():
                    idx += 1
            if len(values) == 1:
                return values[0]
            return values
        except json.JSONDecodeError:
            print(
                f"  WARNING: could not parse response for {endpoint}", file=sys.stderr
            )
            return []


def gh_graphql(query, variables=None):
    """Call GitHub GraphQL via gh CLI."""
    variables = variables or {}
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            continue
        cmd.extend(["-F", f"{key}={value}"])
    try:
        env = os.environ.copy()
        if os.getenv("PROJECTS_TOKEN"):
            env["GH_TOKEN"] = os.getenv("PROJECTS_TOKEN")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except subprocess.CalledProcessError as e:
        print(f"  WARNING: gh graphql failed: {e.stderr.strip()}", file=sys.stderr)
        return {}
    except json.JSONDecodeError:
        print("  WARNING: could not parse GraphQL response", file=sys.stderr)
        return {}


def discover_email_domain_authors(repo, email_domains, max_pages=3):
    """Discover GitHub usernames whose commit emails match given domains."""
    authors = set()
    for page in range(1, max_pages + 1):
        commits = gh_api(
            f"/repos/{repo}/commits?per_page=100&page={page}"
        )
        if not isinstance(commits, list):
            break
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
    for author in authors:
        items = gh_api(
            f"/repos/{repo}/pulls?state=all&sort=updated&direction=desc&per_page=30"
        )
        for pr in items:
            if pr.get("user", {}).get("login") == author:
                prs.append(pr)

    # Fetch PRs by label (open + closed/merged)
    for label in labels:
        for state in ["open", "closed"]:
            items = gh_api(
                f"/repos/{repo}/pulls?state={state}&sort=updated&direction=desc&per_page=30"
            )
            for pr in items:
                pr_labels = [l["name"].lower() for l in pr.get("labels", [])]
                if label.lower() in pr_labels and not any(
                    p["number"] == pr["number"] for p in prs
                ):
                    prs.append(pr)

    # Search PRs by keyword (open + merged)
    scope = f"+in:{keyword_scope}" if keyword_scope else ""
    for kw in keywords:
        for pr_filter in ["is:open", "is:merged"]:
            search_results = gh_api(
                f"/search/issues?q={kw}{scope}+repo:{repo}+is:pr+{pr_filter}&sort=updated&per_page=30"
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
        search_results = gh_api(
            f"/search/issues?q=repo:{repo}+is:pr+is:open+label:{quote(label)}"
            "&sort=updated&order=desc&per_page=100",
            paginate=True,
        )
        if isinstance(search_results, list):
            items = []
            for page in search_results:
                if isinstance(page, dict):
                    items.extend(page.get("items", []))
                elif isinstance(page, list):
                    items.extend(page)
        else:
            items = search_results.get("items", []) if isinstance(search_results, dict) else []
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


def _ready_tickets_path() -> Path:
    return DATA / "vllm" / "ci" / "ready_tickets.json"


def _project_items_path() -> Path:
    return DATA / "vllm" / "ci" / "project_items.json"


def load_linked_ready_ticket_pr_numbers(repo: str):
    """Return linked PR numbers referenced by tracked CI issues for ``repo``.

    ``ready_tickets.json`` is the source of truth for manual/comment-linked PR
    references such as "PR for this here #40176". The home dashboard uses those
    links, so the PR collector must ensure the referenced PR objects are also
    present in ``prs.json``.
    """
    path = _ready_tickets_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if payload.get("issue_repo") != repo:
        return []
    out = set()
    for ticket in payload.get("tickets", []) or []:
        for ref in ticket.get("linked_prs", []) or []:
            try:
                out.add(int(ref.get("number")))
            except (TypeError, ValueError, AttributeError):
                continue
    return sorted(out)


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
query($org: String!, $number: Int!, $cursor: String) {
  organization(login: $org) {
    projectV2(number: $number) {
      url
      items(first: 100, after: $cursor) {
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
    return out


def fetch_project_open_issues(repo):
    """Fetch every open Issue currently on project #39 for ``repo``."""
    issues = []
    cursor = None
    while True:
        data = gh_graphql(
            PROJECT_ITEMS_OPEN_ISSUES_Q,
            {"org": PROJECT_ORG, "number": PROJECT_NUMBER, "cursor": cursor},
        )
        project = (
            ((data.get("data") or {}).get("organization") or {}).get("projectV2")
            if isinstance(data, dict)
            else None
        )
        # ``gh api graphql`` returns the GraphQL payload directly (data/errors).
        if project is None:
            project = ((data.get("organization") or {}).get("projectV2") or {})
        if not project:
            return []
        page = project.get("items") or {}
        for item in page.get("nodes") or []:
            content = item.get("content") or {}
            if content.get("__typename") != "Issue":
                continue
            if (content.get("repository") or {}).get("nameWithOwner") != repo:
                continue
            if (content.get("state") or "").upper() != "OPEN":
                continue
            status = (item.get("fieldValueByName") or {}).get("name") or ""
            issues.append(_normalize_graphql_issue(content, status))
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        cursor = info.get("endCursor")
    seen = set()
    unique = []
    for issue in issues:
        if issue["number"] in seen:
            continue
        seen.add(issue["number"])
        unique.append(issue)
    return sorted(unique, key=lambda i: i["updated_at"], reverse=True)


def fetch_project_open_issues_from_snapshot(repo):
    """Fallback to the committed project snapshot when live GraphQL is unavailable."""
    path = _project_items_path()
    try:
        payload = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    items = payload.get("items_by_number") or {}
    issues = []
    for number in load_project_issue_numbers(repo, open_only=True):
        issue = fetch_issue_by_number(repo, number)
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


def fetch_issue_by_number(repo, number):
    """Fetch one issue directly by number and normalize it."""
    issue = gh_api(f"/repos/{repo}/issues/{number}")
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
            # Single API call to look up the original PR
            orig_pr = gh_api(f"/repos/{repo}/pulls/{orig_num}")
            if isinstance(orig_pr, dict) and orig_pr.get("user"):
                orig_author = orig_pr["user"].get("login", "")
                if orig_author:
                    pr["original_author"] = orig_author
                    pr_map[orig_num] = orig_author
                    resolved += 1

    if resolved:
        print(f"  Resolved {resolved} Copybara PRs to original authors")


def fetch_issues(repo, labels, keywords, keyword_scope=""):
    """Fetch open issues matching filters."""
    issues = []

    for label in labels:
        items = gh_api(
            f"/repos/{repo}/issues?state=open&labels={quote(label)}&sort=updated&direction=desc&per_page=30"
        )
        # Filter out pull requests (GitHub API returns PRs as issues too)
        for item in items:
            if "pull_request" not in item:
                issues.append(item)

    scope = f"+in:{keyword_scope}" if keyword_scope else ""
    for kw in keywords:
        search_results = gh_api(
            f"/search/issues?q={kw}{scope}+repo:{repo}+is:issue+is:open&sort=updated&per_page=30"
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
    """Fetch every comment body for an issue."""
    comments = gh_api(
        f"/repos/{repo}/issues/{number}/comments?per_page=100",
        paginate=True,
    )
    if isinstance(comments, dict):
        comments = [comments]
    if not isinstance(comments, list):
        return []
    out = []
    for comment in comments:
        if isinstance(comment, dict):
            out.append(comment.get("body") or "")
    return out


def extract_pr_refs(text, default_repo):
    """Extract GitHub PR references from issue text/comment text."""
    refs = []
    seen = set()
    for match in _PULL_URL_RE.finditer(text or ""):
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
    for match in _PR_CONTEXT_REF_RE.finditer(text or ""):
        number = int(match.group(1))
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


def enrich_project_issues_with_linked_prs(repo, issues):
    """Attach PR references discovered in each project issue body/comments."""
    enriched = []
    for issue in issues:
        refs = []
        seen = set()
        chunks = [issue.get("body_head") or ""]
        chunks.extend(fetch_issue_comments(repo, issue["number"]))
        for chunk in chunks:
            for ref in extract_pr_refs(chunk, repo):
                key = (ref["repo"].lower(), ref["number"])
                if key in seen:
                    continue
                seen.add(key)
                refs.append(ref)
        issue = dict(issue)
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
    releases = gh_api(f"/repos/{repo}/releases?per_page=5")
    if not releases:
        # Fallback to tags
        tags = gh_api(f"/repos/{repo}/tags?per_page=3")
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
    for author in authors:
        items = gh_api(
            f"/repos/{upstream_repo}/pulls?state=all&sort=updated&direction=desc&per_page=30"
        )
        for pr in items:
            if pr.get("user", {}).get("login") == author:
                prs.append(normalize_pr(pr))

    seen = set()
    unique = []
    for pr in prs:
        if pr["number"] not in seen:
            seen.add(pr["number"])
            unique.append(pr)
    return sorted(unique, key=lambda p: p["updated_at"], reverse=True)


def fetch_all_open_prs(repo):
    """Fetch all open PRs for a repo (for active_dev projects)."""
    items = gh_api(
        f"/repos/{repo}/pulls?state=open&sort=updated&direction=desc&per_page=100",
        paginate=True,
    )
    if not isinstance(items, list):
        items = []
    return [normalize_pr(pr) for pr in items]


def fetch_recently_merged_prs(repo):
    """Fetch recently merged PRs (most recent 100 closed, filtered to merged)."""
    items = gh_api(
        f"/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100",
    )
    if not isinstance(items, list):
        items = []
    return [normalize_pr(pr) for pr in items if pr.get("merged_at")]


def fetch_all_open_issues(repo):
    """Fetch all open issues for a repo (for active_dev projects)."""
    items = gh_api(
        f"/repos/{repo}/issues?state=open&sort=updated&direction=desc&per_page=100",
        paginate=True,
    )
    if not isinstance(items, list):
        items = []
    # Filter out pull requests (GitHub API returns PRs as issues too)
    return [normalize_issue(i) for i in items if "pull_request" not in i]


def collect_project(name, cfg):
    """Collect all data for a single project."""
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
        if not project_issues:
            project_issues = fetch_project_open_issues_from_snapshot(repo)
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

    # Guarantee that any PR explicitly linked from a tracked CI-failure issue
    # is also present in prs.json, even when it slips past the coarse author /
    # label / keyword filters above.
    linked_pr_numbers = load_linked_ready_ticket_pr_numbers(repo)
    if linked_pr_numbers:
        existing_nums = {p["number"] for p in prs}
        for number in linked_pr_numbers:
            if number in existing_nums:
                continue
            pr = fetch_pr_by_number(repo, number)
            if pr:
                prs.append(pr)
                existing_nums.add(number)

    # Guarantee that PRs referenced from any open project #39 issue body or
    # comment are present and tagged as CI PRs.
    if project_issues:
        existing_nums = {p["number"] for p in prs}
        for issue in project_issues:
            for ref in issue.get("linked_prs") or []:
                if (ref.get("repo") or repo).lower() != repo.lower():
                    continue
                number = ref.get("number")
                if not isinstance(number, int) or number in existing_nums:
                    continue
                pr = fetch_pr_by_number(repo, number)
                if pr:
                    prs.append(pr)
                    existing_nums.add(number)

    apply_pr_tags(prs, project_issues, repo)
    prs = sorted(prs, key=lambda p: p["updated_at"], reverse=True)

    # Resolve Copybara-authored PRs to original authors
    if any(_is_copybara(p.get("author", "")) for p in prs):
        resolve_copybara_authors(repo, prs)

    with open(project_dir / "prs.json", "w") as f:
        json.dump({"collected_at": now_iso(), "prs": prs}, f, indent=2)

    # Collect issues
    if project_issues:
        issues = project_issues
    elif role == "active_dev":
        # For our own projects, fetch ALL open issues
        issues = fetch_all_open_issues(repo)
    else:
        # For upstream projects, only fetch issues matching filters
        issues = fetch_issues(repo, labels, keywords, keyword_scope)
    project_issue_numbers = [] if project_issues else load_project_issue_numbers(repo)
    if project_issue_numbers:
        existing_issue_nums = {i["number"] for i in issues}
        for number in project_issue_numbers:
            if number in existing_issue_nums:
                continue
            issue = fetch_issue_by_number(repo, number)
            if issue:
                issues.append(issue)
                existing_issue_nums.add(number)
    with open(project_dir / "issues.json", "w") as f:
        json.dump(
            {"collected_at": now_iso(), "issues": sorted(issues, key=lambda i: i["updated_at"], reverse=True)},
            f,
            indent=2,
        )

    # Collect releases
    releases = fetch_releases(repo)
    with open(project_dir / "releases.json", "w") as f:
        json.dump({"collected_at": now_iso(), "releases": releases}, f, indent=2)

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
