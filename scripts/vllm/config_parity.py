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

import io
import logging
import os
import re
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
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

VLLM_REPOSITORY = "vllm-project/vllm"
VLLM_BRANCH = "main"
VLLM_API_BASE = f"https://api.github.com/repos/{VLLM_REPOSITORY}"
COMMAND_TWIN_TITLE_THRESHOLD = 0.65


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
    match_method: str = "identity"
    title_similarity: float = 1.0


@dataclass
class ConfigSourceSnapshot:
    """One commit-pinned view of the vLLM CI definitions."""
    commit_sha: str
    files: dict[str, object]
    fetched_at: str


_SOURCE_SNAPSHOT: Optional[ConfigSourceSnapshot] = None


# ---------------------------------------------------------------------------
# GitHub fetchers
# ---------------------------------------------------------------------------

def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _load_source_snapshot() -> Optional[ConfigSourceSnapshot]:
    """Download and parse one immutable vLLM ``main`` repository snapshot.

    Resolving the branch once prevents a collection run from comparing YAML
    files from different commits. Reading the selected files directly from the
    tar stream also replaces dozens of mutable raw-content API calls.
    """
    global _SOURCE_SNAPSHOT
    if _SOURCE_SNAPSHOT is not None:
        return _SOURCE_SNAPSHOT

    try:
        commit_response = requests.get(
            f"{VLLM_API_BASE}/commits/{VLLM_BRANCH}",
            headers=_github_headers(),
            timeout=30,
        )
        commit_response.raise_for_status()
        commit_sha = str(commit_response.json().get("sha") or "")
        if not commit_sha:
            raise ValueError("GitHub commit response did not contain a SHA")

        archive_response = requests.get(
            f"{VLLM_API_BASE}/tarball/{commit_sha}",
            headers=_github_headers(),
            timeout=120,
        )
        archive_response.raise_for_status()
        files: dict[str, object] = {}
        with tarfile.open(fileobj=io.BytesIO(archive_response.content), mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile() or "/" not in member.name:
                    continue
                path = member.name.split("/", 1)[1]
                if path != ".buildkite/test-amd.yaml" and not (
                    path.startswith(".buildkite/test_areas/") and path.endswith(".yaml")
                ):
                    continue
                handle = archive.extractfile(member)
                if handle is not None:
                    files[path] = yaml.safe_load(handle.read().decode("utf-8"))

        if ".buildkite/test-amd.yaml" not in files:
            raise ValueError("repository snapshot lacks .buildkite/test-amd.yaml")
        if not any(path.startswith(".buildkite/test_areas/") for path in files):
            raise ValueError("repository snapshot lacks .buildkite/test_areas YAML files")
        _SOURCE_SNAPSHOT = ConfigSourceSnapshot(
            commit_sha=commit_sha,
            files=files,
            fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        return _SOURCE_SNAPSHOT
    except Exception as e:
        log.warning("Failed to download vLLM %s snapshot: %s", VLLM_BRANCH, e)
        return None


def _fetch_yaml_from_github(path: str) -> Optional[dict]:
    """Return one YAML document from the commit-pinned source snapshot."""
    snapshot = _load_source_snapshot()
    if snapshot is None:
        return None
    data = snapshot.files.get(path)
    return data if isinstance(data, (dict, list)) else None


def _list_test_area_files() -> list[str]:
    """List test-area files from the same commit-pinned source snapshot."""
    snapshot = _load_source_snapshot()
    if snapshot is None:
        return []
    return sorted(
        path for path in snapshot.files
        if path.startswith(".buildkite/test_areas/") and path.endswith(".yaml")
    )


def _source_url(path: str, commit_sha: str) -> str:
    return f"https://github.com/{VLLM_REPOSITORY}/blob/{commit_sha}/{path}"


def _source_provenance() -> dict:
    snapshot = _load_source_snapshot()
    if snapshot is None:
        return {}
    return {
        "repository": VLLM_REPOSITORY,
        "branch": VLLM_BRANCH,
        "commit_sha": snapshot.commit_sha,
        "commit_url": f"https://github.com/{VLLM_REPOSITORY}/commit/{snapshot.commit_sha}",
        "amd_definition_url": _source_url(".buildkite/test-amd.yaml", snapshot.commit_sha),
        "upstream_definitions_url": f"https://github.com/{VLLM_REPOSITORY}/tree/{snapshot.commit_sha}/.buildkite/test_areas",
        "fetched_at": snapshot.fetched_at,
        "matching_rules": [
            "Match exact normalized YAML identities first.",
            "For unmatched definitions, accept a unique twin only when both command lists are non-empty and normalize to an exact command match.",
            f"Command twins also require a platform-neutral title similarity of at least {COMMAND_TWIN_TITLE_THRESHOLD:.0%}.",
            "Ambiguous command matches remain unmatched for manual review.",
        ],
    }


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
    amd_steps, nvidia_steps, _ = _load_config_steps()
    if amd_steps is None or nvidia_steps is None:
        return []
    return sorted({
        step.label.replace("%N", "").strip().lower()
        for step in [*amd_steps, *nvidia_steps]
        if step.parallelism and step.parallelism > 1 and "%N" in step.label
    })


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
        steps.append(_parse_step(item, '.buildkite/test-amd.yaml', group))
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


def _platform_neutral_title(label: str) -> str:
    """Normalize platform spelling while preserving the test's semantic title."""
    title = _parity_key_base(label)
    title = re.sub(r"\(\s*\d+\s+gpus?\s*\)", " ", title, flags=re.IGNORECASE)
    title = re.sub(
        r"\b(?:amd|cuda|nvidia|rocm|hip|h100|h200|b200|mi250|mi300|mi325|mi355)\b",
        " ",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"[^a-z0-9]+", " ", title.lower())
    return re.sub(r"\s+", " ", title).strip()


def _title_similarity(amd_step: ConfigStep, nvidia_step: ConfigStep) -> float:
    amd_title = _platform_neutral_title(amd_step.label)
    nvidia_title = _platform_neutral_title(nvidia_step.label)
    if not amd_title or not nvidia_title:
        return 0.0
    sequence = SequenceMatcher(None, amd_title, nvidia_title).ratio()
    amd_tokens = set(amd_title.split())
    nvidia_tokens = set(nvidia_title.split())
    token_score = len(amd_tokens & nvidia_tokens) / len(amd_tokens | nvidia_tokens)
    return max(sequence, token_score)


def _dedupe_steps(steps: list[ConfigStep]) -> dict[str, ConfigStep]:
    return {step.identity_key: step for step in reversed(steps)}


def _match_config_steps(
    amd_steps: list[ConfigStep],
    nvidia_steps: list[ConfigStep],
    mirrors: list[dict],
) -> tuple[list[ConfigMatch], list[ConfigStep], list[ConfigStep]]:
    """Match definitions by identity, then by unique exact-command twins."""
    amd_by_identity = _dedupe_steps(amd_steps)
    nvidia_by_identity = _dedupe_steps(nvidia_steps)
    mirrored_nvidia = {mirror["identity_key"] for mirror in mirrors}
    matches: list[ConfigMatch] = []
    matched_amd: set[str] = set()
    matched_nvidia: set[str] = set()

    for identity, amd_step in amd_by_identity.items():
        nvidia_step = nvidia_by_identity.get(identity)
        if nvidia_step is None:
            continue
        similarity = commands_similarity(amd_step.commands, nvidia_step.commands)
        matches.append(ConfigMatch(
            amd_step=amd_step,
            nvidia_step=nvidia_step,
            command_similarity=similarity,
            color=similarity_color(similarity),
            match_method="identity",
            title_similarity=_title_similarity(amd_step, nvidia_step),
        ))
        matched_amd.add(identity)
        matched_nvidia.add(identity)

    # Build unique-best proposals first, then reject a proposal if multiple AMD
    # definitions target the same upstream identity. Exact commands alone are
    # insufficient when labels are ambiguous.
    proposals = []
    candidates_by_nvidia: dict[str, list[tuple[float, str]]] = {}
    for amd_identity, amd_step in amd_by_identity.items():
        if amd_identity in matched_amd or not amd_step.commands:
            continue
        candidates = []
        for nvidia_identity, nvidia_step in nvidia_by_identity.items():
            if nvidia_identity in matched_nvidia or nvidia_identity in mirrored_nvidia:
                continue
            if not nvidia_step.commands:
                continue
            command_score = commands_similarity(amd_step.commands, nvidia_step.commands)
            if command_score < 0.999999:
                continue
            title_score = _title_similarity(amd_step, nvidia_step)
            if title_score >= COMMAND_TWIN_TITLE_THRESHOLD:
                candidates.append((title_score, nvidia_identity, nvidia_step))
                candidates_by_nvidia.setdefault(nvidia_identity, []).append(
                    (title_score, amd_identity)
                )
        candidates.sort(key=lambda row: (-row[0], row[1]))
        if not candidates:
            continue
        best_score = candidates[0][0]
        best = [row for row in candidates if abs(row[0] - best_score) < 1e-9]
        if len(best) == 1:
            proposals.append((best_score, amd_identity, amd_step, best[0][1], best[0][2]))

    for title_score, amd_identity, amd_step, nvidia_identity, nvidia_step in sorted(
        proposals, key=lambda row: (-row[0], row[1], row[3]),
    ):
        reverse_candidates = sorted(
            candidates_by_nvidia.get(nvidia_identity, []),
            key=lambda row: (-row[0], row[1]),
        )
        if not reverse_candidates:
            continue
        reverse_best_score = reverse_candidates[0][0]
        reverse_best = [
            row for row in reverse_candidates
            if abs(row[0] - reverse_best_score) < 1e-9
        ]
        if len(reverse_best) != 1 or reverse_best[0][1] != amd_identity:
            continue
        matches.append(ConfigMatch(
            amd_step=amd_step,
            nvidia_step=nvidia_step,
            command_similarity=1.0,
            color="green",
            match_method="command_twin",
            title_similarity=title_score,
        ))
        matched_amd.add(amd_identity)
        matched_nvidia.add(nvidia_identity)

    amd_only = [
        step for identity, step in amd_by_identity.items()
        if identity not in matched_amd and identity not in mirrored_nvidia
    ]
    nvidia_only = [
        step for identity, step in nvidia_by_identity.items()
        if identity not in matched_nvidia and identity not in mirrored_nvidia
    ]
    matches.sort(key=lambda match: (match.command_similarity, match.amd_step.label.lower()))
    amd_only.sort(key=lambda step: step.label.lower())
    nvidia_only.sort(key=lambda step: step.label.lower())
    return matches, amd_only, nvidia_only


def extract_parity_key_overrides() -> dict[str, str]:
    """Return normalized runtime-label -> YAML identity key overrides.

    Only identities present on both AMD and upstream are exported. This avoids
    remapping genuinely AMD-only or upstream-only labels while still fixing
    cases where one side encodes GPU count in YAML metadata and the other side
    encodes it in the label.
    """
    amd_steps, nvidia_steps, mirrors = _load_config_steps()
    if amd_steps is None or nvidia_steps is None:
        return {}

    matches, _, _ = _match_config_steps(amd_steps, nvidia_steps, mirrors or [])
    identities_by_label: dict[str, set[str]] = {}
    canonical_by_label: dict[str, str] = {}
    for match in matches:
        canonical = match.amd_step.identity_key
        for step in (match.amd_step, match.nvidia_step):
            identities_by_label.setdefault(step.normalized_label, set()).add(canonical)
            canonical_by_label[step.normalized_label] = canonical

    overrides: dict[str, str] = {}
    for normalized_label, canonical in canonical_by_label.items():
        if len(identities_by_label.get(normalized_label, set())) > 1:
            continue
        if _parity_key_base(normalized_label) == canonical:
            continue
        overrides[normalized_label] = canonical
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

    matches, amd_only, nvidia_only = _match_config_steps(amd_steps, nvidia_steps, mirrors)
    amd_deduped = _dedupe_steps(amd_steps)
    nvidia_deduped = _dedupe_steps(nvidia_steps)

    # Compute summary metrics
    total_amd = len(amd_deduped)
    total_nvidia = len(nvidia_deduped)
    match_rate = len(matches) / (len(matches) + len(amd_only)) * 100 if (len(matches) + len(amd_only)) > 0 else 0
    avg_similarity = (
        sum(m.command_similarity for m in matches) / len(matches) * 100
        if matches else 0
    )

    source = _source_provenance()
    commit_sha = source.get("commit_sha", "")
    identity_matches = sum(match.match_method == "identity" for match in matches)
    command_twins = sum(match.match_method == "command_twin" for match in matches)
    return {
        "generated_at": source.get("fetched_at"),
        "source": source,
        "summary": {
            "total_amd_steps": total_amd,
            "total_nvidia_steps": total_nvidia,
            "matched": len(matches),
            "identity_matches": identity_matches,
            "command_twins": command_twins,
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
                "title_similarity": round(m.title_similarity, 4),
                "match_method": m.match_method,
                "color": m.color,
                "amd_source": m.amd_step.source_file,
                "nvidia_source": m.nvidia_step.source_file,
                "amd_source_url": _source_url(m.amd_step.source_file, commit_sha),
                "nvidia_source_url": _source_url(m.nvidia_step.source_file, commit_sha),
                "amd_commands": m.amd_step.commands,
                "nvidia_commands": m.nvidia_step.commands,
            }
            for m in matches
        ],
        "amd_only": [
            {
                "label": s.label,
                "normalized": s.normalized_label,
                "identity_key": s.identity_key,
                "group": s.group,
                "source": s.source_file,
                "source_url": _source_url(s.source_file, commit_sha),
                "commands": s.commands,
            }
            for s in amd_only
        ],
        "nvidia_only": [
            {
                "label": s.label,
                "normalized": s.normalized_label,
                "identity_key": s.identity_key,
                "source": s.source_file,
                "source_url": _source_url(s.source_file, commit_sha),
                "commands": s.commands,
            }
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
                "source_url": _source_url(m["source_file"], commit_sha),
            }
            for m in mirrors
        ],
    }
