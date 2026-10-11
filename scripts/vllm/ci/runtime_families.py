"""Commit-pinned AMD source families, separate from observed physical routing."""

from functools import lru_cache
from copy import deepcopy
import importlib
import json
import re
from typing import Any

from vllm.ci import analyzer
from vllm.main_ci_definitions import (
    MainCISnapshot, _runtime_definition_files, amd_source_steps,
    validate_runtime_scope_index,
)
from vllm.pipelines import SKIP_JOB_PATTERNS, _job_queue, is_amd_ci_job

IDENTITY_BASIS = "commit_pinned_amd_definition_family"
_SHA = re.compile(r"[0-9a-f]{40}")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
_CLAIMS = ("source_definition_id", "source_agent_pool", "source_commit",
           "source_step_key", "source_binding_basis", "source_family_key")
_DEFINITION_FIELDS = {"definition_id", "label", "agent_pool", "family_key", "source_label", "step_key"}


def _label(name: str, bases: list[str]) -> str:
    return analyzer._strip_known_shard_index(analyzer._normalize_job_name(name), bases)


def validate_family_catalog(catalog: Any, *, expected_commit: str,
                            expected_tree: str | None = None) -> dict:
    """Validate a catalog carried by authenticated immutable source evidence."""
    if (not isinstance(catalog, dict)
            or set(catalog) != {"version", "commit_sha", "definition_tree_sha", "shard_bases", "definitions"}
            or type(catalog.get("version")) is not int or catalog["version"] != 1
            or not _SHA.fullmatch(expected_commit) or catalog.get("commit_sha") != expected_commit
            or not _SHA.fullmatch(str(catalog.get("definition_tree_sha") or ""))
            or (expected_tree is not None and catalog["definition_tree_sha"] != expected_tree)):
        raise ValueError("AMD source family catalog does not match its immutable commit/tree")
    bases, rows = catalog["shard_bases"], catalog["definitions"]
    if (not isinstance(bases, list) or any(not isinstance(base, str) or not base or len(base) > 4096 for base in bases)
            or bases != sorted(set(bases)) or not isinstance(rows, list) or not rows or len(rows) > 2048
            or len(json.dumps(catalog, ensure_ascii=True, allow_nan=False).encode()) > 1024 * 1024):
        raise ValueError("AMD source family catalog exceeds its canonical bounds")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != _DEFINITION_FIELDS
                or any(not isinstance(value, str) or len(value) > 4096 for value in row.values())
                or not row["definition_id"].startswith(".buildkite/") or "#" not in row["definition_id"]
                or not row["definition_id"].rsplit("#", 1)[-1] or row["definition_id"] in seen
                or not row["label"] or row["label"] != _label(row["source_label"], bases)
                or not re.fullmatch(r"mi\d+b?(?:_[a-z0-9_-]+)?", row["agent_pool"])
                or not row["family_key"] or row["family_key"] != row["family_key"].strip().casefold()
                or (row["step_key"] and row["definition_id"].rsplit("#", 1)[-1] != row["step_key"])):
            raise ValueError("AMD source family definition is malformed or contradictory")
        seen.add(row["definition_id"])
    if rows != sorted(rows, key=lambda row: row["definition_id"]):
        raise ValueError("AMD source family definitions must have canonical order")
    return catalog


def family_catalog_from_snapshot(snapshot: MainCISnapshot) -> dict:
    """Derive the same configuration families used by the nightly health analyzer."""
    config_parity = importlib.import_module("vllm.config_parity")

    raw = amd_source_steps(snapshot, require_keys=False)
    steps = config_parity._parse_amd_data({"steps": raw})
    _, families = config_parity._amd_identity_family_keys(config_parity._semantic_amd_steps(steps))
    bases = sorted({analyzer._normalize_job_name(step.label.replace("%N", "").strip())
                    for step in steps if step.parallelism and step.parallelism > 1 and "%N" in step.label})
    rows = [{"definition_id": step.definition_id, "label": _label(step.label, bases),
             "agent_pool": step.agent_pool.casefold(), "family_key": families[step.definition_id],
             "source_label": step.label, "step_key": str(item.get("key") or "")}
            for step, item in zip(steps, raw)]
    return validate_family_catalog({"version": 1, "commit_sha": snapshot.commit_sha,
                                   "definition_tree_sha": snapshot.definition_tree_sha,
                                   "shard_bases": bases, "definitions": sorted(rows, key=lambda row: row["definition_id"])},
                                  expected_commit=snapshot.commit_sha)


@lru_cache(maxsize=64)
def _tree_catalog(tree: str, commit: str) -> dict:
    # The tree identity comes from an already authenticated exact-pin CPU index;
    # every tree/blob read still undergoes the existing Git-object verification.
    return family_catalog_from_snapshot(MainCISnapshot(commit, _runtime_definition_files(tree), "", tree))


def family_catalog_from_scope_index(index: dict, *, expected_commit: str) -> dict:
    index = validate_runtime_scope_index(index, expected_commit=expected_commit)
    return deepcopy(_tree_catalog(index["definition_tree_sha"], expected_commit))


def _definition_tuple(row: dict) -> tuple[str, str, str, str]:
    return row["label"], row["agent_pool"], row["family_key"], row["source_label"]


def _declared_name(name: str, bases: list[str]) -> str:
    """Normalize shards against this source, retaining every route discriminator."""
    _, platform, hardware, native_pool = analyzer._amd_declared_label_signature(name)
    label = _label(name, bases)
    if not platform:
        return label
    return f":{platform}: ({hardware}) {label}" + (f" ({native_pool})" if native_pool else "")


def catalog_definitions_from_report(report: dict) -> tuple[str, dict[str, tuple[str, str, str, str]]]:
    """Recover health's full pinned definition tuples without collector imports."""
    config_parity = importlib.import_module("vllm.config_parity")
    commit, routes = config_parity.extract_amd_runtime_group_key_map_from_report(report)
    if not _SHA.fullmatch(commit):
        raise ValueError("AMD definition catalog requires an exact source commit")
    definitions = {}
    for raw in report.get("amd_execution_definitions", []):
        identity = raw.get("definition_id")
        label = analyzer._normalize_job_name(str(raw.get("label") or "")).strip()
        pool = str(raw.get("agent_pool") or "").strip().casefold()
        family = routes.get((label, pool))
        if not isinstance(identity, str) or "#" not in identity or not identity.rsplit("#", 1)[-1] or not family:
            raise ValueError("AMD execution definition lacks a pinned family identity")
        if identity in definitions:
            raise ValueError("AMD execution definition identities are duplicated")
        definitions[identity] = (label, pool, family, str(raw.get("label") or ""))
    return commit, definitions


def bind_source_family(job: dict, catalog: dict) -> dict:
    """Bind one exact attempt by source key or its unique declared AMD route."""
    name = str(job.get("raw_name") or job.get("name") or "")
    identity = job.get("job_id") or job.get("id")
    if not isinstance(identity, str) or not _UUID.fullmatch(identity):
        raise ValueError("AMD source family requires an exact runtime job UUID")
    step = job.get("step") or {}
    if not isinstance(step, dict):
        raise ValueError("AMD runtime step identity is malformed")
    if job.get("step_id") and step.get("id") and job["step_id"] != step["id"]:
        raise ValueError("AMD runtime step UUID evidence is contradictory")
    keys = ([job["step_key"]] if "step_key" in job else []) + ([step["key"]] if "key" in step else [])
    if any(not isinstance(key, str) or not key or key != key.strip() for key in keys) or len(set(keys)) > 1:
        raise ValueError("AMD runtime source keys are malformed or disagree")
    bases = catalog["shard_bases"]
    label = _label(name, bases)
    declared_name = _declared_name(name, bases)
    definitions = catalog["definitions"]
    if keys:
        candidates = [row for row in definitions if row["step_key"] == keys[0] and row["label"] == label]
        if len(candidates) != 1:
            raise ValueError("AMD runtime key lacks an unambiguous pinned source family")
        candidate = candidates[0]
        if analyzer._amd_declared_label_signature(name)[1] and not analyzer._amd_declared_label_matches(declared_name, _definition_tuple(candidate)):
            raise ValueError("AMD runtime source key contradicts its original declaration")
        basis, key = "runtime_step_key", keys[0]
    else:
        candidates = [row for row in definitions if row["label"] == label
                      and analyzer._amd_declared_route_matches(declared_name, _definition_tuple(row), _job_queue(job))]
        step_id = job.get("step_id") or step.get("id")
        if len(candidates) != 1 or not isinstance(step_id, str) or not _UUID.fullmatch(step_id):
            raise ValueError("AMD runtime declaration lacks an unambiguous pinned source family")
        candidate, basis, key = candidates[0], "pinned_declared_label", ""
    return {"source_definition_id": candidate["definition_id"], "source_agent_pool": candidate["agent_pool"],
            "source_commit": catalog["commit_sha"], "source_step_key": key,
            "source_binding_basis": basis, "source_family_key": candidate["family_key"]}


def annotate_build_source_families(build: dict, catalog: dict) -> dict:
    """Re-derive claims before retaining them; contradictory restored claims fail."""
    commit = str(build.get("commit") or "").casefold()
    tree = build.get("source_definition_tree_sha")
    if not isinstance(tree, str) or not _SHA.fullmatch(tree):
        raise ValueError("AMD source families require an authenticated definition tree")
    catalog = validate_family_catalog(catalog, expected_commit=commit,
                                      expected_tree=tree)
    jobs = []
    for job in build.get("jobs") or []:
        raw_name = str(job.get("raw_name") or job.get("name") or "")
        if not is_amd_ci_job(job) or any(skip in raw_name.lower() for skip in SKIP_JOB_PATTERNS):
            jobs.append({key: value for key, value in job.items() if key not in _CLAIMS})
            continue
        proof = bind_source_family(job, catalog)
        if any(key in job and job[key] != value for key, value in proof.items()):
            raise ValueError("Restored AMD source family differs from its exact runtime attempt")
        jobs.append({**job, **proof})
    result = {**build, "source_family_catalog": catalog, "jobs": jobs}
    validate_build_source_families(result)
    return result


def validate_build_source_families(build: dict) -> dict[str, str]:
    """Independently reconstruct each retained exact attempt's family claim."""
    commit = str(build.get("commit") or "").casefold()
    tree = build.get("source_definition_tree_sha")
    if not isinstance(tree, str) or not _SHA.fullmatch(tree):
        raise ValueError("AMD source families require an authenticated definition tree")
    catalog = validate_family_catalog(build.get("source_family_catalog"), expected_commit=commit,
                                      expected_tree=tree)
    result = {}
    for job in build.get("jobs") or []:
        raw_name = str(job.get("raw_name") or job.get("name") or "")
        if not is_amd_ci_job(job) or any(skip in raw_name.lower() for skip in SKIP_JOB_PATTERNS):
            continue
        identity = job.get("job_id") or job.get("id")
        proof = bind_source_family(job, catalog)
        if identity in result or any(job.get(key) != value for key, value in proof.items()):
            raise ValueError("AMD source family claim does not match its pinned exact attempt")
        result[identity] = proof["source_family_key"]
    return result
