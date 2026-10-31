"""Exercise DNS publication shells with synthetic crypto and local Git only."""

import gzip
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from cryptography.fernet import Fernet

from vllm.ci.dns_state_crypto import decrypt_state, encrypt_state

ROOT = Path(__file__).resolve().parents[2]
STATE = Path("data/vllm/ci/dns_health/scan_state.json.gz")
CIPHER = Path("data/vllm/ci/dns_health/scan_state.fernet")
PUBLIC = Path("data/vllm/ci/dns_failures.json")
GENERATION = "2026-10-12T16:05:24Z"


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True, timeout=10).stdout.strip()


def _write(root, path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _fixture(tmp_path, *, bootstrap=False, missing_public=False, corrupt_public=False, corrupt_cipher=False):
    root = tmp_path / "checkout"
    root.mkdir()
    remote = tmp_path / "remote.git"
    runtime = tmp_path / "runner-temp"
    runtime.mkdir()
    _git(root, "init")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "user.name", "DNS Fixture")
    _git(root, "config", "user.email", "dns-fixture@example.invalid")
    _git(root, "init", "--bare", str(remote))
    _git(root, "remote", "add", "origin", str(remote))
    script = Path("scripts/vllm/ci/dns_state_crypto.py")
    _write(root, script, (ROOT / script).read_bytes())
    _git(root, "add", ".")
    _git(root, "commit", "-m", "Synthetic code")
    key = Fernet.generate_key().decode("ascii")
    state = gzip.compress(b'{"synthetic":"original"}\n', mtime=0)
    public = json.dumps({"generated_at": GENERATION, "synthetic": "original"}).encode() + b"\n"
    cipher = encrypt_state(state, key)
    if not bootstrap:
        source = tmp_path / "seed"
        _git(root, "worktree", "add", "--detach", str(source), "HEAD")
        _git(source, "switch", "--orphan", "fixture-dns-source")
        _git(source, "rm", "-rf", "--ignore-unmatch", ".")
        _write(source, CIPHER, b"invalid authenticated token" if corrupt_cipher else cipher)
        if not missing_public:
            _write(source, PUBLIC, b"invalid public JSON\n" if corrupt_public else public)
        _git(source, "add", ".")
        _git(source, "commit", "-m", "Synthetic parentless DNS source")
        _git(source, "push", "origin", "HEAD:refs/heads/dns-health-data")
        _git(root, "worktree", "remove", "--force", str(source))
    workflow = yaml.safe_load((ROOT / ".github/workflows/dns-health.yml").read_text())
    steps = {step.get("name"): step for step in workflow["jobs"]["collect"]["steps"]}
    env = {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
           "LANG": "C.UTF-8", "RUNNER_TEMP": str(runtime), "GITHUB_WORKSPACE": str(ROOT),
           "DNS_STATE_ENCRYPTION_KEY": key}
    return root, remote, runtime, steps, env, state, public, cipher


def _run(root, steps, env, name, *, check=True):
    return subprocess.run(["bash", "-e", "-c", steps[name]["run"]], cwd=root, env=env,
                          capture_output=True, timeout=15, check=check)


def _ref(root):
    raw = _git(root, "ls-remote", "--refs", "origin", "refs/heads/dns-health-data")
    return raw.split()[0].decode() if raw else None


@pytest.mark.parametrize("case", ["unchanged", "private-change", "public-change", "bootstrap", "missing-public", "corrupt-public"])
def test_dns_publication_reuses_only_identical_authenticated_evidence(tmp_path, case):
    root, remote, runtime, steps, env, state, public, cipher = _fixture(
        tmp_path, bootstrap=case == "bootstrap", missing_public=case == "missing-public",
        corrupt_public=case == "corrupt-public")
    before = _ref(root)
    _run(root, steps, env, "Resolve durable DNS scanner state")
    if case == "private-change":
        state = gzip.compress(b'{"synthetic":"changed"}\n', mtime=0)
    if case == "public-change":
        public = json.dumps({"generated_at": GENERATION, "synthetic": "repaired"}).encode() + b"\n"
    _write(root, STATE, state)
    _write(root, PUBLIC, public)
    baseline = runtime / "dns-health-publication-baseline"
    if case != "bootstrap":
        assert (baseline / "scan_state.fernet").stat().st_mode & 0o777 == 0o600
        assert (baseline / "scan_state.json.gz").stat().st_mode & 0o777 == 0o600
    _run(root, steps, env, "Encrypt durable DNS scanner state")
    assert not (root / STATE).exists()
    assert not (baseline / "scan_state.json.gz").exists()
    emitted = (runtime / "dns-health-scan-state.fernet").read_bytes()
    assert decrypt_state(emitted, env["DNS_STATE_ENCRYPTION_KEY"]) == state
    if case not in {"private-change", "bootstrap"}:
        assert hashlib.sha256(emitted).hexdigest() == hashlib.sha256(cipher).hexdigest()
    else:
        assert hashlib.sha256(emitted).hexdigest() != hashlib.sha256(cipher).hexdigest()
    result = _run(root, steps, env, "Publish durable DNS evidence")
    after = _ref(root)
    assert (after == before) is (case == "unchanged")
    assert bool(b"preserving the durable evidence commit" in result.stdout) is (case == "unchanged")
    stored_public = _git(root, "--git-dir", str(remote), "show", f"{after}:{PUBLIC}")
    assert stored_public == public.strip()
    stored_cipher = _git(root, "--git-dir", str(remote), "show", f"{after}:{CIPHER}")
    assert hashlib.sha256(stored_cipher).hexdigest() == hashlib.sha256(emitted).hexdigest()
    assert len(_git(root, "--git-dir", str(remote), "rev-list", "--parents", "-n", "1", after).split()) == 1
    _run(root, steps, env, "Remove private DNS plaintext")


def test_dns_restore_authentication_failure_hard_fails_without_plaintext_or_push(tmp_path):
    root, _, runtime, steps, env, _, _, _ = _fixture(tmp_path, corrupt_cipher=True)
    before = _ref(root)
    _write(root, STATE, b"stale synthetic plaintext")
    result = _run(root, steps, env, "Resolve durable DNS scanner state", check=False)
    assert result.returncode != 0
    assert _ref(root) == before
    assert not (root / STATE).exists()
    assert not (runtime / "dns-health-publication-baseline/scan_state.json.gz").exists()
    assert not (runtime / "dns-health-publication-baseline/scan_state.fernet").exists()


def test_dns_failed_encryption_always_removes_current_and_baseline_plaintext(tmp_path):
    root, _, runtime, steps, env, _, _, _ = _fixture(tmp_path)
    before = _ref(root)
    _run(root, steps, env, "Resolve durable DNS scanner state")
    _write(root, STATE, gzip.compress(b'{"synthetic":"changed"}\n', mtime=0))
    result = _run(root, steps, dict(env, DNS_STATE_ENCRYPTION_KEY=""), "Encrypt durable DNS scanner state", check=False)
    assert result.returncode != 0
    assert _ref(root) == before
    assert not (root / STATE).exists()
    assert not (runtime / "dns-health-publication-baseline/scan_state.json.gz").exists()


def test_dns_cleanup_runs_after_failed_collection_without_changing_generation_gates(tmp_path):
    root, _, runtime, steps, env, _, _, _ = _fixture(tmp_path)
    _run(root, steps, env, "Resolve durable DNS scanner state")
    assert steps["Remove private DNS plaintext"]["if"] == "always()"
    _run(root, steps, env, "Remove private DNS plaintext")
    assert not (root / STATE).exists()
    assert not (runtime / "dns-health-publication-baseline/scan_state.json.gz").exists()
    generation = steps["Capture validated DNS generation"]
    assert generation["id"] == "dns-generation"
    assert "generated_at=$DNS_GENERATED_AT" in generation["run"]
    assert "steps.collect-dns.outcome == 'success'" in generation["if"]
    workflow = yaml.safe_load((ROOT / ".github/workflows/dns-health.yml").read_text())
    assert workflow["jobs"]["collect"]["outputs"]["dns_generation"] == "${{ steps.dns-generation.outputs.generated_at }}"
    assert workflow["jobs"]["reconcile-publication"]["if"] == "needs.collect.outputs.dns_generation != ''"
