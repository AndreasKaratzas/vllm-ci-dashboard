# cspell:ignore AKIA ASIA bkua gaierror github_pat pousr servname xapp xethub xoxb
"""Classify and aggregate Buildkite DNS failures without retaining job logs.

This module is deliberately split from the collector CLI. It owns the pure
classifier, the sanitized scanner-state contract (encrypted before durable
storage), deterministic gzip serialization, and the bounded public projection.
Raw Buildkite log content and secrets must remain in memory only and must never
be passed to any state/output writer.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Literal, TypedDict


SCHEMA_VERSION = 1
STATE_KIND = "vllm-ci-dns-scan-state"
CLASSIFIER_ID = "dns-v1"
RETENTION_HOURS = 720
EPISODE_GAP_SECONDS = 5
LOG_CLOCK_TOLERANCE_SECONDS = 60
MAX_LOG_BYTES = 16 * 1024 * 1024
PUBLIC_EVIDENCE_LIMIT = 3000
PUBLIC_EVIDENCE_BYTE_BUDGET = 5 * 1024 * 1024

PIPELINES = ("amd-ci", "ci")
JOB_STATES = ("passed", "soft", "hard")
SCAN_STATUSES = ("positive", "negative", "pending", "unavailable", "oversize")
FINAL_SCAN_STATUSES = frozenset({"positive", "negative", "oversize"})
TARGET_CATEGORIES = (
    "huggingface_hub",
    "vllm_public_assets",
    "aws_s3",
    "github",
    "pypi",
    "other_public",
    "unknown",
)
UNAVAILABLE_REASONS = (
    "authentication",
    "forbidden",
    "not_found",
    "rate_limited",
    "server_error",
    "network_error",
    "invalid_response",
)
TIME_BASES = ("log_timestamp", "job_finished_at")

class WindowOption(TypedDict):
    id: str
    label: str
    hours: int


WINDOW_OPTIONS: tuple[WindowOption, ...] = (
    {"id": "1h", "label": "Last hour", "hours": 1},
    {"id": "3h", "label": "Last 3 hours", "hours": 3},
    {"id": "12h", "label": "Last 12 hours", "hours": 12},
    {"id": "24h", "label": "Last day", "hours": 24},
    {"id": "72h", "label": "Last 3 days", "hours": 72},
    {"id": "168h", "label": "Last 7 days", "hours": 168},
    {"id": "720h", "label": "Last 30 days", "hours": 720},
)
DEFAULT_WINDOW = "24h"
COUNT_BASIS = "distinct_buildkite_job_attempts_with_strong_dns_evidence"

SIGNATURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "temporary_name_resolution",
        re.compile(r"temporary failure in name resolution", re.IGNORECASE),
    ),
    (
        "name_or_service_unknown",
        re.compile(r"name or service not known", re.IGNORECASE),
    ),
    (
        "urllib3_name_resolution",
        re.compile(r"\bNameResolutionError\b"),
    ),
    (
        "curl_could_not_resolve",
        re.compile(r"(?:curl:\s*\(6\)|could not resolve host\s*:)", re.IGNORECASE),
    ),
    (
        "getaddrinfo_eai_again",
        re.compile(r"\bgetaddrinfo\b.{0,80}\bEAI_AGAIN\b|\bEAI_AGAIN\b", re.IGNORECASE),
    ),
    (
        "getaddrinfo_failed",
        re.compile(r"\bgetaddrinfo failed\b", re.IGNORECASE),
    ),
    (
        "no_such_host",
        re.compile(r"\bno such host\b", re.IGNORECASE),
    ),
    (
        "nodename_not_known",
        re.compile(
            r"nodename nor servname provided|nodename.{0,40}(?:not known|unknown)",
            re.IGNORECASE,
        ),
    ),
    (
        "temporary_failure_resolving",
        re.compile(r"temporary failure resolving", re.IGNORECASE),
    ),
    (
        "dns_resolution_failed",
        re.compile(r"(?:dns|domain name) resolution (?:failed|failure|error)", re.IGNORECASE),
    ),
)
SIGNATURE_IDS = tuple(signature for signature, _ in SIGNATURE_PATTERNS)

_BUILDKITE_TIMESTAMP_RE = re.compile(r"\x1b_bk;t=(\d{10,19})")
_PUBLIC_HOST_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:[A-Za-z0-9-]+\.)+(?:com|org|net|io|co|ai|dev|edu)(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_QUEUE_RE = re.compile(r"^amd_mi(\d{3,4})(?:_|$)", re.IGNORECASE)
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# Coordinates normally contain only queue/node identifiers. Reject recognizable
# credential shapes as a second boundary in case an upstream label is ever
# mapped into one of those otherwise syntactically valid fields.
_SENSITIVE_VALUE_RE = re.compile(
    r"(?:"
    r"\b(?:xox[a-z]|xapp)-[A-Za-z0-9-]{12,}|"
    r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})|"
    r"\bbk[a-z]{1,6}_[A-Za-z0-9]{16,}|"
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|"
    r"\bauthorization\s*[:=]|"
    r"\b(?:bearer|basic)\s+[A-Za-z0-9+/_.~=-]{12,}|"
    r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY(?: BLOCK)?-----|"
    r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
    r"password|secret|token)\b[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9+/_.~=-]{8,}"
    r")",
    re.IGNORECASE,
)
_HARDWARE_RE = re.compile(r"^MI[0-9]{3,4}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_STATE_TOP_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "generated_at",
        "retention",
        "discovery",
        "classifier",
        "jobs",
    }
)
_STATE_BASE_JOB_KEYS = frozenset(
    {
        "pipeline",
        "build_number",
        "job_id",
        "queue",
        "node",
        "hardware",
        "state",
        "started_at",
        "finished_at",
        "status",
        "attempts",
        "last_attempt_at",
    }
)
_STATE_STATUS_KEYS = {
    "positive": frozenset(
        {
            "match_count",
            "episode_times",
            "episode_metrics",
            "signature_ids",
            "target_categories",
            "time_basis",
        }
    ),
    "negative": frozenset(),
    "pending": frozenset(),
    "unavailable": frozenset({"unavailable_reason"}),
    "oversize": frozenset({"log_bytes"}),
}


class StateValidationError(ValueError):
    """The sanitized durable DNS scanner state violates its strict contract."""


@dataclass(frozen=True)
class DnsEpisodeMetric:
    """Privacy-safe aggregate for one five-second DNS episode."""

    at: str
    match_count: int
    signature_ids: tuple[str, ...]
    target_categories: tuple[str, ...]


@dataclass(frozen=True)
class DnsClassification:
    """Privacy-safe result of scanning one complete Buildkite job log."""

    match_count: int
    episode_times: tuple[str, ...]
    signature_ids: tuple[str, ...]
    target_categories: tuple[str, ...]
    time_basis: Literal["log_timestamp", "job_finished_at"]
    episode_metrics: tuple[DnsEpisodeMetric, ...] = ()

    @property
    def positive(self) -> bool:
        return self.match_count > 0


def utc_now() -> datetime:
    """Return the collection clock frozen to whole UTC seconds."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_timestamp(value: object, field: str = "timestamp") -> datetime:
    if not isinstance(value, str) or not value:
        raise StateValidationError(f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StateValidationError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise StateValidationError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def iso_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    utc = value.astimezone(timezone.utc)
    timespec = "microseconds" if utc.microsecond else "seconds"
    return utc.isoformat(timespec=timespec).replace("+00:00", "Z")


def canonical_uuid(value: object, field: str = "job_id") -> str:
    if not isinstance(value, str):
        raise StateValidationError(f"{field} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise StateValidationError(f"{field} must be a UUID string") from exc
    canonical = str(parsed)
    if value != canonical or parsed.version not in {1, 2, 3, 4, 5}:
        raise StateValidationError(f"{field} must be a canonical lowercase UUID")
    return canonical


def queue_hardware(queue: object) -> str:
    text = str(queue or "").strip()
    match = _QUEUE_RE.match(text)
    return f"MI{match.group(1)}" if match else ""


def _line_timestamp(line: str, current: datetime | None) -> datetime | None:
    matches = _BUILDKITE_TIMESTAMP_RE.findall(line)
    if matches:
        raw = int(matches[-1])
        # Buildkite has emitted seconds, milliseconds, microseconds, and
        # nanoseconds in different renderers. Digit-count thresholds avoid
        # silently interpreting a 19-digit nanosecond marker as milliseconds.
        if raw < 10**11:
            divisor = 1
        elif raw < 10**14:
            divisor = 1_000
        elif raw < 10**17:
            divisor = 1_000_000
        else:
            divisor = 1_000_000_000
        try:
            return datetime.fromtimestamp(raw / divisor, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return current
    return current


def _target_categories(context: str) -> set[str]:
    lowered = context.casefold()
    categories: set[str] = set()
    if "huggingface.co" in lowered or "hf.co" in lowered or "xethub.hf.co" in lowered:
        categories.add("huggingface_hub")
    if "vllm-public-assets" in lowered:
        categories.add("vllm_public_assets")
    if "amazonaws.com" in lowered or "amazonaws.com.cn" in lowered:
        categories.add("aws_s3")
    if "github.com" in lowered or "githubusercontent.com" in lowered:
        categories.add("github")
    if "pypi.org" in lowered or "pythonhosted.org" in lowered:
        categories.add("pypi")
    if not categories and _PUBLIC_HOST_RE.search(context):
        categories.add("other_public")
    if not categories:
        categories.add("unknown")
    return categories


def _episode_metrics(
    matches: Iterable[tuple[datetime, set[str], set[str], bool]],
    gap_seconds: int,
) -> tuple[DnsEpisodeMetric, ...]:
    """Collapse adjacent matches while retaining enum/time associations."""
    ordered = sorted(matches, key=lambda entry: entry[0])
    if not ordered:
        return ()
    clusters: list[list[tuple[datetime, set[str], set[str], bool]]] = [[ordered[0]]]
    for entry in ordered[1:]:
        if (entry[0] - clusters[-1][-1][0]).total_seconds() > gap_seconds:
            clusters.append([])
        clusters[-1].append(entry)
    metrics: list[DnsEpisodeMetric] = []
    for cluster in clusters:
        observed_targets = {value for entry in cluster for value in entry[2]}
        metrics.append(
            DnsEpisodeMetric(
                at=iso_timestamp(cluster[0][0].replace(microsecond=0)),
                match_count=len(cluster),
                signature_ids=tuple(
                    sorted({value for entry in cluster for value in entry[1]})
                ),
                target_categories=tuple(
                    value for value in TARGET_CATEGORIES if value in observed_targets
                ),
            )
        )
    return tuple(metrics)


def classify_dns_log(
    log_text: str,
    *,
    job_finished_at: str,
    job_started_at: str | None = None,
) -> DnsClassification:
    """Classify strong DNS evidence in a complete job log.

    One matching log line contributes at most one raw match even when it contains
    several nested exception names. Matches no more than five seconds apart are
    one episode. Generic connection failures, retry exhaustion, timeouts, TLS
    failures, and HTTP status failures are deliberately not signatures.
    """
    if not isinstance(log_text, str):
        raise TypeError("log_text must be a string")
    fallback = parse_timestamp(job_finished_at, "job_finished_at")
    started = (
        parse_timestamp(job_started_at, "job_started_at")
        if job_started_at is not None
        else None
    )
    if started is not None and started > fallback:
        raise StateValidationError("job_started_at cannot be after job_finished_at")
    current_time: datetime | None = None
    matches: list[tuple[datetime, set[str], set[str], bool]] = []
    lines = log_text.splitlines() or [log_text]

    for line in lines:
        current_time = _line_timestamp(line, current_time)
        signatures = {identifier for identifier, pattern in SIGNATURE_PATTERNS if pattern.search(line)}
        if not signatures:
            continue
        # Target attribution stays on the matching physical line. Looking at
        # neighboring lines can fold an old Hugging Face host into a newer
        # GitHub episode when traceback output is interleaved.
        categories = _target_categories(line)
        trusted_time: datetime | None = None
        if current_time is not None and started is not None:
            tolerance = timedelta(seconds=LOG_CLOCK_TOLERANCE_SECONDS)
            if started - tolerance <= current_time <= fallback + tolerance:
                # Keep evidence inside the actual job interval even when an
                # agent clock is slightly skewed around a boundary.
                trusted_time = min(fallback, max(started, current_time))
        matches.append(
            (
                trusted_time or fallback,
                signatures,
                categories,
                trusted_time is not None,
            )
        )

    if not matches:
        return DnsClassification(
            match_count=0,
            episode_times=(),
            signature_ids=(),
            target_categories=(),
            time_basis="job_finished_at",
            episode_metrics=(),
        )

    episode_metrics = _episode_metrics(matches, EPISODE_GAP_SECONDS)
    episode_times = tuple(metric.at for metric in episode_metrics)
    signature_ids = tuple(sorted({value for entry in matches for value in entry[1]}))
    observed_targets = {value for entry in matches for value in entry[2]}
    targets = tuple(value for value in TARGET_CATEGORIES if value in observed_targets)
    time_basis: Literal["log_timestamp", "job_finished_at"] = (
        "log_timestamp" if any(entry[3] for entry in matches) else "job_finished_at"
    )
    return DnsClassification(
        match_count=len(matches),
        episode_times=episode_times,
        signature_ids=signature_ids,
        target_categories=targets,
        time_basis=time_basis,
        episode_metrics=episode_metrics,
    )


def evidence_id(pipeline: str, job_id: str) -> str:
    material = f"dns-evidence-v1\0{pipeline}\0{job_id}".encode()
    return hashlib.sha256(material).hexdigest()


def pending_record(metadata: dict, *, previous_attempts: int = 0) -> dict:
    """Return the strict durable-state record for a newly discovered job."""
    return {
        "pipeline": metadata["pipeline"],
        "build_number": metadata["build_number"],
        "job_id": metadata["job_id"],
        "queue": metadata["queue"],
        "node": metadata["node"],
        "hardware": metadata["hardware"],
        "state": metadata["state"],
        "started_at": metadata.get("started_at"),
        "finished_at": metadata["finished_at"],
        "status": "pending",
        "attempts": previous_attempts,
        "last_attempt_at": None,
    }


def scan_record(
    metadata: dict,
    classification: DnsClassification,
    *,
    attempted_at: str,
    previous_attempts: int = 0,
) -> dict:
    row = pending_record(metadata, previous_attempts=previous_attempts + 1)
    row["last_attempt_at"] = attempted_at
    if classification.positive:
        metrics = classification.episode_metrics
        if not metrics:
            if classification.match_count < len(classification.episode_times):
                raise ValueError("classification has fewer matches than episodes")
            metrics = tuple(
                DnsEpisodeMetric(
                    at=at,
                    match_count=(
                        classification.match_count - len(classification.episode_times) + 1
                        if index == 0
                        else 1
                    ),
                    signature_ids=classification.signature_ids,
                    target_categories=classification.target_categories,
                )
                for index, at in enumerate(classification.episode_times)
            )
        normalized_counts: dict[str, int] = {}
        normalized_signatures: dict[str, set[str]] = {}
        normalized_targets: dict[str, set[str]] = {}
        for metric in sorted(metrics, key=lambda value: parse_timestamp(value.at)):
            at = iso_timestamp(parse_timestamp(metric.at).replace(microsecond=0))
            normalized_counts[at] = normalized_counts.get(at, 0) + metric.match_count
            normalized_signatures.setdefault(at, set()).update(metric.signature_ids)
            normalized_targets.setdefault(at, set()).update(metric.target_categories)
        metrics = tuple(
            DnsEpisodeMetric(
                at=at,
                match_count=normalized_counts[at],
                signature_ids=tuple(sorted(normalized_signatures[at])),
                target_categories=tuple(
                    value
                    for value in TARGET_CATEGORIES
                    if value in normalized_targets[at]
                ),
            )
            for at in normalized_counts
        )
        metric_match_count = sum(metric.match_count for metric in metrics)
        metric_signature_ids = tuple(
            sorted({value for metric in metrics for value in metric.signature_ids})
        )
        observed_targets = {
            value for metric in metrics for value in metric.target_categories
        }
        metric_target_categories = tuple(
            value for value in TARGET_CATEGORIES if value in observed_targets
        )
        if (
            metric_match_count != classification.match_count
            or metric_signature_ids != classification.signature_ids
            or metric_target_categories != classification.target_categories
        ):
            raise ValueError("classification episode metrics do not reconcile")
        row.update(
            {
                "status": "positive",
                "match_count": classification.match_count,
                "episode_times": [metric.at for metric in metrics],
                "episode_metrics": [
                    {
                        "at": metric.at,
                        "match_count": metric.match_count,
                        "signature_ids": list(metric.signature_ids),
                        "target_categories": list(metric.target_categories),
                    }
                    for metric in metrics
                ],
                "signature_ids": list(metric_signature_ids),
                "target_categories": list(metric_target_categories),
                "time_basis": classification.time_basis,
            }
        )
    else:
        row["status"] = "negative"
    return row


def unavailable_record(
    metadata: dict,
    reason: str,
    *,
    attempted_at: str,
    previous_attempts: int = 0,
) -> dict:
    if reason not in UNAVAILABLE_REASONS:
        raise ValueError(f"unsupported unavailable reason: {reason}")
    row = pending_record(metadata, previous_attempts=previous_attempts + 1)
    row.update(
        {
            "status": "unavailable",
            "last_attempt_at": attempted_at,
            "unavailable_reason": reason,
        }
    )
    return row


def oversize_record(
    metadata: dict,
    log_bytes: int,
    *,
    attempted_at: str,
    previous_attempts: int = 0,
) -> dict:
    if isinstance(log_bytes, bool) or not isinstance(log_bytes, int) or log_bytes < 0:
        raise ValueError("log_bytes must be a non-negative integer")
    row = pending_record(metadata, previous_attempts=previous_attempts + 1)
    row.update(
        {
            "status": "oversize",
            "last_attempt_at": attempted_at,
            "log_bytes": log_bytes,
        }
    )
    return row


def empty_state(now: datetime, discovery_start: datetime) -> dict:
    end = iso_timestamp(now)
    start = iso_timestamp(now - timedelta(hours=RETENTION_HOURS))
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": STATE_KIND,
        "generated_at": end,
        "retention": {"start": start, "end_exclusive": end, "hours": RETENTION_HOURS},
        "discovery": {
            "start": iso_timestamp(discovery_start),
            "end_exclusive": end,
            "complete": True,
            "pipelines": list(PIPELINES),
            "include_retried_jobs": True,
        },
        "classifier": {
            "id": CLASSIFIER_ID,
            "episode_gap_seconds": EPISODE_GAP_SECONDS,
            "max_log_bytes": MAX_LOG_BYTES,
        },
        "jobs": [],
    }


def _validate_exact_keys(value: dict, expected: frozenset[str], field: str) -> None:
    if set(value) != expected:
        raise StateValidationError(f"{field} has unexpected keys")


def _validate_token(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not _SAFE_TOKEN_RE.fullmatch(value)
        or _SENSITIVE_VALUE_RE.search(value)
    ):
        raise StateValidationError(f"{field} is not a safe token")
    return value


def validate_state(payload: object) -> dict:
    """Validate and normalize decoded sanitized scanner state, failing closed."""
    if not isinstance(payload, dict):
        raise StateValidationError("state must be an object")
    _validate_exact_keys(payload, _STATE_TOP_KEYS, "state")
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("kind") != STATE_KIND:
        raise StateValidationError("unsupported state schema")
    generated = parse_timestamp(payload.get("generated_at"), "generated_at")
    if generated.microsecond or payload.get("generated_at") != iso_timestamp(generated):
        raise StateValidationError("generated_at must be canonical whole-second UTC")

    retention = payload.get("retention")
    if not isinstance(retention, dict):
        raise StateValidationError("retention must be an object")
    _validate_exact_keys(retention, frozenset({"start", "end_exclusive", "hours"}), "retention")
    if retention.get("hours") != RETENTION_HOURS:
        raise StateValidationError("retention hours do not match the contract")
    retention_start = parse_timestamp(retention.get("start"), "retention.start")
    retention_end = parse_timestamp(retention.get("end_exclusive"), "retention.end_exclusive")
    if retention_end != generated or retention_start != retention_end - timedelta(hours=RETENTION_HOURS):
        raise StateValidationError("retention boundaries are inconsistent")

    discovery = payload.get("discovery")
    if not isinstance(discovery, dict):
        raise StateValidationError("discovery must be an object")
    _validate_exact_keys(
        discovery,
        frozenset({"start", "end_exclusive", "complete", "pipelines", "include_retried_jobs"}),
        "discovery",
    )
    discovery_start = parse_timestamp(discovery.get("start"), "discovery.start")
    discovery_end = parse_timestamp(discovery.get("end_exclusive"), "discovery.end_exclusive")
    if discovery_end != generated or discovery_start >= discovery_end:
        raise StateValidationError("discovery boundaries are inconsistent")
    if discovery.get("complete") is not True:
        raise StateValidationError("persisted discovery must be exhaustive")
    if discovery.get("pipelines") != list(PIPELINES) or discovery.get("include_retried_jobs") is not True:
        raise StateValidationError("discovery scope does not match the contract")

    classifier = payload.get("classifier")
    if not isinstance(classifier, dict):
        raise StateValidationError("classifier must be an object")
    _validate_exact_keys(
        classifier,
        frozenset({"id", "episode_gap_seconds", "max_log_bytes"}),
        "classifier",
    )
    if classifier != {
        "id": CLASSIFIER_ID,
        "episode_gap_seconds": EPISODE_GAP_SECONDS,
        "max_log_bytes": MAX_LOG_BYTES,
    }:
        raise StateValidationError("classifier contract does not match this collector")

    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise StateValidationError("jobs must be a list")
    normalized_jobs: list[dict] = []
    identities: set[tuple[str, str]] = set()
    previous_sort: tuple[datetime, int, str] | None = None
    for index, raw in enumerate(jobs):
        if not isinstance(raw, dict):
            raise StateValidationError(f"jobs[{index}] must be an object")
        status = raw.get("status")
        if status not in SCAN_STATUSES:
            raise StateValidationError(f"jobs[{index}].status is invalid")
        _validate_exact_keys(
            raw,
            _STATE_BASE_JOB_KEYS | _STATE_STATUS_KEYS[status],
            f"jobs[{index}]",
        )
        pipeline = raw.get("pipeline")
        if pipeline not in PIPELINES:
            raise StateValidationError(f"jobs[{index}].pipeline is invalid")
        build_number = raw.get("build_number")
        if isinstance(build_number, bool) or not isinstance(build_number, int) or build_number <= 0:
            raise StateValidationError(f"jobs[{index}].build_number is invalid")
        job_id = canonical_uuid(raw.get("job_id"), f"jobs[{index}].job_id")
        identity = (pipeline, job_id)
        if identity in identities:
            raise StateValidationError("state contains a duplicate job identity")
        identities.add(identity)
        queue = _validate_token(raw.get("queue"), f"jobs[{index}].queue")
        hardware = raw.get("hardware")
        if not isinstance(hardware, str) or not _HARDWARE_RE.fullmatch(hardware):
            raise StateValidationError(f"jobs[{index}].hardware is invalid")
        if queue_hardware(queue) != hardware:
            raise StateValidationError(f"jobs[{index}] queue/hardware mismatch")
        node = raw.get("node")
        if node != "unidentified":
            _validate_token(node, f"jobs[{index}].node")
        if raw.get("state") not in JOB_STATES:
            raise StateValidationError(f"jobs[{index}].state is invalid")
        started = raw.get("started_at")
        parsed_started: datetime | None = None
        if started is not None:
            parsed_started = parse_timestamp(started, f"jobs[{index}].started_at")
        finished = parse_timestamp(raw.get("finished_at"), f"jobs[{index}].finished_at")
        if parsed_started is not None and parsed_started > finished:
            raise StateValidationError("started_at cannot be after finished_at")
        if not retention_start <= finished < retention_end:
            raise StateValidationError("job finished_at lies outside retained bounds")
        attempts = raw.get("attempts")
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
            raise StateValidationError(f"jobs[{index}].attempts is invalid")
        last_attempt = raw.get("last_attempt_at")
        if status == "pending":
            if last_attempt is not None:
                raise StateValidationError("pending jobs cannot have last_attempt_at")
        else:
            parse_timestamp(last_attempt, f"jobs[{index}].last_attempt_at")
            if attempts < 1:
                raise StateValidationError("attempted jobs must have attempts >= 1")

        if status == "positive":
            match_count = raw.get("match_count")
            if isinstance(match_count, bool) or not isinstance(match_count, int) or match_count <= 0:
                raise StateValidationError("positive match_count must be positive")
            episodes = raw.get("episode_times")
            if not isinstance(episodes, list) or not episodes:
                raise StateValidationError("positive jobs require episode_times")
            parsed_episodes = [parse_timestamp(value, "episode_times") for value in episodes]
            if any(
                value.microsecond or raw_value != iso_timestamp(value)
                for raw_value, value in zip(episodes, parsed_episodes)
            ):
                raise StateValidationError(
                    "episode_times must be canonical whole-second UTC"
                )
            if parsed_episodes != sorted(set(parsed_episodes)):
                raise StateValidationError("episode_times must be unique and sorted")
            if parsed_started is None:
                if any(value != finished for value in parsed_episodes):
                    raise StateValidationError(
                        "jobs without started_at must use finished_at episode time"
                    )
            elif any(
                not (parsed_started.replace(microsecond=0) <= value <= finished)
                for value in parsed_episodes
            ):
                raise StateValidationError("episode_times must lie inside the job interval")
            if match_count < len(parsed_episodes):
                raise StateValidationError("match_count cannot be smaller than episode count")
            episode_metrics = raw.get("episode_metrics")
            if not isinstance(episode_metrics, list) or not episode_metrics:
                raise StateValidationError("positive jobs require episode_metrics")
            metric_times: list[str] = []
            metric_match_count = 0
            metric_signatures: set[str] = set()
            metric_targets: set[str] = set()
            for metric_index, metric in enumerate(episode_metrics):
                if not isinstance(metric, dict):
                    raise StateValidationError("episode metric must be an object")
                _validate_exact_keys(
                    metric,
                    frozenset(
                        {"at", "match_count", "signature_ids", "target_categories"}
                    ),
                    f"jobs[{index}].episode_metrics[{metric_index}]",
                )
                metric_at = metric.get("at")
                parse_timestamp(metric_at, "episode_metrics.at")
                metric_times.append(metric_at)
                metric_count = metric.get("match_count")
                if (
                    isinstance(metric_count, bool)
                    or not isinstance(metric_count, int)
                    or metric_count <= 0
                ):
                    raise StateValidationError("episode metric match_count is invalid")
                metric_match_count += metric_count
                metric_signature_ids = metric.get("signature_ids")
                if (
                    not isinstance(metric_signature_ids, list)
                    or not metric_signature_ids
                    or metric_signature_ids != sorted(set(metric_signature_ids))
                    or not set(metric_signature_ids) <= set(SIGNATURE_IDS)
                ):
                    raise StateValidationError("episode metric signature_ids are invalid")
                metric_signatures.update(metric_signature_ids)
                metric_target_categories = metric.get("target_categories")
                if (
                    not isinstance(metric_target_categories, list)
                    or not metric_target_categories
                    or metric_target_categories
                    != [
                        value
                        for value in TARGET_CATEGORIES
                        if value in set(metric_target_categories)
                    ]
                    or not set(metric_target_categories) <= set(TARGET_CATEGORIES)
                ):
                    raise StateValidationError(
                        "episode metric target_categories are invalid"
                    )
                metric_targets.update(metric_target_categories)
            if metric_times != episodes:
                raise StateValidationError("episode metrics do not match episode_times")
            if metric_match_count != match_count:
                raise StateValidationError("episode metric counts do not match match_count")
            signature_ids = raw.get("signature_ids")
            if (
                not isinstance(signature_ids, list)
                or not signature_ids
                or signature_ids != sorted(set(signature_ids))
                or not set(signature_ids) <= set(SIGNATURE_IDS)
            ):
                raise StateValidationError("signature_ids are invalid")
            if signature_ids != sorted(metric_signatures):
                raise StateValidationError("episode signatures do not match job signatures")
            target_categories = raw.get("target_categories")
            if (
                not isinstance(target_categories, list)
                or not target_categories
                or target_categories
                != [value for value in TARGET_CATEGORIES if value in set(target_categories)]
                or not set(target_categories) <= set(TARGET_CATEGORIES)
            ):
                raise StateValidationError("target_categories are invalid")
            if target_categories != [
                value for value in TARGET_CATEGORIES if value in metric_targets
            ]:
                raise StateValidationError("episode targets do not match job targets")
            if raw.get("time_basis") not in TIME_BASES:
                raise StateValidationError("time_basis is invalid")
        elif status == "unavailable":
            if raw.get("unavailable_reason") not in UNAVAILABLE_REASONS:
                raise StateValidationError("unavailable_reason is invalid")
        elif status == "oversize":
            log_bytes = raw.get("log_bytes")
            if isinstance(log_bytes, bool) or not isinstance(log_bytes, int) or log_bytes <= MAX_LOG_BYTES:
                raise StateValidationError("oversize log_bytes must exceed the byte cap")

        sort_key = (finished, build_number, job_id)
        if previous_sort is not None and sort_key > previous_sort:
            raise StateValidationError("jobs must be sorted newest first")
        previous_sort = sort_key
        normalized_jobs.append(dict(raw))

    return dict(payload) | {"jobs": normalized_jobs}


def state_bytes(payload: object) -> bytes:
    """Return deterministic gzip bytes for one validated state."""
    normalized = validate_state(payload)
    encoded = json.dumps(
        normalized,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    return gzip.compress(encoded, compresslevel=9, mtime=0)


def state_from_bytes(compressed: bytes) -> dict:
    if not isinstance(compressed, bytes):
        raise StateValidationError("compressed state must be bytes")
    if len(compressed) > 64 * 1024 * 1024:
        raise StateValidationError("compressed state exceeds the safety limit")
    try:
        decoded = gzip.decompress(compressed)
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise StateValidationError("state is not valid gzip") from exc
    if len(decoded) > 256 * 1024 * 1024:
        raise StateValidationError("decompressed state exceeds the safety limit")
    try:
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateValidationError("state is not valid UTF-8 JSON") from exc
    return validate_state(payload)


def load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise StateValidationError(f"state path is not a file: {path}")
    return state_from_bytes(path.read_bytes())


def write_state(path: Path, payload: object) -> None:
    """Atomically write deterministic gzip state after strict validation."""
    encoded = state_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)


def sort_state_jobs(rows: Iterable[dict]) -> list[dict]:
    return sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            parse_timestamp(row["finished_at"], "finished_at"),
            row["build_number"],
            row["job_id"],
        ),
        reverse=True,
    )


def prune_state_jobs(rows: Iterable[dict], start: datetime, end_exclusive: datetime) -> list[dict]:
    return sort_state_jobs(
        row
        for row in rows
        if start <= parse_timestamp(row["finished_at"], "finished_at") < end_exclusive
    )


def _record_rank(row: dict) -> tuple[int, int, str]:
    status_rank = {"pending": 0, "unavailable": 1, "oversize": 2, "negative": 3, "positive": 3}
    return (
        status_rank[row["status"]],
        int(row.get("attempts") or 0),
        str(row.get("last_attempt_at") or ""),
    )


def merge_state_jobs(*collections: Iterable[dict]) -> list[dict]:
    """Merge validated records by pipeline/job UUID, failing on final conflicts."""
    merged: dict[tuple[str, str], dict] = {}
    for rows in collections:
        for row in rows:
            identity = (row["pipeline"], row["job_id"])
            previous = merged.get(identity)
            if previous is None:
                merged[identity] = dict(row)
                continue
            if (
                previous["status"] in {"positive", "negative"}
                and row["status"] in {"positive", "negative"}
                and previous["status"] != row["status"]
            ):
                raise StateValidationError(f"conflicting final DNS scans for {identity}")
            if _record_rank(row) > _record_rank(previous):
                merged[identity] = dict(row)
    return sort_state_jobs(merged.values())


def _status_counts(rows: Iterable[dict]) -> dict[str, int]:
    counts = {status: 0 for status in SCAN_STATUSES}
    for row in rows:
        counts[row["status"]] += 1
    return counts


def _coverage(rows: list[dict], *, discovery_complete: bool) -> dict:
    counts = _status_counts(rows)
    scanned = counts["positive"] + counts["negative"]
    complete = bool(
        discovery_complete
        and counts["pending"] == 0
        and counts["unavailable"] == 0
        and counts["oversize"] == 0
    )
    return {
        "status": "complete" if complete else "partial",
        "complete": complete,
        "discovery_complete": discovery_complete,
        "eligible_jobs": len(rows),
        "scanned_jobs": scanned,
        "positive_jobs": counts["positive"],
        "negative_jobs": counts["negative"],
        "pending_jobs": counts["pending"],
        "unavailable_jobs": counts["unavailable"],
        "oversize_jobs": counts["oversize"],
    }


def _window_coverage(
    rows: list[dict],
    *,
    start: datetime,
    end_exclusive: datetime,
    discovery_complete: bool,
) -> dict:
    """Classify fully scanned jobs relative to one half-open episode window.

    A job can finish in a short window after its only DNS episode has aged out
    of that window. Such a scan is negative *for this window*, while pending,
    unavailable, and oversize states remain gaps based on finish-time
    eligibility.
    """
    relative_rows: list[dict] = []
    for row in rows:
        relative = row
        if row["status"] == "positive" and not _positive_in_window(
            row, start, end_exclusive
        ):
            relative = dict(row)
            relative["status"] = "negative"
        relative_rows.append(relative)
    return _coverage(relative_rows, discovery_complete=discovery_complete)


def _episode_metrics_in_window(
    row: dict,
    start: datetime,
    end_exclusive: datetime,
) -> list[dict]:
    if row["status"] != "positive":
        return []
    return [
        metric
        for metric in row["episode_metrics"]
        if start <= parse_timestamp(metric["at"], "episode_time") < end_exclusive
    ]


def _positive_in_window(row: dict, start: datetime, end_exclusive: datetime) -> list[str]:
    return [
        metric["at"]
        for metric in _episode_metrics_in_window(row, start, end_exclusive)
    ]


def _episode_summary(metrics: list[dict]) -> dict:
    signatures = sorted(
        {value for metric in metrics for value in metric["signature_ids"]}
    )
    observed_targets = {
        value for metric in metrics for value in metric["target_categories"]
    }
    return {
        "first_at": metrics[0]["at"],
        "last_at": metrics[-1]["at"],
        "episodes": len(metrics),
        "match_count": sum(metric["match_count"] for metric in metrics),
        "signature_ids": signatures,
        "target_categories": [
            value for value in TARGET_CATEGORIES if value in observed_targets
        ],
    }


def _evidence_item(row: dict, end_exclusive: datetime) -> dict:
    retained_metrics = _episode_metrics_in_window(
        row,
        end_exclusive - timedelta(hours=RETENTION_HOURS),
        end_exclusive,
    )
    retained_summary = _episode_summary(retained_metrics)
    window_metrics: dict[str, dict] = {}
    for option in WINDOW_OPTIONS:
        metrics = _episode_metrics_in_window(
            row,
            end_exclusive - timedelta(hours=option["hours"]),
            end_exclusive,
        )
        if metrics:
            window_metrics[option["id"]] = _episode_summary(metrics)
    return {
        "id": evidence_id(row["pipeline"], row["job_id"]),
        "first_at": retained_summary["first_at"],
        "last_at": retained_summary["last_at"],
        "time_basis": row["time_basis"],
        "pipeline": row["pipeline"],
        "queue": row["queue"],
        "node": row["node"],
        "hardware": row["hardware"],
        "build_number": row["build_number"],
        "job_id": row["job_id"],
        "state": row["state"],
        "episodes": retained_summary["episodes"],
        "match_count": retained_summary["match_count"],
        "signature_ids": retained_summary["signature_ids"],
        "target_categories": retained_summary["target_categories"],
        "window_ids": list(window_metrics),
        "window_metrics": window_metrics,
    }


def build_public_output(state: object) -> dict:
    """Project validated sanitized scanner state into the public schema."""
    payload = validate_state(state)
    end = parse_timestamp(payload["generated_at"], "generated_at")
    retention_start = end - timedelta(hours=RETENTION_HOURS)
    discovery_start = parse_timestamp(payload["discovery"]["start"], "discovery.start")
    jobs = payload["jobs"]

    windows: dict[str, dict] = {}
    for option in WINDOW_OPTIONS:
        start = end - timedelta(hours=option["hours"])
        window_jobs = [
            row
            for row in jobs
            if start <= parse_timestamp(row["finished_at"], "finished_at") < end
        ]
        discovery_complete = bool(payload["discovery"]["complete"] and start >= discovery_start)
        coverage = _window_coverage(
            window_jobs,
            start=start,
            end_exclusive=end,
            discovery_complete=discovery_complete,
        )
        positives = [
            (row, _episode_metrics_in_window(row, start, end))
            for row in window_jobs
            if row["status"] == "positive"
        ]
        positives = [(row, episodes) for row, episodes in positives if episodes]
        grouped: dict[tuple[str, str, str], dict] = {}
        for row, episode_metrics in positives:
            key = (row["queue"], row["node"], row["hardware"])
            bucket = grouped.setdefault(
                key,
                {
                    "queue": row["queue"],
                    "node": row["node"],
                    "hardware": row["hardware"],
                    "affected_jobs": 0,
                    "episodes": 0,
                    "huggingface_affected_jobs": 0,
                    "evidence_total": 0,
                },
            )
            bucket["affected_jobs"] += 1
            bucket["episodes"] += len(episode_metrics)
            bucket["huggingface_affected_jobs"] += int(
                any(
                    "huggingface_hub" in metric["target_categories"]
                    for metric in episode_metrics
                )
            )
            bucket["evidence_total"] += 1
        rows = sorted(grouped.values(), key=lambda row: (row["queue"], row["node"]))
        windows[option["id"]] = {
            "start": iso_timestamp(start),
            "end_exclusive": iso_timestamp(end),
            "coverage": coverage,
            "totals": {
                "affected_jobs": len(positives),
                "episodes": sum(len(episodes) for _, episodes in positives),
                "huggingface_affected_jobs": sum(
                    any(
                        "huggingface_hub" in metric["target_categories"]
                        for metric in episode_metrics
                    )
                    for _, episode_metrics in positives
                ),
                "queues": len({row["queue"] for row, _ in positives}),
                "nodes": len({row["node"] for row, _ in positives}),
                "evidence_total": len(positives),
            },
            "rows": rows,
        }

    evidence_rows = [
        row
        for row in jobs
        if row["status"] == "positive"
        and _positive_in_window(row, retention_start, end)
    ]
    evidence_items = [_evidence_item(row, end) for row in evidence_rows]
    evidence_items.sort(
        key=lambda item: (
            item["last_at"],
            item["first_at"],
            item["pipeline"],
            item["build_number"],
            item["job_id"],
        ),
        reverse=True,
    )
    shown: list[dict] = []
    shown_bytes = 2  # JSON list delimiters.
    for item in evidence_items[:PUBLIC_EVIDENCE_LIMIT]:
        item_bytes = len(
            json.dumps(
                item,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        separator_bytes = 1 if shown else 0
        if shown_bytes + separator_bytes + item_bytes > PUBLIC_EVIDENCE_BYTE_BUDGET:
            break
        shown.append(item)
        shown_bytes += separator_bytes + item_bytes

    top_coverage = _window_coverage(
        [
            row
            for row in jobs
            if retention_start <= parse_timestamp(row["finished_at"], "finished_at") < end
        ],
        start=retention_start,
        end_exclusive=end,
        discovery_complete=bool(
            payload["discovery"]["complete"] and discovery_start <= retention_start
        ),
    )
    top_coverage = {
        "status": top_coverage["status"],
        "complete": top_coverage["complete"],
        "discovery_complete": top_coverage["discovery_complete"],
        "discovery_start": payload["discovery"]["start"],
        "discovery_end_exclusive": payload["discovery"]["end_exclusive"],
        **{key: value for key, value in top_coverage.items() if key.endswith("_jobs")},
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": payload["generated_at"],
        "retention": {
            "start": iso_timestamp(retention_start),
            "end_exclusive": iso_timestamp(end),
            "hours": RETENTION_HOURS,
        },
        "default_window": DEFAULT_WINDOW,
        "window_options": [dict(option) for option in WINDOW_OPTIONS],
        "count_basis": COUNT_BASIS,
        "scope": {
            "organization": "vllm",
            "pipelines": list(PIPELINES),
            "branches": "all",
            "job_types": ["script"],
            "states": list(JOB_STATES),
            "queue_scope": "active_amd_gpu",
            "retried_jobs": "included",
        },
        "classifier": {
            "id": CLASSIFIER_ID,
            "episode_gap_seconds": EPISODE_GAP_SECONDS,
            "max_log_bytes": MAX_LOG_BYTES,
            "target_categories": list(TARGET_CATEGORIES),
        },
        "coverage": top_coverage,
        "windows": windows,
        "evidence": {
            "evidence_total": len(evidence_items),
            "shown": len(shown),
            "truncated": len(shown) < len(evidence_items),
            "items": shown,
        },
    }


def write_public_output(path: Path, payload: dict) -> None:
    """Write a deterministic JSON projection atomically."""
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(encoded)
    temporary.replace(path)
