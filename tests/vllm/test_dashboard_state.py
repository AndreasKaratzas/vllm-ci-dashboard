from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from vllm import dashboard_state as state
from vllm import public_projection as projection


GENERATED_AT = "2026-09-01T12:34:56Z"
PUBLIC_PROJECTION = {
    "schema_version": 1,
    "manifest_path": "publication_manifest.json",
    "manifest_sha256": "a" * 64,
    "file_count": 3,
    "total_bytes": 123,
}


def git(root: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        input=input_text,
        text=True,
        check=True,
        capture_output=True,
    )
    return result.stdout.strip()


def write_repair_attestation(
    path: Path,
    *,
    trusted_main_sha: str,
    proofs: dict[str, str],
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "github_compare_api",
                "trusted_main_sha": trusted_main_sha,
                "proofs": [
                    {
                        "state_sha": state_sha,
                        "code_sha": code_sha,
                        "result": "ancestor",
                    }
                    for state_sha, code_sha in sorted(proofs.items())
                ],
            }
        )
    )


def init_repo(root: Path) -> str:
    git(root, "init")
    git(root, "config", "user.name", "Dashboard Test")
    git(root, "config", "user.email", "dashboard@example.com")
    git(root, "config", "commit.gpgsign", "false")
    (root / "scripts").mkdir()
    (root / "scripts/app.py").write_text("print('trusted')\n")
    (root / "data/vllm/ci").mkdir(parents=True)
    (root / "data/vllm/ci/current.json").write_text('{"value": 1}\n')
    (root / "dashboards").mkdir()
    (root / "dashboards/summary.md").write_text("old dashboard\n")
    (root / "README.md").write_text("old readme\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "code and frozen seed")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def policy() -> state.StatePolicy:
    return state.StatePolicy(
        branch="dashboard-state",
        previous_branch="dashboard-state-previous",
        manifest_path="data/vllm/ci/dashboard_state.json",
        generated_roots=("data", "dashboards", "README.md"),
        max_blob_bytes=1024 * 1024,
        max_tree_bytes=8 * 1024 * 1024,
        max_files=100,
        bootstrap_allowed=True,
    )


def make_state(
    root: Path,
    policy: state.StatePolicy,
    code_sha: str,
    *,
    generation: str,
    value: int,
) -> state.ValidatedState:
    (root / "data/vllm/ci/current.json").write_text(json.dumps({"value": value}) + "\n")
    (root / projection.ATTESTATION_PATH).write_text(
        json.dumps(PUBLIC_PROJECTION, sort_keys=True, separators=(",", ":")) + "\n"
    )
    (root / "dashboards/summary.md").write_text(f"dashboard {value}\n")
    (root / "README.md").write_text(f"readme {value}\n")
    git(root, "add", "-A", "--", "data", "dashboards", "README.md")
    manifest = state.prepare_manifest(
        root,
        policy,
        code_sha=code_sha,
        generation_id=generation,
        generated_at=GENERATED_AT,
        source_refs={"queue-data": code_sha},
    )
    assert policy.manifest_path not in manifest["generated_files"]
    return state.create_parentless_commit(root, policy, expected_code_sha=code_sha)


def test_repository_policy_stays_below_ninety_mb_and_private() -> None:
    policy = state.load_policy(state.DEFAULT_CONFIG_PATH)
    assert policy.max_blob_bytes == 85 * 1024 * 1024
    assert policy.max_blob_bytes < 90_000_000
    assert policy.max_tree_bytes == 256 * 1024 * 1024
    assert policy.max_files == 10_000
    assert policy.generated_roots == ("data", "dashboards", "README.md")
    assert policy.bootstrap_allowed is False

    public_policy = json.loads((state.ROOT / "config/public_data_manifest.json").read_text())
    assert "vllm/ci/dashboard_state.json" in public_policy["never_publish_patterns"]
    assert "vllm/ci/public_projection_attestation.json" in public_policy["never_publish_patterns"]


def test_full_tree_validation_rejects_unknown_promisor_blob_size(
    tmp_path: Path,
    policy: state.StatePolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    object_id = "a" * 40

    def fake_git(_root: Path, *args: str, **_kwargs: object) -> bytes:
        assert args == ("ls-tree", "-r", "-z", "-l", "snapshot")
        return f"100644 blob {object_id} -\tdata/missing.json\0".encode()

    monkeypatch.setattr(state, "_git", fake_git)
    entries = state._tree_entries(tmp_path, "snapshot")

    assert entries["data/missing.json"].size == -1
    with pytest.raises(state.DashboardStateError, match="negative blob size"):
        state._validate_entry_limits(entries, policy, label="partial state")


def test_full_state_validation_does_not_hydrate_replaced_code_generated_blob(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    code_sha = init_repo(tmp_path)
    replaced_oid = git(
        tmp_path,
        "rev-parse",
        f"{code_sha}:data/vllm/ci/current.json",
    )
    snapshot = make_state(
        tmp_path,
        policy,
        code_sha,
        generation="run-partial-code",
        value=999,
    )
    object_path = tmp_path / ".git/objects" / replaced_oid[:2] / replaced_oid[2:]
    assert object_path.is_file()
    object_path.unlink()

    validated = state.validate_state_ref(
        tmp_path,
        snapshot.state_sha,
        policy,
        expected_code_sha=code_sha,
    )

    assert validated.state_sha == snapshot.state_sha
    assert validated.code_sha == code_sha


def test_parentless_snapshot_round_trip_and_public_marker(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    code_sha = init_repo(tmp_path)
    snapshot = make_state(tmp_path, policy, code_sha, generation="run-100.1", value=2)

    assert (
        git(tmp_path, "rev-list", "--parents", "-n", "1", snapshot.state_sha) == snapshot.state_sha
    )
    assert snapshot.code_sha == code_sha
    assert snapshot.manifest["schema_version"] == 2
    assert snapshot.manifest["source_refs"] == {"queue-data": code_sha}
    content_sizes = [
        entry.size for path, entry in snapshot.entries.items() if path != policy.manifest_path
    ]
    assert snapshot.manifest["content_summary"] == {
        "file_count": len(content_sizes),
        "max_blob_bytes": max(content_sizes),
        "total_bytes": sum(content_sizes),
    }
    assert set(snapshot.manifest["generated_files"]) == {
        "README.md",
        "dashboards/summary.md",
        "data/vllm/ci/current.json",
        projection.ATTESTATION_PATH,
    }
    for path, descriptor in snapshot.manifest["generated_files"].items():
        assert descriptor["git_oid"] == git(
            tmp_path,
            "rev-parse",
            f"{snapshot.state_sha}:{path}",
        )

    marker_path = tmp_path / "_site/publication_generation.json"
    marker = state.write_public_marker(
        marker_path,
        snapshot,
        public_projection=PUBLIC_PROJECTION,
        publication_status={"generated_at": GENERATED_AT},
        expected_state_tree=snapshot.state_tree,
        expected_code_sha=code_sha,
        expected_generated_at=GENERATED_AT,
    )
    assert (
        state.validate_public_marker(
            json.loads(marker_path.read_text()),
            expected_state_sha=snapshot.state_sha,
        )
        == marker
    )
    assert marker["schema_version"] == 2
    assert marker["public_projection"] == PUBLIC_PROJECTION


def test_public_marker_rejects_publication_status_generation_mismatch(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    code_sha = init_repo(tmp_path)
    snapshot = make_state(tmp_path, policy, code_sha, generation="run-101", value=2)

    with pytest.raises(
        state.DashboardStateError,
        match="state generated_at does not match publication status generated_at",
    ):
        state.write_public_marker(
            tmp_path / "_site/publication_generation.json",
            snapshot,
            public_projection=PUBLIC_PROJECTION,
            publication_status={"generated_at": "2026-08-17T12:01:00Z"},
        )


def test_public_marker_metadata_mode_requires_code_and_never_uses_full_validation(
    tmp_path: Path,
    policy: state.StatePolicy,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code_sha = init_repo(tmp_path)
    snapshot = make_state(tmp_path, policy, code_sha, generation="run-100.marker", value=2)
    marker_path = tmp_path / "_site/publication_generation.json"
    original_metadata_validator = state.validate_state_ref_metadata
    metadata_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(state, "load_policy", lambda _path: policy)

    def reject_full_validation(*_args: object, **_kwargs: object) -> state.ValidatedState:
        raise AssertionError("full state validator must not run in metadata-only mode")

    def record_metadata_validation(
        root: Path,
        ref: str,
        state_policy: state.StatePolicy,
        *,
        expected_code_sha: str,
    ) -> state.ValidatedState:
        metadata_calls.append((ref, expected_code_sha))
        return original_metadata_validator(
            root,
            ref,
            state_policy,
            expected_code_sha=expected_code_sha,
        )

    monkeypatch.setattr(state, "validate_state_ref", reject_full_validation)
    monkeypatch.setattr(state, "validate_state_ref_metadata", record_metadata_validation)

    common_args = [
        "--root",
        str(tmp_path),
        "--config",
        str(tmp_path / "unused-policy.json"),
        "write-public-marker",
        "--state-sha",
        snapshot.state_sha,
        "--public-attestation",
        str(tmp_path / projection.ATTESTATION_PATH),
        "--output",
        str(marker_path),
    ]
    assert state.main([*common_args, "--metadata-only"]) == 1
    assert "--metadata-only requires --code-sha" in capsys.readouterr().err
    assert metadata_calls == []

    assert (
        state.main(
            [
                *common_args,
                "--metadata-only",
                "--code-sha",
                code_sha,
                "--state-tree",
                snapshot.state_tree,
                "--generated-at",
                GENERATED_AT,
            ]
        )
        == 0
    )
    assert metadata_calls == [(snapshot.state_sha, code_sha)]
    marker = json.loads(marker_path.read_text())
    assert marker["state_sha"] == snapshot.state_sha
    assert marker["state_tree"] == snapshot.state_tree
    assert marker["code_sha"] == code_sha


def test_public_marker_cli_defaults_to_full_validation(
    tmp_path: Path,
    policy: state.StatePolicy,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code_sha = init_repo(tmp_path)
    snapshot = make_state(tmp_path, policy, code_sha, generation="run-100.full-marker", value=2)
    original_full_validator = state.validate_state_ref
    full_calls: list[str] = []

    monkeypatch.setattr(state, "load_policy", lambda _path: policy)

    def record_full_validation(
        root: Path,
        ref: str,
        state_policy: state.StatePolicy,
        *,
        expected_code_sha: str | None = None,
    ) -> state.ValidatedState:
        full_calls.append(ref)
        return original_full_validator(
            root,
            ref,
            state_policy,
            expected_code_sha=expected_code_sha,
        )

    def reject_metadata_validation(*_args: object, **_kwargs: object) -> state.ValidatedState:
        raise AssertionError("metadata validator must be explicitly selected")

    monkeypatch.setattr(state, "validate_state_ref", record_full_validation)
    monkeypatch.setattr(state, "validate_state_ref_metadata", reject_metadata_validation)

    publication_status = tmp_path / "publication_status.json"
    publication_status.write_text(
        json.dumps({"generated_at": GENERATED_AT}) + "\n",
        encoding="utf-8",
    )

    base_args = [
        "--root",
        str(tmp_path),
        "--config",
        str(tmp_path / "unused-policy.json"),
        "write-public-marker",
        "--state-sha",
        snapshot.state_sha,
        "--code-sha",
        code_sha,
        "--public-attestation",
        str(tmp_path / projection.ATTESTATION_PATH),
    ]
    missing_output = tmp_path / "_site/missing-status-marker.json"
    assert state.main([*base_args, "--output", str(missing_output)]) == 1
    assert "requires --publication-status" in capsys.readouterr().err
    assert not missing_output.exists()

    assert (
        state.main(
            [
                *base_args,
                "--publication-status",
                str(publication_status),
                "--output",
                str(tmp_path / "_site/publication_generation.json"),
            ]
        )
        == 0
    )
    assert full_calls == [snapshot.state_sha, snapshot.state_sha]

    publication_status.write_text(
        json.dumps({"generated_at": "2026-09-01T12:35:56Z"}) + "\n",
        encoding="utf-8",
    )
    mismatch_output = tmp_path / "_site/mismatched-status-marker.json"
    assert (
        state.main(
            [
                *base_args,
                "--publication-status",
                str(publication_status),
                "--output",
                str(mismatch_output),
            ]
        )
        == 1
    )
    assert "does not match publication status" in capsys.readouterr().err
    assert not mismatch_output.exists()
    assert full_calls == [snapshot.state_sha, snapshot.state_sha, snapshot.state_sha]


@pytest.mark.parametrize(
    ("attestation_payload", "message"),
    [
        (None, "missing its public projection attestation"),
        (b"{}\n", "attestation is invalid"),
        (
            (json.dumps(PUBLIC_PROJECTION, indent=2, sort_keys=True) + "\n").encode(),
            "not canonical JSON",
        ),
        (b" " * 4097, r"exceeds its (?:4096-)?byte limit"),
    ],
)
def test_state_validation_rejects_missing_malformed_or_unbounded_projection_attestation(
    tmp_path: Path,
    policy: state.StatePolicy,
    attestation_payload: bytes | None,
    message: str,
) -> None:
    code_sha = init_repo(tmp_path)
    (tmp_path / "data/vllm/ci/current.json").write_text('{"value":2}\n')
    if attestation_payload is not None:
        (tmp_path / projection.ATTESTATION_PATH).write_bytes(attestation_payload)
    git(tmp_path, "add", "-A", "--", "data", "dashboards", "README.md")
    state.prepare_manifest(
        tmp_path,
        policy,
        code_sha=code_sha,
        generation_id="run-100.invalid-attestation",
        generated_at=GENERATED_AT,
    )
    tree_sha = git(tmp_path, "write-tree")
    invalid_state = git(tmp_path, "commit-tree", tree_sha, input_text="invalid attestation\n")

    with pytest.raises(state.DashboardStateError, match=message):
        state.validate_state_ref(tmp_path, invalid_state, policy)
    with pytest.raises(state.DashboardStateError, match=message):
        state.validate_state_ref_metadata(
            tmp_path,
            invalid_state,
            policy,
            expected_code_sha=code_sha,
        )


def test_metadata_validation_reads_only_manifest_and_projection_attestation(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    code_sha = init_repo(tmp_path)
    snapshot = make_state(tmp_path, policy, code_sha, generation="run-100.metadata", value=2)
    missing_blob_paths = [
        "data/vllm/ci/current.json",
        "scripts/app.py",
    ]
    for path in missing_blob_paths:
        object_id = git(tmp_path, "rev-parse", f"{snapshot.state_sha}:{path}")
        loose_object = tmp_path / ".git/objects" / object_id[:2] / object_id[2:]
        assert loose_object.is_file()
        loose_object.unlink()

    validated = state.validate_state_ref_metadata(
        tmp_path,
        snapshot.state_sha,
        policy,
        expected_code_sha=code_sha,
    )

    assert validated.state_sha == snapshot.state_sha
    assert validated.state_tree == snapshot.state_tree
    assert validated.code_sha == code_sha
    outputs = state._metadata_state_outputs(validated)
    assert outputs["validation_mode"] == "metadata_oid"
    assert outputs["manifest_schema_version"] == 2
    assert outputs["generated_file_count"] == len(snapshot.manifest["generated_files"])
    with pytest.raises(state.DashboardStateError):
        state.validate_state_ref(
            tmp_path,
            snapshot.state_sha,
            policy,
            expected_code_sha=code_sha,
        )


def test_metadata_validation_rejects_generated_oid_source_identity_and_declared_bounds(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    code_sha = init_repo(tmp_path)
    snapshot = make_state(tmp_path, policy, code_sha, generation="run-100.metadata-bad", value=2)

    (tmp_path / "data/vllm/ci/current.json").write_text('{"value":999}\n')
    git(tmp_path, "add", "data/vllm/ci/current.json")
    generated_tamper = git(
        tmp_path,
        "commit-tree",
        git(tmp_path, "write-tree"),
        input_text="generated oid tamper\n",
    )
    with pytest.raises(state.DashboardStateError, match="generated Git identity"):
        state.validate_state_ref_metadata(
            tmp_path,
            generated_tamper,
            policy,
            expected_code_sha=code_sha,
        )

    git(tmp_path, "reset", "--hard", code_sha)
    git(tmp_path, "read-tree", snapshot.state_sha)
    git(tmp_path, "checkout-index", "-a", "-f")
    (tmp_path / "scripts/app.py").write_text("print('different source')\n")
    git(tmp_path, "add", "scripts/app.py")
    source_tamper = git(
        tmp_path,
        "commit-tree",
        git(tmp_path, "write-tree"),
        input_text="source oid tamper\n",
    )
    with pytest.raises(state.DashboardStateError, match="differs from expected code tree"):
        state.validate_state_ref_metadata(
            tmp_path,
            source_tamper,
            policy,
            expected_code_sha=code_sha,
        )

    git(tmp_path, "read-tree", snapshot.state_sha)
    git(tmp_path, "checkout-index", "-a", "-f")
    manifest_path = tmp_path / policy.manifest_path
    manifest = json.loads(manifest_path.read_text())
    manifest["generated_files"]["data/vllm/ci/current.json"]["bytes"] = policy.max_blob_bytes + 1
    manifest_path.write_bytes(state._canonical_manifest_bytes(manifest))
    git(tmp_path, "add", policy.manifest_path)
    declared_oversize = git(
        tmp_path,
        "commit-tree",
        git(tmp_path, "write-tree"),
        input_text="declared oversize\n",
    )
    with pytest.raises(state.DashboardStateError, match="declared generated blob"):
        state.validate_state_ref_metadata(
            tmp_path,
            declared_oversize,
            policy,
            expected_code_sha=code_sha,
        )


def test_metadata_validation_requires_canonical_state_manifest(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    code_sha = init_repo(tmp_path)
    snapshot = make_state(tmp_path, policy, code_sha, generation="run-100.noncanonical", value=2)
    manifest_path = tmp_path / policy.manifest_path
    manifest_path.write_text(manifest_path.read_text() + "\n")
    git(tmp_path, "add", policy.manifest_path)
    noncanonical = git(
        tmp_path,
        "commit-tree",
        git(tmp_path, "write-tree"),
        input_text="noncanonical manifest\n",
    )

    with pytest.raises(state.DashboardStateError, match="manifest is not canonical JSON"):
        state.validate_state_ref_metadata(
            tmp_path,
            noncanonical,
            policy,
            expected_code_sha=code_sha,
        )
    with pytest.raises(state.DashboardStateError, match="manifest is not canonical JSON"):
        state.validate_state_ref(
            tmp_path,
            noncanonical,
            policy,
            expected_code_sha=code_sha,
        )
    assert snapshot.manifest["schema_version"] == 2


def test_refresh_manifest_binds_attestation_without_advancing_generation(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    code_sha = init_repo(tmp_path)
    (tmp_path / "data/vllm/ci/current.json").write_text('{"value":2}\n')
    git(tmp_path, "add", "-A", "--", "data", "dashboards", "README.md")
    original = state.prepare_manifest(
        tmp_path,
        policy,
        code_sha=code_sha,
        generation_id="run-100.2",
        generated_at=GENERATED_AT,
        source_refs={"queue-data": code_sha},
    )

    attestation_path = tmp_path / projection.ATTESTATION_PATH
    attestation_path.write_text(
        json.dumps(PUBLIC_PROJECTION, sort_keys=True, separators=(",", ":")) + "\n"
    )
    git(tmp_path, "add", "--", projection.ATTESTATION_PATH)
    refreshed = state.refresh_manifest(
        tmp_path,
        policy,
        expected_code_sha=code_sha,
    )

    for key in ("generation_id", "generated_at", "code_sha", "source_refs"):
        assert refreshed[key] == original[key]
    descriptor = refreshed["generated_files"][projection.ATTESTATION_PATH]
    assert descriptor["bytes"] == attestation_path.stat().st_size
    snapshot = state.create_parentless_commit(
        tmp_path,
        policy,
        expected_code_sha=code_sha,
    )
    assert projection.ATTESTATION_PATH in snapshot.manifest["generated_files"]


def test_materialization_is_exact_and_preserves_source_files(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    code_sha = init_repo(tmp_path)
    snapshot = make_state(tmp_path, policy, code_sha, generation="run-101.1", value=2)
    (tmp_path / "data/vllm/ci/current.json").write_text('{"value": 999}\n')
    (tmp_path / "data/vllm/ci/stale.json").write_text("{}\n")
    (tmp_path / "dashboards/stale.md").write_text("stale\n")
    (tmp_path / "README.md").write_text("stale readme\n")
    (tmp_path / "scripts/app.py").write_text("print('local source edit')\n")

    restored = state.materialize_generated_roots(
        tmp_path,
        snapshot.state_sha,
        policy,
        expected_code_sha=code_sha,
    )

    assert restored.state_sha == snapshot.state_sha
    assert json.loads((tmp_path / "data/vllm/ci/current.json").read_text()) == {"value": 2}
    assert not (tmp_path / "data/vllm/ci/stale.json").exists()
    assert not (tmp_path / "dashboards/stale.md").exists()
    assert (tmp_path / "README.md").read_text() == "readme 2\n"
    assert (tmp_path / "scripts/app.py").read_text() == "print('local source edit')\n"


def test_materialization_failure_restores_every_old_root(
    tmp_path: Path,
    policy: state.StatePolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code_sha = init_repo(tmp_path)
    snapshot = make_state(tmp_path, policy, code_sha, generation="run-102.1", value=2)
    (tmp_path / "data/vllm/ci/current.json").write_text('{"value": 700}\n')
    (tmp_path / "dashboards/summary.md").write_text("old live dashboard\n")
    (tmp_path / "README.md").write_text("old live readme\n")
    original_replace = state.os.replace

    def fail_installing_readme(source: os.PathLike[str], target: os.PathLike[str]) -> None:
        source_path = Path(source)
        target_path = Path(target)
        if (
            ".dashboard-state-stage-" in source_path.as_posix()
            and source_path.name == "README.md"
            and target_path == tmp_path / "README.md"
        ):
            raise OSError("injected replacement failure")
        original_replace(source, target)

    monkeypatch.setattr(state.os, "replace", fail_installing_readme)
    with pytest.raises(state.DashboardStateError, match="materialization failed"):
        state.materialize_generated_roots(tmp_path, snapshot.state_sha, policy)

    assert json.loads((tmp_path / "data/vllm/ci/current.json").read_text()) == {"value": 700}
    assert (tmp_path / "dashboards/summary.md").read_text() == "old live dashboard\n"
    assert (tmp_path / "README.md").read_text() == "old live readme\n"
    assert not list(tmp_path.glob(".dashboard-state-stage-*"))
    assert not list(tmp_path.glob(".dashboard-state-backup-*"))


def test_validation_rejects_non_parentless_state(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    code_sha = init_repo(tmp_path)
    make_state(tmp_path, policy, code_sha, generation="run-103.1", value=2)
    git(tmp_path, "commit", "-m", "ordinary state-shaped child")
    child = git(tmp_path, "rev-parse", "HEAD")

    with pytest.raises(state.DashboardStateError, match="parentless"):
        state.validate_state_ref(tmp_path, child, policy)

    remote = tmp_path / "shallow-remote.git"
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(tmp_path, "push", str(remote), f"{child}:refs/heads/dashboard-state")
    subprocess.run(
        [
            "git",
            "clone",
            "--depth=1",
            "--branch=dashboard-state",
            f"file://{remote}",
            str(shallow),
        ],
        check=True,
        capture_output=True,
    )
    # rev-list hides parents at a shallow boundary. Reading the commit header
    # must still reject this ordinary child as a state snapshot.
    assert git(shallow, "rev-list", "--parents", "-n", "1", "HEAD") == child
    with pytest.raises(state.DashboardStateError, match="parentless"):
        state.validate_state_ref(shallow, "HEAD", policy)


def test_validation_rejects_generated_hash_or_file_set_mismatch(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    code_sha = init_repo(tmp_path)
    make_state(tmp_path, policy, code_sha, generation="run-104.1", value=2)
    (tmp_path / "data/vllm/ci/current.json").write_text('{"value": 3}\n')
    (tmp_path / "data/vllm/ci/undeclared.json").write_text("{}\n")
    git(tmp_path, "add", "data/vllm/ci/current.json", "data/vllm/ci/undeclared.json")
    tree_sha = git(tmp_path, "write-tree")
    malformed = git(tmp_path, "commit-tree", tree_sha, input_text="malformed state\n")

    with pytest.raises(
        state.DashboardStateError,
        match="generated file set|metadata|hash|content_summary",
    ):
        state.validate_state_ref(tmp_path, malformed, policy)


def test_prepare_rejects_source_tree_drift_and_expected_code_mismatch(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    code_sha = init_repo(tmp_path)
    (tmp_path / "scripts/app.py").write_text("print('untrusted')\n")
    git(tmp_path, "add", "scripts/app.py")
    with pytest.raises(state.DashboardStateError, match="source tree differs"):
        state.prepare_manifest(
            tmp_path,
            policy,
            code_sha=code_sha,
            generation_id="run-105.1",
            generated_at=GENERATED_AT,
        )

    git(tmp_path, "reset", "HEAD", "scripts/app.py")
    git(tmp_path, "restore", "scripts/app.py")
    snapshot = make_state(tmp_path, policy, code_sha, generation="run-105.2", value=2)
    (tmp_path / "scripts/app.py").write_text("print('unstaged test-only source')\n")
    with pytest.raises(state.DashboardStateError, match="source worktree"):
        state.create_parentless_commit(tmp_path, policy)
    git(tmp_path, "restore", "scripts/app.py")

    (tmp_path / "scripts/untracked.py").write_text("print('not in state')\n")
    with pytest.raises(state.DashboardStateError, match="untracked non-artifact"):
        state.create_parentless_commit(tmp_path, policy)
    (tmp_path / "scripts/untracked.py").unlink()
    (tmp_path / "test-output.txt").write_text("allowed workflow artifact\n")
    assert state.create_parentless_commit(tmp_path, policy).state_tree == snapshot.state_tree

    other_sha = "f" * 40
    with pytest.raises(state.DashboardStateError, match="does not match expected"):
        state.validate_state_ref(
            tmp_path,
            snapshot.state_sha,
            policy,
            expected_code_sha=other_sha,
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"max_blob_bytes": 8}, "blob"),
        ({"max_tree_bytes": 16}, "bytes"),
        ({"max_files": 2}, "files"),
    ],
)
def test_candidate_limits_fail_without_large_allocations(
    tmp_path: Path,
    policy: state.StatePolicy,
    override: dict[str, int],
    message: str,
) -> None:
    code_sha = init_repo(tmp_path)
    limited = replace(policy, **override)
    git(tmp_path, "add", "-A", "--", "data", "dashboards", "README.md")

    with pytest.raises(state.DashboardStateError, match=message):
        state.prepare_manifest(
            tmp_path,
            limited,
            code_sha=code_sha,
            generation_id="run-106.1",
            generated_at=GENERATED_AT,
        )


def test_manifest_accepts_only_safe_same_shape_n_minus_one_policy(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    code_sha = init_repo(tmp_path)
    snapshot = make_state(tmp_path, policy, code_sha, generation="run-106.compat", value=2)
    older = json.loads(json.dumps(snapshot.manifest))
    older["schema_version"] = state.STATE_MANIFEST_SCHEMA_VERSION - 1
    summary = older["content_summary"]
    older["limits"] = {
        "max_blob_bytes": max(1, summary["max_blob_bytes"]),
        "max_tree_bytes": max(1, summary["total_bytes"] + 1024 * 1024),
        "max_files": max(1, summary["file_count"] + 1),
    }
    normalized = state._normalize_manifest(older, policy)
    assert normalized["schema_version"] == state.STATE_MANIFEST_SCHEMA_VERSION - 1
    assert normalized["limits"] == older["limits"]

    too_old = json.loads(json.dumps(older))
    too_old["schema_version"] -= 1
    with pytest.raises(state.DashboardStateError, match="schema_version"):
        state._normalize_manifest(too_old, policy)

    expanded = json.loads(json.dumps(older))
    expanded["limits"]["max_tree_bytes"] = state.DEFAULT_MAX_TREE_BYTES + 1
    with pytest.raises(state.DashboardStateError, match="hard bounds"):
        state._normalize_manifest(expanded, policy)

    underdeclared = json.loads(json.dumps(older))
    underdeclared["limits"]["max_tree_bytes"] = max(
        underdeclared["limits"]["max_blob_bytes"], summary["total_bytes"] - 1
    )
    with pytest.raises(state.DashboardStateError, match="max_tree_bytes"):
        state._normalize_manifest(underdeclared, policy)


def test_candidate_rejects_symlink_and_submodule(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    code_sha = init_repo(tmp_path)
    (tmp_path / "data/link").symlink_to("vllm/ci/current.json")
    git(tmp_path, "add", "data/link")
    with pytest.raises(state.DashboardStateError, match="symlink"):
        state.prepare_manifest(
            tmp_path,
            policy,
            code_sha=code_sha,
            generation_id="run-107.1",
            generated_at=GENERATED_AT,
        )

    git(tmp_path, "reset", "HEAD", "data/link")
    (tmp_path / "data/link").unlink()
    git(
        tmp_path,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{code_sha},data/gitlink",
    )
    with pytest.raises(state.DashboardStateError, match="submodule"):
        state.prepare_manifest(
            tmp_path,
            policy,
            code_sha=code_sha,
            generation_id="run-107.2",
            generated_at=GENERATED_AT,
        )


def test_policy_rejects_path_traversal_and_non_boolean_bootstrap(
    tmp_path: Path,
) -> None:
    valid = json.loads(state.DEFAULT_CONFIG_PATH.read_text())
    valid["manifest_path"] = "../dashboard_state.json"
    config = tmp_path / "config.json"
    config.write_text(json.dumps(valid))
    with pytest.raises(state.DashboardStateError, match="canonical POSIX path"):
        state.load_policy(config)

    valid = json.loads(state.DEFAULT_CONFIG_PATH.read_text())
    valid["bootstrap_allowed"] = "true"
    config.write_text(json.dumps(valid))
    with pytest.raises(state.DashboardStateError, match="bootstrap_allowed"):
        state.load_policy(config)

    valid = json.loads(state.DEFAULT_CONFIG_PATH.read_text())
    valid["limits"]["max_tree_bytes"] = state.DEFAULT_MAX_TREE_BYTES + 1
    config.write_text(json.dumps(valid))
    with pytest.raises(state.DashboardStateError, match="immutable hard bounds"):
        state.load_policy(config)


def test_atomic_two_slot_rotation_and_stale_observation_failure(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Dashboard Test")
    git(repo, "config", "user.email", "dashboard@example.com")
    git(repo, "config", "commit.gpgsign", "false")
    code_sha = init_existing_repo_contents(repo)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))

    first = make_state(repo, policy, code_sha, generation="run-108.1", value=2)
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=first.state_sha,
        expected_current_sha=None,
        expected_previous_sha=None,
    )
    assert remote_sha(repo, policy.branch) == first.state_sha
    assert remote_sha(repo, policy.previous_branch) == first.state_sha

    second = make_state(repo, policy, code_sha, generation="run-108.2", value=3)
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=second.state_sha,
        expected_current_sha=first.state_sha,
        expected_previous_sha=first.state_sha,
    )
    assert remote_sha(repo, policy.branch) == second.state_sha
    assert remote_sha(repo, policy.previous_branch) == first.state_sha
    assert git(repo, "rev-list", "--count", second.state_sha) == "1"
    assert git(repo, "rev-list", "--count", first.state_sha) == "1"

    # A definitively missing previous slot does not make the validated current
    # state ambiguous. The next exact-leased rotation repairs redundancy
    # without another state fetch or a Buildkite request.
    git(repo, "push", "origin", f":refs/heads/{policy.previous_branch}")
    assert remote_sha(repo, policy.previous_branch) is None

    third = make_state(repo, policy, code_sha, generation="run-108.3", value=4)
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=third.state_sha,
        expected_current_sha=second.state_sha,
        expected_previous_sha=None,
    )
    assert remote_sha(repo, policy.branch) == third.state_sha
    assert remote_sha(repo, policy.previous_branch) == second.state_sha

    fourth = make_state(repo, policy, code_sha, generation="run-108.4", value=5)
    with pytest.raises(state.DashboardStateError, match="previous state changed"):
        state.rotate_state_refs(
            repo,
            policy,
            new_state_sha=fourth.state_sha,
            expected_current_sha=third.state_sha,
            expected_previous_sha=None,
        )
    assert remote_sha(repo, policy.branch) == third.state_sha
    assert remote_sha(repo, policy.previous_branch) == second.state_sha


@pytest.mark.parametrize("missing_object", ["commit", "content"])
def test_rotation_does_not_read_discarded_previous_state(
    tmp_path: Path,
    policy: state.StatePolicy,
    missing_object: str,
) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    code_sha = init_repo(repo)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))

    discarded = make_state(repo, policy, code_sha, generation="run-108.old", value=2)
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=discarded.state_sha,
        expected_current_sha=None,
        expected_previous_sha=None,
    )
    current = make_state(repo, policy, code_sha, generation="run-108.current", value=3)
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=current.state_sha,
        expected_current_sha=discarded.state_sha,
        expected_previous_sha=discarded.state_sha,
    )
    candidate = make_state(repo, policy, code_sha, generation="run-108.next", value=4)

    if missing_object == "commit":
        object_id = discarded.state_sha
    else:
        object_id = git(
            repo,
            "rev-parse",
            f"{discarded.state_sha}:data/vllm/ci/current.json",
        )
    loose_object = repo / ".git/objects" / object_id[:2] / object_id[2:]
    assert loose_object.is_file()
    loose_object.unlink()
    assert (
        subprocess.run(
            ["git", "cat-file", "-e", object_id],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        != 0
    )
    with pytest.raises(state.DashboardStateError):
        state.validate_state_ref(repo, discarded.state_sha, policy)

    result = state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=candidate.state_sha,
        expected_current_sha=current.state_sha,
        expected_previous_sha=discarded.state_sha,
    )

    assert result == {
        "state_sha": candidate.state_sha,
        "previous_state_sha": current.state_sha,
    }
    assert remote_sha(repo, policy.branch) == candidate.state_sha
    assert remote_sha(repo, policy.previous_branch) == current.state_sha


@pytest.mark.parametrize("current_mode", ["missing", "corrupt"])
def test_repair_slots_promotes_only_valid_previous_atomically(
    tmp_path: Path,
    policy: state.StatePolicy,
    current_mode: str,
) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    attestation = tmp_path / "ancestry.json"
    repo.mkdir()
    code_sha = init_repo(repo)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    previous = make_state(repo, policy, code_sha, generation="run-109.1", value=2)
    current = make_state(repo, policy, code_sha, generation="run-109.2", value=3)
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=previous.state_sha,
        expected_current_sha=None,
        expected_previous_sha=None,
    )
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=current.state_sha,
        expected_current_sha=previous.state_sha,
        expected_previous_sha=previous.state_sha,
    )

    if current_mode == "missing":
        git(repo, "push", "origin", f":refs/heads/{policy.branch}")
        observed_current = None
    else:
        # This is a fetched commit but not a valid dashboard-state snapshot.
        git(repo, "push", "--force", "origin", f"{code_sha}:refs/heads/{policy.branch}")
        observed_current = code_sha
    ancestry_proofs = {previous.state_sha: code_sha}
    if observed_current is not None:
        ancestry_proofs[observed_current] = code_sha
    write_repair_attestation(
        attestation,
        trusted_main_sha=code_sha,
        proofs=ancestry_proofs,
    )

    outputs = state.repair_state_slots(
        repo,
        replace(policy, bootstrap_allowed=False),
        expected_current_sha=observed_current,
        expected_previous_sha=previous.state_sha,
        trusted_main_sha=code_sha,
        ancestry_attestation=attestation,
    )

    assert outputs == {
        "repair_action": "repaired",
        "valid_slots": "previous",
        "current_state_sha": previous.state_sha,
        "previous_state_sha": previous.state_sha,
    }
    assert remote_sha(repo, policy.branch) == previous.state_sha
    assert remote_sha(repo, policy.previous_branch) == previous.state_sha


def test_repair_slots_promotes_only_valid_current_atomically(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    attestation = tmp_path / "ancestry.json"
    repo.mkdir()
    code_sha = init_repo(repo)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    current = make_state(repo, policy, code_sha, generation="run-110.1", value=2)
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=current.state_sha,
        expected_current_sha=None,
        expected_previous_sha=None,
    )
    git(
        repo,
        "push",
        "--force",
        "origin",
        f"{code_sha}:refs/heads/{policy.previous_branch}",
    )
    write_repair_attestation(
        attestation,
        trusted_main_sha=code_sha,
        proofs={current.state_sha: code_sha},
    )

    outputs = state.repair_state_slots(
        repo,
        policy,
        expected_current_sha=current.state_sha,
        expected_previous_sha=code_sha,
        trusted_main_sha=code_sha,
        ancestry_attestation=attestation,
    )

    assert outputs["repair_action"] == "repaired"
    assert outputs["valid_slots"] == "current"
    assert remote_sha(repo, policy.branch) == current.state_sha
    assert remote_sha(repo, policy.previous_branch) == current.state_sha


def test_repair_slots_rejects_hash_bound_malformed_projection_attestation(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    attestation = tmp_path / "ancestry.json"
    repo.mkdir()
    code_sha = init_repo(repo)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    previous = make_state(repo, policy, code_sha, generation="run-110.2", value=2)
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=previous.state_sha,
        expected_current_sha=None,
        expected_previous_sha=None,
    )

    (repo / projection.ATTESTATION_PATH).write_text("{}\n")
    (repo / "data/vllm/ci/current.json").write_text('{"value":3}\n')
    git(repo, "add", "-A", "--", "data", "dashboards", "README.md")
    state.prepare_manifest(
        repo,
        policy,
        code_sha=code_sha,
        generation_id="run-110.3",
        generated_at=GENERATED_AT,
    )
    malformed_current = git(
        repo,
        "commit-tree",
        git(repo, "write-tree"),
        input_text="hash-bound malformed projection attestation\n",
    )
    git(
        repo,
        "push",
        "--force",
        "origin",
        f"{malformed_current}:refs/heads/{policy.branch}",
    )
    write_repair_attestation(
        attestation,
        trusted_main_sha=code_sha,
        proofs={malformed_current: code_sha, previous.state_sha: code_sha},
    )

    outputs = state.repair_state_slots(
        repo,
        policy,
        expected_current_sha=malformed_current,
        expected_previous_sha=previous.state_sha,
        trusted_main_sha=code_sha,
        ancestry_attestation=attestation,
    )

    assert outputs["valid_slots"] == "previous"
    assert remote_sha(repo, policy.branch) == previous.state_sha
    assert remote_sha(repo, policy.previous_branch) == previous.state_sha


def test_repair_slots_rejects_valid_shaped_state_without_main_ancestry_proof(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    attestation = tmp_path / "ancestry.json"
    repo.mkdir()
    trusted_code_sha = init_repo(repo)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    previous = make_state(repo, policy, trusted_code_sha, generation="run-110.4", value=2)
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=previous.state_sha,
        expected_current_sha=None,
        expected_previous_sha=None,
    )
    untrusted_code_sha = git(
        repo,
        "commit-tree",
        f"{trusted_code_sha}^{{tree}}",
        input_text="untrusted parallel code\n",
    )
    untrusted_current = make_state(
        repo,
        policy,
        untrusted_code_sha,
        generation="run-110.5",
        value=3,
    )
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=untrusted_current.state_sha,
        expected_current_sha=previous.state_sha,
        expected_previous_sha=previous.state_sha,
    )
    write_repair_attestation(
        attestation,
        trusted_main_sha=trusted_code_sha,
        proofs={previous.state_sha: trusted_code_sha},
    )

    outputs = state.repair_state_slots(
        repo,
        policy,
        expected_current_sha=untrusted_current.state_sha,
        expected_previous_sha=previous.state_sha,
        trusted_main_sha=trusted_code_sha,
        ancestry_attestation=attestation,
    )

    assert outputs["valid_slots"] == "previous"
    assert remote_sha(repo, policy.branch) == previous.state_sha
    assert remote_sha(repo, policy.previous_branch) == previous.state_sha


def test_repair_slots_recovers_when_other_slot_is_not_a_commit(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    attestation = tmp_path / "ancestry.json"
    repo.mkdir()
    code_sha = init_repo(repo)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    previous = make_state(repo, policy, code_sha, generation="run-110.6", value=2)
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=previous.state_sha,
        expected_current_sha=None,
        expected_previous_sha=None,
    )
    blob_sha = git(repo, "hash-object", "-w", "--stdin", input_text="not a commit\n")
    git(repo, "push", "origin", f"{blob_sha}:refs/dashboard-test/noncommit")
    # Git's normal update-ref protects heads from non-commits. Write the loose
    # ref directly to model a remotely corrupt ref that ls-remote can expose.
    (remote / "refs/heads" / policy.branch).write_text(blob_sha + "\n")
    write_repair_attestation(
        attestation,
        trusted_main_sha=code_sha,
        proofs={blob_sha: code_sha, previous.state_sha: code_sha},
    )

    outputs = state.repair_state_slots(
        repo,
        policy,
        expected_current_sha=blob_sha,
        expected_previous_sha=previous.state_sha,
        trusted_main_sha=code_sha,
        ancestry_attestation=attestation,
    )

    assert outputs["valid_slots"] == "previous"
    assert remote_sha(repo, policy.branch) == previous.state_sha
    assert remote_sha(repo, policy.previous_branch) == previous.state_sha


def test_repair_slots_is_noop_when_both_slots_are_valid(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    attestation = tmp_path / "ancestry.json"
    repo.mkdir()
    code_sha = init_repo(repo)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    previous = make_state(repo, policy, code_sha, generation="run-111.1", value=2)
    current = make_state(repo, policy, code_sha, generation="run-111.2", value=3)
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=previous.state_sha,
        expected_current_sha=None,
        expected_previous_sha=None,
    )
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=current.state_sha,
        expected_current_sha=previous.state_sha,
        expected_previous_sha=previous.state_sha,
    )
    write_repair_attestation(
        attestation,
        trusted_main_sha=code_sha,
        proofs={current.state_sha: code_sha, previous.state_sha: code_sha},
    )

    outputs = state.repair_state_slots(
        repo,
        policy,
        expected_current_sha=current.state_sha,
        expected_previous_sha=previous.state_sha,
        trusted_main_sha=code_sha,
        ancestry_attestation=attestation,
    )

    assert outputs == {
        "repair_action": "noop",
        "valid_slots": "both",
        "current_state_sha": current.state_sha,
        "previous_state_sha": previous.state_sha,
    }
    assert remote_sha(repo, policy.branch) == current.state_sha
    assert remote_sha(repo, policy.previous_branch) == previous.state_sha


def test_repair_slots_fails_closed_without_a_valid_slot_or_exact_lease(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    attestation = tmp_path / "ancestry.json"
    repo.mkdir()
    code_sha = init_repo(repo)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", f"{code_sha}:refs/heads/{policy.branch}")
    git(repo, "push", "origin", f"{code_sha}:refs/heads/{policy.previous_branch}")
    write_repair_attestation(attestation, trusted_main_sha=code_sha, proofs={})

    with pytest.raises(state.DashboardStateError, match="neither dashboard state slot"):
        state.repair_state_slots(
            repo,
            policy,
            expected_current_sha=code_sha,
            expected_previous_sha=code_sha,
            trusted_main_sha=code_sha,
            ancestry_attestation=attestation,
        )
    assert remote_sha(repo, policy.branch) == code_sha
    assert remote_sha(repo, policy.previous_branch) == code_sha

    with pytest.raises(state.DashboardStateError, match="current state changed"):
        state.repair_state_slots(
            repo,
            policy,
            expected_current_sha=None,
            expected_previous_sha=code_sha,
            trusted_main_sha=code_sha,
            ancestry_attestation=attestation,
        )
    assert remote_sha(repo, policy.branch) == code_sha
    assert remote_sha(repo, policy.previous_branch) == code_sha


def test_repair_slots_requires_exact_bound_ancestry_proof(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    attestation = tmp_path / "ancestry.json"
    repo.mkdir()
    code_sha = init_repo(repo)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    valid = make_state(repo, policy, code_sha, generation="run-112.1", value=2)
    state.rotate_state_refs(
        repo,
        policy,
        new_state_sha=valid.state_sha,
        expected_current_sha=None,
        expected_previous_sha=None,
    )
    write_repair_attestation(attestation, trusted_main_sha=code_sha, proofs={})

    with pytest.raises(state.DashboardStateError, match="neither dashboard state slot"):
        state.repair_state_slots(
            repo,
            policy,
            expected_current_sha=valid.state_sha,
            expected_previous_sha=valid.state_sha,
            trusted_main_sha=code_sha,
            ancestry_attestation=attestation,
        )
    assert remote_sha(repo, policy.branch) == valid.state_sha
    assert remote_sha(repo, policy.previous_branch) == valid.state_sha


def init_existing_repo_contents(root: Path) -> str:
    (root / "scripts").mkdir()
    (root / "scripts/app.py").write_text("print('trusted')\n")
    (root / "data/vllm/ci").mkdir(parents=True)
    (root / "data/vllm/ci/current.json").write_text('{"value": 1}\n')
    (root / "dashboards").mkdir()
    (root / "dashboards/summary.md").write_text("old dashboard\n")
    (root / "README.md").write_text("old readme\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "code and frozen seed")
    return git(root, "rev-parse", "HEAD")


def remote_sha(root: Path, branch: str) -> str | None:
    output = git(root, "ls-remote", "--refs", "origin", f"refs/heads/{branch}")
    return output.split()[0] if output else None


def test_rotation_honors_post_cutover_bootstrap_gate(
    tmp_path: Path,
    policy: state.StatePolicy,
) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    code_sha = init_repo(repo)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    snapshot = make_state(repo, policy, code_sha, generation="run-109.1", value=2)

    with pytest.raises(state.DashboardStateError, match="bootstrap"):
        state.rotate_state_refs(
            repo,
            replace(policy, bootstrap_allowed=False),
            new_state_sha=snapshot.state_sha,
            expected_current_sha=None,
            expected_previous_sha=None,
        )

    with pytest.raises(state.DashboardStateError, match="ls-remote.*failed"):
        state.rotate_state_refs(
            repo,
            policy,
            new_state_sha=snapshot.state_sha,
            expected_current_sha=None,
            expected_previous_sha=None,
            remote="missing-remote",
        )
