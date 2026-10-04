#!/usr/bin/env python3
"""Exact cross-process guard for Buildkite API transport starts.

The workflow creates one local counter only after its durable attempt
reservation succeeds.  ``sitecustomize`` installs :func:`install` in every
later Python process.  Each ``requests.Session.send`` of a prepared request to
an audited Buildkite API host atomically increments the shared counter before
transport; allowance + 1 raises without sending a request.  Send-level
instrumentation also covers same-origin redirect hops.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

import fcntl


SCHEMA_VERSION = 1
API_HOSTS = frozenset({"api.buildkite.com", "graphql.buildkite.com"})
SAFE_ATTEMPT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}")
MAX_ALLOWANCE = 10_000
GUARD_ENV_NAMES = (
    "BUILDKITE_REQUEST_GUARD_FILE",
    "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID",
    "BUILDKITE_REQUEST_GUARD_ALLOWANCE",
)
TOKEN_ENV_NAMES = ("BUILDKITE_TOKEN", "BUILDKITE_API_TOKEN")


class BuildkiteRequestGuardError(RuntimeError):
    """A request could not be proven to fit the reserved local allowance."""


class BuildkiteRequestAllowanceExhausted(BuildkiteRequestGuardError):
    """The exact valid local counter has no remaining request starts."""


def _canonical_payload(*, attempt_id: str, allowance: int, starts: int) -> bytes:
    value = {
        "schema_version": SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "allowance": allowance,
        "request_starts": starts,
        "api_hosts": sorted(API_HOSTS),
    }
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _validate_state(
    value: object,
    *,
    expected_attempt_id: str,
    expected_allowance: int,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "attempt_id",
        "allowance",
        "request_starts",
        "api_hosts",
    }:
        raise BuildkiteRequestGuardError("request guard state has an unexpected shape")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise BuildkiteRequestGuardError("request guard schema is unsupported")
    attempt_id = value.get("attempt_id")
    if attempt_id != expected_attempt_id or not SAFE_ATTEMPT_RE.fullmatch(attempt_id):
        raise BuildkiteRequestGuardError("request guard attempt identity disagrees")
    allowance = value.get("allowance")
    if (
        isinstance(allowance, bool)
        or not isinstance(allowance, int)
        or allowance != expected_allowance
        or not 0 <= allowance <= MAX_ALLOWANCE
    ):
        raise BuildkiteRequestGuardError("request guard allowance disagrees")
    starts = value.get("request_starts")
    if isinstance(starts, bool) or not isinstance(starts, int) or not 0 <= starts <= allowance:
        raise BuildkiteRequestGuardError("request guard count is outside its allowance")
    if value.get("api_hosts") != sorted(API_HOSTS):
        raise BuildkiteRequestGuardError("request guard API host policy disagrees")
    return {
        "schema_version": SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "allowance": allowance,
        "request_starts": starts,
        "api_hosts": sorted(API_HOSTS),
    }


def _decode(raw: bytes) -> object:
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise BuildkiteRequestGuardError(
            f"request guard state is not strict UTF-8 JSON: {exc}"
        ) from exc


def _open_locked(path: Path, *, writable: bool):
    flags = os.O_RDWR if writable else os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BuildkiteRequestGuardError(f"request guard state is unavailable: {exc}") from exc
    handle = os.fdopen(descriptor, "r+b" if writable else "rb")
    try:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > 64 * 1024:
            raise BuildkiteRequestGuardError("request guard state is not one bounded regular file")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if writable else fcntl.LOCK_SH)
    except Exception:
        handle.close()
        raise
    return handle


def initialize(path: Path, *, attempt_id: str, allowance: int) -> None:
    if not SAFE_ATTEMPT_RE.fullmatch(attempt_id):
        raise BuildkiteRequestGuardError("request guard attempt id is invalid")
    if isinstance(allowance, bool) or not isinstance(allowance, int) or not 0 <= allowance <= MAX_ALLOWANCE:
        raise BuildkiteRequestGuardError("request guard allowance is invalid")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise BuildkiteRequestGuardError(
            f"refusing to replace existing request guard state: {exc}"
        ) from exc
    try:
        payload = _canonical_payload(attempt_id=attempt_id, allowance=allowance, starts=0)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def read_count(path: Path, *, attempt_id: str, allowance: int) -> int:
    with _open_locked(path.resolve(), writable=False) as handle:
        raw = handle.read(64 * 1024 + 1)
        state = _validate_state(
            _decode(raw),
            expected_attempt_id=attempt_id,
            expected_allowance=allowance,
        )
        if raw != _canonical_payload(
            attempt_id=attempt_id,
            allowance=allowance,
            starts=state["request_starts"],
        ):
            raise BuildkiteRequestGuardError("request guard state is not canonical JSON")
        return state["request_starts"]


def consume(path: Path, *, attempt_id: str, allowance: int) -> int:
    """Charge one start durably before transport and return the new count."""
    with _open_locked(path.resolve(), writable=True) as handle:
        raw = handle.read(64 * 1024 + 1)
        state = _validate_state(
            _decode(raw),
            expected_attempt_id=attempt_id,
            expected_allowance=allowance,
        )
        if raw != _canonical_payload(
            attempt_id=attempt_id,
            allowance=allowance,
            starts=state["request_starts"],
        ):
            raise BuildkiteRequestGuardError("request guard state is not canonical JSON")
        if state["request_starts"] >= allowance:
            raise BuildkiteRequestAllowanceExhausted(
                f"Buildkite request-start allowance exhausted at {allowance}; "
                "request was blocked before transport"
            )
        starts = state["request_starts"] + 1
        payload = _canonical_payload(
            attempt_id=attempt_id,
            allowance=allowance,
            starts=starts,
        )
        handle.seek(0)
        handle.write(payload)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
        return starts


def _api_request(url: object) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() == "https"
        and parsed.hostname is not None
        and parsed.hostname.casefold() in API_HOSTS
        and parsed.username is None
        and parsed.password is None
        and parsed.port in {None, 443}
    )


def install(path: Path, *, attempt_id: str, allowance: int) -> None:
    """Patch requests once; validate the guard before a collector can run."""
    import requests

    # Fail the process during site initialization if local evidence is absent,
    # corrupt, or ambiguous.  This check does not consume an allowance.
    read_count(path, attempt_id=attempt_id, allowance=allowance)
    session_type = requests.sessions.Session
    installed = getattr(session_type, "_vllm_buildkite_request_guard", None)
    identity = (str(path.resolve()), attempt_id, allowance)
    if installed is not None:
        if installed != identity:
            raise BuildkiteRequestGuardError("requests already has a different guard identity")
        return
    original = session_type.send

    @functools.wraps(original)
    def guarded_send(session, request, *args, **kwargs):
        url = getattr(request, "url", None)
        if _api_request(url):
            # urllib3 retries occur below Session.send and would otherwise be
            # invisible to an exact transport-start counter.  Audited
            # collectors use Requests' default zero-retry adapters; reject a
            # future nonzero adapter before it can make the bound ambiguous.
            adapter = session.get_adapter(url)
            retry_policy = getattr(adapter, "max_retries", None)
            retry_total = getattr(retry_policy, "total", 0)
            retry_connect = getattr(retry_policy, "connect", 0)
            retry_read = getattr(retry_policy, "read", 0)
            retry_status = getattr(retry_policy, "status", 0)
            retry_redirect = getattr(retry_policy, "redirect", 0)
            if any(
                value not in (0, False, None)
                for value in (
                    retry_total,
                    retry_connect,
                    retry_read,
                    retry_status,
                    retry_redirect,
                )
            ):
                raise BuildkiteRequestGuardError(
                    "Buildkite API adapter has hidden transport retries enabled"
                )
            consume(path, attempt_id=attempt_id, allowance=allowance)
        return original(session, request, *args, **kwargs)

    # Runtime instrumentation is intentionally class-wide so every Session
    # created by every collector library shares the same transport hook.
    setattr(session_type, "send", guarded_send)  # noqa: B010 - intentional monkeypatch
    setattr(  # noqa: B010 - intentional class marker
        session_type, "_vllm_buildkite_request_guard", identity
    )


def install_from_environment() -> None:
    raw_path = os.getenv("BUILDKITE_REQUEST_GUARD_FILE", "")
    raw_attempt = os.getenv("BUILDKITE_REQUEST_GUARD_ATTEMPT_ID", "")
    raw_allowance = os.getenv("BUILDKITE_REQUEST_GUARD_ALLOWANCE", "")
    if not any(name in os.environ for name in (*GUARD_ENV_NAMES, *TOKEN_ENV_NAMES)):
        return
    if not raw_path or not raw_attempt or not raw_allowance.isdigit():
        raise BuildkiteRequestGuardError("request guard environment is incomplete")
    install(Path(raw_path), attempt_id=raw_attempt, allowance=int(raw_allowance))


def install_from_environment_or_exit() -> None:
    """Activate the guard for a direct CLI, exiting 78 before its body on error."""
    try:
        install_from_environment()
    except BaseException as exc:
        sys.stderr.write(f"fatal Buildkite request guard activation error: {exc}\n")
        sys.stderr.flush()
        raise SystemExit(78) from exc


def _append_outputs(path: Path | None, values: Mapping[str, object]) -> None:
    payload = "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    if "\r" in payload:
        raise BuildkiteRequestGuardError("request guard output contains a carriage return")
    if path is None:
        sys.stdout.write(payload)
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("initialize")
    init.add_argument("--file", type=Path, required=True)
    init.add_argument("--attempt-id", required=True)
    init.add_argument("--allowance", type=int, required=True)
    report = subparsers.add_parser("report")
    report.add_argument("--file", type=Path, required=True)
    report.add_argument("--attempt-id", required=True)
    report.add_argument("--allowance", type=int, required=True)
    report.add_argument("--github-output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "initialize":
            initialize(args.file, attempt_id=args.attempt_id, allowance=args.allowance)
            _append_outputs(
                None,
                {"attempt_id": args.attempt_id, "request_start_allowance": args.allowance},
            )
        else:
            starts = read_count(
                args.file,
                attempt_id=args.attempt_id,
                allowance=args.allowance,
            )
            _append_outputs(args.github_output, {"actual_request_starts": starts})
    except (BuildkiteRequestGuardError, OSError, ValueError) as exc:
        print(f"Buildkite request guard error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
