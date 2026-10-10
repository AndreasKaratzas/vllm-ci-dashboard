"""Strict private cache for privacy-minimized DNS log classifications.

Core CI already downloads a subset of Buildkite job logs for pytest parsing.
This cache retains only the DNS classifier's fixed enums, counts, canonical
timestamps, and Buildkite identity so the independent DNS collector can reuse
that work. Raw log text, labels, URLs, queue names, and environment values are
outside the exact row schema and can never be serialized here.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .dns_failures import (
    CLASSIFIER_ID,
    PIPELINES,
    DnsClassification,
    StateValidationError,
    canonical_uuid,
    classification_from_payload,
    classification_payload,
    classify_dns_log,
    iso_timestamp,
    parse_timestamp,
    queue_hardware,
    utc_now,
)

CACHE_SCHEMA_VERSION = 1
CACHE_KIND = "vllm-ci-dns-classification"
CACHE_DIRECTORY_NAME = "dns-classifications-v1"
CACHE_RETENTION_DAYS = 35
MAX_COMPRESSED_SHARD_BYTES = 8 * 1024 * 1024
MAX_COMPRESSED_TOTAL_BYTES = 64 * 1024 * 1024
MAX_UNCOMPRESSED_SHARD_BYTES = 32 * 1024 * 1024
_SHARD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl\.gz$")
_STAGED_SHARD_RE = re.compile(
    r"^\.\d{4}-\d{2}-\d{2}\.jsonl\.gz\.\d+\.tmp$"
)
_ROW_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "classifier_id",
        "pipeline",
        "build_number",
        "job_id",
        "started_at",
        "finished_at",
        "classification",
    }
)
_QUEUE_RULE_RE = re.compile(r"^queue=(.+)$", re.IGNORECASE)
_DNS_JOB_STATES = frozenset(
    {"passed", "failed", "timed_out", "broken", "expired", "soft_failed", "soft_fail"}
)


class DnsClassificationCacheError(StateValidationError):
    """A private DNS classification shard violates its strict contract."""


def _cache_clock(value: datetime | None) -> datetime:
    clock = value or utc_now()
    if not isinstance(clock, datetime) or clock.tzinfo is None:
        raise ValueError("DNS classification cache clock must be timezone-aware")
    return clock.astimezone(timezone.utc).replace(microsecond=0)


def _job_queue(job: dict) -> str:
    for rule in job.get("agent_query_rules") or []:
        match = _QUEUE_RULE_RE.match(str(rule).strip())
        if match:
            return match.group(1).strip().casefold()
    for tag in (job.get("agent") or {}).get("meta_data") or []:
        if isinstance(tag, str) and tag.casefold().startswith("queue="):
            return tag.split("=", 1)[1].strip().casefold()
    return ""


def _normalized_timestamp(value: object, field: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        return iso_timestamp(parse_timestamp(value, field).replace(microsecond=0))
    except StateValidationError:
        return None


def _validated_row(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != _ROW_KEYS:
        raise DnsClassificationCacheError("DNS classification cache row has unexpected keys")
    if (
        payload.get("schema_version") != CACHE_SCHEMA_VERSION
        or payload.get("kind") != CACHE_KIND
        or payload.get("classifier_id") != CLASSIFIER_ID
    ):
        raise DnsClassificationCacheError("unsupported DNS classification cache schema")
    pipeline = payload.get("pipeline")
    if pipeline not in PIPELINES:
        raise DnsClassificationCacheError("DNS classification cache pipeline is invalid")
    build_number = payload.get("build_number")
    if isinstance(build_number, bool) or not isinstance(build_number, int) or build_number <= 0:
        raise DnsClassificationCacheError("DNS classification cache build number is invalid")
    try:
        job_id = canonical_uuid(payload.get("job_id"), "cache job_id")
        finished = parse_timestamp(payload.get("finished_at"), "cache finished_at")
        finished_at = iso_timestamp(finished.replace(microsecond=0))
    except StateValidationError as exc:
        raise DnsClassificationCacheError(str(exc)) from exc
    if payload.get("finished_at") != finished_at:
        raise DnsClassificationCacheError("cache finished_at must be canonical whole-second UTC")
    started_at = payload.get("started_at")
    started: datetime | None = None
    if started_at is not None:
        try:
            started = parse_timestamp(started_at, "cache started_at")
        except StateValidationError as exc:
            raise DnsClassificationCacheError(str(exc)) from exc
        canonical_started = iso_timestamp(started.replace(microsecond=0))
        if started_at != canonical_started or started > finished:
            raise DnsClassificationCacheError("cache started_at is invalid")
    try:
        classification = classification_from_payload(payload.get("classification"))
    except StateValidationError as exc:
        raise DnsClassificationCacheError(str(exc)) from exc
    episode_times = [parse_timestamp(value, "cache episode time") for value in classification.episode_times]
    if started is None:
        if episode_times and any(value != finished for value in episode_times):
            raise DnsClassificationCacheError("cache episode lies outside the job interval")
        if classification.time_basis == "log_timestamp":
            raise DnsClassificationCacheError("timestamp-based cache evidence lacks started_at")
    elif any(value < started or value > finished for value in episode_times):
        raise DnsClassificationCacheError("cache episode lies outside the job interval")
    if classification.time_basis == "job_finished_at" and any(
        value != finished for value in episode_times
    ):
        raise DnsClassificationCacheError("fallback cache episode is not at job finish")
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "kind": CACHE_KIND,
        "classifier_id": CLASSIFIER_ID,
        "pipeline": pipeline,
        "build_number": build_number,
        "job_id": job_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "classification": classification_payload(classification),
    }


def _bounded_gzip_payload(path: Path) -> bytes:
    size = path.stat().st_size
    if size <= 0 or size > MAX_COMPRESSED_SHARD_BYTES:
        raise DnsClassificationCacheError("DNS classification cache shard exceeds its size limit")
    try:
        compressed = path.read_bytes()
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
            decoded = stream.read(MAX_UNCOMPRESSED_SHARD_BYTES + 1)
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise DnsClassificationCacheError("DNS classification cache shard is not valid gzip") from exc
    if len(decoded) > MAX_UNCOMPRESSED_SHARD_BYTES:
        raise DnsClassificationCacheError(
            "DNS classification cache shard exceeds its decompressed size limit"
        )
    if not decoded or not decoded.endswith(b"\n"):
        raise DnsClassificationCacheError("DNS classification cache shard is incomplete")
    return decoded


def _decode_shard(path: Path, shard_date: str) -> list[dict]:
    try:
        text = _bounded_gzip_payload(path).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DnsClassificationCacheError("DNS classification cache shard is not UTF-8") from exc
    rows: list[dict] = []
    previous_identity: tuple[str, str] | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DnsClassificationCacheError(
                f"DNS classification cache shard has invalid JSON on line {line_number}"
            ) from exc
        row = _validated_row(decoded)
        if row["finished_at"][:10] != shard_date:
            raise DnsClassificationCacheError("DNS classification cache row is in the wrong shard")
        identity = (row["pipeline"], row["job_id"])
        if previous_identity is not None and identity <= previous_identity:
            raise DnsClassificationCacheError(
                "DNS classification cache rows are duplicate or unsorted"
            )
        previous_identity = identity
        rows.append(row)
    return rows


def _encode_shard(rows: list[dict]) -> bytes:
    body = b"".join(
        json.dumps(row, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        + b"\n"
        for row in rows
    )
    if len(body) > MAX_UNCOMPRESSED_SHARD_BYTES:
        raise DnsClassificationCacheError(
            "DNS classification cache shard exceeds its decompressed size limit"
        )
    compressed = gzip.compress(body, compresslevel=9, mtime=0)
    if len(compressed) > MAX_COMPRESSED_SHARD_BYTES:
        raise DnsClassificationCacheError("DNS classification cache shard exceeds its size limit")
    return compressed


def _encode_bounded_shard(rows: list[dict]) -> tuple[bytes | None, list[dict]]:
    """Keep a deterministic newest subset that fits one bounded daily shard."""
    newest_first = sorted(
        rows,
        key=lambda row: (row["finished_at"], row["pipeline"], row["job_id"]),
        reverse=True,
    )
    low = 0
    high = len(newest_first)
    best_payload: bytes | None = None
    best_rows: list[dict] = []
    while low <= high:
        count = (low + high) // 2
        candidate = sorted(
            newest_first[:count],
            key=lambda row: (row["pipeline"], row["job_id"]),
        )
        try:
            payload = _encode_shard(candidate)
        except DnsClassificationCacheError:
            high = count - 1
            continue
        best_payload = payload
        best_rows = candidate
        low = count + 1
    if not best_rows:
        return None, []
    return best_payload, best_rows


class DnsClassificationCache:
    """Thread-safe in-memory view of validated bounded daily cache shards."""

    def __init__(self, path: Path, *, now: datetime | None = None) -> None:
        self.path = Path(path)
        self.now = _cache_clock(now)
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str], dict] = {}
        self._load()

    def _load(self) -> None:
        # ``exists`` follows symlinks and is false for a broken one. Reject the
        # link explicitly so upload validation cannot treat it as an empty cache.
        if self.path.is_symlink():
            raise DnsClassificationCacheError("DNS classification cache path is not a directory")
        if not self.path.exists():
            return
        if not self.path.is_dir():
            raise DnsClassificationCacheError("DNS classification cache path is not a directory")
        entries = sorted(self.path.iterdir())
        if any(
            not entry.is_file()
            or entry.is_symlink()
            or not _SHARD_RE.fullmatch(entry.name)
            for entry in entries
        ):
            raise DnsClassificationCacheError("DNS classification cache contains an invalid path")
        total_size = sum(entry.stat().st_size for entry in entries)
        if total_size > MAX_COMPRESSED_TOTAL_BYTES:
            raise DnsClassificationCacheError("DNS classification cache exceeds its total size limit")
        cutoff = self.now.date() - timedelta(days=CACHE_RETENTION_DAYS - 1)
        seen_identities: set[tuple[str, str]] = set()
        for entry in entries:
            shard_date = entry.name[:10]
            try:
                parsed_date = datetime.strptime(shard_date, "%Y-%m-%d").date()
            except ValueError as exc:
                raise DnsClassificationCacheError("DNS classification cache shard date is invalid") from exc
            if parsed_date > self.now.date():
                raise DnsClassificationCacheError("DNS classification cache contains a future shard")
            rows = _decode_shard(entry, shard_date)
            for row in rows:
                if parse_timestamp(row["finished_at"], "cache finished_at") > self.now:
                    raise DnsClassificationCacheError(
                        "DNS classification cache contains a future row"
                    )
                identity = (row["pipeline"], row["job_id"])
                if identity in seen_identities:
                    raise DnsClassificationCacheError(
                        "DNS classification cache contains a duplicate job identity"
                    )
                seen_identities.add(identity)
                if parsed_date >= cutoff:
                    self._rows[identity] = row

    def observe_job_log(
        self,
        *,
        job: dict,
        pipeline: str,
        build_number: int,
        log_text: str,
    ) -> bool:
        """Classify an already-downloaded eligible AMD job log in memory."""
        if pipeline not in PIPELINES or not isinstance(job, dict) or not isinstance(log_text, str):
            return False
        state = str(job.get("state") or "").casefold()
        if state not in _DNS_JOB_STATES and not job.get("soft_failed"):
            return False
        if not queue_hardware(_job_queue(job)):
            return False
        if isinstance(build_number, bool) or not isinstance(build_number, int) or build_number <= 0:
            return False
        try:
            job_id = canonical_uuid(job.get("id"), "job_id")
        except StateValidationError:
            return False
        finished_at = _normalized_timestamp(job.get("finished_at"), "finished_at")
        if finished_at is None:
            return False
        started_at = _normalized_timestamp(job.get("started_at"), "started_at")
        if started_at is not None and parse_timestamp(started_at) > parse_timestamp(finished_at):
            started_at = None
        classification = classify_dns_log(
            log_text,
            job_finished_at=finished_at,
            job_started_at=started_at,
        )
        row = _validated_row(
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "kind": CACHE_KIND,
                "classifier_id": CLASSIFIER_ID,
                "pipeline": pipeline,
                "build_number": build_number,
                "job_id": job_id,
                "started_at": started_at,
                "finished_at": finished_at,
                "classification": classification_payload(classification),
            }
        )
        finished_day = parse_timestamp(finished_at).date()
        cutoff = self.now.date() - timedelta(days=CACHE_RETENTION_DAYS - 1)
        if not cutoff <= finished_day <= self.now.date():
            return False
        identity = (pipeline, job_id)
        with self._lock:
            # The just-downloaded raw log is the authoritative observation.
            # A restored row can be stale when Buildkite corrects timestamps or
            # a classifier deployment replaces older private cache evidence.
            self._rows[identity] = row
        return True

    def classification_for(self, metadata: dict) -> DnsClassification | None:
        """Return a classification only when discovery metadata exactly matches."""
        identity = (metadata.get("pipeline"), metadata.get("job_id"))
        with self._lock:
            row = self._rows.get(identity)
            row = dict(row) if row is not None else None
        if row is None:
            return None
        expected = {
            "build_number": metadata.get("build_number"),
            "started_at": metadata.get("started_at"),
            "finished_at": metadata.get("finished_at"),
        }
        if any(row[key] != value for key, value in expected.items()):
            # Discovery metadata is authoritative. Never apply a classification
            # to a possibly-reused/corrected identity; fetch the log normally.
            return None
        return classification_from_payload(row["classification"])

    def flush(self, *, now: datetime | None = None) -> dict[str, int]:
        """Atomically replace bounded daily shards and prune expired days."""
        clock = _cache_clock(now) if now is not None else self.now
        cutoff = clock.date() - timedelta(days=CACHE_RETENTION_DAYS - 1)
        with self._lock:
            rows = [
                dict(row)
                for row in self._rows.values()
                if cutoff <= parse_timestamp(row["finished_at"]).date()
                and parse_timestamp(row["finished_at"]) <= clock
            ]
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row["finished_at"][:10], []).append(row)
        encoded: dict[str, bytes] = {}
        retained_identities: set[tuple[str, str]] = set()
        for day, day_rows in grouped.items():
            payload, retained_rows = _encode_bounded_shard(day_rows)
            if payload is None:
                continue
            encoded[f"{day}.jsonl.gz"] = payload
            retained_identities.update(
                (row["pipeline"], row["job_id"]) for row in retained_rows
            )
        # Size pressure evicts the oldest complete day. Missing private cache
        # evidence only causes the DNS collector to fetch that job log normally.
        while sum(len(payload) for payload in encoded.values()) > MAX_COMPRESSED_TOTAL_BYTES:
            removed = min(encoded)
            del encoded[removed]
            removed_day = removed[:10]
            retained_identities = {
                identity
                for identity in retained_identities
                if self._rows[identity]["finished_at"][:10] != removed_day
            }

        self.path.mkdir(parents=True, exist_ok=True)
        staged: dict[str, Path] = {}
        try:
            for name, payload in encoded.items():
                temporary = self.path / f".{name}.{os.getpid()}.tmp"
                temporary.write_bytes(payload)
                os.chmod(temporary, 0o600)
                staged[name] = temporary
            for name, temporary in staged.items():
                os.replace(temporary, self.path / name)
            for existing in self.path.iterdir():
                if (
                    existing.name not in encoded
                    and _SHARD_RE.fullmatch(existing.name)
                    and existing.is_file()
                    and not existing.is_symlink()
                ):
                    existing.unlink()
        finally:
            for temporary in staged.values():
                if temporary.exists():
                    temporary.unlink()

        with self._lock:
            self._rows = {
                identity: row
                for identity, row in self._rows.items()
                if identity in retained_identities
            }
        return {
            "shards": len(encoded),
            "classifications": len(self._rows),
            "compressed_bytes": sum(len(payload) for payload in encoded.values()),
        }

    def __len__(self) -> int:
        with self._lock:
            return len(self._rows)


def load_optional_dns_classification_cache(
    path: Path,
    *,
    now: datetime | None = None,
) -> tuple[DnsClassificationCache | None, bool]:
    """Load an optional restored cache, discarding invalid state as a miss.

    ``DnsClassificationCache`` remains the strict validation boundary. This
    availability wrapper is for collectors: an untrusted or partially restored
    private cache must never be consumed, but it must not prevent the normal
    Buildkite-backed collection path either. The boolean reports that restored
    state was rejected without exposing its contents or validation detail.
    """
    cache_path = Path(path)
    try:
        return DnsClassificationCache(cache_path, now=now), False
    except (DnsClassificationCacheError, OSError):
        pass

    try:
        # Never remove or rename a caller-selected root. Reset is authorized
        # only for an existing, non-symlink, flat directory containing nothing
        # except files with the private cache shard naming contract. This makes
        # broad paths (including /) and unexpected restored contents a disabled
        # cache rather than a destructive cleanup target.
        if (
            cache_path == Path(cache_path.anchor)
            or cache_path.name != CACHE_DIRECTORY_NAME
            or not cache_path.is_dir()
            or cache_path.is_symlink()
        ):
            return None, True
        entries = list(cache_path.iterdir())
        if any(
            not entry.is_file()
            or entry.is_symlink()
            or not (
                _SHARD_RE.fullmatch(entry.name)
                or _STAGED_SHARD_RE.fullmatch(entry.name)
            )
            for entry in entries
        ):
            return None, True
        for entry in entries:
            entry.unlink()
        return DnsClassificationCache(cache_path, now=now), True
    except (DnsClassificationCacheError, OSError):
        # A read-only/broken cache mount disables reuse for this run. Core CI
        # still parses normally and DNS still falls back to its own log GETs.
        return None, True
