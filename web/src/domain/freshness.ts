import targets from '@config/data_freshness_targets.json'
import type { Tone } from '@/lib/tone'
import { age, shortDate, type Rec } from './format'

export type FreshnessState = 'fresh' | 'warn' | 'stale'

interface GroupTarget {
  sources: string[]
  warn_minutes: number
  stale_minutes: number
}

interface SourceTarget {
  label: string
  area: string
  kind: string
  collector_limit_minutes?: number
  path?: string
}

interface FreshnessTargets {
  core: GroupTarget
  queue: GroupTarget
  default_collector_limit_minutes: number
  sources: Record<string, SourceTarget>
}

const TARGETS: FreshnessTargets = targets
const MINUTE_MS = 60_000
const SEVERITY: Record<FreshnessState, number> = { fresh: 0, warn: 1, stale: 2 }

export interface SourceFreshness {
  key: string
  label: string
  area: string
  /** Upstream activity (e.g. the latest AMD nightly) informs but never counts as collection age. */
  informational: boolean
  timestamp: string | null
  ageMs: number | null
  state: FreshnessState
  warnMinutes: number
  staleMinutes: number
  path: string | null
  timestampSource: string | null
  published: boolean
}

export interface FreshnessSummary {
  /** The oldest source timestamp, or null when any source has none. */
  timestamp: string | null
  ageMs: number | null
  state: FreshnessState
  sources: SourceFreshness[]
}

function limits(key: string, target: SourceTarget) {
  const group = [TARGETS.core, TARGETS.queue].find((candidate) => candidate.sources.includes(key))
  if (group) return { warnMinutes: group.warn_minutes, staleMinutes: group.stale_minutes }
  const limit = target.collector_limit_minutes ?? TARGETS.default_collector_limit_minutes
  return { warnMinutes: limit, staleMinutes: limit }
}

function parse(timestamp: unknown): number | null {
  if (typeof timestamp !== 'string' || !timestamp) return null
  const ms = Date.parse(timestamp)
  return Number.isFinite(ms) ? ms : null
}

function stateOf(ageMs: number | null, warnMinutes: number, staleMinutes: number): FreshnessState {
  if (ageMs === null || ageMs > staleMinutes * MINUTE_MS) return 'stale'
  return ageMs > warnMinutes * MINUTE_MS ? 'warn' : 'fresh'
}

/**
 * Every configured source with its collection age and state, from the manifest shell
 * (`ops.sources`) and, for perf_eval when the shell does not list it, perf_eval.json itself.
 * A source without a timestamp is stale: its age cannot be shown to be within target.
 */
export function sourceFreshness(ops: Rec | null | undefined, perf: Rec | null | undefined, nowMs: number): SourceFreshness[] {
  const records: Rec = ops?.sources ?? {}
  return Object.entries(TARGETS.sources).map(([key, target]) => {
    const record: Rec = records[key] ?? (key === 'perf_eval' && perf ? { timestamp: perf.generated_at, timestamp_source: 'payload' } : {})
    const timestamp = record.timestamp || record.generated_at || null
    const observedMs = parse(timestamp)
    const ageMs = observedMs === null ? null : Math.max(0, nowMs - observedMs)
    const { warnMinutes, staleMinutes } = limits(key, target)
    return {
      key,
      label: target.label,
      area: target.area,
      informational: target.kind === 'upstream_activity',
      timestamp: observedMs === null ? null : timestamp,
      ageMs,
      state: stateOf(ageMs, warnMinutes, staleMinutes),
      warnMinutes,
      staleMinutes,
      path: record.path ?? target.path ?? null,
      timestampSource: record.timestamp_source ?? null,
      published: record.published !== false,
    }
  })
}

function summarize(rows: SourceFreshness[]): FreshnessSummary | null {
  if (!rows.length) return null
  const missing = rows.some((row) => row.ageMs === null)
  const oldest = missing ? null : rows.reduce((a, b) => (b.ageMs! > a.ageMs! ? b : a))
  const state = rows.reduce<FreshnessState>((worst, row) => (SEVERITY[row.state] > SEVERITY[worst] ? row.state : worst), 'fresh')
  return { timestamp: oldest?.timestamp ?? null, ageMs: oldest?.ageMs ?? null, state, sources: rows }
}

const pick = (rows: SourceFreshness[], keys: readonly string[]) => rows.filter((row) => keys.includes(row.key))

/** Core collector data: as old as its oldest core source. */
export function coreFreshness(rows: SourceFreshness[]): FreshnessSummary | null {
  return summarize(pick(rows, TARGETS.core.sources))
}

export function queueFreshness(rows: SourceFreshness[]): FreshnessSummary | null {
  return summarize(pick(rows, TARGETS.queue.sources))
}

/** One page area's collector sources; upstream activity sources are excluded. */
export function areaFreshness(rows: SourceFreshness[], area: string): FreshnessSummary | null {
  return summarize(rows.filter((row) => row.area === area && !row.informational))
}

/** Rows of the "Data freshness" sheet: worst state first, then by label. */
export function freshnessRows(ops: Rec | null | undefined, perf: Rec | null | undefined, nowMs: number): SourceFreshness[] {
  return sourceFreshness(ops, perf, nowMs).sort(
    (left, right) => SEVERITY[right.state] - SEVERITY[left.state] || left.label.localeCompare(right.label),
  )
}

export interface FreshnessChip {
  text: string
  tone: Tone
  detail: string
}

const CHIP_TONE: Record<FreshnessState, Tone> = { fresh: 'pass', warn: 'soft', stale: 'hard' }

/** A page header chip for one collector source: its age, state, and the target it is held to. */
export function sourceChip(row: SourceFreshness, nowMs: number): FreshnessChip {
  const when = row.timestamp ? `${shortDate(row.timestamp)} (${age(row.timestamp, nowMs)})` : 'at an unknown time'
  const limits =
    row.warnMinutes === row.staleMinutes
      ? `Stale after ${row.staleMinutes} minutes.`
      : `Target within ${row.warnMinutes} minutes; stale after ${row.staleMinutes} minutes.`
  return {
    text: row.timestamp ? `Collected ${age(row.timestamp, nowMs)}` : 'Collection time unknown',
    tone: CHIP_TONE[row.state],
    detail: `${row.label} was collected ${when}. ${limits}${row.path ? ` Source: ${row.path}.` : ''}`,
  }
}
