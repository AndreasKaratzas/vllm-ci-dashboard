"""Unit tests for ``scripts/vllm/collect_analytics.py`` window handling."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from vllm import collect_analytics as ca


NOW = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _job(name: str, dur: float, wait: float = 0.2, state: str = "passed", queue: str = "amd_mi300_1"):
    row = {"name": name, "state": state, "dur": dur}
    if wait is not None:
        row["wait"] = wait
    if queue:
        row["q"] = queue
    return row


def _build(number: int, days_ago: float, jobs: list[dict], state: str = "passed"):
    created = NOW - timedelta(days=days_ago)
    return {
        "number": number,
        "state": state,
        "created_at": _iso(created),
        "date": ca.nightly_date(_iso(created)),
        "message": "nightly",
        "author": "",
        "wall_mins": 60.0,
        "passed": sum(1 for j in jobs if j.get("state") == "passed"),
        "failed": sum(1 for j in jobs if j.get("state") in ("failed", "timed_out", "broken")),
        "soft_failed": sum(1 for j in jobs if j.get("state") == "soft_fail"),
        "total_jobs": len(jobs),
        "jobs": jobs,
        "web_url": "",
    }


class TestWindowedAnalytics:
    def test_fetch_pipeline_builds_includes_page_two_and_deduplicates(self, monkeypatch):
        page_one = [
            {"number": number, "created_at": f"2026-04-20T09:{number % 60:02d}:00Z"}
            for number in range(1, 101)
        ]
        page_two = [
            {"number": number, "created_at": f"2026-04-21T09:{number % 60:02d}:00Z"}
            for number in range(100, 121)
        ]
        calls = []

        def fake_get(path, token, params=None):
            calls.append(dict(params or {}))
            return page_one if params["page"] == 1 else page_two

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds("amd-ci", "fake-token", 30)

        assert len(builds) == 120
        assert {build["number"] for build in builds} == set(range(1, 121))
        assert [call["page"] for call in calls] == [1, 2]
        assert all(call["per_page"] == 100 for call in calls)
        assert all(call["branch"] == "main" for call in calls)
        assert all(call["include_retried_jobs"] == "true" for call in calls)
        assert provenance["exhaustive"] is True
        assert provenance["termination_reason"] == "short_page"

    def test_fetch_pipeline_builds_stops_when_a_full_page_adds_no_builds(self, monkeypatch):
        repeated_page = [{"number": number} for number in range(1, 101)]
        pages = []

        def fake_get(path, token, params=None):
            pages.append(params["page"])
            return repeated_page

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds("amd-ci", "fake-token", 30)

        assert len(builds) == 100
        assert pages == [1, 2]
        assert provenance["exhaustive"] is False
        assert provenance["termination_reason"] == "duplicate_page"

    def test_upstream_fetch_includes_page_two(self, monkeypatch):
        pages = []

        def fake_get(path, token, params=None):
            pages.append(params["page"])
            if params["page"] == 1:
                return [{"number": number} for number in range(1, 101)]
            return [{"number": number} for number in range(101, 121)]

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds("ci", "fake-token", 30)

        assert pages == [1, 2]
        assert len(builds) == 120
        assert provenance["exhaustive"] is True

    def test_fetch_pipeline_builds_marks_the_safety_cap_incomplete(self, monkeypatch):
        monkeypatch.setattr(
            ca,
            "bk_get",
            lambda path, token, params=None: [
                {"number": number} for number in range(1, 101)
            ],
        )

        builds, provenance = ca.fetch_pipeline_builds(
            "ci", "fake-token", 30, max_pages=1
        )

        assert len(builds) == 100
        assert provenance["exhaustive"] is False
        assert provenance["termination_reason"] == "max_pages"

    def test_fetch_pipeline_builds_keeps_the_richer_duplicate(self, monkeypatch):
        page_one = [{"number": number, "jobs": []} for number in range(1, 101)]
        richer = {
            "number": 100,
            "state": "passed",
            "finished_at": "2026-04-21T12:00:00Z",
            "jobs": [{"id": "retained-job"}],
        }

        def fake_get(path, token, params=None):
            return page_one if params["page"] == 1 else [richer]

        monkeypatch.setattr(ca, "bk_get", fake_get)

        builds, provenance = ca.fetch_pipeline_builds("ci", "fake-token", 30)

        retained = next(build for build in builds if build["number"] == 100)
        assert retained["jobs"] == [{"id": "retained-job"}]
        assert provenance["exhaustive"] is True
        assert provenance["termination_reason"] == "short_page"

    def test_fetch_pipeline_builds_ignores_malformed_rows(self, monkeypatch):
        monkeypatch.setattr(
            ca,
            "bk_get",
            lambda path, token, params=None: [
                None,
                {"number": "not-a-number"},
                {"number": 42, "created_at": "2026-07-12T09:00:00Z"},
            ],
        )

        builds, provenance = ca.fetch_pipeline_builds("ci", "fake-token", 30)

        assert [build["number"] for build in builds] == [42]
        assert provenance["exhaustive"] is True

    def test_main_emits_all_main_reliability_only_for_upstream(self, monkeypatch, tmp_path):
        messages = {
            "amd-ci": "AMD Full CI Run - nightly",
            "ci": "Full CI run - nightly",
        }

        def fake_fetch(pipeline_slug, token, days, max_pages=None):
            number = 101 if pipeline_slug == "amd-ci" else 202
            return [{
                "number": number,
                "branch": "main",
                "state": "passed",
                "commit": f"commit-{number}",
                "message": messages[pipeline_slug],
                "created_at": "2026-07-12T09:00:00Z",
                "started_at": "2026-07-12T09:01:00Z",
                "finished_at": "2026-07-12T10:00:00Z",
                "web_url": f"https://buildkite.com/vllm/{pipeline_slug}/builds/{number}",
                "jobs": [
                    {
                        "id": f"failed-{number}",
                        "type": "script",
                        "name": "Retry group",
                        "state": "failed",
                        "retried_in_job_id": f"passed-{number}",
                        "runnable_at": "2026-07-12T09:01:00Z",
                        "started_at": "2026-07-12T09:02:00Z",
                        "finished_at": "2026-07-12T09:03:00Z",
                        "agent_query_rules": ["queue=gpu_1_queue"],
                        "step": {"id": f"step-{number}", "key": "retry-group"},
                    },
                    {
                        "id": f"passed-{number}",
                        "type": "script",
                        "name": "Retry group",
                        "state": "passed",
                        "retry_type": "automatic",
                        "runnable_at": "2026-07-12T09:03:00Z",
                        "started_at": "2026-07-12T09:04:00Z",
                        "finished_at": "2026-07-12T09:05:00Z",
                        "agent_query_rules": ["queue=gpu_1_queue"],
                        "step": {"id": f"step-{number}", "key": "retry-group"},
                    },
                ],
            }], {
                "created_from": "2026-06-12T00:00:00Z",
                "page_size": 100,
                "max_pages": 50,
                "pages_fetched": 1,
                "termination_reason": "short_page",
                "exhaustive": True,
            }

        monkeypatch.setenv("BUILDKITE_TOKEN", "fake-token")
        monkeypatch.setattr(ca, "fetch_pipeline_builds", fake_fetch)
        monkeypatch.setattr(ca, "load_test_result_builds", lambda *args, **kwargs: [])
        monkeypatch.setattr(ca.sys, "argv", [
            "collect_analytics.py",
            "--days", "30",
            "--pipeline", "both",
            "--output", str(tmp_path),
        ])

        ca.main()

        payload = json.loads((tmp_path / "analytics.json").read_text())
        assert "all_main_reliability" not in payload["amd-ci"]
        assert "main_retry_analysis" not in payload["amd-ci"]
        block = payload["ci"]
        reliability = block["all_main_reliability"]
        assert reliability["cohort"]["id"] == "ci-main-completed-pass-fail"
        assert reliability["cohort"]["pipeline"] == "ci"
        assert block["main_retry_analysis"]["summary"]["builds_evaluated"] == 1
        assert block["main_retry_analysis"]["summary"]["retry_attempt_count"] == 1
        assert block["main_retry_analysis"]["summary"]["failed_then_passed_recovery_count"] == 1
        assert "/vllm/ci/builds/" in block["main_retry_analysis"]["retry_attempts"][0]["url"]

    def test_tokenless_refresh_preserves_complete_main_retry_ledger(self, monkeypatch, tmp_path):
        previous_build = _build(202, 0.5, [_job("No retry in compact history", 10)])
        previous_build["web_url"] = "https://buildkite.com/vllm/ci/builds/202"
        reliability = {
            "schema_version": 1,
            "cohort": {
                "id": "ci-main-completed-pass-fail",
                "pipeline": "ci",
                "branch": "main",
                "build_states": ["failed", "passed"],
                "build_count": 1,
                "canonical_nightly_build_count": 1,
                "non_nightly_main_build_count": 0,
                "exhaustive": True,
            },
            "denominator": {"eligible_observations": 0},
            "provenance": {
                "pipeline": "ci",
                "endpoint": "/organizations/vllm/pipelines/ci/builds",
                "query": {"branch": "main"},
                "collection": {"exhaustive": True},
            },
            "builds": [{
                "number": 202,
                "branch": "main",
                "state": "passed",
                "finished_at": "2026-04-20T12:00:00Z",
                "url": "https://buildkite.com/vllm/ci/builds/202",
            }],
            "groups": [],
        }
        preserved_retry = {
            "available": True,
            "summary": {
                "builds_evaluated": 30,
                "builds_with_retries": 1,
                "retry_attempt_count": 1,
                "failed_then_passed_recovery_count": 0,
            },
            "retry_attempts": [{
                "build_number": 202,
                "job_id": "older-retry",
                "url": "https://buildkite.com/vllm/ci/builds/202/steps/canvas?jid=older-retry",
            }],
            "failed_then_passed_recoveries": [],
            "provenance": {
                "source_pipeline": "ci",
                "complete": True,
                "cohort_build_numbers": [202],
            },
        }
        (tmp_path / "analytics.json").write_text(json.dumps({
            "ci": {
                "display_name": "Upstream CI",
                "builds": [previous_build],
                "all_main_reliability": reliability,
                "main_retry_analysis": preserved_retry,
            },
        }))
        monkeypatch.delenv("BUILDKITE_TOKEN", raising=False)
        monkeypatch.setattr(ca, "load_test_result_builds", lambda *args, **kwargs: [])
        monkeypatch.setattr(ca.sys, "argv", [
            "collect_analytics.py",
            "--days", "30",
            "--pipeline", "ci",
            "--output", str(tmp_path),
        ])

        ca.main()

        refreshed = json.loads((tmp_path / "analytics.json").read_text())
        assert refreshed["ci"]["main_retry_analysis"] == preserved_retry

    def test_analytics_uses_exact_amd_nightly_pattern(self, monkeypatch):
        builds = [
            {
                "number": 9537,
                "message": "AMD Full CI Run - nightly",
                "state": "passed",
                "created_at": "2026-06-15T09:00:00Z",
                "finished_at": "2026-06-15T12:00:00Z",
                "jobs": [],
                "web_url": "https://buildkite.com/vllm/amd-ci/builds/9537",
            },
            {
                "number": 9542,
                "message": "AMD Full CI Run - TheRock nightly (2026-06-15, base 9872921c5)",
                "state": "running",
                "created_at": "2026-06-15T12:00:00Z",
                "finished_at": "",
                "jobs": [],
                "web_url": "https://buildkite.com/vllm/amd-ci/builds/9542",
            },
        ]
        monkeypatch.setattr(ca, "bk_get", lambda path, token, params=None: builds)

        out = ca.collect_pipeline(
            "amd-ci",
            token="fake-token",
            days=1,
            nightly_only=True,
            name_pattern=ca.NIGHTLY_NAME_PATTERNS_BY_SLUG["amd-ci"],
        )

        assert [build["number"] for build in out] == [9537]

    def test_emits_precomputed_windows(self):
        builds = [
            _build(1, 0.5, [_job("Recent", 40)]),
            _build(2, 2.0, [_job("Mid", 50)]),
            _build(3, 6.0, [_job("Week", 60)]),
            _build(4, 10.0, [_job("Old", 70)]),
        ]

        windows = ca.compute_window_blocks(builds, 30, now=NOW)

        assert set(windows) == {"1d", "3d", "7d", "14d", "30d"}
        assert windows["1d"]["build_count"] == 1
        assert windows["3d"]["build_count"] == 2
        assert windows["7d"]["build_count"] == 3
        assert windows["14d"]["build_count"] == 4
        assert windows["30d"]["build_count"] == 4
        assert "jobs" not in windows["30d"]["builds"][0]

    def test_shorter_windows_forget_older_jobs(self):
        builds = [
            _build(1, 10.0, [_job("Legacy MI325 bottleneck", 600, queue="amd_mi325_1")]),
            _build(2, 1.0, [_job("Current MI300 bottleneck", 45, queue="amd_mi300_1")]),
        ]

        windows = ca.compute_window_blocks(builds, 14, now=NOW)
        names_14d = [row["name"] for row in windows["14d"]["duration_ranking"]]
        names_3d = [row["name"] for row in windows["3d"]["duration_ranking"]]

        assert "Legacy MI325 bottleneck" in names_14d
        assert "Legacy MI325 bottleneck" not in names_3d
        assert names_3d == ["Current MI300 bottleneck"]

    def test_window_block_recomputes_summary_and_failures(self):
        builds = [
            _build(1, 8.0, [_job("Flaky", 30, state="failed")], state="failed"),
            _build(2, 0.5, [_job("Flaky", 32, state="passed"), _job("Stable", 20)], state="passed"),
        ]

        windows = ca.compute_window_blocks(builds, 14, now=NOW)

        assert windows["14d"]["summary"]["total_builds"] == 2
        assert windows["14d"]["summary"]["jobs_with_failures"] == 1
        assert windows["7d"]["summary"]["total_builds"] == 1
        assert windows["7d"]["summary"]["jobs_with_failures"] == 0

    def test_top_level_rankings_can_still_cover_full_span(self):
        builds = [
            _build(1, 10.0, [_job("Legacy MI325 bottleneck", 600, queue="amd_mi325_1")]),
            _build(2, 0.5, [_job("Current MI300 bottleneck", 45, queue="amd_mi300_1")]),
        ]

        rankings = ca.compute_job_rankings(builds)
        queues = {row["name"]: row["queues"] for row in rankings}

        assert sorted(queues["Legacy MI325 bottleneck"]) == ["amd_mi325_1"]
        assert sorted(queues["Current MI300 bottleneck"]) == ["amd_mi300_1"]

    def test_gating_nightlies_omit_heavy_job_fields(self, tmp_path):
        builds = [
            _build(1, 0.5, [{**_job("AMD: Samplers Test (mi325_1)", 40), "wait": 12, "extra": "drop"}]),
        ]
        all_data = {
            "ci": {"display_name": "Upstream CI", "builds": builds},
            "amd-ci": {"display_name": "AMD CI", "builds": builds},
        }

        ca.write_gating_nightlies(tmp_path, all_data, "2026-04-20T12:00:00Z")
        payload = json.loads((tmp_path / "gating_nightlies.json").read_text())
        job = payload["ci"]["builds"][0]["jobs"][0]

        assert "name" in job
        assert "state" in job
        assert "dur" not in job
        assert "wait" not in job
        assert "extra" not in job

    def test_gating_nightlies_keep_exact_job_link_fields(self, tmp_path):
        builds = [
            _build(1, 0.5, [{
                **_job("AMD: Samplers Test (mi325_1)", 40),
                "job_id": "019ed951-af8e-4dc8-9590-72a47f9fed96",
                "step_id": "019ed951-ad41-4cc1-8942-051077910be7",
                "url": "https://buildkite.com/vllm/ci/builds/1/steps/canvas?jid=019ed951-af8e-4dc8-9590-72a47f9fed96&tab=output",
            }]),
        ]
        all_data = {
            "ci": {"display_name": "Upstream CI", "builds": builds},
            "amd-ci": {"display_name": "AMD CI", "builds": builds},
        }

        ca.write_gating_nightlies(tmp_path, all_data, "2026-04-20T12:00:00Z")
        payload = json.loads((tmp_path / "gating_nightlies.json").read_text())
        job = payload["ci"]["builds"][0]["jobs"][0]

        assert job["job_id"] == "019ed951-af8e-4dc8-9590-72a47f9fed96"
        assert job["step_id"] == "019ed951-ad41-4cc1-8942-051077910be7"
        assert "url" not in job

    def test_gating_nightlies_parse_exact_ids_from_existing_urls(self, tmp_path):
        builds = [
            _build(1, 0.5, [{
                **_job("AMD: Samplers Test (mi325_1)", 40),
                "url": "https://buildkite.com/vllm/ci/builds/1/steps/canvas?jid=019ed951-af8e-4dc8-9590-72a47f9fed96&tab=output",
            }]),
            _build(2, 0.5, [{
                **_job("mi325_1: Samplers Test", 40),
                "url": "https://buildkite.com/vllm/amd-ci/builds/2/steps/canvas?sid=019ed951-ad41-4cc1-8942-051077910be7&tab=output",
            }]),
        ]
        all_data = {
            "ci": {"display_name": "Upstream CI", "builds": [builds[0]]},
            "amd-ci": {"display_name": "AMD CI", "builds": [builds[1]]},
        }

        ca.write_gating_nightlies(tmp_path, all_data, "2026-04-20T12:00:00Z")
        payload = json.loads((tmp_path / "gating_nightlies.json").read_text())

        assert payload["ci"]["builds"][0]["jobs"][0]["job_id"] == "019ed951-af8e-4dc8-9590-72a47f9fed96"
        assert payload["amd-ci"]["builds"][0]["jobs"][0]["step_id"] == "019ed951-ad41-4cc1-8942-051077910be7"

    def test_gating_nightlies_are_capped_and_compact(self, tmp_path):
        builds = [
            _build(i, i * 0.5, [_job(f"Job {i}", 40)])
            for i in range(ca.GATING_NIGHTLY_LIMIT + 5)
        ]
        all_data = {
            "ci": {"display_name": "Upstream CI", "builds": builds},
            "amd-ci": {"display_name": "AMD CI", "builds": builds},
        }

        ca.write_gating_nightlies(tmp_path, all_data, "2026-04-20T12:00:00Z")
        text = (tmp_path / "gating_nightlies.json").read_text()
        payload = json.loads(text)

        assert text.count("\n") == 1
        assert len(payload["ci"]["builds"]) == ca.GATING_NIGHTLY_LIMIT
        assert len(payload["amd-ci"]["builds"]) == ca.GATING_NIGHTLY_LIMIT
        assert payload["ci"]["builds"][-1]["number"] == ca.GATING_NIGHTLY_LIMIT - 1

    def test_summary_counts_soft_failed_jobs_as_failures(self):
        builds = [
            _build(1, 0.5, [_job("Accepted Failure", 20, state="soft_fail")]),
        ]

        rankings = ca.compute_job_rankings(builds)
        summary = ca.compute_summary(builds, rankings)

        assert summary["jobs_with_failures"] == 1
        assert summary["jobs_with_hard_failures"] == 0
        assert summary["jobs_with_soft_failures"] == 1


class TestParsedResultFallback:
    def test_fallback_created_at_uses_current_nightly_schedule(self):
        assert ca._iso_from_nightly_date("2026-05-08", "ci") == "2026-05-08T06:00:00Z"
        assert ca._iso_from_nightly_date("2026-05-08", "amd-ci") == "2026-05-08T09:00:00Z"
        assert ca._iso_from_nightly_date("2026-05-08", "other") == "2026-05-08T12:00:00Z"

    def test_loads_amd_builds_from_test_result_jsonl(self, tmp_path):
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        result_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        rows = [
            {
                "name": "__passed__ (7)",
                "status": "passed",
                "duration_secs": 120.0,
                "job_name": "mi300_1: Passing Group",
                "build_number": 123,
                "pipeline": "amd-ci",
                "date": result_date,
            },
            {
                "name": "__failed__ (2)",
                "status": "failed",
                "duration_secs": 4.0,
                "job_name": "mi300_1: Broken Group",
                "build_number": 123,
                "pipeline": "amd-ci",
                "date": result_date,
            },
            {
                "name": "__skipped__ (5)",
                "status": "skipped",
                "duration_secs": 0.1,
                "job_name": "mi300_1: Skipped Group",
                "build_number": 123,
                "pipeline": "amd-ci",
                "date": result_date,
            },
        ]
        (results_dir / f"{result_date}_amd.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n"
        )

        builds = ca.load_test_result_builds(tmp_path, "amd-ci", 14, buildkite_builds=[], previous_builds=[])

        assert len(builds) == 1
        build = builds[0]
        assert build["number"] == 123
        assert build["source"] == "test_results"
        assert build["state"] == "failed"
        assert build["passed"] == 1
        assert build["failed"] == 1
        assert build["skipped"] == 1
        assert {job["name"]: job["state"] for job in build["jobs"]} == {
            "Passing Group": "passed",
            "Broken Group": "failed",
            "Skipped Group": "skipped",
        }
        passing = {job["name"]: job for job in build["jobs"]}["Passing Group"]
        assert passing["test_duration_mins"] == 2.0
        assert "dur" not in passing

    def test_buildkite_summary_labels_wall_queue_and_end_to_end_durations(self):
        raw = [{
            "number": 777,
            "branch": "main",
            "commit": "abc123",
            "message": "post-merge validation",
            "state": "passed",
            "created_at": "2026-04-20T09:00:00Z",
            "finished_at": "2026-04-20T09:30:00Z",
            "jobs": [{
                "id": "job-777",
                "type": "script",
                "name": "mi300_1: Duration Group",
                "state": "passed",
                "runnable_at": "2026-04-20T09:01:00Z",
                "started_at": "2026-04-20T09:06:00Z",
                "finished_at": "2026-04-20T09:26:00Z",
                "step": {"id": "step-777", "key": "duration-group"},
                "agent_query_rules": ["queue=amd_mi300_1"],
            }],
        }]

        build = ca.summarize_pipeline_builds("amd-ci", raw)[0]
        job = build["jobs"][0]

        assert build["branch"] == "main"
        assert build["commit"] == "abc123"
        assert job["dur"] == job["wall_completion_mins"] == 20.0
        assert job["queue_wait_mins"] == 5.0
        assert job["end_to_end_mins"] == 25.0
        assert job["duration_source"] == "buildkite_wall"
        assert "test_duration_mins" not in job

    def test_test_result_builds_emit_buildkite_job_urls(self, tmp_path):
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        result_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        rows = [
            {
                "name": "__passed__ (7)",
                "status": "passed",
                "duration_secs": 120.0,
                "job_name": "AMD: Passing Group (mi325_1)",
                "job_id": "019ed951-af8e-4dc8-9590-72a47f9fed96",
                "step_id": "019ed951-ad41-4cc1-8942-051077910be7",
                "build_number": 72843,
                "pipeline": "ci",
                "date": result_date,
            },
        ]
        (results_dir / f"{result_date}_upstream.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n"
        )

        builds = ca.load_test_result_builds(tmp_path, "ci", 14, buildkite_builds=[], previous_builds=[])

        assert len(builds) == 1
        job = builds[0]["jobs"][0]
        assert job["url"] == (
            "https://buildkite.com/vllm/ci/builds/72843/steps/canvas"
            "?jid=019ed951-af8e-4dc8-9590-72a47f9fed96&tab=output"
        )
        assert job["job_id"] == "019ed951-af8e-4dc8-9590-72a47f9fed96"
        assert job["step_id"] == "019ed951-ad41-4cc1-8942-051077910be7"

    def test_test_result_builds_inherit_exact_job_ids_from_buildkite_metadata(self, tmp_path):
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        result_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        rows = [
            {
                "name": "__passed__ (7)",
                "status": "passed",
                "duration_secs": 120.0,
                "job_name": "AMD: Passing Group (mi325_1)",
                "build_number": 72843,
                "pipeline": "ci",
                "date": result_date,
            },
        ]
        (results_dir / f"{result_date}_upstream.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n"
        )
        buildkite_builds = [
            {
                "number": 72843,
                "jobs": [
                    {
                        "name": "Passing Group",
                        "raw_name": "AMD: Passing Group (mi325_1)",
                        "state": "passed",
                        "q": "gpu_1_queue",
                        "job_id": "019ed951-af8e-4dc8-9590-72a47f9fed96",
                        "step_id": "019ed951-ad41-4cc1-8942-051077910be7",
                    }
                ],
                "web_url": "https://buildkite.com/vllm/ci/builds/72843",
            }
        ]

        builds = ca.load_test_result_builds(tmp_path, "ci", 14, buildkite_builds=buildkite_builds, previous_builds=[])

        job = builds[0]["jobs"][0]
        assert job["job_id"] == "019ed951-af8e-4dc8-9590-72a47f9fed96"
        assert job["step_id"] == "019ed951-ad41-4cc1-8942-051077910be7"
        assert job["url"] == (
            "https://buildkite.com/vllm/ci/builds/72843/steps/canvas"
            "?jid=019ed951-af8e-4dc8-9590-72a47f9fed96&tab=output"
        )

    def test_keeps_hardware_specific_result_jobs_separate(self, tmp_path):
        """Same title on MI300 and MI355 must not collapse into one job.

        The AMD matrix joins analytics rows by normalized title *and* queue.
        If parsed JSONL rows are grouped only by normalized title, a failure on
        MI300 can be rendered as an MI355 failure.
        """
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        result_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        rows = [
            {
                "name": "__failed__ (5)",
                "status": "failed",
                "duration_secs": 0.0,
                "job_name": "mi300_1: Entrypoints Integration (Pooling)",
                "build_number": 8193,
                "pipeline": "amd-ci",
                "date": result_date,
            },
            {
                "name": "__passed__ (306)",
                "status": "passed",
                "duration_secs": 1848.45,
                "job_name": "mi355_1: Entrypoints Integration (Pooling)",
                "build_number": 8193,
                "pipeline": "amd-ci",
                "date": result_date,
            },
        ]
        (results_dir / f"{result_date}_amd.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n"
        )

        builds = ca.load_test_result_builds(tmp_path, "amd-ci", 14, buildkite_builds=[], previous_builds=[])

        assert len(builds) == 1
        build = builds[0]
        assert build["passed"] == 1
        assert build["failed"] == 1
        jobs = sorted(build["jobs"], key=lambda row: row["q"])
        assert [(job["name"], job["q"], job["state"]) for job in jobs] == [
            ("Entrypoints Integration (Pooling)", "amd_mi300_1", "failed"),
            ("Entrypoints Integration (Pooling)", "amd_mi355_1", "passed"),
        ]

    def test_test_result_builds_preserve_buildkite_soft_fail_state(self, tmp_path):
        """Parsed JSONL failures should not turn Buildkite soft-fails hard-red.

        The current upstream nightly can have vendor hardware jobs that exit
        non-zero but are configured as ``soft_failed`` in Buildkite. The JSONL
        rows still contain failed pytest counts, so analytics must carry over
        the Buildkite job state when it is available.
        """
        results_dir = tmp_path / "test_results"
        results_dir.mkdir()
        result_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        rows = [
            {
                "name": "__unidentified_failures__ (6)",
                "status": "failed",
                "duration_secs": 0.0,
                "job_name": "Intel GPU Test",
                "build_number": 65324,
                "pipeline": "ci",
                "date": result_date,
            },
        ]
        (results_dir / f"{result_date}_upstream.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n"
        )

        buildkite_builds = [
            _build(
                65324,
                0.5,
                [
                    {
                        "name": "Intel GPU Test",
                        "raw_name": "Intel GPU Test",
                        "state": "soft_fail",
                        "dur": 4.6,
                        "wait": 0.0,
                        "q": "intel-gpu",
                    }
                ],
                state="running",
            )
        ]

        builds = ca.load_test_result_builds(tmp_path, "ci", 14, buildkite_builds=buildkite_builds)

        assert len(builds) == 1
        build = builds[0]
        assert build["failed"] == 0
        assert build["soft_failed"] == 1
        assert build["jobs"][0]["state"] == "soft_fail"
        assert build["jobs"][0]["q"] == "intel-gpu"

    def test_choose_analytics_builds_preserves_previous_on_empty_collection(self):
        previous = [_build(42, 1.0, [_job("Known Good", 10)])]

        chosen = ca.choose_analytics_builds([], [], previous, "amd-ci")

        assert chosen == previous
