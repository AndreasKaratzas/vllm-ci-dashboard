from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from vllm.ci import dns_state_crypto as crypto


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "vllm" / "ci" / "dns_state_crypto.py"


def test_authenticated_state_round_trip_is_randomized_and_private(tmp_path: Path):
    key = Fernet.generate_key()
    plaintext = b"\x1f\x8b sanitized state with negative-job coordinates"

    first = crypto.encrypt_state(plaintext, key)
    second = crypto.encrypt_state(plaintext, key)

    assert first != second
    assert plaintext not in first
    assert crypto.decrypt_state(first, key) == plaintext
    assert crypto.decrypt_state(second, key) == plaintext


def test_tamper_wrong_key_and_wrong_context_fail_closed():
    key = Fernet.generate_key()
    token = crypto.encrypt_state(b"state", key)
    tampered = token[:-2] + (b"A" if token[-2:-1] != b"A" else b"B") + token[-1:]

    with pytest.raises(crypto.StateCryptoError):
        crypto.decrypt_state(tampered, key)
    with pytest.raises(crypto.StateCryptoError):
        crypto.decrypt_state(token, Fernet.generate_key())
    wrong_context = Fernet(key).encrypt(b"another-protocol\0state")
    with pytest.raises(crypto.StateCryptoError):
        crypto.decrypt_state(wrong_context, key)


def test_transform_is_atomic_and_uses_private_permissions(tmp_path: Path):
    key = Fernet.generate_key()
    source = tmp_path / "state.json.gz"
    encrypted = tmp_path / "state.fernet"
    restored = tmp_path / "restored.json.gz"
    source.write_bytes(b"\x1f\x8b" + b"fixture")

    crypto.transform_file("encrypt", source, encrypted, key=key)
    crypto.transform_file("decrypt", encrypted, restored, key=key)

    assert restored.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(encrypted.stat().st_mode) == 0o600
    assert stat.S_IMODE(restored.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".*.tmp"))


def test_transform_rejects_symlinks_and_preserves_existing_output(tmp_path: Path):
    key = Fernet.generate_key()
    source = tmp_path / "source"
    source.write_bytes(b"state")
    symlink = tmp_path / "linked"
    symlink.symlink_to(source)
    output = tmp_path / "output"
    output.write_bytes(b"last-known-good")

    with pytest.raises(crypto.StateCryptoError):
        crypto.transform_file("encrypt", symlink, output, key=key)
    assert output.read_bytes() == b"last-known-good"


def test_cli_uses_environment_only_and_never_prints_sensitive_material(tmp_path: Path):
    source = tmp_path / "state"
    output = tmp_path / "encrypted"
    source.write_bytes(b"sensitive-state-marker")
    key = Fernet.generate_key().decode()
    environment = dict(os.environ, DNS_STATE_ENCRYPTION_KEY=key)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "encrypt", str(source), str(output)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert key not in result.stdout + result.stderr
    assert "sensitive-state-marker" not in result.stdout + result.stderr

    bad = subprocess.run(
        [sys.executable, str(SCRIPT), "decrypt", str(output), str(tmp_path / "bad")],
        cwd=ROOT,
        env=dict(os.environ, DNS_STATE_ENCRYPTION_KEY=Fernet.generate_key().decode()),
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad.returncode == 1
    assert key not in bad.stdout + bad.stderr
    assert "sensitive-state-marker" not in bad.stdout + bad.stderr


def test_cli_has_no_key_argument_and_missing_key_fails_before_output(tmp_path: Path):
    help_result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "--key" not in help_result.stdout

    source = tmp_path / "state"
    output = tmp_path / "encrypted"
    source.write_bytes(b"state")
    environment = dict(os.environ)
    environment.pop(crypto.KEY_ENV, None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "encrypt", str(source), str(output)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert not output.exists()
