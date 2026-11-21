"""Classifier changes re-attest retained DNS rows instead of aborting collection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from vllm import collect_dns_failures as collector
from vllm.ci import dns_failures as dns

CLOCK = datetime(2026, 11, 3, 3, tzinfo=timezone.utc)
JOB_ID = "01a1751d-235e-4997-8689-4e55ccc2e69b"


def metadata(queue: str, classification: str, *, commit: str = "a" * 40) -> dict:
    return {
        "pipeline": "ci", "build_number": 97168, "job_id": JOB_ID,
        "queue": queue, "node": "unidentified", "hardware": dns.queue_hardware(queue),
        "state": "passed", "started_at": "2026-10-25T10:00:00Z",
        "finished_at": "2026-10-25T10:05:00Z",
        "execution_proof": {"version": 2, "source_commit": commit,
                            "definition_tree": "b" * 40, "classification": classification},
    }


def prepare(old: dict, fresh: dict) -> list[dict]:
    return collector._prepare_records(
        [old], [fresh], retention_start=CLOCK - timedelta(hours=dns.RETENTION_HOURS),
        end_exclusive=CLOCK,
    )


def test_mi250_queue_fold_reclassification_keeps_the_fresh_scope():
    old = dns.pending_record(metadata("amd_mi250", "amd_mi_gpu"))
    fresh = metadata("amd_mi250_1", "excluded_cpu")
    [row] = prepare(old, fresh)
    assert row == dns.pending_record(fresh)


def test_reclassification_keeps_the_completed_scan_outcome():
    old = dns.scan_record(metadata("amd_mi250", "amd_mi_gpu"), dns.classify_dns_log(
        "getaddrinfo failed", job_finished_at="2026-10-25T10:05:00Z",
    ), attempted_at="2026-10-25T11:00:00Z")
    [row] = prepare(old, metadata("amd_mi250_1", "excluded_cpu"))
    assert row["status"] == "positive"
    assert row["episode_times"] == old["episode_times"]
    assert (row["queue"], row["execution_proof"]["classification"]) == ("amd_mi250_1", "excluded_cpu")
    assert dns.build_public_output({**dns.empty_state(CLOCK, CLOCK - timedelta(days=30)),
                                    "jobs": [row]})["coverage"]["eligible_jobs"] == 0


def test_unscanned_cpu_row_reclassified_as_gpu_becomes_scan_eligible():
    old = dns.pending_record(metadata("amd_mi250", "excluded_cpu"))
    [row] = prepare(old, metadata("amd_mi250_1", "amd_mi_gpu"))
    assert row["status"] == "pending"
    assert row["execution_proof"]["classification"] == "amd_mi_gpu"


def test_changed_source_commit_for_the_same_job_still_fails_closed():
    old = dns.pending_record(metadata("amd_mi250", "amd_mi_gpu"))
    with pytest.raises(dns.StateValidationError) as raised:
        prepare(old, metadata("amd_mi250_1", "amd_mi_gpu", commit="c" * 40))
    assert raised.value.diagnostic_code == "execution_proof_conflict"
    assert raised.value.differing_fields == ("source_commit",)

