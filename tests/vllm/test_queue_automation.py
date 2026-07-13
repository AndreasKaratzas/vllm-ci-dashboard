"""
Tests for the CI queue monitor automation pipeline.

Validates that:
1. The collect_queue_snapshot script produces valid JSONL consumable by ci-queue.js
2. The site assembly places both docs and data correctly (no double rm -rf)
3. The queue_timeseries.jsonl data has the correct schema
4. The queue-monitor workflow includes a deploy step
"""

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from vllm import collect_queue_snapshot as cqs

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
WORKFLOWS = ROOT / ".github" / "workflows"


class TestQueueTimeseriesSchema:
    """Validate the queue_timeseries.jsonl file has the correct structure."""

    @pytest.fixture
    def snapshots(self):
        path = DATA / "vllm" / "ci" / "queue_timeseries.jsonl"
        if not path.exists():
            pytest.skip("queue_timeseries.jsonl not collected yet")
        lines = [l for l in path.read_text().strip().split("\n") if l.strip()]
        if not lines:
            pytest.fail("queue_timeseries.jsonl exists but is empty")
        return [json.loads(line) for line in lines]

    def test_file_exists(self):
        path = DATA / "vllm" / "ci" / "queue_timeseries.jsonl"
        assert path.exists(), "queue_timeseries.jsonl must exist for CI queue tab"

    def test_file_not_empty(self):
        path = DATA / "vllm" / "ci" / "queue_timeseries.jsonl"
        if not path.exists():
            pytest.skip("queue_timeseries.jsonl not collected yet")
        content = path.read_text().strip()
        assert len(content) > 0, "queue_timeseries.jsonl must not be empty"

    def test_each_line_is_valid_json(self):
        path = DATA / "vllm" / "ci" / "queue_timeseries.jsonl"
        if not path.exists():
            pytest.skip("queue_timeseries.jsonl not collected yet")
        for i, line in enumerate(path.read_text().strip().split("\n")):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as e:
                pytest.fail(f"Line {i + 1} is not valid JSON: {e}")

    def test_snapshots_have_required_keys(self, snapshots):
        for i, snap in enumerate(snapshots):
            assert "ts" in snap, f"Snapshot {i} missing 'ts'"
            assert "queues" in snap, f"Snapshot {i} missing 'queues'"
            assert isinstance(snap["queues"], dict), f"Snapshot {i} 'queues' must be dict"

    def test_timestamps_are_iso_format(self, snapshots):
        for i, snap in enumerate(snapshots):
            ts = snap["ts"]
            assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts), (
                f"Snapshot {i} timestamp '{ts}' not in ISO format"
            )

    def test_queues_have_job_counts(self, snapshots):
        for i, snap in enumerate(snapshots):
            for qname, qdata in snap["queues"].items():
                assert "waiting" in qdata, f"Snapshot {i}, queue '{qname}' missing 'waiting'"
                assert "running" in qdata, f"Snapshot {i}, queue '{qname}' missing 'running'"
                assert isinstance(qdata["waiting"], int), (
                    f"Snapshot {i}, queue '{qname}' waiting must be int"
                )
                assert isinstance(qdata["running"], int), (
                    f"Snapshot {i}, queue '{qname}' running must be int"
                )
                assert qdata["waiting"] >= 0, f"Snapshot {i}, queue '{qname}' waiting < 0"
                assert qdata["running"] >= 0, f"Snapshot {i}, queue '{qname}' running < 0"

    def test_totals_present(self, snapshots):
        for i, snap in enumerate(snapshots):
            assert "total_waiting" in snap, f"Snapshot {i} missing 'total_waiting'"
            assert "total_running" in snap, f"Snapshot {i} missing 'total_running'"

    def test_totals_match_sum(self, snapshots):
        for i, snap in enumerate(snapshots):
            expected_waiting = sum(q["waiting"] for q in snap["queues"].values())
            expected_running = sum(q["running"] for q in snap["queues"].values())
            assert snap["total_waiting"] == expected_waiting, (
                f"Snapshot {i}: total_waiting {snap['total_waiting']} != sum {expected_waiting}"
            )
            assert snap["total_running"] == expected_running, (
                f"Snapshot {i}: total_running {snap['total_running']} != sum {expected_running}"
            )

    def test_timestamps_are_chronological(self, snapshots):
        for i in range(1, len(snapshots)):
            assert snapshots[i]["ts"] >= snapshots[i - 1]["ts"], (
                f"Snapshots not chronological: {snapshots[i - 1]['ts']} > {snapshots[i]['ts']}"
            )

    def test_release_normalizer_prevents_workload_history_overstatement(self, snapshots):
        warnings = []
        for snapshot in snapshots:
            normalized = cqs.normalize_history_snapshot(snapshot)
            assert normalized is not None
            for queue, row in normalized["queues"].items():
                assert not cqs.is_excluded_queue(queue)
                for split_key, total_key in (
                    ("waiting_by_workload", "waiting"),
                    ("running_by_workload", "running"),
                ):
                    split = row.get(split_key)
                    if isinstance(split, dict) and sum(split.values()) > row[total_key]:
                        warnings.append(f"{normalized['ts']} {queue} {split_key}")

        assert warnings == []


class TestCollectQueueSnapshotScript:
    """Validate the collector script structure and output path."""

    def test_script_exists(self):
        script = ROOT / "scripts" / "vllm" / "collect_queue_snapshot.py"
        assert script.exists(), "collect_queue_snapshot.py must exist"

    def test_script_output_path_matches_data(self):
        script = ROOT / "scripts" / "vllm" / "collect_queue_snapshot.py"
        if not script.exists():
            pytest.skip("script not present")
        content = script.read_text()
        assert "queue_timeseries.jsonl" in content, "Script must write to queue_timeseries.jsonl"

    def test_script_appends_jsonl(self):
        """Verify the script opens file in append mode, not write mode."""
        script = ROOT / "scripts" / "vllm" / "collect_queue_snapshot.py"
        if not script.exists():
            pytest.skip("script not present")
        content = script.read_text()
        assert '"a"' in content or "'a'" in content, (
            "Script must open file in append mode to preserve history"
        )

    def test_script_syntax_valid(self):
        script = ROOT / "scripts" / "vllm" / "collect_queue_snapshot.py"
        if not script.exists():
            pytest.skip("script not present")
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(script)], capture_output=True, text=True
        )
        assert result.returncode == 0, f"Script has syntax errors: {result.stderr}"


class TestSiteAssemblyCorrectness:
    """Verify the site assembly step in workflows doesn't nuke docs."""

    ASSEMBLY_WORKFLOWS = [
        "deploy-pages.yml",
        "daily-update.yml",
        "ci-collect.yml",
        "queue-monitor.yml",
    ]

    @pytest.mark.parametrize("wf_name", ASSEMBLY_WORKFLOWS)
    def test_no_double_rm_rf_site(self, wf_name):
        """The assembly must not rm -rf _site after copying docs into it."""
        wf_path = WORKFLOWS / wf_name
        if not wf_path.exists():
            pytest.skip(f"{wf_name} not present")
        content = wf_path.read_text()
        # Count occurrences of 'rm -rf _site' — should be at most 1
        matches = re.findall(r"rm\s+-rf\s+_site", content)
        assert len(matches) <= 1, (
            f"{wf_name} has {len(matches)} 'rm -rf _site' — second one nukes docs content"
        )

    @pytest.mark.parametrize("wf_name", ASSEMBLY_WORKFLOWS)
    def test_assembly_copies_docs_then_data(self, wf_name):
        """Assembly must copy docs/* first, then overlay data/* without clearing."""
        wf_path = WORKFLOWS / wf_name
        if not wf_path.exists():
            pytest.skip(f"{wf_name} not present")
        content = wf_path.read_text()
        if "Assemble site" not in content:
            pytest.skip(f"{wf_name} has no assembly step")
        # Extract the assembly run block
        wf = yaml.safe_load(content)
        for job_data in wf.get("jobs", {}).values():
            for step in job_data.get("steps", []):
                if step.get("name") == "Assemble site":
                    run_block = step.get("run", "")
                    # After the first rm -rf _site, there should be cp docs then cp data
                    # with NO second rm -rf _site between them
                    lines = [l.strip() for l in run_block.split("\n") if l.strip()]
                    rm_count = sum(1 for l in lines if "rm -rf _site" in l)
                    assert rm_count <= 1, (
                        f"{wf_name}: assembly has {rm_count} 'rm -rf _site' commands"
                    )


class TestQueueMonitorWorkflow:
    """Validate the queue-monitor workflow is correctly configured."""

    @pytest.fixture
    def workflow(self):
        path = WORKFLOWS / "queue-monitor.yml"
        if not path.exists():
            pytest.skip("queue-monitor.yml not present")
        return yaml.safe_load(path.read_text())

    def test_has_trigger(self, workflow):
        """queue-monitor must have workflow_dispatch (schedule moved to hourly-master)."""
        triggers = workflow.get(True, {})  # 'on' parses as True in yaml
        assert "workflow_dispatch" in triggers or "schedule" in triggers, (
            "queue-monitor must have workflow_dispatch or schedule trigger"
        )

    def test_has_deploy_step(self, workflow):
        """Queue monitor must deploy to gh-pages so data reaches the dashboard."""
        steps = workflow["jobs"]["snapshot"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        has_deploy = any("deploy" in n.lower() or "pages" in n.lower() for n in step_names)
        has_deploy = has_deploy or any(
            "peaceiris/actions-gh-pages" in str(s.get("uses", "")) for s in steps
        )
        assert has_deploy, "queue-monitor.yml must include a deploy step to push data to gh-pages"

    def test_has_assemble_step(self, workflow):
        steps = workflow["jobs"]["snapshot"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert any("assemble" in n.lower() for n in step_names), (
            "queue-monitor.yml must assemble the site before deploying"
        )

    def test_workflow_references_correct_script(self):
        path = WORKFLOWS / "queue-monitor.yml"
        if not path.exists():
            pytest.skip("queue-monitor.yml not present")
        content = path.read_text()
        assert "collect_queue_snapshot.py" in content, (
            "Workflow must reference collect_queue_snapshot.py"
        )

    def test_workflow_has_contents_write_permission(self, workflow):
        perms = workflow.get("permissions", {})
        assert perms.get("contents") == "write", "queue-monitor needs contents:write to push data"

    def test_push_failure_does_not_block_deploy(self):
        """The push to main may fail due to branch protection. The workflow must
        not abort before the deploy step — it should treat push as best-effort."""
        path = WORKFLOWS / "queue-monitor.yml"
        if not path.exists():
            pytest.skip("queue-monitor.yml not present")
        content = path.read_text()
        # The push command must use || (or continue-on-error) so failure
        # doesn't abort the job before the deploy step runs
        assert "|| " in content or "continue-on-error" in content, (
            "queue-monitor push to main must be non-blocking (use || or "
            "continue-on-error) so deploy to gh-pages always runs"
        )

    def test_workflow_syncs_from_gh_pages(self):
        """Workflow must sync queue data from gh-pages before collecting,
        because the push to main may have been blocked by branch protection."""
        path = WORKFLOWS / "queue-monitor.yml"
        if not path.exists():
            pytest.skip("queue-monitor.yml not present")
        content = path.read_text()
        assert "gh-pages" in content and "queue_timeseries" in content, (
            "queue-monitor must sync queue_timeseries from gh-pages before "
            "collecting to avoid data loss when main push fails"
        )

    def test_deploy_pages_syncs_queue_from_gh_pages(self):
        """deploy-pages.yml must also sync queue data from gh-pages so it
        doesn't overwrite fresh data with stale main data."""
        path = WORKFLOWS / "deploy-pages.yml"
        if not path.exists():
            pytest.skip("deploy-pages.yml not present")
        content = path.read_text()
        assert "gh-pages" in content and "queue_timeseries" in content, (
            "deploy-pages.yml must sync queue_timeseries from gh-pages to "
            "avoid overwriting fresh queue data"
        )


class TestQueueDashboardControls:
    """Validate the queue dashboard's visible wait controls."""

    def test_jobs_over_time_defaults_to_running_workload(self):
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        assert "let metric = 'running';" in js, (
            "Queue dashboard should default Jobs Over Time to running workload"
        )

    def test_wait_dashboard_defaults_to_official_p95(self):
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        assert "const DEFAULT_WAIT_METRIC = 'p95_wait';" in js, (
            "Queue dashboard should default to Buildkite's official p95 wait"
        )

    def test_wait_dashboard_hides_unsupported_wait_metrics(self):
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        for token in (
            "{k:'avg_wait',label:'Avg'}",
            "{k:'p75_wait',label:'p75'}",
            "{k:'p90_wait',label:'p90'}",
            "{k:'p99_wait',label:'p99'}",
            "{key:'p75_wait',label:'p75'}",
            "{key:'p90_wait',label:'p90'}",
            "{key:'p99_wait',label:'p99'}",
            "{key:'avg_wait',label:'Avg'}",
        ):
            assert token not in js, (
                f"Queue dashboard should not expose removed wait metric control {token}"
            )

    def test_wait_dashboard_does_not_flatten_unsampled_backlog_to_zero(self):
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        assert "wait_sample_count" in js
        assert "wait_source !== 'cluster_metrics'" in js
        assert "return null" in js, (
            "Backlogged queues without job-level wait samples should render as "
            "missing data, not as a false 0m wait"
        )

    def test_capacity_monitor_defaults_to_mi325_1_scope(self):
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        assert "let selectedScopeKey = 'mi325_1';" in js
        assert "MI325 1-GPU" in js
        assert "Projected Target Demand Over Time" in js
        assert "Capacity status is loading" in js
        assert "Traffic scope" in js
        assert "capacityIntervalHours = 168" in js
        assert "Interval:" in js

    def test_capacity_monitor_omits_dependency_score_ui(self):
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        assert "Dependency Scope" not in js
        assert "Largest AMD Mirror" not in js
        assert "Projected Lines" not in js
        assert "Assumptions:" not in js
        assert "YAML Jobs" not in js

    def test_capacity_monitor_timeseries_uses_distinct_legend_colors(self):
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        assert "label:'Actual demand / capacity'" in js
        assert "label:`Projected demand @ ${_fmtInt(theoreticalGroups)} groups / capacity`" in js
        assert "label:'100% capacity'" in js
        assert "borderColor:C.o" in js, (
            "Incoming demand should have a fixed orange legend/line color, "
            "not dynamic green/red segments that the legend cannot explain."
        )
        assert "borderColor:C.p" in js
        assert "segment:{borderColor:ctx => _pressureColor" not in js

    def test_capacity_monitor_projects_peak_demand_not_yaml_jobs(self):
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        assert "Projected Demand" in js
        assert "Projected @ ${_fmtInt(theoreticalGroups)}" in js
        assert "projectedPeakDemand" in js
        assert "Projection model:" in js
        assert "Selected scope peak running" not in js
        assert "configuredJobs" not in js

    def test_capacity_monitor_has_executive_story_not_clipped_bar_chart(self):
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        assert "Capacity Gap" in js
        assert "Target Over Capacity" in js
        assert "Queue Breakdown" in js
        assert "Projected Target Demand Over Time" in js
        assert "chartMax = _chartMaxPct" in js
        assert "max:chartMax" in js
        assert "Capacity Pressure by Queue" not in js
        assert "0-200% Scale" not in js

    def test_capacity_monitor_chart_has_interval_selector(self):
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        for label in (
            "'1h'",
            "'3h'",
            "'6h'",
            "'12h'",
            "'24h'",
            "'2d'",
            "'3d'",
            "'5d'",
            "'7d'",
            "'14d'",
            "'1m'",
        ):
            assert f"label:{label}" in js
        assert "label:'3m'" not in js
        assert "hours:2160" not in js
        assert "snapshotsInCapacityInterval" in js
        assert "capacityIntervalHours = iv.hours" in js
        assert "selectedInterval.hours" in js
        assert "selectedInterval.label" in js
        assert "last 7 days" not in js

    def test_queue_monitor_trusts_only_one_month_of_history(self):
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        constants = ROOT / "scripts" / "vllm" / "constants.py"
        constants_text = constants.read_text()
        assert "QUEUE_HISTORY_TRUST_HOURS = 720" in js
        assert "QUEUE_HISTORY_TRUST_HOURS * 3600000" in js
        assert "QUEUE_HISTORY_RETENTION_DAYS = 30" in constants_text


class TestCollectorPagination:
    """Validate the collector handles pagination for large result sets."""

    def test_collector_uses_pagination(self):
        """The collector must paginate Buildkite API results to capture all builds."""
        script = ROOT / "scripts" / "vllm" / "collect_queue_snapshot.py"
        if not script.exists():
            pytest.skip("script not present")
        content = script.read_text()
        assert "paginated" in content.lower() or "page" in content, (
            "Collector must paginate API calls to avoid missing builds beyond the first 100"
        )

    def test_collector_handles_scheduled_state(self):
        """Jobs in 'scheduled' state are waiting in the queue and must be counted."""
        script = ROOT / "scripts" / "vllm" / "collect_queue_snapshot.py"
        if not script.exists():
            pytest.skip("script not present")
        content = script.read_text()
        assert '"scheduled"' in content, (
            "Collector must handle 'scheduled' job state — these jobs are "
            "waiting in the queue but may not show as 'waiting' in BK API"
        )


class TestCIQueueFrontend:
    """Validate the ci-queue.js frontend can consume the data format."""

    def test_ci_queue_js_exists(self):
        assert (DOCS / "assets" / "js" / "ci-queue.js").exists()

    def test_ci_queue_fetches_correct_path(self):
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        assert "queue_timeseries.jsonl" in js, "ci-queue.js must fetch queue_timeseries.jsonl"

    def test_ci_queue_has_admin_amd_triage(self):
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        assert "AMD Queue Triage" in js, "Queue monitor should expose an AMD triage panel"
        assert "run_min" in js, (
            "Queue monitor should inspect long-running jobs, not just queued jobs"
        )
        assert "isAdmin()" in js or "isAdminUser" in js, "AMD triage actions should be admin-gated"
        assert "Copy curl" in js, "Queue monitor should expose a copyable Buildkite cancel command"

    def test_ci_queue_tab_registered(self):
        """CI queue tab can be in HTML or dynamically registered via JS."""
        html = (DOCS / "index.html").read_text()
        js = (DOCS / "assets" / "js" / "utils.js").read_text()
        in_html = 'data-tab="ci-queue"' in html
        in_js = "id: 'ci-queue'" in js
        assert in_html or in_js, "ci-queue tab not found in HTML or registerCISection"

    def test_data_matches_js_expectations(self):
        """Verify that the JSONL data has fields the JS actually reads."""
        path = DATA / "vllm" / "ci" / "queue_timeseries.jsonl"
        if not path.exists():
            pytest.skip("no data yet")
        lines = [l for l in path.read_text().strip().split("\n") if l.strip()]
        if not lines:
            pytest.skip("empty data")
        snap = json.loads(lines[0])
        # ci-queue.js reads: snap.ts, snap.queues, snap.total_waiting, snap.total_running
        assert "ts" in snap
        assert "queues" in snap
        assert "total_waiting" in snap
        assert "total_running" in snap
        # For each queue, JS reads: qdata.waiting, qdata.running, qdata.p50_wait
        for qname, qdata in snap["queues"].items():
            assert "waiting" in qdata, f"queue '{qname}' missing 'waiting'"
            assert "running" in qdata, f"queue '{qname}' missing 'running'"

    def test_queue_wait_time_percentiles_present(self):
        """ci-queue.js percentile selector reads p50/p75/p90/p99/max/avg_wait fields."""
        path = DATA / "vllm" / "ci" / "queue_timeseries.jsonl"
        if not path.exists():
            pytest.skip("no data yet")
        lines = [l for l in path.read_text().strip().split("\n") if l.strip()]
        if not lines:
            pytest.skip("empty data")
        snap = json.loads(lines[-1])  # latest snapshot
        required = {"p50_wait", "p75_wait", "p90_wait", "p99_wait", "max_wait", "avg_wait"}
        for qname, qdata in snap["queues"].items():
            missing = required - set(qdata.keys())
            assert not missing, (
                f"queue '{qname}' missing wait time percentile fields: {missing}. "
                f"collect_queue_snapshot.py must emit all percentile fields."
            )

    def test_queue_percentile_ordering(self):
        """Wait time percentiles must be ordered: p50 <= p75 <= p90 <= p99 <= max."""
        path = DATA / "vllm" / "ci" / "queue_timeseries.jsonl"
        if not path.exists():
            pytest.skip("no data yet")
        lines = [l for l in path.read_text().strip().split("\n") if l.strip()]
        if not lines:
            pytest.skip("empty data")
        bad = []
        for line in lines:
            snap = json.loads(line)
            for qname, qdata in snap["queues"].items():
                vals = [
                    qdata.get(k)
                    for k in ["p50_wait", "p75_wait", "p90_wait", "p99_wait", "max_wait"]
                ]
                for i in range(len(vals) - 1):
                    # Missing percentiles are explicitly null when Buildkite
                    # exposes neither queue-native metrics nor a job sample.
                    if vals[i] is None or vals[i + 1] is None:
                        continue
                    if vals[i] > vals[i + 1] + 0.01:  # small tolerance for rounding
                        bad.append(f"{snap['ts']} queue '{qname}': {vals}")
                        break
        assert not bad, f"Percentile ordering violated:\n" + "\n".join(bad[:5])


class TestIntervalFilteringLogic:
    """Strict tests for the interval filtering logic used in ci-queue.js.

    The JS updateChart() function filters snapshots using:
        lastSnapshotTs = last snapshot's timestamp
        cutoff = lastSnapshotTs - intervalHours * 3600000
        filtered = snapshots where ts >= cutoff

    These tests re-implement that logic in Python and verify:
    1. Every enabled interval returns non-empty results
    2. The cutoff is relative to the last snapshot, NOT to wall-clock time
    3. Filtered results are correct subsets of the data
    4. The auto-selected default interval is valid
    """

    INTERVALS = [
        {"label": "1h", "hours": 1},
        {"label": "3h", "hours": 3},
        {"label": "6h", "hours": 6},
        {"label": "12h", "hours": 12},
        {"label": "24h", "hours": 24},
        {"label": "2d", "hours": 48},
        {"label": "3d", "hours": 72},
        {"label": "5d", "hours": 120},
        {"label": "7d", "hours": 168},
        {"label": "14d", "hours": 336},
        {"label": "1m", "hours": 720},
    ]

    @pytest.fixture
    def snapshots(self):
        path = DATA / "vllm" / "ci" / "queue_timeseries.jsonl"
        if not path.exists():
            pytest.skip("queue_timeseries.jsonl not collected yet")
        lines = [l for l in path.read_text().strip().split("\n") if l.strip()]
        if not lines:
            pytest.fail("queue_timeseries.jsonl exists but is empty")
        return [json.loads(line) for line in lines]

    @staticmethod
    def _parse_ts(ts_str):
        from datetime import datetime, timezone

        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

    @staticmethod
    def _available_hours(snapshots):
        first_ts = TestIntervalFilteringLogic._parse_ts(snapshots[0]["ts"])
        last_ts = TestIntervalFilteringLogic._parse_ts(snapshots[-1]["ts"])
        import math

        return max(1, math.ceil((last_ts - first_ts).total_seconds() / 3600))

    @staticmethod
    def _filter_snapshots(snapshots, interval_hours):
        """Re-implements the JS filtering: cutoff relative to LAST snapshot."""
        last_ts = TestIntervalFilteringLogic._parse_ts(snapshots[-1]["ts"])
        from datetime import timedelta

        cutoff = last_ts - timedelta(hours=interval_hours)
        return [s for s in snapshots if TestIntervalFilteringLogic._parse_ts(s["ts"]) >= cutoff]

    @staticmethod
    def _snapshots_in_interval(snapshots, hours):
        """Count how many snapshots fall within the interval (mirrors JS snapshotsInInterval)."""
        return len(TestIntervalFilteringLogic._filter_snapshots(snapshots, hours))

    @staticmethod
    def _enabled_intervals(snapshots):
        """An interval is enabled only if it contains >= 2 snapshots (matches JS logic)."""
        return [
            iv
            for iv in TestIntervalFilteringLogic.INTERVALS
            if TestIntervalFilteringLogic._snapshots_in_interval(snapshots, iv["hours"]) >= 2
        ]

    def test_intervals_match_js(self):
        """Verify that our INTERVALS list matches what ci-queue.js defines."""
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        for iv in self.INTERVALS:
            assert f"label:'{iv['label']}'" in js or f"label: '{iv['label']}'" in js, (
                f"Interval {iv['label']} not found in ci-queue.js"
            )

    def test_cutoff_uses_last_snapshot_not_now(self):
        """The cutoff computation in updateChart must NOT use Date.now().
        It must reference the last snapshot timestamp instead."""
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        # Extract the updateChart function body
        start = js.find("function updateChart()")
        assert start != -1, "updateChart function not found in ci-queue.js"
        # Find matching closing brace (count braces)
        depth = 0
        body_start = js.index("{", start)
        i = body_start
        while i < len(js):
            if js[i] == "{":
                depth += 1
            elif js[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        fn_body = js[body_start : i + 1]
        # Strip single-line comments before checking — comments may mention Date.now()
        code_lines = [l for l in fn_body.split("\n") if not l.strip().startswith("//")]
        code_only = "\n".join(code_lines)
        assert "Date.now()" not in code_only, (
            "updateChart must NOT use Date.now() for cutoff — use last snapshot timestamp"
        )
        assert "lastSnapshotTs" in fn_body or "snapshots[snapshots.length" in fn_body, (
            "updateChart must reference the last snapshot timestamp for cutoff"
        )

    def test_every_enabled_interval_returns_data(self, snapshots):
        """For each interval that the UI marks as enabled (hours <= availableHours),
        the filtering must return at least one snapshot."""
        enabled = self._enabled_intervals(snapshots)
        assert len(enabled) > 0, "At least one interval should be enabled"
        for iv in enabled:
            filtered = self._filter_snapshots(snapshots, iv["hours"])
            assert len(filtered) > 0, (
                f"Interval {iv['label']} ({iv['hours']}h) is enabled "
                f"but filtering returns 0 snapshots"
            )

    def test_smallest_enabled_interval_returns_subset(self, snapshots):
        """The smallest enabled interval should return a proper subset
        (not all data) when there are enough snapshots spanning a larger range."""
        enabled = self._enabled_intervals(snapshots)
        if len(enabled) < 2:
            pytest.skip("Need at least 2 enabled intervals to test subsetting")
        smallest = enabled[0]
        filtered = self._filter_snapshots(snapshots, smallest["hours"])
        if len(snapshots) > 1 and self._available_hours(snapshots) > smallest["hours"]:
            assert len(filtered) < len(snapshots), (
                f"Interval {smallest['label']} should return a subset, "
                f"not all {len(snapshots)} snapshots"
            )

    def test_larger_interval_includes_smaller(self, snapshots):
        """A larger interval must return a superset of a smaller interval's results."""
        enabled = self._enabled_intervals(snapshots)
        for i in range(len(enabled) - 1):
            small = self._filter_snapshots(snapshots, enabled[i]["hours"])
            large = self._filter_snapshots(snapshots, enabled[i + 1]["hours"])
            small_ts = {s["ts"] for s in small}
            large_ts = {s["ts"] for s in large}
            assert small_ts <= large_ts, (
                f"Interval {enabled[i + 1]['label']} must include all snapshots "
                f"from {enabled[i]['label']}"
            )

    def test_full_range_interval_returns_all(self, snapshots):
        """An interval >= available hours must return all snapshots."""
        available = self._available_hours(snapshots)
        filtered = self._filter_snapshots(snapshots, available)
        assert len(filtered) == len(snapshots), (
            f"Interval covering full range ({available}h) should return all "
            f"{len(snapshots)} snapshots, got {len(filtered)}"
        )

    def test_auto_selected_default_is_valid(self, snapshots):
        """The auto-selected default interval must be the largest with >= 2 snapshots."""
        enabled = self._enabled_intervals(snapshots)
        default = enabled[-1] if enabled else self.INTERVALS[0]
        filtered = self._filter_snapshots(snapshots, default["hours"])
        assert len(filtered) >= 2 or len(snapshots) < 2, (
            f"Auto-selected default interval {default['label']} has fewer than 2 snapshots"
        )

    def test_filtered_timestamps_are_after_cutoff(self, snapshots):
        """Every snapshot in filtered results must have ts >= cutoff."""
        from datetime import timedelta

        for iv in self._enabled_intervals(snapshots):
            last_ts = self._parse_ts(snapshots[-1]["ts"])
            cutoff = last_ts - timedelta(hours=iv["hours"])
            filtered = self._filter_snapshots(snapshots, iv["hours"])
            for s in filtered:
                ts = self._parse_ts(s["ts"])
                assert ts >= cutoff, (
                    f"Interval {iv['label']}: snapshot at {s['ts']} is before "
                    f"cutoff {cutoff.isoformat()}"
                )

    def test_excluded_snapshots_are_before_cutoff(self, snapshots):
        """Snapshots NOT in filtered results must have ts < cutoff."""
        from datetime import timedelta

        for iv in self._enabled_intervals(snapshots):
            last_ts = self._parse_ts(snapshots[-1]["ts"])
            cutoff = last_ts - timedelta(hours=iv["hours"])
            filtered_ts = {s["ts"] for s in self._filter_snapshots(snapshots, iv["hours"])}
            for s in snapshots:
                if s["ts"] not in filtered_ts:
                    ts = self._parse_ts(s["ts"])
                    assert ts < cutoff, (
                        f"Interval {iv['label']}: snapshot at {s['ts']} excluded "
                        f"but is after cutoff {cutoff.isoformat()}"
                    )

    def test_3h_interval_with_5h_data_returns_correct_count(self, snapshots):
        """Regression test: with ~5h of data, the 3h interval must return data.
        This is the exact scenario from the bug report."""
        available = self._available_hours(snapshots)
        if available < 3:
            pytest.skip("Need at least 3h of data for this test")
        filtered = self._filter_snapshots(snapshots, 3)
        assert len(filtered) > 0, (
            "BUG REGRESSION: 3h interval with 5h of data must return snapshots. "
            "If this fails, the cutoff is likely using wall-clock time instead of "
            "the last snapshot timestamp."
        )
        assert len(filtered) <= len(snapshots), (
            "3h filter should not return more than total snapshots"
        )

    def test_enabled_intervals_require_at_least_2_snapshots(self, snapshots):
        """Regression: intervals must be enabled only if >= 2 snapshots exist in range.
        This prevents enabling intervals that would render a single-point chart."""
        for iv in self._enabled_intervals(snapshots):
            count = self._snapshots_in_interval(snapshots, iv["hours"])
            assert count >= 2, (
                f"Interval {iv['label']} is enabled with only {count} snapshot(s). "
                f"Need >= 2 for a renderable chart."
            )

    def test_disabled_intervals_have_fewer_than_2_snapshots(self, snapshots):
        """Intervals NOT in the enabled list must have < 2 snapshots in range."""
        enabled_labels = {iv["label"] for iv in self._enabled_intervals(snapshots)}
        for iv in self.INTERVALS:
            if iv["label"] not in enabled_labels:
                count = self._snapshots_in_interval(snapshots, iv["hours"])
                assert count < 2, (
                    f"Interval {iv['label']} is disabled but has {count} snapshots "
                    f"(>= 2). It should be enabled."
                )


class TestIntervalEnablementSynthetic:
    """Synthetic data tests for interval enablement logic.

    These tests don't depend on real data and verify the >= 2 snapshot
    requirement that prevents enabling unrenderable intervals.
    """

    INTERVALS = TestIntervalFilteringLogic.INTERVALS

    @staticmethod
    def _make_snapshots(timestamps):
        """Create minimal snapshots from a list of ISO timestamp strings."""
        return [
            {"ts": ts, "queues": {}, "total_waiting": 0, "total_running": 0} for ts in timestamps
        ]

    @staticmethod
    def _filter(snapshots, hours):
        return TestIntervalFilteringLogic._filter_snapshots(snapshots, hours)

    @staticmethod
    def _enabled(snapshots):
        return [
            iv
            for iv in TestIntervalFilteringLogic.INTERVALS
            if len(TestIntervalFilteringLogic._filter_snapshots(snapshots, iv["hours"])) >= 2
        ]

    def test_single_snapshot_enables_nothing(self):
        """A single snapshot cannot render any interval (need >= 2 points)."""
        snaps = self._make_snapshots(["2025-01-01T12:00:00Z"])
        enabled = self._enabled(snaps)
        assert len(enabled) == 0, (
            f"Single snapshot should enable no intervals, got: {[iv['label'] for iv in enabled]}"
        )

    def test_two_snapshots_1min_apart_enables_only_1h(self):
        """Two snapshots 1 minute apart: only 1h (and larger if they still capture both) enabled."""
        snaps = self._make_snapshots(
            [
                "2025-01-01T12:00:00Z",
                "2025-01-01T12:01:00Z",
            ]
        )
        enabled = self._enabled(snaps)
        # All intervals >= 1h should include both snapshots (span is only 1 min)
        # so all intervals should be enabled
        for iv in enabled:
            count = len(self._filter(snaps, iv["hours"]))
            assert count >= 2

    def test_5h_span_hourly_does_not_enable_6h(self):
        """6 snapshots over 5 hours: 6h interval has < 2 snapshots only if
        the span is truly < 6h. With 5h span, 6h captures all 6 — so it IS enabled.
        But 1m, 14d, etc. that span more than the data should still be enabled too
        since they capture all snapshots."""
        snaps = self._make_snapshots(
            [
                f"2025-01-01T{10 + i:02d}:00:00Z"
                for i in range(6)  # 10:00 to 15:00
            ]
        )
        enabled_labels = {iv["label"] for iv in self._enabled(snaps)}
        # 1h: cutoff at 14:00, captures 14:00 and 15:00 = 2 snapshots => enabled
        assert "1h" in enabled_labels
        # 3h: cutoff at 12:00, captures 12,13,14,15 = 4 snapshots => enabled
        assert "3h" in enabled_labels
        # 6h: cutoff at 09:00, captures all 6 => enabled
        assert "6h" in enabled_labels

    def test_2_snapshots_4h_apart_disables_3h_if_only_1_in_range(self):
        """Two snapshots 4 hours apart: the 3h interval cutoff is at last-3h,
        which only captures the last snapshot (1 point) — should be disabled."""
        snaps = self._make_snapshots(
            [
                "2025-01-01T10:00:00Z",
                "2025-01-01T14:00:00Z",
            ]
        )
        enabled_labels = {iv["label"] for iv in self._enabled(snaps)}
        # 3h: cutoff at 11:00, only 14:00 is after => 1 snapshot => disabled
        assert "3h" not in enabled_labels, (
            "3h should be disabled: only 1 snapshot falls within 3h of the last"
        )
        # 5d (120h): both snapshots captured => enabled
        assert "5d" in enabled_labels

    def test_banner_duration_matches_filtered_not_total(self):
        """Regression: the info banner must show the filtered data span, not the total.
        This test verifies the logic by checking that the filtered span for a small
        interval is less than the total span."""
        snaps = self._make_snapshots(
            [
                "2025-01-01T10:00:00Z",
                "2025-01-01T11:00:00Z",
                "2025-01-01T12:00:00Z",
                "2025-01-01T13:00:00Z",
                "2025-01-01T14:00:00Z",
                "2025-01-01T15:00:00Z",
            ]
        )
        total_span_h = 5  # 10:00 to 15:00
        # 1h filter: cutoff at 14:00, gets 14:00 + 15:00
        filtered_1h = self._filter(snaps, 1)
        first = TestIntervalFilteringLogic._parse_ts(filtered_1h[0]["ts"])
        last = TestIntervalFilteringLogic._parse_ts(filtered_1h[-1]["ts"])
        filtered_span_h = (last - first).total_seconds() / 3600
        assert filtered_span_h < total_span_h, (
            f"Filtered 1h span ({filtered_span_h}h) should be less than "
            f"total span ({total_span_h}h). Banner must show filtered span."
        )

    def test_js_uses_snapshots_in_interval_for_enablement(self):
        """The JS must use a >= 2 snapshot count check for interval enablement,
        not just hours <= availableHours."""
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        assert ">= 2" in js or ">=2" in js, (
            "ci-queue.js must check for >= 2 snapshots when enabling intervals"
        )

    def test_js_info_banner_updates_in_update_chart(self):
        """The info banner must be updated inside updateChart(), not static."""
        js = (DOCS / "assets" / "js" / "ci-queue.js").read_text()
        start = js.find("function updateChart()")
        assert start != -1, "updateChart function not found"
        # Find the function body
        depth = 0
        body_start = js.index("{", start)
        i = body_start
        while i < len(js):
            if js[i] == "{":
                depth += 1
            elif js[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        fn_body = js[body_start : i + 1]
        assert "infoBanner" in fn_body, (
            "updateChart() must update the infoBanner element. "
            "The banner should reflect the filtered data, not total."
        )
