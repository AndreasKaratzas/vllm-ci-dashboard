from __future__ import annotations

import pytest

from vllm import ensure_ci_operations_labels as labels


class FakeClient:
    instances = []
    failures = set()

    def __init__(self, token, repo):
        self.token = token
        self.repo = repo
        self.calls = []
        self.__class__.instances.append(self)

    def ensure_label(self, name, color, description):
        self.calls.append((name, color, description))
        return name not in self.failures


def test_label_set_has_exact_project_workstreams():
    names = [name for name, _color, _description in labels.LABEL_SPECS]

    assert names == [
        "automated",
        "amd-ci-regression",
        "ci-main-failure",
        "workstream:infra",
        "workstream:dashboard-ci",
        "workstream:dev",
    ]


def test_run_ensures_every_label_in_dashboard_repo(monkeypatch):
    FakeClient.instances = []
    FakeClient.failures = set()
    monkeypatch.setattr(labels, "GitHubIssueClient", FakeClient)
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", labels.DASHBOARD_REPO)

    assert labels.run() == 0
    client = FakeClient.instances[-1]
    assert client.repo == labels.DASHBOARD_REPO
    assert client.calls == labels.LABEL_SPECS


def test_run_fails_closed_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", labels.DASHBOARD_REPO)

    assert labels.run() == 1


def test_run_rejects_other_repository(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "other/repo")

    with pytest.raises(RuntimeError, match="restricted"):
        labels.run()
