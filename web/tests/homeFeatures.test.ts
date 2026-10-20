import { describe, expect, it } from 'vitest'
import { currentSubView, subViewQuery } from '@/components/layout/navigation'
import { rateAxis } from '@/components/charts/chart'
import { attentionTone, nightlyResultTone, noSignalLabel, nightlySeries, type NightlyCounts } from '@/domain/home'
import { sourceFreshness } from '@/domain/freshness'
import { freshnessChip, freshnessDetail } from '@/features/freshness/freshness'
import { buildEvidenceDetail } from '@/features/nightly/buildEvidence'

const URL = 'https://buildkite.com/vllm/ci/builds/30005'
const job = (name: string, state: string, extra = {}) => ({
  name,
  state,
  queue: 'amd_mi355_1',
  job_url: `${URL}/steps/canvas?jid=${name}`,
  build_number: 30005,
  ...extra,
})
const build = {
  number: 30005,
  state: 'failed',
  created_at: '2026-10-08T09:00:00Z',
  url: URL,
  source_pipeline: 'ci',
  commit: 'a'.repeat(40),
  message: 'Fix the thing\n\nlong body',
  total_groups: 6,
  has_test_results: true,
  failed_groups: [job('Broken', 'failed'), job('Beta', 'failed')],
  soft_failed_groups: [],
  failure_movement: {
    policy_id: 'observed-failure-movement-v1',
    available: true,
    preceding_build_number: 30004,
    new: [job('Broken', 'failed')],
    recurring: [job('Beta', 'failed')],
    fixed: [job('Alpha', 'passed', { current_url: `${URL}/steps/canvas?jid=alpha` })],
  },
}

describe('build evidence detail', () => {
  it('summarizes the build and lists every movement bucket', () => {
    const detail = buildEvidenceDetail(build)
    expect(detail.title).toBe('AMD nightly #30005')
    expect(detail.subtitle).toMatch(/· failure movement against the previous nightly$/)
    expect(detail.fields?.find((f) => f.label === 'Message')?.value).toBe('Fix the thing')
    expect(detail.fields?.find((f) => f.label === 'Buildkite state')).toMatchObject({ value: 'Failed', tone: 'hard' })
    expect(detail.stats?.map((s) => [s.label, s.value])).toEqual([
      ['Job variants observed', '6'],
      ['New failures', '1'],
      ['Recurring failures', '1'],
      ['Fixed', '1'],
    ])
    expect(detail.stats?.[1]).toMatchObject({ tone: 'hard', toneLabel: 'New' })
    expect(detail.fields?.find((f) => f.label === 'Current failures')?.value).toBe('2 hard · 0 soft')
    expect(detail.sources).toEqual([{ label: 'Open Buildkite build', url: URL }])
    const table = detail.tables![0]!
    expect(table.rows.map((r: { lifecycle: string }) => r.lifecycle)).toEqual(['New failure', 'Recurring failure', 'Fixed'])
    const change = table.columns.find((c) => c.id === 'change')!
    expect(table.rows.map((r) => change.cell!(r).tone)).toEqual(['hard', 'hard', 'pass'])
    expect(table.columns[0]!.cell!(table.rows[0]).href).toBe(`${URL}/steps/canvas?jid=Broken`)
  })

  it('scopes to one severity with its current result and a custom title', () => {
    const detail = buildEvidenceDetail(build, { scope: 'hard', title: 'Hard-failed groups' })
    expect(detail.title).toBe('Hard-failed groups')
    expect(detail.subtitle).toMatch(/· Current hard failures evidence$/)
    expect(detail.stats?.map((s) => s.label)).toEqual(['Job variants observed', 'Hard failures', 'Soft failures'])
    expect(detail.tables![0]!.meta).toBe('2 job variants')
    expect(detail.tables![0]!.columns.map((c) => c.label)).toEqual(['Job variant', 'Current result', 'Queue'])
  })

  it('says when a scope has nothing, and explains a build without test signal', () => {
    expect(buildEvidenceDetail(build, { scope: 'soft' }).empty).toBe('No current soft failures are observed in this build.')
    const blocked = buildEvidenceDetail({ ...build, has_test_results: false, test_jobs_blocked: 4, failure_movement: undefined })
    expect(blocked.notes?.[0]).toMatchObject({ tone: 'hard' })
    expect(blocked.notes?.[0]?.text).toContain('4 test jobs were dependency-blocked')
    expect(blocked.stats?.map((s) => s.label)).toEqual(['Job variants observed'])
    expect(blocked.fields?.find((f) => f.label === 'Test signal')?.value).toBe('Unavailable')
  })
})

describe('home tones and domains', () => {
  it('reads a nightly outcome tone and the reason for a missing pass rate', () => {
    expect(nightlyResultTone(build)).toBe('hard')
    expect(nightlyResultTone({ ...build, failed_groups: [], soft_failed_groups: [job('s', 'soft')] })).toBe('soft')
    expect(nightlyResultTone({ ...build, failed_groups: [] })).toBe('pass')
    expect(nightlyResultTone({ has_test_results: false, test_jobs_blocked: 2 })).toBe('hard')
    expect(noSignalLabel({ has_test_results: false, test_jobs_blocked: 2 })).toBe('Infra blocked')
    expect(noSignalLabel({ has_test_results: false })).toBe('No test signal')
    expect(noSignalLabel(build)).toBeNull()
  })

  it('keeps small pass-rate changes visible and ranks attention severity', () => {
    const rates = (series: NightlyCounts[]) => series.map((row) => row.passRate * 100)
    expect(rateAxis(rates(nightlySeries([build])))).toEqual({ domain: [20, 100], ticks: [20, 40, 60, 80, 100] })
    expect(rateAxis(rates(nightlySeries([{ ...build, total_groups: 300, failed_groups: [job('a', 'failed')] }])))).toEqual({
      domain: [97, 100],
      ticks: [97, 98, 99, 100],
    })
    expect(rateAxis([])).toEqual({ domain: [0, 100], ticks: [0, 25, 50, 75, 100] })
    expect(attentionTone([{ severity: 'warning' }, { severity: 'critical' }])).toBe('hard')
    expect(attentionTone([{ severity: 'warning' }])).toBe('soft')
    expect(attentionTone([])).toBe('pass')
  })
})

describe('freshness presentation', () => {
  const NOW = Date.parse('2026-10-17T10:00:00Z')
  const ops = {
    sources: {
      analytics: { path: 'analytics.json', timestamp: '2026-10-17T09:00:00Z' },
      ci_health: { path: 'ci_health.json', timestamp: '2026-10-17T06:00:00Z' },
    },
  }

  it('labels a page chip with the oldest source and the worst state', () => {
    const rows = sourceFreshness(ops, null, NOW).filter((row) => ['analytics', 'ci_health'].includes(row.key))
    const chip = freshnessChip({ timestamp: '2026-10-17T06:00:00Z', ageMs: 4 * 3_600_000, state: 'stale', sources: rows, missing: [] }, NOW)
    expect(chip).toMatchObject({ text: 'Collected 4h ago', tone: 'hard' })
    expect(chip?.detail).toContain('CI health 4h ago')
    expect(freshnessChip(null, NOW)).toBeUndefined()
  })

  it('builds the Data freshness sheet with every configured source', () => {
    const rows = sourceFreshness(ops, null, NOW)
    const detail = freshnessDetail(rows, NOW)
    expect(detail.title).toBe('Data freshness')
    expect(detail.tables![0]!.rows).toHaveLength(rows.length)
    expect(detail.stats?.[0]).toMatchObject({ label: 'Core data', tone: 'hard', toneLabel: 'Stale' })
    const target = detail.tables![0]!.columns.find((c) => c.id === 'target')!
    const analytics = rows.find((row) => row.key === 'analytics')!
    expect(target.cell!(analytics).text).toBe('2h 30m / 3h')
    expect(detail.stats?.[1]).toMatchObject({ label: 'Queue', meta: '1 source' })
    expect(detail.stats?.[2]).toMatchObject({ label: 'Sources past target', tone: 'hard', toneLabel: 'Stale' })
  })
})

describe('navigation', () => {
  it('resolves the current sub-view with a default and keeps defaults out of the URL', () => {
    expect(currentSubView('health', { view: 'parity' })?.label).toBe('Upstream parity')
    expect(currentSubView('health', { view: 'retired' })?.label).toBe('Overview')
    expect(currentSubView('home', {})).toBeNull()
    expect(subViewQuery('analytics', 'groups')).toEqual({})
    expect(subViewQuery('analytics', 'dns')).toEqual({ view: 'dns' })
  })
})
