"""Tests for per-physical-agent (node) AMD CI health tracking.

Covers:
- ``log_parser.extract_node`` / ``node_from_agent`` — physical-node extraction.
- ``constants.amd_gpu_hardware`` — AMD GPU queue scoping.
- ``collect_agent_health`` — all-builds observation scoping, state classification,
  infra-suspect determination, and per-node/day rollups.
- ``build_operations_snapshot._amd_agent_health`` — pass-through of the assembled
  ``agent_health.json`` block.
- The frontend co-failure clustering (ported to JS) via a quickjs parity check.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vllm import build_operations_snapshot as ops
from vllm import collect_agent_health as ah
from vllm.audit_dashboard_data import DashboardAudit
from vllm.buildkite_request_guard import BuildkiteRequestAllowanceExhausted
from vllm.constants import amd_gpu_hardware
from vllm.ci import buildkite_client
from vllm.ci.log_parser import extract_node, node_from_agent


NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# extract_node / node_from_agent
# --------------------------------------------------------------------------- #

_REAL_BANNER = (
    "\x1b_bk;t=1784019766616\x07=== Pod: buildkite-019f5fdb-00b1-49b5-9253-5bd88b4879bd-55rwd"
    " | Node: chi-mi325x-pod2-032 | Tue Jul 14 09:02:46 UTC 2026 ==="
)


def test_extract_node_real_banner():
    assert extract_node(_REAL_BANNER) == "chi-mi325x-pod2-032"


def test_extract_node_ignores_topology_decoys():
    log = "  Node:                    0\n  Internal Node ID:        0\n"
    assert extract_node(log) == ""


def test_extract_node_absent():
    assert extract_node("no node marker here") == ""
    assert extract_node(None) == ""


def test_node_from_agent_reads_k8s_tag():
    job = {"agent": {"meta_data": ["queue=amd_mi300_1", "k8s:node=gpu9124"]}}
    assert node_from_agent(job) == "gpu9124"


def test_node_from_agent_absent():
    assert node_from_agent({"agent": {"meta_data": ["queue=amd-cpu"]}}) == ""
    assert node_from_agent({}) == ""


# --------------------------------------------------------------------------- #
# constants.amd_gpu_hardware — GPU-only scope
# --------------------------------------------------------------------------- #

def test_amd_gpu_hardware_scopes_gpu_only():
    assert amd_gpu_hardware("amd_mi300_1") == "MI300"
    assert amd_gpu_hardware("amd_mi325_4") == "MI325"
    assert amd_gpu_hardware("amd_mi355_1") == "MI355"
    # CPU steps, NVIDIA, and retired queue families are out of scope.
    assert amd_gpu_hardware("amd-cpu") == ""
    assert amd_gpu_hardware("gpu_4_queue") == ""
    assert amd_gpu_hardware("amd_mi250_8") == "MI250"
    assert amd_gpu_hardware("amd_mi355b_1") == ""
    assert amd_gpu_hardware("") == ""


# --------------------------------------------------------------------------- #
# collect_agent_health helpers
# --------------------------------------------------------------------------- #

def _job(job_id, name, state, queue, node, start, end, soft=False):
    agent_meta = ["queue=" + queue]
    if node:
        agent_meta.append("k8s:node=" + node)
    return {
        "id": job_id,
        "name": name,
        "state": state,
        "soft_failed": soft,
        "exit_status": 0 if state == "passed" else 1,
        "agent_query_rules": ["queue=" + queue],
        "agent": {"meta_data": agent_meta},
        "started_at": start,
        "finished_at": end,
    }


def _build(number=100, branch="main", message="PR: fix thing", created="2026-07-14T09:00:00Z",
           state="finished"):
    return {"number": number, "branch": branch, "message": message, "created_at": created,
            "state": state}


NIGHTLY_RE = re.compile(r"^AMD Full CI Run\s*-\s*nightly(?:\s|$)", re.IGNORECASE)


def test_queue_of_reads_agent_query_rules():
    assert ah._queue_of({"agent_query_rules": ["queue=amd_mi300_1"]}) == "amd_mi300_1"
    assert ah._queue_of({"agent": {"meta_data": ["queue=amd_mi250_4"]}}) == "amd_mi250_4"
    assert ah._queue_of({}) == ""


def test_run_state_classification():
    assert ah._run_state({"state": "passed"}) == "pass"
    assert ah._run_state({"state": "failed"}) == "hard"
    assert ah._run_state({"state": "timed_out"}) == "hard"
    assert ah._run_state({"state": "canceled"}) == "canceled"
    assert ah._run_state({"state": "passed", "soft_failed": True}) == "soft"
    assert ah._run_state({"state": "running"}) == "skip"


def test_observe_scopes_amd_gpu_only():
    b = _build()
    # AMD GPU job on a node -> observed.
    row = ah._observe("amd-ci", b, _job("j1", "G", "failed", "amd_mi300_1", "gpu9124",
                                        "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"), NIGHTLY_RE)
    assert row is not None and row["node"] == "gpu9124" and row["hardware"] == "MI300"
    assert row["state"] == "hard" and row["is_main"] is True
    # NVIDIA job with a node tag -> excluded (non-AMD-GPU queue).
    assert ah._observe("ci", b, _job("jx", "N", "failed", "gpu_4_queue", "nvidia-1",
                                     "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"), None) is None
    # amd-cpu bootstrap -> excluded.
    assert ah._observe("amd-ci", b, _job("jc", "boot", "passed", "amd-cpu", "cpu-1",
                                         "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"), NIGHTLY_RE) is None
    # retired mi355b -> excluded.
    assert ah._observe("amd-ci", b, _job("jr", "R", "failed", "amd_mi355b_1", "n1",
                                         "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"), NIGHTLY_RE) is None


def test_observe_skips_never_run_and_keeps_canceled():
    b = _build()
    # blocked/never-run job (no node, no start) -> skipped.
    blocked = _job("jb", "B", "blocked", "amd_mi300_1", "", "", "")
    blocked["started_at"] = ""
    blocked["agent"]["meta_data"] = ["queue=amd_mi300_1"]
    assert ah._observe("amd-ci", b, blocked, NIGHTLY_RE) is None
    # canceled job is preserved with a distinct state.
    row = ah._observe("amd-ci", b, _job("jc", "C", "canceled", "amd_mi300_1", "gpu9124",
                                        "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"), NIGHTLY_RE)
    assert row is not None and row["state"] == "canceled"


def test_observe_flags_superseded_build_failures():
    job = _job("j1", "G", "failed", "amd_mi300_1", "gpu9124",
               "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z")
    # Failure in a live build -> not flagged.
    row = ah._observe("amd-ci", _build(state="finished"), job, NIGHTLY_RE)
    assert row["build_canceled"] is False
    assert ah._failing_row(dict(row, infra_suspect=True))["bc"] == 0
    # Same failure in a canceled/superseded build -> flagged for the toggle.
    row = ah._observe("amd-ci", _build(state="canceled"), job, NIGHTLY_RE)
    assert row["build_canceled"] is True
    assert ah._failing_row(dict(row, infra_suspect=True))["bc"] == 1


def test_failing_row_carries_infra_suspect_flag():
    # The signal toggle relies on `i` (1=infra-suspect subset, 0=general failure).
    row = ah._observe("amd-ci", _build(state="finished"),
                      _job("j1", "G", "failed", "amd_mi300_1", "gpu9124",
                           "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"), NIGHTLY_RE)
    assert ah._failing_row(dict(row, infra_suspect=True))["i"] == 1
    assert ah._failing_row(dict(row, infra_suspect=False))["i"] == 0
    assert ah._failing_row(row)["i"] == 0  # missing flag defaults to general


def test_observe_nightly_flag_requires_main_and_pattern():
    nightly_msg = "AMD Full CI Run - nightly (2026-07-14)"
    # nightly name + main branch -> nightly True.
    row = ah._observe("amd-ci", _build(branch="main", message=nightly_msg),
                      _job("j1", "G", "passed", "amd_mi300_1", "n1",
                           "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"), NIGHTLY_RE)
    assert row["nightly"] is True
    # nightly name but PR branch -> nightly False.
    row = ah._observe("amd-ci", _build(branch="user:pr", message=nightly_msg),
                      _job("j2", "G", "passed", "amd_mi300_1", "n1",
                           "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"), NIGHTLY_RE)
    assert row["nightly"] is False and row["is_main"] is False


def test_mark_infra_suspect_isolates_anomalous_failures():
    # "Healthy" group: passes on nodes A,B,C, fails once on D -> D's failure is infra-suspect.
    obs = []
    for i, node in enumerate(["A", "B", "C"]):
        obs.append({"group": "G", "day": "2026-07-14", "node": node, "state": "pass"})
    fail_d = {"group": "G", "day": "2026-07-14", "node": "D", "state": "hard"}
    obs.append(fail_d)
    # "Broken" group: fails everywhere -> not infra-suspect (code bug).
    broken = [{"group": "B2", "day": "2026-07-14", "node": n, "state": "hard"} for n in ("A", "B", "C")]
    obs.extend(broken)
    ah._mark_infra_suspect(obs)
    assert fail_d["infra_suspect"] is True
    assert all(r.get("infra_suspect") is False for r in broken)


def test_mark_infra_suspect_needs_pass_on_other_node():
    # Group passes only on the SAME node that also failed -> not infra-suspect.
    obs = [
        {"group": "G", "day": "2026-07-14", "node": "A", "state": "pass"},
        {"group": "G", "day": "2026-07-14", "node": "A", "state": "pass"},
        {"group": "G", "day": "2026-07-14", "node": "A", "state": "hard"},
    ]
    ah._mark_infra_suspect(obs)
    assert obs[-1]["infra_suspect"] is False


def test_rollup_rows_split_buckets():
    obs = [
        {"node": "A", "day": "2026-07-14", "hardware": "MI300", "state": "pass", "nightly": True},
        {"node": "A", "day": "2026-07-14", "hardware": "MI300", "state": "soft", "nightly": False},
        {"node": "A", "day": "2026-07-14", "hardware": "MI300", "state": "hard", "nightly": False},
        {"node": "A", "day": "2026-07-14", "hardware": "MI300", "state": "canceled", "nightly": False},
    ]
    rollups = ah._rollup_rows(obs)
    row = rollups[("A", "2026-07-14")]
    # bucket = [runs, soft, hard, canceled].
    # all bucket: 4 runs, 1 soft, 1 hard, 1 canceled; nightly bucket: 1 run, rest 0.
    assert row["a"] == [4, 1, 1, 1]
    assert row["n"] == [1, 0, 0, 0]


def test_incremental_build_fetch_is_bounded_exact_and_deduplicated(monkeypatch):
    barrier = threading.Barrier(ah.MAX_INCREMENTAL_SLICE_WORKERS)
    lock = threading.Lock()
    active = 0
    max_active = 0
    calls = []
    build = _build()
    build["jobs"] = [
        _job(
            "j1",
            "G",
            "passed",
            "amd_mi300_1",
            "node-1",
            "2026-07-14T09:00:00Z",
            "2026-07-14T09:05:00Z",
        )
    ]

    def paginate(_url, params):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            calls.append(dict(params))
        barrier.wait(timeout=2)
        with lock:
            active -= 1
        # Deliberate duplicate proves slice aggregation cannot double-count.
        return [build]

    monkeypatch.setattr(ah, "_paginate", paginate)
    rows = ah._fetch_pipeline_builds(
        "https://api.buildkite.com/v2/organizations/vllm/pipelines/amd-ci/builds",
        NOW - timedelta(days=3), NOW,
        3,
    )

    assert max_active == ah.MAX_INCREMENTAL_SLICE_WORKERS
    assert len(rows) == 1
    assert rows[0]["jobs"][0]["id"] == "j1"
    assert sorted(
        (params["created_from"], params["created_to"])
        for params in calls
    ) == [
        ("2026-07-11T12:00:00+00:00", "2026-07-12T12:00:00+00:00"),
        ("2026-07-12T12:00:00+00:00", "2026-07-13T12:00:00+00:00"),
        ("2026-07-13T12:00:00+00:00", "2026-07-14T12:00:00+00:00"),
    ]
    assert all(
        params["per_page"] == 100 and params["exclude_pipeline"] == "true"
        for params in calls
    )


def test_long_backfill_keeps_single_paginated_query(monkeypatch):
    calls = []

    def paginate(_url, params):
        calls.append(dict(params))
        return []

    monkeypatch.setattr(ah, "_paginate", paginate)
    assert ah._fetch_pipeline_builds(
        "https://api.buildkite.com/v2/organizations/vllm/pipelines/ci/builds",
        NOW - timedelta(days=60), NOW,
        60,
    ) == []
    assert calls == [{
        "per_page": 100,
        "exclude_pipeline": "true",
        "created_from": "2026-05-15T12:00:00+00:00",
    }]


def test_upstream_incremental_bounds_page_payload_without_extra_slices(monkeypatch):
    calls = []

    def paginate(_url, params):
        calls.append(dict(params))
        return []

    monkeypatch.setattr(ah, "_paginate", paginate)
    assert ah._fetch_pipeline_builds(
        "https://api.buildkite.com/v2/organizations/vllm/pipelines/ci/builds",
        NOW - timedelta(days=3), NOW,
        3,
        incremental_per_page=ah.UPSTREAM_INCREMENTAL_PER_PAGE,
    ) == []

    # Preserve the three exact daily slice roots (the request ceiling before
    # Link pagination) while halving only the response-heavy upstream pages.
    assert len(calls) == 3
    assert {params["per_page"] for params in calls} == {
        ah.UPSTREAM_INCREMENTAL_PER_PAGE
    }
    assert ah.UPSTREAM_INCREMENTAL_PER_PAGE == 50
    assert all(params["exclude_pipeline"] == "true" for params in calls)
    assert sorted(
        (params["created_from"], params["created_to"])
        for params in calls
    ) == [
        ("2026-07-11T12:00:00+00:00", "2026-07-12T12:00:00+00:00"),
        ("2026-07-12T12:00:00+00:00", "2026-07-13T12:00:00+00:00"),
        ("2026-07-13T12:00:00+00:00", "2026-07-14T12:00:00+00:00"),
    ]


def test_incremental_slice_failure_is_fail_closed(monkeypatch):
    barrier = threading.Barrier(ah.MAX_INCREMENTAL_SLICE_WORKERS)

    def paginate(_url, params):
        barrier.wait(timeout=2)
        if params["created_from"] == "2026-07-12T12:00:00+00:00":
            raise RuntimeError("slice failed after retries")
        return []

    monkeypatch.setattr(ah, "_paginate", paginate)
    with pytest.raises(RuntimeError, match="slice failed after retries"):
        ah._fetch_pipeline_builds(
            "https://api.buildkite.com/v2/organizations/vllm/pipelines/ci/builds",
            NOW - timedelta(days=3), NOW, 3,
        )


# --------------------------------------------------------------------------- #
# Bounded, atomic retained generation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("payload", "message"),
    (
        ('{"d":"2026-07-13"}\n{not-json}\n', "ledger is invalid"),
        ('{"d":"2026-07-13"}\n[]\n', "row is not an object"),
        ('<<<<<<< ours\n{"d":"2026-07-13"}\n', "ledger is invalid"),
    ),
)
def test_agent_health_retained_ledger_corruption_fails_closed(
    tmp_path,
    payload,
    message,
):
    path = tmp_path / "node_days.jsonl"
    path.write_text(payload)

    with pytest.raises(RuntimeError, match=message):
        ah._load_jsonl(path)


def test_agent_health_retained_ledger_allows_blank_lines(tmp_path):
    path = tmp_path / "node_days.jsonl"
    row = _retained_node_day("2026-07-13")
    path.write_text("\n" + json.dumps(row) + "\n\n")

    assert ah._load_jsonl(path) == [row]

def _retained_node_day(day: str, suffix: str = "node") -> dict:
    return {
        "nd": f"{suffix}-{day}",
        "h": "MI300",
        "d": day,
        "a": [10, 1, 2, 0],
        "n": [2, 0, 1, 0],
    }


def _retained_failure(day: str, index: int, *, padding: int = 0) -> dict:
    return {
        "nd": f"node-{index % 4}",
        "h": "MI300",
        "p": "amd-ci",
        "q": "amd_mi300_1",
        "g": f"group-{index}-" + ("x" * padding),
        "s": "hard",
        "ng": False,
        "i": index % 2,
        "bc": 0,
        "b": 1000 + index,
        "j": f"job-{day}-{index}",
        "t": f"{day}T12:{index % 60:02d}:00Z",
        "e": f"{day}T12:{index % 60:02d}:30Z",
        "d": day,
    }


def _run_scoped_collection(monkeypatch, tmp_path, *, pipeline=None, fail=False, fetch_fn=None, days=3):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    calls = []

    def fetch(slug, days, *, query_time):
        calls.append((slug, days, query_time))
        if fail:
            raise RuntimeError("source request failed")
        row = ah._observe(
            slug, _build(),
            _job(f"new-{slug}", "current group", "failed", "amd_mi300_1", "current-node",
                 "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"),
            NIGHTLY_RE,
        )
        assert row is not None
        return [row]

    argv = ["collect_agent_health.py", "--output", str(tmp_path)]
    if days is not None:
        argv.extend(["--days", str(days)])
    if pipeline is not None:
        argv.extend(["--pipeline", pipeline])
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(ah, "datetime", Clock)
    monkeypatch.setattr(ah.cfg, "BK_TOKEN", "unit-test-token")
    monkeypatch.setattr(ah, "_fetch_pipeline_observations", fetch_fn or fetch)
    assert ah.main() == 0
    return calls, json.loads((tmp_path / ah.OUTPUT_JSON).read_text())


def _seed_scoped_history(tmp_path, pipelines, *, generated_at="2026-07-13T12:00:00Z"):
    store = tmp_path / ah.STORE_SUBDIR
    store.mkdir()
    node = _retained_node_day("2026-07-10")
    failures = [_retained_failure("2026-07-10", 1), _retained_failure("2026-07-10", 2)]
    failures[1]["p"] = "ci"
    (store / ah.NODE_DAYS_JSONL).write_bytes(ah._encoded_jsonl([node]))
    (store / ah.INFRA_FAILURES_JSONL).write_bytes(ah._encoded_jsonl(failures))
    (tmp_path / ah.OUTPUT_JSON).write_text(json.dumps({
        "pipelines": pipelines,
        "generated_at": generated_at,
        "retention": {"pipeline_scope": {"collected_from": "2026-05-10T12:00:00Z"}},
    }))
    return node, failures


def _expected_job_scope():
    return {
        "version": 1,
        "basis": "terminal_jobs_by_started_at",
        "collected_from": "2026-07-11T00:00:00Z",
        "collected_to": "2026-07-14T12:00:00Z",
        "requested_days": 3,
        "exhaustive": True,
        "discovery_legs": {"created": True, "older_finished": True, "older_active": True},
        "active_build_states": ["creating", "scheduled", "running", "failing", "blocked", "canceling"],
        "attempt_policy": "latest_attempt_per_step",
        "terminal_time_policy": "finished_at_or_terminal_build_bound_for_canceled",
        "complete_window": False,
    }


def _window_build(number, created, *, state="passed", start="2026-07-14T09:00:00Z",
                  finish="2026-07-14T09:05:00Z", job_state="passed", build_finish=None):
    build = _build(number=number, created=created, state=state)
    build["finished_at"] = build_finish
    build["jobs"] = [_job(str(number), "current group", job_state, "amd_mi300_1",
                          "current-node", start, finish)]
    return build


def _window_page(builds, calls):
    def page(_url, params):
        calls.append(dict(params))
        selected = []
        for build in builds:
            created = datetime.fromisoformat(build["created_at"].replace("Z", "+00:00"))
            if "created_from" in params and created < datetime.fromisoformat(params["created_from"]):
                continue
            if "created_to" in params and created >= datetime.fromisoformat(params["created_to"]):
                continue
            if "finished_from" in params:
                finish = build.get("finished_at")
                if not finish or datetime.fromisoformat(finish.replace("Z", "+00:00")) < datetime.fromisoformat(params["finished_from"]):
                    continue
            if "state[]" in params and not (
                build["state"] in params["state[]"]
                or ("blocked" in params["state[]"] and build.get("blocked"))
            ):
                continue
            selected.append(build)
        return selected
    return page


def test_started_job_discovery_includes_older_finished_and_unfinished_builds(monkeypatch):
    builds = [
        _window_build(1, "2026-07-05T00:00:00Z", build_finish="2026-07-14T10:00:00Z"),
        _window_build(2, "2026-06-05T00:00:00Z", state="running"),
        _window_build(3, "2026-07-14T08:00:00Z"),
        _window_build(4, "2026-06-01T00:00:00Z", build_finish="2026-06-01T01:00:00Z"),
    ]
    # REST retains the prior state for blocked builds; its blocked flag remains
    # queryable through the documented state[]=blocked filter.
    builds[3]["blocked"] = True
    calls = []
    monkeypatch.setattr(ah, "_paginate", _window_page(builds, calls))

    rows = ah._fetch_pipeline_observations("ci", 3, query_time=NOW)

    assert {row["job_id"] for row in rows} == {"1", "2", "3", "4"}
    assert len(calls) == 6  # four aligned daily roots plus the two old-build legs
    old_finished = next(params for params in calls if "finished_from" in params)
    old_active = next(params for params in calls if "state[]" in params)
    assert old_finished["finished_from"] == old_finished["created_to"] == "2026-07-11T00:00:00+00:00"
    assert old_active["created_to"] == "2026-07-11T00:00:00+00:00"
    assert old_active["state[]"] == list(ah.ACTIVE_BUILD_STATES)
    assert all(params["exclude_pipeline"] == "true" for params in calls)
    assert all("exclude_jobs" not in params and "include_paused" not in params for params in calls)


def test_default_agent_health_fresh_proof_covers_seven_day_display(monkeypatch, tmp_path):
    calls, payload = _run_scoped_collection(monkeypatch, tmp_path, days=None)

    assert [days for _, days, _ in calls] == [7]
    scope = payload["retention"]["pipeline_scope"]
    assert scope["requested_days"] == payload["default_window_days"] == 7
    assert scope["collected_from"] == "2026-07-07T00:00:00Z"
    assert scope["collected_to"] == "2026-07-14T12:00:00Z"
    assert not scope["complete_window"]


def test_seven_day_job_window_uses_bounded_daily_roots_and_projects_each_slice(monkeypatch):
    calls = []
    raw = _window_build(1, "2026-07-14T08:00:00Z")
    raw["unneeded_large_metadata"] = "x" * 100_000

    def page(_url, params):
        calls.append(dict(params))
        return [raw]

    projected = []

    def project(build):
        projected.append(build["number"])
        return {"number": build["number"], "observation_count": len(build["jobs"])}

    monkeypatch.setattr(ah, "_paginate", page)
    result = ah._fetch_pipeline_builds(
        "https://api.buildkite.com/v2/organizations/vllm/pipelines/ci/builds",
        ah._job_window_start(NOW, 7), NOW, 7,
        incremental_per_page=ah.UPSTREAM_INCREMENTAL_PER_PAGE,
        project=project,
    )

    assert ah.MAX_INCREMENTAL_SLICE_DAYS == 7
    assert ah.MAX_INCREMENTAL_SLICE_WORKERS == 3
    assert len(calls) == len(projected) == 8
    assert {params["per_page"] for params in calls} == {50}
    assert sorted((p["created_from"], p["created_to"]) for p in calls)[0] == (
        "2026-07-07T00:00:00+00:00", "2026-07-08T00:00:00+00:00",
    )
    assert sorted((p["created_from"], p["created_to"]) for p in calls)[-1] == (
        "2026-07-14T00:00:00+00:00", "2026-07-14T12:00:00+00:00",
    )
    assert result == [{"number": 1, "observation_count": 1}]
    assert "unneeded_large_metadata" not in result[0]


def test_started_job_overlap_deduplicates_later_build_snapshot(monkeypatch):
    early = _window_build(1, "2026-07-14T08:00:00Z", job_state="failed")
    late = _window_build(1, "2026-07-14T08:00:00Z")

    def page(_url, params):
        return [late] if "state[]" in params else [early]

    monkeypatch.setattr(ah, "_paginate", page)

    rows = ah._fetch_pipeline_observations("ci", 3, query_time=NOW)

    assert len(rows) == 1 and rows[0]["state"] == "pass"


def test_started_job_whole_day_refresh_preserves_morning_counts_on_repeat(monkeypatch, tmp_path):
    builds = [
        _window_build(1, "2026-07-11T01:00:00Z", start="2026-07-11T02:00:00Z", finish="2026-07-11T02:05:00Z"),
        _window_build(2, "2026-07-11T13:00:00Z", start="2026-07-11T14:00:00Z", finish="2026-07-11T14:05:00Z"),
    ]
    calls = []
    monkeypatch.setattr(ah, "_paginate", _window_page(builds, calls))
    actual_fetch = ah._fetch_pipeline_observations
    _, first = _run_scoped_collection(monkeypatch, tmp_path, fetch_fn=actual_fetch)
    _, second = _run_scoped_collection(monkeypatch, tmp_path, fetch_fn=actual_fetch)

    assert first["total_runs"] == second["total_runs"] == 2
    assert first["node_days"] == second["node_days"]
    assert second["retention"]["pipeline_scope"]["collected_from"] == "2026-07-11T00:00:00Z"
    assert any(params.get("created_from") == "2026-07-11T00:00:00+00:00" for params in calls)


@pytest.mark.parametrize("leg", ["older_finished", "older_active"])
@pytest.mark.parametrize("error", [RuntimeError, BuildkiteRequestAllowanceExhausted])
def test_started_job_leg_failure_preserves_entire_generation(monkeypatch, tmp_path, leg, error):
    _seed_scoped_history(tmp_path, ["ci"])
    paths = [tmp_path / ah.OUTPUT_JSON, *sorted((tmp_path / ah.STORE_SUBDIR).iterdir())]
    before = {path: path.read_bytes() for path in paths}

    def page(_url, params):
        if (leg == "older_finished" and "finished_from" in params) or (leg == "older_active" and "state[]" in params):
            raise error("incomplete old-build discovery")
        return []

    monkeypatch.setattr(ah, "_paginate", page)
    with pytest.raises(error, match="incomplete old-build discovery"):
        _run_scoped_collection(monkeypatch, tmp_path, fetch_fn=ah._fetch_pipeline_observations)

    assert {path: path.read_bytes() for path in paths} == before


def test_started_job_old_build_pagination_cap_preserves_entire_generation(monkeypatch, tmp_path):
    _seed_scoped_history(tmp_path, ["ci"])
    paths = [tmp_path / ah.OUTPUT_JSON, *sorted((tmp_path / ah.STORE_SUBDIR).iterdir())]
    before = {path: path.read_bytes() for path in paths}
    old_pages = []

    class Response:
        def __init__(self, links):
            self.links = links

        def json(self):
            return []

    def request(url, params=None):
        if (params and "state[]" in params) or "?page=" in url:
            old_pages.append(url)
            return Response({"next": {"url": url.split("?")[0] + f"?page={len(old_pages) + 1}"}})
        return Response({})

    monkeypatch.setattr(buildkite_client, "_request", request)
    with pytest.raises(RuntimeError, match="100-page safety cap"):
        _run_scoped_collection(monkeypatch, tmp_path, fetch_fn=ah._fetch_pipeline_observations)

    assert len(old_pages) == buildkite_client.PAGINATION_SAFETY_CAP
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("invalid_field,value", [
    ("number", True), ("number", 0), ("number", -1), ("number", "1"),
    ("jobs", None), ("jobs", {}), ("jobs", ["not a job"]), ("missing_jobs", None),
])
def test_malformed_embedded_roster_preserves_entire_generation(
    monkeypatch, tmp_path, invalid_field, value,
):
    _seed_scoped_history(tmp_path, ["ci"])
    paths = [tmp_path / ah.OUTPUT_JSON, *sorted((tmp_path / ah.STORE_SUBDIR).iterdir())]
    before = {path: path.read_bytes() for path in paths}
    malformed = _window_build(1, "2026-07-05T00:00:00Z")
    if invalid_field == "missing_jobs":
        del malformed["jobs"]
    else:
        malformed[invalid_field] = value

    def page(_url, params):
        return [malformed] if "state[]" in params else []

    monkeypatch.setattr(ah, "_paginate", page)
    with pytest.raises(RuntimeError, match="agent-health discovery returned an invalid"):
        _run_scoped_collection(monkeypatch, tmp_path, fetch_fn=ah._fetch_pipeline_observations)

    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("start,finish,job_state,expected", [
    ("2026-07-10T23:59:59Z", "2026-07-14T09:05:00Z", "passed", False),
    ("2026-07-14T12:00:00Z", "2026-07-14T12:00:00Z", "passed", False),
    ("2026-07-14T09:00:00Z", "2026-07-14T12:00:01Z", "passed", False),
    ("2026-07-14T09:00:00Z", "2026-07-14T12:00:00Z", "passed", True),
    ("2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z", "canceled", True),
    ("", "", "canceled", False),
])
def test_started_job_window_excludes_unexecuted_outside_and_future_terminal_runs(start, finish, job_state, expected):
    build = _window_build(1, "2026-07-14T08:00:00Z", start=start, finish=finish, job_state=job_state)
    observed = ah._observe_in_window("ci", build, build["jobs"][0], None, NOW - timedelta(days=3), NOW)
    assert (observed is not None) is expected


@pytest.mark.parametrize("field,value", [
    ("started_at", "invalid"), ("started_at", "2026-07-14T09:00:00"),
    ("started_at", None), ("finished_at", "invalid"),
    ("finished_at", "2026-07-14T09:05:00"), ("finished_at", None),
    ("finished_at", "2026-07-14T08:59:00Z"),
])
def test_started_terminal_timestamp_ambiguity_refuses_exact_generation(field, value):
    build = _window_build(1, "2026-07-14T08:00:00Z")
    job = build["jobs"][0]
    job[field] = value
    with pytest.raises(RuntimeError, match="agent-health terminal job"):
        ah._observe_in_window("ci", build, job, None, NOW - timedelta(days=3), NOW)


@pytest.mark.parametrize("state", ["passed", "failed", "timed_out", "soft_failed"])
def test_missing_started_execution_without_agent_preserves_prior_generation(
    monkeypatch, tmp_path, state,
):
    _seed_scoped_history(tmp_path, ["ci"])
    paths = [tmp_path / ah.OUTPUT_JSON, *sorted((tmp_path / ah.STORE_SUBDIR).iterdir())]
    before = {path: path.read_bytes() for path in paths}
    build = _window_build(1, "2026-07-14T08:00:00Z", job_state=state, start=None)
    build["jobs"][0]["agent"] = None
    monkeypatch.setattr(ah, "_paginate", _window_page([build], []))

    with pytest.raises(RuntimeError, match="invalid started_at"):
        _run_scoped_collection(monkeypatch, tmp_path, fetch_fn=ah._fetch_pipeline_observations)

    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("state", ["canceled", "broken", "expired"])
def test_never_started_terminal_jobs_with_no_agent_remain_out_of_execution_scope(state):
    build = _window_build(1, "2026-07-14T08:00:00Z", job_state=state, start=None)
    build["jobs"][0]["agent"] = None
    assert ah._observe_in_window("ci", build, build["jobs"][0], None, NOW - timedelta(days=3), NOW) is None


def test_started_canceled_job_uses_final_parent_finish_bound():
    build = _window_build(1, "2026-07-14T08:00:00Z", job_state="canceled", finish="", build_finish="2026-07-14T10:00:00Z")
    assert ah._observe_in_window("ci", build, build["jobs"][0], None, NOW - timedelta(days=3), NOW)
    build["state"] = "canceling"
    with pytest.raises(RuntimeError, match="no provable finish time"):
        ah._observe_in_window("ci", build, build["jobs"][0], None, NOW - timedelta(days=3), NOW)
    build["state"] = "canceled"
    build["finished_at"] = "2026-07-14T12:00:01Z"
    with pytest.raises(RuntimeError, match="no as-of finish bound"):
        ah._observe_in_window("ci", build, build["jobs"][0], None, NOW - timedelta(days=3), NOW)


def test_running_soft_failure_flag_is_not_a_terminal_started_run():
    build = _window_build(1, "2026-07-14T08:00:00Z", job_state="running")
    build["jobs"][0]["soft_failed"] = True
    assert ah._observe_in_window("ci", build, build["jobs"][0], None, NOW - timedelta(days=3), NOW) is None


def test_created_slice_duplicate_cannot_hide_malformed_embedded_roster(monkeypatch):
    valid = _window_build(1, "2026-07-14T08:00:00Z")
    malformed = {"number": 1}

    def page(_url, params):
        if params.get("created_from") == "2026-07-12T00:00:00+00:00":
            return [malformed]
        return [valid]

    monkeypatch.setattr(ah, "_paginate", page)
    with pytest.raises(RuntimeError, match="invalid embedded job roster"):
        ah._fetch_pipeline_observations("ci", 3, query_time=NOW)


@pytest.mark.parametrize("old_scope", [("amd-ci", "ci"), ("amd-ci",), None])
def test_default_agent_health_discards_mixed_or_unproven_retained_totals(
    monkeypatch, tmp_path, old_scope,
):
    _seed_scoped_history(tmp_path, old_scope)

    calls, payload = _run_scoped_collection(monkeypatch, tmp_path)

    assert [slug for slug, _, _ in calls] == ["ci"]
    assert payload["pipelines"] == ["ci"]
    assert payload["total_runs"] == 1
    assert payload["infra_failure_count"] == 1
    assert {row["p"] for row in payload["failing_runs"]} == {"ci"}
    assert {row["d"] for row in payload["node_days"]} == {"2026-07-14"}
    assert payload["retention"]["pipeline_scope"] == _expected_job_scope()
    assert ah._load_jsonl(tmp_path / ah.STORE_SUBDIR / ah.NODE_DAYS_JSONL) == payload["node_days"]
    assert ah._load_jsonl(tmp_path / ah.STORE_SUBDIR / ah.INFRA_FAILURES_JSONL) == payload["failing_runs"]


def test_current_agent_health_retains_verified_ci_history_and_filters_legacy_failures(
    monkeypatch, tmp_path,
):
    node, failures = _seed_scoped_history(tmp_path, ["ci"])

    _, payload = _run_scoped_collection(monkeypatch, tmp_path)

    assert payload["total_runs"] == node["a"][0] + 1
    assert payload["infra_failure_count"] == 2
    assert failures[1] in payload["failing_runs"]
    assert failures[0] not in payload["failing_runs"]
    assert {row["p"] for row in payload["failing_runs"]} == {"ci"}
    assert payload["retention"]["pipeline_scope"] == _expected_job_scope()


@pytest.mark.parametrize("pipeline,expected", [
    ("amd-ci", ["amd-ci"]), ("both", ["amd-ci", "ci"]),
])
def test_explicit_agent_health_historical_scope_keeps_truthful_provenance(
    monkeypatch, tmp_path, pipeline, expected,
):
    calls, payload = _run_scoped_collection(monkeypatch, tmp_path, pipeline=pipeline)

    assert [slug for slug, _, _ in calls] == expected
    assert payload["pipelines"] == expected
    assert payload["total_runs"] == len(expected)
    assert {row["p"] for row in payload["failing_runs"]} == set(expected)


def test_agent_health_missing_incremental_overlap_does_not_claim_complete_scope(
    monkeypatch, tmp_path,
):
    _seed_scoped_history(tmp_path, ["ci"], generated_at="2026-07-10T12:00:00Z")

    _, payload = _run_scoped_collection(monkeypatch, tmp_path)

    assert payload["retention"]["pipeline_scope"] == _expected_job_scope()


@pytest.mark.parametrize("stored_start", [
    "2026-05-10T15:30:00+03:30", "2026-05-10T12:00:00", "invalid",
])
def test_agent_health_old_creation_scope_never_extends_started_job_proof(
    monkeypatch, tmp_path, stored_start,
):
    _seed_scoped_history(tmp_path, ["ci"])
    path = tmp_path / ah.OUTPUT_JSON
    previous = json.loads(path.read_text())
    previous["retention"]["pipeline_scope"]["collected_from"] = stored_start
    path.write_text(json.dumps(previous))

    _, payload = _run_scoped_collection(monkeypatch, tmp_path)

    assert payload["retention"]["pipeline_scope"] == _expected_job_scope()


def test_agent_health_keeps_prior_byte_pruning_visible_in_scope_coverage(
    monkeypatch, tmp_path,
):
    _seed_scoped_history(tmp_path, ["ci"])
    path = tmp_path / ah.OUTPUT_JSON
    previous = json.loads(path.read_text())
    previous["retention"].update({
        "dropped_oldest_day_count": 2,
        "retained_start": "2026-07-10",
    })
    path.write_text(json.dumps(previous))

    _, payload = _run_scoped_collection(monkeypatch, tmp_path)

    assert payload["retention"]["pipeline_scope"] == _expected_job_scope()


def test_agent_health_scope_migration_source_failure_preserves_prior_generation(
    monkeypatch, tmp_path,
):
    _seed_scoped_history(tmp_path, ["amd-ci", "ci"])
    paths = [tmp_path / ah.OUTPUT_JSON, *sorted((tmp_path / ah.STORE_SUBDIR).iterdir())]
    before = {path: path.read_bytes() for path in paths}

    with pytest.raises(RuntimeError, match="source request failed"):
        _run_scoped_collection(monkeypatch, tmp_path, fail=True)

    assert {path: path.read_bytes() for path in paths} == before


def test_agent_health_generation_is_bounded_by_dropping_oldest_whole_days():
    days = ["2026-07-12", "2026-07-13", "2026-07-14"]
    node_days = [_retained_node_day(day) for day in days]
    failures = [
        _retained_failure(day, index, padding=1000)
        for day in days
        for index in range(40)
    ]

    generation = ah._prepare_generation(
        list(reversed(node_days)),
        list(reversed(failures)),
        NOW,
        max_file_bytes=60_000,
        max_generation_bytes=120_000,
    )

    assert {row["d"] for row in generation["node_days"]} == {days[-1]}
    assert {row["d"] for row in generation["failing"]} == {days[-1]}
    assert generation["dropped_days"] == tuple(days[:-1])
    assert max(generation["file_sizes"].values()) <= 60_000
    assert generation["total_bytes"] <= 120_000
    assert generation["payload"]["node_day_count"] == 1
    assert generation["payload"]["infra_failure_count"] == 40
    assert generation["payload"]["retention"] == {
        "policy": "drop_oldest_whole_days_then_bound_exact_failure_evidence",
        "configured_days": ah.MAX_WINDOW_DAYS,
        "original_day_count": 3,
        "retained_day_count": 1,
        "retained_start": days[-1],
        "retained_end": days[-1],
        "dropped_oldest_day_count": 2,
        "byte_limited": True,
        "max_file_bytes": 60_000,
        "max_generation_bytes": 120_000,
        "failure_evidence": {
            "source": 40,
            "published": 40,
            "omitted": 0,
            "complete_relative_to_source": True,
            "selection": "newest_then_infra_suspect_then_recency",
        },
        "failure_accounting": {
            "source": 40,
            "accounted": 40,
            "rows": 4,
            "complete_relative_to_source": True,
        },
    }

    repeated = ah._prepare_generation(
        node_days,
        failures,
        NOW,
        max_file_bytes=60_000,
        max_generation_bytes=120_000,
    )
    assert repeated["encoded"] == generation["encoded"]


def test_agent_health_refuses_newest_day_only_if_compact_accounting_cannot_fit():
    day = "2026-07-14"
    failures = [_retained_failure(day, index, padding=1000) for index in range(5)]

    with pytest.raises(RuntimeError, match="compact accounting cannot fit"):
        ah._prepare_generation(
            [_retained_node_day(day)],
            failures,
            NOW,
            max_file_bytes=1_000,
            max_generation_bytes=2_000,
        )


def test_agent_health_compacts_newest_day_links_but_preserves_exact_accounting():
    day = "2026-07-14"
    failures = [_retained_failure(day, index, padding=1000) for index in range(20)]

    generation = ah._prepare_generation(
        [_retained_node_day(day)],
        failures,
        NOW,
        max_file_bytes=12_000,
        max_generation_bytes=20_000,
    )

    payload = generation["payload"]
    evidence = payload["retention"]["failure_evidence"]
    accounting = payload["retention"]["failure_accounting"]
    assert evidence["source"] == 20
    assert 0 < evidence["published"] < evidence["source"]
    assert evidence["omitted"] == evidence["source"] - evidence["published"]
    assert evidence["complete_relative_to_source"] is False
    assert accounting == {
        "source": 20,
        "accounted": 20,
        "rows": 4,
        "complete_relative_to_source": True,
    }
    assert payload["infra_failure_count"] == 20
    assert payload["published_failure_evidence_count"] == evidence["published"]
    assert sum(row["c"] for row in payload["failure_accounting"]) == 20
    assert failures[-1]["j"] in {row["j"] for row in generation["failing"]}
    assert all(size <= 12_000 for size in generation["file_sizes"].values())
    assert generation["total_bytes"] <= 20_000

    valid_audit = DashboardAudit(Path("."))
    valid_audit.audit_agent_health(
        {"amd_agent_health": payload},
        "data/vllm/ci/operations_v2.json",
    )
    assert "operations-agent-health-accounting" not in {
        finding.code for finding in valid_audit.report.errors
    }

    payload["failure_accounting"][0]["c"] += 1
    invalid_audit = DashboardAudit(Path("."))
    invalid_audit.audit_agent_health(
        {"amd_agent_health": payload},
        "data/vllm/ci/operations_v2.json",
    )
    assert "operations-agent-health-accounting" in {
        finding.code for finding in invalid_audit.report.errors
    }


def test_agent_health_generation_publication_writes_exact_bounded_files(tmp_path):
    generation = ah._prepare_generation(
        [_retained_node_day("2026-07-14")],
        [_retained_failure("2026-07-14", 1)],
        NOW,
    )

    ah._publish_generation(tmp_path, generation)

    store = tmp_path / ah.STORE_SUBDIR
    assert (store / ah.NODE_DAYS_JSONL).read_bytes() == generation["encoded"][
        ah.NODE_DAYS_JSONL
    ]
    assert (store / ah.INFRA_FAILURES_JSONL).read_bytes() == generation["encoded"][
        ah.INFRA_FAILURES_JSONL
    ]
    assert (tmp_path / ah.OUTPUT_JSON).read_bytes() == generation["encoded"][
        ah.OUTPUT_JSON
    ]
    assert not list(tmp_path.glob(f".{ah.STORE_SUBDIR}.stage.*"))
    assert not list(tmp_path.glob(f".{ah.STORE_SUBDIR}.backup.*"))


def test_agent_health_summary_failure_rolls_back_the_ledger(monkeypatch, tmp_path):
    store = tmp_path / ah.STORE_SUBDIR
    store.mkdir()
    old_node_days = b'{"d":"old-node-days"}\n'
    old_failures = b'{"d":"old-failures"}\n'
    old_summary = b'{"generation":"old"}\n'
    (store / ah.NODE_DAYS_JSONL).write_bytes(old_node_days)
    (store / ah.INFRA_FAILURES_JSONL).write_bytes(old_failures)
    (tmp_path / ah.OUTPUT_JSON).write_bytes(old_summary)
    generation = ah._prepare_generation(
        [_retained_node_day("2026-07-14")],
        [_retained_failure("2026-07-14", 1)],
        NOW,
    )

    def fail_summary_replace(_path, _payload):
        raise OSError("injected summary replacement failure")

    monkeypatch.setattr(ah, "_atomic_write_bytes", fail_summary_replace)
    with pytest.raises(OSError, match="injected summary replacement failure"):
        ah._publish_generation(tmp_path, generation)

    assert (store / ah.NODE_DAYS_JSONL).read_bytes() == old_node_days
    assert (store / ah.INFRA_FAILURES_JSONL).read_bytes() == old_failures
    assert (tmp_path / ah.OUTPUT_JSON).read_bytes() == old_summary
    assert not list(tmp_path.glob(f".{ah.STORE_SUBDIR}.stage.*"))
    assert not list(tmp_path.glob(f".{ah.STORE_SUBDIR}.backup.*"))


def test_agent_health_failed_rollback_preserves_the_only_prior_backup(
    monkeypatch,
    tmp_path,
):
    store = tmp_path / ah.STORE_SUBDIR
    store.mkdir()
    old_node_days = b'{"d":"old-node-days"}\n'
    old_failures = b'{"d":"old-failures"}\n'
    (store / ah.NODE_DAYS_JSONL).write_bytes(old_node_days)
    (store / ah.INFRA_FAILURES_JSONL).write_bytes(old_failures)
    (tmp_path / ah.OUTPUT_JSON).write_bytes(b'{"generation":"old"}\n')
    generation = ah._prepare_generation(
        [_retained_node_day("2026-07-14")],
        [_retained_failure("2026-07-14", 1)],
        NOW,
    )
    real_remove_path = ah._remove_path

    def fail_summary_replace(_path, _payload):
        raise OSError("injected summary replacement failure")

    def fail_new_store_removal(path):
        if path == store:
            raise OSError("injected rollback removal failure")
        return real_remove_path(path)

    monkeypatch.setattr(ah, "_atomic_write_bytes", fail_summary_replace)
    monkeypatch.setattr(ah, "_remove_path", fail_new_store_removal)

    with pytest.raises(RuntimeError, match="prior ledger backup remains at"):
        ah._publish_generation(tmp_path, generation)

    backups = list(tmp_path.glob(f".{ah.STORE_SUBDIR}.backup.*"))
    assert len(backups) == 1
    assert (backups[0] / ah.NODE_DAYS_JSONL).read_bytes() == old_node_days
    assert (backups[0] / ah.INFRA_FAILURES_JSONL).read_bytes() == old_failures


def test_agent_health_storage_caps_stay_below_dashboard_sync_limit():
    assert ah.AGENT_HEALTH_MAX_FILE_BYTES == 32 * 1024 * 1024
    assert ah.AGENT_HEALTH_MAX_GENERATION_BYTES == 16 * 1024 * 1024
    assert ah.AGENT_HEALTH_MAX_FILE_BYTES < 90_000_000
    assert ah.AGENT_HEALTH_MAX_GENERATION_BYTES < 90_000_000


def test_agent_health_transaction_scratch_directories_cannot_be_committed():
    ignore = (Path(__file__).resolve().parents[2] / ".gitignore").read_text()

    assert "data/vllm/ci/.agent_health.stage.*/" in ignore
    assert "data/vllm/ci/.agent_health.backup.*/" in ignore


# --------------------------------------------------------------------------- #
# build_operations_snapshot._amd_agent_health — pass-through loader
# --------------------------------------------------------------------------- #

def test_amd_agent_health_passthrough(tmp_path):
    block = {"generated_at": "x", "node_days": [{"nd": "A"}], "failing_runs": []}
    (tmp_path / "agent_health.json").write_text(json.dumps(block))
    assert ops._amd_agent_health(tmp_path) == block


def test_amd_agent_health_missing_file(tmp_path):
    assert ops._amd_agent_health(tmp_path) == {}


# --------------------------------------------------------------------------- #
# Frontend co-failure clustering (JS) — quickjs parity
# --------------------------------------------------------------------------- #

_OPS_JS = Path(__file__).resolve().parents[2] / "docs" / "assets" / "js" / "ops-v2.js"


def _extract_js_function(source: str, name: str) -> str:
    start = source.index("function " + name + "(")
    depth = 0
    i = source.index("{", start)
    body_start = start
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[body_start:j + 1]
    raise ValueError("unbalanced braces for " + name)


def _py_cluster(runs, window_mins):
    """Python reference: mirrors clusterNodeCofailures for parity checks."""
    failing = sorted((r for r in runs if r["_start"] is not None), key=lambda r: r["_start"])
    window = window_mins * 60000
    events, cluster, cluster_end = [], [], None
    def flush():
        nonlocal cluster
        # Collapse retries of the same (pipeline, build, group) — keep the latest
        # attempt — then require >=2 distinct logical failures (need NOT differ
        # by group). Mirrors clusterNodeCofailures' flush().
        by_key: dict = {}
        for r in cluster:
            key = (r["pipeline"], r.get("build_number"), r["group"])
            prev = by_key.get(key)
            if prev is None or r["_start"] > prev["_start"]:
                by_key[key] = r
        distinct = list(by_key.values())
        if len(distinct) >= 2:
            groups = {r["group"] for r in distinct}
            intervals = sorted(([r["_start"], r["_end"] if r["_end"] is not None else r["_start"]] for r in distinct))
            concurrent = any(intervals[k][0] < intervals[k - 1][1] for k in range(1, len(intervals)))
            events.append({
                "group_count": len(groups),
                "concurrent": concurrent,
                "cross_pipeline": len({r["pipeline"] for r in distinct}) > 1,
            })
        cluster = []
    for r in failing:
        start = r["_start"]
        end = r["_end"] if r["_end"] is not None else start
        if cluster_end is not None and (start - cluster_end) > window:
            flush(); cluster_end = None
        cluster.append(r)
        cluster_end = end if cluster_end is None else max(cluster_end, end)
    flush()
    return events


def test_js_cofailure_clustering_matches_reference():
    quickjs = pytest.importorskip("quickjs")
    source = _OPS_JS.read_text()
    js = (
        _extract_js_function(source, "clusterNodeCofailures")
        + "\n"
        + _extract_js_function(source, "makeCofailEvent")
    )
    ctx = quickjs.Context()
    ctx.eval(js)
    scenarios = [
        # concurrent overlap, 2 groups -> event
        ([{"group": "A", "pipeline": "amd-ci", "build_number": 1, "state": "hard", "_start": 0, "_end": 300000},
          {"group": "B", "pipeline": "amd-ci", "build_number": 1, "state": "hard", "_start": 120000, "_end": 360000}], 180),
        # sequential within window, cross-pipeline -> event
        ([{"group": "A", "pipeline": "amd-ci", "build_number": 1, "state": "hard", "_start": 0, "_end": 60000},
          {"group": "B", "pipeline": "ci", "build_number": 2, "state": "hard", "_start": 7200000, "_end": 7260000}], 180),
        # too far apart -> no event
        ([{"group": "A", "pipeline": "amd-ci", "build_number": 1, "state": "hard", "_start": 0, "_end": 60000},
          {"group": "B", "pipeline": "amd-ci", "build_number": 1, "state": "hard", "_start": 99999999, "_end": 99999999}], 180),
        # same group, SAME build (retries) -> no event (collapsed to one failure)
        ([{"group": "A", "pipeline": "amd-ci", "build_number": 5, "state": "hard", "_start": 0, "_end": 60000},
          {"group": "A", "pipeline": "amd-ci", "build_number": 5, "state": "hard", "_start": 120000, "_end": 180000}], 180),
        # same group, DIFFERENT builds -> event (distinct-group requirement relaxed)
        ([{"group": "A", "pipeline": "amd-ci", "build_number": 5, "state": "hard", "_start": 0, "_end": 60000},
          {"group": "A", "pipeline": "amd-ci", "build_number": 6, "state": "hard", "_start": 120000, "_end": 180000}], 180),
        # two retries of group A (same build) + one B failure -> event with 2 logical failures
        ([{"group": "A", "pipeline": "amd-ci", "build_number": 5, "state": "hard", "_start": 0, "_end": 60000},
          {"group": "A", "pipeline": "amd-ci", "build_number": 5, "state": "hard", "_start": 90000, "_end": 150000},
          {"group": "B", "pipeline": "amd-ci", "build_number": 5, "state": "hard", "_start": 120000, "_end": 180000}], 180),
    ]
    for runs, window in scenarios:
        py = _py_cluster(runs, window)
        js_events = json.loads(ctx.eval(
            "JSON.stringify(clusterNodeCofailures('lbl','raw','MI300',"
            + json.dumps(runs) + "," + str(window) + "))"
        ))
        assert len(js_events) == len(py), (runs, window)
        for je, pe in zip(js_events, py):
            assert je["group_count"] == pe["group_count"]
            assert je["concurrent"] == pe["concurrent"]
            assert je["cross_pipeline"] == pe["cross_pipeline"]
