#!/usr/bin/env python3
"""Queue latency issue watcher.

Reads the latest snapshot in ``data/vllm/ci/queue_timeseries.jsonl`` and
opens a GitHub issue when a queue's p90 wait exceeds ``P90_THRESHOLD_MIN``.
Closes the tracked issue when the queue is healthy again (p90 below
``P90_HEALTHY_MIN``) to provide hysteresis — without that margin a queue
flapping around the threshold would churn issue open/close events.

State is stored in ``data/vllm/ci/open_queue_issues.json`` so we remember
which issue number covers which queue across runs. The tracking file is the
source of truth; we never rely on GitHub label queries alone.

Requires:
  - ``GITHUB_TOKEN`` with ``issues: write`` on the target repo
  - ``GITHUB_REPOSITORY`` ("owner/repo"), set automatically in GH Actions
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.constants import (  # noqa: E402
    AMD_QUEUE_PREFIX,
    QUEUE_ISSUE_MAX_AGE_MIN,
    QUEUE_MIN_WAITING_SAMPLES,
    QUEUE_P90_HEALTHY_MIN,
    QUEUE_P90_TRIGGER_MIN,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
SNAPSHOTS = ROOT / "data" / "vllm" / "ci" / "queue_timeseries.jsonl"
STATE = ROOT / "data" / "vllm" / "ci" / "open_queue_issues.json"

# Back-compat local aliases — the rest of the module reads these names.
P90_THRESHOLD_MIN = QUEUE_P90_TRIGGER_MIN
P90_HEALTHY_MIN = QUEUE_P90_HEALTHY_MIN
MIN_WAITING_SAMPLES = QUEUE_MIN_WAITING_SAMPLES
LABEL = "queue-latency"

GH_API = "https://api.github.com"


def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo_owner(repo: str) -> str:
    return (repo.split("/", 1)[0] if "/" in repo else repo or "AndreasKaratzas").strip() or "AndreasKaratzas"


def _read_last_snapshot() -> dict | None:
    if not SNAPSHOTS.exists():
        return None
    last = None
    with SNAPSHOTS.open() as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    return last


def _normalize_open_entry(entry: int | dict, fallback_p90: float = 0.0, fallback_ts: str = "") -> dict:
    """State file historically stored ``{queue: issue_number}`` (a bare int).
    Newer runs persist a dict carrying the issue number plus the peak p90 we've
    seen and the timestamp of our last status comment, so hourly updates don't
    lose context across runs. Accept either shape on read.
    """
    if isinstance(entry, dict):
        entry.setdefault("number", 0)
        entry.setdefault("peak_p90", fallback_p90)
        entry.setdefault("opened_ts", fallback_ts)
        entry.setdefault("last_status_ts", "")
        return entry
    # legacy int
    return {
        "number": int(entry),
        "peak_p90": fallback_p90,
        "opened_ts": fallback_ts,
        "last_status_ts": "",
    }


def _normalize_suppressed_entry(entry: str | dict, fallback_ts: str = "") -> dict:
    if isinstance(entry, dict):
        entry.setdefault("closed_ts", fallback_ts)
        entry.setdefault("last_number", 0)
        return entry
    return {
        "closed_ts": str(entry or fallback_ts or ""),
        "last_number": 0,
    }


def _read_state() -> dict:
    if not STATE.exists():
        return {"open": {}, "suppressed": {}}
    try:
        data = json.loads(STATE.read_text())
        open_raw = data.get("open") or {}
        data["open"] = {q: _normalize_open_entry(v) for q, v in open_raw.items()}
        suppressed_raw = data.get("suppressed") or {}
        data["suppressed"] = {q: _normalize_suppressed_entry(v) for q, v in suppressed_raw.items()}
        return data
    except (json.JSONDecodeError, OSError):
        return {"open": {}, "suppressed": {}}


def _write_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True))


def _format_metric_row(label: str, value: str | int) -> str:
    return f"| {label} | {value} |\n"


def _format_wait(value: float | int | None) -> str:
    return f"{float(value or 0):.1f}m"


def _open_issue_body(queue: str, stats: dict, run_url: str, owner_login: str) -> str:
    return (
        f"## Queue latency alert\n\n"
        f"Queue **`{queue}`** exceeded p90 wait of {P90_THRESHOLD_MIN:.0f}m in the latest snapshot.\n\n"
        f"| metric | value |\n|---|---|\n"
        f"{_format_metric_row('waiting jobs', int(stats.get('waiting') or 0))}"
        f"{_format_metric_row('running jobs', int(stats.get('running') or 0))}"
        f"{_format_metric_row('p50 wait', _format_wait(stats.get('p50_wait')))}"
        f"{_format_metric_row('p90 wait', _format_wait(stats.get('p90_wait')))}"
        f"{_format_metric_row('p99 wait', _format_wait(stats.get('p99_wait')))}\n"
        f"This issue will auto-close once p90 drops below {P90_HEALTHY_MIN:.0f}m, "
        f"or after 24h if the queue stays elevated.\n\n"
        f"cc @{owner_login} for visibility.\n\n"
        f"*Opened by `queue_issue_watcher.py` from {run_url}.*\n"
    )


def _open_issue(token: str, repo: str, queue: str, stats: dict, run_url: str) -> int | None:
    title = f"Queue {queue}: p90 wait {stats.get('p90_wait', 0):.1f}m"
    owner_login = _repo_owner(repo)
    body = _open_issue_body(queue, stats, run_url, owner_login)
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
        log.error("Failed to open issue for %s: %d %s", queue, resp.status_code, resp.text[:200])
        return None
    return resp.json().get("number")


def _status_update_body(queue: str, stats: dict, peak_p90: float, opened_ts: str, snapshot_ts: str, run_url: str) -> str:
    """Body for the periodic 'still elevated' comment posted while a queue
    remains above trigger. Shows current metrics plus peak-since-open so the
    reader can tell at a glance whether latency is worsening or easing."""
    p90 = float(stats.get("p90_wait") or 0)
    trend = "peaking" if p90 >= peak_p90 - 0.1 else "easing" if p90 < peak_p90 - 5 else "holding"
    return (
        f"### Still elevated ({trend})\n\n"
        f"Snapshot `{snapshot_ts or 'latest'}` \u2014 queue `{queue}` is still above "
        f"the {P90_THRESHOLD_MIN:.0f}m trigger.\n\n"
        f"| metric | value |\n|---|---|\n"
        f"{_format_metric_row('waiting jobs', int(stats.get('waiting') or 0))}"
        f"{_format_metric_row('running jobs', int(stats.get('running') or 0))}"
        f"{_format_metric_row('p50 wait', _format_wait(stats.get('p50_wait')))}"
        f"{_format_metric_row('p90 wait (current)', _format_wait(p90))}"
        f"{_format_metric_row('p90 wait (peak since open)', _format_wait(peak_p90))}"
        f"{_format_metric_row('p99 wait', _format_wait(stats.get('p99_wait')))}\n"
        f"Issue opened at `{opened_ts or 'unknown'}`. Will auto-close once p90 "
        f"drops below {P90_HEALTHY_MIN:.0f}m, or after 24h if the queue stays "
        f"elevated.\n\n"
        f"*Update posted by `queue_issue_watcher.py` from {run_url}.*\n"
    )


def _comment_issue(token: str, repo: str, number: int, body: str) -> None:
    resp = requests.post(
        f"{GH_API}/repos/{repo}/issues/{number}/comments",
        headers=_gh_headers(token),
        json={"body": body},
        timeout=30,
    )
    if resp.status_code >= 300:
        log.warning("Comment on #%d failed: %d", number, resp.status_code)


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


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _issue_age_minutes(opened_ts: str, snapshot_ts: str) -> float:
    opened = _parse_ts(opened_ts)
    snap = _parse_ts(snapshot_ts)
    if not opened or not snap:
        return 0.0
    return max(0.0, (snap - opened).total_seconds() / 60.0)


def run() -> int:
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY") or "AndreasKaratzas/vllm-ci-dashboard"
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}" if run_id else f"https://github.com/{repo}"

    snapshot = _read_last_snapshot()
    if not snapshot:
        log.warning("No snapshot to evaluate; exiting")
        return 0

    state = _read_state()
    open_map: dict[str, dict] = dict(state.get("open", {}))
    suppressed_map: dict[str, dict] = dict(state.get("suppressed", {}))
    queues = snapshot.get("queues", {}) or {}
    snapshot_ts = snapshot.get("ts", "")

    # Only AMD queues are in scope — NVIDIA/CPU/partner queues are noisy
    # and out of this dashboard's responsibility.
    hot = []
    for q, s in queues.items():
        if not q.startswith(AMD_QUEUE_PREFIX):
            continue
        p90 = float(s.get("p90_wait") or 0)
        waiting = int(s.get("waiting") or 0)
        if p90 >= P90_THRESHOLD_MIN and waiting >= MIN_WAITING_SAMPLES:
            hot.append((q, s, p90))

    healthy = []
    for q, entry in open_map.items():
        # Auto-close any stale non-AMD issues that predate the AMD-only filter.
        s = queues.get(q) or {}
        p90 = float(s.get("p90_wait") or 0)
        if not q.startswith(AMD_QUEUE_PREFIX) or p90 <= P90_HEALTHY_MIN:
            healthy.append((q, entry["number"], p90, s))

    log.info("Evaluated %d queues: %d hot, %d healthy-with-open-issue", len(queues), len(hot), len(healthy))

    if not token:
        log.warning("GITHUB_TOKEN not set; skipping GitHub mutations")
        _write_state(state)
        return 0

    for q, s, p90 in hot:
        if q in suppressed_map:
            log.info("Suppressed queue alert for %s while it remains unhealthy", q)
            continue
        if q in open_map:
            # Already tracked — post an hourly status update so the issue
            # reflects current latency instead of freezing at the metrics
            # captured when the issue was first opened. Track peak p90 so the
            # comment can call out whether latency is worsening or easing.
            entry = open_map[q]
            number = entry["number"]
            _ensure_owner_assigned(token, repo, number)
            age_min = _issue_age_minutes(entry.get("opened_ts") or "", snapshot_ts)
            if age_min >= QUEUE_ISSUE_MAX_AGE_MIN:
                _comment_issue(
                    token,
                    repo,
                    number,
                    "Closing this queue-latency issue after 24h to keep the operational "
                    "backlog bounded. The queue is still elevated, but no new queue-latency "
                    "issue will open until it first returns to healthy.\n\n"
                    f"*{run_url}*",
                )
                _close_issue(token, repo, number)
                suppressed_map[q] = {
                    "closed_ts": snapshot_ts,
                    "last_number": number,
                }
                open_map.pop(q, None)
                log.info("Closed stale queue-latency issue #%d for %s after %.1fm", number, q, age_min)
                continue
            peak = max(float(entry.get("peak_p90") or 0), p90)
            entry["peak_p90"] = peak
            opened_ts = entry.get("opened_ts") or ""
            body = _status_update_body(q, s, peak, opened_ts, snapshot_ts, run_url)
            _comment_issue(token, repo, number, body)
            entry["last_status_ts"] = snapshot_ts
            log.info("Posted status update to #%d (%s p90=%.1f peak=%.1f)", number, q, p90, peak)
            continue
        number = _open_issue(token, repo, q, s, run_url)
        if number is not None:
            open_map[q] = {
                "number": number,
                "peak_p90": p90,
                "opened_ts": snapshot_ts,
                "last_status_ts": "",
            }
            log.info("Opened issue #%d for %s", number, q)

    for q, number, p90, s in healthy:
        _ensure_owner_assigned(token, repo, number)
        _comment_issue(token, repo, number,
                       f"Queue healthy again: p90={p90:.1f}m (threshold {P90_HEALTHY_MIN:.0f}m). Closing.\n\n*{run_url}*")
        _close_issue(token, repo, number)
        open_map.pop(q, None)
        log.info("Closed issue #%d for %s", number, q)

    for q in list(suppressed_map.keys()):
        s = queues.get(q) or {}
        p90 = float(s.get("p90_wait") or 0)
        if not q.startswith(AMD_QUEUE_PREFIX) or p90 <= P90_HEALTHY_MIN:
            suppressed_map.pop(q, None)
            log.info("Cleared suppression for %s after the queue returned healthy", q)

    state["open"] = open_map
    state["suppressed"] = suppressed_map
    state["last_run"] = snapshot_ts
    _write_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(run())
