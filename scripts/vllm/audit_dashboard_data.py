#!/usr/bin/env python3
"""Audit the dashboard's generated data, frontend contracts, and deploy path.

The normal pytest suite has focused unit and schema checks. This script is the
cross-surface pass: it follows the user-facing dashboard numbers back to their
JSON files, verifies that related views agree on the same source of truth, and
checks the workflows that publish those files.

Usage:
    python scripts/vllm/audit_dashboard_data.py
    python scripts/vllm/audit_dashboard_data.py --format json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
VLLM = DATA / "vllm"
CI = VLLM / "ci"

AMD_FAILURE_STATES = {"failed", "timed_out", "broken", "soft_fail"}
AMD_WAITING_STATES = {"running", "scheduled", "assigned"}
RESULT_SUFFIXES = {"amd-ci": "amd", "ci": "upstream"}
CROSS_VIEW_GROUP_DRIFT_TOLERANCE = 1


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list:
    return value if isinstance(value, list) else []


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _strict_positive_int_set(value: Any) -> set[int] | None:
    if not isinstance(value, list):
        return None
    if any(
        not isinstance(item, int) or isinstance(item, bool) or item <= 0
        for item in value
    ):
        return None
    return set(value)


def _buildkite_url_matches(
    value: Any,
    pipeline: str,
    build_number: Any = None,
    *,
    require_job: bool = False,
) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.netloc != "buildkite.com":
        return False
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[:3] != ["vllm", pipeline, "builds"]:
        return False
    actual = _safe_int(parts[3], -1)
    expected = _safe_int(build_number, -1) if build_number not in (None, "") else None
    if actual <= 0 or (expected is not None and actual != expected):
        return False
    suffix = parts[4:]
    if not require_job:
        return not suffix
    if len(suffix) < 2 or suffix[0] != "steps":
        return False
    if suffix[1] == "canvas":
        query = parse_qs(parsed.query)
        return bool(query.get("jid") or query.get("sid"))
    return bool(suffix[1])


@dataclass(frozen=True)
class DataSpec:
    relpath: str
    producers: tuple[str, ...]
    consumers: tuple[str, ...]
    required_keys: tuple[str, ...] = ()
    description: str = ""


DATA_SPECS: tuple[DataSpec, ...] = (
    DataSpec(
        "data/site/projects.json",
        ("scripts/render.py",),
        ("docs/assets/js/dashboard.js",),
        ("projects",),
        "Project selector/config shell",
    ),
    DataSpec(
        "data/vllm/prs.json",
        ("scripts/collect.py",),
        ("docs/assets/js/dashboard.js",),
        ("collected_at", "prs"),
        "Home PR list and top PR counters",
    ),
    DataSpec(
        "data/vllm/issues.json",
        ("scripts/collect.py",),
        ("docs/assets/js/dashboard.js",),
        ("collected_at", "issues"),
        "Home project #39 issue list and issue counter",
    ),
    DataSpec(
        "data/vllm/ci/ci_health.json",
        ("scripts/collect_ci.py", "scripts/vllm/ci/reporter.py"),
        ("docs/assets/js/dashboard.js", "docs/assets/js/ci-health.js"),
        ("generated_at", "amd", "upstream"),
        "CI Health cards and hardware test-count breakdown",
    ),
    DataSpec(
        "data/vllm/ci/parity_report.json",
        ("scripts/collect_ci.py", "scripts/vllm/ci/reporter.py"),
        ("docs/assets/js/dashboard.js", "docs/assets/js/ci-health.js"),
        ("generated_at", "job_groups", "amd_build", "upstream_build"),
        "ROCm/CUDA parity and Home AMD hardware breakdown",
    ),
    DataSpec(
        "data/vllm/ci/config_parity.json",
        ("scripts/collect_ci.py",),
        ("scripts/vllm/build_operations_snapshot.py",),
        ("summary", "matches", "amd_only", "nvidia_only"),
        "Commit-pinned vLLM AMD/upstream CI source-definition parity",
    ),
    DataSpec(
        "data/vllm/ci/analytics.json",
        ("scripts/vllm/collect_analytics.py",),
        ("scripts/vllm/build_operations_snapshot.py", "docs/assets/js/ci-analytics.js"),
        ("amd-ci", "ci"),
        "Nightly comparison plus all-main reliability evidence",
    ),
    DataSpec(
        "data/vllm/ci/gating_nightlies.json",
        ("scripts/vllm/collect_analytics.py",),
        ("docs/assets/js/ci-health.js",),
        ("generated_at", "ci", "amd-ci"),
        "Slim nightly Buildkite job signal for the AMD gating executive view",
    ),
    DataSpec(
        "data/vllm/ci/gating_targets.json",
        ("scripts/vllm/collect_gating_targets.py",),
        ("docs/assets/js/ci-health.js",),
        ("generated_at", "summary", "groups"),
        "Canonical AMD gating target list used for still-to-gate tracking",
    ),
    DataSpec(
        "data/vllm/ci/amd_test_matrix.json",
        ("scripts/vllm/collect_amd_test_matrix.py",),
        ("docs/assets/js/dashboard.js", "docs/assets/js/ci-analytics.js"),
        ("generated_at", "source", "summary", "architectures", "rows"),
        "AMD hardware matrix and cross-view hardware-group counts",
    ),
    DataSpec(
        "data/vllm/ci/gating_proposals.json",
        ("scripts/vllm/collect_gating_proposals.py",),
        ("docs/assets/js/ci-health.js",),
        ("generated_at", "source_repo", "tracked_authors", "summary", "pull_requests"),
        "Open PRs from tracked engineers that propose new AMD mirror gating",
    ),
    DataSpec(
        "data/vllm/ci/gating_target_candidates.json",
        ("scripts/vllm/collect_gating_target_candidates.py",),
        ("docs/assets/js/ci-health.js",),
        ("generated_at", "source", "heuristics", "summary", "rows"),
        "Review-only daily audit for maintaining the canonical AMD gating target list",
    ),
    DataSpec(
        "data/vllm/ci/queue_timeseries.jsonl",
        ("scripts/vllm/collect_queue_snapshot.py",),
        ("docs/assets/js/ci-queue.js", "docs/assets/js/ci-hotness.js"),
        (),
        "Queue charts and wait/running workload trend",
    ),
    DataSpec(
        "data/vllm/ci/queue_jobs.json",
        ("scripts/vllm/collect_queue_snapshot.py",),
        ("docs/assets/js/ci-queue.js",),
        ("ts", "pending", "running"),
        "Queue job overlays and admin triage",
    ),
    DataSpec(
        "data/vllm/ci/group_changes.json",
        ("scripts/vllm/collect_group_changes.py",),
        ("docs/assets/js/ci-analytics.js",),
        ("generated_at", "changes"),
        "Test-group trend PR attribution",
    ),
    DataSpec(
        "data/vllm/ci/operations_v2.json",
        ("scripts/vllm/build_operations_snapshot.py",),
        ("docs/assets/js/ops-v2.js",),
        (
            "schema_version", "generated_at", "nightly", "reliability",
            "gating", "queue", "amd_agent_health",
        ),
        "Versioned AMD current-signal and upstream reliability read model",
    ),
    DataSpec(
        "data/vllm/ci/operations_v2_manifest.json",
        ("scripts/vllm/build_operations_snapshot.py",),
        ("docs/assets/js/ops-v2.js",),
        ("schema_version", "bundle_version", "generated_at", "shell", "sections"),
        "Fast operational shell and lazy evidence-section manifest",
    ),
    DataSpec(
        "data/vllm/ci/ready_tickets.json",
        ("scripts/vllm/sync_ready_tickets.py",),
        ("docs/assets/js/ci-ready.js",),
        ("generated_at", "failing_groups_total", "tickets", "groups_all"),
        "Ready-ticket triage and per-group build evidence",
    ),
    DataSpec(
        "data/vllm/perf_eval/perf_eval.json",
        ("scripts/vllm/collect_perf_eval.py",),
        ("docs/assets/js/ops-v2.js", "docs/assets/js/ci-perf-eval.js"),
        ("generated_at", "pipeline", "metric_meta", "models", "summary"),
        "Webhook-fed AMD performance and accuracy series",
    ),
)


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    path: str = ""

    def as_dict(self) -> dict[str, str]:
        out = {"severity": self.severity, "code": self.code, "message": self.message}
        if self.path:
            out["path"] = self.path
        return out


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "errors": [f.as_dict() for f in self.errors],
            "warnings": [f.as_dict() for f in self.warnings],
            "metrics": self.metrics,
        }


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def result_count(row: dict[str, Any]) -> int:
    match = re.search(r"\((\d+)\)\s*$", str(row.get("name") or ""))
    return int(match.group(1)) if match else 1


def normalize_job_name(name: str) -> str:
    text = re.sub(r"^(mi\d+_\d+|gpu_\d+|amd_\w+):\s*", "", name or "", flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def is_amd_queue(name: str) -> bool:
    return str(name or "").startswith("amd_") or str(name or "") == "amd-cpu"


def is_retired_queue(name: str) -> bool:
    normalized = str(name or "").strip().casefold()
    return "mi355b" in normalized


def is_mi355b_queue(name: str) -> bool:
    return "mi355b" in str(name or "").casefold()


def same_repo(ref_repo: str | None, default_repo: str) -> bool:
    return (ref_repo or default_repo).lower() == default_repo.lower()


class DashboardAudit:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.report = AuditReport()
        self._json_cache: dict[Path, Any] = {}

    def run(self) -> AuditReport:
        self.audit_data_inventory()
        self.audit_operations_v2()
        self.audit_operations_bundle()
        self.audit_home_pr_issue_data()
        self.audit_ci_health()
        self.audit_gating_target_candidates()
        self.audit_analytics()
        self.audit_amd_matrix()
        self.audit_queue_data()
        self.audit_ready_tickets()
        self.audit_frontend_contracts()
        self.audit_workflows()
        return self.report

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def add(self, severity: str, code: str, message: str, path: str | Path = "") -> None:
        self.report.findings.append(Finding(severity, code, message, str(path)))

    def error(self, code: str, message: str, path: str | Path = "") -> None:
        self.add("error", code, message, path)

    def warning(self, code: str, message: str, path: str | Path = "") -> None:
        self.add("warning", code, message, path)

    def load_json(self, relpath: str, default: Any = None) -> Any:
        path = self.root / relpath
        if path in self._json_cache:
            return self._json_cache[path]
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            self.error("missing-json", f"{relpath} is missing", relpath)
            return default
        except json.JSONDecodeError as exc:
            self.error("invalid-json", f"{relpath} is not valid JSON: {exc}", relpath)
            return default
        self._json_cache[path] = data
        return data

    def load_jsonl(self, relpath: str) -> list[dict[str, Any]]:
        path = self.root / relpath
        rows: list[dict[str, Any]] = []
        try:
            lines = path.read_text().splitlines()
        except FileNotFoundError:
            self.error("missing-jsonl", f"{relpath} is missing", relpath)
            return rows
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                self.error(
                    "invalid-jsonl-row",
                    f"{relpath}:{line_no} is not valid JSON: {exc}",
                    relpath,
                )
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                self.error(
                    "invalid-jsonl-row",
                    f"{relpath}:{line_no} is {type(row).__name__}, expected object",
                    relpath,
                )
        return rows

    def latest_result_file(self, suffix: str) -> Path | None:
        paths = sorted((self.root / "data/vllm/ci/test_results").glob(f"*_{suffix}.jsonl"))
        return paths[-1] if paths else None

    def build_numbers_in_jsonl(self, path: Path | None) -> set[int]:
        if path is None:
            return set()
        numbers: set[int] = set()
        for raw in path.read_text().splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            try:
                numbers.add(int(row.get("build_number") or 0))
            except (TypeError, ValueError):
                continue
        return {n for n in numbers if n}

    def audit_data_inventory(self) -> None:
        inventory: list[dict[str, Any]] = []
        for spec in DATA_SPECS:
            path = self.root / spec.relpath
            exists = path.exists()
            inventory.append(
                {
                    "path": spec.relpath,
                    "exists": exists,
                    "description": spec.description,
                    "producers": spec.producers,
                    "consumers": spec.consumers,
                }
            )
            if not exists:
                self.error("missing-data-file", f"{spec.relpath} is missing", spec.relpath)
                continue

            if spec.relpath.endswith(".json"):
                payload = self.load_json(spec.relpath, {})
                if isinstance(payload, dict):
                    missing = set(spec.required_keys) - set(payload.keys())
                    if missing:
                        self.error(
                            "missing-data-keys",
                            f"{spec.relpath} missing required keys {sorted(missing)}",
                            spec.relpath,
                        )
                else:
                    self.error(
                        "data-shape",
                        f"{spec.relpath} is {type(payload).__name__}, expected object",
                        spec.relpath,
                    )
            elif spec.relpath.endswith(".jsonl"):
                if not self.load_jsonl(spec.relpath):
                    self.error("empty-jsonl", f"{spec.relpath} has no valid rows", spec.relpath)

            basename = Path(spec.relpath).name
            producer_mentions = False
            for producer in spec.producers:
                producer_path = self.root / producer
                if not producer_path.exists():
                    self.error("missing-producer", f"Producer {producer} is missing", producer)
                    continue
                if basename in producer_path.read_text(errors="ignore"):
                    producer_mentions = True
            if not producer_mentions:
                self.warning(
                    "producer-lineage",
                    f"No listed producer mentions {basename}; lineage may be stale",
                    spec.relpath,
                )
            for consumer in spec.consumers:
                consumer_path = self.root / consumer
                if not consumer_path.exists():
                    self.error("missing-consumer", f"Consumer {consumer} is missing", consumer)
                    continue
                if basename not in consumer_path.read_text(errors="ignore"):
                    self.warning(
                        "consumer-lineage",
                        f"{consumer} does not mention {basename}; frontend contract may be stale",
                        consumer,
                    )
        self.report.metrics["data_inventory"] = inventory

    def audit_operations_v2(self) -> None:
        relpath = "data/vllm/ci/operations_v2.json"
        payload = self.load_json(relpath, {})
        if not isinstance(payload, dict):
            return
        if payload.get("schema_version") != 2:
            self.error("operations-schema", "operations_v2.json must use schema_version 2", relpath)

        definition_parity = _mapping(payload.get("definition_parity"))
        if definition_parity:
            parity_summary = _mapping(definition_parity.get("summary"))
            parity_matches = _rows(definition_parity.get("matches"))
            parity_amd_only = _rows(definition_parity.get("amd_only"))
            parity_upstream_only = _rows(definition_parity.get("nvidia_only"))
            expected_counts = {
                "matched": len(parity_matches),
                "amd_only": len(parity_amd_only),
                "nvidia_only": len(parity_upstream_only),
                "command_twins": sum(
                    _mapping(row).get("match_method") == "command_twin"
                    for row in parity_matches
                ),
            }
            for key, expected in expected_counts.items():
                if _safe_int(parity_summary.get(key)) != expected:
                    self.error(
                        "definition-parity-count",
                        f"definition_parity.summary.{key}={parity_summary.get(key)} but rows imply {expected}",
                        relpath,
                    )
            source = _mapping(definition_parity.get("source"))
            commit_sha = str(source.get("commit_sha") or "")
            if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
                self.error(
                    "definition-parity-commit",
                    "definition_parity must identify one full vLLM commit SHA",
                    relpath,
                )
            invalid_twins = [
                _mapping(row).get("amd_label")
                for row in parity_matches
                if _mapping(row).get("match_method") == "command_twin"
                and (
                    _safe_float(_mapping(row).get("command_similarity")) < 0.999999
                    or _safe_float(_mapping(row).get("title_similarity")) < 0.65
                )
            ]
            if invalid_twins:
                self.error(
                    "definition-parity-twin",
                    f"{len(invalid_twins)} command twins violate the exact-command/title threshold",
                    relpath,
                )

        nightly_builds = _rows(
            _mapping(_mapping(payload.get("nightly")).get("canonical_history")).get("builds")
        )
        health_payload = self.load_json("data/vllm/ci/ci_health.json", {})
        health_amd = _mapping(_mapping(health_payload).get("amd"))
        latest_pipeline = _mapping(
            health_amd.get("latest_pipeline_build") or health_amd.get("latest_build")
        )
        if nightly_builds and latest_pipeline:
            operations_number = _safe_int(_mapping(nightly_builds[0]).get("number"))
            health_number = _safe_int(
                latest_pipeline.get("build_number") or latest_pipeline.get("number")
            )
            if operations_number != health_number:
                self.error(
                    "operations-latest-nightly",
                    f"operations latest AMD nightly #{operations_number} does not match ci_health pipeline build #{health_number}",
                    relpath,
                )

        gating = _mapping(payload.get("gating"))
        active = _rows(gating.get("active_target_groups"))
        active_summary = _mapping(gating.get("active_target_summary"))
        expected_active = _safe_int(active_summary.get("target_group_count"))
        if len(active) != expected_active:
            self.error(
                "operations-active-target-count",
                f"active target rows={len(active)} but summary target_group_count={expected_active}",
                relpath,
            )

        unsupported_owners = [
            _mapping(row).get("label")
            for row in active
            if _mapping(row).get("owner") or "owner" in _mapping(row)
        ]
        if unsupported_owners:
            self.error(
                "operations-unsupported-owners",
                f"{len(unsupported_owners)} gating rows publish an unsupported owner field",
                relpath,
            )

        linked_gating = 0
        observed_gating = 0
        wrong_latest_pipeline = []
        wrong_latest_urls = []
        wrong_history_pipeline = []
        wrong_history_evidence = []
        wrong_history_urls = []
        for raw_row in active:
            row = _mapping(raw_row)
            latest = _mapping(row.get("latest_amd_result"))
            if latest.get("source_pipeline") != "amd-ci":
                wrong_latest_pipeline.append(row.get("label"))
            latest_evidence = _rows(latest.get("evidence"))
            if any(
                not isinstance(item, dict)
                or item.get("source_pipeline") != "amd-ci"
                for item in latest_evidence
            ):
                wrong_latest_pipeline.append(row.get("label"))
            latest_links = [
                (
                    item.get("url") or item.get("job_url") or item.get("build_url"),
                    item.get("build_number") or latest.get("build_number"),
                )
                for item in latest_evidence
                if isinstance(item, dict)
                if item.get("url") or item.get("job_url") or item.get("build_url")
            ]
            if any(
                not _buildkite_url_matches(
                    url,
                    "amd-ci",
                    build_number,
                    require_job=True,
                )
                for url, build_number in latest_links
            ):
                wrong_latest_urls.append(row.get("label"))
            history = _mapping(row.get("main_reliability"))
            if history.get("source_pipeline") != "ci":
                wrong_history_pipeline.append(row.get("label"))
            history_evidence = _rows(row.get("evidence"))
            if any(
                not isinstance(item, dict) or item.get("source_pipeline") != "ci"
                for item in history_evidence
            ):
                wrong_history_evidence.append(row.get("label"))
            bad_history_links = any(
                not _buildkite_url_matches(
                    item.get("url") or item.get("job_url"),
                    "ci",
                    item.get("build_number"),
                    require_job=True,
                )
                for item in history_evidence
                if isinstance(item, dict)
            )
            incident = _mapping(row.get("last_incident"))
            if incident and (
                incident.get("source_pipeline") != "ci"
                or not _buildkite_url_matches(
                    incident.get("job_url"),
                    "ci",
                    incident.get("build_number"),
                    require_job=True,
                )
            ):
                bad_history_links = True
            if history.get("latest_url") and not _buildkite_url_matches(
                history.get("latest_url"), "ci", require_job=True
            ):
                bad_history_links = True
            if bad_history_links:
                wrong_history_urls.append(row.get("label"))
            if latest.get("state") in {"passed", "soft", "hard"}:
                observed_gating += 1
                if any(item.get("url") for item in latest_evidence if isinstance(item, dict)):
                    linked_gating += 1
        if observed_gating != linked_gating:
            self.error(
                "operations-gating-missing-links",
                f"{observed_gating - linked_gating} of {observed_gating} observed gating rows lack exact AMD evidence",
                relpath,
            )
        if wrong_latest_pipeline:
            self.error(
                "operations-gating-latest-source-pipeline",
                f"{len(wrong_latest_pipeline)} gating rows do not source latest results from amd-ci",
                relpath,
            )
        if wrong_latest_urls:
            self.error(
                "operations-gating-latest-source-url",
                f"{len(wrong_latest_urls)} gating rows contain non-AMD links in latest AMD evidence",
                relpath,
            )
        if wrong_history_pipeline or wrong_history_evidence or wrong_history_urls:
            self.error(
                "operations-gating-history-source-pipeline",
                (
                    f"{len(wrong_history_pipeline)} reliability summaries and "
                    f"{len(wrong_history_evidence)} evidence lists do not source history from ci; "
                    f"{len(wrong_history_urls)} rows contain non-ci history links"
                ),
                relpath,
            )

        reliability = _mapping(payload.get("reliability"))
        if reliability.get("source_pipeline") != "ci":
            self.error(
                "operations-reliability-source-pipeline",
                (
                    "Canonical operations_v2.reliability must publish "
                    f"source_pipeline='ci', found {reliability.get('source_pipeline')!r}"
                ),
                relpath,
            )
        if reliability.get("available") is not True:
            self.error(
                "operations-reliability-unavailable",
                "Canonical upstream reliability must fail closed until a strict ci all-main cohort is present",
                relpath,
            )
        if "amd_reliability" in payload:
            self.error(
                "operations-amd-historical-reliability",
                "AMD historical reliability must not be published; AMD is the latest gating signal only",
                relpath,
            )
        cohort = _mapping(reliability.get("cohort"))
        if cohort.get("id") != "main":
            self.error(
                "operations-reliability-cohort",
                "Reliability must identify the all-main cohort separately from nightlies",
                relpath,
            )
        composition = _mapping(cohort.get("composition"))
        cohort_builds = _safe_int(composition.get("all_main_builds") or cohort.get("build_count"))
        cohort_nightlies = _safe_int(
            composition.get("canonical_nightlies")
            or cohort.get("canonical_nightly_build_count")
        )
        cohort_other_main = _safe_int(
            composition.get("other_main_builds")
            or cohort.get("other_main_build_count")
            or cohort.get("non_nightly_main_build_count")
        )
        if cohort_builds != cohort_nightlies + cohort_other_main:
            self.error(
                "operations-reliability-cohort-composition",
                (
                    f"all-main builds={cohort_builds} but canonical nightlies + other main "
                    f"builds={cohort_nightlies + cohort_other_main}"
                ),
                relpath,
            )

        nightly = _mapping(payload.get("nightly"))
        canonical = _mapping(nightly.get("canonical_history")) or next(
            (
                row for row in _rows(nightly.get("pipelines"))
                if isinstance(row, dict) and row.get("pipeline") == "amd-ci"
            ),
            {},
        )
        canonical_rows = _rows(canonical.get("builds"))
        expected_nightlies = min(30, _safe_int(canonical.get("builds_available")))
        if len(canonical_rows) != expected_nightlies:
            self.error(
                "operations-nightly-retention",
                f"canonical AMD history retains {len(canonical_rows)} builds, expected {expected_nightlies}",
                relpath,
            )
        upstream_parity = _mapping(nightly.get("upstream_parity"))
        if upstream_parity.get("pipeline") != "ci" or canonical.get("pipeline") != "amd-ci":
            self.error(
                "operations-upstream-parity-scope",
                "Canonical AMD nightly history and upstream parity must be published separately",
                relpath,
            )
        if "branch=main" not in str((reliability.get("denominator") or {}).get("unit") or ""):
            self.error(
                "operations-reliability-denominator",
                "Reliability denominator must explicitly describe ci branch=main observations",
                relpath,
            )
        cohort_provenance = _mapping(cohort.get("provenance"))
        source_cohort = _mapping(cohort_provenance.get("cohort"))
        if source_cohort.get("pipeline") != "ci" or cohort_provenance.get("fallback"):
            self.error(
                "operations-reliability-cohort-source",
                "Canonical reliability must be backed by the strict ci all-main collector cohort",
                relpath,
            )

        catalog = _rows(reliability.get("group_catalog"))
        catalog_by_id = {
            row.get("id"): row
            for row in catalog
            if isinstance(row, dict) and row.get("id")
        }
        candidates = _rows(reliability.get("flaky_candidates"))
        cohort_build_numbers = {
            _safe_int(number, -1)
            for number in _rows(cohort.get("build_numbers"))
            if _safe_int(number, -1) > 0
        }
        if reliability.get("available") is True and len(cohort_build_numbers) != cohort_builds:
            self.error(
                "operations-reliability-cohort-build-numbers",
                (
                    f"cohort declares {cohort_builds} builds but publishes "
                    f"{len(cohort_build_numbers)} unique build-number foreign keys"
                ),
                relpath,
            )
        observations = 0
        linked_observations = 0
        denominator_observations = 0
        for raw_group in catalog:
            group = _mapping(raw_group)
            rows = _rows(group.get("observations"))
            if group.get("source_pipeline") != "ci":
                self.error(
                    "operations-reliability-group-source",
                    f"{group.get('name')}: group source_pipeline is not ci",
                    relpath,
                )
            wrong_source_rows = [
                row for row in rows
                if not isinstance(row, dict)
                or row.get("source_pipeline") != "ci"
                or _safe_int(row.get("build_number"), -1) not in cohort_build_numbers
                or not _buildkite_url_matches(
                    row.get("build_url"), "ci", row.get("build_number")
                )
                or not _buildkite_url_matches(
                    row.get("job_url"), "ci", row.get("build_number"), require_job=True
                )
                or (
                    row.get("step_url")
                    and not _buildkite_url_matches(
                        row.get("step_url"), "ci", row.get("build_number"), require_job=True
                    )
                )
            ]
            if wrong_source_rows:
                self.error(
                    "operations-reliability-observation-source",
                    f"{group.get('name')}: {len(wrong_source_rows)} retained observations are not upstream ci evidence",
                    relpath,
                )
            observations += len(rows)
            linked_observations += sum(
                bool(row.get("job_url")) for row in rows if isinstance(row, dict)
            )
            runs = _safe_int(group.get("runs"))
            denominator_observations += runs
            retained = _safe_int(group.get("retained_observation_count"))
            if len(rows) != retained or retained > runs:
                self.error(
                    "operations-reliability-evidence-count",
                    f"{group.get('name')}: runs={runs}, retained={retained}, rows={len(rows)}",
                    relpath,
                )
            group_ids = group.get("group_ids") or []
            if not group.get("id") or group.get("id") not in group_ids:
                self.error(
                    "operations-reliability-group-identity",
                    f"{group.get('name')}: strict id is absent from aggregate group_ids",
                    relpath,
                )
            if "hardware" not in group or "queues" not in group:
                self.error(
                    "operations-reliability-hardware-identity",
                    f"{group.get('name')}: hardware/queue identity is missing",
                    relpath,
                )
            if group.get("median_dur") is not None and group.get("max_dur") is None:
                self.error(
                    "operations-reliability-max-duration",
                    f"{group.get('name')}: median duration is published without a maximum",
                    relpath,
                )
        expected_denominator = _safe_int(
            _mapping(reliability.get("denominator")).get("observations")
        )
        if denominator_observations != expected_denominator:
            self.error(
                "operations-reliability-denominator-sum",
                f"catalog runs={denominator_observations} but denominator observations={expected_denominator}",
                relpath,
            )
        for raw_candidate in candidates:
            candidate = _mapping(raw_candidate)
            if candidate.get("source_pipeline") != "ci":
                self.error(
                    "operations-flaky-candidate-source",
                    f"{candidate.get('name')}: flaky candidate source_pipeline is not ci",
                    relpath,
                )
            evidence_ref = candidate.get("evidence_ref") or candidate.get("id")
            if evidence_ref not in catalog_by_id:
                self.error(
                    "operations-reliability-evidence-ref",
                    f"{candidate.get('name')}: missing catalog evidence reference {evidence_ref!r}",
                    relpath,
                )
            if candidate.get("evidence_type") != "mixed_outcome_history":
                self.error(
                    "operations-reliability-evidence-type",
                    f"{candidate.get('name')}: missing mixed_outcome_history classification",
                    relpath,
                )
        if observations and linked_observations != observations:
            self.error(
                "operations-reliability-missing-links",
                f"{observations - linked_observations} of {observations} retained observations lack an exact job URL",
                relpath,
            )

        latency = _rows(_mapping(reliability.get("latency_rankings")).get("by_p90_duration"))
        wrong_latency_source = [
            _mapping(row).get("name")
            for row in latency
            if _mapping(row).get("source_pipeline") != "ci"
        ]
        if wrong_latency_source:
            self.error(
                "operations-latency-source",
                f"{len(wrong_latency_source)} latency rows are not sourced from upstream ci",
                relpath,
            )
        missing_latency_max = [
            _mapping(row).get("name")
            for row in latency
            if _mapping(row).get("max_dur") is None
        ]
        if missing_latency_max:
            self.error(
                "operations-latency-max-duration",
                f"{len(missing_latency_max)} latency rows omit max_dur",
                relpath,
            )

        retry = _mapping(reliability.get("retry_analysis"))
        retry_summary = _mapping(retry.get("summary"))
        retry_attempts = _rows(retry.get("retry_attempts"))
        recoveries = _rows(retry.get("failed_then_passed_recoveries"))
        if _safe_int(retry_summary.get("retry_attempt_count")) != len(retry_attempts):
            self.error(
                "operations-retry-attempt-count",
                (
                    f"retry summary={retry_summary.get('retry_attempt_count')} but "
                    f"{len(retry_attempts)} attempt rows are published"
                ),
                relpath,
            )
        if _safe_int(retry_summary.get("failed_then_passed_recovery_count")) != len(recoveries):
            self.error(
                "operations-retry-recovery-count",
                (
                    f"recovery summary={retry_summary.get('failed_then_passed_recovery_count')} but "
                    f"{len(recoveries)} chains are published"
                ),
                relpath,
            )
        missing_retry_urls = sum(
            not isinstance(row, dict) or not (row.get("job_url") or row.get("url"))
            for row in retry_attempts
        )
        missing_recovery_urls = sum(
            not isinstance(row, dict) or not (row.get("failed_url") and row.get("passed_url"))
            for row in recoveries
        )
        if missing_retry_urls or missing_recovery_urls:
            self.error(
                "operations-retry-links",
                (
                    f"{missing_retry_urls} retry attempts and {missing_recovery_urls} recovered chains "
                    "lack exact retained URLs"
                ),
                relpath,
            )
        wrong_retry_sources = sum(
            not isinstance(row, dict)
            or row.get("source_pipeline") != "ci"
            or _safe_int(row.get("build_number"), -1) not in cohort_build_numbers
            or not _buildkite_url_matches(
                row.get("job_url") or row.get("url"),
                "ci",
                row.get("build_number"),
                require_job=True,
            )
            for row in retry_attempts
        )
        wrong_recovery_sources = sum(
            not isinstance(row, dict)
            or row.get("source_pipeline") != "ci"
            or _safe_int(row.get("build_number"), -1) not in cohort_build_numbers
            or not _buildkite_url_matches(
                row.get("failed_url"), "ci", row.get("build_number"), require_job=True
            )
            or not _buildkite_url_matches(
                row.get("passed_url"), "ci", row.get("build_number"), require_job=True
            )
            for row in recoveries
        )
        if wrong_retry_sources or wrong_recovery_sources:
            self.error(
                "operations-retry-source",
                (
                    f"{wrong_retry_sources} retry attempts and {wrong_recovery_sources} recoveries "
                    "are not exact upstream ci evidence"
                ),
                relpath,
            )

        queue_block = _mapping(payload.get("queue"))
        queue_provenance = _mapping(queue_block.get("provenance"))
        trajectory_provenance = _mapping(_mapping(payload.get("trajectory")).get("provenance"))
        omni_provenance = _mapping(_mapping(payload.get("omni")).get("provenance"))
        expected_source_paths = {
            "queue history": (
                _mapping(queue_provenance.get("source_paths"))
            ).get("history"),
            "trajectory builds": (
                _mapping(trajectory_provenance.get("source_paths"))
            ).get("build_history"),
            "trajectory changes": (
                _mapping(trajectory_provenance.get("source_paths"))
            ).get("group_changes"),
            "Omni aggregates": (
                _mapping(omni_provenance.get("source_paths"))
            ).get("queue_aggregates"),
        }
        required_source_paths = {
            "queue history": "queue_timeseries.jsonl",
            "trajectory builds": "analytics.json",
            "trajectory changes": "group_changes.json",
            "Omni aggregates": "queue_timeseries.jsonl",
        }
        mismatched_paths = {
            label: (expected_source_paths.get(label), expected)
            for label, expected in required_source_paths.items()
            if expected_source_paths.get(label) != expected
        }
        if mismatched_paths:
            self.error(
                "operations-aggregate-provenance",
                f"Published aggregate source paths are incomplete or incorrect: {mismatched_paths}",
                relpath,
            )

        trajectory = _mapping(payload.get("trajectory"))
        trajectory_pipelines = _rows(trajectory.get("pipelines"))
        trajectory_pipeline = _mapping(trajectory_pipelines[0]) if trajectory_pipelines else {}
        trajectory_history = _mapping(
            _mapping(trajectory.get("provenance")).get("build_history")
        )
        if (
            trajectory.get("source_pipeline") != "ci"
            or trajectory.get("available") is not (reliability.get("available") is True)
            or trajectory.get("pipeline_order") != ["ci"]
            or len(trajectory_pipelines) != 1
            or trajectory_pipeline.get("pipeline") != "ci"
            or trajectory_pipeline.get("source_key") != "ci.all_main_reliability"
            or trajectory_pipeline.get("groups")
            != _safe_int(_mapping(reliability.get("denominator")).get("groups"))
            or trajectory_pipeline.get("observations")
            != _safe_int(_mapping(reliability.get("denominator")).get("observations"))
            or trajectory_pipeline.get("cohort") != cohort
            or trajectory_history.get("source_pipeline") != "ci"
            or trajectory_history.get("source_key") != "ci.all_main_reliability"
        ):
            self.error(
                "operations-trajectory-scope",
                "Workload trajectory must use only the strict upstream ci all-main cohort",
                relpath,
            )

        queue_history = _rows(queue_block.get("history"))
        if len(queue_history) < 2:
            self.error(
                "operations-queue-history",
                "Queue history must retain more than the current snapshot",
                relpath,
            )
        if "mi355b" in json.dumps(payload, sort_keys=True).lower():
            self.error(
                "operations-retired-mi355b",
                "operations_v2.json still contains retired amd_mi355B queues",
                relpath,
            )

        self.audit_agent_health(payload, relpath)

        self.report.metrics["operations_v2"] = {
            "active_targets": len(active),
            "linked_active_targets": linked_gating,
            "mixed_outcome_candidates": len(candidates),
            "reliability_groups": len(catalog),
            "reliability_observations": observations,
            "linked_reliability_observations": linked_observations,
            "queue_history_snapshots": len(queue_history),
            "canonical_nightlies": len(canonical_rows),
            "all_main_builds": cohort_builds,
            "all_main_nightlies": cohort_nightlies,
            "other_main_builds": cohort_other_main,
            "retry_attempts": len(retry_attempts),
            "retry_recoveries": len(recoveries),
        }

    def audit_agent_health(self, payload: dict, relpath: str) -> None:
        """Cross-check the pre-aggregated AMD CI agent-health block.

        collect_agent_health.py walks every build across all branches in the AMD
        pipelines and ships compact per-node/day reliability rollups plus the raw
        infra-suspect failing runs; the frontend aggregates the table and clusters
        co-failure events client-side. This audits the aggregated block's meta,
        rollup shape, and the AMD scoping of the shipped failing runs.
        """
        agent_health = _mapping(payload.get("amd_agent_health"))
        if not agent_health:
            self.error(
                "operations-agent-health-missing",
                "operations_v2.json omits the amd_agent_health block",
                relpath,
            )
            return
        for key in ("window_options", "cofailure_window_options"):
            if not _rows(agent_health.get(key)):
                self.error(
                    "operations-agent-health-options",
                    f"amd_agent_health must publish {key} for the control presets",
                    relpath,
                )
        if _safe_int(agent_health.get("default_cofailure_window_mins")) <= 0:
            self.error(
                "operations-agent-health-cofail-default",
                "amd_agent_health must publish a positive default_cofailure_window_mins",
                relpath,
            )

        def _is_amd_gpu_queue(queue: str) -> bool:
            # GPU-only scope (amd_mi<model>), excluding the retired amd_mi355b family.
            q = str(queue or "").casefold()
            return q.startswith("amd_mi") and not q.startswith("amd_mi355b")

        # Per-node/day rollup shape: [runs, soft, hard, canceled], soft+hard+canceled
        # never exceed runs, and every row carries an identifiable day + node.
        node_days = _rows(agent_health.get("node_days"))
        bad_rollups = 0
        for raw_row in node_days:
            row = _mapping(raw_row)
            if not row.get("d") or not row.get("nd"):
                bad_rollups += 1
                continue
            for bucket_key in ("a", "n"):
                bucket = _rows(row.get(bucket_key))
                if len(bucket) != 4:
                    bad_rollups += 1
                    break
                runs, soft, hard, canceled = (_safe_int(v) for v in bucket)
                if soft + hard + canceled > runs or min(runs, soft, hard, canceled) < 0:
                    bad_rollups += 1
                    break
        if bad_rollups:
            self.error(
                "operations-agent-health-rollup-shape",
                f"{bad_rollups} node_days rollup rows are malformed (bad bucket / day / node)",
                relpath,
            )

        # Every shipped failing run must be an infra-suspect AMD GPU failure.
        failing = _rows(agent_health.get("failing_runs"))
        non_amd = [
            _mapping(run).get("q")
            for run in failing
            if not _is_amd_gpu_queue(_mapping(run).get("q"))
        ]
        if non_amd:
            self.error(
                "operations-agent-health-queue-scope",
                f"{len(non_amd)} agent-health failing runs are on non-AMD-GPU queues, e.g. {non_amd[:3]}",
                relpath,
            )
        bad_state = [
            _mapping(run).get("s")
            for run in failing
            if _mapping(run).get("s") not in ("hard", "soft")
        ]
        if bad_state:
            self.error(
                "operations-agent-health-failing-state",
                f"{len(bad_state)} failing runs have a non-failure state, e.g. {bad_state[:3]}",
                relpath,
            )
        # The signal toggle needs an infra-suspect flag (1/0) on every failing run.
        bad_flag = [
            _mapping(run).get("i")
            for run in failing
            if _mapping(run).get("i") not in (0, 1)
        ]
        if bad_flag:
            self.error(
                "operations-agent-health-infra-flag",
                f"{len(bad_flag)} failing runs have a bad infra-suspect flag (i), e.g. {bad_flag[:3]}",
                relpath,
            )

    def audit_operations_bundle(self) -> None:
        relpath = "data/vllm/ci/operations_v2_manifest.json"
        manifest_path = self.root / relpath
        manifest = self.load_json(relpath, {})
        if not isinstance(manifest, dict):
            return
        if manifest.get("schema_version") != 2 or manifest.get("bundle_version") != 1:
            self.error(
                "operations-bundle-schema",
                "operations manifest must use schema_version=2 and bundle_version=1",
                relpath,
            )
        shell = _mapping(manifest.get("shell"))
        monolith = self.load_json("data/vllm/ci/operations_v2.json", {})
        if shell.get("generated_at") != _mapping(monolith).get("generated_at"):
            self.error(
                "operations-bundle-freshness",
                "operations shell and compatibility snapshot have different generation timestamps",
                relpath,
            )

        expected = {
            "nightly",
            "amd_test_health",
            "amd_agent_health",
            "reliability",
            "definition_parity",
            "gating",
            "queue",
            "trajectory",
            "omni",
            "diagnostics",
        }
        sections = _mapping(manifest.get("sections"))
        missing = expected - set(sections)
        if missing:
            self.error(
                "operations-bundle-sections",
                f"operations manifest is missing lazy sections: {sorted(missing)}",
                relpath,
            )

        section_sizes: dict[str, int] = {}
        root = manifest_path.parent.resolve()
        for name, raw_descriptor in sections.items():
            descriptor = _mapping(raw_descriptor)
            relative = str(descriptor.get("path") or "")
            path = (manifest_path.parent / relative).resolve()
            if not relative or not path.is_relative_to(root):
                self.error(
                    "operations-bundle-path",
                    f"section {name} has an unsafe path: {relative!r}",
                    relpath,
                )
                continue
            if not path.exists():
                declared_size = _safe_int(descriptor.get("bytes"))
                section_sizes[name] = declared_size
                continue
            size = path.stat().st_size
            section_sizes[name] = size
            if _safe_int(descriptor.get("bytes")) != size:
                self.error(
                    "operations-bundle-size",
                    f"section {name} reports {descriptor.get('bytes')} bytes but file size is {size}",
                    relpath,
                )
            try:
                section = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError, UnicodeError) as exc:
                self.error(
                    "operations-bundle-json",
                    f"section {name} is not valid JSON: {exc}",
                    self.rel(path),
                )
                continue
            if not isinstance(section, dict):
                self.error(
                    "operations-bundle-shape",
                    f"section {name} must contain a JSON object",
                    self.rel(path),
                )

        manifest_size = manifest_path.stat().st_size if manifest_path.exists() else 0
        home_budget = 2_000_000
        health_budget = 12_000_000
        queue_budget = 6_000_000
        health_initial = manifest_size + section_sizes.get("nightly", 0) + section_sizes.get("amd_test_health", 0)
        if manifest_size > home_budget:
            self.error(
                "operations-home-payload-budget",
                f"first-render shell is {manifest_size} bytes; budget is {home_budget}",
                relpath,
            )
        if health_initial > health_budget:
            self.error(
                "operations-health-payload-budget",
                f"CI Health overview payload is {health_initial} bytes; budget is {health_budget}",
                relpath,
            )
        if section_sizes.get("queue", 0) > queue_budget:
            self.error(
                "operations-queue-payload-budget",
                f"queue section is {section_sizes['queue']} bytes; budget is {queue_budget}",
                relpath,
            )
        self.report.metrics["operations_bundle"] = {
            "manifest_bytes": manifest_size,
            "health_overview_bytes": health_initial,
            "section_bytes": section_sizes,
        }

    def audit_home_pr_issue_data(self) -> None:
        prs_payload = self.load_json("data/vllm/prs.json", {})
        issues_payload = self.load_json("data/vllm/issues.json", {})
        prs = prs_payload.get("prs") if isinstance(prs_payload, dict) else []
        issues = issues_payload.get("issues") if isinstance(issues_payload, dict) else []
        if not isinstance(prs, list) or not isinstance(issues, list):
            self.error("home-shape", "prs.json/issues.json must contain list payloads")
            return

        repo = "vllm-project/vllm"
        pr_by_number = {p.get("number"): p for p in prs if isinstance(p, dict)}
        issue_by_number = {i.get("number"): i for i in issues if isinstance(i, dict)}
        linked_refs = 0

        for issue in issues:
            if (issue.get("state") or "").lower() != "open":
                self.error(
                    "project-issue-not-open",
                    f"Issue #{issue.get('number')} is in issues.json but is not open",
                    "data/vllm/issues.json",
                )
            if issue.get("repo") and issue.get("repo") != repo:
                self.error(
                    "project-issue-repo",
                    f"Issue #{issue.get('number')} belongs to {issue.get('repo')}, expected {repo}",
                    "data/vllm/issues.json",
                )
            if "projects/39" not in (issue.get("project_url") or ""):
                self.error(
                    "project-issue-source",
                    f"Issue #{issue.get('number')} is missing the project #39 source URL",
                    "data/vllm/issues.json",
                )
            for ref in issue.get("linked_prs") or []:
                if not same_repo(ref.get("repo"), repo):
                    continue
                number = ref.get("number")
                linked_refs += 1
                if number not in pr_by_number:
                    self.error(
                        "linked-ci-pr-missing",
                        f"Issue #{issue.get('number')} links PR #{number}, but prs.json does not include it",
                        "data/vllm/prs.json",
                    )
                    continue
                pr = pr_by_number[number]
                if not pr.get("is_ci_pr"):
                    self.error(
                        "linked-ci-pr-untagged",
                        f"PR #{number} is linked from issue #{issue.get('number')} but is_ci_pr is false",
                        "data/vllm/prs.json",
                    )
                if issue.get("number") not in (pr.get("ci_issue_numbers") or []):
                    self.error(
                        "ci-pr-issue-backlink",
                        f"PR #{number} lacks ci_issue_numbers backlink to issue #{issue.get('number')}",
                        "data/vllm/prs.json",
                    )

        for pr in prs:
            number = pr.get("number")
            labels = {str(label).lower() for label in pr.get("labels") or []}
            expected_rocm = "rocm" in labels
            if bool(pr.get("is_rocm_pr")) != expected_rocm:
                self.error(
                    "rocm-pr-tag",
                    f"PR #{number} has is_rocm_pr={pr.get('is_rocm_pr')} but labels={sorted(labels)}",
                    "data/vllm/prs.json",
                )
            ci_issue_numbers = pr.get("ci_issue_numbers") or []
            if bool(pr.get("is_ci_pr")) != bool(ci_issue_numbers):
                self.error(
                    "ci-pr-tag",
                    f"PR #{number} has inconsistent is_ci_pr and ci_issue_numbers",
                    "data/vllm/prs.json",
                )
            for issue_number in ci_issue_numbers:
                if issue_number not in issue_by_number:
                    self.error(
                        "ci-pr-issue-missing",
                        f"PR #{number} points at issue #{issue_number}, but issues.json does not include it",
                        "data/vllm/issues.json",
                    )
            tags = pr.get("custom_tags") or []
            if pr.get("is_ci_pr") and "CI" not in tags:
                self.error("ci-custom-tag", f"PR #{number} is CI but missing custom CI tag")
            if pr.get("is_rocm_pr") and "ROCm" not in tags:
                self.error("rocm-custom-tag", f"PR #{number} is ROCm but missing custom ROCm tag")

        open_prs = [p for p in prs if (p.get("state") or "").lower() == "open"]
        self.report.metrics["home"] = {
            "prs": len(prs),
            "open_prs": len(open_prs),
            "ci_prs": sum(1 for p in open_prs if p.get("is_ci_pr")),
            "rocm_prs": sum(1 for p in open_prs if p.get("is_rocm_pr")),
            "project_issues": len(issues),
            "linked_issue_pr_refs": linked_refs,
        }

    def audit_ci_health(self) -> None:
        health = self.load_json("data/vllm/ci/ci_health.json", {})
        if not isinstance(health, dict):
            return

        metrics: dict[str, Any] = {}
        for side, suffix in (("amd", "amd"), ("upstream", "upstream")):
            latest = ((health.get(side) or {}).get("latest_build") or {})
            if not latest:
                self.error("ci-health-latest", f"ci_health.json lacks {side}.latest_build")
                continue
            path = self.latest_result_file(suffix)
            result_numbers = self.build_numbers_in_jsonl(path)
            build_number = latest.get("build_number") or latest.get("number")
            if result_numbers and build_number not in result_numbers:
                self.error(
                    "ci-health-jsonl-build-mismatch",
                    f"{side} ci_health latest build #{build_number} does not match {path.name} build numbers {sorted(result_numbers)}",
                    "data/vllm/ci/ci_health.json",
                )
            total = latest.get("total_tests", 0)
            counted = latest.get("passed", 0) + latest.get("failed", 0) + latest.get("skipped", 0)
            if total != counted:
                self.error(
                    "ci-health-total",
                    f"{side} total_tests={total} but passed+failed+skipped={counted}",
                    "data/vllm/ci/ci_health.json",
                )
            metrics[side] = {
                "build_number": build_number,
                "total_tests": total,
                "groups": latest.get("unique_test_groups"),
                "by_hardware": {
                    hw: row.get("groups")
                    for hw, row in (latest.get("by_hardware") or {}).items()
                    if str(hw).startswith("mi")
                },
            }
        self.report.metrics["ci_health"] = metrics

    def audit_gating_target_candidates(self) -> None:
        payload = self.load_json("data/vllm/ci/gating_target_candidates.json", {})
        if not isinstance(payload, dict):
            return
        rows = payload.get("rows") or []
        if not isinstance(rows, list):
            self.error(
                "gating-target-candidates-shape",
                "gating_target_candidates.json rows must be a list",
                "data/vllm/ci/gating_target_candidates.json",
            )
            return
        default_gpu_offenders = [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("decision") == "excluded"
            and "not_gpu_like" in (row.get("exclusion_reasons") or [])
            and re.search(r"(^|[^a-z0-9])gpu_", str(row.get("queue") or ""), re.IGNORECASE)
        ]
        if default_gpu_offenders:
            examples = "; ".join(
                f"{row.get('label')} on {row.get('queue')}"
                for row in default_gpu_offenders[:5]
            )
            self.error(
                "gating-target-gpu-queue-excluded",
                "Default Buildkite GPU queue rows must not be excluded as not_gpu_like. "
                f"Examples: {examples}",
                "data/vllm/ci/gating_target_candidates.json",
            )
        self.report.metrics["gating_target_candidates"] = {
            "rows": len(rows),
            "default_gpu_not_gpu_like_exclusions": len(default_gpu_offenders),
            "summary": payload.get("summary") or {},
        }

    def audit_analytics(self) -> None:
        analytics = self.load_json("data/vllm/ci/analytics.json", {})
        if not isinstance(analytics, dict):
            return
        metrics: dict[str, Any] = {}

        for slug in ("amd-ci", "ci"):
            block = analytics.get(slug)
            if not isinstance(block, dict):
                self.error("analytics-pipeline-missing", f"analytics.json missing {slug}")
                continue
            builds = _rows(block.get("builds"))
            if not builds:
                self.error("analytics-empty-builds", f"{slug} analytics has no builds")
                continue

            suffix = RESULT_SUFFIXES[slug]
            latest_results = self.latest_result_file(suffix)
            result_numbers = self.build_numbers_in_jsonl(latest_results)
            latest = _mapping(builds[0])
            if result_numbers and latest.get("number") not in result_numbers:
                self.error(
                    "analytics-jsonl-build-mismatch",
                    f"{slug} latest analytics build #{latest.get('number')} does not match {latest_results.name} build numbers {sorted(result_numbers)}",
                    "data/vllm/ci/analytics.json",
                )
            if result_numbers and latest.get("source") != "test_results":
                self.warning(
                    "analytics-source",
                    f"{slug} latest build is not sourced from parsed test_results",
                    "data/vllm/ci/analytics.json",
                )

            windows = _mapping(block.get("windows"))
            default_window = block.get("default_window")
            if default_window not in windows:
                self.error(
                    "analytics-default-window",
                    f"{slug} default_window={default_window!r} is absent from windows",
                    "data/vllm/ci/analytics.json",
                )
            for key in ("1d", "3d", "7d", "14d", "30d"):
                if key not in windows:
                    self.error(
                        "analytics-window-missing",
                        f"{slug} missing precomputed {key} window",
                        "data/vllm/ci/analytics.json",
                    )

            chartable_builds = [
                b
                for b in _rows(_mapping(windows.get(default_window)).get("builds")) or builds
                if isinstance(b, dict) and _safe_int(b.get("total_jobs")) > 10
            ]
            if len(chartable_builds) < 2:
                self.error(
                    "analytics-chart-empty",
                    f"{slug} default window has fewer than two chartable builds",
                    "data/vllm/ci/analytics.json",
                )

            for raw_build in builds[:20]:
                build = _mapping(raw_build)
                jobs = _rows(build.get("jobs"))
                if build.get("total_jobs") != len(jobs):
                    self.error(
                        "analytics-total-jobs",
                        f"{slug} build #{build.get('number')} total_jobs={build.get('total_jobs')} but has {len(jobs)} jobs",
                        "data/vllm/ci/analytics.json",
                    )
                state_counts = {
                    "passed": sum(
                        1 for j in jobs
                        if isinstance(j, dict) and j.get("state") == "passed"
                    ),
                    "failed": sum(
                        1
                        for j in jobs
                        if isinstance(j, dict)
                        and j.get("state") in {"failed", "timed_out", "broken"}
                    ),
                    "soft_failed": sum(
                        1 for j in jobs
                        if isinstance(j, dict) and j.get("state") == "soft_fail"
                    ),
                    "skipped": sum(
                        1 for j in jobs
                        if isinstance(j, dict) and j.get("state") == "skipped"
                    ),
                }
                for key, expected in state_counts.items():
                    if build.get(key, 0) != expected:
                        self.error(
                            "analytics-job-counts",
                            f"{slug} build #{build.get('number')} {key}={build.get(key)} but jobs imply {expected}",
                            "data/vllm/ci/analytics.json",
                        )

            rankings = _rows(block.get("duration_ranking"))
            too_long = [
                row
                for row in rankings
                if isinstance(row, dict)
                and isinstance(row.get("median_dur"), (int, float))
                and row["median_dur"] > 360
            ]
            if too_long:
                self.warning(
                    "analytics-duration-units",
                    f"{slug} has median job durations over 6h; check seconds/minutes conversion",
                    "data/vllm/ci/analytics.json",
                )
            all_main_metrics = None
            all_main = block.get("all_main_reliability") or {}
            if slug == "amd-ci":
                if all_main or block.get("main_retry_analysis"):
                    self.error(
                        "analytics-amd-historical-reliability",
                        "AMD analytics must not publish historical reliability or flake/retry ledgers",
                        "data/vllm/ci/analytics.json",
                    )
                metrics[slug] = {
                    "builds": len(builds),
                    "latest_build": latest.get("number"),
                    "latest_source": latest.get("source"),
                    "default_window": default_window,
                    "failure_rankings": len(_rows(block.get("failure_ranking"))),
                    "duration_rankings": len(_rows(block.get("duration_ranking"))),
                }
                continue
            if not isinstance(all_main, dict) or not isinstance(all_main.get("groups"), list):
                self.error(
                    "analytics-all-main-missing",
                    f"{slug} analytics must retain a separate all-main reliability cohort",
                    "data/vllm/ci/analytics.json",
                )
            else:
                cohort = _mapping(all_main.get("cohort"))
                denominator = _mapping(all_main.get("denominator"))
                groups = _rows(all_main.get("groups"))
                provenance = _mapping(all_main.get("provenance"))
                collection = _mapping(provenance.get("collection"))
                cohort_builds_rows = _rows(all_main.get("builds"))
                build_states = cohort.get("build_states")
                expected_endpoint = f"/organizations/vllm/pipelines/{slug}/builds"
                if (
                    cohort.get("id") != f"{slug}-main-completed-pass-fail"
                    or cohort.get("branch") != "main"
                    or cohort.get("pipeline") != slug
                    or not isinstance(build_states, list)
                    or any(not isinstance(state, str) for state in build_states)
                    or set(build_states) != {"failed", "passed"}
                    or provenance.get("pipeline") != slug
                    or _mapping(provenance.get("query")).get("branch") != "main"
                    or not str(provenance.get("endpoint") or "").endswith(expected_endpoint)
                    or cohort.get("exhaustive") is not True
                    or collection.get("exhaustive") is not True
                ):
                    self.error(
                        "analytics-all-main-branch",
                        (
                            f"{slug} all-main cohort does not prove the strict completed branch=main "
                            "Buildkite query"
                        ),
                        "data/vllm/ci/analytics.json",
                    )
                malformed_builds = [
                    row
                    for row in cohort_builds_rows
                    if not isinstance(row, dict)
                    or row.get("branch") != "main"
                    or str(row.get("state") or "").lower() not in {"failed", "passed"}
                    or not row.get("finished_at")
                    or not _buildkite_url_matches(row.get("url"), slug, row.get("number"))
                ]
                if malformed_builds or len(cohort_builds_rows) != _safe_int(cohort.get("build_count")):
                    self.error(
                        "analytics-all-main-build-provenance",
                        (
                            f"{slug} strict cohort has {len(malformed_builds)} malformed builds and "
                            f"{len(cohort_builds_rows)} rows for declared count {cohort.get('build_count')}"
                        ),
                        "data/vllm/ci/analytics.json",
                    )
                cohort_builds = _safe_int(cohort.get("build_count"))
                cohort_nightlies = _safe_int(cohort.get("canonical_nightly_build_count"))
                cohort_other_main = _safe_int(cohort.get("non_nightly_main_build_count"))
                if cohort_builds != cohort_nightlies + cohort_other_main:
                    self.error(
                        "analytics-all-main-cohort-composition",
                        (
                            f"all-main builds={cohort_builds}, canonical nightlies={cohort_nightlies}, "
                            f"other main={cohort_other_main}"
                        ),
                        "data/vllm/ci/analytics.json",
                    )
                eligible = sum(
                    _safe_int(_mapping(group).get("denominator")) for group in groups
                )
                expected_eligible = _safe_int(denominator.get("eligible_observations"))
                if eligible != expected_eligible:
                    self.error(
                        "analytics-all-main-denominator",
                        f"group denominators sum to {eligible}, cohort reports {expected_eligible}",
                        "data/vllm/ci/analytics.json",
                    )
                retained = [
                    observation
                    for group in groups
                    for observation in _rows(_mapping(group).get("observations"))
                    if isinstance(observation, dict)
                    and observation.get("eligible_for_reliability")
                ]
                all_observations = [
                    observation
                    for group in groups
                    for observation in _rows(_mapping(group).get("observations"))
                    if isinstance(observation, dict)
                ]
                cohort_build_numbers = {
                    _safe_int(_mapping(row).get("number"), -1)
                    for row in cohort_builds_rows
                    if _safe_int(_mapping(row).get("number"), -1) > 0
                }
                missing_links = sum(not observation.get("job_url") for observation in all_observations)
                wrong_pipeline_links = sum(
                    _safe_int(observation.get("build_number"), -1) not in cohort_build_numbers
                    or not _buildkite_url_matches(
                        observation.get("build_url"), slug, observation.get("build_number")
                    )
                    or not _buildkite_url_matches(
                        observation.get("job_url"),
                        slug,
                        observation.get("build_number"),
                        require_job=True,
                    )
                    for observation in all_observations
                )
                if missing_links or wrong_pipeline_links:
                    self.error(
                        "analytics-all-main-links",
                        (
                            f"{missing_links} retained observations lack an exact job URL and "
                            f"{wrong_pipeline_links} link to a different pipeline"
                        ),
                        "data/vllm/ci/analytics.json",
                    )
                retry = _mapping(block.get("main_retry_analysis"))
                retry_provenance = _mapping(retry.get("provenance"))
                attempts = _rows(retry.get("retry_attempts"))
                recoveries = _rows(retry.get("failed_then_passed_recoveries"))
                if retry.get("available") is True:
                    retry_cohort = _strict_positive_int_set(
                        retry_provenance.get("cohort_build_numbers")
                    )
                    invalid_retry = (
                        retry_provenance.get("source_pipeline") != "ci"
                        or retry_provenance.get("complete") is not True
                        or retry_cohort != cohort_build_numbers
                        or any(
                            not isinstance(row, dict)
                            or _safe_int(row.get("build_number"), -1) not in cohort_build_numbers
                            or not _buildkite_url_matches(
                                row.get("job_url") or row.get("url"),
                                "ci",
                                row.get("build_number"),
                                require_job=True,
                            )
                            for row in attempts
                        )
                        or any(
                            not isinstance(row, dict)
                            or _safe_int(row.get("build_number"), -1) not in cohort_build_numbers
                            or not _buildkite_url_matches(
                                row.get("failed_url"),
                                "ci",
                                row.get("build_number"),
                                require_job=True,
                            )
                            or not _buildkite_url_matches(
                                row.get("passed_url"),
                                "ci",
                                row.get("build_number"),
                                require_job=True,
                            )
                            for row in recoveries
                        )
                    )
                    if invalid_retry:
                        self.error(
                            "analytics-main-retry-provenance",
                            "CI retry evidence is not transitively bound to the exhaustive upstream cohort",
                            "data/vllm/ci/analytics.json",
                        )
                elif attempts or recoveries:
                    self.error(
                        "analytics-main-retry-unavailable-rows",
                        "Unavailable retry analysis must not publish partial attempt rows",
                        "data/vllm/ci/analytics.json",
                    )
                retired = [
                    _mapping(group).get("name")
                    for group in groups
                    if is_retired_queue(_mapping(group).get("queue"))
                ]
                if retired:
                    self.error(
                        "analytics-retired-queue",
                        f"all-main reliability contains {len(retired)} retired queue variants",
                        "data/vllm/ci/analytics.json",
                    )
                all_main_metrics = {
                    "builds": cohort.get("build_count"),
                    "nightlies": cohort.get("canonical_nightly_build_count"),
                    "non_nightly_main": cohort.get("non_nightly_main_build_count"),
                    "groups": len(groups),
                    "eligible_observations": eligible,
                    "retained_linked_observations": len(all_observations) - missing_links,
                }
            metrics[slug] = {
                "builds": len(builds),
                "latest_build": latest.get("number"),
                "latest_source": latest.get("source"),
                "default_window": default_window,
                "failure_rankings": len(_rows(block.get("failure_ranking"))),
                "duration_rankings": len(_rows(block.get("duration_ranking"))),
            }
            if all_main_metrics is not None:
                metrics[slug]["all_main"] = all_main_metrics
        self.report.metrics["analytics"] = metrics

    def matrix_cell_stats(self, matrix: dict[str, Any]) -> dict[str, Any]:
        architectures = [a.get("id") for a in matrix.get("architectures") or [] if a.get("id")]
        rows = matrix.get("rows") or []
        stats: dict[str, Any] = {
            "unique_groups": len(rows),
            "architecture_count": len(architectures),
            "hardware_cells": 0,
            "latest_matched_cells": 0,
            "passing_cells": 0,
            "failing_cells": 0,
            "waiting_cells": 0,
            "unknown_cells": 0,
            "fully_shared_groups": 0,
            "single_arch_groups": 0,
            "multi_variant_cells": 0,
            "attention_families": 0,
            "by_arch": {
                arch: {"total": 0, "matched": 0, "passing": 0, "failing": 0, "waiting": 0, "unknown": 0}
                for arch in architectures
            },
        }

        for row in rows:
            row_coverage = 0
            row_nightly = 0
            row_attention = False
            for arch in architectures:
                cell = ((row.get("cells") or {}).get(arch) or {})
                if not cell.get("exists"):
                    continue
                row_coverage += 1
                stats["hardware_cells"] += 1
                stats["by_arch"][arch]["total"] += 1

                raw_variant_count = cell.get("raw_variant_count", cell.get("variant_count", 0))
                if raw_variant_count > 1:
                    stats["multi_variant_cells"] += 1

                if cell.get("latest_matched"):
                    row_nightly += 1
                    stats["latest_matched_cells"] += 1
                    stats["by_arch"][arch]["matched"] += 1
                else:
                    row_attention = True

                state = cell.get("latest_state")
                if state == "passed":
                    stats["passing_cells"] += 1
                    stats["by_arch"][arch]["passing"] += 1
                elif state in AMD_FAILURE_STATES:
                    row_attention = True
                    stats["failing_cells"] += 1
                    stats["by_arch"][arch]["failing"] += 1
                elif state in AMD_WAITING_STATES:
                    row_attention = True
                    stats["waiting_cells"] += 1
                    stats["by_arch"][arch]["waiting"] += 1
                else:
                    stats["unknown_cells"] += 1
                    stats["by_arch"][arch]["unknown"] += 1

            if row_coverage == len(architectures):
                stats["fully_shared_groups"] += 1
            if row_coverage == 1:
                stats["single_arch_groups"] += 1
            if row_attention:
                stats["attention_families"] += 1
            if row.get("coverage_count") != row_coverage:
                self.error(
                    "matrix-row-coverage",
                    f"{row.get('title')} coverage_count={row.get('coverage_count')} but cells imply {row_coverage}",
                    "data/vllm/ci/amd_test_matrix.json",
                )
            if row.get("nightly_coverage_count") != row_nightly:
                self.error(
                    "matrix-row-nightly-coverage",
                    f"{row.get('title')} nightly_coverage_count={row.get('nightly_coverage_count')} but cells imply {row_nightly}",
                    "data/vllm/ci/amd_test_matrix.json",
                )
        return stats

    def matrix_health_policy_stats(
        self,
        rows: list[dict[str, Any]],
        *,
        reduce_duplicates: bool,
        ignore_mi355_only: bool,
    ) -> dict[str, Any]:
        components: dict[str, list[dict[str, Any]]] = {}
        for index, row in enumerate(rows):
            group_id = str(
                row.get("duplicate_group_id")
                or row.get("id")
                or f"legacy-row-{index}"
            )
            components.setdefault(group_id, []).append(row)

        if reduce_duplicates:
            candidates = [
                (members, members)
                for members in components.values()
            ]
        else:
            candidates = [
                ([row], components[str(
                    row.get("duplicate_group_id")
                    or row.get("id")
                    or f"legacy-row-{index}"
                )])
                for index, row in enumerate(rows)
            ]

        def cells(
            selected_rows: list[dict[str, Any]],
            architectures: set[str],
        ) -> list[dict[str, Any]]:
            return [
                cell
                for row in selected_rows
                for arch, cell in (row.get("cells") or {}).items()
                if arch in architectures and cell.get("exists")
            ]

        counts = {
            "passing_groups": 0,
            "failed_only_groups": 0,
            "mixed_groups": 0,
            "waiting_groups": 0,
            "unknown_groups": 0,
            "ignored_mi355_only_groups": 0,
            "inherited_mi355_groups": 0,
        }
        core_architectures = {"mi250", "mi300", "mi325"}
        for candidate_rows, component_rows in candidates:
            candidate_core = cells(candidate_rows, core_architectures)
            candidate_mi355 = cells(candidate_rows, {"mi355"})
            component_core = cells(component_rows, core_architectures)
            if candidate_core:
                signal_cells = candidate_core
                if candidate_mi355:
                    counts["inherited_mi355_groups"] += 1
            elif component_core:
                signal_cells = component_core
                counts["inherited_mi355_groups"] += 1
            elif ignore_mi355_only:
                counts["ignored_mi355_only_groups"] += 1
                continue
            else:
                signal_cells = candidate_mi355

            states = {
                str(cell.get("latest_state") or "").casefold()
                for cell in signal_cells
            }
            has_pass = "passed" in states
            has_incident = bool(states & AMD_FAILURE_STATES)
            if has_pass and has_incident:
                counts["mixed_groups"] += 1
            elif has_pass:
                counts["passing_groups"] += 1
            elif has_incident:
                counts["failed_only_groups"] += 1
            elif states & AMD_WAITING_STATES:
                counts["waiting_groups"] += 1
            else:
                counts["unknown_groups"] += 1

        counts["failing_groups"] = (
            counts["failed_only_groups"] + counts["mixed_groups"]
        )
        counts["resolved_groups"] = (
            counts["passing_groups"] + counts["failing_groups"]
        )
        counts["included_groups"] = (
            counts["resolved_groups"]
            + counts["waiting_groups"]
            + counts["unknown_groups"]
        )
        counts["pass_percentage"] = round(
            counts["passing_groups"] / counts["resolved_groups"] * 100,
            1,
        ) if counts["resolved_groups"] else None
        counts["reduce_duplicates"] = reduce_duplicates
        counts["ignore_mi355_only"] = ignore_mi355_only
        return counts

    def audit_amd_matrix(self) -> None:
        matrix = self.load_json("data/vllm/ci/amd_test_matrix.json", {})
        if not isinstance(matrix, dict):
            return
        rows = matrix.get("rows") or []
        if not rows:
            self.error("matrix-empty", "amd_test_matrix.json has no rows")
            return

        stats = self.matrix_cell_stats(matrix)
        summary = matrix.get("summary") or {}
        for key in (
            "unique_groups",
            "architecture_count",
            "hardware_cells",
            "latest_matched_cells",
            "passing_cells",
            "failing_cells",
            "waiting_cells",
            "unknown_cells",
            "fully_shared_groups",
            "single_arch_groups",
            "multi_variant_cells",
        ):
            if summary.get(key) != stats[key]:
                self.error(
                    "matrix-summary",
                    f"summary.{key}={summary.get(key)} but rows imply {stats[key]}",
                    "data/vllm/ci/amd_test_matrix.json",
                )

        health_policies = summary.get("health_policies") or {}
        if health_policies:
            row_by_id = {row.get("id"): row for row in rows}
            group_ids = {
                row.get("duplicate_group_id")
                for row in rows
                if row.get("duplicate_group_id")
            }
            duplicate_groups = matrix.get("duplicate_groups") or []
            duplicate_rows = 0
            for group in duplicate_groups:
                member_ids = group.get("member_ids") or []
                duplicate_rows += len(member_ids)
                members = [row_by_id.get(member_id) for member_id in member_ids]
                if len(member_ids) < 2 or any(member is None for member in members):
                    self.error(
                        "matrix-duplicate-members",
                        f"duplicate group {group.get('id')} has invalid members",
                        "data/vllm/ci/amd_test_matrix.json",
                    )
                    continue
                fingerprints = {
                    member.get("command_fingerprint")
                    for member in members
                    if member
                }
                if len(fingerprints) != 1:
                    self.error(
                        "matrix-duplicate-commands",
                        f"duplicate group {group.get('id')} spans command fingerprints",
                        "data/vllm/ci/amd_test_matrix.json",
                    )
                if any(
                    len(str(match.get("shared_substring") or "")) < 2
                    for match in group.get("pair_matches") or []
                ):
                    self.error(
                        "matrix-duplicate-title-rule",
                        f"duplicate group {group.get('id')} contains a title match shorter than 2",
                        "data/vllm/ci/amd_test_matrix.json",
                    )

            expected_summary = {
                "definition_rows": len(rows),
                "reduced_unique_groups": len(group_ids),
                "duplicate_clusters": len(duplicate_groups),
                "duplicate_definition_rows": duplicate_rows,
            }
            for key, expected in expected_summary.items():
                if summary.get(key) != expected:
                    self.error(
                        "matrix-unique-summary",
                        f"summary.{key}={summary.get(key)} but rows imply {expected}",
                        "data/vllm/ci/amd_test_matrix.json",
                    )

            policy_specs = {
                "reduced_ignore_mi355": (True, True),
                "reduced_include_mi355": (True, False),
                "definitions_ignore_mi355": (False, True),
                "definitions_include_mi355": (False, False),
            }
            for key, flags in policy_specs.items():
                expected = self.matrix_health_policy_stats(
                    rows,
                    reduce_duplicates=flags[0],
                    ignore_mi355_only=flags[1],
                )
                if health_policies.get(key) != expected:
                    self.error(
                        "matrix-health-policy",
                        f"summary.health_policies.{key} does not reconcile with matrix rows",
                        "data/vllm/ci/amd_test_matrix.json",
                    )

        source = matrix.get("source") or {}
        source_build = source.get("latest_build_number")
        analytics = self.load_json("data/vllm/ci/analytics.json", {})
        health = self.load_json("data/vllm/ci/ci_health.json", {})
        analytics_build = (((analytics.get("amd-ci") or {}).get("builds") or [{}])[0]).get("number")
        health_build = ((health.get("amd") or {}).get("latest_build") or {}).get("build_number")
        if analytics_build and source_build != analytics_build:
            self.error(
                "matrix-analytics-build",
                f"matrix source build #{source_build} does not match analytics AMD latest #{analytics_build}",
                "data/vllm/ci/amd_test_matrix.json",
            )
        if health_build and source_build != health_build:
            self.error(
                "matrix-health-build",
                f"matrix source build #{source_build} does not match ci_health AMD latest #{health_build}",
                "data/vllm/ci/amd_test_matrix.json",
            )

        arch_counts = {a.get("id"): a for a in matrix.get("architectures") or []}
        for arch, arch_stats in stats["by_arch"].items():
            record = arch_counts.get(arch) or {}
            if record.get("group_count") != arch_stats["total"]:
                self.error(
                    "matrix-arch-group-count",
                    f"{arch} group_count={record.get('group_count')} but rows imply {arch_stats['total']}",
                    "data/vllm/ci/amd_test_matrix.json",
                )
            if record.get("nightly_match_count") != arch_stats["matched"]:
                self.error(
                    "matrix-arch-nightly-count",
                    f"{arch} nightly_match_count={record.get('nightly_match_count')} but rows imply {arch_stats['matched']}",
                    "data/vllm/ci/amd_test_matrix.json",
                )
            health_groups = (
                ((health.get("amd") or {}).get("latest_build") or {})
                .get("by_hardware", {})
                .get(arch, {})
                .get("groups")
            )
            # ci_health counts groups observed in the latest build. The matrix
            # total also includes configured cells that were not present in that
            # build, so its like-for-like denominator is the matched cell count.
            observed_groups = arch_stats["matched"]
            if health_groups is not None and health_groups != observed_groups:
                terminal_observed = observed_groups - arch_stats["waiting"]
                diff = abs(int(health_groups) - int(observed_groups))
                if terminal_observed <= health_groups <= observed_groups:
                    self.warning(
                        "matrix-health-hardware-count-in-progress",
                        f"{arch} matrix matched groups={observed_groups} including "
                        f"{arch_stats['waiting']} waiting cells; ci_health by_hardware "
                        f"currently reports {health_groups} observed terminal groups",
                        "data/vllm/ci/amd_test_matrix.json",
                    )
                elif diff <= CROSS_VIEW_GROUP_DRIFT_TOLERANCE:
                    self.warning(
                        "matrix-health-hardware-count-drift",
                        f"{arch} matrix matched groups={observed_groups} but ci_health "
                        f"by_hardware groups={health_groups}; allowing one-group "
                        "cross-view collector drift",
                        "data/vllm/ci/amd_test_matrix.json",
                    )
                else:
                    self.error(
                        "matrix-health-hardware-count",
                        f"{arch} matrix matched groups={observed_groups} but ci_health "
                        f"by_hardware groups={health_groups}",
                        "data/vllm/ci/amd_test_matrix.json",
                    )

        latest_url_build_re = re.compile(r"/builds/(\d+)")
        stale_urls: list[str] = []
        for row in rows:
            for cell in (row.get("cells") or {}).values():
                if not cell.get("exists"):
                    continue
                candidates = [cell.get("latest_url")]
                for variant in cell.get("variants") or []:
                    candidates.append(variant.get("latest_url"))
                    for entry in variant.get("entries") or []:
                        candidates.append(entry.get("latest_url"))
                for url in candidates:
                    if not url:
                        continue
                    match = latest_url_build_re.search(str(url))
                    if match and source_build and int(match.group(1)) != int(source_build):
                        stale_urls.append(str(url))
        if stale_urls:
            self.error(
                "matrix-stale-build-link",
                f"{len(stale_urls)} AMD matrix links point at a build other than #{source_build}",
                "data/vllm/ci/amd_test_matrix.json",
            )

        self.audit_parity_hardware_matches_matrix(matrix, stats)
        self.report.metrics["amd_matrix"] = {
            **{k: v for k, v in stats.items() if k != "by_arch"},
            "by_arch": stats["by_arch"],
            "latest_build": source_build,
        }

    def audit_parity_hardware_matches_matrix(
        self,
        matrix: dict[str, Any],
        matrix_stats: dict[str, Any],
    ) -> None:
        parity = self.load_json("data/vllm/ci/parity_report.json", {})
        if not isinstance(parity, dict):
            return
        parity_stats: dict[str, dict[str, int]] = {}
        for group in parity.get("job_groups") or []:
            for hw in group.get("hardware") or []:
                if not re.match(r"^mi\d+", str(hw), flags=re.I):
                    continue
                stats = parity_stats.setdefault(
                    hw,
                    {"passing": 0, "failing": 0, "pending": 0, "canceled": 0, "total": 0},
                )
                pending = bool(group.get("backfilled") or (group.get("hw_backfilled") or {}).get(hw))
                failed = (group.get("hw_failures") or {}).get(hw, 0) > 0
                canceled = (group.get("hw_canceled") or {}).get(hw, 0) > 0 and not failed
                if pending:
                    stats["pending"] += 1
                elif failed:
                    stats["failing"] += 1
                elif canceled:
                    stats["canceled"] += 1
                else:
                    stats["passing"] += 1
                stats["total"] += 1

        for arch, mstats in matrix_stats["by_arch"].items():
            pstats = parity_stats.get(arch, {})
            if pstats.get("total") != mstats["total"]:
                diff = abs((pstats.get("total") or 0) - mstats["total"])
                if diff <= CROSS_VIEW_GROUP_DRIFT_TOLERANCE:
                    self.warning(
                        "parity-matrix-hardware-total-drift",
                        f"{arch} parity hardware total={pstats.get('total')} and AMD "
                        f"matrix total={mstats['total']} differ by one group; allowing "
                        "cross-view collector drift",
                        "data/vllm/ci/parity_report.json",
                    )
                else:
                    self.error(
                        "parity-matrix-hardware-total",
                        f"{arch} parity hardware total={pstats.get('total')} but AMD matrix total={mstats['total']}",
                        "data/vllm/ci/parity_report.json",
                    )
            parity_failing = pstats.get("failing")
            if parity_failing != mstats["failing"]:
                diff = abs((parity_failing or 0) - mstats["failing"])
                if mstats.get("waiting", 0) and diff <= mstats["waiting"]:
                    self.warning(
                        "parity-matrix-hardware-failing-in-progress",
                        f"{arch} parity failing groups={parity_failing} and AMD matrix "
                        f"failing cells={mstats['failing']} differ by {diff} while "
                        f"{mstats['waiting']} matrix cells are still waiting",
                        "data/vllm/ci/parity_report.json",
                    )
                elif diff <= CROSS_VIEW_GROUP_DRIFT_TOLERANCE:
                    self.warning(
                        "parity-matrix-hardware-failing-drift",
                        f"{arch} parity failing groups={parity_failing} and AMD matrix "
                        f"failing cells={mstats['failing']} differ by one group; "
                        "allowing cross-view collector drift",
                        "data/vllm/ci/parity_report.json",
                    )
                else:
                    self.error(
                        "parity-matrix-hardware-failing",
                        f"{arch} parity failing groups={parity_failing} but AMD matrix failing cells={mstats['failing']}",
                        "data/vllm/ci/parity_report.json",
                    )
        self.report.metrics["parity_hardware"] = parity_stats

    def audit_queue_data(self) -> None:
        rows = self.load_jsonl("data/vllm/ci/queue_timeseries.jsonl")
        if not rows:
            return
        if len(rows) < 2:
            self.error(
                "queue-history-missing",
                "Queue timeseries contains only the current snapshot; historical counts must be retained",
                "data/vllm/ci/queue_timeseries.jsonl",
            )
        latest = rows[-1]
        latest_ts = parse_iso(latest.get("ts"))
        if latest_ts:
            age_hours = (datetime.now(timezone.utc) - latest_ts).total_seconds() / 3600
            if age_hours > 6:
                self.warning(
                    "queue-stale",
                    f"latest queue snapshot is {age_hours:.1f}h old",
                    "data/vllm/ci/queue_timeseries.jsonl",
                )

        workload_mismatches: list[str] = []
        retired_queue_rows = 0
        for idx, row in enumerate(rows, 1):
            queues = row.get("queues") or {}
            retired_queue_rows += sum(is_mi355b_queue(queue) for queue in queues)
            total_waiting = sum((q.get("waiting") or 0) for q in queues.values())
            total_running = sum((q.get("running") or 0) for q in queues.values())
            if row.get("total_waiting") != total_waiting:
                self.error(
                    "queue-total-waiting",
                    f"queue_timeseries row {idx} total_waiting={row.get('total_waiting')} but queues sum to {total_waiting}",
                    "data/vllm/ci/queue_timeseries.jsonl",
                )
            if row.get("total_running") != total_running:
                self.error(
                    "queue-total-running",
                    f"queue_timeseries row {idx} total_running={row.get('total_running')} but queues sum to {total_running}",
                    "data/vllm/ci/queue_timeseries.jsonl",
                )
            for queue, queue_row in queues.items():
                for key in ("waiting_by_workload", "running_by_workload"):
                    split = queue_row.get(key)
                    if not isinstance(split, dict):
                        continue
                    base_key = key.replace("_by_workload", "")
                    split_total = sum((v or 0) for v in split.values())
                    if split_total > (queue_row.get(base_key) or 0):
                        workload_mismatches.append(
                            f"row {idx} {queue}.{key}={split_total} above {base_key}={queue_row.get(base_key)}"
                        )
        if workload_mismatches:
            examples = "; ".join(workload_mismatches[:3])
            self.warning(
                "queue-workload-split-drift",
                f"{len(workload_mismatches)} queue workload split rows exceed their metric snapshot; likely API timing drift. Examples: {examples}",
                "data/vllm/ci/queue_timeseries.jsonl",
            )
        if retired_queue_rows:
            self.error(
                "queue-retired-mi355b",
                f"Queue history contains {retired_queue_rows} retired amd_mi355B queue rows",
                "data/vllm/ci/queue_timeseries.jsonl",
            )
        cutoff = latest_ts.timestamp() - 72 * 3600 if latest_ts else None
        recent_rows = [
            row
            for row in rows
            if cutoff is None
            or ((parse_iso(row.get("ts")) or datetime.fromtimestamp(0, timezone.utc)).timestamp() >= cutoff)
        ]
        amd_workload = 0
        for row in recent_rows:
            for queue, queue_row in (row.get("queues") or {}).items():
                if is_amd_queue(queue) and not is_retired_queue(queue):
                    amd_workload += (queue_row.get("waiting") or 0) + (queue_row.get("running") or 0)
        if amd_workload == 0:
            self.error(
                "queue-amd-workload-zero",
                "AMD queues have zero waiting+running workload across the default 72h window",
                "data/vllm/ci/queue_timeseries.jsonl",
            )

        jobs = self.load_json("data/vllm/ci/queue_jobs.json", {})
        pending = jobs.get("pending") if isinstance(jobs, dict) else []
        running = jobs.get("running") if isinstance(jobs, dict) else []
        if not isinstance(pending, list) or not isinstance(running, list):
            self.error("queue-jobs-shape", "queue_jobs.json pending/running must be lists")
        else:
            for kind, job_rows in (("pending", pending), ("running", running)):
                for job in job_rows[:100]:
                    missing = {"name", "queue", "url"} - set(job.keys())
                    if kind == "pending":
                        missing -= {"url"} if job.get("analysis_excluded") else set()
                        missing |= {"wait_min"} - set(job.keys())
                    if missing:
                        self.error(
                            "queue-job-row",
                            f"{kind} job row missing {sorted(missing)}",
                            "data/vllm/ci/queue_jobs.json",
                        )

        self.report.metrics["queue"] = {
            "snapshots": len(rows),
            "latest_ts": latest.get("ts"),
            "latest_total_waiting": latest.get("total_waiting"),
            "latest_total_running": latest.get("total_running"),
            "amd_workload_72h": amd_workload,
            "pending_jobs": len(pending) if isinstance(pending, list) else None,
            "running_jobs": len(running) if isinstance(running, list) else None,
        }

    def audit_ready_tickets(self) -> None:
        payload = self.load_json("data/vllm/ci/ready_tickets.json", {})
        if not isinstance(payload, dict):
            return
        tickets = payload.get("tickets") or []
        groups = payload.get("groups_all") or []
        expected = int(payload.get("failing_groups_total") or 0)
        if expected != len(tickets):
            self.error(
                "ready-ticket-count",
                f"failing_groups_total={expected} but tickets contains {len(tickets)} rows",
                "data/vllm/ci/ready_tickets.json",
            )
        build_refs = 0
        invalid_refs = 0
        for row in groups:
            refs = row.get("build_refs_latest") or []
            build_refs += len(refs)
            invalid_refs += sum(
                not ref.get("url") or "buildkite.com/" not in str(ref.get("url"))
                for ref in refs
            )
        if invalid_refs:
            self.error(
                "ready-ticket-build-links",
                f"{invalid_refs} ready-ticket build references lack an exact Buildkite URL",
                "data/vllm/ci/ready_tickets.json",
            )
        self.report.metrics["ready_tickets"] = {
            "failing_tickets": len(tickets),
            "groups": len(groups),
            "latest_build_links": build_refs,
        }

    def audit_frontend_contracts(self) -> None:
        checks = [
            (
                "docs/assets/js/dashboard.js",
                "prs: { page: 1, pageSize: 10",
                "home-pr-page-size",
                "Home PR table should default to 10 rows",
            ),
            (
                "docs/assets/js/dashboard.js",
                "issues: { page: 1, pageSize: 10",
                "home-issue-page-size",
                "Home issue table should default to 10 rows",
            ),
            (
                "docs/assets/js/dashboard.js",
                "Open Project Issues",
                "home-project-issue-counter",
                "Home top counter should expose project issues",
            ),
            (
                "docs/assets/js/dashboard.js",
                "parity-hw-overall",
                "home-overall-score-bar",
                "Home parity hardware table should render an overall score bar",
            ),
            (
                "docs/assets/js/dashboard.js",
                "mini-bar-wide",
                "home-wide-hardware-bars",
                "Home parity hardware bars should use the wider bar style",
            ),
            (
                "docs/assets/js/ci-analytics.js",
                "amd_test_matrix.json",
                "analytics-matrix-fetch",
                "CI Analytics should fetch the AMD matrix data source",
            ),
            (
                "docs/assets/js/ci-analytics.js",
                "attentionFamilies",
                "analytics-attention-families",
                "AMD Matrix Needs Attention should count affected rows",
            ),
            (
                "docs/assets/js/ci-analytics.js",
                "failing hardware jobs",
                "analytics-failing-cell-copy",
                "AMD Matrix should explain raw failing hardware-job count",
            ),
            (
                "docs/assets/js/ci-queue.js",
                "let metric = 'running'",
                "queue-default-running",
                "Queue Monitor should default to a nonzero running workload metric",
            ),
        ]
        metrics: dict[str, bool] = {}
        for relpath, token, code, message in checks:
            path = self.root / relpath
            text = path.read_text(errors="ignore") if path.exists() else ""
            ok = token in text
            metrics[code] = ok
            if not ok:
                self.error(code, message, relpath)

        weekly_match = re.search(
            r"function\s+renderWeeklySummary[\s\S]*?function\s+renderCards",
            (self.root / "docs/assets/js/dashboard.js").read_text(errors="ignore"),
        )
        if weekly_match and "Release" in weekly_match.group(0):
            self.error(
                "home-release-counter",
                "renderWeeklySummary still appears to render a release counter",
                "docs/assets/js/dashboard.js",
            )
        self.report.metrics["frontend_contracts"] = metrics

    def audit_workflows(self) -> None:
        workflows = sorted((self.root / ".github/workflows").glob("*.yml"))
        gh_pages_workflows: list[str] = []
        for path in workflows:
            text = path.read_text(errors="ignore")
            if "peaceiris/actions-gh-pages" not in text:
                continue
            gh_pages_workflows.append(path.name)
            if "group: gh-pages-deploy" not in text:
                self.error(
                    "workflow-gh-pages-concurrency",
                    f"{path.name} deploys to gh-pages without the shared concurrency group",
                    self.rel(path),
                )
            if "cancel-in-progress: false" not in text:
                self.error(
                    "workflow-gh-pages-cancel",
                    f"{path.name} deploys to gh-pages without cancel-in-progress: false",
                    self.rel(path),
                )
            if "python scripts/build_site.py --cache-bust-index" not in text:
                self.error(
                    "workflow-cache-bust",
                    f"{path.name} deploys Pages without cache-busting index.html",
                    self.rel(path),
                )

        hourly = self.root / ".github/workflows/hourly-master.yml"
        text = hourly.read_text(errors="ignore") if hourly.exists() else ""
        ordered_tokens = [
            "name: Collect CI data",
            "name: Collect CI analytics",
            "name: Collect test group changes",
            "name: Collect AMD test matrix",
            "name: Collect AMD gating proposals",
            "name: Run dashboard data audit",
            "python scripts/build_site.py --cache-bust-index",
        ]
        last = -1
        for token in ordered_tokens:
            idx = text.find(token)
            if idx < 0:
                self.error(
                    "workflow-hourly-step-missing",
                    f"hourly-master.yml missing {token!r}",
                    ".github/workflows/hourly-master.yml",
                )
                continue
            if idx <= last:
                self.error(
                    "workflow-hourly-step-order",
                    f"hourly-master.yml step {token!r} is out of order",
                    ".github/workflows/hourly-master.yml",
                )
            last = idx

        deploy_pages = self.root / ".github/workflows/deploy-pages.yml"
        deploy_text = deploy_pages.read_text(errors="ignore") if deploy_pages.exists() else ""
        forbidden_ci_writes = [
            "ci_health.json",
            "parity_report.json",
            "analytics.json",
            "amd_test_matrix.json",
            "group_changes.json",
        ]
        for name in forbidden_ci_writes:
            if re.search(r">\s*data/vllm/ci/" + re.escape(name), deploy_text):
                self.error(
                    "workflow-stale-gh-pages-sync",
                    f"deploy-pages.yml can overwrite {name} from gh-pages",
                    ".github/workflows/deploy-pages.yml",
                )

        self.report.metrics["workflows"] = {
            "workflow_count": len(workflows),
            "gh_pages_workflows": gh_pages_workflows,
        }


def run_audit(root: Path = ROOT) -> AuditReport:
    return DashboardAudit(root).run()


def format_text(report: AuditReport) -> str:
    lines = [
        "Dashboard data audit",
        f"Errors: {len(report.errors)}",
        f"Warnings: {len(report.warnings)}",
    ]
    for severity, findings in (("ERROR", report.errors), ("WARN", report.warnings)):
        if not findings:
            continue
        lines.append("")
        lines.append(severity)
        for finding in findings:
            path = f" [{finding.path}]" if finding.path else ""
            lines.append(f"- {finding.code}{path}: {finding.message}")
    lines.append("")
    lines.append("Key metrics")
    for key in sorted(report.metrics):
        lines.append(f"- {key}: {json.dumps(report.metrics[key], sort_keys=True, default=str)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit generated dashboard data and contracts")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="Return nonzero when warnings are present",
    )
    args = parser.parse_args(argv)

    report = run_audit(ROOT)
    if args.format == "json":
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True, default=str))
    else:
        print(format_text(report))

    if report.errors or (args.strict_warnings and report.warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
