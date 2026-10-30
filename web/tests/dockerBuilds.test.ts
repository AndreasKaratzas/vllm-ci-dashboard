import { describe, expect, it } from 'vitest'
import { jobDetail, jobFilters } from '@/components/analytics/docker/details'
import {
  BASE_STEP,
  DOCKER_STALE_MS,
  IMAGE_STEP,
  assertDockerBuilds,
  baseRebuilds,
  cacheClass,
  decodeJobs,
  dockerBuildsValid,
  dockerDaily,
  dockerFreshness,
  dockerJobUrl,
  dockerRange,
  filterJobs,
  percentile,
  stepStats,
} from '@/domain/dockerBuilds'
import { seconds, type Rec } from '@/domain/format'
import { readFixture } from './support/fixtures'

const payload = () => readFixture<Rec>('docker_builds_pages.json')
const DAY = 86_400_000

describe('docker builds payload', () => {
  it('accepts the published fixture and the not-collected seed', () => {
    expect(dockerBuildsValid(payload())).toBe(true)
    const seed = {
      ...payload(),
      coverage: { status: 'not_collected', refetch_hours: 10, pipelines: {} },
      jobs: { n: 0, id: [], build: [], pipeline: [], step: [], state: [], source: [], retried: [], created: [], wait_s: [], run_s: [], exit: [] },
    }
    expect(dockerBuildsValid(seed)).toBe(true)
  })

  it.each([
    ['a short column', (p: Rec) => p.jobs.run_s.pop()],
    ['an index past its dim', (p: Rec) => (p.jobs.step[0] = 3)],
    ['a job id that is not a uuid', (p: Rec) => (p.jobs.id[0] = 'https://buildkite.com/x')],
    ['an unknown coverage status', (p: Rec) => (p.coverage.status = 'partial')],
    ['another schema version', (p: Rec) => (p.schema_version = 2)],
    ['a negative run time', (p: Rec) => (p.jobs.run_s[2] = -1)],
    ['a retried flag of 2', (p: Rec) => (p.jobs.retried[0] = 2)],
    ['a negative build number', (p: Rec) => (p.jobs.build[0] = -1)],
    ['a fractional exit status', (p: Rec) => (p.jobs.exit[0] = 1.5)],
    ['a negative wait time', (p: Rec) => (p.jobs.wait_s[0] = -1)],
    ['a negative creation offset', (p: Rec) => (p.jobs.created[0] = -1)],
    ['window days as a string', (p: Rec) => (p.window.days = '30')],
  ])('rejects %s so the fetch is retried', (_, mutate) => {
    const bad = payload()
    mutate(bad)
    expect(dockerBuildsValid(bad)).toBe(false)
    expect(() => assertDockerBuilds(bad)).toThrow()
  })

  it('decodes columns into jobs newest first with absolute creation times and job links', () => {
    const jobs = decodeJobs(payload())
    expect(jobs).toHaveLength(14)
    expect(jobs[0]).toMatchObject({ step: IMAGE_STEP, state: 'running', source: 'pr', runS: null, createdMs: Date.parse('2026-10-29T11:30:00Z') })
    expect(jobs.every((job, i) => i === 0 || jobs[i - 1]!.createdMs >= job.createdMs)).toBe(true)
    const amd = jobs.find((job) => job.pipeline === 'amd-ci')!
    expect(dockerJobUrl(amd)).toBe(`https://buildkite.com/vllm/amd-ci/builds/512#${amd.id}`)
    expect(jobDetail(amd).sources).toEqual([{ label: 'Buildkite job', url: dockerJobUrl(amd) }])
  })
})

describe('docker builds model', () => {
  const data = payload()
  const all = decodeJobs(data)

  it('selects whole UTC days ending on the generation day, clipped to the published window', () => {
    const week = dockerRange(data, '7d')
    expect(week).toEqual({ startMs: Date.parse('2026-10-23T00:00:00Z'), endMs: Date.parse('2026-10-29T12:00:00Z'), days: 7 })
    expect(dockerRange(data, '30d').startMs).toBe(Date.parse('2026-09-30T00:00:00Z'))
  })

  it('filters by window, source scope and pipeline', () => {
    const range = dockerRange(data, '14d')
    expect(filterJobs(all, range, { scope: 'all', pipeline: 'all' })).toHaveLength(12)
    expect(filterJobs(all, dockerRange(data, '30d'), { scope: 'all', pipeline: 'all' })).toHaveLength(14)
    expect(filterJobs(all, range, { scope: 'main', pipeline: 'ci' }).map((job) => job.build)).toEqual([9009, 9008, 9008, 9006, 9002])
    expect(filterJobs(all, range, { scope: 'pr', pipeline: 'amd-ci' })).toEqual([])
  })

  it('times only finished first attempts of a step', () => {
    const image = stepStats(filterJobs(all, dockerRange(data, '14d'), { scope: 'all', pipeline: 'ci' }), IMAGE_STEP)
    // 600, 840, 960, 1200, 2100: the retried 9999 s attempt and the running job are excluded.
    expect(image).toMatchObject({ jobs: 7, runs: 5, retried: 1, failed: 1, p50: 960, p90: 2100, max: 2100, cache: null, rebuildShare: null })
  })

  it('counts a timed out run as failed', () => {
    const image = stepStats(filterJobs(all, dockerRange(data, '30d'), { scope: 'all', pipeline: 'ci' }), IMAGE_STEP)
    expect(all.filter((job) => job.state === 'timed_out').map((job) => job.build)).toEqual([9000])
    expect(image.failed).toBe(2)
  })

  it('classes base runs as reused, partial or full rebuilds', () => {
    expect(cacheClass({ step: BASE_STEP, runS: 599 })).toBe('reused')
    expect(cacheClass({ step: BASE_STEP, runS: 600 })).toBe('partial')
    expect(cacheClass({ step: 'ensure-ci-base-amd', runS: 7199 })).toBe('partial')
    expect(cacheClass({ step: BASE_STEP, runS: 7200 })).toBe('full')
    expect(cacheClass({ step: BASE_STEP, runS: null })).toBeNull()
    expect(cacheClass({ step: IMAGE_STEP, runS: 9000 })).toBeNull()
    const slice = filterJobs(all, dockerRange(data, '14d'), { scope: 'all', pipeline: 'all' })
    const base = stepStats(slice, BASE_STEP)
    expect(base.cache).toEqual({ reused: 3, partial: 1, full: 1 })
    expect(base.rebuildShare).toBeCloseTo(2 / 5)
    expect(base.fullP50).toBe(15120)
    const rebuilds = baseRebuilds(slice)
    expect(rebuilds.reused).toBe(3)
    expect(rebuilds.points.map((point) => [point.runS, point.cls])).toEqual([
      [1800, 'partial'],
      [15120, 'full'],
    ])
    expect(baseRebuilds(slice, 1)).toMatchObject({ total: 2, points: [{ runS: 1800 }] })
  })

  it('buckets each UTC day of the range, leaving days without a finished run null rather than zero', () => {
    const range = dockerRange(data, '7d')
    const days = dockerDaily(filterJobs(all, range, { scope: 'all', pipeline: 'ci' }), range, IMAGE_STEP)
    expect(days).toHaveLength(7)
    expect(days.map((day) => day.start)).toEqual(Array.from({ length: 7 }, (_, i) => range.startMs + i * DAY))
    const byDay = Object.fromEntries(days.map((day) => [new Date(day.start).toISOString().slice(5, 10), [day.runs, day.p50, day.p90]]))
    expect(byDay).toMatchObject({
      '10-25': [1, 960, 960],
      '10-26': [1, 2100, 2100],
      '10-27': [0, null, null],
      '10-28': [1, 600, 600],
      '10-29': [2, 840, 1200],
    })
  })

  it('uses nearest-rank percentiles', () => {
    expect(percentile([], 0.5)).toBeNull()
    expect(percentile([1, 2, 3, 4], 0.5)).toBe(2)
    expect(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9)).toBe(9)
    expect(percentile([7], 0.9)).toBe(7)
  })

  it('turns stale six hours after generation', () => {
    const generated = Date.parse(data.generated_at)
    expect(dockerFreshness(data, generated + DOCKER_STALE_MS).stale).toBe(false)
    expect(dockerFreshness(data, generated + DOCKER_STALE_MS + 1).stale).toBe(true)
  })
})

describe('seconds', () => {
  it.each([
    [null, '-'],
    ['x', '-'],
    [0, '0s'],
    [42, '42s'],
    [84, '1m 24s'],
    [840, '14m'],
    [3599.6, '1h'],
    [4.2 * 3600, '4h 12m'],
    [7200, '2h'],
    [27 * 3600, '1d 3h'],
  ])('formats %s as %s', (input, expected) => {
    expect(seconds(input)).toBe(expected)
  })
})

describe('docker jobs table', () => {
  it('keeps running jobs in the slow-run filter but not jobs that have not started', () => {
    const job = decodeJobs(payload())[0]!
    const run = jobFilters([job]).find((filter) => filter.id === 'run')!
    expect(run.predicate({ ...job, runS: null, state: 'running' }, '600')).toBe(true)
    expect(run.predicate({ ...job, runS: null, state: 'pending' }, '600')).toBe(false)
    expect(run.predicate({ ...job, runS: 599, state: 'passed' }, '600')).toBe(false)
    expect(run.predicate({ ...job, runS: 600, state: 'passed' }, '600')).toBe(true)
  })
})
