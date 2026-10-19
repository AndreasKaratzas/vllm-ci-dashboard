import { describe, expect, it } from 'vitest'
import { nightlyCounts, nightlySeries, passRateTrend } from '@/domain/home'

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

  it('does not present an older nightly as the latest pass rate', () => {
    expect(passRateTrend([build(3, 100, 0, 0, { has_test_results: false }), build(2, 100, 1, 0)])).toBeNull()
  })
})
