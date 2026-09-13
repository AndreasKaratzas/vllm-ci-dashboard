"""Regression coverage for finite GitHub working-set collection."""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import collect
import collect_activity
import pytest


def test_project_rest_query_stops_at_explicit_page_cap(monkeypatch):
    collect._reset_source_coverage()
    calls = []

    def fake_api(endpoint, **kwargs):
        assert kwargs == {"fail_closed": True}
        calls.append(endpoint)
        return [{"number": len(calls) * 1000 + index} for index in range(100)]

    monkeypatch.setattr(collect, "gh_api", fake_api)
    with pytest.raises(collect.GitHubAPIError, match="authoritative 2-page cap"):
        collect._bounded_rest_items(
            "/repos/example/repo/pulls?state=open&per_page=100",
            query_name="test_open_prs",
            scope="test scope",
            max_pages=2,
        )

    assert len(calls) == 2
    assert calls[0].endswith("per_page=100&page=1")
    assert calls[1].endswith("per_page=100&page=2")
    assert collect._source_coverage_snapshot() == {
        "complete": False,
        "authoritative_complete": False,
        "population_semantics": "lower_bound",
        "truncated": True,
        "queries": [
            {
                "name": "test_open_prs",
                "scope": "test scope",
                "complete": False,
                "truncated": True,
                "error": False,
                "authoritative": True,
                "pages_fetched": 2,
                "max_pages": 2,
                "page_size": 100,
                "items_observed": 200,
                "total_count_hint": None,
                "provider_incomplete": False,
                "completion_reason": "page_cap",
            }
        ],
    }


def test_project_rest_query_records_short_page_as_complete(monkeypatch):
    collect._reset_source_coverage()
    calls = []

    def fake_api(endpoint, **_kwargs):
        calls.append(endpoint)
        return [{"number": 1}]

    monkeypatch.setattr(collect, "gh_api", fake_api)
    assert collect._bounded_rest_items(
        "/repos/example/repo/issues?state=open",
        query_name="test_open_issues",
        scope="test scope",
        max_pages=5,
    ) == [{"number": 1}]

    assert len(calls) == 1
    coverage = collect._source_coverage_snapshot()
    assert coverage["complete"] is True
    assert coverage["authoritative_complete"] is True
    assert coverage["truncated"] is False
    assert coverage["queries"][0]["completion_reason"] == "short_page"


def test_issue_comment_collection_never_exceeds_two_pages(monkeypatch):
    collect._reset_source_coverage()
    calls = []

    def fake_api(endpoint, **_kwargs):
        calls.append(endpoint)
        return [{"body": f"comment {index}"} for index in range(100)]

    monkeypatch.setattr(collect, "gh_api", fake_api)
    comments = collect.fetch_issue_comments("example/repo", 42)

    assert len(comments) == 200
    assert len(calls) == collect.MAX_ISSUE_COMMENT_PAGES == 2
    coverage = collect._source_coverage_snapshot()
    assert coverage["complete"] is False
    assert coverage["authoritative_complete"] is False
    assert coverage["population_semantics"] == "lower_bound"
    assert coverage["truncated"] is True
    assert coverage["queries"][0]["name"] == "project_issue_comments:42"
    assert coverage["queries"][0]["authoritative"] is True


def test_project_link_discovery_reads_full_body_but_publishes_bounded_preview(
    monkeypatch,
):
    collect._reset_source_coverage()
    monkeypatch.setattr(collect, "fetch_issue_comments", lambda *_args: [])
    body = "x" * 9000 + "\nPR #4321"
    issue = collect._normalize_graphql_issue(
        {
            "number": 42,
            "body": body,
            "repository": {"nameWithOwner": "example/repo"},
        }
    )

    assert len(issue["body_head"]) == 8000
    enriched = collect.enrich_project_issues_with_linked_prs(
        "example/repo", [issue]
    )

    assert enriched[0]["linked_prs"] == [
        {
            "repo": "example/repo",
            "number": 4321,
            "url": "https://github.com/example/repo/pull/4321",
        }
    ]
    assert "_link_source_body" not in enriched[0]


def test_project_issue_pr_references_have_item_and_lookup_caps(monkeypatch):
    collect._reset_source_coverage()
    monkeypatch.setattr(collect, "fetch_issue_comments", lambda *_args: [])
    issue = {
        "number": 42,
        "body_head": "\n".join(
            f"PR #{number}" for number in range(1, collect.MAX_LINKED_PRS_PER_ISSUE + 6)
        ),
    }
    enriched = collect.enrich_project_issues_with_linked_prs(
        "example/repo", [issue]
    )
    assert len(enriched[0]["linked_prs"]) == collect.MAX_LINKED_PRS_PER_ISSUE

    calls = []
    monkeypatch.setattr(
        collect,
        "fetch_pr_by_number",
        lambda _repo, number: calls.append(number),
    )
    repeated = []
    for issue_number in range(6):
        repeated.append(
            {
                "number": issue_number,
                "linked_prs": [
                    {"repo": "example/repo", "number": issue_number * 20 + offset}
                    for offset in range(20)
                ],
            }
        )
    collect.resolve_project_issue_pr_refs("example/repo", repeated, [])

    assert len(calls) == collect.MAX_DIRECT_LINKED_PR_LOOKUPS
    coverage = collect._source_coverage_snapshot()
    assert coverage["complete"] is False
    assert coverage["authoritative_complete"] is False
    assert coverage["population_semantics"] == "lower_bound"
    assert coverage["truncated"] is True
    assert coverage["queries"][-1]["completion_reason"] == "direct_lookup_cap"


def _project_payload(*, has_next_page):
    return {
        "data": {
            "organization": {
                "projectV2": {
                    "items": {
                        "nodes": [],
                        "pageInfo": {
                            "hasNextPage": has_next_page,
                            "endCursor": "cursor" if has_next_page else None,
                        },
                    }
                }
            }
        }
    }


def test_project_items_uses_one_server_filtered_page_and_marks_lower_bound(
    monkeypatch,
):
    collect._reset_source_coverage()
    calls = []

    def fake_graphql(query, variables, **kwargs):
        calls.append((query, variables, kwargs))
        return _project_payload(has_next_page=True)

    monkeypatch.setattr(collect, "gh_graphql", fake_graphql)
    assert collect.fetch_project_open_issues("vllm-project/vllm") == []

    assert len(calls) == 1
    query, variables, kwargs = calls[0]
    assert "items(first: 100, query: $itemQuery)" in query
    assert "after:" not in query
    assert variables["itemQuery"] == "is:issue is:open repo:vllm-project/vllm"
    assert kwargs == {"fail_closed": True}
    assert collect._PROJECT_QUERY_USABLE is True
    coverage = collect._source_coverage_snapshot()
    assert coverage["complete"] is False
    assert coverage["authoritative_complete"] is False
    assert coverage["population_semantics"] == "lower_bound"
    assert coverage["truncated"] is True
    assert coverage["queries"][0]["pages_fetched"] == 1


def test_empty_complete_project_query_is_usable_and_does_not_restore_stale_items(
    monkeypatch,
):
    collect._reset_source_coverage()
    monkeypatch.setattr(
        collect,
        "gh_graphql",
        lambda *_args, **_kwargs: _project_payload(has_next_page=False),
    )

    assert collect.fetch_project_open_issues("vllm-project/vllm") == []
    assert collect._PROJECT_QUERY_USABLE is True
    assert collect._source_coverage_snapshot()["complete"] is True


def _seed_home_surface(data_root):
    project_dir = data_root / "vllm"
    ci_dir = project_dir / "ci"
    ci_dir.mkdir(parents=True)
    paths = {
        "prs": project_dir / "prs.json",
        "issues": project_dir / "issues.json",
        "releases": project_dir / "releases.json",
        "project": ci_dir / "project_items.json",
    }
    for name, path in paths.items():
        path.write_bytes(f"prior-{name}\n".encode())
    return paths


def test_project_graphql_error_cannot_overwrite_prior_home_surface(
    tmp_path, monkeypatch
):
    data_root = tmp_path / "data"
    paths = _seed_home_surface(data_root)
    before = {name: path.read_bytes() for name, path in paths.items()}
    monkeypatch.setattr(collect, "DATA", data_root)

    def fail_graphql(*_args, **_kwargs):
        raise collect.GitHubAPIError("simulated GraphQL failure")

    monkeypatch.setattr(collect, "gh_graphql", fail_graphql)
    with pytest.raises(collect.GitHubAPIError, match="simulated GraphQL failure"):
        collect.collect_project("vllm", {"repo": "vllm-project/vllm"})

    assert {name: path.read_bytes() for name, path in paths.items()} == before


def test_authoritative_rest_cap_publishes_truthful_lower_bound(
    tmp_path, monkeypatch
):
    data_root = tmp_path / "data"
    paths = _seed_home_surface(data_root)
    monkeypatch.setattr(collect, "DATA", data_root)
    monkeypatch.setattr(
        collect,
        "gh_graphql",
        lambda *_args, **_kwargs: _project_payload(has_next_page=False),
    )

    def full_search_page(endpoint, **_kwargs):
        assert "/search/issues" in endpoint
        return {
            "total_count": 1000,
            "incomplete_results": False,
            "items": [{"number": index} for index in range(100)],
        }

    monkeypatch.setattr(collect, "gh_api", full_search_page)
    monkeypatch.setattr(collect, "fetch_releases", lambda *_args: [])
    collect.collect_project("vllm", {"repo": "vllm-project/vllm"})

    for name in ("prs", "issues"):
        payload = json.loads(paths[name].read_text())
        assert payload["count_semantics"] == "lower_bound"
        assert payload["source_coverage"]["authoritative_complete"] is False
        assert payload["source_coverage"]["truncated"] is True
    project = json.loads(paths["project"].read_text())
    assert project["count_semantics"] == "lower_bound"


def test_search_incomplete_results_are_never_marked_exhaustive(monkeypatch):
    collect._reset_source_coverage()
    calls = []

    def incomplete_search(endpoint, **_kwargs):
        calls.append(endpoint)
        return {
            "total_count": 50,
            "incomplete_results": True,
            "items": [{"number": 1}],
        }

    monkeypatch.setattr(collect, "gh_api", incomplete_search)
    items = collect._bounded_rest_items(
        "/search/issues?q=repo:example/repo+is:pr",
        query_name="incomplete_search",
        scope="test search",
        max_pages=3,
        item_key="items",
        allow_partial=True,
    )

    assert items == [{"number": 1}]
    assert len(calls) == 1
    query = collect._source_coverage_snapshot()["queries"][0]
    assert query["complete"] is False
    assert query["provider_incomplete"] is True
    assert query["completion_reason"] == "provider_incomplete"


def test_activity_query_stops_when_sorted_time_scope_is_exhausted(monkeypatch):
    collect_activity._reset_source_coverage()
    calls = []
    cutoff = datetime(2026, 8, 1, tzinfo=timezone.utc)
    recent = {"updated_at": "2026-08-02T00:00:00Z"}
    old = {"updated_at": "2026-07-31T23:59:59Z"}

    def fake_api(endpoint, **_kwargs):
        calls.append(endpoint)
        if len(calls) == 1:
            return [recent] * 100
        return [old] * 100

    monkeypatch.setattr(collect_activity, "gh_api", fake_api)
    items = collect_activity.gh_api_list(
        "/repos/example/repo/pulls?state=all&per_page=100",
        query_name="recent_prs",
        scope="updated since cutoff",
        max_pages=5,
        stop_when=lambda item: collect_activity.parse_iso(item["updated_at"]) < cutoff,
    )

    assert len(items) == 200
    assert len(calls) == 2
    coverage = collect_activity._source_coverage_snapshot()
    # This direct helper test has no PR/issue input snapshots, so overall
    # activity coverage remains incomplete even though the query's own time
    # scope was proven complete.
    assert coverage["complete"] is False
    assert coverage["queries"][0]["complete"] is True
    assert coverage["queries"][0]["completion_reason"] == "scope_boundary"


def test_activity_query_cap_is_explicitly_incomplete(monkeypatch):
    collect_activity._reset_source_coverage()
    calls = []

    def fake_api(endpoint, **_kwargs):
        calls.append(endpoint)
        return [{"sha": str(index)} for index in range(100)]

    monkeypatch.setattr(collect_activity, "gh_api", fake_api)
    items = collect_activity.gh_api_list(
        "/repos/example/repo/commits?since=2026-08-01T00:00:00Z&per_page=100",
        query_name="recent_commits",
        scope="30 days",
        max_pages=3,
        allow_partial=True,
    )

    assert len(items) == 300
    assert len(calls) == 3
    coverage = collect_activity._source_coverage_snapshot()
    assert coverage["complete"] is False
    assert coverage["truncated"] is True
    assert coverage["population_semantics"] == "lower_bound"
    assert coverage["queries"][0]["completion_reason"] == "page_cap"


def test_incomplete_activity_search_count_is_a_lower_bound(monkeypatch):
    collect_activity._reset_source_coverage()
    monkeypatch.setattr(
        collect_activity,
        "gh_api",
        lambda *_args, **_kwargs: {
            "total_count": 123,
            "incomplete_results": True,
        },
    )

    assert collect_activity._search_total_count(
        "/search/issues?q=test",
        query_name="test_count",
        scope="test",
    ) == 123
    query = collect_activity._SOURCE_QUERY_COVERAGE[0]
    assert query["complete"] is False
    assert query["truncated"] is True
    assert query["completion_reason"] == "provider_incomplete"


def test_transient_rest_failure_is_retried_once(monkeypatch):
    calls = []

    def fake_run(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(
                1, ["gh", "api"], stderr="HTTP 502: upstream unavailable"
            )
        return subprocess.CompletedProcess(["gh", "api"], 0, stdout="[]", stderr="")

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    assert collect.gh_api("/repos/example/repo/issues", fail_closed=True) == []
    assert len(calls) == collect.GH_TRANSIENT_ATTEMPTS == 2


def test_workflow_runs_are_reused_across_activity_metrics(monkeypatch):
    collect_activity._reset_source_coverage()
    calls = []
    runs = [{
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:10:00Z",
        "conclusion": "success",
    }]

    def fake_api(endpoint, **_kwargs):
        calls.append(endpoint)
        return {"workflow_runs": runs}

    monkeypatch.setattr(collect_activity, "gh_api", fake_api)
    collect_activity.collect_ci_health("example/repo", "pytorch")
    collect_activity.collect_ci_signal_time("example/repo", "pytorch")

    assert len(calls) == len(collect_activity.WORKFLOW_IDS["pytorch"])


def test_activity_main_exits_nonzero_after_github_collection_failure(
    tmp_path, monkeypatch
):
    config = tmp_path / "projects.yaml"
    config.write_text("projects:\n  vllm:\n    repo: vllm-project/vllm\n")
    monkeypatch.setattr(collect_activity, "CONFIG", config)
    monkeypatch.setattr(
        collect_activity,
        "collect_project_activity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            collect_activity.GitHubAPIError("simulated API failure")
        ),
    )

    with pytest.raises(SystemExit, match="failed closed for: vllm"):
        collect_activity.main()


def test_recurring_project_collectors_do_not_use_unbounded_cli_pagination():
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "scripts/collect.py",
        "scripts/collect_activity.py",
        ".github/workflows/nightly-ci.yml",
    ):
        text = (root / relative).read_text()
        assert "--paginate" not in text
        assert "github.paginate" not in text
        assert "paginate=True" not in text


def test_nightly_incident_label_remains_a_single_canonical_index():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github" / "workflows" / "nightly-ci.yml").read_text()

    # Both failure and recovery paths remove the ownership label from every
    # non-canonical labeled issue, while adding it only to the selected owner.
    assert workflow.count("await github.rest.issues.removeLabel({") == 2
    assert workflow.count(
        "if (existing && issue.number === existing.number) continue;"
    ) == 1
    assert workflow.count("if (issue.number === canonicalNumber) continue;") == 1
    assert workflow.count("issue_number: existing.number,") >= 1
    assert "issue_number: issue.number," in workflow
    assert workflow.count("if (hasNextPage(labeledResponse))") == 2
    assert workflow.count("if (![...candidates.values()].some(isOwned))") == 2
    assert workflow.count("if (hasNextPage(recentResponse))") == 2
    assert "!recentResponse.data.some(isOwned)" not in workflow
    assert "cancel-in-progress: false" in workflow
    assert (
        "for (const issue of ownedIssues) {\n"
        "              const issueLabels = new Set"
    ) not in workflow
