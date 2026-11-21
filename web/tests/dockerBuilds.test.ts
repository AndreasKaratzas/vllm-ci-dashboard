import { describe, expect, it } from 'vitest'
import { jobDetail, jobFilters } from '@/components/analytics/docker/details'
import {
  BASE_STEP,
  DOCKER_STALE_MS,
  DOCKER_WINDOW_IDS,
  IMAGE_STEP,
  assertDockerBuilds,
  cacheClass,
  comparePercent,
  comparePoints,
  decodeJobs,
  dockerBuckets,
  dockerBuildsValid,
  dockerFreshness,
  dockerJobUrl,
  dockerRange,
  filterJobs,
  hitText,
  percentile,
  previousRange,
  rebuildEpisodes,
  runSpread,
  stepStats,
  type DockerJob,
  type DockerWindow,
} from '@/domain/dockerBuilds'
import { agentHours, seconds, type Rec } from '@/domain/format'
import { readFixture } from './support/fixtures'

const payload = () => readFixture<Rec>('docker_builds_pages.json')
const DAY = 86_400_000
const MIN = 60_000
const at = (iso: string) => Date.parse(iso)

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
  const asOf = Date.parse(data.generated_at)
  const job = (over: Partial<DockerJob>): DockerJob => ({ ...all[0]!, step: BASE_STEP, state: 'passed', source: 'pr', retried: false, waitS: 5, runS: 60, ...over })
  const range = (window: DockerWindow, generatedAt = data.generated_at) => dockerRange({ ...data, generated_at: generatedAt }, window)

  it('selects whole UTC buckets ending with the one that holds the generation time, clipped to the published window', () => {
    expect(DOCKER_WINDOW_IDS).toEqual(['1h', '3h', '6h', '1d', '3d', '7d', '14d', '30d'])
    expect(range('7d')).toEqual({ startMs: at('2026-10-23T00:00:00Z'), endMs: asOf, bucketMs: DAY, buckets: 7 })
    expect(range('30d').startMs).toBe(at('2026-09-30T00:00:00Z'))
    expect(range('1h')).toEqual({ startMs: at('2026-10-29T11:00:00Z'), endMs: asOf, bucketMs: 5 * MIN, buckets: 12 })
    expect(range('1d')).toMatchObject({ startMs: at('2026-10-28T12:00:00Z'), buckets: 24 })
    expect(range('3d')).toMatchObject({ startMs: at('2026-10-26T12:00:00Z'), bucketMs: 3 * 60 * MIN })
    const offBoundary = range('1h', '2026-10-29T12:07:30Z')
    expect(offBoundary).toMatchObject({ startMs: at('2026-10-29T11:10:00Z'), buckets: 12 })
    expect(offBoundary.startMs + 11 * offBoundary.bucketMs).toBe(at('2026-10-29T12:05:00Z'))
    // Generated exactly at midnight, the window closes the previous day instead of opening an empty one.
    expect(range('7d', '2026-10-29T00:00:00Z')).toEqual({ startMs: at('2026-10-22T00:00:00Z'), endMs: at('2026-10-29T00:00:00Z'), bucketMs: DAY, buckets: 7 })
    expect(range('2h' as DockerWindow)).toEqual(range('1d'))
  })

  it('compares with the equal-length period before the range while it lies inside the published window', () => {
    const week = range('7d')
    expect(previousRange(data, week)).toMatchObject({ startMs: at('2026-10-16T12:00:00Z'), endMs: at('2026-10-23T00:00:00Z') - 1 })
    expect(previousRange(data, range('30d'))).toBeNull()
    const edge = job({ createdMs: week.startMs })
    const filter = { scope: 'all', pipeline: 'all' } as const
    expect(filterJobs([edge], week, filter)).toEqual([edge])
    expect(filterJobs([edge], previousRange(data, week)!, filter)).toEqual([])
  })

  it('filters by window, source scope and pipeline', () => {
    const fortnight = range('14d')
    expect(filterJobs(all, fortnight, { scope: 'all', pipeline: 'all' })).toHaveLength(12)
    expect(filterJobs(all, range('30d'), { scope: 'all', pipeline: 'all' })).toHaveLength(14)
    expect(filterJobs(all, fortnight, { scope: 'main', pipeline: 'ci' }).map((job) => job.build)).toEqual([9009, 9008, 9008, 9006, 9002])
    expect(filterJobs(all, fortnight, { scope: 'pr', pipeline: 'amd-ci' })).toEqual([])
  })

  it('times only finished first attempts of a step', () => {
    const image = stepStats(filterJobs(all, range('14d'), { scope: 'all', pipeline: 'ci' }), IMAGE_STEP, asOf)
    // 600, 840, 960, 1200, 2100: the retried 9999 s attempt and the running job are excluded.
    expect(image).toMatchObject({ jobs: 7, runs: 5, retried: 1, failed: 1, min: 600, p25: 840, p50: 960, p75: 1200, p90: 2100, max: 2100, cache: null, hitRate: null })
    expect(image).toMatchObject({ rebuilding: 0, pending: 1 })
  })

  it('counts a timed out run as failed', () => {
    const image = stepStats(filterJobs(all, range('30d'), { scope: 'all', pipeline: 'ci' }), IMAGE_STEP)
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
    const base = stepStats(filterJobs(all, range('14d'), { scope: 'all', pipeline: 'all' }), BASE_STEP, asOf)
    // 84, 90, 100, 1800, 15120
    expect(base).toMatchObject({ cache: { reused: 3, partial: 1, full: 1 }, min: 84, p25: 90, p75: 1800, fullP50: 15120, rebuildS: 1800 + 15120 })
    expect(base).toMatchObject({ decided: 5, hitRate: 3 / 5, rebuilding: 0, pending: 0 })
  })

  it('counts a base run still running past ten minutes at collection as a miss and a younger one as not decided', () => {
    const slice = filterJobs(all, range('14d'), { scope: 'all', pipeline: 'all' })
    const running = (minutes: number, step = BASE_STEP) => job({ step, state: 'running', runS: null, createdMs: asOf - minutes * MIN })
    expect(stepStats([...slice, running(11)], BASE_STEP, asOf)).toMatchObject({ rebuilding: 1, pending: 0, decided: 6, hitRate: 3 / 6, rebuildS: 16920 })
    expect(stepStats([...slice, running(9)], BASE_STEP, asOf)).toMatchObject({ rebuilding: 0, pending: 1, decided: 5, hitRate: 3 / 5 })
    expect(stepStats([...slice, running(11)], BASE_STEP)).toMatchObject({ rebuilding: 0, pending: 1 })
    expect(stepStats([running(60, IMAGE_STEP)], IMAGE_STEP, asOf)).toMatchObject({ rebuilding: 0, pending: 1 })
  })

  it('spreads run times apart for cache hits and rebuilds, listing values while there are fewer than five', () => {
    const slice = filterJobs(all, range('14d'), { scope: 'all', pipeline: 'all' })
    expect(runSpread(slice, BASE_STEP, 'reused')).toMatchObject({ n: 3, min: 84, p50: 90, max: 100, values: [84, 90, 100] })
    expect(runSpread(slice, BASE_STEP, 'rebuilt')).toMatchObject({ n: 2, values: [1800, 15120] })
    expect(runSpread(slice, BASE_STEP)).toEqual({ n: 5, min: 84, p25: 90, p50: 100, p75: 1800, p90: 15120, max: 15120, values: [] })
    expect(runSpread([], IMAGE_STEP)).toEqual({ n: 0, min: null, p25: null, p50: null, p75: null, p90: null, max: null, values: [] })
  })

  it('buckets each UTC day of the range, leaving days without a finished run null rather than zero', () => {
    const week = range('7d')
    const days = dockerBuckets(filterJobs(all, week, { scope: 'all', pipeline: 'ci' }), week, IMAGE_STEP, asOf)
    expect(days).toHaveLength(7)
    expect(days.map((day) => day.start)).toEqual(Array.from({ length: 7 }, (_, i) => week.startMs + i * DAY))
    expect(days.at(-1)).toMatchObject({ start: at('2026-10-29T00:00:00Z'), end: asOf })
    const byDay = Object.fromEntries(days.map((day) => [new Date(day.start).toISOString().slice(5, 10), [day.runs, day.p50, day.p90]]))
    expect(byDay).toMatchObject({
      '10-25': [1, 960, 960],
      '10-26': [1, 2100, 2100],
      '10-27': [0, null, null],
      '10-28': [1, 600, 600],
      '10-29': [2, 840, 1200],
    })
  })

  it('buckets hours on clock boundaries and keeps the cache outcome of each bucket', () => {
    const day = range('1d')
    const jobs = [
      job({ createdMs: at('2026-10-29T10:59:59Z'), runS: 60 }),
      job({ createdMs: at('2026-10-29T11:00:00Z'), runS: 7200 }),
      job({ createdMs: asOf, runS: 900 }),
      job({ createdMs: at('2026-10-29T11:30:00Z'), state: 'running', runS: null }),
    ]
    const hours = dockerBuckets(jobs, day, BASE_STEP, asOf)
    expect(hours).toHaveLength(24)
    expect(hours[0]).toMatchObject({ runs: 0, reused: 0, rebuilding: 0, p50: null, hitRate: null, rebuildS: 0 })
    expect(hours[22]).toMatchObject({ start: at('2026-10-29T10:00:00Z'), end: at('2026-10-29T11:00:00Z'), runs: 1, reused: 1, hitRate: 1 })
    expect(hours[23]).toMatchObject({ end: asOf, runs: 2, reused: 0, partial: 1, full: 1, rebuilding: 1, hitRate: 0, rebuildS: 8100 })
  })

  it('chains rebuilds starting within the gap into episodes, newest first', () => {
    const a = at('2026-10-28T00:00:00Z')
    const rebuild = (minutes: number, over: Partial<DockerJob> = {}) => job({ createdMs: a + minutes * MIN, runS: 7200, ...over })
    const jobs = [
      rebuild(0),
      rebuild(50, { runS: 1800, source: 'main' }),
      rebuild(100),
      rebuild(159),
      rebuild(220),
      rebuild(230, { runS: 60 }),
      rebuild(240, { step: IMAGE_STEP }),
      rebuild(280, { state: 'running', runS: null, createdMs: asOf - 20 * MIN }),
    ]
    const episodes = rebuildEpisodes(jobs, asOf)
    expect(episodes.map((ep) => [new Date(ep.firstMs).toISOString(), ep.jobs.length])).toEqual([
      ['2026-10-29T11:40:00.000Z', 1],
      ['2026-10-28T03:40:00.000Z', 1],
      ['2026-10-28T00:00:00.000Z', 4],
    ])
    expect(episodes[0]).toMatchObject({ rebuilding: 1, rebuildS: 0, runningS: 20 * 60 - 5, longestS: null })
    expect(episodes[2]).toMatchObject({ id: `docker-episode:${a}`, lastMs: a + 159 * MIN, partial: 1, full: 3, rebuildS: 3 * 7200 + 1800, longestS: 7200, sources: { pr: 3, main: 1 } })
    expect(rebuildEpisodes([rebuild(0), rebuild(61)], asOf)).toHaveLength(2)
  })

  it('compares periods only when the change can be computed', () => {
    expect(comparePercent(12, 0)).toBeNull()
    expect(comparePercent(12, null)).toBeNull()
    expect(comparePercent(39, 100)).toBe(-61)
    expect(comparePoints(0.9374, 0.7812)).toBe(15.6)
    expect(comparePoints(null, 0.5)).toBeNull()
  })

  it('never rounds a near-perfect hit rate up to 100%', () => {
    expect([1, 0.9996, 0.991, 0.986, 0.333, null].map(hitText)).toEqual(['100%', '99.9%', '99.1%', '99%', '33%', '—'])
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

describe('agentHours', () => {
  it.each([
    [3540, '59m'],
    [51480, '14.3 h'],
    [359999, '100 h'],
    [1396800, '388 h'],
  ])('formats %s s as %s', (input, expected) => {
    expect(agentHours(input)).toBe(expected)
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
