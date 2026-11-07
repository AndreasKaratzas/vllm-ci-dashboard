"""Resource failures keep their phase through nested immutable-source reads."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest
import requests

from vllm import main_ci_definitions as source

PIN = "a" * 40


@pytest.fixture(autouse=True)
def isolated_source(monkeypatch):
    cached = [source.runtime_snapshot, source._runtime_commit_tree, source._runtime_tree,
              source._runtime_blob, source._runtime_definition_files]
    for function in cached:
        function.cache_clear()
    for name, value in (("_RUNTIME_SOURCE_STARTS", 0), ("_RUNTIME_SOURCE_STARTED_AT", None),
                        ("_RUNTIME_SOURCE_ACTIVE_SECONDS", 0.0), ("_RUNTIME_SOURCE_ACTIVE_DEPTH", 0),
                        ("_RUNTIME_SOURCE_GRAPHQL_FALLBACKS", 0), ("_RUNTIME_PRIMED_BUILDKITE_TREES", {})):
        monkeypatch.setattr(source, name, value)
    monkeypatch.setenv("GITHUB_TOKEN", "test-only-source-token")
    yield
    for function in cached:
        function.cache_clear()


def _blob(payload):
    oid = hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()
    return oid, {"sha": oid, "encoding": "base64", "size": len(payload),
                 "content": base64.b64encode(payload).decode()}


def _tree(rows):
    ordered = sorted(rows, key=lambda row: row["path"] + ("/" if row["type"] == "tree" else ""))
    payload = b"".join(row["mode"].lstrip("0").encode() + b" " + row["path"].encode()
                       + b"\0" + bytes.fromhex(row["sha"]) for row in ordered)
    oid = hashlib.sha1(f"tree {len(payload)}\0".encode() + payload).hexdigest()
    return oid, {"sha": oid, "truncated": False, "tree": rows}


def _source_objects():
    config, config_body = _blob(b"job_dirs: [.buildkite/test_areas]\n")
    step, step_body = _blob(b"steps:\n- key: gpu\n  label: ':amd: (MI300) GPU Test'\n  device: mi300_1\n")
    areas, areas_body = _tree([{"path": "sample.yaml", "sha": step, "type": "blob", "mode": "100644"}])
    definitions, definitions_body = _tree([
        {"path": "ci_config.yaml", "sha": config, "type": "blob", "mode": "100644"},
        {"path": "test_areas", "sha": areas, "type": "tree", "mode": "040000"},
    ])
    root, root_body = _tree([{"path": ".buildkite", "sha": definitions, "type": "tree", "mode": "040000"}])
    objects = {f"commits/{PIN}": {"sha": PIN, "tree": {"sha": root}},
               f"trees/{root}": root_body, f"trees/{definitions}": definitions_body,
               f"trees/{areas}": areas_body, f"blobs/{config}": config_body,
               f"blobs/{step}": step_body}
    return objects, {"commit": f"commits/{PIN}", "tree": f"trees/{root}",
                     "definitions-tree": f"trees/{definitions}", "blob": f"blobs/{config}"}


@pytest.mark.parametrize("resource,phase", [
    ("commit", "commit"), ("tree", "tree"), ("definitions-tree", "tree"), ("blob", "blob"),
])
@pytest.mark.parametrize("http_status,reason", [(404, "dependency-unavailable"), (429, "rate-limit"), (503, "transient-http")])
def test_real_nested_http_failure_preserves_leaf_resource_and_exact_commit(monkeypatch, resource, phase, http_status, reason):
    objects, paths = _source_objects()
    calls = []

    def get(url, **kwargs):
        path = url.removeprefix(f"{source.API_BASE}/git/")
        calls.append(path)
        response = requests.Response()
        response.status_code = http_status if path == paths[resource] else 200
        response._content = (b'private raw response and secret' if response.status_code != 200
                             else json.dumps(objects[path]).encode())
        return response

    monkeypatch.setattr(source.requests, "get", get)
    with pytest.raises(source.RuntimeSourceError) as raised:
        source.runtime_snapshot(PIN)

    failure = raised.value
    assert failure.commit_sha == PIN
    assert failure.phase == phase
    assert failure.reason_class == reason
    assert failure.http_status == http_status
    assert source.runtime_source_request_stats()["request_starts"] == len(calls)
    assert source._RUNTIME_SOURCE_ACTIVE_DEPTH == 0
    assert source._RUNTIME_SOURCE_STARTED_AT is None
    assert "private" not in str(failure)
    assert "test-only-source-token" not in str(failure)
