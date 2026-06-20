"""Unit tests for scripts/vllm/ci/utils.py — the shared CI collector primitives.

These helpers are exercised by every collector (queue snapshot, hotness,
analytics) so bugs here ripple everywhere. Cover the edge cases:

- ``parse_iso`` must accept the ``Z`` suffix and return ``None`` on junk
- ``percentile`` must match the index-based shape the legacy timeseries expects
- ``classify_workload`` must give queue > branch > default precedence
- ``hardware_from_job_name`` must prefer the job-name prefix over the queue hint
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from vllm.ci.utils import (
    classify_workload,
    duration_mins,
    hardware_from_job_name,
    parse_iso,
    percentile,
    queue_from_rules,
)


class TestParseIso:
    def test_z_suffix(self):
        dt = parse_iso("2026-04-18T10:11:12Z")
        assert dt == datetime(2026, 4, 18, 10, 11, 12, tzinfo=timezone.utc)

    def test_offset(self):
        dt = parse_iso("2026-04-18T10:11:12+00:00")
        assert dt is not None and dt.tzinfo is not None

    @pytest.mark.parametrize("bad", ["", None, "not-a-date", "abc"])
    def test_bad_input_returns_none(self, bad):
        assert parse_iso(bad) is None


class TestDurationMins:
    def test_happy_path(self):
        assert duration_mins("2026-04-18T00:00:00Z", "2026-04-18T00:30:00Z") == 30.0

    def test_fractional_minutes_rounded_to_one_decimal(self):
        assert duration_mins("2026-04-18T00:00:00Z", "2026-04-18T00:00:45Z") == 0.8

    def test_missing_start_returns_none(self):
        assert duration_mins(None, "2026-04-18T00:00:00Z") is None

    def test_missing_end_returns_none(self):
        assert duration_mins("2026-04-18T00:00:00Z", None) is None


class TestPercentile:
    def test_empty_returns_zero(self):
        assert percentile([], 50) == 0.0

    def test_single_value(self):
        assert percentile([42], 50) == 42
        assert percentile([42], 99) == 42

    def test_index_based_p90(self):
        # len=10, idx = int(10 * 90 / 100) = 9 → values[9] = 10
        assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90) == 10

    def test_p50_matches_legacy_snapshot(self):
        # len=4, idx = int(4 * 50 / 100) = 2 → values[2]
        assert percentile([1, 2, 3, 4], 50) == 3

    def test_p100_clamps_to_last(self):
        assert percentile([1, 2, 3], 100) == 3


class TestQueueFromRules:
    def test_finds_queue_tag(self):
        assert queue_from_rules(["os=linux", "queue=amd_mi250_1"]) == "amd_mi250_1"

    def test_returns_none_when_absent(self):
        assert queue_from_rules(["os=linux"]) is None

    def test_none_input(self):
        assert queue_from_rules(None) is None

    def test_empty_list(self):
        assert queue_from_rules([]) is None


class TestClassifyWorkload:
    def test_vllm_default(self):
        assert classify_workload("amd-ci", "main") == "vllm"

    def test_omni_from_branch(self):
        assert classify_workload("amd-ci", "user/omni-feature") == "omni"

    def test_omni_from_queue_wins_over_vllm_branch(self):
        # Queue suffix is the strongest signal.
        assert classify_workload("amd-ci", "main", "intel-gpu-omni") == "omni"

    def test_case_insensitive(self):
        assert classify_workload("amd-ci", "OMNI/foo") == "omni"
        assert classify_workload("OMNI-ci", "main") == "omni"

    def test_empty_queue_still_classifies(self):
        assert classify_workload("amd-ci", "omni-branch", "") == "omni"


class TestHardwareFromJobName:
    def test_prefix_wins_over_queue(self):
        assert hardware_from_job_name("mi325_4: V1 e2e", "amd_mi250_1") == "mi325"

    def test_b_variant_preserved(self):
        assert hardware_from_job_name("mi355B_8: foo", None) == "mi355b"

    def test_queue_fallback(self):
        assert hardware_from_job_name("no-prefix here", "amd_mi250_1") == "mi250"

    def test_unknown_when_neither(self):
        assert hardware_from_job_name("cpu: build", "cpu_queue_postmerge") == "unknown"

    def test_empty_inputs(self):
        assert hardware_from_job_name("", None) == "unknown"
