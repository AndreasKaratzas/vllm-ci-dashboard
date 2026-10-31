# cspell:ignore mibibyte

import pytest

from vllm import build_operations_snapshot as operations
from vllm import collect_agent_health
from vllm import operations_bundle_contract as contract


def _section_sizes(*, total: int) -> dict[str, int]:
    if total < len(contract.OPERATIONS_CANARY_SECTIONS):
        raise ValueError("total cannot give every section a positive byte")
    section_sizes = {name: 1 for name in contract.OPERATIONS_CANARY_SECTIONS}
    remaining = total - len(section_sizes)
    for name in contract.OPERATIONS_CANARY_SECTIONS:
        room = contract.OPERATIONS_CANARY_SECTION_MAX_BYTES[name] - 1
        increment = min(room, remaining)
        section_sizes[name] += increment
        remaining -= increment
    if remaining:
        raise ValueError("total exceeds additive canary section allocations")
    section_sizes["reliability"] = 1
    return section_sizes


def test_probe_policy_covers_every_declared_section_and_streams_reliability():
    assert contract.OPERATIONS_BUNDLE_VERSION == 5
    assert (
        contract.OPERATIONS_PRODUCER_BUNDLE_VERSION
        in contract.OPERATIONS_SUPPORTED_BUNDLE_VERSIONS
    )
    assert contract.OPERATIONS_SUPPORTED_BUNDLE_VERSIONS == {1, 2, 3, 4, 5}
    assert contract.OPERATIONS_STREAMED_LARGE_SECTIONS == ("reliability",)
    assert (
        set(contract.OPERATIONS_SECTION_NAMES)
        - set(contract.OPERATIONS_CANARY_SECTIONS)
        == set(contract.OPERATIONS_STREAMED_LARGE_SECTIONS)
    )
    assert len(contract.OPERATIONS_CANARY_SECTIONS) == len(
        contract.OPERATIONS_SECTION_NAMES
    ) - 1
    assert set(contract.OPERATIONS_CANARY_SECTION_MAX_BYTES) == set(
        contract.OPERATIONS_CANARY_SECTIONS
    )
    assert (
        contract.OPERATIONS_MANIFEST_MAX_BYTES
        + sum(contract.OPERATIONS_CANARY_SECTION_MAX_BYTES.values())
        <= contract.OPERATIONS_CANARY_BUNDLE_MAX_BYTES
    )


def test_agent_health_source_and_public_allocations_compose_with_canary_budget():
    public_limit = contract.OPERATIONS_CANARY_SECTION_MAX_BYTES["amd_agent_health"]

    assert collect_agent_health.AGENT_HEALTH_MAX_FILE_BYTES == 32 * 1024 * 1024
    assert collect_agent_health.AGENT_HEALTH_MAX_GENERATION_BYTES == 16 * 1024 * 1024
    assert operations.OPERATIONS_AGENT_HEALTH_SECTION_MAX_BYTES == public_limit
    assert public_limit <= contract.OPERATIONS_CANARY_FILE_MAX_BYTES
    assert public_limit + contract.OPERATIONS_MANIFEST_MAX_BYTES < (
        contract.OPERATIONS_CANARY_BUNDLE_MAX_BYTES
    )


@pytest.mark.parametrize("unexpected_name", [None, "future_section"])
def test_canary_budget_requires_the_exact_section_inventory(unexpected_name):
    section_bytes = _section_sizes(total=len(contract.OPERATIONS_CANARY_SECTIONS))
    if unexpected_name is None:
        section_bytes.pop("reliability")
    else:
        section_bytes[unexpected_name] = 1

    with pytest.raises(
        contract.OperationsBundleContractError,
        match="exact supported section inventory",
    ):
        contract.validate_operations_canary_budget(
            manifest_bytes=1,
            section_bytes=section_bytes,
        )


def test_canary_budget_accepts_the_exact_boundary_and_large_health_shard():
    manifest_bytes = contract.OPERATIONS_MANIFEST_MAX_BYTES
    section_bytes = dict(contract.OPERATIONS_CANARY_SECTION_MAX_BYTES)
    section_bytes["reliability"] = 1

    assert section_bytes["amd_test_health"] > 2 * 1024 * 1024
    assert contract.validate_operations_canary_budget(
        manifest_bytes=manifest_bytes,
        section_bytes=section_bytes,
    ) <= contract.OPERATIONS_CANARY_BUNDLE_MAX_BYTES


def test_canary_budget_rejects_one_byte_over_the_boundary():
    manifest_bytes = contract.OPERATIONS_MANIFEST_MAX_BYTES
    section_bytes = dict(contract.OPERATIONS_CANARY_SECTION_MAX_BYTES)
    section_bytes["diagnostics"] += 1
    section_bytes["reliability"] = 1

    with pytest.raises(
        contract.OperationsBundleContractError,
        match="section 'diagnostics'",
    ):
        contract.validate_operations_canary_budget(
            manifest_bytes=manifest_bytes,
            section_bytes=section_bytes,
        )


def test_legacy_bundle_keeps_bounded_rollout_compatibility():
    section_bytes = {name: 1 for name in contract.OPERATIONS_V2_SECTION_NAMES}
    section_bytes["comparison_retry_evidence"] = (
        contract.OPERATIONS_V2_CANARY_SECTION_MAX_BYTES[
            "comparison_retry_evidence"
        ]
        + 1
    )

    with pytest.raises(contract.OperationsBundleContractError):
        contract.validate_operations_canary_budget(
            manifest_bytes=1,
            section_bytes=section_bytes,
        )
    assert contract.validate_operations_canary_budget_for_bundle_version(
        bundle_version=contract.OPERATIONS_LEGACY_BUNDLE_VERSION,
        manifest_bytes=1,
        section_bytes=section_bytes,
    ) > 0


@pytest.mark.parametrize("bundle_version", [True, 0, 6, "2"])
def test_bundle_version_dispatch_rejects_unknown_versions(bundle_version):
    with pytest.raises(
        contract.OperationsBundleContractError,
        match="unsupported bundle version",
    ):
        contract.validate_operations_canary_budget_for_bundle_version(
            bundle_version=bundle_version,
            manifest_bytes=1,
            section_bytes=_section_sizes(
                total=len(contract.OPERATIONS_CANARY_SECTIONS)
            ),
        )


def test_v3_readers_require_smaller_inventory():
    sections = {name: 1 for name in contract.OPERATIONS_V3_SECTION_NAMES}
    assert contract.validate_operations_canary_budget_for_bundle_version(
        bundle_version=3, manifest_bytes=1, section_bytes=sections
    ) == len(sections)
    for version in (1, 2, 4, 5):
        with pytest.raises(contract.OperationsBundleContractError, match="inventory"):
            contract.validate_operations_canary_budget_for_bundle_version(
                bundle_version=version, manifest_bytes=1, section_bytes=sections
            )


def test_v4_adds_main_ci_and_keeps_v3_readable():
    sections = {name: 1 for name in contract.OPERATIONS_V4_SECTION_NAMES}
    assert set(contract.OPERATIONS_V4_SECTION_NAMES) - set(
        contract.OPERATIONS_V3_SECTION_NAMES
    ) == {"main_ci"}
    assert contract.validate_operations_canary_budget_for_bundle_version(
        bundle_version=4, manifest_bytes=1, section_bytes=sections
    ) == len(sections)
    with pytest.raises(contract.OperationsBundleContractError, match="inventory"):
        contract.validate_operations_canary_budget_for_bundle_version(
            bundle_version=3, manifest_bytes=1, section_bytes=sections
        )


def test_v5_producer_retires_ownership_and_keeps_v4_readable():
    sections = {name: 1 for name in contract.OPERATIONS_V5_SECTION_NAMES}
    assert contract.OPERATIONS_PRODUCER_BUNDLE_VERSION == 5
    assert set(contract.OPERATIONS_V4_SECTION_NAMES) - set(
        contract.OPERATIONS_V5_SECTION_NAMES
    ) == {"ownership"}
    assert contract.validate_operations_canary_budget_for_bundle_version(
        bundle_version=5, manifest_bytes=1, section_bytes=sections
    ) == len(sections)
    with pytest.raises(contract.OperationsBundleContractError, match="inventory"):
        contract.validate_operations_canary_budget_for_bundle_version(
            bundle_version=5, manifest_bytes=1, section_bytes={**sections, "ownership": 1}
        )
    v4 = {name: 1 for name in contract.OPERATIONS_V4_SECTION_NAMES}
    assert contract.validate_operations_canary_budget_for_bundle_version(
        bundle_version=4, manifest_bytes=1, section_bytes=v4
    ) == len(v4)


def test_v3_reader_keeps_its_published_comparison_allocation():
    sections = {name: 1 for name in contract.OPERATIONS_V3_SECTION_NAMES}
    sections["comparison"] = contract.OPERATIONS_V3_CANARY_SECTION_MAX_BYTES["comparison"]
    assert sections["comparison"] > contract.OPERATIONS_CANARY_SECTION_MAX_BYTES["comparison"]
    assert contract.validate_operations_canary_budget_for_bundle_version(
        bundle_version=3, manifest_bytes=1, section_bytes=sections
    ) > 0


@pytest.mark.parametrize("retired_section", ["gating", "trajectory", "comparison_retry_evidence"])
def test_v3_reader_rejects_retired_sections(retired_section):
    for version, names in (
        (3, contract.OPERATIONS_V3_SECTION_NAMES),
        (4, contract.OPERATIONS_V4_SECTION_NAMES),
        (5, contract.OPERATIONS_V5_SECTION_NAMES),
    ):
        sections = {name: 1 for name in names}
        sections[retired_section] = 1
        with pytest.raises(contract.OperationsBundleContractError, match="inventory"):
            contract.validate_operations_canary_budget_for_bundle_version(
                bundle_version=version, manifest_bytes=1, section_bytes=sections
            )


_V3 = (3, contract.OPERATIONS_V3_SECTION_NAMES, contract.OPERATIONS_V3_CANARY_SECTION_MAX_BYTES)
_V4 = (4, contract.OPERATIONS_V4_SECTION_NAMES, contract.OPERATIONS_V4_CANARY_SECTION_MAX_BYTES)
_V5 = (5, contract.OPERATIONS_V5_SECTION_NAMES, contract.OPERATIONS_CANARY_SECTION_MAX_BYTES)


@pytest.mark.parametrize(
    ("version", "names", "limits", "section"),
    [(*_V3, "comparison"), (*_V4, "comparison"), (*_V4, "main_ci"), (*_V4, "ownership"),
     (*_V5, "comparison"), (*_V5, "main_ci")],
)
def test_readers_retain_additive_per_section_limits(version, names, limits, section):
    sections = {name: 1 for name in names}
    sections[section] = limits[section] + 1
    with pytest.raises(contract.OperationsBundleContractError, match=f"section '{section}'"):
        contract.validate_operations_canary_budget_for_bundle_version(
            bundle_version=version, manifest_bytes=1, section_bytes=sections
        )


def test_streamed_section_has_an_independent_64_mibibyte_bound():
    section_bytes = _section_sizes(total=len(contract.OPERATIONS_CANARY_SECTIONS))
    section_bytes["reliability"] = contract.OPERATIONS_STREAMED_FILE_MAX_BYTES + 1

    with pytest.raises(
        contract.OperationsBundleContractError,
        match="streamed section 'reliability'",
    ):
        contract.validate_operations_canary_budget(
            manifest_bytes=1,
            section_bytes=section_bytes,
        )


@pytest.mark.parametrize(
    ("manifest_bytes", "section_bytes"),
    [
        (0, {"nightly": 1, "amd_test_health": 1, "diagnostics": 1}),
        (True, {"nightly": 1, "amd_test_health": 1, "diagnostics": 1}),
        (1, {"nightly": 1, "amd_test_health": 1}),
        (1, {"nightly": 1, "amd_test_health": 0, "diagnostics": 1}),
        (1, {"nightly": 1, "amd_test_health": True, "diagnostics": 1}),
    ],
)
def test_canary_budget_rejects_missing_empty_or_non_integer_sizes(
    manifest_bytes,
    section_bytes,
):
    with pytest.raises(contract.OperationsBundleContractError):
        contract.validate_operations_canary_budget(
            manifest_bytes=manifest_bytes,
            section_bytes=section_bytes,
        )
