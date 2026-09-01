# cspell:ignore hdrs

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

import build_site
from vllm import check_site_health as health
from vllm import operations_bundle_contract as bundle_contract


NOW = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)


def test_projection_health_proof_uses_exact_publication_ceiling() -> None:
    assert health.PROJECTION_MAX_BLOB_BYTES == 85 * 1024 * 1024
    assert health.PROJECTION_MAX_TREE_BYTES == 256 * 1024 * 1024
    assert health.PROJECTION_MAX_FILES == 10_000


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
            for name in bundle_contract.OPERATIONS_SECTION_NAMES
        }
        section_bodies = {
            name: json.dumps({name: {"status": "healthy"}}).encode() + b"\n"
            for name in bundle_contract.OPERATIONS_SECTION_NAMES
        }
        operations_payload = {
            "schema_version": 2,
            "bundle_version": bundle_contract.OPERATIONS_BUNDLE_VERSION,
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
                for name in bundle_contract.OPERATIONS_SECTION_NAMES
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
                for name in bundle_contract.OPERATIONS_SECTION_NAMES
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
                for name in bundle_contract.OPERATIONS_SECTION_NAMES
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


def _rebind_manifest(fetcher: Fetcher, manifest: dict[str, object]) -> bytes:
    raw = health._canonical_json(manifest)
    fetcher.responses[health.PUBLICATION_MANIFEST_PATH] = _response(raw)
    marker = json.loads(fetcher.responses[health.PUBLICATION_GENERATION_PATH]["body"])
    marker["public_projection"].update(
        {
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "file_count": manifest["file_count"],
            "total_bytes": manifest["total_bytes"],
        }
    )
    fetcher.responses[health.PUBLICATION_GENERATION_PATH] = _response(
        json.dumps(marker).encode()
    )
    return raw


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
    assert report["projection"]["operations_streamed_sections"] == [
        {
            "name": name,
            "path": f"data/vllm/ci/operations_v2/{name}.json",
            "http_status": 200,
            "bytes_read": len(
                json.dumps({name: {"status": "healthy"}}).encode() + b"\n"
            ),
            "sha256": hashlib.sha256(
                json.dumps({name: {"status": "healthy"}}).encode() + b"\n"
            ).hexdigest(),
            "verified": True,
        }
        for name in health.OPERATIONS_STREAMED_LARGE_SECTIONS
    ]
    assert report["projection"]["verification_scope"] == "complete"
    assert report["projection"]["manifest_policy"] == "current"
    assert report["projection"]["enforced_limits"] == {
        "max_blob_bytes": health.PROJECTION_MAX_BLOB_BYTES,
        "max_tree_bytes": health.PROJECTION_MAX_TREE_BYTES,
        "max_files": health.PROJECTION_MAX_FILES,
    }
    assert report["projection"]["verified_files"] == [
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
    assert [limit for _, limit in fetcher.calls] == [
        health.SITE_MAX_BYTES,
        health.STATUS_MAX_BYTES,
        health.MARKER_MAX_BYTES,
        health.MANIFEST_MAX_BYTES,
        *([health.ASSET_MAX_BYTES] * len(health.CRITICAL_ASSET_PATHS)),
        health.OPERATIONS_MANIFEST_MAX_BYTES,
        *[
            len(
                json.dumps({name: {"status": "healthy"}}).encode()
                + b"\n"
            )
            for name in health.OPERATIONS_CANARY_SECTIONS
        ],
        *[
            len(json.dumps({name: {"status": "healthy"}}).encode() + b"\n")
            for name in health.OPERATIONS_STREAMED_LARGE_SECTIONS
        ],
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
    assert health.OPERATIONS_CANARY_SECTIONS == (
        bundle_contract.OPERATIONS_CANARY_SECTIONS
    )
    assert health.OPERATIONS_CANARY_MAX_BYTES == (
        bundle_contract.OPERATIONS_CANARY_FILE_MAX_BYTES
    )
    assert health.OPERATIONS_STREAMED_LARGE_SECTIONS == (
        bundle_contract.OPERATIONS_STREAMED_LARGE_SECTIONS
    )
    assert health.OPERATIONS_STREAMED_MAX_BYTES == (
        bundle_contract.OPERATIONS_STREAMED_FILE_MAX_BYTES
    )
    operations_manifest = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "data/vllm/ci/operations_v2_manifest.json"
        ).read_text()
    )
    assert tuple(operations_manifest["sections"]) == (
        bundle_contract.OPERATIONS_SECTION_NAMES
    )
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
        (_response(b'{"schema_version":NaN}'), "publication-json"),
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


def test_production_fetch_uses_bounded_timeout_and_reads_only_limit_plus_one(monkeypatch):
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


def test_production_fetch_whole_attempt_deadline_interrupts_slow_trickle(monkeypatch):
    opens = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _amount):
            # Model HTTPResponse.read(amount) receiving one byte often enough
            # that its per-recv socket timeout never fires, without returning
            # control to the caller.
            while True:
                time.sleep(0.005)

    class Opener:
        def open(self, request, timeout):
            opens.append((request.full_url, timeout))
            return Response()

    monkeypatch.setattr(health, "build_opener", lambda _handler: Opener())
    monkeypatch.setattr(health, "FETCH_TIMEOUT_SECONDS", 0.03)
    started = time.monotonic()

    result = health.fetch_url("https://example.test/resource", 10)

    assert time.monotonic() - started < 1.0
    assert len(opens) == health.FETCH_ATTEMPTS == 2
    assert all(timeout == 0.03 for _url, timeout in opens)
    assert result == {
        "http_status": 0,
        "body": b"",
        "oversize": False,
        "error": "TimeoutError",
        "final_url": None,
    }


@pytest.mark.parametrize(
    "first_failure",
    [
        URLError("transient transport failure"),
        HTTPError(
            "https://example.test/resource",
            503,
            "transient upstream failure",
            hdrs=None,
            fp=None,
        ),
    ],
)
def test_production_fetch_retries_one_transient_failure(monkeypatch, first_failure):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, amount):
            assert amount == 11
            return b"recovered"

        def getcode(self):
            return 200

        def geturl(self):
            return "https://example.test/resource"

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            if len(calls) == 1:
                raise first_failure
            return Response()

    monkeypatch.setattr(
        health,
        "build_opener",
        lambda handler: (
            Opener()
            if isinstance(handler, health._NoRedirectHandler)
            else pytest.fail("redirect guard was not installed")
        ),
    )

    result = health.fetch_url("https://example.test/resource", 10)

    assert calls == [
        ("https://example.test/resource", health.FETCH_TIMEOUT_SECONDS),
        ("https://example.test/resource", health.FETCH_TIMEOUT_SECONDS),
    ]
    assert result == {
        "http_status": 200,
        "body": b"recovered",
        "oversize": False,
        "error": None,
        "final_url": "https://example.test/resource",
    }


def test_production_fetch_exhausts_exact_transient_attempt_bound(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            raise URLError("secret-bearing transient failure")

    monkeypatch.setattr(health, "build_opener", lambda _handler: Opener())

    result = health.fetch_url("https://example.test/resource", 10)

    assert len(calls) == health.FETCH_ATTEMPTS == 2
    assert result == {
        "http_status": 0,
        "body": b"",
        "oversize": False,
        "error": "URLError",
        "final_url": None,
    }
    assert "secret-bearing" not in repr(result)


def test_production_fetch_does_not_retry_definitive_http_failure(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            raise HTTPError(request.full_url, 404, "missing", hdrs=None, fp=None)

    monkeypatch.setattr(health, "build_opener", lambda _handler: Opener())

    result = health.fetch_url("https://example.test/resource", 10)

    assert len(calls) == 1
    assert result == {
        "http_status": 404,
        "body": b"",
        "oversize": False,
        "error": "HTTP 404",
        "final_url": "https://example.test/resource",
    }


def test_streamed_fetch_hashes_in_bounded_chunks_without_retaining_the_body(monkeypatch):
    payload = b"a" * (2 * 1024 * 1024 + 17)
    reads = []

    class Response:
        def __init__(self):
            self.offset = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, amount):
            reads.append(amount)
            chunk = payload[self.offset : self.offset + amount]
            self.offset += len(chunk)
            return chunk

        def getcode(self):
            return 200

        def geturl(self):
            return "https://example.test/reliability.json"

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://example.test/reliability.json"
            assert timeout == health.FETCH_TIMEOUT_SECONDS
            return Response()

    monkeypatch.setattr(health, "build_opener", lambda _handler: Opener())
    monkeypatch.setattr(health.time, "monotonic", lambda: 0.0)

    result = health.fetch_url_digest(
        "https://example.test/reliability.json", len(payload)
    )

    assert result["bytes_read"] == len(payload)
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["oversize"] is False
    assert max(reads) <= 1024 * 1024


def test_streamed_fetch_total_deadline_is_enforced_across_slow_chunks(monkeypatch):
    elapsed = {"seconds": 0.0}
    opens = []
    read_starts = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _amount):
            read_starts.append(elapsed["seconds"])
            elapsed["seconds"] += 31.0
            return b"x"

        def getcode(self):
            return 200

        def geturl(self):
            return "https://example.test/reliability.json"

    class Opener:
        def open(self, request, timeout):
            opens.append((request.full_url, timeout))
            return Response()

    monkeypatch.setattr(health, "build_opener", lambda _handler: Opener())
    monkeypatch.setattr(health.time, "monotonic", lambda: elapsed["seconds"])

    monkeypatch.setattr(health, "STREAM_TOTAL_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(health, "STREAM_ATTEMPT_MAX_SECONDS", 70)

    result = health.fetch_url_digest(
        "https://example.test/reliability.json", 1024
    )

    assert len(opens) == health.FETCH_ATTEMPTS == 2
    assert result["http_status"] == 0
    assert result["error"] == "TimeoutError"
    assert result["bytes_read"] == 0
    # In both attempts, the second chunk begins before the 60-second deadline
    # and finishes after it. The mandatory post-read check catches that overrun.
    assert read_starts == [0.0, 31.0, 62.0, 93.0]
    assert elapsed["seconds"] == 124.0


def test_streamed_fetch_whole_attempt_deadline_interrupts_one_slow_read(monkeypatch):
    opens = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _amount):
            # A buffered read can internally consume endless sub-timeout bytes.
            # The process timer must interrupt it even though this method never
            # returns for the ordinary monotonic checks around the read.
            while True:
                time.sleep(0.005)

    class Opener:
        def open(self, request, timeout):
            opens.append((request.full_url, timeout))
            return Response()

    monkeypatch.setattr(health, "build_opener", lambda _handler: Opener())
    monkeypatch.setattr(health, "FETCH_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(health, "STREAM_TOTAL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(health, "STREAM_ATTEMPT_MAX_SECONDS", 0.08)
    started = time.monotonic()

    result = health.fetch_url_digest(
        "https://example.test/reliability.json", 1024
    )

    assert time.monotonic() - started < 1.0
    assert len(opens) == health.FETCH_ATTEMPTS == 2
    assert all(timeout == 0.03 for _url, timeout in opens)
    assert result == {
        "http_status": 0,
        "bytes_read": 0,
        "sha256": None,
        "oversize": False,
        "error": "TimeoutError",
        "final_url": None,
    }


def test_streamed_fetch_uses_one_transient_retry_then_succeeds(monkeypatch):
    payload = b"reliability-proof\n"
    opens = []

    class Response:
        def __init__(self):
            self.sent = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _amount):
            if self.sent:
                return b""
            self.sent = True
            return payload

        def getcode(self):
            return 200

        def geturl(self):
            return "https://example.test/reliability.json"

    class Opener:
        def open(self, request, timeout):
            opens.append((request.full_url, timeout))
            if len(opens) == 1:
                raise URLError("transient")
            return Response()

    monkeypatch.setattr(health, "build_opener", lambda _handler: Opener())
    monkeypatch.setattr(health.time, "monotonic", lambda: 0.0)

    result = health.fetch_url_digest(
        "https://example.test/reliability.json", len(payload)
    )

    assert len(opens) == health.FETCH_ATTEMPTS == 2
    assert result["http_status"] == 200
    assert result["bytes_read"] == len(payload)
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()


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


class GenerationAttemptFetcher:
    def __init__(self, generations):
        self.generations = list(generations)
        self.fetchers = {}
        for index, generation in enumerate(dict.fromkeys(self.generations), start=1):
            fetcher = Fetcher()
            marker_response = fetcher.responses[health.PUBLICATION_GENERATION_PATH]
            marker = json.loads(marker_response["body"])
            marker["generation_id"] = f"generation-{generation}"
            marker["state_sha"] = f"{index:x}" * 40
            marker["state_tree"] = f"{index + 4:x}" * 40
            marker_response["body"] = json.dumps(marker).encode()
            self.fetchers[generation] = fetcher

    def __call__(self, url, max_bytes):
        token = parse_qs(urlsplit(url).query)["health_check"][0]
        attempt = int(token.split("-")[1])
        generation = self.generations[attempt - 1]
        return self.fetchers[generation](url, max_bytes)

    def reliability_call_count(self):
        reliability_path = "data/vllm/ci/operations_v2/reliability.json"
        return sum(
            urlsplit(url).path.endswith(reliability_path)
            for fetcher in self.fetchers.values()
            for url, _limit in fetcher.calls
        )

    def canary_call_count(self, name):
        canary_path = f"data/vllm/ci/operations_v2/{name}.json"
        return sum(
            urlsplit(url).path.endswith(canary_path)
            for fetcher in self.fetchers.values()
            for url, _limit in fetcher.calls
        )


@pytest.mark.parametrize("generations", [("a", "a", "b"), ("a", "b", "b")])
def test_confirmation_streams_the_modal_generation_during_one_rollover(generations):
    fetcher = GenerationAttemptFetcher(generations)

    report = health.confirm_site_health(
        "https://example.test/dashboard/",
        clock=lambda: NOW,
        fetch=fetcher,
        sleep=lambda _seconds: None,
    )

    assert report["healthy"] is True
    assert report["confirmation"]["healthy_count"] == 3
    assert report["confirmation"]["streamed_projection_attempt"] == 2
    assert report["confirmation"]["complete_projection_attempt"] == 2
    assert report["confirmation"]["matching_projection_healthy_count"] == 2
    assert [
        probe["streamed_projection_attempted"]
        for probe in report["confirmation"]["probes"]
    ] == [False, True, False]
    assert fetcher.reliability_call_count() == 1
    assert fetcher.canary_call_count("amd_test_health") == 1


def test_confirmation_falls_back_to_probe_three_when_middle_cannot_stream():
    fetcher = AttemptFetcher({2})

    report = health.confirm_site_health(
        "https://example.test/dashboard/",
        clock=lambda: NOW,
        fetch=fetcher,
        sleep=lambda _seconds: None,
    )

    assert report["healthy"] is True
    assert report["confirmation"]["healthy_count"] == 2
    assert report["confirmation"]["streamed_projection_attempt"] == 3
    assert report["confirmation"]["complete_projection_attempt"] == 3
    assert report["confirmation"]["matching_projection_healthy_count"] == 2
    assert [
        probe["streamed_projection_attempted"]
        for probe in report["confirmation"]["probes"]
    ] == [False, False, True]
    reliability_path = "data/vllm/ci/operations_v2/reliability.json"
    assert sum(
        urlsplit(url).path.endswith(reliability_path)
        for url, _limit in fetcher.base.calls
    ) == 1


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
    assert report["confirmation"]["streamed_projection_attempt"] == 2
    assert report["confirmation"]["complete_projection_attempt"] == 2
    assert report["confirmation"]["complete_projection_verified"] is True
    assert report["confirmation"]["matching_projection_healthy_count"] == 2
    reliability_path = "data/vllm/ci/operations_v2/reliability.json"
    assert sum(
        urlsplit(url).path.endswith(reliability_path)
        for url, _limit in fetcher.base.calls
    ) == 1
    assert [
        probe["streamed_projection_attempted"]
        for probe in report["confirmation"]["probes"]
    ] == [False, True, False]
    assert {token.split("-")[1] for token in fetcher.tokens} == {"1", "2", "3"}
    assert len(set(fetcher.tokens)) == 3


def test_confirmation_fails_globally_when_the_single_full_reliability_stream_fails():
    reliability_path = "data/vllm/ci/operations_v2/reliability.json"
    fetcher = Fetcher(resources={reliability_path: _response(b"corrupt")})

    report = health.confirm_site_health(
        "https://example.test/dashboard/",
        clock=lambda: NOW,
        fetch=fetcher,
        sleep=lambda _seconds: None,
    )

    assert report["healthy"] is False
    assert report["confirmation"]["healthy_count"] == 2
    assert report["confirmation"]["complete_projection_verified"] is False
    assert "complete-projection-required" in _codes(report)
    assert sum(
        urlsplit(url).path.endswith(reliability_path)
        for url, _limit in fetcher.calls
    ) == 1


def test_confirmation_does_not_redownload_canaries_after_bounded_failure():
    canary_path = "data/vllm/ci/operations_v2/amd_test_health.json"
    fetcher = Fetcher(resources={canary_path: _response(b"corrupt")})

    report = health.confirm_site_health(
        "https://example.test/dashboard/",
        clock=lambda: NOW,
        fetch=fetcher,
        sleep=lambda _seconds: None,
    )

    assert report["healthy"] is False
    assert report["confirmation"]["healthy_count"] == 2
    assert report["confirmation"]["streamed_projection_attempt"] is None
    assert "complete-projection-required" in _codes(report)
    assert sum(
        urlsplit(url).path.endswith(canary_path)
        for url, _limit in fetcher.calls
    ) == 1


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
    assert health.REQUESTS_PER_PROBE == 48
    assert health.CONTROL_REQUESTS_PER_PROBE == 22
    assert health.CANARY_REQUESTS_PER_CONFIRMATION == 26
    assert health.STREAMED_REQUESTS_PER_CONFIRMATION == 2
    assert health.MAX_CONFIRMATION_REQUESTS == (
        health.CONFIRMATION_ATTEMPTS * health.CONTROL_REQUESTS_PER_PROBE
        + health.CANARY_REQUESTS_PER_CONFIRMATION
        + health.STREAMED_REQUESTS_PER_CONFIRMATION
    )
    assert health.MAX_CONFIRMATION_TRANSPORT_SECONDS == (
        health.CONFIRMATION_ATTEMPTS
        * health.CONTROL_REQUESTS_PER_PROBE
        * health.FETCH_TIMEOUT_SECONDS
        + health.CANARY_REQUESTS_PER_CONFIRMATION
        * health.CANARY_FETCH_TIMEOUT_SECONDS
        + health.STREAMED_REQUESTS_PER_CONFIRMATION
        * health.STREAM_ATTEMPT_MAX_SECONDS
    )
    assert report["confirmation"]["max_requests"] == 94
    assert health.STREAM_TOTAL_TIMEOUT_SECONDS == 150
    assert report["confirmation"]["canary_request_timeout_seconds"] == 20
    assert report["confirmation"]["max_transport_seconds"] == 1500
    assert report["confirmation"]["retry_delays_seconds"] == [0.0, 2.0, 5.0]
    assert report["confirmation"]["max_elapsed_seconds"] == 1507
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
        *[
            (f"data/vllm/ci/operations_v2/{name}.json", response, expected)
            for name in health.OPERATIONS_STREAMED_LARGE_SECTIONS
            for response, expected in (
                (_response(status=404), "operations-streamed-http"),
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


def test_operations_manifest_requires_the_large_streamed_section_descriptor():
    fetcher = Fetcher()
    operations = json.loads(fetcher.responses[health.OPERATIONS_MANIFEST_PATH]["body"])
    projection = json.loads(
        fetcher.responses[health.PUBLICATION_MANIFEST_PATH]["body"]
    )
    descriptor = operations["sections"].pop("reliability")
    projection["files"].pop(f"data/vllm/ci/{descriptor['path']}")

    with pytest.raises(
        health._ProjectionFailure,
        match="exact supported section inventory",
    ):
        health._normalize_operations_manifest(operations, projection)


def test_checker_streams_and_verifies_large_reliability_route():
    fetcher = Fetcher()

    report = health.check_site_health(now=NOW, fetch=fetcher)

    requested_paths = {urlsplit(url).path for url, _limit in fetcher.calls}
    reliability_path = "data/vllm/ci/operations_v2/reliability.json"
    assert any(path.endswith(reliability_path) for path in requested_paths)
    assert any(
        path.endswith(reliability_path)
        for path in report["projection"]["verified_files"]
    )
    assert report["projection"]["operations_streamed_sections"][0]["verified"] is True
    assert report["projection"]["application_section_count"] == len(
        bundle_contract.OPERATIONS_SECTION_NAMES
    )
    assert report["healthy"] is True


def test_operations_manifest_rejects_an_unbounded_canary_bundle():
    fetcher = Fetcher()
    operations = json.loads(fetcher.responses[health.OPERATIONS_MANIFEST_PATH]["body"])
    projection = json.loads(
        fetcher.responses[health.PUBLICATION_MANIFEST_PATH]["body"]
    )
    for canary_name in health.OPERATIONS_CANARY_SECTIONS[:3]:
        descriptor = operations["sections"][canary_name]
        descriptor["bytes"] = bundle_contract.OPERATIONS_CANARY_FILE_MAX_BYTES
        projection["files"][f"data/vllm/ci/{descriptor['path']}"]["bytes"] = (
            bundle_contract.OPERATIONS_CANARY_FILE_MAX_BYTES
        )

    with pytest.raises(health._ProjectionFailure, match="canary bundle") as exc:
        health._normalize_operations_manifest(operations, projection)

    assert exc.value.code == "operations-canary-budget"


def test_operations_manifest_uses_declared_legacy_budget_during_rollout():
    fetcher = Fetcher()
    operations = json.loads(fetcher.responses[health.OPERATIONS_MANIFEST_PATH]["body"])
    projection = json.loads(
        fetcher.responses[health.PUBLICATION_MANIFEST_PATH]["body"]
    )
    section_name = "comparison_retry_evidence"
    descriptor = operations["sections"][section_name]
    legacy_size = (
        bundle_contract.OPERATIONS_CANARY_SECTION_MAX_BYTES[section_name] + 1
    )
    assert legacy_size <= bundle_contract.OPERATIONS_LEGACY_CANARY_FILE_MAX_BYTES
    descriptor["bytes"] = legacy_size
    projection["files"][f"data/vllm/ci/{descriptor['path']}"]["bytes"] = legacy_size

    operations["bundle_version"] = bundle_contract.OPERATIONS_LEGACY_BUNDLE_VERSION
    health._normalize_operations_manifest(operations, projection)

    operations["bundle_version"] = bundle_contract.OPERATIONS_BUNDLE_VERSION
    with pytest.raises(health._ProjectionFailure, match="canary bundle section") as exc:
        health._normalize_operations_manifest(operations, projection)
    assert exc.value.code == "operations-canary-budget"


def test_operations_manifest_rejects_unknown_bundle_version():
    fetcher = Fetcher()
    operations = json.loads(fetcher.responses[health.OPERATIONS_MANIFEST_PATH]["body"])
    projection = json.loads(
        fetcher.responses[health.PUBLICATION_MANIFEST_PATH]["body"]
    )
    operations["bundle_version"] = max(
        bundle_contract.OPERATIONS_SUPPORTED_BUNDLE_VERSIONS
    ) + 1

    with pytest.raises(health._ProjectionFailure, match="unsupported bundle version"):
        health._normalize_operations_manifest(operations, projection)


def test_checker_accepts_a_hash_bound_canary_larger_than_two_mibibytes():
    fetcher = Fetcher()
    canary_name = "amd_test_health"
    canary_path = f"data/vllm/ci/operations_v2/{canary_name}.json"
    canary_body = b'{"padding":"' + b"x" * (2 * 1024 * 1024) + b'"}\n'

    operations = json.loads(fetcher.responses[health.OPERATIONS_MANIFEST_PATH]["body"])
    operations["sections"][canary_name]["bytes"] = len(canary_body)
    operations_body = json.dumps(operations).encode()
    fetcher.responses[health.OPERATIONS_MANIFEST_PATH] = _response(operations_body)
    fetcher.responses[canary_path] = _response(canary_body)

    projection = json.loads(
        fetcher.responses[health.PUBLICATION_MANIFEST_PATH]["body"]
    )
    for path, body in (
        (health.OPERATIONS_MANIFEST_PATH, operations_body),
        (canary_path, canary_body),
    ):
        projection["files"][path]["bytes"] = len(body)
        projection["files"][path]["sha256"] = hashlib.sha256(body).hexdigest()
    projection["total_bytes"] = sum(
        descriptor["bytes"] for descriptor in projection["files"].values()
    )
    projection_raw = health._canonical_json(projection)
    fetcher.responses[health.PUBLICATION_MANIFEST_PATH] = _response(projection_raw)

    marker = json.loads(fetcher.responses[health.PUBLICATION_GENERATION_PATH]["body"])
    marker["public_projection"].update(
        {
            "manifest_sha256": hashlib.sha256(projection_raw).hexdigest(),
            "total_bytes": projection["total_bytes"],
        }
    )
    fetcher.responses[health.PUBLICATION_GENERATION_PATH] = _response(
        json.dumps(marker).encode()
    )

    report = health.check_site_health(now=NOW, fetch=fetcher)

    assert report["healthy"] is True
    assert len(canary_body) > 2 * 1024 * 1024
    assert next(
        limit for url, limit in fetcher.calls if urlsplit(url).path.endswith(canary_path)
    ) == len(canary_body)


def test_checker_rejects_hash_bound_nonfinite_canary_json():
    fetcher = Fetcher()
    canary_name = "nightly"
    canary_path = f"data/vllm/ci/operations_v2/{canary_name}.json"
    canary_body = b'{"derived_metric":NaN}\n'

    operations = json.loads(fetcher.responses[health.OPERATIONS_MANIFEST_PATH]["body"])
    operations["sections"][canary_name]["bytes"] = len(canary_body)
    operations_body = json.dumps(operations).encode()
    fetcher.responses[health.OPERATIONS_MANIFEST_PATH] = _response(operations_body)
    fetcher.responses[canary_path] = _response(canary_body)

    projection = json.loads(
        fetcher.responses[health.PUBLICATION_MANIFEST_PATH]["body"]
    )
    for path, body in (
        (health.OPERATIONS_MANIFEST_PATH, operations_body),
        (canary_path, canary_body),
    ):
        projection["files"][path]["bytes"] = len(body)
        projection["files"][path]["sha256"] = hashlib.sha256(body).hexdigest()
    projection["total_bytes"] = sum(
        descriptor["bytes"] for descriptor in projection["files"].values()
    )
    projection_raw = health._canonical_json(projection)
    fetcher.responses[health.PUBLICATION_MANIFEST_PATH] = _response(projection_raw)

    marker = json.loads(fetcher.responses[health.PUBLICATION_GENERATION_PATH]["body"])
    marker["public_projection"].update(
        {
            "manifest_sha256": hashlib.sha256(projection_raw).hexdigest(),
            "total_bytes": projection["total_bytes"],
        }
    )
    fetcher.responses[health.PUBLICATION_GENERATION_PATH] = _response(
        json.dumps(marker).encode()
    )

    report = health.check_site_health(now=NOW, fetch=fetcher)

    assert report["healthy"] is False
    assert "operations-canary-json" in {
        reason["code"] for reason in report["reasons"]
    }


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
        + len(bundle_contract.OPERATIONS_SECTION_NAMES)
    )
    assert report["projection"]["total_bytes"] > 0


def test_health_accepts_only_hash_bound_safe_historical_tree_declaration():
    fetcher = Fetcher()
    manifest = json.loads(fetcher.responses[health.PUBLICATION_MANIFEST_PATH]["body"])
    manifest["limits"]["max_tree_bytes"] = health.PROJECTION_MAX_TREE_BYTES + 1
    legacy_raw = health._canonical_json(manifest)
    fetcher.responses[health.PUBLICATION_MANIFEST_PATH] = _response(legacy_raw)

    unbound = health.check_site_health(now=NOW, fetch=fetcher)
    assert unbound["healthy"] is False
    assert "projection-attestation" in _codes(unbound)

    _rebind_manifest(fetcher, manifest)
    bound = health.check_site_health(now=NOW, fetch=fetcher)
    assert bound["healthy"] is True
    assert bound["projection"]["manifest_policy"] == "safe-historical-read-only"
    assert bound["projection"]["total_bytes"] <= health.PROJECTION_MAX_TREE_BYTES


@pytest.mark.parametrize(
    "limits",
    [
        {
            "max_blob_bytes": health.PROJECTION_MAX_BLOB_BYTES + 1,
            "max_tree_bytes": health.PROJECTION_MAX_TREE_BYTES + 1,
            "max_files": health.PROJECTION_MAX_FILES,
        },
        {
            "max_blob_bytes": health.PROJECTION_MAX_BLOB_BYTES,
            "max_tree_bytes": health.PROJECTION_MAX_TREE_BYTES - 1,
            "max_files": health.PROJECTION_MAX_FILES,
        },
        {
            "max_blob_bytes": health.PROJECTION_MAX_BLOB_BYTES,
            "max_tree_bytes": health.PROJECTION_MAX_TREE_BYTES + 1,
            "max_files": health.PROJECTION_MAX_FILES + 1,
        },
        {
            "max_blob_bytes": health.PROJECTION_MAX_BLOB_BYTES,
            "max_tree_bytes": False,
            "max_files": health.PROJECTION_MAX_FILES,
        },
    ],
)
def test_health_rejects_unsafe_historical_limit_declarations(
    limits: dict[str, object],
):
    fetcher = Fetcher()
    manifest = json.loads(fetcher.responses[health.PUBLICATION_MANIFEST_PATH]["body"])
    manifest["limits"] = limits
    _rebind_manifest(fetcher, manifest)

    report = health.check_site_health(now=NOW, fetch=fetcher)

    assert report["healthy"] is False
    assert "manifest-contract" in _codes(report)


def test_health_legacy_compatibility_never_expands_actual_tree_cap():
    fetcher = Fetcher()
    manifest = json.loads(fetcher.responses[health.PUBLICATION_MANIFEST_PATH]["body"])
    manifest["limits"]["max_tree_bytes"] = health.PROJECTION_MAX_TREE_BYTES + 1
    descriptors = list(manifest["files"].values())
    for descriptor, size in zip(
        descriptors,
        (
            health.PROJECTION_MAX_BLOB_BYTES,
            health.PROJECTION_MAX_BLOB_BYTES,
            health.PROJECTION_MAX_BLOB_BYTES,
            2 * 1024 * 1024,
        ),
    ):
        descriptor["bytes"] = size
    manifest["total_bytes"] = sum(row["bytes"] for row in descriptors)
    _rebind_manifest(fetcher, manifest)

    report = health.check_site_health(now=NOW, fetch=fetcher)

    assert report["healthy"] is False
    # The marker's bound total is independently capped before the manifest is
    # considered, so the oversized actual inventory cannot reach compatibility.
    assert "generation-contract" in _codes(report)


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
