"""Current source coverage must not inherit historical side-pipeline counts."""

from __future__ import annotations

import io
import json
import tarfile
from copy import deepcopy
from pathlib import Path

import pytest

from vllm import build_test_group_parity as parity
from vllm import main_ci_definitions as source

SHA = "a" * 40
RUNTIME_SHA = "b" * 40
GENERATED_AT = "2026-10-08T20:00:00Z"
FILE = ".buildkite/test_areas/kernels.yaml"


def snapshot() -> source.MainCISnapshot:
    def step(key, title, mirror=None, **extra):
        row = {
            "key": key,
            "label": f":nvidia: (H100) {title}",
            "device": "h100",
            "commands": ["pytest tests/workload.py"],
            **extra,
        }
        if mirror is not None:
            row["mirror"] = {
                "amd": {"label": f":amd: (MI355) {title}", "device": "mi355", **mirror}
            }
        return row

    return source.MainCISnapshot(
        SHA,
        {
            source.CI_CONFIG: {"job_dirs": [".buildkite/test_areas"]},
            FILE: {
                "group": "Kernels",
                "steps": [
                    step("required", "Required", {}),
                    step("required-replica", "Required", {}, device="h200"),
                    step("optional", "Optional", {"optional": True}),
                    step("soft", "Soft", {"soft_fail": True}),
                    step("missing", "Missing"),
                    step("cuda-only", "CUDA only"),
                    {
                        "key": "native",
                        "label": ":amd: (MI355) Native AMD",
                        "device": "mi355",
                        "commands": ["pytest tests/rocm.py"],
                    },
                    {
                        "key": "cpu",
                        "label": ":computer: (CPU) CPU",
                        "device": "cpu",
                        "commands": ["pytest tests/cpu.py"],
                    },
                ],
            },
        },
        GENERATED_AT,
    )


def policy():
    return {
        "schema_version": 4,
        "pipeline": "ci",
        "not_applicable": [
            {"definition_id": f"{FILE}#cuda-only", "reason": "NVIDIA-specific backend."}
        ],
    }


def test_source_counts_logical_workloads_and_separates_configured_required_routes():
    payload = parity.build_payload(
        policy(),
        snapshot=snapshot(),
        generated_at=GENERATED_AT,
        definition_parity={
            "source": {"commit_sha": RUNTIME_SHA},
            "matches": [{"amd_label": "Legacy side pipeline"}],
        },
    )
    assert payload["schema_version"] == 3
    assert payload["summary"] == {
        "upstream_physical_definitions": 6,
        "upstream_logical_groups": 5,
        "applicable_groups": 4,
        "main_complete_groups": 3,
        "unsupported_groups": 1,
        "action_groups": 1,
        "main_missing_groups": 1,
        "main_applicable_rate_pct": 75.0,
        "main_required_groups": 1,
        "main_required_rate_pct": 25.0,
        "main_optional_only_groups": 1,
        "main_soft_fail_only_groups": 1,
    }
    assert payload["source"]["pipeline"] == "ci"
    assert payload["source"]["current_definition_commit_sha"] == SHA
    assert payload["source"]["runtime_source_commit_sha"] == RUNTIME_SHA
    assert payload["rocm_inventory"]["main"]["physical_definitions"] == 5
    assert payload["rocm_inventory"]["main"]["inline_mirror_definitions"] == 4
    assert payload["mirror_inventory"]["summary"] == {
        "total": 4,
        "required": 2,
        "optional": 1,
        "soft_fail": 1,
    }
    groups = {row["title"]: row for row in payload["groups"]}
    assert len(groups["Required"]["upstream_definition_ids"]) == 2
    assert groups["CUDA only"]["gate_kind"] == "not_applicable"
    assert groups["Missing"]["gate_kind"] == "missing"
    assert "Legacy" not in json.dumps(payload)


def test_current_yaml_changes_update_denominator_without_a_static_inventory_review():
    current = snapshot()
    current.files[FILE]["steps"].append(
        {"key": "new", "label": ":nvidia: (H100) Newly landed", "device": "h100"}
    )
    payload = parity.build_payload(policy(), snapshot=current)
    assert payload["summary"]["applicable_groups"] == 5
    assert payload["summary"]["main_missing_groups"] == 2
    assert payload["summary"]["main_applicable_rate_pct"] == 60.0


def test_new_main_route_overrides_old_not_applicable_classification():
    current = snapshot()
    excluded = current.files[FILE]["steps"][5]
    excluded["mirror"] = {"amd": {"label": ":amd: (MI355) CUDA only", "device": "mi355"}}
    payload = parity.build_payload(policy(), snapshot=current)
    assert payload["summary"]["unsupported_groups"] == 0
    assert payload["summary"]["main_complete_groups"] == 4


def test_not_applicable_requires_every_hardware_replica_to_be_classified():
    current = snapshot()
    current.files[FILE]["steps"].append(
        {"key": "cuda-only-new-replica", "label": ":nvidia: (B200) CUDA only", "device": "b200"}
    )
    payload = parity.build_payload(policy(), snapshot=current)
    assert (
        next(row for row in payload["groups"] if row["title"] == "CUDA only")["state"] == "action"
    )


@pytest.mark.parametrize(
    "mutation,message",
    [
        (lambda p: p.update(pipeline="amd-ci"), "authoritative ci"),
        (lambda p: p["not_applicable"].append(deepcopy(p["not_applicable"][0])), "duplicate"),
        (lambda p: p["not_applicable"][0].update(definition_id=f"{FILE}#0"), "explicit"),
        (lambda p: p["not_applicable"][0].update(reason=""), "reason"),
    ],
)
def test_policy_rejects_unstable_keys_and_legacy_authority(tmp_path, mutation, message):
    document = policy()
    mutation(document)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match=message):
        parity.load_review(path)


def test_mirror_expansion_inherits_execution_and_flags_and_keeps_exact_source():
    current = snapshot()
    parent = current.files[FILE]["steps"][0]
    parent.update(optional=True, working_dir="tests", parallelism=3)
    parent["mirror"]["amd"]["commands"] = ["pytest tests/rocm-specific.py"]
    routes = source.amd_source_steps(current)
    route = routes[0]
    assert route["commands"] == ["pytest tests/rocm-specific.py"]
    assert route["optional"] is True
    assert route["working_dir"] == "tests"
    assert route["parallelism"] == 3
    assert route["source_file"] == FILE
    assert route["upstream_definition_id"] == f"{FILE}#required"
    assert source.route_is_required(route) is False


def test_source_loader_resolves_once_then_reads_only_the_pinned_archive(monkeypatch):
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        for path, content in {
            source.CI_CONFIG: "job_dirs: [.buildkite/test_areas]",
            FILE: "steps: []",
            ".buildkite/test-amd.yaml": "steps: [legacy]",
        }.items():
            blob = content.encode()
            entry = tarfile.TarInfo(f"repo-root/{path}")
            entry.size = len(blob)
            archive.addfile(entry, io.BytesIO(blob))
    calls = []

    class Response:
        content = archive_buffer.getvalue()

        def raise_for_status(self):
            pass

        def json(self):
            return {"sha": SHA}

    def get(url, **kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr(source.requests, "get", get)
    current = source.load_snapshot("main")
    assert calls == [f"{source.API_BASE}/commits/main", f"{source.API_BASE}/tarball/{SHA}"]
    assert current.commit_sha == SHA
    assert ".buildkite/test-amd.yaml" not in current.files


def test_publish_writes_current_source_payload_and_retains_lkg_on_budget_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(parity, "load_review", lambda path: policy())
    path, payload = parity.publish(
        output_dir=tmp_path, snapshot=snapshot(), generated_at=GENERATED_AT
    )
    assert json.loads(path.read_text()) == payload
    monkeypatch.setattr(parity, "TEST_GROUP_PARITY_MAX_BYTES", 128)
    with pytest.raises(RuntimeError, match="exceeds its byte budget"):
        parity.publish(output_dir=tmp_path, snapshot=snapshot())
    assert json.loads(path.read_text()) == payload


def test_bounded_detail_keeps_action_rows_and_exact_aggregate_counts():
    current = snapshot()
    current.files[FILE]["steps"].extend(
        {"key": f"extra-{index}", "label": f":nvidia: (H100) Extra {index}", "device": "h100"}
        for index in range(100)
    )
    payload = parity.build_payload(policy(), snapshot=current)
    bounded = parity.bounded_payload(payload, max_bytes=20_000)
    assert bounded["summary"] == payload["summary"]
    assert bounded["publication_retention"]["complete_relative_to_source"] is False
    assert bounded["publication_retention"]["aggregate_summary_complete"] is True
    assert all(row["state"] == "action" for row in bounded["groups"])
    assert bounded["mirror_inventory"] == payload["mirror_inventory"]
