#!/usr/bin/env python3
# cspell:ignore fchmod
"""Encrypt the durable DNS scanner ledger before it leaves an Actions runner.

The repository and its orphan data branch are public.  This helper therefore
wraps the sanitized-but-nonpublic scanner ledger in authenticated encryption.
The key is accepted only through ``DNS_STATE_ENCRYPTION_KEY`` and error output
never includes key material, ciphertext, or decrypted content.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


KEY_ENV = "DNS_STATE_ENCRYPTION_KEY"
PLAINTEXT_CONTEXT = b"vllm-ci-dns-scan-state-v1\0"
# A 63 MiB plaintext plus Fernet framing/base64 is at most 88,080,504
# bytes. Keep both sides of the transform beneath the repository's common
# 85 MiB Git-blob guard (and therefore beneath 90,000,000 decimal bytes).
MAX_PLAINTEXT_BYTES = 63 * 1024 * 1024
MAX_CIPHERTEXT_BYTES = 85 * 1024 * 1024


class StateCryptoError(RuntimeError):
    """The encrypted DNS state could not be processed safely."""


def _fernet(key: str | bytes) -> Fernet:
    try:
        encoded = key.encode("ascii") if isinstance(key, str) else bytes(key)
        return Fernet(encoded)
    except (UnicodeEncodeError, ValueError, TypeError):
        raise StateCryptoError("invalid encryption key") from None


def encrypt_state(plaintext: bytes, key: str | bytes) -> bytes:
    """Return a randomized, authenticated token bound to this state format."""
    if not isinstance(plaintext, bytes) or not plaintext:
        raise StateCryptoError("state plaintext must be non-empty bytes")
    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise StateCryptoError("state plaintext exceeds the bounded size")
    ciphertext = _fernet(key).encrypt(PLAINTEXT_CONTEXT + plaintext)
    if len(ciphertext) > MAX_CIPHERTEXT_BYTES:
        raise StateCryptoError("state ciphertext exceeds the bounded size")
    return ciphertext


def decrypt_state(ciphertext: bytes, key: str | bytes) -> bytes:
    """Authenticate and decrypt one DNS state token."""
    if not isinstance(ciphertext, bytes) or not ciphertext:
        raise StateCryptoError("state ciphertext must be non-empty bytes")
    if len(ciphertext) > MAX_CIPHERTEXT_BYTES:
        raise StateCryptoError("state ciphertext exceeds the bounded size")
    try:
        decoded = _fernet(key).decrypt(ciphertext)
    except InvalidToken:
        raise StateCryptoError("state ciphertext authentication failed") from None
    if not decoded.startswith(PLAINTEXT_CONTEXT):
        raise StateCryptoError("state ciphertext has the wrong context")
    plaintext = decoded[len(PLAINTEXT_CONTEXT) :]
    if not plaintext:
        raise StateCryptoError("decrypted state is empty")
    return plaintext


def _read_regular_file(path: Path, *, max_bytes: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError:
        raise StateCryptoError("state input is unavailable") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise StateCryptoError("state input must be a regular file")
    if metadata.st_size <= 0 or metadata.st_size > max_bytes:
        raise StateCryptoError("state input size is invalid")
    try:
        return path.read_bytes()
    except OSError:
        raise StateCryptoError("state input could not be read") from None


def _write_private_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def transform_file(
    operation: str,
    input_path: Path,
    output_path: Path,
    *,
    key: str | bytes,
) -> None:
    """Encrypt or decrypt a file without ever writing partial output."""
    if operation == "encrypt":
        source = _read_regular_file(input_path, max_bytes=MAX_PLAINTEXT_BYTES)
        result = encrypt_state(source, key)
    elif operation == "decrypt":
        source = _read_regular_file(input_path, max_bytes=MAX_CIPHERTEXT_BYTES)
        result = decrypt_state(source, key)
    else:
        raise StateCryptoError("unsupported state operation")
    try:
        _write_private_file(output_path, result)
    except OSError:
        raise StateCryptoError("state output could not be written") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("encrypt", "decrypt"))
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    key = os.environ.get(KEY_ENV, "")
    if not key:
        print("DNS state encryption key is unavailable", file=sys.stderr)
        return 2
    try:
        transform_file(args.operation, args.input, args.output, key=key)
    except StateCryptoError:
        print("DNS state cryptographic operation failed safely", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
