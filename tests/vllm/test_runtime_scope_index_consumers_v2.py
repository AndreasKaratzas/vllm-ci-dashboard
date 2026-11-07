"""Exercise mixed authenticated index generations through the actual collector."""

from __future__ import annotations

import copy

import pytest

from vllm import collect_analytics as collector
from vllm import main_ci_definitions as source
from vllm.ci import analytics_cache as cache
from vllm.ci import analyzer

PIN = "a" * 40
TREE = "b" * 40
OTHER_PIN = "c" * 40
OTHER_TREE = "d" * 40


def snapshot():
    return source.MainCISnapshot(PIN, {
        ".buildkite/ci_config.yaml": {"job_dirs": [".buildkite/test_areas"]},
        ".buildkite/test_areas/sample.yaml": {"steps": [
            {"label": ":amd: (MI300) CPU Audit 0", "device": "mi300_1", "no_gpu": True},
            {"label": ":amd: (MI300) GPU Test", "device": "mi300_1", "key": "gpu-test"},
        ]},
    }, "2026-10-12T00:00:00Z", TREE)


def legacy_index(pin=PIN, tree=TREE):
    return {"version": 1, "commit_sha": pin, "definition_tree_sha": tree,
            "cpu_routes": [{"key": "", "label": "cpu audit", "agent_pool": "mi300_1"}]}


def build(number, index=None, *, pin=PIN):
    value = {"number": number, "commit": pin, "jobs": [
        {"id": f"00000000-0000-4000-8000-{number * 2:012d}", "type": "script",
         "name": ":amd: (MI300) CPU Audit 0", "agent_query_rules": ["queue=amd_mi300_1"]},
        {"id": f"00000000-0000-4000-8000-{number * 2 + 1:012d}", "type": "script",
         "name": ":amd: (MI300) GPU Test", "agent_query_rules": ["queue=amd_mi300_1"]},
    ]}
    if index is not None:
        value.update(source_scope_commit=pin, source_definition_tree_sha=index["definition_tree_sha"],
                     source_scope_index=copy.deepcopy(index))
    return value


@pytest.fixture(autouse=True)
def isolated_source_state(monkeypatch):
    previous = list(analyzer._SHARD_BASES)
    monkeypatch.setattr(collector, "_SOURCE_SCOPE_INDEXES", {})
    monkeypatch.setattr(collector, "_SOURCE_SCOPE_CACHE_DIR", None)
    monkeypatch.setattr(source, "_RUNTIME_SOURCE_STARTED_AT", None)
    monkeypatch.setattr(source, "_RUNTIME_SOURCE_ACTIVE_SECONDS", 0.0)
    monkeypatch.setattr(source, "_RUNTIME_SOURCE_ACTIVE_DEPTH", 0)
    monkeypatch.setattr(source, "_RUNTIME_SOURCE_STARTS", 0)
    monkeypatch.setattr(source, "prewarm_runtime_snapshots", lambda *_: pytest.fail("retained pins must not be reset"))
    monkeypatch.setattr(collector, "bk_get", lambda *_args, **_kwargs: pytest.fail("index migration must not access Buildkite"))
    analyzer.set_shard_bases(["cpu audit"])
    yield
    analyzer.set_shard_bases(previous)


@pytest.mark.parametrize("provided_version,restored_version,embedded_version", [
    (1, 2, 1), (2, 1, 1), (1, 2, 2), (2, 2, 1),
])
def test_collector_reconciles_mixed_authenticated_generations_without_source_refetch(
    monkeypatch, provided_version, restored_version, embedded_version,
):
    indexes = {1: legacy_index(), 2: source.runtime_scope_index(snapshot())}
    monkeypatch.setattr(collector, "_SOURCE_SCOPE_INDEXES", {PIN: indexes[restored_version]})
    monkeypatch.setattr(source, "_runtime_definition_files", lambda *_: pytest.fail("verified v2 already exists"))
    original = [build(1, indexes[embedded_version]), build(2, indexes[3 - embedded_version])]
    before = copy.deepcopy(original)

    result = collector._current_mi_builds(original, "ci", scope_indexes={PIN: indexes[provided_version]})

    assert original == before
    assert all(row["source_scope_index"] == indexes[2] for row in result)
    assert all([job["name"] for job in row["jobs"]] == [":amd: (MI300) GPU Test"] for row in result)
    assert collector._SOURCE_SCOPE_INDEXES[PIN] == indexes[2]


def test_legacy_reproof_checkpoints_once_and_reuses_v2_for_remaining_builds(monkeypatch, tmp_path):
    legacy = {PIN: legacy_index(), OTHER_PIN: legacy_index(OTHER_PIN, OTHER_TREE)}
    source_dir = tmp_path / cache.RUNTIME_SOURCE_CACHE_DIR_NAME
    cache.write_runtime_source_indexes(source_dir, legacy)
    monkeypatch.setattr(collector, "_SOURCE_SCOPE_INDEXES", copy.deepcopy(legacy))
    monkeypatch.setattr(collector, "_SOURCE_SCOPE_CACHE_DIR", source_dir)
    calls = []

    def files(tree):
        calls.append(tree)
        assert tree == TREE
        return snapshot().files

    monkeypatch.setattr(source, "_runtime_definition_files", files)
    original = [build(number, legacy[PIN]) for number in range(1, 4)]
    before = copy.deepcopy(original)

    result = collector._current_mi_builds(original, "ci")

    assert calls == [TREE]
    assert original == before
    assert all(row["source_scope_index"]["version"] == 2 for row in result)
    assert all(len(row["jobs"]) == 1 for row in result)
    retained = cache.read_runtime_source_indexes(source_dir)
    assert retained[PIN] == source.runtime_scope_index(snapshot())
    assert retained[OTHER_PIN] == legacy[OTHER_PIN]


def test_completed_upgrade_survives_a_later_source_failure(monkeypatch, tmp_path):
    retained = {PIN: legacy_index(), OTHER_PIN: legacy_index(OTHER_PIN, OTHER_TREE)}
    source_dir = tmp_path / cache.RUNTIME_SOURCE_CACHE_DIR_NAME
    cache.write_runtime_source_indexes(source_dir, retained)
    monkeypatch.setattr(collector, "_SOURCE_SCOPE_INDEXES", copy.deepcopy(retained))
    monkeypatch.setattr(collector, "_SOURCE_SCOPE_CACHE_DIR", source_dir)

    def files(tree):
        if tree == OTHER_TREE:
            raise source.RuntimeSourceError(reason_class="network", phase="definitions")
        assert tree == TREE
        return snapshot().files

    monkeypatch.setattr(source, "_runtime_definition_files", files)
    original = [build(1, retained[PIN]), build(2, retained[OTHER_PIN], pin=OTHER_PIN)]
    before = copy.deepcopy(original)

    with pytest.raises(source.RuntimeSourceError) as raised:
        collector._current_mi_builds(original, "ci")

    assert raised.value.reason_class == "network"
    assert original == before
    checkpoint = cache.read_runtime_source_indexes(source_dir)
    assert checkpoint[PIN]["version"] == 2
    assert checkpoint[OTHER_PIN] == retained[OTHER_PIN]


@pytest.mark.parametrize("mismatch", ["same-version-routes", "changed-tree", "changed-commit"])
def test_conflicting_authenticated_indexes_fail_before_source_access(monkeypatch, mismatch):
    current = source.runtime_scope_index(snapshot())
    other = copy.deepcopy(current if mismatch == "same-version-routes" else legacy_index())
    if mismatch == "same-version-routes":
        other["cpu_routes"] = []
    else:
        other["definition_tree_sha" if mismatch == "changed-tree" else "commit_sha"] = OTHER_TREE
    monkeypatch.setattr(collector, "_SOURCE_SCOPE_INDEXES", {PIN: current})
    monkeypatch.setattr(source, "_runtime_definition_files", lambda *_: pytest.fail("contradictions must fail before fetching"))

    with pytest.raises(source.RuntimeSourceError) as raised:
        collector._current_mi_builds([build(1, other)], "ci")

    assert raised.value.reason_class == "schema-drift"
    assert collector._SOURCE_SCOPE_INDEXES == {PIN: current}
