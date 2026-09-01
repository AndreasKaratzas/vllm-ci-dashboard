"""Shared bounded-consumability contract for public Operations shards."""

from __future__ import annotations

from collections.abc import Mapping


# These are the independent routes exercised by the synthetic health check.
# The first two are fetched together by the default CI-health overview; the
# diagnostics shard proves that a second lazy route is usable as well.
OPERATIONS_CANARY_SECTIONS = ("nightly", "amd_test_health", "diagnostics")
OPERATIONS_CANARY_BUNDLE_MAX_BYTES = 12_000_000
OPERATIONS_CANARY_FILE_MAX_BYTES = OPERATIONS_CANARY_BUNDLE_MAX_BYTES
OPERATIONS_MANIFEST_MAX_BYTES = 2 * 1024 * 1024


class OperationsBundleContractError(ValueError):
    """A public Operations bundle cannot be consumed within its fixed budget."""


def validate_operations_canary_budget(
    *,
    manifest_bytes: int,
    section_bytes: Mapping[str, object],
) -> int:
    """Return the bounded canary byte total or raise on an invalid contract."""
    if type(manifest_bytes) is not int or not 0 < manifest_bytes <= (
        OPERATIONS_MANIFEST_MAX_BYTES
    ):
        raise OperationsBundleContractError(
            "Operations manifest exceeds its bounded read budget"
        )

    total = manifest_bytes
    for name in OPERATIONS_CANARY_SECTIONS:
        size = section_bytes.get(name)
        if type(size) is not int or not 0 < size <= OPERATIONS_CANARY_FILE_MAX_BYTES:
            raise OperationsBundleContractError(
                f"Operations canary section {name!r} has an invalid byte size"
            )
        total += size
    if total > OPERATIONS_CANARY_BUNDLE_MAX_BYTES:
        raise OperationsBundleContractError(
            "Operations canary bundle is "
            f"{total} bytes; limit is {OPERATIONS_CANARY_BUNDLE_MAX_BYTES} bytes"
        )
    return total
