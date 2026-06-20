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


ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
VLLM = DATA / "vllm"
CI = VLLM / "ci"

AMD_FAILURE_STATES = {"failed", "timed_out", "broken", "soft_fail"}
AMD_WAITING_STATES = {"running", "scheduled", "assigned"}
RESULT_SUFFIXES = {"amd-ci": "amd", "ci": "upstream"}


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
        "data/vllm/ci/analytics.json",
        ("scripts/vllm/collect_analytics.py",),
        ("docs/assets/js/ci-analytics.js",),
        ("amd-ci", "ci"),
        "CI Analytics comparison, recent builds, trends, rankings",
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


def is_mi355b_queue(name: str) -> bool:
    return "mi355b" in str(name or "").lower()


def same_repo(ref_repo: str | None, default_repo: str) -> bool:
    return (ref_repo or default_repo).lower() == default_repo.lower()


class DashboardAudit:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.report = AuditReport()
        self._json_cache: dict[Path, Any] = {}

    def run(self) -> AuditReport:
        self.audit_data_inventory()
        self.audit_home_pr_issue_data()
        self.audit_ci_health()
        self.audit_gating_target_candidates()
        self.audit_analytics()
        self.audit_amd_matrix()
        self.audit_queue_data()
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
            builds = block.get("builds") or []
            if not builds:
                self.error("analytics-empty-builds", f"{slug} analytics has no builds")
                continue

            suffix = RESULT_SUFFIXES[slug]
            latest_results = self.latest_result_file(suffix)
            result_numbers = self.build_numbers_in_jsonl(latest_results)
            latest = builds[0]
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

            windows = block.get("windows") or {}
            default_window = block.get("default_window")
            if default_window not in windows:
                self.error(
                    "analytics-default-window",
                    f"{slug} default_window={default_window!r} is absent from windows",
                    "data/vllm/ci/analytics.json",
                )
            for key in ("1d", "3d", "7d", "14d", "30d", "90d"):
                if key not in windows:
                    self.error(
                        "analytics-window-missing",
                        f"{slug} missing precomputed {key} window",
                        "data/vllm/ci/analytics.json",
                    )

            chartable_builds = [
                b
                for b in ((windows.get(default_window) or {}).get("builds") or builds)
                if (b.get("total_jobs") or 0) > 10
            ]
            if len(chartable_builds) < 2:
                self.error(
                    "analytics-chart-empty",
                    f"{slug} default window has fewer than two chartable builds",
                    "data/vllm/ci/analytics.json",
                )

            for build in builds[:20]:
                jobs = build.get("jobs") or []
                if build.get("total_jobs") != len(jobs):
                    self.error(
                        "analytics-total-jobs",
                        f"{slug} build #{build.get('number')} total_jobs={build.get('total_jobs')} but has {len(jobs)} jobs",
                        "data/vllm/ci/analytics.json",
                    )
                state_counts = {
                    "passed": sum(1 for j in jobs if j.get("state") == "passed"),
                    "failed": sum(
                        1
                        for j in jobs
                        if j.get("state") in {"failed", "timed_out", "broken"}
                    ),
                    "soft_failed": sum(1 for j in jobs if j.get("state") == "soft_fail"),
                    "skipped": sum(1 for j in jobs if j.get("state") == "skipped"),
                }
                for key, expected in state_counts.items():
                    if build.get(key, 0) != expected:
                        self.error(
                            "analytics-job-counts",
                            f"{slug} build #{build.get('number')} {key}={build.get(key)} but jobs imply {expected}",
                            "data/vllm/ci/analytics.json",
                        )

            rankings = block.get("duration_ranking") or []
            too_long = [
                row
                for row in rankings
                if isinstance(row.get("median_dur"), (int, float)) and row["median_dur"] > 360
            ]
            if too_long:
                self.warning(
                    "analytics-duration-units",
                    f"{slug} has median job durations over 6h; check seconds/minutes conversion",
                    "data/vllm/ci/analytics.json",
                )
            metrics[slug] = {
                "builds": len(builds),
                "latest_build": latest.get("number"),
                "latest_source": latest.get("source"),
                "default_window": default_window,
                "failure_rankings": len(block.get("failure_ranking") or []),
                "duration_rankings": len(block.get("duration_ranking") or []),
            }
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
            if health_groups is not None and health_groups != arch_stats["total"]:
                self.error(
                    "matrix-health-hardware-count",
                    f"{arch} matrix groups={arch_stats['total']} but ci_health by_hardware groups={health_groups}",
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
                self.error(
                    "parity-matrix-hardware-total",
                    f"{arch} parity hardware total={pstats.get('total')} but AMD matrix total={mstats['total']}",
                    "data/vllm/ci/parity_report.json",
                )
            if pstats.get("failing") != mstats["failing"]:
                self.error(
                    "parity-matrix-hardware-failing",
                    f"{arch} parity failing groups={pstats.get('failing')} but AMD matrix failing cells={mstats['failing']}",
                    "data/vllm/ci/parity_report.json",
                )
        self.report.metrics["parity_hardware"] = parity_stats

    def audit_queue_data(self) -> None:
        rows = self.load_jsonl("data/vllm/ci/queue_timeseries.jsonl")
        if not rows:
            return
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
        for idx, row in enumerate(rows, 1):
            queues = row.get("queues") or {}
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
                if is_amd_queue(queue) and not is_mi355b_queue(queue):
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
