"""Keep verbose test output bounded before any test starts."""

# cspell:ignore nodeid

import pytest


MAX_TEST_NODE_ID_LENGTH = 4096


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    oversized = sum(len(item.nodeid) > MAX_TEST_NODE_ID_LENGTH for item in items)
    if oversized:
        raise pytest.UsageError(
            f"Oversized test IDs: {oversized} exceed {MAX_TEST_NODE_ID_LENGTH} characters. "
            "Assign short explicit pytest parameter ids."
        )
