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
import tomllib
from pathlib import Path, PurePosixPath

import pytest
import yaml

from vllm.publication_surfaces import surface_for_path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
SCRIPTS_DIR = REPO_ROOT / "scripts"

ACTION_PINS = {
    "actions/cache/restore": "55cc8345863c7cc4c66a329aec7e433d2d1c52a9",  # action revision
    "actions/cache/save": "55cc8345863c7cc4c66a329aec7e433d2d1c52a9",  # action revision
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",  # action revision
    "actions/github-script": "3a2844b7e9c422d3c10d287c895573f7108da1b3",  # action revision
    "actions/setup-node": "820762786026740c76f36085b0efc47a31fe5020",  # action revision
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",  # action revision
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",  # action revision
    "peaceiris/actions-gh-pages": "84c30a85c19949d7eee79c4ff27748b70285e453",  # action revision
}


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

    def test_all_workflows_reject_duplicate_mapping_keys(self):
        class UniqueKeyLoader(yaml.SafeLoader):
            pass

        def construct_unique_mapping(loader, node, deep=False):
            mapping = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                if key in mapping:
                    raise AssertionError(
                        f"duplicate YAML mapping key {key!r} at line "
                        f"{key_node.start_mark.line + 1}"
                    )
                mapping[key] = loader.construct_object(value_node, deep=deep)
            return mapping

        UniqueKeyLoader.add_constructor(
            yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
            construct_unique_mapping,
        )
        for workflow in WORKFLOWS.glob("*.yml"):
            try:
                yaml.load(workflow.read_text(), Loader=UniqueKeyLoader)
            except AssertionError as exc:
                raise AssertionError(f"{workflow.name}: {exc}") from exc

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
            for name, job in data["jobs"].items():
                assert job.get("runs-on") == "ubuntu-24.04", (
                    f"{f.name}:{name}: runner image must not float"
                )

    def test_isolated_dashboard_audits_are_limited_to_stdlib_safe_modes(self):
        isolated_entrypoint = "python -S scripts/vllm/audit_dashboard_data.py"
        safe_mode_flags = ("--dns-only", "--queue-lifecycle-only")
        isolated_commands = []

        for workflow_path in WORKFLOWS.glob("*.yml"):
            workflow = yaml.safe_load(workflow_path.read_text())
            for job_name, job in workflow["jobs"].items():
                for step in job.get("steps", []) or []:
                    script = str(step.get("run") or "")
                    logical_command = ""
                    for raw_line in script.splitlines():
                        line = raw_line.strip()
                        logical_command = f"{logical_command} {line}".strip()
                        if line.endswith("\\"):
                            logical_command = logical_command[:-1].rstrip()
                            continue
                        if isolated_entrypoint in logical_command:
                            isolated_commands.append(
                                (workflow_path.name, job_name, logical_command)
                            )
                        logical_command = ""

        assert isolated_commands, "expected focused isolated dashboard audits"
        for workflow_name, job_name, command in isolated_commands:
            selected_modes = [flag for flag in safe_mode_flags if flag in command]
            assert len(selected_modes) == 1, (
                f"{workflow_name}:{job_name}: python -S may run the dashboard audit "
                "only in one stdlib-safe focused mode; "
                f"found {selected_modes!r} in {command!r}"
            )

    def test_remote_actions_are_allowlisted_and_immutably_pinned(self):
        """A moving action tag must never change production code implicitly."""

        pattern = re.compile(r"^([^@]+)@([0-9a-f]{40})$")
        for workflow in WORKFLOWS.glob("*.yml"):
            for line_number, line in enumerate(workflow.read_text().splitlines(), 1):
                match = re.match(r"\s*(?:-\s*)?uses:\s*([^\s#]+)", line)
                if match is None:
                    continue
                reference = match.group(1)
                if reference.startswith("./"):
                    continue
                parsed = pattern.fullmatch(reference)
                assert parsed is not None, (
                    f"{workflow.name}:{line_number}: action reference must use a "
                    f"full commit SHA: {reference}"
                )
                action, revision = parsed.groups()
                assert action in ACTION_PINS, (
                    f"{workflow.name}:{line_number}: action is not allowlisted: {action}"
                )
                assert revision == ACTION_PINS[action], (
                    f"{workflow.name}:{line_number}: unexpected revision for {action}"
                )

    def test_every_uploaded_artifact_has_bounded_retention(self):
        """Diagnostic artifacts must not become an unbounded storage sink."""

        upload_action = (
            "actions/upload-artifact@" + ACTION_PINS["actions/upload-artifact"]
        )
        observed = 0
        for workflow in WORKFLOWS.glob("*.yml"):
            parsed = _load_workflow(workflow.name)
            for job_name, job in (parsed.get("jobs") or {}).items():
                for step in job.get("steps", []) or []:
                    if step.get("uses") != upload_action:
                        continue
                    observed += 1
                    retention = (step.get("with") or {}).get("retention-days")
                    assert (
                        isinstance(retention, int)
                        and not isinstance(retention, bool)
                        and 1 <= retention <= 30
                    ), (
                        f"{workflow.name}:{job_name}: uploaded artifacts need an "
                        "explicit 1-30 day retention"
                    )
        assert observed == 3

    def test_health_and_lifecycle_have_external_scheduler_wakeups(self):
        """An external tick can recover a delayed or dropped GitHub cron."""

        expected = {
            "health-check.yml": ["site_health_tick"],
            "queue-lifecycle.yml": ["queue_lifecycle_tick"],
        }
        for workflow_name, event_types in expected.items():
            workflow = _load_workflow(workflow_name)
            triggers = workflow.get(True, workflow.get("on", {}))
            assert triggers["repository_dispatch"] == {"types": event_types}

    def test_python_and_pip_installations_are_hermetic(self):
        for workflow in WORKFLOWS.glob("*.yml"):
            text = workflow.read_text()
            for version in re.findall(r"python-version:\s*['\"]?([^'\"\s]+)", text):
                assert version == "3.12.13", f"{workflow.name}: Python is not exact"
            for version in re.findall(r"node-version:\s*['\"]?([^'\"\s]+)", text):
                assert version == "22.23.2", f"{workflow.name}: Node.js is not exact"
            for line_number, line in enumerate(text.splitlines(), 1):
                if re.search(r"(?:python\s+-m\s+)?pip\s+install\b", line):
                    assert "-c constraints.txt" in line, (
                        f"{workflow.name}:{line_number}: pip install bypasses constraints"
                    )
                assert "npm install -g cspell" not in line, (
                    f"{workflow.name}:{line_number}: cspell bypasses the npm lock"
                )
            parsed = _load_workflow(workflow.name)
            for job in (parsed.get("jobs") or {}).values():
                for step in job.get("steps", []):
                    if step.get("uses") != (
                        "actions/setup-python@"
                        + ACTION_PINS["actions/setup-python"]
                    ):
                        continue
                    setup = step.get("with", {})
                    if setup.get("cache") != "pip":
                        continue
                    dependency_paths = setup.get("cache-dependency-path", "")
                    assert "constraints.txt" in dependency_paths, (
                        f"{workflow.name}: pip cache does not key on constraints.txt"
                    )

    def test_every_python_using_job_sets_up_the_exact_runtime(self):
        python_command = re.compile(r"(?<![-\w])python(?:3)?(?:\s|$)")
        for workflow in WORKFLOWS.glob("*.yml"):
            parsed = _load_workflow(workflow.name)
            for job_name, job in (parsed.get("jobs") or {}).items():
                steps = job.get("steps", []) or []
                commands = "\n".join(
                    str(step.get("run", ""))
                    for step in steps
                    if isinstance(step, dict)
                )
                if python_command.search(commands) is None:
                    continue
                setups = [
                    step
                    for step in steps
                    if step.get("uses")
                    == "actions/setup-python@" + ACTION_PINS["actions/setup-python"]
                ]
                assert setups, (
                    f"{workflow.name}:{job_name}: Python executes without setup-python"
                )
                assert all(
                    step.get("with", {}).get("python-version") == "3.12.13"
                    for step in setups
                ), f"{workflow.name}:{job_name}: Python runtime is not exact"

    def test_cspell_uses_the_committed_npm_lock(self):
        package_path = REPO_ROOT / "tools" / "spellcheck" / "package.json"
        lock_path = REPO_ROOT / "tools" / "spellcheck" / "package-lock.json"
        package = json.loads(package_path.read_text())
        lock = json.loads(lock_path.read_text())
        assert package["private"] is True
        assert package["devDependencies"] == {"cspell": "8.19.4"}
        assert lock["lockfileVersion"] == 3
        assert lock["packages"][""]["devDependencies"] == {"cspell": "8.19.4"}
        assert lock["packages"]["node_modules/cspell"]["version"] == "8.19.4"

        lint = _load_workflow("ci.yml")["jobs"]["lint"]
        commands = "\n".join(step.get("run", "") for step in lint["steps"])
        assert "npm ci --prefix tools/spellcheck" in commands
        assert "tools/spellcheck/node_modules/.bin/cspell" in commands
        assert "npm install -g" not in commands

    def test_setup_node_cache_policy_is_explicit(self):
        """Node 24 actions must never enable an accidental package cache."""

        setup_node = "actions/setup-node@" + ACTION_PINS["actions/setup-node"]
        observed = []
        for workflow in WORKFLOWS.glob("*.yml"):
            parsed = _load_workflow(workflow.name)
            for job_name, job in (parsed.get("jobs") or {}).items():
                for step in job.get("steps", []):
                    if step.get("uses") != setup_node:
                        continue
                    settings = step.get("with", {})
                    observed.append((workflow.name, job_name, settings))
                    if settings.get("cache") == "npm":
                        assert settings.get("cache-dependency-path") == (
                            "tools/spellcheck/package-lock.json"
                        )
                        continue
                    assert settings.get("package-manager-cache") is False, (
                        f"{workflow.name}:{job_name}: setup-node without an explicit "
                        "cache must disable automatic package-manager caching"
                    )

        assert len(observed) == 2

    def test_github_script_v9_uses_the_modern_octokit_surface(self):
        """Keep scripts compatible with the github-script v9 client contract."""

        github_script = (
            "actions/github-script@" + ACTION_PINS["actions/github-script"]
        )
        legacy_client = re.compile(
            r"\bgithub\.(?:actions|checks|issues|pulls|repos)\."
        )
        observed = 0
        for workflow in WORKFLOWS.glob("*.yml"):
            parsed = _load_workflow(workflow.name)
            for job_name, job in (parsed.get("jobs") or {}).items():
                for step in job.get("steps", []):
                    if step.get("uses") != github_script:
                        continue
                    observed += 1
                    script = step.get("with", {}).get("script")
                    assert isinstance(script, str) and script.strip(), (
                        f"{workflow.name}:{job_name}: github-script needs inline code"
                    )
                    assert legacy_client.search(script) is None, (
                        f"{workflow.name}:{job_name}: github-script must use github.rest"
                    )

        assert observed == 9

    def test_dependency_contracts_are_exact_and_covered_by_lock(self):
        constraint_lines = [
            line.strip()
            for line in (REPO_ROOT / "constraints.txt").read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        exact = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")
        locked = {}
        for line in constraint_lines:
            match = exact.fullmatch(line)
            assert match is not None, f"constraint is not an exact version: {line}"
            name, version = match.groups()
            normalized = name.lower().replace("_", "-")
            assert normalized not in locked, f"duplicate constraint for {name}"
            locked[normalized] = version

        project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        declared = list(project["project"]["dependencies"])
        for extra in project["project"].get("optional-dependencies", {}).values():
            declared.extend(extra)
        declared.extend(project["build-system"]["requires"])
        declared.extend(
            line.strip()
            for line in (REPO_ROOT / "requirements.txt").read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        for requirement in declared:
            match = exact.fullmatch(requirement)
            assert match is not None, f"dependency is not exactly pinned: {requirement}"
            name, version = match.groups()
            normalized = name.lower().replace("_", "-")
            assert locked.get(normalized) == version, (
                f"{requirement} is not covered exactly by constraints.txt"
            )

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
            concurrencies = [data.get("concurrency", {})]
            concurrencies.extend(
                job.get("concurrency", {})
                for job in (data.get("jobs") or {}).values()
                if isinstance(job, dict)
            )
            conc = next(
                (
                    candidate
                    for candidate in concurrencies
                    if (
                        candidate.get("group")
                        if isinstance(candidate, dict)
                        else candidate
                    )
                    == "gh-pages-deploy"
                ),
                {},
            )
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
            if f.name == "hourly-master.yml":
                assert queue == "max"
            else:
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
                if not any(step.get("id") == "corruption-redeploy" for step in steps):
                    continue

                validation = next(
                    (
                        step
                        for step in steps
                        if step.get("id") == "post-deploy-validation"
                    ),
                    None,
                )
                assert validation and validation.get("id") == "post-deploy-validation", (
                    f"{f.name} has a corruption redeploy but no id on post-deploy validation"
                )
                assert validation.get("continue-on-error") is True
                validation_script = validation.get("run", "")
                assert "scripts/vllm/public_projection.py verify-git" in (
                    validation_script
                )
                assert "--git-ref origin/gh-pages" in validation_script
                assert "--attestation" in validation_script
                assert "--expected-marker" in validation_script
                assert "github_git_proof.py hydrate-ref" in validation_script
                assert "--profile pages" in validation_script
                assert "GIT_NO_LAZY_FETCH=1 python" in validation_script
                assert "hydrate_status=$?" in validation_script
                assert '[ "$hydrate_status" -eq 2 ]' in validation_script
                assert (
                    'echo "corruption_confirmed=true" >> "$GITHUB_OUTPUT"'
                    in validation_script
                )
                assert "proof was ambiguous; refusing a destructive redeploy" in (
                    validation_script
                )
                assert validation.get("env") == {"GH_TOKEN": "${{ github.token }}"}

                redeploy = next(
                    step
                    for step in steps
                    if step.get("id") == "corruption-redeploy"
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
                assert (
                    "steps.post-deploy-validation.outputs."
                    "corruption_confirmed == 'true'" in condition
                ), f"{f.name} must never mutate Pages after an ambiguous proof failure"

                recovery = next(
                    step
                    for step in steps
                    if step.get("id") == "corruption-recovery-validation"
                )
                assert recovery.get("continue-on-error") is True
                recovery_condition = str(recovery.get("if", ""))
                assert "always()" in recovery_condition
                assert (
                    "steps.post-deploy-validation.outcome == 'failure'"
                    in recovery_condition
                )
                assert (
                    "steps.post-deploy-validation.outputs."
                    "corruption_confirmed == 'true'" in recovery_condition
                )
                assert (
                    "steps.corruption-redeploy.outcome == 'success'"
                    in recovery_condition
                )
                recovery_script = recovery.get("run", "")
                assert "scripts/vllm/public_projection.py verify-git" in recovery_script
                assert "--git-ref origin/gh-pages" in recovery_script
                assert "--attestation" in recovery_script
                assert "--expected-marker" in recovery_script
                assert "github_git_proof.py hydrate-ref" in recovery_script
                assert "--profile pages" in recovery_script
                assert "GIT_NO_LAZY_FETCH=1 python" in recovery_script
                assert recovery.get("env") == {"GH_TOKEN": "${{ github.token }}"}

                final = next(
                    step for step in steps if step.get("id") == "final-deploy-validation"
                )
                final_condition = str(final.get("if", ""))
                assert final_condition.startswith("always()")
                final_env = final.get("env", {})
                assert "steps.post-deploy-validation.outcome" in final_env[
                    "INITIAL_VALIDATION_OUTCOME"
                ]
                assert "outputs.corruption_confirmed" in final_env[
                    "INITIAL_CORRUPTION_CONFIRMED"
                ]
                assert "steps.corruption-redeploy.outcome" in final_env[
                    "REDEPLOY_OUTCOME"
                ]
                assert "steps.corruption-recovery-validation.outcome" in final_env[
                    "RECOVERY_VALIDATION_OUTCOME"
                ]
                final_script = final.get("run", "")
                assert '[ "$INITIAL_VALIDATION_OUTCOME" = success ]' in final_script
                assert '[ "$INITIAL_STATE_UNCHANGED" = true ]' in final_script
                assert '[ "$INITIAL_CORRUPTION_CONFIRMED" = true ]' in final_script
                assert '[ "$REDEPLOY_OUTCOME" = success ]' in final_script
                assert '[ "$RECOVERY_VALIDATION_OUTCOME" = success ]' in final_script
                assert "Pages did not reach a validated exact-state projection" in (
                    final_script
                )


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
        setup_python = next(
            step
            for step in steps
            if step.get("uses")
            == "actions/setup-python@" + ACTION_PINS["actions/setup-python"]
        )
        assert setup_python["with"]["python-version"] == "3.12.13"
        build_dependencies = steps[names.index("Install dashboard build dependencies")]
        assert build_dependencies["run"] == (
            "pip install -c constraints.txt requests pyyaml"
        )
        assert "Install browser smoke dependencies" in names
        assert "Install Chromium" in names
        assert "Run dashboard browser smoke" in names
        assert names.index("Install dashboard build dependencies") < names.index(
            "Run dashboard browser smoke"
        )

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


class TestNightlyCIFailureTransport:
    """Keep arbitrary pytest output out of generated JavaScript source."""

    def test_failure_evidence_is_bounded_single_line_base64(self):
        data = _load_workflow("nightly-ci.yml")
        test_job = data["jobs"]["tests"]
        assert set(test_job["outputs"]) == {
            "test_result",
            "test_output_b64",
            "summary_b64",
        }
        run_tests = next(
            step for step in test_job["steps"] if step.get("name") == "Run full test suite"
        )["run"]
        assert "base64.urlsafe_b64encode" in run_tests
        assert "scan_limit = 64 * 1024" in run_tests
        assert "output_limit = 16 * 1024" in run_tests
        assert "summary_limit = 1024" in run_tests
        assert "test_output_b64=" in run_tests
        assert "summary_b64=" in run_tests
        assert "output<<" not in run_tests

    def test_issue_script_decodes_only_environment_values(self):
        data = _load_workflow("nightly-ci.yml")
        steps = data["jobs"]["create-issue-on-failure"]["steps"]
        issue = next(
            step for step in steps if step.get("name") == "Create or update GitHub issue"
        )
        assert issue["env"] == {
            "SUMMARY_B64": "${{ needs.tests.outputs.summary_b64 }}",
            "TEST_OUTPUT_B64": "${{ needs.tests.outputs.test_output_b64 }}",
        }
        script = issue["with"]["script"]
        assert "${{" not in script
        assert "process.env.SUMMARY_B64" in script
        assert "process.env.TEST_OUTPUT_B64" in script
        assert "Buffer.from(encoded, 'base64url')" in script
        assert "decoded.length > maxBytes" in script


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
        assert inputs["queue_generation"]["default"] == ""
        assert inputs["watchdog_generation"]["default"] == ""
        assert inputs["recovery_key"]["default"] == ""
        assert "[recovery:{0}]" in workflow["run-name"]
        steps = workflow["jobs"]["collect-and-deploy"]["steps"]
        validate = next(
            step
            for step in steps
            if step.get("name") == "Validate and normalize workflow inputs"
        )
        assert validate["env"]["RAW_CI_DAYS"] == "${{ inputs.ci_days }}"
        assert 'raw_days = os.environ["RAW_CI_DAYS"] or "8"' in validate["run"]
        collect = next(step for step in steps if step.get("name") == "Collect CI data")
        assert 'DAYS="$HOURLY_CI_DAYS"' in collect["run"]

    def test_untrusted_dispatch_inputs_are_validated_before_shell_use(self):
        workflow = _load_workflow("hourly-master.yml")
        steps = workflow["jobs"]["collect-and-deploy"]["steps"]
        names = [step.get("name") for step in steps]
        validate = steps[names.index("Validate and normalize workflow inputs")]
        assert names.index(validate["name"]) < names.index("Capture immutable main code")
        assert set(validate["env"]) == {
            "RAW_CI_DAYS",
            "RAW_SKIP_TESTS",
            "RAW_RESET_PERF_EVAL",
            "RAW_DNS_GENERATION",
            "RAW_QUEUE_GENERATION",
            "RAW_WATCHDOG_GENERATION",
            "RAW_RECOVERY_KEY",
        }
        script = validate["run"]
        for token in (
            '1 <= days <= 30',
            '{"true", "false"}',
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',
            'unavailable=True',
            'HOURLY_CI_DAYS',
            'HOURLY_SKIP_TESTS',
            'HOURLY_RESET_PERF_EVAL',
            'HOURLY_DNS_GENERATION_INPUT',
            'HOURLY_QUEUE_GENERATION_INPUT',
            'HOURLY_WATCHDOG_GENERATION_INPUT',
            'HOURLY_RECOVERY_KEY',
            r'[0-9a-f]{64}',
            'targeted publication inputs are mutually exclusive',
        ):
            assert token in script
        unsafe = re.compile(r"\$\{\{\s*(?:inputs|github\.event\.inputs)\.")
        for step in steps:
            assert not unsafe.search(step.get("run", "")), step.get("name")

    def test_state_code_is_server_side_proven_on_protected_main(self):
        workflow = _load_workflow("hourly-master.yml")
        steps = workflow["jobs"]["collect-and-deploy"]["steps"]
        checkout = steps[0]
        assert checkout["with"].get("fetch-depth") != 0
        restore = next(
            step
            for step in steps
            if step.get("name") == "Restore validated dashboard state"
        )
        assert restore["env"] == {"GH_TOKEN": "${{ github.token }}"}
        script = restore["run"]
        assert "github_git_proof.py compare-ancestor" in script
        assert '--base "$code_sha"' in script
        assert '--head "$PUBLICATION_CODE_SHA"' in script
        assert 'declare -A PROVEN_CODES=()' in script
        assert 'declare -A PROVEN_CODE_TREES=()' in script
        assert 'PROVEN_CODES["$code_sha"]=ancestor' in script
        assert 'PROVEN_CODES["$code_sha"]=nonancestor' in script
        assert 'add_slot_proof "$OBSERVED_STATE_SHA" current' in script
        assert 'add_slot_proof "$OBSERVED_PREVIOUS_SHA" previous' in script
        assert "dashboard_state.py repair-slots" in script
        equality_shortcut = 'if [ "$code_sha" = "$PUBLICATION_CODE_SHA" ]'
        assert equality_shortcut in script
        assert script.index(equality_shortcut) < script.index(
            "github_git_proof.py compare-ancestor"
        )
        assert "hydrate_proven_blob" in script
        assert "--profile dashboard-state" in script
        assert "--profile dashboard-code" in script
        assert "state_manifest_bytes" in script
        assert "state_attestation_bytes" in script
        assert "--filter=blob:none" in script
        assert "dashboard_state.py validate-ref-metadata" in script
        assert "GIT_NO_LAZY_FETCH=1" in script
        pre_repair = script.split("dashboard_state.py repair-slots", 1)[0]
        assert "blob:limit=" not in pre_repair
        assert pre_repair.index("--profile dashboard-state") < pre_repair.index(
            'origin "$state_sha"'
        )
        post_repair = script.split("dashboard_state.py repair-slots", 1)[1]
        assert "--refetch" in post_repair
        assert 'full_validate_state "$CURRENT_STATE_SHA" current' in post_repair
        assert 'full_validate_state "$PREVIOUS_STATE_SHA" previous' in post_repair
        assert post_repair.index(
            'full_validate_state "$CURRENT_STATE_SHA" current'
        ) < post_repair.index('case "$CURRENT_FULL_STATUS" in') < post_repair.index(
            'full_validate_state "$PREVIOUS_STATE_SHA" previous'
        )
        assert "dashboard-state-full-fallback-ancestry.json" in post_repair
        assert "Neither dashboard state slot passes full content validation" in (
            post_repair
        )
        assert post_repair.index("--refetch") < post_repair.index(
            "dashboard_state.py validate-ref"
        )
        assert "git merge-base --is-ancestor" not in script

    def test_dns_reconciliation_is_generation_acknowledged_and_idempotent(self):
        workflow = _load_workflow("hourly-master.yml")
        collect = workflow["jobs"]["collect-and-deploy"]
        preflight = workflow["jobs"]["dns-reconcile-preflight"]
        watchdog_preflight = workflow["jobs"]["publication-watchdog-preflight"]
        cadence_preflight = workflow["jobs"]["cadence-preflight"]

        assert collect["needs"] == [
            "cadence-preflight",
            "dns-reconcile-preflight",
            "queue-reconcile-preflight",
            "publication-watchdog-preflight",
        ]
        assert collect["timeout-minutes"] == 50
        assert "always()" in collect["if"]
        assert "!cancelled()" in collect["if"]
        for conflict in (
            "inputs.dns_generation != '' && inputs.queue_generation != ''",
            "inputs.dns_generation != '' && inputs.watchdog_generation != ''",
            "inputs.queue_generation != '' && inputs.watchdog_generation != ''",
        ):
            assert conflict in collect["if"]
        assert "needs.dns-reconcile-preflight.result != 'success'" in collect["if"]
        assert "needs.dns-reconcile-preflight.outputs.required != 'false'" in (
            collect["if"]
        )
        assert "needs.publication-watchdog-preflight.result == 'success'" in collect["if"]
        assert "needs.publication-watchdog-preflight.outputs.required == 'true'" in (
            collect["if"]
        )
        assert "github.event_name == 'workflow_dispatch'" in collect["if"]
        assert "github.event_name == 'schedule'" in collect["if"]
        assert "github.event_name == 'repository_dispatch'" in collect["if"]
        assert "needs.cadence-preflight.result == 'success'" in collect["if"]
        assert "needs.cadence-preflight.outputs.observation_valid == 'true'" in (
            collect["if"]
        )
        assert "needs.cadence-preflight.outputs.required == 'true'" in collect["if"]
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
        assert "env" not in generation_check
        for token in (
            "request_bearing_attempt_budget.py",
            "config/data_collection_attempt_budget.json observe",
            '--github-output "$GITHUB_OUTPUT"',
        ):
            assert token in generation_check["run"]
        assert "publication_status" not in generation_check["run"]

        assert cadence_preflight["if"] == "github.event_name == 'repository_dispatch'"
        assert cadence_preflight["permissions"] == {"contents": "read"}
        assert cadence_preflight["outputs"]["observation_valid"] == (
            "${{ steps.cadence-check.outputs.observation_valid }}"
        )
        cadence_check = next(
            step
            for step in cadence_preflight["steps"]
            if step.get("id") == "cadence-check"
        )
        for token in (
            "request_bearing_attempt_budget.py",
            "config/data_collection_attempt_budget.json observe",
            '--github-output "$GITHUB_OUTPUT"',
        ):
            assert token in cadence_check["run"]
        assert "publication_status" not in cadence_check["run"]

        perf = next(
            step
            for step in collect["steps"]
            if step.get("name") == "Decide whether to regenerate perf-eval"
        )
        assert perf["env"] == {"DISPATCH_TYPE": "${{ github.event.action }}"}
        assert 'if [ -n "$HOURLY_DNS_GENERATION_INPUT" ]' in perf["run"]
        assert 'elif [ -n "$HOURLY_WATCHDOG_GENERATION_INPUT" ]' in perf["run"]
        assert 'if [ "$DISPATCH_TYPE" = "perf_eval_build_finished" ]' in perf["run"]

        confirmation = next(
            step
            for step in collect["steps"]
            if step.get("name") == "Confirm targeted DNS reconciliation"
        )
        assert confirmation["id"] == "dns-target-confirmation"
        assert "inputs.dns_generation != ''" in confirmation["if"]
        assert "steps.pages-deploy.outcome == 'success'" in confirmation["if"]
        assert "steps.final-deploy-validation.outcome == 'success'" in (
            confirmation["if"]
        )
        assert "env" not in confirmation
        for token in (
            "origin/gh-pages:data/vllm/ci/publication_status.json",
            "origin/gh-pages:data/vllm/ci/dns_failures.json",
            "audit_dashboard_data.py",
            '--dns-only --dns-path "$CANONICAL_DNS"',
            "--canonical-dns-data",
            "--target-dns-generation",
            '"$HOURLY_DNS_GENERATION_INPUT"',
            "--fail-if-required",
        ):
            assert token in confirmation["run"]

    def test_queue_reconciliation_is_single_surface_zero_request_and_exact(self):
        workflow = _load_workflow("hourly-master.yml")
        collect = workflow["jobs"]["collect-and-deploy"]
        preflight = workflow["jobs"]["queue-reconcile-preflight"]
        assert preflight["if"] == (
            "github.event_name == 'workflow_dispatch' && "
            "inputs.queue_generation != ''"
        )
        assert preflight["permissions"] == {"contents": "read"}
        target = next(
            step for step in preflight["steps"] if step.get("id") == "target-check"
        )
        assert target["env"] == {
            "TARGET_QUEUE_GENERATION": "${{ inputs.queue_generation }}"
        }
        for token in (
            "origin/gh-pages:data/vllm/ci/publication_status.json",
            "origin/gh-pages:data/vllm/ci/queue_jobs.json",
            "plan_queue_publication_reconcile.py",
            "--canonical-queue-data",
            "--target-queue-generation",
            '--github-output "$GITHUB_OUTPUT"',
        ):
            assert token in target["run"]

        steps = collect["steps"]
        names = [step.get("name") for step in steps]
        guard = steps[names.index("Install deny-all queue reconciliation request guard")]
        sync = steps[names.index("Sync queue data from durable live branch")]
        candidate = steps[names.index("Validate targeted queue candidate generation")]
        restore = steps[
            names.index("Restore baseline queue projections after targeted validation")
        ]
        lifecycle = steps[names.index("Sync validated queue lifecycle aggregate")]
        dns = steps[names.index("Sync validated DNS health aggregate")]
        reserve = steps[names.index("Reserve guarded Data Collection attempt")]
        selector = steps[names.index("Select validated publication surfaces")]
        report = steps[names.index("Confirm zero queue reconciliation Buildkite requests")]
        commit = steps[names.index("Publish validated dashboard state")]
        confirmation = steps[names.index("Confirm targeted queue reconciliation")]

        assert names.index(guard["name"]) < names.index(sync["name"])
        assert names.index(sync["name"]) < names.index(candidate["name"])
        assert (
            names.index(candidate["name"])
            < names.index(restore["name"])
            < names.index(selector["name"])
        )
        assert names.index(report["name"]) < names.index(commit["name"])
        assert guard["if"] == "inputs.queue_generation != ''"
        assert "buildkite_request_guard.py initialize" in guard["run"]
        assert "--allowance 0" in guard["run"]
        assert sync["id"] == "queue-data-sync"
        assert sync["if"] == "inputs.dns_generation == ''"
        assert 'if [ -n "$HOURLY_QUEUE_GENERATION_INPUT" ]' in sync["run"]
        assert 'echo "source_sha=$QUEUE_SOURCE_SHA"' in sync["run"]
        assert "git rev-parse --verify 'origin/queue-data^{commit}'" in sync["run"]
        for path in (
            "data/vllm/ci/operations_v2/queue.json",
            "data/vllm/ci/queue_history_chart.json",
            "data/vllm/ci/queue_jobs.json",
            "data/vllm/ci/queue_timeseries.jsonl",
        ):
            assert path in sync["run"]
        assert 'git ls-tree -r --name-only "$QUEUE_SOURCE_SHA"' in sync["run"]
        assert 'git show "$QUEUE_SOURCE_SHA:$QUEUE_PATH"' in sync["run"]
        assert sync["run"].index('git show "$QUEUE_SOURCE_SHA:$QUEUE_PATH"') < sync[
            "run"
        ].index('install -D -m 0644 "$QUEUE_STAGE_DIR/$QUEUE_PATH"')
        target_branch = sync["run"].index(
            'if [ -n "$HOURLY_QUEUE_GENERATION_INPUT" ]; then'
        )
        target_return = sync["run"].index("return 0", target_branch)
        routine_merge = sync["run"].index("--merge-history-git-ref origin/queue-data")
        assert target_branch < target_return < routine_merge
        assert candidate["if"] == "inputs.queue_generation != ''"
        assert (
            "python scripts/vllm/audit_dashboard_data.py --queue-only"
            in candidate["run"]
        )
        assert (
            "python -S scripts/vllm/audit_dashboard_data.py --queue-only"
            not in candidate["run"]
        )
        assert "candidate < target" in candidate["run"]
        assert "candidate queue_jobs metrics generation must equal" in candidate["run"]
        assert 'if "metrics_observed_at" in jobs' in candidate["run"]
        assert 'if "metrics_observed_at" in latest' in candidate["run"]
        assert restore["if"] == (
            "inputs.dns_generation == '' && inputs.queue_generation != ''"
        )
        for path in (
            "data/vllm/ci/operations_v2/queue.json",
            "data/vllm/ci/queue_history_chart.json",
        ):
            assert path in restore["run"]
        assert 'git show "$PUBLICATION_BASELINE_REF:$QUEUE_PATH"' in restore["run"]
        assert "queue_jobs.json" not in restore["run"]
        assert "queue_timeseries.jsonl" not in restore["run"]
        assert "inputs.queue_generation == ''" in lifecycle["if"]
        assert dns["if"] == "inputs.queue_generation == ''"
        assert "inputs.queue_generation == ''" in reserve["if"]
        assert "--refresh-only-surface queue" in selector["run"]
        assert "--refresh-only-surface dns_health" in selector["run"]
        assert report["if"] == "inputs.queue_generation != ''"
        assert "--allowance 0" in report["run"]

        for step in steps:
            if "BUILDKITE_TOKEN" in (step.get("env") or {}):
                assert "inputs.queue_generation == ''" in step.get("if", "")
        for required in (
            "Live publication audit",
            "Run test suite",
            "Enforce publication validation results",
            "Assemble site",
            "Verify exact local public projection",
            "Enforce final exact-state deployment validation",
        ):
            assert "inputs.queue_generation != ''" in steps[
                names.index(required)
            ].get("if", "")

        assert confirmation["id"] == "queue-target-confirmation"
        assert confirmation["env"] == {
            "EXPECTED_QUEUE_SOURCE_SHA": (
                "${{ steps.queue-data-sync.outputs.source_sha }}"
            )
        }
        for token in (
            "--canonical-queue-data",
            '"$HOURLY_QUEUE_GENERATION_INPUT"',
            "--fail-if-required",
            'state.get("source_refs", {}).get("queue-data")',
            "actual != expected",
        ):
            assert token in confirmation["run"]

        state_candidate = steps[names.index("Prepare bounded dashboard state candidate")]
        assert "import os" in state_candidate["run"]
        assert 'candidate_branches = ("queue-data",)' in state_candidate["run"]
        assert 'candidate_branches = ("dns-health-data",)' in state_candidate["run"]

    def test_queue_target_can_close_only_the_exact_queue_only_incident(self):
        workflow = _load_workflow("hourly-master.yml")
        steps = workflow["jobs"]["collect-and-deploy"]["steps"]
        by_name = {step.get("name"): step for step in steps}
        recovery = by_name["Establish publication recovery validation"]
        close = by_name["Close issue after healthy publication"]
        create = by_name["Create hourly validation incident"]
        recovery_script = recovery["with"]["script"]
        close_script = close["with"]["script"]
        create_script = create["with"]["script"]

        assert "steps.queue-target-confirmation.outcome == 'success'" in recovery[
            "if"
        ]
        assert "targeted-queue-tests-not-successful" in recovery_script
        assert "setValidation(true, 'targeted-queue'" in recovery_script
        assert "const targetedQueueRecovery = validationSource === 'targeted-queue'" in (
            close_script
        )
        assert "isMarkedQueueOnly" in close_script
        assert "isStrictLegacyQueueOnly" in close_script
        assert "isStrictLegacySurfaceOnlyIncident" in close_script
        assert "currentIssueBody, 'queue'" in close_script
        assert "targetedDnsRecovery || targetedQueueRecovery ? 1 : 6" in close_script
        assert "hourly-ci-queue-only:v1" in close_script
        assert "const isQueueOnlyIncident" in create_script
        assert "...(isQueueOnlyIncident ? [queueOnlyIncidentMarker] : [])" in (
            create_script
        )
        for script in (create_script, close_script):
            assert "github.paginate" not in script
            assert "automation:hourly-master" in script
            assert "github.rest.issues.getLabel" in script
            assert "github.rest.issues.createLabel" in script
            assert "state: 'all', labels: hourlyOwnerLabel" in script
            assert "labels: 'ci-failure,automated,workstream:dashboard-ci'" in script
            assert "sort: 'updated', direction: 'desc', per_page: 100, page: 1" in (
                script
            )
            assert "owner-label lookup is ambiguous" in script
            assert "migration lookup is ambiguous" in script
            assert "labels: [hourlyOwnerLabel]" in script
            assert "hasExactMarker" in script

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

    def test_state_publication_never_rebases_or_pushes_generated_data_to_main(self):
        data = _load_workflow("hourly-master.yml")
        steps = data["jobs"]["collect-and-deploy"].get("steps", [])
        names = [step.get("name") for step in steps]
        text = _load_workflow_text("hourly-master.yml")

        assert "git pull --rebase origin main" not in text
        assert "git push origin HEAD:main" not in text
        assert names.index("Validate private perf-eval event store") < names.index(
            "Prepare bounded dashboard state candidate"
        ) < names.index("Publish validated dashboard state")

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
        assert queue_fetch in script
        assert "Could not fetch the mandatory durable queue-data branch" in script
        assert "The mandatory durable queue-data ref did not resolve" in script
        assert "--merge-history-git-ref origin/queue-data" in script
        assert "--require-merge-history" in script
        assert script.index(queue_fetch) < script.index("return 1", script.index(queue_fetch))
        assert "origin/gh-pages" not in script
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

        restore_index = names.index("Restore validated dashboard state")
        collect_index = names.index("Collect vLLM/Omni AMD workload mappings")
        heuristic_index = names.index("Refresh Omni surge heuristic")
        selector = names.index("Select validated publication surfaces")
        second_build = names.index(
            "Rebuild v2 operations snapshot with selected issue state"
        )
        assert restore_index < collect_index < heuristic_index < selector < second_build
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

    def test_calls_collect_gating_targets(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_gating_targets.py" in text

    def test_hourly_rebuilds_gating_targets_after_live_data_sync(self):
        data = _load_workflow("hourly-master.yml")
        job = next(iter(data["jobs"].values()))
        steps = job.get("steps", []) or []
        names = [step.get("name") for step in steps]

        restore = names.index("Restore validated dashboard state")
        collect = names.index("Collect AMD gating target list")
        candidates = names.index("Collect AMD gating target candidate audit")

        assert restore < collect < candidates
        assert 'run_surface_collector ci_gating "AMD gating target list"' in (
            steps[collect]["run"]
        )
        assert "python scripts/vllm/collect_gating_targets.py" in steps[collect][
            "run"
        ]

    def test_calls_collect_gating_target_candidates(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_gating_target_candidates.py" in text

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
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_gating_proposals.py" in text
        assert "GITHUB_TOKEN" in text

    def test_ci_collect_calls_collect_gating_targets(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_gating_targets.py" in text

    def test_ci_collect_calls_collect_gating_target_candidates(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_gating_target_candidates.py" in text

    def test_ci_collect_validates_untrusted_collection_inputs(self):
        workflow = _load_workflow("ci-collect.yml")
        steps = workflow["jobs"]["collect"]["steps"]
        collect = next(
            step for step in steps if step.get("name") == "Validate compatibility inputs"
        )
        assert collect["env"]["RAW_DAYS"] == "${{ inputs.days }}"
        assert collect["env"]["RAW_PIPELINE"] == "${{ inputs.pipeline }}"
        assert "1 <= int(raw_days) <= 30" in collect["run"]
        assert '{"amd", "upstream", "both"}' in collect["run"]
        assert not re.search(
            r"\$\{\{\s*(?:inputs|github\.event\.inputs)\.", collect["run"]
        )

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

    def test_code_and_state_baselines_are_restored_before_collection(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        install = names.index("Install dependencies")
        baseline = names.index("Capture immutable main code")
        restore = names.index("Restore validated dashboard state")
        resolve = names.index("Resolve immutable vLLM config snapshot")

        assert install < baseline < restore < resolve
        assert steps[baseline].get("id") == "publication-baseline"
        script = steps[baseline]["run"]
        assert "git rev-parse --verify 'HEAD^{commit}'" in script
        assert "^[0-9a-f]{40}$" in script
        assert "git diff --quiet" in script
        assert "python scripts/vllm/audit_dashboard_data.py" not in script
        assert "PUBLICATION_CODE_SHA=$CODE_SHA" in script
        assert "PUBLICATION_FAILED_SURFACES_FILE=$FAILED_SURFACES_FILE" in script
        assert (
            "PUBLICATION_COLLECTOR_FAILURES_FILE=$COLLECTOR_FAILURES_FILE"
            in script
        )
        assert 'FAILED_SURFACES_FILE="$RUNNER_TEMP/' in script
        assert 'COLLECTOR_FAILURES_FILE="$RUNNER_TEMP/' in script
        restore_script = steps[restore]["run"]
        assert "git ls-remote --exit-code --refs" in restore_script
        assert "CURRENT_STATE_SHA" in restore_script
        assert "PREVIOUS_STATE_SHA" in restore_script
        assert "bootstrap_allowed" in restore_script
        assert "add_slot_proof" in restore_script
        assert "dashboard_state.py repair-slots" in restore_script
        assert restore_script.index(
            'add_slot_proof "$OBSERVED_STATE_SHA" current'
        ) < restore_script.index(
            'add_slot_proof "$OBSERVED_PREVIOUS_SHA" previous'
        ) < restore_script.index("dashboard_state.py repair-slots")
        assert '--current-sha "$OBSERVED_STATE_SHA"' in restore_script
        assert '--previous-sha "$OBSERVED_PREVIOUS_SHA"' in restore_script
        assert "--expected-code-sha" in restore_script
        assert "hydrate_proven_blob" in restore_script
        assert "github_git_proof.py prove" in restore_script
        assert "--profile dashboard-state" in restore_script
        assert "--profile dashboard-code" in restore_script
        assert "dashboard_state.py validate-ref-metadata" in restore_script
        assert "--filter=blob:none" in restore_script
        assert "--refetch" in restore_script
        assert "dashboard_state.py materialize" in restore_script
        assert "PUBLICATION_BASELINE_REF=$PUBLICATION_BASELINE_REF" in restore_script

    def test_external_collectors_force_atomic_surface_selection(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        baseline = steps[names.index("Capture immutable main code")]
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
        assert "--candidate-code-ref \"$PUBLICATION_CODE_SHA\"" in selector_run
        assert "--force-degraded-surfaces \"$FORCED_SURFACES\"" in selector_run
        assert (
            '--collector-failures-file "$PUBLICATION_COLLECTOR_FAILURES_FILE"'
            in selector_run
        )
        assert 'sort -u "$PUBLICATION_FAILED_SURFACES_FILE"' in selector_run
        assert selector.get("continue-on-error") is not True
        assert "Sync CI data from gh-pages" not in names
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
        baseline = steps[names.index("Capture immutable main code")]["run"]
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

        assert "Sync CI data from gh-pages" not in names
        assert "git show origin/gh-pages:data/vllm/ci/analytics.json" not in (
            _load_workflow_text("hourly-master.yml")
        )

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

        workflow_text = _load_workflow_text("hourly-master.yml")
        assert "git show origin/gh-pages:data/vllm/ci/analytics.json" not in workflow_text
        assert "Sync CI data from gh-pages" not in workflow_text

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
        assert restore["uses"] == (
            "actions/cache/restore@" + ACTION_PINS["actions/cache/restore"]
        )
        assert restore["with"]["path"] == cache_path
        assert "current_day_prefix" in restore["with"]["restore-keys"]
        assert "prior_day_prefix" in restore["with"]["restore-keys"]
        assert "namespace_prefix" in restore["with"]["restore-keys"]
        collect = steps[collect_index]
        assert collect["id"] == "collect-ci"
        assert '--github-output "$GITHUB_OUTPUT"' in collect["run"]
        assert 'echo "cache_save=true"' in collect["run"]
        save = steps[save_index]
        assert save["uses"] == (
            "actions/cache/save@" + ACTION_PINS["actions/cache/save"]
        )
        assert save["if"] == (
            "inputs.dns_generation == '' && "
            "inputs.queue_generation == '' && "
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
        assert master_restore["uses"] == (
            "actions/cache/restore@" + ACTION_PINS["actions/cache/restore"]
        )
        assert master_restore["continue-on-error"] is True
        assert master_restore["with"] == {
            "path": cache_path,
            "key": "${{ steps.dns-classification-cache-key.outputs.key }}",
            "restore-keys": (
                "${{ steps.dns-classification-cache-key.outputs.current_day_prefix }}\n"
                "${{ steps.dns-classification-cache-key.outputs.prior_day_prefix }}\n"
                "${{ steps.dns-classification-cache-key.outputs.namespace_prefix }}\n"
            ),
        }
        master_save = master_steps[save_index]
        assert master_save["uses"] == (
            "actions/cache/save@" + ACTION_PINS["actions/cache/save"]
        )
        assert master_save["if"] == (
            "inputs.dns_generation == '' && "
            "inputs.queue_generation == '' && "
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
        dns_key = dns_steps[dns_key_index]
        dns_restore = dns_steps[dns_restore_index]
        assert re.fullmatch(r"actions/cache/restore@[0-9a-f]{40}", dns_restore["uses"])
        assert dns_restore["with"]["path"] == cache_path
        assert dns_restore["with"]["restore-keys"] == (
            "${{ steps.dns-classification-cache-key.outputs.current_day_prefix }}\n"
            "${{ steps.dns-classification-cache-key.outputs.prior_day_prefix }}\n"
            "${{ steps.dns-classification-cache-key.outputs.namespace_prefix }}\n"
        )
        assert 'echo "namespace_prefix=$CACHE_NAMESPACE-"' in dns_key["run"]
        dns_collect = dns_steps[dns_collect_index]["run"]
        assert f"--classification-cache {cache_path}" in dns_collect
        assert not any(
            str(step.get("uses", "")).startswith("actions/cache/save@")
            for step in dns_steps
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
        assert 'echo "namespace_prefix=$CACHE_NAMESPACE-"' in key_script
        assert "github.run_id" in key_script
        assert "github.run_attempt" in key_script

        restore = steps[restore_index]
        assert restore["uses"] == (
            "actions/cache/restore@" + ACTION_PINS["actions/cache/restore"]
        )
        assert restore["continue-on-error"] is True
        assert restore["with"] == {
            "path": cache_path,
            "key": "${{ steps.analytics-cache-key.outputs.key }}",
            "restore-keys": (
                "${{ steps.analytics-cache-key.outputs.current_day_prefix }}\n"
                "${{ steps.analytics-cache-key.outputs.prior_day_prefix }}\n"
                "${{ steps.analytics-cache-key.outputs.namespace_prefix }}\n"
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
        assert save["uses"] == (
            "actions/cache/save@" + ACTION_PINS["actions/cache/save"]
        )
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
        candidate = steps[names.index("Prepare bounded dashboard state candidate")][
            "run"
        ]
        assert cache_path not in candidate
        assert not re.search(
            r"\bgit\s+add\b[^\n]*(?:\s-f\b|\s--force\b)", candidate
        )
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

    def test_hourly_refuses_to_publish_tracked_private_caches(self):
        workflow = _load_workflow("hourly-master.yml")
        steps = next(iter(workflow["jobs"].values())).get("steps", [])
        candidate = next(
            step
            for step in steps
            if step.get("name") == "Prepare bounded dashboard state candidate"
        )
        script = candidate["run"]

        guard = "git ls-files -- ':(glob)**/.cache/**'"
        assert guard in script
        assert script.count("assert_no_tracked_private_cache") == 2
        assert script.index("assert_no_tracked_private_cache\n") < script.index(
            "git add -A -- data/ dashboards/ README.md"
        )
        assert "git push" not in _load_workflow_text("hourly-master.yml")
        assert candidate["env"] == {
            "PUBLICATION_GENERATED_AT": (
                "${{ steps.publication-selector.outputs.generated_at }}"
            )
        }
        assert 'GENERATED_AT="$PUBLICATION_GENERATED_AT"' in script
        assert "GENERATED_AT=$(date" not in script

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
                assert "request_mode == 'reserved'" in step.get("if", "")

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
            "Create hourly validation incident",
        }
        assert issue_side_effect_steps <= set(names)
        for step in steps:
            if step.get("name") in issue_side_effect_steps:
                assert dns_absent in step.get("if", "")

        selector_index = names.index("Select validated publication surfaces")
        target_only_prefix = {
            "actions/checkout@" + ACTION_PINS["actions/checkout"],
            "actions/setup-python@" + ACTION_PINS["actions/setup-python"],
            "Install dependencies",
            "Validate and normalize workflow inputs",
            "Install deny-all queue reconciliation request guard",
            "Capture immutable main code",
            "Restore validated dashboard state",
            "Sync validated DNS health aggregate",
            "Validate targeted DNS candidate generation",
            "Validate targeted queue candidate generation",
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
        assert "inputs.dns_generation != ''" in run_tests.get("if", "")
        assert "github.event.inputs.skip_tests != 'true'" in run_tests.get("if", "")
        assert dns_absent not in run_tests.get("if", "")
        assert dns_absent in health.get("if", "")
        clock = steps[names.index("Advance canonical collector clock")]
        assert clock.get("if") == (
            "inputs.dns_generation == '' && "
            "inputs.queue_generation == '' && "
            "steps.request-attempt.outputs.request_mode == 'reserved'"
        )
        recovery = steps[names.index("Establish publication recovery validation")]
        close = steps[names.index("Close issue after healthy publication")]
        assert "steps.dns-target-confirmation.outcome == 'success'" in recovery["if"]
        assert "HOURLY_DNS_GENERATION_INPUT" in recovery["with"]["script"]
        assert "targeted-dns-tests-not-successful" in recovery["with"]["script"]
        assert "setValidation(true, 'targeted-dns'" in recovery["with"]["script"]
        assert "inputs.dns_generation == ''" not in close["if"]
        assert "targetedDnsRecovery || targetedQueueRecovery ? 1 : 6" in close[
            "with"
        ]["script"]
        assert "isStrictLegacyDnsOnly" in close["with"]["script"]
        assert "hourly-ci-dns-only:v1" in close["with"]["script"]
        create = steps[names.index("Create hourly validation incident")]
        assert "const isDnsOnlyIncident" in create["with"]["script"]
        assert "...(isDnsOnlyIncident ? [dnsOnlyIncidentMarker] : [])" in create[
            "with"
        ]["script"]

    def test_targeted_dns_path_keeps_all_validation_fail_closed(self):
        data = _load_workflow("hourly-master.yml")
        steps = data["jobs"]["collect-and-deploy"].get("steps", [])
        by_name = {step.get("name"): step for step in steps}

        audit = by_name["Live publication audit"]
        run_tests = by_name["Run test suite"]
        enforce = by_name["Enforce publication validation results"]
        assert "inputs.dns_generation == ''" not in audit.get("if", "")
        assert "inputs.dns_generation != ''" in run_tests.get("if", "")
        assert "github.event.inputs.skip_tests != 'true'" in run_tests.get("if", "")
        assert "steps.live-data-audit.outcome != 'success'" in enforce["if"]
        assert "steps.live-data-audit.outputs.exit_code != '0'" in enforce["if"]
        assert "inputs.dns_generation != ''" in enforce["if"]
        assert "steps.run-tests.outputs.exit_code != '0'" in enforce["if"]
        assert "Live publication audit failed" in enforce["run"]
        assert '[ -n "$HOURLY_DNS_GENERATION_INPUT" ]' in enforce["run"]
        assert "Deterministic dashboard tests failed" in enforce["run"]

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
        assert artifact.get("uses") == (
            "actions/upload-artifact@" + ACTION_PINS["actions/upload-artifact"]
        )
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
            "Publish validated dashboard state"
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
        assert "for (let attempt = 1; attempt <= 2; attempt += 1)" in close[
            "with"
        ]["script"]
        assert "github.rest.issues.get({" in close["with"]["script"]
        assert "transientWriteStatuses" in close["with"]["script"]
        assert "state: 'all', labels: hourlyOwnerLabel" in close["with"]["script"]
        assert "per_page: 100, page: 1" in close["with"]["script"]
        assert "owner-label lookup is ambiguous" in close["with"]["script"]
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
        assert "steps.final-deploy-validation.outcome == 'success'" in condition
        env = validation.get("env", {})
        assert "steps.publication-commit.outputs.published_sha" in env[
            "HOURLY_PUBLICATION_SHA"
        ]
        assert "steps.publication-commit.outputs.local_test_gap_safe" in env[
            "HOURLY_LOCAL_TEST_GAP_SAFE"
        ]
        assert "steps.run-tests.outcome" in env["HOURLY_TEST_OUTCOME"]
        assert "HOURLY_TESTS_SKIPPED" not in env

        script = validation["with"]["script"]
        assert "const localTestsPassed" in script
        assert "testOutcome === 'success' && testExitCode === '0'" in script
        assert "if (localTestsPassed && localTestGapSafe)" in script
        assert "setValidation(true, 'hourly-tests', publicationSha" in script
        assert "testsIntentionallySkipped" in script
        assert "process.env.HOURLY_SKIP_TESTS" in script
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
        assert "steps.final-deploy-validation.outcome == 'failure'" in condition
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
        assert "github.paginate" not in script
        assert "github.rest.issues.listForRepo({" in script
        assert "state: 'all', labels: hourlyOwnerLabel" in script
        assert "labels: 'ci-failure,automated,workstream:dashboard-ci'" in script
        assert "sort: 'updated', direction: 'desc', per_page: 100, page: 1" in script
        assert "exactOwnerPage.length >= 100" in script
        assert "migrationPage.length >= 100" in script
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
        assert "targetedDnsRecovery || targetedQueueRecovery ? 1 : 6" in close[
            "with"
        ]["script"]
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

        assert "targetedDnsRecovery || targetedQueueRecovery ? 1 : 6" in script
        assert "validationSource === 'targeted-dns'" in script
        assert "if (targetedDnsRecovery && !isMarkedDnsOnly" in script
        assert "required eligible healthy recovery runs" in script
        assert "github.paginate" not in script
        assert "github.rest.issues.listForRepo({" in script
        assert "labels: 'ci-failure,automated,workstream:dashboard-ci'" in script
        assert "sort: 'updated', direction: 'desc', per_page: 100, page: 1" in script
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
        assert "This single current ticket is now closed" in script
        assert "validationSource === 'separate-ci'" in script
        close_update = script.index("await github.rest.issues.update(closePayload)")
        close_comment = script.index("await github.rest.issues.createComment({")
        assert close_update < close_comment
        assert "retrying once" in script
        assert "readback.data.state !== 'closed'" in script
        assert "could not add" in script
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

    def test_final_state_publication_is_validated_atomic_and_main_read_only(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        candidate = next(
            step
            for step in steps
            if step.get("name") == "Create validated dashboard state candidate"
        )
        publish = next(
            step
            for step in steps
            if step.get("name") == "Publish validated dashboard state"
        )
        candidate_script = candidate.get("run", "")
        publish_script = publish.get("run", "")
        assert names.index("Prepare bounded dashboard state candidate") < names.index(
            "Live publication audit"
        ) < names.index("Run test suite") < names.index(
            "Enforce publication validation results"
        ) < names.index("Assemble site") < names.index(
            "Create validated dashboard state candidate"
        ) < names.index("Write state publication marker") < names.index(
            "Verify exact local public projection"
        ) < names.index("Read exact guarded Buildkite request total") < names.index(
            "Publish validated dashboard state"
        ) < names.index("Mark durable Data Collection success") < names.index(
            "Deploy to GitHub Pages"
        )
        assert "dashboard_state.py create-commit" in candidate_script
        assert '--code-sha "$PUBLICATION_CODE_SHA"' in candidate_script
        assert "dashboard_state.py validate-ref" in candidate_script
        assert "dashboard_state.py rotate" not in candidate_script
        assert "dashboard_state.py rotate" in publish_script
        assert '--new-state "$DASHBOARD_CANDIDATE_STATE_SHA"' in publish_script
        assert '--current-sha "$DASHBOARD_CURRENT_STATE_SHA"' in publish_script
        assert '--previous-sha "$DASHBOARD_PREVIOUS_STATE_SHA"' in publish_script
        assert "--remote origin" in publish_script
        assert 'echo "published_sha=$PUBLICATION_CODE_SHA"' in publish_script
        assert 'echo "local_test_gap_safe=true"' in publish_script

        text = _load_workflow_text("hourly-master.yml")
        assert "git pull --rebase origin main" not in text
        assert "git push origin HEAD:main" not in text
        assert 'git commit -m "auto: update data' not in text

    def test_every_direct_git_publisher_checks_staged_blob_budget(self):
        daily = _load_workflow("daily-update.yml")
        daily_steps = next(iter(daily["jobs"].values())).get("steps", [])
        daily_text = _load_workflow_text("daily-update.yml")
        assert [step.get("name") for step in daily_steps] == [
            "Dispatch canonical Data Collection"
        ]
        assert "hourly-master.yml/dispatches" in daily_text
        for forbidden in ("git add", "git commit", "git push", "contents: write"):
            assert forbidden not in daily_text

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

    def test_corruption_redeploy_never_overwrites_a_newer_state(self):
        for workflow_name in ("hourly-master.yml", "deploy-pages.yml"):
            workflow = _load_workflow(workflow_name)
            steps = next(iter(workflow["jobs"].values())).get("steps", [])
            by_name = {step.get("name"): step for step in steps}
            post_name = (
                "Post-deploy validation (check gh-pages for corruption)"
                if workflow_name == "hourly-master.yml"
                else "Post-deploy state and bundle validation"
            )
            redeploy_name = (
                "Redeploy if corrupted"
                if workflow_name == "hourly-master.yml"
                else "Redeploy exact state if validation failed"
            )
            post = by_name[post_name]
            redeploy = by_name[redeploy_name]
            assert 'echo "state_unchanged=true" >> "$GITHUB_OUTPUT"' in post["run"]
            assert "outputs.state_unchanged == 'true'" in redeploy["if"]
            assert post["run"].index("LIVE_STATE_SHA") < post["run"].index(
                'echo "state_unchanged=true"'
            )

        preview = _load_workflow("pr-preview.yml")
        trigger = preview.get("on") or preview.get(True)
        assert trigger["pull_request_target"]["types"] == [
            "opened",
            "synchronize",
            "reopened",
        ]
        assert set(preview["jobs"]) == {"deploy-preview"}
        assert preview["concurrency"] == {
            "group": "pr-preview-${{ github.event.pull_request.number }}",
            "cancel-in-progress": True,
        }
        assert preview["jobs"]["deploy-preview"]["concurrency"] == {
            "group": "gh-pages-deploy",
            "queue": "max",
            "cancel-in-progress": False,
        }

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
        assert (
            "github.event.pull_request.head.repo.full_name == github.repository"
            in deploy["if"]
        )
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
        compose = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Compose exact bounded Pages tree"
        )
        assert assemble < compose < publish
        assert steps[publish]["with"]["publish_dir"] == "./trusted-base/_pages_publish"
        assert steps[publish]["with"]["keep_files"] is False
        assert steps[publish]["with"]["force_orphan"] is True

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

    def test_has_recurring_cron(self):
        data = _load_workflow("hourly-master.yml")
        triggers = data.get(True, data.get("on", {}))
        schedules = triggers.get("schedule", []) if isinstance(triggers, dict) else []
        crons = [s.get("cron", "") for s in schedules]
        assert crons, f"hourly-master.yml must have a recurring cron, found: {crons}"

    def test_full_refresh_runs_once_every_two_hours(self):
        data = _load_workflow("hourly-master.yml")
        triggers = data.get(True, data.get("on", {}))
        schedules = triggers.get("schedule", []) if isinstance(triggers, dict) else []
        crons = [s.get("cron", "") for s in schedules]
        assert crons == ["13 */2 * * *"], (
            "The full refresh takes about 25 minutes and must not be queued "
            f"more than once every two hours; found {crons}"
        )

    def test_restores_ci_data_from_private_state_not_public_pages(self):
        workflow = _load_workflow("hourly-master.yml")
        steps = workflow["jobs"]["collect-and-deploy"]["steps"]
        names = [step.get("name") for step in steps]
        restore = steps[names.index("Restore validated dashboard state")]
        assert "dashboard_state.py materialize" in restore["run"]
        assert 'PUBLICATION_BASELINE_REF="$CURRENT_STATE_SHA"' in restore["run"]
        before_deploy = "\n".join(
            step.get("run", "") for step in steps[: names.index("Deploy to GitHub Pages")]
        )
        assert "origin/gh-pages:data/vllm/ci/" not in before_deploy

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
        assert 'line.startswith((b"FAILED ", b"ERROR "))' in text
        assert "failures[-16_000:]" in text
        assert "base64.b64encode" in text
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
            "scheduler-activity.yml",
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


class TestSchedulerActivityKeepalive:
    def test_keepalive_is_bounded_tokenless_and_main_only(self):
        workflow = _load_workflow("scheduler-activity.yml")
        triggers = workflow.get(True, workflow.get("on", {}))
        assert triggers["schedule"] == [{"cron": "23 4 * * 3"}]
        assert workflow["permissions"] == {"contents": "write"}
        job = workflow["jobs"]["keep-active"]
        assert job["timeout-minutes"] == 5
        steps = job["steps"]
        script = steps[-1]["run"]
        assert "THRESHOLD_SECONDS=$((30 * 24 * 60 * 60))" in script
        assert "refs/heads/main:refs/remotes/origin/main" in script
        assert ".github/scheduler-activity.txt" in script
        assert "check_git_blob_sizes.py" in script
        assert "git worktree add --detach" in script
        assert "push origin HEAD:main" in script
        assert "push --force" not in script
        assert "push -f" not in script
        assert "BUILDKITE" not in _load_workflow_text("scheduler-activity.yml")
        assert 'git -C "$HEARTBEAT_ROOT" add .github/scheduler-activity.txt' in script
        assert script.count("git -C \"$HEARTBEAT_ROOT\" add ") == 1


class TestPublicationWatchdogWorkflow:
    def _workflow(self):
        workflow = _load_workflow("publication-watchdog.yml")
        return workflow, workflow["jobs"]["recover"]["steps"]

    def test_has_redundant_trusted_triggers_and_minimal_permissions(self):
        workflow, _ = self._workflow()
        triggers = workflow.get(True, workflow.get("on", {}))
        assert triggers["schedule"] == [{"cron": "10,25,40,55 * * * *"}]
        assert triggers["repository_dispatch"] == {
            "types": ["publication_watchdog_tick"]
        }
        assert "workflow_dispatch" in triggers
        assert triggers["workflow_run"] == {
            "workflows": [
                "Data Collection",
                "Deploy to GitHub Pages",
                "Queue Lifecycle Monitor (2h)",
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
        assert job["timeout-minutes"] == 20
        assert job["permissions"] == {"actions": "write", "contents": "read"}

    def test_uses_trusted_main_and_bounded_canonical_state(self):
        _, steps = self._workflow()
        checkout = steps[0]
        assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"])
        assert checkout["with"] == {"ref": "main", "persist-credentials": False}
        names = [step.get("name") for step in steps]
        route = steps[names.index("Inspect durable state and choose recovery target")]
        reads = steps[names.index("Read selected recovery workflow state")]
        plan = steps[names.index("Plan deduplicated targeted recovery")]
        assert names.index(route["name"]) < names.index(reads["name"]) < names.index(
            plan["name"]
        )
        route_script = route["run"]
        assert "git ls-remote --exit-code --refs" in route_script
        assert "STATE_STATUS" in route_script and "PREVIOUS_STATUS" in route_script
        assert "validate_deployable_slot" in route_script
        assert "Neither dashboard state slot is deployable" in route_script
        assert 'force_reason=state-slot-repair' in route_script
        assert "Exactly one dashboard state slot is valid" in route_script
        assert 'validate_deployable_slot "$STATE_SHA" current' in route_script
        assert 'validate_deployable_slot "$PREVIOUS_SHA" previous' in route_script
        assert route_script.index(
            'validate_deployable_slot "$PREVIOUS_SHA" previous'
        ) < route_script.index('if [ "$CURRENT_VALIDATION" -eq 90 ]')
        assert "github_git_proof.py compare-ancestor" in route_script
        assert '--base "$code_sha"' in route_script
        assert '--head "$TRUSTED_MAIN_SHA"' in route_script
        assert "declare -A STATE_CODE_ANCESTRY=()" in route_script
        assert 'STATE_CODE_ANCESTRY["$code_sha"]=ancestor' in route_script
        assert 'STATE_CODE_ANCESTRY["$code_sha"]=nonancestor' in route_script
        equality_shortcut = 'if [ "$code_sha" = "$TRUSTED_MAIN_SHA" ]'
        assert equality_shortcut in route_script
        assert route_script.index(equality_shortcut) < route_script.index(
            "github_git_proof.py compare-ancestor"
        )
        assert "declare -A PROVEN_STATE_CODE_TREES=()" in route_script
        assert "Both state refs are absent and frozen-main bootstrap is disabled" in (
            route_script
        )
        assert "bootstrap_policy_active" in route_script
        assert "--write-bootstrap-ref-evidence" in route_script
        assert "--bootstrap-ref-evidence" in route_script
        assert '--repository "$GITHUB_REPOSITORY"' in route_script
        assert "dashboard_state.py validate-ref-metadata" in route_script
        assert "dashboard_state.py validate-ref \\" not in route_script
        assert "--expected-code-sha" in route_script
        assert "--filter=blob:none" in route_script
        assert "blob:limit=" not in route_script
        assert "hydrate_proven_blob" in route_script
        assert "--profile dashboard-state" in route_script
        assert "--profile dashboard-code" in route_script
        assert "state_manifest_bytes" in route_script
        assert "state_attestation_bytes" in route_script
        assert route_script.count("GIT_NO_LAZY_FETCH=1 git show") >= 2
        assert 'git fetch origin "${FETCH_REFS[@]}" --depth=1' not in route_script
        assert "git merge-base --is-ancestor" not in route_script
        validator_function = route_script.split("validate_deployable_slot()", 1)[1].split(
            "VALIDATED_STATE_CODE_SHA=", 1
        )[0]
        assert "set +e" not in validator_function
        assert 'if validate_deployable_slot "$STATE_SHA" current' in route_script
        assert 'if validate_deployable_slot "$PREVIOUS_SHA" previous' in route_script
        assert "dashboard_state.py write-public-marker" in route_script
        assert "--metadata-only" in route_script
        assert '--code-sha "$STATE_CODE_SHA"' in route_script
        assert "public_projection_attestation.json" in route_script
        assert '--public-attestation "$STATE_ATTESTATION"' in route_script
        assert "public_projection.py verify-git" in route_script
        assert '--git-ref origin/gh-pages' in route_script
        assert '--attestation "$STATE_ATTESTATION"' in route_script
        assert '--expected-marker "$EXPECTED_MARKER"' in route_script
        assert "--filter=blob:none" in route_script
        pages_segment = route_script.split('PAGES_PROOF="$RUNNER_TEMP/', 1)[1]
        assert pages_segment.index("--profile pages") < pages_segment.index(
            'origin "$PAGES_SHA"'
        )
        assert "pages_manifest_bytes" in pages_segment
        assert "pages_marker_bytes" in pages_segment
        assert "pages_status_bytes" in pages_segment
        assert "GIT_NO_LAZY_FETCH=1 git show" in pages_segment
        assert "GIT_NO_LAZY_FETCH=1 python" in pages_segment
        assert route_script.index('cmp -s "$EXPECTED_MARKER" "$DEPLOYED_MARKER"') < (
            route_script.index("public_projection.py verify-git")
        )
        assert 'target=deploy-pages' in route_script
        assert 'force_reason=state-pages-mismatch' in route_script
        assert "state_pages_mismatch_target" in route_script
        assert 'target=$MISMATCH_TARGET' in route_script
        assert 'target=collector' not in route_script.split(
            "route_state_pages_mismatch()", 1
        )[1].split("}", 1)[0]
        assert 'target=dns-health' in route_script
        assert 'force_reason=dns-only-degraded' in route_script
        assert 'force_reason=site-health-failed' in route_script
        assert 'github.event.workflow_run.name' in route_script
        assert '"Site Health Check"' in route_script
        assert 'github.event.workflow_run.conclusion' in route_script
        assert "scripts/vllm/check_site_health.py" in route_script
        assert "--max-publication-age-hours 3" in route_script
        assert 'report.get("overall_status") == "confirmed_unhealthy"' in (
            route_script
        )
        assert 'confirmation.get("strategy") == "2-of-3-quorum"' in route_script
        assert 'confirmation.get("attempted") == 3' in route_script
        assert 'confirmation.get("required_healthy") == 2' in route_script
        assert "refusing speculative deployment" in route_script
        assert route_script.index("public_projection.py verify-git") < (
            route_script.index("scripts/vllm/check_site_health.py")
        )
        assert "is_dns_only_degraded" in route_script
        assert "cmp -s \"$EXPECTED_MARKER\" \"$DEPLOYED_MARKER\"" in route_script
        assert "actions/workflows/$WORKFLOW_FILE/runs?per_page=100" in reads["run"]
        assert "plan_publication_watchdog.py" in plan["run"]
        assert "--workflow-runs" in plan["run"]
        assert '--recovery-target "$RECOVERY_TARGET"' in plan["run"]
        assert "--max-age-minutes 95" in plan["run"]
        assert "--retry-cooldown-minutes 15" in plan["run"]
        assert "--retry-cooldown-minutes 70" in plan["run"]
        assert "--force-recovery-reason" in plan["run"]
        assert "--active-run-max-age-minutes 75" in plan["run"]
        assert '--github-output "$GITHUB_OUTPUT"' in plan["run"]
        text = _load_workflow_text("publication-watchdog.yml")
        assert "github.event.workflow_run.head_sha" not in text
        assert "download-artifact" not in text
        assert "client_payload" not in text

    def test_dispatches_only_planned_fixed_target(self):
        _, steps = self._workflow()
        by_name = {step.get("name"): step for step in steps}
        expected = {
            "Dispatch exact-state Pages recovery": ("deploy-pages", "deploy-pages.yml"),
            "Dispatch DNS-only recovery": ("dns-health", "dns-health.yml"),
            "Dispatch stale collector recovery": ("collector", "hourly-master.yml"),
        }
        for name, (target, workflow_file) in expected.items():
            dispatch = by_name[name]
            assert "steps.recovery-plan.outputs.required == 'true'" in dispatch["if"]
            assert f"steps.recovery-route.outputs.target == '{target}'" in dispatch["if"]
            assert f"{workflow_file}/dispatches" in dispatch["run"]
            assert "main" in dispatch["run"]
            assert dispatch["env"]["RECOVERY_KEY"] == (
                "${{ steps.recovery-plan.outputs.recovery_key }}"
            )
            assert "recovery_key" in dispatch["run"]
        assert "watchdog_generation" not in by_name[
            "Dispatch exact-state Pages recovery"
        ]["run"]
        assert "watchdog_generation" not in by_name["Dispatch DNS-only recovery"][
            "run"
        ]
        assert "watchdog_generation" in by_name[
            "Dispatch stale collector recovery"
        ]["run"]

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

    def test_is_hourly_isolated_and_minimally_privileged(self):
        workflow, _ = self._workflow()
        triggers = workflow.get(True, workflow.get("on", {}))
        assert triggers["schedule"] == [{"cron": "37 * * * *"}]
        assert triggers["repository_dispatch"] == {"types": ["dns_health_tick"]}
        assert triggers["workflow_dispatch"] == {
            "inputs": {
                "recovery_key": {
                    "description": "Optional incident-scoped watchdog recovery key",
                    "required": False,
                    "type": "string",
                    "default": "",
                }
            }
        }
        assert workflow["run-name"] == (
            "DNS Health Monitor${{ inputs.recovery_key != '' && "
            "format(' [recovery:{0}]', inputs.recovery_key) || '' }}"
        )
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

        steps = workflow["jobs"]["collect"]["steps"]
        recovery_guard = steps[0]
        assert recovery_guard["name"] == "Validate optional recovery key"
        assert recovery_guard["env"] == {
            "RECOVERY_KEY": "${{ inputs.recovery_key || '' }}"
        }
        assert "^[0-9a-f]{64}$" in recovery_guard["run"]
        assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", steps[1]["uses"])

        reconcile = workflow["jobs"]["reconcile-publication"]
        assert reconcile["needs"] == "collect"
        assert reconcile["timeout-minutes"] == 5
        assert reconcile["permissions"] == {
            "actions": "write",
            "contents": "read",
        }

    def test_hourly_recovery_uses_a_durable_rolling_buildkite_budget(self):
        workflow, steps = self._workflow()
        triggers = workflow.get(True, workflow.get("on", {}))
        cron = triggers["schedule"][0]["cron"]
        assert cron.split() == ["37", "*", "*", "*", "*"]

        names = [step.get("name") for step in steps]
        reserve = steps[names.index("Reserve durable rolling DNS request budget")]
        collect = steps[names.index("Collect DNS failure observations")]["run"]
        argument_lines = {line.strip() for line in collect.splitlines()}
        max_requests = int(
            next(line for line in argument_lines if line.startswith("--max-requests "))
            .split()[1]
        )
        time_budget_seconds = int(
            next(
                line
                for line in argument_lines
                if line.startswith("--time-budget-seconds ")
            ).split()[1]
        )
        minimum_interval_hours = int(
            next(
                line
                for line in argument_lines
                if line.startswith("--minimum-interval-hours ")
            ).split()[1]
        )

        policy = json.loads((REPO_ROOT / "config/dns_request_budget.json").read_text())
        assert minimum_interval_hours == 3
        assert max_requests == 110
        assert time_budget_seconds == 1200
        assert policy["window_hours"] == 25
        assert policy["max_request_starts"] == 990
        assert policy["scan_reservation_request_starts"] == max_requests
        assert policy["branch"] == "dns-request-budget"
        assert reserve["id"] == "dns-request-budget"
        assert "BUILDKITE_TOKEN" not in reserve.get("env", {})
        assert reserve["env"] == {
            "ATTEMPT_ID": "dns-${{ github.run_id }}-${{ github.run_attempt }}"
        }
        assert "dns_request_budget.py reserve" in reserve["run"]
        assert '--reservation-id "$ATTEMPT_ID"' in reserve["run"]
        assert "buildkite_request_guard.py initialize" in reserve["run"]
        assert 'if [ "$REQUEST_MODE" = reserved ]' not in reserve["run"]
        assert 'echo "BUILDKITE_REQUEST_GUARD_FILE=$GUARD_FILE"' in reserve["run"]
        assert 'echo "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID=$ATTEMPT_ID"' in reserve["run"]
        assert 'echo "BUILDKITE_REQUEST_GUARD_ALLOWANCE=$ALLOWANCE"' in reserve["run"]
        assert '--now "${{ steps.dns-request-budget.outputs.decision_at }}"' in collect

    def test_restores_exact_state_collects_and_validates_before_publish(self):
        _, steps = self._workflow()
        names = [step.get("name") for step in steps]
        install = steps[names.index("Install dependencies")]["run"]
        preflight = steps[names.index("Preflight DNS-only validator")]["run"]
        restore_step = steps[names.index("Resolve durable DNS scanner state")]
        restore = restore_step["run"]
        reserve = steps[names.index("Reserve durable rolling DNS request budget")]
        collect = steps[names.index("Collect DNS failure observations")]
        report = steps[names.index("Read exact guarded Buildkite request total")]
        validate = steps[names.index("Validate bounded DNS artifacts")]["run"]
        generation_step = steps[names.index("Capture validated DNS generation")]
        encrypt_step = steps[names.index("Encrypt durable DNS scanner state")]
        encrypt = encrypt_step["run"]
        publish = steps[names.index("Publish durable DNS evidence")]["run"]

        assert names.index("Install dependencies") < names.index(
            "Preflight DNS-only validator"
        ) < names.index("Resolve durable DNS scanner state") < names.index(
            "Reserve durable rolling DNS request budget"
        ) < names.index(
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

        assert "BUILDKITE_TOKEN" not in reserve.get("env", {})
        assert "dns_request_budget.py reserve" in reserve["run"]
        assert "buildkite_request_guard.py initialize" in reserve["run"]

        assert collect.get("env", {}).get("BUILDKITE_TOKEN") == (
            "${{ secrets.BUILDKITE_TOKEN }}"
        )
        assert collect["id"] == "collect-dns"
        assert collect["if"] == (
            "steps.dns-request-budget.outputs.request_mode != 'capacity_gated'"
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
        assert (
            '--now "${{ steps.dns-request-budget.outputs.decision_at }}"'
            in argument_lines
        )

        assert "always()" in report["if"]
        assert "steps.dns-request-budget.outcome == 'success'" in report["if"]
        assert "buildkite_request_guard.py report" in report["run"]
        assert '--file "$BUILDKITE_REQUEST_GUARD_FILE"' in report["run"]
        assert '--attempt-id "$BUILDKITE_REQUEST_GUARD_ATTEMPT_ID"' in report["run"]
        assert '--allowance "$BUILDKITE_REQUEST_GUARD_ALLOWANCE"' in report["run"]
        assert "--time-budget-seconds 1200" in argument_lines
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
        assert reconcile["if"] == "needs.collect.outputs.dns_generation != ''"
        reconcile_steps = reconcile["steps"]
        reconcile_names = [step.get("name") for step in reconcile_steps]
        plan = reconcile_steps[
            reconcile_names.index("Plan canonical publication reconciliation")
        ]
        dispatch = reconcile_steps[
            reconcile_names.index("Dispatch canonical DNS reconciliation")
        ]
        assert re.fullmatch(
            r"actions/checkout@[0-9a-f]{40}", reconcile_steps[0]["uses"]
        )
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
        assert "origin/dns-health-data:data/vllm/ci/dns_failures.json" not in deploy
        assert "dashboard_state.py" in deploy and "materialize --ref" in deploy
        assert "dashboard_state.py validate-ref" in deploy
        assert "python scripts/vllm/audit_dashboard_data.py" in deploy
        assert "data/vllm/ci/dns_health/scan_state.json.gz" not in deploy
        assert "data/vllm/ci/dns_health/scan_state.fernet" not in deploy


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
            "Set up exact Python runtime",
            "Observe durable core collection freshness",
            "Run synthetic site health check",
            "Normalize bounded health evidence",
            "Upload bounded site health report",
            "Reconcile marker-owned site health issue",
            "Enforce synthetic health result",
        ]
        assert names == expected
        assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", steps[0]["uses"])
        assert steps[1]["uses"] == (
            "actions/setup-python@" + ACTION_PINS["actions/setup-python"]
        )
        assert steps[1]["with"]["python-version"] == "3.12.13"

        core_freshness = steps[names.index("Observe durable core collection freshness")]
        assert core_freshness["id"] == "core-freshness"
        assert core_freshness["continue-on-error"] is True
        assert "request_bearing_attempt_budget.py" in core_freshness["run"]
        assert "data_collection_attempt_budget.json observe" in core_freshness["run"]

        checker = steps[names.index("Run synthetic site health check")]
        assert checker["id"] == "synthetic-health"
        assert checker["continue-on-error"] is True
        for token in (
            "python scripts/vllm/check_site_health.py",
            "--site-url \"$SITE_URL\"",
            "--max-publication-age-hours 3",
            "--write-bootstrap-ref-evidence",
            "--bootstrap-ref-evidence",
            '--repository "$GITHUB_REPOSITORY"',
            "--output \"$REPORT_PATH\"",
            "--github-output \"$GITHUB_OUTPUT\"",
            "--markdown-output \"$DETAILS_PATH\"",
        ):
            assert token in checker["run"]

        normalize = steps[names.index("Normalize bounded health evidence")]
        assert normalize["if"] == "always()"
        assert normalize["id"] == "health-result"
        assert "max_report_bytes = 64 * 1024" in normalize["run"]
        assert "report_path.write_bytes(encoded)" in normalize["run"]
        assert "if not required[key].strip()" in normalize["run"]
        assert 'confirmation.get("max_requests") != 42' in normalize["run"]
        assert 'confirmation.get("max_transport_seconds") != 420' in normalize["run"]
        assert 'confirmation.get("max_elapsed_seconds") != 427' in normalize["run"]
        assert 'projection.get("operations_canaries") != [' in normalize["run"]
        for name in ("nightly", "amd_test_health", "diagnostics"):
            assert f'"name": "{name}"' in normalize["run"]
            assert f'operations_v2/{name}.json' in normalize["run"]
        assert "_legacy_bootstrap_allowed" in normalize["run"]

        bootstrap_policy = json.loads(
            (REPO_ROOT / "config/dashboard_bootstrap.json").read_text()
        )
        assert bootstrap_policy == {
            "schema_version": 1,
            "bootstrap_deadline": "2026-09-02T00:00:00Z",
        }

        upload = steps[names.index("Upload bounded site health report")]
        assert upload["if"] == "always()"
        assert re.fullmatch(r"actions/upload-artifact@[0-9a-f]{40}", upload["uses"])
        assert upload["with"]["path"] == "${{ runner.temp }}/site-health-report.json"
        assert upload["with"]["if-no-files-found"] == "error"
        assert upload["with"]["retention-days"] == 14

        reconcile = steps[names.index("Reconcile marker-owned site health issue")]
        enforce = steps[names.index("Enforce synthetic health result")]
        assert reconcile["if"] == "always()"
        assert re.fullmatch(r"actions/github-script@[0-9a-f]{40}", reconcile["uses"])
        assert enforce["if"] == "always()"
        assert "RECONCILE_OUTCOME" in enforce["env"]
        assert "RECONCILED" in enforce["env"]
        assert '[ "$HEALTHY" != "true" ]' in enforce["run"]
        assert "exit 1" in enforce["run"]

    def test_missing_or_malformed_checker_evidence_fails_closed(self):
        _, steps = self._steps()
        names = [step.get("name") for step in steps]
        normalize = steps[names.index("Normalize bounded health evidence")]
        checker = steps[names.index("Run synthetic site health check")]
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
        assert 'mandatory_output_keys = {' in script
        mandatory_block = script[
            script.index('mandatory_output_keys = {') : script.index(
                'missing = sorted(', script.index('mandatory_output_keys = {')
            )
        ]
        for nullable_diagnostic in (
            '"publication_mode"',
            '"publication_status"',
            '"generated_at"',
            '"age_hours"',
        ):
            assert nullable_diagnostic not in mandatory_block
        for quorum_field in (
            '"healthy"',
            '"overall_status"',
            '"publication_http"',
            '"confirmation_confirmed"',
            '"probe_attempts"',
            '"healthy_probe_count"',
            '"required_healthy_probes"',
        ):
            assert quorum_field in mandatory_block
        assert 'type(report.get("schema_version")) is not int' in script
        assert "report healthy disagreed with checker output" in script
        assert "report reason count disagreed with checker output" in script
        assert "report overall_status disagreed with checker output" in script
        assert '"healthy": False' in checker["run"]

    def test_normalizer_cross_checks_typed_report_fields_with_outputs(self):
        _, steps = self._steps()
        names = [step.get("name") for step in steps]
        script = steps[names.index("Normalize bounded health evidence")]["run"]
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
        names = [step.get("name") for step in steps]
        normalize = steps[names.index("Normalize bounded health evidence")]
        reconcile = steps[names.index("Reconcile marker-owned site health issue")]
        script = reconcile["with"]["script"]
        assert reconcile["env"]["BODY_PATH"].endswith("/site-health-issue.md")
        assert reconcile["env"]["OWNERSHIP_MARKER"] == (
            "<!-- vllm-ci-dashboard:site-health:v1 -->"
        )
        assert "fs.readFileSync(process.env.BODY_PATH" in script
        assert "github.rest.issues.getLabel" in script
        assert "if (error.status !== 404) throw error" in script
        assert "github.rest.issues.createLabel" in script
        assert "github.rest.issues.listForRepo" in script
        assert "state: 'all'" in script
        assert "github.paginate(" not in script
        assert "const labeledResponse" in script
        assert "const recentResponse" in script
        assert "labels: labelName" in script
        assert "per_page: 100" in script
        assert "page: 1" in script
        assert "bounded ambiguity limit" in script
        lookup = script[
            script.index("const labeledResponse") : script.index("const owned")
        ]
        assert lookup.count("github.rest.issues.listForRepo") == 2
        assert ".some(line => line.trim() === ownershipMarker)" in script
        assert "hasExactMarker(issue.body)" in script
        assert script.index("const labeledResponse") < script.index(
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

    def test_issue_requires_confirmed_in_run_quorum_before_mutation(self):
        _, steps = self._steps()
        names = [step.get("name") for step in steps]
        script = steps[names.index("Reconcile marker-owned site health issue")]["with"]["script"]
        for token in (
            "const confirmed = process.env.CONFIRMED === 'true'",
            "if (!confirmed)",
            "issue state is unchanged",
            "const state = healthy ? 'closed' : 'open'",
            "The in-run 2-of-3 healthy quorum confirmed recovery",
            "The in-run 2-of-3 quorum confirmed the dashboard health failure",
            "site-health-state:confirmed=true;healthy=${healthy};rearmed=${healthy}",
            "Confirmed healthy quorum with no owned incident",
            "const existing = owned[0] || null",
            "duplicate.number !== existing?.number",
        ):
            assert token in script
        assert "owned.find(issue => issue.state === 'open')" not in script
        assert "site-health-state:recovery=" not in script
        assert script.index("if (!confirmed)") < script.index(
            "github.rest.issues.getLabel"
        )
        normalize = steps[names.index("Normalize bounded health evidence")]
        assert "does not post hourly comments" in normalize["run"]

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
            assert "github.paginate" not in script
            assert "const ownershipLabel = 'automation:nightly-ci'" in script
            assert "github.rest.issues.getLabel" in script
            assert "github.rest.issues.createLabel" in script
            assert script.count("github.rest.issues.listForRepo") == 2
            assert "labels: ownershipLabel" in script
            assert "sort: 'created'" in script
            assert script.count("per_page: 100") == 2
            assert script.count("\n  page: 1,") == 2
            assert "bounded ambiguity limit" in script
            assert "const candidates = new Map()" in script
            assert "github.rest.issues.addLabels" in script
        assert "const existing = ownedIssues[0] || null" in create
        assert "ownedIssues.slice(1)" in create
        assert "Superseded by #${issue.number}" in create
        assert "github.rest.issues.addLabels" in create
        assert "ownershipLabel," in create
        assert "state: 'open'" in create
        assert "hasExactMarker" in create
        assert "hasExactMarker" in close
        assert "labels: [ownershipLabel]" in close
        assert "labels," in create
        assert "existing.data[0]" not in create
        assert "[...candidates.values()].filter" in close
        assert "if (issue.state !== 'open') continue" in close
        workflow = data["concurrency"]
        assert workflow["cancel-in-progress"] is False
        assert "if (hasNextPage(recentResponse))" in create
        assert "if (hasNextPage(recentResponse))" in close
        assert "!recentResponse.data.some(isOwned)" not in create
        assert "!recentResponse.data.some(isOwned)" not in close
        assert create.index("state: 'closed'") < create.index(
            "name: ownershipLabel,", create.index("state: 'closed'")
        )
        assert close.index("state: 'closed'") < close.index(
            "labels: [ownershipLabel]"
        )


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

    def test_root_deployers_publish_an_exact_dashboard_state_marker(self):
        for wf in self._deploying_workflows():
            # pr-preview.yml deploys to a subdirectory (pr-preview/pr-N), not root
            if wf == "pr-preview.yml":
                continue
            text = _load_workflow_text(wf)
            assert "dashboard_state.py write-public-marker" in text
            assert "publication_generation.json" in text
            assert (
                "--publication-status _site/data/vllm/ci/publication_status.json"
                in text
            )
            assert "git show origin/gh-pages:data/vllm/ci/analytics.json" not in text

    def test_every_full_marker_command_binds_the_assembled_publication_status(self):
        full = []
        metadata_only = []
        for workflow_path in WORKFLOWS.glob("*.yml"):
            workflow = _load_workflow(workflow_path.name)
            for job_name, job in (workflow.get("jobs") or {}).items():
                for step in job.get("steps", []):
                    script = str(step.get("run") or "")
                    if "write-public-marker" not in script:
                        continue
                    identity = (workflow_path.name, job_name, step.get("name"))
                    if "--metadata-only" in script:
                        metadata_only.append(identity)
                        continue
                    full.append(identity)
                    assert (
                        "--publication-status "
                        "_site/data/vllm/ci/publication_status.json"
                    ) in script

        assert len(full) == 4
        assert metadata_only == [
            (
                "publication-watchdog.yml",
                "recover",
                "Inspect durable state and choose recovery target",
            )
        ]

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
        assert "data/vllm/ci/operations_v2.json.gz" in command
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
        """The frozen bootstrap tree carries the initial shard-bases dataset."""
        shard_path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "vllm"
            / "ci"
            / "shard_bases.json"
        )
        assert shard_path.exists(), (
            "shard_bases.json not found in the frozen bootstrap tree."
        )

    def test_ci_collect_is_a_tokenless_canonical_dispatch_and_cannot_write_main(self):
        text = _load_workflow_text("ci-collect.yml")
        workflow = _load_workflow("ci-collect.yml")
        assert workflow.get("name") == "CI Data Collection (Canonical Dispatch)"
        assert workflow.get("permissions", {}).get("contents") == "read"
        assert workflow.get("permissions", {}).get("actions") == "write"
        assert workflow.get("concurrency", {}).get("group") == (
            "ci-collect-validation"
        )
        assert "Dispatch authoritative guarded collection" in text
        assert "hourly-master.yml/dispatches" in text
        assert "BUILDKITE_TOKEN" not in text
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
    """Ensure publication restores durable state instead of public feedback."""

    def test_manual_deploy_materializes_only_the_exact_validated_state(self):
        data = _load_workflow("deploy-pages.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        triggers = data.get(True, data.get("on", {}))
        assert triggers["workflow_dispatch"]["inputs"]["recovery_key"]["default"] == ""
        assert "[recovery:{0}]" in data["run-name"]
        recovery_validation = next(
            step for step in steps if step.get("name") == "Validate recovery key"
        )
        assert recovery_validation["env"] == {
            "RAW_RECOVERY_KEY": "${{ inputs.recovery_key }}"
        }
        assert "^[0-9a-f]{64}$" in recovery_validation["run"]
        script = next(
            step["run"]
            for step in steps
            if step.get("name") == "Restore exact validated dashboard state"
        )
        assert "git ls-remote --exit-code --refs" in script
        assert "dashboard_state.py validate-ref" in script
        assert "--expected-code-sha" in script
        assert "dashboard_state.py" in script and "materialize --ref" in script
        assert "dashboard_state.py repair-slots" in script
        assert '--current-sha "$OBSERVED_STATE_SHA"' in script
        assert '--previous-sha "$OBSERVED_PREVIOUS_SHA"' in script
        assert '--trusted-main-sha "$TRUSTED_MAIN_SHA"' in script
        assert '--ancestry-attestation "$ANCESTRY_ATTESTATION"' in script
        assert '"provider": "github_compare_api"' in script
        assert '"result": "ancestor"' in script
        assert "PROVEN_STATES" in script and "PROVEN_CODES" in script
        assert "--depth=1 --filter=blob:none" in script
        assert 'origin "$state_sha"' in script
        assert script.index("github_git_proof.py prove") < script.index(
            'origin "$state_sha"'
        )
        assert "Dashboard state has not been bootstrapped" in script
        assert "origin/queue-data" not in script
        assert "origin/gh-pages" not in script
        assert "github_git_proof.py compare-ancestor" in script
        assert '--base "$code_sha"' in script
        assert '--head "$TRUSTED_MAIN_SHA"' in script
        assert 'PROVEN_CODES["$code_sha"]=nonancestor' in script
        equality_shortcut = 'if [ "$code_sha" = "$TRUSTED_MAIN_SHA" ]'
        assert equality_shortcut in script
        assert script.index(equality_shortcut) < script.index(
            "github_git_proof.py compare-ancestor"
        )
        assert 'add_slot_proof "$OBSERVED_STATE_SHA" current' in script
        assert 'add_slot_proof "$OBSERVED_PREVIOUS_SHA" previous' in script
        assert "hydrate_proven_blob" in script
        assert "--profile dashboard-state" in script
        assert "--profile dashboard-code" in script
        assert "state_manifest_bytes" in script
        assert "state_attestation_bytes" in script
        assert "--filter=blob:none" in script
        assert "dashboard_state.py validate-ref-metadata" in script
        pre_repair = script.split("dashboard_state.py repair-slots", 1)[0]
        assert "blob:limit=" not in pre_repair
        assert pre_repair.index("--profile dashboard-state") < pre_repair.index(
            'origin "$state_sha"'
        )
        post_repair = script.split("dashboard_state.py repair-slots", 1)[1]
        assert "--refetch" in post_repair
        assert 'full_validate_state "$STATE_SHA" current' in post_repair
        assert 'full_validate_state "$PREVIOUS_SHA" previous' in post_repair
        assert post_repair.index(
            'full_validate_state "$STATE_SHA" current'
        ) < post_repair.index('case "$CURRENT_FULL_STATUS" in') < post_repair.index(
            'full_validate_state "$PREVIOUS_SHA" previous'
        )
        assert "dashboard-state-full-fallback-ancestry.json" in post_repair
        assert "Neither dashboard state slot passes full content validation" in (
            post_repair
        )
        assert post_repair.index("--refetch") < post_repair.index(
            "dashboard_state.py validate-ref"
        )
        assert 'GIT_NO_LAZY_FETCH=1 git checkout --detach "$STATE_SHA"' in script
        assert 'git checkout --detach "$STATE_CODE_SHA"' not in script
        assert "git merge-base --is-ancestor" not in script
        assert steps[0]["with"].get("fetch-depth") != 0

        names = [step.get("name") for step in steps]
        rebuild = steps[
            names.index("Rebuild deterministic private Operations assembly input")
        ]["run"]
        assert 'operations_v2_manifest.json' in rebuild
        assert 'get("generated_at")' in rebuild
        assert 'datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")' in rebuild
        assert '--generated-at "$OPS_GENERATED_AT"' in rebuild
        assert 'git diff --exit-code "$DASHBOARD_STATE_SHA"' in rebuild
        assert "git add -f" not in rebuild

    def test_queue_history_uses_durable_producer_then_retention(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        merge = steps[names.index("Sync queue data from durable live branch")]["run"]
        assert "--merge-history-git-ref origin/queue-data" in merge
        assert "--require-merge-history" in merge
        assert "origin/gh-pages" not in merge
        assert names.index("Sync queue data from durable live branch") < names.index(
            "Normalize and prune queue history"
        )
        deploy = _load_workflow_text("deploy-pages.yml")
        assert "collect_queue_snapshot.py" not in deploy

    def test_hourly_hotness_collection_follows_stale_data_sync(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        sync_idx = next(
            i
            for i, step in enumerate(steps)
            if step.get("name") == "Restore validated dashboard state"
        )
        hotness_idx = next(
            i
            for i, step in enumerate(steps)
            if step.get("name", "").startswith("Collect AMD hotness")
        )

        assert sync_idx < hotness_idx
        assert "git show origin/gh-pages:data/vllm/ci/hotness.json" not in (
            _load_workflow_text("hourly-master.yml")
        )

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
        """Deploy-only materializes private state and never seeds from Pages."""
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
                f"overwrites exact dashboard state with public copies. Remove the sync."
            )

    def test_manual_root_deploy_rebuilds_and_audits_before_assembly(self):
        data = _load_workflow("deploy-pages.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        assert (
            names.index("Rebuild deterministic private Operations assembly input")
            < names.index("Run dashboard data audit")
            < names.index("Assemble exact state site")
        )

    def test_hourly_restores_private_state_before_collection_without_pages_feedback(self):
        data = _load_workflow("hourly-master.yml")
        steps = next(iter(data["jobs"].values())).get("steps", [])
        names = [step.get("name") for step in steps]
        assert names.index("Restore validated dashboard state") < names.index(
            "Collect CI data"
        )
        assert "Sync CI data from gh-pages" not in names

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
                    "the selected state candidate with stale public copies."
                )
            # Direct references (no loop): check the literal path.
            for m in re.finditer(r"git show origin/gh-pages:data/vllm/ci/([^\s]+)", run):
                target = m.group(1)
                # Allow sync into non-CI-analysis paths such as queue history.
                basename = Path(target).name
                assert basename not in CI_ANALYSIS_FILES, (
                    f"Step {step.get('name')!r} syncs {target!r} from gh-pages "
                    "AFTER collection — overwrites the selected state candidate."
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
            "fcntl",
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

        restore = names.index("Restore validated dashboard state")
        restore_run = steps[restore].get("run", "")
        assert "dashboard_state.py materialize" in restore_run
        assert "origin/gh-pages" not in restore_run

        amd_collect = names.index("Collect CI analytics")
        agent_collect = names.index("Collect AMD agent health (all builds, all branches)")
        amd_watch = names.index("Watch AMD main test-group failures (open/close issue)")
        ci_watch = names.index(
            "Watch upstream CI main test-group failures (open/close issue)"
        )
        duration_watch = names.index("Watch AMD main duration regressions (open/close issue)")
        agent_watch = names.index("Watch AMD CI agent health (open/close issue)")

        assert restore < min(amd_collect, agent_collect)
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
            "inputs.queue_generation == '' && "
            "steps.request-attempt.outputs.request_mode == 'reserved' && "
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

    def test_alert_state_is_published_with_collected_state(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "git add -A -- data/ dashboards/ README.md" in text
        assert "dashboard_state.py prepare" in text
        assert "dashboard_state.py create-commit" in text
        assert "git push origin HEAD:main" not in text

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
