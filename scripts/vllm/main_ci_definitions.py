# cspell:ignore abhl
"""Immutable source definitions for the upstream ``ci`` pipeline.

The legacy test-amd.yaml pipeline never contributes to this inventory. CPU
and other accelerator definitions remain outside the CUDA-to-AMD comparison.
"""

from __future__ import annotations

import io
import os
import re
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests
import yaml

from vllm.reviewed_definition_labels import definition_title


REPOSITORY = "vllm-project/vllm"
API_BASE = f"https://api.github.com/repos/{REPOSITORY}"
FULL_SHA = re.compile(r"[0-9a-f]{40}")
TEST_AREAS = ".buildkite/test_areas/"
CI_CONFIG = ".buildkite/ci_config.yaml"


@dataclass(frozen=True)
class MainCISnapshot:
    commit_sha: str
    files: dict[str, Any]
    fetched_at: str


def load_snapshot(commit: str | None = None) -> MainCISnapshot:
    """Resolve main once, then read its CI files from one commit archive."""
    requested = commit or os.environ.get("VLLM_CONFIG_SHA", "").strip() or "main"
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    sha = requested.lower()
    if not FULL_SHA.fullmatch(sha):
        response = requests.get(f"{API_BASE}/commits/{requested}", headers=headers, timeout=30)
        response.raise_for_status()
        sha = str(response.json().get("sha") or "").lower()
    if not FULL_SHA.fullmatch(sha):
        raise ValueError("main CI definitions require one complete upstream commit SHA")
    response = requests.get(f"{API_BASE}/tarball/{sha}", headers=headers, timeout=120)
    response.raise_for_status()
    files = {}
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile() or "/" not in member.name:
                continue
            path = member.name.split("/", 1)[1]
            if path != CI_CONFIG and not (path.startswith(TEST_AREAS) and path.endswith(".yaml")):
                continue
            handle = archive.extractfile(member)
            if handle is not None:
                files[path] = yaml.safe_load(handle.read().decode("utf-8"))
    snapshot = MainCISnapshot(sha, files, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(snapshot: MainCISnapshot) -> None:
    if not FULL_SHA.fullmatch(snapshot.commit_sha):
        raise ValueError("main CI source commit must be a full SHA")
    config = snapshot.files.get(CI_CONFIG)
    if not isinstance(config, dict) or ".buildkite/test_areas" not in config.get("job_dirs", []):
        raise ValueError("upstream ci_config must include the main test_areas job directory")
    if not any(path.startswith(TEST_AREAS) for path in snapshot.files):
        raise ValueError("main CI snapshot has no test-area definitions")


def source_steps(snapshot: MainCISnapshot) -> list[dict[str, Any]]:
    validate_snapshot(snapshot)
    rows = []
    identities = set()
    for path, document in sorted(snapshot.files.items()):
        if not (path.startswith(TEST_AREAS) and path.endswith(".yaml")):
            continue
        if not isinstance(document, dict) or not isinstance(document.get("steps", []), list):
            raise ValueError(f"main CI definition file is malformed: {path}")
        for index, step in enumerate(document.get("steps", [])):
            if not isinstance(step, dict):
                raise ValueError(f"main CI definition step is malformed: {path}#{index}")
            key = step.get("key")
            if not isinstance(key, str) or not key.strip():
                raise ValueError(
                    f"main CI definition requires an explicit stable key: {path}#{index}"
                )
            identity = f"{path}#{key}"
            if identity in identities:
                raise ValueError(f"duplicate main CI definition: {identity}")
            identities.add(identity)
            rows.append(
                {
                    **step,
                    "source_file": path,
                    "definition_id": identity,
                    "area": str(document.get("group") or path.rsplit("/", 1)[-1][:-5]),
                    "yaml_index": index,
                }
            )
    return rows


def is_cuda_definition(step: dict[str, Any]) -> bool:
    label = str(step.get("label") or "")
    device = str(step.get("device") or "").lower()
    if device.startswith(("cpu", "mi", "amd", "intel", "arm", "ascend")):
        return False
    return bool(
        re.search(r":nvidia:\s*\(", label, flags=re.I)
        or re.fullmatch(r"(?:[abhl]\d+|gh\d+|dgx-spark)(?:[_-].*)?", device)
    )


def is_amd_definition(step: dict[str, Any]) -> bool:
    return bool(
        re.match(r"(?:amd_)?mi\d+", str(step.get("device") or ""), flags=re.I)
        or re.search(r":amd:\s*\(\s*mi\d+", str(step.get("label") or ""), flags=re.I)
    )


def amd_source_steps(snapshot: MainCISnapshot) -> list[dict[str, Any]]:
    """Expand current inline mirrors and native AMD routes into exact steps."""
    routes = []
    for step in source_steps(snapshot):
        mirror = step.get("mirror")
        amd = mirror.get("amd") if isinstance(mirror, dict) else None
        if isinstance(amd, dict) and amd:
            inherited = {key: value for key, value in step.items() if key != "mirror"}
            route = {
                **inherited,
                **amd,
                "definition_id": step["definition_id"],
                "upstream_definition_id": step["definition_id"],
                "source_kind": "inline_mirror",
            }
            route["key"] = str(amd.get("key") or f"amd-{step['key']}")
            if "commands" in amd:
                route.pop("command", None)
            elif "command" in amd:
                route.pop("commands", None)
            if not is_amd_definition(route):
                raise ValueError(f"AMD mirror has no AMD execution route: {step['definition_id']}")
            route["agent_pool"] = str(route.get("device") or route.get("agent_pool") or "")
            routes.append(route)
        elif is_amd_definition(step):
            routes.append(
                {**step, "agent_pool": str(step.get("device") or ""), "source_kind": "native_amd"}
            )
    return routes


def logical_title(step: dict[str, Any]) -> str:
    return definition_title(str(step.get("label") or "")).strip()


def route_is_required(route: dict[str, Any]) -> bool:
    return not bool(route.get("optional") or route.get("soft_fail"))
