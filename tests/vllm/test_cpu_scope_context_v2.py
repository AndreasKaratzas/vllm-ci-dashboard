"""Immutable CPU exclusions must survive unrelated producer shard changes."""

from __future__ import annotations

import copy

import pytest

from vllm import collect_dns_failures as collector
from vllm import main_ci_definitions as source
from vllm.ci import analyzer
from vllm.ci import dns_failures as dns

PIN = "a" * 40
TREE = "b" * 40


@pytest.fixture(autouse=True)
def isolated_globals(monkeypatch):
    previous = list(analyzer._SHARD_BASES)
    monkeypatch.setattr(source, "_RUNTIME_SOURCE_STARTED_AT", None)
    monkeypatch.setattr(source, "_RUNTIME_SOURCE_ACTIVE_SECONDS", 0.0)
    monkeypatch.setattr(source, "_RUNTIME_SOURCE_ACTIVE_DEPTH", 0)
    monkeypatch.setattr(source, "_RUNTIME_SOURCE_STARTS", 0)
    yield
    analyzer.set_shard_bases(previous)


def snapshot(*steps):
    return source.MainCISnapshot(PIN, {
        ".buildkite/ci_config.yaml": {"job_dirs": [".buildkite/test_areas"]},
        ".buildkite/test_areas/sample.yaml": {"steps": list(steps)},
    }, "2026-10-12T00:00:00Z", TREE)


def cpu(label="CPU Audit 0", **kwargs):
    return {"label": f":amd: (MI300) {label}", "device": "mi300_1", "no_gpu": True, **kwargs}


def job(label="CPU Audit 0", **kwargs):
    return {"id": "job-1", "name": f":amd: (MI300) {label}",
            "agent_query_rules": ["queue=mi300_1"], **kwargs}


@pytest.mark.parametrize("global_bases", [[], ["cpu audit"], ["other", "cpu audit"], ["renamed"]])
def test_literal_cpu_index_and_join_are_independent_of_global_shard_changes(global_bases):
    analyzer.set_shard_bases(global_bases)
    index = source.runtime_scope_index(snapshot(cpu()))
    assert index == {"version": 2, "commit_sha": PIN, "definition_tree_sha": TREE,
                     "cpu_routes": [{"key": "", "label": "cpu audit 0", "agent_pool": "mi300_1"}]}
    analyzer.set_shard_bases(list(reversed(global_bases)) + ["cpu audit"])
    annotated = source.annotate_runtime_source_scope({"commit": PIN, "jobs": [job()]}, scope_index=index)
    assert annotated["jobs"][0]["source_no_gpu"] is True
    assert analyzer._normalize_job_name("CPU Audit 0", shard_bases=[]) == "cpu audit 0"
    assert analyzer._normalize_job_name("CPU Audit 0", shard_bases=["CPU Audit"]) == "cpu audit"


def test_literal_number_is_not_alias_for_an_unrelated_gpu_step():
    gpu = {"label": ":amd: (MI300) CPU Audit", "device": "mi300_1"}
    analyzer.set_shard_bases(["cpu audit"])
    index = source.runtime_scope_index(snapshot(cpu(), gpu))
    rows = source.annotate_runtime_source_scope(
        {"commit": PIN, "jobs": [job(), job("CPU Audit")]}, scope_index=index,
    )["jobs"]
    assert rows[0]["source_no_gpu"] is True
    assert "source_no_gpu" not in rows[1]


def test_source_parallel_expansion_is_bounded_and_checks_each_gpu_collision():
    gpu = {"label": ":amd: (MI300) CPU Audit 1", "device": "mi300_1"}
    index = source.runtime_scope_index(snapshot(cpu("CPU Audit %N", parallelism=2), gpu))
    labels = {row["label"] for row in index["cpu_routes"]}
    assert labels == {"cpu audit", "cpu audit 0"}
    analyzer.set_shard_bases(["cpu audit"])
    rows = source.annotate_runtime_source_scope(
        {"commit": PIN, "jobs": [job(f"CPU Audit {index}") for index in range(4)]},
        scope_index=index,
    )["jobs"]
    assert [row.get("source_no_gpu", False) for row in rows] == [True, False, False, False]


@pytest.mark.parametrize("parallelism", [True, 0, -1, 2049, "2", None])
def test_unproved_parallel_source_cannot_create_label_exclusions(parallelism):
    with pytest.raises(ValueError, match="bounded source parallelism"):
        source.runtime_scope_index(snapshot(cpu("CPU Audit %N", parallelism=parallelism)))


def test_parallel_expansion_cannot_write_an_index_above_the_aggregate_route_bound():
    with pytest.raises(ValueError, match="route bound"):
        source.runtime_scope_index(snapshot(cpu("CPU Audit %N", parallelism=2048)))


@pytest.mark.parametrize("reverse", [False, True])
def test_mixed_scope_contracts_prefer_verified_v2_without_rewriting_legacy_routes(reverse):
    legacy = {"version": 1, "commit_sha": PIN, "definition_tree_sha": TREE,
              "cpu_routes": [{"key": "", "label": "cpu audit", "agent_pool": "mi300_1"}]}
    current = source.runtime_scope_index(snapshot(cpu()))
    originals = copy.deepcopy((legacy, current))
    pair = (current, legacy) if reverse else (legacy, current)
    assert source.merge_runtime_scope_indexes(*pair, expected_commit=PIN) == current
    assert (legacy, current) == originals


@pytest.mark.parametrize("version", [1, 2])
def test_same_scope_contract_still_rejects_unequal_routes(version):
    original = source.runtime_scope_index(snapshot(cpu()))
    original["version"] = version
    conflicting = {**original, "cpu_routes": []}
    with pytest.raises(ValueError, match="same immutable contract"):
        source.merge_runtime_scope_indexes(original, conflicting, expected_commit=PIN)


def test_scope_contract_migration_cannot_change_tree_or_commit():
    old = source.runtime_scope_index(snapshot(cpu()))
    old["version"] = 1
    current = {**old, "version": 2, "definition_tree_sha": "d" * 40}
    with pytest.raises(ValueError, match="exact immutable tree"):
        source.merge_runtime_scope_indexes(old, current, expected_commit=PIN)
    current = {**old, "version": 2, "commit_sha": "c" * 40}
    with pytest.raises(ValueError, match="exact immutable commit/tree"):
        source.merge_runtime_scope_indexes(old, current, expected_commit=PIN)


def test_v1_index_reproof_uses_its_verified_tree_and_preserves_other_cached_pins(monkeypatch, tmp_path):
    from vllm.ci.analytics_cache import read_runtime_source_indexes, write_runtime_source_indexes

    old = {"version": 1, "commit_sha": PIN, "definition_tree_sha": TREE,
           "cpu_routes": [{"key": "", "label": "cpu audit", "agent_pool": "mi300_1"}]}
    other_pin = "c" * 40
    other = {"version": 1, "commit_sha": other_pin, "definition_tree_sha": "d" * 40,
             "cpu_routes": []}
    cache = tmp_path / "runtime-source-indexes-v1"
    write_runtime_source_indexes(cache, {PIN: old, other_pin: other})
    stored = read_runtime_source_indexes(cache)
    calls = []

    def files(tree):
        calls.append(tree)
        return snapshot(cpu()).files

    monkeypatch.setattr(source, "_runtime_definition_files", files)
    monkeypatch.setattr(source, "runtime_snapshot", lambda pin: pytest.fail("do not reset commit proof cache"))
    analyzer.set_shard_bases(["cpu audit"])
    annotated = source.annotate_runtime_source_scope(
        {"commit": PIN, "jobs": [job()]}, scope_index=stored[PIN],
    )
    assert calls == [TREE]
    assert annotated["jobs"][0]["source_no_gpu"] is True
    assert annotated["source_scope_index"]["version"] == 2
    write_runtime_source_indexes(cache, {**stored, PIN: annotated["source_scope_index"]})
    upgraded = read_runtime_source_indexes(cache)
    assert upgraded[other_pin] == other
    assert old == stored[PIN]
    assert "generated_at" not in upgraded[PIN]


def test_v1_bad_verified_tree_content_cannot_be_promoted(monkeypatch):
    old = {"version": 1, "commit_sha": PIN, "definition_tree_sha": TREE, "cpu_routes": []}
    monkeypatch.setattr(source, "_runtime_definition_files", lambda tree: {})
    with pytest.raises(source.RuntimeSourceError):
        source.annotate_runtime_source_scope({"commit": PIN, "jobs": [job()]}, scope_index=old)
    assert old["version"] == 1


@pytest.mark.parametrize("module", ["collect_queue_snapshot", "collect_workload_mapping"])
def test_queue_and_workload_reproof_checkpoint_once_and_preserve_unrequested_pins(module, monkeypatch, tmp_path):
    import importlib

    from vllm.ci.analytics_cache import read_runtime_source_indexes

    consumer = importlib.import_module(f"vllm.{module}")
    old = {"version": 1, "commit_sha": PIN, "definition_tree_sha": TREE,
           "cpu_routes": [{"key": "", "label": "hidden audit", "agent_pool": "mi300_1"}]}
    other = {"version": 1, "commit_sha": "c" * 40, "definition_tree_sha": "d" * 40,
             "cpu_routes": []}
    cache = tmp_path / "runtime-source-indexes-v1"
    monkeypatch.setattr(consumer, "_SOURCE_SCOPE_INDEXES", {PIN: old, other["commit_sha"]: other})
    monkeypatch.setattr(consumer, "_SOURCE_SCOPE_CACHE_DIR", cache)
    monkeypatch.setattr(source, "prewarm_runtime_snapshots", lambda pins: pytest.fail("retained pins avoid root lookup"))
    reads = []
    monkeypatch.setattr(source, "_runtime_definition_files", lambda tree: reads.append(tree) or snapshot(cpu("Hidden Audit 0")).files)
    build = {"pipeline": {"slug": "ci"}, "commit": PIN,
             "jobs": [job("Hidden Audit 0", type="script", agent_query_rules=["queue=amd_mi300_1"])]}
    results = consumer._scope_ci_builds([build, build], "ci")
    assert reads == [TREE]
    assert all(result["jobs"][0]["source_no_gpu"] is True for result in results)
    stored = read_runtime_source_indexes(cache)
    assert stored[PIN]["version"] == 2
    assert stored[other["commit_sha"]] == other
    assert not any("generated_at" in index for index in stored.values())


def proof(version, classification):
    return {"version": version, "source_commit": PIN,
            "definition_tree": TREE, "classification": classification}


def observation(version=1, classification="amd_mi_gpu"):
    return {
        "pipeline": "ci", "build_number": 123,
        "job_id": "00000000-0000-4000-8000-000000000001",
        "queue": "amd_mi300_1", "hardware": "MI300", "node": "node-1", "state": "passed",
        "started_at": "2026-10-12T00:00:00Z", "finished_at": "2026-10-12T00:30:00Z",
        "execution_proof": proof(version, classification),
    }


@pytest.mark.parametrize("old_class,new_class", [
    ("amd_mi_gpu", "amd_mi_gpu"), ("excluded_cpu", "excluded_cpu"),
    ("amd_mi_gpu", "excluded_cpu"), ("excluded_cpu", "amd_mi_gpu"),
])
def test_verified_same_job_v2_migration_keeps_log_outcome_and_private_legacy_trail(old_class, new_class):
    old = dns.scan_record(observation(1, old_class), dns.classify_dns_log(
        "getaddrinfo failed", job_finished_at="2026-10-12T00:30:00Z",
    ), attempted_at="2026-10-12T01:00:00Z")
    current = observation(2, new_class)
    before = copy.deepcopy(old)
    refreshed = collector._refresh_record(old, current)
    expected = proof(2, new_class)
    if old_class != new_class:
        expected["legacy_classification"] = old_class
    assert refreshed["execution_proof"] == expected
    assert old == before
    assert refreshed["status"] == old["status"]
    assert refreshed["last_attempt_at"] == old["last_attempt_at"]
    assert refreshed["episode_times"] == old["episode_times"]
    assert collector._refresh_record(refreshed, current) == refreshed
    pending = dns.pending_record(current)
    assert dns.merge_state_jobs([old], [pending])[0] == refreshed
    assert dns.merge_state_jobs([pending], [old])[0] == refreshed


@pytest.mark.parametrize("field,value", [
    ("source_commit", "c" * 40), ("definition_tree", "d" * 40),
])
def test_scope_version_migration_never_changes_an_exact_source_identity(field, value):
    old, current = observation(1), observation(2)
    current["execution_proof"][field] = value
    with pytest.raises(dns.StateValidationError) as raised:
        collector._refresh_record(old, current)
    assert raised.value.diagnostic_code == "execution_proof_conflict"
    assert old["execution_proof"] == proof(1, "amd_mi_gpu")


@pytest.mark.parametrize("field,value", [
    ("pipeline", "amd-ci"), ("build_number", 124),
    ("job_id", "00000000-0000-4000-8000-000000000002"),
    ("queue", "amd_mi300_2"), ("hardware", "MI355"),
    ("finished_at", "2026-10-12T00:31:00Z"),
])
def test_scope_version_migration_requires_the_same_observed_job_metadata(field, value):
    old, current = observation(1), observation(2)
    current[field] = value
    with pytest.raises(dns.StateValidationError) as raised:
        collector._refresh_record(old, current)
    assert raised.value.diagnostic_code == "execution_proof_job_mismatch"


def test_same_version_scope_disagreement_remains_a_hard_failure():
    for version in (1, 2):
        old, current = observation(version), observation(version, "excluded_cpu")
        with pytest.raises(dns.StateValidationError) as raised:
            collector._refresh_record(old, current)
        assert raised.value.diagnostic_code == "execution_proof_conflict"


def test_two_conflicting_legacy_states_fail_before_any_provider_work(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone

    clock = datetime(2026, 10, 12, 2, tzinfo=timezone.utc)
    local = dns.empty_state(clock - timedelta(hours=1), clock - timedelta(days=30))
    restored = copy.deepcopy(local)
    local["jobs"] = [dns.pending_record(observation(1, "amd_mi_gpu"))]
    restored["jobs"] = [dns.pending_record(observation(1, "excluded_cpu"))]
    monkeypatch.setattr(collector, "load_state", lambda path: local)
    monkeypatch.setattr(collector, "load_state_from_git_ref", lambda *args, **kwargs: restored)

    class NoProvider:
        def __getattr__(self, name):
            pytest.fail("conflicting retained identity must fail before provider access")

    with pytest.raises(dns.StateValidationError) as raised:
        collector.collect(client=NoProvider(), state_path=tmp_path / "private.json",
                          output_path=tmp_path / "public.json", now=clock,
                          minimum_interval_hours=0, merge_state_git_ref="checkpoint",
                          repo_root=tmp_path, dry_run=True)
    assert raised.value.diagnostic_code == "execution_proof_conflict"
    assert local["jobs"][0]["execution_proof"]["version"] == 1
    assert not (tmp_path / "public.json").exists()


def test_legacy_proofs_remain_readable_but_cannot_authorize_current_scan_or_coverage():
    from datetime import datetime, timedelta, timezone

    clock = datetime(2026, 10, 12, 1, tzinfo=timezone.utc)
    state = dns.empty_state(clock, clock - timedelta(hours=1))
    state["jobs"] = [dns.pending_record(observation(1))]
    restored = dns.state_from_bytes(dns.state_bytes(state))
    assert restored == state
    public = dns.build_public_output(restored)
    assert public["coverage"]["eligible_jobs"] == 0
    assert public["coverage"]["discovery_complete"] is False

    class NoLogs:
        def fetch_job_log(self, *args, **kwargs):
            pytest.fail("unreobserved v1 scope cannot spend log requests")

    rows = collector.scan_records(restored["jobs"], client=NoLogs(),
                                  attempted_at="2026-10-12T01:00:00Z", max_logs=1)
    assert rows == restored["jobs"]


@pytest.mark.parametrize("mutation", [
    {"version": True}, {"version": 3}, {"legacy_classification": "gpu"},
    {"legacy_classification": "amd_mi_gpu"}, {"private_body": "secret"},
])
def test_v2_proof_and_legacy_trail_contract_fail_closed(mutation):
    with pytest.raises(dns.StateValidationError):
        dns.validate_execution_proof({**proof(2, "amd_mi_gpu"), **mutation})


@pytest.mark.parametrize("code", sorted(dns.STATE_FAILURE_CODES))
def test_state_failure_artifact_contains_only_fixed_type_and_code(tmp_path, monkeypatch, caplog, code):
    private = "private-response-uuid-node-token"

    def fail(**kwargs):
        raise dns.StateValidationError(private, diagnostic_code=code)

    monkeypatch.setenv("BUILDKITE_TOKEN", "memory-only-token")
    monkeypatch.setattr(collector, "read_runtime_source_indexes", lambda path: {})
    monkeypatch.setattr(collector, "collect", fail)
    path = tmp_path / "failure.json"
    assert collector.main(["--failure-diagnostics", str(path)]) == 1
    import json

    assert json.loads(path.read_text()) == {
        "schema_version": 1, "error_type": "StateValidationError", "diagnostic_code": code,
    }
    assert private not in caplog.text + path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_size <= collector.FAILURE_DIAGNOSTICS_MAX_BYTES


@pytest.mark.parametrize("unknown", ["private-value", True, None, [], {}])
def test_unknown_state_diagnostic_code_is_redacted_to_the_fixed_default(unknown):
    assert dns.StateValidationError("private", diagnostic_code=unknown).diagnostic_code == "invalid_state"
