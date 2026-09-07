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


def test_eligible_issue_reader_uses_three_finite_workstream_intersections(
    monkeypatch,
):
    calls = []

    class Response:
        @staticmethod
        def raise_for_status():
            return None

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    def get(url, *, headers, params, timeout):
        calls.append(params)
        workstream = params["labels"].split(",", 1)[1]
        return Response([_issue(len(calls), ["automated", workstream])])

    monkeypatch.setattr(sync.requests, "get", get)

    rows = sync.fetch_eligible_issues("token", sync.DASHBOARD_REPO)

    assert len(rows) == len(sync.ALLOWED_WORKSTREAMS) == 3
    assert {call["labels"] for call in calls} == {
        f"automated,{workstream}" for workstream in sync.ALLOWED_WORKSTREAMS
    }
    assert all(call["page"] == 1 and call["per_page"] == 100 for call in calls)


def test_eligible_issue_reader_fails_closed_on_full_page(monkeypatch):
    class Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return [
                _issue(index, ["automated", "workstream:dashboard-ci"])
                for index in range(100)
            ]

    monkeypatch.setattr(sync.requests, "get", lambda *_args, **_kwargs: Response())

    with pytest.raises(RuntimeError, match="bounded ambiguity limit"):
        sync.fetch_eligible_issues("token", sync.DASHBOARD_REPO)


def test_project_membership_reader_checks_only_bounded_eligible_issue_ids(
    monkeypatch,
):
    payload = {
        "nodes": [
            {
                "__typename": "Issue",
                "id": "I_keep",
                "number": 1,
                "repository": {"nameWithOwner": sync.DASHBOARD_REPO},
                "projectItems": {
                    "nodes": [
                        {"project": {"id": sync.EXPECTED_PROJECT["id"]}}
                    ],
                    "pageInfo": {"hasNextPage": False},
                },
            },
            {
                "__typename": "Issue",
                "id": "I_missing",
                "number": 2,
                "repository": {"nameWithOwner": sync.DASHBOARD_REPO},
                "projectItems": {
                    "nodes": [{"project": {"id": "PVT_other"}}],
                    "pageInfo": {"hasNextPage": False},
                },
            },
        ]
    }
    seen = []

    def fake_graphql(token, query, variables):
        seen.append((query, variables))
        return payload

    monkeypatch.setattr(sync, "_graphql", fake_graphql)

    assert sync.fetch_project_issue_ids(
        "token",
        sync.EXPECTED_PROJECT["id"],
        sync.DASHBOARD_REPO,
        ["I_missing", "I_keep"],
    ) == {"I_keep"}
    assert seen == [
        (
            sync.ISSUE_PROJECT_MEMBERSHIP_QUERY,
            {"issueIds": ["I_keep", "I_missing"]},
        )
    ]


def test_project_membership_reader_fails_closed_when_ten_items_are_incomplete(
    monkeypatch,
):
    monkeypatch.setattr(
        sync,
        "_graphql",
        lambda *_args, **_kwargs: {
            "nodes": [
                {
                    "__typename": "Issue",
                    "id": "I_1",
                    "repository": {"nameWithOwner": sync.DASHBOARD_REPO},
                    "projectItems": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": True},
                    },
                }
            ]
        },
    )

    with pytest.raises(RuntimeError, match="membership is ambiguous"):
        sync.fetch_project_issue_ids(
            "token",
            sync.EXPECTED_PROJECT["id"],
            sync.DASHBOARD_REPO,
            ["I_1"],
        )


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
        "fetch_project_metadata",
        lambda token, project_id, repo: {},
    )
    monkeypatch.setattr(
        sync,
        "fetch_project_issue_ids",
        lambda token, project_id, repo, issue_ids: {"I_1"},
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


def test_run_completes_all_membership_reads_before_first_mutation(monkeypatch):
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
        lambda *_args: [_issue(1, ["automated", "workstream:infra"])],
    )
    monkeypatch.setattr(sync, "fetch_project_metadata", lambda *_args: {})
    monkeypatch.setattr(
        sync,
        "fetch_project_issue_ids",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("ambiguous membership")),
    )
    added = []
    monkeypatch.setattr(sync, "add_project_item", lambda *args: added.append(args))

    with pytest.raises(RuntimeError, match="ambiguous membership"):
        sync.run()

    assert added == []


def test_add_mutation_is_the_only_graphql_mutation():
    assert "mutation" not in sync.PROJECT_METADATA_QUERY.casefold()
    assert "items(first:" not in sync.PROJECT_METADATA_QUERY
    assert "mutation" not in sync.ISSUE_PROJECT_MEMBERSHIP_QUERY.casefold()
    assert "projectItems(first: 10, includeArchived: true)" in (
        sync.ISSUE_PROJECT_MEMBERSHIP_QUERY
    )
    assert sync.ADD_PROJECT_ITEM_MUTATION.casefold().count("mutation") == 1
    assert "addProjectV2ItemById" in sync.ADD_PROJECT_ITEM_MUTATION
    for forbidden in (
        "deleteProjectV2Item",
        "updateProjectV2ItemFieldValue",
        "updateProjectV2",
    ):
        assert forbidden not in sync.ADD_PROJECT_ITEM_MUTATION
