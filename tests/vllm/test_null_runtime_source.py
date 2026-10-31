"""Explicit null source aliases require exact REST proof before DNS can proceed."""

from __future__ import annotations

import copy
import hashlib
import json
import re

import pytest
import requests

from vllm import collect_dns_failures as collector
from vllm import main_ci_definitions as source

NULL_PIN = "11b9bad58f69441fd403751b62c026d7a8a33cf0"  # Upstream Git commit SHA from the production failure.
KNOWN = "b" * 40
OTHER = "d" * 40
DEFINITIONS = "c" * 40


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
    monkeypatch.setattr(collector, "_SOURCE_SCOPE_INDEXES", {})
    monkeypatch.setattr(collector, "_SOURCE_UNAVAILABLE_PINS", set())
    monkeypatch.setattr(collector, "_SOURCE_SCOPE_CACHE_DIR", None)
    monkeypatch.setenv("GITHUB_TOKEN", "local-test-source-token")
    monkeypatch.setattr("requests.sessions.Session.request",
                        lambda *args, **kwargs: pytest.fail("source regressions cannot use a provider"))
    yield
    for function in cached:
        function.cache_clear()


def response(value=None, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(value if status == 200 else {"message": "private response secret"}).encode()
    return result


def root_tree():
    rows = [{"name": ".buildkite", "mode": 0o040000, "type": "tree", "oid": DEFINITIONS}]
    data = b"40000 .buildkite\0" + bytes.fromhex(DEFINITIONS)
    oid = hashlib.sha1(f"tree {len(data)}\0".encode() + data).hexdigest()
    return {"oid": oid, "entries": rows}


def payload(pins, *, nulls=(NULL_PIN,)):
    return {"data": {"repository": {"nameWithOwner": source.REPOSITORY, **{
        f"c{index}": None if pin in nulls else {
            "__typename": "Commit", "oid": pin, "tree": root_tree(),
        } for index, pin in enumerate(pins)
    }}}}


def install_provider(monkeypatch, *, nulls=(NULL_PIN,), statuses=None):
    calls = []
    statuses = statuses or {}

    def post(url, **kwargs):
        assert url == "https://api.github.com/graphql"
        pins = re.findall(r'object\(oid: "([0-9a-f]{40})"\)', kwargs["json"]["query"])
        calls.append(("graphql", tuple(pins)))
        return response(payload(pins, nulls=nulls))

    def get(url, **kwargs):
        path = url.removeprefix(f"{source.API_BASE}/git/")
        calls.append(("rest", path))
        status = statuses.get(path, 200)
        if path.startswith("commits/"):
            value = {"sha": path.split("/")[1], "tree": {"sha": root_tree()["oid"]}}
        else:
            assert path == f"trees/{root_tree()['oid']}"
            value = {"sha": root_tree()["oid"], "truncated": False,
                     "tree": [{"path": ".buildkite", "mode": "040000", "type": "tree", "sha": DEFINITIONS}]}
        return response(value, status)

    monkeypatch.setattr(source.requests, "post", post)
    monkeypatch.setattr(source.requests, "get", get)
    return calls


def test_explicit_null_is_verified_by_exact_rest_only_and_diagnostic_is_bounded(monkeypatch, capsys):
    calls = install_provider(monkeypatch)
    assert source.prewarm_runtime_snapshots([KNOWN, NULL_PIN]) == {KNOWN: DEFINITIONS, NULL_PIN: DEFINITIONS}
    assert calls == [("graphql", (KNOWN, NULL_PIN)), ("rest", f"commits/{NULL_PIN}"),
                     ("rest", f"trees/{root_tree()['oid']}")]
    text = capsys.readouterr().out
    diagnostic = json.loads(text[text.index("{"):])
    assert diagnostic["graphql_error_types"] == ["NULL_OBJECT"]
    assert diagnostic["failed_commit_shas"] == [NULL_PIN]
    assert diagnostic["verified_pins"] == 2
    assert "private" not in text and "local-test-source-token" not in text
    source.prewarm_runtime_snapshots([KNOWN, NULL_PIN])
    assert len(calls) == 3


def test_confirmed_null_commit404_checkpoints_only_verified_sibling_and_retry_uses_no_source_requests(monkeypatch):
    calls = install_provider(monkeypatch, statuses={f"commits/{NULL_PIN}": 404})
    with pytest.raises(source.RuntimeSourceError) as raised:
        source.prewarm_runtime_snapshots([KNOWN, NULL_PIN])
    assert raised.value.phase == "commit" and raised.value.commit_sha == NULL_PIN
    assert raised.value.http_status == 404
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {KNOWN: DEFINITIONS}
    assert "_verified_batch_roots" not in raised.value.diagnostics
    assert "private response secret" not in str(raised.value)
    assert source.prewarm_runtime_snapshots([KNOWN]) == {KNOWN: DEFINITIONS}
    assert calls == [("graphql", (KNOWN, NULL_PIN)), ("rest", f"commits/{NULL_PIN}")]


def test_multiple_nulls_keep_successfully_verified_rest_siblings_before_a_confirmed_missing_pin(monkeypatch):
    calls = install_provider(monkeypatch, nulls=(OTHER, NULL_PIN),
                             statuses={f"commits/{NULL_PIN}": 404})
    with pytest.raises(source.RuntimeSourceError):
        source.prewarm_runtime_snapshots([KNOWN, OTHER, NULL_PIN])
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {KNOWN: DEFINITIONS, OTHER: DEFINITIONS}
    source.prewarm_runtime_snapshots([KNOWN, OTHER])
    assert len(calls) == 4


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502, 503])
def test_null_rest_auth_or_transient_failure_does_not_checkpoint_any_sibling(monkeypatch, status):
    install_provider(monkeypatch, statuses={f"commits/{NULL_PIN}": status})
    with pytest.raises(source.RuntimeSourceError) as raised:
        source.prewarm_runtime_snapshots([KNOWN, NULL_PIN])
    assert raised.value.http_status == status
    assert raised.value.phase == "commit"
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {}


@pytest.mark.parametrize("status", [404, 429, 503])
def test_null_rest_tree_failure_is_never_a_missing_commit_checkpoint(monkeypatch, status):
    install_provider(monkeypatch, statuses={f"trees/{root_tree()['oid']}": status})
    with pytest.raises(source.RuntimeSourceError) as raised:
        source.prewarm_runtime_snapshots([KNOWN, NULL_PIN])
    assert raised.value.http_status == status
    assert raised.value.phase == "tree"
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {}


@pytest.mark.parametrize("bad", ["missing", False, [], "null", {}, "wrong-oid", "wrong-type", "wrong-tree"])
def test_malformed_non_null_sibling_stays_hard_before_any_null_rest_lookup(monkeypatch, bad):
    value = payload([KNOWN, NULL_PIN])
    repository = value["data"]["repository"]
    if bad == "missing":
        del repository["c0"]
    elif bad == "wrong-oid":
        repository["c0"]["oid"] = OTHER
    elif bad == "wrong-type":
        repository["c0"]["__typename"] = "Tree"
    elif bad == "wrong-tree":
        repository["c0"]["tree"]["entries"][0]["oid"] = OTHER
    else:
        repository["c0"] = bad
    monkeypatch.setattr(source.requests, "post", lambda *args, **kwargs: response(value))
    monkeypatch.setattr(source.requests, "get", lambda *args, **kwargs: pytest.fail("malformed GraphQL cannot use null recovery"))
    with pytest.raises(source.RuntimeSourceError) as raised:
        source.prewarm_runtime_snapshots([KNOWN, NULL_PIN])
    assert raised.value.reason_class == "schema-drift"
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {}
    assert source.runtime_source_request_stats()["graphql_rest_fallbacks"] == 0


def test_wrong_commit404_exception_cannot_transfer_verified_sibling_roots(monkeypatch):
    install_provider(monkeypatch)

    def wrong_pin(pin):
        raise source.RuntimeSourceError(reason_class="dependency-unavailable", phase="commit",
                                        http_status=404, commit_sha=KNOWN)

    monkeypatch.setattr(source, "_runtime_rest_root", wrong_pin)
    with pytest.raises(source.RuntimeSourceError) as raised:
        source.prewarm_runtime_snapshots([KNOWN, NULL_PIN])
    assert raised.value.commit_sha == KNOWN
    assert not hasattr(raised.value, "_verified_batch_roots")
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {}


@pytest.mark.parametrize("resource", ["commit", "tree"])
def test_null_rest_identity_or_tree_hash_contradiction_never_checkpoints_siblings(monkeypatch, resource):
    install_provider(monkeypatch)
    good_get = source.requests.get

    def bad_get(url, **kwargs):
        result = good_get(url, **kwargs)
        value = result.json()
        if resource == "commit" and url.endswith(f"commits/{NULL_PIN}"):
            value["sha"] = KNOWN
        elif resource == "tree" and "/trees/" in url:
            value["tree"][0]["sha"] = OTHER
        return response(value)

    monkeypatch.setattr(source.requests, "get", bad_get)
    with pytest.raises(source.RuntimeSourceError) as raised:
        source.prewarm_runtime_snapshots([KNOWN, NULL_PIN])
    assert raised.value.reason_class == "schema-drift"
    assert raised.value.phase == resource
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {}


@pytest.mark.parametrize("verified", [
    [], {OTHER: DEFINITIONS}, {NULL_PIN: DEFINITIONS}, {KNOWN: "bad"},
])
def test_partial_exception_payload_must_be_a_bounded_exact_batch_subset(monkeypatch, verified):
    failure = source.RuntimeSourceError(reason_class="dependency-unavailable", phase="commit",
                                       http_status=404, commit_sha=NULL_PIN)
    failure._verified_batch_roots = copy.deepcopy(verified)
    monkeypatch.setattr(source, "_runtime_batch_roots", lambda batch: (_ for _ in ()).throw(failure))
    with pytest.raises(source.RuntimeSourceError) as raised:
        source.prewarm_runtime_snapshots([KNOWN, NULL_PIN])
    assert raised.value.reason_class == "schema-drift"
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {}


def test_null_verification_stays_inside_existing_request_budget(monkeypatch):
    calls = install_provider(monkeypatch)
    monkeypatch.setattr(source, "RUNTIME_SOURCE_MAX_REQUESTS", 1)
    with pytest.raises(source.RuntimeSourceError) as raised:
        source.prewarm_runtime_snapshots([KNOWN, NULL_PIN])
    assert raised.value.reason_class == "rate-limit"
    assert calls == [("graphql", (KNOWN, NULL_PIN))]
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {}


@pytest.mark.parametrize("status", [201, 401, 403, 429, 503])
def test_null_response_with_other_http_status_cannot_use_rest_recovery(monkeypatch, status):
    result = response(payload([KNOWN, NULL_PIN]))
    result.status_code = status
    monkeypatch.setattr(source.requests, "post", lambda *args, **kwargs: result)
    monkeypatch.setattr(source.requests, "get", lambda *args, **kwargs: pytest.fail("null recovery requires HTTP200"))
    with pytest.raises(source.RuntimeSourceError):
        source.prewarm_runtime_snapshots([KNOWN, NULL_PIN])
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {}
    assert source.runtime_source_request_stats()["graphql_rest_fallbacks"] == 0


def test_oversized_exception_checkpoint_is_rejected_without_any_cache_mutation(monkeypatch):
    pins = [f"{index:040x}" for index in range(1, 52)]
    failure = source.RuntimeSourceError(reason_class="dependency-unavailable", phase="commit",
                                       http_status=404, commit_sha=NULL_PIN)
    failure._verified_batch_roots = dict.fromkeys(pins, DEFINITIONS)
    monkeypatch.setattr(source, "_runtime_batch_roots", lambda batch: (_ for _ in ()).throw(failure))
    with pytest.raises(source.RuntimeSourceError) as raised:
        source.prewarm_runtime_snapshots([NULL_PIN, *pins])
    assert raised.value.reason_class == "schema-drift"
    assert source._RUNTIME_PRIMED_BUILDKITE_TREES == {}


@pytest.mark.parametrize("unknown_pins", [(NULL_PIN,), (NULL_PIN, OTHER)])
def test_dns_quarantines_confirmed_missing_null_and_uses_positive_sibling_without_refetch(monkeypatch, unknown_pins):
    calls = install_provider(monkeypatch, nulls=unknown_pins,
                             statuses={f"commits/{pin}": 404 for pin in unknown_pins})
    monkeypatch.setattr(source, "_runtime_definition_files", lambda tree: {
        ".buildkite/ci_config.yaml": {"job_dirs": [".buildkite/test_areas"]},
        ".buildkite/test_areas/sample.yaml": {"steps": [
            {"key": "gpu", "label": ":amd: (MI300) GPU Test", "device": "mi300_1"},
        ]},
    })
    builds = [{"commit": pin, "number": index, "jobs": [{
        "id": f"00000000-0000-4000-8000-{index:012x}", "type": "script", "state": "passed",
        "name": ":amd: (MI300) GPU Test", "agent_query_rules": ["queue=amd_mi300_1"],
        "finished_at": "2026-10-12T06:00:00Z",
    }]} for index, pin in enumerate((KNOWN, *unknown_pins), 1)]
    rows = collector.discover_job_metadata({"ci": builds})
    assert len(rows) == 1 + len(unknown_pins)
    assert collector._SOURCE_UNAVAILABLE_PINS == set(unknown_pins)
    assert set(collector._SOURCE_SCOPE_INDEXES) == {KNOWN}
    assert next(row for row in rows if row["build_number"] == 1)["execution_proof"]["source_commit"] == KNOWN
    for index, pin in enumerate(unknown_pins, 2):
        unknown = next(row for row in rows if row["build_number"] == index)
        assert unknown["source_unavailable_commit"] == pin and "execution_proof" not in unknown
        assert sum(call == ("rest", f"commits/{pin}") for call in calls) == 1
    queries = [pins for kind, pins in calls if kind == "graphql"]
    assert queries[0] == tuple(sorted((KNOWN, *unknown_pins)))
    assert all(KNOWN not in pins for pins in queries[1:])
    assert len(queries) == len(unknown_pins)
