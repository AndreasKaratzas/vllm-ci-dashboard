from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_ci_rejects_composed_state_overflow_before_merge() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert (
        "python scripts/vllm/check_dashboard_state_storage_budget.py --root ."
        in workflow
    )


def test_hourly_candidate_checks_composition_before_and_after_manifest() -> None:
    workflow = (ROOT / ".github" / "workflows" / "hourly-master.yml").read_text()
    guard = "python scripts/vllm/check_dashboard_state_storage_budget.py"
    prepare = "python scripts/vllm/dashboard_state.py prepare"

    assert workflow.count(guard) == 2
    first_guard = workflow.index(guard)
    prepare_position = workflow.index(prepare, first_guard)
    second_guard = workflow.index(guard, first_guard + len(guard))
    refresh = workflow.index("dashboard_state.py refresh-manifest", prepare_position)
    create = workflow.index("dashboard_state.py create-commit", refresh)

    assert first_guard < prepare_position < refresh < second_guard < create
