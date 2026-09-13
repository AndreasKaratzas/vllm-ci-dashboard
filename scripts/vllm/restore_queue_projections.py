#!/usr/bin/env python3
"""Mirror derived queue projections to one exact Git baseline.

Targeted queue reconciliation temporarily installs four files from the durable
``queue-data`` branch so they can be audited as one generation.  The
publication selector, however, owns only the two queue source files.  This
helper puts the two derived projections back into the exact state represented
by the already-validated publication baseline before selection.  A projection
that is absent from that baseline is removed; this is the normal case for the
gitignored ``operations_v2`` section.

Both baseline entries and both current destinations are preflighted before any
mutation.  Applying the plan is rollback-capable so an I/O failure cannot
leave a mixed-baseline pair behind.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
# Keep every individual projection below the dashboard state's 85 MiB hard
# blob bound (and comfortably below GitHub's 100 MB rejection threshold).
MAX_PROJECTION_BYTES = 85 * 1024 * 1024
PROJECTION_PATHS = (
    "data/vllm/ci/operations_v2/queue.json",
    "data/vllm/ci/queue_history_chart.json",
)


class ProjectionRestoreError(RuntimeError):
    """The exact baseline could not be mirrored safely."""


@dataclass(frozen=True)
class ProjectionPlan:
    relative_path: str
    content: bytes | None


@dataclass(frozen=True)
class OriginalFile:
    content: bytes | None
    mode: int | None


def _git(root: Path, *args: str) -> bytes:
    env = os.environ.copy()
    env["GIT_NO_LAZY_FETCH"] = "1"
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()[-1000:]
        command = " ".join(("git", *args[:3]))
        raise ProjectionRestoreError(
            f"{command} failed" + (f": {detail}" if detail else "")
        ) from exc
    return result.stdout


def _resolve_exact_commit(root: Path, baseline_ref: str) -> str:
    if FULL_SHA_RE.fullmatch(baseline_ref) is None:
        raise ProjectionRestoreError("baseline ref must be one full lowercase commit SHA")
    resolved = _git(root, "rev-parse", "--verify", f"{baseline_ref}^{{commit}}")
    try:
        resolved_sha = resolved.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ProjectionRestoreError("baseline ref resolved to non-ASCII output") from exc
    if resolved_sha != baseline_ref:
        raise ProjectionRestoreError("baseline ref did not resolve to its exact commit SHA")
    return resolved_sha


def _git_blob_oid(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _baseline_projection(root: Path, commit_sha: str, relative: str) -> ProjectionPlan:
    raw = _git(root, "ls-tree", "--full-tree", "-z", commit_sha, "--", relative)
    records = [record for record in raw.split(b"\0") if record]
    if not records:
        return ProjectionPlan(relative, None)
    if len(records) != 1:
        raise ProjectionRestoreError(f"baseline repeats projection path {relative!r}")
    try:
        metadata, encoded_path = records[0].split(b"\t", 1)
        mode_raw, kind_raw, oid_raw = metadata.split()
        path = encoded_path.decode("utf-8")
        mode = mode_raw.decode("ascii")
        kind = kind_raw.decode("ascii")
        oid = oid_raw.decode("ascii")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProjectionRestoreError(
            f"baseline projection entry is malformed for {relative!r}"
        ) from exc
    if path != relative:
        raise ProjectionRestoreError(f"baseline returned the wrong path for {relative!r}")
    if mode != "100644" or kind != "blob" or FULL_SHA_RE.fullmatch(oid) is None:
        raise ProjectionRestoreError(
            f"baseline projection {relative!r} is not a regular 100644 blob"
        )
    raw_size = _git(root, "cat-file", "-s", oid)
    try:
        encoded_size = raw_size.decode("ascii").strip()
        if not encoded_size.isdigit():
            raise ValueError
        size = int(encoded_size)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProjectionRestoreError(
            f"baseline projection {relative!r} has an invalid Git object size"
        ) from exc
    if size > MAX_PROJECTION_BYTES:
        raise ProjectionRestoreError(
            f"baseline projection {relative!r} exceeds the "
            f"{MAX_PROJECTION_BYTES}-byte hard limit"
        )
    content = _git(root, "cat-file", "blob", oid)
    if len(content) != size or _git_blob_oid(content) != oid:
        raise ProjectionRestoreError(
            f"baseline projection {relative!r} disagrees with its Git object ID"
        )
    return ProjectionPlan(relative, content)


def _inspect_destination(root: Path, relative: str) -> OriginalFile:
    destination = root / relative
    parent = destination.parent
    try:
        parent_mode = parent.stat().st_mode
    except OSError as exc:
        raise ProjectionRestoreError(
            f"projection parent is unavailable for {relative!r}: {exc}"
        ) from exc
    if not stat.S_ISDIR(parent_mode) or parent.is_symlink():
        raise ProjectionRestoreError(
            f"projection parent must be a real directory for {relative!r}"
        )
    try:
        file_stat = destination.lstat()
    except FileNotFoundError as exc:
        raise ProjectionRestoreError(
            f"audited candidate projection {relative!r} is missing"
        ) from exc
    except OSError as exc:
        raise ProjectionRestoreError(
            f"could not inspect current projection {relative!r}: {exc}"
        ) from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise ProjectionRestoreError(
            f"audited candidate projection {relative!r} must remain a regular file"
        )
    if file_stat.st_size > MAX_PROJECTION_BYTES:
        raise ProjectionRestoreError(
            f"audited candidate projection {relative!r} exceeds the "
            f"{MAX_PROJECTION_BYTES}-byte hard limit"
        )
    try:
        return OriginalFile(destination.read_bytes(), stat.S_IMODE(file_stat.st_mode))
    except OSError as exc:
        raise ProjectionRestoreError(
            f"could not read current projection {relative!r}: {exc}"
        ) from exc


def _atomic_write(destination: Path, content: bytes, mode: int = 0o644) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.restore-", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _apply_projection(destination: Path, content: bytes | None, mode: int = 0o644) -> None:
    if content is None:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        return
    _atomic_write(destination, content, mode)


def _matches(destination: Path, content: bytes | None) -> bool:
    if content is None:
        return not destination.exists() and not destination.is_symlink()
    try:
        return (
            not destination.is_symlink()
            and destination.is_file()
            and destination.read_bytes() == content
            and stat.S_IMODE(destination.stat().st_mode) == 0o644
        )
    except OSError:
        return False


def mirror_queue_projections(root: Path, baseline_ref: str) -> None:
    root = root.resolve(strict=True)
    commit_sha = _resolve_exact_commit(root, baseline_ref)

    # Finish every Git/object and destination check before changing either
    # projection.  In particular, a missing blob is not treated as absence:
    # only an empty, successful ls-tree result produces a removal plan.
    plans = [
        _baseline_projection(root, commit_sha, relative)
        for relative in PROJECTION_PATHS
    ]
    originals = {
        plan.relative_path: _inspect_destination(root, plan.relative_path)
        for plan in plans
    }

    try:
        for plan in plans:
            _apply_projection(root / plan.relative_path, plan.content)
        for plan in plans:
            if not _matches(root / plan.relative_path, plan.content):
                raise ProjectionRestoreError(
                    f"postcheck failed for restored projection {plan.relative_path!r}"
                )
    except Exception as apply_error:
        rollback_errors = []
        for relative in reversed(PROJECTION_PATHS):
            original = originals[relative]
            try:
                _apply_projection(
                    root / relative,
                    original.content,
                    original.mode if original.mode is not None else 0o644,
                )
            except Exception as rollback_error:  # pragma: no cover - catastrophic I/O
                rollback_errors.append(f"{relative}: {rollback_error}")
        if rollback_errors:
            raise ProjectionRestoreError(
                "projection restore failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
            ) from apply_error
        raise ProjectionRestoreError(
            f"projection restore failed; original pair was restored: {apply_error}"
        ) from apply_error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        mirror_queue_projections(args.root, args.baseline_ref)
    except (OSError, ProjectionRestoreError) as exc:
        parser.error(str(exc))
    print(f"Mirrored derived queue projections to exact baseline {args.baseline_ref}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
