#!/usr/bin/env python3
"""Build a reviewable AMD gating target candidate audit.

This collector intentionally does not rewrite the canonical target list. It
scans the latest upstream nightly signal, folds obvious hardware
duplicates, compares the result with the reviewed target list, and writes a
daily artifact that humans can use to keep the canonical list fresh.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT = ROOT / "data" / "vllm" / "ci"
TARGETS = OUTPUT / "gating_targets.json"
NIGHTLIES = OUTPUT / "gating_nightlies.json"
PROPOSALS = OUTPUT / "gating_proposals.json"

MULTISPACE_RE = re.compile(r"\s+")
AMD_PREFIX_RE = re.compile(r"^AMD:\s*", re.IGNORECASE)
INTERNAL_AMD_PREFIX_RE = re.compile(r"^mi\d{3,4}b?_\d+:\s*", re.IGNORECASE)
AMD_DEVICE_SUFFIX_RE = re.compile(r"\s*\((mi\d{3,4}b?_\d+)\)\s*$", re.IGNORECASE)
SHARD_TEMPLATE_SUFFIX_RE = re.compile(r"\s*%N\s*$", re.IGNORECASE)
GPU_QUEUE_RE = re.compile(r"(^|[^a-z0-9])gpu([_-]|$)|\bgpus?\b|h100|h200|a100|b200|gh200|mithril", re.IGNORECASE)
CPU_OR_NON_GPU_RE = re.compile(
    r"(^|[^a-z0-9])(arm-cpu|small_cpu|medium_cpu|intel-cpu|intel|cpu|xpu|hpu|npu|ascend)(?=$|[^a-z0-9])",
    re.IGNORECASE,
)

EXCLUSION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cpu_or_non_gpu", CPU_OR_NON_GPU_RE),
    ("temporary", re.compile(r"\btemporary\b", re.IGNORECASE)),
    ("experimental_model_runner_v2", re.compile(r"\bmodel runner v2\b", re.IGNORECASE)),
    ("rust_frontend_long_term", re.compile(r"\brust frontend\b", re.IGNORECASE)),
    ("moe_refactor_temporary", re.compile(r"\bmoe refactor integration test\b", re.IGNORECASE)),
    ("docker_metadata_review", re.compile(r"\bdocker build metadata\b", re.IGNORECASE)),
)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def clean_text(value: Any) -> str:
    return MULTISPACE_RE.sub(" ", str(value or "").strip()).strip()


def clean_job_label(value: Any) -> str:
    text = clean_text(value)
    text = AMD_PREFIX_RE.sub("", text)
    text = INTERNAL_AMD_PREFIX_RE.sub("", text)
    text = AMD_DEVICE_SUFFIX_RE.sub("", text)
    return clean_text(text.replace(r"\%N", "%N"))


def hardware_fold_key(value: Any) -> str:
    text = clean_job_label(value).lower()
    text = text.replace("%n", "%N")
    text = re.sub(r"\s+nightly\s+b200\b", "", text)
    text = re.sub(r"\((\d+)x(?:h100|h200|a100|b200|gh200)(?:\s*-\s*\d+xmi\d{3,4}b?)?\)", r"(\1 gpus)", text)
    text = re.sub(r"\((\d+)\s*(?:h100s?|h200s?|a100s?|b200s?|gh200s?)\)", r"(\1 gpus)", text)
    text = re.sub(r"\((\d+)\s*gpus?\)\s*\((?:h100|h200|a100|b200|gh200|mi\d{3,4}b?)\)", r"(\1 gpus)", text)
    text = re.sub(r"\((?:h100|h200|a100|b200|gh200|cuda)\s*-\s*mi\d{3,4}b?\)", "", text)
    text = re.sub(r"\((?:h100|h200|a100|b200|gh200|cuda|mi\d{3,4}b?)\)", "", text)
    text = re.sub(r"\btests\b", "test", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\(\s+", "(", text)
    return text.strip()


def is_amd_mirror_job(job: dict[str, Any]) -> bool:
    raw = str(job.get("raw_name") or job.get("name") or "")
    return bool(AMD_PREFIX_RE.match(raw))


def is_gpu_like_job(job: dict[str, Any]) -> bool:
    raw = clean_job_label(job.get("raw_name") or job.get("name") or "")
    queue = str(job.get("q") or "")
    haystack = f"{raw} {queue}"
    if CPU_OR_NON_GPU_RE.search(haystack):
        return False
    return bool(GPU_QUEUE_RE.search(haystack))


def exclusion_reasons(label: str, queue: str = "") -> list[str]:
    haystack = f"{label} {queue}"
    return [name for name, pattern in EXCLUSION_RULES if pattern.search(haystack)]


def latest_build(payload: dict[str, Any], slug: str) -> dict[str, Any] | None:
    builds = payload.get(slug, {}).get("builds") or []
    return builds[0] if builds else None


def build_job_url(build: dict[str, Any] | None, job: dict[str, Any]) -> str:
    base = str((build or {}).get("web_url") or "").rstrip("/")
    if not base:
        return str(job.get("url") or job.get("web_url") or "")
    if job.get("job_id"):
        return f"{base}/steps/canvas?jid={job['job_id']}&tab=output"
    if job.get("step_id"):
        return f"{base}/steps/canvas?sid={job['step_id']}&tab=output"
    if job.get("url") or job.get("web_url"):
        return str(job.get("url") or job.get("web_url"))
    return base


def canonical_index(targets: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for target in targets.get("groups") or []:
        if not isinstance(target, dict):
            continue
        rows[hardware_fold_key(target.get("label"))].append(target)
    return rows


def shard_template_index(
    targets: dict[str, Any],
) -> list[tuple[re.Pattern[str], str, list[dict[str, Any]]]]:
    """Compile only explicitly reviewed ``%N`` targets into shard matchers."""
    canonical = canonical_index(targets)
    templates = []
    for target_key, target_rows in canonical.items():
        label = clean_job_label(target_rows[0].get("label"))
        if not SHARD_TEMPLATE_SUFFIX_RE.search(label):
            continue
        base_key = hardware_fold_key(SHARD_TEMPLATE_SUFFIX_RE.sub("", label))
        templates.append((
            re.compile(rf"{re.escape(base_key)}\s+(?P<shard>\d+)", re.IGNORECASE),
            target_key,
            target_rows,
        ))
    return templates


def canonical_match(
    runtime_key: str,
    canonical: dict[str, list[dict[str, Any]]],
    shard_templates: list[tuple[re.Pattern[str], str, list[dict[str, Any]]]],
) -> tuple[str, list[dict[str, Any]], int | None]:
    """Prefer exact targets, then match numbered shards of explicit templates."""
    exact = canonical.get(runtime_key, [])
    if exact:
        return runtime_key, exact, None
    for pattern, target_key, target_rows in shard_templates:
        match = pattern.fullmatch(runtime_key)
        if match:
            return target_key, target_rows, int(match.group("shard"))
    return runtime_key, [], None


def proposal_index(proposals: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pr in proposals.get("pull_requests") or []:
        if not isinstance(pr, dict):
            continue
        for mirror in pr.get("new_mirrors") or []:
            if not isinstance(mirror, dict):
                continue
            label = mirror.get("label") or ""
            rows[hardware_fold_key(label)].append({
                "pr": pr.get("number"),
                "url": pr.get("url") or "",
                "title": pr.get("title") or "",
                "author": pr.get("author") or "",
                "label": label,
                "device": mirror.get("device") or "",
                "yaml_file": mirror.get("yaml_file") or "",
            })
    return rows


def internal_status_for_key(key: str, build: dict[str, Any] | None) -> dict[str, Any]:
    if not build:
        return {"state": "not_observed", "jobs": []}
    matches: list[dict[str, Any]] = []
    for job in build.get("jobs") or []:
        if hardware_fold_key(job.get("raw_name") or job.get("name")) == key:
            matches.append({
                "label": clean_job_label(job.get("raw_name") or job.get("name")),
                "state": str(job.get("state") or ""),
                "queue": job.get("q") or "",
                "url": build_job_url(build, job),
            })
    if not matches:
        return {"state": "not_observed", "jobs": []}
    states = {row["state"].lower() for row in matches}
    if states and states <= {"passed"}:
        state = "passed"
    elif states & {"failed", "timed_out", "broken", "error"}:
        state = "failed"
    elif states & {"soft_fail", "soft_failed"}:
        state = "soft_fail"
    elif states & {"running", "scheduled", "assigned"}:
        state = "running"
    else:
        state = sorted(states)[0] if states else "unknown"
    return {"state": state, "jobs": matches}


def infer_pf_signal(key: str, label: str, internal_build: dict[str, Any] | None) -> str:
    if re.search(r"\b2 node\b", label, re.IGNORECASE):
        return "purple"
    state = internal_status_for_key(key, internal_build)["state"]
    if state == "passed":
        return "green"
    if state in {"failed", "soft_fail"}:
        return "red"
    return "yellow"


def _audit_state_rank(state: Any) -> int:
    normalized = str(state or "").strip().lower()
    if normalized in {"failed", "timed_out", "broken", "error"}:
        return 4
    if normalized in {"soft_fail", "soft_failed"}:
        return 3
    if normalized in {"running", "scheduled", "assigned"}:
        return 2
    if normalized == "passed":
        return 1
    return 0


def _merge_runtime_shards(existing: dict[str, Any], candidate: dict[str, Any]) -> None:
    """Merge expanded jobs into one canonical target row, incident first."""
    by_label: dict[str, dict[str, Any]] = {}
    for shard in (existing.get("runtime_shards") or []) + (candidate.get("runtime_shards") or []):
        label_key = clean_text(shard.get("label")).casefold()
        previous = by_label.get(label_key)
        if previous is None or _audit_state_rank(shard.get("state")) > _audit_state_rank(previous.get("state")):
            by_label[label_key] = shard
    shards = sorted(
        by_label.values(),
        key=lambda row: (
            row.get("shard_index") is None,
            int(row.get("shard_index") or 0),
            clean_text(row.get("label")).casefold(),
        ),
    )
    representative = sorted(
        shards,
        key=lambda row: (
            -_audit_state_rank(row.get("state")),
            clean_text(row.get("label")).casefold(),
        ),
    )[0]
    existing["runtime_shards"] = shards
    existing["shard_count"] = len(shards)
    existing["shard_states"] = dict(sorted(Counter(
        str(row.get("state") or "unknown") for row in shards
    ).items()))
    for key in ("state", "queue", "url"):
        existing[key] = representative.get(key)


def collect_upstream_candidates(
    upstream_build: dict[str, Any] | None,
    internal_build: dict[str, Any] | None,
    targets: dict[str, Any],
    proposals: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    canonical = canonical_index(targets)
    shard_templates = shard_template_index(targets)
    proposed = proposal_index(proposals)
    candidate_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []

    for job in (upstream_build or {}).get("jobs") or []:
        raw = job.get("raw_name") or job.get("name") or ""
        label = clean_job_label(raw)
        if not label or is_amd_mirror_job(job):
            continue
        queue = str(job.get("q") or "")
        runtime_key = hardware_fold_key(label)
        key, target_matches, shard_index = canonical_match(
            runtime_key,
            canonical,
            shard_templates,
        )
        reasons = exclusion_reasons(label, queue)
        if not is_gpu_like_job(job) and not reasons:
            reasons.append("not_gpu_like")
        row = {
            "label": label,
            "canonical_key": key,
            "state": job.get("state") or "",
            "queue": queue,
            "url": build_job_url(upstream_build, job),
            "exclusion_reasons": reasons,
        }
        if reasons:
            row["decision"] = "excluded"
            excluded_rows.append(row)
            continue

        proposal_matches = proposed.get(key, []) or proposed.get(runtime_key, [])
        exact_match = (
            target_matches[0]
            if shard_index is not None
            else next(
                (
                    target
                    for target in target_matches
                    if clean_text(target.get("label")) == label
                ),
                None,
            )
        )
        if exact_match:
            row.update({
                "decision": "canonical",
                "target_id": exact_match.get("id"),
                "canonical_label": exact_match.get("label"),
                "source_signal": exact_match.get("gating_signal") or exact_match.get("source_signal") or "unknown",
                "readiness_signal": exact_match.get("pf_signal") or exact_match.get("readiness_signal") or "unknown",
                "target_signal": exact_match.get("assigned_signal") or exact_match.get("target_signal") or "unknown",
                "proposal_matches": proposal_matches,
            })
            if shard_index is not None:
                row["label"] = exact_match.get("label")
                row["shard_count"] = 1
                row["shard_states"] = {
                    str(job.get("state") or "unknown"): 1,
                }
                row["runtime_shards"] = [{
                    "label": label,
                    "shard_index": shard_index,
                    "state": job.get("state") or "",
                    "queue": queue,
                    "url": build_job_url(upstream_build, job),
                }]
                storage_key = ("target", str(exact_match.get("id")))
                existing = candidate_by_key.get(storage_key)
                if existing is not None and existing.get("target_id") == row.get("target_id"):
                    _merge_runtime_shards(existing, row)
                    continue
            candidate_by_key[(
                "target",
                str(exact_match.get("id")),
            )] = row
            continue
        if target_matches:
            target = target_matches[0]
            row.update({
                "decision": "likely_duplicate",
                "duplicate_of": target.get("label"),
                "target_id": target.get("id"),
                "proposal_matches": proposal_matches,
            })
            duplicate_rows.append(row)
            continue
        row.update({
            "decision": "new_candidate",
            "source_signal": "red",
            "readiness_signal": infer_pf_signal(key, label, internal_build),
            "target_signal": "unknown",
            "internal_signal": internal_status_for_key(key, internal_build),
            "proposal_matches": proposal_matches,
        })
        candidate_by_key.setdefault(("candidate", key), row)

    candidate_rows = sorted(candidate_by_key.values(), key=lambda row: (row["decision"], row["label"].lower()))
    observed_target_ids = {
        str(row.get("target_id"))
        for row in [*candidate_rows, *duplicate_rows]
        if row.get("target_id") is not None
    }
    missing = []
    for target in targets.get("groups") or []:
        key = hardware_fold_key(target.get("label"))
        if str(target.get("id")) in observed_target_ids:
            continue
        missing.append({
            "decision": "missing_from_upstream",
            "target_id": target.get("id"),
            "label": target.get("label") or "",
            "canonical_key": key,
            "source_signal": target.get("gating_signal") or target.get("source_signal") or "unknown",
            "readiness_signal": target.get("pf_signal") or target.get("readiness_signal") or "unknown",
            "target_signal": target.get("assigned_signal") or target.get("target_signal") or "unknown",
        })

    rows = candidate_rows + sorted(duplicate_rows, key=lambda row: row["label"].lower()) + sorted(excluded_rows, key=lambda row: row["label"].lower()) + missing
    counts = Counter(row["decision"] for row in rows)
    summary = {
        "upstream_build": (upstream_build or {}).get("number"),
        "amd_ci_build": (internal_build or {}).get("number"),
        "canonical_target_count": len(targets.get("groups") or []),
        "row_count": len(rows),
        "canonical_match_count": counts.get("canonical", 0),
        "likely_duplicate_count": counts.get("likely_duplicate", 0),
        "new_candidate_count": counts.get("new_candidate", 0),
        "excluded_count": counts.get("excluded", 0),
        "missing_from_upstream_count": counts.get("missing_from_upstream", 0),
        "by_decision": dict(sorted(counts.items())),
    }
    return rows, summary


def build_payload(
    targets: dict[str, Any],
    nightlies: dict[str, Any],
    proposals: dict[str, Any],
) -> dict[str, Any]:
    upstream_build = latest_build(nightlies, "ci")
    internal_build = latest_build(nightlies, "amd-ci")
    rows, summary = collect_upstream_candidates(upstream_build, internal_build, targets, proposals)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "description": "Heuristic daily audit for maintaining the reviewed AMD gating target list.",
            "canonical_targets": "data/vllm/ci/gating_targets.json",
            "nightly_signal": "data/vllm/ci/gating_nightlies.json",
            "proposal_signal": "data/vllm/ci/gating_proposals.json",
        },
        "heuristics": {
            "duplicate_key": "Fold hardware-only suffixes while preserving GPU counts.",
            "parallel_templates": (
                "Only explicit trailing %N targets match numbered runtime shards; "
                "other numeric suffixes remain distinct."
            ),
            "excluded": [name for name, _pattern in EXCLUSION_RULES] + ["not_gpu_like"],
            "safety": "This artifact is review-only and does not rewrite the canonical target config.",
        },
        "summary": summary,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect AMD gating target candidates")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--targets", type=Path, default=TARGETS)
    parser.add_argument("--nightlies", type=Path, default=NIGHTLIES)
    parser.add_argument("--proposals", type=Path, default=PROPOSALS)
    args = parser.parse_args()

    payload = build_payload(
        load_json(args.targets, {"groups": []}),
        load_json(args.nightlies, {}),
        load_json(args.proposals, {}),
    )
    args.output.mkdir(parents=True, exist_ok=True)
    out_path = args.output / "gating_target_candidates.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(
        "Wrote "
        f"{out_path} with {payload['summary']['new_candidate_count']} new candidates, "
        f"{payload['summary']['likely_duplicate_count']} likely duplicates, "
        f"{payload['summary']['missing_from_upstream_count']} missing canonical targets"
    )


if __name__ == "__main__":
    main()
