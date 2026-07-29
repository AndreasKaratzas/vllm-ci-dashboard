#!/usr/bin/env python3
"""Sync AMD nightly failures into one upstream tracking issue.

For every AMD test group that is currently failing in the most recent nightly,
this script derives summary metrics and writes them to
``data/vllm/ci/ready_tickets.json`` for the dashboard.

Live mode has exactly one upstream write capability: update one pinned,
pre-existing comment on a validated umbrella issue. Individual issues and
project fields are read-only. This module cannot create, close, reopen,
relabel, reassign, or rewrite any issue, including the umbrella issue itself.

A 2-month Buildkite backfill is done from the on-disk nightly JSONLs in
``data/vllm/ci/test_results/*_amd.jsonl`` — so we can report first-failure,
last-successful, and break-frequency metrics on the dashboard without extra
API calls.

Defaults to **dry-run**. Dry-run writes a plan to
``data/vllm/ci/ready_tickets.json`` so the dashboard shows the same grouped
data it would publish live. Live mode updates the managed umbrella comment and
then writes the resulting comment metadata back into the same JSON so the
dashboard stays in sync.

Env:
  PROJECTS_READ_TOKEN  optional read-only token used for Projects V2 evidence;
                  public projects are read anonymously when it is absent.
  UPSTREAM_COMMENT_TOKEN  environment-protected token used only to update the
                  pinned umbrella comment. It is never passed to collectors.
  READY_TICKETS_LIVE  ``"1"`` → request the scoped live write; anything
                  else → dry run.
  READY_TICKETS_ALLOW_UPSTREAM_WRITES  second explicit ack required for live
                  mutation. Without this the script refuses to touch upstream
                  issues even if ``READY_TICKETS_LIVE=1`` and a token exists.
  READY_TICKETS_WRITE_SCOPE  must equal ``"master_comment_only"``. Any other
                  value fails closed before an upstream write is attempted.
  READY_TICKETS_REQUIRE_PROJECT_REFRESH  ``"1"`` makes a failed Projects
                  snapshot refresh fail the run after preserving the prior
                  snapshot. Scheduled workflows should enable this.
  GITHUB_RUN_ID   link-back URL for generated diagnostics, set by Actions.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_DIR = ROOT / "data" / "vllm" / "ci" / "test_results"
OUT = ROOT / "data" / "vllm" / "ci" / "ready_tickets.json"
STATE = ROOT / "data" / "vllm" / "ci" / "ready_tickets_state.json"
# Snapshot of every item on project #39 (issue_number → {status, title, url}).
# Refreshed from the public Projects REST API whenever the workflow requests
# live mode, including its forced read-only fallback. The dashboard uses it to
# render the current column (Backlog / Ready / In Progress / In Review / Done)
# next to each tracked CI-failure issue.
PROJECT_ITEMS_OUT = ROOT / "data" / "vllm" / "ci" / "project_items.json"

# The Projects V2 board the team uses for triage.
PROJECT_ORG = "vllm-project"
PROJECT_NUMBER = 39
ISSUE_REPO = "vllm-project/vllm"
LABEL = "ci-failure"
ISSUE_MODE = "single_master"
MASTER_ISSUE_NUMBER = 40554
MASTER_ISSUE_TITLE = "[AMD][CI Failure][Tracker] Static dashboard tracker for current CI failures"
MASTER_ISSUE_URL = f"https://github.com/{ISSUE_REPO}/issues/{MASTER_ISSUE_NUMBER}"
MASTER_COMMENT_MARKER = "<!-- ready-tickets-master-comment -->"
MASTER_COMMENT_ID = 4291606592
MASTER_ISSUE_OWNER = "AndreasKaratzas"
MASTER_COMMENT_OWNER = "AndreasKaratzas"
MASTER_ISSUE_BODY_SENTINEL = "single dashboard-managed umbrella issue"
MASTER_COMMENT_WRITE_SCOPE = "master_comment_only"

# 2-month backfill window for break-frequency / first-failure metrics.
BACKFILL_DAYS = 60

GH_API = "https://api.github.com"
PROJECTS_REST_API_VERSION = "2026-03-10"
TEST_AMD_YAML_URL = (
    "https://raw.githubusercontent.com/vllm-project/vllm/main/.buildkite/test-amd.yaml"
)
PAUSE_REASON = (
    "Ready Tickets upstream writes are paused. Live mode requires the exact "
    "master_comment_only scope; individual issues and project fields are "
    "always read-only."
)


# ---------------------------------------------------------------------------
# Shard template discovery (Buildkite %N parallelism)
# ---------------------------------------------------------------------------

def _fetch_shard_templates() -> list[str]:
    """Return ``%N``-bearing labels from upstream ``test-amd.yaml``.

    Buildkite expands ``parallelism: N`` by substituting ``%N`` in the step
    label with 1..N, producing per-shard job names like ``Kernels MoE Test 1``
    / ``...Test 2``. Those shards are the same test group as far as triage
    is concerned — one ticket per template, not one per shard.

    Returns the raw templates (e.g. ``["Kernels MoE Test %N", ...]``) so the
    caller can match incoming job names against each template's regex. We
    authoritatively consult the YAML rather than stripping trailing integers
    heuristically — legitimate group names end in numbers (e.g. ``LoRA 4``
    when it *isn't* parallelized), and we can't tell them apart without the
    source of truth.

    Fetch failures degrade gracefully: return ``[]`` so grouping falls back
    to raw job names rather than blocking the sync.
    """
    try:
        resp = requests.get(TEST_AMD_YAML_URL, timeout=15)
        resp.raise_for_status()
        data = yaml.safe_load(resp.text)
    except Exception as e:
        log.warning("Could not fetch test-amd.yaml for shard templates: %s", e)
        return []
    if not isinstance(data, dict):
        return []
    templates: list[str] = []
    for step in data.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        label = step.get("label") or ""
        par = step.get("parallelism")
        try:
            par_int = int(par) if par is not None else 0
        except (TypeError, ValueError):
            par_int = 0
        if par_int > 1 and "%N" in label:
            templates.append(label)
    return templates


def _compile_shard_patterns(templates: list[str]) -> list[tuple[re.Pattern[str], str]]:
    """Compile ``(regex, template)`` pairs so the %N slot matches any integer."""
    compiled: list[tuple[re.Pattern[str], str]] = []
    for tpl in templates:
        pattern = re.escape(tpl).replace(re.escape("%N"), r"\d+")
        compiled.append((re.compile(f"^{pattern}$"), tpl))
    return compiled


def _canonicalize_shard(test_name: str, patterns: list[tuple[re.Pattern[str], str]]) -> str:
    """Collapse a shard-specific name back to its ``%N`` template, if any."""
    for pat, tpl in patterns:
        if pat.match(test_name):
            return tpl
    return test_name


# ---------------------------------------------------------------------------
# Local nightly parsing
# ---------------------------------------------------------------------------

def _group_key(
    job_name: str,
    shard_patterns: list[tuple[re.Pattern[str], str]] | None = None,
) -> str:
    """Agent-qualified job name, e.g. ``mi325_1: Quantized MoE Test (B200-MI325)``.

    vllm-project's CI-failure convention on project #39 is one ticket per
    ``{agent}: {test_name}`` pair — a test can be green on mi250 but broken on
    mi325, and the reviewer needs to see that split. So we key groups by the
    full job name, not by the HW-stripped test name.

    When ``shard_patterns`` is supplied (from ``_fetch_shard_templates`` +
    ``_compile_shard_patterns``), the per-shard suffix that Buildkite derived
    from ``%N`` is folded back to the template. So
    ``mi325_1: Kernels MoE Test 1..4`` all collapse to
    ``mi325_1: Kernels MoE Test %N`` — one ticket per template, matching
    the step defined in ``test-amd.yaml``.
    """
    name = (job_name or "").strip()
    if not name or not shard_patterns:
        return name
    if ": " in name:
        agent, test = name.split(": ", 1)
        canonical = _canonicalize_shard(test, shard_patterns)
        return f"{agent}: {canonical}"
    return _canonicalize_shard(name, shard_patterns)


def _is_failing(status: str) -> bool:
    return (status or "").lower() in ("failed", "error", "broken", "timed_out", "soft_failed")


def _load_nightly(date_file: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with date_file.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return rows


def _collect_group_history(
    days: int,
    shard_patterns: list[tuple[re.Pattern[str], str]] | None = None,
) -> dict:
    """Walk per-day nightly JSONLs, keyed by HW-stripped group name.

    Returns a dict keyed by group with per-date bucket status:
        { group: { "YYYY-MM-DD": {"pass": N, "fail": N, "hardware": {hw: status}} } }
    Only looks at AMD nightlies. Oldest first.

    Parallelized test steps (``parallelism: N`` with ``%N`` in the label) are
    collapsed to a single group so we don't file N tickets for what is
    fundamentally one test definition — the shard templates come from
    ``test-amd.yaml`` on upstream main, fetched once per run.
    """
    files = sorted(RESULTS_DIR.glob("*_amd.jsonl"))
    if not files:
        return {}
    today = datetime.now(timezone.utc).date()

    if shard_patterns is None:
        shard_patterns = _compile_shard_patterns(_fetch_shard_templates())

    per_group: dict[str, dict] = defaultdict(lambda: defaultdict(
        lambda: {"pass": 0, "fail": 0, "hardware": {}, "build_numbers": set(), "build_refs": set()}
    ))
    for f in files:
        # filenames are YYYY-MM-DD_amd.jsonl
        stem = f.stem.split("_")[0]
        try:
            d = datetime.strptime(stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - d).days > days:
            continue
        for row in _load_nightly(f):
            group = _group_key(
                row.get("job_name") or row.get("classname") or "",
                shard_patterns,
            )
            if not group:
                continue
            status = (row.get("status") or "").lower()
            bucket = per_group[group][stem]
            if _is_failing(status):
                bucket["fail"] += 1
            elif status in ("passed", "xpassed"):
                bucket["pass"] += 1
            hw = (row.get("classname") or "").split(": ", 1)[0]
            if hw:
                prior = bucket["hardware"].get(hw)
                if prior != "fail":
                    bucket["hardware"][hw] = "fail" if _is_failing(status) else (prior or "pass")
            if row.get("build_number"):
                build_number = int(row["build_number"])
                bucket["build_numbers"].add(build_number)
                pipeline = (row.get("pipeline") or "amd-ci").strip()
                build_url = _buildkite_build_url(pipeline, build_number)
                bucket["build_refs"].add((pipeline, build_number, build_url or ""))

    # Convert sets → sorted lists so JSON-serializable.
    result: dict[str, dict] = {}
    for g, dates in per_group.items():
        result[g] = {}
        for d, stats in dates.items():
            result[g][d] = {
                "pass": stats["pass"],
                "fail": stats["fail"],
                "hardware": stats["hardware"],
                "build_numbers": sorted(stats["build_numbers"]),
                "build_refs": [
                    {
                        "pipeline": pipeline,
                        "build_number": build_number,
                        "url": url or _buildkite_build_url(pipeline, build_number) or "",
                    }
                    for pipeline, build_number, url in sorted(
                        stats["build_refs"], key=lambda ref: (ref[1], ref[0])
                    )
                ],
            }
    return result


def _latest_amd_build() -> tuple[str | None, int | None, list[dict]]:
    """Return ``(date, build_number, rows)`` for the newest AMD nightly build.

    ``test_results`` stores one JSONL per day, but a single day file can still
    contain rows from more than one build if a later recollection landed on the
    same calendar date. The master tracker comment should reflect the most
    recent build only, not "any build that happened on the latest date".
    """
    latest_date: str | None = None
    latest_build: int | None = None
    latest_rows: list[dict] = []

    for f in sorted(RESULTS_DIR.glob("*_amd.jsonl")):
        stem = f.stem.split("_")[0]
        try:
            file_date = datetime.strptime(stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        rows = _load_nightly(f)
        build_numbers: list[int] = []
        for row in rows:
            try:
                build_numbers.append(int(row.get("build_number")))
            except (TypeError, ValueError):
                continue
        if not build_numbers:
            continue
        file_build = max(build_numbers)
        if latest_date is None or (file_date.isoformat(), file_build) > (latest_date, latest_build or -1):
            latest_date = file_date.isoformat()
            latest_build = file_build
            latest_rows = []
            for row in rows:
                try:
                    if int(row.get("build_number")) == file_build:
                        latest_rows.append(row)
                except (TypeError, ValueError):
                    continue

    return latest_date, latest_build, latest_rows


def _collect_latest_failing_groups(
    shard_patterns: list[tuple[re.Pattern[str], str]] | None = None,
) -> tuple[str | None, int | None, dict[str, dict]]:
    """Return the normalized failing groups from the newest AMD nightly build.

    The historical summary view intentionally spans 60 days, but the live
    upstream tracker comment must mirror Buildkite's current nightly state.
    That means we only count groups that are failing in the newest AMD build,
    with Buildkite's ``%N`` shards collapsed back to one logical test group.
    """
    latest_date, latest_build, rows = _latest_amd_build()
    if latest_build is None:
        return latest_date, latest_build, {}

    out: dict[str, dict] = {}
    for row in rows:
        status = (row.get("status") or "").lower()
        if not _is_failing(status):
            continue
        group = _group_key(
            row.get("job_name") or row.get("classname") or "",
            shard_patterns,
        )
        if not group:
            continue
        bucket = out.setdefault(group, {
            "hardware": {},
            "build_numbers": set(),
            "build_refs": set(),
        })
        hw = (group.split(": ", 1)[0] if ": " in group else "")
        if hw:
            bucket["hardware"][hw] = "fail"
        bucket["build_numbers"].add(latest_build)
        pipeline = (row.get("pipeline") or "amd-ci").strip()
        build_url = _buildkite_build_url(pipeline, latest_build)
        bucket["build_refs"].add((pipeline, latest_build, build_url or ""))

    result: dict[str, dict] = {}
    for group, bucket in out.items():
        result[group] = {
            "latest_date": latest_date,
            "hardware": bucket["hardware"],
            "build_numbers": sorted(bucket["build_numbers"]),
            "build_refs": [
                {
                    "pipeline": pipeline,
                    "build_number": build_number,
                    "url": url or _buildkite_build_url(pipeline, build_number) or "",
                }
                for pipeline, build_number, url in sorted(
                    bucket["build_refs"], key=lambda ref: (ref[1], ref[0])
                )
            ],
        }
    return latest_date, latest_build, result


def _summarize_group(group: str, history: dict[str, dict]) -> dict:
    """Derive first-failure / last-successful / break frequency from history."""
    dates_sorted = sorted(history.keys())
    first_failure: str | None = None
    last_success: str | None = None
    flips = 0
    prior_state: str | None = None  # "pass" | "fail"

    for d in dates_sorted:
        bucket = history[d]
        state: str | None = None
        if bucket["fail"]:
            state = "fail"
        elif bucket["pass"]:
            state = "pass"
        if state == "fail" and first_failure is None:
            first_failure = d
        if state == "pass":
            last_success = d
        if state and prior_state and state != prior_state:
            flips += 1
        if state:
            prior_state = state

    latest_date = dates_sorted[-1] if dates_sorted else None
    latest_failing = bool(latest_date and history[latest_date]["fail"])

    # If the group has since recovered, the "first_failure" of the current
    # streak is only meaningful if it extends to today. Walk backwards from
    # the latest date while the state is fail.
    current_streak_start: str | None = None
    if latest_failing:
        for d in reversed(dates_sorted):
            if history[d]["fail"]:
                current_streak_start = d
            elif history[d]["pass"]:
                break

    return {
        "group": group,
        "latest_date": latest_date,
        "currently_failing": latest_failing,
        "first_failure_in_window": first_failure,
        "current_streak_started": current_streak_start,
        "last_successful": last_success,
        "break_frequency": flips,
        "hardware_latest": history[latest_date]["hardware"] if latest_date else {},
        "builds_latest": history[latest_date]["build_numbers"] if latest_date else [],
        "build_refs_latest": history[latest_date].get("build_refs", []) if latest_date else [],
    }


# ---------------------------------------------------------------------------
# GitHub / Projects V2 helpers
# ---------------------------------------------------------------------------

def _rest_headers(token: str) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _project_rest_headers(token: str) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": PROJECTS_REST_API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _project_field_name(field: dict) -> str:
    value = field.get("value") or {}
    if not isinstance(value, dict):
        return ""
    name = value.get("name") or ""
    if isinstance(name, dict):
        return str(name.get("raw") or "")
    return str(name)


def _project_title_priority(item: dict) -> tuple[bool, int]:
    """Prefer an open duplicate title, then the highest issue number."""
    try:
        number = int(item.get("issueNumber") or 0)
    except (TypeError, ValueError):
        number = 0
    return str(item.get("issueState") or "").upper() == "OPEN", number


def _fetch_project_items_rest(
    token: str,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Read every item from the public Projects V2 REST API."""
    base_url = f"{GH_API}/orgs/{PROJECT_ORG}/projectsV2/{PROJECT_NUMBER}"
    headers = _project_rest_headers(token)
    fields: list[dict] = []
    fields_url: str | None = f"{base_url}/fields"
    fields_params: dict[str, int] | None = {"per_page": 100}
    seen_field_urls: set[str] = set()
    while fields_url:
        if fields_url in seen_field_urls:
            raise RuntimeError("Projects field pagination returned a repeated URL")
        seen_field_urls.add(fields_url)
        fields_response = requests.get(
            fields_url,
            headers=headers,
            params=fields_params,
            timeout=30,
        )
        fields_response.raise_for_status()
        field_page = fields_response.json()
        if not isinstance(field_page, list):
            raise RuntimeError("Projects fields response is not a list")
        fields.extend(field for field in field_page if isinstance(field, dict))
        fields_url = (
            str(((fields_response.links.get("next") or {}).get("url")) or "")
            or None
        )
        fields_params = None
    field_ids = {
        str(field.get("name") or ""): field.get("id")
        for field in fields
    }
    required_fields = ("Title", "Status")
    missing = [name for name in required_fields if not field_ids.get(name)]
    if missing:
        raise RuntimeError(
            f"Projects response is missing required fields: {', '.join(missing)}"
        )

    next_url: str | None = f"{base_url}/items"
    params: dict[str, str | int] | None = {
        "per_page": 100,
        "fields": ",".join(str(field_ids[name]) for name in required_fields),
    }
    items: list[dict] = []
    seen_urls: set[str] = set()
    while next_url:
        if next_url in seen_urls:
            raise RuntimeError("Projects item pagination returned a repeated URL")
        seen_urls.add(next_url)
        response = requests.get(
            next_url,
            headers=headers,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, list):
            raise RuntimeError("Projects items response is not a list")
        items.extend(item for item in page if isinstance(item, dict))
        next_url = str(((response.links.get("next") or {}).get("url")) or "") or None
        params = None

    by_title: dict[str, dict] = {}
    by_number: dict[str, dict] = {}
    for item in items:
        if item.get("content_type") != "Issue":
            continue
        content = item.get("content") or {}
        if not isinstance(content, dict):
            continue
        number = content.get("number")
        if number is None:
            continue
        title = str(content.get("title") or "")
        status = ""
        for field in item.get("fields") or []:
            if isinstance(field, dict) and field.get("name") == "Status":
                status = _project_field_name(field)
                break
        repository = content.get("repository") or {}
        if not isinstance(repository, dict):
            repository = {}
        normalized = {
            "itemId": item.get("node_id") or item.get("id"),
            "issueNumber": number,
            "issueState": str(content.get("state") or "").upper(),
            "status": status,
            "url": content.get("html_url") or "",
            "repo": repository.get("full_name") or "",
        }
        by_number[str(number)] = {
            "issue_number": number,
            "title": title,
            "status": normalized["status"],
            "issue_state": normalized["issueState"],
            "url": normalized["url"],
            "repo": normalized["repo"],
        }
        previous = by_title.get(title)
        if previous is None or _project_title_priority(
            normalized
        ) > _project_title_priority(previous):
            by_title[title] = normalized
    if not by_number:
        raise RuntimeError(
            f"Public project #{PROJECT_NUMBER} returned no issue items"
        )
    return by_title, by_number


def _write_project_items_snapshot(
    project_items_by_number: dict[str, dict],
    generated_at: str,
) -> None:
    snapshot = {
        "generated_at": generated_at,
        "project": f"{PROJECT_ORG}/projects/{PROJECT_NUMBER}",
        "project_url": f"https://github.com/orgs/{PROJECT_ORG}/projects/{PROJECT_NUMBER}",
        "items_by_number": project_items_by_number,
    }
    PROJECT_ITEMS_OUT.parent.mkdir(parents=True, exist_ok=True)
    PROJECT_ITEMS_OUT.write_text(json.dumps(snapshot, indent=2, sort_keys=True))


def _refresh_project_items_snapshot(
    token: str,
    generated_at: str,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Fetch and persist public Projects V2 evidence without mutating GitHub."""
    by_title, by_number = _fetch_project_items_rest(token)
    _write_project_items_snapshot(by_number, generated_at)
    return by_title, by_number


def _is_post_umbrella_project_issue(issue_number: int | str | None) -> bool:
    try:
        return int(issue_number) > int(MASTER_ISSUE_NUMBER)
    except (TypeError, ValueError):
        return False


def _filter_matchable_existing(existing: dict[str, dict]) -> dict[str, dict]:
    """Only adopt post-umbrella per-group issues from project #39.

    The static tracker issue is the only automation-owned upstream surface now.
    Any per-group work item we attach back onto a failing group must therefore
    be a newer, human-managed follow-up issue, not one of the legacy tickets
    that predate the tracker switch.
    """
    out: dict[str, dict] = {}
    for title, meta in (existing or {}).items():
        number = meta.get("issueNumber", meta.get("number"))
        if not _is_post_umbrella_project_issue(number):
            continue
        out[title] = meta
    return out


def _canonical_title(group: str) -> str:
    # Matches vllm-project's established CI-failure title scheme exactly, e.g.
    # ``[CI Failure]: mi325_1: Quantized MoE Test (B200-MI325)``. Title equality
    # is how we dedupe against the project board, so don't evolve this casually.
    return f"[CI Failure]: {group}"


_HW_PREFIX_RE = re.compile(r"^mi\d+_\d+:\s*", re.IGNORECASE)
_HW_PREFIX_CAPTURE_RE = re.compile(r"^(mi\d+_\d+):\s*", re.IGNORECASE)
_CI_PREFIX_RE = re.compile(r"^\[CI Failure\]:\s*", re.IGNORECASE)
_PULL_URL_RE = re.compile(r"https?://github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)", re.IGNORECASE)
_PR_CONTEXT_REF_RE = re.compile(
    r"(?i)\b(?:pr|pull request|expected to be solved after|solved after)\b[^\n#]{0,120}#(\d+)"
)


def _hw_prefix(title: str) -> str | None:
    """Return the ``mi{N}_{M}`` GPU-pool prefix from a title, or ``None``.

    Same test group (e.g. ``Kernels MoE Test %N``) runs on several pools
    (``mi250_1``, ``mi325_1``, ``mi355_1``) and each pool needs its own
    ticket — they fail and recover independently. Callers use this to
    reject a normalized-title match when the existing ticket's prefix
    disagrees with the incoming group's prefix.
    """
    s = _CI_PREFIX_RE.sub("", title or "")
    m = _HW_PREFIX_CAPTURE_RE.match(s)
    return m.group(1).lower() if m else None


def _normalized_match_compatible(existing_title: str, incoming_title: str) -> bool:
    """Guard the normalized-title fallback against cross-pool collapse.

    Normalization strips the HW prefix so that a hand-filed
    ``[CI Failure]: Transformers Nightly Models Test`` (no prefix) can
    match our synthesized ``[CI Failure]: mi325_1: Transformers ...``.
    But the same stripping makes ``mi325_1: Kernels MoE Test %N`` collide
    with ``mi355_1: Kernels MoE Test %N``, merging two pools' tickets
    onto whichever was filed first.

    Accept the match only when the existing ticket is HW-agnostic (the
    upstream case normalization was designed for) OR its HW prefix
    matches the incoming group's. Reject cross-pool matches — they need
    separate tickets.
    """
    existing_hw = _hw_prefix(existing_title)
    incoming_hw = _hw_prefix(incoming_title)
    if existing_hw is None:
        return True
    return existing_hw == incoming_hw


def _build_norm_index(titles) -> dict[str, list[str]]:
    """Group existing titles by their normalized key.

    Multiple existing titles can collide under the same normalized key —
    e.g. ``[CI Failure]: mi325_1: Kernels MoE Test %N`` and
    ``[CI Failure]:  mi355_1: Kernels MoE Test %N`` (note the double
    space — a hand-filed quirk) both normalize to ``kernels moe test``.
    Storing one-per-key with ``setdefault`` silently drops the other,
    and the caller ends up matching the incoming ticket against the
    wrong pool's existing title. Return every candidate so
    ``_pick_normalized_candidate`` can pick the one whose HW prefix
    matches the incoming group.
    """
    out: dict[str, list[str]] = {}
    for t in titles:
        n = _normalize_title(t)
        if n:
            out.setdefault(n, []).append(t)
    return out


def _pick_normalized_candidate(
    candidates: list[str], incoming_title: str
) -> str | None:
    """From multiple normalized-key collisions, pick the one compatible
    with ``incoming_title``'s HW prefix.

    Preference order:
      1. Exact HW-prefix match (e.g. existing mi355_1, incoming mi355_1).
      2. HW-agnostic existing (no prefix — hand-filed upstream ticket).
      3. No match — do not adopt a differently-pooled existing ticket.
    """
    if not candidates:
        return None
    incoming_hw = _hw_prefix(incoming_title)
    # Same-pool wins.
    for c in candidates:
        if _hw_prefix(c) == incoming_hw and incoming_hw is not None:
            return c
    # HW-agnostic existing (the original design case).
    for c in candidates:
        if _hw_prefix(c) is None:
            return c
    # Only differently-pooled existing candidates are left — reject.
    return None


def _normalize_title(title: str) -> str:
    """Strip decoration so plan entries match manually filed issue titles.

    Upstream has a habit of filing one ``[CI Failure]: Transformers Nightly
    Models Test`` with no HW prefix, while we'd synthesize
    ``[CI Failure]: mi325_1: Transformers Nightly Models Test``. Without
    a secondary normalized lookup we cheerfully duplicate their ticket.

    Normalization: drop ``[CI Failure]:`` prefix, drop ``mi{N}_{M}:``
    hardware prefix, drop trailing ``%N`` shard marker, collapse whitespace,
    lowercase. Deliberately conservative — only used as a *fallback* after
    exact-title match fails. The hardware compatibility check then prevents a
    closely named issue from being linked as evidence for the wrong group.
    """
    s = _CI_PREFIX_RE.sub("", title or "")
    s = _HW_PREFIX_RE.sub("", s)
    s = re.sub(r"\s+%N\s*$", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _buildkite_build_url(pipeline: str | None, build_number: int | str | None) -> str | None:
    pipeline_name = (pipeline or "").strip()
    if not pipeline_name or build_number in (None, ""):
        return None
    try:
        number = int(build_number)
    except (TypeError, ValueError):
        return None
    return f"https://buildkite.com/vllm/{pipeline_name}/builds/{number}"


def _format_build_refs(summary: dict, limit: int = 5) -> str:
    refs = summary.get("build_refs_latest") or []
    if refs:
        rendered: list[str] = []
        for ref in sorted(
            refs,
            key=lambda item: (int(item.get("build_number") or 0), item.get("pipeline") or ""),
            reverse=True,
        )[:limit]:
            build_number = ref.get("build_number")
            pipeline = (ref.get("pipeline") or "").strip()
            label = f"{pipeline or 'build'} #{build_number}"
            url = ref.get("url") or _buildkite_build_url(pipeline, build_number)
            rendered.append(f"[{label}]({url})" if url else label)
        if rendered:
            return ", ".join(rendered)
    builds = summary.get("builds_latest") or []
    return ", ".join(f"build #{n}" for n in builds[:limit]) or "—"


def _summary_arch(summary: dict) -> str:
    group = (summary.get("group") or "").strip()
    m = _HW_PREFIX_CAPTURE_RE.match(group)
    if not m:
        return "OTHER"
    return m.group(1).split("_", 1)[0].upper()


def _master_comment_body(failing: list[dict], run_url: str) -> str:
    latest_dates = sorted({s.get("latest_date") for s in failing if s.get("latest_date")})
    latest_date = latest_dates[-1] if latest_dates else "—"
    grouped: dict[str, list[dict]] = defaultdict(list)
    for summary in failing:
        grouped[_summary_arch(summary)].append(summary)

    lines = [
        MASTER_COMMENT_MARKER,
        "",
        "## AMD nightly CI summary",
        "",
        "This umbrella issue tracks every AMD nightly test group that is currently failing.",
        "The dashboard automation updates one managed comment three times per day; it does not mutate per-group upstream issues.",
        "",
        "### Snapshot",
        f"- Current failing groups: **{len(failing)}**",
        f"- Tracking window: **{BACKFILL_DAYS} days**",
        f"- Latest nightly date: **{latest_date}**",
        f"- Last sync: {run_url or 'manual run'}",
        "",
    ]

    if not failing:
        lines.extend([
            "### Current failing groups",
            "",
            "No AMD nightly test groups are currently failing.",
            "",
            "This issue stays open as the single source of truth for AMD nightly CI tracking.",
            "",
        ])
        return "\n".join(lines).rstrip() + "\n"

    lines.extend([
        "### Current failing groups",
        "",
        "Each section below is a current failure subtitle. The metrics mirror the dashboard’s ready-ticket summaries.",
        "",
    ])

    def _arch_sort_key(name: str) -> tuple[int, str]:
        m = re.search(r"(\d+)$", name)
        return (int(m.group(1)) if m else 9999, name)

    for arch in sorted(grouped.keys(), key=_arch_sort_key):
        lines.extend([f"### {arch}", ""])
        for summary in sorted(grouped[arch], key=lambda item: item["group"]):
            builds = _format_build_refs(summary)
            hw_status = ", ".join(
                f"`{hw}`={state}" for hw, state in sorted((summary.get("hardware_latest") or {}).items())
            ) or "—"
            lines.extend([
                f"#### `{summary['group']}`",
                "",
                f"- Current streak start: {summary.get('current_streak_started') or '—'}",
                f"- First failure in {BACKFILL_DAYS}d window: {summary.get('first_failure_in_window') or '—'}",
                f"- Last successful nightly: {summary.get('last_successful') or '—'}",
                f"- Break frequency ({BACKFILL_DAYS}d, pass↔fail flips): {summary.get('break_frequency', 0)}",
                f"- Latest nightly date: {summary.get('latest_date') or '—'}",
                f"- Latest build(s): {builds}",
                f"- Latest hardware status: {hw_status}",
                "",
            ])

    lines.extend([
        "---",
        "",
        "Auto-managed by `sync_ready_tickets.py` from the vLLM CI dashboard.",
        "",
    ])
    return "\n".join(lines).rstrip() + "\n"


def _expected_master_comment_id() -> int:
    """Validate that committed state still points at the pinned comment."""
    try:
        state = json.loads(STATE.read_text())
        master = state["master_issue"]
        issue_number = int(master["issue_number"])
        comment_id = int(master["comment_id"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Pinned umbrella-comment state is missing or invalid") from exc
    if issue_number != MASTER_ISSUE_NUMBER or comment_id != MASTER_COMMENT_ID:
        raise RuntimeError("Pinned umbrella-comment state does not match the write allowlist")
    return comment_id


def _retained_master_comment() -> dict | None:
    """Return the pinned umbrella-comment link without making a GitHub call."""
    try:
        state = json.loads(STATE.read_text())
        master = state["master_issue"]
        issue_number = int(master["issue_number"])
        comment_id = int(master["comment_id"])
        comment_url = str(master["comment_url"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    expected_url = f"{MASTER_ISSUE_URL}#issuecomment-{MASTER_COMMENT_ID}"
    if (
        issue_number != MASTER_ISSUE_NUMBER
        or comment_id != MASTER_COMMENT_ID
        or comment_url != expected_url
    ):
        return None
    return {"id": comment_id, "url": comment_url, "action": "retained"}


def _update_pinned_master_comment(
    token: str, *, body: str, expected_comment_id: int
) -> dict:
    _validate_master_issue_target(token)
    if int(expected_comment_id) != MASTER_COMMENT_ID:
        raise RuntimeError("Refusing to update an unapproved umbrella comment")
    comments = _issue_comments(token, ISSUE_REPO, MASTER_ISSUE_NUMBER)
    marked = [
        comment
        for comment in comments
        if MASTER_COMMENT_MARKER in (comment.get("body") or "")
    ]
    if len(marked) != 1:
        raise RuntimeError(
            "Expected exactly one managed umbrella comment; refusing to write"
        )
    existing = marked[0]
    existing_owner = ((existing.get("user") or {}).get("login") or "").strip()
    if int(existing.get("id") or 0) != expected_comment_id:
        raise RuntimeError("Managed marker is not on the pinned umbrella comment")
    if existing_owner != MASTER_COMMENT_OWNER:
        raise RuntimeError("Pinned umbrella comment is owned by another account")
    resp = requests.patch(
        f"{GH_API}/repos/{ISSUE_REPO}/issues/comments/{expected_comment_id}",
        headers=_rest_headers(token),
        json={"body": body},
        timeout=30,
    )
    action = "updated"
    if resp.status_code >= 400:
        log.error(
            "%s master issue comment on #%s returned %d: %s",
            action.upper(),
            MASTER_ISSUE_NUMBER,
            resp.status_code,
            resp.text[:500],
        )
    resp.raise_for_status()
    comment = resp.json()
    comment_id = int(comment.get("id") or 0)
    verified = _issue_comments(token, ISSUE_REPO, MASTER_ISSUE_NUMBER)
    verified_comment = next(
        (c for c in reversed(verified) if int(c.get("id") or 0) == comment_id),
        None,
    )
    verified_owner = (
        ((verified_comment or {}).get("user") or {}).get("login") or ""
    ).strip()
    if (
        not verified_comment
        or verified_comment.get("body") != body
        or verified_owner != MASTER_COMMENT_OWNER
    ):
        raise RuntimeError(
            "Master issue comment verification failed: GitHub did not persist "
            "the expected automation comment body"
        )
    return {
        "id": comment_id,
        "url": verified_comment.get("html_url") or comment.get("html_url"),
        "action": action,
    }


def _issue_details(token: str, repo_full_name: str, issue_number: int) -> dict:
    resp = requests.get(
        f"{GH_API}/repos/{repo_full_name}/issues/{issue_number}",
        headers=_rest_headers(token),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _validate_master_issue_target(token: str) -> dict:
    issue = _issue_details(token, ISSUE_REPO, MASTER_ISSUE_NUMBER)
    title = (issue.get("title") or "").strip()
    author = ((issue.get("user") or {}).get("login") or "").strip()
    body = issue.get("body") or ""

    problems = []
    if title != MASTER_ISSUE_TITLE:
        problems.append(
            f"title mismatch (expected {MASTER_ISSUE_TITLE!r}, got {title!r})"
        )
    if author != MASTER_ISSUE_OWNER:
        problems.append(
            f"owner mismatch (expected {MASTER_ISSUE_OWNER!r}, got {author!r})"
        )
    if MASTER_ISSUE_BODY_SENTINEL not in body:
        problems.append("body sentinel missing")
    if problems:
        raise RuntimeError(
            "Refusing to update the configured master issue because it no longer "
            "matches the dedicated dashboard-owned tracker: "
            + "; ".join(problems)
        )
    return issue


def _issue_comments(token: str, repo_full_name: str, issue_number: int) -> list[dict]:
    comments: list[dict] = []
    page = 1
    while True:
        resp = requests.get(
            f"{GH_API}/repos/{repo_full_name}/issues/{issue_number}/comments",
            headers=_rest_headers(token),
            params={"per_page": 100, "page": page},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        comments.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return comments


def _extract_linked_prs_from_text(text: str, repo_full_name: str) -> list[dict]:
    refs: dict[int, dict] = {}
    body = text or ""
    repo_norm = (repo_full_name or "").lower()

    for match in _PULL_URL_RE.finditer(body):
        repo = match.group(1)
        if repo.lower() != repo_norm:
            continue
        number = int(match.group(2))
        refs[number] = {
            "number": number,
            "url": f"https://github.com/{repo_full_name}/pull/{number}",
        }

    for match in _PR_CONTEXT_REF_RE.finditer(body):
        number = int(match.group(1))
        refs.setdefault(number, {
            "number": number,
            "url": f"https://github.com/{repo_full_name}/pull/{number}",
        })

    return [refs[n] for n in sorted(refs)]


def _collect_issue_linked_prs(token: str, repo_full_name: str, issue_number: int) -> list[dict]:
    try:
        issue = _issue_details(token, repo_full_name, issue_number)
    except requests.RequestException as e:
        log.warning("Could not fetch issue #%s for PR-link extraction: %s", issue_number, e)
        return []

    refs: dict[int, dict] = {}
    for ref in _extract_linked_prs_from_text(issue.get("body") or "", repo_full_name):
        refs[ref["number"]] = ref

    comment_count = int(issue.get("comments") or 0)
    if comment_count <= 0:
        return [refs[n] for n in sorted(refs)]

    try:
        comments = _issue_comments(token, repo_full_name, issue_number)
    except requests.RequestException as e:
        log.warning("Could not fetch comments for issue #%s: %s", issue_number, e)
        return [refs[n] for n in sorted(refs)]

    for comment in comments:
        for ref in _extract_linked_prs_from_text(comment.get("body") or "", repo_full_name):
            refs[ref["number"]] = ref
    return [refs[n] for n in sorted(refs)]


def _collect_issue_metadata(token: str, repo_full_name: str, issue_number: int) -> dict:
    issue = _issue_details(token, repo_full_name, issue_number)
    assignees = [
        a.get("login")
        for a in (issue.get("assignees") or [])
        if a.get("login")
    ]
    return {
        "linked_prs": _collect_issue_linked_prs(token, repo_full_name, issue_number),
        "assignees": assignees,
        "assignee": assignees[0] if assignees else None,
    }


# ---------------------------------------------------------------------------
# Dry-run preflight — read-only lookup of already-filed issues
# ---------------------------------------------------------------------------
#
# The scheduled live path learns about existing tickets from the public
# Projects REST snapshot. A plain local dry-run intentionally skips that board
# refresh, so this optional REST search can still annotate matching open
# ``label:ci-failure`` issues when a read token is available.
# We then match by exact title, falling back to ``_normalize_title`` so a
# hand-filed ``[CI Failure]: Transformers Nightly Models Test`` adopts the
# syncer's ``mi325_1:``-prefixed twin.
#
# This is strictly read-only; no POST / PATCH / assignment happens in the
# dry-run path. If the search call fails (token missing, rate-limited,
# network), we silently fall through to ``pending`` — better a stale
# preview than a crashed workflow.


def _fetch_existing_ci_failure_issues(
    token: str, repo: str
) -> dict[str, dict]:
    """Return {title: {number, html_url, state}} for open CI-failure issues.

    Paginates ``/search/issues?q=repo:X+is:issue+is:open+label:ci-failure``.
    Returns ``{}`` on any error so callers can proceed with no enrichment.
    """
    out: dict[str, dict] = {}
    page = 1
    while page <= 10:  # 10 * 100 = 1000 issues — well beyond any realistic ci-failure backlog
        try:
            r = requests.get(
                f"{GH_API}/search/issues",
                headers=_rest_headers(token),
                params={
                    "q": f"repo:{repo} is:issue is:open label:{LABEL}",
                    "per_page": 100,
                    "page": page,
                },
                timeout=30,
            )
        except requests.RequestException as e:
            log.warning("dry-run preflight: search failed on page %d: %s", page, e)
            return out
        if r.status_code != 200:
            log.warning("dry-run preflight: search returned %s: %s",
                        r.status_code, r.text[:200])
            return out
        data = r.json()
        items = data.get("items") or []
        for it in items:
            out[it["title"]] = {
                "number": it["number"],
                "html_url": it["html_url"],
                "state": it["state"],
            }
        if len(items) < 100:
            break
        page += 1
    return out


def _enrich_dry_run_plan(plan: list[dict], existing: dict[str, dict]) -> None:
    """Populate ``issue_number``/``issue_url`` on plan entries that already
    have a manually filed upstream issue. Matching is read-only and never
    changes the issue or its project status.
    """
    existing = _filter_matchable_existing(existing)
    if not existing:
        return
    by_norm = _build_norm_index(existing.keys())
    for p in plan:
        title = p["title"]
        matched = existing.get(title)
        if not matched:
            candidates = by_norm.get(_normalize_title(title), [])
            alt = _pick_normalized_candidate(candidates, title)
            if alt:
                matched = existing.get(alt)
        if matched:
            p["issue_number"] = matched["number"]
            p["issue_url"] = matched["html_url"]
            p["action"] = "would_track_manual_issue"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> int:
    live_requested = os.getenv("READY_TICKETS_LIVE", "").strip() == "1"
    write_acknowledged = (
        os.getenv("READY_TICKETS_ALLOW_UPSTREAM_WRITES", "").strip() == "1"
    )
    requested_write_scope = os.getenv("READY_TICKETS_WRITE_SCOPE", "").strip()
    scope_allowed = requested_write_scope == MASTER_COMMENT_WRITE_SCOPE
    allow_live = write_acknowledged and scope_allowed
    read_token = os.getenv("PROJECTS_READ_TOKEN") or os.getenv("GITHUB_TOKEN")
    require_project_refresh = (
        os.getenv("READY_TICKETS_REQUIRE_PROJECT_REFRESH", "").strip() == "1"
    )
    write_token = os.getenv("UPSTREAM_COMMENT_TOKEN")
    live = live_requested and allow_live and bool(write_token)
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_url = f"https://github.com/AndreasKaratzas/vllm-ci-dashboard/actions/runs/{run_id}" if run_id else ""

    shard_patterns = _compile_shard_patterns(_fetch_shard_templates())
    history = _collect_group_history(BACKFILL_DAYS, shard_patterns)
    summaries = [_summarize_group(g, h) for g, h in history.items()]
    summaries.sort(key=lambda s: s["group"])
    latest_date, latest_build, latest_failing = _collect_latest_failing_groups(shard_patterns)
    summaries_by_group = {s["group"]: s for s in summaries}
    failing: list[dict] = []
    for group in sorted(latest_failing):
        current = latest_failing[group]
        summary = dict(summaries_by_group.get(group, {
            "group": group,
            "latest_date": latest_date,
            "currently_failing": True,
            "first_failure_in_window": latest_date,
            "current_streak_started": latest_date,
            "last_successful": None,
            "break_frequency": 0,
            "hardware_latest": {},
            "builds_latest": [],
            "build_refs_latest": [],
        }))
        summary["currently_failing"] = True
        if current.get("latest_date"):
            summary["latest_date"] = current["latest_date"]
        summary["hardware_latest"] = current.get("hardware", {})
        summary["builds_latest"] = current.get("build_numbers", [])
        summary["build_refs_latest"] = current.get("build_refs", [])
        failing.append(summary)

    log.info(
        "Groups: %d tracked, %d currently failing in latest AMD nightly build %s on %s (window=%dd)",
        len(summaries),
        len(failing),
        latest_build if latest_build is not None else "—",
        latest_date or "—",
        BACKFILL_DAYS,
    )

    plan: list[dict] = []
    for s in failing:
        plan.append({
            "title": _canonical_title(s["group"]),
            "summary": s,
            "action": "pending_master_comment",
            "issue_number": MASTER_ISSUE_NUMBER,
            "issue_url": MASTER_ISSUE_URL,
            "project_status": "Tracked in master issue",
            "linked_prs": [],
            "assignees": [],
            "assignee": None,
        })

    master_issue = {
        "number": MASTER_ISSUE_NUMBER,
        "title": MASTER_ISSUE_TITLE,
        "url": MASTER_ISSUE_URL,
    }
    master_comment_body = _master_comment_body(failing, run_url or MASTER_ISSUE_URL)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": BACKFILL_DAYS,
        "issue_repo": ISSUE_REPO,
        "project": f"{PROJECT_ORG}/projects/{PROJECT_NUMBER}",
        "issue_mode": ISSUE_MODE,
        "master_issue": master_issue,
        "master_issue_comment": _retained_master_comment(),
        "write_scope": MASTER_COMMENT_WRITE_SCOPE,
        "mode": "live" if live else "dry_run",
        "failing_groups_total": len(failing),
        "groups_all": summaries,
        "tickets": plan,
    }

    if live_requested and not allow_live:
        log.warning(
            "READY_TICKETS_LIVE=1 without both the explicit write ack and "
            "READY_TICKETS_WRITE_SCOPE=%s — forcing paused mode with no "
            "upstream GitHub calls",
            MASTER_COMMENT_WRITE_SCOPE,
        )
        paused_output = dict(output)
        paused_output.update({
            "mode": "paused",
            "feature_paused": True,
            "pause_reason": PAUSE_REASON,
            "failing_groups_total": 0,
            "groups_all": [],
            "tickets": [],
        })
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(paused_output, indent=2, sort_keys=True))
        PROJECT_ITEMS_OUT.parent.mkdir(parents=True, exist_ok=True)
        PROJECT_ITEMS_OUT.write_text(json.dumps({
            "feature_paused": True,
            "generated_at": paused_output["generated_at"],
            "items_by_number": {},
            "project": f"{PROJECT_ORG}/projects/{PROJECT_NUMBER}",
            "project_url": f"https://github.com/orgs/{PROJECT_ORG}/projects/{PROJECT_NUMBER}",
        }, indent=2, sort_keys=True))
        log.info("Wrote paused Ready Tickets snapshot to %s", OUT)
        return 0

    if not live:
        forced_read_only = live_requested and not write_token
        if live_requested and not write_token:
            log.warning(
                "READY_TICKETS_LIVE=1 but UPSTREAM_COMMENT_TOKEN is not set; "
                "forcing read-only dry-run"
            )
            output["mode"] = "dry_run_forced"
        for p in plan:
            p["action"] = "would_update_master_issue_comment"
        # Read-only preflight: if any token is available (the default
        # ``GITHUB_TOKEN`` is enough — public read), annotate each plan
        # entry that already has an open issue on the target repo.
        preflight_token = read_token
        if preflight_token and plan:
            existing = _fetch_existing_ci_failure_issues(preflight_token, ISSUE_REPO)
            _enrich_dry_run_plan(plan, existing)
            matched = sum(
                1 for p in plan if p["action"] == "would_track_manual_issue"
            )
            log.info("Dry-run preflight: %d of %d plan entries match existing %s issues",
                     matched, len(plan), ISSUE_REPO)
        # The scheduled workflow intentionally requests live mode but may lack
        # the protected write token. Project #39 is public, so refresh its
        # snapshot anonymously when no optional read token is configured. This
        # path never writes ``STATE`` or invokes the comment updater.
        project_refresh_failed = False
        if forced_read_only:
            try:
                _, project_items_by_number = _refresh_project_items_snapshot(
                    read_token or "",
                    output["generated_at"],
                )
                log.info(
                    "Refreshed read-only project #%d snapshot (%d items)",
                    PROJECT_NUMBER,
                    len(project_items_by_number),
                )
            except Exception as e:
                # Preserve the prior snapshot and its timestamp on failure;
                # replacing it with an empty fresh file would hide staleness.
                log.warning("Could not refresh project #%d snapshot: %s", PROJECT_NUMBER, e)
                project_refresh_failed = True
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(output, indent=2, sort_keys=True))
        log.info("Wrote dry-run plan (%d tickets) to %s", len(plan), OUT)
        if project_refresh_failed and require_project_refresh:
            log.error(
                "Required project #%d snapshot refresh failed; exiting nonzero",
                PROJECT_NUMBER,
            )
        return int(project_refresh_failed and require_project_refresh)

    # Live mode from here on.
    existing_manual_issues: dict[str, dict] = {}
    project_items_by_title: dict[str, dict] = {}
    project_items_by_number: dict[str, dict] = {}
    try:
        project_items_by_title, project_items_by_number = _refresh_project_items_snapshot(
            read_token or "",
            output["generated_at"],
        )
        for title, it in project_items_by_title.items():
            matchable = _is_post_umbrella_project_issue(it.get("issueNumber"))
            if (it.get("issueState") or "").lower() == "open":
                if matchable:
                    existing_manual_issues[title] = {
                        "number": it["issueNumber"],
                        "html_url": it["url"],
                        "state": "open",
                        "repo": it.get("repo") or ISSUE_REPO,
                    }
    except Exception as e:
        log.warning("Could not refresh project #39 snapshot: %s", e)
        if require_project_refresh:
            log.error(
                "Required project #%d snapshot refresh failed; exiting nonzero",
                PROJECT_NUMBER,
            )
            return 1
        # Preserve the historical live-mode contract when no prior snapshot is
        # available: the dashboard still receives a well-shaped empty file.
        if not PROJECT_ITEMS_OUT.exists():
            _write_project_items_snapshot({}, output["generated_at"])

    # Only adopt per-group issues that are actually on project #39 and newer
    # than the static tracker. Legacy repo-wide CI tickets are intentionally
    # ignored now.
    _enrich_dry_run_plan(plan, existing_manual_issues)

    master_comment = _update_pinned_master_comment(
        write_token,
        body=master_comment_body,
        expected_comment_id=_expected_master_comment_id(),
    )
    output["master_issue"] = master_issue
    output["master_issue_comment"] = master_comment

    for entry in plan:
        manual_issue_number = entry.get("issue_number")
        if manual_issue_number and int(manual_issue_number) != master_issue["number"]:
            issue_url = entry.get("issue_url") or f"https://github.com/{ISSUE_REPO}/issues/{manual_issue_number}"
            try:
                if not read_token:
                    raise RuntimeError("No read token available for issue metadata")
                metadata = _collect_issue_metadata(
                    read_token, ISSUE_REPO, int(manual_issue_number)
                )
            except requests.RequestException as e:
                log.warning("Could not refresh metadata for issue #%s: %s", manual_issue_number, e)
                metadata = {"linked_prs": [], "assignees": [], "assignee": None}
            entry["issue_number"] = int(manual_issue_number)
            entry["issue_url"] = issue_url
            entry["action"] = "tracked_manual_issue"
            entry["project_status"] = (
                (project_items_by_number.get(str(manual_issue_number)) or {}).get("status")
                or "Tracked by manual issue"
            )
            entry["linked_prs"] = metadata["linked_prs"]
            entry["assignees"] = metadata["assignees"]
            entry["assignee"] = metadata["assignee"]
            continue

        entry["issue_number"] = master_issue["number"]
        entry["issue_url"] = master_issue["url"]
        entry["action"] = "updated_master_issue_comment"
        entry["project_status"] = "Tracked in master issue"
        entry["linked_prs"] = []
        entry["assignees"] = []
        entry["assignee"] = None

    state = {
        "master_issue": {
            "issue_number": master_issue["number"],
            "issue_url": master_issue["url"],
            "last_synced_at": output["generated_at"],
            "title": master_issue["title"],
            "comment_id": master_comment["id"],
            "comment_url": master_comment["url"],
        }
    }
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True))
    OUT.write_text(json.dumps(output, indent=2, sort_keys=True))
    log.info(
        "%s master issue comment on #%s with %d failing groups.",
        master_comment["action"].capitalize(),
        master_issue["number"],
        len(plan),
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
