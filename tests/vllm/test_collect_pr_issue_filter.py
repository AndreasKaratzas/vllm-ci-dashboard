"""Regression tests for the PR/issue boundary in ``scripts/collect.py``.

The ``/search/issues`` endpoint returns both PRs and issues — even with
``is:pr`` in the query, a result occasionally slips through as a plain
issue (type mixing in the API shape, stale cache, rate-limit reshape).
If a plain issue leaks into ``fetch_prs()``, it pollutes ``prs.json``
with an issue that the dashboard then renders in the PR table — wrong
URL, no merge state, nonsensical draft flag.

These tests feed realistic GitHub payloads into the two collectors and
assert the boundary holds: PRs go to the PR sink, issues to the issue
sink, and mixed responses are filtered correctly. The payload shapes
below were captured from real ``gh api`` responses against
``vllm-project/vllm`` so they match the production schema (``html_url``
format, ``pull_request`` key presence/absence, nested ``labels``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/ is on sys.path via conftest, but add defensively here too so
# this file can be run in isolation.
_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS = str(_ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import collect  # noqa: E402  (sys.path mutation above)


def test_graphql_transport_rejects_mutations_before_subprocess(monkeypatch):
    def _unexpected_run(*args, **kwargs):
        raise AssertionError("rejected mutation reached gh")

    monkeypatch.setattr(collect.subprocess, "run", _unexpected_run)
    with pytest.raises(ValueError, match="does not permit GraphQL mutations"):
        collect.gh_graphql("mutation { deleteProjectV2Item(input: {}) { clientMutationId } }")


# ---------------------------------------------------------------------------
# Realistic payload fixtures.
# ---------------------------------------------------------------------------

# A real PR shape from ``/repos/:r/pulls``: has ``html_url`` pointing at
# ``/pull/<n>`` and a ``draft`` flag. The ``pull_request`` nested object
# is only present in ``/search/issues`` responses.
REAL_PR = {
    "number": 12345,
    "title": "[Kernel] Fuse MoE with attention",
    "state": "open",
    "user": {"login": "karatzas-amd"},
    "created_at": "2026-04-10T12:00:00Z",
    "updated_at": "2026-04-18T09:30:00Z",
    "html_url": "https://github.com/vllm-project/vllm/pull/12345",
    "labels": [{"name": "performance"}, {"name": "rocm"}],
    "draft": False,
    "merged_at": None,
}

# A real merged-PR shape — ``merged_at`` populated.
REAL_MERGED_PR = {
    "number": 12000,
    "title": "Fix AMD CI flake",
    "state": "closed",
    "user": {"login": "karatzas-amd"},
    "created_at": "2026-04-01T00:00:00Z",
    "updated_at": "2026-04-15T00:00:00Z",
    "html_url": "https://github.com/vllm-project/vllm/pull/12000",
    "labels": [{"name": "ci"}],
    "draft": False,
    "merged_at": "2026-04-15T00:00:00Z",
}

# A plain issue that leaked from /search/issues despite ``is:pr`` — this
# is the exact shape of the regression. ``html_url`` lives under
# ``/issues/<n>`` and there's no ``pull_request`` key, no ``draft`` flag.
LEAKED_ISSUE = {
    "number": 99999,
    "title": "Crash on startup when ROCm unavailable",
    "state": "open",
    "user": {"login": "someone-else"},
    "created_at": "2026-04-17T10:00:00Z",
    "updated_at": "2026-04-18T10:00:00Z",
    "html_url": "https://github.com/vllm-project/vllm/issues/99999",
    "labels": [{"name": "bug"}],
}

# A PR shape as returned by /search/issues (has ``pull_request`` nested
# object, unlike issues). This must be kept as a PR.
SEARCH_ISSUES_PR_SHAPE = {
    "number": 42,
    "title": "Search-API-shaped PR",
    "state": "open",
    "user": {"login": "karatzas-amd"},
    "created_at": "2026-04-10T12:00:00Z",
    "updated_at": "2026-04-18T09:30:00Z",
    # Notable: /search/issues returns html_url under /pull/ for PRs and
    # ALSO nests a pull_request object. Our filter accepts either cue.
    "html_url": "https://github.com/vllm-project/vllm/pull/42",
    "pull_request": {
        "url": "https://api.github.com/repos/vllm-project/vllm/pulls/42",
        "merged_at": None,
    },
    "labels": [],
    "draft": False,
    "merged_at": None,
}


class TestLinkedPrReferences:
    def test_buildkite_build_labeled_as_pr_is_not_a_github_reference(self):
        repo = "vllm-project/vllm"
        text = (
            "[PR #82340](https://buildkite.com/vllm/ci/builds/82340#job) failed\n"
            "PR #82340 (retry) also failed\n"
            "PR for this here #40176\n"
        )

        assert collect.extract_pr_refs(text, repo) == [{
            "repo": repo,
            "number": 40176,
            "url": "https://github.com/vllm-project/vllm/pull/40176",
        }]

    def test_unresolved_heuristic_reference_is_pruned_before_publication(
        self, monkeypatch, capsys
    ):
        repo = "vllm-project/vllm"
        confirmed_raw = dict(REAL_PR)
        confirmed_raw.update({
            "number": 49937,
            "html_url": f"https://github.com/{repo}/pull/49937",
        })
        confirmed_pr = collect.normalize_pr(confirmed_raw)
        issues = [{
            "number": 51115,
            "linked_prs": [
                {
                    "repo": repo,
                    "number": 49937,
                    "url": f"https://github.com/{repo}/pull/49937",
                },
                {
                    "repo": repo,
                    "number": 82340,
                    "url": f"https://github.com/{repo}/pull/82340",
                },
            ],
        }]
        prs = []
        monkeypatch.setattr(
            collect,
            "fetch_pr_by_number",
            lambda _repo, number: confirmed_pr if number == 49937 else None,
        )

        collect.resolve_project_issue_pr_refs(repo, issues, prs)

        assert [ref["number"] for ref in issues[0]["linked_prs"]] == [49937]
        assert [pr["number"] for pr in prs] == [49937]
        assert "Ignoring unresolved PR reference #82340" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Fake ``gh_api`` — returns specific payloads per endpoint prefix.
# ---------------------------------------------------------------------------

class _FakeGhApi:
    """Map endpoint substrings to canned responses.

    Unlike a one-size-fits-all mock, this asserts the collector sends the
    correct query shape (``is:pr`` for fetch_prs, ``is:issue`` for
    fetch_issues) — a sloppy mock that returned the same payload for
    both endpoints would mask the regression this test is pinning.
    """

    def __init__(self, routes):
        self.routes = routes
        self.calls = []  # list of endpoints hit, for post-call assertions

    def __call__(self, endpoint, *args, **kwargs):
        self.calls.append(endpoint)
        for key, payload in self.routes.items():
            if key in endpoint:
                return payload
        # Unrouted endpoints get an empty result — like gh_api on a 404.
        return [] if "/search/" not in endpoint else {"items": []}


@pytest.fixture
def patch_gh_api(monkeypatch):
    """Return a helper that installs a _FakeGhApi with the given routes."""
    def _install(routes):
        fake = _FakeGhApi(routes)
        monkeypatch.setattr(collect, "gh_api", fake)
        return fake
    return _install


# ---------------------------------------------------------------------------
# fetch_prs — the regression under test.
# ---------------------------------------------------------------------------

class TestFetchPrsDropsLeakedIssues:
    def test_leaked_issue_from_search_is_dropped(self, patch_gh_api):
        """The /search/issues response mixes a real PR and a leaked issue.
        Only the PR must survive into the output."""
        fake = patch_gh_api({
            # /repos/vllm-project/vllm/pulls → empty (no author match)
            "/repos/vllm-project/vllm/pulls": [],
            # /search/issues → PR + leaked issue in the same response
            "/search/issues": {
                "items": [REAL_PR, LEAKED_ISSUE],
            },
        })
        prs = collect.fetch_prs(
            "vllm-project/vllm",
            authors=[],
            labels=[],
            keywords=["moe"],
        )
        numbers = [p["number"] for p in prs]
        assert 12345 in numbers, "Real PR #12345 must pass the filter"
        assert 99999 not in numbers, (
            "Leaked issue #99999 (html_url under /issues/) must be dropped "
            "— this is the regression that put issues in the PR pane."
        )
        # And the PR must carry the PR-specific fields the UI renders.
        pr = next(p for p in prs if p["number"] == 12345)
        assert pr["html_url"].endswith("/pull/12345")
        assert pr["state"] == "open"
        assert pr["merged"] is False
        assert "performance" in pr["labels"]

    def test_search_issues_pr_shape_is_kept(self, patch_gh_api):
        """A PR returned via /search/issues has the nested ``pull_request``
        object. That cue alone must keep the item — html_url may be elided."""
        # Strip html_url to prove pull_request alone is sufficient.
        shape = dict(SEARCH_ISSUES_PR_SHAPE)
        shape["html_url"] = ""
        patch_gh_api({
            "/repos/": [],
            "/search/issues": {"items": [shape]},
        })
        prs = collect.fetch_prs(
            "vllm-project/vllm",
            authors=[],
            labels=[],
            keywords=["anything"],
        )
        assert [p["number"] for p in prs] == [42]

    def test_author_fetched_prs_only_keep_real_prs(self, patch_gh_api):
        """/repos/:r/pulls returns only PRs already, but a future bug could
        drift this. Confirm the filter is defence-in-depth: an item with
        html_url under /issues/ and no pull_request key is still rejected."""
        pretend_drift = dict(LEAKED_ISSUE)
        # Put it in the author sink so we'd keep it if not for the filter.
        pretend_drift["user"] = {"login": "karatzas-amd"}
        patch_gh_api({
            "/repos/vllm-project/vllm/pulls": [REAL_PR, pretend_drift],
            "/search/issues": {"items": []},
        })
        prs = collect.fetch_prs(
            "vllm-project/vllm",
            authors=["karatzas-amd"],
            labels=[],
            keywords=[],
        )
        assert [p["number"] for p in prs] == [12345]

    def test_multiple_shapes_merge_without_dup_and_without_issue_leak(
        self, patch_gh_api
    ):
        """Realistic cross-sink scenario: author sink returns a PR, search
        returns the same PR plus a leaked issue. Final output: one PR."""
        patch_gh_api({
            "/repos/vllm-project/vllm/pulls": [REAL_PR],
            "/search/issues": {"items": [REAL_PR, LEAKED_ISSUE]},
        })
        prs = collect.fetch_prs(
            "vllm-project/vllm",
            authors=["karatzas-amd"],
            labels=[],
            keywords=["moe"],
        )
        assert [p["number"] for p in prs] == [12345]

    def test_empty_result_on_empty_inputs(self, patch_gh_api):
        patch_gh_api({})
        prs = collect.fetch_prs(
            "vllm-project/vllm",
            authors=[],
            labels=[],
            keywords=[],
        )
        assert prs == []


# ---------------------------------------------------------------------------
# fetch_issues — the *other* side of the boundary.
# ---------------------------------------------------------------------------

class TestFetchIssuesDropsPRs:
    def test_issues_endpoint_strips_prs(self, patch_gh_api):
        """/repos/:r/issues includes PRs (they're issues with a pull_request
        key). fetch_issues must strip those so PRs never land in issues.json."""
        # Clone a PR into the "issues" listing as GitHub really does.
        pr_as_issue = dict(REAL_PR)
        pr_as_issue["pull_request"] = {
            "url": "https://api.github.com/repos/vllm-project/vllm/pulls/12345"
        }
        patch_gh_api({
            "/repos/vllm-project/vllm/issues": [pr_as_issue, LEAKED_ISSUE],
            "/search/issues": {"items": []},
        })
        issues = collect.fetch_issues(
            "vllm-project/vllm",
            labels=["bug"],
            keywords=[],
        )
        numbers = [i["number"] for i in issues]
        assert 99999 in numbers, "Real issue must be present"
        assert 12345 not in numbers, (
            "PR #12345 carries ``pull_request`` key — must not land in "
            "fetch_issues output"
        )

    def test_search_issues_pure_issue_kept(self, patch_gh_api):
        patch_gh_api({
            "/repos/vllm-project/vllm/issues": [],
            "/search/issues": {"items": [LEAKED_ISSUE]},
        })
        issues = collect.fetch_issues(
            "vllm-project/vllm",
            labels=[],
            keywords=["crash"],
        )
        assert [i["number"] for i in issues] == [99999]


class TestFetchAllOpenIssues:
    """``fetch_all_open_issues`` is the active-dev path. It must also strip
    PRs — same boundary, different collector function. This pins the defence
    on both helpers so refactoring one doesn't drop the guard on the other.
    """

    def test_strips_pull_requests_from_all_open_issues(self, patch_gh_api):
        pr_as_issue = dict(REAL_PR)
        pr_as_issue["pull_request"] = {
            "url": "https://api.github.com/repos/vllm-project/vllm/pulls/12345"
        }
        patch_gh_api({
            "/repos/vllm-project/vllm/issues": [pr_as_issue, LEAKED_ISSUE],
        })
        issues = collect.fetch_all_open_issues("vllm-project/vllm")
        numbers = [i["number"] for i in issues]
        assert 99999 in numbers
        assert 12345 not in numbers


class TestCollectProjectIncludesProjectIssues:
    def test_collect_project_pulls_in_project_snapshot_issue_with_assignee(
        self, tmp_path, monkeypatch, patch_gh_api
    ):
        data_root = tmp_path / "data"
        project_dir = data_root / "vllm" / "ci"
        project_dir.mkdir(parents=True)
        (project_dir / "project_items.json").write_text(json.dumps({
            "items_by_number": {
                "40240": {
                    "issue_number": 40240,
                    "repo": "vllm-project/vllm",
                    "status": "In Progress",
                    "title": "[CI Failure]: mi355_1: V1 Spec Decode",
                    "url": "https://github.com/vllm-project/vllm/issues/40240",
                }
            }
        }))
        monkeypatch.setattr(collect, "DATA", data_root)
        def load_complete_snapshot(repo):
            collect._PROJECT_QUERY_USABLE = True
            return collect.fetch_project_open_issues_from_snapshot(repo)

        monkeypatch.setattr(
            collect, "fetch_project_open_issues", load_complete_snapshot
        )

        patch_gh_api({
            "/repos/vllm-project/vllm/issues/40240/comments": [],
            "/repos/vllm-project/vllm/issues/40240": {
                "number": 40240,
                "title": "[CI Failure]: mi355_1: V1 Spec Decode",
                "state": "open",
                "user": {"login": "AndreasKaratzas"},
                "created_at": "2026-04-18T10:39:02Z",
                "updated_at": "2026-04-21T15:36:24Z",
                "html_url": "https://github.com/vllm-project/vllm/issues/40240",
                "labels": [{"name": "ci-failure"}],
                "assignees": [{"login": "AndreasKaratzas"}],
            },
        })
        monkeypatch.setattr(collect, "fetch_open_label_prs", lambda *a, **kw: [])
        monkeypatch.setattr(collect, "fetch_prs", lambda *a, **kw: [])
        monkeypatch.setattr(collect, "fetch_issues", lambda *a, **kw: [])
        monkeypatch.setattr(collect, "fetch_releases", lambda *a, **kw: [])

        collect.collect_project("vllm", {
            "repo": "vllm-project/vllm",
            "role": "upstream_watch",
            "track_authors": [],
            "track_labels": ["rocm", "amd"],
            "track_keywords": ["ROCm", "AMD", "HIP"],
        })

        payload = json.loads((data_root / "vllm" / "issues.json").read_text())
        assert payload["issues"] == [{
            "number": 40240,
            "title": "[CI Failure]: mi355_1: V1 Spec Decode",
            "author": "AndreasKaratzas",
            "state": "open",
            "created_at": "2026-04-18T10:39:02Z",
            "updated_at": "2026-04-21T15:36:24Z",
            "html_url": "https://github.com/vllm-project/vllm/issues/40240",
            "labels": ["ci-failure"],
            "assignees": ["AndreasKaratzas"],
            "project_status": "In Progress",
            "project_url": "https://github.com/orgs/vllm-project/projects/39",
            "repo": "vllm-project/vllm",
            "linked_prs": [],
        }]

    def test_live_project_issues_refresh_the_independent_snapshot(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(collect, "DATA", tmp_path / "data")
        monkeypatch.setattr(
            collect,
            "now_iso",
            lambda: "2026-08-22T10:00:00Z",
        )

        coverage = {
            "complete": True,
            "authoritative_complete": True,
            "population_semantics": "complete",
            "truncated": False,
            "queries": [],
        }
        collect.write_project_items_snapshot(
            [{
                "number": 40240,
                "state": "open",
                "repo": "vllm-project/vllm",
                "project_status": "In Progress",
                "title": "[CI Failure]: V1 Spec Decode",
                "html_url": "https://github.com/vllm-project/vllm/issues/40240",
            }],
            coverage,
        )

        snapshot = json.loads(
            (tmp_path / "data/vllm/ci/project_items.json").read_text()
        )
        assert snapshot == {
            "generated_at": "2026-08-22T10:00:00Z",
            "items_by_number": {
                "40240": {
                    "issue_number": 40240,
                    "issue_state": "OPEN",
                    "repo": "vllm-project/vllm",
                    "status": "In Progress",
                    "title": "[CI Failure]: V1 Spec Decode",
                    "url": "https://github.com/vllm-project/vllm/issues/40240",
                }
            },
            "project": "vllm-project/projects/39",
            "project_url": "https://github.com/orgs/vllm-project/projects/39",
            "count_semantics": "complete",
            "source_coverage": coverage,
        }
