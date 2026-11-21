#!/usr/bin/env python3
"""Omni workload surge heuristic.

Derives the threshold above which the number of Omni-classified jobs waiting
across AMD queues counts as a surge, by counting test groups in the
``vllm-project/vllm-omni`` Buildkite YAMLs:

    trigger = max(OMNI_SURGE_FLOOR_TRIGGER, ceil(multiplier * total_groups))
    healthy = floor(trigger * healthy_ratio)

If the YAML sources cannot all be read, the dashboard retains the last
known-good heuristic (or exposes the static floor when none exists). A moved or
temporarily unavailable source must never silently lower the threshold.
"""

from __future__ import annotations

import json
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.constants import (  # noqa: E402
    OMNI_REPO,
    OMNI_SURGE_FLOOR_TRIGGER,
    OMNI_SURGE_HEALTHY_RATIO,
    OMNI_SURGE_MULTIPLIER,
    OMNI_YAML_PATHS,
)
from vllm.bounded_json import pretty_json_bytes, write_pretty_json_lkg  # noqa: E402
from vllm.dashboard_storage_budget import writer_max_bytes  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
HEURISTIC_PATH = ROOT / "data" / "vllm" / "ci" / "omni_surge_heuristic.json"
OMNI_HEURISTIC_MAX_BYTES = writer_max_bytes("omni_surge_heuristic")

RAW_BASE = "https://raw.githubusercontent.com"


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
    """Build a status-rich heuristic and indicate whether it is current."""
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
    """Refresh and persist the heuristic.

    This is deliberately independent from the queue snapshot so publication
    validation can refresh the heuristic before it decides whether the Omni
    publication surface is usable.
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

    A retained heuristic remains useful as a threshold, but it must not make
    a collection run look current.  Returning nonzero for
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


def run() -> int:
    _, _, info = _refresh_heuristic()
    if not _heuristic_is_usable(info):
        log.error(
            "Omni heuristic refresh produced no auditable usable result "
            "(source status: %s)",
            info.get("source_status") or "unknown",
        )
        return 1
    log.info(
        "Refreshed Omni heuristic (status=%s, groups=%d, trigger=%d)",
        info["source_status"],
        info["total_groups"],
        info["trigger"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
