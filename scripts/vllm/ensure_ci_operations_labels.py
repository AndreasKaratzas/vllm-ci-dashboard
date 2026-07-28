#!/usr/bin/env python3
"""Ensure labels required by dashboard issue and Project automation exist."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.ci.managed_issue import (  # noqa: E402
    DASHBOARD_REPO,
    GitHubIssueClient,
    validate_target_repo,
)


LABEL_SPECS = [
    ("automated", "6f42c1", "Managed by dashboard automation"),
    ("amd-ci-regression", "d73a49", "Latest AMD CI target regression"),
    ("workstream:infra", "0e8a16", "AMD CI infrastructure and capacity"),
    (
        "workstream:dashboard-ci",
        "5319e7",
        "Dashboard collection, validation, and deployment CI",
    ),
    ("workstream:dev", "1d76db", "AMD CI test-area development"),
]


def run() -> int:
    repo = os.getenv("GITHUB_REPOSITORY") or DASHBOARD_REPO
    validate_target_repo(repo)
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        print("GITHUB_TOKEN is required to ensure CI Operations labels", file=sys.stderr)
        return 1
    client = GitHubIssueClient(token, repo)
    failed = [
        name
        for name, color, description in LABEL_SPECS
        if not client.ensure_label(name, color, description)
    ]
    if failed:
        print(f"Could not ensure CI Operations labels: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"Ensured {len(LABEL_SPECS)} CI Operations labels")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
