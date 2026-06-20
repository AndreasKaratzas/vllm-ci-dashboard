from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"


class TestReadyTicketsSnapshot:
    @pytest.fixture
    def ready(self):
        path = DATA / "vllm" / "ci" / "ready_tickets.json"
        if not path.exists():
            pytest.skip("ready_tickets.json not collected yet")
        return json.loads(path.read_text())

    @pytest.fixture
    def ready_state(self):
        path = DATA / "vllm" / "ci" / "ready_tickets_state.json"
        if not path.exists():
            pytest.skip("ready_tickets_state.json not collected yet")
        return json.loads(path.read_text())

    def test_single_master_snapshot_uses_static_tracker_issue(self, ready):
        if ready.get("issue_mode") != "single_master":
            pytest.skip("ready tickets not in single-master mode")
        master = ready.get("master_issue") or {}
        assert master.get("number") == 40554
        assert str(master.get("url", "")).endswith("/issues/40554")
        assert "Static dashboard tracker for current CI failures" in str(master.get("title", ""))

    def test_single_master_comment_points_at_static_tracker(self, ready):
        if ready.get("issue_mode") != "single_master":
            pytest.skip("ready tickets not in single-master mode")
        comment = ready.get("master_issue_comment") or {}
        assert "/issues/40554#issuecomment-" in str(comment.get("url", ""))

    def test_single_master_state_matches_static_tracker(self, ready_state):
        master = ready_state.get("master_issue") or {}
        assert master.get("issue_number") == 40554
        assert str(master.get("issue_url", "")).endswith("/issues/40554")
        assert "/issues/40554#issuecomment-" in str(master.get("comment_url", ""))
