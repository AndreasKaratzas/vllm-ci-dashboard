#!/usr/bin/env python3
"""Collect privacy-minimized DNS failure evidence from all AMD GPU CI jobs.

The collector deliberately persists only fixed enums, timestamps, Buildkite
identifiers, and safe queue/node coordinates. Complete logs are bounded and
classified in memory; raw content, URLs, response headers, and environment
values are never written or logged.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterable

import requests

# Make ``vllm`` importable when this file is invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.ci.dns_failures import (  # noqa: E402
    MAX_LOG_BYTES,
    PIPELINES,
    RETENTION_HOURS,
    StateValidationError,
    build_public_output,
    canonical_uuid,
    classify_dns_log,
    empty_state,
    iso_timestamp,
    load_state,
    merge_state_jobs,
    oversize_record,
    parse_timestamp,
    pending_record,
    prune_state_jobs,
    queue_hardware,
    scan_record,
    sort_state_jobs,
    state_from_bytes,
    unavailable_record,
    validate_state,
    write_public_output,
    write_state,
)


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_STATE = ROOT / "data" / "vllm" / "ci" / "dns_health" / "scan_state.json.gz"
DEFAULT_OUTPUT = ROOT / "data" / "vllm" / "ci" / "dns_failures.json"
STATE_GIT_PATH = "data/vllm/ci/dns_health/scan_state.json.gz"
BUILDKITE_API = "https://api.buildkite.com/v2"
BUILDKITE_ORGANIZATION = "vllm"
DEFAULT_DISCOVER_DAYS = 30
DEFAULT_MAX_LOGS = 500
DEFAULT_TIME_BUDGET_SECONDS = 0
FINALIZATION_RESERVE_SECONDS = 30
BOOTSTRAP_DISCOVERY_HOURS = 24
INCREMENTAL_DISCOVERY_OVERLAP_HOURS = 2
MAX_INCREMENTAL_DISCOVERY_GAP_HOURS = 24
MAX_DISCOVER_DAYS = RETENTION_HOURS // 24
MAX_DISCOVERY_PAGES = 1000
PAGE_SIZE = 100
MAX_REQUEST_ATTEMPTS = 5
MAX_RETRY_SLEEP_SECONDS = 60
REQUEST_TIMEOUT = (15, 120)
RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504, 520, 522, 524})
REQUESTS_PER_MINUTE = 30
REQUEST_INTERVAL_SECONDS = 60 / REQUESTS_PER_MINUTE
# Production logs average about eleven seconds per response. Eight workers are
# enough to hide that latency behind the unchanged two-second admission pace,
# while bounding raw in-flight log content to 128 MiB on the Actions runner.
MAX_CONCURRENT_LOG_FETCHES = 8
MAX_IN_FLIGHT_RAW_LOG_BYTES = MAX_CONCURRENT_LOG_FETCHES * MAX_LOG_BYTES
SHARED_QUOTA_RESERVE = 10
ACTIVE_DISCOVERY_SLICE_HOURS = 24
MAX_CONCURRENT_ACTIVE_SLICES = 3
ACTIVE_BUILD_STATES = (
    "creating",
    "scheduled",
    "running",
    "failing",
    "blocked",
    "canceling",
)

_QUEUE_RULE_RE = re.compile(r"^queue=(.+)$", re.IGNORECASE)
_SAFE_COORD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PUBLIC_HOST_COORD_RE = re.compile(
    r"(?:[A-Za-z0-9-]+\.)+(?:com|org|net|io|ai|co)$",
    re.IGNORECASE,
)
_NODE_BANNER_RE = re.compile(
    r"Pod:\s*\S+\s*\|\s*Node:\s*([A-Za-z0-9][A-Za-z0-9._-]{0,127})",
    re.IGNORECASE,
)
_SAFE_GIT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")

log = logging.getLogger("dns-health")


class CollectionError(RuntimeError):
    """A bounded collection operation could not complete safely."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class LogUnavailable(CollectionError):
    """A complete job log was not available after bounded retries."""


class BudgetExhausted(CollectionError):
    """The monotonic collection deadline cannot accommodate more I/O."""

    def __init__(self) -> None:
        super().__init__("budget_exhausted")


class OversizeLog(CollectionError):
    """A job log exceeded the configured full-log byte ceiling."""

    def __init__(self, log_bytes: int):
        super().__init__("oversize")
        self.log_bytes = max(MAX_LOG_BYTES + 1, int(log_bytes))


def retry_after_seconds(
    value: object,
    *,
    now: datetime | None = None,
    fallback: int = 1,
) -> int:
    """Parse numeric or HTTP-date Retry-After into a bounded delay."""
    delay: float
    text = str(value or "").strip()
    try:
        delay = float(text)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            reference = now or datetime.now(timezone.utc)
            delay = (parsed.astimezone(timezone.utc) - reference).total_seconds()
        except (TypeError, ValueError, OverflowError):
            delay = float(fallback)
    return min(MAX_RETRY_SLEEP_SECONDS, max(0, int(delay + 0.999)))


def rate_limit_reset_seconds(
    headers: object,
    *,
    now: datetime | None = None,
) -> int:
    """Return the longest advertised account/user reset delay."""
    if not hasattr(headers, "get"):
        return 0
    reference = now or datetime.now(timezone.utc)
    waits: list[float] = []
    for name in ("RateLimit-Reset", "RateLimit-User-Reset"):
        try:
            value = float(headers.get(name, ""))
        except (TypeError, ValueError):
            continue
        if value > 1_000_000_000:
            value -= reference.timestamp()
        waits.append(max(0, value) + 1)
    return min(MAX_RETRY_SLEEP_SECONDS, int(max(waits, default=0) + 0.999))


def _unavailable_reason(status_code: int) -> str:
    if status_code == 401:
        return "authentication"
    if status_code == 403:
        return "forbidden"
    if status_code == 404:
        return "not_found"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "server_error"
    return "invalid_response"


class BuildkiteClient:
    """Small Buildkite client that never exposes response content in errors."""

    def __init__(
        self,
        token: str,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not token:
            raise CollectionError("authentication")
        self._authorization = {"Authorization": f"Bearer {token}"}
        self._injected_session = session
        self._thread_sessions = threading.local()
        # Preserve the public attribute used by callers that inject a session,
        # while giving each production worker its own requests.Session. Requests
        # sessions are not a safe place to share mutable connection/cookie state
        # across concurrent log downloads.
        self.session = session or self._new_session()
        if session is not None:
            self.session.headers.update(self._authorization)
        else:
            self._thread_sessions.session = self.session
        self.sleep = sleep
        self.monotonic = monotonic
        self._next_request_at = 0.0
        self._quota_blocked_until = 0.0
        # Only one thread may wait for/admit the next request at a time. Quota
        # observations use a separate lock so a response can extend the block
        # while the admission thread is sleeping.
        self._admission_lock = threading.Lock()
        self._quota_lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self._request_starts = {"build_page": 0, "job_log": 0}
        self._metrics = {
            "pacing_wait_seconds": 0.0,
            "quota_wait_seconds": 0.0,
            "retry_sleep_worker_seconds": 0.0,
            "network_worker_seconds": 0.0,
            "network_max_seconds": 0.0,
            "stream_worker_seconds": 0.0,
            "stream_max_seconds": 0.0,
            "retry_requests": 0,
            "rate_limited_responses": 0,
        }

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(self._authorization)
        return session

    def _request_session(self) -> requests.Session:
        if self._injected_session is not None:
            return self._injected_session
        session = getattr(self._thread_sessions, "session", None)
        if session is None:
            session = self._new_session()
            self._thread_sessions.session = session
        return session

    def _sleep_with_deadline(self, seconds: float, deadline: float | None) -> None:
        wait = max(0.0, seconds)
        remaining = deadline - self.monotonic() if deadline is not None else None
        if remaining is not None and wait >= remaining:
            raise BudgetExhausted()
        if wait:
            self.sleep(wait)

    def _add_metric(self, name: str, value: float = 1.0) -> None:
        with self._metrics_lock:
            self._metrics[name] += value

    def _observe_network_seconds(self, started_at: float) -> None:
        elapsed = max(0.0, self.monotonic() - started_at)
        with self._metrics_lock:
            self._metrics["network_worker_seconds"] += elapsed
            self._metrics["network_max_seconds"] = max(
                self._metrics["network_max_seconds"],
                elapsed,
            )

    def _throttle(self, deadline: float | None) -> None:
        with self._admission_lock:
            while True:
                with self._quota_lock:
                    now = self.monotonic()
                    next_request_at = self._next_request_at
                    quota_blocked_until = self._quota_blocked_until
                    ready_at = max(next_request_at, quota_blocked_until)
                wait = max(0.0, ready_at - now)
                wait_metric = (
                    "quota_wait_seconds"
                    if quota_blocked_until > next_request_at
                    else "pacing_wait_seconds"
                )
                wait_started = self.monotonic()
                self._sleep_with_deadline(wait, deadline)
                if wait:
                    self._add_metric(
                        wait_metric,
                        max(0.0, self.monotonic() - wait_started),
                    )
                # Recheck the adaptive block after sleeping. A concurrent
                # response may have advertised a near-empty shared quota while
                # this admission was waiting for the ordinary two-second pace.
                with self._quota_lock:
                    observed_at = self.monotonic()
                    logical_now = max(observed_at, ready_at)
                    if self._quota_blocked_until > logical_now:
                        continue
                    if deadline is not None and logical_now >= deadline:
                        raise BudgetExhausted()
                    # The logical floor keeps deterministic/no-op test clocks
                    # from turning concurrent callers into a burst.
                    self._next_request_at = logical_now + REQUEST_INTERVAL_SECONDS
                    return

    def _observe_quota_headers(self, headers: object) -> None:
        if not hasattr(headers, "get"):
            return
        remaining_values: list[int] = []
        for name in ("RateLimit-Remaining", "RateLimit-User-Remaining"):
            try:
                remaining_values.append(int(float(headers.get(name, ""))))
            except (TypeError, ValueError):
                continue
        if remaining_values and min(remaining_values) <= SHARED_QUOTA_RESERVE:
            reset_wait = rate_limit_reset_seconds(headers)
            if reset_wait:
                self._block_requests_for(reset_wait)

    def _block_requests_for(self, seconds: float) -> None:
        """Publish one response's backoff to every concurrent requester."""
        wait = max(0.0, seconds)
        if not wait:
            return
        with self._quota_lock:
            self._quota_blocked_until = max(
                self._quota_blocked_until,
                self.monotonic() + wait,
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        stream: bool = False,
        accept: str = "application/json",
        deadline: float | None = None,
    ) -> requests.Response:
        url = f"{BUILDKITE_API}{path}"
        request_kind = "job_log" if path.endswith("/log") else "build_page"
        for attempt in range(MAX_REQUEST_ATTEMPTS):
            self._throttle(deadline)
            remaining = deadline - self.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise BudgetExhausted()
            timeout: tuple[float, float]
            if remaining is None:
                timeout = REQUEST_TIMEOUT
            else:
                # Allocate each socket phase no more than half the remaining
                # wall budget; streamed bodies are checked again per chunk.
                phase = max(0.25, remaining / 2)
                timeout = (
                    min(REQUEST_TIMEOUT[0], phase),
                    min(REQUEST_TIMEOUT[1], phase),
                )
            try:
                with self._metrics_lock:
                    self._request_starts[request_kind] += 1
                if attempt:
                    self._add_metric("retry_requests")
                network_started = self.monotonic()
                response = self._request_session().request(
                    method,
                    url,
                    params=params,
                    headers={"Accept": accept},
                    timeout=timeout,
                    stream=stream,
                )
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
            ):
                self._observe_network_seconds(network_started)
                if attempt + 1 == MAX_REQUEST_ATTEMPTS:
                    raise CollectionError("network_error") from None
                wait = min(MAX_RETRY_SLEEP_SECONDS, 2**attempt)
                try:
                    retry_started = self.monotonic()
                    self._sleep_with_deadline(wait, deadline)
                    self._add_metric(
                        "retry_sleep_worker_seconds",
                        max(0.0, self.monotonic() - retry_started),
                    )
                except BudgetExhausted:
                    raise BudgetExhausted() from None
                continue
            self._observe_network_seconds(network_started)

            self._observe_quota_headers(response.headers)
            if response.status_code in RETRYABLE_HTTP_STATUSES:
                if response.status_code == 429:
                    self._add_metric("rate_limited_responses")
                if attempt + 1 == MAX_REQUEST_ATTEMPTS:
                    reason = _unavailable_reason(response.status_code)
                    response.close()
                    raise CollectionError(reason)
                wait = retry_after_seconds(
                    response.headers.get("Retry-After"),
                    fallback=2**attempt,
                )
                wait = max(wait, rate_limit_reset_seconds(response.headers))
                # A 429/5xx Retry-After describes the shared endpoint, not just
                # this worker. Publish it before sleeping so the other two log
                # workers cannot consume quota throughout the backoff window.
                self._block_requests_for(wait)
                response.close()
                retry_started = self.monotonic()
                self._sleep_with_deadline(wait, deadline)
                self._add_metric(
                    "retry_sleep_worker_seconds",
                    max(0.0, self.monotonic() - retry_started),
                )
                continue
            if response.status_code < 200 or response.status_code >= 300:
                reason = _unavailable_reason(response.status_code)
                response.close()
                raise CollectionError(reason)
            return response
        raise CollectionError("network_error")

    def request_starts(self) -> dict[str, int]:
        """Return privacy-safe request counters for phase diagnostics."""
        with self._metrics_lock:
            return dict(self._request_starts)

    def telemetry(self) -> dict[str, int | float]:
        """Return aggregate numeric I/O metrics without response metadata."""
        with self._metrics_lock:
            return {**self._request_starts, **self._metrics}

    def build_page(
        self,
        pipeline: str,
        *,
        filters: dict,
        page: int,
        deadline: float | None = None,
    ) -> list[dict]:
        path = (
            f"/organizations/{BUILDKITE_ORGANIZATION}/pipelines/"
            f"{pipeline}/builds"
        )
        params = dict(filters)
        if "branch" in params or not params:
            raise CollectionError("invalid_response")
        params.update(
            {
                "per_page": PAGE_SIZE,
                "page": page,
                "include_retried_jobs": "true",
                "exclude_pipeline": "true",
            }
        )
        response = self._request(
            "GET",
            path,
            params=params,
            deadline=deadline,
        )
        try:
            payload = response.json()
        except (requests.exceptions.JSONDecodeError, json.JSONDecodeError, ValueError):
            raise CollectionError("invalid_response") from None
        finally:
            response.close()
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise CollectionError("invalid_response")
        return payload

    def _paginate_builds(
        self,
        pipeline: str,
        *,
        filters: dict,
        deadline: float | None = None,
    ) -> list[dict]:
        builds: list[dict] = []
        seen_numbers: set[int] = set()
        for page in range(1, MAX_DISCOVERY_PAGES + 1):
            batch = self.build_page(
                pipeline,
                filters=filters,
                page=page,
                deadline=deadline,
            )
            for build in batch:
                number = build.get("number")
                if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
                    raise CollectionError("invalid_response")
                if number not in seen_numbers:
                    seen_numbers.add(number)
                    builds.append(build)
            if len(batch) < PAGE_SIZE:
                return builds
        raise CollectionError("invalid_response")

    def _active_builds(
        self,
        pipeline: str,
        *,
        created_from: str,
        created_to: str,
        deadline: float | None,
    ) -> list[dict]:
        """Fetch the active horizon in bounded, concurrent UTC slices."""
        start = parse_timestamp(created_from, "active_created_from")
        end = parse_timestamp(created_to, "active_created_to")
        if start >= end:
            raise CollectionError("invalid_response")

        slices: list[tuple[datetime, datetime]] = []
        cursor = end
        width = timedelta(hours=ACTIVE_DISCOVERY_SLICE_HOURS)
        while cursor > start:
            slice_start = max(start, cursor - width)
            slices.append((slice_start, cursor))
            cursor = slice_start
        # Buildkite lists builds newest-first. Process newest slices first and
        # merge in this stable order so slicing does not change determinism.

        results: dict[int, list[dict]] = {}
        in_flight: dict[Future[list[dict]], int] = {}
        next_index = 0

        def submit_available(pool: ThreadPoolExecutor) -> None:
            nonlocal next_index
            while (
                next_index < len(slices)
                and len(in_flight) < MAX_CONCURRENT_ACTIVE_SLICES
            ):
                slice_start, slice_end = slices[next_index]
                index = next_index
                next_index += 1
                in_flight[
                    pool.submit(
                        self._active_slice_builds,
                        pipeline,
                        created_from=slice_start,
                        created_to=slice_end,
                        deadline=deadline,
                    )
                ] = index

        with ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_ACTIVE_SLICES,
            thread_name_prefix="dns-active-slice",
        ) as pool:
            submit_available(pool)
            while in_flight:
                done, _ = wait(tuple(in_flight), return_when=FIRST_COMPLETED)
                for future in sorted(done, key=in_flight.__getitem__):
                    index = in_flight.pop(future)
                    results[index] = future.result()
                submit_available(pool)

        merged: dict[int, dict] = {}
        for index in range(len(slices)):
            for build in results[index]:
                merged.setdefault(build["number"], build)
        return list(merged.values())

    def _active_slice_builds(
        self,
        pipeline: str,
        *,
        created_from: datetime,
        created_to: datetime,
        deadline: float | None,
    ) -> list[dict]:
        """Fetch one time slice without mutable page-number offsets.

        A full first page is ambiguous: it may have more rows behind it, or
        exactly fill the page. Split that interval in half and refetch both
        children until every accepted leaf fits in one response. This avoids
        losing an unrelated active build when another build leaves page one
        before a later numbered page is read.
        """
        pending = [(created_from, created_to)]
        builds: list[dict] = []
        seen_numbers: set[int] = set()
        pages = 0

        while pending:
            if pages >= MAX_DISCOVERY_PAGES:
                raise CollectionError("invalid_response")
            range_start, range_end = pending.pop()
            batch = self.build_page(
                pipeline,
                filters={
                    "state[]": list(ACTIVE_BUILD_STATES),
                    "created_from": iso_timestamp(range_start),
                    "created_to": iso_timestamp(range_end),
                },
                page=1,
                deadline=deadline,
            )
            pages += 1
            if len(batch) > PAGE_SIZE:
                raise CollectionError("invalid_response")
            validated_batch: list[tuple[int, dict]] = []
            for build in batch:
                number = build.get("number")
                if (
                    isinstance(number, bool)
                    or not isinstance(number, int)
                    or number <= 0
                ):
                    raise CollectionError("invalid_response")
                validated_batch.append((number, build))
            if len(batch) == PAGE_SIZE:
                midpoint = range_start + (range_end - range_start) / 2
                if midpoint <= range_start or midpoint >= range_end:
                    raise CollectionError("invalid_response")
                # LIFO: query the newer half first to preserve API ordering.
                pending.append((range_start, midpoint))
                pending.append((midpoint, range_end))
                continue
            for number, build in validated_batch:
                if number not in seen_numbers:
                    seen_numbers.add(number)
                    builds.append(build)
        return builds

    def discover_builds(
        self,
        pipeline: str,
        *,
        finished_from: str,
        active_created_from: str | None = None,
        active_created_to: str | None = None,
        deadline: float | None = None,
    ) -> list[dict]:
        """Union recent active builds with all builds finished in the window.

        Querying active states first and the unbounded-upper finished cohort
        second closes state transitions during pagination: a build that
        finishes between legs remains present in at least one cohort. Bound
        the active leg to the caller's target parent-build horizon so a
        unbounded historical backlog cannot consume the entire collection
        budget, without shrinking it to the incremental finished overlap.
        """
        active_started = self.monotonic()
        active_requests_before = self.request_starts()["build_page"]
        active_from = active_created_from or finished_from
        active = (
            self._active_builds(
                pipeline,
                created_from=active_from,
                created_to=active_created_to,
                deadline=deadline,
            )
            if active_created_to is not None
            else self._paginate_builds(
                pipeline,
                filters={
                    "state[]": list(ACTIVE_BUILD_STATES),
                    "created_from": active_from,
                },
                deadline=deadline,
            )
        )
        active_requests = self.request_starts()["build_page"] - active_requests_before
        log.info(
            "DNS discovery cohort complete: pipeline=%s cohort=active "
            "elapsed_seconds=%.3f builds=%d build_page_requests=%d",
            pipeline,
            max(0.0, self.monotonic() - active_started),
            len(active),
            active_requests,
        )
        finished_started = self.monotonic()
        finished_requests_before = self.request_starts()["build_page"]
        finished = self._paginate_builds(
            pipeline,
            filters={"finished_from": finished_from},
            deadline=deadline,
        )
        finished_requests = (
            self.request_starts()["build_page"] - finished_requests_before
        )
        log.info(
            "DNS discovery cohort complete: pipeline=%s cohort=finished "
            "elapsed_seconds=%.3f builds=%d build_page_requests=%d",
            pipeline,
            max(0.0, self.monotonic() - finished_started),
            len(finished),
            finished_requests,
        )
        merged: dict[int, dict] = {}
        for build in (*active, *finished):
            merged[build["number"]] = build
        return list(merged.values())

    def fetch_job_log(
        self,
        metadata: dict,
        *,
        deadline: float | None = None,
    ) -> tuple[str, int]:
        """Download one complete log into bounded memory and return text/bytes."""
        path = (
            f"/organizations/{BUILDKITE_ORGANIZATION}/pipelines/"
            f"{metadata['pipeline']}/builds/{metadata['build_number']}/jobs/"
            f"{metadata['job_id']}/log"
        )
        try:
            response = self._request(
                "GET",
                path,
                stream=True,
                accept="text/plain",
                deadline=deadline,
            )
        except BudgetExhausted:
            raise
        except CollectionError as exc:
            raise LogUnavailable(exc.reason) from None

        try:
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_bytes = int(content_length)
                except (TypeError, ValueError):
                    raise LogUnavailable("invalid_response") from None
                if declared_bytes > MAX_LOG_BYTES:
                    raise OversizeLog(declared_bytes)

            chunks: list[bytes] = []
            received = 0
            stream_started = self.monotonic()
            try:
                iterator = response.iter_content(chunk_size=64 * 1024)
                for chunk in iterator:
                    if deadline is not None and self.monotonic() >= deadline:
                        raise BudgetExhausted()
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > MAX_LOG_BYTES:
                        raise OversizeLog(received)
                    chunks.append(chunk)
            except OversizeLog:
                raise
            except BudgetExhausted:
                raise
            except requests.exceptions.RequestException:
                raise LogUnavailable("network_error") from None
            finally:
                stream_elapsed = max(0.0, self.monotonic() - stream_started)
                with self._metrics_lock:
                    self._metrics["stream_worker_seconds"] += stream_elapsed
                    self._metrics["stream_max_seconds"] = max(
                        self._metrics["stream_max_seconds"],
                        stream_elapsed,
                    )
            body = b"".join(chunks)
        finally:
            response.close()

        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = body.decode("utf-8", errors="replace")

        content_type = str(response.headers.get("Content-Type") or "").casefold()
        if "json" in content_type or text.lstrip().startswith("{"):
            try:
                envelope = json.loads(text)
            except json.JSONDecodeError:
                raise LogUnavailable("invalid_response") from None
            content = envelope.get("content") if isinstance(envelope, dict) else None
            if not isinstance(content, str):
                raise LogUnavailable("invalid_response")
            content_bytes = len(content.encode("utf-8"))
            if content_bytes > MAX_LOG_BYTES:
                raise OversizeLog(content_bytes)
            return content, content_bytes
        if content_type and "text/plain" not in content_type:
            raise LogUnavailable("invalid_response")
        return text, len(body)


def _queue_of(job: dict) -> str:
    for rule in job.get("agent_query_rules") or []:
        match = _QUEUE_RULE_RE.match(str(rule).strip())
        if match:
            return match.group(1).strip().casefold()
    for tag in (job.get("agent") or {}).get("meta_data") or []:
        if isinstance(tag, str) and tag.casefold().startswith("queue="):
            return tag.split("=", 1)[1].strip().casefold()
    return ""


def _node_of(job: dict) -> str:
    for tag in (job.get("agent") or {}).get("meta_data") or []:
        if isinstance(tag, str) and tag.casefold().startswith("k8s:node="):
            candidate = tag.split("=", 1)[1].strip()
            return (
                candidate
                if _SAFE_COORD_RE.fullmatch(candidate)
                and not _PUBLIC_HOST_COORD_RE.search(candidate)
                else "unidentified"
            )
    return "unidentified"


def _node_from_log(log_text: str) -> str:
    match = _NODE_BANNER_RE.search(log_text)
    if not match:
        return ""
    candidate = match.group(1)
    if _PUBLIC_HOST_COORD_RE.search(candidate):
        return ""
    return candidate


def _job_state(job: dict) -> str:
    state = str(job.get("state") or "").casefold()
    if job.get("soft_failed") or state in {"soft_failed", "soft_fail"}:
        return "soft"
    if state == "passed":
        return "passed"
    if state in {"failed", "timed_out", "broken", "expired"}:
        return "hard"
    return ""


def _normalized_timestamp(value: object, field: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        # Keep the durable/public contract canonical at whole-second UTC. This
        # also makes a missing-start-time fallback episode equal to the job's
        # normalized finish time when Buildkite supplies fractional seconds.
        return iso_timestamp(parse_timestamp(value, field).replace(microsecond=0))
    except StateValidationError:
        return None


def job_metadata(pipeline: str, build: dict, job: dict) -> dict | None:
    """Return the safe state metadata for one eligible AMD GPU script job."""
    if pipeline not in PIPELINES or job.get("type") != "script":
        return None
    state = _job_state(job)
    if not state:
        return None
    queue = _queue_of(job)
    hardware = queue_hardware(queue)
    if not hardware:
        return None
    if not _SAFE_COORD_RE.fullmatch(queue):
        raise CollectionError("invalid_response")
    build_number = build.get("number")
    if isinstance(build_number, bool) or not isinstance(build_number, int) or build_number <= 0:
        raise CollectionError("invalid_response")
    try:
        job_id = canonical_uuid(job.get("id"))
    except StateValidationError:
        raise CollectionError("invalid_response") from None
    finished_at = _normalized_timestamp(job.get("finished_at"), "finished_at")
    if finished_at is None:
        raise CollectionError("invalid_response")
    started_at = _normalized_timestamp(job.get("started_at"), "started_at")
    if (
        started_at is not None
        and parse_timestamp(started_at, "started_at")
        > parse_timestamp(finished_at, "finished_at")
    ):
        started_at = None
    return {
        "pipeline": pipeline,
        "build_number": build_number,
        "job_id": job_id,
        "queue": queue,
        "node": _node_of(job),
        "hardware": hardware,
        "state": state,
        "started_at": started_at,
        "finished_at": finished_at,
    }


def discover_job_metadata(builds_by_pipeline: dict[str, Iterable[dict]]) -> list[dict]:
    """Extract every distinct terminal AMD GPU script attempt, newest first."""
    discovered: dict[tuple[str, str], dict] = {}
    for pipeline in PIPELINES:
        for build in builds_by_pipeline.get(pipeline, []):
            if not isinstance(build, dict) or not isinstance(build.get("jobs"), list):
                raise CollectionError("invalid_response")
            for job in build["jobs"]:
                if not isinstance(job, dict):
                    raise CollectionError("invalid_response")
                metadata = job_metadata(pipeline, build, job)
                if metadata is not None:
                    discovered[(pipeline, metadata["job_id"])] = metadata
    return sorted(
        discovered.values(),
        key=lambda row: (
            parse_timestamp(row["finished_at"], "finished_at"),
            row["build_number"],
            row["job_id"],
        ),
        reverse=True,
    )


def _refresh_record(record: dict, metadata: dict) -> dict:
    refreshed = dict(record)
    for key in (
        "pipeline",
        "build_number",
        "job_id",
        "queue",
        "node",
        "hardware",
        "state",
        "started_at",
        "finished_at",
    ):
        if (
            key == "node"
            and metadata[key] == "unidentified"
            and record.get("node") != "unidentified"
        ):
            # A completed scan may have recovered the physical node from the
            # runner banner. Do not erase that durable attribution merely
            # because the compact build listing still lacks agent metadata.
            continue
        refreshed[key] = metadata[key]
    return refreshed


def _validate_git_ref(ref: str) -> str:
    if not _SAFE_GIT_REF_RE.fullmatch(ref) or ".." in ref or "//" in ref:
        raise StateValidationError("merge state git ref is invalid")
    return ref


def load_state_from_git_ref(ref: str, *, repo_root: Path = ROOT) -> dict:
    """Load sanitized durable state from an established ref, failing closed."""
    safe_ref = _validate_git_ref(ref)
    revision = f"{safe_ref}^{{commit}}"
    established = subprocess.run(
        ["git", "rev-parse", "--verify", "--end-of-options", revision],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if established.returncode != 0:
        raise StateValidationError("merge state git ref is not established")
    object_name = f"{safe_ref}:{STATE_GIT_PATH}"
    exists = subprocess.run(
        ["git", "cat-file", "-e", object_name],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if exists.returncode != 0:
        raise StateValidationError("merge state is missing from the established ref")
    loaded = subprocess.run(
        ["git", "show", object_name],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if loaded.returncode != 0:
        raise StateValidationError("merge state could not be read")
    return state_from_bytes(loaded.stdout)


def _prepare_records(
    old_rows: Iterable[dict],
    discovered: Iterable[dict],
    *,
    retention_start: datetime,
    end_exclusive: datetime,
) -> list[dict]:
    records = {
        (row["pipeline"], row["job_id"]): row
        for row in prune_state_jobs(old_rows, retention_start, end_exclusive)
    }
    for metadata in discovered:
        finished = parse_timestamp(metadata["finished_at"], "finished_at")
        if not retention_start <= finished < end_exclusive:
            continue
        identity = (metadata["pipeline"], metadata["job_id"])
        previous = records.get(identity)
        records[identity] = (
            _refresh_record(previous, metadata) if previous is not None else pending_record(metadata)
        )
    return sort_state_jobs(records.values())


def _discovery_window(
    prior_states: Iterable[dict],
    *,
    clock: datetime,
    target_start: datetime,
) -> tuple[datetime, datetime]:
    """Return the bounded query start and honestly contiguous coverage start.

    A first run intentionally bootstraps only one day, even though the durable
    retention target is thirty days. Later exhaustive queries overlap the most
    recent valid state and carry older coverage only across touching intervals.
    A stale state is still useful as retained evidence, but is not a safe basis
    for an unbounded catch-up query or a continuity claim.
    """
    bootstrap_start = max(
        target_start,
        clock - timedelta(hours=BOOTSTRAP_DISCOVERY_HOURS),
    )
    intervals: list[tuple[datetime, datetime]] = []
    for state in prior_states:
        generated_at = parse_timestamp(state["generated_at"], "generated_at")
        discovery_start = parse_timestamp(
            state["discovery"]["start"],
            "discovery.start",
        )
        if generated_at > clock:
            raise StateValidationError("prior state generated_at is in the future")
        intervals.append((max(target_start, discovery_start), generated_at))

    if not intervals:
        return bootstrap_start, bootstrap_start

    latest_end = max(end for _, end in intervals)
    max_gap = timedelta(hours=MAX_INCREMENTAL_DISCOVERY_GAP_HOURS)
    if clock - latest_end > max_gap:
        return bootstrap_start, bootstrap_start

    query_start = max(
        target_start,
        latest_end - timedelta(hours=INCREMENTAL_DISCOVERY_OVERLAP_HOURS),
    )
    coverage_start = query_start
    # Each validated prior state describes one exhaustive half-open interval.
    # Extend only through intervals touching the current query or one another;
    # disconnected older state may contribute rows, never a coverage claim.
    for start, end in sorted(intervals, key=lambda interval: interval[1], reverse=True):
        if end >= coverage_start and start < coverage_start:
            coverage_start = start
    return query_start, coverage_start


def _fair_pending_order(rows: Iterable[dict]) -> list[dict]:
    """Interleave oldest and newest pending rows, starting with backfill.

    The durable state is sorted newest-first. Always taking that prefix lets a
    sustained arrival rate monopolize the bounded request budget and strand
    older jobs. Alternating the two ends advances the oldest backlog on every
    other request while retaining equally frequent samples from fresh jobs.
    Starting at the old end also guarantees that a deadline-shortened run that
    completes at least one request still makes backfill progress.
    """
    ordered = list(rows)
    fair: list[dict] = []
    newest = 0
    oldest = len(ordered) - 1
    take_oldest = True
    while newest <= oldest:
        if take_oldest:
            fair.append(ordered[oldest])
            oldest -= 1
        else:
            fair.append(ordered[newest])
            newest += 1
        take_oldest = not take_oldest
    return fair


def _scan_candidate(
    row: dict,
    *,
    client: BuildkiteClient,
    attempted_at: str,
    deadline: float | None,
    monotonic: Callable[[], float],
    stop_event: threading.Event,
) -> dict:
    """Fetch and classify one log, returning only sanitized scanner state."""
    if stop_event.is_set() or (deadline is not None and monotonic() >= deadline):
        stop_event.set()
        raise BudgetExhausted()
    previous_attempts = int(row.get("attempts") or 0)
    try:
        log_text, _ = client.fetch_job_log(row, deadline=deadline)
        scan_metadata = row
        if row["node"] == "unidentified":
            recovered_node = _node_from_log(log_text)
            if recovered_node:
                scan_metadata = dict(row)
                scan_metadata["node"] = recovered_node
        classification = classify_dns_log(
            log_text,
            job_finished_at=row["finished_at"],
            job_started_at=row.get("started_at"),
        )
        return scan_record(
            scan_metadata,
            classification,
            attempted_at=attempted_at,
            previous_attempts=previous_attempts,
        )
    except OversizeLog as exc:
        return oversize_record(
            row,
            exc.log_bytes,
            attempted_at=attempted_at,
            previous_attempts=previous_attempts,
        )
    except LogUnavailable as exc:
        return unavailable_record(
            row,
            exc.reason,
            attempted_at=attempted_at,
            previous_attempts=previous_attempts,
        )
    except BudgetExhausted:
        # Wake every worker before this exception reaches the coordinator. A
        # task that has not started I/O yet will observe the shared stop flag.
        stop_event.set()
        raise


def scan_records(
    rows: Iterable[dict],
    *,
    client: BuildkiteClient,
    attempted_at: str,
    max_logs: int,
    deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> list[dict]:
    """Scan fairly ordered new backlog before bounded unavailable retries."""
    ordered = sort_state_jobs(rows)
    by_identity = {(row["pipeline"], row["job_id"]): row for row in ordered}
    pending = _fair_pending_order(
        row for row in ordered if row["status"] == "pending"
    )
    unavailable = [row for row in ordered if row["status"] == "unavailable"]
    candidates = (pending + unavailable)[:max_logs]
    if not candidates:
        return ordered

    # Keep only one task per worker in flight. This gives cancellation a hard
    # boundary: once any worker observes the shared deadline, no queued task can
    # wake later and start another request. Results are committed in candidate
    # order so network completion races never affect serialized state ordering.
    completed: dict[int, dict] = {}
    in_flight: dict[Future[dict], int] = {}
    next_index = 0
    budget_exhausted = False
    stop_event = threading.Event()

    def submit_available(pool: ThreadPoolExecutor) -> None:
        nonlocal next_index, budget_exhausted
        while (
            not budget_exhausted
            and next_index < len(candidates)
            and len(in_flight) < MAX_CONCURRENT_LOG_FETCHES
        ):
            if deadline is not None and monotonic() >= deadline:
                budget_exhausted = True
                stop_event.set()
                return
            index = next_index
            next_index += 1
            in_flight[
                pool.submit(
                    _scan_candidate,
                    candidates[index],
                    client=client,
                    attempted_at=attempted_at,
                    deadline=deadline,
                    monotonic=monotonic,
                    stop_event=stop_event,
                )
            ] = index

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_LOG_FETCHES) as pool:
        submit_available(pool)
        while in_flight:
            done, _ = wait(tuple(in_flight), return_when=FIRST_COMPLETED)
            for future in sorted(done, key=in_flight.__getitem__):
                index = in_flight.pop(future)
                try:
                    completed[index] = future.result()
                except BudgetExhausted:
                    budget_exhausted = True
            submit_available(pool)

    log.info(
        "DNS scan scheduler complete: candidates=%d submitted=%d completed=%d "
        "deadline_exhausted=%s",
        len(candidates),
        next_index,
        len(completed),
        str(budget_exhausted).lower(),
    )

    for index in sorted(completed):
        row = completed[index]
        by_identity[(row["pipeline"], row["job_id"])] = row
    return sort_state_jobs(by_identity.values())


def collect(
    *,
    client: BuildkiteClient,
    state_path: Path,
    output_path: Path,
    discover_days: int = DEFAULT_DISCOVER_DAYS,
    max_logs: int = DEFAULT_MAX_LOGS,
    merge_state_git_ref: str | None = None,
    dry_run: bool = False,
    time_budget_seconds: int = DEFAULT_TIME_BUDGET_SECONDS,
    now: datetime | None = None,
    repo_root: Path = ROOT,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict:
    """Run discovery, bounded scanning, state merge, and public projection."""
    if not 1 <= discover_days <= MAX_DISCOVER_DAYS:
        raise ValueError(f"discover_days must be in 1..{MAX_DISCOVER_DAYS}")
    if max_logs < 1:
        raise ValueError("max_logs must be positive")
    if time_budget_seconds < 0:
        raise ValueError("time_budget_seconds cannot be negative")
    started_monotonic = monotonic()
    deadline = (
        started_monotonic
        + max(0, time_budget_seconds - FINALIZATION_RESERVE_SECONDS)
        if time_budget_seconds > 0
        else None
    )
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    target_start = clock - timedelta(days=discover_days)
    retention_start = clock - timedelta(hours=RETENTION_HOURS)

    local_state = load_state(state_path)
    ref_state = (
        load_state_from_git_ref(merge_state_git_ref, repo_root=repo_root)
        if merge_state_git_ref
        else None
    )
    old_rows = merge_state_jobs(
        local_state["jobs"] if local_state else [],
        ref_state["jobs"] if ref_state else [],
    )

    query_start, coverage_start = _discovery_window(
        [state for state in (local_state, ref_state) if state is not None],
        clock=clock,
        target_start=target_start,
    )
    finished_from = iso_timestamp(query_start)
    active_created_from = iso_timestamp(target_start)
    active_created_to = iso_timestamp(clock)
    discovered: list[dict] = []
    discovered_builds = 0
    discovery_started = monotonic()
    requests_before_discovery = (
        client.request_starts()
        if isinstance(client, BuildkiteClient)
        else {"build_page": 0, "job_log": 0}
    )
    for pipeline in PIPELINES:
        builds = client.discover_builds(
            pipeline,
            finished_from=finished_from,
            active_created_from=active_created_from,
            active_created_to=active_created_to,
            deadline=deadline,
        )
        discovered_builds += len(builds)
        discovered.extend(discover_job_metadata({pipeline: builds}))
    requests_after_discovery = (
        client.request_starts()
        if isinstance(client, BuildkiteClient)
        else requests_before_discovery
    )
    log.info(
        "DNS collection phase complete: phase=discovery elapsed_seconds=%.3f "
        "builds=%d eligible_job_rows=%d build_page_requests=%d",
        max(0.0, monotonic() - discovery_started),
        discovered_builds,
        len(discovered),
        requests_after_discovery["build_page"]
        - requests_before_discovery["build_page"],
    )
    rows = _prepare_records(
        old_rows,
        discovered,
        retention_start=retention_start,
        end_exclusive=clock,
    )
    scan_started = monotonic()
    requests_before_scan = requests_after_discovery
    rows = scan_records(
        rows,
        client=client,
        attempted_at=iso_timestamp(clock),
        max_logs=max_logs,
        deadline=deadline,
        monotonic=monotonic,
    )
    requests_after_scan = (
        client.request_starts()
        if isinstance(client, BuildkiteClient)
        else requests_before_scan
    )
    log.info(
        "DNS collection phase complete: phase=scan elapsed_seconds=%.3f "
        "job_log_requests=%d",
        max(0.0, monotonic() - scan_started),
        requests_after_scan["job_log"] - requests_before_scan["job_log"],
    )
    if isinstance(client, BuildkiteClient):
        telemetry = client.telemetry()
        log.info(
            "DNS request telemetry: build_page_requests=%d job_log_requests=%d "
            "pacing_wait_seconds=%.3f quota_wait_seconds=%.3f "
            "retry_sleep_worker_seconds=%.3f network_worker_seconds=%.3f "
            "network_max_seconds=%.3f stream_worker_seconds=%.3f "
            "stream_max_seconds=%.3f retry_requests=%d "
            "rate_limited_responses=%d",
            telemetry["build_page"],
            telemetry["job_log"],
            telemetry["pacing_wait_seconds"],
            telemetry["quota_wait_seconds"],
            telemetry["retry_sleep_worker_seconds"],
            telemetry["network_worker_seconds"],
            telemetry["network_max_seconds"],
            telemetry["stream_worker_seconds"],
            telemetry["stream_max_seconds"],
            telemetry["retry_requests"],
            telemetry["rate_limited_responses"],
        )

    state = empty_state(clock, coverage_start)
    state["jobs"] = prune_state_jobs(rows, retention_start, clock)
    state = validate_state(state)
    output = build_public_output(state)
    if not dry_run:
        write_state(state_path, state)
        write_public_output(output_path, output)
    return output


def _summary(payload: dict) -> str:
    coverage = payload["coverage"]
    return (
        f"eligible={coverage['eligible_jobs']} scanned={coverage['scanned_jobs']} "
        f"positive={coverage['positive_jobs']} pending={coverage['pending_jobs']} "
        f"unavailable={coverage['unavailable_jobs']} oversize={coverage['oversize_jobs']} "
        f"coverage={coverage['status']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--discover-days",
        type=int,
        default=DEFAULT_DISCOVER_DAYS,
        help=(
            "Target coverage horizon; a missing or stale state bootstraps 24h "
            "and grows coverage through overlapping incremental runs."
        ),
    )
    parser.add_argument("--max-logs", type=int, default=DEFAULT_MAX_LOGS)
    parser.add_argument(
        "--time-budget-seconds",
        type=int,
        default=DEFAULT_TIME_BUDGET_SECONDS,
        help="Stop starting new log requests after this monotonic budget (0 disables).",
    )
    parser.add_argument("--merge-state-git-ref")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    token = os.environ.get("BUILDKITE_TOKEN", "")
    if not token:
        log.error("BUILDKITE_TOKEN is required")
        return 2
    try:
        payload = collect(
            client=BuildkiteClient(token),
            state_path=args.state,
            output_path=args.output,
            discover_days=args.discover_days,
            max_logs=args.max_logs,
            time_budget_seconds=args.time_budget_seconds,
            merge_state_git_ref=args.merge_state_git_ref,
            dry_run=args.dry_run,
        )
    except CollectionError as exc:
        # Fail closed without interpolating response bodies, headers, URLs, or
        # arbitrary labels into CI logs.
        log.error("DNS health collection failed safely: reason=%s", exc.reason)
        return 1
    except (StateValidationError, ValueError):
        log.error("DNS health collection failed safely: reason=invalid_state")
        return 1
    log.info("DNS health collection complete: %s", _summary(payload))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
