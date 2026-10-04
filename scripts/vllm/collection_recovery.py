"""Read bounded collection-failure evidence from an exact published state.

The attempt ledger already attests the durable state commit. This reader checks
that commit's parentless identity, manifest-bound publication-state bytes and
collection clock; it never substitutes the newer Pages, DNS, or queue state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any

from vllm.publication_surfaces import SURFACE_SPECS
from vllm import github_git_proof
from vllm.dashboard_storage_budget import writer_max_bytes


RETRY_SURFACES = frozenset({
    "ci_core", "ci_analytics", "ci_gating", "ci_changes", "ci_hotness",
    "agent_health", "github_home", "perf_eval",
})
REASON_CLASSES = frozenset({
    "payload-budget", "rate-limit", "timeout", "schema-drift",
    "transient-http", "network", "dependency-unavailable", "command-error",
})
STATE_PATH = "data/vllm/ci/publication_state.json"
MANIFEST_PATH = "data/vllm/ci/dashboard_state.json"
MAX_STATE_BYTES = writer_max_bytes("publication_state")
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
SHA_RE = re.compile(r"[0-9a-f]{40}")


class CollectionEvidenceError(ValueError):
    """A publication cannot prove an earlier collection retry is warranted."""


class CollectionEvidenceUnavailable(CollectionEvidenceError):
    """Required immutable objects are not yet available in the local checkout."""


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise CollectionEvidenceError("publication clock is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CollectionEvidenceError("publication clock is invalid") from exc
    if (
        parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
        or parsed.microsecond or parsed.isoformat().replace("+00:00", "Z") != value
    ):
        raise CollectionEvidenceError("publication clock must be canonical whole-second UTC")
    return parsed


def retry_surfaces_from_state(payload: object) -> list[str]:
    """Only typed failed collectors in fallback qualify, not general degradation."""
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload["schema_version"] != 2:
        raise CollectionEvidenceError("unsupported publication state schema")
    if payload.get("mode") not in {"current", "degraded", "fallback", "mixed"}:
        raise CollectionEvidenceError("publication state is not publishable")
    lanes: dict[str, set[str]] = {}
    for field in ("fallback_surfaces", "fresh_degraded_surfaces", "degraded_surfaces"):
        values = payload.get(field)
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) or value not in SURFACE_SPECS for value in values)
            or len(values) != len(set(values))
        ):
            raise CollectionEvidenceError("invalid publication surface lanes")
        lanes[field] = set(values)
    fallback = lanes["fallback_surfaces"]
    fresh = lanes["fresh_degraded_surfaces"]
    if fallback & fresh or fallback | fresh != lanes["degraded_surfaces"]:
        raise CollectionEvidenceError("publication surface lanes disagree")
    expected_mode = "mixed" if fallback and fresh else (
        "fallback" if fallback else "degraded" if fresh else "current"
    )
    if payload["mode"] != expected_mode:
        raise CollectionEvidenceError("publication mode disagrees with surface lanes")
    records = payload.get("collector_failures")
    if not isinstance(records, list) or len(records) > 256:
        raise CollectionEvidenceError("collector failure evidence is unavailable")
    selected: set[str] = set()
    for row in records:
        if not isinstance(row, dict):
            raise CollectionEvidenceError("invalid collector failure record")
        surface = row.get("surface")
        collector = row.get("collector")
        step = row.get("step")
        if (
            type(row.get("schema_version")) is not int or row["schema_version"] != 1
            or not isinstance(surface, str) or surface not in SURFACE_SPECS
            or not isinstance(collector, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", collector)
            or not isinstance(step, str) or not 1 <= len(step) <= 200
            or not isinstance(row.get("reason_class"), str)
            or row["reason_class"] not in REASON_CLASSES
            or type(row.get("exit_code")) is not int
            or not 1 <= row["exit_code"] <= 255
            or surface not in fallback
        ):
            raise CollectionEvidenceError("invalid collector failure record")
        if surface in RETRY_SURFACES:
            selected.add(surface)
    return sorted(selected)


def normalize_collection_evidence(value: object, *, durable_ref: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "durable_ref", "publication_state_oid", "retry_surfaces",
    }:
        raise CollectionEvidenceError("invalid collection evidence shape")
    surfaces = value.get("retry_surfaces")
    if (
        type(value.get("schema_version")) is not int or value["schema_version"] != 1
        or value.get("durable_ref") != durable_ref
        or not isinstance(value.get("publication_state_oid"), str)
        or not SHA_RE.fullmatch(value["publication_state_oid"])
        or not isinstance(surfaces, list)
        or any(not isinstance(surface, str) or surface not in RETRY_SURFACES for surface in surfaces)
        or surfaces != sorted(set(surfaces))
    ):
        raise CollectionEvidenceError("invalid collection evidence values")
    return dict(value)


def _git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=True, timeout=60,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"},
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        # Do not put remote URLs or credential-helper output into diagnostics.
        raise CollectionEvidenceUnavailable("exact collection state is unavailable") from exc


def _json(payload: bytes) -> dict[str, Any]:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise CollectionEvidenceError("duplicate evidence JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(payload, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as exc:
        raise CollectionEvidenceError("invalid evidence JSON") from exc
    if not isinstance(value, dict):
        raise CollectionEvidenceError("evidence must be a JSON object")
    return value


def _blob(root: Path, ref: str, path: str, limit: int) -> tuple[str, bytes]:
    rows = _git(root, "ls-tree", "-z", ref, "--", path).split(b"\0")
    rows = [row for row in rows if row]
    if len(rows) != 1:
        raise CollectionEvidenceError("exact collection evidence file is absent")
    try:
        metadata, name = rows[0].split(b"\t")
        mode, kind, oid = metadata.decode("ascii").split()
        if name.decode("utf-8") != path or mode != "100644" or kind != "blob":
            raise ValueError("unexpected tree entry")
        if not SHA_RE.fullmatch(oid):
            raise ValueError("invalid object ID")
        size = int(_git(root, "cat-file", "-s", oid))
    except (ValueError, UnicodeError) as exc:
        raise CollectionEvidenceError("invalid collection evidence tree entry") from exc
    if not 0 < size <= limit:
        raise CollectionEvidenceError("collection evidence exceeds its byte bound")
    raw = _git(root, "cat-file", "blob", oid)
    if len(raw) != size:
        raise CollectionEvidenceError("collection evidence size disagrees")
    return oid, raw


def _read_local_evidence(root: Path, *, durable_ref: str, reserved_at: str,
                         succeeded_at: str) -> dict[str, Any]:
    if _git(root, "cat-file", "-t", durable_ref).strip() != b"commit":
        raise CollectionEvidenceError("durable collection identity is not a commit")
    commit_size = int(_git(root, "cat-file", "-s", durable_ref))
    if not 0 < commit_size <= github_git_proof.MAX_COMMIT_RESPONSE_BYTES:
        raise CollectionEvidenceError("durable commit exceeds its byte bound")
    headers = _git(root, "cat-file", "commit", durable_ref)
    if any(line.startswith(b"parent ") for line in headers.split(b"\n\n", 1)[0].splitlines()):
        raise CollectionEvidenceError("durable collection state must be parentless")
    _, manifest_raw = _blob(root, durable_ref, MANIFEST_PATH, MAX_MANIFEST_BYTES)
    manifest = _json(manifest_raw)
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] not in {1, 2}:
        raise CollectionEvidenceError("unsupported durable state manifest")
    files = manifest.get("generated_files")
    if not isinstance(files, dict) or len(files) > 10000:
        raise CollectionEvidenceError("invalid durable generated-file manifest")
    descriptor = files.get(STATE_PATH)
    if not isinstance(descriptor, dict):
        raise CollectionEvidenceError("publication state is not manifest-bound")
    # Check the declared size before hydrating this generated blob.
    if type(descriptor.get("bytes")) is not int or not 0 < descriptor["bytes"] <= MAX_STATE_BYTES:
        raise CollectionEvidenceError("publication state descriptor exceeds its byte bound")
    oid, raw = _blob(root, durable_ref, STATE_PATH, MAX_STATE_BYTES)
    if descriptor != {
        "bytes": len(raw), "git_oid": oid, "mode": "100644",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }:
        raise CollectionEvidenceError("publication state disagrees with its durable manifest")
    state = _json(raw)
    if not _timestamp(reserved_at) <= _timestamp(state.get("generated_at")) <= _timestamp(succeeded_at):
        raise CollectionEvidenceError("publication state clock is outside its recorded attempt")
    return {
        "schema_version": 1,
        "durable_ref": durable_ref,
        "publication_state_oid": oid,
        "retry_surfaces": retry_surfaces_from_state(state),
    }


def _hydrate_remote_evidence(root: Path, *, durable_ref: str, remote: str) -> None:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not repository:
        url = _git(root, "remote", "get-url", remote).decode("utf-8").strip()
        match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?", url)
        if not match:
            raise CollectionEvidenceError("exact evidence requires an explicit GitHub repository")
        repository = match.group(1)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    profile = replace(
        github_git_proof.PROFILES["dashboard-state"],
        required={MANIFEST_PATH: MAX_MANIFEST_BYTES, STATE_PATH: MAX_STATE_BYTES},
    )
    for attempt in range(2):
        try:
            proof = github_git_proof.prove_commit_tree(repository, durable_ref, profile, token=token)
            # Commit/tree metadata may now be fetched; exact required blobs
            # have independent server-size proof before any object hydration.
            _git(root, "fetch", "--no-tags", "--depth=1", "--filter=blob:none", remote, durable_ref)
            tree = _git(root, "rev-parse", f"{durable_ref}^{{tree}}").decode("ascii").strip()
            if tree != proof["tree_sha"]:
                raise CollectionEvidenceError("fetched state disagrees with its GitHub tree proof")
            for descriptor in proof["required_blobs"].values():
                oid = descriptor["oid"]
                try:
                    size = int(_git(root, "cat-file", "-s", oid))
                except CollectionEvidenceUnavailable:
                    _git(root, "fetch", "--no-tags", "--filter=blob:none", remote, oid)
                    size = int(_git(root, "cat-file", "-s", oid))
                if size != descriptor["bytes"]:
                    raise CollectionEvidenceError("fetched evidence disagrees with its GitHub size proof")
            return
        except (github_git_proof.AmbiguousProof, CollectionEvidenceUnavailable) as exc:
            if attempt:
                raise CollectionEvidenceError("exact collection state transport proof is unavailable") from exc
            time.sleep(1)
        except github_git_proof.ProofError as exc:
            raise CollectionEvidenceError("exact collection state proof was rejected") from exc


def read_collection_evidence(
    root: Path, *, durable_ref: str, reserved_at: str, succeeded_at: str, remote: str,
) -> dict[str, Any]:
    """Use immutable ledger evidence even after queue/DNS republish newer state."""
    if not isinstance(durable_ref, str) or not SHA_RE.fullmatch(durable_ref):
        raise CollectionEvidenceError("invalid durable state identity")
    try:
        return _read_local_evidence(
            root, durable_ref=durable_ref, reserved_at=reserved_at, succeeded_at=succeeded_at,
        )
    except CollectionEvidenceUnavailable:
        _hydrate_remote_evidence(root, durable_ref=durable_ref, remote=remote)
        return _read_local_evidence(
            root, durable_ref=durable_ref, reserved_at=reserved_at, succeeded_at=succeeded_at,
        )
