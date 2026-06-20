"""Tests for ``scripts/vllm/omni_surge_watcher.py``.

The watcher opens / closes an issue when the count of waiting *omni* jobs
(summed across AMD queues) crosses a dynamic threshold derived from the
omni YAML test groups.

These tests stub out the YAML fetch and the GitHub API entirely; we
validate the decision logic — threshold math, hysteresis, state
persistence, and the heuristic snapshot that's written for the dashboard
— without touching the network.
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
    snaps = tmp_path / "queue_timeseries.jsonl"
    state = tmp_path / "open_omni_surge_issues.json"
    heur = tmp_path / "omni_surge_heuristic.json"
    monkeypatch.setattr(osw, "SNAPSHOTS", snaps, raising=False)
    monkeypatch.setattr(osw, "STATE", state, raising=False)
    monkeypatch.setattr(osw, "HEURISTIC_PATH", heur, raising=False)
    return snaps, state, heur


def _write_snapshot(path: Path, queues: dict, ts: str = "2026-04-18T10:00:00Z"):
    path.write_text(json.dumps({"ts": ts, "queues": queues}) + "\n")


class _StubbedApi:
    """Stub the watcher's HTTP surface.

    We replace ``_fetch_yaml`` so the watcher never reaches the network, plus
    ``_open_issue`` / ``_close`` / ``_comment`` so we can assert on the
    exact set of mutations the watcher would have performed.
    """

    def __init__(self, yaml_groups: int = 100):
        self.opened = []   # list of (waiting, trigger) tuples
        self.closed = []
        self.commented = []
        self.assigned = []
        self._next = 500
        # A stub YAML where every `label:` row counts as one group.
        self._yaml_text = "\n".join([f"- label: test-{i}" for i in range(yaml_groups)])

    def fetch_yaml(self, path):
        return self._yaml_text

    def open_issue(self, token, repo, waiting, by_queue, heuristic, snap_ts, run_url):
        num = self._next
        self._next += 1
        owner = repo.split("/", 1)[0]
        body = (
            f"cc @{owner} for visibility.\n\n"
            f"Auto-opened by `omni_surge_watcher.py` from {run_url}. Will auto-close once the "
            f"waiting count drops to {heuristic['healthy']}.\n"
        )
        self.opened.append((waiting, heuristic["trigger"], num, body))
        return num

    def close(self, token, repo, number):
        self.closed.append(number)

    def comment(self, token, repo, number, body):
        self.commented.append((number, body))

    def assign(self, token, repo, number):
        self.assigned.append(number)


@pytest.fixture
def stub_api(monkeypatch):
    api = _StubbedApi()
    # Pin OMNI_YAML_PATHS to a single path during tests — the production
    # tuple lists four fallback locations and our stub returns the same
    # text for every path, which would quadruple ``total_groups`` and
    # silently push ``trigger`` past the floor. Forcing a single path
    # keeps the group counts in this fixture equal to what each test
    # declares in ``_yaml_text``.
    monkeypatch.setattr(
        osw, "OMNI_YAML_PATHS", (".buildkite/test-amd.yaml",), raising=False
    )
    monkeypatch.setattr(osw, "_fetch_yaml", api.fetch_yaml)
    monkeypatch.setattr(osw, "_open_issue", api.open_issue)
    monkeypatch.setattr(osw, "_close", api.close)
    monkeypatch.setattr(osw, "_comment", api.comment)
    monkeypatch.setattr(osw, "_ensure_owner_assigned", api.assign)
    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    monkeypatch.setenv("GITHUB_REPOSITORY", "AndreasKaratzas/vllm-ci-dashboard")
    return api


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


class TestYamlParse:
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


class TestWaitingExtraction:
    def test_sums_across_amd_queues_only(self):
        snap = {
            "queues": {
                "amd_mi250_1": {"waiting_by_workload": {"omni": 5, "ci": 20}},
                "amd_mi300_1": {"waiting_by_workload": {"omni": 7}},
                "other_queue":  {"waiting_by_workload": {"omni": 99}},  # skipped
            }
        }
        total, by_queue = osw._current_omni_waiting(snap)
        assert total == 12
        assert by_queue == {"amd_mi250_1": 5, "amd_mi300_1": 7}

    def test_no_omni_key_returns_zero(self):
        snap = {"queues": {"amd_mi250_1": {"waiting_by_workload": {"ci": 10}}}}
        total, by_queue = osw._current_omni_waiting(snap)
        assert total == 0
        assert by_queue == {}

    def test_missing_queues_key_returns_zero(self):
        total, by_queue = osw._current_omni_waiting({})
        assert total == 0
        assert by_queue == {}


class TestRun:
    def test_opens_issue_when_waiting_above_trigger(self, isolated_paths, stub_api):
        snaps, state, heur = isolated_paths
        # 40 omni jobs waiting, trigger will be 30 (floor) or higher.
        _write_snapshot(snaps, {
            "amd_mi250_1": {"waiting_by_workload": {"omni": 40}},
        })
        stub_api._yaml_text = "\n".join(f"- label: t{i}" for i in range(10))  # 10 groups
        rc = osw.run()
        assert rc == 0
        assert len(stub_api.opened) == 1
        assert stub_api.opened[0][0] == 40  # waiting
        assert stub_api.opened[0][1] == OMNI_SURGE_FLOOR_TRIGGER
        assert "cc @AndreasKaratzas for visibility." in stub_api.opened[0][3]
        persisted = json.loads(state.read_text())
        assert persisted["open"] == stub_api.opened[0][2]
        assert persisted["last_value"] == 40
        # Heuristic snapshot must be written so the dashboard can render it.
        assert heur.exists()
        heur_data = json.loads(heur.read_text())
        assert heur_data["total_groups"] == 10
        assert heur_data["trigger"] == OMNI_SURGE_FLOOR_TRIGGER

    def test_no_action_when_below_trigger(self, isolated_paths, stub_api):
        snaps, state, _ = isolated_paths
        _write_snapshot(snaps, {"amd_mi250_1": {"waiting_by_workload": {"omni": 5}}})
        rc = osw.run()
        assert rc == 0
        assert stub_api.opened == []
        assert stub_api.closed == []

    def test_no_reopen_when_already_tracked(self, isolated_paths, stub_api):
        snaps, state, _ = isolated_paths
        state.write_text(json.dumps({"open": 999, "last_value": 50}))
        _write_snapshot(snaps, {"amd_mi250_1": {"waiting_by_workload": {"omni": 50}}})
        rc = osw.run()
        assert rc == 0
        assert stub_api.opened == []  # don't reopen an already-open tracker

    def test_closes_when_waiting_drops_to_healthy(self, isolated_paths, stub_api):
        snaps, state, _ = isolated_paths
        state.write_text(json.dumps({"open": 999, "last_value": 50}))
        # Healthy threshold for floor-trigger of 30 is floor(30*0.7)=21 → 10 is <= healthy.
        _write_snapshot(snaps, {"amd_mi250_1": {"waiting_by_workload": {"omni": 10}}})
        stub_api._yaml_text = "\n".join(f"- label: t{i}" for i in range(5))
        rc = osw.run()
        assert rc == 0
        assert stub_api.assigned == [999]
        assert 999 in stub_api.closed
        assert stub_api.commented and stub_api.commented[0][0] == 999
        persisted = json.loads(state.read_text())
        assert persisted["open"] is None

    def test_hysteresis_keeps_issue_open_between_thresholds(self, isolated_paths, stub_api):
        snaps, state, _ = isolated_paths
        state.write_text(json.dumps({"open": 999, "last_value": 25}))
        # 25 is below trigger(30) but above healthy(21) — don't close.
        _write_snapshot(snaps, {"amd_mi250_1": {"waiting_by_workload": {"omni": 25}}})
        stub_api._yaml_text = "\n".join(f"- label: t{i}" for i in range(5))
        rc = osw.run()
        assert rc == 0
        assert stub_api.assigned == [999]
        assert stub_api.closed == []
        assert stub_api.opened == []  # already tracked anyway
        # last_value is refreshed so the dashboard reflects the current reading.
        assert json.loads(state.read_text())["last_value"] == 25

    def test_no_snapshot_returns_early(self, isolated_paths, stub_api):
        # No snapshot file → nothing to do, graceful exit.
        rc = osw.run()
        assert rc == 0
        assert stub_api.opened == []

    def test_no_token_skips_mutations_but_writes_state(self, isolated_paths, stub_api, monkeypatch):
        snaps, state, heur = isolated_paths
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        _write_snapshot(snaps, {"amd_mi250_1": {"waiting_by_workload": {"omni": 50}}})
        rc = osw.run()
        assert rc == 0
        assert stub_api.opened == []  # no token → no mutations
        # State and heuristic still written so the dashboard reflects the reading.
        assert state.exists()
        assert heur.exists()

    def test_yaml_fetch_failure_falls_back_to_floor(self, isolated_paths, stub_api, monkeypatch):
        snaps, _, heur = isolated_paths
        monkeypatch.setattr(osw, "_fetch_yaml", lambda path: None)
        _write_snapshot(snaps, {"amd_mi250_1": {"waiting_by_workload": {"omni": 40}}})
        rc = osw.run()
        assert rc == 0
        # Fallback — floor trigger, not derived. Should still open the issue.
        assert stub_api.opened and stub_api.opened[0][1] == OMNI_SURGE_FLOOR_TRIGGER
        info = json.loads(heur.read_text())
        assert info["fallback_floor_used"] is True
        assert info["total_groups"] == 0

    def test_runs_read_junk_lines_without_crashing(self, isolated_paths, stub_api):
        snaps, _, _ = isolated_paths
        # Append a real line after a garbage line — _read_last_snapshot picks last valid.
        snaps.write_text(
            "not json garbage\n"
            + json.dumps({"ts": "T2", "queues": {"amd_mi250_1": {"waiting_by_workload": {"omni": 5}}}})
            + "\n"
        )
        rc = osw.run()
        assert rc == 0
        assert stub_api.opened == []  # only 5 jobs, below trigger
