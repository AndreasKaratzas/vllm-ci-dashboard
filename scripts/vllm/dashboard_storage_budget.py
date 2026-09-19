"""Strict shared byte budgets for the immutable dashboard-state tree.

The state commit contains the complete code checkout as well as generated
data.  Per-writer limits alone therefore cannot prove that independently
growing surfaces still compose below the state tree ceiling.  This module
loads the single allocation policy used both by writers and by the final
staged-index publication guard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config" / "dashboard_state_storage_budget.json"


class StorageBudgetError(ValueError):
    """The storage allocation is malformed or cannot fit the state tree."""


@dataclass(frozen=True)
class StorageGroup:
    name: str
    max_bytes: int
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class WriterLimit:
    name: str
    group: str
    max_bytes: int


@dataclass(frozen=True)
class StorageBudget:
    max_tree_bytes: int
    max_files: int
    required_headroom_bytes: int
    unmanaged_max_bytes: int
    groups: dict[str, StorageGroup]
    writer_limits: dict[str, WriterLimit]

    @property
    def allocated_bytes(self) -> int:
        return self.unmanaged_max_bytes + sum(
            group.max_bytes for group in self.groups.values()
        )

    @property
    def available_headroom_bytes(self) -> int:
        return self.max_tree_bytes - self.allocated_bytes

    def matching_groups(self, path: str) -> tuple[str, ...]:
        candidate = PurePosixPath(path)
        return tuple(
            name
            for name, group in self.groups.items()
            if any(candidate.match(pattern) for pattern in group.patterns)
        )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise StorageBudgetError(f"storage budget repeats key {key!r}")
        value[key] = child
    return value


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StorageBudgetError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise StorageBudgetError(f"{label} must be a JSON object")
    return value


def _positive_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StorageBudgetError(f"{label} must be a positive integer")
    return value


def _safe_path(value: object, *, label: str, allow_glob: bool = False) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise StorageBudgetError(f"{label} must be a nonempty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise StorageBudgetError(f"{label} must remain repository-relative")
    if not allow_glob and any(char in value for char in "*?[]"):
        raise StorageBudgetError(f"{label} cannot contain glob syntax")
    return value


def load_storage_budget(path: Path = DEFAULT_CONFIG_PATH) -> StorageBudget:
    """Load and fully validate one repository storage allocation."""
    path = Path(path).resolve()
    payload = _load_json(path, label="dashboard storage budget")
    if set(payload) != {
        "schema_version",
        "state_policy",
        "required_headroom_bytes",
        "unmanaged_max_bytes",
        "groups",
        "writer_limits",
    }:
        raise StorageBudgetError("dashboard storage budget has an unexpected shape")
    if payload.get("schema_version") != 1:
        raise StorageBudgetError("dashboard storage budget schema_version is unsupported")

    state_policy_rel = _safe_path(payload.get("state_policy"), label="state_policy")
    # The allocation file normally lives in ``config/``. Resolve the policy
    # from the repository root so the same checked-in document is authoritative
    # for local runs and Actions.
    repository_root = path.parent.parent
    state_policy = _load_json(
        repository_root / state_policy_rel,
        label="dashboard state policy",
    )
    try:
        max_tree_bytes = _positive_int(
            state_policy["limits"]["max_tree_bytes"],
            label="state policy max_tree_bytes",
        )
        max_files = _positive_int(
            state_policy["limits"]["max_files"],
            label="state policy max_files",
        )
    except (KeyError, TypeError) as exc:
        raise StorageBudgetError(
            "dashboard state policy has no tree byte/file limit"
        ) from exc

    raw_groups = payload.get("groups")
    if not isinstance(raw_groups, dict) or not raw_groups:
        raise StorageBudgetError("storage groups must be a nonempty object")
    groups: dict[str, StorageGroup] = {}
    all_patterns: set[str] = set()
    for name, raw_group in sorted(raw_groups.items()):
        if not isinstance(name, str) or not name or not isinstance(raw_group, dict):
            raise StorageBudgetError("storage group entries are invalid")
        if set(raw_group) != {"max_bytes", "patterns"}:
            raise StorageBudgetError(f"storage group {name!r} has an unexpected shape")
        raw_patterns = raw_group.get("patterns")
        if (
            not isinstance(raw_patterns, list)
            or not raw_patterns
            or any(not isinstance(item, str) for item in raw_patterns)
        ):
            raise StorageBudgetError(f"storage group {name!r} patterns are invalid")
        patterns = tuple(
            _safe_path(item, label=f"storage group {name!r} pattern", allow_glob=True)
            for item in raw_patterns
        )
        if len(set(patterns)) != len(patterns) or all_patterns.intersection(patterns):
            raise StorageBudgetError("storage group patterns must be globally unique")
        all_patterns.update(patterns)
        groups[name] = StorageGroup(
            name=name,
            max_bytes=_positive_int(
                raw_group.get("max_bytes"), label=f"storage group {name!r} max_bytes"
            ),
            patterns=patterns,
        )

    raw_writers = payload.get("writer_limits")
    if not isinstance(raw_writers, dict) or not raw_writers:
        raise StorageBudgetError("writer_limits must be a nonempty object")
    writers: dict[str, WriterLimit] = {}
    for name, raw_writer in sorted(raw_writers.items()):
        if not isinstance(name, str) or not name or not isinstance(raw_writer, dict):
            raise StorageBudgetError("writer limit entries are invalid")
        if set(raw_writer) != {"group", "max_bytes"}:
            raise StorageBudgetError(f"writer limit {name!r} has an unexpected shape")
        group = raw_writer.get("group")
        if not isinstance(group, str) or group not in groups:
            raise StorageBudgetError(f"writer limit {name!r} names an unknown group")
        max_bytes = _positive_int(
            raw_writer.get("max_bytes"), label=f"writer limit {name!r} max_bytes"
        )
        if max_bytes > groups[group].max_bytes:
            raise StorageBudgetError(f"writer limit {name!r} exceeds group {group!r}")
        writers[name] = WriterLimit(name=name, group=group, max_bytes=max_bytes)

    budget = StorageBudget(
        max_tree_bytes=max_tree_bytes,
        max_files=max_files,
        required_headroom_bytes=_positive_int(
            payload.get("required_headroom_bytes"), label="required_headroom_bytes"
        ),
        unmanaged_max_bytes=_positive_int(
            payload.get("unmanaged_max_bytes"), label="unmanaged_max_bytes"
        ),
        groups=groups,
        writer_limits=writers,
    )
    if budget.available_headroom_bytes < budget.required_headroom_bytes:
        raise StorageBudgetError(
            "storage allocations do not preserve the required state-tree headroom"
        )
    return budget


def group_max_bytes(name: str, path: Path = DEFAULT_CONFIG_PATH) -> int:
    try:
        return load_storage_budget(path).groups[name].max_bytes
    except KeyError as exc:
        raise StorageBudgetError(f"unknown storage group {name!r}") from exc


def writer_max_bytes(name: str, path: Path = DEFAULT_CONFIG_PATH) -> int:
    try:
        return load_storage_budget(path).writer_limits[name].max_bytes
    except KeyError as exc:
        raise StorageBudgetError(f"unknown writer limit {name!r}") from exc
