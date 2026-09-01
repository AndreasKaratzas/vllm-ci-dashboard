"""Tests for GitHub Actions workflow YAML integrity, CI collect completeness,
framework isolation, and cron schedule safety.

These tests ensure:
- All workflow files are valid YAML with required fields
- ci-collect.yml exercises its collectors without writing main
- Deploying workflows sync vLLM CI data from gh-pages (prevents clobbering)
- No cron schedule conflicts between hourly workflows
"""

import ast
import json
import re
from pathlib import Path, PurePosixPath

import pytest
import yaml

from vllm.publication_surfaces import surface_for_path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load_workflow(name):
    path = WORKFLOWS / name
    assert path.exists(), f"Workflow file not found: {name}"
    return yaml.safe_load(path.read_text())


def _load_workflow_text(name):
    path = WORKFLOWS / name
    assert path.exists(), f"Workflow file not found: {name}"
    return path.read_text()


# ---------------------------------------------------------------------------
# 3a. Workflow YAML validation
# ---------------------------------------------------------------------------


class TestWorkflowYAML:
    """Validate all workflow files parse and have required fields."""

    def test_all_workflows_parse_as_yaml(self):
        yml_files = list(WORKFLOWS.glob("*.yml"))
        assert len(yml_files) >= 5, f"Expected at least 5 workflow files, found {len(yml_files)}"
        for f in yml_files:
            try:
                data = yaml.safe_load(f.read_text())
                assert isinstance(data, dict), f"{f.name}: parsed but is not a dict"
            except yaml.YAMLError as e:
                raise AssertionError(f"{f.name}: invalid YAML — {e}") from e

    def test_all_workflows_have_name_on_jobs(self):
        for f in WORKFLOWS.glob("*.yml"):
            data = yaml.safe_load(f.read_text())
            assert "name" in data, f"{f.name}: missing 'name' field"
            # YAML parses 'on:' as the boolean True key
            assert True in data or "on" in data, f"{f.name}: missing 'on' trigger field"
            assert "jobs" in data, f"{f.name}: missing 'jobs' field"

    def test_expression_bearing_scalars_fit_github_parser_limit(self):
        """GitHub rejects any interpolated YAML scalar longer than 21,000 chars."""

        def scalars(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield from scalars(key)
                    yield from scalars(child)
            elif isinstance(value, list):
                for child in value:
                    yield from scalars(child)
            elif isinstance(value, str):
                yield value

        for workflow in WORKFLOWS.glob("*.yml"):
            data = yaml.safe_load(workflow.read_text())
            for scalar in scalars(data):
                if "${{" not in scalar:
                    continue
                assert len(scalar) <= 21_000, (
                    f"{workflow.name}: expression-bearing scalar is {len(scalar)} "
                    "characters; GitHub's parser limit is 21000"
                )

    def test_no_duplicate_concurrency_groups(self):
        # gh-pages-deploy is intentionally shared by every workflow that writes
        # to the gh-pages branch so those deploys serialize instead of racing.
        SHARED_GROUPS = {"gh-pages-deploy"}
        groups = {}
        for f in WORKFLOWS.glob("*.yml"):
            data = yaml.safe_load(f.read_text())
            conc = data.get("concurrency", {})
            if isinstance(conc, dict):
                group = conc.get("group")
            elif isinstance(conc, str):
                group = conc
            else:
                continue
            if group and group not in SHARED_GROUPS:
                assert group not in groups, (
                    f"Duplicate concurrency group '{group}' in {f.name} and {groups[group]}"
                )
                groups[group] = f.name

    def test_all_gh_pages_writers_share_concurrency_group(self):
        for f in WORKFLOWS.glob("*.yml"):
            text = f.read_text()
            deploys_branch = "publish_branch: gh-pages" in text or "ref: gh-pages" in text
            if not deploys_branch:
                continue
            data = yaml.safe_load(text)
            conc = data.get("concurrency", {})
            if isinstance(conc, dict):
                group = conc.get("group")
                cancel = conc.get("cancel-in-progress")
                queue = conc.get("queue")
            else:
                group = conc
                cancel = None
                queue = None
            assert group == "gh-pages-deploy", (
                f"{f.name} writes to gh-pages but does not share the gh-pages-deploy "
                "concurrency group"
            )
            assert cancel is False, (
                f"{f.name} writes to gh-pages but still has cancel-in-progress enabled"
            )
            assert queue == "max", (
                f"{f.name} writes to gh-pages but can replace an already-pending writer"
            )

    def test_repo_governance_workflow_exists_and_watches_issues_and_prs(self):
        wf = _load_workflow("repo-governance.yml")
        triggers = wf.get(True, wf.get("on", {}))
        assert "issues" in triggers, "repo-governance.yml must watch issue creation"
        assert "pull_request_target" in triggers, (
            "repo-governance.yml must watch PR creation on the base repo context"
        )
        perms = wf.get("permissions") or {}
        assert perms.get("issues") == "write"
        assert perms.get("pull-requests") == "write"

    def test_rebase_failures_are_aborted_before_deploy_continues(self):
        offenders = []
        for f in WORKFLOWS.glob("*.yml"):
            text = f.read_text()
            if "git pull --rebase origin main" not in text:
                continue
            if "git rebase --abort || true" not in text:
                offenders.append(f.name)
        assert not offenders, (
            "Workflows that rebase against origin/main must abort failed rebases "
            "before later steps continue, otherwise conflict markers can leak "
            f"into published data: {offenders}"
        )

    def test_corruption_redeploy_only_runs_after_post_deploy_validation(self):
        """A pre-deploy failure must never publish an empty ``_site`` tree."""
        for f in WORKFLOWS.glob("*.yml"):
            data = yaml.safe_load(f.read_text())
            for job in (data.get("jobs") or {}).values():
                steps = job.get("steps") or []
                if not any(
                    step.get("name", "").startswith("Redeploy if corrupted") for step in steps
                ):
                    continue

                validation = next(
                    (
                        step
                        for step in steps
                        if step.get("name", "").startswith("Post-deploy validation")
                    ),
                    None,
                )
                assert validation and validation.get("id") == "post-deploy-validation", (
                    f"{f.name} has a corruption redeploy but no id on post-deploy validation"
                )
                validation_script = validation.get("run", "")
                assert "origin/gh-pages:data/vllm/ci/org_summary.json" in validation_script
                assert "python -m json.tool" in validation_script
                assert "_site/data/vllm/ci/org_summary.json" in validation_script
                assert "cmp -s" in validation_script
                assert (
                    "origin/gh-pages:data/vllm/ci/operations_v2_manifest.json"
                    in validation_script
                )
                assert (
                    "_site/data/vllm/ci/operations_v2_manifest.json"
                    in validation_script
                )
                assert (
                    "scripts/vllm/verify_published_operations_bundle.py"
                    in validation_script
                )
                assert "--git-ref origin/gh-pages" in validation_script

                redeploy = next(
                    step
                    for step in steps
                    if step.get("name", "").startswith("Redeploy if corrupted")
                )
                assert redeploy.get("id") == "corruption-redeploy"
                condition = str(redeploy.get("if", ""))
                assert "steps.post-deploy-validation.outcome == 'failure'" in condition, (
                    f"{f.name} corruption redeploy must only run when post-deploy "
                    "validation itself fails"
                )
                assert "hashFiles('_site/index.html') != ''" in condition, (
                    f"{f.name} corruption redeploy must require an assembled site"
                )

                recovery = next(
                    step
                    for step in steps
                    if step.get("name") == "Confirm corruption recovery"
                )
                recovery_condition = str(recovery.get("if", ""))
                assert "always()" in recovery_condition
                assert (
                    "steps.post-deploy-validation.outcome == 'failure'"
                    in recovery_condition
                )
                assert (
                    "steps.corruption-redeploy.outcome == 'success'"
                    in recovery_condition
                )
                recovery_script = recovery.get("run", "")
                assert (
                    "git ls-tree -r --name-only origin/gh-pages -- data/"
                    in recovery_script
                )
                assert (
                    "data files remain corrupted after recovery deployment"
                    in recovery_script
                )
                assert "recovered-org-summary.json" in recovery_script
                assert "recovered-operations-manifest.json" in recovery_script
                assert (
                    "scripts/vllm/verify_published_operations_bundle.py"
                    in recovery_script
                )
                assert "--git-ref origin/gh-pages" in recovery_script


class TestPrimaryCIWorkflow:
    """Keep the required test and browser failure propagation in primary CI."""

    def test_pytest_pipeline_propagates_the_pytest_exit_code(self):
        data = _load_workflow("ci.yml")
        steps = data["jobs"]["test"]["steps"]
        run_tests = next(step for step in steps if step.get("name") == "Run tests")
        assert run_tests.get("id") == "run-tests"
        assert "set -o pipefail" in run_tests.get("run", "")
        assert (
            "pytest tests/ -m 'not live_data' -v --tb=short 2>&1 | tee test-output.txt"
            in run_tests.get("run", "")
        )

    def test_pr_comment_uses_the_terminal_pytest_summary(self):
        data = _load_workflow("ci.yml")
        steps = data["jobs"]["test"]["steps"]
        comment = next(
            step for step in steps if step.get("name") == "Comment test results on PR"
        )["with"]["script"]
        assert "[...lines].reverse().find" in comment
        assert "outcomePattern" in comment
        assert "steps.run-tests.outcome" in comment
        assert "fs.existsSync('test-output.txt')" in comment
        assert "lines.find(l => l.includes('passed')" not in comment

    def test_e2e_smoke_runs_the_pinned_playwright_suite(self):
        data = _load_workflow("ci.yml")
        steps = data["jobs"]["e2e-smoke"]["steps"]
        names = [step.get("name") for step in steps]
        assert "Install browser smoke dependencies" in names
        assert "Install Chromium" in names
        assert "Run dashboard browser smoke" in names

        package = REPO_ROOT / "tests" / "browser" / "package.json"
        package_text = package.read_text()
        assert '"@playwright/test": "1.62.1"' in package_text
        package_data = json.loads(package_text)
        pretest = package_data["scripts"]["pretest"]
        assert "scripts/vllm/build_operations_snapshot.py" in pretest
        assert pretest.index("build_operations_snapshot.py") < pretest.index(
            "scripts/build_site.py"
        )

        smoke = (REPO_ROOT / "tests" / "browser" / "dashboard-smoke.spec.mjs").read_text()
        assert "'/#ci-hotness'" in smoke
        assert "CI Workload Trajectory" in smoke
        assert "browserErrors" in smoke
        assert ".ops-error" in smoke
        assert "12_500" in smoke


@pytest.mark.parametrize("workflow", ["ci.yml", "nightly-ci.yml"])
def test_nonpublication_ci_excludes_live_snapshot_contracts(workflow: str) -> None:
    text = _load_workflow_text(workflow)
    assert "pytest tests/ -m 'not live_data'" in text
    assert "pytest tests/ -m 'live_data'" not in text
    assert "live-data-audit" not in text


# ---------------------------------------------------------------------------
# 3b. CI Collect workflow completeness
# ---------------------------------------------------------------------------


class TestHourlyMasterWorkflow:
    """Validate hourly-master.yml runs all collection, tests, and deploys."""

    def test_exists(self):
        assert (WORKFLOWS / "hourly-master.yml").exists()

    def test_calls_collect_ci_script(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_ci.py" in text

    def test_manual_ci_history_default_covers_completed_nightly_evidence(self):
        workflow = _load_workflow("hourly-master.yml")
        triggers = workflow.get(True, workflow.get("on", {}))
        inputs = triggers["workflow_dispatch"]["inputs"]

        assert inputs["ci_days"]["default"] == "8"
        assert inputs["dns_generation"]["default"] == ""
        assert inputs["watchdog_generation"]["default"] == ""
        assert 'github.event.inputs.ci_days || \'8\'' in _load_workflow_text(
            "hourly-master.yml"
        )

    def test_dns_reconciliation_is_generation_acknowledged_and_idempotent(self):
        workflow = _load_workflow("hourly-master.yml")
        collect = workflow["jobs"]["collect-and-deploy"]
        preflight = workflow["jobs"]["dns-reconcile-preflight"]
        watchdog_preflight = workflow["jobs"]["publication-watchdog-preflight"]
        cadence_preflight = workflow["jobs"]["cadence-preflight"]

        assert collect["needs"] == [
            "cadence-preflight",
            "dns-reconcile-preflight",
            "publication-watchdog-preflight",
        ]
        assert collect["timeout-minutes"] == 60
        assert "always()" in collect["if"]
        assert "!cancelled()" in collect["if"]
        assert "needs.dns-reconcile-preflight.result != 'success'" in collect["if"]
        assert "needs.dns-reconcile-preflight.outputs.required != 'false'" in (
            collect["if"]
        )
        assert "needs.publication-watchdog-preflight.result != 'success'" in collect["if"]
        assert "needs.publication-watchdog-preflight.outputs.required != 'false'" in (
            collect["if"]
        )
        assert "needs.cadence-preflight.result != 'success'" in collect["if"]
        assert "needs.cadence-preflight.outputs.required != 'false'" in collect["if"]
        assert "github.event_name != 'schedule'" in collect["if"]
        assert preflight["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.dns_generation != ''"
        )
        assert preflight["permissions"] == {"contents": "read"}
        assert preflight["outputs"] == {
            "required": "${{ steps.target-check.outputs.required }}",
            "reason": "${{ steps.target-check.outputs.reason }}",
        }
        target = next(
            step
            for step in preflight["steps"]
            if step.get("name") == "Check whether the DNS generation is already canonical"
        )
        assert target["env"] == {
            "TARGET_DNS_GENERATION": "${{ inputs.dns_generation }}"
        }
        for token in (
            "origin/gh-pages:data/vllm/ci/publication_status.json",
            "origin/gh-pages:data/vllm/ci/dns_failures.json",
            "audit_dashboard_data.py",
            '--dns-only --dns-path "$CANONICAL_DNS"',
            "--canonical-dns-data",
            "--target-dns-generation",
            '--github-output "$GITHUB_OUTPUT"',
        ):
            assert token in target["run"]

        assert watchdog_preflight["if"] == (
            "github.event_name == 'workflow_dispatch' && "
            "inputs.watchdog_generation != ''"
        )
        assert watchdog_preflight["permissions"] == {"contents": "read"}
        generation_check = next(
            step
            for step in watchdog_preflight["steps"]
            if step.get("id") == "generation-check"
        )
        assert generation_check["env"] == {
            "EXPECTED_GENERATION": "${{ inputs.watchdog_generation }}"
        }
        for token in (
            "origin/gh-pages:data/vllm/ci/publication_status.json",
            "plan_publication_watchdog.py",
            "--expected-generation",
            "--max-age-minutes 45",
            '--github-output "$GITHUB_OUTPUT"',
        ):
            assert token in generation_check["run"]

        assert cadence_preflight["if"] == "github.event_name == 'schedule'"
        assert cadence_preflight["permissions"] == {"contents": "read"}
        cadence_check = next(
            step
            for step in cadence_preflight["steps"]
            if step.get("id") == "cadence-check"
        )
        for token in (
            "origin/gh-pages:data/vllm/ci/publication_status.json",
            "plan_publication_watchdog.py",
            "--cadence-preflight",
            "--max-age-minutes 30",
            '--github-output "$GITHUB_OUTPUT"',
        ):
            assert token in cadence_check["run"]

        perf = next(
            step
            for step in collect["steps"]
            if step.get("name") == "Decide whether to regenerate perf-eval"
        )
        assert perf["env"] == {
            "DNS_RECONCILE_GENERATION": "${{ inputs.dns_generation }}",
            "WATCHDOG_GENERATION": "${{ inputs.watchdog_generation }}",
            "DISPATCH_TYPE": "${{ github.event.action }}",
        }
        assert 'if [ -n "$DNS_RECONCILE_GENERATION" ]' in perf["run"]
        assert 'elif [ -n "$WATCHDOG_GENERATION" ]' in perf["run"]
        assert 'if [ "$DISPATCH_TYPE" = "perf_eval_build_finished" ]' in perf["run"]

        confirmation = next(
            step
            for step in collect["steps"]
            if step.get("name") == "Confirm targeted DNS reconciliation"
        )
        assert confirmation["id"] == "dns-target-confirmation"
        assert "inputs.dns_generation != ''" in confirmation["if"]
        assert "steps.pages-deploy.outcome == 'success'" in confirmation["if"]
        assert "steps.post-deploy-validation.outcome == 'success'" in (
            confirmation["if"]
        )
        assert confirmation["env"] == {
            "TARGET_DNS_GENERATION": "${{ inputs.dns_generation }}"
        }
        for token in (
            "origin/gh-pages:data/vllm/ci/publication_status.json",
            "origin/gh-pages:data/vllm/ci/dns_failures.json",
            "audit_dashboard_data.py",
            '--dns-only --dns-path "$CANONICAL_DNS"',
            "--canonical-dns-data",
            "--target-dns-generation",
            "--fail-if-required",
        ):
            assert token in confirmation["run"]

    def test_calls_collect_analytics_script(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_analytics.py" in text

    def test_private_perf_seed_is_validated_without_public_branch_dependency(self):
        data = _load_workflow("hourly-master.yml")
        steps = data["jobs"]["collect-and-deploy"].get("steps", [])
        sync = next(
            step
            for step in steps
            if step.get("name") == "Validate private perf-eval event store"
        )
        script = sync["run"]

        merge_events = "python scripts/vllm/merge_perf_eval_events.py"
        rebuild = "python scripts/vllm/collect_perf_eval.py"
        for token in (
            merge_events,
            "--local data/vllm/perf_eval/events.jsonl",
            rebuild,
            "--output data/vllm/perf_eval/perf_eval.json",
            "run_surface_collector perf_eval",
        ):
            assert token in script

        assert "LIVE_LINES" not in script
        assert "LOCAL_LINES" not in script
        assert "wc -l" not in script
        assert "--remote" not in script
        assert "gh-pages" not in script
        assert "git fetch" not in script
        assert "git show" not in script
        assert "|| true" not in script
        assert script.count(merge_events) == 1
        assert script.index(merge_events) < script.index(rebuild)
        assert sync.get("env") in (None, {})
        for network_token in ("curl ", "gh api", "api.buildkite.com", "api.github.com"):
            assert network_token not in script

        manifest = json.loads(
            (REPO_ROOT / "config/public_data_manifest.json").read_text()
        )
        assert "vllm/perf_eval/events.jsonl" in manifest["never_publish_patterns"]

    def test_post_rebase_rebuilds_private_perf_projection_before_retest(self):
        data = _load_workflow("hourly-master.yml")
        steps = data["jobs"]["collect-and-deploy"].get("steps", [])
        commit = next(step for step in steps if step.get("name") == "Commit and push")
        script = commit["run"]

        pull = "git pull --rebase origin main"
        validate = "python scripts/vllm/merge_perf_eval_events.py"
        rebuild = "python scripts/vllm/collect_perf_eval.py"
        operations = "python scripts/vllm/build_operations_snapshot.py"
        assert script.index(pull) < script.index(validate)
        assert script.index(validate) < script.index(rebuild)
        assert script.index(rebuild) < script.index(operations)
        assert "--local data/vllm/perf_eval/events.jsonl" in script
        assert "--output data/vllm/perf_eval/perf_eval.json" in script

    def test_hourly_consumes_queue_snapshot_without_refetching_buildkite(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        assert "Sync queue data from durable live branch" in names
        assert "Normalize and prune queue history" in names
        assert "Collect queue snapshot" not in names

    def test_hourly_queue_sync_installs_valid_non_regressing_jobs_snapshot(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        script = next(
            step["run"]
            for step in steps
            if step.get("name") == "Sync queue data from durable live branch"
        )

        remote_read = "git show origin/queue-data:data/vllm/ci/queue_jobs.json"
        validation = "remote_timestamp < local_timestamp"
        install = 'install -m 0644 "$LIVE_QUEUE_JOBS"'
        assert remote_read in script
        assert 'payload.get("ts")' in script
        assert 'for key in ("pending", "running")' in script
        assert "datetime.fromisoformat" in script
        assert validation in script
        assert "would regress the embedded timestamp" in script
        assert install in script
        assert "data/vllm/ci/queue_jobs.json || return $?" in script
        assert (
            script.index(remote_read)
            < script.index(validation)
            < script.index(install)
        )

    def test_hourly_queue_sync_fails_into_surface_fallback_without_durable_refs(
        self,
    ):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        sync = next(
            step
            for step in steps
            if step.get("name") == "Sync queue data from durable live branch"
        )
        script = sync["run"]

        queue_fetch = (
            "if ! git fetch origin \\\n"
            "    +refs/heads/queue-data:refs/remotes/origin/queue-data"
        )
        pages_fetch = (
            "if git fetch origin \\\n"
            "    +refs/heads/gh-pages:refs/remotes/origin/gh-pages"
        )
        assert queue_fetch in script
        assert pages_fetch in script
        assert "Could not fetch the mandatory durable queue-data branch" in script
        assert "The mandatory durable queue-data ref did not resolve" in script
        assert "--merge-history-git-ref origin/queue-data" in script
        assert "--require-merge-history" in script
        assert script.index(queue_fetch) < script.index("return 1", script.index(queue_fetch))
        assert script.index("return 1", script.index(queue_fetch)) < script.index(
            pages_fetch
        )
        assert "--depth=1 || true" not in script
        assert (
            'run_surface_collector queue "durable queue data seed" sync_queue_data'
            in script
        )
        assert sync.get("if") == "inputs.dns_generation == ''"

    def test_workload_mapping_is_seeded_and_collected_before_operations_builds(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]

        sync_index = names.index("Sync CI data from gh-pages")
        collect_index = names.index("Collect vLLM/Omni AMD workload mappings")
        heuristic_index = names.index("Refresh Omni surge heuristic")
        selector = names.index("Select validated publication surfaces")
        second_build = names.index(
            "Rebuild v2 operations snapshot with selected issue state"
        )
        assert sync_index < collect_index < heuristic_index < selector < second_build

        sync_run = steps[sync_index].get("run", "")
        assert "workload_mapping.json" in sync_run
        assert "REMOTE_SCHEMA" in sync_run
        assert "LOCAL_SCHEMA" in sync_run
        assert '"$REMOTE_SCHEMA" -gt "$LOCAL_SCHEMA"' in sync_run
        assert '"$REMOTE_GENERATED" > "$LOCAL_GENERATED"' in sync_run
        collect = steps[collect_index]
        assert 'run_surface_collector queue "AMD workload mappings"' in collect["run"]
        assert "python scripts/vllm/collect_workload_mapping.py" in collect["run"]

        heuristic = steps[heuristic_index]
        assert 'run_surface_collector queue "Omni surge heuristic"' in heuristic["run"]
        assert (
            "python scripts/vllm/omni_surge_watcher.py --heuristic-only"
            in heuristic["run"]
        )
        assert "--output data/vllm/ci/workload_mapping.json" in collect["run"]
        assert collect.get("env", {}).get("BUILDKITE_TOKEN") == (
            "${{ secrets.BUILDKITE_TOKEN }}"
        )
        for step in steps[collect_index + 1 :]:
            assert (
                "git show origin/gh-pages:data/vllm/ci/workload_mapping.json"
                not in (step.get("run", "") or "")
            )

    def test_calls_collect_group_changes(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_group_changes.py" in text

    def test_calls_collect_amd_test_matrix(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_amd_test_matrix.py" in text

    def test_calls_collect_gating_proposals(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_gating_proposals.py" in text
        assert "gating_proposals.json" in text

    def test_calls_collect_gating_targets(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_gating_targets.py" in text
        assert "gating_targets.json" in text

    def test_hourly_rebuilds_gating_targets_after_live_data_sync(self):
        data = _load_workflow("hourly-master.yml")
        job = next(iter(data["jobs"].values()))
        steps = job.get("steps", []) or []
        names = [step.get("name") for step in steps]

        sync = names.index("Sync CI data from gh-pages")
        collect = names.index("Collect AMD gating target list")
        candidates = names.index("Collect AMD gating target candidate audit")

        assert sync < collect < candidates
        assert 'run_surface_collector ci_gating "AMD gating target list"' in (
            steps[collect]["run"]
        )
        assert "python scripts/vllm/collect_gating_targets.py" in steps[collect][
            "run"
        ]

    def test_calls_collect_gating_target_candidates(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_gating_target_candidates.py" in text
        assert "gating_target_candidates.json" in text

    def test_amd_matrix_uses_the_frozen_collect_ci_roster(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        collect_ci_index = names.index("Collect CI data")
        matrix_index = names.index("Collect AMD test matrix")
        matrix = steps[matrix_index]

        assert collect_ci_index < matrix_index
        assert (
            "--build-snapshot data/vllm/ci/.cache/amd_nightly_snapshot.json"
            in matrix["run"]
        )

    def test_ci_collect_calls_collect_gating_proposals(self):
        text = _load_workflow_text("ci-collect.yml")
        assert "collect_gating_proposals.py" in text
        assert "GITHUB_TOKEN" in text

    def test_ci_collect_calls_collect_gating_targets(self):
        text = _load_workflow_text("ci-collect.yml")
        assert "collect_gating_targets.py" in text

    def test_ci_collect_calls_collect_gating_target_candidates(self):
        text = _load_workflow_text("ci-collect.yml")
        assert "collect_gating_target_candidates.py" in text

    def test_calls_github_data_collection(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect.py" in text, "hourly-master.yml must call collect.py"

    def test_current_config_collectors_share_one_immutable_vllm_sha(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        resolve = steps[names.index("Resolve immutable vLLM config snapshot")]
        capacity = steps[names.index("Collect queue capacity monitor")]
        collect_ci = steps[names.index("Collect CI data")]

        assert names.index("Resolve immutable vLLM config snapshot") < names.index(
            "Collect queue capacity monitor"
        ) < names.index("Collect CI data")
        assert "VLLM_CONFIG_SHA" in resolve["run"]
        assert "[0-9a-f]{40}" in resolve["run"]
        assert 'env_file.write(f"VLLM_CONFIG_SHA={sha}\\n")' in resolve["run"]
        assert '--ref "$VLLM_CONFIG_SHA"' in capacity["run"]
        # GITHUB_ENV values are inherited by every later collection step,
        # including config_parity inside collect_ci.
        assert "VLLM_CONFIG_SHA" not in (collect_ci.get("env") or {})

    def test_publication_baseline_is_captured_before_collection(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        install = names.index("Install dependencies")
        baseline = names.index("Capture immutable main baseline")
        resolve = names.index("Resolve immutable vLLM config snapshot")

        assert install < baseline < resolve
        assert steps[baseline].get("id") == "publication-baseline"
        script = steps[baseline]["run"]
        assert "git rev-parse --verify 'HEAD^{commit}'" in script
        assert "^[0-9a-f]{40}$" in script
        assert "git diff --quiet" in script
        assert "python scripts/vllm/audit_dashboard_data.py" not in script
        assert "PUBLICATION_BASELINE_REF=$BASELINE_REF" in script
        assert "PUBLICATION_FAILED_SURFACES_FILE=$FAILED_SURFACES_FILE" in script
        assert (
            "PUBLICATION_COLLECTOR_FAILURES_FILE=$COLLECTOR_FAILURES_FILE"
            in script
        )
        assert 'FAILED_SURFACES_FILE="$RUNNER_TEMP/' in script
        assert 'COLLECTOR_FAILURES_FILE="$RUNNER_TEMP/' in script

    def test_external_collectors_force_atomic_surface_selection(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        baseline = steps[names.index("Capture immutable main baseline")]
        selector = steps[names.index("Select validated publication surfaces")]

        helper = baseline["run"]
        assert "run_surface_collector()" in helper
        assert '"$surface" "$label" "$status" "$diagnostic_file" "$collector"' in helper
        assert 'sort -u -o "$PUBLICATION_FAILED_SURFACES_FILE"' in helper
        assert '"reason_class": reason_class' in helper
        assert '"collector": re.sub' in helper
        assert '"step": " ".join(step.split())' in helper
        assert '"payload-budget"' in helper
        assert '"rate-limit"' in helper
        assert '"timeout"' in helper
        assert '"schema-drift"' in helper
        assert "local status=${PIPESTATUS[0]}" in helper
        assert "mirror_surface_failure()" in helper
        assert 'mirrored["surface"] = target_surface' in helper
        assert 'candidate.get("reason_class")' in helper
        assert '"component_bytes"' in helper
        for name, surface in (
            ("Sync queue data from durable live branch", "queue"),
            ("Sync issue automation state from gh-pages", "queue"),
            ("Normalize and prune queue history", "queue"),
            ("Refresh Omni surge heuristic", "queue"),
            ("Collect CI data", "ci_core"),
            ("Collect AMD agent health (all builds, all branches)", "agent_health"),
            ("Validate private perf-eval event store", "perf_eval"),
            ("Ingest perf-eval artifacts from Buildkite", "perf_eval"),
            ("Collect GitHub data", "github_home"),
        ):
            assert f"run_surface_collector {surface}" in steps[names.index(name)]["run"]

        selector_run = selector["run"]
        assert selector.get("id") == "publication-selector"
        assert "--baseline-ref \"$PUBLICATION_BASELINE_REF\"" in selector_run
        assert "--force-degraded-surfaces \"$FORCED_SURFACES\"" in selector_run
        assert (
            '--collector-failures-file "$PUBLICATION_COLLECTOR_FAILURES_FILE"'
            in selector_run
        )
        assert 'sort -u "$PUBLICATION_FAILED_SURFACES_FILE"' in selector_run
        assert selector.get("continue-on-error") is not True
        sync_ci = steps[names.index("Sync CI data from gh-pages")]["run"]
        for surface in ("ci_core", "ci_gating", "ci_changes", "ci_hotness"):
            assert f"run_surface_collector {surface}" in sync_ci
        assert "run_surface_collector ci_gating" in steps[
            names.index("Collect AMD gating target list")
        ]["run"]
        assert "Build v2 operations snapshot" not in names
        assert "run_surface_collector queue" in steps[
            names.index("Normalize and prune queue history")
        ]["run"]

    def test_ci_collectors_and_seeds_use_split_publication_surfaces(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        baseline = steps[names.index("Capture immutable main baseline")]["run"]
        assert (
            "ci_core|ci_analytics|ci_gating|ci_changes|ci_hotness|queue|queue_lifecycle|"
            "agent_health|dns_health|github_home|perf_eval"
        ) in baseline

        expected_collectors = {
            "Collect CI data": "ci_core",
            "Collect CI analytics": "ci_analytics",
            "Collect AMD test matrix": "ci_core",
            "Collect build-pinned CI ownership parity": "ci_core",
            "Collect AMD gating target list": "ci_gating",
            "Collect AMD gating proposals": "ci_gating",
            "Collect AMD gating target candidate audit": "ci_gating",
            "Collect test group changes": "ci_changes",
            "Collect AMD hotness (3d window)": "ci_hotness",
        }
        for name, surface in expected_collectors.items():
            assert f"run_surface_collector {surface}" in steps[names.index(name)][
                "run"
            ]

        sync = steps[names.index("Sync CI data from gh-pages")]["run"]
        seed_sections = {
            "ci_core": sync[
                sync.index("sync_ci_core_seed()") : sync.index(
                    "sync_ci_gating_seed()"
                )
            ],
            "ci_gating": sync[
                sync.index("sync_ci_gating_seed()") : sync.index(
                    "sync_ci_changes_seed()"
                )
            ],
            "ci_changes": sync[
                sync.index("sync_ci_changes_seed()") : sync.index(
                    "sync_ci_hotness_seed()"
                )
            ],
            "ci_hotness": sync[
                sync.index("sync_ci_hotness_seed()") : sync.index(
                    "sync_workload_mapping_seed()"
                )
            ],
        }
        for filename in (
            "ci_health.json",
            "amd_test_matrix.json",
            "ownership_config_parity.json",
        ):
            assert filename in seed_sections["ci_core"]
        assert "gating_nightlies.json" not in seed_sections["ci_core"]
        assert "analytics.json" not in seed_sections["ci_core"]
        assert "PUBLIC-ANALYTICS-BOUNDARY" in sync
        for filename in (
            "gating_targets.json",
            "gating_proposals.json",
            "gating_target_candidates.json",
            "gating_nightlies.json",
        ):
            assert filename in seed_sections["ci_gating"]
        assert "group_changes.json" in seed_sections["ci_changes"]
        assert "hotness.json" in seed_sections["ci_hotness"]
        assert "operations_v2.json" not in sync

        workflow_text = _load_workflow_text("hourly-master.yml")
        assert not re.search(
            r"(?:run_surface_collector|record_surface_failure)\s+ci(?:\s|\")",
            workflow_text,
        )
        assert "',ci,'" not in workflow_text

    def test_private_analytics_projection_has_no_public_feedback_loop(self):
        private_path = "data/vllm/ci/analytics.json"
        manifest_path = "vllm/ci/analytics.json"
        assert surface_for_path(private_path) == "ci_analytics"

        manifest = json.loads(
            (REPO_ROOT / "config/public_data_manifest.json").read_text()
        )
        assert manifest_path in manifest["build_inputs"]
        assert manifest_path not in {
            *manifest["required_files"],
            *manifest["optional_files"],
        }
        descriptor = next(
            item
            for item in manifest["projected_files"]
            if item["path"] == manifest_path
        )
        assert descriptor == {
            "path": manifest_path,
            "projector": "public_analytics_v1",
            "max_bytes": 8 * 1024 * 1024,
        }

        build_site = (REPO_ROOT / "scripts/build_site.py").read_text()
        for token in (
            "materialize_projected_files",
            "compact_public_analytics_json",
            "PUBLIC_ANALYTICS_PROJECTOR_ID",
        ):
            assert token in build_site
        assert re.search(
            r"PUBLIC_ANALYTICS_PROJECTOR_ID\s*:\s*compact_public_analytics_json",
            build_site,
        )

        workflow = _load_workflow("hourly-master.yml")
        steps = next(iter(workflow["jobs"].values())).get("steps", [])
        sync = next(
            step["run"]
            for step in steps
            if step.get("name") == "Sync CI data from gh-pages"
        )
        commands = "\n".join(
            line for line in sync.splitlines() if not line.lstrip().startswith("#")
        )
        assert "PUBLIC-ANALYTICS-BOUNDARY" in sync
        assert "analytics.json" not in commands

    def test_private_ci_roster_cache_is_rolling_sharded_and_one_way(self):
        cache_path = "data/vllm/ci/.cache/nightly-rosters-v2"
        workflow = _load_workflow("hourly-master.yml")
        steps = next(iter(workflow["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        key_index = names.index("Prepare private CI roster cache key")
        restore_index = names.index("Restore private CI roster cache")
        collect_index = names.index("Collect CI data")
        save_index = names.index("Save private CI roster cache")
        assert key_index < restore_index < collect_index < save_index

        key_script = steps[key_index]["run"]
        assert 'CACHE_NAMESPACE="nightly-rosters-v2-${{ runner.os }}"' in key_script
        assert "github.run_id" in key_script
        assert "github.run_attempt" in key_script
        restore = steps[restore_index]
        assert restore["uses"] == "actions/cache/restore@v4"
        assert restore["with"]["path"] == cache_path
        assert "current_day_prefix" in restore["with"]["restore-keys"]
        assert "prior_day_prefix" in restore["with"]["restore-keys"]
        collect = steps[collect_index]
        assert collect["id"] == "collect-ci"
        assert '--github-output "$GITHUB_OUTPUT"' in collect["run"]
        assert 'echo "cache_save=true"' in collect["run"]
        save = steps[save_index]
        assert save["uses"] == "actions/cache/save@v4"
        assert save["if"] == (
            "inputs.dns_generation == '' && "
            "steps.collect-ci.outputs.cache_save == 'true' && "
            "steps.collect-ci.outputs.roster_cache_save == 'true'"
        )
        assert save["with"] == {
            "path": cache_path,
            "key": "${{ steps.ci-roster-cache-key.outputs.key }}",
        }
        assert "data/vllm/ci/.cache/" in {
            line.strip()
            for line in (REPO_ROOT / ".gitignore").read_text().splitlines()
        }

    def test_private_dns_classification_cache_is_shared_and_one_way(self):
        cache_path = "data/vllm/ci/.cache/dns-classifications-v1"
        master = _load_workflow("hourly-master.yml")
        master_steps = next(iter(master["jobs"].values())).get("steps", [])
        master_names = [step.get("name") for step in master_steps]
        key_index = master_names.index(
            "Prepare private DNS classification cache key"
        )
        restore_index = master_names.index(
            "Restore private DNS classification cache"
        )
        collect_index = master_names.index("Collect CI data")
        save_index = master_names.index("Save private DNS classification cache")
        assert key_index < restore_index < collect_index < save_index

        key_script = master_steps[key_index]["run"]
        assert 'CACHE_NAMESPACE="dns-classifications-v1-${{ runner.os }}"' in key_script
        assert "github.run_id" in key_script
        assert "github.run_attempt" in key_script
        master_restore = master_steps[restore_index]
        assert master_restore["uses"] == "actions/cache/restore@v4"
        assert master_restore["continue-on-error"] is True
        assert master_restore["with"] == {
            "path": cache_path,
            "key": "${{ steps.dns-classification-cache-key.outputs.key }}",
            "restore-keys": (
                "${{ steps.dns-classification-cache-key.outputs.current_day_prefix }}\n"
                "${{ steps.dns-classification-cache-key.outputs.prior_day_prefix }}\n"
            ),
        }
        master_save = master_steps[save_index]
        assert master_save["uses"] == "actions/cache/save@v4"
        assert master_save["if"] == (
            "inputs.dns_generation == '' && "
            "steps.collect-ci.outputs.cache_save == 'true' && "
            "steps.collect-ci.outputs.dns_cache_save == 'true'"
        )
        assert master_save["with"] == {
            "path": cache_path,
            "key": "${{ steps.dns-classification-cache-key.outputs.key }}",
        }

        dns = _load_workflow("dns-health.yml")
        dns_job = dns["jobs"]["collect"]
        assert dns_job["permissions"] == {"actions": "read", "contents": "write"}
        dns_steps = dns_job["steps"]
        dns_names = [step.get("name") for step in dns_steps]
        dns_key_index = dns_names.index(
            "Prepare private DNS classification cache key"
        )
        dns_restore_index = dns_names.index(
            "Restore private DNS classification cache"
        )
        dns_collect_index = dns_names.index("Collect DNS failure observations")
        assert dns_key_index < dns_restore_index < dns_collect_index
        dns_restore = dns_steps[dns_restore_index]
        assert dns_restore["uses"] == "actions/cache/restore@v4"
        assert dns_restore["with"]["path"] == cache_path
        assert "current_day_prefix" in dns_restore["with"]["restore-keys"]
        assert "prior_day_prefix" in dns_restore["with"]["restore-keys"]
        dns_collect = dns_steps[dns_collect_index]["run"]
        assert f"--classification-cache {cache_path}" in dns_collect
        assert not any(
            step.get("uses") == "actions/cache/save@v4" for step in dns_steps
        )

        assert "data/vllm/ci/.cache/" in {
            line.strip()
            for line in (REPO_ROOT / ".gitignore").read_text().splitlines()
        }

    def test_private_analytics_cache_is_rolling_bounded_and_one_way(self):
        cache_path = "data/vllm/ci/.cache/analytics-builds-v1"
        manifest_cache_path = "vllm/ci/.cache/analytics-builds-v1"
        cache_sample = f"{manifest_cache_path}/amd-ci.json"
        workflow = _load_workflow("hourly-master.yml")
        steps = next(iter(workflow["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        key_index = names.index("Prepare private analytics cache key")
        restore_index = names.index("Restore private analytics build cache")
        collect_index = names.index("Collect CI analytics")
        save_index = names.index("Save private analytics build cache")
        assert key_index < restore_index < collect_index < save_index

        key_step = steps[key_index]
        assert key_step["id"] == "analytics-cache-key"
        key_script = key_step["run"]
        assert "CACHE_DAY=$(date -u +%Y-%m-%d)" in key_script
        assert "PRIOR_CACHE_DAY=$(date -u -d '1 day ago' +%Y-%m-%d)" in key_script
        assert 'CACHE_NAMESPACE="analytics-builds-v1-${{ runner.os }}"' in key_script
        assert (
            'echo "key=$CACHE_NAMESPACE-$CACHE_DAY-${{ github.run_id }}-'
            '${{ github.run_attempt }}"' in key_script
        )
        assert 'echo "current_day_prefix=$CACHE_NAMESPACE-$CACHE_DAY-"' in key_script
        assert (
            'echo "prior_day_prefix=$CACHE_NAMESPACE-$PRIOR_CACHE_DAY-"'
            in key_script
        )
        assert "github.run_id" in key_script
        assert "github.run_attempt" in key_script

        restore = steps[restore_index]
        assert restore["uses"] == "actions/cache/restore@v4"
        assert restore["continue-on-error"] is True
        assert restore["with"] == {
            "path": cache_path,
            "key": "${{ steps.analytics-cache-key.outputs.key }}",
            "restore-keys": (
                "${{ steps.analytics-cache-key.outputs.current_day_prefix }}\n"
                "${{ steps.analytics-cache-key.outputs.prior_day_prefix }}\n"
            ),
        }

        collect = steps[collect_index]
        assert collect["id"] == "collect-analytics"
        assert "surface_is_current ci_analytics" in collect["run"]
        assert "GATING_NIGHTLIES_BEFORE=$(gating_nightlies_digest)" in collect["run"]
        assert "GATING_NIGHTLIES_AFTER=$(gating_nightlies_digest)" in collect["run"]
        assert '"$GATING_NIGHTLIES_BEFORE" = "$GATING_NIGHTLIES_AFTER"' in collect[
            "run"
        ]
        assert (
            'mirror_surface_failure \\\n    ci_analytics ci_gating "CI gating nightly evidence"'
            in collect["run"]
        )
        assert '--github-output "$GITHUB_OUTPUT"' in collect["run"]
        assert 'echo "cache_save=true"' in collect["run"]
        assert 'echo "cache_save=false"' in collect["run"]

        save = steps[save_index]
        assert save["uses"] == "actions/cache/save@v4"
        assert save["continue-on-error"] is True
        assert "steps.collect-analytics.outputs.cache_save == 'true'" in save["if"]
        assert (
            "steps.collect-analytics.outputs.analytics_cache_save == 'true'"
            in save["if"]
        )
        assert "analytics-cache-restore.outputs.cache-hit" not in save["if"]
        assert save["with"] == {
            "path": cache_path,
            "key": "${{ steps.analytics-cache-key.outputs.key }}",
        }

        for step in steps:
            run = step.get("run") or ""
            if "origin/gh-pages" not in run:
                continue
            seed_commands = "\n".join(
                line
                for line in run.splitlines()
                if not line.lstrip().startswith("#")
            )
            assert cache_path not in seed_commands
            assert "analytics-builds-v1" not in seed_commands
        commit = steps[names.index("Commit and push")]["run"]
        assert cache_path not in commit
        assert not re.search(r"\bgit\s+add\b[^\n]*(?:\s-f\b|\s--force\b)", commit)
        assert "data/vllm/ci/.cache/" in {
            line.strip()
            for line in (REPO_ROOT / ".gitignore").read_text().splitlines()
        }

        manifest = json.loads(
            (REPO_ROOT / "config/public_data_manifest.json").read_text()
        )
        exact_paths = {
            relative
            for field in (
                "required_files",
                "optional_files",
                "build_inputs",
                "generated_files",
            )
            for relative in manifest[field]
        }
        exact_paths.update(item["path"] for item in manifest["projected_files"])
        assert not any(
            path == manifest_cache_path
            or path.startswith(f"{manifest_cache_path}/")
            for path in exact_paths
        )
        assert not any(
            PurePosixPath(cache_sample).match(pattern)
            for pattern in manifest["optional_globs"]
        )
        assert any(
            PurePosixPath(cache_sample).match(pattern)
            for pattern in manifest["never_publish_patterns"]
        )

    def test_private_actions_caches_are_explicitly_pruned(self):
        workflow = _load_workflow("hourly-master.yml")
        job = next(iter(workflow["jobs"].values()))
        steps = job.get("steps", [])
        prune = next(
            step
            for step in steps
            if step.get("name") == "Prune superseded private Actions caches"
        )

        assert workflow["permissions"]["actions"] == "write"
        assert prune["continue-on-error"] is True
        assert prune["env"] == {"GH_TOKEN": "${{ github.token }}"}
        script = prune["run"]
        assert "nightly-rosters-v2-${{ runner.os }}-" in script
        assert '--key "nightly-rosters-v1-${{ runner.os }}-"' in script
        assert "--jq '.[].id'" in script
        assert "analytics-builds-v1-${{ runner.os }}-" in script
        assert "dns-classifications-v1-${{ runner.os }}-" in script
        assert "--ref \"refs/heads/$GITHUB_REF_NAME\"" in script
        assert "--sort created_at" in script
        assert "--order desc" in script
        assert "--jq '.[8:] | .[].id'" in script
        assert 'gh cache delete "$CACHE_ID"' in script

    def test_hourly_refuses_to_commit_or_push_tracked_private_caches(self):
        workflow = _load_workflow("hourly-master.yml")
        steps = next(iter(workflow["jobs"].values())).get("steps", [])
        script = next(
            step["run"] for step in steps if step.get("name") == "Commit and push"
        )

        guard = "git ls-files -- ':(glob)**/.cache/**'"
        assert guard in script
        assert script.count("assert_no_tracked_private_cache") == 3
        assert script.index("assert_no_tracked_private_cache\n") < script.index(
            "git add data/ dashboards/ README.md"
        )
        assert script.rindex("assert_no_tracked_private_cache") < script.index(
            "git push origin HEAD:main"
        )

    def test_selection_precedes_side_effects_render_and_tests(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        selector = names.index("Select validated publication surfaces")
        live_audit = names.index("Live publication audit")
        watcher_surfaces = (
            ("Watch queue latency (open/close issues)", "queue"),
            ("Watch zombie queue jobs (open/close issues)", "queue"),
            ("Watch Omni workload surge (open/close issues)", "queue"),
            (
                "Watch AMD main test-group failures (open/close issue)",
                "ci_analytics",
            ),
            (
                "Watch upstream CI main test-group failures (open/close issue)",
                "ci_analytics",
            ),
            ("Watch AMD main duration regressions (open/close issue)", "ci_analytics"),
            ("Watch AMD CI agent health (open/close issue)", "agent_health"),
            ("Watch AMD CI test-area regressions (ranked owners)", "ci_core"),
        )
        for name, surface in watcher_surfaces:
            watcher = steps[names.index(name)]
            assert names.index(name) > selector
            assert names.index(name) < live_audit
            condition = watcher.get("if", "")
            assert "publication-selector.outcome == 'success'" in condition
            assert "degraded_surfaces" in condition
            assert f",{surface}," in condition
        assert steps[names.index("Watch Omni workload surge (open/close issues)")][
            "run"
        ] == "python scripts/vllm/omni_surge_watcher.py --issues-only"
        assert selector < names.index("Render dashboards after publication selection")
        assert names.index(
            "Rebuild v2 operations snapshot with selected issue state"
        ) < live_audit
        assert names.index("Render dashboards after publication selection") < names.index(
            "Live publication audit"
        )
        assert names.index("Live publication audit") < names.index(
            "Run test suite"
        )

    def test_targeted_dns_reconciliation_has_no_buildkite_or_issue_side_effects(
        self,
    ):
        data = _load_workflow("hourly-master.yml")
        steps = data["jobs"]["collect-and-deploy"].get("steps", [])
        names = [step.get("name") or step.get("uses") for step in steps]
        dns_absent = "inputs.dns_generation == ''"

        expected_buildkite_steps = {
            "Collect AMD hotness (3d window)",
            "Collect vLLM/Omni AMD workload mappings",
            "Collect CI data",
            "Collect CI analytics",
            "Collect AMD agent health (all builds, all branches)",
            "Collect AMD test matrix",
            "Ingest perf-eval artifacts from Buildkite",
        }
        buildkite_steps = {
            step.get("name")
            for step in steps
            if any(
                "secrets.BUILDKITE_TOKEN" in str(value)
                for value in (step.get("env") or {}).values()
            )
        }
        assert buildkite_steps == expected_buildkite_steps
        for step in steps:
            if step.get("name") in buildkite_steps:
                assert dns_absent in step.get("if", "")

        issue_side_effect_steps = {
            "Ensure CI Operations issue labels",
            "Watch queue latency (open/close issues)",
            "Watch zombie queue jobs (open/close issues)",
            "Watch Omni workload surge (open/close issues)",
            "Watch AMD main test-group failures (open/close issue)",
            "Watch upstream CI main test-group failures (open/close issue)",
            "Watch AMD main duration regressions (open/close issue)",
            "Watch AMD CI agent health (open/close issue)",
            "Stage managed alert issue state",
            "Watch AMD CI test-area regressions (ranked owners)",
            "Sync managed issues to AMD CI Operations project",
            "Stage CI ownership issue state",
            "Establish publication recovery validation",
            "Create hourly validation incident",
            "Close issue after healthy publication",
        }
        assert issue_side_effect_steps <= set(names)
        for step in steps:
            if step.get("name") in issue_side_effect_steps:
                assert dns_absent in step.get("if", "")

        selector_index = names.index("Select validated publication surfaces")
        target_only_prefix = {
            "actions/checkout@v4",
            "actions/setup-python@v5",
            "Install dependencies",
            "Capture immutable main baseline",
            "Sync validated DNS health aggregate",
            "Validate targeted DNS candidate generation",
        }
        for step in steps[:selector_index]:
            label = step.get("name") or step.get("uses")
            if label not in target_only_prefix:
                assert dns_absent in step.get("if", ""), label

        selector = steps[selector_index]
        assert "--refresh-only-surface dns_health" in selector.get("run", "")
        validate_index = names.index("Validate targeted DNS candidate generation")
        assert names.index("Sync validated DNS health aggregate") < validate_index
        assert validate_index < selector_index
        validate = steps[validate_index]
        assert "inputs.dns_generation != ''" in validate.get("if", "")
        assert "--dns-only --dns-path data/vllm/ci/dns_failures.json" in validate[
            "run"
        ]
        assert "candidate < target" in validate["run"]

        run_tests = steps[names.index("Run test suite")]
        health = steps[names.index("Health check")]
        assert dns_absent in run_tests.get("if", "")
        assert dns_absent in health.get("if", "")
        commit = steps[names.index("Commit and push")]["run"]
        assert "if [ -z \"${{ inputs.dns_generation }}\" ]; then" in commit
        assert commit.index('if [ -z "${{ inputs.dns_generation }}" ]; then') < (
            commit.index("last_collected_at.txt")
        )

    def test_targeted_dns_path_keeps_full_live_audit_fail_closed(self):
        data = _load_workflow("hourly-master.yml")
        steps = data["jobs"]["collect-and-deploy"].get("steps", [])
        by_name = {step.get("name"): step for step in steps}

        audit = by_name["Live publication audit"]
        enforce = by_name["Enforce publication validation results"]
        assert "inputs.dns_generation == ''" not in audit.get("if", "")
        assert "steps.live-data-audit.outcome != 'success'" in enforce["if"]
        assert "steps.live-data-audit.outputs.exit_code != '0'" in enforce["if"]
        assert "Live publication audit failed" in enforce["run"]
        assert '[ -z "${{ inputs.dns_generation }}" ]' in enforce["run"]

    def test_github_freshness_has_no_retired_ready_ticket_inputs(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "ready_tickets" not in text
        assert "test_builds" not in text

    def test_runs_pytest(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "pytest tests/ -m 'not live_data'" in text
        assert "pytest tests/ -m 'live_data'" in text

    def test_live_publication_audit_is_independent_and_captures_diagnostics(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        audit = steps[names.index("Live publication audit")]
        script = audit.get("run", "")

        assert audit.get("id") == "live-data-audit"
        assert audit.get("continue-on-error") is True
        assert "always()" in audit.get("if", "")
        assert "steps.publication-selector.outcome == 'success'" in audit.get("if", "")
        assert "skip_tests" not in audit.get("if", "")
        assert "python scripts/vllm/audit_dashboard_data.py --format json" in script
        assert "pytest tests/ -m 'live_data'" in script
        assert "live-publication-audit.json" in script
        for output in ("exit_code", "summary", "findings", "output"):
            assert f'"{output}"' in script or f"{output}=" in script

        artifact = steps[names.index("Upload live publication audit artifact")]
        assert artifact.get("uses") == "actions/upload-artifact@v4"
        assert "live-publication-audit.json" in artifact["with"]["path"]
        assert names.index("Live publication audit") < names.index(
            "Enforce publication validation results"
        )

    def test_failed_validation_blocks_publication(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        enforce = steps[names.index("Enforce publication validation results")]
        assert "steps.run-tests.outputs.exit_code != '0'" in enforce["if"]
        assert "steps.live-data-audit.outcome != 'success'" in enforce["if"]
        assert "steps.live-data-audit.outputs.exit_code != '0'" in enforce["if"]
        assert "Live publication audit failed" in enforce["run"]
        assert "Deterministic dashboard tests failed" in enforce["run"]
        assert names.index("Enforce publication validation results") < names.index(
            "Commit and push"
        )
        assert names.index("Enforce publication validation results") < names.index(
            "Assemble site"
        )
        assert names.index("Enforce publication validation results") < names.index(
            "Deploy to GitHub Pages"
        )

    def test_success_issue_closure_requires_eligible_validation_and_successful_workflow(
        self,
    ):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        close = next(
            step
            for step in steps
            if step.get("name") == "Close issue after healthy publication"
        )
        condition = close.get("if", "")
        assert "success()" in condition
        assert "steps.publication-recovery-validation.outcome == 'success'" in condition
        assert (
            "steps.publication-recovery-validation.outputs.eligible == 'true'"
            in condition
        )
        assert "steps.live-data-audit.outcome == 'success'" in condition
        assert "steps.live-data-audit.outputs.exit_code == '0'" in condition
        assert "steps.publication-selector.outcome == 'success'" in condition
        assert "steps.publication-selector.outputs.degraded == 'false'" in condition
        # Expedited publications can use a separately green code SHA, so test
        # execution is resolved by the validation step instead of this guard.
        assert "steps.run-tests" not in condition
        assert "always()" not in condition
        assert "RECOVERED_MARKER: recoveredMarker" in close["with"]["script"]
        assert "scripts/vllm/hourly_incident_recovery.js" in close["with"]["script"]
        assert "body: recoveredBody" in close["with"]["script"]
        assert "state: 'all', per_page: 100" in close["with"]["script"]
        assert "labels: 'ci-failure'" not in close["with"]["script"]
        assert "hasExactMarker" in close["with"]["script"]
        assert "if (currentIssue.state === 'closed')" in close["with"]["script"]

    def test_expedited_recovery_requires_green_code_ancestor_and_bot_data_gap(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        validation = steps[names.index("Establish publication recovery validation")]
        close = steps[names.index("Close issue after healthy publication")]

        assert validation.get("id") == "publication-recovery-validation"
        assert names.index("Establish publication recovery validation") < names.index(
            "Close issue after healthy publication"
        )
        condition = validation.get("if", "")
        assert "success()" in condition
        assert "steps.publication-selector.outputs.degraded == 'false'" in condition
        assert "steps.live-data-audit.outputs.exit_code == '0'" in condition
        assert "steps.pages-deploy.outcome == 'success'" in condition
        assert "steps.post-deploy-validation.outcome == 'success'" in condition
        env = validation.get("env", {})
        assert "steps.publication-commit.outputs.published_sha" in env[
            "HOURLY_PUBLICATION_SHA"
        ]
        assert "steps.publication-commit.outputs.local_test_gap_safe" in env[
            "HOURLY_LOCAL_TEST_GAP_SAFE"
        ]
        assert "steps.run-tests.outcome" in env["HOURLY_TEST_OUTCOME"]
        assert "github.event.inputs.skip_tests" in env["HOURLY_TESTS_SKIPPED"]

        script = validation["with"]["script"]
        assert "const localTestsPassed" in script
        assert "testOutcome === 'success' && testExitCode === '0'" in script
        assert "if (localTestsPassed && localTestGapSafe)" in script
        assert "setValidation(true, 'hourly-tests', publicationSha" in script
        assert "testsIntentionallySkipped" in script
        assert "const needsSeparateCi" in script
        assert "localTestsPassed && !localTestGapSafe" in script
        assert "github.rest.actions.listWorkflowRuns" in script
        assert "workflow_id: 'ci.yml'" in script
        assert "branch: 'main'" in script
        assert "event: 'push'" in script
        assert "run.conclusion === 'success'" in script
        assert "github.rest.repos.compareCommitsWithBasehead" in script
        assert "`${codeSha}...${publicationSha}`" in script
        assert "comparison.data.behind_by === 0" in script
        assert "comparisonIsComplete" in script
        assert "commits.every(isGeneratedPublicationCommit)" in script
        assert "/^auto: update data(?:\\s|$)/" in script
        assert "github-actions[bot]" in script
        assert "setValidation(true, 'separate-ci', codeSha" in script
        assert "setValidation(false, 'no-green-code-ancestor')" in script

        close_env = close.get("env", {})
        assert "publication-recovery-validation.outputs.source" in close_env[
            "HOURLY_RECOVERY_VALIDATION_SOURCE"
        ]
        assert "publication-recovery-validation.outputs.code_sha" in close_env[
            "HOURLY_RECOVERY_CODE_SHA"
        ]

    def test_failure_issues_keep_fingerprints_inside_one_current_incident(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        create = next(
            step
            for step in steps
            if step.get("name") == "Create hourly validation incident"
        )
        assembly = next(
            step for step in steps if step.get("name") == "Assemble site"
        )
        script = create["with"]["script"]
        condition = create.get("if", "")

        assert assembly.get("id") == "site-assembly"
        assert "steps.publication-selector.outcome == 'failure'" in condition
        assert "steps.publication-selector.outputs.degraded == 'true'" in condition
        assert (
            "steps.publication-selector.outputs.alertable_degradation == 'true'"
            in condition
        )
        assert "steps.pages-deploy.outcome == 'failure'" in condition
        assert "steps.site-assembly.outcome == 'failure'" in condition
        assert "steps.post-deploy-validation.outcome == 'failure'" in condition
        assert "failure()" in condition
        assert "steps.live-data-audit.outcome != 'success'" in condition
        assert "steps.live-data-audit.outputs.exit_code != '0'" in condition
        assert "createHash('sha256')" in script
        assert "Hourly validation failure [${fingerprint.slice(0, 8)}]" in script
        assert "<!-- ci-failure-owner:hourly-master -->" in script
        assert "<!-- hourly-ci-fingerprint:${fingerprint} -->" in script
        assert "publicationState.candidate_errors" in script
        assert "publicationState.candidate_degradations" in script
        assert "publicationState.final_errors" in script
        assert "publicationState.final_degradations" in script
        assert "Publication findings" in script
        assert "Live Publication Audit Failure" in script
        assert "workflow:unclassified-step-failure" in script
        assert "priorJobStatus === 'failure'" in script
        assert "liveAuditFindings" in script
        assert "Live publication audit findings" in script
        assert "Failing deterministic tests" in script
        assert "deterministic:${node}" in script
        assert "const publicationConditionSignals" in script
        assert "publicationConditionSignals.length" in script
        assert "context.reason_class" in script
        assert "context.collector" in script
        assert "context.step" in script
        assert "publication-finding:${signal}" in script
        assert "publication:${surface}" in script
        assert "live-audit:${code}" in script
        assert "live-contract:${node}" in script
        assert "hourly-ci-v4\\n${fingerprintSource}" in script
        assert "<!-- hourly-ci-fingerprint-version:4 -->" in script
        assert "<!-- hourly-ci-current-incident:v1 -->" in script
        assert "<!-- hourly-ci-superseded:v1 -->" in script
        assert "was manually closed" in script
        assert "leaving it suppressed until the signal recovers" in script
        assert ".replace(/\\b\\d+(?:\\.\\d+)?\\b/g, '<number>')" in script
        assert "github.paginate(github.rest.issues.listForRepo" in script
        assert "state: 'all', per_page: 100" in script
        assert "labels: 'ci-failure'" not in script
        assert "hasExactMarker" in script
        assert "github.rest.issues.addLabels" in script
        assert "allIssues.filter" in script
        assert "if (issue.pull_request) return false" in script
        assert "const currentIssues = activeOwnedIssues.filter" in script
        assert "const openOwnedIssues = activeOwnedIssues.filter" in script
        assert "const openCurrentFirst = (left, right) =>" in script
        assert "Number(right.state === 'open') - Number(left.state === 'open')" in script
        assert ").sort(openCurrentFirst)" in script
        assert "let existing = currentIssues[0] || openOwnedIssues[0]" in script
        assert "const supersedeOtherIssues = async canonicalNumber =>" in script
        assert "issue.state !== 'open' && !wasCurrent" in script
        assert "state: 'closed'" in script
        assert "Superseded by #${canonicalNumber}" in script
        assert "await supersedeOtherIssues(existing.number)" in script
        assert "await supersedeOtherIssues(created.data.number)" in script
        assert "const resetOnlyDegradation" in script
        assert "the current incident recovery streak was reset" in script
        assert "hasExactMarker(issueBody, ownershipMarker)" in script
        assert "*Auto-created by hourly-master workflow.*" in script
        assert "for (const issue of ownedIssues)" in script
        assert "suppressedDegradationRecoveryTransition," in script
        assert "const resetTransition = suppressedDegradationRecoveryTransition(" in script
        assert "if (resetTransition.body !== (existing.body || ''))" in script
        assert "existing.body = resetTransition.body" in script
        assert "existing.data[0]" not in script

    def test_hourly_incident_identity_ignores_suppressed_transient_findings(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        create = next(
            step
            for step in steps
            if step.get("name") == "Create hourly validation incident"
        )
        script = create["with"]["script"]

        assert "const rawPublicationFindings" in script
        assert (
            "rawPublicationFindings.map(finding => "
            "[JSON.stringify(finding), finding])"
        ) in script
        assert "const alertablePublicationFindings" in script
        assert "return context.alertable !== false" in script
        assert "finding.code !== 'publication-collector-failed'" not in script
        assert "Transient publication fallback has not reached" in script

        identity = script[
            script.index("const publicationConditionSignals"):
            script.index("const liveFailureNodes")
        ]
        assert identity.count("alertablePublicationFindings") == 1
        assert "publicationFindings.map" not in identity

        report = script[
            script.index("const report = ["):
            script.index("const contentFingerprint")
        ]
        assert "JSON.stringify(publicationFindings, null, 2)" in report

    def test_hourly_issue_storm_is_migrated_to_one_owned_current_slot(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        create = next(
            step
            for step in steps
            if step.get("name") == "Create hourly validation incident"
        )
        script = create["with"]["script"]

        assert "const activeOwnedIssues = ownedIssues.filter" in script
        assert "!(issue.body || '').includes(supersededMarker)" in script
        assert "const currentIssues = activeOwnedIssues.filter" in script
        assert "const openOwnedIssues = activeOwnedIssues.filter" in script
        assert "const openCurrentFirst = (left, right) =>" in script
        assert ").sort(openCurrentFirst)" in script
        assert ").sort(newestFirst)" in script
        assert (
            "let existing = currentIssues[0] || openOwnedIssues[0] ||"
            in script
        )
        assert "exactIssues[0] || recoveredIssues[0] || null" in script
        assert "const markCurrent = issueBody =>" in script
        assert "const supersedeOtherIssues = async canonicalNumber =>" in script
        assert "if (issue.number === canonicalNumber) continue" in script
        assert "issue.state !== 'open' && !wasCurrent" in script
        assert "issueBody.split(currentIncidentMarker).join('')" in script
        assert "state: 'closed'" in script
        assert "This ticket remains closed as history" in script
        # Only the no-history path creates a ticket; changing fingerprints is an
        # in-place update of the stable current slot.
        assert script.count("github.rest.issues.create({") == 1
        assert "body: desiredIssueBody" in script
        assert "created.data.number" in script

    def test_manual_suppression_applies_only_to_the_same_current_signal(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        create = next(
            step
            for step in steps
            if step.get("name") == "Create hourly validation incident"
        )
        script = create["with"]["script"]

        suppression = script[
            script.index("const incidentTransition = classifyFailureTransition("):
            script.index("const evidenceChanged")
        ]
        assert "existing.body || '', existing.state, fingerprintMarker" in suppression
        assert "incidentTransition === 'manually-suppressed'" in suppression
        assert "const suppressedBody = resetRecoveryStreak(" in suppression
        assert "await supersedeOtherIssues(existing.number)" in suppression
        assert "leaving it suppressed until the signal recovers" in suppression
        assert "classifyFailureTransition," in script

    def test_suppressed_transient_breaks_the_six_run_recovery_sequence(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        create = next(
            step
            for step in steps
            if step.get("name") == "Create hourly validation incident"
        )
        close = next(
            step
            for step in steps
            if step.get("name") == "Close issue after healthy publication"
        )
        condition = create.get("if", "")
        script = create["with"]["script"]

        assert "steps.publication-selector.outputs.degraded == 'true'" in condition
        reset_definition = script.index("const resetOnlyDegradation")
        suppressed_return = script.index("if (resetOnlyDegradation)")
        incident_lookup = script.index(
            "const incidentTransition = classifyFailureTransition(",
            suppressed_return,
        )
        reset_branch = script[suppressed_return:incident_lookup]
        assert "transientAlertSuppressed" in script
        assert "!hasUnclassifiedWorkflowFailure" in script[
            reset_definition:suppressed_return
        ]
        assert "const resetTransition = suppressedDegradationRecoveryTransition(" in reset_branch
        assert "markCurrent(existing.body || '')" in reset_branch
        assert "existing.state" in reset_branch
        assert "body: resetTransition.body" in reset_branch
        assert "await supersedeOtherIssues(existing.number)" in reset_branch
        assert "return;" in reset_branch
        # The reset branch has no issue creation path and mutates only the chosen
        # current slot before reconciling stale duplicates.
        assert "github.rest.issues.create({" not in reset_branch
        assert "const requiredRecoveryRuns = 6" in close["with"]["script"]
        assert "advanceRecoveryStreak(currentBody)" in close["with"]["script"]
        assert "steps.publication-selector.outputs.degraded == 'false'" in close.get(
            "if", ""
        )

    def test_hourly_open_issue_refreshes_evidence_without_notification_churn(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        create = next(
            step
            for step in steps
            if step.get("name") == "Create hourly validation incident"
        )
        script = create["with"]["script"]

        assert "hourly-ci-content-v1\\n${report}" in script
        assert "<!-- hourly-ci-content:${contentFingerprint} -->" in script
        assert "const evidenceChanged =" in script
        assert "body: desiredIssueBody" in script
        assert "title," in script
        assert script.index("if (incidentTransition === 'manually-suppressed')") < script.index(
            "const evidenceChanged ="
        )

    def test_unchanged_hourly_failure_suppresses_duplicate_notification(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        create = next(
            step
            for step in steps
            if step.get("name") == "Create hourly validation incident"
        )
        script = create["with"]["script"]

        assert "const incidentTransition = classifyFailureTransition(" in script
        assert "existing.body || '', existing.state, fingerprintMarker" in script
        assert "if (incidentTransition === 'reopened')" in script
        assert "incidentTransition === 'changed'" in script
        assert "duplicate notification suppressed" in script
        # The only comment in the failure handler is for a recurrence or a
        # genuinely changed signal; refreshed evidence is otherwise silent.
        comment = "await github.rest.issues.createComment({"
        assert script.count(comment) == 1
        comment_guard = (
            "if (incidentTransition === 'reopened' ||\n"
            "      incidentTransition === 'changed')"
        )
        assert comment_guard in script
        assert script.index(comment_guard) < script.index(comment)
        assert script.index(comment) < script.index("duplicate notification suppressed")

    def test_hourly_issue_closure_advances_only_one_owned_current_slot(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        close = next(
            step
            for step in steps
            if step.get("name") == "Close issue after healthy publication"
        )
        script = close["with"]["script"]

        assert "const requiredRecoveryRuns = 6" in script
        assert "required eligible healthy recovery runs" in script
        assert "github.paginate(github.rest.issues.listForRepo" in script
        assert "issues.filter" in script
        assert "hasExactMarker(body, ownershipMarker)" in script
        assert "body.includes(legacySignature)" in script
        assert "if (issue.pull_request) return false" in script
        assert "const currentIssues = activeOwnedIssues.filter" in script
        assert "const openOwnedIssues = activeOwnedIssues.filter" in script
        assert "const openCurrentFirst = (left, right) =>" in script
        assert ").sort(openCurrentFirst)" in script
        assert "const currentIssue = currentIssues[0] || openOwnedIssues[0] || null" in script
        assert "await supersedeOtherIssues(currentIssue.number)" in script
        assert "nextRecoveryStreak < requiredRecoveryRuns" in script
        assert "const recoveredBody = nextBody.includes(recoveredMarker)" in script
        assert "issue_number: currentIssue.number" in script
        assert "body: recoveredBody" in script
        assert "state: 'closed'" in script
        assert "The active hourly publication incident was absent" in script
        assert "Closing this single current ticket" in script
        assert "validationSource === 'separate-ci'" in script
        assert "Separate CI validated code SHA" in script
        closed_recovered = (
            "if (currentIssue.state === 'closed' &&\n"
            "    currentBody.includes(recoveredMarker))"
        )
        closed_rearm = "if (currentIssue.state === 'closed')"
        assert closed_recovered in script
        assert script.index(closed_recovered) < script.index(
            "nextRecoveryStreak < requiredRecoveryRuns"
        ) < script.index(closed_rearm)
        assert "recurrence is rearmed" in script
        assert "for (const issue of issues.data)" not in script

    def test_final_main_publication_retries_push_races_and_fails_closed(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        publish = next(step for step in steps if step.get("name") == "Commit and push")
        script = publish.get("run", "")
        assert "for attempt in 1 2 3" in script
        assert "git pull --rebase origin main" in script
        pull = script.index("git pull --rebase origin main")
        rebuild = script.index("python scripts/vllm/build_operations_snapshot.py", pull)
        render = script.index("python scripts/render.py", rebuild)
        audit = script.index("python scripts/vllm/audit_dashboard_data.py", render)
        stage = script.index("git add -- data/ dashboards/ README.md", audit)
        budget = script.index("python scripts/vllm/check_git_blob_sizes.py", stage)
        unstaged_guard = script.index(
            "assert_no_unstaged_generated_output",
            budget,
        )
        retest_decision = script.index(
            "scripts/vllm/publication_retest_required.py",
            unstaged_guard,
        )
        deterministic_retest = script.index(
            "pytest tests/ -m 'not live_data'",
            retest_decision,
        )
        live_retest = script.index(
            "pytest tests/ -m 'live_data'",
            deterministic_retest,
        )
        amend = script.index("git commit --amend --no-edit", audit)
        push = script.index("git push origin HEAD:main", amend)
        assert (
            pull
            < rebuild
            < render
            < audit
            < stage
            < budget
            < unstaged_guard
            < retest_decision
            < deterministic_retest
            < live_retest
            < amend
            < push
        )
        staged_outputs = script[stage:budget]
        assert "git add -- data/ dashboards/ README.md" in staged_outputs
        assert "if ! git diff --quiet; then" in script
        assert (
            "git ls-files --others --exclude-standard -- \\\n"
            "    data/ dashboards/ README.md"
        ) in script
        assert "The publication worktree differs from its staged candidate" in script
        assert "A generated untracked output remains unstaged" in script
        assert "INITIAL_PUBLICATION_TREE=$(git rev-parse --verify 'HEAD^{tree}')" in script
        assert '--baseline-parent "$PUBLICATION_BASELINE_REF"' in script
        assert '--tested-tree "$INITIAL_PUBLICATION_TREE"' in script
        assert 'if [ "$RETEST_REQUIRED" = "true" ]; then' in script
        assert script.index(
            "assert_no_unstaged_generated_output",
            live_retest,
        ) < amend
        assert "git push origin HEAD:main" in script
        assert "refusing to deploy unpublished output" in script
        assert "PUBLISHED_SHA=$(git rev-parse --verify 'HEAD^{commit}')" in script
        assert 'LOCAL_TEST_GAP_SAFE=true' in script
        assert "git merge-base --is-ancestor" in script
        assert '"$PUBLICATION_BASELINE_REF" "$PUBLISHED_SHA"' in script
        assert 'git rev-list --reverse' in script
        assert '^auto:\\ update\\ data($|[[:space:]])' in script
        assert '"$author" != "github-actions[bot]"' in script
        assert '"$committer" != "github-actions[bot]"' in script
        assert 'echo "published_sha=$PUBLISHED_SHA" >> "$GITHUB_OUTPUT"' in script
        assert (
            'echo "local_test_gap_safe=$LOCAL_TEST_GAP_SAFE" >> "$GITHUB_OUTPUT"'
            in script
        )
        unchanged = "Publication push was rejected while main remained unchanged"
        assert "git fetch origin main" in script[push:]
        assert "git merge-base --is-ancestor" in script[push:]
        assert unchanged in script
        assert script.index(unchanged) < script.index(
            "Main advanced during publication attempt"
        )
        assert "Failed to publish collected dashboard data" in script
        assert "exit 1" in script

        initial_stage = script.index("git add data/ dashboards/ README.md")
        initial_budget = script.index(
            "python scripts/vllm/check_git_blob_sizes.py",
            initial_stage,
        )
        initial_commit = script.index("git commit -m", initial_budget)
        assert initial_stage < initial_budget < initial_commit < pull

    def test_every_direct_git_publisher_checks_staged_blob_budget(self):
        daily = _load_workflow("daily-update.yml")
        daily_steps = next(iter(daily["jobs"].values())).get("steps", [])
        daily_publish = next(
            step for step in daily_steps if step.get("name") == "Commit and push"
        )["run"]
        assert daily_publish.index("git add data/") < daily_publish.index(
            "python scripts/vllm/check_git_blob_sizes.py"
        ) < daily_publish.index("git commit")

        for workflow_name, publish_name in (
            ("dns-health.yml", "Publish durable DNS evidence"),
            ("queue-monitor.yml", "Publish durable live queue evidence"),
            ("queue-lifecycle.yml", "Publish durable lifecycle evidence"),
        ):
            workflow = _load_workflow(workflow_name)
            steps = next(iter(workflow["jobs"].values())).get("steps", [])
            script = next(
                step["run"] for step in steps if step.get("name") == publish_name
            )
            stage = script.index('git -C "$LIVE_ROOT" add')
            guard = script.index('check_git_blob_sizes.py" --root "$LIVE_ROOT"')
            commit = script.index('git -C "$LIVE_ROOT" commit')
            assert stage < guard < commit

        preview = _load_workflow("pr-preview.yml")
        cleanup = preview["jobs"]["cleanup-preview"]["steps"]
        script = next(
            step["run"] for step in cleanup if step.get("name") == "Remove preview"
        )
        assert script.index("git add -A") < script.index(
            'python "$GUARD_SCRIPT"'
        ) < script.index("git commit")

    def test_pr_preview_separates_untrusted_validation_from_privileged_publish(self):
        preview = _load_workflow("pr-preview.yml")
        trigger = preview.get("on") or preview.get(True)
        assert set(trigger) == {"pull_request_target"}
        assert preview["permissions"] == {"contents": "read"}

        validation_workflow = _load_workflow("pr-preview-validation.yml")
        validation_trigger = validation_workflow.get("on") or validation_workflow.get(True)
        assert set(validation_trigger) == {"pull_request"}
        assert validation_workflow["permissions"] == {"contents": "read"}
        assert set(validation_workflow["jobs"]) == {"validate-preview"}
        validation = validation_workflow["jobs"]["validate-preview"]
        assert validation["permissions"] == {"contents": "read"}
        validation_checkout = next(
            step for step in validation["steps"]
            if step.get("name")
            == "Checkout pull request merge for unprivileged validation"
        )
        assert validation_checkout["with"]["persist-credentials"] is False
        assert any(
            "scripts/vllm/build_operations_snapshot.py" in str(step.get("run", ""))
            for step in validation["steps"]
        )
        assert not any("actions-gh-pages" in str(step) for step in validation["steps"])

        deploy = preview["jobs"]["deploy-preview"]
        assert "validate-preview" not in preview["jobs"]
        assert "needs" not in deploy
        assert "OWNER" in deploy["if"]
        assert "MEMBER" in deploy["if"]
        assert "COLLABORATOR" in deploy["if"]
        assert "author_association" in deploy["if"]
        assert deploy["permissions"] == {
            "contents": "write",
            "pull-requests": "write",
        }
        steps = deploy["steps"]
        trusted_checkout = next(
            step for step in steps
            if step.get("name") == "Checkout immutable trusted base"
        )
        assert trusted_checkout["with"]["ref"] == (
            "${{ github.event.pull_request.base.sha }}"
        )
        assert trusted_checkout["with"]["path"] == "trusted-base"
        assert trusted_checkout["with"]["persist-credentials"] is False
        assert "scripts" in trusted_checkout["with"]["sparse-checkout"]
        assert "config" in trusted_checkout["with"]["sparse-checkout"]

        static_checkout = next(
            step for step in steps
            if step.get("name") == "Checkout pull request as static input"
        )
        assert static_checkout["with"]["path"] == "pr-input"
        assert static_checkout["with"]["persist-credentials"] is False

        copy = next(
            step for step in steps
            if step.get("name") == "Validate and copy static pull-request inputs"
        )["run"]
        assert "find pr-input/docs pr-input/data -type l" in copy
        assert "trusted-base/scripts/vllm/check_git_blob_sizes.py" in copy
        assert "--root pr-input" in copy
        assert "10_000" in copy
        assert "384 * 1024 * 1024" in copy
        assert "cp -a pr-input/docs trusted-base/docs" in copy
        assert "cp -a pr-input/data trusted-base/data" in copy

        privileged_commands = "\n".join(
            str(step.get("run", "")) for step in steps
        )
        assert "pr-input/scripts" not in privileged_commands
        assert "python scripts/" not in privileged_commands
        assert "python -P trusted-base/scripts/" in privileged_commands

        assemble = next(
            index for index, step in enumerate(steps)
            if step.get("name") == "Assemble site"
        )
        publish = next(
            index for index, step in enumerate(steps)
            if str(step.get("uses", "")).startswith("peaceiris/actions-gh-pages@")
        )
        assert assemble < publish
        assert steps[publish]["with"]["publish_dir"] == "./trusted-base/_site"

    def test_has_buildkite_token(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "BUILDKITE_TOKEN" in text

    def test_deploys_to_gh_pages(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "peaceiris/actions-gh-pages" in text
        assert "publish_branch: gh-pages" in text

    def test_assembles_site(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "python scripts/build_site.py --cache-bust-index" in text

    def test_has_frequent_cron(self):
        data = _load_workflow("hourly-master.yml")
        triggers = data.get(True, data.get("on", {}))
        schedules = triggers.get("schedule", []) if isinstance(triggers, dict) else []
        crons = [s.get("cron", "") for s in schedules]
        has_frequent = any("* * * *" in c for c in crons)
        assert has_frequent, f"hourly-master.yml must have a recurring cron, found: {crons}"

    def test_full_refresh_runs_once_per_hour(self):
        data = _load_workflow("hourly-master.yml")
        triggers = data.get(True, data.get("on", {}))
        schedules = triggers.get("schedule", []) if isinstance(triggers, dict) else []
        crons = [s.get("cron", "") for s in schedules]
        assert crons == ["13 * * * *"], (
            "The full refresh takes about 25 minutes and must not be queued "
            f"more than once per hour; found {crons}"
        )

    def test_syncs_ci_data_from_gh_pages(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "git fetch origin gh-pages" in text or "git show origin/gh-pages" in text

    def test_retired_dashboard_control_workflows_are_removed(self):
        text = _load_workflow_text("hourly-master.yml")
        for name in ("ready-tickets-live.yml", "test-build.yml", "user-signup.yml"):
            assert not (WORKFLOWS / name).exists()
        for retired in (
            "sync_ready_tickets.py",
            "collect_test_builds.py",
            "register_test_build.py",
            "process_signup.py",
        ):
            assert retired not in text

    def test_test_failure_issue_assigns_without_mentioning_repo_owner(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "issues.addAssignees" in text
        assert "assignees: [context.repo.owner]" in text
        assert "GitHub assignee: ${context.repo.owner}." in text
        assert "cc @${context.repo.owner}" not in text

    def test_deterministic_failure_report_leads_with_concise_test_names(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "grep -E '^(FAILED|ERROR) ' test-output.txt" in text
        assert "steps.run-tests.outputs.failures" in text
        assert "**Failing deterministic tests:**" in text


class TestNoOrphanedCronSchedules:
    """Ensure only the approved collectors own recurring cron schedules."""

    def test_only_master_has_cron(self):
        # hourly-master.yml owns the frequent collection cadence.
        allowed = {
            "health-check.yml",
            "hourly-master.yml",
            "dns-health.yml",
            "queue-monitor.yml",
            "queue-lifecycle.yml",
            "publication-watchdog.yml",
        }
        for f in WORKFLOWS.glob("*.yml"):
            data = yaml.safe_load(f.read_text())
            triggers = data.get(True, data.get("on", {}))
            if not isinstance(triggers, dict):
                continue
            schedules = triggers.get("schedule", [])
            if schedules:
                assert f.name in allowed, (
                    f"{f.name} has a cron schedule but should not — "
                    f"all scheduled runs should be in one of {sorted(allowed)}"
                )


class TestPublicationWatchdogWorkflow:
    def _workflow(self):
        workflow = _load_workflow("publication-watchdog.yml")
        return workflow, workflow["jobs"]["recover"]["steps"]

    def test_has_redundant_trusted_triggers_and_minimal_permissions(self):
        workflow, _ = self._workflow()
        triggers = workflow.get(True, workflow.get("on", {}))
        assert triggers["schedule"] == [{"cron": "25 * * * *"}]
        assert triggers["repository_dispatch"] == {
            "types": ["publication_watchdog_tick"]
        }
        assert "workflow_dispatch" in triggers
        assert triggers["workflow_run"] == {
            "workflows": [
                "Queue Monitor (10 minute)",
                "Queue Lifecycle Monitor (hourly)",
                "DNS Health Monitor",
                "Site Health Check",
            ],
            "types": ["completed"],
            "branches": ["main"],
        }
        assert workflow["permissions"] == {}
        assert workflow["concurrency"] == {
            "group": "publication-watchdog",
            "cancel-in-progress": False,
        }
        job = workflow["jobs"]["recover"]
        assert job["timeout-minutes"] == 5
        assert job["permissions"] == {"actions": "write", "contents": "read"}

    def test_uses_trusted_main_and_bounded_canonical_state(self):
        _, steps = self._workflow()
        checkout = steps[0]
        assert checkout["uses"] == "actions/checkout@v4"
        assert checkout["with"] == {"ref": "main", "persist-credentials": False}
        names = [step.get("name") for step in steps]
        read = steps[names.index("Read canonical publication and collection state")]
        plan = steps[names.index("Plan proactive publication recovery")]
        dispatch = steps[names.index("Dispatch stale publication recovery")]
        assert names.index(read["name"]) < names.index(plan["name"]) < names.index(
            dispatch["name"]
        )
        assert "contents/data/vllm/ci/publication_status.json?ref=gh-pages" in read["run"]
        assert "application/vnd.github.raw+json" in read["run"]
        assert "actions/workflows/hourly-master.yml/runs?per_page=100" in read["run"]
        assert "plan_publication_watchdog.py" in plan["run"]
        assert "--workflow-runs" in plan["run"]
        assert "--max-age-minutes 45" in plan["run"]
        assert "--retry-cooldown-minutes 30" in plan["run"]
        assert "--active-run-max-age-minutes 75" in plan["run"]
        assert '--github-output "$GITHUB_OUTPUT"' in plan["run"]
        text = _load_workflow_text("publication-watchdog.yml")
        assert "github.event.workflow_run.head_sha" not in text
        assert "download-artifact" not in text
        assert "client_payload" not in text

    def test_dispatches_only_planned_fixed_main_generation(self):
        _, steps = self._workflow()
        dispatch = next(
            step
            for step in steps
            if step.get("name") == "Dispatch stale publication recovery"
        )
        assert dispatch["if"] == "steps.recovery-plan.outputs.required == 'true'"
        assert dispatch["env"] == {
            "GH_TOKEN": "${{ github.token }}",
            "RECOVERY_REASON": "${{ steps.recovery-plan.outputs.reason }}",
            "OBSERVED_GENERATION": (
                "${{ steps.recovery-plan.outputs.observed_generation }}"
            ),
        }
        assert "hourly-master.yml/dispatches" in dispatch["run"]
        assert "inputs: {watchdog_generation: $watchdog_generation}" in dispatch["run"]
        assert "--arg ref main" in dispatch["run"]

    def test_is_not_a_pages_main_or_data_writer(self):
        text = _load_workflow_text("publication-watchdog.yml")
        for forbidden in (
            "peaceiris/actions-gh-pages",
            "publish_branch:",
            "git push",
            "git commit",
            "git add",
            "contents: write",
        ):
            assert forbidden not in text


class TestDnsHealthWorkflow:
    def _workflow(self):
        workflow = _load_workflow("dns-health.yml")
        return workflow, workflow["jobs"]["collect"]["steps"]

    def test_is_three_hourly_isolated_and_minimally_privileged(self):
        workflow, _ = self._workflow()
        triggers = workflow.get(True, workflow.get("on", {}))
        assert triggers["schedule"] == [{"cron": "39 */3 * * *"}]
        assert triggers["repository_dispatch"] == {"types": ["dns_health_tick"]}
        assert "workflow_dispatch" in triggers
        assert workflow["permissions"] == {}
        assert workflow["concurrency"] == {
            "group": "dns-health-data-publish",
            "cancel-in-progress": False,
        }
        assert workflow["jobs"]["collect"]["timeout-minutes"] == 34
        assert workflow["jobs"]["collect"]["permissions"] == {
            "actions": "read",
            "contents": "write",
        }
        assert workflow["jobs"]["collect"]["outputs"] == {
            "dns_generation": "${{ steps.dns-generation.outputs.generated_at }}"
        }

        reconcile = workflow["jobs"]["reconcile-publication"]
        assert reconcile["needs"] == "collect"
        assert reconcile["timeout-minutes"] == 5
        assert reconcile["permissions"] == {
            "actions": "write",
            "contents": "read",
        }

    def test_restores_exact_state_collects_and_validates_before_publish(self):
        _, steps = self._workflow()
        names = [step.get("name") for step in steps]
        install = steps[names.index("Install dependencies")]["run"]
        preflight = steps[names.index("Preflight DNS-only validator")]["run"]
        restore_step = steps[names.index("Resolve durable DNS scanner state")]
        restore = restore_step["run"]
        collect = steps[names.index("Collect DNS failure observations")]
        validate = steps[names.index("Validate bounded DNS artifacts")]["run"]
        generation_step = steps[names.index("Capture validated DNS generation")]
        encrypt_step = steps[names.index("Encrypt durable DNS scanner state")]
        encrypt = encrypt_step["run"]
        publish = steps[names.index("Publish durable DNS evidence")]["run"]

        assert names.index("Install dependencies") < names.index(
            "Preflight DNS-only validator"
        ) < names.index("Resolve durable DNS scanner state") < names.index(
            "Collect DNS failure observations"
        ) < names.index("Validate bounded DNS artifacts") < names.index(
            "Capture validated DNS generation"
        ) < names.index(
            "Encrypt durable DNS scanner state"
        ) < names.index(
            "Publish durable DNS evidence"
        )
        assert "requests cryptography" in install
        assert "python -S scripts/vllm/audit_dashboard_data.py --dns-only" in preflight
        assert restore_step["env"] == {
            "DNS_STATE_ENCRYPTION_KEY": "${{ secrets.DNS_STATE_ENCRYPTION_KEY }}"
        }
        assert 'if [ -z "${DNS_STATE_ENCRYPTION_KEY:-}" ]' in restore
        assert "DNS state encryption key is unavailable" in restore
        assert "git ls-remote --exit-code origin refs/heads/dns-health-data" in restore
        assert "DNS_DATA_STATUS" in restore
        assert '"$DNS_DATA_STATUS" -eq 2' in restore
        assert "+refs/heads/dns-health-data:refs/remotes/origin/dns-health-data" in restore
        assert (
            "origin/dns-health-data:data/vllm/ci/dns_health/scan_state.fernet"
            in restore
        )
        assert "dns_state_crypto.py decrypt" in restore
        assert "data/vllm/ci/dns_health/scan_state.json.gz" in restore

        assert collect.get("env", {}).get("BUILDKITE_TOKEN") == (
            "${{ secrets.BUILDKITE_TOKEN }}"
        )
        assert "DNS_STATE_ENCRYPTION_KEY" not in collect.get("env", {})
        script = collect["run"]
        assert "scripts/vllm/collect_dns_failures.py" in script
        assert "--merge-state-git-ref" not in script
        argument_lines = {line.strip() for line in script.splitlines()}
        assert "--state data/vllm/ci/dns_health/scan_state.json.gz" in argument_lines
        assert "--output data/vllm/ci/dns_failures.json" in argument_lines
        assert "--discover-days 30" in argument_lines
        assert "--max-logs 500" in argument_lines
        assert "--max-requests 110" in argument_lines
        assert "--minimum-interval-hours 3" in argument_lines
        assert "--time-budget-seconds 600" in argument_lines
        assert (
            "--classification-cache data/vllm/ci/.cache/dns-classifications-v1"
            in argument_lines
        )

        assert "python -m json.tool data/vllm/ci/dns_failures.json" in validate
        assert "python -S scripts/vllm/audit_dashboard_data.py --dns-only" in validate
        assert "gzip -t data/vllm/ci/dns_health/scan_state.json.gz" in validate
        assert "chmod 0600 data/vllm/ci/dns_health/scan_state.json.gz" in validate
        assert generation_step["id"] == "dns-generation"
        assert "DNS_GENERATED_AT=$(jq -er" in generation_step["run"]
        assert 'echo "generated_at=$DNS_GENERATED_AT" >> "$GITHUB_OUTPUT"' in (
            generation_step["run"]
        )

        assert encrypt_step["env"] == {
            "DNS_STATE_ENCRYPTION_KEY": "${{ secrets.DNS_STATE_ENCRYPTION_KEY }}"
        }
        assert "dns_state_crypto.py encrypt" in encrypt
        assert "data/vllm/ci/dns_health/scan_state.json.gz" in encrypt
        assert '"$RUNNER_TEMP/dns-health-scan-state.fernet"' in encrypt
        assert "rm -f data/vllm/ci/dns_health/scan_state.json.gz" in encrypt

        assert "switch --orphan dns-health-data-publish" in publish
        assert "data/vllm/ci/dns_failures.json" in publish
        assert "data/vllm/ci/dns_health/scan_state.fernet" in publish
        assert "data/vllm/ci/dns_health/scan_state.json.gz" not in publish
        assert "DNS_STATE_ENCRYPTION_KEY" not in publish
        assert "push --force origin HEAD:dns-health-data" in publish
        assert "gh-pages" not in publish
        assert "data/vllm/ci/dns_health/" in (
            REPO_ROOT / ".gitignore"
        ).read_text().splitlines()

        workflow, _ = self._workflow()
        reconcile = workflow["jobs"]["reconcile-publication"]
        reconcile_steps = reconcile["steps"]
        reconcile_names = [step.get("name") for step in reconcile_steps]
        plan = reconcile_steps[
            reconcile_names.index("Plan canonical publication reconciliation")
        ]
        dispatch = reconcile_steps[
            reconcile_names.index("Dispatch canonical DNS reconciliation")
        ]
        assert reconcile_steps[0]["uses"] == "actions/checkout@v4"
        assert reconcile_steps[0]["with"] == {
            "ref": "main",
            "persist-credentials": False,
        }
        assert reconcile_names.index(
            "Plan canonical publication reconciliation"
        ) < reconcile_names.index("Dispatch canonical DNS reconciliation")
        assert plan["id"] == "publication-reconcile"
        assert "origin/gh-pages:data/vllm/ci/publication_status.json" in plan["run"]
        assert "plan_dns_publication_reconcile.py" in plan["run"]
        assert '--github-output "$GITHUB_OUTPUT"' in plan["run"]
        assert dispatch["if"] == (
            "steps.publication-reconcile.outputs.required == 'true'"
        )
        assert dispatch["env"] == {
            "GH_TOKEN": "${{ github.token }}",
            "RECONCILE_REASON": "${{ steps.publication-reconcile.outputs.reason }}",
            "TARGET_DNS_GENERATION": "${{ needs.collect.outputs.dns_generation }}",
        }
        assert "PENDING_RUNS" not in dispatch["run"]
        assert "workflow_runs" not in dispatch["run"]
        assert "hourly-master.yml/dispatches" in dispatch["run"]
        assert "inputs: {dns_generation: $dns_generation}" in dispatch["run"]
        assert "--input -" in dispatch["run"]

    def test_canonical_workflows_import_only_the_validated_public_aggregate(self):
        hourly = _load_workflow("hourly-master.yml")
        steps = next(iter(hourly["jobs"].values()))["steps"]
        names = [step.get("name") for step in steps]
        sync_index = names.index("Sync validated DNS health aggregate")
        selector_index = names.index("Select validated publication surfaces")
        assert sync_index < selector_index
        sync = steps[sync_index]["run"]
        assert "origin/dns-health-data:data/vllm/ci/dns_failures.json" in sync
        assert "audit_dashboard_data.py" in sync and "--dns-only" in sync
        assert "data/vllm/ci/dns_failures.json" in sync
        assert "scan_state.json.gz" not in sync
        assert "scan_state.fernet" not in sync
        assert "record_surface_failure dns_health" not in sync
        assert "retaining the existing safe candidate" in sync
        assert "REMOTE_DNS_STATUS" in sync
        assert '"$REMOTE_DNS_GENERATED" < "$LOCAL_DNS_GENERATED"' in sync

        deploy = _load_workflow_text("deploy-pages.yml")
        assert "origin/dns-health-data:data/vllm/ci/dns_failures.json" in deploy
        assert "--dns-only --dns-path" in deploy
        assert "data/vllm/ci/dns_health/scan_state.json.gz" not in deploy
        assert "data/vllm/ci/dns_health/scan_state.fernet" not in deploy
        assert "REMOTE_DNS_GENERATED" in deploy
        assert "LOCAL_DNS_GENERATED" in deploy


class TestSiteHealthWorkflow:
    def _steps(self):
        workflow = _load_workflow("health-check.yml")
        return workflow, workflow["jobs"]["check"]["steps"]

    def test_is_hourly_offset_manual_and_read_only_for_pages(self):
        workflow, _ = self._steps()
        triggers = workflow.get(True, workflow.get("on", {}))
        assert triggers["schedule"] == [{"cron": "57 * * * *"}]
        assert "workflow_dispatch" in triggers
        assert workflow["permissions"] == {
            "contents": "read",
            "issues": "write",
        }
        assert workflow["concurrency"] == {
            "group": "site-health-monitor",
            "cancel-in-progress": False,
        }
        assert workflow["jobs"]["check"]["timeout-minutes"] == 10

        text = _load_workflow_text("health-check.yml")
        for forbidden in (
            "peaceiris/actions-gh-pages",
            "publish_branch: gh-pages",
            "ref: gh-pages",
            "git push",
            "git commit",
            "git add",
            "scripts/build_site.py",
        ):
            assert forbidden not in text

    def test_checker_report_upload_reconcile_and_enforcement_are_ordered(self):
        _, steps = self._steps()
        names = [step.get("name") for step in steps]
        expected = [
            "Check out monitor",
            "Run synthetic site health check",
            "Normalize bounded health evidence",
            "Upload bounded site health report",
            "Reconcile marker-owned site health issue",
            "Enforce synthetic health result",
        ]
        assert names == expected
        assert steps[0]["uses"] == "actions/checkout@v4"

        checker = steps[1]
        assert checker["id"] == "synthetic-health"
        assert checker["continue-on-error"] is True
        for token in (
            "python scripts/vllm/check_site_health.py",
            "--site-url \"$SITE_URL\"",
            "--max-publication-age-hours 3",
            "--output \"$REPORT_PATH\"",
            "--github-output \"$GITHUB_OUTPUT\"",
            "--markdown-output \"$DETAILS_PATH\"",
        ):
            assert token in checker["run"]

        normalize = steps[2]
        assert normalize["if"] == "always()"
        assert normalize["id"] == "health-result"
        assert "max_report_bytes = 64 * 1024" in normalize["run"]
        assert "report_path.write_bytes(encoded)" in normalize["run"]
        assert "if not value.strip()" in normalize["run"]

        upload = steps[3]
        assert upload["if"] == "always()"
        assert upload["uses"] == "actions/upload-artifact@v4"
        assert upload["with"]["path"] == "${{ runner.temp }}/site-health-report.json"
        assert upload["with"]["if-no-files-found"] == "error"
        assert upload["with"]["retention-days"] == 14

        reconcile = steps[4]
        enforce = steps[5]
        assert reconcile["if"] == "always()"
        assert reconcile["uses"] == "actions/github-script@v7"
        assert enforce["if"] == "always()"
        assert "RECONCILE_OUTCOME" in enforce["env"]
        assert "RECONCILED" in enforce["env"]
        assert '[ "$HEALTHY" != "true" ]' in enforce["run"]
        assert "exit 1" in enforce["run"]

    def test_missing_or_malformed_checker_evidence_fails_closed(self):
        _, steps = self._steps()
        normalize = steps[2]
        script = normalize["run"]
        for output in (
            "CHECKER_HEALTHY",
            "OVERALL_STATUS",
            "SITE_HTTP",
            "SITE_BYTES",
            "PUBLICATION_HTTP",
            "PUBLICATION_MODE",
            "PUBLICATION_STATUS",
            "GENERATED_AT",
            "AGE_HOURS",
            "REASON_COUNT",
        ):
            assert output in normalize["env"]
            assert output in script
        assert 'os.environ.get("CHECKER_OUTCOME") == "success"' in script
        assert "checker_healthy is True" in script
        assert "and not missing" in script
        assert "and report_valid" in script
        assert 'type(report.get("schema_version")) is not int' in script
        assert "report healthy disagreed with checker output" in script
        assert "report reason count disagreed with checker output" in script
        assert "report overall_status disagreed with checker output" in script
        assert '"healthy": False' in steps[1]["run"]

    def test_normalizer_cross_checks_typed_report_fields_with_outputs(self):
        _, steps = self._steps()
        script = steps[2]["run"]
        for token in (
            "def parse_nonnegative_int_output",
            "raw_value != str(value) or value < 0",
            "isinstance(value, int)",
            "not isinstance(value, bool)",
            'not isinstance(site, dict)',
            'not isinstance(publication, dict)',
            "report site HTTP disagreed with checker output",
            "report site bytes disagreed with checker output",
            "report publication HTTP disagreed with checker output",
            '("mode", "publication_mode", "publication mode")',
            '("status", "publication_status", "publication status")',
            '("generated_at", "generated_at", "publication timestamp")',
            'required[output_key] != expected_output',
            'f"report {label} disagreed with checker output"',
            "datetime.fromisoformat",
            "parsed_generated_at.tzinfo is None",
            "not math.isfinite(report_age)",
            "not math.isfinite(output_age)",
            'required["age_hours"] != str(report_age)',
            "report publication age disagreed with checker output",
            "report reason count disagreed with checker output",
            "healthy report contained findings",
            "healthy report failed the site-shell contract",
            "healthy report lacked publication HTTP 200",
            "healthy report used an unhealthy publication mode",
            "healthy report used an unhealthy publication status",
            "healthy report was publication-blocked",
            "healthy report had contradictory fallback state",
            "healthy report had an invalid publication age",
        ):
            assert token in script

    def test_issue_reconciliation_is_marker_owned_stable_and_comment_free(self):
        _, steps = self._steps()
        normalize = steps[2]
        reconcile = steps[4]
        script = reconcile["with"]["script"]
        assert reconcile["env"]["BODY_PATH"].endswith("/site-health-issue.md")
        assert reconcile["env"]["OWNERSHIP_MARKER"] == (
            "<!-- vllm-ci-dashboard:site-health:v1 -->"
        )
        assert "fs.readFileSync(process.env.BODY_PATH" in script
        assert "github.paginate(" in script
        assert "github.rest.issues.getLabel" in script
        assert "if (error.status !== 404) throw error" in script
        assert "github.rest.issues.createLabel" in script
        assert "github.rest.issues.listForRepo" in script
        assert "state: 'all'" in script
        lookup = script[
            script.index("const allIssues") : script.index("const owned")
        ]
        assert "labels:" not in lookup
        assert ".some(line => line.trim() === ownershipMarker)" in script
        assert "hasExactMarker(issue.body)" in script
        assert script.index("github.paginate(") < script.index(
            "github.rest.issues.create("
        )
        assert "github.rest.issues.update" in script
        assert "github.rest.issues.createComment" not in script
        existing_branch = script.index("if (existing) {")
        update_start = script.index(
            "await github.rest.issues.update({", existing_branch
        )
        update_end = script.index("});", update_start)
        assert "labels:" not in script[update_start:update_end]
        assert "github.rest.issues.addLabels" in script
        assert "labels: [labelName]" in script
        assert "${{" not in script
        assert "actions/runs/" in normalize["run"]
        assert "/deployments" in normalize["run"]
        assert "html.escape(details" in normalize["run"]

    def test_issue_requires_two_healthy_probes_and_honors_manual_close(self):
        _, steps = self._steps()
        script = steps[4]["with"]["script"]
        for token in (
            "site-health-state:recovery=",
            "const priorRecovery",
            "const priorRearmed",
            "recovery = Math.min(2, priorRecovery + 1)",
            "rearmed = recovery >= 2",
            "Two consecutive healthy probes confirmed recovery",
            "existing?.state === 'closed' && !priorRearmed",
            "state = 'closed'",
            "state = 'open'",
            "recovery = 0",
            "rearmed = false",
            "const existing = owned[0] || null",
            "duplicate.number !== existing?.number",
        ):
            assert token in script
        assert "owned.find(issue => issue.state === 'open')" not in script
        manual_close = "existing?.state === 'closed' && !priorRearmed"
        reopen = "state = 'open'"
        assert script.index(manual_close) < script.index(reopen, script.index(manual_close))
        assert "does not post hourly comments" in steps[2]["run"]

    def test_hourly_master_does_not_claim_to_replace_health_monitor(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "health-check.yml" not in text
        assert "synthetic site\n# monitor remains independent" in text


class TestNightlyCIWorkflow:
    def test_nightly_failure_issue_assigns_without_mentioning_repo_owner(self):
        text = _load_workflow_text("nightly-ci.yml")
        assert "issues.addAssignees" in text
        assert "assignees: [context.repo.owner]" in text
        assert "GitHub assignee: ${context.repo.owner}." in text
        assert "cc @${context.repo.owner}" not in text

    def test_nightly_issue_lifecycle_only_mutates_nightly_owned_issues(self):
        data = _load_workflow("nightly-ci.yml")
        create_steps = data["jobs"]["create-issue-on-failure"]["steps"]
        close_steps = data["jobs"]["close-issue-on-success"]["steps"]
        create = next(
            step
            for step in create_steps
            if step.get("name") == "Create or update GitHub issue"
        )["with"]["script"]
        close = next(
            step
            for step in close_steps
            if step.get("name") == "Close resolved ci-failure issues"
        )["with"]["script"]

        for script in (create, close):
            assert "<!-- ci-failure-owner:nightly-ci -->" in script
            assert (
                "*This issue was created automatically by the nightly CI workflow.*"
                in script
            )
            assert "github.paginate(github.rest.issues.listForRepo" in script
        assert "const existing = ownedIssues[0] || null" in create
        assert "ownedIssues.slice(1)" in create
        assert "Superseded by #${issue.number}" in create
        assert "github.rest.issues.addLabels" in create
        assert "labels: 'ci-failure'" not in create
        assert "labels: 'ci-failure'" not in close
        assert "hasExactMarker" in create
        assert "hasExactMarker" in close
        assert "existing.data[0]" not in create
        assert "issues.filter" in close
        assert "for (const issue of issues.data)" not in close


# ---------------------------------------------------------------------------
# 3c. Framework isolation
# ---------------------------------------------------------------------------


class TestFrameworkIsolation:
    """Validate workflows don't clobber other frameworks' data."""

    def _deploying_workflows(self):
        """Return workflow names that deploy to gh-pages."""
        result = []
        for f in WORKFLOWS.glob("*.yml"):
            text = f.read_text()
            if "peaceiris/actions-gh-pages" in text:
                result.append(f.name)
        return result

    def test_deploying_workflows_sync_ci_from_gh_pages(self):
        """All workflows that deploy to gh-pages must sync CI data from gh-pages first,
        to prevent overwriting fresh CI data with stale copies from main."""
        for wf in self._deploying_workflows():
            # pr-preview.yml deploys to a subdirectory (pr-preview/pr-N), not root
            if wf == "pr-preview.yml":
                continue
            text = _load_workflow_text(wf)
            assert "git fetch origin gh-pages" in text or "git show origin/gh-pages" in text, (
                f"{wf} deploys to gh-pages but does not sync CI data from gh-pages first. "
                "This will overwrite fresh CI data with stale copies from main."
            )

    def test_pr_preview_rebuilds_untracked_operations_input(self):
        data = _load_workflow("pr-preview.yml")
        steps = data["jobs"]["deploy-preview"]["steps"]
        rebuild = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Rebuild v2 operations snapshot"
        )
        assemble = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Assemble site"
        )
        command = steps[rebuild].get("run", "")
        assert "scripts/vllm/build_operations_snapshot.py" in command
        assert "data/vllm/ci/operations_v2.json" in command
        assert rebuild < assemble

    def test_browser_smoke_rebuilds_untracked_operations_input(self):
        package = json.loads(
            (REPO_ROOT / "tests" / "browser" / "package.json").read_text()
        )
        pretest = package["scripts"]["pretest"]
        rebuild = pretest.index("scripts/vllm/build_operations_snapshot.py")
        assemble = pretest.index("scripts/build_site.py")
        assert rebuild < assemble

    def test_shard_bases_available_at_deploy(self):
        """shard_bases.json must be on the main branch (committed by hourly-master)
        so deploy workflows can include it in _site/. No gh-pages sync needed."""
        shard_path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "vllm"
            / "ci"
            / "shard_bases.json"
        )
        assert shard_path.exists(), (
            "shard_bases.json not found on main branch. "
            "hourly-master should generate and commit it."
        )

    def test_ci_collect_is_validation_only_and_cannot_write_main(self):
        text = _load_workflow_text("ci-collect.yml")
        workflow = _load_workflow("ci-collect.yml")
        assert workflow.get("name") == "CI Data Collection (Validation Only)"
        assert workflow.get("permissions", {}).get("contents") == "read"
        assert workflow.get("concurrency", {}).get("group") == (
            "ci-collect-validation"
        )
        assert "Report validation-only collection" in text
        assert "select publication surfaces and update main" in text
        assert "git add" not in text
        assert "git commit" not in text
        assert "git push" not in text
        assert "peaceiris/actions-gh-pages" not in text

    def test_queue_monitor_only_writes_queue_data(self):
        """queue-monitor.yml should only write queue-monitor datasets/state."""
        text = _load_workflow_text("queue-monitor.yml")
        git_adds = re.findall(r"git add\s+(\S+)", text)
        allowed = {
            "queue_timeseries.jsonl",
            "queue_jobs.json",
            "open_queue_issues.json",
            "open_queue_zombie_issues.json",
        }
        for target in git_adds:
            basename = Path(target).name
            assert basename in allowed, (
                f"queue-monitor.yml has 'git add {target}' — expected only queue-monitor data/state files"
            )


# ---------------------------------------------------------------------------
# 3d. Cron schedule safety
# ---------------------------------------------------------------------------


class TestCronSchedules:
    """Validate no cron schedule conflicts between hourly workflows."""

    def _extract_crons(self):
        """Extract all cron schedules from all workflows."""
        result = []
        for f in WORKFLOWS.glob("*.yml"):
            data = yaml.safe_load(f.read_text())
            # YAML parses 'on:' as boolean True
            triggers = data.get(True, data.get("on", {}))
            if not isinstance(triggers, dict):
                continue
            schedules = triggers.get("schedule", [])
            if not schedules:
                continue
            for s in schedules:
                cron = s.get("cron", "")
                if cron:
                    result.append((f.name, cron))
        return result

    def test_no_conflicting_cron_minutes_for_hourly_workflows(self):
        """Hourly workflows must not share the same minute to prevent deploy races."""
        # Only check hourly workflows (cron minute field is a number, hour field is *)
        hourly_by_minute = {}
        for wf, cron in self._extract_crons():
            parts = cron.split()
            if len(parts) < 5:
                continue
            minute, hour = parts[0], parts[1]
            # Only flag conflicts for workflows that run every hour (hour = *)
            if hour != "*":
                continue
            if minute in hourly_by_minute:
                raise AssertionError(
                    f"Cron minute conflict at :{minute} between "
                    f"{hourly_by_minute[minute]} and {wf}. "
                    "Hourly workflows must use different minutes to prevent deploy races."
                )
            hourly_by_minute[minute] = wf

    def test_cron_minutes_have_safe_spacing(self):
        """Hourly workflows use safe spacing for their expected workloads."""
        hourly_minutes = []
        for wf, cron in self._extract_crons():
            parts = cron.split()
            if len(parts) < 5:
                continue
            minute, hour = parts[0], parts[1]
            if hour != "*":
                continue
            try:
                hourly_minutes.append((int(minute), wf))
            except ValueError:
                continue
        hourly_minutes.sort()
        for i in range(len(hourly_minutes) - 1):
            m1, wf1 = hourly_minutes[i]
            m2, wf2 = hourly_minutes[i + 1]
            gap = m2 - m1
            assert gap >= 10, (
                f"Only {gap} minutes between {wf1} (:{m1:02d}) and {wf2} (:{m2:02d}). "
                "Hourly workflows should be at least 10 minutes apart."
            )


class TestDeployDataFreshness:
    """Ensure deploy workflows don't overwrite fresh main data with stale gh-pages data."""

    def test_manual_deploy_installs_only_valid_non_regressing_queue_jobs(self):
        data = _load_workflow("deploy-pages.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        script = next(
            step["run"]
            for step in steps
            if step.get("name") == "Sync live data from gh-pages"
        )

        remote_read = "git show origin/queue-data:data/vllm/ci/queue_jobs.json"
        validation = "remote_timestamp < local_timestamp"
        install = 'install -m 0644 "$LIVE_QUEUE_JOBS"'
        assert remote_read in script
        assert 'payload.get("ts")' in script
        assert 'for key in ("pending", "running")' in script
        assert "datetime.fromisoformat" in script
        assert validation in script
        assert "would regress the embedded timestamp" in script
        assert install in script
        assert (
            script.index(remote_read)
            < script.index(validation)
            < script.index(install)
        )

    @pytest.mark.parametrize(
        "workflow",
        ["hourly-master.yml", "deploy-pages.yml"],
    )
    def test_queue_history_is_merged_by_timestamp(self, workflow):
        text = _load_workflow_text(workflow)
        assert "collect_queue_snapshot.py --merge-history-git-ref origin/gh-pages" in text, (
            f"{workflow} must merge queue history rather than replace by line count"
        )
        assert "take the longer file" not in text

    @pytest.mark.parametrize(
        "workflow",
        ["hourly-master.yml", "deploy-pages.yml"],
    )
    def test_queue_history_merge_precedes_retention_prune(self, workflow):
        data = _load_workflow(workflow)
        jobs_with_history = 0
        for job in data["jobs"].values():
            steps = job.get("steps", [])
            merge_indexes = [
                index
                for index, step in enumerate(steps)
                if "collect_queue_snapshot.py --merge-history-git-ref origin/gh-pages"
                in (step.get("run", "") or "")
            ]
            if not merge_indexes:
                continue
            jobs_with_history += 1
            prune_indexes = [
                index
                for index, step in enumerate(steps)
                if "collect_queue_snapshot.py --prune-only" in (step.get("run", "") or "")
            ]
            assert prune_indexes, f"{workflow} merges queue history but never applies retention"
            assert max(merge_indexes) < min(prune_indexes), (
                f"{workflow} must merge all append-only history before applying retention"
            )

        assert jobs_with_history == 1

    def test_hourly_hotness_collection_follows_stale_data_sync(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        sync_idx = next(
            i for i, step in enumerate(steps) if step.get("name") == "Sync CI data from gh-pages"
        )
        hotness_idx = next(
            i
            for i, step in enumerate(steps)
            if step.get("name", "").startswith("Collect AMD hotness")
        )

        assert sync_idx < hotness_idx
        for step in steps[hotness_idx + 1 :]:
            run = step.get("run", "") or ""
            assert "git show origin/gh-pages:data/vllm/ci/hotness.json" not in run

    def test_hourly_history_sync_precedes_prune_without_api_collection(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name", "") for step in steps]
        assert names.index("Sync queue data from durable live branch") < names.index(
            "Normalize and prune queue history"
        )
        assert "Collect queue snapshot" not in names
        lifecycle_step = next(
            step for step in steps if step.get("name") == "Sync validated queue lifecycle aggregate"
        )
        lifecycle_run = lifecycle_step.get("run", "")
        assert "origin/queue-lifecycle-data:data/vllm/ci/queue_lifecycle.json" in lifecycle_run
        assert (
            "+refs/heads/queue-lifecycle-data:refs/remotes/origin/queue-lifecycle-data"
            in lifecycle_run
        )
        assert "--queue-lifecycle-only" in lifecycle_run
        assert '--queue-lifecycle-path "$LIVE_QUEUE_LIFECYCLE"' in lifecycle_run
        strict_audit = lifecycle_run.index("--queue-lifecycle-only")
        non_regression = lifecycle_run.index("if remote < local:")
        install = lifecycle_run.index("install -m 0644")
        assert strict_audit < non_regression < install
        assert 'generation(sys.argv[1], "durable")' in lifecycle_run
        assert 'generation(sys.argv[2], "local")' in lifecycle_run
        assert "would regress generated_at" in lifecycle_run
        assert 'record_surface_failure queue_lifecycle' in lifecycle_run

    def test_lifecycle_collection_is_not_a_queue_or_pages_dependency(self):
        queue_text = _load_workflow_text("queue-monitor.yml")
        hourly_text = _load_workflow_text("hourly-master.yml")
        assert "collect_queue_lifecycle.py" not in queue_text
        assert "Collect canonical AMD queue lifecycle" not in hourly_text
        assert "Sync validated queue lifecycle aggregate" in hourly_text

    def test_deploy_pages_does_not_sync_ci_json_from_ghpages(self):
        """deploy-pages.yml must NOT overwrite CI analysis JSON files from gh-pages.

        Main branch always has the latest data (committed by hourly-master).
        The deploy workflow should use main's data as-is, not replace it
        with potentially stale gh-pages copies.

        Only queue_timeseries.jsonl (append-only) may be synced from gh-pages.
        """
        wf = _load_workflow("deploy-pages.yml")
        wf_text = (WORKFLOWS / "deploy-pages.yml").read_text()

        # Check that no step writes CI JSON files from gh-pages to local.
        # Pattern: echo "$LIVE" > data/vllm/ci/<file>  (overwrite with gh-pages data)
        # Reading gh-pages for corruption checks is OK; WRITING is not.
        import re as _re

        ci_files = [
            "ci_health.json",
            "parity_report.json",
            "analytics.json",
            "shard_bases.json",
            "group_changes.json",
            "amd_test_matrix.json",
        ]
        for f in ci_files:
            # Match: > data/vllm/ci/<file>  (redirect/write to local file)
            write_pattern = _re.compile(r">\s*data/vllm/ci/" + _re.escape(f))
            assert not write_pattern.search(wf_text), (
                f"deploy-pages.yml writes {f} from gh-pages to local, which "
                f"overwrites fresh main data with stale copies. Remove the sync."
            )

    def test_manual_root_deploy_rebuilds_and_audits_before_assembly(self):
        data = _load_workflow("deploy-pages.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        assert (
            names.index("Rebuild v2 operations snapshot")
            < names.index("Run dashboard data audit")
            < names.index("Assemble site")
        )

    def test_hourly_master_syncs_before_collection(self):
        """hourly-master.yml may sync CI data from gh-pages, but ONLY
        before the collection step (as seed data for the collector).
        The collector then overwrites with fresh Buildkite data.

        Verify the sync step comes BEFORE 'Collect CI data'.
        """
        wf_text = (WORKFLOWS / "hourly-master.yml").read_text()
        lines = wf_text.split("\n")

        sync_line = None
        collect_line = None
        for i, line in enumerate(lines):
            if "Sync CI data from gh-pages" in line:
                sync_line = i
            if "Collect CI data" in line and collect_line is None:
                collect_line = i

        if sync_line is None:
            return  # no sync step, that's fine

        assert collect_line is not None, (
            "hourly-master.yml has 'Sync CI data from gh-pages' but no "
            "'Collect CI data' step to overwrite the synced data."
        )
        assert sync_line < collect_line, (
            f"'Sync CI data from gh-pages' (line {sync_line}) must come BEFORE "
            f"'Collect CI data' (line {collect_line}). Otherwise fresh data "
            f"gets overwritten with stale gh-pages copies."
        )

    def test_no_ghpages_sync_after_collection(self):
        """No workflow step after 'Collect CI data' should overwrite **CI
        analysis data** (the files produced by ``scripts/collect_ci.py``)
        with a stale gh-pages copy.

        Other datasets sync after collection and that's correct — for example,
        ``queue_timeseries.jsonl`` is appended by the queue-monitor cron.

        We match by **step** (parsed YAML) to catch cases where the
        filename is pulled from a shell for-loop var like ``$f``, which
        would bypass a line-by-line scanner.
        """
        data = _load_workflow("hourly-master.yml")
        job = next(iter(data["jobs"].values()))
        steps = job.get("steps", []) or []

        collect_idx = next(
            (i for i, s in enumerate(steps) if s.get("name") == "Collect CI data"),
            None,
        )
        if collect_idx is None:
            pytest.skip("no Collect CI data step")

        # These are the files ``collect_ci.py`` produces — the ones it would
        # be a bug to overwrite with stale gh-pages copies after collection.
        CI_ANALYSIS_FILES = {
            "ci_health.json",
            "parity_report.json",
            "config_parity.json",
            "flaky_tests.json",
            "failure_trends.json",
            "quarantine.json",
            "analytics.json",
            "shard_bases.json",
            "group_changes.json",
            "amd_test_matrix.json",
            "hotness.json",
            "open_queue_issues.json",
        }

        for step in steps[collect_idx + 1 :]:
            run = step.get("run", "") or ""
            if "git show origin/gh-pages:data/vllm/ci/" not in run:
                continue
            # Parse the for-loop filenames if present. If any name is in
            # CI_ANALYSIS_FILES, that's a stale-overwrite bug.
            for m in re.finditer(r"for\s+\w+\s+in\s+([^;]+?);\s*do", run):
                files = m.group(1).split()
                overlap = set(files) & CI_ANALYSIS_FILES
                assert not overlap, (
                    f"Step {step.get('name')!r} syncs CI analysis files "
                    f"{overlap} from gh-pages AFTER collection — overwrites "
                    "fresh main-branch data with stale copies."
                )
            # Direct references (no loop): check the literal path.
            for m in re.finditer(r"git show origin/gh-pages:data/vllm/ci/([^\s]+)", run):
                target = m.group(1)
                # Allow sync into non-CI-analysis paths such as queue history.
                basename = Path(target).name
                assert basename not in CI_ANALYSIS_FILES, (
                    f"Step {step.get('name')!r} syncs {target!r} from gh-pages "
                    "AFTER collection — overwrites fresh main-branch data."
                )


# ---------------------------------------------------------------------------
# 3e. Script import ↔ workflow ``pip install`` parity
# ---------------------------------------------------------------------------


class TestWorkflowPipInstallMatchesImports:
    """Every script a workflow invokes must have its third-party imports
    installed by the workflow's ``pip install`` step.

    This test walks every workflow's ``pip install`` line, parses every
    invoked script's top-level imports, and fails loudly if any third-party
    import lacks an installer.
    """

    # Map of import module name → pip distribution name. Only modules where
    # the two differ need an entry; identical names resolve automatically.
    IMPORT_TO_PIP = {
        "yaml": "pyyaml",
    }

    # Stdlib (rough allowlist — any module not in this set is assumed to
    # need pip installation). Scoped to the modules we actually use across
    # this repo's scripts to keep the list tight.
    STDLIB = frozenset(
        {
            "__future__",
            "abc",
            "argparse",
            "ast",
            "base64",
            "collections",
            "concurrent",
            "contextlib",
            "contextvars",
            "copy",
            "csv",
            "dataclasses",
            "datetime",
            "email",
            "enum",
            "functools",
            "glob",
            "gzip",
            "hashlib",
            "hmac",
            "html",
            "http",
            "io",
            "itertools",
            "json",
            "logging",
            "math",
            "operator",
            "os",
            "pathlib",
            "random",
            "re",
            "shutil",
            "socket",
            "ssl",
            "stat",
            "string",
            "subprocess",
            "sys",
            "tempfile",
            "textwrap",
            "threading",
            "time",
            "traceback",
            "types",
            "typing",
            "unittest",
            "urllib",
            "uuid",
            "unicodedata",
            "warnings",
            "xml",
            "zipfile",
            "statistics",
            "importlib",
        }
    )

    def _iter_workflow_pip_installs(self):
        """Yield (workflow_name, step_name, pip_packages_set, scripts_list).

        For each step that does ``pip install <pkgs>`` and a *subsequent*
        step that ``python scripts/...``, pair them up so we can verify
        the install covers the scripts actually invoked by the workflow.
        """
        for wf in WORKFLOWS.glob("*.yml"):
            data = yaml.safe_load(wf.read_text())
            jobs = data.get("jobs", {}) or {}
            for job_name, job in jobs.items():
                steps = job.get("steps", []) or []
                pip_pkgs: set[str] = set()
                pip_step_name = None
                scripts: list[tuple[str, str]] = []  # (script_rel_path, step_name)
                for step in steps:
                    run = step.get("run", "") or ""
                    # Accumulate every ``pip install`` we encounter.
                    for m in re.finditer(r"pip install\s+((?:[^\n&|<>;]|\s(?!\-))+)", run):
                        line = m.group(1).strip()
                        for tok in line.split():
                            if tok.startswith("-") or tok == "pip":
                                continue
                            # Strip version pins like ``requests==2.31``.
                            name = re.split(r"[<>=!~]", tok, maxsplit=1)[0]
                            if name:
                                pip_pkgs.add(name.lower())
                        if pip_step_name is None:
                            pip_step_name = step.get("name") or "install"
                    for m in re.finditer(r"python\s+(scripts/\S+\.py)", run):
                        scripts.append((m.group(1), step.get("name") or "?"))
                if scripts:
                    yield wf.name, pip_step_name, pip_pkgs, scripts

    def _third_party_imports(self, script_rel: str) -> set[str]:
        """Return the set of third-party top-level module names imported by
        ``script_rel``. Relative/local imports and stdlib are filtered out.
        """
        path = REPO_ROOT / script_rel
        if not path.exists():
            return set()
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            return set()
        out: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    out.add(root)
            elif isinstance(node, ast.ImportFrom):
                # Skip relative imports (from . import ...) and our own
                # ``vllm.*`` namespace (tests/vllm is on sys.path locally,
                # but in workflows scripts are invoked directly).
                if node.level and node.level > 0:
                    continue
                if node.module is None:
                    continue
                root = node.module.split(".")[0]
                out.add(root)
        # Drop stdlib + our own in-repo packages.
        out -= self.STDLIB
        out.discard("vllm")  # local package under scripts/vllm
        out.discard("collect")  # local sibling module
        # Also drop anything importable from the scripts/ tree directly.
        for top in list(out):
            candidate = SCRIPTS_DIR / f"{top}.py"
            candidate_dir = SCRIPTS_DIR / top
            if candidate.exists() or (candidate_dir / "__init__.py").exists():
                out.discard(top)
        return out

    def test_every_workflow_installs_scripts_imports(self):
        """If a workflow invokes a script, it must install every third-party
        package that script top-level imports — or rely on a preinstalled
        environment. We flag the case where a package is imported but there's
        no ``pip install`` covering it at all.
        """
        failures = []
        for wf_name, install_step, pkgs, scripts in self._iter_workflow_pip_installs():
            # Workflows that don't do any pip install at all are out of scope
            # (they either rely on preinstalled environments or use an action
            # that brings its own Python deps).
            if not pkgs:
                continue
            need: set[str] = set()
            for script_rel, _ in scripts:
                need |= self._third_party_imports(script_rel)
            # Map import names to pip names for comparison.
            need_pip = {self.IMPORT_TO_PIP.get(m, m).lower() for m in need}
            missing = need_pip - pkgs
            if missing:
                failures.append(
                    f"{wf_name}: step {install_step!r} installs {sorted(pkgs)} "
                    f"but {sorted(scripts, key=lambda t: t[0])} import "
                    f"{sorted(need)} — missing pip deps: {sorted(missing)}"
                )
        assert not failures, (
            "Workflow pip install steps do not cover script imports:\n  - "
            + "\n  - ".join(failures)
        )

class TestAlertAutomationWorkflow:
    """All state-owned alert watchers run after their authoritative collectors."""

    def test_alert_watchers_restore_state_and_run_after_collection(self):
        data = _load_workflow("hourly-master.yml")
        job = next(iter(data["jobs"].values()))
        steps = job.get("steps", []) or []
        names = [step.get("name") for step in steps]

        sync = next(step for step in steps if step.get("name") == "Sync issue automation state from gh-pages")
        sync_run = sync.get("run", "")
        assert "open_queue_issues.json" in sync_run
        assert "open_queue_zombie_issues.json" in sync_run
        assert "open_amd_main_failure_issues.json" not in sync_run
        assert "open_ci_main_failure_issues.json" not in sync_run
        assert "open_amd_duration_regression_issues.json" not in sync_run
        assert "open_agent_health_issues.json" not in sync_run

        amd_collect = names.index("Collect CI analytics")
        agent_collect = names.index("Collect AMD agent health (all builds, all branches)")
        amd_watch = names.index("Watch AMD main test-group failures (open/close issue)")
        ci_watch = names.index(
            "Watch upstream CI main test-group failures (open/close issue)"
        )
        duration_watch = names.index("Watch AMD main duration regressions (open/close issue)")
        agent_watch = names.index("Watch AMD CI agent health (open/close issue)")

        assert amd_watch > amd_collect
        assert ci_watch > amd_collect
        assert duration_watch > amd_collect
        assert agent_watch > agent_collect
        assert steps[amd_watch]["run"] == "python scripts/vllm/amd_main_failure_watcher.py"
        assert steps[ci_watch]["run"] == "python scripts/vllm/ci_main_failure_watcher.py"
        assert steps[duration_watch]["run"] == "python scripts/vllm/amd_duration_regression_watcher.py"
        assert steps[agent_watch]["run"] == "python scripts/vllm/agent_health_issue_watcher.py"
        for index in (amd_watch, ci_watch, duration_watch, agent_watch):
            env = steps[index].get("env") or {}
            assert {"GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"} <= set(env)

        persist = names.index("Stage managed alert issue state")
        assert persist > max(amd_watch, ci_watch, duration_watch, agent_watch)
        assert steps[persist].get("if") == (
            "inputs.dns_generation == '' && "
            "steps.publication-selector.outcome == 'success'"
        )
        persist_run = steps[persist].get("run", "")
        for state_file in (
            "open_amd_main_failure_issues.json",
            "open_ci_main_failure_issues.json",
            "open_amd_duration_regression_issues.json",
            "open_agent_health_issues.json",
        ):
            assert state_file in persist_run
            assert surface_for_path(f"data/vllm/ci/{state_file}") is None
        assert "git add" in persist_run
        assert "git commit" not in persist_run
        assert "git push" not in persist_run

    def test_alert_state_is_committed_with_collected_data(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "git add data/ dashboards/ README.md" in text

    def test_ranked_ci_area_watcher_runs_after_matrix_and_persists_before_rebuild(self):
        data = _load_workflow("hourly-master.yml")
        job = next(iter(data["jobs"].values()))
        steps = job.get("steps", []) or []
        names = [step.get("name") for step in steps]

        ensure_labels = names.index("Ensure CI Operations issue labels")
        first_issue_watcher = names.index("Watch queue latency (open/close issues)")
        matrix = names.index("Collect AMD test matrix")
        ownership_parity = names.index("Collect build-pinned CI ownership parity")
        selector = names.index("Select validated publication surfaces")
        watcher = names.index("Watch AMD CI test-area regressions (ranked owners)")
        project_sync = names.index("Sync managed issues to AMD CI Operations project")
        persist = names.index("Stage CI ownership issue state")
        second_build = names.index(
            "Rebuild v2 operations snapshot with selected issue state"
        )

        assert (
            ensure_labels
            < first_issue_watcher
            and
            matrix
            < ownership_parity
            < selector
            < ensure_labels
            < watcher
            < project_sync
            < persist
            < second_build
        )
        assert steps[ensure_labels]["run"] == (
            "python scripts/vllm/ensure_ci_operations_labels.py"
        )
        assert {"GITHUB_TOKEN", "GITHUB_REPOSITORY"} <= set(
            steps[ensure_labels].get("env") or {}
        )
        assert 'run_surface_collector ci_core "CI ownership parity"' in (
            steps[ownership_parity]["run"]
        )
        assert "python scripts/vllm/collect_ownership_parity.py" in (
            steps[ownership_parity]["run"]
        )
        assert "GITHUB_TOKEN" in (steps[ownership_parity].get("env") or {})
        assert steps[watcher]["run"] == "python scripts/vllm/ci_area_regression_watcher.py"
        assert {
            "GITHUB_TOKEN",
            "GITHUB_REPOSITORY",
            "GITHUB_RUN_ID",
        } <= set(steps[watcher].get("env") or {})
        assert "CI_OWNER_AVAILABILITY_JSON" not in (
            steps[watcher].get("env") or {}
        )
        assert "publication-selector.outcome == 'success'" in steps[persist].get(
            "if", ""
        )
        assert ",ci_core," in steps[persist].get("if", "")
        assert "open_ci_area_regression_issues.json" in steps[persist]["run"]
        assert (
            surface_for_path("data/vllm/ci/open_ci_area_regression_issues.json")
            is None
        )
        assert "git add" in steps[persist]["run"]
        assert "git commit" not in steps[persist]["run"]
        assert "git push" not in steps[persist]["run"]
        assert steps[project_sync]["run"] == (
            "python scripts/vllm/sync_ci_operations_project.py"
        )
        assert steps[project_sync].get("continue-on-error") is True
        assert {
            "GITHUB_TOKEN",
            "GITHUB_REPOSITORY",
            "PROJECTS_WRITE_TOKEN",
        } <= set(steps[project_sync].get("env") or {})
