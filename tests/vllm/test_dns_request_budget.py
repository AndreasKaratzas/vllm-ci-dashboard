from __future__ import annotations

import json
import copy
import urllib.error
import os
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
DNS_CODE_SHA = "a" * 40


def dns_runs(*, head_sha=DNS_CODE_SHA, status="completed", conclusion="failure", keyed=False):
    return {"total_count": 1, "workflow_runs": [{
        "id": 123, "path": budget.DNS_WORKFLOW_PATH,
        "repository": {"full_name": budget.DNS_REPOSITORY},
        "head_branch": "main", "head_sha": head_sha, "status": status,
        "created_at": "2026-09-01T05:40:00Z", "run_started_at": "2026-09-01T05:41:00Z",
        "updated_at": "2026-09-01T05:50:00Z", "conclusion": conclusion,
        "display_title": "DNS Health Monitor" + (" [recovery:" + "b" * 64 + "]" if keyed else ""),
    }]}


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


@pytest.mark.parametrize("keyed", [False, True])
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_normal_and_keyed_failed_dns_retry_keeps_exact_remote_ledger_unchanged(repo, policy, tmp_path, keyed, conclusion):
    checkout, _ = repo
    now = datetime(2026, 9, 1, 6, tzinfo=timezone.utc)
    before = budget.initialize_budget(checkout, policy, seeds=["2026-09-01T05:20:00Z=110"], now=INITIALIZED_AT)["budget_sha"]
    outputs = budget.reserve_budget(
        checkout, policy, state_path=tmp_path / "missing.gz", minimum_interval_hours=3,
        reservation_id="dns-200-1", now=now,
        workflow_runs_payload=dns_runs(keyed=keyed, conclusion=conclusion), current_code_sha=DNS_CODE_SHA,
    )

    assert outputs["request_mode"] == "retry_gated"
    assert outputs["reserved_request_starts"] == 0
    assert outputs["available_at"] == "2026-09-01T07:00:01Z"
    assert outputs["rolling_reserved_starts"] == 110
    assert remote_sha(checkout) == before
    assert len(budget.validate_budget_ref(checkout, before, policy).ledger["reservations"]) == 1


@pytest.mark.parametrize("runs", [
    dns_runs(head_sha="c" * 40),
    dns_runs(status="in_progress", conclusion=None),
    dns_runs(conclusion="success"),
])
def test_new_code_current_running_self_or_success_does_not_block_normal_dns_admission(repo, policy, tmp_path, runs):
    checkout, _ = repo
    budget.initialize_budget(checkout, policy, seeds=["2026-09-01T05:20:00Z=110"], now=INITIALIZED_AT)
    outputs = budget.reserve_budget(
        checkout, policy, state_path=tmp_path / "missing.gz", minimum_interval_hours=3,
        reservation_id="dns-201-1", now=datetime(2026, 9, 1, 6, tzinfo=timezone.utc),
        workflow_runs_payload=runs, current_code_sha=DNS_CODE_SHA,
    )

    assert outputs["request_mode"] == "reserved"
    assert outputs["reserved_request_starts"] == 110
    assert len(budget.validate_budget_ref(checkout, str(outputs["budget_sha"]), policy).ledger["reservations"]) == 2


@pytest.mark.parametrize("second, gated", [(0, True), (1, False)])
def test_universal_dns_retry_preserves_watchdog_inclusive_boundary(second, gated):
    now = datetime(2026, 9, 1, 7, 0, second, tzinfo=timezone.utc)
    available = budget.dns_retry_available_at(dns_runs(), current_code_sha=DNS_CODE_SHA, now=now)

    assert (available is not None) is gated


@pytest.mark.parametrize("age_hours, expected_mode", [(1, "interval_gated"), (4, "retry_gated")])
def test_universal_retry_preserves_actual_successful_generation_interval(repo, policy, tmp_path, age_hours, expected_mode):
    checkout, _ = repo
    now = datetime(2026, 9, 1, 6, tzinfo=timezone.utc)
    before = budget.initialize_budget(checkout, policy, seeds=["2026-09-01T05:20:00Z=110"], now=INITIALIZED_AT)["budget_sha"]
    state_path = tmp_path / "scanner.gz"
    write_scanner_state(state_path, now - timedelta(hours=age_hours))
    outputs = budget.reserve_budget(
        checkout, policy, state_path=state_path, minimum_interval_hours=3,
        reservation_id="dns-202-1", now=now,
        workflow_runs_payload=dns_runs(), current_code_sha=DNS_CODE_SHA,
    )

    assert outputs["request_mode"] == expected_mode
    assert outputs["reserved_request_starts"] == 0
    assert remote_sha(checkout) == before
    assert dns_failures.load_state(state_path)["generated_at"] == dns_failures.iso_timestamp(now - timedelta(hours=age_hours))


@pytest.mark.parametrize("seed_at, available_at", [
    ("2026-08-31T05:30:00Z", "2026-09-01T07:00:01Z"),
    ("2026-08-31T10:00:00Z", "2026-09-01T11:00:00Z"),
])
def test_combined_dns_capacity_and_retry_gate_reports_later_actual_availability(repo, policy, tmp_path, seed_at, available_at):
    checkout, _ = repo
    before = budget.initialize_budget(checkout, policy, seeds=[f"{seed_at}=990"], now=INITIALIZED_AT)["budget_sha"]
    outputs = budget.reserve_budget(
        checkout, policy, state_path=tmp_path / "missing.gz", minimum_interval_hours=3,
        reservation_id="dns-203-1", now=datetime(2026, 9, 1, 6, tzinfo=timezone.utc),
        workflow_runs_payload=dns_runs(), current_code_sha=DNS_CODE_SHA,
    )

    assert outputs["request_mode"] == "capacity_gated"
    assert outputs["available_at"] == available_at
    assert outputs["reserved_request_starts"] == 0
    assert remote_sha(checkout) == before


@pytest.mark.parametrize("field, value", [
    ("path", ".github/workflows/other.yml"),
    ("repository", {"full_name": "someone/else"}),
    ("head_branch", "feature"), ("head_sha", None), ("head_sha", "short"),
    ("conclusion", "unknown"), ("updated_at", "2026-09-01T07:00:00Z"),
])
def test_unknown_dns_retry_identity_fails_before_remote_ledger_read_or_mutation(monkeypatch, field, value, tmp_path, policy):
    payload = copy.deepcopy(dns_runs());payload["workflow_runs"][0][field] = value
    monkeypatch.setattr(budget, "_remote_ref_sha", lambda *a, **kw: pytest.fail("unknown proof reached the ledger"))
    with pytest.raises((budget.DnsRequestBudgetError, ValueError)):
        budget.reserve_budget(
            tmp_path, policy, state_path=tmp_path / "missing.gz", minimum_interval_hours=3,
            reservation_id="dns-204-1", now=datetime(2026, 9, 1, 6, tzinfo=timezone.utc),
            workflow_runs_payload=payload, current_code_sha=DNS_CODE_SHA,
        )


@pytest.mark.parametrize("failure", [TimeoutError("private timeout"), urllib.error.URLError("private transport"),
    urllib.error.HTTPError(budget.DNS_RUNS_URL, 503, "private response", {}, None)])
def test_transient_dns_retry_api_failure_never_reserves_or_writes_outputs(monkeypatch, failure, tmp_path, capsys):
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == budget.DNS_RUNS_URL
            assert request.get_method() == "GET"
            assert timeout == budget.DNS_RUNS_TIMEOUT_SECONDS
            raise failure
    monkeypatch.setattr(budget.urllib.request, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(budget, "_resolve_commit", lambda *a: DNS_CODE_SHA)
    monkeypatch.setattr(budget, "reserve_budget", lambda *a, **kw: pytest.fail("transient API failure reserved"))
    monkeypatch.setenv("GH_TOKEN", "fixture-only-token")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", DNS_CODE_SHA)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    output_path = tmp_path / "outputs"
    result = budget.main(["reserve", "--reservation-id", "dns-205-1", "--minimum-interval-hours", "3",
                          "--check-workflow-retries", "--github-output", str(output_path)])

    assert result == 1
    assert not output_path.exists()
    assert "private" not in capsys.readouterr().err


@pytest.mark.parametrize("raw", [b"not-json", b"{}", b'{"total_count":1,"total_count":2}',
                                b'{"ignored":NaN}', b'{"ignored":Infinity}',
                                b"x" * (2 * 1024 * 1024 + 1)],
                         ids=["invalid-json", "invalid-shape", "duplicate-keys", "nan", "infinity", "oversize"])
def test_bounded_dns_retry_api_never_admits_invalid_or_oversize_evidence(monkeypatch, raw):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit):
            assert limit == budget.workflow_contract.WORKFLOW_RUNS_MAX_BYTES + 1
            return raw[:limit]
    class Opener:
        def open(self, request, timeout): return Response()
    monkeypatch.setattr(budget.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(budget.DnsRequestBudgetError):
        payload = budget.fetch_dns_workflow_runs("fixture-only-token")
        budget.dns_retry_available_at(payload, current_code_sha=DNS_CODE_SHA,
                                      now=datetime(2026, 9, 1, 6, tzinfo=timezone.utc))


def test_dns_retry_api_uses_only_fixed_get_identity_and_nonredirecting_token_boundary(monkeypatch):
    raw = json.dumps(dns_runs()).encode()
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return raw
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == budget.DNS_RUNS_URL
            assert request.get_method() == "GET"
            assert request.get_header("Authorization") == "Bearer fixture-only-token"
            assert request.get_header("Accept-encoding") == "identity"
            assert timeout == 30
            return Response()
    def build_opener(handler):
        assert isinstance(handler, budget._NoRedirect)
        assert handler.redirect_request(None, None, 302, "redirect", {}, "https://example.invalid/") is None
        return Opener()
    monkeypatch.setattr(budget.urllib.request, "build_opener", build_opener)

    assert budget.fetch_dns_workflow_runs("fixture-only-token") == dns_runs()


@pytest.mark.parametrize("token", ["", "bad token", "bad\ntoken", "x" * 4097],
                         ids=["empty", "whitespace", "newline", "oversize"])
def test_dns_retry_api_rejects_bad_credentials_without_transport_or_token_echo(monkeypatch, token):
    monkeypatch.setattr(budget.urllib.request, "build_opener", lambda *a: pytest.fail("bad token reached transport"))
    with pytest.raises(budget.DnsRequestBudgetError, match="valid GitHub token"):
        budget.fetch_dns_workflow_runs(token)


@pytest.mark.parametrize("total_count, row_count", [(1, 0), (100, 1), (101, 99), (False, 0), (-1, 0)])
def test_truncated_or_inconsistent_dns_first_page_fails_before_ledger_read(
    monkeypatch, tmp_path, policy, total_count, row_count,
):
    payload = {"total_count": total_count, "workflow_runs": [dns_runs()["workflow_runs"][0]] * row_count}
    monkeypatch.setattr(budget, "_remote_ref_sha", lambda *a, **kw: pytest.fail("truncated proof reached ledger"))
    with pytest.raises(budget.DnsRequestBudgetError, match="invalid run counts"):
        budget.reserve_budget(
            tmp_path, policy, state_path=tmp_path / "missing.gz", minimum_interval_hours=3,
            reservation_id="dns-207-1", now=datetime(2026, 9, 1, 6, tzinfo=timezone.utc),
            workflow_runs_payload=payload, current_code_sha=DNS_CODE_SHA,
        )


@pytest.mark.parametrize("ref, trigger_sha", [
    ("refs/heads/feature", DNS_CODE_SHA), ("refs/heads/main", "b" * 40),
    (None, DNS_CODE_SHA), ("refs/heads/main", None),
])
@pytest.mark.parametrize("legacy", [False, True])
def test_dns_retry_cli_rejects_trigger_checkout_mismatch_before_api_or_reservation(
    monkeypatch, tmp_path, ref, trigger_sha, legacy,
):
    monkeypatch.setattr(budget, "_resolve_commit", lambda *a: DNS_CODE_SHA)
    monkeypatch.setattr(budget, "fetch_dns_workflow_runs", lambda *a: pytest.fail("untrusted checkout reached API"))
    monkeypatch.setattr(budget, "reserve_budget", lambda *a, **kw: pytest.fail("untrusted checkout reserved"))
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_WORKFLOW", budget.DNS_WORKFLOW_NAME if legacy else "CI")
    for name, value in [("GITHUB_REF", ref), ("GITHUB_SHA", trigger_sha)]:
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    output_path = tmp_path / "outputs"

    args = ["reserve", "--reservation-id", "dns-206-1", "--minimum-interval-hours", "3",
            "--github-output", str(output_path)]
    if not legacy:
        args.append("--check-workflow-retries")
    assert budget.main(args) == 1
    assert not output_path.exists()


@pytest.mark.parametrize("ref, expected", [("refs/heads/main", 0), ("refs/heads/feature", 1), ("", 1)])
def test_actual_dns_main_guard_precedes_immutable_checkout_and_all_reservations(ref, expected):
    workflow = yaml.safe_load((budget.ROOT / ".github/workflows/dns-health.yml").read_text())
    steps = workflow["jobs"]["collect"]["steps"]
    assert steps[1]["with"]["ref"] == "${{ github.sha }}"
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", steps[0]["run"]],
                            env=dict(os.environ, GITHUB_REF=ref, RECOVERY_KEY=""),
                            capture_output=True, text=True)

    assert result.returncode == expected


@pytest.mark.parametrize("attempt", ["2", "0", "01", "invalid", None])
@pytest.mark.parametrize("legacy", [False, True])
def test_reused_or_unknown_dns_run_attempt_fails_before_api_or_reservation(
    monkeypatch, tmp_path, capsys, attempt, legacy,
):
    monkeypatch.setattr(budget, "fetch_dns_workflow_runs", lambda *a: pytest.fail("rerun reached API"))
    monkeypatch.setattr(budget, "reserve_budget", lambda *a, **kw: pytest.fail("rerun reserved"))
    monkeypatch.setenv("GITHUB_WORKFLOW", budget.DNS_WORKFLOW_NAME if legacy else "CI")
    if attempt is None:
        monkeypatch.delenv("GITHUB_RUN_ATTEMPT", raising=False)
    else:
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", attempt)
    args = ["reserve", "--reservation-id", "dns-208-1", "--minimum-interval-hours", "3",
            "--github-output", str(tmp_path / "outputs")]
    if not legacy:
        args.append("--check-workflow-retries")

    assert budget.main(args) == 1
    assert "Use a new main workflow dispatch" in capsys.readouterr().err
    assert not (tmp_path / "outputs").exists()


def test_legacy_dns_workflow_automatically_checks_admission_without_flag_or_credentials(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GITHUB_WORKFLOW", budget.DNS_WORKFLOW_NAME)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", DNS_CODE_SHA)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(budget, "_resolve_commit", lambda *a: DNS_CODE_SHA)
    monkeypatch.setattr(budget.urllib.request, "build_opener", lambda *a: pytest.fail("missing credentials reached API"))
    monkeypatch.setattr(budget, "reserve_budget", lambda *a, **kw: pytest.fail("legacy missing proof reserved"))

    assert budget.main(["reserve", "--reservation-id", "dns-209-1", "--minimum-interval-hours", "3",
                        "--github-output", str(tmp_path / "outputs")]) == 1
    assert "valid GitHub token" in capsys.readouterr().err
    assert not (tmp_path / "outputs").exists()


def test_ordinary_non_dns_local_reserve_keeps_existing_no_flag_behavior(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_WORKFLOW", "CI")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setattr(budget, "fetch_dns_workflow_runs", lambda *a: pytest.fail("local reserve reached API"))
    monkeypatch.setattr(budget, "_resolve_commit", lambda *a: pytest.fail("local reserve read a workflow checkout"))
    def reserve(*args, **kwargs):
        assert kwargs["workflow_runs_payload"] is None
        assert kwargs["current_code_sha"] is None
        return {"request_mode": "capacity_gated", "reserved_request_starts": 0}
    monkeypatch.setattr(budget, "reserve_budget", reserve)

    assert budget.main(["reserve", "--reservation-id", "dns-210-1", "--minimum-interval-hours", "3",
                        "--github-output", str(tmp_path / "outputs")]) == 0
    assert "request_mode=capacity_gated" in (tmp_path / "outputs").read_text()


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

    gated = budget.reserve_budget(
        checkout,
        policy,
        state_path=missing_state,
        minimum_interval_hours=3,
        reservation_id="dns-101-1",
        now=INITIALIZED_AT,
    )
    assert gated == {
        "request_mode": "capacity_gated",
        "decision_at": "2026-09-01T05:40:00Z",
        "available_at": "2026-09-01T08:33:07Z",
        "budget_sha": initialized_sha,
        "reserved_request_starts": 0,
        "rolling_reserved_starts": 1368,
        "remaining_request_starts": 0,
    }
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


def test_runtime_capacity_exhaustion_is_a_zero_start_gate(
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

    outputs = budget.reserve_budget(
        checkout,
        policy,
        state_path=tmp_path / "missing.json.gz",
        minimum_interval_hours=3,
        reservation_id="dns-103-1",
        now=now,
    )
    assert outputs == {
        "request_mode": "capacity_gated",
        "decision_at": "2026-09-01T09:00:00Z",
        "available_at": "2026-09-02T01:00:00Z",
        "budget_sha": old_sha,
        "reserved_request_starts": 0,
        "rolling_reserved_starts": 990,
        "remaining_request_starts": 0,
    }
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
    capacity_report = steps[names.index("Report capacity-gated DNS request budget")]
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
        "GH_TOKEN": "${{ github.token }}",
        "ATTEMPT_ID": "dns-${{ github.run_id }}-${{ github.run_attempt }}"
    }
    assert '--reservation-id "$ATTEMPT_ID"' in reserve["run"]
    assert "--minimum-interval-hours 3" in reserve["run"]
    assert "buildkite_request_guard.py initialize" in reserve["run"]
    assert '^(reserved|interval_gated|capacity_gated|retry_gated)$' in reserve["run"]
    assert '^(interval_gated|capacity_gated|retry_gated)$' in reserve["run"]
    assert "--check-workflow-retries" in reserve["run"]
    assert reserve["timeout-minutes"] == 2
    assert '[ "$ALLOWANCE" != 0 ]' in (
        reserve["run"]
    )
    assert 'if [ "$REQUEST_MODE" = reserved ]' not in reserve["run"]
    assert 'echo "BUILDKITE_REQUEST_GUARD_FILE=$GUARD_FILE"' in reserve["run"]
    assert 'echo "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID=$ATTEMPT_ID"' in reserve["run"]
    assert 'echo "BUILDKITE_REQUEST_GUARD_ALLOWANCE=$ALLOWANCE"' in reserve["run"]
    assert collect["env"] == {
        "BUILDKITE_TOKEN": "${{ secrets.BUILDKITE_TOKEN }}",
        "GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
    }
    assert collect["id"] == "collect-dns"
    assert collect["if"] == (
        "steps.dns-request-budget.outputs.request_mode != 'capacity_gated' && "
        "steps.dns-request-budget.outputs.request_mode != 'retry_gated'"
    )
    assert capacity_report["if"] == (
        "steps.dns-request-budget.outputs.request_mode == 'capacity_gated'"
    )
    assert "made zero Buildkite requests" in capacity_report["run"]
    assert "available_at" in capacity_report["run"]
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
        condition = steps[names.index(step_name)]["if"]
        assert "request_mode != 'capacity_gated'" in condition
        assert "request_mode != 'retry_gated'" in condition
        assert "steps.collect-dns.outcome == 'success'" in condition
        assert "steps.dns-request-guard-report.outcome == 'success'" in condition
    assert workflow["jobs"]["reconcile-publication"]["if"] == (
        "needs.collect.outputs.dns_generation != ''"
    )


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
