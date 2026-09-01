import pytest

from vllm import operations_bundle_contract as contract


def _section_sizes(*, total: int) -> dict[str, int]:
    # Keep every required JSON shard nonempty and put the remaining budget in
    # the largest real-world section.
    return {
        "nightly": 1,
        "amd_test_health": total - 2,
        "diagnostics": 1,
    }


def test_canary_budget_accepts_the_exact_boundary_and_large_health_shard():
    manifest_bytes = 1
    section_bytes = _section_sizes(
        total=contract.OPERATIONS_CANARY_BUNDLE_MAX_BYTES - manifest_bytes,
    )

    assert section_bytes["amd_test_health"] > 2 * 1024 * 1024
    assert contract.validate_operations_canary_budget(
        manifest_bytes=manifest_bytes,
        section_bytes=section_bytes,
    ) == contract.OPERATIONS_CANARY_BUNDLE_MAX_BYTES


def test_canary_budget_rejects_one_byte_over_the_boundary():
    manifest_bytes = 1
    section_bytes = _section_sizes(
        total=contract.OPERATIONS_CANARY_BUNDLE_MAX_BYTES,
    )

    with pytest.raises(
        contract.OperationsBundleContractError,
        match="canary bundle",
    ):
        contract.validate_operations_canary_budget(
            manifest_bytes=manifest_bytes,
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
