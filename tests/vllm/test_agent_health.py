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

def test_node_label_supplements_bare_names():
    # Bare GPU names get the GPU type appended.
    assert ops._node_label("gpu9124", "MI300", "300") == "gpu9124 (MI300)"
    assert ops._node_label("mia1-p02-g49", "MI355", "355") == "mia1-p02-g49 (MI355)"


def test_node_label_keeps_informative_names():
    # Names already carrying the model number are left unchanged.
    assert ops._node_label("smci250-ccs-aus-c21-08", "MI250", "250") == "smci250-ccs-aus-c21-08"
    assert ops._node_label("chi-mi325x-pod2-032", "MI325", "325") == "chi-mi325x-pod2-032"


def test_queue_hardware():
    assert ops._queue_hardware("amd_mi300_1") == ("MI300", "300")
    assert ops._queue_hardware("amd_mi325_4") == ("MI325", "325")
    assert ops._queue_hardware("gpu_4_queue") == ("", "")


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


def test_agent_health_joins_node_and_scopes_amd(tmp_path):
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

    # NVIDIA job excluded; three AMD jobs across both pipelines remain.
    assert result["coverage"]["in_scope_jobs"] == 3
    assert result["coverage"]["identified_jobs"] == 3
    assert result["coverage"]["coverage_pct"] == 100.0
    assert result["summary"]["nodes"] == 2

    # Names already carrying the model number keep their raw label.
    nodes = {agent["node_raw"]: agent for agent in result["agents"]}
    assert set(nodes) == {"chi-mi325x-a", "chi-mi325x-b"}
    assert nodes["chi-mi325x-a"]["node"] == "chi-mi325x-a"
    assert nodes["chi-mi325x-a"]["hardware"] == "MI325"
    assert nodes["chi-mi325x-a"]["runs"] == 2
    assert nodes["chi-mi325x-a"]["pipelines"] == ["amd-ci"]
    assert nodes["chi-mi325x-b"]["pipelines"] == ["ci"]
    assert nodes["chi-mi325x-a"]["soft_failed"] == 1


def test_agent_health_supplements_bare_node_name(tmp_path):
    # A bare MI300 node name is labelled with its GPU type in the agent record.
    _write_nodes(tmp_path, {"j1": "gpu9124"})
    analytics = _analytics(
        amd_jobs=[
            _job("j1", "Group A", "failed", "amd_mi300_1",
                 "2026-07-14T09:00:00Z", "2026-07-14T09:05:00Z"),
        ],
        ci_jobs=[],
    )
    result = ops._amd_agent_health(tmp_path, analytics, NOW)
    agent = result["agents"][0]
    assert agent["node_raw"] == "gpu9124"
    assert agent["node"] == "gpu9124 (MI300)"
    assert agent["hardware"] == "MI300"


def test_agent_health_unidentified_bucket(tmp_path):
    _write_nodes(tmp_path, {"j1": "node-a"})  # j2 has no node
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
    assert result["coverage"]["in_scope_jobs"] == 2
    assert result["coverage"]["identified_jobs"] == 1
    assert result["coverage"]["unidentified_jobs"] == 1
    # Identified node count excludes the unidentified bucket.
    assert result["summary"]["nodes"] == 1
    unidentified = [a for a in result["agents"] if not a["identified"]]
    assert len(unidentified) == 1
    assert unidentified[0]["node"] == "(unidentified)"
    # The unidentified bucket never produces co-failure events.
    assert unidentified[0]["cofailure_event_count"] == 0


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
    assert result["summary"]["cofailure_events"] == 1
    assert result["summary"]["concurrent_cofailures"] == 1
    event = result["cofailure_events"][0]
    assert event["node"] == "chi-mi325x-a"
    assert event["pattern"] == "concurrent"
    assert event["concurrent"] is True
    assert event["group_count"] == 2


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
    assert result["summary"]["cofailure_events"] == 1
    assert result["summary"]["sequential_cofailures"] == 1
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
    assert result["summary"]["cofailure_events"] == 0


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
    assert result["summary"]["cofailure_events"] == 1
    assert result["summary"]["cross_pipeline_cofailures"] == 1
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
    assert result["coverage"]["in_scope_jobs"] == 0
    assert result["agents"] == []


def test_agent_health_window_excludes_old_builds(tmp_path):
    _write_nodes(tmp_path, {"j1": "node-a"})
    analytics = {
        "amd-ci": {"builds": [{
            "number": 1,
            "created_at": "2026-07-01T09:00:00Z",  # >7 days before NOW
            "jobs": [_job("j1", "Group A", "failed", "amd_mi325_1",
                          "2026-07-01T09:00:00Z", "2026-07-01T09:05:00Z")],
        }]},
        "ci": {"builds": []},
    }
    result = ops._amd_agent_health(tmp_path, analytics, NOW)
    assert result["coverage"]["in_scope_jobs"] == 0
