"""Security contracts for the public, read-only dashboard shell."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent.parent
JS = ROOT / "docs" / "assets" / "js"
INDEX = ROOT / "docs" / "index.html"
WORKFLOWS = ROOT / ".github" / "workflows"


def test_retired_browser_auth_and_control_tools_are_absent() -> None:
    removed = (
        "auth.js",
        "token-vault.js",
        "ci-testbuild.js",
        "ci-ready.js",
        "ci-admin.js",
    )
    index = INDEX.read_text()
    utils = (JS / "utils.js").read_text()

    for name in removed:
        assert not (JS / name).exists()
        assert name not in index
    for route in ("ci-testbuild", "ci-ready", "ci-admin"):
        assert route not in index
        assert route not in utils
    assert "__authGate" not in (JS / "dashboard-nav.js").read_text()


def test_content_security_policy_has_no_browser_mutation_api() -> None:
    index = INDEX.read_text()
    match = re.search(
        r'<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]+)"',
        index,
    )
    assert match
    policy = match.group(1)
    assert "default-src 'self'" in policy
    assert "connect-src 'self' https://raw.githubusercontent.com" in policy
    assert "api.github.com" not in policy
    assert "api.buildkite.com" not in policy
    assert "frame-ancestors" not in policy


def test_no_browser_bundle_contains_embedded_credentials() -> None:
    suspicious = (
        "ghp_",
        "github_pat_",
        "BUILDKITE_TOKEN=",
        "Authorization: Bearer gh",
    )
    for path in JS.glob("*.js"):
        source = path.read_text()
        for token in suspicious:
            assert token not in source, f"{path.name} contains {token!r}"


def test_dns_state_key_is_confined_to_cryptographic_steps() -> None:
    workflow_hits = [
        path.name
        for path in WORKFLOWS.glob("*.yml")
        if "DNS_STATE_ENCRYPTION_KEY" in path.read_text()
    ]
    assert workflow_hits == ["dns-health.yml"]

    workflow = yaml.safe_load((WORKFLOWS / "dns-health.yml").read_text())
    steps = workflow["jobs"]["collect"]["steps"]
    keyed_steps = [
        step
        for step in steps
        if "DNS_STATE_ENCRYPTION_KEY" in (step.get("env") or {})
    ]
    assert [step["name"] for step in keyed_steps] == [
        "Resolve durable DNS scanner state",
        "Encrypt durable DNS scanner state",
    ]
    for step in keyed_steps:
        assert step["env"] == {
            "DNS_STATE_ENCRYPTION_KEY": "${{ secrets.DNS_STATE_ENCRYPTION_KEY }}"
        }
        assert "--key" not in step["run"]
        assert "echo \"$DNS_STATE_ENCRYPTION_KEY\"" not in step["run"]

    for name in ("Collect DNS failure observations", "Publish durable DNS evidence"):
        step = next(item for item in steps if item.get("name") == name)
        assert "DNS_STATE_ENCRYPTION_KEY" not in (step.get("env") or {})
