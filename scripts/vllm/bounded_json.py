"""Atomic JSON publication helpers for last-known-good dashboard snapshots."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def pretty_json_bytes(payload: Any) -> bytes:
    """Return the repository's canonical human-readable JSON encoding."""
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Fsync and atomically replace one file in its existing directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def write_pretty_json_lkg(
    path: Path,
    payload: Any,
    *,
    max_bytes: int,
    label: str,
) -> int:
    """Write a bounded JSON value without replacing the LKG on overflow."""
    encoded = pretty_json_bytes(payload)
    if len(encoded) > max_bytes:
        raise RuntimeError(
            f"{label} exceeds its byte budget; preserving the last-known-good "
            f"file: {len(encoded)} > {max_bytes} bytes"
        )
    atomic_write_bytes(path, encoded)
    return len(encoded)
