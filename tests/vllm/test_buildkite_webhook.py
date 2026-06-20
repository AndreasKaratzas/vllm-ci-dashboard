"""Tests for the standalone Buildkite webhook routing helpers."""

from vllm.ci.webhook import is_nightly_build


def test_webhook_accepts_standard_amd_nightly():
    assert is_nightly_build({"message": "AMD Full CI Run - nightly"}, "amd-ci")


def test_webhook_ignores_therock_amd_nightly():
    assert not is_nightly_build(
        {"message": "AMD Full CI Run - TheRock nightly (2026-06-15, base 9872921c5)"},
        "amd-ci",
    )


def test_webhook_accepts_standard_upstream_nightly():
    assert is_nightly_build({"message": "Full CI run - nightly"}, "ci")

