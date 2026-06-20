"""Unit tests for scripts/vllm/collect_queue_snapshot.py.

Covers wait-time summary math, queue-metrics seeding, the legacy fallback,
and the ``queue_jobs.json`` side effect the dashboard depends on.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

from vllm import collect_queue_snapshot as cqs


class TestWaitSummary:
    def test_empty_returns_zero_block(self):
        assert cqs._wait_summary([]) == {
            "p50_wait": 0,
            "p75_wait": 0,
            "p90_wait": 0,
            "p95_wait": 0,
            "p99_wait": 0,
            "max_wait": 0,
            "avg_wait": 0,
        }

    def test_values_rounded_to_one_decimal(self):
        out = cqs._wait_summary([1.0, 2.0, 3.0, 4.0, 5.0])
        assert out["max_wait"] == 5.0
        assert out["avg_wait"] == 3.0
        assert out["p50_wait"] == 3.0
        assert out["p90_wait"] == 5.0
        assert out["p95_wait"] == 5.0


class TestRewriteJobUrl:
    def test_hash_style_converted_to_step_canvas(self):
        url = "https://buildkite.com/vllm/amd-ci/builds/12345#deadbeef-1234-5678-90ab-cdef01234567"
        expected = (
            "https://buildkite.com/vllm/amd-ci/builds/12345/steps/canvas"
            "?jid=deadbeef-1234-5678-90ab-cdef01234567&tab=output"
        )
        assert cqs._rewrite_job_url(url) == expected

    def test_canonical_url_passthrough(self):
        url = "https://buildkite.com/vllm/amd-ci/builds/12345/jobs/abc"
        assert cqs._rewrite_job_url(url) == url

    def test_empty_returns_empty(self):
        assert cqs._rewrite_job_url("") == ""


class TestGraphqlQueueMetrics:
    def test_fetches_official_queue_wait_metrics(self, monkeypatch):
        def fake_graphql(query, token, variables):
            return {
                "organization": {
                    "cluster": {
                        "queues": {
                            "edges": [{
                                "node": {
                                    "id": "ClusterQueueID",
                                    "key": "amd_mi355_1",
                                    "uuid": "queue-uuid",
                                    "dispatchPaused": False,
                                    "metrics": {
                                        "timestamp": "2026-05-20T12:00:00Z",
                                        "connectedAgentsCount": 8,
                                        "waitingJobsCount": 3,
                                        "runningJobsCount": 5,
                                        "waitTimeSec": {
                                            "min": 60,
                                            "p50": 120,
                                            "p95": 900,
                                            "max": 1200,
                                        },
                                    },
                                }
                            }],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }

        monkeypatch.setattr(cqs, "bk_graphql", fake_graphql)

        metrics = cqs.fetch_cluster_queue_metrics("fake-token")

        assert metrics["amd_mi355_1"]["graphql_id"] == "ClusterQueueID"
        assert metrics["amd_mi355_1"]["wait_summary"]["p50_wait"] == 2.0
        assert metrics["amd_mi355_1"]["wait_summary"]["p95_wait"] == 15.0
        assert metrics["amd_mi355_1"]["wait_summary"]["max_wait"] == 20.0

    def test_fetches_jobs_by_cluster_queue_graphql_id(self, monkeypatch):
        calls = []

        def fake_graphql(query, token, variables):
            calls.append((query, variables))
            return {
                "organization": {
                    "jobs": {
                        "edges": [{
                            "node": {
                                "uuid": "deadbeef-1234-5678-90ab-cdef01234567",
                                "state": "SCHEDULED",
                                "label": "mi355_1: queue check",
                                "runnableAt": "2026-05-20T12:00:00Z",
                                "scheduledAt": "2026-05-20T12:00:00Z",
                                "createdAt": "2026-05-20T11:59:00Z",
                                "startedAt": None,
                                "agentQueryRules": [],
                                "clusterQueue": {"key": "amd_mi355_1"},
                                "build": {
                                    "number": 123,
                                    "branch": "main",
                                    "commit": "abcdef1234567890",
                                    "url": "https://buildkite.com/vllm/amd-ci/builds/123",
                                },
                                "pipeline": {"slug": "amd-ci"},
                            }
                        }],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }

        monkeypatch.setattr(cqs, "bk_graphql", fake_graphql)

        jobs = cqs.fetch_active_cluster_jobs("fake-token", {"amd_mi355_1": "ClusterQueueID"})

        assert calls[0][0] == cqs.GRAPHQL_QUEUE_JOBS_Q
        assert calls[0][1]["queue"] == "ClusterQueueID"
        assert jobs[0]["queue"] == "amd_mi355_1"
        assert jobs[0]["state"] == "SCHEDULED"


class TestHistoryPrune:
    def test_drops_pre_reset_and_old_schema_rows(self, tmp_path):
        path = tmp_path / "queue_timeseries.jsonl"
        path.write_text(
            json.dumps({
                "ts": "2026-04-20T22:00:00Z",
                "queues": {"amd_mi250_1": {"waiting": 1}},
                "total_waiting": 1,
                "total_running": 0,
            }) + "\n"
            + json.dumps({
                "ts": "2026-04-20T23:45:00Z",
                "queues": {"amd_mi250_1": {
                    "waiting": 0, "running": 0, "p50_wait": 0, "p75_wait": 0,
                    "p90_wait": 0, "p99_wait": 0, "max_wait": 0, "avg_wait": 0,
                }},
                "total_waiting": 0,
                "total_running": 0,
            }) + "\n"
            + json.dumps({
                "ts": "2026-04-20T23:46:00Z",
                "queues": {"amd_mi250_1": {
                    "waiting": 0, "running": 0, "p50_wait": 0, "p75_wait": 0,
                    "p90_wait": 0, "p95_wait": 0, "p99_wait": 0, "max_wait": 0, "avg_wait": 0,
                }},
                "total_waiting": 0,
                "total_running": 0,
                "sources": {"counts": "cluster_metrics"},
            }) + "\n"
        )

        total, kept = cqs.prune_history_file(path, now=datetime(2026, 4, 21, tzinfo=timezone.utc))
        assert total == 3
        assert kept == 1
        lines = [line for line in path.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["ts"] == "2026-04-20T23:46:00Z"

    def test_drops_rows_older_than_retention_window(self, tmp_path):
        path = tmp_path / "queue_timeseries.jsonl"

        def snapshot(ts: str) -> str:
            return json.dumps({
                "ts": ts,
                "queues": {"amd_mi250_1": {
                    "waiting": 0, "running": 0, "p50_wait": 0, "p75_wait": 0,
                    "p90_wait": 0, "p95_wait": 0, "p99_wait": 0, "max_wait": 0, "avg_wait": 0,
                }},
                "total_waiting": 0,
                "total_running": 0,
                "sources": {"counts": "cluster_metrics"},
            })

        path.write_text(
            snapshot("2026-05-17T00:00:00Z") + "\n"
            + snapshot("2026-05-20T00:00:00Z") + "\n"
        )

        total, kept = cqs.prune_history_file(path, now=datetime(2026, 6, 18, tzinfo=timezone.utc))
        assert total == 2
        assert kept == 1
        lines = [line for line in path.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["ts"] == "2026-05-20T00:00:00Z"


def _active_job(
    queue: str,
    state: str,
    *,
    runnable_at: str | None = None,
    scheduled_at: str | None = None,
    created_at: str | None = None,
    started_at: str | None = None,
    name: str = "mi250_1: foo",
    pipeline: str = "amd-ci",
    branch: str = "main",
    build: int = 100,
    commit: str = "abc123def456",
    build_url: str = "https://buildkite.com/vllm/amd-ci/builds/100",
) -> dict:
    return {
        "queue": queue,
        "state": state,
        "name": name,
        "job_uuid": "deadbeef-1234-5678-90ab-cdef01234567",
        "build_url": build_url,
        "pipeline": pipeline,
        "build": build,
        "branch": branch,
        "commit": commit[:12],
        "workload": cqs.classify_workload(pipeline, branch, queue),
        "fork_url": "",
        "source": "",
        "runnable_at": runnable_at,
        "scheduled_at": scheduled_at,
        "created_at": created_at,
        "started_at": started_at,
    }


class _FakeBk:
    """Inject canned Buildkite REST responses keyed by build state."""

    def __init__(self, running_builds=None, scheduled_builds=None):
        self._pages = {"running": [running_builds or []], "scheduled": [scheduled_builds or []]}

    def __call__(self, path, token, params=None):
        params = params or {}
        state = params.get("state", "")
        page = params.get("page", 1)
        pages = self._pages.get(state, [])
        if 1 <= page <= len(pages):
            return pages[page - 1]
        return []


def _build(state_pipeline="amd-ci", branch="main", jobs=None, number=100, commit="abc123def456"):
    return {
        "number": number,
        "branch": branch,
        "commit": commit,
        "source": "ui",
        "pipeline": {"slug": state_pipeline},
        "pull_request": {},
        "web_url": f"https://buildkite.com/vllm/{state_pipeline}/builds/{number}",
        "jobs": jobs or [],
    }


def _job(queue, state, runnable_at=None, started_at=None, name="mi250_1: foo", web_url=""):
    return {
        "id": "deadbeef-1234-5678-90ab-cdef01234567",
        "type": "script",
        "state": state,
        "name": name,
        "web_url": web_url,
        "agent_query_rules": [f"queue={queue}"] if queue else [],
        "runnable_at": runnable_at,
        "scheduled_at": runnable_at,
        "started_at": started_at,
    }


class TestCollectSnapshot:
    def test_tracked_queue_zero_filled_when_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "queue_timeseries.jsonl", raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {})
        monkeypatch.setattr(cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: [])

        snap = cqs.collect_snapshot("fake-token")
        for q in cqs.TRACKED_QUEUES:
            assert q in snap["queues"]
            row = snap["queues"][q]
            assert row["waiting"] == 0
            assert row["running"] == 0
            assert row["p95_wait"] == 0

    def test_running_jobs_do_not_inflate_current_wait(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {})
        monkeypatch.setattr(cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: [
            _active_job("amd_mi250_1", "SCHEDULED", runnable_at="2026-04-18T11:55:00Z"),
            _active_job(
                "amd_mi250_1",
                "RUNNING",
                runnable_at="2026-04-18T09:00:00Z",
                started_at="2026-04-18T11:00:00Z",
                name="mi250_1: long queue wait",
            ),
        ])

        with patch("vllm.collect_queue_snapshot.datetime") as dt_mock:
            from datetime import datetime, timezone
            dt_mock.now.return_value = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
            dt_mock.fromisoformat = datetime.fromisoformat
            snap = cqs.collect_snapshot("fake-token")

        row = snap["queues"]["amd_mi250_1"]
        assert row["waiting"] == 1
        assert row["running"] == 1
        assert row["max_wait"] == 5.0
        assert row["p95_wait"] == 5.0
        assert row["avg_wait"] == 5.0

    def test_zombie_jobs_are_excluded_from_analysis_counts(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {
            "amd_mi250_1": {
                "waiting": 2,
                "running": 2,
                "connected_agents": 4,
                "metrics_ts": "2026-04-20T23:50:00Z",
            }
        })
        monkeypatch.setattr(cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: [
            _active_job("amd_mi250_1", "SCHEDULED", runnable_at="2026-04-20T19:00:00Z"),
            _active_job("amd_mi250_1", "SCHEDULED", runnable_at="2026-04-20T23:45:00Z"),
            _active_job("amd_mi250_1", "RUNNING", started_at="2026-04-20T19:00:00Z"),
            _active_job("amd_mi250_1", "RUNNING", started_at="2026-04-20T23:30:00Z"),
        ])

        with patch("vllm.collect_queue_snapshot.datetime") as dt_mock:
            from datetime import datetime, timezone
            dt_mock.now.return_value = datetime(2026, 4, 20, 23, 50, 0, tzinfo=timezone.utc)
            dt_mock.fromisoformat = datetime.fromisoformat
            snap = cqs.collect_snapshot("fake-token")

        row = snap["queues"]["amd_mi250_1"]
        assert row["waiting"] == 1
        assert row["running"] == 1
        assert row["zombie_waiting"] == 1
        assert row["zombie_running"] == 1
        assert snap["total_zombie_waiting"] == 1
        assert snap["total_zombie_running"] == 1
        assert row["p95_wait"] == 5.0

    def test_cluster_metrics_seed_counts_and_agents(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {
            "amd_mi250_1": {
                "waiting": 9,
                "running": 8,
                "connected_agents": 7,
                "metrics_ts": "2026-04-18T12:00:00Z",
                "queue_url": "https://buildkite.com/organizations/vllm/clusters/cluster/queues/q1",
                "dispatch_paused": False,
                "wait_summary": {
                    "p50_wait": 2.0,
                    "p75_wait": 12.0,
                    "p90_wait": 12.0,
                    "p95_wait": 12.0,
                    "p99_wait": 20.0,
                    "max_wait": 20.0,
                    "avg_wait": 2.0,
                },
            }
        })
        monkeypatch.setattr(cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: [
            _active_job("amd_mi250_1", "SCHEDULED", runnable_at="2026-04-18T11:55:00Z"),
            _active_job("amd_mi250_1", "RUNNING", runnable_at="2026-04-18T11:58:00Z", started_at="2026-04-18T11:59:00Z"),
        ])

        with patch("vllm.collect_queue_snapshot.datetime") as dt_mock:
            from datetime import datetime, timezone
            dt_mock.now.return_value = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
            dt_mock.fromisoformat = datetime.fromisoformat
            snap = cqs.collect_snapshot("fake-token")

        row = snap["queues"]["amd_mi250_1"]
        assert row["waiting"] == 9
        assert row["running"] == 8
        assert row["total"] == 17
        assert row["connected_agents"] == 7
        assert row["queue_url"].endswith("/queues/q1")
        assert row["wait_source"] == "cluster_metrics"
        assert row["p50_wait"] == 2.0
        assert row["p95_wait"] == 12.0
        assert row["max_wait"] == 20.0
        assert snap["sources"]["waits"] == "cluster_metrics"
        assert row["waiting_by_workload"] == {"vllm": 1, "omni": 0}
        assert row["running_by_workload"] == {"vllm": 1, "omni": 0}

    def test_workload_split_from_omni_queue(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {})
        monkeypatch.setattr(cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: [
            _active_job("intel-gpu-omni", "SCHEDULED", runnable_at="2026-04-18T11:59:00Z"),
        ])

        with patch("vllm.collect_queue_snapshot.datetime") as dt_mock:
            from datetime import datetime, timezone
            dt_mock.now.return_value = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
            dt_mock.fromisoformat = datetime.fromisoformat
            snap = cqs.collect_snapshot("fake-token")
        row = snap["queues"]["intel-gpu-omni"]
        assert row["waiting_by_workload"] == {"vllm": 0, "omni": 1}

    def test_workload_split_from_omni_branch(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {})
        monkeypatch.setattr(cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: [
            _active_job(
                "amd_mi250_1",
                "RUNNING",
                runnable_at="2026-04-18T11:58:00Z",
                started_at="2026-04-18T12:00:00Z",
                branch="user/omni-feature",
            ),
        ])

        with patch("vllm.collect_queue_snapshot.datetime") as dt_mock:
            from datetime import datetime, timezone
            dt_mock.now.return_value = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
            dt_mock.fromisoformat = datetime.fromisoformat
            snap = cqs.collect_snapshot("fake-token")
        row = snap["queues"]["amd_mi250_1"]
        assert row["running_by_workload"] == {"vllm": 0, "omni": 1}

    def test_jobs_without_queue_rule_skipped(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {})
        monkeypatch.setattr(cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: [
            _active_job("", "RUNNING", name="no-queue job"),
        ])

        snap = cqs.collect_snapshot("fake-token")
        assert snap["total_running"] == 0

    def test_legacy_fallback_treats_assigned_as_running(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {})
        monkeypatch.setattr(cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: (_ for _ in ()).throw(RuntimeError("no graphql")))
        monkeypatch.setattr(cqs, "bk_get", _FakeBk(running_builds=[_build(jobs=[
            _job("amd_mi250_1", "scheduled", runnable_at="2026-04-18T11:55:00Z"),
            _job("amd_mi250_1", "assigned", runnable_at="2026-04-18T11:58:00Z"),
        ])]))

        with patch("vllm.collect_queue_snapshot.datetime") as dt_mock:
            from datetime import datetime, timezone
            dt_mock.now.return_value = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
            dt_mock.fromisoformat = datetime.fromisoformat
            snap = cqs.collect_snapshot("fake-token")

        row = snap["queues"]["amd_mi250_1"]
        assert row["waiting"] == 1
        assert row["running"] == 1

    def test_output_schema_has_required_top_level_keys(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {})
        monkeypatch.setattr(cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: [])

        snap = cqs.collect_snapshot("fake-token")
        for key in ("ts", "queues", "total_waiting", "total_running", "total_zombie_waiting", "total_zombie_running", "sources"):
            assert key in snap
        assert snap["ts"].endswith("Z")
        json.dumps(snap)


class TestJobsJsonSideEffect:
    """``collect_snapshot`` writes ``queue_jobs.json`` as a side effect."""

    def test_jobs_file_written_with_expected_schema(self, monkeypatch, tmp_path):
        out_path = tmp_path / "queue_timeseries.jsonl"
        monkeypatch.setattr(cqs, "OUTPUT", out_path, raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {})
        monkeypatch.setattr(cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: [
            _active_job("amd_mi250_1", "SCHEDULED", runnable_at="2026-04-18T11:55:00Z"),
            _active_job("amd_mi250_1", "RUNNING", started_at="2026-04-18T11:58:00Z"),
        ])

        with patch("vllm.collect_queue_snapshot.datetime") as dt_mock:
            from datetime import datetime, timezone
            dt_mock.now.return_value = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
            dt_mock.fromisoformat = datetime.fromisoformat
            cqs.collect_snapshot("fake-token")
        jobs_file = out_path.parent / "queue_jobs.json"
        assert jobs_file.exists()
        data = json.loads(jobs_file.read_text())
        assert "ts" in data and "pending" in data and "running" in data
        assert len(data["pending"]) == 1
        pending = data["pending"][0]
        for field in ("name", "queue", "wait_min", "url", "workload", "branch", "commit"):
            assert field in pending
        assert pending["state"] == "scheduled"
        assert pending["analysis_excluded"] is False
        assert len(data["running"]) == 1
        running = data["running"][0]
        for field in ("name", "queue", "url", "run_min", "queue_wait_before_start_min"):
            assert field in running
        assert running["state"] == "running"
        assert running["analysis_excluded"] is False
