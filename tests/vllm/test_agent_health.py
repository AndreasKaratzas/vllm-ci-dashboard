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
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vllm import build_operations_snapshot as ops
from vllm import collect_agent_health as ah
from vllm.constants import amd_gpu_hardware
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
    assert amd_gpu_hardware("amd_mi250_8") == "MI250"
    assert amd_gpu_hardware("amd_mi355_1") == "MI355"
    # CPU steps, NVIDIA, and the retired mi355b family are out of scope.
    assert amd_gpu_hardware("amd-cpu") == ""
    assert amd_gpu_hardware("gpu_4_queue") == ""
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
