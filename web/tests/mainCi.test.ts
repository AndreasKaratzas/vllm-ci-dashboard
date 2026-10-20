import { describe, expect, it } from 'vitest'
import { sentence } from '@/domain/format'
import {
  buildOutcome,
  buildsBetween,
  buildTone,
  dayRateChange,
  eventTone,
  failingGroups,
  flakinessThresholds,
  flakyGroups,
  groupLabel,
  isNewIncident,
  lastDayPoint,
  mainBuilds,
  mainCiSummary,
  mainCohort,
  mainIncidents,
  mainTimeline,
  passRate,
  rangeText,
  recentOutcomes,
  retryGroups,
  suspectRanges,
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

  it('reads the 24 hours before the latest 24 and the point change between them', () => {
    const summary = mainCiSummary({ ...shell, previous_24h: { ...shell.last_24h, pass_rate: 99.75 } })!
    expect(summary.previous24h).toMatchObject({ builds: 61, passRate: 99.75 })
    expect(dayRateChange(summary)).toBe(-6.4)
    expect(dayRateChange({ ...summary, previous24h: null })).toBeNull()
    expect(dayRateChange({ ...summary, last24h: { ...summary.last24h!, passRate: null } })).toBeNull()
  })

  it('opens the last 24 hours as a point ending at the collection', () => {
    const summary = mainCiSummary(shell)!
    const end = Date.parse('2026-10-20T10:12:19Z')
    expect(lastDayPoint(summary)).toMatchObject({ from: end - 86_400_000, to: end, builds: 61, hard: 369, passRate: 93.36 })
    expect(lastDayPoint({ ...summary, collectedAt: null })).toBeNull()
    expect(lastDayPoint({ ...summary, last24h: null })).toBeNull()
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

  it('tallies how the newest builds ended', () => {
    const rows = mainBuilds({ builds: [build(4, '4', amd(9, 1)), build(3, '3', amd(9, 0, 1)), build(2, '2'), build(1, '1', null)] })
    expect(recentOutcomes(rows, 3)).toEqual({ builds: 3, pass: 1, soft: 1, hard: 1, neutral: 0 })
    expect(recentOutcomes(rows, 30)).toEqual({ builds: 4, pass: 1, soft: 1, hard: 1, neutral: 1 })
  })

  it('names the failing groups of a build from the group catalog, or says the snapshot has none', () => {
    const record = {
      group_catalog: { g1: { name: ':amd: (MI355) Kernels', hardware: 'mi355', queue: 'amd_mi355_1' } },
      builds: [
        build(2, '2026-10-20T00:00:00Z', amd(9, 1, 1), {
          failing: [
            { group_id: 'g1', severity: 'hard', job_url: 'https://buildkite.com/vllm/ci/builds/2/steps/canvas?jid=j' },
            { group_id: 'g2', severity: 'soft' },
            { severity: 'hard' },
          ],
        }),
        build(1, '2026-10-19T00:00:00Z', amd(9, 1)),
      ],
    }
    const [latest, older] = mainBuilds(record)
    expect(failingGroups(latest!, record)).toEqual([
      { groupId: 'g1', severity: 'hard', jobUrl: 'https://buildkite.com/vllm/ci/builds/2/steps/canvas?jid=j', name: ':amd: (MI355) Kernels', hardware: 'mi355', queue: 'amd_mi355_1' },
      { groupId: 'g2', severity: 'soft', jobUrl: '', name: 'g2', hardware: '', queue: '' },
    ])
    expect(older!.failing).toBeNull()
    expect(failingGroups(older!, record)).toBeNull()
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

  it('starts six-hour blocks on round UTC hours, so only the oldest and newest are partial', () => {
    // 03:00 to 12:59 UTC: 03-05, 06-11, 12.
    const points = trendPoints({ series: { hourly: Array.from({ length: 10 }, (_, index) => hour(index + 3, 10)) } }, '7d')
    expect(points.map((point) => point.builds)).toEqual([3, 6, 1])
    expect(points.map((point) => new Date(point.from).toISOString().slice(11, 16))).toEqual(['03:00', '06:00', '12:00'])
    expect(points.map((point) => new Date(point.to).toISOString().slice(11, 16))).toEqual(['06:00', '12:00', '13:00'])
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

  it('dates an incident by its opening event, else by its first bad build, newest first', () => {
    const at = (hours: number) => new Date(Date.UTC(2026, 9, 20) + hours * 3_600_000).toISOString()
    const row = (group_id: string, bad: number) => ({ group_id, name: group_id, severity: 'hard', bad_build_number: bad })
    const incidents = mainIncidents({
      builds: [build(5, at(5)), build(7, at(7))],
      incidents: { available: true, active: [row('old', 5), row('new', 7), row('evented', 1)], pending_soft: [] },
      timeline: [
        { event: 'opened', group_id: 'evented', build_number: 1, at: at(9) },
        { event: 'opened', group_id: 'evented', build_number: 0, at: at(1) },
      ],
    })
    expect(incidents.active.map((incident) => [incident.groupId, incident.openedAt])).toEqual([
      ['evented', at(9)],
      ['new', at(7)],
      ['old', at(5)],
    ])
    const now = Date.parse(at(30))
    expect(isNewIncident(incidents.active[1]!, now)).toBe(true)
    expect(isNewIncident(incidents.active[2]!, now)).toBe(false)
    expect(isNewIncident({ openedAt: '' }, now)).toBe(false)
  })

  it('rolls up incidents that first failed at one commit, from the newest good commit among them', () => {
    const incident = (groupId: string, good: string, goodBuild: number, bad: string, openedAt: string) =>
      ({ ...mainIncidents(record).active[0]!, groupId, good: { commit: good, build: goodBuild }, bad: { commit: bad, build: 9 }, openedAt, compareUrl: `${good}...${bad}` })
    const ranges = suspectRanges([
      incident('a', 'g1', 5, 'b1', '2026-10-20T03:00:00Z'),
      incident('b', 'g0', 3, 'b1', '2026-10-20T01:00:00Z'),
      incident('c', 'g2', 5, 'b2', '2026-10-20T04:00:00Z'),
      incident('d', 'g3', 5, 'b3', '2026-10-20T05:00:00Z'),
      incident('e', 'g3', 5, 'b3', '2026-10-20T06:00:00Z'),
      incident('f', 'g3', 5, 'b3', '2026-10-20T07:00:00Z'),
      incident('g', '', 0, 'b4', ''),
      incident('h', '', 0, 'b4', ''),
    ])
    expect(ranges.map((range) => [range.bad.commit, range.good.commit, range.compareUrl, range.incidents.map((row) => row.groupId), range.openedAt])).toEqual([
      ['b3', 'g3', 'g3...b3', ['d', 'e', 'f'], '2026-10-20T05:00:00Z'],
      ['b1', 'g1', 'g1...b1', ['a', 'b'], '2026-10-20T01:00:00Z'],
    ])
  })

  it('explains each commit-range status', () => {
    expect(rangeText('candidate').short).toBe('Bisectable')
    expect(rangeText('missing_good').long).toContain('No passing build')
    expect(rangeText('same_commit').short).toBe('Same commit')
    expect(rangeText('some_new_status')).toEqual({ short: 'Some new status', long: 'Some new status' })
    expect(rangeText('').short).toBe('Unknown')
  })

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

  it('labels published event names as phrases', () => {
    expect([sentence('opened'), sentence('soft_confirmed'), sentence('')]).toEqual(['Opened', 'Soft confirmed', ''])
  })

  it('drops the AMD prefixes from group names', () => {
    expect(groupLabel('AMD: :amd: (MI355) DeepSeek')).toBe('(MI355) DeepSeek')
    expect(groupLabel(':amd: (MI300) LoRA')).toBe('(MI300) LoRA')
  })
})

describe('flaky and retry groups', () => {
  const record = {
    thresholds: { window_days: 7, min_outcome_flips: 3, min_failed_then_passed: 3, max_issue_rows: 50 },
    // One group in both lists: it alerts on recoveries, not on its single flip.
    top_flaky: [
      { group_id: 'g', name: 'Mixed', hardware: 'mi300', queue: 'q', trend: 'rising', alerting: false, flips_window: 1, flips_previous_window: 0, flips_30d: 2, runs_30d: 9, hard_30d: 1, soft_30d: 0, pass_rate_window: 83.3, pass_rate_30d: 88.9, last_failure: { build_number: 7, at: '2026-10-20T00:00:00Z', url: 'u7' } },
      { group_id: 'h', name: 'Bare' },
    ],
    retry_hotspots: [
      { group_id: 'g', name: 'Mixed', hardware: 'mi300', queue: 'q', alerting: true, recovered_window: 3, recovered_previous_window: 1, recovered_30d: 4, runs_30d: 9, last_recovery: { build_number: 8, at: '2026-10-20T01:00:00Z', url: 'u8' } },
    ],
  }

  it('keeps each list’s own alert', () => {
    const [mixed, bare] = flakyGroups(record)
    expect(mixed).toMatchObject({ alerting: false, flips: 1, previousFlips: 0, flips30d: 2, passRate: 83.3, trend: 'rising', lastFailure: { number: 7, url: 'u7' } })
    expect(bare).toMatchObject({ alerting: false, flips: 0, passRate: null, trend: 'steady', lastFailure: null })
    expect(retryGroups(record)).toEqual([
      { groupId: 'g', name: 'Mixed', hardware: 'mi300', queue: 'q', trend: 'steady', alerting: true, recovered: 3, previousRecovered: 1, recovered30d: 4, runs30d: 9, lastRecovery: { number: 8, at: '2026-10-20T01:00:00Z', url: 'u8' } },
    ])
    expect(flakyGroups(null)).toEqual([])
    expect(retryGroups({})).toEqual([])
  })

  it('reads the thresholds only when all three are set', () => {
    expect(flakinessThresholds(record)).toEqual({ windowDays: 7, minFlips: 3, minRecoveries: 3 })
    expect(flakinessThresholds({ thresholds: { window_days: 7, min_outcome_flips: 3 } })).toBeNull()
    expect(flakinessThresholds(null)).toBeNull()
  })
})

describe('the main cohort', () => {
  it('reads the cohort and how many build rows the byte budget dropped', () => {
    const record = {
      cohort: { window_days: 30, observed_from: 'a', observed_to: 'b', build_count: 1776, canonical_nightly_build_count: 29, exhaustive: true },
      publication_retention: { builds: { source: 1776, published: 1500, omitted: 276 } },
    }
    expect(mainCohort(record)).toEqual({ windowDays: 30, from: 'a', to: 'b', builds: 1776, nightlies: 29, exhaustive: true, published: 1500, omitted: 276 })
    expect(mainCohort(null)).toEqual({ windowDays: null, from: '', to: '', builds: null, nightlies: null, exhaustive: false, published: null, omitted: 0 })
  })
})
