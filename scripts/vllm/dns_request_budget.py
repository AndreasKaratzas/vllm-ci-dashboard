#!/usr/bin/env python3
"""Reserve a durable rolling budget before DNS Buildkite request starts.

The public ``dns-request-budget`` branch contains one small JSON ledger in one
parentless commit.  Every request-bearing DNS attempt reserves its complete
per-run allowance before the collector receives a Buildkite token.  Failed or
interrupted attempts therefore remain charged for the full rolling window.
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

# Support direct execution as ``python scripts/vllm/dns_request_budget.py``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vllm.ci.dns_failures import StateValidationError, load_state, parse_timestamp  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config/dns_request_budget.json"
DEFAULT_STATE_PATH = ROOT / "data/vllm/ci/dns_health/scan_state.json.gz"
SCHEMA_VERSION = 1
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
SAFE_BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
SAFE_RESERVATION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
RESERVATION_KINDS = frozenset({"legacy_seed", "scan"})


class DnsRequestBudgetError(RuntimeError):
    """The durable request budget could not be established safely."""


@dataclass(frozen=True)
class BudgetPolicy:
    branch: str
    ledger_path: str
    window_hours: int
    max_request_starts: int
    scan_reservation_request_starts: int
    max_legacy_seed_request_starts: int
    max_reservations: int
    max_ledger_bytes: int


@dataclass(frozen=True)
class ValidatedBudget:
    commit_sha: str
    tree_sha: str
    ledger: Mapping[str, Any]


def _json_object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
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
            object_pairs_hook=_json_object_no_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise DnsRequestBudgetError(f"{label} is not strict UTF-8 JSON: {exc}") from exc


def _positive_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DnsRequestBudgetError(f"{label} must be a positive integer")
    return value


def _safe_branch(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not SAFE_BRANCH_RE.fullmatch(value):
        raise DnsRequestBudgetError(f"{label} is not a safe branch name")
    if (
        value.startswith("-")
        or value.endswith((".", "/", ".lock"))
        or ".." in value
        or "@{" in value
        or "//" in value
        or any(part.startswith(".") for part in value.split("/"))
    ):
        raise DnsRequestBudgetError(f"{label} is not a canonical branch name")
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
        raise DnsRequestBudgetError("ledger_path must be one safe top-level file")
    return value


def _full_sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not FULL_SHA_RE.fullmatch(value):
        raise DnsRequestBudgetError(f"{label} must be one full lowercase SHA-1")
    return value


def _safe_revision(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 300
        or value.startswith("-")
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        raise DnsRequestBudgetError(f"{label} is not a safe Git revision")
    return value


def _canonical_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 32:
        raise DnsRequestBudgetError(f"{label} must be a bounded timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DnsRequestBudgetError(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.microsecond:
        raise DnsRequestBudgetError(f"{label} must be canonical whole-second UTC")
    parsed = parsed.astimezone(timezone.utc)
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise DnsRequestBudgetError(f"{label} must be canonical whole-second UTC")
    return parsed


def _iso_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clock(value: str | None) -> datetime:
    if value is not None:
        return _canonical_timestamp(value, label="--now")
    return datetime.now(timezone.utc).replace(microsecond=0)


def load_policy(path: Path = DEFAULT_CONFIG_PATH) -> BudgetPolicy:
    try:
        payload = _decode_json(path.read_bytes(), label=str(path))
    except OSError as exc:
        raise DnsRequestBudgetError(f"DNS request budget config is unreadable: {exc}") from exc
    expected = {
        "schema_version",
        "branch",
        "ledger_path",
        "window_hours",
        "max_request_starts",
        "scan_reservation_request_starts",
        "max_legacy_seed_request_starts",
        "max_reservations",
        "max_ledger_bytes",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise DnsRequestBudgetError("DNS request budget config has an unexpected shape")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise DnsRequestBudgetError("DNS request budget config schema is unsupported")
    policy = BudgetPolicy(
        branch=_safe_branch(payload.get("branch"), label="branch"),
        ledger_path=_safe_ledger_path(payload.get("ledger_path")),
        window_hours=_positive_int(payload.get("window_hours"), label="window_hours"),
        max_request_starts=_positive_int(
            payload.get("max_request_starts"), label="max_request_starts"
        ),
        scan_reservation_request_starts=_positive_int(
            payload.get("scan_reservation_request_starts"),
            label="scan_reservation_request_starts",
        ),
        max_legacy_seed_request_starts=_positive_int(
            payload.get("max_legacy_seed_request_starts"),
            label="max_legacy_seed_request_starts",
        ),
        max_reservations=_positive_int(payload.get("max_reservations"), label="max_reservations"),
        max_ledger_bytes=_positive_int(payload.get("max_ledger_bytes"), label="max_ledger_bytes"),
    )
    if policy.window_hours > 168:
        raise DnsRequestBudgetError("window_hours exceeds the seven-day safety limit")
    if policy.scan_reservation_request_starts > policy.max_request_starts:
        raise DnsRequestBudgetError("one scan reservation exceeds the rolling budget")
    if policy.max_reservations > 256 or policy.max_ledger_bytes > 1024 * 1024:
        raise DnsRequestBudgetError("DNS request ledger storage bounds are too large")
    return policy


def _normalize_ledger(value: object, policy: BudgetPolicy) -> dict[str, Any]:
    expected = {
        "schema_version",
        "updated_at",
        "policy",
        "migration_debt",
        "reservations",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise DnsRequestBudgetError("DNS request budget ledger has an unexpected shape")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise DnsRequestBudgetError("DNS request budget ledger schema is unsupported")
    expected_policy = {
        "window_hours": policy.window_hours,
        "max_request_starts": policy.max_request_starts,
        "scan_reservation_request_starts": policy.scan_reservation_request_starts,
    }
    if value.get("policy") != expected_policy:
        raise DnsRequestBudgetError("DNS request budget ledger policy disagrees with config")
    if not isinstance(value.get("migration_debt"), bool):
        raise DnsRequestBudgetError("migration_debt must be a boolean")
    updated_at = _canonical_timestamp(value.get("updated_at"), label="updated_at")
    raw_reservations = value.get("reservations")
    if (
        not isinstance(raw_reservations, list)
        or not raw_reservations
        or len(raw_reservations) > policy.max_reservations
    ):
        raise DnsRequestBudgetError("reservations must be a nonempty bounded array")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    cutoff = updated_at - timedelta(hours=policy.window_hours)
    for index, raw in enumerate(raw_reservations):
        if not isinstance(raw, dict) or set(raw) != {
            "id",
            "kind",
            "reserved_at",
            "request_starts",
        }:
            raise DnsRequestBudgetError(f"reservation {index} has an unexpected shape")
        reservation_id = raw.get("id")
        if (
            not isinstance(reservation_id, str)
            or not SAFE_RESERVATION_ID_RE.fullmatch(reservation_id)
            or reservation_id in seen_ids
        ):
            raise DnsRequestBudgetError(f"reservation {index} has an invalid or duplicate id")
        seen_ids.add(reservation_id)
        kind = raw.get("kind")
        if kind not in RESERVATION_KINDS:
            raise DnsRequestBudgetError(f"reservation {index} has an invalid kind")
        request_starts = _positive_int(
            raw.get("request_starts"), label=f"reservation {index} request_starts"
        )
        if kind == "scan" and request_starts != policy.scan_reservation_request_starts:
            raise DnsRequestBudgetError("scan reservation does not equal the per-run allowance")
        if kind == "legacy_seed" and request_starts > policy.max_legacy_seed_request_starts:
            raise DnsRequestBudgetError("legacy seed exceeds its bounded per-attempt limit")
        reserved_at = _canonical_timestamp(
            raw.get("reserved_at"), label=f"reservation {index} reserved_at"
        )
        if reserved_at > updated_at or reserved_at <= cutoff:
            raise DnsRequestBudgetError("ledger contains a reservation outside its creation window")
        normalized.append(
            {
                "id": reservation_id,
                "kind": kind,
                "reserved_at": _iso_timestamp(reserved_at),
                "request_starts": request_starts,
            }
        )

    ordered = sorted(normalized, key=lambda row: (row["reserved_at"], row["id"]))
    if normalized != ordered:
        raise DnsRequestBudgetError("reservations are not in canonical order")
    total = sum(row["request_starts"] for row in ordered)
    migration_debt = value["migration_debt"]
    if migration_debt:
        if total <= policy.max_request_starts or any(
            row["kind"] != "legacy_seed" for row in ordered
        ):
            raise DnsRequestBudgetError("migration debt must be over-cap legacy telemetry only")
        if total > policy.max_legacy_seed_request_starts * policy.max_reservations:
            raise DnsRequestBudgetError("migration debt exceeds its bounded ceiling")
    elif total > policy.max_request_starts:
        raise DnsRequestBudgetError("runtime ledger exceeds the rolling request budget")
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _iso_timestamp(updated_at),
        "policy": expected_policy,
        "migration_debt": migration_debt,
        "reservations": ordered,
    }


def _ledger_bytes(value: object, policy: BudgetPolicy) -> bytes:
    ledger = _normalize_ledger(value, policy)
    encoded = (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > policy.max_ledger_bytes:
        raise DnsRequestBudgetError("DNS request budget ledger exceeds its byte limit")
    return encoded


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
        result = subprocess.run(
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
        command = " ".join(("git", *args[:3]))
        raise DnsRequestBudgetError(
            f"{command} failed" + (f": {detail}" if detail else "")
        ) from exc
    return result.stdout


def _resolve_commit(root: Path, ref: str) -> str:
    ref = _safe_revision(ref, label="budget ref")
    try:
        resolved = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    except DnsRequestBudgetError as exc:
        raise DnsRequestBudgetError("budget ref does not resolve to a commit") from exc
    return _full_sha(resolved.decode("ascii").strip(), label="budget commit")


def validate_budget_ref(root: Path, ref: str, policy: BudgetPolicy) -> ValidatedBudget:
    """Validate one parentless exact one-file budget commit."""
    root = root.resolve()
    commit_sha = _resolve_commit(root, ref)
    commit_payload = _git(root, "cat-file", "commit", commit_sha)
    headers = commit_payload.split(b"\n\n", 1)[0].splitlines()
    if any(line.startswith(b"parent ") for line in headers):
        raise DnsRequestBudgetError("DNS request budget commit must be parentless")
    tree_sha = _full_sha(
        _git(root, "rev-parse", f"{commit_sha}^{{tree}}").decode("ascii").strip(),
        label="budget tree",
    )
    raw_tree = _git(root, "ls-tree", "-r", "-z", "-l", commit_sha)
    records = [record for record in raw_tree.split(b"\0") if record]
    if len(records) != 1:
        raise DnsRequestBudgetError("DNS request budget tree must contain exactly one file")
    try:
        metadata, encoded_path = records[0].split(b"\t", 1)
        mode, kind, object_id, raw_size = metadata.split()
        path = encoded_path.decode("utf-8")
        size = int(raw_size)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DnsRequestBudgetError("DNS request budget tree entry is malformed") from exc
    if path != policy.ledger_path or mode != b"100644" or kind != b"blob":
        raise DnsRequestBudgetError("DNS request budget tree contains an unexpected entry")
    if size <= 0 or size > policy.max_ledger_bytes:
        raise DnsRequestBudgetError("DNS request budget blob exceeds its byte limit")
    object_sha = _full_sha(object_id.decode("ascii"), label="budget blob")
    payload = _git(root, "cat-file", "blob", object_sha)
    if len(payload) != size:
        raise DnsRequestBudgetError("DNS request budget blob changed during validation")
    ledger = _normalize_ledger(_decode_json(payload, label="DNS request ledger"), policy)
    if _ledger_bytes(ledger, policy) != payload:
        raise DnsRequestBudgetError("DNS request budget ledger is not canonically encoded")
    return ValidatedBudget(commit_sha=commit_sha, tree_sha=tree_sha, ledger=ledger)


def _create_parentless_commit(
    root: Path,
    ledger: Mapping[str, Any],
    policy: BudgetPolicy,
) -> ValidatedBudget:
    payload = _ledger_bytes(ledger, policy)
    blob_sha = _full_sha(
        _git(root, "hash-object", "-w", "--stdin", input_bytes=payload).decode("ascii").strip(),
        label="created budget blob",
    )
    tree_input = f"100644 blob {blob_sha}\t{policy.ledger_path}\n".encode("utf-8")
    tree_sha = _full_sha(
        _git(root, "mktree", input_bytes=tree_input).decode("ascii").strip(),
        label="created budget tree",
    )
    updated_at = str(ledger["updated_at"])
    commit_env = {
        "GIT_AUTHOR_NAME": "github-actions[bot]",
        "GIT_AUTHOR_EMAIL": "github-actions[bot]@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "github-actions[bot]",
        "GIT_COMMITTER_EMAIL": "github-actions[bot]@users.noreply.github.com",
        "GIT_AUTHOR_DATE": updated_at,
        "GIT_COMMITTER_DATE": updated_at,
    }
    message = f"dns: reserve request budget {updated_at}\n".encode("utf-8")
    commit_sha = _full_sha(
        _git(
            root,
            "commit-tree",
            tree_sha,
            input_bytes=message,
            extra_env=commit_env,
        )
        .decode("ascii")
        .strip(),
        label="created budget commit",
    )
    created = validate_budget_ref(root, commit_sha, policy)
    if created.tree_sha != tree_sha:
        raise DnsRequestBudgetError("created DNS request budget tree changed")
    return created


def _safe_remote(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 500
        or value.startswith("-")
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        raise DnsRequestBudgetError("remote must be a safe non-option name or URL")
    return value


def _remote_ref_sha(root: Path, remote: str, policy: BudgetPolicy) -> str | None:
    remote = _safe_remote(remote)
    ref = f"refs/heads/{policy.branch}"
    output = _git(root, "ls-remote", "--refs", remote, ref).decode("ascii")
    rows = [line.split() for line in output.splitlines() if line.strip()]
    if not rows:
        return None
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != ref:
        raise DnsRequestBudgetError("remote returned an ambiguous DNS request budget ref")
    return _full_sha(rows[0][0], label="remote budget commit")


def _fetch_observed_budget(
    root: Path,
    remote: str,
    observed_sha: str,
    policy: BudgetPolicy,
) -> ValidatedBudget:
    ref = f"refs/heads/{policy.branch}"
    _git(root, "fetch", "--no-tags", "--depth=1", remote, ref)
    fetched_sha = _resolve_commit(root, "FETCH_HEAD")
    if fetched_sha != observed_sha:
        raise DnsRequestBudgetError("DNS request budget changed between lookup and fetch")
    return validate_budget_ref(root, fetched_sha, policy)


def _push_budget(
    root: Path,
    remote: str,
    new_sha: str,
    expected_sha: str | None,
    policy: BudgetPolicy,
) -> None:
    ref = f"refs/heads/{policy.branch}"
    lease = expected_sha or ""
    _git(
        root,
        "push",
        f"--force-with-lease={ref}:{lease}",
        remote,
        f"{new_sha}:{ref}",
    )
    verified = _remote_ref_sha(root, remote, policy)
    if verified != new_sha:
        raise DnsRequestBudgetError(
            "DNS request budget push completed but post-push verification disagreed"
        )


def _parse_seed(value: str, *, index: int, policy: BudgetPolicy) -> dict[str, Any]:
    if "=" not in value:
        raise DnsRequestBudgetError("--seed must use TIMESTAMP=REQUEST_STARTS")
    raw_timestamp, raw_amount = value.rsplit("=", 1)
    reserved_at = _canonical_timestamp(raw_timestamp, label=f"seed {index} timestamp")
    try:
        amount = int(raw_amount)
    except ValueError as exc:
        raise DnsRequestBudgetError(f"seed {index} request starts are invalid") from exc
    if amount <= 0 or amount > policy.max_legacy_seed_request_starts:
        raise DnsRequestBudgetError(f"seed {index} request starts exceed the bounded limit")
    compact = reserved_at.strftime("%Y%m%dT%H%M%SZ")
    return {
        "id": f"legacy-{compact}-{index:02d}",
        "kind": "legacy_seed",
        "reserved_at": _iso_timestamp(reserved_at),
        "request_starts": amount,
    }


def initialize_budget(
    root: Path,
    policy: BudgetPolicy,
    *,
    seeds: Sequence[str],
    now: datetime,
    remote: str = "origin",
) -> dict[str, object]:
    """Create the missing branch from operator-verified rolling telemetry."""
    root = root.resolve()
    if not seeds:
        raise DnsRequestBudgetError("initialization requires at least one verified --seed")
    if len(seeds) > policy.max_reservations:
        raise DnsRequestBudgetError("initial seed count exceeds the ledger limit")
    if _remote_ref_sha(root, remote, policy) is not None:
        raise DnsRequestBudgetError("DNS request budget branch already exists")
    rows = [_parse_seed(seed, index=index + 1, policy=policy) for index, seed in enumerate(seeds)]
    cutoff = now - timedelta(hours=policy.window_hours)
    if any(
        not cutoff < _canonical_timestamp(row["reserved_at"], label="seed reserved_at") <= now
        for row in rows
    ):
        raise DnsRequestBudgetError("initial seed lies outside the rolling window")
    rows.sort(key=lambda row: (row["reserved_at"], row["id"]))
    total = sum(row["request_starts"] for row in rows)
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _iso_timestamp(now),
        "policy": {
            "window_hours": policy.window_hours,
            "max_request_starts": policy.max_request_starts,
            "scan_reservation_request_starts": policy.scan_reservation_request_starts,
        },
        "migration_debt": total > policy.max_request_starts,
        "reservations": rows,
    }
    created = _create_parentless_commit(root, ledger, policy)
    _push_budget(root, remote, created.commit_sha, None, policy)
    return {
        "budget_sha": created.commit_sha,
        "decision_at": _iso_timestamp(now),
        "rolling_reserved_starts": total,
        "remaining_request_starts": max(0, policy.max_request_starts - total),
        "migration_debt": str(total > policy.max_request_starts).lower(),
    }


def _scanner_requires_requests(
    state_path: Path,
    *,
    now: datetime,
    minimum_interval_hours: int,
) -> bool:
    if (
        isinstance(minimum_interval_hours, bool)
        or not isinstance(minimum_interval_hours, int)
        or minimum_interval_hours < 0
    ):
        raise DnsRequestBudgetError("minimum interval must be a non-negative integer")
    state = load_state(state_path)
    if state is None:
        return True
    generated_at = parse_timestamp(state["generated_at"], "generated_at")
    if generated_at > now:
        raise DnsRequestBudgetError("DNS scanner state generated_at is in the future")
    return now - generated_at >= timedelta(hours=minimum_interval_hours)


def _active_rows(
    ledger: Mapping[str, Any],
    *,
    now: datetime,
    policy: BudgetPolicy,
) -> list[dict[str, Any]]:
    updated_at = _canonical_timestamp(ledger["updated_at"], label="ledger updated_at")
    if updated_at > now:
        raise DnsRequestBudgetError("DNS request budget ledger is from the future")
    cutoff = now - timedelta(hours=policy.window_hours)
    return [
        dict(row)
        for row in ledger["reservations"]
        if _canonical_timestamp(row["reserved_at"], label="reserved_at") > cutoff
    ]


def _next_available_at(
    rows: Sequence[Mapping[str, Any]],
    *,
    policy: BudgetPolicy,
) -> datetime:
    remaining = sum(int(row["request_starts"]) for row in rows)
    by_expiry: dict[datetime, int] = {}
    for row in rows:
        expiry = _canonical_timestamp(row["reserved_at"], label="reserved_at") + timedelta(
            hours=policy.window_hours
        )
        by_expiry[expiry] = by_expiry.get(expiry, 0) + int(row["request_starts"])
    for expiry, amount in sorted(by_expiry.items()):
        remaining -= amount
        if remaining + policy.scan_reservation_request_starts <= policy.max_request_starts:
            return expiry
    raise DnsRequestBudgetError("rolling budget cannot become available from a valid ledger")


def reserve_budget(
    root: Path,
    policy: BudgetPolicy,
    *,
    state_path: Path,
    minimum_interval_hours: int,
    reservation_id: str,
    now: datetime,
    remote: str = "origin",
) -> dict[str, object]:
    """Validate the durable ledger and reserve before a request-bearing scan."""
    root = root.resolve()
    if not SAFE_RESERVATION_ID_RE.fullmatch(reservation_id):
        raise DnsRequestBudgetError("reservation id is not a safe bounded identifier")
    observed_sha = _remote_ref_sha(root, remote, policy)
    if observed_sha is None:
        raise DnsRequestBudgetError(
            "DNS request budget branch is absent; run the controlled initialize command "
            "with verified active 25-hour reservation telemetry before enabling "
            "DNS collection"
        )
    established = _fetch_observed_budget(root, remote, observed_sha, policy)
    rows = _active_rows(established.ledger, now=now, policy=policy)
    used = sum(int(row["request_starts"]) for row in rows)
    decision_at = _iso_timestamp(now)
    if not _scanner_requires_requests(
        state_path,
        now=now,
        minimum_interval_hours=minimum_interval_hours,
    ):
        return {
            "request_mode": "interval_gated",
            "decision_at": decision_at,
            "budget_sha": established.commit_sha,
            "reserved_request_starts": 0,
            "rolling_reserved_starts": used,
            "remaining_request_starts": max(0, policy.max_request_starts - used),
        }
    if any(row["id"] == reservation_id for row in rows):
        raise DnsRequestBudgetError("reservation id already exists; refusing permit reuse")
    new_total = used + policy.scan_reservation_request_starts
    if new_total > policy.max_request_starts:
        available_at = _next_available_at(rows, policy=policy)
        # A valid but full rolling ledger is an expected scheduling gate, not
        # a producer failure.  Leave the exact remote ledger untouched and
        # return a deny-all mode.  The workflow skips collection/publication in
        # this mode and exposes the next safe opportunity without presenting a
        # stale generation as freshly collected.
        return {
            "request_mode": "capacity_gated",
            "decision_at": decision_at,
            "available_at": _iso_timestamp(available_at),
            "budget_sha": established.commit_sha,
            "reserved_request_starts": 0,
            "rolling_reserved_starts": used,
            "remaining_request_starts": max(0, policy.max_request_starts - used),
        }
    rows.append(
        {
            "id": reservation_id,
            "kind": "scan",
            "reserved_at": decision_at,
            "request_starts": policy.scan_reservation_request_starts,
        }
    )
    rows.sort(key=lambda row: (row["reserved_at"], row["id"]))
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": decision_at,
        "policy": {
            "window_hours": policy.window_hours,
            "max_request_starts": policy.max_request_starts,
            "scan_reservation_request_starts": policy.scan_reservation_request_starts,
        },
        "migration_debt": False,
        "reservations": rows,
    }
    created = _create_parentless_commit(root, ledger, policy)
    _push_budget(root, remote, created.commit_sha, observed_sha, policy)
    return {
        "request_mode": "reserved",
        "decision_at": decision_at,
        "budget_sha": created.commit_sha,
        "reserved_request_starts": policy.scan_reservation_request_starts,
        "rolling_reserved_starts": new_total,
        "remaining_request_starts": policy.max_request_starts - new_total,
    }


def _append_outputs(path: Path | None, values: Mapping[str, object]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    if any("\n" in line or "\r" in line for line in lines):
        raise DnsRequestBudgetError("output values must be single-line")
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
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("initialize")
    initialize.add_argument("--seed", action="append", default=[])
    initialize.add_argument("--now")
    initialize.add_argument("--remote", default="origin")
    initialize.add_argument("--github-output", type=Path)

    reserve = subparsers.add_parser("reserve")
    reserve.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    reserve.add_argument("--minimum-interval-hours", type=int, required=True)
    reserve.add_argument("--reservation-id", required=True)
    reserve.add_argument("--now")
    reserve.add_argument("--remote", default="origin")
    reserve.add_argument("--github-output", type=Path)

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
            outputs = initialize_budget(
                root,
                policy,
                seeds=args.seed,
                now=_clock(args.now),
                remote=args.remote,
            )
        elif args.command == "reserve":
            outputs = reserve_budget(
                root,
                policy,
                state_path=args.state.resolve(),
                minimum_interval_hours=args.minimum_interval_hours,
                reservation_id=args.reservation_id,
                now=_clock(args.now),
                remote=args.remote,
            )
        elif args.command == "validate-ref":
            validated = validate_budget_ref(root, args.ref, policy)
            rows = validated.ledger["reservations"]
            outputs = {
                "budget_sha": validated.commit_sha,
                "tree_sha": validated.tree_sha,
                "updated_at": validated.ledger["updated_at"],
                "recorded_request_starts": sum(row["request_starts"] for row in rows),
                "migration_debt": str(validated.ledger["migration_debt"]).lower(),
            }
        else:  # pragma: no cover - argparse guarantees the command set.
            raise DnsRequestBudgetError(f"unknown command {args.command!r}")
        _append_outputs(args.github_output, outputs)
    except (DnsRequestBudgetError, StateValidationError, OSError, ValueError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
