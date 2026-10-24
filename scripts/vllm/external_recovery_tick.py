#!/usr/bin/env python3
# cspell:ignore geteuid
"""Wake guarded dashboard workflows from a clock outside GitHub Actions.

This standalone standard-library client dispatches only fixed main-branch
workflow profiles. It cannot collect source data or change quota ledgers.
Run it with the accompanying timers on an always-on host. Successful dispatches
are locally serialized and bounded by the selected profile's interval; failures
retain the last successful timestamp so the next activation can retry.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


DEFAULT_REPOSITORY = "AndreasKaratzas/vllm-ci-dashboard"
WORKFLOW = "publication-watchdog.yml"
WORKFLOW_INTERVALS = {
    WORKFLOW: 600,
    "queue-monitor.yml": 600,
    "queue-lifecycle.yml": 1800,
    "dns-health.yml": 3600,
    "health-check.yml": 3600,
    "deployment-retention.yml": 86400,
    "scheduler-activity.yml": 604800,
}
STATE_MAX_BYTES = 4096
RESPONSE_MAX_BYTES = 16 * 1024
REQUEST_TIMEOUT_SECONDS = 30


class RecoveryTickError(RuntimeError):
    """The tick could not safely acknowledge a workflow dispatch."""


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RecoveryTickError("GitHub API redirect refused")


def repository_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise RecoveryTickError("Expected an owner/repository name")
    return value


def canonical_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise RecoveryTickError("The host clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise RecoveryTickError("Dispatch state has an invalid clock")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecoveryTickError("Dispatch state has an invalid clock") from exc


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryTickError("Duplicate key in dispatch state")
        result[key] = value
    return result


def workflow_name(value: str) -> str:
    if value not in WORKFLOW_INTERVALS:
        raise RecoveryTickError("Workflow is outside the fixed recovery profiles")
    return value


def read_state(path: Path, repository: str, workflow: str) -> datetime | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RecoveryTickError("Dispatch state must be a regular file")
        raw = stream.read(STATE_MAX_BYTES + 1)
    if not raw or len(raw) > STATE_MAX_BYTES:
        raise RecoveryTickError("Dispatch state exceeds its size bound")
    try:
        state = json.loads(raw, object_pairs_hook=strict_object)
    except (UnicodeDecodeError, ValueError) as exc:
        raise RecoveryTickError("Dispatch state is not valid JSON") from exc
    if (
        not isinstance(state, dict)
        or set(state) != {
            "schema_version", "repository", "workflow", "last_dispatched_at", "workflow_run_id"
        }
        or type(state["schema_version"]) is not int
        or state["schema_version"] != 2
        or state["repository"] != repository
        or state["workflow"] != workflow
        or not (
            state["workflow_run_id"] is None
            or type(state["workflow_run_id"]) is int and state["workflow_run_id"] > 0
        )
    ):
        raise RecoveryTickError("Dispatch state does not match this repository and workflow")
    return parse_time(state["last_dispatched_at"])


def dispatch_workflow(repository: str, token: str, workflow: str = WORKFLOW) -> int | None:
    if not token or len(token) > 4096 or any(character.isspace() for character in token):
        raise RecoveryTickError("Set GH_TOKEN to a valid repository Actions token")
    repository = repository_name(repository)
    workflow = workflow_name(workflow)
    endpoint = f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/dispatches"
    payload: dict[str, object] = {"ref": "main"}
    if workflow == "deployment-retention.yml":
        payload["inputs"] = {"dry_run": False}
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "vllm-ci-dashboard-recovery",
        },
    )
    try:
        with urllib.request.build_opener(NoRedirects()).open(
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            if response.status == 204:
                return None  # Older GitHub API versions acknowledge without a run ID.
            if response.status != 200:
                raise RecoveryTickError(f"GitHub dispatch returned HTTP {response.status}")
            raw = response.read(RESPONSE_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RecoveryTickError(f"GitHub dispatch returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RecoveryTickError("GitHub dispatch transport failed; next tick will retry") from None
    if len(raw) > RESPONSE_MAX_BYTES:
        raise RecoveryTickError("GitHub dispatch response exceeds its size bound")
    try:
        result = json.loads(raw, object_pairs_hook=strict_object)
    except (UnicodeDecodeError, ValueError) as exc:
        raise RecoveryTickError("GitHub dispatch acknowledgment is invalid") from exc
    if not isinstance(result, dict):
        raise RecoveryTickError("GitHub dispatch acknowledgment is invalid")
    run_id = result.get("workflow_run_id")
    if (
        type(run_id) is not int
        or run_id <= 0
        or result.get("run_url") != f"https://api.github.com/repos/{repository}/actions/runs/{run_id}"
        or result.get("html_url") != f"https://github.com/{repository}/actions/runs/{run_id}"
    ):
        raise RecoveryTickError("GitHub dispatch acknowledgment has the wrong repository")
    return run_id


def tick(
    state_directory: Path,
    repository: str,
    token: str,
    *,
    workflow: str = WORKFLOW,
    now: datetime | None = None,
    dispatch: Callable[[str, str], int | None] | None = None,
) -> dict:
    repository = repository_name(repository)
    workflow = workflow_name(workflow)
    clock = now or datetime.now(timezone.utc)
    timestamp = canonical_time(clock)
    if state_directory.is_symlink():
        raise RecoveryTickError("Dispatch directory must not be a symlink")
    state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = state_directory.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise RecoveryTickError("Dispatch directory must be private to the service user")
    lock_fd = os.open(
        state_directory / "dispatch.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(lock_fd, "a") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise RecoveryTickError("Dispatch lock must be a regular file")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "already-running", "repository": repository, "workflow": workflow}
        state_path = state_directory / "last-dispatch.json"
        previous = read_state(state_path, repository, workflow)
        if previous is not None:
            if previous > clock:
                raise RecoveryTickError("The host clock precedes its last acknowledged dispatch")
            if clock - previous < timedelta(seconds=WORKFLOW_INTERVALS[workflow]):
                return {"status": "cooldown", "repository": repository, "workflow": workflow}
        run_id = (
            dispatch(repository, token)
            if dispatch is not None else dispatch_workflow(repository, token, workflow)
        )
        state = {
            "schema_version": 2,
            "repository": repository,
            "workflow": workflow,
            "last_dispatched_at": timestamp,
            "workflow_run_id": run_id,
        }
        descriptor, temporary = tempfile.mkstemp(prefix=".dispatch-", dir=state_directory)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(state, stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, state_path)
            directory_fd = os.open(state_directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {
            "status": "dispatched", "repository": repository,
            "workflow": workflow, "workflow_run_id": run_id,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--workflow", choices=tuple(WORKFLOW_INTERVALS), default=WORKFLOW)
    parser.add_argument("--state-directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = tick(
            args.state_directory, args.repository, os.environ.get("GH_TOKEN", ""),
            workflow=args.workflow,
        )
    except (RecoveryTickError, OSError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
