# cspell:ignore abhl
"""Immutable source definitions for the upstream ``ci`` pipeline.

The legacy test-amd.yaml pipeline never contributes to this inventory. CPU
and other accelerator definitions remain outside the CUDA-to-AMD comparison.
"""

from __future__ import annotations

import io
import base64
import hashlib
import json
import os
import re
import tarfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
from functools import lru_cache, wraps
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
    definition_tree_sha: str = ""


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


def is_cpu_only_definition(step: dict[str, Any]) -> bool:
    """Identify explicit CPU execution, preserving GPU CPU-offload workloads."""
    device = str(step.get("device") or step.get("agent_pool") or "").strip().casefold()
    label = str(step.get("label") or "")
    return bool(
        step.get("no_gpu") is True
        or step.get("source_no_gpu") is True
        or any(type(step.get(key)) is int and step[key] == 0 for key in ("num_devices", "num_gpus", "gpu_count"))
        or device in {"cpu", "amd-cpu", "amd_cpu", "arm", "intel"}
        or device.startswith(("cpu_", "cpu-", "arm_", "arm-", "intel_", "intel-"))
        or re.search(r":(?:computer|amd):\s*\(\s*(?:cpu|arm|intel)\b", label, re.I)
    )


def is_cuda_definition(step: dict[str, Any]) -> bool:
    if is_cpu_only_definition(step):
        return False
    label = str(step.get("label") or "")
    device = str(step.get("device") or "").lower()
    if device.startswith(("cpu", "mi", "amd", "intel", "arm", "ascend")):
        return False
    return bool(
        re.search(r":nvidia:\s*\(", label, flags=re.I)
        or re.fullmatch(r"(?:[abhl]\d+|gh\d+|dgx-spark)(?:[_-].*)?", device)
    )


def is_amd_definition(step: dict[str, Any]) -> bool:
    return bool(re.fullmatch(r"(?:amd_)?mi\d+b?(?:_[a-z0-9_-]+)?", str(step.get("device") or step.get("agent_pool") or ""), flags=re.I))


def amd_source_steps(
    snapshot: MainCISnapshot, *, include_cpu: bool = False
) -> list[dict[str, Any]]:
    """Expand exact MI GPU routes; CPU declarations are opt-in for proof joins."""
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
                if is_cpu_only_definition(route):
                    continue
                raise ValueError(f"AMD mirror has no AMD execution route: {step['definition_id']}")
            route["agent_pool"] = str(route.get("device") or route.get("agent_pool") or "")
            if include_cpu or not is_cpu_only_definition(route):
                routes.append(route)
        elif is_amd_definition(step):
            if include_cpu or not is_cpu_only_definition(step):
                routes.append(
                    {**step, "agent_pool": str(step.get("device") or ""), "source_kind": "native_amd"}
                )
    return routes


RUNTIME_SOURCE_MAX_BYTES = 8 * 1024 * 1024
RUNTIME_SOURCE_MAX_FILES = 128
RUNTIME_SOURCE_MAX_REQUESTS = 2400
RUNTIME_SOURCE_MAX_SECONDS = 600
RUNTIME_SOURCE_BATCH_SIZE = 50
RUNTIME_SOURCE_MAX_PINS = 4096
_RUNTIME_SOURCE_LOCK = threading.RLock()
_RUNTIME_SOURCE_STARTS = 0
_RUNTIME_SOURCE_STARTED_AT: float | None = None
_RUNTIME_SOURCE_ACTIVE_SECONDS = 0.0
_RUNTIME_SOURCE_ACTIVE_DEPTH = 0
_RUNTIME_SOURCE_GRAPHQL_FALLBACKS = 0
_RUNTIME_PRIMED_BUILDKITE_TREES: dict[str, str] = {}


def _runtime_active_seconds() -> float:
    return _RUNTIME_SOURCE_ACTIVE_SECONDS + (
        max(0.0, time.monotonic() - _RUNTIME_SOURCE_STARTED_AT)
        if _RUNTIME_SOURCE_STARTED_AT is not None else 0.0
    )


def runtime_source_request_stats() -> dict[str, int | float]:
    with _RUNTIME_SOURCE_LOCK:
        return {
            "request_starts": _RUNTIME_SOURCE_STARTS,
            "max_request_starts": RUNTIME_SOURCE_MAX_REQUESTS,
            "active_source_seconds": round(_runtime_active_seconds(), 3),
            "max_active_source_seconds": RUNTIME_SOURCE_MAX_SECONDS,
            "graphql_rest_fallbacks": _RUNTIME_SOURCE_GRAPHQL_FALLBACKS,
        }


class RuntimeSourceError(ValueError):
    """Bounded source failure; response bodies and exception messages stay private."""

    def __init__(
        self, message: str = "Exact runtime source verification failed", *,
        reason_class: str = "schema-drift", commit_sha: str | None = None,
        phase: str = "scope", http_status: int | None = None,
    ) -> None:
        # The compatibility message argument deliberately never reaches logs.
        self.reason_class = reason_class if isinstance(reason_class, str) and reason_class in {
            "payload-budget", "rate-limit", "timeout", "schema-drift",
            "transient-http", "network", "dependency-unavailable", "command-error",
        } else "schema-drift"
        self.phase = phase if isinstance(phase, str) and phase in {
            "batch", "commit", "tree", "blob", "definitions", "scope", "metadata",
        } else "scope"
        self.commit_sha = commit_sha if isinstance(commit_sha, str) and FULL_SHA.fullmatch(commit_sha) else None
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.diagnostics = {
            "reason_class": self.reason_class, "phase": self.phase,
            "commit_sha": self.commit_sha, "http_status": self.http_status,
            **runtime_source_request_stats(),
        }
        explanation = (
            "Authenticated immutable source evidence is unavailable."
            if self.reason_class == "dependency-unavailable" else
            "Immutable source evidence is incomplete or does not match its Git identity."
            if self.reason_class == "schema-drift" else
            "Exact runtime source acquisition failed within its unchanged bounds."
        )
        super().__init__(
            f"RuntimeSourceError[reason_class={self.reason_class}, phase={self.phase}, "
            f"commit={self.commit_sha or 'none'}] {explanation} "
            + json.dumps(self.diagnostics, separators=(",", ":"), sort_keys=True)
        )


def _check_runtime_source_time() -> None:
    with _RUNTIME_SOURCE_LOCK:
        if _runtime_active_seconds() >= RUNTIME_SOURCE_MAX_SECONDS:
            raise RuntimeSourceError(reason_class="timeout", phase="metadata")


@contextmanager
def _runtime_source_activity():
    """Charge concurrent/nested source work once; unrelated collector idle is free."""
    global _RUNTIME_SOURCE_STARTED_AT, _RUNTIME_SOURCE_ACTIVE_SECONDS, _RUNTIME_SOURCE_ACTIVE_DEPTH
    with _RUNTIME_SOURCE_LOCK:
        _check_runtime_source_time()
        if _RUNTIME_SOURCE_ACTIVE_DEPTH == 0:
            _RUNTIME_SOURCE_STARTED_AT = time.monotonic()
        _RUNTIME_SOURCE_ACTIVE_DEPTH += 1
    try:
        yield
    finally:
        with _RUNTIME_SOURCE_LOCK:
            _RUNTIME_SOURCE_ACTIVE_DEPTH -= 1
            if _RUNTIME_SOURCE_ACTIVE_DEPTH == 0:
                _RUNTIME_SOURCE_ACTIVE_SECONDS = _runtime_active_seconds()
                _RUNTIME_SOURCE_STARTED_AT = None


def _runtime_source_operation(phase: str, *, commit_argument: bool = False):
    def decorate(function):
        @wraps(function)
        def operation(*args, **kwargs):
            argument = args[0] if commit_argument and args else None
            commit = argument if isinstance(argument, str) else (
                argument.get("commit") if isinstance(argument, dict) else None
            )
            try:
                with _runtime_source_activity():
                    result = function(*args, **kwargs)
                    _check_runtime_source_time()
                    return result
            except RuntimeSourceError as exc:
                if commit and not exc.commit_sha:
                    raise RuntimeSourceError(reason_class=exc.reason_class, commit_sha=commit, phase=phase, http_status=exc.http_status) from None
                raise
            except requests.Timeout:
                raise RuntimeSourceError(reason_class="timeout", commit_sha=commit, phase=phase) from None
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                reason = "rate-limit" if status == 429 else "transient-http" if status in {500, 502, 503, 504} else "dependency-unavailable"
                raise RuntimeSourceError(reason_class=reason, commit_sha=commit, phase=phase, http_status=status) from None
            except requests.exceptions.JSONDecodeError:
                raise RuntimeSourceError(commit_sha=commit, phase=phase) from None
            except requests.RequestException:
                raise RuntimeSourceError(reason_class="network", commit_sha=commit, phase=phase) from None
            except (ValueError, TypeError, KeyError, UnicodeError, yaml.YAMLError):
                raise RuntimeSourceError(commit_sha=commit, phase=phase) from None
        return operation
    return decorate


def _verify_runtime_tree(tree_sha: str, rows: list[dict], *, recursive: bool) -> None:
    """Check Git tree bytes, including every subtree in a recursive response."""
    entries: dict[str, list[dict]] = {"": []}
    expected = {"": tree_sha}
    for row in rows:
        kind, mode = row.get("type"), row.get("mode")
        if (kind, mode) not in {("tree", "040000"), ("blob", "100644"), ("blob", "100755"), ("blob", "120000"), ("commit", "160000")}:
            raise ValueError("runtime source tree entry has an invalid Git mode")
        parent, _, name = row["path"].rpartition("/")
        if parent and not recursive:
            raise ValueError("nonrecursive runtime tree contains a nested entry")
        entries.setdefault(parent, []).append({**row, "name": name})
        if kind == "tree" and recursive:
            expected[row["path"]] = row["sha"]
            entries.setdefault(row["path"], [])
    if set(entries) != set(expected):
        raise ValueError("recursive runtime tree omits a parent subtree")
    for path, children in entries.items():
        ordered = sorted(children, key=lambda row: (row["name"] + ("/" if row["type"] == "tree" else "")).encode("utf-8"))
        payload = b"".join(
            row["mode"].lstrip("0").encode() + b" " + row["name"].encode("utf-8") + b"\0" + bytes.fromhex(row["sha"])
            for row in ordered
        )
        oid = hashlib.sha1(f"tree {len(payload)}\0".encode() + payload).hexdigest()
        if oid != expected[path]:
            raise ValueError("runtime source tree bytes do not match their immutable Git identity")


def _runtime_source_headers() -> dict[str, str]:
    """Reserve one authenticated HTTP start under the shared source bounds."""
    global _RUNTIME_SOURCE_STARTS
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeSourceError(reason_class="dependency-unavailable", phase="metadata")
    with _RUNTIME_SOURCE_LOCK:
        _check_runtime_source_time()
        if _RUNTIME_SOURCE_STARTS >= RUNTIME_SOURCE_MAX_REQUESTS:
            raise RuntimeSourceError(reason_class="rate-limit", phase="metadata")
        _RUNTIME_SOURCE_STARTS += 1
    headers = {"Accept": "application/vnd.github+json"}
    headers["Authorization"] = f"Bearer {token}"
    return headers


def _runtime_source_timeout() -> float:
    with _RUNTIME_SOURCE_LOCK:
        remaining = RUNTIME_SOURCE_MAX_SECONDS - _runtime_active_seconds()
        if remaining <= 0:
            raise RuntimeSourceError(reason_class="timeout", phase="metadata")
        return min(30.0, remaining)


@_runtime_source_operation("metadata")
def _runtime_source_json(path: str, *, params: dict[str, str] | None = None) -> dict:
    headers = _runtime_source_headers()
    response = requests.get(f"{API_BASE}/git/{path}", headers=headers, params=params, timeout=_runtime_source_timeout())
    response.raise_for_status()
    if len(response.content) > RUNTIME_SOURCE_MAX_BYTES:
        raise RuntimeSourceError(reason_class="payload-budget", phase="metadata")
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError("runtime source metadata must be an object")
    return value


def prewarm_runtime_snapshots(fullpins: list[str]) -> dict[str, str]:
    """Batch exact commit/root-tree proof; definitions stay lazily shared.

    Callers can prime fifty pins, derive and checkpoint their compact indexes,
    then advance to the next batch. This acquisition never supplies runtime
    observations, clocks or completeness. Schema reference:
    https://docs.github.com/en/graphql/reference/git
    """
    if not isinstance(fullpins, list) or any(not isinstance(pin, str) or not FULL_SHA.fullmatch(pin) for pin in fullpins):
        raise RuntimeSourceError(phase="batch")
    pins = list(dict.fromkeys(fullpins))
    with _RUNTIME_SOURCE_LOCK:
        if len(set(pins) | set(_RUNTIME_PRIMED_BUILDKITE_TREES)) > RUNTIME_SOURCE_MAX_PINS:
            raise RuntimeSourceError(reason_class="payload-budget", phase="batch")
        pending = [pin for pin in pins if pin not in _RUNTIME_PRIMED_BUILDKITE_TREES]
    for offset in range(0, len(pending), RUNTIME_SOURCE_BATCH_SIZE):
        batch = pending[offset:offset + RUNTIME_SOURCE_BATCH_SIZE]
        resolved, fallback_diagnostic = _runtime_batch_roots(batch)
        # Admit the whole batch only after every requested object is proved.
        with _RUNTIME_SOURCE_LOCK:
            if len(set(resolved) | set(_RUNTIME_PRIMED_BUILDKITE_TREES)) > RUNTIME_SOURCE_MAX_PINS:
                raise RuntimeSourceError(reason_class="payload-budget", phase="batch")
            if any(pin in _RUNTIME_PRIMED_BUILDKITE_TREES and _RUNTIME_PRIMED_BUILDKITE_TREES[pin] != tree for pin, tree in resolved.items()):
                raise RuntimeSourceError(phase="batch")
            _RUNTIME_PRIMED_BUILDKITE_TREES.update(resolved)
        if fallback_diagnostic is not None:
            print("Runtime source REST fallback verified " + json.dumps(
                {**fallback_diagnostic, **runtime_source_request_stats()},
                separators=(",", ":"), sort_keys=True,
            ), flush=True)
    with _RUNTIME_SOURCE_LOCK:
        return {pin: _RUNTIME_PRIMED_BUILDKITE_TREES[pin] for pin in pins}


@_runtime_source_operation("batch")
def _runtime_batch_roots(batch: list[str]) -> tuple[dict[str, str], dict | None]:
    """Resolve a whole batch before admitting any immutable associations."""
    global _RUNTIME_SOURCE_GRAPHQL_FALLBACKS
    aliases = {f"c{index}": pin for index, pin in enumerate(batch)}
    fields = " ".join(
        f'{alias}: object(oid: "{pin}") {{ __typename ... on Commit {{ oid tree {{ oid entries {{ name mode type oid }} }} }} }}'
        for alias, pin in aliases.items()
    )
    query = f'query {{ repository(owner: "vllm-project", name: "vllm") {{ nameWithOwner {fields} }} }}'
    response = requests.post("https://api.github.com/graphql", headers=_runtime_source_headers(), json={"query": query}, timeout=_runtime_source_timeout())
    response.raise_for_status()
    if len(response.content) > RUNTIME_SOURCE_MAX_BYTES:
        raise RuntimeSourceError(reason_class="payload-budget", phase="batch")
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError("runtime source batch returned incomplete GraphQL evidence")
    errors = value.get("errors", [])
    if (not isinstance(errors, list) or len(errors) > 100
            or any(not isinstance(error, dict) or not isinstance(error.get("message"), str)
                   or not error["message"].strip() for error in errors)):
        raise ValueError("runtime source batch returned malformed GraphQL errors")
    if errors:
        if getattr(response, "status_code", 200) != 200:
            raise ValueError("runtime source fallback requires an HTTP200 GraphQL error")
        # No errored GraphQL data enters the proof. Resolve every exact pin
        # independently through REST and verify its root Git tree bytes.
        with _RUNTIME_SOURCE_LOCK:
            _RUNTIME_SOURCE_GRAPHQL_FALLBACKS += 1
        resolved = {pin: _runtime_rest_root(pin) for pin in batch}
        allowed_types = {"RATE_LIMITED", "NOT_FOUND", "FORBIDDEN", "INTERNAL",
                         "UNPROCESSABLE", "SERVICE_UNAVAILABLE", "MAX_NODE_LIMIT_EXCEEDED",
                         "GRAPHQL_VALIDATION_FAILED"}
        codes = set()
        failed_pins = set()
        for error in errors:
            extensions = error.get("extensions")
            code = error.get("type") or (extensions.get("code") if isinstance(extensions, dict) else None)
            codes.add(code if isinstance(code, str) and code in allowed_types else "other")
            path = error.get("path")
            if isinstance(path, list) and len(path) >= 2 and path[0] == "repository" and isinstance(path[1], str) and path[1] in aliases:
                failed_pins.add(aliases[path[1]])
        return resolved, {"event": "runtime-source-rest-fallback", "phase": "batch",
                          "graphql_error_types": sorted(codes), "failed_commit_shas": sorted(failed_pins),
                          "verified_pins": len(resolved)}
    if not isinstance(value.get("data"), dict):
        raise ValueError("runtime source batch returned incomplete GraphQL evidence")
    repository = value["data"].get("repository")
    if (set(value["data"]) != {"repository"} or not isinstance(repository, dict)
            or set(repository) != {"nameWithOwner", *aliases} or repository.get("nameWithOwner") != REPOSITORY):
        raise ValueError("runtime source batch repository or aliases do not match")
    resolved = {}
    for alias, pin in aliases.items():
        try:
            commit = repository[alias]
            if (not isinstance(commit, dict) or set(commit) != {"__typename", "oid", "tree"}
                    or commit.get("__typename") != "Commit" or commit.get("oid") != pin):
                raise ValueError("runtime source batch commit identity does not match")
            tree = commit["tree"]
            if not isinstance(tree, dict) or set(tree) != {"oid", "entries"} or not FULL_SHA.fullmatch(str(tree.get("oid") or "")):
                raise ValueError("runtime source batch root tree is malformed")
            entries = tree["entries"]
            if not isinstance(entries, list) or len(entries) > 2048:
                raise ValueError("runtime source batch root tree is incomplete")
            rows = []
            seen = set()
            for entry in entries:
                if (not isinstance(entry, dict) or set(entry) != {"name", "mode", "type", "oid"}
                        or not isinstance(entry.get("name"), str) or type(entry.get("mode")) is not int
                        or not isinstance(entry.get("type"), str)
                        or not FULL_SHA.fullmatch(str(entry.get("oid") or ""))):
                    raise ValueError("runtime source batch root entry is malformed")
                name = entry["name"]
                if not name or name in {".", ".."} or "/" in name or "\0" in name or name in seen:
                    raise ValueError("runtime source batch root entry path is invalid")
                seen.add(name)
                rows.append({"path": name, "mode": format(entry["mode"], "06o"),
                             "type": entry["type"], "sha": entry["oid"]})
            _verify_runtime_tree(tree["oid"], rows, recursive=False)
            buildkite = next((row for row in rows if row["path"] == ".buildkite"), None)
            if not buildkite or buildkite["type"] != "tree":
                raise ValueError("runtime source batch commit lacks main CI definitions")
            resolved[pin] = buildkite["sha"]
        except RuntimeSourceError:
            raise
        except (ValueError, TypeError, KeyError):
            raise RuntimeSourceError(commit_sha=pin, phase="batch") from None
    return resolved, None


@_runtime_source_operation("commit", commit_argument=True)
def _runtime_rest_root(commit_sha: str) -> str:
    root_tree = _runtime_commit_tree(commit_sha)
    actual_tree, rows = _runtime_tree(root_tree)
    if actual_tree != root_tree:
        raise ValueError("runtime commit tree identity changed")
    buildkite = next((row for row in rows if row["path"] == ".buildkite"), None)
    if not buildkite or buildkite.get("type") != "tree":
        raise ValueError("runtime commit lacks the main CI source tree")
    return buildkite["sha"]


@lru_cache(maxsize=256)
@_runtime_source_operation("tree")
def _runtime_tree(object_sha: str, recursive: bool = False) -> tuple[str, tuple[dict, ...]]:
    if not FULL_SHA.fullmatch(object_sha):
        raise ValueError("runtime source tree requires an exact full SHA")
    value = _runtime_source_json(f"trees/{object_sha}", params={"recursive": "1"} if recursive else None)
    tree_sha = str(value.get("sha") or "")
    rows = value.get("tree")
    if tree_sha != object_sha or value.get("truncated") is not False or not isinstance(rows, list) or len(rows) > 2048:
        raise ValueError("runtime source tree is incomplete or malformed")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str) or not FULL_SHA.fullmatch(str(row.get("sha") or "")):
            raise ValueError("runtime source tree entry is malformed")
        path = row["path"]
        if path in seen or not path or path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
            raise ValueError("runtime source tree entry has an invalid path")
        seen.add(path)
    _verify_runtime_tree(tree_sha, rows, recursive=recursive)
    return tree_sha, tuple(rows)


@lru_cache(maxsize=1024)
@_runtime_source_operation("blob")
def _runtime_blob(object_sha: str) -> bytes:
    value = _runtime_source_json(f"blobs/{object_sha}")
    if value.get("sha") != object_sha or value.get("encoding") != "base64" or type(value.get("size")) is not int:
        raise ValueError("runtime source blob identity is malformed")
    try:
        encoded = str(value.get("content") or "").replace("\n", "")
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("runtime source blob encoding is malformed") from exc
    if len(payload) > RUNTIME_SOURCE_MAX_BYTES:
        raise RuntimeSourceError(reason_class="payload-budget", phase="blob")
    if len(payload) != value["size"]:
        raise ValueError("runtime source blob does not match its declared size")
    digest = hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()
    if digest != object_sha:
        raise ValueError("runtime source blob bytes do not match their immutable Git identity")
    return payload


@lru_cache(maxsize=256)
@_runtime_source_operation("definitions")
def _runtime_definition_files(buildkite_tree_sha: str) -> dict[str, Any]:
    """Read shared CI blobs once, even when hundreds of builds share them."""
    actual_sha, rows = _runtime_tree(buildkite_tree_sha)
    if actual_sha != buildkite_tree_sha:
        raise ValueError("runtime .buildkite tree identity changed")
    config = next((row for row in rows if row["path"] == "ci_config.yaml"), None)
    areas = next((row for row in rows if row["path"] == "test_areas"), None)
    if not config or config.get("type") != "blob" or not areas or areas.get("type") != "tree":
        raise ValueError("runtime source lacks main CI configuration or test areas")
    actual_areas, entries = _runtime_tree(areas["sha"], True)
    if actual_areas != areas["sha"]:
        raise ValueError("runtime test-area tree identity changed")
    selected = [row for row in entries if row["path"].endswith(".yaml")]
    if len(selected) > RUNTIME_SOURCE_MAX_FILES:
        raise RuntimeSourceError(reason_class="payload-budget", phase="definitions")
    if any(row.get("type") != "blob" or row.get("mode") not in {"100644", "100755"} for row in selected):
        raise ValueError("runtime source file inventory has invalid modes")
    blobs = {CI_CONFIG: _runtime_blob(config["sha"])}
    for row in selected:
        blobs[f"{TEST_AREAS}{row['path']}"] = _runtime_blob(row["sha"])
    if sum(len(payload) for payload in blobs.values()) > RUNTIME_SOURCE_MAX_BYTES:
        raise RuntimeSourceError(reason_class="payload-budget", phase="definitions")
    return {path: yaml.safe_load(payload.decode("utf-8")) for path, payload in blobs.items()}


@lru_cache(maxsize=4096)
@_runtime_source_operation("commit", commit_argument=True)
def _runtime_commit_tree(commit_sha: str) -> str:
    """Resolve the exact commit before validating its tree object bytes.

    GitHub's trees endpoint accepts a commit alias and echoes that alias in
    ``sha``. Resolve the actual tree identity from immutable commit metadata.
    """
    if not FULL_SHA.fullmatch(commit_sha):
        raise ValueError("runtime source commit requires an exact full SHA")
    value = _runtime_source_json(f"commits/{commit_sha}")
    tree = value.get("tree")
    if value.get("sha") != commit_sha or not isinstance(tree, dict) or not FULL_SHA.fullmatch(str(tree.get("sha") or "")):
        raise ValueError("runtime source commit identity is malformed")
    return tree["sha"]


@lru_cache(maxsize=4096)
@_runtime_source_operation("scope", commit_argument=True)
def runtime_snapshot(commit_sha: str) -> MainCISnapshot:
    """Bind an exact commit to cached immutable CI trees, without repo archives."""
    if not FULL_SHA.fullmatch(commit_sha):
        raise ValueError("runtime execution scope requires an exact full commit SHA")
    tree_sha = _RUNTIME_PRIMED_BUILDKITE_TREES.get(commit_sha)
    if tree_sha is None:
        tree_sha = _runtime_rest_root(commit_sha)
    snapshot = MainCISnapshot(commit_sha, _runtime_definition_files(tree_sha), datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), tree_sha)
    validate_snapshot(snapshot)
    return snapshot


@_runtime_source_operation("scope", commit_argument=True)
def annotate_runtime_source_scope(
    build: dict[str, Any], *, snapshot: MainCISnapshot | None = None,
    scope_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach CPU exclusions only after an exact pinned step and route join.

    An MI agent can execute a ``no_gpu`` step. Its observed physical route is
    retained; this annotation describes the immutable execution definition.
    Unknown jobs remain unannotated rather than inheriting a label guess.
    """
    from vllm.ci.analyzer import _JOB_PREFIX_RE, _normalize_job_name
    from vllm.pipelines import _job_queue

    commit = str(build.get("commit") or "").strip().casefold()
    if not FULL_SHA.fullmatch(commit):
        raise ValueError("runtime execution scope requires an exact full commit SHA")
    if scope_index is not None:
        index = validate_runtime_scope_index(scope_index, expected_commit=commit)
    else:
        snapshot = snapshot or runtime_snapshot(commit)
        if snapshot.commit_sha != commit:
            raise ValueError("runtime execution scope definitions do not match the build commit")
        index = runtime_scope_index(snapshot)

    def pool(value: Any) -> str:
        return str(value or "").strip().casefold().removeprefix("amd_")

    keyed = {(route["key"], route["agent_pool"]) for route in index["cpu_routes"] if route["key"]}
    labeled = {(route["label"], route["agent_pool"]) for route in index["cpu_routes"] if route["label"]}
    jobs = []
    for raw_job in build.get("jobs", []) or []:
        if not isinstance(raw_job, dict):
            raise ValueError("runtime execution scope job roster must contain objects")
        job = dict(raw_job)
        # Re-attest fresh definitions; a restored annotation is not authority.
        job.pop("source_no_gpu", None)
        job.pop("source_scope_commit", None)
        name = str(job.get("raw_name") or job.get("name") or job.get("job_name") or "")
        raw_queue = _job_queue(job)
        if not raw_queue:
            prefix = _JOB_PREFIX_RE.match(name)
            raw_queue = prefix.group(1) if prefix else ""
        queue = pool(raw_queue)
        raw_step = job.get("step")
        step = raw_step if isinstance(raw_step, dict) else {}
        key = str(job.get("step_key") or step.get("key") or "").strip()
        cpu_only = (key, queue) in keyed if key else (_normalize_job_name(name), queue) in labeled
        if cpu_only:
            job["source_no_gpu"] = True
        job["source_scope_commit"] = commit
        jobs.append(job)
    return {**build, "jobs": jobs, "source_scope_commit": commit,
            "source_definition_tree_sha": index["definition_tree_sha"], "source_scope_index": index}


def runtime_scope_index(snapshot: MainCISnapshot) -> dict[str, Any]:
    """Compact the exact source CPU joins for authenticated private-cache reuse."""
    from vllm.ci.analyzer import _normalize_job_name

    routes = amd_source_steps(snapshot, include_cpu=True)
    keys: dict[tuple[str, str], list[dict]] = {}
    labels: dict[tuple[str, str], list[dict]] = {}
    for route in routes:
        queue = str(route.get("agent_pool") or "").strip().casefold().removeprefix("amd_")
        keys.setdefault((str(route.get("key") or ""), queue), []).append(route)
        labels.setdefault((_normalize_job_name(str(route.get("label") or "")), queue), []).append(route)
    exclusions = set()
    for route in routes:
        if not is_cpu_only_definition(route):
            continue
        queue = str(route.get("agent_pool") or "").strip().casefold().removeprefix("amd_")
        key, label = str(route.get("key") or ""), _normalize_job_name(str(route.get("label") or ""))
        key = key if all(is_cpu_only_definition(row) for row in keys[(key, queue)]) else ""
        label = label if all(is_cpu_only_definition(row) for row in labels[(label, queue)]) else ""
        if key or label:
            exclusions.add((key, label, queue))
    return {"version": 1, "commit_sha": snapshot.commit_sha,
            "definition_tree_sha": snapshot.definition_tree_sha,
            "cpu_routes": [{"key": key, "label": label, "agent_pool": queue} for key, label, queue in sorted(exclusions)]}


def validate_runtime_scope_index(index: Any, *, expected_commit: str) -> dict[str, Any]:
    """Validate an immutable index from an already authenticated private cache."""
    if not isinstance(index, dict) or set(index) != {"version", "commit_sha", "definition_tree_sha", "cpu_routes"} or type(index.get("version")) is not int or index["version"] != 1:
        raise ValueError("runtime scope index has an invalid shape")
    if not FULL_SHA.fullmatch(expected_commit) or index.get("commit_sha") != expected_commit or not FULL_SHA.fullmatch(str(index.get("definition_tree_sha") or "")):
        raise ValueError("runtime scope index does not match its exact immutable commit/tree")
    rows = index.get("cpu_routes")
    if not isinstance(rows, list) or len(rows) > 2048:
        raise ValueError("runtime scope index exceeds its route bound")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"key", "label", "agent_pool"} or any(not isinstance(row.get(key), str) for key in row):
            raise ValueError("runtime scope index route has an invalid shape")
        identity = (row["key"], row["label"], row["agent_pool"])
        if not (row["key"] or row["label"]) or any(len(value) > 4096 for value in identity) or not re.fullmatch(r"mi\d+(?:b)?(?:_[a-z0-9_-]+)?", row["agent_pool"]) or identity in seen:
            raise ValueError("runtime scope index route is empty, duplicated or invalid")
        seen.add(identity)
    if rows != [{"key": key, "label": label, "agent_pool": queue} for key, label, queue in sorted(seen)]:
        raise ValueError("runtime scope index routes must be canonical and sorted")
    return {**index, "cpu_routes": [dict(row) for row in rows]}


def logical_title(step: dict[str, Any]) -> str:
    return definition_title(str(step.get("label") or "")).strip()


def route_is_required(route: dict[str, Any]) -> bool:
    return not bool(route.get("optional") or route.get("soft_fail"))
