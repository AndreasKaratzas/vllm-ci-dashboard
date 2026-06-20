#!/usr/bin/env python3
"""Scan the working tree for real-looking secrets.

Run locally::

    python scripts/vllm/secrets_scan.py

Run in CI (``.github/workflows/secrets-scan.yml``) on every push + PR.
Exits with a non-zero status if a match looks like a real credential, so
the PR is blocked before the secret lands on a default branch.

What it looks for
-----------------
* Known token prefixes with minimum length guards so we do not flag
  placeholder strings like ``ghp_...`` or ``bkua_...``:

    ``ghp_`` / ``gho_`` / ``ghu_`` / ``ghs_`` / ``ghr_`` — classic
    GitHub PATs. Real tokens are 36+ base62 chars after the prefix.

    ``github_pat_`` — fine-grained GitHub PATs. 50+ chars after prefix.

    ``bkua_`` — Buildkite API tokens. 40 hex chars after prefix.

    ``hf_`` — HuggingFace tokens. 34+ alphanumerics after prefix.

* Hash-like strings: 40+ consecutive hex chars outside of allowlisted
  paths (git lockfiles, benchmark data, and the hashed user entries in
  ``data/users.json``).

Allowlist
---------
We skip paths where long hex / prefixed placeholders are either
structural (git SHAs, vendored lockfiles) or already-hashed user
material (never plaintext). The allowlist is kept short on purpose —
when you add a new path that legitimately contains long hex, add it
here rather than weakening the regex.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Paths we NEVER scan. These either contain structural hex (commit SHAs,
# lockfile hashes) or already-hashed material that is safe by design.
PATH_ALLOWLIST = (
    ".git/",
    "data/",  # hashed user entries + CI data with commit SHAs
    "flake.lock",
    "node_modules/",
    "_site/",
    ".venv/",  # local dev virtualenv — third-party wheels carry SHA-1/256 blobs
    ".tox/",
    "__pycache__/",
    ".pytest_cache/",
    "scripts/vllm/secrets_scan.py",  # this file documents the patterns
    "tests/vllm/test_secrets_scan.py",  # test asserts on the patterns
    "tests/vllm/test_auth_and_token_isolation.py",  # same
    "tests/vllm/test_token_safety.py",  # same
)

# File suffixes we care about. Keep this to source code + config so we
# do not walk large JSON/CSV fixtures.
SCAN_SUFFIXES = (
    ".py", ".js", ".ts", ".mjs", ".cjs", ".html", ".css",
    ".yml", ".yaml", ".json", ".sh", ".toml", ".md",
)

# Known token prefixes. Each entry has a compiled regex and a label.
# Minimum tail lengths are tuned so that placeholders such as
# ``ghp_...``, ``ghp_…``, ``bkua_<token>`` do not trip the scanner.
TOKEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"), "GitHub PAT (classic)"),
    (re.compile(r"\bgho_[A-Za-z0-9]{36,}\b"), "GitHub OAuth token"),
    (re.compile(r"\bghu_[A-Za-z0-9]{36,}\b"), "GitHub user-to-server token"),
    (re.compile(r"\bghs_[A-Za-z0-9]{36,}\b"), "GitHub server-to-server token"),
    (re.compile(r"\bghr_[A-Za-z0-9]{36,}\b"), "GitHub refresh token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b"), "GitHub fine-grained PAT"),
    (re.compile(r"\bbkua_[a-f0-9]{40}\b"), "Buildkite API token"),
    (re.compile(r"\bhf_[A-Za-z0-9]{34,}\b"), "HuggingFace token"),
)

HASH_PATTERN = re.compile(r"\b[a-f0-9]{40,}\b")

# Lines containing any of these markers are expected to carry a long
# hex string (git SHA, checksum, etc.). We skip hash-like matches on
# those lines — the prefix patterns above still apply.
HASH_CONTEXT_HINTS = (
    "sha", "commit", "rev", "digest", "checksum", "integrity",
    "password_hash", "\"salt\"", "'salt'",
    "etag", "hash:", "oid:",
)


def _is_allowlisted(rel: str) -> bool:
    return any(rel == p or rel.startswith(p) for p in PATH_ALLOWLIST)


def _iter_candidate_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _is_allowlisted(rel):
            continue
        if path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        yield path, rel


def scan_text(text: str, rel: str) -> list[str]:
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for pat, label in TOKEN_PATTERNS:
            m = pat.search(line)
            if m:
                findings.append(
                    f"{rel}:{lineno}: {label}: {m.group(0)[:8]}… "
                    "(looks like a real token — use a placeholder or env var)"
                )
        # Hash-like hex: only flag if the line lacks a clear structural
        # hint. This keeps us from yelling about git SHAs in changelogs.
        lowered = line.lower()
        if any(h in lowered for h in HASH_CONTEXT_HINTS):
            continue
        for m in HASH_PATTERN.finditer(line):
            findings.append(
                f"{rel}:{lineno}: hash-like {len(m.group(0))}-char hex "
                f"({m.group(0)[:6]}…{m.group(0)[-4:]}) — add a context "
                "comment or allowlist this path"
            )
    return findings


def main() -> int:
    root = ROOT
    all_findings: list[str] = []
    for path, rel in _iter_candidate_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"WARN: could not read {rel}: {e}", file=sys.stderr)
            continue
        all_findings.extend(scan_text(text, rel))

    if all_findings:
        print("Secrets scan found suspicious matches:")
        for f in all_findings:
            print(f"  - {f}")
        print()
        print(
            "If a match is a placeholder or known-safe hash, either "
            "shorten the placeholder, add a HASH_CONTEXT_HINTS marker "
            "to the line, or add the path to PATH_ALLOWLIST in "
            "scripts/vllm/secrets_scan.py."
        )
        return 1

    print("No secrets detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
