"""Integrity-validated private checkpoint for bounded CI log backfills.

Only complete per-nightly result shards are checkpointed.  A transport-budget
failure can therefore retain the public last-known-complete CI surface while a
private Actions cache advances monotonically.  The next guarded attempt
restores those complete shards and skips their already-proven job rosters.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..dashboard_storage_budget import writer_max_bytes


SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"
SHARD_DIR = "test_results"
SHARD_RE = re.compile(r"\d{4}-\d{2}-\d{2}_(amd|upstream)\.jsonl")
MAX_SHARD_BYTES = writer_max_bytes("test_result_shard")
MAX_TOTAL_BYTES = writer_max_bytes("test_result_store")
MAX_SHARDS = 64
RETAIN_SHARDS = 16
MAX_LINE_BYTES = 2 * 1024 * 1024


class BackfillCheckpointError(ValueError):
    """The private backfill checkpoint is unsafe or inconsistent."""


def _decode_json(raw: bytes, *, label: str) -> object:
    def no_duplicates(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                raise ValueError(f"duplicate key {key!r}")
            value[key] = child
        return value

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise BackfillCheckpointError(f"{label} is not strict UTF-8 JSON: {exc}") from exc


def _canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _validate_shard(path: Path, name: str) -> dict[str, Any]:
    match = SHARD_RE.fullmatch(name)
    if match is None or path.name != name or not path.is_file() or path.is_symlink():
        raise BackfillCheckpointError(f"invalid checkpoint shard path {name!r}")
    size = path.stat().st_size
    if size <= 0 or size > MAX_SHARD_BYTES:
        raise BackfillCheckpointError(f"checkpoint shard {name} exceeds its byte bound")
    digest = hashlib.sha256()
    build_numbers: set[int] = set()
    expected_slug = "amd-ci" if match.group(1) == "amd" else "ci"
    rows = 0
    with path.open("rb") as handle:
        for raw_line in handle:
            if not raw_line.endswith(b"\n") or len(raw_line) > MAX_LINE_BYTES:
                raise BackfillCheckpointError(f"checkpoint shard {name} has an invalid line")
            digest.update(raw_line)
            try:
                row = _decode_json(raw_line, label=f"checkpoint shard {name}")
            except BackfillCheckpointError as exc:
                raise BackfillCheckpointError(
                    f"checkpoint shard {name} is not JSONL"
                ) from exc
            if not isinstance(row, dict) or row.get("pipeline") != expected_slug:
                raise BackfillCheckpointError(f"checkpoint shard {name} has wrong pipeline")
            number = row.get("build_number")
            if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
                raise BackfillCheckpointError(f"checkpoint shard {name} has invalid build")
            build_numbers.add(number)
            rows += 1
    if rows <= 0 or len(build_numbers) != 1:
        raise BackfillCheckpointError(
            f"checkpoint shard {name} must contain exactly one complete build"
        )
    return {
        "bytes": size,
        "sha256": digest.hexdigest(),
        "rows": rows,
        "build_number": next(iter(build_numbers)),
    }


def _load_manifest(root: Path) -> dict[str, Any]:
    manifest_path = root / MANIFEST_NAME
    try:
        raw = manifest_path.read_bytes()
        manifest = _decode_json(raw, label="checkpoint manifest")
    except (OSError, BackfillCheckpointError) as exc:
        raise BackfillCheckpointError(f"checkpoint manifest is unreadable: {exc}") from exc
    if len(raw) > 1024 * 1024 or not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "updated_at",
        "shards",
    }:
        raise BackfillCheckpointError("checkpoint manifest has an unexpected shape")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise BackfillCheckpointError("checkpoint schema is unsupported")
    try:
        updated = datetime.fromisoformat(manifest["updated_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise BackfillCheckpointError("checkpoint updated_at is invalid") from exc
    if (
        updated.tzinfo is None
        or updated.microsecond
        or updated.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        != manifest["updated_at"]
    ):
        raise BackfillCheckpointError("checkpoint updated_at is not canonical UTC")
    shards = manifest.get("shards")
    if not isinstance(shards, dict) or len(shards) > MAX_SHARDS:
        raise BackfillCheckpointError("checkpoint shard manifest is not bounded")
    normalized: dict[str, dict[str, Any]] = {}
    total = 0
    for name, descriptor in sorted(shards.items()):
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "bytes",
            "sha256",
            "rows",
            "build_number",
        }:
            raise BackfillCheckpointError(f"checkpoint descriptor {name!r} is invalid")
        actual = _validate_shard(root / SHARD_DIR / name, name)
        if descriptor != actual:
            raise BackfillCheckpointError(f"checkpoint descriptor {name!r} disagrees")
        total += actual["bytes"]
        if total > MAX_TOTAL_BYTES:
            raise BackfillCheckpointError("checkpoint exceeds its total byte bound")
        normalized[name] = actual
    actual_names = {
        path.name
        for path in (root / SHARD_DIR).glob("*")
        if path.is_file() or path.is_symlink()
    } if (root / SHARD_DIR).exists() else set()
    if actual_names != set(normalized):
        raise BackfillCheckpointError("checkpoint contains unmanifested shards")
    canonical = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": manifest["updated_at"],
        "shards": normalized,
    }
    if raw != _canonical(canonical):
        raise BackfillCheckpointError("checkpoint manifest is not canonical JSON")
    return canonical


def _empty(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    (root / SHARD_DIR).mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    manifest = {"schema_version": SCHEMA_VERSION, "updated_at": now, "shards": {}}
    _atomic_write(root / MANIFEST_NAME, _canonical(manifest))
    return manifest


def load_or_reset(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not root.exists():
        return _empty(root)
    try:
        return _load_manifest(root)
    except BackfillCheckpointError:
        # This is an ephemeral, exact-purpose Actions cache directory.  Never
        # carry corrupt restored bytes into a new immutable cache generation.
        shutil.rmtree(root)
        return _empty(root)


def restore_complete_shards(root: Path, results_dir: Path) -> int:
    manifest = load_or_reset(root)
    results_dir.mkdir(parents=True, exist_ok=True)
    restored = 0
    for name, descriptor in manifest["shards"].items():
        destination = results_dir / name
        if destination.exists() and destination.is_file() and not destination.is_symlink():
            try:
                existing = _validate_shard(destination, name)
            except BackfillCheckpointError:
                existing = None
            if existing is not None:
                if existing["build_number"] >= descriptor["build_number"]:
                    continue
        source = root / SHARD_DIR / name
        temporary = destination.with_name(f".{destination.name}.backfill")
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
        restored += 1
    return restored


def record_complete_shard(root: Path, shard: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest = load_or_reset(root)
    descriptor = _validate_shard(shard, shard.name)
    existing = manifest["shards"].get(shard.name)
    if existing is not None and existing["build_number"] > descriptor["build_number"]:
        raise BackfillCheckpointError("checkpoint progress may not regress a build number")
    shards = dict(manifest["shards"])
    shards[shard.name] = descriptor
    retained_names = set(shards)
    while True:
        retained = {
            name: row for name, row in shards.items() if name in retained_names
        }
        if (
            len(retained) <= min(RETAIN_SHARDS, MAX_SHARDS)
            and sum(row["bytes"] for row in retained.values()) <= MAX_TOTAL_BYTES
        ):
            break
        retained_days = sorted({name[:10] for name in retained})
        if len(retained_days) <= 1:
            raise BackfillCheckpointError(
                "checkpoint newest whole UTC day exceeds its storage bound"
            )
        oldest_day = retained_days[0]
        retained_names.difference_update(
            name for name in retained if name.startswith(f"{oldest_day}_")
        )
    target = root / SHARD_DIR / shard.name
    if shard.name in retained_names:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.incoming")
        shutil.copyfile(shard, temporary)
        os.replace(temporary, target)
    for stale_name in sorted(set(shards) - retained_names):
        try:
            (root / SHARD_DIR / stale_name).unlink()
        except FileNotFoundError:
            pass
        shards.pop(stale_name, None)
    updated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    updated = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": updated_at,
        "shards": dict(sorted(shards.items())),
    }
    _atomic_write(root / MANIFEST_NAME, _canonical(updated))
    _load_manifest(root)
    return descriptor


def validate(root: Path) -> dict[str, int]:
    manifest = _load_manifest(root.resolve())
    return {
        "shards": len(manifest["shards"]),
        "bytes": sum(row["bytes"] for row in manifest["shards"].values()),
    }
