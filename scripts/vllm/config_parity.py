"""YAML config parity analysis for vLLM CI pipelines.

Compares test step definitions between:
- AMD: .buildkite/test-amd.yaml
- NVIDIA: .buildkite/test_areas/*.yaml

Fetches files directly from the upstream vLLM GitHub repo (main branch)
so no local clone is needed.

Uses command similarity (adapted from vllm_ci_parity.py) to measure how
closely AMD test commands match their NVIDIA counterparts.

This is a *static* analysis of the CI config files, complementing the
*runtime* parity analysis in analyzer.py which compares actual test results.
"""

import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
import yaml

from vllm.ci.analyzer import (
    _normalize_job_name,
    _parity_key_base,
    commands_similarity,
    similarity_color,
)

log = logging.getLogger(__name__)

# GitHub raw content base URL for upstream vLLM
VLLM_RAW_BASE = "https://raw.githubusercontent.com/vllm-project/vllm/main"
# GitHub API for listing directory contents
VLLM_API_BASE = "https://api.github.com/repos/vllm-project/vllm/contents"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ConfigStep:
    """A test step parsed from CI YAML config."""
    label: str
    normalized_label: str
    identity_key: str
    source_file: str
    group: str
    commands: list[str] = field(default_factory=list)
    timeout_in_minutes: Optional[int] = None
    num_gpus: Optional[int] = None
    parallelism: Optional[int] = None
    optional: bool = False
    soft_fail: bool = False
    grade: Optional[str] = None


@dataclass
class ConfigMatch:
    """A matched pair of AMD and NVIDIA config steps."""
    amd_step: ConfigStep
    nvidia_step: ConfigStep
    command_similarity: float
    color: str  # green/yellow/orange/red


# ---------------------------------------------------------------------------
# GitHub fetchers
# ---------------------------------------------------------------------------

def _fetch_yaml_from_github(path: str) -> Optional[dict]:
    """Fetch and parse a YAML file from the upstream vLLM repo."""
    url = f"{VLLM_RAW_BASE}/{path}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return yaml.safe_load(resp.text)
    except Exception as e:
        log.warning("Failed to fetch %s: %s", url, e)
        return None


def _list_test_area_files() -> list[str]:
    """List all .yaml files in .buildkite/test_areas/ from GitHub API."""
    url = f"{VLLM_API_BASE}/.buildkite/test_areas"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        entries = resp.json()
        return [
            e["path"] for e in entries
            if e.get("name", "").endswith(".yaml")
        ]
    except Exception as e:
        log.warning("Failed to list test_areas from GitHub: %s", e)
        return []


# ---------------------------------------------------------------------------
# YAML parsing (adapted from vllm_ci_parity.py)
# ---------------------------------------------------------------------------

def _flatten_commands(raw_cmds) -> list[str]:
    """Flatten potentially nested command structures into a simple list."""
    if not raw_cmds:
        return []
    flat = []
    for c in raw_cmds:
        if isinstance(c, list):
            flat.extend(_flatten_commands(c))
        elif isinstance(c, str):
            for line in c.strip().split('\n'):
                line = line.strip()
                if line and not line.startswith('#'):
                    flat.append(line)
    return flat


def _gpu_count(value) -> Optional[int]:
    """Return a positive GPU count from YAML metadata, if present."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _has_gpu_count_suffix(key: str) -> bool:
    return re.search(r'\(\s*\d+\s+gpus?\s*\)', key, re.IGNORECASE) is not None


_CONFIG_IDENTITY_ALIASES = {
    # Upstream splits the B200 small-model eval from the plain H100 job, while
    # AMD carries the same hardware-specific coverage as MI300/MI355 variants.
    # Keep that family distinct from the plain "LM Eval Small Models" row so
    # the AMD variants do not show up as AMD-only.
    "lm eval small models (b200)": "lm eval small models (hardware variants)",
    "lm eval small models (mi300)": "lm eval small models (hardware variants)",
    "lm eval small models (2xb200-2xmi300)": "lm eval small models (hardware variants)",
    "lm eval small models (2xb200-2xmi355)": "lm eval small models (hardware variants)",
}


def _config_identity_key(label: str, num_gpus) -> str:
    """Canonical YAML identity for matching AMD and upstream steps.

    Runtime labels are not enough: upstream YAML often stores the GPU count in
    ``num_devices`` while AMD stores the corresponding H100/MI combo in the
    label.  Preserve the GPU count when it is metadata-only so static config
    matching and runtime parity matching agree.
    """
    normalized = _normalize_job_name(label)
    if normalized in _CONFIG_IDENTITY_ALIASES:
        return _CONFIG_IDENTITY_ALIASES[normalized]
    key = _parity_key_base(label)
    n = _gpu_count(num_gpus)
    if n and not _has_gpu_count_suffix(key):
        key = f"{key} ({n} gpus)"
    return key


def _parse_step(item: dict, source_file: str, group: str) -> ConfigStep:
    """Parse a single step dictionary into a ConfigStep."""
    label = item.get('label', 'unknown')
    cmds = item.get('commands', [])
    if 'command' in item:
        cmds = [item['command']]
    cmds = _flatten_commands(cmds)

    num_gpus = item.get('num_devices') or item.get('num_gpus')

    return ConfigStep(
        label=label,
        normalized_label=_normalize_job_name(label),
        identity_key=_config_identity_key(label, num_gpus),
        source_file=source_file,
        group=group,
        commands=cmds,
        timeout_in_minutes=item.get('timeout_in_minutes'),
        num_gpus=num_gpus,
        parallelism=item.get('parallelism'),
        optional=item.get('optional', False) or False,
        soft_fail=item.get('soft_fail', False) or False,
        grade=item.get('grade'),
    )


def extract_shard_bases() -> list[str]:
    """Fetch test-amd.yaml and upstream test_areas YAMLs from GitHub,
    return lowercased label prefixes for steps that use %N parallelism.

    These are the ONLY groups whose trailing shard index should be stripped
    during normalization.
    """
    bases = set()

    # AMD pipeline
    amd_data = _fetch_yaml_from_github(".buildkite/test-amd.yaml")
    if amd_data:
        for step in amd_data.get("steps", []):
            label = step.get("label", "")
            par = step.get("parallelism")
            if par and par > 1 and "%N" in label:
                base = label.replace("%N", "").strip().lower()
                bases.add(base)

    # Upstream (NVIDIA) pipeline — test_areas/*.yaml
    area_files = _list_test_area_files()
    for fpath in area_files:
        data = _fetch_yaml_from_github(fpath)
        if not data:
            continue
        for item in data if isinstance(data, list) else data.get("steps", []):
            if not isinstance(item, dict):
                continue
            label = item.get("label", "")
            par = item.get("parallelism")
            if par and par > 1 and "%N" in label:
                base = label.replace("%N", "").strip().lower()
                bases.add(base)

    return sorted(bases)


def _parse_amd_data(data: dict) -> list[ConfigStep]:
    """Parse test-amd.yaml data into ConfigStep list."""
    if not data:
        return []
    steps = []
    for item in data.get('steps', []):
        agent_pool = item.get('agent_pool', '')
        if 'mi355' in agent_pool:
            group = 'mi355'
        elif 'mi325' in agent_pool:
            group = 'mi325'
        else:
            group = 'amd'
        steps.append(_parse_step(item, 'test-amd.yaml', group))
    return steps


def _parse_nvidia_data(
    yaml_files: list[tuple[str, dict]],
) -> tuple[list[ConfigStep], list[dict]]:
    """Parse test_areas YAML data. Returns (nvidia_steps, mirror_entries)."""
    nvidia_steps = []
    mirrors = []

    for filename, data in yaml_files:
        if not data:
            continue
        group_name = data.get('group', Path(filename).stem)

        for item in data.get('steps', []):
            step = _parse_step(item, filename, group_name)
            nvidia_steps.append(step)

            mirror = item.get('mirror')
            if mirror and isinstance(mirror, dict) and 'amd' in mirror:
                amd_cfg = mirror['amd']
                amd_cmds_raw = amd_cfg.get('commands')
                commands_overridden = amd_cmds_raw is not None

                if commands_overridden:
                    amd_cmds = _flatten_commands(amd_cmds_raw)
                else:
                    amd_cmds = list(step.commands)

                mirrors.append({
                    "nvidia_label": step.label,
                    "normalized": step.normalized_label,
                    "identity_key": step.identity_key,
                    "nvidia_commands": step.commands,
                    "amd_commands": amd_cmds,
                    "commands_overridden": commands_overridden,
                    "command_similarity": commands_similarity(step.commands, amd_cmds),
                    "source_file": filename,
                })

    return nvidia_steps, mirrors


def _load_config_steps() -> tuple[list[ConfigStep], list[ConfigStep], list[dict]] | tuple[None, None, None]:
    """Fetch upstream YAML and return parsed AMD/NVIDIA config steps."""
    log.info("Fetching test-amd.yaml from upstream...")
    amd_data = _fetch_yaml_from_github(".buildkite/test-amd.yaml")
    if not amd_data:
        return None, None, None

    log.info("Listing test_areas/ files from upstream...")
    area_files = _list_test_area_files()
    if not area_files:
        return None, None, None

    log.info("Fetching %d test_areas YAML files...", len(area_files))
    nvidia_yamls = []
    for fpath in area_files:
        data = _fetch_yaml_from_github(fpath)
        if data:
            nvidia_yamls.append((fpath, data))

    amd_steps = _parse_amd_data(amd_data)
    nvidia_steps, mirrors = _parse_nvidia_data(nvidia_yamls)
    return amd_steps, nvidia_steps, mirrors


def extract_parity_key_overrides() -> dict[str, str]:
    """Return normalized runtime-label -> YAML identity key overrides.

    Only identities present on both AMD and upstream are exported. This avoids
    remapping genuinely AMD-only or upstream-only labels while still fixing
    cases where one side encodes GPU count in YAML metadata and the other side
    encodes it in the label.
    """
    amd_steps, nvidia_steps, _ = _load_config_steps()
    if amd_steps is None or nvidia_steps is None:
        return {}

    sides_by_identity: dict[str, set[str]] = {}
    for step in amd_steps:
        sides_by_identity.setdefault(step.identity_key, set()).add("amd")
    for step in nvidia_steps:
        sides_by_identity.setdefault(step.identity_key, set()).add("upstream")

    shared = {
        key for key, sides in sides_by_identity.items()
        if "amd" in sides and "upstream" in sides
    }
    identities_by_label: dict[str, set[str]] = {}
    for step in [*amd_steps, *nvidia_steps]:
        if step.identity_key in shared:
            identities_by_label.setdefault(step.normalized_label, set()).add(step.identity_key)

    overrides: dict[str, str] = {}
    for step in [*amd_steps, *nvidia_steps]:
        if step.identity_key not in shared:
            continue
        if len(identities_by_label.get(step.normalized_label, set())) > 1:
            continue
        if _parity_key_base(step.normalized_label) == step.identity_key:
            continue
        overrides[step.normalized_label] = step.identity_key
    return dict(sorted(overrides.items()))


# ---------------------------------------------------------------------------
# Config parity report
# ---------------------------------------------------------------------------

def build_config_parity() -> dict:
    """Build a YAML config parity report by fetching from upstream GitHub.

    Fetches .buildkite/test-amd.yaml and .buildkite/test_areas/*.yaml
    from vllm-project/vllm main branch.

    Returns:
        Config parity report dict.
    """
    amd_steps, nvidia_steps, mirrors = _load_config_steps()
    if amd_steps is None:
        return {"error": "Failed to fetch test-amd.yaml from upstream"}
    if nvidia_steps is None:
        return {"error": "Failed to list test_areas/ from upstream"}

    # Deduplicate AMD steps (mi325 vs mi355 copies), using the YAML identity
    # key so labels with metadata-only GPU counts line up with upstream.
    seen = {}
    amd_deduped = []
    for step in amd_steps:
        if step.identity_key not in seen:
            seen[step.identity_key] = step
            amd_deduped.append(step)

    # Build NVIDIA lookup by YAML identity key.
    nvidia_by_identity = {}
    for step in nvidia_steps:
        if step.identity_key not in nvidia_by_identity:
            nvidia_by_identity[step.identity_key] = step

    # Match AMD to NVIDIA by YAML identity key.
    matches = []
    amd_only = []
    matched_nvidia = set()
    mirrored_nvidia = {m["identity_key"] for m in mirrors}

    for amd_step in amd_deduped:
        nv_step = nvidia_by_identity.get(amd_step.identity_key)
        if nv_step:
            sim = commands_similarity(amd_step.commands, nv_step.commands)
            matches.append(ConfigMatch(
                amd_step=amd_step,
                nvidia_step=nv_step,
                command_similarity=sim,
                color=similarity_color(sim),
            ))
            matched_nvidia.add(amd_step.identity_key)
        else:
            amd_only.append(amd_step)

    # NVIDIA-only: not matched and not mirrored
    nvidia_only = [
        s for s in nvidia_steps
        if s.identity_key not in matched_nvidia
        and s.identity_key not in mirrored_nvidia
    ]

    # Also filter amd_only: remove AMD tests covered by mirrors
    amd_only = [s for s in amd_only if s.identity_key not in mirrored_nvidia]

    # Sort matches by similarity (lowest first = most divergent)
    matches.sort(key=lambda m: m.command_similarity)

    # Compute summary metrics
    total_amd = len(amd_deduped)
    total_nvidia = len(set(s.identity_key for s in nvidia_steps))
    match_rate = len(matches) / (len(matches) + len(amd_only)) * 100 if (len(matches) + len(amd_only)) > 0 else 0
    avg_similarity = (
        sum(m.command_similarity for m in matches) / len(matches) * 100
        if matches else 0
    )

    return {
        "summary": {
            "total_amd_steps": total_amd,
            "total_nvidia_steps": total_nvidia,
            "matched": len(matches),
            "amd_only": len(amd_only),
            "nvidia_only": len(nvidia_only),
            "mirrors": len(mirrors),
            "match_rate_pct": round(match_rate, 1),
            "avg_command_similarity_pct": round(avg_similarity, 1),
        },
        "matches": [
            {
                "amd_label": m.amd_step.label,
                "nvidia_label": m.nvidia_step.label,
                "normalized": m.amd_step.normalized_label,
                "identity_key": m.amd_step.identity_key,
                "command_similarity": round(m.command_similarity, 4),
                "color": m.color,
                "amd_source": m.amd_step.source_file,
                "nvidia_source": m.nvidia_step.source_file,
                # Include commands for divergent matches so frontend can show diffs
                **({"amd_commands": m.amd_step.commands, "nvidia_commands": m.nvidia_step.commands}
                   if m.command_similarity < 1.0 else {}),
            }
            for m in matches
        ],
        "amd_only": [
            {"label": s.label, "normalized": s.normalized_label, "identity_key": s.identity_key, "group": s.group}
            for s in amd_only
        ],
        "nvidia_only": [
            {"label": s.label, "normalized": s.normalized_label, "identity_key": s.identity_key, "source": s.source_file}
            for s in nvidia_only
        ],
        "mirrors": [
            {
                "nvidia_label": m["nvidia_label"],
                "identity_key": m["identity_key"],
                "commands_overridden": m["commands_overridden"],
                "command_similarity": round(m["command_similarity"], 4),
                "color": similarity_color(m["command_similarity"]),
                "source_file": m["source_file"],
            }
            for m in mirrors
        ],
    }
