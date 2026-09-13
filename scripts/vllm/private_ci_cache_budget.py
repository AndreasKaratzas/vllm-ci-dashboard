"""Strict checked-in byte limits for private CI cache and handoff files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config" / "private_ci_cache_budget.json"


class PrivateCiCacheBudgetError(ValueError):
    """The private CI cache allocation is malformed or inconsistent."""


@dataclass(frozen=True)
class PrivateCiCacheBudget:
    nightly_roster_max_shard_bytes: int
    nightly_roster_max_total_bytes: int
    amd_frozen_nightly_max_bytes: int


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise PrivateCiCacheBudgetError(
                f"private CI cache budget repeats key {key!r}"
            )
        value[key] = child
    return value


def _positive_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PrivateCiCacheBudgetError(f"{label} must be a positive integer")
    return value


def load_private_ci_cache_budget(
    path: Path = DEFAULT_CONFIG_PATH,
) -> PrivateCiCacheBudget:
    try:
        payload = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrivateCiCacheBudgetError(
            f"private CI cache budget is unreadable: {exc}"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "nightly_roster_cache",
        "amd_frozen_nightly_handoff",
    }:
        raise PrivateCiCacheBudgetError("private CI cache budget has an invalid shape")
    if payload.get("schema_version") != 1:
        raise PrivateCiCacheBudgetError(
            "private CI cache budget schema_version is unsupported"
        )
    roster = payload.get("nightly_roster_cache")
    handoff = payload.get("amd_frozen_nightly_handoff")
    if not isinstance(roster, dict) or set(roster) != {
        "max_shard_bytes",
        "max_total_bytes",
    }:
        raise PrivateCiCacheBudgetError("nightly_roster_cache has an invalid shape")
    if not isinstance(handoff, dict) or set(handoff) != {"max_bytes"}:
        raise PrivateCiCacheBudgetError(
            "amd_frozen_nightly_handoff has an invalid shape"
        )
    max_shard = _positive_int(
        roster.get("max_shard_bytes"), label="nightly roster max_shard_bytes"
    )
    max_total = _positive_int(
        roster.get("max_total_bytes"), label="nightly roster max_total_bytes"
    )
    max_handoff = _positive_int(
        handoff.get("max_bytes"), label="AMD frozen-nightly max_bytes"
    )
    if max_shard > max_total:
        raise PrivateCiCacheBudgetError(
            "nightly roster shard limit exceeds its total cache limit"
        )
    if max_handoff > max_total:
        raise PrivateCiCacheBudgetError(
            "AMD frozen-nightly handoff exceeds the private cache budget"
        )
    if max_total >= 90_000_000:
        raise PrivateCiCacheBudgetError(
            "private CI cache files must remain below the 90 MB sync boundary"
        )
    return PrivateCiCacheBudget(
        nightly_roster_max_shard_bytes=max_shard,
        nightly_roster_max_total_bytes=max_total,
        amd_frozen_nightly_max_bytes=max_handoff,
    )


PRIVATE_CI_CACHE_BUDGET = load_private_ci_cache_budget()
