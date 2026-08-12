#!/usr/bin/env python3
"""Omni workload surge watcher.

Counts how many Omni-classified jobs are currently waiting across AMD queues
and opens a GitHub issue in this repo when the total exceeds a dynamically
derived threshold. Auto-closes with hysteresis when the queue drains.

The trigger is computed by counting test groups in the ``vllm-project/vllm-omni``
Buildkite YAMLs:

    trigger = max(OMNI_SURGE_FLOOR_TRIGGER, ceil(multiplier * total_groups))
    healthy = floor(trigger * healthy_ratio)

If the YAML sources cannot all be read, the dashboard retains the last
known-good heuristic (or exposes the static floor when none exists) and GitHub
mutations are suppressed for that run. A moved or temporarily unavailable
source must never silently lower the alert threshold or close an active issue.

State lives at ``data/vllm/ci/open_omni_surge_issues.json`` so the watcher
remembers which issue tracks the current surge across runs.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.constants import (  # noqa: E402
    AMD_QUEUE_PREFIX,
    OMNI_REPO,
    OMNI_SURGE_FLOOR_TRIGGER,
    OMNI_SURGE_HEALTHY_RATIO,
    OMNI_SURGE_MULTIPLIER,
    OMNI_YAML_PATHS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
SNAPSHOTS = ROOT / "data" / "vllm" / "ci" / "queue_timeseries.jsonl"
STATE = ROOT / "data" / "vllm" / "ci" / "open_omni_surge_issues.json"
HEURISTIC_PATH = ROOT / "data" / "vllm" / "ci" / "omni_surge_heuristic.json"

GH_API = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"
LABEL = "omni-surge"
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


def _repo_owner(repo: str) -> str:
    return (repo.split("/", 1)[0] if "/" in repo else repo or "AndreasKaratzas").strip() or "AndreasKaratzas"


def _read_last_snapshot() -> dict | None:
    if not SNAPSHOTS.exists():
        return None
    last = None
    with SNAPSHOTS.open() as fh:
        for line in fh:
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    return last


def _read_state() -> dict:
    if not STATE.exists():
        return {"open": None, "last_value": 0}
    try:
        data = json.loads(STATE.read_text())
        data.setdefault("open", None)
        data.setdefault("last_value", 0)
        return data
    except (json.JSONDecodeError, OSError):
        return {"open": None, "last_value": 0}


def _write_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True))


def _fetch_yaml(path: str) -> str | None:
    url = f"{RAW_BASE}/{OMNI_REPO}/main/{path}"
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            return r.text
        log.info("YAML fetch %s → %s", path, r.status_code)
    except Exception as e:
        log.warning("YAML fetch failed for %s: %s", path, e)
    return None


def _parse_test_groups(yaml_text: str) -> list[dict]:
    """Yield every entry in the YAML that carries a ``label``.

    The omni buildkite YAMLs are pipeline files — top-level is either a list of
    steps or ``{steps: [...]}``. Each test group is a dict with a ``label`` key.
    We also flatten nested ``group:`` blocks so counting stays honest when the
    omni team reorganizes.
    """
    try:
        data = yaml.safe_load(yaml_text) or []
    except yaml.YAMLError as e:
        log.warning("YAML parse failed: %s", e)
        return []

    if isinstance(data, dict):
        steps = data.get("steps") or data.get("tests") or []
    else:
        steps = data

    groups: list[dict] = []

    def walk(items):
        for it in items or []:
            if not isinstance(it, dict):
                continue
            if isinstance(it.get("group"), str) and isinstance(it.get("steps"), list):
                walk(it["steps"])
                continue
            if "label" in it:
                groups.append(it)

    walk(steps)
    return groups


def _compute_trigger(groups: list[dict]) -> tuple[int, int, dict]:
    total = len(groups)
    dynamic = math.ceil(total * OMNI_SURGE_MULTIPLIER)
    trigger = max(OMNI_SURGE_FLOOR_TRIGGER, dynamic)
    healthy = math.floor(trigger * OMNI_SURGE_HEALTHY_RATIO)

    pool_counts: dict[str, int] = {}
    for g in groups:
        pool = g.get("agent_pool") or g.get("agents", {}).get("queue") or "unknown"
        if not isinstance(pool, str):
            pool = "unknown"
        pool_counts[pool] = pool_counts.get(pool, 0) + 1

    info = {
        "total_groups": total,
        "dynamic_component": dynamic,
        "trigger": trigger,
        "healthy": healthy,
        "pool_distribution": pool_counts,
    }
    return trigger, healthy, info


def _read_last_good_heuristic() -> dict | None:
    """Return the most recent usable dynamic heuristic, if one exists.

    ``HEURISTIC_PATH`` is also the dashboard-facing status snapshot. Failed
    runs keep the last valid counts in that file and add failure metadata, so
    accept both a freshly fetched value and a previously retained one.
    """
    try:
        data = json.loads(HEURISTIC_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    try:
        total = int(data.get("total_groups") or 0)
        trigger = int(data.get("trigger") or 0)
        healthy = int(data.get("healthy") or 0)
    except (TypeError, ValueError):
        return None
    if (
        total <= 0
        or trigger < OMNI_SURGE_FLOOR_TRIGGER
        or healthy < 0
        or healthy > trigger
    ):
        return None
    if data.get("fallback_floor_used"):
        return None
    if (
        data.get("source_status") in {"partial", "unavailable"}
        and not data.get("using_last_known_good")
    ):
        return None
    return data


def _derive_heuristic(
    groups: list[dict],
    fetched_paths: list[str],
    last_good: dict | None,
) -> tuple[int, int, dict]:
    """Build a status-rich heuristic and indicate whether mutation is safe."""
    configured_paths = list(OMNI_YAML_PATHS)
    missing_paths = [path for path in configured_paths if path not in fetched_paths]
    sources_complete = not missing_paths and bool(configured_paths)

    if sources_complete:
        trigger, healthy, info = _compute_trigger(groups)
        info.update({
            "yaml_paths_configured": configured_paths,
            "yaml_paths_fetched": fetched_paths,
            "yaml_paths_failed": [],
            "source_status": "fresh",
            "fallback_floor_used": False,
            "using_last_known_good": False,
            "mutations_suppressed": False,
        })
        return trigger, healthy, info

    if last_good:
        info = dict(last_good)
        trigger = int(info["trigger"])
        healthy = int(info["healthy"])
        info.update({
            "yaml_paths_configured": configured_paths,
            "last_successful_yaml_paths": (
                last_good.get("last_successful_yaml_paths")
                or last_good.get("yaml_paths_fetched")
                or []
            ),
            "yaml_paths_fetched": fetched_paths,
            "yaml_paths_failed": missing_paths,
            "source_status": "last_known_good",
            "source_error": (
                "Omni YAML discovery was incomplete; retained the last "
                "known-good dynamic heuristic."
            ),
            "fallback_floor_used": False,
            "using_last_known_good": True,
            "mutations_suppressed": True,
        })
        return trigger, healthy, info

    trigger, healthy, info = _compute_trigger(groups)
    info.update({
        "yaml_paths_configured": configured_paths,
        "yaml_paths_fetched": fetched_paths,
        "yaml_paths_failed": missing_paths,
        "source_status": "partial" if fetched_paths else "unavailable",
        "source_error": (
            "Omni YAML discovery was incomplete and no last known-good "
            "dynamic heuristic was available."
        ),
        "fallback_floor_used": not fetched_paths,
        "using_last_known_good": False,
        "mutations_suppressed": True,
    })
    return trigger, healthy, info


def _refresh_heuristic() -> tuple[int, int, dict]:
    """Refresh and persist the heuristic without touching issue state.

    This is deliberately independent from the queue snapshot and issue
    automation so publication validation can refresh the heuristic before it
    decides whether the queue surface is usable.
    """
    all_groups: list[dict] = []
    fetched_paths: list[str] = []
    for path in OMNI_YAML_PATHS:
        text = _fetch_yaml(path)
        if not text:
            continue
        groups = _parse_test_groups(text)
        if not groups:
            log.warning("YAML %s contained no discoverable test groups", path)
            continue
        fetched_paths.append(path)
        all_groups.extend(groups)

    trigger, healthy, info = _derive_heuristic(
        all_groups,
        fetched_paths,
        _read_last_good_heuristic(),
    )
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    info["generated_at"] = checked_at
    if info["source_status"] == "fresh":
        info["last_successful_at"] = checked_at
    else:
        info.setdefault("last_successful_at", None)
    HEURISTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEURISTIC_PATH.write_text(json.dumps(info, indent=2, sort_keys=True))
    return trigger, healthy, info


def _heuristic_is_usable(info: dict) -> bool:
    """Return whether a refreshed payload is current publication evidence.

    A retained heuristic remains useful to suppress unsafe alert mutations,
    but it must not make a collection run look current.  Returning nonzero for
    every incomplete source refresh lets the publication selector quarantine
    the whole queue transaction under its bounded fallback policy.
    """
    try:
        total = int(info.get("total_groups") or 0)
        trigger = int(info.get("trigger") or 0)
        healthy = int(info.get("healthy") or 0)
    except (TypeError, ValueError):
        return False

    if (
        total <= 0
        or trigger < OMNI_SURGE_FLOOR_TRIGGER
        or healthy < 0
        or healthy > trigger
        or info.get("fallback_floor_used")
    ):
        return False

    for field in ("generated_at", "last_successful_at"):
        value = info.get(field)
        if not isinstance(value, str) or not value.strip():
            return False
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None:
            return False

    configured = info.get("yaml_paths_configured")
    fetched = info.get("yaml_paths_fetched")
    return (
        info.get("source_status") == "fresh"
        and not info.get("using_last_known_good")
        and not info.get("mutations_suppressed")
        and isinstance(configured, list)
        and bool(configured)
        and fetched == configured
        and info.get("yaml_paths_failed") == []
    )


def _current_omni_waiting(snapshot: dict) -> tuple[int, dict]:
    queues = snapshot.get("queues") or {}
    total = 0
    by_queue: dict[str, int] = {}
    for q, stats in queues.items():
        if not q.startswith(AMD_QUEUE_PREFIX):
            continue
        wbw = stats.get("waiting_by_workload") or {}
        omni_waiting = int(wbw.get("omni") or 0)
        if omni_waiting:
            by_queue[q] = omni_waiting
            total += omni_waiting
    return total, by_queue


def _open_issue(
    token: str,
    repo: str,
    waiting: int,
    by_queue: dict[str, int],
    heuristic: dict,
    snap_ts: str,
    run_url: str,
) -> int | None:
    owner_login = _repo_owner(repo)
    title = f"Omni CI surge: {waiting} jobs waiting (threshold {heuristic['trigger']})"
    rows = "\n".join(f"| `{q}` | {n} |" for q, n in sorted(by_queue.items(), key=lambda kv: -kv[1])) or "| — | 0 |"
    pools = "\n".join(f"- `{p}`: {n}" for p, n in sorted(heuristic["pool_distribution"].items()))
    body = (
        f"## Omni workload surge\n\n"
        f"**{waiting}** Omni-classified jobs are waiting across AMD queues as of `{snap_ts}` — "
        f"at or above the dynamic trigger of **{heuristic['trigger']}** "
        f"(derived from {heuristic['total_groups']} test groups × {OMNI_SURGE_MULTIPLIER} "
        f"multiplier, floor {OMNI_SURGE_FLOOR_TRIGGER}).\n\n"
        f"### Per-queue breakdown\n\n"
        f"| queue | omni waiting |\n|---|---|\n{rows}\n\n"
        f"### Heuristic context\n\n"
        f"- total groups counted across omni YAMLs: **{heuristic['total_groups']}**\n"
        f"- dynamic component (`ceil(groups × {OMNI_SURGE_MULTIPLIER})`): {heuristic['dynamic_component']}\n"
        f"- healthy threshold (close at or below): **{heuristic['healthy']}**\n\n"
        f"<details><summary>Per-pool distribution from omni YAMLs</summary>\n\n{pools}\n</details>\n\n"
        f"GitHub assignee: {owner_login}.\n\n"
        f"Auto-opened by `omni_surge_watcher.py` from {run_url}. Will auto-close once the "
        f"waiting count drops to {heuristic['healthy']}.\n"
    )
    resp = requests.post(
        f"{GH_API}/repos/{repo}/issues",
        headers=_gh_headers(token),
        json={
            "title": title,
            "body": body,
            "labels": [LABEL, "automated", "workstream:infra"],
            "assignees": [owner_login],
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        log.error("Failed to open surge issue: %d %s", resp.status_code, resp.text[:200])
        return None
    return resp.json().get("number")


def _comment(token: str, repo: str, number: int, body: str) -> None:
    resp = requests.post(
        f"{GH_API}/repos/{repo}/issues/{number}/comments",
        headers=_gh_headers(token), json={"body": body}, timeout=30,
    )
    if resp.status_code >= 300:
        log.warning("Comment on #%d failed: %d", number, resp.status_code)


def _ensure_owner_assigned(token: str, repo: str, number: int) -> None:
    owner_login = _repo_owner(repo)
    resp = requests.post(
        f"{GH_API}/repos/{repo}/issues/{number}/assignees",
        headers=_gh_headers(token), json={"assignees": [owner_login]}, timeout=30,
    )
    if resp.status_code not in {200, 201}:
        log.warning("Assign owner on #%d failed: %d", number, resp.status_code)


def _close(token: str, repo: str, number: int) -> None:
    resp = requests.patch(
        f"{GH_API}/repos/{repo}/issues/{number}",
        headers=_gh_headers(token),
        json={"state": "closed", "state_reason": "completed"},
        timeout=30,
    )
    if resp.status_code >= 300:
        log.warning("Close #%d failed: %d", number, resp.status_code)


def run(heuristic_only: bool = False, issues_only: bool = False) -> int:
    if heuristic_only and issues_only:
        raise ValueError("heuristic-only and issues-only modes are mutually exclusive")
    if heuristic_only:
        _, _, info = _refresh_heuristic()
        if not _heuristic_is_usable(info):
            log.error(
                "Omni heuristic refresh produced no auditable usable result "
                "(source status: %s)",
                info.get("source_status") or "unknown",
            )
            return 1
        log.info(
            "Refreshed Omni heuristic only (status=%s, groups=%d, trigger=%d)",
            info["source_status"],
            info["total_groups"],
            info["trigger"],
        )
        return 0

    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY") or DASHBOARD_REPO
    _validate_target_repo(repo)
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}" if run_id else f"https://github.com/{repo}"

    snapshot = _read_last_snapshot()
    if not snapshot:
        log.warning("No snapshot available; skipping")
        return 0

    # The hourly workflow refreshes and validates the heuristic before its
    # publication boundary, then invokes issues-only mode afterward. Keeping
    # this phase read-only with respect to the heuristic prevents issue
    # automation from changing a source file after publication selection.
    if issues_only:
        info = _read_last_good_heuristic()
        if not info or not _heuristic_is_usable(info):
            log.error("Selected Omni heuristic is not current and usable")
            return 1
        trigger = int(info["trigger"])
        healthy = int(info["healthy"])
    else:
        # Backward-compatible standalone mode refreshes the heuristic and then
        # performs issue automation in one invocation.
        trigger, healthy, info = _refresh_heuristic()
    fetched_paths = info["yaml_paths_fetched"]

    waiting, by_queue = _current_omni_waiting(snapshot)
    log.info(
        "Omni waiting=%d (trigger=%d, healthy=%d, groups=%d, fetched=%d/%d yamls)",
        waiting, trigger, healthy, info["total_groups"],
        len(fetched_paths), len(OMNI_YAML_PATHS),
    )

    state = _read_state()
    open_issue: int | None = state.get("open")
    state["last_value"] = waiting
    state["last_trigger"] = trigger
    state["last_healthy"] = healthy
    state["last_snapshot_ts"] = snapshot.get("ts", "")

    if not token:
        log.warning("GITHUB_TOKEN not set; skipping GitHub mutations")
        _write_state(state)
        return 0

    if info["mutations_suppressed"]:
        log.error(
            "Omni YAML discovery status=%s; suppressing GitHub mutations "
            "(failed paths: %s)",
            info["source_status"],
            ", ".join(info["yaml_paths_failed"]) or "none",
        )
        _write_state(state)
        return 0

    if open_issue is not None:
        _ensure_owner_assigned(token, repo, open_issue)

    if waiting >= trigger and open_issue is None:
        number = _open_issue(token, repo, waiting, by_queue, info,
                             snapshot.get("ts", ""), run_url)
        if number is not None:
            state["open"] = number
            log.info("Opened omni surge issue #%d", number)
    elif waiting <= healthy and open_issue is not None:
        _comment(token, repo, open_issue,
                 f"Omni queue drained: {waiting} waiting (healthy ≤ {healthy}). Closing.\n\n*{run_url}*")
        _close(token, repo, open_issue)
        log.info("Closed omni surge issue #%d", open_issue)
        state["open"] = None

    _write_state(state)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--heuristic-only",
        action="store_true",
        help="refresh only omni_surge_heuristic.json without issue automation",
    )
    mode.add_argument(
        "--issues-only",
        action="store_true",
        help="apply issue automation using the already validated heuristic",
    )
    args = parser.parse_args(argv)
    return run(heuristic_only=args.heuristic_only, issues_only=args.issues_only)


if __name__ == "__main__":
    sys.exit(main())
