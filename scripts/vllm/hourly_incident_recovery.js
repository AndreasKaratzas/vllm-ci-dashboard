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
const RECOVERY_CREDIT_PREFIX = '<!-- hourly-ci-recovery-credit:';
const RECOVERY_CREDIT_PATTERN =
  /<!-- hourly-ci-recovery-credit:([0-9a-f]{40}) -->/g;
const RECOVERED_MARKER = '<!-- hourly-ci-recovered -->';
const ZERO_RECOVERY_MARKER = `${RECOVERY_MARKER_PREFIX}0 -->`;
const FINGERPRINT_MARKER_PATTERN = /<!-- hourly-ci-fingerprint:[0-9a-f]+ -->/;
const OWNERSHIP_MARKER = '<!-- ci-failure-owner:hourly-master -->';
const CURRENT_INCIDENT_MARKER = '<!-- hourly-ci-current-incident:v1 -->';
const SUPERSEDED_MARKER = '<!-- hourly-ci-superseded:v1 -->';
const DNS_ONLY_INCIDENT_MARKER = '<!-- hourly-ci-dns-only:v1 -->';
const QUEUE_ONLY_INCIDENT_MARKER = '<!-- hourly-ci-queue-only:v1 -->';
const LEGACY_SIGNATURE = '*Auto-created by hourly-master workflow.*';
const HOURLY_OWNER_LABEL = 'automation:hourly-master';

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
    .replace(RECOVERY_CREDIT_PATTERN, '')
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

function recoveryCreditMarker(stateSha) {
  if (typeof stateSha !== 'string' || !/^[0-9a-f]{40}$/.test(stateSha)) {
    throw new TypeError('recovery credit must be a full lowercase state SHA');
  }
  return `${RECOVERY_CREDIT_PREFIX}${stateSha} -->`;
}

function advanceRecoveryStreak(body, stateSha) {
  const source = bodyText(body);
  const creditMarker = recoveryCreditMarker(stateSha);
  const previousStreak = parseRecoveryStreak(body);
  if (source.split(/\r?\n/).includes(creditMarker)) {
    return Object.freeze({
      previousStreak,
      streak: previousStreak,
      credited: false,
      creditMarker,
      body: source,
    });
  }
  const streak = previousStreak + 1;
  const creditedBody = `${creditMarker}\n${source}`;
  return Object.freeze({
    previousStreak,
    streak,
    credited: true,
    creditMarker,
    body: setRecoveryStreak(creditedBody, streak),
  });
}

function hasExactMarker(body, marker) {
  return bodyText(body).split(/\r?\n/).includes(marker);
}

function issueHasLabel(issue, labelName) {
  return (issue && Array.isArray(issue.labels) ? issue.labels : []).some(label =>
    (typeof label === 'string' ? label : label && label.name) === labelName
  );
}

function isOwnedIssue(issue) {
  if (!issue || issue.pull_request) return false;
  const body = bodyText(issue.body);
  return hasExactMarker(body, OWNERSHIP_MARKER) || body.includes(LEGACY_SIGNATURE);
}

function newestFirst(left, right) {
  return Date.parse(right.updated_at || right.created_at || 0) -
      Date.parse(left.updated_at || left.created_at || 0) ||
    Number(right.number || 0) - Number(left.number || 0);
}

function uniqueIssue(issues, label) {
  if (issues.length > 1) {
    throw new Error(
      `${label} is ambiguous: ${issues.map(issue => `#${issue.number}`).join(', ')}`,
    );
  }
  return issues[0] || null;
}

function selectCanonicalIncident(issues) {
  const owned = (Array.isArray(issues) ? issues : []).filter(isOwnedIssue);
  const active = owned.filter(issue =>
    !hasExactMarker(issue.body, SUPERSEDED_MARKER)
  );
  const openCurrent = active.filter(issue =>
    issue.state === 'open' && hasExactMarker(issue.body, CURRENT_INCIDENT_MARKER)
  ).sort(newestFirst);
  const exactOpen = uniqueIssue(openCurrent, 'open hourly current-incident marker');
  if (exactOpen) return exactOpen;

  const openOwned = active.filter(issue => issue.state === 'open').sort(newestFirst);
  const uniqueOpen = uniqueIssue(openOwned, 'open hourly legacy incident');
  if (uniqueOpen) return uniqueOpen;

  const closedCurrent = active.filter(issue =>
    issue.state === 'closed' && hasExactMarker(issue.body, CURRENT_INCIDENT_MARKER)
  ).sort(newestFirst);
  return uniqueIssue(closedCurrent, 'closed hourly current-incident marker');
}

async function findCanonicalIncident({github, context, includeClosed = true}) {
  const list = async parameters => {
    const response = await github.rest.issues.listForRepo({
      owner: context.repo.owner,
      repo: context.repo.repo,
      sort: 'updated',
      direction: 'desc',
      per_page: 100,
      page: 1,
      ...parameters,
    });
    if (!response || !Array.isArray(response.data)) {
      throw new Error('hourly incident lookup returned a malformed response');
    }
    return response.data;
  };

  const openOwnerPage = await list({state: 'open', labels: HOURLY_OWNER_LABEL});
  if (openOwnerPage.length >= 100) {
    throw new Error('hourly open owner-label lookup is ambiguous');
  }
  const openCurrent = openOwnerPage.filter(issue =>
    isOwnedIssue(issue) && hasExactMarker(issue.body, CURRENT_INCIDENT_MARKER)
  );
  uniqueIssue(openCurrent, 'owner-labeled open hourly incident');
  // Always inspect the bounded migration page too. Otherwise an exact labeled
  // slot could hide an older in-flight workflow's unlabeled duplicate forever.
  const migrationPage = await list({
    state: 'open',
    labels: 'ci-failure,automated,workstream:dashboard-ci',
  });
  if (migrationPage.length >= 100) {
    throw new Error('hourly incident migration lookup is ambiguous');
  }
  const openByNumber = new Map(
    [...openOwnerPage, ...migrationPage].map(issue => [issue.number, issue]),
  );
  const openIssues = [...openByNumber.values()];
  const openIssue = selectCanonicalIncident(openIssues);
  if (openIssue) {
    return Object.freeze({
      issue: openIssue,
      ownedIssues: openIssues.filter(isOwnedIssue),
    });
  }

  let closedOwnerPage = [];
  if (includeClosed) {
    closedOwnerPage = await list({state: 'closed', labels: HOURLY_OWNER_LABEL});
    if (closedOwnerPage.length >= 100) {
      throw new Error('hourly closed owner-label lookup is ambiguous');
    }
    const closedCurrent = closedOwnerPage.filter(issue =>
      isOwnedIssue(issue) &&
      !hasExactMarker(issue.body, SUPERSEDED_MARKER) &&
      hasExactMarker(issue.body, CURRENT_INCIDENT_MARKER)
    );
    const exactClosed = uniqueIssue(
      closedCurrent,
      'owner-labeled closed hourly incident',
    );
    if (exactClosed) {
      return Object.freeze({
        issue: exactClosed,
        ownedIssues: closedOwnerPage.filter(isOwnedIssue),
      });
    }
  }

  return Object.freeze({
    issue: null,
    ownedIssues: [...openOwnerPage, ...closedOwnerPage, ...migrationPage]
      .filter(isOwnedIssue),
  });
}

async function ensureOwnerLabel({github, context}) {
  try {
    await github.rest.issues.getLabel({
      owner: context.repo.owner,
      repo: context.repo.repo,
      name: HOURLY_OWNER_LABEL,
    });
  } catch (error) {
    if (error.status !== 404) throw error;
    try {
      await github.rest.issues.createLabel({
        owner: context.repo.owner,
        repo: context.repo.repo,
        name: HOURLY_OWNER_LABEL,
        color: '5319e7',
        description: 'Single incident slot owned by hourly-master',
      });
    } catch (createError) {
      if (createError.status !== 422) throw createError;
    }
  }
}

async function retireOwnerLabel({github, context, issue}) {
  if (!issueHasLabel(issue, HOURLY_OWNER_LABEL)) return false;
  try {
    await github.rest.issues.removeLabel({
      owner: context.repo.owner,
      repo: context.repo.repo,
      issue_number: issue.number,
      name: HOURLY_OWNER_LABEL,
    });
  } catch (error) {
    const readback = await github.rest.issues.get({
      owner: context.repo.owner,
      repo: context.repo.repo,
      issue_number: issue.number,
    });
    if (issueHasLabel(readback.data, HOURLY_OWNER_LABEL)) throw error;
  }
  return true;
}

async function closeHourlyIncident({
  github,
  context,
  core,
  validationSource,
  validationCodeSha,
  validationStateSha,
  validationCiUrl = '',
}) {
  const allowedValidationSources = new Set([
    'targeted-dns',
    'targeted-queue',
    'hourly-tests',
    'separate-ci',
  ]);
  if (!allowedValidationSources.has(validationSource)) {
    throw new Error('eligible recovery requires an allowlisted validation source');
  }
  if (!/^[0-9a-f]{40}$/.test(validationCodeSha || '')) {
    throw new Error('eligible recovery requires an exact lowercase code SHA');
  }
  if (!/^[0-9a-f]{40}$/.test(validationStateSha || '')) {
    throw new Error('eligible recovery requires an exact dashboard state SHA');
  }
  await ensureOwnerLabel({github, context});
  const {issue: currentIssue, ownedIssues} = await findCanonicalIncident({
    github,
    context,
    includeClosed: true,
  });
  if (!currentIssue) {
    console.log('No current hourly incident needs recovery tracking.');
    return Object.freeze({action: 'none'});
  }
  if (!issueHasLabel(currentIssue, HOURLY_OWNER_LABEL)) {
    await github.rest.issues.addLabels({
      owner: context.repo.owner,
      repo: context.repo.repo,
      issue_number: currentIssue.number,
      labels: [HOURLY_OWNER_LABEL],
    });
    currentIssue.labels = [...(currentIssue.labels || []), {name: HOURLY_OWNER_LABEL}];
  }

  const originalBody = bodyText(currentIssue.body);
  const targetedDnsRecovery = validationSource === 'targeted-dns';
  const targetedQueueRecovery = validationSource === 'targeted-queue';
  const isMarkedDnsOnly = hasExactMarker(originalBody, DNS_ONLY_INCIDENT_MARKER);
  const isMarkedQueueOnly = hasExactMarker(originalBody, QUEUE_ONLY_INCIDENT_MARKER);
  const isStrictLegacyDnsOnly = isStrictLegacySurfaceOnlyIncident(
    originalBody,
    'dns_health',
  );
  const isStrictLegacyQueueOnly = isStrictLegacySurfaceOnlyIncident(
    originalBody,
    'queue',
  );
  if (targetedDnsRecovery && !isMarkedDnsOnly && !isStrictLegacyDnsOnly) {
    console.log(
      `Targeted DNS publication will not advance non-DNS-only incident ` +
      `#${currentIssue.number}.`,
    );
    return Object.freeze({action: 'scope-mismatch', issue: currentIssue.number});
  }
  if (targetedQueueRecovery && !isMarkedQueueOnly && !isStrictLegacyQueueOnly) {
    console.log(
      `Targeted queue publication will not advance non-queue-only incident ` +
      `#${currentIssue.number}.`,
    );
    return Object.freeze({action: 'scope-mismatch', issue: currentIssue.number});
  }

  await github.rest.issues.addLabels({
    owner: context.repo.owner,
    repo: context.repo.repo,
    issue_number: currentIssue.number,
    labels: ['ci-failure', 'automated', 'workstream:dashboard-ci', HOURLY_OWNER_LABEL],
  });
  const runUrl = `${context.serverUrl}/${context.repo.owner}/${context.repo.repo}` +
    `/actions/runs/${context.runId}`;
  const supersedeOtherIssues = async canonicalNumber => {
    for (const issue of ownedIssues) {
      if (issue.number === canonicalNumber) continue;
      const issueBody = bodyText(issue.body);
      const wasCurrent = hasExactMarker(issueBody, CURRENT_INCIDENT_MARKER);
      if (issue.state === 'open' || wasCurrent) {
        let nextBody = issueBody.split(CURRENT_INCIDENT_MARKER).join('')
          .replace(/^\n+/, '');
        if (!hasExactMarker(nextBody, OWNERSHIP_MARKER)) {
          nextBody = `${OWNERSHIP_MARKER}\n${nextBody}`;
        }
        if (!hasExactMarker(nextBody, SUPERSEDED_MARKER)) {
          nextBody = [
            SUPERSEDED_MARKER,
            nextBody,
            '',
            '---',
            `Superseded by #${canonicalNumber}, the single current hourly ` +
              'publication incident. This ticket remains closed as history.',
            `Reconciliation run: ${runUrl}`,
          ].join('\n');
        }
        await github.rest.issues.update({
          owner: context.repo.owner,
          repo: context.repo.repo,
          issue_number: issue.number,
          body: nextBody,
          state: 'closed',
        });
      }
      await retireOwnerLabel({github, context, issue});
    }
  };
  await supersedeOtherIssues(currentIssue.number);

  let currentBody = originalBody.split(SUPERSEDED_MARKER).join('').replace(/^\n+/, '');
  if (!hasExactMarker(currentBody, OWNERSHIP_MARKER)) {
    currentBody = `${OWNERSHIP_MARKER}\n${currentBody}`;
  }
  if (!hasExactMarker(currentBody, CURRENT_INCIDENT_MARKER)) {
    currentBody = `${CURRENT_INCIDENT_MARKER}\n${currentBody}`;
  }
  if (targetedDnsRecovery && !hasExactMarker(currentBody, DNS_ONLY_INCIDENT_MARKER)) {
    currentBody = `${DNS_ONLY_INCIDENT_MARKER}\n${currentBody}`;
  }
  if (targetedQueueRecovery && !hasExactMarker(currentBody, QUEUE_ONLY_INCIDENT_MARKER)) {
    currentBody = `${QUEUE_ONLY_INCIDENT_MARKER}\n${currentBody}`;
  }
  if (currentIssue.state === 'closed' && hasExactMarker(currentBody, RECOVERED_MARKER)) {
    if (currentBody !== originalBody) {
      await github.rest.issues.update({
        owner: context.repo.owner,
        repo: context.repo.repo,
        issue_number: currentIssue.number,
        body: currentBody,
      });
    }
    console.log(`Current hourly incident #${currentIssue.number} is already recovered.`);
    return Object.freeze({action: 'already-recovered', issue: currentIssue.number});
  }

  const requiredRecoveryRuns = targetedDnsRecovery || targetedQueueRecovery ? 1 : 6;
  const transition = advanceRecoveryStreak(currentBody, validationStateSha);
  if (transition.streak < requiredRecoveryRuns) {
    if (transition.body !== originalBody) {
      await github.rest.issues.update({
        owner: context.repo.owner,
        repo: context.repo.repo,
        issue_number: currentIssue.number,
        body: transition.body,
      });
    }
    console.log(
      transition.credited
        ? `Current issue #${currentIssue.number} has ${transition.streak} of ` +
          `${requiredRecoveryRuns} required distinct healthy publication states ` +
          `(validation=${validationSource}, code=${validationCodeSha}).`
        : `State ${validationStateSha} already credited issue #${currentIssue.number}; ` +
          `recovery remains ${transition.streak} of ${requiredRecoveryRuns}.`,
    );
    return Object.freeze({
      action: transition.credited ? 'advanced' : 'duplicate-credit',
      issue: currentIssue.number,
      streak: transition.streak,
    });
  }

  const recoveredBody = hasExactMarker(transition.body, RECOVERED_MARKER)
    ? transition.body
    : `${RECOVERED_MARKER}\n${transition.body}`;
  if (currentIssue.state === 'closed') {
    await github.rest.issues.update({
      owner: context.repo.owner,
      repo: context.repo.repo,
      issue_number: currentIssue.number,
      body: recoveredBody,
    });
    currentIssue.body = recoveredBody;
    console.log(
      `Manually closed current issue #${currentIssue.number} recovered in ` +
      `${requiredRecoveryRuns} distinct eligible publication states.`,
    );
    return Object.freeze({action: 'recovered-closed', issue: currentIssue.number});
  }

  const closePayload = {
    owner: context.repo.owner,
    repo: context.repo.repo,
    issue_number: currentIssue.number,
    body: recoveredBody,
    state: 'closed',
  };
  const transientWriteStatuses = new Set([409, 429, 500, 502, 503, 504]);
  let closeError = null;
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      await github.rest.issues.update(closePayload);
      closeError = null;
      break;
    } catch (error) {
      closeError = error;
      if (attempt >= 2 || !transientWriteStatuses.has(error.status)) break;
      core.warning(
        `Transient close failure for hourly issue #${currentIssue.number}; retrying once.`,
      );
    }
  }
  if (closeError) {
    const readback = await github.rest.issues.get({
      owner: context.repo.owner,
      repo: context.repo.repo,
      issue_number: currentIssue.number,
    });
    if (readback.data.state !== 'closed' ||
        !hasExactMarker(readback.data.body, RECOVERED_MARKER)) {
      throw closeError;
    }
  }
  currentIssue.state = 'closed';
  currentIssue.body = recoveredBody;

  const validationEvidence = validationSource === 'targeted-dns'
    ? `The targeted DNS generation was validated in exact canonical state ` +
      `\`${validationStateSha}\` for code \`${validationCodeSha}\`.`
    : validationSource === 'targeted-queue'
      ? `The targeted queue generation was validated in exact canonical state ` +
        `\`${validationStateSha}\` for code \`${validationCodeSha}\`.`
      : validationSource === 'separate-ci'
        ? `Separate CI validated code SHA \`${validationCodeSha}\`` +
          (validationCiUrl ? ` ([run](${validationCiUrl})).` : '.')
        : `This publication's deterministic suite validated \`${validationCodeSha}\`.`;
  try {
    await github.rest.issues.createComment({
      owner: context.repo.owner,
      repo: context.repo.repo,
      issue_number: currentIssue.number,
      body: [
        `The active hourly publication incident was absent in ` +
          `${requiredRecoveryRuns} distinct eligible healthy publication states.`,
        '',
        validationEvidence,
        `**Publication run:** ${runUrl}`,
      ].join('\n'),
    });
  } catch (error) {
    core.warning(
      `Closed hourly issue #${currentIssue.number}, but could not add the recovery ` +
      `comment: ${String(error.message || error)}`,
    );
  }
  console.log(`Closed hourly CI incident #${currentIssue.number} after recovery.`);
  return Object.freeze({action: 'closed', issue: currentIssue.number});
}

module.exports = Object.freeze({
  CURRENT_INCIDENT_MARKER,
  DNS_ONLY_INCIDENT_MARKER,
  FINGERPRINT_MARKER_PATTERN,
  HOURLY_OWNER_LABEL,
  LEGACY_SIGNATURE,
  OWNERSHIP_MARKER,
  QUEUE_ONLY_INCIDENT_MARKER,
  RECOVERED_MARKER,
  RECOVERY_CREDIT_PATTERN,
  RECOVERY_CREDIT_PREFIX,
  RECOVERY_MARKER_PATTERN,
  RECOVERY_MARKER_PREFIX,
  SUPERSEDED_MARKER,
  ZERO_RECOVERY_MARKER,
  advanceRecoveryStreak,
  classifyFailureTransition,
  closeHourlyIncident,
  ensureOwnerLabel,
  findCanonicalIncident,
  hasExactMarker,
  isStrictLegacySurfaceOnlyIncident,
  issueHasLabel,
  parseRecoveryStreak,
  recoveryMarker,
  recoveryCreditMarker,
  resetRecoveryStreak,
  retireOwnerLabel,
  selectCanonicalIncident,
  setRecoveryStreak,
  suppressedDegradationRecoveryTransition,
});
