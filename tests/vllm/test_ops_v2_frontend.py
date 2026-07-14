"""Static contracts for the Signal Desk v2 frontend boundary."""

import json

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "docs" / "index.html").read_text()
OPS_JS = (ROOT / "docs" / "assets" / "js" / "ops-v2.js").read_text()
OPS_CSS = (ROOT / "docs" / "assets" / "css" / "ops-v2.css").read_text()
DASHBOARD_CSS = (ROOT / "docs" / "assets" / "css" / "dashboard.css").read_text()
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


def test_test_group_history_switches_cohorts_with_clickable_outcome_evidence():
    for contract in (
        "function isNightlyObservation",
        "function observationHistoryPoint",
        "function historyOutcomeTone",
        "function historyRunCell",
        "function historyIncidentRow",
        "All main",
        "Nightly only",
        "Test-group history cohort",
        "Test-group reliability",
        "Select test group for historical analysis",
        "Outcome timeline",
        "Incidents to inspect",
        "RETAINED PASS RATE",
        "CURRENT SIGNAL",
        "LAST INCIDENT",
        "TYPICAL COMPLETION",
        "Outcome history",
        "Completion and queue wait",
        "Historical outcomes, latency, and exact Buildkite evidence",
    ):
        assert contract in OPS_JS
    assert "observation.build_kind" in OPS_JS
    assert "exactPipelineEvidenceUrl(observation, sourcePipeline)" in OPS_JS
    assert "analytics_group" in OPS_JS
    assert "analytics_cohort" in OPS_JS
    assert "The source retains up to 60 exact observations" in OPS_JS
    assert "Pass and incident history" not in OPS_JS
    assert "Rolling reliability" not in OPS_JS

    for contract in (
        ".ops-page .ops-history-snapshot",
        ".ops-page .ops-history-detail-grid",
        ".ops-page .ops-history-batches",
        ".ops-page .ops-run-cell",
        ".ops-page .ops-incident-row",
        ".ops-v2:has(#main-content > .ops-page.active) > footer",
    ):
        assert contract in OPS_CSS
    assert ".ops-v2:has(#main-content > .ops-page.active) footer {" not in OPS_CSS
    assert "body > footer {" in DASHBOARD_CSS
    assert "\nfooter {" not in DASHBOARD_CSS

    groups = OPS_DATA["reliability"]["group_catalog"]
    assert any(
        {row.get("build_kind") for row in group.get("observations", [])}
        >= {"nightly", "main"}
        for group in groups
    )
    assert all(
        row.get("job_url", "").startswith("https://buildkite.com/vllm/ci/builds/")
        for group in groups
        for row in group.get("observations", [])
    )


def test_amd_health_and_platform_comparison_are_distinct_first_visit_surfaces():
    for contract in (
        "function renderAmdHealth",
        "function openAmdCatalog",
        "function openAmdGroupDetail",
        "AMD health by nightly",
        "Latest health by hardware variant",
        "All AMD test groups",
        "AMD nightly test health",
        "AMD-first, upstream-only flake evidence",
        "function platformComparison",
        "function openPlatformComparisonDetail",
        "function renderPlatformFlakes",
        "All AMD groups remain visible",
        "Browse all ",
        "AMD flake comparison",
    ):
        assert contract in OPS_JS
    for contract in (
        ".ops-page .ops-history-explorer",
        ".ops-page .ops-cluster-section",
        ".ops-page .ops-cluster-grid",
        ".ops-page .ops-cluster-tile",
        ".ops-page .ops-amd-cluster-grid",
    ):
        assert contract in OPS_CSS
    assert "name: 'flake-comparison'" in OPS_JS
    assert "comparisonFlakeColumns" in OPS_JS
    assert "renderGroupOverviewCharts(host, catalog" not in OPS_JS

    health = OPS_DATA["amd_test_health"]
    summary = health["summary"]
    latest = summary["latest_state_counts"]
    assert health["source_pipeline"] == "amd-ci"
    assert summary["build_count"] == 30
    assert summary["latest_group_count"] == sum(latest.values())
    assert latest["soft"] > 0
    assert latest["hard"] >= 0
    assert len({row["id"] for row in health["group_catalog"]}) == summary["group_count"]
    assert all(
        observation["url"].startswith("https://buildkite.com/vllm/amd-ci/builds/")
        for row in health["group_catalog"]
        for observation in row["observations"]
    )

    comparison = OPS_DATA["reliability"]["platform_comparison"]
    assert comparison["available"] is True
    assert comparison["source_pipeline"] == "ci"
    assert comparison["summary"]["amd_base_group_count"] == len(comparison["rows"])
    assert comparison["summary"]["matched_base_group_count"] > 0
    assert comparison["summary"]["comparable_base_group_count"] + comparison["summary"]["review_required_base_group_count"] == len(comparison["rows"])
    assert all(row["amd"]["variant_count"] > 0 for row in comparison["rows"])
    assert all(isinstance(row["match_issues"], list) for row in comparison["rows"])
    assert all(row["comparison_eligible"] == (row["match_status"] == "exact_cuda_pair") for row in comparison["rows"])
    assert all(
        row["amd"]["variant_count"] == row["cuda"]["variant_count"] == 1
        for row in comparison["rows"]
        if row["comparison_eligible"]
    )
    assert comparison["summary"]["amd"]["child_retry_attempts"] <= comparison["summary"]["amd"]["retry_involved_attempts"]


def test_flake_visualizations_compare_amd_and_exact_cuda_equivalents():
    for contract in (
        "Highest AMD incident frequencies",
        "AMD INCIDENT FREQUENCY",
        "PAIRED AMD VS CUDA",
        "EXACT CUDA PAIRS",
        "AMD incidents",
        "CUDA reference",
        "AMD attempts / 100 builds",
        "Inspect exact AMD and CUDA variants",
    ):
        assert contract in OPS_JS
    assert "row.amd.incident_rate_pct" in OPS_JS
    assert "row.cuda.incident_rate_pct" in OPS_JS
    assert "openPlatformComparisonDetail" in OPS_JS
    assert "if (raw === null || raw === undefined || raw === '') return '-'" in OPS_JS
    assert "percentileValue(p90Values, 0.5)" in OPS_JS


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


def test_every_shared_popup_has_stack_aware_back_navigation():
    for contract in (
        "let overlayStack = []",
        "function backOverlay()",
        "Back to previous dialog",
        "Back to dashboard",
        "activeOverlay.root.hidden = true",
        "activeOverlay = overlayStack.pop()",
        "restoreOverlayCharts(activeOverlay)",
    ):
        assert contract in OPS_JS
    assert "add(header, [back, heading, close])" in OPS_JS
    assert ".ops-v2 .ops-overlay-back" in OPS_CSS
    assert ".ops-v2 .ops-overlay[hidden]" in OPS_CSS


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
        "Upstream pass history",
        "Upstream nightly streak",
        "Last upstream incident",
        "History evidence",
    ):
        assert visible_label in OPS_JS
    assert "const latestEvidence = (((group.latest_amd_result || {}).evidence) || []).filter" in OPS_JS
    assert "const historyEvidence = (group.evidence || []).filter" in OPS_JS
    assert "exactPipelineEvidenceUrl(row, 'amd-ci')" in OPS_JS
    assert "exactPipelineEvidenceUrl(row, 'ci')" in OPS_JS
    assert "Latest AMD execution" in OPS_JS
    assert "Upstream history" in OPS_JS
    assert "const latest = explicitLatest || failure || null" in OPS_JS
    assert "explicitLatest || failure || observed" not in OPS_JS
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
    assert "No queue in scope reported a current p95" in OPS_JS
    assert "agentMeasurements" in OPS_JS
    assert "function hasAgentMeasurement" in OPS_JS
    assert "connected_agents_available" in OPS_JS
    assert "'active_jobs', 'webhook', 'job_scan'" in OPS_JS
    assert "countProvenance" in OPS_JS
    assert "count source: ' + countProvenance" in OPS_JS
    assert "P95 QUEUE LEADER" in OPS_JS
    assert "SAMPLED P99 LEADER" in OPS_JS
    assert "p95 official/fallback" in OPS_JS
    assert "p99 scheduled sample" in OPS_JS
    assert "waitSampleCount" in OPS_JS
    assert "waitSourceDetail" in OPS_JS
    assert "minutes === null || minutes === undefined" in OPS_JS
    assert "Array.isArray(queueBlock.history)" in OPS_JS
    assert "queueBlock.history_summary" in OPS_JS


def test_queue_history_has_selectable_wait_and_pressure_visualizations():
    for contract in (
        "queueHistoryQueue: 'fleet'",
        "queue_history_queue",
        "function queueWaitHistoryPoint",
        "function queuePressureRows",
        "Select queue for historical activity and wait time",
        "const selectedHistory = state.queueHistoryQueue === 'fleet'",
        "All AMD queues combined",
        "Worst individual queue wait at each snapshot",
        "Combined scope has two different reducers",
        "p95Queues",
        "p99Queues",
        "Worst sampled p99 queue",
        "Queue pressure against retained baseline",
        "Historical p95",
        "p99 scheduled sample",
    ):
        assert contract in OPS_JS
    assert "they are not fleet percentiles" in OPS_JS
    assert "missing waits are not rendered as zero" in OPS_JS
    assert ".ops-page .ops-wait-leader-grid" in OPS_CSS
    assert ".ops-page .ops-wait-leader" in OPS_CSS

    history = OPS_DATA["queue"]["history"]
    assert len(history) >= 2
    assert any(
        row.get("p50_wait_source") or row.get("p95_wait_source")
        for snapshot in history
        for row in snapshot.get("queues", {}).values()
    )


def test_workload_anomaly_views_compare_recent_and_baseline_evidence():
    for contract in (
        "function trajectoryAnomaliesFromReliability",
        "function openTrajectoryAnomalyHistory",
        "Execution-frequency changes",
        "Completion-time regressions",
        "Abnormal test-group activity",
        "Latest builds / day",
        "Prior builds / day",
        "Median change",
        "Abnormal activity method",
    ):
        assert contract in OPS_JS
    assert "recentCount >= 2" in OPS_JS
    assert "cadenceRecentCount >= 4" in OPS_JS
    assert "cadenceBaselineCount >= 4" in OPS_JS
    assert "function executionCadencePerDay" in OPS_JS
    assert "function trajectoryAnomalyObservations" in OPS_JS
    assert "Number(row.frequencyChangePct) >= 25" in OPS_JS
    assert "Number(row.durationChangePct) >= 15" in OPS_JS
    assert "queueHistoryQueue: queueName" in OPS_JS
    assert "Open exact cadence, baseline, and recent Buildkite history" in OPS_JS


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
    assert "AMD nightlies" in OPS_JS
    assert "All main" in OPS_JS
    assert "AMD/CUDA comparison unavailable" in OPS_JS
    assert "will not substitute unmatched hardware or a different pipeline" in OPS_JS
    assert "reliabilityCatalog" in OPS_JS
    assert "evidence_ref" in OPS_JS
    assert "canonical_nightly_build_count" in OPS_JS
    assert "non_nightly_main_build_count" in OPS_JS
    assert "canonicalReliability(ops)" in OPS_JS
    assert "return reliabilityForPipeline(ops, 'ci')" in OPS_JS
    assert "AMD health is primary" in OPS_JS
    assert "exact CUDA-name equivalents" in OPS_JS
    assert "{id: 'groups', label: 'AMD test health'}" in OPS_JS
    assert "{id: 'flakes', label: 'Flake comparison'}" in OPS_JS
    assert "{id: 'retries', label: 'Retry comparison'}" in OPS_JS
    assert "{id: 'latency', label: 'Latency comparison'}" in OPS_JS


def test_nightly_pipeline_selector_defaults_amd_and_is_route_backed():
    assert "analyticsPipeline: 'amd-ci'" in OPS_JS
    assert "['analyticsPipeline', 'analytics_pipeline', ['ci', 'amd-ci']]" in OPS_JS
    assert "nightlyForPipeline(ops, state.analyticsPipeline)" in OPS_JS
    assert "{id: 'ci', label: 'Upstream parity'}" in OPS_JS
    assert "{id: 'amd-ci', label: 'AMD'}" in OPS_JS
    assert "'Nightly pipeline'" in OPS_JS
    assert "AMD is the default operational signal" in OPS_JS
    assert "retained for parity checks" in OPS_JS


def test_retry_attempts_recoveries_and_latency_use_exact_evidence():
    assert "function renderPlatformRetries" in OPS_JS
    assert "function renderPlatformLatency" in OPS_JS
    assert "retry.retry_attempts || []" in OPS_JS
    assert "Retry-involved attempts" in OPS_JS
    assert "Confirmed retry recoveries" in OPS_JS
    assert "Open failed log" in OPS_JS
    assert "Open passing log" in OPS_JS
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

    comparison = OPS_DATA["reliability"]["platform_comparison"]
    catalog_ids = {row["id"] for row in OPS_DATA["reliability"]["group_catalog"]}
    assert all(
        group_id in catalog_ids
        for row in comparison["rows"]
        for side in (row["amd"], row["cuda"])
        for group_id in side["group_ids"]
    )
    assert "comparisonGroupById(reliability, variant.group_id)" in OPS_JS
    assert "exactPipelineEvidenceUrl(attempt, 'ci')" in OPS_JS


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
    assert "Recent AMD build trajectory" not in OPS_JS
    assert "trajectoryAmd" not in OPS_JS
    assert "appendHardwareOptions(hwSelect, hardware, state.trajectoryHardware)" in OPS_JS
    assert "{label: 'AMD', matches:" in OPS_JS
    assert "including AMD MI mirror queues" in OPS_JS


def test_pipeline_evidence_links_fail_closed_in_the_renderer():
    assert "function pipelineUrlMatches" in OPS_JS
    assert "function exactPipelineEvidenceUrl" in OPS_JS
    assert "function exactPipelineBuildUrl" in OPS_JS
    assert "parsed.host !== 'buildkite.com'" in OPS_JS
    assert "parsed.protocol !== 'https:'" in OPS_JS
    assert "suffix[0] !== 'steps'" in OPS_JS
    assert "exactPipelineEvidenceUrl(row, sourcePipeline)" in OPS_JS
    assert "exactPipelineEvidenceUrl(row, 'amd-ci')" in OPS_JS
    assert "exactPipelineEvidenceUrl(row, 'ci')" in OPS_JS
    assert "row.build_url || buildUrl(pipeline, row.build_number)" not in OPS_JS
    assert "(ops || {}).amd_reliability" not in OPS_JS


def test_empty_tables_and_mobile_evidence_have_single_scroll_surfaces():
    assert "if (!rows.length)" in OPS_JS
    assert "wrap.classList.add('is-empty')" in OPS_JS
    assert ".ops-page .ops-evidence-table-host .ops-table-scroll" in OPS_CSS
    assert "max-height: none" in OPS_CSS
    assert '.ops-segmented[aria-label="CI Analytics view"]' in OPS_CSS


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
    assert "integer(unknownCells) + ' unknown of ' + integer(matrix.hardware_cells)" in OPS_JS


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


def test_dense_tables_use_explicit_colgroups_and_scroll_geometry():
    assert "const colgroup = n('colgroup')" in OPS_JS
    assert "table.append(colgroup)" in OPS_JS
    assert "table.classList.add('has-column-geometry')" in OPS_JS
    assert "table.dataset.geometry = geometry.name || 'automatic'" in OPS_JS
    assert "const columnWidths = columns.map" in OPS_JS
    assert "column.sticky ? '280px' : column.numeric ? '110px' : '160px'" in OPS_JS
    assert "--ops-table-min-width" in OPS_JS
    for geometry in (
        "gating",
        "amd-health-browser",
        "amd-current-incidents",
        "retry-attempts",
        "retry-recoveries",
        "latency",
        "reliability-browser",
        "nightly",
        "trajectory-anomalies",
    ):
        assert f"name: '{geometry}'" in OPS_JS
    assert "table.has-column-geometry" in OPS_CSS
    assert "table-layout: fixed" in OPS_CSS
    assert "width: max(100%, var(--ops-table-min-width))" in OPS_CSS
    assert "min-width: var(--ops-table-min-width)" in OPS_CSS
    assert ".ops-page .ops-table-wrap,\n.ops-page .ops-table-scroll" not in OPS_CSS


def test_table_headers_and_cells_share_explicit_alignment_contract():
    assert "th.dataset.align = alignment" in OPS_JS
    assert "td.dataset.align = alignment" in OPS_JS
    assert "#main-content .ops-page .ops-table .is-numeric" in OPS_CSS
    assert '#main-content .ops-page .ops-table [data-align="numeric"]' in OPS_CSS
    assert "td.is-numeric > .ops-link-button" in OPS_CSS
    assert "margin-left: auto" in OPS_CSS
