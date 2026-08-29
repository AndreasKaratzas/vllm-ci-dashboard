"""Static contracts for the public publication-status banner."""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "docs" / "index.html").read_text()
SCRIPT = (ROOT / "docs" / "assets" / "js" / "publication-status.js").read_text()
CSS = (ROOT / "docs" / "assets" / "css" / "dashboard.css").read_text()
MANIFEST = (ROOT / "config" / "public_data_manifest.json").read_text()


def test_publication_status_banner_is_global_accessible_and_cache_busted() -> None:
    banner = 'id="publication-status-banner"'
    assert banner in INDEX
    assert INDEX.index(banner) < INDEX.index('id="tab-projects"')
    assert 'aria-live="polite"' in INDEX
    assert 'aria-atomic="true"' in INDEX
    assert "assets/js/publication-status.js?v=3" in INDEX


def test_publication_status_script_handles_every_nonhealthy_mode() -> None:
    assert "data/vllm/ci/publication_status.json" in SCRIPT
    for mode in ("degraded", "fallback", "mixed", "blocked"):
        assert f"{mode}: Object.freeze" in SCRIPT
    assert "Publication status unavailable" in SCRIPT
    assert "Dashboard snapshot is stale" in SCRIPT
    assert "HEALTHY_MAX_AGE_MS = 3 * 60 * 60 * 1000" in SCRIPT
    assert "no-store" in SCRIPT


def test_healthy_publication_older_than_three_hours_renders_stale() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const sandbox = {
  window: {},
  document: {readyState: 'loading', addEventListener: function () {}},
  Date: Date,
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, {filename: process.argv[1]});
const viewFor = sandbox.window.PublicationStatusBanner.viewFor;
const now = Date.parse('2026-08-20T16:00:00Z');
assert.equal(viewFor({
  mode: 'current', status: 'healthy', generated_at: '2026-08-20T14:00:00Z',
}, now), null);
assert.equal(viewFor({
  mode: 'current', status: 'healthy', generated_at: '2026-08-20T12:00:00Z',
}, now).title, 'Dashboard snapshot is stale');
assert.equal(viewFor({
  mode: 'current', status: 'healthy', generated_at: 'not-a-time',
}, now).title, 'Publication status unavailable');
"""
    result = subprocess.run(
        [node, "-e", script, str(ROOT / "docs" / "assets" / "js" / "publication-status.js")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_old_noncurrent_publication_escalates_to_stale_snapshot() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const sandbox = {
  window: {},
  document: {readyState: 'loading', addEventListener: function () {}},
  Date: Date,
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, {filename: process.argv[1]});
const viewFor = sandbox.window.PublicationStatusBanner.viewFor;
const now = Date.parse('2026-08-20T16:00:00Z');
for (const mode of ['degraded', 'fallback', 'mixed']) {
  assert.equal(viewFor({
    mode: mode,
    status: 'degraded',
    generated_at: '2026-08-20T12:00:00Z',
  }, now).title, 'Dashboard snapshot is stale');
}
assert.equal(viewFor({
  mode: 'blocked',
  status: 'blocked',
  generated_at: '2026-08-20T12:00:00Z',
}, now).title, 'Latest dashboard refresh blocked');
"""
    result = subprocess.run(
        [
            node,
            "-e",
            script,
            str(ROOT / "docs" / "assets" / "js" / "publication-status.js"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_publication_status_rendering_does_not_inject_remote_content() -> None:
    assert ".textContent = view.title" in SCRIPT
    assert ".textContent = view.message" in SCRIPT
    assert "meta.textContent = details.join" in SCRIPT
    assert "innerHTML" not in SCRIPT


def test_publication_status_banner_has_warning_and_critical_styles() -> None:
    assert ".publication-status-banner" in CSS
    assert ".publication-status-banner[hidden]" in CSS
    assert ".publication-status-banner.is-critical" in CSS
    assert ".publication-status-banner.is-critical .publication-status-icon" in CSS


def test_public_status_is_generated_while_private_state_remains_private() -> None:
    assert '"vllm/ci/publication_state.json"' in MANIFEST
    assert '"vllm/ci/publication_status.json"' in MANIFEST
    assert '"vllm/ci/*_state.json"' in MANIFEST
