#!/usr/bin/env python3
"""Build only the lazy Operations v2 queue section.

The frequent queue collector must be able to publish fresh queue evidence
without rebuilding every unrelated dashboard section. This script reads only
``queue_timeseries.jsonl`` and ``queue_jobs.json`` and writes the same bounded
``operations_v2/queue.json`` payload as the full operations snapshot builder.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the local ``scripts/vllm`` package win when executed as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.bounded_json import atomic_write_bytes
from vllm.build_operations_snapshot import (
    OPERATIONS_BUNDLE_DIR_NAME,
    QUEUE_HISTORY_CHART_NAME,
    SOURCE_FILES,
    _compact_queue,
    _filter_queue_snapshot,
    _load_json,
    _queue,
    load_latest_queue_snapshot,
    load_queue_history,
    write_queue_history_chart,
)
from vllm.queue_section_projection import (
    QUEUE_SECTION_MAX_BYTES,
    compact_queue_section,
    encode_queue_section,
)


DEFAULT_INPUT = Path(__file__).resolve().parent.parent.parent / "data" / "vllm" / "ci"


def build_queue_section(data_dir: Path) -> dict:
    history_path = data_dir / SOURCE_FILES["queue_timeseries"]
    history = load_queue_history(history_path)
    snapshot = _filter_queue_snapshot(load_latest_queue_snapshot(history_path))
    queue_jobs = _load_json(data_dir / SOURCE_FILES["queue_jobs"]) or {}
    return compact_queue_section(_compact_queue(_queue(snapshot, queue_jobs, history)))


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def validate_queue_section_file(path: Path) -> int:
    """Validate one staged live queue section against the producer contract."""
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"queue section is not a regular file: {path}")
    size = path.stat().st_size
    if not 0 < size <= QUEUE_SECTION_MAX_BYTES:
        raise RuntimeError(
            f"queue section is {size} bytes; limit is {QUEUE_SECTION_MAX_BYTES} bytes"
        )
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonfinite_json,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"queue section is not strict JSON: {exc}") from exc
    queue = payload.get("queue") if isinstance(payload, dict) else None
    retention = (
        queue.get("operations_publication_retention")
        if isinstance(queue, dict)
        else None
    )
    if not isinstance(retention, dict) or retention.get("max_bytes") != (
        QUEUE_SECTION_MAX_BYTES
    ):
        raise RuntimeError("queue section does not declare the exact producer byte cap")
    if not isinstance(retention.get("complete_relative_to_source"), bool):
        raise RuntimeError("queue section does not declare publication completeness")
    return size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT))
    parser.add_argument("--output")
    parser.add_argument("--history-output")
    parser.add_argument("--validate-output")
    args = parser.parse_args(argv)

    if args.validate_output:
        size = validate_queue_section_file(Path(args.validate_output))
        print(
            f"Validated {args.validate_output} ({size} bytes; "
            f"limit {QUEUE_SECTION_MAX_BYTES} bytes)"
        )
        return 0

    data_dir = Path(args.input_dir)
    output = (
        Path(args.output)
        if args.output
        else data_dir / OPERATIONS_BUNDLE_DIR_NAME / "queue.json"
    )
    section = build_queue_section(data_dir)
    encoded = encode_queue_section(section)
    if len(encoded) > QUEUE_SECTION_MAX_BYTES:
        raise RuntimeError(
            "queue-section serializer exceeded its byte budget; preserving the "
            f"last-known-good file: {len(encoded)} > {QUEUE_SECTION_MAX_BYTES} bytes"
        )
    atomic_write_bytes(output, encoded)
    history_output = (
        Path(args.history_output)
        if args.history_output
        else data_dir / QUEUE_HISTORY_CHART_NAME
    )
    history = load_queue_history(data_dir / SOURCE_FILES["queue_timeseries"])
    write_queue_history_chart(
        history_output,
        [_filter_queue_snapshot(row) for row in history],
        history[-1].get("ts") if history else None,
    )
    print(f"Wrote {output} ({output.stat().st_size} bytes)")
    print(f"Wrote {history_output} ({history_output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
