"""Tests for ``scripts/vllm/secrets_scan.py``.

The scanner runs in ``.github/workflows/secrets-scan.yml`` on every push
and PR. These tests pin its behavior on known-good / known-bad inputs so
a future refactor cannot accidentally widen the placeholder allowlist or
shrink the real-token detection.
"""

from __future__ import annotations

import pytest

from vllm import secrets_scan as ss


class TestTokenPatterns:
    @pytest.mark.parametrize("line,label", [
        ("token = 'ghp_" + "A" * 36 + "'", "GitHub PAT (classic)"),
        ("const x = 'gho_" + "B" * 40 + "'", "GitHub OAuth token"),
        ("ghs_" + "C" * 40, "GitHub server-to-server token"),
        ("github_pat_" + "D" * 60, "GitHub fine-grained PAT"),
        ("bkua_" + "0" * 40, "Buildkite API token"),
        ("hf_" + "E" * 40, "HuggingFace token"),
    ])
    def test_flags_real_looking_tokens(self, line, label):
        findings = ss.scan_text(line, "demo.py")
        assert findings, f"scanner missed {label}: {line[:16]}…"
        assert label in findings[0]

    @pytest.mark.parametrize("line", [
        "placeholder = 'ghp_...'",
        "placeholder = 'ghp_…'",
        "placeholder = 'bkua_...'",
        "placeholder = 'hf_...'",
        "export GITHUB_TOKEN=\"ghp_...\"  # docstring example",
        "export BUILDKITE_TOKEN=\"bkua_...\"",
    ])
    def test_ignores_placeholders(self, line):
        assert ss.scan_text(line, "demo.py") == []


class TestHashLike:
    def test_flags_bare_hash(self):
        # 64-char hex with no context hint → flagged.
        line = "x = '" + "a" * 64 + "'"
        findings = ss.scan_text(line, "demo.py")
        assert any("hash-like" in f for f in findings)

    def test_allows_hash_with_context_hint(self):
        # Same hex, but a context word on the line makes it structural.
        line = "# commit sha: " + "a" * 64
        assert ss.scan_text(line, "demo.py") == []

class TestAllowlist:
    @pytest.mark.parametrize("rel", [
        "data/vllm/ci/ci_health.json",
        "flake.lock",
        "tests/browser/node_modules/playwright/index.js",
        "tests/browser/test-results/trace/resources/request.json",
        "tests/browser/playwright-report/index.html",
    ])
    def test_paths_skipped(self, rel):
        assert ss._is_allowlisted(rel)

    @pytest.mark.parametrize("rel", [
        "docs/assets/js/ops-v2.js",
        "scripts/vllm/collect_ci.py",
    ])
    def test_source_paths_not_skipped(self, rel):
        # These are source files the scanner MUST walk.
        if rel.startswith("tests/vllm/test_auth_and_token_isolation.py"):
            pytest.skip("that test file is itself allowlisted")
        assert not ss._is_allowlisted(rel)


class TestRepoClean:
    """The real repo must pass its own scanner."""

    def test_main_returns_zero(self, capsys):
        assert ss.main() == 0
