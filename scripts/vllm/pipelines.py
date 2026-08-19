"""vLLM-specific Buildkite pipeline definitions.

This file contains the pipeline configurations specific to vLLM.
To add a new project, create a similar file under scripts/<project>/pipelines.py
with its own PIPELINES dict.
"""

import re

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
UPSTREAM_SCHEDULED_GATING_NAME_PATTERN = (
    r"^Full CI run\s*-\s*(nightly|daily)(?:\s|$)"
)
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
        "slug": "amd-ci",
        "name_pattern": AMD_NIGHTLY_NAME_PATTERN,
        "branch": "main",
        "display_name": "AMD Nightly",
    },
    "upstream": {
        "slug": "ci",
        "name_pattern": UPSTREAM_NIGHTLY_NAME_PATTERN,
        "branch": "main",
        "display_name": "Upstream Nightly",
    },
}

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
