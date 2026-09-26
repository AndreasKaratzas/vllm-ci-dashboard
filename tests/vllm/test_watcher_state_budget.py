from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from vllm.ci.watcher_state import (
    RETENTION_KEY,
    WATCHER_STATE_WRITERS,
    WatcherStateBudgetError,
    bounded_watcher_state,
    watcher_state_allocated_bytes,
    watcher_state_max_bytes,
    write_watcher_state,
)
from vllm.dashboard_storage_budget import group_max_bytes


ROOT = Path(__file__).resolve().parents[2]


def _managed_issue(number: int) -> dict:
    return {
        "schema_version": 2,
        "initialized": True,
        "processed_build_numbers": list(range(1, 401)),
        "processed_through": {
            "number": 400,
            "created_at": "2026-09-01T00:00:00Z",
            "finished_at": "2026-09-01T00:00:00Z",
        },
        "active": {
            "live-group": {
                "group_id": "live-group",
                "name": "live group",
                "result": "failed",
                "build_number": 400,
                "build_url": "https://buildkite.example/build/400",
                "job_url": "https://buildkite.example/build/400/job",
                "observed_at": "2026-09-01T00:00:00Z",
                "transition": {
                    "status": "confirmed",
                    "severity": "hard",
                    "peak_severity": "hard",
                    "incident_start_build_id": "400",
                    "last_eligible_build_id": "400",
                    "confirmed_build_id": "400",
                    "soft_streak": 0,
                },
                "retry_evidence": {"unbounded": "x" * 20_000},
            }
        },
        "pending_soft": {
            "pending-group": {
                "group_id": "pending-group",
                "name": "pending group",
                "result": "soft_fail",
                "build_number": 400,
                "transition": {
                    "status": "pending_soft",
                    "severity": "soft",
                    "peak_severity": "soft",
                    "incident_start_build_id": "400",
                    "last_eligible_build_id": "400",
                    "confirmed_build_id": None,
                    "soft_streak": 1,
                },
            }
        },
        "group_watermarks": {
            **{
                f"retired-{index:06d}": {
                    "build_number": index,
                    "order_at": f"2026-08-{index % 28 + 1:02d}T00:00:00Z",
                    "created_at": "duplicate",
                    "finished_at": "duplicate",
                    "result": "passed",
                    "commit": "a" * 40,
                    "refetchable": "z" * 300,
                }
                for index in range(6_000)
            },
            "live-group": {"build_number": 400, "order_at": "2026-09-01T00:00:00Z"},
            "pending-group": {"build_number": 400, "order_at": "2026-09-01T00:00:00Z"},
        },
        "issue": {"number": number, "opened_at": "2026-09-01T00:00:00Z"},
        "suppressed": False,
        "last_fingerprint": "signal",
        "last_content_fingerprint": "content",
        "last_run": "2026-09-01T00:00:00Z",
    }


def test_suballocations_compose_exactly_to_the_shared_three_mib_budget() -> None:
    assert len(WATCHER_STATE_WRITERS) == 8
    assert watcher_state_allocated_bytes() == 3 * 1024 * 1024
    assert watcher_state_allocated_bytes() == group_max_bytes("watcher_state")


@pytest.mark.parametrize(
    "module_path",
    [
        "scripts/vllm/agent_health_issue_watcher.py",
        "scripts/vllm/amd_duration_regression_watcher.py",
        "scripts/vllm/amd_main_failure_watcher.py",
        "scripts/vllm/ci_area_regression_watcher.py",
        "scripts/vllm/ci_main_failure_watcher.py",
        "scripts/vllm/omni_surge_watcher.py",
        "scripts/vllm/queue_issue_watcher.py",
        "scripts/vllm/queue_zombie_watcher.py",
    ],
)
def test_every_watcher_routes_state_through_the_shared_writer(module_path: str) -> None:
    source = (ROOT / module_path).read_text()
    if module_path.endswith("ci_main_failure_watcher.py"):
        # This thin wrapper delegates to amd_main_failure_watcher.run_watcher,
        # whose configured path is passed to the shared writer.
        assert "shared.run_watcher(CONFIG)" in source
    else:
        assert "write_watcher_state" in source


def test_main_state_drops_only_inactive_cache_and_preserves_all_actionable_rows() -> None:
    path = Path("open_ci_main_failure_issues.json")
    source = _managed_issue(991)
    published, encoded = bounded_watcher_state(path, source)

    assert len(encoded) <= watcher_state_max_bytes(path)
    assert published["issue"] == source["issue"]
    assert set(published["active"]) == {"live-group"}
    assert set(published["pending_soft"]) == {"pending-group"}
    assert published["active"]["live-group"]["transition"] == source["active"][
        "live-group"
    ]["transition"]
    assert "retry_evidence" not in published["active"]["live-group"]
    assert {"live-group", "pending-group"}.issubset(published["group_watermarks"])
    retention = published[RETENTION_KEY]
    assert retention["complete_relative_to_source"] is False
    assert retention["protected_mappings"] == {
        "source": 3,
        "published": 3,
        "omitted": 0,
    }
    assert retention["collections"]["detail_fields"]["omitted"] > 0
    assert retention["published_bytes"] == len(encoded)


def test_main_state_bounds_a_large_active_catalog_without_losing_an_identity() -> None:
    source = _managed_issue(992)
    source["group_watermarks"] = {}
    source["processed_build_numbers"] = []
    source["active"] = {
        f"active-{index:05d}": {
            "group_id": f"active-{index:05d}",
            "name": f"active group {index}",
            "result": "failed",
            "build_number": index + 1,
            "hardware": "h200",
            "queue": "default",
            "transition": {
                "status": "confirmed",
                "severity": "hard",
                "peak_severity": "hard",
                "incident_start_build_id": str(index + 1),
                "last_eligible_build_id": str(index + 1),
                "confirmed_build_id": str(index + 1),
                "soft_streak": 0,
            },
            "retry_evidence": {"refetchable": "x" * 5_000},
            "build_message": "y" * 5_000,
        }
        for index in range(1_000)
    }
    path = Path("open_ci_main_failure_issues.json")
    published, encoded = bounded_watcher_state(path, source)

    assert len(encoded) <= watcher_state_max_bytes(path)
    assert set(published["active"]) == set(source["active"])
    assert published[RETENTION_KEY]["protected_mappings"] == {
        "source": 1_002,
        "published": 1_002,
        "omitted": 0,
    }
    assert published[RETENTION_KEY]["collections"]["detail_fields"]["omitted"] > 0


def test_processed_build_suffix_can_compact_behind_the_global_ordering_fence() -> None:
    source = _managed_issue(993)
    source["active"] = {}
    source["pending_soft"] = {}
    source["group_watermarks"] = {}
    source["processed_build_numbers"] = list(range(1, 200_001))
    path = Path("open_amd_main_failure_issues.json")
    published, encoded = bounded_watcher_state(path, source)

    assert len(encoded) <= watcher_state_max_bytes(path)
    retained = published["processed_build_numbers"]
    assert retained == list(range(200_001 - len(retained), 200_001))
    collection = published[RETENTION_KEY]["collections"]["processed_build_numbers"]
    assert collection["source"] == 200_000
    assert collection["published"] == len(retained)
    assert collection["omitted"] > 0


def test_global_ordering_fence_prevents_a_compacted_build_from_replaying() -> None:
    from vllm import amd_main_failure_watcher as watcher

    state = watcher._default_state()
    state["initialized"] = True
    state["processed_through"] = {
        "number": 400,
        "created_at": "2026-09-01T00:00:00Z",
        "finished_at": "2026-09-01T01:00:00Z",
    }

    def reliability(number: int, created_at: str) -> dict:
        return {
            "schema_version": 1,
            "builds": [
                {
                    "number": number,
                    "created_at": created_at,
                    "finished_at": created_at,
                }
            ],
            "groups": [
                {
                    "group_id": "new-group",
                    "name": "new group",
                    "observations": [
                        {
                            "build_number": number,
                            "eligible_for_reliability": True,
                            "result": "failed",
                            "observed_at": created_at,
                            "finished_at": created_at,
                        }
                    ],
                }
            ],
        }

    held = watcher.advance_incidents(
        reliability(399, "2026-08-31T00:00:00Z"),
        state,
    )
    assert held["active"] == {}
    assert held["pending_soft"] == {}

    advanced = watcher.advance_incidents(
        reliability(401, "2026-09-02T00:00:00Z"),
        held,
    )
    assert set(advanced["active"]) == {"new-group"}
    assert advanced["processed_through"]["number"] == 401


def test_area_state_prunes_clear_cache_but_keeps_open_and_pending_mappings() -> None:
    clear_signals = {
        f"clear-{index:06d}": {
            "status": "clear",
            "build_watermark": index,
            "identity": {"id": f"clear-{index:06d}", "label": "x" * 500},
        }
        for index in range(5_000)
    }
    source = {
        "schema_version": 1,
        "last_run": "2026-09-01T00:00:00Z",
        "areas": {
            "active-area": {
                "issue": {"number": 812, "opened_at": "now"},
                "suppressed": False,
                "signals": {
                    **clear_signals,
                    "live": {
                        "status": "confirmed",
                        "severity": "hard",
                        "peak_severity": "hard",
                        "build_watermark": 12,
                        "identity": {"id": "live", "label": "live target"},
                        "evidence": {"build_number": 12, "url": "https://example.test/job"},
                    },
                },
            },
            "retired-cache": {"signals": clear_signals},
        },
    }
    path = Path("open_ci_area_regression_issues.json")
    published, encoded = bounded_watcher_state(path, source)

    assert len(encoded) <= watcher_state_max_bytes(path)
    assert published["areas"]["active-area"]["issue"]["number"] == 812
    assert set(published["areas"]["active-area"]["signals"]) == {"live"}
    assert "retired-cache" not in published["areas"]
    retention = published[RETENTION_KEY]
    assert retention["protected_mappings"]["omitted"] == 0
    assert retention["collections"]["signals"]["omitted"] == 10_000
    assert retention["collections"]["areas"]["omitted"] == 1


def test_managed_area_keeps_a_clear_schema_sentinel_for_soft_hysteresis() -> None:
    from vllm.ci.ownership import apply_incident_hysteresis

    source = {
        "schema_version": 1,
        "areas": {
            "kernels": {
                "issue": {"number": 44, "opened_at": "now"},
                "incident_state_version": 1,
                "signals": {
                    "old-clear": {
                        "status": "clear",
                        "build_watermark": 10,
                        "identity": {"id": "old-clear", "label": "old"},
                        "refetchable": "x" * 5_000,
                    }
                },
            }
        },
        "last_run": "now",
    }
    published, _ = bounded_watcher_state(
        Path("open_ci_area_regression_issues.json"),
        source,
        max_bytes=2_000,
    )
    assert set(published["areas"]["kernels"]["signals"]) == {"old-clear"}

    status = {
        "areas": [
            {
                "area": "kernels",
                "targets": [
                    {
                        "id": "new-soft",
                        "label": "new soft",
                        "result": "soft",
                        "build_number": 11,
                        "observed_at": "2026-09-01T00:00:00Z",
                    }
                ],
                "counts": {},
            }
        ],
        "summary": {},
    }
    signals = apply_incident_hysteresis(status, published["areas"])
    assert signals["kernels"]["new-soft"]["status"] == "pending_soft"


def test_queue_state_keeps_every_open_and_suppressed_mapping() -> None:
    source = {
        "last_run": "2026-09-01T00:00:00Z",
        "open": {
            f"queue-{index}": {
                "number": index + 1,
                "peak_p90": 90,
                "opened_ts": "2026-09-01T00:00:00Z",
                "last_status_ts": "2026-09-01T00:00:00Z",
                "refetchable": "x" * 500,
            }
            for index in range(100)
        },
        "suppressed": {
            f"retired-{index:05d}": {
                "closed_ts": f"2025-01-{index % 28 + 1:02d}T00:00:00Z",
                "last_number": index,
                "detail": "z" * 500,
            }
            for index in range(1_000)
        },
    }
    path = Path("open_queue_issues.json")
    published, encoded = bounded_watcher_state(path, source)

    assert len(encoded) <= watcher_state_max_bytes(path)
    assert set(published["open"]) == set(source["open"])
    assert set(published["suppressed"]) == set(source["suppressed"])
    assert published[RETENTION_KEY]["protected_mappings"] == {
        "source": 1_100,
        "published": 1_100,
        "omitted": 0,
    }
    assert published[RETENTION_KEY]["collections"]["suppressed"]["omitted"] == 0
    assert published[RETENTION_KEY]["collections"]["detail_fields"]["omitted"] > 0


def test_irreducible_active_mapping_overflow_fails_without_replacing_lkg(
    tmp_path: Path,
) -> None:
    path = tmp_path / "open_queue_zombie_issues.json"
    original = b'{"open": {"safe": {"number": 1}}}\n'
    path.write_bytes(original)
    state = {
        "open": {
            (f"queue-{index}-" + "q" * 4_000): {
                "number": index + 1,
                "opened_ts": "now",
                "last_fingerprint": "f" * 64,
            }
            for index in range(100)
        },
        "last_run": "now",
    }

    with pytest.raises(WatcherStateBudgetError, match="preserving LKG"):
        write_watcher_state(path, state)

    assert path.read_bytes() == original
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []


def test_atomic_replace_failure_preserves_lkg_and_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "open_omni_surge_issues.json"
    original = b'{"open": 44}\n'
    path.write_bytes(original)

    def fail_replace(source: str | os.PathLike, destination: str | os.PathLike) -> None:
        raise OSError("replacement failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failed"):
        write_watcher_state(path, {"open": 44, "last_value": 9})

    assert path.read_bytes() == original
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []


def test_current_bundle_is_within_the_aggregate_budget() -> None:
    paths = [ROOT / "data" / "vllm" / "ci" / name for name in WATCHER_STATE_WRITERS]
    assert sum(path.stat().st_size for path in paths) <= group_max_bytes("watcher_state")
    for path in paths:
        payload = json.loads(path.read_text())
        _, encoded = bounded_watcher_state(path, payload)
        assert len(encoded) <= watcher_state_max_bytes(path)
