"""Static contracts for the vLLM AMD CI Operations frontend boundary."""

import json
import shutil
import subprocess

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "docs" / "index.html").read_text()
OPS_JS = (ROOT / "docs" / "assets" / "js" / "ops-v2.js").read_text()
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
        "automation issue text contains no user mentions",
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
        "UPSTREAM DEFINITIONS",
        "AMD DEFINITIONS",
        "AMD DEFINITIONS MATCHED",
        "UNMATCHED DEFINITIONS",
        "Definition coverage, not passing test groups.",
        "Source-definition comparison",
        "Command twin",
        "Open pinned vLLM commit",
    ):
        assert visible_label in OPS_JS
    assert "ops.definition_parity || {}" in OPS_JS
    assert "row.match_method === 'command_twin'" in OPS_JS
    assert "row.amd_source_url" in OPS_JS
    assert "row.nvidia_source_url" in OPS_JS
    assert "Search 127 reviewed groups" not in OPS_JS
    assert "matrixData.rows || []" in OPS_JS
    assert "matrixData.rows || []).slice" not in OPS_JS


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
"""
    result = subprocess.run(
        ["node", "-e", script, str(ROOT / "docs" / "assets" / "js" / "ops-v2.js")],
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
    assert "function omniHistoryPoints" in OPS_JS
    assert "waiting_observed" in OPS_JS
    assert "Exact active-job ledger:" in OPS_JS
    assert "These evidence types are never forced to agree" in OPS_JS
    assert "Aggregate queue totals are never reclassified as Omni" in OPS_JS
    assert "All-fleet running observed" in OPS_JS
    assert "AMD running observed" in OPS_JS
    assert "const excludedPending = pendingLedger.filter" in OPS_JS
    assert "Inspect excluded stale jobs" in OPS_JS
    assert "const OMNI_RANGE_WINDOWS" in OPS_JS
    assert "{id: '1h', label: '1 hour', hours: 1}" in OPS_JS
    assert "{id: '72h', label: '3 days', hours: 72}" in OPS_JS
    assert "Day-over-day observed queued workload (UTC)" in OPS_JS
    assert "function omniDailyRows" in OPS_JS
    assert "const OMNI_AGE_BANDS" in OPS_JS
    assert "Queued task age" in OPS_JS
    assert "'ops_omni_range'" in OPS_JS
    assert "'ops_omni_age'" in OPS_JS
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
            assert current["count_basis"][state] == "active_job_ledger"
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
            scope = point["all_fleet"]
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
