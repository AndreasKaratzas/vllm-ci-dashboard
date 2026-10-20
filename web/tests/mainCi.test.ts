import { describe, expect, it } from 'vitest'
import {
  buildFailingGroups,
  buildOutcome,
  buildsBetween,
  buildTone,
  eventTone,
  groupLabel,
  mainBuilds,
  mainCiSummary,
  mainIncidents,
  mainTimeline,
  passRate,
  previousDayRate,
  trendPoints,
} from '@/domain/mainCi'

const amd = (passed: number, hard = 0, soft = 0, retried = 0) => ({ passed, soft, hard, unknown: 0, not_run: 0, retried })
const build = (number: number, created_at: string, counts: object | null = amd(10), extra = {}) => ({
  number,
  created_at,
  commit: 'c'.repeat(40),
  headline: `Build ${number}`,
  nightly: false,
  state: 'passed',
  amd: counts,
  url: `https://buildkite.com/vllm/ci/builds/${number}`,
  ...extra,
})
const hour = (index: number, passed: number, hard = 0, soft = 0, builds = 1) => ({
  t: new Date(Date.UTC(2026, 9, 1) + index * 3_600_000).toISOString().replace('.000', ''),
  builds,
  amd_builds: builds,
  green_builds: hard ? 0 : builds,
  passed,
  soft,
  hard,
  pass_rate: passed + soft + hard ? (passed / (passed + soft + hard)) * 100 : null,
})

describe('main CI summary', () => {
  const shell = {
    available: true,
    source_generated_at: '2026-10-20T10:12:19Z',
    builds: 1772,
    latest_build: build(96108, '2026-10-20T08:55:31Z', amd(11)),
    last_24h: { builds: 61, amd_builds: 61, green_builds: 35, passed: 5192, soft: 0, hard: 369, pass_rate: 93.36 },
    active_incidents: 46,
    hard_incidents: 46,
    alerting_flaky_groups: 12,
    alerting_retry_groups: 17,
  }

  it('reads the flat manifest headline before the section loads', () => {
    expect(mainCiSummary(shell)).toMatchObject({
      available: true,
      reason: null,
      collectedAt: '2026-10-20T10:12:19Z',
      buildCount: 1772,
      latest: { number: 96108, amd: { passed: 11, hard: 0, notRun: 0 } },
      last24h: { amdBuilds: 61, greenBuilds: 35, passRate: 93.36 },
      activeIncidents: 46,
      flakyGroups: null,
      alertingFlaky: 12,
    })
  })

  it('reads the section summary once loaded, keeping the shell keys out of the way', () => {
    const merged = { ...shell, builds: [build(2, '2026-10-20T09:00:00Z')], summary: { ...shell, builds: 2, flaky_groups: 252, pending_soft: 1 } }
    expect(mainCiSummary(merged)).toMatchObject({ buildCount: 2, flakyGroups: 252, pendingSoft: 1 })
  })

  it('names why a section is unavailable', () => {
    expect(mainCiSummary({ available: false, unavailable_reason: 'strict_all_main_reliability_unavailable' })).toMatchObject({
      available: false,
      reason: 'strict_all_main_reliability_unavailable',
      latest: null,
      last24h: null,
    })
    expect(mainCiSummary(null)).toBeNull()
  })
})

describe('main builds', () => {
  it('sorts newest first and rejects malformed AMD counts', () => {
    const rows = mainBuilds({
      builds: [build(1, '2026-10-19T00:00:00Z'), build(3, '2026-10-20T00:00:00Z', { passed: 1 }), build(2, '2026-10-19T12:00:00Z'), { number: 0 }],
    })
    expect(rows.map((row) => row.number)).toEqual([3, 2, 1])
    expect(rows[0]!.amd).toBeNull()
  })

  it('takes the worst AMD outcome as the build tone and says it plainly', () => {
    const tone = (counts: object | null) => buildTone(mainBuilds({ builds: [build(1, 'x', counts)] })[0]!)
    expect([tone(amd(10, 2, 1)), tone(amd(10, 0, 1)), tone(amd(10)), tone(amd(0)), tone(null)]).toEqual(['hard', 'soft', 'pass', 'neutral', 'neutral'])
    const outcome = (counts: object | null) => buildOutcome(mainBuilds({ builds: [build(1, 'x', counts)] })[0]!)
    expect([outcome(amd(10, 2, 1)), outcome(amd(10)), outcome(amd(0)), outcome(null)]).toEqual(['2 hard · 1 soft', 'Passed', 'No AMD result', 'No AMD result'])
  })

  it('computes the pass rate over verdicts only', () => {
    expect(passRate({ passed: 43, hard: 2, soft: 0 })).toBeCloseTo(95.56, 2)
    expect(passRate({ passed: 0, hard: 0, soft: 0 })).toBeNull()
  })

  it('selects the builds of one interval', () => {
    const rows = mainBuilds({ builds: [build(1, '2026-10-20T00:59:59Z'), build(2, '2026-10-20T01:00:00Z'), build(3, '2026-10-20T02:00:00Z')] })
    const from = Date.parse('2026-10-20T01:00:00Z')
    expect(buildsBetween(rows, from, from + 3_600_000).map((row) => row.number)).toEqual([2])
  })
})

describe('pass-rate trend', () => {
  const hourly = Array.from({ length: 168 }, (_, index) => hour(index, 10, index === 167 ? 10 : 0))
  const record = { series: { hourly, daily: [hour(0, 90, 10), { ...hour(24, 0), builds: 0, amd_builds: 0, pass_rate: null }] } }

  it('shows hours for 24h, six-hour blocks for 7d and days for 30d', () => {
    const day = trendPoints(record, '24h')
    expect(day).toHaveLength(24)
    expect(day.at(-1)).toMatchObject({ passed: 10, hard: 10, passRate: 50 })
    const week = trendPoints(record, '7d')
    expect(week).toHaveLength(28)
    expect(week.at(-1)).toMatchObject({ builds: 6, passed: 60, hard: 10, to: week.at(-1)!.from + 6 * 3_600_000 })
    expect(week.at(-1)!.passRate).toBeCloseTo((60 / 70) * 100)
    const month = trendPoints(record, '30d')
    expect(month.map((point) => point.passRate)).toEqual([90, null])
  })

  it('cuts blocks from the newest bucket so only the oldest can be partial', () => {
    const points = trendPoints({ series: { hourly: hourly.slice(0, 10) } }, '7d')
    expect(points.map((point) => point.builds)).toEqual([4, 6])
  })

  it('compares the latest 24 hours with the 24 before', () => {
    expect(previousDayRate({ series: { hourly: Array.from({ length: 48 }, (_, i) => hour(i, i < 24 ? 9 : 10, i < 24 ? 1 : 0)) } })).toBe(90)
    expect(previousDayRate({ series: { hourly: hourly.slice(0, 30) } })).toBeNull()
  })
})

describe('incidents and events', () => {
  const record = {
    incidents: {
      available: true,
      watcher_last_run: '2026-10-20T10:12:19Z',
      issue: { number: 737, url: 'https://github.com/AndreasKaratzas/vllm-ci-dashboard/issues/737' },
      suppressed: false,
      active: [
        {
          group_id: 'g1',
          name: ':amd: (MI355) Kernels',
          hardware: 'mi355',
          queue: 'amd_mi355_1',
          severity: 'hard',
          status: 'confirmed',
          latest_build_number: 96045,
          good_commit: 'a'.repeat(40),
          good_build_number: 96039,
          bad_commit: 'b'.repeat(40),
          bad_build_number: 96045,
          compare_url: 'https://github.com/vllm-project/vllm/compare/a...b',
        },
      ],
      pending_soft: [{ group_id: 'g2', name: 'Soft one', severity: 'soft', status: 'pending' }],
    },
    timeline: [
      { event: 'resolved', group_id: 'g3', severity: 'hard', build_number: 96108 },
      { event: 'opened', group_id: 'g2', severity: 'soft', build_number: 'x' },
    ],
  }

  it('reads incidents with their good and bad builds and the managed issue', () => {
    const incidents = mainIncidents(record)
    expect(incidents).toMatchObject({ available: true, issue: { number: 737 }, suppressed: false })
    expect(incidents.active[0]).toMatchObject({ severity: 'hard', pending: false, good: { build: 96039 }, bad: { build: 96045 }, latestBuild: 96045 })
    expect(incidents.pending[0]).toMatchObject({ severity: 'soft', pending: true, good: { commit: '', build: null } })
    expect(mainIncidents({}).available).toBe(false)
  })

  it('tones resolutions as passes and openings by severity', () => {
    const [resolved, opened] = mainTimeline(record)
    expect([eventTone(resolved!), eventTone(opened!)]).toEqual(['pass', 'soft'])
    expect(opened!.build).toBeNull()
  })

  it('drops the AMD prefixes from group names', () => {
    expect(groupLabel('AMD: :amd: (MI355) DeepSeek')).toBe('(MI355) DeepSeek')
    expect(groupLabel(':amd: (MI300) LoRA')).toBe('(MI300) LoRA')
  })
})

describe('failing groups of a build', () => {
  const observation = (build_number: number, state: string, observed_at: string, extra = {}) => ({ build_number, state, observed_at, queue: 'amd_mi355_1', job_url: `job-${observed_at}`, ...extra })
  const reliability = {
    group_catalog: [
      // Failed, retried, failed again: failing with two attempts.
      { id: 'a', name: 'B group', hardware: 'mi355', observations: [observation(7, 'hard', '2', {}), observation(7, 'hard', '1', { retry_evidence: { retried: true } })] },
      // Failed, then passed on retry: not failing.
      { id: 'b', name: 'Recovered', hardware: 'mi300', observations: [observation(7, 'passed', '2'), observation(7, 'hard', '1', { retry_evidence: { retried: true } })] },
      { id: 'c', name: 'A soft', hardware: 'mi250', observations: [observation(7, 'soft', '1')] },
      { id: 'd', name: 'Other build', hardware: 'mi250', observations: [observation(6, 'hard', '1')] },
    ],
  }

  it('keeps the final attempt per group, hard first', () => {
    const { rows, retained } = buildFailingGroups(reliability, 7)
    expect(retained).toBe(3)
    expect(rows.map((row) => [row.groupId, row.severity, row.attempts, row.jobUrl])).toEqual([
      ['a', 'hard', 2, 'job-2'],
      ['c', 'soft', 1, 'job-1'],
    ])
    expect(buildFailingGroups(null, 7)).toEqual({ rows: [], retained: 0 })
  })
})
