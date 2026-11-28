"""Project ROCm docker build jobs into the compact public ``docker_builds`` contract.

Pure domain code: no network and no tokens.  Only an allowlisted projection of
each GraphQL job node is kept; branch names, commits, creators, agents, labels
and URLs are read in memory at most and never stored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from ..dashboard_storage_budget import writer_max_bytes
from .utils import parse_iso, queue_from_rules


SCHEMA_VERSION = 1
PIPELINES = ("amd-ci", "ci")
STEP_KEYS = ("ensure-ci-base-amd", "image-build-amd", "refresh-rocm-base-amd")
QUEUE = "amd-cpu"
STATES = ("passed", "failed", "canceled", "timed_out", "running", "pending", "other")
SOURCES = ("main", "pr", "other")
RETENTION_DAYS = 30
REFETCH_HOURS = 10
MAX_REFETCH_HOURS = 48
MAX_FRESH_AGE_HOURS = 6
PUBLIC_PATH = "data/vllm/ci/docker_builds.json"
PUBLIC_OUTPUT_MAX_BYTES = writer_max_bytes("docker_builds")

COVERAGE_STATUSES = ("complete", "not_collected")
NON_TERMINAL_STATES = frozenset({"running", "pending"})
_GRAPHQL_STATES = {
    "CANCELED": "canceled",
    "CANCELING": "canceled",
    "TIMED_OUT": "timed_out",
    "TIMING_OUT": "timed_out",
    "RUNNING": "running",
    "ASSIGNED": "running",
    "ACCEPTED": "running",
    "RESERVED": "running",
    "SCHEDULED": "pending",
    "WAITING": "pending",
    "LIMITED": "pending",
    "PENDING": "pending",
    "BLOCKED": "pending",
    "UNBLOCKED": "pending",
}
DIMS = {
    "pipeline": list(PIPELINES),
    "step": list(STEP_KEYS),
    "state": list(STATES),
    "source": list(SOURCES),
}
JOB_COLUMNS = (
    "id",
    "build",
    "pipeline",
    "step",
    "state",
    "source",
    "retried",
    "created",
    "wait_s",
    "run_s",
    "exit",
)
TOP_LEVEL_KEYS = frozenset({"schema_version", "generated_at", "window", "coverage", "dims", "jobs"})
_ID_RE = re.compile(r"^[0-9a-f-]{36}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_FORBIDDEN_MARKERS = ("http", "bkua_", "@")


@dataclass(frozen=True)
class JobRow:
    id: str
    build: int
    pipeline: str
    step: str
    state: str
    source: str
    retried: bool
    created_at: datetime
    wait_s: int | None
    run_s: int | None
    exit: int | None


def format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = parse_iso(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _seconds_between(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds()))


def _state(node: Mapping[str, Any]) -> str:
    state = node.get("state")
    if state == "FINISHED":
        return "passed" if node.get("passed") is True else "failed"
    return _GRAPHQL_STATES.get(state, "other") if isinstance(state, str) else "other"


def _exit_status(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def normalize_node(node: dict, pipeline: str) -> JobRow | None:
    """Project one GraphQL ``JobTypeCommand`` node, or drop it when out of scope."""
    if not isinstance(node, dict) or pipeline not in PIPELINES:
        return None
    job_id = node.get("uuid")
    step = node.get("step")
    build = node.get("build")
    if not isinstance(job_id, str) or not _ID_RE.fullmatch(job_id):
        return None
    if not isinstance(step, dict) or step.get("key") not in STEP_KEYS:
        return None
    if not isinstance(build, dict):
        return None
    number = build.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        return None
    rules = node.get("agentQueryRules")
    if not isinstance(rules, list) or queue_from_rules(rules) != QUEUE:
        return None
    created_at = _parse_timestamp(node.get("createdAt"))
    if created_at is None:
        return None
    started_at = _parse_timestamp(node.get("startedAt"))
    runnable_at = _parse_timestamp(node.get("runnableAt")) or created_at
    if build.get("pullRequest") is not None:
        source = "pr"
    elif build.get("branch") == "main":
        source = "main"
    else:
        source = "other"
    return JobRow(
        id=job_id,
        build=number,
        pipeline=pipeline,
        step=step["key"],
        state=_state(node),
        source=source,
        retried=node.get("retried") is True,
        created_at=created_at.replace(microsecond=0),
        wait_s=_seconds_between(runnable_at, started_at),
        run_s=_seconds_between(started_at, _parse_timestamp(node.get("finishedAt"))),
        exit=_exit_status(node.get("exitStatus")),
    )


def _sort_key(row: JobRow) -> tuple[float, str]:
    return (-row.created_at.timestamp(), row.id)


def merge_rows(
    existing: Iterable[JobRow],
    fresh: Iterable[JobRow],
    *,
    now: datetime,
    retention_days: int = RETENTION_DAYS,
) -> list[JobRow]:
    """Dedupe by job id (fresh wins), prune past retention, newest first."""
    cutoff = now - timedelta(days=retention_days)
    merged = {row.id: row for row in existing}
    merged.update((row.id, row) for row in fresh)
    return sorted((row for row in merged.values() if row.created_at >= cutoff), key=_sort_key)


def refetch_floor(
    rows: Iterable[JobRow],
    now: datetime,
    *,
    refetch_hours: int = REFETCH_HOURS,
) -> datetime:
    """Return how far back the head pass must re-read to refresh unfinished jobs."""
    floor = now - timedelta(hours=refetch_hours)
    for row in rows:
        if row.state in NON_TERMINAL_STATES and row.created_at < floor:
            floor = row.created_at
    return max(now - timedelta(hours=MAX_REFETCH_HOURS), floor)


def _window(generated_at: datetime, window_days: int) -> tuple[datetime, datetime]:
    to = generated_at.astimezone(timezone.utc).replace(microsecond=0)
    return to - timedelta(days=window_days), to


def encode_public(
    rows: Iterable[JobRow],
    *,
    generated_at: datetime,
    window_days: int,
    coverage: Mapping[str, Any],
) -> dict:
    window_from, window_to = _window(generated_at, window_days)
    ordered = sorted(rows, key=_sort_key)
    columns: dict[str, list[Any]] = {name: [] for name in JOB_COLUMNS}
    for row in ordered:
        created = int((row.created_at - window_from).total_seconds())
        if created < 0:
            raise ValueError(f"job {row.id} predates the public window")
        columns["id"].append(row.id)
        columns["build"].append(row.build)
        columns["pipeline"].append(PIPELINES.index(row.pipeline))
        columns["step"].append(STEP_KEYS.index(row.step))
        columns["state"].append(STATES.index(row.state))
        columns["source"].append(SOURCES.index(row.source))
        columns["retried"].append(int(row.retried))
        columns["created"].append(created)
        columns["wait_s"].append(row.wait_s)
        columns["run_s"].append(row.run_s)
        columns["exit"].append(row.exit)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": format_timestamp(window_to),
        "window": {
            "days": window_days,
            "from": format_timestamp(window_from),
            "to": format_timestamp(window_to),
        },
        "coverage": json.loads(json.dumps(coverage)),
        "dims": json.loads(json.dumps(DIMS)),
        "jobs": {"n": len(ordered), **columns},
    }


def complete_coverage(window_from: datetime, *, refetch_hours: int = REFETCH_HOURS) -> dict:
    complete_from = format_timestamp(window_from)
    return {
        "status": "complete",
        "refetch_hours": refetch_hours,
        "pipelines": {pipeline: {"complete_from": complete_from} for pipeline in PIPELINES},
    }


def seed_payload(now: datetime) -> dict:
    payload = encode_public(
        [],
        generated_at=now,
        window_days=RETENTION_DAYS,
        coverage={"status": "not_collected", "refetch_hours": REFETCH_HOURS, "pipelines": {}},
    )
    payload["window"]["from"] = payload["window"]["to"]
    return payload


def serialize_public(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode()


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_jobs(jobs: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(jobs) != {"n", *JOB_COLUMNS}:
        return [f"jobs keys {sorted(jobs)} differ from the contract"]
    n = jobs["n"]
    if not _is_int(n) or n < 0:
        return ["jobs.n must be a non-negative integer"]
    for name in JOB_COLUMNS:
        if not isinstance(jobs[name], list) or len(jobs[name]) != n:
            errors.append(f"jobs.{name} must be a list of length {n}")
    if errors:
        return errors
    ids = jobs["id"]
    if not all(isinstance(value, str) and _ID_RE.fullmatch(value) for value in ids):
        errors.append("jobs.id must hold job uuids")
    elif len(set(ids)) != n:
        errors.append("jobs.id must be unique")
    if not all(_is_int(value) and value >= 1 for value in jobs["build"]):
        errors.append("jobs.build must hold positive integers")
    for name in ("pipeline", "step", "state", "source"):
        size = len(DIMS[name])
        if not all(_is_int(value) and 0 <= value < size for value in jobs[name]):
            errors.append(f"jobs.{name} must index dims.{name}")
    if not all(value in (0, 1) and _is_int(value) for value in jobs["retried"]):
        errors.append("jobs.retried must hold 0 or 1")
    created = jobs["created"]
    if not all(_is_int(value) and value >= 0 for value in created):
        errors.append("jobs.created must hold non-negative integers")
    elif any(created[i] < created[i + 1] for i in range(n - 1)):
        errors.append("jobs must be sorted by created descending")
    for name in ("wait_s", "run_s"):
        if not all(value is None or (_is_int(value) and value >= 0) for value in jobs[name]):
            errors.append(f"jobs.{name} must hold non-negative integers or null")
    if not all(value is None or _is_int(value) for value in jobs["exit"]):
        errors.append("jobs.exit must hold integers or null")
    return errors


def _validate_coverage(
    coverage: Mapping[str, Any], window_from: datetime | None, n: object
) -> list[str]:
    if set(coverage) != {"status", "refetch_hours", "pipelines"}:
        return ["coverage keys differ from the contract"]
    errors: list[str] = []
    status = coverage["status"]
    if status not in COVERAGE_STATUSES:
        errors.append("coverage.status is invalid")
    refetch = coverage["refetch_hours"]
    if not _is_int(refetch) or not 1 <= refetch <= MAX_REFETCH_HOURS:
        errors.append("coverage.refetch_hours is out of range")
    pipelines = coverage["pipelines"]
    if not isinstance(pipelines, dict):
        return [*errors, "coverage.pipelines must be an object"]
    if status == "complete" and set(pipelines) != set(PIPELINES):
        errors.append("complete coverage must cover every pipeline")
    if status == "not_collected" and (pipelines or n != 0):
        errors.append("not_collected coverage must be empty")
    for name, entry in pipelines.items():
        value = entry.get("complete_from") if isinstance(entry, dict) else None
        complete_from = _parse_timestamp(value)
        if (
            name not in PIPELINES
            or not isinstance(entry, dict)
            or set(entry) != {"complete_from"}
            or not isinstance(value, str)
            or not _TIMESTAMP_RE.fullmatch(value)
            or complete_from is None
        ):
            errors.append(f"coverage.pipelines.{name} is invalid")
        elif window_from is not None and complete_from > window_from:
            errors.append(f"coverage.pipelines.{name} does not cover the window")
    return errors


def validate_public(payload: object) -> list[str]:
    """Return contract violations; an empty list means the payload is publishable."""
    if not isinstance(payload, dict):
        return ["payload must be an object"]
    if set(payload) != TOP_LEVEL_KEYS:
        return [f"top-level keys {sorted(payload)} differ from the contract"]
    errors: list[str] = []
    if payload["schema_version"] != SCHEMA_VERSION or not _is_int(payload["schema_version"]):
        errors.append("schema_version is unsupported")
    window = payload["window"]
    generated_at = payload["generated_at"]
    window_from: datetime | None = None
    if not isinstance(generated_at, str) or not _TIMESTAMP_RE.fullmatch(generated_at):
        errors.append("generated_at must be a UTC second timestamp")
    if not isinstance(window, dict) or set(window) != {"days", "from", "to"}:
        errors.append("window keys differ from the contract")
    elif not all(
        isinstance(window[key], str) and _TIMESTAMP_RE.fullmatch(window[key])
        for key in ("from", "to")
    ):
        errors.append("window bounds must be UTC second timestamps")
    elif not _is_int(window["days"]) or not 1 <= window["days"] <= RETENTION_DAYS:
        errors.append("window.days is out of range")
    else:
        window_from = _parse_timestamp(window["from"])
        window_to = _parse_timestamp(window["to"])
        if window["to"] != generated_at:
            errors.append("window.to must equal generated_at")
        coverage = payload["coverage"]
        seed = isinstance(coverage, dict) and coverage.get("status") == "not_collected"
        span = timedelta(0) if seed else timedelta(days=window["days"])
        if window_from is None or window_to is None or window_from != window_to - span:
            errors.append("window.from does not match window.days")
    if payload["dims"] != DIMS:
        errors.append("dims differ from the contract")
    jobs = payload["jobs"]
    if not isinstance(jobs, dict):
        errors.append("jobs must be an object")
        jobs = {}
    else:
        errors.extend(_validate_jobs(jobs))
    coverage = payload["coverage"]
    if not isinstance(coverage, dict):
        errors.append("coverage must be an object")
    else:
        errors.extend(_validate_coverage(coverage, window_from, jobs.get("n")))
    encoded = serialize_public(payload)
    text = encoded.decode().lower()
    for marker in _FORBIDDEN_MARKERS:
        if marker in text:
            errors.append(f"payload contains forbidden marker {marker!r}")
    if len(encoded) > PUBLIC_OUTPUT_MAX_BYTES:
        errors.append(f"payload is {len(encoded)} bytes, above {PUBLIC_OUTPUT_MAX_BYTES}")
    return errors


def decode_public(payload: dict) -> list[JobRow]:
    errors = validate_public(payload)
    if errors:
        raise ValueError("invalid docker_builds payload: " + "; ".join(errors))
    window_from = _parse_timestamp(payload["window"]["from"])
    assert window_from is not None
    jobs = payload["jobs"]
    return [
        JobRow(
            id=jobs["id"][i],
            build=jobs["build"][i],
            pipeline=PIPELINES[jobs["pipeline"][i]],
            step=STEP_KEYS[jobs["step"][i]],
            state=STATES[jobs["state"][i]],
            source=SOURCES[jobs["source"][i]],
            retried=bool(jobs["retried"][i]),
            created_at=window_from + timedelta(seconds=jobs["created"][i]),
            wait_s=jobs["wait_s"][i],
            run_s=jobs["run_s"][i],
            exit=jobs["exit"][i],
        )
        for i in range(jobs["n"])
    ]
