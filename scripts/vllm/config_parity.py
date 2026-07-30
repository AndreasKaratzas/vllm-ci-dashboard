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
import posixpath
import re
import shlex
import tarfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
from functools import lru_cache
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
from vllm.collect_amd_test_matrix import (
    canonical_title as matrix_canonical_title,
    definition_fingerprint as matrix_definition_fingerprint,
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
    definition_id: str = ""
    agent_pool: str = ""
    working_dir: str = ""
    semantic_title: str = ""
    definition_fingerprint: str = ""
    member_definition_ids: tuple[str, ...] = ()
    member_labels: tuple[str, ...] = ()
    member_groups: tuple[str, ...] = ()
    member_agent_pools: tuple[str, ...] = ()
    physical_member_count: int = 1
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
    relationship: str = ""


@dataclass
class ConfigMirrorVariant:
    """A standalone AMD definition linked to an upstream inline AMD mirror."""
    amd_step: ConfigStep
    nvidia_step: ConfigStep
    mirror: dict
    command_similarity: float
    amd_route_similarity: float
    color: str
    title_similarity: float = 1.0
    relationship: str = "same_hardware_command_variant"


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
            (
                "Retain provenance for every parsed YAML definition and report "
                "the physical AMD count separately."
            ),
            (
                "Coalesce only AMD architecture replicas with the same matrix "
                "canonical title, execution fingerprint, projected reference "
                "hardware, and GPU count; preserve every member definition ID, "
                "label, group, and agent pool."
            ),
            (
                "Report total_amd_steps as collision-safe parity nodes and "
                "amd_matrix_semantic_rows separately as the matrix row count."
            ),
            (
                "Count AMD identity families separately from parity nodes. "
                "Within one normalized identity, merge only cross-architecture "
                "replicas whose canonical YAML title or executed test target "
                "agrees; never collapse two definitions that coexist on the "
                "same AMD architecture."
            ),
            (
                "Exclude inline mirrors by exact upstream definition ID (or an "
                "exact source-file/raw-label fallback) from direct one-to-one "
                "assignment, never by a lossy normalized label."
            ),
            (
                "After direct assignment, classify compatible standalone AMD "
                "definitions that share an exact inline-mirror identity as "
                "mirror-linked variants instead of AMD-only gaps; retain both "
                "execution routes and their command evidence."
            ),
            (
                "Classify AMD definitions left over after one-to-one cardinality "
                "is exhausted as additional execution or hardware variants only "
                "when that exact upstream identity already has a direct match; "
                "unpaired hardware collisions remain gaps."
            ),
            (
                "Reject explicit reference-hardware or GPU-count mismatches "
                "before matching shared normalized identities."
            ),
            "Prefer an exact YAML label with an exact normalized command list.",
            (
                "Before shared-identity matching, reserve unique bidirectional "
                "exact-command/title twins across different identities so GPU "
                "metadata cannot hide a command-equivalent counterpart. An "
                "exact same-identity definition, including an inline mirror, "
                "blocks this fallback."
            ),
            (
                "Within a shared identity, use a deterministic maximum-cardinality "
                "one-to-one assignment weighted by projected counterpart label, "
                "hardware, exact label, and commands."
            ),
            (
                "For unmatched identities, accept a unique twin only when both "
                "command lists are non-empty and normalize to an exact command "
                "match."
            ),
            f"Command twins also require a platform-neutral title similarity of at least {COMMAND_TWIN_TITLE_THRESHOLD:.0%}.",
            (
                "Ignore CUDA/HIP visibility and platform target-suite selector "
                "values when measuring command similarity; preserve other "
                "environment assignments that can change test coverage."
            ),
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
    "lm eval small models (1 gpus)": "lm eval small models (hardware variants)",
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


def _parse_step(
    item: dict,
    source_file: str,
    group: str,
    yaml_index: int = 0,
) -> ConfigStep:
    """Parse a single step dictionary into a ConfigStep."""
    label = item.get('label', 'unknown')
    cmds = item.get('commands', [])
    if 'command' in item:
        cmds = [item['command']]
    cmds = _flatten_commands(cmds)

    num_gpus = item.get('num_devices') or item.get('num_gpus')
    step_key = str(item.get("key") or "").strip()
    definition_id = f"{source_file}#{step_key or yaml_index}"

    return ConfigStep(
        label=label,
        normalized_label=_normalize_job_name(label),
        identity_key=_config_identity_key(label, num_gpus),
        source_file=source_file,
        group=group,
        commands=cmds,
        definition_id=definition_id,
        agent_pool=str(item.get("agent_pool") or ""),
        working_dir=str(item.get("working_dir") or ""),
        semantic_title=matrix_canonical_title(label),
        definition_fingerprint=matrix_definition_fingerprint(item),
        member_definition_ids=(definition_id,),
        member_labels=(label,),
        member_groups=(group,),
        member_agent_pools=(str(item.get("agent_pool") or ""),),
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
    for yaml_index, item in enumerate(data.get('steps', [])):
        agent_pool = item.get('agent_pool', '')
        if 'mi355' in agent_pool:
            group = 'mi355'
        elif 'mi325' in agent_pool:
            group = 'mi325'
        else:
            group = 'amd'
        steps.append(
            _parse_step(
                item,
                '.buildkite/test-amd.yaml',
                group,
                yaml_index,
            )
        )
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

        for yaml_index, item in enumerate(data.get('steps', [])):
            step = _parse_step(item, filename, group_name, yaml_index)
            nvidia_steps.append(step)

            mirror = item.get('mirror')
            if mirror and isinstance(mirror, dict) and 'amd' in mirror:
                amd_cfg = (
                    mirror['amd']
                    if isinstance(mirror.get('amd'), dict)
                    else {}
                )
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
                    "nvidia_definition_id": step.definition_id,
                    "amd_device": str(
                        amd_cfg.get("device")
                        or amd_cfg.get("agent_pool")
                        or ""
                    ),
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


def _yaml_label_key(value: str) -> str:
    """Normalize insignificant YAML label formatting without folding hardware."""
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


REFERENCE_HARDWARE = r"(?:gh200|gb200|h200|h100|a100|b200|l40s?)"
REFERENCE_HARDWARE_RE = re.compile(
    rf"(?:(?P<count>\d+)\s*x\s*)?(?P<hardware>{REFERENCE_HARDWARE})\b",
    re.IGNORECASE,
)
REFERENCE_HARDWARE_SPACE_RE = re.compile(
    rf"(?P<count>\d+)\s+(?P<hardware>{REFERENCE_HARDWARE})s?\b",
    re.IGNORECASE,
)
GENERIC_GPU_COUNT_RE = re.compile(
    r"\(\s*(?P<count>\d+)\s+gpus?\s*\)",
    re.IGNORECASE,
)
AMD_COUNTERPART_PAIR_RE = re.compile(
    rf"(?:(?P<count>\d+)\s*x\s*)?"
    rf"(?P<hardware>{REFERENCE_HARDWARE})\s*-\s*"
    r"(?:(?P<amd_count>\d+)\s*x\s*)?mi\d{2,4}b?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HardwareProjection:
    """Reference-platform identity retained before broad label normalization."""
    counterpart_key: str
    hardware: str = ""
    gpu_count: Optional[int] = None
    kind: str = "none"


def _hardware_projection(step: ConfigStep) -> HardwareProjection:
    label = re.sub(
        r"\s*%n\s*$",
        "",
        _yaml_label_key(step.label),
    )

    def counterpart_replacement(match: re.Match) -> str:
        count = match.group("count")
        hardware = match.group("hardware").casefold()
        return f"{count}x{hardware}" if count else hardware

    counterpart = AMD_COUNTERPART_PAIR_RE.sub(
        counterpart_replacement,
        label,
    )
    counterpart = re.sub(
        rf"\(\s*(?P<count>\d+)\s+gpus?\s*\)\s*"
        rf"\(\s*(?P<hardware>{REFERENCE_HARDWARE})\s*\)",
        lambda match: (
            f"({match.group('count')}x"
            f"{match.group('hardware').casefold()})"
        ),
        counterpart,
        flags=re.IGNORECASE,
    )
    counterpart = REFERENCE_HARDWARE_SPACE_RE.sub(
        lambda match: (
            f"{match.group('count')}x"
            f"{match.group('hardware').casefold()}"
        ),
        counterpart,
    )
    counterpart = re.sub(r"\s+", " ", counterpart).strip()

    space_explicit = REFERENCE_HARDWARE_SPACE_RE.search(label)
    explicit = REFERENCE_HARDWARE_RE.search(label) or space_explicit
    if explicit:
        count = _gpu_count(explicit.group("count"))
        if count is None:
            if (
                space_explicit
                and space_explicit.group("hardware").casefold()
                == explicit.group("hardware").casefold()
            ):
                count = _gpu_count(space_explicit.group("count"))
        if count is None:
            count = _gpu_count(step.num_gpus)
        if count is None:
            generic = GENERIC_GPU_COUNT_RE.search(label)
            count = _gpu_count(generic.group("count")) if generic else None
        return HardwareProjection(
            counterpart_key=counterpart,
            hardware=explicit.group("hardware").casefold(),
            gpu_count=count,
            kind="explicit",
        )

    generic = GENERIC_GPU_COUNT_RE.search(label)
    count = _gpu_count(generic.group("count")) if generic else _gpu_count(
        step.num_gpus
    )
    return HardwareProjection(
        counterpart_key=counterpart,
        gpu_count=count,
        kind="generic" if count else "none",
    )


def _hardware_compatibility(
    left: ConfigStep,
    right: ConfigStep,
) -> int | None:
    left_projection = _hardware_projection(left)
    right_projection = _hardware_projection(right)
    if (
        left_projection.hardware
        and right_projection.hardware
        and left_projection.hardware != right_projection.hardware
    ):
        return None
    if (
        left_projection.gpu_count
        and right_projection.gpu_count
        and left_projection.gpu_count != right_projection.gpu_count
    ):
        return None
    if left_projection.hardware and right_projection.hardware:
        return 3
    if left_projection.kind == "generic" and right_projection.kind == "generic":
        return 2
    return 1 if (left_projection.hardware or right_projection.hardware) else 2


def _counterpart_labels_match(left: ConfigStep, right: ConfigStep) -> bool:
    return (
        _hardware_projection(left).counterpart_key
        == _hardware_projection(right).counterpart_key
    )


def _step_sort_key(index: int, step: ConfigStep) -> tuple:
    """Stable ordering for collision groups and otherwise identical rows."""
    return (
        str(step.identity_key).casefold(),
        _yaml_label_key(step.label),
        tuple(str(command) for command in step.commands),
        str(step.source_file).casefold(),
        str(step.definition_id).casefold(),
        str(step.group).casefold(),
        index,
    )


def _semantic_amd_steps(
    steps: list[ConfigStep],
) -> list[ConfigStep]:
    """Coalesce only matrix-equivalent AMD execution replicas."""
    fingerprints_by_title: dict[str, set[str]] = {}
    for step in steps:
        if not step.semantic_title or not step.definition_fingerprint:
            continue
        fingerprints_by_title.setdefault(step.semantic_title, set()).add(
            step.definition_fingerprint
        )

    groups: dict[tuple[str, ...], list[tuple[int, ConfigStep]]] = {}
    for index, step in enumerate(steps):
        if not step.semantic_title or not step.definition_fingerprint:
            key = ("physical", step.definition_id or str(index))
        else:
            projection = _hardware_projection(step)
            key_parts = [
                "semantic",
                step.semantic_title,
            ]
            if len(fingerprints_by_title.get(step.semantic_title, set())) > 1:
                key_parts.append(step.definition_fingerprint)
            key_parts.extend((
                projection.hardware,
                str(projection.gpu_count or ""),
            ))
            key = tuple(key_parts)
        groups.setdefault(key, []).append((index, step))

    logical_steps = []
    for members in groups.values():
        members.sort(key=lambda item: _step_sort_key(item[0], item[1]))
        representative = members[0][1]
        if len(members) == 1:
            logical_steps.append(representative)
            continue
        logical_steps.append(
            replace(
                representative,
                member_definition_ids=tuple(
                    step.definition_id for _index, step in members
                ),
                member_labels=tuple(
                    step.label for _index, step in members
                ),
                member_groups=tuple(
                    step.group for _index, step in members
                ),
                member_agent_pools=tuple(
                    step.agent_pool for _index, step in members
                ),
                physical_member_count=len(members),
            )
        )
    return logical_steps


def _matrix_semantic_amd_count(steps: list[ConfigStep]) -> int:
    """Count rows under the AMD matrix's title/fingerprint policy."""
    fingerprints_by_title: dict[str, set[str]] = {}
    for step in steps:
        if step.semantic_title and step.definition_fingerprint:
            fingerprints_by_title.setdefault(step.semantic_title, set()).add(
                step.definition_fingerprint
            )

    keys: set[tuple[str, ...]] = set()
    for index, step in enumerate(steps):
        if not step.semantic_title or not step.definition_fingerprint:
            key = ("physical", step.definition_id or str(index))
        elif len(fingerprints_by_title.get(step.semantic_title, set())) > 1:
            key = (
                "semantic",
                step.semantic_title,
                step.definition_fingerprint,
            )
        else:
            key = ("semantic", step.semantic_title)
        keys.add(key)
    return len(keys)


AMD_ARCHITECTURE_RE = re.compile(r"mi\d{3,4}b?", re.IGNORECASE)
WORKLOAD_RUNNERS = frozenset({
    "bash",
    "py.test",
    "pytest",
    "python",
    "python3",
    "sh",
    "torchrun",
})
PYTEST_VALUE_OPTIONS = frozenset({
    "-k",
    "-m",
    "-n",
    "--basetemp",
    "--capture",
    "--color",
    "--confcutdir",
    "--deselect",
    "--durations",
    "--ignore",
    "--ignore-glob",
    "--junitxml",
    "--maxfail",
    "--numprocesses",
    "--rootdir",
    "--tb",
})


def _amd_step_architectures(step: ConfigStep) -> frozenset[str]:
    """Return AMD architectures represented by one logical parity node."""
    architectures = set()
    pools = step.member_agent_pools or (step.agent_pool,)
    for pool in pools:
        normalized_pool = str(pool or "").strip().casefold()
        match = AMD_ARCHITECTURE_RE.search(normalized_pool)
        if match:
            architectures.add(match.group(0).casefold())
        elif normalized_pool:
            architectures.add(f"pool:{normalized_pool}")
    if not architectures:
        # Treat all unknown placements as one architecture: without YAML
        # placement evidence it is unsafe to call two rows replicas.
        architectures.add("unknown")
    return frozenset(architectures)


def _normalize_workload_target(target: str, working_dir: str) -> str:
    """Resolve one executed test/script target to a workspace-relative path."""
    value = str(target or "").strip().strip("\"'").rstrip(",")
    if not value:
        return ""
    node_suffix = ""
    if "::" in value:
        value, node_suffix = value.split("::", 1)
        node_suffix = f"::{node_suffix}"
    if not value or value.startswith("-"):
        return ""
    if not (
        "/" in value
        or value.endswith((".py", ".sh"))
    ):
        return ""

    base = str(working_dir or "").strip() or "/vllm-workspace/tests"
    if value.startswith("/"):
        resolved = posixpath.normpath(value)
    else:
        resolved = posixpath.normpath(posixpath.join(base, value))
    workspace_prefix = "/vllm-workspace/"
    if resolved.startswith(workspace_prefix):
        resolved = resolved[len(workspace_prefix):]
    elif resolved == "/vllm-workspace":
        resolved = "."
    return resolved.rstrip("/") + node_suffix


def _step_workload_targets(step: ConfigStep) -> frozenset[str]:
    """Extract actual pytest/python/shell targets from flattened YAML commands.

    Setup commands, environment assignments, selectors, and config-list
    arguments are intentionally excluded. They commonly differ between GPU
    architectures without defining a different test family.
    """
    targets = set()
    for command in step.commands:
        try:
            tokens = shlex.split(str(command))
        except ValueError:
            tokens = str(command).split()
        for index, token in enumerate(tokens):
            runner = token.rsplit("/", 1)[-1].casefold()
            if runner not in WORKLOAD_RUNNERS:
                continue

            offset = index + 1
            if (
                runner in {"python", "python3"}
                and tokens[offset:offset + 2]
                and tokens[offset:offset + 1] == ["-m"]
            ):
                if (
                    len(tokens) <= offset + 1
                    or tokens[offset + 1].casefold() not in {"pytest", "py.test"}
                ):
                    continue
                runner = "pytest"
                offset += 2

            skip_value = False
            for candidate in tokens[offset:]:
                if candidate in {"&&", "||", ";", "|"}:
                    break
                if skip_value:
                    skip_value = False
                    continue
                if candidate.startswith("-"):
                    option = candidate.split("=", 1)[0]
                    skip_value = "=" not in candidate and option in PYTEST_VALUE_OPTIONS
                    continue
                normalized = _normalize_workload_target(
                    candidate,
                    step.working_dir,
                )
                if normalized:
                    targets.add(normalized)
                    # A runner's first positional path is its executed target;
                    # later paths are usually option values such as --ignore.
                    break
    return frozenset(targets)


def _identity_family_edge_weight(
    left: ConfigStep,
    right: ConfigStep,
) -> tuple[int, ...] | None:
    """Score one safe cross-architecture identity-family replica edge."""
    if left.identity_key != right.identity_key:
        return None
    if _amd_step_architectures(left) & _amd_step_architectures(right):
        return None

    left_title = str(left.semantic_title or "").strip().casefold()
    right_title = str(right.semantic_title or "").strip().casefold()
    same_title = bool(left_title and left_title == right_title)
    left_targets = _step_workload_targets(left)
    right_targets = _step_workload_targets(right)
    shared_targets = left_targets & right_targets
    if not same_title and not shared_targets:
        return None

    target_union = left_targets | right_targets
    target_overlap = (
        round(len(shared_targets) / len(target_union) * 1_000_000)
        if target_union
        else 0
    )
    return (
        int(same_title),
        int(bool(left_targets) and left_targets == right_targets),
        len(shared_targets),
        target_overlap,
        int(_exact_commands(left, right)),
        round(commands_similarity(left.commands, right.commands) * 1_000_000),
        round(_raw_label_similarity(left, right) * 1_000_000),
    )


def _amd_identity_family_keys(
    steps: list[ConfigStep],
) -> tuple[dict[str, tuple[ConfigStep, ...]], dict[str, str]]:
    """Group logical AMD nodes into architecture-aware identity families.

    The constrained maximum-spanning forest prevents transitive merges from
    placing two definitions for the same AMD architecture in one family.
    """
    parent = list(range(len(steps)))
    component_architectures = [
        set(_amd_step_architectures(step))
        for step in steps
    ]

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    edges = []
    for left_index, left in enumerate(steps):
        for right_index in range(left_index + 1, len(steps)):
            right = steps[right_index]
            weight = _identity_family_edge_weight(left, right)
            if weight is None:
                continue
            pair_keys = sorted((
                _step_sort_key(left_index, left),
                _step_sort_key(right_index, right),
            ))
            edges.append((
                weight,
                pair_keys[0],
                pair_keys[1],
                left_index,
                right_index,
            ))

    edges.sort(key=lambda edge: (
        tuple(-value for value in edge[0]),
        edge[1],
        edge[2],
    ))
    for _weight, _left_key, _right_key, left_index, right_index in edges:
        left_root = find(left_index)
        right_root = find(right_index)
        if left_root == right_root:
            continue
        if component_architectures[left_root] & component_architectures[right_root]:
            continue
        if left_root > right_root:
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        component_architectures[left_root].update(
            component_architectures[right_root]
        )

    components_by_identity: dict[str, list[tuple[ConfigStep, ...]]] = {}
    component_members: dict[int, list[tuple[int, ConfigStep]]] = {}
    for index, step in enumerate(steps):
        component_members.setdefault(find(index), []).append((index, step))
    for members in component_members.values():
        members.sort(key=lambda item: _step_sort_key(item[0], item[1]))
        component = tuple(step for _index, step in members)
        components_by_identity.setdefault(
            component[0].identity_key,
            [],
        ).append(component)

    families: dict[str, tuple[ConfigStep, ...]] = {}
    family_by_definition_id: dict[str, str] = {}
    for identity in sorted(components_by_identity):
        components = sorted(
            components_by_identity[identity],
            key=lambda component: _step_sort_key(
                steps.index(component[0]),
                component[0],
            ),
        )
        for offset, component in enumerate(components, start=1):
            family_key = (
                identity
                if len(components) == 1
                else f"{identity}::{offset}"
            )
            families[family_key] = component
            for step in component:
                definition_ids = (
                    step.member_definition_ids
                    or (step.definition_id,)
                )
                for definition_id in definition_ids:
                    if definition_id:
                        family_by_definition_id[definition_id] = family_key
    return families, family_by_definition_id


def _exact_commands(left: ConfigStep, right: ConfigStep) -> bool:
    return bool(
        left.commands
        and right.commands
        and commands_similarity(left.commands, right.commands) >= 0.999999
    )


def _raw_label_similarity(left: ConfigStep, right: ConfigStep) -> float:
    return SequenceMatcher(
        None,
        _yaml_label_key(left.label),
        _yaml_label_key(right.label),
    ).ratio()


def _identity_edge_weight(
    amd_step: ConfigStep,
    nvidia_step: ConfigStep,
) -> tuple[int, ...] | None:
    """Return the lexicographic weight for one compatible identity edge."""
    hardware_rank = _hardware_compatibility(amd_step, nvidia_step)
    if hardware_rank is None:
        return None
    return (
        int(_counterpart_labels_match(amd_step, nvidia_step)),
        hardware_rank,
        int(
            _yaml_label_key(amd_step.label)
            == _yaml_label_key(nvidia_step.label)
        ),
        int(_exact_commands(amd_step, nvidia_step)),
        round(commands_similarity(amd_step.commands, nvidia_step.commands) * 1_000_000),
        round(_raw_label_similarity(amd_step, nvidia_step) * 1_000_000),
        round(_title_similarity(amd_step, nvidia_step) * 1_000_000),
    )


def _pair_signature(pairs: tuple[tuple[int, ConfigStep, int, ConfigStep], ...]) -> tuple:
    """Stable tie-breaker for equally weighted assignments."""
    return tuple(sorted(
        (
            _step_sort_key(amd_index, amd_step),
            _step_sort_key(nvidia_index, nvidia_step),
        )
        for amd_index, amd_step, nvidia_index, nvidia_step in pairs
    ))


def _optimal_identity_pairs(
    amd_refs: list[tuple[int, ConfigStep]],
    nvidia_refs: list[tuple[int, ConfigStep]],
) -> list[tuple[int, ConfigStep, int, ConfigStep]]:
    """Find a maximum-cardinality, maximum-weight compatible assignment."""
    if not amd_refs or not nvidia_refs:
        return []

    sorted_amd = sorted(
        amd_refs,
        key=lambda item: _step_sort_key(item[0], item[1]),
    )
    sorted_nvidia = sorted(
        nvidia_refs,
        key=lambda item: _step_sort_key(item[0], item[1]),
    )

    # Mask the smaller side. Identity collision buckets are normally tiny, and
    # this orientation also keeps the exact dynamic program bounded when one
    # side has many more definitions than the other.
    rows_are_amd = len(sorted_amd) >= len(sorted_nvidia)
    rows = sorted_amd if rows_are_amd else sorted_nvidia
    choices = sorted_nvidia if rows_are_amd else sorted_amd
    empty_score = (0,) * 8

    def prefer(
        candidate: tuple[
            tuple[int, ...],
            tuple[tuple[int, ConfigStep, int, ConfigStep], ...],
        ],
        incumbent: tuple[
            tuple[int, ...],
            tuple[tuple[int, ConfigStep, int, ConfigStep], ...],
        ],
    ) -> bool:
        if candidate[0] != incumbent[0]:
            return candidate[0] > incumbent[0]
        return _pair_signature(candidate[1]) < _pair_signature(incumbent[1])

    @lru_cache(maxsize=None)
    def solve(
        row_offset: int,
        used_choices: int,
    ) -> tuple[
        tuple[int, ...],
        tuple[tuple[int, ConfigStep, int, ConfigStep], ...],
    ]:
        if row_offset >= len(rows):
            return empty_score, ()

        best = solve(row_offset + 1, used_choices)
        row_index, row_step = rows[row_offset]
        for choice_offset, (choice_index, choice_step) in enumerate(choices):
            bit = 1 << choice_offset
            if used_choices & bit:
                continue
            if rows_are_amd:
                amd_index, amd_step = row_index, row_step
                nvidia_index, nvidia_step = choice_index, choice_step
            else:
                amd_index, amd_step = choice_index, choice_step
                nvidia_index, nvidia_step = row_index, row_step
            edge_weight = _identity_edge_weight(amd_step, nvidia_step)
            if edge_weight is None:
                continue

            remaining_score, remaining_pairs = solve(
                row_offset + 1,
                used_choices | bit,
            )
            score = (
                remaining_score[0] + 1,
                *(
                    remaining_score[index + 1] + value
                    for index, value in enumerate(edge_weight)
                ),
            )
            candidate = (
                score,
                (
                    (amd_index, amd_step, nvidia_index, nvidia_step),
                    *remaining_pairs,
                ),
            )
            if prefer(candidate, best):
                best = candidate
        return best

    _score, pairs = solve(0, 0)
    return sorted(
        pairs,
        key=lambda pair: (
            _step_sort_key(pair[0], pair[1]),
            _step_sort_key(pair[2], pair[3]),
        ),
    )


def _resolved_inline_mirrors(
    nvidia_steps: list[ConfigStep],
    mirrors: list[dict],
) -> list[tuple[dict, int, ConfigStep]]:
    """Resolve each inline mirror to one exact upstream YAML definition."""
    resolved: list[tuple[dict, int, ConfigStep]] = []
    claimed: set[int] = set()
    for mirror in mirrors:
        definition_id = str(mirror.get("nvidia_definition_id") or "")
        if definition_id:
            exact_id = [
                (index, step)
                for index, step in enumerate(nvidia_steps)
                if index not in claimed
                and step.definition_id == definition_id
            ]
            if exact_id:
                index, step = min(
                    exact_id,
                    key=lambda item: _step_sort_key(item[0], item[1]),
                )
                claimed.add(index)
                resolved.append((mirror, index, step))
                continue

        identity = str(mirror.get("identity_key") or "")
        raw_label = str(mirror.get("nvidia_label") or "")
        source_file = str(mirror.get("source_file") or "")
        candidates = [
            (index, step)
            for index, step in enumerate(nvidia_steps)
            if index not in claimed
            and step.identity_key == identity
            and str(step.label) == raw_label
            and step.source_file == source_file
        ]
        if not candidates:
            continue
        index, step = min(
            candidates,
            key=lambda item: _step_sort_key(item[0], item[1]),
        )
        claimed.add(index)
        resolved.append((mirror, index, step))
    return resolved


def _mirrored_nvidia_indices(
    nvidia_steps: list[ConfigStep],
    mirrors: list[dict],
) -> set[int]:
    """Return upstream rows already represented by inline AMD mirrors."""
    return {
        index
        for _mirror, index, _step in _resolved_inline_mirrors(
            nvidia_steps,
            mirrors,
        )
    }


def _queue_identity(value: str) -> str:
    key = str(value or "").strip().casefold().replace("-", "_")
    return key.removeprefix("amd_")


def _classify_inline_mirror_variants(
    amd_only: list[ConfigStep],
    nvidia_steps: list[ConfigStep],
    mirrors: list[dict],
) -> tuple[list[ConfigMirrorVariant], list[ConfigStep]]:
    """Separate mirror-linked standalone AMD variants from true AMD gaps.

    Direct matching remains one-to-one.  Inline mirrors are intentionally
    reserved from that assignment because they already define an AMD route in
    ``test_areas``.  A standalone ``test-amd.yaml`` definition with the same
    compatible identity is still covered by upstream source, though, and must
    not be described as AMD-only.  Multiple standalone execution variants may
    link to one inline mirror; every physical/logical AMD definition retains
    its own provenance and command comparison.
    """
    mirrors_by_identity: dict[
        str,
        list[tuple[dict, int, ConfigStep]],
    ] = {}
    for mirror, index, step in _resolved_inline_mirrors(
        nvidia_steps,
        mirrors,
    ):
        mirrors_by_identity.setdefault(step.identity_key, []).append(
            (mirror, index, step)
        )

    variants: list[ConfigMirrorVariant] = []
    remaining: list[ConfigStep] = []
    for amd_step in amd_only:
        candidates = []
        for mirror, nvidia_index, nvidia_step in mirrors_by_identity.get(
            amd_step.identity_key,
            [],
        ):
            weight = _identity_edge_weight(amd_step, nvidia_step)
            compatible = weight is not None
            if weight is None:
                weight = (
                    0,
                    -1,
                    int(
                        _yaml_label_key(amd_step.label)
                        == _yaml_label_key(nvidia_step.label)
                    ),
                    int(_exact_commands(amd_step, nvidia_step)),
                    round(
                        commands_similarity(
                            amd_step.commands,
                            nvidia_step.commands,
                        )
                        * 1_000_000
                    ),
                    round(
                        _raw_label_similarity(
                            amd_step,
                            nvidia_step,
                        )
                        * 1_000_000
                    ),
                    round(
                        _title_similarity(
                            amd_step,
                            nvidia_step,
                        )
                        * 1_000_000
                    ),
                )
            candidates.append((
                weight,
                _step_sort_key(nvidia_index, nvidia_step),
                mirror,
                nvidia_step,
                compatible,
            ))
        if not candidates:
            remaining.append(amd_step)
            continue

        best_weight = max(candidate[0] for candidate in candidates)
        best = [
            candidate
            for candidate in candidates
            if candidate[0] == best_weight
        ]
        if len(best) != 1:
            remaining.append(amd_step)
            continue

        _weight, _sort_key, mirror, nvidia_step, compatible = best[0]
        command_similarity = commands_similarity(
            amd_step.commands,
            nvidia_step.commands,
        )
        inline_amd_commands = list(
            mirror.get("amd_commands")
            or nvidia_step.commands
        )
        amd_route_similarity = commands_similarity(
            amd_step.commands,
            inline_amd_commands,
        )
        mirror_device = _queue_identity(
            str(mirror.get("amd_device") or "")
        )
        member_pools = {
            _queue_identity(pool)
            for pool in (
                amd_step.member_agent_pools
                or (amd_step.agent_pool,)
            )
            if str(pool or "").strip()
        }
        if (
            not compatible
            or mirror_device
            and mirror_device not in member_pools
        ):
            relationship = "hardware_variant"
        elif amd_route_similarity >= 0.999999:
            relationship = "effective_command_duplicate"
        else:
            relationship = "same_hardware_command_variant"
        variants.append(ConfigMirrorVariant(
            amd_step=amd_step,
            nvidia_step=nvidia_step,
            mirror=mirror,
            command_similarity=command_similarity,
            amd_route_similarity=amd_route_similarity,
            color=similarity_color(command_similarity),
            title_similarity=_title_similarity(amd_step, nvidia_step),
            relationship=relationship,
        ))

    variants.sort(key=lambda variant: (
        variant.command_similarity,
        _yaml_label_key(variant.amd_step.label),
        _yaml_label_key(variant.nvidia_step.label),
        variant.amd_step.source_file,
        variant.nvidia_step.source_file,
    ))
    remaining.sort(key=lambda step: (
        _yaml_label_key(step.label),
        step.identity_key,
        step.source_file,
        step.group,
        tuple(step.commands),
    ))
    return variants, remaining


def _classify_additional_variants(
    amd_only: list[ConfigStep],
    direct_matches: list[ConfigMatch],
) -> tuple[list[ConfigMatch], list[ConfigStep]]:
    """Link excess AMD cardinality to an already matched upstream identity."""
    upstream_by_identity: dict[str, list[tuple[int, ConfigStep]]] = {}
    for index, match in enumerate(direct_matches):
        step = match.nvidia_step
        upstream_by_identity.setdefault(step.identity_key, []).append(
            (index, step)
        )

    variants: list[ConfigMatch] = []
    remaining: list[ConfigStep] = []
    for amd_step in amd_only:
        candidates = []
        for nvidia_index, nvidia_step in upstream_by_identity.get(
            amd_step.identity_key,
            [],
        ):
            weight = _identity_edge_weight(amd_step, nvidia_step)
            compatible = weight is not None
            if weight is None:
                weight = (
                    0,
                    -1,
                    int(
                        _yaml_label_key(amd_step.label)
                        == _yaml_label_key(nvidia_step.label)
                    ),
                    int(_exact_commands(amd_step, nvidia_step)),
                    round(
                        commands_similarity(
                            amd_step.commands,
                            nvidia_step.commands,
                        )
                        * 1_000_000
                    ),
                    round(
                        _raw_label_similarity(
                            amd_step,
                            nvidia_step,
                        )
                        * 1_000_000
                    ),
                    round(
                        _title_similarity(
                            amd_step,
                            nvidia_step,
                        )
                        * 1_000_000
                    ),
                )
            candidates.append((
                (int(compatible), *weight),
                _step_sort_key(nvidia_index, nvidia_step),
                nvidia_step,
                compatible,
            ))
        if not candidates:
            remaining.append(amd_step)
            continue

        best_weight = max(candidate[0] for candidate in candidates)
        _weight, _sort_key, nvidia_step, compatible = min(
            (
                candidate
                for candidate in candidates
                if candidate[0] == best_weight
            ),
            key=lambda candidate: candidate[1],
        )
        similarity = commands_similarity(
            amd_step.commands,
            nvidia_step.commands,
        )
        variants.append(ConfigMatch(
            amd_step=amd_step,
            nvidia_step=nvidia_step,
            command_similarity=similarity,
            color=similarity_color(similarity),
            match_method="additional_variant",
            title_similarity=_title_similarity(amd_step, nvidia_step),
            relationship=(
                "additional_execution_variant"
                if compatible
                else "additional_hardware_variant"
            ),
        ))

    variants.sort(key=lambda variant: (
        variant.command_similarity,
        _yaml_label_key(variant.amd_step.label),
        _yaml_label_key(variant.nvidia_step.label),
        variant.amd_step.source_file,
        variant.nvidia_step.source_file,
    ))
    remaining.sort(key=lambda step: (
        _yaml_label_key(step.label),
        step.identity_key,
        step.source_file,
        step.group,
        tuple(step.commands),
    ))
    return variants, remaining


def _match_config_steps(
    amd_steps: list[ConfigStep],
    nvidia_steps: list[ConfigStep],
    mirrors: list[dict],
) -> tuple[list[ConfigMatch], list[ConfigStep], list[ConfigStep]]:
    """Match logical definitions one-to-one while preserving physical provenance."""
    logical_amd_steps = _semantic_amd_steps(amd_steps)
    amd_refs = list(enumerate(logical_amd_steps))
    nvidia_refs = list(enumerate(nvidia_steps))
    mirrored_nvidia = _mirrored_nvidia_indices(nvidia_steps, mirrors)
    matches: list[ConfigMatch] = []
    matched_amd: set[int] = set()
    matched_nvidia: set[int] = set(mirrored_nvidia)

    def add_match(
        amd_index: int,
        amd_step: ConfigStep,
        nvidia_index: int,
        nvidia_step: ConfigStep,
    ) -> None:
        similarity = commands_similarity(amd_step.commands, nvidia_step.commands)
        matches.append(ConfigMatch(
            amd_step=amd_step,
            nvidia_step=nvidia_step,
            command_similarity=similarity,
            color=similarity_color(similarity),
            match_method=(
                "identity"
                if amd_step.identity_key == nvidia_step.identity_key
                else "command_twin"
            ),
            title_similarity=_title_similarity(amd_step, nvidia_step),
        ))
        matched_amd.add(amd_index)
        matched_nvidia.add(nvidia_index)

    def unmatched_amd() -> list[tuple[int, ConfigStep]]:
        return [
            ref for ref in amd_refs
            if ref[0] not in matched_amd
        ]

    def unmatched_nvidia() -> list[tuple[int, ConfigStep]]:
        return [
            ref for ref in nvidia_refs
            if ref[0] not in matched_nvidia
        ]

    # Strongest evidence: exact YAML labels with exact normalized commands.
    # Iterate because resolving a singleton can expose another singleton in the
    # same collision graph without relying on source order.
    while True:
        available_amd = unmatched_amd()
        available_nvidia = unmatched_nvidia()
        by_amd = {
            amd_index: [
                (nvidia_index, nvidia_step)
                for nvidia_index, nvidia_step in available_nvidia
                if _yaml_label_key(amd_step.label)
                == _yaml_label_key(nvidia_step.label)
                and _exact_commands(amd_step, nvidia_step)
                and _hardware_compatibility(amd_step, nvidia_step) is not None
            ]
            for amd_index, amd_step in available_amd
        }
        by_nvidia = {
            nvidia_index: [
                (amd_index, amd_step)
                for amd_index, amd_step in available_amd
                if _yaml_label_key(amd_step.label)
                == _yaml_label_key(nvidia_step.label)
                and _exact_commands(amd_step, nvidia_step)
                and _hardware_compatibility(amd_step, nvidia_step) is not None
            ]
            for nvidia_index, nvidia_step in available_nvidia
        }
        pairs = []
        for amd_index, amd_step in available_amd:
            candidates = by_amd.get(amd_index) or []
            if len(candidates) != 1:
                continue
            nvidia_index, nvidia_step = candidates[0]
            reverse = by_nvidia.get(nvidia_index) or []
            if len(reverse) == 1 and reverse[0][0] == amd_index:
                pairs.append((
                    _step_sort_key(amd_index, amd_step),
                    _step_sort_key(nvidia_index, nvidia_step),
                    amd_index,
                    amd_step,
                    nvidia_index,
                    nvidia_step,
                ))
        if not pairs:
            break
        for _amd_key, _nvidia_key, amd_index, amd_step, nvidia_index, nvidia_step in sorted(pairs):
            if amd_index in matched_amd or nvidia_index in matched_nvidia:
                continue
            add_match(amd_index, amd_step, nvidia_index, nvidia_step)

    # Reserve unique, bidirectional exact-command/title pairs before matching
    # remaining shared identities. This retains the metadata-conflict override
    # while allowing multiple definitions to coexist under one identity. A
    # mirrored definition is unavailable for a direct pair but still blocks a
    # cross-identity fallback from claiming its standalone AMD duplicate.
    amd_with_exact_identity = {
        amd_index
        for amd_index, amd_step in amd_refs
        if any(
            amd_step.identity_key == nvidia_step.identity_key
            and _exact_commands(amd_step, nvidia_step)
            and _hardware_compatibility(amd_step, nvidia_step) is not None
            for _nvidia_index, nvidia_step in nvidia_refs
        )
    }
    nvidia_with_exact_identity = {
        nvidia_index
        for nvidia_index, nvidia_step in nvidia_refs
        if any(
            amd_step.identity_key == nvidia_step.identity_key
            and _exact_commands(amd_step, nvidia_step)
            and _hardware_compatibility(amd_step, nvidia_step) is not None
            for _amd_index, amd_step in amd_refs
        )
    }
    while True:
        available_amd = unmatched_amd()
        available_nvidia = unmatched_nvidia()
        by_amd: dict[
            int,
            list[tuple[tuple[int, int], int, ConfigStep]],
        ] = {}
        by_nvidia: dict[
            int,
            list[tuple[tuple[int, int], int, ConfigStep]],
        ] = {}
        for amd_index, amd_step in available_amd:
            for nvidia_index, nvidia_step in available_nvidia:
                if (
                    amd_index in amd_with_exact_identity
                    or nvidia_index in nvidia_with_exact_identity
                ):
                    continue
                if amd_step.identity_key == nvidia_step.identity_key:
                    continue
                if not _exact_commands(amd_step, nvidia_step):
                    continue
                if _hardware_compatibility(amd_step, nvidia_step) is None:
                    continue
                title_score = _title_similarity(amd_step, nvidia_step)
                if title_score < COMMAND_TWIN_TITLE_THRESHOLD:
                    continue
                twin_score = (
                    int(_counterpart_labels_match(amd_step, nvidia_step)),
                    round(title_score * 1_000_000),
                )
                by_amd.setdefault(amd_index, []).append(
                    (twin_score, nvidia_index, nvidia_step)
                )
                by_nvidia.setdefault(nvidia_index, []).append(
                    (twin_score, amd_index, amd_step)
                )

        proposals = []
        for amd_index, amd_step in available_amd:
            candidates = by_amd.get(amd_index) or []
            if not candidates:
                continue
            best_score = max(row[0] for row in candidates)
            best = [row for row in candidates if row[0] == best_score]
            if len(best) != 1:
                continue
            _twin_score, nvidia_index, nvidia_step = best[0]
            reverse_candidates = by_nvidia.get(nvidia_index) or []
            reverse_score = max(
                (row[0] for row in reverse_candidates),
                default=(-1, -1),
            )
            reverse_best = [
                row for row in reverse_candidates
                if row[0] == reverse_score
            ]
            if len(reverse_best) != 1 or reverse_best[0][1] != amd_index:
                continue
            proposals.append((
                _step_sort_key(amd_index, amd_step),
                _step_sort_key(nvidia_index, nvidia_step),
                amd_index,
                amd_step,
                nvidia_index,
                nvidia_step,
            ))
        if not proposals:
            break
        for (
            _amd_key,
            _nvidia_key,
            amd_index,
            amd_step,
            nvidia_index,
            nvidia_step,
        ) in sorted(proposals):
            if amd_index in matched_amd or nvidia_index in matched_nvidia:
                continue
            add_match(amd_index, amd_step, nvidia_index, nvidia_step)

    # Finally pair remaining definitions only within the same identity. Solve
    # the whole collision bucket at once: a greedy edge can consume the only
    # hardware-compatible counterpart available to another definition.
    identities = sorted({
        step.identity_key
        for _index, step in [*unmatched_amd(), *unmatched_nvidia()]
    })
    for identity in identities:
        available_amd = [
            ref for ref in unmatched_amd()
            if ref[1].identity_key == identity
        ]
        available_nvidia = [
            ref for ref in unmatched_nvidia()
            if ref[1].identity_key == identity
        ]
        for (
            amd_index,
            amd_step,
            nvidia_index,
            nvidia_step,
        ) in _optimal_identity_pairs(available_amd, available_nvidia):
            add_match(amd_index, amd_step, nvidia_index, nvidia_step)

    amd_only = [
        step for index, step in amd_refs
        if index not in matched_amd
    ]
    nvidia_only = [
        step for index, step in nvidia_refs
        if index not in matched_nvidia
    ]
    matches.sort(key=lambda match: (
        match.command_similarity,
        _yaml_label_key(match.amd_step.label),
        _yaml_label_key(match.nvidia_step.label),
        match.amd_step.source_file,
        match.nvidia_step.source_file,
    ))
    amd_only.sort(key=lambda step: (
        _yaml_label_key(step.label),
        step.identity_key,
        step.source_file,
        step.group,
        tuple(step.commands),
    ))
    nvidia_only.sort(key=lambda step: (
        _yaml_label_key(step.label),
        step.identity_key,
        step.source_file,
        step.group,
        tuple(step.commands),
    ))
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
        labels = {
            *(match.amd_step.member_labels or (match.amd_step.label,)),
            match.nvidia_step.label,
        }
        for label in labels:
            normalized_label = _normalize_job_name(label)
            identities_by_label.setdefault(normalized_label, set()).add(
                canonical
            )
            canonical_by_label[normalized_label] = canonical

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

    logical_amd_steps = _semantic_amd_steps(amd_steps)
    identity_families, family_by_definition_id = _amd_identity_family_keys(
        logical_amd_steps
    )

    def family_key_for_step(step: ConfigStep) -> str:
        for definition_id in (
            step.member_definition_ids
            or (step.definition_id,)
        ):
            family_key = family_by_definition_id.get(definition_id)
            if family_key:
                return family_key
        raise ValueError(
            "logical AMD step is missing an identity-family assignment: "
            f"{step.definition_id or step.label}"
        )

    matches, unmatched_amd, nvidia_only = _match_config_steps(
        amd_steps,
        nvidia_steps,
        mirrors,
    )
    inline_mirror_variants, amd_only = _classify_inline_mirror_variants(
        unmatched_amd,
        nvidia_steps,
        mirrors,
    )
    additional_variants, amd_only = _classify_additional_variants(
        amd_only,
        matches,
    )
    # Compute summary metrics
    raw_amd = len(amd_steps)
    total_amd = len(logical_amd_steps)
    matrix_amd = _matrix_semantic_amd_count(amd_steps)
    total_nvidia = len(nvidia_steps)
    covered = (
        len(matches)
        + len(inline_mirror_variants)
        + len(additional_variants)
    )
    covered_family_keys = {
        family_key_for_step(step)
        for step in (
            *(match.amd_step for match in matches),
            *(variant.amd_step for variant in inline_mirror_variants),
            *(variant.amd_step for variant in additional_variants),
        )
    }
    amd_only_node_family_keys = {
        family_key_for_step(step)
        for step in amd_only
    }
    all_family_keys = set(identity_families)
    amd_only_family_keys = all_family_keys - covered_family_keys
    partially_covered_family_keys = (
        covered_family_keys & amd_only_node_family_keys
    )
    family_coverage_rate = (
        len(covered_family_keys) / len(all_family_keys) * 100
        if all_family_keys
        else 0
    )
    coverage_rate = (
        covered / total_amd * 100
        if total_amd > 0
        else 0
    )
    direct_match_rate = (
        len(matches) / total_amd * 100
        if total_amd > 0
        else 0
    )
    direct_avg_similarity = (
        sum(m.command_similarity for m in matches) / len(matches) * 100
        if matches else 0
    )
    covered_similarities = [
        *(match.command_similarity for match in matches),
        *(
            variant.command_similarity
            for variant in inline_mirror_variants
        ),
        *(
            variant.command_similarity
            for variant in additional_variants
        ),
    ]
    avg_similarity = (
        sum(covered_similarities) / len(covered_similarities) * 100
        if covered_similarities else 0
    )

    source = _source_provenance()
    commit_sha = source.get("commit_sha", "")
    identity_matches = sum(match.match_method == "identity" for match in matches)
    command_twins = sum(match.match_method == "command_twin" for match in matches)
    mirror_variant_kinds = {
        kind: sum(
            variant.relationship == kind
            for variant in inline_mirror_variants
        )
        for kind in (
            "effective_command_duplicate",
            "same_hardware_command_variant",
            "hardware_variant",
        )
    }
    return {
        "generated_at": source.get("fetched_at"),
        "source": source,
        "summary": {
            "total_amd_steps": total_amd,
            "amd_parity_nodes": total_amd,
            "amd_matrix_semantic_rows": matrix_amd,
            "amd_hardware_split_rows": total_amd - matrix_amd,
            "raw_amd_steps": raw_amd,
            "amd_execution_replica_rows": raw_amd - total_amd,
            "amd_matrix_replica_rows": raw_amd - matrix_amd,
            "total_nvidia_steps": total_nvidia,
            "unique_amd_identities": len({
                step.identity_key for step in logical_amd_steps
            }),
            "unique_nvidia_identities": len({
                step.identity_key for step in nvidia_steps
            }),
            "amd_identity_families": len(all_family_keys),
            "covered_identity_families": len(covered_family_keys),
            "amd_only_identity_families": len(amd_only_family_keys),
            "partially_covered_identity_families": len(
                partially_covered_family_keys
            ),
            "identity_family_replica_rows": total_amd - len(all_family_keys),
            "amd_identity_collision_rows": total_amd - len({
                step.identity_key for step in logical_amd_steps
            }),
            "nvidia_identity_collision_rows": total_nvidia - len({
                step.identity_key for step in nvidia_steps
            }),
            "matched": len(matches),
            "direct_matches": len(matches),
            "identity_matches": identity_matches,
            "command_twins": command_twins,
            "inline_mirror_variants": len(inline_mirror_variants),
            "inline_mirror_variant_kinds": mirror_variant_kinds,
            "additional_variants": len(additional_variants),
            "covered": covered,
            "amd_only": len(amd_only),
            "nvidia_only": len(nvidia_only),
            "mirrors": len(mirrors),
            # Preserve the legacy direct-match metrics for downstream readers.
            # Source coverage has separate, explicitly named fields below.
            "match_rate_pct": round(direct_match_rate, 1),
            "coverage_rate_pct": round(coverage_rate, 1),
            "identity_family_coverage_rate_pct": round(
                family_coverage_rate,
                1,
            ),
            "direct_match_rate_pct": round(direct_match_rate, 1),
            "avg_command_similarity_pct": round(
                direct_avg_similarity,
                1,
            ),
            "covered_avg_command_similarity_pct": round(
                avg_similarity,
                1,
            ),
            "direct_avg_command_similarity_pct": round(
                direct_avg_similarity,
                1,
            ),
        },
        "matches": [
            {
                "amd_label": m.amd_step.label,
                "nvidia_label": m.nvidia_step.label,
                "normalized": m.amd_step.normalized_label,
                "identity_key": m.amd_step.identity_key,
                "amd_identity_family_key": family_key_for_step(m.amd_step),
                "command_similarity": round(m.command_similarity, 4),
                "title_similarity": round(m.title_similarity, 4),
                "match_method": m.match_method,
                "color": m.color,
                "amd_group": m.amd_step.group,
                "nvidia_group": m.nvidia_step.group,
                "amd_definition_id": m.amd_step.definition_id,
                "nvidia_definition_id": m.nvidia_step.definition_id,
                "amd_physical_member_count": m.amd_step.physical_member_count,
                "amd_member_definition_ids": list(
                    m.amd_step.member_definition_ids
                    or (m.amd_step.definition_id,)
                ),
                "amd_member_labels": list(
                    m.amd_step.member_labels or (m.amd_step.label,)
                ),
                "amd_member_groups": list(
                    m.amd_step.member_groups or (m.amd_step.group,)
                ),
                "amd_member_agent_pools": list(
                    m.amd_step.member_agent_pools
                    or (m.amd_step.agent_pool,)
                ),
                "amd_source": m.amd_step.source_file,
                "nvidia_source": m.nvidia_step.source_file,
                "amd_source_url": _source_url(m.amd_step.source_file, commit_sha),
                "nvidia_source_url": _source_url(m.nvidia_step.source_file, commit_sha),
                "amd_commands": m.amd_step.commands,
                "nvidia_commands": m.nvidia_step.commands,
            }
            for m in matches
        ],
        "inline_mirror_variants": [
            {
                "amd_label": variant.amd_step.label,
                "nvidia_label": variant.nvidia_step.label,
                "normalized": variant.amd_step.normalized_label,
                "identity_key": variant.amd_step.identity_key,
                "amd_identity_family_key": family_key_for_step(
                    variant.amd_step
                ),
                "command_similarity": round(
                    variant.command_similarity,
                    4,
                ),
                "title_similarity": round(
                    variant.title_similarity,
                    4,
                ),
                "match_method": "inline_mirror_variant",
                "mirror_relationship": variant.relationship,
                "color": variant.color,
                "amd_group": variant.amd_step.group,
                "nvidia_group": variant.nvidia_step.group,
                "amd_definition_id": variant.amd_step.definition_id,
                "nvidia_definition_id": (
                    variant.nvidia_step.definition_id
                ),
                "amd_physical_member_count": (
                    variant.amd_step.physical_member_count
                ),
                "amd_member_definition_ids": list(
                    variant.amd_step.member_definition_ids
                    or (variant.amd_step.definition_id,)
                ),
                "amd_member_labels": list(
                    variant.amd_step.member_labels
                    or (variant.amd_step.label,)
                ),
                "amd_member_groups": list(
                    variant.amd_step.member_groups
                    or (variant.amd_step.group,)
                ),
                "amd_member_agent_pools": list(
                    variant.amd_step.member_agent_pools
                    or (variant.amd_step.agent_pool,)
                ),
                "amd_source": variant.amd_step.source_file,
                "nvidia_source": variant.nvidia_step.source_file,
                "amd_source_url": _source_url(
                    variant.amd_step.source_file,
                    commit_sha,
                ),
                "nvidia_source_url": _source_url(
                    variant.nvidia_step.source_file,
                    commit_sha,
                ),
                "amd_commands": variant.amd_step.commands,
                "nvidia_commands": variant.nvidia_step.commands,
                "inline_mirror_commands_overridden": bool(
                    variant.mirror.get("commands_overridden")
                ),
                "inline_mirror_command_similarity": round(
                    float(
                        variant.mirror.get("command_similarity")
                        or 0
                    ),
                    4,
                ),
                "amd_route_similarity": round(
                    variant.amd_route_similarity,
                    4,
                ),
                "inline_mirror_amd_commands": list(
                    variant.mirror.get("amd_commands")
                    or variant.nvidia_step.commands
                ),
                "inline_mirror_amd_device": str(
                    variant.mirror.get("amd_device")
                    or ""
                ),
            }
            for variant in inline_mirror_variants
        ],
        "additional_variants": [
            {
                "amd_label": variant.amd_step.label,
                "nvidia_label": variant.nvidia_step.label,
                "normalized": variant.amd_step.normalized_label,
                "identity_key": variant.amd_step.identity_key,
                "amd_identity_family_key": family_key_for_step(
                    variant.amd_step
                ),
                "command_similarity": round(
                    variant.command_similarity,
                    4,
                ),
                "title_similarity": round(
                    variant.title_similarity,
                    4,
                ),
                "match_method": "additional_variant",
                "variant_relationship": variant.relationship,
                "color": variant.color,
                "amd_group": variant.amd_step.group,
                "nvidia_group": variant.nvidia_step.group,
                "amd_definition_id": variant.amd_step.definition_id,
                "nvidia_definition_id": (
                    variant.nvidia_step.definition_id
                ),
                "amd_physical_member_count": (
                    variant.amd_step.physical_member_count
                ),
                "amd_member_definition_ids": list(
                    variant.amd_step.member_definition_ids
                    or (variant.amd_step.definition_id,)
                ),
                "amd_member_labels": list(
                    variant.amd_step.member_labels
                    or (variant.amd_step.label,)
                ),
                "amd_member_groups": list(
                    variant.amd_step.member_groups
                    or (variant.amd_step.group,)
                ),
                "amd_member_agent_pools": list(
                    variant.amd_step.member_agent_pools
                    or (variant.amd_step.agent_pool,)
                ),
                "amd_source": variant.amd_step.source_file,
                "nvidia_source": variant.nvidia_step.source_file,
                "amd_source_url": _source_url(
                    variant.amd_step.source_file,
                    commit_sha,
                ),
                "nvidia_source_url": _source_url(
                    variant.nvidia_step.source_file,
                    commit_sha,
                ),
                "amd_commands": variant.amd_step.commands,
                "nvidia_commands": variant.nvidia_step.commands,
            }
            for variant in additional_variants
        ],
        "amd_only": [
            {
                "label": s.label,
                "normalized": s.normalized_label,
                "identity_key": s.identity_key,
                "amd_identity_family_key": family_key_for_step(s),
                "group": s.group,
                "definition_id": s.definition_id,
                "physical_member_count": s.physical_member_count,
                "member_definition_ids": list(
                    s.member_definition_ids or (s.definition_id,)
                ),
                "member_labels": list(s.member_labels or (s.label,)),
                "member_groups": list(s.member_groups or (s.group,)),
                "member_agent_pools": list(
                    s.member_agent_pools or (s.agent_pool,)
                ),
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
                "definition_id": s.definition_id,
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
                "nvidia_definition_id": m.get("nvidia_definition_id", ""),
                "commands_overridden": m["commands_overridden"],
                "command_similarity": round(m["command_similarity"], 4),
                "color": similarity_color(m["command_similarity"]),
                "source_file": m["source_file"],
                "source_url": _source_url(m["source_file"], commit_sha),
                "amd_device": str(m.get("amd_device") or ""),
                "nvidia_commands": list(m.get("nvidia_commands") or []),
                "amd_commands": list(m.get("amd_commands") or []),
            }
            for m in mirrors
        ],
    }
