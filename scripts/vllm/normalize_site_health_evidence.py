#!/usr/bin/env python3
"""Normalize and attest bounded evidence from the site-health checker.

The workflow deliberately invokes this as a standalone program so ordinary
Python import and lint checks cover the full fail-closed evidence contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Support direct execution as ``python scripts/vllm/normalize_site_health_evidence.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def normalize_health_evidence() -> None:
    import base64
    import html
    import json
    import math
    import os
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from vllm.check_site_health import (
        CANARY_FETCH_TIMEOUT_SECONDS,
        CONFIRMATION_ATTEMPTS,
        CONFIRMATION_DELAYS_SECONDS,
        CONFIRMATION_QUORUM,
        CRITICAL_ASSET_PATHS,
        DEFAULT_MAX_PUBLICATION_AGE_HOURS,
        FETCH_TIMEOUT_SECONDS,
        FUTURE_SKEW,
        MAX_CONFIRMATION_ELAPSED_SECONDS,
        MAX_CONFIRMATION_REQUESTS,
        MAX_CONFIRMATION_TRANSPORT_SECONDS,
        OPERATIONS_CANARY_SECTIONS,
        OPERATIONS_MANIFEST_PATH,
        OPERATIONS_STREAMED_LARGE_SECTIONS,
        OPERATIONS_STREAMED_MAX_BYTES,
        PUBLICATION_STATUS_PATH,
        SITE_MIN_BYTES,
    )

    max_report_bytes = 64 * 1024
    max_details_bytes = 16 * 1024
    max_requests = MAX_CONFIRMATION_REQUESTS
    max_transport_seconds = MAX_CONFIRMATION_TRANSPORT_SECONDS
    max_elapsed_seconds = MAX_CONFIRMATION_ELAPSED_SECONDS
    confirmation_strategy = (
        f"{CONFIRMATION_QUORUM}-of-{CONFIRMATION_ATTEMPTS}-quorum"
    )
    expected_operations_canaries = [
        {
            "name": name,
            "path": f"data/vllm/ci/operations_v2/{name}.json",
            "http_status": 200,
        }
        for name in OPERATIONS_CANARY_SECTIONS
    ]
    expected_verified_files = [
        "index.html",
        PUBLICATION_STATUS_PATH,
        *CRITICAL_ASSET_PATHS,
        OPERATIONS_MANIFEST_PATH,
        *[row["path"] for row in expected_operations_canaries],
        *[
            f"data/vllm/ci/operations_v2/{name}.json"
            for name in OPERATIONS_STREAMED_LARGE_SECTIONS
        ],
    ]
    report_path = Path(os.environ["REPORT_PATH"])
    effective_report_path = Path(
        os.environ.get("EFFECTIVE_REPORT_PATH", os.environ["REPORT_PATH"])
    )
    details_path = Path(os.environ["DETAILS_PATH"])
    body_path = Path(os.environ["BODY_PATH"])

    def fallback_report(reason):
        return {
            "schema_version": 1,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "healthy": False,
            "overall_status": "workflow_evidence_invalid",
            "reasons": [reason],
        }

    def reject_duplicate_keys(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def reject_nonfinite_constant(value):
        raise ValueError(f"non-finite JSON constant: {value}")

    report_syntax_valid = False
    report_error = ""
    try:
        raw = report_path.read_bytes()
        if len(raw) > max_report_bytes:
            raise ValueError("checker report exceeded 64 KiB")
        report = json.loads(
            raw,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite_constant,
        )
        if not isinstance(report, dict):
            raise ValueError("checker report was not a JSON object")
        encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
        if len(encoded) > max_report_bytes:
            raise ValueError("normalized checker report exceeded 64 KiB")
        report_syntax_valid = True
    except (OSError, RecursionError, ValueError, json.JSONDecodeError) as exc:
        report_error = str(exc)[:500]
        report = fallback_report(report_error)

    required = {
        "healthy": os.environ.get("CHECKER_HEALTHY", ""),
        "overall_status": os.environ.get("OVERALL_STATUS", ""),
        "site_http": os.environ.get("SITE_HTTP", ""),
        "site_bytes": os.environ.get("SITE_BYTES", ""),
        "publication_http": os.environ.get("PUBLICATION_HTTP", ""),
        "publication_mode": os.environ.get("PUBLICATION_MODE", ""),
        "publication_status": os.environ.get("PUBLICATION_STATUS", ""),
        "generated_at": os.environ.get("GENERATED_AT", ""),
        "age_hours": os.environ.get("AGE_HOURS", ""),
        "reason_count": os.environ.get("REASON_COUNT", ""),
        "confirmation_confirmed": os.environ.get("CONFIRMATION_CONFIRMED", ""),
        "probe_attempts": os.environ.get("PROBE_ATTEMPTS", ""),
        "healthy_probe_count": os.environ.get("HEALTHY_PROBE_COUNT", ""),
        "required_healthy_probes": os.environ.get("REQUIRED_HEALTHY_PROBES", ""),
    }
    # A completed unhealthy probe can legitimately have no parsed mode,
    # status, generation, or age (for example when publication_status.json
    # is unavailable or malformed). Quorum/transport evidence is mandatory;
    # nullable publication diagnostics are cross-checked below and become
    # mandatory through the stricter healthy-report contract.
    mandatory_output_keys = {
        "healthy",
        "overall_status",
        "site_http",
        "site_bytes",
        "publication_http",
        "reason_count",
        "confirmation_confirmed",
        "probe_attempts",
        "healthy_probe_count",
        "required_healthy_probes",
    }
    missing = sorted(key for key in mandatory_output_keys if not required[key].strip())
    contract_errors = []

    def parse_nonnegative_int_output(key, label):
        raw_value = required[key]
        try:
            value = int(raw_value, 10)
        except ValueError:
            contract_errors.append(f"checker {label} output was not an integer")
            return None
        if raw_value != str(value) or value < 0:
            contract_errors.append(
                f"checker {label} output was not a canonical nonnegative integer"
            )
            return None
        return value

    def is_nonnegative_int(value):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def is_finite_number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False

    checker_healthy = None
    if required["healthy"] in {"true", "false"}:
        checker_healthy = required["healthy"] == "true"
    else:
        contract_errors.append("checker healthy output was not boolean")
    if not required["overall_status"]:
        contract_errors.append("checker overall_status output was missing")
    output_site_http = parse_nonnegative_int_output("site_http", "site HTTP")
    output_site_bytes = parse_nonnegative_int_output("site_bytes", "site bytes")
    output_publication_http = parse_nonnegative_int_output("publication_http", "publication HTTP")
    output_reason_count = parse_nonnegative_int_output("reason_count", "reason count")
    output_probe_attempts = parse_nonnegative_int_output("probe_attempts", "probe attempts")
    output_healthy_probe_count = parse_nonnegative_int_output(
        "healthy_probe_count", "healthy probe count"
    )
    output_required_healthy = parse_nonnegative_int_output(
        "required_healthy_probes", "required healthy probes"
    )
    confirmation_confirmed = None
    if required["confirmation_confirmed"] in {"true", "false"}:
        confirmation_confirmed = required["confirmation_confirmed"] == "true"
    else:
        contract_errors.append("checker confirmation output was not boolean")

    if report_syntax_valid:
        reasons = report.get("reasons")
        report_healthy = report.get("healthy")
        site = report.get("site")
        publication = report.get("publication")
        projection = report.get("projection")
        confirmation = report.get("confirmation")
        if type(report.get("schema_version")) is not int or report.get("schema_version") != 1:
            contract_errors.append("report schema_version was not 1")
        if not isinstance(report_healthy, bool):
            contract_errors.append("report healthy was not boolean")
        elif checker_healthy is not None and report_healthy != checker_healthy:
            contract_errors.append("report healthy disagreed with checker output")
        if not isinstance(reasons, list):
            contract_errors.append("report reasons was not an array")
        elif output_reason_count is not None and len(reasons) != output_reason_count:
            contract_errors.append("report reason count disagreed with checker output")
        report_status = report.get("overall_status")
        if not isinstance(report_status, str) or not report_status:
            contract_errors.append("report overall_status was not a nonempty string")
        elif report_status != required["overall_status"]:
            contract_errors.append("report overall_status disagreed with checker output")
        if checker_healthy is True and required["overall_status"] != "healthy":
            contract_errors.append("healthy checker output lacked healthy overall_status")
        if checker_healthy is False and required["overall_status"] == "healthy":
            contract_errors.append("unhealthy checker output claimed healthy status")

        if not isinstance(site, dict):
            contract_errors.append("report site was not an object")
            site = {}
        if not isinstance(publication, dict):
            contract_errors.append("report publication was not an object")
            publication = {}
        if not isinstance(projection, dict):
            contract_errors.append("report projection was not an object")
            projection = {}
        if not isinstance(confirmation, dict):
            contract_errors.append("report confirmation was not an object")
            confirmation = {}

        report_confirmed = confirmation.get("confirmed")
        attempted = confirmation.get("attempted")
        required_healthy = confirmation.get("required_healthy")
        healthy_count = confirmation.get("healthy_count")
        unhealthy_count = confirmation.get("unhealthy_count")
        streamed_projection_attempt = confirmation.get("streamed_projection_attempt")
        complete_projection_attempt = confirmation.get("complete_projection_attempt")
        complete_projection_verified = confirmation.get("complete_projection_verified")
        matching_projection_healthy_count = confirmation.get(
            "matching_projection_healthy_count"
        )
        required_matching_projection_healthy = confirmation.get(
            "required_matching_projection_healthy"
        )
        probes = confirmation.get("probes")
        if report_confirmed is not True:
            contract_errors.append("report lacked completed quorum confirmation")
        if confirmation_confirmed is not None and report_confirmed is not confirmation_confirmed:
            contract_errors.append("report confirmation disagreed with checker output")
        if (
            confirmation.get("strategy") != confirmation_strategy
            or confirmation.get("max_attempts") != CONFIRMATION_ATTEMPTS
            or attempted != CONFIRMATION_ATTEMPTS
            or required_healthy != CONFIRMATION_QUORUM
            or (
                streamed_projection_attempt is not None
                and (
                    not is_nonnegative_int(streamed_projection_attempt)
                    or not 1 <= streamed_projection_attempt <= CONFIRMATION_ATTEMPTS
                )
            )
            or (
                complete_projection_attempt is not None
                and (
                    not is_nonnegative_int(complete_projection_attempt)
                    or not 1 <= complete_projection_attempt <= CONFIRMATION_ATTEMPTS
                )
            )
            or not isinstance(complete_projection_verified, bool)
            or (
                complete_projection_verified
                and complete_projection_attempt != streamed_projection_attempt
            )
            or (complete_projection_verified != (complete_projection_attempt is not None))
            or not is_nonnegative_int(healthy_count)
            or not is_nonnegative_int(unhealthy_count)
            or not is_nonnegative_int(matching_projection_healthy_count)
            or required_matching_projection_healthy != CONFIRMATION_QUORUM
            or matching_projection_healthy_count > healthy_count
            or confirmation.get("max_requests") != max_requests
            or confirmation.get("per_request_timeout_seconds") != FETCH_TIMEOUT_SECONDS
            or confirmation.get("canary_request_timeout_seconds")
            != CANARY_FETCH_TIMEOUT_SECONDS
            or confirmation.get("max_transport_seconds") != max_transport_seconds
            or confirmation.get("retry_delays_seconds") != list(CONFIRMATION_DELAYS_SECONDS)
            or confirmation.get("max_elapsed_seconds") != max_elapsed_seconds
            or healthy_count + unhealthy_count != attempted
            or not isinstance(probes, list)
            or len(probes) != attempted
        ):
            contract_errors.append("report quorum confirmation was malformed")
        if output_probe_attempts is not None and attempted != output_probe_attempts:
            contract_errors.append("report probe attempts disagreed with checker output")
        if output_healthy_probe_count is not None and healthy_count != output_healthy_probe_count:
            contract_errors.append("report healthy probe count disagreed with checker output")
        if output_required_healthy is not None and required_healthy != output_required_healthy:
            contract_errors.append("report quorum disagreed with checker output")
        if isinstance(report_healthy, bool) and is_nonnegative_int(healthy_count):
            expected_health = (
                healthy_count >= CONFIRMATION_QUORUM
                and complete_projection_verified is True
                and is_nonnegative_int(matching_projection_healthy_count)
                and matching_projection_healthy_count >= CONFIRMATION_QUORUM
            )
            if report_healthy != expected_health:
                contract_errors.append("report health disagreed with its probe quorum")
        if isinstance(probes, list):
            observed_probe_health = 0
            observed_matching_projection_health = 0
            observed_complete_projection_attempts = []
            observed_streamed_projection_attempts = []
            expected_probe_fields = {
                "attempt",
                "checked_at",
                "healthy",
                "site_http",
                "publication_http",
                "generation_http",
                "manifest_http",
                "projection_mode",
                "projection_verified",
                "complete_projection",
                "streamed_projection_attempted",
                "matches_complete_projection",
                "reason_codes",
            }
            for index, probe in enumerate(probes, 1):
                if not isinstance(probe, dict) or set(probe) != expected_probe_fields:
                    contract_errors.append("report probe summary was malformed")
                    continue
                if probe.get("attempt") != index or not isinstance(probe.get("healthy"), bool):
                    contract_errors.append("report probe sequence was malformed")
                else:
                    observed_probe_health += int(probe["healthy"])
                    if probe.get("healthy") is True and probe.get(
                        "matches_complete_projection"
                    ) is True:
                        observed_matching_projection_health += 1
                if (
                    not isinstance(probe.get("complete_projection"), bool)
                    or not isinstance(probe.get("streamed_projection_attempted"), bool)
                    or not isinstance(probe.get("matches_complete_projection"), bool)
                ):
                    contract_errors.append("report probe projection proof was malformed")
                elif probe["complete_projection"]:
                    observed_complete_projection_attempts.append(index)
                if probe.get("streamed_projection_attempted") is True:
                    observed_streamed_projection_attempts.append(index)
                if probe.get("complete_projection") is not (
                    probe.get("projection_mode") == "verified"
                    and probe.get("projection_verified") is True
                ):
                    contract_errors.append(
                        "report probe complete projection label was inconsistent"
                    )
                for field in (
                    "site_http",
                    "publication_http",
                    "generation_http",
                    "manifest_http",
                ):
                    if not is_nonnegative_int(probe.get(field)):
                        contract_errors.append("report probe HTTP evidence was malformed")
                reason_codes = probe.get("reason_codes")
                if (
                    not isinstance(reason_codes, list)
                    or len(reason_codes) > 20
                    or any(not isinstance(code, str) or not code for code in reason_codes)
                ):
                    contract_errors.append("report probe findings were malformed")
                if probe.get("healthy") is True:
                    projection_mode = probe.get("projection_mode")
                    projection_proof_valid = (
                        projection_mode == "verified"
                        and probe.get("projection_verified") is True
                        and probe.get("generation_http") == 200
                        and probe.get("manifest_http") == 200
                    ) or (
                        projection_mode == "critical-routes-verified"
                        and probe.get("projection_verified") is False
                        and probe.get("complete_projection") is False
                        and probe.get("generation_http") == 200
                        and probe.get("manifest_http") == 200
                    ) or (
                        projection_mode == "manifest-identity-verified"
                        and probe.get("projection_verified") is False
                        and probe.get("complete_projection") is False
                        and probe.get("generation_http") == 200
                        and probe.get("manifest_http") == 200
                    ) or (
                        projection_mode == "legacy-bootstrap"
                        and probe.get("projection_verified") is False
                        and probe.get("generation_http") == 404
                        and probe.get("manifest_http") == 404
                    )
                    if (
                        probe.get("site_http") != 200
                        or probe.get("publication_http") != 200
                        or reason_codes != []
                        or not projection_proof_valid
                    ):
                        contract_errors.append("healthy probe summary lacked health proof")
            if is_nonnegative_int(healthy_count) and observed_probe_health != healthy_count:
                contract_errors.append("report probe summaries disagreed with healthy count")
            if observed_complete_projection_attempts != (
                [complete_projection_attempt] if complete_projection_verified is True else []
            ):
                contract_errors.append(
                    "report probe summaries disagreed with complete projection proof"
                )
            if observed_streamed_projection_attempts != (
                [streamed_projection_attempt]
                if streamed_projection_attempt is not None
                else []
            ):
                contract_errors.append(
                    "report probe summaries disagreed with streamed projection attempt"
                )
            if (
                is_nonnegative_int(matching_projection_healthy_count)
                and observed_matching_projection_health
                != matching_projection_healthy_count
            ):
                contract_errors.append(
                    "report probe summaries disagreed with matching projection count"
                )

        report_site_http = site.get("http_status")
        if not is_nonnegative_int(report_site_http):
            contract_errors.append("report site http_status was not a nonnegative integer")
        elif output_site_http is not None and report_site_http != output_site_http:
            contract_errors.append("report site HTTP disagreed with checker output")

        report_site_bytes = site.get("bytes_read")
        if not is_nonnegative_int(report_site_bytes):
            contract_errors.append("report site bytes_read was not a nonnegative integer")
        elif output_site_bytes is not None and report_site_bytes != output_site_bytes:
            contract_errors.append("report site bytes disagreed with checker output")

        report_publication_http = publication.get("http_status")
        if not is_nonnegative_int(report_publication_http):
            contract_errors.append("report publication http_status was not a nonnegative integer")
        elif (
            output_publication_http is not None
            and report_publication_http != output_publication_http
        ):
            contract_errors.append("report publication HTTP disagreed with checker output")

        for field, output_key, label in (
            ("mode", "publication_mode", "publication mode"),
            ("status", "publication_status", "publication status"),
            ("generated_at", "generated_at", "publication timestamp"),
        ):
            report_value = publication.get(field)
            if report_value is not None and (
                not isinstance(report_value, str)
                or not report_value
                or "\r" in report_value
                or "\n" in report_value
            ):
                contract_errors.append(f"report {label} was not a safe nonempty string or null")
                continue
            expected_output = "" if report_value is None else report_value
            if required[output_key] != expected_output:
                contract_errors.append(f"report {label} disagreed with checker output")

        report_generated_at = publication.get("generated_at")
        if isinstance(report_generated_at, str) and report_generated_at:
            try:
                parsed_generated_at = datetime.fromisoformat(
                    report_generated_at.replace("Z", "+00:00")
                )
                if parsed_generated_at.tzinfo is None:
                    raise ValueError
            except ValueError:
                contract_errors.append(
                    "report publication timestamp was not timezone-aware ISO-8601"
                )

        report_age = publication.get("age_hours")
        if report_age is None:
            if required["age_hours"]:
                contract_errors.append("report publication age disagreed with checker output")
        elif not is_finite_number(report_age):
            contract_errors.append("report publication age_hours was not a finite number or null")
        else:
            try:
                output_age = float(required["age_hours"])
            except ValueError:
                output_age = math.nan
            if not math.isfinite(output_age) or required["age_hours"] != str(report_age):
                contract_errors.append("report publication age disagreed with checker output")

        if checker_healthy is True:
            if confirmation_confirmed is not True:
                contract_errors.append("healthy report was not quorum-confirmed")
            if reasons:
                contract_errors.append("healthy report contained findings")
            if (
                report_site_http != 200
                or not is_nonnegative_int(report_site_bytes)
                or report_site_bytes < SITE_MIN_BYTES
            ):
                contract_errors.append("healthy report failed the site-shell contract")
            if report_publication_http != 200:
                contract_errors.append("healthy report lacked publication HTTP 200")
            report_mode = publication.get("mode")
            report_publication_status = publication.get("status")
            if report_mode not in {"current", "degraded", "fallback", "mixed"}:
                contract_errors.append("healthy report used an unhealthy publication mode")
            if report_publication_status not in {"healthy", "degraded"}:
                contract_errors.append("healthy report used an unhealthy publication status")
            if publication.get("publication_blocked") is not False:
                contract_errors.append("healthy report was publication-blocked")
            expected_fallback = report_mode in {"fallback", "mixed"}
            if publication.get("uses_fallback") is not expected_fallback:
                contract_errors.append("healthy report had contradictory fallback state")
            affected_surfaces = publication.get("affected_surfaces")
            affected_surface_count = publication.get("affected_surface_count")
            fallback_surface_count = publication.get("fallback_surface_count")
            fresh_degraded_surface_count = publication.get(
                "fresh_degraded_surface_count"
            )
            if (
                not isinstance(affected_surfaces, list)
                or any(not isinstance(item, str) or not item for item in affected_surfaces)
                or affected_surfaces != sorted(set(affected_surfaces))
                or not all(
                    is_nonnegative_int(value)
                    for value in (
                        affected_surface_count,
                        fallback_surface_count,
                        fresh_degraded_surface_count,
                    )
                )
                or affected_surface_count != len(affected_surfaces)
                or fallback_surface_count > affected_surface_count
                or fresh_degraded_surface_count > affected_surface_count
            ):
                contract_errors.append("healthy report had malformed publication surfaces")
            elif report_mode == "current" and (
                affected_surfaces
                or affected_surface_count != 0
                or fallback_surface_count != 0
                or fresh_degraded_surface_count != 0
            ):
                contract_errors.append("healthy current report had degraded surfaces")
            if (
                not is_finite_number(report_age)
                or report_age < -(FUTURE_SKEW.total_seconds() / 3600)
                or report_age > DEFAULT_MAX_PUBLICATION_AGE_HOURS
            ):
                contract_errors.append("healthy report had an invalid publication age")
            projection_mode = projection.get("mode")
            if projection_mode == "verified":
                exact_identity_fields = (
                    projection.get("state_sha"),
                    projection.get("state_tree"),
                    projection.get("code_sha"),
                )
                exact_identity_valid = all(
                    isinstance(value, str)
                    and len(value) == 40
                    and all(char in "0123456789abcdef" for char in value)
                    for value in exact_identity_fields
                )
                streamed_rows = projection.get("operations_streamed_sections")
                streamed_proof_valid = (
                    isinstance(streamed_rows, list)
                    and len(streamed_rows) == len(OPERATIONS_STREAMED_LARGE_SECTIONS)
                )
                if streamed_proof_valid:
                    for name, row in zip(OPERATIONS_STREAMED_LARGE_SECTIONS, streamed_rows):
                        streamed_proof_valid = bool(
                            isinstance(row, dict)
                            and set(row)
                            == {
                                "name",
                                "path",
                                "http_status",
                                "bytes_read",
                                "sha256",
                                "verified",
                            }
                            and row.get("name") == name
                            and row.get("path")
                            == f"data/vllm/ci/operations_v2/{name}.json"
                            and row.get("http_status") == 200
                            and is_nonnegative_int(row.get("bytes_read"))
                            and 0 < row["bytes_read"] <= OPERATIONS_STREAMED_MAX_BYTES
                            and isinstance(row.get("sha256"), str)
                            and len(row["sha256"]) == 64
                            and all(
                                char in "0123456789abcdef" for char in row["sha256"]
                            )
                            and row.get("verified") is True
                        )
                        if not streamed_proof_valid:
                            break
                if (
                    projection.get("verified") is not True
                    or projection.get("verification_scope") != "complete"
                    or projection.get("generation_http") != 200
                    or projection.get("manifest_http") != 200
                    or not exact_identity_valid
                    or not isinstance(projection.get("manifest_sha256"), str)
                    or len(projection["manifest_sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in projection["manifest_sha256"])
                    or not is_nonnegative_int(projection.get("file_count"))
                    or not is_nonnegative_int(projection.get("total_bytes"))
                    or projection.get("verified_files") != expected_verified_files
                    or projection.get("operations_canaries")
                    != expected_operations_canaries
                    or not streamed_proof_valid
                ):
                    contract_errors.append("healthy report lacked exact projection proof")
            elif projection_mode == "legacy-bootstrap":
                try:
                    from vllm.check_site_health import (
                        _legacy_bootstrap_allowed,
                    )

                    bootstrap_authorized = _legacy_bootstrap_allowed(
                        evidence_path=Path(os.environ["BOOTSTRAP_EVIDENCE_PATH"]),
                        repository=os.environ["GITHUB_REPOSITORY"],
                        now=datetime.now(timezone.utc),
                    )
                except (ImportError, OSError, OverflowError, ValueError):
                    bootstrap_authorized = False
                if (
                    bootstrap_authorized is not True
                    or projection.get("verified") is not False
                    or projection.get("generation_http") != 404
                    or projection.get("manifest_http") != 404
                ):
                    contract_errors.append("legacy projection exception lacked bootstrap policy")
            else:
                contract_errors.append("healthy report used an invalid projection mode")

    report_valid = report_syntax_valid and not contract_errors
    if not report_valid:
        reason = "; ".join(contract_errors) or report_error or "report invalid"
        report = fallback_report(reason[:500])

    core_outcome = os.environ.get("CORE_FRESHNESS_OUTCOME", "")
    core_required = os.environ.get("CORE_COLLECTION_REQUIRED", "")
    core_mode = os.environ.get("CORE_REQUEST_MODE", "")
    core_available_at = os.environ.get("CORE_AVAILABLE_AT", "")
    core_latest_succeeded_at = os.environ.get("CORE_LATEST_SUCCEEDED_AT", "")
    core_observation_valid = (
        core_outcome == "success"
        and core_required in {"true", "false"}
        and core_mode in {"reserved", "success_gated", "retry_gated", "cap_gated"}
        and ((core_required == "true") == (core_mode == "reserved"))
    )
    core_success_at = None
    if core_latest_succeeded_at:
        try:
            core_success_at = datetime.fromisoformat(
                core_latest_succeeded_at.replace("Z", "+00:00")
            )
            if (
                core_success_at.tzinfo is None
                or core_success_at.microsecond
                or core_success_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                != core_latest_succeeded_at
            ):
                core_success_at = None
        except (OverflowError, ValueError):
            core_success_at = None
    now = datetime.now(timezone.utc)
    core_current = bool(
        core_observation_valid
        and core_success_at is not None
        and now - timedelta(minutes=5) <= core_success_at + timedelta(hours=3)
        and core_success_at <= now + timedelta(minutes=5)
    )

    confirmed = (
        confirmation_confirmed is True
        and not missing
        and report_valid
        and core_observation_valid
        and required["overall_status"] in {"healthy", "confirmed_unhealthy"}
        and (
            (checker_healthy is True and os.environ.get("CHECKER_OUTCOME") == "success")
            or (checker_healthy is False and os.environ.get("CHECKER_OUTCOME") == "failure")
        )
    )
    healthy = confirmed and checker_healthy is True and core_current

    effective_status = "workflow_evidence_invalid"
    if report_valid:
        if not confirmed:
            effective_status = "workflow_evidence_unconfirmed"
        elif checker_healthy is not True:
            effective_status = "confirmed_unhealthy"
        elif not core_current:
            effective_status = "durable_core_stale"
        else:
            effective_status = "healthy"

        checker_reasons = report.get("reasons")
        effective_reasons = list(checker_reasons) if isinstance(checker_reasons, list) else []
        if not confirmed:
            effective_reasons.append(
                {
                    "code": "workflow-evidence-unconfirmed",
                    "message": (
                        "Synthetic quorum and durable-core evidence did not form "
                        "one confirmed workflow result."
                    ),
                }
            )
        if not core_observation_valid:
            effective_reasons.append(
                {
                    "code": "durable-core-observation-invalid",
                    "message": (
                        "The durable core collection ledger could not be observed "
                        "with a valid bounded contract."
                    ),
                }
            )
        elif not core_current:
            effective_reasons.append(
                {
                    "code": "durable-core-stale",
                    "message": (
                        "The latest durable core collection success exceeded the "
                        "three-hour freshness contract."
                    ),
                }
            )

        def bounded_evidence(value):
            text = " ".join(str(value or "").split())[:200]
            return text or None

        report["checker_health"] = {
            "healthy": checker_healthy,
            "overall_status": required["overall_status"] or None,
            "outcome": bounded_evidence(os.environ.get("CHECKER_OUTCOME")),
        }
        report["durable_core"] = {
            "observation_valid": core_observation_valid,
            "current": core_current,
            "outcome": bounded_evidence(core_outcome),
            "collection_required": (
                core_required == "true" if core_required in {"true", "false"} else None
            ),
            "request_mode": bounded_evidence(core_mode),
            "available_at": bounded_evidence(core_available_at),
            "latest_succeeded_at": bounded_evidence(core_latest_succeeded_at),
            "max_age_hours": 3,
        }
        report["workflow_confirmation"] = {
            "confirmed": confirmed,
            "synthetic_quorum_confirmed": confirmation_confirmed is True,
            "durable_core_observation_valid": core_observation_valid,
        }
        report["healthy"] = healthy
        report["overall_status"] = effective_status
        report["reasons"] = effective_reasons

    encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    if len(encoded) > max_report_bytes:
        report_valid = False
        confirmed = False
        healthy = False
        effective_status = "workflow_evidence_invalid"
        report = fallback_report("effective health report exceeded 64 KiB")
        encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    # The workflow pre-seeds this independent path with fail-closed evidence.
    # Prepare the candidate now, but do not promote it until the issue body and
    # all GitHub outputs have also been durably written.  Replacing this file is
    # deliberately the normalizer's final I/O operation: any earlier exception
    # leaves the fail-closed seed at the artifact path.
    effective_report_path.parent.mkdir(parents=True, exist_ok=True)
    effective_report_tmp = effective_report_path.with_name(
        f".{effective_report_path.name}.normalizing"
    )
    effective_report_tmp.write_bytes(encoded)

    try:
        details_raw = details_path.read_bytes()
        if len(details_raw) > max_details_bytes:
            raise ValueError("checker details exceeded 16 KiB")
        details = details_raw.decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        details = f"Synthetic checker details unavailable: {exc}"
    escaped_details = html.escape(details[:max_details_bytes])
    safe_details = escaped_details.encode("utf-8")[:max_details_bytes].decode(
        "utf-8", errors="ignore"
    )

    repository = os.environ["GITHUB_REPOSITORY"]
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_url = f"{server}/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    deploy_url = f"{server}/{repository}/deployments"
    status = "healthy" if healthy else "confirmed unhealthy" if confirmed else "unconfirmed"
    missing_text = ", ".join(missing) if missing else "none"
    body = "\n".join(
        (
            os.environ["OWNERSHIP_MARKER"],
            "<!-- SITE_HEALTH_STATE -->",
            "",
            f"## Dashboard synthetic health: {status}",
            "",
            f"- **Site:** [{os.environ['SITE_URL']}]({os.environ['SITE_URL']})",
            f"- **Workflow run:** [synthetic monitor evidence]({run_url})",
            f"- **Deployment history:** [GitHub deployments]({deploy_url})",
            "- **Checker outcome:** `"
            + html.escape(os.environ.get("CHECKER_OUTCOME", "missing"))
            + "`",
            "- **Quorum confirmation:** `"
            + str(confirmed).lower()
            + "` ("
            + html.escape(required["healthy_probe_count"] or "missing")
            + "/"
            + html.escape(required["probe_attempts"] or "missing")
            + " healthy; "
            + html.escape(required["required_healthy_probes"] or "missing")
            + " required)",
            "- **Overall status:** `"
            + html.escape(effective_status)
            + "`",
            "- **Publication mode/status:** `"
            + html.escape(required["publication_mode"] or "missing")
            + "` / `"
            + html.escape(required["publication_status"] or "missing")
            + "`",
            "- **Publication age:** `"
            + html.escape(required["age_hours"] or "missing")
            + "` hours",
            "- **Durable core collection:** `"
            + ("current" if core_current else "stale/unavailable")
            + "` (last success `"
            + html.escape(core_latest_succeeded_at or "missing")
            + "`, observation `"
            + html.escape(core_outcome or "missing")
            + "`, mode `"
            + html.escape(core_mode or "missing")
            + "`, next `"
            + html.escape(core_available_at or "now")
            + "`)",
            f"- **Missing checker outputs:** `{html.escape(missing_text)}`",
            "",
            f"<details><summary>Bounded checker details</summary><pre>{safe_details}</pre></details>",
            "",
            "<!-- SITE_HEALTH_RECOVERY_NOTE -->",
            "",
            "This issue is owned by the synthetic site-health workflow. Evidence is",
            "updated in place; the workflow does not post hourly comments.",
            "",
        )
    )
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_tmp = body_path.with_name(f".{body_path.name}.normalizing")
    body_tmp.write_text(body, encoding="utf-8")

    summary = (
        (
            f"healthy={str(healthy).lower()} "
            f"confirmed={str(confirmed).lower()} "
            f"status={effective_status} "
            f"mode={required['publication_mode'] or 'missing'} "
            f"age_hours={required['age_hours'] or 'missing'} "
            f"core_current={str(core_current).lower()} "
            f"core_mode={core_mode or 'missing'} "
            f"missing_outputs={len(missing)}"
        )
        .replace("\r", " ")
        .replace("\n", " ")[:1000]
    )
    hourly_recovery_evidence = ""
    if report_valid and confirmed and healthy:
        publication = (
            report.get("publication")
            if isinstance(report.get("publication"), dict)
            else {}
        )
        projection = (
            report.get("projection")
            if isinstance(report.get("projection"), dict)
            else {}
        )
        confirmation = (
            report.get("confirmation")
            if isinstance(report.get("confirmation"), dict)
            else {}
        )
        workflow_confirmation = (
            report.get("workflow_confirmation")
            if isinstance(report.get("workflow_confirmation"), dict)
            else {}
        )
        recovery_payload = {
            "normalized": True,
            "reportValid": True,
            "confirmed": workflow_confirmation.get("confirmed") is True,
            "healthy": report.get("healthy") is True,
            "overallStatus": report.get("overall_status"),
            "publicationMode": publication.get("mode"),
            "publicationStatus": publication.get("status"),
            "degradedSince": publication.get("degraded_since"),
            "publicationBlocked": publication.get("publication_blocked"),
            "usesFallback": publication.get("uses_fallback"),
            "affectedSurfaces": publication.get("affected_surfaces"),
            "affectedSurfaceCount": publication.get("affected_surface_count"),
            "fallbackSurfaceCount": publication.get("fallback_surface_count"),
            "freshDegradedSurfaceCount": publication.get(
                "fresh_degraded_surface_count"
            ),
            "generatedAt": publication.get("generated_at"),
            "confirmationStrategy": confirmation.get("strategy"),
            "probeAttempts": confirmation.get("attempted"),
            "healthyProbeCount": confirmation.get("healthy_count"),
            "requiredHealthyProbes": confirmation.get("required_healthy"),
            "completeProjectionVerified": confirmation.get(
                "complete_projection_verified"
            ),
            "matchingProjectionHealthyCount": confirmation.get(
                "matching_projection_healthy_count"
            ),
            "requiredMatchingProjectionHealthy": confirmation.get(
                "required_matching_projection_healthy"
            ),
            "generationId": projection.get("generation_id"),
            "stateSha": projection.get("state_sha"),
            "stateTree": projection.get("state_tree"),
            "codeSha": projection.get("code_sha"),
            "manifestSha256": projection.get("manifest_sha256"),
            "fileCount": projection.get("file_count"),
            "totalBytes": projection.get("total_bytes"),
        }

        def canonical_hex(value, length):
            return (
                isinstance(value, str)
                and len(value) == length
                and all(char in "0123456789abcdef" for char in value)
            )

        def canonical_generation(value):
            ascii_letters_and_digits = (
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "abcdefghijklmnopqrstuvwxyz"
                "0123456789"
            )
            allowed = ascii_letters_and_digits + "._:/-"
            return (
                isinstance(value, str)
                and 1 <= len(value) <= 128
                and value[0] in ascii_letters_and_digits
                and all(char in allowed for char in value)
            )

        healthy_probe_count = recovery_payload["healthyProbeCount"]
        matching_projection_count = recovery_payload[
            "matchingProjectionHealthyCount"
        ]
        # Synthetic liveness permits a complete degraded/fallback projection.
        # Hourly incident recovery does not: only an exact clean publication
        # and its complete matching identity proof may make the serialized job
        # eligible through this otherwise-empty output.
        recovery_eligible = (
            recovery_payload["confirmed"] is True
            and recovery_payload["healthy"] is True
            and recovery_payload["overallStatus"] == "healthy"
            and recovery_payload["publicationMode"] == "current"
            and recovery_payload["publicationStatus"] == "healthy"
            and recovery_payload["degradedSince"] is None
            and recovery_payload["publicationBlocked"] is False
            and recovery_payload["usesFallback"] is False
            and recovery_payload["affectedSurfaces"] == []
            and recovery_payload["affectedSurfaceCount"] == 0
            and recovery_payload["fallbackSurfaceCount"] == 0
            and recovery_payload["freshDegradedSurfaceCount"] == 0
            and isinstance(recovery_payload["generatedAt"], str)
            and bool(recovery_payload["generatedAt"])
            and recovery_payload["confirmationStrategy"]
            == confirmation_strategy
            and recovery_payload["probeAttempts"] == CONFIRMATION_ATTEMPTS
            and is_nonnegative_int(healthy_probe_count)
            and CONFIRMATION_QUORUM
            <= healthy_probe_count
            <= CONFIRMATION_ATTEMPTS
            and recovery_payload["requiredHealthyProbes"]
            == CONFIRMATION_QUORUM
            and recovery_payload["completeProjectionVerified"] is True
            and is_nonnegative_int(matching_projection_count)
            and CONFIRMATION_QUORUM
            <= matching_projection_count
            <= healthy_probe_count
            and recovery_payload["requiredMatchingProjectionHealthy"]
            == CONFIRMATION_QUORUM
            and canonical_generation(recovery_payload["generationId"])
            and canonical_hex(recovery_payload["stateSha"], 40)
            and canonical_hex(recovery_payload["stateTree"], 40)
            and canonical_hex(recovery_payload["codeSha"], 40)
            and canonical_hex(recovery_payload["manifestSha256"], 64)
            and is_nonnegative_int(recovery_payload["fileCount"])
            and recovery_payload["fileCount"] > 0
            and is_nonnegative_int(recovery_payload["totalBytes"])
            and recovery_payload["totalBytes"] > 0
            and projection.get("mode") == "verified"
            and projection.get("verified") is True
            and projection.get("verification_scope") == "complete"
            and confirmation.get("confirmed") is True
            and workflow_confirmation.get("synthetic_quorum_confirmed")
            is True
            and workflow_confirmation.get("durable_core_observation_valid")
            is True
        )
        if recovery_eligible:
            compact_recovery_payload = json.dumps(
                recovery_payload,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            hourly_recovery_evidence = base64.b64encode(
                compact_recovery_payload
            ).decode("ascii")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"healthy={str(healthy).lower()}\n")
        output.write(f"confirmed={str(confirmed).lower()}\n")
        output.write(f"core_current={str(core_current).lower()}\n")
        output.write(f"report_valid={str(report_valid).lower()}\n")
        output.write(f"missing_output_count={len(missing)}\n")
        output.write(
            f"hourly_recovery_evidence={hourly_recovery_evidence}\n"
        )
        output.write(f"summary={summary}\n")
        output.flush()
        os.fsync(output.fileno())

    # Make the issue body visible before the effective report.  Reconciliation
    # separately requires this step's successful outcome, so outputs from a
    # failed final promotion cannot authorize an issue mutation.
    os.replace(body_tmp, body_path)
    os.replace(effective_report_tmp, effective_report_path)


def main() -> int:
    normalize_health_evidence()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
