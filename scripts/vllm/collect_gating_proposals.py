#!/usr/bin/env python3
"""Collect open PRs that propose new AMD mirrors for upstream test groups.

Each run searches recent tracked-author PRs and rechecks cached PRs that
previously added mirrors so proposal counts do not disappear after search
windows move.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import yaml


log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT = ROOT / "data" / "vllm" / "ci"

GITHUB_REPO = "vllm-project/vllm"
TEST_AREAS_PREFIX = ".buildkite/test_areas/"
TRACKED_AUTHORS = (
    "AndreasKaratzas",
    "Fangzhou-Ai",
    "aarushjain29",
    "divakar-amd",
    "fxmarty-amd",
    "gchinora",
    "gyohuangxin",
    "mawong-amd",
    "micah-wil",
    "okorzh-amd",
    "stefankoncarevic",
)

DEFAULT_LOOKBACK_DAYS = 35
MAX_SEARCH_REQUESTS = 24
MAX_SEARCH_PAGES_PER_AUTHOR = 2
MAX_PR_FILE_PAGES = 10
MAX_OUTPUT_BYTES = 80 * 1024 * 1024
MULTISPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class MirrorStep:
    identity: str
    label: str
    key: str
    area: str
    yaml_file: str
    yaml_index: int
    device: str
    timeout_in_minutes: int | None
    source_file_dependencies: tuple[str, ...]


@dataclass
class SearchRequestBudget:
    """One collector-wide ceiling below GitHub Search's 30/minute bucket."""

    limit: int = MAX_SEARCH_REQUESTS
    used: int = 0

    def reserve(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


class GitHubCoverageError(RuntimeError):
    """A finite GitHub read whose complete result could not be proven."""


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def clean_text(value: Any) -> str:
    return MULTISPACE_RE.sub(" ", str(value or "").strip()).strip()


def slugify(value: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return out or "unknown"


def normalize_dependency_list(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    deps: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            deps.append(item.strip().lstrip("./"))
    return tuple(deps)


def safe_int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def area_from_yaml(parsed: dict[str, Any], path: str) -> str:
    return clean_text(parsed.get("group")) or Path(path).stem.replace("_", " ").title()


def parse_mirror_steps(yaml_text: str, path: str) -> dict[str, MirrorStep]:
    try:
        parsed = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as exc:
        log.warning("Skipping unparsable YAML from %s: %s", path, exc)
        return {}
    if not isinstance(parsed, dict):
        return {}
    area = area_from_yaml(parsed, path)
    steps = parsed.get("steps") or []
    if not isinstance(steps, list):
        return {}

    mirrors: dict[str, MirrorStep] = {}
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        mirror = step.get("mirror")
        if not isinstance(mirror, dict) or "amd" not in mirror:
            continue
        amd = mirror.get("amd") or {}
        if not isinstance(amd, dict):
            amd = {}
        label = clean_text(step.get("label")) or clean_text(step.get("key")) or f"{area} #{idx + 1}"
        key = clean_text(step.get("key"))
        identity = key or slugify(label)
        mirrors[identity] = MirrorStep(
            identity=identity,
            label=label,
            key=key,
            area=area,
            yaml_file=path,
            yaml_index=idx,
            device=clean_text(amd.get("device") or step.get("device")),
            timeout_in_minutes=safe_int_or_none(amd.get("timeout_in_minutes") or step.get("timeout_in_minutes")),
            source_file_dependencies=normalize_dependency_list(
                amd.get("source_file_dependencies") or step.get("source_file_dependencies")
            ),
        )
    return mirrors


def new_mirrors(base_yaml: str, head_yaml: str, path: str) -> list[MirrorStep]:
    base = parse_mirror_steps(base_yaml, path)
    head = parse_mirror_steps(head_yaml, path)
    return [step for key, step in head.items() if key not in base]


class GitHubClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        resp = self.session.get(url, headers=_github_headers(), params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_text(self, url: str) -> str:
        resp = self.session.get(url, headers=_github_headers(), timeout=30)
        resp.raise_for_status()
        return resp.text


def collection_error(scope: str, exc: BaseException, **fields: Any) -> dict[str, Any]:
    error = {"scope": scope, **fields, "error": clean_text(exc)}
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        error["status_code"] = status_code
    return error


def _parse_number(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _candidate_from_search_item(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item.get("pull_request"), dict):
        return None
    number = _parse_number(item.get("number"))
    if number is None:
        return None
    pull_request = item.get("pull_request") or {}
    return {
        "number": number,
        "title": clean_text(item.get("title")),
        "url": pull_request.get("html_url") or item.get("html_url") or "",
        "api_url": pull_request.get("url") or "",
        "author": clean_text((item.get("user") or {}).get("login")),
        "state": clean_text(item.get("state")),
        "created_at": item.get("created_at") or "",
        "updated_at": item.get("updated_at") or "",
    }


def _candidate_from_previous_pr(pr: dict[str, Any]) -> dict[str, Any] | None:
    number = _parse_number(pr.get("number"))
    if number is None:
        return None
    return {
        "number": number,
        "title": clean_text(pr.get("title")),
        "url": pr.get("url") or f"https://github.com/{GITHUB_REPO}/pull/{number}",
        "api_url": pr.get("api_url") or "",
        "author": clean_text(pr.get("author")),
        "state": clean_text(pr.get("state")),
        "created_at": pr.get("created_at") or "",
        "updated_at": pr.get("updated_at") or "",
        "from_previous_proposal": True,
    }


def search_pr_candidates_with_errors(
    client: GitHubClient,
    repo: str,
    authors: list[str],
    *,
    since_date: str | None = None,
    state: str | None = None,
    budget: SearchRequestBudget | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    budget = budget or SearchRequestBudget()
    candidates: dict[int, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for author in authors:
        parts = [f"repo:{repo}", "is:pr"]
        if state:
            parts.append(f"is:{state}")
        parts.append(f"author:{author}")
        if since_date:
            parts.append(f"created:>={since_date}")
        query = " ".join(parts)
        author_candidates: dict[int, dict[str, Any]] = {}
        author_errors: list[dict[str, Any]] = []
        observed_items = 0
        complete = False
        total_count: int | None = None
        for page in range(1, MAX_SEARCH_PAGES_PER_AUTHOR + 1):
            if not budget.reserve():
                error = GitHubCoverageError(
                    f"global Search request ceiling {budget.limit} reached"
                )
                author_errors.append(
                    collection_error(
                        "search_budget",
                        error,
                        author=author,
                        page=page,
                    )
                )
                break
            try:
                payload = client.get_json(
                    "https://api.github.com/search/issues",
                    params={"q": query, "sort": "updated", "order": "desc", "per_page": 100, "page": page},
                )
            except requests.RequestException as exc:
                log.warning("GitHub search failed for author %s page %s: %s", author, page, exc)
                author_errors.append(
                    collection_error("search", exc, author=author, page=page)
                )
                break
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                author_errors.append(
                    collection_error(
                        "search_coverage",
                        GitHubCoverageError("Search response did not contain an items list"),
                        author=author,
                        page=page,
                    )
                )
                break
            incomplete_results = payload.get("incomplete_results")
            if incomplete_results is True:
                author_errors.append(
                    collection_error(
                        "search_coverage",
                        GitHubCoverageError("GitHub reported incomplete Search results"),
                        author=author,
                        page=page,
                    )
                )
                break
            if incomplete_results is not None and incomplete_results is not False:
                author_errors.append(
                    collection_error(
                        "search_coverage",
                        GitHubCoverageError(
                            "Search response contained an invalid incomplete_results flag"
                        ),
                        author=author,
                        page=page,
                    )
                )
                break
            raw_total = payload.get("total_count")
            if isinstance(raw_total, int) and not isinstance(raw_total, bool) and raw_total >= 0:
                total_count = raw_total
            elif raw_total is not None:
                author_errors.append(
                    collection_error(
                        "search_coverage",
                        GitHubCoverageError(
                            "Search response contained an invalid total_count"
                        ),
                        author=author,
                        page=page,
                    )
                )
                break
            items = payload["items"]
            observed_items += len(items)
            for item in items:
                if not isinstance(item, dict):
                    continue
                candidate = _candidate_from_search_item(item)
                if candidate:
                    author_candidates[candidate["number"]] = candidate
            if total_count is not None and observed_items > total_count:
                author_errors.append(
                    collection_error(
                        "search_coverage",
                        GitHubCoverageError(
                            "Search item count exceeded total_count"
                        ),
                        author=author,
                        page=page,
                    )
                )
                break
            if total_count is not None and observed_items >= total_count:
                complete = True
                break
            if len(items) < 100:
                if total_count is not None and observed_items < total_count:
                    author_errors.append(
                        collection_error(
                            "search_coverage",
                            GitHubCoverageError(
                                "Search short page disagreed with total_count"
                            ),
                            author=author,
                            page=page,
                        )
                    )
                else:
                    complete = True
                break
        if not complete and not author_errors:
            author_errors.append(
                collection_error(
                    "search_coverage",
                    GitHubCoverageError(
                        "tracked-author Search exceeded its bounded page window"
                    ),
                    author=author,
                    page=MAX_SEARCH_PAGES_PER_AUTHOR,
                )
            )
        if author_errors:
            # Never accept a prefix as the author's authoritative current set.
            errors.extend(author_errors)
        else:
            candidates.update(author_candidates)
    rows = sorted(candidates.values(), key=lambda row: (row.get("updated_at") or "", row["number"]), reverse=True)
    return rows, errors


def search_open_pr_numbers_with_errors(
    client: GitHubClient, repo: str, authors: list[str]
) -> tuple[list[int], list[dict[str, Any]]]:
    candidates, errors = search_pr_candidates_with_errors(client, repo, authors, state="open")
    return [int(candidate["number"]) for candidate in candidates], errors


def search_open_pr_numbers(client: GitHubClient, repo: str, authors: list[str]) -> list[int]:
    numbers, _errors = search_open_pr_numbers_with_errors(client, repo, authors)
    return numbers


def raw_url(repo: str, sha: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{sha}/{path}"


def paged_pr_files(client: GitHubClient, repo: str, number: int) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for page in range(1, MAX_PR_FILE_PAGES + 1):
        page_rows = client.get_json(
            f"https://api.github.com/repos/{repo}/pulls/{number}/files",
            params={"per_page": 100, "page": page},
        )
        if not isinstance(page_rows, list):
            raise GitHubCoverageError(
                f"PR #{number} changed-file lookup returned a non-list page"
            )
        files.extend(row for row in page_rows if isinstance(row, dict))
        if len(page_rows) < 100:
            return files
    raise GitHubCoverageError(
        f"PR #{number} changed-file lookup exceeded {MAX_PR_FILE_PAGES} pages"
    )


def collect_pr(
    client: GitHubClient,
    repo: str,
    number: int,
    *,
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    owner_repo = repo
    pull_url = (candidate or {}).get("api_url") or f"https://api.github.com/repos/{owner_repo}/pulls/{number}"
    pull = client.get_json(pull_url)
    if clean_text(pull.get("state")).lower() != "open":
        return None
    files = paged_pr_files(client, repo, number)
    base_sha = pull.get("base", {}).get("sha") or ""
    head_sha = pull.get("head", {}).get("sha") or ""
    additions: list[dict[str, Any]] = []

    for file_row in files:
        path = str(file_row.get("filename") or "")
        if not path.startswith(TEST_AREAS_PREFIX) or not path.endswith((".yaml", ".yml")):
            continue
        try:
            base_text = client.get_text(raw_url(repo, base_sha, path))
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                base_text = ""
            else:
                raise
        head_text = client.get_text(raw_url(repo, head_sha, path))
        for step in new_mirrors(base_text, head_text, path):
            additions.append(asdict(step))

    if not additions:
        return None

    additions.sort(key=lambda row: (row["yaml_file"], row["yaml_index"], row["label"]))
    return {
        "number": pull.get("number"),
        "title": pull.get("title") or "",
        "state": pull.get("state") or "",
        "url": pull.get("html_url") or f"https://github.com/{repo}/pull/{number}",
        "api_url": pull_url,
        "author": (pull.get("user") or {}).get("login") or "",
        "head_ref": (pull.get("head") or {}).get("ref") or "",
        "head_sha": head_sha,
        "base_ref": (pull.get("base") or {}).get("ref") or "",
        "base_sha": base_sha,
        "updated_at": pull.get("updated_at") or "",
        "created_at": pull.get("created_at") or "",
        "new_mirror_count": len(additions),
        "new_mirrors": additions,
    }


def _pr_number(pr: dict[str, Any]) -> int | None:
    try:
        return int(pr.get("number"))
    except (TypeError, ValueError):
        return None


def merge_previous_on_partial_scan(
    proposals: list[dict[str, Any]],
    previous: dict[str, Any] | None,
    *,
    generated_at: str,
    failed_pr_numbers: set[int],
    errored_authors: set[str],
    allowed_authors: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(previous, dict):
        return proposals, []

    current_numbers = {number for pr in proposals if (number := _pr_number(pr)) is not None}
    retained: list[dict[str, Any]] = []
    for previous_pr in previous.get("pull_requests") or []:
        if not isinstance(previous_pr, dict):
            continue
        number = _pr_number(previous_pr)
        author = clean_text(previous_pr.get("author"))
        author_key = author.casefold()
        if author_key not in allowed_authors:
            continue
        if number is not None and number in current_numbers:
            continue
        if (number is not None and number in failed_pr_numbers) or (
            author_key and author_key in errored_authors
        ):
            retained_pr = copy.deepcopy(previous_pr)
            retained_pr["retained_from_previous_scan"] = True
            retained_pr["retained_at"] = generated_at
            if previous.get("generated_at"):
                retained_pr["last_seen_at"] = previous["generated_at"]
            retained.append(retained_pr)

    if not retained:
        return proposals, []

    merged = proposals + retained
    merged.sort(key=lambda pr: (pr.get("updated_at") or "", pr.get("number") or 0), reverse=True)
    return merged, retained


def load_previous_payload(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read previous proposal payload from %s: %s", path, exc)
        return None


def write_payload_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Publish one complete sub-90-MiB snapshot without an in-place tear."""
    encoded = (json.dumps(payload, indent=2, default=str) + "\n").encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise RuntimeError(
            f"Gating proposal payload is {len(encoded)} bytes; "
            f"refusing the {MAX_OUTPUT_BYTES}-byte publication limit"
        )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def previous_candidate_rows(previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(previous, dict):
        return []
    rows: list[dict[str, Any]] = []
    cache = ((previous.get("collection") or {}).get("candidate_cache") or {}).get("pull_requests") or []
    for row in cache:
        if (
            isinstance(row, dict)
            and row.get("has_new_mirrors") is True
            and _parse_number(row.get("number")) is not None
        ):
            rows.append(dict(row))
    for pr in previous.get("pull_requests") or []:
        if not isinstance(pr, dict):
            continue
        candidate = _candidate_from_previous_pr(pr)
        if candidate:
            rows.append(candidate)
    return rows


def merge_candidate_rows(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for rows in groups:
        for row in rows:
            number = _parse_number(row.get("number"))
            if number is None:
                continue
            existing = merged.get(number, {})
            merged[number] = {**existing, **{key: value for key, value in row.items() if value not in (None, "")}}
            merged[number]["number"] = number
    return sorted(merged.values(), key=lambda row: (row.get("updated_at") or "", row["number"]), reverse=True)


def cache_row_for_candidate(
    candidate: dict[str, Any],
    *,
    checked_at: str,
    proposal: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = dict(candidate)
    row["checked_at"] = checked_at
    row["has_new_mirrors"] = bool(proposal)
    row["new_mirror_count"] = int((proposal or {}).get("new_mirror_count") or 0)
    if proposal:
        row.update({
            "title": proposal.get("title") or row.get("title") or "",
            "url": proposal.get("url") or row.get("url") or "",
            "api_url": proposal.get("api_url") or row.get("api_url") or "",
            "author": proposal.get("author") or row.get("author") or "",
            "state": proposal.get("state") or row.get("state") or "",
            "created_at": proposal.get("created_at") or row.get("created_at") or "",
            "updated_at": proposal.get("updated_at") or row.get("updated_at") or "",
            "head_sha": proposal.get("head_sha") or "",
            "new_mirror_labels": [mirror.get("label") for mirror in proposal.get("new_mirrors") or []],
        })
    if error:
        row["last_error"] = error
    else:
        row.pop("last_error", None)
    return row


def summarize(prs: list[dict[str, Any]], scanned_pr_count: int, authors: list[str]) -> dict[str, Any]:
    by_device: dict[str, int] = {}
    by_author: dict[str, int] = {}
    proposed_group_count = 0
    for pr in prs:
        by_author[pr["author"]] = by_author.get(pr["author"], 0) + int(pr.get("new_mirror_count") or 0)
        for mirror in pr.get("new_mirrors") or []:
            proposed_group_count += 1
            device = mirror.get("device") or "unknown"
            by_device[device] = by_device.get(device, 0) + 1
    return {
        "tracked_author_count": len(authors),
        "scanned_pr_count": scanned_pr_count,
        "proposal_pr_count": len(prs),
        "proposed_group_count": proposed_group_count,
        "by_device": dict(sorted(by_device.items())),
        "by_author": dict(sorted(by_author.items())),
    }


def collect_gating_proposals(
    repo: str,
    authors: list[str],
    client: GitHubClient | None = None,
    previous: dict[str, Any] | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    since_date: str | None = None,
) -> dict[str, Any]:
    client = client or GitHubClient()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if since_date is None:
        since_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    search_budget = SearchRequestBudget()
    fresh_candidates, errors = search_pr_candidates_with_errors(
        client,
        repo,
        authors,
        since_date=since_date,
        state="open",
        budget=search_budget,
    )
    allowed_authors = {clean_text(author).casefold() for author in authors}
    allowed_authors.discard("")
    errored_authors = {
        clean_text(error.get("author")).casefold()
        for error in errors
        if str(error.get("scope") or "").startswith("search")
    }
    errored_authors.discard("")
    previous_candidates = [
        row for row in previous_candidate_rows(previous)
        if clean_text(row.get("author")).casefold() in allowed_authors
        and clean_text(row.get("author")).casefold() not in errored_authors
    ]
    candidates = merge_candidate_rows(fresh_candidates, previous_candidates)
    proposals: list[dict[str, Any]] = []
    candidate_cache: list[dict[str, Any]] = []
    failed_pr_numbers: set[int] = set()
    for candidate in candidates:
        number = int(candidate["number"])
        try:
            pr = collect_pr(client, repo, number, candidate=candidate)
        except (requests.RequestException, GitHubCoverageError) as exc:
            log.warning("Skipping PR #%s after GitHub error: %s", number, exc)
            failed_pr_numbers.add(number)
            error = collection_error("pull_request", exc, number=number)
            errors.append(error)
            candidate_cache.append(cache_row_for_candidate(candidate, checked_at=generated_at, error=error))
            continue
        if pr:
            proposals.append(pr)
            candidate_cache.append(cache_row_for_candidate(candidate, checked_at=generated_at, proposal=pr))
        else:
            candidate_cache.append(cache_row_for_candidate(candidate, checked_at=generated_at))
    proposals.sort(key=lambda pr: (pr.get("updated_at") or "", pr.get("number") or 0), reverse=True)

    retained: list[dict[str, Any]] = []
    if errors:
        proposals, retained = merge_previous_on_partial_scan(
            proposals,
            previous,
            generated_at=generated_at,
            failed_pr_numbers=failed_pr_numbers,
            errored_authors=errored_authors,
            allowed_authors=allowed_authors,
        )

    return {
        "generated_at": generated_at,
        "source_repo": repo,
        "tracked_authors": authors,
        "summary": summarize(proposals, len(candidates), authors),
        "collection": {
            "complete": not errors,
            "error_count": len(errors),
            "errors": errors,
            "retained_pr_count": len(retained),
            "retained_pr_numbers": [pr.get("number") for pr in retained],
            "lookback_days": lookback_days,
            "since_date": since_date,
            "fresh_candidate_count": len(fresh_candidates),
            "search_request_count": search_budget.used,
            "search_request_limit": search_budget.limit,
            "candidate_cache": {
                "generated_at": generated_at,
                "pr_count": len(candidate_cache),
                "proposal_pr_numbers": [pr.get("number") for pr in proposals],
                "pull_requests": sorted(
                    candidate_cache,
                    key=lambda row: (row.get("has_new_mirrors") is True, row.get("updated_at") or "", row.get("number") or 0),
                    reverse=True,
                ),
            },
        },
        "pull_requests": proposals,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect PRs proposing new AMD mirror gating")
    parser.add_argument("--output", default=str(OUTPUT), help="Output directory")
    parser.add_argument("--repo", default=GITHUB_REPO, help="GitHub repository, owner/name")
    parser.add_argument("--authors", default=",".join(TRACKED_AUTHORS), help="Comma-separated GitHub authors")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="Days of recent tracked-author PRs to scan")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    authors = [author.strip() for author in args.authors.split(",") if author.strip()]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    out_path = output / "gating_proposals.json"
    previous = load_previous_payload(out_path)
    payload = collect_gating_proposals(args.repo, authors, previous=previous, lookback_days=args.lookback_days)
    if payload.get("collection", {}).get("complete") is not True:
        raise RuntimeError(
            "Gating proposal collection was incomplete; preserving the prior output"
        )
    write_payload_atomic(out_path, payload)
    log.info(
        "Wrote %s with %d PRs and %d proposed mirrors",
        out_path,
        payload["summary"]["proposal_pr_count"],
        payload["summary"]["proposed_group_count"],
    )


if __name__ == "__main__":
    main()
