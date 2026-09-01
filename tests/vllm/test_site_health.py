import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


def _response(body=b"", *, status=200, oversize=False, final_url=None):
    return {
        "http_status": status,
        "body": body,
        "oversize": oversize,
        "error": None,
        "final_url": final_url,
    }


class Fetcher:
    def __init__(self, publication=None, *, site=None, resources=None):
        default_site = (
            b"<!doctype html><title>vLLM AMD CI Operations</title>"
            b'<link rel="stylesheet" href="assets/css/dashboard.css?v=test">'
            b'<link rel="stylesheet" href="assets/css/ops-v2.css?v=test">'
            b'<section id="publication-status-banner"></section>'
            b'<script src="assets/js/utils.js?v=test"></script>'
            b'<script src="assets/js/publication-status.js?v=test"></script>'
            b'<script src="assets/js/dashboard-nav.js?v=test"></script>'
            b'<script src="assets/js/ops-v2.js?v=test"></script>'
            + b"x" * 800
        )
        self.site = site or _response(default_site)
        payload = _publication() if publication is None else publication
        self.publication = (
            payload
            if isinstance(payload, dict) and "http_status" in payload
            else _response(json.dumps(payload).encode())
        )
        asset_bodies = {
            path: (
                f"/* {path} */\n".encode()
                if path.endswith(".css")
                else f"window.asset = {path!r};\n".encode()
            )
            for path in health.CRITICAL_ASSET_PATHS
        }
        organization_path = "data/vllm/ci/org_summary.json"
        organization_body = b'{"schema_version":1}\n'
        section_paths = {
            name: f"data/vllm/ci/operations_v2/{name}.json"
            for name in health.OPERATIONS_CANARY_SECTIONS
        }
        section_bodies = {
            name: json.dumps({name: {"status": "healthy"}}).encode() + b"\n"
            for name in health.OPERATIONS_CANARY_SECTIONS
        }
        operations_payload = {
            "schema_version": 2,
            "bundle_version": 1,
            "generated_at": health._iso_utc(NOW - timedelta(minutes=30)),
            "monolith": None,
            "shell": {},
            "organization_summary": {
                "path": "org_summary.json",
                "bytes": len(organization_body),
                "schema_version": 1,
            },
            "sections": {
                name: {
                    "path": f"operations_v2/{name}.json",
                    "bytes": len(section_bodies[name]),
                }
                for name in health.OPERATIONS_CANARY_SECTIONS
            },
        }
        operations_body = json.dumps(operations_payload).encode()
        files = {
            "index.html": self.site.get("body", b""),
            health.PUBLICATION_STATUS_PATH: self.publication.get("body", b""),
            **asset_bodies,
            health.OPERATIONS_MANIFEST_PATH: operations_body,
            organization_path: organization_body,
            **{
                section_paths[name]: section_bodies[name]
                for name in health.OPERATIONS_CANARY_SECTIONS
            },
        }
        descriptors = {
            path: {
                "bytes": len(body),
                "mode": "100644",
                "sha256": hashlib.sha256(body).hexdigest(),
                "git_oid": "0" * 40,
            }
            for path, body in files.items()
        }
        manifest = {
            "schema_version": 1,
            "hash_algorithm": "sha256",
            "git_object_format": "sha1",
            "excluded_prefixes": ["pr-preview/"],
            "limits": {
                "max_blob_bytes": health.PROJECTION_MAX_BLOB_BYTES,
                "max_tree_bytes": health.PROJECTION_MAX_TREE_BYTES,
                "max_files": health.PROJECTION_MAX_FILES,
            },
            "file_count": len(descriptors),
            "total_bytes": sum(row["bytes"] for row in descriptors.values()),
            "files": descriptors,
        }
        manifest_raw = health._canonical_json(manifest)
        marker = {
            "schema_version": 2,
            "generation_id": "test-generation",
            "generated_at": _publication().get("generated_at"),
            "state_sha": "1" * 40,
            "state_tree": "2" * 40,
            "code_sha": "3" * 40,
            "public_projection": {
                "schema_version": 1,
                "manifest_path": health.PUBLICATION_MANIFEST_PATH,
                "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                "file_count": manifest["file_count"],
                "total_bytes": manifest["total_bytes"],
            },
        }
        publication_payload = payload if isinstance(payload, dict) else {}
        if "http_status" not in publication_payload:
            raw_generated_at = publication_payload.get("generated_at")
            if isinstance(raw_generated_at, str):
                try:
                    parsed_generated_at = datetime.fromisoformat(
                        raw_generated_at.replace("Z", "+00:00")
                    )
                except ValueError:
                    parsed_generated_at = None
                if parsed_generated_at is not None and parsed_generated_at.tzinfo is not None:
                    marker["generated_at"] = health._iso_utc(parsed_generated_at)
        marker["generated_at"] = health._iso_utc(
            datetime.fromisoformat(marker["generated_at"].replace("Z", "+00:00"))
        )
        self.responses = {
            health.PUBLICATION_GENERATION_PATH: _response(json.dumps(marker).encode()),
            health.PUBLICATION_MANIFEST_PATH: _response(manifest_raw),
            **{
                path: _response(body)
                for path, body in asset_bodies.items()
            },
            health.OPERATIONS_MANIFEST_PATH: _response(operations_body),
            **{
                section_paths[name]: _response(section_bodies[name])
                for name in health.OPERATIONS_CANARY_SECTIONS
            },
        }
        self.responses.update(resources or {})
        self.calls = []

    def __call__(self, url, max_bytes):
        self.calls.append((url, max_bytes))
        path = urlsplit(url).path
        if path.endswith(health.PUBLICATION_STATUS_PATH):
            return self.publication
        for relative, response in self.responses.items():
            if path.endswith(relative):
                return response
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
    assert report["projection"]["verified"] is True
    assert report["projection"]["operations_canaries"] == [
        {
            "name": name,
            "path": f"data/vllm/ci/operations_v2/{name}.json",
            "http_status": 200,
        }
        for name in health.OPERATIONS_CANARY_SECTIONS
    ]
    assert report["projection"]["verified_files"] == [
        "index.html",
        health.PUBLICATION_STATUS_PATH,
        *health.CRITICAL_ASSET_PATHS,
        health.OPERATIONS_MANIFEST_PATH,
        *[
            f"data/vllm/ci/operations_v2/{name}.json"
            for name in health.OPERATIONS_CANARY_SECTIONS
        ],
    ]
    assert [limit for _, limit in fetcher.calls] == [
        health.SITE_MAX_BYTES,
        health.STATUS_MAX_BYTES,
        health.MARKER_MAX_BYTES,
        health.MANIFEST_MAX_BYTES,
        *([health.ASSET_MAX_BYTES] * len(health.CRITICAL_ASSET_PATHS)),
        health.OPERATIONS_MANIFEST_MAX_BYTES,
        *(
            [health.OPERATIONS_CANARY_MAX_BYTES]
            * len(health.OPERATIONS_CANARY_SECTIONS)
        ),
    ]
    for url, _ in fetcher.calls:
        parsed = urlsplit(url)
        assert (parsed.scheme, parsed.netloc) == ("https", "example.test")
        assert parse_qs(parsed.query)["health_check"] == [str(int(NOW.timestamp()))]
    assert urlsplit(fetcher.calls[1][0]).path == (
        "/dashboard/data/vllm/ci/publication_status.json"
    )


def test_critical_asset_contract_covers_every_local_shell_dependency():
    shell = (Path(__file__).resolve().parents[2] / "docs/index.html").read_text()
    parser = health._ShellAssetParser()
    parser.feed(shell)
    parser.close()

    referenced = tuple(
        urlsplit(reference).path
        for reference in [*parser.stylesheets, *parser.scripts]
        if not urlsplit(reference).scheme and not urlsplit(reference).netloc
    )
    assert parser.malformed is False
    assert referenced == health.CRITICAL_ASSET_PATHS


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


def test_oversize_site_cannot_claim_exact_integrity():
    body = (
        b"<title>vLLM AMD CI Operations</title>"
        b'<section id="publication-status-banner">'
        + b"x" * health.SITE_MAX_BYTES
    )[: health.SITE_MAX_BYTES]
    report = health.check_site_health(
        now=NOW,
        fetch=Fetcher(site=_response(body, oversize=True)),
    )
    assert report["healthy"] is False
    assert "site-oversize" in _codes(report)


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
    assert _codes(report) == {
        "site-http",
        "publication-http",
        "generation-http",
        "manifest-http",
    }
    assert "secret-bearing" not in json.dumps(report)


def test_production_fetch_uses_one_timeout_and_reads_only_limit_plus_one(monkeypatch):
    observed = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, amount):
            observed["read"] = amount
            return b"x" * amount

        def getcode(self):
            return 200

        def geturl(self):
            return "https://example.test/resource"

    class Opener:
        def open(self, request, timeout):
            observed["url"] = request.full_url
            observed["timeout"] = timeout
            return Response()

    def build_opener(handler):
        assert isinstance(handler, health._NoRedirectHandler)
        return Opener()

    monkeypatch.setattr(health, "build_opener", build_opener)
    result = health.fetch_url("https://example.test/resource", 10)

    assert observed == {
        "url": "https://example.test/resource",
        "timeout": health.FETCH_TIMEOUT_SECONDS,
        "read": 11,
    }
    assert result["body"] == b"x" * 10
    assert result["oversize"] is True


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
    expected_report = health.confirm_site_health(
        clock=lambda: NOW, fetch=Fetcher(), sleep=lambda _seconds: None
    )
    monkeypatch.setattr(
        health,
        "confirm_site_health",
        lambda *args, **kwargs: expected_report,
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
    assert "confirmation_confirmed=true\n" in github_text
    assert "probe_attempts=3\n" in github_text


def test_cli_internal_failure_is_structured_and_exits_one(monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(
        health,
        "confirm_site_health",
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


class AttemptFetcher:
    def __init__(self, failing_attempts):
        self.base = Fetcher()
        self.failing_attempts = set(failing_attempts)
        self.tokens = []

    def __call__(self, url, max_bytes):
        token = parse_qs(urlsplit(url).query)["health_check"][0]
        self.tokens.append(token)
        attempt = int(token.split("-")[1])
        if attempt in self.failing_attempts and urlsplit(url).path.endswith("/dashboard/"):
            return _response(status=503)
        return self.base(url, max_bytes)


def test_confirmation_tolerates_one_transient_failure_with_fresh_cache_tokens():
    fetcher = AttemptFetcher({1})

    report = health.confirm_site_health(
        "https://example.test/dashboard/",
        clock=lambda: NOW,
        fetch=fetcher,
        sleep=lambda _seconds: None,
    )

    assert report["healthy"] is True
    assert report["overall_status"] == "healthy"
    assert report["reasons"] == []
    assert report["confirmation"]["healthy_count"] == 2
    assert report["confirmation"]["attempted"] == 3
    assert [probe["healthy"] for probe in report["confirmation"]["probes"]] == [
        False,
        True,
        True,
    ]
    assert {token.split("-")[1] for token in fetcher.tokens} == {"1", "2", "3"}
    assert len(set(fetcher.tokens)) == 3


def test_confirmation_reports_a_bounded_quorum_failure():
    report = health.confirm_site_health(
        "https://example.test/dashboard/",
        clock=lambda: NOW,
        fetch=AttemptFetcher({1, 2}),
        sleep=lambda _seconds: None,
    )

    assert report["healthy"] is False
    assert report["overall_status"] == "confirmed_unhealthy"
    assert report["confirmation"]["confirmed"] is True
    assert report["confirmation"]["healthy_count"] == 1
    assert report["confirmation"]["max_requests"] == 42
    assert report["confirmation"]["max_transport_seconds"] == 420
    assert report["confirmation"]["retry_delays_seconds"] == [0.0, 2.0, 5.0]
    assert report["confirmation"]["max_elapsed_seconds"] == 427
    assert "confirmation-quorum" in _codes(report)


def test_confirmed_publication_outage_keeps_nullable_diagnostics_empty():
    report = health.confirm_site_health(
        "https://example.test/dashboard/",
        clock=lambda: NOW,
        fetch=Fetcher(publication=_response(status=404)),
        sleep=lambda _seconds: None,
    )
    outputs = health.github_outputs(report)

    assert report["healthy"] is False
    assert report["overall_status"] == "confirmed_unhealthy"
    assert report["confirmation"]["confirmed"] is True
    assert report["confirmation"]["healthy_count"] == 0
    assert outputs["confirmation_confirmed"] is True
    assert outputs["publication_http"] == 404
    assert outputs["publication_mode"] is None
    assert outputs["publication_status"] is None
    assert outputs["generated_at"] is None
    assert outputs["age_hours"] is None


@pytest.mark.parametrize(
    ("path", "response", "expected"),
    [
        (health.PUBLICATION_GENERATION_PATH, _response(status=404), "generation-http"),
        (health.PUBLICATION_GENERATION_PATH, _response(b"{}"), "generation-contract"),
        (
            health.PUBLICATION_GENERATION_PATH,
            _response(b'{"schema_version":2,"schema_version":2}'),
            "generation-json",
        ),
        (
            health.PUBLICATION_GENERATION_PATH,
            _response(b"{}", oversize=True),
            "generation-oversize",
        ),
        (health.PUBLICATION_MANIFEST_PATH, _response(status=404), "manifest-http"),
        (health.PUBLICATION_MANIFEST_PATH, _response(b"not-json"), "manifest-json"),
        (
            health.PUBLICATION_MANIFEST_PATH,
            _response(b"{}", oversize=True),
            "manifest-oversize",
        ),
        (health.CRITICAL_ASSET_PATHS[0], _response(status=404), "asset-http"),
        (
            health.CRITICAL_ASSET_PATHS[0],
            _response(b"body{}", oversize=True),
            "asset-oversize",
        ),
        (
            health.CRITICAL_ASSET_PATHS[0],
            _response(
                b"body { color: black; }\n",
                final_url="https://evil.test/assets/css/dashboard.css",
            ),
            "asset-redirect",
        ),
        (health.CRITICAL_ASSET_PATHS[-1], _response(b"corrupt"), "projection-integrity"),
        (
            health.OPERATIONS_MANIFEST_PATH,
            _response(status=404),
            "operations-manifest-http",
        ),
        (
            health.OPERATIONS_MANIFEST_PATH,
            _response(b"corrupt"),
            "projection-integrity",
        ),
        *[
            (f"data/vllm/ci/operations_v2/{name}.json", response, expected)
            for name in health.OPERATIONS_CANARY_SECTIONS
            for response, expected in (
                (_response(status=404), "operations-canary-http"),
                (_response(b"corrupt"), "projection-integrity"),
            )
        ],
    ],
)
def test_missing_or_corrupt_projection_metadata_and_assets_fail(path, response, expected):
    report = health.check_site_health(
        now=NOW,
        fetch=Fetcher(resources={path: response}),
    )

    assert report["healthy"] is False
    assert expected in _codes(report)


@pytest.mark.parametrize("canary_name", health.OPERATIONS_CANARY_SECTIONS)
def test_operations_manifest_requires_every_default_route_canary(canary_name):
    fetcher = Fetcher()
    operations = json.loads(fetcher.responses[health.OPERATIONS_MANIFEST_PATH]["body"])
    projection = json.loads(
        fetcher.responses[health.PUBLICATION_MANIFEST_PATH]["body"]
    )
    descriptor = operations["sections"].pop(canary_name)
    projection["files"].pop(f"data/vllm/ci/{descriptor['path']}")

    with pytest.raises(
        health._ProjectionFailure,
        match="omitted required default-route canaries",
    ):
        health._normalize_operations_manifest(operations, projection)


def test_generation_attestation_must_match_exact_canonical_manifest_digest_and_totals():
    fetcher = Fetcher()
    marker_response = fetcher.responses[health.PUBLICATION_GENERATION_PATH]
    marker = json.loads(marker_response["body"])
    marker["public_projection"]["manifest_sha256"] = "f" * 64
    fetcher.responses[health.PUBLICATION_GENERATION_PATH] = _response(
        json.dumps(marker).encode()
    )

    report = health.check_site_health(now=NOW, fetch=fetcher)

    assert report["healthy"] is False
    assert "projection-attestation" in _codes(report)


def test_verified_projection_reports_the_exact_manifest_digest():
    fetcher = Fetcher()
    manifest_raw = fetcher.responses[health.PUBLICATION_MANIFEST_PATH]["body"]

    report = health.check_site_health(now=NOW, fetch=fetcher)

    assert report["healthy"] is True
    assert report["projection"]["manifest_sha256"] == hashlib.sha256(
        manifest_raw
    ).hexdigest()
    assert report["projection"]["file_count"] == (
        4
        + len(health.CRITICAL_ASSET_PATHS)
        + len(health.OPERATIONS_CANARY_SECTIONS)
    )
    assert report["projection"]["total_bytes"] > 0


def test_manifest_must_be_canonical_even_when_marker_attests_its_digest():
    fetcher = Fetcher()
    manifest = json.loads(fetcher.responses[health.PUBLICATION_MANIFEST_PATH]["body"])
    noncanonical = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    marker = json.loads(fetcher.responses[health.PUBLICATION_GENERATION_PATH]["body"])
    marker["public_projection"]["manifest_sha256"] = hashlib.sha256(
        noncanonical
    ).hexdigest()
    fetcher.responses[health.PUBLICATION_MANIFEST_PATH] = _response(noncanonical)
    fetcher.responses[health.PUBLICATION_GENERATION_PATH] = _response(
        json.dumps(marker).encode()
    )

    report = health.check_site_health(now=NOW, fetch=fetcher)

    assert report["healthy"] is False
    assert "manifest-canonical" in _codes(report)


def test_manifest_rejects_duplicate_keys_and_path_traversal():
    duplicate = Fetcher(
        resources={
            health.PUBLICATION_MANIFEST_PATH: _response(
                b'{"schema_version":1,"schema_version":1}'
            )
        }
    )
    assert "manifest-json" in _codes(
        health.check_site_health(now=NOW, fetch=duplicate)
    )

    traversing = Fetcher()
    manifest = json.loads(traversing.responses[health.PUBLICATION_MANIFEST_PATH]["body"])
    manifest["files"]["../escape.js"] = manifest["files"].pop(
        health.CRITICAL_ASSET_PATHS[-1]
    )
    traversing_raw = health._canonical_json(manifest)
    marker = json.loads(traversing.responses[health.PUBLICATION_GENERATION_PATH]["body"])
    marker["public_projection"]["manifest_sha256"] = hashlib.sha256(
        traversing_raw
    ).hexdigest()
    traversing.responses[health.PUBLICATION_MANIFEST_PATH] = _response(traversing_raw)
    traversing.responses[health.PUBLICATION_GENERATION_PATH] = _response(
        json.dumps(marker).encode()
    )
    assert "manifest-contract" in _codes(
        health.check_site_health(now=NOW, fetch=traversing)
    )


def test_critical_asset_reference_cannot_escape_the_site_origin():
    site = _response(
        b"<!doctype html><title>vLLM AMD CI Operations</title>"
        b'<link rel="stylesheet" href="https://evil.test/assets/css/dashboard.css">'
        b'<section id="publication-status-banner"></section>'
        b'<script src="assets/js/publication-status.js"></script>'
        + b"x" * 800
    )
    fetcher = Fetcher(site=site)

    report = health.check_site_health(
        "https://example.test/dashboard/", now=NOW, fetch=fetcher
    )

    assert report["healthy"] is False
    assert "site-assets" in _codes(report)
    assert all(urlsplit(url).netloc == "example.test" for url, _ in fetcher.calls)


def test_shell_rejects_base_url_and_duplicate_asset_attributes():
    for injected in (
        b'<base href="https://evil.test/">',
        b'<link rel="stylesheet" href="assets/css/dashboard.css" href="https://evil.test/x">',
    ):
        site = _response(
            b"<!doctype html><title>vLLM AMD CI Operations</title>"
            + injected
            + b'<link rel="stylesheet" href="assets/css/dashboard.css">'
            + b'<section id="publication-status-banner"></section>'
            + b'<script src="assets/js/publication-status.js"></script>'
            + b"x" * 800
        )
        report = health.check_site_health(
            "https://example.test/dashboard/",
            now=NOW,
            fetch=Fetcher(site=site),
        )
        assert report["healthy"] is False
        assert "site-assets" in _codes(report)


def test_bootstrap_policy_allows_only_two_definitive_metadata_404s():
    absent = {
        health.PUBLICATION_GENERATION_PATH: _response(status=404),
        health.PUBLICATION_MANIFEST_PATH: _response(status=404),
    }
    allowed = health.check_site_health(
        now=NOW,
        fetch=Fetcher(resources=absent),
        allow_legacy_metadata_absence=True,
    )
    strict = health.check_site_health(
        now=NOW,
        fetch=Fetcher(resources=absent),
        allow_legacy_metadata_absence=False,
    )
    mixed = health.check_site_health(
        now=NOW,
        fetch=Fetcher(
            resources={health.PUBLICATION_GENERATION_PATH: _response(status=404)}
        ),
        allow_legacy_metadata_absence=True,
    )

    assert allowed["healthy"] is True
    assert allowed["projection"]["mode"] == "legacy-bootstrap"
    assert strict["healthy"] is False
    assert {"generation-http", "manifest-http"} <= _codes(strict)
    assert mixed["healthy"] is False
    assert "generation-http" in _codes(mixed)


def test_legacy_guard_requires_deadline_and_fresh_two_slot_absence(tmp_path):
    policy = tmp_path / "dashboard_state.json"
    bootstrap = tmp_path / "dashboard_bootstrap.json"
    evidence = tmp_path / "bootstrap-ref-evidence.json"
    value = {
        "schema_version": 1,
        "branch": "dashboard-state",
        "previous_branch": "dashboard-state-previous",
        "manifest_path": "data/vllm/ci/dashboard_state.json",
        "generated_roots": ["data"],
        "limits": {
            "max_blob_bytes": 1,
            "max_tree_bytes": 1,
            "max_files": 1,
        },
        "bootstrap_allowed": True,
    }
    policy.write_text(json.dumps(value) + "\n")
    bootstrap.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bootstrap_deadline": health._iso_utc(NOW + timedelta(hours=1)),
            }
        )
        + "\n"
    )

    def write_evidence(*, current="absent", previous="absent", checked_at=NOW):
        def descriptor(branch, status, sha):
            return {
                "ref": f"refs/heads/{branch}",
                "status": status,
                "sha": sha,
            }

        payload = {
            "schema_version": 1,
            "provider": "github-rest-git-ref-v1",
            "repository": "owner/repo",
            "checked_at": health._iso_utc(checked_at),
            "refs": {
                "dashboard-state": descriptor(
                    "dashboard-state",
                    current,
                    "1" * 40 if current == "present" else None,
                ),
                "dashboard-state-previous": descriptor(
                    "dashboard-state-previous",
                    previous,
                    "2" * 40 if previous == "present" else None,
                ),
            },
        }
        evidence.write_bytes(health._canonical_json(payload))

    write_evidence()
    def guard():
        return health._legacy_bootstrap_allowed(
            policy,
            bootstrap_config_path=bootstrap,
            evidence_path=evidence,
            repository="owner/repo",
            now=NOW,
        )
    assert guard() is True

    # The first observed state closes legacy health even before the required
    # follow-up commit flips the migration boolean.
    write_evidence(current="present")
    assert guard() is False
    report = health.check_site_health(
        now=NOW,
        fetch=Fetcher(
            resources={
                health.PUBLICATION_GENERATION_PATH: _response(status=404),
                health.PUBLICATION_MANIFEST_PATH: _response(status=404),
            }
        ),
        allow_legacy_metadata_absence=guard(),
    )
    assert report["healthy"] is False
    assert {"generation-http", "manifest-http"} <= _codes(report)
    write_evidence()

    value["bootstrap_allowed"] = False
    policy.write_text(json.dumps(value) + "\n")
    assert guard() is False
    value["bootstrap_allowed"] = True
    policy.write_text(json.dumps(value) + "\n")

    bootstrap.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bootstrap_deadline": health._iso_utc(NOW),
            }
        )
        + "\n"
    )
    assert guard() is False
    bootstrap.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bootstrap_deadline": health._iso_utc(NOW + timedelta(hours=1)),
            }
        )
        + "\n"
    )
    write_evidence(checked_at=NOW - health.BOOTSTRAP_EVIDENCE_MAX_AGE - timedelta(seconds=1))
    assert guard() is False

    policy.write_text(
        '{"schema_version":1,"bootstrap_allowed":true,"bootstrap_allowed":true}\n'
    )
    assert guard() is False


def test_bootstrap_evidence_writer_observes_both_refs_but_locked_policy_denies(
    tmp_path, monkeypatch
):
    output = tmp_path / "bootstrap-ref-evidence.json"
    calls = []

    def observe(repository, branch, *, token):
        calls.append((repository, branch, token))
        return {
            "ref": f"refs/heads/{branch}",
            "status": "absent",
            "sha": None,
        }

    monkeypatch.setattr(health, "_github_ref_observation", observe)
    evidence = health.write_bootstrap_ref_evidence(
        output,
        "owner/repo",
        now=NOW,
        token="runner-token",
    )

    assert calls == [
        ("owner/repo", "dashboard-state", "runner-token"),
        ("owner/repo", "dashboard-state-previous", "runner-token"),
    ]
    assert output.read_bytes() == health._canonical_json(evidence)
    assert not health._legacy_bootstrap_allowed(
        evidence_path=output,
        repository="owner/repo",
        now=NOW,
    )


def test_bootstrap_evidence_writer_leaves_no_proof_on_ambiguous_observation(
    tmp_path, monkeypatch
):
    output = tmp_path / "bootstrap-ref-evidence.json"

    def observe(repository, branch, *, token):
        del repository, token
        if branch == "dashboard-state-previous":
            raise RuntimeError("ambiguous")
        return {
            "ref": f"refs/heads/{branch}",
            "status": "absent",
            "sha": None,
        }

    monkeypatch.setattr(health, "_github_ref_observation", observe)
    with pytest.raises(RuntimeError, match="ambiguous"):
        health.write_bootstrap_ref_evidence(
            output,
            "owner/repo",
            now=NOW,
            token="runner-token",
        )

    assert not output.exists()


def test_checked_in_bootstrap_policy_is_locked_and_deadline_remains_canonical():
    bootstrap = json.loads(health.DEFAULT_BOOTSTRAP_CONFIG.read_text())
    assert bootstrap == {
        "schema_version": 1,
        "bootstrap_deadline": "2026-09-02T00:00:00Z",
    }
    assert not health.bootstrap_policy_active(
        now=datetime(2026, 9, 1, 23, 59, 59, tzinfo=timezone.utc)
    )
    assert not health.bootstrap_policy_active(
        now=datetime(2026, 9, 2, tzinfo=timezone.utc)
    )
