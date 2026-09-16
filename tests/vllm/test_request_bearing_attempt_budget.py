from __future__ import annotations

import json
import hashlib
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vllm import request_bearing_attempt_budget as budget
from vllm import collection_recovery as recovery


BASE = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
REF = "a" * 40


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


@pytest.fixture
def policy() -> budget.AttemptPolicy:
    return budget.AttemptPolicy(
        producer="data_collection",
        branch="data-collection-attempt-budget",
        ledger_path="data_collection_attempt_budget.json",
        window_hours=25,
        max_request_bearing_attempts=16,
        success_interval_minutes=120,
        failed_retry_interval_minutes=30,
        request_start_allowance=800,
        max_migration_overlap_attempts=19,
        max_migration_runtime_attempts=16,
        max_legacy_seed_attempts=64,
        max_ledger_bytes=128 * 1024,
    )


def seed(run_id: int, reserved_at: datetime, *, success: bool = True) -> dict[str, object]:
    return {
        "workflow_run_id": str(run_id),
        "workflow_run_attempt": 1,
        "event_name": "schedule",
        "reserved_at": budget._iso(reserved_at),
        "request_start_bound_proven": False,
        "succeeded_at": budget._iso(reserved_at + timedelta(minutes=20)) if success else None,
        "durable_ref": REF if success else None,
        "actual_request_starts": None,
    }


def initialize(
    root: Path,
    policy: budget.AttemptPolicy,
    seeds: list[dict[str, object]],
    *,
    now: datetime = BASE,
) -> str:
    seed_path = root / "seeds.json"
    seed_path.write_text(json.dumps(seeds), encoding="utf-8")
    outputs = budget.initialize(
        root,
        policy,
        seed_file=seed_path,
        now=now,
        remote="origin",
    )
    return str(outputs["budget_sha"])


def reserve(
    root: Path,
    policy: budget.AttemptPolicy,
    number: int,
    now: datetime,
) -> dict[str, object]:
    return budget.reserve(
        root,
        policy,
        attempt_id=f"data-{number}-1",
        workflow_run_id=str(number),
        workflow_run_attempt=1,
        event_name="schedule",
        now=now,
        remote="origin",
    )


def remote_sha(root: Path, policy: budget.AttemptPolicy) -> str | None:
    output = git(root, "ls-remote", "--refs", "origin", f"refs/heads/{policy.branch}")
    return output.split()[0] if output else None


def published_state(root: Path, *, failures=(), fallback=(), fresh=(), generated_at=None,
                    corrupt_manifest=False, parent=None) -> str:
    """A real parentless commit with manifest-bound recovery evidence."""
    fallback = sorted(set(fallback) | set(failures))
    payload = {
        "schema_version": 2,
        "generated_at": generated_at or budget._iso(BASE + timedelta(minutes=5)),
        "mode": "mixed" if fallback and fresh else (
            "fallback" if fallback else "degraded" if fresh else "current"
        ),
        "fallback_surfaces": fallback,
        "fresh_degraded_surfaces": sorted(fresh),
        "degraded_surfaces": sorted(set(fallback) | set(fresh)),
        "collector_failures": [
            {"schema_version": 1, "surface": surface, "collector": "collect.py",
             "step": "Requested collector", "reason_class": "rate-limit", "exit_code": 1}
            for surface in failures
        ],
    }
    state = root / recovery.STATE_PATH
    state.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload) + "\n").encode()
    state.write_bytes(raw)
    oid = git(root, "hash-object", "-w", str(state))
    manifest = {
        "schema_version": 2,
        "generated_files": {
            recovery.STATE_PATH: {
                "bytes": len(raw), "git_oid": oid, "mode": "100644",
                "sha256": "0" * 64 if corrupt_manifest else hashlib.sha256(raw).hexdigest(),
            },
        },
    }
    (root / recovery.MANIFEST_PATH).write_text(json.dumps(manifest))
    git(root, "add", "--", recovery.STATE_PATH, recovery.MANIFEST_PATH)
    tree = git(root, "write-tree")
    parent_args = ["-p", parent] if parent else []
    return git(root, "commit-tree", tree, *parent_args, input_text="Publication evidence fixture\n")


def record_publication(root, policy, *, state_sha, monkeypatch=None):
    started = reserve(root, policy, 2000, BASE)
    kwargs = dict(
        attempt_id=started["attempt_id"], durable_ref=state_sha,
        actual_request_starts=123, now=BASE + timedelta(minutes=10), remote="origin",
    )
    if monkeypatch is None:
        budget.mark_success(root, policy, require_collection_evidence=True, **kwargs)
    else:
        # Model an existing success written by the pre-recovery workflow.
        with monkeypatch.context() as patch:
            def unavailable(*args, **kwargs):
                raise recovery.CollectionEvidenceError("legacy writer")
            patch.setattr(budget, "read_collection_evidence", unavailable)
            budget.mark_success(root, policy, **kwargs)
    return started


def test_repository_policies_match_audited_composed_caps() -> None:
    data = budget.load_policy(budget.ROOT / "config/data_collection_attempt_budget.json")
    lifecycle = budget.load_policy(budget.ROOT / "config/queue_lifecycle_attempt_budget.json")
    assert (data.window_hours, data.max_request_bearing_attempts) == (25, 16)
    assert (data.success_interval_minutes, data.failed_retry_interval_minutes) == (120, 30)
    assert data.request_start_allowance == 800
    assert lifecycle.request_start_allowance == 100
    for value in (data, lifecycle):
        assert value.max_migration_runtime_attempts == 16
        assert value.max_migration_overlap_attempts == 19
    assert data.max_request_bearing_attempts * data.request_start_allowance == 12_800
    assert lifecycle.max_request_bearing_attempts * lifecycle.request_start_allowance == 1_600


def test_initialization_is_parentless_exact_one_file_and_missing_fails_closed(
    repo: tuple[Path, Path], policy: budget.AttemptPolicy
) -> None:
    checkout, _ = repo
    with pytest.raises(budget.AttemptBudgetError, match="branch is absent"):
        reserve(checkout, policy, 1, BASE)

    sha = initialize(checkout, policy, [seed(10, BASE - timedelta(hours=3))])
    assert remote_sha(checkout, policy) == sha
    assert git(checkout, "rev-list", "--parents", "-n", "1", sha) == sha
    assert git(checkout, "ls-tree", "-r", "--name-only", sha) == policy.ledger_path
    assert budget.validate_ledger_ref(checkout, sha, policy).ledger["migration_debt"] is True


def test_migration_overlap_allows_one_guarded_cutover_then_frees_exact_slots(
    repo: tuple[Path, Path], policy: budget.AttemptPolicy
) -> None:
    checkout, _ = repo
    legacy = [
        seed(100 + index, BASE - timedelta(hours=24 - index))
        for index in range(18)
    ]
    initialize(checkout, policy, legacy)

    first = reserve(checkout, policy, 1000, BASE)
    assert first["request_mode"] == "reserved"
    assert first["active_attempts"] == 19
    assert first["active_legacy_attempts"] == 18

    blocked = reserve(checkout, policy, 1001, BASE + timedelta(minutes=30))
    assert blocked["request_mode"] == "cap_gated"
    assert blocked["available_at"] == budget._iso(BASE + timedelta(hours=1))

    freed = reserve(checkout, policy, 1002, BASE + timedelta(hours=1))
    assert freed["request_mode"] == "reserved"
    assert freed["active_attempts"] == 19
    assert freed["active_legacy_attempts"] == 17


def test_success_cadence_is_start_to_start_not_completion_to_start(
    repo: tuple[Path, Path], policy: budget.AttemptPolicy
) -> None:
    checkout, _ = repo
    initialize(checkout, policy, [seed(10, BASE - timedelta(hours=3))])
    started = reserve(checkout, policy, 2000, BASE)
    budget.mark_success(
        checkout,
        policy,
        attempt_id=str(started["attempt_id"]),
        durable_ref="b" * 40,
        actual_request_starts=711,
        now=BASE + timedelta(minutes=25),
        remote="origin",
    )

    before = reserve(checkout, policy, 2001, BASE + timedelta(minutes=119, seconds=59))
    assert before["request_mode"] == "success_gated"
    assert before["available_at"] == budget._iso(BASE + timedelta(minutes=120))
    due = reserve(checkout, policy, 2002, BASE + timedelta(minutes=120))
    assert due["request_mode"] == "reserved"


def test_failed_attempt_can_retry_at_30_minutes_and_reservation_survives_failure(
    repo: tuple[Path, Path], policy: budget.AttemptPolicy
) -> None:
    checkout, _ = repo
    initialize(checkout, policy, [seed(10, BASE - timedelta(hours=3))])
    first = reserve(checkout, policy, 3000, BASE)
    first_sha = str(first["budget_sha"])

    before = reserve(checkout, policy, 3001, BASE + timedelta(minutes=29, seconds=59))
    assert before["request_mode"] == "retry_gated"
    assert remote_sha(checkout, policy) == first_sha
    retry = reserve(checkout, policy, 3002, BASE + timedelta(minutes=30))
    assert retry["request_mode"] == "reserved"
    assert retry["active_attempts"] == 3


def test_read_only_observation_never_moves_branch_and_fails_closed(
    repo: tuple[Path, Path], policy: budget.AttemptPolicy
) -> None:
    checkout, _ = repo
    initialized = initialize(checkout, policy, [seed(10, BASE - timedelta(hours=3))])
    due = budget.observe(checkout, policy, now=BASE, remote="origin")
    assert due["observation_valid"] == "true"
    assert due["required"] == "true"
    assert due["latest_succeeded_at"] == budget._iso(
        BASE - timedelta(hours=2, minutes=40)
    )
    assert due["latest_durable_ref"] == REF
    assert remote_sha(checkout, policy) == initialized

    reserve(checkout, policy, 4000, BASE)
    gated_sha = remote_sha(checkout, policy)
    gated = budget.observe(checkout, policy, now=BASE + timedelta(minutes=1), remote="origin")
    assert gated["required"] == "false"
    assert gated["request_mode"] == "retry_gated"
    assert remote_sha(checkout, policy) == gated_sha


def test_mark_success_is_exact_idempotent_and_allowance_bounded(
    repo: tuple[Path, Path], policy: budget.AttemptPolicy
) -> None:
    checkout, _ = repo
    initialize(checkout, policy, [seed(10, BASE - timedelta(hours=3))])
    started = reserve(checkout, policy, 5000, BASE)
    first = budget.mark_success(
        checkout,
        policy,
        attempt_id=str(started["attempt_id"]),
        durable_ref="c" * 40,
        actual_request_starts=800,
        now=BASE + timedelta(minutes=20),
        remote="origin",
    )
    second = budget.mark_success(
        checkout,
        policy,
        attempt_id=str(started["attempt_id"]),
        durable_ref="c" * 40,
        actual_request_starts=800,
        now=BASE + timedelta(minutes=21),
        remote="origin",
    )
    assert second["budget_sha"] == first["budget_sha"]
    with pytest.raises(budget.AttemptBudgetError, match="reserved allowance"):
        budget.mark_success(
            checkout,
            policy,
            attempt_id=str(started["attempt_id"]),
            durable_ref="c" * 40,
            actual_request_starts=801,
            now=BASE + timedelta(minutes=22),
            remote="origin",
        )


def test_nonparentless_or_stale_exact_lease_cannot_mutate_budget(
    repo: tuple[Path, Path], policy: budget.AttemptPolicy
) -> None:
    checkout, _ = repo
    base_sha = initialize(checkout, policy, [seed(10, BASE - timedelta(hours=3))])
    base = budget.validate_ledger_ref(checkout, base_sha, policy).ledger
    rows = [dict(row) for row in base["attempts"]]
    rows.append(
        budget._attempt_row(
            attempt_id="winner-1",
            reserved_at=BASE,
            policy=policy,
            source="runtime",
            bound_proven=True,
            workflow_run_id="6000",
            workflow_run_attempt=1,
            event_name="schedule",
        )
    )
    winner = budget._create_commit(
        checkout, budget._new_ledger(rows, now=BASE, policy=policy), policy
    )
    stale = budget._create_commit(
        checkout,
        budget._new_ledger(
            [
                *[dict(row) for row in base["attempts"]],
                budget._attempt_row(
                    attempt_id="stale-1",
                    reserved_at=BASE,
                    policy=policy,
                    source="runtime",
                    bound_proven=True,
                    workflow_run_id="6001",
                    workflow_run_attempt=1,
                    event_name="schedule",
                ),
            ],
            now=BASE,
            policy=policy,
        ),
        policy,
    )
    budget._push(checkout, "origin", winner.commit_sha, base_sha, policy)
    with pytest.raises(budget.AttemptBudgetError, match="force-with-lease"):
        budget._push(checkout, "origin", stale.commit_sha, base_sha, policy)

    tree = git(checkout, "rev-parse", f"{winner.commit_sha}^{{tree}}")
    child = git(checkout, "commit-tree", tree, "-p", winner.commit_sha, input_text="bad\n")
    git(checkout, "push", "--force", "origin", f"{child}:refs/heads/{policy.branch}")
    with pytest.raises(budget.AttemptBudgetError, match="parentless"):
        budget.observe(checkout, policy, now=BASE + timedelta(minutes=31), remote="origin")


def test_runtime_attempt_ceiling_proves_rolling_request_bound(policy: budget.AttemptPolicy) -> None:
    attempts = [
        budget._attempt_row(
            attempt_id=f"runtime-{index}",
            reserved_at=BASE - timedelta(minutes=31 * (16 - index)),
            policy=policy,
            source="runtime",
            bound_proven=True,
            workflow_run_id=str(7000 + index),
            workflow_run_attempt=1,
            event_name="schedule",
        )
        for index in range(16)
    ]
    mode, _ = budget._request_mode(attempts, now=BASE, policy=policy)
    assert mode == "cap_gated"
    assert len(attempts) * policy.request_start_allowance == 12_800


def test_fresh_dns_publication_clock_cannot_mask_stale_durable_collection(
    policy: budget.AttemptPolicy,
) -> None:
    # A DNS-only publication may have generated_at=BASE, but that timestamp is
    # intentionally absent from the attempt-ledger decision. The most recent
    # durable Buildkite collection started 121 minutes ago, so recovery is due.
    fresh_publication_generated_at = budget._iso(BASE)
    attempt = budget._attempt_row(
        attempt_id="runtime-stale-success",
        reserved_at=BASE - timedelta(minutes=121),
        policy=policy,
        source="runtime",
        bound_proven=True,
        workflow_run_id="8000",
        workflow_run_attempt=1,
        event_name="schedule",
        succeeded_at=BASE - timedelta(minutes=96),
        durable_ref="d" * 40,
        actual_request_starts=700,
    )
    mode, available = budget._request_mode([attempt], now=BASE, policy=policy)
    assert fresh_publication_generated_at == "2026-09-01T12:00:00Z"
    assert mode == "reserved"
    assert available is None


def test_published_collector_failures_retry_at_thirty_minutes(repo, policy):
    checkout, _ = repo
    initialize(checkout, policy, [seed(10, BASE - timedelta(hours=3))])
    sha = published_state(checkout, failures=["ci_gating", "github_home"])
    record_publication(checkout, policy, state_sha=sha)
    unchanged = remote_sha(checkout, policy)

    gated = budget.observe(checkout, policy, now=BASE + timedelta(minutes=29, seconds=59), remote="origin")
    assert gated["request_mode"] == "retry_gated"
    assert gated["available_at"] == budget._iso(BASE + timedelta(minutes=30))
    assert gated["retry_surfaces"] == "ci_gating,github_home"
    assert gated["collection_retry_required"] == "true"
    due = budget.observe(checkout, policy, now=BASE + timedelta(minutes=30), remote="origin")
    assert due["required"] == "true"
    assert due["collection_evidence_status"] == "incomplete"
    assert remote_sha(checkout, policy) == unchanged
    retried = reserve(checkout, policy, 2001, BASE + timedelta(minutes=30))
    assert retried["request_mode"] == "reserved"
    assert retried["active_attempts"] == 3
    assert retried["rolling_reserved_request_starts"] == 1600
    assert retried["retry_surfaces"] == "ci_gating,github_home"

    # Failure before a durable publication must not lose the pending perf or
    # GitHub intent merely because the latest ledger row has no durable_ref.
    after_failed_retry = budget.observe(checkout, policy, now=BASE + timedelta(minutes=60), remote="origin")
    assert after_failed_retry["required"] == "true"
    assert after_failed_retry["retry_surfaces"] == "ci_gating,github_home"


@pytest.mark.parametrize("kwargs", [
    {}, {"failures": ["queue", "dns_health"]},
    {"fallback": ["ci_core", "ci_analytics"]},
    {"fresh": ["ci_core", "perf_eval"]},
])
def test_complete_or_intentionally_degraded_publications_keep_two_hour_cadence(repo, policy, kwargs):
    checkout, _ = repo
    initialize(checkout, policy, [seed(10, BASE - timedelta(hours=3))])
    sha = published_state(checkout, **kwargs)
    record_publication(checkout, policy, state_sha=sha)
    observed = budget.observe(checkout, policy, now=BASE + timedelta(minutes=30), remote="origin")
    assert observed["request_mode"] == "success_gated"
    assert observed["collection_retry_required"] == "false"
    assert observed["collection_evidence_status"] == "complete"
    assert observed["available_at"] == budget._iso(BASE + timedelta(minutes=120))


def test_legacy_success_migrates_exact_state_only_in_next_leased_reservation(repo, policy, monkeypatch):
    checkout, _ = repo
    initialize(checkout, policy, [seed(10, BASE - timedelta(hours=3))])
    failed_sha = published_state(checkout, failures=["perf_eval", "github_home"])
    record_publication(checkout, policy, state_sha=failed_sha, monkeypatch=monkeypatch)
    original_ledger = remote_sha(checkout, policy)
    old = next(row for row in budget.validate_ledger_ref(checkout, original_ledger, policy).ledger["attempts"] if row["source"] == "runtime")
    assert "collection_evidence" not in old
    # A later healthy-looking queue/DNS publication is not the old attempt's
    # durable_ref and cannot erase its typed collection failure.
    newer = published_state(checkout, generated_at=budget._iso(BASE + timedelta(minutes=20)))
    git(checkout, "update-ref", "refs/heads/dashboard-state", newer)
    observed = budget.observe(checkout, policy, now=BASE + timedelta(minutes=30), remote="origin")
    assert observed["required"] == "true"
    assert observed["latest_durable_ref"] == failed_sha
    assert observed["retry_surfaces"] == "github_home,perf_eval"
    assert remote_sha(checkout, policy) == original_ledger

    reserved = reserve(checkout, policy, 2001, BASE + timedelta(minutes=30))
    rows = budget.validate_ledger_ref(checkout, reserved["budget_sha"], policy).ledger["attempts"]
    migrated = next(row for row in rows if row["id"] == old["id"])
    assert {key: migrated[key] for key in old} == old
    assert migrated["collection_evidence"]["durable_ref"] == failed_sha
    # The compact proof is now durable; no historical-state fetch is needed.
    monkeypatch.setattr(budget, "read_collection_evidence", lambda *a, **kw: pytest.fail("proof already persisted"))
    assert budget.observe(checkout, policy, now=BASE + timedelta(minutes=31), remote="origin")["retry_surfaces"] == "github_home,perf_eval"


@pytest.mark.parametrize("corruption", ["manifest", "clock", "parent"])
def test_invalid_legacy_evidence_cannot_authorize_early_retry(repo, policy, monkeypatch, corruption):
    checkout, _ = repo
    initialize(checkout, policy, [seed(10, BASE - timedelta(hours=3))])
    parent = published_state(checkout) if corruption == "parent" else None
    sha = published_state(
        checkout, failures=["github_home"], corrupt_manifest=corruption == "manifest",
        generated_at=budget._iso(BASE - timedelta(minutes=1)) if corruption == "clock" else None,
        parent=parent,
    )
    record_publication(checkout, policy, state_sha=sha, monkeypatch=monkeypatch)
    observed = budget.observe(checkout, policy, now=BASE + timedelta(minutes=30), remote="origin")
    assert observed["request_mode"] == "success_gated"
    assert observed["collection_evidence_status"] == "unavailable"
    assert observed["retry_surfaces"] == ""
    assert reserve(checkout, policy, 2001, BASE + timedelta(minutes=30))["request_mode"] == "success_gated"


def test_recovery_clears_retry_intent_only_after_next_complete_publication(repo, policy):
    checkout, _ = repo
    initialize(checkout, policy, [seed(10, BASE - timedelta(hours=3))])
    failed = published_state(checkout, failures=["perf_eval"])
    record_publication(checkout, policy, state_sha=failed)
    retry = reserve(checkout, policy, 2001, BASE + timedelta(minutes=30))
    healthy = published_state(checkout, generated_at=budget._iso(BASE + timedelta(minutes=35)))
    budget.mark_success(
        checkout, policy, attempt_id=retry["attempt_id"], durable_ref=healthy,
        actual_request_starts=99, now=BASE + timedelta(minutes=40), remote="origin",
        require_collection_evidence=True,
    )
    observed = budget.observe(checkout, policy, now=BASE + timedelta(minutes=60), remote="origin")
    assert observed["request_mode"] == "success_gated"
    assert observed["retry_surfaces"] == ""
    assert observed["available_at"] == budget._iso(BASE + timedelta(minutes=150))


def test_incomplete_success_does_not_bypass_sixteen_attempt_cap(policy):
    rows = [budget._attempt_row(
        attempt_id=f"runtime-{number}", reserved_at=BASE - timedelta(minutes=31 + number * 30),
        policy=policy, source="runtime", bound_proven=True, workflow_run_id=str(number + 1),
        workflow_run_attempt=1, event_name="schedule", succeeded_at=BASE - timedelta(minutes=30),
        durable_ref=REF, actual_request_starts=1,
    ) for number in range(16)]
    rows[0]["collection_evidence"] = {
        "schema_version": 1, "durable_ref": REF, "publication_state_oid": "b" * 40,
        "retry_surfaces": ["github_home"],
    }
    assert budget._request_mode(rows, now=BASE, policy=policy)[0] == "cap_gated"
    assert sum(row["request_start_allowance"] for row in rows) == 12_800


def test_strict_mark_success_rejects_missing_evidence_before_ledger_mutation(repo, policy):
    checkout, _ = repo
    initialize(checkout, policy, [seed(10, BASE - timedelta(hours=3))])
    started = reserve(checkout, policy, 2000, BASE)
    before = remote_sha(checkout, policy)
    with pytest.raises(budget.AttemptBudgetError, match="durable collection evidence"):
        budget.mark_success(
            checkout, policy, attempt_id=started["attempt_id"], durable_ref=REF,
            actual_request_starts=10, now=BASE + timedelta(minutes=10), remote="origin",
            require_collection_evidence=True,
        )
    assert remote_sha(checkout, policy) == before


@pytest.mark.parametrize("elapsed", [timedelta(hours=25, minutes=31), timedelta(hours=50)])
def test_retry_intent_survives_expiry_of_original_published_attempt(repo, policy, monkeypatch, elapsed):
    checkout, _ = repo
    initialize(checkout, policy, [seed(10, BASE - timedelta(hours=3))])
    failed_sha = published_state(checkout, failures=["perf_eval"])
    record_publication(checkout, policy, state_sha=failed_sha)
    first_retry = reserve(checkout, policy, 2001, BASE + timedelta(minutes=30))
    assert first_retry["retry_surfaces"] == "perf_eval"
    # No subsequent attempt publishes. A later retry still carries the exact
    # unresolved proof, but expired executions remain outside the request cap.
    reserve(checkout, policy, 2002, BASE + timedelta(hours=24))
    monkeypatch.setattr(budget, "read_collection_evidence", lambda *a, **kw: pytest.fail("persisted intent needs no source fetch"))
    now = BASE + elapsed
    observed = budget.observe(checkout, policy, now=now, remote="origin")
    assert observed["required"] == "true"
    assert observed["retry_surfaces"] == "perf_eval"
    assert observed["collection_retry_required"] == "true"
    next_retry = reserve(checkout, policy, 2003, now)
    assert next_retry["retry_surfaces"] == "perf_eval"
    assert next_retry["active_attempts"] == (1 if elapsed > timedelta(hours=49) else 2)
    rows = budget.validate_ledger_ref(checkout, next_retry["budget_sha"], policy).ledger["attempts"]
    assert all(row["id"] not in {"data-2000-1", "data-2001-1"} for row in rows)
    assert rows[-1]["pending_collection_evidence"]["durable_ref"] == failed_sha
