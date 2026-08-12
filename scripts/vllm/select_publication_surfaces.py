#!/usr/bin/env python3
"""Select validated current data or atomic last-known-good surfaces.

This is the reconciliation boundary between collection and publication.  It
never makes an invalid candidate look healthy: rejected surfaces are recorded
as degraded, restored as coherent transactions from a previously audited main
commit, rebuilt, and subjected to the complete audit again.
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
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError("validated baseline publication state has an invalid schema")
    mode = payload.get("mode")
    surfaces = payload.get("degraded_surfaces")
    since = payload.get("degraded_since")
    if (
        mode not in {"current", "fallback"}
        or not isinstance(surfaces, list)
        or any(not isinstance(surface, str) or surface not in SURFACE_SPECS for surface in surfaces)
        or len(set(surfaces)) != len(surfaces)
        or not FULL_SHA_RE.fullmatch(str(payload.get("baseline_ref") or ""))
        or _parse_utc(payload.get("generated_at")) is None
        or payload.get("fallback_max_age_hours") != FALLBACK_MAX_AGE_HOURS
        or not isinstance(since, dict)
        or (mode == "current" and surfaces)
        or (mode == "fallback" and set(since) != set(surfaces))
        or any(_parse_utc(value) is None for value in since.values())
    ):
        raise RuntimeError("validated baseline publication state is inconsistent")
    manifest = payload.get("restored_manifest")
    if mode == "current":
        if manifest not in (None, {}):
            raise RuntimeError("current baseline state declares restored content")
        return payload
    if not isinstance(manifest, dict) or set(manifest) != set(surfaces):
        raise RuntimeError("fallback baseline state has an incomplete restore manifest")
    for surface in surfaces:
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
    return payload


def _fallback_start_times(
    degraded: set[str],
    previous: dict | None,
    now: datetime,
) -> dict[str, str]:
    previous_surfaces = set((previous or {}).get("degraded_surfaces") or [])
    previous_since = (previous or {}).get("degraded_since") or {}
    current = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        surface: str(previous_since[surface])
        if surface in previous_surfaces
        else current
        for surface in sorted(degraded)
    }


def _raise_if_fallback_expired(
    degraded_since: dict[str, str],
    now: datetime,
) -> None:
    expired = []
    for surface, raw_since in degraded_since.items():
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
    candidate_errors = []
    degraded: set[str] = set(forced)
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
        "schema_version": 1,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "baseline_ref": baseline_ref,
        "mode": "current",
        "degraded_surfaces": [],
        "degraded_since": {},
        "fallback_max_age_hours": FALLBACK_MAX_AGE_HOURS,
        "candidate_errors": candidate_errors,
        "final_errors": [],
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
        for finding in source_audit.report.errors:
            surfaces = finding_surfaces(finding)
            record = _finding_record(finding, surfaces)
            candidate_errors.append(record)
            if not surfaces:
                raise RuntimeError(
                    "source preflight produced a global or unrouted audit finding"
                )
            degraded.update(surfaces)

        if degraded:
            degraded_since = _fallback_start_times(degraded, prior_state(), now)
            _raise_if_fallback_expired(degraded_since, now)
            preflight = {
                surface: _baseline_payloads(root, baseline_ref, SURFACE_SPECS[surface])
                for surface in sorted(degraded)
            }
            for surface in sorted(degraded):
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
                degraded.update(surfaces)
            else:
                unrouted.append(record)
        state["candidate_errors"] = candidate_errors
        if unrouted:
            state["mode"] = "blocked"
            state["degraded_surfaces"] = sorted(degraded)
            state["final_errors"] = unrouted
            _write_state(state_path, state)
            _emit_outputs(state)
            raise RuntimeError(
                "candidate audit has global or unrouted errors; refusing fallback"
            )
        if not degraded:
            _write_state(state_path, state)
            _emit_outputs(state)
            print("Publication selection: all candidate surfaces are valid and current.")
            return state

        degraded_since = _fallback_start_times(degraded, prior_state(), now)
        _raise_if_fallback_expired(degraded_since, now)
        additional = degraded - set(restored)
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
        state.update({
            "mode": "fallback",
            "degraded_surfaces": sorted(degraded),
            "degraded_since": degraded_since,
            "restored_paths": restored,
            "restored_manifest": _surface_manifest(root, restored),
        })
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
        if final.errors:
            state["mode"] = "blocked"
            _write_state(state_path, state)
            _emit_outputs(state)
            raise RuntimeError(
                "last-known-good surface selection still fails the complete dashboard audit"
            )
    except Exception as exc:
        if state.get("mode") != "blocked":
            state["mode"] = "blocked"
            state["degraded_surfaces"] = sorted(degraded)
            state["degraded_since"] = _fallback_start_times(
                degraded, previous_state if previous_state_loaded else None, now
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
    print(
        "Publication selection: retained last-known-good surface(s): "
        + ", ".join(sorted(degraded))
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
