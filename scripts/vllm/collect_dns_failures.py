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
import time
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
MAX_DISCOVER_DAYS = RETENTION_HOURS // 24
MAX_DISCOVERY_PAGES = 1000
PAGE_SIZE = 100
MAX_REQUEST_ATTEMPTS = 5
MAX_RETRY_SLEEP_SECONDS = 60
REQUEST_TIMEOUT = (15, 120)
RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504, 520, 522, 524})
REQUESTS_PER_MINUTE = 30
REQUEST_INTERVAL_SECONDS = 60 / REQUESTS_PER_MINUTE
SHARED_QUOTA_RESERVE = 10
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
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        self.sleep = sleep
        self.monotonic = monotonic
        self._next_request_at = 0.0
        self._quota_blocked_until = 0.0

    def _sleep_with_deadline(self, seconds: float, deadline: float | None) -> None:
        wait = max(0.0, seconds)
        remaining = deadline - self.monotonic() if deadline is not None else None
        if remaining is not None and wait >= remaining:
            raise BudgetExhausted()
        if wait:
            self.sleep(wait)

    def _throttle(self, deadline: float | None) -> None:
        now = self.monotonic()
        ready_at = max(self._next_request_at, self._quota_blocked_until)
        self._sleep_with_deadline(max(0.0, ready_at - now), deadline)
        # Use the scheduled instant as a logical floor so deterministic test
        # clocks and tiny scheduler wakeups cannot accidentally burst.
        started_at = max(self.monotonic(), ready_at)
        self._next_request_at = started_at + REQUEST_INTERVAL_SECONDS

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
                self._quota_blocked_until = max(
                    self._quota_blocked_until,
                    self.monotonic() + reset_wait,
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
                response = self.session.request(
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
                if attempt + 1 == MAX_REQUEST_ATTEMPTS:
                    raise CollectionError("network_error") from None
                wait = min(MAX_RETRY_SLEEP_SECONDS, 2**attempt)
                try:
                    self._sleep_with_deadline(wait, deadline)
                except BudgetExhausted:
                    raise BudgetExhausted() from None
                continue

            self._observe_quota_headers(response.headers)
            if response.status_code in RETRYABLE_HTTP_STATUSES:
                if attempt + 1 == MAX_REQUEST_ATTEMPTS:
                    reason = _unavailable_reason(response.status_code)
                    response.close()
                    raise CollectionError(reason)
                wait = retry_after_seconds(
                    response.headers.get("Retry-After"),
                    fallback=2**attempt,
                )
                wait = max(wait, rate_limit_reset_seconds(response.headers))
                response.close()
                self._sleep_with_deadline(wait, deadline)
                continue
            if response.status_code < 200 or response.status_code >= 300:
                reason = _unavailable_reason(response.status_code)
                response.close()
                raise CollectionError(reason)
            return response
        raise CollectionError("network_error")

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

    def discover_builds(
        self,
        pipeline: str,
        *,
        finished_from: str,
        deadline: float | None = None,
    ) -> list[dict]:
        """Union recent active builds with all builds finished in the window.

        Querying active states first and the unbounded-upper finished cohort
        second closes state transitions during pagination: a build that
        finishes between legs remains present in at least one cohort. Bound
        the active leg to the same parent-build horizon so a historical
        blocked-build backlog cannot consume the entire collection budget.
        """
        active = self._paginate_builds(
            pipeline,
            filters={
                "state[]": list(ACTIVE_BUILD_STATES),
                "created_from": finished_from,
            },
            deadline=deadline,
        )
        finished = self._paginate_builds(
            pipeline,
            filters={"finished_from": finished_from},
            deadline=deadline,
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


def scan_records(
    rows: Iterable[dict],
    *,
    client: BuildkiteClient,
    attempted_at: str,
    max_logs: int,
    deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> list[dict]:
    """Scan new backlog before retries, newest-first within each class."""
    ordered = sort_state_jobs(rows)
    by_identity = {(row["pipeline"], row["job_id"]): row for row in ordered}
    pending = [row for row in ordered if row["status"] == "pending"]
    unavailable = [row for row in ordered if row["status"] == "unavailable"]
    candidates = (pending + unavailable)[:max_logs]
    for row in candidates:
        if deadline is not None and monotonic() >= deadline:
            break
        identity = (row["pipeline"], row["job_id"])
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
            by_identity[identity] = scan_record(
                scan_metadata,
                classification,
                attempted_at=attempted_at,
                previous_attempts=previous_attempts,
            )
        except OversizeLog as exc:
            by_identity[identity] = oversize_record(
                row,
                exc.log_bytes,
                attempted_at=attempted_at,
                previous_attempts=previous_attempts,
            )
        except LogUnavailable as exc:
            by_identity[identity] = unavailable_record(
                row,
                exc.reason,
                attempted_at=attempted_at,
                previous_attempts=previous_attempts,
            )
        except BudgetExhausted:
            break
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
    discovery_start = clock - timedelta(days=discover_days)
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

    finished_from = iso_timestamp(discovery_start)
    discovered: list[dict] = []
    for pipeline in PIPELINES:
        builds = client.discover_builds(
            pipeline,
            finished_from=finished_from,
            deadline=deadline,
        )
        discovered.extend(discover_job_metadata({pipeline: builds}))
    rows = _prepare_records(
        old_rows,
        discovered,
        retention_start=retention_start,
        end_exclusive=clock,
    )
    rows = scan_records(
        rows,
        client=client,
        attempted_at=iso_timestamp(clock),
        max_logs=max_logs,
        deadline=deadline,
        monotonic=monotonic,
    )

    state = empty_state(clock, discovery_start)
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
    parser.add_argument("--discover-days", type=int, default=DEFAULT_DISCOVER_DAYS)
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
