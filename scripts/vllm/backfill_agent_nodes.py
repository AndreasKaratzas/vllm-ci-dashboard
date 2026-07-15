#!/usr/bin/env python3
"""Backfill the physical CI agent ``node`` field into existing test-result JSONL.

DEPRECATED: the CI agent-health view no longer joins node identity from the
nightly test-result JSONL. It is now driven by ``collect_agent_health.py``, which
reads the ``k8s:node`` agent tag directly from the build *list* endpoint across
*all* builds/branches. This script remains only for one-off enrichment of the
legacy ``node`` field and is not part of the hourly pipeline.

The per-test JSONL (``data/vllm/ci/test_results/*.jsonl``) only carries a
``node`` value for rows collected after log_parser started capturing it. To make
the CI Agent Health view useful immediately over the trailing window, this
script reads the Buildkite agent ``k8s:node`` tag for every job in the recent
builds present in the JSONL and patches it into the rows (matched by ``job_id``).

The node comes straight from ``job["agent"]["meta_data"]`` in the build-detail
JSON, so this needs only one request per build (no per-job log downloads) and
covers every AMD GPU queue (mi250/mi300/mi325/mi355). Resolved nodes overwrite
any previously stored value so the authoritative agent tag wins.

Requires ``BUILDKITE_TOKEN``. Reads Buildkite and rewrites local JSONL only.

Usage:
    python scripts/vllm/backfill_agent_nodes.py --days 8
    python scripts/vllm/backfill_agent_nodes.py --days 8 --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add scripts/ to path so the vllm package is importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.ci import config as cfg
from vllm.ci.buildkite_client import fetch_build_detail, fetch_build_jobs
from vllm.ci.log_parser import node_from_agent
from vllm.pipelines import BK_ORG as VLLM_ORG, PIPELINES as VLLM_PIPELINES

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = ROOT / "data" / "vllm" / "ci"

# JSONL filename suffix -> Buildkite pipeline key used by cfg.PIPELINES.
SUFFIX_TO_PIPELINE_KEY = {"amd": "amd", "upstream": "upstream"}


def _file_date(path: Path) -> str:
    return path.name.rsplit(".", 1)[0].split("_", 1)[0]


def _pipeline_key(path: Path) -> str | None:
    parts = path.name.rsplit(".", 1)[0].split("_", 1)
    if len(parts) != 2:
        return None
    return SUFFIX_TO_PIPELINE_KEY.get(parts[1])


def _resolve_nodes_for_build(pipeline_key: str, build_number: int) -> dict[str, str]:
    """job_id -> physical node for one build, from the agent ``k8s:node`` tags."""
    try:
        build = fetch_build_detail(pipeline_key, build_number)
    except Exception as exc:  # noqa: BLE001 - network/HTTP errors are non-fatal
        log.warning("  build #%s (%s): failed to fetch detail: %s", build_number, pipeline_key, exc)
        return {}
    nodes: dict[str, str] = {}
    for job in fetch_build_jobs(build):
        node = node_from_agent(job)
        job_id = str(job.get("id") or "")
        if job_id and node:
            nodes[job_id] = node
    return nodes


def backfill(data_dir: Path, days: int, dry_run: bool) -> int:
    results_dir = data_dir / "test_results"
    if not results_dir.is_dir():
        log.error("No test_results directory at %s", results_dir)
        return 1
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    files = sorted(
        path for path in results_dir.glob("*.jsonl")
        if _file_date(path) >= cutoff_date and _pipeline_key(path)
    )
    if not files:
        log.info("No JSONL files within the trailing %d-day window (cutoff %s).", days, cutoff_date)
        return 0

    # Every (pipeline_key, build_number) present in the in-window files. Cache
    # lookups so a build shared across files is fetched once.
    builds_needed: set[tuple[str, int]] = set()
    for path in files:
        pipeline_key = _pipeline_key(path)
        for row in _iter_rows(path):
            if row.get("build_number") and row.get("job_id"):
                builds_needed.add((pipeline_key, int(row["build_number"])))
    if not builds_needed:
        log.info("No builds referenced by the in-window JSONL rows.")
        return 0
    log.info("Reading agent node tags for %d build(s) across %d file(s)...", len(builds_needed), len(files))

    node_by_job: dict[str, str] = {}
    for pipeline_key, build_number in sorted(builds_needed):
        resolved = _resolve_nodes_for_build(pipeline_key, build_number)
        node_by_job.update(resolved)
        log.info("  build #%s (%s): %d node(s) resolved", build_number, pipeline_key, len(resolved))

    total_patched = 0
    for path in files:
        patched = _patch_file(path, node_by_job, dry_run)
        if patched:
            log.info("  %s: %s %d row(s)", path.name, "would patch" if dry_run else "patched", patched)
        total_patched += patched
    log.info("%s %d row(s) with a physical node.", "Would patch" if dry_run else "Patched", total_patched)
    return 0


def _iter_rows(path: Path):
    try:
        with path.open(encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(row, dict):
                    yield row
    except (OSError, UnicodeError) as exc:
        log.warning("  %s: unreadable (%s)", path.name, exc)


def _patch_file(path: Path, node_by_job: dict[str, str], dry_run: bool) -> int:
    """Overwrite each row's node with the authoritative agent tag when resolved."""
    out_lines: list[str] = []
    patched = 0
    changed = False
    for row in _iter_rows(path):
        job_id = str(row.get("job_id") or "")
        resolved = node_by_job.get(job_id)
        if resolved and row.get("node") != resolved:
            row["node"] = resolved
            patched += 1
            changed = True
        out_lines.append(json.dumps(row, ensure_ascii=False))
    if changed and not dry_run:
        path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return patched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=8, help="Trailing window of JSONL files to backfill")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="CI data directory")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args(argv)

    if not cfg.BK_TOKEN:
        log.error("BUILDKITE_TOKEN is not set; cannot read agent tags.")
        return 2
    cfg.configure(VLLM_ORG, VLLM_PIPELINES)
    return backfill(Path(args.data_dir), args.days, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
