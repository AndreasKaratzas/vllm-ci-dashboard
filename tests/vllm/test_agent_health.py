"""Tests for per-physical-agent (node) AMD CI health tracking.

Covers:
- ``log_parser.extract_node`` — parsing the "Node:" line from decorated logs.
- ``build_operations_snapshot._amd_agent_health`` — joining the JSONL ``node``
  to analytics job timing, AMD-only scoping across both pipelines, the
  unidentified bucket, and near-simultaneous co-failure detection.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from vllm import build_operations_snapshot as ops
from vllm.ci.log_parser import extract_node, node_from_agent


NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# extract_node
# --------------------------------------------------------------------------- #

# The real MI325 runner banner, including the Buildkite OSC timestamp escape
# that precedes it in raw logs.
_REAL_BANNER = (
    "\x1b_bk;t=1784019766616\x07=== Pod: buildkite-019f5fdb-00b1-49b5-9253-5bd88b4879bd-55rwd"
    " | Node: chi-mi325x-pod2-032 | Tue Jul 14 09:02:46 UTC 2026 ==="
)


def test_extract_node_real_banner():
    assert extract_node(_REAL_BANNER) == "chi-mi325x-pod2-032"


def test_extract_node_banner_within_full_log():
    log = "some setup\n" + _REAL_BANNER + "\nmore output\n"
    assert extract_node(log) == "chi-mi325x-pod2-032"


def test_extract_node_ignores_topology_decoys():
    # GPU-topology "Node: 0" lines must never be mistaken for the physical node.
    log = "  Node:                    0\n  Internal Node ID:        0\n"
    assert extract_node(log) == ""


def test_extract_node_first_match_wins():
    log = (
        "=== Pod: pod-a | Node: chi-mi325x-pod1-001 | ts ===\n"
        "=== Pod: pod-b | Node: chi-mi325x-pod2-002 | ts ===\n"
    )
    assert extract_node(log) == "chi-mi325x-pod1-001"


def test_extract_node_absent():
    assert extract_node("no node marker here") == ""
    assert extract_node("") == ""
    assert extract_node(None) == ""


# --------------------------------------------------------------------------- #
# node_from_agent (primary source: the k8s:node agent tag)
# --------------------------------------------------------------------------- #

def test_node_from_agent_reads_k8s_tag():
    job = {"agent": {"meta_data": ["queue=amd_mi300_1", "k8s:node=gpu9124"]}}
    assert node_from_agent(job) == "gpu9124"


def test_node_from_agent_mi250_and_mi355():
    assert node_from_agent({"agent": {"meta_data": ["k8s:node=smci250-ccs-aus-c21-08"]}}) == "smci250-ccs-aus-c21-08"
    assert node_from_agent({"agent": {"meta_data": ["k8s:node=mia1-p02-g49"]}}) == "mia1-p02-g49"


def test_node_from_agent_absent():
    assert node_from_agent({"agent": {"meta_data": ["queue=amd-cpu"]}}) == ""
    assert node_from_agent({"agent": {}}) == ""
    assert node_from_agent({}) == ""


# --------------------------------------------------------------------------- #
# GPU-type supplementing for bare node names
# --------------------------------------------------------------------------- #

def test_node_label_always_appends_gpu_type():
    # Every node name gets the GPU type appended for consistency.
    assert ops._node_label("gpu9124", "MI300") == "gpu9124 (MI300)"
    assert ops._node_label("mia1-p02-g49", "MI355") == "mia1-p02-g49 (MI355)"
    assert ops._node_label("smci250-ccs-aus-c21-08", "MI250") == "smci250-ccs-aus-c21-08 (MI250)"
    assert ops._node_label("chi-mi325x-pod2-032", "MI325") == "chi-mi325x-pod2-032 (MI325)"


def test_node_label_keeps_raw_when_hardware_unknown():
    assert ops._node_label("some-node", "") == "some-node"


def test_queue_hardware():
    assert ops._queue_hardware("amd_mi300_1") == "MI300"
    assert ops._queue_hardware("amd_mi325_4") == "MI325"
    assert ops._queue_hardware("gpu_4_queue") == ""


# --------------------------------------------------------------------------- #
# _amd_agent_health
# --------------------------------------------------------------------------- #

def _job(job_id, name, state, queue, start, end):
    return {
        "job_id": job_id,
        "name": name,
        "raw_name": f"mi325_1: {name}",
        "state": state,
        "q": queue,
        "started_at": start,
        "finished_at": end,
    }


def _write_nodes(data_dir: Path, mapping: dict[str, str]) -> None:
    results = data_dir / "test_results"
    results.mkdir(parents=True, exist_ok=True)
    rows = [{"job_id": jid, "node": node} for jid, node in mapping.items()]
    (results / "2026-07-14_amd.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n"
    )


def _analytics(amd_jobs, ci_jobs):
    return {
        "amd-ci": {"builds": [{"number": 100, "created_at": "2026-07-14T09:00:00Z", "jobs": amd_jobs}]},
        "ci": {"builds": [{"number": 200, "created_at": "2026-07-14T09:00:00Z", "jobs": ci_jobs}]},
    }


def _runs_by_node_raw(result):
    grouped = {}
    for run in result["runs"]:
        grouped.setdefault(run["node_raw"], []).append(run)
    return grouped


def test_agent_health_metadata(tmp_path):
    _write_nodes(tmp_path, {"j1": "chi-mi325x-a"})
    analytics = _analytics(
        amd_jobs=[_job("j1", "Group A", "passed", "amd_mi325_1",
                       "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z")],
        ci_jobs=[],
    )
    result = ops._amd_agent_health(tmp_path, analytics, NOW)
    assert result["window_options"] == [1, 3, 7, 14, 30, 60]
    assert result["default_window_days"] == 7
    assert result["max_window_days"] == 60
    assert result["hardware_types"] == ["MI325"]
    assert isinstance(result["runs"], list)
    assert isinstance(result["cofailure_events"], list)


def test_agent_health_runs_scope_amd(tmp_path):
    # jX is a non-AMD job that also carries a node tag — it must still be excluded
    # because the k8s:node tag now exists for NVIDIA runners too.
    _write_nodes(tmp_path, {
        "j1": "chi-mi325x-a", "j2": "chi-mi325x-a", "j3": "chi-mi325x-b",
        "jX": "gpu-nvidia-01",
    })
    analytics = _analytics(
        amd_jobs=[
            _job("j1", "Kernels MoE Test 1", "soft_failed", "amd_mi325_1",
                 "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"),
            _job("j2", "Language Models Test", "passed", "amd_mi325_1",
                 "2026-07-14T09:10:00Z", "2026-07-14T09:15:00Z"),
        ],
        ci_jobs=[
            # AMD node job inside upstream ci — in scope.
            _job("j3", "Upstream AMD Job", "failed", "amd_mi325_2",
                 "2026-07-14T09:01:00Z", "2026-07-14T09:04:00Z"),
            # NVIDIA job with a node tag — must be excluded (non-AMD queue).
            _job("jX", "NVIDIA Job", "failed", "gpu_4_queue",
                 "2026-07-14T09:01:00Z", "2026-07-14T09:04:00Z"),
        ],
    )
    result = ops._amd_agent_health(tmp_path, analytics, NOW)

    # NVIDIA job excluded; three AMD runs across both pipelines remain.
    assert len(result["runs"]) == 3
    assert all(r["queue"].startswith("amd_") for r in result["runs"])
    grouped = _runs_by_node_raw(result)
    assert set(grouped) == {"chi-mi325x-a", "chi-mi325x-b"}
    assert all(r["hardware"] == "MI325" for r in result["runs"])
    assert {r["pipeline"] for r in grouped["chi-mi325x-b"]} == {"ci"}


def test_agent_health_runs_include_unidentified(tmp_path):
    _write_nodes(tmp_path, {"j1": "chi-mi325x-a"})  # j2 has no node
    analytics = _analytics(
        amd_jobs=[
            _job("j1", "Group A", "passed", "amd_mi325_1",
                 "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"),
            _job("j2", "Group B", "failed", "amd_mi325_1",
                 "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"),
        ],
        ci_jobs=[],
    )
    result = ops._amd_agent_health(tmp_path, analytics, NOW)
    grouped = _runs_by_node_raw(result)
    assert set(grouped) == {"chi-mi325x-a", "(unidentified)"}
    # Unidentified runs never produce co-failure events.
    assert all(e["node_raw"] != "(unidentified)" for e in result["cofailure_events"])


def test_agent_health_concurrent_cofailure(tmp_path):
    # Two overlapping failures -> one concurrent co-failure event (contention).
    _write_nodes(tmp_path, {"j1": "chi-mi325x-a", "j2": "chi-mi325x-a"})
    analytics = _analytics(
        amd_jobs=[
            _job("j1", "Group A", "soft_failed", "amd_mi325_1",
                 "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"),
            _job("j2", "Group B", "failed", "amd_mi325_1",
                 "2026-07-14T09:02:00Z", "2026-07-14T09:06:00Z"),
        ],
        ci_jobs=[],
    )
    result = ops._amd_agent_health(tmp_path, analytics, NOW)
    assert len(result["cofailure_events"]) == 1
    event = result["cofailure_events"][0]
    assert event["node"] == "chi-mi325x-a (MI325)"
    assert event["node_raw"] == "chi-mi325x-a"
    assert event["hardware"] == "MI325"
    assert event["pattern"] == "concurrent"
    assert event["concurrent"] is True
    assert event["group_count"] == 2


def test_agent_health_cofailure_supplements_label(tmp_path):
    # A bare MI300 node name is GPU-type-labelled on the event.
    _write_nodes(tmp_path, {"j1": "gpu9124", "j2": "gpu9124"})
    analytics = _analytics(
        amd_jobs=[
            _job("j1", "Group A", "failed", "amd_mi300_1",
                 "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"),
            _job("j2", "Group B", "failed", "amd_mi300_1",
                 "2026-07-14T09:02:00Z", "2026-07-14T09:06:00Z"),
        ],
        ci_jobs=[],
    )
    result = ops._amd_agent_health(tmp_path, analytics, NOW)
    event = result["cofailure_events"][0]
    assert event["node"] == "gpu9124 (MI300)"
    assert event["node_raw"] == "gpu9124"
    assert event["hardware"] == "MI300"


def test_agent_health_sequential_cofailure(tmp_path):
    # Two non-overlapping failures 2h apart -> one sequential event within the
    # 12h window (possible unclean-node carryover).
    _write_nodes(tmp_path, {"j1": "chi-mi325x-a", "j2": "chi-mi325x-a"})
    analytics = _analytics(
        amd_jobs=[
            _job("j1", "Group A", "failed", "amd_mi325_1",
                 "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"),
            _job("j2", "Group B", "failed", "amd_mi325_1",
                 "2026-07-14T11:00:00Z", "2026-07-14T11:05:00Z"),
        ],
        ci_jobs=[],
    )
    result = ops._amd_agent_health(tmp_path, analytics, NOW)
    assert len(result["cofailure_events"]) == 1
    event = result["cofailure_events"][0]
    assert event["pattern"] == "sequential"
    assert event["concurrent"] is False
    assert event["group_count"] == 2


def test_agent_health_cofailure_window_boundary(tmp_path):
    # Failures more than 12h apart do not cluster.
    _write_nodes(tmp_path, {"j1": "chi-mi325x-a", "j2": "chi-mi325x-a"})
    analytics = _analytics(
        amd_jobs=[
            _job("j1", "Group A", "failed", "amd_mi325_1",
                 "2026-07-13T09:00:00Z", "2026-07-13T09:05:00Z"),
            _job("j2", "Group B", "failed", "amd_mi325_1",
                 "2026-07-13T22:00:00Z", "2026-07-13T22:05:00Z"),
        ],
        ci_jobs=[],
    )
    result = ops._amd_agent_health(tmp_path, analytics, NOW)
    assert result["cofailure_events"] == []


def test_agent_health_cross_pipeline_cofailure(tmp_path):
    _write_nodes(tmp_path, {"j1": "chi-mi325x-a", "j2": "chi-mi325x-a"})
    analytics = _analytics(
        amd_jobs=[
            _job("j1", "Nightly Group", "failed", "amd_mi325_1",
                 "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"),
        ],
        ci_jobs=[
            _job("j2", "Upstream Group", "failed", "amd_mi325_1",
                 "2026-07-14T09:01:00Z", "2026-07-14T09:06:00Z"),
        ],
    )
    result = ops._amd_agent_health(tmp_path, analytics, NOW)
    assert len(result["cofailure_events"]) == 1
    event = result["cofailure_events"][0]
    assert event["cross_pipeline"] is True
    assert sorted(event["pipelines"]) == ["amd-ci", "ci"]


def test_agent_health_excludes_retired_queue(tmp_path):
    # A job on a retired mi355b queue must be dropped even if it has a node.
    _write_nodes(tmp_path, {"j1": "node-mi355b"})
    analytics = _analytics(
        amd_jobs=[
            _job("j1", "Group A", "failed", "amd_mi355b_1",
                 "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"),
        ],
        ci_jobs=[],
    )
    result = ops._amd_agent_health(tmp_path, analytics, NOW)
    assert result["runs"] == []


def test_agent_health_window_keeps_45d_drops_70d(tmp_path):
    # Max emitted window is 60d: a 45-day-old build is kept, a 70-day-old dropped.
    _write_nodes(tmp_path, {"j45": "chi-mi325x-a", "j70": "chi-mi325x-b"})
    analytics = {
        "amd-ci": {"builds": [
            {"number": 1, "created_at": "2026-05-30T09:00:00Z",  # 45d before NOW
             "jobs": [_job("j45", "Group A", "failed", "amd_mi325_1",
                           "2026-05-30T09:00:00Z", "2026-05-30T09:05:00Z")]},
            {"number": 2, "created_at": "2026-05-05T09:00:00Z",  # 70d before NOW
             "jobs": [_job("j70", "Group B", "failed", "amd_mi325_1",
                           "2026-05-05T09:00:00Z", "2026-05-05T09:05:00Z")]},
        ]},
        "ci": {"builds": []},
    }
    result = ops._amd_agent_health(tmp_path, analytics, NOW)
    node_raws = {r["node_raw"] for r in result["runs"]}
    assert node_raws == {"chi-mi325x-a"}
