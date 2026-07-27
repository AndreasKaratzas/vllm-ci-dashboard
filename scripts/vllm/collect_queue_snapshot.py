#!/usr/bin/env python3
"""Buildkite queue snapshot collector for dashboard queue monitoring.

Appends one JSON line per snapshot to ``data/vllm/ci/queue_timeseries.jsonl``.

The collector prefers Buildkite's queue-native cluster metrics for queue
counts and wait-time percentiles. Active jobs are still collected for job
detail, workload splits, zombie filtering, and as a fallback when queue-native
metrics are unavailable.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# Add scripts/ to sys.path so the ``vllm`` package resolves when this file is
# executed as ``python scripts/vllm/collect_queue_snapshot.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.constants import (  # noqa: E402
    BK_API_BASE,
    BK_CLUSTER_UUID,
    BK_GRAPHQL_URL,
    BK_ORG,
    QUEUE_HISTORY_RETENTION_DAYS,
    QUEUE_ZOMBIE_THRESHOLD_MIN,
    TRACKED_QUEUES,
    is_amd_queue,
    is_excluded_queue,
    queue_history_reset_datetime,
)
from vllm.ci.utils import classify_workload, parse_iso, percentile, queue_from_rules  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

OUTPUT = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "vllm"
    / "ci"
    / "queue_timeseries.jsonl"
)
HISTORY_REPO_PATH = "data/vllm/ci/queue_timeseries.jsonl"

# Buildkite URL rewrite: the jobs endpoint returns hash-anchored URLs that
# 404 in the step canvas; re-point them so dashboard links land on the output tab.
_JOB_URL_REWRITE = re.compile(r"^(https://buildkite\.com/vllm/[a-z\-]+/builds/\d+)#([0-9a-f\-]+)$")

GRAPHQL_QUEUE_METRICS_Q = """
query QueueMetrics($org: ID!, $cluster: ID!, $first: Int!, $after: String) {
  organization(slug: $org) {
    cluster(id: $cluster) {
      queues(first: $first, after: $after) {
        edges {
          node {
            id
            key
            uuid
            dispatchPaused
            metrics {
              timestamp
              connectedAgentsCount
              waitingJobsCount
              runningJobsCount
              waitTimeSec {
                min
                p50
                p95
                max
              }
            }
          }
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  }
}
"""

GRAPHQL_ACTIVE_JOBS_Q = """
query ActiveJobs($org: ID!, $states: [JobStates!], $first: Int!, $after: String) {
  organization(slug: $org) {
    jobs(
      first: $first,
      after: $after,
      clustered: true,
      type: [COMMAND],
      state: $states
    ) {
      edges {
        node {
          ... on JobTypeCommand {
            uuid
            state
            label
            runnableAt
            scheduledAt
            createdAt
            startedAt
            agentQueryRules
            clusterQueue {
              key
            }
            build {
              number
              branch
              commit
              url
            }
            pipeline {
              slug
            }
          }
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""

GRAPHQL_QUEUE_JOBS_Q = """
query QueueJobs($org: ID!, $queue: [ID!]!, $states: [JobStates!], $first: Int!, $after: String) {
  organization(slug: $org) {
    jobs(
      first: $first,
      after: $after,
      clusterQueue: $queue,
      type: [COMMAND],
      state: $states
    ) {
      edges {
        node {
          ... on JobTypeCommand {
            uuid
            state
            label
            runnableAt
            scheduledAt
            createdAt
            startedAt
            agentQueryRules
            clusterQueue {
              key
            }
            build {
              number
              branch
              commit
              url
            }
            pipeline {
              slug
            }
          }
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""

GRAPHQL_PAGE_SIZE = 100
GRAPHQL_WAITING_STATES = frozenset({"SCHEDULED"})
GRAPHQL_RUNNING_STATES = frozenset({"ASSIGNED", "ACCEPTED", "RUNNING", "CANCELING", "TIMING_OUT"})
GRAPHQL_ACTIVE_STATES = tuple(sorted(GRAPHQL_WAITING_STATES | GRAPHQL_RUNNING_STATES))

# Legacy REST build scan states. These are intentionally aligned with
# Buildkite's queue metrics docs rather than the older dashboard behavior:
# only ``scheduled`` jobs are "waiting", while assigned/accepted jobs count
# as already dispatched / running. Concurrency-limited jobs are excluded
# because they are not part of queue-page waiting-job metrics.
LEGACY_WAITING_STATES = frozenset({"scheduled"})
LEGACY_RUNNING_STATES = frozenset({"assigned", "accepted", "running", "canceling", "timing_out"})


def bk_get(path: str, token: str, params: dict | None = None):
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{BK_API_BASE}{path}", headers=headers, params=params, timeout=30)
    if resp.status_code == 429:
        log.warning("Rate limited on %s", path)
        return []
    resp.raise_for_status()
    return resp.json()


def bk_get_paginated(path: str, token: str, params: dict | None = None, max_pages: int = 5):
    """Fetch all pages from a Buildkite REST API endpoint."""
    params = dict(params or {})
    params.setdefault("per_page", 100)
    all_items: list = []
    for page in range(1, max_pages + 1):
        params["page"] = page
        items = bk_get(path, token, params)
        if not isinstance(items, list) or not items:
            break
        all_items.extend(items)
        if len(items) < params["per_page"]:
            break
    return all_items


def bk_graphql(query: str, token: str, variables: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    resp = requests.post(
        BK_GRAPHQL_URL,
        headers=headers,
        json={"query": query, "variables": variables or {}},
        timeout=30,
    )
    if resp.status_code == 429:
        raise RuntimeError("Buildkite GraphQL rate limited")
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError(
            f"Buildkite GraphQL error: {payload['errors'][0].get('message', 'unknown')}"
        )
    return payload.get("data") or {}


def _rewrite_job_url(web_url: str) -> str:
    m = _JOB_URL_REWRITE.match(web_url or "")
    if m:
        return f"{m.group(1)}/steps/canvas?jid={m.group(2)}&tab=output"
    return web_url


def _queue_web_url(queue_uuid: str | None) -> str:
    if not queue_uuid:
        return ""
    return f"https://buildkite.com/organizations/{BK_ORG}/clusters/{BK_CLUSTER_UUID}/queues/{queue_uuid}"


def _queue_row() -> dict:
    return {
        "waiting": 0,
        "running": 0,
        "scheduled": 0,
        "total": 0,
        "connected_agents": None,
        "connected_agents_source": None,
        "zombie_waiting": 0,
        "zombie_running": 0,
        "wait_times": [],
        "count_source": "active_job_scan",
    }


def _history_cutoff(now: datetime) -> datetime:
    retention_cutoff = now - timedelta(days=QUEUE_HISTORY_RETENTION_DAYS)
    return max(retention_cutoff, queue_history_reset_datetime())


def _queue_row_has_current_schema(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    return (
        isinstance(row.get("official_wait"), dict)
        and isinstance(row.get("sample_wait"), dict)
        and isinstance(row.get("current_wait"), dict)
        and "p50_wait_source" in row
        and "p95_wait_source" in row
        and "p99_wait_source" in row
    )


def _snapshot_has_current_schema(snapshot: dict) -> bool:
    if not isinstance(snapshot, dict):
        return False
    sources = snapshot.get("sources")
    if not isinstance(sources, dict) or not isinstance(sources.get("wait_fields"), dict):
        return False
    queues = snapshot.get("queues")
    if not isinstance(queues, dict):
        return False
    for row in queues.values():
        if not _queue_row_has_current_schema(row):
            return False
    return True


_SAMPLE_WAIT_METRICS = ("p50", "p75", "p90", "p95", "p99", "max", "avg")


def _as_count(value, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _as_optional_count(value) -> int | None:
    if value is None:
        return None
    return _as_count(value)


def _as_optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _empty_official_wait() -> dict:
    return {"p50": None, "p95": None, "max": None}


def _empty_sample_wait(*, available: bool, count: int | None) -> dict:
    return {
        "available": available,
        "count": count,
        **{metric: None for metric in _SAMPLE_WAIT_METRICS},
    }


def _apply_wait_contract(row: dict, official_wait: dict, sample_wait: dict) -> dict:
    """Attach typed wait fields and their provenance to one queue row."""
    official = {
        metric: _as_optional_float(official_wait.get(metric)) for metric in ("p50", "p95", "max")
    }
    sample_count = _as_optional_count(sample_wait.get("count"))
    sample_available = bool(sample_wait.get("available"))
    sampled = _empty_sample_wait(available=sample_available, count=sample_count)
    if sample_available and sample_count:
        for metric in _SAMPLE_WAIT_METRICS:
            sampled[metric] = _as_optional_float(sample_wait.get(metric))
    if sample_wait.get("source"):
        sampled["source"] = sample_wait["source"]

    row["official_wait"] = official
    row["sample_wait"] = sampled
    row["wait_sample_count"] = sampled["count"]

    for metric in ("p50", "p95"):
        if official[metric] is not None:
            value, source = official[metric], "official_wait"
        elif sampled[metric] is not None:
            value, source = sampled[metric], "sample_wait"
        else:
            value, source = None, None
        row[f"{metric}_wait"] = value
        row[f"{metric}_wait_source"] = source

    row["p99_wait"] = sampled["p99"]
    row["p99_wait_source"] = "sample_wait" if sampled["p99"] is not None else None
    row["current_wait"] = {
        metric: {
            "value": row[f"{metric}_wait"],
            "source": row[f"{metric}_wait_source"],
        }
        for metric in ("p50", "p95", "p99")
    }

    for metric in ("p75", "p90", "avg"):
        row[f"{metric}_wait"] = sampled[metric]
        row[f"{metric}_wait_source"] = "sample_wait" if sampled[metric] is not None else None
    official_max = official["max"]
    sampled_max = sampled["max"]
    if sampled_max is not None and (official_max is None or sampled_max > official_max):
        row["max_wait"] = sampled_max
        row["max_wait_source"] = "sample_wait"
    else:
        row["max_wait"] = official_max
        row["max_wait_source"] = "official_wait" if official_max is not None else None

    row["wait_source"] = {
        "official_wait": "cluster_metrics",
        "sample_wait": "scheduled_jobs",
    }.get(row["p95_wait_source"], "none")
    return row


def _normalize_workload_splits(row: dict, workload_source: str) -> dict:
    """Validate job-scan workload splits against authoritative queue totals.

    Queue-native metrics and active-job scans are separate observations and can
    be captured a few seconds apart. A split that exceeds its queue total is
    therefore evidence of timing drift, not permission to increase or scale the
    queue count. Preserve that evidence in provenance while making the split
    unavailable to workload-history consumers.
    """
    for split_key, total_key in (
        ("waiting_by_workload", "waiting"),
        ("running_by_workload", "running"),
    ):
        provenance_key = f"{split_key}_provenance"
        existing_provenance = row.get(provenance_key)
        split = row.get(split_key)
        if split is None:
            if isinstance(existing_provenance, dict):
                row[provenance_key] = dict(existing_provenance)
            continue

        source = str(
            row.get(f"{split_key}_source")
            or (existing_provenance.get("source") if isinstance(existing_provenance, dict) else "")
            or workload_source
            or "active_job_scan"
        )
        if not isinstance(split, dict):
            row[split_key] = None
            row[provenance_key] = {
                "available": False,
                "status": "invalid",
                "source": source,
                "reason": "workload_split_is_not_an_object",
                "queue_total": row[total_key],
            }
            continue

        normalized_split: dict[str, int] = {}
        invalid_value = False
        for workload, value in split.items():
            try:
                count = int(value)
            except (TypeError, ValueError):
                invalid_value = True
                break
            if count < 0:
                invalid_value = True
                break
            normalized_split[str(workload)] = count

        if invalid_value:
            row[split_key] = None
            row[provenance_key] = {
                "available": False,
                "status": "invalid",
                "source": source,
                "reason": "workload_split_contains_invalid_count",
                "queue_total": row[total_key],
                "observed_split": split,
            }
            continue

        split_total = sum(normalized_split.values())
        queue_total = row[total_key]
        if split_total > queue_total:
            row[split_key] = None
            row[provenance_key] = {
                "available": False,
                "status": "inconsistent",
                "source": source,
                "reason": "observed_split_exceeds_queue_total",
                "queue_total": queue_total,
                "observed_split_total": split_total,
                "observed_split": normalized_split,
            }
            continue

        row[split_key] = normalized_split
        row[provenance_key] = {
            "available": True,
            "status": "complete" if split_total == queue_total else "partial",
            "source": source,
            "queue_total": queue_total,
            "observed_split_total": split_total,
        }
    return row


def _normalize_queue_row(
    row: dict,
    snapshot_count_source: str,
    snapshot_active_jobs_source: str,
    *,
    legacy: bool,
) -> dict:
    source_row = row if isinstance(row, dict) else {}
    normalized = dict(source_row)
    normalized.pop("wait_times", None)
    normalized["waiting"] = _as_count(source_row.get("waiting"))
    normalized["running"] = _as_count(source_row.get("running"))
    normalized["scheduled"] = normalized["waiting"]
    normalized["total"] = normalized["waiting"] + normalized["running"]
    normalized["zombie_waiting"] = _as_count(source_row.get("zombie_waiting"))
    normalized["zombie_running"] = _as_count(source_row.get("zombie_running"))

    original_count_source = str(
        source_row.get("count_source") or snapshot_count_source or "unknown"
    )
    if legacy:
        normalized["count_source"] = "historical_counts"
        normalized["count_provenance"] = {
            "kind": "legacy_snapshot",
            "original_source": original_count_source,
            "preserved_fields": ["waiting", "running"],
        }
        normalized["connected_agents"] = None
        normalized["connected_agents_source"] = None
        official_wait = _empty_official_wait()
        sample_count = _as_count(source_row.get("wait_sample_count"))
        sample_wait = _empty_sample_wait(
            available=sample_count > 0,
            count=sample_count,
        )
        if sample_count > 0:
            sample_wait.update(
                {
                    metric: _as_optional_float(source_row.get(f"{metric}_wait"))
                    for metric in _SAMPLE_WAIT_METRICS
                }
            )
            sample_wait["source"] = "historical_scheduled_job_sample"
        normalized["official_wait_source"] = None
        normalized["sample_wait_source"] = (
            "historical_scheduled_job_sample" if sample_count > 0 else None
        )
    else:
        count_source = original_count_source
        if count_source == "active_jobs":
            count_source = "active_job_scan"
        normalized["count_source"] = count_source
        agent_source = source_row.get("connected_agents_source")
        if (
            not agent_source
            and count_source == "cluster_metrics"
            and "connected_agents" in source_row
            and source_row.get("connected_agents") is not None
        ):
            agent_source = "queue_native_metrics"
        normalized["connected_agents_source"] = agent_source or None
        normalized["connected_agents"] = (
            _as_count(source_row.get("connected_agents")) if agent_source else None
        )
        official_wait = source_row.get("official_wait") or _empty_official_wait()
        sample_wait = source_row.get("sample_wait") or _empty_sample_wait(
            available=False,
            count=None,
        )
        has_official = any(
            _as_optional_float(official_wait.get(metric)) is not None
            for metric in ("p50", "p95", "max")
        )
        normalized["official_wait_source"] = source_row.get("official_wait_source") or (
            "queue_native_metrics" if has_official else None
        )
        normalized["sample_wait_source"] = (
            source_row.get("sample_wait_source")
            or sample_wait.get("source")
            or ("active_job_scan" if sample_wait.get("available") else None)
        )

    normalized = _apply_wait_contract(normalized, official_wait, sample_wait)
    return _normalize_workload_splits(normalized, snapshot_active_jobs_source)


def _scope_totals(queues: dict[str, dict]) -> dict:
    count_sources = sorted({str(row.get("count_source") or "unknown") for row in queues.values()})
    if not count_sources:
        source = "unavailable"
    elif len(count_sources) == 1:
        source = count_sources[0]
    else:
        source = "mixed"
    return {
        "waiting": sum(row["waiting"] for row in queues.values()),
        "running": sum(row["running"] for row in queues.values()),
        "count_source": source,
        "count_sources": count_sources,
        "queue_count": len(queues),
    }


def _wait_field_descriptions() -> dict:
    return {
        "official_wait": (
            "Buildkite queue-native waitTimeSec converted to minutes; contains only p50, p95, and max."
        ),
        "sample_wait": (
            "Exact statistics in minutes from fetched, currently SCHEDULED, non-zombie jobs; "
            "available records whether that queue's jobs were fetched, and count is null when they were not."
        ),
        "current_wait": "Displayed p50, p95, and p99 values paired with their per-field source labels.",
        "p50_wait": "official_wait.p50 when available, otherwise sample_wait.p50, otherwise null.",
        "p95_wait": "official_wait.p95 when available, otherwise sample_wait.p95, otherwise null.",
        "p99_wait": "sample_wait.p99 when sampled jobs are available, otherwise null.",
        "p75_wait_p90_wait_avg_wait": "Sample-only compatibility fields; null without sampled jobs.",
        "max_wait": "Greater reported value of official_wait.max and sample_wait.max, otherwise null.",
        "field_source_labels": (
            "Each root wait field has a matching *_wait_source value of official_wait, sample_wait, or null."
        ),
    }


def normalize_history_snapshot(snapshot: dict) -> dict | None:
    """Migrate one queue snapshot without inventing unavailable measurements."""
    if not isinstance(snapshot, dict) or parse_iso(snapshot.get("ts") or "") is None:
        return None
    queues = snapshot.get("queues")
    if not isinstance(queues, dict):
        return None

    original_sources = snapshot.get("sources") if isinstance(snapshot.get("sources"), dict) else {}
    history_provenance = (
        original_sources.get("history_provenance")
        if isinstance(original_sources.get("history_provenance"), dict)
        else {}
    )
    already_migrated = history_provenance.get("migration") == "legacy_queue_snapshot_v1_to_v2"
    legacy = not _snapshot_has_current_schema(snapshot)
    historical = legacy or already_migrated
    snapshot_count_source = str(original_sources.get("counts") or "unknown")
    snapshot_active_jobs_source = str(original_sources.get("active_jobs") or "active_job_scan")
    normalized_queues = {
        queue: _normalize_queue_row(
            row,
            snapshot_count_source,
            snapshot_active_jobs_source,
            legacy=legacy,
        )
        for queue, row in sorted(queues.items())
        if not is_excluded_queue(queue)
    }

    normalized = dict(snapshot)
    normalized["schema_version"] = 2
    normalized["queues"] = normalized_queues
    normalized["total_waiting"] = sum(row["waiting"] for row in normalized_queues.values())
    normalized["total_running"] = sum(row["running"] for row in normalized_queues.values())
    normalized["total_zombie_waiting"] = sum(
        row["zombie_waiting"] for row in normalized_queues.values()
    )
    normalized["total_zombie_running"] = sum(
        row["zombie_running"] for row in normalized_queues.values()
    )
    normalized["scope_totals"] = {
        "all": _scope_totals(normalized_queues),
        "amd": _scope_totals(
            {queue: row for queue, row in normalized_queues.items() if is_amd_queue(queue)}
        ),
    }

    sources = dict(original_sources)
    sources["wait_fields"] = _wait_field_descriptions()
    sources["workload_split_fields"] = {
        "source": "Fetched active jobs, independent of queue-native metric timing.",
        "rule": (
            "Retain observed workload counts only when their sum does not exceed "
            "the authoritative queue total. Partial splits remain partial; no "
            "remainder is assigned. Over-limit splits are null and their raw "
            "evidence is preserved in the matching *_provenance field."
        ),
    }
    sources["history_reset_ts"] = queue_history_reset_datetime().strftime("%Y-%m-%dT%H:%M:%SZ")
    sources["zombie_threshold_min"] = QUEUE_ZOMBIE_THRESHOLD_MIN
    if legacy:
        original_count_sources = sorted(
            {
                row.get("count_provenance", {}).get("original_source", "unknown")
                for row in normalized_queues.values()
            }
        )
        has_samples = any(
            (row.get("sample_wait") or {}).get("count", 0) > 0 for row in normalized_queues.values()
        )
        sources.update(
            {
                "counts": "historical_counts",
                "agents": "unavailable",
                "official_wait": "unavailable",
                "sampled_wait": (
                    "historical_scheduled_job_sample" if has_samples else "unavailable"
                ),
                "waits": "sampled_historical_jobs" if has_samples else "none",
                "history_provenance": {
                    "migration": "legacy_queue_snapshot_v1_to_v2",
                    "counts": "Preserved running/waiting counts only.",
                    "original_count_sources": original_count_sources,
                    "agents": "Unavailable in the migrated contract.",
                    "official_wait": "Unavailable; legacy zero/default values were not retained.",
                    "sampled_wait": "Retained only where wait_sample_count was greater than zero.",
                },
            }
        )
    elif historical:
        has_samples = any(
            (row.get("sample_wait") or {}).get("count", 0) > 0 for row in normalized_queues.values()
        )
        sources.update(
            {
                "counts": "historical_counts",
                "agents": "unavailable",
                "official_wait": "unavailable",
                "sampled_wait": (
                    "historical_scheduled_job_sample" if has_samples else "unavailable"
                ),
                "waits": "sampled_historical_jobs" if has_samples else "none",
            }
        )
    else:
        has_agents = any(row.get("connected_agents_source") for row in normalized_queues.values())
        has_official = any(row.get("official_wait_source") for row in normalized_queues.values())
        has_sample_scan = any(row.get("sample_wait_source") for row in normalized_queues.values())
        sources["agents"] = "queue_native_metrics" if has_agents else "unavailable"
        sources["official_wait"] = "queue_native_metrics" if has_official else "unavailable"
        sources["sampled_wait"] = (
            str(sources.get("active_jobs") or "active_job_scan")
            if has_sample_scan
            else "unavailable"
        )
    normalized["sources"] = sources
    return normalized


def _read_history_text(text: str) -> tuple[int, list[dict]]:
    total = 0
    snapshots: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        total += 1
        try:
            snapshot = json.loads(line)
        except json.JSONDecodeError:
            continue
        normalized = normalize_history_snapshot(snapshot)
        if normalized is not None:
            snapshots.append(normalized)
    return total, snapshots


def _read_history_file(path: Path) -> tuple[int, list[dict]]:
    if not path.exists():
        return 0, []
    return _read_history_text(path.read_text())


def normalize_history_rows(rows: list[dict]) -> list[dict]:
    """Normalize, de-duplicate by timestamp, and sort snapshots deterministically."""
    by_timestamp: dict[str, dict] = {}
    for snapshot in rows:
        normalized = normalize_history_snapshot(snapshot)
        if normalized is not None:
            by_timestamp[normalized["ts"]] = normalized
    return [by_timestamp[ts] for ts in sorted(by_timestamp)]


def write_history_file(path: Path, rows: list[dict]) -> None:
    normalized = normalize_history_rows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in normalized
    )
    path.write_text(text)


def merge_history_rows(path: Path, incoming_rows: list[dict]) -> tuple[int, int]:
    """Merge incoming history with local rows; local rows win equal timestamps."""
    _, existing_rows = _read_history_file(path)
    merged = normalize_history_rows([*incoming_rows, *existing_rows])
    write_history_file(path, merged)
    return len(incoming_rows), len(merged)


def merge_history_from_git_ref(path: Path, git_ref: str) -> tuple[int, int]:
    """Merge queue history from a git ref without line-count replacement."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    result = subprocess.run(
        ["git", "show", f"{git_ref}:{HISTORY_REPO_PATH}"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        log.warning("No queue history available at %s", git_ref)
        _, existing = _read_history_file(path)
        return 0, len(existing)
    if any(marker in result.stdout for marker in ("<<<<<<<", "=======", ">>>>>>>")):
        log.warning("Queue history at %s contains conflict markers; ignoring it", git_ref)
        _, existing = _read_history_file(path)
        return 0, len(existing)

    incoming_total, incoming_rows = _read_history_text(result.stdout)
    incoming_count, merged_count = merge_history_rows(path, incoming_rows)
    log.info(
        "Merged queue history from %s: %d parsed of %d lines, %d total rows",
        git_ref,
        incoming_count,
        incoming_total,
        merged_count,
    )
    return incoming_count, merged_count


def prune_history_file(path: Path, now: datetime | None = None) -> tuple[int, int]:
    """Migrate history in place, then drop only pre-reset or stale snapshots."""
    total, snapshots = _read_history_file(path)
    if total == 0 and not path.exists():
        return 0, 0

    cutoff = _history_cutoff(now or datetime.now(timezone.utc))
    kept = [snapshot for snapshot in snapshots if parse_iso(snapshot["ts"]) >= cutoff]
    write_history_file(path, kept)
    return total, len(normalize_history_rows(kept))


def _wait_summary(times: list[float]) -> dict:
    """Return exact observed-sample statistics in minutes."""
    if not times:
        return {
            "p50": None,
            "p75": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "max": None,
            "avg": None,
        }
    ordered = sorted(times)
    return {
        "p50": round(percentile(ordered, 50), 1),
        "p75": round(percentile(ordered, 75), 1),
        "p90": round(percentile(ordered, 90), 1),
        "p95": round(percentile(ordered, 95), 1),
        "p99": round(percentile(ordered, 99), 1),
        "max": round(max(ordered), 1),
        "avg": round(sum(ordered) / len(ordered), 1),
    }


def _minutes_from_seconds(value) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value) / 60.0, 1)
    except (TypeError, ValueError):
        return None


def _wait_summary_from_queue_metrics(wait_time_sec: dict | None) -> dict | None:
    """Return only wait statistics Buildkite reports natively."""
    if not isinstance(wait_time_sec, dict):
        return None
    p50 = _minutes_from_seconds(wait_time_sec.get("p50"))
    p95 = _minutes_from_seconds(wait_time_sec.get("p95"))
    max_wait = _minutes_from_seconds(wait_time_sec.get("max"))
    if p50 is None and p95 is None and max_wait is None:
        return None

    return {
        "p50": p50,
        "p95": p95,
        "max": max_wait,
    }


def _make_canvas_job_url(build_url: str, job_uuid: str, fallback_url: str = "") -> str:
    if build_url and job_uuid:
        return f"{build_url}/steps/canvas?jid={job_uuid}&tab=output"
    return _rewrite_job_url(fallback_url)


def _wait_minutes(
    now: datetime, runnable_at: str | None, scheduled_at: str | None, created_at: str | None
) -> float:
    anchor = parse_iso(runnable_at) or parse_iso(scheduled_at) or parse_iso(created_at)
    if anchor is None:
        return 0.0
    return (now - anchor).total_seconds() / 60


def _started_wait_minutes(
    runnable_at: str | None,
    scheduled_at: str | None,
    created_at: str | None,
    started_at: str | None,
) -> float | None:
    anchor = parse_iso(runnable_at) or parse_iso(scheduled_at) or parse_iso(created_at)
    started = parse_iso(started_at)
    if anchor is None or started is None:
        return None
    return round((started - anchor).total_seconds() / 60, 1)


def _run_minutes(now: datetime, started_at: str | None) -> float | None:
    started = parse_iso(started_at)
    if started is None:
        return None
    return round((now - started).total_seconds() / 60, 1)


def fetch_cluster_queue_metrics(token: str) -> dict[str, dict]:
    """Fetch queue-native counts from Buildkite cluster metrics."""
    metrics: dict[str, dict] = {}
    after = None
    while True:
        data = bk_graphql(
            GRAPHQL_QUEUE_METRICS_Q,
            token,
            {"org": BK_ORG, "cluster": BK_CLUSTER_UUID, "first": GRAPHQL_PAGE_SIZE, "after": after},
        )
        cluster = (data.get("organization") or {}).get("cluster") or {}
        queues = cluster.get("queues") or {}
        for edge in queues.get("edges") or []:
            node = edge.get("node") or {}
            key = node.get("key") or ""
            if not key or is_excluded_queue(key):
                continue
            latest = node.get("metrics") or {}
            waiting_count = latest.get("waitingJobsCount")
            running_count = latest.get("runningJobsCount")
            connected_agents = latest.get("connectedAgentsCount")
            metrics[key] = {
                "graphql_id": node.get("id") or "",
                "counts_available": waiting_count is not None and running_count is not None,
                "waiting": _as_count(waiting_count),
                "running": _as_count(running_count),
                "connected_agents": (
                    _as_count(connected_agents) if connected_agents is not None else None
                ),
                "official_wait": _wait_summary_from_queue_metrics(latest.get("waitTimeSec")),
                "metrics_ts": latest.get("timestamp") or "",
                "queue_url": _queue_web_url(node.get("uuid")),
                "dispatch_paused": bool(node.get("dispatchPaused")),
            }
        page = queues.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return metrics
        after = page.get("endCursor")


def _graphql_job_record(node: dict, fallback_queue: str = "") -> dict | None:
    state = node.get("state") or ""
    queue = (
        ((node.get("clusterQueue") or {}).get("key"))
        or fallback_queue
        or queue_from_rules(node.get("agentQueryRules"))
    )
    if not queue or is_excluded_queue(queue):
        return None
    build = node.get("build") or {}
    pipeline = node.get("pipeline") or {}
    return {
        "queue": queue,
        "state": state,
        "name": node.get("label") or "",
        "job_uuid": node.get("uuid") or "",
        "build_url": build.get("url") or "",
        "pipeline": pipeline.get("slug") or "",
        "build": build.get("number") or 0,
        "branch": build.get("branch") or "",
        "commit": (build.get("commit") or "")[:12],
        "workload": classify_workload(pipeline.get("slug") or "", build.get("branch") or "", queue),
        "fork_url": "",
        "source": "",
        "runnable_at": node.get("runnableAt"),
        "scheduled_at": node.get("scheduledAt"),
        "created_at": node.get("createdAt"),
        "started_at": node.get("startedAt"),
    }


def _fetch_graphql_jobs(
    token: str,
    *,
    query: str,
    variables: dict,
    fallback_queue: str = "",
) -> list[dict]:
    jobs: list[dict] = []
    after = None
    while True:
        page_vars = dict(variables)
        page_vars["after"] = after
        data = bk_graphql(
            query,
            token,
            page_vars,
        )
        conn = (data.get("organization") or {}).get("jobs") or {}
        for edge in conn.get("edges") or []:
            node = edge.get("node") or {}
            record = _graphql_job_record(node, fallback_queue)
            if record:
                jobs.append(record)
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return jobs
        after = page.get("endCursor")


def fetch_active_cluster_jobs(
    token: str, queue_ids_by_key: dict[str, str] | None = None
) -> list[dict]:
    """Fetch active command jobs via GraphQL.

    Buildkite's queue metrics API accepts a cluster UUID, but the jobs API
    expects a GraphQL cluster-queue ID. Querying active jobs per queue keeps
    wait samples aligned with the queue-native backlog counts.
    """
    if queue_ids_by_key:
        jobs: list[dict] = []
        for queue, queue_id in sorted(queue_ids_by_key.items()):
            if not queue_id or is_excluded_queue(queue):
                continue
            jobs.extend(
                _fetch_graphql_jobs(
                    token,
                    query=GRAPHQL_QUEUE_JOBS_Q,
                    variables={
                        "org": BK_ORG,
                        "queue": [queue_id],
                        "states": list(GRAPHQL_ACTIVE_STATES),
                        "first": GRAPHQL_PAGE_SIZE,
                    },
                    fallback_queue=queue,
                )
            )
        return jobs

    return _fetch_graphql_jobs(
        token,
        query=GRAPHQL_ACTIVE_JOBS_Q,
        variables={
            "org": BK_ORG,
            "states": list(GRAPHQL_ACTIVE_STATES),
            "first": GRAPHQL_PAGE_SIZE,
        },
    )


def _collect_legacy_active_jobs(token: str) -> list[dict]:
    """Legacy fallback that scans active builds from the REST API."""
    records: list[dict] = []
    for state in ("running", "scheduled"):
        builds = bk_get_paginated(f"/organizations/{BK_ORG}/builds", token, {"state": state})
        log.info("Fetched %d %s builds", len(builds), state)

        for build in builds:
            build_branch = build.get("branch", "") or ""
            build_commit = (build.get("commit", "") or "")[:12]
            build_source = build.get("source", "") or ""
            pr = build.get("pull_request") or {}
            fork_url = pr.get("repository") or ""
            pipeline_slug = (build.get("pipeline") or {}).get("slug", "")
            build_url = build.get("web_url", "") or ""

            for job in build.get("jobs", []):
                if job.get("type") != "script":
                    continue
                queue = queue_from_rules(job.get("agent_query_rules"))
                if not queue or is_excluded_queue(queue):
                    continue

                job_state = (job.get("state", "") or "").lower()
                if (
                    job_state not in LEGACY_WAITING_STATES
                    and job_state not in LEGACY_RUNNING_STATES
                ):
                    continue

                records.append(
                    {
                        "queue": queue,
                        "state": job_state.upper(),
                        "name": job.get("name", "") or "",
                        "job_uuid": job.get("id", "") or "",
                        "build_url": build_url,
                        "pipeline": pipeline_slug,
                        "build": build.get("number", 0),
                        "branch": build_branch,
                        "commit": build_commit,
                        "workload": classify_workload(pipeline_slug, build_branch, queue),
                        "fork_url": fork_url,
                        "source": build_source,
                        "runnable_at": job.get("runnable_at"),
                        "scheduled_at": job.get("scheduled_at"),
                        "created_at": job.get("created_at"),
                        "started_at": job.get("started_at"),
                        "fallback_url": job.get("web_url", "") or "",
                    }
                )
    return records


def _seed_queue_metrics(queue_stats: dict, metrics_by_queue: dict[str, dict]) -> None:
    for queue, meta in metrics_by_queue.items():
        if is_excluded_queue(queue):
            continue
        stats = queue_stats[queue]
        if meta.get("counts_available", True):
            stats["waiting"] = _as_count(meta.get("waiting"))
            stats["running"] = _as_count(meta.get("running"))
            stats["scheduled"] = stats["waiting"]
            stats["total"] = stats["waiting"] + stats["running"]
            stats["count_source"] = "cluster_metrics"
        if meta.get("connected_agents") is not None:
            stats["connected_agents"] = _as_count(meta.get("connected_agents"))
            stats["connected_agents_source"] = "queue_native_metrics"
        if meta.get("queue_url"):
            stats["queue_url"] = meta["queue_url"]
        if meta.get("metrics_ts"):
            stats["metrics_ts"] = meta["metrics_ts"]
        if meta.get("dispatch_paused"):
            stats["dispatch_paused"] = True
        if meta.get("official_wait"):
            stats["official_wait"] = dict(meta["official_wait"])


def _apply_active_jobs(
    now: datetime,
    queue_stats: dict,
    active_jobs: list[dict],
    trusted_count_queues: set[str],
) -> tuple[list[dict], list[dict]]:
    pending_jobs: list[dict] = []
    running_jobs: list[dict] = []

    for job in active_jobs:
        queue = job.get("queue") or ""
        if not queue or is_excluded_queue(queue):
            continue

        stats = queue_stats[queue]
        trust_counts = queue in trusted_count_queues
        state = job.get("state") or ""
        is_waiting = state in GRAPHQL_WAITING_STATES
        is_running = state in GRAPHQL_RUNNING_STATES or state.lower() in LEGACY_RUNNING_STATES
        if not is_waiting and not is_running:
            continue

        workload = job.get("workload") or "vllm"
        build_url = job.get("build_url") or ""
        web_url = _make_canvas_job_url(
            build_url, job.get("job_uuid") or "", job.get("fallback_url", "")
        )
        queue_wait_before_start = _started_wait_minutes(
            job.get("runnable_at"),
            job.get("scheduled_at"),
            job.get("created_at"),
            job.get("started_at"),
        )

        if is_waiting:
            wait_mins = round(
                _wait_minutes(
                    now, job.get("runnable_at"), job.get("scheduled_at"), job.get("created_at")
                ),
                1,
            )
            is_zombie = wait_mins >= QUEUE_ZOMBIE_THRESHOLD_MIN
            if is_zombie:
                stats["zombie_waiting"] = int(stats.get("zombie_waiting") or 0) + 1
            else:
                if not trust_counts:
                    stats["waiting"] += 1
                    stats["scheduled"] += 1
                    stats["total"] += 1
                stats.setdefault("waiting_by_workload", {"vllm": 0, "omni": 0})
                stats["waiting_by_workload"][workload] += 1
                stats["wait_times"].append(wait_mins)

            pending_jobs.append(
                {
                    "name": job.get("name") or "",
                    "queue": queue,
                    "state": "scheduled",
                    "wait_min": wait_mins,
                    "analysis_excluded": is_zombie,
                    "exclusion_reason": "zombie_wait" if is_zombie else "",
                    "zombie_threshold_min": QUEUE_ZOMBIE_THRESHOLD_MIN,
                    "url": web_url,
                    "pipeline": job.get("pipeline") or "",
                    "build": job.get("build") or 0,
                    "branch": job.get("branch") or "",
                    "commit": job.get("commit") or "",
                    "workload": workload,
                    "fork_url": job.get("fork_url") or "",
                    "source": job.get("source") or "",
                    "queue_url": stats.get("queue_url") or "",
                }
            )
            continue

        run_mins = _run_minutes(now, job.get("started_at"))
        is_zombie = (run_mins or 0) >= QUEUE_ZOMBIE_THRESHOLD_MIN
        if is_zombie:
            stats["zombie_running"] = int(stats.get("zombie_running") or 0) + 1
        else:
            if not trust_counts:
                stats["running"] += 1
                stats["total"] += 1
            stats.setdefault("running_by_workload", {"vllm": 0, "omni": 0})
            stats["running_by_workload"][workload] += 1
        running_jobs.append(
            {
                "name": job.get("name") or "",
                "queue": queue,
                "state": "running",
                "analysis_excluded": is_zombie,
                "exclusion_reason": "zombie_running" if is_zombie else "",
                "zombie_threshold_min": QUEUE_ZOMBIE_THRESHOLD_MIN,
                "url": web_url,
                "pipeline": job.get("pipeline") or "",
                "build": job.get("build") or 0,
                "branch": job.get("branch") or "",
                "commit": job.get("commit") or "",
                "workload": workload,
                "fork_url": job.get("fork_url") or "",
                "source": job.get("source") or "",
                "queue_wait_before_start_min": queue_wait_before_start,
                "run_min": run_mins,
                "queue_url": stats.get("queue_url") or "",
            }
        )

    return pending_jobs, running_jobs


def collect_snapshot(token: str) -> dict:
    """Collect the latest queue state using queue-native metrics when possible."""
    now = datetime.now(timezone.utc)
    prune_history_file(OUTPUT, now)
    queue_stats: dict = defaultdict(_queue_row)
    for queue in TRACKED_QUEUES:
        queue_stats[queue]

    metrics_by_queue: dict[str, dict] = {}
    counts_source = "active_job_scan"
    active_jobs_source = "legacy_build_scan"
    sampled_queues: set[str] | None = None

    try:
        metrics_by_queue = fetch_cluster_queue_metrics(token)
        _seed_queue_metrics(queue_stats, metrics_by_queue)
        if any(meta.get("counts_available", True) for meta in metrics_by_queue.values()):
            counts_source = "cluster_metrics"
    except Exception as exc:
        log.warning(
            "Buildkite cluster metrics unavailable, falling back to active job counts: %s", exc
        )

    active_queue_ids = {
        queue: str(meta.get("graphql_id") or "")
        for queue, meta in metrics_by_queue.items()
        if (
            not is_excluded_queue(queue)
            and meta.get("graphql_id")
            and (_as_count(meta.get("waiting")) or _as_count(meta.get("running")))
        )
    }

    try:
        active_jobs = fetch_active_cluster_jobs(token, active_queue_ids or None)
        active_jobs_source = (
            "cluster_queue_graphql" if active_queue_ids else "organization_jobs_graphql"
        )
        sampled_queues = set(active_queue_ids) if active_queue_ids else None
    except Exception as exc:
        log.warning(
            "Buildkite GraphQL active jobs unavailable, falling back to build scan: %s", exc
        )
        active_jobs = _collect_legacy_active_jobs(token)

    trusted_count_queues = {
        queue
        for queue, meta in metrics_by_queue.items()
        if not is_excluded_queue(queue) and meta.get("counts_available", True)
    }
    pending_jobs, running_jobs = _apply_active_jobs(
        now,
        queue_stats,
        active_jobs,
        trusted_count_queues,
    )

    queues = {}
    has_official_wait = False
    has_sample_wait = False
    has_agent_metrics = False
    for queue, stats in sorted(queue_stats.items()):
        if is_excluded_queue(queue):
            continue
        if queue not in TRACKED_QUEUES and not stats["waiting"] and not stats["running"]:
            continue
        row = {k: v for k, v in stats.items() if k not in {"wait_times", "official_wait"}}
        official_wait = stats.get("official_wait") or {"p50": None, "p95": None, "max": None}
        sample_summary = _wait_summary(stats["wait_times"])
        sample_available = sampled_queues is None or queue in sampled_queues
        sample_wait = {
            "available": sample_available,
            "count": len(stats["wait_times"]) if sample_available else None,
            **sample_summary,
        }
        row["official_wait_source"] = (
            "queue_native_metrics"
            if any(value is not None for value in official_wait.values())
            else None
        )
        row["sample_wait_source"] = active_jobs_source if sample_available else None
        _apply_wait_contract(row, official_wait, sample_wait)
        has_official_wait = has_official_wait or any(
            value is not None for value in official_wait.values()
        )
        has_sample_wait = has_sample_wait or bool(sample_wait["count"])
        has_agent_metrics = has_agent_metrics or bool(row.get("connected_agents_source"))
        queues[queue] = row

    queue_count_sources = {row["count_source"] for row in queues.values()}
    if len(queue_count_sources) == 1:
        counts_source = next(iter(queue_count_sources))
    elif queue_count_sources:
        counts_source = "mixed_queue_native_and_active_job_scan"

    snapshot = {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "queues": queues,
        "total_waiting": sum(int(s.get("waiting") or 0) for s in queues.values()),
        "total_running": sum(int(s.get("running") or 0) for s in queues.values()),
        "total_zombie_waiting": sum(int(s.get("zombie_waiting") or 0) for s in queues.values()),
        "total_zombie_running": sum(int(s.get("zombie_running") or 0) for s in queues.values()),
        "sources": {
            "counts": counts_source,
            "waits": (
                "cluster_metrics"
                if has_official_wait
                else ("scheduled_jobs" if has_sample_wait else "none")
            ),
            "active_jobs": active_jobs_source,
            "agents": "queue_native_metrics" if has_agent_metrics else "unavailable",
            "official_wait": ("queue_native_metrics" if has_official_wait else "unavailable"),
            "sampled_wait": active_jobs_source if has_sample_wait else "unavailable",
            "count_fields": {
                "waiting_running_scheduled_total": (
                    "Each queue row uses Buildkite cluster metrics when count_source is cluster_metrics; "
                    "otherwise counts are derived from fetched active jobs. Queue-native counts include zombies."
                ),
                "zombie_waiting_zombie_running": (
                    "Derived from fetched active jobs and reported separately from queue-native counts."
                ),
            },
            "wait_fields": _wait_field_descriptions(),
            "history_reset_ts": queue_history_reset_datetime().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "zombie_threshold_min": QUEUE_ZOMBIE_THRESHOLD_MIN,
        },
    }

    run_id = os.getenv("GITHUB_RUN_ID", "")
    if run_id:
        snapshot["run_id"] = run_id

    snapshot = normalize_history_snapshot(snapshot)
    if snapshot is None:
        raise RuntimeError("Generated queue snapshot failed schema normalization")

    jobs_data = {
        "ts": snapshot["ts"],
        "zombie_threshold_min": QUEUE_ZOMBIE_THRESHOLD_MIN,
        "pending": sorted(pending_jobs, key=lambda job: job.get("wait_min", 0), reverse=True),
        "running": running_jobs,
    }
    jobs_path = OUTPUT.parent / "queue_jobs.json"
    jobs_path.write_text(json.dumps(jobs_data, indent=2))
    log.info(
        "Wrote %d pending + %d running jobs to %s",
        len(pending_jobs),
        len(running_jobs),
        jobs_path,
    )

    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prune-only", action="store_true")
    parser.add_argument(
        "--merge-history-git-ref",
        metavar="REF",
        help="Merge queue history from REF by timestamp, then exit.",
    )
    args = parser.parse_args()

    if args.merge_history_git_ref:
        merge_history_from_git_ref(OUTPUT, args.merge_history_git_ref)
        return

    if args.prune_only:
        before, kept = prune_history_file(OUTPUT)
        log.info("Pruned queue history: %d -> %d rows", before, kept)
        return

    token = os.getenv("BUILDKITE_TOKEN")
    if not token:
        log.error("BUILDKITE_TOKEN not set")
        sys.exit(1)

    log.info("Collecting queue snapshot...")
    snapshot = collect_snapshot(token)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("a", encoding="utf-8") as history_file:
        history_file.write(json.dumps(snapshot, separators=(",", ":")) + "\n")
    prune_history_file(OUTPUT)

    log.info(
        "Snapshot: %d queues, %d waiting, %d running -> %s",
        len(snapshot["queues"]),
        snapshot["total_waiting"],
        snapshot["total_running"],
        OUTPUT,
    )

    for queue, stats in sorted(
        snapshot["queues"].items(), key=lambda item: item[1]["waiting"], reverse=True
    ):
        if stats["waiting"] > 0 or stats["running"] > 0:
            print(f"  {queue:30s} waiting={stats['waiting']:3d} running={stats['running']:3d}")


if __name__ == "__main__":
    main()
