#!/usr/bin/env python3
"""Build an AMD test-group coverage matrix from upstream ``test-amd.yaml``.

The output powers the CI Analytics "AMD HW Matrix" view:

- one definition row per canonical test-group title and execution identity
- duplicate clusters for exact command lists whose titles share >= 2 characters
- one dynamic column per AMD architecture found in the YAML
- per-cell metadata about the exact YAML label(s) and the latest AMD nightly
  match, so the frontend can link each symbol to Buildkite

Usage:
    python scripts/vllm/collect_amd_test_matrix.py --output data/vllm/ci/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml


log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT = ROOT / "data" / "vllm" / "ci"
RAW_YAML_URL = (
    "https://raw.githubusercontent.com/vllm-project/vllm/"
    "refs/heads/main/.buildkite/test-amd.yaml"
)
RAW_YAML_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/vllm-project/vllm/"
    "{commit}/.buildkite/test-amd.yaml"
)
BUILDKITE_BUILD_URL = (
    "https://api.buildkite.com/v2/organizations/vllm/"
    "pipelines/amd-ci/builds/{build_number}"
)

AREA_PATTERNS = [
    ("Kernels", re.compile(r"^kernels?|attention test|quantization test", re.I)),
    ("Attention", re.compile(r"attention", re.I)),
    ("Distributed", re.compile(r"distributed|torchrun|pipeline parallel|elastic ep|eplb", re.I)),
    ("Models", re.compile(r"models? test|weight loading", re.I)),
    ("Multi-Modal", re.compile(r"multi-modal|whisper|vision|audio", re.I)),
    ("Entrypoints", re.compile(r"entrypoint|api server|openai", re.I)),
    ("Compile", re.compile(r"compile|compilation|pytorch fullgraph|fullgraph", re.I)),
    ("Engine", re.compile(r"engine|async engine|inputs, utils, worker|shutdown", re.I)),
    ("LoRA", re.compile(r"lora", re.I)),
    ("Spec Decode", re.compile(r"spec.?decode|eagle|ngram|speculator|mtp", re.I)),
    ("Evaluations", re.compile(r"lm eval|gsm8k|gpqa|accuracy eval|ppl", re.I)),
    ("Quantization", re.compile(r"quantiz|fp8|mxfp4", re.I)),
    ("Examples", re.compile(r"examples?", re.I)),
    ("Benchmarks", re.compile(r"benchmark", re.I)),
    ("V1", re.compile(r"^v1\b", re.I)),
]

MULTISPACE_RE = re.compile(r"\s+")
HW_ARCH_RE = re.compile(r"mi\d{3}", re.I)
TRAILING_PARENS_RE = re.compile(r"\s*\(([^)]*)\)\s*$")
SIMPLE_HARDWARE_PAYLOAD_RE = re.compile(r"[a-z0-9-]+", re.I)
AMD_VARIANT_TOKEN_RE = re.compile(r"mi(?:250|300|325|355)\b", re.I)
CORE_AMD_ARCHITECTURES = frozenset({"mi250", "mi300", "mi325"})
INCIDENT_STATES = frozenset({"failed", "timed_out", "broken", "soft_fail"})
WAITING_STATES = frozenset({"running", "scheduled", "assigned"})


def _github_headers() -> dict[str, str]:
    headers = {}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def clean_label(label: str) -> str:
    text = (label or "").strip().strip('"').strip("'")
    return MULTISPACE_RE.sub(" ", text).strip()


def link_label(label: str) -> str:
    text = clean_label(label)
    text = re.sub(r"\s*%N\s*$", "", text)
    return MULTISPACE_RE.sub(" ", text).strip()


def canonical_title(label: str) -> str:
    text = link_label(label)
    match = TRAILING_PARENS_RE.search(text)
    if match:
        payload = match.group(1).strip()
        is_hardware = re.search(r"(mi\d+|h\d{3}|b\d{3}|gfx\d+|hw-tag)", payload, re.I)
        has_counts = re.search(r"\b\d+x", payload, re.I)
        if is_hardware and not has_counts and SIMPLE_HARDWARE_PAYLOAD_RE.fullmatch(payload):
            text = text[:match.start()].strip()
        elif is_hardware and has_counts:
            normalized_payload = AMD_VARIANT_TOKEN_RE.sub("MI", payload)
            text = f"{text[:match.start()].strip()} ({normalized_payload})"
    return MULTISPACE_RE.sub(" ", text).strip()


def _normalize_fingerprint_value(value: Any) -> str:
    if isinstance(value, str):
        return clean_label(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return clean_label(str(value))


def definition_fingerprint(step: dict[str, Any]) -> str:
    """Capture the execution identity of a YAML step.

    Some labels differ only by a hardware-looking suffix but actually run
    different commands or from a different working directory. Those should not
    collapse into one matrix row.
    """
    payload = {
        "working_dir": _normalize_fingerprint_value(step.get("working_dir", "")),
        "commands": [
            _normalize_fingerprint_value(cmd)
            for cmd in (step.get("commands") or [])
            if _normalize_fingerprint_value(cmd)
        ],
        "source_file_dependencies": [
            _normalize_fingerprint_value(dep)
            for dep in (step.get("source_file_dependencies") or [])
            if _normalize_fingerprint_value(dep)
        ],
    }
    return json.dumps(payload, sort_keys=True)


def command_fingerprint(step: dict[str, Any]) -> str:
    """Return the exact normalized command list used by the duplicate rule."""
    commands = [
        _normalize_fingerprint_value(command)
        for command in (step.get("commands") or [])
        if _normalize_fingerprint_value(command)
    ]
    return json.dumps(commands, ensure_ascii=True)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]
    return f"{prefix}-{digest}"


def longest_shared_title_substring(left: str, right: str) -> str:
    """Return the longest case-insensitive shared title substring."""
    a = clean_label(left).casefold()
    b = clean_label(right).casefold()
    if not a or not b:
        return ""

    previous = [0] * (len(b) + 1)
    best_length = 0
    best_end = 0
    for i, left_char in enumerate(a, start=1):
        current = [0] * (len(b) + 1)
        for j, right_char in enumerate(b, start=1):
            if left_char != right_char:
                continue
            current[j] = previous[j - 1] + 1
            if current[j] > best_length:
                best_length = current[j]
                best_end = i
        previous = current
    return a[best_end - best_length:best_end]


def annotate_duplicate_groups(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Annotate command-equal rows connected by a shared title substring."""
    if not rows:
        return []

    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    by_commands: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        command_key = row.get("_command_key", "")
        if command_key and command_key != "[]":
            by_commands[command_key].append(index)

    pair_matches: list[dict[str, Any]] = []
    for indexes in by_commands.values():
        for offset, left_index in enumerate(indexes):
            for right_index in indexes[offset + 1:]:
                shared = longest_shared_title_substring(
                    rows[left_index]["title"], rows[right_index]["title"]
                )
                if len(shared) < 2:
                    continue
                union(left_index, right_index)
                pair_matches.append(
                    {
                        "left_id": rows[left_index]["id"],
                        "right_id": rows[right_index]["id"],
                        "shared_substring": shared,
                    }
                )

    components: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows):
        components[find(index)].append(row)

    duplicate_groups: list[dict[str, Any]] = []
    for members in components.values():
        members.sort(key=lambda row: (row["yaml_order"], row["title"].casefold()))
        command_key = members[0].get("_command_key", "")
        member_ids = [row["id"] for row in members]
        group_id = _stable_id(
            "duplicate-group",
            command_key if len(members) > 1 else members[0]["id"],
            *sorted(member_ids),
        )
        for row in members:
            row["duplicate_group_id"] = group_id
            row["duplicate_group_size"] = len(members)

        if len(members) < 2:
            continue
        id_set = set(member_ids)
        matches = [
            match
            for match in pair_matches
            if match["left_id"] in id_set and match["right_id"] in id_set
        ]
        duplicate_groups.append(
            {
                "id": group_id,
                "title": members[0]["title"],
                "command_fingerprint": members[0]["command_fingerprint"],
                "member_ids": member_ids,
                "member_titles": [row["title"] for row in members],
                "architectures": sorted(
                    {
                        arch
                        for row in members
                        for arch in row.get("architectures_present", [])
                    },
                    key=_arch_sort_key,
                ),
                "pair_matches": matches,
            }
        )

    for row in rows:
        row.pop("_command_key", None)
    duplicate_groups.sort(key=lambda group: group["title"].casefold())
    return duplicate_groups


def _matrix_cells(
    rows: list[dict[str, Any]],
    architectures: set[str],
) -> list[dict[str, Any]]:
    return [
        cell
        for row in rows
        for arch, cell in (row.get("cells") or {}).items()
        if arch in architectures and cell.get("exists")
    ]


def _health_status(cells: list[dict[str, Any]]) -> str:
    states = [str(cell.get("latest_state") or "").casefold() for cell in cells]
    has_pass = "passed" in states
    has_incident = any(state in INCIDENT_STATES for state in states)
    if has_pass and has_incident:
        return "mixed"
    if has_pass:
        return "passing"
    if has_incident:
        return "failed"
    if any(state in WAITING_STATES for state in states):
        return "waiting"
    return "unknown"


def matrix_health_policy(
    rows: list[dict[str, Any]],
    *,
    reduce_duplicates: bool,
    ignore_mi355_only: bool,
) -> dict[str, Any]:
    """Summarize unique AMD health under one explicit reduction policy."""
    components: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        components[row["duplicate_group_id"]].append(row)

    if reduce_duplicates:
        candidates = [
            (group_id, members, members)
            for group_id, members in components.items()
        ]
    else:
        candidates = [
            (row["id"], [row], components[row["duplicate_group_id"]])
            for row in rows
        ]

    counts = {
        "passing_groups": 0,
        "failed_only_groups": 0,
        "mixed_groups": 0,
        "waiting_groups": 0,
        "unknown_groups": 0,
        "ignored_mi355_only_groups": 0,
        "inherited_mi355_groups": 0,
    }
    for _, candidate_rows, component_rows in candidates:
        candidate_core = _matrix_cells(candidate_rows, set(CORE_AMD_ARCHITECTURES))
        candidate_mi355 = _matrix_cells(candidate_rows, {"mi355"})
        component_core = _matrix_cells(component_rows, set(CORE_AMD_ARCHITECTURES))
        if candidate_core:
            cells = candidate_core
            if candidate_mi355:
                counts["inherited_mi355_groups"] += 1
        elif component_core:
            cells = component_core
            counts["inherited_mi355_groups"] += 1
        elif ignore_mi355_only:
            counts["ignored_mi355_only_groups"] += 1
            continue
        else:
            cells = _matrix_cells(candidate_rows, {"mi355"})

        status = _health_status(cells)
        count_key = "failed_only_groups" if status == "failed" else status + "_groups"
        counts[count_key] += 1

    counts["failing_groups"] = (
        counts["failed_only_groups"] + counts["mixed_groups"]
    )
    counts["resolved_groups"] = (
        counts["passing_groups"] + counts["failing_groups"]
    )
    counts["included_groups"] = (
        counts["resolved_groups"]
        + counts["waiting_groups"]
        + counts["unknown_groups"]
    )
    counts["pass_percentage"] = round(
        counts["passing_groups"] / counts["resolved_groups"] * 100,
        1,
    ) if counts["resolved_groups"] else None
    counts["reduce_duplicates"] = reduce_duplicates
    counts["ignore_mi355_only"] = ignore_mi355_only
    return counts


def matrix_health_policies(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        "reduced_ignore_mi355": matrix_health_policy(
            rows, reduce_duplicates=True, ignore_mi355_only=True
        ),
        "reduced_include_mi355": matrix_health_policy(
            rows, reduce_duplicates=True, ignore_mi355_only=False
        ),
        "definitions_ignore_mi355": matrix_health_policy(
            rows, reduce_duplicates=False, ignore_mi355_only=True
        ),
        "definitions_include_mi355": matrix_health_policy(
            rows, reduce_duplicates=False, ignore_mi355_only=False
        ),
    }


def _variant_preference(label: str, arch: str, row_title: str) -> tuple[int, str]:
    normalized = clean_label(label)
    lowered = normalized.lower()
    if normalized == row_title:
        return (0, lowered)
    if arch.upper() in normalized.upper():
        return (1, lowered)
    return (2, lowered)


def _agent_pool_from_job_name(job_name: str) -> str:
    text = clean_label(job_name)
    if ":" not in text:
        return ""
    return text.split(":", 1)[0].strip().lower()


def _queue_matches_agent_pool(queue: str, agent_pool: str) -> bool:
    q = clean_label(queue).lower()
    pool = clean_label(agent_pool).lower()
    if not q or not pool:
        return True
    return q == pool or q.endswith("_" + pool)


def _amd_links(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        link
        for link in (row.get("job_links") or [])
        if isinstance(link, dict) and link.get("side") == "amd"
    ]


def _link_arch(link: dict[str, Any]) -> str | None:
    job_arch = arch_from_agent_pool(_agent_pool_from_job_name(link.get("job_name", "")))
    if job_arch:
        return job_arch
    return arch_from_queue(link.get("hw", ""))


def _parity_row_backfilled_for_arch(row: dict[str, Any], arch: str) -> bool:
    if row.get("backfilled"):
        return True
    return bool((row.get("hw_backfilled") or {}).get(arch))


def _parity_link_for_arch(
    row: dict[str, Any],
    arch: str,
    full_job_name: str,
) -> str | None:
    exact_name = clean_label(full_job_name)
    fallback: str | None = None
    for link in _amd_links(row):
        if _link_arch(link) != arch:
            continue
        url = clean_label(link.get("url", "")) or None
        if not url:
            continue
        if clean_label(link.get("job_name", "")) == exact_name:
            return url
        if fallback is None:
            fallback = url
    return fallback


def _parity_state_for_arch(
    row: dict[str, Any],
    arch: str,
    analytics_state: str | None,
) -> str | None:
    if _parity_row_backfilled_for_arch(row, arch):
        return analytics_state
    if analytics_state == "passed":
        return "passed"
    hw_failures = row.get("hw_failures") or {}
    hw_canceled = row.get("hw_canceled") or {}
    if hw_failures.get(arch, 0) > 0:
        return "soft_fail" if analytics_state == "soft_fail" else "failed"
    if hw_canceled.get(arch, 0) > 0:
        return "canceled"
    if analytics_state in {"soft_fail", "running", "scheduled", "assigned"}:
        return analytics_state
    amd = row.get("amd") or {}
    if amd.get("total", 0) > 0:
        return analytics_state or "passed"
    return analytics_state


def build_parity_amd_index(
    parity: dict[str, Any],
    shard_bases: list[str],
) -> tuple[
    dict[tuple[str, str], list[dict[str, Any]]],
    dict[tuple[str, str], list[dict[str, Any]]],
]:
    exact: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    normalized: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for row in (parity.get("job_groups") or []):
        if not isinstance(row, dict) or not row.get("amd"):
            continue

        added = False
        for link in _amd_links(row):
            arch = _link_arch(link)
            if not arch:
                continue
            full_name = clean_label(link.get("job_name", ""))
            if not full_name:
                continue
            exact[(arch, full_name)].append(row)
            normalized[(arch, strip_shard_index(full_name, shard_bases))].append(row)
            added = True

        if added:
            continue

        full_name = clean_label(row.get("amd_job_name", ""))
        arch = arch_from_agent_pool(_agent_pool_from_job_name(full_name))
        if not full_name or not arch:
            continue
        exact[(arch, full_name)].append(row)
        normalized[(arch, strip_shard_index(full_name, shard_bases))].append(row)

    return exact, normalized


def select_parity_row(
    parity_exact_index: dict[tuple[str, str], list[dict[str, Any]]],
    parity_norm_index: dict[tuple[str, str], list[dict[str, Any]]],
    arch: str,
    full_job_name: str,
    normalized_key: str,
) -> dict[str, Any] | None:
    exact_rows = parity_exact_index.get((arch, clean_label(full_job_name)), [])
    if exact_rows:
        return exact_rows[0]

    candidates = parity_norm_index.get((arch, normalized_key), [])
    if not candidates:
        return None

    full_name = clean_label(full_job_name)

    def _score(row: dict[str, Any]) -> tuple[int, str]:
        for link in _amd_links(row):
            if _link_arch(link) == arch and clean_label(link.get("job_name", "")) == full_name:
                return (0, clean_label(link.get("job_name", "")))
        amd_job_name = clean_label(row.get("amd_job_name", ""))
        if amd_job_name == full_name:
            return (1, amd_job_name)
        return (2, amd_job_name)

    return sorted(candidates, key=_score)[0]


def merge_cell_variant(
    existing: dict[str, Any],
    candidate: dict[str, Any],
    arch: str,
    row_title: str,
) -> None:
    aliases = existing.setdefault("aliases", [existing["label"]])
    if candidate["label"] not in aliases:
        aliases.append(candidate["label"])
    entries = existing.setdefault("entries", [])
    candidate_entry = {
        "label": candidate["label"],
        "agent_pool": candidate["agent_pool"],
        "optional": candidate["optional"],
        "parallelism": candidate["parallelism"],
        "latest_matched": candidate["latest_matched"],
        "latest_match_count": candidate["latest_match_count"],
        "latest_state": candidate["latest_state"],
        "latest_url": candidate.get("latest_url"),
        "aliases": [candidate["label"]],
        "raw_variant_count": 1,
    }
    if not entries:
        entries.append({
            "label": existing["label"],
            "agent_pool": existing["agent_pool"],
            "optional": existing["optional"],
            "parallelism": existing["parallelism"],
            "latest_matched": existing["latest_matched"],
            "latest_match_count": existing["latest_match_count"],
            "latest_state": existing["latest_state"],
            "latest_url": existing.get("latest_url"),
            "aliases": [existing["label"]],
            "raw_variant_count": 1,
        })
    entries.append(candidate_entry)
    existing["raw_variant_count"] = len(aliases)
    existing["optional"] = existing["optional"] or candidate["optional"]
    existing["parallelism"] = max(existing["parallelism"], candidate["parallelism"])
    existing["latest_matched"] = existing["latest_matched"] or candidate["latest_matched"]
    existing["latest_match_count"] += candidate["latest_match_count"]
    existing["latest_state"] = aggregate_state(
        [state for state in [existing.get("latest_state"), candidate.get("latest_state")] if state]
    )
    if _variant_preference(candidate["label"], arch, row_title) < _variant_preference(
        existing["label"], arch, row_title
    ):
        existing["label"] = candidate["label"]
        existing["agent_pool"] = candidate["agent_pool"]
        existing["latest_url"] = candidate.get("latest_url")


def classify_area(title: str) -> str:
    for area, pattern in AREA_PATTERNS:
        if pattern.search(title):
            return area
    return "Other"


def arch_from_agent_pool(agent_pool: str) -> str | None:
    pool = (agent_pool or "").strip().lower()
    if not pool:
        return None
    match = HW_ARCH_RE.search(pool)
    if not match:
        return None
    return match.group(0).lower()


def arch_from_queue(queue: str) -> str | None:
    return arch_from_agent_pool(queue)


def _arch_sort_key(arch: str) -> int:
    match = re.search(r"\d+", arch)
    return int(match.group(0)) if match else 0


def _normalize_job_name(name: str) -> str:
    name = re.sub(r"^(mi\d+_\d+|gpu_\d+|amd_\w+):\s*", "", name or "", flags=re.I)
    return MULTISPACE_RE.sub(" ", name).strip().lower()


def strip_shard_index(name: str, shard_bases: list[str]) -> str:
    lower = _normalize_job_name(name)
    for base in shard_bases:
        if lower.startswith(base) and lower != base:
            rest = lower[len(base):]
            if re.fullmatch(r"\s+\d+\s*", rest):
                return base
    return lower


def aggregate_state(states: list[str]) -> str | None:
    ordered = [s for s in states if s]
    if not ordered:
        return None
    priority = {
        "failed": 6,
        "timed_out": 6,
        "broken": 6,
        "soft_fail": 5,
        "running": 4,
        "scheduled": 3,
        "assigned": 3,
        "passed": 2,
        "canceled": 1,
        "skipped": 1,
    }
    return max(ordered, key=lambda s: priority.get(s, 0))


def fetch_yaml_text(url: str) -> str:
    resp = requests.get(url, headers=_github_headers(), timeout=30)
    resp.raise_for_status()
    return resp.text


def yaml_url_for_build(
    latest_build: dict[str, Any] | None,
    explicit_url: str | None = None,
) -> str:
    """Resolve the AMD YAML that governed the observed nightly."""
    if explicit_url:
        return explicit_url
    commit = clean_label((latest_build or {}).get("commit", ""))
    if re.fullmatch(r"[0-9a-f]{40}", commit, re.I):
        return RAW_YAML_URL_TEMPLATE.format(commit=commit)
    return RAW_YAML_URL


def parse_steps(yaml_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    parsed = yaml.safe_load(yaml_text) or {}
    raw_steps = parsed.get("steps", []) if isinstance(parsed, dict) else []
    steps: list[dict[str, Any]] = []
    arches: set[str] = set()

    for idx, step in enumerate(raw_steps):
        if not isinstance(step, dict):
            continue
        label = clean_label(step.get("label", ""))
        arch = arch_from_agent_pool(step.get("agent_pool", ""))
        if not label or not arch:
            continue
        arches.add(arch)
        steps.append(
            {
                "label": label,
                "link_label": link_label(label),
                "title": canonical_title(label),
                "definition_key": definition_fingerprint(step),
                "command_key": command_fingerprint(step),
                "area": classify_area(canonical_title(label)),
                "arch": arch,
                "yaml_order": idx,
                "optional": bool(step.get("optional")),
                "parallelism": int(step.get("parallelism") or 1),
                "agent_pool": step.get("agent_pool", ""),
            }
        )

    arch_list = sorted(arches, key=_arch_sort_key)
    return steps, arch_list


def build_latest_job_index(
    analytics: dict[str, Any],
    shard_bases: list[str],
) -> tuple[dict[str, dict[str, list[dict[str, Any]]]], dict[str, Any] | None]:
    amd = analytics.get("amd-ci", {}) if isinstance(analytics, dict) else {}
    latest_builds = amd.get("builds", []) if isinstance(amd, dict) else []
    latest_build = latest_builds[0] if latest_builds else None
    index: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))

    if not isinstance(latest_build, dict):
        return index, None

    for job in latest_build.get("jobs", []) or []:
        arch = arch_from_queue(job.get("q", ""))
        if not arch:
            continue
        key = strip_shard_index(job.get("name", ""), shard_bases)
        index[arch][key].append(job)
    return index, latest_build


def _queue_from_rules(rules: Any) -> str:
    for rule in rules or []:
        text = str(rule or "")
        if text.startswith("queue="):
            return text.split("=", 1)[1].strip()
    return ""


def build_buildkite_job_index(
    build: dict[str, Any],
    shard_bases: list[str],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Index the authoritative, non-superseded script roster for one build."""
    index: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    build_number = build.get("number")
    for job in build.get("jobs", []) or []:
        if job.get("type") != "script" or job.get("retried_in_job_id"):
            continue
        full_name = clean_label(job.get("name", ""))
        agent_pool = _agent_pool_from_job_name(full_name)
        queue = _queue_from_rules(job.get("agent_query_rules"))
        arch = arch_from_queue(queue) or arch_from_agent_pool(agent_pool)
        if not full_name or not arch:
            continue

        state = (
            "soft_fail"
            if job.get("soft_failed")
            else clean_label(job.get("state", "")).casefold()
        )
        job_id = clean_label(job.get("id", ""))
        step_id = clean_label((job.get("step") or {}).get("id", ""))
        base_url = f"https://buildkite.com/vllm/amd-ci/builds/{build_number}"
        if job_id:
            job_url = f"{base_url}/steps/canvas?jid={job_id}&tab=output"
        elif step_id:
            job_url = f"{base_url}/steps/canvas?sid={step_id}&tab=output"
        else:
            job_url = clean_label(job.get("web_url", "")) or base_url

        key = strip_shard_index(full_name, shard_bases)
        index[arch][key].append({
            "name": full_name,
            "raw_name": full_name,
            "state": state,
            "q": queue or (f"amd_{agent_pool}" if agent_pool else ""),
            "url": job_url,
            "job_id": job_id,
            "step_id": step_id,
            "matrix_source": "buildkite_build_detail",
        })
    return index


def fetch_buildkite_job_index(
    build_number: int | str | None,
    token: str,
    shard_bases: list[str],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    if build_number in (None, "") or not token:
        return {}
    try:
        response = requests.get(
            BUILDKITE_BUILD_URL.format(build_number=build_number),
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        response.raise_for_status()
        build = response.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Could not fetch AMD build #%s detail: %s", build_number, exc)
        return {}
    if str(build.get("number") or "") != str(build_number):
        log.warning("Ignoring mismatched AMD build detail for #%s", build_number)
        return {}
    return build_buildkite_job_index(build, shard_bases)


def build_hotness_job_index(
    hotness: dict[str, Any],
    latest_build_number: int | str | None,
    shard_bases: list[str],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Index exact Buildkite jobs omitted by parsed test-result analytics.

    Hotness observes script jobs directly from Buildkite, including utility
    steps that emit no pytest rows. Only evidence from the exact matrix build
    is eligible so a newer PR build or an older nightly cannot leak into the
    latest-nightly signal.
    """
    index: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    if latest_build_number in (None, ""):
        return index

    expected_build = str(latest_build_number)
    for row in hotness.get("test_groups", []) or []:
        if not isinstance(row, dict):
            continue
        evidence = row.get("latest_evidence") or {}
        if not isinstance(evidence, dict):
            continue
        if evidence.get("pipeline") != "amd-ci":
            continue
        if str(evidence.get("build_number") or "") != expected_build:
            continue

        full_name = clean_label(evidence.get("job_name", ""))
        agent_pool = _agent_pool_from_job_name(full_name)
        arch = (
            arch_from_agent_pool(agent_pool)
            or arch_from_queue(row.get("hw", ""))
        )
        if not full_name or not arch:
            continue

        state = clean_label(evidence.get("state", "")).casefold()
        if state == "soft_failed":
            state = "soft_fail"
        queue = (
            f"amd_{agent_pool}"
            if agent_pool
            else clean_label(row.get("hw", ""))
        )
        job_id = clean_label(evidence.get("job_id", ""))
        job_url = clean_label(evidence.get("job_url", ""))
        if job_id:
            job_url = (
                f"https://buildkite.com/vllm/amd-ci/builds/{expected_build}"
                f"/steps/canvas?jid={job_id}&tab=output"
            )
        job = {
            "name": full_name,
            "raw_name": full_name,
            "state": state,
            "q": queue,
            "url": job_url,
            "job_id": job_id,
            "matrix_source": "hotness_latest_build",
        }
        key = strip_shard_index(full_name, shard_bases)
        index[arch][key].append(job)
    return index


def merge_latest_job_indexes(
    primary: dict[str, dict[str, list[dict[str, Any]]]],
    fallback: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Fill missing matrix keys without overriding parsed analytics rows."""
    for arch, groups in fallback.items():
        primary_groups = primary.setdefault(arch, defaultdict(list))
        for key, jobs in groups.items():
            if not primary_groups.get(key):
                primary_groups[key].extend(jobs)
    return primary


def latest_build_metadata(
    analytics_build: dict[str, Any] | None,
    ci_health: dict[str, Any],
    parity: dict[str, Any],
) -> dict[str, Any] | None:
    if isinstance(analytics_build, dict) and analytics_build.get("number"):
        return analytics_build

    amd_latest = ((ci_health.get("amd") or {}).get("latest_build") or {})
    number = amd_latest.get("build_number") or amd_latest.get("number") or parity.get("amd_build")
    if not number:
        return None

    created_at = clean_label(amd_latest.get("created_at", ""))
    date = amd_latest.get("date") or (created_at[:10] if created_at else None) or parity.get("amd_date")
    build_url = (
        amd_latest.get("build_url")
        or amd_latest.get("web_url")
        or f"https://buildkite.com/vllm/amd-ci/builds/{number}"
    )
    return {
        "number": number,
        "date": date,
        "web_url": build_url,
        "message": amd_latest.get("message") or "AMD Full CI Run - nightly",
    }


def build_matrix(
    steps: list[dict[str, Any]],
    architectures: list[str],
    latest_job_index: dict[str, dict[str, list[dict[str, Any]]]],
    latest_build: dict[str, Any] | None,
    parity_exact_index: dict[tuple[str, str], list[dict[str, Any]]],
    parity_norm_index: dict[tuple[str, str], list[dict[str, Any]]],
    shard_bases: list[str],
    yaml_url: str,
) -> dict[str, Any]:
    rows_by_title: dict[str, dict[str, Any]] = {}
    title_fingerprints: dict[str, set[str]] = defaultdict(set)

    for step in steps:
        title_fingerprints[step["title"]].add(step["definition_key"])

    ambiguous_titles = {
        title for title, fingerprints in title_fingerprints.items() if len(fingerprints) > 1
    }

    for step in steps:
        row_title = step["label"] if step["title"] in ambiguous_titles else step["title"]
        row_key = (
            f"{step['title']}::{step['definition_key']}"
            if step["title"] in ambiguous_titles
            else step["title"]
        )
        row = rows_by_title.setdefault(
            row_key,
            {
                "id": _stable_id("matrix-row", row_key),
                "title": row_title,
                "canonical_title": step["title"],
                "definition_fingerprint": _stable_id(
                    "definition", step["definition_key"]
                ),
                "command_fingerprint": _stable_id(
                    "commands", step["command_key"]
                ),
                "_command_key": step["command_key"],
                "area": step["area"],
                "yaml_order": step["yaml_order"],
                "cells": {arch: {"exists": False} for arch in architectures},
            },
        )
        row["yaml_order"] = min(row["yaml_order"], step["yaml_order"])
        cell = row["cells"][step["arch"]]
        if not cell.get("exists"):
            cell.update(
                {
                    "exists": True,
                    "optional": False,
                    "variant_count": 0,
                    "variants": [],
                    "latest_matched": False,
                    "latest_state": None,
                    "latest_build_number": latest_build.get("number") if latest_build else None,
                }
            )
        cell["optional"] = cell["optional"] or step["optional"]
        variant_key = strip_shard_index(step["link_label"], shard_bases)
        matches = latest_job_index.get(step["arch"], {}).get(variant_key, [])
        filtered_matches = [
            match for match in matches
            if _queue_matches_agent_pool(match.get("q", ""), step["agent_pool"])
        ]
        if filtered_matches:
            matches = filtered_matches
        analytics_state = aggregate_state([
            state for m in matches if isinstance((state := m.get("state")), str)
        ])
        full_job_name = f"{step['agent_pool']}: {step['link_label']}"
        parity_row = select_parity_row(
            parity_exact_index,
            parity_norm_index,
            step["arch"],
            full_job_name,
            variant_key,
        )
        parity_matched = bool(parity_row) and not _parity_row_backfilled_for_arch(parity_row, step["arch"])
        latest_matched = bool(matches) or parity_matched
        latest_state = (
            _parity_state_for_arch(parity_row, step["arch"], analytics_state)
            if parity_row
            else analytics_state
        )
        matched_url = next(
            (
                clean_label(match.get("url", "") or match.get("job_url", ""))
                for match in matches
                if match.get("url") or match.get("job_url")
            ),
            None,
        )
        latest_url = (
            _parity_link_for_arch(parity_row, step["arch"], full_job_name)
            if parity_row
            else None
        ) or matched_url or (
            latest_build.get("web_url") if latest_matched and latest_build else None
        )
        variant = {
            "label": step["link_label"],
            "agent_pool": step["agent_pool"],
            "optional": step["optional"],
            "parallelism": step["parallelism"],
            "latest_matched": latest_matched,
            "latest_match_count": len(matches),
            "latest_state": latest_state,
            "latest_url": latest_url,
            "aliases": [step["link_label"]],
            "raw_variant_count": 1,
            "entries": [],
        }
        if cell["variants"]:
            merge_cell_variant(cell["variants"][0], variant, step["arch"], row["title"])
        else:
            variant["entries"] = [{
                "label": variant["label"],
                "agent_pool": variant["agent_pool"],
                "optional": variant["optional"],
                "parallelism": variant["parallelism"],
                "latest_matched": variant["latest_matched"],
                "latest_match_count": variant["latest_match_count"],
                "latest_state": variant["latest_state"],
                "latest_url": variant.get("latest_url"),
                "aliases": [variant["label"]],
                "raw_variant_count": 1,
            }]
            cell["variants"].append(variant)
        cell["variant_count"] = len(cell["variants"])

    rows = []
    for row in rows_by_title.values():
        coverage_count = 0
        nightly_coverage_count = 0
        architectures_present = []
        for arch in architectures:
            cell = row["cells"][arch]
            if not cell.get("exists"):
                continue
            coverage_count += 1
            architectures_present.append(arch)
            cell["variants"].sort(key=lambda v: (v["label"].lower(), v["agent_pool"]))
            cell["primary_label"] = cell["variants"][0]["label"]
            cell["raw_variant_count"] = sum(v.get("raw_variant_count", 1) for v in cell["variants"])
            cell["latest_matched"] = any(v["latest_matched"] for v in cell["variants"])
            cell["latest_state"] = aggregate_state(
                [v["latest_state"] for v in cell["variants"] if v["latest_state"]]
            )
            cell["latest_url"] = next(
                (v.get("latest_url") for v in cell["variants"] if v.get("latest_url")),
                None,
            )
            if cell["latest_matched"]:
                nightly_coverage_count += 1
        row["coverage_count"] = coverage_count
        row["nightly_coverage_count"] = nightly_coverage_count
        row["architectures_present"] = architectures_present
        row["signature"] = " + ".join(arch.upper() for arch in architectures_present)
        rows.append(row)

    rows.sort(key=lambda row: (row["yaml_order"], row["title"].lower()))
    duplicate_groups = annotate_duplicate_groups(rows)
    health_policies = matrix_health_policies(rows)

    fully_shared = sum(1 for row in rows if row["coverage_count"] == len(architectures))
    single_arch = sum(1 for row in rows if row["coverage_count"] == 1)
    multi_variant_cells = sum(
        1
        for row in rows
        for arch in architectures
        if row["cells"][arch].get("raw_variant_count", row["cells"][arch].get("variant_count", 0)) > 1
    )
    hardware_cells = sum(row["coverage_count"] for row in rows)
    latest_matched_cells = sum(row["nightly_coverage_count"] for row in rows)
    failure_states = {"failed", "timed_out", "broken", "soft_fail"}
    waiting_states = {"running", "scheduled", "assigned"}
    passing_cells = 0
    failing_cells = 0
    waiting_cells = 0
    unknown_cells = 0
    for row in rows:
        for arch in architectures:
            cell = row["cells"][arch]
            if not cell.get("exists"):
                continue
            state = cell.get("latest_state")
            if state == "passed":
                passing_cells += 1
            elif state in failure_states:
                failing_cells += 1
            elif state in waiting_states:
                waiting_cells += 1
            else:
                unknown_cells += 1

    arch_stats = []
    for arch in architectures:
        total = sum(1 for row in rows if row["cells"][arch].get("exists"))
        nightly = sum(1 for row in rows if row["cells"][arch].get("latest_matched"))
        arch_stats.append(
            {
                "id": arch,
                "label": arch.upper(),
                "group_count": total,
                "nightly_match_count": nightly,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "yaml_url": yaml_url,
            "latest_build_number": latest_build.get("number") if latest_build else None,
            "latest_build_date": latest_build.get("date") if latest_build else None,
            "latest_build_url": latest_build.get("web_url") if latest_build else None,
            "latest_build_message": latest_build.get("message") if latest_build else None,
        },
        "summary": {
            "unique_groups": len(rows),
            "latest_build_number": latest_build.get("number") if latest_build else None,
            "definition_rows": len(rows),
            "reduced_unique_groups": len(rows)
            - sum(len(group["member_ids"]) - 1 for group in duplicate_groups),
            "duplicate_clusters": len(duplicate_groups),
            "duplicate_definition_rows": sum(
                len(group["member_ids"]) for group in duplicate_groups
            ),
            "health_policies": health_policies,
            "architecture_count": len(architectures),
            "hardware_cells": hardware_cells,
            "latest_matched_cells": latest_matched_cells,
            "passing_cells": passing_cells,
            "failing_cells": failing_cells,
            "waiting_cells": waiting_cells,
            "unknown_cells": unknown_cells,
            "fully_shared_groups": fully_shared,
            "single_arch_groups": single_arch,
            "multi_variant_cells": multi_variant_cells,
        },
        "architectures": arch_stats,
        "areas": sorted({row["area"] for row in rows}),
        "duplicate_policy": {
            "command_rule": "normalized command lists are exactly equal",
            "title_rule": "longest normalized shared substring has length >= 2",
            "cluster_rule": "transitive closure of matching row pairs",
            "empty_commands_match": False,
            "default_reduce_duplicates": True,
            "default_ignore_mi355_only": True,
            "mi355_with_core_status": "inherit MI250, MI300, or MI325 signal",
        },
        "duplicate_groups": duplicate_groups,
        "rows": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect AMD test-group coverage matrix")
    parser.add_argument("--output", type=str, default=str(OUTPUT))
    parser.add_argument(
        "--yaml-url",
        type=str,
        default=None,
        help="Explicit YAML URL override (default: the latest AMD build commit)",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    analytics = _load_json(output / "analytics.json", {})
    hotness = _load_json(output / "hotness.json", {})
    ci_health = _load_json(output / "ci_health.json", {})
    parity = _load_json(output / "parity_report.json", {})
    shard_bases = _load_json(output / "shard_bases.json", [])

    latest_job_index, analytics_latest_build = build_latest_job_index(analytics, shard_bases)
    latest_build = latest_build_metadata(analytics_latest_build, ci_health, parity)
    yaml_url = yaml_url_for_build(latest_build, args.yaml_url)
    log.info("Fetching build-pinned AMD YAML from %s", yaml_url)
    yaml_text = fetch_yaml_text(yaml_url)
    steps, architectures = parse_steps(yaml_text)
    buildkite_job_index = fetch_buildkite_job_index(
        latest_build.get("number") if latest_build else None,
        os.getenv("BUILDKITE_TOKEN", "").strip(),
        shard_bases,
    )
    if buildkite_job_index:
        latest_job_index = merge_latest_job_indexes(
            buildkite_job_index,
            latest_job_index,
        )
    hotness_job_index = build_hotness_job_index(
        hotness,
        latest_build.get("number") if latest_build else None,
        shard_bases,
    )
    latest_job_index = merge_latest_job_indexes(latest_job_index, hotness_job_index)
    parity_exact_index, parity_norm_index = build_parity_amd_index(parity, shard_bases)

    matrix = build_matrix(
        steps=steps,
        architectures=architectures,
        latest_job_index=latest_job_index,
        latest_build=latest_build,
        parity_exact_index=parity_exact_index,
        parity_norm_index=parity_norm_index,
        shard_bases=shard_bases,
        yaml_url=yaml_url,
    )

    out_path = output / "amd_test_matrix.json"
    out_path.write_text(json.dumps(matrix, indent=2))
    log.info(
        "Wrote %s with %d groups across %d architectures",
        out_path,
        matrix["summary"]["unique_groups"],
        matrix["summary"]["architecture_count"],
    )


if __name__ == "__main__":
    main()
