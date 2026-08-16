#!/usr/bin/env python3
"""Select validated current data or atomic last-known-good surfaces.

This is the reconciliation boundary between collection and publication.  It
publishes usable-but-degraded candidate surfaces in place while restoring hard
failures as coherent transactions from a previously audited main commit. Any
restored result is rebuilt and subjected to the complete audit again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.audit_dashboard_data import DashboardAudit  # noqa: E402
from vllm.publication_surfaces import (  # noqa: E402
    SURFACE_SPECS,
    SurfaceSpec,
    finding_surfaces,
)


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_STATE = Path("data/vllm/ci/publication_state.json")
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
FALLBACK_MAX_AGE_HOURS = 36


class FallbackExpiredError(RuntimeError):
    def __init__(self, findings: list[dict]):
        super().__init__("last-known-good publication fallback exceeded its hard limit")
        self.findings = findings


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _publication_mode(
    fresh_degraded: set[str],
    fallback: set[str],
) -> str:
    if fresh_degraded and fallback:
        return "mixed"
    if fresh_degraded:
        return "degraded"
    if fallback:
        return "fallback"
    return "current"


def _run_git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _baseline_publication_state(
    root: Path,
    ref: str,
    state_path: Path,
) -> dict | None:
    """Read a prior selector state, failing closed if a tracked state is corrupt."""
    try:
        relative = state_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{relative}"],
        cwd=root,
        capture_output=True,
    )
    if exists.returncode != 0:
        return None
    try:
        payload = json.loads(_run_git(root, "show", f"{ref}:{relative}"))
    except (subprocess.CalledProcessError, json.JSONDecodeError, UnicodeError) as exc:
        raise RuntimeError("validated baseline publication state is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") not in {1, 2}:
        raise RuntimeError("validated baseline publication state has an invalid schema")

    schema_version = payload["schema_version"]
    mode = payload.get("mode")
    degraded = payload.get("degraded_surfaces")
    degraded_since = payload.get("degraded_since")
    if (
        not isinstance(degraded, list)
        or any(
            not isinstance(surface, str) or surface not in SURFACE_SPECS
            for surface in degraded
        )
        or len(set(degraded)) != len(degraded)
        or not FULL_SHA_RE.fullmatch(str(payload.get("baseline_ref") or ""))
        or _parse_utc(payload.get("generated_at")) is None
        or payload.get("fallback_max_age_hours") != FALLBACK_MAX_AGE_HOURS
        or not isinstance(degraded_since, dict)
    ):
        raise RuntimeError("validated baseline publication state is inconsistent")

    if schema_version == 1:
        if (
            mode not in {"current", "fallback"}
            or (mode == "current" and degraded)
            or set(degraded_since) != set(degraded)
            or any(_parse_utc(value) is None for value in degraded_since.values())
        ):
            raise RuntimeError("validated baseline publication state is inconsistent")
        fresh_degraded: list[str] = []
        fallback = list(degraded)
        fallback_since = dict(degraded_since)
        normalized = {
            **payload,
            "schema_version": 2,
            "mode": "fallback" if fallback else "current",
            "degraded_surfaces": sorted(fallback),
            "fresh_degraded_surfaces": [],
            "fallback_surfaces": sorted(fallback),
            "degraded_since": dict(degraded_since),
            "fallback_since": fallback_since,
        }
    else:
        fresh_degraded = payload.get("fresh_degraded_surfaces")
        fallback = payload.get("fallback_surfaces")
        fallback_since = payload.get("fallback_since")
        if (
            mode not in {"current", "degraded", "fallback", "mixed"}
            or not isinstance(fresh_degraded, list)
            or not isinstance(fallback, list)
            or any(
                not isinstance(surface, str) or surface not in SURFACE_SPECS
                for surface in [*fresh_degraded, *fallback]
            )
            or len(set(fresh_degraded)) != len(fresh_degraded)
            or len(set(fallback)) != len(fallback)
            or set(fresh_degraded) & set(fallback)
            or set(degraded) != set(fresh_degraded) | set(fallback)
            or set(degraded_since) != set(degraded)
            or any(_parse_utc(value) is None for value in degraded_since.values())
            or not isinstance(fallback_since, dict)
            or set(fallback_since) != set(fallback)
            or any(_parse_utc(value) is None for value in fallback_since.values())
            or mode != _publication_mode(set(fresh_degraded), set(fallback))
        ):
            raise RuntimeError("validated baseline publication state is inconsistent")
        normalized = payload

    manifest = payload.get("restored_manifest")
    restored_paths = payload.get("restored_paths")
    if not fallback:
        if manifest not in (None, {}):
            raise RuntimeError("non-fallback baseline state declares restored content")
        if restored_paths not in (None, {}):
            raise RuntimeError("non-fallback baseline state declares restored paths")
        return normalized
    if not isinstance(manifest, dict) or set(manifest) != set(fallback):
        raise RuntimeError("fallback baseline state has an incomplete restore manifest")
    if restored_paths is not None and (
        not isinstance(restored_paths, dict)
        or set(restored_paths) != set(fallback)
    ):
        raise RuntimeError("fallback baseline state has incomplete restored paths")
    for surface in fallback:
        entries = manifest.get(surface)
        if not isinstance(entries, dict):
            raise RuntimeError(f"fallback baseline manifest for {surface} is invalid")
        spec = SURFACE_SPECS[surface]
        expected = set(spec.required_paths)
        for relative in spec.optional_paths:
            exists = subprocess.run(
                ["git", "cat-file", "-e", f"{ref}:{relative}"],
                cwd=root,
                capture_output=True,
            )
            if exists.returncode == 0:
                expected.add(relative)
        expected.update(_baseline_paths(root, ref, spec))
        if set(entries) != expected:
            raise RuntimeError(
                f"fallback baseline manifest path set for {surface} is inconsistent"
            )
        for relative, descriptor in entries.items():
            if not isinstance(descriptor, dict):
                raise RuntimeError(
                    f"fallback baseline descriptor for {relative} is invalid"
                )
            payload_bytes = _run_git(root, "show", f"{ref}:{relative}")
            expected_sha = str(descriptor.get("sha256") or "")
            if (
                descriptor.get("bytes") != len(payload_bytes)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
                or hashlib.sha256(payload_bytes).hexdigest() != expected_sha
            ):
                raise RuntimeError(
                    f"fallback baseline content for {relative} does not match its manifest"
                )
        if restored_paths is not None and restored_paths.get(surface) != sorted(entries):
            raise RuntimeError(
                f"fallback baseline restored paths for {surface} are inconsistent"
            )
    return normalized


def _start_times(
    surfaces: set[str],
    previous: dict | None,
    *,
    previous_surfaces_key: str,
    previous_since_key: str,
    now: datetime,
) -> dict[str, str]:
    previous_surfaces = set((previous or {}).get(previous_surfaces_key) or [])
    previous_since = (previous or {}).get(previous_since_key) or {}
    current = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        surface: str(previous_since[surface])
        if surface in previous_surfaces
        else current
        for surface in sorted(surfaces)
    }


def _degraded_start_times(
    degraded: set[str],
    previous: dict | None,
    now: datetime,
) -> dict[str, str]:
    return _start_times(
        degraded,
        previous,
        previous_surfaces_key="degraded_surfaces",
        previous_since_key="degraded_since",
        now=now,
    )


def _fallback_start_times(
    fallback: set[str],
    previous: dict | None,
    now: datetime,
) -> dict[str, str]:
    return _start_times(
        fallback,
        previous,
        previous_surfaces_key="fallback_surfaces",
        previous_since_key="fallback_since",
        now=now,
    )


def _raise_if_fallback_expired(
    fallback_since: dict[str, str],
    now: datetime,
) -> None:
    expired = []
    for surface, raw_since in fallback_since.items():
        since = _parse_utc(raw_since)
        age_hours = (now - since).total_seconds() / 3600 if since else float("inf")
        if age_hours > FALLBACK_MAX_AGE_HOURS or age_hours < -1:
            expired.append({
                "severity": "error",
                "code": "publication-fallback-expired",
                "message": (
                    f"{surface} has used last-known-good data for {age_hours:.1f}h; "
                    f"the hard limit is {FALLBACK_MAX_AGE_HOURS}h"
                ),
                "path": DEFAULT_STATE.as_posix(),
                "context": {"surface": surface, "since": raw_since},
                "surfaces": [],
            })
    if expired:
        raise FallbackExpiredError(expired)


def _baseline_paths(root: Path, ref: str, spec: SurfaceSpec) -> set[str]:
    prefixes = sorted({pattern.split("*", 1)[0].rstrip("/") for pattern in spec.globs})
    paths: set[str] = set()
    for prefix in prefixes:
        output = _run_git(root, "ls-tree", "-r", "--name-only", ref, "--", prefix)
        for raw in output.decode().splitlines():
            if any(Path(raw).match(pattern) for pattern in spec.globs):
                paths.add(raw)
    return paths


def _baseline_payloads(
    root: Path,
    ref: str,
    spec: SurfaceSpec,
) -> tuple[dict[str, bytes], set[str]]:
    """Preflight a complete surface before changing any candidate file."""
    payloads: dict[str, bytes] = {}
    absent_optional: set[str] = set()
    for relative in spec.required_paths:
        try:
            payloads[relative] = _run_git(root, "show", f"{ref}:{relative}")
        except subprocess.CalledProcessError:
            raise RuntimeError(
                f"validated baseline {ref} is missing required publication path {relative}"
            ) from None
    for relative in spec.optional_paths:
        try:
            payloads[relative] = _run_git(root, "show", f"{ref}:{relative}")
        except subprocess.CalledProcessError:
            absent_optional.add(relative)
    for relative in _baseline_paths(root, ref, spec):
        try:
            payloads[relative] = _run_git(root, "show", f"{ref}:{relative}")
        except subprocess.CalledProcessError:
            raise RuntimeError(
                f"validated baseline {ref} changed while reading {relative}"
            ) from None
    return payloads, absent_optional


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(payload)
        staged = Path(handle.name)
    os.replace(staged, path)


def restore_surface(
    root: Path,
    ref: str,
    spec: SurfaceSpec,
    *,
    preflight: tuple[dict[str, bytes], set[str]] | None = None,
) -> list[str]:
    """Restore one transaction after preflighting every baseline member."""
    payloads, absent_optional = preflight or _baseline_payloads(root, ref, spec)
    baseline = set(payloads)
    candidate = {
        path.relative_to(root).as_posix()
        for pattern in spec.globs
        for path in root.glob(pattern)
        if path.is_file() or path.is_symlink()
    }
    for relative in sorted(candidate - baseline):
        (root / relative).unlink()
    for relative in sorted(absent_optional):
        path = root / relative
        if path.is_file() or path.is_symlink():
            path.unlink()
    for relative, payload in sorted(payloads.items()):
        _atomic_write(root / relative, payload)
    return sorted(payloads)


def _surface_manifest(root: Path, restored: dict[str, list[str]]) -> dict[str, dict]:
    manifest = {}
    for surface, paths in sorted(restored.items()):
        entries = {}
        for relative in sorted(paths):
            path = root / relative
            entries[relative] = {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        manifest[surface] = entries
    return manifest


def _finding_record(finding, surfaces: Iterable[str]) -> dict:
    return {
        **finding.as_dict(),
        "surfaces": sorted(surfaces),
    }


def _apply_surface_state(
    state: dict,
    fresh_degraded: set[str],
    fallback: set[str],
    previous: dict | None,
    now: datetime,
) -> None:
    """Record disjoint fresh/fallback lanes and their independent clocks."""
    fallback = set(fallback)
    fresh_degraded = set(fresh_degraded) - fallback
    degraded = fresh_degraded | fallback
    state.update({
        "mode": _publication_mode(fresh_degraded, fallback),
        "degraded_surfaces": sorted(degraded),
        "fresh_degraded_surfaces": sorted(fresh_degraded),
        "fallback_surfaces": sorted(fallback),
        "degraded_since": _degraded_start_times(degraded, previous, now),
        "fallback_since": _fallback_start_times(fallback, previous, now),
    })


def _write_state(path: Path, state: dict) -> None:
    _atomic_write(path, (json.dumps(state, indent=2, sort_keys=True) + "\n").encode())


def _emit_outputs(state: dict) -> None:
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    degraded = bool(state.get("degraded_surfaces"))
    blocked = state.get("mode") == "blocked"
    lines = [
        f"degraded={'true' if degraded else 'false'}",
        f"blocked={'true' if blocked else 'false'}",
        f"degraded_surfaces={','.join(state.get('degraded_surfaces') or [])}",
        (
            "fresh_degraded_surfaces="
            + ",".join(state.get("fresh_degraded_surfaces") or [])
        ),
        f"fallback_surfaces={','.join(state.get('fallback_surfaces') or [])}",
    ]
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def _rebuild_operations(root: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "scripts/vllm/build_operations_snapshot.py",
            "--input-dir",
            "data/vllm/ci",
            "--output",
            "data/vllm/ci/operations_v2.json",
        ],
        cwd=root,
        check=True,
    )


def select_publication(
    root: Path,
    baseline_ref: str,
    state_path: Path,
    *,
    forced_degraded: Iterable[str] = (),
) -> dict:
    baseline_ref = baseline_ref.strip().lower()
    if not FULL_SHA_RE.fullmatch(baseline_ref):
        raise ValueError("baseline ref must be one full lowercase commit SHA")
    _run_git(root, "cat-file", "-e", f"{baseline_ref}^{{commit}}")
    previous_state: dict | None = None
    previous_state_loaded = False

    def prior_state() -> dict | None:
        nonlocal previous_state, previous_state_loaded
        if not previous_state_loaded:
            previous_state = _baseline_publication_state(
                root, baseline_ref, state_path
            )
            previous_state_loaded = True
        return previous_state

    forced = {str(surface).strip() for surface in forced_degraded if str(surface).strip()}
    unknown_forced = forced - set(SURFACE_SPECS)
    if unknown_forced:
        raise ValueError(f"unknown forced publication surfaces: {sorted(unknown_forced)}")

    now = datetime.now(timezone.utc)
    candidate_errors: list[dict] = []
    candidate_degradations: list[dict] = []
    fresh_degraded: set[str] = set()
    fallback: set[str] = set(forced)
    restored: dict[str, list[str]] = {}
    for surface in sorted(forced):
        candidate_errors.append({
            "severity": "error",
            "code": "publication-collector-failed",
            "message": f"{surface} collection failed before publication validation",
            "path": "",
            "context": {"surface": surface},
            "surfaces": [surface],
        })
    state = {
        "schema_version": 2,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "baseline_ref": baseline_ref,
        "mode": "current",
        "degraded_surfaces": [],
        "fresh_degraded_surfaces": [],
        "fallback_surfaces": [],
        "degraded_since": {},
        "fallback_since": {},
        "fallback_max_age_hours": FALLBACK_MAX_AGE_HOURS,
        "candidate_errors": candidate_errors,
        "candidate_degradations": candidate_degradations,
        "final_errors": [],
        "final_degradations": [],
        "restored_paths": {},
        "restored_manifest": {},
    }

    try:
        # Parse all source files first. This catches truncated/missing collector
        # output without depending on the derived Operations bundle being
        # buildable. Forced collector failures join the same transaction set.
        source_audit = DashboardAudit(
            root,
            allow_publication_fallback=False,
            publication_state_path=state_path,
        )
        source_audit.audit_publication_surface_files()
        unrouted = []
        for finding in source_audit.report.errors:
            surfaces = finding_surfaces(finding)
            record = _finding_record(finding, surfaces)
            candidate_errors.append(record)
            if surfaces:
                fallback.update(surfaces)
            else:
                unrouted.append(record)
        for finding in getattr(source_audit.report, "degradations", []):
            surfaces = finding_surfaces(finding)
            record = _finding_record(finding, surfaces)
            candidate_degradations.append(record)
            if surfaces:
                fresh_degraded.update(surfaces)
            else:
                unrouted.append(record)
        fresh_degraded.difference_update(fallback)
        state["candidate_errors"] = candidate_errors
        state["candidate_degradations"] = candidate_degradations
        if unrouted:
            previous = prior_state() if fresh_degraded or fallback else None
            _apply_surface_state(state, fresh_degraded, fallback, previous, now)
            state["mode"] = "blocked"
            state["final_errors"] = unrouted
            _write_state(state_path, state)
            _emit_outputs(state)
            raise RuntimeError(
                "source preflight produced a global or unrouted audit finding"
            )

        if fallback:
            previous = prior_state()
            _apply_surface_state(state, fresh_degraded, fallback, previous, now)
            _raise_if_fallback_expired(state["fallback_since"], now)
            preflight = {
                surface: _baseline_payloads(root, baseline_ref, SURFACE_SPECS[surface])
                for surface in sorted(fallback)
            }
            for surface in sorted(fallback):
                restored[surface] = restore_surface(
                    root,
                    baseline_ref,
                    SURFACE_SPECS[surface],
                    preflight=preflight[surface],
                )

        # Build the candidate read model after any command-level or parse-level
        # quarantine, then use the full cross-surface audit to discover semantic
        # transaction failures such as matrix/health count drift.
        _rebuild_operations(root)
        candidate = DashboardAudit(
            root,
            allow_publication_fallback=False,
            publication_state_path=state_path,
        ).run()
        unrouted = []
        for finding in candidate.errors:
            surfaces = finding_surfaces(finding)
            record = _finding_record(finding, surfaces)
            candidate_errors.append(record)
            if surfaces:
                fallback.update(surfaces)
            else:
                unrouted.append(record)
        for finding in getattr(candidate, "degradations", []):
            surfaces = finding_surfaces(finding)
            record = _finding_record(finding, surfaces)
            candidate_degradations.append(record)
            if surfaces:
                fresh_degraded.update(surfaces)
            else:
                unrouted.append(record)
        fresh_degraded.difference_update(fallback)
        state["candidate_errors"] = candidate_errors
        state["candidate_degradations"] = candidate_degradations
        previous = prior_state() if fresh_degraded or fallback else None
        _apply_surface_state(state, fresh_degraded, fallback, previous, now)
        if unrouted:
            state["mode"] = "blocked"
            state["final_errors"] = unrouted
            _write_state(state_path, state)
            _emit_outputs(state)
            raise RuntimeError(
                "candidate audit has global or unrouted errors; refusing fallback"
            )
        if not fallback:
            _write_state(state_path, state)
            _emit_outputs(state)
            if fresh_degraded:
                print(
                    "Publication selection: published fresh degraded surface(s): "
                    + ", ".join(sorted(fresh_degraded))
                )
            else:
                print("Publication selection: all candidate surfaces are valid and current.")
            return state

        _raise_if_fallback_expired(state["fallback_since"], now)
        additional = fallback - set(restored)
        preflight = {
            surface: _baseline_payloads(root, baseline_ref, SURFACE_SPECS[surface])
            for surface in sorted(additional)
        }
        for surface in sorted(additional):
            restored[surface] = restore_surface(
                root,
                baseline_ref,
                SURFACE_SPECS[surface],
                preflight=preflight[surface],
            )
        state["restored_paths"] = restored
        state["restored_manifest"] = _surface_manifest(root, restored)
        # State must exist before the final audit so bounded stale-source
        # handling applies only to the explicitly quarantined transactions.
        _write_state(state_path, state)
        _rebuild_operations(root)
        final = DashboardAudit(
            root,
            allow_publication_fallback=True,
            publication_state_path=state_path,
        ).run()
        state["final_errors"] = [
            _finding_record(finding, finding_surfaces(finding))
            for finding in final.errors
        ]
        final_degradations = [
            _finding_record(finding, finding_surfaces(finding))
            for finding in getattr(final, "degradations", [])
        ]
        state["final_degradations"] = final_degradations
        unrouted_final_degradations = [
            record for record in final_degradations if not record["surfaces"]
        ]
        for record in final_degradations:
            fresh_degraded.update(record["surfaces"])
        final_error_surfaces = {
            surface
            for record in state["final_errors"]
            for surface in record["surfaces"]
        }
        # Hard errors are never represented as publishable fresh degradation.
        fresh_degraded.difference_update(fallback | final_error_surfaces)
        _apply_surface_state(state, fresh_degraded, fallback, previous, now)
        if final.errors or unrouted_final_degradations:
            state["mode"] = "blocked"
            _write_state(state_path, state)
            _emit_outputs(state)
            raise RuntimeError(
                "last-known-good surface selection still fails the complete dashboard audit"
            )
    except Exception as exc:
        if state.get("mode") != "blocked":
            previous = previous_state if previous_state_loaded else None
            _apply_surface_state(state, fresh_degraded, fallback, previous, now)
            state["mode"] = "blocked"
            state["restored_paths"] = restored
            state["restored_manifest"] = (
                _surface_manifest(root, restored) if restored else {}
            )
            state["final_errors"] = (
                exc.findings
                if isinstance(exc, FallbackExpiredError)
                else [{
                    "severity": "error",
                    "code": "publication-selection-failed",
                    "message": str(exc),
                    "path": DEFAULT_STATE.as_posix(),
                    "surfaces": [],
                }]
            )
            _write_state(state_path, state)
            _emit_outputs(state)
        raise

    _write_state(state_path, state)
    _emit_outputs(state)
    if fresh_degraded:
        print(
            "Publication selection: retained last-known-good surface(s) "
            f"{', '.join(sorted(fallback))}; published fresh degraded surface(s) "
            + ", ".join(sorted(fresh_degraded))
        )
    else:
        print(
            "Publication selection: retained last-known-good surface(s): "
            + ", ".join(sorted(fallback))
        )
    return state


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--state-output", default=str(DEFAULT_STATE))
    parser.add_argument(
        "--force-degraded-surface",
        action="append",
        default=[],
        choices=sorted(SURFACE_SPECS),
        help="Restore this surface even when its failed collector left valid-looking files",
    )
    parser.add_argument(
        "--force-degraded-surfaces",
        default="",
        help="Comma-separated form of --force-degraded-surface for workflow plumbing",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    state_path = Path(args.state_output)
    if not state_path.is_absolute():
        state_path = root / state_path
    try:
        forced = [*args.force_degraded_surface]
        forced.extend(
            surface.strip()
            for surface in args.force_degraded_surfaces.split(",")
            if surface.strip()
        )
        select_publication(
            root,
            args.baseline_ref,
            state_path,
            forced_degraded=forced,
        )
    except Exception as exc:
        print(f"Publication selection failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
