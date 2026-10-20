import { describe, expect, it } from 'vitest'
import { counted, rateText, reasonText } from '@/domain/format'
import { nightlyCounts, nightlySeries, passRateTrend } from '@/domain/home'
import { hasTestSignal, nightlyCountLabel, nightlyRunning, pendingNightly, signalNightly } from '@/domain/nightly'

const build = (number: number, total: number, hard: number, soft: number, extra = {}) => ({
  number,
  created_at: `2026-10-0${number % 9}T09:00:00Z`,
  total_groups: total,
  failed_groups: Array.from({ length: hard }, (_, i) => ({ name: `h${i}` })),
  soft_failed_groups: Array.from({ length: soft }, (_, i) => ({ name: `s${i}` })),
  ...extra,
})

describe('home KPIs', () => {
  it('counts passes as total minus hard and soft', () => {
    expect(nightlyCounts(build(5, 100, 3, 2))).toMatchObject({ passed: 95, hard: 3, soft: 2, passRate: 0.95 })
  })

  it('treats builds without test signal as absent', () => {
    expect(nightlyCounts(build(5, 100, 0, 0, { has_test_results: false }))).toBeNull()
    expect(nightlyCounts(build(5, 0, 0, 0))).toBeNull()
  })

  it('orders the series oldest first and skips no-signal builds', () => {
    const series = nightlySeries([build(3, 10, 1, 0), build(2, 10, 0, 0, { has_test_results: false }), build(1, 10, 2, 0)])
    expect(series.map((row) => row.number)).toEqual([1, 3])
  })

  it('reports the point change against the previous signal-bearing nightly', () => {
    const trend = passRateTrend([build(3, 100, 1, 0), build(2, 100, 0, 0, { has_test_results: false }), build(1, 100, 4, 0)])
    expect(trend?.latest.number).toBe(3)
    expect(trend?.deltaPoints).toBeCloseTo(3)
  })

  it('counts from the newest nightly with results while a newer one has none', () => {
    const trend = passRateTrend([build(3, 100, 0, 0, { has_test_results: false }), build(2, 100, 1, 0)])
    expect(trend?.latest.number).toBe(2)
    expect(trend?.deltaPoints).toBeNull()
  })
})

describe('the nightly every page counts from', () => {
  const running = build(4, 0, 2, 1, { has_test_results: false, state: 'failing' })
  const history = [running, build(3, 100, 1, 0, { has_test_results: false }), build(2, 100, 1, 0), build(1, 100, 0, 0)]

  it('is the newest nightly with test results', () => {
    expect([hasTestSignal(history[2]), hasTestSignal(history[1]), hasTestSignal(build(9, 0, 0, 0)), hasTestSignal(null)]).toEqual([true, false, false, false])
    expect(signalNightly(history)?.number).toBe(2)
    expect(signalNightly([running])).toBeUndefined()
    expect(signalNightly(null)).toBeUndefined()
  })

  it('names a newer nightly without results and the failures already in it', () => {
    expect(pendingNightly(history)).toMatchObject({ latest: { number: 4 }, signal: { number: 2 }, running: true, hard: 2, soft: 1 })
    expect(pendingNightly([build(5, 0, 0, 0, { has_test_results: false, state: 'failed' }), ...history])).toMatchObject({ running: false, hard: 0 })
    expect(pendingNightly(history.slice(2))).toBeNull()
    expect(pendingNightly([])).toBeNull()
    expect([nightlyRunning({ state: 'failing' }), nightlyRunning({ state: 'Running' }), nightlyRunning({ state: 'failed' }), nightlyRunning(null)]).toEqual([true, true, false, false])
  })

  it('labels a nightly count with the history it comes from', () => {
    expect(nightlyCountLabel(30, 'builds')).toBe('30 nightlies (build summaries)')
    expect(nightlyCountLabel(1, 'jobs')).toBe('1 nightly (per-job results)')
    expect(nightlyCountLabel(1500, 'builds')).toBe('1,500 nightlies (build summaries)')
  })
})

describe('shared formats', () => {
  it('formats rates, counts and published reasons', () => {
    expect([rateText(93.3333), rateText(100), rateText(null), rateText(Number.NaN)]).toEqual(['93.3%', '100.0%', '—', '—'])
    expect([counted(1, 'event'), counted(3, 'event'), counted(2, 'nightly', 'nightlies'), counted(1200, 'build')]).toEqual(['1 event', '3 events', '2 nightlies', '1,200 builds'])
    expect(reasonText('strict_all_main_reliability_unavailable')).toBe('Strict all main reliability unavailable')
  })
})
