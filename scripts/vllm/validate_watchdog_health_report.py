#!/usr/bin/env python3
"""Validate that fresh Site Health evidence authorizes Pages recovery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPORT_MAX_BYTES = 64 * 1024
CONFIRMATION_ATTEMPTS = 3
CONFIRMATION_QUORUM = 2


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate key {key!r}")
        value[key] = item
    return value


def report_confirms_recovery(report: object) -> bool:
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
        probe["healthy"] and probe["matches_complete_projection"]
        for probe in probes
    ):
        return False

    complete_probe_count = sum(probe["complete_projection"] for probe in probes)
    if projection_verified:
        if (
            type(projection_attempt) is not int
            or not 1 <= projection_attempt <= CONFIRMATION_ATTEMPTS
            or complete_probe_count != 1
            or probes[projection_attempt - 1]["complete_projection"] is not True
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
        and confirmation.get("max_attempts") == CONFIRMATION_ATTEMPTS
        and confirmation.get("attempted") == CONFIRMATION_ATTEMPTS
        and confirmation.get("required_healthy") == CONFIRMATION_QUORUM
        and confirmation.get("required_matching_projection_healthy")
        == CONFIRMATION_QUORUM
    )
    mandatory_health_failed = (
        healthy_count < CONFIRMATION_QUORUM
        or projection_verified is False
        or matching_count < CONFIRMATION_QUORUM
    )
    return contract_valid and mandatory_health_failed


def path_confirms_recovery(path: Path) -> bool:
    """Load one strict, bounded JSON report and validate its recovery authority."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(REPORT_MAX_BYTES + 1)
    except OSError:
        return False
    if not raw or len(raw) > REPORT_MAX_BYTES:
        return False
    try:
        report = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False
    return report_confirms_recovery(report)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    return 0 if path_confirms_recovery(args.input) else 2


if __name__ == "__main__":
    raise SystemExit(main())
