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

    def test_single_master_snapshot_uses_dashboard_tracker_issue(self, ready):
        if ready.get("issue_mode") != "single_master":
            pytest.skip("ready tickets not in single-master mode")
        master = ready.get("master_issue") or {}
        assert master.get("repo") == "AndreasKaratzas/vllm-ci-dashboard"
        assert master.get("number") == 255
        assert str(master.get("url", "")).endswith("/issues/255")
        assert master.get("title") == "[AMD][CI Failure][Tracker] Current nightly failures"

    def test_single_master_comment_points_at_dashboard_tracker_after_live_sync(self, ready):
        if ready.get("issue_mode") != "single_master":
            pytest.skip("ready tickets not in single-master mode")
        comment = ready.get("master_issue_comment")
        if ready.get("mode") != "live":
            assert comment is None or "/issues/255#issuecomment-" in str(comment.get("url", ""))
            return
        assert int(comment.get("id") or 0) > 0
        assert "/issues/255#issuecomment-" in str(comment.get("url", ""))

    def test_single_master_state_matches_dashboard_tracker(self, ready_state):
        master = ready_state.get("master_issue") or {}
        assert master.get("issue_number") == 255
        assert str(master.get("issue_url", "")).endswith("/issues/255")
        comment_id = master.get("comment_id")
        comment_url = master.get("comment_url")
        if comment_id is None:
            assert comment_url is None
        else:
            assert int(comment_id) > 0
            assert str(comment_url).endswith(f"/issues/255#issuecomment-{comment_id}")

    def test_snapshot_has_no_per_group_issue_drafts(self, ready):
        master = ready.get("master_issue") or {}
        assert "master_issue_body" not in ready
        for ticket in ready.get("tickets", []):
            assert "body" not in ticket
            assert "labels" not in ticket
            assert ticket.get("issue_number")
            assert ticket.get("issue_url")
            if ticket.get("project_status") == "Tracked in dashboard tracker":
                assert ticket["issue_number"] == master.get("number")
