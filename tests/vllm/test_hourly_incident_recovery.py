"""Contract and executable behavior tests for hourly incident recovery markers."""

from __future__ import annotations

import shutil
import json
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml


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
    "RECOVERY_IDENTITY_CREDIT_PATTERN",
    "RECOVERY_IDENTITY_CREDIT_PREFIX",
    "RECOVERY_MARKER_PATTERN",
    "RECOVERY_MARKER_PREFIX",
    "RECOVERY_PROGRESS_END",
    "RECOVERY_PROGRESS_START",
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
    "recoveryIdentityCreditMarker",
    "recoveryMarker",
    "resetRecoveryStreak",
    "retireOwnerLabel",
    "selectCanonicalIncident",
    "setRecoveryProgress",
    "setRecoveryStreak",
    "stripRecoveryProgress",
    "suppressedDegradationRecoveryTransition",
    "validateSiteHealthEvidence",
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


def _site_health_evidence() -> dict:
    return {
        "normalized": True, "reportValid": True, "confirmed": True,
        "healthy": True, "overallStatus": "healthy",
        "publicationMode": "current", "publicationStatus": "healthy",
        "degradedSince": None, "publicationBlocked": False, "usesFallback": False,
        "affectedSurfaces": [], "affectedSurfaceCount": 0,
        "fallbackSurfaceCount": 0, "freshDegradedSurfaceCount": 0,
        "confirmationStrategy": "2-of-3-quorum", "probeAttempts": 3,
        "healthyProbeCount": 3, "requiredHealthyProbes": 2,
        "completeProjectionVerified": True, "matchingProjectionHealthyCount": 3,
        "requiredMatchingProjectionHealthy": 2,
        "checkedAt": "2026-10-10T11:55:00Z", "generatedAt": "2026-10-10T10:00:00Z",
        "durableCoreSucceededAt": "2026-10-10T10:00:00Z",
        "stateSha": "a" * 40, "codeSha": "b" * 40, "stateTree": "c" * 40,
        "generationId": "hourly-12345-1", "manifestSha256": "d" * 64,
        "fileCount": 46, "totalBytes": 100000,
    }


def test_site_health_recovery_checks_source_clocks_and_expiry_boundaries() -> None:
    _run_node("const evidence = " + json.dumps(_site_health_evidence()) + ";\n" + r"""
      const assert = require('node:assert/strict');
      const recovery = require(process.argv[1]);
      const now = Date.parse('2026-10-10T12:00:00Z');
      const validate = (value, clock = now) => recovery.validateSiteHealthEvidence(
        value, evidence.stateSha, evidence.codeSha, clock,
      );
      assert.equal(validate(evidence), true);
      assert.equal(validate({...evidence, checkedAt: '2026-10-10T11:45:00Z'}), true);
      assert.equal(validate({...evidence, generatedAt: '2026-10-10T09:00:00Z'}), true);
      assert.equal(validate({...evidence, durableCoreSucceededAt: '2026-10-10T08:55:00Z'}), true);
      for (const field of ['checkedAt', 'generatedAt', 'durableCoreSucceededAt']) {
        for (const value of [undefined, 'not-a-time', '2026-10-10T11:55:00', '2026-10-10T12:06:00Z']) {
          assert.throws(() => validate({...evidence, [field]: value}), /expired|freshness clocks/);
        }
      }
      for (const patch of [
        {checkedAt: '2026-10-10T11:44:59.999Z'},
        {generatedAt: '2026-10-10T08:59:59.999Z'},
        {durableCoreSucceededAt: '2026-10-10T08:54:59.999Z'},
      ]) assert.throws(() => validate({...evidence, ...patch}), /expired/);
      assert.throws(() => validate(evidence, NaN), /freshness clocks/);
      // Date.parse normalizes February 30 to March 2. A malformed calendar
      // date must not masquerade as a fresh five-minute-old probe.
      assert.throws(() => validate({
        ...evidence, checkedAt: '2026-02-30T11:55:00Z',
        generatedAt: '2026-03-02T11:00:00Z',
        durableCoreSucceededAt: '2026-03-02T11:00:00Z',
      }, Date.parse('2026-03-02T12:00:00Z')), /freshness clocks/);
    """)


def test_site_health_cannot_mutate_after_reads_or_retries_exhaust_freshness() -> None:
    _run_node("const evidence = " + json.dumps(_site_health_evidence()) + ";\n" + r"""
      const assert = require('node:assert/strict');
      const recovery = require(process.argv[1]);
      const originalNow = Date.now;
      const context = {repo: {owner: 'o', repo: 'r'}, serverUrl: 'https://github.test', runId: 99};
      (async () => {
        for (const scenario of ['scan-proof', 'scan-publication', 'scan-core', 'label-proof', 'retry-proof']) {
          let now = Date.parse('2026-10-10T12:00:00Z');
          Date.now = () => now;
          const proof = {...evidence};
          if (scenario === 'scan-publication') proof.generatedAt = '2026-10-10T09:01:00Z';
          if (scenario === 'scan-core') proof.durableCoreSucceededAt = '2026-10-10T08:56:00Z';
          if (scenario === 'retry-proof') proof.checkedAt = '2026-10-10T11:45:10Z';
          const issue = {number: 568, state: 'open', labels: [{name: recovery.HOURLY_OWNER_LABEL}],
            body: [recovery.OWNERSHIP_MARKER, recovery.CURRENT_INCIDENT_MARKER,
              recovery.recoveryMarker(1), recovery.recoveryCreditMarker(proof.stateSha),
              recovery.recoveryIdentityCreditMarker(proof.stateSha, proof.codeSha)].join('\n')};
          const originalBody = issue.body;
          let reads = 0, labels = 0, closeAttempts = 0, otherWrites = 0;
          const github = {rest: {issues: {
            listForRepo: async parameters => {
              if (++reads === 1 && scenario.startsWith('scan-')) {
                now += (scenario === 'scan-proof' ? 11 : 2) * 60000;
              }
              return {data: parameters.state === 'open' ? [issue] : []};
            },
            getLabel: async () => ({data: {}}),
            createLabel: async () => {otherWrites++; return {data: {}};},
            addLabels: async () => {
              labels++;
              if (scenario === 'label-proof') now += 11 * 60000;
              return {data: issue};
            },
            update: async parameters => {
              if (parameters.state === 'closed') {
                closeAttempts++;
                if (scenario === 'retry-proof') {
                  now += 11000;
                  throw Object.assign(new Error('temporary close failure'), {status: 503});
                }
              }
              otherWrites++;
              return {data: issue};
            },
            get: async () => ({data: issue}),
            removeLabel: async () => {otherWrites++; return {data: {}};},
            createComment: async () => {otherWrites++; return {data: {}};},
          }}};
          await assert.rejects(recovery.closeHourlyIncident({
            github, context, core: {warning() {}}, validationSource: 'site-health',
            validationCodeSha: proof.codeSha, validationStateSha: proof.stateSha,
            validationEvidence: proof,
          }), /expired/);
          assert.equal(issue.state, 'open');
          assert.equal(issue.body, originalBody);
          assert.equal(labels, scenario.startsWith('scan-') ? 0 : 1);
          assert.equal(closeAttempts, scenario === 'retry-proof' ? 1 : 0);
          assert.equal(otherWrites, 0);
        }
      })().catch(error => {console.error(error); process.exitCode = 1;})
        .finally(() => {Date.now = originalNow;});
    """)


@pytest.mark.parametrize("read_delay_ms", [0, 120000])
def test_serialized_health_workflow_rechecks_age_after_canonical_reads(read_delay_ms: int) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/health-check.yml").read_text())
    steps = workflow["jobs"]["confirm-hourly-recovery"]["steps"]
    script = next(step["with"]["script"] for step in steps if "RECOVERY_EVIDENCE" in step.get("env", {}))
    proof = _site_health_evidence()
    proof["generatedAt"] = "2026-10-10T09:01:00Z"
    prefix = (
        "const evidence = " + json.dumps(proof) + ";\n"
        + "const workflowSource = " + json.dumps(script) + ";\n"
        + f"const readDelay = {read_delay_ms};\n"
    )
    _run_node(prefix + r"""
      const assert = require('node:assert/strict');
      const recovery = require(process.argv[1]);
      let now = Date.parse('2026-10-10T12:00:00Z'), closed = 0, reads = 0;
      const originalNow = Date.now;
      Date.now = () => now;
      const marker = {schema_version: 2, generation_id: evidence.generationId,
        generated_at: evidence.generatedAt, state_sha: evidence.stateSha,
        state_tree: evidence.stateTree, code_sha: evidence.codeSha,
        public_projection: {schema_version: 1, manifest_path: 'publication_manifest.json',
          manifest_sha256: evidence.manifestSha256, file_count: evidence.fileCount,
          total_bytes: evidence.totalBytes}};
      const status = {schema_version: 1, status: 'healthy', mode: 'current',
        generated_at: evidence.generatedAt, degraded_since: null, uses_fallback: false,
        publication_blocked: false, affected_surfaces: [], affected_surface_count: 0,
        fallback_surface_count: 0, fresh_degraded_surface_count: 0};
      const github = {rest: {repos: {getContent: async parameters => {
        assert.equal(parameters.ref, 'gh-pages');
        reads++;
        const raw = Buffer.from(JSON.stringify(parameters.path === 'publication_generation.json' ? marker : status));
        if (parameters.path !== 'publication_generation.json') now += readDelay;
        return {data: {type: 'file', encoding: 'base64', size: raw.length, content: raw.toString('base64')}};
      }}}};
      const execute = new (Object.getPrototypeOf(async function() {}).constructor)(
        'require', 'github', 'context', 'core', 'process', workflowSource,
      );
      const promise = execute(() => ({...recovery, closeHourlyIncident: async () => {closed++;}}),
        github, {repo: {owner: 'o', repo: 'r'}}, {info() {}},
        {env: {RECOVERY_EVIDENCE: Buffer.from(JSON.stringify(evidence)).toString('base64'), GITHUB_WORKSPACE: '/fixture'}});
      (async () => {
        if (readDelay) await assert.rejects(promise, /expired/);
        else await promise;
        assert.equal(reads, 2);
        assert.equal(closed, readDelay ? 0 : 1);
      })().catch(error => {console.error(error); process.exitCode = 1;})
        .finally(() => {Date.now = originalNow;});
    """)


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
          'RECOVERY_IDENTITY_CREDIT_PATTERN',
          'RECOVERY_IDENTITY_CREDIT_PREFIX',
          'RECOVERY_MARKER_PATTERN',
          'RECOVERY_MARKER_PREFIX',
          'RECOVERY_PROGRESS_END',
          'RECOVERY_PROGRESS_START',
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
          'recoveryIdentityCreditMarker',
          'recoveryMarker',
          'resetRecoveryStreak',
          'retireOwnerLabel',
          'selectCanonicalIncident',
          'setRecoveryProgress',
          'setRecoveryStreak',
          'stripRecoveryProgress',
          'suppressedDegradationRecoveryTransition',
          'validateSiteHealthEvidence',
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

        // A legacy state-only credit remains idempotent, but an exact repeat
        // under the new helper can safely bind its code identity without
        // incrementing the streak.
        const legacyBody = [
          '<!-- hourly-ci-recovery-streak:1 -->',
          recovery.recoveryCreditMarker(firstState),
          'legacy evidence',
        ].join('\n');
        const rebound = recovery.advanceRecoveryStreak(
          legacyBody,
          firstState,
          'a'.repeat(40),
        );
        assert.equal(rebound.credited, false);
        assert.equal(rebound.identityBound, true);
        assert.equal(rebound.streak, 1);
        assert.ok(rebound.body.includes(
          recovery.recoveryIdentityCreditMarker(firstState, 'a'.repeat(40)),
        ));
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


def test_node_general_recovery_uses_two_states_or_matching_site_health_and_resets():
    _run_node(
        r"""
        const assert = require('node:assert/strict');
        const recovery = require(process.argv[1]);
        const codeA = 'a'.repeat(40);
        const codeB = 'b'.repeat(40);
        const stateOne = '1'.repeat(40);
        const stateTwo = '2'.repeat(40);

        const siteEvidence = (stateSha, codeSha) => ({
          normalized: true,
          reportValid: true,
          confirmed: true,
          healthy: true,
          overallStatus: 'healthy',
          publicationMode: 'current',
          publicationStatus: 'healthy',
          degradedSince: null,
          publicationBlocked: false,
          usesFallback: false,
          affectedSurfaces: [],
          affectedSurfaceCount: 0,
          fallbackSurfaceCount: 0,
          freshDegradedSurfaceCount: 0,
          checkedAt: new Date(Math.floor(Date.now() / 1000) * 1000)
            .toISOString().replace('.000Z', 'Z'),
          generatedAt: new Date(Math.floor(Date.now() / 1000) * 1000 - 3600000)
            .toISOString().replace('.000Z', 'Z'),
          durableCoreSucceededAt: new Date(Math.floor(Date.now() / 1000) * 1000 - 3600000)
            .toISOString().replace('.000Z', 'Z'),
          confirmationStrategy: '2-of-3-quorum',
          probeAttempts: 3,
          healthyProbeCount: 3,
          requiredHealthyProbes: 2,
          completeProjectionVerified: true,
          matchingProjectionHealthyCount: 3,
          requiredMatchingProjectionHealthy: 2,
          stateSha,
          codeSha,
        });

        const makeHarness = () => {
          const issue = {
            number: 568,
            state: 'open',
            title: 'Hourly validation failure [deadbeef] — 2026-09-01',
            labels: [{name: recovery.HOURLY_OWNER_LABEL}],
            body: [
              recovery.OWNERSHIP_MARKER,
              recovery.CURRENT_INCIDENT_MARKER,
              recovery.ZERO_RECOVERY_MARKER,
              '## Degraded Publication — 2026-09-01',
              'Original failure evidence',
            ].join('\n'),
          };
          const comments = [];
          let issueMutations = 0;
          const github = {rest: {issues: {
            getLabel: async () => ({data: {name: recovery.HOURLY_OWNER_LABEL}}),
            createLabel: async () => ({data: {}}),
            listForRepo: async params => {
              if (params.state === issue.state) return {data: [issue]};
              return {data: []};
            },
            addLabels: async params => {
              issueMutations += 1;
              for (const name of params.labels) {
                if (!issue.labels.some(label => label.name === name)) {
                  issue.labels.push({name});
                }
              }
              return {data: issue};
            },
            update: async params => {
              issueMutations += 1;
              if (params.title !== undefined) issue.title = params.title;
              if (params.body !== undefined) issue.body = params.body;
              if (params.state !== undefined) issue.state = params.state;
              return {data: issue};
            },
            removeLabel: async params => {
              issueMutations += 1;
              issue.labels = issue.labels.filter(label => label.name !== params.name);
              return {data: issue};
            },
            get: async () => ({data: issue}),
            createComment: async params => {
              issueMutations += 1;
              comments.push(params.body);
              return {data: {}};
            },
          }}};
          return {
            issue,
            github,
            comments,
            mutations: () => issueMutations,
          };
        };
        const context = {
          repo: {owner: 'o', repo: 'r'},
          serverUrl: 'https://github.test',
          runId: 99,
        };
        const core = {warning: message => { throw new Error(message); }};
        const publish = (harness, stateSha, codeSha = codeA) =>
          recovery.closeHourlyIncident({
            github: harness.github,
            context,
            core,
            validationSource: 'hourly-tests',
            validationCodeSha: codeSha,
            validationStateSha: stateSha,
          });
        const confirm = (harness, stateSha, codeSha = codeA) =>
          recovery.closeHourlyIncident({
            github: harness.github,
            context,
            core,
            validationSource: 'site-health',
            validationCodeSha: codeSha,
            validationStateSha: stateSha,
            validationEvidence: siteEvidence(stateSha, codeSha),
          });

        (async () => {
          // One clean full publication stays open, but visibly stops claiming
          // the failure is still current.
          const independentlyConfirmed = makeHarness();
          const first = await publish(independentlyConfirmed, stateOne);
          assert.equal(first.action, 'advanced');
          assert.equal(first.streak, 1);
          assert.equal(independentlyConfirmed.issue.state, 'open');
          assert.match(independentlyConfirmed.issue.title, /validation failure/);
          assert.ok(independentlyConfirmed.issue.body.includes(
            '**Recovery verification in progress.**',
          ));
          assert.ok(independentlyConfirmed.issue.body.includes(
            recovery.recoveryIdentityCreditMarker(stateOne, codeA),
          ));

          // The same state is idempotent and cannot double-credit.
          const duplicate = await publish(independentlyConfirmed, stateOne);
          assert.equal(duplicate.action, 'duplicate-credit');
          assert.equal(duplicate.streak, 1);
          assert.equal(
            (independentlyConfirmed.issue.body.match(
              /<!-- hourly-ci-recovery-credit:[0-9a-f]{40} -->/g,
            ) || []).length,
            1,
          );

          // An exact, independently normalized Site Health quorum can close
          // only the already credited state+code identity.
          const matched = await confirm(independentlyConfirmed, stateOne);
          assert.equal(matched.action, 'closed');
          assert.equal(independentlyConfirmed.issue.state, 'closed');
          assert.ok(independentlyConfirmed.issue.body.includes(
            recovery.RECOVERED_MARKER,
          ));
          assert.ok(!independentlyConfirmed.issue.body.includes(
            recovery.RECOVERY_PROGRESS_START,
          ));
          assert.equal(independentlyConfirmed.comments.length, 1);

          // Site Health alone, a different state, or a different code is a
          // read-only mismatch and cannot manufacture publication credit.
          const noPublication = makeHarness();
          const aloneBody = noPublication.issue.body;
          const aloneMutations = noPublication.mutations();
          const fallbackEvidence = siteEvidence(stateOne, codeA);
          fallbackEvidence.publicationMode = 'fallback';
          fallbackEvidence.publicationStatus = 'degraded';
          fallbackEvidence.usesFallback = true;
          await assert.rejects(
            recovery.closeHourlyIncident({
              github: noPublication.github,
              context,
              core,
              validationSource: 'site-health',
              validationCodeSha: codeA,
              validationStateSha: stateOne,
              validationEvidence: fallbackEvidence,
            }),
            /requires normalized healthy\/current 2-of-3 evidence/,
          );
          assert.equal(noPublication.mutations(), aloneMutations);
          const lingeringDegradation = siteEvidence(stateOne, codeA);
          lingeringDegradation.degradedSince = '2026-09-01T22:55:19Z';
          assert.throws(
            () => recovery.validateSiteHealthEvidence(
              lingeringDegradation,
              stateOne,
              codeA,
            ),
            /requires normalized healthy\/current 2-of-3 evidence/,
          );
          const alone = await confirm(noPublication, stateOne);
          assert.equal(alone.action, 'identity-mismatch');
          assert.equal(noPublication.issue.state, 'open');
          assert.equal(noPublication.issue.body, aloneBody);
          assert.equal(noPublication.mutations(), aloneMutations);

          const stale = makeHarness();
          await publish(stale, stateOne);
          const pendingBody = stale.issue.body;
          const pendingMutations = stale.mutations();
          const wrongState = await confirm(stale, stateTwo);
          assert.equal(wrongState.action, 'identity-mismatch');
          assert.equal(stale.issue.body, pendingBody);
          assert.equal(stale.mutations(), pendingMutations);
          const wrongCode = await confirm(stale, stateOne, codeB);
          assert.equal(wrongCode.action, 'identity-mismatch');
          assert.equal(stale.issue.body, pendingBody);
          assert.equal(stale.mutations(), pendingMutations);

          // A second distinct clean full publication remains an independent
          // closure path when the monitor has not yet run.
          const twoPublications = makeHarness();
          assert.equal((await publish(twoPublications, stateOne)).action, 'advanced');
          const second = await publish(twoPublications, stateTwo);
          assert.equal(second.action, 'closed');
          assert.equal(twoPublications.issue.state, 'closed');
          assert.equal(recovery.parseRecoveryStreak(twoPublications.issue.body), 2);

          // Any degradation resets all state credits, state+code bindings, and
          // the visible pending-verification banner.
          const resetting = makeHarness();
          await publish(resetting, stateOne);
          const reset = recovery.suppressedDegradationRecoveryTransition(
            resetting.issue.body,
            'open',
          );
          assert.equal(reset.shouldReset, true);
          assert.equal(recovery.parseRecoveryStreak(reset.body), 0);
          assert.ok(!reset.body.includes(recovery.RECOVERY_CREDIT_PREFIX));
          assert.ok(!reset.body.includes(recovery.RECOVERY_IDENTITY_CREDIT_PREFIX));
          assert.ok(!reset.body.includes(recovery.RECOVERY_PROGRESS_START));
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """
    )
