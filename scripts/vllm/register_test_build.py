"""Register a user-launched Buildkite build for results polling.

The dashboard's "Test Build" tab creates the build on Buildkite client-side
using the user's Buildkite token, then dispatches this workflow with the
resulting build metadata. We never call the Buildkite API here — the admin's
BUILDKITE_TOKEN stays out of per-user write paths.

All inputs arrive via env vars so the workflow can keep them out of argv:
    TB_BUILD_NUMBER  — Buildkite build number (already created by browser)
    TB_WEB_URL       — Buildkite build web URL
    TB_COMMIT        — resolved commit sha
    TB_MESSAGE       — human-readable description
    TB_BRANCH        — branch name
    TB_ENV           — newline-separated KEY=value pairs
    TB_CLEAN         — "true"/"false" for clean_checkout
    TB_CLEANUP       — "always" | "on_success" | "never"
    TB_FORK_REPO     — "owner/name" of fork, empty = default vllm repo
    TB_BRANCH_REF    — "owner/name:branch" label for the registry
    TB_BASE_IMAGE    — informational
    TB_REQUESTED_BY  — GitHub login of the dispatcher

Writes ``data/vllm/ci/test_builds/index.json``; ``collect_test_builds.py`` later
fetches the results and computes the nightly comparison on the hourly cron.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

log = logging.getLogger("register_test_build")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


PIPELINE_SLUG = "amd-ci"
REGISTRY_DIR = REPO_ROOT / "data" / "vllm" / "ci" / "test_builds"
REGISTRY_FILE = REGISTRY_DIR / "index.json"


def _parse_env(raw: str) -> dict:
    out: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        if k:
            out[k] = v
    return out


def _load_registry() -> list[dict]:
    if not REGISTRY_FILE.exists():
        return []
    try:
        data = json.loads(REGISTRY_FILE.read_text())
        return data if isinstance(data, list) else []
    except Exception as e:
        log.warning("Registry parse failed (%s); starting fresh", e)
        return []


def _save_registry(rows: list[dict]) -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")


def main() -> int:
    build_number_raw = os.getenv("TB_BUILD_NUMBER", "").strip()
    if not build_number_raw:
        log.error("TB_BUILD_NUMBER is required")
        return 1
    try:
        build_number = int(build_number_raw)
    except ValueError:
        log.error("TB_BUILD_NUMBER must be an integer, got %r", build_number_raw)
        return 1

    web_url = os.getenv("TB_WEB_URL", "").strip()
    commit = os.getenv("TB_COMMIT", "").strip() or "HEAD"
    msg = os.getenv("TB_MESSAGE", "").strip() or "Test build (project-dashboard)"
    branch = os.getenv("TB_BRANCH", "").strip() or "main"
    env_raw = os.getenv("TB_ENV", "")
    clean = os.getenv("TB_CLEAN", "").strip().lower() == "true"
    cleanup_mode = os.getenv("TB_CLEANUP", "never").strip().lower() or "never"
    fork_repo = os.getenv("TB_FORK_REPO", "").strip()
    branch_ref = os.getenv("TB_BRANCH_REF", "").strip()
    base_image = os.getenv("TB_BASE_IMAGE", "").strip()
    requested_by = os.getenv("TB_REQUESTED_BY", "").strip() or "unknown"

    env = _parse_env(env_raw)

    entry = {
        "id": f"{PIPELINE_SLUG}-{build_number}",
        "pipeline": PIPELINE_SLUG,
        "build_number": build_number,
        "web_url": web_url,
        "message": msg,
        "branch": branch,
        "commit": commit,
        "branch_ref": branch_ref or f"vllm/vllm-project:{branch}",
        "fork_repo": fork_repo,
        "base_image": base_image,
        "env": env,
        "clean_checkout": clean,
        "cleanup_mode": cleanup_mode,
        "requested_by": requested_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state": "scheduled",
        "results_fetched": False,
        "comparison": None,
    }

    rows = _load_registry()
    rows = [r for r in rows if r.get("build_number") != build_number]
    rows.append(entry)
    _save_registry(rows)

    log.info("Registered test build #%s at %s", build_number, web_url)

    gh_out = os.getenv("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.write(f"build_number={build_number}\n")
            fh.write(f"web_url={web_url}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
