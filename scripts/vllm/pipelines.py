# cspell:ignore abhl
"""vLLM-specific Buildkite pipeline definitions.

This file contains the pipeline configurations specific to vLLM.
To add a new project, create a similar file under scripts/<project>/pipelines.py
with its own PIPELINES dict.
"""

import re

from vllm.constants import amd_gpu_hardware

# vLLM Buildkite build names to monitor.
#
# Keep the AMD pattern exact: "AMD Full CI Run - TheRock nightly" is a
# separate nightly stream and should not drive the dashboard's normal AMD
# health, analytics, or matrix views.
AMD_NIGHTLY_NAME_PATTERN = r"^AMD Full CI Run\s*-\s*nightly(?:\s|$)"
UPSTREAM_NIGHTLY_NAME_PATTERN = r"^Full CI run\s*-\s*nightly(?:\s|$)"

# Upstream's scheduled gating builds use two distinct messages. Keep this
# broader classifier separate from ``UPSTREAM_NIGHTLY_NAME_PATTERN`` so daily
# builds can be identified without becoming canonical nightlies elsewhere.
UPSTREAM_SCHEDULED_GATING_NAME_PATTERN = r"^Full CI run\s*-\s*(nightly|daily)(?:\s|$)"
SCHEDULED_GATING_KINDS = frozenset({"nightly", "daily"})


def upstream_scheduled_gating_kind(message: object) -> str | None:
    """Return the exact upstream scheduled gating kind, if present."""
    if not isinstance(message, str):
        return None
    match = re.search(
        UPSTREAM_SCHEDULED_GATING_NAME_PATTERN,
        message,
        flags=re.IGNORECASE,
    )
    return match.group(1).lower() if match else None


NIGHTLY_NAME_PATTERNS_BY_SLUG = {
    "amd-ci": AMD_NIGHTLY_NAME_PATTERN,
    "ci": UPSTREAM_NIGHTLY_NAME_PATTERN,
}

# vLLM Buildkite pipelines to monitor
PIPELINES = {
    "amd": {
        "slug": "ci",
        "name_pattern": UPSTREAM_NIGHTLY_NAME_PATTERN,
        "branch": "main",
        "display_name": "AMD main CI",
        "job_scope": "amd_gpu",
    },
    "upstream": {
        "slug": "ci",
        "name_pattern": UPSTREAM_NIGHTLY_NAME_PATTERN,
        "branch": "main",
        "display_name": "Upstream Nightly",
        "job_scope": "cuda_gpu",
    },
}


def _job_queue(job: dict) -> str:
    """Prefer observed agent routing over the display label and request rules."""
    explicit = job.get("agent_queue") or job.get("queue") or job.get("q")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    agent = job.get("agent") or {}
    metadata = agent.get("meta_data") if isinstance(agent, dict) else None
    if isinstance(metadata, dict):
        queue = metadata.get("queue")
        if isinstance(queue, str) and queue.strip():
            return queue.strip()
    for values in (metadata, job.get("agent_query_rules")):
        for value in values if isinstance(values, list) else []:
            if isinstance(value, str) and value.casefold().startswith("queue="):
                return value.split("=", 1)[1].strip()
    return ""


def _job_hardware_scope(job: dict) -> str:
    if not isinstance(job, dict) or job.get("type", "script") != "script":
        return ""
    queue = _job_queue(job).casefold()
    if queue:
        if amd_gpu_hardware(queue):
            return "amd_gpu"
        if "cpu" in queue or queue.startswith(("amd", "intel", "arm", "ascend")):
            return ""
        if queue.startswith(("gpu_", "nvidia", "mithril-h", "nebius-h")) or re.fullmatch(
            r"(?:[abhl]\d+|gh\d+|dgx-spark)(?:[_-].*)?", queue
        ):
            return "cuda_gpu"
    name = str(job.get("raw_name") or job.get("name") or job.get("job_name") or "")
    if re.search(r":computer:\s*\(\s*cpu\b", name, flags=re.I):
        return ""
    if re.match(r"(?:amd_)?mi\d+b?(?:_[a-z0-9_-]+)?:", name, flags=re.I):
        return "amd_gpu"
    if re.match(
        r"(?:gpu_[a-z0-9_-]+|[abhl]\d+(?:_[a-z0-9_-]+)?|gh\d+(?:_[a-z0-9_-]+)?):", name, flags=re.I
    ):
        return "cuda_gpu"
    if re.search(r":amd:\s*\(\s*mi\d+", name, flags=re.I):
        return "amd_gpu"
    if re.search(r":nvidia:\s*\(", name, flags=re.I):
        return "cuda_gpu"
    hardware = str(job.get("hardware") or job.get("device") or "").casefold()
    if "cpu" in re.split(r"[_ -]+", hardware):
        return ""
    if re.fullmatch(r"mi\d+b?(?:[_ -][a-z0-9_-]+)?", hardware):
        return "amd_gpu"
    # BuildSummary preserves architecture decorators, including MIG profiles,
    # DGX Spark, and explicit GPU counts such as ``4xB200``.
    if hardware in {"h200 mig 18gb", "h200 mig 35gb", "dgx", "dgx-spark"} or re.fullmatch(
        r"(?:[1-9]\d*x)?(?:[abhl]\d+|gh\d+)(?:[_ -][a-z0-9_-]+)?", hardware
    ):
        return "cuda_gpu"
    return ""


def is_amd_ci_job(job: dict) -> bool:
    """Select AMD GPU test jobs from the authoritative upstream ``ci`` roster."""
    return _job_hardware_scope(job) == "amd_gpu"


def is_upstream_cuda_ci_job(job: dict) -> bool:
    """Select CUDA GPU test jobs without counting AMD mirrors or CPU steps."""
    return _job_hardware_scope(job) == "cuda_gpu"


def pipeline_job_matches_scope(job: dict, pipeline_key: str) -> bool:
    return (
        is_amd_ci_job(job)
        if pipeline_key == "amd"
        else (is_upstream_cuda_ci_job(job) if pipeline_key == "upstream" else False)
    )


# Buildkite org for vLLM
BK_ORG = "vllm"

# Job name patterns to skip (non-test infrastructure jobs).
# These are matched as substrings of lowercased job names.
# Be specific — "pipeline" was matching "Pipeline + Context Parallelism" test group!
# A bare "docker" used to drop the real "Docker Build Metadata (ROCm)" test.
# Buildkite's infrastructure steps use the explicit :docker: emoji marker.
SKIP_JOB_PATTERNS = (
    "bootstrap",
    ":docker:",
    "docker build test image",
    "build image",
    "upload",
    "pipeline upload",
)
