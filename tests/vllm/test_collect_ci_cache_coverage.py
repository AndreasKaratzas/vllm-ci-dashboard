"""Regression tests for the cache-coverage check in collect_ci.py.

The AMD nightly build can flip to ``state="passed"`` while one or more
``soft_fail: true`` jobs are still running — the build doesn't wait on
soft-fail jobs to block completion. Concrete incident that motivated this:

    build 7791, job "mi250_1: Basic Models Tests (Other)":
        state=timed_out  soft_failed=true  retries_count=2
        started 12:30 UTC, finished 15:31 UTC (timeout at 3h)

    The previous collector pass ran at ~04:46 UTC (before the job even
    started). It wrote a partial jsonl without this job. When the next
    collector pass eventually ran (with build now state=passed), the old
    cache-skip logic saw ``date in existing_dates and state in
    TERMINAL_STATES`` and skipped. Result: the timed-out job was
    permanently missing, parity_report.json recorded ``amd=None`` for
    ``basic models tests (other)``, and the dashboard's "Failing Tests"
    filter (which requires ``g.amd.failed > 0``) dropped the group
    from the count — 9 shown, 10 actually soft-failed on Buildkite.

These tests exercise the ``_cache_covers_all_jobs`` / ``_cached_job_names``
helpers directly, without hitting Buildkite. The rule being locked in:
for the newest nightly, *cache-skip is only valid if every test job currently
visible in the build has at least one record in the cached jsonl*. Historical
cached builds are trusted; re-fetching old complete Buildkite logs is slow and
can rate-limit hard enough to block publication of the latest snapshot.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from collect_ci import (  # noqa: E402
    _cache_covers_all_jobs,
    _cached_build_numbers,
    _cached_job_ids,
    _cached_job_names,
    _compact_amd_build_snapshot,
    _completed_result_entries,
    _find_false_normalization_merges,
    _find_missing_parity_groups,
    _extend_parity_side_hardware,
    _is_complete_nightly_build,
    _select_shard_evidence_build,
    _select_latest_complete_evidence_build,
    _shard_catalog_evidence,
    _is_parity_excluded_group,
    _should_verify_cache_coverage,
    collect_pipeline,
    write_amd_nightly_snapshot,
)
from vllm.ci.models import TestResult  # noqa: E402
from vllm.ci import reporter as reporter_module  # noqa: E402
from vllm.ci.reporter import prune_old_results  # noqa: E402


def _job(name: str, state: str = "passed", soft_failed: bool = False) -> dict:
    return {
        "type": "script",
        "name": name,
        "state": state,
        "soft_failed": soft_failed,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _record(job_name: str, build_num: int = 7791, job_id: str = "") -> dict:
    return {
        "test_id": f"{job_name}::__passed__",
        "name": "__passed__ (1)",
        "classname": job_name,
        "status": "passed",
        "duration_secs": 1.0,
        "failure_message": "",
        "job_name": job_name,
        "job_id": job_id,
        "step_id": "",
        "build_number": build_num,
        "pipeline": "amd-ci",
        "date": "2026-04-18",
    }


def test_parity_side_hardware_extends_even_when_merged_hardware_already_exists():
    group = {
        "hardware": ["mi300"],
        "amd_hardware": ["mi300"],
        "upstream_hardware": [],
    }

    added = _extend_parity_side_hardware(group, "upstream", {"mi300"})

    assert added == {"mi300"}
    assert group["amd_hardware"] == ["mi300"]
    assert group["upstream_hardware"] == ["mi300"]
    assert group["hardware"] == ["mi300"]


class TestCachedJobNames:
    def test_empty_when_file_missing(self, tmp_path):
        # No cache file means no coverage — the collector must re-fetch.
        names = _cached_job_names(tmp_path / "missing.jsonl", 7791)
        assert names == set()

    def test_returns_distinct_job_names(self, tmp_path):
        path = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(path, [
            _record("mi250_1: LoRA"),
            _record("mi250_1: LoRA"),           # duplicate row — dedupes
            _record("mi250_1: OpenAI API correctness"),
            _record("mi250_1: V1 Sample + Logits"),
        ])
        assert _cached_job_names(path, 7791) == {
            "mi250_1: LoRA",
            "mi250_1: OpenAI API correctness",
            "mi250_1: V1 Sample + Logits",
        }

    def test_ignores_other_build_numbers(self, tmp_path):
        # A date collision between builds must not make the current build
        # look more covered than it actually is.
        path = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(path, [
            _record("mi250_1: LoRA", build_num=7791),
            _record("mi250_1: SomethingElse", build_num=7777),  # different build
        ])
        assert _cached_job_names(path, 7791) == {"mi250_1: LoRA"}

    def test_skips_malformed_lines(self, tmp_path):
        path = tmp_path / "2026-04-18_amd.jsonl"
        path.write_text(
            json.dumps(_record("mi250_1: LoRA")) + "\n"
            "not valid json\n"
            "\n"
            + json.dumps(_record("mi250_1: OpenAI API")) + "\n"
        )
        assert _cached_job_names(path, 7791) == {
            "mi250_1: LoRA",
            "mi250_1: OpenAI API",
        }

    def test_returns_exact_job_attempt_ids(self, tmp_path):
        path = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(path, [
            _record("mi250_1: LoRA", job_id="attempt-a"),
            _record("mi250_1: LoRA", job_id="attempt-a"),
            _record("mi250_1: LoRA", job_id="attempt-b"),
            _record("mi250_1: Other build", build_num=7777, job_id="other"),
        ])

        assert _cached_job_ids(path, 7791) == {"attempt-a", "attempt-b"}

    def test_returns_positive_build_numbers(self, tmp_path):
        path = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(path, [
            _record("mi250_1: LoRA", build_num=7791),
            _record("mi250_1: Other", build_num=7777),
            {"build_number": "bad"},
        ])

        assert _cached_build_numbers(path) == {7777, 7791}


class TestParityCandidateExclusions:
    def test_amd_prefixed_upstream_control_jobs_are_not_parity_groups(self):
        assert _is_parity_excluded_group("amd: engine (1 gpu) (mi325_1)")

    def test_real_gpu_queue_groups_remain_parity_candidates(self):
        assert not _is_parity_excluded_group("mi325_1: engine (1 gpu)")


class TestParityCollectorValidation:
    @staticmethod
    def _result(job_name: str):
        return type("Result", (), {"job_name": job_name})()

    def test_cross_hardware_variants_are_not_false_merges(self):
        results = [
            self._result("mi250_1: Engine (1 GPU)"),
            self._result("mi300_1: Engine (1 GPU)"),
        ]
        assert _find_false_normalization_merges(results) == []

    def test_same_hardware_non_shard_merge_is_reported(self):
        results = [
            self._result("mi250_1: Engine (1 GPU)"),
            self._result("mi250_1: Engine (1 GPU) # duplicate"),
        ]
        false_merges = _find_false_normalization_merges(results)
        assert len(false_merges) == 1
        assert false_merges[0][0:2] == ("mi250", "engine (1 gpu)")

    def test_configured_same_hardware_shards_are_not_false_merges(self):
        results = [
            self._result("mi300_1: LoRA 0"),
            self._result("mi300_1: LoRA 1"),
        ]
        assert _find_false_normalization_merges(results) == []

    def test_missing_group_check_uses_only_supplied_current_cohort(self):
        current = [self._result("mi300_1: Current Group")]
        parity = {"job_groups": [{"name": "current group"}]}
        assert _find_missing_parity_groups(current, parity) == []
        assert _find_missing_parity_groups(current, {"job_groups": []}) == [
            "current group",
        ]


class TestCacheCoversAllJobs:
    def test_only_latest_build_forces_cache_coverage_verification(self):
        assert _should_verify_cache_coverage(8193, 8193) is True
        assert _should_verify_cache_coverage(64187, 64258) is False
        assert _should_verify_cache_coverage(64187, 64258, 64187) is True


    @staticmethod
    def _build(number: int, state: str, job_state: str, commit: str = "a" * 40):
        return {
            "number": number,
            "state": state,
            "commit": commit,
            "created_at": f"2026-08-{number - 100:02d}T09:00:00Z",
            "jobs": [_job("mi300_1: Model tests", state=job_state)],
        }

    def test_soft_job_still_running_makes_terminal_build_provisional(self):
        build = self._build(112, "passed", "running")
        assert not _is_complete_nightly_build(build)

    def test_selects_previous_complete_build_when_latest_is_running(self):
        latest = self._build(112, "running", "running")
        previous = self._build(111, "passed", "passed")
        results = {112: [object()], 111: [object()]}

        selected = _select_latest_complete_evidence_build(
            [latest, previous], results
        )

        assert selected is previous

    def test_shard_catalog_uses_latest_build_as_explicit_provisional_evidence(self):
        running = self._build(112, "running", "running")

        selected, verified_complete = _select_shard_evidence_build([running], {})
        evidence = _shard_catalog_evidence(
            selected,
            verified_complete=verified_complete,
        )

        assert selected is running
        assert evidence == {
            "pipeline": "amd",
            "build_number": 112,
            "build_commit": "a" * 40,
            "build_state": "running",
            "roster_complete": False,
            "result_file": "",
            "job_names": ["mi300_1: Model tests"],
        }

    def test_shard_catalog_evidence_is_present_when_no_build_is_available(self):
        selected, verified_complete = _select_shard_evidence_build([], {})

        assert selected is None
        assert _shard_catalog_evidence(
            selected,
            verified_complete=verified_complete,
        ) == {
            "pipeline": "amd",
            "build_number": 0,
            "build_commit": "",
            "build_state": "unavailable",
            "roster_complete": False,
            "result_file": "",
            "job_names": [],
        }

    def test_shard_catalog_prefers_verified_complete_evidence(self):
        latest = self._build(112, "running", "running")
        previous = self._build(111, "passed", "passed")

        selected, verified_complete = _select_shard_evidence_build(
            [latest, previous],
            {111: [object()]},
        )

        assert selected is previous
        assert verified_complete is True
        assert _shard_catalog_evidence(
            selected,
            verified_complete=verified_complete,
        )["result_file"] == "2026-08-11_amd.jsonl"

    def test_nonterminal_results_are_excluded_from_canonical_analysis(self):
        latest = self._build(112, "running", "running")
        previous = self._build(111, "passed", "passed")
        entries = [
            (111, "2026-08-11", [object()]),
            (112, "2026-08-12", [object()]),
        ]

        assert _completed_result_entries(entries, [latest, previous]) == [entries[0]]

    def test_cache_complete_skips(self, tmp_path):
        # All 3 current jobs are in the cache → cache is complete → True.
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [
            _record("mi250_1: LoRA"),
            _record("mi250_1: OpenAI API correctness"),
            _record("mi250_1: Basic Models Tests (Other)"),
        ])
        build = {
            "state": "passed",
            "jobs": [
                _job("mi250_1: LoRA"),
                _job("mi250_1: OpenAI API correctness"),
                _job("mi250_1: Basic Models Tests (Other)",
                     state="timed_out", soft_failed=True),
            ],
        }
        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is True

    def test_same_name_new_retry_attempt_triggers_refetch(self, tmp_path):
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [
            _record("mi250_1: LoRA", job_id="original-attempt"),
        ])
        build = {
            "number": 7791,
            "state": "passed",
            "jobs": [
                {
                    **_job("mi250_1: LoRA"),
                    "id": "retry-attempt",
                }
            ],
        }

        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is False

    def test_exact_job_attempt_id_allows_cache_skip(self, tmp_path):
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [
            _record("mi250_1: LoRA", job_id="current-attempt"),
        ])
        build = {
            "number": 7791,
            "state": "passed",
            "jobs": [
                {
                    **_job("mi250_1: LoRA"),
                    "id": "current-attempt",
                }
            ],
        }

        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is True

    @pytest.mark.parametrize(
        "retry_state",
        ["expired", "not_run", "skipped", "waiting_failed", "blocked"],
    )
    def test_nonparseable_terminal_retry_evicts_superseded_attempt(
        self,
        tmp_path,
        retry_state,
    ):
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [
            _record("mi250_1: LoRA", job_id="original-attempt"),
        ])
        build = {
            "number": 7791,
            "state": "passed",
            "jobs": [
                {
                    **_job("mi250_1: LoRA", state="failed"),
                    "id": "original-attempt",
                    "retried_in_job_id": "retry-attempt",
                },
                {
                    **_job("mi250_1: LoRA", state=retry_state),
                    "id": "retry-attempt",
                },
            ],
        }

        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is False

        # After the refresh removes the superseded row, a nonparseable
        # terminal attempt is covered without forcing another refetch.
        _write_jsonl(jsonl, [])
        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is True

    def test_cache_missing_soft_fail_timeout_triggers_refetch(self, tmp_path):
        # Exact shape of the build-7791 incident: the cache has the jobs
        # that finished before the cache was written, but NOT the soft-fail
        # that timed out hours later. Must return False so collector
        # re-fetches.
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [
            _record("mi250_1: LoRA"),
            _record("mi250_1: OpenAI API correctness"),
            # NB: "Basic Models Tests (Other)" is absent here
        ])
        build = {
            "state": "passed",
            "jobs": [
                _job("mi250_1: LoRA"),
                _job("mi250_1: OpenAI API correctness"),
                _job("mi250_1: Basic Models Tests (Other)",
                     state="timed_out", soft_failed=True),
            ],
        }
        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is False

    def test_skip_patterns_not_counted(self, tmp_path):
        # bootstrap / docker / build image / upload jobs are filtered from
        # the collector's parse path, so they must not count as "missing"
        # from the cache either — otherwise every cached build would look
        # incomplete and we'd re-fetch on every cron tick.
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [
            _record("mi250_1: LoRA"),
        ])
        build = {
            "state": "passed",
            "jobs": [
                _job("mi250_1: LoRA"),
                _job("bootstrap"),           # skipped
                _job("docker build image"),  # skipped
                _job("upload artifacts"),    # skipped
            ],
        }
        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is True

    def test_active_nonterminal_retry_makes_cache_provisional(self, tmp_path):
        # ``fetch_build_jobs`` excludes superseded and nonterminal attempts
        # from parsing, but the full roster must still be complete before a
        # cached canonical result can be reused.
        from vllm.ci.buildkite_client import fetch_build_jobs

        build = {
            "jobs": [
                {"type": "script", "name": "mi250_1: LoRA",
                 "state": "passed"},
                # Superseded retry — fetch_build_jobs must drop this.
                {"type": "script", "name": "mi250_1: Superseded",
                 "state": "failed",
                 "retried_in_job_id": "abc-123"},
                # Still running — fetch_build_jobs must drop this too.
                {"type": "script", "name": "mi250_1: StillRunning",
                 "state": "running"},
            ],
        }
        surviving = {j["name"] for j in fetch_build_jobs(build)}
        assert surviving == {"mi250_1: LoRA"}

        # Cache coverage alone is insufficient while an active retry remains
        # nonterminal: the canonical JSONL must be invalidated until it ends.
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [_record("mi250_1: LoRA")])
        build["state"] = "passed"
        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is False

    def test_empty_cache_is_incomplete_when_jobs_exist(self, tmp_path):
        jsonl = tmp_path / "2026-04-18_amd.jsonl"  # not created
        build = {"jobs": [_job("mi250_1: LoRA")]}
        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is False

    def test_empty_build_jobs_trusts_cache(self, tmp_path):
        # Pathological but defensive: if the build has no test jobs at all
        # (e.g. a pipeline-upload-only build) the cache trivially covers
        # it. Must not thrash by returning False on an empty set diff.
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [])
        build = {"jobs": []}
        assert _cache_covers_all_jobs(build, jsonl, "amd", 7791) is True

    def test_fetches_detail_when_jobs_missing_from_summary(self, tmp_path):
        # ``fetch_nightly_builds`` sometimes returns summaries without the
        # ``jobs`` array. The helper must fetch full build detail in that
        # case rather than silently treating "no jobs visible" as covered.
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [_record("mi250_1: LoRA")])
        summary_only_build = {"number": 7791}  # no "jobs" key
        full_detail = {
            "number": 7791,
            "jobs": [
                _job("mi250_1: LoRA"),
                _job("mi250_1: Basic Models Tests (Other)",
                     state="timed_out", soft_failed=True),
            ],
        }
        with patch("collect_ci.fetch_build_detail", return_value=full_detail) as m:
            assert _cache_covers_all_jobs(
                summary_only_build, jsonl, "amd", 7791
            ) is False
            m.assert_called_once_with("amd", 7791)
        assert summary_only_build == full_detail

    def test_api_failure_on_detail_falls_back_to_trusting_cache(self, tmp_path):
        # If Buildkite is flaky we must not make collection fail outright
        # for the rest of the pipeline — next cron tick retries. The helper
        # logs a warning and returns True so the caller uses the cache.
        jsonl = tmp_path / "2026-04-18_amd.jsonl"
        _write_jsonl(jsonl, [_record("mi250_1: LoRA")])
        summary_only_build = {"number": 7791}
        with patch("collect_ci.fetch_build_detail",
                   side_effect=RuntimeError("503 upstream")):
            assert _cache_covers_all_jobs(
                summary_only_build, jsonl, "amd", 7791
            ) is True


class TestCanonicalResultPublication:
    @staticmethod
    def _build(*, state: str, job_state: str) -> dict:
        return {
            "number": 84160,
            "state": state,
            "commit": "a" * 40,
            "created_at": "2026-08-17T06:00:00Z",
            "jobs": [
                {
                    "type": "script",
                    "id": "job-1",
                    "name": "H100: Engine tests",
                    "state": job_state,
                    "retried_in_job_id": None,
                }
            ],
        }

    def test_running_build_does_not_write_partial_daily_jsonl(self, tmp_path):
        summary = self._build(state="failing", job_state="passed")
        detail = json.loads(json.dumps(summary))

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results") as parse_results,
        ):
            builds, results = collect_pipeline("upstream", 8, tmp_path)

        assert builds[0]["number"] == 84160
        assert results == {}
        assert not (tmp_path / "test_results" / "2026-08-17_upstream.jsonl").exists()
        parse_results.assert_not_called()

    def test_byte_limited_floor_skips_old_build_details_on_repeated_runs(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(reporter_module, "TEST_RESULT_STORE_MAX_BYTES", 80)
        monkeypatch.setattr(reporter_module, "TEST_RESULT_SHARD_MAX_BYTES", 100)
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        (results_dir / "2026-08-17_upstream.jsonl").write_bytes(b"x" * 80)
        (results_dir / "2026-08-18_upstream.jsonl").write_bytes(b"x" * 80)
        assert prune_old_results(
            results_dir,
            max_days=365,
            max_total_bytes=80,
            max_shard_bytes=100,
        ) == 1
        summary = self._build(state="passed", job_state="passed")

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch(
                "collect_ci.fetch_build_detail",
                side_effect=AssertionError("old retained-out build was re-fetched"),
            ) as fetch_detail,
            patch("collect_ci.parse_job_results") as parse_results,
        ):
            for _ in range(2):
                _, results = collect_pipeline("upstream", 8, tmp_path)
                assert results == {}

        fetch_detail.assert_not_called()
        parse_results.assert_not_called()

    def test_running_retry_invalidates_its_cached_canonical_jsonl(self, tmp_path):
        summary = self._build(state="running", job_state="running")
        detail = json.loads(json.dumps(summary))
        cached_path = tmp_path / "test_results" / "2026-08-17_upstream.jsonl"
        cached = _record(
            "H100: Engine tests",
            build_num=84160,
            job_id="original-attempt",
        )
        cached["pipeline"] = "ci"
        cached["date"] = "2026-08-17"
        _write_jsonl(cached_path, [cached])

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results") as parse_results,
        ):
            _, results = collect_pipeline("upstream", 8, tmp_path)

        assert results == {}
        assert not cached_path.exists()
        parse_results.assert_not_called()

    def test_terminal_nonparseable_retry_removes_superseded_cache(self, tmp_path):
        summary = self._build(state="passed", job_state="expired")
        summary["jobs"][0]["id"] = "retry-attempt"
        detail = json.loads(json.dumps(summary))
        cached_path = tmp_path / "test_results" / "2026-08-17_upstream.jsonl"
        cached = _record(
            "H100: Engine tests",
            build_num=84160,
            job_id="original-attempt",
        )
        cached["pipeline"] = "ci"
        cached["date"] = "2026-08-17"
        _write_jsonl(cached_path, [cached])

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results") as parse_results,
        ):
            _, results = collect_pipeline("upstream", 8, tmp_path)

        assert results == {}
        assert not cached_path.exists()
        parse_results.assert_not_called()

    def test_terminal_build_with_running_soft_job_stays_provisional(self, tmp_path):
        summary = self._build(state="passed", job_state="running")
        detail = json.loads(json.dumps(summary))

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results") as parse_results,
        ):
            _, results = collect_pipeline("upstream", 8, tmp_path)

        assert results == {}
        assert not (tmp_path / "test_results" / "2026-08-17_upstream.jsonl").exists()
        parse_results.assert_not_called()

    def test_metadata_only_summary_persists_hydrated_roster(self, tmp_path):
        # This starts one second before the roster's last retained UTC day.
        # Discovery and the final hydrated write must use the same frozen
        # clock even when the real test process (or production collection)
        # continues past midnight.
        collection_clock = datetime(
            2026, 9, 1, 23, 59, 59, tzinfo=timezone.utc
        )
        summary = self._build(state="failing", job_state="passed")
        summary.pop("jobs")
        detail = self._build(state="failing", job_state="passed")
        detail["creator"] = {
            "name": "Public Buildkite Name",
            "email": "private@example.invalid",
        }
        detail["env"] = {"BUILD_SECRET": "do-not-cache"}
        detail["meta_data"] = {"tenant": "do-not-cache"}
        detail["jobs"][0].update({
            "agent": {"name": "private-agent", "meta_data": ["host=private"]},
            "command": "export JOB_SECRET=do-not-cache",
            "env": {"JOB_SECRET": "do-not-cache"},
            "future_api_field": {"private": True},
        })

        with (
            patch(
                "collect_ci.fetch_nightly_builds", return_value=[summary]
            ) as fetch_builds,
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results") as parse_results,
        ):
            collect_pipeline(
                "upstream",
                8,
                tmp_path,
                now=collection_clock,
            )

        fetch_builds.assert_called_once_with(
            "upstream",
            days=8,
            cache_dir=tmp_path / ".cache",
            cache_errors=None,
            now=collection_clock,
        )

        cache_path = (
            tmp_path
            / ".cache"
            / "nightly-rosters-v2"
            / "upstream"
            / "2026-08-17_84160.json"
        )
        payload = json.loads(cache_path.read_text())
        assert payload == {
            "schema_version": 2,
            "build": {
                "number": 84160,
                "created_at": "2026-08-17T06:00:00Z",
                "jobs": [{
                    "type": "script",
                    "id": "job-1",
                    "name": "H100: Engine tests",
                    "state": "passed",
                }],
            },
        }
        serialized = cache_path.read_text()
        for forbidden in (
            "creator", "email", "env", "meta_data", "agent", "command",
            "future_api_field", "do-not-cache", "private-agent",
        ):
            assert forbidden not in serialized
        parse_results.assert_not_called()

    def test_latest_terminal_summary_hydrates_before_cache_coverage(self, tmp_path):
        summary = self._build(state="passed", job_state="passed")
        detail = json.loads(json.dumps(summary))
        detail["jobs"].append(
            {
                "type": "script",
                "id": "job-2",
                "name": "H100: Late soft failure",
                "state": "timed_out",
                "soft_failed": True,
                "retried_in_job_id": None,
            }
        )

        cached_path = tmp_path / "test_results" / "2026-08-17_upstream.jsonl"
        cached = _record("H100: Engine tests", build_num=84160)
        cached["pipeline"] = "ci"
        cached["date"] = "2026-08-17"
        _write_jsonl(cached_path, [cached])

        parsed_by_job = {}
        for job in detail["jobs"]:
            row = _record(job["name"], build_num=84160)
            row["pipeline"] = "ci"
            row["date"] = "2026-08-17"
            if job["id"] == "job-2":
                row["status"] = "failed"
            parsed_by_job[job["id"]] = TestResult(**row)

        def parse_job(job, *_args):
            return [parsed_by_job[job["id"]]]

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch("collect_ci.fetch_build_detail", return_value=detail) as fetch_detail,
            patch("collect_ci.parse_job_results", side_effect=parse_job) as parse_results,
        ):
            _, results = collect_pipeline("upstream", 8, tmp_path)

        fetch_detail.assert_called_once_with("upstream", 84160)
        assert parse_results.call_count == 2
        assert {result.job_name for result in results[84160]} == {
            "H100: Engine tests",
            "H100: Late soft failure",
        }
        published = [json.loads(line) for line in cached_path.read_text().splitlines()]
        assert {row["job_name"] for row in published} == {
            "H100: Engine tests",
            "H100: Late soft failure",
        }

    def test_metadata_only_historical_build_keeps_canonical_evidence(self, tmp_path):
        latest = self._build(state="passed", job_state="passed")
        latest["number"] = 84161
        latest["created_at"] = "2026-08-18T06:00:00Z"
        historical = self._build(state="passed", job_state="passed")

        details = {
            int(latest["number"]): json.loads(json.dumps(latest)),
            int(historical["number"]): json.loads(json.dumps(historical)),
        }
        summaries = []
        for build in (latest, historical):
            summary = json.loads(json.dumps(build))
            summary.pop("jobs")
            summaries.append(summary)

            date = summary["created_at"][:10]
            row = _record(
                "H100: Engine tests",
                build_num=summary["number"],
                job_id="job-1",
            )
            row["pipeline"] = "ci"
            row["date"] = date
            _write_jsonl(
                tmp_path / "test_results" / f"{date}_upstream.jsonl",
                [row],
            )

        def fetch_detail(_pipeline, build_number):
            return details[build_number]

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=summaries),
            patch("collect_ci.fetch_build_detail", side_effect=fetch_detail) as detail_fetch,
            patch("collect_ci.parse_job_results") as parse_results,
        ):
            builds, results = collect_pipeline("upstream", 8, tmp_path)

        assert detail_fetch.call_count == 2
        parse_results.assert_not_called()
        assert set(results) == {84160, 84161}
        assert all(build.get("jobs") for build in builds)
        entries = [
            (build_number, rows[0].date, rows)
            for build_number, rows in sorted(results.items())
        ]
        assert _completed_result_entries(entries, builds) == entries

    def test_terminal_retry_with_same_name_replaces_cached_attempt(self, tmp_path):
        summary = self._build(state="passed", job_state="passed")
        detail = json.loads(json.dumps(summary))
        detail["jobs"][0]["id"] = "retry-attempt"
        cached_path = tmp_path / "test_results" / "2026-08-17_upstream.jsonl"
        cached = _record(
            "H100: Engine tests",
            build_num=84160,
            job_id="original-attempt",
        )
        cached["pipeline"] = "ci"
        cached["date"] = "2026-08-17"
        _write_jsonl(cached_path, [cached])

        replacement = _record(
            "H100: Engine tests",
            build_num=84160,
            job_id="retry-attempt",
        )
        replacement["pipeline"] = "ci"
        replacement["date"] = "2026-08-17"
        parsed = TestResult(**replacement)

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results", return_value=[parsed]) as parse_results,
        ):
            _, results = collect_pipeline("upstream", 8, tmp_path)

        assert results == {84160: [parsed]}
        parse_results.assert_called_once()
        published = json.loads(cached_path.read_text())
        assert published["job_id"] == "retry-attempt"

    def test_terminal_running_terminal_lifecycle_publishes_only_new_attempt(self, tmp_path):
        cached_path = tmp_path / "test_results" / "2026-08-17_upstream.jsonl"

        def collect(build: dict, parsed_job_id: str | None = None):
            detail = json.loads(json.dumps(build))
            parsed_rows = []
            if parsed_job_id is not None:
                row = _record(
                    "H100: Engine tests",
                    build_num=84160,
                    job_id=parsed_job_id,
                )
                row["pipeline"] = "ci"
                row["date"] = "2026-08-17"
                parsed_rows = [TestResult(**row)]
            with (
                patch("collect_ci.fetch_nightly_builds", return_value=[build]),
                patch("collect_ci.fetch_build_detail", return_value=detail),
                patch("collect_ci.parse_job_results", return_value=parsed_rows),
            ):
                return collect_pipeline("upstream", 8, tmp_path)

        terminal_a = self._build(state="passed", job_state="passed")
        terminal_a["jobs"][0]["id"] = "attempt-a"
        _, first_results = collect(terminal_a, "attempt-a")
        assert first_results[84160][0].job_id == "attempt-a"
        assert json.loads(cached_path.read_text())["job_id"] == "attempt-a"

        running_b = self._build(state="running", job_state="running")
        running_b["jobs"][0]["id"] = "attempt-b"
        _, provisional_results = collect(running_b)
        assert provisional_results == {}
        assert not cached_path.exists()

        terminal_b = self._build(state="passed", job_state="passed")
        terminal_b["jobs"][0]["id"] = "attempt-b"
        _, final_results = collect(terminal_b, "attempt-b")
        assert final_results[84160][0].job_id == "attempt-b"
        assert json.loads(cached_path.read_text())["job_id"] == "attempt-b"

    def test_complete_build_promotes_results_to_daily_jsonl(self, tmp_path):
        summary = self._build(state="passed", job_state="passed")
        detail = json.loads(json.dumps(summary))
        row = _record("H100: Engine tests", build_num=84160)
        row["pipeline"] = "ci"
        row["date"] = "2026-08-17"
        parsed = TestResult(**row)

        with (
            patch("collect_ci.fetch_nightly_builds", return_value=[summary]),
            patch("collect_ci.fetch_build_detail", return_value=detail),
            patch("collect_ci.parse_job_results", return_value=[parsed]),
        ):
            _, results = collect_pipeline("upstream", 8, tmp_path)

        assert results == {84160: [parsed]}
        path = tmp_path / "test_results" / "2026-08-17_upstream.jsonl"
        assert path.exists()
        assert json.loads(path.read_text())["build_number"] == 84160


class TestFrozenAmdNightlySnapshot:
    def test_snapshot_keeps_only_matrix_fields_and_strips_pii(self, tmp_path):
        build = {
            "number": 7791,
            "state": "running",
            "branch": "main",
            "commit": "a" * 40,
            "created_at": "2026-04-18T09:00:00Z",
            "message": "AMD Full CI Run - nightly",
            "web_url": "https://buildkite.com/vllm/amd-ci/builds/7791",
            "creator": {"name": "Private User", "email": "private@example.com"},
            "jobs": [
                {
                    "type": "script",
                    "id": "job-1",
                    "name": "mi300_1: Engine",
                    "state": "running",
                    "soft_failed": False,
                    "agent_query_rules": [
                        "queue=amd_mi300_1",
                        "agent-name=private-agent",
                    ],
                    "step": {"id": "engine", "key": "private-key"},
                    "agent": {"name": "private-agent"},
                    "raw_log_url": "https://example.invalid/private",
                }
            ],
        }

        compact = _compact_amd_build_snapshot(build)
        assert compact["number"] == 7791
        assert compact["jobs"] == [
            {
                "type": "script",
                "id": "job-1",
                "name": "mi300_1: Engine",
                "state": "running",
                "soft_failed": False,
                "agent_query_rules": ["queue=amd_mi300_1"],
                "step": {"id": "engine"},
            }
        ]
        serialized = json.dumps(compact)
        assert "private@example.com" not in serialized
        assert "private-agent" not in serialized
        assert "raw_log_url" not in serialized

        path = write_amd_nightly_snapshot(build, tmp_path)
        assert path == tmp_path / ".cache" / "amd_nightly_snapshot.json"
        payload = json.loads(path.read_text())
        assert payload["schema_version"] == 2
        assert payload["pipeline"] == "amd-ci"
        assert payload["build"] == compact
        assert payload["publication_retention"]["job_rows"] == {
            "source": 1,
            "published": 1,
            "omitted": 0,
            "complete_relative_to_source": True,
        }
        assert (
            payload["publication_retention"]["complete_relative_to_source"]
            is True
        )
