#!/usr/bin/env python3
"""Encrypt the kill-job proof sentinel with the admin's Buildkite token.

Run this locally (never in CI) to seed ``data/vllm/ci/kill_auth.enc.json``.
The file is AES-GCM ciphertext of the ASCII string ``"1"``, keyed by a
PBKDF2 derivation of the admin's Buildkite API token. Only someone who
knows the same Buildkite token can decrypt the file back to ``"1"`` — the
Queue tab's "Kill stuck job" flow requires that proof before it will call
Buildkite's cancel API.

Why a proof-of-possession ciphertext instead of a plain password prompt
----------------------------------------------------------------------
The kill action fires ``PUT api.buildkite.com/.../builds/{N}/cancel`` with
a user-typed token. Without a pre-committed proof, any viewer of the
dashboard could enter an arbitrary Buildkite token and get the UI to
send a destructive API call with it — useful to anyone who just stole a
token, and a footgun for anyone who typos a different org's token.

With the proof, we refuse to dispatch the cancel unless the decryption
of ``kill_auth.enc.json`` matches the sentinel ``"1"``. That binds the
UI's "armed" state to the specific token the admin authorized during
encryption, not to "any token that looks valid to Buildkite".

Key derivation
--------------
Mirrors the JS side (``_deriveKillAuthKey`` in ci-queue.js):

    key = PBKDF2(bk_token, b"vllm-ci-dashboard|kill-auth", 200_000, 32)

The salt is a fixed domain separator, not secret — the whole protection
comes from the token itself being the PBKDF2 input. Iteration count
matches the existing vault so the KDF budget stays uniform.

Output format
-------------
Matches the ``{v, iv, ct}`` envelope used elsewhere in the dashboard
(token-vault, encrypt_roster). AES-GCM outputs ``ciphertext || tag``
concatenated; WebCrypto consumes the same layout, so no re-framing.

Env / args
----------
BUILDKITE_TOKEN  if set, skip the interactive prompt (still verified).
--skip-verify    do not call Buildkite; for offline re-encryption with a
                 known-good token. Prefer the verified path.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "data" / "vllm" / "ci" / "kill_auth.enc.json"

# Fixed salt string — keep byte-for-byte identical to the JS derivation
# in docs/assets/js/ci-queue.js. Changing this on either side makes every
# previously-committed kill_auth.enc.json undecryptable.
KDF_SALT = b"vllm-ci-dashboard|kill-auth"
KDF_ITERATIONS = 200_000
SENTINEL = "1"


def _derive_key(bk_token: str) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", bk_token.encode("utf-8"), KDF_SALT, KDF_ITERATIONS, dklen=32
    )


def _verify_token(bk_token: str) -> dict:
    """Ping Buildkite's access-token endpoint — fails fast on bad tokens.

    A 401 here means we'd ship an undecryptable file: the browser would
    prompt the admin for the token, derive the same PBKDF2 key, decrypt
    successfully (any valid AES-GCM key decrypts some plaintext), but
    Buildkite would 401 when the kill actually fires. Verifying up front
    is one extra HTTP call that prevents that scenario.
    """
    r = requests.get(
        "https://api.buildkite.com/v2/access-token",
        headers={"Authorization": f"Bearer {bk_token}"},
        timeout=15,
    )
    if r.status_code == 401:
        raise SystemExit(
            "Buildkite rejected the token (401). Generate a new one at "
            "https://buildkite.com/user/api-access-tokens and try again."
        )
    r.raise_for_status()
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skip-verify", action="store_true",
                    help="Skip the Buildkite verification HTTP call.")
    args = ap.parse_args()

    token = os.environ.get("BUILDKITE_TOKEN") or ""
    if not token:
        token = getpass.getpass("Buildkite API token: ")
    token = token.strip()
    if not token:
        print("No token entered; aborting.", file=sys.stderr)
        return 3

    if not args.skip_verify:
        info = _verify_token(token)
        # Surface the token's scope so the admin notices if it's missing
        # ``write_builds`` — without that scope the cancel call will
        # later 403 even though the ciphertext decrypts fine.
        scopes = info.get("scopes") or []
        if "write_builds" not in scopes:
            print(
                "WARNING: this token does not carry the ``write_builds`` scope; "
                "cancel calls will 403 at kill time. Re-issue with that scope.",
                file=sys.stderr,
            )

    key = _derive_key(token)
    iv = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(iv, SENTINEL.encode("utf-8"), None)

    record = {
        "v": 1,
        "iv": iv.hex(),
        "ct": base64.b64encode(ct).decode("ascii"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(record, indent=2) + "\n")
    print(f"Wrote kill-auth proof ciphertext → {OUT.relative_to(ROOT)}")
    print(
        "Commit this file; the dashboard reads it to gate the Queue tab's "
        "kill-stuck-job button on proof-of-token-possession."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
