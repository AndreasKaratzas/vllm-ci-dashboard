"""Bounded-state contracts for the test-group change collector."""

from __future__ import annotations

import json

import pytest

from vllm import collect_group_changes as changes
from vllm.collect_group_changes import (
    _compact_group_changes_for_publication,
    _retain_source_window_cache,
    _write_bounded_group_changes,
    _write_json_atomic,
)


def test_group_change_cache_drops_rows_outside_declared_window() -> None:
    changes, no_changes = _retain_source_window_cache(
        [
            {"date": "2026-07-01", "sha": "old"},
            {"date": "2026-08-15", "sha": "kept"},
        ],
        {"present", "retired"},
        [{"sha": "present"}],
        cutoff_date="2026-08-01",
    )

    assert changes == [{"date": "2026-08-15", "sha": "kept"}]
    assert no_changes == {"present"}


def test_group_change_writer_replaces_complete_json(tmp_path) -> None:
    path = tmp_path / "group_changes.json"
    path.write_text('{"generation":"old"}\n')

    _write_json_atomic(path, {"generation": "new"})

    assert json.loads(path.read_text()) == {"generation": "new"}


def test_group_changes_retain_newest_whole_rows_with_exact_accounting() -> None:
    source = {
        "generated_at": "2026-09-01T00:00:00Z",
        "days": 30,
        "changes": [
            {"date": f"2026-08-{day:02d}", "sha": str(day), "padding": "x" * 700}
            for day in range(1, 21)
        ],
        "_no_change_shas": [f"sha-{index:03d}" for index in range(20)],
    }

    first = _compact_group_changes_for_publication(source, max_bytes=5_000)
    second = _compact_group_changes_for_publication(source, max_bytes=5_000)

    assert first == second
    assert len((json.dumps(first, indent=2) + "\n").encode()) <= 5_000
    assert 0 < len(first["changes"]) < len(source["changes"])
    assert first["changes"] == source["changes"][-len(first["changes"]):]
    assert first["total_changes"] == len(first["changes"])
    assert first["source_total_changes"] == len(source["changes"])
    retention = first["publication_retention"]
    assert retention["complete_relative_to_source"] is False
    assert retention["changes"]["source"] == len(source["changes"])
    assert retention["changes"]["published"] == len(first["changes"])
    assert retention["changes"]["omitted"] > 0


def test_group_changes_trim_refetchable_cache_before_public_history() -> None:
    source = {
        "generated_at": "2026-09-01T00:00:00Z",
        "days": 30,
        "changes": [
            {"date": f"2026-08-0{day}", "sha": str(day), "padding": "x" * 200}
            for day in range(1, 4)
        ],
        "_no_change_shas": [f"newest-{index:03d}-" + "s" * 80 for index in range(100)],
    }

    bounded = _compact_group_changes_for_publication(source, max_bytes=3_000)

    assert bounded["changes"] == source["changes"]
    assert 0 < len(bounded["_no_change_shas"]) < 100
    assert bounded["_no_change_shas"] == source["_no_change_shas"][
        :len(bounded["_no_change_shas"])
    ]
    retention = bounded["publication_retention"]
    assert retention["changes"]["complete"] is True
    assert retention["no_change_cache"]["complete"] is False


def test_group_change_bound_failure_preserves_last_known_good(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "group_changes.json"
    path.write_bytes(b"last-known-good\n")
    monkeypatch.setattr(changes, "GROUP_CHANGES_MAX_BYTES", 1)

    with pytest.raises(RuntimeError, match="last-known-good"):
        _write_bounded_group_changes(
            path,
            {"generated_at": "2026-09-01T00:00:00Z", "changes": []},
        )

    assert path.read_bytes() == b"last-known-good\n"
