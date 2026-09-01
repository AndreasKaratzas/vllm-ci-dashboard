"""JSON/JSONL output generation for the CI dashboard."""

import json
import hashlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..bounded_json import pretty_json_bytes, write_pretty_json_lkg
from ..dashboard_storage_budget import writer_max_bytes
from .models import (
    PASS_RATE_CONTRACT_VERSION,
    BuildSummary,
    TestHealth,
    TestResult,
)

log = logging.getLogger(__name__)

TEST_RESULT_SHARD_MAX_BYTES = writer_max_bytes("test_result_shard")
TEST_RESULT_STORE_MAX_BYTES = writer_max_bytes("test_result_store")
TEST_RESULT_RETENTION_MAX_BYTES = writer_max_bytes("test_result_retention")
TEST_RESULT_RETENTION_FILE = "retention.json"
CI_HEALTH_MAX_BYTES = writer_max_bytes("ci_health")
CI_PARITY_PAIR_MAX_BYTES = writer_max_bytes("ci_parity_pair")
FAILURE_TRENDS_MAX_BYTES = writer_max_bytes("failure_trends")
FLAKY_TESTS_MAX_BYTES = writer_max_bytes("flaky_tests")
QUARANTINE_REPORT_MAX_BYTES = writer_max_bytes("quarantine_report")
_RESULT_FILE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_(amd|upstream)\.jsonl")


@dataclass(frozen=True)
class _ResultRetentionPlan:
    removals: tuple[Path, ...]
    retained_start: str | None
    retained_end: str | None
    byte_limited: bool
    replacement_retained: bool

HEALTH_LABEL_BUCKETS = (
    "passing",
    "failing",
    "new_failure",
    "fixed",
    "flaky",
    "skipped",
    "new_test",
    "quarantined",
    "allowlisted",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _collection_prefix(value: list | dict, limit: int) -> list | dict:
    if isinstance(value, list):
        return value[:limit]
    return dict(list(value.items())[:limit])


def _compact_collection_fields(
    source: dict,
    *,
    fields: tuple[str, ...],
    max_bytes: int,
    policy: str,
) -> dict:
    """Bound whole list/dict entries with source-relative accounting."""
    collections = {
        field: value
        for field in fields
        if isinstance((value := source.get(field)), (list, dict))
    }
    maximum = max((len(value) for value in collections.values()), default=0)

    def candidate(limit: int) -> dict:
        result = {
            key: value
            for key, value in source.items()
            if key not in collections and key != "publication_retention"
        }
        counts = {}
        for field, value in collections.items():
            retained = _collection_prefix(value, limit)
            result[field] = retained
            counts[field] = {
                "source": len(value),
                "published": len(retained),
                "omitted": len(value) - len(retained),
                "complete": len(retained) == len(value),
            }
        complete = all(row["complete"] for row in counts.values())
        result["publication_retention"] = {
            "policy": policy,
            "max_bytes": max_bytes,
            "complete_relative_to_source": complete,
            "collections": counts,
        }
        return result

    smallest = candidate(0)
    if len(pretty_json_bytes(smallest)) > max_bytes:
        raise RuntimeError(
            "fixed dashboard snapshot metadata exceeds its byte budget; "
            "preserving the last-known-good file"
        )
    low = 0
    high = maximum
    while low < high:
        middle = (low + high + 1) // 2
        if len(pretty_json_bytes(candidate(middle))) <= max_bytes:
            low = middle
        else:
            high = middle - 1
    return candidate(low)


def _compact_ci_health(source: dict, *, max_bytes: int) -> dict:
    """Retain newest whole build summaries while keeping exact scalar totals."""
    source_builds = {
        side: list((source.get(side) or {}).get("builds") or [])
        for side in ("amd", "upstream")
    }

    def candidate(counts: dict[str, int]) -> dict:
        result = {
            key: value
            for key, value in source.items()
            if key not in {"amd", "upstream", "publication_retention"}
        }
        metadata = {}
        for side in ("amd", "upstream"):
            section = dict(source.get(side) or {})
            retained = source_builds[side][: counts[side]]
            section["builds"] = retained
            result[side] = section
            metadata[side] = {
                "source": len(source_builds[side]),
                "published": len(retained),
                "omitted": len(source_builds[side]) - len(retained),
                "complete": len(retained) == len(source_builds[side]),
            }
        result["publication_retention"] = {
            "policy": "retain_newest_whole_build_summaries",
            "max_bytes": max_bytes,
            "complete_relative_to_source": all(
                row["complete"] for row in metadata.values()
            ),
            "builds": metadata,
            "aggregate_scalars_complete": True,
        }
        return result

    counts = {side: len(rows) for side, rows in source_builds.items()}
    bounded = candidate(counts)
    while len(pretty_json_bytes(bounded)) > max_bytes:
        removable = [side for side, count in counts.items() if count > 0]
        if not removable:
            raise RuntimeError(
                "CI health fixed/latest metadata exceeds its byte budget; "
                "preserving the last-known-good file"
            )
        side = max(
            removable,
            key=lambda name: (
                counts[name],
                len(pretty_json_bytes(source_builds[name][counts[name] - 1])),
                name,
            ),
        )
        counts[side] -= 1
        bounded = candidate(counts)
    return bounded


# ---------------------------------------------------------------------------
# Per-build test results (JSONL)
# ---------------------------------------------------------------------------

def write_test_results(
    results: list[TestResult],
    date: str,
    pipeline_key: str,
    output_dir: Path,
) -> Path | None:
    """Write per-test results as JSONL (one JSON object per line).

    Args:
        results: List of TestResult objects
        date: ISO date string (e.g. "2026-03-22")
        pipeline_key: "amd" or "upstream"
        output_dir: Directory to write into (e.g. data/vllm/ci/test_results/)

    Returns:
        Path to the written file, or ``None`` when an older complete shard is
        outside the byte-bounded retained suffix.
    """
    try:
        normalized_date = datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ValueError("test-result date must be a canonical ISO date") from exc
    if normalized_date != date or pipeline_key not in {"amd", "upstream"}:
        raise ValueError("test-result shard name is invalid")

    payload = "".join(
        json.dumps(r.to_dict(), separators=(",", ":")) + "\n"
        for r in results
    ).encode("utf-8")
    if len(payload) > TEST_RESULT_SHARD_MAX_BYTES:
        raise RuntimeError(
            "complete test-result shard exceeds its byte budget; preserving "
            f"the last-known-good file: {len(payload)} > "
            f"{TEST_RESULT_SHARD_MAX_BYTES} bytes"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{date}_{pipeline_key}.jsonl"
    previous_retention = validate_result_retention(output_dir)
    plan = _result_retention_plan(
        output_dir,
        max_days=None,
        max_total_bytes=TEST_RESULT_STORE_MAX_BYTES,
        max_shard_bytes=TEST_RESULT_SHARD_MAX_BYTES,
        replacement=(path, len(payload)),
        protected=path,
    )
    byte_limited = plan.byte_limited or bool(
        previous_retention and previous_retention["byte_limited"] is True
    )
    if not plan.replacement_retained:
        _write_result_retention(
            output_dir,
            plan,
            byte_limited=True,
            max_total_bytes=TEST_RESULT_STORE_MAX_BYTES,
            max_shard_bytes=TEST_RESULT_SHARD_MAX_BYTES,
        )
        log.info(
            "Omitted complete historical test-result shard %s because it "
            "falls before the bounded retained suffix",
            path.name,
        )
        return None

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=output_dir
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
        for stale in plan.removals:
            stale.unlink(missing_ok=True)
        _write_result_retention(
            output_dir,
            plan,
            byte_limited=byte_limited,
            max_total_bytes=TEST_RESULT_STORE_MAX_BYTES,
            max_shard_bytes=TEST_RESULT_SHARD_MAX_BYTES,
        )
    finally:
        temporary_path.unlink(missing_ok=True)

    log.info(
        "Wrote %d test results to %s (%d bytes, pruned %d older shards)",
        len(results),
        path,
        len(payload),
        len(plan.removals),
    )
    return path


# ---------------------------------------------------------------------------
# CI health summary
# ---------------------------------------------------------------------------

def write_ci_health(
    amd_summaries: list[BuildSummary],
    upstream_summaries: list[BuildSummary],
    health_data: list[TestHealth],
    output_dir: Path,
) -> Path:
    """Write ci_health.json with overall dashboard data.

    Args:
        amd_summaries: AMD build summaries, newest-first
        upstream_summaries: Upstream build summaries, newest-first
        health_data: All test health labels
        output_dir: Output directory

    Returns:
        Path to the written file.
    """
    # Keep the output schema stable even when a bucket has no current tests.
    label_counts = {label: 0 for label in HEALTH_LABEL_BUCKETS}
    for h in health_data:
        label_counts[h.label] = label_counts.get(h.label, 0) + 1

    # Determine overall health direction
    def _health_direction(summaries: list[BuildSummary]) -> str:
        summaries = [summary for summary in summaries if summary.has_test_results]
        if len(summaries) < 3:
            return "unknown"
        recent = summaries[:3]
        older = summaries[3:6] if len(summaries) >= 6 else summaries[3:]
        if not older:
            return "stable"
        recent_avg = sum(s.pass_rate for s in recent) / len(recent)
        older_avg = sum(s.pass_rate for s in older) / len(older)
        diff = recent_avg - older_avg
        if diff > 0.02:
            return "improving"
        elif diff < -0.02:
            return "degrading"
        return "stable"

    def _build_section(summaries: list[BuildSummary]) -> dict:
        if not summaries:
            return {
                "latest_build": None,
                "latest_test_signal_build": None,
                "latest_pipeline_build": None,
                "builds": [],
                "trend": "unknown",
            }
        signal_summaries = [summary for summary in summaries if summary.has_test_results]
        latest_signal = signal_summaries[0] if signal_summaries else None
        latest_pipeline = summaries[0]
        return {
            # ``latest_build`` is retained as the test-evidence build for
            # compatibility with parity, matrix, and raw-JSONL audits.
            "latest_build": latest_signal.to_dict() if latest_signal else None,
            "latest_test_signal_build": latest_signal.to_dict() if latest_signal else None,
            "latest_pipeline_build": latest_pipeline.to_dict(),
            "latest_pipeline_build_has_test_results": latest_pipeline.has_test_results,
            "builds": [s.to_dict() for s in summaries],
            "trend": _health_direction(summaries),
        }

    data = {
        "pass_rate_contract_version": PASS_RATE_CONTRACT_VERSION,
        "generated_at": _now_iso(),
        "amd": _build_section(amd_summaries),
        "upstream": _build_section(upstream_summaries),
        "overall_health": _health_direction(amd_summaries),
        "test_counts": label_counts,
        "total_unique_tests": len(health_data),
    }

    path = output_dir / "ci_health.json"
    bounded = _compact_ci_health(data, max_bytes=CI_HEALTH_MAX_BYTES)
    size = write_pretty_json_lkg(
        path,
        bounded,
        max_bytes=CI_HEALTH_MAX_BYTES,
        label="CI health snapshot",
    )
    log.info("Wrote ci_health.json (%d bytes)", size)
    return path


# ---------------------------------------------------------------------------
# Parity report
# ---------------------------------------------------------------------------

def write_parity_report(
    parity_data: dict,
    amd_date: str,
    upstream_date: str,
    output_dir: Path,
) -> Path:
    """Write parity_report.json."""
    report = {
        "generated_at": _now_iso(),
        "amd_date": amd_date,
        "upstream_date": upstream_date,
        **parity_data,
    }

    path = output_dir / "parity_report.json"
    # This snapshot is published twice (CI path plus compatibility copy), so
    # each exact encoding receives half of the pair allocation.
    per_file_max = CI_PARITY_PAIR_MAX_BYTES // 2
    bounded = _compact_collection_fields(
        report,
        fields=("by_module", "job_groups", "details"),
        max_bytes=per_file_max,
        policy="retain_deterministic_whole_parity_rows",
    )
    write_pretty_json_lkg(
        path,
        bounded,
        max_bytes=per_file_max,
        label="CI parity snapshot",
    )
    log.info("Wrote parity_report.json (parity: %.1f%%)", parity_data.get("parity_pct", 0))
    return path


# ---------------------------------------------------------------------------
# Flaky tests
# ---------------------------------------------------------------------------

def write_flaky_tests(
    health_data: list[TestHealth],
    output_dir: Path,
) -> Path:
    """Write flaky_tests.json with all tests labeled as flaky."""
    flaky = [h for h in health_data if h.label == "flaky"]
    flaky.sort(key=lambda h: h.pass_rate)

    data = {
        "generated_at": _now_iso(),
        "window_builds": len(health_data[0].history) if health_data else 0,
        "total_flaky": len(flaky),
        "tests": [h.to_dict() for h in flaky],
    }

    path = output_dir / "flaky_tests.json"
    bounded = _compact_collection_fields(
        data,
        fields=("tests",),
        max_bytes=FLAKY_TESTS_MAX_BYTES,
        policy="retain_lowest_pass_rate_whole_test_rows",
    )
    write_pretty_json_lkg(
        path,
        bounded,
        max_bytes=FLAKY_TESTS_MAX_BYTES,
        label="flaky-test snapshot",
    )
    log.info(
        "Wrote flaky_tests.json (%d/%d flaky tests)",
        len(bounded["tests"]),
        len(flaky),
    )
    return path


# ---------------------------------------------------------------------------
# Failure trends
# ---------------------------------------------------------------------------

def write_failure_trends(
    trends_data: dict,
    output_dir: Path,
) -> Path:
    """Write failure_trends.json."""
    report = {
        "generated_at": _now_iso(),
        **trends_data,
    }

    path = output_dir / "failure_trends.json"
    bounded = _compact_collection_fields(
        report,
        fields=(
            "top_offenders",
            "new_failures",
            "recently_fixed",
            "degrading_modules",
            "pass_rate_trend",
        ),
        max_bytes=FAILURE_TRENDS_MAX_BYTES,
        policy="retain_ranked_whole_failure_trend_rows",
    )
    write_pretty_json_lkg(
        path,
        bounded,
        max_bytes=FAILURE_TRENDS_MAX_BYTES,
        label="failure-trend snapshot",
    )
    log.info(
        "Wrote failure_trends.json (%d top offenders, %d new failures, %d fixed)",
        len(bounded.get("top_offenders", [])),
        len(bounded.get("new_failures", [])),
        len(bounded.get("recently_fixed", [])),
    )
    return path


# ---------------------------------------------------------------------------
# Quarantine report
# ---------------------------------------------------------------------------

def write_quarantine_report(
    quarantine_report: dict,
    output_dir: Path,
) -> Path:
    """Write quarantine.json."""
    report = {
        "generated_at": _now_iso(),
        **quarantine_report,
    }

    path = output_dir / "quarantine.json"
    bounded = _compact_collection_fields(
        report,
        fields=("quarantine_entries", "allowlist_entries"),
        max_bytes=QUARANTINE_REPORT_MAX_BYTES,
        policy="retain_canonical_whole_quarantine_and_allowlist_rows",
    )
    write_pretty_json_lkg(
        path,
        bounded,
        max_bytes=QUARANTINE_REPORT_MAX_BYTES,
        label="quarantine report",
    )
    log.info(
        "Wrote quarantine.json (%d quarantine rows, %d allowlist rows)",
        len(bounded.get("quarantine_entries", [])),
        len(bounded.get("allowlist_entries", [])),
    )
    return path


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def _result_retention_plan(
    results_dir: Path,
    *,
    max_days: int | None,
    max_total_bytes: int,
    max_shard_bytes: int,
    now: datetime | None = None,
    replacement: tuple[Path, int] | None = None,
    protected: Path | None = None,
) -> _ResultRetentionPlan:
    """Plan whole-day removals without changing a last-known-good store."""
    if max_days is not None and max_days < 0:
        raise ValueError("max_days must be nonnegative")
    if max_total_bytes <= 0 or max_shard_bytes <= 0:
        raise ValueError("test-result byte budgets must be positive")

    files: dict[Path, tuple[str, int]] = {}
    if results_dir.exists():
        for path in sorted(results_dir.glob("*.jsonl")):
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f"test-result shard is not a regular file: {path}")
            match = _RESULT_FILE_RE.fullmatch(path.name)
            if match is None:
                raise RuntimeError(f"unrecognized test-result shard: {path.name}")
            files[path] = (match.group(1), path.stat().st_size)

    replacement_path: Path | None = None
    if replacement is not None:
        replacement_path, replacement_bytes = replacement
        replacement_path = Path(replacement_path)
        match = _RESULT_FILE_RE.fullmatch(replacement_path.name)
        if replacement_path.parent != results_dir or match is None:
            raise ValueError("replacement test-result shard path is invalid")
        if replacement_bytes < 0:
            raise ValueError("replacement test-result shard size is invalid")
        files[replacement_path] = (match.group(1), replacement_bytes)

    planned: set[Path] = set()
    if max_days is not None:
        from datetime import timedelta

        reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff_date = (reference - timedelta(days=max_days)).strftime("%Y-%m-%d")
        planned.update(path for path, (date, _) in files.items() if date < cutoff_date)

    byte_limited = False
    while True:
        retained = {
            path: descriptor for path, descriptor in files.items() if path not in planned
        }
        oversized = [
            path for path, (_, size) in retained.items() if size > max_shard_bytes
        ]
        total_bytes = sum(size for _, size in retained.values())
        if not oversized and total_bytes <= max_total_bytes:
            break
        retained_dates = sorted({date for date, _ in retained.values()})
        if len(retained_dates) <= 1:
            details = {path.name: size for path, (_, size) in retained.items()}
            raise RuntimeError(
                "newest complete test-result day cannot fit the byte budgets; "
                f"files={details}, total={total_bytes}, "
                f"max_shard={max_shard_bytes}, max_store={max_total_bytes}"
            )
        byte_limited = True
        oldest = retained_dates[0]
        planned.update(path for path, (date, _) in retained.items() if date == oldest)

    retained_dates = sorted(
        {date for path, (date, _) in files.items() if path not in planned}
    )
    replacement_retained = protected is None or Path(protected) not in planned
    # A hypothetical replacement is never unlinked. If it was omitted, retain
    # any existing last-known-good file at that path.
    removals = tuple(
        sorted(path for path in planned if path != replacement_path and path.exists())
    )
    return _ResultRetentionPlan(
        removals=removals,
        retained_start=retained_dates[0] if retained_dates else None,
        retained_end=retained_dates[-1] if retained_dates else None,
        byte_limited=byte_limited,
        replacement_retained=replacement_retained,
    )


def _read_result_retention(results_dir: Path) -> dict | None:
    path = results_dir / TEST_RESULT_RETENTION_FILE
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("test-result retention marker is not a regular file")
    raw = path.read_bytes()
    if len(raw) > TEST_RESULT_RETENTION_MAX_BYTES:
        raise RuntimeError("test-result retention marker exceeds its byte budget")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("test-result retention marker is invalid") from exc
    expected = {
        "schema_version",
        "policy",
        "byte_limited",
        "retained_start",
        "retained_end",
        "max_total_bytes",
        "max_shard_bytes",
        "shard_count",
        "shard_bytes",
        "shards_sha256",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected
        or payload.get("schema_version") != 1
        or payload.get("policy") != "drop_oldest_complete_utc_days"
        or not isinstance(payload.get("byte_limited"), bool)
    ):
        raise RuntimeError("test-result retention marker has an invalid shape")
    for key in ("retained_start", "retained_end"):
        value = payload.get(key)
        if value is not None and (
            not isinstance(value, str)
            or _RESULT_FILE_RE.fullmatch(f"{value}_amd.jsonl") is None
        ):
            raise RuntimeError("test-result retention marker has an invalid date")
    for key in ("max_total_bytes", "max_shard_bytes"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RuntimeError("test-result retention marker has an invalid byte limit")
    for key in ("shard_count", "shard_bytes"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError("test-result retention marker has invalid shard accounting")
    if (
        not isinstance(payload.get("shards_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", payload["shards_sha256"]) is None
    ):
        raise RuntimeError("test-result retention marker has an invalid shard digest")
    if (
        payload["retained_start"] is None
        and payload["retained_end"] is not None
    ) or (
        payload["retained_start"] is not None
        and payload["retained_end"] is not None
        and payload["retained_start"] > payload["retained_end"]
    ):
        raise RuntimeError("test-result retention marker has an invalid range")
    return payload


def _result_shard_attestation(results_dir: Path) -> dict:
    digest = hashlib.sha256()
    dates: list[str] = []
    shard_count = 0
    shard_bytes = 0
    if results_dir.exists():
        for path in sorted(results_dir.glob("*.jsonl")):
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f"test-result shard is not a regular file: {path}")
            match = _RESULT_FILE_RE.fullmatch(path.name)
            if match is None:
                raise RuntimeError(f"unrecognized test-result shard: {path.name}")
            payload = path.read_bytes()
            shard_digest = hashlib.sha256(payload).hexdigest()
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(len(payload)).encode("ascii"))
            digest.update(b"\0")
            digest.update(shard_digest.encode("ascii"))
            digest.update(b"\n")
            dates.append(match.group(1))
            shard_count += 1
            shard_bytes += len(payload)
    return {
        "retained_start": min(dates) if dates else None,
        "retained_end": max(dates) if dates else None,
        "shard_count": shard_count,
        "shard_bytes": shard_bytes,
        "shards_sha256": digest.hexdigest(),
    }


def validate_result_retention(
    results_dir: Path,
    *,
    max_total_bytes: int | None = None,
    max_shard_bytes: int | None = None,
) -> dict | None:
    """Reconcile a retention marker with the exact shard generation."""
    if max_total_bytes is None:
        max_total_bytes = TEST_RESULT_STORE_MAX_BYTES
    if max_shard_bytes is None:
        max_shard_bytes = TEST_RESULT_SHARD_MAX_BYTES
    payload = _read_result_retention(results_dir)
    if payload is None:
        return None
    if (
        payload["max_total_bytes"] != max_total_bytes
        or payload["max_shard_bytes"] != max_shard_bytes
    ):
        raise RuntimeError("test-result retention marker uses stale byte limits")
    attestation = _result_shard_attestation(results_dir)
    for key, actual in attestation.items():
        if payload.get(key) != actual:
            raise RuntimeError(
                f"test-result retention marker disagrees with exact shards: {key}"
            )
    if attestation["shard_bytes"] > max_total_bytes:
        raise RuntimeError("test-result shards exceed their aggregate byte budget")
    for path in results_dir.glob("*.jsonl"):
        if path.stat().st_size > max_shard_bytes:
            raise RuntimeError(f"test-result shard exceeds its byte budget: {path.name}")
    return payload


def retained_result_start(results_dir: Path) -> str | None:
    """Return the reconciled byte-limited floor used to avoid futile backfills."""
    payload = validate_result_retention(results_dir)
    if not payload or payload["byte_limited"] is not True:
        return None
    return payload["retained_start"]


def _write_result_retention(
    results_dir: Path,
    plan: _ResultRetentionPlan,
    *,
    byte_limited: bool,
    max_total_bytes: int,
    max_shard_bytes: int,
) -> None:
    payload = {
        "schema_version": 1,
        "policy": "drop_oldest_complete_utc_days",
        "byte_limited": byte_limited,
        "retained_start": plan.retained_start,
        "retained_end": plan.retained_end,
        "max_total_bytes": max_total_bytes,
        "max_shard_bytes": max_shard_bytes,
        **_result_shard_attestation(results_dir),
    }
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(encoded) > TEST_RESULT_RETENTION_MAX_BYTES:
        raise RuntimeError("test-result retention marker exceeds its byte budget")
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / TEST_RESULT_RETENTION_FILE
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=results_dir
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def prune_old_results(
    results_dir: Path,
    max_days: int = 30,
    *,
    max_total_bytes: int = TEST_RESULT_STORE_MAX_BYTES,
    max_shard_bytes: int = TEST_RESULT_SHARD_MAX_BYTES,
    now: datetime | None = None,
    allow_generation_change: bool = False,
) -> int:
    """Remove oldest whole UTC days until age and exact byte bounds hold.

    ``allow_generation_change`` is reserved for the checkpoint-restore path:
    callers must validate the public generation before restoring private shards,
    then use this flag once to attest the resulting compacted generation.
    """
    if not results_dir.exists():
        return 0

    previous_retention = (
        _read_result_retention(results_dir)
        if allow_generation_change
        else validate_result_retention(
            results_dir,
            max_total_bytes=max_total_bytes,
            max_shard_bytes=max_shard_bytes,
        )
    )
    plan = _result_retention_plan(
        results_dir,
        max_days=max_days,
        max_total_bytes=max_total_bytes,
        max_shard_bytes=max_shard_bytes,
        now=now,
    )
    for path in plan.removals:
        path.unlink(missing_ok=True)
    byte_limited = plan.byte_limited or bool(
        previous_retention and previous_retention["byte_limited"] is True
    )
    _write_result_retention(
        results_dir,
        plan,
        byte_limited=byte_limited,
        max_total_bytes=max_total_bytes,
        max_shard_bytes=max_shard_bytes,
    )
    if plan.removals:
        log.info(
            "Pruned %d test-result shards to the %d-day/%d-byte store budget",
            len(plan.removals),
            max_days,
            max_total_bytes,
        )
    return len(plan.removals)
