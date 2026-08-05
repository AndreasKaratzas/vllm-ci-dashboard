"""Static contracts for the vLLM AMD CI Operations frontend boundary."""

import json
import shutil
import subprocess

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "docs" / "index.html").read_text()
OPS_JS = (ROOT / "docs" / "assets" / "js" / "ops-v2.js").read_text()
LEGACY_HEALTH_JS = (
    ROOT / "docs" / "assets" / "js" / "ci-health.js"
).read_text()
OPS_CSS = (ROOT / "docs" / "assets" / "css" / "ops-v2.css").read_text()
DASHBOARD_CSS = (ROOT / "docs" / "assets" / "css" / "dashboard.css").read_text()
DASHBOARD_JS = (ROOT / "docs" / "assets" / "js" / "dashboard.js").read_text()
OPS_DATA = json.loads((ROOT / "data" / "vllm" / "ci" / "operations_v2.json").read_text())
OPS_MANIFEST_PATH = ROOT / "data" / "vllm" / "ci" / "operations_v2_manifest.json"
OPS_MANIFEST = json.loads(OPS_MANIFEST_PATH.read_text())


def test_v2_assets_and_mobile_shell_are_loaded():
    assert "<title>vLLM AMD CI Operations</title>" in INDEX
    assert '<span class="ops-brand-kicker">vLLM</span>' in INDEX
    assert "<h1>AMD CI Operations</h1>" in INDEX
    assert "<strong>vLLM</strong>" in INDEX
    assert "<span>AMD CI Operations</span>" in INDEX
    assert "Signal Desk" not in INDEX
    assert "Signal Desk" not in OPS_JS
    assert "assets/css/ops-v2.css" in INDEX
    assert "assets/js/ops-v2.js" in INDEX
    assert "window.__DASHBOARD_V2__ = true" in INDEX
    assert 'id="ops-menu-toggle"' in INDEX
    assert 'id="ops-nav-backdrop"' in INDEX


def test_operations_data_is_lazy_loaded_with_bounded_first_render_payloads():
    assert "operations_v2_manifest.json" in OPS_JS
    assert "function loadOperations" in OPS_JS
    assert "function operationSectionNames" in OPS_JS
    assert "return loadOperationSections(manifest.shell, operationSectionNames(tabId))" in OPS_JS
    assert "const ops = await loadOperations(tabId)" in OPS_JS
    assert "fetchJSON('data/vllm/ci/operations_v2.json')" not in OPS_JS
    assert "using compatibility snapshot" not in OPS_JS

    assert OPS_MANIFEST["schema_version"] == 2
    assert OPS_MANIFEST["bundle_version"] == 1
    assert OPS_MANIFEST["generated_at"] == OPS_DATA["generated_at"]
    assert "reliability" not in OPS_MANIFEST["shell"]
    assert "amd_agent_health" not in OPS_MANIFEST["shell"]
    assert set(OPS_MANIFEST["sections"]) >= {
        "nightly",
        "amd_test_health",
        "amd_agent_health",
        "reliability",
        "definition_parity",
        "ownership",
        "queue",
        "omni",
        "diagnostics",
    }

    manifest_bytes = OPS_MANIFEST_PATH.stat().st_size
    section_bytes = {
        name: descriptor["bytes"]
        for name, descriptor in OPS_MANIFEST["sections"].items()
    }
    assert manifest_bytes < 2_000_000
    assert (
        manifest_bytes
        + section_bytes["nightly"]
        + section_bytes["amd_test_health"]
    ) < 12_000_000
    assert section_bytes["queue"] < 6_000_000
    assert manifest_bytes < (ROOT / "data" / "vllm" / "ci" / "operations_v2.json").stat().st_size * 0.05
    site_builder = (ROOT / "scripts" / "build_site.py").read_text()
    assert "materialize_operations_bundle(DATA, output_dir / \"data\", manifest)" in site_builder
    assert "write_snapshot_bundle(output, payload, write_monolith=False" in site_builder


def test_chart_library_does_not_block_dashboard_boot():
    assert '<script src="https://cdn.jsdelivr.net/npm/chart.js' not in INDEX
    assert "const CHART_LIBRARY_URL" in OPS_JS
    assert "function loadChartLibrary" in OPS_JS
    assert "script.async = true" in OPS_JS
    assert "if (canvas.isConnected) drawChart(key, canvas, config)" in OPS_JS


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


def test_ci_ownership_renderer_is_reusable_and_removed_from_ci_health():
    for contract in (
        "function openOwnershipAreaDetail",
        "function renderOwnership(host, ops)",
        "const ownership = (ops || {}).ownership || {};",
        "renderOwnership: renderOwnership",
        "CI test-area ownership",
        "Regional working-hours routing",
        "Missing or invalid working-hour schedules",
        "Europe/Belgrade",
        "America/Chicago",
        "UNMAPPED TARGETS",
        "GitHub assignability is checked before mutation",
        "Regression issues tag the selected owner and verified assignee",
        "CC each remaining ranked area owner once",
    ):
        assert contract in OPS_JS
    assert "private PTO" not in OPS_JS
    assert "Private availability" not in OPS_JS
    assert "availability.fresh === true" in OPS_JS
    assert (
        "['healthView', 'health_view', "
        "['overview', 'targets', 'gating', 'coverage', 'diagnostics']]"
    ) in OPS_JS
    assert "{id: 'ownership', label: 'CI ownership'}" not in OPS_JS
    assert "if (state.healthView === 'ownership')" not in OPS_JS
    assert "gating.ownership" not in OPS_JS
    assert "architectureSignalStateRank(ownershipAreaState(left))" in OPS_JS
    assert "compareText(left.source_file, right.source_file)" in OPS_JS
    assert OPS_JS.index("function renderOwnership(host, ops)") < OPS_JS.index(
        "async function renderHealth"
    )


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
        "Outcome trend",
        "function trailingPassStats",
        "Trailing 10-run pass rate",
        "Current trailing 10:",
        "bar color is the exact result",
        "stepped: 'after'",
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
        "Retained AMD job-variant catalog",
        "AMD nightly test health",
        "AMD-first, upstream-only incident evidence",
        "function platformComparison",
        "function openPlatformComparisonDetail",
        "function renderPlatformFlakes",
        "ACTIVE AMD GROUPS",
        "AMD incident comparison",
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
    assert summary["build_count"] == len(health["builds"])
    assert summary["build_count"] > 0
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


def test_amd_health_keeps_latest_and_historical_job_variant_counts_distinct():
    segment = OPS_JS[
        OPS_JS.index("function renderAmdHealth"):
        OPS_JS.index("const AGENT_WINDOW_DAYS")
    ]
    for contract in (
        "retained_group_count || summary.union_group_count || summary.group_count",
        "label: 'LATEST JOB VARIANTS'",
        "value: integer(summary.latest_group_count)",
        "older variants retained only for history",
        "const currentVariants = currentPassing.concat(currentIncidents, currentUnknown)",
        "openAmdCatalog('Latest AMD job variants'",
        "older names remain available as history and are not treated as missing incidents",
        "'Historical only'",
        "Not classified as current incidents",
    ):
        assert contract in segment

    assert "integer(summary.latest_group_count) + ' / ' + integer(summary.group_count)" not in segment
    assert "currentIncidents.length + missing.length" not in segment
    assert "!['soft', 'hard', 'missing'].includes(latest)" not in OPS_JS

    summary = OPS_DATA["amd_test_health"]["summary"]
    retained = summary.get("retained_group_count", summary["union_group_count"])
    assert retained == summary["group_count"] == len(
        OPS_DATA["amd_test_health"]["group_catalog"]
    )
    assert summary["latest_group_count"] < retained


def test_flake_visualizations_compare_amd_and_exact_cuda_equivalents():
    for contract in (
        "AMD incident frequency - ",
        "Observation window",
        "REGRESSED VS PRIOR",
        "AMD INCIDENT FREQUENCY",
        "PAIRED AMD / CUDA",
        "AMD incidents / attempts",
        "CUDA incidents / attempts",
        "vs prior window",
        "AMD attempts / 100 builds",
        "Inspect exact AMD and CUDA variants",
    ):
        assert contract in OPS_JS
    assert "row.amd.incident_rate_pct" in OPS_JS
    assert "row.cuda.incident_rate_pct" in OPS_JS
    assert "openPlatformComparisonDetail" in OPS_JS
    assert "if (raw === null || raw === undefined || raw === '') return '-'" in OPS_JS
    assert "percentileValue(p90Values, 0.5)" in OPS_JS


def test_recent_flake_and_retry_windows_are_timestamped_and_route_backed():
    for contract in (
        "const ANALYTICS_WINDOW_HOURS = {'1h': 1, '3h': 3, '6h': 6, '24h': 24, '7d': 168, '30d': 720}",
        "function analyticsWindowBounds",
        "function platformComparisonForWindow",
        "function observationInRange",
        "analytics_window",
        "Movement compares this window with the immediately preceding equal-length window.",
        "Movement compares timestamped child retries with the immediately preceding equal-length window.",
        "AMD child retry share",
        "AMD recovered share",
    ):
        assert contract in OPS_JS
    assert "observed_at" in OPS_JS
    assert ".ops-page .ops-analytics-window-toolbar" in OPS_CSS


def test_architecture_and_test_group_history_show_exact_counts_at_a_glance():
    for contract in (
        "AMD architecture health",
        "configured groups",
        "passing",
        "incident",
        "unobserved",
        "Complete test-group history",
        "Latest 30 exact runs - oldest to newest",
        "Explore all groups",
        "MEDIAN PASS RATE",
        "Outcome timeline",
        "--ops-history-track-width",
        "renderGroupHistoryExplorer(host, reliabilityCatalog(reliability), ops, reliability)",
    ):
        assert contract in OPS_JS
    for contract in (
        ".ops-page .ops-architecture-scorecard",
        ".ops-page .ops-architecture-row",
        ".ops-page .ops-architecture-bar",
        ".ops-page .ops-architecture-metrics",
        ".ops-page .ops-history-map-row",
        ".ops-page .ops-history-map-track",
        ".ops-page .ops-history-track",
    ):
        assert contract in OPS_CSS
    assert "margin: 10px 0;" in OPS_CSS


def test_operational_routes_prune_unrelated_state_and_perf_has_return_control():
    for contract in (
        "const ROUTE_QUERY_KEYS",
        "const ROUTE_DEFAULTS",
        "function pruneRouteQuery",
        "key.startsWith('ops_') && !allowed.has(key)",
        "Back to all performance models",
        "\\u2190 All models",
        "perf_model",
        "perf_device",
    ):
        assert contract in OPS_JS
    assert ".ops-page .ops-perf-back" in OPS_CSS


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


def test_definition_parity_is_source_scoped_and_not_presented_as_runtime_health():
    for removed_label in (
        "Current target",
        "Readiness",
        "Target origin",
        "REVIEWED TARGETS",
        "LINKED AMD RESULTS",
    ):
        assert removed_label not in OPS_JS
    for visible_label in (
        "Definition parity",
        "AMD IDENTITY FAMILIES",
        "AMD FAMILY COVERAGE",
        "INLINE MIRRORS",
        "UNLINKED DEFINITIONS",
        "Definition coverage, not passing test groups.",
        "Source-definition comparison",
        "Direct command twin",
        "Mirror-linked standalone variants",
        "Additional AMD variant",
        "AMD-only standalone",
        "Inline mirror inventory",
        "Open pinned vLLM commit",
    ):
        assert visible_label in OPS_JS
    assert "ops.definition_parity || {}" in OPS_JS
    assert "row.match_method === 'command_twin'" in OPS_JS
    assert "definitionSummary.amd_identity_families" in OPS_JS
    assert "definitionSummary.covered_identity_families" in OPS_JS
    assert "definitionSummary.amd_only_identity_families" in OPS_JS
    assert "summary.amd_identity_families" in OPS_JS
    assert "summary.covered_identity_families" in OPS_JS
    assert "summary.amd_only_identity_families" in OPS_JS
    assert "summary.identity_family_coverage_rate_pct" in OPS_JS
    assert "summary.covered" in OPS_JS
    assert "summary.direct_matches" in OPS_JS
    assert "summary.inline_mirror_variants" in OPS_JS
    assert "summary.additional_variants" in OPS_JS
    assert "parity nodes source-covered" in OPS_JS
    assert "YAML-derived identity families" in OPS_JS
    assert (
        "label: 'AMD DEFINITIONS', value: "
        "integer(definitionSummary.total_amd_steps)"
    ) not in OPS_JS
    assert (
        "label: 'AMD DEFINITIONS', value: integer(summary.total_amd_steps)"
    ) not in OPS_JS
    assert (
        "label: 'AMD DEFINITION COVERAGE', value: "
        "integer(summary.covered) + ' / ' + integer(summary.total_amd_steps)"
    ) not in OPS_JS
    for legacy_family_field in (
        "s.amd_identity_families",
        "s.covered_identity_families",
        "s.amd_only_identity_families",
        "s.identity_family_coverage_rate_pct",
    ):
        assert legacy_family_field in LEGACY_HEALTH_JS
    assert "parity nodes source-covered" in LEGACY_HEALTH_JS
    assert "parity.inline_mirror_variants" in OPS_JS
    assert "parity.additional_variants" in OPS_JS
    assert "row.amd_route_similarity" in OPS_JS
    assert "row.inline_mirror_command_similarity" in OPS_JS
    assert "row.amd_source_url" in OPS_JS
    assert "row.nvidia_source_url" in OPS_JS
    assert "Search 127 reviewed groups" not in OPS_JS
    assert "matrixData.rows || []" in OPS_JS
    assert "matrixData.rows || []).slice" not in OPS_JS


def test_published_definition_parity_reconciles_coverage_and_mirror_evidence():
    parity = OPS_DATA["definition_parity"]
    summary = parity["summary"]

    assert len(parity["matches"]) == summary["direct_matches"]
    assert (
        len(parity["inline_mirror_variants"])
        == summary["inline_mirror_variants"]
    )
    assert len(parity["additional_variants"]) == summary["additional_variants"]
    assert len(parity["amd_only"]) == summary["amd_only"]
    assert len(parity["nvidia_only"]) == summary["nvidia_only"]
    assert len(parity["mirrors"]) == summary["mirrors"]
    assert summary["covered"] == (
        summary["direct_matches"]
        + summary["inline_mirror_variants"]
        + summary["additional_variants"]
    )
    assert summary["covered"] + summary["amd_only"] == summary["total_amd_steps"]
    covered_rows = [
        *parity["matches"],
        *parity["inline_mirror_variants"],
        *parity["additional_variants"],
    ]
    covered_family_keys = {
        row["amd_identity_family_key"]
        for row in covered_rows
    }
    amd_only_member_family_keys = {
        row["amd_identity_family_key"]
        for row in parity["amd_only"]
    }
    all_family_keys = covered_family_keys | amd_only_member_family_keys
    assert len(all_family_keys) == summary["amd_identity_families"]
    assert len(covered_family_keys) == summary["covered_identity_families"]
    assert (
        len(amd_only_member_family_keys - covered_family_keys)
        == summary["amd_only_identity_families"]
    )
    assert (
        len(covered_family_keys & amd_only_member_family_keys)
        == summary["partially_covered_identity_families"]
    )
    assert (
        summary["total_amd_steps"] - len(all_family_keys)
        == summary["identity_family_replica_rows"]
    )
    assert summary["match_rate_pct"] == summary["direct_match_rate_pct"]
    assert (
        summary["avg_command_similarity_pct"]
        == summary["direct_avg_command_similarity_pct"]
    )
    assert "covered_avg_command_similarity_pct" in summary

    basic = next(
        row
        for row in parity["inline_mirror_variants"]
        if row["amd_label"] == "Basic Correctness"
    )
    assert not any(
        row["label"] == "Basic Correctness"
        for row in parity["amd_only"]
    )
    assert basic["nvidia_label"] == "Basic Correctness"
    assert basic["match_method"] == "inline_mirror_variant"
    assert basic["amd_definition_id"]
    assert basic["nvidia_definition_id"]
    assert basic["amd_source_url"]
    assert basic["nvidia_source_url"]
    for field in (
        "command_similarity",
        "amd_route_similarity",
        "inline_mirror_command_similarity",
    ):
        assert field in basic

    for mirror in parity["mirrors"]:
        assert mirror["nvidia_definition_id"]
        assert mirror["source_url"]
        assert isinstance(mirror["commands_overridden"], bool)
        assert isinstance(mirror["amd_commands"], list)
        assert isinstance(mirror["nvidia_commands"], list)


def test_runtime_target_incident_attention_loads_and_filters_runtime_gating():
    for contract in (
        "{id: 'targets', label: 'Runtime targets'}",
        "if (state.healthView === 'targets') return ['gating']",
        "healthView: 'targets', healthResult: 'incident'",
        "Runtime AMD target signal, not definition parity.",
        "const incidentTargets = allTargets.filter(isTargetIncident)",
        "filters[state.healthResult]",
        "openGatingDetailWithEvidence(row, ops)",
        "non-passing latest results first",
    ):
        assert contract in OPS_JS
    assert (
        "healthView: 'gating', healthResult: 'incident'"
        not in OPS_JS
    )


def test_runtime_target_resolution_is_explained_and_drillable():
    for contract in (
        "function targetResolutionPresentation",
        "function targetAssessmentText",
        "function targetNoSignalBreakdown",
        "No one-to-one AMD definition",
        "Target mapping needs review",
        "Ambiguous AMD mapping",
        "Not observed in latest AMD build",
        "runtime_resolution",
        "source_commits",
        "source_alignment",
        "source_urls",
        "AMD matrix commit",
        "Definition parity commit",
        "Resolution method",
        "AMD definitions",
        "Plan note",
        "mapping review",
    ):
        assert contract in OPS_JS
    assert "targetAssessmentText(row)" in OPS_JS
    assert "resolution.amdDefinitionLabels.join(' ')" in OPS_JS


def test_diagnostics_do_not_link_private_collector_state():
    assert "row.record.published === false ? ''" in OPS_JS
    assert 'sources[internal_source]["published"] = False' in (
        ROOT / "scripts" / "vllm" / "build_operations_snapshot.py"
    ).read_text()


def test_blocked_nightly_is_separate_from_the_latest_test_signal():
    for contract in (
        "function amdNightlyPresentation",
        "Infra blocked",
        "test groups never started",
        "latest test signal #",
        "Latest nightly has no test signal.",
        "Nightlies with test execution only; latest signal #",
        "No pass/fail movement is inferred.",
    ):
        assert contract in OPS_JS
    assert "row.has_test_results !== false" in OPS_JS
    assert "build.test_jobs_blocked" in OPS_JS


def test_nightly_assessment_uses_explicit_movement_rules():
    for contract in (
        "function amdNightlyMovement",
        "previousIncidents: recurring + fixed",
        "delta: newlyIncident - fixed",
        "movement.currentIncidents === incidentCount",
        "Running with incidents",
        "Regressed",
        "Improved",
        "Changed, net even",
        "Stable incidents",
        "Recovered",
        "No net change",
        "NIGHTLY VARIANT MOVEMENT",
        "fewer job variants",
        "provisional while Buildkite is running",
    ):
        assert contract in OPS_JS
    assert "soft ? 'Degraded'" not in OPS_JS


def test_nightly_counts_are_labeled_as_exact_job_variants():
    assert "JOB VARIANTS OBSERVED" in OPS_JS
    assert "NEW INCIDENT VARIANTS" in OPS_JS
    assert "label: 'GROUPS OBSERVED'" not in OPS_JS


def test_coverage_matrix_supports_route_safe_platform_name_and_area_sorting():
    for contract in (
        "healthCoverageSort: 'platform'",
        "'ops_health_sort'",
        "['healthCoverageSort', 'health_sort', ['platform', 'name', 'area']]",
        "const AMD_MATRIX_PLATFORM_ORDER = ['mi250', 'mi300', 'mi325', 'mi355']",
        "function sortAmdMatrixRows",
        "{id: 'platform', label: 'Platform'}",
        "{id: 'name', label: 'Test group'}",
        "{id: 'area', label: 'Test area'}",
        "Sort AMD test matrix",
        "setRouteState('ci-health', 'healthCoverageSort', sortMode, 'health_sort')",
        "headerActions: coverageSortGroup",
        "previewLabel: 'sorted rows'",
    ):
        assert contract in OPS_JS
    assert "const coverageSort = n('select', 'ops-select')" not in OPS_JS
    assert "const coverageSortToolbar" not in OPS_JS
    assert "const coverageSortGroup = n('div', 'ops-panel-header-actions')" in OPS_JS
    assert ".ops-page .ops-panel-header-trailing" in OPS_CSS


def test_architecture_signal_drilldown_sorts_nonpassing_results_before_passes_and_names():
    for contract in (
        "const AMD_ARCHITECTURE_HARD_SIGNAL_STATES",
        "'hard', 'failed', 'failing', 'incident', 'error', 'timed_out', 'broken'",
        "'canceled', 'cancelled', 'expired'",
        "const AMD_ARCHITECTURE_SOFT_SIGNAL_STATES",
        "'soft', 'soft_fail', 'soft_failed'",
        "function architectureSignalStateRank",
        "if (AMD_ARCHITECTURE_HARD_SIGNAL_STATES.has(normalized)) return 0",
        "if (AMD_ARCHITECTURE_SOFT_SIGNAL_STATES.has(normalized)) return 1",
        "if (normalized === 'passed') return 3",
        "function sortArchitectureSignalRows",
        "architectureSignalStateRank(latestState(left)) - architectureSignalStateRank(latestState(right))",
        "compareText(left.title, right.title)",
        "const selectedRows = sortArchitectureSignalRows(architectureRows(architecture), architecture.id)",
        "non-passing latest results first, then test group A-Z",
    ):
        assert contract in OPS_JS

    matrix = json.loads(
        (ROOT / "data" / "vllm" / "ci" / "amd_test_matrix.json").read_text()
    )
    mi300_rows = [
        row
        for row in matrix["rows"]
        if row.get("cells", {}).get("mi300", {}).get("exists")
    ]

    hard_states = {
        "hard", "failed", "failing", "incident", "error", "timed_out", "broken",
        "canceled", "cancelled", "expired",
    }
    soft_states = {"soft", "soft_fail", "soft_failed"}

    def sort_key(row):
        state = str(
            row.get("cells", {}).get("mi300", {}).get("latest_state")
            or "unobserved"
        ).strip().lower()
        rank = (
            0 if state in hard_states
            else 1 if state in soft_states
            else 3 if state == "passed"
            else 2
        )
        return (
            rank,
            str(row.get("title") or "").casefold(),
            str(row.get("area") or "").casefold(),
            str(row.get("id") or "").casefold(),
        )

    ordered = sorted(mi300_rows, key=sort_key)
    ranks = [sort_key(row)[0] for row in ordered]
    assert ranks == sorted(ranks)
    for rank in set(ranks):
        titles = [row["title"] for row in ordered if sort_key(row)[0] == rank]
        assert [title.casefold() for title in titles] == sorted(
            title.casefold() for title in titles
        )

    ordered_titles = [row["title"] for row in ordered]
    assert ordered_titles.index("Basic Models Tests (Other)") < ordered_titles.index(
        "e2e Scheduling (1 GPU)"
    )
    first_passing = ranks.index(3)
    assert all(rank < 3 for rank in ranks[:first_passing])
    assert all(rank == 3 for rank in ranks[first_passing:])


def test_runtime_target_sort_uses_one_shared_in_scope_text_comparator():
    shared_definition = "\n  function compareText(left, right) {"
    assert OPS_JS.count(shared_definition) == 1
    assert OPS_JS.index(shared_definition) < OPS_JS.index("async function renderHealth")
    render_health = OPS_JS.index("async function renderHealth")
    targets_start = OPS_JS.index(
        "if (state.healthView === 'targets')",
        render_health,
    )
    targets_branch = OPS_JS[
        targets_start
        :OPS_JS.index("if (state.healthView === 'gating')", targets_start)
    ]
    assert "sortRuntimeTargetRows(filters[state.healthResult] || allTargets)" in targets_branch


def test_runtime_target_and_omni_helpers_execute_in_javascript():
    if not shutil.which("node"):
        import pytest

        pytest.skip("node is not available")
    script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const sandbox = {
  window: {__OPS_V2_TEST__: true},
  document: {addEventListener: function () {}},
  console: console,
  URL: URL,
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, {filename: process.argv[1]});
const helpers = sandbox.window.OpsV2Test;
assert.ok(helpers);

const ordered = helpers.sortRuntimeTargetRows([
  {id: 'pass-e2e', label: 'e2e Scheduling (1 GPU)', area: 'Other', latest_amd_result: {state: 'passed'}},
  {id: 'unknown', label: 'Unknown Signal', area: 'Other', latest_amd_result: {state: 'unobserved'}},
  {id: 'soft', label: 'Soft Incident', area: 'Other', latest_amd_result: {state: 'soft_failed'}},
  {id: 'pass-basic', label: 'Basic Models Tests (Other)', area: 'Models', latest_amd_result: {state: 'passed'}},
  {id: 'hard', label: 'Hard Incident', area: 'Other', latest_amd_result: {state: 'failed'}},
]).map(function (row) { return row.id; });
assert.equal(JSON.stringify(ordered), JSON.stringify([
  'hard', 'soft', 'unknown', 'pass-basic', 'pass-e2e',
]));

const staleResolution = helpers.targetResolutionPresentation({
  latest_amd_result: {state: 'unknown'},
    runtime_resolution: {
    status: 'stale_target_alias',
    method: 'definition_parity',
    reason: 'Reviewed label no longer identifies the current 2-GPU definition.',
    target_identity_key: 'gpqa eval',
    amd_definition_labels: ['GPQA Eval (2xH100-2xMI300)'],
    candidate_count: 2,
    source_commits: {amd_matrix: 'abcdef1234567890', definition_parity: '123456abcdef7890'},
    source_alignment: 'different_commits',
    source_urls: {amd_matrix: 'https://example.com/amd', definition_parity: 'https://example.com/parity'},
    mapping_quality: 'partial_commands',
    command_similarity_pct: 61.9,
  },
});
assert.equal(staleResolution.label, 'Target mapping needs review');
assert.equal(staleResolution.methodLabel, 'Definition parity identity');
assert.equal(staleResolution.sourceAlignment, 'different_commits');
assert.equal(staleResolution.sourceAlignmentLabel, 'AMD matrix and definition parity use different commits');
assert.equal(staleResolution.sourceCommits.amdMatrix, 'abcdef1234567890');
assert.equal(staleResolution.amdDefinitionLabels.length, 1);
assert.equal(staleResolution.candidateCount, 2);
assert.equal(staleResolution.mappingQuality, 'partial commands');
assert.equal(staleResolution.commandSimilarityPct, 61.9);
assert.ok(helpers.targetAssessmentText({
  latest_amd_result: {state: 'unknown'},
  runtime_resolution: {
    status: 'no_amd_definition',
    reason: 'No matching test-amd.yaml definition.',
  },
}).includes('No one-to-one AMD definition'));
assert.deepEqual(
  helpers.targetNoSignalBreakdown([
    {latest_amd_result: {state: 'unknown'}, runtime_resolution: {status: 'no_amd_definition'}},
    {latest_amd_result: {state: 'unknown'}, runtime_resolution: {status: 'stale_target_alias'}},
    {latest_amd_result: {state: 'unknown'}, runtime_resolution: {status: 'ambiguous'}},
    {latest_amd_result: {state: 'unknown'}, runtime_resolution: {status: 'not_observed'}},
  ]),
  {noDefinition: 1, needsReview: 2, notObserved: 1},
);

[
  [59.9, 'lt1h'], [60, '1to3h'], [180, '3to6h'], [360, '6to12h'],
  [720, '12to24h'], [1440, '1to3d'], [4320, 'gte3d'],
].forEach(function (pair) {
  assert.equal(helpers.omniAgeBand({wait_min: pair[0]}), pair[1]);
});
assert.equal(helpers.omniAgeBand({}), '');

const historyPoints = helpers.omniHistoryPoints({history: {points: [{
  ts: '2026-04-22T10:00:00Z',
  all_fleet: {
    waiting_supported: true,
    running_supported: false,
    waiting_observed: 5,
    running_observed: 0,
    waiting_attribution: 'partial',
    running_attribution: 'unavailable',
  },
  amd: {
    waiting_supported: false,
    running_supported: false,
    waiting_observed: 0,
    running_observed: 0,
    waiting_attribution: 'unavailable',
    running_attribution: 'unavailable',
  },
}]}});
assert.equal(historyPoints[0].allWaiting, 5);
assert.equal(historyPoints[0].allRunning, null);
assert.equal(historyPoints[0].amdWaiting, null);

const start = Date.parse('2026-04-22T10:00:00Z');
const points = [0, 1, 2, 3].map(function (hours) {
  return {time: start + hours * 3600000, allWaiting: hours};
});
assert.deepEqual(
  helpers.omniWindowPoints(points, '1h').map(function (point) { return point.allWaiting; }),
  [2, 3],
);

const daily = helpers.omniDailyRows([
  {time: Date.parse('2026-04-20T23:00:00Z'), allWaiting: 2, amdWaiting: 1, waitingCoverage: 'complete'},
  {time: Date.parse('2026-04-21T23:00:00Z'), allWaiting: 5, amdWaiting: 2, waitingCoverage: 'complete'},
]);
assert.equal(daily[0].day, '2026-04-21');
assert.equal(daily[0].delta, 3);

const hourlyMapping = {
  schema_version: 2,
  generated_at: '2026-07-29T06:29:00Z',
  window: {
    collection_complete: true,
    job_created_range_exhaustive: false,
    lower_bound: false,
  },
  scope: {
    attribution: {
      parent_build_lookback_days: 3,
      job_created_range_exhaustive: false,
      exact_within_declared_source_window: true,
      limitation: 'Delayed jobs on older parent builds can be absent.',
    },
  },
  query: {
    parent_build_lookback_days: 3,
    job_created_range_exhaustive: false,
  },
  hourly: Array.from({length: 7}, function (_, hour) {
    const start = '2026-07-29T' + String(hour).padStart(2, '0') + ':00:00Z';
    const end = '2026-07-29T' + String(hour + 1).padStart(2, '0') + ':00:00Z';
    return {
      hour: start,
      end_exclusive: end,
      observed_through: hour === 6 ? '2026-07-29T06:29:00Z' : end,
      state: hour === 6 ? 'open' : 'closed',
      open: hour === 6,
      complete: hour !== 6,
      collection_complete: true,
      lower_bound: false,
      workloads: {
        omni: {mapped_jobs: 1, started_jobs: 1, mapped_gpu_slots: 2, gpu_hours: 0.5, by_queue: {amd_mi325_2: {mapped_jobs: 1, started_jobs: 1, mapped_gpu_slots: 2, gpu_hours: 0.5}}, by_pipeline: {'vllm-omni-amd-ci': {mapped_jobs: 1, started_jobs: 1, mapped_gpu_slots: 2, gpu_hours: 0.5}}},
        main: {mapped_jobs: 10, started_jobs: 8, mapped_gpu_slots: 10, gpu_hours: 2, by_queue: {}, by_pipeline: {}},
      },
    };
  }),
  daily: [],
};
const sixHourWindow = helpers.omniMappingWindow(hourlyMapping, '6h');
assert.equal(sixHourWindow.available, true);
assert.equal(sixHourWindow.resolution, 'hourly');
assert.equal(sixHourWindow.rows.length, 6);
assert.equal(sixHourWindow.rows[0].hour, '2026-07-29T01:00:00Z');
assert.equal(sixHourWindow.retainedComplete, true);
assert.equal(sixHourWindow.apiCollectionComplete, true);
assert.equal(sixHourWindow.complete, false);
assert.equal(sixHourWindow.coverageStatus, 'open');
assert.equal(sixHourWindow.lowerBound, false);
assert.equal(sixHourWindow.jobCreatedRangeExhaustive, false);
assert.equal(sixHourWindow.parentBuildLookbackDays, 3);
assert.equal(sixHourWindow.sourceWindowExact, true);
assert.equal(sixHourWindow.limitation, 'Delayed jobs on older parent builds can be absent.');
assert.ok(sixHourWindow.reason.includes('current UTC hour'));
const sixHourBuckets = helpers.omniMappingBuckets(sixHourWindow);
assert.equal(sixHourBuckets.length, 6);
assert.equal(helpers.omniMappingTotals(sixHourWindow.rows).omni.mapped_jobs, 6);
assert.equal(sixHourBuckets[0].workloads.omni.by_queue.amd_mi325_2.mapped_jobs, 1);

const currentHour = Date.parse('2026-07-29T22:00:00Z');
const retained169 = [];
for (let offset = 168; offset >= 0; offset -= 1) {
  const start = currentHour - offset * 3600000;
  retained169.push({
    hour: new Date(start).toISOString(),
    end_exclusive: new Date(start + 3600000).toISOString(),
    observed_through: offset === 0 ? '2026-07-29T22:29:00Z' : new Date(start + 3600000).toISOString(),
    state: offset === 0 ? 'open' : 'closed',
    open: offset === 0,
    complete: offset !== 0,
    collection_complete: true,
    lower_bound: false,
    workloads: {omni: {mapped_jobs: 1}, main: {mapped_jobs: 10}},
  });
}
const retainedMapping = {
  generated_at: '2026-07-29T22:29:00Z',
  hourly: retained169,
  daily: [],
  coverage: {hourly: {bucket_count: 169, expected_bucket_count: 169, missing_bucket_count: 0, contiguous: true, collection_complete: true, has_open_bucket: true}},
};
[['6h', 6], ['1d', 24], ['3d', 72], ['7d', 168]].forEach(function (pair) {
  const selectedWindow = helpers.omniMappingWindow(retainedMapping, pair[0]);
  assert.equal(selectedWindow.rows.length, pair[1], pair[0] + ' must select the exact UTC bucket count');
  assert.equal(helpers.omniMappingTotals(selectedWindow.rows).omni.mapped_jobs, pair[1]);
});
[['3d', 24, 3], ['7d', 28, 6]].forEach(function (pair) {
  const selectedWindow = helpers.omniMappingWindow(retainedMapping, pair[0]);
  const chartBuckets = helpers.omniMappingBuckets(selectedWindow);
  assert.equal(chartBuckets.length, pair[1], pair[0] + ' must use equal-duration chart buckets');
  assert.ok(chartBuckets.every(function (bucket) {
    return bucket.sourceRows === pair[2];
  }), pair[0] + ' chart buckets must have equal source-hour counts');
  assert.ok(chartBuckets.slice(0, -1).every(function (bucket) {
    return bucket.complete === true;
  }), pair[0] + ' closed chart buckets must be complete');
  assert.equal(chartBuckets[chartBuckets.length - 1].hasOpenBucket, true);
  assert.equal(chartBuckets[chartBuckets.length - 1].complete, false);
});
const retainedWithRecentGap = JSON.parse(JSON.stringify(retainedMapping));
retainedWithRecentGap.hourly.splice(retainedWithRecentGap.hourly.length - 3, 1);
const sixHoursWithGap = helpers.omniMappingWindow(retainedWithRecentGap, '6h');
assert.equal(sixHoursWithGap.rows.length, 5);
assert.equal(sixHoursWithGap.coverageStatus, 'partial');
assert.equal(helpers.omniMappingTotals(sixHoursWithGap.rows).omni.mapped_jobs, 5);
assert.ok(sixHoursWithGap.reason.includes('Only 5 of 6'));

const dailyOnlyMapping = {
  generated_at: '2026-07-29T18:00:00Z',
  hourly: [],
  daily: ['27', '28', '29'].map(function (day) {
    return {date: '2026-07-' + day, state: day === '29' ? 'open' : 'closed', open: day === '29', complete: day !== '29', collection_complete: true, lower_bound: false, observed_through: day === '29' ? '2026-07-29T18:00:00Z' : '2026-07-' + day + 'T23:59:59Z', workloads: {omni: {mapped_jobs: 2}, main: {mapped_jobs: 20}}};
  }),
};
assert.equal(helpers.omniMappingWindow(dailyOnlyMapping, '6h').available, false);
const threeDayFallback = helpers.omniMappingWindow(dailyOnlyMapping, '3d');
assert.equal(threeDayFallback.available, true);
assert.equal(threeDayFallback.resolution, 'daily');
assert.ok(threeDayFallback.reason.includes('UTC-day buckets'));
assert.ok(threeDayFallback.reason.includes('current UTC day'));
const dailyBuckets = helpers.omniMappingBuckets(threeDayFallback);
assert.equal(dailyBuckets[dailyBuckets.length - 1].hasOpenBucket, true);
assert.equal(dailyBuckets[dailyBuckets.length - 1].complete, false);

const aggregateRows = [1, 2, 3, 4].map(function (hour) {
  return {
    hour: '2026-07-29T' + String(hour).padStart(2, '0') + ':00:00Z',
    state: 'closed',
    complete: true,
    collection_complete: true,
    lower_bound: false,
    workloads: {omni: {mapped_jobs: 1}, main: {mapped_jobs: 10}},
  };
});
const partialAggregate = helpers.omniMappingBuckets({
  available: true,
  resolution: 'hourly',
  selected: {hourlyBin: 3},
  windowStart: Date.parse('2026-07-29T01:00:00Z'),
  rows: aggregateRows,
});
assert.equal(partialAggregate.length, 2);
assert.equal(partialAggregate[0].complete, true);
assert.equal(partialAggregate[1].complete, false);
assert.equal(partialAggregate[0].sourceRows, 3);
assert.equal(partialAggregate[1].sourceRows, 1);
assert.equal(partialAggregate[0].expectedSourceRows, 3);
const completeAggregate = helpers.omniMappingBuckets({
  available: true,
  resolution: 'hourly',
  selected: {hourlyBin: 3},
  rows: [{
    hour: '2026-07-29T00:00:00Z', state: 'closed', complete: true, collection_complete: true,
  }, {
    hour: '2026-07-29T01:00:00Z', state: 'closed', complete: true, collection_complete: true,
  }, {
    hour: '2026-07-29T02:00:00Z', state: 'closed', complete: true, collection_complete: true,
  }],
});
assert.equal(completeAggregate.length, 1);
assert.equal(completeAggregate[0].complete, true);
"""
    result = subprocess.run(
        ["node", "-e", script, str(ROOT / "docs" / "assets" / "js" / "ops-v2.js")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_definition_parity_helpers_keep_relationship_categories_exclusive():
    if not shutil.which("node"):
        import pytest

        pytest.skip("node is not available")
    script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const sandbox = {
  window: {__OPS_V2_TEST__: true},
  document: {addEventListener: function () {}},
  console: console,
  URL: URL,
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, {filename: process.argv[1]});
const helpers = sandbox.window.OpsV2Test;
const parity = {
  matches: [
    {_id: 'direct', match_method: 'identity', command_similarity: 1},
    {_id: 'twin', match_method: 'command_twin', command_similarity: 1},
  ],
  inline_mirror_variants: [{
    _id: 'inline',
    match_method: 'inline_mirror_variant',
    mirror_relationship: 'same_hardware_command_variant',
    command_similarity: 0.637,
    amd_route_similarity: 1,
    inline_mirror_command_similarity: 0.053,
  }],
  additional_variants: [{
    _id: 'additional',
    match_method: 'additional_variant',
    command_similarity: 0.8,
  }],
  amd_only: [{_id: 'amd-gap'}],
  nvidia_only: [{_id: 'upstream-gap'}],
  mirrors: [{
    _id: 'mirror',
    nvidia_label: 'Mirrored upstream',
    commands_overridden: true,
    command_similarity: 0.7,
    source_url: 'https://example.com/upstream',
  }],
};
const comparisons = helpers.definitionParityComparisonRows(parity);
const mirrors = helpers.definitionParityMirrorRows(parity);
const rows = comparisons.concat(mirrors);
function ids(plan) {
  return helpers.definitionParityFilter(rows, plan).map(function (row) {
    return row._id;
  }).sort().join(',');
}
assert.equal(comparisons.length, 6);
assert.equal(mirrors.length, 1);
assert.equal(ids('all'), 'additional,amd-gap,direct,inline,twin,upstream-gap');
assert.equal(ids('amd'), 'additional,amd-gap,direct,inline,twin');
assert.equal(ids('covered'), 'additional,direct,inline,twin');
assert.equal(ids('direct'), 'direct,twin');
assert.equal(ids('inline_variant'), 'inline');
assert.equal(ids('additional_variant'), 'additional');
assert.equal(ids('twins'), 'twin');
assert.equal(ids('changed'), 'additional,inline');
assert.equal(ids('unlinked'), 'amd-gap,upstream-gap');
assert.equal(ids('mirror_inventory'), 'mirror');
assert.equal(
  helpers.definitionParityPresentation(
    comparisons.find(function (row) { return row._id === 'inline'; })
  ).label,
  'Inline mirror command variant'
);
assert.equal(
  helpers.definitionParityPresentation(
    comparisons.find(function (row) { return row._id === 'inline'; })
  ).primarySimilarity,
  0.053
);
assert.equal(
  helpers.definitionParityPresentation(
    comparisons.find(function (row) { return row._id === 'inline'; })
  ).evidenceLabel,
  'inline AMD ↔ upstream'
);
assert.equal(
  helpers.definitionParityPresentation(
    comparisons.find(function (row) { return row._id === 'additional'; })
  ).label,
  'Additional AMD variant'
);
assert.equal(
  helpers.definitionParityPresentation(mirrors[0]).primarySimilarity,
  0.7
);
assert.equal(
  helpers.definitionParityPresentation(mirrors[0]).evidenceLabel,
  'inline AMD ↔ upstream'
);
assert.equal(mirrors[0].inline_mirror_command_similarity, 0.7);
assert.equal(helpers.definitionParityEvidence(mirrors[0]).changed, true);
"""
    result = subprocess.run(
        [
            "node",
            "-e",
            script,
            str(ROOT / "docs" / "assets" / "js" / "ops-v2.js"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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



def test_ci_health_uses_unique_group_policy_and_exact_evidence_drilldown():
    for contract in (
        "healthReduceDuplicates: true",
        "healthIgnoreMi355Only: true",
        "function matrixHealthPolicy",
        "function matrixHealthCollection",
        "function openMatrixHealthBrowser",
        "function openMatrixGroupEvidence",
        "UNIQUE AMD TEST GROUPS",
        "Unique AMD test-group health",
        "Reduce duplicates",
        "Ignore MI355-only",
        "Shared title substring",
        "MI355 evidence is shown",
    ):
        assert contract in OPS_JS
    for selector in (
        ".ops-page .ops-unique-health",
        ".ops-page .ops-unique-health-controls",
        ".ops-page .ops-unique-health-bar",
        ".ops-page .ops-unique-health-segment.is-mixed",
    ):
        assert selector in OPS_CSS


def test_retired_mi355b_queues_are_excluded_on_every_frontend_path():
    assert "function isRetiredQueue" in OPS_JS
    assert "name === 'amd_mi250_8'" not in OPS_JS
    assert "/^amd_mi355b(?:_|$)/i" in OPS_JS
    assert "&& !isRetiredQueue(name)" in OPS_JS
    assert "if (isRetiredQueue(entry[0])) return false" in OPS_JS
    assert "if (isRetiredQueue(job.queue)) return false" in OPS_JS
    assert "isRetiredQueue(name)" in OPS_JS


def test_amd_cpu_is_included_in_general_amd_scope_but_omni_uses_exact_allowlist():
    assert "function isAmdQueue" in OPS_JS
    assert "name === 'amd-cpu' || name.startsWith('amd_')" in OPS_JS
    assert "!isAmdQueue(entry[0])" in OPS_JS
    assert "state.queueScope === 'all' || isAmdQueue(job.queue)" in OPS_JS
    assert "state.queueScope === 'all' || isAmdQueue(name)" in OPS_JS
    assert "const mapping = omni.mapping_history || {}" in OPS_JS
    assert "Object.keys(omniByQueue).concat(Object.keys(mainByQueue))" in OPS_JS
    assert "INCOMING OMNI JOBS" in OPS_JS
    assert "OBSERVED OMNI MAPPINGS" in OPS_JS
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


def test_trajectory_has_exact_capacity_projection_subview():
    assert "trajectoryView: 'workload'" in OPS_JS
    assert "{id: 'capacity', label: 'Capacity projection'}" in OPS_JS
    assert "function renderCapacityProjection" in OPS_JS
    assert "function capacityScenario" in OPS_JS
    assert "function capacityBurstWait" in OPS_JS
    assert "function capacityServiceSourceLabel" in OPS_JS
    assert "target-runtime command-job median average" in OPS_JS
    assert "completed mapping proxy fallback (potentially downward biased)" in OPS_JS
    assert "function capacityLargestRemainder" in OPS_JS
    assert "Target groups · auto mix" in OPS_JS
    assert "Total jobs · auto mix" in OPS_JS
    assert "Specific queue / test" in OPS_JS
    assert "'-group queue topology to the exact ' + integer(targetTopology.groups)" in OPS_JS
    assert "observed 53-group queue topology" not in OPS_JS
    assert "exact 160-group target" not in OPS_JS
    assert "ONE-TIME P95 START WAIT" in OPS_JS
    assert "STEADY-STATE P95 WAIT" in OPS_JS
    assert "5-day joint p95" in OPS_JS
    assert "Observed stress" in OPS_JS
    assert "function capacityErlangC" in OPS_JS
    assert "Sustained load adds only the expansion delta" in OPS_JS
    assert "capacityProfileForPlacement" in OPS_JS
    assert "mi355_preferred" in OPS_JS
    assert "Configured quota does not reconcile with observed capacity signals." in OPS_JS
    assert "Queue-native connected agents versus planning quota" in OPS_JS
    assert "Configured planning quota:" in OPS_JS
    assert "Live connected-agent capacity is reported separately below." in OPS_JS
    assert "amd-cpu is Docker-build-only" in OPS_JS
    assert "perf_eval and retiring MI325 queues are excluded" in OPS_JS
    assert "{label: 'Provider', value: (row.sourceQueue || {}).provider || 'Not specified'}" in OPS_JS
    assert "Suite-alone simultaneous-start queue-shape gap" in OPS_JS
    assert "Background + suite zero-wait fixed-family gap" in OPS_JS
    assert "START-AT-ONCE GAP" in OPS_JS
    assert "MI325 workload is unplaced—and excluded from this answer." in OPS_JS
    assert "Inspect and model manually" in OPS_JS
    assert "unplaced_retiring_workload" in OPS_JS
    assert "MI325 mapping counts are UUID-deduplicated observations inside the " in OPS_JS
    assert "MI325 mapping population exhaustiveness is not published" in OPS_JS
    assert "unplacedWindow.source_limitation || unplacedWindow.limitation" in OPS_JS
    assert "Observed mapped jobs" in OPS_JS
    assert "Planning model, not an SLA." in OPS_JS
    assert "No compatibility or cross-family migration is inferred." in OPS_JS
    assert "ops_capacity_groups" in OPS_JS
    assert "ops_capacity_queue" in OPS_JS
    assert "ops_capacity_suites" in OPS_JS
    assert ".ops-page .ops-capacity-planner" in OPS_CSS
    assert ".ops-page .ops-capacity-verdict" in OPS_CSS
    assert ".ops-page .ops-capacity-fields" in OPS_CSS
    assert ".ops-page .ops-capacity-unplaced" in OPS_CSS


def test_capacity_planning_helpers_execute_in_javascript():
    if not shutil.which("node"):
        import pytest

        pytest.skip("node is not available")
    script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const sandbox = {
  window: {__OPS_V2_TEST__: true},
  document: {addEventListener: function () {}},
  console: console,
  URL: URL,
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, {filename: process.argv[1]});
const helpers = sandbox.window.OpsV2Test;
assert.ok(helpers);

assert.equal(
  JSON.stringify(helpers.capacityLargestRemainder([1, 1, 1], 5)),
  JSON.stringify([2, 2, 1])
);
assert.equal(
  helpers.capacityLargestRemainder([0, 0], 7).reduce(function (sum, value) { return sum + value; }, 0),
  7
);

const baseline = function (running, waiting) {
  return {
    current: {available: true, running: running, waiting: waiting},
    typical: {available: true, running: running, waiting: waiting},
    peak: {available: true, running: running, waiting: waiting},
    sample_count: 30,
  };
};
const profile = {
  available: true,
  topology: {
    current: {groups: 2, jobs: 3, gpu_slots: 10},
    target: {groups: 4, jobs: 6, gpu_slots: 20},
  },
  queues: [{
    id: 'amd_mi300_1',
    label: 'mi300_1',
    family: 'MI300',
    gpus_per_job: 1,
    capacity_jobs: 12,
    history: baseline(1, 0),
    workload: {service_minutes: 10, service_minutes_source: 'observed'},
    demand: {
      current: {groups: 1, jobs: 2, gpu_slots: 2},
      target: {groups: 2, jobs: 4, gpu_slots: 4},
    },
  }, {
    id: 'amd_mi300_8',
    label: 'mi300_8',
    family: 'MI300',
    gpus_per_job: 8,
    capacity_jobs: 1,
    history: baseline(0, 0),
    workload: {service_minutes: 20, service_minutes_source: 'runtime_fallback'},
    demand: {
      current: {groups: 1, jobs: 1, gpu_slots: 8},
      target: {groups: 2, jobs: 2, gpu_slots: 16},
    },
  }],
};

const midpoint = helpers.capacityTopologyForGroups(profile, 3, null);
assert.equal(midpoint.groups, 3);
assert.equal(midpoint.jobs, 5);
assert.equal(midpoint.rows.reduce(function (sum, row) { return sum + row.groups; }, 0), 3);
assert.equal(midpoint.rows.reduce(function (sum, row) { return sum + row.jobs; }, 0), 5);
assert.equal(
  JSON.stringify(midpoint.rows.map(function (row) { return row.jobs; })),
  JSON.stringify([3, 2])
);
const forcedJobs = helpers.capacityTopologyForGroups(profile, 4, 7);
assert.equal(forcedJobs.rows.reduce(function (sum, row) { return sum + row.jobs; }, 0), 7);
assert.equal(helpers.capacityGroupsForJobs(profile, 6), 4);

const productionGroups = [10, 8, 5, 5, 8, 6, 6, 5, 0];
const productionJobs = [12, 10, 6, 6, 10, 8, 7, 6, 0];
const targetGroups = [28, 24, 18, 12, 24, 20, 18, 16, 0];
const targetJobs = [34, 29, 21, 15, 29, 24, 23, 21, 0];
const productionProfile = {
  available: true,
  topology: {
    current: {groups: 53, jobs: 65, gpu_slots: 100},
    target: {groups: 160, jobs: 196, gpu_slots: 312},
  },
  queues: productionGroups.map(function (groups, index) {
    return {
      id: 'amd_queue_' + index,
      label: 'queue_' + index,
      family: index < 4 ? 'MI250' : 'MI300',
      gpus_per_job: index % 4 === 3 ? 8 : 1,
      capacity_jobs: 500,
      history: baseline(0, 0),
      workload: {service_minutes: 10, service_minutes_source: 'observed'},
      demand: {
        current: {groups: groups, jobs: productionJobs[index]},
        target: {groups: targetGroups[index], jobs: targetJobs[index]},
      },
    };
  }),
};
function assertPairedTopology(topology, expectedGroups, expectedJobs) {
  assert.equal(topology.allocationValid, true);
  assert.equal(topology.rows.reduce(function (sum, row) { return sum + row.groups; }, 0), expectedGroups);
  assert.equal(topology.rows.reduce(function (sum, row) { return sum + row.jobs; }, 0), expectedJobs);
  topology.rows.forEach(function (row) {
    assert.equal(row.groups > 0, row.jobs > 0, row.id + ' must pair group and job allocation');
  });
}
[0, 1, 17, 53, 54, 80, 159, 160, 161, 240].forEach(function (groups) {
  const topology = helpers.capacityTopologyForGroups(productionProfile, groups, null);
  assertPairedTopology(topology, groups, topology.jobs);
});
[0, 1, 65, 66, 195, 196, 197, 294].forEach(function (jobs) {
  const groups = helpers.capacityGroupsForJobs(productionProfile, jobs);
  const topology = helpers.capacityTopologyForGroups(productionProfile, groups, jobs);
  assertPairedTopology(topology, groups, jobs);
});

const publishedTargetRows = [
  ['amd_mi250_1', 6, 6, 20, 25, 78, 1],
  ['amd_mi250_2', 0, 0, 0, 0, 24, 2],
  ['amd_mi250_4', 0, 0, 1, 1, 16, 4],
  ['amd_mi250_8', 0, 0, 0, 0, 4, 8],
  ['amd_mi300_1', 37, 49, 60, 84, 296, 1],
  ['amd_mi300_2', 5, 5, 17, 17, 18, 2],
  ['amd_mi300_4', 4, 4, 21, 21, 19, 4],
  ['amd_mi300_8', 1, 1, 3, 3, 2, 8],
  ['amd_mi355_1', 0, 0, 28, 35, 240, 1],
  ['amd_mi355_2', 0, 0, 9, 9, 20, 2],
  ['amd_mi355_4', 0, 0, 1, 1, 16, 4],
  ['amd_mi355_8', 0, 0, 0, 0, 1, 8],
];
const publishedTargetProfile = {
  available: true,
  topology: {
    current: {groups: 53, jobs: 65, gpu_slots: 89},
    target: {groups: 160, jobs: 196, gpu_slots: 312},
  },
  queues: publishedTargetRows.map(function (row) {
    return {
      id: row[0],
      label: row[0].replace('amd_', ''),
      family: row[0].includes('mi250') ? 'MI250' : row[0].includes('mi300') ? 'MI300' : 'MI355',
      gpus_per_job: row[6],
      capacity_jobs: row[5],
      history: baseline(0, 0),
      workload: {service_minutes: 10, service_minutes_source: 'observed'},
      demand: {
        current: {groups: row[1], jobs: row[2]},
        target: {groups: row[3], jobs: row[4]},
      },
    };
  }),
};
const publishedTarget = helpers.capacityTopologyForGroups(publishedTargetProfile, 160, null);
assert.equal(publishedTarget.allocationValid, true);
assert.equal(publishedTarget.allocationExact, true);
assert.equal(publishedTarget.groups, 160);
assert.equal(publishedTarget.jobs, 196);
publishedTarget.rows.forEach(function (row, index) {
  assert.equal(row.groups, publishedTargetRows[index][3], row.id + ' target groups must remain exact');
  assert.equal(row.jobs, publishedTargetRows[index][4], row.id + ' target jobs must remain exact');
});
const publishedTargetScenario = helpers.capacityScenario(publishedTargetProfile, {
  mode: 'groups',
  groups: 160,
  baseline: 'peak',
  suites: 1,
});
assert.equal(publishedTargetScenario.shapeGapGpus, 16);
assert.equal(
  publishedTargetScenario.rows.find(function (row) { return row.id === 'amd_mi300_4'; }).shapeGapGpus,
  8
);
assert.equal(
  publishedTargetScenario.rows.find(function (row) { return row.id === 'amd_mi300_8'; }).shapeGapGpus,
  8
);

const queueShape = helpers.capacityTopologyForQueue(profile, 'amd_mi300_8', 2, 3, 45);
assert.equal(queueShape.groups, 2);
assert.equal(queueShape.jobs, 6);
assert.equal(queueShape.totalGateGroups, 4);
assert.equal(queueShape.totalGateJobs, 9);
assert.equal(queueShape.rows[0].jobs, 0);
assert.equal(queueShape.rows[1].jobs, 6);
assert.equal(queueShape.rows[1].serviceMinutes, 45);
assert.equal(queueShape.rows[1].serviceSource, 'user_input_for_specific_test_shape');

const finiteWait = helpers.capacityBurstWait(
  4,
  3,
  {available: true, running: 1, waiting: 1},
  10
);
assert.equal(finiteWait.status, 'finite');
assert.equal(finiteWait.p50, 10);
assert.equal(finiteWait.p95, 10);
assert.equal(finiteWait.max, 10);
assert.equal(finiteWait.allStartedBy, 10);
assert.equal(finiteWait.allCompletedBy, 20);
assert.equal(
  helpers.capacityBurstWait(2, 1, {available: true, running: 1, waiting: 0}, 20).status,
  'finite'
);
const excessRunningWait = helpers.capacityBurstWait(
  2,
  2,
  {available: true, running: 5, waiting: 1},
  10
);
assert.equal(excessRunningWait.status, 'finite');
assert.equal(excessRunningWait.backlogJobs, 4);
assert.equal(excessRunningWait.p95, 30);
assert.equal(excessRunningWait.max, 30);
assert.equal(
  helpers.capacityBurstWait(2, 1, {available: false}, 20).status,
  'unavailable'
);
assert.equal(
  helpers.capacityBurstWait(2, 1, {available: true, running: 0, waiting: 0}, null).status,
  'unavailable'
);
assert.equal(
  helpers.capacityBurstWait(50001, 1, {available: true, running: 0, waiting: 0}, 10).status,
  'unavailable'
);

const scenario = helpers.capacityScenario(profile, {
  mode: 'groups',
  groups: 4,
  baseline: 'peak',
  suites: 1,
});
assert.equal(scenario.groups, 4);
assert.equal(scenario.jobs, 6);
assert.equal(scenario.gpuSlots, 20);
assert.equal(scenario.waitStatus, 'finite');
assert.equal(scenario.p50Wait, 0);
assert.equal(scenario.p95Wait, 20);
assert.equal(scenario.maxWait, 20);
assert.equal(scenario.shapeGapGpus, 8);
assert.equal(scenario.familyGapGpus, 0);
assert.equal(scenario.zeroWaitShapeGapGpus, 8);
assert.equal(scenario.zeroWaitFamilyGapGpus, 1);
assert.equal(scenario.baselineQueuedGpus, 1);
assert.ok(helpers.capacityVerdict(scenario).includes('reallocate 8 GPUs'));
const unplacedProfile = JSON.parse(JSON.stringify(profile));
unplacedProfile.unplaced_retiring_workload = {
  available: true,
  excluded_from_wait_and_headroom: true,
  status: 'unplaced',
  compatibility: 'unknown',
};
const unplacedScenario = helpers.capacityScenario(unplacedProfile, {
  mode: 'groups',
  groups: 4,
  baseline: 'peak',
  suites: 1,
});
assert.ok(helpers.capacityVerdict(unplacedScenario).includes('MI325 workload is unplaced'));
assert.ok(helpers.capacityVerdict(unplacedScenario).includes('excluded from every wait and headroom figure'));

const overlap = helpers.capacityScenario(profile, {
  mode: 'groups',
  groups: 4,
  baseline: 'peak',
  suites: 2,
});
assert.equal(overlap.familyGapGpus, 20);
assert.equal(overlap.zeroWaitFamilyGapGpus, 21);
assert.ok(helpers.capacityVerdict(overlap).includes('short 20 GPU slots'));

const waitingProfile = JSON.parse(JSON.stringify(profile));
waitingProfile.queues[0].history.peak.waiting = 2;
const waitingScenario = helpers.capacityScenario(waitingProfile, {
  mode: 'groups',
  groups: 4,
  baseline: 'peak',
  suites: 1,
});
assert.equal(waitingScenario.rows[0].combinedJobs, 7);
assert.equal(waitingScenario.baselineQueuedGpus, 3);
assert.equal(waitingScenario.zeroWaitFamilyGapGpus, 3);
assert.ok(Math.abs(waitingScenario.aggregatePressurePct - 115) < 0.001);

const saturatedProfile = JSON.parse(JSON.stringify(profile));
saturatedProfile.queues[1].history.peak.running = 1;
const saturated = helpers.capacityScenario(saturatedProfile, {
  mode: 'groups',
  groups: 4,
  baseline: 'peak',
  suites: 1,
});
assert.equal(saturated.waitStatus, 'finite');
assert.equal(saturated.p95Wait, 40);
assert.equal(saturated.maxWait, 40);
assert.ok(helpers.capacityVerdict(saturated).includes('conservative full-service residual'));

const curve = helpers.capacityGrowthCurve(profile, {baseline: 'peak', suites: 1}, 4);
assert.equal(curve.length, 9);
assert.ok(curve.every(function (point) { return point.status === 'finite'; }));
assert.ok(curve.some(function (point) { return point.selected && point.x === 4; }));
const defaultGroupCurve = helpers.capacityGrowthCurve(productionProfile, {
  mode: 'groups', groups: 160, baseline: 'peak', suites: 1,
});
assert.ok(defaultGroupCurve.some(function (point) { return point.selected && point.x === 160; }));
assert.ok(defaultGroupCurve.every(function (point) { return point.mode === 'groups'; }));
const jobsCurve = helpers.capacityGrowthCurve(productionProfile, {
  mode: 'jobs', jobs: 196, baseline: 'peak', suites: 1,
});
assert.ok(jobsCurve.some(function (point) { return point.selected && point.x === 196; }));
assert.ok(jobsCurve.every(function (point) { return point.mode === 'jobs'; }));
const queueCurve = helpers.capacityGrowthCurve(productionProfile, {
  mode: 'queue',
  queue: 'amd_queue_3',
  queueGroups: 3,
  parallel: 2,
  duration: 30,
  baseline: 'peak',
  suites: 1,
});
assert.ok(queueCurve.some(function (point) { return point.selected && point.x === 3; }));
assert.ok(queueCurve.every(function (point) { return point.mode === 'queue'; }));

const stableQueue = helpers.capacityErlangC(6, 2, 10);
assert.equal(stableQueue.status, 'finite');
assert.ok(Math.abs(stableQueue.rho - 0.5) < 0.0001);
assert.ok(stableQueue.p95 > 0);
assert.equal(helpers.capacityErlangC(12, 2, 10).status, 'unstable');
assert.equal(helpers.capacityErlangC(null, 2, 10).status, 'unavailable');

const sustainedProfile = JSON.parse(JSON.stringify(profile));
sustainedProfile.queues[0].workload.weekday_started_cohort_rate_jobs_per_hour = 2;
sustainedProfile.queues[1].workload.weekday_started_cohort_rate_jobs_per_hour = 0;
sustainedProfile.queues[0].history.peak.running = 999;
const sustained = helpers.capacityScenario(sustainedProfile, {
  mode: 'groups',
  groups: 4,
  trafficMode: 'sustained',
  suitesPerHour: 1,
  suites: 20,
});
assert.equal(sustained.waitStatus, 'finite');
assert.equal(sustained.jobs, 6);
assert.ok(Math.abs(sustained.rows[0].arrivalRate - 4) < 0.0001);
assert.ok(Math.abs(sustained.rows[0].wait.rho - (4 * 10 / 60 / 12)) < 0.0001);
assert.equal(sustained.rows[0].baselineRunning, 999);
assert.ok(helpers.capacityVerdict(sustained).includes('stable at every used queue'));

const unstableProfile = JSON.parse(JSON.stringify(sustainedProfile));
unstableProfile.queues[1].workload.weekday_started_cohort_rate_jobs_per_hour = 3;
const unstable = helpers.capacityScenario(unstableProfile, {
  mode: 'groups',
  groups: 4,
  trafficMode: 'sustained',
  suitesPerHour: 1,
});
assert.equal(unstable.waitStatus, 'unstable');
assert.equal(unstable.rows[1].wait.status, 'unstable');
assert.ok(unstable.stabilityGapGpus > 0);

const placementProfile = JSON.parse(JSON.stringify(profile));
placementProfile.placement_profiles = {
  default_strategy_id: 'mi355_preferred',
  strategies: [{
    id: 'mi355_preferred',
    label: 'Prefer MI355 where defined',
    topology: {groups: 4, jobs: 6, gpu_slots: 20},
    queues: [
      {id: 'amd_mi300_1', groups: 1, jobs: 2, gpu_slots: 2, service_minutes: 12, service_minutes_source: 'placement_strategy_target_command_job_median_average'},
      {id: 'amd_mi300_8', groups: 3, jobs: 4, gpu_slots: 32, service_minutes: 22, service_minutes_source: 'placement_strategy_target_command_job_median_average'},
    ],
  }, {
    id: 'current_definition_precedence',
    label: 'Current definition precedence',
    topology: {groups: 4, jobs: 6, gpu_slots: 20},
    queues: [
      {id: 'amd_mi300_1', groups: 2, jobs: 4, gpu_slots: 4},
      {id: 'amd_mi300_8', groups: 2, jobs: 2, gpu_slots: 16},
    ],
  }],
};
const placed = helpers.capacityProfileForPlacement(placementProfile, 'mi355_preferred');
assert.equal(placed.selected_placement_strategy.id, 'mi355_preferred');
assert.equal(placed.queues[0].demand.target.groups, 1);
assert.equal(placed.queues[1].demand.target.groups, 3);
assert.equal(placed.queues[0].workload.service_minutes, 12);
const explicitQueue = helpers.capacityScenario(placed, {
  mode: 'queue',
  queue: 'amd_mi300_1',
  queueGroups: 1,
  parallel: 1,
  duration: 10,
  trafficMode: 'burst',
});
assert.equal(explicitQueue.placementStrategy, null);
const oversizedBurst = helpers.capacityScenario(profile, {
  mode: 'queue',
  queue: 'amd_mi300_1',
  queueGroups: 5000,
  parallel: 256,
  duration: 10,
  trafficMode: 'burst',
  suites: 20,
});
assert.equal(oversizedBurst.burstLimitExceeded, true);
assert.equal(oversizedBurst.waitStatus, 'unavailable');
"""
    result = subprocess.run(
        ["node", "-e", script, str(ROOT / "docs" / "assets" / "js" / "ops-v2.js")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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


def test_omni_is_exact_pipeline_and_amd_queue_scoped_with_mapping_histogram():
    utils_js = (ROOT / "docs" / "assets" / "js" / "utils.js").read_text()
    assert "{ id: 'ci-omni', label: 'Omni CI'" in utils_js
    omni_render = OPS_JS[
        OPS_JS.index("async function renderOmni")
        :OPS_JS.index("async function render(tabId")
    ]
    assert "'Omni CI'" in omni_render
    assert "vLLM Omni CI" not in omni_render
    assert "vllm-project/vllm-omni" in OPS_JS
    assert "vllm-project/vllm" in OPS_JS
    assert "Observed incoming mappings from " in omni_render
    assert "Where Omni lands" in omni_render
    assert "Repository comparison" in omni_render
    assert "GPU-SLOT REQUESTS" in omni_render
    assert "Sum of configured GPU widths across observed mappings; not simultaneous use or GPU-hours" in omni_render
    assert "Requested concurrent slots" not in omni_render
    assert "REQUESTED GPU SLOTS" not in omni_render
    assert "comparison stays numeric instead of sharing a misleading chart scale" in omni_render
    assert "label: OMNI_REPOSITORIES.omni + ' observed mapped jobs'" in omni_render
    assert "yAxisID: 'y1'" not in omni_render
    assert "Main vLLM mapped jobs" not in omni_render
    assert "data/vllm/ci/workload_mapping.json" in OPS_JS
    assert "const OMNI_MAPPING_WINDOWS" in OPS_JS
    for range_id in ("6h", "1d", "3d", "7d", "1m", "3m"):
        assert f"{{id: '{range_id}'" in OPS_JS
    assert "'ops_omni_mapping_range'" in OPS_JS
    assert "function omniMappingWindow" in OPS_JS
    assert "function omniMappingPopulationBoundary" in OPS_JS
    assert "function omniMappingBuckets" in OPS_JS
    assert "latestStart - (expectedBuckets - 1) * bucketMs" in OPS_JS
    assert "item.start >= earliestStart" in OPS_JS
    assert "selectedContiguous" in OPS_JS
    assert "retainedComplete" in OPS_JS
    assert "apiCollectionComplete" in OPS_JS
    assert "jobCreatedRangeExhaustive" in OPS_JS
    assert "parentBuildLookbackDays" in OPS_JS
    assert "API/UUID collection complete inside the source window" in omni_render
    assert "All job-created mappings are not provably exhaustive." in omni_render
    assert "UUID-exact only within the declared parent-build source window" in omni_render
    assert "Jobs attached later to older parent builds can be absent" in OPS_JS
    assert "Selected buckets complete." not in omni_render
    assert "Exact unique command-job mappings in the selected window." not in omni_render
    assert "exact selected-window mapping aggregates" not in omni_render
    assert "Hourly mapping history is not available yet" in OPS_JS
    assert "Daily totals cannot answer a trailing " in OPS_JS
    assert "Inspect time buckets" in omni_render
    assert "Browse all queues" in omni_render
    assert "openQueueMappingDetail" in omni_render
    assert "openMappingBucket" in omni_render
    assert "openTrafficShareDetail" in omni_render
    assert "openMi325ExposureDetail" in omni_render
    assert "retiring queues only" in omni_render
    assert "MI325 retirement is an Omni migration blocker" not in omni_render
    assert "open current UTC " in omni_render
    assert "bucket.expectedSourceRows = expectedSourceRows" in OPS_JS
    assert "Live AMD queue state" in omni_render
    assert "point.waitingSupported || point.runningSupported" in omni_render
    assert "openOccupancyEvidence" in omni_render
    assert "Aggregate queue totals are not reclassified as Omni" in omni_render
    assert "Daily closing context" in omni_render
    assert "Legacy closing occupancy context (UTC)" not in omni_render
    assert "@media (max-width: 1279px)" in OPS_CSS
    assert "minWidth: '292px'" in omni_render
    assert "function omniHistoryPoints" in OPS_JS
    assert "waiting_observed" in OPS_JS
    assert "aggregate queue totals are never expanded into synthetic jobs" in OPS_JS
    assert "const excludedPending = pendingLedger.filter" in OPS_JS
    assert "Inspect excluded stale jobs" in OPS_JS
    assert "const OMNI_RANGE_WINDOWS" in OPS_JS
    assert "{id: '1h', label: '1 hour', hours: 1}" in OPS_JS
    assert "{id: '72h', label: '3 days', hours: 72}" in OPS_JS
    assert "function omniDailyRows" in OPS_JS
    assert "const OMNI_AGE_BANDS" in OPS_JS
    assert "'ops_omni_range'" in OPS_JS  # compact live-occupancy control
    jobs = OPS_DATA["omni"]["current_jobs"]
    current = OPS_DATA["omni"]["current"]
    active_pending = [job for job in jobs["pending"] if not job.get("analysis_excluded")]
    active_running = [job for job in jobs["running"] if not job.get("analysis_excluded")]
    excluded = [
        job
        for state in ("pending", "running")
        for job in jobs[state]
        if job.get("analysis_excluded")
    ]
    assert len(active_pending) == current["ledger"]["waiting"]
    assert len(active_running) == current["ledger"]["running"]
    for state in ("waiting", "running"):
        if current["count_basis"][state] == "observed_queue_workload_split":
            assert current["attribution"][f"{state}_supported"] is True
            assert current[state] == current["attribution"][f"{state}_observed"]
        else:
            assert current["count_basis"][state] == "exact_pipeline_active_job_ledger"
            assert current[state] == current["ledger"][state]
    assert all(job.get("exclusion_reason") for job in excluded)
    assert all(
        job.get("workload") == "omni"
        and job.get("url", "").startswith("https://buildkite.com/")
        for state in ("pending", "running")
        for job in jobs[state]
    )
    history = OPS_DATA["omni"]["history"]
    assert history["summary"]["snapshot_count"] > 0
    assert history["points"]
    for point in history["points"]:
        for state in ("waiting", "running"):
            scope = point["amd"]
            expected = {"complete", "partial"} if scope[f"{state}_supported"] else {"unavailable"}
            assert scope[f"{state}_attribution"] in expected


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
    assert "UNIQUE AMD TEST GROUPS" in OPS_JS
    assert "uniqueHealth.passing_groups" in OPS_JS
    assert "uniqueHealth.failing_groups" in OPS_JS


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
        "definition-parity",
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
