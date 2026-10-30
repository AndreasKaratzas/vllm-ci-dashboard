// CI Analytics "Docker builds": run time of the ROCm docker build jobs on Buildkite,
// decoded from the columnar docker_builds.json that scripts/vllm/collect_docker_builds.py publishes.
import type { Rec } from './format'

export const DOCKER_STALE_MS = 6 * 60 * 60 * 1000
const DAY_MS = 24 * 60 * 60 * 1000

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
  { id: '7d', label: 'Last 7 days', days: 7 },
  { id: '14d', label: 'Last 14 days', days: 14 },
  { id: '30d', label: 'Last 30 days', days: 30 },
] as const
export type DockerWindow = (typeof DOCKER_WINDOWS)[number]['id']
export const DOCKER_WINDOW_IDS: DockerWindow[] = DOCKER_WINDOWS.map((w) => w.id)
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
  /** UTC midnight of the first day shown. */
  startMs: number
  /** End of the published window (generated_at). */
  endMs: number
  days: number
}

/** The selected window: whole UTC days ending with the day the payload was generated, clipped to the published window. */
export function dockerRange(payload: Rec, window: DockerWindow): DockerRange {
  const days = DOCKER_WINDOWS.find((w) => w.id === window)?.days ?? 14
  const endMs = Date.parse(payload.generated_at)
  const lastDay = Math.floor(endMs / DAY_MS) * DAY_MS
  const startMs = Math.max(lastDay - (days - 1) * DAY_MS, Math.floor(Date.parse(payload.window.from) / DAY_MS) * DAY_MS)
  return { startMs, endMs, days: Math.round((lastDay - startMs) / DAY_MS) + 1 }
}

export function filterJobs(jobs: DockerJob[], range: DockerRange, filter: Omit<DockerFilter, 'window'>): DockerJob[] {
  return jobs.filter(
    (job) =>
      job.createdMs >= range.startMs &&
      (filter.scope === 'all' || job.source === filter.scope) &&
      (filter.pipeline === 'all' || job.pipeline === filter.pipeline),
  )
}

/** Nearest-rank percentile of ascending values; null when there are none. */
export function percentile(sorted: number[], p: number): number | null {
  if (!sorted.length) return null
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1))]!
}

const ascending = (values: number[]) => Float64Array.from(values).sort()

export interface StepStats {
  step: string
  /** Every job of the step in the slice, retried attempts and unfinished ones included. */
  jobs: number
  /** Finished final attempts, the population of the percentiles. */
  runs: number
  retried: number
  failed: number
  p50: number | null
  p90: number | null
  max: number | null
  cache: Record<CacheClass, number> | null
  /** Partial plus full rebuilds over classified runs, 0..1; null for a step without a cache or runs. */
  rebuildShare: number | null
  fullP50: number | null
}

export function stepStats(jobs: DockerJob[], step: string): StepStats {
  const ofStep = jobs.filter((job) => job.step === step)
  const runs = ofStep.filter(timed)
  const sorted = Array.from(ascending(runs.map((job) => job.runS)))
  const base = BASE_STEPS.has(step)
  const cache: Record<CacheClass, number> = { reused: 0, partial: 0, full: 0 }
  const full: number[] = []
  if (base)
    for (const job of runs) {
      const cls = cacheClass(job)!
      cache[cls]++
      if (cls === 'full') full.push(job.runS)
    }
  return {
    step,
    jobs: ofStep.length,
    runs: runs.length,
    retried: ofStep.filter((job) => job.retried).length,
    failed: ofStep.filter((job) => job.state === 'failed' || job.state === 'timed_out').length,
    p50: percentile(sorted, 0.5),
    p90: percentile(sorted, 0.9),
    max: sorted.at(-1) ?? null,
    cache: base ? cache : null,
    rebuildShare: base && runs.length ? (cache.partial + cache.full) / runs.length : null,
    fullP50: percentile(Array.from(ascending(full)), 0.5),
  }
}

export interface DailyPoint {
  start: number
  runs: number
  /** Run time percentiles in seconds; null on a day without a finished run. */
  p50: number | null
  p90: number | null
}

/** One point per UTC day of the range for one step, with null percentiles on days without runs. */
export function dockerDaily(jobs: DockerJob[], range: DockerRange, step: string): DailyPoint[] {
  const buckets: number[][] = Array.from({ length: range.days }, () => [])
  for (const job of jobs) {
    if (job.step !== step || !timed(job)) continue
    const index = Math.floor((job.createdMs - range.startMs) / DAY_MS)
    if (index >= 0 && index < range.days) buckets[index]!.push(job.runS)
  }
  return buckets.map((values, i) => {
    const sorted = Array.from(ascending(values))
    return { start: range.startMs + i * DAY_MS, runs: values.length, p50: percentile(sorted, 0.5), p90: percentile(sorted, 0.9) }
  })
}

export interface RebuildPoint {
  job: DockerJob
  runS: number
  cls: Exclude<CacheClass, 'reused'>
}

/** The base step's rebuilds, newest first and at most `limit`, and how many runs reused the cache. */
export function baseRebuilds(jobs: DockerJob[], limit = 400): { points: RebuildPoint[]; total: number; reused: number } {
  const points: RebuildPoint[] = []
  let reused = 0
  for (const job of jobs) {
    if (job.step !== BASE_STEP || !timed(job)) continue
    const cls = cacheClass(job)
    if (cls === 'reused') reused++
    else if (cls) points.push({ job, runS: job.runS, cls })
  }
  return { points: points.slice(0, limit), total: points.length, reused }
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
