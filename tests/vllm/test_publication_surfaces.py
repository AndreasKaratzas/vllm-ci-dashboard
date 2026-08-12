"""Regression tests for atomic dashboard publication-surface fallback."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from vllm import audit_dashboard_data as audit_module
from vllm import select_publication_surfaces as selector_module
from vllm.audit_dashboard_data import DashboardAudit, Finding
from vllm.publication_surfaces import (
    GLOBAL_DATA_PATHS,
    SOURCE_SURFACES,
    SURFACE_SPECS,
    SurfaceSpec,
    finding_surfaces,
    public_manifest_ownership_path,
    surface_for_path,
)
from vllm.select_publication_surfaces import restore_surface


ROOT = Path(__file__).resolve().parents[2]


def test_surface_ownership_is_unique_and_covers_public_source_manifest() -> None:
    exact_owners: dict[str, str] = {}
    glob_owners: dict[str, str] = {}
    for surface, spec in SURFACE_SPECS.items():
        for path in (*spec.required_paths, *spec.optional_paths):
            assert path not in exact_owners, (
                f"{path} is owned by both {exact_owners[path]} and {surface}"
            )
            exact_owners[path] = surface
            assert surface_for_path(path) == surface
        for pattern in spec.globs:
            assert pattern not in glob_owners, (
                f"{pattern} is owned by both {glob_owners[pattern]} and {surface}"
            )
            glob_owners[pattern] = surface

    manifest = json.loads((ROOT / "config/public_data_manifest.json").read_text())
    source_contract = {
        public_manifest_ownership_path(relative)
        for key in ("required_files", "optional_files", "build_inputs")
        for relative in manifest[key]
    }
    unowned = {
        path
        for path in source_contract
        if surface_for_path(path) is None and path not in GLOBAL_DATA_PATHS
    }
    assert unowned == set()

    assert set(SOURCE_SURFACES.values()) <= set(SURFACE_SPECS)
    assert audit_module.OPERATIONS_FRESH_SOURCE_KEYS <= set(SOURCE_SURFACES)


def test_findings_route_to_source_transactions_or_global_stop() -> None:
    matrix = Finding(
        "error",
        "matrix-summary-mismatch",
        "bad matrix summary",
        "data/vllm/ci/amd_test_matrix.json",
    )
    queue_source = Finding(
        "error",
        "operations-source-timestamp",
        "queue source has no timestamp",
        "data/vllm/ci/operations_v2.json",
        {"source": "queue_timeseries"},
    )
    stale_queue_source = Finding(
        "error",
        "operations-stale-source",
        "queue source is stale",
        "data/vllm/ci/operations_v2.json",
        {"source": "queue_timeseries"},
    )
    docs = Finding(
        "error",
        "matrix-summary-mismatch",
        "frontend contract changed",
        "docs/assets/js/ci-hotness.js",
    )

    assert finding_surfaces(matrix) == frozenset({"ci"})
    assert finding_surfaces(queue_source) == frozenset({"queue"})
    assert finding_surfaces(stale_queue_source) == frozenset({"queue"})
    assert finding_surfaces(docs) == frozenset()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_restore_surface_restores_exact_and_globbed_files_atomically(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    history = repo / "data/history"
    history.mkdir(parents=True)
    exact = repo / "data/current.json"
    retained = history / "retained.jsonl"
    exact.write_text('{"version":"baseline"}\n')
    retained.write_text('{"row":"baseline"}\n')

    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated baseline")
    baseline = _git(repo, "rev-parse", "HEAD")

    exact.write_text('{"version":"candidate"}\n')
    retained.write_text('{"row":"candidate"}\n')
    candidate_only = history / "candidate-only.jsonl"
    candidate_only.write_text('{"row":"candidate-only"}\n')

    restored = restore_surface(
        repo,
        baseline,
        SurfaceSpec(
            required_paths=("data/current.json",),
            globs=("data/history/*.jsonl",),
        ),
    )

    assert exact.read_text() == '{"version":"baseline"}\n'
    assert retained.read_text() == '{"row":"baseline"}\n'
    assert not candidate_only.exists()
    assert restored == ["data/current.json", "data/history/retained.jsonl"]


def test_restore_surface_preflights_every_required_file_before_mutating(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    history = repo / "data/history"
    history.mkdir(parents=True)
    existing = repo / "data/existing.json"
    retained = history / "retained.jsonl"
    existing.write_text('{"version":"baseline"}\n')
    retained.write_text('{"row":"baseline"}\n')

    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "incomplete validated baseline")
    baseline = _git(repo, "rev-parse", "HEAD")

    missing = repo / "data/missing.json"
    candidate_only = history / "candidate-only.jsonl"
    existing.write_text('{"version":"candidate"}\n')
    missing.write_text('{"version":"candidate-only"}\n')
    retained.write_text('{"row":"candidate"}\n')
    candidate_only.write_text('{"row":"candidate-only"}\n')
    candidate_contents = {
        path: path.read_bytes()
        for path in (existing, missing, retained, candidate_only)
    }

    with pytest.raises(RuntimeError, match="missing required publication path"):
        restore_surface(
            repo,
            baseline,
            SurfaceSpec(
                required_paths=("data/existing.json", "data/missing.json"),
                globs=("data/history/*.jsonl",),
            ),
        )

    assert {
        path: path.read_bytes()
        for path in (existing, missing, retained, candidate_only)
    } == candidate_contents


def test_forced_degraded_surface_restores_a_clean_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    source = repo / "data/source.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"version":"baseline"}\n')
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated baseline")
    baseline = _git(repo, "rev-parse", "HEAD")
    source.write_text('{"version":"candidate"}\n')

    audit_runs: list[bool] = []

    class CleanAudit:
        def __init__(self, *args, allow_publication_fallback: bool, **kwargs):
            audit_runs.append(allow_publication_fallback)

        def run(self) -> SimpleNamespace:
            return SimpleNamespace(errors=[])

        def audit_publication_surface_files(self) -> None:
            self.report = SimpleNamespace(errors=[])

    monkeypatch.setattr(
        selector_module,
        "SURFACE_SPECS",
        {"ci": SurfaceSpec(required_paths=("data/source.json",))},
    )
    monkeypatch.setattr(selector_module, "DashboardAudit", CleanAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    state = selector_module.select_publication(
        repo,
        baseline,
        repo / "publication-state.json",
        forced_degraded=("ci",),
    )

    assert source.read_text() == '{"version":"baseline"}\n'
    assert state["mode"] == "fallback"
    assert state["degraded_surfaces"] == ["ci"]
    assert state["candidate_errors"][0]["code"] == "publication-collector-failed"
    assert audit_runs == [False, False, True]


def _write_operations_fixture(root: Path, source_timestamp: str) -> None:
    ci = root / "data/vllm/ci"
    ci.mkdir(parents=True, exist_ok=True)
    (ci / "operations_v2.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": "2026-08-12T12:00:00Z",
                "sources": {
                    "analytics": {
                        "path": "analytics.json",
                        "timestamp": source_timestamp,
                        "timestamp_source": "generated_at",
                    }
                },
            }
        )
    )
    analytics = ci / "analytics.json"
    analytics.write_text("{}\n")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (ci / "publication_state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": generated_at,
                "baseline_ref": "0" * 40,
                "mode": "fallback",
                "degraded_surfaces": ["ci"],
                "degraded_since": {"ci": generated_at},
                "fallback_max_age_hours": 36,
                "restored_manifest": {
                    "ci": {
                        "data/vllm/ci/analytics.json": {
                            "bytes": analytics.stat().st_size,
                            "sha256": hashlib.sha256(analytics.read_bytes()).hexdigest(),
                        }
                    }
                },
            }
        )
    )


def test_malformed_fallback_surface_item_is_reported_without_crashing(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "publication-state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "baseline_ref": "0" * 40,
                "mode": "fallback",
                "degraded_surfaces": ["ci", {"surface": "queue"}],
                "degraded_since": {},
                "fallback_max_age_hours": 36,
                "restored_manifest": {},
            }
        )
    )

    audit = DashboardAudit(tmp_path, publication_state_path=state_path)

    assert audit.fallback_surfaces() == frozenset()
    assert [finding.code for finding in audit.report.errors] == [
        "publication-state-invalid"
    ]


def test_tampered_restored_manifest_disables_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_operations_fixture(tmp_path, "2026-08-12T00:00:00Z")
    monkeypatch.setattr(
        audit_module,
        "SURFACE_SPECS",
        {"ci": SurfaceSpec(required_paths=("data/vllm/ci/analytics.json",))},
    )
    (tmp_path / "data/vllm/ci/analytics.json").write_text('{"tampered":true}\n')

    audit = DashboardAudit(tmp_path)

    assert audit.fallback_surfaces() == frozenset()
    mismatches = [
        finding
        for finding in audit.report.errors
        if finding.code == "publication-fallback-manifest-mismatch"
    ]
    assert len(mismatches) == 1
    assert mismatches[0].context == {
        "surface": "ci",
        "path": "data/vllm/ci/analytics.json",
    }


def test_prior_fallback_start_persists_until_hard_expiration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    source = repo / "data/source.json"
    state_path = repo / "data/vllm/ci/publication_state.json"
    state_path.parent.mkdir(parents=True)
    source.write_text('{"version":"baseline"}\n')
    original_since = "2000-01-01T00:00:00Z"
    source_bytes = source.read_bytes()
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2000-01-01T00:00:00Z",
                "baseline_ref": "0" * 40,
                "mode": "fallback",
                "degraded_surfaces": ["ci"],
                "degraded_since": {"ci": original_since},
                "fallback_max_age_hours": 36,
                "restored_manifest": {
                    "ci": {
                        "data/source.json": {
                            "bytes": len(source_bytes),
                            "sha256": hashlib.sha256(source_bytes).hexdigest(),
                        }
                    }
                },
            }
        )
    )
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "degraded validated baseline")
    baseline = _git(repo, "rev-parse", "HEAD")
    source.write_text('{"version":"candidate"}\n')

    class CleanAudit:
        def __init__(self, *args, **kwargs):
            self.report = SimpleNamespace(errors=[])

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self) -> SimpleNamespace:
            return SimpleNamespace(errors=[])

    monkeypatch.setattr(
        selector_module,
        "SURFACE_SPECS",
        {"ci": SurfaceSpec(required_paths=("data/source.json",))},
    )
    monkeypatch.setattr(selector_module, "DashboardAudit", CleanAudit)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    with pytest.raises(RuntimeError, match="fallback exceeded its hard limit"):
        selector_module.select_publication(
            repo,
            baseline,
            state_path,
            forced_degraded=("ci",),
        )

    blocked = json.loads(state_path.read_text())
    assert blocked["mode"] == "blocked"
    assert blocked["degraded_since"] == {"ci": original_since}
    assert blocked["final_errors"][0]["code"] == "publication-fallback-expired"
    assert source.read_text() == '{"version":"candidate"}\n'


@pytest.mark.parametrize(
    ("source_timestamp", "expected_severity"),
    [
        ("2026-08-11T00:00:00Z", "warning"),
        ("2026-08-10T23:59:59Z", "error"),
    ],
)
def test_stale_source_fallback_is_bounded_at_36_hours(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_timestamp: str,
    expected_severity: str,
) -> None:
    _write_operations_fixture(tmp_path, source_timestamp)
    monkeypatch.setattr(
        audit_module,
        "OPERATIONS_FRESH_SOURCE_KEYS",
        frozenset({"analytics"}),
    )
    monkeypatch.setattr(
        audit_module,
        "SURFACE_SPECS",
        {"ci": SurfaceSpec(required_paths=("data/vllm/ci/analytics.json",))},
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_operations_v2()
    findings = {
        finding.code: finding
        for finding in audit.report.findings
        if finding.code in {
            "operations-stale-source",
            "operations-stale-source-fallback",
        }
    }

    expected_code = (
        "operations-stale-source-fallback"
        if expected_severity == "warning"
        else "operations-stale-source"
    )
    assert set(findings) == {expected_code}
    assert findings[expected_code].severity == expected_severity
    assert findings[expected_code].context == {"source": "analytics"}


def test_candidate_audit_never_accepts_stale_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_operations_fixture(tmp_path, "2026-08-11T00:00:00Z")
    monkeypatch.setattr(
        audit_module,
        "OPERATIONS_FRESH_SOURCE_KEYS",
        frozenset({"analytics"}),
    )
    monkeypatch.setattr(
        audit_module,
        "SURFACE_SPECS",
        {"ci": SurfaceSpec(required_paths=("data/vllm/ci/analytics.json",))},
    )

    audit = DashboardAudit(tmp_path, allow_publication_fallback=False)
    audit.audit_operations_v2()

    assert "operations-stale-source" in {
        finding.code for finding in audit.report.errors
    }
    assert "operations-stale-source-fallback" not in {
        finding.code for finding in audit.report.warnings
    }


def test_stale_shard_bases_are_a_routable_ci_surface_error(tmp_path: Path) -> None:
    ci = tmp_path / "data/vllm/ci"
    results = ci / "test_results"
    results.mkdir(parents=True)
    (ci / "shard_bases.json").write_text(
        json.dumps(["active sharded group", "removed sharded group"])
    )
    (results / "2026-08-12_amd.jsonl").write_text(
        json.dumps({"job_name": "mi300_1: Active Sharded Group 1"}) + "\n"
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_shard_bases()

    finding = next(
        finding
        for finding in audit.report.errors
        if finding.code == "shard-bases-unused"
    )
    assert finding.path == "data/vllm/ci/shard_bases.json"
    assert finding_surfaces(finding) == frozenset({"ci"})
    assert "removed sharded group" in finding.message
