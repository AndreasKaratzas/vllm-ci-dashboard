"""Composition and overflow contracts for the operational-misc state bundle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import collect_ci
from vllm import omni_surge_watcher
from vllm import select_publication_surfaces as selector
from vllm import write_last_collected_at
from vllm.bounded_json import pretty_json_bytes
from vllm.ci import reporter
from vllm.dashboard_storage_budget import load_storage_budget


ROOT = Path(__file__).resolve().parents[2]


def _catalog() -> dict:
    definitions = []
    for index in range(60):
        pipeline = "amd" if index < 20 else "upstream"
        definitions.append({
            "base": f"group-{index:03d}",
            "pipeline": pipeline,
            "label": "label-" + ("x" * 300) + str(index),
            "source_file": f".buildkite/test_areas/{index:03d}.yaml",
            "definition_id": f"definition-{index:03d}",
            "parallelism": 4,
            "optional": 10 <= index < 20,
        })
    return {
        "schema_version": 1,
        "source": {"commit_sha": "a" * 40},
        "normalization_bases": [f"group-{index:03d}" for index in range(60)],
        "pipelines": {
            "amd": [f"group-{index:03d}" for index in range(20)],
            "upstream": [f"group-{index:03d}" for index in range(20, 60)],
        },
        "definitions": definitions,
        "evidence": {
            "pipeline": "amd",
            "build_number": 123,
            "build_commit": "a" * 40,
            "build_state": "passed",
            "roster_complete": True,
            "result_file": "2026-09-01_amd.jsonl",
            "job_names": [
                f"mi300_1: job-{index:03d}-" + ("y" * 200)
                for index in range(120)
            ],
        },
    }


def test_shard_catalog_withdraws_roster_then_whole_definition_rows() -> None:
    source = _catalog()

    bounded = collect_ci.bounded_shard_base_catalog(source, max_bytes=12_000)
    retention = bounded["publication_retention"]

    assert len(pretty_json_bytes(bounded)) <= 12_000
    assert bounded["normalization_bases"] == source["normalization_bases"]
    assert bounded["pipelines"] == source["pipelines"]
    assert bounded["evidence"]["roster_complete"] is False
    assert bounded["evidence"]["result_file"] == ""
    assert bounded["evidence"]["job_names"] == []
    assert retention["evidence_job_names"] == {
        "source": 120,
        "published": 0,
        "omitted": 120,
        "complete": False,
    }
    assert retention["definitions"]["source"] == 60
    assert retention["definitions"]["published"] == len(bounded["definitions"])
    assert retention["definitions"]["omitted"] == 60 - len(
        bounded["definitions"]
    )
    retained = bounded["definitions"]
    if any(row["optional"] is True for row in retained):
        assert sum(
            row["pipeline"] == "amd" and row["optional"] is not True
            for row in retained
        ) == 10
    if any(row["pipeline"] == "upstream" for row in retained):
        assert sum(row["pipeline"] == "amd" for row in retained) == 20


def test_shard_catalog_compaction_is_permutation_invariant() -> None:
    source = _catalog()
    reversed_source = dict(source)
    reversed_source["definitions"] = list(reversed(source["definitions"]))
    reversed_evidence = dict(source["evidence"])
    reversed_evidence["job_names"] = list(reversed(source["evidence"]["job_names"]))
    reversed_source["evidence"] = reversed_evidence

    assert collect_ci.bounded_shard_base_catalog(
        source,
        max_bytes=12_000,
    ) == collect_ci.bounded_shard_base_catalog(
        reversed_source,
        max_bytes=12_000,
    )


def test_definition_control_preflight_preserves_all_lkg_files_on_overflow(
    tmp_path,
) -> None:
    paths = [
        tmp_path / "shard_bases.json",
        tmp_path / "shard_base_catalog.json",
        tmp_path / "parity_key_overrides.json",
    ]
    for path in paths:
        path.write_text('{"generation":"last-known-good"}\n')

    with pytest.raises(RuntimeError, match="composable byte budgets"):
        collect_ci.write_definition_controls(
            tmp_path,
            shard_bases=["group"],
            shard_catalog=_catalog(),
            parity_key_overrides={"x" * 500: "y" * 500},
            shard_bases_max_bytes=1_000,
            catalog_max_bytes=12_000,
            overrides_max_bytes=100,
        )

    assert [json.loads(path.read_text()) for path in paths] == [
        {"generation": "last-known-good"},
        {"generation": "last-known-good"},
        {"generation": "last-known-good"},
    ]


def test_publication_state_bounds_only_diagnostics_and_preserves_recovery_controls() -> None:
    diagnostics = [
        {"code": f"finding-{index:03d}", "message": "x" * 500}
        for index in range(40)
    ]
    state = {
        "schema_version": 2,
        "mode": "fallback",
        "fallback_surfaces": ["ci_core"],
        "fallback_since": {"ci_core": "2026-09-01T00:00:00Z"},
        "restored_paths": {"ci_core": ["data/vllm/ci/ci_health.json"]},
        "restored_manifest": {
            "ci_core": {
                "data/vllm/ci/ci_health.json": {
                    "bytes": 100,
                    "sha256": "a" * 64,
                }
            }
        },
        "collector_failures": [{"surface": "ci_core", "reason_class": "timeout"}],
        "final_errors": diagnostics,
        "candidate_errors": diagnostics,
        "final_degradations": diagnostics,
        "candidate_degradations": diagnostics,
    }

    bounded = selector.bounded_publication_state(state, max_bytes=10_000)
    retention = bounded["publication_retention"]

    assert len(pretty_json_bytes(bounded)) <= 10_000
    assert bounded["restored_manifest"] == state["restored_manifest"]
    assert bounded["restored_paths"] == state["restored_paths"]
    assert bounded["fallback_since"] == state["fallback_since"]
    assert bounded["collector_failures"] == state["collector_failures"]
    assert retention["recovery_controls_complete"] is True
    assert retention["complete_relative_to_source"] is False
    assert retention["diagnostic_findings"]["final_errors"]["published"] > 0
    assert retention["diagnostic_findings"]["candidate_degradations"]["published"] == 0


def test_publication_state_impossible_fixed_core_preserves_lkg(tmp_path, monkeypatch) -> None:
    output = tmp_path / "publication_state.json"
    output.write_text('{"generation":"last-known-good"}\n')
    monkeypatch.setattr(selector, "PUBLICATION_STATE_MAX_BYTES", 64)

    with pytest.raises(RuntimeError, match="recovery controls exceed"):
        selector._write_state(output, {"restored_manifest": {"x": "y" * 200}})

    assert json.loads(output.read_text()) == {"generation": "last-known-good"}


def test_omni_heuristic_keeps_exact_thresholds_and_largest_pool_rows() -> None:
    source = {
        "generated_at": "2026-09-01T00:00:00Z",
        "total_groups": 20_100,
        "dynamic_component": 26_130,
        "trigger": 26_130,
        "healthy": 18_291,
        "pool_distribution": {
            f"pool-{index:03d}-" + ("x" * 80): index + 1
            for index in range(200)
        },
    }

    bounded = omni_surge_watcher.bounded_heuristic_payload(
        source,
        max_bytes=3_000,
    )
    retention = bounded["publication_retention"]["pool_distribution"]

    assert len(pretty_json_bytes(bounded)) <= 3_000
    for field in ("total_groups", "dynamic_component", "trigger", "healthy"):
        assert bounded[field] == source[field]
    assert retention["source"] == 200
    assert retention["published"] == len(bounded["pool_distribution"])
    assert retention["omitted"] == 200 - retention["published"]
    assert min(bounded["pool_distribution"].values()) > retention["omitted"]


def test_quarantine_writer_compacts_rows_and_preserves_lkg_on_fixed_overflow(
    tmp_path,
    monkeypatch,
) -> None:
    rows = [
        {"test_id": f"test-{index:03d}", "reason": "x" * 250}
        for index in range(80)
    ]
    monkeypatch.setattr(reporter, "QUARANTINE_REPORT_MAX_BYTES", 4_000)

    output = reporter.write_quarantine_report(
        {
            "quarantined_count": 80,
            "allowlisted_count": 80,
            "excluded_from_failures": 160,
            "quarantine_entries": rows,
            "allowlist_entries": rows,
        },
        tmp_path,
    )
    payload = json.loads(output.read_text())

    assert output.stat().st_size <= 4_000
    assert payload["quarantined_count"] == 80
    assert payload["allowlisted_count"] == 80
    assert payload["publication_retention"]["complete_relative_to_source"] is False
    for name in ("quarantine_entries", "allowlist_entries"):
        counts = payload["publication_retention"]["collections"][name]
        assert counts["source"] == counts["published"] + counts["omitted"]

    output.write_text('{"generation":"last-known-good"}\n')
    monkeypatch.setattr(reporter, "QUARANTINE_REPORT_MAX_BYTES", 1)
    with pytest.raises(RuntimeError, match="fixed dashboard snapshot metadata"):
        reporter.write_quarantine_report({}, tmp_path)
    assert json.loads(output.read_text()) == {"generation": "last-known-good"}


def test_timestamp_writer_is_atomic_canonical_and_preserves_lkg(tmp_path) -> None:
    output = tmp_path / "last_collected_at.txt"
    output.write_text("2026-08-31T00:00:00Z\n")

    written = write_last_collected_at.write_timestamp(
        output,
        timestamp="2026-09-01T12:34:56Z",
    )
    assert written == "2026-09-01T12:34:56Z"
    assert output.read_text() == "2026-09-01T12:34:56Z\n"

    with pytest.raises(ValueError, match="canonical UTC"):
        write_last_collected_at.write_timestamp(output, timestamp="not-a-time")
    assert output.read_text() == "2026-09-01T12:34:56Z\n"

    with pytest.raises(RuntimeError, match="exceeds its byte budget"):
        write_last_collected_at.write_timestamp(
            output,
            timestamp="2026-09-01T12:34:56Z",
            max_bytes=1,
        )
    assert output.read_text() == "2026-09-01T12:34:56Z\n"


def test_current_fixed_shape_controls_fit_their_independent_caps() -> None:
    budget = load_storage_budget()
    writers = budget.writer_limits
    controls = {
        "project_test_results": ROOT / "data/vllm/test_results.json",
        "parity_key_overrides": ROOT / "data/vllm/ci/parity_key_overrides.json",
        "shard_bases": ROOT / "data/vllm/ci/shard_bases.json",
        "last_collected_at": ROOT / "data/vllm/ci/last_collected_at.txt",
    }

    for writer, path in controls.items():
        assert path.stat().st_size <= writers[writer].max_bytes


def test_workflow_uses_atomic_timestamp_writer() -> None:
    workflow = (ROOT / ".github/workflows/hourly-master.yml").read_text()

    assert "run: python scripts/vllm/write_last_collected_at.py" in workflow
    assert "date -u +%Y-%m-%dT%H:%M:%SZ > data/vllm/ci/last_collected_at.txt" not in workflow
