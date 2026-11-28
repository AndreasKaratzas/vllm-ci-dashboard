#!/usr/bin/env python3
"""Decide whether a rebased publication candidate must be retested."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class RetestDecision:
    required: bool
    reasons: tuple[str, ...]
    current_parent: str
    candidate_tree: str


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _full_sha(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not FULL_SHA_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be one full lowercase Git object ID")
    return normalized


def publication_retest_decision(
    root: Path,
    *,
    baseline_parent: str,
    tested_tree: str,
) -> RetestDecision:
    """Compare the staged candidate with the parent/tree covered before rebase."""
    baseline_parent = _full_sha(baseline_parent, "baseline parent")
    tested_tree = _full_sha(tested_tree, "tested tree")
    _git(root, "cat-file", "-e", f"{baseline_parent}^{{commit}}")
    _git(root, "cat-file", "-e", f"{tested_tree}^{{tree}}")
    current_parent = _full_sha(
        _git(root, "rev-parse", "--verify", "HEAD^1"),
        "current publication parent",
    )
    candidate_tree = _full_sha(
        _git(root, "write-tree"),
        "staged candidate tree",
    )
    reasons = []
    if current_parent != baseline_parent:
        reasons.append("publication-parent-changed")
    if candidate_tree != tested_tree:
        reasons.append("publication-tree-changed")
    return RetestDecision(
        required=bool(reasons),
        reasons=tuple(reasons),
        current_parent=current_parent,
        candidate_tree=candidate_tree,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--baseline-parent", required=True)
    parser.add_argument("--tested-tree", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        decision = publication_retest_decision(
            args.root,
            baseline_parent=args.baseline_parent,
            tested_tree=args.tested_tree,
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Could not attest publication retest state: {exc}", file=sys.stderr)
        return 2
    reason = ",".join(decision.reasons) or "unchanged"
    print(
        "Publication retest decision: "
        f"required={str(decision.required).lower()} reason={reason} "
        f"parent={decision.current_parent} tree={decision.candidate_tree}",
        file=sys.stderr,
    )
    print("true" if decision.required else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
