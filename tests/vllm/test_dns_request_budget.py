from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from vllm import dns_request_budget as budget
from vllm.ci import dns_failures


INITIALIZED_AT = datetime(2026, 9, 1, 5, 40, tzinfo=timezone.utc)
LEGACY_SEEDS = (
    "2026-08-31T07:33:07Z=570",
    "2026-08-31T15:20:21Z=568",
    "2026-08-31T23:00:56Z=110",
    "2026-09-01T05:20:34Z=10",
    "2026-09-01T05:33:17Z=110",
)


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
        branch="dns-request-budget",
        ledger_path="dns_request_budget.json",
        window_hours=25,
        max_request_starts=990,
        scan_reservation_request_starts=110,
        max_legacy_seed_request_starts=1000,
        max_reservations=32,
        max_ledger_bytes=64 * 1024,
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
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        check=True,
        capture_output=True,
    )
    git(checkout, "remote", "add", "origin", str(remote))
    return checkout, remote


def remote_sha(root: Path, branch: str = "dns-request-budget") -> str | None:
    output = git(root, "ls-remote", "--refs", "origin", f"refs/heads/{branch}")
    return output.split()[0] if output else None


def initialize(
    root: Path,
    policy: budget.BudgetPolicy,
    *,
    now: datetime = INITIALIZED_AT,
) -> str:
    outputs = budget.initialize_budget(
        root,
        policy,
        seeds=LEGACY_SEEDS,
        now=now,
    )
    return str(outputs["budget_sha"])


def write_scanner_state(path: Path, generated_at: datetime) -> None:
    state = dns_failures.empty_state(
        generated_at,
        generated_at - timedelta(hours=24),
    )
    dns_failures.write_state(path, state)


def test_repository_policy_is_the_exact_rolling_cap() -> None:
    policy = budget.load_policy()
    assert policy.branch == "dns-request-budget"
    assert policy.ledger_path == "dns_request_budget.json"
    assert policy.window_hours == 25
    assert policy.max_request_starts == 990
    assert policy.scan_reservation_request_starts == 110
    assert policy.max_reservations == 32
    assert policy.max_ledger_bytes == 64 * 1024


def test_operator_initialization_records_bounded_legacy_debt_parentlessly(
    repo: tuple[Path, Path],
    policy: budget.BudgetPolicy,
) -> None:
    checkout, _ = repo
    state_sha = initialize(checkout, policy)

    assert remote_sha(checkout) == state_sha
    assert git(checkout, "rev-list", "--parents", "-n", "1", state_sha) == state_sha
    assert git(checkout, "ls-tree", "-r", "--name-only", state_sha) == policy.ledger_path
    validated = budget.validate_budget_ref(checkout, state_sha, policy)
    assert validated.ledger["migration_debt"] is True
    assert sum(row["request_starts"] for row in validated.ledger["reservations"]) == 1368
    assert {row["kind"] for row in validated.ledger["reservations"]} == {"legacy_seed"}


def test_interval_gated_republication_does_not_reserve_or_move_branch(
    repo: tuple[Path, Path],
    policy: budget.BudgetPolicy,
    tmp_path: Path,
) -> None:
    checkout, _ = repo
    initialized_sha = initialize(checkout, policy)
    scanner_state = tmp_path / "scan_state.json.gz"
    write_scanner_state(
        scanner_state,
        datetime(2026, 9, 1, 5, 33, 17, tzinfo=timezone.utc),
    )

    outputs = budget.reserve_budget(
        checkout,
        policy,
        state_path=scanner_state,
        minimum_interval_hours=3,
        reservation_id="dns-100-1",
        now=datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc),
    )

    assert outputs["request_mode"] == "interval_gated"
    assert outputs["reserved_request_starts"] == 0
    assert outputs["rolling_reserved_starts"] == 1368
    assert outputs["decision_at"] == "2026-09-01T06:00:00Z"
    assert remote_sha(checkout) == initialized_sha


def test_legacy_debt_blocks_then_expires_on_the_half_open_boundary(
    repo: tuple[Path, Path],
    policy: budget.BudgetPolicy,
    tmp_path: Path,
) -> None:
    checkout, _ = repo
    initialized_sha = initialize(checkout, policy)
    missing_state = tmp_path / "missing.json.gz"

    with pytest.raises(
        budget.DnsRequestBudgetError,
        match="1368 starts remain reserved.*2026-09-01T08:33:07Z",
    ):
        budget.reserve_budget(
            checkout,
            policy,
            state_path=missing_state,
            minimum_interval_hours=3,
            reservation_id="dns-101-1",
            now=INITIALIZED_AT,
        )
    assert remote_sha(checkout) == initialized_sha

    outputs = budget.reserve_budget(
        checkout,
        policy,
        state_path=missing_state,
        minimum_interval_hours=3,
        reservation_id="dns-102-1",
        now=datetime(2026, 9, 1, 8, 33, 7, tzinfo=timezone.utc),
    )

    assert outputs["request_mode"] == "reserved"
    assert outputs["reserved_request_starts"] == 110
    assert outputs["rolling_reserved_starts"] == 908
    assert outputs["remaining_request_starts"] == 82
    new_sha = str(outputs["budget_sha"])
    assert new_sha != initialized_sha
    assert remote_sha(checkout) == new_sha
    validated = budget.validate_budget_ref(checkout, new_sha, policy)
    assert validated.ledger["migration_debt"] is False
    assert sum(row["request_starts"] for row in validated.ledger["reservations"]) == 908
    assert git(checkout, "rev-list", "--count", new_sha) == "1"


def test_runtime_reservation_never_creates_an_over_cap_ledger(
    repo: tuple[Path, Path],
    policy: budget.BudgetPolicy,
    tmp_path: Path,
) -> None:
    checkout, _ = repo
    now = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    seeds = tuple(f"2026-09-01T{hour:02d}:00:00Z=110" for hour in range(0, 9))
    initialized = budget.initialize_budget(
        checkout,
        policy,
        seeds=seeds,
        now=now,
    )
    old_sha = str(initialized["budget_sha"])

    with pytest.raises(budget.DnsRequestBudgetError, match="990 starts remain reserved"):
        budget.reserve_budget(
            checkout,
            policy,
            state_path=tmp_path / "missing.json.gz",
            minimum_interval_hours=3,
            reservation_id="dns-103-1",
            now=now,
        )
    assert remote_sha(checkout) == old_sha
    assert budget.validate_budget_ref(checkout, old_sha, policy).ledger["migration_debt"] is False


def test_missing_or_non_parentless_established_ledger_fails_closed(
    repo: tuple[Path, Path],
    policy: budget.BudgetPolicy,
    tmp_path: Path,
) -> None:
    checkout, _ = repo
    with pytest.raises(budget.DnsRequestBudgetError, match="controlled initialize"):
        budget.reserve_budget(
            checkout,
            policy,
            state_path=tmp_path / "missing.json.gz",
            minimum_interval_hours=3,
            reservation_id="dns-104-1",
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

    with pytest.raises(budget.DnsRequestBudgetError, match="parentless"):
        budget.reserve_budget(
            checkout,
            policy,
            state_path=tmp_path / "missing.json.gz",
            minimum_interval_hours=3,
            reservation_id="dns-105-1",
            now=INITIALIZED_AT,
        )


def test_stale_exact_lease_cannot_overwrite_a_concurrent_reservation(
    repo: tuple[Path, Path],
    policy: budget.BudgetPolicy,
) -> None:
    checkout, _ = repo
    base = budget.initialize_budget(
        checkout,
        policy,
        seeds=("2026-09-01T05:00:00Z=110",),
        now=INITIALIZED_AT,
    )
    base_sha = str(base["budget_sha"])
    base_ledger = budget.validate_budget_ref(checkout, base_sha, policy).ledger

    def candidate(reservation_id: str) -> budget.ValidatedBudget:
        rows = [dict(row) for row in base_ledger["reservations"]]
        rows.append(
            {
                "id": reservation_id,
                "kind": "scan",
                "reserved_at": "2026-09-01T06:00:00Z",
                "request_starts": 110,
            }
        )
        return budget._create_parentless_commit(
            checkout,
            {
                **base_ledger,
                "updated_at": "2026-09-01T06:00:00Z",
                "reservations": rows,
            },
            policy,
        )

    winner = candidate("dns-winner-1")
    stale = candidate("dns-stale-1")
    budget._push_budget(checkout, "origin", winner.commit_sha, base_sha, policy)

    with pytest.raises(budget.DnsRequestBudgetError, match="force-with-lease"):
        budget._push_budget(checkout, "origin", stale.commit_sha, base_sha, policy)
    assert remote_sha(checkout) == winner.commit_sha


def test_config_and_ledger_validation_reject_ambiguous_input(
    tmp_path: Path,
    policy: budget.BudgetPolicy,
) -> None:
    duplicate_config = tmp_path / "config.json"
    duplicate_config.write_text(
        '{"schema_version":1,"schema_version":1,"branch":"dns-request-budget"}'
    )
    with pytest.raises(budget.DnsRequestBudgetError, match="strict UTF-8 JSON"):
        budget.load_policy(duplicate_config)

    ledger = {
        "schema_version": 1,
        "updated_at": "2026-09-01T06:00:00Z",
        "policy": {
            "window_hours": 25,
            "max_request_starts": 990,
            "scan_reservation_request_starts": 110,
        },
        "migration_debt": False,
        "reservations": [
            {
                "id": "scan-1",
                "kind": "scan",
                "reserved_at": "2026-09-01T05:00:00Z",
                "request_starts": 881,
            }
        ],
    }
    with pytest.raises(budget.DnsRequestBudgetError, match="per-run allowance"):
        budget._normalize_ledger(ledger, policy)


def test_workflow_reserves_before_buildkite_and_reuses_the_exact_decision_clock() -> None:
    workflow_path = budget.ROOT / ".github/workflows/dns-health.yml"
    workflow = yaml.safe_load(workflow_path.read_text())
    collect_job = workflow["jobs"]["collect"]
    assert workflow["concurrency"]["group"] == "dns-health-data-publish"
    steps = collect_job["steps"]
    names = [step.get("name") for step in steps]
    reserve = steps[names.index("Reserve durable rolling DNS request budget")]
    collect = steps[names.index("Collect DNS failure observations")]
    report = steps[names.index("Read exact guarded Buildkite request total")]

    assert collect_job["env"] == {
        "PYTHONPATH": "${{ github.workspace }}/scripts"
    }
    assert (
        names.index("Resolve durable DNS scanner state")
        < names.index("Reserve durable rolling DNS request budget")
        < names.index("Collect DNS failure observations")
        < names.index("Read exact guarded Buildkite request total")
    )
    assert reserve["id"] == "dns-request-budget"
    assert "BUILDKITE_TOKEN" not in reserve.get("env", {})
    assert "dns_request_budget.py reserve" in reserve["run"]
    assert reserve["env"] == {
        "ATTEMPT_ID": "dns-${{ github.run_id }}-${{ github.run_attempt }}"
    }
    assert '--reservation-id "$ATTEMPT_ID"' in reserve["run"]
    assert "--minimum-interval-hours 3" in reserve["run"]
    assert "buildkite_request_guard.py initialize" in reserve["run"]
    assert '[ "$REQUEST_MODE" = interval_gated ] && [ "$ALLOWANCE" != 0 ]' in (
        reserve["run"]
    )
    assert 'if [ "$REQUEST_MODE" = reserved ]' not in reserve["run"]
    assert 'echo "BUILDKITE_REQUEST_GUARD_FILE=$GUARD_FILE"' in reserve["run"]
    assert 'echo "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID=$ATTEMPT_ID"' in reserve["run"]
    assert 'echo "BUILDKITE_REQUEST_GUARD_ALLOWANCE=$ALLOWANCE"' in reserve["run"]
    assert collect["env"] == {"BUILDKITE_TOKEN": "${{ secrets.BUILDKITE_TOKEN }}"}
    assert collect["id"] == "collect-dns"
    assert "if" not in collect
    assert "--max-requests 110" in collect["run"]
    assert '--now "${{ steps.dns-request-budget.outputs.decision_at }}"' in collect["run"]
    assert "always()" in report["if"]
    assert "steps.dns-request-budget.outcome == 'success'" in report["if"]
    assert "buildkite_request_guard.py report" in report["run"]
    assert "--file \"$BUILDKITE_REQUEST_GUARD_FILE\"" in report["run"]
    assert "--attempt-id \"$BUILDKITE_REQUEST_GUARD_ATTEMPT_ID\"" in report["run"]
    assert "--allowance \"$BUILDKITE_REQUEST_GUARD_ALLOWANCE\"" in report["run"]

    for step_name in (
        "Validate bounded DNS artifacts",
        "Capture validated DNS generation",
        "Encrypt durable DNS scanner state",
        "Publish durable DNS evidence",
    ):
        assert "if" not in steps[names.index(step_name)]
    assert "if" not in workflow["jobs"]["reconcile-publication"]


def test_fixed_collector_clock_keeps_an_interval_gated_run_at_zero_requests(
    tmp_path: Path,
) -> None:
    from vllm import collect_dns_failures as collector

    generated_at = datetime(2026, 9, 1, 5, 33, 17, tzinfo=timezone.utc)
    state_path = tmp_path / "scan_state.json.gz"
    output_path = tmp_path / "dns_failures.json"
    write_scanner_state(state_path, generated_at)

    class NoRequestClient:
        def discover_builds(self, *args, **kwargs):  # pragma: no cover - must not run.
            raise AssertionError("interval-gated collection made a Buildkite request")

    payload = collector.collect(
        client=NoRequestClient(),
        state_path=state_path,
        output_path=output_path,
        minimum_interval_hours=3,
        now=generated_at + timedelta(hours=2, minutes=59, seconds=59),
        classification_cache_path=None,
    )

    assert payload["generated_at"] == "2026-09-01T05:33:17Z"
    assert dns_failures.load_state(state_path)["generated_at"] == ("2026-09-01T05:33:17Z")
