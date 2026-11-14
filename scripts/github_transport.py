"""Bounded GitHub read retries and shared Search pacing.

Only fixed timing fields are persisted in the runner's temporary directory;
credentials, request URLs, and response bodies never enter the shared state.
Policy follows https://docs.github.com/rest/guides/best-practices-for-integrators.
Callers remain responsible for authorizing reads and bounding pagination.
"""

from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit

import requests


log = logging.getLogger(__name__)
_STATE_FIELDS = ("search_not_before", "cooldown_until")


@dataclass(frozen=True)
class GitHubResponse:
    status_code: int
    headers: Mapping[str, str]
    text: str

    def json(self):
        return json.loads(self.text)


class GitHubTransportError(requests.RequestException):
    """A failed read whose response must never be treated as empty data."""

    def __init__(self, message: str, *, reason: str, response=None):
        super().__init__(message, response=response)
        self.reason = reason


def _number(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _rate_limited(response: GitHubResponse) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    headers = {str(key).lower(): value for key, value in response.headers.items()}
    message = response.text.lower()
    return (
        "retry-after" in headers
        or str(headers.get("x-ratelimit-remaining")) == "0"
        or any(part in message for part in ("rate limit", "rate-limit", "ratelimit", "abuse detection"))
    )


def _rate_delay(response: GitHubResponse, now: float, attempt: int) -> float:
    headers = {str(key).lower(): value for key, value in response.headers.items()}
    delays = []
    raw_retry = headers.get("retry-after")
    if raw_retry is not None:
        delay = _number(raw_retry)
        if delay is None:
            try:
                delay = max(0.0, parsedate_to_datetime(str(raw_retry)).timestamp() - now)
            except (TypeError, ValueError, OverflowError):
                pass
        if delay is not None:
            delays.append(delay)
    if str(headers.get("x-ratelimit-remaining")) == "0":
        reset = _number(headers.get("x-ratelimit-reset"))
        if reset is not None:
            delays.append(max(0.0, reset - now))
    return max(1.0, *delays) if delays else 60.0 * 2 ** (attempt - 1)


class GitHubTransport:
    """Serial read policy with three attempts and a total 180-second wait cap.

    Search starts are separated by 2.1 seconds, including across collectors
    sharing GITHUB_REQUEST_STATE_FILE. An exhausted rate-limit response stops
    this transport for the rest of the collection, instead of retrying every
    author independently. Server cooldowns longer than the wait budget are
    recorded but never shortened to force a request through.
    """

    def __init__(
        self,
        *,
        state_path: Path | str | None = None,
        max_attempts: int = 3,
        max_wait_seconds: float = 180,
        search_interval: float = 2.1,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if max_attempts < 1 or max_wait_seconds < 0 or search_interval < 0:
            raise ValueError("GitHub retry, wait, and pacing limits must be nonnegative")
        configured = state_path if state_path is not None else os.getenv("GITHUB_REQUEST_STATE_FILE")
        self.state_path = Path(configured) if configured else None
        self.max_attempts = max_attempts
        self.max_wait_seconds = max_wait_seconds
        self.search_interval = search_interval
        self.clock = clock
        self.sleep = sleep
        self.waited_seconds = 0.0
        self._memory_state = {key: 0.0 for key in _STATE_FIELDS}
        self._blocked_error: GitHubTransportError | None = None
        self._last_rate_response = None

    @contextmanager
    def _state(self):
        if self.state_path is None:
            yield self._memory_state
            return
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(path.suffix + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = {key: 0.0 for key in _STATE_FIELDS}
            if path.exists():
                try:
                    if path.stat().st_size > 4096:
                        raise ValueError("shared timing state exceeds its size bound")
                    raw = json.loads(path.read_text())
                    if not isinstance(raw, dict) or set(raw) != set(_STATE_FIELDS):
                        raise ValueError("shared timing state has unexpected fields")
                    for key in _STATE_FIELDS:
                        value = _number(raw[key])
                        if value is None:
                            raise ValueError("shared timing state has an invalid timestamp")
                        state[key] = value
                except (OSError, ValueError) as exc:
                    raise GitHubTransportError(
                        "GitHub shared timing state is invalid; refusing requests",
                        reason="state",
                    ) from exc
            yield state
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump(state, handle, sort_keys=True)
                    handle.write("\n")
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def _stop_rate_limit(self, message: str):
        self._blocked_error = GitHubTransportError(
            "GitHub rate limit: " + message,
            reason="rate-limit",
            response=self._last_rate_response,
        )
        raise self._blocked_error

    def _wait(self, seconds: float, *, rate_limit: bool):
        if seconds <= 0:
            return
        if self.waited_seconds + seconds > self.max_wait_seconds + 1e-6:
            if rate_limit:
                self._stop_rate_limit("shared cooldown exceeds the collection wait budget")
            raise GitHubTransportError("GitHub read wait budget exhausted", reason="transient")
        self.waited_seconds += seconds
        # Keep each blocking sleep bounded while honoring longer server delays.
        remaining = seconds
        while remaining > 0:
            interval = min(remaining, 60.0)
            self.sleep(interval)
            remaining -= interval

    def _before_request(self, url: str, before_request):
        search = urlsplit(url).path.startswith("/search/")
        while True:
            with self._state() as state:
                now = self.clock()
                cooldown = state["cooldown_until"]
                ready = max(cooldown, state["search_not_before"] if search else 0)
                delay = max(0.0, ready - now)
                if not delay:
                    if before_request is not None:
                        before_request()
                    if search:
                        state["search_not_before"] = now + self.search_interval
                    return
            self._wait(delay, rate_limit=cooldown > now)

    def _cooldown(self, until: float):
        with self._state() as state:
            state["cooldown_until"] = max(state["cooldown_until"], until)

    def request(self, url: str, send, *, before_request=None) -> GitHubResponse:
        if self._blocked_error is not None:
            raise self._blocked_error
        for attempt in range(1, self.max_attempts + 1):
            self._before_request(url, before_request)
            try:
                response = send()
            except requests.RequestException as exc:
                if attempt == self.max_attempts:
                    raise GitHubTransportError(
                        "GitHub network read failed after bounded retries", reason="transient"
                    ) from exc
                self._wait(2.0 ** (attempt - 1), rate_limit=False)
                continue
            if _rate_limited(response):
                self._last_rate_response = response
                delay = _rate_delay(response, self.clock(), attempt)
                self._cooldown(self.clock() + delay)
                if attempt == self.max_attempts:
                    self._stop_rate_limit(f"HTTP {response.status_code}; bounded retries exhausted")
                log.warning("GitHub rate limit HTTP %s; respecting a %.1fs cooldown", response.status_code, delay)
                continue
            if 500 <= response.status_code <= 599:
                if attempt < self.max_attempts:
                    self._wait(2.0 ** (attempt - 1), rate_limit=False)
                    continue
                raise GitHubTransportError(
                    f"GitHub HTTP {response.status_code}; bounded retries exhausted",
                    reason="transient", response=response,
                )
            if not 200 <= response.status_code < 400:
                raise GitHubTransportError(
                    f"GitHub HTTP {response.status_code}", reason="http", response=response
                )
            headers = {str(key).lower(): value for key, value in response.headers.items()}
            if str(headers.get("x-ratelimit-remaining")) == "0":
                self._cooldown(self.clock() + _rate_delay(response, self.clock(), attempt))
            return response
        raise AssertionError("bounded GitHub transport loop exhausted unexpectedly")
