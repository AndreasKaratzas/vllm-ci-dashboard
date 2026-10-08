"""Static contracts for the vLLM AMD CI Operations frontend boundary."""

# cspell:ignore Untimed xoxb

import gzip
import json
import shutil
import subprocess

from pathlib import Path

import pytest

from vllm import operations_bundle_contract as bundle_contract


ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "docs" / "index.html").read_text()
OPS_JS_PATH = ROOT / "docs" / "assets" / "js" / "ops-v2.js"
OPS_JS = OPS_JS_PATH.read_text()
AMD_MIRROR_INVENTORY_JS_PATH = (
    ROOT / "docs" / "assets" / "js" / "amd-mirror-inventory.js"
)
AMD_MIRROR_INVENTORY_JS = AMD_MIRROR_INVENTORY_JS_PATH.read_text()
OPS_CSS = (ROOT / "docs" / "assets" / "css" / "ops-v2.css").read_text()
DASHBOARD_CSS = (ROOT / "docs" / "assets" / "css" / "dashboard.css").read_text()
DASHBOARD_NAV_JS = (
    ROOT / "docs" / "assets" / "js" / "dashboard-nav.js"
).read_text()
UTILS_JS = (ROOT / "docs" / "assets" / "js" / "utils.js").read_text()
OPS_DATA_PATH = ROOT / "data" / "vllm" / "ci" / "operations_v2.json.gz"
OPS_MANIFEST_PATH = ROOT / "data" / "vllm" / "ci" / "operations_v2_manifest.json"


@pytest.fixture(scope="module")
def ops_data():
    with gzip.open(OPS_DATA_PATH, "rt", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def ops_manifest():
    return json.loads(OPS_MANIFEST_PATH.read_text())


@pytest.mark.parametrize(
    "script_path", (OPS_JS_PATH, AMD_MIRROR_INVENTORY_JS_PATH)
)
def test_operations_javascript_has_valid_syntax(script_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    result = subprocess.run(
        [node, "--check", str(script_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_agent_health_compact_accounting_keeps_counts_exact_and_labels_evidence():
    assert "Agent-health drill-down evidence is storage-bounded." in OPS_JS
    assert "node-specific failure rates are unavailable" in OPS_JS
    assert "compact accounting is exact only for that retained suffix" in OPS_JS
    assert "operationsAccountingRetention.complete === false || sourceHistoryIncomplete" in OPS_JS
    assert "agentAggregateRunCounts" in OPS_JS
    assert "agentAggregateFailureCounts" in OPS_JS
    node = shutil.which("node")
    if not node:
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
vm.runInContext(source, sandbox);
const count = sandbox.window.OpsV2Test.agentFailureAccountingCounts;
const rows = [
  {d: '2026-08-31', nd: 'node-a', s: 'hard', i: 1, ng: 1, bc: 0, c: 3},
  {d: '2026-08-31', nd: 'node-a', s: 'soft', i: 0, ng: 0, bc: 0, c: 5},
  {d: '2026-08-31', nd: 'node-a', s: 'hard', i: 1, ng: 1, bc: 1, c: 7},
  {d: '2026-08-20', nd: 'node-a', s: 'hard', i: 1, ng: 1, bc: 0, c: 11},
];
let totals = count(rows, {startDay: '2026-08-30', nightlyOnly: false, excludeCancelled: true, signal: 'infra'}).get('node-a');
assert.equal(totals.hard, 3);
assert.equal(totals.soft, 5);
assert.equal(totals.signal, 3);
totals = count(rows, {startDay: '2026-08-30', nightlyOnly: true, excludeCancelled: false, signal: 'all'}).get('node-a');
assert.equal(totals.hard, 10);
assert.equal(totals.soft, 0);
assert.equal(totals.signal, 10);
const sourceComplete = sandbox.window.OpsV2Test.agentSourceHistoryComplete;
assert.equal(sourceComplete({retention: {byte_limited: false, dropped_oldest_day_count: 0, original_day_count: 60, retained_day_count: 60}}), true);
assert.equal(sourceComplete({retention: {byte_limited: true, dropped_oldest_day_count: 2, original_day_count: 60, retained_day_count: 58}}), false);
assert.equal(sourceComplete({retention: {byte_limited: false, dropped_oldest_day_count: 0, original_day_count: 60, retained_day_count: 59}}), false);
"""
    result = subprocess.run(
        [node, "-e", script, str(ROOT / "docs" / "assets" / "js" / "ops-v2.js")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_omni_storage_retention_disables_incomplete_window_rates():
    assert "mappingPublication.complete_relative_to_source === false" in OPS_JS
    assert "const mappingRatesAvailable = mappingAvailable && mappingView.retainedComplete" in OPS_JS
    assert "Rates, shares, and deltas are unavailable for incomplete selected coverage." in OPS_JS
    assert "value: mappingRatesAvailable ? percent(omniTotal.mapped_jobs" in OPS_JS


def test_ci_health_reporter_retention_is_disclosed_without_inferred_rates():
    assert "ciHealthPublicationRetentionMessage" in OPS_JS
    assert "no rate or delta is derived from them" in OPS_JS


def test_amd_test_health_publication_retention_discloses_lower_bound_catalogs():
    assert "AMD test-health drill-down is storage-bounded" in OPS_JS
    assert "catalog counts, hardware distributions, and history charts are retained-row lower bounds" in OPS_JS
    assert "Aggregate share unavailable for incomplete catalog" in OPS_JS
    assert "AMD detail is storage-bounded" in OPS_JS
    assert "architecture rates and routes are hidden" in OPS_JS


def test_home_workbench_marks_bounded_populations_as_lower_bounds():
    assert "Issues (' + observedCountLabel(" in OPS_JS
    assert "PRs (' + observedCountLabel(" in OPS_JS

    node = shutil.which("node")
    if not node:
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
vm.runInContext(source, sandbox);
const helpers = sandbox.window.OpsV2Test;

assert.equal(helpers.populationSemantics({count_semantics: 'complete'}), 'complete');
assert.equal(helpers.populationSemantics({count_semantics: 'lower_bound'}), 'lower_bound');
assert.equal(helpers.populationSemantics({
  source_coverage: {authoritative_complete: false},
}), 'lower_bound');
assert.equal(helpers.populationSemantics({
  count_semantics: 'complete',
  publication_retention: {complete_relative_to_source: false},
}), 'lower_bound');
assert.equal(helpers.populationSemantics({}), 'lower_bound');
assert.equal(helpers.observedCountLabel(12, 'complete'), '12');
assert.equal(helpers.observedCountLabel(12, 'lower_bound'), '≥12');
"""
    result = subprocess.run(
        [node, "-e", script, str(ROOT / "docs" / "assets" / "js" / "ops-v2.js")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr




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
    assert "assets/js/dashboard-nav.js" in INDEX
    assert "window.__DASHBOARD_V2__ = true" in INDEX
    assert 'id="ops-menu-toggle"' in INDEX
    assert 'id="ops-nav-backdrop"' in INDEX


def test_mobile_navigation_contains_focus_and_marks_background_inert():
    for contract in (
        "element.inert = Boolean(open)",
        "event.key === 'Tab'",
        "event.shiftKey && document.activeElement === first",
        "document.activeElement === last",
        "menuToggle.setAttribute('aria-expanded'",
    ):
        assert contract in DASHBOARD_NAV_JS


def test_v2_boot_omits_retired_renderers_and_control_tools():
    active_runtime = INDEX
    for retired in (
        "dashboard.js",
        "ci-health.js",
        "ci-analytics.js",
        "ci-perf-eval.js",
        "ci-queue.js",
        "ci-hotness.js",
        "ci-omni.js",
    ):
        assert f'<script src="assets/js/{retired}' not in active_runtime
        assert not (ROOT / "docs" / "assets" / "js" / retired).exists()
    for control in ("ci-testbuild.js", "ci-ready.js", "ci-admin.js"):
        assert f'<script src="assets/js/{control}' not in active_runtime

    assert INDEX.index("window.__DASHBOARD_V2__ = true") < INDEX.index("assets/js/utils.js")
    assert INDEX.index("assets/js/dashboard-nav.js") < INDEX.index("assets/js/ops-v2.js")
    assert "ops-deferred-script-manifest" not in INDEX
    assert "window.__loadOpsControlTools" not in INDEX

    runtime_files = (
        "utils.js",
        "publication-status.js",
        "dashboard-nav.js",
        "ops-v2.js",
    )
    # GitHub Pages publishes the repository blobs with LF line endings. Keep
    # this budget stable on developer checkouts configured with core.autocrlf.
    runtime_bytes = sum(
        len(
            (ROOT / "docs" / "assets" / "js" / name)
            .read_bytes()
            .replace(b"\r\n", b"\n")
        )
        for name in runtime_files
    )
    assert runtime_bytes < 900_000


def test_dns_deep_link_preloads_only_its_same_origin_fallback():
    assert "route.searchParams.get('ops_analytics_view') !== 'dns'" in INDEX
    assert "preload.rel = 'preload'" in INDEX
    assert "preload.as = 'fetch'" in INDEX
    assert "preload.fetchPriority = 'high'" in INDEX
    assert "data/vllm/ci/dns_failures.json?_=" in INDEX


def test_unrelated_link_registry_data_waits_for_first_v2_render():
    utils = (ROOT / "docs" / "assets" / "js" / "utils.js").read_text()
    assert "function afterOpsV2FirstRender(task)" in utils
    assert "window.addEventListener('ops-v2:first-render', function() { schedule(1500); }" in utils
    assert "afterOpsV2FirstRender(function() {\n  LinkRegistry.onReady" in utils
    shard_start = utils.index("var _shardBasesReady")
    shard_end = utils.index("function _stripShardIndex", shard_start)
    assert "afterOpsV2FirstRender(function()" in utils[shard_start:shard_end]


def test_operations_data_is_lazy_loaded_with_bounded_first_render_payloads():
    assert "operations_v2_manifest.json" in OPS_JS
    assert "function loadOperations" in OPS_JS
    assert "function operationSectionNames" in OPS_JS
    assert "return loadOperationSections(manifest.shell, operationSectionNames(tabId))" in OPS_JS
    assert "const ops = await loadOperations(tabId)" in OPS_JS
    assert "fetchJSON('data/vllm/ci/operations_v2.json')" not in OPS_JS
    assert "using compatibility snapshot" not in OPS_JS
    site_builder = (ROOT / "scripts" / "build_site.py").read_text()
    assert "materialize_operations_bundle(DATA, output_dir / \"data\", manifest)" in site_builder
    assert "write_snapshot_bundle(output, payload, write_monolith=False" in site_builder


def test_data_fetches_retry_and_do_not_cache_transient_failures():
    assert "for (let attempt = 0; attempt < 3; attempt += 1)" in OPS_JS
    assert "fetch(requestPath, {cache: 'no-store'})" in OPS_JS
    assert "if (cache.get(key) === request) cache.delete(key)" in OPS_JS
    assert "if (operationsManifestPromise === request) operationsManifestPromise = null" in OPS_JS


@pytest.mark.live_data
def test_current_operations_payloads_are_bounded(ops_data, ops_manifest):
    assert ops_manifest["schema_version"] == 2
    assert (
        ops_manifest["bundle_version"]
        == bundle_contract.OPERATIONS_PRODUCER_BUNDLE_VERSION
    )
    assert ops_manifest["generated_at"] == ops_data["generated_at"]
    assert "reliability" not in ops_manifest["shell"]
    assert "amd_agent_health" not in ops_manifest["shell"]
    assert set(ops_manifest["sections"]) >= {
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
        for name, descriptor in ops_manifest["sections"].items()
    }
    assert manifest_bytes <= bundle_contract.OPERATIONS_MANIFEST_MAX_BYTES
    assert bundle_contract.validate_operations_canary_budget(
        manifest_bytes=manifest_bytes,
        section_bytes=section_bytes,
    ) == manifest_bytes + sum(
        section_bytes[name]
        for name in bundle_contract.OPERATIONS_CANARY_SECTIONS
    )
    assert section_bytes["queue"] <= (
        bundle_contract.OPERATIONS_CANARY_SECTION_MAX_BYTES["queue"]
    )
    assert manifest_bytes < OPS_DATA_PATH.stat().st_size * 0.05


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
        "Confirmed-incident issues tag the selected owner and verified assignee",
        "CC each remaining ranked area owner once",
        "AREAS WITH CONFIRMED INCIDENTS",
        "PENDING SOFT OBSERVATIONS",
        "requires 2 distinct completed builds",
        "Confirmed AMD incidents",
        "Pending soft observations",
        "Incident response, escalation, runtime signal, and parity obligations",
        "incident_observation_eligible === false",
        "function ownershipObservationLabel",
        "ignored older build",
    ):
        assert contract in OPS_JS
    assert "private PTO" not in OPS_JS
    assert "Private availability" not in OPS_JS
    assert "Regression response, escalation, runtime signal" not in OPS_JS
    assert "availability.fresh === true" in OPS_JS
    assert (
        "['healthView', 'health_view', "
        "['overview', 'parity', 'coverage', 'mirrors']]"
    ) in OPS_JS
    assert "{id: 'ownership', label: 'CI ownership'}" not in OPS_JS
    assert "if (state.healthView === 'ownership')" not in OPS_JS
    assert "gating.ownership" not in OPS_JS
    assert "architectureSignalStateRank(ownershipAreaState(left))" in OPS_JS
    assert "compareText(left.source_file, right.source_file)" in OPS_JS
    assert OPS_JS.index("function renderOwnership(host, ops)") < OPS_JS.index(
        "async function renderHealth"
    )


def test_legacy_renderers_are_removed():
    js_dir = ROOT / "docs" / "assets" / "js"
    for name in (
        "ci-health.js",
        "ci-analytics.js",
        "ci-perf-eval.js",
        "ci-queue.js",
        "ci-hotness.js",
        "ci-omni.js",
    ):
        assert not (js_dir / name).exists()


def test_reliability_evidence_is_drillable_and_honestly_named():
    assert "openMixedOutcomeEvidence" in OPS_JS
    assert "openGroupDetail" in OPS_JS
    assert "openHistoryEvidence" in OPS_JS
    assert "mixed-outcome candidate" in OPS_JS
    assert "not a test-case flake probability" in OPS_JS
    assert "Open log" in OPS_JS
    assert "Failures only" in OPS_JS


def test_pass_rate_copy_names_each_observation_denominator():
    assert "MEDIAN RETAINED-RUN PASS RATE" in OPS_JS
    assert "RETAINED-RUN PASS RATE" in OPS_JS
    assert "Retained-run pass rate" in OPS_JS
    assert "job_variant_pass_rate" in OPS_JS
    assert "MEDIAN PASS RATE" not in OPS_JS
    assert "RETAINED PASS RATE" not in OPS_JS
    assert "details: {observed_job_variants:" in OPS_JS


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
        "Failures to inspect",
        "RETAINED-RUN PASS RATE",
        "CURRENT SIGNAL",
        "LAST FAILURE",
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


@pytest.mark.live_data
def test_current_group_history_has_main_and_nightly_evidence(ops_data):
    groups = ops_data["reliability"]["group_catalog"]
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


def test_main_ci_health_and_recent_latency_are_distinct_surfaces():
    for contract in ("function renderAmdHealth", "function openAmdLogicalCatalog", "function openAmdLogicalGroupDetail", "function openAmdCatalog", "function openAmdGroupDetail", "function latencyComparison", "function openLatencyEvidence", "function renderLatencyComparison", "Latest five completed main CI nightlies", "median", "sample_count"):
        assert contract in OPS_JS
    for retired in ("function platformComparison", "function renderPlatformFlakes", "function renderPlatformRetries", "comparisonRetryEvidence", "analyticsWindow"):
        assert retired not in OPS_JS


@pytest.mark.live_data
def test_current_main_ci_amd_health_and_recent_latency_reconcile(ops_data):
    health = ops_data["amd_test_health"]
    summary = health["summary"]
    latest = summary["latest_state_counts"]
    assert health["source_pipeline"] == "ci"
    assert health["job_scope"] == "amd_gpu"
    assert summary["build_count"] == len(health["builds"])
    assert summary["build_count"] > 0
    assert summary["latest_group_count"] > 0
    assert summary["latest_group_count"] == sum(latest.values())
    # A fully green nightly legitimately has no soft or hard failures.
    assert set(latest) == {"passed", "soft", "hard", "unknown"}
    assert all(
        isinstance(count, int) and not isinstance(count, bool) and count >= 0
        for count in latest.values()
    )
    for state, field in (
        ("passed", "latest_passed_group_count"),
        ("soft", "latest_soft_failed_group_count"),
        ("hard", "latest_hard_failed_group_count"),
        ("unknown", "latest_unknown_group_count"),
    ):
        assert summary[field] == latest[state]
    assert summary["latest_incident_group_count"] == latest["soft"] + latest["hard"]
    assert len({row["id"] for row in health["group_catalog"]}) == summary["group_count"]
    assert all(
        observation["url"].startswith("https://buildkite.com/vllm/ci/builds/")
        for row in health["group_catalog"]
        for observation in row["observations"]
    )

    latency = ops_data["latency"]
    assert latency["source_pipeline"] == "ci"
    assert latency["branch"] == "main"
    assert latency["build_limit"] == 5
    assert latency["statistic"] == "median_of_per_nightly_group_wall_minutes"
    cohort_numbers = {build["number"] for build in latency["cohort"]["nightlies"]}
    assert len(cohort_numbers) <= 5
    for row in latency["rows"]:
        for side in (row.get("amd"), row.get("upstream")):
            if side:
                assert side["sample_count"] == len(side["samples"]) <= 5
                assert {sample["build_number"] for sample in side["samples"]} <= cohort_numbers


def test_amd_health_separates_same_build_test_groups_and_job_variants():
    segment = OPS_JS[
        OPS_JS.index("function renderAmdHealth"):
        OPS_JS.index("const AGENT_WINDOW_DAYS")
    ]
    for contract in (
        "summary.latest_job_variant_state_counts || summary.latest_state_counts",
        "summary.latest_job_variant_count !== undefined",
        "const latestTestGroups = summary.latest_test_group_counts || {}",
        "const logicalInventory = amdLogicalInventory(amdHealth)",
        "Browse logical test groups",
        "openAmdLogicalCatalog('Latest AMD logical test groups'",
        "latestTestGroups.available === true",
        "logicalBuild === Number(latestBuild)",
        "label: 'LATEST AMD TEST GROUPS'",
        "same-build logical test-group counts unavailable",
        "logicalTestGroupPresentation(latestTestGroups)",
        "latestTestGroups.count_basis || 'Unique source-aligned AMD test-group identities observed in this main CI nightly; topology-distinct routes remain separate and configured shards count once.'",
        "summary.retained_job_variant_count || summary.retained_group_count",
        "label: 'LATEST JOB VARIANTS'",
        "value: integer(latestVariantCount)",
        "integer(passing) + ' passing - ' + integer(nonPassing) + ' non-passing exact jobs'",
        "older variants retained only for history",
        "const currentVariants = currentPassing.concat(currentIncidents, currentUnknown)",
        "openAmdCatalog('Latest AMD job variants'",
        "older names remain available as history and are not treated as missing failures",
        "Soft results are raw warning observations, not confirmed incidents",
        "'Historical only'",
        "Not classified as current failures",
    ):
        assert contract in segment
    for presentation_contract in (
        "pass on every route",
        "pass on some hardware only",
        "non-passing everywhere",
    ):
        assert presentation_contract in OPS_JS

    for misleading in (
        "label: 'PASSING NOW'",
        "of observed groups",
        "AMD groups passing now",
        "AMD job groups",
        "exact job groups",
        "Search AMD test groups",
        "Filter AMD test groups",
        "Raw groups with a soft or hard result",
        "AMD test group'",
    ):
        assert misleading not in segment

    assert "currentIncidents.length + missing.length" not in segment
    assert "!['soft', 'hard', 'missing'].includes(latest)" not in OPS_JS


def test_raw_soft_results_are_presented_as_observations_not_incidents():
    for contract in (
        "Soft observations",
        "Latest AMD failure observations",
        "FAILURE OBSERVATIONS",
        "Soft results are raw warning observations, not confirmed incidents",
        "Current AMD failures to inspect",
    ):
        assert contract in OPS_JS
    for misleading in (
        "Running with incidents",
        "Latest available AMD incidents",
        "label: 'INCIDENTS NOW'",
        "Current AMD incidents to inspect",
        "Soft incident latest",
    ):
        assert misleading not in OPS_JS


@pytest.mark.live_data
def test_current_amd_health_keeps_latest_and_retained_counts_distinct(ops_data):
    summary = ops_data["amd_test_health"]["summary"]
    retained = summary.get("retained_group_count", summary["union_group_count"])
    assert retained == summary["group_count"] == len(
        ops_data["amd_test_health"]["group_catalog"]
    )
    assert summary["latest_group_count"] < retained








def test_architecture_and_test_group_history_show_exact_counts_at_a_glance():
    for contract in (
        "AMD architecture routes",
        "configured routes",
        "passing",
        "incident",
        "unobserved",
        "Complete test-group history",
        "Latest 30 exact runs - oldest to newest",
        "Explore all groups",
        "MEDIAN RETAINED-RUN PASS RATE",
        "Outcome timeline",
        "--ops-history-track-width",
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


def test_ci_health_navigation_is_scoped_history_safe_and_accessible():
    for contract in (
        "function tabList",
        "wrap.setAttribute('role', 'group')",
        "control.setAttribute('aria-pressed'",
        "wrap.scrollLeft = Math.max(0, centered)",
        "pendingTabFocus = items[targetIndex].id",
        "active.focus({preventScroll: true})",
        "pendingSegmentFocus = {group: groupLabel, id: item.id}",
        "active.control.focus({preventScroll: true})",
        "{id: 'coverage', label: 'AMD hardware'}",
        "{id: 'mirrors', label: 'AMD mirrors'}",
        "openHealthDataFreshness(ops)",
        "setQueryValue(queryKey || key, next, {history: 'push'})",
        "window.history.pushState(null, '', nextUrl.pathname",
    ):
        assert contract in OPS_JS

    for contract in (
        "const method = options && options.history === 'replace' ? 'replaceState' : 'pushState'",
        "window.addEventListener('popstate', syncLocationRoute)",
        "button.setAttribute('aria-current', 'page')",
    ):
        assert contract in DASHBOARD_NAV_JS

    for contract in (
        "header.setAttribute('role', 'button')",
        "header.setAttribute('tabindex', '0')",
        "header.setAttribute('aria-expanded', 'false')",
        "header.addEventListener('keydown'",
    ):
        assert contract in UTILS_JS


def test_ci_health_overview_surfaces_the_physical_amd_mirror_count():
    load_operations = OPS_JS[
        OPS_JS.index("async function loadOperations")
        : OPS_JS.index("function ownedHost")
    ]
    render_health = OPS_JS[
        OPS_JS.index("async function renderHealth")
        : OPS_JS.index("function amdHealthGroups")
    ]
    mirror_summary = AMD_MIRROR_INVENTORY_JS[
        AMD_MIRROR_INVENTORY_JS.index("function renderAmdMirrorSummary")
        : AMD_MIRROR_INVENTORY_JS.index("function renderAmdMirrorInventory")
    ]

    # The overview physical count comes from the current main CI source scan,
    # independently of the runtime logical-group and configured parity counts.
    assert "state.healthView === 'overview'" in load_operations
    assert "loadOperationSections({}, ['test_group_parity'])" in load_operations
    assert "loadAmdMirrorInventoryModule().catch(function () { return null; })" in load_operations
    assert "summary.total" in AMD_MIRROR_INVENTORY_JS
    for contract in (
        "inventoryState.total",
        "ops-health-mirror-summary",
        "AMD MIRROR CONFIGURATION ON MAIN",
        "configured AMD mirror groups",
        "runtime group count unavailable",
        "ui.n('button', 'ops-health-mirror-summary",
    ):
        assert contract in mirror_summary
    assert "summaryCard: renderAmdMirrorSummary" in AMD_MIRROR_INVENTORY_JS
    assert "mirrorRenderer.summaryCard(" in render_health
    assert "logicalGroups.available ? logicalTotal : null" in render_health
    assert "if (mirrorRenderer && typeof mirrorRenderer.summaryCard === 'function')" in render_health
    assert "AMD mirror summary render API is unavailable" not in render_health
    assert "navigateTo('ci-health', {healthView: 'mirrors'})" in render_health

    assert ".ops-page .ops-health-mirror-summary" in OPS_CSS


def test_ci_health_amd_mirrors_uses_the_physical_declaration_inventory():
    for contract in (
        "state.healthView === 'mirrors'",
        "(ops.test_group_parity || {}).mirror_inventory",
        "AMD_MIRROR_INVENTORY_MODULE_URL",
        "function loadGlobalScript(url, globalName, label, validate)",
        "const cacheKey = 'script:' + url",
        "cache.delete(cacheKey)",
        "loadAmdMirrorInventoryModule()",
        "const renderer = window.AmdMirrorInventory",
        "renderer.render(host, (ops.test_group_parity || {}).mirror_inventory || {}, amdMirrorUiHelpers())",
        "openTableBrowser,",
    ):
        assert contract in OPS_JS

    for contract in (
        "global.AmdMirrorInventory = Object.freeze({",
        "render: renderAmdMirrorInventory",
        "const SOURCE_ASSET_URL = 'data/vllm/ci/operations_v2/test_group_parity.json'",
        "summary.total",
        "retention.group_index",
        "groupIndex.complete_relative_to_source",
        "The published aggregate is marked incomplete, and",
        "{label: 'Source definition ID', value: row.key}",
        "ops-mirror-hero",
        "ops-mirror-area-bars",
        "ops-mirror-preview-list",
        "const breakdownComplete = inventoryState.detailComplete",
        "Complete hardware breakdown unavailable",
        "ops-mirror-area-breakdown",
        "AMD mirror inventory",
        "'Browse all ' + ui.integer(mirrorCount) + ' AMD mirrors'",
        "One top-level YAML step with a non-empty mirror.amd mapping counts once",
    ):
        assert contract in AMD_MIRROR_INVENTORY_JS

    assert "function amdMirrorInventoryRows" not in OPS_JS
    assert "function renderAmdMirrorInventory" not in OPS_JS
    assert "ui.statusStrip(" not in AMD_MIRROR_INVENTORY_JS
    assert "ui.compactTablePanel(" not in AMD_MIRROR_INVENTORY_JS
    assert "{label: 'Buildkite step key', value: row.key}" not in AMD_MIRROR_INVENTORY_JS
    assert 'src="assets/js/amd-mirror-inventory.js' not in INDEX
    for selector in (
        ".ops-page .ops-mirror-hero",
        ".ops-page .ops-mirror-area-bars",
        ".ops-page .ops-mirror-preview-list",
        ".ops-page .ops-mirror-area-breakdown",
    ):
        assert selector in OPS_CSS


def test_ci_health_metrics_do_not_double_as_unlabeled_navigation():
    render_health = OPS_JS[
        OPS_JS.index("async function renderHealth")
        : OPS_JS.index("function amdHealthGroups")
    ]
    assert "LATEST UNIQUE TEST GROUPS" not in render_health
    assert "health-upstream-scheduled-gating" not in render_health
    assert "Inspect all logical test groups →" in render_health
    assert "onOpen: function () { navigateTo('ci-analytics'" not in render_health.split(
        "Related investigation views"
    )[0]


def test_ci_health_previews_remove_repeated_counts_and_redundant_columns():
    render_health = OPS_JS[
        OPS_JS.index("async function renderHealth")
        : OPS_JS.index("function amdHealthGroups")
    ]
    for helper in ("function openParityRows", "function openAmdLogicalCatalog"):
        assert helper in OPS_JS
    for contract in (
        "ops-health-hero-grid",
        "Potential open gaps by test area",
        "Tables open in a searchable popup",
        "Data freshness",
    ):
        assert contract in render_health
    assert "integer(targetRows.length) + ' target groups in the selected result state'" not in render_health
    assert "integer(definitions.length) + ' standalone comparison rows" not in render_health




def test_reviewed_upstream_test_group_parity_is_first_class_and_action_first():
    for contract in (
        "{id: 'parity', label: 'Upstream parity'}",
        "ops.test_group_parity || {}",
        "UPSTREAM PARITY ON MAIN",
        "Applicable test groups covered",
        "Potential open gaps by test area",
        "Browse all ' + integer(actionTotal) + ' potential open gaps",
        "Complete current upstream inventory",
        "logical AMD test groups",
        "function openTestGroupParityDetail",
        "function openParityRows",
        "healthParityState: 'action'",
        "healthParityArea: 'all'",
        "is-not-targeted",
    ):
        assert contract in OPS_JS or contract in OPS_CSS
    for obsolete_parity_label in (
        "#50519",
        "published PR",
        "local candidate",
        "direct upstream links",
        "WITH PROPOSED CHANGES",
        "Proposed (",
    ):
        assert obsolete_parity_label not in OPS_JS
    for ambiguous_label in (
        "BEST-HARDWARE TEST GROUPS",
        "UPSTREAM SCHEDULED GATING",
        "GATED TEST GROUPS",
        "Logical gating groups",
        "Gated groups by Buildkite queue",
    ):
        assert ambiguous_label not in OPS_JS


def test_runtime_test_group_card_names_numerator_and_denominator():
    for contract in (
        "LATEST AMD TEST GROUPS",
        "amdHealthSummary.latest_test_group_counts || {}",
        "integer(passingAny) + ' / ' + integer(total) + ' passing'",
        "pass on some hardware only",
        "non-passing everywhere",
        "Configured AMD test groups",
    ):
        assert contract in OPS_JS






def test_amd_logical_inventory_accepts_reconciled_unaligned_identity_fallback():
    inventory_start = OPS_JS.index("function amdLogicalInventory")
    inventory_end = OPS_JS.index("function amdLogicalStateLabel", inventory_start)
    inventory_helper = OPS_JS[OPS_JS.index("function currentAmdHealth"):OPS_JS.index("function amdHealthGroups")] + OPS_JS[inventory_start:inventory_end]

    assert "inventory.route_map_aligned !== true" not in inventory_helper
    for contract in (
        "reconciliation.matches_latest_test_group_counts !== true",
        "counts.available !== true",
        "!buildAligned",
        "Number(counts.total) !== inventory.rows.length",
    ):
        assert contract in inventory_helper
    if not shutil.which("node"):
        pytest.skip("node is not available")
    script = f"""
const assert = require('assert');
{inventory_helper}
const reconciled = {{
  source_pipeline: 'ci', job_scope: 'amd_gpu',
  summary: {{latest_test_group_counts: {{available: true, build_number: 123, total: 1}}}},
  latest_logical_test_groups: {{
    available: true,
    build_number: 123,
    route_map_aligned: false,
    reconciliation: {{matches_latest_test_group_counts: true}},
    rows: [{{state: 'passing_all'}}],
  }},
}};
assert.equal(amdLogicalInventory(reconciled).available, true);
const missingBuilds = JSON.parse(JSON.stringify(reconciled));
missingBuilds.summary.latest_test_group_counts.build_number = null;
missingBuilds.latest_logical_test_groups.build_number = null;
assert.equal(amdLogicalInventory(missingBuilds).available, false);
const mismatchedBuilds = JSON.parse(JSON.stringify(reconciled));
mismatchedBuilds.latest_logical_test_groups.build_number = 124;
assert.equal(amdLogicalInventory(mismatchedBuilds).available, false);
"""
    result = subprocess.run(
        ["node", "-e", script],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr




def test_retired_diagnostics_do_not_link_private_collector_state():
    assert "state.healthView === 'diagnostics'" not in OPS_JS
    assert "upstreamScheduledGating" not in OPS_JS


def test_blocked_nightly_is_separate_from_the_latest_test_signal():
    for contract in (
        "function amdNightlyPresentation",
        "Infra blocked",
        "test groups never started",
        "latest test signal #",
        "Latest nightly has no test signal.",
        "Latest signal #",
        "New and recurring failures are above zero; fixes are below.",
        "No pass/fail movement is inferred.",
    ):
        assert contract in OPS_JS
    assert "row.has_test_results !== false" in OPS_JS
    assert "build.test_jobs_blocked" in OPS_JS


def test_nightly_assessment_uses_explicit_movement_rules():
    for contract in (
        "const CONFIRMED_INCIDENT_POLICY_ID = 'confirmed-incidents-v1'",
        "const OBSERVED_FAILURE_MOVEMENT_ID = 'observed-failure-movement-v1'",
        "function confirmedNightlyTransitions",
        "function nightlyFailureMovement",
        "function amdNightlyMovement",
        "currentFailures: newlyFailing + recurring",
        "previousFailures: recurring + fixed",
        "delta: newlyFailing - fixed",
        "Running with failures",
        "More failures",
        "Improved",
        "Changed, net even",
        "Stable failure count",
        "Recovered",
        "No net change",
        "fewer failures",
        "provisional while Buildkite is running",
        "no change is inferred",
    ):
        assert contract in OPS_JS
    assert "movement.currentFailures === incidentCount" in OPS_JS
    assert "movement.currentIncidents" not in OPS_JS
    assert "soft ? 'Degraded'" not in OPS_JS


def test_nightly_counts_are_labeled_as_exact_job_variants():
    assert "JOB VARIANTS OBSERVED" in OPS_JS
    assert "NEW FAILURES" in OPS_JS
    assert "RECURRING FAILURES" in OPS_JS
    assert "{label: 'FIXED'" in OPS_JS
    assert "nightly failure movement" in OPS_JS
    assert "{label: 'New failure'" in OPS_JS
    assert "{label: 'Recurring failure'" in OPS_JS
    assert "{label: 'Fixed'" in OPS_JS
    assert "Every current hard or soft failure is counted once" in OPS_JS
    assert "Missing or skipped jobs are omitted." in OPS_JS
    assert "return -Number(nightlyFailureCount(b, 'fixed') || 0)" in OPS_JS
    assert "Recurring confirmed" not in OPS_JS
    assert "Confirmed held" not in OPS_JS
    assert "New confirmed" not in OPS_JS
    assert "Still failing" not in OPS_JS
    assert "Open — no result" not in OPS_JS
    assert "movementBuilds = builds.filter" in OPS_JS
    assert "nightly regressions" not in OPS_JS
    assert "label: 'GROUPS OBSERVED'" not in OPS_JS


def test_failure_movement_helper_excludes_unobserved_incident_state():
    node = shutil.which("node")
    if not node:
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

const legacy = {
  has_test_results: true,
  transition_eligible: true,
  transitions: {
    policy_id: 'confirmed-incidents-v1',
    preceding_build_number: 100,
    new: [
      {group_id: 'hard'},
      {group_id: 'soft-confirmed', current_severity: 'soft', soft_streak: 2, transition_change: 'confirmed'},
    ],
    recurring: [{group_id: 'recurring'}],
    fixed: [{group_id: 'fixed'}],
    pending_soft: [
      {group_id: 'soft-started', transition_change: 'pending_started'},
      {group_id: 'soft-advanced', transition_change: 'pending_advanced'},
      {group_id: 'stale-pending', transition_change: 'held'},
    ],
    not_observed: [{group_id: 'stale-confirmed'}],
    indeterminate: [{group_id: 'unknown-confirmed'}],
  },
};
const movement = helpers.nightlyFailureMovement(legacy);
assert.deepEqual(Array.from(movement.new, function (row) { return row.group_id; }), [
  'hard', 'soft-started', 'soft-advanced',
]);
assert.deepEqual(Array.from(movement.recurring, function (row) { return row.group_id; }), [
  'recurring', 'soft-confirmed',
]);
assert.deepEqual(Array.from(movement.fixed, function (row) { return row.group_id; }), ['fixed']);
assert.equal(movement.new.some(function (row) { return row.group_id === 'stale-pending'; }), false);
assert.equal(movement.new.some(function (row) { return row.group_id === 'stale-confirmed'; }), false);

const counts = helpers.amdNightlyMovement(legacy);
assert.equal(counts.currentFailures, 5);
assert.equal(counts.previousFailures, 3);
assert.equal(counts.delta, 2);
assert.equal(counts.hasComparison, true);

const published = Object.assign({}, legacy, {
  failure_movement: {
    policy_id: 'observed-failure-movement-v1',
    available: true,
    preceding_build_number: 101,
    new: [{group_id: 'published-new'}],
    recurring: [],
    fixed: [{group_id: 'published-fixed'}],
  },
});
assert.deepEqual(
  Array.from(helpers.nightlyFailureMovement(published).new, function (row) { return row.group_id; }),
  ['published-new'],
);

const unavailableBuild = Object.assign({}, legacy, {
  has_test_results: false,
  transition_eligible: false,
});
const unavailable = helpers.nightlyFailureMovement(unavailableBuild);
assert.equal(unavailable.available, false);
assert.equal(helpers.nightlyFailureCount(unavailableBuild, 'new'), null);
assert.equal(helpers.amdNightlyMovement(unavailableBuild).policyAvailable, false);

const waitingBuild = {number: 12228, state: 'running', has_test_results: false};
const now = Date.parse('2026-08-20T16:00:00Z');
const freshWaiting = helpers.amdNightlyPresentation(
  waitingBuild, {}, '2026-08-20T14:00:00Z', now,
);
assert.equal(freshWaiting.label, 'Awaiting results');
assert.equal(freshWaiting.meta.includes('Buildkite is running'), true);
const staleWaiting = helpers.amdNightlyPresentation(
  waitingBuild, {}, '2026-08-20T12:00:00Z', now,
);
assert.equal(staleWaiting.label, 'Snapshot stale');
assert.equal(staleWaiting.tone, 'is-warning');
assert.equal(staleWaiting.meta, '#12228 - Last published while Buildkite was running; no parsed test groups in this snapshot');
"""
    result = subprocess.run(
        [node, "-e", script, str(ROOT / "docs" / "assets" / "js" / "ops-v2.js")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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


@pytest.mark.live_data
def test_current_architecture_signal_rows_sort_nonpassing_before_passes():
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

    first_passing = ranks.index(3)
    assert all(rank < 3 for rank in ranks[:first_passing])
    assert all(rank == 3 for rank in ranks[first_passing:])




def test_omni_helpers_execute_in_javascript():
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




def test_authoritative_group_catalog_preserves_id_and_variant_identity():
    assert "Array.isArray(reliability.group_catalog)" in OPS_JS
    assert "result = reliability.group_catalog.map" in OPS_JS
    assert "reliabilityCatalogCache" in OPS_JS
    assert "reliabilityCatalogIndexCache" in OPS_JS
    assert "'id:' + String(row.id || row.evidence_ref)" in OPS_JS
    assert "groupReliabilityByRef" in OPS_JS
    assert "row.queues" in OPS_JS
    assert "row.shard" in OPS_JS
    assert "byName: new Map()" in OPS_JS












def test_analytics_dns_view_is_fast_visual_drillable_and_coverage_honest():
    for contract in (
        "queueDns: QUEUE_DNS_LIVE_BASE + 'dns_failures.json'",
        "/dns-health-data/data/vllm/ci/",
        "queueDnsFallback: 'data/vllm/ci/dns_failures.json'",
        "async function loadQueueDns",
        "const sources = [SOURCE_ASSETS.queueDns, SOURCE_ASSETS.queueDnsFallback]",
        "Promise.any(attempts)",
        "QUEUE_DNS_ARBITRATION_MS = 200",
        "resolved.slice().sort(compareQueueDnsCandidates)",
        "queueDnsPreferredCandidate = newest",
        "if (tabId === 'ci-analytics' && state.analyticsView === 'dns') return {}",
        "if (state.analyticsView === 'dns') return []",
        "ops_analytics_dns_window",
        "analytics_dns_window: '24h'",
        "ops_analytics_dns_scope",
        "analytics_dns_scope: 'amd'",
        "{id: 'nightlies', label: 'AMD nightlies'}, {id: 'dns', label: 'DNS health'}",
        "ops_queue_dns_window",
        "function supportedRoute",
        "target = 'ci-analytics'",
        "DNS observation window",
        "JOBS WITH DNS OBSERVATIONS",
        "DNS observations by queue and physical node",
        "function renderQueueDnsNativeHistogram",
        "ops-dns-node-bar",
        "Affected queues only",
        "openQueueDnsNodeEvidence",
        "Exact Buildkite log evidence",
        "Exact evidence shown",
        "Exact evidence total",
        "Evidence retention truncated",
        "Job outcome",
        "Passed after observation",
        "A passing job is an observation in a job that ultimately passed, not an incident",
        "Outcome is correlation, not proof DNS caused the result",
        "passed_jobs",
        "soft_failed_jobs",
        "hard_failed_jobs",
        "QUEUE_DNS_OUTCOME_CONTRACT = 'dns-job-outcomes-v1'",
        "payload.outcome_contract !== QUEUE_DNS_OUTCOME_CONTRACT",
        "queueTimestamp(payload.retention.end_exclusive) !== generatedAtMs",
        "lastDnsRefreshAt = Date.now()",
        "host.dataset.renderToken !== analyticsRenderToken || state.analyticsView !== 'dns'",
        "{label: 'Time basis'",
        "{label: 'Hardware'",
        "The histogram continues to use the published affected-job row count",
        "Partial coverage - counts are lower bounds",
        "not_collected",
        "QUEUE_DNS_STALE_MS = 12 * 60 * 60 * 1000",
        "QUEUE_DNS_FETCH_TIMEOUT_MS = 8 * 1000",
        "function queueDnsWithTimeout",
        "function queueDnsFreshness",
        "function queueDnsEvidenceWindowRow",
        "window_metrics",
        "metric = row.window_metrics[windowId]",
        "function queueDnsMatchesPublishedScope",
        "DNS observations are stale",
        "queueDnsDisplayCount(nodeRow.episodes, coverage)",
        "queueDnsDisplayCount(nodeRow.huggingfaceAffectedJobs, coverage)",
        "cache.delete(SOURCE_ASSETS.queueDns)",
        "cache.delete(SOURCE_ASSETS.queueDnsFallback)",
        "Canonical AMD is the 12 standard MI250, MI300, and MI355 queues",
        "All active AMD GPU also includes other amd_mi* models and widths",
        "retired MI355B queues are excluded",
        "scopeControl.setAttribute('aria-describedby', scopeHelp.id)",
    ):
        assert contract in OPS_JS or contract in DASHBOARD_NAV_JS

    for label in (
        "Last hour",
        "Last 3 hours",
        "Last 12 hours",
        "Last day",
        "Last 3 days",
        "Last 7 days",
        "Last 30 days",
    ):
        assert label in OPS_JS

    assert "new Set(['amd-ci', 'ci'])" in OPS_JS
    assert "QUEUE_DNS_JOB_ID_RE" in OPS_JS
    assert "'/list?jid='" in OPS_JS
    assert "{id: 'canonical', label: 'Canonical AMD (12)'}" in OPS_JS
    assert "{id: 'amd', label: 'All active AMD GPU'}" in OPS_JS
    assert "state.analyticsDnsScope" in OPS_JS
    assert "{id: 'dns', label: 'DNS'}" not in OPS_JS
    assert "row.url" not in OPS_JS[
        OPS_JS.index("function queueDnsEvidenceUrl")
        : OPS_JS.index("function queueDnsEvidenceForNode")
    ]
    for contract in (
        ".ops-page .ops-dns-summary",
        ".ops-page .ops-dns-stale-warning",
        ".ops-page .ops-dns-queue-grid",
        ".ops-page .ops-dns-node-bar:focus-visible",
        ".ops-page .ops-dns-bar-segment.is-passed",
        ".ops-dns-summary-item.is-danger .ops-dns-summary-value",
        "#main-content .ops-page .ops-dns-method-summary",
    ):
        assert contract in OPS_CSS

    render_body = OPS_JS[
        OPS_JS.index("async function render(tabId") : OPS_JS.index("window.OpsV2 =")
    ]
    load_dns_body = OPS_JS[
        OPS_JS.index("async function loadQueueDns") : OPS_JS.index("function queueDnsWindow")
    ]
    assert "&& !force && Date.now() - lastDnsRefreshAt" not in render_body
    assert "lastDnsRefreshAt = Date.now()" not in render_body
    assert "lastDnsRefreshAt = Date.now()" in load_dns_body
    assert "if (host.dataset.renderToken !== token) return false" in render_body
    dns_invalidation = render_body[
        render_body.index("if (tabId === 'ci-analytics' && state.analyticsView === 'dns'")
        : render_body.index("const ops = await loadOperations(tabId)")
    ]
    assert "queueDnsRefreshDue()" in dns_invalidation
    assert "!force" not in dns_invalidation


def test_queue_dns_helpers_enforce_counts_scope_coverage_and_exact_urls():
    if not shutil.which("node"):
        pytest.skip("node is not available")
    script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const validJobId = '01a00c92-9cab-4dd2-9a75-32210e739d02';
const rows = [
  {queue: 'amd_mi300_1', node: 'node-a', hardware: 'MI300', affected_jobs: 2, episodes: 3, huggingface_affected_jobs: 1, evidence_total: 2, passed_jobs: 1, soft_failed_jobs: 1, hard_failed_jobs: 0},
  {queue: 'amd_mi300_1', node: 'node-a', hardware: 'MI300', affected_jobs: 1, episodes: 2, huggingface_affected_jobs: 1, evidence_total: 0, passed_jobs: 1, soft_failed_jobs: 0, hard_failed_jobs: 0},
  {queue: 'amd_mi300_1', node: '', hardware: 'MI300', affected_jobs: 1, episodes: 1, huggingface_affected_jobs: 0, evidence_total: 0, passed_jobs: 0, soft_failed_jobs: 0, hard_failed_jobs: 1},
  {queue: 'amd_mi300_8', node: 'node-b', hardware: 'MI300', affected_jobs: 5, episodes: 7, huggingface_affected_jobs: 4, evidence_total: 1, passed_jobs: 4, soft_failed_jobs: 0, hard_failed_jobs: 1},
  {queue: 'amd_mi355b_1', node: 'retired-node', hardware: 'MI355', affected_jobs: 100, episodes: 100, huggingface_affected_jobs: 100, evidence_total: 100, passed_jobs: 100, soft_failed_jobs: 0, hard_failed_jobs: 0},
  {queue: 'gpu_queue', node: 'upstream-node', hardware: 'H100', affected_jobs: 200, episodes: 200, huggingface_affected_jobs: 200, evidence_total: 200, passed_jobs: 200, soft_failed_jobs: 0, hard_failed_jobs: 0},
];
const completeCoverage = {
  status: 'complete', complete: true, discovery_complete: true,
  eligible_jobs: 20, scanned_jobs: 20, positive_jobs: 9,
  negative_jobs: 11, pending_jobs: 0, unavailable_jobs: 0, oversize_jobs: 0,
};
function windowBlock(hours, blockRows) {
  return {
    start: new Date(Date.parse('2026-08-17T10:00:00Z') - hours * 60 * 60 * 1000).toISOString().replace('.000Z', 'Z'),
    end_exclusive: '2026-08-17T10:00:00Z',
    coverage: completeCoverage,
    totals: {
      affected_jobs: 309, episodes: 313, huggingface_affected_jobs: 306,
      queues: 4, nodes: 6, evidence_total: 303,
      passed_jobs: 306, soft_failed_jobs: 1, hard_failed_jobs: 2,
    },
    rows: blockRows,
  };
}
const canonicalWindowOptions = [
  {id: '1h', label: 'Last hour', hours: 1},
  {id: '3h', label: 'Last 3 hours', hours: 3},
  {id: '12h', label: 'Last 12 hours', hours: 12},
  {id: '24h', label: 'Last day', hours: 24},
  {id: '72h', label: 'Last 3 days', hours: 72},
  {id: '168h', label: 'Last 7 days', hours: 168},
  {id: '720h', label: 'Last 30 days', hours: 720},
];
function evidenceMetric(firstAt, lastAt, episodes, matchCount, signatureIds, targetCategories) {
  return {
    first_at: firstAt, last_at: lastAt, episodes: episodes, match_count: matchCount,
    signature_ids: signatureIds.slice(), target_categories: targetCategories.slice(),
  };
}
function repeatedWindowMetrics(windowIds, metric) {
  return Object.fromEntries(windowIds.map(function (windowId) {
    return [windowId, {
      first_at: metric.first_at, last_at: metric.last_at, episodes: metric.episodes,
      match_count: metric.match_count, signature_ids: metric.signature_ids.slice(),
      target_categories: metric.target_categories.slice(),
    }];
  }));
}
const recentWindowIds = ['3h', '12h', '24h', '72h', '168h', '720h'];
const firstRecentMetric = evidenceMetric(
  '2026-08-17T08:00:00Z', '2026-08-17T08:00:05Z', 3, 9,
  ['temporary_name_resolution'], ['huggingface_hub'],
);
const secondRecentMetric = evidenceMetric(
  '2026-08-17T08:00:00Z', '2026-08-17T08:01:00Z', 3, 10,
  ['temporary_name_resolution'], ['huggingface_hub'],
);
const oldMetric = evidenceMetric(
  '2026-08-10T08:00:00Z', '2026-08-10T08:00:05Z', 1, 1,
  ['dns_resolution_failed'], ['unknown'],
);
const livePayload = {
  schema_version: 1,
  outcome_contract: 'dns-job-outcomes-v1',
  generated_at: '2026-08-17T10:00:00Z',
  retention: {start: '2026-07-18T10:00:00Z', end_exclusive: '2026-08-17T10:00:00Z', hours: 720},
  default_window: '24h',
  window_options: canonicalWindowOptions,
  count_basis: 'distinct_buildkite_job_attempts_with_strong_dns_evidence',
  scope: {pipelines: ['amd-ci', 'ci'], queue_scope: 'active_amd_gpu'},
  classifier: {id: 'dns-v1', target_categories: ['huggingface_hub', 'unknown']},
  coverage: Object.assign({}, completeCoverage, {
    discovery_start: '2026-07-18T10:00:00Z',
    discovery_end_exclusive: '2026-08-17T10:00:00Z',
  }),
  windows: Object.fromEntries(canonicalWindowOptions.map(function (option) {
    return [option.id, windowBlock(option.hours, rows)];
  })),
  evidence: {
    evidence_total: 2,
    shown: 1,
    truncated: true,
    items: [{
      id: 'a'.repeat(64), first_at: '2026-08-17T08:00:00Z', last_at: '2026-08-17T08:00:05Z',
      time_basis: 'job_finished_at', pipeline: 'amd-ci', queue: 'amd_mi300_1', node: 'node-a', hardware: 'MI300',
      build_number: 12112, job_id: validJobId, job_name: 'MUST NOT RENDER xoxb-secret', state: 'hard', episodes: 3, match_count: 9,
      signature_ids: ['temporary_name_resolution'], target_categories: ['huggingface_hub'], window_ids: recentWindowIds,
      window_metrics: repeatedWindowMetrics(recentWindowIds, firstRecentMetric),
      url: 'https://attacker.example/log',
    }, {
      id: 'a'.repeat(64), first_at: '2026-08-17T08:00:00Z', last_at: '2026-08-17T08:01:00Z',
      time_basis: 'job_finished_at', pipeline: 'amd-ci', queue: 'amd_mi300_1', node: 'node-a', hardware: 'MI300',
      build_number: 12112, job_id: validJobId, job_name: 'MUST NOT RENDER xoxb-secret', state: 'hard', episodes: 3, match_count: 10,
      signature_ids: ['temporary_name_resolution'], target_categories: ['huggingface_hub'], window_ids: recentWindowIds,
      window_metrics: repeatedWindowMetrics(recentWindowIds, secondRecentMetric),
    }, {
      id: 'b'.repeat(64), first_at: '2026-08-10T08:00:00Z', last_at: '2026-08-10T08:00:05Z',
      time_basis: 'job_finished_at', pipeline: 'ci', queue: 'amd_mi300_1', node: 'node-a', hardware: 'MI300',
      build_number: 10, job_id: validJobId, job_name: 'MUST NOT RENDER xoxb-secret', state: 'passed', episodes: 1, match_count: 1,
      signature_ids: ['dns_resolution_failed'], target_categories: ['unknown'], window_ids: ['720h'],
      window_metrics: repeatedWindowMetrics(['720h'], oldMetric),
    }],
  },
};
const pagesPayload = JSON.parse(JSON.stringify(livePayload));
pagesPayload.generated_at = '2026-08-17T10:30:00Z';
pagesPayload.retention.start = '2026-07-18T10:30:00Z';
pagesPayload.retention.end_exclusive = pagesPayload.generated_at;
pagesPayload.coverage.discovery_start = pagesPayload.retention.start;
pagesPayload.coverage.discovery_end_exclusive = pagesPayload.generated_at;
Object.entries(pagesPayload.windows).forEach(function (entry) {
  const option = canonicalWindowOptions.find(function (candidate) { return candidate.id === entry[0]; });
  entry[1].start = new Date(Date.parse(pagesPayload.generated_at) - option.hours * 60 * 60 * 1000).toISOString().replace('.000Z', 'Z');
  entry[1].end_exclusive = pagesPayload.generated_at;
});
const newerLivePayload = JSON.parse(JSON.stringify(livePayload));
newerLivePayload.generated_at = '2026-08-17T11:00:00Z';
newerLivePayload.retention.start = '2026-07-18T11:00:00Z';
newerLivePayload.retention.end_exclusive = newerLivePayload.generated_at;
newerLivePayload.coverage.discovery_start = newerLivePayload.retention.start;
newerLivePayload.coverage.discovery_end_exclusive = newerLivePayload.generated_at;
Object.entries(newerLivePayload.windows).forEach(function (entry) {
  const option = canonicalWindowOptions.find(function (candidate) { return candidate.id === entry[0]; });
  entry[1].start = new Date(Date.parse(newerLivePayload.generated_at) - option.hours * 60 * 60 * 1000).toISOString().replace('.000Z', 'Z');
  entry[1].end_exclusive = newerLivePayload.generated_at;
});
let fetchMode = 'hung-live';
let fetchCalls = 0;
const sandbox = {
  window: {__OPS_V2_TEST__: true, setTimeout: setTimeout, clearTimeout: clearTimeout},
  document: {addEventListener: function () {}},
  console: console,
  URL: URL,
  fetch: function (url) {
    fetchCalls += 1;
    if (String(url).includes('raw.githubusercontent.com')) {
      if (fetchMode === 'hung-live') return new Promise(function () {});
      return new Promise(function (resolve) {
        setTimeout(function () {
          resolve({ok: true, json: function () { return Promise.resolve(newerLivePayload); }});
        }, 20);
      });
    }
    return Promise.resolve({ok: true, json: function () { return Promise.resolve(pagesPayload); }});
  },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, {filename: process.argv[1]});
const helpers = sandbox.window.OpsV2Test;

assert.equal(helpers.queueDnsPayloadValid(livePayload), true);
assert.equal(helpers.queueDnsPayloadValid(pagesPayload), true);
const compactedPayload = JSON.parse(JSON.stringify(livePayload));
Object.values(compactedPayload.windows).forEach(function (windowBlock) {
  windowBlock.rows = windowBlock.rows.slice(0, 3);
});
compactedPayload.evidence.items = compactedPayload.evidence.items.slice(0, 1);
compactedPayload.evidence.shown = 1;
compactedPayload.evidence.truncated = true;
compactedPayload.publication_retention = {
  policy: 'retain_exact_totals_with_deterministic_whole_row_prefixes',
  max_bytes: 8 * 1024 * 1024,
  complete_relative_to_source: false,
  aggregate_scalars_complete: true,
  window_rows: Object.fromEntries(canonicalWindowOptions.map(function (option) {
    return [option.id, {source: 6, published: 3, omitted: 3, complete: false}];
  })),
  evidence: {source: 3, published: 1, omitted: 2, complete: false},
};
assert.equal(helpers.queueDnsPayloadValid(compactedPayload), true);
const compactedWrongPublishedCount = JSON.parse(JSON.stringify(compactedPayload));
compactedWrongPublishedCount.publication_retention.window_rows['3h'].published = 4;
compactedWrongPublishedCount.publication_retention.window_rows['3h'].omitted = 2;
assert.equal(helpers.queueDnsPayloadValid(compactedWrongPublishedCount), false);
const compactedFalseCompleteness = JSON.parse(JSON.stringify(compactedPayload));
compactedFalseCompleteness.publication_retention.window_rows['3h'].complete = true;
assert.equal(helpers.queueDnsPayloadValid(compactedFalseCompleteness), false);
const compactedRowsExceedTotals = JSON.parse(JSON.stringify(compactedPayload));
compactedRowsExceedTotals.windows['3h'].totals.passed_jobs = 1;
compactedRowsExceedTotals.windows['3h'].totals.hard_failed_jobs = 307;
assert.equal(helpers.queueDnsPayloadValid(compactedRowsExceedTotals), false);
const legacyPayload = JSON.parse(JSON.stringify(livePayload));
delete legacyPayload.outcome_contract;
Object.values(legacyPayload.windows).forEach(function (windowBlock) {
  delete windowBlock.totals.passed_jobs;
  delete windowBlock.totals.soft_failed_jobs;
  delete windowBlock.totals.hard_failed_jobs;
  windowBlock.rows.forEach(function (row) {
    delete row.passed_jobs;
    delete row.soft_failed_jobs;
    delete row.hard_failed_jobs;
  });
});
assert.equal(helpers.queueDnsPayloadValid(legacyPayload), true);
assert.equal(helpers.queueDnsPayloadValid(Object.assign({}, livePayload, {outcome_contract: 'dns-job-outcomes-v2'})), false);
assert.equal(helpers.queueDnsPayloadValid(Object.assign({}, livePayload, {
  retention: Object.assign({}, livePayload.retention, {end_exclusive: '2026-08-17T09:59:59Z'}),
})), false);
const invalidRowOutcomes = JSON.parse(JSON.stringify(livePayload));
invalidRowOutcomes.windows['3h'].rows[0].passed_jobs += 1;
assert.equal(helpers.queueDnsPayloadValid(invalidRowOutcomes), false);
const invalidTotalOutcomes = JSON.parse(JSON.stringify(livePayload));
invalidTotalOutcomes.windows['3h'].totals.passed_jobs += 1;
assert.equal(helpers.queueDnsPayloadValid(invalidTotalOutcomes), false);
const mismatchedTotalOutcomes = JSON.parse(JSON.stringify(livePayload));
mismatchedTotalOutcomes.windows['3h'].totals.passed_jobs -= 1;
mismatchedTotalOutcomes.windows['3h'].totals.soft_failed_jobs += 1;
assert.equal(helpers.queueDnsPayloadValid(mismatchedTotalOutcomes), false);
assert.equal(helpers.queueDnsPayloadValid({schema_version: 1}), false);
assert.equal(helpers.queueDnsPayloadValid(Object.assign({}, livePayload, {windows: {}})), false);
assert.equal(helpers.queueDnsPayloadValid(Object.assign({}, livePayload, {window_options: canonicalWindowOptions.slice(1)})), false);
const missingDefaultBlock = JSON.parse(JSON.stringify(livePayload));
delete missingDefaultBlock.windows['24h'];
assert.equal(helpers.queueDnsPayloadValid(missingDefaultBlock), false);
const missingSelectedMetrics = JSON.parse(JSON.stringify(livePayload));
delete missingSelectedMetrics.evidence.items[0].window_metrics['3h'];
assert.equal(helpers.queueDnsPayloadValid(missingSelectedMetrics), false);
assert.equal(helpers.queueDnsWindow(livePayload, '3h').id, '3h');
assert.equal(helpers.queueDnsWindow(livePayload, '7d').id, '24h');

const coverage = helpers.queueDnsCoverage(livePayload, livePayload.windows['3h']);
assert.equal(coverage.complete, true);
assert.equal(helpers.queueDnsDisplayCount(0, coverage), '0');
const publicationBoundedPayload = Object.assign({}, livePayload, {
  publication_retention: {
    complete_relative_to_source: false,
    window_rows: {'3h': {source: 10, published: 4, omitted: 6, complete: false}},
  },
});
const publicationBoundedCoverage = helpers.queueDnsCoverage(
  publicationBoundedPayload,
  publicationBoundedPayload.windows['3h'],
);
assert.equal(publicationBoundedCoverage.complete, false);
assert.ok(publicationBoundedCoverage.notes.join(' ').includes('4 of 10 aggregate rows'));
assert.equal(helpers.queueDnsDisplayCount(2, publicationBoundedCoverage), '≥ 2');
const retainedPartialPayload = Object.assign({}, livePayload, {
  coverage: {
    status: 'partial', complete: false, discovery_complete: true,
    eligible_jobs: 100, scanned_jobs: 99, positive_jobs: 9,
    negative_jobs: 90, pending_jobs: 1, unavailable_jobs: 0, oversize_jobs: 0,
  },
});
const selectedCompleteCoverage = helpers.queueDnsCoverage(
  retainedPartialPayload,
  retainedPartialPayload.windows['3h'],
);
assert.equal(selectedCompleteCoverage.complete, true);
assert.equal(selectedCompleteCoverage.status, 'complete');
assert.equal(helpers.queueDnsDisplayCount(0, selectedCompleteCoverage), '0');
const selectedPartialCoverage = helpers.queueDnsCoverage(
  livePayload,
  Object.assign({}, livePayload.windows['3h'], {
    coverage: {
      status: 'partial', complete: false, discovery_complete: true,
      eligible_jobs: 20, scanned_jobs: 19, positive_jobs: 9,
      negative_jobs: 10, pending_jobs: 1, unavailable_jobs: 0, oversize_jobs: 0,
    },
  }),
);
assert.equal(selectedPartialCoverage.complete, false);
assert.equal(selectedPartialCoverage.status, 'partial');
assert.equal(helpers.queueDnsDisplayCount(0, selectedPartialCoverage), '-');
const structuralSeedCoverage = helpers.queueDnsCoverage(
  Object.assign({}, livePayload, {coverage: {status: 'not_collected', complete: false, discovery_complete: false}}),
  Object.assign({}, livePayload.windows['3h'], {coverage: {status: 'not_collected', complete: false, discovery_complete: false}}),
);
assert.equal(structuralSeedCoverage.complete, false);
assert.equal(helpers.queueDnsDisplayCount(0, structuralSeedCoverage), '-');
assert.equal(helpers.queueDnsDisplayCount(2, structuralSeedCoverage), '≥ 2');
const almostStale = helpers.queueDnsFreshness(
  livePayload,
  livePayload.windows['3h'],
  Date.parse('2026-08-17T21:59:59Z'),
);
assert.equal(almostStale.stale, false);
const stale = helpers.queueDnsFreshness(
  livePayload,
  livePayload.windows['3h'],
  Date.parse('2026-08-17T22:00:01Z'),
);
assert.equal(stale.stale, true);
assert.equal(stale.thresholdMs, 12 * 60 * 60 * 1000);
const staleWindowOnly = helpers.queueDnsFreshness(
  Object.assign({}, livePayload, {generated_at: '2026-08-17T22:30:00Z'}),
  livePayload.windows['3h'],
  Date.parse('2026-08-17T22:30:00Z'),
);
assert.equal(staleWindowOnly.stale, true);

const nodeRows = helpers.queueDnsNodeRows(livePayload.windows['3h'], 'amd');
assert.equal(JSON.stringify(nodeRows.map(function (row) { return [row.queue, row.node, row.affectedJobs]; })), JSON.stringify([
  ['amd_mi300_8', 'node-b', 5],
  ['amd_mi300_1', 'node-a', 3],
  ['amd_mi300_1', '(unidentified)', 1],
]));
assert.equal(nodeRows[1].episodes, 5);
assert.equal(nodeRows[1].huggingfaceAffectedJobs, 2);
assert.equal(nodeRows[0].outcomesAvailable, true);
assert.equal(nodeRows[0].passedJobs, 4);
assert.equal(nodeRows[0].softFailedJobs, 0);
assert.equal(nodeRows[0].hardFailedJobs, 1);
assert.equal(nodeRows[1].outcomesAvailable, true);
assert.equal(nodeRows[1].passedJobs, 2);
assert.equal(nodeRows[1].softFailedJobs, 1);
assert.equal(nodeRows[1].hardFailedJobs, 0);
assert.equal(nodeRows[2].outcomesAvailable, true);
assert.equal(nodeRows[2].passedJobs, 0);
assert.equal(nodeRows[2].softFailedJobs, 0);
assert.equal(nodeRows[2].hardFailedJobs, 1);
const nativeNodeOutcomes = helpers.queueDnsNodeOutcomes(livePayload, '3h', nodeRows[1]);
assert.equal(nativeNodeOutcomes.available, true);
assert.equal(nativeNodeOutcomes.passed, 2);
assert.equal(nativeNodeOutcomes.softFailed, 1);
assert.equal(nativeNodeOutcomes.hardFailed, 0);
const legacyCompletePayload = JSON.parse(JSON.stringify(legacyPayload));
legacyCompletePayload.evidence = {
  evidence_total: 1,
  shown: 1,
  truncated: false,
  items: [JSON.parse(JSON.stringify(livePayload.evidence.items[2]))],
};
const legacyCompleteNode = {
  queue: 'amd_mi300_1', node: 'node-a', nodeRaw: 'node-a',
  affectedJobs: 1, evidenceTotal: 1, outcomesAvailable: false,
  passedJobs: 0, softFailedJobs: 0, hardFailedJobs: 0,
};
const legacyCompleteOutcomes = helpers.queueDnsNodeOutcomes(
  legacyCompletePayload, '720h', legacyCompleteNode,
);
assert.deepEqual(
  JSON.parse(JSON.stringify(legacyCompleteOutcomes)),
  {available: true, passed: 1, softFailed: 0, hardFailed: 0},
);
const legacyTruncatedRows = helpers.queueDnsNodeRows(legacyPayload.windows['3h'], 'amd');
const legacyTruncatedNode = legacyTruncatedRows.find(function (row) {
  return row.queue === 'amd_mi300_1' && row.node === 'node-a';
});
const legacyTruncatedOutcomes = helpers.queueDnsNodeOutcomes(
  legacyPayload, '3h', legacyTruncatedNode,
);
assert.equal(legacyTruncatedOutcomes.available, false);
assert.deepEqual(
  JSON.parse(JSON.stringify(helpers.queueDnsOutcomeCounts({
    affected_jobs: 3, passed_jobs: 2, soft_failed_jobs: 1, hard_failed_jobs: 0,
  }))),
  {available: true, passed: 2, softFailed: 1, hardFailed: 0},
);
assert.equal(helpers.queueDnsOutcomeCounts({affected_jobs: 3}).available, false);
assert.equal(helpers.queueDnsOutcomePresentation('passed').label, 'Passed after observation');
assert.equal(helpers.queueDnsScope('all'), 'amd');
assert.equal(helpers.queueDnsMatchesPublishedScope('amd_mi300_1', 'all'), true);
assert.equal(helpers.queueDnsMatchesPublishedScope('amd-cpu', 'all'), false);
assert.equal(helpers.queueDnsMatchesPublishedScope('amd_rocm', 'all'), false);
assert.equal(helpers.queueDnsMatchesPublishedScope('gpu_queue', 'all'), false);
assert.equal(helpers.queueDnsMatchesPublishedScope('amd_mi355b_1', 'all'), false);
const publishedAllRows = helpers.queueDnsNodeRows(livePayload.windows['3h'], 'all');
assert.equal(JSON.stringify(publishedAllRows), JSON.stringify(nodeRows));
const queueRows = helpers.queueDnsQueueRows(livePayload.windows['3h'], 'amd', [
  'amd_mi300_1', 'amd_mi300_2', 'amd_mi355b_1', 'amd-cpu', 'gpu_queue',
]);
assert.equal(JSON.stringify(queueRows.map(function (row) { return [row.queue, row.affectedJobs]; })), JSON.stringify([
  ['amd_mi300_8', 5], ['amd_mi300_1', 4], ['amd_mi300_2', 0],
]));
assert.ok(!queueRows.some(function (row) { return row.queue === 'amd_mi355b_1'; }));
const allRouteQueueRows = helpers.queueDnsQueueRows(livePayload.windows['3h'], 'all', [
  'amd_mi300_1', 'amd_mi300_2', 'amd_mi355b_1', 'amd-cpu', 'gpu_queue',
]);
assert.equal(JSON.stringify(allRouteQueueRows), JSON.stringify(queueRows));

const expectedUrl = 'https://buildkite.com/vllm/amd-ci/builds/12112/list?jid=' + validJobId + '&tab=output';
assert.equal(helpers.queueDnsEvidenceUrl(livePayload.evidence.items[0]), expectedUrl);
assert.equal(helpers.queueDnsEvidenceUrl(Object.assign({}, livePayload.evidence.items[0], {pipeline: 'evil'})), '');
assert.equal(helpers.queueDnsEvidenceUrl(Object.assign({}, livePayload.evidence.items[0], {build_number: 0})), '');
assert.equal(helpers.queueDnsEvidenceUrl(Object.assign({}, livePayload.evidence.items[0], {job_id: 'not-a-uuid'})), '');
assert.equal(helpers.queueDnsEvidenceUrl(Object.assign({}, livePayload.evidence.items[0], {job_id: '00000000-0000-0000-0000-000000000000'})), '');
assert.equal(helpers.queueDnsEvidenceUrl({url: expectedUrl}), '');
const nodeEvidence = helpers.queueDnsEvidenceForNode(livePayload, '3h', 'amd_mi300_1', 'node-a');
assert.equal(nodeEvidence.length, 1);
assert.equal(nodeEvidence[0].match_count, 10);
assert.equal(Object.prototype.hasOwnProperty.call(nodeEvidence[0], 'window_metrics'), false);
assert.equal(Object.prototype.hasOwnProperty.call(nodeEvidence[0], 'url'), false);
assert.equal(Object.prototype.hasOwnProperty.call(nodeEvidence[0], 'job_name'), false);

const longJobWindowIds = canonicalWindowOptions.map(function (option) { return option.id; });
const recentGitHubMetric = evidenceMetric(
  '2026-08-17T09:30:00Z', '2026-08-17T09:31:00Z', 1, 4,
  ['name_or_service_unknown'], ['github'],
);
const retainedLongJobMetric = evidenceMetric(
  '2026-08-16T08:00:00Z', '2026-08-17T09:31:00Z', 2, 9,
  ['name_or_service_unknown', 'temporary_name_resolution'], ['huggingface_hub', 'github'],
);
const longJobMetrics = Object.fromEntries(longJobWindowIds.map(function (windowId) {
  return [windowId, ['72h', '168h', '720h'].includes(windowId)
    ? Object.assign({}, retainedLongJobMetric, {
      signature_ids: retainedLongJobMetric.signature_ids.slice(),
      target_categories: retainedLongJobMetric.target_categories.slice(),
    })
    : Object.assign({}, recentGitHubMetric, {
      signature_ids: recentGitHubMetric.signature_ids.slice(),
      target_categories: recentGitHubMetric.target_categories.slice(),
    })];
}));
const longJob = {
  id: 'c'.repeat(64), first_at: retainedLongJobMetric.first_at, last_at: retainedLongJobMetric.last_at,
  time_basis: 'log_timestamp', pipeline: 'amd-ci', queue: 'amd_mi300_1', node: 'node-long', hardware: 'MI300',
  build_number: 12113, job_id: validJobId, job_name: 'MUST NOT RENDER xoxb-secret', state: 'hard',
  episodes: retainedLongJobMetric.episodes, match_count: retainedLongJobMetric.match_count,
  signature_ids: retainedLongJobMetric.signature_ids.slice(), target_categories: retainedLongJobMetric.target_categories.slice(),
  window_ids: longJobWindowIds, window_metrics: longJobMetrics,
};
const selectedLongJob = helpers.queueDnsEvidenceWindowRow(longJob, '1h', livePayload.windows);
assert.ok(selectedLongJob);
assert.equal(selectedLongJob.first_at, recentGitHubMetric.first_at);
assert.equal(selectedLongJob.episodes, 1);
assert.equal(selectedLongJob.match_count, 4);
assert.equal(JSON.stringify(selectedLongJob.target_categories), JSON.stringify(['github']));
assert.equal(JSON.stringify(selectedLongJob.signature_ids), JSON.stringify(['name_or_service_unknown']));
assert.equal(Object.prototype.hasOwnProperty.call(selectedLongJob, 'window_metrics'), false);
const malformedLongJob = JSON.parse(JSON.stringify(longJob));
delete malformedLongJob.window_metrics['1h'];
assert.equal(helpers.queueDnsEvidenceWindowRow(malformedLongJob, '1h', livePayload.windows), null);

(async function () {
  assert.equal(helpers.queueDnsLastRefreshAt(), 0);
  const fastFallbackStarted = Date.now();
  const selected = await helpers.loadQueueDns(5);
  assert.ok(Date.now() - fastFallbackStarted < 100, 'hung live source must not block fast Pages fallback');
  assert.equal(selected.generated_at, pagesPayload.generated_at);
  assert.equal(selected.__sourceAsset, 'data/vllm/ci/dns_failures.json');
  const firstRefreshAt = helpers.queueDnsLastRefreshAt();
  assert.ok(firstRefreshAt > 0);
  assert.equal(helpers.queueDnsRefreshDue(firstRefreshAt + 5 * 60 * 1000 - 1), false);
  assert.equal(helpers.queueDnsRefreshDue(firstRefreshAt + 5 * 60 * 1000), true);
  assert.equal(fetchCalls, 2);
  const cached = await helpers.loadQueueDns();
  assert.equal(cached.generated_at, pagesPayload.generated_at);
  assert.equal(fetchCalls, 2, 'cached scope/window rerenders must not launch a new source race');
  assert.equal(helpers.queueDnsLastRefreshAt(), firstRefreshAt, 'cached rerenders must not reset the refresh clock');

  helpers.invalidateDnsData();
  assert.equal(helpers.queueDnsLastRefreshAt(), firstRefreshAt, 'cache invalidation alone must not reset the refresh clock');
  fetchMode = 'slow-newer-live';
  const upgraded = await helpers.loadQueueDns();
  assert.equal(upgraded.generated_at, newerLivePayload.generated_at);
  assert.ok(upgraded.__sourceAsset.includes('raw.githubusercontent.com'));
  assert.ok(helpers.queueDnsLastRefreshAt() > firstRefreshAt);
  assert.equal(fetchCalls, 4);

  const newest = [
    {payload: livePayload, priority: 0},
    {payload: pagesPayload, priority: 1},
  ].sort(helpers.compareQueueDnsCandidates);
  assert.equal(newest[0].payload.generated_at, pagesPayload.generated_at);
  const tied = [
    {payload: livePayload, priority: 1},
    {payload: livePayload, priority: 0},
  ].sort(helpers.compareQueueDnsCandidates);
  assert.equal(tied[0].priority, 0);
})().catch(function (error) {
  console.error(error);
  process.exitCode = 1;
});
"""
    result = subprocess.run(
        ["node", "-e", script, str(ROOT / "docs" / "assets" / "js" / "ops-v2.js")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr














def test_ops_render_marks_boot_state_and_records_handled_errors():
    render_body = OPS_JS[
        OPS_JS.index("async function render(tabId") : OPS_JS.index("window.OpsV2 =")
    ]
    assert "host.dataset.renderState = 'loading'" in render_body
    assert "host.dataset.renderState = 'ready'" in render_body
    assert "host.dataset.renderState = 'error'" in render_body
    assert "window.__recordBootIssue('ops-v2 render'" in render_body



def test_ci_health_uses_unique_group_policy_and_exact_evidence_drilldown():
    for contract in (
        "function matrixHealthPolicy",
        "policies.best_hardware",
        "function bestHardwareMatrixContract",
        "groups.length === Number(policy.included_groups || 0)",
        "function matrixHealthCollection",
        "function openMatrixHealthBrowser",
        "function openMatrixGroupEvidence",
        "Configured AMD test groups",
        "{mode: 'all', label: 'Test groups', count: policy.included_groups, tone: 'is-neutral'}",
        "n('span', '', 'Passing')",
        "generic best-of-hardware families",
        "explicit MI355-sensitive obligations",
        "health_groups",
        "classification_reason",
        "Filter configured test-group status",
        "Filter configured test-group classification",
        "Search group, reason, command, hardware, or queue",
        "filters: [",
        "Executed commands",
        "Command identity",
        "Open AMD test definitions",
        "Open latest AMD build",
        "This hardware-sensitive obligation uses its exact MI355 route",
        "This generic family passes when at least one configured AMD route passes",
        "matrixHealthOverview(",
        "if (state.healthView === 'coverage')",
    ):
        assert contract in OPS_JS
    for retired_contract in (
        "healthReduceDuplicates",
        "healthIgnoreMi355Only",
        "Reduce duplicates",
        "Ignore MI355-only",
        "resolved groups passing",
    ):
        assert retired_contract not in OPS_JS
    assert 'assets/css/ops-v2.css?v=17' in INDEX
    assert 'assets/js/ops-v2.js?v=32' in INDEX
    assert "assets/js/amd-mirror-inventory.js?v=3" in OPS_JS
    assert "Number(policy.passing_groups || 0) / included * 100" in OPS_JS
    assert "gated groups passing" not in OPS_JS
    for retired_gate_label in (
        "BEST-HARDWARE GATED GROUPS",
        "Best-hardware gated groups",
        "Best-hardware gated-group health",
        "Gating test groups",
        "{label: 'Gated test group', sticky:",
        "Gate classification",
        "Filter best-hardware gate status",
        "Filter best-hardware gate classification",
    ):
        assert retired_gate_label not in OPS_JS
    assert "openMatrixHealthBrowser('all')" in OPS_JS
    assert "shell.setAttribute('role', 'dialog')" in OPS_JS
    assert "shell.setAttribute('aria-modal', 'true')" in OPS_JS
    assert "requestAnimationFrame(function () { search.focus(); })" in OPS_JS
    for selector in (
        ".ops-page .ops-unique-health",
        ".ops-page .ops-unique-health-bar",
        ".ops-page .ops-unique-health-segment.is-mixed",
    ):
        assert selector in OPS_CSS




def test_retired_mi355b_queues_are_excluded_on_every_frontend_path():
    assert "function isRetiredQueue" in OPS_JS
    assert "name === 'amd_mi250_8'" not in OPS_JS
    assert "/^amd_mi355b(?:_|$)/i" in OPS_JS
    assert "isRetiredQueue(name)" in OPS_JS


def test_omni_mapping_controls_remain_scoped_after_queue_page_removal():
    assert "const mapping = omni.mapping_history || {}" in OPS_JS
    assert "Object.keys(omniByQueue).concat(Object.keys(mainByQueue))" in OPS_JS
    assert "INCOMING OMNI JOBS" in OPS_JS
    assert "OBSERVED OMNI MAPPINGS" in OPS_JS
    assert "state.queueScope" not in OPS_JS


def test_main_ci_analytics_preserves_health_nightlies_and_recent_latency():
    for contract in ("canonicalReliability(ops)", "return reliabilityForPipeline(ops, 'ci')", "{id: 'groups', label: 'AMD test health'}", "{id: 'latency', label: 'Latency comparison'}", "Current main CI AMD health", "will not substitute unmatched hardware or a different pipeline"):
        assert contract in OPS_JS
    assert "{id: 'flakes'" not in OPS_JS
    assert "{id: 'retries'" not in OPS_JS


def test_nightly_main_ci_hardware_selector_defaults_amd_and_is_route_backed():
    for contract in ("analyticsPipeline: 'ci-amd'", "['analyticsPipeline', 'analytics_pipeline', ['ci-amd', 'ci-cuda']]", "nightlyForCohort(ops, state.analyticsPipeline)", "{id: 'ci-amd', label: 'AMD gating jobs'}", "{id: 'ci-cuda', label: 'CUDA gating jobs'}", "Main CI nightly hardware cohort", "nightly.upstream_parity", "nightly.canonical_history"):
        assert contract in OPS_JS


def test_recent_latency_uses_exact_main_ci_evidence():
    assert "exactPipelineEvidenceUrl({url: job.url, build_number: item.build.number}, 'ci')" in OPS_JS
    assert "pipelineUrlMatches(url, 'ci', false, item.build.number)" in OPS_JS
    assert "source_pipeline !== 'ci'" in OPS_JS
    assert "build_limit !== 5" in OPS_JS
    assert "median_of_per_nightly_group_wall_minutes" in OPS_JS


def test_nightly_failure_drilldowns_only_show_the_selected_current_outcomes():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const sandbox = {
  window: {__OPS_V2_TEST__: true},
  document: {addEventListener: function () {}},
  console: console,
  URL: URL,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox);
const evidence = sandbox.window.OpsV2Test.nightlyBuildEvidence;
const hard = {group_id: 'hard', state: 'failed', build_number: 20};
const recurring = {group_id: 'recurring', state: 'failed', build_number: 20};
const soft = {group_id: 'soft', state: 'soft_failed', build_number: 20};
const fixed = {group_id: 'fixed', state: 'failed', current_state: 'passed', build_number: 19, current_url: 'https://buildkite.com/vllm/ci/builds/20#fixed'};
const unknown = {group_id: 'unknown', state: 'unknown'};
const missing = {group_id: 'missing'};
const stale = {group_id: 'stale', state: 'failed', build_number: 19};
const held = {group_id: 'held', state: 'failed', observed_in_current_build: false};
const build = {
  number: 20,
  has_test_results: true,
  failed_groups: [hard, recurring, fixed, soft, unknown, missing, stale, held, {state: 'failed', current_state: 'unknown'}],
  soft_failed_groups: [soft, hard, unknown, missing],
  failure_movement: {
    policy_id: 'observed-failure-movement-v1', available: true,
    new: [hard, soft], recurring: [recurring], fixed: [fixed],
  },
};
function ids(result) { return Array.from(result.rows, row => row.group_id); }
assert.deepStrictEqual(ids(evidence(build, 'hard')), ['hard', 'recurring']);
assert.deepStrictEqual(ids(evidence(build, 'soft')), ['soft']);
assert.deepStrictEqual(ids(evidence(build, 'new')), ['hard', 'soft']);
assert.deepStrictEqual(ids(evidence(build, 'recurring')), ['recurring']);
const fixedEvidence = evidence(build, 'fixed');
assert.deepStrictEqual(ids(fixedEvidence), ['fixed']);
assert.equal(fixedEvidence.rows[0].job_url, fixed.current_url);
assert.equal(fixedEvidence.rows[0].build_number, 20);
assert.deepStrictEqual(ids(evidence(build)), ['hard', 'soft', 'recurring', 'fixed']);
assert.equal(evidence(build, 'hard').label, 'Current hard failures');
assert.equal(evidence(build, 'soft').label, 'Current soft failures');
assert.equal(evidence(build).label, 'Failure movement');
assert.equal(fixed.build_number, 19, 'Source rows must not be mutated');

const noComparison = {...build, failure_movement: {}};
assert.deepStrictEqual(ids(evidence(noComparison, 'hard')), ['hard', 'recurring']);
assert.equal(evidence(noComparison, 'hard').available, true);
assert.equal(evidence(noComparison).available, false);
const noRawGroups = {...build};
delete noRawGroups.failed_groups;
delete noRawGroups.soft_failed_groups;
assert.deepStrictEqual(ids(evidence(noRawGroups, 'hard')), ['hard', 'recurring']);
assert.deepStrictEqual(ids(evidence(noRawGroups, 'soft')), ['soft']);
assert.deepStrictEqual(ids(evidence({...build, failed_groups: []}, 'hard')), [], 'Explicit current evidence wins over movement');
const noSignal = evidence({...build, has_test_results: false}, 'hard');
assert.equal(noSignal.available, false);
assert.equal(noSignal.rows.length, 0);
assert.equal(evidence({number: 20}, 'hard').available, false);
"""
    result = subprocess.run(
        [node, "-e", script, str(ROOT / "docs" / "assets" / "js" / "ops-v2.js")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr




def test_bounded_reliability_consumers_fail_closed_or_disclose_coverage():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const sandbox = {
  window: {__OPS_V2_TEST__: true, innerWidth: 1280},
  document: {addEventListener: function () {}},
  console: console,
  URL: URL,
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, {filename: process.argv[1]});
const helpers = sandbox.window.OpsV2Test;

const retainedGroup = {
  id: 'kept',
  name: 'Kept group',
  source_pipeline: 'ci',
  publication_history_complete: false,
  observations: [{
    source_pipeline: 'ci',
    build_number: 10,
    state: 'passed',
    observed_at: '2026-09-01T11:00:00Z',
    job_url: 'https://buildkite.com/vllm/ci/builds/10/steps/job',
  }],
};
const reliability = {
  available: true,
  source_pipeline: 'ci',
  cohort: {available: true, observed_from: '2026-08-01T12:00:00Z', observed_to: '2026-09-01T12:00:00Z'},
  group_catalog: [retainedGroup],
  publication_retention: {
    groups: {source: 2, published: 1, omitted: 1},
    observations: {source: 5, published: 1, omitted: 4},
  },
};
const catalogState = helpers.reliabilityPublicationState(reliability);
assert.equal(catalogState.complete, false);
assert.ok(catalogState.message.includes('1 of 2 groups'));
assert.ok(catalogState.message.includes('1 of 5 exact observations'));
assert.ok(catalogState.message.includes('rates, percentiles, and anomaly deltas'));
assert.equal(helpers.groupPublicationHistoryComplete(retainedGroup), false);
assert.equal(helpers.groupPublicationHistoryComplete({
  publication_history_complete: true,
  history_truncated: true,
  observation_count: 5,
  source_retained_observation_count: 2,
  retained_observation_count: 2,
}), false);
assert.equal(helpers.groupPublicationHistoryComplete({
  publication_history_complete: true,
  history_truncated: false,
  excluded_observation_count: 1,
  observation_count: 2,
  source_retained_observation_count: 2,
  retained_observation_count: 2,
}), false);
assert.equal(helpers.groupPublicationHistoryComplete({
  publication_history_complete: true,
  history_truncated: false,
  excluded_observation_count: 0,
  observation_count: 3,
  source_retained_observation_count: 3,
  retained_observation_count: 3,
}), true);

"""
    result = subprocess.run(
        [node, "-e", script, str(ROOT / "docs" / "assets" / "js" / "ops-v2.js")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    assert "Published test-group history" in OPS_JS
    assert "Complete test-group history" in OPS_JS


def test_bounded_group_history_dom_fails_closed():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');

class TextNode {
  constructor(text) {
    this.nodeType = 3;
    this.textContent = String(text);
    this.parentNode = null;
  }
}

class Element {
  constructor(tagName) {
    this.nodeType = 1;
    this.tagName = String(tagName).toUpperCase();
    this.className = '';
    this.childNodes = [];
    this.parentNode = null;
    this.attributes = {};
    this.dataset = {};
    this.style = {setProperty: function () {}};
    this._text = '';
    this.classList = {
      add: (...names) => {
        const existing = this.className.split(/\s+/).filter(Boolean);
        names.forEach(function (name) { if (name && !existing.includes(name)) existing.push(name); });
        this.className = existing.join(' ');
      },
    };
  }
  append(...children) {
    children.forEach((child) => {
      const node = child && child.nodeType ? child : new TextNode(child);
      node.parentNode = this;
      this.childNodes.push(node);
    });
  }
  appendChild(child) { this.append(child); return child; }
  removeChild(child) {
    const index = this.childNodes.indexOf(child);
    if (index >= 0) this.childNodes.splice(index, 1);
    child.parentNode = null;
    return child;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener() {}
  focus() {}
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get textContent() {
    return this._text + this.childNodes.map(function (child) { return child.textContent; }).join('');
  }
  set textContent(value) {
    this._text = String(value);
    this.childNodes = [];
  }
}

const document = {
  createElement: function (tagName) { return new Element(tagName); },
  createTextNode: function (text) { return new TextNode(text); },
  addEventListener: function () {},
};
const sandbox = {
  window: {
    __OPS_V2_TEST__: true,
    innerWidth: 1280,
    location: {href: 'https://example.test/#ci-analytics'},
  },
  document: document,
  console: console,
  URL: URL,
  requestAnimationFrame: function () {},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, {filename: process.argv[1]});
const helpers = sandbox.window.OpsV2Test;

const partialGroup = {
  id: 'partial',
  name: 'Partial group',
  source_pipeline: 'ci',
  publication_history_complete: false,
  observations: [{
    source_pipeline: 'ci',
    build_kind: 'main',
    build_number: 10,
    state: 'passed',
    observed_at: '2026-09-01T11:00:00Z',
    job_url: 'https://buildkite.com/vllm/ci/builds/10/steps/job-10',
  }],
};
const reliability = {
  group_catalog: [partialGroup],
  publication_retention: {
    groups: {source: 1, published: 1, omitted: 0},
    observations: {source: 5, published: 1, omitted: 4},
  },
};

sandbox.window.OpsV2.state.analyticsGroupCohort = 'main';
const historyHost = document.createElement('div');
helpers.renderGroupHistoryExplorer(historyHost, [partialGroup], {}, reliability);
const historyText = historyHost.textContent;
assert.ok(historyText.includes('LATEST PUBLISHED FAILURE'));
assert.ok(historyText.includes('None published'));
assert.ok(historyText.includes('Omitted rows may contain failures'));
assert.ok(historyText.includes('Passing streak unavailable'));
assert.ok(historyText.includes('published map'));
assert.equal(historyText.includes('No failures in this cohort'), false);
assert.equal(historyText.includes('-run current passing streak'), false);
assert.equal(historyText.includes('complete map'), false);

sandbox.window.OpsV2.state.analyticsGroupCohort = 'nightly';
const emptyNightlyHost = document.createElement('div');
helpers.renderGroupHistoryExplorer(emptyNightlyHost, [partialGroup], {}, reliability);
assert.ok(emptyNightlyHost.textContent.includes('bounded published history'));
assert.equal(emptyNightlyHost.textContent.includes('complete retained history'), false);

"""
    result = subprocess.run(
        [node, "-e", script, str(ROOT / "docs" / "assets" / "js" / "ops-v2.js")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr




def test_history_surfaces_have_exact_or_published_source_assets():
    assert "SOURCE_ASSETS" in OPS_JS
    assert "historyPointSources" in OPS_JS
    assert "Open published source data" in OPS_JS








def test_pipeline_evidence_links_fail_closed_in_the_renderer():
    assert "function pipelineUrlMatches" in OPS_JS
    assert "function exactPipelineEvidenceUrl" in OPS_JS
    assert "function exactPipelineBuildUrl" in OPS_JS
    assert "parsed.host !== 'buildkite.com'" in OPS_JS
    assert "parsed.protocol !== 'https:'" in OPS_JS
    assert "suffix[0] !== 'steps'" in OPS_JS
    assert "exactPipelineEvidenceUrl(row, sourcePipeline)" in OPS_JS
    assert "exactPipelineEvidenceUrl(observation, 'ci')" in OPS_JS
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


@pytest.mark.live_data
def test_current_omni_counts_and_history_reconcile(ops_data):
    jobs = ops_data["omni"]["current_jobs"]
    current = ops_data["omni"]["current"]
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
    history = ops_data["omni"]["history"]
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
    assert "function resetRouteScroll" in DASHBOARD_NAV_JS
    assert "window.scrollTo(0, 0)" in DASHBOARD_NAV_JS
    assert "main.scrollTop = 0" in DASHBOARD_NAV_JS
    assert "Configured AMD test groups" in OPS_JS
    assert "matrixHealthOverview(" in OPS_JS
    assert "if (state.healthView === 'coverage')" in OPS_JS




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
        "amd-health-browser",
        "amd-current-incidents",
        "latency-comparison",
        "latency-evidence",
        "nightly",
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


def test_recent_latency_rejects_legacy_sources_and_older_group_backfill():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const sandbox = {window: {__OPS_V2_TEST__: true}, document: {addEventListener() {}}, URL, console};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox);
const helpers = sandbox.window.OpsV2Test;
const nightlies = [105,104,103,102,101].map(number => ({number,created_at:'2026-10-08T00:00:00Z'}));
const metric = (number, mins) => ({build_number:number,duration_mins:mins});
const side = (samples, median) => ({samples,sample_count:samples.length,median_duration_mins:median});
const base = {schema_version:1,source_pipeline:'ci',branch:'main',build_limit:5,available:true,
 statistic:'median_of_per_nightly_group_wall_minutes',cohort:{nightlies},
 rows:[{id:'group',label:'Current group',match_status:'matched',amd:side([metric(105,20),metric(103,30)],25),upstream:side([metric(105,10)],10)}]};
let result = helpers.latencyComparison({latency:base});
assert.equal(result.available,true);
assert.equal(result.rows[0].amd.sample_count,2);
assert.equal(result.rows[0].ratio,2.5);
assert.equal(result.rows[0].delta_mins,15);
const legacy = JSON.parse(JSON.stringify(base)); legacy.source_pipeline='amd-ci';
assert.equal(helpers.latencyComparison({latency:legacy}).available,false);
assert.equal(helpers.latencyComparison({reliability:{platform_comparison:base}}).available,false);
const backfill = JSON.parse(JSON.stringify(base)); backfill.rows[0].amd=side([metric(100,999)],999);
result=helpers.latencyComparison({latency:backfill});
assert.equal(result.available,true); assert.equal(result.rows[0].amd,null); assert.equal(result.rows[0].ratio,null);
const duplicate = JSON.parse(JSON.stringify(base)); duplicate.rows[0].amd=side([metric(105,20),metric(105,30)],25);
assert.equal(helpers.latencyComparison({latency:duplicate}).rows[0].amd,null);
const missing = JSON.parse(JSON.stringify(base)); missing.rows[0].upstream=null;
assert.equal(helpers.latencyComparison({latency:missing}).rows[0].delta_mins,null);
const untimed = JSON.parse(JSON.stringify(base)); untimed.rows[0].amd.median_duration_mins=null;
assert.equal(helpers.latencyComparison({latency:untimed}).rows[0].amd,null);
assert.equal(helpers.latencyMetric(null),null); assert.equal(helpers.latencyMetric(''),null);
assert.equal(helpers.latencyMetric(-1),null); assert.equal(helpers.latencyMetric(0),0);
const columns=['job_id','step_id','url','queue','hardware','raw_name','started_at','finished_at','duration_mins'];
const packed=JSON.parse(JSON.stringify(base)); packed.job_columns=columns;
for(const source of [packed.rows[0].amd,packed.rows[0].upstream]) {
 for(const sample of source.samples) {
  sample.jobs=[['job-'+sample.build_number,'step','https://buildkite.com/vllm/ci/builds/'+sample.build_number+'/steps/job-'+sample.build_number,'amd_mi300_1','mi300','Current group','2026-10-08T00:00:00Z','2026-10-08T00:20:00Z',sample.duration_mins]];
 }
}
result=helpers.latencyComparison({latency:packed});
assert.equal(result.rows[0].amd.samples[0].jobs[0].job_id,'job-105');
assert.equal(result.rows[0].amd.samples[0].jobs[0].duration_mins,20);
assert.equal(result.rows[0].ratio,2.5);
assert.equal(Array.isArray(packed.rows[0].amd.samples[0].jobs[0]),true);
const dictionaries=JSON.parse(JSON.stringify(packed)); delete dictionaries.job_columns;
for(const source of [dictionaries.rows[0].amd,dictionaries.rows[0].upstream]) {
 for(const sample of source.samples) sample.jobs=sample.jobs.map(values=>Object.fromEntries(columns.map((column,index)=>[column,values[index]])));
}
result=helpers.latencyComparison({latency:dictionaries});
assert.equal(result.rows[0].amd.samples[0].jobs[0].job_id,'job-105');
const unknown=JSON.parse(JSON.stringify(packed)); unknown.job_columns[0]='unknown';
assert.equal(helpers.latencyComparison({latency:unknown}).available,false);
const reordered=JSON.parse(JSON.stringify(packed)); reordered.job_columns.reverse();
assert.equal(helpers.latencyComparison({latency:reordered}).available,false);
const undeclared=JSON.parse(JSON.stringify(packed)); delete undeclared.job_columns;
assert.equal(helpers.latencyComparison({latency:undeclared}).rows[0].amd,null);
const short=JSON.parse(JSON.stringify(packed)); short.rows[0].amd.samples[0].jobs[0].pop();
assert.equal(helpers.latencyComparison({latency:short}).rows[0].amd,null);
for(const bad of [null,true,'20',-1,Infinity,NaN]) {
 const invalid=JSON.parse(JSON.stringify(packed)); invalid.rows[0].amd.samples[0].jobs[0][8]=bad;
 assert.equal(helpers.latencyComparison({latency:invalid}).rows[0].amd,null);
}
const invalidTime=JSON.parse(JSON.stringify(packed)); invalidTime.rows[0].amd.samples[0].jobs[0][6]='not-a-time';
assert.equal(helpers.latencyComparison({latency:invalidTime}).rows[0].amd,null);
const invalidString=JSON.parse(JSON.stringify(packed)); invalidString.rows[0].amd.samples[0].jobs[0][3]=null;
assert.equal(helpers.latencyComparison({latency:invalidString}).rows[0].amd,null);
"""
    result = subprocess.run([node, "-e", script, str(OPS_JS_PATH)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_retired_features_have_only_useful_navigation_compatibility():
    for feature in ("ci-queue", "ci-hotness"):
        assert f"id: '{feature}'" not in UTILS_JS
        assert f"'{feature}'" not in OPS_JS
        assert feature in DASHBOARD_NAV_JS
    for retired in ("renderQueue", "renderTrajectory", "renderCapacityProjection", "renderPlatformFlakes", "renderPlatformRetries", "openTargetRows", "loadQueueLifecycle", "fetchJSONL"):
        assert f"function {retired}(" not in OPS_JS
    assert "target = 'ci-omni'" in DASHBOARD_NAV_JS
    assert "target = 'ci-health'" in DASHBOARD_NAV_JS
    assert "['flakes', 'retries']" in DASHBOARD_NAV_JS


def test_compact_nightly_cohorts_require_pipeline_hardware_and_unique_identity():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = r"""
const assert=require('assert'),fs=require('fs'),vm=require('vm');
const context={window:{__OPS_V2_TEST__:true},document:{addEventListener(){}},URL,console};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const select=context.window.OpsV2Test.nightlyForCohort;
const amd={source_pipeline:'ci',cohort_id:'ci-amd',job_scope:'amd_gpu',builds:[{number:30005}]};
const cuda={source_pipeline:'ci',cohort_id:'ci-cuda',job_scope:'cuda_gpu',builds:[{number:30004}]};
assert.equal(select({nightly:{pipelines:[amd,cuda]}},'ci-amd').builds[0].number,30005);
assert.equal(select({nightly:{pipelines:[amd,cuda]}},'ci-cuda').builds[0].number,30004);
assert.equal(select({nightly:{canonical_history:amd}},'ci-amd').builds[0].number,30005);
assert.equal(select({nightly:{upstream_parity:cuda}},'ci-cuda').builds[0].number,30004);
assert.equal(select({nightly:{pipelines:[amd,{...amd}],canonical_history:amd}},'ci-amd').builds.length,0);
assert.equal(select({nightly:{canonical_history:{...amd,source_pipeline:'amd-ci'}}},'ci-amd').builds.length,0);
assert.equal(select({nightly:{canonical_history:{...amd,job_scope:'cuda_gpu'}}},'ci-amd').builds.length,0);
assert.equal(select({nightly:{pipelines:[{...amd,cohort_id:undefined},{...cuda,cohort_id:undefined}]}},'ci-amd').builds.length,0);
"""
    result = subprocess.run([node, "-e", script, str(OPS_JS_PATH)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_parity_counts_reject_the_legacy_reviewed_source_snapshot():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = r"""
const assert=require('assert'),fs=require('fs'),vm=require('vm');
const context={window:{__OPS_V2_TEST__:true},document:{addEventListener(){}},URL,console};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const select=context.window.OpsV2Test.currentTestGroupParity;
const old={source:{main_commit:'a'.repeat(40)},summary:{main_complete_groups:139,applicable_groups:163},groups:[{state:'existing'}]};
assert.equal(select(old).available,false);assert.equal(select(old).summary.main_complete_groups,undefined);
const current={source:{pipeline:'ci',current_definition_commit_sha:'b'.repeat(40)},summary:{main_complete_groups:205,applicable_groups:212},groups:[]};
assert.equal(select(current).summary.main_complete_groups,205);
const incomplete={...current,source:{pipeline:'ci',current_definition_commit_sha:'short'}};
assert.equal(select(incomplete).available,false);
"""
    result=subprocess.run([node,"-e",script,str(OPS_JS_PATH)],text=True,capture_output=True,check=False)
    assert result.returncode == 0, result.stderr
