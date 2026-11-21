"""One unbindable AMD attempt must not discard a nightly's attested results."""

from __future__ import annotations

import json

import pytest

from tests.vllm.test_collect_ci_cache_coverage import (
    _amd_source_definition_catalog,
    _attest_amd_source_results,
    _attest_amd_source_shards,
    _current_scope_results,
    _keyless_rerouted_source_build,
    _load_cached_results,
    _record,
    _rerouted_source_build,
    _rerouted_source_report,
    _scope_nightly_build,
    offline_runtime_definition_pins,  # noqa: F401
    reporter_module,
)
from vllm.ci.analyzer import compute_build_summary
from vllm.ci.models import TestResult

_CLAIMS = ("source_definition_id", "source_agent_pool", "source_commit",
           "source_step_key", "source_binding_basis")


@pytest.fixture(autouse=True)
def no_provider_requests(monkeypatch):
    monkeypatch.setattr("requests.sessions.Session.request",
                        lambda *_args, **_kwargs: pytest.fail("Attestation tests cannot contact providers"))


@pytest.fixture
def pinned_analyzer(monkeypatch):
    from vllm.ci import analyzer
    from vllm.config_parity import extract_amd_runtime_group_key_map_from_report
    report = _rerouted_source_report()
    commit, definitions = _amd_source_definition_catalog(report)
    _, routes, _ = extract_amd_runtime_group_key_map_from_report(report)
    monkeypatch.setattr(analyzer, "_AMD_RUNTIME_GROUP_KEY_COMMIT", "")
    monkeypatch.setattr(analyzer, "_AMD_RUNTIME_GROUP_KEYS", {})
    monkeypatch.setattr(analyzer, "_AMD_RUNTIME_DEFINITIONS", {})
    analyzer.set_amd_runtime_group_key_map(commit, routes, definitions)
    return commit, definitions


def _rows(build):
    return _current_scope_results([TestResult(**_record(job["name"], job_id=job["id"]))
                                   for job in build["jobs"]], "amd", build)


def test_unknown_step_key_keeps_row_without_source_claims(tmp_path, capsys, pinned_analyzer):
    commit, definitions = pinned_analyzer
    build = _scope_nightly_build(_rerouted_source_build(), "amd")
    build["jobs"][1]["step"]["key"] = "renamed-upstream-key"
    rows = _rows(build)
    rows[1].source_definition_id = "forged#renamed-upstream-key"
    path = reporter_module.write_test_results(rows, "2026-04-18", "amd", tmp_path)
    assert path is not None
    unattested: dict[str, dict] = {}
    selected = _attest_amd_source_shards(tmp_path, build, commit, definitions, unattested)[7791]
    assert [row.job_id for row in selected] == ["job-0", "job-1"]
    assert list(unattested) == ["job-1"]
    assert selected[0].source_binding_basis == "runtime_step_key"
    assert all(getattr(selected[1], field) == "" for field in _CLAIMS)
    assert _load_cached_results(path) == selected
    stderr = capsys.readouterr().err
    assert "::warning::1 AMD jobs in build 7791" in stderr
    summary = compute_build_summary(build, selected, "amd")
    assert summary.total_tests == summary.passed == 2
    assert summary.unique_test_groups == compute_build_summary(build, _rows(build), "amd").unique_test_groups


@pytest.mark.parametrize(("change", "reason"), [
    ("key", "unambiguous pinned source definition"),
    ("wrong_native_pool", "pinned declared source identity"),
])
def test_unbindable_attempt_is_reported_as_unattested(change, reason):
    build = (_scope_nightly_build(_rerouted_source_build(), "amd") if change == "key"
             else _keyless_rerouted_source_build())
    job = build["jobs"][1]
    if change == "key":
        job["step"]["key"] = "unknown-key"
    else:
        job["name"] = "AMD: :amd: (MI355 DPX) LM Eval Small Models (mi355_1)"
    rows = _current_scope_results([TestResult(**_record(job["name"], job_id=job["id"]))] * 2, "amd", build)
    commit, definitions = _amd_source_definition_catalog(_rerouted_source_report())
    unattested: dict[str, dict] = {}
    attested = _attest_amd_source_results(rows, build, commit, definitions, unattested)
    assert len(attested) == 2
    assert all(getattr(row, field) == "" for row in attested for field in _CLAIMS)
    assert list(unattested) == ["job-1"]
    entry = unattested["job-1"]
    assert entry["label"] == rows[0].job_name and entry["queue"] == "amd_mi355_1"
    assert entry["step_key"] == ("unknown-key" if change == "key" else "")
    assert reason in entry["reason"]


@pytest.mark.parametrize("change", ["commit", "label", "job_id"])
def test_identity_contradictions_still_fail_closed(change):
    build = _scope_nightly_build(_rerouted_source_build(), "amd")
    build["jobs"][1]["step"]["key"] = "unknown-key"
    row = _rows(build)[1]
    commit, definitions = _amd_source_definition_catalog(_rerouted_source_report())
    if change == "commit":
        build["commit"] = "b" * 40
    elif change == "label":
        row.job_name = "amd_mi355_1: Unknown group"
    else:
        row.job_id = "foreign"
    with pytest.raises(ValueError):
        _attest_amd_source_results([row], build, commit, definitions, {})


def test_ci_health_publishes_unattested_source_jobs(tmp_path):
    jobs = [{"job_id": f"job-{index}", "reason": "unbound"} for index in range(60)]
    reporter_module.write_ci_health([], [], tmp_path, unattested_sources=jobs)
    health = json.loads((tmp_path / "ci_health.json").read_text())
    assert health["source_attestation"] == {
        "unattested_job_count": 60,
        "unattested_jobs": jobs[:50],
    }
