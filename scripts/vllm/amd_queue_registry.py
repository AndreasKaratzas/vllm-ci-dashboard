#!/usr/bin/env python3
"""AMD queue registry derived from upstream ci-infra routing.

ci-infra's pipeline generator is the source of truth for which Buildkite
queues AMD CI steps target (``AgentQueue`` plus ``AMD_DEVICE_CONFIGS``).  The
refresh CLI parses both files at one resolved commit and caches the result as
last-good dashboard state; collectors read only the cached file, so an
unreachable upstream degrades to the previous registry with its provenance
instead of to a hand-maintained queue list.
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache, partial
from pathlib import Path
from typing import Any, Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.bounded_json import write_pretty_json_lkg  # noqa: E402
from vllm.constants import amd_gpu_hardware  # noqa: E402
from vllm.dashboard_storage_budget import writer_max_bytes  # noqa: E402

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_RELATIVE_PATH = "data/vllm/ci/amd_queue_registry.json"
REGISTRY_PATH = ROOT / REGISTRY_RELATIVE_PATH
REGISTRY_MAX_BYTES = writer_max_bytes("amd_queue_registry")
SCHEMA_VERSION = 1

CI_INFRA_REPOSITORY = "vllm-project/ci-infra"
CI_INFRA_REF = "main"
CI_INFRA_CONSTANTS_PATH = "buildkite/pipeline_generator/constants.py"
CI_INFRA_AMD_PATH = "buildkite/pipeline_generator/amd.py"
GITHUB_API_BASE = "https://api.github.com/repos"
RAW_BASE = "https://raw.githubusercontent.com"
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")

# Queue lifecycle/throughput metrics cover the accelerator families requested
# by the AMD CI operators; MI325, CPU, partner and ``mi355b`` queues stay out.
TARGET_HARDWARE: tuple[str, ...] = ("MI250", "MI300", "MI355")
TARGET_SCOPE_ID = "amd_mi250_mi300_mi355"


class RegistryError(RuntimeError):
    """The upstream routing or the cached registry failed validation."""


def _string_assignments(body: Iterable[ast.stmt]) -> dict[str, str]:
    return {
        node.targets[0].id: node.value.value
        for node in body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _enum_members(module: ast.Module, name: str) -> dict[str, str]:
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return _string_assignments(node.body)
    raise RegistryError(f"ci-infra constants.py has no {name} enum")


def _enum_ref(node: ast.expr | None, enum: str, members: dict[str, str]) -> str:
    if isinstance(node, ast.Attribute) and node.attr == "value":
        node = node.value
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == enum
        and node.attr in members
    ):
        return members[node.attr]
    raise RegistryError(f"ci-infra AMD device config has an unresolvable {enum} reference")


def parse_ci_infra(
    constants_source: str, amd_source: str
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return AMD GPU queues routed by ci-infra in upstream device order, plus the
    device/queue pairs whose queue name the dashboard cannot map to hardware."""
    try:
        constants = ast.parse(constants_source)
        amd = ast.parse(amd_source)
    except SyntaxError as exc:
        raise RegistryError(f"ci-infra source is not valid Python: {exc}") from exc
    devices = _enum_members(constants, "DeviceType")
    queues = _enum_members(constants, "AgentQueue")
    configs = next(
        (
            node.value
            for node in amd.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and isinstance(node.value, ast.Dict)
            and any(
                isinstance(target, ast.Name) and target.id.lstrip("_") == "AMD_DEVICE_CONFIGS"
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            )
        ),
        None,
    )
    if configs is None:
        raise RegistryError("ci-infra amd.py has no AMD_DEVICE_CONFIGS mapping")

    rows: dict[str, dict[str, Any]] = {}
    unrecognized: list[dict[str, str]] = []
    for key, value in zip(configs.keys, configs.values):
        if not isinstance(value, ast.Call):
            raise RegistryError("ci-infra AMD device config is not a constructor call")
        device = _enum_ref(key, "DeviceType", devices)
        keywords = {kw.arg: kw.value for kw in value.keywords if kw.arg}
        queue = _enum_ref(value.args[0] if value.args else keywords.get("queue"), "AgentQueue", queues)
        gpus_node = value.args[1] if len(value.args) > 1 else keywords.get("default_gpu_count")
        tag_node = keywords.get("gpu_size_tag")
        gpus = gpus_node.value if isinstance(gpus_node, ast.Constant) else None
        gpu_size_tag = tag_node.value if isinstance(tag_node, ast.Constant) else False
        if type(gpus) is not int or gpus < 1 or not isinstance(gpu_size_tag, bool):
            raise RegistryError(f"ci-infra AMD device {device} has an invalid GPU shape")
        hardware = amd_gpu_hardware(queue)
        if not hardware:
            unrecognized.append({"device": device, "queue": queue})
            continue
        row = rows.setdefault(
            queue,
            {"queue": queue, "hardware": hardware, "gpu_size_tag": gpu_size_tag, "devices": []},
        )
        if row["gpu_size_tag"] != gpu_size_tag:
            raise RegistryError(f"ci-infra queue {queue} mixes gpu_size routing modes")
        row["devices"].append({"device": device, "gpus": gpus})
    return validate_queues(list(rows.values())), unrecognized


def validate_queues(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise RegistryError("AMD queue registry has no queues")
    seen: set[str] = set()
    lanes: set[str] = set()
    normalized = []
    for row in value:
        if not isinstance(row, dict) or set(row) != {"queue", "hardware", "gpu_size_tag", "devices"}:
            raise RegistryError("AMD queue registry row schema is invalid")
        queue = row["queue"]
        if not isinstance(queue, str) or queue in seen or amd_gpu_hardware(queue) != row["hardware"]:
            raise RegistryError(f"AMD queue registry row {queue!r} is invalid")
        devices = row["devices"]
        if (
            not isinstance(row["gpu_size_tag"], bool)
            or not isinstance(devices, list)
            or not devices
            or any(
                not isinstance(device, dict)
                or set(device) != {"device", "gpus"}
                or not isinstance(device["device"], str)
                or not device["device"]
                or type(device["gpus"]) is not int
                or device["gpus"] < 1
                for device in devices
            )
        ):
            raise RegistryError(f"AMD queue registry devices for {queue} are invalid")
        row_lanes = _row_lanes(row)
        if lanes & set(row_lanes):
            raise RegistryError(f"AMD queue registry lanes for {queue} collide")
        seen.add(queue)
        lanes.update(row_lanes)
        normalized.append(
            {
                "queue": queue,
                "hardware": row["hardware"],
                "gpu_size_tag": row["gpu_size_tag"],
                "devices": [dict(device) for device in devices],
            }
        )
    if not any(row["hardware"] in TARGET_HARDWARE for row in normalized):
        raise RegistryError("AMD queue registry has no target-family queue")
    return normalized


def _row_lanes(row: dict[str, Any]) -> list[str]:
    if not row["gpu_size_tag"]:
        return [row["queue"]]
    return list(dict.fromkeys(f"{row['queue']}_{device['gpus']}" for device in row["devices"]))


def validate_registry(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise RegistryError("AMD queue registry schema_version is unsupported")
    source = payload.get("source")
    if (
        not isinstance(source, dict)
        or source.get("repository") != CI_INFRA_REPOSITORY
        or not FULL_SHA_RE.fullmatch(str(source.get("commit") or ""))
        or source.get("paths") != [CI_INFRA_CONSTANTS_PATH, CI_INFRA_AMD_PATH]
        or not isinstance(source.get("fetched_at"), str)
    ):
        raise RegistryError("AMD queue registry source provenance is invalid")
    try:
        datetime.fromisoformat(source["fetched_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistryError("AMD queue registry fetched_at is invalid") from exc
    fallback = source.get("fallback")
    if fallback is not None and (
        not isinstance(fallback, dict)
        or set(fallback) != {"reason", "attempted_at"}
        or not all(isinstance(value, str) for value in fallback.values())
    ):
        raise RegistryError("AMD queue registry fallback provenance is invalid")
    unrecognized = payload.get("unrecognized_queues", [])
    if not isinstance(unrecognized, list) or any(
        not isinstance(row, dict)
        or set(row) != {"device", "queue"}
        or not all(isinstance(value, str) for value in row.values())
        for row in unrecognized
    ):
        raise RegistryError("AMD queue registry unrecognized_queues is invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "source": dict(source),
        "queues": validate_queues(payload.get("queues")),
        "unrecognized_queues": [dict(row) for row in unrecognized],
    }


def build_registry(
    constants_source: str, amd_source: str, *, commit: str, fetched_at: datetime
) -> dict[str, Any]:
    queues, unrecognized = parse_ci_infra(constants_source, amd_source)
    return validate_registry(
        {
            "schema_version": SCHEMA_VERSION,
            "source": {
                "repository": CI_INFRA_REPOSITORY,
                "ref": CI_INFRA_REF,
                "commit": commit,
                "paths": [CI_INFRA_CONSTANTS_PATH, CI_INFRA_AMD_PATH],
                "fetched_at": _utc_iso(fetched_at),
            },
            "queues": queues,
            "unrecognized_queues": unrecognized,
        }
    )


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _github_headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def fetch_upstream(*, now: datetime | None = None, timeout: float = 30) -> dict[str, Any]:
    """Resolve ci-infra's ref to one commit and parse its AMD routing there."""
    import requests

    response = requests.get(
        f"{GITHUB_API_BASE}/{CI_INFRA_REPOSITORY}/commits/{CI_INFRA_REF}",
        headers=_github_headers(),
        timeout=timeout,
    )
    response.raise_for_status()
    commit = str((response.json() or {}).get("sha") or "").lower()
    if not FULL_SHA_RE.fullmatch(commit):
        raise RegistryError("GitHub did not resolve ci-infra to a full commit SHA")
    sources = []
    for path in (CI_INFRA_CONSTANTS_PATH, CI_INFRA_AMD_PATH):
        file_response = requests.get(
            f"{RAW_BASE}/{CI_INFRA_REPOSITORY}/{commit}/{path}", timeout=timeout
        )
        file_response.raise_for_status()
        sources.append(file_response.text)
    return build_registry(
        sources[0], sources[1], commit=commit, fetched_at=now or datetime.now(timezone.utc)
    )


def _file_registry(path: Path) -> dict[str, Any]:
    return validate_registry(json.loads(path.read_text()))


def _git_registry(ref: str, root: Path) -> dict[str, Any]:
    blob = subprocess.run(
        ["git", "show", f"{ref}:{REGISTRY_RELATIVE_PATH}"],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    return validate_registry(json.loads(blob))


def refresh(
    path: Path,
    *,
    fallback_git_refs: Iterable[str] = (),
    fetch: Callable[[], dict[str, Any]] = fetch_upstream,
    root: Path = ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write the live registry, else the newest valid last-good copy marked as a fallback."""
    try:
        registry = fetch()
    except Exception as exc:
        log.warning("ci-infra AMD queue routing unavailable: %s", exc)
        candidates = []
        loaders: list[tuple[str, Callable[[], dict[str, Any]]]] = [
            (str(path), partial(_file_registry, path)),
            *((ref, partial(_git_registry, ref, root)) for ref in fallback_git_refs),
        ]
        for label, loader in loaders:
            try:
                candidates.append(loader())
            except Exception as cache_exc:
                log.warning("Cached AMD queue registry at %s is unusable: %s", label, cache_exc)
        if not candidates:
            raise RegistryError(
                "ci-infra AMD queue routing is unreachable and no last-good registry exists"
            ) from exc
        registry = max(candidates, key=lambda row: row["source"]["fetched_at"])
        registry["source"]["fallback"] = {
            "reason": f"{type(exc).__name__}: {exc}"[:300],
            "attempted_at": _utc_iso(now or datetime.now(timezone.utc)),
        }
        log.warning(
            "Using last-good AMD queue registry from ci-infra %s fetched at %s",
            registry["source"]["commit"],
            registry["source"]["fetched_at"],
        )
    write_pretty_json_lkg(
        path, registry, max_bytes=REGISTRY_MAX_BYTES, label="AMD queue registry"
    )
    _read_registry.cache_clear()
    _registry_lanes.cache_clear()
    return registry


def load_registry() -> dict[str, Any]:
    return _read_registry(REGISTRY_PATH)


@lru_cache(maxsize=None)
def _read_registry(path: Path) -> dict[str, Any]:
    try:
        return validate_registry(json.loads(path.read_text()))
    except (OSError, ValueError) as exc:
        raise RegistryError(f"AMD queue registry {path} is unreadable: {exc}") from exc


def registry_provenance(registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_registry()
    source = registry["source"]
    return {
        **{key: source[key] for key in ("repository", "commit", "fetched_at")},
        **({"fallback": source["fallback"]} if "fallback" in source else {}),
        **(
            {"unrecognized_queues": registry["unrecognized_queues"]}
            if registry["unrecognized_queues"]
            else {}
        ),
    }


def tracked_queues(registry: dict[str, Any] | None = None) -> tuple[str, ...]:
    """Physical AMD GPU queues that ci-infra currently routes work to."""
    return tuple(row["queue"] for row in (registry or load_registry())["queues"])


def target_queues(registry: dict[str, Any] | None = None) -> tuple[str, ...]:
    """Physical queues of the operator-requested target families."""
    return tuple(
        row["queue"]
        for row in (registry or load_registry())["queues"]
        if row["hardware"] in TARGET_HARDWARE
    )


def target_gpu_widths(registry: dict[str, Any] | None = None) -> list[int]:
    return sorted(
        {
            device["gpus"]
            for row in (registry or load_registry())["queues"]
            if row["hardware"] in TARGET_HARDWARE
            for device in row["devices"]
        }
    )


def shared_queues(registry: dict[str, Any] | None = None) -> frozenset[str]:
    """Physical queues whose GPU widths are split only by a ``gpu_size`` tag."""
    return frozenset(
        row["queue"] for row in (registry or load_registry())["queues"] if row["gpu_size_tag"]
    )


def lanes(registry: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Per-width routing lanes keyed like ``_job_queue`` folds shared queues."""
    return _registry_lanes(REGISTRY_PATH) if registry is None else _lanes(registry)


@lru_cache(maxsize=None)
def _registry_lanes(path: Path) -> dict[str, dict[str, Any]]:
    return _lanes(_read_registry(path))


def _lanes(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in registry["queues"]:
        for device in row["devices"]:
            lane = f"{row['queue']}_{device['gpus']}" if row["gpu_size_tag"] else row["queue"]
            rows.setdefault(
                lane,
                {
                    "id": lane,
                    "queue": row["queue"],
                    "device": device["device"],
                    "hardware": row["hardware"],
                    "gpus_per_job": device["gpus"],
                },
            )
    return rows


def physical_queue(queue: str, registry: dict[str, Any] | None = None) -> str:
    """Fold a gpu_size lane (``amd_mi250_4``) to its shared physical queue."""
    lane = lanes(registry).get(queue)
    return lane["queue"] if lane else queue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--fallback-git-ref",
        action="append",
        default=[],
        help="Git ref holding a last-good registry; the newest valid copy wins.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    registry = refresh(args.output or REGISTRY_PATH, fallback_git_refs=args.fallback_git_ref)
    fallback = registry["source"].get("fallback")
    if fallback:
        print(
            "::warning::ci-infra AMD queue routing unavailable; using the registry fetched at "
            f"{registry['source']['fetched_at']} ({fallback['reason']})"
        )
    for row in registry["unrecognized_queues"]:
        print(
            f"::warning::ci-infra AMD device {row['device']} routes to queue {row['queue']}, "
            "which the dashboard cannot map to AMD hardware"
        )
    log.info(
        "AMD queue registry: %d queues from %s@%s; target scope %s",
        len(registry["queues"]),
        CI_INFRA_REPOSITORY,
        registry["source"]["commit"],
        ", ".join(target_queues(registry)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
