#!/usr/bin/env python3
"""Queue zombie-job watcher.

Reads ``data/vllm/ci/queue_jobs.json`` and opens or updates GitHub issues for
AMD queues that currently have waiting or running jobs older than the configured
zombie threshold. Unlike the latency watcher, this watcher updates the issue
body in place and avoids hourly comment spam.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.constants import (  # noqa: E402
    AMD_QUEUE_PREFIX,
    QUEUE_ZOMBIE_THRESHOLD_MIN,
)
from vllm.ci.managed_issue import (  # noqa: E402
    IssueLookupError,
    MAX_DIRECT_ISSUE_LOOKUPS,
    bounded_open_issue_candidates,
    fetch_open_issue_candidate,
    repair_issue_labels,
)
from vllm.ci.watcher_state import write_watcher_state  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
JOBS = ROOT / "data" / "vllm" / "ci" / "queue_jobs.json"
STATE = ROOT / "data" / "vllm" / "ci" / "open_queue_zombie_issues.json"
LABEL = "queue-zombie"
AUTOMATED_LABEL = "automated"
WORKSTREAM_LABEL = "workstream:infra"
OWNED_LABELS = frozenset({LABEL, AUTOMATED_LABEL, WORKSTREAM_LABEL})
OWNERSHIP_MARKER = "<!-- vllm-ci-dashboard:managed-alert:queue-zombie:v1 -->"
MAX_SNAPSHOT_AGE = timedelta(hours=6)
MAX_FUTURE_SKEW = timedelta(minutes=15)
GH_API = "https://api.github.com"
DASHBOARD_REPO = "AndreasKaratzas/vllm-ci-dashboard"


def _validate_target_repo(repo: str) -> None:
    if repo.strip().lower() != DASHBOARD_REPO.lower():
        raise RuntimeError(f"Issue automation is restricted to {DASHBOARD_REPO}")


def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_snapshot_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed.astimezone(timezone.utc)


def _snapshot_is_fresh(data: dict, now: datetime | None = None) -> bool:
    snapshot_ts = _parse_snapshot_ts(data.get("ts"))
    if snapshot_ts is None:
        return False
    age = (now or _utc_now()) - snapshot_ts
    return -MAX_FUTURE_SKEW <= age <= MAX_SNAPSHOT_AGE


def _issue_label_names(issue: dict) -> set[str]:
    names: set[str] = set()
    for label in issue.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else label
        if isinstance(name, str) and name:
            names.add(name)
    return names


def _has_exact_marker(body: str) -> bool:
    return any(line.strip() == OWNERSHIP_MARKER for line in body.splitlines())


def _owned_queue_issue(issue: object) -> dict | None:
    """Return strictly identified queue-zombie issue metadata.

    New issues are owned by the exact HTML marker. Legacy issues predate that
    marker and are adopted only when all managed labels, the generated body
    signature, queue sentence, and title agree. A broad label match alone is
    never enough authority to mutate an issue.
    """
    if not isinstance(issue, dict) or "pull_request" in issue:
        return None
    number = issue.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        return None

    body = str(issue.get("body") or "")
    has_marker = _has_exact_marker(body)
    if not has_marker:
        if not OWNED_LABELS.issubset(_issue_label_names(issue)):
            return None
        if "*Managed by `queue_zombie_watcher.py` from " not in body:
            return None

    match = re.search(
        r"^Queue \*\*`([^`\r\n]+)`\*\* currently has waiting or running jobs older than ",
        body,
        flags=re.MULTILINE,
    )
    if not match:
        return None
    queue = match.group(1)
    title = str(issue.get("title") or "")
    title_pattern = (
        rf"^Queue {re.escape(queue)}: zombie jobs > "
        rf"{QUEUE_ZOMBIE_THRESHOLD_MIN // 60}h \(\d+\)$"
    )
    if not re.fullmatch(title_pattern, title):
        return None
    return {
        "number": number,
        "queue": queue,
        "created_at": str(issue.get("created_at") or ""),
        "labels": issue.get("labels") or [],
        "legacy": not has_marker,
    }


def _list_owned_open_issues(
    token: str,
    repo: str,
    *,
    include_recovery: bool = True,
    tracked_numbers: tuple[int, ...] = (),
) -> list[dict] | None:
    """Return all provably owned open issues, or ``None`` on partial lookup."""
    owned_by_number: dict[int, dict] = {}
    tracked = sorted(set(tracked_numbers))
    if len(tracked) > MAX_DIRECT_ISSUE_LOOKUPS:
        log.warning(
            "Queue-zombie recovery has %d tracked numbers; refusing the "
            "bounded direct-lookup limit of %d",
            len(tracked),
            MAX_DIRECT_ISSUE_LOOKUPS,
        )
        return None
    try:
        for number in tracked:
            issue = fetch_open_issue_candidate(
                token,
                repo,
                number,
                request_get=requests.get,
            )
            if issue is None:
                continue
            normalized = _owned_queue_issue(issue)
            if normalized is None:
                raise IssueLookupError(
                    f"tracked queue-zombie issue #{number} lost exact ownership"
                )
            owned_by_number[number] = normalized
        if tracked_numbers and not include_recovery:
            return [owned_by_number[number] for number in sorted(owned_by_number)]
        candidates = bounded_open_issue_candidates(
            token,
            repo,
            durable_label=LABEL,
            recovery_labels=(AUTOMATED_LABEL, WORKSTREAM_LABEL),
            include_recovery=include_recovery,
            exact_candidate=lambda issue: _owned_queue_issue(issue) is not None,
            request_get=requests.get,
        )
    except (IssueLookupError, ValueError) as error:
        log.warning("Queue-zombie issue recovery lookup failed: %s", error)
        return None
    for issue in candidates:
        normalized = _owned_queue_issue(issue)
        if normalized is not None:
            owned_by_number[normalized["number"]] = normalized
    return [owned_by_number[number] for number in sorted(owned_by_number)]


def _repo_owner(repo: str) -> str:
    return (repo.split("/", 1)[0] if "/" in repo else repo or "AndreasKaratzas").strip() or "AndreasKaratzas"


def _read_jobs() -> dict | None:
    if not JOBS.exists():
        return None
    try:
        payload = json.loads(JOBS.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_entry(entry: int | dict) -> dict:
    if isinstance(entry, dict):
        raw_number = entry.get("number")
        number = (
            int(raw_number)
            if isinstance(raw_number, int) and not isinstance(raw_number, bool)
            else 0
        )
        return {
            **entry,
            "number": max(0, number),
            "opened_ts": str(entry.get("opened_ts") or ""),
            "last_fingerprint": str(entry.get("last_fingerprint") or ""),
        }
    if not isinstance(entry, int) or isinstance(entry, bool):
        return {"number": 0, "opened_ts": "", "last_fingerprint": ""}
    return {
        "number": max(0, entry),
        "opened_ts": "",
        "last_fingerprint": "",
    }


def _read_state() -> dict:
    if not STATE.exists():
        return {"open": {}, "last_run": ""}
    try:
        data = json.loads(STATE.read_text())
        if not isinstance(data, dict):
            return {"open": {}, "last_run": ""}
        raw_open = data.get("open")
        data["open"] = {
            str(queue): _normalize_entry(entry)
            for queue, entry in (
                raw_open.items() if isinstance(raw_open, dict) else []
            )
            if isinstance(queue, str) and queue
        }
        data.setdefault("last_run", "")
        return data
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {"open": {}, "last_run": ""}


def _write_state(state: dict) -> None:
    write_watcher_state(
        STATE,
        state,
        state_filename="open_queue_zombie_issues.json",
    )


def _job_age(job: dict) -> float:
    if job.get("state") == "scheduled":
        return float(job.get("wait_min") or 0)
    return float(job.get("run_min") or 0)


def _group_zombies(data: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for bucket in ("pending", "running"):
        for job in data.get(bucket) or []:
            queue = job.get("queue") or ""
            if not queue.startswith(AMD_QUEUE_PREFIX):
                continue
            age = _job_age(job)
            is_zombie = bool(job.get("analysis_excluded")) or age >= QUEUE_ZOMBIE_THRESHOLD_MIN
            if not is_zombie:
                continue
            grouped.setdefault(queue, []).append(job)
    for jobs in grouped.values():
        jobs.sort(key=_job_age, reverse=True)
    return grouped


def _fingerprint(queue: str, jobs: list[dict], jobs_ts: str) -> str:
    compact = [
        {
            "state": job.get("state") or "",
            "build": int(job.get("build") or 0),
            "pipeline": job.get("pipeline") or "",
            "queue": job.get("queue") or "",
            "age": round(_job_age(job), 1),
            "name": job.get("name") or "",
        }
        for job in jobs
    ]
    return json.dumps({"queue": queue, "ts": jobs_ts, "jobs": compact}, sort_keys=True, separators=(",", ":"))


def _issue_title(queue: str, jobs: list[dict]) -> str:
    return f"Queue {queue}: zombie jobs > {QUEUE_ZOMBIE_THRESHOLD_MIN // 60}h ({len(jobs)})"


def _issue_body(queue: str, jobs: list[dict], opened_ts: str, jobs_ts: str, run_url: str, owner_login: str) -> str:
    lines = [
        OWNERSHIP_MARKER,
        "## Queue zombie-job alert",
        "",
        f"Queue **`{queue}`** currently has waiting or running jobs older than "
        f"**{QUEUE_ZOMBIE_THRESHOLD_MIN // 60} hours**.",
        "",
        "These jobs are excluded from queue analytics so they do not distort the dashboard's queue counts or wait percentiles.",
        "",
        f"Issue opened at `{opened_ts or jobs_ts or 'unknown'}`.",
        f"Latest queue snapshot: `{jobs_ts or 'unknown'}`.",
        "",
        "| state | age | build | branch | job | review |",
        "|---|---:|---|---|---|---|",
    ]
    for job in jobs:
        age = _job_age(job)
        build_ref = f"{job.get('pipeline') or '?'} #{int(job.get('build') or 0)}"
        review = job.get("url") or ""
        review_md = f"[Buildkite]({review})" if review else "—"
        lines.append(
            f"| {job.get('state') or '?'} | {age:.1f}m | {build_ref} | "
            f"`{job.get('branch') or '—'}` | {job.get('name') or '—'} | {review_md} |"
        )
    lines.extend([
        "",
        f"GitHub assignee: {owner_login}.",
        "",
        f"*Managed by `queue_zombie_watcher.py` from {run_url}.*",
    ])
    return "\n".join(lines) + "\n"


def _open_issue(token: str, repo: str, title: str, body: str) -> int | None:
    owner_login = _repo_owner(repo)
    resp = requests.post(
        f"{GH_API}/repos/{repo}/issues",
        headers=_gh_headers(token),
        json={
            "title": title,
            "body": body,
            "labels": [LABEL, AUTOMATED_LABEL, WORKSTREAM_LABEL],
            "assignees": [owner_login],
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        log.error("Failed to open zombie issue: %d %s", resp.status_code, resp.text[:200])
        return None
    return int(resp.json().get("number") or 0) or None


def _update_issue(token: str, repo: str, number: int, title: str, body: str) -> bool:
    resp = requests.patch(
        f"{GH_API}/repos/{repo}/issues/{number}",
        headers=_gh_headers(token),
        json={"title": title, "body": body},
        timeout=30,
    )
    if resp.status_code >= 300:
        log.warning("Update #%d failed: %d", number, resp.status_code)
        return False
    return True


def _ensure_owner_assigned(token: str, repo: str, number: int) -> None:
    owner_login = _repo_owner(repo)
    resp = requests.post(
        f"{GH_API}/repos/{repo}/issues/{number}/assignees",
        headers=_gh_headers(token),
        json={"assignees": [owner_login]},
        timeout=30,
    )
    if resp.status_code not in {200, 201}:
        log.warning("Assign owner on #%d failed: %d", number, resp.status_code)


def _close_issue(token: str, repo: str, number: int) -> bool:
    resp = requests.patch(
        f"{GH_API}/repos/{repo}/issues/{number}",
        headers=_gh_headers(token),
        json={"state": "closed", "state_reason": "completed"},
        timeout=30,
    )
    if resp.status_code >= 300:
        log.warning("Close #%d failed: %d", number, resp.status_code)
        return False
    return True


def run() -> int:
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY") or DASHBOARD_REPO
    _validate_target_repo(repo)
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}" if run_id else f"https://github.com/{repo}"

    jobs = _read_jobs()
    if not jobs:
        log.warning("No queue_jobs.json payload to evaluate; exiting")
        return 0
    if not _snapshot_is_fresh(jobs):
        log.error(
            "queue_jobs.json is invalid, stale, or future-dated; refusing issue mutations"
        )
        return 0

    jobs_ts = jobs.get("ts", "")
    grouped = _group_zombies(jobs)
    state = _read_state()
    open_map: dict[str, dict] = dict(state.get("open", {}))

    log.info("Evaluated zombie jobs: %d affected AMD queues", len(grouped))

    if not token:
        log.warning("GITHUB_TOKEN not set; skipping GitHub mutations")
        state["last_run"] = jobs_ts
        _write_state(state)
        return 0

    owned_issues = _list_owned_open_issues(
        token,
        repo,
        include_recovery=(not open_map or any(queue not in open_map for queue in grouped)),
        tracked_numbers=tuple(
            int(entry.get("number") or 0)
            for entry in open_map.values()
            if int(entry.get("number") or 0) > 0
        ),
    )
    if owned_issues is None:
        log.error(
            "Queue-zombie issue recovery lookup was incomplete; refusing issue mutations"
        )
        return 0
    remote_by_queue: dict[str, list[dict]] = {}
    for issue in owned_issues:
        remote_by_queue.setdefault(issue["queue"], []).append(issue)
    for issues in remote_by_queue.values():
        issues.sort(key=lambda issue: issue["number"])

    queues = sorted(set(grouped) | set(open_map) | set(remote_by_queue))
    for queue in queues:
        offenders = grouped.get(queue)
        entry = open_map.get(queue)
        tracked_number = int((entry or {}).get("number") or 0)
        remote_issues = remote_by_queue.get(queue, [])
        canonical = next(
            (
                issue
                for issue in remote_issues
                if issue["number"] == tracked_number
            ),
            remote_issues[0] if remote_issues else None,
        )

        if not offenders:
            failed_closes: list[dict] = []
            for issue in remote_issues:
                _ensure_owner_assigned(token, repo, issue["number"])
                if _close_issue(token, repo, issue["number"]):
                    log.info("Closed zombie issue #%d for %s", issue["number"], queue)
                else:
                    failed_closes.append(issue)
            if not failed_closes:
                open_map.pop(queue, None)
            elif entry and any(
                issue["number"] == tracked_number for issue in failed_closes
            ):
                open_map[queue] = entry
            # A failed close discovered without local state remains discoverable
            # by its marker on the next run. Do not claim it in the ledger until
            # a mutation has succeeded.
            continue

        title = _issue_title(queue, offenders)
        fingerprint = _fingerprint(queue, offenders, jobs_ts)
        if canonical is None:
            opened_ts = jobs_ts
            body = _issue_body(
                queue,
                offenders,
                opened_ts,
                jobs_ts,
                run_url,
                _repo_owner(repo),
            )
            number = _open_issue(token, repo, title, body)
            if number is None:
                continue
            open_map[queue] = {
                "number": number,
                "opened_ts": opened_ts,
                "last_fingerprint": fingerprint,
            }
            log.info("Opened zombie issue #%d for %s", number, queue)
            continue

        if not repair_issue_labels(
            token,
            repo,
            canonical,
            OWNED_LABELS,
            request_post=requests.post,
        ):
            log.warning(
                "Could not repair durable labels on zombie issue #%d",
                canonical["number"],
            )
            return 0

        for duplicate in remote_issues:
            if duplicate["number"] == canonical["number"]:
                continue
            _ensure_owner_assigned(token, repo, duplicate["number"])
            if _close_issue(token, repo, duplicate["number"]):
                log.info(
                    "Closed duplicate zombie issue #%d for %s; canonical is #%d",
                    duplicate["number"],
                    queue,
                    canonical["number"],
                )

        recovered = tracked_number != canonical["number"]
        opened_ts = (
            str((entry or {}).get("opened_ts") or "")
            if not recovered
            else ""
        ) or canonical.get("created_at") or jobs_ts
        body = _issue_body(
            queue,
            offenders,
            opened_ts,
            jobs_ts,
            run_url,
            _repo_owner(repo),
        )
        needs_update = (
            recovered
            or canonical.get("legacy")
            or (entry or {}).get("last_fingerprint") != fingerprint
        )
        _ensure_owner_assigned(token, repo, canonical["number"])
        if needs_update and not _update_issue(
            token,
            repo,
            canonical["number"],
            title,
            body,
        ):
            continue
        if needs_update:
            log.info("Updated zombie issue #%d for %s", canonical["number"], queue)
        open_map[queue] = {
            "number": canonical["number"],
            "opened_ts": opened_ts,
            "last_fingerprint": fingerprint,
        }

    state["open"] = open_map
    state["last_run"] = jobs_ts
    _write_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
