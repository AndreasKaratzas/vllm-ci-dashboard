#!/usr/bin/env python3
"""Synthetic liveness check for the published dashboard and its data plane."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_SITE_URL = "https://andreaskaratzas.github.io/vllm-ci-dashboard/"
PUBLICATION_STATUS_PATH = "data/vllm/ci/publication_status.json"
DEFAULT_MAX_PUBLICATION_AGE_HOURS = 3.0
FETCH_TIMEOUT_SECONDS = 15
SITE_MIN_BYTES = 500
SITE_MAX_BYTES = 2 * 1024 * 1024
STATUS_MAX_BYTES = 64 * 1024
FUTURE_SKEW = timedelta(minutes=5)
SITE_REQUIRED_MARKERS = (
    b"<title>vLLM AMD CI Operations</title>",
    b'id="publication-status-banner"',
)
PUBLICATION_MODES = frozenset({"current", "degraded", "fallback", "mixed", "blocked"})
PUBLICATION_STATUSES = frozenset({"healthy", "degraded", "blocked"})
PUBLICATION_SURFACE_LABELS = frozenset({
    "Agent health",
    "CI core health",
    "CI gating",
    "CI health",
    "CI test changes",
    "CI workload hotness",
    "DNS health",
    "Performance evaluation",
    "Project activity",
    "Queue health",
    "Queue lifecycle",
})

Fetch = Callable[[str, int], dict[str, Any]]


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _normalize_site_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("site URL must be an absolute HTTP(S) URL without credentials")
    path = parsed.path or "/"
    if not path.endswith("/"):
        path += "/"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _cache_bust(url: str, now: datetime) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("health_check", str(int(now.timestamp()))))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _publication_url(site_url: str) -> str:
    target = urljoin(site_url, PUBLICATION_STATUS_PATH)
    site = urlsplit(site_url)
    parsed = urlsplit(target)
    if parsed.scheme != site.scheme or parsed.netloc != site.netloc:
        raise ValueError("publication status URL escaped the configured site origin")
    return target


def fetch_url(url: str, max_bytes: int) -> dict[str, Any]:
    """Fetch a bounded public resource and convert all transport errors to data."""
    request = Request(
        url,
        headers={
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
            "User-Agent": "vllm-ci-dashboard-site-health/1",
        },
    )
    try:
        with urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            body = response.read(max_bytes + 1)
            return {
                "http_status": int(response.getcode() or 0),
                "body": body[:max_bytes],
                "oversize": len(body) > max_bytes,
                "error": None,
            }
    except HTTPError as exc:
        return {
            "http_status": int(exc.code or 0),
            "body": b"",
            "oversize": False,
            "error": f"HTTP {int(exc.code or 0)}",
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {
            "http_status": 0,
            "body": b"",
            "oversize": False,
            "error": type(exc).__name__,
        }


def _reason(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def check_site_health(
    site_url: str = DEFAULT_SITE_URL,
    *,
    max_publication_age_hours: float = DEFAULT_MAX_PUBLICATION_AGE_HOURS,
    now: datetime | None = None,
    fetch: Fetch = fetch_url,
) -> dict[str, Any]:
    """Return a deterministic report for site-shell and publication liveness."""
    if not math.isfinite(max_publication_age_hours) or max_publication_age_hours <= 0:
        raise ValueError("max publication age must be a positive finite number")
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    checked_at = checked_at.astimezone(timezone.utc)
    base_url = _normalize_site_url(site_url)
    publication_url = _publication_url(base_url)
    reasons: list[dict[str, str]] = []

    site_response = fetch(_cache_bust(base_url, checked_at), SITE_MAX_BYTES)
    site_http = int(site_response.get("http_status") or 0)
    site_body = site_response.get("body")
    site_body = site_body if isinstance(site_body, bytes) else b""
    if site_http != 200:
        reasons.append(_reason("site-http", f"Dashboard root returned HTTP {site_http}."))
    elif len(site_body) < SITE_MIN_BYTES:
        reasons.append(
            _reason(
                "site-too-small",
                f"Dashboard root contained only {len(site_body)} bytes.",
            )
        )
    elif any(marker not in site_body for marker in SITE_REQUIRED_MARKERS):
        reasons.append(
            _reason(
                "site-shell-marker",
                "Dashboard root did not contain the expected application shell.",
            )
        )

    status_response = fetch(_cache_bust(publication_url, checked_at), STATUS_MAX_BYTES)
    publication_http = int(status_response.get("http_status") or 0)
    publication: dict[str, Any] = {
        "url": publication_url,
        "http_status": publication_http,
        "schema_version": None,
        "status": None,
        "mode": None,
        "generated_at": None,
        "degraded_since": None,
        "age_hours": None,
        "publication_blocked": None,
        "uses_fallback": None,
        "affected_surfaces": None,
        "affected_surface_count": None,
        "fallback_surface_count": None,
        "fresh_degraded_surface_count": None,
    }
    payload: object | None = None
    if publication_http != 200:
        reasons.append(
            _reason(
                "publication-http",
                f"Publication status returned HTTP {publication_http}.",
            )
        )
    elif status_response.get("oversize") is True:
        reasons.append(
            _reason("publication-oversize", "Publication status exceeded 64 KiB.")
        )
    else:
        body = status_response.get("body")
        try:
            if not isinstance(body, bytes):
                raise ValueError("response body was not bytes")
            payload = json.loads(
                body.decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_duplicate_keys,
            )
        except (UnicodeError, json.JSONDecodeError, ValueError):
            reasons.append(
                _reason("publication-json", "Publication status was not valid JSON.")
            )

    if payload is not None:
        if not isinstance(payload, dict):
            reasons.append(
                _reason("publication-shape", "Publication status must be a JSON object.")
            )
        else:
            schema_version = payload.get("schema_version")
            status = payload.get("status")
            mode = payload.get("mode")
            blocked = payload.get("publication_blocked")
            uses_fallback = payload.get("uses_fallback")
            generated_at = _parse_timestamp(payload.get("generated_at"))
            degraded_since_value = payload.get("degraded_since")
            degraded_since = (
                _parse_timestamp(degraded_since_value)
                if degraded_since_value is not None
                else None
            )
            affected_surfaces = payload.get("affected_surfaces")
            affected_surface_count = payload.get("affected_surface_count")
            fallback_surface_count = payload.get("fallback_surface_count")
            fresh_degraded_surface_count = payload.get(
                "fresh_degraded_surface_count"
            )
            publication.update(
                {
                    "schema_version": schema_version,
                    "status": status if isinstance(status, str) else None,
                    "mode": mode if isinstance(mode, str) else None,
                    "generated_at": _iso_utc(generated_at) if generated_at else None,
                    "degraded_since": (
                        _iso_utc(degraded_since) if degraded_since else None
                    ),
                    "publication_blocked": blocked if isinstance(blocked, bool) else None,
                    "uses_fallback": uses_fallback if isinstance(uses_fallback, bool) else None,
                    "affected_surfaces": (
                        affected_surfaces
                        if isinstance(affected_surfaces, list)
                        and all(isinstance(item, str) for item in affected_surfaces)
                        else None
                    ),
                    "affected_surface_count": (
                        affected_surface_count
                        if _is_nonnegative_int(affected_surface_count)
                        else None
                    ),
                    "fallback_surface_count": (
                        fallback_surface_count
                        if _is_nonnegative_int(fallback_surface_count)
                        else None
                    ),
                    "fresh_degraded_surface_count": (
                        fresh_degraded_surface_count
                        if _is_nonnegative_int(fresh_degraded_surface_count)
                        else None
                    ),
                }
            )
            expected_fields = {
                "schema_version",
                "status",
                "mode",
                "generated_at",
                "degraded_since",
                "uses_fallback",
                "publication_blocked",
                "affected_surfaces",
                "affected_surface_count",
                "fallback_surface_count",
                "fresh_degraded_surface_count",
            }
            missing_fields = sorted(expected_fields - payload.keys())
            if missing_fields:
                reasons.append(
                    _reason(
                        "publication-contract",
                        "Publication status omitted required version-1 fields.",
                    )
                )
            if type(schema_version) is not int or schema_version != 1:
                reasons.append(
                    _reason("publication-schema", "Publication status schema is not version 1.")
                )
            if status not in PUBLICATION_STATUSES:
                reasons.append(
                    _reason("publication-status", "Publication status value is unsupported.")
                )
            if mode not in PUBLICATION_MODES:
                reasons.append(
                    _reason("publication-mode", "Publication mode value is unsupported.")
                )
            if not isinstance(blocked, bool) or not isinstance(uses_fallback, bool):
                reasons.append(
                    _reason("publication-flags", "Publication status flags are malformed.")
                )
            surface_list_valid = (
                isinstance(affected_surfaces, list)
                and all(
                    isinstance(item, str) and item in PUBLICATION_SURFACE_LABELS
                    for item in affected_surfaces
                )
                and affected_surfaces == sorted(set(affected_surfaces))
            )
            counts_valid = all(
                _is_nonnegative_int(value)
                for value in (
                    affected_surface_count,
                    fallback_surface_count,
                    fresh_degraded_surface_count,
                )
            )
            if not surface_list_valid or not counts_valid:
                reasons.append(
                    _reason(
                        "publication-contract",
                        "Publication surface labels or counts are malformed.",
                    )
                )
            elif (
                affected_surface_count != len(affected_surfaces)
                or fallback_surface_count > affected_surface_count
                or fresh_degraded_surface_count > affected_surface_count
            ):
                reasons.append(
                    _reason(
                        "publication-consistency",
                        "Publication surface counts contradict the affected surfaces.",
                    )
                )
            elif mode in PUBLICATION_MODES and mode != "blocked":
                counts_match_mode = {
                    "current": (
                        affected_surface_count == 0
                        and fallback_surface_count == 0
                        and fresh_degraded_surface_count == 0
                    ),
                    "degraded": (
                        affected_surface_count > 0
                        and fallback_surface_count == 0
                        and fresh_degraded_surface_count == affected_surface_count
                    ),
                    "fallback": (
                        affected_surface_count > 0
                        and fallback_surface_count == affected_surface_count
                        and fresh_degraded_surface_count == 0
                    ),
                    "mixed": (
                        fallback_surface_count > 0
                        and fresh_degraded_surface_count > 0
                        and fallback_surface_count + fresh_degraded_surface_count
                        == affected_surface_count
                    ),
                }[mode]
                if not counts_match_mode:
                    reasons.append(
                        _reason(
                            "publication-consistency",
                            "Publication mode contradicts its surface counts.",
                        )
                    )
            if degraded_since_value is not None:
                if degraded_since is None:
                    reasons.append(
                        _reason(
                            "publication-degraded-timestamp",
                            "Publication degradation timestamp is invalid.",
                        )
                    )
                elif degraded_since > checked_at + FUTURE_SKEW:
                    reasons.append(
                        _reason(
                            "publication-degraded-future",
                            "Publication degradation timestamp is in the future.",
                        )
                    )
            if generated_at is None:
                reasons.append(
                    _reason("publication-timestamp", "Publication timestamp is missing or invalid.")
                )
            else:
                age = checked_at - generated_at
                publication["age_hours"] = round(age.total_seconds() / 3600, 3)
                if age < -FUTURE_SKEW:
                    reasons.append(
                        _reason("publication-future", "Publication timestamp is in the future.")
                    )
                elif age > timedelta(hours=max_publication_age_hours):
                    reasons.append(
                        _reason(
                            "publication-stale",
                            (
                                f"Publication is {age.total_seconds() / 3600:.1f} hours old; "
                                f"limit is {max_publication_age_hours:g} hours."
                            ),
                        )
                    )
            if mode in PUBLICATION_MODES and status in PUBLICATION_STATUSES:
                expected_uses_fallback = mode in {"fallback", "mixed"}
                expected_blocked = mode == "blocked"
                affected = (
                    affected_surface_count
                    if _is_nonnegative_int(affected_surface_count)
                    else 0
                )
                expected_status = (
                    "blocked"
                    if expected_blocked
                    else "degraded"
                    if mode != "current" or affected > 0
                    else "healthy"
                )
                if (
                    uses_fallback is not expected_uses_fallback
                    or blocked is not expected_blocked
                    or status != expected_status
                ):
                    reasons.append(
                        _reason(
                            "publication-consistency",
                            "Publication mode, status, and flags contradict each other.",
                        )
                    )
            if blocked is True or status == "blocked" or mode == "blocked":
                reasons.append(
                    _reason("publication-blocked", "Publication selection is blocked.")
                )

    healthy = not reasons
    return {
        "schema_version": 1,
        "checked_at": _iso_utc(checked_at),
        "healthy": healthy,
        "overall_status": "healthy" if healthy else "unhealthy",
        "max_publication_age_hours": max_publication_age_hours,
        "site": {
            "url": base_url,
            "http_status": site_http,
            "bytes_read": len(site_body),
        },
        "publication": publication,
        "reasons": reasons,
    }


def _write_text(path: str | None, text: str) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _append_text(path: str | None, text: str) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(text)


def _output_value(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    return str(value).replace("\r", " ").replace("\n", " ")


def github_outputs(report: dict[str, Any]) -> dict[str, object]:
    site = report.get("site") if isinstance(report.get("site"), dict) else {}
    publication = (
        report.get("publication") if isinstance(report.get("publication"), dict) else {}
    )
    reasons = report.get("reasons") if isinstance(report.get("reasons"), list) else []
    return {
        "healthy": report.get("healthy") is True,
        "overall_status": report.get("overall_status"),
        "site_http": site.get("http_status"),
        "site_bytes": site.get("bytes_read"),
        "publication_http": publication.get("http_status"),
        "publication_mode": publication.get("mode"),
        "publication_status": publication.get("status"),
        "generated_at": publication.get("generated_at"),
        "age_hours": publication.get("age_hours"),
        "reason_count": len(reasons),
    }


def markdown_report(report: dict[str, Any]) -> str:
    def safe(value: object) -> str:
        text = "unknown" if value in (None, "") else str(value)
        return text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")[:500]

    site = report.get("site") if isinstance(report.get("site"), dict) else {}
    publication = (
        report.get("publication") if isinstance(report.get("publication"), dict) else {}
    )
    lines = [
        "## Latest synthetic probe",
        "",
        "| Check | Value |",
        "| --- | --- |",
        f"| Healthy | {safe(report.get('healthy'))} |",
        f"| Checked at | {safe(report.get('checked_at'))} |",
        f"| Site HTTP | {safe(site.get('http_status'))} |",
        f"| Site bytes read | {safe(site.get('bytes_read'))} |",
        f"| Publication HTTP | {safe(publication.get('http_status'))} |",
        f"| Publication mode | {safe(publication.get('mode'))} |",
        f"| Publication status | {safe(publication.get('status'))} |",
        f"| Publication generated at | {safe(publication.get('generated_at'))} |",
        f"| Publication age (hours) | {safe(publication.get('age_hours'))} |",
        "",
        "### Findings",
        "",
    ]
    reasons = report.get("reasons") if isinstance(report.get("reasons"), list) else []
    if reasons:
        for reason in reasons:
            if isinstance(reason, dict):
                lines.append(
                    f"- `{safe(reason.get('code'))}` — {safe(reason.get('message'))}"
                )
    else:
        lines.append("- No liveness findings.")
    return "\n".join(lines) + "\n"


def _internal_error_report(site_url: str) -> dict[str, Any]:
    try:
        safe_site_url: str | None = _normalize_site_url(site_url)
    except ValueError:
        safe_site_url = None
    return {
        "schema_version": 1,
        "checked_at": _iso_utc(datetime.now(timezone.utc)),
        "healthy": False,
        "overall_status": "checker_internal_error",
        "max_publication_age_hours": None,
        "site": {"url": safe_site_url, "http_status": 0, "bytes_read": 0},
        "publication": {
            "url": None,
            "http_status": 0,
            "schema_version": None,
            "status": None,
            "mode": None,
            "generated_at": None,
            "degraded_since": None,
            "age_hours": None,
            "publication_blocked": None,
            "uses_fallback": None,
            "affected_surfaces": None,
            "affected_surface_count": None,
            "fallback_surface_count": None,
            "fresh_degraded_surface_count": None,
        },
        "reasons": [
            _reason("checker-internal", "The synthetic checker could not complete safely.")
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    parser.add_argument(
        "--max-publication-age-hours",
        type=float,
        default=DEFAULT_MAX_PUBLICATION_AGE_HOURS,
    )
    parser.add_argument("--output")
    parser.add_argument("--github-output")
    parser.add_argument("--markdown-output")
    args = parser.parse_args(argv)

    try:
        report = check_site_health(
            args.site_url,
            max_publication_age_hours=args.max_publication_age_hours,
        )
    except Exception:
        report = _internal_error_report(args.site_url)

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    _write_text(args.output, encoded)
    _write_text(args.markdown_output, markdown_report(report))
    if args.github_output:
        outputs = github_outputs(report)
        _append_text(
            args.github_output,
            "".join(f"{key}={_output_value(value)}\n" for key, value in outputs.items()),
        )
    if not args.output:
        sys.stdout.write(encoded)
    return 0 if report.get("healthy") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
