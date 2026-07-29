#!/usr/bin/env python3
"""Collect unique AMD queue mappings for vLLM Omni CI and main vLLM CI.

Queue snapshots measure occupancy.  They cannot answer how many *unique*
jobs were mapped to AMD hardware because the same active job can appear in
many snapshots and short jobs can appear in none.  This collector walks the
explicit Buildkite pipelines configured in ``config/vllm_amd_queue_capacity.json``,
deduplicates command-job UUIDs, and publishes daily aggregates only.

The first run backfills 14 UTC calendar days.  Later runs refresh the current
and previous UTC day, merge those replacements into the committed aggregate,
and retain 30 days.  Raw Buildkite responses and job UUIDs are never written.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.constants import BK_API_BASE, BK_ORG  # noqa: E402
from vllm.ci.utils import parse_iso, queue_from_rules  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "vllm_amd_queue_capacity.json"
OUTPUT = ROOT / "data" / "vllm" / "ci" / "workload_mapping.json"

DEFAULT_BOOTSTRAP_DAYS = 14
DEFAULT_REFRESH_DAYS = 2
DEFAULT_RETENTION_DAYS = 30
DEFAULT_WINDOW_DAYS = 14
DEFAULT_MAX_PAGES = 50
PER_PAGE = 100

log = logging.getLogger(__name__)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_start(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc)
    return datetime.combine(value.date(), time.min, tzinfo=timezone.utc)


def _date_range(start: date, end: date) -> list[date]:
    if end < start:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def load_config(path: Path = CONFIG_PATH) -> dict:
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1:
        raise ValueError(f"Unsupported AMD queue config schema in {path}")
    if not isinstance(data.get("queues"), list):
        raise ValueError(f"AMD queue config has no queues list: {path}")
    pipelines = data.get("workload_pipelines")
    if not isinstance(pipelines, dict) or not all(
        isinstance(pipelines.get(name), list) and pipelines[name]
        for name in ("omni", "main")
    ):
        raise ValueError(f"AMD queue config has incomplete workload_pipelines: {path}")
    return data


def monitored_queues(config: dict) -> dict[str, dict]:
    """Return the exact public AMD queue allowlist keyed by Buildkite queue ID."""
    rows: dict[str, dict] = {}
    for raw in config.get("queues") or []:
        if not isinstance(raw, dict) or raw.get("monitored") is not True:
            continue
        queue_id = str(raw.get("id") or "").strip()
        if not queue_id or "perf_eval" in queue_id.casefold():
            continue
        try:
            gpus_per_job = int(raw.get("gpus_per_job"))
        except (TypeError, ValueError):
            continue
        if gpus_per_job not in (1, 2, 4, 8):
            continue
        rows[queue_id] = {
            "id": queue_id,
            "label": raw.get("label") or queue_id.removeprefix("amd_"),
            "family": raw.get("family") or "unknown",
            "gpus_per_job": gpus_per_job,
            "lifecycle": raw.get("lifecycle") or "unknown",
        }
    if not rows:
        raise ValueError("AMD queue config produced an empty monitored queue allowlist")
    return rows


def _job_queue(job: dict) -> str:
    queue = queue_from_rules(job.get("agent_query_rules"))
    if queue:
        return queue
    cluster_queue = job.get("cluster_queue")
    if isinstance(cluster_queue, dict):
        return str(cluster_queue.get("key") or "")
    return ""


def _job_mapped_at(job: dict, build: dict) -> datetime | None:
    return (
        parse_iso(job.get("created_at"))
        or parse_iso(job.get("runnable_at"))
        or parse_iso(build.get("created_at"))
    )


def _job_gpu_hours(job: dict, gpus_per_job: int) -> float | None:
    started = parse_iso(job.get("started_at"))
    finished = parse_iso(job.get("finished_at"))
    if started is None or finished is None or finished <= started:
        return None
    duration_hours = (finished - started).total_seconds() / 3600
    # Treat records longer than a day as stale rather than publishing a
    # misleading resource-consumption spike.
    if duration_hours > 24:
        return None
    return duration_hours * gpus_per_job


def _empty_workload() -> dict:
    return {
        "mapped_jobs": 0,
        "started_jobs": 0,
        "finished_jobs": 0,
        "mapped_gpu_slots": 0,
        "gpu_hours": 0.0,
        "by_queue": {},
    }


def _empty_day(day: str) -> dict:
    return {
        "date": day,
        "complete": True,
        "lower_bound": False,
        "workloads": {
            "omni": _empty_workload(),
            "main": _empty_workload(),
        },
    }


def _request_build_page(
    path: str,
    token: str,
    params: dict[str, Any],
) -> list[dict]:
    response = requests.get(
        f"{BK_API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise TypeError(f"Buildkite returned non-list payload for {path}")
    return payload


def fetch_pipeline_builds(
    token: str,
    pipeline: str,
    start: datetime,
    end: datetime,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    page_fetcher: Callable[[str, str, dict[str, Any]], list[dict]] = _request_build_page,
) -> tuple[list[dict], dict]:
    """Fetch one pipeline exhaustively and report whether pagination completed."""
    path = f"/organizations/{BK_ORG}/pipelines/{pipeline}/builds"
    base_params: dict[str, Any] = {
        "created_from": _utc_iso(start),
        "created_to": _utc_iso(end),
        "include_retried_jobs": "true",
        "exclude_pipeline": "true",
        "per_page": PER_PAGE,
    }
    builds: list[dict] = []
    complete = False
    error = ""
    pages = 0
    for page in range(1, max_pages + 1):
        params = {**base_params, "page": page}
        try:
            rows = page_fetcher(path, token, params)
        except Exception as exc:  # retain explicit lower-bound metadata
            error = f"{type(exc).__name__}: {exc}"
            break
        pages = page
        log.info(
            "Fetched %s page %d (%d builds)",
            pipeline,
            page,
            len(rows),
        )
        if not rows:
            complete = True
            break
        builds.extend(row for row in rows if isinstance(row, dict))
        if len(rows) < PER_PAGE:
            complete = True
            break
    return builds, {
        "pipeline": pipeline,
        "pages_fetched": pages,
        "builds_fetched": len(builds),
        "complete": complete,
        "truncated": not complete and not error and pages >= max_pages,
        "error": error or None,
    }


def _events_from_builds(
    builds: list[dict],
    *,
    workload: str,
    pipeline: str,
    queue_catalog: dict[str, dict],
    start: datetime,
    end: datetime,
) -> tuple[list[dict], dict]:
    events: list[dict] = []
    seen: set[str] = set()
    missing_job_ids = 0
    duplicate_job_ids = 0
    for build in builds:
        build_pipeline = str((build.get("pipeline") or {}).get("slug") or pipeline)
        if build_pipeline != pipeline:
            continue
        for job in build.get("jobs") or []:
            if not isinstance(job, dict) or job.get("type") not in {"script", "command"}:
                continue
            queue = _job_queue(job)
            if queue not in queue_catalog:
                continue
            job_id = str(job.get("id") or "").strip()
            if not job_id:
                missing_job_ids += 1
                continue
            if job_id in seen:
                duplicate_job_ids += 1
                continue
            seen.add(job_id)
            mapped_at = _job_mapped_at(job, build)
            if mapped_at is None or mapped_at < start or mapped_at >= end:
                continue
            queue_row = queue_catalog[queue]
            gpu_hours = _job_gpu_hours(job, queue_row["gpus_per_job"])
            events.append({
                "job_id": job_id,
                "date": mapped_at.astimezone(timezone.utc).date().isoformat(),
                "workload": workload,
                "pipeline": pipeline,
                "queue": queue,
                "gpus_per_job": queue_row["gpus_per_job"],
                "started": parse_iso(job.get("started_at")) is not None,
                "finished": parse_iso(job.get("finished_at")) is not None,
                "gpu_hours": gpu_hours,
            })
    return events, {
        "mapped_job_ids": len(seen),
        "missing_job_ids": missing_job_ids,
        "duplicate_job_ids": duplicate_job_ids,
    }


def _aggregate_days(
    events: list[dict],
    start_day: date,
    end_day: date,
    workload_complete: dict[str, bool],
) -> list[dict]:
    rows = {
        day.isoformat(): _empty_day(day.isoformat())
        for day in _date_range(start_day, end_day)
    }
    seen_global: set[str] = set()
    for event in events:
        job_id = event["job_id"]
        if job_id in seen_global:
            continue
        seen_global.add(job_id)
        row = rows.get(event["date"])
        if row is None:
            continue
        workload = event["workload"]
        bucket = row["workloads"][workload]
        bucket["mapped_jobs"] += 1
        bucket["started_jobs"] += int(event["started"])
        bucket["finished_jobs"] += int(event["finished"])
        bucket["mapped_gpu_slots"] += int(event["gpus_per_job"])
        if event["gpu_hours"] is not None:
            bucket["gpu_hours"] += float(event["gpu_hours"])
        queue_bucket = bucket["by_queue"].setdefault(event["queue"], {
            "mapped_jobs": 0,
            "started_jobs": 0,
            "finished_jobs": 0,
            "mapped_gpu_slots": 0,
            "gpu_hours": 0.0,
        })
        queue_bucket["mapped_jobs"] += 1
        queue_bucket["started_jobs"] += int(event["started"])
        queue_bucket["finished_jobs"] += int(event["finished"])
        queue_bucket["mapped_gpu_slots"] += int(event["gpus_per_job"])
        if event["gpu_hours"] is not None:
            queue_bucket["gpu_hours"] += float(event["gpu_hours"])

    for row in rows.values():
        row["complete"] = all(workload_complete.values())
        row["lower_bound"] = not row["complete"]
        for workload in row["workloads"].values():
            workload["gpu_hours"] = round(workload["gpu_hours"], 2)
            workload["by_queue"] = {
                queue: {
                    **stats,
                    "gpu_hours": round(stats["gpu_hours"], 2),
                }
                for queue, stats in sorted(workload["by_queue"].items())
            }
    return list(rows.values())


def _merge_daily(existing: dict, replacements: list[dict], retention_start: date) -> list[dict]:
    merged = {
        str(row.get("date")): row
        for row in existing.get("daily") or []
        if isinstance(row, dict) and row.get("date")
    }
    merged.update({row["date"]: row for row in replacements})
    return [
        merged[day]
        for day in sorted(merged)
        if day >= retention_start.isoformat()
    ]


def _sum_workloads(rows: list[dict]) -> dict:
    totals = {"omni": _empty_workload(), "main": _empty_workload()}
    for row in rows:
        for workload_name, bucket in (row.get("workloads") or {}).items():
            if workload_name not in totals:
                continue
            target = totals[workload_name]
            for field in ("mapped_jobs", "started_jobs", "finished_jobs", "mapped_gpu_slots"):
                target[field] += int(bucket.get(field) or 0)
            target["gpu_hours"] += float(bucket.get("gpu_hours") or 0)
            for queue, queue_bucket in (bucket.get("by_queue") or {}).items():
                aggregate = target["by_queue"].setdefault(queue, {
                    "mapped_jobs": 0,
                    "started_jobs": 0,
                    "finished_jobs": 0,
                    "mapped_gpu_slots": 0,
                    "gpu_hours": 0.0,
                })
                for field in ("mapped_jobs", "started_jobs", "finished_jobs", "mapped_gpu_slots"):
                    aggregate[field] += int(queue_bucket.get(field) or 0)
                aggregate["gpu_hours"] += float(queue_bucket.get("gpu_hours") or 0)
    for bucket in totals.values():
        bucket["gpu_hours"] = round(bucket["gpu_hours"], 2)
        bucket["by_queue"] = {
            queue: {**stats, "gpu_hours": round(stats["gpu_hours"], 2)}
            for queue, stats in sorted(bucket["by_queue"].items())
        }
    return totals


def collect_workload_mapping(
    token: str,
    config: dict,
    *,
    existing: dict | None = None,
    now: datetime | None = None,
    bootstrap_days: int = DEFAULT_BOOTSTRAP_DAYS,
    refresh_days: int = DEFAULT_REFRESH_DAYS,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    window_days: int = DEFAULT_WINDOW_DAYS,
    max_pages: int = DEFAULT_MAX_PAGES,
    page_fetcher: Callable[[str, str, dict[str, Any]], list[dict]] = _request_build_page,
    force_days: int | None = None,
) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    existing = existing if isinstance(existing, dict) else {}
    has_history = bool(existing.get("daily"))
    query_days = force_days or (refresh_days if has_history else bootstrap_days)
    query_start = _day_start(now) - timedelta(days=max(1, query_days) - 1)
    query_end = _day_start(now) + timedelta(days=1)
    queue_catalog = monitored_queues(config)

    events: list[dict] = []
    sources: list[dict] = []
    workload_complete: dict[str, bool] = {}
    diagnostics: dict[str, dict] = {}
    for workload in ("omni", "main"):
        workload_sources = []
        workload_complete[workload] = True
        missing_ids = 0
        duplicates = 0
        for pipeline in config["workload_pipelines"][workload]:
            # Fetch one extra build-created day because dynamic pipeline
            # uploads can create command jobs after their parent build.
            builds, source = fetch_pipeline_builds(
                token,
                pipeline,
                query_start - timedelta(days=1),
                query_end,
                max_pages=max_pages,
                page_fetcher=page_fetcher,
            )
            extracted, event_meta = _events_from_builds(
                builds,
                workload=workload,
                pipeline=pipeline,
                queue_catalog=queue_catalog,
                start=query_start,
                end=query_end,
            )
            events.extend(extracted)
            source.update({
                "workload": workload,
                "mapped_jobs_in_query": len(extracted),
                **event_meta,
            })
            sources.append(source)
            workload_sources.append(source)
            workload_complete[workload] = workload_complete[workload] and bool(
                source["complete"] and event_meta["missing_job_ids"] == 0
            )
            missing_ids += event_meta["missing_job_ids"]
            duplicates += event_meta["duplicate_job_ids"]
        diagnostics[workload] = {
            "pipelines": len(workload_sources),
            "missing_job_ids": missing_ids,
            "duplicate_job_ids": duplicates,
        }

    replacements = _aggregate_days(
        events,
        query_start.date(),
        (query_end - timedelta(days=1)).date(),
        workload_complete,
    )
    retention_start = now.date() - timedelta(days=max(1, retention_days) - 1)
    daily = _merge_daily(existing, replacements, retention_start)
    window_start = now.date() - timedelta(days=max(1, window_days) - 1)
    window_rows = [row for row in daily if row["date"] >= window_start.isoformat()]
    complete = len(window_rows) == window_days and all(row.get("complete") for row in window_rows)
    collection_start = daily[0]["date"] if daily else now.date().isoformat()

    return {
        "schema_version": 1,
        "generated_at": _utc_iso(now),
        "collection_start": collection_start,
        "timezone": "UTC",
        "window": {
            "days": window_days,
            "start_date": window_start.isoformat(),
            "end_date": now.date().isoformat(),
            "complete": complete,
            "lower_bound": not complete,
        },
        "scope": {
            "queues": sorted(queue_catalog),
            "excluded_queue_classes": list(
                (config.get("scope") or {}).get("excluded_queue_classes") or []
            ),
            "workload_pipelines": config["workload_pipelines"],
        },
        "semantics": {
            "mapped_jobs": (
                "Unique Buildkite command-job UUIDs whose explicit queue rule maps "
                "to a monitored AMD queue; retry attempts are included as distinct UUIDs."
            ),
            "daily_bucket": "UTC date when the job mapping record was created.",
            "started_jobs": "Mapped jobs with a Buildkite started_at timestamp.",
            "mapped_gpu_slots": "Sum of configured GPUs per mapped job; not GPU-hours.",
            "gpu_hours": (
                "Sum of started-to-finished wall hours multiplied by configured GPUs "
                "per job; unfinished and stale >24h records are excluded."
            ),
            "privacy": "Only daily aggregates are published; raw jobs and UUIDs are not retained.",
        },
        "query": {
            "start": _utc_iso(query_start),
            "end_exclusive": _utc_iso(query_end),
            "bootstrap_days": bootstrap_days,
            "refresh_days": refresh_days,
            "forced_days": force_days,
            "pipeline_sources": sources,
            "diagnostics": diagnostics,
        },
        "totals": _sum_workloads(window_rows),
        "daily": daily,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect vLLM/Omni AMD job mappings")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--bootstrap-days", type=int, default=DEFAULT_BOOTSTRAP_DAYS)
    parser.add_argument("--refresh-days", type=int, default=DEFAULT_REFRESH_DAYS)
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument(
        "--force-days",
        type=int,
        default=None,
        help="Ignore incremental refresh and replace this many UTC calendar days.",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args()
    token = os.getenv("BUILDKITE_TOKEN", "").strip()
    if not token:
        raise SystemExit("BUILDKITE_TOKEN not set")
    config = load_config(args.config)
    existing = {}
    if args.output.exists():
        try:
            existing = json.loads(args.output.read_text())
        except (OSError, json.JSONDecodeError):
            log.warning("Ignoring unreadable existing workload mapping at %s", args.output)
    payload = collect_workload_mapping(
        token,
        config,
        existing=existing,
        bootstrap_days=args.bootstrap_days,
        refresh_days=args.refresh_days,
        retention_days=args.retention_days,
        window_days=args.window_days,
        max_pages=args.max_pages,
        force_days=args.force_days,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    log.info(
        "Wrote %s: Omni=%d main=%d mapped jobs in the %d-day window (%s)",
        args.output,
        payload["totals"]["omni"]["mapped_jobs"],
        payload["totals"]["main"]["mapped_jobs"],
        payload["window"]["days"],
        "complete" if payload["window"]["complete"] else "lower bound",
    )


if __name__ == "__main__":
    main()
