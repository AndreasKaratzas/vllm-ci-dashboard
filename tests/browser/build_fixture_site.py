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
from hashlib import sha1
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import build_site  # noqa: E402
from vllm import build_operations_snapshot as operations  # noqa: E402
from vllm import build_test_group_parity as parity  # noqa: E402
from vllm import collect_agent_health as agents  # noqa: E402
from vllm import collect_amd_test_matrix as matrix  # noqa: E402
from vllm.ci.nightly_latency import build_current_nightly_latency  # noqa: E402
from vllm.ci.reliability_history import build_all_main_reliability  # noqa: E402
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


# Main builds between the nightlies: four a day for seven days. Small builds (odd slots) run three
# groups, full builds all six. Routed Beta breaks with nightly #30001, Broken Group fails throughout,
# Attention Kernels Shard 2 flips, Basic Models passes only after a retry, and the newest build
# soft-fails Attention Kernels Shard 1.
MAIN_FIRST = datetime(2026, 10, 2, 1, tzinfo=timezone.utc)
MAIN_HOURS = (1, 7, 13, 19)
MAIN_DAYS = 7
SMALL_BUILD = ("routed-alpha", "attention-2", "basic-models")
FLAKY_SLOTS = {9, 14, 18, 22, 25}
RETRY_SLOTS = {5, 12, 17, 23}
HEADLINES = (
    "[Bugfix] Guard empty prefix cache hits in the scheduler (#61{:03d})",
    "[ROCm] Tune AITER fused MoE block sizes for MI355 (#61{:03d})",
    "[CI] Shard the distributed tests automatically (#61{:03d})",
    "[Model] Add tool-call parser for a new chat template (#61{:03d})",
)
WATCHER_ISSUE = 512
DURATION_ISSUE = 553


def main_commit(slot: int) -> str:
    return sha1(f"browser-main-{slot}".encode()).hexdigest()


def main_job(number: int, started: datetime, device: str, title: str, key: str, state: str, suffix: str = "", **extra) -> dict:
    job = {
        "id": str(uuid5(NAMESPACE_URL, f"browser-ci/{number}/{key}{suffix}")), "type": "script", "state": state,
        "name": f"{device}_1: :amd: ({device.upper()}) {title}",
        "agent_query_rules": [f"queue=amd_{device}_1"],
        "step": {"key": "attention" if key.startswith("attention") else key},
        "started_at": started.isoformat(), "finished_at": (started + timedelta(minutes=20)).isoformat(),
        **extra,
    }
    if key.startswith("attention"):
        job.update(parallel_group_index=int(key[-1]) - 1, parallel_group_total=2)
    return job


def raw_main_builds(nightly_starts: list[datetime]) -> list[dict]:
    builds = []
    broken_from = min(nightly_starts)
    for slot in range(MAIN_DAYS * len(MAIN_HOURS)):
        started = MAIN_FIRST + timedelta(days=slot // len(MAIN_HOURS), hours=MAIN_HOURS[slot % len(MAIN_HOURS)] - 1)
        number = 31000 + slot
        jobs = []
        for device, title, key, _, _, _ in VARIANTS:
            if slot % 2 and key not in SMALL_BUILD:
                continue
            state, extra = "passed", {}
            if key == "routed-beta" and started > broken_from or key == "broken":
                state = "failed"
            elif key == "attention-2" and slot in FLAKY_SLOTS:
                state = "failed"
            elif key == "attention-1" and slot == MAIN_DAYS * len(MAIN_HOURS) - 2:
                state, extra = "failed", {"soft_failed": True}
            if key == "basic-models" and slot in RETRY_SLOTS:
                retry = main_job(number, started + timedelta(minutes=25), device, title, key, "passed", "/retry",
                                 retry_source={"job_id": "", "retry_type": "automatic"}, retries_count=1, retry_type="automatic")
                first = main_job(number, started, device, title, key, "failed", retried=True, retried_in_job_id=retry["id"])
                retry["retry_source"]["job_id"] = first["id"]
                jobs += [first, retry]
                continue
            jobs.append(main_job(number, started, device, title, key, state, **extra))
        builds.append({
            "number": number, "message": HEADLINES[slot % len(HEADLINES)].format(slot), "branch": "main",
            "state": "failed" if any(job["state"] == "failed" and not job.get("retried") and not job.get("soft_failed") for job in jobs) else "passed",
            "commit": main_commit(slot), "created_at": started.isoformat(),
            "finished_at": (started + timedelta(hours=1)).isoformat(),
            "web_url": f"https://buildkite.com/vllm/ci/builds/{number}", "jobs": jobs,
        })
    return builds


def watcher_state(reliability: dict, nightlies: list[dict], mains: list[dict]) -> dict:
    """The main-failure watcher's state for the synthetic incidents above."""
    groups = {group["name"].split(") ", 1)[1]: group for group in reliability["groups"]}
    every = sorted(nightlies + mains, key=lambda build: build["created_at"])
    first_nightly = min(nightlies, key=lambda build: build["created_at"])
    before = [build for build in every if build["created_at"] < first_nightly["created_at"]][-1]
    latest = every[-1]

    def url(build: dict, job_id: str = "") -> str:
        return build["web_url"] + (f"/steps/canvas?jid={job_id}&tab=output" if job_id else "")

    def row(title: str, severity: str, status: str, good: dict | None, bad: dict) -> dict:
        group = groups[title]
        job = next(job for job in latest["jobs"] if job["name"].endswith(title)) if any(
            job["name"].endswith(title) for job in latest["jobs"]) else bad["jobs"][0]
        return {
            "name": group["name"], "hardware": group["hardware"], "queue": group["queue"], "result": "failed",
            "transition": {"severity": severity, "peak_severity": severity, "status": status},
            "build_number": latest["number"], "build_url": url(latest), "job_url": url(latest, job["id"]),
            "observed_at": latest["finished_at"],
            "good_commit": good["commit"] if good else "", "good_build_number": good["number"] if good else None,
            "bad_commit": bad["commit"], "bad_build_number": bad["number"],
            "commit_range_status": "candidate" if good else "missing_good",
            "compare_url": f"https://github.com/vllm-project/vllm/compare/{good['commit']}...{bad['commit']}" if good else "",
            "bisect_command": f"git bisect start {bad['commit']} {good['commit']}" if good else "",
        }

    flaky = groups["Attention Kernels Shard 2"]
    flips = [build for build in mains if int(build["number"]) - 31000 in FLAKY_SLOTS][-2:]
    timeline = [
        {"event": event, "group_id": flaky["group_id"], "name": flaky["name"], "hardware": flaky["hardware"], "queue": flaky["queue"],
         "build_number": build["number"], "build_url": url(build), "at": build["created_at"], "severity": "hard", "source": "pulse"}
        for failed in flips
        for event, build in (("opened", failed), ("resolved", every[every.index(failed) + 1]))
    ]
    return {
        "hardware_scope": "amd_mi_gpu", "last_run": GENERATED_AT, "issue": {"number": WATCHER_ISSUE}, "suppressed": False,
        "active": {
            groups["Routed Beta"]["group_id"]: row("Routed Beta", "hard", "confirmed", before, first_nightly),
            groups["Broken Group"]["group_id"]: row("Broken Group", "hard", "confirmed", None, every[0]),
        },
        "pending_soft": {groups["Attention Kernels Shard 1"]["group_id"]: row("Attention Kernels Shard 1", "soft", "pending", every[-2], latest)},
        "timeline": timeline,
    }


def _jitter(key: str, spread: float) -> float:
    return (int(sha1(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF - 0.5) * 2 * spread


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _slowdown(key: str, name: str, hardware: str, runs: list[dict], onset: int, levels: tuple[float, float],
              jobs: int = 1, status: str = "active") -> dict:
    """One duration-watcher row over (number, commit, created_at) runs; the step is slower from runs[onset]."""
    timed = [{**run, "wall_mins": round((levels[index >= onset]) + _jitter(f"{key}/{run['number']}", 0.6), 1)}
             for index, run in enumerate(runs)]
    before, since = timed[:onset], timed[onset:]
    baseline, recent = (round(_median([run["wall_mins"] for run in rows]), 2) for rows in (before, since))

    def ref(run: dict) -> dict:
        return {"build_number": run["number"], "build_url": f"https://buildkite.com/vllm/ci/builds/{run['number']}",
                "commit": run["commit"], "created_at": run["created_at"]}

    same_commit = before[-1]["commit"] == since[0]["commit"]
    thinned = since if len(since) <= 40 else [since[round(index * (len(since) - 1) / 39)] for index in range(40)]
    return {
        "signal_id": sha1(f"{key}/{hardware}".encode()).hexdigest()[:20],
        "name": f":amd: ({hardware.upper()}) {name}", "step_key": key, "hardware": hardware, "queue": f"amd_{hardware}_1",
        "jobs": jobs, "status": status,
        "baseline_mins": baseline, "baseline_runs": len(before), "recent_mins": recent, "recent_runs": len(since),
        "increase_mins": round(recent - baseline, 1), "increase_pct": round((recent - baseline) / baseline * 100, 1),
        "noise_mins": 0.42,
        "onset": ref(since[0]), "last_good": ref(before[-1]),
        "likely_cause": "environment" if same_commit else "vllm_change",
        "compare_url": "" if same_commit else f"https://github.com/vllm-project/vllm/compare/{before[-1]['commit']}...{since[0]['commit']}",
        "last_rise": ref(since[0]), "latest": ref(since[-1]),
        "first_detected_at": (datetime.fromisoformat(since[0]["created_at"]) + timedelta(hours=50)).isoformat(),
        "samples": [[run["number"], run["created_at"], run["wall_mins"]] for run in before[-20:] + thinned],
    }


def duration_state(mains: list[dict]) -> dict:
    """The duration watcher's state: two active slowdowns on fixture steps and one settled since September.
    Only main builds are sampled: their numbers rise with time, as Buildkite's do, unlike the fixture nightlies'."""
    every = sorted(mains, key=lambda build: build["created_at"])

    def runs(step: str) -> list[dict]:
        return [{"number": build["number"], "commit": build["commit"], "created_at": build["created_at"]}
                for build in every if any(job["step"]["key"] == step and job["state"] == "passed" for job in build["jobs"])]

    alpha, attention = runs("routed-alpha"), runs("attention")
    # A September retry on the same commit made Basic Models slower; it has not risen since, so it settled.
    september = datetime(2026, 9, 12, 2, tzinfo=timezone.utc)
    older = [{"number": 29800 + index, "commit": main_commit(-1 - index // 2 * 2),
              "created_at": (september + timedelta(hours=6 * index)).isoformat()} for index in range(40)]
    basic = older + runs("basic-models")
    rows = [
        _slowdown("routed-alpha", "Routed Alpha", "mi300", alpha, len(alpha) // 2, (24.8, 31.6)),
        _slowdown("attention", "Attention Kernels", "mi300", attention, len(attention) * 2 // 3, (18.2, 22.1), jobs=2),
        _slowdown("basic-models", "Basic Models (Other)", "mi300", basic, 21, (12.4, 15.3), status="settled"),
    ]
    return {
        "schema_version": 1, "incident_state_version": 2, "hardware_scope": "amd_mi_gpu", "last_run": GENERATED_AT,
        "issue": {"number": DURATION_ISSUE}, "suppressed": False,
        "active": {row["signal_id"]: row for row in rows},
    }


def seed_current_ci(data_dir: Path) -> None:
    raw = raw_nightlies()
    builds = summarize_pipeline_builds("ci", raw)
    analytics = {"builds": builds, "generated_at": GENERATED_AT, "job_scope": "amd_gpu", "hardware_scope": "amd_mi_gpu"}
    analytics["current_nightly_latency"] = build_current_nightly_latency(
        builds, generated_at=GENERATED_AT, source_available=True,
    )
    # A complete all-main cohort (nightlies plus the main builds between them) gives the
    # main-derived core clock a real collection time and the main CI section its builds.
    mains = raw_main_builds([datetime.fromisoformat(build["created_at"]) for build in raw])
    analytics["all_main_reliability"] = build_all_main_reliability(
        raw + mains, pipeline_slug="ci", window_days=30, generated_at=GENERATED_AT,
        nightly_pattern="nightly", collection_provenance={"exhaustive": True},
    )
    write_json(data_dir / "analytics.json", {"ci": analytics})
    write_json(data_dir / operations.MAIN_FAILURE_STATE_NAME, watcher_state(analytics["all_main_reliability"], raw, mains))
    write_json(data_dir / operations.DURATION_STATE_NAME, duration_state(mains))
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
    agents._attribute_failures(observed)
    generation = agents._prepare_generation(
        list(agents._rollup_rows(observed).values()),
        [agents._failing_row(row) for row in observed if row["state"] in ("hard", "soft")],
        now, pipelines=("ci",), pipeline_scope={
            "version": 3, "branch": "main", "basis": "terminal_jobs_by_build_created_at",
            "eligible_completion": "current_ci_build_creation_cohort_with_provable_completion",
            "day_basis": "build_created_at_utc", "discovery_legs": {"created": True},
            "requested_days": 7, "collected_from": "2026-10-01T00:00:00Z", "collected_to": GENERATED_AT,
            "exhaustive": True, "complete_window": False,
            "attempt_policy": agents.ATTEMPT_POLICY,
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
