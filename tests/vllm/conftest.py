"""Ensure scripts/vllm is importable as 'vllm.ci.*' from tests/vllm/.

Without this, Python resolves 'vllm' to tests/vllm/ (this package)
instead of scripts/vllm/ where the CI modules live.
"""

# cspell:ignore nodeid
import sys
from pathlib import Path

# Insert scripts/ at the front of sys.path so 'from vllm.ci.models import ...'
# resolves to scripts/vllm/ci/models.py, not tests/vllm/.
_scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

import pytest  # noqa: E402

# Live-data tests by the publication surfaces they read. When the selector
# publishes every one of them as unavailable (candidate and last-known-good data
# both failed the audit), the failure is those surfaces', not the publication's.
# The most specific node wins; an empty set marks a policy or public-safety
# check that is never excused.
LIVE_DATA_TEST_SURFACES: dict[str, frozenset[str]] = {
    "test_data_freshness.py::TestCIDataFreshness::test_amd_test_matrix_exists": frozenset({"ci_core"}),
    "test_data_freshness.py::TestCIDataFreshness::test_amd_test_matrix_fresh": frozenset({"ci_core"}),
    "test_data_freshness.py::TestCIDataFreshness::test_analytics_exists": frozenset({"ci_analytics"}),
    "test_data_freshness.py::TestCIDataFreshness::test_ci_health_exists": frozenset({"ci_core"}),
    "test_data_freshness.py::TestCIDataFreshness::test_ci_health_fresh": frozenset({"ci_core"}),
    "test_data_freshness.py::TestCIDataFreshness::test_ci_health_has_amd_build": frozenset({"ci_core"}),
    "test_data_freshness.py::TestCIDataFreshness::test_workload_mapping_exists": frozenset({"queue_workload"}),
    "test_data_freshness.py::TestCIDataFreshness::test_workload_mapping_fresh_and_window_current": frozenset({"queue_workload"}),
    "test_data_freshness.py::TestQueueDataFreshness::test_latest_snapshot_fresh": frozenset({"queue"}),
    "test_data_freshness.py::TestQueueDataFreshness::test_latest_snapshot_valid": frozenset({"queue"}),
    "test_data_freshness.py::TestQueueDataFreshness::test_queue_data_not_empty": frozenset({"queue"}),
    "test_data_freshness.py::TestQueueDataFreshness::test_queue_lifecycle_aggregate_fresh": frozenset({"queue_lifecycle"}),
    "test_data_freshness.py::TestQueueDataFreshness::test_queue_lifecycle_exists": frozenset({"queue_lifecycle"}),
    "test_data_freshness.py::TestQueueDataFreshness::test_queue_timeseries_exists": frozenset({"queue"}),
    "test_data_integrity.py::TestAnalyticsData": frozenset({"ci_analytics"}),
    "test_data_integrity.py::TestAnalyticsData::test_ci_analytics_not_blank_when_amd_test_results_exist": (
        frozenset({"ci_analytics", "ci_core"})
    ),
    "test_data_integrity.py::TestCIHealthData": frozenset({"ci_core"}),
    "test_data_integrity.py::TestFrontendFiles::test_queue_stats_computable_from_builds": frozenset({"ci_analytics"}),
    "test_data_schemas.py::TestAmdTestMatrix": frozenset({"ci_core"}),
    "test_data_schemas.py::TestAnalytics": frozenset({"ci_analytics"}),
    "test_data_schemas.py::TestCiHealth": frozenset({"ci_core"}),
    "test_data_schemas.py::TestCiOwnership": frozenset({"ci_core"}),
    "test_data_schemas.py::TestConfigParity": frozenset({"ci_core"}),
    "test_data_schemas.py::TestDnsFailures": frozenset({"dns_health"}),
    "test_data_schemas.py::TestDnsFailures::test_public_payload_has_no_log_or_url_fields": frozenset(),
    "test_data_schemas.py::TestFailureTrends": frozenset({"ci_core"}),
    "test_data_schemas.py::TestFlakyTests": frozenset({"ci_core"}),
    "test_data_schemas.py::TestOpenQueueIssues": frozenset({"queue"}),
    "test_data_schemas.py::TestOpenQueueZombieIssues": frozenset({"queue"}),
    "test_data_schemas.py::TestProjectTestResults": frozenset({"ci_core"}),
    "test_data_schemas.py::TestQueueJobs": frozenset({"queue"}),
    "test_data_schemas.py::TestQueueLifecycle": frozenset({"queue_lifecycle"}),
    "test_data_schemas.py::TestQueueTimeseries": frozenset({"queue"}),
    "test_data_schemas.py::TestWorkloadMapping": frozenset({"queue_workload"}),
    "test_data_schemas.py::TestWorkloadMapping::test_query_is_bounded_and_contains_no_raw_records": frozenset(),
    "test_number_correctness.py::TestBuildStateIntegrity": frozenset({"ci_core"}),
    "test_number_correctness.py::TestCurrentMiExecutionScope::test_latest_mi_assertion_counts_match_exact_current_shard": (
        frozenset({"ci_core"})
    ),
    "test_number_correctness.py::TestGroupCountCorrectness": frozenset({"ci_core"}),
    "test_number_correctness.py::TestGroupCountCorrectness::test_ci_health_group_counts_match_jsonl": frozenset(),
    "test_number_correctness.py::TestGroupCountCorrectness::test_commit_aligned_definition_families_match_runtime_groups": frozenset(),
    "test_number_correctness.py::TestGroupCountCorrectness::test_per_hw_test_counts_match_jsonl": frozenset(),
    "test_number_correctness.py::TestLogParserJobStateOverride": frozenset({"ci_core"}),
    "test_perf_eval_dashboard.py::TestPerfEvalDataContract": frozenset({"perf_eval"}),
    "test_perf_eval_dashboard.py::TestPerfEvalDataContract::test_models_are_amd_only": frozenset(),
    "test_perf_eval_dashboard.py::TestPerfEvalDataContract::test_no_nvidia_models_present": frozenset(),
    "test_queue_automation.py::TestIntervalFilteringLogic": frozenset({"queue"}),
    "test_queue_automation.py::TestQueueTimeseriesSchema": frozenset({"queue"}),
    "test_queue_automation.py::TestQueueTimeseriesSchema::test_release_normalizer_prevents_workload_history_overstatement": frozenset(),
    "test_queue_automation.py::TestQueueViewContract::test_queue_source_percentile_ordering": frozenset({"queue"}),
    "test_rendering_data.py::TestJSRenderingSafety": frozenset({"ci_core"}),
    "test_rendering_data.py::TestNoJobStateMismatch": frozenset({"ci_core"}),
    "test_rendering_data.py::TestVLLMCIData": frozenset({"ci_core"}),
    "test_rendering_data.py::TestVLLMCIData::test_shard_bases_exists": frozenset(),
}


def live_data_test_surfaces(nodeid: str) -> frozenset[str]:
    """Surfaces a live-data test reads, by its most specific mapped node."""
    parts = nodeid.split("[", 1)[0].split("::")
    parts[0] = Path(parts[0]).name
    for end in range(len(parts), 1, -1):
        key = "::".join(parts[:end])
        if key in LIVE_DATA_TEST_SURFACES:
            return LIVE_DATA_TEST_SURFACES[key]
    return frozenset()


def excused_live_data_failure(nodeid: str, unavailable: frozenset[str]) -> frozenset[str]:
    """Return the unavailable surfaces that own a failing live-data test, if all do."""
    surfaces = live_data_test_surfaces(nodeid)
    return surfaces if surfaces and surfaces <= unavailable else frozenset()


_UNAVAILABLE_SURFACES: list[frozenset[str]] = []


def _published_unavailable_surfaces() -> frozenset[str]:
    if not _UNAVAILABLE_SURFACES:
        from vllm.audit_dashboard_data import ROOT, DashboardAudit

        _UNAVAILABLE_SURFACES.append(DashboardAudit(ROOT).unavailable_surfaces())
    return _UNAVAILABLE_SURFACES[0]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed or item.get_closest_marker("live_data") is None:
        return
    if not live_data_test_surfaces(item.nodeid):
        return
    excused = excused_live_data_failure(item.nodeid, _published_unavailable_surfaces())
    if excused:
        report.outcome = "skipped"
        report.longrepr = (
            str(item.path),
            item.location[1] or 0,
            f"Skipped: publication surface(s) {', '.join(sorted(excused))} are published as unavailable",
        )
