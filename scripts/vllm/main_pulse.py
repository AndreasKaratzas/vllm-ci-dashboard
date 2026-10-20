#!/usr/bin/env python3
"""Detect new AMD MI failures on vLLM main minutes after a build finishes.

Data Collection replays the full 30-day main cohort at most every two hours.
The pulse fills the gap between collections: on each reserved run it lists
``ci`` branch=main builds finished since its durable cursor, keeps their AMD MI
jobs, and applies them with the same incident transitions on top of the
published collection watcher state. A new hard failure therefore opens or
updates the managed issue in the pulse run.

Durable state lives on two parentless one-file branches: the request ledger
(``budget_branch``) is reserved before any Buildkite token is exposed, and the
overlay (``state_branch``) holds the cursor plus only the observations that
are newer than the collection state. The pulse reconciles the issue only while
an incident is active, so closing a resolved issue stays with the collection
watcher, which merges the overlay first. (The shared reconciliation may still
close a verified duplicate managed issue, as the collection would.)

The authority state is the last *published* collection state, which lags the
collection's own issue mutation. Before mutating the issue the pulse checks
that GitHub agrees with it (its issue is still open, or no managed issue is
open when it owns none) and otherwise defers to its next run. The pulse also
publishes its observations before mutating the issue, so a collection that
exported an older overlay sees the state branch move and does not close.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.buildkite_request_guard import install_from_environment_or_exit  # noqa: E402

install_from_environment_or_exit()

from vllm import amd_main_failure_watcher as watcher  # noqa: E402
from vllm import ci_main_failure_watcher as ci_watcher  # noqa: E402
from vllm import queue_request_budget as ledger_git  # noqa: E402
from vllm.ci import main_pulse_overlay as overlay_lib  # noqa: E402
from vllm.ci.managed_issue import (  # noqa: E402
    DASHBOARD_REPO,
    GitHubIssueClient,
    normalize_managed_state,
    validate_target_repo,
)
from vllm.ci.reliability_history import (  # noqa: E402
    build_all_main_reliability,
    validate_all_main_reliability,
)
from vllm.pipelines import NIGHTLY_NAME_PATTERNS_BY_SLUG  # noqa: E402


log = logging.getLogger("main_pulse")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config/main_pulse.json"
SCHEMA_VERSION = 1
MAX_RESERVATION_REQUEST_STARTS = 40
PROCESSED_BUILD_RETENTION = timedelta(days=3)


class MainPulseError(RuntimeError):
    """The pulse could not establish its durable inputs safely."""


@dataclass(frozen=True)
class PulseConfig:
    pipeline: str
    budget_branch: str
    budget_ledger_path: str
    state_branch: str
    state_path: str
    window_hours: int
    max_request_starts: int
    reservation_request_starts: int
    minimum_interval_minutes: int
    max_reservations: int
    max_ledger_bytes: int
    max_state_bytes: int
    max_pages: int
    max_lookback_minutes: int
    cursor_overlap_minutes: int


def _iso(value: datetime) -> str:
    return ledger_git._iso_timestamp(value)


def _positive(payload: Mapping[str, Any], key: str) -> int:
    try:
        return ledger_git._positive_int(payload.get(key), label=key)
    except ledger_git.QueueRequestBudgetError as exc:
        raise MainPulseError(str(exc)) from None


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> PulseConfig:
    from vllm.collect_analytics import BK_GET_MAX_ATTEMPTS

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MainPulseError(f"main pulse config is unreadable: {exc}") from exc
    fields = set(PulseConfig.__dataclass_fields__) | {"schema_version"}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise MainPulseError("main pulse config has an unexpected shape")
    if payload["schema_version"] != SCHEMA_VERSION or payload["pipeline"] != "ci":
        raise MainPulseError("main pulse config schema or pipeline is unsupported")
    try:
        for key in ("budget_branch", "state_branch"):
            ledger_git._safe_branch(payload[key], label=key)
        for key in ("budget_ledger_path", "state_path"):
            ledger_git._safe_ledger_path(payload[key])
    except ledger_git.QueueRequestBudgetError as exc:
        raise MainPulseError(str(exc)) from None
    config = PulseConfig(
        pipeline=payload["pipeline"],
        budget_branch=payload["budget_branch"],
        budget_ledger_path=payload["budget_ledger_path"],
        state_branch=payload["state_branch"],
        state_path=payload["state_path"],
        **{
            key: _positive(payload, key)
            for key in fields
            - {"schema_version", "pipeline", "budget_branch", "budget_ledger_path", "state_branch", "state_path"}
        },
    )
    if config.budget_branch == config.state_branch:
        raise MainPulseError("budget and state need separate branches")
    if config.reservation_request_starts > MAX_RESERVATION_REQUEST_STARTS:
        raise MainPulseError("one pulse may not reserve more than 40 request starts")
    if config.reservation_request_starts < config.max_pages * BK_GET_MAX_ATTEMPTS:
        raise MainPulseError("the reservation does not cover every page retry")
    if config.minimum_interval_minutes < 10:
        raise MainPulseError("the pulse may not run more often than every ten minutes")
    runs = math.ceil(config.window_hours * 60 / config.minimum_interval_minutes)
    if runs * config.reservation_request_starts > config.max_request_starts:
        raise MainPulseError("the interval cadence does not fit the rolling request cap")
    if runs > config.max_reservations:
        raise MainPulseError("max_reservations is below the interval cadence")
    if config.cursor_overlap_minutes >= config.max_lookback_minutes:
        raise MainPulseError("cursor overlap must be shorter than the lookback")
    return config


# Parentless one-file branches -------------------------------------------------


def _branch_ref(branch: str) -> SimpleNamespace:
    return SimpleNamespace(branch=branch)


def read_branch_file(
    root: Path, remote: str, branch: str, path: str, max_bytes: int
) -> tuple[str | None, bytes | None]:
    """Return the exact commit and file of a parentless one-file branch."""
    try:
        observed = ledger_git._remote_ref_sha(root, remote, _branch_ref(branch))
        if observed is None:
            return None, None
        ledger_git._git(root, "fetch", "--no-tags", "--depth=1", remote, f"refs/heads/{branch}")
        if ledger_git._resolve_commit(root, "FETCH_HEAD") != observed:
            raise MainPulseError(f"{branch} moved between lookup and fetch")
        headers = ledger_git._git(root, "cat-file", "commit", observed).split(b"\n\n", 1)[0]
        if any(line.startswith(b"parent ") for line in headers.splitlines()):
            raise MainPulseError(f"{branch} must be one parentless commit")
        records = [row for row in ledger_git._git(root, "ls-tree", "-r", "-z", "-l", observed).split(b"\0") if row]
        if len(records) != 1:
            raise MainPulseError(f"{branch} must contain exactly one file")
        metadata, name = records[0].split(b"\t", 1)
        mode, kind, object_id, size = metadata.split()
        if name.decode() != path or mode != b"100644" or kind != b"blob" or not 0 < int(size) <= max_bytes:
            raise MainPulseError(f"{branch} contains an unexpected or oversized entry")
        return observed, ledger_git._git(root, "cat-file", "blob", object_id.decode("ascii"))
    except ledger_git.QueueRequestBudgetError as exc:
        raise MainPulseError(str(exc)) from None


def publish_branch_file(
    root: Path,
    remote: str,
    branch: str,
    path: str,
    payload: bytes,
    *,
    expected_sha: str | None,
    message: str,
    at: str,
) -> str:
    """Replace the branch with one parentless commit, leased on ``expected_sha``."""
    try:
        blob = ledger_git._git(root, "hash-object", "-w", "--stdin", input_bytes=payload).decode().strip()
        tree = ledger_git._git(root, "mktree", input_bytes=f"100644 blob {blob}\t{path}\n".encode()).decode().strip()
        identity = "github-actions[bot]"
        email = "github-actions[bot]@users.noreply.github.com"
        env = {
            "GIT_AUTHOR_NAME": identity,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": identity,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_AUTHOR_DATE": at,
            "GIT_COMMITTER_DATE": at,
        }
        commit = ledger_git._git(
            root, "commit-tree", tree, input_bytes=f"{message}\n".encode(), extra_env=env
        ).decode().strip()
        ledger_git._push_budget(root, remote, commit, expected_sha, _branch_ref(branch))
    except ledger_git.QueueRequestBudgetError as exc:
        raise MainPulseError(str(exc)) from None
    return commit


# Request budget ---------------------------------------------------------------


def _ledger_policy(config: PulseConfig) -> dict[str, int]:
    return {
        "window_hours": config.window_hours,
        "max_request_starts": config.max_request_starts,
        "reservation_request_starts": config.reservation_request_starts,
        "minimum_interval_minutes": config.minimum_interval_minutes,
    }


def normalize_ledger(value: Any, config: PulseConfig) -> dict:
    if not isinstance(value, dict) or set(value) != {"schema_version", "updated_at", "policy", "reservations"}:
        raise MainPulseError("main pulse ledger has an unexpected shape")
    if value["schema_version"] != SCHEMA_VERSION or value["policy"] != _ledger_policy(config):
        raise MainPulseError("main pulse ledger schema or policy disagrees with config")
    try:
        updated_at = ledger_git._canonical_timestamp(value["updated_at"], label="updated_at")
        rows = value["reservations"]
        if not isinstance(rows, list) or len(rows) > config.max_reservations:
            raise MainPulseError("main pulse reservations are unbounded")
        cutoff = updated_at - timedelta(hours=config.window_hours)
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"id", "reserved_at", "request_starts"}:
                raise MainPulseError("main pulse reservation has an unexpected shape")
            if not ledger_git.SAFE_RESERVATION_ID_RE.fullmatch(str(row["id"])) or row["id"] in seen:
                raise MainPulseError("main pulse reservation id is invalid or duplicated")
            seen.add(row["id"])
            if row["request_starts"] != config.reservation_request_starts:
                raise MainPulseError("main pulse reservation has the wrong allowance")
            reserved_at = ledger_git._canonical_timestamp(row["reserved_at"], label="reserved_at")
            if not cutoff < reserved_at <= updated_at:
                raise MainPulseError("main pulse reservation is outside its window")
    except ledger_git.QueueRequestBudgetError as exc:
        raise MainPulseError(str(exc)) from None
    if rows != sorted(rows, key=lambda row: (row["reserved_at"], row["id"])):
        raise MainPulseError("main pulse reservations are not in canonical order")
    if sum(row["request_starts"] for row in rows) > config.max_request_starts:
        raise MainPulseError("main pulse ledger exceeds its rolling cap")
    return value


def _encode_ledger(ledger: dict, config: PulseConfig) -> bytes:
    encoded = (json.dumps(normalize_ledger(ledger, config), indent=2, sort_keys=True) + "\n").encode()
    if len(encoded) > config.max_ledger_bytes:
        raise MainPulseError("main pulse ledger exceeds its byte limit")
    return encoded


def plan_reservation(ledger: dict | None, config: PulseConfig, *, reservation_id: str, now: datetime) -> tuple[dict, dict | None]:
    """Return workflow outputs and the ledger to publish (None when gated)."""
    if not ledger_git.SAFE_RESERVATION_ID_RE.fullmatch(reservation_id):
        raise MainPulseError("reservation id is not a safe bounded identifier")
    rows = []
    if ledger is not None:
        normalize_ledger(ledger, config)
        if ledger_git._canonical_timestamp(ledger["updated_at"], label="updated_at") > now:
            raise MainPulseError("main pulse ledger is from the future")
        cutoff = now - timedelta(hours=config.window_hours)
        rows = [
            dict(row)
            for row in ledger["reservations"]
            if ledger_git._canonical_timestamp(row["reserved_at"], label="reserved_at") > cutoff
        ]
    used = sum(row["request_starts"] for row in rows)
    outputs: dict[str, Any] = {
        "decision_at": _iso(now),
        "reserved_request_starts": 0,
        "rolling_reserved_starts": used,
    }
    last = max((ledger_git._canonical_timestamp(row["reserved_at"], label="at") for row in rows), default=None)
    if last is not None and now - last < timedelta(minutes=config.minimum_interval_minutes):
        outputs["request_mode"] = "interval_gated"
        outputs["available_at"] = _iso(last + timedelta(minutes=config.minimum_interval_minutes))
        return outputs, None
    if used + config.reservation_request_starts > config.max_request_starts:
        outputs["request_mode"] = "capacity_gated"
        return outputs, None
    if any(row["id"] == reservation_id for row in rows):
        raise MainPulseError("reservation id already exists; refusing permit reuse")
    rows.append({"id": reservation_id, "reserved_at": _iso(now), "request_starts": config.reservation_request_starts})
    rows.sort(key=lambda row: (row["reserved_at"], row["id"]))
    outputs.update(
        request_mode="reserved",
        reserved_request_starts=config.reservation_request_starts,
        rolling_reserved_starts=used + config.reservation_request_starts,
    )
    return outputs, {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _iso(now),
        "policy": _ledger_policy(config),
        "reservations": rows,
    }


def reserve(root: Path, config: PulseConfig, *, reservation_id: str, now: datetime, remote: str) -> dict:
    observed, payload = read_branch_file(
        root, remote, config.budget_branch, config.budget_ledger_path, config.max_ledger_bytes
    )
    # An absent branch starts empty: unlike the queue and DNS ledgers, no
    # earlier collector already spends this budget.
    ledger = None
    if payload is not None:
        try:
            ledger = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MainPulseError(f"main pulse ledger is not JSON: {exc}") from exc
        if _encode_ledger(ledger, config) != payload:
            raise MainPulseError("main pulse ledger is not canonically encoded")
    outputs, updated = plan_reservation(ledger, config, reservation_id=reservation_id, now=now)
    if updated is not None:
        outputs["budget_sha"] = publish_branch_file(
            root,
            remote,
            config.budget_branch,
            config.budget_ledger_path,
            _encode_ledger(updated, config),
            expected_sha=observed,
            message=f"main pulse: reserve request budget {outputs['decision_at']}",
            at=outputs["decision_at"],
        )
    return outputs


# Pulse ------------------------------------------------------------------------


def fetch_finished_builds(config: PulseConfig, token: str, since: datetime) -> tuple[list[dict], dict]:
    """One bounded ``finished_from`` leg through the analytics collector client."""
    from vllm.collect_analytics import _fetch_pipeline_build_leg

    return _fetch_pipeline_build_leg(
        config.pipeline,
        token,
        filter_name="finished_from",
        since=since.isoformat(),
        max_pages=config.max_pages,
    )


def fetch_window(config: PulseConfig, overlay: dict | None, now: datetime) -> datetime:
    floor = now - timedelta(minutes=config.max_lookback_minutes)
    cursor = watcher._parse_ts((overlay or {}).get("cursor"))
    return max(cursor, floor) if cursor else floor


class DryRunIssueClient:
    """Records the issue mutations a real run would make."""

    def __init__(self, known_open: Sequence[int] = ()):
        self.known_open = set(known_open)
        self.actions: list[tuple[str, int]] = []

    def issue_state(self, number: int, ownership_marker: str) -> str:
        return "open"

    def find_open_issues(self, ownership_marker: str) -> list[int]:
        return sorted(self.known_open)

    def open_issue(self, title: str, body: str, label_specs: Any) -> int:
        number = 900_000 + len(self.actions)
        self.actions.append(("open", number))
        return number

    def update_issue(self, number: int, title: str, body: str) -> bool:
        self.actions.append(("update", number))
        return True

    def ensure_owner_assigned(self, number: int) -> bool:
        return True

    def comment_issue(self, number: int, body: str) -> bool:
        self.actions.append(("comment", number))
        return True

    def close_issue(self, number: int) -> bool:
        self.actions.append(("close", number))
        return True


def _ready_authority(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise MainPulseError("collection watcher state is not an object")
    state = watcher._readable_state_copy(raw)
    if not state.get("initialized") or raw.get("hardware_scope") != "amd_mi_gpu":
        raise MainPulseError("collection watcher state is not an initialized MI-scoped state")
    return state


def issue_basis_is_current(state: dict, client: Any) -> bool:
    """Whether GitHub agrees with the issue ownership the pulse is working from."""
    managed = normalize_managed_state(state)
    marker = ci_watcher.CONFIG.ownership_marker
    if managed["issue"]:
        return client.issue_state(managed["issue"]["number"], marker) == "open"
    if managed["suppressed"]:
        return True
    if isinstance(client, GitHubIssueClient):
        found = client.find_open_issues(
            marker,
            durable_label=ci_watcher.CONFIG.label_specs[0][0],
            recovery_labels=("automated", "workstream:dev"),
        )
    else:
        found = client.find_open_issues(marker)
    return not found


def pulse_step(
    config: PulseConfig,
    authority_raw: dict,
    overlay: dict | None,
    builds: list[dict],
    leg: dict,
    *,
    since: datetime,
    fetched_at: datetime,
    client: Any,
    repo: str,
    run_url: str,
    before_issue_mutation: Callable[[dict], None] | None = None,
) -> tuple[dict, dict]:
    """Apply one fetched slice; return the next overlay and a run summary.

    ``before_issue_mutation`` receives the overlay without the issue change
    and must make it durable before the issue is touched.
    """
    authority = _ready_authority(authority_raw)
    base = overlay_lib.merge_overlay(authority, overlay)
    previous_processed = {row["number"]: row for row in (overlay or {}).get("processed_builds") or []}
    base["processed_build_numbers"] = sorted(
        set(authority.get("processed_build_numbers") or []) | set(previous_processed)
    )
    generated_at = _iso(fetched_at)
    reliability = build_all_main_reliability(
        builds,
        pipeline_slug=config.pipeline,
        window_days=1,
        generated_at=generated_at,
        nightly_pattern=NIGHTLY_NAME_PATTERNS_BY_SLUG.get(config.pipeline) or "",
        collection_provenance={**leg, "created_from": since.isoformat()},
    )
    if not validate_all_main_reliability(reliability, config.pipeline, require_exhaustive=False):
        raise MainPulseError("fetched builds do not form a valid main reliability slice")
    reliability = watcher.mi_scoped_reliability(reliability)
    events: list[dict] = []
    state = watcher.advance_incidents(
        reliability,
        base,
        track_commit_range=ci_watcher.CONFIG.track_commit_range,
        events=events,
        partial_cohort=True,
    )
    active = state.get("active") or {}
    content = watcher._content_fingerprint(active)
    changed = content != watcher._content_fingerprint(base.get("active") or {})
    issue, basis = None, ""
    if overlay and overlay["issue"] is not None and overlay["issue_basis"] == overlay_lib.issue_basis(authority):
        issue, basis = overlay["issue"], overlay["issue_basis"]
    needs_issue = bool(active) and (
        changed
        or not state.get("suppressed")
        and (not state.get("issue") or state.get("last_content_fingerprint") != content)
    )

    groups = overlay_lib.overlay_groups(authority, state)
    timeline = watcher.bounded_timeline(
        list((overlay or {}).get("timeline") or []) + [{**event, "source": "pulse"} for event in events],
        fetched_at,
    )
    authority_processed = set(authority.get("processed_build_numbers") or [])
    processed = dict(previous_processed)
    for build in reliability.get("builds") or []:
        processed[int(build["number"])] = {"number": int(build["number"]), "created_at": str(build.get("created_at") or "")}
    cutoff = fetched_at - PROCESSED_BUILD_RETENTION
    processed_builds = sorted(
        (
            row
            for number, row in processed.items()
            if number not in authority_processed
            and (watcher._parse_ts(row["created_at"]) or fetched_at) >= cutoff
        ),
        key=lambda row: row["number"],
    )[-overlay_lib.MAX_PROCESSED_BUILDS:]
    exhaustive = leg.get("exhaustive") is True
    # A truncated newest-first page leaves older builds unseen; keep them in
    # the next window instead of skipping past them.
    cursor = max(since, fetched_at - timedelta(minutes=config.cursor_overlap_minutes)) if exhaustive else since
    summary = {
        "fetched_at": generated_at,
        "finished_from": since.isoformat(),
        "builds_fetched": len(builds),
        "main_builds": len(reliability.get("builds") or []),
        "new_builds": len(set(int(b["number"]) for b in reliability.get("builds") or []) - set(base["processed_build_numbers"])),
        "exhaustive": exhaustive,
        "opened": sum(event["event"] == "opened" for event in events),
        "resolved": sum(event["event"] == "resolved" for event in events),
        "active": len(active),
        "pending_soft": len(state.get("pending_soft") or {}),
        "issue_action": "none",
    }
    if not exhaustive:
        log.warning("::warning::Main pulse page was truncated; the cursor stays at %s", since.isoformat())

    def overlay_with(issue_fields: dict | None, issue_basis: str) -> dict:
        return overlay_lib.normalize_overlay(
            {
                "schema_version": overlay_lib.OVERLAY_SCHEMA_VERSION,
                "updated_at": generated_at,
                "cursor": _iso(cursor),
                "groups": groups,
                "issue": issue_fields,
                "issue_basis": issue_basis,
                "timeline": [event for event in timeline if event.get("group_id") in groups],
                "processed_builds": processed_builds,
                "last_run": dict(summary),
            }
        )

    if needs_issue:
        if not issue_basis_is_current(state, client):
            summary["issue_action"] = "deferred"
            log.info("Published collection state disagrees with GitHub; deferring the issue to the next pulse")
        else:
            if before_issue_mutation is not None:
                before_issue_mutation(overlay_with(issue, basis))
            before = (state.get("issue") or {}).get("number")
            state = watcher.reconcile_issue(
                state,
                reliability,
                ci_watcher.CONFIG,
                client=client,
                repo=repo,
                run_url=run_url,
                observed_at=generated_at,
            )
            after = (state.get("issue") or {}).get("number")
            summary["issue_action"] = "opened" if after and not before else "updated" if after else "unresolved"
            issue, basis = overlay_lib.issue_block(state), overlay_lib.issue_basis(authority)
    next_overlay = overlay_with(issue, basis)
    return next_overlay, summary


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MainPulseError(f"{path} is unreadable: {exc}") from exc


def run(args: argparse.Namespace, config: PulseConfig) -> dict:
    root = args.root.resolve()
    authority = _load_json(args.authority_state)
    _ready_authority(authority)
    if args.state_file is not None:
        observed_sha = None
        overlay = overlay_lib.load_overlay_file(args.state_file)
    else:
        observed_sha, payload = read_branch_file(
            root, args.remote, config.state_branch, config.state_path, config.max_state_bytes
        )
        overlay = overlay_lib.decode_overlay(payload) if payload is not None else None
    started = ledger_git._clock(args.now)
    since = fetch_window(config, overlay, started)
    if args.recorded_builds is not None:
        rows = _load_json(args.recorded_builds)
        builds = [row for row in rows if isinstance(row, dict) and (watcher._parse_ts(row.get("finished_at")) or since) >= since]
        leg = {"finished_from": since.isoformat(), "filter": "finished_from", "pages_fetched": 0,
               "termination_reason": "recorded_responses", "exhaustive": True}
    else:
        token = os.getenv("BUILDKITE_TOKEN") or os.getenv("BUILDKITE_API_TOKEN")
        if not token:
            raise MainPulseError("BUILDKITE_TOKEN is not set")
        builds, leg = fetch_finished_builds(config, token, since)
    repo = os.getenv("GITHUB_REPOSITORY") or DASHBOARD_REPO
    validate_target_repo(repo)
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}" if run_id else f"https://github.com/{repo}"
    if args.dry_run:
        client: Any = DryRunIssueClient()
    else:
        github_token = os.getenv("GITHUB_TOKEN")
        if not github_token:
            raise MainPulseError("GITHUB_TOKEN is not set")
        client = GitHubIssueClient(github_token, repo)
    lease = {"sha": observed_sha}

    def publish(value: dict) -> None:
        payload = overlay_lib.encode_overlay(value)
        if len(payload) > config.max_state_bytes:
            raise MainPulseError("main pulse overlay exceeds its byte limit")
        if args.state_file is not None:
            args.state_file.parent.mkdir(parents=True, exist_ok=True)
            args.state_file.write_bytes(payload)
            return
        lease["sha"] = publish_branch_file(
            root, args.remote, config.state_branch, config.state_path, payload,
            expected_sha=lease["sha"],
            message=f"main pulse: observations through {value['cursor']}",
            at=value["updated_at"],
        )

    next_overlay, summary = pulse_step(
        config, authority, overlay, builds, leg,
        since=since, fetched_at=started, client=client, repo=repo, run_url=run_url,
        before_issue_mutation=publish,
    )
    if args.dry_run:
        summary["dry_run_issue_actions"] = [list(action) for action in client.actions]
    publish(next_overlay)
    if lease["sha"] is not None and args.state_file is None:
        summary["state_sha"] = lease["sha"]
    return summary


def export_overlay(args: argparse.Namespace, config: PulseConfig) -> dict:
    """Write the export record; any failure leaves no file, so closing waits."""
    sha, payload = read_branch_file(
        args.root.resolve(), args.remote, config.state_branch, config.state_path, config.max_state_bytes
    )
    overlay = overlay_lib.decode_overlay(payload) if payload is not None else None
    record = overlay_lib.encode_export(config.state_branch, sha, overlay)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(record)
    if overlay is None:
        return {"overlay": "absent"}
    return {"overlay": "exported", "groups": len(overlay["groups"]), "cursor": overlay["cursor"], "state_sha": sha}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--remote", default="origin")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--github-output", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    reserve_parser = commands.add_parser("reserve", parents=[common])
    reserve_parser.add_argument("--reservation-id", required=True)
    reserve_parser.add_argument("--now")
    run_parser = commands.add_parser("run", parents=[common])
    run_parser.add_argument("--authority-state", type=Path, required=True)
    run_parser.add_argument("--state-file", type=Path, help="local overlay instead of the state branch")
    run_parser.add_argument("--recorded-builds", type=Path, help="recorded Buildkite builds instead of the API")
    run_parser.add_argument("--dry-run", action="store_true", help="record issue mutations instead of making them")
    run_parser.add_argument("--now")
    export_parser = commands.add_parser("export-overlay", parents=[common])
    export_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "run" and args.dry_run and (args.state_file is None or args.recorded_builds is None):
        parser.error("--dry-run needs --state-file and --recorded-builds")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    args = parse_args(argv)
    try:
        config = load_config(args.config.resolve())
        if args.command == "reserve":
            outputs = reserve(
                args.root.resolve(), config, reservation_id=args.reservation_id,
                now=ledger_git._clock(args.now), remote=args.remote,
            )
        elif args.command == "run":
            outputs = run(args, config)
        else:
            outputs = export_overlay(args, config)
        log.info("main pulse %s: %s", args.command, json.dumps(outputs, sort_keys=True))
        if args.github_output is not None or args.command == "reserve":
            ledger_git._append_outputs(
                args.github_output,
                {key: value for key, value in outputs.items() if not isinstance(value, (list, dict))},
            )
    except (MainPulseError, ValueError, OSError, ledger_git.QueueRequestBudgetError) as exc:
        print(f"::error::main pulse {args.command}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
