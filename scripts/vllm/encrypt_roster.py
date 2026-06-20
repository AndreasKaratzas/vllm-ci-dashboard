#!/usr/bin/env python3
"""Encrypt the engineer roster with the admin's vault key.

Run this locally (never in CI) whenever ``ENGINEERS`` changes in
``scripts/vllm/engineers.py``. The output at
``data/vllm/ci/engineers.enc.json`` is an AES-GCM ciphertext that only the
admin's browser can decrypt — via ``docs/assets/js/token-vault.js`` after
the admin signs in and the vault derives its wrap key from their PAT.

Why we go through this dance
----------------------------
The dashboard is served publicly on gh-pages. ``ready_tickets.json`` used
to embed the roster verbatim ``{github_login, display_name}``. Even though
those fields are individually public on GitHub, the *association* — "this
is the AMD-internal vLLM triage team" — is PII we do not want to hand to
anyone who pulls the static site. The roster is only actually consumed by
the admin's assignee ``<select>``; gating it behind the admin's PAT leaves
non-admin viewers looking at an opaque blob they cannot decrypt.

Key derivation
--------------
Matches ``_deriveWrapKey`` in token-vault.js exactly:

    wrap_key = PBKDF2(pat, str(github_id) || b"|vault", 200_000, dklen=32)

The admin's ``github_id`` is read from ``users.json`` (``admin_id``).
``pat`` is prompted at runtime and verified against GitHub's ``/user``
endpoint, so a wrong token fails fast instead of shipping undecryptable
ciphertext.

Output format
-------------
Matches the record shape token-vault.js writes to sessionStorage::

    {"v": 1, "iv": <12-byte IV, hex>, "ct": <base64(ciphertext+tag)>}

WebCrypto's AES-GCM output and pyca/cryptography's ``AESGCM.encrypt`` both
emit ``ciphertext || tag`` in the same order, so the browser can decrypt
without any re-framing.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import secrets
import sys
from pathlib import Path

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.engineers import to_dict as engineers_to_dict  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
USERS = ROOT / "data" / "users.json"
OUT = ROOT / "data" / "vllm" / "ci" / "engineers.enc.json"
KDF_ITERATIONS = 200000


def _derive_wrap_key(pat: str, github_id: int) -> bytes:
    # Must match docs/assets/js/token-vault.js :: _deriveWrapKey exactly,
    # otherwise the browser will fail to decrypt.
    salt = f"{github_id}|vault".encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", pat.encode("utf-8"), salt, KDF_ITERATIONS, dklen=32)


def _verify_pat(pat: str, expected_id: int) -> None:
    r = requests.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"token {pat}",
            "Accept": "application/vnd.github+json",
        },
        timeout=15,
    )
    if r.status_code == 401:
        raise SystemExit("PAT rejected by GitHub (401). Regenerate the token and try again.")
    r.raise_for_status()
    me = r.json()
    if int(me.get("id", 0)) != int(expected_id):
        raise SystemExit(
            f"PAT belongs to @{me.get('login')} (id={me.get('id')}), "
            f"but users.json admin_id is {expected_id}. Use the admin's PAT."
        )


def main() -> int:
    if not USERS.exists():
        print(f"users.json not found at {USERS}", file=sys.stderr)
        return 1
    db = json.loads(USERS.read_text())
    admin_id = int(db.get("admin_id") or 0)
    if not admin_id:
        print(
            "users.json has no admin_id — set it to the admin's numeric GitHub id "
            "(e.g. 42451412 for @AndreasKaratzas) and re-run.",
            file=sys.stderr,
        )
        return 2

    pat = getpass.getpass(f"Admin PAT (for github_id={admin_id}): ")
    if not pat:
        print("No PAT entered; aborting.", file=sys.stderr)
        return 3
    _verify_pat(pat, admin_id)

    wrap_key = _derive_wrap_key(pat, admin_id)
    roster = engineers_to_dict()
    plaintext = json.dumps(roster, separators=(",", ":")).encode("utf-8")
    iv = secrets.token_bytes(12)
    ct = AESGCM(wrap_key).encrypt(iv, plaintext, None)

    record = {
        "v": 1,
        "iv": iv.hex(),
        "ct": base64.b64encode(ct).decode("ascii"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(record, indent=2) + "\n")
    print(f"Wrote encrypted roster ({len(roster)} entries) → {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
