#!/usr/bin/env python3
"""Build an offline browser site with explicit synthetic main-CI evidence.

Frozen repository data seeds retained infrastructure/performance views only.
Current CI definitions, runtime results, and timing are generated below and
passed through the production parsers, snapshot writer, and site assembler.
Everything is staged in a temporary directory; data/ is never rewritten.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from pathlib import Path
import shutil
import sys
import tempfile
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import build_site  # noqa: E402
from vllm import build_operations_snapshot as operations  # noqa: E402
from vllm import build_test_group_parity as parity  # noqa: E402
from vllm import collect_agent_health as agents  # noqa: E402
from vllm import collect_amd_test_matrix as matrix  # noqa: E402
from vllm.ci.nightly_latency import build_current_nightly_latency  # noqa: E402
from vllm.collect_analytics import summarize_pipeline_builds  # noqa: E402
from vllm.main_ci_definitions import CI_CONFIG, MainCISnapshot  # noqa: E402

GENERATED_AT = "2026-10-08T20:00:00Z"
SOURCE_SHA = "a" * 40
SOURCE_FILE = ".buildkite/test_areas/browser_fixture.yaml"
VARIANTS = [
    ("mi300", "Routed Alpha", "routed-alpha", "passed", "passed", 25),
    ("mi355", "Routed Beta", "routed-beta", "failed", "failed", 35),
    ("mi300", "Attention Kernels Shard 1", "attention-1", "passed", "passed", 10),
    ("mi300", "Attention Kernels Shard 2", "attention-2", "passed", "passed", 15),
    ("mi355", "Broken Group", "broken", "failed", "error", 18),
    ("mi300", "Basic Models (Other)", "basic-models", "passed", "passed", 20),
]


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n")


def source_fixture() -> MainCISnapshot:
    steps = []
    titles = [row[1] for row in VARIANTS] + [
        "Optional integration", "Soft integration", "Extra models", "Extra kernels",
    ]
    for index, title in enumerate(titles):
        device = VARIANTS[index][0] if index < len(VARIANTS) else "mi300"
        flags = {"optional": True} if index == 6 else {"soft_fail": True} if index == 7 else {}
        steps.append({
            "key": f"current-{index}", "label": f":nvidia: (H100) {title}",
            "device": "h100", "num_devices": 1,
            "commands": [f"pytest tests/browser_fixture_{index}.py"],
            "mirror": {"amd": {
                "label": f":amd: ({device.upper()}) {title}",
                "device": device, "num_devices": 1, **flags,
            }},
        })
    for key, title in [("missing", "Missing current workload"), ("cuda-only", "CUDA backend only")]:
        steps.append({
            "key": key, "label": f":nvidia: (H100) {title}",
            "device": "h100", "commands": [f"pytest tests/{key}.py"],
        })
    return MainCISnapshot(SOURCE_SHA, {
        CI_CONFIG: {"job_dirs": [".buildkite/test_areas"]},
        SOURCE_FILE: {"group": "Browser fixture", "steps": steps},
    }, GENERATED_AT)


def raw_nightlies() -> list[dict]:
    builds = []
    for offset in range(5):
        number = 30005 - offset
        started = datetime(2026, 10, 8, 9, tzinfo=timezone.utc) - timedelta(days=offset)
        jobs = []
        for device, title, key, state, _, minutes in VARIANTS:
            job_id = str(uuid5(NAMESPACE_URL, f"browser-ci/{number}/{key}"))
            job = {
                "id": job_id, "type": "script", "state": state,
                "name": f"{device}_1: :amd: ({device.upper()}) {title}",
                "agent_query_rules": [f"queue=amd_{device}_1"],
                "step": {"key": "attention" if key.startswith("attention") else key},
                "started_at": started.isoformat(),
                "finished_at": (started + timedelta(minutes=minutes + offset)).isoformat(),
            }
            if key.startswith("attention"):
                job.update(parallel_group_index=int(key[-1]) - 1, parallel_group_total=2)
            jobs.append(job)
        jobs.append({
            "id": str(uuid5(NAMESPACE_URL, f"browser-ci/{number}/cuda-basic")),
            "type": "script", "state": "passed",
            "name": ":nvidia: (H100) Basic Models (Other)",
            "agent_query_rules": ["queue=gpu_1"], "step": {"key": "cuda-basic"},
            "started_at": started.isoformat(),
            "finished_at": (started + timedelta(minutes=10 + offset)).isoformat(),
        })
        builds.append({
            "number": number, "message": "Full CI run - nightly", "branch": "main",
            "state": "failed", "commit": SOURCE_SHA,
            "created_at": started.isoformat(), "finished_at": (started + timedelta(hours=1)).isoformat(),
            "web_url": f"https://buildkite.com/vllm/ci/builds/{number}", "jobs": jobs,
        })
    return builds


def seed_current_ci(data_dir: Path) -> None:
    raw = raw_nightlies()
    builds = summarize_pipeline_builds("ci", raw)
    analytics = {"builds": builds, "generated_at": GENERATED_AT, "job_scope": "amd_gpu", "hardware_scope": "amd_mi_gpu"}
    analytics["current_nightly_latency"] = build_current_nightly_latency(
        builds, generated_at=GENERATED_AT, source_available=True,
    )
    write_json(data_dir / "analytics.json", {"ci": analytics})
    latest = raw[0]
    reference = {
        "build_number": latest["number"], "branch": "main", "commit": SOURCE_SHA,
        "build_url": latest["web_url"], "created_at": latest["created_at"],
        "unique_test_groups": 4, "test_groups_passing_or": 3,
        "test_groups_passing_all": 2, "test_groups_partial": 1,
    }
    write_json(data_dir / "ci_health.json", {
        "amd": {"source_pipeline": "ci", "job_scope": "amd_gpu", "hardware_scope": "amd_mi_gpu", "latest_test_signal_build": reference},
    })
    definition_parity = {"source": {"commit_sha": SOURCE_SHA}, "matches": [{
        "amd_identity_family_key": "routed family (2 gpus)",
        "amd_member_labels": [":amd: (MI300) Routed Alpha", ":amd: (MI355) Routed Beta"],
        "amd_member_agent_pools": ["mi300_1", "mi355_1"],
    }]}
    write_json(data_dir / "config_parity.json", definition_parity)
    write_json(data_dir / "shard_bases.json", ["attention kernels shard"])
    results = data_dir / "test_results"
    results.mkdir(parents=True, exist_ok=True)
    for build in raw:
        rows = []
        for job, variant in zip(build["jobs"], VARIANTS):
            rows.append({
                "name": f"test_{variant[2]}", "status": variant[4],
                "job_name": job["name"], "job_id": job["id"],
                "build_number": build["number"], "pipeline": "ci",
                "date": build["created_at"][:10],
            })
        (results / f"{build['created_at'][:10]}_amd.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
        )
    snapshot = source_fixture()
    current_parity = parity.build_payload({
        "schema_version": 4, "pipeline": "ci", "not_applicable": [{
            "definition_id": f"{SOURCE_FILE}#cuda-only", "reason": "Synthetic CUDA-only backend.",
        }],
    }, snapshot=snapshot, generated_at=GENERATED_AT, definition_parity=definition_parity)
    write_json(data_dir / "test_group_parity.json", parity.bounded_payload(current_parity))
    steps, architectures = matrix.parse_main_ci_steps(snapshot)
    index, latest_build = matrix.build_latest_job_index({"ci": analytics}, ["attention kernels shard"])
    current_matrix = matrix.build_matrix(
        steps, architectures, index, latest_build, {}, {}, ["attention kernels shard"],
        f"https://github.com/vllm-project/vllm/tree/{SOURCE_SHA}/.buildkite/test_areas",
    )
    current_matrix["generated_at"] = GENERATED_AT
    current_matrix["source"].update({
        "pipeline": "ci", "definition_source": "main_ci_inline_and_native_amd", "hardware_scope": "amd_mi_gpu",
        "commit_sha": SOURCE_SHA,
    })
    write_json(data_dir / "amd_test_matrix.json", current_matrix)

    now = datetime.fromisoformat(GENERATED_AT.replace("Z", "+00:00"))
    observed = []
    for build in raw:
        for job in build["jobs"]:
            source = {**job, "agent": {"meta_data": ["k8s:node=fixture-" + job["id"][:8]]}}
            observation = agents._observe("ci", build, source, re.compile("nightly", re.I))
            if observation:
                observation["day"] = build["created_at"][:10]
                observed.append(observation)
    agents._mark_infra_suspect(observed)
    generation = agents._prepare_generation(
        list(agents._rollup_rows(observed).values()),
        [agents._failing_row(row) for row in observed if row["state"] in ("hard", "soft")],
        now, pipelines=("ci",), pipeline_scope={
            "version": 3, "branch": "main", "basis": "terminal_jobs_by_build_created_at",
            "eligible_completion": "current_ci_build_creation_cohort_with_provable_completion",
            "day_basis": "build_created_at_utc", "discovery_legs": {"created": True},
            "requested_days": 7, "collected_from": "2026-10-01T00:00:00Z", "collected_to": GENERATED_AT,
            "exhaustive": True, "complete_window": False,
            "attempt_policy": "latest_attempt_per_step",
            "terminal_time_policy": "finished_at_or_terminal_build_bound_for_canceled",
        },
    )
    write_json(data_dir / "agent_health.json", generation["payload"])


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dashboard-browser-fixture-") as temporary:
        data = Path(temporary) / "data"
        shutil.copytree(ROOT / "data", data, ignore=shutil.ignore_patterns(
            ".cache", "test_results", "operations_v2", "operations_v2.json", "operations_v2.json.gz",
        ))
        ci = data / "vllm" / "ci"
        seed_current_ci(ci)
        payload = operations.build_snapshot(ci, generated_at=GENERATED_AT)
        operations.write_snapshot_bundle(ci / "operations_v2.json.gz", payload)
        build_site.DATA = data
        build_site.build_site(ROOT / "_site", False)
    print("Browser fixture site built offline; current CI evidence is synthetic and data/ is unchanged.")


if __name__ == "__main__":
    main()
