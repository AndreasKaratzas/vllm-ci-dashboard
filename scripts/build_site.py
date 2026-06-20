#!/usr/bin/env python3
"""Assemble the published static site from the shell in docs/ and JSON in data/."""

from __future__ import annotations

import argparse
import re
import shutil
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA = ROOT / "data"
CACHE_BUST_RE = re.compile(r"\?v=\d+")


def copy_tree_contents(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dest / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)


def cache_bust_index(index_path: Path, stamp: str) -> None:
    if not index_path.exists():
        return
    text = index_path.read_text()
    updated = CACHE_BUST_RE.sub(f"?v={stamp}", text)
    index_path.write_text(updated)


def build_site(output_dir: Path, cache_bust: bool) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    copy_tree_contents(DOCS, output_dir)
    shutil.copytree(DATA, output_dir / "data", dirs_exist_ok=True)
    (output_dir / ".nojekyll").write_text("")
    if cache_bust:
        cache_bust_index(output_dir / "index.html", str(int(time.time())))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assemble the GitHub Pages site from docs/ and data/."
    )
    parser.add_argument(
        "--output",
        default="_site",
        help="Output directory relative to repo root (default: _site)",
    )
    parser.add_argument(
        "--cache-bust-index",
        action="store_true",
        help="Rewrite ?v=... asset query strings in index.html with the current Unix timestamp.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_site(ROOT / args.output, cache_bust=args.cache_bust_index)


if __name__ == "__main__":
    main()
