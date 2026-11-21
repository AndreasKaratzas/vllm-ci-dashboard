"""Tests for the DNS-to-canonical publication wake-up decision."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from vllm import check_site_health as health
from vllm import plan_dns_publication_reconcile as reconcile


NOW = datetime(2026, 8, 29, tzinfo=timezone.utc)


def _status(**overrides):
    payload = {
        "schema_version": 1,
        "mode": "current",
        "status": "healthy",
        "generated_at": (NOW - timedelta(minutes=30)).isoformat(),
        "degraded_since": None,
        "publication_blocked": False,
        "uses_fallback": False,
        "affected_surfaces": [],
        "affected_surface_count": 0,
        "fallback_surface_count": 0,
        "fresh_degraded_surface_count": 0,
    }
    payload.update(overrides)
    return payload


def _dns(generated_at=None, **overrides):
    payload = {
        "schema_version": 1,
        "generated_at": generated_at or (NOW - timedelta(minutes=30)).isoformat(),
    }
    payload.update(overrides)
    return payload


def test_current_recent_canonical_publication_does_not_duplicate_work():
    assert reconcile.reconciliation_reason(_status(), now=NOW) is None


def test_dns_degradation_is_reconciled_even_when_status_is_recent():
    reason = reconcile.reconciliation_reason(
        _status(
            mode="degraded",
            status="degraded",
            affected_surfaces=["DNS health"],
            affected_surface_count=1,
            fresh_degraded_surface_count=1,
        ),
        now=NOW,
    )

    assert reason == "dns-health-affected"


def test_old_canonical_publication_is_reconciled_after_dns_publish():
    reason = reconcile.reconciliation_reason(
        _status(generated_at=(NOW - timedelta(hours=4)).isoformat()),
        now=NOW,
    )

    assert reason == "publication-stale"


def test_unrelated_blocked_publication_does_not_duplicate_full_collection():
    reason = reconcile.reconciliation_reason(
        _status(
            mode="blocked",
            status="blocked",
            publication_blocked=True,
            generated_at=(NOW - timedelta(hours=4)).isoformat(),
        ),
        now=NOW,
    )

    assert reason is None


def test_blocked_publication_is_reconciled_when_dns_is_affected():
    reason = reconcile.reconciliation_reason(
        _status(
            mode="blocked",
            status="blocked",
            publication_blocked=True,
            affected_surfaces=["DNS health"],
            affected_surface_count=1,
        ),
        now=NOW,
    )

    assert reason == "dns-health-affected"


def test_unrelated_recent_degradation_does_not_duplicate_full_collection():
    reason = reconcile.reconciliation_reason(
        _status(
            mode="degraded",
            status="degraded",
            affected_surfaces=["Queue health"],
            affected_surface_count=1,
            fresh_degraded_surface_count=1,
        ),
        now=NOW,
    )

    assert reason is None


def test_contract_allowlist_tracks_the_synthetic_health_checker():
    assert reconcile.PUBLICATION_SURFACE_LABELS == health.PUBLICATION_SURFACE_LABELS
    assert reconcile.PUBLICATION_MODES == health.PUBLICATION_MODES
    assert reconcile.PUBLICATION_STATUSES == health.PUBLICATION_STATUSES


def test_target_preflight_skips_an_already_acknowledged_dns_generation():
    reason = reconcile.target_reconciliation_reason(
        _status(),
        _dns(),
        target_dns_generation=(NOW - timedelta(hours=1)).isoformat(),
        now=NOW,
    )

    assert reason is None


def test_target_preflight_does_not_skip_a_stale_acknowledgement():
    reason = reconcile.target_reconciliation_reason(
        _status(generated_at=(NOW - timedelta(hours=4)).isoformat()),
        _dns(),
        target_dns_generation=(NOW - timedelta(hours=5)).isoformat(),
        now=NOW,
    )

    assert reason == "publication-stale"


def test_target_preflight_honors_the_configured_publication_age():
    reason = reconcile.target_reconciliation_reason(
        _status(generated_at=(NOW - timedelta(hours=2)).isoformat()),
        _dns(),
        target_dns_generation=(NOW - timedelta(hours=3)).isoformat(),
        now=NOW,
        max_publication_age_hours=1,
    )

    assert reason == "publication-stale"


@pytest.mark.parametrize(
    ("status", "dns", "target", "expected"),
    [
        (
            _status(generated_at=(NOW - timedelta(hours=1)).isoformat()),
            _dns(generated_at=NOW.isoformat()),
            (NOW - timedelta(minutes=30)).isoformat(),
            "publication-before-target",
        ),
        (
            _status(generated_at=NOW.isoformat()),
            _dns(generated_at=(NOW - timedelta(hours=1)).isoformat()),
            (NOW - timedelta(minutes=30)).isoformat(),
            "dns-generation-pending",
        ),
        (
            _status(
                mode="degraded",
                status="degraded",
                affected_surfaces=["DNS health"],
                affected_surface_count=1,
                fresh_degraded_surface_count=1,
            ),
            _dns(generated_at=NOW.isoformat()),
            (NOW - timedelta(minutes=30)).isoformat(),
            "dns-health-affected",
        ),
        (
            _status(generated_at=NOW.isoformat()),
            _dns(generated_at=NOW.isoformat()),
            "0001-01-01T00:00:00+23:59",
            "target-invalid",
        ),
    ],
)
def test_target_preflight_requires_unacknowledged_generation(status, dns, target, expected):
    assert (
        reconcile.target_reconciliation_reason(
            status,
            dns,
            target_dns_generation=target,
            now=NOW,
        )
        == expected
    )


def test_target_preflight_fails_safe_when_canonical_dns_is_unavailable(tmp_path):
    status = tmp_path / "status.json"
    missing_dns = tmp_path / "missing-dns.json"
    status.write_text(json.dumps(_status()))

    assert (
        reconcile.load_target_reconciliation_reason(
            status,
            missing_dns,
            target_dns_generation=(NOW - timedelta(minutes=15)).isoformat(),
            now=NOW,
        )
        == "canonical-dns-unavailable"
    )


def test_cli_can_enforce_the_target_postcondition(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    canonical_dns = tmp_path / "dns.json"
    status.write_text(json.dumps(_status(generated_at=NOW.isoformat())))
    canonical_dns.write_text(json.dumps(_dns(generated_at=(NOW - timedelta(hours=1)).isoformat())))
    monkeypatch.setattr(
        "sys.argv",
        [
            "plan_dns_publication_reconcile.py",
            "--status",
            str(status),
            "--canonical-dns-data",
            str(canonical_dns),
            "--target-dns-generation",
            (NOW - timedelta(minutes=30)).isoformat(),
            "--now",
            NOW.isoformat(),
            "--fail-if-required",
        ],
    )

    assert reconcile.main() == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": True},
        {"mode": []},
        {"status": {}},
        {"publication_blocked": True},
        {"affected_surfaces": ["Queue health"]},
        {"mode": "fallback", "status": "healthy"},
        {
            "mode": "fallback",
            "status": "degraded",
            "uses_fallback": True,
        },
        {
            "mode": "degraded",
            "status": "degraded",
            "affected_surfaces": ["Unknown", "Unknown"],
            "affected_surface_count": 2,
            "fresh_degraded_surface_count": 2,
        },
        {"affected_surface_count": True},
        {"generated_at": (NOW + timedelta(minutes=6)).isoformat()},
        {"generated_at": "0001-01-01T00:00:00+23:59"},
        {"degraded_since": "not-a-time"},
    ],
)
def test_malformed_or_contradictory_status_fails_safe_to_reconciliation(overrides):
    assert reconcile.reconciliation_reason(_status(**overrides), now=NOW) == ("status-invalid")


def test_missing_required_field_fails_safe_to_reconciliation():
    payload = _status()
    payload.pop("affected_surface_count")

    assert reconcile.reconciliation_reason(payload, now=NOW) == "status-invalid"


def test_missing_or_invalid_status_fails_safe_to_reconciliation(tmp_path):
    missing = tmp_path / "missing.json"
    invalid = tmp_path / "invalid.json"
    duplicate = tmp_path / "duplicate.json"
    invalid.write_text("not json")
    duplicate.write_text('{"schema_version": 1, "schema_version": 1}')

    assert reconcile.load_reconciliation_reason(missing, now=NOW) == ("status-unavailable")
    assert reconcile.load_reconciliation_reason(invalid, now=NOW) == ("status-unavailable")
    assert reconcile.load_reconciliation_reason(duplicate, now=NOW) == ("status-unavailable")


def test_oversized_or_deep_status_fails_safe_to_reconciliation(tmp_path):
    oversized = tmp_path / "oversized.json"
    deep = tmp_path / "deep.json"
    oversized.write_bytes(b" " * (reconcile.STATUS_MAX_BYTES + 1))
    deep.write_text("[" * 10_000 + "0" + "]" * 10_000)

    assert reconcile.load_reconciliation_reason(oversized, now=NOW) == ("status-unavailable")
    assert reconcile.load_reconciliation_reason(deep, now=NOW) == ("status-unavailable")
