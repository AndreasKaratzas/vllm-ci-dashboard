from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from vllm import amd_duration_regression_watcher as duration
from vllm import amd_main_failure_watcher as amd
from vllm import ci_main_failure_watcher as upstream
from vllm.ci.reliability_history import (
    LEGACY_OBSERVATION_DERIVED_FIELDS,
    build_all_main_reliability,
    hydrate_reliability_observations,
    validate_all_main_reliability,
)


def _amd_build(number, finished_at):
    return {
        "number": number,
        "finished_at": finished_at,
        "created_at": finished_at,
    }


def _amd_observation(number, result, observed_at, job_id, retried_in=""):
    build_url = f"https://buildkite.com/vllm/amd-ci/builds/{number}"
    row = {
        "source_pipeline": "amd-ci",
        "build_number": number,
        "build_url": build_url,
        "job_id": job_id,
        "job_url": f"{build_url}/steps/canvas?jid={job_id}&tab=output",
        "observed_at": observed_at,
        "started_at": observed_at,
        "finished_at": observed_at,
        "result": result,
        "eligible_for_reliability": True,
    }
    if retried_in:
        row["retry_evidence"] = {"retried_in_job_id": retried_in}
    return row


def _amd_group(group_id, observations, name="AMD group"):
    return {
        "group_id": group_id,
        "name": name,
        "raw_name": f"mi300_1: {name}",
        "step_key": group_id,
        "hardware": "mi300",
        "queue": "amd_mi300_1",
        "observations": observations,
    }


def test_current_amd_failure_watcher_uses_ci_gpu_groups_only(monkeypatch):
    now = datetime.now(timezone.utc)
    build = _raw_amd_watcher_build(90001, now - timedelta(hours=1), result="failed")
    build["web_url"] = "https://buildkite.com/vllm/ci/builds/90001"
    cuda = copy.deepcopy(build["jobs"][0])
    cuda.update(id="cuda-job", name=":nvidia: (H100) CUDA failure", agent_query_rules=["queue=gpu_1"])
    cuda["step"] = {"id": "cuda-step", "key": "cuda-step"}
    build["jobs"].append(cuda)
    reliability = build_all_main_reliability([build], pipeline_slug="ci", window_days=30,
                                           generated_at=now.isoformat(), collection_provenance={"exhaustive": True})
    assert validate_all_main_reliability(reliability, "ci")
    old = amd._default_state()
    old["active"] = {"retired": {"name": "Legacy failure", "hardware": "mi300", "queue": "amd_mi300_1", "job_url": "https://buildkite.com/vllm/amd-ci/builds/100#old"}}
    captured = []
    monkeypatch.setattr(amd, "_read_reliability", lambda pipeline: reliability if pipeline == "ci" else None)
    monkeypatch.setattr(amd, "_read_state", lambda path: old)
    monkeypatch.setattr(amd, "_write_state", lambda state, path, **kwargs: captured.append((state, path, kwargs)))
    assert amd.run_watcher(upstream.CONFIG) == 0
    active = captured[0][0]["active"]
    assert len(active) == 1
    assert all(row["hardware"].startswith("mi") for row in active.values())
    assert "retired" not in active
    assert captured[0][2]["state_filename"] == "open_ci_main_failure_issues.json"


def _amd_reliability(builds, groups, generated_at="2026-07-17T12:10:00Z"):
    return {
        "generated_at": generated_at,
        "builds": builds,
        "groups": groups,
    }


def _raw_amd_watcher_build(
    number,
    created_at,
    *,
    result="passed",
    wall_completion_mins=20,
    message="normalized watcher evidence",
):
    runnable_at = created_at + timedelta(minutes=1)
    started_at = created_at + timedelta(minutes=5)
    finished_at = started_at + timedelta(minutes=wall_completion_mins)

    def timestamp(value):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")

    failed = result in {"failed", "soft_fail"}
    return {
        "number": number,
        "branch": "main",
        "state": "failed" if failed else "passed",
        "commit": f"{number:040x}",
        "message": message,
        "created_at": timestamp(created_at),
        "started_at": timestamp(runnable_at),
        "finished_at": timestamp(finished_at + timedelta(minutes=1)),
        "web_url": f"https://buildkite.com/vllm/amd-ci/builds/{number}",
        "jobs": [
            {
                "id": f"normalized-job-{number}",
                "type": "script",
                "name": "mi300_1: Normalized watcher group",
                "state": "failed" if failed else "passed",
                "soft_failed": result == "soft_fail",
                "runnable_at": timestamp(runnable_at),
                "started_at": timestamp(started_at),
                "finished_at": timestamp(finished_at),
                "agent_query_rules": ["queue=amd_mi300_1"],
                "step": {
                    "id": f"normalized-step-{number}",
                    "key": "normalized-watcher-step",
                },
            }
        ],
    }


def _normalized_watcher_reliability(builds):
    return build_all_main_reliability(
        builds,
        pipeline_slug="amd-ci",
        window_days=30,
        generated_at="2026-07-17T12:00:00Z",
    )


def _legacy_watcher_reliability(normalized):
    legacy = copy.deepcopy(normalized)
    legacy["schema_version"] = 1
    for normalized_group, legacy_group in zip(
        normalized["groups"], legacy["groups"], strict=True
    ):
        legacy_group["observations"] = hydrate_reliability_observations(
            normalized,
            normalized_group["observations"],
            pipeline_slug="amd-ci",
        )
    return legacy


def test_amd_watcher_schema_v2_matches_legacy_hydrated_evidence_and_links():
    message = "Merge queue: preserve normalized watcher popup evidence"
    normalized = _normalized_watcher_reliability(
        [
            _raw_amd_watcher_build(
                610,
                datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc),
                result="failed",
                message=message,
            )
        ]
    )
    legacy = _legacy_watcher_reliability(normalized)

    assert validate_all_main_reliability(normalized, "amd-ci")
    assert validate_all_main_reliability(legacy, "amd-ci")
    stored = normalized["groups"][0]["observations"][0]
    assert not (set(LEGACY_OBSERVATION_DERIVED_FIELDS) & stored.keys())

    normalized_evidence = amd._observations_by_build(normalized)
    legacy_evidence = amd._observations_by_build(legacy)
    assert normalized_evidence == legacy_evidence

    normalized_state = amd.advance_incidents(normalized, amd._default_state())
    legacy_state = amd.advance_incidents(legacy, amd._default_state())
    assert normalized_state == legacy_state

    incident = next(iter(normalized_state["active"].values()))
    build_url = "https://buildkite.com/vllm/amd-ci/builds/610"
    job_url = f"{build_url}/steps/canvas?jid=normalized-job-610&tab=output"
    assert incident["build_url"] == build_url
    assert incident["job_url"] == job_url
    assert incident["build_commit"] == f"{610:040x}"
    assert incident["build_message"] == message


def test_amd_watcher_initializes_from_latest_build_then_resolves_on_pass():
    reliability = _amd_reliability(
        [
            _amd_build(10, "2026-07-17T10:00:00Z"),
            _amd_build(11, "2026-07-17T11:00:00Z"),
        ],
        [
            _amd_group("old", [_amd_observation(10, "failed", "2026-07-17T09:50:00Z", "old-fail")]),
            _amd_group(
                "current",
                [_amd_observation(11, "soft_fail", "2026-07-17T10:50:00Z", "current-soft")],
            ),
        ],
    )

    initialized = amd.advance_incidents(reliability, amd._default_state())

    assert set(initialized["processed_build_numbers"]) == {10, 11}
    assert initialized["schema_version"] == 2
    assert initialized["active"] == {}
    assert set(initialized["pending_soft"]) == {"current"}
    assert initialized["pending_soft"]["current"]["transition"]["soft_streak"] == 1

    reliability["builds"].append(_amd_build(12, "2026-07-17T12:00:00Z"))
    reliability["groups"][1]["observations"].insert(
        0,
        _amd_observation(12, "soft_fail", "2026-07-17T11:50:00Z", "current-soft-2"),
    )
    confirmed = amd.advance_incidents(reliability, initialized)

    assert set(confirmed["active"]) == {"current"}
    transition = confirmed["active"]["current"]["transition"]
    assert transition["status"] == "confirmed"
    assert transition["soft_streak"] == 2
    assert confirmed["pending_soft"] == {}

    reliability["builds"].append(_amd_build(13, "2026-07-17T13:00:00Z"))
    reliability["groups"][1]["observations"].insert(
        0,
        _amd_observation(13, "passed", "2026-07-17T12:50:00Z", "current-pass"),
    )
    resolved = amd.advance_incidents(reliability, confirmed)

    assert resolved["active"] == {}
    assert resolved["pending_soft"] == {}
    assert set(resolved["processed_build_numbers"]) == {10, 11, 12, 13}


def test_amd_watcher_latest_retry_attempt_wins_inside_build():
    reliability = _amd_reliability(
        [_amd_build(20, "2026-07-17T12:00:00Z")],
        [
            _amd_group(
                "retried",
                [
                    _amd_observation(20, "passed", "2026-07-17T11:50:00Z", "retry-pass"),
                    _amd_observation(
                        20, "failed", "2026-07-17T11:50:00Z", "retry-fail", "retry-pass"
                    ),
                ],
            ),
        ],
    )

    state = amd.advance_incidents(reliability, amd._default_state())

    assert state["active"] == {}
    assert state["pending_soft"] == {}


def test_amd_watcher_hard_failure_confirms_immediately_and_absence_holds():
    reliability = _amd_reliability(
        [_amd_build(21, "2026-07-17T11:00:00Z")],
        [
            _amd_group(
                "hard-group",
                [_amd_observation(21, "failed", "2026-07-17T10:50:00Z", "hard-21")],
            )
        ],
    )

    confirmed = amd.advance_incidents(reliability, amd._default_state())
    assert confirmed["active"]["hard-group"]["transition"] == {
        "status": "confirmed",
        "severity": "hard",
        "peak_severity": "hard",
        "soft_streak": 0,
        "last_eligible_build_id": "21",
        "incident_start_build_id": "21",
        "confirmed_build_id": "21",
    }

    reliability["builds"].append(_amd_build(22, "2026-07-17T12:00:00Z"))
    held = amd.advance_incidents(reliability, confirmed)

    assert held["active"] == confirmed["active"]
    assert set(held["processed_build_numbers"]) == {21, 22}


def test_amd_watcher_pass_clears_one_soft_without_confirming_issue():
    reliability = _amd_reliability(
        [_amd_build(25, "2026-07-17T11:00:00Z")],
        [
            _amd_group(
                "soft-then-pass",
                [_amd_observation(25, "soft_fail", "2026-07-17T10:50:00Z", "soft-25")],
            )
        ],
    )
    pending = amd.advance_incidents(reliability, amd._default_state())
    assert set(pending["pending_soft"]) == {"soft-then-pass"}
    assert pending["active"] == {}

    reliability["builds"].append(_amd_build(26, "2026-07-17T12:00:00Z"))
    reliability["groups"][0]["observations"].insert(
        0,
        _amd_observation(26, "passed", "2026-07-17T11:50:00Z", "pass-26"),
    )
    cleared = amd.advance_incidents(reliability, pending)

    assert cleared["pending_soft"] == {}
    assert cleared["active"] == {}


def test_amd_watcher_sorts_unprocessed_builds_before_soft_confirmation():
    reliability = _amd_reliability(
        [
            _amd_build(32, "2026-07-17T12:00:00Z"),
            _amd_build(31, "2026-07-17T11:00:00Z"),
        ],
        [
            _amd_group(
                "out-of-list-order",
                [
                    _amd_observation(32, "soft_fail", "2026-07-17T11:50:00Z", "soft-32"),
                    _amd_observation(31, "soft_fail", "2026-07-17T10:50:00Z", "soft-31"),
                ],
            )
        ],
    )
    already_initialized = amd._default_state() | {"initialized": True}

    confirmed = amd.advance_incidents(reliability, already_initialized)

    transition = confirmed["active"]["out-of-list-order"]["transition"]
    assert transition["soft_streak"] == 2
    assert transition["incident_start_build_id"] == "31"
    assert transition["confirmed_build_id"] == "32"

    duplicate = amd.advance_incidents(reliability, confirmed)
    assert duplicate["active"] == confirmed["active"]


def test_amd_watcher_ignores_older_build_discovered_after_newer_result():
    reliability = _amd_reliability(
        [_amd_build(40, "2026-07-17T12:00:00Z")],
        [
            _amd_group(
                "late-old-build",
                [_amd_observation(40, "soft_fail", "2026-07-17T11:50:00Z", "soft-40")],
            )
        ],
    )
    pending = amd.advance_incidents(reliability, amd._default_state())

    late_old_build = _amd_build(39, "2026-07-17T13:00:00Z")
    late_old_build["created_at"] = "2026-07-17T11:00:00Z"
    reliability["builds"].append(late_old_build)
    reliability["groups"][0]["observations"].append(
        _amd_observation(39, "soft_fail", "2026-07-17T10:50:00Z", "late-soft-39")
    )
    held = amd.advance_incidents(reliability, pending)

    row = held["pending_soft"]["late-old-build"]
    assert row["build_number"] == 40
    assert row["transition"]["soft_streak"] == 1
    assert held["group_watermarks"]["late-old-build"]["build_number"] == 40


def test_amd_watcher_prunes_order_fences_older_than_retained_catalog():
    state = amd._default_state()
    state["initialized"] = True
    state["group_watermarks"] = {
        "retired-group": {
            "build_number": 1,
            "order_at": "2026-06-01T00:00:00Z",
            "created_at": "2026-06-01T00:00:00Z",
            "finished_at": "2026-06-01T01:00:00Z",
            "result": "passed",
            "commit": "",
        }
    }
    reliability = _amd_reliability(
        [_amd_build(40, "2026-07-17T12:00:00Z")],
        [],
    )

    updated = amd.advance_incidents(reliability, state)

    assert updated["group_watermarks"] == {}


def test_amd_watcher_migrates_v1_order_fence_before_processing_late_build():
    reliability = _amd_reliability(
        [
            _amd_build(50, "2026-07-17T12:00:00Z"),
            _amd_build(49, "2026-07-17T11:00:00Z"),
        ],
        [
            _amd_group(
                "resolved-before-migration",
                [
                    _amd_observation(50, "passed", "2026-07-17T11:50:00Z", "pass-50"),
                    _amd_observation(49, "failed", "2026-07-17T10:50:00Z", "late-fail-49"),
                ],
            )
        ],
    )
    schema_v1_state = {
        "schema_version": 1,
        "initialized": True,
        "processed_build_numbers": [50],
        "active": {},
        "group_watermarks": {},
    }

    migrated = amd.advance_incidents(reliability, schema_v1_state)

    assert migrated["active"] == {}
    assert migrated["pending_soft"] == {}
    assert migrated["group_watermarks"]["resolved-before-migration"]["build_number"] == 50


def test_amd_watcher_migrates_legacy_active_rows_as_confirmed():
    reliability = _amd_reliability(
        [_amd_build(24, "2026-07-17T12:00:00Z")],
        [],
    )
    legacy = {
        "schema_version": 1,
        "initialized": True,
        "processed_build_numbers": [24],
        "active": {
            "legacy-soft": _amd_group(
                "legacy-soft",
                [],
            )
            | {
                "result": "soft_fail",
                "build_number": 24,
                "observed_at": "2026-07-17T11:50:00Z",
            }
        },
    }

    migrated = amd.advance_incidents(reliability, legacy)

    transition = migrated["active"]["legacy-soft"]["transition"]
    assert migrated["schema_version"] == 2
    assert transition["status"] == "confirmed"
    assert transition["peak_severity"] == "soft"
    assert migrated["signal_fingerprint_version"] == 1


def _ci_build(number, finished_at, commit, *, created_at=None):
    return {
        "number": number,
        "finished_at": finished_at,
        "created_at": created_at or finished_at,
        "commit": commit,
    }


def _ci_observation(number, result, observed_at, job_id, commit):
    build_url = f"https://buildkite.com/vllm/ci/builds/{number}"
    return {
        "source_pipeline": "ci",
        "build_number": number,
        "build_url": build_url,
        "build_commit": commit,
        "job_id": job_id,
        "job_url": f"{build_url}/steps/canvas?jid={job_id}&tab=output",
        "observed_at": observed_at,
        "started_at": observed_at,
        "finished_at": observed_at,
        "result": result,
        "eligible_for_reliability": True,
    }


def _ci_group(group_id, observations):
    return {
        "group_id": group_id,
        "name": "Upstream group",
        "raw_name": "amd_mi300_1: MI group",
        "step_key": "upstream-step",
        "hardware": "mi300",
        "queue": "amd_mi300_1",
        "observations": observations,
    }


def test_mi_scope_migration_purges_foreign_incidents_and_fences():
    mi = {**_ci_group("mi", []), "result": "failed", "job_url": "https://buildkite.com/vllm/ci/builds/93523#mi"}
    cuda = {**mi, "name": "B200 failure", "raw_name": ":nvidia: (B200) CUDA", "hardware": "b200", "queue": "gpu_1"}
    cpu = {**mi, "name": "CPU failure", "raw_name": ":computer: (CPU) CPU", "queue": "amd_mi300_1"}
    unproved_cpu = {**mi, "name": "Old CPU-only source on MI queue"}
    reliability = {"groups": [_ci_group("mi", [])], "builds": [], "hardware_scope": "amd_mi_gpu"}
    state = {**upstream._default_state(), "initialized": True,
             "active": {"mi": mi, "cuda": cuda, "cpu": cpu, "source-cpu": unproved_cpu},
             "pending_soft": {"cuda-soft": cuda},
             "group_watermarks": {key: {"number": 93523} for key in ("mi", "cuda", "cpu", "source-cpu", "cuda-soft")}}
    migrated = upstream.advance_incidents(reliability, state)
    assert set(migrated["active"]) == {"mi"}
    assert migrated["pending_soft"] == {}
    assert set(migrated["group_watermarks"]) == {"mi"}
    assert migrated["hardware_scope"] == "amd_mi_gpu"


def test_tokenless_run_advances_state_and_clears_the_retired_issue(monkeypatch):
    """The watcher needs no GitHub token; freshness consumers read last_run."""
    now = datetime.now(timezone.utc)
    build = _raw_amd_watcher_build(90001, now - timedelta(hours=1))
    build["web_url"] = "https://buildkite.com/vllm/ci/builds/90001"
    reliability = build_all_main_reliability(
        [build], pipeline_slug="ci", window_days=30,
        generated_at=now.isoformat(), collection_provenance={"exhaustive": True},
    )
    assert validate_all_main_reliability(reliability, "ci")
    old = {
        **upstream._default_state(),
        "initialized": True,
        "hardware_scope": "amd_mi_gpu",
        "issue": {"number": 540, "opened_at": ""},
        "suppressed": True,
        "last_run": "2026-01-01T00:00:00Z",
    }
    saved = []
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(amd, "_read_reliability", lambda _pipeline: reliability)
    monkeypatch.setattr(amd, "_read_state", lambda _path: old)
    monkeypatch.setattr(amd, "_write_state", lambda state, _path, **_kwargs: saved.append(state))

    assert upstream.run() == 0
    assert len(saved) == 1
    assert saved[0]["last_run"] == reliability["generated_at"]
    assert saved[0]["hardware_scope"] == "amd_mi_gpu"
    assert saved[0]["schema_version"] == 2
    assert saved[0]["signal_fingerprint_version"] == 2
    assert saved[0]["issue"] is None
    assert saved[0]["suppressed"] is False


def test_upstream_watcher_retains_last_good_and_first_bad_commit():
    good = "a" * 40
    first_bad = "b" * 40
    later_bad = "c" * 40
    recovered_commit = "d" * 40
    reliability = {
        "generated_at": "2026-07-17T12:10:00Z",
        "builds": [
            _ci_build(30, "2026-07-17T10:00:00Z", good),
            _ci_build(31, "2026-07-17T11:00:00Z", first_bad),
        ],
        "groups": [
            _ci_group(
                "upstream-group",
                [
                    _ci_observation(31, "failed", "2026-07-17T10:50:00Z", "bad-31", first_bad),
                    _ci_observation(30, "passed", "2026-07-17T09:50:00Z", "good-30", good),
                ],
            )
        ],
    }

    initialized = upstream.advance_incidents(reliability, upstream._default_state())
    incident = initialized["active"]["upstream-group"]

    assert incident["good_commit"] == good
    assert incident["good_build_number"] == 30
    assert incident["bad_commit"] == first_bad
    assert incident["bad_build_number"] == 31
    assert incident["commit_range_status"] == "candidate"
    assert incident["compare_url"].endswith(f"/compare/{good}...{first_bad}")
    assert incident["bisect_command"] == f"git bisect start {first_bad} {good}"

    reliability["generated_at"] = "2026-07-17T13:10:00Z"
    reliability["builds"].append(_ci_build(32, "2026-07-17T12:00:00Z", later_bad))
    reliability["groups"][0]["observations"].insert(
        0,
        _ci_observation(32, "soft_fail", "2026-07-17T11:50:00Z", "bad-32", later_bad),
    )
    continued = upstream.advance_incidents(reliability, initialized)
    incident = continued["active"]["upstream-group"]
    assert incident["good_commit"] == good
    assert incident["bad_commit"] == first_bad
    assert incident["latest_bad_commit"] == later_bad
    assert incident["transition"]["severity"] == "soft"
    assert incident["transition"]["peak_severity"] == "hard"
    assert incident["transition"]["incident_start_build_id"] == "31"

    reliability["generated_at"] = "2026-07-17T14:10:00Z"
    reliability["builds"].append(_ci_build(33, "2026-07-17T13:00:00Z", recovered_commit))
    reliability["groups"][0]["observations"].insert(
        0,
        _ci_observation(
            33,
            "passed",
            "2026-07-17T12:50:00Z",
            "good-33",
            recovered_commit,
        ),
    )
    recovered = upstream.advance_incidents(reliability, continued)
    assert recovered["active"] == {}


def test_upstream_watcher_confirms_soft_on_second_build_and_keeps_first_bad():
    good = "a" * 40
    first_soft = "b" * 40
    second_soft = "c" * 40
    reliability = {
        "generated_at": "2026-07-17T12:10:00Z",
        "builds": [
            _ci_build(60, "2026-07-17T09:00:00Z", good),
            _ci_build(61, "2026-07-17T10:00:00Z", first_soft),
        ],
        "groups": [
            _ci_group(
                "soft-upstream-group",
                [
                    _ci_observation(
                        61,
                        "soft_fail",
                        "2026-07-17T09:50:00Z",
                        "soft-61",
                        first_soft,
                    ),
                    _ci_observation(60, "passed", "2026-07-17T08:50:00Z", "good-60", good),
                ],
            )
        ],
    }

    pending = upstream.advance_incidents(reliability, upstream._default_state())

    assert pending["active"] == {}
    pending_row = pending["pending_soft"]["soft-upstream-group"]
    assert pending_row["transition"]["soft_streak"] == 1
    assert pending_row["bad_commit"] == first_soft
    assert pending_row["good_commit"] == good

    reliability["builds"].append(_ci_build(62, "2026-07-17T11:00:00Z", second_soft))
    reliability["groups"][0]["observations"].insert(
        0,
        _ci_observation(
            62,
            "soft_fail",
            "2026-07-17T10:50:00Z",
            "soft-62",
            second_soft,
        ),
    )
    confirmed = upstream.advance_incidents(reliability, pending)

    incident = confirmed["active"]["soft-upstream-group"]
    assert confirmed["pending_soft"] == {}
    assert incident["bad_commit"] == first_soft
    assert incident["latest_bad_commit"] == second_soft
    assert incident["transition"]["incident_start_build_id"] == "61"
    assert incident["transition"]["confirmed_build_id"] == "62"


def test_upstream_watcher_orders_commit_range_by_build_creation():
    good = "1" * 40
    bad = "2" * 40
    reliability = {
        "generated_at": "2026-07-17T13:10:00Z",
        "builds": [
            _ci_build(
                40,
                "2026-07-17T12:30:00Z",
                good,
                created_at="2026-07-17T10:00:00Z",
            ),
            _ci_build(
                41,
                "2026-07-17T11:30:00Z",
                bad,
                created_at="2026-07-17T11:00:00Z",
            ),
        ],
        "groups": [
            _ci_group(
                "out-of-order-group",
                [
                    _ci_observation(41, "failed", "2026-07-17T11:20:00Z", "bad-41", bad),
                    _ci_observation(40, "passed", "2026-07-17T12:20:00Z", "good-40", good),
                ],
            )
        ],
    }

    state = upstream.advance_incidents(reliability, upstream._default_state())

    incident = state["active"]["out-of-order-group"]
    assert incident["good_commit"] == good
    assert incident["bad_commit"] == bad


def test_upstream_watcher_initializes_from_each_groups_latest_outcome():
    bad = "1" * 40
    recovered = "2" * 40
    reliability = {
        "generated_at": "2026-07-17T13:10:00Z",
        "builds": [
            _ci_build(45, "2026-07-17T11:30:00Z", bad),
            _ci_build(46, "2026-07-17T12:30:00Z", recovered),
        ],
        "groups": [
            _ci_group(
                "recovered-before-enable-group",
                [
                    _ci_observation(46, "passed", "2026-07-17T12:20:00Z", "good-46", recovered),
                    _ci_observation(45, "failed", "2026-07-17T11:20:00Z", "bad-45", bad),
                ],
            )
        ],
    }

    state = upstream.advance_incidents(reliability, upstream._default_state())

    assert state["active"] == {}
    assert state["group_watermarks"]["recovered-before-enable-group"]["build_number"] == 46


def test_upstream_watcher_ignores_older_build_that_finishes_late():
    bad = "3" * 40
    old_good = "2" * 40
    recovered_commit = "4" * 40
    reliability = {
        "generated_at": "2026-07-17T12:10:00Z",
        "builds": [
            _ci_build(
                51,
                "2026-07-17T11:30:00Z",
                bad,
                created_at="2026-07-17T11:00:00Z",
            )
        ],
        "groups": [
            _ci_group(
                "late-build-group",
                [_ci_observation(51, "failed", "2026-07-17T11:20:00Z", "bad-51", bad)],
            )
        ],
    }
    initial = upstream.advance_incidents(reliability, upstream._default_state())
    assert "late-build-group" in initial["active"]

    reliability["generated_at"] = "2026-07-17T13:10:00Z"
    reliability["builds"].append(
        _ci_build(
            50,
            "2026-07-17T12:30:00Z",
            old_good,
            created_at="2026-07-17T10:00:00Z",
        )
    )
    reliability["groups"][0]["observations"].append(
        _ci_observation(50, "passed", "2026-07-17T12:20:00Z", "old-good-50", old_good)
    )
    after_late_pass = upstream.advance_incidents(reliability, initial)
    assert "late-build-group" in after_late_pass["active"]
    incident = after_late_pass["active"]["late-build-group"]
    assert incident["good_commit"] == old_good
    assert incident["bad_commit"] == bad

    reliability["generated_at"] = "2026-07-17T14:10:00Z"
    reliability["builds"].append(
        _ci_build(
            52,
            "2026-07-17T13:30:00Z",
            recovered_commit,
            created_at="2026-07-17T12:00:00Z",
        )
    )
    reliability["groups"][0]["observations"].insert(
        0,
        _ci_observation(
            52,
            "passed",
            "2026-07-17T13:20:00Z",
            "recovered-52",
            recovered_commit,
        ),
    )
    recovered = upstream.advance_incidents(reliability, after_late_pass)
    assert recovered["active"] == {}


def test_alert_payload_freshness_fails_closed():
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)

    assert amd._is_fresh({"generated_at": "2026-07-17T10:00:00Z"}, now)
    assert not amd._is_fresh({"generated_at": "2026-07-17T08:00:00Z"}, now)
    assert duration._is_fresh({"generated_at": "2026-07-17T10:00:00Z"}, now)
    assert not duration._is_fresh({"generated_at": "2026-07-17T08:00:00Z"}, now)
    assert upstream._is_fresh({"generated_at": "2026-07-17T10:00:00Z"}, now)
    assert not upstream._is_fresh({"generated_at": "2026-07-17T08:00:00Z"}, now)
