import { age, duration, reasonText, shortSha, type Rec, type Tone } from './format'

/**
 * AMD MI test-step slowdowns on main (`main_ci.slowdowns`), exactly as the duration watcher holds
 * them. One row is one Buildkite step on one queue; its minutes are its job minutes: the run time of
 * the step's jobs (its shards) summed per completed main build, so not wall time when it has shards.
 */

export type SlowdownStatus = 'active' | 'settled'
/**
 * `environment`: the last fast and first slow builds ran the same vLLM commit. `vllm_change`: vLLM
 * commits landed between them (test, source or CI-definition changes; the compare link shows which).
 */
export type SlowdownCause = 'vllm_change' | 'environment'

export interface BuildRef {
  number: number
  url: string
  commit: string
  createdAt: string
}

export interface SlowdownSample {
  build: number
  createdAt: string
  minutes: number
  /** At or after the onset build; the samples keep up to 20 runs of the level before it. */
  slow: boolean
}

export interface Slowdown {
  id: string
  name: string
  stepKey: string
  hardware: string
  queue: string
  /** Jobs (shards) per build in the compared layout. */
  jobs: number
  status: SlowdownStatus
  baselineMins: number
  baselineRuns: number
  recentMins: number
  recentRuns: number
  increaseMins: number
  increasePct: number
  noiseMins: number | null
  onset: BuildRef
  lastGood: BuildRef | null
  /** Start of the latest level increase; the slowdown settles 14 days after it. */
  lastRise: BuildRef | null
  latest: BuildRef | null
  cause: SlowdownCause
  compareUrl: string
  firstDetectedAt: string
  /** Oldest first, at most 60: the baseline's tail, then the runs since the onset thinned evenly. */
  samples: SlowdownSample[]
}

export interface SlowdownRules {
  minBaselineRuns: number
  minRecentRuns: number
  minLevelHours: number
  minSlowdownHours: number
  alertMinMins: number
  alertMinFraction: number
  alertLargeMins: number
  sigmas: number
  consistency: number
  resolveScale: number
  settleAfterDays: number
  staleAfterHours: number
}

export interface Slowdowns {
  available: boolean
  /** Why the block is unavailable, e.g. `duration_watcher_state_stale` when the watcher did not evaluate this cohort. */
  reason: string | null
  watcherLastRun: string | null
  issue: { number: number; url: string } | null
  /** The issue was closed by hand and stays closed until the set of slowdowns changes. */
  suppressed: boolean
  rules: SlowdownRules | null
  /** Largest increase first. */
  active: Slowdown[]
  settled: Slowdown[]
}

const num = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)
const str = (value: unknown): string => (typeof value === 'string' ? value : '')
const isRec = (value: unknown): value is Rec => !!value && typeof value === 'object' && !Array.isArray(value)

function buildRef(value: unknown): BuildRef | null {
  if (!isRec(value) || !(num(value.build_number)! > 0)) return null
  return { number: value.build_number, url: str(value.build_url), commit: str(value.commit), createdAt: str(value.created_at) }
}

function samples(value: unknown, onset: number): SlowdownSample[] {
  if (!Array.isArray(value)) return []
  const rows: SlowdownSample[] = []
  for (const entry of value) {
    if (!Array.isArray(entry)) continue
    const [build, createdAt, minutes] = entry
    if (!(num(build)! > 0) || num(minutes) === null) continue
    rows.push({ build, createdAt: str(createdAt), minutes, slow: build >= onset })
  }
  return rows
}

export function slowdown(row: Rec): Slowdown | null {
  const onset = buildRef(row.onset)
  const baselineMins = num(row.baseline_mins)
  const recentMins = num(row.recent_mins)
  if (!onset || baselineMins === null || recentMins === null || !str(row.signal_id)) return null
  return {
    id: row.signal_id,
    name: str(row.name) || 'unknown',
    stepKey: str(row.step_key),
    hardware: str(row.hardware) || 'unknown',
    queue: str(row.queue),
    jobs: num(row.jobs) ?? 1,
    status: row.status === 'settled' ? 'settled' : 'active',
    baselineMins,
    baselineRuns: num(row.baseline_runs) ?? 0,
    recentMins,
    recentRuns: num(row.recent_runs) ?? 0,
    increaseMins: num(row.increase_mins) ?? recentMins - baselineMins,
    increasePct: num(row.increase_pct) ?? ((recentMins - baselineMins) / baselineMins) * 100,
    noiseMins: num(row.noise_mins),
    onset,
    lastGood: buildRef(row.last_good),
    lastRise: buildRef(row.last_rise),
    latest: buildRef(row.latest),
    cause: row.likely_cause === 'environment' ? 'environment' : 'vllm_change',
    compareUrl: str(row.compare_url),
    firstDetectedAt: str(row.first_detected_at),
    samples: samples(row.samples, onset.number),
  }
}

function rules(value: unknown): SlowdownRules | null {
  if (!isRec(value)) return null
  const keys: [keyof SlowdownRules, string][] = [
    ['minBaselineRuns', 'min_baseline_runs'],
    ['minRecentRuns', 'min_recent_runs'],
    ['minLevelHours', 'min_level_hours'],
    ['minSlowdownHours', 'min_slowdown_hours'],
    ['alertMinMins', 'alert_min_mins'],
    ['alertMinFraction', 'alert_min_fraction'],
    ['alertLargeMins', 'alert_large_mins'],
    ['sigmas', 'sigmas'],
    ['consistency', 'consistency'],
    ['resolveScale', 'resolve_scale'],
    ['settleAfterDays', 'settle_after_days'],
    ['staleAfterHours', 'stale_after_hours'],
  ]
  const result = {} as SlowdownRules
  for (const [key, source] of keys) {
    const valueOf = num(value[source])
    if (valueOf === null) return null
    result[key] = valueOf
  }
  return result
}

/** Tenths of a minute below an hour: a 7.6m to 11.2m step must not read as 7.6m to 11m. */
export const minutes = (value: number): string => (value < 60 ? `${value.toFixed(1)}m` : duration(value))

const byIncrease = (a: Slowdown, b: Slowdown) => b.increaseMins - a.increaseMins || a.name.localeCompare(b.name)

/** The slowdowns block of a `main_ci` record (the section payload's `main_ci`). */
export function slowdowns(mainCi: Rec | null | undefined): Slowdowns {
  const block = isRec(mainCi?.slowdowns) ? mainCi!.slowdowns : null
  const available = block?.available === true
  const rows = available && Array.isArray(block!.rows) ? (block!.rows as unknown[]).filter(isRec) : []
  const parsed = rows.map(slowdown).filter((row): row is Slowdown => row !== null)
  const issue = isRec(block?.issue) && num(block!.issue.number)! > 0 ? { number: block!.issue.number, url: str(block!.issue.url) } : null
  return {
    available,
    reason: available ? null : str(block?.reason) || 'slowdowns_unpublished',
    watcherLastRun: str(block?.watcher_last_run) || null,
    issue,
    suppressed: block?.suppressed === true,
    rules: rules(block?.rules),
    active: parsed.filter((row) => row.status === 'active').sort(byIncrease),
    settled: parsed.filter((row) => row.status === 'settled').sort(byIncrease),
  }
}

const UNAVAILABLE: Record<string, string> = {
  duration_watcher_state_unavailable: 'The duration watcher has not published its state for this collection.',
  duration_watcher_state_stale: 'The duration watcher did not evaluate the builds of this collection.',
  slowdowns_unpublished: 'This snapshot does not publish slowdowns yet.',
}

/** Why the slowdowns block is unavailable, as a sentence. */
export function unavailableText(reason: string | null): string {
  return UNAVAILABLE[reason ?? ''] ?? `${reasonText(reason)}.`
}

/** Active slowdowns ask for attention; settled ones (no further increase for 14 days) are the new normal. */
export function slowdownTone(row: Slowdown): Tone {
  return row.status === 'active' ? 'soft' : 'neutral'
}

export function increaseLabel(row: Slowdown): string {
  return `+${minutes(row.increaseMins)} (+${Math.round(row.increasePct)}%)`
}

/** "12.3m (40 runs) → 15.6m (12 runs)". */
export function levelsLabel(row: Slowdown): string {
  return `${minutes(row.baselineMins)} (${row.baselineRuns} runs) → ${minutes(row.recentMins)} (${row.recentRuns} runs)`
}

/** "12.3m → 15.6m", the table form of levelsLabel. */
export function levelsText(row: Slowdown): string {
  return `${minutes(row.baselineMins)} → ${minutes(row.recentMins)}`
}

export function stepLabel(row: Slowdown): string {
  return row.jobs > 1 ? `${row.name} (${row.jobs} shards)` : row.name
}

export function causeLabel(row: Slowdown): string {
  return row.cause === 'environment'
    ? 'Same vLLM commit before and after: image, node, storage or driver change'
    : 'vLLM commits between the last fast and first slow build (tests, source or CI definition)'
}

/** The cause in a table cell; causeLabel says what it means. */
export function causeShort(row: Slowdown): string {
  return row.cause === 'environment' ? 'Environment' : 'vLLM commits'
}

/** "#94275 → #94303" (last fast build → first slow build). */
export function changeLabel(row: Slowdown): string {
  return row.lastGood ? `#${row.lastGood.number} → #${row.onset.number}` : `#${row.onset.number}`
}

/** The vLLM commits from the last fast build to the first slow one, e.g. "5c8952e1a2b3…c008d9d4e5f6"; one SHA when both ran it. */
export function commitRange(row: Slowdown): string | null {
  const bad = shortSha(row.onset.commit)
  const good = shortSha(row.lastGood?.commit)
  return bad ? (good && good !== bad ? `${good}…${bad}` : bad) : null
}

/** The Buildkite page of one sampled build, on the pipeline the onset build links to. */
export function sampleUrl(row: Slowdown, build: number): string {
  return /\/builds\/\d+/.test(row.onset.url) ? row.onset.url.replace(/\/builds\/\d+/, `/builds/${build}`) : ''
}

/** Job minutes the slowdowns add to a main build that runs every slowed step (steps run in parallel, so not wall time). */
export function addedMinutes(rows: Slowdown[]): number {
  return rows.reduce((sum, row) => sum + row.increaseMins, 0)
}

export function onsetAge(row: Slowdown, nowMs?: number): string {
  return age(row.onset.createdAt, nowMs)
}

export function slowdownSearchText(row: Slowdown): string {
  return [row.name, row.hardware, row.queue, row.stepKey, row.onset.commit, String(row.onset.number)].join(' ').toLowerCase()
}

/** One sentence per watcher rule, for the view's methodology popover. */
export function rulesText(value: SlowdownRules): string[] {
  const pct = (fraction: number) => `${Math.round(fraction * 100)}%`
  return [
    'One signal per Buildkite step and queue: its job minutes (the run time of its jobs, summed across shards) in each completed main build where all of them passed. Queue wait is excluded.',
    "Only builds with the step's current shard count are compared, so automatic shard re-layouts never count as slowdowns.",
    `A slowdown is a level at least ${minutes(value.alertMinMins)} and ${pct(value.alertMinFraction)} (or ${minutes(value.alertLargeMins)}) above the level before it and ${value.sigmas} standard errors, held by ${pct(value.consistency)} of its runs, without the step returning to the earlier level since.`,
    `It opens once the new level has ${value.minRecentRuns} runs over ${value.minLevelHours} hours and the slowdown is ${value.minSlowdownHours} hours old (the level before it needs ${value.minBaselineRuns} runs), so self-recovering infrastructure events stay out.`,
    `An open slowdown resolves when its increase falls below ${pct(value.resolveScale)} of those thresholds or the step has not run for ${value.staleAfterHours} hours; ${value.settleAfterDays} days after its latest increase it is listed as settled and leaves the issue.`,
  ]
}
