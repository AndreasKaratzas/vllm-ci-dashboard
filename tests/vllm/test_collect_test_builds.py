"""Tests for ``scripts/vllm/collect_test_builds.py``'s comparison logic.

The full ``main()`` pipeline hits Buildkite — that belongs in an integration
test, not here. These tests drive the pure-python comparison function
(``_compare``) which powers the "nightly vs test build" diff that the Test
Build dashboard tab shows.

The comparison has four subtle classifications the team lead cared about:

    - common_pass    : green both sides
    - common_fail    : red both sides (still broken)
    - new_fail       : regression (pass on nightly, fail on test build)
    - new_pass       : fix (fail on nightly, pass on test build)

If any of these buckets drifts, the "fixed/regressed" counts on the tab
become wrong and reviewers stop trusting the diff.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vllm import collect_test_builds as ctb
from vllm.ci.models import TestResult


def _tr(test_id: str, status: str, duration: float = 1.0, job: str = "mi250_1: G") -> TestResult:
    cls, _, name = test_id.partition("::")
    return TestResult(
        test_id=test_id,
        name=name,
        classname=cls,
        status=status,
        duration_secs=duration,
        failure_message="",
        job_name=job,
        job_id="",
        step_id="",
        build_number=1,
        pipeline="amd-ci",
        date="2026-04-18",
    )


class TestStatusBucket:
    def test_pass_statuses(self):
        assert ctb._status_bucket("passed") == "pass"
        assert ctb._status_bucket("xpassed") == "pass"
        assert ctb._status_bucket("PASSED") == "pass"

    def test_fail_statuses(self):
        for s in ("failed", "error", "broken", "timed_out", "xfailed"):
            assert ctb._status_bucket(s) == "fail"

    def test_other_statuses(self):
        for s in ("skipped", "canceled", "", None):
            assert ctb._status_bucket(s or "") == "other"


class TestGroupName:
    def test_strips_hardware_prefix(self):
        t = _tr("mod.test::case", "passed", job="mi250_1: Some Group")
        assert ctb._group_name(t) == "Some Group"

    def test_falls_back_to_classname_when_job_empty(self):
        t = _tr("cls::name", "passed", job="")
        t.classname = "mi250_1: fallback"
        assert ctb._group_name(t) == "fallback"

    def test_unknown_when_both_empty(self):
        t = _tr("cls::name", "passed", job="")
        t.classname = ""
        assert ctb._group_name(t) == "unknown"


class TestCompareSummary:
    def test_common_pass_counted(self):
        cur = [_tr("a::1", "passed"), _tr("b::1", "passed")]
        base = [_tr("a::1", "passed"), _tr("b::1", "passed")]
        out = ctb._compare(cur, base)
        s = out["summary"]
        assert s["common_pass"] == 2
        assert s["new_fail"] == 0 and s["new_pass"] == 0

    def test_new_fail_is_regression(self):
        cur = [_tr("a::1", "failed")]    # now broken
        base = [_tr("a::1", "passed")]   # was green
        out = ctb._compare(cur, base)
        assert out["summary"]["new_fail"] == 1
        assert out["summary"]["common_pass"] == 0
        assert "a::1" in out["new_fail_tests"]

    def test_new_pass_is_fix(self):
        cur = [_tr("a::1", "passed")]    # fixed
        base = [_tr("a::1", "failed")]   # was broken
        out = ctb._compare(cur, base)
        assert out["summary"]["new_pass"] == 1
        assert "a::1" in out["new_pass_tests"]

    def test_common_fail(self):
        cur = [_tr("a::1", "failed")]
        base = [_tr("a::1", "failed")]
        out = ctb._compare(cur, base)
        assert out["summary"]["common_fail"] == 1

    def test_only_in_test(self):
        cur = [_tr("only-here::1", "passed")]
        base = []
        out = ctb._compare(cur, base)
        assert out["summary"]["only_in_test"] == 1
        assert out["summary"]["only_in_nightly"] == 0

    def test_only_in_nightly(self):
        cur = []
        base = [_tr("vanished::1", "passed")]
        out = ctb._compare(cur, base)
        assert out["summary"]["only_in_nightly"] == 1
        assert out["summary"]["only_in_test"] == 0

    def test_totals_match_inputs(self):
        cur = [_tr(f"t::{i}", "passed") for i in range(5)]
        base = [_tr(f"t::{i}", "failed") for i in range(7)]
        out = ctb._compare(cur, base)
        assert out["summary"]["test_total"] == 5
        assert out["summary"]["nightly_total"] == 7

    def test_skipped_not_counted_as_fail_or_pass(self):
        # Skipped is "other" — must not show up in either common_pass or new_fail.
        cur = [_tr("a::1", "skipped")]
        base = [_tr("a::1", "passed")]
        out = ctb._compare(cur, base)
        assert out["summary"]["new_fail"] == 0
        assert out["summary"]["common_pass"] == 0


class TestCompareGroups:
    def test_per_group_counts(self):
        cur = [
            _tr("a::1", "passed", job="mi250_1: GroupA"),
            _tr("a::2", "failed", job="mi250_1: GroupA"),
            _tr("b::1", "passed", job="mi250_1: GroupB"),
        ]
        base = [
            _tr("a::1", "passed", job="mi250_1: GroupA"),
            _tr("a::2", "passed", job="mi250_1: GroupA"),
            _tr("b::1", "passed", job="mi250_1: GroupB"),
        ]
        out = ctb._compare(cur, base)
        groups = {g["group"]: g for g in out["groups"]}
        assert set(groups.keys()) == {"GroupA", "GroupB"}
        assert groups["GroupA"]["test_pass"] == 1
        assert groups["GroupA"]["test_fail"] == 1
        assert groups["GroupA"]["new_fail"] == 1  # a::2 regressed
        assert groups["GroupB"]["test_pass"] == 1
        assert groups["GroupB"]["new_fail"] == 0

    def test_duration_delta_is_test_minus_nightly(self):
        cur = [_tr("a::1", "passed", duration=10.0)]
        base = [_tr("a::1", "passed", duration=4.0)]
        out = ctb._compare(cur, base)
        [g] = out["groups"]
        assert g["duration_delta"] == pytest.approx(6.0)
        assert g["test_duration"] == pytest.approx(10.0)
        assert g["nightly_duration"] == pytest.approx(4.0)

    def test_new_pass_counted_per_group(self):
        cur = [_tr("a::1", "passed", job="mi250_1: GroupA")]
        base = [_tr("a::1", "failed", job="mi250_1: GroupA")]
        out = ctb._compare(cur, base)
        [g] = out["groups"]
        assert g["new_pass"] == 1


class TestCommitDate:
    def test_parses_z_suffix(self):
        assert ctb._commit_date("2026-04-18T10:00:00Z") == "2026-04-18"

    def test_parses_offset(self):
        assert ctb._commit_date("2026-04-18T10:00:00+00:00") == "2026-04-18"

    def test_empty_falls_back_to_today(self):
        # Should not crash on empty; some value consistent with today's date.
        out = ctb._commit_date("")
        assert out[:4].isdigit() and len(out) == 10


class TestNightlyResolver:
    def test_exact_date_match(self, tmp_path, monkeypatch):
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        target = results_dir / "2026-04-18_amd.jsonl"
        row = {
            "test_id": "a::1", "name": "1", "classname": "a", "status": "passed",
            "duration_secs": 0.1, "failure_message": "", "job_name": "mi250_1: G",
            "job_id": "", "step_id": "", "build_number": 1, "pipeline": "amd-ci",
            "date": "2026-04-18",
        }
        import json
        target.write_text(json.dumps(row) + "\n")
        monkeypatch.setattr(ctb, "NIGHTLY_RESULTS_DIR", results_dir, raising=False)

        resolved, rows = ctb._find_nightly_results("2026-04-18")
        assert resolved == "2026-04-18"
        assert [r.test_id for r in rows] == ["a::1"]

    def test_falls_back_to_earlier(self, tmp_path, monkeypatch):
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        (results_dir / "2026-04-15_amd.jsonl").write_text("")  # empty earlier file
        monkeypatch.setattr(ctb, "NIGHTLY_RESULTS_DIR", results_dir, raising=False)
        resolved, rows = ctb._find_nightly_results("2026-04-18")
        assert resolved == "2026-04-15"
        assert rows == []

    def test_empty_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ctb, "NIGHTLY_RESULTS_DIR", tmp_path, raising=False)
        resolved, rows = ctb._find_nightly_results("2026-04-18")
        assert resolved == ""
        assert rows == []
