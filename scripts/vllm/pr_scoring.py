"""PR importance scoring for vLLM ROCm contributions.

vLLM-SPECIFIC module. This scoring system is tailored to vLLM's codebase
structure, CI system (Buildkite), and contribution patterns. Other projects
in the dashboard should implement their own scoring under scripts/<project>/
or skip scoring entirely (the dashboard works without it).

Heuristic scoring (1-10) based on:
- Diff size (additions + deletions, with move/rename detection)
- File type weights (vLLM-specific: kernels > model code > tests > config > docs)
- Complexity signals (commits, review comments, duration)
- Effort: Buildkite build count (vLLM-specific KPI — measures testing iteration)
- Surgical fix boost (small diff on hard files + bugfix keywords)
- PR state (merged > open > draft > closed)
"""

import logging
import re

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# vLLM-specific file type weights
# ---------------------------------------------------------------------------
# These are tuned for vLLM's directory structure. Other projects would need
# their own weight tables (e.g., PyTorch has different dirs for kernels).

VLLM_FILE_WEIGHTS = [
    # Kernel implementations — highest value (CUDA, HIP, Triton kernels)
    (re.compile(r'(kernels?|csrc|cuda|hip|triton.*\.py)/', re.I), 5.0, "kernel"),
    (re.compile(r'\.(cu|hip|cuh)$', re.I), 5.0, "kernel"),
    # Model code — vLLM model executor, layers, attention, quantization
    (re.compile(r'(model_executor|layers|attention|quantization)/', re.I), 4.0, "model"),
    # Engine / scheduler / core runtime
    (re.compile(r'(engine|scheduler|worker|executor|core)/', re.I), 3.5, "engine"),
    # API / entrypoints
    (re.compile(r'(entrypoints|api_server|openai)/', re.I), 3.0, "api"),
    # Tests
    (re.compile(r'tests?/', re.I), 2.0, "test"),
    # CI config (Buildkite pipelines, GitHub Actions)
    (re.compile(r'(\.buildkite|\.github|ci)/', re.I), 2.0, "ci"),
    # Config / packaging
    (re.compile(r'(setup\.|pyproject|requirements|Dockerfile|CMake)', re.I), 1.5, "config"),
    # Docs
    (re.compile(r'\.(md|rst|txt)$', re.I), 1.0, "docs"),
    (re.compile(r'docs?/', re.I), 1.0, "docs"),
]

DEFAULT_FILE_WEIGHT = 2.5


def classify_file(path: str) -> tuple[float, str]:
    """Return (weight, category) for a file path. Uses vLLM-specific weights."""
    for pattern, weight, category in VLLM_FILE_WEIGHTS:
        if pattern.search(path):
            return weight, category
    return DEFAULT_FILE_WEIGHT, "other"


# ---------------------------------------------------------------------------
# PR importance scoring
# ---------------------------------------------------------------------------

def score_pr(pr: dict) -> dict:
    """Compute an importance score (1-10) for a vLLM PR.

    This is a vLLM-specific scorer. The following signals are vLLM-specific:
    - File type weights (VLLM_FILE_WEIGHTS): tuned for vLLM directory layout
    - ci_build_count: Buildkite builds on this PR's branch (vLLM uses Buildkite)

    Args:
        pr: PR dict with GitHub API fields. Optional vLLM-specific fields:
            - ci_build_count: number of Buildkite builds on this branch (vLLM-only)

    Returns:
        Dict with score, breakdown, and category.
    """
    additions = pr.get("additions", 0) or 0
    deletions = pr.get("deletions", 0) or 0
    changed_files = pr.get("changed_files", 0) or 0
    commits = pr.get("commits", 0) or 0
    review_comments = pr.get("review_comments", 0) or 0
    comments = pr.get("comments", 0) or 0
    total_lines = additions + deletions
    merged = pr.get("merged", False)
    draft = pr.get("draft", False)
    state = pr.get("state", "open")
    title = (pr.get("title") or "").lower()

    # vLLM-specific: Buildkite build count on this branch (testing effort proxy)
    ci_builds = pr.get("ci_build_count", 0) or 0

    # --- Component scores (each 0-10) ---

    # 1. Size score: logarithmic, but penalize pure-move PRs
    files = pr.get("files", [])
    rename_count = sum(1 for f in files if f.get("status") == "renamed")
    is_likely_move = (
        changed_files > 5
        and abs(additions - deletions) < total_lines * 0.15
        and rename_count > changed_files * 0.3
    )

    if total_lines == 0:
        size_score = 0.5
    elif is_likely_move:
        size_score = min(3.0, total_lines / 1000 + 1.0)
    elif total_lines <= 10:
        size_score = 1.5
    elif total_lines <= 50:
        size_score = 2.5
    elif total_lines <= 200:
        size_score = 4.0
    elif total_lines <= 500:
        size_score = 5.5
    elif total_lines <= 1000:
        size_score = 7.0
    elif total_lines <= 3000:
        size_score = 8.0
    else:
        size_score = 9.0

    # 2. File type score (vLLM-specific weights)
    if files:
        weighted_sum = 0
        total_file_lines = 0
        categories_touched = set()
        for f in files:
            fname = f.get("filename", "")
            flines = (f.get("additions", 0) or 0) + (f.get("deletions", 0) or 0)
            weight, category = classify_file(fname)
            weighted_sum += weight * max(flines, 1)
            total_file_lines += max(flines, 1)
            categories_touched.add(category)
        file_type_score = (weighted_sum / total_file_lines) * 2 if total_file_lines > 0 else 5.0
        file_type_score = min(file_type_score, 10.0)
    else:
        file_type_score = min(changed_files * 0.5, 5.0) if changed_files > 0 else 2.5
        categories_touched = set()

    # 3. Complexity score (generic — works for any project)
    complexity_signals = 0
    if commits > 5:
        complexity_signals += 2
    elif commits > 2:
        complexity_signals += 1
    if review_comments > 10:
        complexity_signals += 2
    elif review_comments > 3:
        complexity_signals += 1
    if changed_files > 20:
        complexity_signals += 2
    elif changed_files > 8:
        complexity_signals += 1
    if len(categories_touched) > 3:
        complexity_signals += 1
    complexity_score = min(complexity_signals * 1.5 + 1, 10.0)

    # 4. Effort score (vLLM-specific: uses Buildkite build count)
    effort_score = 1.0
    if ci_builds > 20:
        effort_score = 8.0
    elif ci_builds > 10:
        effort_score = 6.0
    elif ci_builds > 5:
        effort_score = 4.0
    elif ci_builds > 2:
        effort_score = 2.5

    # PR duration: long-lived PRs with active commits = sustained effort
    created = pr.get("created_at", "")
    closed = pr.get("merged_at") or pr.get("closed_at") or pr.get("updated_at", "")
    if created and closed:
        try:
            from datetime import datetime
            d1 = datetime.fromisoformat(created.replace("Z", "+00:00"))
            d2 = datetime.fromisoformat(closed.replace("Z", "+00:00"))
            pr_days = (d2 - d1).days
            if pr_days > 14 and commits > 5:
                effort_score = max(effort_score, 6.0)
            elif pr_days > 7 and commits > 3:
                effort_score = max(effort_score, 4.0)
        except (ValueError, TypeError):
            pass

    # 5. Precision/difficulty boost — based on structural signals, NOT title keywords
    #
    # Instead of checking for "bugfix" in the title (which is gameable),
    # we use structural signals that require actual effort:
    #
    # a) Small diff on hard files: if you change <= 20 lines in kernel/model code,
    #    the change is likely surgical. The file_type_score already captures "hardness".
    # b) High review-to-size ratio: many review comments on a small diff means the
    #    change was contentious/difficult to get right — can't be faked.
    # c) Many commits on few lines: iteration on a small fix = debugging effort.
    #
    precision_boost = 0.0

    # Small diff on high-value files (kernel/model code, file_type_score >= 6)
    if total_lines <= 20 and file_type_score >= 6.0:
        precision_boost = 2.0
    elif total_lines <= 50 and file_type_score >= 6.0:
        precision_boost = 1.0

    # High review density: many comments relative to diff size
    if total_lines > 0:
        review_density = review_comments / max(total_lines, 1)
        if review_density > 0.5 and review_comments >= 5:
            # 5+ review comments on < 10 lines = extremely deliberated change
            precision_boost = max(precision_boost, 2.5)
        elif review_density > 0.1 and review_comments >= 3:
            precision_boost = max(precision_boost, 1.5)

    # High commit-to-size ratio: many iterations on small fix = debugging
    if total_lines > 0 and total_lines <= 50:
        commit_density = commits / max(total_lines, 1)
        if commit_density > 0.2 and commits >= 5:
            precision_boost = max(precision_boost, 2.0)

    # 6. State multiplier (generic)
    if merged:
        state_mult = 1.0
    elif state == "open" and not draft:
        state_mult = 0.85
    elif draft:
        state_mult = 0.6
    else:
        state_mult = 0.3

    # --- Final score ---
    raw_score = (
        size_score * 0.20
        + file_type_score * 0.30
        + complexity_score * 0.20
        + effort_score * 0.15
    ) + precision_boost * 0.5
    final_score = round(max(1.0, min(10.0, raw_score * state_mult)), 1)

    # Category label
    if final_score >= 8:
        category = "major"
    elif final_score >= 6:
        category = "significant"
    elif final_score >= 4:
        category = "moderate"
    elif final_score >= 2:
        category = "minor"
    else:
        category = "trivial"

    return {
        "score": final_score,
        "category": category,
        "breakdown": {
            "size": round(size_score, 1),
            "file_type": round(file_type_score, 1),
            "complexity": round(complexity_score, 1),
            "effort": round(effort_score, 1),
            "precision_boost": round(precision_boost, 1),
            "state_multiplier": state_mult,
            "is_likely_move": is_likely_move,
        },
        "stats": {
            "additions": additions,
            "deletions": deletions,
            "changed_files": changed_files,
            "commits": commits,
            "ci_builds": ci_builds,
            "review_comments": review_comments,
            "total_lines": total_lines,
        },
        "categories_touched": sorted(categories_touched) if categories_touched else [],
    }


# ---------------------------------------------------------------------------
# Engineer activity profile
# ---------------------------------------------------------------------------

def compute_engineer_profile(author: str, scored_prs: list[dict]) -> dict:
    """Compute an activity profile for an engineer.

    Args:
        author: GitHub username
        scored_prs: List of scored PR dicts (PR data + 'importance' field from score_pr)

    Returns:
        Engineer profile dict.
    """
    if not scored_prs:
        return {"author": author, "activity_score": 0, "prs": []}

    total_score = sum(p["importance"]["score"] for p in scored_prs)
    merged_prs = [p for p in scored_prs if p.get("merged")]
    open_prs = [p for p in scored_prs if p.get("state") == "open" and not p.get("merged")]
    draft_prs = [p for p in scored_prs if p.get("draft")]

    total_additions = sum(p.get("additions", 0) or 0 for p in scored_prs)
    total_deletions = sum(p.get("deletions", 0) or 0 for p in scored_prs)
    total_commits = sum(p.get("commits", 0) or 0 for p in scored_prs)

    all_categories = set()
    for p in scored_prs:
        all_categories.update(p["importance"].get("categories_touched", []))

    avg_importance = round(total_score / len(scored_prs), 1) if scored_prs else 0

    # Composite activity score: balances volume and quality
    # Uses log2(n+1) for volume so 27 trivial PRs can't dominate 2 major ones
    # Score = avg_importance * log2(num_prs + 1) * merge_bonus
    import math
    merge_ratio = len(merged_prs) / len(scored_prs) if scored_prs else 0
    merge_bonus = 0.7 + 0.3 * merge_ratio  # 0.7 base, up to 1.0 with all merged
    volume_factor = math.log2(len(scored_prs) + 1)  # log2(28)=4.8, log2(3)=1.6, log2(6)=2.6
    activity_score = round(avg_importance * volume_factor * merge_bonus, 1)

    return {
        "author": author,
        "activity_score": activity_score,
        "avg_importance": avg_importance,
        "total_prs": len(scored_prs),
        "merged": len(merged_prs),
        "open": len(open_prs),
        "draft": len(draft_prs),
        "total_additions": total_additions,
        "total_deletions": total_deletions,
        "total_commits": total_commits,
        "categories_touched": sorted(all_categories),
        "top_prs": sorted(
            [{"number": p["number"], "title": p.get("title", ""), "score": p["importance"]["score"],
              "category": p["importance"]["category"]}
             for p in scored_prs],
            key=lambda x: x["score"], reverse=True
        )[:10],
    }
