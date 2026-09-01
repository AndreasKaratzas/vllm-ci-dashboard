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
import os
from pathlib import Path

import pytest

from vllm import omni_surge_watcher as osw
from vllm.constants import (
    OMNI_SURGE_FLOOR_TRIGGER,
    OMNI_SURGE_HEALTHY_RATIO,
    OMNI_SURGE_MULTIPLIER,
)


def test_issue_writes_are_restricted_to_dashboard_repo():
    osw._validate_target_repo(osw.DASHBOARD_REPO)
    with pytest.raises(RuntimeError, match="restricted"):
        osw._validate_target_repo("vllm-project/vllm")


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
        self.adopted = []
        self.discovered = []
        self.discovery_calls = 0
        self.open_succeeds = True
        self.close_succeeds = True
        self.comment_succeeds = True
        self.assign_succeeds = True
        self.adopt_succeeds = True
        self.mutations = []
        self._next = 500
        # A stub YAML where every `label:` row counts as one group.
        self._yaml_text = "\n".join([f"- label: test-{i}" for i in range(yaml_groups)])

    def fetch_yaml(self, path):
        return self._yaml_text

    def open_issue(self, token, repo, waiting, by_queue, heuristic, snap_ts, run_url):
        self.mutations.append(("open", waiting))
        if not self.open_succeeds:
            return None
        num = self._next
        self._next += 1
        owner = repo.split("/", 1)[0]
        body = (
            f"GitHub assignee: {owner}.\n\n"
            f"Auto-opened by `omni_surge_watcher.py` from {run_url}. Will auto-close once the "
            f"waiting count drops to {heuristic['healthy']}.\n"
        )
        self.opened.append((waiting, heuristic["trigger"], num, body))
        return num

    def close(self, token, repo, number):
        self.mutations.append(("close", number))
        if not self.close_succeeds:
            return False
        self.closed.append(number)
        return True

    def comment(self, token, repo, number, body):
        self.mutations.append(("comment", number))
        if not self.comment_succeeds:
            return False
        self.commented.append((number, body))
        return True

    def assign(self, token, repo, number):
        self.mutations.append(("assign", number))
        if not self.assign_succeeds:
            return False
        self.assigned.append(number)
        return True

    def list_owned(self, token, repo, **_kwargs):
        self.discovery_calls += 1
        if self.discovered is None:
            return None
        return [dict(issue) for issue in self.discovered]

    def adopt(self, token, repo, issue):
        self.mutations.append(("adopt", issue["number"]))
        if not self.adopt_succeeds:
            return False
        self.adopted.append(issue["number"])
        issue["legacy"] = False
        issue["body"] = f"{osw.OWNERSHIP_MARKER}\n{issue['body']}"
        return True


@pytest.fixture
def stub_api(monkeypatch):
    api = _StubbedApi()
    # Pin OMNI_YAML_PATHS to a single path during tests — the production
    # tuple lists two additive pipeline files and our stub returns the same
    # text for every path, which would double ``total_groups`` and
    # silently push ``trigger`` past the floor. Forcing a single path
    # keeps the group counts in this fixture equal to what each test
    # declares in ``_yaml_text``.
    monkeypatch.setattr(
        osw, "OMNI_YAML_PATHS", (".buildkite/amd/test-amd-ready.yml",), raising=False
    )
    monkeypatch.setattr(osw, "_fetch_yaml", api.fetch_yaml)
    monkeypatch.setattr(osw, "_open_issue", api.open_issue)
    monkeypatch.setattr(osw, "_close", api.close)
    monkeypatch.setattr(osw, "_comment", api.comment)
    monkeypatch.setattr(osw, "_ensure_owner_assigned", api.assign)
    monkeypatch.setattr(osw, "_list_owned_open_issues", api.list_owned)
    monkeypatch.setattr(osw, "_adopt_legacy_issue", api.adopt)
    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    monkeypatch.setenv("GITHUB_REPOSITORY", "AndreasKaratzas/vllm-ci-dashboard")
    return api


def _owned_issue(number: int, *, legacy: bool = False) -> dict:
    return {
        "number": number,
        "body": "legacy body" if legacy else osw.OWNERSHIP_MARKER,
        "created_at": f"2026-04-18T{number % 24:02d}:00:00Z",
        "labels": [{"name": name} for name in osw.OWNED_LABELS],
        "legacy": legacy,
    }


def _legacy_raw_issue(number: int = 321, waiting: int = 40, trigger: int = 30) -> dict:
    return {
        "number": number,
        "title": f"Omni CI surge: {waiting} jobs waiting (threshold {trigger})",
        "body": (
            "## Omni workload surge\n\n"
            f"**{waiting}** Omni-classified jobs are waiting across AMD queues "
            "as of `2026-04-18T10:00:00Z` — at or above the "
            f"dynamic trigger of **{trigger}**.\n\n"
            "GitHub assignee: AndreasKaratzas.\n\n"
            "Auto-opened by `omni_surge_watcher.py` from "
            "https://github.com/AndreasKaratzas/vllm-ci-dashboard/actions/runs/1. "
            "Will auto-close once the waiting count drops to 21.\n"
        ),
        "labels": [
            {"name": osw.LABEL},
            {"name": osw.AUTOMATED_LABEL},
            {"name": osw.WORKSTREAM_LABEL},
        ],
        "created_at": "2026-04-18T10:00:00Z",
    }


def test_tracked_issue_lookup_uses_direct_number_and_zero_list_calls(monkeypatch):
    calls = []
    issue = {
        "number": 568,
        "state": "open",
        "title": "human edited",
        "body": osw.OWNERSHIP_MARKER,
        "labels": [],
        "created_at": "2026-04-18T10:00:00Z",
    }

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return issue

    def get(url, *, headers, timeout):
        calls.append(url)
        return Response()

    monkeypatch.setattr(osw.requests, "get", get)

    rows = osw._list_owned_open_issues(
        "token",
        osw.DASHBOARD_REPO,
        include_recovery=False,
        tracked_numbers=(568,),
    )

    assert [row["number"] for row in rows] == [568]
    assert calls == [f"{osw.GH_API}/repos/{osw.DASHBOARD_REPO}/issues/568"]


class TestIssueOwnershipAndDiscovery:
    def test_exact_marker_line_is_authoritative(self):
        issue = {
            "number": 7,
            "title": "human-edited title",
            "body": f"intro\n{osw.OWNERSHIP_MARKER}\nmore",
            "labels": [],
        }
        assert osw._owned_open_issue(issue) == {
            "number": 7,
            "body": issue["body"],
            "created_at": "",
            "labels": [],
            "legacy": False,
        }

        issue["body"] = f"quoted {osw.OWNERSHIP_MARKER} text"
        assert osw._owned_open_issue(issue) is None

    def test_pull_request_is_never_owned_even_with_marker(self):
        issue = {
            "number": 8,
            "body": osw.OWNERSHIP_MARKER,
            "pull_request": {"url": "https://example.invalid"},
        }
        assert osw._owned_open_issue(issue) is None

    def test_legacy_adoption_requires_labels_body_and_title(self):
        issue = _legacy_raw_issue()
        normalized = osw._owned_open_issue(issue)
        assert normalized is not None and normalized["legacy"] is True

        for label in osw.OWNED_LABELS:
            candidate = _legacy_raw_issue()
            candidate["labels"] = [
                value
                for value in candidate["labels"]
                if value["name"] != label
            ]
            assert osw._owned_open_issue(candidate) is None

        candidate = _legacy_raw_issue()
        candidate["title"] = "Omni CI surge"
        assert osw._owned_open_issue(candidate) is None

        candidate = _legacy_raw_issue()
        candidate["body"] = candidate["body"].replace(
            "Auto-opened by `omni_surge_watcher.py`", "Opened manually"
        )
        assert osw._owned_open_issue(candidate) is None

        candidate = _legacy_raw_issue()
        candidate["body"] = candidate["body"].replace("**40**", "**41**")
        assert osw._owned_open_issue(candidate) is None

    def test_owner_label_exact_match_skips_recent_recovery(self, monkeypatch):
        exact = {
            "number": 101,
            "title": "edited",
            "body": osw.OWNERSHIP_MARKER,
            "labels": [],
        }
        calls = []

        class Response:
            status_code = 200

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        def get(url, *, headers, params, timeout):
            calls.append(params)
            return Response([exact])

        monkeypatch.setattr(osw.requests, "get", get)

        discovered = osw._list_owned_open_issues("token", osw.DASHBOARD_REPO)

        assert [issue["number"] for issue in discovered] == [101]
        assert len(calls) == 1
        assert calls[0]["labels"] == osw.LABEL

    def test_recent_recovery_preserves_strict_legacy_568_adoption(
        self, monkeypatch
    ):
        calls = []

        class Response:
            status_code = 200

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        def get(url, *, headers, params, timeout):
            calls.append(params)
            if params["labels"] == osw.LABEL:
                return Response([])
            return Response([_legacy_raw_issue(number=568)])

        monkeypatch.setattr(osw.requests, "get", get)

        discovered = osw._list_owned_open_issues("token", osw.DASHBOARD_REPO)

        assert [issue["number"] for issue in discovered] == [568]
        assert discovered[0]["legacy"] is True
        assert [call["labels"] for call in calls] == [
            osw.LABEL,
            f"{osw.AUTOMATED_LABEL},{osw.WORKSTREAM_LABEL}",
        ]

    def test_new_issue_carries_exact_marker_and_managed_labels(self, monkeypatch):
        captured = {}

        class Response:
            status_code = 201
            text = ""

            @staticmethod
            def json():
                return {"number": 910}

        def post(url, *, headers, json, timeout):
            captured.update(json)
            return Response()

        monkeypatch.setattr(osw.requests, "post", post)
        heuristic = {
            "trigger": 30,
            "healthy": 21,
            "total_groups": 10,
            "dynamic_component": 13,
            "pool_distribution": {"amd": 10},
        }

        number = osw._open_issue(
            "token",
            osw.DASHBOARD_REPO,
            40,
            {"amd_mi300_1": 40},
            heuristic,
            "2026-04-18T10:00:00Z",
            "https://example.invalid/run",
        )

        assert number == 910
        assert captured["body"].splitlines()[0] == osw.OWNERSHIP_MARKER
        assert set(captured["labels"]) == set(osw.OWNED_LABELS)

    def test_watcher_has_no_buildkite_api_surface(self):
        source = Path(osw.__file__).read_text(encoding="utf-8")
        assert "api.buildkite.com" not in source
        assert "BUILDKITE_TOKEN" not in source
        assert "BUILDKITE_API_TOKEN" not in source


class TestAtomicState:
    def test_replace_failure_preserves_prior_state_and_cleans_temp(
        self, isolated_paths, monkeypatch
    ):
        _, state, _ = isolated_paths
        original = b'{"open": 77, "last_value": 40}\n'
        state.write_bytes(original)

        def fail_replace(source, destination):
            raise OSError("replace failed")

        monkeypatch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="replace failed"):
            osw._write_state({"open": None, "last_value": 0})

        assert state.read_bytes() == original
        assert list(state.parent.glob(f".{state.name}.*.tmp")) == []


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
        _, _, heuristic_path = isolated_paths
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
        _, _, heuristic_path = isolated_paths
        monkeypatch.setattr(
            osw,
            "OMNI_YAML_PATHS",
            ("ready.yml", "merge.yml"),
            raising=False,
        )
        monkeypatch.setattr(
            osw,
            "_read_last_snapshot",
            lambda: {"ts": "2026-07-27T12:00:00Z", "queues": {}},
        )
        monkeypatch.setattr(
            osw,
            "_fetch_yaml",
            lambda path: f"steps:\n  - label: {path}\n",
        )
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

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
        assert "GitHub assignee: AndreasKaratzas." in stub_api.opened[0][3]
        assert "@AndreasKaratzas" not in stub_api.opened[0][3]
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
        stub_api.discovered = [_owned_issue(999)]
        stub_api._yaml_text = "\n".join(f"- label: t{i}" for i in range(10))
        _write_snapshot(snaps, {"amd_mi250_1": {"waiting_by_workload": {"omni": 50}}})
        rc = osw.run()
        assert rc == 0
        assert stub_api.opened == []  # don't reopen an already-open tracker

    def test_closes_when_waiting_drops_to_healthy(self, isolated_paths, stub_api):
        snaps, state, _ = isolated_paths
        state.write_text(json.dumps({"open": 999, "last_value": 50}))
        stub_api.discovered = [_owned_issue(999)]
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
        stub_api.discovered = [_owned_issue(999)]
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

    def test_recovers_lost_state_from_owned_issue(self, isolated_paths, stub_api):
        snapshots, state, _ = isolated_paths
        stub_api.discovered = [_owned_issue(740)]
        stub_api._yaml_text = "\n".join(f"- label: t{i}" for i in range(10))
        _write_snapshot(
            snapshots,
            {"amd_mi250_1": {"waiting_by_workload": {"omni": 50}}},
        )

        assert osw.run() == 0

        assert stub_api.discovery_calls == 1
        assert stub_api.opened == []
        assert stub_api.assigned == [740]
        assert json.loads(state.read_text())["open"] == 740

    def test_adopts_strict_legacy_issue_before_recording_it(
        self, isolated_paths, stub_api
    ):
        snapshots, state, _ = isolated_paths
        stub_api.discovered = [_owned_issue(741, legacy=True)]
        stub_api._yaml_text = "\n".join(f"- label: t{i}" for i in range(10))
        _write_snapshot(
            snapshots,
            {"amd_mi250_1": {"waiting_by_workload": {"omni": 50}}},
        )

        assert osw.run() == 0

        assert stub_api.adopted == [741]
        assert stub_api.assigned == [741]
        assert stub_api.mutations.index(("adopt", 741)) < stub_api.mutations.index(
            ("assign", 741)
        )
        assert json.loads(state.read_text())["open"] == 741

    def test_deduplicates_after_full_recovery_before_lifecycle_mutation(
        self, isolated_paths, stub_api
    ):
        snapshots, state, _ = isolated_paths
        stub_api.discovered = [_owned_issue(80), _owned_issue(81)]
        stub_api._yaml_text = "\n".join(f"- label: t{i}" for i in range(10))
        _write_snapshot(
            snapshots,
            {"amd_mi250_1": {"waiting_by_workload": {"omni": 50}}},
        )

        assert osw.run() == 0

        assert stub_api.commented[0][0] == 81
        assert stub_api.closed == [81]
        assert stub_api.mutations.index(("close", 81)) < stub_api.mutations.index(
            ("assign", 80)
        )
        assert stub_api.opened == []
        assert json.loads(state.read_text())["open"] == 80

    def test_discovery_failure_is_fail_closed_and_preserves_state(
        self, isolated_paths, stub_api
    ):
        snapshots, state, _ = isolated_paths
        original = b'{"open": 77, "last_value": 12}\n'
        state.write_bytes(original)
        stub_api.discovered = None
        stub_api._yaml_text = "\n".join(f"- label: t{i}" for i in range(10))
        _write_snapshot(
            snapshots,
            {"amd_mi250_1": {"waiting_by_workload": {"omni": 50}}},
        )

        assert osw.run() == 1

        assert state.read_bytes() == original
        assert stub_api.mutations == []

    def test_open_failure_does_not_advance_ledger(self, isolated_paths, stub_api):
        snapshots, state, _ = isolated_paths
        stub_api.open_succeeds = False
        stub_api._yaml_text = "\n".join(f"- label: t{i}" for i in range(10))
        _write_snapshot(
            snapshots,
            {"amd_mi250_1": {"waiting_by_workload": {"omni": 50}}},
        )

        assert osw.run() == 1

        assert not state.exists()
        assert stub_api.mutations == [("open", 50)]

    def test_close_failure_keeps_open_ledger(self, isolated_paths, stub_api):
        snapshots, state, _ = isolated_paths
        original = b'{"open": 999, "last_value": 50}\n'
        state.write_bytes(original)
        stub_api.discovered = [_owned_issue(999)]
        stub_api.close_succeeds = False
        _write_snapshot(
            snapshots,
            {"amd_mi250_1": {"waiting_by_workload": {"omni": 10}}},
        )

        assert osw.run() == 1

        assert state.read_bytes() == original
        assert stub_api.commented[0][0] == 999
        assert stub_api.closed == []

    def test_comment_failure_prevents_close_and_ledger_change(
        self, isolated_paths, stub_api
    ):
        snapshots, state, _ = isolated_paths
        original = b'{"open": 999, "last_value": 50}\n'
        state.write_bytes(original)
        stub_api.discovered = [_owned_issue(999)]
        stub_api.comment_succeeds = False
        _write_snapshot(
            snapshots,
            {"amd_mi250_1": {"waiting_by_workload": {"omni": 10}}},
        )

        assert osw.run() == 1

        assert state.read_bytes() == original
        assert stub_api.closed == []

    def test_recovery_assignment_failure_does_not_adopt_local_state(
        self, isolated_paths, stub_api
    ):
        snapshots, state, _ = isolated_paths
        stub_api.discovered = [_owned_issue(444)]
        stub_api.assign_succeeds = False
        stub_api._yaml_text = "\n".join(f"- label: t{i}" for i in range(10))
        _write_snapshot(
            snapshots,
            {"amd_mi250_1": {"waiting_by_workload": {"omni": 50}}},
        )

        assert osw.run() == 1

        assert not state.exists()
        assert stub_api.opened == []

    def test_duplicate_close_failure_preserves_prior_ledger(
        self, isolated_paths, stub_api
    ):
        snapshots, state, _ = isolated_paths
        original = b'{"open": 80, "last_value": 50}\n'
        state.write_bytes(original)
        stub_api.discovered = [_owned_issue(80), _owned_issue(81)]
        stub_api.close_succeeds = False
        stub_api._yaml_text = "\n".join(f"- label: t{i}" for i in range(10))
        _write_snapshot(
            snapshots,
            {"amd_mi250_1": {"waiting_by_workload": {"omni": 50}}},
        )

        assert osw.run() == 1

        assert state.read_bytes() == original
        assert stub_api.assigned == []

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

    def test_yaml_fetch_failure_exposes_floor_without_mutating(
        self, isolated_paths, stub_api, monkeypatch
    ):
        snaps, state, heur = isolated_paths
        monkeypatch.setattr(osw, "_fetch_yaml", lambda path: None)
        _write_snapshot(snaps, {"amd_mi250_1": {"waiting_by_workload": {"omni": 40}}})
        rc = osw.run()
        assert rc == 0
        # Without source evidence, the floor remains visible but cannot drive
        # an issue open/close mutation.
        assert stub_api.opened == []
        assert stub_api.closed == []
        info = json.loads(heur.read_text())
        assert info["fallback_floor_used"] is True
        assert info["total_groups"] == 0
        assert info["source_status"] == "unavailable"
        assert info["mutations_suppressed"] is True
        assert info["yaml_paths_failed"] == [".buildkite/amd/test-amd-ready.yml"]
        assert json.loads(state.read_text())["last_trigger"] == OMNI_SURGE_FLOOR_TRIGGER

    def test_yaml_fetch_failure_retains_last_good_heuristic_without_mutating(
        self, isolated_paths, stub_api, monkeypatch
    ):
        snaps, state, heur = isolated_paths
        last_trigger, last_healthy, last_info = osw._compute_trigger(
            [{"label": f"t{i}", "agent_pool": "amd"} for i in range(40)]
        )
        last_info.update({
            "yaml_paths_fetched": [".buildkite/amd/test-amd-ready.yml"],
            "fallback_floor_used": False,
        })
        heur.write_text(json.dumps(last_info))
        monkeypatch.setattr(osw, "_fetch_yaml", lambda path: None)
        _write_snapshot(
            snaps,
            {"amd_mi250_1": {"waiting_by_workload": {"omni": last_trigger + 10}}},
        )

        rc = osw.run()

        assert rc == 0
        assert stub_api.opened == []
        assert stub_api.closed == []
        info = json.loads(heur.read_text())
        assert info["total_groups"] == 40
        assert info["trigger"] == last_trigger
        assert info["healthy"] == last_healthy
        assert info["source_status"] == "last_known_good"
        assert info["using_last_known_good"] is True
        assert info["fallback_floor_used"] is False
        assert info["mutations_suppressed"] is True
        persisted = json.loads(state.read_text())
        assert persisted["last_trigger"] == last_trigger
        assert persisted["open"] is None

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


class TestHeuristicOnly:
    @staticmethod
    def _forbidden(*args, **kwargs):
        pytest.fail("heuristic-only mode touched issue automation")

    def _forbid_issue_automation(self, monkeypatch):
        for name in (
            "_validate_target_repo",
            "_read_last_snapshot",
            "_read_state",
            "_write_state",
            "_open_issue",
            "_close",
            "_comment",
            "_ensure_owner_assigned",
        ):
            monkeypatch.setattr(osw, name, self._forbidden)

    def test_cli_refreshes_only_heuristic(self, isolated_paths, monkeypatch):
        snapshots, state, heuristic_path = isolated_paths
        self._forbid_issue_automation(monkeypatch)
        monkeypatch.setattr(osw, "OMNI_YAML_PATHS", ("ready.yml",), raising=False)
        monkeypatch.setattr(
            osw,
            "_fetch_yaml",
            lambda path: "steps:\n  - label: ready-test\n",
        )
        # Heuristic refresh does not target a repository, so an unrelated repo
        # value must not enter the issue-automation guard.
        monkeypatch.setenv("GITHUB_REPOSITORY", "example/unrelated")

        assert osw.main(["--heuristic-only"]) == 0

        assert not snapshots.exists()
        assert not state.exists()
        payload = json.loads(heuristic_path.read_text())
        assert payload["source_status"] == "fresh"
        assert payload["yaml_paths_fetched"] == ["ready.yml"]
        assert payload["last_successful_at"] == payload["generated_at"]

    def test_missing_sources_fail_without_last_known_good(
        self, isolated_paths, monkeypatch
    ):
        _, state, heuristic_path = isolated_paths
        self._forbid_issue_automation(monkeypatch)
        monkeypatch.setattr(osw, "OMNI_YAML_PATHS", ("ready.yml",), raising=False)
        monkeypatch.setattr(osw, "_fetch_yaml", lambda path: None)

        assert osw.run(heuristic_only=True) == 1

        assert not state.exists()
        payload = json.loads(heuristic_path.read_text())
        assert payload["source_status"] == "unavailable"
        assert payload["fallback_floor_used"] is True
        assert payload["mutations_suppressed"] is True

    def test_failed_refresh_retains_diagnostic_but_rejects_last_known_good(
        self, isolated_paths, monkeypatch
    ):
        _, state, heuristic_path = isolated_paths
        self._forbid_issue_automation(monkeypatch)
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

        assert osw.run(heuristic_only=True) == 1

        assert not state.exists()
        payload = json.loads(heuristic_path.read_text())
        assert payload["source_status"] == "last_known_good"
        assert payload["using_last_known_good"] is True
        assert payload["mutations_suppressed"] is True
        assert payload["last_successful_at"] == "2026-08-11T10:00:00Z"
        assert payload["last_successful_yaml_paths"] == ["ready.yml"]

    def test_failed_refresh_rejects_undated_last_known_good(
        self, isolated_paths, monkeypatch
    ):
        _, _, heuristic_path = isolated_paths
        self._forbid_issue_automation(monkeypatch)
        monkeypatch.setattr(osw, "OMNI_YAML_PATHS", ("ready.yml",), raising=False)
        monkeypatch.setattr(osw, "_fetch_yaml", lambda path: None)
        _, _, last_good = osw._compute_trigger([{"label": "test", "agent_pool": "amd"}])
        last_good.update({
            "last_successful_at": "not-a-timestamp",
            "yaml_paths_fetched": ["ready.yml"],
            "fallback_floor_used": False,
        })
        heuristic_path.write_text(json.dumps(last_good))

        assert osw.run(heuristic_only=True) == 1


class TestIssuesOnly:
    def test_uses_selected_heuristic_without_refreshing_it(
        self, isolated_paths, stub_api, monkeypatch
    ):
        snapshots, state, heuristic_path = isolated_paths
        _write_snapshot(
            snapshots,
            {"amd_mi250_1": {"waiting_by_workload": {"omni": 40}}},
        )
        _, _, heuristic = osw._compute_trigger(
            [{"label": f"test-{index}", "agent_pool": "amd"} for index in range(10)]
        )
        now = osw.datetime.now(osw.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        heuristic.update({
            "generated_at": now,
            "last_successful_at": now,
            "yaml_paths_configured": ["ready.yml"],
            "yaml_paths_fetched": ["ready.yml"],
            "yaml_paths_failed": [],
            "source_status": "fresh",
            "fallback_floor_used": False,
            "using_last_known_good": False,
            "mutations_suppressed": False,
        })
        heuristic_path.write_text(json.dumps(heuristic, sort_keys=True))
        original = heuristic_path.read_bytes()
        monkeypatch.setattr(
            osw,
            "_refresh_heuristic",
            lambda: pytest.fail("issues-only mode refreshed heuristic evidence"),
        )

        assert osw.main(["--issues-only"]) == 0

        assert heuristic_path.read_bytes() == original
        assert len(stub_api.opened) == 1
        assert stub_api.opened[0][0] == 40
        assert json.loads(state.read_text())["open"] == stub_api.opened[0][2]

    def test_rejects_unselected_last_known_good(
        self, isolated_paths, stub_api, monkeypatch
    ):
        snapshots, state, heuristic_path = isolated_paths
        _write_snapshot(snapshots, {})
        _, _, heuristic = osw._compute_trigger([{"label": "test"}])
        heuristic.update({
            "generated_at": "2026-08-12T20:00:00Z",
            "last_successful_at": "2026-08-11T20:00:00Z",
            "yaml_paths_configured": ["ready.yml"],
            "yaml_paths_fetched": [],
            "yaml_paths_failed": ["ready.yml"],
            "source_status": "last_known_good",
            "fallback_floor_used": False,
            "using_last_known_good": True,
            "mutations_suppressed": True,
        })
        heuristic_path.write_text(json.dumps(heuristic))
        monkeypatch.setattr(
            osw,
            "_refresh_heuristic",
            lambda: pytest.fail("issues-only mode refreshed heuristic evidence"),
        )

        assert osw.run(issues_only=True) == 1
        assert not state.exists()
        assert stub_api.opened == []

    def test_cli_modes_are_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            osw.main(["--heuristic-only", "--issues-only"])
