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
    def test_empty_returns_nullable_block(self):
        assert cqs._wait_summary([]) == {
            "p50": None,
            "p75": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "max": None,
            "avg": None,
        }

    def test_values_rounded_to_one_decimal(self):
        out = cqs._wait_summary([1.0, 2.0, 3.0, 4.0, 5.0])
        assert out == {
            "p50": 3.0,
            "p75": 4.0,
            "p90": 5.0,
            "p95": 5.0,
            "p99": 5.0,
            "max": 5.0,
            "avg": 3.0,
        }


class TestOfficialWaitSummary:
    def test_only_exposes_queue_native_metrics(self):
        assert cqs._wait_summary_from_queue_metrics(
            {
                "min": 60,
                "p50": 120,
                "p95": 900,
                "max": 1200,
            }
        ) == {
            "p50": 2.0,
            "p95": 15.0,
            "max": 20.0,
        }

    def test_missing_native_metrics_remain_null(self):
        assert cqs._wait_summary_from_queue_metrics({"p50": 120}) == {
            "p50": 2.0,
            "p95": None,
            "max": None,
        }


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
                            "edges": [
                                {
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
                                }
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }

        monkeypatch.setattr(cqs, "bk_graphql", fake_graphql)

        metrics = cqs.fetch_cluster_queue_metrics("fake-token")

        assert metrics["amd_mi355_1"]["graphql_id"] == "ClusterQueueID"
        assert metrics["amd_mi355_1"]["official_wait"] == {
            "p50": 2.0,
            "p95": 15.0,
            "max": 20.0,
        }
        assert "p99" not in metrics["amd_mi355_1"]["official_wait"]

    def test_fetches_jobs_by_cluster_queue_graphql_id(self, monkeypatch):
        calls = []

        def fake_graphql(query, token, variables):
            calls.append((query, variables))
            return {
                "organization": {
                    "jobs": {
                        "edges": [
                            {
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
                            }
                        ],
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


class TestQueueExclusions:
    def test_predicate_is_case_insensitive_and_covers_suffixes(self):
        assert cqs.is_excluded_queue("amd_mi355B")
        assert cqs.is_excluded_queue("AMD_MI355b_8")
        assert cqs.is_excluded_queue("amd_mi355B_future_suffix")
        assert cqs.is_excluded_queue("amd_mi250_8")
        assert not cqs.is_excluded_queue("amd_mi355_8")
        assert not cqs.is_excluded_queue("amd_mi250_4")
        assert all(not cqs.is_excluded_queue(queue) for queue in cqs.TRACKED_QUEUES)

    def test_amd_scope_includes_cpu_and_excludes_mi355b(self):
        assert cqs.is_amd_queue("AMD_MI250_1")
        assert cqs.is_amd_queue("amd-cpu")
        assert not cqs.is_amd_queue("amd_mi250_8")
        assert not cqs.is_amd_queue("amd_mi355B_8")
        assert not cqs.is_amd_queue("gpu_1_queue")


def _history_snapshot(ts: str) -> dict:
    return {
        "ts": ts,
        "queues": {
            "amd_mi250_1": {
                "waiting": 0,
                "running": 0,
                "official_wait": {"p50": None, "p95": None, "max": None},
                "sample_wait": {
                    "available": True,
                    "count": 0,
                    "p50": None,
                    "p75": None,
                    "p90": None,
                    "p95": None,
                    "p99": None,
                    "max": None,
                    "avg": None,
                },
                "current_wait": {
                    "p50": {"value": None, "source": None},
                    "p95": {"value": None, "source": None},
                    "p99": {"value": None, "source": None},
                },
                "p50_wait": None,
                "p50_wait_source": None,
                "p95_wait": None,
                "p95_wait_source": None,
                "p99_wait": None,
                "p99_wait_source": None,
            }
        },
        "total_waiting": 0,
        "total_running": 0,
        "sources": {"counts": "cluster_metrics", "wait_fields": {}},
    }


class TestHistoryPrune:
    def test_drops_pre_reset_but_migrates_old_schema_rows(self, tmp_path):
        path = tmp_path / "queue_timeseries.jsonl"
        path.write_text(
            json.dumps(
                {
                    "ts": "2026-04-20T22:00:00Z",
                    "queues": {"amd_mi250_1": {"waiting": 1}},
                    "total_waiting": 1,
                    "total_running": 0,
                }
            )
            + "\n"
            + json.dumps(
                {
                    "ts": "2026-04-20T23:45:00Z",
                    "queues": {
                        "amd_mi250_1": {
                            "waiting": 2,
                            "running": 3,
                            "connected_agents": 9,
                            "p50_wait": 0,
                            "p75_wait": 0,
                            "p90_wait": 0,
                            "p99_wait": 0,
                            "max_wait": 0,
                            "avg_wait": 0,
                        }
                    },
                    "total_waiting": 2,
                    "total_running": 3,
                    "sources": {"counts": "cluster_metrics", "waits": "cluster_metrics"},
                }
            )
            + "\n"
            + json.dumps(_history_snapshot("2026-04-20T23:46:00Z"))
            + "\n"
        )

        total, kept = cqs.prune_history_file(path, now=datetime(2026, 4, 21, tzinfo=timezone.utc))
        assert total == 3
        assert kept == 2
        lines = [line for line in path.read_text().splitlines() if line.strip()]
        assert len(lines) == 2
        migrated = json.loads(lines[0])
        assert migrated["ts"] == "2026-04-20T23:45:00Z"
        assert (migrated["total_waiting"], migrated["total_running"]) == (2, 3)
        row = migrated["queues"]["amd_mi250_1"]
        assert row["count_source"] == "historical_counts"
        assert row["count_provenance"]["original_source"] == "cluster_metrics"
        assert row["connected_agents"] is None
        assert row["connected_agents_source"] is None
        assert row["official_wait"] == {"p50": None, "p95": None, "max": None}
        assert row["p50_wait"] is None
        assert row["p95_wait"] is None
        assert row["p99_wait"] is None
        assert migrated["sources"]["history_provenance"]["migration"] == (
            "legacy_queue_snapshot_v1_to_v2"
        )

    def test_migrated_waits_require_nonzero_sample_and_are_labeled_sampled(self):
        snapshot = {
            "ts": "2026-06-20T12:00:00Z",
            "queues": {
                "amd_mi250_1": {
                    "waiting": 2,
                    "running": 1,
                    "wait_sample_count": 2,
                    "p50_wait": 1.5,
                    "p75_wait": 2.0,
                    "p90_wait": 2.5,
                    "p95_wait": 2.5,
                    "p99_wait": 2.5,
                    "max_wait": 2.5,
                    "avg_wait": 2.0,
                    "wait_source": "cluster_metrics",
                },
                "amd_mi250_2": {
                    "waiting": 0,
                    "running": 4,
                    "wait_sample_count": 0,
                    "p50_wait": 0,
                    "p95_wait": 0,
                    "p99_wait": 0,
                },
            },
            "sources": {"counts": "active_job_scan", "waits": "scheduled_jobs"},
        }

        migrated = cqs.normalize_history_snapshot(snapshot)
        sampled = migrated["queues"]["amd_mi250_1"]
        unsupported = migrated["queues"]["amd_mi250_2"]

        assert sampled["sample_wait"]["count"] == 2
        assert sampled["sample_wait"]["source"] == "historical_scheduled_job_sample"
        assert (sampled["p50_wait"], sampled["p50_wait_source"]) == (1.5, "sample_wait")
        assert (sampled["p99_wait"], sampled["p99_wait_source"]) == (2.5, "sample_wait")
        assert unsupported["sample_wait"]["count"] == 0
        assert unsupported["p50_wait"] is None
        assert unsupported["p95_wait"] is None
        assert unsupported["p99_wait"] is None
        assert migrated["sources"]["official_wait"] == "unavailable"
        remigrated = cqs.normalize_history_snapshot(migrated)
        assert remigrated["sources"]["sampled_wait"] == ("historical_scheduled_job_sample")
        assert (
            remigrated["sources"]["history_provenance"]
            == (migrated["sources"]["history_provenance"])
        )

    def test_migration_removes_excluded_queues_and_recomputes_totals(self):
        snapshot = {
            "ts": "2026-06-20T12:00:00Z",
            "queues": {
                "amd_mi250_1": {"waiting": 1, "running": 2},
                "AMD_MI355b_1": {"waiting": 50, "running": 60},
                "amd_mi355B_future_suffix": {"waiting": 70, "running": 80},
            },
            "total_waiting": 121,
            "total_running": 142,
        }

        migrated = cqs.normalize_history_snapshot(snapshot)

        assert set(migrated["queues"]) == {"amd_mi250_1"}
        assert migrated["total_waiting"] == 1
        assert migrated["total_running"] == 2

    def test_inconsistent_workload_splits_become_unavailable_without_changing_totals(self):
        snapshot = {
            "ts": "2026-06-20T12:00:00Z",
            "queues": {
                "amd_mi250_1": {
                    "waiting": 1,
                    "running": 2,
                    "waiting_by_workload": {"vllm": 0, "omni": 2},
                    "running_by_workload": {"vllm": 1, "omni": 0},
                },
                "amd-cpu": {
                    "waiting": 3,
                    "running": 4,
                    "waiting_by_workload": {"vllm": 3, "omni": 0},
                    "running_by_workload": {"vllm": 4, "omni": 0},
                },
                "gpu_1_queue": {"waiting": 5, "running": 6},
                "AMD_MI355b_8": {"waiting": 100, "running": 100},
            },
            "total_waiting": 109,
            "total_running": 112,
            "sources": {
                "counts": "active_job_scan",
                "active_jobs": "legacy_build_scan",
            },
        }

        migrated = cqs.normalize_history_snapshot(snapshot)
        row = migrated["queues"]["amd_mi250_1"]

        assert row["waiting_by_workload"] is None
        assert row["waiting_by_workload_provenance"] == {
            "available": False,
            "status": "inconsistent",
            "source": "legacy_build_scan",
            "reason": "observed_split_exceeds_queue_total",
            "queue_total": 1,
            "observed_split_total": 2,
            "observed_split": {"omni": 2, "vllm": 0},
        }
        assert row["running_by_workload"] == {"omni": 0, "vllm": 1}
        assert row["running_by_workload_provenance"]["status"] == "partial"
        assert migrated["total_waiting"] == 9
        assert migrated["total_running"] == 12
        assert migrated["scope_totals"]["all"] == {
            "waiting": 9,
            "running": 12,
            "count_source": "historical_counts",
            "count_sources": ["historical_counts"],
            "queue_count": 3,
        }
        assert migrated["scope_totals"]["amd"] == {
            "waiting": 4,
            "running": 6,
            "count_source": "historical_counts",
            "count_sources": ["historical_counts"],
            "queue_count": 2,
        }
        assert "no remainder is assigned" in migrated["sources"]["workload_split_fields"]["rule"]

        remigrated = cqs.normalize_history_snapshot(migrated)
        assert remigrated["queues"]["amd_mi250_1"]["waiting_by_workload"] is None
        assert (
            remigrated["queues"]["amd_mi250_1"]["waiting_by_workload_provenance"]
            == row["waiting_by_workload_provenance"]
        )

    def test_merge_is_deterministic_and_deduplicates_timestamps(self, tmp_path):
        path = tmp_path / "queue_timeseries.jsonl"
        local = _history_snapshot("2026-06-21T00:00:00Z")
        local["merge_marker"] = "local"
        path.write_text(json.dumps(local) + "\n")
        duplicate = _history_snapshot("2026-06-21T00:00:00Z")
        duplicate["merge_marker"] = "incoming"
        incoming = [
            _history_snapshot("2026-06-20T00:00:00Z"),
            duplicate,
        ]

        assert cqs.merge_history_rows(path, incoming) == (2, 2)
        first_write = path.read_text()
        assert cqs.merge_history_rows(path, incoming) == (2, 2)
        assert path.read_text() == first_write
        assert [json.loads(line)["ts"] for line in first_write.splitlines()] == [
            "2026-06-20T00:00:00Z",
            "2026-06-21T00:00:00Z",
        ]
        assert json.loads(first_write.splitlines()[-1])["merge_marker"] == "local"

    def test_drops_rows_older_than_retention_window(self, tmp_path):
        path = tmp_path / "queue_timeseries.jsonl"

        def snapshot(ts: str) -> str:
            return json.dumps(_history_snapshot(ts))

        path.write_text(
            snapshot("2026-05-17T00:00:00Z") + "\n" + snapshot("2026-05-20T00:00:00Z") + "\n"
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
        monkeypatch.setattr(
            cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: []
        )

        snap = cqs.collect_snapshot("fake-token")
        for q in cqs.TRACKED_QUEUES:
            assert q in snap["queues"]
            row = snap["queues"][q]
            assert row["waiting"] == 0
            assert row["running"] == 0
            assert row["p95_wait"] is None
            assert row["p99_wait"] is None
            assert row["official_wait"] == {"p50": None, "p95": None, "max": None}
            assert row["sample_wait"]["count"] == 0
            assert row["connected_agents"] is None
            assert row["connected_agents_source"] is None
        assert snap["sources"]["agents"] == "unavailable"
        assert snap["sources"]["official_wait"] == "unavailable"
        assert snap["sources"]["counts"] == "active_job_scan"

    def test_excluded_queues_never_reach_rows_jobs_or_totals(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "queue_timeseries.jsonl", raising=False)
        monkeypatch.setattr(
            cqs,
            "fetch_cluster_queue_metrics",
            lambda token: {
                "AMD_MI355b_4": {
                    "counts_available": True,
                    "waiting": 20,
                    "running": 30,
                    "connected_agents": 40,
                    "official_wait": {"p50": 1.0, "p95": 2.0, "max": 3.0},
                },
                "amd_mi250_1": {
                    "counts_available": True,
                    "waiting": 1,
                    "running": 2,
                    "connected_agents": 3,
                },
            },
        )
        monkeypatch.setattr(
            cqs,
            "fetch_active_cluster_jobs",
            lambda token, queue_ids_by_key=None: [
                _active_job("amd_mi355B_future", "SCHEDULED"),
                _active_job("amd_mi250_1", "RUNNING"),
            ],
        )

        snap = cqs.collect_snapshot("fake-token")

        assert all(not cqs.is_excluded_queue(queue) for queue in snap["queues"])
        assert snap["total_waiting"] == 1
        assert snap["total_running"] == 2
        jobs = json.loads((tmp_path / "queue_jobs.json").read_text())
        assert all(
            not cqs.is_excluded_queue(job["queue"]) for job in [*jobs["pending"], *jobs["running"]]
        )

    def test_running_jobs_do_not_inflate_current_wait(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {})
        monkeypatch.setattr(
            cqs,
            "fetch_active_cluster_jobs",
            lambda token, queue_ids_by_key=None: [
                _active_job("amd_mi250_1", "SCHEDULED", runnable_at="2026-04-18T11:55:00Z"),
                _active_job(
                    "amd_mi250_1",
                    "RUNNING",
                    runnable_at="2026-04-18T09:00:00Z",
                    started_at="2026-04-18T11:00:00Z",
                    name="mi250_1: long queue wait",
                ),
            ],
        )

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
        assert row["p99_wait"] == 5.0
        assert row["avg_wait"] == 5.0
        assert row["sample_wait"] == {
            "available": True,
            "count": 1,
            "p50": 5.0,
            "p75": 5.0,
            "p90": 5.0,
            "p95": 5.0,
            "p99": 5.0,
            "max": 5.0,
            "avg": 5.0,
        }
        assert row["p50_wait_source"] == "sample_wait"
        assert row["p95_wait_source"] == "sample_wait"
        assert row["p99_wait_source"] == "sample_wait"
        assert row["current_wait"] == {
            "p50": {"value": 5.0, "source": "sample_wait"},
            "p95": {"value": 5.0, "source": "sample_wait"},
            "p99": {"value": 5.0, "source": "sample_wait"},
        }

    def test_zombie_jobs_are_excluded_from_analysis_counts(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(
            cqs,
            "fetch_cluster_queue_metrics",
            lambda token: {
                "amd_mi250_1": {
                    "waiting": 2,
                    "running": 2,
                    "connected_agents": 4,
                    "metrics_ts": "2026-04-20T23:50:00Z",
                }
            },
        )
        monkeypatch.setattr(
            cqs,
            "fetch_active_cluster_jobs",
            lambda token, queue_ids_by_key=None: [
                _active_job("amd_mi250_1", "SCHEDULED", runnable_at="2026-04-20T19:00:00Z"),
                _active_job("amd_mi250_1", "SCHEDULED", runnable_at="2026-04-20T23:45:00Z"),
                _active_job("amd_mi250_1", "RUNNING", started_at="2026-04-20T19:00:00Z"),
                _active_job("amd_mi250_1", "RUNNING", started_at="2026-04-20T23:30:00Z"),
            ],
        )

        with patch("vllm.collect_queue_snapshot.datetime") as dt_mock:
            from datetime import datetime, timezone

            dt_mock.now.return_value = datetime(2026, 4, 20, 23, 50, 0, tzinfo=timezone.utc)
            dt_mock.fromisoformat = datetime.fromisoformat
            snap = cqs.collect_snapshot("fake-token")

        row = snap["queues"]["amd_mi250_1"]
        assert row["waiting"] == 2
        assert row["running"] == 2
        assert row["zombie_waiting"] == 1
        assert row["zombie_running"] == 1
        assert snap["total_zombie_waiting"] == 1
        assert snap["total_zombie_running"] == 1
        assert row["p95_wait"] == 5.0
        assert row["sample_wait"]["count"] == 1
        assert row["sample_wait"]["max"] == 5.0
        assert row["count_source"] == "cluster_metrics"

    def test_cluster_metrics_seed_counts_and_agents(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(
            cqs,
            "fetch_cluster_queue_metrics",
            lambda token: {
                "amd_mi250_1": {
                    "waiting": 9,
                    "running": 8,
                    "connected_agents": 7,
                    "metrics_ts": "2026-04-18T12:00:00Z",
                    "queue_url": "https://buildkite.com/organizations/vllm/clusters/cluster/queues/q1",
                    "dispatch_paused": False,
                    "official_wait": {
                        "p50": 2.0,
                        "p95": 12.0,
                        "max": 20.0,
                    },
                }
            },
        )
        monkeypatch.setattr(
            cqs,
            "fetch_active_cluster_jobs",
            lambda token, queue_ids_by_key=None: [
                _active_job("amd_mi250_1", "SCHEDULED", runnable_at="2026-04-18T11:55:00Z"),
                _active_job(
                    "amd_mi250_1",
                    "RUNNING",
                    runnable_at="2026-04-18T11:58:00Z",
                    started_at="2026-04-18T11:59:00Z",
                ),
            ],
        )

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
        assert row["connected_agents_source"] == "queue_native_metrics"
        assert row["queue_url"].endswith("/queues/q1")
        assert row["wait_source"] == "cluster_metrics"
        assert row["count_source"] == "cluster_metrics"
        assert row["p50_wait"] == 2.0
        assert row["p95_wait"] == 12.0
        assert row["p99_wait"] == 5.0
        assert row["max_wait"] == 20.0
        assert row["p50_wait_source"] == "official_wait"
        assert row["p95_wait_source"] == "official_wait"
        assert row["p99_wait_source"] == "sample_wait"
        assert row["max_wait_source"] == "official_wait"
        assert row["p75_wait"] == 5.0
        assert row["p75_wait_source"] == "sample_wait"
        assert snap["sources"]["waits"] == "cluster_metrics"
        assert snap["sources"]["official_wait"] == "queue_native_metrics"
        assert snap["sources"]["agents"] == "queue_native_metrics"
        assert row["waiting_by_workload"] == {"vllm": 1, "omni": 0}
        assert row["running_by_workload"] == {"vllm": 1, "omni": 0}

    def test_official_max_never_becomes_p99_or_sample_only_metrics(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(
            cqs,
            "fetch_cluster_queue_metrics",
            lambda token: {
                "amd_mi250_1": {
                    "waiting": 9,
                    "running": 8,
                    "connected_agents": 7,
                    "official_wait": {"p50": 2.0, "p95": 12.0, "max": 20.0},
                }
            },
        )
        monkeypatch.setattr(
            cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: []
        )

        snap = cqs.collect_snapshot("fake-token")
        row = snap["queues"]["amd_mi250_1"]

        assert row["official_wait"] == {"p50": 2.0, "p95": 12.0, "max": 20.0}
        assert row["sample_wait"]["count"] == 0
        assert row["p50_wait"] == 2.0
        assert row["p95_wait"] == 12.0
        assert row["max_wait"] == 20.0
        assert row["p99_wait"] is None
        assert row["p99_wait_source"] is None
        assert row["current_wait"]["p99"] == {"value": None, "source": None}
        assert row["p75_wait"] is None
        assert row["p90_wait"] is None
        assert row["avg_wait"] is None
        assert "sample_wait.p99" in snap["sources"]["wait_fields"]["p99_wait"]

    def test_sample_wait_contains_all_exact_job_percentiles(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {})
        monkeypatch.setattr(
            cqs,
            "fetch_active_cluster_jobs",
            lambda token, queue_ids_by_key=None: [
                _active_job(
                    "amd_mi250_1", "SCHEDULED", runnable_at=f"2026-04-18T11:{minute:02d}:00Z"
                )
                for minute in (59, 58, 57, 56, 55)
            ],
        )

        with patch("vllm.collect_queue_snapshot.datetime") as dt_mock:
            from datetime import datetime, timezone

            dt_mock.now.return_value = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
            dt_mock.fromisoformat = datetime.fromisoformat
            snap = cqs.collect_snapshot("fake-token")

        row = snap["queues"]["amd_mi250_1"]
        assert row["official_wait"] == {"p50": None, "p95": None, "max": None}
        assert row["sample_wait"] == {
            "available": True,
            "count": 5,
            "p50": 3.0,
            "p75": 4.0,
            "p90": 5.0,
            "p95": 5.0,
            "p99": 5.0,
            "max": 5.0,
            "avg": 3.0,
        }
        assert row["p50_wait"] == 3.0
        assert row["p95_wait"] == 5.0
        assert row["p99_wait"] == 5.0
        assert row["p50_wait_source"] == "sample_wait"
        assert row["p95_wait_source"] == "sample_wait"
        assert row["p99_wait_source"] == "sample_wait"
        assert row["current_wait"]["p99"] == {"value": 5.0, "source": "sample_wait"}

    def test_official_p50_and_p95_are_selected_but_sample_supplies_p99(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(
            cqs,
            "fetch_cluster_queue_metrics",
            lambda token: {
                "amd_mi250_1": {
                    "waiting": 2,
                    "running": 0,
                    "connected_agents": 1,
                    "official_wait": {"p50": 2.0, "p95": 12.0, "max": 20.0},
                }
            },
        )
        monkeypatch.setattr(
            cqs,
            "fetch_active_cluster_jobs",
            lambda token, queue_ids_by_key=None: [
                _active_job("amd_mi250_1", "SCHEDULED", runnable_at="2026-04-18T11:55:00Z"),
                _active_job("amd_mi250_1", "SCHEDULED", runnable_at="2026-04-18T11:50:00Z"),
            ],
        )

        with patch("vllm.collect_queue_snapshot.datetime") as dt_mock:
            from datetime import datetime, timezone

            dt_mock.now.return_value = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
            dt_mock.fromisoformat = datetime.fromisoformat
            snap = cqs.collect_snapshot("fake-token")

        row = snap["queues"]["amd_mi250_1"]
        assert (row["p50_wait"], row["p50_wait_source"]) == (2.0, "official_wait")
        assert (row["p95_wait"], row["p95_wait_source"]) == (12.0, "official_wait")
        assert (row["p99_wait"], row["p99_wait_source"]) == (10.0, "sample_wait")
        assert row["sample_wait"]["p99"] == 10.0
        assert row["current_wait"] == {
            "p50": {"value": 2.0, "source": "official_wait"},
            "p95": {"value": 12.0, "source": "official_wait"},
            "p99": {"value": 10.0, "source": "sample_wait"},
        }

    def test_sample_max_wins_when_it_exceeds_official_max(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(
            cqs,
            "fetch_cluster_queue_metrics",
            lambda token: {
                "amd_mi250_1": {
                    "waiting": 1,
                    "running": 0,
                    "connected_agents": 1,
                    "official_wait": {"p50": 0.0, "p95": 0.0, "max": 0.0},
                }
            },
        )
        monkeypatch.setattr(
            cqs,
            "fetch_active_cluster_jobs",
            lambda token, queue_ids_by_key=None: [
                _active_job("amd_mi250_1", "SCHEDULED", runnable_at="2026-04-18T11:42:00Z"),
            ],
        )

        with patch("vllm.collect_queue_snapshot.datetime") as dt_mock:
            from datetime import datetime, timezone

            dt_mock.now.return_value = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
            dt_mock.fromisoformat = datetime.fromisoformat
            snap = cqs.collect_snapshot("fake-token")

        row = snap["queues"]["amd_mi250_1"]
        assert (row["p50_wait"], row["p50_wait_source"]) == (0.0, "official_wait")
        assert (row["p95_wait"], row["p95_wait_source"]) == (0.0, "official_wait")
        assert (row["p99_wait"], row["p99_wait_source"]) == (18.0, "sample_wait")
        assert (row["max_wait"], row["max_wait_source"]) == (18.0, "sample_wait")

    def test_sample_fills_missing_official_percentile_only(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(
            cqs,
            "fetch_cluster_queue_metrics",
            lambda token: {
                "amd_mi250_1": {
                    "waiting": 1,
                    "running": 0,
                    "connected_agents": 1,
                    "official_wait": {"p50": None, "p95": 12.0, "max": 20.0},
                }
            },
        )
        monkeypatch.setattr(
            cqs,
            "fetch_active_cluster_jobs",
            lambda token, queue_ids_by_key=None: [
                _active_job("amd_mi250_1", "SCHEDULED", runnable_at="2026-04-18T11:55:00Z"),
            ],
        )

        with patch("vllm.collect_queue_snapshot.datetime") as dt_mock:
            from datetime import datetime, timezone

            dt_mock.now.return_value = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
            dt_mock.fromisoformat = datetime.fromisoformat
            snap = cqs.collect_snapshot("fake-token")

        row = snap["queues"]["amd_mi250_1"]
        assert (row["p50_wait"], row["p50_wait_source"]) == (5.0, "sample_wait")
        assert (row["p95_wait"], row["p95_wait_source"]) == (12.0, "official_wait")

    def test_queue_scoped_fetch_marks_other_queue_samples_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(
            cqs,
            "fetch_cluster_queue_metrics",
            lambda token: {
                "amd_mi250_1": {
                    "graphql_id": "ClusterQueueID",
                    "waiting": 1,
                    "running": 0,
                    "connected_agents": 1,
                    "official_wait": {"p50": 2.0, "p95": 12.0, "max": 20.0},
                }
            },
        )
        monkeypatch.setattr(
            cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: []
        )

        snap = cqs.collect_snapshot("fake-token")

        sampled = snap["queues"]["amd_mi250_1"]["sample_wait"]
        assert sampled["available"] is True
        assert sampled["count"] == 0
        unavailable = next(
            row
            for queue, row in snap["queues"].items()
            if queue != "amd_mi250_1" and row["count_source"] == "active_job_scan"
        )["sample_wait"]
        assert unavailable["available"] is False
        assert unavailable["count"] is None
        assert unavailable["p99"] is None

    def test_workload_split_from_omni_queue(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {})
        monkeypatch.setattr(
            cqs,
            "fetch_active_cluster_jobs",
            lambda token, queue_ids_by_key=None: [
                _active_job("intel-gpu-omni", "SCHEDULED", runnable_at="2026-04-18T11:59:00Z"),
            ],
        )

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
        monkeypatch.setattr(
            cqs,
            "fetch_active_cluster_jobs",
            lambda token, queue_ids_by_key=None: [
                _active_job(
                    "amd_mi250_1",
                    "RUNNING",
                    runnable_at="2026-04-18T11:58:00Z",
                    started_at="2026-04-18T12:00:00Z",
                    branch="user/omni-feature",
                ),
            ],
        )

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
        monkeypatch.setattr(
            cqs,
            "fetch_active_cluster_jobs",
            lambda token, queue_ids_by_key=None: [
                _active_job("", "RUNNING", name="no-queue job"),
            ],
        )

        snap = cqs.collect_snapshot("fake-token")
        assert snap["total_running"] == 0

    def test_legacy_fallback_treats_assigned_as_running(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "out.jsonl", raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {})
        monkeypatch.setattr(
            cqs,
            "fetch_active_cluster_jobs",
            lambda token, queue_ids_by_key=None: (_ for _ in ()).throw(RuntimeError("no graphql")),
        )
        monkeypatch.setattr(
            cqs,
            "bk_get",
            _FakeBk(
                running_builds=[
                    _build(
                        jobs=[
                            _job("amd_mi250_1", "scheduled", runnable_at="2026-04-18T11:55:00Z"),
                            _job("amd_mi250_1", "assigned", runnable_at="2026-04-18T11:58:00Z"),
                        ]
                    )
                ]
            ),
        )

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
        monkeypatch.setattr(
            cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: []
        )

        snap = cqs.collect_snapshot("fake-token")
        for key in (
            "ts",
            "queues",
            "total_waiting",
            "total_running",
            "total_zombie_waiting",
            "total_zombie_running",
            "sources",
        ):
            assert key in snap
        assert snap["ts"].endswith("Z")
        json.dumps(snap)


class TestJobsJsonSideEffect:
    """``collect_snapshot`` writes ``queue_jobs.json`` as a side effect."""

    def test_jobs_file_written_with_expected_schema(self, monkeypatch, tmp_path):
        out_path = tmp_path / "queue_timeseries.jsonl"
        monkeypatch.setattr(cqs, "OUTPUT", out_path, raising=False)
        monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {})
        monkeypatch.setattr(
            cqs,
            "fetch_active_cluster_jobs",
            lambda token, queue_ids_by_key=None: [
                _active_job("amd_mi250_1", "SCHEDULED", runnable_at="2026-04-18T11:55:00Z"),
                _active_job("amd_mi250_1", "RUNNING", started_at="2026-04-18T11:58:00Z"),
            ],
        )

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
