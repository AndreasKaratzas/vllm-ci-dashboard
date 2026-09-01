#!/usr/bin/env python3
"""Create and verify the exact bounded GitHub Pages projection.

The public manifest records every canonical root-site regular file and binds
both its SHA-256 digest and its Git blob object ID.  A verifier with a partial
Git clone can therefore compare an entire deployed tree without fetching each
large blob: the state-bound manifest supplies the SHA-256 contract and the Git
tree supplies the exact content-addressed object IDs.

``pr-preview/`` is deliberately outside the canonical root-site projection.
The privileged preview workflow owns that subtree and serializes its mutations
under the same Pages concurrency lock.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


MANIFEST_SCHEMA_VERSION = 1
ATTESTATION_SCHEMA_VERSION = 1
MANIFEST_NAME = "publication_manifest.json"
MARKER_NAME = "publication_generation.json"
ATTESTATION_PATH = "data/vllm/ci/public_projection_attestation.json"
EXCLUDED_PREFIXES = ("pr-preview/",)
MAX_BLOB_BYTES = 85 * 1024 * 1024
MAX_TREE_BYTES = 384 * 1024 * 1024
MAX_FILES = 10_000
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_MARKER_BYTES = 4096
MAX_ATTESTATION_BYTES = 4096
SHA256_RE = re.compile(r"[0-9a-f]{64}")
GIT_OID_RE = re.compile(r"[0-9a-f]{40}")
SAFE_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,299}")


class PublicProjectionError(RuntimeError):
    """The public projection failed a bounded fail-closed invariant."""


def _json_object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _decode_json(raw: bytes, *, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_json_object_no_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PublicProjectionError(f"{label} is not strict UTF-8 JSON: {exc}") from exc


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _read_bounded(path: Path, *, limit: int, label: str) -> bytes:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PublicProjectionError(f"{label} is not a regular file")
        if metadata.st_size > limit:
            raise PublicProjectionError(f"{label} exceeds {limit} bytes")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
    except PublicProjectionError:
        raise
    except OSError as exc:
        raise PublicProjectionError(f"{label} is unreadable: {type(exc).__name__}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or opened.st_size != metadata.st_size
        ):
            raise PublicProjectionError(f"{label} changed while opening")
        chunks: list[bytes] = []
        consumed = 0
        while consumed <= limit:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - consumed))
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
        closed = os.fstat(descriptor)
        if consumed > limit:
            raise PublicProjectionError(f"{label} exceeds {limit} bytes")
        if (
            consumed != metadata.st_size
            or closed.st_size != metadata.st_size
            or closed.st_mtime_ns != opened.st_mtime_ns
        ):
            raise PublicProjectionError(f"{label} changed while reading")
        return b"".join(chunks)
    except OSError as exc:
        raise PublicProjectionError(f"{label} is unreadable: {type(exc).__name__}") from exc
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        temporary.replace(path)
    except OSError as exc:
        raise PublicProjectionError(
            f"could not atomically write {path}: {type(exc).__name__}"
        ) from exc
    finally:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)


def _safe_relative_path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 500:
        raise PublicProjectionError(f"{label} must be a nonempty bounded path")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PublicProjectionError(f"{label} contains a control character")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise PublicProjectionError(f"{label} is not valid UTF-8") from exc
    if len(encoded) > 1000:
        raise PublicProjectionError(f"{label} exceeds its encoded byte limit")
    if "\\" in value or value.startswith("/") or value.endswith("/"):
        raise PublicProjectionError(f"{label} is not a canonical POSIX path: {value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise PublicProjectionError(f"{label} is not a canonical POSIX path: {value!r}")
    if PurePosixPath(value).as_posix() != value:
        raise PublicProjectionError(f"{label} is not normalized: {value!r}")
    return value


def _is_excluded(path: str) -> bool:
    return path in {MANIFEST_NAME, MARKER_NAME} or any(
        path.startswith(prefix) for prefix in EXCLUDED_PREFIXES
    )


def _file_mode(raw_mode: int) -> str:
    return "100755" if raw_mode & 0o111 else "100644"


def _hash_regular_file(path: Path, metadata: os.stat_result) -> tuple[str, str, int]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PublicProjectionError(
            f"could not safely open projection file {path}: {type(exc).__name__}"
        ) from exc
    sha256 = hashlib.sha256()
    # GitHub currently serves this repository from a SHA-1 object database.
    # ``usedforsecurity=False`` keeps this content-addressing use available on
    # FIPS hosts; SHA-256 remains the security digest in the public contract.
    git_oid = hashlib.sha1(usedforsecurity=False)
    git_oid.update(f"blob {metadata.st_size}\0".encode("ascii"))
    consumed = 0
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or opened.st_size != metadata.st_size
        ):
            raise PublicProjectionError(f"projection file changed while opening: {path}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            consumed += len(chunk)
            sha256.update(chunk)
            git_oid.update(chunk)
        closed = os.fstat(descriptor)
        if (
            consumed != metadata.st_size
            or closed.st_size != metadata.st_size
            or closed.st_mtime_ns != opened.st_mtime_ns
        ):
            raise PublicProjectionError(f"projection file changed while hashing: {path}")
    finally:
        os.close(descriptor)
    return sha256.hexdigest(), git_oid.hexdigest(), consumed


def _scan_local_tree(site_root: Path) -> dict[str, dict[str, Any]]:
    root = site_root.absolute()
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise PublicProjectionError(
            f"site root is unreadable: {type(exc).__name__}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PublicProjectionError("site root must be a real directory")

    files: dict[str, dict[str, Any]] = {}
    total_bytes = 0

    def visit(directory: Path, relative_directory: str = "") -> None:
        nonlocal total_bytes
        try:
            children = sorted(os.scandir(directory), key=lambda row: row.name)
        except OSError as exc:
            raise PublicProjectionError(
                f"could not scan site directory {directory}: {type(exc).__name__}"
            ) from exc
        for child in children:
            relative = f"{relative_directory}/{child.name}" if relative_directory else child.name
            relative = _safe_relative_path(relative, label="site path")
            try:
                child_metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise PublicProjectionError(
                    f"could not inspect site path {relative}: {type(exc).__name__}"
                ) from exc
            if stat.S_ISLNK(child_metadata.st_mode):
                raise PublicProjectionError(f"site path is a symlink: {relative}")
            if stat.S_ISDIR(child_metadata.st_mode):
                if relative + "/" in EXCLUDED_PREFIXES:
                    continue
                visit(Path(child.path), relative)
                continue
            if not stat.S_ISREG(child_metadata.st_mode):
                raise PublicProjectionError(f"site path is not a regular file: {relative}")
            if _is_excluded(relative):
                continue
            if child_metadata.st_size > MAX_BLOB_BYTES:
                raise PublicProjectionError(
                    f"public blob {relative} is {child_metadata.st_size} bytes; "
                    f"limit is {MAX_BLOB_BYTES}"
                )
            if len(files) + 1 > MAX_FILES:
                raise PublicProjectionError(f"public projection exceeds {MAX_FILES} files")
            if total_bytes + child_metadata.st_size > MAX_TREE_BYTES:
                raise PublicProjectionError(
                    f"public projection exceeds {MAX_TREE_BYTES} total bytes"
                )
            digest, object_id, consumed = _hash_regular_file(
                Path(child.path), child_metadata
            )
            files[relative] = {
                "bytes": consumed,
                "mode": _file_mode(child_metadata.st_mode),
                "sha256": digest,
                "git_oid": object_id,
            }
            total_bytes += consumed

    visit(root)
    return dict(sorted(files.items()))


def _manifest_from_files(files: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "hash_algorithm": "sha256",
        "git_object_format": "sha1",
        "excluded_prefixes": list(EXCLUDED_PREFIXES),
        "limits": {
            "max_blob_bytes": MAX_BLOB_BYTES,
            "max_tree_bytes": MAX_TREE_BYTES,
            "max_files": MAX_FILES,
        },
        "file_count": len(files),
        "total_bytes": sum(row["bytes"] for row in files.values()),
        "files": dict(sorted(files.items())),
    }


def create_manifest(site_root: Path, manifest_path: Path) -> dict[str, Any]:
    """Write the canonical self-excluding manifest for ``site_root``."""
    manifest_path = manifest_path.absolute()
    expected_path = site_root.absolute() / MANIFEST_NAME
    if manifest_path != expected_path:
        raise PublicProjectionError(
            f"public manifest must be written to {expected_path}"
        )
    manifest = _manifest_from_files(_scan_local_tree(site_root))
    encoded = _canonical_json(manifest)
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise PublicProjectionError(
            f"public projection manifest exceeds {MAX_MANIFEST_BYTES} bytes"
        )
    # The two self-excluded metadata files are still part of the published
    # root. Reserve the marker's full bounded size before it exists.
    if manifest["file_count"] + 2 > MAX_FILES:
        raise PublicProjectionError(
            f"public projection plus metadata exceeds {MAX_FILES} files"
        )
    if manifest["total_bytes"] + len(encoded) + MAX_MARKER_BYTES > MAX_TREE_BYTES:
        raise PublicProjectionError(
            f"public projection plus metadata exceeds {MAX_TREE_BYTES} total bytes"
        )
    _atomic_write(manifest_path, encoded)
    return manifest


def _normalize_descriptor(value: object, *, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "bytes",
        "mode",
        "sha256",
        "git_oid",
    }:
        raise PublicProjectionError(f"projection descriptor for {path} is malformed")
    size = value.get("bytes")
    mode = value.get("mode")
    digest = value.get("sha256")
    git_oid = value.get("git_oid")
    if type(size) is not int or not 0 <= size <= MAX_BLOB_BYTES:
        raise PublicProjectionError(f"projection descriptor for {path} has invalid bytes")
    if mode not in {"100644", "100755"}:
        raise PublicProjectionError(f"projection descriptor for {path} has invalid mode")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise PublicProjectionError(f"projection descriptor for {path} has invalid SHA-256")
    if not isinstance(git_oid, str) or GIT_OID_RE.fullmatch(git_oid) is None:
        raise PublicProjectionError(f"projection descriptor for {path} has invalid Git OID")
    return {"bytes": size, "mode": mode, "sha256": digest, "git_oid": git_oid}


def normalize_manifest(value: object) -> dict[str, Any]:
    expected = {
        "schema_version",
        "hash_algorithm",
        "git_object_format",
        "excluded_prefixes",
        "limits",
        "file_count",
        "total_bytes",
        "files",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise PublicProjectionError("public projection manifest has an unexpected shape")
    if value.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise PublicProjectionError("public projection manifest schema_version is unsupported")
    if value.get("hash_algorithm") != "sha256" or value.get("git_object_format") != "sha1":
        raise PublicProjectionError("public projection manifest hash contract is unsupported")
    if value.get("excluded_prefixes") != list(EXCLUDED_PREFIXES):
        raise PublicProjectionError("public projection exclusions disagree with policy")
    expected_limits = {
        "max_blob_bytes": MAX_BLOB_BYTES,
        "max_tree_bytes": MAX_TREE_BYTES,
        "max_files": MAX_FILES,
    }
    if value.get("limits") != expected_limits:
        raise PublicProjectionError("public projection limits disagree with policy")
    raw_files = value.get("files")
    if not isinstance(raw_files, dict) or len(raw_files) > MAX_FILES:
        raise PublicProjectionError("public projection files must be a bounded object")
    files: dict[str, dict[str, Any]] = {}
    for raw_path, raw_descriptor in raw_files.items():
        path = _safe_relative_path(raw_path, label="projection path")
        if _is_excluded(path):
            raise PublicProjectionError(f"projection declares excluded path {path!r}")
        files[path] = _normalize_descriptor(raw_descriptor, path=path)
    file_count = value.get("file_count")
    total_bytes = value.get("total_bytes")
    calculated_bytes = sum(row["bytes"] for row in files.values())
    if type(file_count) is not int or file_count != len(files):
        raise PublicProjectionError("public projection file_count is inconsistent")
    if type(total_bytes) is not int or total_bytes != calculated_bytes:
        raise PublicProjectionError("public projection total_bytes is inconsistent")
    if total_bytes > MAX_TREE_BYTES:
        raise PublicProjectionError("public projection exceeds its tree byte limit")
    return _manifest_from_files(dict(sorted(files.items())))


def load_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_bounded(path, limit=MAX_MANIFEST_BYTES, label="public projection manifest")
    manifest = normalize_manifest(_decode_json(raw, label="public projection manifest"))
    if raw != _canonical_json(manifest):
        raise PublicProjectionError("public projection manifest is not canonical JSON")
    return manifest, raw


def _attestation_for_manifest(manifest: Mapping[str, Any], raw: bytes) -> dict[str, Any]:
    return {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "manifest_path": MANIFEST_NAME,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
    }


def normalize_attestation(value: object) -> dict[str, Any]:
    expected = {
        "schema_version",
        "manifest_path",
        "manifest_sha256",
        "file_count",
        "total_bytes",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise PublicProjectionError("public projection attestation has an unexpected shape")
    if value.get("schema_version") != ATTESTATION_SCHEMA_VERSION:
        raise PublicProjectionError("public projection attestation schema_version is unsupported")
    if value.get("manifest_path") != MANIFEST_NAME:
        raise PublicProjectionError("public projection attestation path is unsupported")
    digest = value.get("manifest_sha256")
    file_count = value.get("file_count")
    total_bytes = value.get("total_bytes")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise PublicProjectionError("public projection attestation has an invalid digest")
    if type(file_count) is not int or not 0 <= file_count <= MAX_FILES:
        raise PublicProjectionError("public projection attestation has an invalid file_count")
    if type(total_bytes) is not int or not 0 <= total_bytes <= MAX_TREE_BYTES:
        raise PublicProjectionError("public projection attestation has invalid total_bytes")
    return {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "manifest_path": MANIFEST_NAME,
        "manifest_sha256": digest,
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def load_attestation(path: Path) -> dict[str, Any]:
    raw = _read_bounded(path, limit=MAX_ATTESTATION_BYTES, label="projection attestation")
    attestation = normalize_attestation(_decode_json(raw, label="projection attestation"))
    if raw != _canonical_json(attestation):
        raise PublicProjectionError("public projection attestation is not canonical JSON")
    return attestation


def write_attestation(manifest_path: Path, output: Path) -> dict[str, Any]:
    manifest, raw = load_manifest(manifest_path)
    attestation = _attestation_for_manifest(manifest, raw)
    _atomic_write(output.absolute(), _canonical_json(attestation))
    return attestation


def _assert_attestation_matches(
    manifest: Mapping[str, Any], raw: bytes, attestation: Mapping[str, Any]
) -> None:
    expected = _attestation_for_manifest(manifest, raw)
    if dict(attestation) != expected:
        raise PublicProjectionError(
            "public projection manifest disagrees with state-bound attestation"
        )


def _load_marker(path: Path, attestation: Mapping[str, Any]) -> bytes:
    raw = _read_bounded(path, limit=MAX_MARKER_BYTES, label="publication marker")
    marker = _decode_json(raw, label="publication marker")
    if not isinstance(marker, dict) or marker.get("public_projection") != dict(attestation):
        raise PublicProjectionError(
            "publication marker does not bind the exact public projection"
        )
    return raw


def verify_local_projection(
    site_root: Path,
    manifest_path: Path,
    attestation_path: Path,
    marker_path: Path,
) -> dict[str, Any]:
    manifest, raw = load_manifest(manifest_path)
    attestation = load_attestation(attestation_path)
    _assert_attestation_matches(manifest, raw, attestation)
    observed = _manifest_from_files(_scan_local_tree(site_root))
    if observed != manifest:
        observed_paths = set(observed["files"])
        declared_paths = set(manifest["files"])
        changed = sorted(
            path
            for path in observed_paths & declared_paths
            if observed["files"][path] != manifest["files"][path]
        )
        raise PublicProjectionError(
            "local public projection differs from its manifest "
            f"(undeclared={sorted(observed_paths - declared_paths)[:10]}, "
            f"missing={sorted(declared_paths - observed_paths)[:10]}, "
            f"changed={changed[:10]})"
        )
    marker_raw = _load_marker(marker_path, attestation)
    if manifest["file_count"] + 2 > MAX_FILES:
        raise PublicProjectionError("local public projection exceeds its file limit")
    if manifest["total_bytes"] + len(raw) + len(marker_raw) > MAX_TREE_BYTES:
        raise PublicProjectionError("local public projection exceeds its tree byte limit")
    return attestation


def _safe_git_ref(value: str) -> str:
    if (
        SAFE_REF_RE.fullmatch(value) is None
        or value.startswith("-")
        or value.endswith((".", "/", ".lock"))
        or ".." in value
        or "@{" in value
        or "//" in value
    ):
        raise PublicProjectionError("Git tree ref is not a safe canonical revision")
    return value


def _git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    env = os.environ.copy()
    # Git projection verification is metadata-only. The workflow must
    # explicitly hydrate its server-size-proven manifest and marker first.
    env["GIT_NO_LAZY_FETCH"] = "1"
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        input=input_bytes,
        env=env,
    )
    if result.returncode != 0:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()[-1000:]
        raise PublicProjectionError(
            f"git {' '.join(args[:2])} failed: {diagnostic or 'no diagnostic'}"
        )
    return result.stdout


def _git_tree_entries(root: Path, git_ref: str) -> dict[str, tuple[str, str, str]]:
    raw = _git(root, "ls-tree", "-r", "-z", "--full-tree", git_ref)
    entries: dict[str, tuple[str, str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.decode("ascii", errors="strict").split()
        if not separator or len(fields) != 3:
            raise PublicProjectionError("Git tree returned a malformed entry")
        try:
            path = raw_path.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise PublicProjectionError("Git tree contains a non-UTF-8 path") from exc
        path = _safe_relative_path(path, label="Git tree path")
        if path in entries:
            raise PublicProjectionError(f"Git tree repeats path {path!r}")
        mode, kind, object_id = fields
        entries[path] = (mode, kind, object_id)
    return entries


def _git_blob(root: Path, object_id: str, *, limit: int, label: str) -> bytes:
    if GIT_OID_RE.fullmatch(object_id) is None:
        raise PublicProjectionError(f"{label} has an invalid Git object ID")
    header = _git(
        root,
        "cat-file",
        "--batch-check=%(objecttype) %(objectsize)",
        input_bytes=(object_id + "\n").encode("ascii"),
    ).decode("ascii", errors="replace").strip().split()
    if len(header) != 2 or header[0] != "blob":
        raise PublicProjectionError(f"{label} is not a Git blob")
    try:
        size = int(header[1])
    except ValueError as exc:
        raise PublicProjectionError(f"{label} has an invalid Git object size") from exc
    if not 0 <= size <= limit:
        raise PublicProjectionError(f"{label} exceeds its {limit}-byte limit")
    raw = _git(root, "cat-file", "blob", object_id)
    if len(raw) != size:
        raise PublicProjectionError(f"{label} size changed while reading")
    return raw


def _git_oid_for_bytes(raw: bytes) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(f"blob {len(raw)}\0".encode("ascii"))
    digest.update(raw)
    return digest.hexdigest()


def verify_git_projection(
    repo_root: Path,
    git_ref: str,
    attestation_path: Path,
    *,
    expected_marker_path: Path | None = None,
) -> dict[str, Any]:
    """Verify an exact Pages tree while reading only its small metadata blobs."""
    root = repo_root.absolute()
    git_ref = _safe_git_ref(git_ref)
    attestation = load_attestation(attestation_path)
    entries = _git_tree_entries(root, git_ref)

    manifest_entry = entries.get(MANIFEST_NAME)
    marker_entry = entries.get(MARKER_NAME)
    for label, entry in (("manifest", manifest_entry), ("marker", marker_entry)):
        if entry is None or entry[0] != "100644" or entry[1] != "blob":
            raise PublicProjectionError(f"deployed publication {label} is missing or unsafe")
    assert manifest_entry is not None and marker_entry is not None
    manifest_raw = _git_blob(
        root,
        manifest_entry[2],
        limit=MAX_MANIFEST_BYTES,
        label="deployed projection manifest",
    )
    manifest = normalize_manifest(
        _decode_json(manifest_raw, label="deployed projection manifest")
    )
    if manifest_raw != _canonical_json(manifest):
        raise PublicProjectionError("deployed projection manifest is not canonical JSON")
    _assert_attestation_matches(manifest, manifest_raw, attestation)

    marker_raw = _git_blob(
        root,
        marker_entry[2],
        limit=MAX_MARKER_BYTES,
        label="deployed publication marker",
    )
    marker = _decode_json(marker_raw, label="deployed publication marker")
    if not isinstance(marker, dict) or marker.get("public_projection") != attestation:
        raise PublicProjectionError(
            "deployed publication marker does not bind the state projection"
        )
    if expected_marker_path is not None:
        expected_marker = _read_bounded(
            expected_marker_path,
            limit=MAX_MARKER_BYTES,
            label="expected publication marker",
        )
        if marker_raw != expected_marker:
            raise PublicProjectionError(
                "deployed publication marker differs from the expected state marker"
            )
    if manifest["file_count"] + 2 > MAX_FILES:
        raise PublicProjectionError("deployed public projection exceeds its file limit")
    if manifest["total_bytes"] + len(manifest_raw) + len(marker_raw) > MAX_TREE_BYTES:
        raise PublicProjectionError("deployed public projection exceeds its tree byte limit")

    actual_files: dict[str, tuple[str, str, str]] = {}
    for path, entry in entries.items():
        if _is_excluded(path):
            continue
        mode, kind, object_id = entry
        if mode not in {"100644", "100755"} or kind != "blob":
            raise PublicProjectionError(f"deployed projection path is unsafe: {path}")
        if GIT_OID_RE.fullmatch(object_id) is None:
            raise PublicProjectionError(f"deployed projection path has invalid OID: {path}")
        actual_files[path] = entry

    declared_files = manifest["files"]
    if set(actual_files) != set(declared_files):
        raise PublicProjectionError(
            "deployed public file set differs from its exact manifest "
            f"(undeclared={sorted(set(actual_files) - set(declared_files))[:10]}, "
            f"missing={sorted(set(declared_files) - set(actual_files))[:10]})"
        )
    changed: list[str] = []
    for path, descriptor in declared_files.items():
        mode, _kind, object_id = actual_files[path]
        if mode != descriptor["mode"] or object_id != descriptor["git_oid"]:
            changed.append(path)
    if changed:
        raise PublicProjectionError(
            f"deployed public blobs differ from their SHA-256 manifest: {changed[:10]}"
        )
    return attestation


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage exact public projection manifests")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--site-root", type=Path, required=True)
    create.add_argument("--manifest", type=Path, required=True)
    create.add_argument(
        "--attestation",
        type=Path,
        help="Also write the private state attestation (canonical collection only)",
    )

    local = subparsers.add_parser("verify-local")
    local.add_argument("--site-root", type=Path, required=True)
    local.add_argument("--manifest", type=Path, required=True)
    local.add_argument("--attestation", type=Path, required=True)
    local.add_argument("--marker", type=Path, required=True)

    git = subparsers.add_parser("verify-git")
    git.add_argument("--repo-root", type=Path, default=Path.cwd())
    git.add_argument("--git-ref", required=True)
    git.add_argument("--attestation", type=Path, required=True)
    git.add_argument("--expected-marker", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "create":
            manifest = create_manifest(args.site_root, args.manifest)
            if args.attestation is not None:
                attestation = write_attestation(args.manifest, args.attestation)
            else:
                loaded, raw = load_manifest(args.manifest)
                attestation = _attestation_for_manifest(loaded, raw)
        elif args.command == "verify-local":
            attestation = verify_local_projection(
                args.site_root,
                args.manifest,
                args.attestation,
                args.marker,
            )
            manifest = None
        elif args.command == "verify-git":
            attestation = verify_git_projection(
                args.repo_root,
                args.git_ref,
                args.attestation,
                expected_marker_path=args.expected_marker,
            )
            manifest = None
        else:  # pragma: no cover - argparse guarantees the command set.
            raise PublicProjectionError(f"unknown command {args.command!r}")
        count = manifest["file_count"] if manifest is not None else attestation["file_count"]
        total = manifest["total_bytes"] if manifest is not None else attestation["total_bytes"]
        print(
            f"Verified public projection: {count} files, {total} bytes, "
            f"manifest {attestation['manifest_sha256']}"
        )
    except PublicProjectionError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
