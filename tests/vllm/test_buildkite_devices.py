"""vLLM steps in the `devices:` form read like the legacy `device` + `mirror` form.

The compile rules are ci-infra's (step_vendors.py); these tests pin the ones
the dashboard depends on and check that each reader sees the same jobs from a
legacy step and from its `devices:` rewrite.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from vllm import build_test_group_parity as parity
from vllm import collect_capacity_monitor as capacity
from vllm import config_parity
from vllm import main_ci_definitions as source
from vllm.buildkite_devices import (
    DEVICES,
    DeviceStepError,
    Vendor,
    amd_job_fields,
    compile_device_step,
    expand_steps_indexed,
    is_exact_mirror,
    resolve_step,
)

SHA = "a" * 40
FILE = ".buildkite/test_areas/attention.yaml"
CI_CONFIG = {"job_dirs": [".buildkite/test_areas"]}


def _yaml(text: str):
    return yaml.safe_load(text)


ATTENTION = _yaml(
    """
label: V1 Attention
key: v1-attention-h100-mi300
devices:
  h100:
    timeout_in_minutes: 85
    source_file_dependencies:
      - vllm/v1/hisparse/
    commands:
      - pytest -v -s v1/attention
    variants:
      eager:
        env:
          VLLM_ENFORCE_EAGER: "1"
      extended:
        timeout_in_minutes: 90
        commands:
          - pytest -v -s --runslow v1/attention
  b200-k8s:
    same_as: h100
    timeout_in_minutes: 30
  mi300_1:
    timeout_in_minutes: 125
    source_file_dependencies:
      - vllm/platforms/rocm.py
    commands:
      - pytest -v -s v1/attention --ignore=v1/attention/test_mla_backends.py
  mi355_1:
    same_as: mi300_1
automatic_shard: true
source_file_dependencies:
  - vllm/v1/attention
  - tests/v1/attention
"""
)


def test_registry_carries_the_label_tags_key_tags_and_pool_gpus():
    assert (DEVICES["h100"].vendor, DEVICES["h100"].tag, DEVICES["h100"].key_tag) == (
        Vendor.NVIDIA, "H100", "h100")
    assert (DEVICES["b200-k8s"].tag, DEVICES["b200-k8s"].key_tag) == ("B200", "b200")
    assert DEVICES["h200_35gb"].tag == "H200 MIG 35GB"
    assert (DEVICES["mi300_dpx"].tag, DEVICES["mi300_dpx"].key_tag) == ("MI300 DPX", "mi300-dpx")
    assert [DEVICES[name].gpus for name in ("mi355_1", "mi355_2", "mi355_4")] == [1, 2, 4]
    assert DEVICES["mi355_4"].image == "image-build-amd"
    assert (DEVICES["cpu-small"].vendor, DEVICES["cpu-small"].tag) == (Vendor.CPU, "CPU")


def test_devices_compile_keys_labels_and_merges_like_ci_infra():
    jobs = {job.key: job for job in resolve_step(ATTENTION, ["image-build"])}
    assert list(jobs) == [
        "v1-attention-h100-mi300",
        "v1-attention-h100-mi300-eager",
        "v1-attention-h100-mi300-extended",
        "v1-attention-h100-mi300-b200",
        "amd-v1-attention-h100-mi300",
        "amd-v1-attention-h100-mi300-mi355",
    ]
    base = jobs["v1-attention-h100-mi300"]
    assert base.label == ":nvidia: (H100) V1 Attention"
    # Shared dependencies first, then the device's own.
    assert base.fields["source_file_dependencies"] == [
        "vllm/v1/attention", "tests/v1/attention", "vllm/v1/hisparse/"]
    eager = jobs["v1-attention-h100-mi300-eager"]
    assert eager.label == ":nvidia: (H100) V1 Attention [eager]"
    assert eager.fields["env"] == {"VLLM_ENFORCE_EAGER": "1"}
    assert eager.fields["timeout_in_minutes"] == 85
    extended = jobs["v1-attention-h100-mi300-extended"]
    assert extended.fields["commands"] == ["pytest -v -s --runslow v1/attention"]
    # same_as copies the sibling (not its variants); written fields replace.
    b200 = jobs["v1-attention-h100-mi300-b200"]
    assert (b200.label, b200.fields["timeout_in_minutes"]) == (":nvidia: (B200) V1 Attention", 30)
    assert b200.fields["commands"] == base.fields["commands"]
    amd = jobs["amd-v1-attention-h100-mi300"]
    assert amd.label == ":amd: (MI300) V1 Attention"
    assert amd.depends_on == ["image-build-amd"]
    mi355 = jobs["amd-v1-attention-h100-mi300-mi355"]
    assert (mi355.label, mi355.fields["timeout_in_minutes"]) == (":amd: (MI355) V1 Attention", 125)


def test_a_variant_label_replaces_only_the_title():
    step = _yaml(
        "label: T\nkey: t\ndevices:\n  h100:\n    commands:\n      - x\n"
        "    variants:\n      slow:\n        label: T Slow\n        timeout_in_minutes: 9\n"
    )
    labels = [job.label for job in resolve_step(step)]
    assert labels == [":nvidia: (H100) T", ":nvidia: (H100) T Slow [slow]"]


def test_first_nvidia_job_is_the_step_and_its_amd_job_an_exact_mirror():
    steps = compile_device_step(ATTENTION, ["image-build"])
    assert [step["key"] for step in steps] == [
        "v1-attention-h100-mi300",
        "v1-attention-h100-mi300-eager",
        "v1-attention-h100-mi300-extended",
        "v1-attention-h100-mi300-b200",
        "amd-v1-attention-h100-mi300-mi355",
    ]
    mirror = steps[0]["mirror"]["amd"]
    assert is_exact_mirror(mirror)
    assert "key" not in mirror
    assert (mirror["device"], mirror["dind"], mirror["num_devices"]) == ("mi300_1", False, 1)
    assert all("mirror" not in step for step in steps[1:])


def test_nightly_label_pinned_key_and_cpu_registry_defaults():
    amd_only = _yaml(
        """
label: Fusion E2E TP2 AR-RMS (Inductor Partition)
key: fusion-e2e-tp2-ar-rms-inductor-partition
devices:
  mi355_2:
    key: fusion-e2e-tp2-ar-rms-inductor-partition-amd
    optional: true
    timeout_in_minutes: 65
commands:
  - pytest -v -s tests/compile/fusions_e2e/test_tp2_ar_rms.py
"""
    )
    [step] = compile_device_step(amd_only, ["image-build"])
    assert step["key"] == "fusion-e2e-tp2-ar-rms-inductor-partition-amd"
    assert step["label"] == ":amd: (MI355) [Nightly] Fusion E2E TP2 AR-RMS (Inductor Partition)"
    assert (step["num_devices"], step["depends_on"]) == (2, ["image-build-amd"])
    cpu = _yaml("label: V1 Others\nkey: v1-others-cpu\ndevices:\n  cpu-small:\ncommands:\n  - pytest v1\n")
    [step] = compile_device_step(cpu, ["image-build"])
    assert (step["label"], step["depends_on"]) == (":computer: (CPU) V1 Others", ["image-build-cpu"])


@pytest.mark.parametrize(
    ("code", "text"),
    [
        ("E1", "devices:\n  h999:\n"),
        ("E4", "timeout_in_minutes: 5\ndevices:\n  h100:\n"),
        ("E3", "source_file_dependencies:\n  - a/\ndevices:\n  h100:\n    source_file_dependencies:\n      - a/\n"),
        ("E3", "commands:\n  - x\ndevices:\n  h100:\n    commands:\n      - y\n"),
        ("E5", "devices:\n  h100:\n    commands:\n      - x\n  mi300_1:\n    same_as: h100\n"),
        ("E7", "devices:\n  mi355_4:\n    num_devices: 4\n"),
        ("E8", "devices:\n  mi355_1:\n  mi355_4:\n"),
        ("E2", "devices:\n  h100:\n    mirror:\n      amd:\n        device: mi300_1\n"),
        ("E6", "devices:\n  h100:\n    variants:\n      eager:\n"),
    ],
    ids=["unknown-device", "step-machine-field", "shadowed-dep", "shadowed-commands",
         "cross-vendor-same-as", "amd-num-devices", "key-tag-collision", "mirror-in-device",
         "empty-variant"],
)
def test_rules_ci_infra_rejects_are_rejected(code, text):
    with pytest.raises(DeviceStepError) as error:
        resolve_step(_yaml("label: T\nkey: t\n" + text))
    assert error.value.code == code


LEGACY = """
group: Attention
depends_on:
  - image-build
steps:
- label: ":nvidia: (H100) V1 Attention"
  key: v1-attention
  device: h100
  timeout_in_minutes: 85
  automatic_shard: true
  env:
    SHARED: "1"
  source_file_dependencies:
  - vllm/v1/attention
  - vllm/v1/hisparse/
  commands:
  - pytest -v -s v1/attention
  mirror:
    amd:
      label: ":amd: (MI300) V1 Attention"
      device: mi300_1
      dind: false
      timeout_in_minutes: 125
      env:
        AMD_ONLY: "1"
      source_file_dependencies:
      - vllm/platforms/rocm.py
      - vllm/v1/attention
      commands:
      - pytest -v -s v1/attention --ignore=v1/attention/test_mla_backends.py
      depends_on:
      - image-build-amd
- label: ":nvidia: (H100) Spec Decode"
  key: spec-decode
  device: h100
  timeout_in_minutes: 40
  working_dir: /vllm-workspace/tests
  source_file_dependencies:
  - vllm/v1/spec_decode
  commands:
  - pytest -v -s v1/spec_decode
  mirror:
    amd:
      label: ":amd: (MI300 DPX) Spec Decode"
      device: mi300_dpx
      dind: false
      timeout_in_minutes: 50
      depends_on:
      - image-build-amd
- label: ":amd: (MI355) [Nightly] AMD Only"
  key: amd-only-amd
  device: mi355_2
  optional: true
  dind: false
  timeout_in_minutes: 65
  num_devices: 2
  commands:
  - pytest amd_only
  depends_on:
  - image-build-amd
- label: ":computer: (CPU) V1 Others"
  key: v1-others-cpu
  device: cpu-small
  commands:
  - pytest v1
  depends_on:
  - image-build-cpu
"""

DEVICES_FORM = """
group: Attention
depends_on:
  - image-build
steps:
- label: V1 Attention
  key: v1-attention
  devices:
    h100:
      timeout_in_minutes: 85
      commands:
        - pytest -v -s v1/attention
    mi300_1:
      timeout_in_minutes: 125
      env:
        AMD_ONLY: "1"
      source_file_dependencies:
        - vllm/platforms/rocm.py
      commands:
        - pytest -v -s v1/attention --ignore=v1/attention/test_mla_backends.py
  automatic_shard: true
  env:
    SHARED: "1"
  source_file_dependencies:
    - vllm/v1/attention
    - vllm/v1/hisparse/
- label: Spec Decode
  key: spec-decode
  devices:
    h100:
      timeout_in_minutes: 40
    mi300_dpx:
      timeout_in_minutes: 50
  source_file_dependencies:
    - vllm/v1/spec_decode
  commands:
    - pytest -v -s v1/spec_decode
- label: AMD Only
  key: amd-only
  devices:
    mi355_2:
      key: amd-only-amd
      optional: true
      timeout_in_minutes: 65
  commands:
    - pytest amd_only
- label: V1 Others
  key: v1-others-cpu
  devices:
    cpu-small:
  commands:
    - pytest v1
"""


def _snapshot(text: str) -> source.MainCISnapshot:
    return source.MainCISnapshot(
        SHA, {source.CI_CONFIG: CI_CONFIG, FILE: _yaml(text)}, "2026-11-24T00:00:00Z", "b" * 40)


def _route_view(route: dict) -> dict:
    fields = ("definition_id", "upstream_definition_id", "source_kind", "key", "label", "device",
              "agent_pool", "num_devices", "parallelism", "timeout_in_minutes", "commands",
              "working_dir", "env", "no_gpu", "source_file", "area", "yaml_index")
    view = {name: route.get(name) for name in fields}
    view["optional"] = bool(route.get("optional"))
    view["source_file_dependencies"] = sorted(route.get("source_file_dependencies") or [])
    return view


def test_amd_routes_are_the_same_jobs_in_both_forms():
    legacy = source.amd_source_steps(_snapshot(LEGACY), include_cpu=True)
    devices = source.amd_source_steps(_snapshot(DEVICES_FORM), include_cpu=True)
    assert [_route_view(route) for route in devices] == [_route_view(route) for route in legacy]
    attention = next(route for route in devices if route["key"] == "amd-v1-attention")
    assert attention["source_kind"] == "inline_mirror"
    assert attention["definition_id"] == f"{FILE}#v1-attention"
    # The generator's AMD job: its own dependencies plus the step's, the
    # step's env updated with its own, only its own timeout, the pool's GPUs.
    assert sorted(attention["source_file_dependencies"]) == [
        "vllm/platforms/rocm.py", "vllm/v1/attention", "vllm/v1/hisparse/"]
    assert attention["env"] == {"SHARED": "1", "AMD_ONLY": "1"}
    assert (attention["timeout_in_minutes"], attention["num_devices"]) == (125, 1)
    assert "_exact" not in attention
    native = next(route for route in devices if route["key"] == "amd-only-amd")
    assert native["source_kind"] == "native_amd"
    assert native["label"] == ":amd: (MI355) [Nightly] AMD Only"


def test_source_steps_keep_the_written_step_index():
    rows = source.source_steps(_snapshot(DEVICES_FORM))
    assert [(row["key"], row["yaml_index"]) for row in rows] == [
        ("v1-attention", 0), ("spec-decode", 1), ("amd-only-amd", 2), ("v1-others-cpu", 3)]
    assert all(row["working_dir"] == "/vllm-workspace/tests" for row in rows)


def test_a_devices_step_that_does_not_compile_fails_loudly():
    broken = "steps:\n- label: T\n  key: t\n  devices:\n    h999:\n"
    with pytest.raises(ValueError, match="does not compile.*E1"):
        source.source_steps(_snapshot(broken))


def test_extra_devices_and_variants_are_their_own_definitions():
    text = DEVICES_FORM.replace(
        "    h100:\n      timeout_in_minutes: 40\n",
        "    h100:\n      timeout_in_minutes: 40\n      variants:\n        slow:\n"
        "          timeout_in_minutes: 80\n    b200-k8s:\n      timeout_in_minutes: 30\n",
    ).replace("    mi300_dpx:\n      timeout_in_minutes: 50\n",
              "    mi300_dpx:\n      timeout_in_minutes: 50\n    mi355_1:\n      same_as: mi300_dpx\n")
    snapshot = _snapshot(text)
    keys = [row["key"] for row in source.source_steps(snapshot)]
    assert keys[1:5] == ["spec-decode", "spec-decode-slow", "spec-decode-b200", "amd-spec-decode-mi355"]
    routes = {route["key"]: route for route in source.amd_source_steps(snapshot)}
    assert routes["amd-spec-decode"]["source_kind"] == "inline_mirror"
    assert routes["amd-spec-decode-mi355"]["source_kind"] == "native_amd"
    assert routes["amd-spec-decode-mi355"]["commands"] == ["pytest -v -s v1/spec_decode"]
    rows = parity.test_group_inventory_rows(snapshot)
    kinds = {row["definition_id"].rsplit("#", 1)[1]: row["kind"] for row in rows}
    assert kinds["spec-decode"] == "gpu_mirror"
    assert kinds["amd-spec-decode-mi355"] == "native"
    # An AMD job other than the paired amd-<key> one is `native` (never
    # coverage), but its step's CUDA job is still named.
    by_id = {row["definition_id"].rsplit("#", 1)[1]: row for row in rows}
    cuda = next(row for row in source.source_steps(snapshot) if row["key"] == "spec-decode")
    extra = by_id["amd-spec-decode-mi355"]
    assert extra["upstream_label"] is None and extra["nvidia_definition_id"] is None
    assert (extra["step_cuda_definition_id"], extra["step_cuda_label"]) == (
        cuda["definition_id"], cuda["label"])
    assert by_id["spec-decode"]["step_cuda_label"] == cuda["label"]
    assert by_id["amd-only-amd"]["kind"] == "native"
    assert by_id["amd-only-amd"]["step_cuda_definition_id"] is None
    assert "no upstream CUDA definition" not in parity.INVENTORY_KINDS["native"]


def test_config_parity_reads_the_same_amd_jobs_in_both_forms():
    def mirrors(text: str) -> list[dict]:
        return config_parity._parse_nvidia_data([(FILE, _yaml(text))])[1]

    legacy, devices = mirrors(LEGACY), mirrors(DEVICES_FORM)
    assert devices == legacy
    by_key = {row["amd_definition_id"]: row for row in devices}
    assert by_key[f"{FILE}#amd-v1-attention"]["commands_overridden"] is True
    # Shared commands are written once in the `devices:` form: not an override.
    assert by_key[f"{FILE}#amd-spec-decode"]["commands_overridden"] is False


def test_capacity_monitor_counts_the_same_amd_jobs_in_both_forms(tmp_path: Path):
    def groups(text: str, name: str) -> list[dict]:
        repo = tmp_path / name
        path = repo / FILE
        path.parent.mkdir(parents=True)
        path.write_text(text)
        return capacity.parse_amd_mirror_groups(repo)

    legacy, devices = groups(LEGACY, "legacy"), groups(DEVICES_FORM, "devices")
    assert devices == legacy
    assert [group["key"] for group in devices] == ["v1-attention", "spec-decode"]
    assert devices[0]["source_file_dependencies"] == [
        "vllm/platforms/rocm.py", "vllm/v1/attention", "vllm/v1/hisparse/"]
    assert devices[0]["timeout_in_minutes"] == 125


def test_legacy_steps_are_returned_unchanged_and_documents_are_not_modified():
    document = _yaml(LEGACY)
    before = yaml.safe_dump(document)
    expanded = expand_steps_indexed(document)
    assert [index for index, _ in expanded] == [0, 1, 2, 3]
    assert yaml.safe_dump(document) == before
    step = expanded[1][1]
    amd = amd_job_fields(step, step["mirror"]["amd"])
    # A legacy block inherits what it does not set, except the timeout.
    assert amd["commands"] == ["pytest -v -s v1/spec_decode"]
    assert amd["timeout_in_minutes"] == 50
    assert amd["source_file_dependencies"] == ["vllm/v1/spec_decode"]
