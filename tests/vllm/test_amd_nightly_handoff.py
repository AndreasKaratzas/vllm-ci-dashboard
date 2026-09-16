from __future__ import annotations

import json
from pathlib import Path

import pytest

from vllm.amd_nightly_handoff import (
    AMD_NIGHTLY_HANDOFF_MAX_BYTES,
    load_frozen_build_snapshot,
    write_amd_nightly_snapshot,
)
from vllm.ci.buildkite_client import (
    NIGHTLY_ROSTER_MAX_SHARD_BYTES,
    NIGHTLY_ROSTER_MAX_TOTAL_BYTES,
)
from vllm.private_ci_cache_budget import (
    DEFAULT_CONFIG_PATH,
    PrivateCiCacheBudgetError,
    load_private_ci_cache_budget,
)


def _build(*, padding: int = 0) -> dict:
    return {
        "number": 1234,
        "state": "passed",
        "commit": "a" * 40,
        "jobs": [
            {
                "type": "script",
                "id": "job-1",
                "name": "mi300_1: Engine" + "x" * padding,
                "state": "passed",
                "soft_failed": False,
                "agent_query_rules": [
                    "queue=amd_mi300_1",
                    "agent-name=private-host",
                ],
                "step": {"id": "engine", "command": "private command"},
                "agent": {"hostname": "private-host"},
            },
            {
                "type": "waiter",
                "id": "waiter-1",
                "state": "passed",
            },
        ],
    }


def test_checked_in_handoff_limit_fits_existing_private_cache_budget() -> None:
    budget = load_private_ci_cache_budget()

    assert AMD_NIGHTLY_HANDOFF_MAX_BYTES == 64 * 1024 * 1024
    assert budget.amd_frozen_nightly_max_bytes == AMD_NIGHTLY_HANDOFF_MAX_BYTES
    assert budget.nightly_roster_max_total_bytes == NIGHTLY_ROSTER_MAX_TOTAL_BYTES
    assert budget.nightly_roster_max_shard_bytes == NIGHTLY_ROSTER_MAX_SHARD_BYTES
    assert AMD_NIGHTLY_HANDOFF_MAX_BYTES <= NIGHTLY_ROSTER_MAX_TOTAL_BYTES
    assert NIGHTLY_ROSTER_MAX_SHARD_BYTES == 8 * 1024 * 1024
    assert NIGHTLY_ROSTER_MAX_TOTAL_BYTES == 64 * 1024 * 1024
    assert NIGHTLY_ROSTER_MAX_TOTAL_BYTES < 90_000_000


def test_handoff_is_exhaustive_privacy_projected_and_bounded(tmp_path: Path) -> None:
    path = write_amd_nightly_snapshot(_build(), tmp_path, max_bytes=4_096)
    raw = path.read_bytes()
    payload = json.loads(raw)

    assert len(raw) <= 4_096
    assert payload["publication_retention"]["job_rows"] == {
        "source": 2,
        "published": 2,
        "omitted": 0,
        "complete_relative_to_source": True,
    }
    assert len(payload["build"]["jobs"]) == 2
    assert payload["build"]["jobs"][0]["agent_query_rules"] == [
        "queue=amd_mi300_1"
    ]
    serialized = raw.decode()
    assert "private-host" not in serialized
    assert "private command" not in serialized
    assert load_frozen_build_snapshot(
        path, 1234, max_bytes=4_096
    ) == payload["build"]


def test_overflow_preserves_last_known_good_snapshot(tmp_path: Path) -> None:
    path = tmp_path / ".cache" / "amd_nightly_snapshot.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"prior-snapshot\n")

    with pytest.raises(RuntimeError, match="preserving the last-known-good"):
        write_amd_nightly_snapshot(_build(padding=8_000), tmp_path, max_bytes=512)

    assert path.read_bytes() == b"prior-snapshot\n"


def test_reader_rejects_oversized_file_before_json_decode(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_bytes(b"{" + b"x" * 512)

    with pytest.raises(ValueError, match="exceeds its read bound"):
        load_frozen_build_snapshot(path, 1234, max_bytes=512)


def test_reader_rejects_non_exhaustive_retention(tmp_path: Path) -> None:
    path = write_amd_nightly_snapshot(_build(), tmp_path, max_bytes=4_096)
    payload = json.loads(path.read_text())
    payload["publication_retention"]["job_rows"]["published"] = 1
    payload["publication_retention"]["job_rows"]["omitted"] = 1
    payload["publication_retention"]["job_rows"][
        "complete_relative_to_source"
    ] = False
    payload["publication_retention"]["complete_relative_to_source"] = False
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="invalid retention metadata"):
        load_frozen_build_snapshot(path, 1234, max_bytes=4_096)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda payload: payload["publication_retention"].__setitem__(
                "unattested_rows", 1
            ),
            "invalid retention metadata",
        ),
        (
            lambda payload: payload.__setitem__("generated_at", ""),
            "invalid generated_at",
        ),
    ],
)
def test_reader_rejects_noncanonical_truth_metadata(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    path = write_amd_nightly_snapshot(_build(), tmp_path, max_bytes=4_096)
    payload = json.loads(path.read_text())
    mutation(payload)
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match=message):
        load_frozen_build_snapshot(path, 1234, max_bytes=4_096)


def test_reader_rejects_symlinked_handoff(tmp_path: Path) -> None:
    target = write_amd_nightly_snapshot(_build(), tmp_path, max_bytes=4_096)
    link = tmp_path / "linked-snapshot.json"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="Unable to open frozen AMD build snapshot"):
        load_frozen_build_snapshot(link, 1234, max_bytes=4_096)


def test_private_cache_config_rejects_handoff_larger_than_total(
    tmp_path: Path,
) -> None:
    payload = json.loads(DEFAULT_CONFIG_PATH.read_text())
    payload["amd_frozen_nightly_handoff"]["max_bytes"] = (
        payload["nightly_roster_cache"]["max_total_bytes"] + 1
    )
    candidate = tmp_path / "private_ci_cache_budget.json"
    candidate.write_text(json.dumps(payload))

    with pytest.raises(PrivateCiCacheBudgetError, match="exceeds the private cache"):
        load_private_ci_cache_budget(candidate)
