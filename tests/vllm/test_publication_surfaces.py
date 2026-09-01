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
from vllm import publication_surfaces as surfaces_module
from vllm import select_publication_surfaces as selector_module
from vllm.audit_dashboard_data import DashboardAudit, Finding
from vllm.publication_surfaces import (
    AGENT_HEALTH_WATCHER_STATE_PATHS,
    CI_CORE_WATCHER_STATE_PATHS,
    GLOBAL_DATA_PATHS,
    LEGACY_CI_SURFACE_SPEC,
    PRE_ANALYTICS_CI_CORE_SURFACE_SPEC,
    PRE_ANALYTICS_CI_GATING_SURFACE_SPEC,
    PRE_QUEUE_SPLIT_SURFACE_CONTRACT_VERSION,
    PRE_QUEUE_SPLIT_SURFACE_SPEC,
    SOURCE_SURFACES,
    SURFACE_SPECS,
    SurfaceSpec,
    finding_surfaces,
    public_manifest_ownership_path,
    surface_for_path,
)
from vllm.select_publication_surfaces import load_collector_failures, restore_surface


ROOT = Path(__file__).resolve().parents[2]


def test_surface_contract_version_has_one_owner() -> None:
    assert surfaces_module.SURFACE_CONTRACT_VERSION == 5
    assert (
        selector_module.SURFACE_CONTRACT_VERSION
        == surfaces_module.SURFACE_CONTRACT_VERSION
    )
    assert (
        audit_module.SURFACE_CONTRACT_VERSION
        == surfaces_module.SURFACE_CONTRACT_VERSION
    )


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
        "docs/assets/js/ops-v2.js",
    )

    assert finding_surfaces(matrix) == frozenset({"ci_core"})
    assert finding_surfaces(queue_source) == frozenset({"queue"})
    assert finding_surfaces(stale_queue_source) == frozenset({"queue"})
    assert finding_surfaces(docs) == frozenset()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
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


def _write_v4_queue_fallback_baseline(
    repo: Path,
    since: str,
) -> tuple[Path, dict[str, bytes]]:
    baseline_bytes: dict[str, bytes] = {}
    for relative in (
        *PRE_QUEUE_SPLIT_SURFACE_SPEC.required_paths,
        *PRE_QUEUE_SPLIT_SURFACE_SPEC.optional_paths,
    ):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps({"selection": "v4-baseline", "path": relative}) + "\n"
        ).encode()
        path.write_bytes(payload)
        baseline_bytes[relative] = payload

    state_path = repo / "data/vllm/ci/publication_state.json"
    manifest = {
        relative: _manifest_descriptor(payload)
        for relative, payload in baseline_bytes.items()
    }
    state_path.write_text(json.dumps({
        "schema_version": 2,
        "surface_contract_version": PRE_QUEUE_SPLIT_SURFACE_CONTRACT_VERSION,
        "generated_at": since,
        "baseline_ref": "0" * 40,
        "mode": "fallback",
        "degraded_surfaces": ["queue"],
        "fresh_degraded_surfaces": [],
        "fallback_surfaces": ["queue"],
        "degraded_since": {"queue": since},
        "fallback_since": {"queue": since},
        "fallback_max_age_hours": 36,
        "restored_paths": {"queue": sorted(manifest)},
        "restored_manifest": {"queue": manifest},
    }))
    return state_path, baseline_bytes


def test_refresh_only_dns_recovers_without_clearing_unrelated_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    queue_relative = "data/queue_lifecycle.json"
    dns_relative = "data/dns_failures.json"
    hotness_relative = "data/hotness.json"
    state_relative = "data/publication_state.json"
    queue_path = repo / queue_relative
    dns_path = repo / dns_relative
    hotness_path = repo / hotness_relative
    state_path = repo / state_relative
    queue_path.parent.mkdir(parents=True)
    queue_payload = b'{"generation":"queue-fallback"}\n'
    old_dns_payload = b'{"generation":"dns-fallback"}\n'
    queue_path.write_bytes(queue_payload)
    dns_path.write_bytes(old_dns_payload)
    hotness_path.write_text('{"generation":"fresh-degraded"}\n')
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    queue_failure = {
        "schema_version": 1,
        "surface": "queue_lifecycle",
        "collector": "queue-lifecycle-sync",
        "step": "Sync validated queue lifecycle aggregate",
        "reason_class": "schema-drift",
        "exit_code": 1,
        "details": {},
        "persistence_runs": 3,
        "alertable": True,
    }
    dns_failure = {
        "schema_version": 1,
        "surface": "dns_health",
        "collector": "dns-health-sync",
        "step": "Sync validated DNS health aggregate",
        "reason_class": "schema-drift",
        "exit_code": 1,
        "details": {},
        "persistence_runs": 1,
        "alertable": True,
    }
    queue_identity = selector_module._collector_failure_persistence_identity(
        queue_failure
    )
    dns_identity = selector_module._collector_failure_persistence_identity(dns_failure)
    retry_observation = {
        "kind": "published-build-active-retry",
        "surface": "ci_core",
        "pipeline": "amd",
        "build_number": 123,
        "candidate_test_signal_build_number": 122,
        "persistence_runs": 2,
        "alertable": True,
    }
    retry_identity = selector_module._upstream_retry_identity(retry_observation)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "surface_contract_version": selector_module.SURFACE_CONTRACT_VERSION,
                "generated_at": since,
                "baseline_ref": "0" * 40,
                "mode": "mixed",
                "degraded_surfaces": [
                    "ci_hotness",
                    "dns_health",
                    "queue_lifecycle",
                ],
                "fresh_degraded_surfaces": ["ci_hotness"],
                "fallback_surfaces": ["dns_health", "queue_lifecycle"],
                "degraded_since": {
                    "ci_hotness": since,
                    "dns_health": since,
                    "queue_lifecycle": since,
                },
                "fallback_since": {
                    "dns_health": since,
                    "queue_lifecycle": since,
                },
                "fallback_max_age_hours": 36,
                "collector_failures": [dns_failure, queue_failure],
                "collector_failure_streaks": {
                    dns_identity: 1,
                    queue_identity: 3,
                },
                "collector_incident_policy": {
                    "alert": True,
                    "reason": "deterministic-or-unclassified-collector-failure",
                    "transient_persistence_runs_required": 2,
                    "max_observed_streak": 3,
                },
                "incident_policy": {
                    "alert": True,
                    "reason": "deterministic-or-unclassified-collector-failure",
                    "transient_persistence_runs_required": 2,
                    "max_observed_streak": 3,
                },
                "upstream_retry_observations": [retry_observation],
                "upstream_retry_streaks": {
                    retry_identity: 2,
                    "stale-retry-without-an-observation": 99,
                },
                "candidate_errors": [],
                "candidate_degradations": [],
                "final_errors": [],
                "final_degradations": [],
                "restored_paths": {
                    "dns_health": [dns_relative],
                    "queue_lifecycle": [queue_relative],
                },
                "restored_manifest": {
                    "dns_health": {
                        dns_relative: _manifest_descriptor(old_dns_payload),
                    },
                    "queue_lifecycle": {
                        queue_relative: _manifest_descriptor(queue_payload),
                    },
                },
            }
        )
    )
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated mixed fallback")
    baseline = _git(repo, "rev-parse", "HEAD")
    new_dns_payload = b'{"generation":"dns-current"}\n'
    dns_path.write_bytes(new_dns_payload)

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
        {
            "dns_health": SurfaceSpec(required_paths=(dns_relative,)),
            "ci_hotness": SurfaceSpec(required_paths=(hotness_relative,)),
            "ci_core": SurfaceSpec(required_paths=()),
            "queue_lifecycle": SurfaceSpec(required_paths=(queue_relative,)),
        },
    )
    monkeypatch.setattr(selector_module, "DashboardAudit", CleanAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    state = selector_module.select_publication(
        repo,
        baseline,
        state_path,
        refresh_only_surface="dns_health",
    )

    assert dns_path.read_bytes() == new_dns_payload
    assert queue_path.read_bytes() == queue_payload
    assert state["mode"] == "mixed"
    assert state["fresh_degraded_surfaces"] == ["ci_hotness"]
    assert state["fallback_surfaces"] == ["queue_lifecycle"]
    assert state["fallback_since"] == {"queue_lifecycle": since}
    assert state["degraded_since"] == {
        "ci_hotness": since,
        "queue_lifecycle": since,
    }
    assert state["restored_paths"] == {
        "queue_lifecycle": [queue_relative],
    }
    assert set(state["restored_manifest"]) == {"queue_lifecycle"}
    assert state["collector_failures"] == [queue_failure]
    assert state["collector_failure_streaks"] == {queue_identity: 3}
    assert state["upstream_retry_observations"] == [retry_observation]
    assert state["upstream_retry_streaks"] == {retry_identity: 2}


def test_refresh_only_transient_target_failure_cannot_suppress_preserved_incident(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    dns_relative = "data/dns_failures.json"
    queue_relative = "data/queue_lifecycle.json"
    state_relative = "data/publication_state.json"
    dns_path = repo / dns_relative
    queue_path = repo / queue_relative
    state_path = repo / state_relative
    dns_path.parent.mkdir(parents=True)
    dns_payload = b'{"generation":"dns-baseline"}\n'
    queue_payload = b'{"generation":"queue-fallback"}\n'
    dns_path.write_bytes(dns_payload)
    queue_path.write_bytes(queue_payload)
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    queue_failure = {
        "schema_version": 1,
        "surface": "queue_lifecycle",
        "collector": "queue-lifecycle-sync",
        "step": "Sync validated queue lifecycle aggregate",
        "reason_class": "schema-drift",
        "exit_code": 1,
        "details": {},
        "persistence_runs": 3,
        "alertable": True,
    }
    queue_identity = selector_module._collector_failure_persistence_identity(
        queue_failure
    )
    preserved_policy = {
        "alert": True,
        "reason": "deterministic-or-unclassified-collector-failure",
        "transient_persistence_runs_required": 2,
        "max_observed_streak": 3,
    }
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "surface_contract_version": selector_module.SURFACE_CONTRACT_VERSION,
                "generated_at": since,
                "baseline_ref": "0" * 40,
                "mode": "fallback",
                "degraded_surfaces": ["queue_lifecycle"],
                "fresh_degraded_surfaces": [],
                "fallback_surfaces": ["queue_lifecycle"],
                "degraded_since": {"queue_lifecycle": since},
                "fallback_since": {"queue_lifecycle": since},
                "fallback_max_age_hours": 36,
                "collector_failures": [queue_failure],
                "collector_failure_streaks": {queue_identity: 3},
                "collector_incident_policy": preserved_policy,
                "incident_policy": preserved_policy,
                "upstream_retry_observations": [],
                "upstream_retry_streaks": {},
                "candidate_errors": [],
                "candidate_degradations": [],
                "final_errors": [],
                "final_degradations": [],
                "restored_paths": {"queue_lifecycle": [queue_relative]},
                "restored_manifest": {
                    "queue_lifecycle": {
                        queue_relative: _manifest_descriptor(queue_payload),
                    }
                },
            }
        )
    )
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated queue fallback")
    baseline = _git(repo, "rev-parse", "HEAD")

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
        {
            "dns_health": SurfaceSpec(required_paths=(dns_relative,)),
            "queue_lifecycle": SurfaceSpec(required_paths=(queue_relative,)),
        },
    )
    monkeypatch.setattr(selector_module, "DashboardAudit", CleanAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    output = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    dns_failure = {
        "schema_version": 1,
        "surface": "dns_health",
        "collector": "dns-health-sync",
        "step": "Sync validated DNS health aggregate",
        "reason_class": "timeout",
        "exit_code": 1,
        "details": {},
    }
    dns_identity = selector_module._collector_failure_persistence_identity(
        dns_failure
    )

    state = selector_module.select_publication(
        repo,
        baseline,
        state_path,
        collector_failures=[dns_failure],
        refresh_only_surface="dns_health",
    )

    assert state["fallback_surfaces"] == ["dns_health", "queue_lifecycle"]
    assert state["fallback_since"]["queue_lifecycle"] == since
    assert state["degraded_since"]["queue_lifecycle"] == since
    assert state["collector_failure_streaks"] == {
        dns_identity: 1,
        queue_identity: 3,
    }
    assert state["collector_incident_policy"]["alert"] is True
    assert state["collector_incident_policy"]["max_observed_streak"] == 3
    assert state["incident_policy"]["alert"] is True
    assert state["incident_policy"]["max_observed_streak"] == 3
    assert "alertable_degradation=true" in output.read_text()
    assert "transient_alert_suppressed=false" in output.read_text()


def test_refresh_only_rejects_an_unrelated_candidate_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    queue_relative = "data/queue_lifecycle.json"
    dns_relative = "data/dns_failures.json"
    state_relative = "data/publication_state.json"
    for relative in (queue_relative, dns_relative):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"generation":"baseline"}\n')
    state_path = repo / state_relative
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "surface_contract_version": selector_module.SURFACE_CONTRACT_VERSION,
                "generated_at": "2026-09-01T00:00:00Z",
                "baseline_ref": "0" * 40,
                "mode": "current",
                "degraded_surfaces": [],
                "fresh_degraded_surfaces": [],
                "fallback_surfaces": [],
                "degraded_since": {},
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
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated current publication")
    baseline = _git(repo, "rev-parse", "HEAD")
    (repo / dns_relative).write_text('{"generation":"dns-current"}\n')
    (repo / queue_relative).write_text('{"generation":"queue-unexpected"}\n')
    monkeypatch.setattr(
        selector_module,
        "SURFACE_SPECS",
        {
            "dns_health": SurfaceSpec(required_paths=(dns_relative,)),
            "queue_lifecycle": SurfaceSpec(required_paths=(queue_relative,)),
        },
    )

    with pytest.raises(RuntimeError, match="changed non-target paths"):
        selector_module.select_publication(
            repo,
            baseline,
            state_path,
            refresh_only_surface="dns_health",
        )


def _refresh_only_candidate_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = tmp_path / "repo"
    dns_relative = "data/dns_failures.json"
    queue_relative = "data/queue_lifecycle.json"
    for relative in (dns_relative, queue_relative):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"generation":"baseline"}\n')
    policy = repo / "config/dashboard_state.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({"generated_roots": ["data", "dashboards", "README.md"]}))
    source = repo / "scripts/collector.py"
    source.parent.mkdir(parents=True)
    source.write_text('VERSION = "baseline"\n')
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated dashboard baseline")
    baseline = _git(repo, "rev-parse", "HEAD")
    source.write_text('VERSION = "candidate"\n')
    _git(repo, "add", "scripts/collector.py")
    _git(repo, "commit", "-m", "legitimate candidate code change")
    candidate = _git(repo, "rev-parse", "HEAD")
    return repo, baseline, candidate, dns_relative


def test_refresh_only_candidate_code_ref_allows_legitimate_source_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, baseline, candidate, dns_relative = _refresh_only_candidate_repo(tmp_path)
    (repo / dns_relative).write_text('{"generation":"candidate"}\n')
    monkeypatch.setattr(
        selector_module,
        "SURFACE_SPECS",
        {
            "dns_health": SurfaceSpec(required_paths=(dns_relative,)),
            "queue_lifecycle": SurfaceSpec(
                required_paths=("data/queue_lifecycle.json",)
            ),
        },
    )

    selector_module._validate_refresh_only_candidate(
        repo,
        baseline,
        "dns_health",
        candidate,
    )


def test_refresh_only_candidate_code_ref_still_anchors_generated_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, baseline, _, dns_relative = _refresh_only_candidate_repo(tmp_path)
    queue_relative = "data/queue_lifecycle.json"
    (repo / queue_relative).write_text('{"generation":"candidate-code"}\n')
    _git(repo, "add", queue_relative)
    _git(repo, "commit", "-m", "candidate contains unrelated generated data")
    candidate = _git(repo, "rev-parse", "HEAD")
    (repo / dns_relative).write_text('{"generation":"candidate"}\n')
    monkeypatch.setattr(
        selector_module,
        "SURFACE_SPECS",
        {
            "dns_health": SurfaceSpec(required_paths=(dns_relative,)),
            "queue_lifecycle": SurfaceSpec(required_paths=(queue_relative,)),
        },
    )

    with pytest.raises(RuntimeError, match="data/queue_lifecycle.json"):
        selector_module._validate_refresh_only_candidate(
            repo,
            baseline,
            "dns_health",
            candidate,
        )


def test_refresh_only_without_candidate_code_ref_keeps_legacy_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, baseline, _, dns_relative = _refresh_only_candidate_repo(tmp_path)
    (repo / dns_relative).write_text('{"generation":"candidate"}\n')
    monkeypatch.setattr(
        selector_module,
        "SURFACE_SPECS",
        {"dns_health": SurfaceSpec(required_paths=(dns_relative,))},
    )

    with pytest.raises(RuntimeError, match="scripts/collector.py"):
        selector_module._validate_refresh_only_candidate(
            repo,
            baseline,
            "dns_health",
        )


def test_selector_cli_accepts_candidate_code_ref() -> None:
    args = selector_module.parse_args([
        "--baseline-ref",
        "a" * 40,
        "--candidate-code-ref",
        "b" * 40,
    ])

    assert args.candidate_code_ref == "b" * 40


def test_refresh_only_still_blocks_an_unrouted_full_audit_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    dns_relative = "data/dns_failures.json"
    state_relative = "data/publication_state.json"
    dns_path = repo / dns_relative
    state_path = repo / state_relative
    dns_path.parent.mkdir(parents=True)
    dns_path.write_text('{"generation":"baseline"}\n')
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "surface_contract_version": selector_module.SURFACE_CONTRACT_VERSION,
                "generated_at": "2026-09-01T00:00:00Z",
                "baseline_ref": "0" * 40,
                "mode": "current",
                "degraded_surfaces": [],
                "fresh_degraded_surfaces": [],
                "fallback_surfaces": [],
                "degraded_since": {},
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
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated current publication")
    baseline = _git(repo, "rev-parse", "HEAD")
    dns_path.write_text('{"generation":"candidate"}\n')

    global_error = Finding(
        "error",
        "global-contract-failure",
        "full audit found an unrouted invariant failure",
        "data/unowned.json",
    )

    class GloballyInvalidAudit:
        def __init__(self, *args, **kwargs):
            self.report = SimpleNamespace(errors=[], degradations=[])

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self) -> SimpleNamespace:
            return SimpleNamespace(errors=[global_error], degradations=[])

    monkeypatch.setattr(
        selector_module,
        "SURFACE_SPECS",
        {"dns_health": SurfaceSpec(required_paths=(dns_relative,))},
    )
    monkeypatch.setattr(selector_module, "DashboardAudit", GloballyInvalidAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    with pytest.raises(RuntimeError, match="global or unrouted errors"):
        selector_module.select_publication(
            repo,
            baseline,
            state_path,
            refresh_only_surface="dns_health",
        )

    blocked = json.loads(state_path.read_text())
    assert blocked["mode"] == "blocked"
    assert blocked["final_errors"][0]["code"] == "global-contract-failure"


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
    assert audit_runs == [False, True, True]


def test_collector_failure_jsonl_is_typed_bounded_and_secret_safe(
    tmp_path: Path,
) -> None:
    path = tmp_path / "collector-failures.jsonl"
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "surface": "ci_analytics",
            "collector": "collect_analytics.py",
            "step": "Collect CI analytics",
            "reason_class": "payload-budget",
            "exit_code": 1,
            "details": {
                "summary": (
                    "token=do-not-publish https://buildkite.example/failure "
                    "payload exceeds budget"
                ),
                "observed_bytes": 99_219_601,
                "max_bytes": 94_371_840,
                "component_bytes": {
                    "ci": {
                        "bytes": 80_000_000,
                        "components": {
                            "all_main_reliability": 54_500_000,
                        },
                    },
                },
            },
        })
        + "\n"
    )

    [record] = load_collector_failures(path)

    assert record["collector"] == "collect_analytics.py"
    assert record["step"] == "Collect CI analytics"
    assert record["reason_class"] == "payload-budget"
    assert record["details"]["observed_bytes"] == 99_219_601
    assert record["details"]["component_bytes"]["ci"]["components"] == {
        "all_main_reliability": 54_500_000,
    }
    assert "do-not-publish" not in record["details"]["summary"]
    assert "buildkite.example" not in record["details"]["summary"]


@pytest.mark.parametrize(
    ("reason_class", "expected_alertable"),
    (
        ("payload-budget", True),
        ("timeout", False),
    ),
)
def test_typed_analytics_failure_restores_only_analytics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reason_class: str,
    expected_alertable: bool,
) -> None:
    repo = tmp_path / "repo"
    paths = {
        "ci_core": repo / "data/core.json",
        "ci_analytics": repo / "data/analytics.json",
        "ci_gating": repo / "data/gating.json",
    }
    for surface, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"surface": surface, "version": "baseline"}))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated split baseline")
    baseline = _git(repo, "rev-parse", "HEAD")
    for surface, path in paths.items():
        path.write_text(json.dumps({"surface": surface, "version": "candidate"}))

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
        {
            surface: SurfaceSpec(
                required_paths=(path.relative_to(repo).as_posix(),)
            )
            for surface, path in paths.items()
        },
    )
    monkeypatch.setattr(selector_module, "DashboardAudit", CleanAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    output = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    state = selector_module.select_publication(
        repo,
        baseline,
        repo / "publication-state.json",
        collector_failures=({
            "schema_version": 1,
            "surface": "ci_analytics",
            "collector": "collect_analytics.py",
            "step": "Collect CI analytics",
            "reason_class": reason_class,
            "exit_code": 1,
            "details": {
                "observed_bytes": 99_219_601,
                "max_bytes": 94_371_840,
            },
        },),
    )

    assert json.loads(paths["ci_analytics"].read_text())["version"] == "baseline"
    assert json.loads(paths["ci_core"].read_text())["version"] == "candidate"
    assert json.loads(paths["ci_gating"].read_text())["version"] == "candidate"
    assert state["fallback_surfaces"] == ["ci_analytics"]
    assert state["collector_incident_policy"]["alert"] is expected_alertable
    assert state["collector_failures"][0]["alertable"] is expected_alertable
    finding = state["candidate_errors"][0]
    assert finding["context"]["collector"] == "collect_analytics.py"
    assert finding["context"]["reason_class"] == reason_class
    assert finding["context"]["alertable"] is expected_alertable
    expected_output = (
        "alertable_degradation=true"
        if expected_alertable
        else "alertable_degradation=false"
    )
    assert expected_output in output.read_text()


@pytest.mark.parametrize(
    ("fallback_surface", "advanced_surface"),
    (
        ("ci_analytics", "ci_core"),
        ("ci_core", "ci_analytics"),
    ),
)
def test_selector_keeps_attested_analytics_core_build_skew_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback_surface: str,
    advanced_surface: str,
) -> None:
    repo = tmp_path / "repo"
    relative_paths = {
        "ci_analytics": "data/vllm/ci/analytics.json",
        "ci_core": "data/vllm/ci/amd_test_matrix.json",
    }
    paths = {
        surface: repo / relative
        for surface, relative in relative_paths.items()
    }
    for surface, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"surface": surface, "build": 100}))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated split build baseline")
    baseline = _git(repo, "rev-parse", "HEAD")

    for surface, path in paths.items():
        build = 101 if surface == advanced_surface else 100
        path.write_text(json.dumps({"surface": surface, "build": build}))

    specs = {
        surface: SurfaceSpec(required_paths=(relative_paths[surface],))
        for surface in relative_paths
    }
    audit_runs: list[tuple[bool, list[str], list[str]]] = []

    class BuildAlignmentAudit(DashboardAudit):
        def __init__(self, *args, allow_publication_fallback: bool, **kwargs):
            super().__init__(
                *args,
                allow_publication_fallback=allow_publication_fallback,
                **kwargs,
            )
            self._allow_fallback = allow_publication_fallback

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self):
            analytics_build = self.load_json(
                relative_paths["ci_analytics"], {}
            ).get("build")
            core_build = self.load_json(
                relative_paths["ci_core"], {}
            ).get("build")
            if analytics_build != core_build:
                self.report_cross_surface_build_mismatch(
                    "matrix-analytics-build",
                    (
                        f"matrix source build #{core_build} does not match "
                        f"analytics AMD latest #{analytics_build}"
                    ),
                    relative_paths["ci_core"],
                    left_surface="ci_core",
                    left_build=core_build,
                    right_surface="ci_analytics",
                    right_build=analytics_build,
                )
            audit_runs.append((
                self._allow_fallback,
                [finding.code for finding in self.report.errors],
                [finding.code for finding in self.report.warnings],
            ))
            return self.report

    monkeypatch.setattr(selector_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(audit_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(surfaces_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(selector_module, "DashboardAudit", BuildAlignmentAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    state = selector_module.select_publication(
        repo,
        baseline,
        repo / "data/vllm/ci/publication_state.json",
        forced_degraded=(fallback_surface,),
    )

    assert state["mode"] == "fallback"
    assert state["fallback_surfaces"] == [fallback_surface]
    assert state["final_errors"] == []
    assert json.loads(paths[fallback_surface].read_text())["build"] == 100
    assert json.loads(paths[advanced_surface].read_text())["build"] == 101
    assert audit_runs == [
        (
            True,
            [],
            ["matrix-analytics-build-fallback-skew"],
        ),
        (
            True,
            [],
            ["matrix-analytics-build-fallback-skew"],
        ),
    ]


def test_selector_expands_fallback_until_retry_cohort_is_consistent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    relative_paths = {
        "ci_core": "data/vllm/ci/amd_test_matrix.json",
        "ci_analytics": "data/vllm/ci/analytics.json",
    }
    paths = {
        surface: repo / relative
        for surface, relative in relative_paths.items()
    }
    for surface, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"surface": surface, "build": 101}))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated retry cohort")
    baseline = _git(repo, "rev-parse", "HEAD")

    for surface, path in paths.items():
        path.write_text(json.dumps({"surface": surface, "build": 100}))

    specs = {
        surface: SurfaceSpec(required_paths=(relative_paths[surface],))
        for surface in relative_paths
    }
    audit_runs: list[bool] = []

    class RetryCohortAudit:
        def __init__(self, *args, allow_publication_fallback: bool, **kwargs):
            self._allow_fallback = allow_publication_fallback
            self.report = SimpleNamespace(errors=[], degradations=[])

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self) -> SimpleNamespace:
            audit_runs.append(self._allow_fallback)
            if len(audit_runs) == 1:
                errors = [
                    Finding(
                        "error",
                        "operations-stale-source",
                        "AMD test signal became stale while a retry was running",
                        "data/vllm/ci/operations_v2.json",
                        {"source": "amd_test_signal"},
                    )
                ]
            elif len(audit_runs) == 2:
                errors = [
                    Finding(
                        "error",
                        "matrix-analytics-build",
                        "restored matrix is newer than current analytics",
                        relative_paths["ci_core"],
                    ),
                    Finding(
                        "error",
                        "analytics-jsonl-build-mismatch",
                        "current analytics is older than the restored CI ledger",
                        relative_paths["ci_analytics"],
                    ),
                ]
            else:
                errors = []
            return SimpleNamespace(errors=errors, degradations=[])

    monkeypatch.setattr(selector_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(audit_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(surfaces_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(selector_module, "DashboardAudit", RetryCohortAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    state = selector_module.select_publication(
        repo,
        baseline,
        repo / "data/vllm/ci/publication_state.json",
    )

    assert state["mode"] == "fallback"
    assert state["fallback_surfaces"] == ["ci_analytics", "ci_core"]
    assert state["final_errors"] == []
    assert {
        surface: json.loads(path.read_text())["build"]
        for surface, path in paths.items()
    } == {"ci_core": 101, "ci_analytics": 101}
    assert audit_runs == [False, True, True]


def test_selector_fails_closed_when_fallback_audit_cannot_make_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    relative = "data/vllm/ci/amd_test_matrix.json"
    source = repo / relative
    source.parent.mkdir(parents=True)
    source.write_text('{"build":101}\n')
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated core cohort")
    baseline = _git(repo, "rev-parse", "HEAD")
    source.write_text('{"build":100}\n')

    specs = {"ci_core": SurfaceSpec(required_paths=(relative,))}
    audit_runs: list[bool] = []

    class NoProgressAudit:
        def __init__(self, *args, allow_publication_fallback: bool, **kwargs):
            self._allow_fallback = allow_publication_fallback
            self.report = SimpleNamespace(errors=[], degradations=[])

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self) -> SimpleNamespace:
            audit_runs.append(self._allow_fallback)
            if len(audit_runs) == 1:
                error = Finding(
                    "error",
                    "operations-stale-source",
                    "AMD test signal is stale",
                    "data/vllm/ci/operations_v2.json",
                    {"source": "amd_test_signal"},
                )
            else:
                error = Finding(
                    "error",
                    "matrix-summary-mismatch",
                    "restoring CI core did not repair its invariant",
                    relative,
                )
            return SimpleNamespace(errors=[error], degradations=[])

    monkeypatch.setattr(selector_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(audit_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(surfaces_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(selector_module, "DashboardAudit", NoProgressAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    state_path = repo / "data/vllm/ci/publication_state.json"

    with pytest.raises(
        RuntimeError,
        match="last-known-good surface selection still fails",
    ):
        selector_module.select_publication(repo, baseline, state_path)

    state = json.loads(state_path.read_text())
    assert state["mode"] == "blocked"
    assert state["fallback_surfaces"] == ["ci_core"]
    assert state["final_errors"][0]["code"] == "matrix-summary-mismatch"
    assert json.loads(source.read_text())["build"] == 101
    assert audit_runs == [False, True]


def test_active_retry_reconciliation_is_debounced_after_coherent_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    health_relative = "data/vllm/ci/ci_health.json"
    analytics_relative = "data/vllm/ci/analytics.json"
    health_path = repo / health_relative
    analytics_path = repo / analytics_relative
    health_path.parent.mkdir(parents=True)

    def health_payload(
        signal_build: int,
        *,
        active_retry: bool,
    ) -> dict:
        return {
            "upstream": {
                "latest_build": {"build_number": signal_build},
                "latest_test_signal_build": {"build_number": signal_build},
                "latest_pipeline_build": {
                    "build_number": 101,
                    "active_retry": active_retry,
                },
            }
        }

    health_path.write_text(json.dumps(health_payload(101, active_retry=False)))
    analytics_path.write_text(json.dumps({"build": 101}))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated completed retry build")
    baseline = _git(repo, "rev-parse", "HEAD")

    health_path.write_text(json.dumps(health_payload(100, active_retry=True)))
    analytics_path.write_text(json.dumps({"build": 100}))
    specs = {
        "ci_core": SurfaceSpec(required_paths=(health_relative,)),
        "ci_analytics": SurfaceSpec(required_paths=(analytics_relative,)),
    }
    audited_generations = []
    preflight_analytics_builds = []
    preflight_audits = []

    class ActiveRetryAudit:
        def __init__(self, *args, **kwargs):
            self.report = SimpleNamespace(
                errors=[],
                degradations=[],
                findings=[],
            )

        def audit_publication_surface_files(self) -> None:
            return None

        def audit_ci_health(self) -> None:
            preflight_audits.append("audit_ci_health")

        def audit_root_test_results(self) -> None:
            preflight_audits.append("audit_root_test_results")

        def audit_shard_bases(self) -> None:
            preflight_audits.append("audit_shard_bases")

        def audit_analytics(self) -> None:
            preflight_audits.append("audit_analytics")
            preflight_analytics_builds.append(
                json.loads(analytics_path.read_text())["build"]
            )

        def audit_amd_matrix(self) -> None:
            preflight_audits.append("audit_amd_matrix")

        def run(self) -> SimpleNamespace:
            audited_generations.append((
                json.loads(health_path.read_text())["upstream"][
                    "latest_test_signal_build"
                ]["build_number"],
                json.loads(analytics_path.read_text())["build"],
            ))
            return SimpleNamespace(errors=[], degradations=[])

    monkeypatch.setattr(selector_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(audit_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(surfaces_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(selector_module, "DashboardAudit", ActiveRetryAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    output = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    state_path = repo / "data/vllm/ci/publication_state.json"

    state = selector_module.select_publication(
        repo,
        baseline,
        state_path,
    )

    assert state["fallback_surfaces"] == ["ci_analytics", "ci_core"]
    assert json.loads(health_path.read_text()) == health_payload(
        101,
        active_retry=False,
    )
    assert json.loads(analytics_path.read_text()) == {"build": 101}
    assert state["incident_policy"]["alert"] is False
    assert state["incident_policy"]["reason"] == (
        "active-upstream-retry-first-observation"
    )
    assert audited_generations == [(101, 101), (101, 101)]
    assert preflight_analytics_builds == [100]
    assert preflight_audits == list(
        selector_module.UPSTREAM_RETRY_CANDIDATE_AUDITS
    )
    assert {
        finding["code"] for finding in state["candidate_errors"]
    } == {"publication-upstream-retry-provisional"}
    assert state["upstream_retry_observations"] == [{
        "kind": "published-build-active-retry",
        "surface": "ci_core",
        "pipeline": "upstream",
        "build_number": 101,
        "candidate_test_signal_build_number": 100,
        "persistence_runs": 1,
        "alertable": False,
    }]
    assert all(
        finding["context"]["alertable"] is False
        for finding in state["candidate_errors"]
    )
    assert "degraded=true" in output.read_text()
    assert "blocked=false" in output.read_text()
    assert "alertable_degradation=false" in output.read_text()
    assert "transient_alert_suppressed=true" in output.read_text()

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "publish first retry fallback")
    second_baseline = _git(repo, "rev-parse", "HEAD")
    health_path.write_text(json.dumps(health_payload(100, active_retry=True)))
    analytics_path.write_text(json.dumps({"build": 100}))
    audited_generations.clear()
    preflight_analytics_builds.clear()
    preflight_audits.clear()
    output.write_text("")

    second_state = selector_module.select_publication(
        repo,
        second_baseline,
        state_path,
    )

    assert audited_generations == [(101, 101), (101, 101)]
    assert preflight_analytics_builds == [100]
    assert preflight_audits == list(
        selector_module.UPSTREAM_RETRY_CANDIDATE_AUDITS
    )
    assert second_state["incident_policy"]["alert"] is True
    assert second_state["incident_policy"]["reason"] == (
        "active-upstream-retry-persisted"
    )
    assert second_state["upstream_retry_observations"][0][
        "persistence_runs"
    ] == 2
    assert "alertable_degradation=true" in output.read_text()
    assert "transient_alert_suppressed=false" in output.read_text()


@pytest.mark.parametrize(
    ("defect", "expected_code"),
    (
        ("summary", "matrix-summary"),
        ("analytics-build", "matrix-analytics-build"),
        ("health-build", "matrix-health-build"),
    ),
)
def test_active_retry_preflight_preserves_real_candidate_matrix_defects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
    expected_code: str,
) -> None:
    repo = tmp_path / "repo"
    health_relative = "data/vllm/ci/ci_health.json"
    analytics_relative = "data/vllm/ci/analytics.json"
    matrix_relative = "data/vllm/ci/amd_test_matrix.json"
    parity_relative = "data/vllm/ci/parity_report.json"
    paths = {
        relative: repo / relative
        for relative in (
            health_relative,
            analytics_relative,
            matrix_relative,
            parity_relative,
        )
    }
    paths[health_relative].parent.mkdir(parents=True)

    def health_payload(signal_build: int, *, active_retry: bool) -> dict:
        return {
            "amd": {
                "latest_build": {
                    "build_number": signal_build,
                    "by_hardware": {"mi300": {"groups": 1}},
                },
                "latest_test_signal_build": {"build_number": signal_build},
                "latest_pipeline_build": {
                    "build_number": 101,
                    "active_retry": active_retry,
                },
            }
        }

    def analytics_payload(build: int) -> dict:
        return {"amd-ci": {"builds": [{"number": build}]}}

    def matrix_payload(build: int, *, corrupt_summary: bool = False) -> dict:
        summary = {
            "unique_groups": 1,
            "architecture_count": 1,
            "hardware_cells": 1,
            "latest_matched_cells": 1,
            "passing_cells": 1,
            "failing_cells": 0,
            "waiting_cells": 0,
            "unknown_cells": 0,
            "fully_shared_groups": 1,
            "single_arch_groups": 1,
            "multi_variant_cells": 0,
        }
        if corrupt_summary:
            summary["unique_groups"] = 999
        return {
            "source": {"latest_build_number": build},
            "architectures": [{
                "id": "mi300",
                "group_count": 1,
                "nightly_match_count": 1,
            }],
            "rows": [{
                "id": "matrix-row",
                "title": "matrix row",
                "coverage_count": 1,
                "nightly_coverage_count": 1,
                "cells": {
                    "mi300": {
                        "exists": True,
                        "latest_matched": True,
                        "latest_state": "passed",
                        "raw_variant_count": 1,
                    }
                },
            }],
            "summary": summary,
        }

    baseline_health = health_payload(101, active_retry=False)
    baseline_analytics = analytics_payload(101)
    baseline_matrix = matrix_payload(101)
    parity = {
        "job_groups": [{
            "amd_hardware": ["mi300"],
            "amd_hw_failures": {},
            "amd_hw_canceled": {},
        }]
    }
    paths[health_relative].write_text(json.dumps(baseline_health))
    paths[analytics_relative].write_text(json.dumps(baseline_analytics))
    paths[matrix_relative].write_text(json.dumps(baseline_matrix))
    paths[parity_relative].write_text(json.dumps(parity))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated completed AMD build")
    baseline = _git(repo, "rev-parse", "HEAD")

    candidate_analytics_build = 99 if defect in {
        "analytics-build",
        "health-build",
    } else 100
    candidate_matrix_build = 99 if defect == "health-build" else 100
    paths[health_relative].write_text(
        json.dumps(health_payload(100, active_retry=True))
    )
    paths[analytics_relative].write_text(
        json.dumps(analytics_payload(candidate_analytics_build))
    )
    paths[matrix_relative].write_text(json.dumps(matrix_payload(
        candidate_matrix_build,
        corrupt_summary=defect == "summary",
    )))

    specs = {
        "ci_core": SurfaceSpec(required_paths=(
            health_relative,
            matrix_relative,
            parity_relative,
        )),
        "ci_analytics": SurfaceSpec(required_paths=(analytics_relative,)),
    }

    class RealMatrixPreflightAudit(DashboardAudit):
        def audit_publication_surface_files(self) -> None:
            return None

        def audit_ci_health(self) -> None:
            return None

        def audit_root_test_results(self) -> None:
            return None

        def audit_shard_bases(self) -> None:
            return None

        def audit_analytics(self) -> None:
            return None

        def run(self):
            return self.report

    monkeypatch.setattr(selector_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(audit_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(surfaces_module, "SURFACE_SPECS", specs)
    monkeypatch.setattr(selector_module, "DashboardAudit", RealMatrixPreflightAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    output = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    state = selector_module.select_publication(
        repo,
        baseline,
        repo / "data/vllm/ci/publication_state.json",
    )

    defect_findings = [
        finding
        for finding in state["candidate_errors"]
        if finding["code"] == expected_code
    ]
    assert len(defect_findings) == 1
    assert defect_findings[0]["context"]["publication_phase"] == (
        selector_module.UPSTREAM_RETRY_CANDIDATE_PHASE
    )
    assert state["mode"] == "fallback"
    assert state["fallback_surfaces"] == ["ci_analytics", "ci_core"]
    assert state["incident_policy"]["alert"] is True
    assert json.loads(paths[health_relative].read_text()) == baseline_health
    assert json.loads(paths[analytics_relative].read_text()) == baseline_analytics
    assert json.loads(paths[matrix_relative].read_text()) == baseline_matrix
    assert "alertable_degradation=true" in output.read_text()


def test_active_retry_alert_requires_two_consecutive_observations() -> None:
    observation = {
        "kind": "published-build-active-retry",
        "surface": "ci_core",
        "pipeline": "amd",
        "build_number": 101,
        "candidate_test_signal_build_number": 100,
    }
    first, first_streaks = selector_module._upstream_retry_incident_policy(
        [observation], None
    )
    second, second_streaks = selector_module._upstream_retry_incident_policy(
        [observation], {"upstream_retry_streaks": first_streaks}
    )
    recovered, recovered_streaks = selector_module._upstream_retry_incident_policy(
        [], {"upstream_retry_streaks": second_streaks}
    )
    after_recovery, _ = selector_module._upstream_retry_incident_policy(
        [observation], {"upstream_retry_streaks": recovered_streaks}
    )

    assert first["alert"] is False
    assert first["max_observed_streak"] == 1
    assert second["alert"] is True
    assert second["max_observed_streak"] == 2
    assert recovered["alert"] is False
    assert recovered_streaks == {}
    assert after_recovery["alert"] is False


def test_active_retry_candidate_mismatch_is_not_treated_as_restore_skew() -> None:
    record = {
        "code": "analytics-jsonl-build-mismatch",
        "surfaces": ["ci_analytics", "ci_core"],
        "context": {
            "pipeline": "upstream",
            "publication_phase": selector_module.UPSTREAM_RETRY_CANDIDATE_PHASE,
        },
    }

    assert selector_module._retry_reconciliation_finding(
        record,
        {"upstream"},
    ) is False


def test_active_retry_does_not_suppress_an_unrelated_publication_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "generated_at": "2026-09-01T12:34:56Z",
        "mode": "fallback",
        "degraded_surfaces": ["ci_core"],
        "fallback_surfaces": ["ci_core"],
        "collector_failures": [],
        "incident_policy": {
            "alert": True,
            "reason": "non-collector-publication-finding",
        },
        "candidate_errors": [{
            "severity": "error",
            "code": "matrix-summary-mismatch",
            "message": "matrix totals do not reconcile",
            "path": "data/vllm/ci/amd_test_matrix.json",
            "surfaces": ["ci_core"],
        }],
        "candidate_degradations": [],
        "final_errors": [],
        "final_degradations": [],
    }
    observation = {
        "kind": "published-build-active-retry",
        "surface": "ci_core",
        "pipeline": "amd",
        "build_number": 101,
        "candidate_test_signal_build_number": 100,
    }
    policy = {
        "alert": False,
        "reason": "active-upstream-retry-first-observation",
        "max_observed_streak": 1,
    }
    output = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    selector_module._apply_upstream_retry_reporting_policy(
        state,
        [observation],
        policy,
        forced=set(),
    )
    selector_module._emit_outputs(state)

    assert state["incident_policy"]["alert"] is True
    assert "context" not in state["candidate_errors"][0]
    assert "alertable_degradation=true" in output.read_text()
    assert "transient_alert_suppressed=false" in output.read_text()
    assert "generated_at=2026-09-01T12:34:56Z" in output.read_text()


def test_transient_collector_alert_requires_two_consecutive_fallbacks() -> None:
    failure = {
        "surface": "ci_analytics",
        "collector": "collect_analytics.py",
        "step": "Collect CI analytics",
        "reason_class": "rate-limit",
    }
    first, first_streaks = selector_module._collector_incident_policy(
        [failure], None, has_untyped_forced_surface=False
    )
    second, second_streaks = selector_module._collector_incident_policy(
        [failure], {"collector_failure_streaks": first_streaks},
        has_untyped_forced_surface=False,
    )
    recovered, recovered_streaks = selector_module._collector_incident_policy(
        [], {"collector_failure_streaks": second_streaks},
        has_untyped_forced_surface=False,
    )
    after_recovery, _ = selector_module._collector_incident_policy(
        [failure], {"collector_failure_streaks": recovered_streaks},
        has_untyped_forced_surface=False,
    )

    assert first["alert"] is False
    assert first["max_observed_streak"] == 1
    assert second["alert"] is True
    assert second["max_observed_streak"] == 2
    assert recovered_streaks == {}
    assert after_recovery["alert"] is False
    assert after_recovery["max_observed_streak"] == 1


def test_transient_collector_persistence_survives_reason_reclassification() -> None:
    first_failure = {
        "surface": "ci_analytics",
        "collector": "collect_analytics.py",
        "step": "Collect CI analytics",
        "reason_class": "timeout",
    }
    reclassified_failure = {
        **first_failure,
        "reason_class": "transient-http",
    }

    first, first_streaks = selector_module._collector_incident_policy(
        [first_failure], None, has_untyped_forced_surface=False
    )
    second, second_streaks = selector_module._collector_incident_policy(
        [reclassified_failure],
        {"collector_failure_streaks": first_streaks},
        has_untyped_forced_surface=False,
    )

    assert first["alert"] is False
    assert second["alert"] is True
    assert second["reason"] == "transient-collector-failure-persisted"
    assert second["max_observed_streak"] == 2
    assert len(first_streaks) == len(second_streaks) == 1


def test_legacy_ci_state_migrates_clocks_without_restoring_independent_domains(
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
        for surface in (
            "ci_core",
            "ci_analytics",
            "ci_gating",
            "ci_changes",
            "ci_hotness",
        )
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

    fallback = {"ci_core"}
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
    for surface in {"ci_analytics", "ci_gating", "ci_changes", "ci_hotness"}:
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
        "ci_analytics",
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


def test_pre_analytics_schema_v2_fallback_proof_and_clock_are_split(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path, baseline_bytes = _write_legacy_ci_baseline(repo, since)
    old_specs = {
        "ci_core": PRE_ANALYTICS_CI_CORE_SURFACE_SPEC,
        "ci_gating": PRE_ANALYTICS_CI_GATING_SURFACE_SPEC,
    }
    manifests = {
        surface: {
            relative: _manifest_descriptor(baseline_bytes[relative])
            for relative in spec.required_paths
        }
        for surface, spec in old_specs.items()
    }
    state_path.write_text(json.dumps({
        "schema_version": 2,
        "generated_at": since,
        "baseline_ref": "0" * 40,
        "mode": "fallback",
        "degraded_surfaces": ["ci_core", "ci_gating"],
        "fresh_degraded_surfaces": [],
        "fallback_surfaces": ["ci_core", "ci_gating"],
        "degraded_since": {"ci_core": since, "ci_gating": since},
        "fallback_since": {"ci_core": since, "ci_gating": since},
        "fallback_max_age_hours": 36,
        "restored_paths": {
            surface: sorted(entries) for surface, entries in manifests.items()
        },
        "restored_manifest": manifests,
    }))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "pre-analytics schema-v2 fallback")
    baseline = _git(repo, "rev-parse", "HEAD")

    migrated = selector_module._baseline_publication_state(
        repo, baseline, state_path
    )

    assert migrated is not None
    expected = {"ci_core", "ci_analytics", "ci_gating"}
    assert migrated["surface_contract_version"] == 5
    assert set(migrated["fallback_surfaces"]) == expected
    assert migrated["fallback_since"] == {
        surface: since for surface in expected
    }
    assert set(migrated["restored_manifest"]) == expected
    assert set(migrated["restored_manifest"]["ci_analytics"]) == {
        "data/vllm/ci/analytics.json"
    }
    assert "data/vllm/ci/gating_nightlies.json" in migrated[
        "restored_manifest"
    ]["ci_gating"]


def test_contract_v5_ci_core_fallback_is_not_reinterpreted_as_pre_analytics(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries: dict[str, dict[str, int | str]] = {}
    for relative in SURFACE_SPECS["ci_core"].required_paths:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps({"path": relative}) + "\n").encode()
        path.write_bytes(payload)
        entries[relative] = _manifest_descriptor(payload)
    state_path = repo / "data/vllm/ci/publication_state.json"
    state_path.write_text(json.dumps({
        "schema_version": 2,
        "surface_contract_version": surfaces_module.SURFACE_CONTRACT_VERSION,
        "generated_at": since,
        "baseline_ref": "0" * 40,
        "mode": "fallback",
        "degraded_surfaces": ["ci_core"],
        "fresh_degraded_surfaces": [],
        "fallback_surfaces": ["ci_core"],
        "degraded_since": {"ci_core": since},
        "fallback_since": {"ci_core": since},
        "fallback_max_age_hours": 36,
        "restored_paths": {"ci_core": sorted(entries)},
        "restored_manifest": {"ci_core": entries},
    }))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "contract-v5 ci-core fallback")
    baseline = _git(repo, "rev-parse", "HEAD")

    validated = selector_module._baseline_publication_state(
        repo,
        baseline,
        state_path,
    )

    assert validated is not None
    assert validated["surface_contract_version"] == 5
    assert validated["degraded_surfaces"] == ["ci_core"]
    assert validated["fallback_surfaces"] == ["ci_core"]
    assert set(validated["restored_manifest"]) == {"ci_core"}


def test_unknown_surface_contract_is_not_reinterpreted_as_pre_analytics(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path = repo / "data/vllm/ci/publication_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "schema_version": 2,
        "surface_contract_version": 999,
        "generated_at": since,
        "baseline_ref": "0" * 40,
        "mode": "degraded",
        "degraded_surfaces": ["ci_core"],
        "fresh_degraded_surfaces": ["ci_core"],
        "fallback_surfaces": [],
        "degraded_since": {"ci_core": since},
        "fallback_since": {},
        "fallback_max_age_hours": 36,
        "restored_paths": {},
        "restored_manifest": {},
    }))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "unknown surface contract")
    baseline = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="unsupported surface contract"):
        selector_module._baseline_publication_state(
            repo,
            baseline,
            state_path,
        )


@pytest.mark.parametrize("invalid_contract", [5.0, True, "5"])
def test_non_integer_surface_contract_is_rejected(
    tmp_path: Path,
    invalid_contract: object,
) -> None:
    repo = tmp_path / "repo"
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path = repo / "data/vllm/ci/publication_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "schema_version": 2,
        "surface_contract_version": invalid_contract,
        "generated_at": since,
        "baseline_ref": "0" * 40,
        "mode": "current",
        "degraded_surfaces": [],
        "fresh_degraded_surfaces": [],
        "fallback_surfaces": [],
        "degraded_since": {},
        "fallback_since": {},
        "fallback_max_age_hours": 36,
        "restored_paths": {},
        "restored_manifest": {},
    }))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "invalid surface contract")
    baseline = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="invalid surface contract"):
        selector_module._baseline_publication_state(repo, baseline, state_path)


def test_v4_queue_fallback_is_partitioned_before_live_only_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path, baseline_bytes = _write_v4_queue_fallback_baseline(repo, since)
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "contract-v4 queue fallback")
    baseline = _git(repo, "rev-parse", "HEAD")

    candidate_live: dict[str, bytes] = {}
    for relative in SURFACE_SPECS["queue"].required_paths:
        payload = (
            json.dumps({"selection": "live-candidate", "path": relative}) + "\n"
        ).encode()
        (repo / relative).write_bytes(payload)
        candidate_live[relative] = payload

    class CleanAudit:
        def __init__(self, *args, **kwargs):
            self.report = SimpleNamespace(errors=[], degradations=[])

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self) -> SimpleNamespace:
            return SimpleNamespace(errors=[], degradations=[])

    monkeypatch.setattr(selector_module, "DashboardAudit", CleanAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    state = selector_module.select_publication(
        repo,
        baseline,
        state_path,
        refresh_only_surface="queue",
    )

    companions = {"queue_capacity", "queue_omni", "queue_workload"}
    assert state["surface_contract_version"] == 5
    assert set(state["degraded_surfaces"]) == companions
    assert state["fresh_degraded_surfaces"] == []
    assert set(state["fallback_surfaces"]) == companions
    assert state["degraded_since"] == {
        surface: since for surface in companions
    }
    assert state["fallback_since"] == {
        surface: since for surface in companions
    }
    assert set(state["restored_manifest"]) == companions
    assert set(state["restored_paths"]) == companions
    assert "queue" not in state["restored_manifest"]

    for relative, payload in candidate_live.items():
        assert (repo / relative).read_bytes() == payload
    for surface in companions:
        expected = {
            relative
            for relative in baseline_bytes
            if relative in {
                *SURFACE_SPECS[surface].required_paths,
                *SURFACE_SPECS[surface].optional_paths,
            }
        }
        assert set(state["restored_manifest"][surface]) == expected
        assert set(state["restored_paths"][surface]) == expected
        for relative in expected:
            assert (repo / relative).read_bytes() == baseline_bytes[relative]


def test_v5_stale_companions_cannot_roll_back_live_queue_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path, baseline_bytes = _write_v4_queue_fallback_baseline(repo, since)
    state_path.write_text(json.dumps({
        "schema_version": 2,
        "surface_contract_version": surfaces_module.SURFACE_CONTRACT_VERSION,
        "generated_at": since,
        "baseline_ref": "0" * 40,
        "mode": "current",
        "degraded_surfaces": [],
        "fresh_degraded_surfaces": [],
        "fallback_surfaces": [],
        "degraded_since": {},
        "fallback_since": {},
        "fallback_max_age_hours": 36,
        "restored_paths": {},
        "restored_manifest": {},
    }))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "contract-v5 current queue surfaces")
    baseline = _git(repo, "rev-parse", "HEAD")

    candidate_live: dict[str, bytes] = {}
    for relative in SURFACE_SPECS["queue"].required_paths:
        payload = (
            json.dumps({"selection": "live-candidate", "path": relative}) + "\n"
        ).encode()
        (repo / relative).write_bytes(payload)
        candidate_live[relative] = payload

    stale_findings = [
        Finding(
            "error",
            "operations-stale-source",
            f"{source} is stale",
            "data/vllm/ci/operations_v2.json",
            {"source": source},
        )
        for source in (
            "capacity_monitor",
            "omni_heuristic",
            "workload_mapping",
        )
    ]

    class CompanionStaleAudit:
        def __init__(self, *args, **kwargs):
            self.allow_fallback = kwargs.get("allow_publication_fallback") is True
            self.report = SimpleNamespace(errors=[], degradations=[])

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self) -> SimpleNamespace:
            return SimpleNamespace(
                errors=[] if self.allow_fallback else stale_findings,
                degradations=[],
            )

    monkeypatch.setattr(selector_module, "DashboardAudit", CompanionStaleAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    state = selector_module.select_publication(
        repo,
        baseline,
        state_path,
        refresh_only_surface="queue",
    )

    companions = {"queue_capacity", "queue_omni", "queue_workload"}
    assert set(state["fallback_surfaces"]) == companions
    assert "queue" not in state["degraded_surfaces"]
    assert set(state["restored_manifest"]) == companions
    for relative, payload in candidate_live.items():
        assert (repo / relative).read_bytes() == payload
    for surface in companions:
        for relative in (
            *SURFACE_SPECS[surface].required_paths,
            *SURFACE_SPECS[surface].optional_paths,
        ):
            assert (repo / relative).read_bytes() == baseline_bytes[relative]


def test_v4_queue_fallback_migration_rejects_unverified_monolith(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path, _ = _write_v4_queue_fallback_baseline(repo, since)
    state = json.loads(state_path.read_text())
    state["restored_manifest"]["queue"][
        "data/vllm/ci/workload_mapping.json"
    ]["sha256"] = "0" * 64
    state_path.write_text(json.dumps(state))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "tampered contract-v4 queue fallback")
    baseline = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="does not match its manifest"):
        selector_module._baseline_publication_state(
            repo,
            baseline,
            state_path,
        )


def test_v4_queue_fallback_migration_rejects_missing_manifest_path(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path, _ = _write_v4_queue_fallback_baseline(repo, since)
    missing = "data/vllm/ci/workload_mapping.json"
    state = json.loads(state_path.read_text())
    del state["restored_manifest"]["queue"][missing]
    state["restored_paths"]["queue"].remove(missing)
    state_path.write_text(json.dumps(state))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "incomplete contract-v4 queue fallback")
    baseline = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="manifest path set.*inconsistent"):
        selector_module._baseline_publication_state(
            repo,
            baseline,
            state_path,
        )


def test_v4_fresh_degraded_queue_expands_clocks_without_restore_proof(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path, _ = _write_v4_queue_fallback_baseline(repo, since)
    state = json.loads(state_path.read_text())
    state.update({
        "mode": "degraded",
        "degraded_surfaces": ["queue"],
        "fresh_degraded_surfaces": ["queue"],
        "fallback_surfaces": [],
        "degraded_since": {"queue": since},
        "fallback_since": {},
        "restored_paths": {},
        "restored_manifest": {},
    })
    state_path.write_text(json.dumps(state))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fresh-degraded contract-v4 queue")
    baseline = _git(repo, "rev-parse", "HEAD")

    migrated = selector_module._baseline_publication_state(
        repo,
        baseline,
        state_path,
    )

    assert migrated is not None
    assert migrated["surface_contract_version"] == 5
    assert migrated["mode"] == "degraded"
    assert set(migrated["degraded_surfaces"]) == selector_module.QUEUE_SPLIT_SURFACES
    assert set(migrated["fresh_degraded_surfaces"]) == (
        selector_module.QUEUE_SPLIT_SURFACES
    )
    assert migrated["fallback_surfaces"] == []
    assert migrated["degraded_since"] == {
        surface: since for surface in selector_module.QUEUE_SPLIT_SURFACES
    }
    assert migrated["fallback_since"] == {}
    assert migrated["restored_paths"] == {}
    assert migrated["restored_manifest"] == {}


def test_v4_mixed_queue_fallback_preserves_independent_non_queue_clock(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    queue_since = "2026-08-31T01:02:03Z"
    dns_since = "2026-08-31T04:05:06Z"
    state_path, _ = _write_v4_queue_fallback_baseline(repo, queue_since)
    state = json.loads(state_path.read_text())
    state.update({
        "mode": "mixed",
        "degraded_surfaces": ["dns_health", "queue"],
        "fresh_degraded_surfaces": ["dns_health"],
        "fallback_surfaces": ["queue"],
        "degraded_since": {
            "dns_health": dns_since,
            "queue": queue_since,
        },
        "fallback_since": {"queue": queue_since},
    })
    state_path.write_text(json.dumps(state))
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "mixed contract-v4 queue fallback")
    baseline = _git(repo, "rev-parse", "HEAD")

    migrated = selector_module._baseline_publication_state(
        repo,
        baseline,
        state_path,
    )

    assert migrated is not None
    companions = selector_module.QUEUE_SPLIT_SURFACES
    assert migrated["surface_contract_version"] == 5
    assert migrated["mode"] == "mixed"
    assert migrated["fresh_degraded_surfaces"] == ["dns_health"]
    assert set(migrated["fallback_surfaces"]) == companions
    assert set(migrated["degraded_surfaces"]) == {"dns_health", *companions}
    assert migrated["degraded_since"] == {
        "dns_health": dns_since,
        **{surface: queue_since for surface in companions},
    }
    assert migrated["fallback_since"] == {
        surface: queue_since for surface in companions
    }
    assert set(migrated["restored_paths"]) == companions
    assert set(migrated["restored_manifest"]) == companions


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
        "surface_contract_version": 5,
        "generated_at": state["generated_at"],
        "baseline_ref": baseline,
        "mode": "current",
        "degraded_surfaces": [],
        "fresh_degraded_surfaces": [],
        "fallback_surfaces": [],
        "degraded_since": {},
        "fallback_since": {},
        "fallback_max_age_hours": 36,
        "collector_failures": [],
        "collector_failure_streaks": {},
        "collector_incident_policy": {
            "alert": True,
            "reason": "non-collector-publication-finding",
            "transient_persistence_runs_required": 2,
            "max_observed_streak": 0,
        },
        "incident_policy": {
            "alert": True,
            "reason": "non-collector-publication-finding",
            "transient_persistence_runs_required": 2,
            "max_observed_streak": 0,
        },
        "upstream_retry_observations": [],
        "upstream_retry_streaks": {},
        "candidate_errors": [],
        "candidate_degradations": [],
        "final_errors": [],
        "final_degradations": [],
        "restored_paths": {},
        "restored_manifest": {},
    }


@pytest.mark.parametrize("stale", [False, True], ids=("fresh", "stale"))
def test_dns_partial_warning_stays_local_unless_the_payload_is_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stale: bool,
) -> None:
    repo = tmp_path / "repo"
    source = repo / "data/vllm/ci/dns_failures.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"coverage":{"status":"partial"}}\n')
    _git(repo, "init")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "validated DNS baseline")
    baseline = _git(repo, "rev-parse", "HEAD")

    partial_warning = Finding(
        "warning",
        "dns-health-partial",
        "DNS health coverage is partial",
        "data/vllm/ci/dns_failures.json",
    )
    stale_degradation = Finding(
        "degradation",
        "dns-health-stale",
        "DNS health source is stale",
        "data/vllm/ci/dns_failures.json",
    )

    class DnsAudit:
        def __init__(self, *args, **kwargs):
            self.report = SimpleNamespace(errors=[], degradations=[], warnings=[])

        def audit_publication_surface_files(self) -> None:
            return None

        def run(self) -> SimpleNamespace:
            return SimpleNamespace(
                errors=[],
                degradations=[stale_degradation] if stale else [],
                warnings=[partial_warning],
            )

    monkeypatch.setattr(
        selector_module,
        "SURFACE_SPECS",
        {
            "dns_health": SurfaceSpec(
                required_paths=("data/vllm/ci/dns_failures.json",)
            )
        },
    )
    monkeypatch.setattr(selector_module, "DashboardAudit", DnsAudit)
    monkeypatch.setattr(selector_module, "_rebuild_operations", lambda root: None)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    state = selector_module.select_publication(
        repo,
        baseline,
        repo / "publication-state.json",
    )

    assert state["mode"] == ("degraded" if stale else "current")
    expected_surfaces = ["dns_health"] if stale else []
    assert state["degraded_surfaces"] == expected_surfaces
    assert state["fresh_degraded_surfaces"] == expected_surfaces
    assert state["fallback_surfaces"] == []
    assert state["candidate_degradations"] == (
        [
            {
                "severity": "degradation",
                "code": "dns-health-stale",
                "message": "DNS health source is stale",
                "path": "data/vllm/ci/dns_failures.json",
                "surfaces": ["dns_health"],
            }
        ]
        if stale
        else []
    )


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
