"""Live redirect checks for supported GitHub and current-CI evidence URLs.

The retired LinkRegistry and runtime parity report are not dashboard inputs.
Exact job URL identity is covered by the Operations renderer and auditor tests.
"""
import json
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
pytestmark = pytest.mark.live_data

class TestLinksNoRedirect:
    """Verify that generated links do NOT redirect when opened.

    These tests make real HTTP HEAD requests to external services.
    They are marked as 'network' so they can be skipped in offline/CI
    environments with: pytest -m 'not network'
    """

    GITHUB = "https://github.com"
    BUILDKITE = "https://buildkite.com"

    @staticmethod
    def _check_no_redirect(url, label=""):
        """Assert that a URL does not redirect (HTTP 3xx)."""
        try:
            resp = requests.head(url, allow_redirects=False, timeout=10,
                                 headers={"User-Agent": "project-dashboard-link-test/1.0"})
        except requests.RequestException as e:
            pytest.skip(f"Network error for {url}: {e}")

        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location", "?")
            pytest.fail(
                f"REDIRECT detected for {label or url}:\n"
                f"  URL:      {url}\n"
                f"  Status:   {resp.status_code}\n"
                f"  Location: {location}\n"
                f"  Fix: update the URL to point directly to {location}"
            )
        # 2xx or 404 (repo might not exist) are acceptable
        assert resp.status_code < 400 or resp.status_code == 404, (
            f"Unexpected status {resp.status_code} for {url}"
        )

    # ── Static URLs (always testable) ──

    @pytest.mark.network
    def test_github_base_no_redirect(self):
        self._check_no_redirect(f"{self.GITHUB}", "GitHub base")

    @pytest.mark.network
    def test_buildkite_pipeline_ci_no_redirect(self):
        self._check_no_redirect(f"{self.BUILDKITE}/vllm/ci", "Current MI CI pipeline")

    # ── GitHub repo URLs from projects.json ──

    @pytest.mark.live_data
    @pytest.mark.network
    def test_project_repo_urls_no_redirect(self):
        """Every repo URL in projects.json must not redirect."""
        projects_path = ROOT / "data" / "site" / "projects.json"
        if not projects_path.exists():
            pytest.skip("projects.json not found")
        projects = json.loads(projects_path.read_text())
        for name, cfg in projects.get("projects", {}).items():
            repo = cfg.get("repo", "")
            if not repo:
                continue
            url = f"{self.GITHUB}/{repo}"
            self._check_no_redirect(url, f"repo:{name}")


    # ── CI health build URLs ──

    @pytest.mark.live_data
    @pytest.mark.network
    def test_ci_health_build_urls_no_redirect(self):
        """Build URLs in ci_health.json must not redirect."""
        health_path = DATA / "vllm" / "ci" / "ci_health.json"
        if not health_path.exists():
            pytest.skip("ci_health.json not collected yet")
        health = json.loads(health_path.read_text())

        for pipeline in ("amd",):
            data = health.get(pipeline, {})
            lb = data.get("latest_build", {})
            url = lb.get("build_url", "")
            if url:
                self._check_no_redirect(url, f"ci_health:{pipeline}")
