"""Focused regression tests for DNS-health credential boundaries."""

from __future__ import annotations

# cspell:ignore AKIA bkua github_pat xapp xoxb xoxp

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vllm import collect_dns_failures as collector
from vllm.audit_dashboard_data import DashboardAudit
from vllm.ci import dns_failures as dns


def _sensitive_samples() -> tuple[tuple[str, str], ...]:
    """Return inert, synthetic credential shapes without source-level secrets."""
    return (
        ("slack-bot", "xox" + "b-" + "1" * 32),
        ("slack-user", "xox" + "p-" + "2" * 32),
        ("slack-app", "x" + "app-1-" + "3" * 28),
        ("github-classic", "gh" + "p_" + "4" * 32),
        ("github-fine-grained", "github" + "_pat_" + "5" * 32),
        ("buildkite", "bk" + "ua_" + "6" * 32),
        ("aws-access-key", "AK" + "IA" + "7" * 16),
        ("aws-session-key", "AS" + "IA" + "8" * 16),
        ("bearer", "Bearer " + "a" * 24),
        ("basic", "Basic " + "b" * 24),
        ("authorization", "Authorization: Token " + "d" * 24),
        ("private-key", "-----BEGIN " + "RSA PRIVATE KEY-----"),
        ("assigned-secret", "client_secret=" + "c" * 24),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _metadata(now: datetime, *, node: str = "node-1") -> dict:
    return {
        "pipeline": "amd-ci",
        "build_number": 123,
        "job_id": "00000000-0000-4000-8000-000000000001",
        "queue": "amd_mi300_1",
        "node": node,
        "hardware": "MI300",
        "state": "passed",
        "started_at": dns.iso_timestamp(now - timedelta(minutes=20)),
        "finished_at": dns.iso_timestamp(now - timedelta(minutes=10)),
    }


def _positive_public_payload(now: datetime) -> dict:
    metadata = _metadata(now)
    classification = dns.DnsClassification(
        match_count=1,
        episode_times=(dns.iso_timestamp(now - timedelta(minutes=15)),),
        signature_ids=("temporary_name_resolution",),
        target_categories=("huggingface_hub",),
        time_basis="log_timestamp",
    )
    state = dns.empty_state(now, now - timedelta(hours=dns.RETENTION_HOURS))
    state["jobs"] = [
        dns.scan_record(
            metadata,
            classification,
            attempted_at=dns.iso_timestamp(now),
        )
    ]
    return dns.build_public_output(state)


@pytest.mark.parametrize(
    ("sample_id", "source_label"),
    _sensitive_samples(),
    ids=[sample[0] for sample in _sensitive_samples()],
)
def test_source_job_labels_never_enter_dns_state_or_public_output(
    sample_id: str,
    source_label: str,
):
    del sample_id
    now = _now()
    metadata = _metadata(now)
    job = {
        "id": metadata["job_id"],
        "type": "script",
        "state": "passed",
        "name": f"nightly tests {source_label}",
        "started_at": metadata["started_at"],
        "finished_at": metadata["finished_at"],
        "agent_query_rules": [f"queue={metadata['queue']}"],
        "agent": {
            "meta_data": [
                f"queue={metadata['queue']}",
                f"k8s:node={metadata['node']}",
            ]
        },
    }
    [discovered] = collector.discover_job_metadata(
        {"amd-ci": [{"number": metadata["build_number"], "jobs": [job]}], "ci": []}
    )
    assert "job_name" not in discovered

    state = dns.empty_state(now, now - timedelta(hours=dns.RETENTION_HOURS))
    state["jobs"] = [
        dns.scan_record(
            discovered,
            dns.DnsClassification(
                1,
                (dns.iso_timestamp(now - timedelta(minutes=15)),),
                ("temporary_name_resolution",),
                ("unknown",),
                "log_timestamp",
            ),
            attempted_at=dns.iso_timestamp(now),
        )
    ]
    state = dns.validate_state(state)
    public = dns.build_public_output(state)

    assert "job_name" not in state["jobs"][0]
    assert "job_name" not in public["evidence"]["items"][0]
    assert source_label not in json.dumps(state)
    assert source_label not in json.dumps(public)


@pytest.mark.parametrize(
    "node",
    [
        "xox" + "b-" + "1" * 32,
        "xox" + "p-" + "2" * 32,
        "x" + "app-1-" + "3" * 28,
        "gh" + "p_" + "4" * 32,
        "github" + "_pat_" + "5" * 32,
        "bk" + "ua_" + "6" * 32,
        "AK" + "IA" + "7" * 16,
        "AS" + "IA" + "8" * 16,
    ],
)
def test_dns_state_rejects_credential_shaped_display_coordinates(node: str):
    now = _now()
    state = dns.empty_state(now, now - timedelta(hours=dns.RETENTION_HOURS))
    state["jobs"] = [dns.pending_record(_metadata(now, node=node))]

    with pytest.raises(dns.StateValidationError, match="not a safe token"):
        dns.validate_state(state)


@pytest.mark.parametrize(
    ("sample_id", "leaked_value"),
    _sensitive_samples(),
    ids=[sample[0] for sample in _sensitive_samples()],
)
def test_dns_audit_rejects_credential_families_in_legacy_fields(
    tmp_path: Path,
    sample_id: str,
    leaked_value: str,
):
    del sample_id
    payload = _positive_public_payload(_now())
    payload["evidence"]["items"][0]["legacy_job_name"] = leaked_value
    path = tmp_path / "data/vllm/ci/dns_failures.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload))
    audit = DashboardAudit(tmp_path)

    audit.audit_dns_failures()

    assert "dns-health-sensitive-content" in {
        finding.code for finding in audit.report.errors
    }
