#!/usr/bin/env python3
"""Bounded retries for GitHub ref pushes and coalesced REST writes.

GitHub can answer a push with a 5xx after the ref update already landed. A
transient push failure is therefore reconciled against ``ls-remote``: every
target at its new commit is success, every leased target still at its expected
commit is retried, and anything else is the original definitive failure.
When ``ls-remote`` itself fails the push is re-sent: a lease (or the
fast-forward rule) still guards every ref, so a re-sent atomic push can only
complete refs left at their expected commits. Lease, fast-forward, hook and
authorization rejections are never retried.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping, Sequence


# Sized for the tightest caller: DNS budget reservation has a two-minute step.
PUSH_RETRY_DELAYS: tuple[float, ...] = (3, 10, 20)
READ_RETRY_DELAYS: tuple[float, ...] = (2, 5)
GH_API_RETRY_DELAYS: tuple[float, ...] = (5, 15, 30)
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
_REF_STATUS_RE = re.compile(
    r"^\s*! \[(?:remote )?(?:rejected|failure)\]\s+\S+ -> \S+ \((?P<reason>[^\n]*)\)\s*$",
    re.MULTILINE,
)
_TRANSIENT_REASONS = frozenset(
    {
        "Internal Server Error",
        "failure",
        "Bad Gateway",
        "Service Unavailable",
        "Gateway Timeout",
        "remote failed to report status",
    }
)
_ATOMIC_REASONS = frozenset({"atomic push failed", "atomic transaction failed"})
_DEFINITIVE_RE = re.compile(
    r"returned error: 4(?!08|29)\d\d|HTTP 4(?!08|29)\d\d\b|Authentication failed"
    r"|could not read (?:Username|Password)|terminal prompts disabled"
    r"|Permission to \S+ denied|repository '[^']*' not found"
    r"|does not appear to be a git repository|invalid refspec|GH\d{3}:"
)
_TRANSIENT_RE = re.compile(
    r"RPC failed|remote end hung up|unexpected disconnect|early EOF"
    r"|returned error: (?:408|429|5\d\d)|HTTP (?:408|429|5\d\d)\b"
    r"|Internal Server Error|fatal error in commit_refs|Could not resolve host"
    r"|Failed to connect|Connection (?:reset|refused|timed out)|timed out|timeout"
    r"|Empty reply from server|transfer closed|SSL_ERROR_SYSCALL|SSL_read|gnutls"
    r"|unexpected end of JSON input|error connecting to",
    re.IGNORECASE,
)


class GitPushError(RuntimeError):
    """A push failed definitively or did not converge within its retry budget."""


def is_transient(detail: str) -> bool:
    """Whether a failed git or gh command may succeed unchanged on retry."""
    reasons = {match["reason"] for match in _REF_STATUS_RE.finditer(detail)}
    if reasons:
        return reasons <= _TRANSIENT_REASONS | _ATOMIC_REASONS and bool(
            reasons & _TRANSIENT_REASONS
        )
    return _DEFINITIVE_RE.search(detail) is None and _TRANSIENT_RE.search(detail) is not None


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=False, env=env
    )


def _detail(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stderr.decode("utf-8", errors="replace").strip()[-2000:]


def remote_refs(
    root: Path,
    remote: str,
    refs: Sequence[str],
    *,
    delays: Sequence[float] = READ_RETRY_DELAYS,
) -> dict[str, str | None]:
    """Read exact remote refs; a missing ref is ``None``."""
    for delay in (*delays, None):
        result = _git(root, "ls-remote", "--refs", remote, *refs)
        if result.returncode == 0:
            break
        detail = _detail(result)
        if delay is None or not is_transient(detail):
            raise GitPushError(f"git ls-remote {remote} failed: {detail}")
        time.sleep(delay)
    observed: dict[str, str | None] = dict.fromkeys(refs)
    for line in result.stdout.decode("ascii", errors="replace").splitlines():
        sha, _, ref = line.partition("\t")
        if ref not in observed:
            continue
        if observed[ref] is not None or FULL_SHA_RE.fullmatch(sha) is None:
            raise GitPushError(f"remote returned an ambiguous {ref}")
        observed[ref] = sha
    return observed


def push_refs(
    root: Path,
    remote: str,
    updates: Mapping[str, str],
    *,
    leases: Mapping[str, str | None] | None = None,
    atomic: bool = False,
    delays: Sequence[float] | None = None,
) -> None:
    """Push ``ref -> sha`` updates, retrying only provably safe transient failures.

    ``leases`` maps every ref to its exact expected SHA (``None`` means absent).
    Without leases the push is a plain fast-forward, which git never rewinds.
    """
    refs = list(updates)
    if leases is not None and set(leases) != set(refs):
        raise GitPushError("every pushed ref needs exactly one lease")
    command = ["push"]
    if atomic:
        command.append("--atomic")
    if leases is not None:
        command.extend(f"--force-with-lease={ref}:{leases[ref] or ''}" for ref in refs)
    command.extend([remote, *(f"{updates[ref]}:{ref}" for ref in refs)])
    label = f"git {' '.join(command[:3])} failed"
    for delay in (*(PUSH_RETRY_DELAYS if delays is None else delays), None):
        result = _git(root, *command)
        if result.returncode == 0:
            if remote_refs(root, remote, refs) != dict(updates):
                raise GitPushError(f"{label}: post-push verification disagreed")
            return
        error = GitPushError(f"{label}: {_detail(result)}")
        if not is_transient(_detail(result)):
            raise error
        try:
            observed: dict[str, str | None] | None = remote_refs(
                root, remote, refs, delays=()
            )
        except GitPushError:
            observed = None
        if observed == dict(updates):
            return
        if delay is None or (
            leases is not None and observed is not None and observed != dict(leases)
        ):
            raise error
        print(
            f"::warning::{label} transiently; retrying in {delay:g}s",
            file=sys.stderr,
        )
        time.sleep(delay)


def gh_api(
    args: Sequence[str],
    payload: bytes,
    *,
    delays: Sequence[float] | None = None,
) -> int:
    """Run ``gh api`` for a coalesced write, retrying transient GitHub errors."""
    for delay in (*(GH_API_RETRY_DELAYS if delays is None else delays), None):
        result = subprocess.run(
            ["gh", "api", *args], input=payload, capture_output=True, check=False
        )
        sys.stdout.buffer.write(result.stdout)
        sys.stdout.flush()
        detail = result.stderr.decode("utf-8", errors="replace")
        if result.returncode == 0 or delay is None or not is_transient(detail):
            sys.stderr.write(detail)
            return result.returncode
        print(
            f"::warning::gh api failed transiently ({detail.strip()[-300:]}); "
            f"retrying in {delay:g}s",
            file=sys.stderr,
        )
        time.sleep(delay)
    raise AssertionError("unreachable")


def _resolve_commit(root: Path, rev: str) -> str:
    result = _git(root, "rev-parse", "--verify", f"{rev}^{{commit}}")
    sha = result.stdout.decode("ascii", errors="replace").strip()
    if result.returncode or FULL_SHA_RE.fullmatch(sha) is None:
        raise GitPushError(f"{rev} does not resolve to one local commit")
    return sha


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    push = subparsers.add_parser("push")
    push.add_argument("--root", type=Path, default=Path.cwd())
    push.add_argument("--remote", default="origin")
    push.add_argument("--ref", required=True, help="full target ref, e.g. refs/heads/x")
    push.add_argument("--source", default="HEAD", help="local commit to publish")
    push.add_argument("--expect", help="exact leased remote SHA; empty means absent")
    api = subparsers.add_parser("gh-api")
    api.add_argument("gh_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if args.command == "gh-api":
        gh_args = args.gh_args[1:] if args.gh_args[:1] == ["--"] else args.gh_args
        reads_stdin = any(
            gh_args[i] == "--input" and gh_args[i + 1] == "-"
            for i in range(len(gh_args) - 1)
        )
        return gh_api(gh_args, sys.stdin.buffer.read() if reads_stdin else b"")

    try:
        if not args.ref.startswith("refs/heads/"):
            raise GitPushError("--ref must be a full refs/heads/ name")
        if args.expect and FULL_SHA_RE.fullmatch(args.expect) is None:
            raise GitPushError("--expect must be a full SHA or empty")
        source = _resolve_commit(args.root, args.source)
        leases = None if args.expect is None else {args.ref: args.expect or None}
        push_refs(args.root, args.remote, {args.ref: source}, leases=leases)
    except GitPushError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    print(f"Published {args.ref} at {source}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
