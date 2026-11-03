"""ci-infra AMD queue routing becomes the dashboard's queue registry."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from vllm import amd_queue_registry as registry

CONSTANTS = '''
class DeviceType(str, Enum):
    AMD_CPU = "amd_cpu"
    AMD_MI250_1 = "mi250_1"
    AMD_MI250_8 = "mi250_8"
    AMD_MI325_1 = "mi325_1"
    AMD_MI355_DPX = "mi355_dpx"
    AMD_MI355_1 = "mi355_1"


class AgentQueue(str, Enum):
    AMD_CPU = "amd-cpu"
    AMD_MI250 = "amd_mi250"
    AMD_MI325_1 = "amd_mi325_1"
    AMD_MI355_DPX = "amd_mi355_dpx"
    AMD_MI355_1 = "amd_mi355_1"
'''
AMD = '''
AMD_DEVICE_CONFIGS = {
    DeviceType.AMD_MI250_1.value: AmdDeviceConfig(AgentQueue.AMD_MI250, 1, gpu_size_tag=True),
    DeviceType.AMD_MI250_8.value: AmdDeviceConfig(AgentQueue.AMD_MI250, 8, gpu_size_tag=True),
    DeviceType.AMD_MI325_1.value: AmdDeviceConfig(AgentQueue.AMD_MI325_1, 1),
    DeviceType.AMD_MI355_DPX.value: AmdDeviceConfig(AgentQueue.AMD_MI355_DPX, 1, uses_dra=True),
    DeviceType.AMD_MI355_1.value: AmdDeviceConfig(AgentQueue.AMD_MI355_1, 1),
}
'''
SHA = "a" * 40


def _registry(fetched_at: str = "2026-11-03T00:00:00Z", commit: str = SHA) -> dict:
    return registry.build_registry(
        CONSTANTS, AMD, commit=commit, fetched_at=datetime.fromisoformat(fetched_at)
    )


def test_routing_comes_from_ci_infra_device_configs_not_queue_name_patterns():
    parsed = _registry()

    assert registry.tracked_queues(parsed) == (
        "amd_mi250",
        "amd_mi325_1",
        "amd_mi355_dpx",
        "amd_mi355_1",
    )
    assert registry.target_queues(parsed) == ("amd_mi250", "amd_mi355_dpx", "amd_mi355_1")
    assert registry.shared_queues(parsed) == {"amd_mi250"}
    assert registry.physical_queue("amd_mi250_8", parsed) == "amd_mi250"
    assert registry.lanes(parsed)["amd_mi355_dpx"]["gpus_per_job"] == 1
    assert parsed["source"]["commit"] == SHA


def test_upstream_refactor_fails_closed_instead_of_emptying_the_registry():
    with pytest.raises(registry.RegistryError, match="AMD_DEVICE_CONFIGS"):
        registry.build_registry(
            CONSTANTS, "CONFIGS = {}\n", commit=SHA, fetched_at=datetime.now(timezone.utc)
        )


def test_unreachable_upstream_keeps_the_newest_last_good_registry(tmp_path, monkeypatch):
    path = tmp_path / "amd_queue_registry.json"
    path.write_text(json.dumps(_registry("2026-11-01T00:00:00Z")))
    newer = _registry("2026-11-02T00:00:00Z", commit="b" * 40)
    monkeypatch.setattr(registry, "_git_registry", lambda ref, root: newer)

    def unreachable():
        raise OSError("network down")

    kept = registry.refresh(
        path,
        fallback_git_refs=["origin/dashboard-state"],
        fetch=unreachable,
        now=datetime(2026, 11, 3, tzinfo=timezone.utc),
    )

    assert kept["source"]["commit"] == "b" * 40
    assert json.loads(path.read_text()) == kept
    assert registry.registry_provenance(kept)["fallback"] == {
        "reason": "OSError: network down", "attempted_at": "2026-11-03T00:00:00Z",
    }
    assert "fallback" not in registry.refresh(path, fetch=_registry)["source"]

    missing = tmp_path / "missing.json"
    with pytest.raises(registry.RegistryError, match="no last-good registry"):
        registry.refresh(missing, fetch=unreachable)
    assert not missing.exists()


def test_device_queues_the_dashboard_cannot_map_are_recorded_not_dropped():
    constants = CONSTANTS.replace(
        '    AMD_MI355_1 = "mi355_1"\n', '    AMD_MI355_1 = "mi355_1"\n    AMD_MI355_X = "mi355_x"\n', 1
    ) + '    AMD_MI355_X = "amd-mi355-x"\n'
    amd = AMD.replace(
        "}\n",
        "    DeviceType.AMD_MI355_X.value: AmdDeviceConfig(queue=AgentQueue.AMD_MI355_X, default_gpu_count=1),\n}\n",
    )

    parsed = registry.build_registry(constants, amd, commit=SHA, fetched_at=datetime.now(timezone.utc))

    assert "amd-mi355-x" not in registry.tracked_queues(parsed)
    assert parsed["unrecognized_queues"] == [{"device": "mi355_x", "queue": "amd-mi355-x"}]
    assert registry.registry_provenance(parsed)["unrecognized_queues"] == parsed["unrecognized_queues"]
