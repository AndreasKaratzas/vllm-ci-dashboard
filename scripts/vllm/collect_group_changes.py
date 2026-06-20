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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

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
        "_no_change_shas": sorted(no_change_shas),
    }
    out_path.write_text(json.dumps(result, indent=2))
    log.info("Wrote %s (%d changes, %d new)", out_path, len(unique_changes), new_processed)


if __name__ == "__main__":
    main()
