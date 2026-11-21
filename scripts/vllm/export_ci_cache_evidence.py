"""Export one validated MI build from an exact private analytics cache restore.

This diagnostic reads existing evidence only. It cannot collect, refresh, save a
cache, acquire source definitions, or publish canonical dashboard data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass, fields
from datetime import date, datetime, timezone
from pathlib import Path

from vllm.ci import analytics_cache
from vllm.ci import backfill_checkpoint
from vllm.ci.models import TestResult
from vllm.pipelines import is_amd_ci_job

ROOT = Path(__file__).resolve().parents[2]
MAX_EXPORT_BYTES = 2 * 1024 * 1024
CACHE_KEY_RE = re.compile(
    r"analytics-builds-v1-Linux-(\d{4}-\d{2}-\d{2})-[1-9]\d{0,19}-[1-9]\d{0,3}"
)
JOB_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
PARSED_FILE_NAME = "parsed-results.jsonl"
# Optional identity provenance is readable before its producer migration lands.
PARSED_SOURCE_FIELDS = frozenset({
    "source_definition_id", "source_agent_pool", "source_commit",
    "source_step_key", "source_binding_basis",
})


class EvidenceError(ValueError):
    """The requested existing evidence cannot be safely exported."""


@dataclass(frozen=True)
class EvidenceRequest:
    cache_key: str
    build_number: int
    full_commit: str
    checkpoint_cache_key: str = ""


def normalize_request(
    cache_key: str, build_number: str, full_commit: str, checkpoint_cache_key: str = "",
) -> EvidenceRequest:
    cache_key = cache_key.strip()
    key_match = CACHE_KEY_RE.fullmatch(cache_key)
    if key_match is None:
        raise EvidenceError("invalid exact analytics cache key")
    try:
        date.fromisoformat(key_match.group(1))
    except ValueError:
        raise EvidenceError("invalid cache key date") from None
    number = build_number.strip()
    if not re.fullmatch(r"[1-9]\d{0,9}", number):
        raise EvidenceError("invalid positive build number")
    commit = full_commit.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise EvidenceError("expected one exact full commit")
    checkpoint_cache_key = checkpoint_cache_key.strip()
    if checkpoint_cache_key and checkpoint_cache_key != cache_key.replace(
        "analytics-builds-v1-Linux-", "ci-backfill-v1-Linux-", 1,
    ):
        raise EvidenceError("parsed checkpoint must come from the exact same collection attempt")
    return EvidenceRequest(cache_key, int(number), commit, checkpoint_cache_key)


def _safe_path(path: Path) -> Path:
    absolute = path.absolute()
    if any(parent.is_symlink() for parent in (absolute, *absolute.parents)):
        raise EvidenceError("symlink paths are forbidden")
    return absolute.resolve()


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _read_manifest(path: Path) -> tuple[bytes, dict]:
    path = _safe_path(path)
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= analytics_cache._MAX_CACHE_TOTAL_BYTES:
        raise EvidenceError("cache manifest exceeds the existing cache bound")
    raw = path.read_bytes()
    if len(raw) != metadata.st_size:
        raise EvidenceError("cache manifest changed while reading")
    # The production reader repeats strict schema, duplicate-key and digest
    # validation; parsing here only supplies its declared coverage arguments.
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise EvidenceError("cache manifest must be an object")
    return raw, decoded


def _blob_proof(raw: bytes) -> dict:
    return {
        "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        "git_blob_oid": hashlib.sha1(
            f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False,
        ).hexdigest(),
    }


def _valid_duration(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _parsed_evidence(directory: Path, request: EvidenceRequest, build: dict) -> tuple[bytes, dict]:
    directory = _safe_path(directory)
    _safe_path(directory / backfill_checkpoint.SHARD_DIR)
    manifest_path = _safe_path(directory / backfill_checkpoint.MANIFEST_NAME)
    if not manifest_path.is_file() or manifest_path.stat().st_size > 1024 * 1024:
        raise EvidenceError("parsed checkpoint manifest is not bounded")
    with manifest_path.open("rb") as handle:
        manifest_raw = handle.read(1024 * 1024 + 1)
    if len(manifest_raw) > 1024 * 1024:
        raise EvidenceError("parsed checkpoint manifest is not bounded")
    backfill_checkpoint.validate(directory)
    if manifest_path.read_bytes() != manifest_raw:
        raise EvidenceError("parsed checkpoint manifest changed during validation")
    manifest = backfill_checkpoint._decode_json(manifest_raw, label="diagnostic checkpoint")
    assert isinstance(manifest, dict)  # The production validator proved this shape.
    selected = [(name, descriptor) for name, descriptor in manifest["shards"].items()
                if name.endswith("_amd.jsonl") and descriptor["build_number"] == request.build_number]
    if len(selected) != 1:
        raise EvidenceError("selected AMD parsed shard is absent or ambiguous")
    name, descriptor = selected[0]
    path = _safe_path(directory / backfill_checkpoint.SHARD_DIR / name)
    if descriptor["bytes"] > MAX_EXPORT_BYTES:
        raise EvidenceError("single-build diagnostic exceeds 2 MiB")
    with path.open("rb") as handle:
        raw = handle.read(MAX_EXPORT_BYTES + 1)
    if len(raw) != descriptor["bytes"] or hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
        raise EvidenceError("parsed checkpoint shard changed during validation")
    roster_ids = [job.get("id") for job in build["jobs"]]
    if any(not isinstance(value, str) or JOB_UUID_RE.fullmatch(value) is None for value in roster_ids):
        raise EvidenceError("selected MI roster lacks exact job UUIDs")
    if len(set(roster_ids)) != len(roster_ids):
        raise EvidenceError("selected MI roster has duplicate job UUIDs")
    eligible_ids = set(roster_ids)
    allowed_fields = {field.name for field in fields(TestResult)} | PARSED_SOURCE_FIELDS
    source_fields = PARSED_SOURCE_FIELDS
    required_fields = allowed_fields - source_fields
    string_fields = allowed_fields - {"build_number", "duration_secs", "parser_version"}
    source_day = name[:10]
    if date.fromisoformat(source_day).isoformat() != datetime.fromisoformat(
        build["created_at"].replace("Z", "+00:00"),
    ).astimezone(timezone.utc).date().isoformat():
        raise EvidenceError("parsed shard day disagrees with the exact build")
    job_ids, parser_versions = set(), set()
    rows = 0
    for line in raw.splitlines(keepends=True):
        row = backfill_checkpoint._decode_json(line, label="diagnostic parsed row")
        if (
            not isinstance(row, dict) or set(row) - allowed_fields
            or not required_fields <= set(row)
            or (set(row) & source_fields and not source_fields <= set(row))
            or any(not isinstance(row.get(key), str) for key in string_fields if key in row)
            or row.get("pipeline") != "ci" or row.get("build_number") != request.build_number
            or row.get("job_id") not in eligible_ids
            or row.get("date") != source_day or not is_amd_ci_job(row)
            or type(row.get("parser_version")) is not int or row["parser_version"] < 0
            or row.get("status") not in {"passed", "failed", "skipped", "error", "xfailed", "xpassed", "canceled"}
            or not _valid_duration(row.get("duration_secs"))
            or (set(row) & source_fields and row.get("source_commit") != request.full_commit)
        ):
            raise EvidenceError("parsed checkpoint contains foreign, mixed, or unsafe rows")
        job_ids.add(row["job_id"])
        parser_versions.add(row["parser_version"])
        rows += 1
    if rows != descriptor["rows"] or manifest_path.read_bytes() != manifest_raw or path.read_bytes() != raw:
        raise EvidenceError("parsed checkpoint changed during export")
    return raw, {
        "cache_key": request.checkpoint_cache_key,
        "manifest_proof": {"file": backfill_checkpoint.MANIFEST_NAME, **_blob_proof(manifest_raw)},
        "original_manifest": manifest,
        "selected_descriptor": descriptor,
        "selected_shard": {"file": PARSED_FILE_NAME, "source_file": f"test_results/{name}", **_blob_proof(raw)},
        "roster_job_count": len(eligible_ids), "parsed_job_count": len(job_ids),
        "rows": rows, "parser_versions": sorted(parser_versions),
    }


def export_evidence(
    cache_dir: Path,
    output_dir: Path,
    request: EvidenceRequest,
    *,
    ref_now: datetime,
    checkout_root: Path = ROOT,
    checkpoint_dir: Path | None = None,
) -> dict:
    """Validate the whole restore, then write only one existing MI build."""
    cache_dir = _safe_path(cache_dir)
    output_dir = _safe_path(output_dir)
    if output_dir.is_relative_to(checkout_root.resolve()) or output_dir.is_relative_to(cache_dir):
        raise EvidenceError("diagnostic output must be outside the checkout and cache")
    if output_dir.exists():
        raise EvidenceError("choose an unused diagnostic output directory")
    raw, manifest = _read_manifest(cache_dir / "ci.json")
    cutoff_text = manifest.get("complete_from")
    stored_window = manifest.get("window_days")
    if not isinstance(cutoff_text, str) or type(stored_window) is not int or stored_window <= 0:
        raise EvidenceError("invalid cache coverage metadata")
    cutoff = datetime.fromisoformat(cutoff_text.replace("Z", "+00:00"))
    loaded = analytics_cache.load_build_cache(
        cache_dir, "ci", cutoff=cutoff, window_days=stored_window, ref_now=ref_now,
    )
    if not loaded.valid:
        raise EvidenceError("cache validation failed: " + loaded.reason)
    if (cache_dir / "ci.json").read_bytes() != raw:
        raise EvidenceError("cache manifest changed during validation")
    selected = [build for build in loaded.builds if build["number"] == request.build_number]
    if len(selected) != 1:
        raise EvidenceError("selected build is absent or ambiguous")
    build = selected[0]
    if (
        build.get("branch") != "main"
        or build.get("commit") != request.full_commit
        or build.get("hardware_scope") != "amd_mi_gpu"
        or build.get("source_scope_commit") != request.full_commit
        or build.get("jobs_complete") is not True
        or not build.get("jobs")
    ):
        raise EvidenceError("selected build lacks exact current MI source authority")

    from vllm.main_ci_definitions import (
        annotate_runtime_source_scope,
        validate_runtime_scope_index,
    )

    index = validate_runtime_scope_index(build.get("source_scope_index"), expected_commit=request.full_commit)
    if index["definition_tree_sha"] != build.get("source_definition_tree_sha"):
        raise EvidenceError("selected build source tree proof disagrees")
    # Supplying the authenticated existing index prevents any source fetch.
    # Recheck CPU-only definitions before exporting the stored MI job roster.
    checked = annotate_runtime_source_scope(build, scope_index=index)
    if any(not is_amd_ci_job(job) for job in checked["jobs"]):
        raise EvidenceError("selected build includes CPU or foreign execution")
    parsed_raw, parsed_proof = b"", None
    if request.checkpoint_cache_key:
        if checkpoint_dir is None:
            raise EvidenceError("requested parsed checkpoint was not restored")
        parsed_raw, parsed_proof = _parsed_evidence(checkpoint_dir, request, checked)
    elif checkpoint_dir is not None:
        raise EvidenceError("parsed checkpoint has no exact requested cache identity")

    build_raw = _json_bytes(build)
    build_sha256 = hashlib.sha256(build_raw).hexdigest()
    manifest_proof = {
        "file": "ci.json", "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "cache_kind": manifest["cache_kind"],
        "integrity": manifest["integrity"],
    }
    for key in ("generation", "build_count", "shards"):
        if key in manifest:
            manifest_proof[key] = manifest[key]
    receipt = {
        "schema_version": 1,
        "diagnostic_only": True,
        "cache_key": request.cache_key,
        "pipeline": "ci", "branch": "main",
        "build_number": request.build_number,
        "full_commit": request.full_commit,
        "cache_manifest_proof": manifest_proof,
        "original_cache_metadata": {
            key: manifest[key] for key in (
                "generated_at", "watermark", "last_full_at", "complete_from",
                "window_days", "query_identity",
            )
        },
        "source_proof": {
            "commit_sha": index["commit_sha"],
            "definition_tree_sha": index["definition_tree_sha"],
            "index_sha256": hashlib.sha256(_json_bytes(index)).hexdigest(),
        },
        "selected_build_blob": {
            "file": "build.json", "bytes": len(build_raw),
            "sha256": build_sha256,
            "git_blob_oid": hashlib.sha1(
                f"blob {len(build_raw)}\0".encode() + build_raw, usedforsecurity=False,
            ).hexdigest(),
        },
    }
    if parsed_proof is not None:
        receipt["parsed_checkpoint_proof"] = parsed_proof
    receipt_raw = _json_bytes(receipt)
    if len(build_raw) + len(receipt_raw) + len(parsed_raw) > MAX_EXPORT_BYTES:
        raise EvidenceError("single-build diagnostic exceeds 2 MiB")
    output_dir.mkdir(mode=0o700, parents=False)
    payloads = [("build.json", build_raw), ("receipt.json", receipt_raw)]
    if parsed_proof is not None:
        payloads.append((PARSED_FILE_NAME, parsed_raw))
    for name, payload in payloads:
        descriptor = os.open(output_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
    return {
        "diagnostic_only": True, "build_number": request.build_number,
        "job_count": len(build["jobs"]), "bytes": len(build_raw) + len(receipt_raw) + len(parsed_raw),
        "build_sha256": build_sha256,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-inputs", action="store_true")
    args = parser.parse_args(argv)
    try:
        request = normalize_request(
            os.environ.get("CACHE_KEY", ""), os.environ.get("BUILD_NUMBER", ""),
            os.environ.get("FULL_COMMIT", ""),
            os.environ.get("CHECKPOINT_CACHE_KEY", ""),
        )
        if args.validate_inputs:
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                for key, value in (
                    ("cache_key", request.cache_key), ("build_number", request.build_number),
                    ("full_commit", request.full_commit),
                    ("checkpoint_cache_key", request.checkpoint_cache_key),
                ):
                    output.write(f"{key}={value}\n")
            return 0
        if os.environ.get("CACHE_HIT") != "true" or os.environ.get("CACHE_MATCHED_KEY") != request.cache_key:
            raise EvidenceError("restore must match the exact requested cache key")
        if request.checkpoint_cache_key and (
            os.environ.get("CHECKPOINT_CACHE_HIT") != "true"
            or os.environ.get("CHECKPOINT_CACHE_MATCHED_KEY") != request.checkpoint_cache_key
        ):
            raise EvidenceError("parsed restore must match the exact requested cache key")
        result = export_evidence(
            Path(os.environ["CACHE_DIRECTORY"]), Path(os.environ["EVIDENCE_DIRECTORY"]),
            request, ref_now=datetime.now(timezone.utc),
            checkpoint_dir=Path(os.environ["CHECKPOINT_DIRECTORY"]) if request.checkpoint_cache_key else None,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError):
        # Cache bodies, arbitrary environment values and authentication never
        # enter Actions logs, even if validation raises a provider-shaped error.
        print("CI cache evidence rejected; no diagnostic upload is authorized.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
