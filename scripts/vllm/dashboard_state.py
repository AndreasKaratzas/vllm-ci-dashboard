#!/usr/bin/env python3
"""Create and validate bounded, parentless dashboard state snapshots.

The canonical state branch is a two-slot snapshot, not an append-only history.
Each commit contains the exact tested source tree plus the generated roots, has
no parent, and carries a private manifest describing every generated blob.  A
second branch points at the previous root commit for explicit rollback.

This module deliberately keeps network mutation in one small ``rotate``
operation.  Collection, validation, commit creation, and site assembly can all
finish before either durable ref is changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

if TYPE_CHECKING or __package__:
    from .public_projection import (
        ATTESTATION_PATH as PUBLIC_PROJECTION_ATTESTATION_PATH,
        MAX_ATTESTATION_BYTES as MAX_PUBLIC_PROJECTION_ATTESTATION_BYTES,
        PublicProjectionError,
        load_attestation,
        normalize_attestation,
    )
else:  # Direct ``python scripts/vllm/dashboard_state.py`` execution.
    from importlib import import_module

    _public_projection = import_module("public_projection")
    PUBLIC_PROJECTION_ATTESTATION_PATH = _public_projection.ATTESTATION_PATH
    MAX_PUBLIC_PROJECTION_ATTESTATION_BYTES = _public_projection.MAX_ATTESTATION_BYTES
    PublicProjectionError = _public_projection.PublicProjectionError
    load_attestation = _public_projection.load_attestation
    normalize_attestation = _public_projection.normalize_attestation


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config/dashboard_state.json"
STATE_CONFIG_SCHEMA_VERSION = 1
STATE_MANIFEST_SCHEMA_VERSION = 2
MIN_COMPATIBLE_STATE_MANIFEST_SCHEMA_VERSION = STATE_MANIFEST_SCHEMA_VERSION - 1
PUBLIC_MARKER_SCHEMA_VERSION = 2
MAX_STATE_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_PUBLICATION_STATUS_BYTES = 64 * 1024
DEFAULT_MAX_BLOB_BYTES = 85 * 1024 * 1024
DEFAULT_MAX_TREE_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_FILES = 10_000
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
SAFE_GENERATION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
SAFE_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
ALLOWED_TRANSIENT_UNTRACKED = frozenset(
    {
        "live-data-test-output.txt",
        "live-publication-audit-output.txt",
        "live-publication-audit.json",
        "live-publication-audit.stderr",
        "test-output.txt",
    }
)


class DashboardStateError(RuntimeError):
    """A state snapshot failed a fail-closed contract."""


@dataclass(frozen=True)
class StatePolicy:
    branch: str
    previous_branch: str
    manifest_path: str
    generated_roots: tuple[str, ...]
    max_blob_bytes: int = DEFAULT_MAX_BLOB_BYTES
    max_tree_bytes: int = DEFAULT_MAX_TREE_BYTES
    max_files: int = DEFAULT_MAX_FILES
    bootstrap_allowed: bool = False


@dataclass(frozen=True)
class TreeEntry:
    path: str
    mode: str
    kind: str
    object_id: str
    size: int


@dataclass(frozen=True)
class ValidatedState:
    state_sha: str
    state_tree: str
    code_sha: str
    generation_id: str
    generated_at: str
    manifest: Mapping[str, Any]
    entries: Mapping[str, TreeEntry]


def _json_object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _decode_json(payload: bytes, *, label: str) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_json_object_no_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise DashboardStateError(f"{label} is not strict UTF-8 JSON: {exc}") from exc


def _safe_relative_path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 500:
        raise DashboardStateError(f"{label} must be a nonempty bounded path")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise DashboardStateError(f"{label} contains a control character")
    if "\\" in value or value.startswith("/") or value.endswith("/"):
        raise DashboardStateError(f"{label} is not a canonical POSIX path: {value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise DashboardStateError(f"{label} is not a canonical POSIX path: {value!r}")
    normalized = PurePosixPath(value).as_posix()
    if normalized != value:
        raise DashboardStateError(f"{label} is not normalized: {value!r}")
    return value


def _safe_branch(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not SAFE_REF_RE.fullmatch(value):
        raise DashboardStateError(f"{label} is not a safe branch name")
    if (
        value.startswith("-")
        or value.endswith((".", "/", ".lock"))
        or ".." in value
        or "@{" in value
        or "//" in value
        or any(part.startswith(".") for part in value.split("/"))
    ):
        raise DashboardStateError(f"{label} is not a canonical branch name")
    return value


def _full_sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not FULL_SHA_RE.fullmatch(value):
        raise DashboardStateError(f"{label} must be one full lowercase SHA-1")
    return value


def _safe_revision(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 300
        or value.startswith("-")
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        raise DashboardStateError(f"{label} is not a safe Git revision")
    return value


def _positive_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DashboardStateError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DashboardStateError(f"{label} must be a nonnegative integer")
    return value


def _canonical_timestamp(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise DashboardStateError(f"{label} must be a bounded timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DashboardStateError(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise DashboardStateError(f"{label} must include a timezone")
    canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if canonical != value:
        raise DashboardStateError(f"{label} must use canonical UTC form {canonical!r}")
    return canonical


def _load_publication_status(path: Path) -> dict[str, Any]:
    """Read the small public status document without following symlinks."""
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise DashboardStateError("publication status must be a regular file")
        if metadata.st_size > MAX_PUBLICATION_STATUS_BYTES:
            raise DashboardStateError("publication status exceeds its byte limit")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
    except DashboardStateError:
        raise
    except OSError as exc:
        raise DashboardStateError(
            f"publication status is unreadable: {type(exc).__name__}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or opened.st_size != metadata.st_size
        ):
            raise DashboardStateError("publication status changed while opening")
        chunks: list[bytes] = []
        consumed = 0
        while consumed <= MAX_PUBLICATION_STATUS_BYTES:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, MAX_PUBLICATION_STATUS_BYTES + 1 - consumed),
            )
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
        closed = os.fstat(descriptor)
        if consumed > MAX_PUBLICATION_STATUS_BYTES:
            raise DashboardStateError("publication status exceeds its byte limit")
        if (
            consumed != metadata.st_size
            or closed.st_size != metadata.st_size
            or closed.st_mtime_ns != opened.st_mtime_ns
        ):
            raise DashboardStateError("publication status changed while reading")
    except OSError as exc:
        raise DashboardStateError(
            f"publication status is unreadable: {type(exc).__name__}"
        ) from exc
    finally:
        os.close(descriptor)
    value = _decode_json(b"".join(chunks), label="publication status")
    if not isinstance(value, dict):
        raise DashboardStateError("publication status must be a JSON object")
    _canonical_timestamp(
        value.get("generated_at"), label="publication status generated_at"
    )
    return value


def _generation_id(value: object) -> str:
    if not isinstance(value, str) or not SAFE_GENERATION_RE.fullmatch(value):
        raise DashboardStateError("generation_id is not a safe bounded identifier")
    return value


def _is_generated(path: str, policy: StatePolicy) -> bool:
    return any(path == root or path.startswith(root + "/") for root in policy.generated_roots)


def load_policy(path: Path = DEFAULT_CONFIG_PATH) -> StatePolicy:
    """Load the strict state-branch policy shared by scripts and workflows."""
    try:
        payload = _decode_json(path.read_bytes(), label=str(path))
    except OSError as exc:
        raise DashboardStateError(f"dashboard state config is unreadable: {exc}") from exc
    expected = {
        "schema_version",
        "branch",
        "previous_branch",
        "manifest_path",
        "generated_roots",
        "limits",
        "bootstrap_allowed",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise DashboardStateError("dashboard state config has an unexpected shape")
    if payload.get("schema_version") != STATE_CONFIG_SCHEMA_VERSION:
        raise DashboardStateError("dashboard state config schema_version is unsupported")
    if not isinstance(payload.get("bootstrap_allowed"), bool):
        raise DashboardStateError("bootstrap_allowed must be a boolean")
    raw_roots = payload.get("generated_roots")
    if (
        not isinstance(raw_roots, list)
        or not raw_roots
        or any(not isinstance(root, str) for root in raw_roots)
    ):
        raise DashboardStateError("generated_roots must be a nonempty string array")
    roots = tuple(_safe_relative_path(root, label="generated root") for root in raw_roots)
    # Top-level, non-overlapping replacements make local materialization
    # transactional and prevent one configured root from swallowing another.
    if len(set(roots)) != len(roots) or any("/" in root for root in roots):
        raise DashboardStateError("generated_roots must be unique top-level paths")
    manifest_path = _safe_relative_path(payload.get("manifest_path"), label="manifest_path")
    provisional = StatePolicy(
        branch=_safe_branch(payload.get("branch"), label="branch"),
        previous_branch=_safe_branch(payload.get("previous_branch"), label="previous_branch"),
        manifest_path=manifest_path,
        generated_roots=roots,
        bootstrap_allowed=payload.get("bootstrap_allowed") is True,
    )
    if provisional.branch == provisional.previous_branch:
        raise DashboardStateError("current and previous state branches must differ")
    if not _is_generated(manifest_path, provisional):
        raise DashboardStateError("manifest_path must be inside a generated root")
    limits = payload.get("limits")
    if not isinstance(limits, dict) or set(limits) != {
        "max_blob_bytes",
        "max_tree_bytes",
        "max_files",
    }:
        raise DashboardStateError("dashboard state limits have an unexpected shape")
    policy = StatePolicy(
        branch=provisional.branch,
        previous_branch=provisional.previous_branch,
        manifest_path=manifest_path,
        generated_roots=roots,
        max_blob_bytes=_positive_int(limits.get("max_blob_bytes"), label="limits.max_blob_bytes"),
        max_tree_bytes=_positive_int(limits.get("max_tree_bytes"), label="limits.max_tree_bytes"),
        max_files=_positive_int(limits.get("max_files"), label="limits.max_files"),
        bootstrap_allowed=provisional.bootstrap_allowed,
    )
    if policy.max_blob_bytes >= 90_000_000:
        raise DashboardStateError("max_blob_bytes must remain below 90,000,000")
    if policy.max_tree_bytes < policy.max_blob_bytes:
        raise DashboardStateError("max_tree_bytes must be at least max_blob_bytes")
    if (
        policy.max_blob_bytes > DEFAULT_MAX_BLOB_BYTES
        or policy.max_tree_bytes > DEFAULT_MAX_TREE_BYTES
        or policy.max_files > DEFAULT_MAX_FILES
    ):
        raise DashboardStateError("dashboard state limits exceed immutable hard bounds")
    return policy


def _git(
    root: Path,
    *args: str,
    input_bytes: bytes | None = None,
    no_lazy_fetch: bool = False,
) -> bytes:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if no_lazy_fetch:
        env["GIT_NO_LAZY_FETCH"] = "1"
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()[-2000:]
        command = " ".join(("git", *args[:3]))
        raise DashboardStateError(f"{command} failed" + (f": {detail}" if detail else "")) from exc
    return result.stdout


def _resolve_commit(root: Path, ref: str, *, label: str) -> str:
    ref = _safe_revision(ref, label=label)
    try:
        resolved = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    except DashboardStateError as exc:
        raise DashboardStateError(f"{label} does not resolve to a commit") from exc
    return _full_sha(resolved.decode("ascii").strip(), label=label)


def _commit_parents(root: Path, commit_sha: str) -> tuple[str, ...]:
    """Read real commit headers even when a depth-one fetch marks it shallow."""
    payload = _git(root, "cat-file", "commit", commit_sha)
    headers = payload.split(b"\n\n", 1)[0].splitlines()
    parents = []
    for line in headers:
        if not line.startswith(b"parent "):
            continue
        try:
            raw_parent = line.split(b" ", 1)[1].decode("ascii")
        except (IndexError, UnicodeDecodeError) as exc:
            raise DashboardStateError("state commit has an invalid parent header") from exc
        parents.append(_full_sha(raw_parent, label="state parent"))
    return tuple(parents)


def _tree_entries(root: Path, treeish: str) -> dict[str, TreeEntry]:
    raw = _git(root, "ls-tree", "-r", "-z", "-l", treeish)
    entries: dict[str, TreeEntry] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode_raw, kind_raw, object_raw, size_raw = metadata.split()
            path = encoded_path.decode("utf-8")
            mode = mode_raw.decode("ascii")
            kind = kind_raw.decode("ascii")
            object_id = object_raw.decode("ascii")
            # ``git ls-tree -l`` reports ``-`` when a promisor blob is not
            # available locally and lazy fetching is disabled.  Unknown is
            # not zero: preserve a sentinel so the size validator fails
            # closed before any content read or checkout can hydrate it.
            size = int(size_raw) if size_raw != b"-" else -1
        except (ValueError, UnicodeDecodeError) as exc:
            raise DashboardStateError("state tree contains an invalid Git entry") from exc
        _safe_relative_path(path, label="tree path")
        if path in entries:
            raise DashboardStateError(f"state tree repeats path {path!r}")
        entries[path] = TreeEntry(path, mode, kind, object_id, size)
    return entries


def _tree_entries_metadata(root: Path, treeish: str) -> dict[str, TreeEntry]:
    """Read tree identity without asking Git for any blob content or size."""
    raw = _git(
        root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        treeish,
        no_lazy_fetch=True,
    )
    entries: dict[str, TreeEntry] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode_raw, kind_raw, object_raw = metadata.split()
            path = encoded_path.decode("utf-8")
            mode = mode_raw.decode("ascii")
            kind = kind_raw.decode("ascii")
            object_id = object_raw.decode("ascii")
        except (ValueError, UnicodeDecodeError) as exc:
            raise DashboardStateError("state tree contains an invalid Git entry") from exc
        _safe_relative_path(path, label="tree path")
        if path in entries:
            raise DashboardStateError(f"state tree repeats path {path!r}")
        entries[path] = TreeEntry(
            path=path,
            mode=mode,
            kind=kind,
            object_id=_full_sha(object_id, label=f"tree object for {path}"),
            size=-1,
        )
    return entries


def _index_entries(root: Path) -> dict[str, TreeEntry]:
    raw = _git(root, "ls-files", "--stage", "-z")
    entries: dict[str, TreeEntry] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode_raw, object_raw, stage_raw = metadata.split()
            path = encoded_path.decode("utf-8")
            mode = mode_raw.decode("ascii")
            object_id = object_raw.decode("ascii")
            stage = stage_raw.decode("ascii")
        except (ValueError, UnicodeDecodeError) as exc:
            raise DashboardStateError("Git index contains an invalid entry") from exc
        if stage != "0":
            raise DashboardStateError(f"Git index has an unresolved entry at {path!r}")
        _safe_relative_path(path, label="index path")
        kind = "commit" if mode == "160000" else "blob"
        size = 0
        if kind == "blob":
            try:
                size = int(_git(root, "cat-file", "-s", object_id))
            except ValueError as exc:
                raise DashboardStateError(f"blob size for {path!r} is invalid") from exc
        entries[path] = TreeEntry(path, mode, kind, object_id, size)
    return entries


def _validate_entry_shapes(
    entries: Mapping[str, TreeEntry],
    policy: StatePolicy,
    *,
    label: str,
) -> None:
    if len(entries) > policy.max_files:
        raise DashboardStateError(f"{label} has {len(entries)} files (max {policy.max_files})")
    for path, entry in entries.items():
        _safe_relative_path(path, label=f"{label} path")
        if entry.kind == "commit" or entry.mode == "160000":
            raise DashboardStateError(f"{label} contains a submodule at {path}")
        if entry.kind != "blob":
            raise DashboardStateError(f"{label} contains unsupported object {path}")
        if entry.mode == "120000":
            raise DashboardStateError(f"{label} contains a symlink at {path}")
        if entry.mode not in {"100644", "100755"}:
            raise DashboardStateError(f"{label} has unsupported mode {entry.mode} at {path}")


def _validate_entry_limits(
    entries: Mapping[str, TreeEntry],
    policy: StatePolicy,
    *,
    label: str,
) -> None:
    _validate_entry_shapes(entries, policy, label=label)
    total = 0
    for path, entry in entries.items():
        if entry.size < 0:
            raise DashboardStateError(f"{label} has a negative blob size at {path}")
        if entry.size > policy.max_blob_bytes:
            raise DashboardStateError(
                f"{label} blob {path} is {entry.size} bytes (max {policy.max_blob_bytes})"
            )
        total += entry.size
    if total > policy.max_tree_bytes:
        raise DashboardStateError(f"{label} is {total} bytes (max {policy.max_tree_bytes})")


def _cat_blob(root: Path, entry: TreeEntry) -> bytes:
    payload = _git(root, "cat-file", "blob", entry.object_id)
    if len(payload) != entry.size:
        raise DashboardStateError(f"blob {entry.path} changed size while it was being validated")
    return payload


def _cat_metadata_blob(
    root: Path,
    entry: TreeEntry,
    *,
    limit: int,
    label: str,
) -> bytes:
    """Read one explicitly small metadata blob without lazy-fetch fallback."""
    payload = _git(
        root,
        "cat-file",
        "blob",
        entry.object_id,
        no_lazy_fetch=True,
    )
    if len(payload) > limit:
        raise DashboardStateError(f"{label} exceeds its {limit}-byte limit")
    return payload


def _descriptor(root: Path, entry: TreeEntry) -> dict[str, Any]:
    payload = _cat_blob(root, entry)
    return {
        "bytes": entry.size,
        "git_oid": entry.object_id,
        "mode": entry.mode,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _validate_descriptor(value: object, *, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "bytes",
        "git_oid",
        "mode",
        "sha256",
    }:
        raise DashboardStateError(f"generated descriptor for {path} is malformed")
    size = value.get("bytes")
    git_oid = value.get("git_oid")
    mode = value.get("mode")
    digest = value.get("sha256")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise DashboardStateError(f"generated descriptor for {path} has invalid bytes")
    if mode not in {"100644", "100755"}:
        raise DashboardStateError(f"generated descriptor for {path} has invalid mode")
    git_oid = _full_sha(git_oid, label=f"generated descriptor for {path} git_oid")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise DashboardStateError(f"generated descriptor for {path} has invalid sha256")
    return {"bytes": size, "git_oid": git_oid, "mode": mode, "sha256": digest}


def _normalize_source_refs(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 32:
        raise DashboardStateError("source_refs must be a bounded object")
    refs: dict[str, str] = {}
    for raw_branch, raw_sha in value.items():
        branch = _safe_branch(raw_branch, label="source_refs branch")
        refs[branch] = _full_sha(raw_sha, label=f"source_refs[{branch!r}]")
    return dict(sorted(refs.items()))


def _normalize_manifest(value: object, policy: StatePolicy) -> dict[str, Any]:
    expected = {
        "schema_version",
        "generation_id",
        "generated_at",
        "code_sha",
        "source_refs",
        "generated_roots",
        "limits",
        "content_summary",
        "generated_files",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise DashboardStateError("dashboard state manifest has an unexpected shape")
    schema_version = value.get("schema_version")
    if schema_version not in {
        MIN_COMPATIBLE_STATE_MANIFEST_SCHEMA_VERSION,
        STATE_MANIFEST_SCHEMA_VERSION,
    }:
        raise DashboardStateError("dashboard state manifest schema_version is unsupported")
    roots = value.get("generated_roots")
    if roots != list(policy.generated_roots):
        raise DashboardStateError("dashboard state manifest generated_roots disagree with policy")
    raw_limits = value.get("limits")
    if not isinstance(raw_limits, dict) or set(raw_limits) != {
        "max_blob_bytes",
        "max_tree_bytes",
        "max_files",
    }:
        raise DashboardStateError("dashboard state manifest limits are malformed")
    limits = {
        "max_blob_bytes": _positive_int(
            raw_limits.get("max_blob_bytes"), label="manifest limits.max_blob_bytes"
        ),
        "max_tree_bytes": _positive_int(
            raw_limits.get("max_tree_bytes"), label="manifest limits.max_tree_bytes"
        ),
        "max_files": _positive_int(
            raw_limits.get("max_files"), label="manifest limits.max_files"
        ),
    }
    # The manifest records the producer's policy.  N/N-1 validators may use a
    # different (usually tightened) policy, but neither generation is allowed
    # to exceed the absolute repository envelope.  Content is checked against
    # both policies below, so accepting the older declaration never weakens the
    # current validator.
    if limits["max_blob_bytes"] >= 90_000_000:
        raise DashboardStateError("dashboard state manifest max_blob_bytes is unsafe")
    if limits["max_tree_bytes"] < limits["max_blob_bytes"]:
        raise DashboardStateError("dashboard state manifest limits are inconsistent")
    if (
        limits["max_blob_bytes"] > DEFAULT_MAX_BLOB_BYTES
        or limits["max_tree_bytes"] > DEFAULT_MAX_TREE_BYTES
        or limits["max_files"] > DEFAULT_MAX_FILES
    ):
        raise DashboardStateError("dashboard state manifest limits exceed hard bounds")
    raw_summary = value.get("content_summary")
    if not isinstance(raw_summary, dict) or set(raw_summary) != {
        "file_count",
        "max_blob_bytes",
        "total_bytes",
    }:
        raise DashboardStateError("dashboard state content_summary is malformed")
    content_summary = {
        "file_count": _nonnegative_int(
            raw_summary.get("file_count"), label="content_summary.file_count"
        ),
        "max_blob_bytes": _nonnegative_int(
            raw_summary.get("max_blob_bytes"), label="content_summary.max_blob_bytes"
        ),
        "total_bytes": _nonnegative_int(
            raw_summary.get("total_bytes"), label="content_summary.total_bytes"
        ),
    }
    if content_summary["file_count"] > min(policy.max_files, limits["max_files"]):
        raise DashboardStateError("dashboard state content_summary exceeds max_files")
    if content_summary["max_blob_bytes"] > min(
        policy.max_blob_bytes, limits["max_blob_bytes"]
    ):
        raise DashboardStateError("dashboard state content_summary exceeds max_blob_bytes")
    if content_summary["total_bytes"] > min(
        policy.max_tree_bytes, limits["max_tree_bytes"]
    ):
        raise DashboardStateError("dashboard state content_summary exceeds max_tree_bytes")
    if content_summary["max_blob_bytes"] > content_summary["total_bytes"]:
        raise DashboardStateError("dashboard state content_summary is internally inconsistent")
    raw_files = value.get("generated_files")
    if not isinstance(raw_files, dict) or len(raw_files) > min(
        policy.max_files, limits["max_files"]
    ):
        raise DashboardStateError("generated_files must be a bounded object")
    files: dict[str, dict[str, Any]] = {}
    for raw_path, descriptor in raw_files.items():
        path = _safe_relative_path(raw_path, label="generated_files path")
        if not _is_generated(path, policy) or path == policy.manifest_path:
            raise DashboardStateError(f"generated_files contains an invalid path {path!r}")
        files[path] = _validate_descriptor(descriptor, path=path)
    return {
        "schema_version": schema_version,
        "generation_id": _generation_id(value.get("generation_id")),
        "generated_at": _canonical_timestamp(value.get("generated_at"), label="generated_at"),
        "code_sha": _full_sha(value.get("code_sha"), label="code_sha"),
        "source_refs": _normalize_source_refs(value.get("source_refs")),
        "generated_roots": list(policy.generated_roots),
        "limits": limits,
        "content_summary": content_summary,
        "generated_files": dict(sorted(files.items())),
    }


def _canonical_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()


def _normalize_canonical_manifest(
    raw: bytes,
    policy: StatePolicy,
    *,
    label: str = "dashboard state manifest",
) -> dict[str, Any]:
    if len(raw) > min(policy.max_blob_bytes, MAX_STATE_MANIFEST_BYTES):
        raise DashboardStateError("dashboard state manifest is unexpectedly large")
    manifest = _normalize_manifest(_decode_json(raw, label=label), policy)
    if raw != _canonical_manifest_bytes(manifest):
        raise DashboardStateError("dashboard state manifest is not canonical JSON")
    return manifest


def _content_summary(
    entries: Mapping[str, TreeEntry],
    *,
    manifest_path: str,
) -> dict[str, int]:
    content = [entry for path, entry in entries.items() if path != manifest_path]
    if any(entry.size < 0 for entry in content):
        raise DashboardStateError("cannot calculate content_summary without blob sizes")
    sizes = [entry.size for entry in content]
    return {
        "file_count": len(content),
        "max_blob_bytes": max(sizes, default=0),
        "total_bytes": sum(sizes),
    }


def _validate_full_content_summary(
    entries: Mapping[str, TreeEntry],
    manifest: Mapping[str, Any],
    policy: StatePolicy,
) -> None:
    calculated = _content_summary(entries, manifest_path=policy.manifest_path)
    if manifest["content_summary"] != calculated:
        raise DashboardStateError("dashboard state content_summary does not match Git")
    manifest_entry = entries.get(policy.manifest_path)
    if manifest_entry is None or manifest_entry.size < 0:
        raise DashboardStateError("dashboard state manifest size is unavailable")
    declared_limits = manifest["limits"]
    if len(entries) > declared_limits["max_files"]:
        raise DashboardStateError("dashboard state tree exceeds its declared max_files")
    if manifest_entry.size > declared_limits["max_blob_bytes"]:
        raise DashboardStateError("dashboard state manifest exceeds its declared blob limit")
    if calculated["total_bytes"] + manifest_entry.size > declared_limits["max_tree_bytes"]:
        raise DashboardStateError("dashboard state tree exceeds its declared byte limit")


def _validate_generated_manifest(
    root: Path,
    entries: Mapping[str, TreeEntry],
    manifest: Mapping[str, Any],
    policy: StatePolicy,
) -> None:
    manifest_entry = entries.get(policy.manifest_path)
    if manifest_entry is None:
        raise DashboardStateError("state tree is missing its dashboard state manifest")
    if manifest_entry.mode != "100644" or manifest_entry.kind != "blob":
        raise DashboardStateError("dashboard state manifest must be a regular file")
    actual_paths = {
        path for path in entries if _is_generated(path, policy) and path != policy.manifest_path
    }
    declared = set(manifest["generated_files"])
    if actual_paths != declared:
        missing = sorted(actual_paths - declared)[:10]
        extra = sorted(declared - actual_paths)[:10]
        raise DashboardStateError(
            "dashboard state generated file set does not match its manifest "
            f"(undeclared={missing}, missing={extra})"
        )
    for generated_root in policy.generated_roots:
        if not any(
            path == generated_root or path.startswith(generated_root + "/") for path in entries
        ):
            raise DashboardStateError(
                f"state tree has no content for generated root {generated_root!r}"
            )
    for path in sorted(actual_paths):
        entry = entries[path]
        descriptor = manifest["generated_files"][path]
        if (
            descriptor["bytes"] != entry.size
            or descriptor["mode"] != entry.mode
            or descriptor["git_oid"] != entry.object_id
        ):
            raise DashboardStateError(f"generated metadata for {path} does not match Git")
        digest = hashlib.sha256(_cat_blob(root, entry)).hexdigest()
        if digest != descriptor["sha256"]:
            raise DashboardStateError(f"generated hash for {path} does not match Git")


def _validate_state_projection_attestation(
    root: Path,
    entries: Mapping[str, TreeEntry],
) -> tuple[dict[str, Any], bytes]:
    entry = entries.get(PUBLIC_PROJECTION_ATTESTATION_PATH)
    if entry is None:
        raise DashboardStateError("state tree is missing its public projection attestation")
    if entry.mode != "100644" or entry.kind != "blob":
        raise DashboardStateError("state public projection attestation must be a regular file")
    raw = (
        _cat_metadata_blob(
            root,
            entry,
            limit=MAX_PUBLIC_PROJECTION_ATTESTATION_BYTES,
            label="state public projection attestation",
        )
        if entry.size < 0
        else _cat_blob(root, entry)
    )
    if len(raw) > MAX_PUBLIC_PROJECTION_ATTESTATION_BYTES:
        raise DashboardStateError("state public projection attestation exceeds its byte limit")
    try:
        normalized = normalize_attestation(
            _decode_json(raw, label="state public projection attestation")
        )
    except PublicProjectionError as exc:
        raise DashboardStateError(f"state public projection attestation is invalid: {exc}") from exc
    canonical = (json.dumps(normalized, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if raw != canonical:
        raise DashboardStateError("state public projection attestation is not canonical JSON")
    return normalized, raw


def _entry_identity(entry: TreeEntry) -> tuple[str, str, str, int]:
    return entry.mode, entry.kind, entry.object_id, entry.size


def _validate_code_identity(
    state_entries: Mapping[str, TreeEntry],
    code_entries: Mapping[str, TreeEntry],
    policy: StatePolicy,
) -> None:
    state_source = {
        path: _entry_identity(entry)
        for path, entry in state_entries.items()
        if not _is_generated(path, policy)
    }
    code_source = {
        path: _entry_identity(entry)
        for path, entry in code_entries.items()
        if not _is_generated(path, policy)
    }
    if state_source != code_source:
        changed = sorted(set(state_source) ^ set(code_source))
        if not changed:
            changed = sorted(
                path for path in state_source if state_source[path] != code_source[path]
            )
        raise DashboardStateError(
            f"state source tree differs from its declared code commit: {changed[:10]}"
        )


def validate_state_ref(
    root: Path,
    ref: str,
    policy: StatePolicy,
    *,
    expected_code_sha: str | None = None,
) -> ValidatedState:
    """Validate a root state commit and every bounded generated descriptor."""
    root = root.resolve()
    state_sha = _resolve_commit(root, ref, label="state ref")
    if _commit_parents(root, state_sha):
        raise DashboardStateError("dashboard state commit must be parentless")
    state_tree = _full_sha(
        _git(root, "rev-parse", f"{state_sha}^{{tree}}").decode("ascii").strip(),
        label="state tree",
    )
    entries = _tree_entries(root, state_sha)
    _validate_entry_limits(entries, policy, label="dashboard state tree")
    manifest_entry = entries.get(policy.manifest_path)
    if manifest_entry is None:
        raise DashboardStateError("state tree is missing its dashboard state manifest")
    if manifest_entry.size > min(policy.max_blob_bytes, MAX_STATE_MANIFEST_BYTES):
        raise DashboardStateError("dashboard state manifest is unexpectedly large")
    manifest = _normalize_canonical_manifest(
        _cat_blob(root, manifest_entry),
        policy,
    )
    _validate_full_content_summary(entries, manifest, policy)
    _validate_generated_manifest(root, entries, manifest, policy)
    _validate_state_projection_attestation(root, entries)
    code_sha = manifest["code_sha"]
    if expected_code_sha is not None:
        expected = _full_sha(expected_code_sha, label="expected code SHA")
        if code_sha != expected:
            raise DashboardStateError(
                f"state code_sha {code_sha} does not match expected {expected}"
            )
    # A caller can first inspect a state whose declared code object has not yet
    # been fetched. Whenever it is available—or explicitly expected—verify the
    # stronger invariant that every non-generated byte is exactly that commit.
    code_object_available = (
        subprocess.run(
            ["git", "cat-file", "-e", f"{code_sha}^{{commit}}"],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )
    if expected_code_sha is not None and not code_object_available:
        raise DashboardStateError("expected code commit is unavailable locally")
    if code_object_available:
        resolved_code = _resolve_commit(root, code_sha, label="manifest code_sha")
        if resolved_code != code_sha:
            raise DashboardStateError("manifest code_sha did not resolve exactly")
        # The state commit already contains, size-bounds, and fully validates
        # every source blob that can execute.  Generated roots in the declared
        # code commit are deliberately replaced by the state generation and
        # must never be hydrated merely to compare source identity.  Compare
        # the code tree by mode/type/OID only; callers that fetched a partial
        # code commit can therefore keep those irrelevant generated blobs
        # absent without weakening the byte checks on the state itself.
        code_entries = _tree_entries_metadata(root, code_sha)
        _validate_entry_shapes(code_entries, policy, label="declared code tree")
        _validate_code_identity_metadata(entries, code_entries, policy)
    return ValidatedState(
        state_sha=state_sha,
        state_tree=state_tree,
        code_sha=code_sha,
        generation_id=manifest["generation_id"],
        generated_at=manifest["generated_at"],
        manifest=manifest,
        entries=entries,
    )


def _validate_generated_manifest_metadata(
    entries: Mapping[str, TreeEntry],
    manifest: Mapping[str, Any],
    policy: StatePolicy,
    *,
    manifest_bytes: int,
    attestation_bytes: int,
) -> None:
    manifest_entry = entries.get(policy.manifest_path)
    if manifest_entry is None:
        raise DashboardStateError("state tree is missing its dashboard state manifest")
    if manifest_entry.mode != "100644" or manifest_entry.kind != "blob":
        raise DashboardStateError("dashboard state manifest must be a regular file")
    actual_paths = {
        path for path in entries if _is_generated(path, policy) and path != policy.manifest_path
    }
    declared = set(manifest["generated_files"])
    if actual_paths != declared:
        missing = sorted(actual_paths - declared)[:10]
        extra = sorted(declared - actual_paths)[:10]
        raise DashboardStateError(
            "dashboard state generated file set does not match its manifest "
            f"(undeclared={missing}, missing={extra})"
        )
    for generated_root in policy.generated_roots:
        if not any(
            path == generated_root or path.startswith(generated_root + "/") for path in entries
        ):
            raise DashboardStateError(
                f"state tree has no content for generated root {generated_root!r}"
            )
    summary = manifest["content_summary"]
    declared_limits = manifest["limits"]
    if summary["file_count"] != len(entries) - 1:
        raise DashboardStateError("dashboard state content_summary file count disagrees with Git")
    if len(entries) > min(policy.max_files, declared_limits["max_files"]):
        raise DashboardStateError("dashboard state metadata declares too many files")
    if manifest_bytes > min(policy.max_blob_bytes, declared_limits["max_blob_bytes"]):
        raise DashboardStateError("dashboard state manifest exceeds max_blob_bytes")
    if max(summary["max_blob_bytes"], manifest_bytes) > min(
        policy.max_blob_bytes, declared_limits["max_blob_bytes"]
    ):
        raise DashboardStateError("dashboard state metadata declares an oversized blob")
    if summary["total_bytes"] + manifest_bytes > min(
        policy.max_tree_bytes, declared_limits["max_tree_bytes"]
    ):
        raise DashboardStateError("dashboard state metadata declares an oversized tree")
    known_generated_total = 0
    for path in sorted(actual_paths):
        entry = entries[path]
        descriptor = manifest["generated_files"][path]
        if descriptor["mode"] != entry.mode or descriptor["git_oid"] != entry.object_id:
            raise DashboardStateError(f"generated Git identity for {path} does not match manifest")
        declared_size = descriptor["bytes"]
        if declared_size > min(policy.max_blob_bytes, declared_limits["max_blob_bytes"]):
            raise DashboardStateError(
                f"declared generated blob {path} is {declared_size} bytes "
                f"(max {policy.max_blob_bytes})"
            )
        if declared_size > summary["max_blob_bytes"]:
            raise DashboardStateError(
                f"declared generated blob {path} exceeds content_summary maximum"
            )
        known_generated_total += declared_size
    if known_generated_total > summary["total_bytes"]:
        raise DashboardStateError(
            "generated descriptor bytes exceed dashboard state content_summary"
        )
    projection_descriptor = manifest["generated_files"].get(PUBLIC_PROJECTION_ATTESTATION_PATH)
    if projection_descriptor is None or projection_descriptor["bytes"] != attestation_bytes:
        raise DashboardStateError(
            "public projection attestation byte count disagrees with state manifest"
        )


def _validate_code_identity_metadata(
    state_entries: Mapping[str, TreeEntry],
    code_entries: Mapping[str, TreeEntry],
    policy: StatePolicy,
) -> None:
    def identity(entry: TreeEntry) -> tuple[str, str, str]:
        return entry.mode, entry.kind, entry.object_id

    state_source = {
        path: identity(entry)
        for path, entry in state_entries.items()
        if not _is_generated(path, policy)
    }
    code_source = {
        path: identity(entry)
        for path, entry in code_entries.items()
        if not _is_generated(path, policy)
    }
    if state_source != code_source:
        changed = sorted(set(state_source) ^ set(code_source))
        if not changed:
            changed = sorted(
                path for path in state_source if state_source[path] != code_source[path]
            )
        raise DashboardStateError(
            f"state source tree differs from expected code tree: {changed[:10]}"
        )


def validate_state_ref_metadata(
    root: Path,
    ref: str,
    policy: StatePolicy,
    *,
    expected_code_sha: str,
) -> ValidatedState:
    """Validate state identity without reading generated or source data blobs.

    Only the bounded dashboard manifest and projection attestation blobs are
    read. Generated content is bound by Git OID/mode and declared byte limits;
    non-generated content is compared by exact tree identity with the required
    code commit. Callers must fetch both commits and their trees plus the two
    small metadata blobs before invoking this function.
    """
    root = root.resolve()
    expected_code_sha = _full_sha(expected_code_sha, label="expected code SHA")
    state_sha = _resolve_commit(root, ref, label="state ref")
    if _commit_parents(root, state_sha):
        raise DashboardStateError("dashboard state commit must be parentless")
    state_tree = _full_sha(
        _git(root, "rev-parse", f"{state_sha}^{{tree}}").decode("ascii").strip(),
        label="state tree",
    )
    entries = _tree_entries_metadata(root, state_sha)
    _validate_entry_shapes(entries, policy, label="dashboard state tree")
    manifest_entry = entries.get(policy.manifest_path)
    if manifest_entry is None:
        raise DashboardStateError("state tree is missing its dashboard state manifest")
    if manifest_entry.mode != "100644" or manifest_entry.kind != "blob":
        raise DashboardStateError("dashboard state manifest must be a regular file")
    manifest_raw = _cat_metadata_blob(
        root,
        manifest_entry,
        limit=min(policy.max_blob_bytes, MAX_STATE_MANIFEST_BYTES),
        label="dashboard state manifest",
    )
    manifest = _normalize_canonical_manifest(manifest_raw, policy)
    if manifest["code_sha"] != expected_code_sha:
        raise DashboardStateError(
            f"state code_sha {manifest['code_sha']} does not match expected {expected_code_sha}"
        )
    _, projection_raw = _validate_state_projection_attestation(root, entries)
    _validate_generated_manifest_metadata(
        entries,
        manifest,
        policy,
        manifest_bytes=len(manifest_raw),
        attestation_bytes=len(projection_raw),
    )

    resolved_code = _resolve_commit(root, expected_code_sha, label="expected code SHA")
    if resolved_code != expected_code_sha:
        raise DashboardStateError("expected code SHA did not resolve exactly")
    code_entries = _tree_entries_metadata(root, expected_code_sha)
    _validate_entry_shapes(code_entries, policy, label="expected code tree")
    _validate_code_identity_metadata(entries, code_entries, policy)
    return ValidatedState(
        state_sha=state_sha,
        state_tree=state_tree,
        code_sha=expected_code_sha,
        generation_id=manifest["generation_id"],
        generated_at=manifest["generated_at"],
        manifest=manifest,
        entries=entries,
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            staged = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(staged, mode)
        os.replace(staged, path)
        staged = None
        _fsync_directory(path.parent)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def _write_staged_blob(path: Path, payload: bytes, mode: str) -> None:
    file_mode = 0o755 if mode == "100755" else 0o644
    _atomic_write(path, payload, mode=file_mode)


def materialize_generated_roots(
    root: Path,
    ref: str,
    policy: StatePolicy,
    *,
    expected_code_sha: str | None = None,
) -> ValidatedState:
    """Replace only generated roots from a fully preflighted state snapshot.

    Every blob is written and hash-checked in a same-filesystem staging area
    before the first live path is renamed. If a rename fails, already replaced
    roots are moved aside and the original roots are restored.
    """
    root = root.resolve()
    state = validate_state_ref(root, ref, policy, expected_code_sha=expected_code_sha)
    stage = Path(tempfile.mkdtemp(prefix=".dashboard-state-stage-", dir=root))
    backup = Path(tempfile.mkdtemp(prefix=".dashboard-state-backup-", dir=root))
    installed: list[str] = []
    backed_up: list[str] = []
    try:
        generated_entries = {
            path: entry for path, entry in state.entries.items() if _is_generated(path, policy)
        }
        for relative, entry in sorted(generated_entries.items()):
            descriptor = (
                None
                if relative == policy.manifest_path
                else state.manifest["generated_files"][relative]
            )
            payload = _cat_blob(root, entry)
            if (
                descriptor is not None
                and hashlib.sha256(payload).hexdigest() != descriptor["sha256"]
            ):
                raise DashboardStateError(
                    f"generated blob {relative} changed during materialization"
                )
            _write_staged_blob(stage / relative, payload, entry.mode)

        for generated_root in policy.generated_roots:
            staged_root = stage / generated_root
            if not staged_root.exists():
                raise DashboardStateError(f"staged state omitted generated root {generated_root!r}")
            target = root / generated_root
            if target.is_symlink():
                raise DashboardStateError(f"live generated root {generated_root!r} is a symlink")
            if target.exists():
                os.replace(target, backup / generated_root)
                backed_up.append(generated_root)
            os.replace(staged_root, target)
            installed.append(generated_root)
        _fsync_directory(root)
    except Exception as exc:
        rollback_errors: list[str] = []
        for generated_root in reversed(installed):
            target = root / generated_root
            try:
                if target.exists() or target.is_symlink():
                    os.replace(target, stage / generated_root)
            except OSError as rollback_exc:
                rollback_errors.append(f"remove {generated_root}: {rollback_exc}")
        for generated_root in reversed(backed_up):
            original = backup / generated_root
            try:
                if original.exists() or original.is_symlink():
                    os.replace(original, root / generated_root)
            except OSError as rollback_exc:
                rollback_errors.append(f"restore {generated_root}: {rollback_exc}")
        try:
            _fsync_directory(root)
        except OSError as rollback_exc:
            rollback_errors.append(f"fsync root: {rollback_exc}")
        if rollback_errors:
            raise DashboardStateError(
                "generated-root materialization failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
            ) from exc
        if isinstance(exc, DashboardStateError):
            raise
        raise DashboardStateError(f"generated-root materialization failed: {exc}") from exc
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)
    return state


def _parse_source_ref_args(values: Iterable[str]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise DashboardStateError("--source-ref must use BRANCH=SHA")
        raw_branch, raw_sha = value.split("=", 1)
        branch = _safe_branch(raw_branch, label="source ref branch")
        if branch in refs:
            raise DashboardStateError(f"duplicate source ref {branch!r}")
        refs[branch] = _full_sha(raw_sha, label=f"source ref {branch!r}")
    return dict(sorted(refs.items()))


def _assert_index_code_identity(
    root: Path,
    entries: Mapping[str, TreeEntry],
    code_sha: str,
    policy: StatePolicy,
) -> None:
    resolved = _resolve_commit(root, code_sha, label="code_sha")
    if resolved != code_sha:
        raise DashboardStateError("code_sha did not resolve exactly")
    code_entries = _tree_entries(root, code_sha)
    _validate_code_identity(entries, code_entries, policy)


def _assert_generated_worktree_is_staged(root: Path, policy: StatePolicy) -> None:
    result = subprocess.run(
        ["git", "diff", "--quiet", "--", *policy.generated_roots],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if result.returncode not in {0, 1}:
        raise DashboardStateError("could not compare generated worktree with its index")
    if result.returncode == 1:
        raise DashboardStateError("generated worktree has unstaged tracked changes")
    untracked = _git(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        *policy.generated_roots,
    )
    if untracked:
        paths = [
            value.decode("utf-8", errors="replace") for value in untracked.split(b"\0") if value
        ]
        raise DashboardStateError(f"generated worktree has untracked output: {paths[:10]}")
    whole_tree = subprocess.run(
        ["git", "diff", "--quiet"],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if whole_tree.returncode not in {0, 1}:
        raise DashboardStateError("could not compare tracked worktree with its index")
    if whole_tree.returncode == 1:
        raise DashboardStateError("source worktree has unstaged tracked changes")
    all_untracked = _git(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    unexpected_untracked = sorted(
        path
        for raw in all_untracked.split(b"\0")
        if raw
        for path in [raw.decode("utf-8", errors="replace")]
        if path not in ALLOWED_TRANSIENT_UNTRACKED
    )
    if unexpected_untracked:
        raise DashboardStateError(
            f"candidate worktree has untracked non-artifact paths: {unexpected_untracked[:10]}"
        )


def _assert_generated_worktree_paths_are_safe(
    root: Path,
    policy: StatePolicy,
) -> None:
    for generated_root in policy.generated_roots:
        target = root / generated_root
        if target.is_symlink():
            raise DashboardStateError(f"live generated root {generated_root!r} is a symlink")
    current = root
    for part in PurePosixPath(policy.manifest_path).parts[:-1]:
        current /= part
        if current.is_symlink():
            raise DashboardStateError(f"manifest parent path escapes through symlink {current}")
    try:
        current.resolve().relative_to(root)
    except ValueError as exc:
        raise DashboardStateError("manifest parent path escapes repository root") from exc


def prepare_manifest(
    root: Path,
    policy: StatePolicy,
    *,
    code_sha: str,
    generation_id: str,
    generated_at: str,
    source_refs: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Write and stage the self-excluding manifest for the current Git index."""
    root = root.resolve()
    code_sha = _full_sha(code_sha, label="code_sha")
    generation_id = _generation_id(generation_id)
    generated_at = _canonical_timestamp(generated_at, label="generated_at")
    normalized_refs = _normalize_source_refs(dict(source_refs or {}))
    _assert_generated_worktree_paths_are_safe(root, policy)
    entries = _index_entries(root)
    _validate_entry_limits(entries, policy, label="candidate state index")
    _assert_index_code_identity(root, entries, code_sha, policy)
    generated = {
        path: _descriptor(root, entry)
        for path, entry in sorted(entries.items())
        if _is_generated(path, policy) and path != policy.manifest_path
    }
    for generated_root in policy.generated_roots:
        if not any(
            path == generated_root or path.startswith(generated_root + "/") for path in entries
        ):
            raise DashboardStateError(
                f"candidate index has no content for generated root {generated_root!r}"
            )
    manifest = {
        "schema_version": STATE_MANIFEST_SCHEMA_VERSION,
        "generation_id": generation_id,
        "generated_at": generated_at,
        "code_sha": code_sha,
        "source_refs": normalized_refs,
        "generated_roots": list(policy.generated_roots),
        "limits": {
            "max_blob_bytes": policy.max_blob_bytes,
            "max_tree_bytes": policy.max_tree_bytes,
            "max_files": policy.max_files,
        },
        "content_summary": _content_summary(
            entries,
            manifest_path=policy.manifest_path,
        ),
        "generated_files": generated,
    }
    encoded = _canonical_manifest_bytes(manifest)
    manifest_file = root / policy.manifest_path
    _atomic_write(manifest_file, encoded)
    _git(root, "add", "--", policy.manifest_path)
    validated = validate_index(root, policy, expected_code_sha=code_sha)
    _assert_generated_worktree_is_staged(root, policy)
    return validated


def refresh_manifest(
    root: Path,
    policy: StatePolicy,
    *,
    expected_code_sha: str | None = None,
) -> dict[str, Any]:
    """Refresh generated descriptors without accepting new generation metadata.

    The authoritative generation identity comes from the already staged,
    structurally valid manifest.  This is used after the public projection
    attestation has been staged: no timestamp, source ref, or generation ID can
    advance between the tested candidate and its final parentless state.
    """
    root = root.resolve()
    entries = _index_entries(root)
    manifest_entry = entries.get(policy.manifest_path)
    if manifest_entry is None:
        raise DashboardStateError("candidate index is missing dashboard state manifest")
    manifest = _normalize_canonical_manifest(
        _cat_blob(root, manifest_entry),
        policy,
    )
    if expected_code_sha is not None:
        expected = _full_sha(expected_code_sha, label="expected code SHA")
        if manifest["code_sha"] != expected:
            raise DashboardStateError("candidate manifest code_sha does not match expected")
    return prepare_manifest(
        root,
        policy,
        code_sha=manifest["code_sha"],
        generation_id=manifest["generation_id"],
        generated_at=manifest["generated_at"],
        source_refs=manifest["source_refs"],
    )


def validate_index(
    root: Path,
    policy: StatePolicy,
    *,
    expected_code_sha: str | None = None,
) -> dict[str, Any]:
    """Validate the exact index that will become a parentless state commit."""
    root = root.resolve()
    entries = _index_entries(root)
    _validate_entry_limits(entries, policy, label="candidate state index")
    manifest_entry = entries.get(policy.manifest_path)
    if manifest_entry is None:
        raise DashboardStateError("candidate index is missing dashboard state manifest")
    manifest = _normalize_canonical_manifest(
        _cat_blob(root, manifest_entry),
        policy,
    )
    _validate_full_content_summary(entries, manifest, policy)
    _validate_generated_manifest(root, entries, manifest, policy)
    if expected_code_sha is not None:
        expected = _full_sha(expected_code_sha, label="expected code SHA")
        if manifest["code_sha"] != expected:
            raise DashboardStateError("candidate manifest code_sha does not match expected")
    _assert_index_code_identity(root, entries, manifest["code_sha"], policy)
    return manifest


def create_parentless_commit(
    root: Path,
    policy: StatePolicy,
    *,
    expected_code_sha: str | None = None,
) -> ValidatedState:
    """Create, but do not publish, a parentless commit from the exact index."""
    root = root.resolve()
    manifest = validate_index(root, policy, expected_code_sha=expected_code_sha)
    _assert_generated_worktree_is_staged(root, policy)
    tree_sha = _full_sha(_git(root, "write-tree").decode("ascii").strip(), label="candidate tree")
    message = (
        f"state: {manifest['generation_id']}\n\n"
        f"Dashboard-Code-SHA: {manifest['code_sha']}\n"
        f"Dashboard-Tree-SHA: {tree_sha}\n"
    ).encode()
    state_sha = _full_sha(
        _git(root, "commit-tree", tree_sha, input_bytes=message).decode("ascii").strip(),
        label="state commit",
    )
    state = validate_state_ref(
        root,
        state_sha,
        policy,
        expected_code_sha=manifest["code_sha"],
    )
    if state.state_tree != tree_sha:
        raise DashboardStateError("created state commit does not contain candidate tree")
    return state


def _remote_ref_sha(root: Path, remote: str, branch: str) -> str | None:
    ref = f"refs/heads/{branch}"
    output = _git(root, "ls-remote", "--refs", remote, ref).decode("ascii")
    rows = [line.split() for line in output.splitlines() if line.strip()]
    if not rows:
        return None
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != ref:
        raise DashboardStateError(f"remote returned ambiguous state for {ref}")
    return _full_sha(rows[0][0], label=f"remote {ref}")


def _expected_slot(value: str, *, label: str) -> str | None:
    if value == "absent":
        return None
    return _full_sha(value, label=label)


def rotate_state_refs(
    root: Path,
    policy: StatePolicy,
    *,
    new_state_sha: str,
    expected_current_sha: str | None,
    expected_previous_sha: str | None,
    remote: str = "origin",
) -> dict[str, str]:
    """Atomically rotate current/previous refs using exact force-with-lease.

    Git's atomic push guarantees that a lease rejection or server-side failure
    updates neither ref. A successful push is verified with a fresh ls-remote;
    a later verification transport failure is reported but cannot roll back an
    already accepted remote transaction.
    """
    root = root.resolve()
    if (
        not isinstance(remote, str)
        or not remote
        or len(remote) > 500
        or remote.startswith("-")
        or any(char.isspace() or ord(char) < 32 for char in remote)
    ):
        raise DashboardStateError("remote must be a safe non-option name or URL")
    new_state_sha = _full_sha(new_state_sha, label="new state SHA")
    validate_state_ref(root, new_state_sha, policy)
    if expected_current_sha is not None:
        expected_current_sha = _full_sha(expected_current_sha, label="expected current SHA")
        validate_state_ref(root, expected_current_sha, policy)
    if expected_previous_sha is not None:
        expected_previous_sha = _full_sha(expected_previous_sha, label="expected previous SHA")
        # The previous slot is discarded by this transaction. Its observed
        # object identity is an exact lease precondition, not input to the new
        # two-slot state, so do not require (or read) its local object content.
        # Rollback remains safe: when the previous state is selected as
        # ``new_state_sha`` it is fully validated above before either ref moves.
    if expected_current_sha is None and expected_previous_sha is not None:
        raise DashboardStateError(
            "cannot bootstrap current state while a previous state is established"
        )
    if expected_current_sha is None and not policy.bootstrap_allowed:
        raise DashboardStateError("dashboard state bootstrap is disabled by policy")

    actual_current = _remote_ref_sha(root, remote, policy.branch)
    actual_previous = _remote_ref_sha(root, remote, policy.previous_branch)
    if actual_current != expected_current_sha:
        raise DashboardStateError(
            f"remote current state changed: expected {expected_current_sha}, "
            f"observed {actual_current}"
        )
    if actual_previous != expected_previous_sha:
        raise DashboardStateError(
            f"remote previous state changed: expected {expected_previous_sha}, "
            f"observed {actual_previous}"
        )

    current_ref = f"refs/heads/{policy.branch}"
    previous_ref = f"refs/heads/{policy.previous_branch}"
    current_lease = expected_current_sha or ""
    previous_lease = expected_previous_sha or ""
    args = [
        "push",
        "--atomic",
        f"--force-with-lease={current_ref}:{current_lease}",
        f"--force-with-lease={previous_ref}:{previous_lease}",
    ]
    # Bootstrap creates both slots at the same root. This makes "both absent"
    # one atomic precondition; otherwise an external writer could create the
    # previous ref between its read and a current-only push. Before generation
    # two, explicit rollback is simply a no-op.
    new_previous_sha = expected_current_sha or new_state_sha
    args.extend(
        [
            remote,
            f"{new_state_sha}:{current_ref}",
            f"{new_previous_sha}:{previous_ref}",
        ]
    )
    _git(root, *args)

    verified_current = _remote_ref_sha(root, remote, policy.branch)
    verified_previous = _remote_ref_sha(root, remote, policy.previous_branch)
    expected_new_previous = new_previous_sha
    if verified_current != new_state_sha or verified_previous != expected_new_previous:
        raise DashboardStateError(
            "remote state rotation succeeded but post-push verification disagreed"
        )
    return {
        "state_sha": new_state_sha,
        "previous_state_sha": expected_new_previous,
    }


def _load_repair_ancestry_attestation(
    path: Path,
    *,
    trusted_main_sha: str,
) -> dict[str, str]:
    """Load exact server-side ancestry proofs without requiring full Git history."""
    if path.is_symlink():
        raise DashboardStateError("repair ancestry attestation must not be a symlink")
    try:
        if not path.is_file() or path.stat().st_size > 16 * 1024:
            raise DashboardStateError(
                "repair ancestry attestation must be a regular file of at most 16 KiB"
            )
        payload = _decode_json(path.read_bytes(), label="repair ancestry attestation")
    except OSError as exc:
        raise DashboardStateError(f"repair ancestry attestation is unreadable: {exc}") from exc
    expected = {
        "schema_version",
        "provider",
        "trusted_main_sha",
        "proofs",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise DashboardStateError("repair ancestry attestation has an unexpected shape")
    if payload.get("schema_version") != 1:
        raise DashboardStateError("repair ancestry attestation schema_version is unsupported")
    if payload.get("provider") != "github_compare_api":
        raise DashboardStateError("repair ancestry attestation provider is unsupported")
    expected_main = _full_sha(trusted_main_sha, label="trusted main SHA")
    attested_main = _full_sha(payload.get("trusted_main_sha"), label="attested main SHA")
    if attested_main != expected_main:
        raise DashboardStateError("repair ancestry attestation targets a different main SHA")
    raw_proofs = payload.get("proofs")
    if not isinstance(raw_proofs, list) or len(raw_proofs) > 2:
        raise DashboardStateError("repair ancestry attestation proofs must be a bounded array")
    proofs: dict[str, str] = {}
    for raw_proof in raw_proofs:
        if not isinstance(raw_proof, dict) or set(raw_proof) != {
            "state_sha",
            "code_sha",
            "result",
        }:
            raise DashboardStateError("repair ancestry proof has an unexpected shape")
        state_sha = _full_sha(raw_proof.get("state_sha"), label="attested state SHA")
        code_sha = _full_sha(raw_proof.get("code_sha"), label="attested code SHA")
        if raw_proof.get("result") != "ancestor":
            raise DashboardStateError("repair ancestry proof did not establish ancestry")
        if state_sha in proofs:
            raise DashboardStateError("repair ancestry attestation repeats a state SHA")
        proofs[state_sha] = code_sha
    return proofs


def _validate_repair_candidate(
    root: Path,
    policy: StatePolicy,
    *,
    state_sha: str | None,
    proofs: Mapping[str, str],
    label: str,
) -> ValidatedState | None:
    if state_sha is None:
        return None
    state_sha = _full_sha(state_sha, label=f"expected {label} SHA")
    proof_code_sha = proofs.get(state_sha)
    if proof_code_sha is None:
        return None
    try:
        resolved = _resolve_commit(root, state_sha, label=f"{label} state object")
        if resolved != state_sha:
            return None
        resolved_code = _resolve_commit(root, proof_code_sha, label=f"{label} state code object")
        if resolved_code != proof_code_sha:
            return None
        return validate_state_ref_metadata(
            root,
            state_sha,
            policy,
            expected_code_sha=proof_code_sha,
        )
    except DashboardStateError:
        # A fetched object that fails the bounded metadata contract is a corrupt
        # slot, not authority to weaken validation of the surviving slot.
        return None


def repair_state_slots(
    root: Path,
    policy: StatePolicy,
    *,
    expected_current_sha: str | None,
    expected_previous_sha: str | None,
    trusted_main_sha: str,
    ancestry_attestation: Path,
    remote: str = "origin",
) -> dict[str, str]:
    """Reconcile two observed slots from independently validated state.

    Both valid slots are retained unchanged. Exactly one valid slot is copied
    to both refs in one exact-leased atomic push. Missing or corrupt slots never
    become bootstrap authority, and no mutation occurs when neither is valid.
    The workflow-created attestation binds GitHub Compare API ancestry results
    to each valid state, its declared code, and one exact trusted main SHA.
    """
    root = root.resolve()
    if (
        not isinstance(remote, str)
        or not remote
        or len(remote) > 500
        or remote.startswith("-")
        or any(char.isspace() or ord(char) < 32 for char in remote)
    ):
        raise DashboardStateError("remote must be a safe non-option name or URL")
    if expected_current_sha is not None:
        expected_current_sha = _full_sha(expected_current_sha, label="expected current SHA")
    if expected_previous_sha is not None:
        expected_previous_sha = _full_sha(expected_previous_sha, label="expected previous SHA")
    trusted_main_sha = _full_sha(trusted_main_sha, label="trusted main SHA")
    resolved_main = _resolve_commit(root, trusted_main_sha, label="trusted main SHA")
    if resolved_main != trusted_main_sha:
        raise DashboardStateError("trusted main SHA did not resolve exactly")
    proofs = _load_repair_ancestry_attestation(
        ancestry_attestation.resolve(),
        trusted_main_sha=trusted_main_sha,
    )
    expected_slot_shas = {
        sha for sha in (expected_current_sha, expected_previous_sha) if sha is not None
    }
    unexpected_proofs = sorted(set(proofs) - expected_slot_shas)
    if unexpected_proofs:
        raise DashboardStateError(
            f"repair ancestry attestation names an unobserved state: {unexpected_proofs}"
        )

    actual_current = _remote_ref_sha(root, remote, policy.branch)
    actual_previous = _remote_ref_sha(root, remote, policy.previous_branch)
    if actual_current != expected_current_sha:
        raise DashboardStateError(
            f"remote current state changed: expected {expected_current_sha}, "
            f"observed {actual_current}"
        )
    if actual_previous != expected_previous_sha:
        raise DashboardStateError(
            f"remote previous state changed: expected {expected_previous_sha}, "
            f"observed {actual_previous}"
        )

    current = _validate_repair_candidate(
        root,
        policy,
        state_sha=expected_current_sha,
        proofs=proofs,
        label="current",
    )
    previous = _validate_repair_candidate(
        root,
        policy,
        state_sha=expected_previous_sha,
        proofs=proofs,
        label="previous",
    )
    if current is not None and previous is not None:
        verified_current = _remote_ref_sha(root, remote, policy.branch)
        verified_previous = _remote_ref_sha(root, remote, policy.previous_branch)
        if verified_current != expected_current_sha or verified_previous != expected_previous_sha:
            raise DashboardStateError("remote state changed during no-op repair validation")
        return {
            "repair_action": "noop",
            "valid_slots": "both",
            "current_state_sha": current.state_sha,
            "previous_state_sha": previous.state_sha,
        }
    survivor = current or previous
    if survivor is None:
        raise DashboardStateError(
            "neither dashboard state slot is valid; refusing implicit bootstrap"
        )
    valid_slots = "current" if current is not None else "previous"

    current_ref = f"refs/heads/{policy.branch}"
    previous_ref = f"refs/heads/{policy.previous_branch}"
    current_lease = expected_current_sha or ""
    previous_lease = expected_previous_sha or ""
    _git(
        root,
        "push",
        "--atomic",
        f"--force-with-lease={current_ref}:{current_lease}",
        f"--force-with-lease={previous_ref}:{previous_lease}",
        remote,
        f"{survivor.state_sha}:{current_ref}",
        f"{survivor.state_sha}:{previous_ref}",
    )

    verified_current = _remote_ref_sha(root, remote, policy.branch)
    verified_previous = _remote_ref_sha(root, remote, policy.previous_branch)
    if verified_current != survivor.state_sha or verified_previous != survivor.state_sha:
        raise DashboardStateError(
            "remote state repair succeeded but post-push verification disagreed"
        )
    return {
        "repair_action": "repaired",
        "valid_slots": valid_slots,
        "current_state_sha": survivor.state_sha,
        "previous_state_sha": survivor.state_sha,
    }


def write_public_marker(
    output: Path,
    state: ValidatedState,
    *,
    public_projection: Mapping[str, Any],
    publication_status: Mapping[str, Any] | None = None,
    expected_state_tree: str | None = None,
    expected_code_sha: str | None = None,
    expected_generated_at: str | None = None,
) -> dict[str, Any]:
    """Atomically write the small public proof linking Pages to state."""
    if expected_state_tree is not None:
        expected_tree = _full_sha(expected_state_tree, label="expected state tree")
        if state.state_tree != expected_tree:
            raise DashboardStateError("state tree does not match expected tree")
    if expected_code_sha is not None:
        expected_code = _full_sha(expected_code_sha, label="expected code SHA")
        if state.code_sha != expected_code:
            raise DashboardStateError("state code_sha does not match expected code SHA")
    if expected_generated_at is not None:
        expected_time = _canonical_timestamp(expected_generated_at, label="expected generated_at")
        if state.generated_at != expected_time:
            raise DashboardStateError("state generated_at does not match expected generated_at")
    if publication_status is not None:
        status_time = _canonical_timestamp(
            publication_status.get("generated_at"),
            label="publication status generated_at",
        )
        if state.generated_at != status_time:
            raise DashboardStateError(
                "state generated_at does not match publication status generated_at"
            )
    try:
        normalized_projection = normalize_attestation(public_projection)
    except PublicProjectionError as exc:
        raise DashboardStateError(f"public projection attestation is invalid: {exc}") from exc
    marker = {
        "schema_version": PUBLIC_MARKER_SCHEMA_VERSION,
        "generation_id": state.generation_id,
        "generated_at": state.generated_at,
        "state_sha": state.state_sha,
        "state_tree": state.state_tree,
        "code_sha": state.code_sha,
        "public_projection": normalized_projection,
    }
    encoded = (json.dumps(marker, indent=2, sort_keys=True) + "\n").encode()
    if len(encoded) > 4096:
        raise DashboardStateError("public generation marker is unexpectedly large")
    _atomic_write(output.resolve(), encoded)
    return marker


def validate_public_marker(
    value: object,
    *,
    expected_state_sha: str | None = None,
) -> dict[str, Any]:
    expected = {
        "schema_version",
        "generation_id",
        "generated_at",
        "state_sha",
        "state_tree",
        "code_sha",
        "public_projection",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise DashboardStateError("public generation marker has an unexpected shape")
    if value.get("schema_version") != PUBLIC_MARKER_SCHEMA_VERSION:
        raise DashboardStateError("public generation marker schema_version is unsupported")
    marker = {
        "schema_version": PUBLIC_MARKER_SCHEMA_VERSION,
        "generation_id": _generation_id(value.get("generation_id")),
        "generated_at": _canonical_timestamp(
            value.get("generated_at"), label="marker generated_at"
        ),
        "state_sha": _full_sha(value.get("state_sha"), label="marker state_sha"),
        "state_tree": _full_sha(value.get("state_tree"), label="marker state_tree"),
        "code_sha": _full_sha(value.get("code_sha"), label="marker code_sha"),
    }
    try:
        marker["public_projection"] = normalize_attestation(value.get("public_projection"))
    except PublicProjectionError as exc:
        raise DashboardStateError(f"public marker projection is invalid: {exc}") from exc
    if expected_state_sha is not None and marker["state_sha"] != _full_sha(
        expected_state_sha, label="expected state SHA"
    ):
        raise DashboardStateError("public marker state_sha does not match expected")
    return marker


def _append_outputs(path: Path | None, values: Mapping[str, object]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    for line in lines:
        if "\n" in line or "\r" in line:
            raise DashboardStateError("output values must be single-line")
    text = "\n".join(lines) + "\n"
    if path is None:
        sys.stdout.write(text)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()


def _state_outputs(state: ValidatedState) -> dict[str, str]:
    return {
        "state_sha": state.state_sha,
        "tree_sha": state.state_tree,
        "code_sha": state.code_sha,
        "generation_id": state.generation_id,
        "generated_at": state.generated_at,
    }


def _metadata_state_outputs(state: ValidatedState) -> dict[str, object]:
    generated_files = state.manifest["generated_files"]
    return {
        **_state_outputs(state),
        "validation_mode": "metadata_oid",
        "manifest_schema_version": state.manifest["schema_version"],
        "generated_file_count": len(generated_files),
        "generated_total_bytes": sum(row["bytes"] for row in generated_files.values()),
    }


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage bounded parentless dashboard state snapshots"
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser


def _add_output_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--github-output",
        type=Path,
        help="Append key=value outputs to this GitHub Actions output file",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = _common_parser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-ref")
    validate.add_argument("--ref", required=True)
    validate.add_argument("--expected-code-sha")
    _add_output_argument(validate)

    validate_metadata = subparsers.add_parser("validate-ref-metadata")
    validate_metadata.add_argument("--ref", required=True)
    validate_metadata.add_argument("--expected-code-sha", required=True)
    _add_output_argument(validate_metadata)

    materialize = subparsers.add_parser("materialize")
    materialize.add_argument("--ref", required=True)
    materialize.add_argument("--expected-code-sha")
    _add_output_argument(materialize)

    refresh = subparsers.add_parser("refresh-manifest")
    refresh.add_argument("--expected-code-sha")
    _add_output_argument(refresh)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--code-sha", required=True)
    prepare.add_argument("--generation-id", required=True)
    prepare.add_argument("--generated-at", required=True)
    prepare.add_argument("--source-ref", action="append", default=[])
    _add_output_argument(prepare)

    create = subparsers.add_parser("create-commit")
    create.add_argument("--code-sha")
    _add_output_argument(create)

    rotate = subparsers.add_parser("rotate")
    rotate.add_argument("--new-state", required=True)
    rotate.add_argument("--current-sha", required=True, help="Full SHA or 'absent'")
    rotate.add_argument("--previous-sha", required=True, help="Full SHA or 'absent'")
    rotate.add_argument("--remote", default="origin")
    _add_output_argument(rotate)

    repair = subparsers.add_parser("repair-slots")
    repair.add_argument("--current-sha", required=True, help="Full SHA or 'absent'")
    repair.add_argument("--previous-sha", required=True, help="Full SHA or 'absent'")
    repair.add_argument("--trusted-main-sha", required=True)
    repair.add_argument("--ancestry-attestation", type=Path, required=True)
    repair.add_argument("--remote", default="origin")
    _add_output_argument(repair)

    marker = subparsers.add_parser("write-public-marker")
    marker.add_argument("--state-sha", required=True)
    marker.add_argument("--state-tree")
    marker.add_argument("--code-sha")
    marker.add_argument("--generated-at")
    marker.add_argument(
        "--metadata-only",
        action="store_true",
        help=(
            "Validate only bounded state metadata and Git object identities; "
            "requires --code-sha"
        ),
    )
    marker.add_argument("--public-attestation", type=Path, required=True)
    marker.add_argument("--publication-status", type=Path)
    marker.add_argument("--output", type=Path, required=True)
    _add_output_argument(marker)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = args.root.resolve()
        policy = load_policy(args.config.resolve())
        if args.command == "validate-ref":
            state = validate_state_ref(
                root,
                args.ref,
                policy,
                expected_code_sha=args.expected_code_sha,
            )
            _append_outputs(args.github_output, _state_outputs(state))
        elif args.command == "validate-ref-metadata":
            state = validate_state_ref_metadata(
                root,
                args.ref,
                policy,
                expected_code_sha=args.expected_code_sha,
            )
            _append_outputs(args.github_output, _metadata_state_outputs(state))
        elif args.command == "materialize":
            state = materialize_generated_roots(
                root,
                args.ref,
                policy,
                expected_code_sha=args.expected_code_sha,
            )
            _append_outputs(args.github_output, _state_outputs(state))
        elif args.command == "refresh-manifest":
            manifest = refresh_manifest(
                root,
                policy,
                expected_code_sha=args.expected_code_sha,
            )
            generated_files = manifest["generated_files"]
            _append_outputs(
                args.github_output,
                {
                    "manifest_path": policy.manifest_path,
                    "generation_id": manifest["generation_id"],
                    "generated_at": manifest["generated_at"],
                    "code_sha": manifest["code_sha"],
                    "generated_file_count": len(generated_files),
                    "generated_total_bytes": sum(row["bytes"] for row in generated_files.values()),
                },
            )
        elif args.command == "prepare":
            manifest = prepare_manifest(
                root,
                policy,
                code_sha=args.code_sha,
                generation_id=args.generation_id,
                generated_at=args.generated_at,
                source_refs=_parse_source_ref_args(args.source_ref),
            )
            generated_files = manifest["generated_files"]
            _append_outputs(
                args.github_output,
                {
                    "manifest_path": policy.manifest_path,
                    "generation_id": manifest["generation_id"],
                    "generated_at": manifest["generated_at"],
                    "code_sha": manifest["code_sha"],
                    "generated_file_count": len(generated_files),
                    "generated_total_bytes": sum(row["bytes"] for row in generated_files.values()),
                },
            )
        elif args.command == "create-commit":
            state = create_parentless_commit(root, policy, expected_code_sha=args.code_sha)
            _append_outputs(args.github_output, _state_outputs(state))
        elif args.command == "rotate":
            outputs = rotate_state_refs(
                root,
                policy,
                new_state_sha=args.new_state,
                expected_current_sha=_expected_slot(args.current_sha, label="current SHA"),
                expected_previous_sha=_expected_slot(args.previous_sha, label="previous SHA"),
                remote=args.remote,
            )
            _append_outputs(args.github_output, outputs)
        elif args.command == "repair-slots":
            outputs = repair_state_slots(
                root,
                policy,
                expected_current_sha=_expected_slot(args.current_sha, label="current SHA"),
                expected_previous_sha=_expected_slot(args.previous_sha, label="previous SHA"),
                trusted_main_sha=args.trusted_main_sha,
                ancestry_attestation=args.ancestry_attestation,
                remote=args.remote,
            )
            _append_outputs(args.github_output, outputs)
        elif args.command == "write-public-marker":
            if args.metadata_only:
                if args.code_sha is None:
                    raise DashboardStateError(
                        "write-public-marker --metadata-only requires --code-sha"
                    )
                state = validate_state_ref_metadata(
                    root,
                    args.state_sha,
                    policy,
                    expected_code_sha=args.code_sha,
                )
            else:
                state = validate_state_ref(
                    root,
                    args.state_sha,
                    policy,
                    expected_code_sha=args.code_sha,
                )
            try:
                projection = load_attestation(args.public_attestation.absolute())
            except PublicProjectionError as exc:
                raise DashboardStateError(
                    f"public projection attestation is invalid: {exc}"
                ) from exc
            if not args.metadata_only and args.publication_status is None:
                raise DashboardStateError(
                    "write-public-marker requires --publication-status outside metadata-only mode"
                )
            publication_status = (
                _load_publication_status(args.publication_status.absolute())
                if args.publication_status is not None
                else None
            )
            marker = write_public_marker(
                args.output,
                state,
                public_projection=projection,
                publication_status=publication_status,
                expected_state_tree=args.state_tree,
                expected_code_sha=args.code_sha,
                expected_generated_at=args.generated_at,
            )
            _append_outputs(args.github_output, marker)
        else:  # pragma: no cover - argparse guarantees the command set.
            raise DashboardStateError(f"unknown command {args.command!r}")
    except DashboardStateError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
