"""Fail-closed immutable publication proofs for bounded collection retries."""

from types import SimpleNamespace

import pytest

from vllm import collection_recovery as recovery


def test_unknown_failure_shape_cannot_trigger_recovery():
    payload = {
        "schema_version": 2, "mode": "fallback",
        "fallback_surfaces": ["github_home"], "fresh_degraded_surfaces": [],
        "degraded_surfaces": ["github_home"],
        "collector_failures": [{
            "schema_version": 1, "surface": "github_home", "collector": "collect.py",
            "step": "GitHub collection", "reason_class": ["rate-limit"], "exit_code": 1,
        }],
    }
    with pytest.raises(recovery.CollectionEvidenceError, match="collector failure"):
        recovery.retry_surfaces_from_state(payload)


def test_remote_evidence_is_size_proven_before_fetching_any_objects(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/dashboard")
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    calls = []
    state_sha, tree_sha, manifest_oid, state_oid = (character * 40 for character in "abcd")

    def prove(repository, sha, profile, *, token):
        calls.append("prove")
        assert (repository, sha, token) == ("owner/dashboard", state_sha, "fake-token")
        assert profile.require_parentless
        assert profile.required == {
            recovery.MANIFEST_PATH: recovery.MAX_MANIFEST_BYTES,
            recovery.STATE_PATH: recovery.MAX_STATE_BYTES,
        }
        return {"tree_sha": tree_sha, "required_blobs": {
            recovery.MANIFEST_PATH: {"oid": manifest_oid, "bytes": 200},
            recovery.STATE_PATH: {"oid": state_oid, "bytes": 100},
        }}

    fetched = set()

    def git(root, *args):
        calls.append(args)
        if args[0] == "fetch":
            fetched.add(args[-1])
            return b""
        if args[0] == "rev-parse":
            return tree_sha.encode()
        assert args[:2] == ("cat-file", "-s")
        if args[-1] not in fetched:
            raise recovery.CollectionEvidenceUnavailable("local object missing")
        return b"200" if args[-1] == manifest_oid else b"100"

    monkeypatch.setattr(recovery.github_git_proof, "prove_commit_tree", prove)
    monkeypatch.setattr(recovery, "_git", git)
    recovery._hydrate_remote_evidence(tmp_path, durable_ref=state_sha, remote="origin")
    assert calls[0] == "prove"
    assert fetched == {state_sha, manifest_oid, state_oid}


def test_invalid_remote_size_proof_never_fetches_objects(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/dashboard")

    def reject(*args, **kwargs):
        raise recovery.github_git_proof.InvalidProof("oversized required metadata")

    monkeypatch.setattr(recovery.github_git_proof, "prove_commit_tree", reject)
    monkeypatch.setattr(recovery, "_git", lambda *a, **kw: pytest.fail("unproven fetch"))
    with pytest.raises(recovery.CollectionEvidenceError, match="proof was rejected"):
        recovery._hydrate_remote_evidence(tmp_path, durable_ref="a" * 40, remote="origin")


def test_ambiguous_remote_proof_has_two_attempt_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/dashboard")
    attempts, sleeps = [], []

    def unavailable(*args, **kwargs):
        attempts.append(1)
        raise recovery.github_git_proof.AmbiguousProof("HTTP 503")

    monkeypatch.setattr(recovery.github_git_proof, "prove_commit_tree", unavailable)
    monkeypatch.setattr(recovery.time, "sleep", sleeps.append)
    monkeypatch.setattr(recovery, "_git", lambda *a, **kw: pytest.fail("unproven fetch"))
    with pytest.raises(recovery.CollectionEvidenceError, match="transport proof"):
        recovery._hydrate_remote_evidence(tmp_path, durable_ref="a" * 40, remote="origin")
    assert len(attempts) == 2
    assert sleeps == [1]


def test_local_git_reads_cannot_lazily_fetch_unknown_size_blobs(tmp_path, monkeypatch):
    def run(args, **kwargs):
        assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
        assert kwargs["timeout"] == 60
        return SimpleNamespace(stdout=b"123")

    monkeypatch.setattr(recovery.subprocess, "run", run)
    assert recovery._git(tmp_path, "cat-file", "-s", "a" * 40) == b"123"


def test_persisted_proof_must_name_its_own_durable_state():
    with pytest.raises(recovery.CollectionEvidenceError, match="evidence values"):
        recovery.normalize_collection_evidence({
            "schema_version": 1, "durable_ref": "a" * 40,
            "publication_state_oid": "c" * 40, "retry_surfaces": ["github_home"],
        }, durable_ref="b" * 40)


@pytest.mark.parametrize("clock", ["2026-09-15T12:00:00+00:00", "2026-09-15 12:00:00Z", "2026-09-15T12:00:00.001Z"])
def test_evidence_clock_requires_canonical_whole_second_utc(clock):
    with pytest.raises(recovery.CollectionEvidenceError, match="canonical"):
        recovery._timestamp(clock)


def test_boolean_schema_version_cannot_prove_collection_outcome():
    with pytest.raises(recovery.CollectionEvidenceError, match="evidence values"):
        recovery.normalize_collection_evidence({
            "schema_version": True, "durable_ref": "a" * 40,
            "publication_state_oid": "c" * 40, "retry_surfaces": ["github_home"],
        }, durable_ref="a" * 40)


@pytest.mark.parametrize("version", (True, 6.0, 7.0, 8.0, "7", "8", 999))
def test_recovery_rejects_malformed_or_unknown_surface_contract_versions(version):
    payload = {"schema_version": 2, "surface_contract_version": version, "mode": "current",
               "fallback_surfaces": [], "fresh_degraded_surfaces": [],
               "degraded_surfaces": [], "collector_failures": []}
    with pytest.raises(recovery.CollectionEvidenceError, match="surface contract"):
        recovery.retry_surfaces_from_state(payload)


@pytest.mark.parametrize("surface", ("ci_gating", "ci_changes", "ci_hotness"))
@pytest.mark.parametrize("contract_version", (6, 7, 8))
def test_retired_collector_is_never_retried_and_current_contract_rejects_it(surface, contract_version):
    payload = {"schema_version": 2, "surface_contract_version": 5, "mode": "fallback",
               "fallback_surfaces": [surface], "fresh_degraded_surfaces": [],
               "degraded_surfaces": [surface], "collector_failures": [{
                   "schema_version": 1, "surface": surface, "collector": "retired.py",
                   "step": "Retired", "reason_class": "timeout", "exit_code": 1,
               }]}
    assert recovery.retry_surfaces_from_state(payload) == []
    payload["surface_contract_version"] = contract_version
    with pytest.raises(recovery.CollectionEvidenceError, match="surface lanes"):
        recovery.retry_surfaces_from_state(payload)


@pytest.mark.parametrize("surface", ("ci_gating", "ci_changes", "ci_hotness"))
def test_historical_proof_preserves_retired_names_but_new_proof_rejects_them(surface):
    proof = {"schema_version": 1, "durable_ref": "a" * 40,
             "publication_state_oid": "b" * 40,
             "retry_surfaces": sorted([surface, "ci_core"])}
    assert recovery.normalize_historical_collection_evidence(proof, durable_ref="a" * 40) == proof
    with pytest.raises(recovery.CollectionEvidenceError, match="evidence values"):
        recovery.normalize_collection_evidence(proof, durable_ref="a" * 40)


def test_historical_proof_never_accepts_unreviewed_retry_surface_names():
    proof = {"schema_version": 1, "durable_ref": "a" * 40,
             "publication_state_oid": "b" * 40, "retry_surfaces": ["unknown_collector"]}
    with pytest.raises(recovery.CollectionEvidenceError, match="evidence values"):
        recovery.normalize_historical_collection_evidence(proof, durable_ref="a" * 40)


@pytest.mark.parametrize("version", [7, 8])
def test_current_and_exact_previous_contracts_preserve_valid_ci_retry_evidence(version):
    state = {"schema_version": 2, "surface_contract_version": version, "mode": "fallback",
             "fallback_surfaces": ["ci_core"], "fresh_degraded_surfaces": [],
             "degraded_surfaces": ["ci_core"], "collector_failures": [{
                 "schema_version": 1, "surface": "ci_core", "collector": "collect_ci.py",
                 "step": "Current MI collection", "reason_class": "network", "exit_code": 1}]}
    assert recovery.retry_surfaces_from_state(state) == ["ci_core"]
