"""Retry, pacing, and cross-collector cooldown contracts without network I/O."""

import json
from email.utils import formatdate

import pytest
import requests

from github_transport import GitHubResponse, GitHubTransport, GitHubTransportError


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def transport(clock, **kwargs):
    return GitHubTransport(clock=clock.time, sleep=clock.sleep, **kwargs)


@pytest.mark.parametrize("status", [403, 429])
def test_rate_limit_without_headers_waits_one_minute_then_recovers(status):
    clock = Clock()
    policy = transport(clock)
    responses = iter([
        GitHubResponse(status, {}, '{"message":"You have exceeded a secondary rate limit"}'),
        GitHubResponse(200, {}, '{"items":[]}'),
    ])
    charged = []
    response = policy.request(
        "https://api.github.com/search/issues", lambda: next(responses),
        before_request=lambda: charged.append(clock.now),
    )
    assert response.json() == {"items": []}
    assert charged[1] - charged[0] == 60
    assert clock.sleeps == [60]


@pytest.mark.parametrize("headers,delay", [
    ({"Retry-After": "7"}, 7),
    ({"Retry-After": formatdate(1_800_000_025, usegmt=True)}, 25),
    ({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1800000015"}, 15),
    ({"Retry-After": "3", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1800000015"}, 15),
    ({"Retry-After": "invalid"}, 60),
])
def test_server_rate_limit_headers_are_honored(headers, delay):
    clock = Clock()
    responses = iter([GitHubResponse(429, headers, "limited"), GitHubResponse(200, {}, "{}")])
    transport(clock).request("/repos/org/repo", lambda: next(responses))
    assert sum(clock.sleeps) == delay


def test_regular_forbidden_fails_without_wait_or_retry():
    clock = Clock()
    calls = []

    def forbidden():
        calls.append(1)
        return GitHubResponse(403, {}, '{"message":"Resource not accessible by integration"}')

    with pytest.raises(GitHubTransportError) as caught:
        transport(clock).request("/repos/org/private", forbidden)
    assert caught.value.reason == "http"
    assert caught.value.response.status_code == 403
    assert calls == [1]
    assert clock.sleeps == []


def test_transient_http_and_network_failures_have_bounded_backoff():
    clock = Clock()
    outcomes = iter([requests.ConnectionError("disconnected"), GitHubResponse(502, {}, "gateway"), GitHubResponse(200, {}, "[]")])

    def send():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert transport(clock).request("/repos/org/repo", send).json() == []
    assert clock.sleeps == [1, 2]


@pytest.mark.parametrize("network", [False, True])
def test_transient_failure_stops_at_attempt_limit(network):
    clock = Clock()
    calls = []

    def send():
        calls.append(1)
        if network:
            raise requests.Timeout("timeout")
        return GitHubResponse(503, {}, "unavailable")

    with pytest.raises(GitHubTransportError) as caught:
        transport(clock).request("/repos/org/repo", send)
    assert caught.value.reason == "transient"
    assert len(calls) == 3
    assert clock.sleeps == [1, 2]


def test_repeated_throttling_stops_future_reads_instead_of_waiting_per_author():
    clock = Clock()
    policy = transport(clock)
    calls = []

    def limited():
        calls.append(clock.now)
        return GitHubResponse(403, {}, "secondary rate limit")

    for _ in range(11):
        with pytest.raises(GitHubTransportError, match="GitHub rate limit"):
            policy.request("/search/issues", limited)
    assert len(calls) == 3
    assert sum(clock.sleeps) == 180
    assert max(clock.sleeps) <= 60


def test_cooldown_exceeding_wait_budget_is_preserved_without_early_retry(tmp_path):
    clock = Clock()
    state = tmp_path / "state.json"
    calls = []

    def limited():
        calls.append(1)
        return GitHubResponse(429, {"Retry-After": "3600"}, "limited")

    with pytest.raises(GitHubTransportError, match="wait budget"):
        transport(clock, state_path=state).request("/search/issues", limited)
    with pytest.raises(GitHubTransportError, match="wait budget"):
        transport(clock, state_path=state).request("/repos/org/repo", limited)
    assert calls == [1]
    assert clock.sleeps == []
    assert json.loads(state.read_text())["cooldown_until"] == clock.now + 3600


def test_search_pacing_is_shared_by_independent_collectors(tmp_path):
    clock = Clock()
    state = tmp_path / "timing.json"
    starts = []

    def send():
        starts.append(clock.now)
        return GitHubResponse(200, {}, "{}")

    first = transport(clock, state_path=state)
    second = transport(clock, state_path=state)
    first.request("/search/issues?q=first", send)
    second.request("https://api.github.com/search/issues?q=second", send)
    first.request("/search/issues?q=third", send)
    assert [start - starts[0] for start in starts] == pytest.approx([0, 2.1, 4.2])
    timing = json.loads(state.read_text())
    assert set(timing) == {"search_not_before", "cooldown_until"}
    assert all(isinstance(value, (int, float)) for value in timing.values())
    assert state.stat().st_size < 256


def test_terminal_rate_limit_cooldown_is_shared_with_next_collector(tmp_path):
    clock = Clock()
    state = tmp_path / "timing.json"
    first = transport(clock, state_path=state, max_attempts=1)
    with pytest.raises(GitHubTransportError):
        first.request("/search/issues", lambda: GitHubResponse(403, {"Retry-After": "9"}, "secret response body"))
    second = transport(clock, state_path=state)
    second.request("/repos/org/repo", lambda: GitHubResponse(200, {}, "{}"))
    assert clock.sleeps == [9]
    assert "secret" not in state.read_text()


def test_success_exhausting_primary_bucket_delays_next_collector(tmp_path):
    clock = Clock()
    state = tmp_path / "timing.json"
    transport(clock, state_path=state).request(
        "/repos/org/repo", lambda: GitHubResponse(200, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(clock.now + 8)}, "{}")
    )
    transport(clock, state_path=state).request("/search/issues", lambda: GitHubResponse(200, {}, "{}"))
    assert clock.sleeps == [8]


def test_corrupt_shared_state_fails_before_network(tmp_path):
    state = tmp_path / "timing.json"
    state.write_text('{"cooldown_until": -1}')
    with pytest.raises(GitHubTransportError, match="timing state is invalid"):
        transport(Clock(), state_path=state).request("/search/issues", lambda: pytest.fail("unexpected request"))


def test_before_request_can_deny_a_transport_retry():
    clock = Clock()
    calls = []
    charged = []

    def reserve():
        if charged:
            raise RuntimeError("physical request budget exhausted")
        charged.append(1)

    def send():
        calls.append(1)
        return GitHubResponse(502, {}, "gateway")

    with pytest.raises(RuntimeError, match="physical request budget"):
        transport(clock).request("/search/issues", send, before_request=reserve)
    assert calls == charged == [1]
