from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vllm import check_site_health as health
from vllm import normalize_site_health_evidence as normalizer


ROOT = Path(__file__).resolve().parents[2]
NORMALIZER = ROOT / "scripts/vllm/normalize_site_health_evidence.py"
VERIFIED_FILES = [
    "index.html",
    health.PUBLICATION_STATUS_PATH,
    *health.CRITICAL_ASSET_PATHS,
    health.OPERATIONS_MANIFEST_PATH,
    *[
        f"data/vllm/ci/operations_v2/{name}.json"
        for name in health.OPERATIONS_CANARY_SECTIONS
    ],
    *[
        f"data/vllm/ci/operations_v2/{name}.json"
        for name in health.OPERATIONS_STREAMED_LARGE_SECTIONS
    ],
]
OPERATIONS_CANARIES = [
    {
        "name": name,
        "path": f"data/vllm/ci/operations_v2/{name}.json",
        "http_status": 200,
    }
    for name in health.OPERATIONS_CANARY_SECTIONS
]


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _healthy_report(now: datetime, *, complete_attempt: int = 1) -> dict:
    generated_at = _iso_utc(now - timedelta(hours=1))
    probes = []
    for attempt in range(1, health.CONFIRMATION_ATTEMPTS + 1):
        complete = attempt == complete_attempt
        probes.append({
            "attempt": attempt,
            "checked_at": _iso_utc(now),
            "healthy": True,
            "site_http": 200,
            "publication_http": 200,
            "generation_http": 200,
            "manifest_http": 200,
            "projection_mode": "verified" if complete else "critical-routes-verified",
            "projection_verified": complete,
            "complete_projection": complete,
            "streamed_projection_attempted": complete,
            "matches_complete_projection": True,
            "reason_codes": [],
        })
    return {
        "schema_version": 1,
        "checked_at": _iso_utc(now),
        "healthy": True,
        "overall_status": "healthy",
        "reasons": [],
        "site": {
            "url": "https://example.test/dashboard/",
            "http_status": 200,
            "bytes_read": 2048,
        },
        "publication": {
            "http_status": 200,
            "mode": "current",
            "status": "healthy",
            "generated_at": generated_at,
            "age_hours": 1.0,
            "publication_blocked": False,
            "uses_fallback": False,
        },
        "projection": {
            "mode": "verified",
            "verified": True,
            "verification_scope": "complete",
            "generation_http": 200,
            "manifest_http": 200,
            "manifest_sha256": "a" * 64,
            "file_count": len(VERIFIED_FILES),
            "total_bytes": 4096,
            "verified_files": VERIFIED_FILES,
            "operations_canaries": OPERATIONS_CANARIES,
            "operations_streamed_sections": [
                {
                    "name": name,
                    "path": f"data/vllm/ci/operations_v2/{name}.json",
                    "http_status": 200,
                    "bytes_read": 1024,
                    "sha256": "b" * 64,
                    "verified": True,
                }
                for name in health.OPERATIONS_STREAMED_LARGE_SECTIONS
            ],
        },
        "confirmation": {
            "strategy": "2-of-3-quorum",
            "confirmed": True,
            "max_attempts": health.CONFIRMATION_ATTEMPTS,
            "attempted": health.CONFIRMATION_ATTEMPTS,
            "required_healthy": health.CONFIRMATION_QUORUM,
            "healthy_count": health.CONFIRMATION_ATTEMPTS,
            "unhealthy_count": 0,
            "streamed_projection_attempt": complete_attempt,
            "complete_projection_attempt": complete_attempt,
            "complete_projection_verified": True,
            "matching_projection_healthy_count": health.CONFIRMATION_ATTEMPTS,
            "required_matching_projection_healthy": health.CONFIRMATION_QUORUM,
            "max_requests": health.MAX_CONFIRMATION_REQUESTS,
            "per_request_timeout_seconds": health.FETCH_TIMEOUT_SECONDS,
            "max_transport_seconds": (
                health.MAX_CONFIRMATION_TRANSPORT_SECONDS
            ),
            "retry_delays_seconds": list(health.CONFIRMATION_DELAYS_SECONDS),
            "max_elapsed_seconds": (
                health.MAX_CONFIRMATION_ELAPSED_SECONDS
            ),
            "probes": probes,
        },
    }


def _output_text(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "" if value is None else str(value)


def _normalizer_environment(
    tmp_path: Path,
    report: dict,
    now: datetime,
) -> dict[str, str]:
    report_path = tmp_path / "site-health-report.json"
    details_path = tmp_path / "site-health-detail.md"
    body_path = tmp_path / "site-health-issue.md"
    output_path = tmp_path / "github-output.txt"
    report_path.write_text(json.dumps(report))
    details_path.write_text("All bounded health probes passed.\n")
    outputs = health.github_outputs(report)
    output_names = {
        "CHECKER_HEALTHY": "healthy",
        "SITE_HTTP": "site_http",
        "SITE_BYTES": "site_bytes",
        "PUBLICATION_HTTP": "publication_http",
        "PUBLICATION_MODE": "publication_mode",
        "PUBLICATION_STATUS": "publication_status",
        "GENERATED_AT": "generated_at",
        "AGE_HOURS": "age_hours",
        "REASON_COUNT": "reason_count",
        "OVERALL_STATUS": "overall_status",
        "CONFIRMATION_CONFIRMED": "confirmation_confirmed",
        "PROBE_ATTEMPTS": "probe_attempts",
        "HEALTHY_PROBE_COUNT": "healthy_probe_count",
        "REQUIRED_HEALTHY_PROBES": "required_healthy_probes",
    }
    environment = {
        **os.environ,
        **{name: _output_text(outputs[key]) for name, key in output_names.items()},
        "CHECKER_OUTCOME": "success",
        "CORE_FRESHNESS_OUTCOME": "success",
        "CORE_COLLECTION_REQUIRED": "false",
        "CORE_REQUEST_MODE": "success_gated",
        "CORE_AVAILABLE_AT": _iso_utc(now + timedelta(hours=1)),
        "CORE_LATEST_SUCCEEDED_AT": _iso_utc(now - timedelta(hours=1)),
        "REPORT_PATH": str(report_path),
        "DETAILS_PATH": str(details_path),
        "BODY_PATH": str(body_path),
        "BOOTSTRAP_EVIDENCE_PATH": str(tmp_path / "bootstrap.json"),
        "SITE_URL": "https://example.test/dashboard/",
        "OWNERSHIP_MARKER": "<!-- vllm-ci-dashboard:site-health:v1 -->",
        "GITHUB_REPOSITORY": "example/dashboard",
        "GITHUB_SERVER_URL": "https://github.example.test",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_OUTPUT": str(output_path),
    }
    for name in (
        "BUILDKITE_TOKEN",
        "BUILDKITE_API_TOKEN",
        "BUILDKITE_REQUEST_GUARD_FILE",
        "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID",
        "BUILDKITE_REQUEST_GUARD_ALLOWANCE",
    ):
        environment.pop(name, None)
    return environment


def _run_normalizer(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(NORMALIZER)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _github_output(environment: dict[str, str]) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in Path(environment["GITHUB_OUTPUT"]).read_text().splitlines()
    )


def test_standalone_normalizer_accepts_healthy_report_with_current_core(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    report = _healthy_report(now)
    environment = _normalizer_environment(tmp_path, report, now)

    completed = _run_normalizer(environment)

    assert completed.returncode == 0, completed.stderr
    assert _github_output(environment) | {"summary": "ignored"} == {
        "healthy": "true",
        "confirmed": "true",
        "core_current": "true",
        "report_valid": "true",
        "missing_output_count": "0",
        "summary": "ignored",
    }
    normalized = json.loads(Path(environment["REPORT_PATH"]).read_text())
    assert normalized["healthy"] is True
    assert normalized["overall_status"] == "healthy"
    assert normalized["reasons"] == []
    assert normalized["checker_health"] == {
        "healthy": True,
        "overall_status": "healthy",
        "outcome": "success",
    }
    assert normalized["durable_core"] == {
        "observation_valid": True,
        "current": True,
        "outcome": "success",
        "collection_required": False,
        "request_mode": "success_gated",
        "available_at": environment["CORE_AVAILABLE_AT"],
        "latest_succeeded_at": environment["CORE_LATEST_SUCCEEDED_AT"],
        "max_age_hours": 3,
    }
    assert normalized["workflow_confirmation"] == {
        "confirmed": True,
        "synthetic_quorum_confirmed": True,
        "durable_core_observation_valid": True,
    }
    body = Path(environment["BODY_PATH"]).read_text()
    assert body == "\n".join(
        (
            "<!-- vllm-ci-dashboard:site-health:v1 -->",
            "<!-- SITE_HEALTH_STATE -->",
            "",
            "## Dashboard synthetic health: healthy",
            "",
            "- **Site:** [https://example.test/dashboard/](https://example.test/dashboard/)",
            "- **Workflow run:** [synthetic monitor evidence]"
            "(https://github.example.test/example/dashboard/actions/runs/12345)",
            "- **Deployment history:** [GitHub deployments]"
            "(https://github.example.test/example/dashboard/deployments)",
            "- **Checker outcome:** `success`",
            "- **Quorum confirmation:** `true` (3/3 healthy; 2 required)",
            "- **Overall status:** `healthy`",
            "- **Publication mode/status:** `current` / `healthy`",
            "- **Publication age:** `1.0` hours",
            "- **Durable core collection:** `current` (last success `"
            + environment["CORE_LATEST_SUCCEEDED_AT"]
            + "`, observation `success`, mode `success_gated`, next `"
            + environment["CORE_AVAILABLE_AT"]
            + "`)",
            "- **Missing checker outputs:** `none`",
            "",
            "<details><summary>Bounded checker details</summary><pre>"
            "All bounded health probes passed.\n</pre></details>",
            "",
            "<!-- SITE_HEALTH_RECOVERY_NOTE -->",
            "",
            "This issue is owned by the synthetic site-health workflow. Evidence is",
            "updated in place; the workflow does not post hourly comments.",
            "",
        )
    )


def test_normalizer_accepts_a_full_stream_discovered_by_a_later_probe(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    report = _healthy_report(now, complete_attempt=2)
    environment = _normalizer_environment(tmp_path, report, now)

    completed = _run_normalizer(environment)

    assert completed.returncode == 0, completed.stderr
    outputs = _github_output(environment)
    assert outputs["healthy"] == "true"
    assert outputs["confirmed"] == "true"
    assert outputs["report_valid"] == "true"
    normalized = json.loads(Path(environment["REPORT_PATH"]).read_text())
    assert normalized["confirmation"]["streamed_projection_attempt"] == 2
    assert normalized["confirmation"]["complete_projection_attempt"] == 2
    assert normalized["healthy"] is True
    assert normalized["overall_status"] == "healthy"


def test_effective_artifact_is_independent_from_the_raw_checker_report(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    report = _healthy_report(now)
    environment = _normalizer_environment(tmp_path, report, now)
    effective_path = tmp_path / "site-health-effective.json"
    effective_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "healthy": False,
                "overall_status": "normalization_not_completed",
            }
        )
    )
    environment["EFFECTIVE_REPORT_PATH"] = str(effective_path)
    raw_before = Path(environment["REPORT_PATH"]).read_bytes()

    completed = _run_normalizer(environment)

    assert completed.returncode == 0, completed.stderr
    assert Path(environment["REPORT_PATH"]).read_bytes() == raw_before
    effective = json.loads(effective_path.read_text())
    assert effective["healthy"] is True
    assert effective["overall_status"] == "healthy"
    assert effective["checker_health"]["healthy"] is True
    assert effective["durable_core"]["current"] is True
    assert not effective_path.with_name(
        f".{effective_path.name}.normalizing"
    ).exists()


def test_late_promotion_failure_keeps_effective_artifact_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    report = _healthy_report(now)
    environment = _normalizer_environment(tmp_path, report, now)
    effective_path = tmp_path / "site-health-effective.json"
    fail_closed_seed = {
        "schema_version": 1,
        "healthy": False,
        "overall_status": "normalization_not_completed",
    }
    effective_path.write_text(json.dumps(fail_closed_seed) + "\n")
    environment["EFFECTIVE_REPORT_PATH"] = str(effective_path)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    real_replace = os.replace

    def fail_effective_promotion(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == effective_path:
            raise OSError("injected final effective-report promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_effective_promotion)

    with pytest.raises(OSError, match="injected final effective-report promotion failure"):
        normalizer.normalize_health_evidence()

    # Body/output preparation has completed, so this models a genuinely late
    # failure with potentially authoritative-looking partial step outputs.
    outputs = _github_output(environment)
    assert outputs["healthy"] == "true"
    assert outputs["confirmed"] == "true"
    assert Path(environment["BODY_PATH"]).is_file()
    # The upload target nevertheless remains the independent fail-closed seed;
    # the workflow's step-outcome gate prevents the partial outputs above from
    # authorizing any issue mutation.
    assert json.loads(effective_path.read_text()) == fail_closed_seed


def test_oversized_report_is_replaced_with_bounded_fail_closed_evidence(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    report = _healthy_report(now)
    environment = _normalizer_environment(tmp_path, report, now)
    Path(environment["REPORT_PATH"]).write_bytes(b"{" + b"x" * (64 * 1024))

    completed = _run_normalizer(environment)

    assert completed.returncode == 0, completed.stderr
    outputs = _github_output(environment)
    assert outputs["healthy"] == "false"
    assert outputs["confirmed"] == "false"
    assert outputs["core_current"] == "true"
    assert outputs["report_valid"] == "false"
    normalized = json.loads(Path(environment["REPORT_PATH"]).read_text())
    assert normalized["healthy"] is False
    assert normalized["overall_status"] == "workflow_evidence_invalid"
    assert normalized["reasons"] == ["checker report exceeded 64 KiB"]
    assert Path(environment["REPORT_PATH"]).stat().st_size <= 64 * 1024


def test_failed_core_observation_cannot_confirm_or_mutate_incident_state(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    report = _healthy_report(now)
    environment = _normalizer_environment(tmp_path, report, now)
    environment["CORE_FRESHNESS_OUTCOME"] = "failure"

    completed = _run_normalizer(environment)

    assert completed.returncode == 0, completed.stderr
    outputs = _github_output(environment)
    assert outputs["healthy"] == "false"
    assert outputs["confirmed"] == "false"
    assert outputs["core_current"] == "false"
    assert outputs["report_valid"] == "true"
    normalized = json.loads(Path(environment["REPORT_PATH"]).read_text())
    assert normalized["healthy"] is False
    assert normalized["overall_status"] == "workflow_evidence_unconfirmed"
    assert normalized["workflow_confirmation"]["confirmed"] is False
    assert {row["code"] for row in normalized["reasons"]} == {
        "workflow-evidence-unconfirmed",
        "durable-core-observation-invalid",
    }
    assert "Dashboard synthetic health: unconfirmed" in Path(
        environment["BODY_PATH"]
    ).read_text()


def test_stale_durable_core_is_written_into_the_effective_uploaded_report(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    report = _healthy_report(now)
    environment = _normalizer_environment(tmp_path, report, now)
    environment["CORE_LATEST_SUCCEEDED_AT"] = _iso_utc(now - timedelta(hours=4))

    completed = _run_normalizer(environment)

    assert completed.returncode == 0, completed.stderr
    outputs = _github_output(environment)
    assert outputs["healthy"] == "false"
    assert outputs["confirmed"] == "true"
    assert outputs["core_current"] == "false"
    normalized = json.loads(Path(environment["REPORT_PATH"]).read_text())
    assert normalized["healthy"] is False
    assert normalized["overall_status"] == "durable_core_stale"
    assert normalized["checker_health"]["healthy"] is True
    assert normalized["durable_core"]["current"] is False
    assert normalized["workflow_confirmation"]["confirmed"] is True
    assert [row["code"] for row in normalized["reasons"]] == [
        "durable-core-stale"
    ]
    body = Path(environment["BODY_PATH"]).read_text()
    assert "Dashboard synthetic health: confirmed unhealthy" in body
    assert "- **Overall status:** `durable_core_stale`" in body


def test_extremely_large_publication_age_fails_closed(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    report = _healthy_report(now)
    report["publication"]["age_hours"] = 10**4000
    environment = _normalizer_environment(tmp_path, report, now)

    completed = _run_normalizer(environment)

    assert completed.returncode == 0, completed.stderr
    outputs = _github_output(environment)
    assert outputs["healthy"] == "false"
    assert outputs["confirmed"] == "false"
    assert outputs["core_current"] == "true"
    assert outputs["report_valid"] == "false"
    normalized = json.loads(Path(environment["REPORT_PATH"]).read_text())
    assert normalized["healthy"] is False
    assert normalized["overall_status"] == "workflow_evidence_invalid"
    assert "publication age_hours was not a finite number" in normalized["reasons"][0]
