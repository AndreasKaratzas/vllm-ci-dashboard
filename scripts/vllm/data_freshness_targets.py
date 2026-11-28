"""Loader for config/data_freshness_targets.json, read by the snapshot builder and Site Health."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any, Mapping


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "data_freshness_targets.json"


@cache
def load_freshness_targets() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text())


def source_stale_minutes(name: str) -> int:
    """Return the age at which a core source is stale; a source may declare its own cadence."""
    targets = load_freshness_targets()
    return targets["sources"][name].get("stale_minutes", targets["core"]["stale_minutes"])


def core_clock_sources() -> list[str]:
    """Core sources plus the all-main cohort clocks that Site Health and the watchdog judge.

    ``analytics.json`` advances even when an incomplete Buildkite scan preserves
    the previous all-main cohort, so main-derived data carries its own clock.
    """
    core = load_freshness_targets()["core"]
    return [*core["sources"], *core.get("main_derived_sources", [])]


def collector_stale_minutes(name: str) -> int:
    """Return the stale limit of any configured source, as Site Health judges core clocks."""
    targets = load_freshness_targets()
    if name in core_clock_sources():
        return source_stale_minutes(name)
    if name in targets["queue"]["sources"]:
        return targets["queue"]["stale_minutes"]
    source = targets["sources"][name]
    return source.get("collector_limit_minutes", targets["default_collector_limit_minutes"])


def surface_stale_minutes(source_surfaces: Mapping[str, str]) -> dict[str, int]:
    """Return each surface's tightest source stale limit."""
    limits: dict[str, int] = {}
    for source in load_freshness_targets()["sources"]:
        surface = source_surfaces.get(source)
        if surface is not None:
            limit = collector_stale_minutes(source)
            limits[surface] = min(limits.get(surface, limit), limit)
    return dict(sorted(limits.items()))
