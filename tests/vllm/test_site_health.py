import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

import build_site
from vllm import check_site_health as health


NOW = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)


def _publication(**overrides):
    payload = {
        "schema_version": 1,
        "status": "healthy",
        "mode": "current",
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


def _response(body=b"", *, status=200, oversize=False):
    return {
        "http_status": status,
        "body": body,
        "oversize": oversize,
        "error": None,
    }


class Fetcher:
    def __init__(self, publication=None, *, site=None):
        default_site = (
            b"<title>vLLM AMD CI Operations</title>"
            b'<section id="publication-status-banner">'
            + b"x" * 800
        )
        self.site = site or _response(default_site)
        payload = _publication() if publication is None else publication
        self.publication = (
            payload
            if isinstance(payload, dict) and "http_status" in payload
            else _response(json.dumps(payload).encode())
        )
        self.calls = []

    def __call__(self, url, max_bytes):
        self.calls.append((url, max_bytes))
        if urlsplit(url).path.endswith("publication_status.json"):
            return self.publication
        return self.site


def _codes(report):
    return {row["code"] for row in report["reasons"]}


def test_healthy_probe_is_cache_busted_and_stays_on_site_origin():
    fetcher = Fetcher()

    report = health.check_site_health(
        "https://example.test/dashboard",
        now=NOW,
        fetch=fetcher,
    )

    assert report["healthy"] is True
    assert report["overall_status"] == "healthy"
    assert report["publication"]["age_hours"] == 0.5
    assert [limit for _, limit in fetcher.calls] == [
        health.SITE_MAX_BYTES,
        health.STATUS_MAX_BYTES,
    ]
    for url, _ in fetcher.calls:
        parsed = urlsplit(url)
        assert (parsed.scheme, parsed.netloc) == ("https", "example.test")
        assert parse_qs(parsed.query)["health_check"] == [str(int(NOW.timestamp()))]
    assert urlsplit(fetcher.calls[1][0]).path == (
        "/dashboard/data/vllm/ci/publication_status.json"
    )


def test_checker_contract_tracks_the_public_status_projector():
    assert health.PUBLICATION_MODES == build_site.PUBLICATION_MODES
    assert health.PUBLICATION_SURFACE_LABELS == frozenset(
        build_site.PUBLICATION_SURFACE_LABELS.values()
    )
    states = {
        "current": {},
        "degraded": {
            "degraded_surfaces": ["ci_core"],
            "fresh_degraded_surfaces": ["ci_core"],
        },
        "fallback": {
            "degraded_surfaces": ["ci_core"],
            "fallback_surfaces": ["ci_core"],
        },
        "mixed": {
            "degraded_surfaces": ["ci_core", "ci_gating"],
            "fresh_degraded_surfaces": ["ci_gating"],
            "fallback_surfaces": ["ci_core"],
        },
        "blocked": {},
    }
    for mode, fields in states.items():
        payload = build_site.project_publication_status({
            "mode": mode,
            "generated_at": (NOW - timedelta(minutes=30)).isoformat(),
            "degraded_since": {
                surface: (NOW - timedelta(hours=1)).isoformat()
                for surface in fields.get("degraded_surfaces", [])
            },
            **fields,
        })

        report = health.check_site_health(now=NOW, fetch=Fetcher(payload))

        assert report["healthy"] is (mode != "blocked"), (mode, report["reasons"])


@pytest.mark.parametrize(
    ("mode", "status", "uses_fallback"),
    [
        ("degraded", "degraded", False),
        ("fallback", "degraded", True),
        ("mixed", "degraded", True),
    ],
)
def test_visible_degradation_is_live_not_a_synthetic_outage(
    mode, status, uses_fallback
):
    surface_fields = {
        "affected_surfaces": ["CI core health"],
        "affected_surface_count": 1,
        "fallback_surface_count": int(mode in {"fallback", "mixed"}),
        "fresh_degraded_surface_count": int(mode in {"degraded", "mixed"}),
        "degraded_since": (NOW - timedelta(hours=1)).isoformat(),
    }
    if mode == "mixed":
        surface_fields.update({
            "affected_surfaces": ["CI core health", "CI gating"],
            "affected_surface_count": 2,
        })
    report = health.check_site_health(
        now=NOW,
        fetch=Fetcher(
            _publication(
                mode=mode,
                status=status,
                uses_fallback=uses_fallback,
                **surface_fields,
            )
        ),
    )

    assert report["healthy"] is True
    assert report["publication"]["mode"] == mode
    assert report["publication"]["status"] == status


@pytest.mark.parametrize(
    "blocked_fields",
    [
        {"mode": "blocked", "status": "blocked", "publication_blocked": True},
        {"mode": "current", "status": "blocked"},
        {"mode": "current", "status": "healthy", "publication_blocked": True},
    ],
)
def test_any_blocked_signal_is_unhealthy(blocked_fields):
    report = health.check_site_health(
        now=NOW,
        fetch=Fetcher(_publication(**blocked_fields)),
    )

    assert report["healthy"] is False
    assert report["overall_status"] == "unhealthy"
    assert "publication-blocked" in _codes(report)


def test_stale_publication_fails_even_when_root_is_http_200():
    report = health.check_site_health(
        now=NOW,
        max_publication_age_hours=3,
        fetch=Fetcher(_publication(generated_at=(NOW - timedelta(hours=4)).isoformat())),
    )

    assert report["site"]["http_status"] == 200
    assert report["healthy"] is False
    assert _codes(report) == {"publication-stale"}


def test_small_future_skew_is_allowed_but_larger_skew_is_rejected():
    allowed = health.check_site_health(
        now=NOW,
        fetch=Fetcher(_publication(generated_at=(NOW + timedelta(minutes=5)).isoformat())),
    )
    rejected = health.check_site_health(
        now=NOW,
        fetch=Fetcher(
            _publication(generated_at=(NOW + timedelta(minutes=5, seconds=1)).isoformat())
        ),
    )

    assert allowed["healthy"] is True
    assert rejected["healthy"] is False
    assert "publication-future" in _codes(rejected)


@pytest.mark.parametrize(
    ("site", "expected"),
    [
        (_response(status=503), "site-http"),
        (_response(b"tiny"), "site-too-small"),
    ],
)
def test_site_shell_failures_are_reported(site, expected):
    report = health.check_site_health(now=NOW, fetch=Fetcher(site=site))
    assert report["healthy"] is False
    assert expected in _codes(report)


def test_large_site_is_read_bounded_but_not_declared_down():
    body = (
        b"<title>vLLM AMD CI Operations</title>"
        b'<section id="publication-status-banner">'
        + b"x" * health.SITE_MAX_BYTES
    )[: health.SITE_MAX_BYTES]
    report = health.check_site_health(
        now=NOW,
        fetch=Fetcher(site=_response(body, oversize=True)),
    )
    assert report["healthy"] is True


def test_unrelated_http_200_page_does_not_pass_the_shell_probe():
    report = health.check_site_health(
        now=NOW,
        fetch=Fetcher(site=_response(b"x" * 800)),
    )

    assert report["healthy"] is False
    assert "site-shell-marker" in _codes(report)


@pytest.mark.parametrize(
    ("publication_response", "expected"),
    [
        (_response(status=404), "publication-http"),
        (_response(b"{}", oversize=True), "publication-oversize"),
        (_response(b"not-json"), "publication-json"),
        (_response(b"[]"), "publication-shape"),
        (_response(b'{"schema_version":1,"schema_version":1}'), "publication-json"),
    ],
)
def test_missing_oversize_or_malformed_publication_status_is_unhealthy(
    publication_response, expected
):
    report = health.check_site_health(
        now=NOW,
        fetch=Fetcher(publication_response),
    )
    assert report["healthy"] is False
    assert expected in _codes(report)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"schema_version": 2}, "publication-schema"),
        ({"schema_version": True}, "publication-schema"),
        ({"status": "unknown"}, "publication-status"),
        ({"mode": "unknown"}, "publication-mode"),
        ({"publication_blocked": "false"}, "publication-flags"),
        ({"uses_fallback": 0}, "publication-flags"),
        ({"generated_at": "not-a-time"}, "publication-timestamp"),
        ({"generated_at": "2026-08-17T11:00:00"}, "publication-timestamp"),
    ],
)
def test_publication_contract_errors_fail_closed(overrides, expected):
    report = health.check_site_health(
        now=NOW,
        fetch=Fetcher(_publication(**overrides)),
    )
    assert report["healthy"] is False
    assert expected in _codes(report)


@pytest.mark.parametrize(
    "overrides",
    [
        {"mode": "fallback", "status": "healthy", "uses_fallback": False},
        {"mode": "current", "status": "degraded"},
        {"mode": "blocked", "status": "degraded", "publication_blocked": False},
        {"affected_surface_count": 1},
        {"affected_surfaces": ["Not a public surface"], "affected_surface_count": 1},
        {"affected_surfaces": ["Queue health", "Queue health"], "affected_surface_count": 2},
        {"affected_surface_count": True},
        {"fallback_surface_count": 1},
        {"fresh_degraded_surface_count": 1},
        {
            "mode": "degraded",
            "status": "degraded",
            "affected_surfaces": ["CI core health"],
            "affected_surface_count": 1,
            "fresh_degraded_surface_count": 0,
        },
        {
            "mode": "fallback",
            "status": "degraded",
            "uses_fallback": True,
            "affected_surfaces": ["CI core health"],
            "affected_surface_count": 1,
            "fallback_surface_count": 0,
        },
    ],
)
def test_contradictory_or_malformed_publication_contract_is_unhealthy(overrides):
    report = health.check_site_health(
        now=NOW,
        fetch=Fetcher(_publication(**overrides)),
    )

    assert report["healthy"] is False
    assert _codes(report) & {"publication-contract", "publication-consistency"}


def test_missing_publication_contract_field_is_unhealthy():
    payload = _publication()
    payload.pop("affected_surface_count")

    report = health.check_site_health(now=NOW, fetch=Fetcher(payload))

    assert report["healthy"] is False
    assert "publication-contract" in _codes(report)


@pytest.mark.parametrize(
    ("degraded_since", "expected"),
    [
        ("not-a-time", "publication-degraded-timestamp"),
        ((NOW + timedelta(minutes=6)).isoformat(), "publication-degraded-future"),
    ],
)
def test_invalid_degradation_timestamp_is_unhealthy(degraded_since, expected):
    report = health.check_site_health(
        now=NOW,
        fetch=Fetcher(_publication(degraded_since=degraded_since)),
    )

    assert report["healthy"] is False
    assert expected in _codes(report)


def test_transport_error_is_structured_without_remote_exception_text():
    def failed_fetch(url, max_bytes):
        del url, max_bytes
        return {
            "http_status": 0,
            "body": b"",
            "oversize": False,
            "error": "secret-bearing exception",
        }

    report = health.check_site_health(now=NOW, fetch=failed_fetch)

    assert report["healthy"] is False
    assert _codes(report) == {"site-http", "publication-http"}
    assert "secret-bearing" not in json.dumps(report)


def test_invalid_site_url_and_age_are_rejected_before_fetch():
    with pytest.raises(ValueError, match="without credentials"):
        health.check_site_health("https://user:secret@example.test/", now=NOW)
    with pytest.raises(ValueError, match="positive finite"):
        health.check_site_health(now=NOW, max_publication_age_hours=float("nan"))
    with pytest.raises(ValueError, match="timezone-aware"):
        health.check_site_health(now=NOW.replace(tzinfo=None))


def test_markdown_and_github_outputs_are_bounded_and_escaped():
    report = health.check_site_health(
        now=NOW,
        fetch=Fetcher(_publication(generated_at=(NOW - timedelta(hours=4)).isoformat())),
    )
    report["reasons"].append({"code": "unsafe|code", "message": "line 1\nline | 2"})

    markdown = health.markdown_report(report)
    outputs = health.github_outputs(report)

    assert "unsafe\\|code" in markdown
    assert "line 1 line \\| 2" in markdown
    assert outputs["healthy"] is False
    assert outputs["overall_status"] == "unhealthy"
    assert outputs["reason_count"] == 2
    assert outputs["publication_http"] == 200


def test_cli_writes_report_markdown_and_appends_github_outputs(monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    output_path = tmp_path / "github-output"
    output_path.write_text("prior=value\n")
    check_site_health = health.check_site_health
    monkeypatch.setattr(
        health,
        "check_site_health",
        lambda *args, **kwargs: check_site_health(
            now=NOW, fetch=Fetcher(), **{k: v for k, v in kwargs.items() if k != "now"}
        ),
    )

    result = health.main(
        [
            "--output",
            str(report_path),
            "--markdown-output",
            str(markdown_path),
            "--github-output",
            str(output_path),
        ]
    )

    assert result == 0
    assert json.loads(report_path.read_text())["healthy"] is True
    assert "Latest synthetic probe" in markdown_path.read_text()
    github_text = output_path.read_text()
    assert github_text.startswith("prior=value\n")
    assert "healthy=true\n" in github_text


def test_cli_internal_failure_is_structured_and_exits_one(monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(
        health,
        "check_site_health",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret")),
    )

    result = health.main(["--output", str(report_path)])
    payload = json.loads(report_path.read_text())

    assert result == 1
    assert payload["healthy"] is False
    assert payload["overall_status"] == "checker_internal_error"
    assert payload["reasons"] == [
        {
            "code": "checker-internal",
            "message": "The synthetic checker could not complete safely.",
        }
    ]
    assert "secret" not in report_path.read_text()


def test_cli_internal_report_does_not_echo_credentials(tmp_path):
    report_path = tmp_path / "report.json"

    result = health.main(
        [
            "--site-url",
            "https://user:super-secret@example.test/",
            "--output",
            str(report_path),
        ]
    )

    payload = json.loads(report_path.read_text())
    assert result == 1
    assert payload["site"]["url"] is None
    assert "super-secret" not in report_path.read_text()
