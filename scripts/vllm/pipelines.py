"""vLLM-specific Buildkite pipeline definitions.

This file contains the pipeline configurations specific to vLLM.
To add a new project, create a similar file under scripts/<project>/pipelines.py
with its own PIPELINES dict.
"""

# vLLM Buildkite build names to monitor.
#
# Keep the AMD pattern exact: "AMD Full CI Run - TheRock nightly" is a
# separate nightly stream and should not drive the dashboard's normal AMD
# health, analytics, or matrix views.
AMD_NIGHTLY_NAME_PATTERN = r"^AMD Full CI Run\s*-\s*nightly(?:\s|$)"
UPSTREAM_NIGHTLY_NAME_PATTERN = r"^Full CI run\s*-\s*nightly(?:\s|$)"

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
SKIP_JOB_PATTERNS = ("bootstrap", "docker", "build image", "upload", "pipeline upload")
