#!/usr/bin/env python3
"""Classify the docker builds first collection for the publication audit.

Until ``docker-builds-data`` exists, the public file is the not-collected seed.
The seed is pending rather than degraded while the latest completed
request-bearing run saved resumable progress and the unpublished attempts stay
below the attempt ledger's budget. Any other bootstrap state stays degraded.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vllm.publication_surfaces import DOCKER_BUILDS_PENDING_CODE as PENDING_CODE  # noqa: E402

SCHEMA_VERSION = 1
EVIDENCE_ENV = "DOCKER_BUILDS_BOOTSTRAP_EVIDENCE"
EVIDENCE_MAX_AGE = timedelta(hours=2)
EVIDENCE_MAX_BYTES = 4096
DATA_BRANCH = "docker-builds-data"
BUDGET_CONFIG = Path(__file__).resolve().parents[2] / "config/docker_builds_attempt_budget.json"
STATES = frozenset({"pending", "over-budget", "failed", "unknown", "collected"})
MAX_RUN_LOOKUPS = 4

RunResult = Mapping[str, Any]
RunFetcher = Callable[[str, int], RunResult]


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def classify(
    *,
    data_branch_exists: bool,
    attempts: Iterable[Mapping[str, Any]],
    max_attempts: int,
    fetch_run: RunFetcher,
    now: datetime,
) -> dict[str, Any]:
    """Return the bootstrap evidence for the active attempt-ledger rows."""
    rows = list(attempts)
    unpublished = [row for row in rows if row.get("succeeded_at") is None]
    evidence: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": _iso(now),
        "state": "unknown",
        "attempts": len(unpublished),
        "max_attempts": max_attempts,
        "run_id": None,
    }
    if data_branch_exists:
        evidence["state"] = "collected"
        return evidence
    if len(unpublished) >= max_attempts:
        evidence["state"] = "over-budget"
        return evidence
    runtime = sorted(
        (row for row in rows if row.get("source") == "runtime"),
        key=lambda row: (str(row.get("reserved_at")), str(row.get("id"))),
        reverse=True,
    )
    for row in runtime[:MAX_RUN_LOOKUPS]:
        run = fetch_run(str(row["workflow_run_id"]), int(row["workflow_run_attempt"]))
        if run.get("status") != "completed":
            continue
        evidence["run_id"] = str(row["workflow_run_id"])
        resumable = run.get("conclusion") == "success" and row.get("succeeded_at") is None
        evidence["state"] = "pending" if resumable else "failed"
        break
    return evidence


def read_evidence(path: str | os.PathLike[str] | None, *, now: datetime) -> dict[str, Any] | None:
    """Return fresh, well-formed bootstrap evidence, or None to fail closed."""
    if not path:
        return None
    candidate = Path(path)
    try:
        if not candidate.is_file() or candidate.is_symlink():
            return None
        raw = candidate.read_bytes()
        if len(raw) > EVIDENCE_MAX_BYTES:
            return None
        payload = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    observed_at = _parse(payload.get("observed_at"))
    attempts = payload.get("attempts")
    max_attempts = payload.get("max_attempts")
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("state") not in STATES
        or observed_at is None
        or observed_at > now + timedelta(minutes=5)
        or now - observed_at > EVIDENCE_MAX_AGE
        or type(attempts) is not int
        or type(max_attempts) is not int
        or not 0 <= attempts
        or max_attempts <= 0
    ):
        return None
    return payload


def bootstrap_pending(evidence: Mapping[str, Any] | None) -> bool:
    return bool(
        evidence
        and evidence["state"] == "pending"
        and evidence["attempts"] < evidence["max_attempts"]
    )


def _data_branch_exists(root: Path, remote: str) -> bool:
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--refs", remote, f"refs/heads/{DATA_BRANCH}"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 2:
        return False
    raise RuntimeError("could not determine the docker builds data branch state")


def _github_run_fetcher(repository: str, token: str) -> RunFetcher:
    def fetch(run_id: str, attempt: int) -> RunResult:
        if not run_id.isdigit() or attempt <= 0:
            return {}
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/attempts/{attempt}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read(1024 * 1024))
        return {"status": payload.get("status"), "conclusion": payload.get("conclusion")}

    return fetch


def observe(
    root: Path,
    *,
    remote: str,
    fetch_run: RunFetcher,
    now: datetime,
    config: Path = BUDGET_CONFIG,
) -> dict[str, Any]:
    from vllm import request_bearing_attempt_budget as budget

    policy = budget.load_policy(config)
    exists = _data_branch_exists(root, remote)
    attempts: list[dict[str, Any]] = []
    if not exists:
        ledger_sha = budget._remote_sha(root, remote, policy)
        if ledger_sha is not None:
            ledger = budget._fetch_observed(root, remote, ledger_sha, policy).ledger
            attempts = budget._active_attempts(ledger, now=now, policy=policy)
    return classify(
        data_branch_exists=exists,
        attempts=attempts,
        max_attempts=policy.max_request_bearing_attempts,
        fetch_run=fetch_run,
        now=now,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not args.repository or not token:
        parser.error("a repository and GITHUB_TOKEN are required")
    evidence = observe(
        args.root.resolve(),
        remote=args.remote,
        fetch_run=_github_run_fetcher(args.repository, token),
        now=datetime.now(timezone.utc),
    )
    args.output.write_text(json.dumps(evidence, sort_keys=True) + "\n")
    print(
        f"Docker builds bootstrap: {evidence['state']} "
        f"({evidence['attempts']}/{evidence['max_attempts']} unpublished attempts)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
