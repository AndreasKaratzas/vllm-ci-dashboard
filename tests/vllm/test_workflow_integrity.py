"""Tests for GitHub Actions workflow YAML integrity, CI collect completeness,
framework isolation, and cron schedule safety.

These tests ensure:
- All workflow files are valid YAML with required fields
- ci-collect.yml calls all necessary collection scripts
- Deploying workflows sync vLLM CI data from gh-pages (prevents clobbering)
- No cron schedule conflicts between hourly workflows
"""

import ast
import re
from pathlib import Path

import pytest
import yaml

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
            else:
                group = conc
                cancel = None
            assert group == "gh-pages-deploy", (
                f"{f.name} writes to gh-pages but does not share the gh-pages-deploy "
                "concurrency group"
            )
            assert cancel is False, (
                f"{f.name} writes to gh-pages but still has cancel-in-progress enabled"
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
                if not any(step.get("name", "").startswith("Redeploy if corrupted") for step in steps):
                    continue

                validation = next(
                    (
                        step for step in steps
                        if step.get("name", "").startswith("Post-deploy validation")
                    ),
                    None,
                )
                assert validation and validation.get("id") == "post-deploy-validation", (
                    f"{f.name} has a corruption redeploy but no id on post-deploy validation"
                )

                redeploy = next(
                    step for step in steps
                    if step.get("name", "").startswith("Redeploy if corrupted")
                )
                condition = str(redeploy.get("if", ""))
                assert "steps.post-deploy-validation.outcome == 'failure'" in condition, (
                    f"{f.name} corruption redeploy must only run when post-deploy "
                    "validation itself fails"
                )
                assert "hashFiles('_site/index.html') != ''" in condition, (
                    f"{f.name} corruption redeploy must require an assembled site"
                )


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

    def test_calls_collect_analytics_script(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_analytics.py" in text

    def test_calls_collect_queue_snapshot(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_queue_snapshot.py" in text

    def test_calls_collect_group_changes(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_group_changes.py" in text

    def test_calls_collect_amd_test_matrix(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect_amd_test_matrix.py" in text

    def test_calls_github_data_collection(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "collect.py" in text, "hourly-master.yml must call collect.py"

    def test_github_freshness_watches_ready_ticket_snapshots(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "data/vllm/ci/ready_tickets.json" in text
        assert "data/vllm/ci/project_items.json" in text

    def test_runs_pytest(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "pytest" in text

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

    def test_syncs_ci_data_from_gh_pages(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "git fetch origin gh-pages" in text or "git show origin/gh-pages" in text

    def test_ready_tickets_sync_removed(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "sync_ready_tickets.py" not in text, (
            "hourly-master.yml must not invoke sync_ready_tickets.py while "
            "upstream project #39 automation is paused"
        )

    def test_test_failure_issue_assigns_and_mentions_repo_owner(self):
        text = _load_workflow_text("hourly-master.yml")
        assert "issues.addAssignees" in text
        assert "assignees: [context.repo.owner]" in text
        assert "cc @${context.repo.owner} for visibility." in text


class TestNoOrphanedCronSchedules:
    """Ensure only the approved collectors own recurring cron schedules."""

    def test_only_master_has_cron(self):
        # hourly-master.yml owns the frequent collection cadence, while the
        # ready-ticket sync is intentionally limited to the 3x/day master-
        # issue updater.
        allowed = {"hourly-master.yml", "ready-tickets-live.yml"}
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


class TestNightlyCIWorkflow:
    def test_nightly_failure_issue_assigns_and_mentions_repo_owner(self):
        text = _load_workflow_text("nightly-ci.yml")
        assert "issues.addAssignees" in text
        assert "assignees: [context.repo.owner]" in text
        assert "cc @${context.repo.owner} for visibility." in text


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
            # ci-collect.yml produces CI data itself, so it doesn't need to sync
            # pr-preview.yml deploys to a subdirectory (pr-preview/pr-N), not root
            if wf in ("ci-collect.yml", "pr-preview.yml"):
                continue
            text = _load_workflow_text(wf)
            assert "git fetch origin gh-pages" in text or "git show origin/gh-pages" in text, (
                f"{wf} deploys to gh-pages but does not sync CI data from gh-pages first. "
                "This will overwrite fresh CI data with stale copies from main."
            )

    def test_shard_bases_available_at_deploy(self):
        """shard_bases.json must be on the main branch (committed by hourly-master)
        so deploy workflows can include it in _site/. No gh-pages sync needed."""
        shard_path = Path(__file__).resolve().parent.parent.parent / "data" / "vllm" / "ci" / "shard_bases.json"
        assert shard_path.exists(), (
            "shard_bases.json not found on main branch. "
            "hourly-master should generate and commit it."
        )

    def test_ci_collect_only_writes_vllm_ci_data(self):
        """ci-collect.yml should only write to data/vllm/ci/."""
        text = _load_workflow_text("ci-collect.yml")
        # Find all 'git add' targets
        git_adds = re.findall(r"git add\s+(\S+)", text)
        for target in git_adds:
            assert "data/vllm/ci" in target, (
                f"ci-collect.yml has 'git add {target}' — expected only data/vllm/ci/"
            )

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
        """Hourly workflows should have at least 10 minutes between them."""
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
            "ci_health.json", "parity_report.json", "analytics.json",
            "shard_bases.json", "group_changes.json", "amd_test_matrix.json",
        ]
        for f in ci_files:
            # Match: > data/vllm/ci/<file>  (redirect/write to local file)
            write_pattern = _re.compile(r'>\s*data/vllm/ci/' + _re.escape(f))
            assert not write_pattern.search(wf_text), (
                f"deploy-pages.yml writes {f} from gh-pages to local, which "
                f"overwrites fresh main data with stale copies. Remove the sync."
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

        Other datasets sync after collection and that's correct — they
        have their own authoritative write paths:
          - ``queue_timeseries.jsonl``: appended by queue-monitor cron
          - ``test_builds/``: written by the browser via register_test_build
          - ``ready_tickets*.json``: written by sync_ready_tickets.py

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
            "ci_health.json", "parity_report.json", "config_parity.json",
            "flaky_tests.json", "failure_trends.json", "quarantine.json",
            "analytics.json", "shard_bases.json", "group_changes.json",
            "amd_test_matrix.json",
            "hotness.json", "open_queue_issues.json",
        }

        for step in steps[collect_idx + 1:]:
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
            for m in re.finditer(
                r"git show origin/gh-pages:data/vllm/ci/([^\s]+)", run
            ):
                target = m.group(1)
                # Allow sync into non-CI-analysis paths (test_builds/index.json,
                # ready_tickets*.json, queue_timeseries.jsonl, etc.).
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

    This pins the regression we hit on 2026-04-18: ``ready-tickets-live.yml``
    ran ``sync_ready_tickets.py`` which imports ``yaml``, but the workflow
    only ``pip install requests``. The live sync crashed with
    ``ModuleNotFoundError: No module named 'yaml'`` until pyyaml was added.
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
    STDLIB = frozenset({
        "__future__", "abc", "argparse", "ast", "base64", "collections",
        "concurrent", "contextlib", "copy", "csv", "dataclasses", "datetime",
        "email", "enum", "functools", "glob", "hashlib", "hmac", "html",
        "http", "io", "itertools", "json", "logging", "math",
        "operator", "os", "pathlib", "random", "re", "shutil",
        "socket", "ssl", "string", "subprocess", "sys", "tempfile",
        "textwrap", "time", "traceback", "types", "typing",
        "unittest", "urllib", "uuid", "warnings", "xml", "zipfile",
        "statistics", "importlib",
    })

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
                    for m in re.finditer(
                        r"pip install\s+((?:[^\n&|<>;]|\s(?!\-))+)", run
                    ):
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

    def test_ready_tickets_live_uses_explicit_allow_flag(self):
        wf = _load_workflow_text("ready-tickets-live.yml")
        assert "READY_TICKETS_ALLOW_UPSTREAM_WRITES" in wf, (
            "ready-tickets-live.yml must set the second explicit allow flag "
            "before sync_ready_tickets.py can touch upstream"
        )
        assert "sync master issue" in wf.lower(), (
            "ready-tickets-live.yml should describe the single-master-issue "
            "mode in its commit message or comments"
        )


class TestManualHourlyUpdateFreshness:
    """Validate the manual hourly update notices ready-ticket refreshes."""

    def test_daily_update_watches_ready_ticket_snapshots(self):
        text = _load_workflow_text("daily-update.yml")
        assert "data/vllm/ci/ready_tickets.json" in text
        assert "data/vllm/ci/project_items.json" in text
