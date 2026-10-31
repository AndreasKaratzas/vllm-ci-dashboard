"""Missing fork commits cannot block verified DNS observations or grant scope."""

# cspell:ignore gaierror

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from vllm import collect_dns_failures as collector
from vllm import main_ci_definitions as source
from vllm.ci import dns_failures as dns

MISSING = "04ab5f1abd404f867c7d5718e6b4e76b59eb8850"  # Upstream Git commit SHA from the production failure.
KNOWN = "b" * 40
TREE = "c" * 40
NOW = datetime(2026, 10, 12, 6, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def offline_source(monkeypatch):
    annotate = source.annotate_runtime_source_scope
    monkeypatch.setattr(collector, "_SOURCE_SCOPE_INDEXES", {})
    monkeypatch.setattr(collector, "_SOURCE_UNAVAILABLE_PINS", set())
    monkeypatch.setattr(collector, "_SOURCE_SCOPE_CACHE_DIR", None)
    monkeypatch.setattr(source, "prewarm_runtime_snapshots", lambda pins: {})
    monkeypatch.setattr(source, "annotate_runtime_source_scope", lambda build, **kwargs: annotate(
        build, scope_index=kwargs.get("scope_index") or {
            "version": 2, "commit_sha": build["commit"], "definition_tree_sha": TREE,
            "cpu_routes": [],
        },
    ))
    monkeypatch.setattr("requests.sessions.Session.request",
                        lambda *args, **kwargs: pytest.fail("offline recovery tests cannot use providers"))


def job(index=1):
    return {"id": str(UUID(f"00000000-0000-4000-8000-{index:012x}")), "type": "script", "name": "GPU workload",
            "state": "passed", "started_at": dns.iso_timestamp(NOW - timedelta(hours=1)),
            "finished_at": dns.iso_timestamp(NOW - timedelta(minutes=30)),
            "agent_query_rules": ["queue=amd_mi300_1"],
            "agent": {"meta_data": ["queue=amd_mi300_1", "k8s:node=node-1"]}}


def build(pin=MISSING, index=1):
    return {"commit": pin, "number": index, "jobs": [job(index)]}


def missing(pin=MISSING, **kwargs):
    fields = {"reason_class": "dependency-unavailable", "phase": "commit",
              "http_status": 404, "commit_sha": pin, **kwargs}
    return source.RuntimeSourceError("private response must never reach output", **fields)


def state(rows):
    value = dns.empty_state(NOW, NOW - timedelta(days=30))
    value["scoped_discovery"] = {"version": 1, "hardware_scope": "amd_mi_gpu",
                                 "pipelines": ["ci"], "start": dns.iso_timestamp(NOW - timedelta(days=30)),
                                 "end_exclusive": dns.iso_timestamp(NOW), "complete": True}
    value["jobs"] = dns.sort_state_jobs(rows)
    return value


def metadata(pin=MISSING, index=1):
    return collector.job_metadata("ci", build(pin, index), job(index))


def test_batch_removes_only_missing_requested_pin_and_checkpoints_verified_siblings(monkeypatch, tmp_path):
    from vllm.ci.analytics_cache import read_runtime_source_indexes

    calls = []

    def prime(pins):
        calls.append(list(pins))
        if MISSING in pins:
            raise missing()
        return {}

    monkeypatch.setattr(source, "prewarm_runtime_snapshots", prime)
    monkeypatch.setattr(collector, "_SOURCE_SCOPE_CACHE_DIR", tmp_path / "runtime-source-indexes-v1")
    rows = collector.discover_job_metadata({"ci": [build(MISSING, 1), build(KNOWN, 2)]})
    assert calls == [sorted([MISSING, KNOWN]), [KNOWN]]
    assert len(rows) == 2
    unknown = next(row for row in rows if row["build_number"] == 1)
    assert unknown["source_unavailable_commit"] == MISSING
    assert "execution_proof" not in unknown
    known = next(row for row in rows if row["build_number"] == 2)
    assert known["execution_proof"]["source_commit"] == KNOWN
    assert set(read_runtime_source_indexes(collector._SOURCE_SCOPE_CACHE_DIR)) == {KNOWN}
    collector.discover_job_metadata({"ci": [build(MISSING, 1), build(KNOWN, 2)]})
    assert len(calls) == 2


@pytest.mark.parametrize("change", [
    {"reason_class": "schema-drift"}, {"reason_class": "timeout"},
    {"reason_class": "network"}, {"reason_class": "rate-limit"},
    {"phase": "tree"}, {"phase": "blob"}, {"phase": "metadata"}, {"phase": "batch"},
    {"phase": "scope"}, {"phase": "definitions"},
    {"http_status": 401}, {"http_status": 403}, {"http_status": 410},
    {"http_status": 429}, {"http_status": 500}, {"http_status": None},
    {"commit_sha": KNOWN}, {"commit_sha": None}, {"commit_sha": "short"},
])
def test_other_source_failures_stay_hard_and_never_enter_the_missing_pin_memo(monkeypatch, change):
    failure = missing(**change)
    monkeypatch.setattr(source, "prewarm_runtime_snapshots",
                        lambda pins: (_ for _ in ()).throw(failure))
    with pytest.raises(source.RuntimeSourceError) as raised:
        collector.discover_job_metadata({"ci": [build()]})
    assert raised.value is failure
    assert collector._SOURCE_UNAVAILABLE_PINS == set()
    assert collector._SOURCE_SCOPE_INDEXES == {}


def test_concurrent_direct_metadata_uses_one_missing_lookup_per_attempt(monkeypatch):
    calls = []

    def unavailable(value, **kwargs):
        calls.append(value["commit"])
        raise missing()

    monkeypatch.setattr(source, "annotate_runtime_source_scope", unavailable)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda index: metadata(index=index), range(1, 9)))
    assert calls == [MISSING]
    assert all(row["source_unavailable_commit"] == MISSING for row in rows)
    assert all("execution_proof" not in row for row in rows)


@pytest.mark.parametrize("status", ["pending", "negative", "positive"])
def test_missing_fresh_source_preserves_private_history_but_revokes_all_current_authority(status):
    old_metadata = metadata()
    old = dns.pending_record(old_metadata)
    if status != "pending":
        text = "socket.gaierror: Temporary failure in name resolution contacting huggingface.co" if status == "positive" else "all passed"
        classification = dns.classify_dns_log(text, job_finished_at=old_metadata["finished_at"],
                                              job_started_at=old_metadata["started_at"])
        old = dns.scan_record(old_metadata, classification, attempted_at=dns.iso_timestamp(NOW))
    fresh = {key: value for key, value in old_metadata.items() if key != "execution_proof"}
    fresh["source_unavailable_commit"] = MISSING
    before = copy.deepcopy(old)
    marked = collector._refresh_record(old, fresh)
    assert old == before
    assert marked["execution_proof"] == old["execution_proof"]
    assert marked["status"] == old["status"]
    assert dns.state_from_bytes(dns.state_bytes(state([marked])))["jobs"] == [marked]

    class NoReads:
        def classification_for(self, row):
            pytest.fail("unknown source cannot read the classification cache")

        def fetch_job_log(self, *args, **kwargs):
            pytest.fail("unknown source cannot read a log")

    assert collector.scan_records([marked], client=NoReads(), classification_cache=NoReads(),
                                  attempted_at=dns.iso_timestamp(NOW), max_logs=1) == [marked]
    public = dns.build_public_output(state([marked]))
    assert public["coverage"]["eligible_jobs"] == 0
    assert public["coverage"]["discovery_complete"] is False
    assert "source_unavailable_commit" not in str(public)
    assert MISSING not in str(public)
    for pair in ((old, marked), (marked, old)):
        merged = dns.merge_state_jobs([pair[0]], [pair[1]])[0]
        assert merged["source_unavailable_commit"] == MISSING
    recovered = collector._refresh_record(marked, old_metadata)
    assert recovered == old


@pytest.mark.parametrize("marker", [None, True, [], {}, "short", "D" * 40, KNOWN])
def test_malformed_or_conflicting_private_source_marker_is_rejected(marker):
    record = dns.pending_record(metadata())
    record["source_unavailable_commit"] = marker
    with pytest.raises(dns.StateValidationError):
        dns.validate_state(state([record]))


def test_missing_pin_memo_is_bounded_without_eviction_or_new_authority(monkeypatch):
    monkeypatch.setattr(collector, "RUNTIME_SOURCE_MAX_PINS", 1)
    assert collector._remember_unavailable_source(missing(), requested_pins=[MISSING])
    with pytest.raises(source.RuntimeSourceError) as raised:
        collector._remember_unavailable_source(missing(KNOWN), requested_pins=[KNOWN])
    assert raised.value.reason_class == "payload-budget"
    assert collector._SOURCE_UNAVAILABLE_PINS == {MISSING}


@pytest.mark.parametrize("field,value", [
    ("pipeline", "amd-ci"), ("build_number", 2),
    ("job_id", "00000000-0000-4000-8000-000000000002"),
    ("queue", "amd_mi300_2"), ("hardware", "MI355"),
    ("finished_at", "2026-10-12T05:31:00Z"),
])
def test_source_recovery_cannot_clear_quarantine_for_changed_job_metadata(field, value):
    current = metadata()
    marked = dns.pending_record({**current, "source_unavailable_commit": MISSING})
    changed = {**current, field: value}
    with pytest.raises(dns.StateValidationError) as raised:
        collector._refresh_record(marked, changed)
    assert raised.value.diagnostic_code == "execution_proof_job_mismatch"


def test_mixed_verified_and_missing_observations_publish_fresh_honest_partial_and_retry_next_attempt(monkeypatch, tmp_path):
    calls = []
    unavailable = True

    def prime(pins):
        calls.append(list(pins))
        if unavailable and MISSING in pins:
            raise missing()
        return {}

    monkeypatch.setattr(source, "prewarm_runtime_snapshots", prime)

    class Client:
        def discover_builds(self, pipeline, **kwargs):
            return [build(MISSING, 1), build(KNOWN, 2)]

        def fetch_job_log(self, row, **kwargs):
            assert row["execution_proof"]["source_commit"] == KNOWN or not unavailable
            assert "source_unavailable_commit" not in row
            return "all passed", 10

    path, public_path = tmp_path / "private.json.gz", tmp_path / "dns_failures.json"
    result = collector.collect(client=Client(), state_path=path, output_path=public_path,
                               now=NOW, minimum_interval_hours=0, max_logs=10)
    assert result["generated_at"] == dns.iso_timestamp(NOW)
    assert result["coverage"]["eligible_jobs"] == 1
    assert result["coverage"]["discovery_complete"] is False
    assert result["coverage"]["status"] == "partial"
    from vllm import audit_dashboard_data as audit_module

    class AuditClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(audit_module, "datetime", AuditClock)
    audit = audit_module.DashboardAudit(tmp_path, allow_publication_fallback=False)
    audit.audit_dns_failures(public_path)
    assert audit.report.errors == []
    assert audit.report.degradations == []
    private = dns.load_state(path)
    assert len(private["jobs"]) == 2
    assert any(row.get("source_unavailable_commit") == MISSING for row in private["jobs"])
    assert collector._SOURCE_UNAVAILABLE_PINS == {MISSING}

    unavailable = False
    recovered = collector.collect(client=Client(), state_path=path, output_path=public_path,
                                  now=NOW + timedelta(hours=4), minimum_interval_hours=0, max_logs=10)
    assert calls[-1] == [MISSING]
    assert recovered["coverage"]["eligible_jobs"] == 2
    assert collector._SOURCE_UNAVAILABLE_PINS == set()
    assert all("source_unavailable_commit" not in row for row in dns.load_state(path)["jobs"])


@pytest.mark.parametrize("known_finished", [None, "excluded_cpu", NOW, NOW + timedelta(seconds=1), NOW - timedelta(days=31)])
def test_unknown_only_observations_do_not_advance_existing_public_or_private_clocks(monkeypatch, tmp_path, known_finished):
    def prime(pins):
        if MISSING in pins:
            raise missing()
        return {}

    monkeypatch.setattr(source, "prewarm_runtime_snapshots", prime)
    if known_finished == "excluded_cpu":
        fixture_annotate = source.annotate_runtime_source_scope

        def source_scope(value, **kwargs):
            return fixture_annotate(value, scope_index=kwargs.get("scope_index") or {
                "version": 2, "commit_sha": value["commit"], "definition_tree_sha": TREE,
                "cpu_routes": [{"key": "", "label": "gpu workload", "agent_pool": "mi300_1"}],
            })

        monkeypatch.setattr(source, "annotate_runtime_source_scope", source_scope)

    class Client:
        def discover_builds(self, pipeline, **kwargs):
            result = [build()]
            if known_finished is not None:
                known = build(KNOWN, 2)
                finished = NOW - timedelta(minutes=30) if known_finished == "excluded_cpu" else known_finished
                known["jobs"][0]["finished_at"] = dns.iso_timestamp(finished)
                known["jobs"][0]["started_at"] = dns.iso_timestamp(finished - timedelta(hours=1))
                result.append(known)
            return result

        def fetch_job_log(self, *args, **kwargs):
            pytest.fail("unknown-only observations cannot spend log requests")

    path, public_path = tmp_path / "private.json.gz", tmp_path / "dns_failures.json"
    prior = dns.empty_state(NOW - timedelta(hours=4), NOW - timedelta(days=1))
    dns.write_state(path, prior)
    public_path.write_text("established public evidence\n")
    before = path.read_bytes()
    with pytest.raises(collector.CollectionError) as raised:
        collector.collect(client=Client(), state_path=path, output_path=public_path,
                          now=NOW, minimum_interval_hours=0)
    assert raised.value.reason == "source_unavailable"
    assert path.read_bytes() == before
    assert public_path.read_text() == "established public evidence\n"


def test_fully_verified_cpu_only_query_still_proves_fresh_zero_mi_absence(monkeypatch, tmp_path):
    fixture_annotate = source.annotate_runtime_source_scope
    monkeypatch.setattr(source, "annotate_runtime_source_scope", lambda value, **kwargs: fixture_annotate(
        value, scope_index=kwargs.get("scope_index") or {
            "version": 2, "commit_sha": value["commit"], "definition_tree_sha": TREE,
            "cpu_routes": [{"key": "", "label": "gpu workload", "agent_pool": "mi300_1"}],
        },
    ))

    class Client:
        def discover_builds(self, pipeline, **kwargs):
            return [build(KNOWN, 2)]

        def fetch_job_log(self, *args, **kwargs):
            pytest.fail("CPU-only scope cannot read job logs")

    result = collector.collect(client=Client(), state_path=tmp_path / "private.json.gz",
                               output_path=tmp_path / "public.json", now=NOW, minimum_interval_hours=0)
    assert result["generated_at"] == dns.iso_timestamp(NOW)
    assert result["windows"]["24h"]["coverage"]["eligible_jobs"] == 0
    assert result["windows"]["24h"]["coverage"]["discovery_complete"] is True
