import { integer, value, type Rec, type Tone } from './format'
import { currentAmdHealth } from './health'
import { nightlyFailureMovement } from './nightly'

export type AmdState = 'passed' | 'soft' | 'hard' | 'missing' | 'unknown'

export function observationState(observation: Rec): string {
  return String(observation.state || observation.result || observation.status || 'unknown').toLowerCase()
}

const INCIDENT_STATES = [
  'hard', 'soft', 'incident', 'error', 'failed', 'failing', 'soft_fail', 'soft_failed', 'timed_out', 'broken', 'canceled', 'expired',
]

export function isIncidentObservation(observation: Rec): boolean {
  return INCIDENT_STATES.includes(observationState(observation))
}

export function observationTimestamp(observation: Rec): string | null {
  return observation.observed_at || observation.finished_at || observation.created_at || observation.date || null
}

export function amdHealthGroups(amdHealth: Rec | null | undefined): Rec[] {
  return amdHealth &&
    amdHealth.source_pipeline === 'ci' &&
    amdHealth.job_scope === 'amd_gpu' &&
    amdHealth.hardware_scope === 'amd_mi_gpu' &&
    Array.isArray(amdHealth.group_catalog)
    ? amdHealth.group_catalog
    : []
}

export function amdGroupObservations(row: Rec | null | undefined): Rec[] {
  return Array.isArray((row || {}).observations)
    ? row!.observations
        .slice()
        .sort(
          (a: Rec, b: Rec) =>
            new Date(observationTimestamp(a) || 0).getTime() - new Date(observationTimestamp(b) || 0).getTime(),
        )
    : []
}

export function amdGroupPassRate(row: Rec | null | undefined): number | null {
  if (Number.isFinite(Number(row && row.pass_rate_pct))) return Number(row!.pass_rate_pct)
  const r = row || {}
  const known = Number(r.passed || 0) + Number(r.soft_failed || 0) + Number(r.hard_failed || 0)
  return known ? (Number(r.passed || 0) / known) * 100 : null
}

export function amdLatestState(row: Rec, latestBuild: unknown): AmdState {
  if (latestBuild && Number(row.latest_build_number) !== Number(latestBuild)) return 'missing'
  const result = String(row.latest_state || 'unknown').toLowerCase()
  if (['soft', 'soft_fail', 'soft_failed'].includes(result)) return 'soft'
  if (['hard', 'incident', 'error', 'failed', 'timed_out', 'broken', 'canceled'].includes(result)) return 'hard'
  return result === 'passed' ? 'passed' : 'unknown'
}

export function amdStateLabel(result: string): string {
  if (result === 'passed') return 'Passing'
  if (result === 'soft') return 'Soft fail'
  if (result === 'hard') return 'Hard fail'
  if (result === 'missing') return 'Not in latest'
  return 'No result'
}

export function logicalTestGroupPresentation(groups: Rec | null | undefined) {
  const summary = groups || {}
  if (summary.available === false || summary.total === undefined) {
    return { available: false, value: 'Unavailable', meta: 'No aligned runtime test-group signal', tone: 'soft' as Tone }
  }
  const total = Number(summary.total || 0)
  const partial = Number(summary.partial || 0)
  const passingAny = Number(summary.passing || 0)
  const passingAll = summary.passing_all === undefined ? Math.max(0, passingAny - partial) : Number(summary.passing_all || 0)
  const nonPassing = summary.non_passing === undefined ? Math.max(0, total - passingAny) : Number(summary.non_passing || 0)
  return {
    available: true,
    value: integer(passingAny) + ' / ' + integer(total) + ' passing',
    meta:
      integer(passingAll) +
      ' pass on every route · ' +
      integer(partial) +
      ' pass on some hardware only · ' +
      integer(nonPassing) +
      ' non-passing everywhere',
    tone: (partial || nonPassing ? 'soft' : 'pass') as Tone,
  }
}

/** The build-pinned logical inventory, only when it reconciles with the published counts. */
export function amdLogicalInventory(amdHealth: Rec | null | undefined): Rec {
  const current = currentAmdHealth(amdHealth)
  const inventory = current.latest_logical_test_groups || {}
  const counts = (current.summary || {}).latest_test_group_counts || {}
  const reconciliation = inventory.reconciliation || {}
  const inventoryBuild = Number(inventory.build_number)
  const countsBuild = Number(counts.build_number)
  const buildAligned =
    Number.isInteger(inventoryBuild) &&
    inventoryBuild > 0 &&
    Number.isInteger(countsBuild) &&
    countsBuild > 0 &&
    inventoryBuild === countsBuild
  if (
    inventory.available !== true ||
    reconciliation.matches_latest_test_group_counts !== true ||
    counts.available !== true ||
    !buildAligned ||
    !Array.isArray(inventory.rows) ||
    Number(counts.total) !== inventory.rows.length
  ) {
    return { ...inventory, available: false, rows: [] }
  }
  return inventory
}

export function amdLogicalStateLabel(stateName: string): string {
  if (stateName === 'passing_all') return 'Passes every route'
  if (stateName === 'partial') return 'Passes some routes'
  if (stateName === 'non_passing') return 'Non-passing'
  return 'Unresolved'
}

export function amdLogicalStateTone(stateName: string): Tone {
  if (stateName === 'passing_all') return 'pass'
  if (stateName === 'partial') return 'soft'
  if (stateName === 'non_passing') return 'hard'
  return 'neutral'
}

export function amdLogicalSignalLabel(stateName: string): string {
  if (stateName === 'passing') return 'Passing'
  if (stateName === 'failing') return 'Failing'
  return 'No pass signal'
}

export function amdLogicalSignalTone(stateName: string): Tone {
  if (stateName === 'passing') return 'pass'
  if (stateName === 'failing') return 'hard'
  return 'neutral'
}

export function amdLogicalCatalogGroup(variant: Rec | null | undefined, amdHealth: Rec | null | undefined): Rec | null {
  const variantId = String((variant || {}).id || '')
  const exactName = String((variant || {}).exact_job_name || '')
  return (
    amdHealthGroups(amdHealth).find(
      (row) => (variantId && String(row.id || '') === variantId) || (exactName && String(row.name || row.job_name || '') === exactName),
    ) || null
  )
}

/** Non-passing first, then partial, then passing; alphabetical within a state. */
export function sortLogicalRows(rows: Rec[]): Rec[] {
  const rank: Record<string, number> = { non_passing: 0, partial: 1, passing_all: 2 }
  return Array.from(rows || []).sort(
    (left, right) =>
      Number(rank[left.state] === undefined ? 3 : rank[left.state]) - Number(rank[right.state] === undefined ? 3 : rank[right.state]) ||
      String(left.label || left.logical_key).localeCompare(String(right.label || right.logical_key)),
  )
}

/** Latest non-passing exact job variants, hard failures first, then lowest pass rate. */
export function latestFailureObservations(amdHealth: Rec | null | undefined): Rec[] {
  const health = currentAmdHealth(amdHealth)
  const latestBuild = (health.summary || {}).latest_build_number
  return amdHealthGroups(health)
    .filter((row) => Number(row.latest_build_number) === Number(latestBuild))
    .filter((row) => ['soft', 'hard'].includes(amdLatestState(row, latestBuild)))
    .sort(
      (a, b) =>
        (amdLatestState(a, latestBuild) === 'hard' ? 0 : 1) - (amdLatestState(b, latestBuild) === 'hard' ? 0 : 1) ||
        Number(amdGroupPassRate(a) || 0) - Number(amdGroupPassRate(b) || 0),
    )
}

/** Oldest-first nightlies (up to 14) whose failure movement is available. */
export function movementBuilds(builds: Rec[]): Rec[] {
  return (builds || [])
    .filter((row) => {
      const movement = nightlyFailureMovement(row)
      return row.has_test_results !== false && Boolean(movement) && movement!.available !== false
    })
    .slice(0, 14)
    .reverse()
}

export function testGroupParityState(stateName: string): { label: string; tone: Tone } {
  const presentations: Record<string, { label: string; tone: Tone }> = {
    existing: { label: '● Covered on main', tone: 'pass' },
    unsupported: { label: '■ Not targeted / unsupported', tone: 'not-targeted' },
    action: { label: '● Potential open gap', tone: 'hard' },
  }
  return presentations[stateName] || { label: value(stateName), tone: 'neutral' }
}

export function testGroupParityRows(payload: Rec | null | undefined, stateName: string, areaName: string): Rec[] {
  const rank: Record<string, number> = { action: 0, unsupported: 1, existing: 2 }
  return (Array.isArray((payload || {}).groups) ? payload!.groups : [])
    .filter((row: Rec) => (stateName === 'all' || row.state === stateName) && (areaName === 'all' || row.area === areaName))
    .sort(
      (left: Rec, right: Rec) =>
        Number(rank[left.state] || 0) - Number(rank[right.state] || 0) ||
        String(left.area || '').localeCompare(String(right.area || '')) ||
        Number(left.id || 0) - Number(right.id || 0),
    )
}

export function testGroupDefinitionState(row: Rec): string {
  const status = (row.definition_resolution || {}).status
  const labels: Record<string, string> = {
    resolved: 'Current',
    unresolved: 'Removed or changed',
    ambiguous: 'Mapping needs review',
    unavailable: 'Snapshot unavailable',
    unconfigured: 'Unlinked',
  }
  return labels[status] || 'Current source snapshot'
}

export const ROUTE_MODE_LABELS: Record<string, string> = {
  required: 'Required',
  optional: 'Optional',
  soft_fail: 'Soft fail',
  missing: 'Missing',
  not_applicable: 'Not applicable',
}

export interface GapArea {
  label: string
  attention: number
  countLabel: string
  total: number
  rows: Rec[]
  segments: { label: string; count: number; tone: Tone }[]
}

/** Test areas with potential open gaps, most gaps first. */
export function parityGapAreas(parity: Rec): GapArea[] {
  const areas: Rec[] = Array.isArray(parity.areas) ? parity.areas : []
  const missingRows = testGroupParityRows(parity, 'action', 'all')
  return areas
    .filter((row) => Number(row.action || 0) > 0)
    .map((row) => {
      const action = Number(row.action || 0)
      return {
        label: row.area,
        attention: action,
        countLabel: integer(action) + ' potential open ' + (action === 1 ? 'gap' : 'gaps'),
        total: Number(row.total || 0),
        rows: missingRows.filter((group) => group.area === row.area),
        segments: [
          { label: 'covered on main', count: Number(row.existing || 0), tone: 'pass' as Tone },
          { label: 'potential open gaps', count: action, tone: 'hard' as Tone },
          { label: 'not targeted', count: Number(row.unsupported || 0), tone: 'not-targeted' as Tone },
        ],
      }
    })
    .sort((left, right) => right.attention - left.attention || left.label.localeCompare(right.label))
}

export interface LogicalHealthPoint {
  number: number
  rate: number
}

/** Logical pass rate (passing on any route) per retained nightly with a test signal, oldest first. */
export function logicalHealthHistory(builds: Rec[] | null | undefined): LogicalHealthPoint[] {
  return (builds || [])
    .filter((build) => build.has_test_results !== false && Number(build.unique_test_groups || 0) > 0)
    .map((build) => ({
      number: Number(build.number),
      rate: (Number(build.test_groups_passing_or || 0) / Number(build.unique_test_groups)) * 100,
    }))
    .reverse()
}

export interface JobVariantPoint {
  number: number
  observed: number
  passed: number
  soft: number
  hard: number
}

/** Exact job-variant outcomes per retained AMD build, oldest first. */
export function jobVariantHistory(amdHealth: Rec | null | undefined): JobVariantPoint[] {
  const builds: Rec[] = Array.isArray(currentAmdHealth(amdHealth).builds) ? currentAmdHealth(amdHealth).builds : []
  return builds
    .filter((build) => Number(build.observed_job_variants ?? build.observed ?? 0) > 0)
    .map((build) => ({
      number: Number(build.build_number ?? build.number),
      observed: Number(build.observed_job_variants ?? build.observed ?? 0),
      passed: Number(build.passed_job_variants ?? build.passed ?? 0),
      soft: Number(build.soft_failed_job_variants ?? build.soft_failed ?? 0),
      hard: Number(build.hard_failed_job_variants ?? build.hard_failed ?? 0),
    }))
    .sort((left, right) => left.number - right.number)
}

/** The configured-route sentence of the upstream parity methodology. */
export function parityConfigNote(summary: Rec, source: Rec): string {
  const required = summary.main_required_groups
  const rate = summary.main_required_rate_pct
  const commit = source.current_definition_commit_sha || source.main_commit || ''
  return (
    `Configured AMD routes cover ${integer(summary.main_complete_groups)} of ${integer(summary.applicable_groups)} applicable current CUDA logical groups. ` +
    (required === undefined
      ? 'Required-route coverage is unavailable. '
      : `${integer(required)} groups have a required source configuration` +
        (Number.isFinite(Number(rate)) ? ` (${Number(rate).toFixed(1)}%)` : '') +
        `; ${integer(summary.main_optional_only_groups)} are optional only and ${integer(summary.main_soft_fail_only_groups)} are soft fail only. `) +
    'These are source configuration flags; individual nightly scheduling and blocking outcomes are separate.' +
    (/^[0-9a-f]{40}$/.test(commit) ? ` Source: main CI at ${commit.slice(0, 12)}.` : '')
  )
}

export interface HardwarePassPoint {
  number: number
  observedAt: string | null
  observed: number
  passed: number
}

/** Exact job outcomes per retained AMD build for each hardware family (lower-case id), oldest build first. */
export function hardwareVariantHistory(amdHealth: Rec | null | undefined): Map<string, HardwarePassPoint[]> {
  const byHardware = new Map<string, Map<number, HardwarePassPoint>>()
  for (const row of amdHealthGroups(currentAmdHealth(amdHealth))) {
    for (const observation of amdGroupObservations(row)) {
      const number = Number(observation.build_number)
      const state = observationState(observation)
      const passed = state === 'passed'
      if (!Number.isFinite(number) || number <= 0 || (!passed && !isIncidentObservation(observation))) continue
      const hardware = String(observation.hardware || row.hardware || 'unknown').toLowerCase()
      const builds = byHardware.get(hardware) ?? new Map<number, HardwarePassPoint>()
      const point = builds.get(number) ?? { number, observedAt: null, observed: 0, passed: 0 }
      const when = observationTimestamp(observation)
      if (when && (!point.observedAt || when < point.observedAt)) point.observedAt = when
      point.observed += 1
      if (passed) point.passed += 1
      builds.set(number, point)
      byHardware.set(hardware, builds)
    }
  }
  return new Map([...byHardware].map(([hardware, builds]) => [hardware, [...builds.values()].sort((a, b) => a.number - b.number)]))
}
