"""Source-defined configurations stay distinct from their physical MI routing."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from vllm.ci import runtime_families as rf
from vllm.ci.nightly_latency import build_current_nightly_latency
from vllm.collect_analytics import summarize_pipeline_builds
from vllm.main_ci_definitions import MainCISnapshot

PIN = "a" * 40
TREE = "b" * 40
CLOCK = "2026-10-09T20:32:03Z"


@pytest.fixture(autouse=True)
def no_provider_requests(monkeypatch):
    monkeypatch.setattr("requests.sessions.Session.request", lambda *_args, **_kwargs: pytest.fail("Offline family tests cannot request providers"))


def snapshot(pin=PIN, *, dpx_devices=1, parallelism=None):
    steps = [
        {"key": "mi300-lm", "label": ":amd: (MI300) LM Eval Small Models",
         "device": "mi300_1", "commands": ["pytest tests/lm_eval.py"]},
        {"key": "dpx-lm", "label": ":amd: (MI355 DPX) LM Eval Small Models",
         "device": "mi355_dpx", "num_devices": dpx_devices,
         "commands": ["pytest tests/lm_eval.py"]},
    ]
    if parallelism:
        steps[1].update(label=steps[1]["label"] + " %N", parallelism=parallelism)
    return MainCISnapshot(pin, {".buildkite/ci_config.yaml": {"job_dirs": [".buildkite/test_areas"]},
                                ".buildkite/test_areas/lm_eval.yaml": {"steps": steps}}, "", TREE)


def job(index, platform="dpx", *, key=None, minutes=12, shard=None):
    name = ":amd: (MI355 DPX) LM Eval Small Models" if platform == "dpx" else ":amd: (MI300) LM Eval Small Models"
    result = {"id": str(UUID(int=index)), "type": "script", "name": name,
              "step": {"id": str(UUID(int=100 + index))}, "state": "passed",
              "q": "amd_mi355_1" if platform == "dpx" else "amd_mi300_1",
              "started_at": "2026-10-09T06:00:00Z",
              "finished_at": f"2026-10-09T06:{minutes:02d}:00Z"}
    if key is not None:
        result["step"]["key"] = key
    if shard is not None:
        result["name"] += f" {shard}"
        result.update(parallel_group_index=shard, parallel_group_total=2)
    return result


def build(jobs, *, pin=PIN, number=93775):
    return {"number": number, "commit": pin, "source_definition_tree_sha": TREE,
            "branch": "main", "message": "Full CI run - nightly", "state": "passed",
            "created_at": "2026-10-09T05:00:00Z", "finished_at": "2026-10-09T07:00:00Z",
            "web_url": f"https://buildkite.com/vllm/ci/builds/{number}", "jobs": jobs}


def test_pinned_configuration_splits_same_title_without_changing_observed_evidence():
    catalog = rf.family_catalog_from_snapshot(snapshot())
    original = build([job(1, minutes=12), job(2, "mi300", minutes=16)])
    retained = deepcopy(original)
    proved = rf.annotate_build_source_families(original, catalog)
    assert original == retained
    assert all({key: row[key] for key in original["jobs"][index]} == original["jobs"][index]
               for index, row in enumerate(proved["jobs"]))
    normalized = summarize_pipeline_builds("ci", [proved])
    latency = build_current_nightly_latency(normalized, generated_at=CLOCK, source_available=True,
                                            source_identity_basis=rf.IDENTITY_BASIS)
    rows = {row["id"]: row["amd"] for row in latency["rows"]}
    assert set(rows) == {"lm eval small models", "lm eval small models (1 gpus)"}
    assert rows["lm eval small models"]["median_duration_mins"] == 16
    assert rows["lm eval small models (1 gpus)"]["median_duration_mins"] == 12
    assert rows["lm eval small models (1 gpus)"]["samples"][0]["jobs"][0]["queue"] == "amd_mi355_1"
    assert latency["source_identity_basis"] == rf.IDENTITY_BASIS
    assert all(row["source_step_key"] == "" for row in proved["jobs"])


def test_shards_use_their_own_pinned_bases_without_global_analyzer_state():
    catalog = rf.family_catalog_from_snapshot(snapshot(parallelism=2))
    original = build([job(1, minutes=12, shard=0), job(2, minutes=20, shard=1)])
    proved = rf.annotate_build_source_families(original, catalog)
    latency = build_current_nightly_latency(summarize_pipeline_builds("ci", [proved]),
                                            generated_at=CLOCK, source_available=True,
                                            source_identity_basis=rf.IDENTITY_BASIS)
    assert len(latency["rows"]) == 1
    assert latency["rows"][0]["amd"]["samples"][0]["duration_mins"] == 20
    assert len(latency["rows"][0]["amd"]["samples"][0]["jobs"]) == 2


@pytest.mark.parametrize("change", ["commit", "tree", "family", "declaration", "step", "key", "null_key", "duplicate"])
def test_runtime_family_claims_reject_contradictions(change):
    catalog = rf.family_catalog_from_snapshot(snapshot())
    proved = rf.annotate_build_source_families(build([job(1)]), catalog)
    row = proved["jobs"][0]
    if change == "commit":
        proved["commit"] = "c" * 40
    elif change == "tree":
        proved["source_definition_tree_sha"] = "c" * 40
    elif change == "family":
        row["source_family_key"] = "lm eval small models"
    elif change == "declaration":
        row["name"] = ":amd: (MI300) LM Eval Small Models"
    elif change == "step":
        row["step"]["id"] = ""
    elif change == "key":
        row["step"]["key"] = "mi300-lm"
    elif change == "null_key":
        row["step_key"] = None
    else:
        proved["jobs"].append(deepcopy(row))
    with pytest.raises(ValueError):
        rf.validate_build_source_families(proved)


def test_ambiguous_declared_route_is_rejected_even_when_family_is_equal():
    catalog = rf.family_catalog_from_snapshot(snapshot())
    clone = deepcopy(next(row for row in catalog["definitions"] if row["step_key"] == "dpx-lm"))
    clone.update(definition_id=".buildkite/test_areas/duplicate.yaml#other", step_key="other")
    catalog["definitions"] = sorted([*catalog["definitions"], clone], key=lambda row: row["definition_id"])
    with pytest.raises(ValueError, match="unambiguous"):
        rf.annotate_build_source_families(build([job(1)]), catalog)


def test_exact_source_key_is_authoritative_but_cannot_contradict_qualified_label():
    catalog = rf.family_catalog_from_snapshot(snapshot())
    proved = rf.annotate_build_source_families(build([job(1, key="dpx-lm")]), catalog)
    assert proved["jobs"][0]["source_binding_basis"] == "runtime_step_key"
    assert proved["jobs"][0]["source_step_key"] == "dpx-lm"
    wrong = build([job(1, "mi300", key="dpx-lm")])
    with pytest.raises(ValueError, match="contradicts"):
        rf.annotate_build_source_families(wrong, catalog)


def test_cached_claims_are_rederived_and_cannot_authorize_a_different_family():
    catalog = rf.family_catalog_from_snapshot(snapshot())
    cached = rf.annotate_build_source_families(build([job(1)]), catalog)
    cached["jobs"][0]["source_family_key"] = "lm eval small models"
    with pytest.raises(ValueError, match="Restored"):
        rf.annotate_build_source_families(cached, catalog)


def test_each_nightly_uses_its_own_source_commit_configuration():
    first = rf.annotate_build_source_families(build([job(1)], number=93775), rf.family_catalog_from_snapshot(snapshot()))
    second_pin = "c" * 40
    second = rf.annotate_build_source_families(build([job(2)], pin=second_pin, number=93774),
                                              rf.family_catalog_from_snapshot(snapshot(second_pin, dpx_devices=2)))
    latency = build_current_nightly_latency(summarize_pipeline_builds("ci", [first, second]),
                                            generated_at=CLOCK, source_available=True,
                                            source_identity_basis=rf.IDENTITY_BASIS)
    assert {row["id"] for row in latency["rows"]} == {"lm eval small models (1 gpus)", "lm eval small models (2 gpus)"}
    assert all(row["amd"]["sample_count"] == 1 for row in latency["rows"])


def test_explicit_current_contract_rejects_missing_or_mixed_catalogs():
    proved = rf.annotate_build_source_families(build([job(1)]), rf.family_catalog_from_snapshot(snapshot()))
    legacy = build([job(2)], number=93774)
    with pytest.raises(ValueError, match="catalog|definition tree"):
        build_current_nightly_latency(summarize_pipeline_builds("ci", [proved, legacy]),
                                      generated_at=CLOCK, source_available=True,
                                      source_identity_basis=rf.IDENTITY_BASIS)


def test_family_acquisition_is_bounded_to_the_global_latest_five(monkeypatch):
    from vllm import collect_analytics as ca
    catalogs = []
    raw = []
    for number in range(93770, 93776):
        candidate = build([job(number)], number=number)
        created = datetime(2026, 10, 9, 5, tzinfo=timezone.utc) - timedelta(hours=93775 - number)
        candidate["created_at"] = created.isoformat()
        candidate["source_scope_index"] = {"version": 1, "commit_sha": PIN, "definition_tree_sha": TREE, "cpu_routes": []}
        raw.append(candidate)
    catalog = rf.family_catalog_from_snapshot(snapshot())
    def load(index, *, expected_commit):
        catalogs.append((index["definition_tree_sha"], expected_commit))
        return catalog
    monkeypatch.setattr(rf, "family_catalog_from_scope_index", load)
    proved = ca.annotate_current_nightly_families(raw, generated_at=CLOCK)
    assert len(catalogs) == 5
    assert "source_family_catalog" not in proved[0]
    assert all("source_family_catalog" in candidate for candidate in proved[1:])
    assert [candidate["created_at"] for candidate in proved] == [candidate["created_at"] for candidate in raw]


def test_reliability_preserves_execution_denominators_and_adds_verified_families():
    from vllm.ci.reliability_history import build_all_main_reliability
    original = build([job(1), job(2, "mi300", minutes=16)])
    proved = rf.annotate_build_source_families(original, rf.family_catalog_from_snapshot(snapshot()))
    options = {"pipeline_slug": "ci", "window_days": 30, "generated_at": CLOCK,
               "nightly_pattern": "Full CI run - nightly", "collection_provenance": {"exhaustive": True}}
    before = build_all_main_reliability([original], **options)
    after = build_all_main_reliability([proved], **options)
    assert before["denominator"] == after["denominator"]
    by_id = {row["group_id"]: row for row in before["groups"]}
    assert len(by_id) == len(after["groups"]) == 2
    for row in after["groups"]:
        previous = by_id[row["group_id"]]
        for key in ("name", "raw_name", "step_key", "hardware", "queue", "denominator", "passed", "failed", "duration"):
            assert row[key] == previous[key]
        assert len(row["source_family_keys"]) == 1
        assert row["observations"][0]["source_family_key"] == row["source_family_keys"][0]
        for key, value in previous["observations"][0].items():
            assert row["observations"][0][key] == value


def test_reliability_rejects_forged_family_before_using_its_group():
    from vllm.ci.reliability_history import build_all_main_reliability
    proved = rf.annotate_build_source_families(build([job(1)]), rf.family_catalog_from_snapshot(snapshot()))
    proved["jobs"][0]["source_family_key"] = "lm eval small models"
    with pytest.raises(ValueError):
        build_all_main_reliability([proved], pipeline_slug="ci", window_days=30,
                                   generated_at=CLOCK, collection_provenance={"exhaustive": True})


def test_family_source_acquisition_failure_retains_its_typed_reason_and_original_roster(monkeypatch):
    from vllm import collect_analytics as ca
    from vllm.main_ci_definitions import RuntimeSourceError
    candidate = build([job(1)])
    original = deepcopy(candidate)
    failure = RuntimeSourceError(reason_class="timeout", commit_sha=PIN, phase="definitions")
    def fail(*_args, **_kwargs):
        raise failure
    monkeypatch.setattr(rf, "family_catalog_from_scope_index", fail)
    with pytest.raises(RuntimeSourceError) as error:
        ca.annotate_current_nightly_families([candidate], generated_at=CLOCK)
    assert error.value is failure
    assert error.value.reason_class == "timeout"
    assert candidate == original


def test_family_acquisition_rederives_mixed_restored_and_fresh_cohort(monkeypatch):
    from vllm import collect_analytics as ca
    catalog = rf.family_catalog_from_snapshot(snapshot())
    restored = rf.annotate_build_source_families(build([job(1)]), catalog)
    fresh = build([job(2)], number=93774)
    original = deepcopy([restored, fresh])
    acquired = []
    def load(_index, *, expected_commit):
        acquired.append(expected_commit)
        return catalog
    monkeypatch.setattr(rf, "family_catalog_from_scope_index", load)
    proved = ca.annotate_current_nightly_families([restored, fresh], generated_at=CLOCK)
    assert acquired == [PIN, PIN]
    assert [restored, fresh] == original
    assert all(rf.validate_build_source_families(candidate) for candidate in proved)
    assert [candidate["created_at"] for candidate in proved] == [candidate["created_at"] for candidate in original]


@pytest.mark.parametrize("contradiction", ["catalog", "claim"])
def test_restored_family_contradictions_fail_inside_typed_source_boundary(monkeypatch, contradiction):
    from vllm import collect_analytics as ca
    from vllm.main_ci_definitions import RuntimeSourceError
    catalog = rf.family_catalog_from_snapshot(snapshot())
    restored = rf.annotate_build_source_families(build([job(1)]), catalog)
    if contradiction == "catalog":
        restored["source_family_catalog"]["definitions"][0]["family_key"] = "forged family"
    else:
        restored["jobs"][0]["source_family_key"] = "forged family"
    original = deepcopy(restored)
    acquired = []
    def load(_index, *, expected_commit):
        acquired.append(expected_commit)
        return catalog
    monkeypatch.setattr(rf, "family_catalog_from_scope_index", load)
    with pytest.raises(RuntimeSourceError) as error:
        ca.annotate_current_nightly_families([restored], generated_at=CLOCK)
    assert acquired == [PIN]
    assert error.value.reason_class == "schema-drift"
    assert error.value.commit_sha == PIN
    assert error.value.phase == "scope"
    assert restored == original
