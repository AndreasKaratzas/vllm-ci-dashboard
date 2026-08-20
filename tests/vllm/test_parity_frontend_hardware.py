"""Focused frontend regressions for side-aware parity hardware counts."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "docs" / "assets" / "js"


def test_hardware_renderers_use_separate_side_maps() -> None:
    health = (JS / "ci-health.js").read_text(encoding="utf-8")
    dashboard = (JS / "dashboard.js").read_text(encoding="utf-8")

    assert "buildParityHardwareGroupMap(allMerged,'amd')" in health
    assert "buildParityHardwareGroupMap(allMerged,'upstream')" in health
    assert "legacyHardwareGroupMap('amd')" in health
    assert "legacyHardwareGroupMap('upstream')" in health
    assert "_buildHwTable(hws, hwNames, amdHwGroupMap" in health
    assert "_buildHwTable(others, hwNames, upstreamHwGroupMap" in health
    assert "buildParityHardwareGroupMap(merged, 'amd')" in dashboard


def test_side_aware_hardware_helpers_and_merges_execute() -> None:
    if not shutil.which("node"):
        pytest.skip("node is not available")

    script = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('function _isAmdHardware');
const end = source.indexOf('function showGroupOverlay', start);
if (start < 0 || end < 0) throw new Error('parity helper block not found');

function _stripShardIndex(value) { return String(value || '').replace(/\s+\d+$/, ''); }
function _parityFamilyName(value) { return String(value || '').toLowerCase(); }
eval(source.slice(start, end));

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const explicitGroups = [
  {
    name: 'upstream mi only', amd: null,
    upstream: {failed: 1, error: 0, canceled: 0},
    hardware: ['mi300'], amd_hardware: [], upstream_hardware: ['mi300'],
    hw_failures: {mi300: 1}, amd_hw_failures: {}, upstream_hw_failures: {mi300: 1},
    job_links: [{side: 'upstream', hw: 'mi300'}], backfilled: false,
  },
  {
    name: 'amd mi only', amd: {passed: 1, failed: 0, error: 0, canceled: 0},
    upstream: null, hardware: ['mi300'], amd_hardware: ['mi300'], upstream_hardware: [],
    amd_hw_failures: {}, upstream_hw_failures: {},
    job_links: [{side: 'amd', hw: 'mi300'}], backfilled: false,
  },
  {
    name: 'paired mi queues', amd: {passed: 1, failed: 0, error: 0, canceled: 0},
    upstream: {passed: 1, failed: 0, error: 0, canceled: 0},
    hardware: ['mi250', 'mi300'], amd_hardware: ['mi250'], upstream_hardware: ['mi300'],
    amd_hw_failures: {}, upstream_hw_failures: {}, backfilled: false,
  },
  {
    name: 'scheduled upstream mi', amd: null, upstream: null, backfilled: true,
    hardware: ['mi355'], amd_hardware: [], upstream_hardware: ['mi355'],
    amd_hw_failures: {}, upstream_hw_failures: {},
  },
];

const amdMap = buildParityHardwareGroupMap(explicitGroups, 'amd');
const upstreamMap = buildParityHardwareGroupMap(explicitGroups, 'upstream');
assert(!amdMap.mi355, 'explicit empty amd_hardware must exclude scheduled upstream MI');
assert(amdMap.mi300.passing.length === 1, 'upstream MI must not inflate AMD MI300');
assert(amdMap.mi250.passing.length === 1, 'paired AMD hardware should remain in AMD map');
assert(upstreamMap.mi300.failing.length === 1, 'upstream MI failure belongs in upstream map');
assert(upstreamMap.mi300.passing.length === 1, 'paired upstream MI belongs in upstream map');
assert(upstreamMap.mi355.pending.length === 1, 'scheduled upstream-only row should remain pending');

const legacyUpstreamMi = {
  upstream: {passed: 1, failed: 0}, hardware: ['mi325'],
  job_links: [{side: 'upstream', hw: 'mi325'}],
};
const legacyAmdMi = {
  amd: {passed: 1, failed: 0}, hardware: ['mi325'],
  job_links: [{side: 'amd', hw: 'mi325'}],
};
assert(parityHardwareForSide(legacyUpstreamMi, 'amd').length === 0,
  'legacy fallback must honor upstream job-link ownership before MI prefix');
assert(parityHardwareForSide(legacyUpstreamMi, 'upstream')[0] === 'mi325',
  'legacy upstream MI should remain visible upstream');
assert(parityHardwareForSide(legacyAmdMi, 'amd')[0] === 'mi325',
  'legacy amd-ci MI should remain visible on AMD');

const merged = mergeShardedGroups([
  {name: 'family 1', amd_hardware: [], upstream_hardware: ['mi300'],
   amd_hw_failures: {}, upstream_hw_failures: {mi300: 1}},
  {name: 'family 2', amd_hardware: [], upstream_hardware: ['mi300'],
   amd_hw_failures: {}, upstream_hw_failures: {mi300: 2}},
])[0];
assert(Object.prototype.hasOwnProperty.call(merged, 'amd_hardware'),
  'merge must preserve an explicit empty AMD list');
assert(merged.amd_hardware.length === 0, 'explicit empty AMD list must stay empty');
assert(merged.upstream_hardware.length === 1, 'merged side hardware should be deduplicated');
assert(merged.upstream_hw_failures.mi300 === 3, 'shard failures should sum by side');

const familyMerged = mergeParityGroups([
  {name: 'A', family_name: 'family', family_key: 'family', amd_job_name: 'amd-a',
   upstream_job_name: 'up-one', amd_hardware: ['mi250'], upstream_hardware: ['mi300'],
   amd_hw_failures: {mi250: 1}, upstream_hw_failures: {mi300: 1}},
  {name: 'B', family_name: 'family', family_key: 'family', amd_job_name: 'amd-b',
   upstream_job_name: 'up-one', amd_hardware: ['mi355'], upstream_hardware: ['mi300'],
   amd_hw_failures: {mi355: 1}, upstream_hw_failures: {mi300: 1}},
])[0];
assert(familyMerged.amd_hardware.join(',') === 'mi250,mi355',
  'family merge should retain distinct AMD hardware');
assert(familyMerged.upstream_hardware.length === 1,
  'family merge should deduplicate upstream hardware');
assert(familyMerged.upstream_hw_failures.mi300 === 1,
  'one upstream job shared by AMD counterparts must not double-count failures');
"""
    result = subprocess.run(
        ["node", "-e", script, str(JS / "utils.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
