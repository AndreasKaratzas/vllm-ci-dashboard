"""Buildkite REST API client for fetching builds, jobs, and artifacts.

Adapted from patterns in bk_investigator.py and vllm_git_rocm_analytics.py.
"""

import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlsplit

import requests

from . import config as cfg
from ..buildkite_request_guard import BuildkiteRequestGuardError
from ..private_ci_cache_budget import PRIVATE_CI_CACHE_BUDGET

log = logging.getLogger(__name__)

NIGHTLY_ROSTER_CACHE_DIR = "nightly-rosters-v2"
NIGHTLY_ROSTER_CACHE_SCHEMA_VERSION = 2
LEGACY_NIGHTLY_ROSTER_CACHE_DIRS = ("nightly-rosters-v1",)
NIGHTLY_ROSTER_RETENTION_DAYS = 16
NIGHTLY_ROSTER_MAX_SHARD_BYTES = (
    PRIVATE_CI_CACHE_BUDGET.nightly_roster_max_shard_bytes
)
NIGHTLY_ROSTER_MAX_TOTAL_BYTES = (
    PRIVATE_CI_CACHE_BUDGET.nightly_roster_max_total_bytes
)
_ROSTER_PIPELINE_KEYS = frozenset({"amd", "upstream"})
_ROSTER_SHARD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d+)\.json$")
_ROSTER_ENVELOPE_FIELDS = frozenset({"schema_version", "build"})
_ROSTER_BUILD_FIELDS = frozenset({"number", "created_at", "jobs"})
_ROSTER_JOB_FIELDS = frozenset(
    {
        "type",
        "id",
        "name",
        "state",
        "soft_failed",
        "step_key",
        "retried_in_job_id",
    }
)
_ROSTER_MAX_TIMESTAMP_CHARS = 64
_ROSTER_MAX_JOB_ID_CHARS = 256
_ROSTER_MAX_JOB_NAME_CHARS = 2048
_ROSTER_MAX_JOB_STATE_CHARS = 64
PAGINATION_SAFETY_CAP = 100


class NightlyRosterCacheError(ValueError):
    """The private nightly-roster tree is unsafe or violates its contract."""


def _headers() -> dict:
    token = cfg.BK_TOKEN
    if not token:
        raise RuntimeError(
            "BUILDKITE_TOKEN not set. Configure it via GitHub Actions secrets or export it locally."
        )
    return {"Authorization": f"Bearer {token}"}


def _request(url: str, params: Optional[dict] = None) -> requests.Response:
    """Make a GET request with retry on transient and rate-limit errors."""
    headers = _headers()
    for attempt in range(1, cfg.MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code == 429:
                # Rate limited — use Retry-After header or exponential backoff
                retry_after = int(resp.headers.get("Retry-After", cfg.RETRY_BACKOFF * attempt))
                if attempt < cfg.MAX_RETRIES:
                    log.warning(
                        "Rate limited (429), retry %d/%d in %ds",
                        attempt, cfg.MAX_RETRIES, retry_after,
                    )
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
            if resp.status_code in cfg.RETRY_CODES and attempt < cfg.MAX_RETRIES:
                wait = cfg.RETRY_BACKOFF * attempt
                log.warning(
                    "HTTP %d on %s, retry %d/%d in %ds",
                    resp.status_code, url, attempt, cfg.MAX_RETRIES, wait,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.exceptions.Timeout:
            if attempt < cfg.MAX_RETRIES:
                log.warning("Timeout on %s, retry %d/%d", url, attempt, cfg.MAX_RETRIES)
                time.sleep(cfg.RETRY_BACKOFF * attempt)
                continue
            raise
    return resp  # should not reach here


def _pagination_url_identity(url: str) -> tuple[tuple[str, str, int], str, str]:
    """Return a normalized origin and request identity for a pagination URL."""
    if not isinstance(url, str) or not url or url.strip() != url:
        raise RuntimeError("Buildkite pagination returned a malformed URL")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("Buildkite pagination returned a malformed URL") from exc
    scheme = parsed.scheme.casefold()
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise RuntimeError("Buildkite pagination returned a malformed URL")
    if port is None:
        port = 443 if scheme == "https" else 80
    origin = (scheme, hostname.casefold(), port)
    return origin, parsed.path or "/", parsed.query


def _paginate(
    url: str,
    params: Optional[dict] = None,
    *,
    max_pages: int = PAGINATION_SAFETY_CAP,
) -> list:
    """Fetch bounded, same-origin pages from a Buildkite endpoint."""
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max_pages must be a positive integer")

    expected_origin, expected_path, _ = _pagination_url_identity(url)
    results = []
    params = dict(params or {})
    seen_urls: set[tuple[tuple[str, str, int], str, str]] = set()
    current_url = url

    for page in range(1, max_pages + 1):
        identity = _pagination_url_identity(current_url)
        if identity[0] != expected_origin:
            raise RuntimeError("Buildkite pagination refused a cross-origin next URL")
        if identity[1] != expected_path:
            raise RuntimeError("Buildkite pagination refused a different endpoint path")
        if identity in seen_urls:
            raise RuntimeError("Buildkite pagination returned a repeated next URL")
        seen_urls.add(identity)

        resp = _request(current_url, params=params if page == 1 else None)
        payload = resp.json()
        if not isinstance(payload, list):
            raise RuntimeError("Buildkite pagination expected each page to be a JSON list")
        results.extend(payload)

        links = resp.links
        if not isinstance(links, dict):
            raise RuntimeError("Buildkite pagination returned malformed Link metadata")
        if "next" not in links:
            return results
        next_link = links["next"]
        if (
            not isinstance(next_link, dict)
            or not isinstance(next_link.get("url"), str)
            or not next_link["url"].strip()
        ):
            raise RuntimeError("Buildkite pagination returned malformed next Link metadata")

        next_url = urljoin(current_url, next_link["url"].strip())
        next_identity = _pagination_url_identity(next_url)
        if next_identity[0] != expected_origin:
            raise RuntimeError("Buildkite pagination refused a cross-origin next URL")
        if next_identity[1] != expected_path:
            raise RuntimeError("Buildkite pagination refused a different endpoint path")
        if next_identity in seen_urls:
            raise RuntimeError("Buildkite pagination returned a repeated next URL")
        if page == max_pages:
            raise RuntimeError(
                f"Buildkite pagination exceeded its {max_pages}-page safety cap"
            )
        current_url = next_url

    raise AssertionError("unreachable Buildkite pagination state")


def _parse_roster_created_at(value: object) -> datetime | None:
    """Return a UTC timestamp accepted by the private roster schema."""
    if not isinstance(value, str) or not value or len(value) > _ROSTER_MAX_TIMESTAMP_CHARS:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _bounded_roster_text(value: object, *, max_chars: int) -> str | None:
    """Accept only bounded strings; never stringify arbitrary API objects."""
    if not isinstance(value, str) or not value or len(value) > max_chars:
        return None
    return value


def _project_nightly_roster_job(job: object) -> dict | None:
    """Project one Buildkite job onto the exact persistent-cache allowlist."""
    if not isinstance(job, dict) or job.get("type") != "script":
        return None
    state = _bounded_roster_text(
        job.get("state"),
        max_chars=_ROSTER_MAX_JOB_STATE_CHARS,
    )
    if state is None:
        return None

    projected = {"type": "script", "state": state}
    for key, limit in (
        ("id", _ROSTER_MAX_JOB_ID_CHARS),
        ("name", _ROSTER_MAX_JOB_NAME_CHARS),
        ("step_key", _ROSTER_MAX_JOB_NAME_CHARS),
        ("retried_in_job_id", _ROSTER_MAX_JOB_ID_CHARS),
    ):
        value = _bounded_roster_text(job.get(key), max_chars=limit)
        if value is not None:
            projected[key] = value
    if isinstance(job.get("soft_failed"), bool):
        projected["soft_failed"] = job["soft_failed"]
    return projected


def _project_nightly_roster_build(build: object) -> dict | None:
    """Return the only build/job fields permitted in persistent roster data.

    Buildkite responses can contain environment variables, creator metadata,
    agent metadata, commands, signed URLs, and future fields we have not
    reviewed.  An allowlist projection is intentionally used instead of a
    denylist scrub so none of those values can reach disk by default.
    """
    if not isinstance(build, dict):
        return None
    number = build.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        return None
    created_at = _parse_roster_created_at(build.get("created_at"))
    if created_at is None:
        return None

    raw_jobs = build.get("jobs")
    jobs = []
    if isinstance(raw_jobs, list):
        for raw_job in raw_jobs:
            projected_job = _project_nightly_roster_job(raw_job)
            if projected_job is not None:
                jobs.append(projected_job)

    normalized_created_at = created_at.isoformat().replace("+00:00", "Z")
    return {
        "number": number,
        "created_at": normalized_created_at,
        "jobs": jobs,
    }


def _decode_nightly_roster_payload(payload: object) -> dict | None:
    """Validate a v2 shard without accepting any unapproved field."""
    if not isinstance(payload, dict) or set(payload) != _ROSTER_ENVELOPE_FIELDS:
        return None
    if payload.get("schema_version") != NIGHTLY_ROSTER_CACHE_SCHEMA_VERSION:
        return None
    raw_build = payload.get("build")
    if not isinstance(raw_build, dict) or set(raw_build) != _ROSTER_BUILD_FIELDS:
        return None
    projected = _project_nightly_roster_build(raw_build)
    if projected is None or projected != raw_build:
        return None
    if any(
        not isinstance(job, dict) or not set(job).issubset(_ROSTER_JOB_FIELDS)
        for job in raw_build["jobs"]
    ):
        return None
    return projected


def _roster_root(cache_dir: Path) -> Path:
    cache_dir = Path(cache_dir)
    if cache_dir.is_symlink() or (cache_dir.exists() and not cache_dir.is_dir()):
        raise NightlyRosterCacheError("nightly roster cache parent is unsafe")
    root = cache_dir / NIGHTLY_ROSTER_CACHE_DIR
    if root.name != NIGHTLY_ROSTER_CACHE_DIR:
        raise NightlyRosterCacheError("nightly roster cache root is unsafe")
    return root


def _remove_roster_entry(path: Path, root: Path) -> None:
    """Remove one exact cache entry without following symlinks."""
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise NightlyRosterCacheError("nightly roster cleanup escaped its root") from exc
    if not relative.parts:
        raise NightlyRosterCacheError("nightly roster cleanup cannot remove its root")
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if not path.exists():
        return
    if not path.is_dir():
        raise NightlyRosterCacheError("nightly roster cache contains a special file")
    for child in list(path.iterdir()):
        _remove_roster_entry(child, root)
    path.rmdir()


def _validated_roster_shard(
    path: Path,
    pipeline_key: str,
    *,
    cutoff_date,
    anchor: datetime,
) -> tuple[str, int] | None:
    """Return a valid shard's date and size, or ``None`` for invalid state."""
    if path.is_symlink() or not path.is_file():
        return None
    match = _ROSTER_SHARD_RE.fullmatch(path.name)
    if match is None:
        return None
    try:
        shard_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        size = path.stat().st_size
    except (OSError, ValueError):
        return None
    if (
        shard_date < cutoff_date
        or shard_date > anchor.date()
        or size <= 0
        or size > NIGHTLY_ROSTER_MAX_SHARD_BYTES
    ):
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    build = _decode_nightly_roster_payload(payload)
    if build is None:
        return None
    created_at = _parse_roster_created_at(build["created_at"])
    if created_at is None or created_at > anchor:
        return None
    if (
        match.group(1) != build["created_at"][:10]
        or int(match.group(2)) != build["number"]
    ):
        return None
    return match.group(1), size


def _inspect_nightly_roster_cache(
    cache_dir: Path,
    *,
    anchor: datetime,
    repair: bool,
) -> dict[str, int]:
    """Validate, optionally repair, and globally bound the private cache tree."""
    root = _roster_root(cache_dir)
    anchor = anchor.astimezone(timezone.utc)
    cutoff_date = (
        anchor.date() - timedelta(days=NIGHTLY_ROSTER_RETENTION_DAYS - 1)
    )
    # ``Path.exists`` is false for a broken symlink, so test the link first.
    # Otherwise final upload validation could mistake an unsafe restored root
    # for an ordinary missing cache.
    if root.is_symlink():
        raise NightlyRosterCacheError("nightly roster cache root is not a directory")
    if not root.exists():
        return {"shards": 0, "bytes": 0}
    if not root.is_dir():
        raise NightlyRosterCacheError("nightly roster cache root is not a directory")

    valid: list[tuple[Path, str, str, int]] = []
    for entry in list(root.iterdir()):
        if (
            entry.name not in _ROSTER_PIPELINE_KEYS
            or entry.is_symlink()
            or not entry.is_dir()
        ):
            if not repair:
                raise NightlyRosterCacheError("nightly roster cache contains an unexpected path")
            _remove_roster_entry(entry, root)
            continue
        for shard in list(entry.iterdir()):
            descriptor = _validated_roster_shard(
                shard,
                entry.name,
                cutoff_date=cutoff_date,
                anchor=anchor,
            )
            if descriptor is None:
                if not repair:
                    raise NightlyRosterCacheError("nightly roster cache contains an invalid shard")
                _remove_roster_entry(shard, root)
                continue
            shard_date, size = descriptor
            valid.append((shard, entry.name, shard_date, size))

    total = sum(size for _, _, _, size in valid)
    if total > NIGHTLY_ROSTER_MAX_TOTAL_BYTES and not repair:
        raise NightlyRosterCacheError("nightly roster cache exceeds its total size limit")
    for path, _, _, size in sorted(
        valid,
        key=lambda row: (row[2], row[1], row[0].name),
    ):
        if total <= NIGHTLY_ROSTER_MAX_TOTAL_BYTES:
            break
        _remove_roster_entry(path, root)
        total -= size
    return {
        "shards": sum(path.exists() for path, _, _, _ in valid),
        "bytes": total,
    }


def validate_nightly_roster_cache(
    cache_dir: Path,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Strict final upload-boundary validation for the entire saved tree."""
    clock = now or datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None:
        raise ValueError("nightly roster cache clock must be timezone-aware")
    return _inspect_nightly_roster_cache(cache_dir, anchor=clock, repair=False)


def _remove_legacy_nightly_roster_cache(pipeline_key: str, cache_dir: Path) -> None:
    """Remove only recognized legacy cache files below exact scoped paths."""
    monolith = cache_dir / f"builds_{pipeline_key}.json"
    if monolith.is_file() or monolith.is_symlink():
        monolith.unlink(missing_ok=True)

    for legacy_name in LEGACY_NIGHTLY_ROSTER_CACHE_DIRS:
        legacy_root = cache_dir / legacy_name
        if legacy_root.is_symlink():
            legacy_root.unlink(missing_ok=True)
            continue
        legacy_pipeline = legacy_root / pipeline_key
        if legacy_pipeline.is_symlink():
            legacy_pipeline.unlink(missing_ok=True)
        elif legacy_pipeline.is_dir():
            for path in legacy_pipeline.iterdir():
                if _ROSTER_SHARD_RE.fullmatch(path.name) and (
                    path.is_file() or path.is_symlink()
                ):
                    path.unlink(missing_ok=True)
            try:
                legacy_pipeline.rmdir()
            except OSError:
                pass
        try:
            legacy_root.rmdir()
        except OSError:
            pass


def write_nightly_build_cache(
    pipeline_key: str,
    builds: list[dict],
    cache_dir: Path,
    *,
    now: datetime | None = None,
) -> Path:
    """Persist one bounded, allowlisted roster shard per nightly build.

    Per-build shards avoid rewriting an ever-growing cache blob and let old
    rosters be removed independently.  This directory is private Actions cache
    state below the repository's gitignored ``.cache`` boundary.
    """
    if pipeline_key not in _ROSTER_PIPELINE_KEYS:
        raise NightlyRosterCacheError("nightly roster cache pipeline is invalid")
    wall_clock = now or datetime.now(timezone.utc)
    if not isinstance(wall_clock, datetime) or wall_clock.tzinfo is None:
        raise ValueError("nightly roster cache clock must be timezone-aware")
    wall_clock = wall_clock.astimezone(timezone.utc)
    cutoff_date = (
        wall_clock.date() - timedelta(days=NIGHTLY_ROSTER_RETENTION_DAYS - 1)
    )
    roster_root = _roster_root(cache_dir)
    projected_builds: list[tuple[dict, datetime, bytes]] = []
    for raw_build in builds:
        build = _project_nightly_roster_build(raw_build)
        if build is None:
            log.warning("Skipping invalid nightly roster cache row")
            continue
        parsed = _parse_roster_created_at(build["created_at"])
        assert parsed is not None
        if parsed > wall_clock or parsed.date() < cutoff_date:
            log.warning(
                "Skipping out-of-window nightly roster cache row for %s build %d",
                pipeline_key,
                build["number"],
            )
            continue
        payload = {
            "schema_version": NIGHTLY_ROSTER_CACHE_SCHEMA_VERSION,
            "build": build,
        }
        serialized = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8") + b"\n"
        if len(serialized) > NIGHTLY_ROSTER_MAX_SHARD_BYTES:
            log.warning(
                "Skipping oversized nightly roster cache shard for %s build %d (%d bytes)",
                pipeline_key,
                build["number"],
                len(serialized),
            )
            continue
        projected_builds.append((build, parsed, serialized))

    # Retention and future-date checks are always anchored to a frozen wall
    # clock, never to API-provided build timestamps. A poisoned future row must
    # not legitimize itself or evict otherwise current restored shards.
    anchor = wall_clock
    # This is the only repair boundary for restored roster state. Every entry
    # below the exact versioned root is cache-owned and may be removed without
    # following symlinks; anything outside that root is never touched.
    _inspect_nightly_roster_cache(cache_dir, anchor=anchor, repair=True)
    roster_root.mkdir(parents=True, exist_ok=True)
    if roster_root.is_symlink() or not roster_root.is_dir():
        raise NightlyRosterCacheError("nightly roster cache root is unsafe")
    shard_dir = roster_root / pipeline_key
    shard_dir.mkdir(exist_ok=True)
    if shard_dir.is_symlink() or not shard_dir.is_dir():
        raise NightlyRosterCacheError("nightly roster pipeline directory is unsafe")

    for build, parsed, serialized in projected_builds:
        number = build["number"]
        path = shard_dir / f"{parsed.date().isoformat()}_{number}.json"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=shard_dir,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    _inspect_nightly_roster_cache(cache_dir, anchor=anchor, repair=True)

    # v1 used a denylist scrub and could retain unreviewed Buildkite fields.
    # Never migrate it into v2; remove only explicitly recognized legacy files.
    _remove_legacy_nightly_roster_cache(pipeline_key, cache_dir)
    return shard_dir


def _load_nightly_build_cache(
    pipeline_key: str,
    cache_dir: Path,
    *,
    now: datetime,
) -> dict[int, dict]:
    """Load only exact-schema v2 shards; legacy caches are never trusted."""
    # Validate the entire upload tree before consuming any shard. This keeps a
    # future, oversized, or unexpected restored entry from influencing API-skip
    # decisions even though the later writer could safely repair it on disk.
    validate_nightly_roster_cache(cache_dir, now=now)
    cached: dict[int, dict] = {}
    roster_root = cache_dir / NIGHTLY_ROSTER_CACHE_DIR
    shard_dir = roster_root / pipeline_key
    if (
        roster_root.is_dir()
        and not roster_root.is_symlink()
        and shard_dir.is_dir()
        and not shard_dir.is_symlink()
    ):
        total = 0
        for path in sorted(shard_dir.glob("*.json"), reverse=True):
            if path.is_symlink() or _ROSTER_SHARD_RE.fullmatch(path.name) is None:
                continue
            try:
                size = path.stat().st_size
                total += size
                if (
                    size > NIGHTLY_ROSTER_MAX_SHARD_BYTES
                    or total > NIGHTLY_ROSTER_MAX_TOTAL_BYTES
                ):
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                build = _decode_nightly_roster_payload(payload)
                if build is None:
                    continue
                number = build["number"]
                match = _ROSTER_SHARD_RE.fullmatch(path.name)
                assert match is not None
                if (
                    match.group(1) != build["created_at"][:10]
                    or int(match.group(2)) != number
                ):
                    continue
                cached.setdefault(number, build)
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
    return cached


# ---------------------------------------------------------------------------
# Build fetching
# ---------------------------------------------------------------------------

def fetch_nightly_builds(
    pipeline_key: str,
    days: int = 8,
    cache_dir: Optional[Path] = None,
    cache_errors: list[str] | None = None,
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Fetch nightly builds for a pipeline, filtering by name pattern.

    Args:
        pipeline_key: Key into config.PIPELINES ("amd" or "upstream")
        days: How many days back to look
        cache_dir: Optional directory for caching build data

    Returns:
        List of build dicts matching the nightly pattern, sorted newest-first.
    """
    pipeline = cfg.PIPELINES[pipeline_key]
    slug = pipeline["slug"]
    branch = pipeline["branch"]
    name_re = re.compile(pipeline["name_pattern"], re.IGNORECASE)

    collection_clock = now or datetime.now(timezone.utc)
    if not isinstance(collection_clock, datetime) or collection_clock.tzinfo is None:
        raise ValueError("nightly build collection clock must be timezone-aware")
    collection_clock = collection_clock.astimezone(timezone.utc)
    created_from = collection_clock - timedelta(days=days)

    # Check cache for already-fetched builds
    cached_builds = {}
    if cache_dir:
        try:
            cached_builds = _load_nightly_build_cache(
                pipeline_key,
                cache_dir,
                now=collection_clock,
            )
        except (OSError, ValueError) as exc:
            if cache_errors is not None:
                cache_errors.append(f"load_{type(exc).__name__}")
            log.warning(
                "Ignoring unavailable private nightly roster cache for %s (%s)",
                pipeline_key,
                type(exc).__name__,
            )

    url = f"{cfg.BK_API_BASE}/organizations/{cfg.BK_ORG}/pipelines/{slug}/builds"
    params = {
        "branch": branch,
        "created_from": created_from.isoformat(),
        "per_page": 100,
        # Discovery only needs the build message/state/timestamps. Downloading
        # every embedded job for hundreds of non-nightly upstream builds made
        # this metadata query take minutes. Selected nightlies are hydrated by
        # ``fetch_build_detail`` when their roster is actually needed.
        "exclude_jobs": "true",
        "exclude_pipeline": "true",
    }

    all_builds = _paginate(url, params)
    log.info("Fetched %d total builds from %s/%s", len(all_builds), cfg.BK_ORG, slug)

    # Filter to nightly builds by name pattern
    nightly_builds = []
    for build in all_builds:
        msg = build.get("message", "") or ""
        if name_re.search(msg):
            build_num = build["number"]
            # Preserve cached job detail, but never replace fresh API metadata
            # wholesale. A cached build may still say ``running`` after the
            # live summary has become terminal, or omit a late soft-fail job.
            if (
                build_num in cached_builds
                and build.get("state") in cfg.TERMINAL_STATES
            ):
                merged = dict(build)
                cached_jobs = cached_builds[build_num].get("jobs")
                if not merged.get("jobs") and isinstance(cached_jobs, list):
                    merged["jobs"] = cached_jobs
                nightly_builds.append(merged)
            else:
                nightly_builds.append(build)

    # Sort newest first
    nightly_builds.sort(key=lambda b: b.get("created_at", ""), reverse=True)

    # Update private, PII-scrubbed Actions cache shards.
    if cache_dir:
        try:
            write_nightly_build_cache(
                pipeline_key,
                nightly_builds,
                cache_dir,
                now=collection_clock,
            )
        except (OSError, ValueError) as exc:
            if cache_errors is not None:
                cache_errors.append(f"write_{type(exc).__name__}")
            log.warning(
                "Could not update optional private nightly roster cache for %s (%s)",
                pipeline_key,
                type(exc).__name__,
            )

    log.info("Found %d nightly builds for %s", len(nightly_builds), pipeline_key)
    return nightly_builds


def create_build(
    pipeline_slug: str,
    commit: str,
    branch: str,
    message: str,
    env: Optional[dict] = None,
    clean_checkout: bool = False,
    author_name: Optional[str] = None,
    author_email: Optional[str] = None,
) -> dict:
    """Create a new Buildkite build via POST /builds.

    See https://buildkite.com/docs/apis/rest-api/builds#create-a-build.
    ``pipeline_slug`` is passed directly (not keyed through PIPELINES) so this
    works for user-scheduled builds against any pipeline in the configured org.
    """
    token = cfg.BK_TOKEN
    if not token:
        raise RuntimeError("BUILDKITE_TOKEN not set.")
    url = f"{cfg.BK_API_BASE}/organizations/{cfg.BK_ORG}/pipelines/{pipeline_slug}/builds"
    body = {
        "commit": commit or "HEAD",
        "branch": branch or "main",
        "message": message or "Test build (project-dashboard)",
        "env": env or {},
        "clean_checkout": bool(clean_checkout),
    }
    if author_name or author_email:
        body["author"] = {k: v for k, v in (("name", author_name), ("email", author_email)) if v}
    resp = requests.post(
        url,
        headers={**_headers(), "Content-Type": "application/json"},
        data=json.dumps(body),
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Buildkite create build failed: {resp.status_code} {resp.text[:400]}")
    return resp.json()


def fetch_build_detail(pipeline_key: str, build_number: int) -> dict:
    """Fetch a single build with full job details."""
    slug = cfg.PIPELINES[pipeline_key]["slug"]
    url = (
        f"{cfg.BK_API_BASE}/organizations/{cfg.BK_ORG}"
        f"/pipelines/{slug}/builds/{build_number}"
    )
    resp = _request(
        url,
        {
            "include_retried_jobs": "true",
            "exclude_pipeline": "true",
        },
    )
    return resp.json()


def fetch_build_jobs(build: dict) -> list[dict]:
    """Extract script-type jobs from a build dict.

    Filters to jobs that actually run test commands (type=script),
    excluding wait steps, trigger steps, etc.
    Only returns terminal jobs (with logs/artifacts available for parsing).

    Retried (superseded) jobs are excluded so that only the latest
    attempt per step is returned.  Buildkite marks superseded jobs by
    setting ``retried_in_job_id`` on the old attempt.
    """
    jobs = build.get("jobs", [])
    return [
        j for j in jobs
        if j.get("type") == "script"
        and j.get("state") in cfg.TERMINAL_STATES
        and not j.get("retried_in_job_id")
    ]


# ---------------------------------------------------------------------------
# Artifact fetching
# ---------------------------------------------------------------------------

def fetch_build_artifacts(
    pipeline_key: str,
    build_number: int,
) -> dict[str, list[dict]]:
    """List all artifacts for a build, grouped by job_id, filtered to XML.

    Uses the build-level artifacts endpoint (one paginated call) instead of
    per-job requests to avoid rate limiting on builds with 200+ jobs.

    Returns:
        Dict mapping job_id -> list of XML artifact dicts.
    """
    slug = cfg.PIPELINES[pipeline_key]["slug"]
    url = (
        f"{cfg.BK_API_BASE}/organizations/{cfg.BK_ORG}"
        f"/pipelines/{slug}/builds/{build_number}/artifacts"
    )
    all_artifacts = _paginate(url, {"per_page": 100})
    by_job: dict[str, list[dict]] = {}
    for a in all_artifacts:
        if a.get("filename", "").endswith(".xml"):
            job_id = a.get("job_id", "")
            by_job.setdefault(job_id, []).append(a)
    return by_job


def fetch_job_artifacts(
    pipeline_key: str,
    build_number: int,
    job_id: str,
) -> list[dict]:
    """List artifacts for a specific job, filtered to XML files."""
    slug = cfg.PIPELINES[pipeline_key]["slug"]
    url = (
        f"{cfg.BK_API_BASE}/organizations/{cfg.BK_ORG}"
        f"/pipelines/{slug}/builds/{build_number}"
        f"/jobs/{job_id}/artifacts"
    )
    artifacts = _paginate(url)
    return [
        a for a in artifacts
        if a.get("filename", "").endswith(".xml")
    ]


def download_artifact(artifact: dict) -> Optional[bytes]:
    """Download an artifact's content.

    The artifact dict should have a 'download_url' field from the artifacts API.
    """
    download_url = artifact.get("download_url")
    if not download_url:
        log.warning("No download_url for artifact %s", artifact.get("id"))
        return None

    try:
        resp = _request(download_url)
        return resp.content
    except BuildkiteRequestGuardError:
        raise
    except Exception as e:
        log.warning("Failed to download artifact %s: %s", artifact.get("filename"), e)
        return None
