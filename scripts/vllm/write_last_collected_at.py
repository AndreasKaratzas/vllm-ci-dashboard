#!/usr/bin/env python3
"""Atomically advance the bounded canonical collector clock."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.bounded_json import atomic_write_bytes  # noqa: E402
from vllm.dashboard_storage_budget import writer_max_bytes  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "data" / "vllm" / "ci" / "last_collected_at.txt"
LAST_COLLECTED_AT_MAX_BYTES = writer_max_bytes("last_collected_at")


def canonical_timestamp(value: str | None = None) -> str:
    timestamp = value or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError("collector timestamp must use canonical UTC YYYY-MM-DDTHH:MM:SSZ") from exc
    canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical != timestamp:
        raise ValueError("collector timestamp must be canonical UTC")
    return canonical


def write_timestamp(
    output: Path,
    *,
    timestamp: str | None = None,
    max_bytes: int = LAST_COLLECTED_AT_MAX_BYTES,
) -> str:
    if max_bytes <= 0:
        raise ValueError("collector timestamp byte budget must be positive")
    canonical = canonical_timestamp(timestamp)
    encoded = f"{canonical}\n".encode("ascii")
    if len(encoded) > max_bytes:
        raise RuntimeError(
            "collector timestamp exceeds its byte budget; preserving the "
            "last-known-good clock"
        )
    atomic_write_bytes(output, encoded)
    return canonical


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timestamp")
    args = parser.parse_args(argv)
    written = write_timestamp(args.output, timestamp=args.timestamp)
    print(f"Wrote {args.output} at {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
