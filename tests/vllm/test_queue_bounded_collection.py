from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from vllm import collect_queue_snapshot as queue


NOW = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
PRIOR = "2026-09-01T07:00:00Z"


def _prior_overlay(path: Path) -> None:
    path.write_text(json.dumps({
        "ts": PRIOR,
        "zombie_threshold_min": 240,
        "pending": [{
            "name": "retained pending",
            "queue": "amd_mi250_1",
            "state": "scheduled",
            "wait_min": 4.0,
            "url": "https://buildkite.com/vllm/amd-ci/builds/1",
        }],
        "running": [],
    }))


def _metrics() -> dict[str, dict]:
    return {
        "amd_mi250_1": {
            "graphql_id": "queue-id",
            "counts_available": True,
            "waiting": 7,
            "running": 3,
            "connected_agents": 4,
            "metrics_ts": "2026-09-01T07:59:58Z",
            "official_wait": {"min": 1.0, "p50": 2.0, "p95": 5.0, "max": 8.0},
        }
    }


def _fixed_clock():
    patched = patch("vllm.collect_queue_snapshot.datetime")
    clock = patched.start()
    clock.now.return_value = NOW
    clock.fromisoformat = datetime.fromisoformat
    return patched


def test_graphql_redirect_cannot_create_an_unbudgeted_transport(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class Response:
        status_code = 302

        def raise_for_status(self):
            raise AssertionError("redirect must be rejected before generic status handling")

    def post(*args, **kwargs):
        observed.update(kwargs)
        return Response()

    monkeypatch.setattr(queue.requests, "post", post)
    with pytest.raises(RuntimeError, match="redirected"):
        queue.bk_graphql("query { organization { id } }", "token")
    assert observed["allow_redirects"] is False


def test_metrics_pagination_stops_at_the_two_start_allowance(monkeypatch) -> None:
    calls = 0

    def graphql(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {
            "organization": {
                "cluster": {
                    "queues": {
                        "edges": [],
                        "pageInfo": {"hasNextPage": True, "endCursor": f"c{calls}"},
                    }
                }
            }
        }

    monkeypatch.setattr(queue, "bk_graphql", graphql)
    telemetry: dict[str, int] = {}
    with pytest.raises(queue.QueuePaginationLimitError, match="after 2 pages"):
        queue.fetch_cluster_queue_metrics(
            "token", max_pages=2, request_telemetry=telemetry
        )
    assert calls == 2
    assert telemetry == {"metrics": 2}


def test_detail_pagination_stops_at_twelve_and_never_returns_partial(monkeypatch) -> None:
    calls = 0

    def graphql(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {
            "organization": {
                "jobs": {
                    "edges": [],
                    "pageInfo": {"hasNextPage": True, "endCursor": f"c{calls}"},
                }
            }
        }

    monkeypatch.setattr(queue, "bk_graphql", graphql)
    telemetry: dict[str, int] = {}
    with pytest.raises(queue.QueuePaginationLimitError, match="after 12 pages"):
        queue._fetch_graphql_jobs(
            "token",
            query=queue.GRAPHQL_ACTIVE_JOBS_Q,
            variables={"org": "vllm", "states": [], "first": 100},
            max_pages=12,
            request_telemetry=telemetry,
        )
    assert calls == 12
    assert telemetry == {"details": 12}


def test_metrics_only_keeps_current_counts_and_old_complete_details(
    monkeypatch, tmp_path: Path
) -> None:
    output = tmp_path / "queue_timeseries.jsonl"
    jobs_path = tmp_path / "queue_jobs.json"
    _prior_overlay(jobs_path)
    monkeypatch.setattr(queue, "OUTPUT", output)
    monkeypatch.setattr(
        queue,
        "fetch_cluster_queue_metrics",
        lambda token, **kwargs: _metrics(),
    )
    monkeypatch.setattr(
        queue,
        "fetch_active_cluster_jobs",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("metrics-only refresh queried active jobs")
        ),
    )

    fixed = _fixed_clock()
    try:
        snapshot = queue.collect_snapshot(
            "token",
            refresh_details=False,
            metrics_max_pages=2,
            details_max_pages=12,
            bounded_workflow_mode=True,
        )
    finally:
        fixed.stop()

    retained = json.loads(jobs_path.read_text())
    assert snapshot["metrics_observed_at"] == "2026-09-01T08:00:00Z"
    assert snapshot["details_observed_at"] == PRIOR
    assert snapshot["details_status"] == "retained_not_refreshed"
    assert snapshot["queues"]["amd_mi250_1"]["waiting"] == 7
    assert snapshot["queues"]["amd_mi250_1"]["sample_wait"]["available"] is False
    assert snapshot["queues"]["amd_mi250_1"]["p95_wait_source"] == "official_wait"
    assert retained["ts"] == PRIOR
    assert retained["details_observed_at"] == PRIOR
    assert retained["pending"][0]["name"] == "retained pending"


def test_page_cap_retains_overlay_with_explicit_status(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "queue_timeseries.jsonl"
    jobs_path = tmp_path / "queue_jobs.json"
    _prior_overlay(jobs_path)
    monkeypatch.setattr(queue, "OUTPUT", output)
    monkeypatch.setattr(
        queue,
        "fetch_cluster_queue_metrics",
        lambda token, **kwargs: _metrics(),
    )
    monkeypatch.setattr(
        queue,
        "fetch_active_cluster_jobs",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            queue.QueuePaginationLimitError("after 12 pages")
        ),
    )

    fixed = _fixed_clock()
    try:
        snapshot = queue.collect_snapshot(
            "token",
            refresh_details=True,
            metrics_max_pages=2,
            details_max_pages=12,
            bounded_workflow_mode=True,
        )
    finally:
        fixed.stop()

    retained = json.loads(jobs_path.read_text())
    assert snapshot["details_status"] == "retained_due_to_page_cap"
    assert snapshot["details_refresh_attempted_at"] == "2026-09-01T08:00:00Z"
    assert snapshot["details_observed_at"] == PRIOR
    assert retained["details_status"] == "retained_due_to_page_cap"
    assert retained["ts"] == PRIOR
    assert retained["pending"][0]["name"] == "retained pending"


def test_complete_detail_refresh_advances_only_after_exhaustion_is_proven(
    monkeypatch, tmp_path: Path
) -> None:
    output = tmp_path / "queue_timeseries.jsonl"
    jobs_path = tmp_path / "queue_jobs.json"
    _prior_overlay(jobs_path)
    monkeypatch.setattr(queue, "OUTPUT", output)
    monkeypatch.setattr(
        queue,
        "fetch_cluster_queue_metrics",
        lambda token, **kwargs: _metrics(),
    )
    monkeypatch.setattr(
        queue,
        "fetch_active_cluster_jobs",
        lambda *args, **kwargs: [],
    )

    fixed = _fixed_clock()
    try:
        snapshot = queue.collect_snapshot(
            "token",
            refresh_details=True,
            metrics_max_pages=2,
            details_max_pages=12,
            bounded_workflow_mode=True,
        )
    finally:
        fixed.stop()

    current = json.loads(jobs_path.read_text())
    assert snapshot["details_status"] == "current"
    assert snapshot["details_observed_at"] == "2026-09-01T08:00:00Z"
    assert current["ts"] == "2026-09-01T08:00:00Z"
    assert current["details_observed_at"] == current["ts"]
    assert current["pending"] == []


def test_metrics_failure_never_relabels_prior_data(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "queue_timeseries.jsonl"
    jobs_path = tmp_path / "queue_jobs.json"
    _prior_overlay(jobs_path)
    before = jobs_path.read_bytes()
    monkeypatch.setattr(queue, "OUTPUT", output)
    monkeypatch.setattr(
        queue,
        "fetch_cluster_queue_metrics",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("metrics unavailable")),
    )

    with pytest.raises(RuntimeError, match="refusing to relabel old metrics"):
        queue.collect_snapshot(
            "token",
            refresh_details=False,
            metrics_max_pages=2,
            details_max_pages=12,
            bounded_workflow_mode=True,
        )
    assert jobs_path.read_bytes() == before
