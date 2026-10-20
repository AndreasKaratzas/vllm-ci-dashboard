import { integer, value, type Rec, type Tone } from './format'

export const OPS_SNAPSHOT_MAX_AGE_MS = 3 * 60 * 60 * 1000
const CONFIRMED_INCIDENT_POLICY_ID = 'confirmed-incidents-v1'
const OBSERVED_FAILURE_MOVEMENT_ID = 'observed-failure-movement-v1'
const MOVEMENT_KEYS = ['new', 'recurring', 'fixed'] as const
export type MovementKey = (typeof MOVEMENT_KEYS)[number]

function validCohort(candidate: Rec | null | undefined) {
  return (
    !!candidate &&
    candidate.source_pipeline === 'ci' &&
    candidate.cohort_id === 'ci-amd' &&
    candidate.job_scope === 'amd_gpu' &&
    candidate.hardware_scope === 'amd_mi_gpu'
  )
}

/** The single canonical AMD MI main-CI nightly cohort, or an empty one. */
export function latestAmd(ops: Rec | null | undefined): Rec {
  const nightly = (ops || {}).nightly || {}
  const empty = {
    pipeline: 'ci',
    source_pipeline: 'ci',
    job_scope: 'amd_gpu',
    hardware_scope: 'amd_mi_gpu',
    cohort_id: 'ci-amd',
    builds: [],
  }
  const cohorts = (Array.isArray(nightly.pipelines) ? nightly.pipelines : []).filter(validCohort)
  if (cohorts.length === 1) return cohorts[0]
  return cohorts.length === 0 && validCohort(nightly.canonical_history) ? nightly.canonical_history : empty
}

export function ciHealthPublicationRetentionMessage(nightly: Rec | null | undefined): string {
  const retention = (nightly || {}).ci_health_publication_retention || {}
  const builds = retention.builds || {}
  if (retention.complete_relative_to_source !== false) return ''
  return (
    'The CI-health reporter retained ' +
    integer(builds.published) +
    ' of ' +
    integer(builds.source) +
    ' newest whole build summaries. Latest-build and aggregate scalars remain source-complete; omitted historical enrichments are not inferred, and no rate or delta is derived from them.'
  )
}

export function nightlyFailureMovement(build: Rec | null | undefined): Rec | null {
  const record = build || {}
  const published = record.failure_movement || {}
  if (published.policy_id === OBSERVED_FAILURE_MOVEMENT_ID && MOVEMENT_KEYS.every((key) => Array.isArray(published[key]))) {
    return published
  }
  const transitions = (record.transitions || {}).policy_id === CONFIRMED_INCIDENT_POLICY_ID ? record.transitions : null
  if (!transitions || !MOVEMENT_KEYS.every((key) => Array.isArray(transitions[key]))) return null
  const currentPending = (transitions.pending_soft || []).filter((row: Rec) =>
    ['pending_started', 'pending_advanced'].includes(String((row || {}).transition_change || '')),
  )
  const newlyConfirmedRecurring = transitions.new.filter(
    (row: Rec) =>
      String((row || {}).current_severity || '') === 'soft' &&
      Number((row || {}).soft_streak || 0) > 1 &&
      String((row || {}).transition_change || '') === 'confirmed',
  )
  const newlyObserved = transitions.new.filter((row: Rec) => !newlyConfirmedRecurring.includes(row))
  const available =
    record.has_test_results !== false &&
    record.transition_eligible !== false &&
    Number(transitions.preceding_build_number || 0) > 0
  return {
    policy_id: 'frontend-compatible-failure-movement',
    available,
    preceding_build_number: transitions.preceding_build_number,
    new: newlyObserved.concat(currentPending),
    recurring: (transitions.recurring || []).concat(newlyConfirmedRecurring),
    fixed: transitions.fixed || [],
  }
}

export function nightlyFailureCount(build: Rec | null | undefined, key: MovementKey): number | null {
  const movement = nightlyFailureMovement(build)
  return movement && movement.available !== false && Array.isArray(movement[key]) ? movement[key].length : null
}

function amdNightlyMovement(build: Rec) {
  const movement = nightlyFailureMovement(build)
  const policyAvailable = Boolean(movement) && movement!.available !== false
  const hasComparison = policyAvailable && Number(movement!.preceding_build_number || 0) > 0
  const newCount = movement && Array.isArray(movement.new) ? movement.new.length : 0
  const recurringCount = movement && Array.isArray(movement.recurring) ? movement.recurring.length : 0
  const fixedCount = movement && Array.isArray(movement.fixed) ? movement.fixed.length : 0
  return {
    policyAvailable,
    hasComparison,
    newCount,
    recurringCount,
    fixedCount,
    currentFailures: newCount + recurringCount,
    delta: newCount - fixedCount,
  }
}

export interface NightlyPresentation {
  label: string
  tone: Tone
  meta: string
  hasSignal: boolean
  movementLabel: string
  movementMeta: string
  movementTone: Tone
  hasComparison: boolean
  incidentCount: number
  incidentDelta: number | null
}

const IN_PROGRESS_STATES = ['running', 'scheduled', 'creating', 'assigned', 'starting']
const BUILDKITE_RUNNING_STATES = ['running', 'failing', 'canceling']

/**
 * Why a nightly has final AMD results while Buildkite still runs it, e.g.
 * "AMD jobs complete · build still running (2 non-AMD jobs)"; null otherwise.
 */
export function amdCompleteLabel(build: Rec | null | undefined): string | null {
  const record = build || {}
  const sourceState = String(record.source_state || '').toLowerCase()
  if (record.amd_complete !== true || !BUILDKITE_RUNNING_STATES.includes(sourceState)) return null
  const active = Number(record.non_amd_jobs_active || 0)
  return (
    'AMD jobs complete · build ' +
    (sourceState === 'canceling' ? 'canceling' : 'still running') +
    (active > 0 ? ' (' + integer(active) + ' non-AMD ' + (active === 1 ? 'job' : 'jobs') + ')' : '')
  )
}

export function amdNightlyPresentation(
  build: Rec | null | undefined,
  healthSummary: Rec | null | undefined,
  snapshotGeneratedAt: unknown,
  nowMs: number = Date.now(),
): NightlyPresentation {
  const record = build || {}
  const summary = healthSummary || {}
  const latestStates = summary.latest_job_variant_state_counts || summary.latest_state_counts || {}
  const latestVariantCount = Number(
    summary.latest_job_variant_count !== undefined ? summary.latest_job_variant_count : summary.latest_group_count || 0,
  )
  const signalBuild = Number(summary.latest_build_number || 0)
  const pipelineBuild = Number(record.number || 0)
  const pipelineState = String(record.state || 'unknown').toLowerCase()
  const inProgress = IN_PROGRESS_STATES.includes(pipelineState)
  const snapshotTimestamp = Date.parse(String(snapshotGeneratedAt || ''))
  const snapshotStale = Number.isFinite(snapshotTimestamp) && nowMs - snapshotTimestamp > OPS_SNAPSHOT_MAX_AGE_MS
  const blocked = Number(record.test_jobs_blocked || 0)
  const explicitlyNoSignal = Object.prototype.hasOwnProperty.call(record, 'has_test_results') && !record.has_test_results
  const summaryMatchesBuild = latestVariantCount > 0 && (!signalBuild || !pipelineBuild || signalBuild === pipelineBuild)

  const stateCount = (keys: string[], fallback: number) => {
    for (const key of keys) {
      if (Object.prototype.hasOwnProperty.call(latestStates, key) && Number.isFinite(Number(latestStates[key]))) {
        return Number(latestStates[key])
      }
    }
    return Number(fallback || 0)
  }

  const fallbackHard = Array.isArray(record.failed_groups) ? record.failed_groups.length : 0
  const fallbackSoft = Array.isArray(record.soft_failed_groups) ? record.soft_failed_groups.length : 0
  const fallbackTotal = Number(record.total_groups || 0)
  const hard = summaryMatchesBuild ? stateCount(['hard', 'failed'], fallbackHard) : fallbackHard
  const soft = summaryMatchesBuild ? stateCount(['soft', 'soft_fail'], fallbackSoft) : fallbackSoft
  const passed = summaryMatchesBuild
    ? stateCount(['passed'], Math.max(0, fallbackTotal - hard - soft))
    : Math.max(0, fallbackTotal - hard - soft)
  const observedGroups = summaryMatchesBuild ? latestVariantCount : fallbackTotal
  const hasSignal = !explicitlyNoSignal && Math.max(observedGroups, passed + soft + hard) > 0
  const movement = amdNightlyMovement(record)
  const incidentCount = hard + soft
  const comparisonReliable = hasSignal && movement.hasComparison && movement.currentFailures === incidentCount
  const buildPrefix = pipelineBuild ? '#' + pipelineBuild + ' - ' : ''

  if (!hasSignal) {
    const retrying = !blocked && record.active_retry === true
    return {
      label: blocked
        ? 'Infra blocked'
        : retrying
          ? 'AMD retry running'
          : inProgress
            ? snapshotStale
              ? 'Snapshot stale'
              : 'Awaiting results'
            : 'No test signal',
      tone: blocked ? 'hard' : retrying || (inProgress && !snapshotStale) ? 'info' : 'soft',
      meta:
        buildPrefix +
        (blocked
          ? integer(blocked) + ' test groups never started'
          : retrying
            ? 'an AMD job was retried; results return once the retry settles'
            : inProgress
            ? snapshotStale
              ? 'Last published while Buildkite was running; no parsed test groups in this snapshot'
              : 'Buildkite is running; no parsed test groups yet'
            : 'no parsed test groups') +
        (signalBuild && signalBuild !== pipelineBuild ? '; latest test signal #' + signalBuild : ''),
      hasSignal: false,
      movementLabel: 'Movement unavailable',
      movementMeta: 'No test execution in the latest nightly; no change is inferred',
      movementTone: 'neutral',
      hasComparison: false,
      incidentCount: 0,
      incidentDelta: null,
    }
  }

  let movementLabel = !movement.policyAvailable
    ? 'Movement unavailable'
    : movement.hasComparison
      ? 'Movement unavailable'
      : 'No prior comparison'
  let movementMeta = !movement.policyAvailable
    ? 'Snapshot has no comparable failure movement'
    : movement.hasComparison
      ? 'Failure movement does not match the latest observed signal'
      : 'No comparable preceding eligible nightly'
  let movementTone: Tone = incidentCount ? 'soft' : 'neutral'
  if (comparisonReliable) {
    movementLabel =
      movement.delta > 0
        ? '+' + integer(movement.delta) + ' failures'
        : movement.delta < 0
          ? integer(Math.abs(movement.delta)) + ' fewer failures'
          : 'No net change'
    movementMeta =
      integer(movement.newCount) +
      ' new - ' +
      integer(movement.recurringCount) +
      ' recurring - ' +
      integer(movement.fixedCount) +
      ' fixed'
    movementTone =
      movement.delta > 0
        ? 'hard'
        : movement.delta < 0
          ? 'pass'
          : movement.currentFailures
            ? 'soft'
            : 'pass'
  }

  let label: string
  let tone: Tone
  if (inProgress) {
    label = incidentCount ? 'Running with failures' : 'Running clean'
    tone = hard ? 'hard' : incidentCount ? 'soft' : 'info'
  } else if (['canceled', 'cancelled'].includes(pipelineState)) {
    label = 'Canceled'
    tone = 'soft'
  } else if (hard) {
    label = 'Hard failures'
    tone = 'hard'
  } else if (['failed', 'failing', 'blocked'].includes(pipelineState)) {
    label = 'Pipeline failed'
    tone = 'hard'
  } else if (!incidentCount) {
    label = comparisonReliable && movement.fixedCount ? 'Recovered' : 'Healthy'
    tone = 'pass'
  } else if (!comparisonReliable) {
    label = soft && !hard ? 'Soft observations' : 'Failures observed'
    tone = 'soft'
  } else if (movement.delta > 0) {
    label = 'More failures'
    tone = 'hard'
  } else if (movement.delta < 0) {
    label = 'Improved'
    tone = 'pass'
  } else if (movement.newCount || movement.fixedCount) {
    label = 'Changed, net even'
    tone = 'soft'
  } else {
    label = 'Stable failure count'
    tone = 'soft'
  }

  return {
    label,
    tone,
    meta:
      buildPrefix +
      integer(passed) +
      ' pass - ' +
      integer(soft) +
      ' soft - ' +
      integer(hard) +
      ' hard; ' +
      movementLabel +
      (inProgress
        ? '; provisional while Buildkite is running'
        : '; ' + (amdCompleteLabel(record) || 'Buildkite ' + value(record.state, 'unknown'))),
    hasSignal: true,
    movementLabel,
    movementMeta,
    movementTone,
    hasComparison: comparisonReliable,
    incidentCount,
    incidentDelta: comparisonReliable ? movement.delta : null,
  }
}

export type EvidenceScope = 'hard' | 'soft' | 'new' | 'recurring' | 'fixed' | 'movement'

const EVIDENCE_LABELS: Record<string, string> = {
  hard: 'Current hard failures',
  soft: 'Current soft failures',
  new: 'New failures',
  recurring: 'Recurring failures',
  fixed: 'Fixed job variants',
}

export interface BuildEvidence {
  scope: EvidenceScope
  label: string
  available: boolean
  rows: Rec[]
}

/** Job variants behind one nightly count (a severity, a movement bucket, or all movement). */
export function nightlyBuildEvidence(build: Rec, scope?: string): BuildEvidence {
  const movement = nightlyFailureMovement(build)
  const selectedScope = (scope && Object.prototype.hasOwnProperty.call(EVIDENCE_LABELS, scope) ? scope : 'movement') as EvidenceScope
  const movementAvailable = Boolean(movement) && movement!.available !== false
  const currentState = (row: Rec) => {
    const stateName = Object.prototype.hasOwnProperty.call(row, 'current_state') ? row.current_state : row.state || row.result
    return String(stateName || '').trim().toLowerCase()
  }
  const severity = (row: Rec) => {
    const stateName = currentState(row)
    if (['soft', 'soft_fail', 'soft_failed'].includes(stateName)) return 'soft'
    if (['hard', 'failed', 'failing', 'incident', 'error', 'timed_out', 'broken', 'canceled', 'cancelled', 'expired'].includes(stateName))
      return 'hard'
    return null
  }
  const currentObservation = (row: Rec) =>
    row.observed_in_current_build !== false &&
    (!row.build_number || !build.number || Number(row.build_number) === Number(build.number))

  let available = movementAvailable
  let rows: Rec[] = []
  if (selectedScope === 'hard' || selectedScope === 'soft') {
    const currentRows = build[selectedScope === 'hard' ? 'failed_groups' : 'soft_failed_groups']
    available = build.has_test_results !== false && (Array.isArray(currentRows) || movementAvailable)
    const candidates: Rec[] = Array.isArray(currentRows)
      ? currentRows
      : movementAvailable
        ? (movement!.new || []).concat(movement!.recurring || [])
        : []
    rows = available ? candidates.filter((row) => currentObservation(row) && severity(row) === selectedScope) : []
  } else if (movementAvailable) {
    const buckets: [MovementKey, string][] = [
      ['new', 'New failure'],
      ['recurring', 'Recurring failure'],
      ['fixed', 'Fixed'],
    ]
    for (const [key, lifecycle] of buckets) {
      if (selectedScope !== 'movement' && selectedScope !== key) continue
      for (const row of (movement![key] || []) as Rec[]) {
        if (selectedScope === 'fixed' && currentState(row) !== 'passed') continue
        if ((selectedScope === 'new' || selectedScope === 'recurring') && (!currentObservation(row) || !severity(row))) continue
        const currentEvidence =
          selectedScope === 'fixed' && row.current_url
            ? { url: row.current_url, job_url: row.current_url, build_number: build.number, state: currentState(row) }
            : {}
        rows.push({ ...row, ...currentEvidence, lifecycle })
      }
    }
  }
  return { scope: selectedScope, label: EVIDENCE_LABELS[selectedScope] || 'Failure movement', available, rows }
}
