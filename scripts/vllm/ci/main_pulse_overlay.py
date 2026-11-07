"""Newer-than-collection incident observations recorded by the main pulse.

The full Data Collection watcher state stays authoritative. The pulse stores
only the strict groups whose source-order watermark is newer than that state.
Merging takes a group from the overlay only while its watermark is still
newer, so a later collection that saw the same builds always wins. The retired
``issue``/``issue_basis`` keys stay in the schema and must be ``null``/``""``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vllm import amd_main_failure_watcher as watcher
from vllm import queue_request_budget as ledger_git


OVERLAY_SCHEMA_VERSION = 1
GROUP_STATUSES = frozenset({"active", "pending_soft", "clear"})
MAX_OVERLAY_GROUPS = 4096
MAX_PROCESSED_BUILDS = 512
MAX_TIMELINE_EVENTS = watcher.TIMELINE_MAX_EVENTS
EXPORT_FIELDS = frozenset({"schema_version", "branch", "state_sha", "overlay"})
OVERLAY_FIELDS = frozenset(
    {
        "schema_version",
        "updated_at",
        "cursor",
        "groups",
        "issue",
        "issue_basis",
        "timeline",
        "processed_builds",
        "last_run",
    }
)


def group_status(state: dict, group_id: str) -> tuple[str, dict | None]:
    for status in ("active", "pending_soft"):
        entry = (state.get(status) or {}).get(group_id)
        if isinstance(entry, dict):
            return status, entry
    return "clear", None


def is_newer(watermark: dict, current: dict | None) -> bool:
    return not current or watcher._watermark_rank(watermark) > watcher._watermark_rank(current)


def _event_key(event: dict) -> tuple:
    return (event.get("event"), event.get("group_id"), event.get("build_number"))


def merge_overlay(state: dict, overlay: dict | None) -> dict:
    """Return ``state`` with every still-newer pulse observation applied."""
    merged = dict(state)
    if not overlay:
        return merged
    active = dict(state.get("active") or {})
    pending_soft = dict(state.get("pending_soft") or {})
    watermarks = dict(state.get("group_watermarks") or {})
    for group_id, row in overlay["groups"].items():
        if not is_newer(row["watermark"], watermarks.get(group_id)):
            continue
        watermarks[group_id] = dict(row["watermark"])
        active.pop(group_id, None)
        pending_soft.pop(group_id, None)
        if row["status"] == "active":
            active[group_id] = dict(row["entry"])
        elif row["status"] == "pending_soft":
            pending_soft[group_id] = dict(row["entry"])
    merged.update(active=active, pending_soft=pending_soft, group_watermarks=watermarks)
    if isinstance(state.get("timeline"), list) and overlay["timeline"]:
        seen = {_event_key(event) for event in state["timeline"] if isinstance(event, dict)}
        merged["timeline"] = list(state["timeline"]) + [
            dict(event) for event in overlay["timeline"] if _event_key(event) not in seen
        ]
    return merged


def overlay_groups(authority: dict, pulse_state: dict) -> dict[str, dict]:
    """Groups whose pulse watermark is newer than the collection state's."""
    authority_watermarks = authority.get("group_watermarks") or {}
    groups = {}
    for group_id, watermark in sorted((pulse_state.get("group_watermarks") or {}).items()):
        if not is_newer(watermark, authority_watermarks.get(group_id)):
            continue
        status, entry = group_status(pulse_state, group_id)
        groups[group_id] = {"watermark": dict(watermark), "status": status, "entry": entry}
    return groups


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"main pulse overlay {message}")


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def normalize_overlay(value: Any) -> dict:
    """Validate one decoded overlay and return it unchanged in shape."""
    _require(isinstance(value, dict) and set(value) == OVERLAY_FIELDS, "has an unexpected shape")
    _require(value["schema_version"] == OVERLAY_SCHEMA_VERSION, "schema is unsupported")
    for field in ("updated_at", "cursor"):
        _require(watcher._parse_ts(value[field]) is not None, f"{field} is not a timestamp")
    groups = value["groups"]
    _require(isinstance(groups, dict) and len(groups) <= MAX_OVERLAY_GROUPS, "groups are unbounded")
    for group_id, row in groups.items():
        _require(
            isinstance(group_id, str)
            and group_id
            and isinstance(row, dict)
            and set(row) == {"watermark", "status", "entry"}
            and row["status"] in GROUP_STATUSES
            and isinstance(row["watermark"], dict)
            and _positive_int(row["watermark"].get("build_number")),
            f"group {group_id!r} is malformed",
        )
        if row["status"] == "clear":
            _require(row["entry"] is None, f"clear group {group_id!r} carries an entry")
        else:
            _require(
                isinstance(row["entry"], dict) and isinstance(row["entry"].get("transition"), dict),
                f"group {group_id!r} has no transition",
            )
    _require(value["issue"] is None and value["issue_basis"] == "", "carries retired issue fields")
    timeline = value["timeline"]
    _require(
        isinstance(timeline, list)
        and len(timeline) <= MAX_TIMELINE_EVENTS
        and all(isinstance(event, dict) for event in timeline),
        "timeline is malformed",
    )
    processed = value["processed_builds"]
    _require(
        isinstance(processed, list)
        and len(processed) <= MAX_PROCESSED_BUILDS
        and all(
            isinstance(row, dict)
            and set(row) == {"number", "created_at"}
            and _positive_int(row["number"])
            for row in processed
        ),
        "processed builds are malformed",
    )
    _require(isinstance(value["last_run"], dict), "last_run is malformed")
    return value


def decode_overlay(payload: bytes) -> dict:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"main pulse overlay is not UTF-8 JSON: {exc}") from exc
    return normalize_overlay(value)


def encode_overlay(overlay: dict) -> bytes:
    return (json.dumps(normalize_overlay(overlay), indent=1, sort_keys=True) + "\n").encode()


def load_overlay_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    return decode_overlay(path.read_bytes())


def encode_export(branch: str, state_sha: str | None, overlay: dict | None) -> bytes:
    """One export record; ``state_sha`` is None only when the branch is absent."""
    return (
        json.dumps(
            normalize_export(
                {
                    "schema_version": OVERLAY_SCHEMA_VERSION,
                    "branch": branch,
                    "state_sha": state_sha,
                    "overlay": overlay,
                }
            ),
            indent=1,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def normalize_export(value: Any) -> dict:
    _require(isinstance(value, dict) and set(value) == EXPORT_FIELDS, "export has an unexpected shape")
    _require(value["schema_version"] == OVERLAY_SCHEMA_VERSION, "export schema is unsupported")
    try:
        ledger_git._safe_branch(value["branch"], label="branch")
    except ledger_git.QueueRequestBudgetError as exc:
        raise ValueError(f"main pulse overlay export {exc}") from None
    sha = value["state_sha"]
    _require(sha is None or (isinstance(sha, str) and ledger_git.FULL_SHA_RE.fullmatch(sha)), "export sha is malformed")
    _require((sha is None) == (value["overlay"] is None), "export sha and overlay disagree")
    if value["overlay"] is not None:
        normalize_overlay(value["overlay"])
    return value


def load_export_file(path: Path) -> dict:
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"main pulse overlay export is not UTF-8 JSON: {exc}") from exc
    return normalize_export(value)
