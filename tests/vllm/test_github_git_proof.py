from __future__ import annotations

import json
from pathlib import Path

import pytest

from vllm import github_git_proof as proof


COMMIT_SHA = "1" * 40
TREE_SHA = "2" * 40
MANIFEST_OID = "3" * 40
ATTESTATION_OID = "4" * 40


def state_commit(*, parents: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {
        "sha": COMMIT_SHA,
        "tree": {"sha": TREE_SHA},
        "parents": [] if parents is None else parents,
    }


def state_tree() -> dict[str, object]:
    return {
        "sha": TREE_SHA,
        "truncated": False,
        "tree": [
            {
                "path": "data",
                "mode": "040000",
                "type": "tree",
                "sha": "5" * 40,
            },
            {
                "path": proof.STATE_MANIFEST_PATH,
                "mode": "100644",
                "type": "blob",
                "sha": MANIFEST_OID,
                "size": 8000,
            },
            {
                "path": proof.STATE_ATTESTATION_PATH,
                "mode": "100644",
                "type": "blob",
                "sha": ATTESTATION_OID,
                "size": 900,
            },
            {
                "path": "scripts/app.py",
                "mode": "100755",
                "type": "blob",
                "sha": "6" * 40,
                "size": 1200,
            },
        ],
    }


def test_state_commit_and_tree_proof_binds_sizes_and_oids() -> None:
    profile = proof.PROFILES["dashboard-state"]
    assert (
        proof.validate_commit_payload(
            state_commit(), expected_commit_sha=COMMIT_SHA, profile=profile
        )
        == TREE_SHA
    )
    summary = proof.validate_tree_payload(
        state_tree(), expected_tree_sha=TREE_SHA, profile=profile
    )
    assert summary["file_count"] == 3
    assert summary["total_bytes"] == 10_100
    assert summary["required_blobs"][proof.STATE_MANIFEST_PATH] == {
        "bytes": 8000,
        "mode": "100644",
        "oid": MANIFEST_OID,
    }


def test_state_proof_rejects_parented_or_mismatched_commit() -> None:
    profile = proof.PROFILES["dashboard-state"]
    with pytest.raises(proof.InvalidProof, match="parentless"):
        proof.validate_commit_payload(
            state_commit(parents=[{"sha": "7" * 40}]),
            expected_commit_sha=COMMIT_SHA,
            profile=profile,
        )
    with pytest.raises(proof.AmbiguousProof, match="requested SHA"):
        proof.validate_commit_payload(
            state_commit(), expected_commit_sha="8" * 40, profile=profile
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda tree: tree["tree"][1].update(size=8 * 1024 * 1024 + 1),
            "required metadata",
        ),
        (lambda tree: tree["tree"][2].update(mode="100755"), "required metadata"),
        (lambda tree: tree["tree"][3].update(mode="120000"), "unsafe object"),
        (lambda tree: tree["tree"][3].update(path="../escape"), "canonical"),
    ],
)
def test_state_tree_proof_rejects_unsafe_or_unbounded_metadata(
    mutation, message: str
) -> None:
    payload = state_tree()
    mutation(payload)
    with pytest.raises(proof.InvalidProof, match=message):
        proof.validate_tree_payload(
            payload,
            expected_tree_sha=TREE_SHA,
            profile=proof.PROFILES["dashboard-state"],
        )


def test_truncated_tree_is_ambiguous_and_never_ref_mutation_authority() -> None:
    payload = state_tree()
    payload["truncated"] = True
    with pytest.raises(proof.AmbiguousProof, match="truncated"):
        proof.validate_tree_payload(
            payload,
            expected_tree_sha=TREE_SHA,
            profile=proof.PROFILES["dashboard-state"],
        )


def test_tree_proof_rejects_aggregate_before_blob_hydration() -> None:
    payload = state_tree()
    payload["tree"][3]["size"] = 85 * 1024 * 1024
    payload["tree"].append(
        {
            "path": "data/second.bin",
            "mode": "100644",
            "type": "blob",
            "sha": "9" * 40,
            "size": 85 * 1024 * 1024,
        }
    )
    payload["tree"].append(
        {
            "path": "data/third.bin",
            "mode": "100644",
            "type": "blob",
            "sha": "a" * 40,
            "size": 85 * 1024 * 1024,
        }
    )
    payload["tree"].append(
        {
            "path": "data/fourth.bin",
            "mode": "100644",
            "type": "blob",
            "sha": "b" * 40,
            "size": 2 * 1024 * 1024,
        }
    )
    with pytest.raises(proof.InvalidProof, match="aggregate-byte"):
        proof.validate_tree_payload(
            payload,
            expected_tree_sha=TREE_SHA,
            profile=proof.PROFILES["dashboard-state"],
        )


def test_pages_profile_bounds_preview_bytes_and_required_metadata() -> None:
    payload = {
        "sha": TREE_SHA,
        "truncated": False,
        "tree": [
            {
                "path": proof.PAGES_MANIFEST_PATH,
                "mode": "100644",
                "type": "blob",
                "sha": MANIFEST_OID,
                "size": 1000,
            },
            {
                "path": proof.PAGES_MARKER_PATH,
                "mode": "100644",
                "type": "blob",
                "sha": ATTESTATION_OID,
                "size": 900,
            },
            {
                "path": "index.html",
                "mode": "100644",
                "type": "blob",
                "sha": "5" * 40,
                "size": 2000,
            },
            {
                "path": proof.PAGES_STATUS_PATH,
                "mode": "100644",
                "type": "blob",
                "sha": "7" * 40,
                "size": 1100,
            },
            {
                "path": "pr-preview/pr-1/large.bin",
                "mode": "100644",
                "type": "blob",
                "sha": "6" * 40,
                "size": 50 * 1024 * 1024,
            },
        ],
    }
    summary = proof.validate_tree_payload(
        payload,
        expected_tree_sha=TREE_SHA,
        profile=proof.PROFILES["pages"],
    )
    assert summary["file_count"] == 5
    assert summary["total_bytes"] == 50 * 1024 * 1024 + 5000
    assert summary["preview_bytes"] == 50 * 1024 * 1024
    assert summary["preview_files"] == 1
    assert len(summary["preview_digest"]) == 64

    payload["tree"][4]["size"] = 90_000_001
    with pytest.raises(proof.InvalidProof, match="blob exceeds"):
        proof.validate_tree_payload(
            payload,
            expected_tree_sha=TREE_SHA,
            profile=proof.PROFILES["pages"],
        )


def test_pages_orphan_profile_rejects_parented_publication() -> None:
    with pytest.raises(proof.InvalidProof, match="parentless"):
        proof.validate_commit_payload(
            state_commit(parents=[{"sha": "7" * 40}]),
            expected_commit_sha=COMMIT_SHA,
            profile=proof.PROFILES["pages-orphan"],
        )
    assert (
        proof.validate_commit_payload(
            state_commit(parents=[{"sha": "7" * 40}]),
            expected_commit_sha=COMMIT_SHA,
            profile=proof.PROFILES["pages"],
        )
        == TREE_SHA
    )


def test_materialize_pages_prefix_fetches_only_proven_preview_blobs(
    monkeypatch, tmp_path
) -> None:
    preview_oid = "8" * 40
    root_oid = "9" * 40
    remote_proof = {
        "profile": "pages",
        "file_count": 2,
        "total_bytes": 9,
        "blobs": {
            "index.html": {"bytes": 4, "mode": "100644", "oid": root_oid},
            "pr-preview/pr-17/index.html": {
                "bytes": 5,
                "mode": "100644",
                "oid": preview_oid,
            },
        },
    }
    hydrated: list[str] = []

    def fake_hydrate(root, remote, *, path, descriptor):
        hydrated.append(path)
        return b"hello"

    monkeypatch.setattr(proof, "_hydrate_proven_blob", fake_hydrate)
    destination = tmp_path / "site"
    proof.materialize_proven_tree(
        tmp_path,
        "origin",
        remote_proof,
        destination,
        prefix="pr-preview/",
    )
    assert hydrated == ["pr-preview/pr-17/index.html"]
    assert (destination / "pr-preview/pr-17/index.html").read_bytes() == b"hello"
    assert not (destination / "index.html").exists()


def test_pages_bounds_accept_one_current_size_preview_with_headroom(
    monkeypatch, tmp_path
) -> None:
    # Production's full preview is currently about 143.7 MiB.  The policy
    # deliberately admits one such preview alongside a similarly-sized root,
    # while the final 384 MiB bound still prevents cumulative growth.
    mib = 1024 * 1024
    files = {
        "index.html": 144 * mib,
        "pr-preview/pr-101/index.html": 144 * mib,
    }
    (tmp_path / "pr-preview/pr-101").mkdir(parents=True)
    (tmp_path / "index.html").touch()
    (tmp_path / "pr-preview/pr-101/index.html").touch()
    monkeypatch.setattr(proof, "_local_page_files", lambda root: dict(files))
    monkeypatch.setattr(proof, "_local_preview_digest", lambda root, rows: "a" * 64)
    summary = proof.bound_pages_directory(tmp_path, protected_preview="pr-101")
    assert summary["preview_bytes"] == 144 * mib
    assert summary["total_bytes"] == 288 * mib
    assert summary["preview_count"] == 1
    assert proof.MAX_SINGLE_PREVIEW_BYTES == 192 * mib


def test_compare_ancestor_is_strict_and_stream_cap_is_explicit(monkeypatch) -> None:
    head = "c" * 40
    payload = {
        "url": f"https://api.github.com/repos/owner/repo/compare/{COMMIT_SHA}...{head}",
        "status": "ahead",
        "ahead_by": 2,
        "behind_by": 0,
        "base_commit": {"sha": COMMIT_SHA},
        "merge_base_commit": {"sha": COMMIT_SHA},
        "commits": [{"sha": "b" * 40}, {"sha": head}],
    }
    monkeypatch.setattr(proof, "_request_json", lambda *args, **kwargs: payload)
    assert proof.compare_ancestor("owner/repo", COMMIT_SHA, head, token="token")
    payload["merge_base_commit"]["sha"] = "d" * 40
    assert not proof.compare_ancestor("owner/repo", COMMIT_SHA, head, token="token")
    payload["merge_base_commit"]["sha"] = COMMIT_SHA
    payload["commits"][-1]["sha"] = "e" * 40
    with pytest.raises(proof.AmbiguousProof, match="requested head"):
        proof.compare_ancestor("owner/repo", COMMIT_SHA, head, token="token")
    assert proof.MAX_COMPARE_RESPONSE_BYTES == 16 * 1024 * 1024


def test_compare_404_is_ambiguous_not_nonancestor(monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "token")

    def missing(*args, **kwargs):
        raise proof.NotFoundProof("comparison was not found")

    monkeypatch.setattr(proof, "_request_json", missing)
    assert (
        proof.main(
            [
                "compare-ancestor",
                "--repository",
                "owner/repo",
                "--base",
                COMMIT_SHA,
                "--head",
                "c" * 40,
            ]
        )
        == 90
    )


def test_duplicate_json_keys_are_ambiguous() -> None:
    with pytest.raises(proof.AmbiguousProof, match="strict UTF-8 JSON"):
        proof._decode_json(b'{"sha":"a","sha":"b"}', label="test response")


def test_proof_output_is_canonical_and_bounded(tmp_path) -> None:
    output = tmp_path / "proof.json"
    payload = {
        "schema_version": 1,
        "required_blobs": {},
        "commit_sha": COMMIT_SHA,
        "tree_sha": TREE_SHA,
    }
    proof._write_proof(output, payload)
    raw = output.read_text(encoding="utf-8")
    assert raw == json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"


def test_hydrate_ref_refuses_update_when_branch_moves_during_proof(
    monkeypatch, tmp_path
) -> None:
    moved_sha = "9" * 40
    observed = iter((COMMIT_SHA, moved_sha))
    remote_proof = {
        "schema_version": 1,
        "profile": "pages",
        "repository": "owner/repo",
        "commit_sha": COMMIT_SHA,
        "tree_sha": TREE_SHA,
        "file_count": 3,
        "max_blob_bytes": 8000,
        "required_blobs": {},
        "total_bytes": 10_100,
    }
    updates: list[tuple[Path, tuple[str, ...]]] = []

    monkeypatch.setenv("GH_TOKEN", "token")
    monkeypatch.setattr(
        proof,
        "resolve_remote_branch",
        lambda root, remote, branch: next(observed),
    )
    monkeypatch.setattr(
        proof,
        "prove_commit_tree",
        lambda repository, commit_sha, profile, *, token: remote_proof,
    )
    monkeypatch.setattr(proof, "hydrate_proven_commit", lambda root, remote, value: None)
    monkeypatch.setattr(
        proof,
        "_git",
        lambda root, *args, **kwargs: updates.append((root, args)) or b"",
    )

    assert (
        proof.main(
            [
                "hydrate-ref",
                "--repository",
                "owner/repo",
                "--branch",
                "gh-pages",
                "--profile",
                "pages",
                "--root",
                str(tmp_path),
                "--local-ref",
                "refs/remotes/origin/gh-pages",
                "--output",
                str(tmp_path / "proof.json"),
            ]
        )
        == 90
    )
    assert updates == []
    assert not (tmp_path / "proof.json").exists()
