from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest
import yaml

from vllm import capture_dashboard_candidate as capture
from vllm import dashboard_state as state


FAILURE = {
    "live_audit": {"outcome": "failure", "exit_code": 1},
    "deterministic_tests": {"outcome": "success", "exit_code": 0},
}


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def candidate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "0")
    root = tmp_path / "workspace"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Candidate Test")
    git(root, "config", "user.email", "candidate@example.com")
    git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_text(".cache/\n")
    (root / "trusted.py").write_text("print('trusted code')\n")
    (root / "data/vllm/ci").mkdir(parents=True)
    (root / "data/vllm/ci/health.json").write_text('{"generated_at":"2026-10-09T20:31:30Z"}\n')
    (root / "dashboards").mkdir()
    (root / "dashboards/audit.md").write_text("exact authored dashboard\n")
    (root / "README.md").write_text("exact generated readme\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "trusted code")
    code = git(root, "rev-parse", "HEAD")
    policy = state.StatePolicy(
        branch="dashboard-state", previous_branch="dashboard-state-previous",
        manifest_path="data/vllm/ci/dashboard_state.json",
        generated_roots=("data", "dashboards", "README.md"),
        max_blob_bytes=1024 * 1024, max_tree_bytes=8 * 1024 * 1024, max_files=100,
    )
    manifest = state.prepare_manifest(
        root, policy, code_sha=code, generation_id="hourly-123-1",
        generated_at="2026-10-09T20:38:12Z", source_refs={"queue-data": "a" * 40},
    )
    return root, policy, code, manifest, tmp_path / "diagnostic"


def take(candidate):
    root, policy, code, manifest, output = candidate
    return capture.capture_candidate(
        root, output, policy=policy, expected_code_sha=code,
        expected_generation_id=manifest["generation_id"],
        validation_outcomes=FAILURE,
    )


def test_exact_candidate_replays_all_generated_bytes_without_caches_or_clock_changes(candidate):
    root, policy, code, manifest, output = candidate
    private = root / "data/vllm/ci/.cache/raw.json"
    private.parent.mkdir()
    private.write_text('{"token":"must-never-enter-diagnostics"}')
    index = (root / ".git/index").read_bytes()
    before_refs = git(root, "show-ref")
    descriptor = take(candidate)
    assert capture.verify_capture(output, policy) == descriptor
    assert descriptor["diagnostic_only"] is True and descriptor["publication_accepted"] is False
    assert descriptor["code_sha"] == code
    assert descriptor["generated_at"] == manifest["generated_at"]
    assert descriptor["source_refs"] == manifest["source_refs"]
    assert descriptor["validation_outcomes"] == FAILURE
    assert (root / ".git/index").read_bytes() == index
    assert git(root, "show-ref") == before_refs
    assert output.stat().st_mode & 0o777 == 0o700
    for name in (capture.ARCHIVE_NAME, capture.DESCRIPTOR_NAME):
        assert (output / name).stat().st_mode & 0o777 == 0o600
    with tarfile.open(output / capture.ARCHIVE_NAME) as archive:
        assert set(archive.getnames()) == {*manifest["generated_files"], policy.manifest_path}
        for member in archive:
            assert member.mode == 0o600 and member.isfile()
            stream = archive.extractfile(member)
            assert stream is not None
            assert stream.read() == (root / member.name).read_bytes()
    assert b"must-never-enter-diagnostics" not in (output / capture.ARCHIVE_NAME).read_bytes()


@pytest.mark.parametrize("change", ["code", "generation", "unstaged", "manifest", "source-code", "symlink", "cache"])
def test_unproved_or_private_candidates_create_no_archive(candidate, change):
    root, policy, code, manifest, output = candidate
    expected_generation = manifest["generation_id"]
    if change == "code":
        code = "b" * 40
    elif change == "generation":
        expected_generation = "hourly-foreign-1"
    elif change == "unstaged":
        (root / "data/vllm/ci/health.json").write_text("changed after preparation")
    elif change == "manifest":
        manifest["generated_files"]["data/vllm/ci/health.json"]["sha256"] = "b" * 64
        (root / policy.manifest_path).write_bytes(state._canonical_manifest_bytes(manifest))
        git(root, "add", policy.manifest_path)
    elif change == "source-code":
        (root / "trusted.py").write_text("untrusted code")
        git(root, "add", "trusted.py")
    else:
        path = root / ("data/vllm/ci/.cache/raw.json" if change == "cache" else "data/link.json")
        path.parent.mkdir(exist_ok=True)
        if change == "cache":
            path.write_text('{"token":"private"}')
            git(root, "add", "-f", str(path.relative_to(root)))
            state.prepare_manifest(root, policy, code_sha=code, generation_id=expected_generation,
                                   generated_at=manifest["generated_at"])
        else:
            path.symlink_to(root / "trusted.py")
            git(root, "add", str(path.relative_to(root)))
    with pytest.raises(state.DashboardStateError):
        capture.capture_candidate(root, output, policy=policy, expected_code_sha=code,
                                  expected_generation_id=expected_generation, validation_outcomes=FAILURE)
    assert not output.exists()


def test_capture_failure_preserves_existing_output_and_removes_partial_stage(candidate, monkeypatch):
    root, policy, code, manifest, output = candidate
    monkeypatch.setattr(capture, "verify_capture", lambda *_: (_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError):
        take(candidate)
    assert not output.exists()
    assert not list(output.parent.glob("candidate-diagnostic-*"))
    output.mkdir()
    (output / "existing").write_bytes(b"preserve")
    with pytest.raises(state.DashboardStateError):
        take(candidate)
    assert (output / "existing").read_bytes() == b"preserve"
    with pytest.raises(state.DashboardStateError):
        capture.capture_candidate(root, root / "data/capture", policy=policy,
                                  expected_code_sha=code, expected_generation_id=manifest["generation_id"],
                                  validation_outcomes=FAILURE)


def test_archive_tamper_is_rejected_even_when_outer_checksum_is_recomputed(candidate):
    _, policy, _, _, output = candidate
    take(candidate)
    path = output / capture.ARCHIVE_NAME
    with tarfile.open(path) as archive:
        rows = [(member.name, archive.extractfile(member).read()) for member in archive]  # type: ignore[union-attr]
    with tarfile.open(path, "w") as archive:
        for name, payload in rows:
            if name.endswith("health.json"):
                payload = b"tampered bytes"
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(payload), 0o600
            archive.addfile(item, io.BytesIO(payload))
    descriptor_path = output / capture.DESCRIPTOR_NAME
    descriptor = json.loads(descriptor_path.read_bytes())
    descriptor["archive"].update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    descriptor_path.write_text(json.dumps(descriptor))
    with pytest.raises(state.DashboardStateError, match="byte proof"):
        capture.verify_capture(output, policy)


def test_capture_keeps_repository_storage_envelope(candidate):
    root, policy, code, manifest, output = candidate
    with pytest.raises(state.DashboardStateError):
        capture.capture_candidate(root, output, policy=replace(policy, max_tree_bytes=100),
                                  expected_code_sha=code, expected_generation_id=manifest["generation_id"],
                                  validation_outcomes=FAILURE)
    assert not output.exists()


@pytest.mark.parametrize("proof", [
    {"live_audit": {"outcome": "success", "exit_code": 0},
     "deterministic_tests": {"outcome": "success", "exit_code": 0}},
    {"live_audit": {"outcome": "success", "exit_code": 0},
     "deterministic_tests": {"outcome": "skipped", "exit_code": None}},
    {"live_audit": {"outcome": "failure", "exit_code": True},
     "deterministic_tests": {"outcome": "success", "exit_code": 0}},
    {"live_audit": {"outcome": "failure", "exit_code": "1"},
     "deterministic_tests": {"outcome": "success", "exit_code": 0}},
    {"live_audit": {"outcome": "failure", "exit_code": 256},
     "deterministic_tests": {"outcome": "success", "exit_code": 0}},
])
def test_healthy_or_malformed_validation_proof_never_creates_archive(candidate, proof):
    root, policy, code, manifest, output = candidate
    with pytest.raises(state.DashboardStateError):
        capture.capture_candidate(root, output, policy=policy, expected_code_sha=code,
                                  expected_generation_id=manifest["generation_id"],
                                  validation_outcomes=proof)
    assert not output.exists()
    assert not list(output.parent.glob("candidate-diagnostic-*"))


def test_workflow_captures_before_enforcement_with_no_token_or_publication_side_effect():
    workflow = yaml.safe_load(Path(".github/workflows/hourly-master.yml").read_text())
    steps = workflow["jobs"]["collect-and-deploy"]["steps"]
    names = [step.get("name") for step in steps]
    capture_step = steps[names.index("Capture exact collected candidate for offline diagnostics")]
    upload = steps[names.index("Upload exact collected candidate diagnostics")]
    assert names.index("Prepare bounded dashboard state candidate") < names.index(capture_step["name"])
    assert names.index("Live publication audit") < names.index(capture_step["name"])
    assert names.index("Run test suite") < names.index(capture_step["name"])
    assert names.index(upload["name"]) < names.index("Enforce publication validation results")
    assert capture_step["continue-on-error"] is True and upload["continue-on-error"] is True
    assert "always()" in capture_step["if"] and "steps.state-candidate.outcome == 'success'" in capture_step["if"]
    assert "steps.live-data-audit.outcome == 'failure'" in capture_step["if"]
    assert "steps.run-tests.outputs.exit_code != '0'" in capture_step["if"]
    assert "BUILDKITE" not in str(capture_step.get("env", {}))
    assert upload["with"]["overwrite"] is False
    assert upload["with"]["retention-days"] == 1
    assert "candidate.tar" in upload["with"]["path"]
