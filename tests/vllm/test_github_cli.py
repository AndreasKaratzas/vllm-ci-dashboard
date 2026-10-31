"""GitHub CLI response headers participate in bounded collector recovery."""

import json
import subprocess

import collect
import collect_activity
import github_cli
import pytest
from github_transport import GitHubTransport


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.waits = []

    def time(self):
        return self.now

    def sleep(self, delay):
        self.waits.append(delay)
        self.now += delay


@pytest.fixture
def transport(monkeypatch):
    clock = Clock()
    client = GitHubTransport(clock=clock.time, sleep=clock.sleep)
    monkeypatch.setattr(github_cli, "_TRANSPORT", client)
    return client, clock


def response(status, payload, *, headers=None):
    head = [f"HTTP/2.0 {status} Test status"]
    head.extend(f"{key}: {value}" for key, value in (headers or {}).items())
    return subprocess.CompletedProcess(
        ["gh", "api"], int(status >= 400),
        stdout="\r\n".join(head) + "\r\n\r\n" + json.dumps(payload), stderr="",
    )


@pytest.mark.parametrize("collector", [collect, collect_activity])
def test_secondary_rate_limit_headers_drive_recovery_for_both_collectors(
    collector, transport, monkeypatch,
):
    _, clock = transport
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[-1] == "--include"
        assert kwargs["timeout"] == github_cli.CLI_TIMEOUT_SECONDS
        if len(calls) == 1:
            return response(403, {"message": "secondary rate limit"}, headers={"Retry-After": "5"})
        return response(200, [{"number": 7}])

    monkeypatch.setattr(collector.subprocess, "run", run)
    assert collector.gh_api("/repos/example/repo/issues", fail_closed=True) == [{"number": 7}]
    assert len(calls) == 2
    assert clock.waits == [5.0]


def test_graphql_rate_limit_in_success_status_recovers_and_preserves_token_env(
    transport, monkeypatch,
):
    _, clock = transport
    calls = []
    monkeypatch.setenv("PROJECTS_READ_TOKEN", "fixture-project-token")

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["env"]["GH_TOKEN"] == "fixture-project-token"
        if len(calls) == 1:
            return response(200, {"errors": [{"type": "RATE_LIMITED", "message": "rate limit exceeded"}]})
        return response(200, {"data": {"project": {"id": "fixture-id"}}})

    monkeypatch.setattr(collect.subprocess, "run", run)
    assert collect.gh_graphql("query { viewer { login } }", fail_closed=True) == {
        "data": {"project": {"id": "fixture-id"}}
    }
    assert len(calls) == 2
    assert clock.waits == [60.0]


def test_permanent_graphql_errors_do_not_retry_or_become_empty_success(transport, monkeypatch):
    calls = []

    def run(*_args, **_kwargs):
        calls.append(1)
        return response(200, {"errors": [{"type": "FORBIDDEN", "message": "not authorized"}]})

    monkeypatch.setattr(collect.subprocess, "run", run)
    with pytest.raises(collect.GitHubAPIError, match="errors"):
        collect.gh_graphql("query { viewer { login } }", fail_closed=True)
    assert len(calls) == 1


def test_exhausted_secondary_limit_stops_later_collector_calls(transport, monkeypatch):
    _, clock = transport
    calls = []

    def run(*_args, **_kwargs):
        calls.append(1)
        return response(403, {"message": "secondary rate limit"})

    monkeypatch.setattr(collect.subprocess, "run", run)
    with pytest.raises(collect.GitHubAPIError, match="rate limit"):
        collect.gh_api("/repos/example/repo/issues", fail_closed=True)
    with pytest.raises(collect_activity.GitHubAPIError, match="rate limit"):
        collect_activity.gh_api("/repos/example/repo/pulls", fail_closed=True)
    assert len(calls) == 3
    assert sum(clock.waits) == 180.0


@pytest.mark.parametrize("collector", [collect, collect_activity])
def test_permanent_forbidden_status_only_in_stderr_is_not_retried(
    collector, transport, monkeypatch,
):
    calls = []

    def run(*_args, **_kwargs):
        calls.append(1)
        return subprocess.CompletedProcess(
            ["gh", "api"], 1, stdout="", stderr="gh: Resource not accessible (HTTP 403)"
        )

    monkeypatch.setattr(collector.subprocess, "run", run)
    with pytest.raises(collector.GitHubAPIError, match="HTTP 403"):
        collector.gh_api("/repos/example/repo/issues", fail_closed=True)
    assert len(calls) == 1


def test_malformed_success_json_and_cli_timeout_share_finite_retry_budget(transport, monkeypatch):
    calls = []

    def run(command, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(command, github_cli.CLI_TIMEOUT_SECONDS)
        return subprocess.CompletedProcess(
            command, 0, stdout="HTTP/2.0 200 OK\n\nnot-json", stderr=""
        )

    monkeypatch.setattr(collect.subprocess, "run", run)
    with pytest.raises(collect.GitHubAPIError, match="bounded retries"):
        collect.gh_api("/repos/example/repo/issues", fail_closed=True)
    assert len(calls) == 3


@pytest.mark.parametrize("message", [
    "gh: You have exceeded a secondary rate limit. Please wait a few minutes.",
    "gh: API rate limit exceeded for this account.",
    "gh: You have triggered an abuse detection mechanism.",
])
def test_explicit_throttling_without_http_response_obeys_cooldown(
    message, transport, monkeypatch,
):
    _, clock = transport
    calls = []

    def run(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            return subprocess.CompletedProcess(["gh", "api"], 1, stdout="", stderr=message)
        return response(200, [{"number": 7}])

    monkeypatch.setattr(collect.subprocess, "run", run)
    assert collect.gh_api("/repos/example/repo/issues", fail_closed=True) == [{"number": 7}]
    assert len(calls) == 2
    assert clock.waits == [60.0]
