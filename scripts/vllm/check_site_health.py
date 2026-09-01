#!/usr/bin/env python3
"""Synthetic liveness check for the published dashboard and its data plane."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


DEFAULT_SITE_URL = "https://andreaskaratzas.github.io/vllm-ci-dashboard/"
DEFAULT_STATE_CONFIG = Path(__file__).resolve().parents[2] / "config/dashboard_state.json"
DEFAULT_BOOTSTRAP_CONFIG = (
    Path(__file__).resolve().parents[2] / "config/dashboard_bootstrap.json"
)
PUBLICATION_STATUS_PATH = "data/vllm/ci/publication_status.json"
PUBLICATION_GENERATION_PATH = "publication_generation.json"
PUBLICATION_MANIFEST_PATH = "publication_manifest.json"
OPERATIONS_MANIFEST_PATH = "data/vllm/ci/operations_v2_manifest.json"
DEFAULT_MAX_PUBLICATION_AGE_HOURS = 3.0
FETCH_TIMEOUT_SECONDS = 10
CONFIRMATION_ATTEMPTS = 3
CONFIRMATION_QUORUM = 2
CONFIRMATION_DELAYS_SECONDS = (0.0, 2.0, 5.0)
SITE_MIN_BYTES = 500
SITE_MAX_BYTES = 2 * 1024 * 1024
STATUS_MAX_BYTES = 64 * 1024
MARKER_MAX_BYTES = 4096
MANIFEST_MAX_BYTES = 8 * 1024 * 1024
ASSET_MAX_BYTES = 4 * 1024 * 1024
OPERATIONS_MANIFEST_MAX_BYTES = 2 * 1024 * 1024
OPERATIONS_CANARY_SECTIONS = ("nightly", "amd_test_health", "diagnostics")
OPERATIONS_CANARY_MAX_BYTES = 2 * 1024 * 1024
REPORT_MAX_BYTES = 64 * 1024
MARKDOWN_MAX_BYTES = 16 * 1024
BOOTSTRAP_CONFIG_MAX_BYTES = 4096
BOOTSTRAP_EVIDENCE_MAX_BYTES = 16 * 1024
BOOTSTRAP_EVIDENCE_MAX_AGE = timedelta(minutes=10)
BOOTSTRAP_REF_FETCH_TIMEOUT_SECONDS = 10
PROJECTION_MAX_BLOB_BYTES = 85 * 1024 * 1024
PROJECTION_MAX_TREE_BYTES = 384 * 1024 * 1024
PROJECTION_MAX_FILES = 10_000
CRITICAL_ASSET_PATHS = (
    "assets/css/dashboard.css",
    "assets/css/ops-v2.css",
    "assets/js/utils.js",
    "assets/js/publication-status.js",
    "assets/js/dashboard-nav.js",
    "assets/js/ops-v2.js",
)
REQUESTS_PER_PROBE = (
    5 + len(CRITICAL_ASSET_PATHS) + len(OPERATIONS_CANARY_SECTIONS)
)
MAX_SHELL_ASSET_REFERENCES = 256
MAX_OPERATIONS_SECTIONS = 64
SHA256_RE = re.compile(r"[0-9a-f]{64}")
GIT_OID_RE = re.compile(r"[0-9a-f]{40}")
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
GENERATION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,299}")
FUTURE_SKEW = timedelta(minutes=5)
SITE_REQUIRED_MARKERS = (
    b"<title>vLLM AMD CI Operations</title>",
    b'id="publication-status-banner"',
)
PUBLICATION_MODES = frozenset({"current", "degraded", "fallback", "mixed", "blocked"})
PUBLICATION_STATUSES = frozenset({"healthy", "degraded", "blocked"})
PUBLICATION_SURFACE_LABELS = frozenset({
    "Agent health",
    "CI analytics",
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
Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


class _ShellAssetParser(HTMLParser):
    """Collect only bounded script and stylesheet references from the shell."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stylesheets: list[str] = []
        self.scripts: list[str] = []
        self.malformed = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        names = [name for name, _value in attrs]
        if len(names) != len(set(names)) or tag == "base":
            self.malformed = True
            return
        attributes = dict(attrs)
        if tag == "link":
            rel = attributes.get("rel")
            href = attributes.get("href")
            if (
                isinstance(rel, str)
                and "stylesheet" in rel.lower().split()
                and isinstance(href, str)
            ):
                if len(self.stylesheets) + len(self.scripts) >= MAX_SHELL_ASSET_REFERENCES:
                    self.malformed = True
                else:
                    self.stylesheets.append(href)
        elif tag == "script" and isinstance(attributes.get("src"), str):
            if len(self.stylesheets) + len(self.scripts) >= MAX_SHELL_ASSET_REFERENCES:
                self.malformed = True
            else:
                self.scripts.append(attributes["src"] or "")


class _NoRedirectHandler(HTTPRedirectHandler):
    """Fail closed instead of following unbounded or cross-origin redirects."""

    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


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


def _cache_bust_token(url: str, token: str) -> str:
    parsed = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "health_check"
    ]
    query.append(("health_check", token))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _publication_url(site_url: str) -> str:
    target = urljoin(site_url, PUBLICATION_STATUS_PATH)
    site = urlsplit(site_url)
    parsed = urlsplit(target)
    if parsed.scheme != site.scheme or parsed.netloc != site.netloc:
        raise ValueError("publication status URL escaped the configured site origin")
    return target


def _same_origin_url(site_url: str, relative_path: str) -> str:
    """Resolve one canonical projection path without allowing an origin escape."""
    if (
        not relative_path
        or len(relative_path) > 500
        or relative_path.startswith(("/", "\\"))
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in relative_path.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 for char in relative_path)
    ):
        raise ValueError("projection path is not a safe canonical relative path")
    target = urljoin(site_url, relative_path)
    site = urlsplit(site_url)
    parsed = urlsplit(target)
    base_path = site.path if site.path.endswith("/") else site.path + "/"
    if (
        parsed.scheme != site.scheme
        or parsed.netloc != site.netloc
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith(base_path)
    ):
        raise ValueError("projection resource escaped the configured site origin")
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
        with build_opener(_NoRedirectHandler()).open(
            request, timeout=FETCH_TIMEOUT_SECONDS
        ) as response:
            body = response.read(max_bytes + 1)
            return {
                "http_status": int(response.getcode() or 0),
                "body": body[:max_bytes],
                "oversize": len(body) > max_bytes,
                "error": None,
                "final_url": response.geturl(),
            }
    except HTTPError as exc:
        return {
            "http_status": int(exc.code or 0),
            "body": b"",
            "oversize": False,
            "error": f"HTTP {int(exc.code or 0)}",
            "final_url": exc.geturl(),
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {
            "http_status": 0,
            "body": b"",
            "oversize": False,
            "error": type(exc).__name__,
            "final_url": None,
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


class _ProjectionFailure(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _strict_json(raw: bytes, *, code: str, label: str) -> object:
    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise _ProjectionFailure(code, f"{label} was not strict UTF-8 JSON.") from exc


def _response_body(
    response: Mapping[str, Any],
    *,
    requested_url: str,
    expected_status: int,
    limit: int,
    code_prefix: str,
    label: str,
) -> bytes:
    status = int(response.get("http_status") or 0)
    if status != expected_status:
        raise _ProjectionFailure(
            f"{code_prefix}-http", f"{label} returned HTTP {status}."
        )
    if response.get("oversize") is True:
        raise _ProjectionFailure(
            f"{code_prefix}-oversize", f"{label} exceeded {limit} bytes."
        )
    body = response.get("body")
    if not isinstance(body, bytes) or len(body) > limit:
        raise _ProjectionFailure(
            f"{code_prefix}-body", f"{label} did not return bounded bytes."
        )
    final_url = response.get("final_url")
    if final_url is not None:
        if not isinstance(final_url, str):
            raise _ProjectionFailure(
                f"{code_prefix}-redirect", f"{label} returned an invalid final URL."
            )
        expected = urlsplit(requested_url)
        final = urlsplit(final_url)
        if (
            final.scheme != expected.scheme
            or final.netloc != expected.netloc
            or final.path != expected.path
            or final.username is not None
            or final.password is not None
        ):
            raise _ProjectionFailure(
                f"{code_prefix}-redirect",
                f"{label} escaped its exact same-origin path.",
            )
    return body


def _safe_manifest_path(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 500
        or value.startswith(("/", "\\"))
        or value.endswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise _ProjectionFailure(
            "manifest-contract", "Publication manifest contained an unsafe path."
        )
    try:
        if len(value.encode("utf-8", errors="strict")) > 1000:
            raise UnicodeError
    except UnicodeError as exc:
        raise _ProjectionFailure(
            "manifest-contract", "Publication manifest contained an unsafe path."
        ) from exc
    return value


def _normalize_projection_attestation(value: object) -> dict[str, Any]:
    expected = {
        "schema_version",
        "manifest_path",
        "manifest_sha256",
        "file_count",
        "total_bytes",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise _ProjectionFailure(
            "generation-contract", "Publication generation attestation was malformed."
        )
    digest = value.get("manifest_sha256")
    file_count = value.get("file_count")
    total_bytes = value.get("total_bytes")
    if (
        value.get("schema_version") != 1
        or value.get("manifest_path") != PUBLICATION_MANIFEST_PATH
        or not isinstance(digest, str)
        or SHA256_RE.fullmatch(digest) is None
        or type(file_count) is not int
        or not 0 <= file_count <= PROJECTION_MAX_FILES
        or type(total_bytes) is not int
        or not 0 <= total_bytes <= PROJECTION_MAX_TREE_BYTES
    ):
        raise _ProjectionFailure(
            "generation-contract", "Publication generation attestation was malformed."
        )
    return {
        "schema_version": 1,
        "manifest_path": PUBLICATION_MANIFEST_PATH,
        "manifest_sha256": digest,
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def _canonical_utc_timestamp(value: object, *, code: str, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise _ProjectionFailure(code, f"{label} timestamp was invalid.")
    parsed = _parse_timestamp(value)
    if parsed is None or _iso_utc(parsed) != value:
        raise _ProjectionFailure(code, f"{label} timestamp was not canonical UTC.")
    return value


def _normalize_generation_marker(value: object) -> dict[str, Any]:
    expected = {
        "schema_version",
        "generation_id",
        "generated_at",
        "state_sha",
        "state_tree",
        "code_sha",
        "public_projection",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise _ProjectionFailure(
            "generation-contract", "Publication generation marker had an unexpected shape."
        )
    generation_id = value.get("generation_id")
    if (
        value.get("schema_version") != 2
        or not isinstance(generation_id, str)
        or GENERATION_RE.fullmatch(generation_id) is None
    ):
        raise _ProjectionFailure(
            "generation-contract", "Publication generation identity was malformed."
        )
    shas: dict[str, str] = {}
    for field in ("state_sha", "state_tree", "code_sha"):
        candidate = value.get(field)
        if not isinstance(candidate, str) or FULL_SHA_RE.fullmatch(candidate) is None:
            raise _ProjectionFailure(
                "generation-contract", "Publication generation identity was malformed."
            )
        shas[field] = candidate
    return {
        "schema_version": 2,
        "generation_id": generation_id,
        "generated_at": _canonical_utc_timestamp(
            value.get("generated_at"),
            code="generation-contract",
            label="Publication generation",
        ),
        **shas,
        "public_projection": _normalize_projection_attestation(
            value.get("public_projection")
        ),
    }


def _normalize_manifest(value: object) -> dict[str, Any]:
    expected = {
        "schema_version",
        "hash_algorithm",
        "git_object_format",
        "excluded_prefixes",
        "limits",
        "file_count",
        "total_bytes",
        "files",
    }
    expected_limits = {
        "max_blob_bytes": PROJECTION_MAX_BLOB_BYTES,
        "max_tree_bytes": PROJECTION_MAX_TREE_BYTES,
        "max_files": PROJECTION_MAX_FILES,
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value.get("schema_version") != 1
        or value.get("hash_algorithm") != "sha256"
        or value.get("git_object_format") != "sha1"
        or value.get("excluded_prefixes") != ["pr-preview/"]
        or value.get("limits") != expected_limits
    ):
        raise _ProjectionFailure(
            "manifest-contract", "Publication manifest policy was malformed."
        )
    raw_files = value.get("files")
    if not isinstance(raw_files, dict) or len(raw_files) > PROJECTION_MAX_FILES:
        raise _ProjectionFailure(
            "manifest-contract", "Publication manifest file map was malformed."
        )
    files: dict[str, dict[str, Any]] = {}
    total = 0
    for raw_path, raw_descriptor in raw_files.items():
        path = _safe_manifest_path(raw_path)
        if path in {PUBLICATION_MANIFEST_PATH, PUBLICATION_GENERATION_PATH} or path.startswith(
            "pr-preview/"
        ):
            raise _ProjectionFailure(
                "manifest-contract", "Publication manifest declared an excluded path."
            )
        if not isinstance(raw_descriptor, dict) or set(raw_descriptor) != {
            "bytes",
            "mode",
            "sha256",
            "git_oid",
        }:
            raise _ProjectionFailure(
                "manifest-contract", "Publication manifest descriptor was malformed."
            )
        size = raw_descriptor.get("bytes")
        mode = raw_descriptor.get("mode")
        digest = raw_descriptor.get("sha256")
        oid = raw_descriptor.get("git_oid")
        if (
            type(size) is not int
            or not 0 <= size <= PROJECTION_MAX_BLOB_BYTES
            or mode not in {"100644", "100755"}
            or not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
            or not isinstance(oid, str)
            or GIT_OID_RE.fullmatch(oid) is None
        ):
            raise _ProjectionFailure(
                "manifest-contract", "Publication manifest descriptor was malformed."
            )
        files[path] = {
            "bytes": size,
            "mode": mode,
            "sha256": digest,
            "git_oid": oid,
        }
        total += size
        if total > PROJECTION_MAX_TREE_BYTES:
            raise _ProjectionFailure(
                "manifest-contract", "Publication manifest exceeded its tree byte limit."
            )
    if (
        type(value.get("file_count")) is not int
        or value.get("file_count") != len(files)
        or type(value.get("total_bytes")) is not int
        or value.get("total_bytes") != total
    ):
        raise _ProjectionFailure(
            "manifest-contract", "Publication manifest totals were inconsistent."
        )
    return {
        "schema_version": 1,
        "hash_algorithm": "sha256",
        "git_object_format": "sha1",
        "excluded_prefixes": ["pr-preview/"],
        "limits": expected_limits,
        "file_count": len(files),
        "total_bytes": total,
        "files": dict(sorted(files.items())),
    }


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _verify_descriptor(
    manifest: Mapping[str, Any], path: str, raw: bytes, *, limit: int
) -> None:
    files = manifest.get("files")
    descriptor = files.get(path) if isinstance(files, dict) else None
    if not isinstance(descriptor, dict):
        raise _ProjectionFailure(
            "projection-missing-file", f"Publication manifest omitted {path}."
        )
    declared_size = descriptor.get("bytes")
    if type(declared_size) is not int or declared_size > limit:
        raise _ProjectionFailure(
            "projection-file-bound", f"Critical publication file {path} exceeded its probe limit."
        )
    if declared_size != len(raw) or descriptor.get("sha256") != hashlib.sha256(raw).hexdigest():
        raise _ProjectionFailure(
            "projection-integrity", f"Critical publication file {path} failed exact verification."
        )


def _normalize_operations_manifest(
    value: object,
    projection_manifest: Mapping[str, Any],
) -> tuple[list[str], dict[str, str]]:
    """Validate the browser's bounded Operations entry point and shard map."""
    expected = {
        "schema_version",
        "bundle_version",
        "generated_at",
        "monolith",
        "shell",
        "organization_summary",
        "sections",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value.get("schema_version") != 2
        or value.get("bundle_version") != 1
        or value.get("monolith") is not None
        or not isinstance(value.get("shell"), dict)
    ):
        raise _ProjectionFailure(
            "operations-manifest-contract",
            "Operations application manifest had an unexpected shape.",
        )
    _canonical_utc_timestamp(
        value.get("generated_at"),
        code="operations-manifest-contract",
        label="Operations application manifest",
    )
    projected_files = projection_manifest.get("files")
    if not isinstance(projected_files, dict):
        raise _ProjectionFailure(
            "operations-manifest-contract",
            "Operations application manifest lacked a public projection.",
        )

    def declared_file(raw: object, *, prefix: str | None = None) -> str:
        if not isinstance(raw, dict) or set(raw) != {"path", "bytes"}:
            raise _ProjectionFailure(
                "operations-manifest-contract",
                "Operations application manifest contained a malformed file descriptor.",
            )
        relative = _safe_manifest_path(raw.get("path"))
        size = raw.get("bytes")
        if (
            type(size) is not int
            or not 0 <= size <= PROJECTION_MAX_BLOB_BYTES
            or (prefix is not None and not relative.startswith(prefix))
            or not relative.endswith(".json")
        ):
            raise _ProjectionFailure(
                "operations-manifest-contract",
                "Operations application manifest contained an unsafe file descriptor.",
            )
        public_path = f"data/vllm/ci/{relative}"
        projection_descriptor = projected_files.get(public_path)
        if (
            not isinstance(projection_descriptor, dict)
            or projection_descriptor.get("bytes") != size
        ):
            raise _ProjectionFailure(
                "operations-projection-contract",
                f"Operations application file {public_path} disagreed with the public projection.",
            )
        return public_path

    organization = value.get("organization_summary")
    if (
        not isinstance(organization, dict)
        or set(organization) != {"path", "bytes", "schema_version"}
        or type(organization.get("schema_version")) is not int
        or organization["schema_version"] <= 0
    ):
        raise _ProjectionFailure(
            "operations-manifest-contract",
            "Operations organization-summary descriptor was malformed.",
        )
    organization_path = declared_file(
        {"path": organization.get("path"), "bytes": organization.get("bytes")}
    )

    raw_sections = value.get("sections")
    if (
        not isinstance(raw_sections, dict)
        or not raw_sections
        or len(raw_sections) > MAX_OPERATIONS_SECTIONS
    ):
        raise _ProjectionFailure(
            "operations-manifest-contract",
            "Operations application sections were not a bounded nonempty object.",
        )
    section_paths: list[str] = []
    named_section_paths: dict[str, str] = {}
    for name, descriptor in raw_sections.items():
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) is None
        ):
            raise _ProjectionFailure(
                "operations-manifest-contract",
                "Operations application manifest contained an unsafe section name.",
            )
        section_path = declared_file(descriptor, prefix="operations_v2/")
        section_paths.append(section_path)
        named_section_paths[name] = section_path
    if len(section_paths) != len(set(section_paths)):
        raise _ProjectionFailure(
            "operations-manifest-contract",
            "Operations application manifest repeated a section path.",
        )
    projected_sections = {
        path
        for path in projected_files
        if path.startswith("data/vllm/ci/operations_v2/") and path.endswith(".json")
    }
    if projected_sections != set(section_paths):
        raise _ProjectionFailure(
            "operations-projection-contract",
            "Operations section set disagreed with the exact public projection.",
        )
    missing_canaries = [
        name for name in OPERATIONS_CANARY_SECTIONS if name not in named_section_paths
    ]
    if missing_canaries:
        raise _ProjectionFailure(
            "operations-manifest-contract",
            "Operations application manifest omitted required default-route canaries: "
            + ", ".join(missing_canaries)
            + ".",
        )
    canary_paths = {
        name: named_section_paths[name] for name in OPERATIONS_CANARY_SECTIONS
    }
    return [organization_path, *sorted(section_paths)], canary_paths


def _critical_asset_references(site_url: str, site_body: bytes) -> dict[str, str]:
    try:
        document = site_body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _ProjectionFailure(
            "site-encoding", "Dashboard root was not strict UTF-8."
        ) from exc
    parser = _ShellAssetParser()
    try:
        parser.feed(document)
        parser.close()
    except (ValueError, AssertionError) as exc:
        raise _ProjectionFailure(
            "site-assets", "Dashboard shell asset references were malformed."
        ) from exc
    if parser.malformed:
        raise _ProjectionFailure(
            "site-assets", "Dashboard shell asset references were malformed."
        )
    matched: dict[str, str] = {}
    site = urlsplit(site_url)
    for path in CRITICAL_ASSET_PATHS:
        references = parser.stylesheets if path.endswith(".css") else parser.scripts
        expected = urlsplit(_same_origin_url(site_url, path))
        for reference in references:
            if (
                not reference
                or len(reference) > 1000
                or any(ord(char) < 32 or ord(char) == 127 for char in reference)
            ):
                continue
            resolved = urlsplit(urljoin(site_url, reference))
            if (
                resolved.scheme == site.scheme
                and resolved.netloc == site.netloc
                and resolved.username is None
                and resolved.password is None
                and resolved.path == expected.path
            ):
                matched[path] = urlunsplit(
                    (resolved.scheme, resolved.netloc, resolved.path, resolved.query, "")
                )
                break
        if path not in matched:
            raise _ProjectionFailure(
                "site-assets", f"Dashboard shell did not reference critical asset {path}."
            )
    return matched


def _read_strict_json_file(path: Path, *, limit: int) -> tuple[object, bytes] | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > limit:
        return None
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return None
    return value, raw


def _strict_utc_seconds(value: object) -> datetime | None:
    parsed = _parse_timestamp(value)
    if (
        parsed is None
        or parsed.microsecond
        or not isinstance(value, str)
        or _iso_utc(parsed) != value
    ):
        return None
    return parsed


def _load_bootstrap_policy(
    state_config_path: Path = DEFAULT_STATE_CONFIG,
    bootstrap_config_path: Path = DEFAULT_BOOTSTRAP_CONFIG,
) -> tuple[dict[str, object], datetime] | None:
    state_loaded = _read_strict_json_file(state_config_path, limit=64 * 1024)
    bootstrap_loaded = _read_strict_json_file(
        bootstrap_config_path,
        limit=BOOTSTRAP_CONFIG_MAX_BYTES,
    )
    if state_loaded is None or bootstrap_loaded is None:
        return None
    value, _state_raw = state_loaded
    bootstrap, _bootstrap_raw = bootstrap_loaded
    expected = {
        "schema_version",
        "branch",
        "previous_branch",
        "manifest_path",
        "generated_roots",
        "limits",
        "bootstrap_allowed",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or not isinstance(value.get("bootstrap_allowed"), bool)
        or not isinstance(value.get("branch"), str)
        or BRANCH_RE.fullmatch(value["branch"]) is None
        or not isinstance(value.get("previous_branch"), str)
        or BRANCH_RE.fullmatch(value["previous_branch"]) is None
        or value["branch"] == value["previous_branch"]
        or not isinstance(bootstrap, dict)
        or set(bootstrap) != {"schema_version", "bootstrap_deadline"}
        or type(bootstrap.get("schema_version")) is not int
        or bootstrap.get("schema_version") != 1
    ):
        return None
    deadline = _strict_utc_seconds(bootstrap.get("bootstrap_deadline"))
    if deadline is None:
        return None
    return value, deadline


def bootstrap_policy_active(
    *,
    now: datetime | None = None,
    state_config_path: Path = DEFAULT_STATE_CONFIG,
    bootstrap_config_path: Path = DEFAULT_BOOTSTRAP_CONFIG,
) -> bool:
    """Return whether the reviewed one-time bootstrap window is still open."""
    checked_at = now or datetime.now(timezone.utc)
    if not isinstance(checked_at, datetime) or checked_at.tzinfo is None:
        return False
    loaded = _load_bootstrap_policy(state_config_path, bootstrap_config_path)
    if loaded is None:
        return False
    state_policy, deadline = loaded
    return (
        state_policy.get("bootstrap_allowed") is True
        and checked_at.astimezone(timezone.utc) < deadline
    )


def _legacy_bootstrap_allowed(
    state_config_path: Path = DEFAULT_STATE_CONFIG,
    *,
    bootstrap_config_path: Path = DEFAULT_BOOTSTRAP_CONFIG,
    evidence_path: Path | None = None,
    repository: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Authorize legacy metadata only from fresh proof that both slots are absent."""
    checked_at = now or datetime.now(timezone.utc)
    if (
        not isinstance(checked_at, datetime)
        or checked_at.tzinfo is None
        or not isinstance(repository, str)
        or REPOSITORY_RE.fullmatch(repository) is None
        or evidence_path is None
    ):
        return False
    checked_at = checked_at.astimezone(timezone.utc)
    loaded = _load_bootstrap_policy(state_config_path, bootstrap_config_path)
    if loaded is None:
        return False
    state_policy, deadline = loaded
    if state_policy.get("bootstrap_allowed") is not True or checked_at >= deadline:
        return False
    evidence_loaded = _read_strict_json_file(
        evidence_path,
        limit=BOOTSTRAP_EVIDENCE_MAX_BYTES,
    )
    if evidence_loaded is None:
        return False
    evidence, evidence_raw = evidence_loaded
    if (
        not isinstance(evidence, dict)
        or set(evidence) != {
            "schema_version",
            "provider",
            "repository",
            "checked_at",
            "refs",
        }
        or evidence.get("schema_version") != 1
        or evidence.get("provider") != "github-rest-git-ref-v1"
        or evidence.get("repository") != repository
        or not isinstance(evidence.get("refs"), dict)
    ):
        return False
    observed_at = _strict_utc_seconds(evidence.get("checked_at"))
    if (
        observed_at is None
        or observed_at > checked_at + FUTURE_SKEW
        or checked_at - observed_at > BOOTSTRAP_EVIDENCE_MAX_AGE
    ):
        return False
    branches = (state_policy["branch"], state_policy["previous_branch"])
    raw_refs = evidence["refs"]
    if set(raw_refs) != set(branches):
        return False
    normalized_refs: dict[str, dict[str, object]] = {}
    for branch in branches:
        descriptor = raw_refs.get(branch)
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "ref",
            "status",
            "sha",
        }:
            return False
        status = descriptor.get("status")
        sha = descriptor.get("sha")
        if (
            descriptor.get("ref") != f"refs/heads/{branch}"
            or status not in {"absent", "present"}
            or (status == "absent" and sha is not None)
            or (
                status == "present"
                and (not isinstance(sha, str) or FULL_SHA_RE.fullmatch(sha) is None)
            )
        ):
            return False
        normalized_refs[branch] = {
            "ref": f"refs/heads/{branch}",
            "status": status,
            "sha": sha,
        }
    normalized = {
        "schema_version": 1,
        "provider": "github-rest-git-ref-v1",
        "repository": repository,
        "checked_at": _iso_utc(observed_at),
        "refs": dict(sorted(normalized_refs.items())),
    }
    if evidence_raw != _canonical_json(normalized):
        return False
    return all(row["status"] == "absent" for row in normalized_refs.values())


def _github_ref_observation(
    repository: str,
    branch: str,
    *,
    token: str,
) -> dict[str, object]:
    if REPOSITORY_RE.fullmatch(repository) is None or BRANCH_RE.fullmatch(branch) is None:
        raise ValueError("repository or branch is malformed")
    if token and (len(token) > 500 or any(ord(char) < 32 for char in token)):
        raise ValueError("GitHub token is malformed")
    url = (
        f"https://api.github.com/repos/{quote(repository, safe='/')}/git/ref/heads/"
        f"{quote(branch, safe='')}"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Accept-Encoding": "identity",
        "User-Agent": "vllm-ci-dashboard-bootstrap-proof/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    try:
        with build_opener(_NoRedirectHandler()).open(
            request,
            timeout=BOOTSTRAP_REF_FETCH_TIMEOUT_SECONDS,
        ) as response:
            if response.getcode() != 200 or response.geturl() != url:
                raise RuntimeError("GitHub ref response was ambiguous")
            raw = response.read(BOOTSTRAP_EVIDENCE_MAX_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 404:
            return {
                "ref": f"refs/heads/{branch}",
                "status": "absent",
                "sha": None,
            }
        raise RuntimeError(f"GitHub ref API returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("GitHub ref API transport was ambiguous") from exc
    if len(raw) > BOOTSTRAP_EVIDENCE_MAX_BYTES:
        raise RuntimeError("GitHub ref API response exceeded its byte limit")
    try:
        payload = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("GitHub ref API response was not strict JSON") from exc
    obj = payload.get("object") if isinstance(payload, dict) else None
    sha = obj.get("sha") if isinstance(obj, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("ref") != f"refs/heads/{branch}"
        or not isinstance(obj, dict)
        or obj.get("type") != "commit"
        or not isinstance(sha, str)
        or FULL_SHA_RE.fullmatch(sha) is None
    ):
        raise RuntimeError("GitHub ref API response did not prove the requested branch")
    return {"ref": f"refs/heads/{branch}", "status": "present", "sha": sha}


def write_bootstrap_ref_evidence(
    output: Path,
    repository: str,
    *,
    now: datetime | None = None,
    token: str = "",
    state_config_path: Path = DEFAULT_STATE_CONFIG,
    bootstrap_config_path: Path = DEFAULT_BOOTSTRAP_CONFIG,
) -> dict[str, object]:
    """Observe both configured refs through GitHub and write canonical evidence."""
    checked_at = now or datetime.now(timezone.utc)
    if not isinstance(checked_at, datetime) or checked_at.tzinfo is None:
        raise ValueError("bootstrap evidence clock must be timezone-aware")
    checked_at = checked_at.astimezone(timezone.utc).replace(microsecond=0)
    loaded = _load_bootstrap_policy(state_config_path, bootstrap_config_path)
    if loaded is None:
        raise ValueError("bootstrap policy is malformed")
    state_policy, _deadline = loaded
    branches = (state_policy["branch"], state_policy["previous_branch"])
    refs = {
        branch: _github_ref_observation(repository, branch, token=token)
        for branch in branches
    }
    evidence = {
        "schema_version": 1,
        "provider": "github-rest-git-ref-v1",
        "repository": repository,
        "checked_at": _iso_utc(checked_at),
        "refs": dict(sorted(refs.items())),
    }
    encoded = _canonical_json(evidence)
    if len(encoded) > BOOTSTRAP_EVIDENCE_MAX_BYTES:
        raise ValueError("bootstrap ref evidence exceeds its byte limit")
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError("bootstrap ref evidence output cannot be a symlink")
    output.write_bytes(encoded)
    return evidence


def check_site_health(
    site_url: str = DEFAULT_SITE_URL,
    *,
    max_publication_age_hours: float = DEFAULT_MAX_PUBLICATION_AGE_HOURS,
    now: datetime | None = None,
    fetch: Fetch = fetch_url,
    cache_token: str | None = None,
    allow_legacy_metadata_absence: bool = False,
) -> dict[str, Any]:
    """Return one deterministic, bounded projection-integrity probe."""
    if not math.isfinite(max_publication_age_hours) or max_publication_age_hours <= 0:
        raise ValueError("max publication age must be a positive finite number")
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    checked_at = checked_at.astimezone(timezone.utc)
    if cache_token is None:
        cache_token = str(int(checked_at.timestamp()))
    if (
        not isinstance(cache_token, str)
        or not cache_token
        or len(cache_token) > 128
        or re.fullmatch(r"[A-Za-z0-9._-]+", cache_token) is None
    ):
        raise ValueError("cache token must be a safe bounded identifier")
    if not isinstance(allow_legacy_metadata_absence, bool):
        raise ValueError("legacy metadata policy must be boolean")
    base_url = _normalize_site_url(site_url)
    publication_url = _publication_url(base_url)
    generation_url = _same_origin_url(base_url, PUBLICATION_GENERATION_PATH)
    manifest_url = _same_origin_url(base_url, PUBLICATION_MANIFEST_PATH)
    reasons: list[dict[str, str]] = []

    site_request_url = _cache_bust_token(base_url, cache_token)
    site_response = fetch(site_request_url, SITE_MAX_BYTES)
    site_http = int(site_response.get("http_status") or 0)
    site_body = site_response.get("body")
    site_body = site_body if isinstance(site_body, bytes) else b""
    if site_http != 200:
        reasons.append(_reason("site-http", f"Dashboard root returned HTTP {site_http}."))
    elif site_response.get("oversize") is True:
        reasons.append(
            _reason("site-oversize", f"Dashboard root exceeded {SITE_MAX_BYTES} bytes.")
        )
    elif isinstance(site_response.get("final_url"), str) and (
        urlsplit(site_response["final_url"]).scheme,
        urlsplit(site_response["final_url"]).netloc,
        urlsplit(site_response["final_url"]).path,
    ) != (
        urlsplit(site_request_url).scheme,
        urlsplit(site_request_url).netloc,
        urlsplit(site_request_url).path,
    ):
        reasons.append(
            _reason("site-redirect", "Dashboard root escaped its exact same-origin path.")
        )
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

    publication_request_url = _cache_bust_token(publication_url, cache_token)
    status_response = fetch(publication_request_url, STATUS_MAX_BYTES)
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
    status_body_value = status_response.get("body")
    status_body = status_body_value if isinstance(status_body_value, bytes) else b""
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
    elif isinstance(status_response.get("final_url"), str) and (
        urlsplit(status_response["final_url"]).scheme,
        urlsplit(status_response["final_url"]).netloc,
        urlsplit(status_response["final_url"]).path,
    ) != (
        urlsplit(publication_request_url).scheme,
        urlsplit(publication_request_url).netloc,
        urlsplit(publication_request_url).path,
    ):
        reasons.append(
            _reason(
                "publication-redirect",
                "Publication status escaped its exact same-origin path.",
            )
        )
    else:
        try:
            if not isinstance(status_body_value, bytes):
                raise ValueError("response body was not bytes")
            payload = json.loads(
                status_body.decode("utf-8", errors="strict"),
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

    generation_request_url = _cache_bust_token(generation_url, cache_token)
    manifest_request_url = _cache_bust_token(manifest_url, cache_token)
    generation_response = fetch(generation_request_url, MARKER_MAX_BYTES)
    manifest_response = fetch(manifest_request_url, MANIFEST_MAX_BYTES)
    generation_http = int(generation_response.get("http_status") or 0)
    manifest_http = int(manifest_response.get("http_status") or 0)
    projection: dict[str, Any] = {
        "generation_url": generation_url,
        "generation_http": generation_http,
        "manifest_url": manifest_url,
        "manifest_http": manifest_http,
        "mode": "invalid",
        "verified": False,
        "generation_id": None,
        "state_sha": None,
        "state_tree": None,
        "code_sha": None,
        "manifest_sha256": None,
        "file_count": None,
        "total_bytes": None,
        "operations_manifest_http": None,
        "operations_canaries": [
            {"name": name, "path": None, "http_status": None}
            for name in OPERATIONS_CANARY_SECTIONS
        ],
        "application_section_count": None,
        "verified_files": [],
    }
    legacy_bootstrap = (
        allow_legacy_metadata_absence
        and generation_http == 404
        and manifest_http == 404
    )
    if legacy_bootstrap:
        projection.update({"mode": "legacy-bootstrap", "verified": False})
    else:
        metadata_http_valid = True
        if generation_http != 200:
            metadata_http_valid = False
            reasons.append(
                _reason(
                    "generation-http",
                    f"Publication generation marker returned HTTP {generation_http}.",
                )
            )
        if manifest_http != 200:
            metadata_http_valid = False
            reasons.append(
                _reason(
                    "manifest-http",
                    f"Publication manifest returned HTTP {manifest_http}.",
                )
            )
        if metadata_http_valid:
            try:
                generation_raw = _response_body(
                    generation_response,
                    requested_url=generation_request_url,
                    expected_status=200,
                    limit=MARKER_MAX_BYTES,
                    code_prefix="generation",
                    label="Publication generation marker",
                )
                manifest_raw = _response_body(
                    manifest_response,
                    requested_url=manifest_request_url,
                    expected_status=200,
                    limit=MANIFEST_MAX_BYTES,
                    code_prefix="manifest",
                    label="Publication manifest",
                )
                marker = _normalize_generation_marker(
                    _strict_json(
                        generation_raw,
                        code="generation-json",
                        label="Publication generation marker",
                    )
                )
                manifest = _normalize_manifest(
                    _strict_json(
                        manifest_raw,
                        code="manifest-json",
                        label="Publication manifest",
                    )
                )
                if manifest_raw != _canonical_json(manifest):
                    raise _ProjectionFailure(
                        "manifest-canonical",
                        "Publication manifest was not exact canonical JSON.",
                    )
                manifest_digest = hashlib.sha256(manifest_raw).hexdigest()
                expected_attestation = {
                    "schema_version": 1,
                    "manifest_path": PUBLICATION_MANIFEST_PATH,
                    "manifest_sha256": manifest_digest,
                    "file_count": manifest["file_count"],
                    "total_bytes": manifest["total_bytes"],
                }
                if marker["public_projection"] != expected_attestation:
                    raise _ProjectionFailure(
                        "projection-attestation",
                        "Publication generation did not bind the exact manifest digest and totals.",
                    )
                if (
                    manifest["file_count"] + 2 > PROJECTION_MAX_FILES
                    or manifest["total_bytes"] + len(manifest_raw) + len(generation_raw)
                    > PROJECTION_MAX_TREE_BYTES
                ):
                    raise _ProjectionFailure(
                        "manifest-contract",
                        "Publication projection plus metadata exceeded its exact bounds.",
                    )
                if (
                    publication.get("generated_at") is not None
                    and marker["generated_at"] != publication.get("generated_at")
                ):
                    raise _ProjectionFailure(
                        "projection-generation",
                        "Publication status and generation marker identified different generations.",
                    )
                _verify_descriptor(manifest, "index.html", site_body, limit=SITE_MAX_BYTES)
                _verify_descriptor(
                    manifest,
                    PUBLICATION_STATUS_PATH,
                    status_body,
                    limit=STATUS_MAX_BYTES,
                )
                references = _critical_asset_references(base_url, site_body)
                verified_files = ["index.html", PUBLICATION_STATUS_PATH]
                for asset_path in CRITICAL_ASSET_PATHS:
                    files = manifest["files"]
                    descriptor = files.get(asset_path)
                    if not isinstance(descriptor, dict):
                        raise _ProjectionFailure(
                            "projection-missing-file",
                            f"Publication manifest omitted {asset_path}.",
                        )
                    size = descriptor.get("bytes")
                    if type(size) is not int or size > ASSET_MAX_BYTES:
                        raise _ProjectionFailure(
                            "projection-file-bound",
                            f"Critical publication file {asset_path} exceeded its probe limit.",
                        )
                    asset_request_url = _cache_bust_token(
                        references[asset_path], cache_token
                    )
                    asset_response = fetch(asset_request_url, ASSET_MAX_BYTES)
                    asset_raw = _response_body(
                        asset_response,
                        requested_url=asset_request_url,
                        expected_status=200,
                        limit=ASSET_MAX_BYTES,
                        code_prefix="asset",
                        label=f"Critical shell asset {asset_path}",
                    )
                    _verify_descriptor(
                        manifest, asset_path, asset_raw, limit=ASSET_MAX_BYTES
                    )
                    verified_files.append(asset_path)
                operations_url = _same_origin_url(base_url, OPERATIONS_MANIFEST_PATH)
                operations_request_url = _cache_bust_token(
                    operations_url, cache_token
                )
                operations_response = fetch(
                    operations_request_url, OPERATIONS_MANIFEST_MAX_BYTES
                )
                projection["operations_manifest_http"] = int(
                    operations_response.get("http_status") or 0
                )
                operations_raw = _response_body(
                    operations_response,
                    requested_url=operations_request_url,
                    expected_status=200,
                    limit=OPERATIONS_MANIFEST_MAX_BYTES,
                    code_prefix="operations-manifest",
                    label="Operations application manifest",
                )
                _verify_descriptor(
                    manifest,
                    OPERATIONS_MANIFEST_PATH,
                    operations_raw,
                    limit=OPERATIONS_MANIFEST_MAX_BYTES,
                )
                application_paths, canary_paths = _normalize_operations_manifest(
                    _strict_json(
                        operations_raw,
                        code="operations-manifest-json",
                        label="Operations application manifest",
                    ),
                    manifest,
                )
                verified_files.append(OPERATIONS_MANIFEST_PATH)
                canary_rows = projection["operations_canaries"]
                for canary_row in canary_rows:
                    canary_name = canary_row["name"]
                    canary_path = canary_paths[canary_name]
                    canary_row["path"] = canary_path
                    canary_url = _same_origin_url(base_url, canary_path)
                    canary_request_url = _cache_bust_token(canary_url, cache_token)
                    canary_response = fetch(
                        canary_request_url,
                        OPERATIONS_CANARY_MAX_BYTES,
                    )
                    canary_row["http_status"] = int(
                        canary_response.get("http_status") or 0
                    )
                    canary_raw = _response_body(
                        canary_response,
                        requested_url=canary_request_url,
                        expected_status=200,
                        limit=OPERATIONS_CANARY_MAX_BYTES,
                        code_prefix="operations-canary",
                        label=f"Operations {canary_name} canary section",
                    )
                    _verify_descriptor(
                        manifest,
                        canary_path,
                        canary_raw,
                        limit=OPERATIONS_CANARY_MAX_BYTES,
                    )
                    verified_files.append(canary_path)
                projection.update(
                    {
                        "mode": "verified",
                        "verified": True,
                        "generation_id": marker["generation_id"],
                        "state_sha": marker["state_sha"],
                        "state_tree": marker["state_tree"],
                        "code_sha": marker["code_sha"],
                        "manifest_sha256": manifest_digest,
                        "file_count": manifest["file_count"],
                        "total_bytes": manifest["total_bytes"],
                        "application_section_count": len(application_paths) - 1,
                        "verified_files": verified_files,
                    }
                )
            except _ProjectionFailure as exc:
                reasons.append(_reason(exc.code, exc.message))

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
        "projection": projection,
        "reasons": reasons,
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def confirm_site_health(
    site_url: str = DEFAULT_SITE_URL,
    *,
    max_publication_age_hours: float = DEFAULT_MAX_PUBLICATION_AGE_HOURS,
    fetch: Fetch = fetch_url,
    clock: Clock = _utc_now,
    sleep: Sleeper = time.sleep,
    allow_legacy_metadata_absence: bool = False,
) -> dict[str, Any]:
    """Confirm liveness with a deterministic 2-of-3 bounded quorum."""
    probes: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for attempt in range(1, CONFIRMATION_ATTEMPTS + 1):
        delay = CONFIRMATION_DELAYS_SECONDS[attempt - 1]
        if delay:
            sleep(delay)
        checked_at = clock()
        if not isinstance(checked_at, datetime) or checked_at.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        checked_at = checked_at.astimezone(timezone.utc)
        epoch_microseconds = int(checked_at.timestamp() * 1_000_000)
        cache_token = f"probe-{attempt}-{epoch_microseconds}"
        probe = check_site_health(
            site_url,
            max_publication_age_hours=max_publication_age_hours,
            now=checked_at,
            fetch=fetch,
            cache_token=cache_token,
            allow_legacy_metadata_absence=allow_legacy_metadata_absence,
        )
        probes.append(probe)
        site = probe.get("site") if isinstance(probe.get("site"), dict) else {}
        publication = (
            probe.get("publication")
            if isinstance(probe.get("publication"), dict)
            else {}
        )
        projection = (
            probe.get("projection")
            if isinstance(probe.get("projection"), dict)
            else {}
        )
        raw_reasons = probe.get("reasons")
        reason_codes = []
        if isinstance(raw_reasons, list):
            reason_codes = [
                row.get("code")
                for row in raw_reasons
                if isinstance(row, dict) and isinstance(row.get("code"), str)
            ][:20]
        summaries.append(
            {
                "attempt": attempt,
                "checked_at": probe.get("checked_at"),
                "healthy": probe.get("healthy") is True,
                "site_http": site.get("http_status"),
                "publication_http": publication.get("http_status"),
                "generation_http": projection.get("generation_http"),
                "manifest_http": projection.get("manifest_http"),
                "projection_mode": projection.get("mode"),
                "projection_verified": projection.get("verified") is True,
                "reason_codes": reason_codes,
            }
        )

    healthy_count = sum(probe.get("healthy") is True for probe in probes)
    confirmed_healthy = healthy_count >= CONFIRMATION_QUORUM
    matching = [
        probe for probe in probes if (probe.get("healthy") is True) is confirmed_healthy
    ]
    representative = dict((matching or probes)[-1])
    if confirmed_healthy:
        reasons: list[dict[str, str]] = []
    else:
        reasons = []
        seen: set[tuple[str, str]] = set()
        for probe in probes:
            raw_reasons = probe.get("reasons")
            if not isinstance(raw_reasons, list):
                continue
            for row in raw_reasons:
                if not isinstance(row, dict):
                    continue
                code = row.get("code")
                message = row.get("message")
                if not isinstance(code, str) or not isinstance(message, str):
                    continue
                key = (code, message)
                if key not in seen and len(reasons) < 40:
                    seen.add(key)
                    reasons.append(_reason(code, message))
        reasons.append(
            _reason(
                "confirmation-quorum",
                (
                    f"Only {healthy_count} of {CONFIRMATION_ATTEMPTS} bounded probes "
                    f"were healthy; {CONFIRMATION_QUORUM} were required."
                ),
            )
        )
    representative.update(
        {
            "schema_version": 1,
            "checked_at": probes[-1]["checked_at"],
            "healthy": confirmed_healthy,
            "overall_status": "healthy" if confirmed_healthy else "confirmed_unhealthy",
            "confirmation": {
                "strategy": "2-of-3-quorum",
                "confirmed": True,
                "max_attempts": CONFIRMATION_ATTEMPTS,
                "attempted": len(probes),
                "required_healthy": CONFIRMATION_QUORUM,
                "healthy_count": healthy_count,
                "unhealthy_count": len(probes) - healthy_count,
                "max_requests": CONFIRMATION_ATTEMPTS * REQUESTS_PER_PROBE,
                "per_request_timeout_seconds": FETCH_TIMEOUT_SECONDS,
                "max_transport_seconds": CONFIRMATION_ATTEMPTS
                * REQUESTS_PER_PROBE
                * FETCH_TIMEOUT_SECONDS,
                "retry_delays_seconds": list(CONFIRMATION_DELAYS_SECONDS),
                "max_elapsed_seconds": CONFIRMATION_ATTEMPTS
                * REQUESTS_PER_PROBE
                * FETCH_TIMEOUT_SECONDS
                + sum(CONFIRMATION_DELAYS_SECONDS),
                "probes": summaries,
            },
            "reasons": reasons,
        }
    )
    return representative


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
    confirmation = (
        report.get("confirmation")
        if isinstance(report.get("confirmation"), dict)
        else {}
    )
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
        "confirmation_confirmed": confirmation.get("confirmed") is True,
        "probe_attempts": confirmation.get("attempted"),
        "healthy_probe_count": confirmation.get("healthy_count"),
        "required_healthy_probes": confirmation.get("required_healthy"),
    }


def markdown_report(report: dict[str, Any]) -> str:
    def safe(value: object) -> str:
        text = "unknown" if value in (None, "") else str(value)
        return text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")[:500]

    site = report.get("site") if isinstance(report.get("site"), dict) else {}
    publication = (
        report.get("publication") if isinstance(report.get("publication"), dict) else {}
    )
    projection = (
        report.get("projection") if isinstance(report.get("projection"), dict) else {}
    )
    confirmation = (
        report.get("confirmation")
        if isinstance(report.get("confirmation"), dict)
        else {}
    )
    raw_canaries = projection.get("operations_canaries")
    canary_summary = "unknown"
    if isinstance(raw_canaries, list) and raw_canaries:
        canary_summary = ", ".join(
            f"{safe(row.get('name'))} · {safe(row.get('http_status'))}"
            for row in raw_canaries
            if isinstance(row, dict)
        ) or "unknown"
    lines = [
        "## Latest synthetic probe",
        "",
        "| Check | Value |",
        "| --- | --- |",
        f"| Healthy | {safe(report.get('healthy'))} |",
        f"| Checked at | {safe(report.get('checked_at'))} |",
        f"| Confirmation | {safe(confirmation.get('healthy_count'))}/{safe(confirmation.get('attempted'))} healthy; quorum {safe(confirmation.get('required_healthy'))} |",
        f"| Site HTTP | {safe(site.get('http_status'))} |",
        f"| Site bytes read | {safe(site.get('bytes_read'))} |",
        f"| Publication HTTP | {safe(publication.get('http_status'))} |",
        f"| Publication mode | {safe(publication.get('mode'))} |",
        f"| Publication status | {safe(publication.get('status'))} |",
        f"| Publication generated at | {safe(publication.get('generated_at'))} |",
        f"| Publication age (hours) | {safe(publication.get('age_hours'))} |",
        f"| Projection mode | {safe(projection.get('mode'))} |",
        f"| Projection manifest SHA-256 | {safe(projection.get('manifest_sha256'))} |",
        f"| Projection files / bytes | {safe(projection.get('file_count'))} / {safe(projection.get('total_bytes'))} |",
        f"| Operations manifest HTTP | {safe(projection.get('operations_manifest_http'))} |",
        f"| Operations canaries | {canary_summary} |",
        f"| Operations section descriptors | {safe(projection.get('application_section_count'))} |",
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
    rendered = "\n".join(lines) + "\n"
    encoded = rendered.encode("utf-8")
    if len(encoded) <= MARKDOWN_MAX_BYTES:
        return rendered
    suffix = b"\n- Additional bounded findings were omitted.\n"
    prefix = encoded[: MARKDOWN_MAX_BYTES - len(suffix)].decode(
        "utf-8", errors="ignore"
    )
    return prefix + suffix.decode("ascii")


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
        "projection": {
            "generation_url": None,
            "generation_http": 0,
            "manifest_url": None,
            "manifest_http": 0,
            "mode": "invalid",
            "verified": False,
            "generation_id": None,
            "state_sha": None,
            "state_tree": None,
            "code_sha": None,
            "manifest_sha256": None,
            "file_count": None,
            "total_bytes": None,
            "operations_manifest_http": None,
            "operations_canaries": [
                {"name": name, "path": None, "http_status": None}
                for name in OPERATIONS_CANARY_SECTIONS
            ],
            "application_section_count": None,
            "verified_files": [],
        },
        "confirmation": {
            "strategy": "2-of-3-quorum",
            "confirmed": False,
            "max_attempts": CONFIRMATION_ATTEMPTS,
            "attempted": 0,
            "required_healthy": CONFIRMATION_QUORUM,
            "healthy_count": 0,
            "unhealthy_count": 0,
            "max_requests": CONFIRMATION_ATTEMPTS * REQUESTS_PER_PROBE,
            "per_request_timeout_seconds": FETCH_TIMEOUT_SECONDS,
            "max_transport_seconds": CONFIRMATION_ATTEMPTS
            * REQUESTS_PER_PROBE
            * FETCH_TIMEOUT_SECONDS,
            "retry_delays_seconds": list(CONFIRMATION_DELAYS_SECONDS),
            "max_elapsed_seconds": CONFIRMATION_ATTEMPTS
            * REQUESTS_PER_PROBE
            * FETCH_TIMEOUT_SECONDS
            + sum(CONFIRMATION_DELAYS_SECONDS),
            "probes": [],
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
    parser.add_argument("--bootstrap-ref-evidence", type=Path)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--write-bootstrap-ref-evidence", type=Path)
    args = parser.parse_args(argv)

    if args.write_bootstrap_ref_evidence is not None:
        if not isinstance(args.repository, str) or REPOSITORY_RE.fullmatch(args.repository) is None:
            parser.error("--repository is required to write bootstrap ref evidence")
        try:
            write_bootstrap_ref_evidence(
                args.write_bootstrap_ref_evidence,
                args.repository,
                token=os.environ.get("GH_TOKEN", ""),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"bootstrap ref observation failed: {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        authority_now = datetime.now(timezone.utc)
        report = confirm_site_health(
            args.site_url,
            max_publication_age_hours=args.max_publication_age_hours,
            allow_legacy_metadata_absence=_legacy_bootstrap_allowed(
                evidence_path=args.bootstrap_ref_evidence,
                repository=args.repository,
                now=authority_now,
            ),
        )
    except Exception:
        report = _internal_error_report(args.site_url)

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if len(encoded.encode("utf-8")) > REPORT_MAX_BYTES:
        report = _internal_error_report(args.site_url)
        report["reasons"] = [
            _reason("checker-report-bound", "The synthetic report exceeded 64 KiB.")
        ]
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
