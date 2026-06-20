"""Tests for the data shapes the dashboard's trend charts depend on.

The CI Workload Trajectory tab renders one Chart.js chart:

  - AMD queue load over 72h (line) from ``queue_timeseries.jsonl``:
    sums ``queues[q].{waiting, running}`` for every queue whose name
    starts with ``amd`` (case-insensitive).

The chart itself runs in the browser — no JS test infrastructure in
this repo — so these tests pin the *data contract* the JS relies on.
If a collector renames or drops any of these keys, the chart silently
breaks; this test fails loudly before that ever ships.

We do two things:

  * Parse the real committed data file and assert it matches the
    shape the JS expects, end to end.
  * Re-implement the JS reducer in Python against a pinned realistic
    fixture, so a future refactor of ``ci-hotness.js`` that changes
    what fields it reads fails here instead of in production.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
QUEUE_TS = ROOT / "data" / "vllm" / "ci" / "queue_timeseries.jsonl"
JS_HOTNESS = ROOT / "docs" / "assets" / "js" / "ci-hotness.js"


# ---------------------------------------------------------------------------
# 1. Real committed data satisfies the JS contract.
# ---------------------------------------------------------------------------


class TestQueueTimeseriesMatchesChartContract:
    """``loadQueueTimeseries()`` in ci-hotness.js expects newline-delimited
    JSON where each row has:

        { ts: <ISO-8601>, queues: { <qname>: { waiting, running, ... } } }

    and the chart filters to queues whose name starts with ``amd``.
    """

    def _load_or_skip(self):
        if not QUEUE_TS.exists():
            pytest.skip("queue_timeseries.jsonl not generated yet")
        rows = []
        for line in QUEUE_TS.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # The JS reducer silently skips malformed lines. Still OK at
                # data layer — but a single valid row is required to render.
                continue
        if not rows:
            pytest.skip("no valid rows in queue_timeseries.jsonl yet")
        return rows

    def test_every_row_has_ts_and_queues(self):
        rows = self._load_or_skip()
        for i, row in enumerate(rows[:500]):  # cap for speed on big logs
            assert "ts" in row, f"row {i} missing ts — chart plots by ts"
            assert "queues" in row, f"row {i} missing queues"
            assert isinstance(row["queues"], dict), (
                f"row {i}.queues must be a dict keyed by queue name"
            )

    def test_at_least_one_amd_queue_has_waiting_and_running(self):
        rows = self._load_or_skip()
        found = False
        for row in rows[-200:]:  # inspect the tail — the 72h window the chart plots
            for qname, q in (row.get("queues") or {}).items():
                if not qname.lower().startswith("amd"):
                    continue
                # The JS reducer defaults missing keys to 0, but if the field
                # is always absent the chart is flatlined, which is the
                # regression we care about pinning.
                if "waiting" in q and "running" in q:
                    found = True
                    assert isinstance(q["waiting"], (int, float))
                    assert isinstance(q["running"], (int, float))
                    break
            if found:
                break
        assert found, (
            "No recent row has an amd_* queue with both waiting and running "
            "— the AMD queue load chart will render as a flat line. Check "
            "collect_queue_snapshot.py."
        )


# ---------------------------------------------------------------------------
# 2. Python re-implementation of the JS reducer against a realistic
#    fixture. Locks in the field names the JS reads.
# ---------------------------------------------------------------------------

QUEUE_TIMESERIES_FIXTURE = [
    {
        "ts": "2026-04-18T10:00:00Z",
        "queues": {
            "amd_mi250_1": {"waiting": 5, "running": 3},
            "amd_mi300_4": {"waiting": 2, "running": 7},
            # non-AMD queue — must be ignored by the chart reducer.
            "gpu_1_queue": {"waiting": 99, "running": 99},
        },
    },
    {
        "ts": "2026-04-18T10:30:00Z",
        "queues": {
            "amd_mi250_1": {"waiting": 0, "running": 8},
            # missing "running" key — JS reducer defaults it to 0.
            "amd_mi300_4": {"waiting": 1},
        },
    },
]


def _js_amd_queue_reducer(rows: list) -> tuple[list[str], list[int], list[int]]:
    """Python mirror of the JS reducer in loadQueueTimeseries().then() —
    sum waiting/running across amd_* queues per row.

    Returns (timestamps, waiting_series, running_series) in input order.
    """
    labels, waiting, running = [], [], []
    for row in rows:
        qs = row.get("queues") or {}
        w = r = 0
        for qname, q in qs.items():
            if not qname.lower().startswith("amd"):
                continue
            w += q.get("waiting", 0) or 0
            r += q.get("running", 0) or 0
        labels.append(row["ts"])
        waiting.append(w)
        running.append(r)
    return labels, waiting, running


class TestAmdQueueReducer:
    def test_sums_only_amd_queues(self):
        labels, waiting, running = _js_amd_queue_reducer(QUEUE_TIMESERIES_FIXTURE)
        assert labels == ["2026-04-18T10:00:00Z", "2026-04-18T10:30:00Z"]
        # row 0: amd_mi250_1 (5, 3) + amd_mi300_4 (2, 7) = (7, 10);
        # gpu_1_queue (99, 99) must be excluded.
        assert waiting == [7, 1]   # row 1: 0 + 1 = 1
        assert running == [10, 8]  # row 1: 8 + missing-defaulted-0 = 8

    def test_missing_waiting_or_running_defaults_to_zero(self):
        rows = [{"ts": "T", "queues": {"amd_q": {}}}]
        labels, waiting, running = _js_amd_queue_reducer(rows)
        assert labels == ["T"]
        assert waiting == [0]
        assert running == [0]

    def test_case_insensitive_amd_prefix(self):
        rows = [{"ts": "T", "queues": {"AMD_BIG": {"waiting": 4, "running": 5}}}]
        _, waiting, running = _js_amd_queue_reducer(rows)
        assert waiting == [4]
        assert running == [5]

    def test_no_amd_queues_yields_zeros_not_empty(self):
        # Important: the chart still needs a data point at each ts so the
        # x-axis aligns. A row with no AMD queues should produce a 0, not
        # be dropped.
        rows = [{"ts": "T", "queues": {"gpu_q": {"waiting": 3, "running": 5}}}]
        labels, waiting, running = _js_amd_queue_reducer(rows)
        assert labels == ["T"]
        assert waiting == [0]
        assert running == [0]


# ---------------------------------------------------------------------------
# 3. The JS source still references the keys the reducer reads.
# ---------------------------------------------------------------------------


class TestCiHotnessJsReadsExpectedFields:
    """Defence against a refactor that silently renames a field read from
    queue_timeseries.jsonl or hotness.json. If one of these substrings
    disappears, someone changed the shape the chart depends on — update
    the collector or the chart together."""

    def test_references_queue_timeseries_keys(self):
        src = JS_HOTNESS.read_text()
        assert "row.queues" in src or ".queues" in src
        assert ".waiting" in src
        assert ".running" in src
        assert "row.ts" in src or ".ts" in src

    def test_references_windowed_hotness_keys(self):
        # Window selector + per-window aggregations are load-bearing for the
        # tab; a rename of these keys would silently break the timeframe UI.
        src = JS_HOTNESS.read_text()
        for field in ("data.windows", "test_groups", "branches", "queues"):
            assert field in src, (
                f"ci-hotness.js no longer references {field!r} — did "
                "collect_hotness.py change its output shape?"
            )
