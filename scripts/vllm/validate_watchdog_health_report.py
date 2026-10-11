#!/usr/bin/env python3
"""Choose bounded collection or Pages recovery from confirmed Site Health evidence."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REPORT_MAX_BYTES = 64 * 1024
CONFIRMATION_ATTEMPTS = 3
CONFIRMATION_QUORUM = 2
REPORT_REASON_MAX_ITEMS = 42
PROBE_REASON_MAX_ITEMS = 20
REASON_CODE_MAX_CHARS = 80
REASON_MESSAGE_MAX_CHARS = 2048
REASON_CODE_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
SUMMARY_REASON_CODES = frozenset(
    {
        "confirmation-quorum",
        "complete-projection-required",
        "projection-generation-quorum",
    }
)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate key {key!r}")
        value[key] = item
    return value


def _failed_quorum_is_valid(report: object) -> bool:
    """Return whether a bounded report proves one mandatory health failure."""
    if not isinstance(report, dict):
        return False
    confirmation = report.get("confirmation")
    if not isinstance(confirmation, dict):
        return False
    probes = confirmation.get("probes")
    if not isinstance(probes, list) or len(probes) != CONFIRMATION_ATTEMPTS:
        return False
    if not all(
        isinstance(probe, dict)
        and type(probe.get("healthy")) is bool
        and type(probe.get("complete_projection")) is bool
        and type(probe.get("matches_complete_projection")) is bool
        for probe in probes
    ):
        return False

    healthy_count = confirmation.get("healthy_count")
    unhealthy_count = confirmation.get("unhealthy_count")
    projection_verified = confirmation.get("complete_projection_verified")
    projection_attempt = confirmation.get("complete_projection_attempt")
    matching_count = confirmation.get("matching_projection_healthy_count")
    if (
        type(healthy_count) is not int
        or not 0 <= healthy_count <= CONFIRMATION_ATTEMPTS
        or type(unhealthy_count) is not int
        or not 0 <= unhealthy_count <= CONFIRMATION_ATTEMPTS
        or type(matching_count) is not int
        or not 0 <= matching_count <= healthy_count
        or type(projection_verified) is not bool
    ):
        return False
    if healthy_count != sum(probe["healthy"] for probe in probes):
        return False
    if unhealthy_count != CONFIRMATION_ATTEMPTS - healthy_count:
        return False
    if matching_count != sum(
        probe["healthy"] and probe["matches_complete_projection"] for probe in probes
    ):
        return False

    complete_probe_count = sum(probe["complete_projection"] for probe in probes)
    if projection_verified:
        if (
            type(projection_attempt) is not int
            or not 1 <= projection_attempt <= CONFIRMATION_ATTEMPTS
            or complete_probe_count != 1
            or probes[projection_attempt - 1]["complete_projection"] is not True
            or probes[projection_attempt - 1]["matches_complete_projection"] is not True
        ):
            return False
    elif (
        projection_attempt is not None
        or complete_probe_count != 0
        or any(probe["matches_complete_projection"] for probe in probes)
    ):
        return False

    contract_valid = (
        report.get("healthy") is False
        and report.get("overall_status") == "confirmed_unhealthy"
        and confirmation.get("confirmed") is True
        and confirmation.get("strategy") == "2-of-3-quorum"
        and all(
            type(confirmation.get(field)) is int and confirmation[field] == expected
            for field, expected in (
                ("max_attempts", CONFIRMATION_ATTEMPTS),
                ("attempted", CONFIRMATION_ATTEMPTS),
                ("required_healthy", CONFIRMATION_QUORUM),
                ("required_matching_projection_healthy", CONFIRMATION_QUORUM),
            )
        )
    )
    mandatory_health_failed = (
        healthy_count < CONFIRMATION_QUORUM
        or projection_verified is False
        or matching_count < CONFIRMATION_QUORUM
    )
    return contract_valid and mandatory_health_failed


def _safe_reason_code(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= REASON_CODE_MAX_CHARS
        and REASON_CODE_RE.fullmatch(value) is not None
    )


def _reason_contract_is_valid(report: dict[str, Any]) -> bool:
    reasons = report.get("reasons")
    if not isinstance(reasons, list) or not 1 <= len(reasons) <= REPORT_REASON_MAX_ITEMS:
        return False
    for reason in reasons:
        if not isinstance(reason, dict) or set(reason) != {"code", "message"}:
            return False
        message = reason["message"]
        if (
            not _safe_reason_code(reason["code"])
            or not isinstance(message, str)
            or not message.strip()
            or len(message) > REASON_MESSAGE_MAX_CHARS
            or not all(character.isprintable() for character in message)
        ):
            return False

    confirmation = report["confirmation"]
    probe_codes: set[str] = set()
    truncated_probe_codes = False
    for probe in confirmation["probes"]:
        codes = probe.get("reason_codes")
        if (
            not isinstance(codes, list)
            or len(codes) > PROBE_REASON_MAX_ITEMS
            or any(not _safe_reason_code(code) or code in SUMMARY_REASON_CODES for code in codes)
            or probe["healthy"] != (not codes)
        ):
            return False
        probe_codes.update(codes)
        truncated_probe_codes |= len(codes) == PROBE_REASON_MAX_ITEMS

    expected_summary_codes = set()
    if confirmation["healthy_count"] < CONFIRMATION_QUORUM:
        expected_summary_codes.add("confirmation-quorum")
    if not confirmation["complete_projection_verified"]:
        expected_summary_codes.add("complete-projection-required")
    elif confirmation["matching_projection_healthy_count"] < CONFIRMATION_QUORUM:
        expected_summary_codes.add("projection-generation-quorum")
    report_codes = {reason["code"] for reason in reasons}
    # The checker retains 20 codes per probe, but unions up to 40 original
    # reasons. A full probe list can therefore hide a legitimate top-level code.
    # Such ambiguity can authorize Pages recovery, never the collector lane.
    return (
        report_codes & SUMMARY_REASON_CODES == expected_summary_codes
        and (truncated_probe_codes or report_codes <= probe_codes | expected_summary_codes)
        and (
            bool(report_codes & probe_codes)
            or not probe_codes
            and report_codes == expected_summary_codes
        )
    )


def report_recovery_target(report: object) -> str | None:
    """Route only complete, identity-consistent staleness to the collector."""
    if (
        not isinstance(report, dict)
        or not _failed_quorum_is_valid(report)
        or not _reason_contract_is_valid(report)
    ):
        return None
    confirmation = report["confirmation"]
    probes = confirmation["probes"]
    pure_staleness = (
        confirmation["complete_projection_verified"] is True
        and all(probe["matches_complete_projection"] for probe in probes)
        and sum(probe["reason_codes"] == ["publication-stale"] for probe in probes)
        >= CONFIRMATION_QUORUM
        and all(
            probe["healthy"] or probe["reason_codes"] == ["publication-stale"] for probe in probes
        )
        and {reason["code"] for reason in report["reasons"]}
        <= {"publication-stale", "confirmation-quorum", "projection-generation-quorum"}
        and any(reason["code"] == "publication-stale" for reason in report["reasons"])
    )
    return "collector" if pure_staleness else "deploy-pages"


def report_confirms_recovery(report: object) -> bool:
    """Return whether the report authorizes either bounded recovery lane."""
    return report_recovery_target(report) is not None


def _reject_nonfinite(value: str) -> None:
    raise ValueError("non-finite JSON number")


def path_recovery_target(path: Path) -> str | None:
    """Load one strict, bounded JSON report and choose its recovery target."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(REPORT_MAX_BYTES + 1)
    except OSError:
        return None
    if not raw or len(raw) > REPORT_MAX_BYTES:
        return None
    try:
        report = json.loads(raw, object_pairs_hook=_strict_object, parse_constant=_reject_nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return None
    return report_recovery_target(report)


def path_confirms_recovery(path: Path) -> bool:
    """Return whether a strict, bounded report authorizes recovery."""
    return path_recovery_target(path) is not None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--print-recovery-target", action="store_true")
    args = parser.parse_args()
    target = path_recovery_target(args.input)
    if target is None:
        return 2
    if args.print_recovery_target:
        print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
