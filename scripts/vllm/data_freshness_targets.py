"""Collector freshness targets shared by the snapshot builder, Site Health and the web build."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "data_freshness_targets.json"


@cache
def load_freshness_targets() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text())


def _utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def core_generated_at(sources: Mapping[str, Any]) -> str | None:
    """Return the oldest core collection time, or None while any core source is undated."""
    names = load_freshness_targets()["core"]["sources"]
    stamps = [
        stamp
        for name in names
        if (stamp := _utc((sources.get(name) or {}).get("timestamp"))) is not None
    ]
    if not stamps or len(stamps) != len(names):
        return None
    return min(stamps).strftime("%Y-%m-%dT%H:%M:%SZ")
