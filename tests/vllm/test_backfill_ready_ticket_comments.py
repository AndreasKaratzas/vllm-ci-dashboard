from __future__ import annotations

import ast
import inspect
import json

import pytest

from vllm import backfill_ready_ticket_comments as backfill


class TestGeneratedCommentDetection:
    def test_matches_still_failing_comment(self):
        body = (
            "Still failing as of 2026-04-19. Build(s): #7806.\n\n"
            "*https://github.com/AndreasKaratzas/vllm-ci-dashboard/actions/runs/24641501904*"
        )
        assert backfill.is_generated_ready_ticket_comment(body) is True

    def test_matches_full_sync_body_comment(self):
        body = (
            "## AMD nightly — failing test group\n\n"
            "**Group:** `mi325_1: Kernels MoE Test %N`\n\n"
            "Auto-managed by `sync_ready_tickets.py`. Closed + moved to Done when this group passes.\n"
        )
        assert backfill.is_generated_ready_ticket_comment(body) is True

    def test_ignores_human_comment(self):
        assert backfill.is_generated_ready_ticket_comment("Human triage note") is False


class TestReadOnlyPolicy:
    def test_graphql_rejects_mutation_before_network(self, monkeypatch):
        def _unexpected_post(*args, **kwargs):
            raise AssertionError("a rejected mutation reached the network")

        monkeypatch.setattr(backfill.requests, "post", _unexpected_post)
        with pytest.raises(RuntimeError, match="GraphQL is read-only"):
            backfill._graphql("fake-token", "mutation { deleteProjectV2Item {} }", {})

    def test_only_graphql_query_uses_an_http_write_verb(self):
        tree = ast.parse(inspect.getsource(backfill))
        write_methods = {"delete", "patch", "post", "put"}
        writers: dict[str, set[str]] = {}

        for function in (
            node for node in tree.body if isinstance(node, ast.FunctionDef)
        ):
            methods = {
                call.func.attr
                for call in ast.walk(function)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "requests"
                and call.func.attr in write_methods
            }
            if methods:
                writers[function.name] = methods

        assert writers == {"_graphql": {"post"}}


class TestDesiredBodyLoading:
    def test_loads_issue_number_to_body_map(self, tmp_path):
        path = tmp_path / "ready_tickets.json"
        path.write_text(json.dumps({
            "tickets": [
                {"issue_number": 40212, "body": "body 40212"},
                {"issue_number": 40213, "body": "body 40213"},
                {"issue_number": None, "body": "skip"},
            ]
        }))
        assert backfill._load_desired_issue_bodies(str(path)) == {
            40212: "body 40212",
            40213: "body 40213",
        }

    def test_missing_file_is_empty(self):
        assert backfill._load_desired_issue_bodies("/no/such/file.json") == {}


class TestProjectItemLoading:
    def test_loads_only_open_repo_issues_from_snapshot(self, tmp_path):
        path = tmp_path / "project_items.json"
        path.write_text(json.dumps({
            "items_by_number": {
                "40212": {
                    "issue_number": 40212,
                    "issue_state": "OPEN",
                    "repo": "vllm-project/vllm",
                    "title": "[CI Failure]: one",
                    "url": "https://github.com/vllm-project/vllm/issues/40212",
                },
                "40213": {
                    "issue_number": 40213,
                    "issue_state": "CLOSED",
                    "repo": "vllm-project/vllm",
                    "title": "[CI Failure]: two",
                    "url": "https://github.com/vllm-project/vllm/issues/40213",
                },
                "40214": {
                    "issue_number": 40214,
                    "issue_state": "OPEN",
                    "repo": "other/repo",
                    "title": "[CI Failure]: three",
                    "url": "https://github.com/other/repo/issues/40214",
                },
            }
        }))
        issues = backfill._iter_open_project_issues_from_snapshot(
            str(path),
            repo_full_name="vllm-project/vllm",
        )
        assert [(issue.issue_number, issue.title, issue.repo) for issue in issues] == [
            (40212, "[CI Failure]: one", "vllm-project/vllm"),
        ]
        assert issues[0].body == ""
