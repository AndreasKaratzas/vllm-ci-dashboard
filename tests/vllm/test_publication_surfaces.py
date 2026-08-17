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
    AGENT_HEALTH_WATCHER_STATE_PATHS,
    CI_CORE_WATCHER_STATE_PATHS,
    GLOBAL_DATA_PATHS,
    LEGACY_CI_SURFACE_SPEC,
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

    assert finding_surfaces(matrix) == frozenset({"ci_core"})
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


def _write_legacy_ci_baseline(repo: Path, since: str) -> tuple[Path, dict[str, bytes]]:
    baseline_bytes: dict[str, bytes] = {}
    for relative in LEGACY_CI_SURFACE_SPEC.required_paths:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps({"selection": "baseline", "path": relative}) + "\n").encode()
        path.write_bytes(payload)
        baseline_bytes[relative] = payload
    state_path = repo / "data/vllm/ci/publication_state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": since,
                "baseline_ref": "0" * 40,
                "mode": "fallback",
                "degraded_surfaces": ["ci"],
                "degraded_since": {"ci": since},
                "fallback_max_age_hours": 36,
                "restored_manifest": {
                    "ci": {
                        relative: {
                            "bytes": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                        for relative, payload in baseline_bytes.items()
                    }
                },
            }
        )
    )
    return state_path, baseline_bytes


def _manifest_descriptor(payload: bytes) -> dict[str, int | str]:
    return {
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_v2_agent_health_baseline(repo: Path, since: str) -> tuple[Path, str, str]:
    surface = "agent_health"
    source_relative = "data/vllm/ci/agent_health.json"
    ledger_relative = AGENT_HEALTH_WATCHER_STATE_PATHS[0]
    source = repo / source_relative
    ledger = repo / ledger_relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source_payload = b'{"selection":"baseline"}\n'
    ledger_payload = b'{"active":{"old":true}}\n'
    source.write_bytes(source_payload)
    ledger.write_bytes(ledger_payload)
    state_path = repo / "data/vllm/ci/publication_state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": since,
                "baseline_ref": "0" * 40,
                "mode": "fallback",
                "degraded_surfaces": [surface],
                "fresh_degraded_surfaces": [],
                "fallback_surfaces": [surface],
                "degraded_since": {surface: since},
                "fallback_since": {surface: since},
                "fallback_max_age_hours": 36,
                "restored_paths": {
                    surface: [source_relative, ledger_relative],
                },
                "restored_manifest": {
                    surface: {
                        source_relative: _manifest_descriptor(source_payload),
                        ledger_relative: _manifest_descriptor(ledger_payload),
                    }
                },
            }
        )
    )
    return state_path, source_relative, ledger_relative


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
    assert state["schema_version"] == 2
    assert state["mode"] == "fallback"
    assert state["degraded_surfaces"] == ["ci"]
    assert state["fresh_degraded_surfaces"] == []
    assert state["fallback_surfaces"] == ["ci"]
    assert set(state["degraded_since"]) == {"ci"}
    assert set(state["fallback_since"]) == {"ci"}
    assert set(state["restored_manifest"]) == {"ci"}
    assert state["candidate_errors"][0]["code"] == "publication-collector-failed"
    assert audit_runs == [False, False, True]


def test_legacy_ci_state_migrates_clocks_and_closes_core_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    original_since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path, baseline_bytes = _write_legacy_ci_baseline(repo, original_since)
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "legacy monolithic fallback")
    baseline = _git(repo, "rev-parse", "HEAD")

    child_paths = {
        surface: SURFACE_SPECS[surface].required_paths[0]
        for surface in ("ci_core", "ci_gating", "ci_changes", "ci_hotness")
    }
    for surface, relative in child_paths.items():
        (repo / relative).write_text(
            json.dumps({"selection": "candidate", "surface": surface}) + "\n"
        )

    class CleanAudit:
        def __init__(self, *args, **kwargs):
            self.report = SimpleNamespace(errors=[])

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self) -> SimpleNamespace:
            return SimpleNamespace(errors=[])

    monkeypatch.setattr(selector_module, "DashboardAudit", CleanAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    state = selector_module.select_publication(
        repo,
        baseline,
        state_path,
        forced_degraded=("ci_core",),
    )

    fallback = {"ci_core", "ci_gating"}
    assert set(state["fallback_surfaces"]) == fallback
    assert "ci" not in state["degraded_surfaces"]
    assert state["degraded_since"] == {
        surface: original_since for surface in fallback
    }
    assert state["fallback_since"] == {
        surface: original_since for surface in fallback
    }
    assert set(state["restored_paths"]) == fallback
    assert set(state["restored_manifest"]) == fallback
    for surface in fallback:
        relative = child_paths[surface]
        assert (repo / relative).read_bytes() == baseline_bytes[relative]
    for surface in {"ci_changes", "ci_hotness"}:
        relative = child_paths[surface]
        assert json.loads((repo / relative).read_text()) == {
            "selection": "candidate",
            "surface": surface,
        }


def test_legacy_ci_state_drops_independently_mutated_watcher_ledger(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path, _ = _write_legacy_ci_baseline(repo, now)
    ledger_relative = CI_CORE_WATCHER_STATE_PATHS[0]
    ledger = repo / ledger_relative
    original = b'{"active":{"before":true}}\n'
    ledger.write_bytes(original)
    state = json.loads(state_path.read_text())
    state["restored_manifest"]["ci"][ledger_relative] = _manifest_descriptor(
        original
    )
    state["restored_paths"] = {
        "ci": sorted(state["restored_manifest"]["ci"]),
    }
    state_path.write_text(json.dumps(state))

    # Watcher hysteresis is deliberately allowed to advance independently of
    # publication data and therefore no longer participates in the proof.
    ledger.write_text('{"active":{"after":true}}\n')
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "legacy fallback with newer watcher state")
    baseline = _git(repo, "rev-parse", "HEAD")

    migrated = selector_module._baseline_publication_state(
        repo, baseline, state_path
    )

    assert migrated is not None
    assert set(migrated["fallback_surfaces"]) == {
        "ci_core",
        "ci_gating",
        "ci_changes",
        "ci_hotness",
    }
    assert all(
        ledger_relative not in entries
        for entries in migrated["restored_manifest"].values()
    )
    assert all(
        ledger_relative not in paths
        for paths in migrated["restored_paths"].values()
    )

    audit = DashboardAudit(repo, publication_state_path=state_path)
    assert audit.fallback_surfaces() == frozenset(migrated["fallback_surfaces"])
    assert "publication-fallback-manifest-mismatch" not in {
        finding.code for finding in audit.report.errors
    }


def test_schema_v2_state_drops_independently_mutated_watcher_ledger(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path, source_relative, ledger_relative = _write_v2_agent_health_baseline(
        repo, now
    )
    (repo / ledger_relative).write_text('{"active":{"after":true}}\n')
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "v2 fallback with newer watcher state")
    baseline = _git(repo, "rev-parse", "HEAD")

    migrated = selector_module._baseline_publication_state(
        repo, baseline, state_path
    )

    assert migrated is not None
    assert migrated["fallback_surfaces"] == ["agent_health"]
    assert migrated["restored_paths"] == {"agent_health": [source_relative]}
    assert set(migrated["restored_manifest"]["agent_health"]) == {
        source_relative
    }

    audit = DashboardAudit(repo, publication_state_path=state_path)
    assert audit.fallback_surfaces() == frozenset({"agent_health"})
    assert "publication-fallback-manifest-mismatch" not in {
        finding.code for finding in audit.report.errors
    }


def test_schema_v2_state_still_rejects_non_ledger_manifest_tampering(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path, source_relative, _ = _write_v2_agent_health_baseline(repo, now)
    (repo / source_relative).write_text('{"selection":"tampered"}\n')
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "tampered v2 fallback")
    baseline = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="does not match its manifest"):
        selector_module._baseline_publication_state(repo, baseline, state_path)

    audit = DashboardAudit(repo, publication_state_path=state_path)
    assert audit.fallback_surfaces() == frozenset()
    mismatches = [
        finding
        for finding in audit.report.errors
        if finding.code == "publication-fallback-manifest-mismatch"
    ]
    assert any(
        finding.context.get("path") == source_relative
        for finding in mismatches
    )


def test_schema_v2_state_still_rejects_non_ledger_restored_path_tampering(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path, source_relative, ledger_relative = (
        _write_v2_agent_health_baseline(repo, now)
    )
    state = json.loads(state_path.read_text())
    state["restored_paths"]["agent_health"] = [ledger_relative]
    state_path.write_text(json.dumps(state))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "tampered v2 restored path proof")
    baseline = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="restored paths .* inconsistent"):
        selector_module._baseline_publication_state(repo, baseline, state_path)

    audit = DashboardAudit(repo, publication_state_path=state_path)
    assert audit.fallback_surfaces() == frozenset()
    mismatches = [
        finding
        for finding in audit.report.errors
        if finding.code == "publication-fallback-manifest-mismatch"
    ]
    assert any(
        finding.context.get("surface") == "agent_health"
        and "restored path list" in finding.message
        for finding in mismatches
    )
    assert source_relative not in state["restored_paths"]["agent_health"]


def test_schema_v2_baseline_rejects_legacy_ci_alias(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state_path = repo / "data/vllm/ci/publication_state.json"
    state_path.parent.mkdir(parents=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": now,
                "baseline_ref": "0" * 40,
                "mode": "fallback",
                "degraded_surfaces": ["ci"],
                "fresh_degraded_surfaces": [],
                "fallback_surfaces": ["ci"],
                "degraded_since": {"ci": now},
                "fallback_since": {"ci": now},
                "fallback_max_age_hours": 36,
                "restored_manifest": {"ci": {}},
            }
        )
    )
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "invalid v2 legacy alias")
    baseline = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="publication state is inconsistent"):
        selector_module._baseline_publication_state(repo, baseline, state_path)


def test_clean_candidate_writes_schema_v2_current_state(
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

    class CleanAudit:
        def __init__(self, *args, **kwargs):
            self.report = SimpleNamespace(errors=[])

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self) -> SimpleNamespace:
            # Deliberately omit ``degradations`` to cover compatibility with
            # lightweight test fakes and older report implementations.
            return SimpleNamespace(errors=[])

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
    )

    assert source.read_text() == '{"version":"candidate"}\n'
    assert state == {
        "schema_version": 2,
        "generated_at": state["generated_at"],
        "baseline_ref": baseline,
        "mode": "current",
        "degraded_surfaces": [],
        "fresh_degraded_surfaces": [],
        "fallback_surfaces": [],
        "degraded_since": {},
        "fallback_since": {},
        "fallback_max_age_hours": 36,
        "candidate_errors": [],
        "candidate_degradations": [],
        "final_errors": [],
        "final_degradations": [],
        "restored_paths": {},
        "restored_manifest": {},
    }


def test_degradation_publishes_candidate_bytes_without_fallback(
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

    degradation = Finding(
        "degradation",
        "candidate-provisional",
        "candidate is usable but provisional",
        "data/source.json",
    )

    class DegradedAudit:
        def __init__(self, *args, **kwargs):
            self.report = SimpleNamespace(errors=[], degradations=[])

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self) -> SimpleNamespace:
            return SimpleNamespace(errors=[], degradations=[degradation])

    monkeypatch.setattr(
        selector_module,
        "SURFACE_SPECS",
        {"ci": SurfaceSpec(required_paths=("data/source.json",))},
    )
    monkeypatch.setattr(
        selector_module,
        "finding_surfaces",
        lambda finding: frozenset({"ci"}),
    )
    monkeypatch.setattr(selector_module, "DashboardAudit", DegradedAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    state = selector_module.select_publication(
        repo,
        baseline,
        repo / "publication-state.json",
    )

    assert source.read_text() == '{"version":"candidate"}\n'
    assert state["mode"] == "degraded"
    assert state["degraded_surfaces"] == ["ci"]
    assert state["fresh_degraded_surfaces"] == ["ci"]
    assert state["fallback_surfaces"] == []
    assert set(state["degraded_since"]) == {"ci"}
    assert state["fallback_since"] == {}
    assert state["restored_paths"] == {}
    assert state["restored_manifest"] == {}
    assert state["candidate_degradations"][0]["code"] == "candidate-provisional"


def test_long_lived_fresh_degradation_keeps_incident_age_without_expiring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    source = repo / "data/source.json"
    state_path = repo / "data/vllm/ci/publication_state.json"
    state_path.parent.mkdir(parents=True)
    source.write_text('{"version":"baseline"}\n')
    original_since = "2000-01-01T00:00:00Z"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": original_since,
                "baseline_ref": "0" * 40,
                "mode": "degraded",
                "degraded_surfaces": ["ci"],
                "fresh_degraded_surfaces": ["ci"],
                "fallback_surfaces": [],
                "degraded_since": {"ci": original_since},
                "fallback_since": {},
                "fallback_max_age_hours": 36,
                "restored_paths": {},
                "restored_manifest": {},
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

    degradation = Finding(
        "degradation",
        "candidate-provisional",
        "candidate is usable but provisional",
        "data/source.json",
    )

    class DegradedAudit:
        def __init__(self, *args, **kwargs):
            self.report = SimpleNamespace(errors=[], degradations=[])

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self) -> SimpleNamespace:
            return SimpleNamespace(errors=[], degradations=[degradation])

    monkeypatch.setattr(
        selector_module,
        "SURFACE_SPECS",
        {"ci": SurfaceSpec(required_paths=("data/source.json",))},
    )
    monkeypatch.setattr(
        selector_module,
        "finding_surfaces",
        lambda finding: frozenset({"ci"}),
    )
    monkeypatch.setattr(selector_module, "DashboardAudit", DegradedAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    state = selector_module.select_publication(repo, baseline, state_path)

    assert source.read_text() == '{"version":"candidate"}\n'
    assert state["mode"] == "degraded"
    assert state["degraded_since"] == {"ci": original_since}
    assert state["fallback_since"] == {}
    assert state["restored_paths"] == {}
    assert state["restored_manifest"] == {}


def test_mixed_state_restores_only_hard_error_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    ci_source = repo / "data/ci.json"
    queue_source = repo / "data/queue.json"
    ci_source.parent.mkdir(parents=True)
    ci_source.write_text('{"version":"ci-baseline"}\n')
    queue_source.write_text('{"version":"queue-baseline"}\n')
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated baseline")
    baseline = _git(repo, "rev-parse", "HEAD")
    ci_source.write_text('{"version":"ci-candidate"}\n')
    queue_source.write_text('{"version":"queue-candidate"}\n')

    candidate_reports = iter([
        SimpleNamespace(
            errors=[Finding("error", "queue-invalid", "bad queue", "data/queue.json")],
            degradations=[
                Finding(
                    "degradation",
                    "ci-provisional",
                    "provisional CI",
                    "data/ci.json",
                )
            ],
        ),
        SimpleNamespace(errors=[], degradations=[]),
    ])

    class MixedAudit:
        def __init__(self, *args, **kwargs):
            self.report = SimpleNamespace(errors=[], degradations=[])

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self) -> SimpleNamespace:
            return next(candidate_reports)

    monkeypatch.setattr(
        selector_module,
        "SURFACE_SPECS",
        {
            "ci": SurfaceSpec(required_paths=("data/ci.json",)),
            "queue": SurfaceSpec(required_paths=("data/queue.json",)),
        },
    )
    monkeypatch.setattr(
        selector_module,
        "finding_surfaces",
        lambda finding: frozenset(
            {"queue" if finding.path == "data/queue.json" else "ci"}
        ),
    )
    monkeypatch.setattr(selector_module, "DashboardAudit", MixedAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    state = selector_module.select_publication(
        repo,
        baseline,
        repo / "publication-state.json",
    )

    assert ci_source.read_text() == '{"version":"ci-candidate"}\n'
    assert queue_source.read_text() == '{"version":"queue-baseline"}\n'
    assert state["mode"] == "mixed"
    assert state["degraded_surfaces"] == ["ci", "queue"]
    assert state["fresh_degraded_surfaces"] == ["ci"]
    assert state["fallback_surfaces"] == ["queue"]
    assert set(state["degraded_since"]) == {"ci", "queue"}
    assert set(state["fallback_since"]) == {"queue"}
    assert set(state["restored_paths"]) == {"queue"}
    assert set(state["restored_manifest"]) == {"queue"}


def test_hard_error_wins_when_surface_is_also_degraded(
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

    reports = iter([
        SimpleNamespace(
            errors=[Finding("error", "candidate-invalid", "invalid", "data/source.json")],
            degradations=[
                Finding(
                    "degradation",
                    "candidate-provisional",
                    "provisional",
                    "data/source.json",
                )
            ],
        ),
        SimpleNamespace(errors=[], degradations=[]),
    ])

    class OverlapAudit:
        def __init__(self, *args, **kwargs):
            self.report = SimpleNamespace(errors=[], degradations=[])

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self) -> SimpleNamespace:
            return next(reports)

    monkeypatch.setattr(
        selector_module,
        "SURFACE_SPECS",
        {"ci": SurfaceSpec(required_paths=("data/source.json",))},
    )
    monkeypatch.setattr(
        selector_module,
        "finding_surfaces",
        lambda finding: frozenset({"ci"}),
    )
    monkeypatch.setattr(selector_module, "DashboardAudit", OverlapAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    state = selector_module.select_publication(
        repo,
        baseline,
        repo / "publication-state.json",
    )

    assert source.read_text() == '{"version":"baseline"}\n'
    assert state["mode"] == "fallback"
    assert state["fresh_degraded_surfaces"] == []
    assert state["fallback_surfaces"] == ["ci"]


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
    assert blocked["schema_version"] == 2
    assert blocked["mode"] == "blocked"
    assert blocked["fresh_degraded_surfaces"] == []
    assert blocked["fallback_surfaces"] == ["ci"]
    assert blocked["degraded_since"] == {"ci": original_since}
    assert blocked["fallback_since"] == {"ci": original_since}
    assert blocked["final_errors"][0]["code"] == "publication-fallback-expired"
    assert source.read_text() == '{"version":"candidate"}\n'


def test_clean_candidate_recovers_after_prior_fallback_expired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    source = repo / "data/source.json"
    state_path = repo / "data/vllm/ci/publication_state.json"
    state_path.parent.mkdir(parents=True)
    source.write_text('{"version":"baseline"}\n')
    source_bytes = source.read_bytes()
    original_since = "2000-01-01T00:00:00Z"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": original_since,
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
    _git(repo, "commit", "-m", "expired fallback baseline")
    baseline = _git(repo, "rev-parse", "HEAD")
    source.write_text('{"version":"fresh-candidate"}\n')

    class CleanAudit:
        def __init__(self, *args, **kwargs):
            self.report = SimpleNamespace(errors=[], degradations=[])

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self) -> SimpleNamespace:
            return SimpleNamespace(errors=[], degradations=[])

    monkeypatch.setattr(
        selector_module,
        "SURFACE_SPECS",
        {"ci": SurfaceSpec(required_paths=("data/source.json",))},
    )
    monkeypatch.setattr(selector_module, "DashboardAudit", CleanAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    state = selector_module.select_publication(repo, baseline, state_path)

    assert source.read_text() == '{"version":"fresh-candidate"}\n'
    assert state["mode"] == "current"
    assert state["degraded_surfaces"] == []
    assert state["fresh_degraded_surfaces"] == []
    assert state["fallback_surfaces"] == []
    assert state["degraded_since"] == {}
    assert state["fallback_since"] == {}
    assert state["restored_paths"] == {}
    assert state["restored_manifest"] == {}


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
    monkeypatch.setattr(audit_module, "SOURCE_SURFACES", {"analytics": "ci"})

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
    (ci / "shard_base_catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"commit_sha": "a" * 40},
                "normalization_bases": [
                    "active sharded group",
                    "removed sharded group",
                ],
                "pipelines": {
                    "amd": ["active sharded group", "removed sharded group"],
                    "upstream": [],
                },
                "definitions": [],
                "evidence": {
                    "pipeline": "amd",
                    "build_number": 100,
                    "build_commit": "a" * 40,
                    "build_state": "passed",
                    "roster_complete": True,
                    "result_file": "2026-08-12_amd.jsonl",
                    "job_names": ["mi300_1: Active Sharded Group 1"],
                },
            }
        )
    )
    (results / "2026-08-12_amd.jsonl").write_text(
        json.dumps({"job_name": "mi300_1: Active Sharded Group 1"}) + "\n"
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_shard_bases()

    finding = next(
        finding
        for finding in audit.report.degradations
        if finding.code == "shard-bases-unused"
    )
    assert finding.path == "data/vllm/ci/shard_bases.json"
    assert finding_surfaces(finding) == frozenset({"ci_core"})
    assert "removed sharded group" in finding.message


def test_missing_shard_catalog_skips_unsafe_cross_pipeline_audit(tmp_path: Path) -> None:
    ci = tmp_path / "data/vllm/ci"
    results = ci / "test_results"
    results.mkdir(parents=True)
    (ci / "shard_bases.json").write_text(
        json.dumps(["unknown pipeline sharded group"])
    )
    (results / "2026-08-12_amd.jsonl").write_text(
        json.dumps({"job_name": "mi300_1: Different Group"}) + "\n"
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_shard_bases()

    assert not audit.report.errors
    assert {finding.code for finding in audit.report.warnings} == {
        "shard-base-catalog-missing"
    }


def test_upstream_only_shard_base_is_not_required_in_amd_evidence(tmp_path: Path) -> None:
    ci = tmp_path / "data/vllm/ci"
    results = ci / "test_results"
    results.mkdir(parents=True)
    (ci / "shard_bases.json").write_text(
        json.dumps(["amd sharded group", "humming eval (h100)"])
    )
    (ci / "shard_base_catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"commit_sha": "a" * 40},
                "normalization_bases": [
                    "amd sharded group",
                    "humming eval (h100)",
                ],
                "pipelines": {
                    "amd": ["amd sharded group"],
                    "upstream": ["humming eval (h100)"],
                },
                "definitions": [],
                "evidence": {
                    "pipeline": "amd",
                    "build_number": 100,
                    "build_commit": "a" * 40,
                    "build_state": "passed",
                    "roster_complete": True,
                    "result_file": "2026-08-12_amd.jsonl",
                    "job_names": ["mi300_1: AMD Sharded Group 1"],
                },
            }
        )
    )
    (results / "2026-08-12_amd.jsonl").write_text(
        json.dumps({"job_name": "mi300_1: AMD Sharded Group 1"}) + "\n"
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_shard_bases()

    assert "shard-bases-unused" not in {
        finding.code for finding in audit.report.errors
    }


def test_shard_audit_uses_completed_evidence_file_not_newer_partial_file(
    tmp_path: Path,
) -> None:
    ci = tmp_path / "data/vllm/ci"
    results = ci / "test_results"
    results.mkdir(parents=True)
    (ci / "shard_bases.json").write_text(json.dumps(["completed group"]))
    (ci / "shard_base_catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"commit_sha": "a" * 40},
                "normalization_bases": ["completed group"],
                "pipelines": {"amd": ["completed group"], "upstream": []},
                "definitions": [],
                "evidence": {
                    "pipeline": "amd",
                    "build_number": 100,
                    "build_commit": "a" * 40,
                    "build_state": "passed",
                    "roster_complete": True,
                    "result_file": "2026-08-11_amd.jsonl",
                    "job_names": ["mi300_1: Completed Group 1"],
                },
            }
        )
    )
    (results / "2026-08-11_amd.jsonl").write_text(
        json.dumps({"job_name": "mi300_1: Parsed Different Group"}) + "\n"
    )
    (results / "2026-08-12_amd.jsonl").write_text(
        json.dumps({"job_name": "mi300_1: Still Running"}) + "\n"
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_shard_bases()

    assert not audit.report.errors


def test_unavailable_shard_evidence_is_liveness_safe(tmp_path: Path) -> None:
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)
    (ci / "shard_bases.json").write_text(json.dumps(["scheduled group"]))
    (ci / "shard_base_catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"commit_sha": "a" * 40},
                "normalization_bases": ["scheduled group"],
                "pipelines": {"amd": ["scheduled group"], "upstream": []},
                "definitions": [],
                "evidence": {
                    "pipeline": "amd",
                    "build_number": 0,
                    "build_commit": "",
                    "build_state": "unavailable",
                    "roster_complete": False,
                    "result_file": "",
                    "job_names": [],
                },
            }
        )
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_shard_bases()

    assert not audit.report.errors
    assert {finding.code for finding in audit.report.warnings} == {
        "shard-evidence-unavailable"
    }


@pytest.mark.parametrize(
    "evidence",
    [
        {},
        {
            "pipeline": "amd",
            "build_number": 0,
            "build_commit": "a" * 40,
            "build_state": "unavailable",
            "roster_complete": False,
            "result_file": "",
            "job_names": [],
        },
        {
            "pipeline": "amd",
            "build_number": 101,
            "build_commit": "a" * 40,
            "build_state": "running",
            "roster_complete": False,
            "result_file": "2026-08-12_amd.jsonl",
            "job_names": ["mi300_1: Scheduled Group 1"],
        },
        {
            "pipeline": "amd",
            "build_number": 101,
            "build_commit": "a" * 40,
            "build_state": "running",
            "roster_complete": "false",
            "result_file": "",
            "job_names": ["mi300_1: Scheduled Group 1"],
        },
        {
            "pipeline": "amd",
            "build_number": 101,
            "build_commit": "a" * 40,
            "build_state": "running",
            "roster_complete": True,
            "result_file": "2026-08-12_amd.jsonl",
            "job_names": ["mi300_1: Scheduled Group 1"],
        },
        {
            "pipeline": "amd",
            "build_number": 101,
            "build_commit": "a" * 40,
            "build_state": "passed",
            "roster_complete": True,
            "result_file": "../2026-08-12_amd.jsonl",
            "job_names": ["mi300_1: Scheduled Group 1"],
        },
    ],
)
def test_invalid_shard_evidence_union_is_rejected(
    tmp_path: Path,
    evidence: dict,
) -> None:
    ci = tmp_path / "data/vllm/ci"
    ci.mkdir(parents=True)
    (ci / "shard_bases.json").write_text(json.dumps(["scheduled group"]))
    (ci / "shard_base_catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"commit_sha": "a" * 40},
                "normalization_bases": ["scheduled group"],
                "pipelines": {"amd": ["scheduled group"], "upstream": []},
                "definitions": [],
                "evidence": evidence,
            }
        )
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_shard_bases()

    assert {finding.code for finding in audit.report.errors} == {
        "publication-source-shape"
    }
    assert not audit.report.warnings


def test_nonterminal_shard_evidence_is_a_warning_not_an_error(tmp_path: Path) -> None:
    ci = tmp_path / "data/vllm/ci"
    results = ci / "test_results"
    results.mkdir(parents=True)
    (ci / "shard_bases.json").write_text(json.dumps(["scheduled group"]))
    (ci / "shard_base_catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"commit_sha": "a" * 40},
                "normalization_bases": ["scheduled group"],
                "pipelines": {"amd": ["scheduled group"], "upstream": []},
                "definitions": [],
                "evidence": {
                    "pipeline": "amd",
                    "build_number": 101,
                    "build_commit": "a" * 40,
                    "build_state": "running",
                    "roster_complete": False,
                    "result_file": "",
                    "job_names": ["mi300_1: Scheduled Group 1"],
                },
            }
        )
    )
    (results / "2026-08-12_amd.jsonl").write_text(
        json.dumps({"job_name": "mi300_1: Other Group"}) + "\n"
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_shard_bases()

    assert not audit.report.errors
    assert {finding.code for finding in audit.report.warnings} == {
        "shard-evidence-provisional"
    }


def test_optional_amd_shard_absence_is_a_warning(tmp_path: Path) -> None:
    ci = tmp_path / "data/vllm/ci"
    results = ci / "test_results"
    results.mkdir(parents=True)
    (ci / "shard_bases.json").write_text(json.dumps(["optional group"]))
    (ci / "shard_base_catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"commit_sha": "a" * 40},
                "normalization_bases": ["optional group"],
                "pipelines": {"amd": ["optional group"], "upstream": []},
                "definitions": [
                    {
                        "base": "optional group",
                        "pipeline": "amd",
                        "optional": True,
                    }
                ],
                "evidence": {
                    "pipeline": "amd",
                    "build_number": 101,
                    "build_commit": "a" * 40,
                    "build_state": "passed",
                    "roster_complete": True,
                    "result_file": "2026-08-12_amd.jsonl",
                    "job_names": ["mi300_1: Other Group"],
                },
            }
        )
    )
    (results / "2026-08-12_amd.jsonl").write_text(
        json.dumps({"job_name": "mi300_1: Other Group"}) + "\n"
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_shard_bases()

    assert not audit.report.errors
    assert {finding.code for finding in audit.report.warnings} == {
        "shard-bases-optional-unobserved"
    }


def test_shard_source_must_match_completed_evidence_commit(tmp_path: Path) -> None:
    ci = tmp_path / "data/vllm/ci"
    results = ci / "test_results"
    results.mkdir(parents=True)
    (ci / "shard_bases.json").write_text(json.dumps(["completed group"]))
    (ci / "shard_base_catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"commit_sha": "a" * 40},
                "normalization_bases": ["completed group"],
                "pipelines": {"amd": ["completed group"], "upstream": []},
                "definitions": [],
                "evidence": {
                    "pipeline": "amd",
                    "build_number": 101,
                    "build_commit": "b" * 40,
                    "build_state": "passed",
                    "roster_complete": True,
                    "result_file": "2026-08-12_amd.jsonl",
                    "job_names": ["mi300_1: Completed Group 1"],
                },
            }
        )
    )
    (results / "2026-08-12_amd.jsonl").write_text(
        json.dumps({"job_name": "mi300_1: Completed Group 1"}) + "\n"
    )

    audit = DashboardAudit(tmp_path)
    audit.audit_shard_bases()

    assert {finding.code for finding in audit.report.errors} == {
        "shard-config-evidence-mismatch"
    }
