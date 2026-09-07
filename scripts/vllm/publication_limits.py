"""Shared hard limits for every GitHub Pages publication boundary."""

from __future__ import annotations

# Keep one source of truth for site assembly, projection attestations, remote
# Git proofs, and synthetic health verification. The blob ceiling stays below
# both the dashboard sync layer's 90 MB threshold and GitHub's large-file flag.
PUBLICATION_MAX_BLOB_BYTES = 85 * 1024 * 1024
PUBLICATION_MAX_TREE_BYTES = 256 * 1024 * 1024
PUBLICATION_MAX_FILES = 10_000

# A preview omits the redundant queue_timeseries fallback before it is nested
# below ``pr-preview/``. One current preview therefore fits beside the canonical
# root with useful headroom, while the final tree still obeys the shared 256 MiB
# ceiling. Preview retention always removes complete cohorts.
PREVIEW_MAX_BYTES = 112 * 1024 * 1024
PREVIEW_MAX_FILES = 4_000
PREVIEW_MAX_COUNT = 50
SINGLE_PREVIEW_MAX_BYTES = 112 * 1024 * 1024
SINGLE_PREVIEW_MAX_FILES = 2_000


def normalize_safe_historical_limits(value: object) -> tuple[dict[str, int], bool]:
    """Return bounded manifest limits and whether they use legacy compatibility.

    This is a validation-only rollover rule.  A historical manifest may name a
    tree ceiling at least as large as the current ceiling because callers still
    enforce the current ceiling against its cryptographically bound inventory.
    Its blob and file-count ceilings may not be weaker than current policy.
    New manifest writers must use the constants above directly instead.
    """
    current = {
        "max_blob_bytes": PUBLICATION_MAX_BLOB_BYTES,
        "max_tree_bytes": PUBLICATION_MAX_TREE_BYTES,
        "max_files": PUBLICATION_MAX_FILES,
    }
    if value == current:
        return current, False
    if not isinstance(value, dict) or set(value) != set(current):
        raise ValueError("manifest limits have an unexpected shape")
    max_blob_bytes = value.get("max_blob_bytes")
    max_tree_bytes = value.get("max_tree_bytes")
    max_files = value.get("max_files")
    if any(
        type(item) is not int
        for item in (max_blob_bytes, max_tree_bytes, max_files)
    ):
        raise ValueError("manifest limits must be integers")
    assert isinstance(max_blob_bytes, int)
    assert isinstance(max_tree_bytes, int)
    assert isinstance(max_files, int)
    if (
        not 0 < max_blob_bytes <= PUBLICATION_MAX_BLOB_BYTES
        or max_tree_bytes < PUBLICATION_MAX_TREE_BYTES
        or not 0 < max_files <= PUBLICATION_MAX_FILES
    ):
        raise ValueError("legacy manifest limits are not safely bounded")
    return {
        "max_blob_bytes": max_blob_bytes,
        "max_tree_bytes": max_tree_bytes,
        "max_files": max_files,
    }, True
