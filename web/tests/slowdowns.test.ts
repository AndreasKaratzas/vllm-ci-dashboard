import { describe, expect, it } from 'vitest'
import {
  causeLabel,
  changeLabel,
  increaseLabel,
  levelsLabel,
  onsetAge,
  rulesText,
  slowdown,
  slowdowns,
  slowdownSearchText,
  slowdownTone,
  stepLabel,
} from '@/domain/slowdowns'
import fixture from './fixtures/main_ci_slowdowns.json'

const rules = {
  min_segment_runs: 5,
  min_baseline_runs: 8,
  min_recent_runs: 8,
  min_level_hours: 24,
  min_slowdown_hours: 48,
  segment_min_mins: 0.5,
  segment_min_fraction: 0.03,
  sigmas: 5,
  noise_floor_fraction: 0.03,
  alert_min_mins: 2,
  alert_min_fraction: 0.2,
  alert_large_mins: 15,
  consistency: 0.8,
  resolve_scale: 0.5,
  settle_after_days: 14,
  stale_after_hours: 72,
}

describe('slowdowns', () => {
  const parsed = slowdowns(fixture.main_ci)

  it('splits active from settled, largest increase first', () => {
    expect(parsed.available).toBe(true)
    expect(parsed.issue).toEqual({ number: 553, url: 'https://github.com/AndreasKaratzas/vllm-ci-dashboard/issues/553' })
    expect(parsed.active.map((row) => [row.name, row.increaseMins])).toEqual([
      [':amd: (MI300) V1 Attention', 15.8],
      [':amd: (MI300) Distributed Compile', 3.4],
    ])
    expect(parsed.settled.map((row) => row.name)).toEqual([':amd: (MI355) LM Eval Qwen3.5 Models'])
    expect(parsed.rules).toMatchObject({ alertMinMins: 2, alertMinFraction: 0.2, alertLargeMins: 15, minSlowdownHours: 48, settleAfterDays: 14 })
  })

  it('carries the onset range, the latest increase and samples on both sides of the onset', () => {
    const [family, row] = parsed.active
    expect(changeLabel(row!)).toBe('#94275 → #94303')
    expect(row!.lastGood!.commit.startsWith('8055293353')).toBe(true)
    expect(row!.compareUrl).toBe(`https://github.com/vllm-project/vllm/compare/${row!.lastGood!.commit}...${row!.onset.commit}`)
    expect(row!.lastRise!.number).toBe(94303)
    expect(family!.lastRise!.number).toBe(94775)
    expect(row!.samples).toHaveLength(12)
    expect(row!.samples[0]).toEqual({ build: 94223, createdAt: '2026-10-12T14:03:00.458000Z', minutes: 7.5, slow: false })
    expect(row!.samples.slice(5, 7).map((sample) => [sample.build, sample.minutes, sample.slow])).toEqual([
      [94275, 7.9, false],
      [94303, 12.5, true],
    ])
  })

  it('labels levels, increase, shards, cause and tone', () => {
    const [family, single] = parsed.active
    expect(stepLabel(family!)).toBe(':amd: (MI300) V1 Attention (2 shards)')
    expect(stepLabel(single!)).toBe(':amd: (MI300) Distributed Compile')
    expect(increaseLabel(single!)).toBe('+3.4m (+45%)')
    expect(levelsLabel(single!)).toBe('7.6m (43 runs) → 11.0m (30 runs)')
    expect(single!.cause).toBe('vllm_change')
    expect(causeLabel(single!)).toMatch(/tests, source or CI definition/)
    expect(slowdownTone(single!)).toBe('soft')
    expect(slowdownTone(parsed.settled[0]!)).toBe('neutral')
    expect(onsetAge(single!, Date.parse('2026-10-20T20:00:00Z'))).toBe('8d ago')
    expect(slowdownSearchText(single!)).toContain('amd-distributed-compile-unit-tests-2xh100')
  })

  it('marks environment changes and samples before the onset', () => {
    const row = slowdown({
      signal_id: 'env',
      name: 'Env',
      baseline_mins: 10,
      recent_mins: 13,
      likely_cause: 'environment',
      onset: { build_number: 12, commit: 'a' },
      last_good: { build_number: 11, commit: 'a' },
      samples: [
        [11, '2026-10-01T00:00:00Z', 10],
        [12, '2026-10-01T02:00:00Z', 13],
        ['bad', '', 1],
      ],
    })!
    expect(row.cause).toBe('environment')
    expect(causeLabel(row)).toMatch(/Same vLLM commit/)
    expect(row.increasePct).toBeCloseTo(30)
    expect(row.samples.map((sample) => sample.slow)).toEqual([false, true])
  })

  it('reports an unpublished or malformed block as unavailable', () => {
    expect(slowdowns(null)).toMatchObject({ available: false, reason: 'slowdowns_unpublished', active: [], settled: [] })
    expect(slowdowns({ slowdowns: { available: false, reason: 'duration_watcher_state_stale', rows: [] } }).reason).toBe(
      'duration_watcher_state_stale',
    )
    expect(slowdown({ signal_id: 'x', baseline_mins: 1, recent_mins: 2 })).toBeNull()
    expect(slowdowns({ slowdowns: { available: true, rows: [], rules: { ...rules, consistency: 'x' } } }).rules).toBeNull()
  })

  it('states the rules in words', () => {
    const text = rulesText(slowdowns({ slowdowns: { available: true, rows: [], rules } }).rules!)
    expect(text).toHaveLength(5)
    expect(text[2]).toContain('at least 2.0m and 20% (or 15.0m) above')
    expect(text[3]).toContain('the slowdown is 48 hours old')
    expect(text[4]).toContain('14 days after its latest increase it is listed as settled')
  })
})
