"""Tests for post-deploy Operations manifest-to-blob verification."""

import json
import subprocess

import pytest

from vllm import verify_published_operations_bundle as verifier
from vllm.operations_bundle_contract import OPERATIONS_BUNDLE_VERSION


def _manifest():
    return {
        "schema_version": 2,
        "bundle_version": OPERATIONS_BUNDLE_VERSION,
        "generated_at": "2026-08-29T00:00:00Z",
        "monolith": None,
        "organization_summary": {"path": "org_summary.json", "bytes": 11},
        "sections": {
            "nightly": {"path": "operations_v2/nightly.json", "bytes": 17},
            "queue": {"path": "operations_v2/queue.json", "bytes": 23},
        },
    }


def _write_manifests(tmp_path, payload=None):
    encoded = json.dumps(payload or _manifest(), separators=(",", ":")) + "\n"
    assembled = tmp_path / "assembled.json"
    deployed = tmp_path / "deployed.json"
    assembled.write_text(encoded)
    deployed.write_text(encoded)
    (tmp_path / "operations_v2").mkdir()
    (tmp_path / "org_summary.json").write_bytes(b"o" * 11)
    (tmp_path / "operations_v2/nightly.json").write_bytes(b"n" * 17)
    (tmp_path / "operations_v2/queue.json").write_bytes(b"q" * 23)
    return assembled, deployed


def test_verifies_every_declared_asset_size(tmp_path):
    assembled, deployed = _write_manifests(tmp_path)
    expected = {
        "data/vllm/ci/org_summary.json": 11,
        "data/vllm/ci/operations_v2/nightly.json": 17,
        "data/vllm/ci/operations_v2/queue.json": 23,
    }
    observed = []

    def asset_info(path, _assembled_path):
        observed.append(path)
        return "100644", "blob", expected[path], "a" * 40, "a" * 40

    count = verifier.verify_published_bundle(
        assembled,
        deployed,
        git_ref="origin/gh-pages",
        asset_info=asset_info,
    )

    assert count == 3
    assert len(observed) == len(expected)
    assert set(observed) == set(expected)


def test_verifier_accepts_immutable_legacy_bundle_during_rollout(tmp_path):
    payload = _manifest()
    payload["bundle_version"] = 1
    assembled, deployed = _write_manifests(tmp_path, payload)
    expected = {
        "data/vllm/ci/org_summary.json": 11,
        "data/vllm/ci/operations_v2/nightly.json": 17,
        "data/vllm/ci/operations_v2/queue.json": 23,
    }

    assert verifier.verify_published_bundle(
        assembled,
        deployed,
        git_ref="origin/gh-pages",
        asset_info=lambda path, _local: (
            "100644",
            "blob",
            expected[path],
            "a" * 40,
            "a" * 40,
        ),
    ) == len(expected)


def test_deployed_manifest_must_exactly_match_assembled_site(tmp_path):
    assembled, deployed = _write_manifests(tmp_path)
    deployed.write_text(deployed.read_text() + "\n")

    with pytest.raises(verifier.BundleVerificationError, match="differs"):
        verifier.verify_published_bundle(
            assembled,
            deployed,
            git_ref="origin/gh-pages",
            asset_info=lambda *_: ("100644", "blob", 0, "a" * 40, "a" * 40),
        )


def test_duplicate_manifest_keys_fail_closed(tmp_path):
    encoded = '{"schema_version":2,"schema_version":2}\n'
    assembled = tmp_path / "assembled.json"
    deployed = tmp_path / "deployed.json"
    assembled.write_text(encoded)
    deployed.write_text(encoded)

    with pytest.raises(verifier.BundleVerificationError, match="invalid JSON"):
        verifier.verify_published_bundle(
            assembled,
            deployed,
            git_ref="origin/gh-pages",
            asset_info=lambda *_: ("100644", "blob", 0, "a" * 40, "a" * 40),
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(monolith="operations_v2.json"),
        lambda payload: payload.update(schema_version=3),
        lambda payload: payload.update(bundle_version=True),
        lambda payload: payload.update(sections={}),
        lambda payload: payload["sections"]["nightly"].update(bytes=True),
        lambda payload: payload["sections"]["nightly"].update(path="../nightly.json"),
        lambda payload: payload["sections"]["nightly"].update(path="operations_v2/queue.json"),
    ],
)
def test_unsafe_or_contradictory_manifest_fails_closed(tmp_path, mutate):
    payload = _manifest()
    mutate(payload)
    assembled, deployed = _write_manifests(tmp_path, payload)

    with pytest.raises(verifier.BundleVerificationError):
        verifier.verify_published_bundle(
            assembled,
            deployed,
            git_ref="origin/gh-pages",
            asset_info=lambda *_: ("100644", "blob", 0, "a" * 40, "a" * 40),
        )


def test_missing_or_wrong_sized_asset_fails_closed(tmp_path):
    assembled, deployed = _write_manifests(tmp_path)

    with pytest.raises(verifier.BundleVerificationError, match="expected 11"):
        verifier.verify_published_bundle(
            assembled,
            deployed,
            git_ref="origin/gh-pages",
            asset_info=lambda *_: ("100644", "blob", 10, "a" * 40, "a" * 40),
        )


def test_wrong_sized_local_asset_is_rejected_before_hashing(tmp_path):
    assembled, deployed = _write_manifests(tmp_path)
    (tmp_path / "org_summary.json").write_bytes(b"too short")
    invoked = False

    def asset_info(*_):
        nonlocal invoked
        invoked = True
        return "100644", "blob", 11, "a" * 40, "a" * 40

    with pytest.raises(verifier.BundleVerificationError, match="local size"):
        verifier.verify_published_bundle(
            assembled,
            deployed,
            git_ref="origin/gh-pages",
            asset_info=asset_info,
        )
    assert invoked is False


@pytest.mark.parametrize(
    "asset_info",
    [
        lambda *_: ("040000", "tree", 11, "a" * 40, "a" * 40),
        lambda *_: ("120000", "blob", 11, "a" * 40, "a" * 40),
        lambda *_: ("100644", "blob", 11, "a" * 40, "b" * 40),
    ],
)
def test_non_blob_or_same_size_wrong_content_fails_closed(tmp_path, asset_info):
    assembled, deployed = _write_manifests(tmp_path)

    with pytest.raises(verifier.BundleVerificationError):
        verifier.verify_published_bundle(
            assembled,
            deployed,
            git_ref="origin/gh-pages",
            asset_info=asset_info,
        )


@pytest.mark.parametrize("git_ref", ["", "../main", "main:asset", "-dangerous"])
def test_git_ref_must_be_bounded_and_safe(tmp_path, git_ref):
    assembled, deployed = _write_manifests(tmp_path)

    with pytest.raises(verifier.BundleVerificationError, match="git ref"):
        verifier.verify_published_bundle(
            assembled,
            deployed,
            git_ref=git_ref,
            asset_info=lambda *_: ("100644", "blob", 0, "a" * 40, "a" * 40),
        )


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_real_git_ref_requires_exact_regular_blobs(tmp_path):
    assembled_root = tmp_path / "site/data/vllm/ci"
    assembled_root.mkdir(parents=True)
    assembled, deployed = _write_manifests(assembled_root)
    public_root = tmp_path / "data/vllm/ci"
    (public_root / "operations_v2").mkdir(parents=True)
    (public_root / "org_summary.json").write_bytes(b"o" * 11)
    (public_root / "operations_v2/nightly.json").write_bytes(b"n" * 17)
    queue_path = public_root / "operations_v2/queue.json"
    queue_path.write_bytes(b"q" * 23)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _git(tmp_path, "add", "data")
    _git(tmp_path, "commit", "-qm", "bundle")

    assert (
        verifier.verify_published_bundle(
            assembled,
            deployed,
            git_ref="HEAD",
            repo_root=tmp_path,
        )
        == 3
    )

    queue_path.write_bytes(b"x" * 23)
    _git(tmp_path, "add", "data")
    _git(tmp_path, "commit", "-qm", "same size wrong content")
    with pytest.raises(verifier.BundleVerificationError, match="differs"):
        verifier.verify_published_bundle(
            assembled,
            deployed,
            git_ref="HEAD",
            repo_root=tmp_path,
        )

    queue_path.unlink()
    queue_path.symlink_to("q" * 23)
    _git(tmp_path, "add", "data")
    _git(tmp_path, "commit", "-qm", "same content symlink")
    with pytest.raises(verifier.BundleVerificationError, match="not a blob"):
        verifier.verify_published_bundle(
            assembled,
            deployed,
            git_ref="HEAD",
            repo_root=tmp_path,
        )


def test_relative_assembled_path_is_not_reinterpreted_below_repo_root(tmp_path, monkeypatch):
    caller_root = tmp_path / "caller"
    repo_root = tmp_path / "repo"
    caller_bundle = caller_root / "site/data/vllm/ci"
    repo_bundle = repo_root / "site/data/vllm/ci"
    caller_bundle.mkdir(parents=True)
    repo_bundle.mkdir(parents=True)
    caller_assembled, _ = _write_manifests(caller_bundle)
    repo_assembled, _ = _write_manifests(repo_bundle)
    (caller_bundle / "operations_v2/queue.json").write_bytes(b"x" * 23)

    public_root = repo_root / "data/vllm/ci"
    (public_root / "operations_v2").mkdir(parents=True)
    (public_root / "org_summary.json").write_bytes(b"o" * 11)
    (public_root / "operations_v2/nightly.json").write_bytes(b"n" * 17)
    (public_root / "operations_v2/queue.json").write_bytes(b"q" * 23)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.name", "test")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "commit.gpgsign", "false")
    _git(repo_root, "add", "data")
    _git(repo_root, "commit", "-qm", "bundle")

    # Before path pinning, size inspection used caller_root while hash-object
    # silently reinterpreted the same relative path below repo_root.
    relative_manifest = caller_assembled.relative_to(caller_root)
    relative_deployed = relative_manifest.with_name("deployed.json")
    assert repo_assembled.relative_to(repo_root) == relative_manifest
    monkeypatch.chdir(caller_root)
    with pytest.raises(verifier.BundleVerificationError, match="differs"):
        verifier.verify_published_bundle(
            relative_manifest,
            relative_deployed,
            git_ref="HEAD",
            repo_root=repo_root,
        )
