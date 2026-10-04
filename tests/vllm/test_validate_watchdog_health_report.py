import json
import subprocess
import sys

from vllm import validate_watchdog_health_report as validator


def _report():
    probes = [
        {
            "healthy": True,
            "complete_projection": True,
            "matches_complete_projection": True,
        },
        {
            "healthy": True,
            "complete_projection": False,
            "matches_complete_projection": True,
        },
        {
            "healthy": False,
            "complete_projection": False,
            "matches_complete_projection": True,
        },
    ]
    return {
        "healthy": False,
        "overall_status": "confirmed_unhealthy",
        "confirmation": {
            "confirmed": True,
            "strategy": "2-of-3-quorum",
            "max_attempts": 3,
            "attempted": 3,
            "required_healthy": 2,
            "healthy_count": 2,
            "unhealthy_count": 1,
            "complete_projection_verified": True,
            "complete_projection_attempt": 1,
            "matching_projection_healthy_count": 2,
            "required_matching_projection_healthy": 2,
            "probes": probes,
        },
    }


def test_accepts_each_mandatory_failure_mode():
    report = _report()
    confirmation = report["confirmation"]
    assert validator.report_confirms_recovery(report) is False

    confirmation["probes"][1]["matches_complete_projection"] = False
    confirmation["matching_projection_healthy_count"] = 1
    assert validator.report_confirms_recovery(report) is True

    confirmation["probes"][0]["complete_projection"] = False
    for probe in confirmation["probes"]:
        probe["matches_complete_projection"] = False
    confirmation["complete_projection_verified"] = False
    confirmation["complete_projection_attempt"] = None
    confirmation["matching_projection_healthy_count"] = 0
    assert validator.report_confirms_recovery(report) is True

    confirmation["probes"][1]["healthy"] = False
    confirmation["healthy_count"] = 1
    confirmation["unhealthy_count"] = 2
    assert validator.report_confirms_recovery(report) is True


def test_rejects_malformed_or_inconsistent_confirmation():
    report = _report()
    confirmation = report["confirmation"]
    confirmation["healthy_count"] = True
    assert validator.report_confirms_recovery(report) is False

    confirmation["healthy_count"] = 1
    assert validator.report_confirms_recovery(report) is False

    confirmation["healthy_count"] = 2
    confirmation["complete_projection_verified"] = False
    confirmation["complete_projection_attempt"] = None
    assert validator.report_confirms_recovery(report) is False


def test_cli_is_bounded_strict_and_silent(tmp_path):
    report = _report()
    confirmation = report["confirmation"]
    confirmation["probes"][0]["complete_projection"] = False
    confirmation["probes"][0]["matches_complete_projection"] = False
    confirmation["probes"][1]["matches_complete_projection"] = False
    confirmation["probes"][2]["matches_complete_projection"] = False
    confirmation["complete_projection_verified"] = False
    confirmation["complete_projection_attempt"] = None
    confirmation["matching_projection_healthy_count"] = 0
    path = tmp_path / "health.json"
    path.write_text(json.dumps(report))

    result = subprocess.run(
        [sys.executable, str(validator.__file__), "--input", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""

    path.write_bytes(b"{" + b" " * validator.REPORT_MAX_BYTES)
    result = subprocess.run(
        [sys.executable, str(validator.__file__), "--input", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2

    path.write_text('{"healthy":false,"healthy":true}')
    assert validator.path_confirms_recovery(path) is False
