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


def test_data_collection_serializes_before_a_failure_surviving_reservation() -> None:
    workflow = load("hourly-master.yml")
    job = workflow["jobs"]["collect-and-deploy"]
    workflow_concurrency = workflow["concurrency"]
    assert workflow_concurrency["cancel-in-progress"] is False
    workflow_group = workflow_concurrency["group"]
    assert "data-collection-routine" in workflow_group
    assert "data-collection-dns-recovery" in workflow_group
    assert "data-collection-watchdog-recovery" in workflow_group
    assert "inputs.dns_generation != ''" in workflow_group
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
    amd_matrix = next(step for step in steps if step.get("name") == "Collect AMD test matrix")
    assert "BUILDKITE_TOKEN" not in (amd_matrix.get("env") or {})


def test_gated_wakeups_cannot_publish_or_advance_buildkite_clock() -> None:
    workflow = load("hourly-master.yml")
    steps = workflow["jobs"]["collect-and-deploy"]["steps"]
    selector_index = next(
        index for index, step in enumerate(steps) if step.get("id") == "publication-selector"
    )
    for step in steps[selector_index:]:
        condition = str(step.get("if", ""))
        assert "request-attempt" in condition or "dns_generation" in condition, step.get(
            "name"
        )

    clock = next(
        step for step in steps if step.get("name") == "Advance canonical collector clock"
    )
    assert "request_mode == 'reserved'" in clock["if"]
    assert "dns_generation == ''" in clock["if"]
    assert "success_gated" not in workflow["jobs"]["collect-and-deploy"].get("if", "")
    text = (WORKFLOWS / "hourly-master.yml").read_text(encoding="utf-8")
    assert "Data collection made zero Buildkite requests" in text
    assert "success_gated deploy" not in text.casefold()


def test_success_is_recorded_after_state_rotation_before_pages_work() -> None:
    workflow = load("hourly-master.yml")
    steps = workflow["jobs"]["collect-and-deploy"]["steps"]
    names = [step.get("name") for step in steps]
    rotation = names.index("Publish validated dashboard state")
    report = names.index("Read exact guarded Buildkite request total")
    success = names.index("Mark durable Data Collection success")
    marker = names.index("Write state publication marker")
    assert rotation < report < success < marker
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
    assert "core_success_at + timedelta(hours=3)" in normalize["run"]
    assert "checker_healthy is True and core_current" in normalize["run"]


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
