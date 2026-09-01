#!/usr/bin/env python3
"""Collect test group change history from git commits to pipeline YAML files.

For each commit that modified test-amd.yaml or test_areas/*.yaml, diffs the
YAML to find which test groups were added/removed and maps the commit to a PR
via the GitHub API.

Produces: data/vllm/ci/group_changes.json

Usage:
    export GITHUB_TOKEN="ghp_..."
    python scripts/vllm/collect_group_changes.py --days 30 --output data/vllm/ci/
"""

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.dashboard_storage_budget import writer_max_bytes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
REPO = "vllm-project/vllm"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}"

# YAML files that define test groups
AMD_YAML = ".buildkite/test-amd.yaml"
UPSTREAM_DIR = ".buildkite/test_areas"

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT = ROOT / "data" / "vllm" / "ci"
GROUP_CHANGES_MAX_BYTES = writer_max_bytes("group_changes")


def _retain_source_window_cache(
    changes: list[dict],
    no_change_shas: set[str],
    commits: list[dict],
    *,
    cutoff_date: str,
) -> tuple[list[dict], set[str]]:
    """Keep cache state only for the declared source window.

    ``--days`` has always described the public history window, but older
    versions appended cached rows forever.  Change rows carry dates and can be
    pruned without another request.  No-change rows historically stored only
    a SHA, so retain those only while they are present in this run's bounded
    source cohort; dropping one merely causes a safe re-check on a later run.
    """
    current_shas = {
        str(commit.get("sha") or "")
        for commit in commits
        if isinstance(commit, dict) and commit.get("sha")
    }
    retained_changes = [
        row
        for row in changes
        if isinstance(row, dict)
        and isinstance(row.get("date"), str)
        and row["date"] >= cutoff_date
    ]
    return retained_changes, no_change_shas & current_shas


def _write_json_atomic(path: Path, payload: dict) -> None:
    encoded = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _compact_group_changes_for_publication(
    source: dict,
    *,
    max_bytes: int | None = None,
) -> dict:
    """Retain newest whole change rows, then a bounded cache SHA set."""
    if max_bytes is None:
        max_bytes = GROUP_CHANGES_MAX_BYTES
    if max_bytes <= 0:
        raise ValueError("group-change byte budget must be positive")
    source_changes = list(source.get("changes") or [])
    source_no_change = list(dict.fromkeys(
        str(sha) for sha in source.get("_no_change_shas") or [] if str(sha)
    ))

    def candidate(change_start: int, no_change_count: int) -> dict:
        changes = source_changes[change_start:]
        no_change = source_no_change[:no_change_count]
        result = {
            key: value
            for key, value in source.items()
            if key not in {
                "changes",
                "_no_change_shas",
                "publication_retention",
                "total_changes",
                "source_total_changes",
            }
        }
        result["source_total_changes"] = len(source_changes)
        result["total_changes"] = len(changes)
        result["changes"] = changes
        result["_no_change_shas"] = no_change
        result["publication_retention"] = {
            "policy": "retain_newest_whole_change_rows_then_cache_sha_entries",
            "max_bytes": max_bytes,
            "complete_relative_to_source": (
                change_start == 0 and no_change_count == len(source_no_change)
            ),
            "changes": {
                "source": len(source_changes),
                "published": len(changes),
                "omitted": change_start,
                "complete": change_start == 0,
            },
            "no_change_cache": {
                "source": len(source_no_change),
                "published": len(no_change),
                "omitted": len(source_no_change) - len(no_change),
                "complete": no_change_count == len(source_no_change),
            },
        }
        return result

    # The no-change SHA list is a request-saving cache and can be reconstructed;
    # preserve public history rows ahead of it. Keep its deterministic newest-
    # first prefix only when the complete public history still fits.
    low = 0
    high = len(source_no_change)
    best: dict | None = None
    while low <= high:
        count = (low + high) // 2
        attempt = candidate(0, count)
        if len((json.dumps(attempt, indent=2) + "\n").encode("utf-8")) <= max_bytes:
            best = attempt
            low = count + 1
        else:
            high = count - 1
    if best is not None:
        return best

    change_start = 0
    bounded = candidate(change_start, 0)
    while len((json.dumps(bounded, indent=2) + "\n").encode("utf-8")) > max_bytes:
        if change_start >= len(source_changes):
            raise RuntimeError(
                "group-change fixed metadata exceeds its byte budget; preserving "
                "the last-known-good file"
            )
        change_start += 1
        bounded = candidate(change_start, 0)
    return bounded


def _write_bounded_group_changes(path: Path, source: dict) -> dict:
    bounded = _compact_group_changes_for_publication(source)
    _write_json_atomic(path, bounded)
    return bounded


def _gh_headers():
    token = os.getenv("GITHUB_TOKEN", "")
    h = {"Accept": "application/vnd.github.v3+json"}
    if token:
        h["Authorization"] = f"token {token}"
    return h


def _gh_get(url, params=None):
    resp = requests.get(url, headers=_gh_headers(), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _fetch_raw(ref, path):
    """Fetch raw file content at a specific commit/ref."""
    url = f"{RAW_BASE}/{ref}/{path}"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def _extract_groups_from_yaml(text):
    """Extract test group labels from a YAML file."""
    if not text:
        return set()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return set()
    if not data:
        return set()

    groups = set()
    steps = data if isinstance(data, list) else data.get("steps", [])
    for step in steps:
        if not isinstance(step, dict):
            continue
        label = step.get("label", "")
        if not label:
            continue
        # Strip %N parallelism marker for canonical name
        label = re.sub(r'\s*%N\s*$', '', label).strip()
        if label:
            groups.add(label)
    return groups


def _get_commits_touching_yaml(days):
    """Get commits on main that touched pipeline YAML files."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    commits = []

    # Get commits touching test-amd.yaml
    try:
        data = _gh_get(
            f"{GITHUB_API}/repos/{REPO}/commits",
            {"path": AMD_YAML, "since": since, "per_page": 100, "sha": "main"}
        )
        for c in data:
            commits.append({
                "sha": c["sha"],
                "date": c["commit"]["committer"]["date"][:10],
                "message": c["commit"]["message"].split("\n")[0][:100],
                "author": c["commit"]["author"]["name"],
                "file": AMD_YAML,
            })
    except Exception as e:
        log.warning("Failed to get commits for %s: %s", AMD_YAML, e)

    # Get commits touching test_areas/
    try:
        # List files in test_areas first
        api_url = f"{GITHUB_API}/repos/{REPO}/contents/{UPSTREAM_DIR}"
        files = _gh_get(api_url)
        yaml_files = [f["path"] for f in files if f["name"].endswith(".yaml")]

        for yf in yaml_files:
            try:
                data = _gh_get(
                    f"{GITHUB_API}/repos/{REPO}/commits",
                    {"path": yf, "since": since, "per_page": 50, "sha": "main"}
                )
                for c in data:
                    commits.append({
                        "sha": c["sha"],
                        "date": c["commit"]["committer"]["date"][:10],
                        "message": c["commit"]["message"].split("\n")[0][:100],
                        "author": c["commit"]["author"]["name"],
                        "file": yf,
                    })
            except Exception as e:
                log.warning("Failed to get commits for %s: %s", yf, e)
    except Exception as e:
        log.warning("Failed to list test_areas: %s", e)

    # Deduplicate by SHA
    seen = set()
    unique = []
    for c in commits:
        if c["sha"] not in seen:
            seen.add(c["sha"])
            unique.append(c)

    unique.sort(key=lambda c: c["date"])
    return unique


def _commit_to_pr(sha):
    """Map a commit SHA to its PR number via GitHub API."""
    try:
        data = _gh_get(f"{GITHUB_API}/repos/{REPO}/commits/{sha}/pulls")
        if data:
            pr = data[0]
            return {
                "number": pr["number"],
                "title": pr["title"][:100],
                "url": pr["html_url"],
                "author": pr["user"]["login"],
            }
    except Exception:
        pass
    return None


def _diff_groups(sha, parent_sha):
    """Diff test groups between two commits, separated by pipeline.

    Returns:
        Tuple of (amd_added, amd_removed, upstream_added, upstream_removed)
    """
    amd_added = set()
    amd_removed = set()
    upstream_added = set()
    upstream_removed = set()

    # Check AMD YAML
    old_text = _fetch_raw(parent_sha, AMD_YAML)
    new_text = _fetch_raw(sha, AMD_YAML)
    old_groups = _extract_groups_from_yaml(old_text)
    new_groups = _extract_groups_from_yaml(new_text)
    amd_added = new_groups - old_groups
    amd_removed = old_groups - new_groups

    # Check upstream test_areas/*.yaml ONLY
    try:
        api_url = f"{GITHUB_API}/repos/{REPO}/contents/{UPSTREAM_DIR}?ref={sha}"
        files = _gh_get(api_url)
        yaml_files = [f["path"] for f in files if f["name"].endswith(".yaml")]

        for yf in yaml_files:
            old_t = _fetch_raw(parent_sha, yf)
            new_t = _fetch_raw(sha, yf)
            old_g = _extract_groups_from_yaml(old_t)
            new_g = _extract_groups_from_yaml(new_t)
            upstream_added |= new_g - old_g
            upstream_removed |= old_g - new_g
    except Exception:
        pass

    return sorted(amd_added), sorted(amd_removed), sorted(upstream_added), sorted(upstream_removed)


def main():
    parser = argparse.ArgumentParser(description="Collect test group change history")
    parser.add_argument("--days", type=int, default=30, help="Days of history (default: 30)")
    parser.add_argument("--output", type=str, default=str(OUTPUT))
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    # Load existing data as cache — skip commits already processed
    out_path = output / "group_changes.json"
    cached_changes = []
    cached_shas = set()
    # Also check commits that had no changes (stored in _no_change_shas)
    cached_no_change_shas = set()
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
            cached_changes = existing.get("changes", [])
            # Only cache entries that have the per-pipeline fields
            cached_changes = [c for c in cached_changes if "amd_added" in c]
            cached_shas = {c["sha"] for c in cached_changes}
            cached_no_change_shas = set(existing.get("_no_change_shas", []))
            log.info("Loaded %d cached changes (%d no-change commits)",
                     len(cached_changes), len(cached_no_change_shas))
        except Exception:
            pass

    log.info("Fetching commits touching pipeline YAML (last %d days)...", args.days)
    commits = _get_commits_touching_yaml(args.days)
    log.info("Found %d unique commits", len(commits))

    cutoff_date = (
        datetime.now(timezone.utc) - timedelta(days=max(1, args.days))
    ).date().isoformat()
    cached_changes, cached_no_change_shas = _retain_source_window_cache(
        cached_changes,
        cached_no_change_shas,
        commits,
        cutoff_date=cutoff_date,
    )
    cached_shas = {str(change.get("sha") or "") for change in cached_changes}

    changes = list(cached_changes)
    no_change_shas = set(cached_no_change_shas)
    new_processed = 0

    for i, commit in enumerate(commits):
        sha = commit["sha"]
        sha_short = sha[:12]

        # Skip if already processed (either had changes or confirmed no changes)
        if sha_short in cached_shas or sha in cached_no_change_shas:
            continue

        new_processed += 1
        log.info("  [%d/%d] %s — %s", i + 1, len(commits), sha[:8], commit["message"][:60])

        # Get parent commit
        try:
            commit_data = _gh_get(f"{GITHUB_API}/repos/{REPO}/commits/{sha}")
            parents = commit_data.get("parents", [])
            if not parents:
                no_change_shas.add(sha)
                continue
            parent_sha = parents[0]["sha"]
        except Exception as e:
            log.warning("    Failed to get parent: %s", e)
            continue

        # Diff groups (separated by pipeline)
        amd_added, amd_removed, up_added, up_removed = _diff_groups(sha, parent_sha)
        if not amd_added and not amd_removed and not up_added and not up_removed:
            no_change_shas.add(sha)
            continue

        # Map to PR
        pr = _commit_to_pr(sha)

        # Combined for backward compat, plus per-pipeline detail
        entry = {
            "date": commit["date"],
            "sha": sha_short,
            "message": commit["message"],
            "author": commit["author"],
            "added": sorted(set(amd_added) | set(up_added)),
            "removed": sorted(set(amd_removed) | set(up_removed)),
            "amd_added": amd_added,
            "amd_removed": amd_removed,
            "upstream_added": up_added,
            "upstream_removed": up_removed,
            "pr": pr,
        }
        changes.append(entry)
        total_added = len(entry["added"])
        total_removed = len(entry["removed"])
        log.info("    +%d/-%d groups%s", total_added, total_removed,
                 f" (PR #{pr['number']})" if pr else "")

    log.info("Processed %d new commits (%d cached)", new_processed,
             len(commits) - new_processed)

    # Sort by date and deduplicate
    seen = set()
    unique_changes = []
    for c in sorted(changes, key=lambda x: x["date"]):
        if c["sha"] not in seen:
            seen.add(c["sha"])
            unique_changes.append(c)

    # Write output
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat()[:19] + "Z",
        "days": args.days,
        "total_changes": len(unique_changes),
        "changes": unique_changes,
        # Cache priority is newest source-cohort commit first. Any residual
        # legacy SHA not present in this run is appended deterministically.
        "_no_change_shas": [
            str(commit["sha"])
            for commit in reversed(commits)
            if str(commit.get("sha") or "") in no_change_shas
        ] + sorted(
            no_change_shas
            - {str(commit.get("sha") or "") for commit in commits}
        ),
    }
    published = _write_bounded_group_changes(out_path, result)
    log.info(
        "Wrote %s (%d of %d changes, %d new)",
        out_path,
        len(published["changes"]),
        len(unique_changes),
        new_processed,
    )


if __name__ == "__main__":
    main()
