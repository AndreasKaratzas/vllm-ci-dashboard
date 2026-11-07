"""Tests for ``scripts/vllm/omni_surge_watcher.py``.

The watcher derives the Omni surge threshold from the omni YAML test groups
and writes the heuristic snapshot for the dashboard. These tests stub out the
YAML fetch and never touch the network.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from vllm import omni_surge_watcher as osw
from vllm.constants import (
    OMNI_SURGE_FLOOR_TRIGGER,
    OMNI_SURGE_HEALTHY_RATIO,
    OMNI_SURGE_MULTIPLIER,
)


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    heur = tmp_path / "omni_surge_heuristic.json"
    monkeypatch.setattr(osw, "HEURISTIC_PATH", heur, raising=False)
    return heur


def test_watcher_has_no_buildkite_api_surface():
    source = Path(osw.__file__).read_text(encoding="utf-8")
    assert "api.buildkite.com" not in source
    assert "BUILDKITE_TOKEN" not in source
    assert "BUILDKITE_API_TOKEN" not in source


class TestThreshold:
    def test_trigger_uses_floor_when_yaml_small(self):
        # 10 groups × 1.3 = 13 → floor of 30 wins.
        trigger, healthy, info = osw._compute_trigger(
            [{"label": f"t-{i}", "agent_pool": "amd"} for i in range(10)]
        )
        assert trigger == OMNI_SURGE_FLOOR_TRIGGER
        assert healthy == math.floor(trigger * OMNI_SURGE_HEALTHY_RATIO)
        assert info["total_groups"] == 10

    def test_trigger_scales_with_group_count(self):
        # 100 groups × 1.3 = 130 → beats floor.
        trigger, healthy, info = osw._compute_trigger(
            [{"label": f"t-{i}"} for i in range(100)]
        )
        assert trigger == math.ceil(100 * OMNI_SURGE_MULTIPLIER)
        assert healthy < trigger
        assert info["total_groups"] == 100

    def test_pool_distribution_counted_from_agent_pool_and_agents_queue(self):
        groups = [
            {"label": "a", "agent_pool": "alpha"},
            {"label": "b", "agents": {"queue": "beta"}},
            {"label": "c"},
        ]
        _, _, info = osw._compute_trigger(groups)
        assert info["pool_distribution"]["alpha"] == 1
        assert info["pool_distribution"]["beta"] == 1
        assert info["pool_distribution"]["unknown"] == 1

    def test_incomplete_dynamic_count_is_not_promoted_to_last_good(
        self, isolated_paths, monkeypatch
    ):
        heuristic_path = isolated_paths
        monkeypatch.setattr(
            osw,
            "OMNI_YAML_PATHS",
            ("ready.yml", "merge.yml"),
            raising=False,
        )
        _, _, info = osw._derive_heuristic(
            [{"label": "ready-test"}],
            ["ready.yml"],
            None,
        )
        heuristic_path.write_text(json.dumps(info))

        assert info["source_status"] == "partial"
        assert info["mutations_suppressed"] is True
        assert osw._read_last_good_heuristic() is None

    def test_run_records_payload_freshness(self, isolated_paths, monkeypatch):
        heuristic_path = isolated_paths
        monkeypatch.setattr(
            osw,
            "OMNI_YAML_PATHS",
            ("ready.yml", "merge.yml"),
            raising=False,
        )
        monkeypatch.setattr(
            osw,
            "_fetch_yaml",
            lambda path: f"steps:\n  - label: {path}\n",
        )
        assert osw.run() == 0
        payload = json.loads(heuristic_path.read_text())
        assert payload["source_status"] == "fresh"
        assert payload["generated_at"].endswith("Z")
        assert payload["last_successful_at"] == payload["generated_at"]


class TestYamlParse:
    def test_configured_paths_match_current_omni_layout(self):
        assert osw.OMNI_YAML_PATHS == (
            ".buildkite/amd/test-amd-ready.yml",
            ".buildkite/amd/test-amd-merge.yml",
        )

    def test_top_level_list(self):
        txt = "- label: a\n- label: b\n"
        assert len(osw._parse_test_groups(txt)) == 2

    def test_top_level_dict_with_steps(self):
        txt = "steps:\n  - label: a\n  - label: b\n"
        assert len(osw._parse_test_groups(txt)) == 2

    def test_flattens_nested_group(self):
        txt = (
            "steps:\n"
            "  - group: outer\n"
            "    steps:\n"
            "      - label: inner-1\n"
            "      - label: inner-2\n"
            "  - label: plain\n"
        )
        # inner-1, inner-2, plain — 3 groups (outer is a container).
        assert len(osw._parse_test_groups(txt)) == 3

    def test_invalid_yaml_returns_empty(self):
        assert osw._parse_test_groups(":::not valid:::\n- label: [") == []


class TestHeuristicRefresh:
    def test_refreshes_heuristic(self, isolated_paths, monkeypatch):
        heuristic_path = isolated_paths
        monkeypatch.setattr(osw, "OMNI_YAML_PATHS", ("ready.yml",), raising=False)
        monkeypatch.setattr(
            osw,
            "_fetch_yaml",
            lambda path: "steps:\n  - label: ready-test\n",
        )

        assert osw.run() == 0

        payload = json.loads(heuristic_path.read_text())
        assert payload["source_status"] == "fresh"
        assert payload["yaml_paths_fetched"] == ["ready.yml"]
        assert payload["last_successful_at"] == payload["generated_at"]

    def test_missing_sources_fail_without_last_known_good(
        self, isolated_paths, monkeypatch
    ):
        heuristic_path = isolated_paths
        monkeypatch.setattr(osw, "OMNI_YAML_PATHS", ("ready.yml",), raising=False)
        monkeypatch.setattr(osw, "_fetch_yaml", lambda path: None)

        assert osw.run() == 1

        payload = json.loads(heuristic_path.read_text())
        assert payload["source_status"] == "unavailable"
        assert payload["fallback_floor_used"] is True
        assert payload["mutations_suppressed"] is True

    def test_failed_refresh_retains_diagnostic_but_rejects_last_known_good(
        self, isolated_paths, monkeypatch
    ):
        heuristic_path = isolated_paths
        monkeypatch.setattr(osw, "OMNI_YAML_PATHS", ("ready.yml",), raising=False)
        monkeypatch.setattr(osw, "_fetch_yaml", lambda path: None)
        _, _, last_good = osw._compute_trigger(
            [{"label": f"test-{index}", "agent_pool": "amd"} for index in range(40)]
        )
        last_good.update({
            "generated_at": "2026-08-11T10:00:00Z",
            "last_successful_at": "2026-08-11T10:00:00Z",
            "yaml_paths_fetched": ["ready.yml"],
            "source_status": "fresh",
            "fallback_floor_used": False,
            "using_last_known_good": False,
            "mutations_suppressed": False,
        })
        heuristic_path.write_text(json.dumps(last_good))

        assert osw.run() == 1

        payload = json.loads(heuristic_path.read_text())
        assert payload["source_status"] == "last_known_good"
        assert payload["using_last_known_good"] is True
        assert payload["mutations_suppressed"] is True
        assert payload["last_successful_at"] == "2026-08-11T10:00:00Z"
        assert payload["last_successful_yaml_paths"] == ["ready.yml"]

    def test_failed_refresh_rejects_undated_last_known_good(
        self, isolated_paths, monkeypatch
    ):
        heuristic_path = isolated_paths
        monkeypatch.setattr(osw, "OMNI_YAML_PATHS", ("ready.yml",), raising=False)
        monkeypatch.setattr(osw, "_fetch_yaml", lambda path: None)
        _, _, last_good = osw._compute_trigger([{"label": "test", "agent_pool": "amd"}])
        last_good.update({
            "last_successful_at": "not-a-timestamp",
            "yaml_paths_fetched": ["ready.yml"],
            "fallback_floor_used": False,
        })
        heuristic_path.write_text(json.dumps(last_good))

        assert osw.run() == 1
