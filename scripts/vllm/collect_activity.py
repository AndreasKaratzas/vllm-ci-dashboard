#!/usr/bin/env python3
"""Enriched activity collector for vLLM ROCm contributions.

Fetches full PR details (diff stats, files, commits, review comments)
from GitHub and computes importance scores for each PR.

Produces:
- data/vllm/engineer_activity.json — per-engineer profiles with scores
- data/vllm/pr_scores.json — per-PR importance scores and breakdown

Usage:
    export GITHUB_TOKEN="ghp_..."  # or use gh CLI auth
    python scripts/vllm/collect_activity.py
    python scripts/vllm/collect_activity.py --days 30    # lookback window
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.pr_scoring import score_pr, compute_engineer_profile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "vllm"

REPO = "vllm-project/vllm"
ROCM_LABELS = {"rocm", "amd"}
ROCM_KEYWORDS = {"rocm", "amd", "hip", "mi250", "mi300", "mi325", "mi355"}

BOT_PATTERNS = {"bot", "copybara", "web-flow", "dependabot", "renovate"}


def gh_api(endpoint: str) -> dict | list:
    """Call GitHub API via gh CLI."""
    cmd = ["gh", "api", endpoint, "--method", "GET"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except subprocess.CalledProcessError as e:
        log.warning("gh api %s failed: %s", endpoint, e.stderr.strip()[:100])
        return {}
    except json.JSONDecodeError:
        return {}


def gh_api_paginated(endpoint: str, max_pages: int = 10) -> list:
    """Fetch all pages from a paginated GitHub endpoint."""
    items = []
    for page in range(1, max_pages + 1):
        sep = "&" if "?" in endpoint else "?"
        data = gh_api(f"{endpoint}{sep}page={page}&per_page=100")
        if isinstance(data, list):
            if not data:
                break
            items.extend(data)
            if len(data) < 100:
                break
        elif isinstance(data, dict) and "items" in data:
            items.extend(data["items"])
            if len(data["items"]) < 100:
                break
        else:
            break
    return items


def is_bot(author: str) -> bool:
    if not author:
        return True
    a = author.lower()
    return any(p in a for p in BOT_PATTERNS)


def is_rocm_pr(pr: dict) -> bool:
    """Check if a PR is ROCm-related (by labels, title, or author)."""
    labels = {l.get("name", "").lower() if isinstance(l, dict) else l.lower()
              for l in pr.get("labels", [])}
    if labels & ROCM_LABELS:
        return True
    title = (pr.get("title") or "").lower()
    if any(kw in title for kw in ROCM_KEYWORDS):
        return True
    return False


def fetch_pr_details(pr_number: int) -> dict:
    """Fetch full PR details including diff stats and files."""
    pr_data = gh_api(f"/repos/{REPO}/pulls/{pr_number}")
    if not pr_data:
        return {}

    # Fetch files (for file-type classification)
    files = gh_api_paginated(f"/repos/{REPO}/pulls/{pr_number}/files", max_pages=3)

    commit_count = pr_data.get("commits", 0)

    # vLLM-specific: fetch Buildkite build count for this PR's branch
    # This KPI is only applicable to vLLM (Buildkite CI). Other projects
    # in the dashboard use GitHub Actions and would need a different approach.
    bk_build_count = _fetch_vllm_bk_build_count(pr_data)

    return {
        "number": pr_number,
        "title": pr_data.get("title", ""),
        "author": (pr_data.get("user") or {}).get("login", ""),
        "state": pr_data.get("state", ""),
        "merged": pr_data.get("merged", False),
        "draft": pr_data.get("draft", False),
        "created_at": pr_data.get("created_at", ""),
        "updated_at": pr_data.get("updated_at", ""),
        "merged_at": pr_data.get("merged_at"),
        "closed_at": pr_data.get("closed_at"),
        "additions": pr_data.get("additions", 0),
        "deletions": pr_data.get("deletions", 0),
        "changed_files": pr_data.get("changed_files", 0),
        "commits": commit_count,
        "review_comments": pr_data.get("review_comments", 0),
        "comments": pr_data.get("comments", 0),
        "html_url": pr_data.get("html_url", ""),
        "ci_build_count": bk_build_count,  # vLLM-specific: Buildkite builds
        "labels": [l.get("name", "") if isinstance(l, dict) else l
                   for l in pr_data.get("labels", [])],
        "files": [
            {
                "filename": f.get("filename", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "status": f.get("status", ""),
            }
            for f in files
        ],
    }


def _fetch_vllm_bk_build_count(pr_data: dict) -> int:
    """Count Buildkite builds for this PR's branch on vLLM's amd-ci pipeline.

    vLLM-SPECIFIC. This fetches from the vLLM Buildkite org/pipeline.
    Other projects should NOT call this — it only works for vLLM.

    This is a proxy for testing effort — more builds = more iteration.
    Requires BUILDKITE_TOKEN env var; returns 0 if not set.
    """
    import os
    token = os.getenv("BUILDKITE_TOKEN")
    if not token:
        return 0

    # PR branch name from the head ref
    head = pr_data.get("head", {})
    branch = head.get("ref", "")
    if not branch:
        return 0

    import requests
    headers = {"Authorization": f"Bearer {token}"}
    # Search AMD CI pipeline for builds on this branch
    url = f"https://api.buildkite.com/v2/organizations/vllm/pipelines/amd-ci/builds"
    params = {"branch": branch, "per_page": 1}  # We just need the count from headers

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            # Buildkite doesn't return total_count; count by checking pagination
            # Fetch up to 100 to get a rough count
            params["per_page"] = 100
            resp2 = requests.get(url, headers=headers, params=params, timeout=10)
            if resp2.status_code == 200:
                return len(resp2.json())
        return 0
    except Exception:
        return 0


def main():
    parser = argparse.ArgumentParser(description="Collect enriched vLLM ROCm activity data")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days (default: 30)")
    parser.add_argument("--output", type=str, default=str(DATA), help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load existing PR data as starting point
    prs_file = output_dir / "prs.json"
    if not prs_file.exists():
        log.error("No prs.json found at %s — run collect.py first", prs_file)
        return

    with open(prs_file) as f:
        prs_data = json.load(f)

    tracked_prs = prs_data.get("prs", [])
    log.info("Found %d tracked PRs in prs.json", len(tracked_prs))

    # Filter to ROCm-related PRs
    rocm_prs = [p for p in tracked_prs if is_rocm_pr(p)]
    log.info("Found %d ROCm-related PRs", len(rocm_prs))

    # Fetch full details for each PR
    scored_prs = []
    for i, pr in enumerate(rocm_prs):
        pr_num = pr.get("number")
        if not pr_num:
            continue

        log.info("  [%d/%d] PR #%d: %s", i + 1, len(rocm_prs), pr_num, pr.get("title", "")[:60])

        details = fetch_pr_details(pr_num)
        if not details:
            log.warning("    Failed to fetch details, using basic data")
            details = pr
            details.setdefault("additions", 0)
            details.setdefault("deletions", 0)
            details.setdefault("changed_files", 0)
            details.setdefault("commits", 0)
            details.setdefault("review_comments", 0)
            details.setdefault("comments", 0)
            details.setdefault("files", [])

        # Score the PR
        importance = score_pr(details)
        details["importance"] = importance

        log.info(
            "    Score: %.1f (%s) | +%d/-%d lines, %d files, %d commits",
            importance["score"], importance["category"],
            details.get("additions", 0), details.get("deletions", 0),
            details.get("changed_files", 0), details.get("commits", 0),
        )

        scored_prs.append(details)

        # Rate limit protection
        if (i + 1) % 10 == 0:
            time.sleep(1)

    # Build engineer profiles
    by_author = defaultdict(list)
    for pr in scored_prs:
        author = pr.get("author", "")
        if author and not is_bot(author):
            by_author[author].append(pr)

    profiles = []
    for author, prs in sorted(by_author.items()):
        profile = compute_engineer_profile(author, prs)
        profiles.append(profile)

    # Sort by activity score (highest first)
    profiles.sort(key=lambda p: p["activity_score"], reverse=True)

    # Write outputs
    pr_scores_path = output_dir / "pr_scores.json"
    pr_scores_data = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_prs_scored": len(scored_prs),
        "score_distribution": {
            "major": len([p for p in scored_prs if p["importance"]["category"] == "major"]),
            "significant": len([p for p in scored_prs if p["importance"]["category"] == "significant"]),
            "moderate": len([p for p in scored_prs if p["importance"]["category"] == "moderate"]),
            "minor": len([p for p in scored_prs if p["importance"]["category"] == "minor"]),
            "trivial": len([p for p in scored_prs if p["importance"]["category"] == "trivial"]),
        },
        "prs": [
            {
                "number": p["number"],
                "title": p.get("title", ""),
                "author": p.get("author", ""),
                "state": p.get("state", ""),
                "merged": p.get("merged", False),
                "draft": p.get("draft", False),
                "html_url": p.get("html_url", ""),
                "importance": p["importance"],
            }
            for p in sorted(scored_prs, key=lambda x: x["importance"]["score"], reverse=True)
        ],
    }
    pr_scores_path.write_text(json.dumps(pr_scores_data, indent=2))
    log.info("Wrote %s (%d PRs scored)", pr_scores_path, len(scored_prs))

    engineer_path = output_dir / "engineer_activity.json"
    engineer_data = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_engineers": len(profiles),
        "profiles": [p for p in profiles],
    }
    engineer_path.write_text(json.dumps(engineer_data, indent=2))
    log.info("Wrote %s (%d engineers)", engineer_path, len(profiles))

    # Print summary
    print("\n" + "=" * 60)
    print("ENGINEER ACTIVITY SUMMARY")
    print("=" * 60)
    for p in profiles[:15]:
        print(
            f"  {p['author']:25s} | Score: {p['activity_score']:6.1f} | "
            f"PRs: {p['total_prs']:2d} ({p['merged']} merged) | "
            f"+{p['total_additions']}/{-p['total_deletions']} lines | "
            f"Avg importance: {p['avg_importance']:.1f}"
        )
    print("=" * 60)

    # PR score distribution
    print("\nPR SCORE DISTRIBUTION")
    for cat in ["major", "significant", "moderate", "minor", "trivial"]:
        count = pr_scores_data["score_distribution"][cat]
        bar = "#" * count
        print(f"  {cat:12s}: {count:3d} {bar}")
    print()


if __name__ == "__main__":
    main()
