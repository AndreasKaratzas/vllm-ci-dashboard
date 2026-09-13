"""Compact, provenance-bearing reliability history for Buildkite main builds.

This module is intentionally collector-only.  It turns Buildkite build/job
payloads into a bounded static-site dataset without treating mixed outcomes as
retry evidence or combining hardware, queue, GPU-count, or shard variants.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any
from urllib.parse import parse_qs, urlparse

from vllm.ci.incident_transitions import (
    INCIDENT_TRANSITION_POLICY_ID,
    SOFT_CONFIRMATION_BUILDS,
    advance_incident,
    completed_build_eligibility,
)
from vllm.ci.utils import duration_mins, hardware_from_job_name, percentile, queue_from_rules
from vllm.constants import BK_ORG, is_excluded_queue
from vllm.pipelines import SKIP_JOB_PATTERNS


SCHEMA_VERSION = 2
OBSERVED_FAILURE_MOVEMENT_ID = "observed-failure-movement-v1"
OBSERVATION_LIMIT = 60
BUILD_MESSAGE_MAX_CHARS = 4096
BUILD_FETCH_PAGE_SIZE = 100
BUILD_FETCH_MAX_PAGES = 50
TRUSTWORTHY_BUILD_STATES = frozenset({"passed", "failed"})
ELIGIBLE_RESULTS = ("passed", "failed", "soft_fail")
FAILED_JOB_STATES = frozenset({"failed", "timed_out", "broken"})
KNOWN_TERMINAL_JOB_STATES = frozenset({
    "passed", "failed", "timed_out", "broken", "canceled", "cancelled",
    "expired", "skipped", "blocked", "soft_fail", "soft_failed",
})
RETRY_FIELDS = (
    "retried", "retried_in_job_id", "retries_count", "retry_source", "retry_type",
)

# Schema v1 repeated these build-catalog and derivable-link values for every
# retained observation.  Schema v2 stores only the foreign keys needed to
# reconstruct them.  Keeping the list explicit also lets validation reject a
# stale or tampered compatibility value instead of silently trusting it.
LEGACY_OBSERVATION_DERIVED_FIELDS = (
    "source_pipeline",
    "build_url",
    "build_commit",
    "build_message",
    "build_created_at",
    "job_url",
    "step_url",
)

_HW_PREFIX_RE = re.compile(r"^(mi\d+[a-z]?_\d+|gpu_\d+|amd_\w+):\s*", re.IGNORECASE)
_UPSTREAM_HW_RE = re.compile(
    r"(?<![a-z0-9])(h100|h200|b100|b200|a100|l4|t4|cpu|npu|tpu)(?![a-z0-9])",
    re.IGNORECASE,
)
_UPSTREAM_AMD_MIRROR_HW_RE = re.compile(
    r"(?<![a-z0-9])(mi\d{3,4}b?)(?:_\d+)?(?![a-z0-9])",
    re.IGNORECASE,
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _build_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _build_url(pipeline_slug: str, build: dict) -> str:
    number = _build_number(build.get("number"))
    return f"https://buildkite.com/{BK_ORG}/{pipeline_slug}/builds/{number}" if number else ""


def buildkite_build_url_matches(value: Any, pipeline_slug: str, build_number: Any = None) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    number = _build_number(build_number)
    prefix = f"/{BK_ORG}/{pipeline_slug}/builds/"
    if parsed.scheme != "https" or parsed.netloc != "buildkite.com" or not parsed.path.startswith(prefix):
        return False
    suffix = parsed.path[len(prefix):].strip("/")
    if not suffix.isdigit() or "/" in suffix:
        return False
    return number is None or int(suffix) == number


def buildkite_job_url_matches(value: Any, pipeline_slug: str, build_number: Any = None) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    number = _build_number(build_number)
    prefix = f"/{BK_ORG}/{pipeline_slug}/builds/"
    if parsed.scheme != "https" or parsed.netloc != "buildkite.com" or not parsed.path.startswith(prefix):
        return False
    suffix = parsed.path[len(prefix):].strip("/")
    parts = suffix.split("/")
    if len(parts) < 3 or not parts[0].isdigit() or parts[1] != "steps":
        return False
    if number is not None and int(parts[0]) != number:
        return False
    if parts[2] == "canvas":
        query = parse_qs(parsed.query)
        return bool(query.get("jid") or query.get("sid"))
    return bool(parts[2])


def _pipeline_slug_from_reliability(reliability: dict) -> str:
    provenance = reliability.get("provenance")
    cohort = reliability.get("cohort")
    for source in (provenance, cohort):
        if not isinstance(source, dict):
            continue
        pipeline = source.get("pipeline")
        if isinstance(pipeline, str) and pipeline:
            return pipeline
    return ""


def resolve_reliability_build(
    reliability: dict,
    build_number: Any,
) -> dict | None:
    """Resolve an observation's authoritative build-catalog row.

    The public resolver intentionally accepts both schema-v1 and schema-v2
    payloads so callers can use one migration path for last-known-good data.
    """
    number = _build_number(build_number)
    if number is None or not isinstance(reliability, dict):
        return None
    for build in reliability.get("builds") or []:
        if isinstance(build, dict) and _build_number(build.get("number")) == number:
            return build
    return None


def _observation_urls(build_url: str, observation: dict) -> tuple[str, str]:
    job_id = str(observation.get("job_id") or "")
    step_id = str(observation.get("step_id") or "")
    step_url = (
        f"{build_url}/steps/canvas?sid={step_id}&tab=output"
        if build_url and step_id
        else ""
    )
    if build_url and job_id:
        job_url = f"{build_url}/steps/canvas?jid={job_id}&tab=output"
    else:
        job_url = step_url or build_url
    # Buildkite script jobs normally have a job ID. Preserve the rare legacy
    # fallback only when its web URL cannot be reconstructed from IDs.
    override = observation.get("job_url_override")
    if isinstance(override, str) and override:
        job_url = override
    return job_url, step_url


def _hydrate_reliability_observation(
    reliability: dict,
    observation: dict,
    *,
    build: dict,
    pipeline_slug: str,
) -> dict:
    """Internal O(1) hydrator used when a build index is already available."""
    hydrated = dict(observation)
    hydrated.pop("job_url_override", None)
    build_url = str(build.get("url") or "")
    job_url, step_url = _observation_urls(build_url, observation)
    hydrated.update({
        "source_pipeline": pipeline_slug,
        "build_url": build_url,
        "build_commit": str(build.get("commit") or ""),
        "build_message": str(build.get("message") or ""),
        "build_created_at": str(build.get("created_at") or ""),
        "job_url": job_url,
        "step_url": step_url,
    })

    retry = observation.get("retry_evidence")
    if isinstance(retry, dict):
        hydrated_retry = dict(retry)
        hydrated_retry.pop("retried_in_job_url", None)
        retried_in = str(hydrated_retry.get("retried_in_job_id") or "")
        if retried_in and build_url:
            hydrated_retry["retried_in_job_url"] = (
                f"{build_url}/steps/canvas?jid={retried_in}&tab=output"
            )
        hydrated["retry_evidence"] = hydrated_retry
    return hydrated


def hydrate_reliability_observation(
    reliability: dict,
    observation: dict,
    *,
    pipeline_slug: str | None = None,
) -> dict:
    """Return one normalized observation in the legacy presentation shape.

    Schema v2 persists build/job/step identifiers and keeps build metadata in
    the authoritative build catalog.  This helper reconstructs the exact
    schema-v1 fields used by popup and server-side render consumers without
    mutating either input.  It also safely canonicalizes schema-v1 rows during
    migration rather than trusting their duplicated values.

    Raises ``KeyError`` when the observation does not reference a cataloged
    build and ``ValueError`` when the source pipeline cannot be determined.
    """
    if not isinstance(reliability, dict) or not isinstance(observation, dict):
        raise TypeError("reliability and observation must be dictionaries")
    build = resolve_reliability_build(reliability, observation.get("build_number"))
    if build is None:
        raise KeyError(f"unknown reliability build {observation.get('build_number')!r}")
    pipeline = pipeline_slug or _pipeline_slug_from_reliability(reliability)
    if not pipeline:
        raise ValueError("reliability pipeline is unavailable")
    return _hydrate_reliability_observation(
        reliability,
        observation,
        build=build,
        pipeline_slug=pipeline,
    )


def hydrate_reliability_observations(
    reliability: dict,
    observations: list[dict],
    *,
    pipeline_slug: str | None = None,
) -> list[dict]:
    """Hydrate a sequence with one build-catalog index construction.

    Prefer this bulk form for dashboard generation and audits with thousands
    of observations. It has the same validation/error behavior and preserves
    input order without mutating the payload.
    """
    if not isinstance(reliability, dict) or not isinstance(observations, list):
        raise TypeError("reliability must be a dictionary and observations a list")
    pipeline = pipeline_slug or _pipeline_slug_from_reliability(reliability)
    if not pipeline:
        raise ValueError("reliability pipeline is unavailable")
    build_by_number: dict[int, dict] = {}
    for build in reliability.get("builds") or []:
        if not isinstance(build, dict):
            continue
        number = _build_number(build.get("number"))
        if number is not None:
            build_by_number[number] = build
    hydrated = []
    for observation in observations:
        if not isinstance(observation, dict):
            raise TypeError("every reliability observation must be a dictionary")
        number = _build_number(observation.get("build_number"))
        build = build_by_number.get(number) if number is not None else None
        if build is None:
            raise KeyError(f"unknown reliability build {observation.get('build_number')!r}")
        hydrated.append(_hydrate_reliability_observation(
            reliability,
            observation,
            build=build,
            pipeline_slug=pipeline,
        ))
    return hydrated


def validate_all_main_reliability(
    payload: Any,
    pipeline_slug: str,
    *,
    require_exhaustive: bool = True,
) -> bool:
    if not isinstance(payload, dict):
        return False
    schema_version = payload.get("schema_version", 1)
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version not in {1, SCHEMA_VERSION}
    ):
        return False
    cohort = payload.get("cohort")
    provenance = payload.get("provenance")
    builds = payload.get("builds")
    groups = payload.get("groups")
    if not isinstance(cohort, dict) or not isinstance(provenance, dict):
        return False
    if not isinstance(builds, list) or not isinstance(groups, list):
        return False
    build_states = cohort.get("build_states")
    build_count = cohort.get("build_count")
    collection = provenance.get("collection")
    query = provenance.get("query")
    if (
        not isinstance(build_states, list)
        or any(not isinstance(state, str) for state in build_states)
        or set(build_states) != set(TRUSTWORTHY_BUILD_STATES)
        or not isinstance(build_count, int)
        or isinstance(build_count, bool)
        or build_count != len(builds)
        or cohort.get("id") != f"{pipeline_slug}-main-completed-pass-fail"
        or cohort.get("pipeline") != pipeline_slug
        or cohort.get("branch") != "main"
        or not isinstance(query, dict)
        or query.get("branch") != "main"
        or provenance.get("pipeline") != pipeline_slug
        or not str(provenance.get("endpoint") or "").endswith(f"/pipelines/{pipeline_slug}/builds")
        or not isinstance(collection, dict)
        or (require_exhaustive and collection.get("exhaustive") is not True)
        or (require_exhaustive and cohort.get("exhaustive") is not True)
    ):
        return False
    build_by_number: dict[int, dict] = {}
    for build in builds:
        if not isinstance(build, dict):
            return False
        number = _build_number(build.get("number"))
        if (
            number is None
            or number in build_by_number
            or build.get("branch") != "main"
            or str(build.get("state") or "").lower() not in TRUSTWORTHY_BUILD_STATES
            or not build.get("finished_at")
            or not buildkite_build_url_matches(build.get("url"), pipeline_slug, number)
            or (
                schema_version == SCHEMA_VERSION
                and (
                    any(
                        not isinstance(build.get(field), str)
                        for field in ("commit", "message", "created_at")
                    )
                    or len(build.get("message") or "") > BUILD_MESSAGE_MAX_CHARS
                )
            )
        ):
            return False
        build_by_number[number] = build
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("observations"), list):
            return False
        for field in (
            "denominator", "passed", "failed", "soft_failed",
            "excluded_observations", "retry_evidence_observations",
        ):
            value = group.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return False
        if not isinstance(group.get("duration"), dict):
            return False
        for observation in group["observations"]:
            if not isinstance(observation, dict):
                return False
            number = _build_number(observation.get("build_number"))
            job_id = observation.get("job_id")
            step_id = observation.get("step_id")
            if (
                number not in build_by_number
                or not isinstance(job_id, str)
                or not isinstance(step_id, str)
                or not (job_id or step_id)
                or (
                    "job_url_override" in observation
                    and (
                        not isinstance(observation["job_url_override"], str)
                        or not buildkite_job_url_matches(
                            observation["job_url_override"], pipeline_slug, number
                        )
                    )
                )
            ):
                return False
            hydrated = _hydrate_reliability_observation(
                payload,
                observation,
                build=build_by_number[number],
                pipeline_slug=pipeline_slug,
            )
            # Schema v1 requires the complete denormalized presentation shape.
            # Schema v2 normally omits it, but accepts a hydrated compatibility
            # row only when every duplicated value agrees with the catalog.
            for field in LEGACY_OBSERVATION_DERIVED_FIELDS:
                if schema_version == 1 and field not in observation:
                    return False
                if field in observation and observation[field] != hydrated[field]:
                    return False
            if (
                not buildkite_build_url_matches(hydrated["build_url"], pipeline_slug, number)
                or not buildkite_job_url_matches(hydrated["job_url"], pipeline_slug, number)
                or (
                    hydrated["step_url"]
                    and not buildkite_job_url_matches(
                        hydrated["step_url"], pipeline_slug, number
                    )
                )
            ):
                return False
            retry = observation.get("retry_evidence")
            if retry is not None and not isinstance(retry, dict):
                return False
            if isinstance(retry, dict) and "retried_in_job_url" in retry:
                hydrated_retry = hydrated.get("retry_evidence") or {}
                if retry["retried_in_job_url"] != hydrated_retry.get(
                    "retried_in_job_url"
                ):
                    return False
    return True


def filter_reliability_builds(builds: list[dict]) -> list[dict]:
    scoped = []
    for build in builds:
        if not _trusted_main_build(build):
            continue
        jobs = [
            job
            for job in build.get("jobs") or []
            if _is_test_job(job)
            and _is_terminal_job(job)
            and not is_excluded_queue(_queue(job))
        ]
        scoped.append({**build, "jobs": jobs})
    return scoped


def _job_urls(pipeline_slug: str, build: dict, job: dict) -> tuple[str, str]:
    """Return exact attempt and expanded-step URLs when identifiers exist."""
    base = _build_url(pipeline_slug, build)
    job_id = str(job.get("id") or job.get("job_id") or "")
    step_id = str((job.get("step") or {}).get("id") or job.get("step_id") or "")
    if base and job_id:
        attempt_url = f"{base}/steps/canvas?jid={job_id}&tab=output"
    else:
        attempt_url = str(job.get("web_url") or "")
    step_url = f"{base}/steps/canvas?sid={step_id}&tab=output" if base and step_id else ""
    if not attempt_url:
        attempt_url = step_url or base
    return attempt_url, step_url


def _canonical_label(raw_label: str) -> str:
    """Remove only the agent prefix; retain GPU counts and shard suffixes."""
    return re.sub(r"\s+", " ", _HW_PREFIX_RE.sub("", raw_label or "")).strip() or raw_label or "unknown"


def _step_key(job: dict) -> str:
    return str(job.get("step_key") or (job.get("step") or {}).get("key") or "")


def _queue(job: dict) -> str:
    return str(job.get("q") or queue_from_rules(job.get("agent_query_rules")) or "")


def _hardware(job_name: str, queue: str, pipeline_slug: str) -> str:
    if pipeline_slug != "ci":
        return hardware_from_job_name(job_name, queue)
    amd_hardware = hardware_from_job_name(job_name, (queue or "").lower())
    if amd_hardware != "unknown":
        return amd_hardware
    if re.match(r"^\s*amd\s*:", job_name or "", re.IGNORECASE):
        amd_match = _UPSTREAM_AMD_MIRROR_HW_RE.search(job_name or "")
        if amd_match:
            return amd_match.group(1).lower()
    match = _UPSTREAM_HW_RE.search(job_name or "")
    if match:
        return match.group(1).lower()
    normalized_queue = (queue or "").lower()
    for token in ("h100", "h200", "b100", "b200", "a100", "l4", "t4"):
        if token in normalized_queue:
            return token
    if normalized_queue.startswith("gpu_") or "gpu" in normalized_queue:
        return "gpu"
    if "cpu" in normalized_queue:
        return "cpu"
    return "unknown"


def _identity(job: dict, pipeline_slug: str = "amd-ci") -> dict[str, str]:
    raw_label = str(job.get("raw_name") or job.get("name") or "unknown").strip()
    queue = _queue(job)
    return {
        "raw_label": raw_label,
        "canonical_label": _canonical_label(raw_label),
        "step_key": _step_key(job),
        "hardware": _hardware(raw_label, queue, pipeline_slug),
        "queue": queue,
    }


def _group_id(identity: dict[str, str]) -> str:
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:20]


def nightly_signal_identity(
    job: dict,
    pipeline_slug: str = "amd-ci",
) -> dict[str, str]:
    """Return the shared strict raw/step/hardware/queue incident identity."""
    return _identity(job, pipeline_slug)


def nightly_signal_id(job: dict, pipeline_slug: str = "amd-ci") -> str:
    return _group_id(nightly_signal_identity(job, pipeline_slug))


def _is_test_job(job: dict) -> bool:
    if job.get("type") != "script":
        return False
    name = str(job.get("name") or "").lower()
    return not any(pattern in name for pattern in SKIP_JOB_PATTERNS)


def _is_terminal_job(job: dict) -> bool:
    state = str(job.get("state") or "").lower()
    return state in KNOWN_TERMINAL_JOB_STATES or bool(job.get("finished_at"))


def _result(job: dict) -> tuple[str, bool, str]:
    state = str(job.get("state") or "unknown").lower()
    soft_failed = bool(job.get("soft_failed")) or state in {"soft_fail", "soft_failed"}
    if soft_failed:
        return "soft_fail", True, ""
    if state == "passed":
        return "passed", True, ""
    if state in FAILED_JOB_STATES:
        return "failed", True, ""
    return "excluded", False, state or "unknown"


def _retry_evidence(job: dict) -> dict:
    evidence = {key: job.get(key) for key in RETRY_FIELDS if job.get(key) not in (None, "", False, 0, "0")}
    job_id = str(job.get("id") or job.get("job_id") or "")
    if evidence and job_id:
        evidence["job_id"] = job_id
    return evidence


def nightly_job_outcome(job: dict) -> str:
    """Return the shared incident-policy outcome for one terminal attempt."""
    result, eligible, _ = _result(job)
    if not eligible:
        return "indeterminate"
    return {
        "failed": "hard",
        "soft_fail": "soft",
        "passed": "passed",
    }[result]


def _attempt_id(job: dict) -> str:
    return str(job.get("id") or job.get("job_id") or "")


def _retry_count(job: dict) -> int:
    value = job.get("retries_count")
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _attempt_time_rank(job: dict) -> tuple[str, str, str]:
    return (
        str(job.get("finished_at") or ""),
        str(job.get("started_at") or ""),
        str(job.get("runnable_at") or ""),
    )


def _retry_depth(job_id: str, predecessors: dict[str, set[str]]) -> int:
    def visit(current: str, seen: set[str]) -> int:
        parents = predecessors.get(current) or set()
        eligible_parents = parents - seen
        if not eligible_parents:
            return 0
        return 1 + max(
            visit(parent, seen | {current, parent})
            for parent in eligible_parents
        )

    return visit(job_id, {job_id}) if job_id else 0


def collapse_nightly_attempts(
    jobs: list[dict],
    pipeline_slug: str = "amd-ci",
) -> dict[str, dict]:
    """Select one order-independent final attempt per strict nightly signal.

    Explicit retry linkage/rank selects the final attempt. Without retry
    evidence, incident outcomes win ties conservatively so unrelated duplicate
    rows cannot manufacture a recovery.
    """
    grouped: dict[str, dict] = {}
    for job in jobs:
        if not isinstance(job, dict):
            continue
        identity = nightly_signal_identity(job, pipeline_slug)
        group_id = _group_id(identity)
        group = grouped.setdefault(
            group_id,
            {"identity": identity, "attempts": []},
        )
        group["attempts"].append(job)

    selected: dict[str, dict] = {}
    outcome_rank = {"hard": 3, "soft": 2, "passed": 1, "indeterminate": 0}
    for group_id, group in grouped.items():
        attempts = group["attempts"]
        by_id = {
            _attempt_id(job): job
            for job in attempts
            if _attempt_id(job)
        }
        predecessors: dict[str, set[str]] = defaultdict(set)
        for job in attempts:
            source_id = _attempt_id(job)
            target_id = str(job.get("retried_in_job_id") or "")
            if source_id and target_id and target_id in by_id:
                predecessors[target_id].add(source_id)
        has_retry_evidence = any(bool(_retry_evidence(job)) for job in attempts)

        def attempt_rank(job: dict) -> tuple:
            outcome = nightly_job_outcome(job)
            job_id = _attempt_id(job)
            if not has_retry_evidence:
                return (
                    outcome_rank[outcome],
                    _attempt_time_rank(job),
                    job_id,
                )
            retry_count = _retry_count(job)
            explicit_retry_attempt = bool(
                retry_count
                or job.get("retry_source")
                or job.get("retry_type")
                or job_id in predecessors
            )
            return (
                _retry_depth(job_id, predecessors),
                retry_count,
                explicit_retry_attempt,
                not bool(job.get("retried")),
                not bool(job.get("retried_in_job_id")),
                _attempt_time_rank(job),
                outcome_rank[outcome],
                job_id,
            )

        final = max(attempts, key=attempt_rank)
        selected[group_id] = {
            "identity": group["identity"],
            "job": final,
            "outcome": nightly_job_outcome(final),
        }
    return selected


def _test_duration_index(
    test_result_builds: list[dict] | None,
) -> dict[tuple[int, str, str], float]:
    index: dict[tuple[int, str, str], float] = {}
    step_attempts: dict[tuple[int, str, str], dict[str, float]] = defaultdict(dict)
    for build in test_result_builds or []:
        number = int(build.get("number") or build.get("build_number") or 0)
        for position, job in enumerate(build.get("jobs") or []):
            duration = job.get("test_duration_mins")
            if not isinstance(duration, (int, float)):
                continue
            job_id = str(job.get("job_id") or "")
            step_id = str(job.get("step_id") or "")
            if job_id:
                index[(number, "job_id", job_id)] = float(duration)
            if step_id:
                # A Buildkite step ID is shared by retry attempts. Use it only
                # when the parsed result identifies one unambiguous attempt.
                attempt_key = job_id or f"anonymous:{position}"
                step_attempts[(number, "step_id", step_id)][attempt_key] = float(duration)
    for key, attempts in step_attempts.items():
        if len(attempts) == 1:
            index[key] = next(iter(attempts.values()))
    return index


def _lookup_test_duration(index: dict[tuple[int, str, str], float], build_number: int, job: dict) -> float | None:
    job_id = job.get("id") or job.get("job_id")
    if job_id:
        value = index.get((build_number, "job_id", str(job_id)))
        return round(value, 1) if value is not None else None
    step_id = (job.get("step") or {}).get("id") or job.get("step_id")
    if step_id:
        value = index.get((build_number, "step_id", str(step_id)))
        return round(value, 1) if value is not None else None
    return None


def _duration_summary(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "p50_mins": None, "p90_mins": None, "max_mins": None}
    return {
        "samples": len(ordered),
        "p50_mins": round(median(ordered), 1),
        "p90_mins": round(percentile(ordered, 90), 1),
        "max_mins": round(max(ordered), 1),
    }


def _trusted_main_build(build: dict) -> bool:
    return (
        str(build.get("branch") or "") == "main"
        and str(build.get("state") or "").lower() in TRUSTWORTHY_BUILD_STATES
        and bool(build.get("finished_at"))
        and bool(build.get("number"))
    )


def _trusted_build_rank(build: dict) -> tuple:
    """Select one stable, most-complete row if paginated input overlaps."""
    return (
        str(build.get("finished_at") or ""),
        str(build.get("created_at") or ""),
        len(build.get("jobs") or []),
        str(build.get("commit") or ""),
        str(build.get("message") or ""),
        str(build.get("web_url") or ""),
    )


def _bounded_build_message(value: Any) -> tuple[str, int | None]:
    """Bound pathological catalog values while preserving normal titles."""
    message = str(value or "")
    if len(message) <= BUILD_MESSAGE_MAX_CHARS:
        return message, None
    # Keep the stored value within the declared bound, including the visible
    # truncation marker. Normal Buildkite titles are preserved byte-for-byte.
    return message[:BUILD_MESSAGE_MAX_CHARS - 1] + "…", len(message)


def _observation(pipeline_slug: str, build: dict, job: dict, test_durations: dict) -> dict:
    number = int(build.get("number") or 0)
    state = str(job.get("state") or "unknown").lower()
    result, eligible, exclusion_reason = _result(job)
    started_at = str(job.get("started_at") or "")
    finished_at = str(job.get("finished_at") or "")
    runnable_at = str(job.get("runnable_at") or "")
    wall = duration_mins(started_at, finished_at)
    wait = duration_mins(runnable_at, started_at)
    e2e = duration_mins(runnable_at, finished_at)
    row = {
        "build_number": number,
        "job_id": str(job.get("id") or job.get("job_id") or ""),
        "step_id": str((job.get("step") or {}).get("id") or job.get("step_id") or ""),
        "observed_at": finished_at or started_at or str(build.get("finished_at") or build.get("created_at") or ""),
        "started_at": started_at,
        "finished_at": finished_at,
        "runnable_at": runnable_at,
        "terminal_state": state,
        "result": result,
        "eligible_for_reliability": eligible,
        "soft_failed": bool(job.get("soft_failed")) or result == "soft_fail",
        "wall_completion_mins": round(wall, 1) if wall is not None else None,
        "test_duration_mins": _lookup_test_duration(test_durations, number, job),
        "queue_wait_mins": round(wait, 1) if wait is not None else None,
        "end_to_end_mins": round(e2e, 1) if e2e is not None else None,
    }
    attempt_url, _ = _job_urls(pipeline_slug, build, job)
    derived_attempt_url, _ = _observation_urls(_build_url(pipeline_slug, build), row)
    if attempt_url and attempt_url != derived_attempt_url:
        row["job_url_override"] = attempt_url
    if exclusion_reason:
        row["exclusion_reason"] = exclusion_reason
    for key in ("tests", "passed_tests", "failed_tests", "skipped_tests"):
        if isinstance(job.get(key), (int, float)) and not isinstance(job.get(key), bool):
            row[key] = job[key]
    retry = _retry_evidence(job)
    if retry:
        row["retry_evidence"] = retry
    return row


def build_all_main_reliability(
    builds: list[dict],
    *,
    pipeline_slug: str = "amd-ci",
    window_days: int,
    generated_at: str | None = None,
    nightly_pattern: str = "",
    test_result_builds: list[dict] | None = None,
    observation_limit: int = OBSERVATION_LIMIT,
    collection_provenance: dict | None = None,
) -> dict:
    """Build an all-main attempt catalog separate from canonical nightlies."""
    generated_at = generated_at or _iso_now()
    nightly_re = re.compile(nightly_pattern, re.IGNORECASE) if nightly_pattern else None
    trusted_by_number: dict[int, dict] = {}
    for build in builds:
        if not _trusted_main_build(build):
            continue
        number = int(build["number"])
        existing = trusted_by_number.get(number)
        if existing is None or _trusted_build_rank(build) > _trusted_build_rank(existing):
            trusted_by_number[number] = build
    trusted = sorted(
        trusted_by_number.values(),
        key=lambda build: (str(build.get("created_at") or ""), int(build.get("number") or 0)),
        reverse=True,
    )
    test_durations = _test_duration_index(test_result_builds)
    build_catalog = []
    grouped: dict[str, dict] = {}
    excluded = Counter()
    excluded_queue_observations = 0
    eligible_total = 0
    retry_observations = 0

    for build in trusted:
        raw_message = str(build.get("message") or "")
        message, original_message_chars = _bounded_build_message(raw_message)
        is_nightly = bool(nightly_re and nightly_re.search(raw_message))
        catalog_build = {
            "number": int(build.get("number") or 0),
            "url": _build_url(pipeline_slug, build),
            "commit": str(build.get("commit") or ""),
            "message": message,
            "branch": "main",
            "state": str(build.get("state") or "unknown").lower(),
            "created_at": str(build.get("created_at") or ""),
            "started_at": str(build.get("started_at") or ""),
            "finished_at": str(build.get("finished_at") or ""),
            "is_canonical_nightly": is_nightly,
        }
        if original_message_chars is not None:
            catalog_build["message_truncated"] = True
            catalog_build["message_original_chars"] = original_message_chars
        build_catalog.append(catalog_build)
        seen_jobs: set[str] = set()
        for job in build.get("jobs") or []:
            if not _is_test_job(job) or not _is_terminal_job(job):
                continue
            if is_excluded_queue(_queue(job)):
                excluded_queue_observations += 1
                continue
            job_id = str(job.get("id") or job.get("job_id") or "")
            dedupe_key = job_id or json.dumps(
                _identity(job, pipeline_slug), sort_keys=True
            ) + "|" + str(job.get("finished_at") or "")
            if dedupe_key in seen_jobs:
                continue
            seen_jobs.add(dedupe_key)
            identity = _identity(job, pipeline_slug)
            group_id = _group_id(identity)
            group = grouped.setdefault(group_id, {
                "group_id": group_id,
                "identity": identity,
                "observations": [],
                "result_counts": Counter(),
                "excluded_counts": Counter(),
                "durations": defaultdict(list),
                "retry_evidence_observations": 0,
            })
            observation = _observation(pipeline_slug, build, job, test_durations)
            group["observations"].append(observation)
            if observation["eligible_for_reliability"]:
                group["result_counts"][observation["result"]] += 1
                eligible_total += 1
            else:
                reason = observation.get("exclusion_reason") or observation["terminal_state"] or "unknown"
                group["excluded_counts"][reason] += 1
                excluded[reason] += 1
            if observation.get("retry_evidence"):
                group["retry_evidence_observations"] += 1
                retry_observations += 1
            for key in ("wall_completion_mins", "test_duration_mins", "queue_wait_mins", "end_to_end_mins"):
                if isinstance(observation.get(key), (int, float)):
                    group["durations"][key].append(float(observation[key]))

    groups = []
    for group in grouped.values():
        observations = sorted(
            group["observations"],
            key=lambda row: (str(row.get("observed_at") or ""), int(row.get("build_number") or 0), str(row.get("job_id") or "")),
            reverse=True,
        )
        counts = group["result_counts"]
        denominator = sum(counts.values())
        incidents = counts["failed"] + counts["soft_fail"]
        identity = group["identity"]
        limit = max(1, int(observation_limit))
        eligible_observations = [row for row in observations if row["eligible_for_reliability"]]
        retained = eligible_observations[:limit]
        if len(retained) < limit:
            excluded_observations = [row for row in observations if not row["eligible_for_reliability"]]
            retained.extend(excluded_observations[:limit - len(retained)])
            retained.sort(
                key=lambda row: (
                    str(row.get("observed_at") or ""),
                    int(row.get("build_number") or 0),
                    str(row.get("job_id") or ""),
                ),
                reverse=True,
            )
        groups.append({
            "group_id": group["group_id"],
            "name": identity["canonical_label"],
            "raw_name": identity["raw_label"],
            "step_key": identity["step_key"],
            "hardware": identity["hardware"],
            "queue": identity["queue"],
            "denominator": denominator,
            "passed": counts["passed"],
            "failed": counts["failed"],
            "soft_failed": counts["soft_fail"],
            "incident_rate": round(incidents / denominator * 100, 1) if denominator else None,
            "excluded_observations": sum(group["excluded_counts"].values()),
            "excluded_by_state": dict(sorted(group["excluded_counts"].items())),
            "retry_evidence_observations": group["retry_evidence_observations"],
            "duration": {
                "wall_completion": _duration_summary(group["durations"]["wall_completion_mins"]),
                "test_reported": _duration_summary(group["durations"]["test_duration_mins"]),
                "queue_wait": _duration_summary(group["durations"]["queue_wait_mins"]),
                "end_to_end": _duration_summary(group["durations"]["end_to_end_mins"]),
            },
            "observation_count": len(observations),
            "retained_observation_count": len(retained),
            "retained_eligible_observation_count": sum(
                bool(row["eligible_for_reliability"]) for row in retained
            ),
            "observations_truncated": len(observations) > len(retained),
            "observations": retained,
        })
    groups.sort(key=lambda row: (
        str(row["name"]).lower(), str(row["raw_name"]).lower(), row["step_key"], row["hardware"], row["queue"], row["group_id"],
    ))

    observed_times = [str(build.get("created_at") or "") for build in trusted if build.get("created_at")]
    requested_from = ""
    generated_dt = _parse_iso(generated_at)
    if generated_dt:
        requested_from = (generated_dt - timedelta(days=window_days)).isoformat().replace("+00:00", "Z")
    collection = dict(collection_provenance or {})
    collection.setdefault("created_from", requested_from)
    collection.setdefault("exhaustive", True)
    collection.setdefault("termination_reason", "provided_builds")
    nightly_count = sum(bool(build["is_canonical_nightly"]) for build in build_catalog)
    eligible_groups = sum(bool(group["denominator"]) for group in groups)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "cohort": {
            "id": f"{pipeline_slug}-main-completed-pass-fail",
            "name": f"{pipeline_slug} branch=main builds with state passed or failed and finished_at",
            "pipeline": pipeline_slug,
            "branch": "main",
            "window_days": window_days,
            "requested_from": requested_from,
            "observed_from": min(observed_times, default=""),
            "observed_to": max(observed_times, default=""),
            "build_states": sorted(TRUSTWORTHY_BUILD_STATES),
            "build_count": len(build_catalog),
            "canonical_nightly_build_count": nightly_count,
            "non_nightly_main_build_count": len(build_catalog) - nightly_count,
            "includes_canonical_nightlies": True,
            "exhaustive": collection["exhaustive"] is True,
            "selection": "branch=main, build state in [failed, passed], and finished_at present",
        },
        "denominator": {
            "unit": "terminal job attempts with passed, failed, or soft-fail outcomes",
            "eligible_results": list(ELIGIBLE_RESULTS),
            "eligible_observations": eligible_total,
            "excluded_observations": sum(excluded.values()),
            "excluded_by_state": dict(sorted(excluded.items())),
            "groups": eligible_groups,
            "catalog_groups": len(groups),
            "excluded_only_groups": len(groups) - eligible_groups,
            "out_of_scope_queue_observations": excluded_queue_observations,
        },
        "provenance": {
            "provider": "Buildkite REST API",
            "organization": BK_ORG,
            "pipeline": pipeline_slug,
            "endpoint": f"/organizations/{BK_ORG}/pipelines/{pipeline_slug}/builds",
            "query": {
                "branch": "main",
                "created_from": collection.get("created_from") or requested_from,
                "include_retried_jobs": True,
            },
            "collection": collection,
            "pagination": {
                "page_size": BUILD_FETCH_PAGE_SIZE,
                "max_pages": BUILD_FETCH_MAX_PAGES,
                "pages_fetched": collection.get("pages_fetched"),
                "termination_reason": collection.get("termination_reason"),
                "exhaustive": collection.get("exhaustive") is True,
                "stop_conditions": ["empty page", "short page", "page adds no build numbers"],
            },
            "build_state_source": "Buildkite build state",
            "job_result_source": "Buildkite terminal job state and soft_failed flag",
            "wall_completion_source": "job started_at to finished_at",
            "test_duration_source": "parsed test-result logs when exact job ID or unique step ID matches",
            "queue_wait_source": "job runnable_at to started_at",
            "end_to_end_source": "job runnable_at to finished_at",
            "retry_source": "explicit Buildkite retry fields only",
            "queue_scope_source": "vllm.constants.is_excluded_queue",
            "queue_scope_policy": "excluded queues are removed before group and denominator accounting",
            "observation_schema": (
                "normalized build_number/job_id/step_id references; hydrate from builds catalog"
            ),
            "build_catalog_authoritative_fields": [
                "url", "commit", "message", "created_at",
            ],
            "build_message_max_chars": BUILD_MESSAGE_MAX_CHARS,
            "observation_limit_per_group": max(1, int(observation_limit)),
            "observation_retention": (
                "newest eligible reliability observations first, then newest excluded observations"
            ),
        },
        "summary": {
            "builds": len(build_catalog),
            "groups": eligible_groups,
            "catalog_groups": len(groups),
            "eligible_observations": eligible_total,
            "retry_evidence_observations": retry_observations,
            "out_of_scope_queue_observations": excluded_queue_observations,
        },
        "builds": build_catalog,
        "groups": groups,
    }


def compact_main_builds(reliability: dict) -> list[dict]:
    """Adapt bounded evidence to the legacy normalized build shape.

    The private analytics artifact no longer persists this compatibility copy;
    consumers use ``all_main_reliability`` directly. The adapter remains for
    callers that need the old in-memory shape while migrating.
    """
    builds: dict[int, dict] = {}
    catalog_by_number: dict[int, dict] = {}
    for source in reliability.get("builds") or []:
        number = int(source.get("number") or 0)
        if not number:
            continue
        catalog_by_number[number] = source
        builds[number] = {
            "number": number,
            "state": source.get("state") or "unknown",
            "created_at": source.get("created_at") or "",
            "started_at": source.get("started_at") or "",
            "finished_at": source.get("finished_at") or "",
            "message": source.get("message") or "",
            "branch": source.get("branch") or "main",
            "commit": source.get("commit") or "",
            "web_url": source.get("url") or "",
            "build_kind": "nightly" if source.get("is_canonical_nightly") else "main",
            "jobs": [],
        }

    for group in reliability.get("groups") or []:
        for stored_observation in group.get("observations") or []:
            if not stored_observation.get("eligible_for_reliability"):
                continue
            number = int(stored_observation.get("build_number") or 0)
            if not number:
                continue
            source = catalog_by_number.get(number)
            pipeline_slug = _pipeline_slug_from_reliability(reliability) or str(
                stored_observation.get("source_pipeline") or ""
            )
            observation = (
                _hydrate_reliability_observation(
                    reliability,
                    stored_observation,
                    build=source,
                    pipeline_slug=pipeline_slug,
                )
                if source is not None and pipeline_slug
                else dict(stored_observation)
            )
            build = builds.setdefault(number, {
                "number": number,
                "state": "unknown",
                "created_at": observation.get("build_created_at") or "",
                "started_at": "",
                "finished_at": "",
                "message": observation.get("build_message") or "",
                "branch": "main",
                "commit": observation.get("build_commit") or "",
                "web_url": observation.get("build_url") or "",
                "build_kind": "main",
                "jobs": [],
            })
            job = {
                "group_id": group.get("group_id") or "",
                "canonical_group_id": group.get("group_id") or "",
                "name": group.get("name") or "unknown",
                "raw_name": group.get("raw_name") or group.get("name") or "unknown",
                "step_key": group.get("step_key") or "",
                "hardware": group.get("hardware") or "unknown",
                "q": group.get("queue") or "",
                "state": observation.get("result") or "unknown",
                "terminal_state": observation.get("terminal_state") or "unknown",
                "soft_failed": bool(observation.get("soft_failed")),
                "job_id": observation.get("job_id") or "",
                "step_id": observation.get("step_id") or "",
                "url": observation.get("job_url") or observation.get("step_url") or "",
                "attempt_url": observation.get("job_url") or "",
                "step_url": observation.get("step_url") or "",
                "started_at": observation.get("started_at") or "",
                "finished_at": observation.get("finished_at") or "",
                "runnable_at": observation.get("runnable_at") or "",
                "wall_duration_mins": observation.get("wall_completion_mins"),
                "wall_completion_mins": observation.get("wall_completion_mins"),
                "test_duration_mins": observation.get("test_duration_mins"),
                "wait_mins": observation.get("queue_wait_mins"),
                "queue_wait_mins": observation.get("queue_wait_mins"),
                "end_to_end_mins": observation.get("end_to_end_mins"),
            }
            job.update(observation.get("retry_evidence") or {})
            build["jobs"].append({
                key: value
                for key, value in job.items()
                if value not in (None, "")
            })

    for build in builds.values():
        build["jobs"].sort(key=lambda job: (
            str(job.get("name") or "").lower(),
            str(job.get("raw_name") or "").lower(),
            str(job.get("group_id") or ""),
            str(job.get("job_id") or ""),
        ))
    return sorted(
        builds.values(),
        key=lambda build: (str(build.get("created_at") or ""), int(build.get("number") or 0)),
        reverse=True,
    )


def _nightly_job_ref(
    pipeline_slug: str,
    build: dict,
    job: dict,
    identity: dict[str, str],
    group_id: str,
    outcome: str,
) -> dict:
    url = str(job.get("url") or job.get("web_url") or "")
    return {
        "group_id": group_id,
        "source_pipeline": pipeline_slug,
        "build_number": build.get("number") or build.get("build_number"),
        "name": identity["canonical_label"],
        "raw_name": identity["raw_label"],
        "step_key": identity["step_key"],
        "hardware": identity["hardware"],
        "queue": identity["queue"],
        "state": {
            "hard": "failed",
            "soft": "soft_fail",
            "passed": "passed",
            "indeterminate": str(job.get("state") or "unknown"),
        }[outcome],
        "url": url,
    }


def _nightly_state_map(
    build: dict,
    pipeline_slug: str,
) -> dict[str, tuple[str, dict]]:
    rows: dict[str, tuple[str, dict]] = {}
    for group_id, selected in collapse_nightly_attempts(
        build.get("jobs") or [], pipeline_slug
    ).items():
        outcome = selected["outcome"]
        rows[group_id] = (
            outcome,
            _nightly_job_ref(
                pipeline_slug,
                build,
                selected["job"],
                selected["identity"],
                group_id,
                outcome,
            ),
        )
    return rows


def compare_nightly_failures(
    current: dict[str, tuple[str, dict]],
    previous: dict[str, tuple[str, dict]] | None,
    *,
    preceding_build_number: Any = None,
    eligible: bool = True,
) -> dict[str, Any]:
    """Return the three observable failure changes for one eligible nightly.

    This deliberately ignores missing and indeterminate identities.  It is a
    current-build presentation model, separate from the conservative incident
    state machine that retains missing signals until an explicit pass.
    """
    comparison_available = eligible and previous is not None
    current_map = current if comparison_available else {}
    previous_map = previous or {}
    failure_outcomes = {"hard", "soft"}
    current_failures = {
        key for key, (outcome, _) in current_map.items()
        if outcome in failure_outcomes
    }
    previous_failures = {
        key for key, (outcome, _) in previous_map.items()
        if outcome in failure_outcomes
    }
    new_keys = current_failures - previous_failures
    recurring_keys = current_failures & previous_failures
    fixed_keys = {
        key for key in previous_failures
        if key in current_map and current_map[key][0] == "passed"
    }
    def sort_key(row: dict) -> tuple[str, str, str, str]:
        return (
            str(row.get("name") or "").casefold(),
            str(row.get("hardware") or ""),
            str(row.get("queue") or ""),
            str(row.get("group_id") or ""),
        )
    fixed = []
    for key in fixed_keys:
        current_ref = current_map[key][1]
        previous_outcome, previous_ref = previous_map[key]
        fixed.append({
            **current_ref,
            "current_state": "passed",
            "previous_state": previous_ref.get("state") or previous_outcome,
            "previous_url": previous_ref.get("url") or "",
        })
    fixed.sort(key=sort_key)
    return {
        "policy_id": OBSERVED_FAILURE_MOVEMENT_ID,
        "available": comparison_available,
        "preceding_build_number": preceding_build_number,
        "new": sorted((current_map[key][1] for key in new_keys), key=sort_key),
        "recurring": sorted(
            (current_map[key][1] for key in recurring_keys), key=sort_key
        ),
        "fixed": fixed,
    }


def compute_nightly_change_history(
    builds: list[dict],
    *,
    pipeline_slug: str = "amd-ci",
) -> list[dict]:
    """Replay canonical nightlies with confirmed-incident hysteresis."""
    ordered = sorted(
        builds,
        key=lambda build: (str(build.get("created_at") or build.get("date") or ""), int(build.get("number") or 0)),
    )
    history: list[dict] = []
    states: dict[str, dict] = {}
    incident_refs: dict[str, dict] = {}
    previous_eligible: dict | None = None
    previous_movement_build: dict | None = None
    previous_movement_map: dict[str, tuple[str, dict]] | None = None
    for current in ordered:
        current_map = _nightly_state_map(current, pipeline_slug)
        build_id = current.get("number") or current.get("build_number")
        transition_eligible, ineligible_reason = completed_build_eligibility(current)
        movement_available = transition_eligible and any(
            outcome in {"hard", "soft", "passed"}
            for outcome, _ in current_map.values()
        )
        transition_preceding_build_number = (
            previous_eligible.get("number")
            or previous_eligible.get("build_number")
            if previous_eligible
            else None
        )
        movement_preceding_build_number = (
            previous_movement_build.get("number")
            or previous_movement_build.get("build_number")
            if previous_movement_build
            else None
        )
        failure_movement = compare_nightly_failures(
            current_map,
            previous_movement_map,
            preceding_build_number=movement_preceding_build_number,
            eligible=movement_available,
        )
        buckets: dict[str, list[dict]] = {
            "new": [],
            "recurring": [],
            "fixed": [],
            "pending_soft": [],
            "not_observed": [],
            "indeterminate": [],
        }
        active_keys = {
            key for key, state in states.items()
            if state.get("status") in {"pending_soft", "confirmed"}
        }
        for key in sorted(set(current_map) | active_keys):
            observed_state, current_ref = current_map.get(key, ("absent", {}))
            current_state = observed_state if transition_eligible else "indeterminate"
            previous_ref = incident_refs.get(key) or {}
            decision = advance_incident(states.get(key), current_state, build_id)
            next_state = decision["state"]
            classification = decision["classification"]

            if classification == "none":
                pass
            elif classification == "fixed":
                buckets[classification].append({
                    **current_ref,
                    "current_state": "passed",
                    "previous_state": previous_ref.get("state") or "",
                    "previous_url": previous_ref.get("url") or "",
                    "transition_change": decision["change"],
                    "transition_eligible": transition_eligible,
                })
            else:
                held_indeterminate = (
                    decision["change"] == "held"
                    and decision["outcome"] == "indeterminate"
                )
                ref = previous_ref if held_indeterminate else (
                    current_ref if current_ref else previous_ref
                )
                row = {
                    **ref,
                    "incident_status": next_state["status"],
                    "current_severity": next_state["severity"],
                    "peak_severity": next_state["peak_severity"],
                    "soft_streak": next_state["soft_streak"],
                    "confirmation_threshold": SOFT_CONFIRMATION_BUILDS,
                    "transition_change": decision["change"],
                    "transition_eligible": transition_eligible,
                }
                if held_indeterminate and current_ref:
                    row["current_indeterminate_evidence"] = dict(current_ref)
                if not current_ref:
                    row["observed_in_current_build"] = False
                if ineligible_reason:
                    row["transition_ineligible_reason"] = ineligible_reason
                buckets[classification].append(row)

            if next_state["status"] == "clear":
                states.pop(key, None)
                incident_refs.pop(key, None)
            else:
                states[key] = next_state
                if transition_eligible and observed_state in {"hard", "soft"}:
                    incident_refs[key] = current_ref

        for rows in buckets.values():
            rows.sort(key=lambda row: (
                str(row.get("name") or "").casefold(),
                str(row.get("raw_name") or "").casefold(),
                str(row.get("hardware") or ""),
                str(row.get("queue") or ""),
                str(row.get("group_id") or ""),
            ))
        history.append({
            "policy_id": INCIDENT_TRANSITION_POLICY_ID,
            "build_number": current.get("number") or current.get("build_number"),
            "build_url": current.get("web_url") or "",
            "created_at": current.get("created_at") or "",
            "transition_eligible": transition_eligible,
            "transition_ineligible_reason": ineligible_reason or None,
            "preceding_build_number": transition_preceding_build_number,
            "failure_movement": failure_movement,
            **buckets,
        })
        if transition_eligible:
            previous_eligible = current
        if movement_available:
            previous_movement_build = current
            previous_movement_map = current_map
    return list(reversed(history))
