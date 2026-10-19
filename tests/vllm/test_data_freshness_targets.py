"""The shared collector freshness config covers exactly what the snapshot publishes."""

from __future__ import annotations

import pytest

from vllm import build_operations_snapshot as ops
from vllm.data_freshness_targets import load_freshness_targets


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def test_every_published_shell_source_is_configured(tmp_path):
    data_dir = tmp_path / "ci"
    data_dir.mkdir()
    emitted = ops.build_snapshot(data_dir, generated_at="2026-04-22T12:00:00Z")["sources"]

    assert set(emitted) == set(load_freshness_targets()["sources"])


def test_freshness_targets_are_well_formed():
    targets = load_freshness_targets()
    sources = targets["sources"]

    assert targets["schema_version"] == 1
    assert _positive_int(targets["default_collector_limit_minutes"])
    for name, source in sources.items():
        assert set(source) <= {
            "label", "area", "kind", "collector_limit_minutes", "path", "stale_minutes",
        }, name
        assert isinstance(source["label"], str) and source["label"], name
        assert isinstance(source["area"], str) and source["area"], name
        assert source["kind"] in {"collector", "upstream_activity"}, name
        assert _positive_int(source.get("collector_limit_minutes", 1)), name
    for clock in ("core", "queue"):
        names = targets[clock]["sources"]
        assert names and len(names) == len(set(names)), clock
        assert all(sources[name]["kind"] == "collector" for name in names), clock
        assert all(_positive_int(targets[clock][key]) for key in ("warn_minutes", "stale_minutes"))
        assert targets[clock]["warn_minutes"] < targets[clock]["stale_minutes"], clock
    assert 0 < targets["core"]["target_minutes"] <= targets["core"]["warn_minutes"]
    for name, source in sources.items():
        if "stale_minutes" in source:
            assert name in targets["core"]["sources"], name
            assert source["stale_minutes"] >= targets["core"]["stale_minutes"], name


@pytest.mark.parametrize("missing", ["perf_eval", "workload_mapping"])
def test_core_clock_is_unknown_while_any_core_source_is_undated(missing):
    sources = {
        name: {"timestamp": "2026-04-22T11:00:00Z"}
        for name in load_freshness_targets()["core"]["sources"]
    }
    assert ops._core_generated_at(sources) == "2026-04-22T11:00:00Z"

    sources[missing] = {"timestamp": None}
    assert ops._core_generated_at(sources) is None
    del sources[missing]
    assert ops._core_generated_at(sources) is None
