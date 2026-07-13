"""Static contracts for the Signal Desk v2 frontend boundary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "docs" / "index.html").read_text()
OPS_JS = (ROOT / "docs" / "assets" / "js" / "ops-v2.js").read_text()
OPS_CSS = (ROOT / "docs" / "assets" / "css" / "ops-v2.css").read_text()


def test_v2_assets_and_mobile_shell_are_loaded():
    assert "assets/css/ops-v2.css" in INDEX
    assert "assets/js/ops-v2.js" in INDEX
    assert "window.__DASHBOARD_V2__ = true" in INDEX
    assert 'id="ops-menu-toggle"' in INDEX
    assert 'id="ops-nav-backdrop"' in INDEX


def test_v2_owns_all_operational_views():
    for tab in (
        "projects",
        "ci-health",
        "ci-analytics",
        "ci-perf-eval",
        "ci-queue",
        "ci-hotness",
        "ci-omni",
    ):
        assert f"'{tab}'" in OPS_JS
    assert "renderPerf" in OPS_JS
    assert ".ops-page .ops-perf-metric-grid" in OPS_CSS


def test_legacy_renderers_yield_to_v2():
    js_dir = ROOT / "docs" / "assets" / "js"
    for name in (
        "ci-health.js",
        "ci-analytics.js",
        "ci-perf-eval.js",
        "ci-queue.js",
        "ci-hotness.js",
        "ci-omni.js",
    ):
        assert "__DASHBOARD_V2__" in (js_dir / name).read_text()


def test_reliability_evidence_is_drillable_and_honestly_named():
    assert "openMixedOutcomeEvidence" in OPS_JS
    assert "mixed-outcome candidate" in OPS_JS
    assert "not a test-case flake probability" in OPS_JS
    assert "Open log" in OPS_JS
    assert "Incidents only" in OPS_JS


def test_gating_and_coverage_render_complete_collector_lists():
    assert "gating.target_groups || []" in OPS_JS
    assert "matrixData.rows || []" in OPS_JS
    assert "target_groups || []).slice" not in OPS_JS
    assert "matrixData.rows || []).slice" not in OPS_JS


def test_p99_is_sample_only_and_missing_values_are_not_zeroed():
    assert "row.p99_wait_source !== 'sample_wait'" in OPS_JS
    assert "Unavailable without job samples" in OPS_JS
    assert "minutes === null || minutes === undefined" in OPS_JS


def test_operations_components_are_scoped_and_responsive():
    assert ".ops-page .ops-status-strip" in OPS_CSS
    assert ".ops-page .ops-table-scroll" in OPS_CSS
    assert "@media (max-width: 767px)" in OPS_CSS
    assert "body.ops-v2.ops-drawer-open #sidebar" in OPS_CSS
    assert "body.ops-v2.ops-drawer-open #ops-nav-backdrop" in OPS_CSS
