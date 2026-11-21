"""Exercise live workload assertions using actual bounded producer output."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from vllm import collect_workload_mapping as workload
from tests.vllm import test_data_freshness as freshness
from tests.vllm import test_data_schemas as schemas
from tests.vllm.test_collect_workload_mapping import _config, _slice_aware_fetcher
from tests.vllm.workload_live_contract import (
    assert_retention_coverage,
    assert_window_coverage,
)

NOW = datetime(2026, 10, 9, 20, 27, 12, tzinfo=timezone.utc)


def _produced(days):
    return workload.collect_workload_mapping(
        "unused", _config(), now=NOW, force_days=days,
        page_fetcher=_slice_aware_fetcher({}),
    )


@pytest.mark.parametrize("days", [2, 14, 90])
def test_live_workload_assertions_accept_truthful_observed_history(days, monkeypatch, tmp_path):
    data = _produced(days)
    directory = tmp_path / "vllm" / "ci"
    directory.mkdir(parents=True)
    (directory / "workload_mapping.json").write_text(json.dumps(data))
    monkeypatch.setenv("CI", "1")
    monkeypatch.setattr(freshness, "DATA", tmp_path)
    monkeypatch.setattr(schemas, "DATA", directory)
    monkeypatch.setattr(freshness, "_age_hours", lambda value: (NOW - freshness._parse_ts(value)).total_seconds() / 3600)
    freshness.TestCIDataFreshness().test_workload_mapping_fresh_and_window_current()
    schema = schemas.TestWorkloadMapping()
    schema.test_hourly_and_daily_ranges_match_declared_coverage()
    schema.test_window_truth_is_independent_of_open_daily_bucket()
    if days == 2:
        assert len(data["hourly"]) == 45 and len(data["daily"]) == 2
        assert data["coverage"]["hourly"]["missing_bucket_count"] == 124
        assert data["coverage"]["daily"]["missing_bucket_count"] == 88
        assert data["window"]["lower_bound"] is True
    else:
        assert data["window"]["collection_complete"] is True
        assert data["daily"][-1]["complete"] is False


@pytest.mark.parametrize("field,value", [
    ("expected_bucket_count", 45), ("bucket_count", 44),
    ("missing_bucket_count", 0), ("contiguous", True),
    ("collection_complete", True), ("expected_start", "2026-10-08T00:00:00Z"),
])
def test_partial_retention_rejects_false_counts_and_completeness(field, value):
    data = deepcopy(_produced(2))
    data["coverage"]["hourly"][field] = value
    with pytest.raises(AssertionError):
        assert_retention_coverage(data)


@pytest.mark.parametrize("field,value", [
    ("collection_complete", True), ("complete", True), ("lower_bound", False),
    ("start_date", "2026-10-08"),
])
def test_short_window_cannot_claim_full_collection(field, value):
    data = deepcopy(_produced(2))
    data["window"][field] = value
    with pytest.raises(AssertionError):
        assert_window_coverage(data)


def test_full_calendar_window_rejects_an_incomplete_source_day():
    data = deepcopy(_produced(14))
    data["daily"][0]["collection_complete"] = False
    with pytest.raises(AssertionError):
        assert_window_coverage(data)
