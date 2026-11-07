"""Retain recent GitHub deployment records while protecting the serving site.

The default mode is read-only. Only inactive records outside the newest twenty
can be deleted. A superseded terminal native Pages record can first become
inactive after a newer protected success proves the current gh-pages commit.
The serving deployment, other active states, and other environments are untouched.

REST authority: https://docs.github.com/en/rest/deployments/deployments
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

ROOT = Path(__file__).resolve().parents[2]
API_ROOT = "https://api.github.com"
KEEP = 20
MAX_PAGES = 100
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
REPOSITORY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*")


class RetentionError(RuntimeError):
    """Retention stopped without weakening a protection."""


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise RetentionError("missing deployment timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise RetentionError("invalid deployment timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RetentionError("deployment timestamp must be UTC")
    return parsed


def normalize_repository(value: str) -> str:
    if not REPOSITORY_RE.fullmatch(value) or any(part in {".", ".."} for part in value.split("/")):
        raise RetentionError("expected one owner/repository name")
    return value


class GitHub:
    """Bound all requests, retries, and waits by one monotonic deadline."""

    def __init__(
        self, repository: str, token: str, *, timeout_seconds: int = 1200,
        session: requests.Session | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.repository = normalize_repository(repository)
        if not 30 <= timeout_seconds <= 1500:
            raise RetentionError("timeout must be between 30 and 1500 seconds")
        self.session = session if session is not None else requests.Session()
        self.monotonic, self.sleep = monotonic, sleep
        self.deadline = monotonic() + timeout_seconds
        self.last_mutation_at: float | None = None
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def wait(self, seconds: float) -> None:
        if seconds < 0 or self.monotonic() + seconds + 1 >= self.deadline:
            raise RetentionError("retention time budget exhausted")
        if seconds:
            self.sleep(seconds)

    def request(self, method: str, suffix: str, *, payload: dict | None = None) -> requests.Response:
        read = method == "GET" and (
            re.fullmatch(r"/deployments(?:/[1-9]\d*/statuses)?\?per_page=100&page=[1-9]\d*", suffix)
            or suffix == "/git/ref/heads/gh-pages"
        )
        delete = method == "DELETE" and re.fullmatch(r"/deployments/[1-9]\d*", suffix)
        retire = (
            method == "POST" and re.fullmatch(r"/deployments/[1-9]\d*/statuses", suffix)
            and payload == {
                "state": "inactive", "auto_inactive": False,
                "environment": "github-pages",
                "description": "Superseded Pages deployment outside the newest 20 records",
            }
        )
        if not (read or delete or retire) or (method != "POST" and payload is not None):
            raise RetentionError("request outside deployment retention authority")
        url = f"{API_ROOT}/repos/{self.repository}{suffix}"
        for attempt in range(3):
            remaining = self.deadline - self.monotonic()
            if remaining <= 1:
                raise RetentionError("retention time budget exhausted")
            if method != "GET" and self.last_mutation_at is not None:
                self.wait(max(0, self.last_mutation_at + 1 - self.monotonic()))
                remaining = self.deadline - self.monotonic()
            try:
                if method != "GET":
                    self.last_mutation_at = self.monotonic()
                response = self.session.request(
                    method, url, headers=self.headers, allow_redirects=False,
                    timeout=min(30, remaining - 1), json=payload,
                )
            except requests.RequestException:
                if method == "POST":
                    # Status creation is not idempotent. A transport ambiguity
                    # leaves an old record intact for the next inventory.
                    raise RetentionError("old Pages retirement response was ambiguous") from None
                if attempt == 2:
                    raise RetentionError("GitHub transport failed after bounded retries") from None
                self.wait(2 ** attempt)
                continue
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise RetentionError("GitHub response exceeds the retention byte bound")
            limited = response.status_code == 429 or (
                response.status_code == 403 and (
                    response.headers.get("Retry-After") is not None
                    or response.headers.get("X-RateLimit-Remaining") == "0"
                )
            )
            if limited or response.status_code in {500, 502, 503, 504}:
                if method == "POST":
                    raise RetentionError("old Pages retirement needs a new inventory")
                if attempt == 2:
                    raise RetentionError("GitHub rate or service limit persisted")
                delay = 2 ** attempt
                if limited:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after is not None:
                        try:
                            delay = max(1, int(retry_after))
                        except ValueError:
                            raise RetentionError("invalid GitHub retry delay") from None
                    elif response.headers.get("X-RateLimit-Remaining") == "0":
                        try:
                            delay = max(1, int(response.headers["X-RateLimit-Reset"]) - int(time.time()) + 1)
                        except (KeyError, ValueError):
                            raise RetentionError("missing GitHub rate reset") from None
                    else:
                        delay = 60
                self.wait(delay)
                continue
            return response
        raise RetentionError("unreachable retry limit")

    def list_rows(self, suffix: str) -> list[dict]:
        rows: list[dict] = []
        identifiers: set[int] = set()
        for page in range(1, MAX_PAGES + 1):
            response = self.request("GET", f"{suffix}?per_page=100&page={page}")
            if response.status_code == 404 and suffix != "/deployments":
                return []
            if response.status_code != 200:
                raise RetentionError(f"GitHub list failed with HTTP {response.status_code}")
            try:
                current = response.json()
            except ValueError:
                raise RetentionError("invalid GitHub JSON response") from None
            if not isinstance(current, list) or len(current) > 100:
                raise RetentionError("invalid paginated deployment list")
            for row in current:
                if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0:
                    raise RetentionError("invalid deployment record identity")
                if row["id"] in identifiers:
                    raise RetentionError("deployment pagination changed; retry a new inventory")
                _timestamp(row.get("created_at"))
                identifiers.add(row["id"])
                rows.append(row)
            if len(current) < 100:
                return rows
        raise RetentionError("deployment pagination exceeds the inventory bound")

    def pages_head(self) -> str | None:
        response = self.request("GET", "/git/ref/heads/gh-pages")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RetentionError(f"Pages reference read failed with HTTP {response.status_code}")
        try:
            value = response.json()
        except ValueError:
            raise RetentionError("invalid Pages reference JSON") from None
        obj = value.get("object") if isinstance(value, dict) else None
        if (
            not isinstance(obj, dict) or value.get("ref") != "refs/heads/gh-pages"
            or obj.get("type") != "commit"
            or not isinstance(obj.get("sha"), str)
            or re.fullmatch(r"[0-9a-f]{40}", obj["sha"]) is None
        ):
            raise RetentionError("invalid exact Pages reference")
        return obj["sha"]


def _latest_status(github: GitHub, identifier: int) -> dict:
    statuses = github.list_rows(f"/deployments/{identifier}/statuses")
    return max(statuses, key=lambda status: (_timestamp(status["created_at"]), status["id"]), default={})


def _native_pages(row: dict) -> bool:
    creator = row.get("creator")
    return (
        row.get("environment") == "github-pages" and row.get("ref") == "gh-pages"
        and row.get("task") == "deploy"
        and row.get("transient_environment") is False
        and row.get("production_environment") is False
        and isinstance(creator, dict) and creator.get("login") == "github-pages[bot]"
        and creator.get("type") == "Bot"
        and isinstance(row.get("sha"), str)
        and re.fullmatch(r"[0-9a-f]{40}", row["sha"]) is not None
    )


def _serving_pages(github: GitHub, protected: list[dict]) -> tuple[dict, dict] | None:
    possible = [row for row in protected if _native_pages(row)]
    if not possible:
        return None
    head = github.pages_head()
    for row in possible:
        if row["sha"] != head:
            continue
        status = _latest_status(github, row["id"])
        url = status.get("environment_url")
        if (
            status.get("state") == "success" and status.get("environment") == "github-pages"
            and isinstance(url, str) and url.startswith("https://")
        ):
            return row, status
    return None


def _retire_superseded_pages(
    github: GitHub, journal: Journal, row: dict, status: dict,
    serving: tuple[dict, dict] | None, *, apply: bool,
) -> bool:
    if serving is None or status.get("state") not in {"success", "failure", "error"} or not _native_pages(row):
        return False
    current, current_status = serving
    if (
        row["id"] == current["id"]
        or _timestamp(row["created_at"]) >= _timestamp(current["created_at"])
        or status.get("environment") != "github-pages"
        or status.get("environment_url") != current_status["environment_url"]
    ):
        return False
    # Bind the current success to the still-current source ref, then recheck
    # the old candidate immediately before its narrowly scoped status write.
    if (
        github.pages_head() != current["sha"]
        or _latest_status(github, current["id"]) != current_status
        or _latest_status(github, row["id"]) != status
    ):
        journal.record("protected", id=row["id"], reason="serving_or_candidate_changed")
        return False
    journal.record(
        "retire_intent" if apply else "would_retire", id=row["id"],
        candidate_status_id=status["id"], serving_id=current["id"],
        serving_status_id=current_status["id"], serving_sha=current["sha"],
    )
    if not apply:
        return True
    response = github.request("POST", f"/deployments/{row['id']}/statuses", payload={
        "state": "inactive", "auto_inactive": False, "environment": "github-pages",
        "description": "Superseded Pages deployment outside the newest 20 records",
    })
    if response.status_code in {404, 422}:
        journal.record("protected", id=row["id"], reason="retirement_rejected", http_status=response.status_code)
        return False
    if response.status_code != 201:
        raise RetentionError(f"old Pages retirement failed with HTTP {response.status_code}")
    retired = _latest_status(github, row["id"])
    if retired.get("state") != "inactive":
        journal.record("protected", id=row["id"], reason="retirement_not_confirmed")
        return False
    journal.record("retired", id=row["id"], status_id=retired["id"])
    return True


class Journal:
    """A private, append-only, flushed record outside the repository."""

    def __init__(self, path: Path):
        absolute = path.absolute()
        if any(parent.is_symlink() for parent in (absolute, *absolute.parents)):
            raise RetentionError("journal symlinks are forbidden")
        if absolute.is_relative_to(ROOT):
            raise RetentionError("journal must be outside the checkout")
        self.path = absolute
        try:
            self.fd = os.open(absolute, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError:
            raise RetentionError("choose an unused private journal path") from None

    def record(self, event: str, **fields: object) -> None:
        raw = (json.dumps({"event": event, **fields}, sort_keys=True, allow_nan=False) + "\n").encode()
        offset = 0
        while offset < len(raw):
            offset += os.write(self.fd, raw[offset:])
        os.fsync(self.fd)

    def close(self) -> None:
        os.close(self.fd)


def run_retention(
    github: GitHub, journal: Journal, *, apply: bool = False,
    max_deletions: int = 250, started_at: datetime | None = None,
) -> dict:
    """Delete only a bounded subset of the original inactive inventory."""
    if type(max_deletions) is not int or not 1 <= max_deletions <= 1000:
        raise RetentionError("deletion bound must be between 1 and 1000")
    started_at = started_at or datetime.now(timezone.utc)
    if started_at.tzinfo is None or started_at.utcoffset() != timezone.utc.utcoffset(started_at):
        raise RetentionError("retention start must be UTC")
    inventory = sorted(
        github.list_rows("/deployments"),
        key=lambda row: (_timestamp(row["created_at"]), row["id"]), reverse=True,
    )
    keep_ids = [row["id"] for row in inventory[:KEEP]]
    journal.record(
        "inventory", repository=github.repository, apply=apply, keep=KEEP,
        started_at=started_at.isoformat(), newest_kept_ids=keep_ids,
        records=[{key: row.get(key) for key in ("id", "created_at", "environment", "sha")} for row in inventory],
    )
    counts: Counter[str] = Counter()
    candidates = inventory[KEEP:]
    serving = _serving_pages(github, inventory[:KEEP]) if any(_native_pages(row) for row in candidates) else None
    for index, row in enumerate(candidates):
        if counts["deleted"] + counts["would_delete"] >= max_deletions:
            counts["deferred"] = len(candidates) - index
            break
        identifier = row["id"]
        if _timestamp(row["created_at"]) >= started_at:
            counts["new"] += 1
            journal.record("protected", id=identifier, reason="created_after_start")
            continue
        latest = _latest_status(github, identifier)
        state = latest.get("state")
        retired = False
        if state in {"success", "failure", "error"}:
            retired = _retire_superseded_pages(github, journal, row, latest, serving, apply=apply)
        if state != "inactive" and not retired:
            reason = state if isinstance(state, str) and state in {
                "success", "pending", "in_progress", "queued", "failure", "error",
            } else "unknown"
            counts[f"protected_{reason}"] += 1
            journal.record("protected", id=identifier, reason=reason)
            continue
        if retired:
            counts["retired" if apply else "would_retire"] += 1
        journal.record("delete_intent" if apply else "would_delete", id=identifier, status_id=latest["id"])
        if not apply:
            counts["would_delete"] += 1
            continue
        response = github.request("DELETE", f"/deployments/{identifier}")
        if response.status_code == 204:
            outcome = "deleted"
        elif response.status_code == 404:
            outcome = "already_absent"
        elif response.status_code == 422:
            # A deployment may have become active after the status read. Leave
            # it intact; a rejected deletion never grants status-write authority.
            outcome = "protected_delete_rejected"
        else:
            journal.record("delete_failed", id=identifier, http_status=response.status_code)
            raise RetentionError(f"GitHub deletion failed with HTTP {response.status_code}")
        counts[outcome] += 1
        journal.record(outcome, id=identifier, http_status=response.status_code)
    remaining = github.list_rows("/deployments")
    remaining_ids = {row["id"] for row in remaining}
    if not set(keep_ids).issubset(remaining_ids):
        raise RetentionError("a protected recent record changed during retention")
    report = {
        "repository": github.repository, "apply": apply, "keep": KEEP,
        "initial_count": len(inventory), "remaining_count": len(remaining),
        "newest_kept_ids": keep_ids, "candidate_count": len(candidates),
        "counts": dict(sorted(counts.items())), "completed": True,
    }
    journal.record("summary", **report)
    return report


def _write_step_summary(report: dict | None, failed: bool) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as output:
        output.write("### Deployment record retention\n\n")
        if failed or report is None:
            output.write("Stopped safely before completing; consult the retained job journal in the runner temporary directory.\n")
        else:
            counts = report["counts"]
            mode = "Applied" if report["apply"] else "Read-only preview"
            output.write(
                f"{mode}: {report['initial_count']} initial records, "
                f"{report['remaining_count']} remaining. Newest {KEEP} retained; "
                "serving, in-flight, and unknown records were preserved.\n\n"
            )
            for name, count in counts.items():
                output.write(f"- {name.replace('_', ' ')}: {count}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--apply", action="store_true", help="delete eligible inactive records")
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--max-deletions", type=int, default=250)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    journal: Journal | None = None
    report = None
    failed = True
    try:
        if args.apply and not token:
            raise RetentionError("a deployment-write token is required for --apply")
        github = GitHub(args.repository, token, timeout_seconds=args.timeout_seconds)
        journal = Journal(args.journal)
        report = run_retention(github, journal, apply=args.apply, max_deletions=args.max_deletions)
        failed = False
        print(json.dumps(report, sort_keys=True))
        return 0
    except (RetentionError, OSError) as exc:
        # Error messages contain neither response bodies nor authorization data.
        if journal is not None:
            journal.record("stopped", completed=False, reason=str(exc))
        print(f"Deployment retention stopped: {exc}")
        return 1
    finally:
        if journal is not None:
            journal.close()
        _write_step_summary(report, failed)


if __name__ == "__main__":
    raise SystemExit(main())
