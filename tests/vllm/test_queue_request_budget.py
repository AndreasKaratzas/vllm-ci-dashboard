from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from vllm import queue_request_budget as budget


INITIALIZED_AT = datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc)


def git(root: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        input=input_text,
        text=True,
        check=True,
        capture_output=True,
    )
    return result.stdout.strip()


@pytest.fixture
def policy() -> budget.BudgetPolicy:
    return budget.BudgetPolicy(
        branch="queue-request-budget",
        ledger_path="queue_request_budget.json",
        window_hours=25,
        max_request_starts=650,
        metrics_reservation_request_starts=2,
        details_reservation_request_starts=12,
        metrics_minimum_interval_minutes=10,
        details_minimum_interval_minutes=60,
        max_legacy_seed_request_starts=650,
        max_reservations=256,
        max_ledger_bytes=128 * 1024,
    )


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, Path]:
    checkout = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    checkout.mkdir()
    git(checkout, "init")
    git(checkout, "config", "commit.gpgsign", "false")
    git(checkout, "config", "user.name", "Dashboard Budget Test")
    git(checkout, "config", "user.email", "dashboard-budget-test@example.invalid")
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(checkout, "remote", "add", "origin", str(remote))
    return checkout, remote


def remote_sha(root: Path) -> str | None:
    output = git(
        root,
        "ls-remote",
        "--refs",
        "origin",
        "refs/heads/queue-request-budget",
    )
    return output.split()[0] if output else None


def initialize(
    root: Path,
    policy: budget.BudgetPolicy,
    *,
    seed_at: datetime = INITIALIZED_AT - timedelta(hours=1),
    amount: int = 8,
) -> str:
    outputs = budget.initialize_budget(
        root,
        policy,
        seeds=(f"{seed_at.strftime('%Y-%m-%dT%H:%M:%SZ')}={amount}",),
        now=INITIALIZED_AT,
    )
    return str(outputs["budget_sha"])


def test_repository_policy_is_the_exact_queue_cap() -> None:
    policy = budget.load_policy()
    assert policy.branch == "queue-request-budget"
    assert policy.ledger_path == "queue_request_budget.json"
    assert policy.window_hours == 25
    assert policy.max_request_starts == 650
    assert policy.metrics_reservation_request_starts == 2
    assert policy.details_reservation_request_starts == 12
    assert policy.metrics_minimum_interval_minutes == 10
    assert policy.details_minimum_interval_minutes == 60
    # 150 metric opportunities plus 25 hourly detail opportunities per 25h.
    assert 150 * 2 + 25 * 12 == 600 < policy.max_request_starts


def test_operator_initialization_is_parentless_and_exactly_one_file(
    repo: tuple[Path, Path],
    policy: budget.BudgetPolicy,
) -> None:
    checkout, _ = repo
    state_sha = initialize(checkout, policy)

    assert remote_sha(checkout) == state_sha
    assert git(checkout, "rev-list", "--parents", "-n", "1", state_sha) == state_sha
    assert git(checkout, "ls-tree", "-r", "--name-only", state_sha) == policy.ledger_path
    validated = budget.validate_budget_ref(checkout, state_sha, policy)
    assert validated.ledger["migration_debt"] is False
    assert validated.ledger["reservations"][0]["kind"] == "legacy_seed"


def test_initializer_accepts_conservative_over_cap_legacy_run_telemetry(
    repo: tuple[Path, Path],
    policy: budget.BudgetPolicy,
) -> None:
    checkout, _ = repo
    seeds = tuple(
        f"2026-09-01T{hour:02d}:00:00Z=101" for hour in range(0, 6)
    ) + ("2026-09-01T05:30:00Z=101",)
    initialized = budget.initialize_budget(
        checkout,
        policy,
        seeds=seeds,
        now=INITIALIZED_AT,
    )
    validated = budget.validate_budget_ref(
        checkout, str(initialized["budget_sha"]), policy
    )
    assert validated.ledger["migration_debt"] is True
    assert sum(row["request_starts"] for row in validated.ledger["reservations"]) == 707

    gated = budget.reserve_budget(
        checkout,
        policy,
        reservation_id="queue-cutover-1",
        now=INITIALIZED_AT + timedelta(hours=1),
    )
    assert gated["request_mode"] == "capacity_gated"
    assert gated["reserved_request_starts"] == 0
    assert gated["metrics_request_limit"] == 0
    assert gated["details_request_limit"] == 0
    assert gated["rolling_reserved_starts"] == 707
    assert gated["remaining_request_starts"] == 0
    assert gated["available_at"] == "2026-09-02T01:00:00Z"
    assert remote_sha(checkout) == initialized["budget_sha"]


def test_burst_is_coalesced_without_moving_the_branch(
    repo: tuple[Path, Path],
    policy: budget.BudgetPolicy,
) -> None:
    checkout, _ = repo
    initial_sha = initialize(
        checkout,
        policy,
        seed_at=INITIALIZED_AT - timedelta(hours=2),
    )
    first = budget.reserve_budget(
        checkout,
        policy,
        reservation_id="queue-100-1",
        now=INITIALIZED_AT,
    )
    assert first["request_mode"] == "metrics_and_details"
    assert first["reserved_request_starts"] == 14
    assert first["metrics_request_limit"] == 2
    assert first["details_request_limit"] == 12
    first_sha = str(first["budget_sha"])
    assert first_sha != initial_sha

    burst = budget.reserve_budget(
        checkout,
        policy,
        reservation_id="queue-101-1",
        now=INITIALIZED_AT + timedelta(minutes=9, seconds=59),
    )
    assert burst["request_mode"] == "interval_gated"
    assert burst["reserved_request_starts"] == 0
    assert remote_sha(checkout) == first_sha


def test_metrics_and_details_have_independent_durable_cadences(
    repo: tuple[Path, Path],
    policy: budget.BudgetPolicy,
) -> None:
    checkout, _ = repo
    initialize(checkout, policy, seed_at=INITIALIZED_AT - timedelta(hours=2))

    full = budget.reserve_budget(
        checkout, policy, reservation_id="queue-110-1", now=INITIALIZED_AT
    )
    metrics = budget.reserve_budget(
        checkout,
        policy,
        reservation_id="queue-111-1",
        now=INITIALIZED_AT + timedelta(minutes=10),
    )
    next_full = budget.reserve_budget(
        checkout,
        policy,
        reservation_id="queue-112-1",
        now=INITIALIZED_AT + timedelta(minutes=60),
    )

    assert full["request_mode"] == "metrics_and_details"
    assert metrics["request_mode"] == "metrics"
    assert metrics["reserved_request_starts"] == 2
    assert metrics["details_request_limit"] == 0
    assert next_full["request_mode"] == "metrics_and_details"
    assert next_full["reserved_request_starts"] == 14
    validated = budget.validate_budget_ref(
        checkout, str(next_full["budget_sha"]), policy
    )
    kinds = [row["kind"] for row in validated.ledger["reservations"]]
    assert kinds.count("metrics") == 3
    assert kinds.count("details") == 2


def test_budget_exhaustion_is_a_zero_start_gate_and_never_moves_the_branch(
    repo: tuple[Path, Path],
    policy: budget.BudgetPolicy,
) -> None:
    checkout, _ = repo
    old_sha = initialize(
        checkout,
        policy,
        seed_at=INITIALIZED_AT - timedelta(hours=2),
        amount=642,
    )
    outputs = budget.reserve_budget(
        checkout,
        policy,
        reservation_id="queue-120-1",
        now=INITIALIZED_AT,
    )
    assert outputs == {
        "request_mode": "capacity_gated",
        "decision_at": "2026-09-01T06:00:00Z",
        "available_at": "2026-09-02T05:00:00Z",
        "budget_sha": old_sha,
        "reserved_request_starts": 0,
        "metrics_request_limit": 0,
        "details_request_limit": 0,
        "rolling_reserved_starts": 642,
        "remaining_request_starts": 8,
    }
    assert remote_sha(checkout) == old_sha


def test_missing_or_non_parentless_ledger_fails_closed(
    repo: tuple[Path, Path],
    policy: budget.BudgetPolicy,
) -> None:
    checkout, _ = repo
    with pytest.raises(budget.QueueRequestBudgetError, match="branch is absent"):
        budget.reserve_budget(
            checkout,
            policy,
            reservation_id="queue-130-1",
            now=INITIALIZED_AT,
        )

    initialized_sha = initialize(checkout, policy)
    tree_sha = git(checkout, "rev-parse", f"{initialized_sha}^{{tree}}")
    child = git(
        checkout,
        "commit-tree",
        tree_sha,
        "-p",
        initialized_sha,
        input_text="invalid child\n",
    )
    git(checkout, "push", "--force", "origin", f"{child}:refs/heads/{policy.branch}")
    with pytest.raises(budget.QueueRequestBudgetError, match="parentless"):
        budget.reserve_budget(
            checkout,
            policy,
            reservation_id="queue-131-1",
            now=INITIALIZED_AT,
        )


def test_stale_exact_lease_cannot_overwrite_a_concurrent_reservation(
    repo: tuple[Path, Path],
    policy: budget.BudgetPolicy,
) -> None:
    checkout, _ = repo
    base_sha = initialize(checkout, policy)
    base = budget.validate_budget_ref(checkout, base_sha, policy).ledger

    def candidate(reservation_id: str) -> budget.ValidatedBudget:
        rows = [dict(row) for row in base["reservations"]]
        rows.append({
            "id": reservation_id,
            "kind": "metrics",
            "reserved_at": "2026-09-01T06:00:00Z",
            "request_starts": 2,
        })
        rows.sort(key=lambda row: (row["reserved_at"], row["id"]))
        return budget._create_parentless_commit(
            checkout,
            {**base, "updated_at": "2026-09-01T06:00:00Z", "reservations": rows},
            policy,
        )

    winner = candidate("queue-winner-1")
    stale = candidate("queue-stale-1")
    budget._push_budget(checkout, "origin", winner.commit_sha, base_sha, policy)
    with pytest.raises(budget.QueueRequestBudgetError, match="force-with-lease"):
        budget._push_budget(checkout, "origin", stale.commit_sha, base_sha, policy)
    assert remote_sha(checkout) == winner.commit_sha


def test_config_and_ledger_validation_reject_ambiguous_or_wrong_allowances(
    tmp_path: Path,
    policy: budget.BudgetPolicy,
) -> None:
    duplicate = tmp_path / "config.json"
    duplicate.write_text(
        '{"schema_version":1,"schema_version":1,"branch":"queue-request-budget"}'
    )
    with pytest.raises(budget.QueueRequestBudgetError, match="strict UTF-8 JSON"):
        budget.load_policy(duplicate)

    ledger = {
        "schema_version": 1,
        "updated_at": "2026-09-01T06:00:00Z",
        "policy": budget._ledger_policy(policy),
        "migration_debt": False,
        "reservations": [{
            "id": "metrics-1",
            "kind": "metrics",
            "reserved_at": "2026-09-01T05:00:00Z",
            "request_starts": 3,
        }],
    }
    with pytest.raises(budget.QueueRequestBudgetError, match="wrong request allowance"):
        budget._normalize_ledger(ledger, policy)


def test_workflow_gates_every_trigger_before_exposing_buildkite_token() -> None:
    workflow_path = budget.ROOT / ".github/workflows/queue-monitor.yml"
    workflow = yaml.safe_load(workflow_path.read_text())
    job = workflow["jobs"]["snapshot"]
    assert job["timeout-minutes"] <= 20
    assert job["env"] == {"PYTHONPATH": "${{ github.workspace }}/scripts"}
    steps = job["steps"]
    names = [step.get("name") for step in steps]
    reserve = steps[names.index("Reserve durable rolling queue request budget")]
    capacity_report = steps[
        names.index("Report capacity-gated queue request budget")
    ]
    collect = steps[names.index("Collect bounded queue snapshot")]
    report = steps[names.index("Read exact guarded queue request total")]

    assert names.index("Reserve durable rolling queue request budget") < names.index(
        "Report capacity-gated queue request budget"
    ) < names.index("Collect bounded queue snapshot")
    assert "BUILDKITE_TOKEN" not in reserve.get("env", {})
    assert reserve["env"] == {
        "ATTEMPT_ID": "queue-${{ github.run_id }}-${{ github.run_attempt }}"
    }
    assert "queue_request_budget.py reserve" in reserve["run"]
    assert '--reservation-id "$ATTEMPT_ID"' in reserve["run"]
    assert "buildkite_request_guard.py initialize" in reserve["run"]
    assert 'if [ "$REQUEST_MODE" != interval_gated ]' not in reserve["run"]
    assert (
        "interval_gated:0:0:0|capacity_gated:0:0:0|"
        "metrics:2:2:0|metrics_and_details:14:2:12"
    ) in reserve["run"]
    for name in (
        "BUILDKITE_REQUEST_GUARD_FILE",
        "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID",
        "BUILDKITE_REQUEST_GUARD_ALLOWANCE",
    ):
        assert f'echo "{name}=' in reserve["run"]
    assert collect["env"] == {"BUILDKITE_TOKEN": "${{ secrets.BUILDKITE_TOKEN }}"}
    assert collect["id"] == "collect-queue"
    assert "--metrics-max-pages 2" in collect["run"]
    assert "--details-max-pages 12" in collect["run"]
    assert "metrics_and_details" in collect["run"]
    assert "request_mode == 'metrics'" in collect["if"]
    assert "request_mode == 'metrics_and_details'" in collect["if"]
    assert capacity_report["if"] == (
        "steps.queue-request-budget.outputs.request_mode == 'capacity_gated'"
    )
    assert "made zero Buildkite requests" in capacity_report["run"]
    assert "available_at" in capacity_report["run"]
    assert names.index("Read exact guarded queue request total") == names.index(
        "Collect bounded queue snapshot"
    ) + 1
    assert "always()" in report["if"]
    assert "steps.queue-request-budget.outcome == 'success'" in report["if"]
    assert "buildkite_request_guard.py report" in report["run"]
    for name in (
        "BUILDKITE_REQUEST_GUARD_FILE",
        "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID",
        "BUILDKITE_REQUEST_GUARD_ALLOWANCE",
    ):
        assert f'"${name}"' in report["run"]

    token_steps = [
        (index, step)
        for index, step in enumerate(steps)
        if "BUILDKITE_TOKEN" in (step.get("env") or {})
        or "BUILDKITE_API_TOKEN" in (step.get("env") or {})
    ]
    assert token_steps == [(names.index("Collect bounded queue snapshot"), collect)]
    assert token_steps[0][0] > names.index("Reserve durable rolling queue request budget")

    for step_name in (
        "Build live queue section",
        "Validate live queue evidence",
        "Publish durable live queue evidence",
    ):
        condition = next(step for step in steps if step.get("name") == step_name)["if"]
        assert "request_mode == 'metrics'" in condition
        assert "request_mode == 'metrics_and_details'" in condition
        assert "steps.collect-queue.outcome == 'success'" in condition
        assert "steps.queue-request-guard-report.outcome == 'success'" in condition
