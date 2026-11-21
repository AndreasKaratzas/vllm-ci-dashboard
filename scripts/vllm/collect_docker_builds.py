#!/usr/bin/env python3
"""Collect ROCm docker build job timings from Buildkite GraphQL.

Each attempt re-reads the newest jobs of both pipelines down to a refetch floor
(head pass), then continues any unfinished backfill from its saved cursor (tail
pass).  ``pipeline.jobs`` has no time filter, so the backfill can only resume
from an ``after`` cursor; progress is kept in a private gzip checkpoint and the
public ``docker_builds.json`` is replaced only once both tails reach the
retention cutoff.

Rows are contiguous from the tail cursor up to a coverage watermark: the
baseline's publication time, or the time of the last attempt whose head pass
finished.  The head pass reads ``refetch_hours`` below the watermark, and a
checkpoint whose watermark is missing or older than ``MAX_REFETCH_HOURS`` is
discarded so a resumed backfill never publishes an unread interval.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Allow direct execution as ``python scripts/vllm/collect_docker_builds.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.buildkite_request_guard import (  # noqa: E402
    BuildkiteRequestAllowanceExhausted,
    install_from_environment_or_exit,
)

install_from_environment_or_exit()

import requests  # noqa: E402

from vllm.bounded_json import atomic_write_bytes  # noqa: E402
from vllm.ci import docker_builds as db  # noqa: E402
from vllm.ci.utils import parse_iso  # noqa: E402
from vllm.constants import BK_GRAPHQL_URL  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CHECKPOINT_SCHEMA_VERSION = 1
CHECKPOINT_PRODUCER = "vllm_docker_builds_wip"
MAX_CHECKPOINT_BYTES = 64 * 1024 * 1024
CHECKPOINT_GUARD_EXIT = 75
CHECKPOINT_WALL_CLOCK_EXIT = 76
PAGE_SIZE = 100
REQUEST_TIMEOUT = (15, 120)
MIN_PAGE_INTERVAL_SECONDS = 22
GRAPHQL_POINT_RESERVE = 6000
MAX_RETRIES = 2
MAX_RATE_LIMIT_WAIT_SECONDS = 301
DOCKER_JOBS_QUERY = """
query DockerBuildJobs($slug: ID!, $first: Int!, $after: String, $keys: [String!]) {
  pipeline(slug: $slug) {
    jobs(first: $first, after: $after, type: [COMMAND], order: RECENTLY_CREATED,
         step: {key: $keys}) {
      edges { node { ... on JobTypeCommand {
        uuid state step { key } createdAt runnableAt startedAt finishedAt
        retried passed exitStatus agentQueryRules
        build { number branch pullRequest { id } }
      } } }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""
_RATE_LIMIT_REMAINING = ("RateLimit-Remaining", "RateLimit-User-Remaining")
_RATE_LIMIT_RESET = ("RateLimit-Reset", "RateLimit-User-Reset")


class CollectionError(RuntimeError):
    """The collection cannot publish and must fail closed."""


class GraphQLError(CollectionError):
    """Buildkite returned GraphQL ``errors`` or no ``data`` object."""


class AllowanceSpent(RuntimeError):
    """The request-start allowance for this attempt is used up."""


class TimeSpent(RuntimeError):
    """The wall-clock budget for this attempt is used up."""


@dataclass
class TailState:
    cursor: str | None = None
    oldest_created_at: datetime | None = None
    reached_cutoff: bool = False


@dataclass
class Page:
    rows: list[db.JobRow]
    oldest_created_at: datetime | None
    end_cursor: str | None
    has_next_page: bool


def _header_int(headers: Any, name: str) -> int | None:
    try:
        return int(float(headers.get(name, "")))
    except (AttributeError, TypeError, ValueError):
        return None


class GraphQLClient:
    """Paced, start-counted GraphQL POST transport without redirects."""

    def __init__(
        self,
        token: str,
        *,
        max_request_starts: int,
        deadline: float,
        transport: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token = token
        self.max_request_starts = max_request_starts
        self.deadline = deadline
        self._transport = transport or requests.post
        self._sleep = sleep
        self._monotonic = monotonic
        self._next_start_at: float | None = None
        self._remaining: dict[str, int] = {}
        self.request_starts = 0

    def _wait_for_start(self) -> None:
        now = self._monotonic()
        ready_at = max(now, self._next_start_at or now)
        if ready_at >= self.deadline:
            raise TimeSpent("GraphQL time budget ended")
        if ready_at > now:
            self._sleep(ready_at - now)

    def _observe(self, headers: Any) -> None:
        observed = {}
        for name in _RATE_LIMIT_REMAINING:
            value = _header_int(headers, name)
            if value is not None:
                observed[name] = value
        if not observed:
            return
        log.info(
            "GraphQL start %d: %s",
            self.request_starts,
            ", ".join(
                f"{name}={value} (delta {value - self._remaining.get(name, value):+d})"
                for name, value in observed.items()
            ),
        )
        self._remaining.update(observed)
        if min(observed.values()) < GRAPHQL_POINT_RESERVE:
            self._block_for(self._reset_seconds(headers))

    @staticmethod
    def _reset_seconds(headers: Any) -> float:
        waits = [
            value + 1
            for name in _RATE_LIMIT_RESET
            if (value := _header_int(headers, name)) is not None and value >= 0
        ]
        return min(MAX_RATE_LIMIT_WAIT_SECONDS, max(waits, default=MAX_RATE_LIMIT_WAIT_SECONDS))

    def _block_for(self, seconds: float) -> None:
        until = self._monotonic() + seconds
        self._next_start_at = max(self._next_start_at or until, until)

    def execute(self, query: str, variables: dict) -> dict:
        for attempt in range(MAX_RETRIES + 1):
            self._wait_for_start()
            if self.request_starts >= self.max_request_starts:
                raise AllowanceSpent(f"{self.request_starts} GraphQL request starts used")
            self.request_starts += 1
            self._next_start_at = self._monotonic() + MIN_PAGE_INTERVAL_SECONDS
            try:
                response = self._transport(
                    BK_GRAPHQL_URL,
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json={"query": query, "variables": variables},
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                if attempt == MAX_RETRIES:
                    raise CollectionError("Buildkite GraphQL network error") from None
                continue
            status = response.status_code
            if 300 <= status < 400:
                raise CollectionError(
                    "Buildkite GraphQL redirected; refusing an unbudgeted follow-up send"
                )
            self._observe(response.headers)
            if status == 429 or status >= 500:
                if attempt == MAX_RETRIES:
                    raise CollectionError(f"Buildkite GraphQL HTTP {status}")
                if status == 429:
                    self._block_for(self._reset_seconds(response.headers))
                continue
            if not 200 <= status < 300:
                raise CollectionError(f"Buildkite GraphQL HTTP {status}")
            try:
                payload = response.json()
            except ValueError:
                raise CollectionError("Buildkite GraphQL returned invalid JSON") from None
            if not isinstance(payload, dict):
                raise GraphQLError("Buildkite GraphQL returned a non-object payload")
            if payload.get("errors") or not isinstance(payload.get("data"), dict):
                errors = payload.get("errors")
                first = errors[0] if isinstance(errors, list) and errors else None
                message = first.get("message") if isinstance(first, dict) else None
                raise GraphQLError(f"Buildkite GraphQL error: {message or 'missing data'}")
            return payload["data"]
        raise CollectionError("Buildkite GraphQL retries exhausted")


def fetch_page(client: GraphQLClient, pipeline: str, after: str | None) -> Page:
    data = client.execute(
        DOCKER_JOBS_QUERY,
        {
            "slug": f"vllm/{pipeline}",
            "first": PAGE_SIZE,
            "after": after,
            "keys": list(db.STEP_KEYS),
        },
    )
    jobs = (data.get("pipeline") or {}).get("jobs")
    if not isinstance(jobs, dict) or not isinstance(jobs.get("edges"), list):
        raise CollectionError(f"pipeline vllm/{pipeline} returned no jobs connection")
    page_info = jobs.get("pageInfo") or {}
    rows: list[db.JobRow] = []
    oldest: datetime | None = None
    for edge in jobs["edges"]:
        node = edge.get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict):
            continue
        created = parse_iso(node.get("createdAt"))
        if created is not None and (oldest is None or created < oldest):
            oldest = created
        row = db.normalize_node(node, pipeline)
        if row is not None:
            rows.append(row)
    end_cursor = page_info.get("endCursor")
    return Page(
        rows=rows,
        oldest_created_at=oldest,
        end_cursor=end_cursor if isinstance(end_cursor, str) else None,
        has_next_page=page_info.get("hasNextPage") is True and isinstance(end_cursor, str),
    )


def fetch_head(
    client: GraphQLClient, pipeline: str, floor: datetime
) -> tuple[list[db.JobRow], Page]:
    """Page from the newest job until a page reaches below ``floor``."""
    rows: list[db.JobRow] = []
    after = None
    while True:
        page = fetch_page(client, pipeline, after)
        rows.extend(page.rows)
        if (
            not page.has_next_page
            or page.oldest_created_at is None
            or page.oldest_created_at < floor
        ):
            return rows, page
        after = page.end_cursor


def _is_cursor_error(exc: GraphQLError) -> bool:
    return "cursor" in str(exc).lower()


def fetch_tail(
    client: GraphQLClient,
    pipeline: str,
    state: TailState,
    cutoff: datetime,
    on_page: Callable[[list[db.JobRow]], None],
) -> None:
    """Continue one pipeline's backfill from its cursor until the cutoff."""
    restarted = False
    while not state.reached_cutoff:
        try:
            page = fetch_page(client, pipeline, state.cursor)
        except GraphQLError as exc:
            if restarted or state.cursor is None or not _is_cursor_error(exc):
                raise
            log.warning("Restarting vllm/%s backfill after a rejected cursor: %s", pipeline, exc)
            restarted = True
            state.cursor = None
            state.oldest_created_at = None
            continue
        _advance(state, page, cutoff)
        on_page(page.rows)


def _advance(state: TailState, page: Page, cutoff: datetime) -> None:
    state.cursor = page.end_cursor
    if page.oldest_created_at is not None:
        state.oldest_created_at = page.oldest_created_at
    state.reached_cutoff = not page.has_next_page or (
        page.oldest_created_at is not None and page.oldest_created_at < cutoff
    )


def _row_to_json(row: db.JobRow) -> dict:
    value = asdict(row)
    value["created_at"] = db.format_timestamp(row.created_at)
    return value


def _row_from_json(value: dict) -> db.JobRow:
    created_at = parse_iso(value["created_at"])
    if created_at is None:
        raise ValueError("checkpoint row has no created_at")
    row = db.JobRow(**{**value, "created_at": created_at.astimezone(timezone.utc)})
    if (
        row.pipeline not in db.PIPELINES
        or row.step not in db.STEP_KEYS
        or row.state not in db.STATES
        or row.source not in db.SOURCES
    ):
        raise ValueError("checkpoint row is outside the contract")
    return row


def save_checkpoint(
    path: Path,
    *,
    baseline_ref: str,
    watermark: datetime | None,
    tails: dict[str, TailState],
    rows: list[db.JobRow],
) -> None:
    payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "producer": CHECKPOINT_PRODUCER,
        "baseline_ref": baseline_ref,
        "watermark": db.format_timestamp(watermark) if watermark is not None else None,
        "pipelines": {
            name: {
                "cursor": state.cursor,
                "oldest_created_at": (
                    db.format_timestamp(state.oldest_created_at)
                    if state.oldest_created_at is not None
                    else None
                ),
                "reached_cutoff": state.reached_cutoff,
            }
            for name, state in sorted(tails.items())
        },
        "rows": [_row_to_json(row) for row in rows],
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    atomic_write_bytes(path, gzip.compress(raw, compresslevel=6, mtime=0))


def load_checkpoint(
    path: Path, *, baseline_ref: str, now: datetime
) -> tuple[dict[str, TailState], list[db.JobRow], datetime] | None:
    """Return resumable progress, or ``None`` when absent, stale, or unreadable."""
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_CHECKPOINT_BYTES:
            raise ValueError("checkpoint is too large")
        with gzip.open(path, "rb") as handle:
            raw = handle.read(MAX_CHECKPOINT_BYTES + 1)
        if len(raw) > MAX_CHECKPOINT_BYTES:
            raise ValueError("checkpoint is too large")
        payload = json.loads(raw)
        if (
            payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION
            or payload.get("producer") != CHECKPOINT_PRODUCER
            or set(payload.get("pipelines", {})) != set(db.PIPELINES)
        ):
            raise ValueError("checkpoint contract mismatch")
        if payload.get("baseline_ref") != baseline_ref:
            log.info("Ignoring checkpoint bound to another baseline")
            return None
        watermark = parse_iso(payload["watermark"]) if payload["watermark"] else None
        if watermark is None or watermark < now - timedelta(hours=db.MAX_REFETCH_HOURS):
            log.info("Ignoring checkpoint without a recent coverage watermark")
            return None
        tails = {}
        for name, value in payload["pipelines"].items():
            cursor = value["cursor"]
            oldest = value["oldest_created_at"]
            if not (cursor is None or isinstance(cursor, str)) or not isinstance(
                value["reached_cutoff"], bool
            ):
                raise ValueError("checkpoint tail state is invalid")
            tails[name] = TailState(
                cursor=cursor,
                oldest_created_at=parse_iso(oldest) if oldest else None,
                reached_cutoff=value["reached_cutoff"],
            )
        rows = [_row_from_json(value) for value in payload["rows"]]
    except (OSError, EOFError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("Ignoring unreadable docker builds checkpoint: %s", exc)
        return None
    return tails, rows, watermark


def read_public(path: Path) -> dict:
    if path.stat().st_size > db.PUBLIC_OUTPUT_MAX_BYTES:
        raise CollectionError(f"{path} exceeds {db.PUBLIC_OUTPUT_MAX_BYTES} bytes")
    payload = json.loads(path.read_bytes())
    errors = db.validate_public(payload)
    if errors:
        raise CollectionError(f"{path} is invalid: " + "; ".join(errors))
    return payload


def read_git_payload(ref: str, *, repo_root: Path = REPO_ROOT) -> bytes:
    object_name = f"{ref}:{db.PUBLIC_PATH}"
    size = subprocess.run(
        ["git", "cat-file", "-s", object_name],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if size.returncode != 0:
        raise CollectionError(f"{object_name} does not exist")
    if int(size.stdout.strip()) > db.PUBLIC_OUTPUT_MAX_BYTES:
        raise CollectionError(f"{object_name} exceeds {db.PUBLIC_OUTPUT_MAX_BYTES} bytes")
    shown = subprocess.run(
        ["git", "show", object_name], cwd=repo_root, capture_output=True, check=False
    )
    if shown.returncode != 0:
        raise CollectionError(f"could not read {object_name}")
    errors = db.validate_public(json.loads(shown.stdout))
    if errors:
        raise CollectionError(f"{object_name} is invalid: " + "; ".join(errors))
    return shown.stdout


def restore_from_git_ref(
    ref: str, output: Path, *, repo_root: Path = REPO_ROOT
) -> list[db.JobRow]:
    """Tokenlessly restore the durable published payload as the next baseline."""
    raw = read_git_payload(ref, repo_root=repo_root)
    atomic_write_bytes(output, raw)
    return db.decode_public(json.loads(raw))


def _baseline(
    output: Path, *, baseline_ref: str, now: datetime
) -> tuple[dict[str, TailState], list[db.JobRow], datetime | None]:
    """Return tail states, rows, and the published time of a complete baseline."""
    if baseline_ref == "bootstrap":
        return {name: TailState() for name in db.PIPELINES}, [], None
    payload = read_public(output)
    published = parse_iso(payload["generated_at"])
    covered = (
        payload["coverage"]["status"] == "complete"
        and published is not None
        and published >= now - timedelta(hours=db.MAX_REFETCH_HOURS)
    )
    if not covered:
        log.info("Baseline coverage is incomplete or stale; backfilling the full window")
    tails = {name: TailState(reached_cutoff=covered) for name in db.PIPELINES}
    return tails, db.decode_public(payload), published if covered else None


def collect(
    *,
    token: str,
    output: Path,
    checkpoint: Path | None,
    baseline_ref: str,
    now: datetime,
    window_days: int = db.RETENTION_DAYS,
    refetch_hours: int = db.REFETCH_HOURS,
    max_requests: int = 30,
    time_budget_seconds: float = 1080,
    transport: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> int:
    cutoff = now - timedelta(days=window_days)
    tails, rows, watermark = _baseline(output, baseline_ref=baseline_ref, now=now)
    resumed = (
        load_checkpoint(checkpoint, baseline_ref=baseline_ref, now=now) if checkpoint else None
    )
    if resumed is not None:
        tails, rows, watermark = resumed
        log.info("Resuming checkpoint with %d rows", len(rows))
    rows = db.merge_rows(rows, [], now=now, retention_days=window_days)
    client = GraphQLClient(
        token,
        max_request_starts=max_requests,
        deadline=monotonic() + time_budget_seconds,
        transport=transport,
        sleep=sleep,
        monotonic=monotonic,
    )

    def persist() -> None:
        if checkpoint is not None:
            save_checkpoint(
                checkpoint, baseline_ref=baseline_ref, watermark=watermark, tails=tails, rows=rows
            )

    def merge(fresh: list[db.JobRow]) -> None:
        nonlocal rows
        rows = db.merge_rows(rows, fresh, now=now, retention_days=window_days)
        persist()

    try:
        floor = db.refetch_floor(rows, now, refetch_hours=refetch_hours)
        if watermark is not None:
            floor = min(floor, watermark - timedelta(hours=refetch_hours))
        for pipeline in db.PIPELINES:
            fresh, last = fetch_head(client, pipeline, floor)
            tail = tails[pipeline]
            if not tail.reached_cutoff and tail.cursor is None:
                _advance(tail, last, cutoff)
            merge(fresh)
        watermark = now
        persist()
        for pipeline in db.PIPELINES:
            fetch_tail(client, pipeline, tails[pipeline], cutoff, merge)
    except (AllowanceSpent, BuildkiteRequestAllowanceExhausted) as exc:
        persist()
        log.warning("Docker builds allowance ended with resumable progress: %s", exc)
        return CHECKPOINT_GUARD_EXIT
    except TimeSpent as exc:
        persist()
        log.warning("Docker builds time budget ended with resumable progress: %s", exc)
        return CHECKPOINT_WALL_CLOCK_EXIT

    payload = db.encode_public(
        rows,
        generated_at=now,
        window_days=window_days,
        coverage=db.complete_coverage(cutoff, refetch_hours=refetch_hours),
    )
    errors = db.validate_public(payload)
    if errors:
        raise CollectionError("refusing to publish: " + "; ".join(errors))
    atomic_write_bytes(output, db.serialize_public(payload))
    log.info(
        "Wrote %d docker build jobs with %d GraphQL request starts",
        payload["jobs"]["n"],
        client.request_starts,
    )
    return 0


def _parse_now(value: str) -> datetime:
    parsed = parse_iso(value)
    if parsed is None or parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--now must be an ISO timestamp with a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / db.PUBLIC_PATH)
    parser.add_argument("--checkpoint", type=Path, help="Private gzip WIP checkpoint")
    parser.add_argument(
        "--baseline-ref",
        default="bootstrap",
        help="Exact durable ref restored into --output, or bootstrap",
    )
    parser.add_argument("--window-days", type=int, default=db.RETENTION_DAYS)
    parser.add_argument("--refetch-hours", type=int, default=db.REFETCH_HOURS)
    parser.add_argument("--max-requests", type=int, default=30)
    parser.add_argument("--time-budget-seconds", type=float, default=1080)
    parser.add_argument("--now", type=_parse_now, help="Override the collection time (tests)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--restore-git-ref",
        metavar="REF",
        help="Tokenlessly validate and restore the published payload from REF into --output",
    )
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--clear-checkpoint", action="store_true")
    args = parser.parse_args(argv)

    if not 1 <= args.window_days <= db.RETENTION_DAYS:
        parser.error(f"--window-days must be between 1 and {db.RETENTION_DAYS}")
    if not 1 <= args.refetch_hours <= db.MAX_REFETCH_HOURS:
        parser.error(f"--refetch-hours must be between 1 and {db.MAX_REFETCH_HOURS}")
    if args.max_requests < 1:
        parser.error("--max-requests must be positive")

    try:
        if args.clear_checkpoint:
            if args.checkpoint is None:
                parser.error("--clear-checkpoint requires --checkpoint")
            args.checkpoint.unlink(missing_ok=True)
            return 0
        if args.validate_only:
            payload = read_public(args.output)
            log.info("Validated %s with %d jobs", args.output, payload["jobs"]["n"])
            return 0
        if args.restore_git_ref:
            rows = restore_from_git_ref(args.restore_git_ref, args.output)
            log.info("Restored %d jobs from %s", len(rows), args.restore_git_ref)
            return 0
        token = os.environ.get("BUILDKITE_TOKEN")
        if not token:
            raise CollectionError("BUILDKITE_TOKEN is required")
        now = args.now or datetime.now(timezone.utc).replace(microsecond=0)
        return collect(
            token=token,
            output=args.output,
            checkpoint=args.checkpoint,
            baseline_ref=args.baseline_ref,
            now=now,
            window_days=args.window_days,
            refetch_hours=args.refetch_hours,
            max_requests=args.max_requests,
            time_budget_seconds=args.time_budget_seconds,
        )
    except (CollectionError, OSError, ValueError) as exc:
        log.error("Docker builds collection failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
