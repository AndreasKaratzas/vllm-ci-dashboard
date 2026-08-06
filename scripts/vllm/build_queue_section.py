#!/usr/bin/env python3
"""Build only the lazy Operations v2 queue section.

The frequent queue collector must be able to publish fresh queue evidence
without rebuilding every unrelated dashboard section.  This script reads only
``queue_timeseries.jsonl`` and ``queue_jobs.json`` and writes the same compact
``operations_v2/queue.json`` payload as the full operations snapshot builder.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the local ``scripts/vllm`` package win when executed as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

DEFAULT_INPUT = Path(__file__).resolve().parent.parent.parent / "data" / "vllm" / "ci"


def build_queue_section(data_dir: Path) -> dict:
    history_path = data_dir / SOURCE_FILES["queue_timeseries"]
    history = load_queue_history(history_path)
    snapshot = _filter_queue_snapshot(load_latest_queue_snapshot(history_path))
    queue_jobs = _load_json(data_dir / SOURCE_FILES["queue_jobs"]) or {}
    return {"queue": _compact_queue(_queue(snapshot, queue_jobs, history))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT))
    parser.add_argument("--output")
    parser.add_argument("--history-output")
    args = parser.parse_args(argv)

    data_dir = Path(args.input_dir)
    output = (
        Path(args.output)
        if args.output
        else data_dir / OPERATIONS_BUNDLE_DIR_NAME / "queue.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_queue_section(data_dir), separators=(",", ":")) + "\n")
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
