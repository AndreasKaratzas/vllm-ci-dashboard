"""Static contracts for the Signal Desk v2 frontend boundary."""

import json

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "docs" / "index.html").read_text()
OPS_JS = (ROOT / "docs" / "assets" / "js" / "ops-v2.js").read_text()
OPS_CSS = (ROOT / "docs" / "assets" / "css" / "ops-v2.css").read_text()
DASHBOARD_JS = (ROOT / "docs" / "assets" / "js" / "dashboard.js").read_text()
OPS_DATA = json.loads((ROOT / "data" / "vllm" / "ci" / "operations_v2.json").read_text())


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
    assert "openGroupDetail" in OPS_JS
    assert "openHistoryEvidence" in OPS_JS
    assert "mixed-outcome candidate" in OPS_JS
    assert "not a test-case flake probability" in OPS_JS
    assert "Open log" in OPS_JS
    assert "Incidents only" in OPS_JS


def test_shared_evidence_primitives_are_accessible_and_source_linked():
    for primitive in (
        "openDetailDrawer",
        "openMetricDetail",
        "linkedBadge",
        "openHistoryEvidence",
        "ops-linked-metric",
        "ops-chart-evidence-action",
    ):
        assert primitive in OPS_JS
    assert "event.key === 'Escape'" in OPS_JS
    assert "event.key === 'Enter' || event.key === ' '" in OPS_JS
    assert "canvas.tabIndex = 0" in OPS_JS
    assert "aria-modal" in OPS_JS
    assert "rel = 'noopener'" in OPS_JS
    assert "Open exact source" in OPS_JS


def test_drawers_and_route_filters_have_namespaced_query_state():
    assert "return 'ops_' + name" in OPS_JS
    assert "url.searchParams.set(queryName(name)" in OPS_JS
    assert "url.searchParams.delete(queryName(name))" in OPS_JS
    assert "window.history.replaceState" in OPS_JS
    assert "setQueryValue('detail'" in OPS_JS
    assert "syncRouteState(tabId)" in OPS_JS
    assert "['analyticsSearch', 'analytics_search', null]" in OPS_JS
    assert "setQueryValue('analytics_search', state.analyticsSearch)" in OPS_JS
    assert "openTestGroupHistory: openTestGroupHistory" in OPS_JS
    assert "queryValue('analytics_search') !== null" in OPS_JS


def test_gating_uses_reviewed_plan_and_observed_evidence_contract():
    for field in (
        "reviewed_plan",
        "latest_amd_result",
        "main_reliability",
        "nightly_green_streak",
        "last_incident",
        "assessment",
    ):
        assert field in OPS_JS
    for removed_label in ("Current target", "Readiness", "Target origin", "Owner"):
        assert removed_label not in OPS_JS
    for visible_label in (
        "Test group",
        "Reviewed plan",
        "Latest AMD result",
        "Main reliability",
        "Green streak",
        "Last incident",
        "Evidence",
    ):
        assert visible_label in OPS_JS
    assert "Search 127 reviewed groups" in OPS_JS
    assert "activeGroups.filter" not in OPS_JS
    assert "active_target_groups || gating.target_groups || []" in OPS_JS
    assert "matrixData.rows || []" in OPS_JS
    assert "matrixData.rows || []).slice" not in OPS_JS


def test_authoritative_group_catalog_preserves_id_and_variant_identity():
    assert "Array.isArray(reliability.group_catalog)" in OPS_JS
    assert "return reliability.group_catalog.map" in OPS_JS
    assert "'id:' + String(row.id || row.evidence_ref)" in OPS_JS
    assert "groupReliabilityByRef" in OPS_JS
    assert "groupVariantMeta" in OPS_JS
    assert "row.queues" in OPS_JS
    assert "row.shard" in OPS_JS
    assert "byName = new Map" not in OPS_JS


def test_gating_drilldown_combines_every_strict_group_id_and_observation():
    assert "function combinedGatingReliability" in OPS_JS
    assert "main.group_ids" in OPS_JS
    assert "groupReliabilityRowsByIds" in OPS_JS
    assert "variant_id: variant.id" in OPS_JS
    assert "observations.push" in OPS_JS
    assert "Inspect all variants and observations" in OPS_JS
    assert "Strict reliability variants" in OPS_JS

    multi_variant = [
        row
        for row in OPS_DATA["gating"]["active_target_groups"]
        if len(row.get("main_reliability", {}).get("group_ids", [])) > 1
    ]
    assert multi_variant


def test_queue_modes_ranges_provenance_and_missing_values_are_explicit():
    for label in ("Current", "History", "Jobs", "24h", "7d", "30d", "Include idle"):
        assert label in OPS_JS
    assert "row.p99_wait_source !== 'sample_wait'" in OPS_JS
    assert "No source reported a current wait percentile" in OPS_JS
    assert "agentMeasurements" in OPS_JS
    assert "function hasAgentMeasurement" in OPS_JS
    assert "connected_agents_available" in OPS_JS
    assert "'active_jobs', 'webhook', 'job_scan'" in OPS_JS
    assert "sums.agentMeasurements ? integer(sums.agents) : '-'" in OPS_JS
    assert "countProvenance" in OPS_JS
    assert "count source: ' + countProvenance" in OPS_JS
    assert "{label: 'p95'" in OPS_JS
    assert "p95 official" not in OPS_JS
    assert "minutes === null || minutes === undefined" in OPS_JS
    assert "Array.isArray(queueBlock.history)" in OPS_JS
    assert "queueBlock.history_summary" in OPS_JS


def test_retired_mi355b_queues_are_excluded_on_every_frontend_path():
    assert "function isRetiredQueue" in OPS_JS
    assert "/^amd_mi355b(?:_|$)/i" in OPS_JS
    assert "if (isRetiredQueue(entry[0])) return false" in OPS_JS
    assert "if (isRetiredQueue(job.queue)) return false" in OPS_JS
    assert "isRetiredQueue(name)" in OPS_JS


def test_amd_cpu_is_included_in_amd_queue_and_omni_scope():
    assert "function isAmdQueue" in OPS_JS
    assert "name === 'amd-cpu' || name.startsWith('amd_')" in OPS_JS
    assert "!isAmdQueue(entry[0])" in OPS_JS
    assert "state.queueScope === 'all' || isAmdQueue(job.queue)" in OPS_JS
    assert "state.queueScope === 'all' || isAmdQueue(name)" in OPS_JS
    assert "const amdPending = pending.filter" in OPS_JS
    assert "return isAmdQueue(job.queue)" in OPS_JS
    assert "ALL-FLEET ACTIVE JOBS" in OPS_JS
    assert OPS_JS.count("startsWith('amd_')") == 1


def test_all_main_and_nightly_analytics_are_distinct_surfaces():
    assert "All-main reliability" in OPS_JS
    assert "Nightly comparisons" in OPS_JS
    assert "All main" in OPS_JS
    assert "Nightlies only" in OPS_JS
    assert "regression lifecycle only" in OPS_JS
    assert "reliabilityCatalog" in OPS_JS
    assert "evidence_ref" in OPS_JS
    assert "ALL-MAIN BUILDS" in OPS_JS
    assert "canonical_nightly_build_count" in OPS_JS
    assert "non_nightly_main_build_count" in OPS_JS
    cohort = OPS_DATA["reliability"]["cohort"]["provenance"]["cohort"]
    assert cohort["build_count"] == (
        cohort["canonical_nightly_build_count"]
        + cohort["non_nightly_main_build_count"]
    )
    assert cohort["build_count"] >= cohort["canonical_nightly_build_count"] >= 0
    assert cohort["non_nightly_main_build_count"] >= 0


def test_retry_attempts_recoveries_and_latency_use_exact_evidence():
    assert "Explicit retry attempts" in OPS_JS
    assert "retry.retry_attempts || []" in OPS_JS
    assert "Recovered fail-to-pass chains" in OPS_JS
    assert "Open exact attempt" in OPS_JS
    retry = OPS_DATA["reliability"]["retry_analysis"]
    assert len(retry["retry_attempts"]) == retry["summary"]["retry_attempt_count"]
    assert len(retry["failed_then_passed_recoveries"]) == retry["summary"][
        "failed_then_passed_recovery_count"
    ]
    assert all(row.get("job_url") for row in retry["retry_attempts"])
    assert all(
        row.get("failed_url") and row.get("passed_url")
        for row in retry["failed_then_passed_recoveries"]
    )

    retry_surface = OPS_JS.split("if (state.analyticsView === 'retries')", 1)[1].split(
        "const latencyRows", 1
    )[0]
    assert "openGroupDetail" not in retry_surface
    latency_surface = OPS_JS.split("const latencyRows", 1)[1].split(
        "async function renderPerf", 1
    )[0]
    assert "groupReliabilityByRef(reliability, r.evidence_ref)" in latency_surface
    assert "groupReliability(reliability, r.name)" not in latency_surface


def test_history_surfaces_have_exact_or_published_source_assets():
    assert "SOURCE_ASSETS" in OPS_JS
    assert "historyPointSources" in OPS_JS
    assert "Open published source data" in OPS_JS
    assert "evidenceAsset: SOURCE_ASSETS.queueHistory" in OPS_JS
    assert "Open published all-main history" in OPS_JS
    assert "Inspect published queue history" in OPS_JS


def test_trajectory_uses_current_all_main_observations_not_stale_hotness():
    assert "trajectoryRowsFromReliability" in OPS_JS
    assert 'const windowHours = {"24h": 24, "72h": 72, "7d": 168, "30d": 720}' in OPS_JS
    assert "reliabilityCatalog(reliability)" in OPS_JS
    assert "strict catalog ID" in OPS_JS
    assert "Open exact Buildkite evidence for catalog ID" in OPS_JS
    assert "fetchJSON('data/vllm/ci/hotness.json')" not in OPS_JS


def test_omni_is_all_fleet_and_never_infers_unsupported_history():
    assert "All current vLLM-Omni demand across the fleet" in OPS_JS
    assert "NON-AMD ACTIVE JOBS" in OPS_JS
    assert "Current all-fleet Omni jobs" in OPS_JS
    assert "activeJobs, integer(activeJobs.length)" in OPS_JS
    assert "hasWaiting && !hasRunning" in OPS_JS
    assert "no Omni history is inferred" in OPS_JS
    assert "All-fleet running" in OPS_JS
    assert "AMD running" in OPS_JS
    jobs = OPS_DATA["omni"]["current_jobs"]
    current = OPS_DATA["omni"]["current"]
    assert len(jobs["pending"]) == current["waiting"]
    assert len(jobs["running"]) == current["running"]
    assert all(
        job.get("workload") == "omni"
        and job.get("url", "").startswith("https://buildkite.com/")
        for state in ("pending", "running")
        for job in jobs[state]
    )


def test_release_layout_scroll_accessibility_and_home_reconciliation():
    assert "ops-chart-viewport" in OPS_JS
    assert ".ops-page .ops-chart-viewport" in OPS_CSS
    assert "--ops-chart-height: 210px" in OPS_CSS
    assert "contain: layout" in OPS_CSS
    assert "max-height: var(--ops-chart-height)" in OPS_CSS
    assert ".ops-page .ops-perf-provenance > *" in OPS_CSS
    assert "overflow-wrap: anywhere" in OPS_CSS
    assert "function _resetRouteScroll" in DASHBOARD_JS
    assert "window.scrollTo(0, 0)" in DASHBOARD_JS
    assert "main.scrollTop = 0" in DASHBOARD_JS
    assert "Filter workload trajectory by workload" in OPS_JS
    assert "Search workload trajectory test groups" in OPS_JS
    assert "ALL-FLEET QUEUE ACTIVITY" in OPS_JS
    assert "queueScope: 'all'" in OPS_JS
    assert "unknown = ' + integer(matrix.hardware_cells)" in OPS_JS


def test_hotness_rates_accept_fraction_or_explicit_percent():
    assert "function hotnessRatePercent" in OPS_JS
    assert "row.fail_rate_percent" in OPS_JS
    assert "row.incident_rate_pct" in OPS_JS
    assert "unit === 'percent' || unit === 'pct'" in OPS_JS
    assert "unit === 'fraction' || unit === 'ratio'" in OPS_JS
    assert "raw >= 0 && raw <= 1 ? raw * 100 : raw" in OPS_JS


def test_operations_components_are_scoped_and_responsive():
    assert ".ops-page .ops-status-strip" in OPS_CSS
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in OPS_CSS
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in OPS_CSS
    assert ".ops-page .ops-table-scroll" in OPS_CSS
    assert ".ops-page .ops-table.is-compact" in OPS_CSS
    assert ".ops-page .ops-table.is-wide" in OPS_CSS
    assert "min-width: 640px" not in OPS_CSS
    assert ".ops-page .ops-chart-stage" in OPS_CSS
    assert ".ops-page .ops-detail-fields" in OPS_CSS
    assert "@media (min-width: 1100px) and (max-width: 1279px)" in OPS_CSS
    assert "@media (max-width: 767px)" in OPS_CSS
    assert "@media (max-width: 420px)" in OPS_CSS
    assert "body.ops-v2.ops-drawer-open #sidebar" in OPS_CSS
    assert "body.ops-v2.ops-drawer-open #ops-nav-backdrop" in OPS_CSS
