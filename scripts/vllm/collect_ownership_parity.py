#!/usr/bin/env python3
"""Collect ownership parity from the exact vLLM commit used by the AMD matrix."""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm import config_parity
from vllm.bounded_json import pretty_json_bytes, write_pretty_json_lkg
from vllm.dashboard_storage_budget import writer_max_bytes


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = ROOT / "data" / "vllm" / "ci"
MATRIX_FILENAME = "amd_test_matrix.json"
OUTPUT_FILENAME = "ownership_config_parity.json"
COMMIT_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")
RAW_HOST = "raw.githubusercontent.com"
REPOSITORY_PATH = ("vllm-project", "vllm")
TEST_AREAS_API = (
    "https://api.github.com/repos/vllm-project/vllm/"
    "contents/.buildkite/test_areas"
)
OWNERSHIP_CONFIG_PARITY_MAX_BYTES = writer_max_bytes("config_parity_pair") // 2
CONFIG_PARITY_ROW_COLLECTIONS = (
    "matches",
    "inline_mirror_variants",
    "additional_variants",
    "amd_only",
    "nvidia_only",
    "mirrors",
)
CONFIG_PARITY_DUPLICATE_COLLECTIONS = frozenset({"mirrors"})


def _canonical_row_key(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def _config_parity_priority(
    collection: str,
    row: dict[str, Any],
) -> tuple[int, float, str]:
    """Rank actionable definition rows ahead of informational matches."""
    collection_rank = {
        "amd_only": 6,
        "nvidia_only": 5,
        "additional_variants": 4,
        "matches": 3,
        "inline_mirror_variants": 2,
    }
    color = str(row.get("color") or "").casefold()
    color_rank = {"red": 3.0, "yellow": 2.0, "green": 1.0}.get(color, 2.0)
    try:
        similarity = float(row.get("command_similarity"))
    except (TypeError, ValueError):
        similarity = 0.0
    # Low similarity is more actionable. The canonical row encoding is a
    # stable final tie-breaker and makes compaction permutation-invariant.
    action_score = color_rank + max(0.0, 1.0 - similarity)
    return collection_rank.get(collection, 0), action_score, _canonical_row_key(row)


def bounded_config_parity_payload(
    report: dict[str, Any],
    *,
    max_bytes: int,
) -> dict[str, Any]:
    """Bound one configuration-parity report using whole logical rows.

    Refetchable mirror rows duplicate target evidence already represented in
    the parity collections, so they are removed first. If the remaining
    source still exceeds the cap, actionable gaps are retained before matched
    informational rows. Scalar source totals remain exact and every list has
    explicit original/retained/omitted accounting.
    """
    if max_bytes <= 0:
        raise ValueError("configuration parity byte budget must be positive")
    collections = {
        name: sorted(
            (
                dict(row)
                for row in report.get(name) or []
                if isinstance(row, dict)
            ),
            key=_canonical_row_key,
        )
        for name in CONFIG_PARITY_ROW_COLLECTIONS
    }
    duplicate_rows = [
        (name, index, row)
        for name in CONFIG_PARITY_ROW_COLLECTIONS
        if name in CONFIG_PARITY_DUPLICATE_COLLECTIONS
        for index, row in enumerate(collections[name])
    ]
    core_rows = sorted(
        (
            (name, index, row)
            for name in CONFIG_PARITY_ROW_COLLECTIONS
            if name not in CONFIG_PARITY_DUPLICATE_COLLECTIONS
            for index, row in enumerate(collections[name])
        ),
        key=lambda item: _config_parity_priority(item[0], item[2]),
        reverse=True,
    )

    def candidate(core_count: int, duplicate_count: int) -> dict[str, Any]:
        selected_core = {
            (name, index)
            for name, index, _row in core_rows[:core_count]
        }
        selected_duplicate = {
            (name, index)
            for name, index, _row in duplicate_rows[:duplicate_count]
        }
        published: dict[str, list[dict[str, Any]]] = {}
        for name in CONFIG_PARITY_ROW_COLLECTIONS:
            selected = (
                selected_duplicate
                if name in CONFIG_PARITY_DUPLICATE_COLLECTIONS
                else selected_core
            )
            published[name] = [
                row
                for index, row in enumerate(collections[name])
                if (name, index) in selected
            ]
        complete = all(
            len(published[name]) == len(collections[name])
            for name in CONFIG_PARITY_ROW_COLLECTIONS
        )
        result = {
            key: value
            for key, value in report.items()
            if key not in {*CONFIG_PARITY_ROW_COLLECTIONS, "publication_retention"}
        }
        result.update(published)
        result["publication_retention"] = {
            "policy": "drop_duplicate_targets_then_actionable_whole_rows_v1",
            "max_bytes": max_bytes,
            "complete_relative_to_source": complete,
            "aggregate_summary_complete": True,
            "collections": {
                name: {
                    "source": len(collections[name]),
                    "published": len(published[name]),
                    "omitted": len(collections[name]) - len(published[name]),
                    "complete_relative_to_source": (
                        len(published[name]) == len(collections[name])
                    ),
                }
                for name in CONFIG_PARITY_ROW_COLLECTIONS
            },
        }
        return result

    def fits(core_count: int, duplicate_count: int) -> bool:
        return len(pretty_json_bytes(candidate(core_count, duplicate_count))) <= max_bytes

    # The complete report is the preferred projection.
    if fits(len(core_rows), len(duplicate_rows)):
        return candidate(len(core_rows), len(duplicate_rows))

    # Duplicated mirror targets are the first eviction tier.
    low, high = 0, len(duplicate_rows)
    best_duplicate = -1
    while low <= high:
        keep = (low + high) // 2
        if fits(len(core_rows), keep):
            best_duplicate = keep
            low = keep + 1
        else:
            high = keep - 1
    if best_duplicate >= 0:
        return candidate(len(core_rows), best_duplicate)

    # Retain the largest actionable-priority prefix of all non-duplicate rows.
    low, high = 0, len(core_rows)
    best_core = -1
    while low <= high:
        keep = (low + high) // 2
        if fits(keep, 0):
            best_core = keep
            low = keep + 1
        else:
            high = keep - 1
    if best_core < 0:
        raise RuntimeError(
            "configuration parity fixed metadata exceeds its byte budget; "
            "preserving the last-known-good file"
        )

    # With the maximum core row count fixed, use any remaining space for the
    # deterministic duplicate prefix without sacrificing actionable evidence.
    low, high = 0, len(duplicate_rows)
    best_duplicate = 0
    while low <= high:
        keep = (low + high) // 2
        if fits(best_core, keep):
            best_duplicate = keep
            low = keep + 1
        else:
            high = keep - 1
    return candidate(best_core, best_duplicate)


def matrix_commit_sha(matrix: dict[str, Any]) -> str:
    """Return the exact commit SHA encoded in ``source.yaml_url``."""
    source = matrix.get("source")
    if not isinstance(source, dict):
        raise ValueError("AMD test matrix is missing source metadata")

    yaml_url = source.get("yaml_url")
    if not isinstance(yaml_url, str) or not yaml_url:
        raise ValueError("AMD test matrix is missing source.yaml_url")
    if yaml_url != yaml_url.strip():
        raise ValueError("AMD test matrix source.yaml_url contains surrounding whitespace")

    parsed = urlsplit(yaml_url)
    segments = tuple(segment for segment in parsed.path.split("/") if segment)
    if (
        parsed.scheme != "https"
        or parsed.netloc.casefold() != RAW_HOST
        or parsed.query
        or parsed.fragment
        or len(segments) != 5
        or segments[:2] != REPOSITORY_PATH
        or not COMMIT_SHA_RE.fullmatch(segments[2])
        or segments[3:] != (".buildkite", "test-amd.yaml")
    ):
        raise ValueError(
            "AMD test matrix source.yaml_url must be the exact commit-pinned "
            "vllm-project/vllm test-amd.yaml URL"
        )
    return segments[2].lower()


def _fetch_yaml(path: str, commit_sha: str) -> tuple[str, object]:
    url = f"https://{RAW_HOST}/vllm-project/vllm/{commit_sha}/{path}"
    response = requests.get(
        url,
        headers={"Accept": "text/plain"},
        timeout=30,
    )
    response.raise_for_status()
    return path, yaml.safe_load(response.text)


def load_pinned_snapshot(commit_sha: str) -> config_parity.ConfigSourceSnapshot:
    """Fetch only CI definition YAML instead of downloading the full repository."""
    response = requests.get(
        TEST_AREAS_API,
        headers=config_parity._github_headers(),
        params={"ref": commit_sha},
        timeout=30,
    )
    response.raise_for_status()
    listing = response.json()
    if not isinstance(listing, list):
        raise ValueError("GitHub test_areas response was not a directory listing")
    area_paths = sorted(
        str(row.get("path") or "")
        for row in listing
        if isinstance(row, dict)
        and row.get("type") == "file"
        and str(row.get("path") or "").startswith(".buildkite/test_areas/")
        and str(row.get("path") or "").endswith(".yaml")
    )
    if not area_paths:
        raise ValueError("Pinned vLLM commit has no test-area YAML files")
    paths = [".buildkite/test-amd.yaml", *area_paths]
    with ThreadPoolExecutor(max_workers=8) as executor:
        files = dict(executor.map(lambda path: _fetch_yaml(path, commit_sha), paths))
    if not isinstance(files.get(".buildkite/test-amd.yaml"), dict):
        raise ValueError("Pinned vLLM commit has invalid test-amd.yaml")
    return config_parity.ConfigSourceSnapshot(
        commit_sha=commit_sha,
        files=files,
        fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def collect_ownership_parity(input_dir: Path, output_dir: Path) -> Path:
    """Build and write parity pinned to the AMD matrix's source commit."""
    matrix_path = input_dir / MATRIX_FILENAME
    matrix = json.loads(matrix_path.read_text())
    if not isinstance(matrix, dict):
        raise ValueError("AMD test matrix root must be a JSON object")

    commit_sha = matrix_commit_sha(matrix)
    original_branch = config_parity.VLLM_BRANCH
    original_snapshot = config_parity._SOURCE_SNAPSHOT
    try:
        config_parity.VLLM_BRANCH = commit_sha
        config_parity._SOURCE_SNAPSHOT = load_pinned_snapshot(commit_sha)
        report = config_parity.build_config_parity()
    finally:
        config_parity.VLLM_BRANCH = original_branch
        config_parity._SOURCE_SNAPSHOT = original_snapshot
    if not isinstance(report, dict):
        raise ValueError("config parity collector returned a non-object result")

    source = report.get("source")
    reported_sha = source.get("commit_sha") if isinstance(source, dict) else None
    if reported_sha != commit_sha:
        raise ValueError(
            "config parity source commit mismatch: "
            f"expected {commit_sha}, received {reported_sha or '<missing>'}"
        )

    report = bounded_config_parity_payload(
        report,
        max_bytes=OWNERSHIP_CONFIG_PARITY_MAX_BYTES,
    )
    output_path = output_dir / OUTPUT_FILENAME
    write_pretty_json_lkg(
        output_path,
        report,
        max_bytes=OWNERSHIP_CONFIG_PARITY_MAX_BYTES,
        label="ownership configuration parity snapshot",
    )
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_DATA_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        output_path = collect_ownership_parity(args.input_dir, args.output)
    except (
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
        requests.RequestException,
        yaml.YAMLError,
    ) as exc:
        print(f"Ownership parity collection failed: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote build-pinned ownership parity to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
