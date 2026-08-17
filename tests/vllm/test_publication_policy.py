"""Fail-closed contracts for the GitHub Pages publication boundary."""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "config" / "public_data_manifest.json"
BUILD_SCRIPT = ROOT / "scripts" / "build_site.py"
README_PATH = ROOT / "README.md"
README_RENDERER = ROOT / "scripts" / "render.py"
VLLM_SCRIPTS_README = ROOT / "scripts" / "vllm" / "README.md"
DASHBOARD_AUDIT = ROOT / "dashboards" / "dashboard-audit.md"
GITIGNORE = ROOT / ".gitignore"


def _load_build_site_module():
    spec = importlib.util.spec_from_file_location("dashboard_build_site", BUILD_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD_SITE = _load_build_site_module()


def test_operational_documentation_matches_the_canonical_publication_path() -> None:
    readme = README_PATH.read_text()
    renderer = README_RENDERER.read_text()
    scripts_readme = VLLM_SCRIPTS_README.read_text()
    audit = DASHBOARD_AUDIT.read_text()

    for text in (readme, renderer):
        assert "deployed automatically on every push to main" not in text
        assert "scripts/vllm/collect_gating_targets.py" in text
        assert "scripts/vllm/build_operations_snapshot.py" in text
        assert "scripts/vllm/collect_ownership_parity.py" in text
        assert "scripts/vllm/ci_area_regression_watcher.py" in text
        assert "Ready Tickets → CI ownership" in text
        assert "CI_OWNER_AVAILABILITY_JSON" not in text
        assert "Europe/Belgrade" in text
        assert "America/Chicago" in text
        assert "Assignment uses only these committed regional schedules" in text
        assert "a schedule cannot be" in text
        assert "evaluated safely" in text
        assert "PROJECTS_WRITE_TOKEN" in text
        assert "`gating_targets.json` is regenerated" in text
        assert "`operations_v2.json` is a private build input" in text

    assert "CI_OWNER_AVAILABILITY_JSON" not in scripts_readme
    assert "regional working-hour profiles" in scripts_readme
    assert "Hourly via `hourly-master.yml`" in scripts_readme
    assert "operations_v2_manifest.json + operations_v2/*.json" in scripts_readme
    assert "four atomic publication surfaces" in scripts_readme
    assert "`ci-collect.yml` workflow is validation-only" in scripts_readme
    assert "exact active-job ledger counts remain separate" in audit
    assert "hard failures, soft failures, and" in audit


def _write(path: Path, text: str = "{}\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _operation_generated_files() -> list[str]:
    return [
        "vllm/ci/operations_v2_manifest.json",
        "vllm/ci/queue_history_chart.json",
    ] + [
        f"vllm/ci/operations_v2/{name}.json"
        for name in (
            "amd_agent_health",
            "amd_test_health",
            "definition_parity",
            "diagnostics",
            "gating",
            "nightly",
            "omni",
            "ownership",
            "queue",
            "reliability",
            "trajectory",
        )
    ]


PUBLICATION_STATE_INPUT = "vllm/ci/publication_state.json"
PUBLICATION_STATUS_OUTPUT = "vllm/ci/publication_status.json"
ANALYTICS_PATH = "vllm/ci/analytics.json"
ANALYTICS_PROJECTOR = "public_analytics_v1"
ANALYTICS_MAX_BYTES = 8 * 1024 * 1024

PRIVATE_ANALYTICS = {
    "amd-ci": {
        "pipeline": "amd-ci",
        "display_name": "AMD CI",
        "days": 30,
        "generated_at": "2026-01-01T00:00:00Z",
        "summary": {"total_builds": 1, "passed": 1, "failed": 0},
        "builds": [{
            "number": 123,
            "state": "passed",
            "created_at": "2026-01-01T00:00:00Z",
            "date": "2026-01-01",
            "passed": 1,
            "failed": 0,
            "soft_failed": 0,
            "total_jobs": 1,
            "author": "private collector identity",
            "jobs": [{
                "name": "Example tests",
                "state": "passed",
                "q": "amd_mi300_1",
                "wait": 1.5,
                "job_id": "private-attempt-id",
            }],
        }],
        "default_window": "30d",
        "windows": {},
        "all_main_reliability": {"private": "full reliability evidence"},
        "main_builds": [{"private": "compatibility reliability rows"}],
        "main_retry_analysis": {"private": "retry evidence"},
    },
    "ci": {
        "pipeline": "ci",
        "display_name": "Upstream CI",
        "days": 30,
        "generated_at": "2026-01-01T00:00:00Z",
        "summary": {"total_builds": 0, "passed": 0, "failed": 0},
        "builds": [],
        "default_window": "30d",
        "windows": {},
        "all_main_reliability": {"private": "upstream evidence"},
    },
}
PRIVATE_ANALYTICS_TEXT = json.dumps(PRIVATE_ANALYTICS, indent=2) + "\n"


def _fixture_manifest() -> dict:
    return {
        "schema_version": 2,
        "policy": "test fixture",
        "required_files": [
            "public.json",
        ],
        "optional_files": ["optional.json"],
        "build_inputs": [
            ANALYTICS_PATH,
            "vllm/ci/operations_v2.json",
            PUBLICATION_STATE_INPUT,
        ],
        "projected_files": [{
            "path": ANALYTICS_PATH,
            "projector": ANALYTICS_PROJECTOR,
            "max_bytes": ANALYTICS_MAX_BYTES,
        }],
        "optional_globs": ["vllm/ci/test_builds/*/comparison.json"],
        "generated_files": _operation_generated_files() + [
            PUBLICATION_STATUS_OUTPUT,
        ],
        "never_publish_patterns": [
            "*/.cache/*",
            "vllm/ci/agent_health/*",
            "vllm/ci/test_results/*",
            "vllm/ci/test_builds/*/results.jsonl",
            "vllm/perf_eval/events.jsonl",
            "vllm/ci/open_*_issues.json",
            "vllm/ci/*_state.json",
            "vllm/ci/retired_*.json",
            "vllm/ci/operations_v2.json",
        ],
    }


def _assemble_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    docs = tmp_path / "docs"
    data = tmp_path / "data"
    output = tmp_path / "_site"
    manifest_path = tmp_path / "public_data_manifest.json"
    _write(docs / "index.html", "<html>fixture</html>\n")
    _write(data / "public.json", '{"public": true}\n')
    _write(data / "optional.json", '{"optional": true}\n')
    _write(data / ANALYTICS_PATH, PRIVATE_ANALYTICS_TEXT)
    _write(
        data / "vllm/ci/operations_v2.json",
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": "2026-01-01T00:00:00Z",
            }
        ),
    )
    _write(
        data / PUBLICATION_STATE_INPUT,
        json.dumps(
            {
                "mode": "fallback",
                "generated_at": "2026-01-01T01:00:00Z",
                "degraded_since": {"ci": "2026-01-01T00:00:00Z"},
                "degraded_surfaces": ["ci"],
                "fallback_surfaces": ["ci"],
                "candidate_errors": [
                    {
                        "path": "data/private.json",
                        "message": "private diagnostic",
                    }
                ],
                "baseline_ref": "private-ref",
                "restored_manifest": {"ci": {"private/path": {"sha256": "secret"}}},
            }
        ),
    )
    _write(data / "vllm/ci/test_builds/example/comparison.json")

    # These represent each sensitive or retired data class that collectors
    # keep locally but the static site must never expose.
    for relative in (
        "vllm/ci/.cache/builds_amd.json",
        "vllm/ci/test_results/2026-01-01_amd.jsonl",
        "vllm/ci/agent_health/node_days.jsonl",
        "vllm/ci/test_builds/example/results.jsonl",
        "vllm/ci/open_queue_issues.json",
        "vllm/ci/ready_tickets_state.json",
        "vllm/ci/retired_legacy.json",
        "vllm/perf_eval/events.jsonl",
    ):
        _write(data / relative, '{"private": true}\n')

    manifest_path.write_text(json.dumps(_fixture_manifest()))
    monkeypatch.setattr(BUILD_SITE, "DOCS", docs)
    monkeypatch.setattr(BUILD_SITE, "DATA", data)
    monkeypatch.setattr(BUILD_SITE, "PUBLIC_DATA_MANIFEST", manifest_path)
    BUILD_SITE.build_site(output, cache_bust=False)
    return output, data


def test_site_assembly_copies_allowlist_and_generated_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, _ = _assemble_fixture(tmp_path, monkeypatch)

    assert (output / "index.html").read_text() == "<html>fixture</html>\n"
    assert (output / ".nojekyll").exists()
    assert (output / "data/public.json").exists()
    assert (output / "data/optional.json").exists()
    assert (output / "data/vllm/ci/test_builds/example/comparison.json").exists()
    projected_path = output / "data" / ANALYTICS_PATH
    projected_text = projected_path.read_text()
    projected_payload = json.loads(projected_text)
    assert projected_payload == BUILD_SITE.project_public_analytics(PRIVATE_ANALYTICS)
    assert projected_text == BUILD_SITE.compact_public_analytics_json(
        PRIVATE_ANALYTICS
    )
    assert projected_text.endswith("\n")
    assert projected_text.count("\n") == 1
    assert "all_main_reliability" not in projected_text
    assert "main_retry_analysis" not in projected_text
    assert "private-attempt-id" not in projected_text
    assert (output / "data/vllm/ci/operations_v2_manifest.json").exists()
    assert not (output / "data/vllm/ci/operations_v2.json").exists()
    assert not (output / "data" / PUBLICATION_STATE_INPUT).exists()
    public_status = json.loads(
        (output / "data" / PUBLICATION_STATUS_OUTPUT).read_text()
    )
    assert public_status == {
        "schema_version": 1,
        "status": "degraded",
        "mode": "fallback",
        "generated_at": "2026-01-01T01:00:00Z",
        "degraded_since": "2026-01-01T00:00:00Z",
        "uses_fallback": True,
        "publication_blocked": False,
        "affected_surfaces": ["CI health"],
        "affected_surface_count": 1,
        "fallback_surface_count": 1,
        "fresh_degraded_surface_count": 0,
    }
    assert json.loads(
        (output / "data/vllm/ci/operations_v2_manifest.json").read_text()
    )["monolith"] is None
    for relative in _operation_generated_files():
        assert (output / "data" / relative).exists()


def test_site_assembly_does_not_modify_or_directly_copy_private_analytics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, data = _assemble_fixture(tmp_path, monkeypatch)

    source = data / ANALYTICS_PATH
    projected = output / "data" / ANALYTICS_PATH
    assert source.read_text() == PRIVATE_ANALYTICS_TEXT
    assert projected.read_bytes() != source.read_bytes()
    assert "private collector identity" in source.read_text()
    assert "private collector identity" not in projected.read_text()


def test_site_assembly_excludes_private_raw_state_and_retired_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, data = _assemble_fixture(tmp_path, monkeypatch)

    private_sources = {
        path.relative_to(data).as_posix()
        for path in data.rglob("*")
        if path.is_file() and json.loads(path.read_text()).get("private")
    }
    assert private_sources
    for relative in private_sources:
        assert not (output / "data" / relative).exists(), relative
    assert not (output / "data" / PUBLICATION_STATE_INPUT).exists()


@pytest.mark.parametrize(
    ("mode", "expected_status", "uses_fallback", "blocked"),
    [
        ("current", "healthy", False, False),
        ("degraded", "degraded", False, False),
        ("fallback", "degraded", True, False),
        ("mixed", "degraded", True, False),
        ("blocked", "blocked", False, True),
    ],
)
def test_publication_status_projection_supports_every_public_mode(
    mode: str,
    expected_status: str,
    uses_fallback: bool,
    blocked: bool,
) -> None:
    payload = BUILD_SITE.project_publication_status({
        "mode": mode,
        "generated_at": "2026-01-01T00:00:00Z",
    })

    assert payload["status"] == expected_status
    assert payload["uses_fallback"] is uses_fallback
    assert payload["publication_blocked"] is blocked


def test_publication_status_projection_never_exposes_private_diagnostics() -> None:
    payload = BUILD_SITE.project_publication_status({
        "mode": "mixed",
        "generated_at": "2026-01-01T01:00:00Z",
        "degraded_since": {
            "ci": "2026-01-01T00:00:00Z",
            "private/path": "leaked timestamp",
        },
        "degraded_surfaces": ["ci", "queue", "private/path"],
        "fresh_degraded_surfaces": ["queue"],
        "fallback_surfaces": ["ci"],
        "candidate_errors": [{
            "message": "do not publish this secret",
            "path": "data/private.json",
        }],
        "final_errors": [{"message": "another secret"}],
        "baseline_ref": "private-git-ref",
        "restored_manifest": {
            "ci": {"data/private.json": {"sha256": "private-hash"}},
        },
    })

    assert set(payload) == {
        "schema_version",
        "status",
        "mode",
        "generated_at",
        "degraded_since",
        "uses_fallback",
        "publication_blocked",
        "affected_surfaces",
        "affected_surface_count",
        "fallback_surface_count",
        "fresh_degraded_surface_count",
    }
    assert payload["affected_surfaces"] == ["CI health", "Queue health"]
    assert payload["affected_surface_count"] == 2
    assert payload["fallback_surface_count"] == 1
    assert payload["fresh_degraded_surface_count"] == 1
    serialized = json.dumps(payload)
    for private_value in (
        "do not publish this secret",
        "data/private.json",
        "private-git-ref",
        "private-hash",
        "private/path",
    ):
        assert private_value not in serialized


def test_publication_status_projection_rejects_an_unknown_mode() -> None:
    with pytest.raises(ValueError, match="Unsupported publication mode"):
        BUILD_SITE.project_publication_status({"mode": "secret/path"})


def test_publication_status_projection_canonicalizes_safe_timestamps() -> None:
    payload = BUILD_SITE.project_publication_status({
        "mode": "degraded",
        "generated_at": "2026-01-01T01:00:00+01:00",
        "degraded_since": {
            "ci": "not-a-timestamp/data/private.json",
        },
        "degraded_surfaces": ["ci"],
    })

    assert payload["generated_at"] == "2026-01-01T00:00:00Z"
    assert payload["degraded_since"] is None


def test_publication_status_projection_labels_split_and_legacy_ci_surfaces() -> None:
    payload = BUILD_SITE.project_publication_status({
        "mode": "degraded",
        "generated_at": "2026-01-01T00:00:00Z",
        "degraded_surfaces": [
            "ci",
            "ci_core",
            "ci_gating",
            "ci_changes",
            "ci_hotness",
        ],
    })

    assert payload["affected_surfaces"] == [
        "CI core health",
        "CI gating",
        "CI health",
        "CI test changes",
        "CI workload hotness",
    ]


def test_docs_cannot_smuggle_an_unlisted_data_file_into_site(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = tmp_path / "docs"
    data = tmp_path / "data"
    manifest_path = tmp_path / "public_data_manifest.json"
    _write(docs / "index.html", "<html></html>")
    _write(docs / "data/unlisted.json")
    _write(data / "public.json")
    _write(data / ANALYTICS_PATH, PRIVATE_ANALYTICS_TEXT)
    _write(
        data / "vllm/ci/operations_v2.json",
        '{"schema_version":2,"generated_at":"2026-01-01T00:00:00Z"}',
    )
    _write(
        data / PUBLICATION_STATE_INPUT,
        '{"mode":"current","generated_at":"2026-01-01T00:00:00Z"}',
    )
    manifest_path.write_text(json.dumps(_fixture_manifest()))
    monkeypatch.setattr(BUILD_SITE, "DOCS", docs)
    monkeypatch.setattr(BUILD_SITE, "DATA", data)
    monkeypatch.setattr(BUILD_SITE, "PUBLIC_DATA_MANIFEST", manifest_path)

    with pytest.raises(RuntimeError, match="non-public data files.*unlisted.json"):
        BUILD_SITE.build_site(tmp_path / "_site", cache_bust=False)


def test_missing_required_public_file_fails_closed(tmp_path: Path) -> None:
    manifest = _fixture_manifest()
    with pytest.raises(FileNotFoundError, match="Required public data files"):
        BUILD_SITE.copy_public_data(tmp_path / "data", tmp_path / "site", manifest)


def _load_fixture_manifest(tmp_path: Path, payload: dict) -> dict:
    path = tmp_path / "public_data_manifest.json"
    path.write_text(json.dumps(payload))
    return BUILD_SITE.load_public_data_manifest(path)


def test_projected_file_manifest_contract_is_normalized(tmp_path: Path) -> None:
    manifest = _load_fixture_manifest(tmp_path, _fixture_manifest())

    assert manifest["schema_version"] == 2
    assert manifest["projected_files"] == [{
        "path": ANALYTICS_PATH,
        "projector": ANALYTICS_PROJECTOR,
        "max_bytes": ANALYTICS_MAX_BYTES,
    }]
    assert ANALYTICS_PATH in manifest["build_inputs"]
    assert ANALYTICS_PATH not in manifest["required_files"]
    assert ANALYTICS_PATH not in manifest["generated_files"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest: manifest.update(schema_version=1),
            "Unsupported public data manifest schema",
        ),
        (
            lambda manifest: manifest.update(projected_files={}),
            "projected_files must be a list",
        ),
        (
            lambda manifest: manifest.update(projected_files=["analytics.json"]),
            r"projected_files\[0\] must be an object",
        ),
        (
            lambda manifest: manifest["projected_files"][0].update(extra=True),
            "must contain exactly",
        ),
        (
            lambda manifest: manifest["projected_files"][0].update(
                path="../analytics.json"
            ),
            "must stay below data",
        ),
        (
            lambda manifest: manifest["projected_files"][0].update(
                projector="unknown"
            ),
            "unknown projector",
        ),
        (
            lambda manifest: manifest["projected_files"][0].update(max_bytes=0),
            "max_bytes must be a positive integer",
        ),
        (
            lambda manifest: manifest["projected_files"][0].update(max_bytes=True),
            "max_bytes must be a positive integer",
        ),
        (
            lambda manifest: manifest["projected_files"].append(
                dict(manifest["projected_files"][0])
            ),
            "duplicate paths",
        ),
        (
            lambda manifest: manifest["build_inputs"].remove(ANALYTICS_PATH),
            "must be declared as build inputs",
        ),
        (
            lambda manifest: manifest["required_files"].append(ANALYTICS_PATH),
            "cannot also be public outputs",
        ),
        (
            lambda manifest: manifest["optional_globs"].append(
                "vllm/ci/*.json"
            ),
            "cannot also match direct public globs",
        ),
        (
            lambda manifest: manifest["never_publish_patterns"].append(
                ANALYTICS_PATH
            ),
            "allowlists blocked paths",
        ),
    ],
)
def test_malformed_projected_file_manifest_fails_closed(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    manifest = _fixture_manifest()
    mutate(manifest)

    with pytest.raises(ValueError, match=message):
        _load_fixture_manifest(tmp_path, manifest)


def test_projected_file_size_limit_fails_closed(tmp_path: Path) -> None:
    manifest_payload = _fixture_manifest()
    manifest_payload["projected_files"][0]["max_bytes"] = 1
    manifest = _load_fixture_manifest(tmp_path, manifest_payload)
    source_data = tmp_path / "data"
    site_data = tmp_path / "site"
    _write(source_data / ANALYTICS_PATH, PRIVATE_ANALYTICS_TEXT)

    with pytest.raises(RuntimeError, match="Projected public file.*limit is 1"):
        BUILD_SITE.materialize_projected_files(source_data, site_data, manifest)

    assert not (site_data / ANALYTICS_PATH).exists()


def test_validation_requires_declared_projected_outputs(tmp_path: Path) -> None:
    manifest = _load_fixture_manifest(tmp_path, _fixture_manifest())

    with pytest.raises(RuntimeError, match="materialize.*analytics.json"):
        BUILD_SITE.validate_public_data(tmp_path / "site", set(), manifest)


def test_production_manifest_matches_active_assets_and_operation_sections() -> None:
    manifest = BUILD_SITE.load_public_data_manifest(MANIFEST_PATH)
    required = set(manifest["required_files"])
    projected = {descriptor["path"] for descriptor in manifest["projected_files"]}
    allowed_exact = required | set(manifest["optional_files"]) | projected

    assert {
        "site/projects.json",
        "users.json",
        "vllm/ci/amd_test_matrix.json",
        "vllm/ci/dns_failures.json",
        "vllm/ci/gating_targets.json",
        "vllm/ci/omni_surge_heuristic.json",
        "vllm/ci/queue_lifecycle.json",
        "vllm/ci/queue_timeseries.jsonl",
        "vllm/ci/ready_tickets.json",
        "vllm/ci/workload_mapping.json",
        "vllm/perf_eval/perf_eval.json",
        "vllm/prs.json",
    } <= allowed_exact
    assert manifest["build_inputs"] == [
        ANALYTICS_PATH,
        "vllm/ci/operations_v2.json",
        PUBLICATION_STATE_INPUT,
    ]
    assert manifest["projected_files"] == [{
        "path": ANALYTICS_PATH,
        "projector": ANALYTICS_PROJECTOR,
        "max_bytes": ANALYTICS_MAX_BYTES,
    }]
    assert ANALYTICS_PATH not in required
    assert ANALYTICS_PATH not in manifest["generated_files"]
    assert PUBLICATION_STATUS_OUTPUT in manifest["generated_files"]

    forbidden = {
        "vllm/ci/.cache/builds_amd.json",
        "vllm/ci/agent_health/node_days.jsonl",
        "vllm/ci/dns_health/scan_state.json.gz",
        "vllm/ci/dns_health/scan_state.fernet",
        "vllm/ci/open_queue_issues.json",
        "vllm/ci/ready_tickets_state.json",
        "vllm/ci/operations_v2.json",
        PUBLICATION_STATE_INPUT,
        "vllm/ci/test_results/2026-07-27_amd.jsonl",
        "vllm/perf_eval/events.jsonl",
        "vllm/ci/queue_lifecycle_jobs/2026-08-11.jsonl.gz",
    }
    for relative in forbidden:
        assert relative not in allowed_exact
        assert not any(
            fnmatch.fnmatchcase(relative, pattern)
            for pattern in manifest["optional_globs"]
        )
    assert any(
        fnmatch.fnmatchcase("vllm/ci/dns_health/scan_state.json.gz", pattern)
        for pattern in manifest["never_publish_patterns"]
    )
    assert any(
        fnmatch.fnmatchcase("vllm/ci/dns_health/scan_state.fernet", pattern)
        for pattern in manifest["never_publish_patterns"]
    )
    assert "data/vllm/ci/dns_health/" in GITIGNORE.read_text().splitlines()


@pytest.mark.live_data
def test_production_manifest_matches_current_operation_sections() -> None:
    manifest = BUILD_SITE.load_public_data_manifest(MANIFEST_PATH)
    required = set(manifest["required_files"])
    projected = {descriptor["path"] for descriptor in manifest["projected_files"]}
    allowed_exact = required | set(manifest["optional_files"]) | projected

    assert all(
        (ROOT / "data" / relative).is_file()
        for relative in manifest["build_inputs"]
    )
    assert all((ROOT / "data" / relative).is_file() for relative in required)

    operation_manifest = json.loads(
        (ROOT / "data/vllm/ci/operations_v2_manifest.json").read_text()
    )
    operation_sections = {
        f"vllm/ci/{descriptor['path']}"
        for descriptor in operation_manifest["sections"].values()
    }
    operation_outputs = operation_sections | {
        "vllm/ci/operations_v2_manifest.json",
        "vllm/ci/queue_history_chart.json",
    }
    assert operation_outputs == (
        set(manifest["generated_files"]) - {PUBLICATION_STATUS_OUTPUT}
    )
    published_diagnostic_sources = {
        f"vllm/ci/{record['path']}"
        for record in operation_manifest["shell"]["sources"].values()
        if record.get("published") is not False
    }
    private_diagnostic_sources = {
        f"vllm/ci/{record['path']}"
        for record in operation_manifest["shell"]["sources"].values()
        if record.get("published") is False
    }
    mutation_checkpoints = {"vllm/ci/open_omni_surge_issues.json"}
    assert published_diagnostic_sources <= allowed_exact
    assert private_diagnostic_sources.isdisjoint(allowed_exact)
    assert mutation_checkpoints <= private_diagnostic_sources


@pytest.mark.live_data
def test_production_site_materializes_bounded_public_analytics(tmp_path: Path) -> None:
    manifest = BUILD_SITE.load_public_data_manifest(MANIFEST_PATH)
    site_data = tmp_path / "site-data"

    projected = BUILD_SITE.materialize_projected_files(
        ROOT / "data",
        site_data,
        manifest,
    )

    assert projected == {ANALYTICS_PATH}
    output = site_data / ANALYTICS_PATH
    descriptor = manifest["projected_files"][0]
    assert output.is_file()
    assert output.stat().st_size <= descriptor["max_bytes"]
    assert set(json.loads(output.read_text())) == {"amd-ci", "ci"}
    assert "all_main_reliability" not in output.read_text()


@pytest.mark.live_data
def test_every_committed_frontend_data_asset_is_publicly_allowlisted() -> None:
    manifest = BUILD_SITE.load_public_data_manifest(MANIFEST_PATH)
    allowed = (
        set(manifest["required_files"])
        | set(manifest["optional_files"])
        | set(manifest["generated_files"])
        | {descriptor["path"] for descriptor in manifest["projected_files"]}
    )
    pattern = re.compile(r"""['"`](data/[^'"`?\s)]+)""")

    for script in (ROOT / "docs/assets/js").glob("*.js"):
        for match in pattern.finditer(script.read_text()):
            url = match.group(1)
            if "${" in url or not (ROOT / url).is_file():
                continue
            relative = url.removeprefix("data/")
            assert relative in allowed or any(
                fnmatch.fnmatchcase(relative, glob)
                for glob in manifest["optional_globs"]
            ), f"{script.name} fetches data/{relative}, but it is not public"


def _pages_steps(workflow_name: str) -> list[dict]:
    payload = yaml.safe_load(
        (ROOT / ".github/workflows" / workflow_name).read_text()
    )
    steps = next(iter(payload["jobs"].values()))["steps"]
    return [
        step
        for step in steps
        if str(step.get("uses", "")).startswith("peaceiris/actions-gh-pages@")
    ]


def test_authoritative_deployments_replace_gh_pages() -> None:
    hourly = next(
        step
        for step in _pages_steps("hourly-master.yml")
        if step.get("name") == "Deploy to GitHub Pages"
    )
    manual = _pages_steps("deploy-pages.yml")[0]
    for step in (hourly, manual):
        assert step["with"]["keep_files"] is False
        assert step["with"]["force_orphan"] is True


def test_only_authoritative_workflows_publish_the_root_site() -> None:
    root_publishers: set[str] = set()
    scoped_publishers: set[str] = set()
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        for step in _pages_steps(path.name):
            if step.get("with", {}).get("destination_dir"):
                scoped_publishers.add(path.name)
            else:
                root_publishers.add(path.name)

    assert root_publishers == {"deploy-pages.yml", "hourly-master.yml"}
    assert scoped_publishers == {"pr-preview.yml"}


@pytest.mark.parametrize(
    "workflow_name",
    ["ci-collect.yml", "daily-update.yml", "queue-monitor.yml"],
)
def test_partial_collectors_never_publish_from_a_stale_checkout(
    workflow_name: str,
) -> None:
    assert not _pages_steps(workflow_name)
