from __future__ import annotations

import pytest

from vllm import sync_ci_operations_project as sync


def _issue(number=1, labels=None, **extra):
    return {
        "number": number,
        "node_id": f"I_{number}",
        "state": "open",
        "labels": [{"name": value} for value in (labels or [])],
        **extra,
    }


def test_eligible_issue_requires_automation_and_exactly_one_known_workstream():
    assert sync.eligible_issue(_issue(labels=["automated", "workstream:infra"]))
    assert sync.eligible_issue(_issue(labels=["AUTOMATED", "workstream:dev"]))
    assert not sync.eligible_issue(_issue(labels=["automated"]))
    assert not sync.eligible_issue(
        _issue(labels=["automated", "workstream:infra", "workstream:dev"])
    )
    assert not sync.eligible_issue(
        _issue(labels=["automated", "workstream:unknown"])
    )
    assert not sync.eligible_issue(
        _issue(labels=["automated", "workstream:infra", "workstream:unknown"])
    )
    assert not sync.eligible_issue(
        _issue(labels=["automated", "workstream:infra"], pull_request={})
    )
    assert not sync.eligible_issue(
        _issue(labels=["automated", "workstream:infra"], state="closed")
    )


def test_project_item_reader_keeps_only_dashboard_issue_ids(monkeypatch):
    def project(nodes, page_info):
        return {
            "id": sync.EXPECTED_PROJECT["id"],
            "number": sync.EXPECTED_PROJECT["number"],
            "title": sync.EXPECTED_PROJECT["title"],
            "url": sync.EXPECTED_PROJECT["url"],
            "public": True,
            "closed": False,
            "viewerCanUpdate": True,
            "owner": {
                "__typename": "User",
                "login": sync.EXPECTED_PROJECT["owner"],
            },
            "repositories": {
                "totalCount": 1,
                "nodes": [{"nameWithOwner": sync.DASHBOARD_REPO}],
            },
            "items": {
                "nodes": nodes,
                "pageInfo": page_info,
            },
        }

    pages = [
        {
            "node": project(
                [
                    {
                        "content": {
                            "__typename": "Issue",
                            "id": "I_keep",
                            "repository": {
                                "nameWithOwner": sync.DASHBOARD_REPO,
                            },
                        }
                    },
                    {
                        "content": {
                            "__typename": "Issue",
                            "id": "I_other",
                            "repository": {"nameWithOwner": "other/repo"},
                        }
                    },
                ],
                {"hasNextPage": True, "endCursor": "next"},
            )
        },
        {
            "node": project(
                [
                    {
                        "content": {
                            "__typename": "PullRequest",
                            "id": "PR_ignore",
                        }
                    },
                    {
                        "content": {
                            "__typename": "Issue",
                            "id": "I_second",
                            "repository": {
                                "nameWithOwner": sync.DASHBOARD_REPO,
                            },
                        }
                    },
                ],
                {"hasNextPage": False, "endCursor": None},
            )
        },
    ]
    seen = []

    def fake_graphql(token, query, variables):
        seen.append(variables)
        return pages.pop(0)

    monkeypatch.setattr(sync, "_graphql", fake_graphql)

    assert sync.fetch_project_issue_ids(
        "token",
        sync.EXPECTED_PROJECT["id"],
        sync.DASHBOARD_REPO,
    ) == {"I_keep", "I_second"}
    assert seen == [
        {"projectId": sync.EXPECTED_PROJECT["id"], "cursor": None},
        {"projectId": sync.EXPECTED_PROJECT["id"], "cursor": "next"},
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("number", 99),
        ("title", "Wrong"),
        ("url", "https://github.com/users/other/projects/2"),
        ("public", False),
        ("closed", True),
        ("viewerCanUpdate", False),
    ],
)
def test_project_validation_fails_closed_on_metadata_mismatch(field, value):
    project = {
        "id": sync.EXPECTED_PROJECT["id"],
        "number": sync.EXPECTED_PROJECT["number"],
        "title": sync.EXPECTED_PROJECT["title"],
        "url": sync.EXPECTED_PROJECT["url"],
        "public": True,
        "closed": False,
        "viewerCanUpdate": True,
        "owner": {"login": sync.EXPECTED_PROJECT["owner"]},
        "repositories": {
            "totalCount": 1,
            "nodes": [{"nameWithOwner": sync.DASHBOARD_REPO}],
        },
    }
    project[field] = value

    with pytest.raises(RuntimeError, match="scope validation"):
        sync.validate_project(
            project,
            sync.EXPECTED_PROJECT["id"],
            sync.DASHBOARD_REPO,
        )


def test_project_validation_rejects_other_owner_or_repository():
    project = {
        "id": sync.EXPECTED_PROJECT["id"],
        "number": sync.EXPECTED_PROJECT["number"],
        "title": sync.EXPECTED_PROJECT["title"],
        "url": sync.EXPECTED_PROJECT["url"],
        "public": True,
        "closed": False,
        "viewerCanUpdate": True,
        "owner": {"login": "other"},
        "repositories": {
            "totalCount": 2,
            "nodes": [
                {"nameWithOwner": sync.DASHBOARD_REPO},
                {"nameWithOwner": "other/repo"},
            ],
        },
    }

    with pytest.raises(
        RuntimeError,
        match="owner, repository, repository_count",
    ):
        sync.validate_project(
            project,
            sync.EXPECTED_PROJECT["id"],
            sync.DASHBOARD_REPO,
        )


def test_missing_project_token_is_safe_noop(monkeypatch):
    monkeypatch.delenv("PROJECTS_WRITE_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", sync.DASHBOARD_REPO)
    monkeypatch.setattr(
        sync,
        "load_ownership_config",
        lambda _path: {
            "project": {
                **sync.EXPECTED_PROJECT,
                "repository": sync.DASHBOARD_REPO,
            }
        },
    )
    monkeypatch.setattr(
        sync,
        "fetch_eligible_issues",
        lambda *_args: pytest.fail("missing token must not touch GitHub"),
    )

    assert sync.run() == 0


def test_run_adds_only_missing_eligible_issues(monkeypatch):
    monkeypatch.setenv("PROJECTS_WRITE_TOKEN", "project-token")
    monkeypatch.setenv("GITHUB_TOKEN", "repo-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", sync.DASHBOARD_REPO)
    monkeypatch.setattr(
        sync,
        "load_ownership_config",
        lambda _path: {
            "project": {
                **sync.EXPECTED_PROJECT,
                "repository": sync.DASHBOARD_REPO,
            }
        },
    )
    monkeypatch.setattr(
        sync,
        "fetch_eligible_issues",
        lambda token, repo: [
            _issue(1, ["automated", "workstream:infra"]),
            _issue(2, ["automated", "workstream:dev"]),
        ],
    )
    monkeypatch.setattr(
        sync,
        "fetch_project_issue_ids",
        lambda token, project_id, repo: {"I_1"},
    )
    added = []
    monkeypatch.setattr(
        sync,
        "add_project_item",
        lambda token, project_id, content_id: added.append(
            (token, project_id, content_id)
        ),
    )

    assert sync.run() == 0
    assert added == [
        ("project-token", sync.EXPECTED_PROJECT["id"], "I_2")
    ]


def test_add_mutation_is_the_only_graphql_mutation():
    assert "mutation" not in sync.PROJECT_ITEMS_QUERY.casefold()
    assert sync.ADD_PROJECT_ITEM_MUTATION.casefold().count("mutation") == 1
    assert "addProjectV2ItemById" in sync.ADD_PROJECT_ITEM_MUTATION
    for forbidden in (
        "deleteProjectV2Item",
        "updateProjectV2ItemFieldValue",
        "updateProjectV2",
    ):
        assert forbidden not in sync.ADD_PROJECT_ITEM_MUTATION
