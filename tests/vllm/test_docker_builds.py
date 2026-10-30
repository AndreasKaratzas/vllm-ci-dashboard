"""ROCm docker build job projection, retention, and public-contract tests."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vllm.ci import docker_builds as db


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 29, 23, 0, tzinfo=timezone.utc)
JOB_ID = "01a189f5-7d74-4c8e-b409-18544a6f7abb"


def node(**overrides) -> dict:
    value = {
        "uuid": JOB_ID,
        "state": "FINISHED",
        "step": {"key": "refresh-rocm-base-amd"},
        "label": ":docker: Ensure ROCm stack + runtime",
        "command": "bash .buildkite/scripts/ensure.sh",
        "createdAt": "2026-10-29T22:18:49.438Z",
        "runnableAt": "2026-10-29T22:18:50.771Z",
        "startedAt": "2026-10-29T22:18:54.904Z",
        "finishedAt": "2026-10-29T22:20:21.509Z",
        "retried": False,
        "passed": True,
        "exitStatus": "0",
        "agentQueryRules": ["queue=amd-cpu"],
        "agent": {"hostname": "amd-cpu-builder-7"},
        "build": {
            "number": 15742,
            "branch": "github.com/someone/vllm/tree/private-branch",
            "message": "PR #61837 /ci run by @someone",
            "commit": "a" * 40,
            "pullRequest": None,
        },
    }
    value.update(overrides)
    return value


def row(index: int, created_at: datetime, **overrides) -> db.JobRow:
    value = db.JobRow(
        id=str(uuid.UUID(int=index)),
        build=1000 + index,
        pipeline="ci",
        step="image-build-amd",
        state="passed",
        source="main",
        retried=False,
        created_at=created_at,
        wait_s=3,
        run_s=840,
        exit=0,
    )
    return replace(value, **overrides)


def complete(rows: list[db.JobRow]) -> dict:
    return db.encode_public(
        rows,
        generated_at=NOW,
        window_days=30,
        coverage=db.complete_coverage(NOW - timedelta(days=30)),
    )


def test_normalize_projects_allowlist_only_and_drops_branch() -> None:
    projected = db.normalize_node(node(), "amd-ci")

    assert projected == db.JobRow(
        id=JOB_ID,
        build=15742,
        pipeline="amd-ci",
        step="refresh-rocm-base-amd",
        state="passed",
        source="other",
        retried=False,
        created_at=datetime(2026, 10, 29, 22, 18, 49, tzinfo=timezone.utc),
        wait_s=4,
        run_s=86,
        exit=0,
    )
    text = json.dumps(complete([projected]))
    for private in ("private-branch", "someone", "builder", "ensure.sh", "a" * 40, "61837"):
        assert private not in text


@pytest.mark.parametrize(
    ("state", "passed", "exit_status", "expected", "expected_exit"),
    [
        ("FINISHED", False, "1", "failed", 1),
        ("CANCELED", False, "-1", "canceled", -1),
        ("TIMED_OUT", False, None, "timed_out", None),
        ("RESERVED", False, None, "running", None),
        ("LIMITED", False, None, "pending", None),
        ("WAITING_FAILED", False, None, "other", None),
        ("SOMETHING_NEW", False, "oops", "other", None),
    ],
)
def test_state_and_exit_mapping(state, passed, exit_status, expected, expected_exit) -> None:
    projected = db.normalize_node(
        node(state=state, passed=passed, exitStatus=exit_status, startedAt=None, finishedAt=None),
        "ci",
    )

    assert projected is not None
    assert (projected.state, projected.exit) == (expected, expected_exit)
    assert projected.run_s is None and projected.wait_s is None


def test_wait_falls_back_to_created_at_without_runnable_at() -> None:
    projected = db.normalize_node(node(runnableAt=None), "ci")

    assert projected is not None and projected.wait_s == 5


def test_source_classification_main_pr_other() -> None:
    def source(branch: str, pull_request: dict | None) -> str:
        build = {"number": 1, "branch": branch, "pullRequest": pull_request}
        projected = db.normalize_node(node(build=build), "ci")
        assert projected is not None
        return projected.source

    assert source("main", None) == "main"
    assert source("main", {"id": "61837"}) == "pr"
    assert source("someone:feature", {"id": "61837"}) == "pr"
    assert source("releases/v0.12.0", None) == "other"


@pytest.mark.parametrize(
    "overrides",
    [
        {"agentQueryRules": ["queue=medium_cpu_queue_premerge"]},
        {"agentQueryRules": []},
        {"step": {"key": "build-cpu-image"}},
        {"step": None},
        {"uuid": "not-a-uuid"},
        {"createdAt": None},
        {"build": {"number": True, "branch": "main", "pullRequest": None}},
    ],
)
def test_drops_non_amd_cpu_and_unknown_step(overrides: dict) -> None:
    assert db.normalize_node(node(**overrides), "ci") is None


def test_drops_unknown_pipeline() -> None:
    assert db.normalize_node(node(), "vllm/ci") is None


def test_merge_dedupes_by_id_fresh_wins_and_prunes_retention() -> None:
    old = row(1, NOW - timedelta(days=30, seconds=1))
    kept = row(2, NOW - timedelta(days=29))
    stale = row(3, NOW - timedelta(hours=2), state="running", run_s=None)
    fresh = replace(stale, state="passed", run_s=600)
    newest = row(4, NOW - timedelta(minutes=5))

    merged = db.merge_rows([old, kept, stale], [fresh, newest], now=NOW)

    assert merged == [newest, fresh, kept]


def test_refetch_floor_covers_oldest_running_job_bounded_48h() -> None:
    finished = row(1, NOW - timedelta(hours=30))
    assert db.refetch_floor([finished], NOW) == NOW - timedelta(hours=10)

    running = row(2, NOW - timedelta(hours=20), state="running")
    assert db.refetch_floor([finished, running], NOW) == NOW - timedelta(hours=20)

    stuck = row(3, NOW - timedelta(days=5), state="pending")
    assert db.refetch_floor([stuck], NOW) == NOW - timedelta(hours=48)


def test_encode_decode_roundtrip_columnar() -> None:
    rows = [
        row(1, NOW - timedelta(days=2), pipeline="amd-ci", step="refresh-rocm-base-amd"),
        row(2, NOW - timedelta(hours=1), source="pr", retried=True, exit=1, state="failed"),
        row(3, NOW - timedelta(hours=1), state="running", wait_s=None, run_s=None, exit=None),
    ]

    payload = complete(rows)

    assert db.validate_public(payload) == []
    jobs = payload["jobs"]
    assert jobs["n"] == 3
    assert jobs["id"] == [rows[1].id, rows[2].id, rows[0].id]
    assert jobs["created"] == [30 * 86400 - 3600, 30 * 86400 - 3600, 28 * 86400]
    assert jobs["pipeline"] == [1, 1, 0]
    assert jobs["step"] == [1, 1, 2]
    assert jobs["state"] == [1, 4, 0]
    assert jobs["source"] == [1, 0, 0]
    assert jobs["retried"] == [1, 0, 0]
    assert jobs["run_s"] == [840, None, 840]
    assert db.decode_public(payload) == [rows[1], rows[2], rows[0]]
    assert complete(db.decode_public(payload)) == payload


def test_encode_refuses_rows_before_the_window() -> None:
    with pytest.raises(ValueError, match="predates"):
        complete([row(1, NOW - timedelta(days=31))])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["jobs"]["id"].__setitem__(0, "https://buildkite.com/vllm/ci/builds/1"),
        lambda p: p["coverage"].__setitem__("status", "someone@example.com"),
        lambda p: p["coverage"].__setitem__("status", "bkua_0123456789"),
        lambda p: p["jobs"]["run_s"].append(1),
        lambda p: p["jobs"]["step"].__setitem__(0, 3),
        lambda p: p["jobs"].__setitem__("branch", ["main", "main"]),
        lambda p: p.__setitem__("evidence", []),
        lambda p: p["jobs"]["created"].reverse(),
        lambda p: p["coverage"]["pipelines"].pop("ci"),
        lambda p: p["window"].__setitem__("from", "2026-09-30T23:00:00Z"),
    ],
)
def test_validate_rejects_url_email_token_and_length_mismatch(mutate) -> None:
    payload = complete([row(1, NOW - timedelta(days=1)), row(2, NOW - timedelta(days=2))])
    mutate(payload)

    assert db.validate_public(payload)
    with pytest.raises(ValueError):
        db.decode_public(payload)


def _set(path: tuple, value):
    def mutate(payload: dict, monkeypatch) -> None:
        target = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _not_collected_with_rows(payload: dict, monkeypatch) -> None:
    payload["coverage"] = {"status": "not_collected", "refetch_hours": 10, "pipelines": {}}
    payload["window"]["from"] = payload["window"]["to"]


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda p, mp: p["jobs"]["id"].__setitem__(1, p["jobs"]["id"][0]), "jobs.id must be unique"),
        (_set(("jobs", "build", 0), 0), "jobs.build must hold positive integers"),
        (_set(("jobs", "retried", 0), 2), "jobs.retried must hold 0 or 1"),
        (_set(("jobs", "run_s", 0), -1), "jobs.run_s must hold non-negative integers or null"),
        (_set(("jobs", "exit", 0), 1.5), "jobs.exit must hold integers or null"),
        (_set(("coverage", "refetch_hours"), 0), "coverage.refetch_hours is out of range"),
        (_not_collected_with_rows, "not_collected coverage must be empty"),
        (_set(("generated_at",), "2026-10-29T22:00:00Z"), "window.to must equal generated_at"),
        (lambda p, mp: p["dims"]["state"].reverse(), "dims differ from the contract"),
        (
            lambda p, mp: mp.setattr(db, "PUBLIC_OUTPUT_MAX_BYTES", 100),
            "payload is {size} bytes, above 100",
        ),
        (_set(("schema_version",), 2), "schema_version is unsupported"),
        (
            _set(("coverage", "pipelines", "ci", "complete_from"), "2026-10-29T23:00:00Z"),
            "coverage.pipelines.ci does not cover the window",
        ),
        (_set(("coverage", "status"), "https://x"), "payload contains forbidden marker 'http'"),
        (_set(("coverage", "status"), "bkua_0123"), "payload contains forbidden marker 'bkua_'"),
        (_set(("coverage", "status"), "a@b"), "payload contains forbidden marker '@'"),
    ],
    ids=[
        "duplicate-id",
        "build-0",
        "retried-2",
        "run-negative",
        "exit-float",
        "refetch-0",
        "not-collected-rows",
        "window-to",
        "dims",
        "oversize",
        "schema-2",
        "complete-from-late",
        "http",
        "bkua",
        "at",
    ],
)
def test_validate_rejects_with_the_specific_error(mutate, expected, monkeypatch) -> None:
    payload = complete([row(1, NOW - timedelta(days=1)), row(2, NOW - timedelta(days=2))])
    mutate(payload, monkeypatch)

    errors = db.validate_public(payload)

    assert expected.format(size=len(db.serialize_public(payload))) in errors


def test_normalize_clamps_a_finish_before_the_start_to_zero_run_time() -> None:
    projected = db.normalize_node(node(finishedAt="2026-10-29T22:18:50.000Z"), "ci")

    assert projected is not None and projected.run_s == 0


def test_public_size_within_writer_budget_at_30_days_of_peak_volume() -> None:
    rows = [
        row(
            index,
            NOW - timedelta(seconds=index * 100),
            build=100_000 + index,
            wait_s=index % 400,
            run_s=index % 7_200,
            exit=index % 2,
        )
        for index in range(25_000)
    ]
    rows = [replace(item, id=str(uuid.UUID(int=(index + 1) * 0x9E3779B97F4A7C15)))
            for index, item in enumerate(rows)]

    payload = complete(rows)

    assert db.validate_public(payload) == []
    assert len(db.serialize_public(payload)) < db.PUBLIC_OUTPUT_MAX_BYTES // 2


def test_seed_payload_is_not_collected() -> None:
    seed = db.seed_payload(NOW)

    assert db.validate_public(seed) == []
    assert seed["coverage"] == {"status": "not_collected", "refetch_hours": 10, "pipelines": {}}
    assert seed["window"] == {
        "days": 30,
        "from": "2026-10-29T23:00:00Z",
        "to": "2026-10-29T23:00:00Z",
    }
    assert seed["jobs"]["n"] == 0
    assert db.decode_public(seed) == []


def test_committed_seed_matches_the_writer() -> None:
    committed = (ROOT / db.PUBLIC_PATH).read_bytes()
    payload = json.loads(committed)

    assert db.validate_public(payload) == []
    assert committed == db.serialize_public(payload)


def _attempt(run_id: int, minutes_ago: int, *, source: str = "runtime") -> dict:
    return {
        "id": f"docker-{run_id}-1",
        "reserved_at": (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "workflow_run_id": str(run_id),
        "workflow_run_attempt": 1,
        "succeeded_at": None,
    }


def _bootstrap(attempts, runs, *, branch=False):
    from vllm import docker_builds_bootstrap

    return docker_builds_bootstrap.classify(
        data_branch_exists=branch,
        attempts=attempts,
        max_attempts=16,
        fetch_run=lambda run_id, _attempt: runs[run_id],
        now=NOW,
    )["state"]


def test_bootstrap_is_pending_when_the_latest_completed_run_saved_progress():
    attempts = [_attempt(0, 300, source="legacy_migration"), _attempt(1, 120), _attempt(2, 60), _attempt(3, 5)]
    runs = {
        "1": {"status": "completed", "conclusion": "failure"},
        "2": {"status": "completed", "conclusion": "success"},
        "3": {"status": "in_progress", "conclusion": None},
    }
    assert _bootstrap(attempts, runs) == "pending"


def test_bootstrap_with_a_failed_latest_run_is_not_pending():
    attempts = [_attempt(1, 120), _attempt(2, 60)]
    runs = {
        "1": {"status": "completed", "conclusion": "success"},
        "2": {"status": "completed", "conclusion": "failure"},
    }
    assert _bootstrap(attempts, runs) == "failed"
    assert _bootstrap([_attempt(3, 5)], {"3": {"status": "queued"}}) == "unknown"


def test_bootstrap_escalates_once_unpublished_attempts_reach_the_budget():
    attempts = [_attempt(run_id, 30 * run_id) for run_id in range(1, 17)]
    runs = {str(run_id): {"status": "completed", "conclusion": "success"} for run_id in range(1, 17)}
    assert _bootstrap(attempts[:15], runs) == "pending"
    assert _bootstrap(attempts, runs) == "over-budget"


def test_bootstrap_ends_once_the_data_branch_exists():
    runs = {"1": {"status": "completed", "conclusion": "success"}}
    assert _bootstrap([_attempt(1, 60)], runs, branch=True) == "collected"
