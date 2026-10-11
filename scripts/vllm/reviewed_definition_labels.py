"""Resolve reviewed row names through explicit upstream YAML keys.

Names may follow current definitions; reviewed coverage and gating decisions
never do. A removed or split key requires a new scope review. Positional YAML
indices and approximate name matching are deliberately not identities here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


KEYED_DEFINITION_RE = re.compile(r"\.buildkite/test_areas/[a-zA-Z0-9_-]+\.yaml#[a-zA-Z0-9_-]*[a-zA-Z][a-zA-Z0-9_-]*")
FULL_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
PLATFORM_PREFIX_RE = re.compile(r"^:(?:nvidia|amd|intel|computer):\s*\([^)]*\)\s*", re.I)
MAX_REPORT_BYTES = 8 * 1024 * 1024


def flatten_execution_commands(raw_cmds: Any) -> list[str]:
    """Share the existing YAML command parser across definition producers."""
    if not raw_cmds:
        return []
    flat = []
    for command in raw_cmds:
        if isinstance(command, list):
            flat.extend(flatten_execution_commands(command))
        elif isinstance(command, str):
            for line in command.strip().split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    flat.append(line)
    return flat


def definition_title(label: str, *, keep_shards: bool = False) -> str:
    """Remove execution hardware decoration, retaining workload qualifiers."""
    title = PLATFORM_PREFIX_RE.sub("", label.strip())
    if not keep_shards:
        title = re.sub(r"\s*%N\b", "", title)
    return title.strip()


def validate_definition_ids(row: dict[str, Any], *, context: str) -> None:
    for field in ("upstream_definition_ids", "successor_definition_ids"):
        ids = row.get(field, [])
        if not isinstance(ids, list) or any(
            not isinstance(value, str) or not KEYED_DEFINITION_RE.fullmatch(value)
            for value in ids
        ) or len(ids) != len(set(ids)):
            raise ValueError(f"{context}.{field} must contain unique explicit upstream YAML keys")
    hashes = row.get("amd_execution_sha256s", [])
    if hashes and row.get("upstream_definition_ids"):
        raise ValueError(f"{context} cannot mix upstream keys and AMD execution identities")
    if not isinstance(hashes, list) or any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
        for value in hashes
    ) or len(hashes) != len(set(hashes)):
        raise ValueError(f"{context}.amd_execution_sha256s must contain unique SHA-256 hashes")


def execution_sha256(step: dict[str, Any]) -> str:
    """Identify an execution route independently of its display label/index."""
    identity = {
        "commands": step["commands"],
        "working_dir": step.get("working_dir", ""),
        "agent_pool": step.get("agent_pool", ""),
        "num_gpus": step.get("num_gpus"),
        "parallelism": step.get("parallelism"),
        "source_file": step["source_file"],
        "definition_fingerprint": step.get("definition_fingerprint", ""),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_definition_parity(path: Path) -> dict[str, Any]:
    """A missing optional name snapshot leaves reviewed rows unresolved."""
    try:
        if path.stat().st_size > MAX_REPORT_BYTES:
            return {}
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _definition_labels(payload: dict[str, Any]) -> dict[str, set[str]]:
    labels: dict[str, set[str]] = defaultdict(set)
    for collection in ("matches", "mirrors", "inline_mirror_variants", "additional_variants", "nvidia_only"):
        rows = payload.get(collection)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if collection == "nvidia_only":
                identity, label = row.get("definition_id"), row.get("label")
            else:
                identity, label = row.get("nvidia_definition_id"), row.get("nvidia_label")
            if isinstance(identity, str) and KEYED_DEFINITION_RE.fullmatch(identity) and isinstance(label, str) and label.strip():
                labels[identity].add(label.strip())
    return dict(labels)


def _amd_execution_labels(payload: dict[str, Any]) -> tuple[dict[str, set[str]], set[str]]:
    retention = payload.get("publication_retention") or {}
    collections = (retention.get("collections") or {}) if isinstance(retention, dict) else {}
    catalog_retention = (collections.get("amd_execution_definitions") or {}) if isinstance(collections, dict) else {}
    if isinstance(catalog_retention, dict) and catalog_retention.get("complete_relative_to_source") is False:
        return {}, set()
    labels: dict[str, set[str]] = defaultdict(set)
    definitions: dict[str, set[str]] = defaultdict(set)
    rows = payload.get("amd_execution_definitions")
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        signature, label = row.get("execution_sha256"), row.get("label")
        if isinstance(signature, str) and re.fullmatch(r"[0-9a-f]{64}", signature) and isinstance(label, str) and label.strip():
            labels[signature].add(label.strip())
            definitions[signature].add(str(row.get("definition_id") or label))
    return dict(labels), {signature for signature, ids in definitions.items() if len(ids) > 1}


def resolve_reviewed_definition_labels(
    rows: list[dict[str, Any]],
    definition_parity: dict[str, Any],
    *,
    label_field: str,
) -> list[dict[str, Any]]:
    """Update presentation only when every reviewed source key resolves.

Successors are informational: they never satisfy a missing reviewed key or
inherit its coverage decision. Conflicting or diverged identities keep the
reviewed display name and publish an explicit unresolved state.
"""
    source = definition_parity.get("source") or {}
    commit = source.get("commit_sha", "") if isinstance(source, dict) else ""
    valid_source = isinstance(commit, str) and FULL_COMMIT_RE.fullmatch(commit) is not None
    retention = definition_parity.get("publication_retention") or {}
    incomplete = isinstance(retention, dict) and retention.get("complete_relative_to_source") is False
    index = _definition_labels(definition_parity) if valid_source else {}
    amd_index, ambiguous_commands = _amd_execution_labels(definition_parity) if valid_source else ({}, set())
    resolved = []
    for raw in rows:
        row = dict(raw)
        reviewed_field = f"reviewed_{label_field}"
        row.setdefault(reviewed_field, row.get(label_field, ""))
        ids = row.get("upstream_definition_ids") or []
        valid_ids = isinstance(ids, list) and all(
            isinstance(value, str) and KEYED_DEFINITION_RE.fullmatch(value)
            for value in ids
        )
        missing = [value for value in ids if value not in index] if valid_ids else []
        labels = sorted({label for value in ids for label in index.get(value, set())}) if valid_ids else []
        titles = {definition_title(label, keep_shards=label_field == "label") for label in labels}
        hashes = row.get("amd_execution_sha256s") or []
        use_commands = bool(hashes) and isinstance(hashes, list) and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes)
        if use_commands:
            missing = [value for value in hashes if value not in amd_index]
            labels = sorted({label for value in hashes for label in amd_index.get(value, set())})
            titles = {definition_title(label, keep_shards=label_field == "label") for label in labels}
        if use_commands and ids:
            status = "ambiguous"
        elif not use_commands and (not ids or not valid_ids):
            status = "unconfigured"
        elif not valid_source:
            status = "unavailable"
        elif use_commands and not isinstance(definition_parity.get("amd_execution_definitions"), list):
            status = "unavailable"
        elif missing:
            status = "unavailable" if incomplete else "unresolved"
        elif (
            (use_commands and any(value in ambiguous_commands for value in hashes))
            or (not use_commands and any(len(index[value]) != 1 for value in ids))
            or len(titles) != 1
        ):
            status = "ambiguous"
        else:
            status = "resolved"
            row[label_field] = next(iter(titles)) + str(row.get("label_suffix") or "")
        successors = row.get("successor_definition_ids") or []
        resolution = {
            "status": status,
            "source_kind": "amd_execution" if use_commands else "upstream_keys",
            "commit_sha": commit if valid_source else "",
            "definition_ids": list(ids) if valid_ids else [],
            "labels": labels,
            "missing_definition_ids": [] if use_commands else missing,
        }
        if use_commands:
            resolution["missing_execution_sha256s"] = missing
        if successors:
            resolution["successor_definition_ids"] = list(successors)
            resolution["successor_labels"] = sorted({
                label for value in successors for label in index.get(value, set())
            })
        if row.get("definition_note"):
            resolution["note"] = row["definition_note"]
        row["definition_resolution"] = resolution
        resolved.append(row)
    return resolved
