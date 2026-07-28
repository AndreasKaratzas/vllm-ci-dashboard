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

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / OUTPUT_FILENAME
    output_path.write_text(json.dumps(report, indent=2) + "\n")
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
