"""Unit tests for scripts/vllm/collect_hotness.py.

Verifies:
- ``_normalize_group`` collapses shard/hardware/parenthetical noise consistently
- ``_stats`` returns the hotness row schema (``count``, ``avg_min``, ``p50_min``,
  ``p90_min``, ``max_min``) with the right rounding
- ``collect_hotness`` with a mocked Buildkite response produces well-formed
  ``test_groups`` / ``branches`` / ``queues`` rows, filters non-AMD queues,
  honours the stuck-job ceiling (> 24h), and emits per-window aggregations
  keyed by ``HOTNESS_WINDOWS_HOURS``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from vllm import collect_hotness as ch


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# Tests pin "now" to a fixed moment so windowed filtering is deterministic
# regardless of when the test runs. Any real wall-clock drift would otherwise
# push the fixtures out of every window.
_NOW = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def frozen_now(monkeypatch):
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D401 — mimic datetime.now signature
            return _NOW if tz is None else _NOW.astimezone(tz)
    monkeypatch.setattr(ch, "datetime", _FrozenDatetime)
    return _NOW


class TestNormalizeGroup:
    @pytest.mark.parametrize("job,expected", [
        # HW prefix + GPU-count decoration + shard all get stripped.
        ("mi325_4: V1 e2e (4 GPUs) 1/3", "V1 e2e"),
        ("mi325_4: V1 e2e (2 GPU)", "V1 e2e"),
        ("mi325_4: V1 e2e (4 gpus)", "V1 e2e"),
        # No decoration — just the HW prefix strip.
        ("mi250_1: V1 e2e", "V1 e2e"),
        ("mi355B_8: distributed tests 2/5", "distributed tests"),
        ("plain-name", "plain-name"),
    ])
    def test_collapses_hardware_shard_and_gpu_counts(self, job, expected):
        assert ch._normalize_group(job) == expected

    @pytest.mark.parametrize("job,expected", [
        # Meaningful trailing parentheticals must survive — these name
        # distinct YAML test groups with their own pool of tests.
        ("mi250_1: Multi-Modal Models (Extended Pooling)",
         "Multi-Modal Models (Extended Pooling)"),
        ("mi250_1: Multi-Modal Models (Extended Generation 1)",
         "Multi-Modal Models (Extended Generation 1)"),
        ("mi250_1: Basic Models Tests (Other)",
         "Basic Models Tests (Other)"),
        ("mi325_1: Multi-Modal Processor (CPU)",
         "Multi-Modal Processor (CPU)"),
        ("mi250_1: Multi-Modal Accuracy Eval (Small Models)",
         "Multi-Modal Accuracy Eval (Small Models)"),
        # Parenthetical mid-string + trailing qualifier — untouched either way.
        ("mi250_1: Multi-Modal Models (Standard) 1: qwen2",
         "Multi-Modal Models (Standard) 1: qwen2"),
    ])
    def test_preserves_meaningful_parentheticals(self, job, expected):
        # Regression: the old ``\([^)]*\)$`` stripped any trailing parens,
        # collapsing eight distinct Multi-Modal YAML groups onto a single
        # phantom "Multi-Modal Models" row in the workload trajectory tab.
        assert ch._normalize_group(job) == expected

    def test_empty_falls_through_to_unknown(self):
        assert ch._normalize_group("") == "unknown"


class TestStats:
    def test_empty_returns_zero_block(self):
        assert ch._stats([]) == {"count": 0, "avg_min": 0.0, "p50_min": 0.0, "p90_min": 0.0, "max_min": 0.0}

    def test_percentile_rounding(self):
        out = ch._stats([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        assert out["count"] == 10
        assert out["avg_min"] == 5.5
        assert out["p50_min"] == 6.0     # idx = int(10 * 50/100) = 5 → values[5]
        assert out["p90_min"] == 10.0    # idx = int(10 * 90/100) = 9 → values[9]
        assert out["max_min"] == 10.0


def _job(name, queue, state="passed", started=None, finished=None):
    return {
        "type": "script",
        "name": name,
        "state": state,
        "started_at": started,
        "finished_at": finished,
        "agent_query_rules": [f"queue={queue}"] if queue else [],
    }


def _build(branch="main", commit="abc" * 4, jobs=None, number=1, slug="amd-ci", created_at=None):
    return {
        "number": number,
        "branch": branch,
        "commit": commit,
        "source": "api",
        "created_at": created_at or _iso(_NOW - timedelta(hours=2)),
        "pipeline": {"slug": slug},
        "pull_request": {},
        "jobs": jobs or [],
    }


def _job_finishing(name, queue, minutes_ago, duration_min=30, state="passed"):
    """Build a job whose ``finished_at`` is ``minutes_ago`` before frozen-now."""
    finished = _NOW - timedelta(minutes=minutes_ago)
    started = finished - timedelta(minutes=duration_min)
    return _job(name, queue, state=state, started=_iso(started), finished=_iso(finished))


class TestCollectHotness:
    def _install_fake(self, monkeypatch, builds):
        """Replace ``_paginate`` so we bypass HTTP and return ``builds`` once."""
        def fake_paginate(path, token, params=None, max_pages=20):
            return builds
        monkeypatch.setattr(ch, "_paginate", fake_paginate)

    def test_amd_queue_only_jobs_counted(self, monkeypatch, frozen_now):
        build = _build(jobs=[
            _job_finishing("mi250_1: foo", "amd_mi250_1", minutes_ago=30),
            _job_finishing("foo", "cpu_queue_postmerge", minutes_ago=30),
        ])
        self._install_fake(monkeypatch, [build])
        data = ch.collect_hotness("fake-token")
        queue_names = [q["queue"] for q in data["queues"]]
        assert "amd_mi250_1" in queue_names
        assert "cpu_queue_postmerge" not in queue_names

    def test_stuck_job_filtered(self, monkeypatch, frozen_now):
        build = _build(jobs=[
            _job_finishing("mi250_1: stuck", "amd_mi250_1", minutes_ago=60, duration_min=72*60),
        ])
        self._install_fake(monkeypatch, [build])
        data = ch.collect_hotness("fake-token")
        assert data["queues"] == []

    def test_fail_rate_computed(self, monkeypatch, frozen_now):
        build = _build(jobs=[
            _job_finishing("mi250_1: flaky", "amd_mi250_1", minutes_ago=120, state="passed"),
            _job_finishing("mi250_1: flaky", "amd_mi250_1", minutes_ago=90, state="failed"),
        ])
        self._install_fake(monkeypatch, [build])
        data = ch.collect_hotness("fake-token")
        flaky_rows = [g for g in data["test_groups"] if g["group"] == "flaky"]
        assert len(flaky_rows) == 1
        row = flaky_rows[0]
        assert row["count"] == 2
        assert row["failures"] == 1
        assert row["fail_rate"] == 0.5

    def test_branch_row_includes_commit_and_fork_url(self, monkeypatch, frozen_now):
        build = {
            "number": 2,
            "branch": "user/feature",
            "commit": "deadbeefdeadbeef",
            "source": "api",
            "created_at": _iso(_NOW - timedelta(hours=2)),
            "pipeline": {"slug": "amd-ci"},
            "pull_request": {"repository": "https://github.com/forkuser/vllm"},
            "jobs": [
                _job_finishing("mi250_1: foo", "amd_mi250_1", minutes_ago=30),
            ],
        }
        self._install_fake(monkeypatch, [build])
        data = ch.collect_hotness("fake-token")
        branch_rows = [b for b in data["branches"] if b["branch"] == "user/feature"]
        assert len(branch_rows) == 1
        assert branch_rows[0]["commit"] == "deadbeefdead"
        assert branch_rows[0]["fork_url"] == "https://github.com/forkuser/vllm"

    def test_output_schema_stable(self, monkeypatch, frozen_now):
        build = _build(jobs=[
            _job_finishing("mi250_1: foo", "amd_mi250_1", minutes_ago=30),
        ])
        self._install_fake(monkeypatch, [build])
        data = ch.collect_hotness("fake-token")
        for key in ("generated_at", "window_hours", "builds_examined",
                    "test_groups", "branches", "queues", "windows"):
            assert key in data
        json.dumps(data)  # must be JSON-serialisable


class TestWindowedAggregation:
    """The collector pre-computes one aggregation per window so the dashboard
    can switch between 1h / 3h / 24h / 72h without refetching."""

    def _install_fake(self, monkeypatch, builds):
        def fake_paginate(path, token, params=None, max_pages=20):
            return builds
        monkeypatch.setattr(ch, "_paginate", fake_paginate)

    def test_emits_all_declared_windows(self, monkeypatch, frozen_now):
        build = _build(jobs=[
            _job_finishing("mi250_1: foo", "amd_mi250_1", minutes_ago=30),
        ])
        self._install_fake(monkeypatch, [build])
        data = ch.collect_hotness("fake-token")
        expected = {f"{w}h" for w in ch.HOTNESS_WINDOWS_HOURS}
        assert expected.issubset(set(data["windows"].keys()))

    def test_default_window_matches_top_level(self, monkeypatch, frozen_now):
        # Top-level test_groups/branches/queues keys mirror the default window
        # (HOTNESS_WINDOW_HOURS) so older consumers keep working unchanged.
        build = _build(jobs=[
            _job_finishing("mi250_1: foo", "amd_mi250_1", minutes_ago=30),
            _job_finishing("mi250_1: bar", "amd_mi250_1", minutes_ago=120),
        ])
        self._install_fake(monkeypatch, [build])
        data = ch.collect_hotness("fake-token")
        default_key = f"{ch.HOTNESS_WINDOW_HOURS}h"
        assert data["test_groups"] == data["windows"][default_key]["test_groups"]
        assert data["branches"] == data["windows"][default_key]["branches"]
        assert data["queues"] == data["windows"][default_key]["queues"]

    def test_smaller_windows_exclude_older_jobs(self, monkeypatch, frozen_now):
        # One recent (30 min ago), one older (5 hours ago). 1h window sees
        # only the recent job; 24h sees both.
        build = _build(jobs=[
            _job_finishing("mi250_1: recent", "amd_mi250_1", minutes_ago=30),
            _job_finishing("mi250_1: older", "amd_mi250_1", minutes_ago=300),
        ])
        self._install_fake(monkeypatch, [build])
        data = ch.collect_hotness("fake-token")

        groups_1h = {g["group"] for g in data["windows"]["1h"]["test_groups"]}
        groups_24h = {g["group"] for g in data["windows"]["24h"]["test_groups"]}
        assert groups_1h == {"recent"}
        assert groups_24h == {"recent", "older"}

    def test_window_boundary_respected_even_for_failures(self, monkeypatch, frozen_now):
        # A failure older than the window should not count toward its fail_rate.
        build = _build(jobs=[
            _job_finishing("mi250_1: flaky", "amd_mi250_1", minutes_ago=30, state="passed"),
            _job_finishing("mi250_1: flaky", "amd_mi250_1", minutes_ago=120, state="failed"),
        ])
        self._install_fake(monkeypatch, [build])
        data = ch.collect_hotness("fake-token")
        # 1h window: only the passed job within cutoff → 0 failures.
        flaky_1h = [g for g in data["windows"]["1h"]["test_groups"] if g["group"] == "flaky"]
        assert len(flaky_1h) == 1
        assert flaky_1h[0]["count"] == 1
        assert flaky_1h[0]["failures"] == 0
        assert flaky_1h[0]["fail_rate"] == 0.0
        # 3h window: both jobs visible → 1/2 failure rate.
        flaky_3h = [g for g in data["windows"]["3h"]["test_groups"] if g["group"] == "flaky"]
        assert flaky_3h[0]["count"] == 2
        assert flaky_3h[0]["failures"] == 1
        assert flaky_3h[0]["fail_rate"] == 0.5

    def test_window_entry_records_window_hours_and_jobs_counted(self, monkeypatch, frozen_now):
        build = _build(jobs=[
            _job_finishing("mi250_1: foo", "amd_mi250_1", minutes_ago=30),
            _job_finishing("mi250_1: bar", "amd_mi250_1", minutes_ago=30),
            _job_finishing("mi250_1: baz", "amd_mi250_1", minutes_ago=400),  # outside 3h
        ])
        self._install_fake(monkeypatch, [build])
        data = ch.collect_hotness("fake-token")
        w3 = data["windows"]["3h"]
        assert w3["window_hours"] == 3
        assert w3["jobs_in_window"] == 2

    def test_buildkite_api_queried_once(self, monkeypatch, frozen_now):
        # Multiple windows → one pass over the widest cutoff → _paginate runs
        # exactly once per pipeline slug. Refetching per window would waste
        # API budget and leave windows inconsistent with each other.
        calls = []

        def counting_paginate(path, token, params=None, max_pages=20):
            calls.append(path)
            return []
        monkeypatch.setattr(ch, "_paginate", counting_paginate)
        ch.collect_hotness("fake-token")
        assert len(calls) == len(ch.AMD_PIPELINES)
