from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False):
    value = {}
    for key_node, child_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in value:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        value[key] = loader.construct_object(child_node, deep=deep)
    return value


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _unique_mapping,
)


def load(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=UniqueKeyLoader)


def test_every_workflow_rejects_duplicate_yaml_keys() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
        assert isinstance(value, dict), path


def test_every_buildkite_token_step_has_one_initialized_local_transport_guard() -> None:
    token_names = {"BUILDKITE_TOKEN", "BUILDKITE_API_TOKEN"}
    guarded_workflows: set[str] = set()

    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = load(path.name)
        assert not token_names.intersection(workflow.get("env") or {}), path
        for job_name, job in (workflow.get("jobs") or {}).items():
            job_env = job.get("env") or {}
            assert not token_names.intersection(job_env), (path, job_name)
            steps = job.get("steps") or []
            token_indexes = [
                index
                for index, step in enumerate(steps)
                if token_names.intersection(step.get("env") or {})
            ]
            if not token_indexes:
                continue

            guarded_workflows.add(path.name)
            assert job_env.get("PYTHONPATH") == "${{ github.workspace }}/scripts"
            first_token = min(token_indexes)
            last_token = max(token_indexes)
            before_token = "\n".join(
                str(step.get("run") or "") for step in steps[:first_token]
            )
            after_token = "\n".join(
                str(step.get("run") or "") for step in steps[last_token + 1 :]
            )
            assert any(
                name in before_token
                for name in (
                    "request_bearing_attempt_budget.py",
                    "dns_request_budget.py",
                    "queue_request_budget.py",
                )
            )
            assert "buildkite_request_guard.py initialize" in before_token
            assert "buildkite_request_guard.py report" in after_token
            for index in token_indexes:
                assert "python -S" not in str(steps[index].get("run") or "")

    assert guarded_workflows == {
        "dns-health.yml",
        "hourly-master.yml",
        "queue-lifecycle.yml",
        "queue-monitor.yml",
    }


def test_data_collection_serializes_before_a_failure_surviving_reservation() -> None:
    workflow = load("hourly-master.yml")
    job = workflow["jobs"]["collect-and-deploy"]
    workflow_concurrency = workflow["concurrency"]
    assert workflow_concurrency["cancel-in-progress"] is False
    workflow_group = workflow_concurrency["group"]
    assert "data-collection-routine" in workflow_group
    assert "data-collection-dns-recovery" in workflow_group
    assert "data-collection-queue-recovery" in workflow_group
    assert "data-collection-watchdog-recovery" in workflow_group
    assert "inputs.dns_generation != ''" in workflow_group
    assert "inputs.queue_generation != ''" in workflow_group
    assert "inputs.watchdog_generation != ''" in workflow_group
    assert job["concurrency"] == {
        "group": "gh-pages-deploy",
        "queue": "max",
        "cancel-in-progress": False,
    }
    # The workflow-level groups keep at most one pending wakeup per routine/DNS/
    # watchdog class, while the shared writer queue guarantees that a survivor
    # cannot replace another recovery already awaiting the Pages lock.
    assert job["timeout-minutes"] <= 50
    assert job["env"]["PYTHONPATH"] == "${{ github.workspace }}/scripts"

    steps = job["steps"]
    reserve_index = next(
        index for index, step in enumerate(steps) if step.get("id") == "request-attempt"
    )
    reserve = steps[reserve_index]
    assert "BUILDKITE_TOKEN" not in reserve.get("env", {})
    assert "request_bearing_attempt_budget.py" in reserve["run"]
    assert "buildkite_request_guard.py initialize" in reserve["run"]

    token_steps = [
        (index, step)
        for index, step in enumerate(steps)
        if "BUILDKITE_TOKEN" in (step.get("env") or {})
    ]
    assert token_steps
    assert token_steps[0][0] == reserve_index + 2  # gated report is skipped on permits
    for index, step in token_steps:
        assert index > reserve_index
        assert "steps.request-attempt.outputs.request_mode == 'reserved'" in str(
            step.get("if", "")
        )
        assert "inputs.queue_generation == ''" in str(step.get("if", ""))
    assert "inputs.queue_generation == ''" in reserve["if"]
    amd_matrix = next(step for step in steps if step.get("name") == "Collect AMD test matrix")
    assert "BUILDKITE_TOKEN" not in (amd_matrix.get("env") or {})

    tests = next(step for step in steps if step.get("name") == "Run test suite")
    assert "BUILDKITE_TOKEN" not in tests.get("env", {})
    assert "BUILDKITE_API_TOKEN" not in tests.get("env", {})
    for name in (
        "BUILDKITE_TOKEN",
        "BUILDKITE_API_TOKEN",
        "BUILDKITE_REQUEST_GUARD_FILE",
        "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID",
        "BUILDKITE_REQUEST_GUARD_ALLOWANCE",
    ):
        assert f"-u {name}" in tests["run"]

    request_report = next(
        step
        for step in steps
        if step.get("name") == "Read exact guarded Buildkite request total"
    )
    assert "always()" in request_report["if"]
    assert "request_mode == 'reserved'" in request_report["if"]
    assert "BUILDKITE_TOKEN" not in request_report.get("env", {})
    assert "buildkite_request_guard.py report" in request_report["run"]
    assert "Exact guarded Buildkite request starts:" in request_report["run"]
    assert '>> "$GITHUB_STEP_SUMMARY"' in request_report["run"]
    request_report_index = steps.index(request_report)
    assert all(index < request_report_index for index, _step in token_steps)

    workflow_text = (WORKFLOWS / "hourly-master.yml").read_text(encoding="utf-8")
    accounting_comment = "\n".join(
        (
            "      # `always()` reports the exact runner-local count after ordinary success or",
            "      # failure. Explicit workflow cancellation can stop the runner before this",
            "      # step, so the durable ledger retains the full reserved allowance for 25",
            "      # hours without claiming an exact cancellation count.",
        )
    )
    assert (
        f"{accounting_comment}\n"
        "      - name: Read exact guarded Buildkite request total"
    ) in workflow_text
    ledger_source = (
        ROOT / "scripts/vllm/request_bearing_attempt_budget.py"
    ).read_text(encoding="utf-8")
    assert "Reservations survive\nfailure and cancellation for 25 hours" in ledger_source
    assert "sum(\n            policy.request_start_allowance" in ledger_source


def test_gated_wakeups_cannot_publish_or_advance_buildkite_clock() -> None:
    workflow = load("hourly-master.yml")
    steps = workflow["jobs"]["collect-and-deploy"]["steps"]
    selector_index = next(
        index for index, step in enumerate(steps) if step.get("id") == "publication-selector"
    )
    for step in steps[selector_index:]:
        condition = str(step.get("if", ""))
        assert (
            "request-attempt" in condition
            or "dns_generation" in condition
            or "queue_generation" in condition
        ), step.get("name")

    clock = next(
        step for step in steps if step.get("name") == "Advance canonical collector clock"
    )
    assert "request_mode == 'reserved'" in clock["if"]
    assert "dns_generation == ''" in clock["if"]
    assert "queue_generation == ''" in clock["if"]
    assert "success_gated" not in workflow["jobs"]["collect-and-deploy"].get("if", "")
    text = (WORKFLOWS / "hourly-master.yml").read_text(encoding="utf-8")
    assert "Data collection made zero Buildkite requests" in text
    assert "success_gated deploy" not in text.casefold()


def test_candidate_is_locally_bound_before_rotation_and_success_accounting() -> None:
    workflow = load("hourly-master.yml")
    steps = workflow["jobs"]["collect-and-deploy"]["steps"]
    names = [step.get("name") for step in steps]
    candidate = names.index("Create validated dashboard state candidate")
    marker = names.index("Write state publication marker")
    projection = names.index("Verify exact local public projection")
    rotation = names.index("Publish validated dashboard state")
    report = names.index("Read exact guarded Buildkite request total")
    queue_report = names.index("Confirm zero queue reconciliation Buildkite requests")
    success = names.index("Mark durable Data Collection success")
    pages = names.index("Preserve only bounded PR previews")
    assert candidate < marker < projection < report < rotation < success < pages
    assert candidate < marker < projection < queue_report < rotation
    assert "steps.publication-commit.outputs.state_sha" in steps[success]["if"]
    assert '--durable-ref "$DURABLE_STATE_SHA"' in steps[success]["run"]


def test_schedules_and_manual_runs_reach_reserve_independent_of_publication_age() -> None:
    workflow = load("hourly-master.yml")
    job_condition = workflow["jobs"]["collect-and-deploy"]["if"]
    assert "github.event_name == 'workflow_dispatch' ||" in job_condition
    assert "github.event_name == 'schedule' ||" in job_condition
    cadence = workflow["jobs"]["cadence-preflight"]
    assert cadence["if"] == "github.event_name == 'repository_dispatch'"
    cadence_run = cadence["steps"][-1]["run"]
    assert "request_bearing_attempt_budget.py" in cadence_run
    assert " observe " in cadence_run
    assert "publication_status" not in cadence_run


def test_watchdog_uses_attempt_success_freshness_not_publication_generated_at() -> None:
    hourly = load("hourly-master.yml")
    preflight = hourly["jobs"]["publication-watchdog-preflight"]
    script = preflight["steps"][-1]["run"]
    assert "request_bearing_attempt_budget.py" in script
    assert " observe " in script
    assert "publication-status" not in script

    watchdog_text = (WORKFLOWS / "publication-watchdog.yml").read_text(encoding="utf-8")
    assert "buildkite-collection-due" in watchdog_text
    assert "request_bearing_attempt_budget.py" in watchdog_text
    ledger_check = watchdog_text.index("ATTEMPT_OUTPUTS=")
    dns_classification = watchdog_text.index("STATUS_CLASS=")
    assert ledger_check < dns_classification


def test_site_health_independently_checks_last_durable_core_success() -> None:
    health = load("health-check.yml")
    steps = health["jobs"]["check"]["steps"]
    names = [step.get("name") for step in steps]
    observe_index = names.index("Observe durable core collection freshness")
    synthetic_index = names.index("Run synthetic site health check")
    normalize = steps[names.index("Normalize bounded health evidence")]
    assert observe_index < synthetic_index
    observe = steps[observe_index]
    assert observe["continue-on-error"] is True
    assert "request_bearing_attempt_budget.py" in observe["run"]
    assert "data_collection_attempt_budget.json observe" in observe["run"]
    assert "BUILDKITE_TOKEN" not in (observe.get("env") or {})
    assert "steps.core-freshness.outputs.latest_succeeded_at" in normalize["env"][
        "CORE_LATEST_SUCCEEDED_AT"
    ]
    assert normalize["run"] == (
        "set -euo pipefail\n"
        "python scripts/vllm/normalize_site_health_evidence.py\n"
    )
    normalizer = (
        ROOT / "scripts/vllm/normalize_site_health_evidence.py"
    ).read_text(encoding="utf-8")
    assert "core_success_at + timedelta(hours=3)" in normalizer
    assert "checker_healthy is True and core_current" in normalizer


def test_partial_backfill_cache_survives_failed_collector_steps() -> None:
    workflow = load("hourly-master.yml")
    steps = workflow["jobs"]["collect-and-deploy"]["steps"]
    validate = next(
        step for step in steps if step.get("name") == "Validate resumable CI backfill checkpoint"
    )
    save = next(
        step for step in steps if step.get("name") == "Save resumable CI backfill checkpoint"
    )
    assert str(validate["if"]).startswith("always() &&")
    assert str(save["if"]).startswith("always() &&")
    assert "steps.request-attempt.outputs.request_mode == 'reserved'" in validate["if"]
    assert "ci-backfill-cache-decision.outputs.cache_save == 'true'" in save["if"]


def test_legacy_ci_entrypoint_has_no_independent_buildkite_token_path() -> None:
    workflow = load("ci-collect.yml")
    text = (WORKFLOWS / "ci-collect.yml").read_text(encoding="utf-8")
    assert "BUILDKITE_TOKEN" not in text
    assert "hourly-master.yml/dispatches" in text
    assert workflow["jobs"]["collect"]["timeout-minutes"] <= 5
