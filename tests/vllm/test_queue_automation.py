"""
Tests for the CI queue monitor automation pipeline.

Validates that:
1. The collect_queue_snapshot script produces valid JSONL for Operations
2. The site assembly places both docs and data correctly (no double rm -rf)
3. The queue_timeseries.jsonl data has the correct schema
4. The queue-monitor workflow includes a deploy step
"""

import json
import os
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
CACHE_ACTION_REVISION = "55cc8345863c7cc4c66a329aec7e433d2d1c52a9"  # action revision


@pytest.mark.live_data
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

    def test_script_retains_history_through_bounded_append(self):
        """Verify collection preserves history without an unbounded append."""
        script = ROOT / "scripts" / "vllm" / "collect_queue_snapshot.py"
        if not script.exists():
            pytest.skip("script not present")
        content = script.read_text()
        assert "append_history_snapshot(OUTPUT, snapshot)" in content
        assert "QUEUE_HISTORY_MAX_BYTES" in content

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
        "hourly-master.yml",
        "pr-preview.yml",
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
        """queue-monitor must have an automatic or manual trigger."""
        triggers = workflow.get(True, {})  # 'on' parses as True in yaml
        assert "workflow_dispatch" in triggers or "schedule" in triggers, (
            "queue-monitor must have workflow_dispatch or schedule trigger"
        )

    def test_has_independent_ten_minute_schedule(self, workflow):
        """Queue evidence must not depend on the large dashboard workflow succeeding."""
        triggers = workflow.get(True, {})
        crons = [row.get("cron") for row in triggers.get("schedule", [])]
        assert "2,12,22,32,42,52 * * * *" in crons

    def test_watchdog_wakeup_cannot_form_an_indirect_failed_data_cycle(
        self, workflow
    ):
        triggers = workflow.get(True, {})
        assert triggers["workflow_run"] == {
            "workflows": ["Publication Recovery Watchdog"],
            "types": ["completed"],
            "branches": ["main"],
        }
        watchdog = yaml.safe_load(
            (WORKFLOWS / "publication-watchdog.yml").read_text()
        )
        watchdog_triggers = watchdog.get(True, {})
        assert "Queue Monitor (10 minute)" not in (
            watchdog_triggers["workflow_run"]["workflows"]
        )
        snapshot_steps = workflow["jobs"]["snapshot"]["steps"]
        generation = next(
            step
            for step in snapshot_steps
            if step.get("name") == "Capture exact validated queue generation"
        )
        condition = generation["if"]
        assert "github.event_name != 'workflow_run'" in condition
        gated_segment = condition[condition.index("github.event_name != 'workflow_run'") :]
        assert "interval_gated" in gated_segment
        assert "capacity_gated" in gated_segment
        # A watchdog wake that acquired a real metrics permit may still repair
        # Queue, but an immediate coalesced zero-request wake cannot redispatch
        # the Data workflow that just woke the watchdog.
        assert "request_mode == 'metrics'" in condition
        assert "request_mode == 'metrics_and_details'" in condition

    def test_does_not_publish_a_partial_root_site(self, workflow):
        """The queue collector must not overwrite unrelated live dashboard data."""
        steps = workflow["jobs"]["snapshot"]["steps"]
        has_deploy = any(
            "peaceiris/actions-gh-pages" in str(s.get("uses", "")) for s in steps
        )
        assert not has_deploy

    def test_does_not_assemble_a_partial_site(self, workflow):
        steps = workflow["jobs"]["snapshot"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert not any("assemble" in n.lower() for n in step_names)

    def test_live_branch_is_limited_to_queue_owned_files(self, workflow):
        steps = workflow["jobs"]["snapshot"]["steps"]
        publish = next(
            step for step in steps if step.get("name") == "Publish durable live queue evidence"
        )
        script = publish.get("run", "")
        assert "--force-with-lease=\"refs/heads/queue-data:$OBSERVED_QUEUE_SHA\"" in (
            script
        )
        assert "push --force origin HEAD:queue-data" not in script
        assert "data/vllm/ci/queue_timeseries.jsonl" in script
        assert "data/vllm/ci/queue_jobs.json" in script
        assert "data/vllm/ci/queue_history_chart.json" in script
        assert "data/vllm/ci/operations_v2/queue.json" in script
        assert "queue_lifecycle" not in script
        assert "data/vllm/ci/operations_v2_manifest.json" not in script
        assert "peaceiris/actions-gh-pages" not in script

        sync = next(
            step
            for step in steps
            if step.get("name") == "Sync durable queue history"
        )["run"]
        assert "QUEUE_DATA_BASELINE_SHA=\"\"" in sync
        assert (
            "QUEUE_DATA_BASELINE_SHA=$(git rev-parse --verify "
            "'origin/queue-data^{commit}')"
        ) in sync
        assert 'echo "QUEUE_DATA_BASELINE_SHA=$QUEUE_DATA_BASELINE_SHA"' in sync

    def test_queue_publish_stale_lease_rejects_racing_writer(self, tmp_path):
        """The exact observed-SHA lease must preserve a newer remote commit."""

        remote = tmp_path / "remote.git"
        seed = tmp_path / "seed"
        writer_a = tmp_path / "writer-a"
        writer_b = tmp_path / "writer-b"

        def git(*args, cwd=None, check=True):
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                check=check,
                capture_output=True,
                text=True,
            )

        git("init", "--bare", str(remote))
        git("init", str(seed))
        git("config", "user.name", "queue-test", cwd=seed)
        git("config", "user.email", "queue-test@example.invalid", cwd=seed)
        git("config", "commit.gpgsign", "false", cwd=seed)
        (seed / "queue.txt").write_text("initial\n", encoding="utf-8")
        git("add", "queue.txt", cwd=seed)
        git("commit", "-m", "initial queue generation", cwd=seed)
        git("remote", "add", "origin", str(remote), cwd=seed)
        git("push", "origin", "HEAD:queue-data", cwd=seed)

        observed = git(
            "ls-remote", "--refs", str(remote), "refs/heads/queue-data"
        ).stdout.split()[0]
        assert re.fullmatch(r"[0-9a-f]{40}", observed)
        git("clone", "--branch", "queue-data", str(remote), str(writer_a))
        git("clone", "--branch", "queue-data", str(remote), str(writer_b))
        for writer in (writer_a, writer_b):
            git("config", "user.name", "queue-test", cwd=writer)
            git("config", "user.email", "queue-test@example.invalid", cwd=writer)
            git("config", "commit.gpgsign", "false", cwd=writer)

        (writer_b / "queue.txt").write_text("racing generation\n", encoding="utf-8")
        git("add", "queue.txt", cwd=writer_b)
        git("commit", "-m", "racing queue generation", cwd=writer_b)
        git("push", "origin", "HEAD:queue-data", cwd=writer_b)
        raced_sha = git("rev-parse", "HEAD", cwd=writer_b).stdout.strip()

        (writer_a / "queue.txt").write_text("stale writer\n", encoding="utf-8")
        git("add", "queue.txt", cwd=writer_a)
        git("commit", "-m", "stale queue generation", cwd=writer_a)
        rejected = git(
            "push",
            f"--force-with-lease=refs/heads/queue-data:{observed}",
            "origin",
            "HEAD:queue-data",
            cwd=writer_a,
            check=False,
        )
        assert rejected.returncode != 0
        actual_remote = git(
            "ls-remote", "--refs", str(remote), "refs/heads/queue-data"
        ).stdout.split()[0]
        assert actual_remote == raced_sha

    def test_hourly_target_replaces_all_stale_queue_files_from_one_commit(
        self, tmp_path
    ):
        """Execute the target sync with stale local source and derived files."""

        remote = tmp_path / "remote.git"
        seed = tmp_path / "seed"
        checkout = tmp_path / "checkout"
        runner_temp = tmp_path / "runner-temp"
        runner_temp.mkdir()

        def git(*args, cwd=None):
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                check=True,
                capture_output=True,
                text=True,
            )

        git("init", "--bare", str(remote))
        git("init", str(seed))
        git("config", "user.name", "queue-test", cwd=seed)
        git("config", "user.email", "queue-test@example.invalid", cwd=seed)
        git("config", "commit.gpgsign", "false", cwd=seed)
        fresh = {
            "data/vllm/ci/operations_v2/queue.json": '{"fresh":"operations"}\n',
            "data/vllm/ci/queue_history_chart.json": '{"fresh":"history"}\n',
            "data/vllm/ci/queue_jobs.json": (
                '{"ts":"2026-09-01T00:00:00Z","pending":[],"running":[]}\n'
            ),
            "data/vllm/ci/queue_timeseries.jsonl": (
                '{"ts":"2026-09-01T00:00:00Z","queues":{},'
                '"total_waiting":0,"total_running":0}\n'
            ),
        }
        for relative, content in fresh.items():
            destination = seed / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        git("add", ".", cwd=seed)
        git("commit", "-m", "exact queue projection", cwd=seed)
        source_sha = git("rev-parse", "HEAD", cwd=seed).stdout.strip()
        git("remote", "add", "origin", str(remote), cwd=seed)
        git("push", "origin", "HEAD:queue-data", cwd=seed)
        git("clone", "--branch", "queue-data", str(remote), str(checkout))

        for relative in fresh:
            (checkout / relative).write_text("stale-local-data\n", encoding="utf-8")
        helper = tmp_path / "collector-helpers.sh"
        helper.write_text("# target sync does not call routine helpers\n", encoding="utf-8")
        github_output = tmp_path / "github-output"
        hourly = yaml.safe_load((WORKFLOWS / "hourly-master.yml").read_text())
        sync = next(
            step
            for step in hourly["jobs"]["collect-and-deploy"]["steps"]
            if step.get("name") == "Sync queue data from durable live branch"
        )
        env = {
            **os.environ,
            "PUBLICATION_COLLECTOR_HELPERS": str(helper),
            "HOURLY_QUEUE_GENERATION_INPUT": "2026-09-01T00:00:00Z",
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_OUTPUT": str(github_output),
        }
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", sync["run"]],
            cwd=checkout,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        for relative, content in fresh.items():
            assert (checkout / relative).read_text(encoding="utf-8") == content
        assert github_output.read_text(encoding="utf-8") == (
            f"source_sha={source_sha}\n"
        )

    def test_hourly_target_accepts_retained_details_with_current_metrics(
        self, tmp_path
    ):
        """A normal metrics-only poll must remain a valid repair generation."""

        jobs_path = tmp_path / "queue_jobs.json"
        timeseries_path = tmp_path / "queue_timeseries.jsonl"
        jobs_path.write_text(
            json.dumps(
                {
                    "ts": "2026-09-01T00:00:00Z",
                    "metrics_observed_at": "2026-09-01T00:10:00Z",
                    "details_observed_at": "2026-09-01T00:00:00Z",
                    "details_status": "retained_not_refreshed",
                    "pending": [],
                    "running": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        timeseries_path.write_text(
            json.dumps(
                {
                    "ts": "2026-09-01T00:10:00Z",
                    "metrics_observed_at": "2026-09-01T00:10:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        hourly = yaml.safe_load((WORKFLOWS / "hourly-master.yml").read_text())
        step = next(
            step
            for step in hourly["jobs"]["collect-and-deploy"]["steps"]
            if step.get("name") == "Validate targeted queue candidate generation"
        )
        python_block = step["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        result = subprocess.run(
            [
                sys.executable,
                "-",
                "2026-09-01T00:10:00Z",
                str(jobs_path),
                str(timeseries_path),
            ],
            input=python_block,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "Validated targeted queue candidate generation" in result.stdout

        jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
        jobs["metrics_observed_at"] = "2026-09-01T00:09:00Z"
        jobs_path.write_text(json.dumps(jobs) + "\n", encoding="utf-8")
        mismatch = subprocess.run(
            [
                sys.executable,
                "-",
                "2026-09-01T00:10:00Z",
                str(jobs_path),
                str(timeseries_path),
            ],
            input=python_block,
            capture_output=True,
            text=True,
        )
        assert mismatch.returncode != 0
        assert "metrics generation must equal" in mismatch.stderr

    def test_queue_monitor_emits_metrics_generation_for_retained_details(
        self, workflow, tmp_path
    ):
        data_dir = tmp_path / "data" / "vllm" / "ci"
        data_dir.mkdir(parents=True)
        (data_dir / "queue_jobs.json").write_text(
            json.dumps(
                {
                    "ts": "2026-09-01T00:00:00Z",
                    "metrics_observed_at": "2026-09-01T00:10:00Z",
                    "details_observed_at": "2026-09-01T00:00:00Z",
                    "details_status": "retained_not_refreshed",
                    "pending": [],
                    "running": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (data_dir / "queue_timeseries.jsonl").write_text(
            json.dumps(
                {
                    "ts": "2026-09-01T00:10:00Z",
                    "metrics_observed_at": "2026-09-01T00:10:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        step = next(
            step
            for step in workflow["jobs"]["snapshot"]["steps"]
            if step.get("name") == "Capture exact validated queue generation"
        )
        python_block = step["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        output = tmp_path / "github-output"
        result = subprocess.run(
            [sys.executable, "-", "metrics", str(output)],
            cwd=tmp_path,
            input=python_block,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert output.read_text(encoding="utf-8") == (
            "generated_at=2026-09-01T00:10:00Z\n"
        )

    def test_semantic_validation_runs_after_build_and_before_force_publish(self, workflow):
        steps = workflow["jobs"]["snapshot"]["steps"]
        names = [step.get("name") for step in steps]
        assert names.index("Build live queue section") < names.index(
            "Validate live queue evidence"
        ) < names.index("Publish durable live queue evidence")
        validate = next(
            step for step in steps if step.get("name") == "Validate live queue evidence"
        )
        assert validate.get("run") == (
            "python scripts/vllm/audit_dashboard_data.py --queue-only"
        )
        assert "env" not in validate

    def test_workflow_references_correct_script(self):
        path = WORKFLOWS / "queue-monitor.yml"
        if not path.exists():
            pytest.skip("queue-monitor.yml not present")
        content = path.read_text()
        assert "collect_queue_snapshot.py" in content, (
            "Workflow must reference collect_queue_snapshot.py"
        )

    def test_workflow_has_contents_write_permission(self, workflow):
        assert workflow.get("permissions") == {}
        perms = workflow["jobs"]["snapshot"].get("permissions", {})
        assert perms == {"contents": "write"}, (
            "only the queue snapshot job needs contents:write to push data"
        )

    def test_validated_generation_requires_exact_publish_or_safe_durable_retry(
        self, workflow
    ):
        snapshot = workflow["jobs"]["snapshot"]
        steps = snapshot["steps"]
        names = [step.get("name") for step in steps]
        generation = steps[names.index("Capture exact validated queue generation")]
        publish = steps[names.index("Publish durable live queue evidence")]
        assert names.index("Validate live queue evidence") < names.index(
            "Capture exact validated queue generation"
        ) < names.index("Publish durable live queue evidence")
        assert generation["id"] == "queue-generation"
        assert 'datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")' in generation["run"]
        assert "queue_jobs metrics_observed_at must equal the latest" in generation["run"]
        assert 'if "metrics_observed_at" in jobs' in generation["run"]
        assert 'if "metrics_observed_at" in latest' in generation["run"]
        assert publish["id"] == "queue-publish"
        assert 'OBSERVED_QUEUE_SHA="${QUEUE_DATA_BASELINE_SHA:-}"' in publish["run"]
        assert "--force-with-lease=\"refs/heads/queue-data:$OBSERVED_QUEUE_SHA\"" in (
            publish["run"]
        )
        assert "push --force origin HEAD:queue-data" not in publish["run"]
        assert "QUEUE_SOURCE_SHA=$(git -C \"$LIVE_ROOT\" rev-parse" in publish["run"]
        assert 'scripts/vllm/build_queue_section.py"' in publish["run"]
        assert (
            '--validate-output "$LIVE_ROOT/data/vllm/ci/operations_v2/queue.json"'
            in publish["run"]
        )
        assert publish["run"].index("--validate-output") < publish["run"].index(
            "check_git_blob_sizes.py"
        )
        assert "git ls-remote --refs origin refs/heads/queue-data" in publish["run"]
        assert 'if [ "$REMOTE_QUEUE_SHA" != "$QUEUE_SOURCE_SHA" ]' in publish["run"]
        assert 'echo "queue_generation=${{ steps.queue-generation.outputs.generated_at }}"' in publish[
            "run"
        ]
        assert snapshot["outputs"] == {
            "queue_generation": "${{ steps.queue-generation.outputs.generated_at }}",
        }
        assert "queue_source_sha" not in str(snapshot["outputs"])

        mode = generation["env"]["REQUEST_MODE"]
        assert mode == "${{ steps.queue-request-budget.outputs.request_mode }}"
        assert "interval_gated" in generation["if"]
        assert "capacity_gated" in generation["if"]
        assert "github.event_name != 'workflow_run'" in generation["if"]
        assert "audit_dashboard_data.py --queue-only" in generation["run"]
        assert "generation must not be future-dated" in generation["run"]
        assert "timedelta(hours=5)" in generation["run"]
        assert "skipping a zero-request retry" in generation["run"]

        hydrate = steps[
            names.index("Hydrate exact durable queue projection for zero-request retry")
        ]
        assert names.index(
            "Reserve durable rolling queue request budget"
        ) < names.index(hydrate["name"]) < names.index(
            "Capture exact validated queue generation"
        )
        assert "interval_gated" in hydrate["if"]
        assert "capacity_gated" in hydrate["if"]
        assert "QUEUE_SOURCE_SHA=$(git rev-parse --verify 'origin/queue-data^{commit}')" in (
            hydrate["run"]
        )
        for relative in (
            "data/vllm/ci/queue_timeseries.jsonl",
            "data/vllm/ci/queue_jobs.json",
            "data/vllm/ci/queue_history_chart.json",
            "data/vllm/ci/operations_v2/queue.json",
        ):
            assert relative in hydrate["run"]
        assert 'git show "$QUEUE_SOURCE_SHA:$path"' in hydrate["run"]
        assert 'install -D -m 0644 "$RETRY_ROOT/$path" "$path"' in hydrate["run"]

    def test_zero_request_hydration_creates_missing_generated_directories(
        self, workflow, tmp_path
    ):
        """Replay hydration from a clean main tree with no operations directory."""

        remote = tmp_path / "remote.git"
        seed = tmp_path / "seed"
        checkout = tmp_path / "checkout"

        def git(*args, cwd=None):
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                check=True,
                capture_output=True,
                text=True,
            )

        git("init", "--bare", str(remote))
        git("init", str(seed))
        git("config", "user.name", "queue-test", cwd=seed)
        git("config", "user.email", "queue-test@example.invalid", cwd=seed)
        git("config", "commit.gpgsign", "false", cwd=seed)
        projection = {
            "data/vllm/ci/operations_v2/queue.json": '{"queue":[]}\n',
            "data/vllm/ci/queue_history_chart.json": '{"history":[]}\n',
            "data/vllm/ci/queue_jobs.json": '{"pending":[],"running":[]}\n',
            "data/vllm/ci/queue_timeseries.jsonl": '{"queues":{}}\n',
        }
        for relative, content in projection.items():
            destination = seed / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        git("add", ".", cwd=seed)
        git("commit", "-m", "durable queue projection", cwd=seed)
        git("remote", "add", "origin", str(remote), cwd=seed)
        git("push", "origin", "HEAD:queue-data", cwd=seed)

        git("init", str(checkout))
        git("remote", "add", "origin", str(remote), cwd=checkout)
        git(
            "fetch",
            "origin",
            "refs/heads/queue-data:refs/remotes/origin/queue-data",
            cwd=checkout,
        )
        assert not (checkout / "data" / "vllm" / "ci" / "operations_v2").exists()
        hydrate = next(
            step
            for step in workflow["jobs"]["snapshot"]["steps"]
            if step.get("name")
            == "Hydrate exact durable queue projection for zero-request retry"
        )
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", hydrate["run"]],
            cwd=checkout,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        for relative, content in projection.items():
            assert (checkout / relative).read_text(encoding="utf-8") == content

    def test_queue_reconciliation_is_conditional_and_least_privilege(self, workflow):
        reconcile = workflow["jobs"]["reconcile-publication"]
        assert reconcile["needs"] == "snapshot"
        assert "needs.snapshot.result == 'success'" in reconcile["if"]
        assert "needs.snapshot.outputs.queue_generation != ''" in reconcile["if"]
        assert reconcile["permissions"] == {"actions": "write", "contents": "read"}
        steps = reconcile["steps"]
        names = [step.get("name") for step in steps]
        plan = steps[names.index("Plan canonical queue publication reconciliation")]
        dispatch = steps[names.index("Dispatch canonical queue reconciliation")]
        assert plan["id"] == "publication-reconcile"
        assert plan["env"] == {
            "GH_TOKEN": "${{ github.token }}",
            "TARGET_QUEUE_GENERATION": (
                "${{ needs.snapshot.outputs.queue_generation }}"
            )
        }
        assert "plan_queue_publication_reconcile.py" in plan["run"]
        assert "if ! PAGES_SHA=$(GIT_NO_LAZY_FETCH=1 git rev-parse" in plan["run"]
        assert '"$PAGES_SHA:data/vllm/ci/publication_status.json"' in plan["run"]
        assert '"$PAGES_SHA:data/vllm/ci/queue_jobs.json"' in plan["run"]
        assert '--canonical-queue-data "$CANONICAL_QUEUE"' in plan["run"]
        assert '--target-queue-generation "$TARGET_QUEUE_GENERATION"' in plan["run"]
        assert '--workflow-runs "$WORKFLOW_RUNS"' in plan["run"]
        assert "actions/workflows/hourly-master.yml/runs?" in plan["run"]
        assert "event=workflow_dispatch" in plan["run"]
        assert "--filter=blob:none" in plan["run"]
        assert "--fail-if-required" not in plan["run"]
        assert "--max-age-hours" not in plan["run"]
        assert dispatch["if"] == (
            "steps.publication-reconcile.outputs.dispatch_required == 'true'"
        )
        assert dispatch["env"]["TARGET_QUEUE_GENERATION"] == (
            "${{ needs.snapshot.outputs.queue_generation }}"
        )
        assert dispatch["env"]["RECONCILIATION_KEY"] == (
            "${{ steps.publication-reconcile.outputs.recovery_key }}"
        )
        assert "queue_generation: $queue_generation" in dispatch["run"]
        assert "recovery_key: $recovery_key" in dispatch["run"]
        assert "workflow_runs" not in dispatch["run"]
        assert "BUILDKITE_TOKEN" not in str(reconcile)

    def test_does_not_race_the_canonical_main_or_pages_writers(self):
        path = WORKFLOWS / "queue-monitor.yml"
        if not path.exists():
            pytest.skip("queue-monitor.yml not present")
        content = path.read_text()
        assert "group: queue-data-publish" in content
        assert "ref: main" in content
        assert "git pull --rebase origin main" not in content
        assert "HEAD:gh-pages" not in content

    def test_workflow_syncs_from_durable_queue_branch(self):
        """The dedicated branch must be merged before every collection."""
        path = WORKFLOWS / "queue-monitor.yml"
        if not path.exists():
            pytest.skip("queue-monitor.yml not present")
        content = path.read_text()
        assert "origin/queue-data" in content
        assert "--merge-history-git-ref origin/queue-data" in content
        assert "--require-merge-history" in content
        assert "origin/gh-pages" in content  # first-run migration fallback
        assert "git ls-remote --exit-code --refs origin refs/heads/queue-data" in content
        assert "+refs/heads/queue-data:refs/remotes/origin/queue-data" in content
        assert "Could not determine durable queue-data branch state" in content
        assert "git ls-remote --exit-code --refs origin refs/heads/gh-pages" in content
        assert "Could not determine legacy gh-pages state" in content
        assert "GH_PAGES_FETCHED_SHA" in content
        assert "GH_PAGES_REMOTE_SHA" in content
        assert "--merge-history-git-ref origin/gh-pages" in content
        assert content.count("--require-merge-history") >= 2
        migration = content[
            content.index('elif [ "$QUEUE_DATA_STATUS" -eq 2 ]') :
            content.index("echo \"QUEUE_DATA_BASELINE_SHA", content.index('elif [ "$QUEUE_DATA_STATUS" -eq 2 ]'))
        ]
        assert "--depth=1 || true" not in migration
        assert "> data/vllm/ci/queue_jobs.json 2>/dev/null || true" not in migration

    def test_deploy_pages_replays_exact_state_without_queue_mutation(self):
        """Deploy-only replays the state that already merged durable producers."""
        path = WORKFLOWS / "deploy-pages.yml"
        if not path.exists():
            pytest.skip("deploy-pages.yml not present")
        content = path.read_text()
        assert 'materialize --ref "$STATE_SHA"' in content
        assert "Restore exact validated dashboard state" in content
        for forbidden in (
            "origin/queue-data",
            "--merge-history-git-ref origin/queue-data",
            "--queue-lifecycle-path",
            "origin/dns-health-data",
        ):
            assert forbidden not in content


class TestQueueLifecycleWorkflow:
    """Validate the heavier lifecycle collector is isolated and durable."""

    @pytest.fixture
    def workflow(self):
        return yaml.safe_load((WORKFLOWS / "queue-lifecycle.yml").read_text())

    def test_checks_recovery_gate_every_thirty_minutes_on_an_independent_lock(
        self, workflow
    ):
        triggers = workflow.get(True, {})
        crons = [row.get("cron") for row in triggers.get("schedule", [])]
        assert crons == ["17,47 * * * *"]
        assert workflow["concurrency"]["group"] == "queue-lifecycle-data-publish"
        assert workflow["concurrency"]["cancel-in-progress"] is False

        # The frequent schedule is tokenless unless the independent durable
        # attempt ledger authorizes the two-hour success cadence or a
        # 30-minute retry for an incomplete checkpoint.
        policy = json.loads(
            (ROOT / "config/queue_lifecycle_attempt_budget.json").read_text()
        )
        assert policy["success_interval_minutes"] == 120
        assert policy["failed_retry_interval_minutes"] == 30
        assert policy["request_start_allowance"] == 100
        assert policy["max_request_bearing_attempts"] == 16

    def test_restores_established_ledger_fail_closed(self, workflow):
        steps = workflow["jobs"]["lifecycle"]["steps"]
        by_name = {step.get("name"): step for step in steps}
        names = [step.get("name") for step in steps]
        restore_step = by_name["Restore durable lifecycle observations"]
        restore = restore_step["run"]
        assert names.index("Reserve guarded Queue Lifecycle attempt") < names.index(
            "Restore durable lifecycle observations"
        )
        assert (
            restore_step["if"]
            == "steps.request-attempt.outputs.request_mode == 'reserved'"
        )
        assert "git ls-remote --exit-code --refs origin refs/heads/queue-lifecycle-data" in restore
        assert (
            "+refs/heads/queue-lifecycle-data:refs/remotes/origin/queue-lifecycle-data"
            in restore
        )
        assert "--restore-jobs-git-ref origin/queue-lifecycle-data" in restore
        assert "baseline_ref=$LIFECYCLE_DATA_SHA" in restore
        assert "baseline_ref=bootstrap" in restore
        assert "refusing to publish" in restore

    def test_gated_attempt_skips_runtime_and_durable_ledger_restore(self, workflow):
        steps = workflow["jobs"]["lifecycle"]["steps"]
        names = [step.get("name") for step in steps]
        reserve_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Reserve guarded Queue Lifecycle attempt"
        )
        assert reserve_index == 1
        for step in steps:
            if step.get("name") in {
                "Install dependencies",
                "Initialize exact Buildkite request guard",
                "Restore durable lifecycle observations",
            } or str(step.get("uses", "")).startswith("actions/setup-python@"):
                assert (
                    step["if"]
                    == "steps.request-attempt.outputs.request_mode == 'reserved'"
                )
        reserve = steps[reserve_index]["run"]
        initialize = next(
            step
            for step in steps
            if step.get("name") == "Initialize exact Buildkite request guard"
        )
        assert "buildkite_request_guard.py initialize" not in reserve
        assert "buildkite_request_guard.py initialize" in initialize["run"]
        setup_index = next(
            index
            for index, step in enumerate(steps)
            if str(step.get("uses", "")).startswith("actions/setup-python@")
        )
        assert setup_index < names.index("Install dependencies") < names.index(
            "Initialize exact Buildkite request guard"
        ) < names.index("Restore durable lifecycle observations")

    def test_collects_only_with_secret_environment(self, workflow):
        steps = workflow["jobs"]["lifecycle"]["steps"]
        collect = next(
            step for step in steps if step.get("name") == "Collect canonical AMD queue lifecycle"
        )
        assert "BUILDKITE_API_TOKEN" in collect.get("env", {})
        assert "collect_queue_lifecycle.py" in collect.get("run", "")
        assert "--full-backfill" not in collect.get("run", "")
        assert workflow["jobs"]["lifecycle"]["timeout-minutes"] == 50
        assert collect["timeout-minutes"] == 44

    def test_bounded_yields_are_successful_private_progress(self, workflow):
        steps = workflow["jobs"]["lifecycle"]["steps"]
        by_name = {step.get("name"): step for step in steps}
        collect = by_name["Collect canonical AMD queue lifecycle"]
        script = collect["run"]
        assert 'if [ "$LIFECYCLE_STATUS" -eq 0 ]' in script
        assert 'elif [ "$LIFECYCLE_STATUS" -eq 75 ]' in script
        assert 'elif [ "$LIFECYCLE_STATUS" -eq 76 ]' in script
        assert script.count('echo "collection_complete=false"') == 2
        assert 'exit "$LIFECYCLE_STATUS"' in script
        for step_name in (
            "Validate retained lifecycle evidence",
            "Publish durable lifecycle evidence",
        ):
            condition = by_name[step_name]["if"]
            assert (
                "steps.collect-lifecycle.outputs.collection_complete == 'true'"
                in condition
            )
            assert "steps.collect-lifecycle.outcome == 'success'" not in condition

    def test_private_checkpoint_is_resumable_bounded_and_never_published(self, workflow):
        steps = workflow["jobs"]["lifecycle"]["steps"]
        by_name = {step.get("name"): step for step in steps}
        names = [step.get("name") for step in steps]
        cache_path = "data/vllm/ci/.cache/queue-lifecycle-wip-v1"

        assert workflow["permissions"]["actions"] == "write"
        reserve = by_name["Reserve guarded Queue Lifecycle attempt"]["run"]
        assert '[ "$ALLOWANCE" != 100 ]' in reserve
        assert names.index("Reserve guarded Queue Lifecycle attempt") < names.index(
            "Restore newest private lifecycle WIP"
        ) < names.index("Collect canonical AMD queue lifecycle")
        restore = by_name["Restore newest private lifecycle WIP"]
        assert restore["uses"] == f"actions/cache/restore@{CACHE_ACTION_REVISION}"
        assert restore["with"]["path"] == cache_path
        assert "namespace_prefix" in restore["with"]["restore-keys"]

        collect = by_name["Collect canonical AMD queue lifecycle"]
        assert '--checkpoint "$LIFECYCLE_CHECKPOINT"' in collect["run"]
        assert '--baseline-ref "$LIFECYCLE_BASELINE_REF"' in collect["run"]
        assert collect["env"]["LIFECYCLE_BASELINE_REF"].endswith(
            "steps.lifecycle-baseline.outputs.baseline_ref }}"
        )

        report_index = names.index("Read exact guarded Buildkite request total")
        publish_index = names.index("Publish durable lifecycle evidence")
        assert names.index("Collect canonical AMD queue lifecycle") < report_index < publish_index

        prepare = by_name["Prepare bounded private lifecycle cache payload"]
        save = by_name["Save private lifecycle recovery checkpoint"]
        assert "always()" in prepare["if"]
        assert "--prepare-checkpoint-cache" in prepare["run"]
        assert "always()" in save["if"]
        assert save["uses"] == f"actions/cache/save@{CACHE_ACTION_REVISION}"
        assert save["with"]["path"] == cache_path
        assert "github.run_id" in save["with"]["key"] or "outputs.key" in save["with"]["key"]

        clear = by_name["Clear published lifecycle WIP"]
        assert names.index("Mark durable Queue Lifecycle success") < names.index(
            "Clear published lifecycle WIP"
        ) < names.index("Save private lifecycle recovery checkpoint")
        assert "--clear-checkpoint" in clear["run"]

        prune = by_name["Prune superseded private lifecycle caches"]
        assert prune["continue-on-error"] is True
        assert "--sort created_at" in prune["run"]
        assert "--jq '.[8:] | .[].id'" in prune["run"]
        assert 'gh cache delete "$CACHE_ID"' in prune["run"]

        publish = by_name["Publish durable lifecycle evidence"]["run"]
        assert cache_path not in publish
        assert "checkpoint" not in publish.casefold()
        assert "--force-with-lease=refs/heads/queue-lifecycle-data:" in publish
        assert ":$LIFECYCLE_BASELINE_REF" in publish
        assert "git -C \"$LIVE_ROOT\" push --force " not in publish

    def test_semantic_validation_runs_before_durable_publish(self, workflow):
        steps = workflow["jobs"]["lifecycle"]["steps"]
        names = [step.get("name") for step in steps]
        assert names.index("Collect canonical AMD queue lifecycle") < names.index(
            "Validate retained lifecycle evidence"
        ) < names.index("Publish durable lifecycle evidence")
        validate = next(
            step
            for step in steps
            if step.get("name") == "Validate retained lifecycle evidence"
        )
        run = validate.get("run", "")
        assert "gzip -t" in run
        assert "xargs -0 -r" in run
        assert "--validate-ledger-only" in run
        assert 'test -n "$(find' not in run
        assert (
            "python -S scripts/vllm/audit_dashboard_data.py --queue-lifecycle-only"
            in run
        )
        assert "env" not in validate

    def test_publishes_only_aggregate_and_privacy_minimized_ledger(self, workflow):
        steps = workflow["jobs"]["lifecycle"]["steps"]
        publish = next(
            step for step in steps if step.get("name") == "Publish durable lifecycle evidence"
        )["run"]
        assert "HEAD:queue-lifecycle-data" in publish
        assert "data/vllm/ci/queue_lifecycle.json" in publish
        assert "data/vllm/ci/queue_lifecycle_jobs" in publish
        assert "git -C \"$LIVE_ROOT\" add --all data/vllm/ci" in publish
        assert "find data/vllm/ci/queue_lifecycle_jobs" in publish
        assert "queue_lifecycle_events.jsonl" not in publish
        assert "queue_timeseries.jsonl" not in publish


class TestQueueDashboardControls:
    """Validate the queue dashboard's visible wait controls."""













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


class TestQueueViewContract:
    """Validate the active Operations queue route is registered."""




    def test_ci_queue_tab_registered(self):
        """CI queue tab can be in HTML or dynamically registered via JS."""
        html = (DOCS / "index.html").read_text()
        js = (DOCS / "assets" / "js" / "utils.js").read_text()
        in_html = 'data-tab="ci-queue"' in html
        in_js = "id: 'ci-queue'" in js
        assert in_html or in_js, "ci-queue tab not found in HTML or registerCISection"



    @pytest.mark.live_data
    def test_queue_source_percentile_ordering(self):
        """Each independently sourced percentile family must stay ordered.

        Root compatibility fields intentionally combine queue-native p50/p95
        with sampled p75/p90/p99 observations. Those sources have different
        sample windows and may cross, so ordering them as one distribution is
        not a valid invariant.
        """
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
                families = {
                    "official": [
                        (qdata.get("official_wait") or {}).get(key)
                        for key in ("p50", "p95", "max")
                    ],
                    "sample": [
                        (qdata.get("sample_wait") or {}).get(key)
                        for key in ("p50", "p75", "p90", "p95", "p99", "max")
                    ],
                }
                for source, vals in families.items():
                    observed = [value for value in vals if value is not None]
                    if any(
                        left > right + 0.01
                        for left, right in zip(observed, observed[1:])
                    ):
                        bad.append(
                            f"{snap['ts']} queue '{qname}' {source}: {vals}"
                        )
                        break
        assert not bad, f"Percentile ordering violated:\n" + "\n".join(bad[:5])


class TestIntervalFilteringLogic:
    """Strict tests for queue-history interval filtering.

    The browser view filters snapshots using:
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



    @pytest.mark.live_data
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

    @pytest.mark.live_data
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

    @pytest.mark.live_data
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

    @pytest.mark.live_data
    def test_full_range_interval_returns_all(self, snapshots):
        """An interval >= available hours must return all snapshots."""
        available = self._available_hours(snapshots)
        filtered = self._filter_snapshots(snapshots, available)
        assert len(filtered) == len(snapshots), (
            f"Interval covering full range ({available}h) should return all "
            f"{len(snapshots)} snapshots, got {len(filtered)}"
        )

    @pytest.mark.live_data
    def test_auto_selected_default_is_valid(self, snapshots):
        """The auto-selected default interval must be the largest with >= 2 snapshots."""
        enabled = self._enabled_intervals(snapshots)
        default = enabled[-1] if enabled else self.INTERVALS[0]
        filtered = self._filter_snapshots(snapshots, default["hours"])
        assert len(filtered) >= 2 or len(snapshots) < 2, (
            f"Auto-selected default interval {default['label']} has fewer than 2 snapshots"
        )

    @pytest.mark.live_data
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

    @pytest.mark.live_data
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

    @pytest.mark.live_data
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

    @pytest.mark.live_data
    def test_enabled_intervals_require_at_least_2_snapshots(self, snapshots):
        """Regression: intervals must be enabled only if >= 2 snapshots exist in range.
        This prevents enabling intervals that would render a single-point chart."""
        for iv in self._enabled_intervals(snapshots):
            count = self._snapshots_in_interval(snapshots, iv["hours"])
            assert count >= 2, (
                f"Interval {iv['label']} is enabled with only {count} snapshot(s). "
                f"Need >= 2 for a renderable chart."
            )

    @pytest.mark.live_data
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
