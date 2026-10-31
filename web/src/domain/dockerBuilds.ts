// CI Analytics "Docker builds": run time of the ROCm docker build jobs on Buildkite,
// decoded from the columnar docker_builds.json that scripts/vllm/collect_docker_builds.py publishes.
import type { Rec } from './format'

export const DOCKER_STALE_MS = 6 * 60 * 60 * 1000
const MINUTE_MS = 60 * 1000
export const HOUR_MS = 60 * MINUTE_MS
export const DAY_MS = 24 * HOUR_MS

export const IMAGE_STEP = 'image-build-amd'
export const BASE_STEP = 'refresh-rocm-base-amd'
const RETIRED_BASE_STEP = 'ensure-ci-base-amd'
const BASE_STEPS = new Set([BASE_STEP, RETIRED_BASE_STEP])
export const STEP_LABELS: Record<string, string> = {
  [BASE_STEP]: 'ROCm stack + runtime (base)',
  [IMAGE_STEP]: 'ROCm test image',
  [RETIRED_BASE_STEP]: 'ROCm ci_base (retired 2026-10-29)',
}
export const stepLabel = (step: string) => STEP_LABELS[step] ?? step
const STEP_SHORT: Record<string, string> = { [BASE_STEP]: 'ROCm base', [IMAGE_STEP]: 'Test image', [RETIRED_BASE_STEP]: 'ci_base (retired)' }
export const stepShort = (step: string) => STEP_SHORT[step] ?? step

/** A base-step run under 10 minutes reused the cached image; from 2 hours it rebuilt the ROCm stack. */
export const REUSED_MAX_S = 10 * 60
export const FULL_REBUILD_MIN_S = 120 * 60
export type CacheClass = 'reused' | 'partial' | 'full'
export const CACHE_LABELS: Record<CacheClass, string> = { reused: 'Reused', partial: 'Partial rebuild', full: 'Full rebuild' }

export const DOCKER_WINDOWS = [
  { id: '1h', label: 'Last hour', bucketMs: 5 * MINUTE_MS, buckets: 12, span: 'hour' },
  { id: '3h', label: 'Last 3 hours', bucketMs: 15 * MINUTE_MS, buckets: 12, span: '3 hours' },
  { id: '6h', label: 'Last 6 hours', bucketMs: 30 * MINUTE_MS, buckets: 12, span: '6 hours' },
  { id: '1d', label: 'Last 24 hours', bucketMs: HOUR_MS, buckets: 24, span: '24 hours' },
  { id: '3d', label: 'Last 3 days', bucketMs: 3 * HOUR_MS, buckets: 24, span: '3 days' },
  { id: '7d', label: 'Last 7 days', bucketMs: DAY_MS, buckets: 7, span: '7 days' },
  { id: '14d', label: 'Last 14 days', bucketMs: DAY_MS, buckets: 14, span: '14 days' },
  { id: '30d', label: 'Last 30 days', bucketMs: DAY_MS, buckets: 30, span: '30 days' },
] as const
export type DockerWindow = (typeof DOCKER_WINDOWS)[number]['id']
export const DOCKER_WINDOW_IDS: DockerWindow[] = DOCKER_WINDOWS.map((w) => w.id)
export const DOCKER_DEFAULT_WINDOW: DockerWindow = '1d'
export const DOCKER_SCOPES = ['all', 'main', 'pr'] as const
export type DockerScope = (typeof DOCKER_SCOPES)[number]
export const DOCKER_PIPELINES = ['ci', 'amd-ci', 'all'] as const
export type DockerPipeline = (typeof DOCKER_PIPELINES)[number]

const COLUMNS = ['n', 'id', 'build', 'pipeline', 'step', 'state', 'source', 'retried', 'created', 'wait_s', 'run_s', 'exit'] as const
const DIMS = ['pipeline', 'step', 'state', 'source'] as const
const UTC_SECOND_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/
const JOB_ID_RE = /^[0-9a-f-]{36}$/

const isPlainObject = (raw: unknown): raw is Rec => !!raw && typeof raw === 'object' && !Array.isArray(raw)
const isTimestamp = (raw: unknown) => typeof raw === 'string' && UTC_SECOND_RE.test(raw) && Number.isFinite(Date.parse(raw))
const isCount = (raw: unknown) => Number.isSafeInteger(raw) && (raw as number) >= 0
const isOptionalCount = (raw: unknown) => raw === null || isCount(raw)

function indexesValid(column: unknown[], size: number): boolean {
  return column.every((v) => Number.isInteger(v) && (v as number) >= 0 && (v as number) < size)
}

/** Structural check of a schema-1 payload: every column has n entries and every index names a dim. */
export function dockerBuildsValid(payload: unknown): boolean {
  if (!isPlainObject(payload) || payload.schema_version !== 1 || !isTimestamp(payload.generated_at)) return false
  const { window, coverage, dims, jobs } = payload
  if (!isPlainObject(window) || !isCount(window.days) || !isTimestamp(window.from) || !isTimestamp(window.to)) return false
  if (!isPlainObject(coverage) || !['complete', 'not_collected'].includes(coverage.status)) return false
  if (!isPlainObject(dims) || !DIMS.every((key) => Array.isArray(dims[key]) && dims[key].every((v: unknown) => typeof v === 'string'))) return false
  if (!isPlainObject(jobs) || !isCount(jobs.n)) return false
  const n = jobs.n as number
  if (!COLUMNS.slice(1).every((key) => Array.isArray(jobs[key]) && jobs[key].length === n)) return false
  return (
    jobs.id.every((v: unknown) => typeof v === 'string' && JOB_ID_RE.test(v)) &&
    jobs.build.every(isCount) &&
    DIMS.every((key) => indexesValid(jobs[key], dims[key].length)) &&
    jobs.retried.every((v: unknown) => v === 0 || v === 1) &&
    jobs.created.every(isCount) &&
    jobs.wait_s.every(isOptionalCount) &&
    jobs.run_s.every(isOptionalCount) &&
    jobs.exit.every((v: unknown) => v === null || Number.isSafeInteger(v))
  )
}

export function assertDockerBuilds(payload: unknown): void {
  if (!dockerBuildsValid(payload)) throw new Error('Docker builds payload is incomplete')
}

export interface DockerJob {
  id: string
  build: number
  pipeline: string
  step: string
  state: string
  source: string
  retried: boolean
  createdMs: number
  waitS: number | null
  runS: number | null
  exit: number | null
}

const decoded = new WeakMap<object, DockerJob[]>()

/** The jobs of a valid payload, newest first; decoded once per payload. */
export function decodeJobs(payload: Rec): DockerJob[] {
  const cached = decoded.get(payload)
  if (cached) return cached
  const { dims, jobs } = payload
  const fromMs = Date.parse(payload.window.from)
  const out: DockerJob[] = new Array(jobs.n)
  for (let i = 0; i < jobs.n; i++) {
    out[i] = {
      id: jobs.id[i],
      build: jobs.build[i],
      pipeline: dims.pipeline[jobs.pipeline[i]],
      step: dims.step[jobs.step[i]],
      state: dims.state[jobs.state[i]],
      source: dims.source[jobs.source[i]],
      retried: jobs.retried[i] === 1,
      createdMs: fromMs + jobs.created[i] * 1000,
      waitS: jobs.wait_s[i],
      runS: jobs.run_s[i],
      exit: jobs.exit[i],
    }
  }
  decoded.set(payload, out)
  return out
}

export function dockerJobUrl(job: Pick<DockerJob, 'pipeline' | 'build' | 'id'>): string {
  return `https://buildkite.com/vllm/${job.pipeline}/builds/${job.build}#${job.id}`
}

export function cacheClass(job: Pick<DockerJob, 'step' | 'runS'>): CacheClass | null {
  if (!BASE_STEPS.has(job.step) || job.runS === null) return null
  return job.runS < REUSED_MAX_S ? 'reused' : job.runS < FULL_REBUILD_MIN_S ? 'partial' : 'full'
}

/** A finished final attempt: the only runs that feed duration statistics. */
export const timed = (job: DockerJob): job is DockerJob & { runS: number } => !job.retried && job.runS !== null

export interface DockerFilter {
  window: DockerWindow
  scope: DockerScope
  pipeline: DockerPipeline
}

export interface DockerRange {
  /** Start of the first bucket, on a UTC clock boundary. */
  startMs: number
  /** Inclusive end: generated_at for the selected window, so the last bucket is usually partial. */
  endMs: number
  bucketMs: number
  buckets: number
}

/** The selected window: whole UTC buckets ending with the one that holds generated_at, clipped to the published window. */
export function dockerRange(payload: Rec, window: DockerWindow): DockerRange {
  const spec = DOCKER_WINDOWS.find((w) => w.id === window) ?? DOCKER_WINDOWS.find((w) => w.id === DOCKER_DEFAULT_WINDOW)!
  const { bucketMs } = spec
  const endMs = Date.parse(payload.generated_at)
  const last = Math.floor((endMs - 1) / bucketMs) * bucketMs
  const startMs = Math.max(last - (spec.buckets - 1) * bucketMs, Math.floor(Date.parse(payload.window.from) / bucketMs) * bucketMs)
  return { startMs, endMs, bucketMs, buckets: Math.round((last - startMs) / bucketMs) + 1 }
}

/** The equal-length period just before the range; null when it starts before the published window. */
export function previousRange(payload: Rec, range: DockerRange): DockerRange | null {
  const len = range.endMs - range.startMs
  if (range.startMs - len < Date.parse(payload.window.from)) return null
  return { startMs: range.startMs - len, endMs: range.startMs - 1, bucketMs: range.bucketMs, buckets: range.buckets }
}

const BUCKET_NOUNS: Record<number, string> = {
  [5 * MINUTE_MS]: '5 minutes',
  [15 * MINUTE_MS]: '15 minutes',
  [30 * MINUTE_MS]: '30 minutes',
  [HOUR_MS]: 'hour',
  [3 * HOUR_MS]: '3 hours',
  [DAY_MS]: 'day',
}
export const bucketNoun = (bucketMs: number) => BUCKET_NOUNS[bucketMs] ?? 'day'

export function filterJobs(jobs: DockerJob[], range: DockerRange, filter: Omit<DockerFilter, 'window'>): DockerJob[] {
  return jobs.filter(
    (job) =>
      job.createdMs >= range.startMs &&
      job.createdMs <= range.endMs &&
      (filter.scope === 'all' || job.source === filter.scope) &&
      (filter.pipeline === 'all' || job.pipeline === filter.pipeline),
  )
}

/** Nearest-rank percentile of ascending values; null when there are none. */
export function percentile(sorted: number[], p: number): number | null {
  if (!sorted.length) return null
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1))]!
}

const ascending = (values: number[]) => Array.from(Float64Array.from(values).sort())
const quantiles = (sorted: number[]) => ({
  min: sorted[0] ?? null,
  p25: percentile(sorted, 0.25),
  p50: percentile(sorted, 0.5),
  p75: percentile(sorted, 0.75),
  p90: percentile(sorted, 0.9),
  max: sorted.at(-1) ?? null,
})

/** Seconds a job has been running at asOfMs, from its creation plus its wait. */
export const elapsedS = (job: DockerJob, asOfMs: number) => (asOfMs - job.createdMs) / 1000 - (job.waitS ?? 0)

/** A base run still running at collection past the cache-hit limit: a known miss of unknown size. */
export function rebuildingAt(job: DockerJob, asOfMs: number): boolean {
  return (
    job.step === BASE_STEP &&
    !job.retried &&
    job.runS === null &&
    job.state === 'running' &&
    job.waitS !== null &&
    elapsedS(job, asOfMs) >= REUSED_MAX_S
  )
}

const unfinished = (job: DockerJob) => job.runS === null && (job.state === 'running' || job.state === 'pending')

export interface StepStats {
  step: string
  /** Every job of the step in the slice, retried attempts and unfinished ones included. */
  jobs: number
  /** Finished final attempts, the population of the percentiles. */
  runs: number
  retried: number
  failed: number
  min: number | null
  p25: number | null
  p50: number | null
  p75: number | null
  p90: number | null
  max: number | null
  cache: Record<CacheClass, number> | null
  fullP50: number | null
  /** Base runs still running past the cache-hit limit at collection, counted as misses. */
  rebuilding: number
  /** Unfinished jobs not counted in rebuilding. */
  pending: number
  /** Finished runs plus rebuilding ones: the population of the hit rate. */
  decided: number
  /** Reused over decided runs, 0..1; null for a step without a cache or decided runs. */
  hitRate: number | null
  /** Run time of the finished partial and full rebuilds. */
  rebuildS: number
}

export function stepStats(jobs: DockerJob[], step: string, asOfMs = -Infinity): StepStats {
  const ofStep = jobs.filter((job) => job.step === step)
  const runs = ofStep.filter(timed)
  const sorted = ascending(runs.map((job) => job.runS))
  const base = BASE_STEPS.has(step)
  const cache: Record<CacheClass, number> = { reused: 0, partial: 0, full: 0 }
  const full: number[] = []
  let rebuildS = 0
  if (base)
    for (const job of runs) {
      const cls = cacheClass(job)!
      cache[cls]++
      if (cls !== 'reused') rebuildS += job.runS
      if (cls === 'full') full.push(job.runS)
    }
  const rebuilding = ofStep.filter((job) => rebuildingAt(job, asOfMs)).length
  const decided = runs.length + rebuilding
  return {
    step,
    jobs: ofStep.length,
    runs: runs.length,
    retried: ofStep.filter((job) => job.retried).length,
    failed: ofStep.filter((job) => job.state === 'failed' || job.state === 'timed_out').length,
    ...quantiles(sorted),
    cache: base ? cache : null,
    fullP50: percentile(ascending(full), 0.5),
    rebuilding,
    pending: ofStep.filter(unfinished).length - rebuilding,
    decided,
    hitRate: base && decided ? cache.reused / decided : null,
    rebuildS,
  }
}

export interface Spread {
  n: number
  min: number | null
  p25: number | null
  p50: number | null
  p75: number | null
  p90: number | null
  max: number | null
  /** Every run time, ascending, only when there are too few runs for percentiles. */
  values: number[]
}

/** Run time spread of a step's finished final attempts, optionally only base runs that reused or rebuilt. */
export function runSpread(jobs: DockerJob[], step: string, cls?: 'reused' | 'rebuilt'): Spread {
  const sorted = ascending(
    jobs.filter((job) => job.step === step && timed(job) && (!cls || (cacheClass(job) === 'reused') === (cls === 'reused'))).map((job) => job.runS!),
  )
  return {
    n: sorted.length,
    ...quantiles(sorted),
    values: sorted.length < 5 ? sorted : [],
  }
}

export interface BucketPoint {
  start: number
  end: number
  runs: number
  /** Run time percentiles in seconds; null in a bucket without a finished run. */
  p50: number | null
  p90: number | null
  reused: number
  partial: number
  full: number
  rebuilding: number
  hitRate: number | null
  rebuildS: number
}

/** One point per bucket of the range for one step; counts are 0 and percentiles null in empty buckets. */
export function dockerBuckets(jobs: DockerJob[], range: DockerRange, step: string, asOfMs: number): BucketPoint[] {
  const slots = Array.from({ length: range.buckets }, () => [] as DockerJob[])
  for (const job of jobs) {
    if (job.step !== step || job.createdMs < range.startMs || job.createdMs > range.endMs) continue
    slots[Math.min(range.buckets - 1, Math.floor((job.createdMs - range.startMs) / range.bucketMs))]!.push(job)
  }
  return slots.map((slot, i) => {
    const start = range.startMs + i * range.bucketMs
    const stats = stepStats(slot, step, asOfMs)
    const cache = stats.cache ?? { reused: 0, partial: 0, full: 0 }
    return {
      start,
      end: Math.min(start + range.bucketMs, range.endMs),
      runs: stats.runs,
      p50: stats.p50,
      p90: stats.p90,
      ...cache,
      rebuilding: stats.rebuilding,
      hitRate: stats.hitRate,
      rebuildS: stats.rebuildS,
    }
  })
}

export const REBUILD_EPISODE_GAP_MS = 60 * MINUTE_MS

export interface RebuildEpisode {
  id: string
  firstMs: number
  lastMs: number
  /** The episode's rebuilds, newest first. */
  jobs: DockerJob[]
  partial: number
  full: number
  rebuilding: number
  /** Run time of the finished rebuilds. */
  rebuildS: number
  /** Elapsed run time of the rebuilds still running at collection. */
  runningS: number
  longestS: number | null
  sources: Record<string, number>
}

/** Base rebuilds chained into episodes: each joins the open one when it starts within gapMs of its latest rebuild. Newest first. */
export function rebuildEpisodes(jobs: DockerJob[], asOfMs: number, gapMs = REBUILD_EPISODE_GAP_MS): RebuildEpisode[] {
  const rebuilds = jobs
    .filter((job) => job.step === BASE_STEP && ((timed(job) && cacheClass(job) !== 'reused') || rebuildingAt(job, asOfMs)))
    .sort((a, b) => a.createdMs - b.createdMs)
  const episodes: RebuildEpisode[] = []
  let open: RebuildEpisode | undefined
  for (const job of rebuilds) {
    if (!open || job.createdMs - open.lastMs > gapMs) {
      open = { id: `docker-episode:${job.createdMs}`, firstMs: job.createdMs, lastMs: job.createdMs, jobs: [], partial: 0, full: 0, rebuilding: 0, rebuildS: 0, runningS: 0, longestS: null, sources: {} }
      episodes.push(open)
    }
    open.lastMs = job.createdMs
    open.jobs.unshift(job)
    open.sources[job.source] = (open.sources[job.source] ?? 0) + 1
    if (job.runS === null) {
      open.rebuilding++
      open.runningS += elapsedS(job, asOfMs)
    } else {
      open[cacheClass(job) as 'partial' | 'full']++
      open.rebuildS += job.runS
      open.longestS = Math.max(open.longestS ?? 0, job.runS)
    }
  }
  return episodes.reverse()
}

/** Fewest decided or finished runs in each period for a delta to be shown. */
export const MIN_COMPARE_RUNS = 20
export const compareCount = (cur: number | null, prev: number | null) => (cur === null || prev === null ? null : cur - prev)
export const comparePercent = (cur: number | null, prev: number | null) =>
  cur === null || prev === null || prev === 0 ? null : Math.round((cur / prev - 1) * 100)
export const comparePoints = (cur: number | null, prev: number | null) => (cur === null || prev === null ? null : Math.round((cur - prev) * 1000) / 10)

/** A hit rate as a whole percent, with one decimal from 99% so a near-perfect rate never reads as 100%. */
export function hitText(rate: number | null): string {
  if (rate === null) return '—'
  const p = rate * 100
  return p === 100 ? '100%' : p >= 99 ? `${Math.floor(p * 10) / 10}%` : `${Math.round(p)}%`
}

export interface DockerFreshness {
  generatedMs: number
  ageMs: number
  stale: boolean
}

export function dockerFreshness(payload: Rec, nowMs: number): DockerFreshness {
  const generatedMs = Date.parse(payload.generated_at)
  const ageMs = Math.max(0, nowMs - generatedMs)
  return { generatedMs, ageMs, stale: ageMs > DOCKER_STALE_MS }
}

export const dockerCollected = (payload: Rec) => payload.coverage?.status === 'complete'
