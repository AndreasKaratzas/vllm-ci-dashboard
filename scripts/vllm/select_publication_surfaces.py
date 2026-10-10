#!/usr/bin/env python3
"""Select validated current data or atomic last-known-good surfaces.

This is the reconciliation boundary between collection and publication.  It
publishes usable-but-degraded candidate surfaces in place while restoring hard
failures as coherent transactions from a previously audited main commit. Any
restored result is rebuilt and subjected to the complete audit again.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.audit_dashboard_data import DashboardAudit  # noqa: E402
from vllm.bounded_json import pretty_json_bytes, write_pretty_json_lkg  # noqa: E402
from vllm.dashboard_storage_budget import writer_max_bytes  # noqa: E402
from vllm.publication_surfaces import (  # noqa: E402
    LEGACY_CI_SURFACE,
    LEGACY_CI_SURFACE_SPEC,
    LEGACY_SURFACE_ALIASES,
    PRE_ANALYTICS_CI_CORE_SURFACE_SPEC,
    PRE_ANALYTICS_CI_GATING_SURFACE_SPEC,
    PRE_QUEUE_SPLIT_SURFACE_CONTRACT_VERSION,
    PRE_QUEUE_SPLIT_SURFACE_SPEC,
    PRE_PARITY_OVERRIDES_SURFACE_CONTRACT_VERSION,
    PRE_PARITY_OVERRIDES_SURFACE_SPECS,
    PRE_RUNTIME_PARITY_SURFACE_CONTRACT_VERSION,
    PRE_RUNTIME_PARITY_SURFACE_SPECS,
    PRE_VIEW_RETIREMENT_SURFACE_CONTRACT_VERSION,
    PRE_VIEW_RETIREMENT_SURFACE_SPECS,
    RETIRED_SURFACES,
    RETIRED_RUNTIME_PARITY_PATHS,
    RETIRED_PARITY_OVERRIDE_PATHS,
    SURFACE_CONTRACT_VERSION,
    SURFACE_SPECS,
    SurfaceSpec,
    fallback_dependency_closure,
    finding_surfaces,
    ignored_watcher_state_paths,
)


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_STATE = Path("data/vllm/ci/publication_state.json")
PUBLICATION_STATE_MAX_BYTES = writer_max_bytes("publication_state")
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
FALLBACK_MAX_AGE_HOURS = 36
DECLARED_SURFACE_NAMES = frozenset(SURFACE_SPECS)
COLLECTOR_FAILURE_SCHEMA_VERSION = 1
COLLECTOR_REASON_CLASSES = frozenset({
    "payload-budget",
    "rate-limit",
    "timeout",
    "schema-drift",
    "transient-http",
    "network",
    "dependency-unavailable",
    "command-error",
})
TRANSIENT_COLLECTOR_REASONS = frozenset({
    "rate-limit",
    "timeout",
    "transient-http",
    "network",
})
TRANSIENT_ALERT_PERSISTENCE_RUNS = 2
CI_HEALTH_PATH = "data/vllm/ci/ci_health.json"
UPSTREAM_RETRY_ALERT_PERSISTENCE_RUNS = 2
UPSTREAM_RETRY_FINDING_CODE = "publication-upstream-retry-provisional"
UPSTREAM_RETRY_RECONCILIATION_CODES = frozenset({
    "analytics-jsonl-build-mismatch",
    "ci-health-jsonl-build-mismatch",
    "matrix-analytics-build",
    "matrix-health-build",
})
UPSTREAM_RETRY_TRANSACTION_SURFACES = frozenset({"ci_core", "ci_analytics"})
UPSTREAM_RETRY_CANDIDATE_PHASE = "active-retry-candidate-preflight"
UPSTREAM_RETRY_CANDIDATE_AUDITS = (
    "audit_ci_health",
    "audit_root_test_results",
    "audit_shard_bases",
    "audit_analytics",
    "audit_amd_matrix",
)
QUEUE_LIVE_SURFACE = "queue"
QUEUE_COMPANION_SURFACES = frozenset({
    "queue_capacity",
    "queue_omni",
    "queue_workload",
})
QUEUE_SPLIT_SURFACES = frozenset({
    QUEUE_LIVE_SURFACE,
    *QUEUE_COMPANION_SURFACES,
})
SELECTION_DIAGNOSTICS_MAX_BYTES = 256 * 1024
SELECTION_FINDINGS_OUTPUT_MAX_BYTES = 32 * 1024
DIAGNOSTIC_FINDING_FIELDS = (
    "candidate_errors", "candidate_degradations", "final_errors", "final_degradations",
)


class FallbackExpiredError(RuntimeError):
    def __init__(self, findings: list[dict]):
        super().__init__("last-known-good publication fallback exceeded its hard limit")
        self.findings = findings


def _bounded_text(value: object, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _safe_detail_text(value: object) -> str:
    text = _bounded_text(value, limit=1000)
    text = re.sub(r"https?://\S+", "<url>", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?i)\b(?:authorization|token|password|secret|api[_-]?key)\b"
        r"\s*[:=]\s*\S+",
        "<redacted>",
        text,
    )
    text = re.sub(r"(?i)\bbearer\s+\S+", "Bearer <redacted>", text)
    text = re.sub(
        r"(?i)\b(?:github_pat_|gh[pousr]_|bkua_|bkup_)[A-Za-z0-9_]+",
        "<redacted-token>",
        text,
    )
    return text


def _diagnostic_text(value: object, *, limit: int = 1000) -> str:
    text = _safe_detail_text(value)
    text = re.sub(r"(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", "<id>", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<address>", text)
    text = re.sub(
        r"(?i)\b(?:agent|node|host|hostname|machine)(?:[_ -]?(?:id|name))?\s*(?:[:=]\s*|\s+)\S+",
        "<redacted-identity>", text,
    )
    return text[:limit]


def _diagnostic_context(value: object, depth: int = 0, remaining: list[int] | None = None) -> dict:
    """Keep invariant counts/source identities, never arbitrary logs or identities."""
    if not isinstance(value, Mapping) or depth > 2:
        return {}
    remaining = [128] if remaining is None else remaining
    result: dict[str, Any] = {}
    for key, raw in list(value.items())[:32]:
        if remaining[0] <= 0:
            break
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", key):
            continue
        if re.search(r"(?i)agent|node|host|machine|token|secret|password|authorization|cache|log|detail|output|command", key):
            continue
        remaining[0] -= 1
        if raw is None or isinstance(raw, bool):
            result[key] = raw
        elif isinstance(raw, int) and abs(raw) <= 2 ** 63 or isinstance(raw, float) and math.isfinite(raw):
            result[key] = raw
        elif isinstance(raw, Mapping):
            result[key] = _diagnostic_context(raw, depth + 1, remaining)
        elif isinstance(raw, str) and key in {
            "source", "phase", "pipeline", "source_pipeline", "surface", "field",
            "branch", "job_scope", "definition_source", "reason_class", "exception_type",
            "commit_sha", "definition_commit", "runtime_source_commit_sha",
        }:
            result[key] = _diagnostic_text(raw, limit=160)
    return result


def _diagnostic_finding(value: object) -> dict:
    row = value if isinstance(value, Mapping) else {}
    return {
        "severity": row.get("severity") if isinstance(row.get("severity"), str) and row.get("severity") in {"error", "degradation", "warning"} else "error",
        "code": _diagnostic_text(row.get("code"), limit=120),
        "message": _diagnostic_text(row.get("message")),
        "path": _diagnostic_text(row.get("path"), limit=240),
        "surfaces": [surface for surface in row.get("surfaces") or [] if isinstance(surface, str) and surface in SURFACE_SPECS],
        "context": _diagnostic_context(row.get("context"), remaining=[32]),
    }


def _candidate_ci_evidence(root: Path) -> dict:
    """Read only explicit generated sources and retain small typed summaries."""
    evidence = {}
    for filename in ("ci_health.json", "amd_test_matrix.json", "config_parity.json", "test_group_parity.json", "analytics.json"):
        path = root / "data/vllm/ci" / filename
        item: dict[str, Any] = {"readable": False}
        evidence[filename] = item
        try:
            resolved = path.resolve()
            if path.is_symlink() or not resolved.is_relative_to(root.resolve()) or any(part.startswith(".cache") for part in resolved.parts):
                continue
            if path.stat().st_size > 64 * 1024 * 1024:
                item["reason"] = "source-too-large-for-diagnostics"
                continue
            raw = path.read_bytes()
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                continue
        except (OSError, ValueError):
            continue
        item.update(readable=True, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        if _parse_utc(payload.get("generated_at")):
            item["generated_at"] = payload["generated_at"]
        source = payload.get("source") or {}
        item["source"] = {
            key: value for key, value in source.items()
            if key in {"pipeline", "definition_source"} and isinstance(value, str) and value in {"ci", "amd-ci", "main_ci_inline_and_native_amd"}
            or key in {"commit_sha", "current_definition_commit_sha", "runtime_source_commit_sha", "main_commit"}
            and isinstance(value, str) and FULL_SHA_RE.fullmatch(value)
        } if isinstance(source, dict) else {}
        item["summary"] = _diagnostic_context(payload.get("summary"))

        def build_summary(build: object) -> dict:
            if not isinstance(build, dict):
                return {}
            summary = _diagnostic_context(build)
            for key in ("created_at", "finished_at"):
                if _parse_utc(build.get(key)):
                    summary[key] = build[key]
            if isinstance(build.get("state"), str) and build["state"] in {"passed", "failed", "canceled", "cancelled", "running", "scheduled", "blocked", "skipped"}:
                summary["state"] = build["state"]
            commit = build.get("commit")
            if isinstance(commit, str) and FULL_SHA_RE.fullmatch(commit):
                summary["commit"] = commit
            build_url = build.get("build_url") or build.get("web_url")
            match = re.fullmatch(r"https://buildkite\.com/vllm/(ci|amd-ci)/builds/([1-9][0-9]*)", build_url) if isinstance(build_url, str) else None
            if match:
                summary["pipeline_from_build_url"] = match[1]
                summary["number_from_build_url"] = int(match[2])
            return summary

        if filename == "ci_health.json":
            item["cohorts"] = {
                side: {key: build_summary((payload.get(side) or {}).get(key))
                       for key in ("latest_pipeline_build", "latest_test_signal_build", "latest_build")}
                for side in ("amd", "upstream") if isinstance(payload.get(side), dict)
            }
        elif filename == "analytics.json":
            ci = payload.get("ci") or {}
            if isinstance(ci, dict):
                builds = ci.get("builds") or []
                item["ci_build_count"] = len(builds) if isinstance(builds, list) else None
                item["latest_ci_builds"] = [build_summary(build) for build in builds[:5]] if isinstance(builds, list) else []
        elif filename == "amd_test_matrix.json":
            item["latest_build_number"] = _positive_int(source.get("latest_build_number")) if isinstance(source, dict) else None
            rows = payload.get("rows") or []
            item["definition_row_count"] = len(rows) if isinstance(rows, list) else None
            routes = []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                cells = row.get("cells") or {}
                for arch, cell in cells.items() if isinstance(cells, dict) else []:
                    if arch not in {"mi250", "mi300", "mi325", "mi355"} or not isinstance(cell, dict):
                        continue
                    variants = cell.get("variants") or []
                    for variant in variants if isinstance(variants, list) else []:
                        if not isinstance(variant, dict):
                            continue
                        definition = variant.get("definition_id")
                        if not isinstance(definition, str) or not re.fullmatch(r"\.buildkite/test_areas/[A-Za-z0-9_./-]+\.ya?ml#[A-Za-z0-9_.:-]+", definition):
                            continue
                        if _diagnostic_text(definition, limit=320) != definition:
                            continue
                        routes.append({"definition_id": definition, "architecture": arch,
                                       "optional": variant.get("optional") is True,
                                       "soft_fail": variant.get("soft_fail") is True,
                                       "latest_matched": variant.get("latest_matched") is True})
                        if len(routes) == 16:
                            break
                    if len(routes) == 16:
                        break
                if len(routes) == 16:
                    break
            item["route_sample"] = routes
    return evidence


def _bounded_selection_diagnostics(diagnostics: dict, *, max_bytes: int) -> dict:
    state = diagnostics.get("_selection_state") or {}
    findings = {field: [_diagnostic_finding(row) for row in state.get(field) or []]
                for field in DIAGNOSTIC_FINDING_FIELDS}
    # Round-robin retention prevents a large post-fallback lane from erasing
    # every initial candidate finding, or the reverse.
    priority = [(field, index) for index in range(max((len(rows) for rows in findings.values()), default=0))
                for field in DIAGNOSTIC_FINDING_FIELDS if index < len(findings[field])]
    def candidate(count: int) -> dict:
        selected = set(priority[:count])
        kept = {field: [row for index, row in enumerate(rows) if (field, index) in selected]
                for field, rows in findings.items()}
        return {
            "schema_version": 1, "generated_at": diagnostics["generated_at"],
            "phase": diagnostics.get("phase", "publication-selection"),
            "mode": state.get("mode", "blocked"),
            "exception_type": diagnostics.get("exception_type"),
            "candidate_evidence": diagnostics.get("candidate_evidence", {}),
            **kept,
            "retention": {field: {"source": len(findings[field]), "published": len(kept[field]),
                                  "omitted": len(findings[field]) - len(kept[field])}
                          for field in DIAGNOSTIC_FINDING_FIELDS},
        }
    low, high, best = 0, len(priority), None
    while low <= high:
        keep = (low + high) // 2
        attempt = candidate(keep)
        if len(pretty_json_bytes(attempt)) <= max_bytes:
            best, low = attempt, keep + 1
        else:
            high = keep - 1
    if best is None:
        raise RuntimeError("sanitized selection diagnostic metadata exceeded its bound")
    return best


def _emit_diagnostic_findings(diagnostics: dict) -> None:
    output_source = {**diagnostics, "candidate_evidence": {}}
    output = _bounded_selection_diagnostics(output_source, max_bytes=SELECTION_FINDINGS_OUTPUT_MAX_BYTES)
    encoded = base64.b64encode(json.dumps(output, separators=(",", ":")).encode()).decode()
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"diagnostic_findings_b64={encoded}\n")


def _write_selection_diagnostics(path: Path, diagnostics: dict) -> None:
    artifact = _bounded_selection_diagnostics(diagnostics, max_bytes=SELECTION_DIAGNOSTICS_MAX_BYTES)
    _atomic_write(path, pretty_json_bytes(artifact))
    _emit_diagnostic_findings(diagnostics)
    counts = ", ".join(f"{field}={record['source']}" for field, record in artifact["retention"].items())
    print(f"Publication selection diagnostics: {counts}")


def _validate_diagnostics_path(root: Path, path: Path, state_path: Path | None = None) -> None:
    resolved = path.resolve()
    if state_path is not None and resolved == state_path.resolve() or any(
        resolved.is_relative_to(root / directory)
        for directory in ("data", "docs", "dashboards", "_site")
    ):
        raise ValueError("Diagnostic artifacts must remain outside publication data and assets")


def ci_core_report_exit_code(report: object, exit_code: int) -> int:
    """A successful subprocess also needs a usable standard JSON audit report."""
    if not isinstance(report, Mapping) or not isinstance(report.get("metrics"), Mapping):
        return exit_code or 1
    for lane, severity in (("errors", "error"), ("degradations", "degradation"), ("warnings", "warning")):
        findings = report.get(lane)
        if not isinstance(findings, list) or any(
            not isinstance(row, Mapping) or row.get("severity") != severity
            or not isinstance(row.get("code"), str) or not isinstance(row.get("message"), str)
            for row in findings
        ):
            return exit_code or 1
    return exit_code or (1 if report["errors"] else 0)


def write_ci_core_diagnostics(
    root: Path, path: Path, report: object, exit_code: int,
    *, collector_failures: Iterable[Mapping[str, Any]] = (),
) -> None:
    """Persist only bounded source facts and findings from the early raw audit."""
    _validate_diagnostics_path(root.resolve(), path)
    payload = report if isinstance(report, Mapping) else {}
    errors = list(payload["errors"]) if isinstance(payload.get("errors"), list) else []
    degradations = payload.get("degradations") if isinstance(payload.get("degradations"), list) else []
    for raw in collector_failures:
        failure = _normalize_collector_failure(raw)
        if failure["surface"] != "ci_core":
            continue
        summary = _diagnostic_text(failure["details"].get("summary", ""))
        errors.append({
            "severity": "error", "code": "ci-core-collector-unavailable",
            "message": (
                f"{failure['step']} exited {failure['exit_code']} "
                f"({failure['reason_class']})" + (f": {summary}" if summary else "")
            ),
            "surfaces": ["ci_core"],
            "context": {"exit_code": failure["exit_code"], "reason_class": failure["reason_class"]},
        })
    if exit_code and not errors:
        errors = [{"severity": "error", "code": "ci-core-audit-report-unavailable",
                   "message": "Current CI core validation failed without a usable JSON error report",
                   "context": {"exit_code": exit_code}}]
    diagnostics = {
        "generated_at": _utc_now(), "phase": "ci-core-pre-analytics",
        "candidate_evidence": _candidate_ci_evidence(root),
        "_selection_state": {
            "mode": "blocked" if exit_code else "current",
            "candidate_errors": errors, "candidate_degradations": degradations,
            "final_errors": errors, "final_degradations": degradations,
        },
    }
    artifact = _bounded_selection_diagnostics(diagnostics, max_bytes=SELECTION_DIAGNOSTICS_MAX_BYTES)
    _atomic_write(path, pretty_json_bytes(artifact))
    _emit_diagnostic_findings(diagnostics)
    print(f"Current CI core validation: exit={exit_code}, errors={len(errors)}, degradations={len(degradations)}")


def _safe_collector_details(value: object) -> dict[str, Any]:
    """Keep small diagnostic primitives; command output is never trusted."""
    if not isinstance(value, Mapping):
        return {}
    remaining = [256]

    def safe(raw: object, depth: int) -> Any:
        if remaining[0] <= 0:
            return None
        remaining[0] -= 1
        if isinstance(raw, bool) or raw is None:
            return raw
        if isinstance(raw, int) and not isinstance(raw, bool):
            return raw
        if isinstance(raw, float):
            return round(raw, 3)
        if isinstance(raw, str):
            return _safe_detail_text(raw)
        if isinstance(raw, Mapping) and depth < 4:
            nested = {}
            for raw_key, raw_value in list(raw.items())[:64]:
                key = _bounded_text(raw_key, limit=64)
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", key):
                    continue
                nested[key] = safe(raw_value, depth + 1)
                if remaining[0] <= 0:
                    break
            return nested
        return None

    return safe(value, 0) or {}


def _normalize_collector_failure(
    value: object, *, allowed_surfaces: frozenset[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("collector failure records must be JSON objects")
    if value.get("schema_version") != COLLECTOR_FAILURE_SCHEMA_VERSION:
        raise ValueError("collector failure record has an unsupported schema_version")
    surface = _bounded_text(value.get("surface"), limit=64)
    if surface not in (allowed_surfaces if allowed_surfaces is not None else SURFACE_SPECS):
        raise ValueError(f"collector failure references unknown surface {surface!r}")
    collector = _bounded_text(value.get("collector"), limit=160)
    step = _bounded_text(value.get("step"), limit=200)
    reason_class = _bounded_text(value.get("reason_class"), limit=64)
    if not collector or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", collector):
        raise ValueError("collector failure has an invalid collector identity")
    if not step:
        raise ValueError("collector failure has an empty workflow step")
    if reason_class not in COLLECTOR_REASON_CLASSES:
        raise ValueError(
            f"collector failure has unsupported reason_class {reason_class!r}"
        )
    exit_code = value.get("exit_code")
    if (
        not isinstance(exit_code, int)
        or isinstance(exit_code, bool)
        or exit_code < 1
        or exit_code > 255
    ):
        raise ValueError("collector failure has an invalid exit_code")
    return {
        "schema_version": COLLECTOR_FAILURE_SCHEMA_VERSION,
        "surface": surface,
        "collector": collector,
        "step": step,
        "reason_class": reason_class,
        "exit_code": exit_code,
        "details": _safe_collector_details(value.get("details")),
    }


def load_collector_failures(path: Path) -> list[dict[str, Any]]:
    """Load the workflow's bounded JSONL failure ledger."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"collector failure file is unreadable: {exc}") from exc
    records = []
    for line_number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"collector failure file line {line_number} is not valid JSON"
            ) from exc
        records.append(_normalize_collector_failure(value))
    return records


def _collector_failure_persistence_identity(record: Mapping[str, Any]) -> str:
    """Group one collector step across transient transport classifications.

    The incident finding still carries ``reason_class`` so its diagnostics and
    ticket fingerprint stay precise. Persistence is intentionally keyed to the
    producer step: one continuous outage can alternate between timeout,
    network, and transient HTTP symptoms without becoming a series of false
    first observations.
    """
    source = "\n".join(
        str(record.get(key) or "")
        for key in ("surface", "collector", "step")
    )
    return hashlib.sha256(source.encode()).hexdigest()[:20]


def _collector_failure_is_alertable(
    record: Mapping[str, Any],
    persistence_runs: int,
) -> bool:
    """Return whether one typed collector failure has crossed its threshold."""
    return (
        record.get("reason_class") not in TRANSIENT_COLLECTOR_REASONS
        or persistence_runs >= TRANSIENT_ALERT_PERSISTENCE_RUNS
    )


def _collector_incident_policy(
    records: list[dict[str, Any]],
    previous: dict | None,
    *,
    has_untyped_forced_surface: bool,
) -> tuple[dict[str, Any], dict[str, int]]:
    previous_streaks = (previous or {}).get("collector_failure_streaks") or {}
    if not isinstance(previous_streaks, dict):
        previous_streaks = {}
    streaks: dict[str, int] = {}
    immediate = has_untyped_forced_surface
    persisted = False
    transient = False
    for record in records:
        identity = _collector_failure_persistence_identity(record)
        prior = previous_streaks.get(identity)
        prior_count = prior if isinstance(prior, int) and prior > 0 else 0
        streaks[identity] = max(streaks.get(identity, 0), prior_count + 1)
        reason = record["reason_class"]
        if reason in TRANSIENT_COLLECTOR_REASONS:
            transient = True
            if _collector_failure_is_alertable(record, streaks[identity]):
                persisted = True
        else:
            immediate = True
    alert = bool(immediate or persisted)
    if immediate:
        reason = "deterministic-or-unclassified-collector-failure"
    elif persisted:
        reason = "transient-collector-failure-persisted"
    elif transient:
        reason = "transient-collector-failure-first-observation"
    else:
        reason = "no-collector-failure"
    return ({
        "alert": alert,
        "reason": reason,
        "transient_persistence_runs_required": TRANSIENT_ALERT_PERSISTENCE_RUNS,
        "max_observed_streak": max(streaks.values(), default=0),
    }, streaks)


def _compose_incident_policy(
    preserved: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Combine an attempted refresh with incidents carried from other surfaces.

    A refresh-only run must not advance unattempted incident streaks, so it
    cannot simply feed preserved failures back through ``_collector_incident_policy``.
    It also must not let a first transient failure on the attempted surface
    silence an already-alertable preserved incident. Select the most relevant
    policy record while independently retaining the strongest alert and streak.
    """
    current_policy = copy.deepcopy(dict(current))
    if not isinstance(preserved, Mapping):
        return current_policy

    preserved_policy = copy.deepcopy(dict(preserved))
    preserved_alert = preserved_policy.get("alert", True) is not False
    current_alert = current_policy.get("alert", True) is not False
    if preserved_alert and not current_alert:
        composed = preserved_policy
    else:
        # A newly alertable target adds severity. When neither policy alerts,
        # the attempted target's reason is the useful current diagnostic.
        composed = current_policy
    preserved_streak = preserved_policy.get("max_observed_streak")
    current_streak = current_policy.get("max_observed_streak")
    composed["alert"] = preserved_alert or current_alert
    composed["max_observed_streak"] = max(
        preserved_streak
        if isinstance(preserved_streak, int) and not isinstance(preserved_streak, bool)
        else 0,
        current_streak
        if isinstance(current_streak, int) and not isinstance(current_streak, bool)
        else 0,
    )
    return composed


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _test_signal_build_number(section: Mapping[str, Any]) -> int | None:
    for key in ("latest_test_signal_build", "latest_build"):
        row = section.get(key)
        if not isinstance(row, Mapping):
            continue
        number = _positive_int(row.get("build_number") or row.get("number"))
        if number is not None:
            return number
    return None


def _load_candidate_json_object(root: Path, relative: str) -> dict | None:
    try:
        value = json.loads((root / relative).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_baseline_json_object(
    root: Path,
    baseline_ref: str,
    relative: str,
) -> dict | None:
    try:
        value = json.loads(_run_git(root, "show", f"{baseline_ref}:{relative}"))
    except (subprocess.CalledProcessError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _active_upstream_retry_observations(
    root: Path,
    baseline_ref: str,
) -> list[dict[str, Any]]:
    """Attest terminal-to-provisional retry transitions from collector output.

    A newer in-progress nightly is routine and is deliberately ignored here.
    The transition is classified only when the collector explicitly saw an
    active retry on the exact build that the immutable baseline had already
    published as complete test evidence, and the candidate signal retreated to
    an older build as a result.
    """
    candidate = _load_candidate_json_object(root, CI_HEALTH_PATH)
    baseline = _load_baseline_json_object(root, baseline_ref, CI_HEALTH_PATH)
    if candidate is None or baseline is None:
        return []

    observations: list[dict[str, Any]] = []
    for pipeline in ("amd", "upstream"):
        candidate_section = candidate.get(pipeline)
        baseline_section = baseline.get(pipeline)
        if not isinstance(candidate_section, Mapping) or not isinstance(
            baseline_section, Mapping
        ):
            continue
        candidate_head = candidate_section.get("latest_pipeline_build")
        baseline_head = baseline_section.get("latest_pipeline_build")
        if not isinstance(candidate_head, Mapping) or not isinstance(
            baseline_head, Mapping
        ):
            continue
        candidate_number = _positive_int(
            candidate_head.get("build_number") or candidate_head.get("number")
        )
        baseline_number = _positive_int(
            baseline_head.get("build_number") or baseline_head.get("number")
        )
        candidate_signal = _test_signal_build_number(candidate_section)
        baseline_signal = _test_signal_build_number(baseline_section)
        if (
            candidate_head.get("active_retry") is not True
            or baseline_head.get("active_retry") is True
            or candidate_number is None
            or candidate_number != baseline_number
            or baseline_signal != candidate_number
            or candidate_signal is None
            or candidate_signal >= candidate_number
        ):
            continue
        observations.append({
            "kind": "published-build-active-retry",
            "surface": "ci_core",
            "pipeline": pipeline,
            "build_number": candidate_number,
            "candidate_test_signal_build_number": candidate_signal,
        })
    return observations


def _upstream_retry_identity(observation: Mapping[str, Any]) -> str:
    source = "\n".join(
        str(observation.get(key) or "")
        for key in ("kind", "surface", "pipeline", "build_number")
    )
    return hashlib.sha256(source.encode()).hexdigest()[:20]


def _audit_active_retry_candidate_transaction(audit: DashboardAudit) -> bool:
    """Audit retry-owned source semantics before either surface is restored."""
    findings = audit.report.findings
    initial_count = len(findings)
    for method_name in UPSTREAM_RETRY_CANDIDATE_AUDITS:
        getattr(audit, method_name)()
    prove_completed = getattr(audit, "audit_current_completed_ci_transaction", None)
    completed_proof = prove_completed() if callable(prove_completed) else False

    existing = {
        (
            finding.severity,
            finding.code,
            finding.message,
            finding.path,
            json.dumps(finding.context, sort_keys=True, default=str),
        )
        for finding in findings[:initial_count]
    }
    semantic_findings = findings[initial_count:]
    del findings[initial_count:]
    for finding in semantic_findings:
        identity = (
            finding.severity,
            finding.code,
            finding.message,
            finding.path,
            json.dumps(finding.context, sort_keys=True, default=str),
        )
        if identity in existing:
            continue
        existing.add(identity)
        finding.context = {
            **finding.context,
            "publication_phase": UPSTREAM_RETRY_CANDIDATE_PHASE,
        }
        findings.append(finding)
    return completed_proof is True and not any(
        finding.severity == "error" and (
            not finding_surfaces(finding)
            or finding_surfaces(finding) & UPSTREAM_RETRY_TRANSACTION_SURFACES
        ) for finding in findings
    )


def _upstream_retry_incident_policy(
    observations: list[dict[str, Any]],
    previous: dict | None,
) -> tuple[dict[str, Any], dict[str, int]]:
    previous_streaks = (previous or {}).get("upstream_retry_streaks") or {}
    if not isinstance(previous_streaks, dict):
        previous_streaks = {}
    streaks: dict[str, int] = {}
    for observation in observations:
        identity = _upstream_retry_identity(observation)
        prior = previous_streaks.get(identity)
        prior_count = prior if isinstance(prior, int) and prior > 0 else 0
        streaks[identity] = max(streaks.get(identity, 0), prior_count + 1)
    max_streak = max(streaks.values(), default=0)
    alert = max_streak >= UPSTREAM_RETRY_ALERT_PERSISTENCE_RUNS
    if alert:
        reason = "active-upstream-retry-persisted"
    elif observations:
        reason = "active-upstream-retry-first-observation"
    else:
        reason = "no-active-upstream-retry"
    return ({
        "alert": alert,
        "reason": reason,
        "transient_persistence_runs_required": (
            UPSTREAM_RETRY_ALERT_PERSISTENCE_RUNS
        ),
        "max_observed_streak": max_streak,
    }, streaks)


def _retry_reconciliation_finding(
    record: Mapping[str, Any],
    pipelines: set[str],
) -> bool:
    code = str(record.get("code") or "")
    surfaces = {
        str(surface)
        for surface in (record.get("surfaces") or [])
        if str(surface)
    }
    if not surfaces or not surfaces <= {"ci_core", "ci_analytics"}:
        return False
    context = record.get("context")
    context = context if isinstance(context, Mapping) else {}
    # A mismatch already present while both candidate surfaces still describe
    # the retry generation is a real defect, not restore-induced skew.
    if context.get("publication_phase") == UPSTREAM_RETRY_CANDIDATE_PHASE:
        return False
    if code == UPSTREAM_RETRY_FINDING_CODE:
        return context.get("pipeline") in pipelines
    if code == "operations-stale-source":
        return context.get("source") in {
            f"{pipeline}_test_signal" for pipeline in pipelines
        }
    return (
        code in UPSTREAM_RETRY_RECONCILIATION_CODES
        and context.get("pipeline") in pipelines
    )


def _apply_upstream_retry_reporting_policy(
    state: dict,
    observations: list[dict[str, Any]],
    policy: dict[str, Any],
    *,
    forced: set[str],
) -> None:
    """Debounce only a fully reconciled, collector-attested retry episode."""
    records = [
        record
        for key in (
            "candidate_errors",
            "candidate_degradations",
            "final_errors",
            "final_degradations",
        )
        for record in (state.get(key) or [])
        if isinstance(record, dict)
    ]
    pipelines = {
        str(observation.get("pipeline") or "")
        for observation in observations
        if observation.get("pipeline")
    }
    fallback = set(state.get("fallback_surfaces") or [])
    eligible = bool(
        observations
        and not forced
        and state.get("mode") != "blocked"
        and not state.get("final_errors")
        and fallback
        and "ci_core" in fallback
        and fallback <= {"ci_core", "ci_analytics"}
        and records
        and all(_retry_reconciliation_finding(record, pipelines) for record in records)
    )
    if not eligible:
        return

    alertable = policy.get("alert", True) is not False
    max_streak = int(policy.get("max_observed_streak") or 0)
    for record in records:
        context = record.get("context")
        context = dict(context) if isinstance(context, Mapping) else {}
        context.update({
            "alertable": alertable,
            "persistence_runs": max_streak,
            "transient_reason": "active-upstream-retry-reconciliation",
        })
        record["context"] = context
    state["incident_policy"] = {
        **policy,
        "source": "active-upstream-retry-reconciliation",
    }


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


def _changed_worktree_paths(root: Path, ref: str) -> set[str]:
    tracked = _run_git(
        root,
        "diff",
        "--name-only",
        ref,
        "--",
    ).decode().splitlines()
    untracked = _run_git(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
    ).decode().splitlines()
    return {path.strip() for path in [*tracked, *untracked] if path.strip()}


def _candidate_generated_roots(root: Path, candidate_code_ref: str) -> tuple[str, ...]:
    """Read the generated-domain boundary from immutable candidate code."""
    try:
        payload = json.loads(
            _run_git(
                root,
                "show",
                f"{candidate_code_ref}:config/dashboard_state.json",
            )
        )
    except (json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "candidate code ref has no valid dashboard-state policy"
        ) from exc
    roots = payload.get("generated_roots") if isinstance(payload, dict) else None
    if (
        not isinstance(roots, list)
        or not roots
        or any(
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or "/" in path
            or path in {".", ".."}
            for path in roots
        )
        or len(set(roots)) != len(roots)
    ):
        raise RuntimeError(
            "candidate code ref declares invalid dashboard generated roots"
        )
    return tuple(roots)


def _is_generated_path(relative: str, generated_roots: tuple[str, ...]) -> bool:
    return any(
        relative == generated_root or relative.startswith(generated_root + "/")
        for generated_root in generated_roots
    )


def _worktree_path_matches_ref(root: Path, ref: str, relative: str) -> bool:
    """Compare one generated path to a ref, including untracked state files."""
    raw_entry = _run_git(root, "ls-tree", "-z", ref, "--", relative)
    path = root / relative
    if not raw_entry:
        return not path.exists() and not path.is_symlink()
    entries = [entry for entry in raw_entry.rstrip(b"\0").split(b"\0") if entry]
    if len(entries) != 1:
        return False
    try:
        metadata, raw_relative = entries[0].split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split()
        entry_relative = raw_relative.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return False
    if entry_relative != relative or object_type != "blob":
        return False
    expected = _run_git(root, "cat-file", "blob", object_id)
    if mode == "120000":
        if not path.is_symlink():
            return False
        return os.fsencode(os.readlink(path)) == expected
    if path.is_symlink() or not path.is_file():
        return False
    executable = bool(path.stat().st_mode & 0o100)
    return path.read_bytes() == expected and executable == (mode == "100755")


def _validate_refresh_only_candidate(
    root: Path,
    baseline_ref: str,
    refresh_surface: str,
    candidate_code_ref: str | None = None,
) -> None:
    """Fail closed unless the pre-selector tree changed only one source surface."""
    if candidate_code_ref is None:
        changed = sorted(_changed_worktree_paths(root, baseline_ref))
    else:
        generated_roots = _candidate_generated_roots(root, candidate_code_ref)
        baseline_changes = _changed_worktree_paths(root, baseline_ref)
        candidate_changes = _changed_worktree_paths(root, candidate_code_ref)
        changed = sorted({
            relative
            for relative in baseline_changes | candidate_changes
            if (
                _is_generated_path(relative, generated_roots)
                and relative in baseline_changes
                and not _worktree_path_matches_ref(root, baseline_ref, relative)
            )
            or (
                not _is_generated_path(relative, generated_roots)
                and relative in candidate_changes
            )
        })
    unexpected = []
    for relative in changed:
        owners = [
            surface
            for surface, spec in SURFACE_SPECS.items()
            if relative in {*spec.required_paths, *spec.optional_paths}
            or any(Path(relative).match(pattern) for pattern in spec.globs)
        ]
        if owners != [refresh_surface]:
            unexpected.append(relative)
    if unexpected:
        raise RuntimeError(
            f"refresh-only {refresh_surface} candidate changed non-target paths: "
            f"{unexpected}"
        )


def _uses_declared_surface_domain() -> bool:
    """Distinguish production surface rules from isolated test-local specs."""
    return set(SURFACE_SPECS) == DECLARED_SURFACE_NAMES


def _legacy_aliases() -> dict[str, frozenset[str]]:
    if not _uses_declared_surface_domain():
        return {}
    return LEGACY_SURFACE_ALIASES


def _closed_fallback_surfaces(surfaces: Iterable[str]) -> set[str]:
    requested = set(surfaces)
    if not _uses_declared_surface_domain():
        return requested
    retired = requested & RETIRED_SURFACES
    return set(fallback_dependency_closure(requested - retired)) | retired


def _surface_expansions(
    surfaces: Iterable[str],
    aliases: dict[str, frozenset[str]],
) -> dict[str, frozenset[str]]:
    expansions: dict[str, frozenset[str]] = {}
    expanded: set[str] = set()
    for surface in surfaces:
        targets = aliases.get(surface, frozenset({surface}))
        if not targets or not set(targets) <= set(_historical_surface_specs()):
            raise RuntimeError(
                f"validated baseline publication surface {surface!r} cannot be migrated"
            )
        overlap = expanded & set(targets)
        if overlap:
            raise RuntimeError(
                "validated baseline publication aliases overlap active surfaces: "
                f"{sorted(overlap)}"
            )
        expansions[surface] = targets
        expanded.update(targets)
    return expansions


def _spec_owns_path(spec: SurfaceSpec, relative: str) -> bool:
    return relative in {*spec.required_paths, *spec.optional_paths} or any(
        Path(relative).match(pattern) for pattern in spec.globs
    )


def _baseline_expected_paths(root: Path, ref: str, spec: SurfaceSpec) -> set[str]:
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
    return expected


def _validate_baseline_manifest(
    root: Path,
    ref: str,
    surface: str,
    spec: SurfaceSpec,
    entries: object,
) -> dict[str, dict]:
    if not isinstance(entries, dict):
        raise RuntimeError(f"fallback baseline manifest for {surface} is invalid")
    ignored = ignored_watcher_state_paths(surface)
    migrated_entries = {
        relative: descriptor
        for relative, descriptor in entries.items()
        if relative not in ignored
    }
    expected = _baseline_expected_paths(root, ref, spec) - ignored
    if set(migrated_entries) != expected:
        raise RuntimeError(
            f"fallback baseline manifest path set for {surface} is inconsistent"
        )
    for relative, descriptor in migrated_entries.items():
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
    return migrated_entries


def _migrated_restored_paths(
    surface: str,
    paths: object,
) -> list[str] | None:
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        return None
    ignored = ignored_watcher_state_paths(surface)
    return sorted(path for path in paths if path not in ignored)


def _partition_baseline_manifest(
    root: Path,
    ref: str,
    manifest: dict[str, dict],
    expansions: dict[str, frozenset[str]],
) -> dict[str, dict]:
    """Partition only after each legacy transaction was validated in full."""
    partitioned: dict[str, dict] = {}
    for surface, targets in expansions.items():
        entries = manifest[surface]
        if targets == frozenset({surface}):
            partitioned[surface] = dict(entries)
            continue
        child_entries = {target: {} for target in targets}
        for relative, descriptor in entries.items():
            owners = [
                target
                for target in targets
                if _spec_owns_path(_historical_surface_specs()[target], relative)
            ]
            if len(owners) != 1:
                raise RuntimeError(
                    "legacy fallback manifest path does not have one active owner: "
                    f"{relative}"
                )
            child_entries[owners[0]][relative] = descriptor
        for target, target_entries in child_entries.items():
            expected = _baseline_expected_paths(root, ref, _historical_surface_specs()[target])
            if set(target_entries) != expected:
                raise RuntimeError(
                    f"legacy fallback manifest partition for {target} is inconsistent"
                )
            partitioned[target] = target_entries
    return partitioned


def _pre_analytics_expansion(surface: str) -> frozenset[str]:
    if surface == "ci_core":
        return frozenset({"ci_core", "ci_analytics", "ci_gating"})
    return frozenset({surface})


def _historical_surface_specs() -> dict[str, SurfaceSpec]:
    return (
        PRE_VIEW_RETIREMENT_SURFACE_SPECS
        if _uses_declared_surface_domain() else SURFACE_SPECS
    )


def _pre_queue_split_surface_names() -> set[str]:
    """Return the exact active surface domain used by contract v4."""
    return set(_historical_surface_specs()) - set(QUEUE_COMPANION_SURFACES)


def _pre_queue_split_spec(surface: str) -> SurfaceSpec:
    """Resolve one v4 surface without trusting the narrower v5 queue spec."""
    if surface == QUEUE_LIVE_SURFACE:
        return PRE_QUEUE_SPLIT_SURFACE_SPEC
    return _historical_surface_specs()[surface]


def _queue_split_expansion(surface: str) -> frozenset[str]:
    if surface == QUEUE_LIVE_SURFACE:
        return QUEUE_SPLIT_SURFACES
    return frozenset({surface})


def _expanded_earliest_clock(
    raw_since: Mapping[str, str],
    surfaces: Iterable[str],
) -> dict[str, str]:
    expanded: dict[str, str] = {}
    for surface in surfaces:
        value = raw_since[surface]
        for target in _pre_analytics_expansion(surface):
            current = expanded.get(target)
            if current is None or (_parse_utc(value) or datetime.max.replace(
                tzinfo=timezone.utc
            )) < (_parse_utc(current) or datetime.max.replace(tzinfo=timezone.utc)):
                expanded[target] = value
    return expanded


def _baseline_descriptor(root: Path, ref: str, relative: str) -> dict[str, Any]:
    try:
        payload = _run_git(root, "show", f"{ref}:{relative}")
    except subprocess.CalledProcessError:
        raise RuntimeError(
            f"validated baseline is missing migration path {relative}"
        ) from None
    return {
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _migrate_pre_analytics_v2_state(
    root: Path,
    ref: str,
    payload: dict,
) -> dict:
    """Validate and split the pre-ci_analytics schema-v2 transaction."""
    mode = payload.get("mode")
    degraded = payload.get("degraded_surfaces")
    fresh = payload.get("fresh_degraded_surfaces")
    fallback = payload.get("fallback_surfaces")
    degraded_since = payload.get("degraded_since")
    fallback_since = payload.get("fallback_since")
    allowed = _pre_queue_split_surface_names()
    if (
        mode not in {"current", "degraded", "fallback", "mixed"}
        or not isinstance(degraded, list)
        or not isinstance(fresh, list)
        or not isinstance(fallback, list)
        or any(
            not isinstance(surface, str) or surface not in allowed
            for surface in [*degraded, *fresh, *fallback]
        )
        or len(set(degraded)) != len(degraded)
        or len(set(fresh)) != len(fresh)
        or len(set(fallback)) != len(fallback)
        or set(fresh) & set(fallback)
        or set(degraded) != set(fresh) | set(fallback)
        or not isinstance(degraded_since, dict)
        or set(degraded_since) != set(degraded)
        or any(_parse_utc(value) is None for value in degraded_since.values())
        or not isinstance(fallback_since, dict)
        or set(fallback_since) != set(fallback)
        or any(_parse_utc(value) is None for value in fallback_since.values())
        or mode != _publication_mode(set(fresh), set(fallback))
        or ("ci_core" in fallback and "ci_gating" not in fallback)
    ):
        raise RuntimeError("validated baseline publication state is inconsistent")

    manifest = payload.get("restored_manifest")
    restored_paths = payload.get("restored_paths")
    if not fallback:
        if manifest not in (None, {}) or restored_paths not in (None, {}):
            raise RuntimeError("non-fallback baseline state declares restored content")
        expanded_fresh_clock = _expanded_earliest_clock(
            degraded_since, fresh
        )
        return {
            **payload,
            "surface_contract_version": PRE_QUEUE_SPLIT_SURFACE_CONTRACT_VERSION,
            "mode": _publication_mode(set(expanded_fresh_clock), set()),
            "degraded_surfaces": sorted(expanded_fresh_clock),
            "fresh_degraded_surfaces": sorted(expanded_fresh_clock),
            "fallback_surfaces": [],
            "degraded_since": expanded_fresh_clock,
            "fallback_since": {},
            "restored_paths": {},
            "restored_manifest": {},
        }
    if not isinstance(manifest, dict) or set(manifest) != set(fallback):
        raise RuntimeError("fallback baseline state has an incomplete restore manifest")
    if restored_paths is not None and (
        not isinstance(restored_paths, dict) or set(restored_paths) != set(fallback)
    ):
        raise RuntimeError("fallback baseline state has incomplete restored paths")

    validated: dict[str, dict] = {}
    for surface in fallback:
        if surface == "ci_core":
            spec = PRE_ANALYTICS_CI_CORE_SURFACE_SPEC
        elif surface == "ci_gating":
            spec = PRE_ANALYTICS_CI_GATING_SURFACE_SPEC
        else:
            spec = _pre_queue_split_spec(surface)
        entries = _validate_baseline_manifest(
            root, ref, surface, spec, manifest[surface]
        )
        if restored_paths is not None and _migrated_restored_paths(
            surface, restored_paths.get(surface)
        ) != sorted(entries):
            raise RuntimeError(
                f"fallback baseline restored paths for {surface} are inconsistent"
            )
        validated[surface] = entries

    expanded_fallback = {
        target for surface in fallback for target in _pre_analytics_expansion(surface)
    }
    partitioned = {surface: {} for surface in expanded_fallback}
    for old_surface, entries in validated.items():
        targets = _pre_analytics_expansion(old_surface)
        for relative, descriptor in entries.items():
            owners = [
                target
                for target in targets
                if _spec_owns_path(_pre_queue_split_spec(target), relative)
            ]
            if len(owners) != 1:
                raise RuntimeError(
                    "pre-analytics fallback path does not have one active owner: "
                    f"{relative}"
                )
            owner = owners[0]
            if relative in partitioned[owner]:
                raise RuntimeError(
                    f"pre-analytics fallback path is duplicated: {relative}"
                )
            partitioned[owner][relative] = descriptor

    for surface, entries in partitioned.items():
        expected = _baseline_expected_paths(
            root,
            ref,
            _pre_queue_split_spec(surface),
        )
        missing = expected - set(entries)
        allowed_missing = set()
        if surface == "ci_gating":
            allowed_missing = missing & {"data/vllm/ci/gating_nightlies.json"}
        if missing != allowed_missing:
            raise RuntimeError(
                f"pre-analytics fallback partition for {surface} is inconsistent"
            )
        for relative in sorted(allowed_missing):
            entries[relative] = _baseline_descriptor(root, ref, relative)
        if set(entries) != expected:
            raise RuntimeError(
                f"pre-analytics fallback partition for {surface} is inconsistent"
            )

    expanded_degraded_clock = _expanded_earliest_clock(
        degraded_since, degraded
    )
    expanded_fallback_clock = _expanded_earliest_clock(
        fallback_since, fallback
    )
    expanded_fresh = set(expanded_degraded_clock) - set(expanded_fallback_clock)
    return {
        **payload,
        "surface_contract_version": PRE_QUEUE_SPLIT_SURFACE_CONTRACT_VERSION,
        "mode": _publication_mode(expanded_fresh, set(expanded_fallback_clock)),
        "degraded_surfaces": sorted(expanded_degraded_clock),
        "fresh_degraded_surfaces": sorted(expanded_fresh),
        "fallback_surfaces": sorted(expanded_fallback_clock),
        "degraded_since": expanded_degraded_clock,
        "fallback_since": expanded_fallback_clock,
        "restored_paths": {
            surface: sorted(entries) for surface, entries in partitioned.items()
        },
        "restored_manifest": partitioned,
    }


def _migrate_pre_queue_split_v2_state(
    root: Path,
    ref: str,
    payload: dict,
) -> dict:
    """Validate and partition a contract-v4 monolithic queue transaction."""
    if (
        payload.get("schema_version") != 2
        or payload.get("surface_contract_version")
        != PRE_QUEUE_SPLIT_SURFACE_CONTRACT_VERSION
    ):
        raise RuntimeError(
            "validated baseline does not use the pre-queue-split surface contract"
        )

    mode = payload.get("mode")
    degraded = payload.get("degraded_surfaces")
    fresh = payload.get("fresh_degraded_surfaces")
    fallback = payload.get("fallback_surfaces")
    degraded_since = payload.get("degraded_since")
    fallback_since = payload.get("fallback_since")
    allowed = _pre_queue_split_surface_names()
    if (
        mode not in {"current", "degraded", "fallback", "mixed"}
        or not isinstance(degraded, list)
        or not isinstance(fresh, list)
        or not isinstance(fallback, list)
        or any(
            not isinstance(surface, str) or surface not in allowed
            for surface in [*degraded, *fresh, *fallback]
        )
        or len(set(degraded)) != len(degraded)
        or len(set(fresh)) != len(fresh)
        or len(set(fallback)) != len(fallback)
        or set(fresh) & set(fallback)
        or set(degraded) != set(fresh) | set(fallback)
        or not isinstance(degraded_since, dict)
        or set(degraded_since) != set(degraded)
        or any(_parse_utc(value) is None for value in degraded_since.values())
        or not isinstance(fallback_since, dict)
        or set(fallback_since) != set(fallback)
        or any(_parse_utc(value) is None for value in fallback_since.values())
        or mode != _publication_mode(set(fresh), set(fallback))
    ):
        raise RuntimeError("validated baseline publication state is inconsistent")

    manifest = payload.get("restored_manifest")
    restored_paths = payload.get("restored_paths")
    if not fallback:
        if manifest not in (None, {}) or restored_paths not in (None, {}):
            raise RuntimeError(
                "non-fallback baseline state declares restored content"
            )
        partitioned: dict[str, dict] = {}
    else:
        if not isinstance(manifest, dict) or set(manifest) != set(fallback):
            raise RuntimeError(
                "fallback baseline state has an incomplete restore manifest"
            )
        if restored_paths is not None and (
            not isinstance(restored_paths, dict)
            or set(restored_paths) != set(fallback)
        ):
            raise RuntimeError(
                "fallback baseline state has incomplete restored paths"
            )

        validated: dict[str, dict] = {}
        for surface in fallback:
            entries = _validate_baseline_manifest(
                root,
                ref,
                surface,
                _pre_queue_split_spec(surface),
                manifest[surface],
            )
            if restored_paths is not None and _migrated_restored_paths(
                surface,
                restored_paths.get(surface),
            ) != sorted(entries):
                raise RuntimeError(
                    f"fallback baseline restored paths for {surface} are inconsistent"
                )
            validated[surface] = entries

        expansions = {
            surface: _queue_split_expansion(surface)
            for surface in fallback
        }
        partitioned = _partition_baseline_manifest(
            root,
            ref,
            validated,
            expansions,
        )

    degraded_expansions = {
        surface: _queue_split_expansion(surface)
        for surface in degraded
    }
    fallback_expansions = {
        surface: _queue_split_expansion(surface)
        for surface in fallback
    }
    expanded_degraded_clock = _expand_clock(
        degraded_since,
        degraded_expansions,
    )
    expanded_fallback_clock = _expand_clock(
        fallback_since,
        fallback_expansions,
    )
    expanded_fallback = set(expanded_fallback_clock)
    if _closed_fallback_surfaces(expanded_fallback) != expanded_fallback:
        raise RuntimeError(
            "validated baseline fallback omits a required dependent surface"
        )
    expanded_fresh = set(expanded_degraded_clock) - expanded_fallback
    return {
        **payload,
        "surface_contract_version": PRE_VIEW_RETIREMENT_SURFACE_CONTRACT_VERSION,
        "mode": _publication_mode(expanded_fresh, expanded_fallback),
        "degraded_surfaces": sorted(expanded_degraded_clock),
        "fresh_degraded_surfaces": sorted(expanded_fresh),
        "fallback_surfaces": sorted(expanded_fallback),
        "degraded_since": expanded_degraded_clock,
        "fallback_since": expanded_fallback_clock,
        "restored_paths": {
            surface: sorted(entries)
            for surface, entries in partitioned.items()
        },
        "restored_manifest": partitioned,
    }


def _expand_clock(
    raw_since: dict[str, str],
    expansions: dict[str, frozenset[str]],
) -> dict[str, str]:
    return {
        target: raw_since[surface]
        for surface, targets in expansions.items()
        for target in targets
    }


def _migrate_retired_view_state(root: Path, ref: str, payload: dict) -> dict:
    """Validate the full v5 proof before dropping retired producer domains."""
    allowed = set(_historical_surface_specs())
    lanes: dict[str, set[str]] = {}
    for field in ("degraded_surfaces", "fresh_degraded_surfaces", "fallback_surfaces"):
        raw = payload.get(field)
        if (
            not isinstance(raw, list)
            or any(not isinstance(value, str) or value not in allowed for value in raw)
            or len(raw) != len(set(raw))
        ):
            raise RuntimeError("validated baseline publication state is inconsistent")
        lanes[field] = set(raw)
    fresh = lanes["fresh_degraded_surfaces"]
    fallback = lanes["fallback_surfaces"]
    degraded = lanes["degraded_surfaces"]
    clocks = payload.get("degraded_since")
    fallback_clocks = payload.get("fallback_since")
    if (
        fresh & fallback
        or fresh | fallback != degraded
        or payload.get("mode") != _publication_mode(fresh, fallback)
        or not isinstance(clocks, dict)
        or set(clocks) != degraded
        or any(_parse_utc(value) is None for value in clocks.values())
        or not isinstance(fallback_clocks, dict)
        or set(fallback_clocks) != fallback
        or any(_parse_utc(value) is None for value in fallback_clocks.values())
    ):
        raise RuntimeError("validated baseline publication state is inconsistent")
    manifest = payload.get("restored_manifest")
    paths = payload.get("restored_paths")
    validated: dict[str, dict] = {}
    if fallback:
        if not isinstance(manifest, dict) or set(manifest) != fallback:
            raise RuntimeError("fallback baseline state has an incomplete restore manifest")
        if paths is not None and (not isinstance(paths, dict) or set(paths) != fallback):
            raise RuntimeError("fallback baseline state has incomplete restored paths")
        for surface in sorted(fallback):
            entries = _validate_baseline_manifest(
                root, ref, surface, _historical_surface_specs()[surface],
                manifest[surface],
            )
            validated[surface] = entries
            if paths is not None and _migrated_restored_paths(
                surface, paths[surface]
            ) != sorted(entries):
                raise RuntimeError("fallback baseline restored paths are inconsistent")
    elif manifest not in (None, {}) or paths not in (None, {}):
        raise RuntimeError("non-fallback baseline state declares restored content")
    records = payload.get("collector_failures", [])
    if not isinstance(records, list):
        raise RuntimeError("validated baseline collector failure evidence is inconsistent")
    try:
        failures = [
            _normalize_collector_failure(row, allowed_surfaces=frozenset(allowed))
            for row in records
        ]
    except ValueError as exc:
        raise RuntimeError("validated baseline collector failure evidence is inconsistent") from exc
    if any(row["surface"] not in fallback for row in failures):
        raise RuntimeError("validated baseline collector failure evidence is inconsistent")
    fresh -= RETIRED_SURFACES
    fallback -= RETIRED_SURFACES
    degraded = fresh | fallback
    retained_failures = [row for row in failures if row["surface"] in fallback]
    old_streaks = payload.get("collector_failure_streaks") or {}
    if not isinstance(old_streaks, dict):
        raise RuntimeError("validated baseline collector streak evidence is inconsistent")
    # Migration does not represent another collection attempt. Recompute the
    # incident from surviving evidence without incrementing its persistence.
    prior_counts = {}
    for row in retained_failures:
        identity = _collector_failure_persistence_identity(row)
        count = old_streaks.get(identity, 1)
        if type(count) is not int or count <= 0:
            raise RuntimeError("validated baseline collector streak evidence is inconsistent")
        prior_counts[identity] = count - 1
    collector_policy, streaks = _collector_incident_policy(
        retained_failures, {"collector_failure_streaks": prior_counts},
        has_untyped_forced_surface=bool(fallback - {row["surface"] for row in retained_failures}),
    )
    diagnostics = {}
    for key in ("candidate_errors", "candidate_degradations", "final_errors", "final_degradations"):
        rows = payload.get(key) or []
        if not isinstance(rows, list):
            raise RuntimeError("validated baseline diagnostic evidence is inconsistent")
        retained = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("surfaces"), list):
                raise RuntimeError("validated baseline diagnostic evidence is inconsistent")
            raw_surfaces = row["surfaces"]
            if any(not isinstance(name, str) or name not in allowed for name in raw_surfaces):
                raise RuntimeError("validated baseline diagnostic evidence is inconsistent")
            active = set(raw_surfaces) & degraded
            if active:
                retained.append({**row, "surfaces": sorted(active)})
        diagnostics[key] = retained
    # Recompose escalation from surviving evidence. A retired deterministic
    # failure must not escalate an unrelated first transient collector failure.
    incident_policy = collector_policy
    if fresh or any(row.get("code") != "publication-collector-failed" for rows in diagnostics.values() for row in rows):
        incident_policy = _compose_incident_policy(collector_policy, {
            "alert": True, "reason": "surviving-publication-diagnostic",
        })
    result = {
        **payload,
        **diagnostics,
        "surface_contract_version": PRE_RUNTIME_PARITY_SURFACE_CONTRACT_VERSION,
        "mode": _publication_mode(fresh, fallback),
        "degraded_surfaces": sorted(degraded),
        "fresh_degraded_surfaces": sorted(fresh),
        "fallback_surfaces": sorted(fallback),
        "degraded_since": {surface: clocks[surface] for surface in sorted(degraded)},
        "fallback_since": {surface: fallback_clocks[surface] for surface in sorted(fallback)},
        "restored_manifest": {surface: validated[surface] for surface in sorted(fallback)},
        "restored_paths": {surface: sorted(validated[surface]) for surface in sorted(fallback)},
        "collector_failures": retained_failures,
        "collector_failure_streaks": streaks,
        "collector_incident_policy": collector_policy,
        "incident_policy": incident_policy,
    }
    observations = [row for row in (payload.get("upstream_retry_observations") or [])
                    if isinstance(row, dict) and row.get("surface") in fallback]
    old_retry_streaks = payload.get("upstream_retry_streaks") or {}
    retry_prior = {}
    for row in observations:
        identity = _upstream_retry_identity(row)
        count = old_retry_streaks.get(identity, row.get("persistence_runs", 1)) if isinstance(old_retry_streaks, dict) else 1
        if type(count) is not int or count <= 0:
            raise RuntimeError("validated baseline retry evidence is inconsistent")
        retry_prior[identity] = count - 1
    retry_policy, retry_streaks = _upstream_retry_incident_policy(
        observations, {"upstream_retry_streaks": retry_prior},
    )
    result["upstream_retry_observations"] = observations
    result["upstream_retry_streaks"] = retry_streaks
    _apply_upstream_retry_reporting_policy(result, observations, retry_policy,
                                          forced={row["surface"] for row in retained_failures})
    # Diagnostic row counts changed; the next bounded writer supplies a new
    # omission ledger if one is needed for this migrated publication.
    result.pop("publication_retention", None)
    return result


def _migrate_retired_runtime_parity_state(root: Path, ref: str, payload: dict) -> dict:
    """Verify the complete v6 proof before dropping retired runtime files."""
    return _migrate_retired_ci_paths(
        root, ref, payload,
        historical_specs=PRE_RUNTIME_PARITY_SURFACE_SPECS,
        target_specs=PRE_PARITY_OVERRIDES_SURFACE_SPECS,
        retired_paths=RETIRED_RUNTIME_PARITY_PATHS,
        target_version=PRE_PARITY_OVERRIDES_SURFACE_CONTRACT_VERSION,
    )


def _migrate_retired_parity_overrides_state(root: Path, ref: str, payload: dict) -> dict:
    """Verify every v7 optional path before retiring its runtime-parity override."""
    return _migrate_retired_ci_paths(
        root, ref, payload,
        historical_specs=PRE_PARITY_OVERRIDES_SURFACE_SPECS,
        target_specs=SURFACE_SPECS,
        retired_paths=RETIRED_PARITY_OVERRIDE_PATHS,
        target_version=SURFACE_CONTRACT_VERSION,
    )


def _migrate_retired_ci_paths(
    root: Path, ref: str, payload: dict, *,
    historical_specs: dict[str, SurfaceSpec], target_specs: dict[str, SurfaceSpec],
    retired_paths: frozenset[str], target_version: int,
) -> dict:
    """Trim a transaction only after validating its complete historical proof."""
    fallback = payload.get("fallback_surfaces")
    manifest = payload.get("restored_manifest")
    paths = payload.get("restored_paths")
    if (not isinstance(fallback, list) or any(not isinstance(surface, str) or surface not in SURFACE_SPECS for surface in fallback)
            or len(fallback) != len(set(fallback))):
        raise RuntimeError("validated baseline publication state is inconsistent")
    if not fallback:
        if manifest not in (None, {}) or paths not in (None, {}):
            raise RuntimeError("non-fallback baseline state declares restored content")
        return {**payload, "surface_contract_version": target_version}
    if not isinstance(manifest, dict) or set(manifest) != set(fallback):
        raise RuntimeError("fallback baseline state has an incomplete restore manifest")
    if paths is not None and (not isinstance(paths, dict) or set(paths) != set(fallback)):
        raise RuntimeError("fallback baseline state has incomplete restored paths")
    validated = {}
    if not _uses_declared_surface_domain():
        historical_specs = target_specs = SURFACE_SPECS
    for surface in sorted(fallback):
        entries = _validate_baseline_manifest(root, ref, surface,
            historical_specs[surface], manifest[surface])
        if paths is not None and _migrated_restored_paths(surface, paths[surface]) != sorted(entries):
            raise RuntimeError("fallback baseline restored paths are inconsistent")
        retained = {relative: descriptor for relative, descriptor in entries.items()
                    if relative not in retired_paths}
        if set(retained) != _baseline_expected_paths(root, ref, target_specs[surface]):
            raise RuntimeError("fallback baseline retired-path migration is incomplete")
        validated[surface] = retained
    return {**payload, "surface_contract_version": target_version,
            "restored_manifest": validated,
            "restored_paths": {surface: sorted(entries) for surface, entries in validated.items()}}


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
    surface_contract_version = payload.get("surface_contract_version")
    mode = payload.get("mode")
    degraded = payload.get("degraded_surfaces")
    degraded_since = payload.get("degraded_since")
    aliases = _legacy_aliases() if schema_version == 1 else {}
    allowed = set(_historical_surface_specs()) | set(aliases)
    if (
        not isinstance(degraded, list)
        or any(
            not isinstance(surface, str) or surface not in allowed
            for surface in degraded
        )
        or len(set(degraded)) != len(degraded)
        or not FULL_SHA_RE.fullmatch(str(payload.get("baseline_ref") or ""))
        or _parse_utc(payload.get("generated_at")) is None
        or payload.get("fallback_max_age_hours") != FALLBACK_MAX_AGE_HOURS
        or not isinstance(degraded_since, dict)
    ):
        raise RuntimeError("validated baseline publication state is inconsistent")

    if (
        schema_version == 2
        and _uses_declared_surface_domain()
        and surface_contract_version is not None
        and type(surface_contract_version) is not int
    ):
        raise RuntimeError(
            "validated baseline publication state uses an invalid surface contract"
        )

    if (
        schema_version == 2
        and _uses_declared_surface_domain()
        and surface_contract_version is None
        and {"ci_core", "ci_gating"} & set(degraded)
    ):
        payload = _migrate_pre_analytics_v2_state(root, ref, payload)
        surface_contract_version = payload.get("surface_contract_version")

    # Early schema-v2 states did not carry a surface-contract version.  When
    # none of their pre-analytics CI transactions needed expansion above,
    # their remaining ownership domain is exactly the contract-v4 domain.
    if (
        schema_version == 2
        and _uses_declared_surface_domain()
        and surface_contract_version is None
    ):
        payload = {
            **payload,
            "surface_contract_version": PRE_QUEUE_SPLIT_SURFACE_CONTRACT_VERSION,
        }
        surface_contract_version = PRE_QUEUE_SPLIT_SURFACE_CONTRACT_VERSION

    if (
        schema_version == 2
        and _uses_declared_surface_domain()
        and surface_contract_version == PRE_QUEUE_SPLIT_SURFACE_CONTRACT_VERSION
    ):
        payload = _migrate_pre_queue_split_v2_state(root, ref, payload)
        surface_contract_version = payload.get("surface_contract_version")
    if (
        schema_version == 2
        and _uses_declared_surface_domain()
        and surface_contract_version == PRE_VIEW_RETIREMENT_SURFACE_CONTRACT_VERSION
    ):
        payload = _migrate_retired_view_state(root, ref, payload)
        surface_contract_version = payload.get("surface_contract_version")
    if (
        schema_version == 2
        and _uses_declared_surface_domain()
        and surface_contract_version == PRE_RUNTIME_PARITY_SURFACE_CONTRACT_VERSION
    ):
        payload = _migrate_retired_runtime_parity_state(root, ref, payload)
        surface_contract_version = payload.get("surface_contract_version")
    if (
        schema_version == 2
        and _uses_declared_surface_domain()
        and surface_contract_version == PRE_PARITY_OVERRIDES_SURFACE_CONTRACT_VERSION
    ):
        payload = _migrate_retired_parity_overrides_state(root, ref, payload)
        surface_contract_version = payload.get("surface_contract_version")
    if (
        schema_version == 2
        and _uses_declared_surface_domain()
        and surface_contract_version != SURFACE_CONTRACT_VERSION
    ):
        raise RuntimeError(
            "validated baseline publication state uses an unsupported surface contract"
        )

    mode = payload.get("mode")
    degraded = payload.get("degraded_surfaces")
    degraded_since = payload.get("degraded_since")

    manifest = payload.get("restored_manifest")
    restored_paths = payload.get("restored_paths")
    if schema_version == 1:
        if (
            mode not in {"current", "fallback"}
            or (mode == "current" and degraded)
            or (mode == "fallback") != bool(degraded)
            or set(degraded_since) != set(degraded)
            or any(_parse_utc(value) is None for value in degraded_since.values())
        ):
            raise RuntimeError("validated baseline publication state is inconsistent")
        if not degraded:
            if manifest not in (None, {}) or restored_paths not in (None, {}):
                raise RuntimeError(
                    "non-fallback baseline state declares restored content"
                )
            return {
                **payload,
                "schema_version": 2,
                "surface_contract_version": SURFACE_CONTRACT_VERSION,
                "mode": "current",
                "degraded_surfaces": [],
                "fresh_degraded_surfaces": [],
                "fallback_surfaces": [],
                "degraded_since": {},
                "fallback_since": {},
                "restored_paths": {},
                "restored_manifest": {},
            }
        if not isinstance(manifest, dict) or set(manifest) != set(degraded):
            raise RuntimeError(
                "fallback baseline state has an incomplete restore manifest"
            )
        if restored_paths is not None and (
            not isinstance(restored_paths, dict)
            or set(restored_paths) != set(degraded)
        ):
            raise RuntimeError("fallback baseline state has incomplete restored paths")

        expansions = _surface_expansions(degraded, aliases)
        validated_manifest: dict[str, dict] = {}
        for surface in degraded:
            spec = (
                LEGACY_CI_SURFACE_SPEC
                if surface == LEGACY_CI_SURFACE and surface in aliases
                else _historical_surface_specs()[surface]
            )
            entries = _validate_baseline_manifest(
                root, ref, surface, spec, manifest[surface]
            )
            if restored_paths is not None and _migrated_restored_paths(
                surface, restored_paths.get(surface)
            ) != sorted(entries):
                raise RuntimeError(
                    f"fallback baseline restored paths for {surface} are inconsistent"
                )
            validated_manifest[surface] = entries

        # Validate the old monolithic transaction before splitting its proof
        # among the active child surfaces.
        partitioned = _partition_baseline_manifest(
            root, ref, validated_manifest, expansions
        )
        fallback = {
            target for targets in expansions.values() for target in targets
        }
        if _closed_fallback_surfaces(fallback) != fallback:
            raise RuntimeError(
                "validated baseline fallback omits a required dependent surface"
            )
        expanded_since = _expand_clock(degraded_since, expansions)
        migrated = _migrate_retired_view_state(root, ref, {
            **payload,
            "schema_version": 2,
            "surface_contract_version": PRE_VIEW_RETIREMENT_SURFACE_CONTRACT_VERSION,
            "mode": "fallback",
            "degraded_surfaces": sorted(fallback),
            "fresh_degraded_surfaces": [],
            "fallback_surfaces": sorted(fallback),
            "degraded_since": expanded_since,
            "fallback_since": dict(expanded_since),
            "restored_paths": {
                surface: sorted(entries)
                for surface, entries in partitioned.items()
            },
            "restored_manifest": partitioned,
        })
        migrated = _migrate_retired_runtime_parity_state(root, ref, migrated)
        return _migrate_retired_parity_overrides_state(root, ref, migrated)

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
        or _closed_fallback_surfaces(fallback) != set(fallback)
    ):
        raise RuntimeError("validated baseline publication state is inconsistent")

    if not fallback:
        if manifest not in (None, {}):
            raise RuntimeError("non-fallback baseline state declares restored content")
        if restored_paths not in (None, {}):
            raise RuntimeError("non-fallback baseline state declares restored paths")
        return payload
    if not isinstance(manifest, dict) or set(manifest) != set(fallback):
        raise RuntimeError("fallback baseline state has an incomplete restore manifest")
    if restored_paths is not None and (
        not isinstance(restored_paths, dict) or set(restored_paths) != set(fallback)
    ):
        raise RuntimeError("fallback baseline state has incomplete restored paths")
    validated_manifest: dict[str, dict] = {}
    migrated_paths: dict[str, list[str]] = {}
    for surface in fallback:
        entries = _validate_baseline_manifest(
            root, ref, surface, SURFACE_SPECS[surface], manifest[surface]
        )
        normalized_paths = (
            _migrated_restored_paths(surface, restored_paths.get(surface))
            if restored_paths is not None
            else sorted(entries)
        )
        if normalized_paths != sorted(entries):
            raise RuntimeError(
                f"fallback baseline restored paths for {surface} are inconsistent"
            )
        validated_manifest[surface] = entries
        migrated_paths[surface] = sorted(entries)
    return {
        **payload,
        "surface_contract_version": SURFACE_CONTRACT_VERSION,
        "restored_paths": migrated_paths,
        "restored_manifest": validated_manifest,
    }


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


def bounded_publication_state(
    state: dict,
    *,
    max_bytes: int = PUBLICATION_STATE_MAX_BYTES,
) -> dict:
    """Bound diagnostic finding rows while preserving selector control state.

    Surface clocks, fallback manifests, restored paths, failure streaks, and
    incident policy remain exact because they drive recovery. Only redundant
    persisted diagnostic rows are eligible for omission; the live in-memory
    selector still emits workflow outputs from its complete findings.
    """
    if max_bytes <= 0:
        raise ValueError("publication-state byte budget must be positive")
    fields = (
        "final_errors",
        "candidate_errors",
        "final_degradations",
        "candidate_degradations",
    )
    source = {
        field: list(state.get(field) or [])
        for field in fields
    }
    prioritized = [
        (field, index)
        for field in fields
        for index in range(len(source[field]))
    ]

    def candidate(count: int) -> dict:
        selected = set(prioritized[:count])
        published = {
            field: [
                row
                for index, row in enumerate(source[field])
                if (field, index) in selected
            ]
            for field in fields
        }
        result = {
            key: value
            for key, value in state.items()
            if key not in {*fields, "publication_retention"}
        }
        result.update(published)
        counts = {
            field: {
                "source": len(source[field]),
                "published": len(published[field]),
                "omitted": len(source[field]) - len(published[field]),
                "complete": len(source[field]) == len(published[field]),
            }
            for field in fields
        }
        complete = all(row["complete"] for row in counts.values())
        result["publication_retention"] = {
            "policy": "exact_recovery_controls_then_priority_diagnostic_rows_v1",
            "max_bytes": max_bytes,
            "complete_relative_to_source": complete,
            "recovery_controls_complete": True,
            "diagnostic_findings": counts,
        }
        return result

    low, high = 0, len(prioritized)
    best = None
    while low <= high:
        keep = (low + high) // 2
        attempt = candidate(keep)
        if len(pretty_json_bytes(attempt)) <= max_bytes:
            best = attempt
            low = keep + 1
        else:
            high = keep - 1
    if best is None:
        raise RuntimeError(
            "publication-state recovery controls exceed their byte budget; "
            "preserving the last-known-good state"
        )
    return best


def _write_state(path: Path, state: dict) -> None:
    bounded = bounded_publication_state(
        state,
        max_bytes=PUBLICATION_STATE_MAX_BYTES,
    )
    write_pretty_json_lkg(
        path,
        bounded,
        max_bytes=PUBLICATION_STATE_MAX_BYTES,
        label="publication selector state",
    )


def _emit_outputs(state: dict) -> None:
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    degraded = bool(state.get("degraded_surfaces"))
    blocked = state.get("mode") == "blocked"
    incident_policy = (
        state.get("incident_policy")
        or state.get("collector_incident_policy")
        or {}
    )
    typed_surfaces = {
        str(record.get("surface") or "")
        for record in (state.get("collector_failures") or [])
        if isinstance(record, dict) and record.get("surface")
    }
    unexpected_finding = False
    for key in (
        "candidate_errors",
        "candidate_degradations",
        "final_errors",
        "final_degradations",
    ):
        for record in state.get(key) or []:
            if not isinstance(record, dict):
                unexpected_finding = True
                continue
            context = record.get("context")
            if isinstance(context, Mapping) and context.get("alertable") is False:
                continue
            if record.get("code") == "publication-collector-failed":
                continue
            finding_surfaces = {
                str(surface)
                for surface in (record.get("surfaces") or [])
                if str(surface)
            }
            if not typed_surfaces or not finding_surfaces <= typed_surfaces:
                unexpected_finding = True
    alertable_degradation = bool(
        blocked
        or unexpected_finding
        or (
            degraded
            and incident_policy.get("alert", True) is not False
        )
    )
    lines = [
        f"generated_at={state['generated_at']}",
        f"degraded={'true' if degraded else 'false'}",
        f"blocked={'true' if blocked else 'false'}",
        (
            "alertable_degradation="
            + ("true" if alertable_degradation else "false")
        ),
        (
            "transient_alert_suppressed="
            + ("true" if degraded and not alertable_degradation else "false")
        ),
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
            "data/vllm/ci/operations_v2.json.gz",
        ],
        cwd=root,
        check=True,
    )


def _operations_build_failure_record(
    exc: Exception,
    surfaces: Iterable[str],
    *,
    phase: str,
) -> dict[str, Any]:
    """Return bounded, redacted evidence for a derived-model build failure."""
    normalized_phase = _bounded_text(phase, limit=80) or "unknown"
    return {
        "severity": "error",
        "code": "publication-operations-build-failed",
        "message": (
            "the Operations read model could not be rebuilt during "
            f"{normalized_phase}"
        ),
        "path": "data/vllm/ci/operations_v2.json.gz",
        "context": {
            "phase": normalized_phase,
            "exception_type": _bounded_text(type(exc).__name__, limit=80),
            "details": _safe_detail_text(exc),
        },
        "surfaces": sorted({str(surface) for surface in surfaces if str(surface)}),
    }


def select_publication(
    root: Path,
    baseline_ref: str,
    state_path: Path,
    *,
    forced_degraded: Iterable[str] = (),
    collector_failures: Iterable[Mapping[str, Any]] = (),
    refresh_only_surface: str | None = None,
    candidate_code_ref: str | None = None,
    diagnostics: dict | None = None,
) -> dict:
    baseline_ref = baseline_ref.strip().lower()
    if not FULL_SHA_RE.fullmatch(baseline_ref):
        raise ValueError("baseline ref must be one full lowercase commit SHA")
    _run_git(root, "cat-file", "-e", f"{baseline_ref}^{{commit}}")
    normalized_candidate_code_ref = (
        str(candidate_code_ref or "").strip().lower() or None
    )
    if normalized_candidate_code_ref is not None:
        if not FULL_SHA_RE.fullmatch(normalized_candidate_code_ref):
            raise ValueError(
                "candidate code ref must be one full lowercase commit SHA"
            )
        _run_git(
            root,
            "cat-file",
            "-e",
            f"{normalized_candidate_code_ref}^{{commit}}",
        )
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

    normalized_failures = [
        _normalize_collector_failure(record) for record in collector_failures
    ]
    typed_surfaces = {record["surface"] for record in normalized_failures}
    forced = {
        str(surface).strip()
        for surface in forced_degraded
        if str(surface).strip()
    } | typed_surfaces
    unknown_forced = forced - set(SURFACE_SPECS)
    if unknown_forced:
        raise ValueError(f"unknown forced publication surfaces: {sorted(unknown_forced)}")

    refresh_surface = str(refresh_only_surface or "").strip() or None
    if refresh_surface is not None and refresh_surface not in SURFACE_SPECS:
        raise ValueError(f"unknown refresh-only publication surface: {refresh_surface}")
    if refresh_surface is not None and (forced | typed_surfaces) - {refresh_surface}:
        raise ValueError(
            "refresh-only publication cannot accept failures for unattempted surfaces"
        )

    preserved_state: dict | None = None
    preserved_fallback: set[str] = set()
    preserved_fresh_degraded: set[str] = set()
    preserved_collector_failures: list[dict[str, Any]] = []
    preserved_collector_streaks: dict[str, int] = {}
    preserved_retry_observations: list[dict[str, Any]] = []
    preserved_retry_streaks: dict[str, int] = {}
    preserved_collector_policy: dict[str, Any] | None = None
    preserved_incident_policy: dict[str, Any] | None = None
    if refresh_surface is not None:
        _validate_refresh_only_candidate(
            root,
            baseline_ref,
            refresh_surface,
            normalized_candidate_code_ref,
        )
        preserved_state = prior_state()
        if preserved_state is None:
            raise RuntimeError(
                "refresh-only publication requires a validated baseline publication state"
            )
        prior_fallback = set(preserved_state.get("fallback_surfaces") or [])
        prior_fresh = set(preserved_state.get("fresh_degraded_surfaces") or [])
        preserved_fallback = prior_fallback - {refresh_surface}
        if _closed_fallback_surfaces(preserved_fallback) != preserved_fallback:
            raise RuntimeError(
                f"cannot refresh {refresh_surface} independently of a preserved "
                "fallback dependency"
            )
        preserved_fresh_degraded = prior_fresh - {refresh_surface}
        preserved_degraded = preserved_fallback | preserved_fresh_degraded
        raw_collector_policy = preserved_state.get("collector_incident_policy")
        if preserved_degraded and isinstance(raw_collector_policy, dict):
            preserved_collector_policy = copy.deepcopy(raw_collector_policy)
        raw_incident_policy = preserved_state.get("incident_policy")
        if preserved_degraded and isinstance(raw_incident_policy, dict):
            preserved_incident_policy = copy.deepcopy(raw_incident_policy)
        preserved_collector_failures = [
            copy.deepcopy(record)
            for record in (preserved_state.get("collector_failures") or [])
            if isinstance(record, dict)
            and record.get("surface") in preserved_degraded
        ]
        preserved_failure_identities = {
            _collector_failure_persistence_identity(record)
            for record in preserved_collector_failures
        }
        previous_collector_streaks = (
            preserved_state.get("collector_failure_streaks") or {}
        )
        if isinstance(previous_collector_streaks, dict):
            preserved_collector_streaks = {
                identity: count
                for identity, count in previous_collector_streaks.items()
                if identity in preserved_failure_identities
                and isinstance(count, int)
                and not isinstance(count, bool)
                and count > 0
            }
        preserved_retry_observations = [
            copy.deepcopy(record)
            for record in (preserved_state.get("upstream_retry_observations") or [])
            if isinstance(record, dict)
            and record.get("surface") != refresh_surface
        ]
        preserved_retry_identities = {
            _upstream_retry_identity(record)
            for record in preserved_retry_observations
        }
        previous_retry_streaks = preserved_state.get("upstream_retry_streaks") or {}
        if preserved_retry_identities and isinstance(previous_retry_streaks, dict):
            preserved_retry_streaks = {
                str(identity): count
                for identity, count in previous_retry_streaks.items()
                if str(identity) in preserved_retry_identities
                and isinstance(count, int)
                and not isinstance(count, bool)
                and count > 0
            }

    retry_observations = (
        []
        if refresh_surface is not None
        else _active_upstream_retry_observations(root, baseline_ref)
    )
    # ``ci_health.json`` owns the retry attestation, but analytics selects the
    # same completed test-signal build. Restoring only CI core first creates a
    # tree in which the two surfaces point at different builds until a later
    # audit pass happens to widen the fallback. Treat an attested retry as one
    # bounded publication transaction up front so every semantic audit sees a
    # coherent generation. This is deliberately narrower than a global
    # fallback dependency: unrelated analytics or CI-core failures remain
    # independently publishable.
    retry_surfaces = (
        set(UPSTREAM_RETRY_TRANSACTION_SURFACES)
        if retry_observations
        else set()
    )
    now = datetime.now(timezone.utc)
    candidate_errors: list[dict] = []
    candidate_degradations: list[dict] = []
    fresh_degraded: set[str] = set(preserved_fresh_degraded)
    fallback: set[str] = preserved_fallback | _closed_fallback_surfaces(
        forced | retry_surfaces
    )
    restored: dict[str, list[str]] = {}
    admitted_completed_retry = False
    previous: dict | None = None
    state = {
        "schema_version": 2,
        "surface_contract_version": SURFACE_CONTRACT_VERSION,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "baseline_ref": baseline_ref,
        "mode": "current",
        "degraded_surfaces": [],
        "fresh_degraded_surfaces": [],
        "fallback_surfaces": [],
        "degraded_since": {},
        "fallback_since": {},
        "fallback_max_age_hours": FALLBACK_MAX_AGE_HOURS,
        "collector_failures": preserved_collector_failures,
        "collector_failure_streaks": preserved_collector_streaks,
        "collector_incident_policy": preserved_collector_policy or {
            "alert": True,
            "reason": "non-collector-publication-finding",
            "transient_persistence_runs_required": (
                TRANSIENT_ALERT_PERSISTENCE_RUNS
            ),
            "max_observed_streak": 0,
        },
        "incident_policy": preserved_incident_policy or {
            "alert": True,
            "reason": "non-collector-publication-finding",
            "transient_persistence_runs_required": (
                TRANSIENT_ALERT_PERSISTENCE_RUNS
            ),
            "max_observed_streak": 0,
        },
        "upstream_retry_observations": preserved_retry_observations,
        "upstream_retry_streaks": preserved_retry_streaks,
        "candidate_errors": candidate_errors,
        "candidate_degradations": candidate_degradations,
        "final_errors": [],
        "final_degradations": [],
        "restored_paths": {},
        "restored_manifest": {},
    }
    if diagnostics is not None:
        # This reference preserves the complete live finding lanes even if the
        # independently bounded recovery-state file omits diagnostic rows.
        diagnostics["_selection_state"] = state

    def restore_active_fallback() -> None:
        """Atomically restore and attest every not-yet-restored fallback lane."""
        nonlocal previous
        previous = prior_state()
        _apply_surface_state(state, fresh_degraded, fallback, previous, now)
        _raise_if_fallback_expired(state["fallback_since"], now)
        additional = fallback - set(restored)
        preflight = {
            surface: _baseline_payloads(
                root,
                baseline_ref,
                SURFACE_SPECS[surface],
            )
            for surface in sorted(additional)
        }
        for surface in sorted(additional):
            restored[surface] = restore_surface(
                root,
                baseline_ref,
                SURFACE_SPECS[surface],
                preflight=preflight[surface],
            )
        state["restored_paths"] = dict(restored)
        state["restored_manifest"] = _surface_manifest(root, restored)
        # A fallback-aware audit is authorized only after the restored source
        # generation has been hash-attested in durable selector state.
        _write_state(state_path, state)

    def rebuild_operations_with_baseline_recovery() -> None:
        """Rebuild once, restoring the complete validated source on failure.

        The derived builder does not own a publication surface. A malformed
        candidate can therefore fail before the semantic audit has enough
        information to route the defect. The only safe automatic recovery is
        to restore every declared source transaction as one generation and
        retry exactly once. Failure of that validated generation remains a
        hard stop because no newer unvalidated code/data combination may be
        published in its place.
        """
        nonlocal fallback
        try:
            _rebuild_operations(root)
            return
        except Exception as exc:
            all_surfaces = _closed_fallback_surfaces(SURFACE_SPECS)
            candidate_errors.append(
                _operations_build_failure_record(
                    exc,
                    all_surfaces,
                    phase="candidate source generation",
                )
            )
            state["candidate_errors"] = candidate_errors
            fallback = all_surfaces
            fresh_degraded.difference_update(fallback)
            restore_active_fallback()

        try:
            _rebuild_operations(root)
        except Exception as retry_exc:
            terminal = _operations_build_failure_record(
                retry_exc,
                fallback,
                phase="validated baseline retry",
            )
            _apply_surface_state(state, fresh_degraded, fallback, previous, now)
            state["mode"] = "blocked"
            state["final_errors"] = [terminal]
            state["final_degradations"] = []
            _write_state(state_path, state)
            _emit_outputs(state)
            raise RuntimeError(
                "the Operations read model cannot be rebuilt from the "
                "validated baseline source generation"
            ) from retry_exc

    try:
        previous_for_policy = prior_state() if forced or retry_observations else None
        incident_policy, collector_streaks = _collector_incident_policy(
            normalized_failures,
            previous_for_policy,
            has_untyped_forced_surface=bool(forced - typed_surfaces),
        )
        if forced:
            state["collector_incident_policy"] = _compose_incident_policy(
                preserved_collector_policy,
                incident_policy,
            )
            state["incident_policy"] = _compose_incident_policy(
                preserved_incident_policy,
                incident_policy,
            )
        state["collector_failure_streaks"] = {
            **preserved_collector_streaks,
            **collector_streaks,
        }
        retry_policy, retry_streaks = _upstream_retry_incident_policy(
            retry_observations,
            previous_for_policy,
        )
        state["upstream_retry_streaks"] = {
            **preserved_retry_streaks,
            **retry_streaks,
        }
        for observation in retry_observations:
            persistence_runs = retry_streaks[
                _upstream_retry_identity(observation)
            ]
            alertable = (
                persistence_runs >= UPSTREAM_RETRY_ALERT_PERSISTENCE_RUNS
            )
            recorded_observation = {
                **observation,
                "persistence_runs": persistence_runs,
                "alertable": alertable,
            }
            state["upstream_retry_observations"].append(recorded_observation)
            candidate_errors.append({
                "severity": "error",
                "code": UPSTREAM_RETRY_FINDING_CODE,
                "message": (
                    f"{observation['pipeline']} build "
                    f"#{observation['build_number']} returned to an active retry; "
                    "CI core and analytics will retain their coherent validated "
                    "completed cohort"
                ),
                "path": CI_HEALTH_PATH,
                "context": {
                    **recorded_observation,
                    "active_retry": True,
                },
                "surfaces": [observation["surface"]],
            })
        for record in normalized_failures:
            persistence_runs = collector_streaks[
                _collector_failure_persistence_identity(record)
            ]
            alertable = _collector_failure_is_alertable(
                record,
                persistence_runs,
            )
            recorded_failure = {
                **record,
                "persistence_runs": persistence_runs,
                "alertable": alertable,
            }
            state["collector_failures"].append(recorded_failure)
            candidate_errors.append({
                "severity": "error",
                "code": "publication-collector-failed",
                "message": (
                    f"{record['step']} ({record['collector']}) failed with "
                    f"{record['reason_class']}; {record['surface']} will use its "
                    "validated baseline"
                ),
                "path": "",
                "context": {
                    "surface": record["surface"],
                    "collector": record["collector"],
                    "step": record["step"],
                    "reason_class": record["reason_class"],
                    "exit_code": record["exit_code"],
                    "details": record["details"],
                    "persistence_runs": persistence_runs,
                    "alertable": alertable,
                },
                "surfaces": [record["surface"]],
            })
        for surface in sorted(forced - typed_surfaces):
            candidate_errors.append({
                "severity": "error",
                "code": "publication-collector-failed",
                "message": (
                    f"{surface} collection failed before publication validation"
                ),
                "path": "",
                "context": {
                    "surface": surface,
                    "reason_class": "command-error",
                },
                "surfaces": [surface],
            })
        state["candidate_errors"] = candidate_errors
        # Parse all source files first. This catches truncated/missing collector
        # output without depending on the derived Operations bundle being
        # buildable. Forced collector failures join the same transaction set.
        source_audit = DashboardAudit(
            root,
            allow_publication_fallback=False,
            publication_state_path=state_path,
        )
        source_audit.audit_publication_surface_files()
        if retry_observations:
            # A retry can invalidate an already published head while the fresh
            # collectors prove a coherent older completed transaction. Admit
            # that generation only after its exact completed roster, results,
            # runtime commit and source semantics pass; the full derived audit
            # still runs before publication. Otherwise preserve strict recovery.
            if _audit_active_retry_candidate_transaction(source_audit):
                admitted_completed_retry = True
                fallback.difference_update(retry_surfaces - forced)
                candidate_errors[:] = [record for record in candidate_errors if record.get("code") != UPSTREAM_RETRY_FINDING_CODE]
                print("Active CI retry: validating the fresh coherent completed CI cohort instead of restoring the previous pipeline head.")
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
        fallback = _closed_fallback_surfaces(fallback)
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
            restore_active_fallback()

        # Build the candidate read model after any command-level or parse-level
        # quarantine, then use the full cross-surface audit to discover semantic
        # transaction failures such as matrix/health count drift.
        rebuild_operations_with_baseline_recovery()
        candidate = DashboardAudit(
            root,
            allow_publication_fallback=bool(restored),
            publication_state_path=state_path,
        ).run()
        unrouted = []
        for finding in candidate.errors:
            surfaces = finding_surfaces(finding)
            record = _finding_record(finding, surfaces)
            candidate_errors.append(record)
            if surfaces:
                fallback.update(surfaces)
                if admitted_completed_retry and surfaces & UPSTREAM_RETRY_TRANSACTION_SURFACES:
                    fallback.update(retry_surfaces)
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
        fallback = _closed_fallback_surfaces(fallback)
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
        # Cross-surface invariants can reveal another transaction only after
        # the first one has been restored. Reconcile to a fixed point, while
        # requiring every failed pass to add at least one previously current
        # surface. The fallback set grows monotonically, so this is bounded by
        # the number of declared publication surfaces.
        while True:
            restore_active_fallback()
            rebuild_operations_with_baseline_recovery()
            final = DashboardAudit(
                root,
                allow_publication_fallback=True,
                publication_state_path=state_path,
            ).run()
            final_errors = [
                _finding_record(finding, finding_surfaces(finding))
                for finding in final.errors
            ]
            final_degradations = [
                _finding_record(finding, finding_surfaces(finding))
                for finding in getattr(final, "degradations", [])
            ]
            state["final_errors"] = final_errors
            state["final_degradations"] = final_degradations
            unrouted_final_errors = [
                record for record in final_errors if not record["surfaces"]
            ]
            unrouted_final_degradations = [
                record for record in final_degradations if not record["surfaces"]
            ]
            for record in final_degradations:
                fresh_degraded.update(record["surfaces"])
            final_error_surfaces = {
                surface
                for record in final_errors
                for surface in record["surfaces"]
            }
            # Hard errors are never represented as publishable fresh
            # degradation.
            fresh_degraded.difference_update(fallback | final_error_surfaces)
            _apply_surface_state(state, fresh_degraded, fallback, previous, now)

            if not final.errors and not unrouted_final_degradations:
                break

            next_fallback = _closed_fallback_surfaces(
                fallback | final_error_surfaces
            )
            newly_implicated = next_fallback - set(restored)
            if (
                unrouted_final_errors
                or unrouted_final_degradations
                or not newly_implicated
            ):
                state["mode"] = "blocked"
                _write_state(state_path, state)
                _emit_outputs(state)
                raise RuntimeError(
                    "last-known-good surface selection still fails the complete "
                    "dashboard audit"
                )

            # Preserve the intermediate evidence that justified widening the
            # atomic fallback, but reserve final_* for the terminal audit pass.
            candidate_errors.extend(final_errors)
            candidate_degradations.extend(final_degradations)
            state["candidate_errors"] = candidate_errors
            state["candidate_degradations"] = candidate_degradations
            state["final_errors"] = []
            state["final_degradations"] = []
            fallback = next_fallback
            fresh_degraded.difference_update(fallback)
            _apply_surface_state(state, fresh_degraded, fallback, previous, now)
            _raise_if_fallback_expired(state["fallback_since"], now)

        _apply_upstream_retry_reporting_policy(
            state,
            retry_observations,
            retry_policy,
            forced=forced,
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
                    "message": _safe_detail_text(exc),
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
    parser.add_argument(
        "--candidate-code-ref",
        help=(
            "Immutable candidate code commit used only to exempt legitimate "
            "non-generated changes during refresh-only validation"
        ),
    )
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--state-output", default=str(DEFAULT_STATE))
    parser.add_argument(
        "--diagnostics-output", default="",
        help="Write bounded sanitized candidate/final findings and pre-fallback CI evidence to a runner artifact",
    )
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
    parser.add_argument(
        "--collector-failures-file",
        default="",
        help=(
            "JSONL records describing typed collector failures; their surfaces "
            "are restored in addition to explicitly forced surfaces"
        ),
    )
    parser.add_argument(
        "--refresh-only-surface",
        choices=sorted(SURFACE_SPECS),
        help=(
            "Refresh only this source transaction while carrying every other "
            "validated baseline degradation and fallback without advancing its clocks"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    state_path = Path(args.state_output)
    if not state_path.is_absolute():
        state_path = root / state_path
    diagnostics_path = Path(args.diagnostics_output) if args.diagnostics_output else None
    if diagnostics_path is not None and not diagnostics_path.is_absolute():
        diagnostics_path = root / diagnostics_path
    diagnostics = {"generated_at": _utc_now(), "candidate_evidence": {}}
    exit_code = 0
    try:
        if diagnostics_path is not None:
            try:
                _validate_diagnostics_path(root, diagnostics_path, state_path)
            except ValueError:
                diagnostics_path = None
                raise
            try:
                diagnostics["candidate_evidence"] = _candidate_ci_evidence(root)
            except Exception as exc:
                diagnostics["candidate_evidence"] = {"capture_exception_type": type(exc).__name__}
        forced = [*args.force_degraded_surface]
        forced.extend(
            surface.strip()
            for surface in args.force_degraded_surfaces.split(",")
            if surface.strip()
        )
        collector_failures = []
        if args.collector_failures_file.strip():
            failures_path = Path(args.collector_failures_file)
            if not failures_path.is_absolute():
                failures_path = root / failures_path
            collector_failures = load_collector_failures(failures_path)
        select_publication(
            root,
            args.baseline_ref,
            state_path,
            forced_degraded=forced,
            collector_failures=collector_failures,
            refresh_only_surface=args.refresh_only_surface,
            candidate_code_ref=args.candidate_code_ref,
            diagnostics=diagnostics if diagnostics_path is not None else None,
        )
    except Exception as exc:
        diagnostics["exception_type"] = type(exc).__name__
        if "_selection_state" not in diagnostics:
            diagnostics["_selection_state"] = {"mode": "blocked", "final_errors": [{
                "severity": "error", "code": "publication-selection-failed-before-audit",
                "message": "Selection failed before an auditable candidate state was created",
                "context": {"exception_type": type(exc).__name__},
            }]}
        print(f"Publication selection failed: {_diagnostic_text(exc)}", file=sys.stderr)
        exit_code = 1
    finally:
        if diagnostics_path is not None:
            try:
                _write_selection_diagnostics(diagnostics_path, diagnostics)
            except Exception as exc:
                # Observability never authorizes fallback or changes selection's
                # result. Keep the original hard-stop exit when selection fails.
                print(f"Publication diagnostics could not be written ({type(exc).__name__})", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
