"""Current source coverage must not inherit historical side-pipeline counts."""

from __future__ import annotations

import io
import base64
import hashlib
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
    with pytest.raises(ValueError, match="do not match"):
        source.annotate_runtime_source_scope({"commit": RUNTIME_SHA, "jobs": jobs}, snapshot=current)


def test_runtime_annotation_never_trusts_a_retained_cpu_exclusion():
    current = snapshot()
    result = source.annotate_runtime_source_scope({"commit": SHA, "jobs": [{
        "step_key": "native", "agent_queue": "amd_mi355", "name": ":amd: (MI355) Native AMD",
        "source_no_gpu": True, "source_scope_commit": RUNTIME_SHA,
    }]}, snapshot=current)
    assert "source_no_gpu" not in result["jobs"][0]


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
    with pytest.raises(ValueError, match="exact immutable"):
        source.annotate_runtime_source_scope({"commit": RUNTIME_SHA, "jobs": []}, scope_index=index)


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
    with pytest.raises(ValueError, match="requires GITHUB_TOKEN"):
        source._runtime_source_json(f"commits/{SHA}")
    assert source.runtime_source_request_stats() == before


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


@pytest.mark.parametrize("tamper", ("errors", "missing", "extra", "commit", "kind", "tree", "mode", "duplicate", "repository"))
def test_runtime_source_batch_rejects_partial_or_contradictory_identity_before_admitting_any_pin(monkeypatch, tamper):
    monkeypatch.setenv("GITHUB_TOKEN", "test-runtime-source-token")
    monkeypatch.setattr(source, "_RUNTIME_PRIMED_BUILDKITE_TREES", {})
    value = _runtime_batch_response([SHA, RUNTIME_SHA])
    repo = value["data"]["repository"]
    if tamper == "errors":
        value["errors"] = [{"message": "partial result"}]
    elif tamper == "missing":
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
