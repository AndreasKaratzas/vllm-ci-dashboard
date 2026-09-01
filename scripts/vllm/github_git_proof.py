#!/usr/bin/env python3
"""Bounded GitHub proofs for metadata-first partial Git fetches.

Git's partial-clone filters do not bound an object explicitly named as a fetch
want.  This module therefore proves an exact commit and recursive tree through
GitHub's size-bearing Git database API before a workflow fetches any blob OID.
It also provides one stream-capped, strictly-shaped ancestry comparison.
"""

# cspell:ignore redef

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
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

if __package__:
    from .publication_limits import (
        PREVIEW_MAX_BYTES,
        PREVIEW_MAX_COUNT,
        PREVIEW_MAX_FILES,
        PUBLICATION_MAX_BLOB_BYTES,
        PUBLICATION_MAX_FILES,
        PUBLICATION_MAX_TREE_BYTES,
        SINGLE_PREVIEW_MAX_BYTES,
        SINGLE_PREVIEW_MAX_FILES,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from publication_limits import (  # type: ignore[no-redef]
        PREVIEW_MAX_BYTES,
        PREVIEW_MAX_COUNT,
        PREVIEW_MAX_FILES,
        PUBLICATION_MAX_BLOB_BYTES,
        PUBLICATION_MAX_FILES,
        PUBLICATION_MAX_TREE_BYTES,
        SINGLE_PREVIEW_MAX_BYTES,
        SINGLE_PREVIEW_MAX_FILES,
    )


API_VERSION = "2022-11-28"
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SAFE_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,299}")
MAX_COMMIT_RESPONSE_BYTES = 1024 * 1024
MAX_TREE_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_COMPARE_RESPONSE_BYTES = 16 * 1024 * 1024
STATE_MANIFEST_PATH = "data/vllm/ci/dashboard_state.json"
STATE_ATTESTATION_PATH = "data/vllm/ci/public_projection_attestation.json"
PAGES_MANIFEST_PATH = "publication_manifest.json"
PAGES_MARKER_PATH = "publication_generation.json"
PAGES_STATUS_PATH = "data/vllm/ci/publication_status.json"
PAGES_PREVIEW_PREFIX = "pr-preview/"
PAGES_PREVIEW_RE = re.compile(r"pr-[1-9][0-9]*")
MAX_PAGES_BLOB_BYTES = PUBLICATION_MAX_BLOB_BYTES
MAX_PAGES_TREE_BYTES = PUBLICATION_MAX_TREE_BYTES
MAX_PAGES_FILES = PUBLICATION_MAX_FILES
MAX_PREVIEW_BYTES = PREVIEW_MAX_BYTES
MAX_PREVIEW_FILES = PREVIEW_MAX_FILES
MAX_PREVIEW_COUNT = PREVIEW_MAX_COUNT
MAX_SINGLE_PREVIEW_BYTES = SINGLE_PREVIEW_MAX_BYTES
MAX_SINGLE_PREVIEW_FILES = SINGLE_PREVIEW_MAX_FILES


class ProofError(RuntimeError):
    """Base class for a rejected remote proof."""


class InvalidProof(ProofError):
    """The remote object definitively violates the bounded profile."""


class AmbiguousProof(ProofError):
    """Transport or response ambiguity prevents a trust decision."""


class NotFoundProof(ProofError):
    """GitHub definitively reported that an exact object is absent."""


@dataclass(frozen=True)
class Profile:
    name: str
    require_parentless: bool
    max_blob_bytes: int
    max_tree_bytes: int
    max_files: int
    excluded_prefixes: tuple[str, ...]
    required: Mapping[str, int]


PROFILES = {
    "dashboard-state": Profile(
        name="dashboard-state",
        require_parentless=True,
        max_blob_bytes=85 * 1024 * 1024,
        max_tree_bytes=256 * 1024 * 1024,
        max_files=10_000,
        excluded_prefixes=(),
        required={STATE_MANIFEST_PATH: 8 * 1024 * 1024, STATE_ATTESTATION_PATH: 4096},
    ),
    "dashboard-code": Profile(
        name="dashboard-code",
        require_parentless=False,
        max_blob_bytes=85 * 1024 * 1024,
        max_tree_bytes=256 * 1024 * 1024,
        max_files=10_000,
        excluded_prefixes=(),
        required={},
    ),
    "pages": Profile(
        name="pages",
        require_parentless=False,
        max_blob_bytes=MAX_PAGES_BLOB_BYTES,
        max_tree_bytes=MAX_PAGES_TREE_BYTES,
        max_files=MAX_PAGES_FILES,
        excluded_prefixes=(),
        required={
            PAGES_MANIFEST_PATH: 8 * 1024 * 1024,
            PAGES_MARKER_PATH: 4096,
            PAGES_STATUS_PATH: 1024 * 1024,
        },
    ),
    "pages-orphan": Profile(
        name="pages-orphan",
        require_parentless=True,
        max_blob_bytes=MAX_PAGES_BLOB_BYTES,
        max_tree_bytes=MAX_PAGES_TREE_BYTES,
        max_files=MAX_PAGES_FILES,
        excluded_prefixes=(),
        required={
            PAGES_MANIFEST_PATH: 8 * 1024 * 1024,
            PAGES_MARKER_PATH: 4096,
            PAGES_STATUS_PATH: 1024 * 1024,
        },
    ),
}


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _full_sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or FULL_SHA_RE.fullmatch(value) is None:
        raise AmbiguousProof(f"{label} is not one full lowercase SHA-1")
    return value


def _repository(value: object) -> str:
    if not isinstance(value, str) or REPOSITORY_RE.fullmatch(value) is None:
        raise AmbiguousProof("repository must use the canonical owner/name shape")
    return value


def _safe_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 500:
        raise InvalidProof("tree contains an empty or unbounded path")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise InvalidProof(f"tree path contains a control character: {value!r}")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise InvalidProof("tree path is not valid UTF-8") from exc
    if len(encoded) > 1000:
        raise InvalidProof("tree path exceeds its encoded byte limit")
    if "\\" in value or value.startswith("/") or value.endswith("/"):
        raise InvalidProof(f"tree path is not canonical POSIX: {value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidProof(f"tree path is not canonical POSIX: {value!r}")
    if PurePosixPath(value).as_posix() != value:
        raise InvalidProof(f"tree path is not normalized: {value!r}")
    return value


def _decode_json(raw: bytes, *, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AmbiguousProof(f"{label} is not strict UTF-8 JSON: {exc}") from exc


def _request_json(
    repository: str,
    endpoint: str,
    *,
    token: str,
    max_bytes: int,
    label: str,
) -> Any:
    repository = _repository(repository)
    if not token or len(token) > 500 or any(ord(char) < 32 for char in token):
        raise AmbiguousProof("GitHub API token is missing or malformed")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/{endpoint}",
        headers={
            "Accept": "application/vnd.github+json",
            "Accept-Encoding": "identity",
            "Authorization": f"Bearer {token}",
            "User-Agent": "vllm-ci-dashboard-proof/1",
            "X-GitHub-Api-Version": API_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise AmbiguousProof(f"{label} returned HTTP {response.status}")
            raw = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NotFoundProof(f"{label} was not found") from exc
        raise AmbiguousProof(f"{label} returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AmbiguousProof(f"{label} transport failed: {exc}") from exc
    if len(raw) > max_bytes:
        raise AmbiguousProof(f"{label} exceeded its {max_bytes}-byte response cap")
    return _decode_json(raw, label=label)


def _is_excluded(path: str, profile: Profile) -> bool:
    return any(path.startswith(prefix) for prefix in profile.excluded_prefixes)


def validate_commit_payload(
    payload: object,
    *,
    expected_commit_sha: str,
    profile: Profile,
) -> str:
    expected_commit_sha = _full_sha(expected_commit_sha, label="expected commit SHA")
    if not isinstance(payload, dict) or payload.get("sha") != expected_commit_sha:
        raise AmbiguousProof("GitHub commit response does not bind the requested SHA")
    tree = payload.get("tree")
    parents = payload.get("parents")
    if not isinstance(tree, dict) or not isinstance(parents, list):
        raise AmbiguousProof("GitHub commit response has an unexpected shape")
    tree_sha = _full_sha(tree.get("sha"), label="commit tree SHA")
    for parent in parents:
        if not isinstance(parent, dict):
            raise AmbiguousProof("GitHub commit parent has an unexpected shape")
        _full_sha(parent.get("sha"), label="commit parent SHA")
    if profile.require_parentless and parents:
        raise InvalidProof("dashboard state commit must be parentless")
    return tree_sha


def validate_tree_payload(
    payload: object,
    *,
    expected_tree_sha: str,
    profile: Profile,
) -> dict[str, Any]:
    expected_tree_sha = _full_sha(expected_tree_sha, label="expected tree SHA")
    if not isinstance(payload, dict) or payload.get("sha") != expected_tree_sha:
        raise AmbiguousProof("GitHub tree response does not bind the requested SHA")
    if payload.get("truncated") is not False:
        if payload.get("truncated") is True:
            raise AmbiguousProof("GitHub recursive tree is truncated")
        raise AmbiguousProof("GitHub tree truncation flag is malformed")
    rows = payload.get("tree")
    if not isinstance(rows, list):
        raise AmbiguousProof("GitHub tree response has an unexpected shape")
    seen: set[str] = set()
    blobs: dict[str, dict[str, Any]] = {}
    file_count = 0
    total_bytes = 0
    max_blob_bytes = 0
    for row in rows:
        if not isinstance(row, dict):
            raise AmbiguousProof("GitHub tree entry has an unexpected shape")
        path = _safe_path(row.get("path"))
        if path in seen:
            raise AmbiguousProof(f"GitHub tree repeats path {path!r}")
        seen.add(path)
        mode = row.get("mode")
        kind = row.get("type")
        object_id = _full_sha(row.get("sha"), label=f"tree object for {path}")
        if kind == "tree":
            if mode != "040000" or row.get("size") is not None:
                raise InvalidProof(f"tree directory has unsafe metadata at {path}")
            continue
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise InvalidProof(f"tree contains an unsafe object at {path}")
        size = row.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise AmbiguousProof(f"GitHub blob size is malformed at {path}")
        blobs[path] = {"bytes": size, "mode": mode, "oid": object_id}
        if size > profile.max_blob_bytes:
            raise InvalidProof(f"tree blob exceeds the profile limit at {path}")
        if _is_excluded(path, profile):
            continue
        file_count += 1
        total_bytes += size
        max_blob_bytes = max(max_blob_bytes, size)
        if file_count > profile.max_files:
            raise InvalidProof("tree exceeds the profile file-count limit")
        if total_bytes > profile.max_tree_bytes:
            raise InvalidProof("tree exceeds the profile aggregate-byte limit")

    required: dict[str, dict[str, Any]] = {}
    for path, limit in profile.required.items():
        descriptor = blobs.get(path)
        if descriptor is None or descriptor["mode"] != "100644":
            raise InvalidProof(f"tree is missing safe required metadata {path}")
        if descriptor["bytes"] > limit:
            raise InvalidProof(f"required metadata exceeds its byte limit at {path}")
        required[path] = descriptor
    result = {
        "blobs": dict(sorted(blobs.items())),
        "file_count": file_count,
        "max_blob_bytes": max_blob_bytes,
        "required_blobs": dict(sorted(required.items())),
        "total_bytes": total_bytes,
    }
    if profile.name in {"pages", "pages-orphan"}:
        preview_rows = [
            [path, row["mode"], row["oid"], row["bytes"]]
            for path, row in sorted(blobs.items())
            if path.startswith(PAGES_PREVIEW_PREFIX)
        ]
        result.update(
            {
                "preview_bytes": sum(row[3] for row in preview_rows),
                "preview_digest": hashlib.sha256(
                    json.dumps(
                        preview_rows, separators=(",", ":"), ensure_ascii=False
                    ).encode("utf-8")
                ).hexdigest(),
                "preview_files": len(preview_rows),
            }
        )
    return result


def prove_commit_tree(
    repository: str,
    commit_sha: str,
    profile: Profile,
    *,
    token: str,
) -> dict[str, Any]:
    commit_sha = _full_sha(commit_sha, label="commit SHA")
    commit = _request_json(
        repository,
        f"git/commits/{commit_sha}",
        token=token,
        max_bytes=MAX_COMMIT_RESPONSE_BYTES,
        label="GitHub commit proof",
    )
    tree_sha = validate_commit_payload(
        commit,
        expected_commit_sha=commit_sha,
        profile=profile,
    )
    tree = _request_json(
        repository,
        f"git/trees/{tree_sha}?recursive=1",
        token=token,
        max_bytes=MAX_TREE_RESPONSE_BYTES,
        label="GitHub recursive tree proof",
    )
    summary = validate_tree_payload(tree, expected_tree_sha=tree_sha, profile=profile)
    return {
        "schema_version": 1,
        "profile": profile.name,
        "repository": _repository(repository),
        "commit_sha": commit_sha,
        "tree_sha": tree_sha,
        **summary,
    }


def compare_ancestor(repository: str, base: str, head: str, *, token: str) -> bool:
    base = _full_sha(base, label="compare base")
    head = _full_sha(head, label="compare head")
    if base == head:
        return True
    payload = _request_json(
        repository,
        f"compare/{base}...{head}",
        token=token,
        max_bytes=MAX_COMPARE_RESPONSE_BYTES,
        label="GitHub ancestry comparison",
    )
    if not isinstance(payload, dict):
        raise AmbiguousProof("GitHub comparison has an unexpected shape")
    status = payload.get("status")
    ahead_by = payload.get("ahead_by")
    behind_by = payload.get("behind_by")
    base_commit = payload.get("base_commit")
    merge_base = payload.get("merge_base_commit")
    commits = payload.get("commits")
    expected_url = f"https://api.github.com/repos/{repository}/compare/{base}...{head}"
    shaped = (
        payload.get("url") == expected_url
        and isinstance(base_commit, dict)
        and base_commit.get("sha") == base
        and isinstance(merge_base, dict)
        and FULL_SHA_RE.fullmatch(str(merge_base.get("sha", ""))) is not None
        and isinstance(commits, list)
        and type(ahead_by) is int
        and type(behind_by) is int
        and status in {"ahead", "behind", "diverged", "identical"}
    )
    if not shaped:
        raise AmbiguousProof("GitHub comparison has an unexpected shape")
    if len(commits) == ahead_by and ahead_by > 0:
        final_commit = commits[-1]
        if not isinstance(final_commit, dict) or final_commit.get("sha") != head:
            raise AmbiguousProof("complete GitHub comparison does not end at requested head")
    return bool(
        merge_base["sha"] == base
        and behind_by == 0
        and (
            (status == "ahead" and ahead_by > 0)
            or (status == "identical" and ahead_by == 0)
        )
    )


def _safe_git_name(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SAFE_REF_RE.fullmatch(value) is None
        or value.startswith("-")
        or ".." in value
        or "@{" in value
        or "//" in value
        or value.endswith((".", "/", ".lock"))
    ):
        raise AmbiguousProof(f"{label} is not a safe Git name")
    return value


def _git(root: Path, *args: str, no_lazy_fetch: bool = False) -> bytes:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if no_lazy_fetch:
        env["GIT_NO_LAZY_FETCH"] = "1"
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            env=env,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = exc.stderr.decode("utf-8", errors="replace").strip()[-1000:]
        raise AmbiguousProof(
            f"git {' '.join(args[:3])} failed" + (f": {detail}" if detail else "")
        ) from exc
    return result.stdout


def resolve_remote_branch(root: Path, remote: str, branch: str) -> str:
    remote = _safe_git_name(remote, label="remote")
    branch = _safe_git_name(branch, label="branch")
    target = f"refs/heads/{branch}"
    raw = _git(root, "ls-remote", "--exit-code", "--refs", remote, target)
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise AmbiguousProof("remote branch lookup returned non-ASCII data") from exc
    if len(lines) != 1:
        raise AmbiguousProof("remote branch lookup was not exact")
    fields = lines[0].split("\t")
    if len(fields) != 2 or fields[1] != target:
        raise AmbiguousProof("remote branch lookup had an unexpected shape")
    return _full_sha(fields[0], label="remote branch SHA")


def hydrate_proven_commit(
    root: Path,
    remote: str,
    proof: Mapping[str, Any],
) -> None:
    root = root.absolute()
    remote = _safe_git_name(remote, label="remote")
    commit_sha = _full_sha(proof.get("commit_sha"), label="proof commit SHA")
    tree_sha = _full_sha(proof.get("tree_sha"), label="proof tree SHA")
    _git(
        root,
        "fetch",
        "--no-tags",
        "--depth=1",
        "--filter=blob:none",
        remote,
        commit_sha,
    )
    fetched_commit = _git(
        root, "rev-parse", "--verify", f"{commit_sha}^{{commit}}", no_lazy_fetch=True
    ).decode("ascii").strip()
    fetched_tree = _git(
        root, "rev-parse", "--verify", f"{commit_sha}^{{tree}}", no_lazy_fetch=True
    ).decode("ascii").strip()
    if fetched_commit != commit_sha or fetched_tree != tree_sha:
        raise AmbiguousProof("fetched commit/tree disagrees with its GitHub proof")

    required = proof.get("required_blobs")
    if not isinstance(required, dict):
        raise AmbiguousProof("proof required_blobs is malformed")
    for path, descriptor in sorted(required.items()):
        if not isinstance(path, str) or not isinstance(descriptor, dict):
            raise AmbiguousProof("proof required blob descriptor is malformed")
        object_id = _full_sha(descriptor.get("oid"), label=f"required blob {path} OID")
        expected_size = descriptor.get("bytes")
        if (
            isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
            or expected_size < 0
        ):
            raise AmbiguousProof(f"required blob {path} size is malformed")
        try:
            _git(root, "cat-file", "-e", object_id, no_lazy_fetch=True)
        except AmbiguousProof:
            _git(root, "fetch", "--no-tags", "--filter=blob:none", remote, object_id)
        object_type = _git(
            root, "cat-file", "-t", object_id, no_lazy_fetch=True
        ).decode("ascii").strip()
        raw_size = _git(
            root, "cat-file", "-s", object_id, no_lazy_fetch=True
        ).decode("ascii").strip()
        if object_type != "blob" or not raw_size.isdigit() or int(raw_size) != expected_size:
            raise AmbiguousProof(f"required blob {path} disagrees with its size proof")


def _validated_proof_blobs(proof: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw_blobs = proof.get("blobs")
    if not isinstance(raw_blobs, dict) or len(raw_blobs) > MAX_PAGES_FILES:
        raise AmbiguousProof("proof blobs are malformed or unbounded")
    blobs: dict[str, dict[str, Any]] = {}
    total = 0
    for raw_path, raw_descriptor in raw_blobs.items():
        path = _safe_path(raw_path)
        if path in blobs or not isinstance(raw_descriptor, dict) or set(raw_descriptor) != {
            "bytes",
            "mode",
            "oid",
        }:
            raise AmbiguousProof(f"proof blob descriptor is malformed at {path}")
        size = raw_descriptor.get("bytes")
        mode = raw_descriptor.get("mode")
        object_id = _full_sha(raw_descriptor.get("oid"), label=f"proof blob {path} OID")
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or not 0 <= size <= MAX_PAGES_BLOB_BYTES
            or mode not in {"100644", "100755"}
        ):
            raise AmbiguousProof(f"proof blob descriptor is unsafe at {path}")
        total += size
        blobs[path] = {"bytes": size, "mode": mode, "oid": object_id}
    if total != proof.get("total_bytes") or len(blobs) != proof.get("file_count"):
        raise AmbiguousProof("proof blob inventory disagrees with its summary")
    if total > MAX_PAGES_TREE_BYTES:
        raise AmbiguousProof("proof blob inventory exceeds the Pages limit")
    return dict(sorted(blobs.items()))


def _hydrate_proven_blob(
    root: Path,
    remote: str,
    *,
    path: str,
    descriptor: Mapping[str, Any],
) -> bytes:
    object_id = _full_sha(descriptor.get("oid"), label=f"proof blob {path} OID")
    expected_size = descriptor.get("bytes")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int):
        raise AmbiguousProof(f"proof blob {path} size is malformed")
    try:
        _git(root, "cat-file", "-e", object_id, no_lazy_fetch=True)
    except AmbiguousProof:
        _git(root, "fetch", "--no-tags", "--filter=blob:none", remote, object_id)
    object_type = _git(root, "cat-file", "-t", object_id, no_lazy_fetch=True).decode(
        "ascii"
    ).strip()
    raw_size = _git(root, "cat-file", "-s", object_id, no_lazy_fetch=True).decode(
        "ascii"
    ).strip()
    if object_type != "blob" or not raw_size.isdigit() or int(raw_size) != expected_size:
        raise AmbiguousProof(f"proof blob {path} disagrees with its size proof")
    payload = _git(root, "cat-file", "blob", object_id, no_lazy_fetch=True)
    if len(payload) != expected_size:
        raise AmbiguousProof(f"proof blob {path} changed size while materializing")
    return payload


def materialize_proven_tree(
    root: Path,
    remote: str,
    proof: Mapping[str, Any],
    destination: Path,
    *,
    prefix: str | None = None,
) -> None:
    """Materialize only server-size-proven regular blobs into a disposable tree."""
    root = root.absolute()
    remote = _safe_git_name(remote, label="remote")
    if proof.get("profile") not in {"pages", "pages-orphan"}:
        raise AmbiguousProof("only a Pages proof may be materialized")
    blobs = _validated_proof_blobs(proof)
    destination = destination.absolute()
    destination.mkdir(parents=True, exist_ok=True)
    if prefix is not None:
        prefix = _safe_path(prefix.rstrip("/")) + "/"
        prefix_root = destination / prefix.rstrip("/")
        if prefix_root.exists() or prefix_root.is_symlink():
            raise AmbiguousProof("materialized prefix destination must not already exist")
    elif any(destination.iterdir()):
        raise AmbiguousProof("materialized tree destination must be empty")

    selected = {
        path: descriptor
        for path, descriptor in blobs.items()
        if prefix is None or path.startswith(prefix)
    }
    for path, descriptor in selected.items():
        target = destination.joinpath(*PurePosixPath(path).parts)
        try:
            target.relative_to(destination)
        except ValueError as exc:
            raise AmbiguousProof(f"materialized path escapes destination: {path}") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise AmbiguousProof(f"materialized path already exists: {path}")
        payload = _hydrate_proven_blob(
            root,
            remote,
            path=path,
            descriptor=descriptor,
        )
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o755 if descriptor["mode"] == "100755" else 0o644)
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _local_page_files(root: Path) -> dict[str, int]:
    root = root.absolute()
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise InvalidProof("Pages directory is unreadable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise InvalidProof("Pages directory must be a real directory")
    files: dict[str, int] = {}
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in sorted(directory_names):
            child = directory_path / name
            child_metadata = child.lstat()
            if stat.S_ISLNK(child_metadata.st_mode) or not stat.S_ISDIR(
                child_metadata.st_mode
            ):
                raise InvalidProof(f"Pages path is not a real directory: {child}")
        for name in sorted(file_names):
            child = directory_path / name
            child_metadata = child.lstat()
            relative = child.relative_to(root).as_posix()
            _safe_path(relative)
            if stat.S_ISLNK(child_metadata.st_mode) or not stat.S_ISREG(
                child_metadata.st_mode
            ):
                raise InvalidProof(f"Pages path is not a regular file: {relative}")
            if child_metadata.st_size > MAX_PAGES_BLOB_BYTES:
                raise InvalidProof(f"Pages blob exceeds the file limit: {relative}")
            files[relative] = child_metadata.st_size
    return files


def _local_preview_digest(root: Path, files: Mapping[str, int]) -> str:
    rows: list[list[object]] = []
    for path, expected_size in sorted(files.items()):
        if not path.startswith(PAGES_PREVIEW_PREFIX):
            continue
        source = root.absolute().joinpath(*PurePosixPath(path).parts)
        metadata = source.lstat()
        digest = hashlib.sha1(f"blob {expected_size}\0".encode("ascii"))
        consumed = 0
        with source.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                consumed += len(chunk)
        closed = source.lstat()
        if (
            consumed != expected_size
            or closed.st_size != metadata.st_size
            or closed.st_mtime_ns != metadata.st_mtime_ns
        ):
            raise AmbiguousProof(f"preview changed while hashing: {path}")
        mode = "100755" if metadata.st_mode & 0o111 else "100644"
        rows.append([path, mode, digest.hexdigest(), consumed])
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def bound_pages_directory(root: Path, *, protected_preview: str | None = None) -> dict[str, int]:
    """Prune whole old preview cohorts and enforce the final Pages envelope."""
    protected = None
    if protected_preview is not None:
        if PAGES_PREVIEW_RE.fullmatch(protected_preview) is None:
            raise InvalidProof("protected preview must have the pr-N shape")
        protected = protected_preview
    files = _local_page_files(root)
    canonical: dict[str, int] = {}
    groups: dict[str, dict[str, int]] = {}
    invalid_preview_roots: set[str] = set()
    for path, size in files.items():
        if not path.startswith(PAGES_PREVIEW_PREFIX):
            canonical[path] = size
            continue
        parts = path.split("/")
        if len(parts) < 3 or PAGES_PREVIEW_RE.fullmatch(parts[1]) is None:
            invalid_preview_roots.add(parts[1] if len(parts) > 1 else "")
            continue
        groups.setdefault(parts[1], {})[path] = size
    preview_root = root.absolute() / PAGES_PREVIEW_PREFIX.rstrip("/")
    for name in sorted(invalid_preview_roots):
        target = preview_root / name
        if target.is_symlink():
            raise InvalidProof(f"preview path is a symlink: {name}")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
        else:
            raise AmbiguousProof(f"preview path changed while pruning: {name}")

    canonical_bytes = sum(canonical.values())
    canonical_files = len(canonical)
    if canonical_bytes > MAX_PAGES_TREE_BYTES or canonical_files > MAX_PAGES_FILES:
        raise InvalidProof("canonical Pages tree alone exceeds the Pages envelope")

    valid_groups: dict[str, dict[str, int]] = {}
    for name, rows in groups.items():
        size = sum(rows.values())
        if size > MAX_SINGLE_PREVIEW_BYTES or len(rows) > MAX_SINGLE_PREVIEW_FILES:
            if name == protected:
                raise InvalidProof(f"protected preview {name} exceeds its cohort limit")
            continue
        valid_groups[name] = rows

    def preview_number(name: str) -> int:
        return int(name.removeprefix("pr-"))

    ordered = sorted(
        valid_groups,
        key=lambda name: (name == protected, preview_number(name)),
        reverse=True,
    )
    kept: list[str] = []
    preview_bytes = 0
    preview_files = 0
    total_bytes = canonical_bytes
    total_files = canonical_files
    for name in ordered:
        rows = valid_groups[name]
        size = sum(rows.values())
        fits = (
            len(kept) + 1 <= MAX_PREVIEW_COUNT
            and preview_bytes + size <= MAX_PREVIEW_BYTES
            and preview_files + len(rows) <= MAX_PREVIEW_FILES
            and total_bytes + size <= MAX_PAGES_TREE_BYTES
            and total_files + len(rows) <= MAX_PAGES_FILES
        )
        if not fits:
            if name == protected:
                raise InvalidProof("protected preview does not fit the final Pages envelope")
            continue
        kept.append(name)
        preview_bytes += size
        preview_files += len(rows)
        total_bytes += size
        total_files += len(rows)

    for name in sorted(set(groups) - set(kept)):
        target = preview_root / name
        if target.is_symlink() or not target.is_dir():
            raise InvalidProof(f"preview cohort is not a real directory: {name}")
        shutil.rmtree(target)
    remaining = _local_page_files(root)
    if len(remaining) != total_files or sum(remaining.values()) != total_bytes:
        raise AmbiguousProof("Pages tree changed while preview bounds were applied")
    return {
        "file_count": total_files,
        "preview_bytes": preview_bytes,
        "preview_count": len(kept),
        "preview_files": preview_files,
        "preview_digest": _local_preview_digest(root, remaining),
        "total_bytes": total_bytes,
    }


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n"


def _write_proof(path: Path, proof: Mapping[str, Any]) -> None:
    path = path.absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(proof), encoding="utf-8")


def _append_outputs(path: Path | None, proof: Mapping[str, Any]) -> None:
    if path is None:
        return
    required = proof["required_blobs"]
    aliases = {
        STATE_MANIFEST_PATH: "state_manifest",
        STATE_ATTESTATION_PATH: "state_attestation",
        PAGES_MANIFEST_PATH: "pages_manifest",
        PAGES_MARKER_PATH: "pages_marker",
        PAGES_STATUS_PATH: "pages_status",
    }
    rows = [
        f"commit_sha={proof['commit_sha']}",
        f"tree_sha={proof['tree_sha']}",
        f"file_count={proof['file_count']}",
        f"total_bytes={proof['total_bytes']}",
    ]
    if "preview_digest" in proof:
        rows.extend(
            (
                f"pages_preview_bytes={proof['preview_bytes']}",
                f"pages_preview_digest={proof['preview_digest']}",
                f"pages_preview_files={proof['preview_files']}",
            )
        )
    for metadata_path, descriptor in required.items():
        alias = aliases[metadata_path]
        rows.extend(
            (f"{alias}_oid={descriptor['oid']}", f"{alias}_bytes={descriptor['bytes']}")
        )
    with path.absolute().open("a", encoding="utf-8") as handle:
        handle.write("\n".join(rows) + "\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prove = subparsers.add_parser("prove")
    prove.add_argument("--repository", required=True)
    prove.add_argument("--commit-sha", required=True)
    prove.add_argument("--profile", choices=sorted(PROFILES), required=True)
    prove.add_argument("--output", type=Path, required=True)
    prove.add_argument("--github-output", type=Path)
    compare = subparsers.add_parser("compare-ancestor")
    compare.add_argument("--repository", required=True)
    compare.add_argument("--base", required=True)
    compare.add_argument("--head", required=True)
    hydrate = subparsers.add_parser("hydrate-ref")
    hydrate.add_argument("--repository", required=True)
    hydrate.add_argument("--branch", required=True)
    hydrate.add_argument("--profile", choices=sorted(PROFILES), required=True)
    hydrate.add_argument("--root", type=Path, default=Path.cwd())
    hydrate.add_argument("--remote", default="origin")
    hydrate.add_argument("--local-ref")
    hydrate.add_argument("--output", type=Path, required=True)
    hydrate.add_argument("--github-output", type=Path)
    hydrate.add_argument("--materialize-root", type=Path)
    hydrate.add_argument("--materialize-prefix")
    bound_pages = subparsers.add_parser("bound-pages-directory")
    bound_pages.add_argument("--root", type=Path, required=True)
    bound_pages.add_argument("--protect-preview")
    bound_pages.add_argument("--github-output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    token = os.environ.get("GH_TOKEN", "")
    try:
        if args.command == "prove":
            proof = prove_commit_tree(
                args.repository,
                args.commit_sha,
                PROFILES[args.profile],
                token=token,
            )
            _write_proof(args.output, proof)
            _append_outputs(args.github_output, proof)
            print(
                f"Proved {args.profile} {proof['commit_sha']} tree {proof['tree_sha']}: "
                f"{proof['file_count']} files, {proof['total_bytes']} bytes"
            )
            return 0
        if args.command == "hydrate-ref":
            commit_sha = resolve_remote_branch(args.root, args.remote, args.branch)
            proof = prove_commit_tree(
                args.repository,
                commit_sha,
                PROFILES[args.profile],
                token=token,
            )
            hydrate_proven_commit(
                args.root,
                args.remote,
                proof,
            )
            if args.materialize_root is not None:
                materialize_proven_tree(
                    args.root,
                    args.remote,
                    proof,
                    args.materialize_root,
                    prefix=args.materialize_prefix,
                )
            elif args.materialize_prefix is not None:
                raise AmbiguousProof("--materialize-prefix requires --materialize-root")
            if resolve_remote_branch(args.root, args.remote, args.branch) != commit_sha:
                raise AmbiguousProof("remote branch changed during bounded hydration")
            if args.local_ref is not None:
                local_ref = _safe_git_name(args.local_ref, label="local ref")
                if not local_ref.startswith("refs/remotes/"):
                    raise AmbiguousProof("local ref must remain under refs/remotes/")
                _git(args.root.absolute(), "update-ref", local_ref, commit_sha)
            _write_proof(args.output, proof)
            _append_outputs(args.github_output, proof)
            print(
                f"Hydrated bounded {args.profile} metadata for {commit_sha} "
                f"({proof['total_bytes']} proven bytes)"
            )
            return 0
        if args.command == "bound-pages-directory":
            summary = bound_pages_directory(
                args.root,
                protected_preview=args.protect_preview,
            )
            print(
                "Bounded Pages tree: "
                f"{summary['file_count']} files, {summary['total_bytes']} bytes; "
                f"{summary['preview_count']} previews, {summary['preview_bytes']} bytes"
            )
            if args.github_output is not None:
                with args.github_output.absolute().open("a", encoding="utf-8") as handle:
                    handle.write(
                        f"pages_preview_bytes={summary['preview_bytes']}\n"
                        f"pages_preview_digest={summary['preview_digest']}\n"
                        f"pages_preview_files={summary['preview_files']}\n"
                        f"file_count={summary['file_count']}\n"
                        f"total_bytes={summary['total_bytes']}\n"
                    )
            return 0
        return 0 if compare_ancestor(
            args.repository, args.base, args.head, token=token
        ) else 3
    except NotFoundProof as exc:
        # A 404 can reflect authorization or object replication as well as
        # absence. It is never authority to invalidate a slot or mutate refs.
        print(f"::error::{exc}", file=sys.stderr)
        return 90
    except InvalidProof as exc:
        print(f"::warning::{exc}", file=sys.stderr)
        return 3 if args.command == "compare-ancestor" else 2
    except AmbiguousProof as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 90


if __name__ == "__main__":
    raise SystemExit(main())
