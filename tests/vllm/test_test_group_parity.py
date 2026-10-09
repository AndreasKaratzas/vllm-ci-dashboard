"""Current source coverage must not inherit historical side-pipeline counts."""

from __future__ import annotations

import io
import base64
import hashlib
import json
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from vllm import build_test_group_parity as parity
from vllm import main_ci_definitions as source

SHA = "a" * 40
RUNTIME_SHA = "b" * 40
GENERATED_AT = "2026-10-08T20:00:00Z"
FILE = ".buildkite/test_areas/kernels.yaml"


@pytest.fixture(autouse=True)
def isolated_runtime_source(monkeypatch):
    cached = [source.runtime_snapshot, source._runtime_commit_tree, source._runtime_tree,
              source._runtime_blob, source._runtime_definition_files]
    for function in cached:
        function.cache_clear()
    for name, value in (("_RUNTIME_SOURCE_STARTS", 0), ("_RUNTIME_SOURCE_STARTED_AT", None),
                        ("_RUNTIME_SOURCE_ACTIVE_SECONDS", 0.0), ("_RUNTIME_SOURCE_ACTIVE_DEPTH", 0),
                        ("_RUNTIME_SOURCE_GRAPHQL_FALLBACKS", 0), ("_RUNTIME_PRIMED_BUILDKITE_TREES", {})):
        monkeypatch.setattr(source, name, value)
    yield
    for function in cached:
        function.cache_clear()


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


def test_gpu_inventory_excludes_explicit_cpu_execution_even_on_mi_routes():
    current = snapshot()
    current.files[FILE]["steps"].extend([
        {"key": "torch-abi", "label": ":amd: (MI250) Torch Stable ABI Audit", "device": "mi300_1", "no_gpu": True},
        {"key": "cpu-mirror", "label": ":nvidia: (H100) CPU audit", "device": "h100", "mirror": {"amd": {"device": "mi300_1", "no_gpu": True}}},
        {"key": "gpu-cpu-offload", "label": ":nvidia: (H100) GPU CPU offload", "device": "h100", "mirror": {"amd": {"device": "mi300_1", "label": ":amd: (MI300) GPU CPU offload"}}},
        {"key": "cuda-no-gpu", "label": ":nvidia: (H100) ABI audit", "device": "h100", "no_gpu": True},
    ])
    routes = source.amd_source_steps(current)
    assert "torch-abi" not in {row["key"] for row in routes}
    assert "amd-cpu-mirror" not in {row["key"] for row in routes}
    assert "amd-gpu-cpu-offload" in {row["key"] for row in routes}
    payload = parity.build_payload(policy(), snapshot=current)
    by_title = {row["title"]: row for row in payload["groups"]}
    assert "ABI audit" not in by_title
    assert by_title["CPU audit"]["state"] == "action"
    assert by_title["GPU CPU offload"]["state"] == "existing"
    assert payload["source"]["hardware_scope"] == "amd_mi_gpu"


def test_runtime_cpu_annotation_requires_exact_commit_step_and_physical_route():
    current = snapshot()
    current.files[FILE]["steps"].append({
        "key": "amd-torch-stable-abi-audit", "label": ":amd: (MI250) Torch Stable ABI Audit",
        "device": "mi300_1", "no_gpu": True,
    })
    jobs = [
        {"id": "exact", "step_key": "amd-torch-stable-abi-audit", "name": ":amd: (MI250) Torch Stable ABI Audit", "agent_queue": "amd_mi300_1"},
        {"id": "wrong-pool", "step_key": "amd-torch-stable-abi-audit", "name": ":amd: (MI250) Torch Stable ABI Audit", "agent_queue": "amd_mi250"},
        {"id": "foreign-step", "step_key": "foreign", "name": ":amd: (MI250) Torch Stable ABI Audit", "agent_queue": "amd_mi300_1"},
        {"id": "label-only", "name": ":amd: (MI250) Torch Stable ABI Audit", "agent_queue": "amd_mi300_1"},
    ]
    result = source.annotate_runtime_source_scope({"commit": SHA, "jobs": jobs}, snapshot=current)
    assert [job["id"] for job in result["jobs"] if job.get("source_no_gpu")] == ["exact", "label-only"]
    assert result["jobs"][0]["source_scope_commit"] == SHA
    assert "source_no_gpu" not in jobs[0]
    with pytest.raises(source.RuntimeSourceError) as failure:
        source.annotate_runtime_source_scope({"commit": RUNTIME_SHA, "jobs": jobs}, snapshot=current)
    assert failure.value.reason_class == "schema-drift"


def test_runtime_annotation_never_trusts_a_retained_cpu_exclusion():
    current = snapshot()
    result = source.annotate_runtime_source_scope({"commit": SHA, "jobs": [{
        "step_key": "native", "agent_queue": "amd_mi355", "name": ":amd: (MI355) Native AMD",
        "source_no_gpu": True, "source_scope_commit": RUNTIME_SHA,
    }]}, snapshot=current)
    assert "source_no_gpu" not in result["jobs"][0]


def historical_keyless_snapshot():
    current = snapshot()
    current = source.MainCISnapshot(SHA, current.files, GENERATED_AT, "c" * 40)
    # These execution fields reproduce the keyless CPU mirror at immutable
    # commit 56d72701380bc68955cc0aa9367636df8356e3c8; commands are irrelevant to scope.
    current.files[".buildkite/test_areas/docker.yaml"] = {"steps": [{
        "label": ":computer: (CPU) Docker Build Metadata", "device": "cpu-small",
        "mirror": {"amd": {"label": ":amd: (MI250) Docker Build Metadata",
                            "device": "mi250_1", "no_gpu": True}},
    }]}
    current.files[FILE]["steps"].extend([
        {"label": ":amd: (MI355) GPU CPU Offload", "device": "mi355"},
        {"label": ":computer: (CPU) Infra Metadata", "device": "cpu-small"},
    ])
    return current


def test_historical_keyless_execution_does_not_relax_current_source_inventory():
    current = historical_keyless_snapshot()
    for collect in (source.source_steps, source.amd_source_steps):
        with pytest.raises(ValueError, match="explicit stable key"):
            collect(current)
    with pytest.raises(ValueError, match="explicit stable key"):
        parity.build_payload(policy(), snapshot=current)

    routes = source.amd_source_steps(current, include_cpu=True, require_keys=False)
    keyless = [row for row in routes if not row["key"]]
    assert {row["label"] for row in keyless} == {
        ":amd: (MI250) Docker Build Metadata", ":amd: (MI355) GPU CPU Offload",
    }
    assert all(row["definition_id"].endswith("#yaml-index:" + str(row["yaml_index"]))
               for row in keyless)


def test_keyless_cpu_route_joins_only_exact_label_and_physical_pool():
    current = historical_keyless_snapshot()
    index = source.runtime_scope_index(current)
    assert index["cpu_routes"] == [{"key": "", "label": "docker build metadata",
                                   "agent_pool": "mi250_1"}]
    assert source.validate_runtime_scope_index(index, expected_commit=SHA) == index
    jobs = [
        {"id": "keyless-job", "name": ":amd: (MI250) Docker Build Metadata",
         "agent_queue": "amd_mi250_1"},
        {"id": "generated-key", "step_key": "generated-docker-key",
         "name": ":amd: (MI250) Docker Build Metadata", "agent_queue": "amd_mi250_1"},
        {"id": "different-pool", "name": ":amd: (MI250) Docker Build Metadata",
         "agent_queue": "amd_mi300_1"},
        {"id": "different-label", "name": ":amd: (MI250) GPU Docker Build Metadata",
         "agent_queue": "amd_mi250_1"},
        {"id": "cpu-offload-gpu", "name": ":amd: (MI355) GPU CPU Offload",
         "agent_queue": "amd_mi355", "source_no_gpu": True},
    ]
    result = source.annotate_runtime_source_scope({"commit": SHA, "jobs": jobs}, scope_index=index)
    assert [job["id"] for job in result["jobs"] if job.get("source_no_gpu")] == [
        "keyless-job", "generated-key",
    ]
    assert all(job["source_scope_commit"] == SHA for job in result["jobs"])
    assert "source_no_gpu" not in jobs[0]
    with pytest.raises(source.RuntimeSourceError):
        source.annotate_runtime_source_scope({"commit": RUNTIME_SHA, "jobs": jobs}, scope_index=index)


@pytest.mark.parametrize("gpu_key", [None, "actual-gpu-step"])
def test_keyless_cpu_label_cannot_exclude_a_same_pool_gpu_definition(gpu_key):
    current = historical_keyless_snapshot()
    gpu = {"label": ":amd: (MI355) Docker Build Metadata", "device": "mi250_1"}
    if gpu_key is not None:
        gpu["key"] = gpu_key
    current.files[FILE]["steps"].append(gpu)
    index = source.runtime_scope_index(current)
    assert index["cpu_routes"] == []
    result = source.annotate_runtime_source_scope({"commit": SHA, "jobs": [{
        "step_key": "generated-key", "name": ":amd: (MI250) Docker Build Metadata",
        "agent_queue": "amd_mi250_1", "source_no_gpu": True,
    }]}, scope_index=index)
    assert "source_no_gpu" not in result["jobs"][0]


@pytest.mark.parametrize("key", [None, "", " ", 12, False])
def test_historical_execution_parser_still_rejects_present_malformed_keys(key):
    current = historical_keyless_snapshot()
    current.files[FILE]["steps"][-1]["key"] = key
    with pytest.raises(ValueError, match="explicit stable key"):
        source.runtime_scope_index(current)


@pytest.mark.parametrize("key", [None, "", " ", False, [], {}, 12])
@pytest.mark.parametrize("runtime", [False, True])
def test_present_malformed_mirror_keys_are_rejected_in_both_source_policies(key, runtime):
    current = snapshot()
    current.files[FILE]["steps"][0]["mirror"]["amd"].update(key=key, no_gpu=True)
    with pytest.raises(ValueError, match="AMD mirror requires a nonempty string key"):
        if runtime:
            source.runtime_scope_index(current)
        else:
            source.amd_source_steps(current, include_cpu=True)


def test_present_valid_mirror_key_is_preserved_without_a_parent_key():
    current = historical_keyless_snapshot()
    current.files[".buildkite/test_areas/docker.yaml"]["steps"][0]["mirror"]["amd"]["key"] = "exact-cpu-step"
    index = source.runtime_scope_index(current)
    assert index["cpu_routes"][0]["key"] == "exact-cpu-step"
    result = source.annotate_runtime_source_scope({"commit": SHA, "jobs": [
        {"step_key": "exact-cpu-step", "name": ":amd: (MI250) Docker Build Metadata",
         "agent_queue": "amd_mi250_1"},
        {"step_key": "different-step", "name": ":amd: (MI250) Docker Build Metadata",
         "agent_queue": "amd_mi250_1"},
    ]}, scope_index=index)
    assert result["jobs"][0]["source_no_gpu"] is True
    assert "source_no_gpu" not in result["jobs"][1]
    assert source.amd_source_steps(snapshot())[0]["key"] == "amd-required"


@pytest.mark.parametrize("document", [{"steps": "invalid"}, {"steps": [None]}, []])
def test_historical_execution_parser_still_rejects_malformed_source_documents(document):
    current = historical_keyless_snapshot()
    current.files[FILE] = document
    with pytest.raises(ValueError, match="malformed"):
        source.runtime_scope_index(current)


def test_keyless_cpu_definition_without_a_label_cannot_invent_an_execution_join():
    current = historical_keyless_snapshot()
    del current.files[".buildkite/test_areas/docker.yaml"]["steps"][0]["mirror"]["amd"]["label"]
    # The parent has no usable execution label either.
    del current.files[".buildkite/test_areas/docker.yaml"]["steps"][0]["label"]
    with pytest.raises(ValueError, match="requires an execution label"):
        source.runtime_scope_index(current)


def test_authenticated_exact_pin_index_rejoins_new_attempts_without_source_requests(monkeypatch):
    current = snapshot()
    current = source.MainCISnapshot(current.commit_sha, current.files, current.fetched_at, "c" * 40)
    current.files[FILE]["steps"].append({
        "key": "amd-torch-stable-abi-audit", "label": ":amd: (MI250) Torch Stable ABI Audit",
        "device": "mi300_1", "no_gpu": True,
    })
    index = source.runtime_scope_index(current)
    assert source.validate_runtime_scope_index(index, expected_commit=SHA) == index
    monkeypatch.setattr(source, "runtime_snapshot", lambda commit: pytest.fail("immutable index reuse must not issue a source request"))
    result = source.annotate_runtime_source_scope({"commit": SHA, "jobs": [{
        "id": "new-attempt", "step_key": "amd-torch-stable-abi-audit",
        "name": ":amd: (MI250) Torch Stable ABI Audit", "agent_queue": "amd_mi300_1",
    }]}, scope_index=index)
    assert result["jobs"][0]["source_no_gpu"] is True
    assert result["jobs"][0]["id"] == "new-attempt"
    assert result["source_scope_commit"] == SHA
    assert result["source_definition_tree_sha"] == "c" * 40
    with pytest.raises(source.RuntimeSourceError) as failure:
        source.annotate_runtime_source_scope({"commit": RUNTIME_SHA, "jobs": []}, scope_index=index)
    assert failure.value.reason_class == "schema-drift"
    assert failure.value.commit_sha == RUNTIME_SHA


@pytest.mark.parametrize("mutation", [
    lambda index: index.update(version=True),
    lambda index: index.update(definition_tree_sha=""),
    lambda index: index["cpu_routes"].append({"key": "x", "label": "x", "agent_pool": "cpu"}),
    lambda index: index["cpu_routes"].append(deepcopy(index["cpu_routes"][0])),
    lambda index: index.update(unexpected=True),
])
def test_runtime_index_rejects_malformed_or_unbound_cached_source(mutation):
    index = {"version": 1, "commit_sha": SHA, "definition_tree_sha": "c" * 40,
             "cpu_routes": [{"key": "abi", "label": "abi", "agent_pool": "mi300_1"}]}
    mutation(index)
    with pytest.raises(ValueError):
        source.validate_runtime_scope_index(index, expected_commit=SHA)


def test_runtime_source_loader_reuses_immutable_ci_tree_across_exact_commits(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-runtime-source-token")
    source.runtime_snapshot.cache_clear()
    source._runtime_commit_tree.cache_clear()
    source._runtime_tree.cache_clear()
    source._runtime_blob.cache_clear()
    source._runtime_definition_files.cache_clear()
    config = b"job_dirs: [.buildkite/test_areas]\n"
    definitions = b"steps:\n- key: torch\n  label: ':amd: (MI250) Torch ABI'\n  device: mi300_1\n  no_gpu: true\n"

    def blob(payload):
        oid = hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()
        return oid, {"sha": oid, "encoding": "base64", "size": len(payload), "content": base64.b64encode(payload).decode()}

    config_oid, config_json = blob(config)
    definitions_oid, definitions_json = blob(definitions)

    def git_tree(rows):
        ordered = sorted(rows, key=lambda row: row["path"] + ("/" if row["type"] == "tree" else ""))
        payload = b"".join(row["mode"].lstrip("0").encode() + b" " + row["path"].encode() + b"\0" + bytes.fromhex(row["sha"]) for row in ordered)
        oid = hashlib.sha1(f"tree {len(payload)}\0".encode() + payload).hexdigest()
        return oid, {"sha": oid, "truncated": False, "tree": rows}

    area_tree, area_json = git_tree([{"path": "kernels.yaml", "sha": definitions_oid, "type": "blob", "mode": "100644"}])
    tree, tree_json = git_tree([
        {"path": "ci_config.yaml", "sha": config_oid, "type": "blob", "mode": "100644"},
        {"path": "test_areas", "sha": area_tree, "type": "tree", "mode": "040000"},
    ])
    root, root_json = git_tree([{"path": ".buildkite", "sha": tree, "type": "tree", "mode": "040000"}])
    responses = {
        f"commits/{SHA}": {"sha": SHA, "tree": {"sha": root}},
        f"commits/{RUNTIME_SHA}": {"sha": RUNTIME_SHA, "tree": {"sha": root}},
        f"trees/{root}": deepcopy(root_json),
        f"trees/{tree}": tree_json,
        f"trees/{area_tree}": area_json,
        f"blobs/{config_oid}": config_json,
        f"blobs/{definitions_oid}": definitions_json,
    }
    calls = []

    class Response:
        def __init__(self, value):
            self.value = value
            self.content = json.dumps(value).encode()

        def raise_for_status(self):
            pass

        def json(self):
            return self.value

    def get(url, **kwargs):
        path = url.removeprefix(f"{source.API_BASE}/git/")
        calls.append(path)
        return Response(responses[path])

    monkeypatch.setattr(source.requests, "get", get)
    try:
        first = source.runtime_snapshot(SHA)
        second = source.runtime_snapshot(RUNTIME_SHA)
        assert first.commit_sha == SHA and second.commit_sha == RUNTIME_SHA
        assert first.definition_tree_sha == second.definition_tree_sha == tree
        assert first.files is second.files
        assert len(calls) == 7
        assert f"trees/{SHA}" not in calls and f"trees/{RUNTIME_SHA}" not in calls
        assert calls.count(f"trees/{root}") == 1
        assert calls.count(f"trees/{tree}") == 1
        assert not any("tarball" in call for call in calls)
        source._runtime_tree.cache_clear()
        responses[f"trees/{root}"]["truncated"] = True
        source.runtime_snapshot.cache_clear()
        with pytest.raises(ValueError, match="incomplete"):
            source.runtime_snapshot(SHA)
        source._runtime_blob.cache_clear()
        responses[f"blobs/{definitions_oid}"]["content"] = base64.b64encode(b"x" * len(definitions)).decode()
        with pytest.raises(ValueError, match="Git identity"):
            source._runtime_blob(definitions_oid)
    finally:
        source.runtime_snapshot.cache_clear()
        source._runtime_commit_tree.cache_clear()
        source._runtime_tree.cache_clear()
        source._runtime_blob.cache_clear()
        source._runtime_definition_files.cache_clear()


def test_runtime_source_acquisition_without_github_token_stops_before_request(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(source.requests, "get", lambda *args, **kwargs: pytest.fail("missing authentication must not start HTTP"))
    before = source.runtime_source_request_stats()
    with pytest.raises(source.RuntimeSourceError) as failure:
        source._runtime_source_json(f"commits/{SHA}")
    assert failure.value.reason_class == "dependency-unavailable"
    assert source.runtime_source_request_stats()["request_starts"] == before["request_starts"]


def _runtime_batch_response(pins):
    rows = [{"name": ".buildkite", "mode": 0o040000, "type": "tree", "oid": "b" * 40},
            {"name": "README.md", "mode": 0o100644, "type": "blob", "oid": "c" * 40}]
    payload = b"".join(format(row["mode"], "o").encode() + b" " + row["name"].encode() + b"\0" + bytes.fromhex(row["oid"])
                       for row in sorted(rows, key=lambda row: row["name"] + ("/" if row["type"] == "tree" else "")))
    root = hashlib.sha1(f"tree {len(payload)}\0".encode() + payload).hexdigest()
    return {"data": {"repository": {"nameWithOwner": source.REPOSITORY, **{
        f"c{index}": {"__typename": "Commit", "oid": pin, "tree": {"oid": root, "entries": deepcopy(rows)}}
        for index, pin in enumerate(pins)}}}}


def test_runtime_source_batches_exact_commits_and_never_reloads_their_root_metadata(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-runtime-source-token")
    monkeypatch.setattr(source, "_RUNTIME_PRIMED_BUILDKITE_TREES", {})
    pins = [f"{index:040x}" for index in range(1, 52)]
    batches = [pins[:50], pins[50:]]
    calls = []

    def post(url, **kwargs):
        batch = batches[len(calls)]
        calls.append(kwargs["json"]["query"])
        assert url == "https://api.github.com/graphql"
        assert all(f'object(oid: "{pin}")' in calls[-1] for pin in batch)
        value = _runtime_batch_response(batch)
        return type("Response", (), {"content": json.dumps(value).encode(), "raise_for_status": lambda self: None, "json": lambda self: value})()

    monkeypatch.setattr(source.requests, "post", post)
    monkeypatch.setattr(source.requests, "get", lambda *args, **kwargs: pytest.fail("primed roots must not issue REST metadata requests"))
    monkeypatch.setattr(source, "_runtime_definition_files", lambda tree: {
        source.CI_CONFIG: {"job_dirs": [".buildkite/test_areas"]}, FILE: {"steps": []}})
    source.runtime_snapshot.cache_clear()
    try:
        assert source.prewarm_runtime_snapshots(pins) == {pin: "b" * 40 for pin in pins}
        assert source.prewarm_runtime_snapshots(pins) == {pin: "b" * 40 for pin in pins}
        assert len(calls) == 2
        assert source.runtime_snapshot(pins[0]).definition_tree_sha == "b" * 40
        assert source.runtime_snapshot(pins[-1]).commit_sha == pins[-1]
    finally:
        source.runtime_snapshot.cache_clear()


@pytest.mark.parametrize("tamper", ("missing", "extra", "commit", "kind", "tree", "mode", "duplicate", "repository"))
def test_runtime_source_batch_rejects_partial_or_contradictory_identity_before_admitting_any_pin(monkeypatch, tamper):
    monkeypatch.setenv("GITHUB_TOKEN", "test-runtime-source-token")
    monkeypatch.setattr(source, "_RUNTIME_PRIMED_BUILDKITE_TREES", {})
    value = _runtime_batch_response([SHA, RUNTIME_SHA])
    repo = value["data"]["repository"]
    if tamper == "missing":
        repo.pop("c1")
    elif tamper == "extra":
        repo["c2"] = deepcopy(repo["c0"])
    elif tamper == "commit":
        repo["c1"]["oid"] = "f" * 40
    elif tamper == "kind":
        repo["c1"]["__typename"] = "Tree"
    elif tamper == "tree":
        repo["c1"]["tree"]["oid"] = "f" * 40
    elif tamper == "mode":
        repo["c1"]["tree"]["entries"][0]["mode"] = True
    elif tamper == "duplicate":
        repo["c1"]["tree"]["entries"].append(deepcopy(repo["c1"]["tree"]["entries"][0]))
    else:
        repo["nameWithOwner"] = "another/repository"
    response = type("Response", (), {"content": json.dumps(value).encode(), "raise_for_status": lambda self: None, "json": lambda self: value})()
    monkeypatch.setattr(source.requests, "post", lambda *args, **kwargs: response)
    monkeypatch.setattr(source.requests, "get", lambda *args, **kwargs: pytest.fail("invalid GraphQL proof must not fall back to REST"))
    with pytest.raises(ValueError):
        source.prewarm_runtime_snapshots([SHA, RUNTIME_SHA])
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {}


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


def _source_response(value, *, status_code=200):
    return SimpleNamespace(content=json.dumps(value).encode(), status_code=status_code,
                           raise_for_status=lambda: None, json=lambda: value)


def _fallback_root_responses(pins):
    value = _runtime_batch_response(pins)
    tree = value["data"]["repository"]["c0"]["tree"]
    root = {"sha": tree["oid"], "truncated": False, "tree": [
        {"path": row["name"], "mode": format(row["mode"], "06o"),
         "type": row["type"], "sha": row["oid"]} for row in tree["entries"]
    ]}
    return {**{f"commits/{pin}": {"sha": pin, "tree": {"sha": tree["oid"]}} for pin in pins},
            f"trees/{tree['oid']}": root}


@pytest.mark.parametrize("partial_data", [False, True])
def test_graphql_errors_require_independent_exact_rest_proof_for_the_whole_batch(monkeypatch, capsys, partial_data):
    monkeypatch.setenv("GITHUB_TOKEN", "source-test-token")
    pins = [SHA, RUNTIME_SHA]
    errored = _runtime_batch_response(pins) if partial_data else {"data": None}
    if partial_data:
        errored["data"]["repository"]["c1"]["oid"] = "f" * 40
    errored["errors"] = [{"message": "private response must never be logged", "type": "RATE_LIMITED", "path": ["repository", "c1"]}]
    monkeypatch.setattr(source.requests, "post", lambda *args, **kwargs: _source_response(errored))
    responses = _fallback_root_responses(pins)
    calls = []

    def get(url, **kwargs):
        path = url.removeprefix(f"{source.API_BASE}/git/")
        calls.append(path)
        return _source_response(responses[path])

    monkeypatch.setattr(source.requests, "get", get)
    assert source.prewarm_runtime_snapshots(pins) == {pin: "b" * 40 for pin in pins}
    assert calls == [f"commits/{SHA}", next(path for path in responses if path.startswith("trees/")),
                     f"commits/{RUNTIME_SHA}"]
    assert source.runtime_source_request_stats()["request_starts"] == 4
    assert source.runtime_source_request_stats()["graphql_rest_fallbacks"] == 1
    output = capsys.readouterr().out
    diagnostic = json.loads(output[output.index("{"):])
    assert diagnostic["graphql_error_types"] == ["RATE_LIMITED"]
    assert diagnostic["failed_commit_shas"] == [RUNTIME_SHA]
    assert diagnostic["verified_pins"] == 2
    assert "private" not in output
    assert source.prewarm_runtime_snapshots(pins) == {pin: "b" * 40 for pin in pins}
    assert len(calls) == 3


@pytest.mark.parametrize("errors", [None, {}, "error", [None], [{}], [{"message": []}],
                                   [{"message": ""}], [{"message": "   "}]])
def test_malformed_graphql_errors_cannot_trigger_rest_fallback(monkeypatch, errors):
    monkeypatch.setenv("GITHUB_TOKEN", "source-test-token")
    monkeypatch.setattr(source.requests, "post", lambda *args, **kwargs:
                        _source_response({"data": None, "errors": errors}))
    monkeypatch.setattr(source.requests, "get", lambda *args, **kwargs:
                        pytest.fail("malformed GraphQL errors must remain fatal"))
    with pytest.raises(source.RuntimeSourceError) as failure:
        source.prewarm_runtime_snapshots([SHA])
    assert failure.value.reason_class == "schema-drift"
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {}
    assert source.runtime_source_request_stats()["graphql_rest_fallbacks"] == 0


@pytest.mark.parametrize("failure_kind", ["wrong-commit", "wrong-root", "tampered-tree", "unavailable"])
def test_rest_fallback_never_promotes_a_partial_batch_or_unavailable_pin(monkeypatch, failure_kind):
    monkeypatch.setenv("GITHUB_TOKEN", "source-test-token")
    pins = [SHA, RUNTIME_SHA]
    monkeypatch.setattr(source.requests, "post", lambda *args, **kwargs:
                        _source_response({"errors": [{"message": "private error"}]}))
    responses = _fallback_root_responses(pins)
    if failure_kind == "wrong-commit":
        responses[f"commits/{RUNTIME_SHA}"]["sha"] = "f" * 40
    elif failure_kind == "wrong-root":
        responses[f"commits/{RUNTIME_SHA}"]["tree"]["sha"] = "f" * 40
        responses["trees/" + "f" * 40] = deepcopy(next(value for path, value in responses.items() if path.startswith("trees/")))
    elif failure_kind == "tampered-tree":
        root = next(value for path, value in responses.items() if path.startswith("trees/"))
        root["tree"][0]["sha"] = "f" * 40

    def get(url, **kwargs):
        path = url.removeprefix(f"{source.API_BASE}/git/")
        if failure_kind == "unavailable" and path == f"commits/{RUNTIME_SHA}":
            response = source.requests.Response()
            response.status_code = 404
            raise source.requests.HTTPError("private raw error", response=response)
        return _source_response(responses[path])

    monkeypatch.setattr(source.requests, "get", get)
    with pytest.raises(source.RuntimeSourceError) as failure:
        source.prewarm_runtime_snapshots(pins)
    assert failure.value.reason_class == ("dependency-unavailable" if failure_kind == "unavailable" else "schema-drift")
    assert failure.value.http_status == (404 if failure_kind == "unavailable" else None)
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {}
    assert "private raw error" not in str(failure.value)
    assert failure.value.commit_sha in pins


def test_source_budget_excludes_unrelated_idle_and_charges_nested_verification(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "source-test-token")
    clock = [100.0]
    monkeypatch.setattr(source, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def get(url, **kwargs):
        clock[0] += 2.0
        return _source_response({"sha": url.rsplit("/", 1)[-1], "tree": {"sha": "c" * 40}})

    monkeypatch.setattr(source.requests, "get", get)
    assert source._runtime_commit_tree(SHA) == "c" * 40
    clock[0] += 1800.0  # Buildkite work between exact source acquisitions.
    assert source._runtime_commit_tree(RUNTIME_SHA) == "c" * 40
    assert source.runtime_source_request_stats()["active_source_seconds"] == 4.0
    assert source.runtime_source_request_stats()["request_starts"] == 2
    assert source._RUNTIME_SOURCE_ACTIVE_DEPTH == 0
    assert source._RUNTIME_SOURCE_STARTED_AT is None


def test_source_budget_rejects_proof_that_finishes_after_active_deadline_before_admission(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "source-test-token")
    clock = [100.0]
    monkeypatch.setattr(source, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    value = _runtime_batch_response([SHA])
    monkeypatch.setattr(source.requests, "post", lambda *args, **kwargs: _source_response(value))
    original = source._verify_runtime_tree

    def verify(*args, **kwargs):
        original(*args, **kwargs)
        clock[0] += 601.0

    monkeypatch.setattr(source, "_verify_runtime_tree", verify)
    with pytest.raises(source.RuntimeSourceError) as failure:
        source.prewarm_runtime_snapshots([SHA])
    assert failure.value.reason_class == "timeout"
    assert source.runtime_source_request_stats()["active_source_seconds"] == 601.0
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {}
    monkeypatch.setattr(source.requests, "get", lambda *args, **kwargs: pytest.fail("expired active budget must deny transport"))
    with pytest.raises(source.RuntimeSourceError):
        source._runtime_commit_tree(SHA)
    assert source.runtime_source_request_stats()["request_starts"] == 1


def test_failed_source_transport_is_charged_and_reports_only_safe_json_tail(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "source-test-token")
    clock = [100.0]
    monkeypatch.setattr(source, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def get(*args, **kwargs):
        clock[0] += 9.0
        raise source.requests.ReadTimeout("private URL/token/node error")

    monkeypatch.setattr(source.requests, "get", get)
    with pytest.raises(source.RuntimeSourceError) as failure:
        source.runtime_snapshot(SHA)
    assert failure.value.reason_class == "timeout"
    assert failure.value.commit_sha == SHA
    assert source.runtime_source_request_stats()["active_source_seconds"] == 9.0
    diagnostic = json.loads(str(failure.value)[str(failure.value).index("{"):])
    assert diagnostic["reason_class"] == "timeout"
    assert diagnostic["commit_sha"] == SHA
    assert diagnostic["request_starts"] == 1
    assert "private" not in str(failure.value)
    assert "source-test-token" not in str(failure.value)
    assert "private" not in str(source.RuntimeSourceError("private injected text", phase="private", commit_sha="private", reason_class="private"))


def test_concurrent_source_acquisition_charges_union_time_once(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "source-test-token")
    clock = [0.0]
    monkeypatch.setattr(source, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]

    def get(url, **kwargs):
        index = 0 if url.endswith(SHA) else 1
        entered[index].set()
        assert release[index].wait(2)
        return _source_response({"sha": url.rsplit("/", 1)[-1], "tree": {"sha": "c" * 40}})

    monkeypatch.setattr(source.requests, "get", get)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(source._runtime_commit_tree, SHA)
        assert entered[0].wait(2)
        clock[0] = 10.0
        second = executor.submit(source._runtime_commit_tree, RUNTIME_SHA)
        assert entered[1].wait(2)
        clock[0] = 20.0
        release[0].set()
        assert first.result(timeout=2) == "c" * 40
        clock[0] = 30.0
        release[1].set()
        assert second.result(timeout=2) == "c" * 40
    assert source.runtime_source_request_stats()["active_source_seconds"] == 30.0
    assert source.runtime_source_request_stats()["request_starts"] == 2
    assert source._RUNTIME_SOURCE_ACTIVE_DEPTH == 0


def test_source_request_cap_blocks_fallback_without_any_guard_reset(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "source-test-token")
    monkeypatch.setattr(source, "_RUNTIME_SOURCE_STARTS", source.RUNTIME_SOURCE_MAX_REQUESTS - 1)
    monkeypatch.setattr(source.requests, "post", lambda *args, **kwargs:
                        _source_response({"errors": [{"message": "recoverable GraphQL error"}]}))
    monkeypatch.setattr(source.requests, "get", lambda *args, **kwargs:
                        pytest.fail("fallback must respect the existing total request cap"))
    with pytest.raises(source.RuntimeSourceError) as failure:
        source.prewarm_runtime_snapshots([SHA])
    assert failure.value.reason_class == "rate-limit"
    assert source.runtime_source_request_stats()["request_starts"] == source.RUNTIME_SOURCE_MAX_REQUESTS
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {}


def test_source_transport_uses_remaining_active_time_and_rejects_late_success(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "source-test-token")
    monkeypatch.setattr(source, "_RUNTIME_SOURCE_ACTIVE_SECONDS", 599.0)
    clock = [100.0]
    monkeypatch.setattr(source, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def get(url, **kwargs):
        assert kwargs["timeout"] == 1.0
        clock[0] += 2.0
        return _source_response({"sha": SHA, "tree": {"sha": "c" * 40}})

    monkeypatch.setattr(source.requests, "get", get)
    with pytest.raises(source.RuntimeSourceError) as failure:
        source._runtime_commit_tree(SHA)
    assert failure.value.reason_class == "timeout"
    assert source.runtime_source_request_stats()["active_source_seconds"] == 601.0
    assert source._runtime_commit_tree.cache_info().currsize == 0


def test_malformed_source_json_is_schema_failure_without_private_response_text(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "source-test-token")

    def decode():
        raise source.requests.exceptions.JSONDecodeError("private response", "private token", 1)

    monkeypatch.setattr(source.requests, "get", lambda *args, **kwargs:
                        SimpleNamespace(content=b"{}", raise_for_status=lambda: None, json=decode))
    with pytest.raises(source.RuntimeSourceError) as failure:
        source._runtime_commit_tree(SHA)
    assert failure.value.reason_class == "schema-drift"
    assert failure.value.commit_sha == SHA
    assert "private" not in str(failure.value)
    assert source.runtime_source_request_stats()["request_starts"] == 1
