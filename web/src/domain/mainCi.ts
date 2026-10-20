import type { Rec, Tone } from './format'

/**
 * The main CI read model (bundle section `main_ci`): every completed AMD MI build of the `ci`
 * pipeline on branch main, the main-failure watcher's incidents, and flaky and retry statistics.
 * Before the section loads, the manifest shell's `main_ci` headline carries the same summary
 * keys flat, so every summary reader accepts either shape.
 */

export interface AmdCounts {
  passed: number
  soft: number
  hard: number
  unknown: number
  notRun: number
  retried: number
}

export interface MainBuild {
  number: number
  createdAt: string
  commit: string
  headline: string
  nightly: boolean
  state: string
  /** AMD MI job counts (final attempt per group); null when the build has none recorded. */
  amd: AmdCounts | null
  url: string
}

export interface Last24h {
  builds: number
  amdBuilds: number
  greenBuilds: number
  passed: number
  soft: number
  hard: number
  passRate: number | null
}

export interface MainCiSummary {
  available: boolean
  /** Why the section is unavailable, as published (e.g. strict_all_main_reliability_unavailable). */
  reason: string | null
  /** When the all-main cohort behind every number was collected. */
  collectedAt: string | null
  buildCount: number | null
  latest: MainBuild | null
  last24h: Last24h | null
  activeIncidents: number | null
  hardIncidents: number | null
  pendingSoft: number | null
  flakyGroups: number | null
  alertingFlaky: number | null
  retryGroups: number | null
  alertingRetry: number | null
  retryAttempts: number | null
  recoveries: number | null
}

const num = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)
const str = (value: unknown): string => (typeof value === 'string' ? value : '')

/** The merged `main_ci` record of an operations payload, or null when the manifest has none. */
export function mainCiRecord(ops: Rec | null | undefined): Rec | null {
  const record = ops?.main_ci
  return record && typeof record === 'object' && !Array.isArray(record) ? record : null
}

function amdCounts(value: unknown): AmdCounts | null {
  if (!value || typeof value !== 'object') return null
  const counts = value as Rec
  const keys = ['passed', 'soft', 'hard', 'unknown', 'not_run', 'retried'] as const
  if (!keys.every((key) => num(counts[key]) !== null)) return null
  return {
    passed: counts.passed,
    soft: counts.soft,
    hard: counts.hard,
    unknown: counts.unknown,
    notRun: counts.not_run,
    retried: counts.retried,
  }
}

export function mainBuild(row: Rec): MainBuild {
  return {
    number: Number(row.number) || 0,
    createdAt: str(row.created_at),
    commit: str(row.commit),
    headline: str(row.headline),
    nightly: row.nightly === true,
    state: str(row.state) || 'unknown',
    amd: amdCounts(row.amd),
    url: str(row.url),
  }
}

/** Every published main build, newest first. */
export function mainBuilds(record: Rec | null | undefined): MainBuild[] {
  const rows = Array.isArray(record?.builds) ? (record!.builds as Rec[]) : []
  return rows
    .filter((row) => row && typeof row === 'object' && Number(row.number) > 0)
    .map(mainBuild)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt) || b.number - a.number)
}

export function mainCiSummary(record: Rec | null | undefined): MainCiSummary | null {
  if (!record) return null
  const flat: Rec = record.summary && typeof record.summary === 'object' ? record.summary : record
  const day: Rec | null = flat.last_24h && typeof flat.last_24h === 'object' ? flat.last_24h : null
  const latest: Rec | null = flat.latest_build && typeof flat.latest_build === 'object' ? flat.latest_build : null
  const available = record.available === true
  return {
    available,
    reason: available ? null : str(record.unavailable_reason) || 'main_ci_unavailable',
    collectedAt: str(record.source_generated_at) || null,
    buildCount: num(flat.builds),
    latest: latest ? mainBuild(latest) : null,
    last24h: day
      ? {
          builds: num(day.builds) ?? 0,
          amdBuilds: num(day.amd_builds) ?? 0,
          greenBuilds: num(day.green_builds) ?? 0,
          passed: num(day.passed) ?? 0,
          soft: num(day.soft) ?? 0,
          hard: num(day.hard) ?? 0,
          passRate: num(day.pass_rate),
        }
      : null,
    activeIncidents: num(flat.active_incidents),
    hardIncidents: num(flat.hard_incidents),
    pendingSoft: num(flat.pending_soft),
    flakyGroups: num(flat.flaky_groups),
    alertingFlaky: num(flat.alerting_flaky_groups),
    retryGroups: num(flat.retry_hotspot_groups),
    alertingRetry: num(flat.alerting_retry_groups),
    retryAttempts: num(flat.retry_attempts),
    recoveries: num(flat.failed_then_passed_recoveries),
  }
}

/** Passed share of the AMD jobs that reached a verdict, in percent; null without any. */
export function passRate(counts: Pick<AmdCounts, 'passed' | 'soft' | 'hard'> | null | undefined): number | null {
  if (!counts) return null
  const total = counts.passed + counts.soft + counts.hard
  return total ? (counts.passed / total) * 100 : null
}

/** A build's AMD outcome: any hard failure, else any soft failure, else a pass; no verdict is neutral. */
export function buildTone(build: Pick<MainBuild, 'amd'>): Tone {
  const amd = build.amd
  if (!amd) return 'neutral'
  if (amd.hard) return 'hard'
  if (amd.soft) return 'soft'
  return amd.passed ? 'pass' : 'neutral'
}

export function buildOutcome(build: Pick<MainBuild, 'amd'>): string {
  const amd = build.amd
  if (!amd || !(amd.passed + amd.soft + amd.hard)) return 'No AMD result'
  const parts = [amd.hard ? `${amd.hard} hard` : '', amd.soft ? `${amd.soft} soft` : ''].filter(Boolean)
  return parts.length ? parts.join(' · ') : 'Passed'
}

export type TrendWindow = '24h' | '7d' | '30d'
export const TREND_WINDOWS: readonly TrendWindow[] = ['24h', '7d', '30d']

export interface TrendPoint {
  /** Bucket start (ms) and end (ms, exclusive). */
  from: number
  to: number
  builds: number
  amdBuilds: number
  greenBuilds: number
  passed: number
  soft: number
  hard: number
  passRate: number | null
}

const HOUR_MS = 3_600_000
// Hours per point: 24h draws every hour, 7d six-hour blocks (28 points), 30d the published days.
const WINDOW_SHAPE: Record<TrendWindow, { series: 'hourly' | 'daily'; points: number; merge: number; stepMs: number }> = {
  '24h': { series: 'hourly', points: 24, merge: 1, stepMs: HOUR_MS },
  '7d': { series: 'hourly', points: 168, merge: 6, stepMs: HOUR_MS },
  '30d': { series: 'daily', points: 30, merge: 1, stepMs: 24 * HOUR_MS },
}

function merged(buckets: Rec[], stepMs: number): TrendPoint | null {
  const from = Date.parse(str(buckets[0]?.t))
  if (!Number.isFinite(from)) return null
  const sum = (key: string) => buckets.reduce((total, bucket) => total + (num(bucket[key]) ?? 0), 0)
  const point = {
    from,
    to: from + stepMs * buckets.length,
    builds: sum('builds'),
    amdBuilds: sum('amd_builds'),
    greenBuilds: sum('green_builds'),
    passed: sum('passed'),
    soft: sum('soft'),
    hard: sum('hard'),
  }
  return { ...point, passRate: passRate(point) }
}

/** The pass-rate trend of one window, oldest first; the newest block always ends at the newest bucket. */
export function trendPoints(record: Rec | null | undefined, window: TrendWindow): TrendPoint[] {
  const shape = WINDOW_SHAPE[window]
  const series: Rec[] = Array.isArray(record?.series?.[shape.series]) ? record!.series[shape.series] : []
  const recent = series.slice(-shape.points)
  // Blocks are cut from the newest end, so a partial block can only be the oldest one.
  const points: TrendPoint[] = []
  for (let end = recent.length; end > 0; end -= shape.merge) {
    const point = merged(recent.slice(Math.max(0, end - shape.merge), end), shape.stepMs)
    if (point) points.unshift(point)
  }
  return points
}

/** The pass rate of the 24 hours before the latest 24, from the hourly series; null without verdicts. */
export function previousDayRate(record: Rec | null | undefined): number | null {
  const hourly: Rec[] = Array.isArray(record?.series?.hourly) ? record!.series.hourly : []
  if (hourly.length < 48) return null
  const point = merged(hourly.slice(-48, -24), HOUR_MS)
  return point ? point.passRate : null
}

/** Builds created in [from, to), newest first. */
export function buildsBetween(builds: MainBuild[], from: number, to: number): MainBuild[] {
  return builds.filter((build) => {
    const at = Date.parse(build.createdAt)
    return at >= from && at < to
  })
}

export interface CommitRef {
  commit: string
  build: number | null
}

export interface MainIncident {
  groupId: string
  name: string
  hardware: string
  queue: string
  severity: 'hard' | 'soft'
  /** confirmed (active) or pending (a soft failure waiting for its second build). */
  status: string
  pending: boolean
  observedAt: string
  latestBuild: number | null
  latestBuildUrl: string
  jobUrl: string
  good: CommitRef
  bad: CommitRef
  rangeStatus: string
  compareUrl: string
  bisectCommand: string
}

export interface MainIncidents {
  available: boolean
  issue: { number: number; url: string } | null
  watcherLastRun: string | null
  suppressed: boolean
  active: MainIncident[]
  pending: MainIncident[]
}

const buildNumber = (value: unknown): number | null => (Number.isInteger(value) && Number(value) > 0 ? Number(value) : null)

function incident(row: Rec, pending: boolean): MainIncident {
  return {
    groupId: str(row.group_id),
    name: str(row.name),
    hardware: str(row.hardware),
    queue: str(row.queue),
    severity: row.severity === 'soft' ? 'soft' : 'hard',
    status: str(row.status) || (pending ? 'pending' : 'confirmed'),
    pending,
    observedAt: str(row.observed_at),
    latestBuild: buildNumber(row.latest_build_number),
    latestBuildUrl: str(row.latest_build_url),
    jobUrl: str(row.job_url),
    good: { commit: str(row.good_commit), build: buildNumber(row.good_build_number) },
    bad: { commit: str(row.bad_commit), build: buildNumber(row.bad_build_number) },
    rangeStatus: str(row.commit_range_status),
    compareUrl: str(row.compare_url),
    bisectCommand: str(row.bisect_command),
  }
}

export function mainIncidents(record: Rec | null | undefined): MainIncidents {
  const source: Rec = record?.incidents && typeof record.incidents === 'object' ? record.incidents : {}
  const issue: Rec | null = source.issue && typeof source.issue === 'object' ? source.issue : null
  return {
    available: source.available === true,
    issue: issue && buildNumber(issue.number) && str(issue.url) ? { number: issue.number, url: issue.url } : null,
    watcherLastRun: str(source.watcher_last_run) || null,
    suppressed: source.suppressed === true,
    active: (Array.isArray(source.active) ? source.active : []).map((row: Rec) => incident(row, false)),
    pending: (Array.isArray(source.pending_soft) ? source.pending_soft : []).map((row: Rec) => incident(row, true)),
  }
}

export interface TimelineEvent {
  event: string
  groupId: string
  name: string
  hardware: string
  queue: string
  severity: string
  build: number | null
  buildUrl: string
  at: string
  compareUrl: string
}

/** Incident lifecycle events (opened, escalated, resolved, ...), newest first as published. */
export function mainTimeline(record: Rec | null | undefined): TimelineEvent[] {
  const rows: Rec[] = Array.isArray(record?.timeline) ? record!.timeline : []
  return rows.map((row) => ({
    event: str(row.event) || 'event',
    groupId: str(row.group_id),
    name: str(row.name),
    hardware: str(row.hardware),
    queue: str(row.queue),
    severity: str(row.severity),
    build: buildNumber(row.build_number),
    buildUrl: str(row.build_url),
    at: str(row.at),
    compareUrl: str(row.compare_url),
  }))
}

/** Tone of an incident event: openings and escalations are failures, resolutions passes. */
export function eventTone(event: TimelineEvent): Tone {
  if (event.event === 'resolved') return 'pass'
  if (event.event === 'opened' || event.event === 'escalated') return event.severity === 'soft' ? 'soft' : 'hard'
  return 'neutral'
}

export function eventLabel(event: string): string {
  const text = event.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export interface BuildRef {
  number: number
  at: string
  url: string
}

export interface FlakyGroup {
  groupId: string
  name: string
  hardware: string
  queue: string
  trend: string
  alerting: boolean
  flips: number
  previousFlips: number
  flips30d: number
  runs30d: number
  hard30d: number
  soft30d: number
  passRate: number | null
  passRate30d: number | null
  lastFailure: BuildRef | null
}

export interface RetryGroup {
  groupId: string
  name: string
  hardware: string
  queue: string
  trend: string
  alerting: boolean
  recovered: number
  previousRecovered: number
  recovered30d: number
  runs30d: number
  lastRecovery: BuildRef | null
}

function buildRef(value: unknown): BuildRef | null {
  const row = value as Rec | null
  const number = buildNumber(row?.build_number)
  return number ? { number, at: str(row!.at), url: str(row!.url) } : null
}

const base = (row: Rec) => ({
  groupId: str(row.group_id),
  name: str(row.name),
  hardware: str(row.hardware),
  queue: str(row.queue),
  trend: str(row.trend) || 'steady',
  alerting: row.alerting === true,
})

/** Groups whose outcome flipped between pass and fail on main, alerting first (as published). */
export function flakyGroups(record: Rec | null | undefined): FlakyGroup[] {
  const rows: Rec[] = Array.isArray(record?.top_flaky) ? record!.top_flaky : []
  return rows.map((row) => ({
    ...base(row),
    flips: num(row.flips_window) ?? 0,
    previousFlips: num(row.flips_previous_window) ?? 0,
    flips30d: num(row.flips_30d) ?? 0,
    runs30d: num(row.runs_30d) ?? 0,
    hard30d: num(row.hard_30d) ?? 0,
    soft30d: num(row.soft_30d) ?? 0,
    passRate: num(row.pass_rate_window),
    passRate30d: num(row.pass_rate_30d),
    lastFailure: buildRef(row.last_failure),
  }))
}

/** Groups that passed only after an automatic retry of a hard failure, alerting first (as published). */
export function retryGroups(record: Rec | null | undefined): RetryGroup[] {
  const rows: Rec[] = Array.isArray(record?.retry_hotspots) ? record!.retry_hotspots : []
  return rows.map((row) => ({
    ...base(row),
    recovered: num(row.recovered_window) ?? 0,
    previousRecovered: num(row.recovered_previous_window) ?? 0,
    recovered30d: num(row.recovered_30d) ?? 0,
    runs30d: num(row.runs_30d) ?? 0,
    lastRecovery: buildRef(row.last_recovery),
  }))
}

export interface MainCohort {
  windowDays: number | null
  from: string
  to: string
  builds: number | null
  nightlies: number | null
  exhaustive: boolean
  /** Build rows the section publishes, when the byte budget dropped the oldest ones. */
  published: number | null
  omitted: number
}

export function mainCohort(record: Rec | null | undefined): MainCohort {
  const cohort: Rec = record?.cohort || {}
  const retention: Rec = record?.publication_retention?.builds || {}
  return {
    windowDays: num(cohort.window_days),
    from: str(cohort.observed_from),
    to: str(cohort.observed_to),
    builds: num(cohort.build_count),
    nightlies: num(cohort.canonical_nightly_build_count),
    exhaustive: cohort.exhaustive === true,
    published: num(retention.published),
    omitted: num(retention.omitted) ?? 0,
  }
}

/** The flakiness window and alert thresholds the watcher applies. */
export function flakinessThresholds(record: Rec | null | undefined): { windowDays: number; minFlips: number; minRecoveries: number } | null {
  const thresholds: Rec = record?.thresholds || {}
  const windowDays = num(thresholds.window_days)
  const minFlips = num(thresholds.min_outcome_flips)
  const minRecoveries = num(thresholds.min_failed_then_passed)
  return windowDays && minFlips && minRecoveries ? { windowDays, minFlips, minRecoveries } : null
}

/** ":amd: (MI355) Foo" and "AMD: :amd: (MI355) Foo" -> "(MI355) Foo". */
export function groupLabel(name: string): string {
  return name.replace(/^AMD:\s*/i, '').replace(/:amd:\s*/gi, '').trim() || name
}

export interface FailingGroup {
  groupId: string
  name: string
  hardware: string
  queue: string
  severity: 'hard' | 'soft'
  jobUrl: string
  /** Attempts of the group in this build; more than one means Buildkite retried it. */
  attempts: number
}

const isRetried = (observation: Rec) => observation.retry_evidence?.retried === true

/**
 * The AMD groups whose final attempt in one build failed, from the 30-day run ledger (the
 * `reliability` section). A retried attempt is superseded by its retry, so a group that failed
 * and then passed on retry is not failing. `retained` counts the groups the ledger still has
 * for this build: per-group history is bounded, so old builds may be incomplete.
 */
export function buildFailingGroups(reliability: Rec | null | undefined, number: number): { rows: FailingGroup[]; retained: number } {
  const catalog: Rec[] = Array.isArray(reliability?.group_catalog) ? reliability!.group_catalog : []
  const rows: FailingGroup[] = []
  let retained = 0
  for (const group of catalog) {
    const attempts = (Array.isArray(group.observations) ? group.observations : []).filter((row: Rec) => Number(row.build_number) === number)
    if (!attempts.length) continue
    retained += 1
    const finals = attempts.filter((row: Rec) => !isRetried(row))
    const last = (finals.length ? finals : attempts).reduce((a: Rec, b: Rec) => (str(b.observed_at) > str(a.observed_at) ? b : a))
    if (last.state !== 'hard' && last.state !== 'soft') continue
    rows.push({
      groupId: str(group.id),
      name: str(group.name),
      hardware: str(group.hardware),
      queue: str(last.queue),
      severity: last.state,
      jobUrl: str(last.job_url),
      attempts: attempts.length,
    })
  }
  rows.sort((a, b) => (a.severity === b.severity ? a.name.localeCompare(b.name) : a.severity === 'hard' ? -1 : 1))
  return { rows, retained }
}
