import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest
import requests

from vllm import check_site_health as health
from vllm import validate_watchdog_health_report as validator


@pytest.fixture(autouse=True)
def reject_network(monkeypatch):
    def reject(*_args, **_kwargs):
        pytest.fail("Health recovery validation must not make provider requests")

    monkeypatch.setattr(requests.sessions.Session, "request", reject)


def _reason(code):
    return {"code": code, "message": f"Bounded {code} evidence."}


def _report(
    codes=(("publication-stale",),) * 3,
    *,
    matches=(True, True, True),
    complete=True,
):
    probes = [
        {
            "healthy": not reasons,
            "complete_projection": complete and index == 0,
            "matches_complete_projection": matches[index],
            "reason_codes": list(reasons),
        }
        for index, reasons in enumerate(codes)
    ]
    healthy_count = sum(probe["healthy"] for probe in probes)
    matching_count = sum(
        probe["healthy"] and probe["matches_complete_projection"] for probe in probes
    )
    reason_codes = list(dict.fromkeys(code for reasons in codes for code in reasons))
    if healthy_count < 2:
        reason_codes.append("confirmation-quorum")
    if not complete:
        reason_codes.append("complete-projection-required")
    elif matching_count < 2:
        reason_codes.append("projection-generation-quorum")
    return {
        "healthy": False,
        "overall_status": "confirmed_unhealthy",
        "reasons": [_reason(code) for code in reason_codes],
        "confirmation": {
            "confirmed": True,
            "strategy": "2-of-3-quorum",
            "max_attempts": 3,
            "attempted": 3,
            "required_healthy": 2,
            "healthy_count": healthy_count,
            "unhealthy_count": 3 - healthy_count,
            "complete_projection_verified": complete,
            "complete_projection_attempt": 1 if complete else None,
            "matching_projection_healthy_count": matching_count,
            "required_matching_projection_healthy": 2,
            "probes": probes,
        },
    }


def test_routes_only_identity_consistent_pure_staleness_to_collector():
    assert validator.report_recovery_target(_report()) == "collector"
    partly_healthy = _report(codes=((), ("publication-stale",), ("publication-stale",)))
    assert validator.report_recovery_target(partly_healthy) == "collector"
    assert validator.report_recovery_target(_report(matches=(True, False, True))) == "deploy-pages"
    mixed = _report(codes=(("publication-stale",), ("publication-stale",), ("site-http",)))
    assert validator.report_recovery_target(mixed) == "deploy-pages"
    extra = _report(
        codes=(("publication-stale", "site-http"), ("publication-stale",), ("publication-stale",))
    )
    assert validator.report_recovery_target(extra) == "deploy-pages"
    assert validator.report_confirms_recovery(extra) is True


def test_accepts_each_mandatory_failure_mode():
    healthy = _report(codes=((), (), ("site-http",)))
    assert validator.report_recovery_target(healthy) is None

    split = _report(codes=((), (), ("site-http",)), matches=(True, False, False))
    assert validator.report_recovery_target(split) == "deploy-pages"

    no_full_projection = _report(
        codes=((), (), ("generation-http",)),
        complete=False,
        matches=(False, False, False),
    )
    assert validator.report_recovery_target(no_full_projection) == "deploy-pages"

    no_health_quorum = _report(codes=((), ("site-http",), ("site-http",)))
    assert validator.report_recovery_target(no_health_quorum) == "deploy-pages"


def _set(report, path, value):
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("reasons",), None),
        (("reasons",), "publication-stale"),
        (("reasons",), []),
        (("reasons",), [_reason("publication-stale")] * 43),
        (("reasons", 0), "publication-stale"),
        (("reasons", 0), {"code": "publication-stale"}),
        (("reasons", 0), {**_reason("publication-stale"), "extra": "untrusted"}),
        (("reasons", 0, "code"), True),
        (("reasons", 0, "code"), ["publication-stale"]),
        (("reasons", 0, "code"), "publication_stale"),
        (("reasons", 0, "code"), "publication-stale\n"),
        (("reasons", 0, "code"), "x" * 81),
        (("reasons", 0, "message"), None),
        (("reasons", 0, "message"), False),
        (("reasons", 0, "message"), "  "),
        (("reasons", 0, "message"), "untrusted\nprovider text"),
        (("reasons", 0, "message"), "x" * 2049),
        (("confirmation", "probes", 0, "reason_codes"), None),
        (("confirmation", "probes", 0, "reason_codes"), "publication-stale"),
        (("confirmation", "probes", 0, "reason_codes"), {"code": "publication-stale"}),
        (("confirmation", "probes", 0, "reason_codes"), []),
        (("confirmation", "probes", 0, "reason_codes"), ["publication-stale"] * 21),
        (("confirmation", "probes", 0, "reason_codes"), [False]),
        (("confirmation", "probes", 0, "reason_codes"), ["confirmation-quorum"]),
        (("confirmation", "healthy_count"), True),
        (("confirmation", "healthy_count"), 1),
        (("confirmation", "unhealthy_count"), 2),
        (("confirmation", "matching_projection_healthy_count"), 1),
        (("confirmation", "complete_projection_attempt"), True),
        (("confirmation", "complete_projection_attempt"), 2),
        (("confirmation", "complete_projection_verified"), False),
        (("confirmation", "probes", 0, "matches_complete_projection"), False),
        (("confirmation", "probes", 1, "complete_projection"), True),
        (("confirmation", "probes", 1, "healthy"), "false"),
        (("confirmation", "probes"), []),
        (("healthy",), True),
        (("overall_status",), "unhealthy"),
    ],
)
def test_rejects_malformed_or_inconsistent_report(path, value):
    report = _report()
    _set(report, path, value)
    assert validator.report_recovery_target(report) is None
    assert validator.report_confirms_recovery(report) is False


@pytest.mark.parametrize(
    "field",
    ["max_attempts", "attempted", "required_healthy", "required_matching_projection_healthy"],
)
@pytest.mark.parametrize("replacement", [True, False, 2.0, 3.0, "2"])
def test_fixed_confirmation_controls_require_exact_integers(field, replacement):
    report = _report()
    report["confirmation"][field] = replacement
    assert validator.report_recovery_target(report) is None


@pytest.mark.parametrize("missing", ["reasons", "reason_codes"])
def test_rejects_missing_reason_contract(missing):
    report = _report()
    if missing == "reasons":
        del report["reasons"]
    else:
        del report["confirmation"]["probes"][0]["reason_codes"]
    assert validator.report_recovery_target(report) is None


def test_rejects_reason_summary_and_probe_health_contradictions():
    report = _report()
    report["reasons"] = [_reason("publication-stale")]
    assert validator.report_recovery_target(report) is None
    report = _report()
    report["reasons"].append(_reason("complete-projection-required"))
    assert validator.report_recovery_target(report) is None
    report = _report()
    report["reasons"].append(_reason("site-http"))
    assert validator.report_recovery_target(report) is None
    report = _report(codes=((), ("publication-stale",), ("publication-stale",)))
    report["confirmation"]["probes"][0]["reason_codes"] = ["site-http"]
    assert validator.report_recovery_target(report) is None


def _confirm_mock_probes(monkeypatch, specs):
    calls = []
    now = datetime(2026, 10, 10, 5, 26, tzinfo=timezone.utc)

    def reject_fetch(*_args, **_kwargs):
        pytest.fail("Confirmation integration must not perform a network request")

    def probe(_site_url, **kwargs):
        codes, generation = specs[len(calls)]
        calls.append(kwargs["verify_streamed_sections"])
        full = kwargs["verify_streamed_sections"]
        projection = {"mode": "failed", "verified": False}
        if generation is not None:
            projection.update(
                mode="verified",
                verified=True,
                verification_scope="complete" if full else "identity",
                generation_id=f"generation-{generation}",
                state_sha="1" * 40,
                state_tree="2" * 40,
                code_sha="3" * 40,
                manifest_sha256="4" * 64,
            )
        if full:
            # Mark the real producer's bounded full request as attempted.
            projection["operations_streamed_sections"] = [
                {"http_status": 200 if generation else 503}
            ]
        return {
            "healthy": not codes,
            "checked_at": now.isoformat(),
            "site": {"http_status": 200},
            "publication": {"http_status": 200},
            "projection": projection,
            "reasons": [_reason(code) for code in codes],
        }

    monkeypatch.setattr(health, "check_site_health", probe)
    report = health.confirm_site_health(
        "https://example.test/dashboard/",
        clock=lambda: now,
        sleep=lambda _seconds: None,
        fetch=reject_fetch,
        stream_fetch=reject_fetch,
    )
    assert calls == [False, True, False]
    return report


def test_real_confirmation_producer_routes_staleness_to_collector(monkeypatch):
    report = _confirm_mock_probes(monkeypatch, [(("publication-stale",), "a")] * 3)
    assert {reason["code"] for reason in report["reasons"]} == {
        "publication-stale",
        "confirmation-quorum",
        "projection-generation-quorum",
    }
    assert report["confirmation"]["complete_projection_attempt"] == 2
    assert validator.report_recovery_target(report) == "collector"


def test_real_confirmation_producer_routes_projection_outage_to_pages(monkeypatch):
    report = _confirm_mock_probes(monkeypatch, [(("generation-http",), None)] * 3)
    assert report["confirmation"]["complete_projection_verified"] is False
    assert validator.report_recovery_target(report) == "deploy-pages"


def test_real_confirmation_producer_routes_mixed_stale_and_outage_to_pages(monkeypatch):
    report = _confirm_mock_probes(
        monkeypatch,
        [
            (("publication-stale",), "a"),
            (("publication-stale",), "a"),
            (("generation-http",), None),
        ],
    )
    assert report["confirmation"]["complete_projection_verified"] is True
    assert validator.report_recovery_target(report) == "deploy-pages"


def test_real_confirmation_producer_preserves_truncated_outage_reason_codes(monkeypatch):
    codes = tuple(f"asset-failure-{index}" for index in range(21))
    report = _confirm_mock_probes(monkeypatch, [(codes, "a")] * 3)
    assert len(report["confirmation"]["probes"][0]["reason_codes"]) == 20
    assert "asset-failure-20" in {reason["code"] for reason in report["reasons"]}
    assert validator.report_recovery_target(report) == "deploy-pages"


def test_real_confirmation_producer_accepts_summary_only_generation_disagreement(monkeypatch):
    report = _confirm_mock_probes(monkeypatch, [((), "a"), ((), "b"), ((), "c")])
    assert report["confirmation"]["healthy_count"] == 3
    assert report["confirmation"]["matching_projection_healthy_count"] == 1
    assert [probe["reason_codes"] for probe in report["confirmation"]["probes"]] == [[], [], []]
    assert [reason["code"] for reason in report["reasons"]] == ["projection-generation-quorum"]
    assert validator.report_recovery_target(report) == "deploy-pages"


def _cli(path, *, print_target=False):
    command = [sys.executable, str(validator.__file__), "--input", str(path)]
    if print_target:
        command.append("--print-recovery-target")
    environment = os.environ.copy()
    for name in ("BUILDKITE_TOKEN", "BUILDKITE_API_TOKEN"):
        environment.pop(name, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(command, check=False, capture_output=True, text=True, env=environment)


@pytest.mark.parametrize("target", ["collector", "deploy-pages"])
def test_cli_is_silent_by_default_and_prints_only_bounded_target(tmp_path, target):
    report = _report()
    if target == "deploy-pages":
        report = _report(codes=(("site-http",),) * 3)
    path = tmp_path / "health.json"
    path.write_text(json.dumps(report))
    assert validator.path_recovery_target(path) == target
    assert validator.path_confirms_recovery(path) is True
    result = _cli(path)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    result = _cli(path, print_target=True)
    assert (result.returncode, result.stdout, result.stderr) == (0, target + "\n", "")


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{" + b" " * validator.REPORT_MAX_BYTES,
        b'{"healthy":false,"healthy":true}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b"\xff",
        b"[" * 1100 + b"]" * 1100,
        b"[]",
    ],
)
def test_cli_and_path_reject_unbounded_or_non_strict_json_silently(tmp_path, raw):
    path = tmp_path / "health.json"
    path.write_bytes(raw)
    assert validator.path_recovery_target(path) is None
    assert validator.path_confirms_recovery(path) is False
    result = _cli(path, print_target=True)
    assert (result.returncode, result.stdout, result.stderr) == (2, "", "")


def test_invalid_report_cli_never_prints_reason_message(tmp_path):
    report = _report()
    report["reasons"][0]["message"] = {"untrusted": "provider text"}
    path = tmp_path / "health.json"
    path.write_text(json.dumps(report))
    result = _cli(path, print_target=True)
    assert (result.returncode, result.stdout, result.stderr) == (2, "", "")
    assert validator.path_recovery_target(tmp_path / "missing.json") is None


@pytest.mark.parametrize("report", [None, [], "untrusted", 1])
def test_object_api_rejects_non_report_shapes(report):
    assert validator.report_recovery_target(report) is None
