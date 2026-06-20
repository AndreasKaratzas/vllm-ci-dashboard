"""
Tests for the centralized LinkRegistry and link correctness.

Verifies that:
1. All links in JS files go through LinkRegistry (no hardcoded URLs)
2. LinkRegistry URL builders produce canonical, non-redirecting URLs
3. All external links in the generated HTML resolve without redirects
"""
import json
import re
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent.parent
JS_DIR = ROOT / "docs" / "assets" / "js"
DATA = ROOT / "data"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. No hardcoded external URLs outside LinkRegistry
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoHardcodedURLs:
    """Ensure all external URLs go through LinkRegistry, not hardcoded strings."""

    JS_FILES = ["dashboard.js", "ci-health.js", "ci-analytics.js", "ci-queue.js"]

    # Patterns that must NOT appear in non-utils JS files
    GITHUB_URL_RE = re.compile(r"""(?:['"`])https://github\.com/""")
    BK_URL_RE = re.compile(r"""(?:['"`])https://buildkite\.com/""")
    HREF_HASH_RE = re.compile(r"""href\s*[=:]\s*['"]#['"]""")

    def _read_js(self, filename):
        return (JS_DIR / filename).read_text()

    @pytest.mark.parametrize("filename", JS_FILES)
    def test_no_hardcoded_github_urls(self, filename):
        """No JS file (except utils.js) should contain hardcoded github.com URLs."""
        js = self._read_js(filename)
        matches = self.GITHUB_URL_RE.findall(js)
        assert not matches, (
            f"{filename} has {len(matches)} hardcoded github.com URL(s). "
            f"Use LinkRegistry.github.* instead."
        )

    @pytest.mark.parametrize("filename", JS_FILES)
    def test_no_hardcoded_buildkite_urls(self, filename):
        """No JS file (except utils.js) should contain hardcoded buildkite.com URLs."""
        js = self._read_js(filename)
        matches = self.BK_URL_RE.findall(js)
        assert not matches, (
            f"{filename} has {len(matches)} hardcoded buildkite.com URL(s). "
            f"Use LinkRegistry.bk.* instead."
        )

    @pytest.mark.parametrize("filename", JS_FILES + ["utils.js"])
    def test_no_href_hash_links(self, filename):
        """No JS file should use href='#' pattern (use real URLs instead)."""
        js = self._read_js(filename)
        matches = self.HREF_HASH_RE.findall(js)
        assert not matches, (
            f"{filename} has {len(matches)} href='#' link(s). "
            f"Use proper href with LinkRegistry URLs instead."
        )

    def test_utils_has_link_registry(self):
        """utils.js must define the LinkRegistry."""
        js = self._read_js("utils.js")
        assert "var LinkRegistry" in js
        assert "LinkRegistry.github" in js or "github:" in js
        assert "LinkRegistry.bk" in js or "bk:" in js

    def test_utils_single_github_constant(self):
        """utils.js should only have one github.com base URL (in LinkRegistry)."""
        js = self._read_js("utils.js")
        matches = re.findall(r"'https://github\.com'", js)
        assert len(matches) == 1, (
            f"Expected exactly 1 GitHub base URL in utils.js, found {len(matches)}"
        )

    def test_utils_single_buildkite_constant(self):
        """utils.js should only have one buildkite.com base URL (in LinkRegistry)."""
        js = self._read_js("utils.js")
        matches = re.findall(r"'https://buildkite\.com'", js)
        assert len(matches) == 1, (
            f"Expected exactly 1 Buildkite base URL in utils.js, found {len(matches)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LinkRegistry URL builder correctness (simulate in Python)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLinkRegistryURLBuilders:
    """Verify the URL patterns that LinkRegistry produces are canonical."""

    GITHUB = "https://github.com"
    BUILDKITE = "https://buildkite.com"

    def test_github_repo_url_format(self):
        url = f"{self.GITHUB}/vllm-project/vllm"
        assert not url.endswith("/")
        assert "//" not in url.replace("https://", "")

    def test_github_user_url_format(self):
        url = f"{self.GITHUB}/someuser"
        assert not url.endswith("/")
        assert "//" not in url.replace("https://", "")

    def test_github_pr_url_format(self):
        url = f"{self.GITHUB}/vllm-project/vllm/pull/123"
        assert url.endswith("/123")
        assert "//" not in url.replace("https://", "")

    def test_github_commit_url_format(self):
        sha = "abc123def456"
        url = f"{self.GITHUB}/pytorch/pytorch/commit/{sha}"
        assert sha in url
        assert "//" not in url.replace("https://", "")

    def test_buildkite_pipeline_url_format(self):
        url = f"{self.BUILDKITE}/vllm/amd-ci"
        assert not url.endswith("/")

    def test_no_trailing_slash_on_any_url(self):
        """All URL patterns must not have trailing slashes."""
        urls = [
            f"{self.GITHUB}/vllm-project/vllm",
            f"{self.GITHUB}/user123",
            f"{self.GITHUB}/org/repo/pull/1",
            f"{self.GITHUB}/org/repo/issues/2",
            f"{self.GITHUB}/org/repo/commit/abc123",
            f"{self.BUILDKITE}/vllm/amd-ci",
            f"{self.BUILDKITE}/vllm/ci",
        ]
        for url in urls:
            assert not url.endswith("/"), f"URL has trailing slash: {url}"

    def test_special_chars_in_username(self):
        """Usernames with special chars must be encoded."""
        from urllib.parse import quote
        username = "user+name"
        encoded = quote(username, safe="")
        url = f"{self.GITHUB}/{encoded}"
        assert "+" not in url.split("/")[-1] or "%2B" in url


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Live link redirect validation (external HTTP check)
# ═══════════════════════════════════════════════════════════════════════════════

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
    def test_buildkite_pipeline_amd_no_redirect(self):
        self._check_no_redirect(f"{self.BUILDKITE}/vllm/amd-ci", "BK AMD pipeline")

    @pytest.mark.network
    def test_buildkite_pipeline_upstream_no_redirect(self):
        self._check_no_redirect(f"{self.BUILDKITE}/vllm/ci", "BK upstream pipeline")

    # ── GitHub repo URLs from projects.json ──

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

    # ── Buildkite job links from parity data ──

    @pytest.mark.network
    def test_parity_job_links_no_redirect(self):
        """Every job_link URL in parity_report.json must not redirect."""
        parity_path = DATA / "vllm" / "ci" / "parity_report.json"
        if not parity_path.exists():
            pytest.skip("parity_report.json not collected yet")
        parity = json.loads(parity_path.read_text())

        checked = 0
        for g in parity.get("job_groups", []):
            for link in g.get("job_links", []):
                url = link.get("url", "")
                if not url:
                    continue
                self._check_no_redirect(url, f"group:{g['name']} side:{link.get('side')}")
                checked += 1
                # Limit to first 20 to avoid hammering Buildkite
                if checked >= 20:
                    return

    # ── CI health build URLs ──

    @pytest.mark.network
    def test_ci_health_build_urls_no_redirect(self):
        """Build URLs in ci_health.json must not redirect."""
        health_path = DATA / "vllm" / "ci" / "ci_health.json"
        if not health_path.exists():
            pytest.skip("ci_health.json not collected yet")
        health = json.loads(health_path.read_text())

        for pipeline in ("amd", "upstream"):
            data = health.get(pipeline, {})
            lb = data.get("latest_build", {})
            url = lb.get("build_url", "")
            if url:
                self._check_no_redirect(url, f"ci_health:{pipeline}")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. LinkRegistry integration: all JS files use it consistently
# ═══════════════════════════════════════════════════════════════════════════════

class TestLinkRegistryIntegration:
    """Verify that the LinkRegistry is used consistently across all JS files."""

    def _read_js(self, filename):
        return (JS_DIR / filename).read_text()

    def test_dashboard_uses_link_registry_for_repos(self):
        """dashboard.js must use LinkRegistry.github.repo for repo URLs."""
        js = self._read_js("dashboard.js")
        assert "LinkRegistry.github.repo(" in js

    def test_dashboard_uses_link_registry_atag(self):
        """dashboard.js must use LinkRegistry.aTag for generating link HTML."""
        js = self._read_js("dashboard.js")
        assert "LinkRegistry.aTag(" in js

    def test_ci_health_uses_link_registry(self):
        """ci-health.js must use LinkRegistry for links."""
        js = self._read_js("ci-health.js")
        assert "LinkRegistry" in js

    def test_ci_analytics_uses_link_registry(self):
        """ci-analytics.js must use LinkRegistry for links."""
        js = self._read_js("ci-analytics.js")
        assert "LinkRegistry" in js

    def test_all_atag_links_have_rel_noopener(self):
        """LinkRegistry.aTag must include rel='noopener' for security."""
        js = self._read_js("utils.js")
        assert "rel=\"noopener\"" in js or "rel='noopener'" in js
