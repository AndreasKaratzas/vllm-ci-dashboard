#!/usr/bin/env python3
"""Validate and publish the reviewed CUDA-to-ROCm test-group inventory.

The automatic definition matcher answers whether AMD YAML definitions can be
linked to upstream YAML definitions.  This reviewed inventory answers a
different question: which upstream logical CUDA test groups have complete
ROCm coverage on main, are intentionally unsupported, or still require
action.  Work that has not landed on main remains an action item.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.bounded_json import pretty_json_bytes, write_pretty_json_lkg  # noqa: E402
from vllm.dashboard_storage_budget import writer_max_bytes  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG = ROOT / "config" / "vllm_upstream_test_group_parity.json"
OUTPUT = ROOT / "data" / "vllm" / "ci"

SCHEMA_VERSION = 3
ALL_STATES = frozenset({"existing", "unsupported", "action"})
GAP_STATES = ALL_STATES - {"existing"}
AREA_COUNT_FIELDS = ("existing", "unsupported", "action")
ROCM_INVENTORY_MILESTONES = ("main",)
ROCM_INVENTORY_POPULATIONS = ("physical_definitions", "logical_groups")
FULL_COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
TEST_GROUP_PARITY_MAX_BYTES = writer_max_bytes("test_group_parity")


def _nonempty_string(value: Any, context: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{context} must be a non-empty string")
    return text


def _nonnegative_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{context} must be a non-negative integer")
    return value


def _positive_int(value: Any, context: str) -> int:
    number = _nonnegative_int(value, context)
    if number == 0:
        raise ValueError(f"{context} must be greater than zero")
    return number


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 1) if denominator else 0.0


def load_review(path: Path = CONFIG) -> dict[str, Any]:
    """Load and fully validate the reviewed source inventory."""
    try:
        review = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read reviewed parity config {path}: {exc}") from exc
    if not isinstance(review, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if review.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"{path} schema_version must be {SCHEMA_VERSION}"
        )

    reviewed_at = _nonempty_string(review.get("reviewed_at"), "reviewed_at")
    try:
        date.fromisoformat(reviewed_at)
    except ValueError as exc:
        raise ValueError("reviewed_at must use YYYY-MM-DD") from exc

    source = review.get("source")
    if not isinstance(source, dict):
        raise ValueError("source must be an object")
    _nonempty_string(source.get("repository"), "source.repository")
    for obsolete_field in ("pull_request", "published_pr_commit", "upstream_commit"):
        if obsolete_field in source:
            raise ValueError(
                f"source.{obsolete_field} is obsolete; parity is pinned to main"
            )
    main_commit = _nonempty_string(
        source.get("main_commit"), "source.main_commit"
    ).casefold()
    if not FULL_COMMIT_SHA_RE.fullmatch(main_commit):
        raise ValueError("source.main_commit must be a full 40-hex commit SHA")

    scope = review.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("scope must be an object")
    _positive_int(
        scope.get("upstream_physical_definitions"),
        "scope.upstream_physical_definitions",
    )
    _nonempty_string(scope.get("count_basis"), "scope.count_basis")
    for field in ("excluded", "included_but_classified"):
        values = scope.get(field)
        if not isinstance(values, list) or not values:
            raise ValueError(f"scope.{field} must be a non-empty list")
        for index, value in enumerate(values):
            _nonempty_string(value, f"scope.{field}[{index}]")

    rocm_inventory = review.get("rocm_inventory")
    if not isinstance(rocm_inventory, dict):
        raise ValueError("rocm_inventory must be an object")
    for milestone in ROCM_INVENTORY_MILESTONES:
        values = rocm_inventory.get(milestone)
        if not isinstance(values, dict):
            raise ValueError(f"rocm_inventory.{milestone} must be an object")
        counts = {
            population: _positive_int(
                values.get(population),
                f"rocm_inventory.{milestone}.{population}",
            )
            for population in ROCM_INVENTORY_POPULATIONS
        }
        if counts["physical_definitions"] < counts["logical_groups"]:
            raise ValueError(
                "ROCm inventory must satisfy physical definitions >= logical "
                f"groups at {milestone}"
            )
    unexpected_milestones = set(rocm_inventory) - {
        *ROCM_INVENTORY_MILESTONES,
        "count_basis",
    }
    if unexpected_milestones:
        raise ValueError(
            "rocm_inventory contains obsolete milestones: "
            f"{sorted(unexpected_milestones)}"
        )
    _nonempty_string(
        rocm_inventory.get("count_basis"), "rocm_inventory.count_basis"
    )

    areas = review.get("areas")
    if not isinstance(areas, list) or not areas:
        raise ValueError("areas must be a non-empty list")
    area_names: set[str] = set()
    area_counts: dict[str, dict[str, int]] = {}
    for index, raw_area in enumerate(areas):
        if not isinstance(raw_area, dict):
            raise ValueError(f"areas[{index}] must be an object")
        name = _nonempty_string(raw_area.get("area"), f"areas[{index}].area")
        if name in area_names:
            raise ValueError(f"duplicate area: {name}")
        area_names.add(name)
        counts = {
            field: _nonnegative_int(
                raw_area.get(field), f"areas[{index}].{field}"
            )
            for field in AREA_COUNT_FIELDS
        }
        total = _positive_int(raw_area.get("total"), f"areas[{index}].total")
        if sum(counts.values()) != total:
            raise ValueError(
                f"area {name} state counts sum to {sum(counts.values())}, "
                f"not total {total}"
            )
        area_counts[name] = {"total": total, **counts}

    total_groups = sum(row["total"] for row in area_counts.values())
    groups = review.get("groups")
    if not isinstance(groups, list):
        raise ValueError("groups must be a list")
    seen_ids: set[int] = set()
    detailed_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise ValueError(f"groups[{index}] must be an object")
        group_id = _positive_int(group.get("id"), f"groups[{index}].id")
        if group_id > total_groups:
            raise ValueError(
                f"groups[{index}].id {group_id} exceeds inventory total {total_groups}"
            )
        if group_id in seen_ids:
            raise ValueError(f"duplicate group id: {group_id}")
        seen_ids.add(group_id)
        area = _nonempty_string(group.get("area"), f"groups[{index}].area")
        if area not in area_counts:
            raise ValueError(f"groups[{index}] references unknown area {area}")
        state = _nonempty_string(group.get("state"), f"groups[{index}].state")
        if state not in ALL_STATES:
            raise ValueError(
                f"groups[{index}].state must be one of {sorted(ALL_STATES)}"
            )
        _nonempty_string(group.get("title"), f"groups[{index}].title")
        _nonempty_string(
            group.get("cuda_variants"), f"groups[{index}].cuda_variants"
        )
        _nonempty_string(group.get("assessment"), f"groups[{index}].assessment")
        if "proposal_stage" in group:
            raise ValueError(
                f"groups[{index}].proposal_stage is obsolete; parity tracks "
                "main only"
            )
        detailed_counts[area][state] += 1

    for area, counts in area_counts.items():
        for state in AREA_COUNT_FIELDS:
            actual = detailed_counts[area][state]
            expected = counts[state]
            if actual != expected:
                raise ValueError(
                    f"area {area} has {actual} detailed {state} groups, "
                    f"expected {expected}"
                )

    totals = Counter()
    for counts in area_counts.values():
        totals.update({field: counts[field] for field in AREA_COUNT_FIELDS})
    if len(groups) != total_groups:
        raise ValueError("groups must contain every logical inventory row exactly once")
    expected_ids = set(range(1, total_groups + 1))
    if seen_ids != expected_ids:
        missing = sorted(expected_ids - seen_ids)
        unexpected = sorted(seen_ids - expected_ids)
        raise ValueError(
            "group ids must be contiguous from 1 through the logical inventory "
            f"total (missing={missing}; unexpected={unexpected})"
        )
    applicable = total_groups - totals["unsupported"]
    if totals["existing"] + totals["action"] != applicable:
        raise ValueError(
            "applicable groups must equal existing groups plus action groups"
        )
    return review


def build_payload(
    review: dict[str, Any],
    config_path: Path = CONFIG,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the dashboard payload from a validated review."""
    areas = [dict(row) for row in review["areas"]]
    groups = sorted(
        (dict(row) for row in review["groups"]),
        key=lambda row: row["id"],
    )
    totals = Counter()
    for row in areas:
        totals.update({field: int(row[field]) for field in AREA_COUNT_FIELDS})

    upstream_logical = sum(int(row["total"]) for row in areas)
    unsupported = totals["unsupported"]
    applicable = upstream_logical - unsupported
    existing = totals["existing"]
    action = totals["action"]
    main_missing = applicable - existing

    normalized_areas = []
    for row in areas:
        normalized_areas.append({
            **row,
            "applicable": int(row["total"]) - int(row["unsupported"]),
            "complete_on_main": int(row["existing"]),
            "missing_on_main": int(row["action"]),
        })

    source = dict(review["source"])
    try:
        source["config_path"] = config_path.relative_to(ROOT).as_posix()
    except ValueError:
        source["config_path"] = str(config_path)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reviewed_at": review["reviewed_at"],
        "source": source,
        "scope": dict(review["scope"]),
        "summary": {
            "upstream_physical_definitions": int(
                review["scope"]["upstream_physical_definitions"]
            ),
            "upstream_logical_groups": upstream_logical,
            "applicable_groups": applicable,
            "main_complete_groups": existing,
            "unsupported_groups": unsupported,
            "action_groups": action,
            "main_missing_groups": main_missing,
            "main_applicable_rate_pct": _rate(existing, applicable),
        },
        "rocm_inventory": dict(review["rocm_inventory"]),
        "areas": normalized_areas,
        "groups": groups,
    }


def bounded_payload(
    payload: dict[str, Any],
    *,
    max_bytes: int = TEST_GROUP_PARITY_MAX_BYTES,
) -> dict[str, Any]:
    """Keep whole actionable inventory rows within the writer allocation.

    ``summary`` and ``areas`` remain exact for the fully reviewed inventory.
    The row index is separately accounted so omitted existing rows can never
    turn a partial publication into an apparent all-clear.
    """
    if max_bytes <= 0:
        raise ValueError("test-group parity byte budget must be positive")
    source_groups = sorted(
        (dict(row) for row in payload.get("groups") or [] if isinstance(row, dict)),
        key=lambda row: (
            int(row.get("id") or 0),
            str(row.get("title") or "").casefold(),
        ),
    )
    state_priority = {"action": 3, "unsupported": 2, "existing": 1}
    prioritized = sorted(
        source_groups,
        key=lambda row: (
            state_priority.get(str(row.get("state") or ""), 0),
            -int(row.get("id") or 0),
            str(row.get("title") or "").casefold(),
        ),
        reverse=True,
    )
    source_by_state = Counter(str(row.get("state") or "unknown") for row in source_groups)

    def candidate(count: int) -> dict[str, Any]:
        selected_ids = {int(row.get("id") or 0) for row in prioritized[:count]}
        published = [
            row for row in source_groups if int(row.get("id") or 0) in selected_ids
        ]
        published_by_state = Counter(
            str(row.get("state") or "unknown") for row in published
        )
        complete = len(published) == len(source_groups)
        result = {
            key: value
            for key, value in payload.items()
            if key not in {"groups", "publication_retention"}
        }
        result["groups"] = published
        result["publication_retention"] = {
            "policy": "action_then_unsupported_then_existing_whole_rows_v1",
            "max_bytes": max_bytes,
            "complete_relative_to_source": complete,
            "aggregate_summary_complete": True,
            "area_rollups_complete": True,
            "groups": {
                "source": len(source_groups),
                "published": len(published),
                "omitted": len(source_groups) - len(published),
                "complete_relative_to_source": complete,
            },
            "by_state": {
                state: {
                    "source": source_by_state[state],
                    "published": published_by_state[state],
                    "omitted": source_by_state[state] - published_by_state[state],
                }
                for state in sorted(source_by_state)
            },
        }
        return result

    low, high = 0, len(source_groups)
    best: dict[str, Any] | None = None
    while low <= high:
        keep = (low + high) // 2
        attempt = candidate(keep)
        if len(pretty_json_bytes(attempt)) <= max_bytes:
            best = attempt
            low = keep + 1
        else:
            high = keep - 1
    if best is None:
        raise RuntimeError(
            "test-group parity fixed metadata exceeds its byte budget; preserving "
            "the last-known-good file"
        )
    return best


def publish(
    config_path: Path = CONFIG,
    output_dir: Path = OUTPUT,
    *,
    generated_at: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Validate the review and write ``test_group_parity.json``."""
    review = load_review(config_path)
    payload = bounded_payload(
        build_payload(
            review,
            config_path,
            generated_at=generated_at,
        ),
        max_bytes=TEST_GROUP_PARITY_MAX_BYTES,
    )
    output_path = output_dir / "test_group_parity.json"
    write_pretty_json_lkg(
        output_path,
        payload,
        max_bytes=TEST_GROUP_PARITY_MAX_BYTES,
        label="test-group parity snapshot",
    )
    return output_path, payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish reviewed upstream CUDA-to-ROCm test-group parity"
    )
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--generated-at", type=str, default=None)
    args = parser.parse_args()

    output_path, payload = publish(
        args.config,
        args.output,
        generated_at=args.generated_at,
    )
    summary = payload["summary"]
    print(
        f"Wrote {output_path} with {summary['upstream_logical_groups']} "
        f"upstream groups and {summary['action_groups']} action groups"
    )


if __name__ == "__main__":
    main()
