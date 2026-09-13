#!/usr/bin/env python3
"""Collect per-physical-node AMD CI agent-health data across ALL builds.

Unlike ``scripts/collect_ci.py`` — which tracks only main-branch *nightly*
builds for the dashboard's headline CI health — this collector walks **every**
build in the AMD-relevant pipelines: all branches, all triggers (PRs, release
branches, scheduled). For each job that ran on a physical AMD **GPU** node it
observes one run. Physical node identity comes from the Buildkite agent's
``k8s:node`` tag, which the build *list* endpoint already returns inline, so no
per-build detail fetch and no log download is needed (~135 paginated GETs for a
full 60-day backfill).

Volume note: all-branch AMD GPU volume is ~9k runs/day across 200+ nodes, ~45-80%
of which "fail" — overwhelmingly PR *code* bugs, not node infra. Shipping raw
runs to the browser (250 MB/60d) is infeasible and clustering every failure would
bury the infra signal. So this collector does the heavy lifting server-side and
emits only compact, meaningful artifacts:

  1. Per-node/day **rollups** (run / failure / cancelled counts, split into an
     "all builds" bucket and a "nightly-on-main" bucket) — the reliability table
     denominators. Tiny.
  2. **Failing runs** — every hard/soft failing run is shipped raw (still far
     smaller than all runs) so the frontend's "failure signal" toggle can drive
     the timeline / co-failure clustering off any of three subsets: infra-suspect
     only, hard failures, or all failures. Each row carries an ``i`` flag marking
     the *infra-suspect* subset: a failure whose test group *mostly passes that
     day* (pass-rate >= threshold across >=N samples) AND passed on a *different*
     node the same day — isolating anomalous, node-attributable failures from
     broadly-broken code. The frontend clusters the selected subset into
     co-failure events reactively (per the window / signal / nightly toggles).

On-disk stores (merged incrementally, pruned to 60d and then byte-bounded by
dropping the oldest complete UTC days):
    data/vllm/ci/agent_health/node_days.jsonl        per-(node,day) rollups
    data/vllm/ci/agent_health/infra_failures.jsonl   failing runs (i=infra-suspect)
Assembled output (read by build_operations_snapshot and published atomically
with the two-file ledger):
    data/vllm/ci/agent_health.json

Guarded workflow CLI form (a token without durable guard state exits 78):
    python scripts/vllm/collect_agent_health.py --days 60 --output data/vllm/ci/  # backfill
    python scripts/vllm/collect_agent_health.py --days 3                           # incremental
    python scripts/vllm/collect_agent_health.py --dry-run --days 7
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add scripts/ to path so the vllm package is importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.buildkite_request_guard import install_from_environment_or_exit

install_from_environment_or_exit()

from vllm.ci import config as cfg
from vllm.ci.buildkite_client import _paginate
from vllm.ci.log_parser import node_from_agent
from vllm.constants import amd_gpu_hardware
from vllm.dashboard_storage_budget import writer_max_bytes
from vllm.pipelines import (
    BK_ORG as VLLM_ORG,
    NIGHTLY_NAME_PATTERNS_BY_SLUG,
    PIPELINES as VLLM_PIPELINES,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUT = ROOT / "data" / "vllm" / "ci"
STORE_SUBDIR = "agent_health"
OUTPUT_JSON = "agent_health.json"
NODE_DAYS_JSONL = "node_days.jsonl"
INFRA_FAILURES_JSONL = "infra_failures.jsonl"

# Bound every tracked blob and the complete three-file generation well below
# the dashboard sync layer's 90 MB ceiling. The assembled JSON repeats the two
# private ledgers, so the aggregate cap also keeps repository growth bounded.
AGENT_HEALTH_MAX_FILE_BYTES = 32 * 1024 * 1024
AGENT_HEALTH_MAX_GENERATION_BYTES = writer_max_bytes("agent_health_generation")

# Retain the same window the frontend can display. Anything older is pruned.
MAX_WINDOW_DAYS = 60
DEFAULT_WINDOW_DAYS = 7
WINDOW_OPTIONS = (1, 3, 7, 14, 30, 60)
# Co-failure clustering window options (minutes) offered by the frontend toggle.
COFAILURE_WINDOW_OPTIONS = (30, 60, 120, 180, 360, 720, 1440)
DEFAULT_COFAILURE_WINDOW_MINS = 180
UNIDENTIFIED_NODE = "(unidentified)"

# Infra-suspect thresholds: a failing run counts as node-attributable only when
# its test group demonstrably works that day (so the failure is anomalous).
INFRA_SUSPECT_MIN_PASS_RATE = 0.5
INFRA_SUSPECT_MIN_SAMPLES = 3

# The hourly workflow refreshes three days.  A single upstream ``ci`` page with
# 100 embedded job rosters is large enough to approach the HTTP timeout, so
# split only that small incremental window into independent 24-hour requests.
# Keeping the long backfill path unchanged avoids multiplying its API quota.
MAX_INCREMENTAL_SLICE_DAYS = 3
MAX_INCREMENTAL_SLICE_WORKERS = 3
# The upstream ``ci`` pipeline embeds substantially larger job rosters than
# ``amd-ci``.  At 100 builds per page, a current 24-hour response can approach
# 200 MB and repeatedly exceed Buildkite's 30-second read window.  Fifty keeps
# the same exact pagination contract while bounding each upstream response;
# long backfills retain 100/page so their request count does not double.
UPSTREAM_INCREMENTAL_PER_PAGE = 50

# AMD-relevant pipeline slugs to walk.
AGENT_HEALTH_SLUGS = ("amd-ci", "ci")

_QUEUE_RULE_RE = re.compile(r"^queue=(.+)$", re.IGNORECASE)

cfg.configure(VLLM_ORG, VLLM_PIPELINES)


def _queue_of(job: dict) -> str:
    """Extract the ``queue=<name>`` value from a job's agent_query_rules."""
    for rule in job.get("agent_query_rules") or []:
        match = _QUEUE_RULE_RE.match(str(rule).strip())
        if match:
            return match.group(1).strip()
    for tag in (job.get("agent") or {}).get("meta_data") or []:
        if isinstance(tag, str) and tag.startswith("queue="):
            return tag[len("queue="):].strip()
    return ""


def _run_state(job: dict) -> str:
    """Classify a job into pass / soft / hard / canceled / skip (non-terminal)."""
    state = str(job.get("state") or "").lower()
    if job.get("soft_failed") or state in ("soft_failed", "soft_fail"):
        return "soft"
    if state == "passed":
        return "pass"
    if state == "canceled":
        return "canceled"
    if state in ("failed", "timed_out", "broken", "expired"):
        return "hard"
    return "skip"  # running / scheduled / assigned / etc. — not yet countable


def _day_of(value) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _observe(slug: str, build: dict, job: dict, nightly_re: re.Pattern | None) -> dict | None:
    """One raw observation for an AMD GPU job, or ``None`` if out of scope."""
    queue = _queue_of(job)
    hardware = amd_gpu_hardware(queue)
    if not hardware:  # not an AMD GPU queue (or excluded family)
        return None
    started = str(job.get("started_at") or "").strip()
    node = node_from_agent(job)
    if not node and not started:
        return None  # never actually ran on a node
    state = _run_state(job)
    if state == "skip":
        return None
    day = _day_of(started) or _day_of(job.get("created_at")) or _day_of(build.get("created_at"))
    if day is None:
        return None
    branch = str(build.get("branch") or "")
    message = str(build.get("message") or build.get("name") or "")
    is_main = branch == "main"
    nightly = bool(nightly_re and nightly_re.search(message)) and is_main
    # A failure inside a canceled/superseded build (new commit pushed -> Buildkite
    # kills the build, but already-failed jobs keep their "failed" state) is noise,
    # not a node fault. Flag it so the frontend can drop it with the toggle.
    build_canceled = str(build.get("state") or "").lower() in ("canceled", "canceling")
    return {
        "node": node or UNIDENTIFIED_NODE,
        "identified": bool(node),
        "hardware": hardware,
        "pipeline": slug,
        "queue": queue,
        "nightly": nightly,
        "is_main": is_main,
        "day": day,
        "group": str(job.get("name") or "").strip(),
        "state": state,
        "build_canceled": build_canceled,
        "build_number": build.get("number"),
        "job_id": str(job.get("id") or ""),
        "started_at": started,
        "finished_at": str(job.get("finished_at") or "").strip(),
    }


def _incremental_slices(
    created_from: datetime,
    created_to: datetime,
) -> list[tuple[datetime, datetime]]:
    """Return exact, non-overlapping 24-hour ranges covering a refresh window."""
    slices = []
    start = created_from
    while start < created_to:
        end = min(start + timedelta(days=1), created_to)
        slices.append((start, end))
        start = end
    return slices


def _fetch_pipeline_builds(
    url: str,
    created_from: datetime,
    created_to: datetime,
    days: int,
    *,
    incremental_per_page: int = 100,
) -> list[dict]:
    """Fetch one pipeline's build/job payloads with bounded incremental fan-out.

    Buildkite defines ``created_from`` as inclusive and ``created_to`` as
    exclusive, so the slices neither omit nor double-count boundary builds.
    Any slice failure is re-raised; callers therefore never publish a partial
    agent-health refresh.  ``_paginate`` retains the existing retry behavior.
    """
    base_params = {
        "per_page": 100,
        "exclude_pipeline": "true",
    }
    if days > MAX_INCREMENTAL_SLICE_DAYS:
        return _paginate(
            url,
            {**base_params, "created_from": created_from.isoformat()},
        )

    incremental_params = {
        **base_params,
        "per_page": incremental_per_page,
    }
    ranges = _incremental_slices(created_from, created_to)
    results: list[list[dict] | None] = [None] * len(ranges)
    with ThreadPoolExecutor(
        max_workers=min(MAX_INCREMENTAL_SLICE_WORKERS, len(ranges)),
        thread_name_prefix="agent-health-slice",
    ) as executor:
        pending = {
            executor.submit(
                _paginate,
                url,
                {
                    **incremental_params,
                    "created_from": start.isoformat(),
                    "created_to": end.isoformat(),
                },
            ): index
            for index, (start, end) in enumerate(ranges)
        }
        for future in as_completed(pending):
            results[pending[future]] = future.result()

    # Restore the builds endpoint's newest-first ordering across slices and
    # defensively deduplicate by pipeline-local build number.
    builds: list[dict] = []
    seen_builds: set[object] = set()
    for rows in reversed(results):
        assert rows is not None
        for build in rows:
            build_number = build.get("number")
            if build_number is not None and build_number in seen_builds:
                continue
            if build_number is not None:
                seen_builds.add(build_number)
            builds.append(build)
    return builds


def _fetch_pipeline_observations(
    slug: str,
    days: int,
    *,
    query_time: datetime | None = None,
) -> list[dict]:
    query_time = query_time or datetime.now(timezone.utc)
    created_from = query_time - timedelta(days=days)
    url = f"{cfg.BK_API_BASE}/organizations/{cfg.BK_ORG}/pipelines/{slug}/builds"
    # NB: we deliberately do NOT request include_retried_jobs. The upstream ``ci``
    # pipeline has thousands of builds over 60d; pulling every superseded attempt
    # inline makes pages huge and the endpoint time out. The latest attempt per
    # job still carries the node tag + terminal state, which is what node-health
    # needs, and this keeps both the backfill and the hourly incremental fast.
    builds = _fetch_pipeline_builds(
        url,
        created_from,
        query_time,
        days,
        incremental_per_page=(
            UPSTREAM_INCREMENTAL_PER_PAGE if slug == "ci" else 100
        ),
    )
    nightly_re = None
    pattern = NIGHTLY_NAME_PATTERNS_BY_SLUG.get(slug)
    if pattern:
        nightly_re = re.compile(pattern, re.IGNORECASE)
    obs: list[dict] = []
    for build in builds:
        for job in build.get("jobs") or []:
            row = _observe(slug, build, job, nightly_re)
            if row is not None:
                obs.append(row)
    log.info("Pipeline %s: %d builds -> %d AMD GPU observations", slug, len(builds), len(obs))
    return obs


def _mark_infra_suspect(obs: list[dict]) -> None:
    """Flag each failing observation as infra-suspect (in place).

    Infra-suspect := the failing run's (group, day) mostly passes and passed on
    at least one *other* node — so the failure is anomalous / node-attributable
    rather than broadly-broken code failing everywhere.
    """
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in obs:
        grouped[(row["group"], row["day"])].append(row)
    for rows in grouped.values():
        gradable = [r for r in rows if r["state"] in ("pass", "hard", "soft")]
        passes = [r for r in gradable if r["state"] == "pass"]
        pass_nodes = {r["node"] for r in passes}
        rate = (len(passes) / len(gradable)) if gradable else 0.0
        healthy_group = (
            len(gradable) >= INFRA_SUSPECT_MIN_SAMPLES
            and rate >= INFRA_SUSPECT_MIN_PASS_RATE
        )
        for r in rows:
            r["infra_suspect"] = bool(
                healthy_group
                and r["state"] in ("hard", "soft")
                and (pass_nodes - {r["node"]})
            )


def _rollup_rows(obs: list[dict]) -> dict[tuple[str, str], dict]:
    """Aggregate observations into per-(node, day) rollup rows keyed by (node, day)."""
    rollups: dict[tuple[str, str], dict] = {}
    for r in obs:
        key = (r["node"], r["day"])
        row = rollups.get(key)
        if row is None:
            row = {
                "nd": r["node"],
                "h": r["hardware"],
                "d": r["day"],
                # bucket = [runs, soft, hard, canceled]
                "a": [0, 0, 0, 0],
                "n": [0, 0, 0, 0],
            }
            rollups[key] = row
        for bucket in ("a", "n") if r["nightly"] else ("a",):
            b = row[bucket]
            b[0] += 1  # every countable terminal run
            if r["state"] == "soft":
                b[1] += 1
            elif r["state"] == "hard":
                b[2] += 1
            elif r["state"] == "canceled":
                b[3] += 1
    return rollups


def _failing_row(r: dict) -> dict:
    """Compact failing-run record shipped to the frontend.

    ``i`` marks the infra-suspect subset (anomalous, node-attributable); the
    frontend's signal toggle uses it plus ``s`` (hard/soft) to select which
    failures drive the timeline and co-failure clustering.
    """
    return {
        "nd": r["node"],
        "h": r["hardware"],
        "p": r["pipeline"],
        "q": r["queue"],
        "g": r["group"],
        "s": r["state"],
        "ng": r["nightly"],
        "i": 1 if r.get("infra_suspect") else 0,
        "bc": 1 if r.get("build_canceled") else 0,
        "b": r["build_number"],
        "j": r["job_id"],
        "t": r["started_at"],
        "e": r["finished_at"],
        "d": r["day"],
    }


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"retained agent-health ledger is invalid at {path}:{line_number}"
            ) from exc
        if not isinstance(row, dict):
            raise RuntimeError(
                f"retained agent-health ledger row is not an object at "
                f"{path}:{line_number}"
            )
        out.append(row)
    return out


def _encoded_jsonl(rows: list[dict]) -> bytes:
    return "".join(
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for row in rows
    ).encode("utf-8")


def _encoded_json(payload: dict) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _failure_accounting_rows(failing: list[dict]) -> list[dict]:
    """Retain exact filterable counts even when raw link evidence is compacted."""
    counts: Counter = Counter()
    for row in failing:
        key = (
            str(row.get("d") or ""),
            str(row.get("nd") or ""),
            str(row.get("h") or ""),
            str(row.get("s") or ""),
            1 if row.get("i") else 0,
            1 if row.get("ng") else 0,
            1 if row.get("bc") else 0,
        )
        counts[key] += 1
    return [
        {
            "d": day,
            "nd": node,
            "h": hardware,
            "s": state,
            "i": infra,
            "ng": nightly,
            "bc": build_canceled,
            "c": count,
        }
        for (day, node, hardware, state, infra, nightly, build_canceled), count
        in sorted(counts.items())
    ]


def _merge_by_day(stored: list[dict], fresh: list[dict], earliest_day: str, cutoff_day: str,
                  key) -> list[dict]:
    """Replace all stored rows on/after ``earliest_day`` with fresh ones, prune < cutoff."""
    kept = [r for r in stored if cutoff_day <= str(r.get("d") or "") < earliest_day]
    fresh = [r for r in fresh if str(r.get("d") or "") >= cutoff_day]
    merged = kept + fresh
    # Dedup defensively (fresh wins) by the provided key.
    seen: dict = {}
    for r in merged:
        seen[key(r)] = r
    return list(seen.values())


def _assemble(
    node_days: list[dict],
    failing: list[dict],
    now: datetime,
    *,
    retention: dict | None = None,
    failure_accounting: list[dict] | None = None,
    source_failure_count: int | None = None,
) -> dict:
    accounting = (
        failure_accounting
        if failure_accounting is not None
        else _failure_accounting_rows(failing)
    )
    accounted_failures = sum(int(row.get("c") or 0) for row in accounting)
    if source_failure_count is None:
        source_failure_count = accounted_failures
    if accounted_failures != source_failure_count:
        raise RuntimeError("agent-health compact accounting does not reconcile")
    hardware_types = sorted({r["h"] for r in node_days if r.get("h")})
    total_runs = sum(r["a"][0] for r in node_days)
    payload = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "max_window_days": MAX_WINDOW_DAYS,
        "default_window_days": DEFAULT_WINDOW_DAYS,
        "window_options": list(WINDOW_OPTIONS),
        "cofailure_window_options": list(COFAILURE_WINDOW_OPTIONS),
        "default_cofailure_window_mins": DEFAULT_COFAILURE_WINDOW_MINS,
        "exclude_cancelled_default": True,
        "nightly_only_default": False,
        "pipelines": list(AGENT_HEALTH_SLUGS),
        "hardware_types": hardware_types,
        "infra_suspect_min_pass_rate": INFRA_SUSPECT_MIN_PASS_RATE,
        "infra_suspect_min_samples": INFRA_SUSPECT_MIN_SAMPLES,
        "total_runs": total_runs,
        "node_day_count": len(node_days),
        "infra_failure_count": source_failure_count,
        "published_failure_evidence_count": len(failing),
        "node_days": sorted(node_days, key=lambda r: (r["d"], r["nd"])),
        "failing_runs": sorted(failing, key=lambda r: (r.get("t") or "", r.get("j") or "")),
        "failure_accounting": accounting,
    }
    if retention is not None:
        payload["retention"] = retention
    return payload


def _row_sort_key(row: dict) -> str:
    return json.dumps(
        row,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _prepare_generation(
    node_days: list[dict],
    failing: list[dict],
    now: datetime,
    *,
    max_file_bytes: int = AGENT_HEALTH_MAX_FILE_BYTES,
    max_generation_bytes: int = AGENT_HEALTH_MAX_GENERATION_BYTES,
) -> dict:
    """Fit one honest generation by bounding days, then exact link evidence."""
    if max_file_bytes <= 0 or max_generation_bytes <= 0:
        raise ValueError("agent-health byte budgets must be positive")

    retained_node_days = sorted(
        node_days,
        key=lambda row: (
            str(row.get("d") or ""),
            str(row.get("nd") or ""),
            _row_sort_key(row),
        ),
    )
    retained_failing = sorted(
        failing,
        key=lambda row: (
            str(row.get("d") or ""),
            str(row.get("t") or ""),
            str(row.get("j") or ""),
            _row_sort_key(row),
        ),
    )
    original_days = sorted(
        {
            str(row.get("d") or "")
            for row in [*retained_node_days, *retained_failing]
            if str(row.get("d") or "")
        }
    )
    dropped_days: list[str] = []

    while True:
        retained_days = sorted(
            {
                str(row.get("d") or "")
                for row in [*retained_node_days, *retained_failing]
                if str(row.get("d") or "")
            }
        )
        source_failing = list(retained_failing)
        failure_accounting = _failure_accounting_rows(source_failing)

        def candidate(published_failing: list[dict]) -> dict:
            published_failing = sorted(
                published_failing,
                key=lambda row: (
                    str(row.get("d") or ""),
                    str(row.get("t") or ""),
                    str(row.get("j") or ""),
                    _row_sort_key(row),
                ),
            )
            omitted = len(source_failing) - len(published_failing)
            retention = {
                "policy": "drop_oldest_whole_days_then_bound_exact_failure_evidence",
                "configured_days": MAX_WINDOW_DAYS,
                "original_day_count": len(original_days),
                "retained_day_count": len(retained_days),
                "retained_start": retained_days[0] if retained_days else None,
                "retained_end": retained_days[-1] if retained_days else None,
                "dropped_oldest_day_count": len(dropped_days),
                "byte_limited": bool(dropped_days or omitted),
                "max_file_bytes": max_file_bytes,
                "max_generation_bytes": max_generation_bytes,
                "failure_evidence": {
                    "source": len(source_failing),
                    "published": len(published_failing),
                    "omitted": omitted,
                    "complete_relative_to_source": omitted == 0,
                    "selection": "newest_then_infra_suspect_then_recency",
                },
                "failure_accounting": {
                    "source": len(source_failing),
                    "accounted": sum(row["c"] for row in failure_accounting),
                    "rows": len(failure_accounting),
                    "complete_relative_to_source": True,
                },
            }
            payload = _assemble(
                retained_node_days,
                published_failing,
                now,
                retention=retention,
                failure_accounting=failure_accounting,
                source_failure_count=len(source_failing),
            )
            encoded = {
                NODE_DAYS_JSONL: _encoded_jsonl(retained_node_days),
                INFRA_FAILURES_JSONL: _encoded_jsonl(published_failing),
                OUTPUT_JSON: _encoded_json(payload),
            }
            file_sizes = {name: len(value) for name, value in encoded.items()}
            return {
                "node_days": retained_node_days,
                "failing": published_failing,
                "payload": payload,
                "encoded": encoded,
                "file_sizes": file_sizes,
                "total_bytes": sum(file_sizes.values()),
                "dropped_days": tuple(dropped_days),
            }

        def fits(generation: dict) -> bool:
            return (
                all(
                    size <= max_file_bytes
                    for size in generation["file_sizes"].values()
                )
                and generation["total_bytes"] <= max_generation_bytes
            )

        complete = candidate(source_failing)
        if fits(complete):
            return complete

        if len(retained_days) <= 1:
            newest_first = sorted(
                source_failing,
                key=lambda row: (
                    str(row.get("t") or ""),
                    str(row.get("j") or ""),
                    _row_sort_key(row),
                ),
                reverse=True,
            )
            priority: list[dict] = []
            selected_ids: set[int] = set()
            if newest_first:
                priority.append(newest_first[0])
                selected_ids.add(id(newest_first[0]))
            for row in newest_first:
                if row.get("i") and id(row) not in selected_ids:
                    priority.append(row)
                    selected_ids.add(id(row))
            for row in newest_first:
                if id(row) not in selected_ids:
                    priority.append(row)
                    selected_ids.add(id(row))

            low = 0
            high = len(priority)
            bounded: dict | None = None
            while low <= high:
                retained_count = (low + high) // 2
                attempt = candidate(priority[:retained_count])
                if fits(attempt):
                    bounded = attempt
                    low = retained_count + 1
                else:
                    high = retained_count - 1
            if bounded is not None:
                return bounded
            raise RuntimeError(
                "agent-health newest whole day compact accounting cannot fit "
                "the byte budgets without losing ledger counts: "
                f"files={complete['file_sizes']}, total={complete['total_bytes']}, "
                f"max_file={max_file_bytes}, max_generation={max_generation_bytes}"
            )

        oldest_day = retained_days[0]
        dropped_days.append(oldest_day)
        retained_node_days = [
            row
            for row in retained_node_days
            if str(row.get("d") or "") != oldest_day
        ]
        retained_failing = [
            row
            for row in retained_failing
            if str(row.get("d") or "") != oldest_day
        ]


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o644)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _publish_generation(output_dir: Path, generation: dict) -> None:
    """Install the private ledger and assembled JSON as one recoverable unit."""
    encoded = generation.get("encoded") or {}
    expected = {NODE_DAYS_JSONL, INFRA_FAILURES_JSONL, OUTPUT_JSON}
    if set(encoded) != expected or any(not isinstance(encoded[name], bytes) for name in expected):
        raise RuntimeError("agent-health generation is incomplete")
    file_sizes = {name: len(encoded[name]) for name in expected}
    if any(size > AGENT_HEALTH_MAX_FILE_BYTES for size in file_sizes.values()):
        raise RuntimeError("agent-health generation exceeds the per-file byte budget")
    if sum(file_sizes.values()) > AGENT_HEALTH_MAX_GENERATION_BYTES:
        raise RuntimeError("agent-health generation exceeds the aggregate byte budget")

    output_dir.mkdir(parents=True, exist_ok=True)
    store_dir = output_dir / STORE_SUBDIR
    summary_path = output_dir / OUTPUT_JSON
    if store_dir.exists() and not store_dir.is_dir():
        raise RuntimeError(f"agent-health ledger path is not a directory: {store_dir}")

    stage = Path(
        tempfile.mkdtemp(
            dir=output_dir,
            prefix=f".{STORE_SUBDIR}.stage.",
        )
    )
    backup: Path | None = None
    store_installed = False
    try:
        _write_bytes(stage / NODE_DAYS_JSONL, encoded[NODE_DAYS_JSONL])
        _write_bytes(stage / INFRA_FAILURES_JSONL, encoded[INFRA_FAILURES_JSONL])
        if store_dir.exists():
            backup = Path(
                tempfile.mkdtemp(
                    dir=output_dir,
                    prefix=f".{STORE_SUBDIR}.backup.",
                )
            )
            backup.rmdir()
            os.replace(store_dir, backup)
        os.replace(stage, store_dir)
        store_installed = True
        _atomic_write_bytes(summary_path, encoded[OUTPUT_JSON])
    except Exception as publish_error:
        try:
            if store_installed:
                _remove_path(store_dir)
            if backup is not None and backup.exists():
                os.replace(backup, store_dir)
                backup = None
        except Exception as rollback_error:
            # Never clean up the only recoverable prior generation after a
            # failed restore. Leave its explicit backup path in the exception
            # and on disk for the next run or an operator to recover.
            recovery_path = (
                backup
                if backup is not None and backup.exists()
                else store_dir if store_dir.exists() else None
            )
            recovery_kind = "prior ledger backup" if recovery_path == backup else "ledger"
            log.error(
                "Agent-health publish rollback failed; recoverable %s remains "
                "at %s: %s",
                recovery_kind,
                recovery_path,
                rollback_error,
            )
            raise RuntimeError(
                "agent-health publish failed and rollback failed; recoverable "
                f"{recovery_kind} remains at {recovery_path}"
            ) from publish_error
        raise
    else:
        if backup is not None and backup.exists():
            _remove_path(backup)
    finally:
        _remove_path(stage)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=3, help="How many days back to walk (max 60).")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Data dir.")
    parser.add_argument(
        "--pipeline", choices=("amd-ci", "ci", "both"), default="both",
        help="Restrict to one pipeline slug.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch + report, but do not write.")
    args = parser.parse_args()

    if not cfg.BK_TOKEN:
        log.error("BUILDKITE_TOKEN not set.")
        return 2

    days = max(1, min(args.days, MAX_WINDOW_DAYS))
    slugs = AGENT_HEALTH_SLUGS if args.pipeline == "both" else (args.pipeline,)
    now = datetime.now(timezone.utc)

    obs: list[dict] = []
    for slug in slugs:
        obs.extend(_fetch_pipeline_observations(slug, days, query_time=now))
    _mark_infra_suspect(obs)

    fresh_rollups = list(_rollup_rows(obs).values())
    # Ship every hard/soft failure raw; ``i`` marks the infra-suspect subset so
    # the frontend's signal toggle can select infra-only / hard / all failures.
    fresh_failing = [_failing_row(r) for r in obs if r["state"] in ("hard", "soft")]

    log.info(
        "Observed %d runs (%d identified) -> %d node-days, %d failing runs "
        "(%d infra-suspect); hardware=%s",
        len(obs), sum(1 for r in obs if r["identified"]),
        len(fresh_rollups), len(fresh_failing),
        sum(1 for r in fresh_failing if r["i"]),
        dict(Counter(r["hardware"] for r in obs)),
    )

    if args.dry_run:
        log.info("[dry-run] sample rollup: %s", json.dumps(fresh_rollups[:2], ensure_ascii=False))
        log.info("[dry-run] sample failure: %s", json.dumps(fresh_failing[:2], ensure_ascii=False))
        return 0

    store_dir = args.output / STORE_SUBDIR
    node_days_path = store_dir / NODE_DAYS_JSONL
    failing_path = store_dir / INFRA_FAILURES_JSONL

    earliest_day = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    cutoff_day = (now - timedelta(days=MAX_WINDOW_DAYS)).strftime("%Y-%m-%d")

    node_days = _merge_by_day(
        _load_jsonl(node_days_path), fresh_rollups, earliest_day, cutoff_day,
        key=lambda r: (r["nd"], r["d"]),
    )
    failing = _merge_by_day(
        _load_jsonl(failing_path), fresh_failing, earliest_day, cutoff_day,
        key=lambda r: r["j"],
    )

    generation = _prepare_generation(node_days, failing, now)
    _publish_generation(args.output, generation)
    payload = generation["payload"]
    out_path = args.output / OUTPUT_JSON
    log.info(
        "Wrote %s (%d node-days, %d failing runs, %.2f MB generation, "
        "%d oldest whole days dropped)",
        out_path,
        payload["node_day_count"],
        payload["infra_failure_count"],
        generation["total_bytes"] / 1e6,
        len(generation["dropped_days"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
