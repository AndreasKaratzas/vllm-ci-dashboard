#!/usr/bin/env python3
"""Durably gate request-bearing dashboard producers before token exposure.

Each configured producer owns a parentless, exact-one-file branch.  A runtime
attempt reserves both one execution slot and a fixed Buildkite API request
allowance before any process can see a Buildkite token.  Reservations survive
failure and cancellation for 25 hours.  A separate local transport guard
enforces the fixed allowance; this ledger composes those per-run bounds across
all schedule, webhook, and manual triggers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 1
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
SAFE_BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}")
SAFE_PRODUCER_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
SAFE_EVENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
RUNTIME_ALLOWANCE_CEILINGS = {
    "data_collection": 800,
    "queue_lifecycle": 100,
}


class AttemptBudgetError(RuntimeError):
    """The durable request-bearing-attempt budget is not trustworthy."""


@dataclass(frozen=True)
class AttemptPolicy:
    producer: str
    branch: str
    ledger_path: str
    window_hours: int
    max_request_bearing_attempts: int
    success_interval_minutes: int
    failed_retry_interval_minutes: int
    request_start_allowance: int
    max_migration_overlap_attempts: int
    max_migration_runtime_attempts: int
    max_legacy_seed_attempts: int
    max_ledger_bytes: int


@dataclass(frozen=True)
class ValidatedLedger:
    commit_sha: str
    tree_sha: str
    ledger: Mapping[str, Any]


def _json_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _decode_json(payload: bytes, *, label: str) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_json_no_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AttemptBudgetError(f"{label} is not strict UTF-8 JSON: {exc}") from exc


def _positive_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AttemptBudgetError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AttemptBudgetError(f"{label} must be a nonnegative integer")
    return value


def _safe_branch(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not SAFE_BRANCH_RE.fullmatch(value):
        raise AttemptBudgetError(f"{label} is not a safe branch name")
    if (
        value.startswith("-")
        or value.endswith((".", "/", ".lock"))
        or ".." in value
        or "@{" in value
        or "//" in value
        or any(part.startswith(".") for part in value.split("/"))
    ):
        raise AttemptBudgetError(f"{label} is not a canonical branch name")
    return value


def _safe_ledger_path(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 200
        or "/" in value
        or "\\" in value
        or value in {".", "..", ".git"}
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise AttemptBudgetError("ledger_path must be one safe top-level file")
    return value


def _full_sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not FULL_SHA_RE.fullmatch(value):
        raise AttemptBudgetError(f"{label} must be one full lowercase SHA-1")
    return value


def _safe_revision(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 300
        or value.startswith("-")
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        raise AttemptBudgetError(f"{label} is not a safe Git revision")
    return value


def _timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 32:
        raise AttemptBudgetError(f"{label} must be a bounded timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AttemptBudgetError(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.microsecond:
        raise AttemptBudgetError(f"{label} must be canonical whole-second UTC")
    parsed = parsed.astimezone(timezone.utc)
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise AttemptBudgetError(f"{label} must be canonical whole-second UTC")
    return parsed


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _clock(value: str | None) -> datetime:
    return _timestamp(value, label="--now") if value else datetime.now(timezone.utc).replace(
        microsecond=0
    )


def load_policy(path: Path) -> AttemptPolicy:
    try:
        raw = _decode_json(path.read_bytes(), label=str(path))
    except OSError as exc:
        raise AttemptBudgetError(f"attempt budget config is unreadable: {exc}") from exc
    expected = {
        "schema_version",
        "producer",
        "branch",
        "ledger_path",
        "window_hours",
        "max_request_bearing_attempts",
        "success_interval_minutes",
        "failed_retry_interval_minutes",
        "request_start_allowance",
        "max_migration_overlap_attempts",
        "max_migration_runtime_attempts",
        "max_legacy_seed_attempts",
        "max_ledger_bytes",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise AttemptBudgetError("attempt budget config has an unexpected shape")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise AttemptBudgetError("attempt budget config schema is unsupported")
    producer = raw.get("producer")
    if not isinstance(producer, str) or not SAFE_PRODUCER_RE.fullmatch(producer):
        raise AttemptBudgetError("producer is not a safe bounded identifier")
    if producer not in RUNTIME_ALLOWANCE_CEILINGS:
        raise AttemptBudgetError("producer does not have an audited allowance ceiling")
    policy = AttemptPolicy(
        producer=producer,
        branch=_safe_branch(raw.get("branch"), label="branch"),
        ledger_path=_safe_ledger_path(raw.get("ledger_path")),
        window_hours=_positive_int(raw.get("window_hours"), label="window_hours"),
        max_request_bearing_attempts=_positive_int(
            raw.get("max_request_bearing_attempts"),
            label="max_request_bearing_attempts",
        ),
        success_interval_minutes=_positive_int(
            raw.get("success_interval_minutes"), label="success_interval_minutes"
        ),
        failed_retry_interval_minutes=_positive_int(
            raw.get("failed_retry_interval_minutes"),
            label="failed_retry_interval_minutes",
        ),
        request_start_allowance=_positive_int(
            raw.get("request_start_allowance"), label="request_start_allowance"
        ),
        max_migration_overlap_attempts=_positive_int(
            raw.get("max_migration_overlap_attempts"),
            label="max_migration_overlap_attempts",
        ),
        max_migration_runtime_attempts=_positive_int(
            raw.get("max_migration_runtime_attempts"),
            label="max_migration_runtime_attempts",
        ),
        max_legacy_seed_attempts=_positive_int(
            raw.get("max_legacy_seed_attempts"), label="max_legacy_seed_attempts"
        ),
        max_ledger_bytes=_positive_int(
            raw.get("max_ledger_bytes"), label="max_ledger_bytes"
        ),
    )
    if policy.window_hours != 25:
        raise AttemptBudgetError("window_hours must remain exactly 25")
    if policy.max_request_bearing_attempts > 16:
        raise AttemptBudgetError("request-bearing attempt cap may not exceed 16")
    if policy.success_interval_minutes < 120:
        raise AttemptBudgetError("successful cadence may not be more frequent than two hours")
    if policy.failed_retry_interval_minutes < 30:
        raise AttemptBudgetError("failed retry cadence may not be more frequent than 30 minutes")
    if policy.request_start_allowance > RUNTIME_ALLOWANCE_CEILINGS[producer]:
        raise AttemptBudgetError("request-start allowance exceeds its audited producer ceiling")
    if (
        policy.max_migration_overlap_attempts > 19
        or policy.max_migration_overlap_attempts < policy.max_request_bearing_attempts
        or policy.max_migration_runtime_attempts > policy.max_request_bearing_attempts
        or policy.max_migration_runtime_attempts
            > policy.max_migration_overlap_attempts
    ):
        raise AttemptBudgetError("migration overlap exceeds its audited cutover bounds")
    if policy.max_legacy_seed_attempts > 128 or policy.max_ledger_bytes > 1024 * 1024:
        raise AttemptBudgetError("attempt ledger storage bounds are too large")
    return policy


def _ledger_policy(policy: AttemptPolicy) -> dict[str, Any]:
    return {
        "producer": policy.producer,
        "window_hours": policy.window_hours,
        "max_request_bearing_attempts": policy.max_request_bearing_attempts,
        "success_interval_minutes": policy.success_interval_minutes,
        "failed_retry_interval_minutes": policy.failed_retry_interval_minutes,
        "request_start_allowance": policy.request_start_allowance,
        "max_migration_overlap_attempts": policy.max_migration_overlap_attempts,
        "max_migration_runtime_attempts": policy.max_migration_runtime_attempts,
    }


def _normalize_attempt(
    raw: object,
    *,
    index: int,
    updated_at: datetime,
    cutoff: datetime,
    policy: AttemptPolicy,
) -> dict[str, Any]:
    fields = {
        "id",
        "reserved_at",
        "request_start_allowance",
        "request_start_bound_proven",
        "source",
        "workflow_run_id",
        "workflow_run_attempt",
        "event_name",
        "succeeded_at",
        "durable_ref",
        "actual_request_starts",
    }
    if not isinstance(raw, dict) or set(raw) != fields:
        raise AttemptBudgetError(f"attempt {index} has an unexpected shape")
    attempt_id = raw.get("id")
    if not isinstance(attempt_id, str) or not SAFE_ID_RE.fullmatch(attempt_id):
        raise AttemptBudgetError(f"attempt {index} has an invalid id")
    reserved_at = _timestamp(raw.get("reserved_at"), label=f"attempt {index} reserved_at")
    if reserved_at > updated_at or reserved_at <= cutoff:
        raise AttemptBudgetError(f"attempt {index} lies outside its ledger window")
    allowance = _positive_int(
        raw.get("request_start_allowance"),
        label=f"attempt {index} request_start_allowance",
    )
    if allowance != policy.request_start_allowance:
        raise AttemptBudgetError(f"attempt {index} has the wrong request allowance")
    proven = raw.get("request_start_bound_proven")
    if not isinstance(proven, bool):
        raise AttemptBudgetError(f"attempt {index} bound proof flag is invalid")
    source = raw.get("source")
    if source not in {"runtime", "legacy_migration"}:
        raise AttemptBudgetError(f"attempt {index} source is invalid")
    if source == "runtime" and not proven:
        raise AttemptBudgetError("runtime attempt must have a proven local request guard")
    if source == "legacy_migration" and proven:
        raise AttemptBudgetError("legacy migration attempt cannot claim a runtime guard proof")
    run_id = raw.get("workflow_run_id")
    if not isinstance(run_id, str) or not run_id.isdigit() or len(run_id) > 32:
        raise AttemptBudgetError(f"attempt {index} workflow_run_id is invalid")
    run_attempt = _positive_int(
        raw.get("workflow_run_attempt"), label=f"attempt {index} workflow_run_attempt"
    )
    if run_attempt > 1000:
        raise AttemptBudgetError(f"attempt {index} workflow_run_attempt is too large")
    event_name = raw.get("event_name")
    if not isinstance(event_name, str) or not SAFE_EVENT_RE.fullmatch(event_name):
        raise AttemptBudgetError(f"attempt {index} event_name is invalid")
    succeeded_raw = raw.get("succeeded_at")
    durable_ref = raw.get("durable_ref")
    actual = raw.get("actual_request_starts")
    if succeeded_raw is None:
        if durable_ref is not None or actual is not None:
            raise AttemptBudgetError(f"attempt {index} incomplete success fields disagree")
        succeeded_at = None
    else:
        succeeded = _timestamp(succeeded_raw, label=f"attempt {index} succeeded_at")
        if succeeded < reserved_at or succeeded > updated_at:
            raise AttemptBudgetError(f"attempt {index} success time is invalid")
        succeeded_at = _iso(succeeded)
        _full_sha(durable_ref, label=f"attempt {index} durable_ref")
        if actual is not None:
            actual = _nonnegative_int(
                actual, label=f"attempt {index} actual_request_starts"
            )
            if actual > allowance:
                raise AttemptBudgetError(f"attempt {index} actual starts exceed allowance")
    return {
        "id": attempt_id,
        "reserved_at": _iso(reserved_at),
        "request_start_allowance": allowance,
        "request_start_bound_proven": proven,
        "source": source,
        "workflow_run_id": run_id,
        "workflow_run_attempt": run_attempt,
        "event_name": event_name,
        "succeeded_at": succeeded_at,
        "durable_ref": durable_ref,
        "actual_request_starts": actual,
    }


def _normalize_ledger(value: object, policy: AttemptPolicy) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "updated_at",
        "policy",
        "migration_debt",
        "attempts",
    }:
        raise AttemptBudgetError("attempt ledger has an unexpected shape")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise AttemptBudgetError("attempt ledger schema is unsupported")
    if value.get("policy") != _ledger_policy(policy):
        raise AttemptBudgetError("attempt ledger policy disagrees with config")
    if not isinstance(value.get("migration_debt"), bool):
        raise AttemptBudgetError("migration_debt must be a boolean")
    updated_at = _timestamp(value.get("updated_at"), label="updated_at")
    raw_attempts = value.get("attempts")
    if (
        not isinstance(raw_attempts, list)
        or not raw_attempts
        or len(raw_attempts) > policy.max_legacy_seed_attempts
    ):
        raise AttemptBudgetError("attempts must be a nonempty bounded array")
    cutoff = updated_at - timedelta(hours=policy.window_hours)
    attempts = [
        _normalize_attempt(
            row,
            index=index,
            updated_at=updated_at,
            cutoff=cutoff,
            policy=policy,
        )
        for index, row in enumerate(raw_attempts)
    ]
    if len({row["id"] for row in attempts}) != len(attempts):
        raise AttemptBudgetError("attempt ids must be unique")
    ordered = sorted(attempts, key=lambda row: (row["reserved_at"], row["id"]))
    if attempts != ordered:
        raise AttemptBudgetError("attempts are not in canonical order")
    debt_required = (
        len(attempts) > policy.max_request_bearing_attempts
        or any(not row["request_start_bound_proven"] for row in attempts)
    )
    if value["migration_debt"] != debt_required:
        raise AttemptBudgetError("migration_debt does not match active attempt telemetry")
    legacy = [row for row in attempts if row["source"] == "legacy_migration"]
    runtime = [row for row in attempts if row["source"] == "runtime"]
    if legacy:
        if (
            len(attempts) > policy.max_migration_overlap_attempts
            or len(runtime) > policy.max_migration_runtime_attempts
        ):
            raise AttemptBudgetError("migration overlap exceeds its audited cutover cap")
    elif len(runtime) > policy.max_request_bearing_attempts:
        raise AttemptBudgetError("runtime attempt ledger exceeds its rolling cap")
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _iso(updated_at),
        "policy": _ledger_policy(policy),
        "migration_debt": debt_required,
        "attempts": ordered,
    }


def _ledger_bytes(value: object, policy: AttemptPolicy) -> bytes:
    normalized = _normalize_ledger(value, policy)
    payload = (json.dumps(normalized, indent=2, sort_keys=True) + "\n").encode()
    if len(payload) > policy.max_ledger_bytes:
        raise AttemptBudgetError("attempt ledger exceeds its byte limit")
    return payload


def _git(
    root: Path,
    *args: str,
    input_bytes: bytes | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> bytes:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if extra_env:
        env.update(extra_env)
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()[-2000:]
        raise AttemptBudgetError(
            f"git {' '.join(args[:3])} failed" + (f": {detail}" if detail else "")
        ) from exc
    return completed.stdout


def _resolve_commit(root: Path, ref: str) -> str:
    ref = _safe_revision(ref, label="ledger ref")
    try:
        output = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    except AttemptBudgetError as exc:
        raise AttemptBudgetError("attempt ledger ref does not resolve to a commit") from exc
    return _full_sha(output.decode("ascii").strip(), label="ledger commit")


def validate_ledger_ref(root: Path, ref: str, policy: AttemptPolicy) -> ValidatedLedger:
    root = root.resolve()
    commit_sha = _resolve_commit(root, ref)
    headers = _git(root, "cat-file", "commit", commit_sha).split(b"\n\n", 1)[0].splitlines()
    if any(line.startswith(b"parent ") for line in headers):
        raise AttemptBudgetError("attempt ledger commit must be parentless")
    tree_sha = _full_sha(
        _git(root, "rev-parse", f"{commit_sha}^{{tree}}").decode("ascii").strip(),
        label="ledger tree",
    )
    records = [
        row
        for row in _git(root, "ls-tree", "-r", "-z", "-l", commit_sha).split(b"\0")
        if row
    ]
    if len(records) != 1:
        raise AttemptBudgetError("attempt ledger tree must contain exactly one file")
    try:
        metadata, raw_path = records[0].split(b"\t", 1)
        mode, kind, object_id, raw_size = metadata.split()
        path = raw_path.decode("utf-8")
        size = int(raw_size)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AttemptBudgetError("attempt ledger tree entry is malformed") from exc
    if path != policy.ledger_path or mode != b"100644" or kind != b"blob":
        raise AttemptBudgetError("attempt ledger tree contains an unexpected entry")
    if size <= 0 or size > policy.max_ledger_bytes:
        raise AttemptBudgetError("attempt ledger blob exceeds its byte limit")
    blob_sha = _full_sha(object_id.decode("ascii"), label="ledger blob")
    payload = _git(root, "cat-file", "blob", blob_sha)
    if len(payload) != size:
        raise AttemptBudgetError("attempt ledger blob changed during validation")
    ledger = _normalize_ledger(_decode_json(payload, label="attempt ledger"), policy)
    if payload != _ledger_bytes(ledger, policy):
        raise AttemptBudgetError("attempt ledger is not canonically encoded")
    return ValidatedLedger(commit_sha=commit_sha, tree_sha=tree_sha, ledger=ledger)


def _create_commit(
    root: Path,
    ledger: Mapping[str, Any],
    policy: AttemptPolicy,
) -> ValidatedLedger:
    payload = _ledger_bytes(ledger, policy)
    blob_sha = _full_sha(
        _git(root, "hash-object", "-w", "--stdin", input_bytes=payload)
        .decode("ascii")
        .strip(),
        label="created ledger blob",
    )
    tree_sha = _full_sha(
        _git(
            root,
            "mktree",
            input_bytes=f"100644 blob {blob_sha}\t{policy.ledger_path}\n".encode(),
        )
        .decode("ascii")
        .strip(),
        label="created ledger tree",
    )
    updated_at = str(ledger["updated_at"])
    env = {
        "GIT_AUTHOR_NAME": "github-actions[bot]",
        "GIT_AUTHOR_EMAIL": "github-actions[bot]@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "github-actions[bot]",
        "GIT_COMMITTER_EMAIL": "github-actions[bot]@users.noreply.github.com",
        "GIT_AUTHOR_DATE": updated_at,
        "GIT_COMMITTER_DATE": updated_at,
    }
    created_sha = _full_sha(
        _git(
            root,
            "commit-tree",
            tree_sha,
            input_bytes=(
                f"budget: {policy.producer} request-bearing attempt {updated_at}\n"
            ).encode(),
            extra_env=env,
        )
        .decode("ascii")
        .strip(),
        label="created ledger commit",
    )
    created = validate_ledger_ref(root, created_sha, policy)
    if created.tree_sha != tree_sha:
        raise AttemptBudgetError("created attempt ledger tree changed")
    return created


def _safe_remote(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 500
        or value.startswith("-")
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        raise AttemptBudgetError("remote must be a safe non-option name or URL")
    return value


def _remote_sha(root: Path, remote: str, policy: AttemptPolicy) -> str | None:
    remote = _safe_remote(remote)
    ref = f"refs/heads/{policy.branch}"
    rows = [
        line.split()
        for line in _git(root, "ls-remote", "--refs", remote, ref)
        .decode("ascii")
        .splitlines()
        if line.strip()
    ]
    if not rows:
        return None
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != ref:
        raise AttemptBudgetError("remote returned an ambiguous attempt ledger ref")
    return _full_sha(rows[0][0], label="remote ledger commit")


def _fetch_observed(
    root: Path,
    remote: str,
    observed_sha: str,
    policy: AttemptPolicy,
) -> ValidatedLedger:
    ref = f"refs/heads/{policy.branch}"
    _git(root, "fetch", "--no-tags", "--depth=1", remote, ref)
    fetched = _resolve_commit(root, "FETCH_HEAD")
    if fetched != observed_sha:
        raise AttemptBudgetError("attempt ledger changed between lookup and fetch")
    return validate_ledger_ref(root, fetched, policy)


def _push(
    root: Path,
    remote: str,
    new_sha: str,
    expected_sha: str | None,
    policy: AttemptPolicy,
) -> None:
    ref = f"refs/heads/{policy.branch}"
    _git(
        root,
        "push",
        f"--force-with-lease={ref}:{expected_sha or ''}",
        remote,
        f"{new_sha}:{ref}",
    )
    if _remote_sha(root, remote, policy) != new_sha:
        raise AttemptBudgetError("attempt ledger post-push verification disagreed")


def _active_attempts(
    ledger: Mapping[str, Any], *, now: datetime, policy: AttemptPolicy
) -> list[dict[str, Any]]:
    updated_at = _timestamp(ledger["updated_at"], label="ledger updated_at")
    if updated_at > now:
        raise AttemptBudgetError("attempt ledger is from the future")
    cutoff = now - timedelta(hours=policy.window_hours)
    return [
        dict(row)
        for row in ledger["attempts"]
        if _timestamp(row["reserved_at"], label="reserved_at") > cutoff
    ]


def _new_ledger(
    attempts: list[dict[str, Any]], *, now: datetime, policy: AttemptPolicy
) -> dict[str, Any]:
    attempts.sort(key=lambda row: (row["reserved_at"], row["id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _iso(now),
        "policy": _ledger_policy(policy),
        "migration_debt": (
            len(attempts) > policy.max_request_bearing_attempts
            or any(not row["request_start_bound_proven"] for row in attempts)
        ),
        "attempts": attempts,
    }


def _attempt_row(
    *,
    attempt_id: str,
    reserved_at: datetime,
    policy: AttemptPolicy,
    source: str,
    bound_proven: bool,
    workflow_run_id: str,
    workflow_run_attempt: int,
    event_name: str,
    succeeded_at: datetime | None = None,
    durable_ref: str | None = None,
    actual_request_starts: int | None = None,
) -> dict[str, Any]:
    return {
        "id": attempt_id,
        "reserved_at": _iso(reserved_at),
        "request_start_allowance": policy.request_start_allowance,
        "request_start_bound_proven": bound_proven,
        "source": source,
        "workflow_run_id": workflow_run_id,
        "workflow_run_attempt": workflow_run_attempt,
        "event_name": event_name,
        "succeeded_at": _iso(succeeded_at) if succeeded_at else None,
        "durable_ref": durable_ref,
        "actual_request_starts": actual_request_starts,
    }


def _request_mode(
    attempts: list[dict[str, Any]], *, now: datetime, policy: AttemptPolicy
) -> tuple[str, datetime | None]:
    """Return the authoritative mode without mutating the durable ledger.

    Successful cadence is deliberately start-to-start.  Basing it on durable
    completion would add collection runtime to every interval (a 25-minute run
    on a two-hour cron would otherwise execute only every four hours).
    """
    latest = max(attempts, key=lambda row: (row["reserved_at"], row["id"]), default=None)
    if latest is not None:
        reserved_at = _timestamp(latest["reserved_at"], label="latest reserved_at")
        interval = (
            policy.success_interval_minutes
            if latest["succeeded_at"] is not None
            else policy.failed_retry_interval_minutes
        )
        available = reserved_at + timedelta(minutes=interval)
        if now < available:
            return (
                "success_gated" if latest["succeeded_at"] is not None else "retry_gated",
                available,
            )

    legacy = [row for row in attempts if row["source"] == "legacy_migration"]
    runtime = [row for row in attempts if row["source"] == "runtime"]
    blocked_until: list[datetime] = []
    if legacy:
        if len(attempts) >= policy.max_migration_overlap_attempts:
            blocked_until.append(
                min(_timestamp(row["reserved_at"], label="reserved_at") for row in attempts)
                + timedelta(hours=policy.window_hours)
            )
        if len(runtime) >= policy.max_migration_runtime_attempts:
            blocked_until.append(
                min(_timestamp(row["reserved_at"], label="reserved_at") for row in runtime)
                + timedelta(hours=policy.window_hours)
            )
    elif len(runtime) >= policy.max_request_bearing_attempts:
        blocked_until.append(
            min(_timestamp(row["reserved_at"], label="reserved_at") for row in runtime)
            + timedelta(hours=policy.window_hours)
        )
    if blocked_until:
        return "cap_gated", max(blocked_until)
    return "reserved", None


def initialize(
    root: Path,
    policy: AttemptPolicy,
    *,
    seed_file: Path,
    now: datetime,
    remote: str,
) -> dict[str, object]:
    if _remote_sha(root, remote, policy) is not None:
        raise AttemptBudgetError("attempt ledger branch already exists")
    try:
        seeds = _decode_json(seed_file.read_bytes(), label=str(seed_file))
    except OSError as exc:
        raise AttemptBudgetError(f"seed file is unreadable: {exc}") from exc
    if not isinstance(seeds, list) or not seeds or len(seeds) > policy.max_legacy_seed_attempts:
        raise AttemptBudgetError("seed file must contain a nonempty bounded array")
    cutoff = now - timedelta(hours=policy.window_hours)
    attempts: list[dict[str, Any]] = []
    for index, seed in enumerate(seeds, start=1):
        expected = {
            "workflow_run_id",
            "workflow_run_attempt",
            "event_name",
            "reserved_at",
            "request_start_bound_proven",
            "succeeded_at",
            "durable_ref",
            "actual_request_starts",
        }
        if not isinstance(seed, dict) or set(seed) != expected:
            raise AttemptBudgetError(f"seed {index} has an unexpected shape")
        reserved_at = _timestamp(seed["reserved_at"], label=f"seed {index} reserved_at")
        if not cutoff < reserved_at <= now:
            raise AttemptBudgetError(f"seed {index} lies outside the 25-hour window")
        run_id = str(seed["workflow_run_id"])
        run_attempt = seed["workflow_run_attempt"]
        succeeded_at = (
            _timestamp(seed["succeeded_at"], label=f"seed {index} succeeded_at")
            if seed["succeeded_at"] is not None
            else None
        )
        attempt = _attempt_row(
            attempt_id=f"legacy-{run_id}-{run_attempt}",
            reserved_at=reserved_at,
            policy=policy,
            source="legacy_migration",
            bound_proven=seed["request_start_bound_proven"],
            workflow_run_id=run_id,
            workflow_run_attempt=run_attempt,
            event_name=seed["event_name"],
            succeeded_at=succeeded_at,
            durable_ref=seed["durable_ref"],
            actual_request_starts=seed["actual_request_starts"],
        )
        attempts.append(attempt)
    ledger = _new_ledger(attempts, now=now, policy=policy)
    created = _create_commit(root, ledger, policy)
    _push(root, remote, created.commit_sha, None, policy)
    return {
        "budget_sha": created.commit_sha,
        "decision_at": _iso(now),
        "active_attempts": len(attempts),
        "rolling_reserved_request_starts": sum(
            policy.request_start_allowance
            for row in attempts
            if row["request_start_bound_proven"]
        ),
        "active_legacy_attempts": sum(
            1 for row in attempts if row["source"] == "legacy_migration"
        ),
        "migration_debt": str(ledger["migration_debt"]).lower(),
    }


def reserve(
    root: Path,
    policy: AttemptPolicy,
    *,
    attempt_id: str,
    workflow_run_id: str,
    workflow_run_attempt: int,
    event_name: str,
    now: datetime,
    remote: str,
) -> dict[str, object]:
    if not SAFE_ID_RE.fullmatch(attempt_id):
        raise AttemptBudgetError("attempt id is not a safe bounded identifier")
    if not workflow_run_id.isdigit() or len(workflow_run_id) > 32:
        raise AttemptBudgetError("workflow run id is invalid")
    if workflow_run_attempt <= 0 or workflow_run_attempt > 1000:
        raise AttemptBudgetError("workflow run attempt is invalid")
    if not SAFE_EVENT_RE.fullmatch(event_name):
        raise AttemptBudgetError("event name is invalid")
    observed_sha = _remote_sha(root, remote, policy)
    if observed_sha is None:
        raise AttemptBudgetError(
            "attempt ledger branch is absent; initialize it from verified 25-hour telemetry"
        )
    established = _fetch_observed(root, remote, observed_sha, policy)
    attempts = _active_attempts(established.ledger, now=now, policy=policy)
    decision_at = _iso(now)
    base = {
        "decision_at": decision_at,
        "budget_sha": established.commit_sha,
        "attempt_id": attempt_id,
        "request_start_allowance": policy.request_start_allowance,
        "active_attempts": len(attempts),
        "rolling_reserved_request_starts": sum(
            policy.request_start_allowance
            for row in attempts
            if row["request_start_bound_proven"]
        ),
        "active_legacy_attempts": sum(
            1 for row in attempts if row["source"] == "legacy_migration"
        ),
    }
    if any(row["id"] == attempt_id for row in attempts):
        raise AttemptBudgetError("attempt id already exists; refusing permit reuse")
    request_mode, available = _request_mode(attempts, now=now, policy=policy)
    if request_mode != "reserved":
        return {
            **base,
            "request_mode": request_mode,
            "available_at": _iso(available),
        }
    attempts.append(
        _attempt_row(
            attempt_id=attempt_id,
            reserved_at=now,
            policy=policy,
            source="runtime",
            bound_proven=True,
            workflow_run_id=workflow_run_id,
            workflow_run_attempt=workflow_run_attempt,
            event_name=event_name,
        )
    )
    ledger = _new_ledger(attempts, now=now, policy=policy)
    created = _create_commit(root, ledger, policy)
    _push(root, remote, created.commit_sha, observed_sha, policy)
    return {
        "request_mode": "reserved",
        "decision_at": decision_at,
        "budget_sha": created.commit_sha,
        "attempt_id": attempt_id,
        "request_start_allowance": policy.request_start_allowance,
        "active_attempts": len(attempts),
        "rolling_reserved_request_starts": sum(
            policy.request_start_allowance
            for row in attempts
            if row["request_start_bound_proven"]
        ),
        "active_legacy_attempts": sum(
            1 for row in attempts if row["source"] == "legacy_migration"
        ),
        "available_at": "",
    }


def observe(
    root: Path,
    policy: AttemptPolicy,
    *,
    now: datetime,
    remote: str,
) -> dict[str, object]:
    """Read-only coalescing hint for webhook preflights.

    This observation never grants authority.  A request-bearing job must still
    call :func:`reserve` after its concurrency serialization and immediately
    before token exposure.
    """
    observed_sha = _remote_sha(root, remote, policy)
    if observed_sha is None:
        raise AttemptBudgetError(
            "attempt ledger branch is absent; initialize it from verified 25-hour telemetry"
        )
    established = _fetch_observed(root, remote, observed_sha, policy)
    attempts = _active_attempts(established.ledger, now=now, policy=policy)
    request_mode, available = _request_mode(attempts, now=now, policy=policy)
    successful = [row for row in attempts if row["succeeded_at"] is not None]
    latest_success = max(
        successful,
        key=lambda row: (row["succeeded_at"], row["reserved_at"], row["id"]),
        default=None,
    )
    return {
        "observation_valid": "true",
        "required": "true" if request_mode == "reserved" else "false",
        "request_mode": request_mode,
        "available_at": _iso(available) if available is not None else "",
        "budget_sha": established.commit_sha,
        "latest_success_reserved_at": (
            latest_success["reserved_at"] if latest_success is not None else ""
        ),
        "latest_succeeded_at": (
            latest_success["succeeded_at"] if latest_success is not None else ""
        ),
        "latest_durable_ref": (
            latest_success["durable_ref"] if latest_success is not None else ""
        ),
    }


def mark_success(
    root: Path,
    policy: AttemptPolicy,
    *,
    attempt_id: str,
    durable_ref: str,
    actual_request_starts: int,
    now: datetime,
    remote: str,
) -> dict[str, object]:
    if not SAFE_ID_RE.fullmatch(attempt_id):
        raise AttemptBudgetError("attempt id is invalid")
    durable_ref = _full_sha(durable_ref, label="durable ref")
    if actual_request_starts < 0 or actual_request_starts > policy.request_start_allowance:
        raise AttemptBudgetError("actual request starts exceed the reserved allowance")
    observed_sha = _remote_sha(root, remote, policy)
    if observed_sha is None:
        raise AttemptBudgetError("attempt ledger branch disappeared before success marking")
    established = _fetch_observed(root, remote, observed_sha, policy)
    attempts = _active_attempts(established.ledger, now=now, policy=policy)
    matches = [row for row in attempts if row["id"] == attempt_id]
    if len(matches) != 1:
        raise AttemptBudgetError("matching active attempt reservation is not unique")
    row = matches[0]
    if not row["request_start_bound_proven"] or row["source"] != "runtime":
        raise AttemptBudgetError("only a guarded runtime attempt can be marked successful")
    if row["succeeded_at"] is not None:
        if (
            row["durable_ref"] == durable_ref
            and row["actual_request_starts"] == actual_request_starts
        ):
            return {
                "budget_sha": established.commit_sha,
                "attempt_id": attempt_id,
                "succeeded_at": row["succeeded_at"],
                "actual_request_starts": actual_request_starts,
            }
        raise AttemptBudgetError("attempt success was already marked with different evidence")
    row["succeeded_at"] = _iso(now)
    row["durable_ref"] = durable_ref
    row["actual_request_starts"] = actual_request_starts
    ledger = _new_ledger(attempts, now=now, policy=policy)
    created = _create_commit(root, ledger, policy)
    _push(root, remote, created.commit_sha, observed_sha, policy)
    return {
        "budget_sha": created.commit_sha,
        "attempt_id": attempt_id,
        "succeeded_at": _iso(now),
        "actual_request_starts": actual_request_starts,
    }


def _append_outputs(path: Path | None, values: Mapping[str, object]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    if any("\n" in line or "\r" in line for line in lines):
        raise AttemptBudgetError("output values must be single-line")
    payload = "\n".join(lines) + "\n"
    if path is None:
        sys.stdout.write(payload)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("initialize")
    init.add_argument("--seed-file", type=Path, required=True)
    init.add_argument("--now")
    init.add_argument("--remote", default="origin")
    init.add_argument("--github-output", type=Path)

    reserve_parser = subparsers.add_parser("reserve")
    reserve_parser.add_argument("--attempt-id", required=True)
    reserve_parser.add_argument("--workflow-run-id", required=True)
    reserve_parser.add_argument("--workflow-run-attempt", type=int, required=True)
    reserve_parser.add_argument("--event-name", required=True)
    reserve_parser.add_argument("--now")
    reserve_parser.add_argument("--remote", default="origin")
    reserve_parser.add_argument("--github-output", type=Path)

    observe_parser = subparsers.add_parser("observe")
    observe_parser.add_argument("--now")
    observe_parser.add_argument("--remote", default="origin")
    observe_parser.add_argument("--github-output", type=Path)

    success = subparsers.add_parser("mark-success")
    success.add_argument("--attempt-id", required=True)
    success.add_argument("--durable-ref", required=True)
    success.add_argument("--actual-request-starts", type=int, required=True)
    success.add_argument("--now")
    success.add_argument("--remote", default="origin")
    success.add_argument("--github-output", type=Path)

    validate = subparsers.add_parser("validate-ref")
    validate.add_argument("--ref", required=True)
    validate.add_argument("--github-output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = args.root.resolve()
        policy = load_policy(args.config.resolve())
        if args.command == "initialize":
            outputs = initialize(
                root,
                policy,
                seed_file=args.seed_file.resolve(),
                now=_clock(args.now),
                remote=args.remote,
            )
        elif args.command == "reserve":
            outputs = reserve(
                root,
                policy,
                attempt_id=args.attempt_id,
                workflow_run_id=args.workflow_run_id,
                workflow_run_attempt=args.workflow_run_attempt,
                event_name=args.event_name,
                now=_clock(args.now),
                remote=args.remote,
            )
        elif args.command == "observe":
            outputs = observe(
                root,
                policy,
                now=_clock(args.now),
                remote=args.remote,
            )
        elif args.command == "mark-success":
            outputs = mark_success(
                root,
                policy,
                attempt_id=args.attempt_id,
                durable_ref=args.durable_ref,
                actual_request_starts=args.actual_request_starts,
                now=_clock(args.now),
                remote=args.remote,
            )
        else:
            validated = validate_ledger_ref(root, args.ref, policy)
            outputs = {
                "budget_sha": validated.commit_sha,
                "active_attempts": len(validated.ledger["attempts"]),
                "migration_debt": str(validated.ledger["migration_debt"]).lower(),
            }
        _append_outputs(args.github_output, outputs)
    except (AttemptBudgetError, OSError, ValueError) as exc:
        print(f"request-bearing attempt budget error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
