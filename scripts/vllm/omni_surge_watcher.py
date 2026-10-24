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
from vllm.bounded_json import pretty_json_bytes, write_pretty_json_lkg  # noqa: E402
from vllm.dashboard_storage_budget import writer_max_bytes  # noqa: E402
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
SNAPSHOTS = ROOT / "data" / "vllm" / "ci" / "queue_timeseries.jsonl"
STATE = ROOT / "data" / "vllm" / "ci" / "open_omni_surge_issues.json"
HEURISTIC_PATH = ROOT / "data" / "vllm" / "ci" / "omni_surge_heuristic.json"
OMNI_HEURISTIC_MAX_BYTES = writer_max_bytes("omni_surge_heuristic")

GH_API = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"
LABEL = "omni-surge"
AUTOMATED_LABEL = "automated"
WORKSTREAM_LABEL = "workstream:infra"
OWNERSHIP_MARKER = "<!-- vllm-ci-dashboard:managed-alert:omni-surge:v1 -->"
OWNED_LABELS = frozenset({LABEL, AUTOMATED_LABEL, WORKSTREAM_LABEL})
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


def _issue_label_names(issue: dict) -> set[str]:
    names: set[str] = set()
    for label in issue.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else label
        if isinstance(name, str) and name:
            names.add(name)
    return names


def _has_exact_ownership_marker(body: str) -> bool:
    """Require the marker as its own HTML-comment line.

    A substring match would let quoted issue text or a copied diagnostic claim
    ownership. Once an issue carries this exact marker, the marker remains the
    durable authority even if a human later edits its title or labels.
    """
    return OWNERSHIP_MARKER in {
        line.strip() for line in str(body or "").splitlines()
    }


def _legacy_owned_issue(issue: dict) -> bool:
    """Recognize only the complete pre-marker issue shape emitted here.

    Legacy adoption is intentionally stricter than marker ownership: all three
    managed labels, the exact generated title grammar, matching values in the
    body, and the generator signature must agree. A label alone never grants
    this watcher authority over an issue.
    """
    if not OWNED_LABELS.issubset(_issue_label_names(issue)):
        return False
    title = str(issue.get("title") or "")
    match = re.fullmatch(
        r"Omni CI surge: ([0-9]+) jobs waiting \(threshold ([0-9]+)\)",
        title,
    )
    if not match:
        return False
    waiting, trigger = match.groups()
    body = str(issue.get("body") or "")
    required_body_fragments = (
        "## Omni workload surge",
        f"**{waiting}** Omni-classified jobs are waiting across AMD queues",
        f"dynamic trigger of **{trigger}**",
        "GitHub assignee: ",
        "Auto-opened by `omni_surge_watcher.py` from ",
        "Will auto-close once the waiting count drops to ",
    )
    return all(fragment in body for fragment in required_body_fragments)


def _owned_open_issue(issue: object) -> dict | None:
    """Normalize a provably watcher-owned open issue."""
    if not isinstance(issue, dict) or "pull_request" in issue:
        return None
    number = issue.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        return None
    body = str(issue.get("body") or "")
    marker_owned = _has_exact_ownership_marker(body)
    if not marker_owned and not _legacy_owned_issue(issue):
        return None
    return {
        "number": number,
        "body": body,
        "created_at": str(issue.get("created_at") or ""),
        "labels": issue.get("labels") or [],
        "legacy": not marker_owned,
    }


def _list_owned_open_issues(
    token: str,
    repo: str,
    *,
    include_recovery: bool = True,
    tracked_numbers: tuple[int, ...] = (),
) -> list[dict] | None:
    """Return bounded owned open issues, or ``None`` on ambiguity.

    A watcher label is the primary index and a shared-label recent page repairs
    label edits. Exact ownership markers remain the mutation authority.
    """
    owned_by_number: dict[int, dict] = {}
    tracked = sorted(set(tracked_numbers))
    if len(tracked) > MAX_DIRECT_ISSUE_LOOKUPS:
        log.error(
            "Omni recovery has %d tracked numbers; refusing the bounded "
            "direct-lookup limit of %d",
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
            normalized = _owned_open_issue(issue)
            if normalized is None:
                raise IssueLookupError(
                    f"tracked Omni issue #{number} lost exact ownership"
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
            exact_candidate=lambda issue: _owned_open_issue(issue) is not None,
            request_get=requests.get,
        )
    except (IssueLookupError, ValueError) as error:
        log.error("Omni issue recovery lookup failed: %s", error)
        return None
    for issue in candidates:
        normalized = _owned_open_issue(issue)
        if normalized is not None:
            owned_by_number[normalized["number"]] = normalized
    return [owned_by_number[number] for number in sorted(owned_by_number)]


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
    write_watcher_state(STATE, state, state_filename="open_omni_surge_issues.json")


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


def bounded_heuristic_payload(
    info: dict,
    *,
    max_bytes: int = OMNI_HEURISTIC_MAX_BYTES,
) -> dict:
    """Keep exact trigger scalars and a bounded deterministic pool index."""
    if max_bytes <= 0:
        raise ValueError("Omni heuristic byte budget must be positive")
    raw_pools = info.get("pool_distribution")
    source_pools = {
        str(name): int(count)
        for name, count in (raw_pools.items() if isinstance(raw_pools, dict) else ())
        if str(name)
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
    }
    prioritized = sorted(
        source_pools,
        key=lambda name: (-source_pools[name], name.casefold(), name),
    )

    def candidate(count: int) -> dict:
        selected = set(prioritized[:count])
        published = {
            name: source_pools[name]
            for name in sorted(selected, key=lambda value: (value.casefold(), value))
        }
        complete = len(published) == len(source_pools)
        result = {
            key: value
            for key, value in info.items()
            if key not in {"pool_distribution", "publication_retention"}
        }
        result["pool_distribution"] = published
        result["publication_retention"] = {
            "policy": "exact_trigger_scalars_then_largest_pool_rows_v1",
            "max_bytes": max_bytes,
            "complete_relative_to_source": complete,
            "trigger_scalars_complete": True,
            "pool_distribution": {
                "source": len(source_pools),
                "published": len(published),
                "omitted": len(source_pools) - len(published),
                "complete": complete,
            },
        }
        return result

    low, high = 0, len(source_pools)
    best = None
    while low <= high:
        keep = (low + high) // 2
        attempt = candidate(keep)
        if len(pretty_json_bytes(attempt)) <= max_bytes:
            best = attempt
            low = keep + 1
        else:
            high = keep - 1
    if best is None:
        raise RuntimeError(
            "Omni heuristic fixed metadata exceeds its byte budget; preserving "
            "the last-known-good file"
        )
    return best


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
    decides whether the Omni publication surface is usable.
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
    info = bounded_heuristic_payload(
        info,
        max_bytes=OMNI_HEURISTIC_MAX_BYTES,
    )
    write_pretty_json_lkg(
        HEURISTIC_PATH,
        info,
        max_bytes=OMNI_HEURISTIC_MAX_BYTES,
        label="Omni surge heuristic",
    )
    return trigger, healthy, info


def _heuristic_is_usable(info: dict) -> bool:
    """Return whether a refreshed payload is current publication evidence.

    A retained heuristic remains useful to suppress unsafe alert mutations,
    but it must not make a collection run look current.  Returning nonzero for
    every incomplete source refresh lets the publication selector quarantine
    the queue_omni transaction under its bounded fallback policy.
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
    pool_retention = (
        (heuristic.get("publication_retention") or {}).get("pool_distribution")
        or {}
    )
    omitted_pools = int(pool_retention.get("omitted") or 0)
    pool_coverage = (
        f"\n\n_{omitted_pools} smaller pool rows were omitted from this bounded "
        "diagnostic; total_groups and thresholds remain exact._"
        if omitted_pools
        else ""
    )
    body = (
        f"{OWNERSHIP_MARKER}\n"
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
        f"<details><summary>Per-pool distribution from omni YAMLs</summary>\n\n"
        f"{pools}{pool_coverage}\n</details>\n\n"
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
            "labels": [LABEL, AUTOMATED_LABEL, WORKSTREAM_LABEL],
            "assignees": [owner_login],
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        log.error("Failed to open surge issue: %d %s", resp.status_code, resp.text[:200])
        return None
    try:
        number = resp.json().get("number")
    except (AttributeError, ValueError):
        return None
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        return None
    return number


def _comment(token: str, repo: str, number: int, body: str) -> bool:
    try:
        resp = requests.post(
            f"{GH_API}/repos/{repo}/issues/{number}/comments",
            headers=_gh_headers(token), json={"body": body}, timeout=30,
        )
    except requests.RequestException as error:
        log.warning("Comment on #%d failed: %s", number, error)
        return False
    if resp.status_code >= 300:
        log.warning("Comment on #%d failed: %d", number, resp.status_code)
        return False
    return True


def _ensure_owner_assigned(token: str, repo: str, number: int) -> bool:
    owner_login = _repo_owner(repo)
    try:
        resp = requests.post(
            f"{GH_API}/repos/{repo}/issues/{number}/assignees",
            headers=_gh_headers(token), json={"assignees": [owner_login]}, timeout=30,
        )
    except requests.RequestException as error:
        log.warning("Assign owner on #%d failed: %s", number, error)
        return False
    if resp.status_code not in {200, 201}:
        log.warning("Assign owner on #%d failed: %d", number, resp.status_code)
        return False
    return True


def _close(token: str, repo: str, number: int) -> bool:
    try:
        resp = requests.patch(
            f"{GH_API}/repos/{repo}/issues/{number}",
            headers=_gh_headers(token),
            json={"state": "closed", "state_reason": "completed"},
            timeout=30,
        )
    except requests.RequestException as error:
        log.warning("Close #%d failed: %s", number, error)
        return False
    if resp.status_code >= 300:
        log.warning("Close #%d failed: %d", number, resp.status_code)
        return False
    return True


def _adopt_legacy_issue(token: str, repo: str, issue: dict) -> bool:
    """Add the exact marker to one strictly recognized legacy issue."""
    body = str(issue.get("body") or "")
    if _has_exact_ownership_marker(body):
        return True
    try:
        response = requests.patch(
            f"{GH_API}/repos/{repo}/issues/{issue['number']}",
            headers=_gh_headers(token),
            json={"body": f"{OWNERSHIP_MARKER}\n{body}"},
            timeout=30,
        )
    except requests.RequestException as error:
        log.warning("Adopt legacy Omni issue #%d failed: %s", issue["number"], error)
        return False
    if response.status_code >= 300:
        log.warning(
            "Adopt legacy Omni issue #%d failed: HTTP %d",
            issue["number"],
            response.status_code,
        )
        return False
    issue["body"] = f"{OWNERSHIP_MARKER}\n{body}"
    issue["legacy"] = False
    return True


def _reconcile_owned_open_issues(
    token: str,
    repo: str,
    tracked_number: int | None,
    run_url: str,
) -> tuple[dict | None, bool]:
    """Recover one canonical issue and close every proven duplicate.

    Discovery is completed before this function mutates anything. Existing
    exact-marker issues are preferred to legacy candidates when local state is
    missing; an already tracked owned issue remains canonical. ``False``
    means the caller must preserve its ledger and stop issue automation.
    """
    discovered = _list_owned_open_issues(
        token,
        repo,
        include_recovery=tracked_number is None,
        tracked_numbers=(tracked_number,) if tracked_number else (),
    )
    if discovered is None:
        return None, False
    discovered.sort(key=lambda issue: issue["number"])
    keeper = next(
        (issue for issue in discovered if issue["number"] == tracked_number),
        None,
    )
    if keeper is None:
        keeper = next(
            (issue for issue in discovered if not issue["legacy"]),
            discovered[0] if discovered else None,
        )

    if keeper is not None and not repair_issue_labels(
        token,
        repo,
        keeper,
        OWNED_LABELS,
        request_post=requests.post,
    ):
        log.error("Could not repair durable labels on Omni issue #%d", keeper["number"])
        return None, False

    for issue in discovered:
        if keeper is not None and issue["number"] == keeper["number"]:
            continue
        reason = (
            "Closing this duplicate watcher-owned Omni surge issue during "
            f"reconciliation. #{keeper['number']} remains canonical.\n\n"
            f"*{run_url}*"
        )
        if not _comment(token, repo, issue["number"], reason):
            return None, False
        if not _close(token, repo, issue["number"]):
            return None, False
        log.info("Closed duplicate Omni surge issue #%d", issue["number"])

    if keeper is not None and keeper["legacy"]:
        if not _adopt_legacy_issue(token, repo, keeper):
            return None, False
        log.info("Adopted legacy Omni surge issue #%d", keeper["number"])
    return keeper, True


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
    raw_open_issue = state.get("open")
    open_issue = (
        raw_open_issue
        if isinstance(raw_open_issue, int)
        and not isinstance(raw_open_issue, bool)
        and raw_open_issue > 0
        else None
    )
    next_state = dict(state)
    next_state["open"] = open_issue
    next_state["last_value"] = waiting
    next_state["last_trigger"] = trigger
    next_state["last_healthy"] = healthy
    next_state["last_snapshot_ts"] = snapshot.get("ts", "")

    if not token:
        log.warning("GITHUB_TOKEN not set; skipping GitHub mutations")
        _write_state(next_state)
        return 0

    if info["mutations_suppressed"]:
        log.error(
            "Omni YAML discovery status=%s; suppressing GitHub mutations "
            "(failed paths: %s)",
            info["source_status"],
            ", ".join(info["yaml_paths_failed"]) or "none",
        )
        _write_state(next_state)
        return 0

    canonical, discovery_complete = _reconcile_owned_open_issues(
        token,
        repo,
        open_issue,
        run_url,
    )
    if not discovery_complete:
        log.error(
            "Omni issue discovery or deduplication was incomplete; preserving state"
        )
        return 1
    open_issue = canonical["number"] if canonical is not None else None

    if waiting >= trigger and open_issue is None:
        number = _open_issue(token, repo, waiting, by_queue, info,
                             snapshot.get("ts", ""), run_url)
        if number is None:
            log.error("Omni surge issue open failed; preserving state")
            return 1
        next_state["open"] = number
        log.info("Opened omni surge issue #%d", number)
    elif waiting <= healthy and open_issue is not None:
        if not _ensure_owner_assigned(token, repo, open_issue):
            return 1
        if not _comment(
            token,
            repo,
            open_issue,
            f"Omni queue drained: {waiting} waiting (healthy ≤ {healthy}). Closing.\n\n*{run_url}*",
        ):
            return 1
        if not _close(token, repo, open_issue):
            return 1
        log.info("Closed omni surge issue #%d", open_issue)
        next_state["open"] = None
    elif open_issue is not None:
        if not _ensure_owner_assigned(token, repo, open_issue):
            return 1
        next_state["open"] = open_issue
    else:
        # Authoritative discovery found no owned issue. Clear stale local state
        # without manufacturing a remote mutation while the signal is healthy.
        next_state["open"] = None

    _write_state(next_state)
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
