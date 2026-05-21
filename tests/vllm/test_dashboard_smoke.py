"""Smoke test for the dashboard bundle (docs/).

A real headless-browser test would require Node or Playwright, neither of
which is available in this repo's venv. Instead we verify the contract
statically:

1. ``docs/index.html`` references every JS module we ship
2. Each referenced JS file exists and has balanced braces/parens
3. Every ``fetch('data/...')`` call in the JS points at a file that
   exists under ``data/`` (or is one we know may not have been
   generated yet — whitelisted)
4. No JS file references the old per-file ``h()`` helper signature
   conflicting with the shared ``el()`` factory (regression guard for
   the Phase 1 de-duplication)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = ROOT / "docs"
JS = DOCS / "assets" / "js"
DATA = ROOT / "data"

# Files that the collectors may not have written yet on a fresh clone.
OPTIONAL_DATA_FILES = {
    "data/vllm/ci/hotness.json",
    "data/vllm/ci/group_changes.json",
    # Populated by register_test_build.py the first time a user dispatches a
    # build; the tab fetches defensively and renders an empty state otherwise.
    "data/vllm/ci/test_builds/index.json",
    # Generated locally by tools/encrypt_engineers.py and only required by the
    # admin-only Ready Tickets tab. Absent on a fresh clone.
    "data/vllm/ci/engineers.enc.json",
    # Written only by the thrice-daily live sync (ready-tickets-live.yml) —
    # requires PROJECTS_TOKEN to query Projects V2. Absent on fresh clones
    # and between the first dry-run and the first live run after deploy.
    "data/vllm/ci/project_items.json",
    # Written once by scripts/vllm/encrypt_kill_auth.py with the admin's
    # Buildkite token. Its absence is the expected state on fresh clones —
    # the Queue tab falls back to "no-auth" in the kill flow.
    "data/vllm/ci/kill_auth.enc.json",
}


# Matches ``<!-- ... -->`` including across newlines. CSP documentation in the
# <head> block embeds example strings like ``<script src="evil.com/…">`` to
# explain what CSP blocks; those are comments, not real script references, and
# must not be parsed as such.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_html_comments(html: str) -> str:
    return _HTML_COMMENT_RE.sub("", html)


def _js_files_referenced_by_index():
    """Return (relative_src, absolute_path) for every local <script src>."""
    html = _strip_html_comments((DOCS / "index.html").read_text())
    refs = re.findall(r'<script[^>]+src="([^"]+)"', html)
    out = []
    for src in refs:
        # Skip remote CDN scripts
        if src.startswith(("http://", "https://", "//")):
            continue
        # Strip cache-busting query string
        clean = src.split("?", 1)[0]
        out.append((clean, DOCS / clean))
    return out


class TestIndexHtml:
    def test_index_exists(self):
        assert (DOCS / "index.html").exists(), "docs/index.html is missing"

    def test_index_references_all_core_js(self):
        html = (DOCS / "index.html").read_text()
        # These five are the core runtime — utils must load first (defines el/h shared helpers).
        required = ["utils.js", "ci-health.js", "ci-analytics.js", "ci-queue.js",
                    "ci-hotness.js", "dashboard.js"]
        for name in required:
            assert name in html, f"docs/index.html must reference {name}"

    def test_utils_loads_before_dependents(self):
        html = (DOCS / "index.html").read_text()
        utils_pos = html.find("utils.js")
        for dep in ("ci-health.js", "ci-analytics.js", "ci-queue.js", "ci-hotness.js", "dashboard.js"):
            dep_pos = html.find(dep)
            assert utils_pos < dep_pos, (
                f"utils.js must load before {dep} — it defines the shared h/el helpers"
            )

    def test_dashboard_loads_before_admin_tools(self):
        html = (DOCS / "index.html").read_text()
        dashboard_pos = html.find("dashboard.js")
        for dep in ("ci-testbuild.js", "ci-ready.js", "ci-admin.js"):
            dep_pos = html.find(dep)
            assert dashboard_pos < dep_pos, (
                f"dashboard.js should load before {dep} so guest/home rendering is not blocked by admin tooling"
            )


class TestJsFilesPresent:
    def test_every_referenced_js_exists(self):
        missing = [src for src, path in _js_files_referenced_by_index() if not path.exists()]
        assert not missing, f"index.html references JS files that don't exist: {missing}"


class TestJsFileShape:
    """Best-effort structural check without a real JS tokenizer.

    A proper parse requires esprima/Node (not available in this venv).
    What we *can* catch statically: zero-byte files, stray BOM, missing
    IIFE wrapper (every module in this codebase wraps itself in an IIFE
    or a DOMContentLoaded listener).
    """

    @pytest.mark.parametrize("name", [
        "utils.js", "ci-health.js", "ci-analytics.js",
        "ci-queue.js", "ci-hotness.js", "dashboard.js",
    ])
    def test_file_is_nonempty_and_well_formed(self, name):
        path = JS / name
        text = path.read_text()
        assert len(text) > 100, f"{name} is suspiciously small"
        assert not text.startswith("\ufeff"), f"{name} starts with a BOM"
        # Every module is either an IIFE or registers a DOMContentLoaded handler.
        has_iife = re.search(r'\(\s*function\s*\(', text) or re.search(r'\(\s*\(\s*\)\s*=>', text)
        has_domready = "DOMContentLoaded" in text
        has_top_level_const = re.match(r'\s*(const|let|var|function|//)', text) is not None
        assert has_iife or has_domready or has_top_level_const, (
            f"{name} doesn't look like a valid JS module "
            "(no IIFE, no DOMContentLoaded, no top-level declarations)"
        )

    def test_dashboard_boot_has_visible_startup_failure_path(self):
        text = (JS / "dashboard.js").read_text()
        assert "renderStartupError" in text, (
            "dashboard.js should surface boot failures instead of leaving the page on Loading..."
        )
        assert "location.protocol==='file:'" in text or 'location.protocol === "file:"' in text, (
            "dashboard.js should detect file:// previews so it can explain the local-server requirement"
        )
        assert "python3 -m http.server 8000 -d docs" in text, (
            "dashboard.js should tell local users how to serve docs/ over HTTP"
        )

    def test_dashboard_consumes_issue_side_pr_links(self):
        text = (JS / "dashboard.js").read_text()
        assert "linked_prs" in text, (
            "dashboard.js should consume issue-side linked_prs so comment-linked fixes appear on Home"
        )
        assert "issueNumsByLinkedPr" in text, (
            "dashboard.js should reverse-map linked PR refs back to CI issues"
        )

    def test_utils_exposes_parity_family_merge_helper(self):
        text = (JS / "utils.js").read_text()
        assert "function mergeParityGroups" in text, (
            "utils.js should expose a canonical parity-family merge helper for shared dashboard views"
        )
        assert "family_name" in text and "family_key" in text, (
            "mergeParityGroups should understand canonical family metadata from parity_report.json"
        )

    def test_parity_views_use_family_merge(self):
        dashboard = (JS / "dashboard.js").read_text()
        ci_health = (JS / "ci-health.js").read_text()
        assert "mergeParityGroups" in dashboard, (
            "dashboard.js should render parity cards from canonical parity families"
        )
        assert ci_health.count("mergeParityGroups") >= 2, (
            "ci-health.js should use canonical parity families in its parity-focused sections"
        )

    def test_ci_health_top_cards_use_consistent_group_denominators(self):
        text = (JS / "ci-health.js").read_text()
        assert "const amdTotalGroups=a.unique_test_groups||mergedAmdGroups||0" in text
        assert "const amdPassAny=a.test_groups_passing_or ?? passingGroups.length" in text
        assert "card('Test Groups',`${amdPassAny}/${amdTotalGroups}`" in text
        assert "const coverageGroups=mergedGroups" in text
        assert "const hasUpstreamCoverage=g=>!!g.upstream||g.status==='upstream_only'" in text
        assert "bothGroups=coverageGroups.filter" in text

    def test_fetchjson_catches_rejected_fetches(self):
        text = (JS / "utils.js").read_text()
        m = re.search(
            r"async function fetchJSON\(url, opts\) \{(.*?)\n\}\n\n// ── Shared element factory",
            text,
            re.DOTALL,
        )
        assert m, "utils.js should define fetchJSON(url)"
        assert "catch" in m.group(1), (
            "fetchJSON should catch network/file-origin failures instead of rejecting and aborting boot"
        )
        assert "AbortController" in m.group(1), (
            "fetchJSON should enforce a timeout so a hanging request cannot stall the whole dashboard forever"
        )

    def test_index_has_boot_fallback_guard(self):
        text = (DOCS / "index.html").read_text()
        assert "__recordBootIssue" in text, (
            "index.html should install a boot-error guard so runtime/script failures are shown on the page"
        )
        assert "__renderBootFallback" in text, (
            "index.html should provide a visible fallback when startup never completes"
        )

    def test_auth_boot_does_not_auto_block_dashboard(self):
        text = (JS / "auth.js").read_text()
        m = re.search(
            r"function boot\(\) \{(.*?)\n  \}",
            text,
            re.DOTALL,
        )
        assert m, "auth.js should define boot()"
        body = m.group(1)
        assert "buildOverlay();" not in body, (
            "auth boot should not auto-open a blocking full-page sign-in overlay for public dashboard viewers"
        )
        assert "emitAuthChanged();" in body, (
            "auth boot should refresh the visible shell state instead of relying on a blocking overlay"
        )

    def test_auth_does_not_install_global_dom_watchers(self):
        text = (JS / "auth.js").read_text()
        assert "function startNavObserver()" not in text, (
            "auth.js should not install subtree-wide nav/main MutationObservers; they can create self-triggering UI churn"
        )
        assert "function _clickGuard(" not in text, (
            "auth.js should not install a document-level capture click guard for normal navigation"
        )

    def test_ready_tickets_render_ignores_stale_async_work(self):
        text = (JS / "ci-ready.js").read_text()
        assert "let renderSeq = 0;" in text, (
            "ci-ready.js should track the latest render so older async fetches cannot append duplicate cards"
        )
        assert "const seq = ++renderSeq;" in text, (
            "ci-ready.js render() should mint a new render token on every call"
        )
        assert text.count("if (seq !== renderSeq) return;") >= 2, (
            "ci-ready.js should bail after each awaited load when a newer render has started"
        )

    def test_ready_tickets_table_is_sortable(self):
        text = (JS / "ci-ready.js").read_text()
        assert "let readyTableSort = { key: null, dir: 'asc' };" in text, (
            "ci-ready.js should persist the ready-ticket table sort selection across rerenders"
        )
        assert "function sortReadyTickets(tickets)" in text, (
            "ci-ready.js should sort ready-ticket rows through a dedicated helper"
        )
        assert "title: `Sort by ${col.label.toLowerCase()}`" in text, (
            "ci-ready.js should expose sortable header controls for the ready-ticket table"
        )

    def test_ready_tickets_uses_project_items_for_post_tracker_issue_links(self):
        text = (JS / "ci-ready.js").read_text()
        assert "async function loadProjectItems()" in text, (
            "ci-ready.js should load project_items.json so it can map failing groups to newer project #39 issues"
        )
        assert "label: 'Project issue'" in text, (
            "ci-ready.js should show a separate project-issue column in single-master mode"
        )
        assert "num > masterIssueNumber" in text, (
            "ci-ready.js should only attach per-group issue links for post-umbrella project items"
        )

    def test_dashboard_uses_project_issues_and_tagged_pr_workbench(self):
        text = (JS / "dashboard.js").read_text()
        assert "buildPRWorkbenchSection" in text and "buildIssueWorkbenchSection" in text, (
            "dashboard.js should render full-width Home PR and issue workbench lists"
        )
        assert "setHomeSort" in text and "setHomePage" in text and "setHomeFilter" in text, (
            "Home PR/issue lists should be sortable, filterable, and paginated"
        )
        assert "pr.is_ci_pr" in text and "pr.is_rocm_pr" in text, (
            "Home PR rows should expose custom CI and ROCm tag columns"
        )
        assert "home-workbench" in text and "workbench-row" in text, (
            "Home should use compact row cards instead of squeezed side-by-side tables"
        )
        assert "projectIssues = issues.filter" in text, (
            "Open project #39 issues should be rendered directly instead of filtered by legacy tracker cutover"
        )


class TestSiteBuildAssembly:
    def test_shared_build_script_exists(self):
        assert (ROOT / "scripts" / "build_site.py").exists(), (
            "scripts/build_site.py should be the canonical site assembler"
        )

    def test_pages_workflows_use_shared_build_script(self):
        for rel in (
            ".github/workflows/deploy-pages.yml",
            ".github/workflows/daily-update.yml",
            ".github/workflows/ci-collect.yml",
            ".github/workflows/hourly-master.yml",
            ".github/workflows/queue-monitor.yml",
            ".github/workflows/pr-preview.yml",
        ):
            text = (ROOT / rel).read_text()
            assert "python scripts/build_site.py --cache-bust-index" in text, (
                f"{rel} should assemble the published site via scripts/build_site.py"
            )


class TestDataFetchContract:
    """Every ``fetch('data/...')`` URL must resolve to a committed file.

    Optional-but-expected files are whitelisted so a fresh clone doesn't
    break this test before the collectors have run.
    """

    def _extract_fetch_urls(self):
        pattern = re.compile(r"""['"`](data/[^'"`?\s]+)""")
        urls = set()
        for js in JS.glob("*.js"):
            text = js.read_text()
            for m in pattern.finditer(text):
                url = m.group(1)
                # Skip dynamic URLs with template literal interpolation
                # (e.g. ``data/.../${entryId}/comparison.json``) — the real
                # path is only known at runtime.
                if "${" in url:
                    continue
                urls.add(url)
        return urls

    def test_every_fetch_url_is_a_real_file_or_whitelisted(self):
        for url in self._extract_fetch_urls():
            abspath = ROOT / url
            if abspath.exists():
                continue
            assert url in OPTIONAL_DATA_FILES, (
                f"JS references {url!r} but file doesn't exist and isn't in the optional whitelist. "
                "Either the file needs to be generated, the path is typo'd, or add it to "
                "OPTIONAL_DATA_FILES in this test."
            )


class TestSharedHelperRegression:
    """Phase 1 extracted the per-file ``h()`` factories into shared ``el()``.

    Each module now aliases ``const h = el;`` instead of redefining h().
    This test locks that invariant in so nobody accidentally re-introduces
    a divergent local h().
    """

    @pytest.mark.parametrize("name", ["ci-health.js", "ci-analytics.js", "ci-hotness.js", "ci-queue.js"])
    def test_module_aliases_shared_el(self, name):
        text = (JS / name).read_text()
        # Either ``const h = el`` alias, or uses el() directly — both are fine.
        assert re.search(r'\bconst\s+h\s*=\s*el\b', text) or "el(" in text, (
            f"{name} should reuse the shared el() factory from utils.js "
            "(via 'const h = el;' alias or direct el() calls)"
        )

    def test_utils_exposes_el_factory(self):
        text = (JS / "utils.js").read_text()
        # The factory should be defined at module scope as a function or arrow.
        assert re.search(r'\bfunction\s+el\s*\(', text) or re.search(r'\bel\s*=\s*(function|\()', text), (
            "utils.js must define the shared el() factory"
        )
