"""Contract and executable behavior tests for hourly incident recovery markers."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "vllm" / "hourly_incident_recovery.js"
NODE = shutil.which("node")
EXPECTED_EXPORTS = {
    "FINGERPRINT_MARKER_PATTERN",
    "RECOVERED_MARKER",
    "RECOVERY_MARKER_PATTERN",
    "RECOVERY_MARKER_PREFIX",
    "ZERO_RECOVERY_MARKER",
    "advanceRecoveryStreak",
    "classifyFailureTransition",
    "parseRecoveryStreak",
    "recoveryMarker",
    "resetRecoveryStreak",
    "setRecoveryStreak",
    "suppressedDegradationRecoveryTransition",
}


def _run_node(program: str) -> None:
    if NODE is None:
        pytest.skip("Node is unavailable locally; CI runners execute this behavior test")
    result = subprocess.run(
        [NODE, "-e", textwrap.dedent(program), str(HELPER)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_commonjs_source_contract_is_dependency_free_and_exports_wiring_api():
    source = HELPER.read_text()

    assert source.startswith("'use strict';")
    assert "require(" not in source
    assert "module.exports = Object.freeze({" in source
    # The capture is part of the contract: failure handling needs the prior
    # numeric streak to decide whether a closed issue has partial recovery.
    assert (
        "const RECOVERY_MARKER_PATTERN = "
        "/<!-- hourly-ci-recovery-streak:(\\d+) -->/;"
    ) in source
    export_block = source[source.index("module.exports = Object.freeze({") :]
    for name in EXPECTED_EXPORTS:
        assert name in export_block


def test_node_parses_the_captured_streak_and_exports_the_declared_contract():
    _run_node(
        r"""
        const assert = require('node:assert/strict');
        const recovery = require(process.argv[1]);
        const expected = [
          'FINGERPRINT_MARKER_PATTERN',
          'RECOVERED_MARKER',
          'RECOVERY_MARKER_PATTERN',
          'RECOVERY_MARKER_PREFIX',
          'ZERO_RECOVERY_MARKER',
          'advanceRecoveryStreak',
          'classifyFailureTransition',
          'parseRecoveryStreak',
          'recoveryMarker',
          'resetRecoveryStreak',
          'setRecoveryStreak',
          'suppressedDegradationRecoveryTransition',
        ].sort();
        assert.deepEqual(Object.keys(recovery).sort(), expected);
        assert.equal(recovery.RECOVERY_MARKER_PATTERN.exec(
          '<!-- hourly-ci-recovery-streak:5 -->',
        )[1], '5');
        assert.equal(recovery.parseRecoveryStreak(
          'before\n<!-- hourly-ci-recovery-streak:5 -->\nafter',
        ), 5);
        assert.equal(recovery.parseRecoveryStreak('no marker'), 0);
        assert.equal(recovery.parseRecoveryStreak(
          '<!-- hourly-ci-recovery-streak:99999999999999999999 -->',
        ), 0);
        """
    )


def test_node_unhealthy_and_suppressed_recurrences_reset_partial_streaks():
    _run_node(
        r"""
        const assert = require('node:assert/strict');
        const recovery = require(process.argv[1]);
        const partial = [
          '<!-- hourly-ci-recovered -->',
          '<!-- hourly-ci-recovery-streak:4 -->',
          'incident evidence',
        ].join('\n');

        const unhealthy = recovery.resetRecoveryStreak(partial);
        const manuallySuppressed = recovery.resetRecoveryStreak(partial);
        for (const body of [unhealthy, manuallySuppressed]) {
          assert.equal(recovery.parseRecoveryStreak(body), 0);
          assert.equal(
            (body.match(/<!-- hourly-ci-recovery-streak:\d+ -->/g) || []).length,
            1,
          );
          assert.ok(body.includes(recovery.ZERO_RECOVERY_MARKER));
          assert.ok(!body.includes(recovery.RECOVERED_MARKER));
          assert.ok(body.includes('incident evidence'));
        }

        const missing = recovery.resetRecoveryStreak('incident evidence');
        assert.ok(missing.startsWith(`${recovery.ZERO_RECOVERY_MARKER}\n`));
        """
    )


def test_node_healthy_streak_advances_deterministically_and_canonicalizes_markers():
    _run_node(
        r"""
        const assert = require('node:assert/strict');
        const recovery = require(process.argv[1]);
        const duplicated = [
          '<!-- hourly-ci-recovery-streak:2 -->',
          'evidence',
          '<!-- hourly-ci-recovery-streak:1 -->',
        ].join('\n');

        const first = recovery.advanceRecoveryStreak(duplicated);
        assert.equal(first.previousStreak, 2);
        assert.equal(first.streak, 3);
        assert.equal(recovery.parseRecoveryStreak(first.body), 3);
        assert.equal(
          (first.body.match(/<!-- hourly-ci-recovery-streak:\d+ -->/g) || []).length,
          1,
        );
        assert.ok(first.body.includes('evidence'));

        const second = recovery.advanceRecoveryStreak(first.body);
        assert.equal(second.previousStreak, 3);
        assert.equal(second.streak, 4);
        assert.equal(
          second.body,
          recovery.setRecoveryStreak(first.body, 4),
        );
        assert.deepEqual(
          recovery.advanceRecoveryStreak(first.body),
          second,
        );
        """
    )


def test_recovered_closed_transient_then_same_persistent_signal_reopens():
    _run_node(
        r"""
        const assert = require('node:assert/strict');
        const recovery = require(process.argv[1]);
        const fingerprint = '<!-- hourly-ci-fingerprint:0123abcd -->';
        const recoveredClosed = [
          recovery.RECOVERED_MARKER,
          '<!-- hourly-ci-recovery-streak:6 -->',
          fingerprint,
          'incident evidence',
        ].join('\n');

        // A non-alertable transient recurrence is diagnostic only. It must not
        // turn an automatically recovered issue into a manual suppression.
        const transient = recovery.suppressedDegradationRecoveryTransition(
          recoveredClosed,
          'closed',
        );
        assert.equal(transient.shouldReset, false);
        assert.equal(transient.wasRecovered, true);
        assert.equal(transient.body, recoveredClosed);
        assert.ok(transient.body.includes(recovery.RECOVERED_MARKER));

        // Once the same fingerprint persists to the alertable threshold, the
        // recovered closed slot is re-opened. A manually closed, unrecovered
        // issue with the same fingerprint remains deliberately suppressed.
        assert.equal(
          recovery.classifyFailureTransition(
            transient.body,
            'closed',
            fingerprint,
          ),
          'reopened',
        );
        const manuallyClosed = recovery.resetRecoveryStreak(recoveredClosed);
        assert.equal(
          recovery.classifyFailureTransition(
            manuallyClosed,
            'closed',
            fingerprint,
          ),
          'manually-suppressed',
        );
        """
    )
