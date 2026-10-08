"""Current AMD/CUDA timing from one fixed, observed main-CI nightly cohort."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import re
from statistics import median
from typing import Any

from vllm.ci.analyzer import _extract_hardware, _parity_key_base
from vllm.constants import amd_gpu_hardware
from vllm.pipelines import (
    SKIP_JOB_PATTERNS,
    UPSTREAM_NIGHTLY_NAME_PATTERN,
    is_amd_ci_job,
    is_upstream_cuda_ci_job,
)

BUILD_LIMIT = 5
LATEST_NIGHTLY_MAX_AGE = timedelta(hours=48)
JOB_COLUMNS = (
    "job_id", "step_id", "url", "queue", "hardware", "raw_name",
    "started_at", "finished_at", "duration_mins",
)


def project_public_nightly_latency(payload: dict, *, max_bytes: int) -> dict:
    """Bound exact evidence without dropping a group, nightly or shard.

    Ordinary payloads retain readable job dictionaries. Large cohorts declare
    the same nine fields once and retain every value in exact column vectors.
    If even that lossless projection exceeds the route budget, fail before the
    publisher replaces its last-known-good generation.
    """
    def encoded_size(value: dict) -> int:
        return len((json.dumps({"latency": value}, separators=(",", ":"),
                               ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8"))

    if encoded_size(payload) <= max_bytes:
        return payload
    projected = {**payload, "job_columns": list(JOB_COLUMNS), "rows": []}
    for row in payload.get("rows") or []:
        public_row = dict(row)
        for side in ("amd", "upstream"):
            public_side = dict(row[side])
            public_side["samples"] = []
            for sample in row[side]["samples"]:
                jobs = sample["jobs"]
                if any(not isinstance(job, dict) or set(job) != set(JOB_COLUMNS) for job in jobs):
                    raise RuntimeError("Cannot compact latency evidence with unexpected job fields")
                public_side["samples"].append({
                    **sample, "jobs": [[job[column] for column in JOB_COLUMNS] for job in jobs],
                })
            public_row[side] = public_side
        projected["rows"].append(public_row)
    compact_bytes = encoded_size(projected)
    if compact_bytes > max_bytes:
        raise RuntimeError(
            "Exact five-nightly latency evidence exceeds its public byte budget; "
            f"preserving the last-known-good generation: {compact_bytes} > {max_bytes} bytes"
        )
    return projected


def _group_key(job: dict, raw_name: str) -> str:
    total, index = job.get("parallel_group_total"), job.get("parallel_group_index")
    if type(total) is int and total > 1 and type(index) is int and 0 <= index < total:
        raw_name = re.sub(rf"\s+{index}$", "", raw_name)
    return _parity_key_base(raw_name)


def _observed_step_families(jobs: list[dict]) -> dict[tuple[str, str], str]:
    """Recognize shard labels only when a shared exact step key proves them."""
    labels: dict[tuple[str, str], set[str]] = defaultdict(set)
    for job in jobs:
        if job.get("retried_in_job_id") or not job.get("step_key"):
            continue
        side = "amd" if is_amd_ci_job(job) else "upstream" if is_upstream_cuda_ci_job(job) else ""
        if side:
            labels[(side, str(job["step_key"]))].add(str(job.get("raw_name") or job.get("name") or ""))
    result = {}
    for identity, names in labels.items():
        bases = {_parity_key_base(re.sub(r"\s+(?:shard\s+)?\d+$", "", name, flags=re.I)) for name in names}
        if len(names) > 1 and len(bases) == 1 and all(re.search(r"\s+\d+$", name) for name in names):
            result[identity] = bases.pop()
    return result


def _timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _interval(samples: list[dict]) -> dict:
    starts = [sample["created_at"] for sample in samples if sample.get("created_at")]
    ends = [sample["finished_at"] for sample in samples if sample.get("finished_at")]
    return {"start": min(starts) if starts else None, "end": max(ends) if ends else None}


def build_current_nightly_latency(
    builds: list[dict], *, generated_at: str, source_available: bool,
    unavailable_reason: str | None = None,
) -> dict:
    """Use fresh Buildkite metadata only; missing groups never backfill old runs.

    Each nightly contributes the maximum completed shard wall duration once per
    group/platform. Superseded attempts, missing timing jobs, CPU steps and legacy
    pipeline links never contribute to a median.
    """
    result: dict[str, Any] = {
        "schema_version": 1, "source_pipeline": "ci", "branch": "main",
        "build_limit": BUILD_LIMIT, "generated_at": generated_at,
        "statistic": "median_of_per_nightly_group_wall_minutes",
        "duration_basis": "maximum_parallel_shard_wall_minutes",
        "available": False, "unavailable_reason": unavailable_reason,
        "cohort": {"nightlies": [], "build_count": 0}, "interval": _interval([]),
        "rows": [],
    }
    if not source_available:
        result["unavailable_reason"] = unavailable_reason or "fresh_ci_buildkite_metadata_unavailable"
        return result
    clock = _timestamp(generated_at)
    candidates = []
    pattern = re.compile(UPSTREAM_NIGHTLY_NAME_PATTERN, re.I)
    for build in builds:
        number = build.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            continue
        if build.get("branch") != "main" or not pattern.search(str(build.get("message") or "")):
            continue
        created, finished = _timestamp(build.get("created_at")), _timestamp(build.get("finished_at"))
        if build.get("state") not in {"passed", "failed"} or clock is None or created is None or finished is None or not created <= finished <= clock:
            continue
        expected_url = f"https://buildkite.com/vllm/ci/builds/{number}"
        if str(build.get("web_url") or "").rstrip("/") != expected_url:
            continue
        candidates.append(build)
    unique = {build["number"]: build for build in candidates}
    cohort = sorted(unique.values(), key=lambda b: (str(b.get("created_at") or ""), b["number"]), reverse=True)[:BUILD_LIMIT]
    nightlies = [{key: build.get(key) for key in ("number", "created_at", "finished_at", "web_url")} for build in cohort]
    result["cohort"] = {"nightlies": nightlies, "build_count": len(nightlies)}
    result["interval"] = _interval(nightlies)
    newest = _timestamp(cohort[0].get("created_at")) if cohort else None
    if clock is None or newest is None or newest > clock or clock - newest > LATEST_NIGHTLY_MAX_AGE:
        result["unavailable_reason"] = "latest_completed_ci_nightly_unavailable_or_stale"
        return result
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: {"amd": [], "upstream": []})
    for build in cohort:
        per_build: dict[tuple[str, str], list[dict]] = defaultdict(list)
        incomplete_groups: set[tuple[str, str]] = set()
        step_families = _observed_step_families(build.get("jobs") or [])
        for job in build.get("jobs") or []:
            if job.get("retried_in_job_id"):
                continue
            raw_name = str(job.get("raw_name") or job.get("name") or "")
            if any(skip in raw_name.lower() for skip in SKIP_JOB_PATTERNS):
                continue
            side = "amd" if is_amd_ci_job(job) else "upstream" if is_upstream_cuda_ci_job(job) else ""
            if not side:
                continue
            key = step_families.get((side, str(job.get("step_key") or ""))) or _group_key(job, raw_name)
            if not key:
                continue
            if job.get("state") not in {"passed", "failed", "soft_fail", "soft_failed", "timed_out", "broken"}:
                incomplete_groups.add((key, side))
                continue
            started, finished = _timestamp(job.get("started_at")), _timestamp(job.get("finished_at"))
            if started is None or finished is None or finished < started or finished > clock:
                incomplete_groups.add((key, side))
                continue
            duration = (finished - started).total_seconds() / 60
            if not math.isfinite(duration):
                incomplete_groups.add((key, side))
                continue
            job_id = str(job.get("job_id") or "")
            url = str(job.get("url") or "")
            exact_job_urls = {
                f"{build['web_url']}#{job_id}",
                f"{build['web_url']}/steps/canvas?jid={job_id}&tab=output",
            }
            if not job_id or url not in exact_job_urls:
                incomplete_groups.add((key, side))
                continue
            per_build[(key, side)].append({
                "job_id": job_id, "step_id": str(job.get("step_id") or ""),
                "url": url, "queue": job.get("q") or job.get("queue") or "",
                "hardware": amd_gpu_hardware(job.get("q") or job.get("queue")).lower() or _extract_hardware(raw_name), "raw_name": raw_name,
                "started_at": job.get("started_at"), "finished_at": job.get("finished_at"),
                "duration_mins": round(duration, 4),
            })
        for (key, side), jobs in per_build.items():
            if (key, side) in incomplete_groups:
                continue
            grouped[key][side].append({
                "build_number": build["number"], "build_url": build["web_url"],
                "created_at": build.get("created_at"), "finished_at": build.get("finished_at"),
                "duration_mins": max(job["duration_mins"] for job in jobs), "jobs": jobs,
            })
    for key, sides in sorted(grouped.items()):
        if not sides["amd"]:
            continue
        row: dict[str, Any] = {"id": key, "label": key, "match_status": "matched" if sides["upstream"] else "unmatched",
               "match_reason": None if sides["upstream"] else "no_timed_cuda_counterpart_in_latest_five_ci_nightlies"}
        for side, label in (("amd", "AMD GPUs · main CI"), ("upstream", "CUDA GPUs · main CI")):
            samples = sides[side]
            row[side] = {
                "platform_label": label, "source_pipeline": "ci",
                "median_duration_mins": round(median(sample["duration_mins"] for sample in samples), 4) if samples else None,
                "sample_count": len(samples), "samples": samples, "interval": _interval(samples),
            }
        amd_median, cuda_median = row["amd"]["median_duration_mins"], row["upstream"]["median_duration_mins"]
        row["ratio"] = round(amd_median / cuda_median, 4) if cuda_median else None
        row["delta_mins"] = round(amd_median - cuda_median, 4) if cuda_median is not None else None
        result["rows"].append(row)
    result["available"] = bool(result["rows"])
    result["unavailable_reason"] = None if result["available"] else "no_timed_amd_jobs_in_latest_five_ci_nightlies"
    return result
