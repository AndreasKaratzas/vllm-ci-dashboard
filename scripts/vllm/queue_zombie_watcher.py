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
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.constants import (  # noqa: E402
    AMD_QUEUE_PREFIX,
    QUEUE_ZOMBIE_THRESHOLD_MIN,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
JOBS = ROOT / "data" / "vllm" / "ci" / "queue_jobs.json"
STATE = ROOT / "data" / "vllm" / "ci" / "open_queue_zombie_issues.json"
LABEL = "queue-zombie"
GH_API = "https://api.github.com"


def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo_owner(repo: str) -> str:
    return (repo.split("/", 1)[0] if "/" in repo else repo or "AndreasKaratzas").strip() or "AndreasKaratzas"


def _read_jobs() -> dict | None:
    if not JOBS.exists():
        return None
    try:
        return json.loads(JOBS.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _normalize_entry(entry: int | dict) -> dict:
    if isinstance(entry, dict):
        entry.setdefault("number", 0)
        entry.setdefault("opened_ts", "")
        entry.setdefault("last_fingerprint", "")
        return entry
    return {
        "number": int(entry),
        "opened_ts": "",
        "last_fingerprint": "",
    }


def _read_state() -> dict:
    if not STATE.exists():
        return {"open": {}, "last_run": ""}
    try:
        data = json.loads(STATE.read_text())
        data["open"] = {queue: _normalize_entry(entry) for queue, entry in (data.get("open") or {}).items()}
        data.setdefault("last_run", "")
        return data
    except (OSError, json.JSONDecodeError):
        return {"open": {}, "last_run": ""}


def _write_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True))


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
        f"cc @{owner_login} for visibility.",
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
            "labels": [LABEL, "automated"],
            "assignees": [owner_login],
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        log.error("Failed to open zombie issue: %d %s", resp.status_code, resp.text[:200])
        return None
    return int(resp.json().get("number") or 0) or None


def _update_issue(token: str, repo: str, number: int, title: str, body: str) -> None:
    resp = requests.patch(
        f"{GH_API}/repos/{repo}/issues/{number}",
        headers=_gh_headers(token),
        json={"title": title, "body": body, "state": "open"},
        timeout=30,
    )
    if resp.status_code >= 300:
        log.warning("Update #%d failed: %d", number, resp.status_code)


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


def _close_issue(token: str, repo: str, number: int) -> None:
    resp = requests.patch(
        f"{GH_API}/repos/{repo}/issues/{number}",
        headers=_gh_headers(token),
        json={"state": "closed", "state_reason": "completed"},
        timeout=30,
    )
    if resp.status_code >= 300:
        log.warning("Close #%d failed: %d", number, resp.status_code)


def run() -> int:
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY") or "AndreasKaratzas/vllm-ci-dashboard"
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}" if run_id else f"https://github.com/{repo}"

    jobs = _read_jobs()
    if not jobs:
        log.warning("No queue_jobs.json payload to evaluate; exiting")
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

    for queue, offenders in grouped.items():
        entry = open_map.get(queue)
        opened_ts = (entry or {}).get("opened_ts") or jobs_ts
        title = _issue_title(queue, offenders)
        body = _issue_body(queue, offenders, opened_ts, jobs_ts, run_url, _repo_owner(repo))
        fingerprint = _fingerprint(queue, offenders, jobs_ts)

        if entry:
            _ensure_owner_assigned(token, repo, entry["number"])
            if entry.get("last_fingerprint") != fingerprint:
                _update_issue(token, repo, entry["number"], title, body)
                log.info("Updated zombie issue #%d for %s", entry["number"], queue)
            entry["last_fingerprint"] = fingerprint
            open_map[queue] = entry
            continue

        number = _open_issue(token, repo, title, body)
        if number is None:
            continue
        open_map[queue] = {
            "number": number,
            "opened_ts": opened_ts,
            "last_fingerprint": fingerprint,
        }
        log.info("Opened zombie issue #%d for %s", number, queue)

    for queue, entry in list(open_map.items()):
        if queue in grouped:
            continue
        _ensure_owner_assigned(token, repo, entry["number"])
        _close_issue(token, repo, entry["number"])
        del open_map[queue]
        log.info("Closed zombie issue #%d for %s", entry["number"], queue)

    state["open"] = open_map
    state["last_run"] = jobs_ts
    _write_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
