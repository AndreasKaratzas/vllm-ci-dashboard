#!/usr/bin/env python3
"""Reject a staged dashboard-state tree that exceeds any composition envelope."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.check_git_blob_sizes import TrackedBlob, tracked_blobs  # noqa: E402
from vllm.dashboard_storage_budget import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    StorageBudget,
    StorageBudgetError,
    load_storage_budget,
)


@dataclass(frozen=True)
class StorageSummary:
    group_bytes: dict[str, int]
    unmanaged_bytes: int
    total_bytes: int
    file_count: int


def summarize(blobs: list[TrackedBlob], budget: StorageBudget) -> StorageSummary:
    group_bytes = {name: 0 for name in budget.groups}
    unmanaged_bytes = 0
    for blob in blobs:
        matches = budget.matching_groups(blob.path)
        if len(matches) > 1:
            raise StorageBudgetError(
                f"tracked path {blob.path!r} matches multiple storage groups: {matches}"
            )
        if matches:
            group_bytes[matches[0]] += blob.size
        else:
            unmanaged_bytes += blob.size
    return StorageSummary(
        group_bytes=group_bytes,
        unmanaged_bytes=unmanaged_bytes,
        total_bytes=unmanaged_bytes + sum(group_bytes.values()),
        file_count=len(blobs),
    )


def violations(summary: StorageSummary, budget: StorageBudget) -> list[str]:
    failures = []
    for name, actual in sorted(summary.group_bytes.items()):
        maximum = budget.groups[name].max_bytes
        if actual > maximum:
            failures.append(
                f"storage group {name} is {actual} bytes (max {maximum})"
            )
    if summary.unmanaged_bytes > budget.unmanaged_max_bytes:
        failures.append(
            "unmanaged dashboard-state content is "
            f"{summary.unmanaged_bytes} bytes (max {budget.unmanaged_max_bytes})"
        )
    if summary.total_bytes > budget.allocated_bytes:
        failures.append(
            f"dashboard-state content is {summary.total_bytes} bytes "
            f"(allocation ceiling {budget.allocated_bytes})"
        )
    if summary.file_count > budget.max_files:
        failures.append(
            f"dashboard-state content has {summary.file_count} files "
            f"(max {budget.max_files})"
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()
    root = args.root.resolve()
    config = args.config
    if not config.is_absolute():
        config = root / config
    try:
        budget = load_storage_budget(config)
        summary = summarize(tracked_blobs(root), budget)
        failures = violations(summary, budget)
    except (OSError, ValueError, StorageBudgetError) as exc:
        print(f"::error::Dashboard-state storage budget is invalid: {exc}")
        return 1

    for name, actual in sorted(summary.group_bytes.items()):
        print(
            f"Dashboard-state storage group {name}: {actual} bytes "
            f"(max {budget.groups[name].max_bytes})."
        )
    print(
        "Dashboard-state unmanaged content: "
        f"{summary.unmanaged_bytes} bytes (max {budget.unmanaged_max_bytes})."
    )
    print(
        "Dashboard-state file count: "
        f"{summary.file_count} files (max {budget.max_files})."
    )
    for failure in failures:
        print(f"::error::{failure}")
    if failures:
        return 1
    print(
        "Dashboard-state composition budget passed: "
        f"{summary.total_bytes} actual bytes; {budget.allocated_bytes} allocated; "
        f"{budget.available_headroom_bytes} bytes guaranteed below the "
        f"{budget.max_tree_bytes}-byte state cap."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
