"""Shared bounded-consumability contract for public Operations shards."""

# cspell:ignore UNFETCHED

from __future__ import annotations

from collections.abc import Mapping

from vllm.dashboard_storage_budget import writer_max_bytes


# Keep the complete lazy-section inventory explicit. A new public route must
# choose a bounded probe policy here instead of silently becoming an untested
# file in the deployed projection.
OPERATIONS_V2_SECTION_NAMES = (
    "nightly",
    "amd_test_health",
    "amd_agent_health",
    "reliability",
    "comparison",
    "comparison_retry_evidence",
    "definition_parity",
    "test_group_parity",
    "gating",
    "ownership",
    "queue",
    "trajectory",
    "omni",
    "diagnostics",
)
OPERATIONS_V3_SECTION_NAMES = tuple(
    name
    for name in OPERATIONS_V2_SECTION_NAMES
    if name not in {"gating", "trajectory", "comparison_retry_evidence"}
)
OPERATIONS_SECTION_NAMES = OPERATIONS_V3_SECTION_NAMES

# Reliability is a separately bounded 64 MiB drill-down payload. The synthetic
# monitor streams it through SHA-256 verification without retaining the body in
# memory, while every smaller lazy route is fetched and strict-JSON parsed.
OPERATIONS_STREAMED_LARGE_SECTIONS = ("reliability",)
# Compatibility spelling used by health/audit tests and downstream consumers.
OPERATIONS_UNFETCHED_LARGE_SECTIONS = frozenset(OPERATIONS_STREAMED_LARGE_SECTIONS)
OPERATIONS_STREAMED_FILE_MAX_BYTES = 64 * 1024 * 1024
OPERATIONS_BUNDLE_VERSION = 3
OPERATIONS_LEGACY_BUNDLE_VERSION = 1
# Producer activation is intentionally separate from reader support. Bundle
# upgrades ship in two phases: readers first, then this single writer selector
# after every prior-version health/watchdog run has drained.
OPERATIONS_PRODUCER_BUNDLE_VERSION = OPERATIONS_BUNDLE_VERSION
OPERATIONS_SUPPORTED_BUNDLE_VERSIONS = frozenset(
    (OPERATIONS_LEGACY_BUNDLE_VERSION, 2, OPERATIONS_BUNDLE_VERSION)
)
OPERATIONS_CANARY_SECTIONS = tuple(
    name
    for name in OPERATIONS_SECTION_NAMES
    if name not in OPERATIONS_STREAMED_LARGE_SECTIONS
)
OPERATIONS_CANARY_BUNDLE_MAX_BYTES = 32 * 1024 * 1024
OPERATIONS_CANARY_FILE_MAX_BYTES = 12 * 1024 * 1024
OPERATIONS_MANIFEST_MAX_BYTES = writer_max_bytes("operations_manifest")
# Agent health has a 16 MiB private-source allowance but is embedded alongside
# every other eagerly parsed canary. Its public projection therefore owns only
# one quarter of the shared bundle ceiling.
# These allocations are exhaustive and additive. Together with the 2 MiB
# manifest allowance they fit the 32 MiB canary envelope, so independently
# legal route files can never form an illegal synthetic-monitor response set.
OPERATIONS_V2_CANARY_SECTION_MAX_BYTES = {
    "nightly": 2 * 1024 * 1024,
    "amd_test_health": 8 * 1024 * 1024,
    "amd_agent_health": 8 * 1024 * 1024,
    "comparison": 1_310_720,
    "comparison_retry_evidence": 4 * 1024 * 1024,
    "definition_parity": 1 * 1024 * 1024,
    "test_group_parity": 512 * 1024,
    "gating": 2 * 1024 * 1024,
    "ownership": 512 * 1024,
    "queue": 1 * 1024 * 1024,
    "trajectory": 512 * 1024,
    "omni": 1 * 1024 * 1024,
    "diagnostics": 256 * 1024,
}

OPERATIONS_CANARY_SECTION_MAX_BYTES = {
    name: size for name, size in OPERATIONS_V2_CANARY_SECTION_MAX_BYTES.items()
    if name in OPERATIONS_CANARY_SECTIONS
}
# Exact five-nightly evidence replaces the smaller historical aggregate table.
# Retired routes free this allowance while the total stays below 32 MiB.
OPERATIONS_CANARY_SECTION_MAX_BYTES["comparison"] = 7 * 1024 * 1024

# Version 1 predates the exact additive per-section allocations above. Its
# immutable manifests remain safe to probe when every eager section fits the
# bounded legacy per-file ceiling and their composed response set fits the
# current 32 MiB envelope. This prevents a producer upgrade from retroactively
# invalidating the last-known-good deployed generation during a rollout.
OPERATIONS_LEGACY_CANARY_FILE_MAX_BYTES = OPERATIONS_CANARY_FILE_MAX_BYTES
OPERATIONS_LEGACY_CANARY_BUNDLE_MAX_BYTES = OPERATIONS_CANARY_BUNDLE_MAX_BYTES


class OperationsBundleContractError(ValueError):
    """A public Operations bundle cannot be consumed within its fixed budget."""


def operations_section_names_for_bundle_version(bundle_version: object) -> tuple[str, ...]:
    """Resolve the exact inventory attested by an immutable publication."""
    if (
        type(bundle_version) is not int
        or bundle_version not in OPERATIONS_SUPPORTED_BUNDLE_VERSIONS
    ):
        raise OperationsBundleContractError(
            "Operations bundle declares an unsupported bundle version"
        )
    return (
        OPERATIONS_V3_SECTION_NAMES
        if bundle_version == 3
        else OPERATIONS_V2_SECTION_NAMES
    )


def validate_operations_canary_budget(
    *,
    manifest_bytes: int,
    section_bytes: Mapping[str, object],
) -> int:
    """Return the bounded canary byte total or raise on an invalid bundle."""
    if type(manifest_bytes) is not int or not 0 < manifest_bytes <= (
        OPERATIONS_MANIFEST_MAX_BYTES
    ):
        raise OperationsBundleContractError(
            "Operations manifest exceeds its bounded read budget"
        )

    if set(section_bytes) != set(OPERATIONS_SECTION_NAMES):
        raise OperationsBundleContractError(
            "Operations bundle does not declare the exact supported section inventory"
        )

    total = manifest_bytes
    for name in OPERATIONS_CANARY_SECTIONS:
        size = section_bytes.get(name)
        section_limit = OPERATIONS_CANARY_SECTION_MAX_BYTES.get(
            name,
            OPERATIONS_CANARY_FILE_MAX_BYTES,
        )
        if type(size) is not int or not 0 < size <= section_limit:
            raise OperationsBundleContractError(
                "Operations canary bundle section "
                f"{name!r} has an invalid byte size (limit {section_limit})"
            )
        total += size
    for name in OPERATIONS_STREAMED_LARGE_SECTIONS:
        size = section_bytes.get(name)
        if type(size) is not int or not 0 < size <= OPERATIONS_STREAMED_FILE_MAX_BYTES:
            raise OperationsBundleContractError(
                f"Operations streamed section {name!r} has an invalid byte size"
            )
    if total > OPERATIONS_CANARY_BUNDLE_MAX_BYTES:
        raise OperationsBundleContractError(
            "Operations canary bundle is "
            f"{total} bytes; limit is {OPERATIONS_CANARY_BUNDLE_MAX_BYTES} bytes"
        )
    return total


def validate_operations_canary_budget_for_bundle_version(
    *,
    bundle_version: object,
    manifest_bytes: int,
    section_bytes: Mapping[str, object],
) -> int:
    """Validate one immutable bundle using the contract it declares."""
    section_names = operations_section_names_for_bundle_version(bundle_version)
    if bundle_version == OPERATIONS_BUNDLE_VERSION:
        return validate_operations_canary_budget(
            manifest_bytes=manifest_bytes,
            section_bytes=section_bytes,
        )

    if type(manifest_bytes) is not int or not 0 < manifest_bytes <= (
        OPERATIONS_MANIFEST_MAX_BYTES
    ):
        raise OperationsBundleContractError(
            "Operations manifest exceeds its bounded read budget"
        )
    if set(section_bytes) != set(section_names):
        raise OperationsBundleContractError(
            "Operations bundle does not declare the exact supported section inventory"
        )

    total = manifest_bytes
    canary_names = tuple(
        name for name in section_names if name not in OPERATIONS_STREAMED_LARGE_SECTIONS
    )
    for name in canary_names:
        size = section_bytes.get(name)
        section_limit = (
            OPERATIONS_V2_CANARY_SECTION_MAX_BYTES[name]
            if bundle_version == 2
            else OPERATIONS_LEGACY_CANARY_FILE_MAX_BYTES
        )
        if type(size) is not int or not 0 < size <= section_limit:
            raise OperationsBundleContractError(
                "Operations canary bundle section "
                f"{name!r} has an invalid byte size"
            )
        total += size
    for name in OPERATIONS_STREAMED_LARGE_SECTIONS:
        size = section_bytes.get(name)
        if type(size) is not int or not 0 < size <= OPERATIONS_STREAMED_FILE_MAX_BYTES:
            raise OperationsBundleContractError(
                f"Operations streamed section {name!r} has an invalid byte size"
            )
    if total > OPERATIONS_LEGACY_CANARY_BUNDLE_MAX_BYTES:
        raise OperationsBundleContractError(
            "Legacy Operations canary bundle is "
            f"{total} bytes; limit is "
            f"{OPERATIONS_LEGACY_CANARY_BUNDLE_MAX_BYTES} bytes"
        )
    return total
