'use strict';

/**
 * Pure recovery-marker transitions for the hourly publication incident.
 *
 * GitHub issue bodies are the durable state store for the recovery streak.
 * Keep this module dependency-free so both actions/github-script blocks can
 * load the exact same parser and transition rules.
 */

const RECOVERY_MARKER_PREFIX = '<!-- hourly-ci-recovery-streak:';
const RECOVERY_MARKER_PATTERN = /<!-- hourly-ci-recovery-streak:(\d+) -->/;
const RECOVERED_MARKER = '<!-- hourly-ci-recovered -->';
const ZERO_RECOVERY_MARKER = `${RECOVERY_MARKER_PREFIX}0 -->`;
const FINGERPRINT_MARKER_PATTERN = /<!-- hourly-ci-fingerprint:[0-9a-f]+ -->/;

function bodyText(body) {
  return typeof body === 'string' ? body : '';
}

function recoveryMarker(streak) {
  if (!Number.isSafeInteger(streak) || streak < 0) {
    throw new RangeError('recovery streak must be a non-negative safe integer');
  }
  return `${RECOVERY_MARKER_PREFIX}${streak} -->`;
}

function parseRecoveryStreak(body) {
  const match = bodyText(body).match(RECOVERY_MARKER_PATTERN);
  if (!match) return 0;
  const streak = Number(match[1]);
  return Number.isSafeInteger(streak) && streak >= 0 ? streak : 0;
}

function setRecoveryStreak(body, streak) {
  const source = bodyText(body);
  const marker = recoveryMarker(streak);
  let replaced = false;
  const canonical = source.replace(
    /<!-- hourly-ci-recovery-streak:\d+ -->/g,
    () => {
      if (replaced) return '';
      replaced = true;
      return marker;
    },
  );
  return replaced ? canonical : `${marker}\n${source}`;
}

function resetRecoveryStreak(body) {
  // Any unhealthy recurrence, including a deliberately suppressed transient
  // fallback or a manually suppressed incident, breaks consecutiveness.
  const activeBody = bodyText(body)
    .split(RECOVERED_MARKER)
    .join('')
    .replace(/^\n+/, '');
  return setRecoveryStreak(activeBody, 0);
}

function suppressedDegradationRecoveryTransition(body, issueState) {
  const source = bodyText(body);
  const wasRecovered = source.includes(RECOVERED_MARKER);
  const priorRecoveryStreak = parseRecoveryStreak(source);
  const closedWithPartialRecovery = issueState === 'closed' &&
    priorRecoveryStreak > 0 && !wasRecovered;
  const shouldReset = issueState === 'open' || closedWithPartialRecovery;
  return Object.freeze({
    body: shouldReset ? resetRecoveryStreak(source) : source,
    closedWithPartialRecovery,
    priorRecoveryStreak,
    shouldReset,
    wasRecovered,
  });
}

function classifyFailureTransition(body, issueState, fingerprintMarker) {
  const source = bodyText(body);
  if (issueState !== 'open' && issueState !== 'closed') {
    throw new TypeError('issue state must be open or closed');
  }
  if (typeof fingerprintMarker !== 'string' ||
      !FINGERPRINT_MARKER_PATTERN.test(fingerprintMarker)) {
    throw new TypeError('fingerprint marker must be canonical');
  }
  const manuallySuppressed = issueState === 'closed' &&
    !source.includes(RECOVERED_MARKER) && source.includes(fingerprintMarker);
  if (manuallySuppressed) return 'manually-suppressed';

  const priorFingerprint = source.match(FINGERPRINT_MARKER_PATTERN)?.[0] || '';
  const signalChanged = Boolean(priorFingerprint) &&
    priorFingerprint !== fingerprintMarker;
  if (issueState !== 'open') return 'reopened';
  return signalChanged ? 'changed' : 'unchanged';
}

function isStrictLegacySurfaceOnlyIncident(body, surface) {
  const allowedModes = {
    dns_health: new Set(['degraded']),
    queue: new Set(['degraded', 'fallback']),
  };
  const modes = allowedModes[surface];
  if (!modes) return false;
  const source = bodyText(body);
  const lines = source.split(/\r?\n/);
  const selectorPrefix = '**Publication selector:** outcome=`success`, mode=`';
  const selectorLines = lines.filter(line => line.startsWith(selectorPrefix));
  const selectorLine = selectorLines.length === 1 ? selectorLines[0] : '';
  const modeMatch = selectorLine.match(/mode=`([^`]+)`/);
  const headerLines = lines.filter(line =>
    /^## Degraded Publication — \d{4}-\d{2}-\d{2}$/.test(line),
  );
  const surfaceLines = lines.filter(line =>
    line === `**Degraded surfaces:** ${surface}`,
  );
  const liveAuditLines = lines.filter(line =>
    line.startsWith('**Live publication audit:** outcome=`success`,'),
  );
  const workflowStatusLines = lines.filter(line =>
    line.startsWith('**Workflow status before incident handling:**'),
  );
  const deploymentLines = lines.filter(line => line.startsWith('**Deployment:**'));
  const workflowSucceeded = workflowStatusLines.length === 1 &&
    workflowStatusLines[0] ===
      '**Workflow status before incident handling:** `success`';
  const deploymentSucceeded = deploymentLines.length === 1 &&
    deploymentLines[0] ===
      '**Deployment:** commit=`success`, site_assembly=`success`, ' +
      'deploy=`success`, post_deploy_validation=`success`';
  return (
    headerLines.length === 1 &&
    surfaceLines.length === 1 &&
    selectorLines.length === 1 &&
    Boolean(modeMatch && modes.has(modeMatch[1])) &&
    liveAuditLines.length === 1 &&
    workflowSucceeded &&
    deploymentSucceeded &&
    !source.includes('**Failing deterministic tests:**') &&
    !source.includes('deployment failed:') &&
    !source.includes('workflow step failed before publication completed')
  );
}

function advanceRecoveryStreak(body) {
  const previousStreak = parseRecoveryStreak(body);
  const streak = previousStreak + 1;
  return Object.freeze({
    previousStreak,
    streak,
    body: setRecoveryStreak(body, streak),
  });
}

module.exports = Object.freeze({
  FINGERPRINT_MARKER_PATTERN,
  RECOVERED_MARKER,
  RECOVERY_MARKER_PATTERN,
  RECOVERY_MARKER_PREFIX,
  ZERO_RECOVERY_MARKER,
  advanceRecoveryStreak,
  classifyFailureTransition,
  isStrictLegacySurfaceOnlyIncident,
  parseRecoveryStreak,
  recoveryMarker,
  resetRecoveryStreak,
  setRecoveryStreak,
  suppressedDegradationRecoveryTransition,
});
