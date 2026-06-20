"""
Render safety tests — validate that parity data will not crash the JS dashboard.

These tests check the data contract between parity_report.json and the
ci-health.js renderGroups / overlay code. They specifically guard against
the 'Cannot read properties of undefined (reading toUpperCase)' class of
errors that occur when data fields expected by the JS are missing or have
wrong types.

The JS code calls .toUpperCase() on:
  - jl.hw          (job_links[].hw)       — line 339, 348 of ci-health.js
  - hw key          (hw_failures keys)     — line 339 of ci-health.js
  - hwNames[hw]||hw (by_hardware keys)     — line 147 of ci-health.js
  - report.arch     (dashboard.js)         — line 402 of dashboard.js
  - platform        (dashboard.js)         — line 892 of dashboard.js

These tests ensure the data always has the required string fields.
"""
import json
import re
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from vllm.ci.models import TestResult
from vllm.ci.analyzer import _compute_job_group_parity

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"


def make_result(job_name, status="passed", name="__passed__ (1)", pipeline="amd-ci",
                build_number=100, job_id="aaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                step_id="ssss-bbbb-cccc-dddd-eeeeeeeeeeee"):
    return TestResult(
        test_id=f"{job_name}::{name}",
        name=name,
        classname=job_name,
        status=status,
        duration_secs=1.0,
        failure_message="",
        job_name=job_name,
        job_id=job_id,
        step_id=step_id,
        build_number=build_number,
        pipeline=pipeline,
        date="2026-03-22",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# toUpperCase safety: ensure hw fields are always strings
# ═══════════════════════════════════════════════════════════════════════════════

class TestToUpperCaseSafety:
    """Guard against 'Cannot read properties of undefined (reading toUpperCase)'."""

    def test_amd_job_link_hw_is_string(self):
        """AMD job_links must have a string 'hw' field."""
        amd = [
            make_result("mi325_1: Kernel Test", pipeline="amd-ci", build_number=100,
                        job_id="aaaa-0001-0000-0000-000000000001"),
        ]
        groups = _compute_job_group_parity(amd, [])
        for g in groups:
            for link in g["job_links"]:
                if link["side"] == "amd":
                    assert isinstance(link["hw"], str), \
                        f"AMD link hw should be str, got {type(link['hw'])}: {link}"
                    assert link["hw"], f"AMD link hw should not be empty: {link}"

    def test_upstream_job_link_hw_is_string(self):
        """Upstream job_links must have a string 'hw' field — was missing, caused toUpperCase crash."""
        upstream = [
            make_result("Kernel Test", pipeline="ci", build_number=200,
                        job_id="bbbb-0001-0000-0000-000000000001"),
        ]
        groups = _compute_job_group_parity([], upstream)
        for g in groups:
            for link in g["job_links"]:
                if link["side"] == "upstream":
                    assert isinstance(link["hw"], str), \
                        f"Upstream link hw should be str, got {type(link['hw'])}: {link}"
                    assert link["hw"], f"Upstream link hw should not be empty: {link}"

    def test_both_sides_have_hw(self):
        """When a group has both AMD and upstream links, ALL links must have 'hw'."""
        amd = [
            make_result("mi250_1: Both Test", status="failed", name="test_fail",
                        pipeline="amd-ci", build_number=100,
                        job_id="aaaa-0001-0000-0000-000000000001"),
            make_result("mi325_1: Both Test", status="passed", name="__passed__ (3)",
                        pipeline="amd-ci", build_number=100,
                        job_id="aaaa-0002-0000-0000-000000000002"),
        ]
        upstream = [
            make_result("Both Test", pipeline="ci", build_number=200,
                        job_id="bbbb-0001-0000-0000-000000000001"),
        ]
        groups = _compute_job_group_parity(amd, upstream)
        group = next(g for g in groups if "both test" in g["name"])

        for link in group["job_links"]:
            assert "hw" in link, \
                f"Link missing 'hw' (side={link.get('side')}): {link}"
            assert isinstance(link["hw"], str) and link["hw"], \
                f"Link 'hw' must be non-empty string: {link}"

    def test_hw_failures_keys_are_strings(self):
        """hw_failures dict keys must be strings (used as .toUpperCase() in JS)."""
        amd = [
            make_result("mi325_1: Fail Test", status="failed", name="test_crash",
                        pipeline="amd-ci", build_number=100,
                        job_id="aaaa-0001-0000-0000-000000000001"),
        ]
        groups = _compute_job_group_parity(amd, [])
        group = next(g for g in groups if "fail test" in g["name"])

        hwf = group.get("hw_failures")
        if hwf:
            for key in hwf:
                assert isinstance(key, str) and key, \
                    f"hw_failures key must be non-empty string, got: {key!r}"

    def test_hw_failures_is_dict_or_none(self):
        """hw_failures must be a dict or None, never a list/string/number."""
        amd = [
            make_result("mi325_1: Type Test", pipeline="amd-ci", build_number=100,
                        job_id="aaaa-0001-0000-0000-000000000001"),
        ]
        groups = _compute_job_group_parity(amd, [])
        for g in groups:
            hwf = g.get("hw_failures")
            assert hwf is None or isinstance(hwf, dict), \
                f"hw_failures should be dict or None, got {type(hwf)}: {hwf}"

    def test_hardware_list_items_are_strings(self):
        """hardware[] items must be strings (used in hwList.map in JS)."""
        amd = [
            make_result("mi250_1: HW List Test", pipeline="amd-ci", build_number=100,
                        job_id="aaaa-0001-0000-0000-000000000001"),
            make_result("mi325_1: HW List Test", pipeline="amd-ci", build_number=100,
                        job_id="aaaa-0002-0000-0000-000000000002"),
        ]
        groups = _compute_job_group_parity(amd, [])
        group = next(g for g in groups if "hw list test" in g["name"])

        for hw in group.get("hardware", []):
            assert isinstance(hw, str) and hw, \
                f"hardware item must be non-empty string, got: {hw!r}"

    def test_group_name_is_string(self):
        """group.name must be a string (used by area() which calls .toLowerCase())."""
        amd = [
            make_result("mi325_1: Name Test", pipeline="amd-ci", build_number=100,
                        job_id="aaaa-0001-0000-0000-000000000001"),
        ]
        upstream = [
            make_result("Name Test", pipeline="ci", build_number=200,
                        job_id="bbbb-0001-0000-0000-000000000001"),
        ]
        groups = _compute_job_group_parity(amd, upstream)
        for g in groups:
            assert isinstance(g["name"], str) and g["name"], \
                f"group name must be non-empty string, got: {g['name']!r}"

    def test_upstream_without_hw_prefix_defaults_to_h100(self):
        """Upstream jobs without hardware prefix (e.g. 'Kernels') default to h100
        since the default NVIDIA queue runs on H100 hardware."""
        upstream = [
            make_result("Kernels", pipeline="ci", build_number=200,
                        job_id="bbbb-0001-0000-0000-000000000001"),
        ]
        groups = _compute_job_group_parity([], upstream)
        group = next(g for g in groups if "kernels" in g["name"])
        up_links = [l for l in group["job_links"] if l["side"] == "upstream"]
        assert len(up_links) == 1
        assert up_links[0]["hw"] == "h100", \
            f"Expected hw='h100' for unprefixed upstream job, got: {up_links[0]['hw']}"


# ═══════════════════════════════════════════════════════════════════════════════
# Render safety: data shapes the JS overlay code expects
# ═══════════════════════════════════════════════════════════════════════════════

class TestGroupRenderContract:
    """Ensure group data has the shape renderGroups() expects."""

    def test_amd_side_has_required_fields(self):
        """g.amd must have passed/failed/skipped fields (used in P/F/S display)."""
        amd = [
            make_result("mi325_1: Fields Test", pipeline="amd-ci", build_number=100,
                        job_id="aaaa-0001-0000-0000-000000000001"),
        ]
        groups = _compute_job_group_parity(amd, [])
        group = next(g for g in groups if "fields test" in g["name"])
        d = group["amd"]
        assert d is not None
        for field in ("passed", "failed", "skipped", "total"):
            assert field in d, f"amd missing '{field}': {d}"
            assert isinstance(d[field], (int, float)), f"amd.{field} not numeric: {d[field]}"

    def test_upstream_side_has_required_fields(self):
        """g.upstream must have passed/failed/skipped fields."""
        upstream = [
            make_result("Fields Test", pipeline="ci", build_number=200,
                        job_id="bbbb-0001-0000-0000-000000000001"),
        ]
        groups = _compute_job_group_parity([], upstream)
        group = next(g for g in groups if "fields test" in g["name"])
        d = group["upstream"]
        assert d is not None
        for field in ("passed", "failed", "skipped", "total"):
            assert field in d, f"upstream missing '{field}': {d}"
            assert isinstance(d[field], (int, float)), f"upstream.{field} not numeric: {d[field]}"

    def test_job_links_is_list(self):
        """job_links must always be a list, never None."""
        amd = [
            make_result("mi325_1: List Test", pipeline="amd-ci", build_number=100,
                        job_id="aaaa-0001-0000-0000-000000000001"),
        ]
        groups = _compute_job_group_parity(amd, [])
        for g in groups:
            assert isinstance(g.get("job_links"), list), \
                f"job_links should be list, got: {type(g.get('job_links'))}"

    def test_failure_tests_is_list(self):
        """failure_tests must always be a list, never None."""
        amd = [
            make_result("mi325_1: FT Test", status="failed", name="test_x",
                        pipeline="amd-ci", build_number=100,
                        job_id="aaaa-0001-0000-0000-000000000001"),
        ]
        groups = _compute_job_group_parity(amd, [])
        for g in groups:
            assert isinstance(g.get("failure_tests"), list), \
                f"failure_tests should be list, got: {type(g.get('failure_tests'))}"

    def test_hardware_is_list(self):
        """hardware must always be a list, never None."""
        amd = [
            make_result("mi325_1: HW Test", pipeline="amd-ci", build_number=100,
                        job_id="aaaa-0001-0000-0000-000000000001"),
        ]
        groups = _compute_job_group_parity(amd, [])
        for g in groups:
            assert isinstance(g.get("hardware"), list), \
                f"hardware should be list, got: {type(g.get('hardware'))}"


# ═══════════════════════════════════════════════════════════════════════════════
# Live data validation (runs against actual parity_report.json)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLiveDataRenderSafety:
    """Validate the actual parity_report.json won't crash the JS dashboard."""

    @pytest.fixture
    def parity(self):
        path = DATA / "vllm" / "ci" / "parity_report.json"
        if not path.exists():
            pytest.skip("parity_report.json not collected yet")
        return json.loads(path.read_text())

    def test_all_job_links_have_hw_string(self, parity):
        """Every job_link in live data must have a string 'hw' field."""
        bad = []
        for g in parity["job_groups"]:
            for jl in g.get("job_links", []):
                hw = jl.get("hw")
                if not isinstance(hw, str) or not hw:
                    bad.append(f"{g['name']} (side={jl.get('side')}): hw={hw!r}")
        assert not bad, (
            f"{len(bad)} job_link(s) with missing/invalid 'hw' — will crash "
            f"toUpperCase in ci-health.js:\n" + "\n".join(bad[:20])
        )

    def test_all_hw_failures_have_string_keys(self, parity):
        """Every hw_failures key in live data must be a string."""
        bad = []
        for g in parity["job_groups"]:
            hwf = g.get("hw_failures")
            if not hwf or not isinstance(hwf, dict):
                continue
            for key in hwf:
                if not isinstance(key, str) or not key:
                    bad.append(f"{g['name']}: key={key!r}")
        assert not bad, f"Non-string hw_failures keys: {bad[:10]}"

    def test_all_group_names_are_strings(self, parity):
        """Every group name must be a non-empty string."""
        for g in parity["job_groups"]:
            assert isinstance(g.get("name"), str) and g["name"], \
                f"Bad group name: {g.get('name')!r}"

    def test_all_hardware_items_are_strings(self, parity):
        """Every hardware[] item must be a string."""
        bad = []
        for g in parity["job_groups"]:
            for hw in g.get("hardware", []):
                if not isinstance(hw, str):
                    bad.append(f"{g['name']}: {hw!r}")
        assert not bad, f"Non-string hardware items: {bad[:10]}"

    def test_no_null_entries_in_job_links(self, parity):
        """job_links must not contain null entries."""
        bad = []
        for g in parity["job_groups"]:
            for i, jl in enumerate(g.get("job_links", [])):
                if jl is None:
                    bad.append(f"{g['name']}: job_links[{i}] is null")
        assert not bad, f"Null job_link entries: {bad[:10]}"

    def test_sides_have_numeric_counts(self, parity):
        """amd/upstream passed/failed/skipped must be numeric (not null/string)."""
        bad = []
        for g in parity["job_groups"]:
            for side_name in ("amd", "upstream"):
                d = g.get(side_name)
                if not d:
                    continue
                for field in ("passed", "failed", "skipped"):
                    val = d.get(field)
                    if val is not None and not isinstance(val, (int, float)):
                        bad.append(f"{g['name']}.{side_name}.{field}={val!r}")
        assert not bad, f"Non-numeric count fields: {bad[:10]}"
