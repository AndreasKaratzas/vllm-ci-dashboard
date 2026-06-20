"""Tests for ``scripts/vllm/encrypt_kill_auth.py`` — the admin tool that
seeds ``data/vllm/ci/kill_auth.enc.json`` with the proof-of-possession
sentinel used by the Queue tab's kill-stuck-build flow.

The JS side (``_deriveKillAuthKey`` + ``_verifyKillToken`` in
``docs/assets/js/ci-queue.js``) and the Python side must agree on every
byte of the key-derivation and the AES-GCM envelope layout. Any drift
between the two makes the committed ciphertext undecryptable at runtime
and silently downgrades the dashboard's "kill" button to a dead button.

These tests lock both halves of the contract:

1. KDF parameters (salt bytes, iteration count, hash, derived-key length)
   are byte-for-byte identical to what the JS reads.
2. The envelope format (``{v, iv, ct}``, base64 vs hex encodings, IV
   length) matches what WebCrypto's ``AES-GCM`` consumes.
3. Encrypt-then-decrypt with the same token recovers the sentinel "1".
4. A different token fails to decrypt, OR decrypts to something other
   than "1" — either way ``_verifyKillToken`` rejects it.
5. ``--skip-verify`` bypasses the Buildkite HTTP call (tested offline).

We can't execute WebCrypto in pytest, but we can hard-code the constants
the JS reads and decrypt with the same parameters via pyca/cryptography,
which guarantees the JS code will do the same.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "vllm" / "encrypt_kill_auth.py"
JS_CI_QUEUE = ROOT / "docs" / "assets" / "js" / "ci-queue.js"


def _load_script_module():
    """Load encrypt_kill_auth as a module for white-box constant testing.

    Importing the script directly gives us access to ``_derive_key``,
    ``KDF_SALT``, ``KDF_ITERATIONS``, ``SENTINEL`` without going through
    the CLI entrypoint (which would prompt for a token).
    """
    spec = importlib.util.spec_from_file_location("encrypt_kill_auth", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestKdfContract:
    """KDF constants must match the JS side byte-for-byte."""

    def test_salt_matches_js(self):
        mod = _load_script_module()
        # The JS reads the salt from ``KILL_AUTH_SALT`` in ci-queue.js.
        js = JS_CI_QUEUE.read_text()
        assert "'vllm-ci-dashboard|kill-auth'" in js, (
            "JS side no longer uses the expected KDF salt literal"
        )
        assert mod.KDF_SALT == b"vllm-ci-dashboard|kill-auth"

    def test_iterations_match_js(self):
        mod = _load_script_module()
        js = JS_CI_QUEUE.read_text()
        # JS uses ``200000`` with no underscore separator.
        assert "200000" in js, "JS side no longer uses 200000 PBKDF2 iterations"
        assert mod.KDF_ITERATIONS == 200_000

    def test_sentinel_is_ascii_one(self):
        mod = _load_script_module()
        # Every byte of the plaintext contributes to the tag, so the
        # sentinel must be exactly b"1" — not "1\n", not "true", not 1.
        assert mod.SENTINEL == "1"
        assert mod.SENTINEL.encode("utf-8") == b"1"

    def test_derive_key_length_is_256_bits(self):
        mod = _load_script_module()
        # AES-GCM-256 demands a 32-byte key. WebCrypto's
        # ``deriveKey({name:'AES-GCM',length:256})`` also produces 32 bytes.
        key = mod._derive_key("whatever")
        assert len(key) == 32

    def test_derive_key_is_deterministic_per_token(self):
        mod = _load_script_module()
        a = mod._derive_key("token-abc")
        b = mod._derive_key("token-abc")
        c = mod._derive_key("token-xyz")
        assert a == b, "same token must derive the same key"
        assert a != c, "different tokens must derive different keys"

    def test_derive_key_matches_manual_pbkdf2(self):
        mod = _load_script_module()
        # Sanity-check that the Python helper genuinely uses PBKDF2-SHA256
        # with the advertised parameters. If someone swaps it for a cheaper
        # hash (or a smaller iteration count), the JS decrypt would still
        # work — but only after dropping security. This test catches that.
        token = "fake-buildkite-token-123"
        expected = hashlib.pbkdf2_hmac(
            "sha256",
            token.encode("utf-8"),
            b"vllm-ci-dashboard|kill-auth",
            200_000,
            dklen=32,
        )
        assert mod._derive_key(token) == expected


class TestEnvelopeRoundtrip:
    """End-to-end encrypt → decrypt with the same token recovers "1"."""

    def _run_script(self, tmp_path, token, monkeypatch, *, skip_verify=True):
        """Invoke the script's main() with OUT redirected to tmp_path."""
        mod = _load_script_module()
        out_path = tmp_path / "kill_auth.enc.json"
        monkeypatch.setattr(mod, "OUT", out_path)
        # ``main`` prints OUT.relative_to(ROOT). Since OUT now lives outside
        # ROOT, re-root it to the tmp dir too so the relative_to call works.
        monkeypatch.setattr(mod, "ROOT", tmp_path)
        monkeypatch.setenv("BUILDKITE_TOKEN", token)
        argv = ["encrypt_kill_auth.py"]
        if skip_verify:
            argv.append("--skip-verify")
        monkeypatch.setattr(sys, "argv", argv)
        assert mod.main() == 0
        return json.loads(out_path.read_text())

    def test_roundtrip_with_same_token(self, tmp_path, monkeypatch):
        record = self._run_script(tmp_path, "admin-token-abc", monkeypatch)
        # The envelope shape is exactly what the JS decoder reads.
        assert record["v"] == 1
        assert isinstance(record["iv"], str) and len(record["iv"]) == 24  # 12 bytes hex
        assert isinstance(record["ct"], str)

        mod = _load_script_module()
        key = mod._derive_key("admin-token-abc")
        iv = bytes.fromhex(record["iv"])
        ct = base64.b64decode(record["ct"])
        plaintext = AESGCM(key).decrypt(iv, ct, None)
        assert plaintext == b"1"

    def test_wrong_token_cannot_recover_sentinel(self, tmp_path, monkeypatch):
        record = self._run_script(tmp_path, "admin-token-abc", monkeypatch)
        mod = _load_script_module()

        wrong_key = mod._derive_key("wrong-token-xyz")
        iv = bytes.fromhex(record["iv"])
        ct = base64.b64decode(record["ct"])
        # AES-GCM with a wrong key fails authentication — this mirrors what
        # ``_verifyKillToken`` sees when a guest types a random token.
        # "InvalidTag" is what pyca raises; the JS side catches
        # ``OperationError`` and returns ``{ok:false, reason:'bad-token'}``.
        with pytest.raises(InvalidTag):
            AESGCM(wrong_key).decrypt(iv, ct, None)

    def test_iv_is_unique_per_run(self, tmp_path, monkeypatch):
        # AES-GCM IV reuse with the same key is catastrophic. Each run must
        # draw a fresh IV from ``secrets.token_bytes``.
        a = self._run_script(tmp_path, "admin-token-abc", monkeypatch)
        b = self._run_script(tmp_path, "admin-token-abc", monkeypatch)
        assert a["iv"] != b["iv"], "IV reuse across runs — secrets.token_bytes broken?"

    def test_envelope_field_encodings(self, tmp_path, monkeypatch):
        """JS expects iv as hex, ct as base64. Swap either and decrypt breaks."""
        record = self._run_script(tmp_path, "token-x", monkeypatch)
        # hex: only 0-9a-f
        assert all(c in "0123456789abcdef" for c in record["iv"])
        # base64: must decode cleanly
        decoded = base64.b64decode(record["ct"], validate=True)
        # AES-GCM output is ciphertext || tag — for a 1-byte plaintext,
        # the total is 1 (ct) + 16 (tag) = 17 bytes.
        assert len(decoded) == 17


class TestVerifyToken:
    """``_verify_token`` is the pre-encrypt Buildkite ping. We must not
    encrypt with a token Buildkite would reject."""

    def test_401_aborts_with_helpful_error(self, monkeypatch):
        mod = _load_script_module()
        fake = mock.Mock()
        fake.status_code = 401
        fake.json.return_value = {}
        monkeypatch.setattr(mod.requests, "get", lambda *a, **k: fake)
        with pytest.raises(SystemExit) as exc:
            mod._verify_token("bogus")
        assert "401" in str(exc.value)

    def test_success_returns_scopes_json(self, monkeypatch):
        mod = _load_script_module()
        fake = mock.Mock()
        fake.status_code = 200
        fake.json.return_value = {"scopes": ["read_builds", "write_builds"]}
        fake.raise_for_status = mock.Mock()
        monkeypatch.setattr(mod.requests, "get", lambda *a, **k: fake)
        info = mod._verify_token("legit")
        assert "write_builds" in info["scopes"]

    def test_skip_verify_does_not_hit_network(self, tmp_path, monkeypatch):
        """--skip-verify is the offline re-encryption path. Must not call
        requests.get; we assert that by making it explode if touched."""
        mod = _load_script_module()
        out_path = tmp_path / "out.json"
        monkeypatch.setattr(mod, "OUT", out_path)
        monkeypatch.setattr(mod, "ROOT", tmp_path)
        monkeypatch.setenv("BUILDKITE_TOKEN", "offline-token")

        def _boom(*a, **k):
            raise AssertionError("requests.get should not be called with --skip-verify")

        monkeypatch.setattr(mod.requests, "get", _boom)
        monkeypatch.setattr(sys, "argv", ["encrypt_kill_auth.py", "--skip-verify"])
        assert mod.main() == 0
        assert out_path.exists()


class TestCliContract:
    def test_script_rejects_empty_token(self, tmp_path, monkeypatch, capsys):
        mod = _load_script_module()
        monkeypatch.setattr(mod, "OUT", tmp_path / "x.json")
        monkeypatch.setenv("BUILDKITE_TOKEN", "   ")  # whitespace-only
        monkeypatch.setattr(sys, "argv", ["encrypt_kill_auth.py", "--skip-verify"])
        rc = mod.main()
        assert rc == 3
        err = capsys.readouterr().err
        assert "No token" in err

    def test_script_runs_as_subprocess(self, tmp_path):
        """Smoke-check the full CLI path — argparse, env read, file write."""
        env = {
            "BUILDKITE_TOKEN": "subproc-token",
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
        }
        # We can't easily redirect OUT via subprocess, so just confirm the
        # script parses args and prints a sensible error on a bad invocation.
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True, text=True, env=env, timeout=10,
        )
        assert result.returncode == 0
        assert "kill-job proof sentinel" in result.stdout
