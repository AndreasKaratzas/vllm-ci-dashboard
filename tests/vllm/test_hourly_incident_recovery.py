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
    "CURRENT_INCIDENT_MARKER",
    "DNS_ONLY_INCIDENT_MARKER",
    "FINGERPRINT_MARKER_PATTERN",
    "HOURLY_OWNER_LABEL",
    "LEGACY_SIGNATURE",
    "OWNERSHIP_MARKER",
    "QUEUE_ONLY_INCIDENT_MARKER",
    "RECOVERED_MARKER",
    "RECOVERY_CREDIT_PATTERN",
    "RECOVERY_CREDIT_PREFIX",
    "RECOVERY_MARKER_PATTERN",
    "RECOVERY_MARKER_PREFIX",
    "SUPERSEDED_MARKER",
    "ZERO_RECOVERY_MARKER",
    "advanceRecoveryStreak",
    "classifyFailureTransition",
    "closeHourlyIncident",
    "ensureOwnerLabel",
    "findCanonicalIncident",
    "hasExactMarker",
    "isStrictLegacySurfaceOnlyIncident",
    "issueHasLabel",
    "parseRecoveryStreak",
    "recoveryCreditMarker",
    "recoveryMarker",
    "resetRecoveryStreak",
    "retireOwnerLabel",
    "selectCanonicalIncident",
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
    assert "workflowStatusLines.length === 1" in source
    assert "deploymentLines.length === 1" in source
    assert "**Workflow status before incident handling:** `success`" in source
    assert (
        "**Deployment:** commit=`success`, site_assembly=`success`, "
        "' +\n      'deploy=`success`, post_deploy_validation=`success`"
    ) in source


def test_node_parses_the_captured_streak_and_exports_the_declared_contract():
    _run_node(
        r"""
        const assert = require('node:assert/strict');
        const recovery = require(process.argv[1]);
        const expected = [
          'CURRENT_INCIDENT_MARKER',
          'DNS_ONLY_INCIDENT_MARKER',
          'FINGERPRINT_MARKER_PATTERN',
          'HOURLY_OWNER_LABEL',
          'LEGACY_SIGNATURE',
          'OWNERSHIP_MARKER',
          'QUEUE_ONLY_INCIDENT_MARKER',
          'RECOVERED_MARKER',
          'RECOVERY_CREDIT_PATTERN',
          'RECOVERY_CREDIT_PREFIX',
          'RECOVERY_MARKER_PATTERN',
          'RECOVERY_MARKER_PREFIX',
          'SUPERSEDED_MARKER',
          'ZERO_RECOVERY_MARKER',
          'advanceRecoveryStreak',
          'classifyFailureTransition',
          'closeHourlyIncident',
          'ensureOwnerLabel',
          'findCanonicalIncident',
          'hasExactMarker',
          'isStrictLegacySurfaceOnlyIncident',
          'issueHasLabel',
          'parseRecoveryStreak',
          'recoveryCreditMarker',
          'recoveryMarker',
          'resetRecoveryStreak',
          'retireOwnerLabel',
          'selectCanonicalIncident',
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
        const firstState = '1'.repeat(40);
        const secondState = '2'.repeat(40);

        const first = recovery.advanceRecoveryStreak(duplicated, firstState);
        assert.equal(first.previousStreak, 2);
        assert.equal(first.streak, 3);
        assert.equal(first.credited, true);
        assert.equal(recovery.parseRecoveryStreak(first.body), 3);
        assert.equal(
          (first.body.match(/<!-- hourly-ci-recovery-streak:\d+ -->/g) || []).length,
          1,
        );
        assert.ok(first.body.includes('evidence'));

        const duplicate = recovery.advanceRecoveryStreak(first.body, firstState);
        assert.equal(duplicate.previousStreak, 3);
        assert.equal(duplicate.streak, 3);
        assert.equal(duplicate.credited, false);
        assert.equal(duplicate.body, first.body);

        const second = recovery.advanceRecoveryStreak(first.body, secondState);
        assert.equal(second.previousStreak, 3);
        assert.equal(second.streak, 4);
        assert.equal(second.credited, true);
        assert.ok(second.body.includes(recovery.recoveryCreditMarker(firstState)));
        assert.ok(second.body.includes(recovery.recoveryCreditMarker(secondState)));
        const reset = recovery.resetRecoveryStreak(second.body);
        assert.ok(!reset.includes(recovery.RECOVERY_CREDIT_PREFIX));
        """
    )


def test_node_recognizes_exact_legacy_queue_fallback_shape_from_issue_568():
    _run_node(
        r"""
        const assert = require('node:assert/strict');
        const recovery = require(process.argv[1]);
        const issue568 = [
          '<!-- ci-failure-owner:hourly-master -->',
          '<!-- hourly-ci-current-incident:v1 -->',
          '## Degraded Publication — 2026-09-01',
          '**Summary:** `publication fallback`',
          '**Publication selector:** outcome=`success`, mode=`fallback`',
          '**Degraded surfaces:** queue',
          '**Workflow status before incident handling:** `success`',
          '**Deployment:** commit=`success`, site_assembly=`success`, deploy=`success`, post_deploy_validation=`success`',
          '**Live publication audit:** outcome=`success`, summary=`audit: 0 errors`',
        ].join('\n');
        assert.equal(
          recovery.isStrictLegacySurfaceOnlyIncident(issue568, 'queue'),
          true,
        );
        assert.equal(
          recovery.isStrictLegacySurfaceOnlyIncident(issue568, 'dns_health'),
          false,
        );
        for (const unsafe of [
          `${issue568}\n**Failing deterministic tests:**`,
          `${issue568}\ndeployment failed: deploy`,
          `${issue568}\nworkflow step failed before publication completed`,
          issue568.replace(
            '**Workflow status before incident handling:** `success`',
            '**Workflow status before incident handling:** `failure`',
          ),
          issue568.replace(
            'commit=`success`, site_assembly=`success`, deploy=`success`, post_deploy_validation=`success`',
            'commit=`success`, site_assembly=`success`, deploy=`failure`, post_deploy_validation=`skipped`',
          ),
          issue568.replace(', post_deploy_validation=`success`', ''),
          `${issue568}\n**Workflow status before incident handling:** \`failure\``,
          `${issue568}\n**Deployment:** commit=\`success\`, site_assembly=\`success\`, deploy=\`failure\`, post_deploy_validation=\`skipped\``,
        ]) {
          assert.equal(
            recovery.isStrictLegacySurfaceOnlyIncident(unsafe, 'queue'),
            false,
          );
        }
        assert.equal(
          recovery.isStrictLegacySurfaceOnlyIncident(
            issue568.replace('mode=`fallback`', 'mode=`blocked`'),
            'queue',
          ),
          false,
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


def test_node_selection_uniquely_prefers_the_exact_open_current_marker():
    _run_node(
        r"""
        const assert = require('node:assert/strict');
        const recovery = require(process.argv[1]);
        const owned = recovery.OWNERSHIP_MARKER;
        const current = recovery.CURRENT_INCIDENT_MARKER;
        const issues = [
          {number: 10, state: 'closed', body: `${owned}\n${current}`, labels: []},
          {number: 11, state: 'open', body: `${owned}\nlegacy`, labels: []},
          {number: 12, state: 'open', body: `${owned}\n${current}`, labels: []},
          {number: 13, state: 'open', body: `${owned}\n${current}`, pull_request: {}},
        ];
        assert.equal(recovery.selectCanonicalIncident(issues).number, 12);
        assert.throws(
          () => recovery.selectCanonicalIncident([
            issues[2],
            {number: 14, state: 'open', body: `${owned}\n${current}`, labels: []},
          ]),
          /open hourly current-incident marker is ambiguous/,
        );
        """
    )


def test_node_owner_marker_skips_closed_history_but_reconciles_open_migration_page():
    _run_node(
        r"""
        const assert = require('node:assert/strict');
        const recovery = require(process.argv[1]);
        const calls = [];
        const current = {
          number: 568,
          state: 'open',
          body: `${recovery.OWNERSHIP_MARKER}\n${recovery.CURRENT_INCIDENT_MARKER}`,
          labels: [{name: recovery.HOURLY_OWNER_LABEL}],
        };
        const legacy = {
          number: 567,
          state: 'open',
          body: `${recovery.OWNERSHIP_MARKER}\nlegacy`,
          labels: [{name: 'ci-failure'}],
        };
        const github = {rest: {issues: {listForRepo: async params => {
          calls.push(params);
          if (params.state === 'open' && params.labels === recovery.HOURLY_OWNER_LABEL) {
            return {data: [current]};
          }
          if (params.state === 'open') return {data: [legacy]};
          throw new Error('closed history must not be queried');
        }}}};
        const context = {repo: {owner: 'o', repo: 'r'}};
        recovery.findCanonicalIncident({github, context, includeClosed: true})
          .then(result => {
            assert.equal(result.issue.number, 568);
            assert.deepEqual(result.ownedIssues.map(issue => issue.number), [568, 567]);
            assert.equal(calls.length, 2);
            assert.ok(calls.every(call => call.state === 'open'));
            assert.ok(calls.every(call => call.per_page === 100));
          })
          .catch(error => { console.error(error); process.exitCode = 1; });
        """
    )


def test_node_owner_marker_full_pages_fail_before_accepting_an_exact_match():
    _run_node(
        r"""
        const assert = require('node:assert/strict');
        const recovery = require(process.argv[1]);
        const exact = state => ({
          number: 568,
          state,
          body: `${recovery.OWNERSHIP_MARKER}\n${recovery.CURRENT_INCIDENT_MARKER}`,
          labels: [{name: recovery.HOURLY_OWNER_LABEL}],
        });
        const filler = (number, state) => ({
          number,
          state,
          body: `${recovery.OWNERSHIP_MARKER}\nhistory-${number}`,
          labels: [{name: recovery.HOURLY_OWNER_LABEL}],
        });
        const context = {repo: {owner: 'o', repo: 'r'}};

        const openPage = [exact('open')];
        while (openPage.length < 100) openPage.push(filler(openPage.length, 'open'));
        const openGithub = {rest: {issues: {listForRepo: async () => ({data: openPage})}}};

        const closedPage = [exact('closed')];
        while (closedPage.length < 100) {
          closedPage.push(filler(closedPage.length, 'closed'));
        }
        const closedGithub = {rest: {issues: {listForRepo: async params => {
          if (params.state === 'open') return {data: []};
          return {data: closedPage};
        }}}};

        Promise.all([
          assert.rejects(
            recovery.findCanonicalIncident({
              github: openGithub, context, includeClosed: true,
            }),
            /open owner-label lookup is ambiguous/,
          ),
          assert.rejects(
            recovery.findCanonicalIncident({
              github: closedGithub, context, includeClosed: true,
            }),
            /closed owner-label lookup is ambiguous/,
          ),
        ]).catch(error => { console.error(error); process.exitCode = 1; });
        """
    )


def test_node_open_legacy_recovery_candidate_precedes_stale_closed_owner_slot():
    _run_node(
        r"""
        const assert = require('node:assert/strict');
        const recovery = require(process.argv[1]);
        const calls = [];
        const openLegacy = {
          number: 568,
          state: 'open',
          body: `${recovery.OWNERSHIP_MARKER}\n${recovery.CURRENT_INCIDENT_MARKER}`,
          labels: [{name: 'ci-failure'}],
        };
        const github = {rest: {issues: {listForRepo: async params => {
          calls.push(params);
          if (params.state === 'open' && params.labels === recovery.HOURLY_OWNER_LABEL) {
            return {data: []};
          }
          if (params.state === 'open') return {data: [openLegacy]};
          throw new Error('a closed owner slot must not eclipse an open migration candidate');
        }}}};
        const context = {repo: {owner: 'o', repo: 'r'}};
        recovery.findCanonicalIncident({github, context, includeClosed: true})
          .then(result => {
            assert.equal(result.issue.number, 568);
            assert.equal(calls.length, 2);
            assert.deepEqual(calls.map(call => call.state), ['open', 'open']);
          })
          .catch(error => { console.error(error); process.exitCode = 1; });
        """
    )


def test_node_already_canonical_queue_finalizer_adopts_and_closes_legacy_568():
    _run_node(
        r"""
        const assert = require('node:assert/strict');
        const recovery = require(process.argv[1]);
        const stateSha = 'c'.repeat(40);
        const issue = {
          number: 568,
          state: 'open',
          labels: [
            {name: 'ci-failure'},
            {name: 'automated'},
            {name: 'workstream:dashboard-ci'},
          ],
          body: [
            recovery.OWNERSHIP_MARKER,
            recovery.CURRENT_INCIDENT_MARKER,
            '<!-- hourly-ci-recovery-streak:0 -->',
            '## Degraded Publication — 2026-09-01',
            '**Summary:** `publication fallback`',
            '**Publication selector:** outcome=`success`, mode=`fallback`',
            '**Degraded surfaces:** queue',
            '**Workflow status before incident handling:** `success`',
            '**Deployment:** commit=`success`, site_assembly=`success`, deploy=`success`, post_deploy_validation=`success`',
            '**Live publication audit:** outcome=`success`, summary=`audit: 0 errors`',
          ].join('\n'),
        };
        const comments = [];
        const github = {rest: {issues: {
          getLabel: async () => ({data: {name: recovery.HOURLY_OWNER_LABEL}}),
          listForRepo: async params => {
            if (params.labels === recovery.HOURLY_OWNER_LABEL) {
              const hasOwner = recovery.issueHasLabel(
                issue, recovery.HOURLY_OWNER_LABEL,
              );
              return {data: hasOwner && params.state === issue.state ? [issue] : []};
            }
            return {data: issue.state === 'open' ? [issue] : []};
          },
          addLabels: async params => {
            for (const name of params.labels) {
              if (!issue.labels.some(label => label.name === name)) issue.labels.push({name});
            }
            return {data: issue};
          },
          update: async params => {
            if (params.body !== undefined) issue.body = params.body;
            if (params.state !== undefined) issue.state = params.state;
            return {data: issue};
          },
          removeLabel: async params => {
            issue.labels = issue.labels.filter(label => label.name !== params.name);
            return {data: []};
          },
          get: async () => ({data: issue}),
          createComment: async params => { comments.push(params.body); return {data: {}}; },
        }}};
        const context = {
          repo: {owner: 'o', repo: 'r'},
          serverUrl: 'https://github.test',
          runId: 99,
        };
        const core = {warning: message => { throw new Error(message); }};
        recovery.closeHourlyIncident({
          github,
          context,
          core,
          validationSource: 'targeted-queue',
          validationCodeSha: 'd'.repeat(40),
          validationStateSha: stateSha,
        }).then(async result => {
          assert.equal(result.action, 'closed');
          assert.equal(issue.state, 'closed');
          assert.ok(issue.body.includes(recovery.RECOVERED_MARKER));
          assert.ok(issue.body.includes(recovery.recoveryCreditMarker(stateSha)));
          assert.ok(issue.body.includes(recovery.QUEUE_ONLY_INCIDENT_MARKER));
          assert.ok(recovery.issueHasLabel(issue, recovery.HOURLY_OWNER_LABEL));
          assert.equal(comments.length, 1);
          const repeated = await recovery.closeHourlyIncident({
            github,
            context,
            core,
            validationSource: 'targeted-queue',
            validationCodeSha: 'd'.repeat(40),
            validationStateSha: stateSha,
          });
          assert.equal(repeated.action, 'already-recovered');
          assert.ok(recovery.issueHasLabel(issue, recovery.HOURLY_OWNER_LABEL));
          assert.equal(comments.length, 1);
        }).catch(error => { console.error(error); process.exitCode = 1; });
        """
    )
