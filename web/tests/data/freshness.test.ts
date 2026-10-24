import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { areaSources, coreFreshness, freshnessOf, freshnessRows, queueFreshness, sourceFreshness, type SourceFreshness } from '@/domain/freshness'
import { freshnessChip } from '@/features/freshness/freshness'

const CONFIG = JSON.parse(readFileSync(resolve(__dirname, '../../../config/data_freshness_targets.json'), 'utf8'))
const MANIFEST = JSON.parse(readFileSync(resolve(__dirname, '../fixtures/operations_v2_manifest.json'), 'utf8'))

const NOW = Date.parse('2026-10-17T10:00:00Z')
const minutesAgo = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString()

/** A shell where every configured collector source was collected `minutes` ago. */
function opsAt(minutes: number, overrides: Record<string, number | null> = {}) {
  const sources: Record<string, object> = {}
  for (const key of Object.keys(CONFIG.sources)) {
    if (key === 'perf_eval') continue
    const age = key in overrides ? overrides[key] : minutes
    sources[key] = age === null ? { path: `${key}.json` } : { path: `${key}.json`, timestamp: minutesAgo(age!), timestamp_source: 'payload' }
  }
  return { sources }
}

const byKey = (rows: ReturnType<typeof sourceFreshness>) => Object.fromEntries(rows.map((row) => [row.key, row]))

describe('source freshness', () => {
  it('covers exactly the configured sources, labelled from the config', () => {
    const rows = sourceFreshness(opsAt(5), null, NOW)
    expect(rows.map((row) => row.key)).toEqual(Object.keys(CONFIG.sources))
    expect(byKey(rows).analytics!.label).toBe(CONFIG.sources.analytics.label)
  })

  it('skips a main-derived clock that a manifest written before it does not list', () => {
    const rows = sourceFreshness(MANIFEST.shell, null, NOW)
    expect(rows.map((row) => row.key)).toEqual(Object.keys(CONFIG.sources).filter((key) => !CONFIG.core.main_derived_sources.includes(key)))
  })

  it('applies the core warn and stale thresholds at their boundaries', () => {
    const core = CONFIG.core
    const state = (minutes: number) => byKey(sourceFreshness(opsAt(minutes), null, NOW)).analytics!.state
    expect(state(core.warn_minutes)).toBe('fresh')
    expect(state(core.warn_minutes + 1)).toBe('warn')
    expect(state(core.stale_minutes)).toBe('warn')
    expect(state(core.stale_minutes + 1)).toBe('stale')
  })

  it('applies queue thresholds to the queue and per-source limits elsewhere', () => {
    const rows = byKey(sourceFreshness(opsAt(CONFIG.queue.warn_minutes + 1, { project_items: 35 * 60, config_parity: 6 * 60 + 1 }), null, NOW))
    expect(rows.queue_timeseries!.state).toBe('warn')
    expect(rows.project_items!.state).toBe('fresh')
    expect(rows.project_items!.staleMinutes).toBe(CONFIG.sources.project_items.collector_limit_minutes)
    expect(rows.config_parity!.state).toBe('stale')
    expect(rows.config_parity!.staleMinutes).toBe(CONFIG.default_collector_limit_minutes)
  })

  it('reads perf_eval from perf_eval.json unless the shell lists it', () => {
    const perf = { generated_at: minutesAgo(20) }
    expect(byKey(sourceFreshness(opsAt(1), perf, NOW)).perf_eval!.ageMs).toBe(20 * 60_000)
    const listed = { sources: { ...opsAt(1).sources, perf_eval: { timestamp: minutesAgo(3) } } }
    expect(byKey(sourceFreshness(listed, perf, NOW)).perf_eval!.ageMs).toBe(3 * 60_000)
    expect(byKey(sourceFreshness(opsAt(1), null, NOW)).perf_eval).toMatchObject({ timestamp: null, ageMs: null, state: 'stale' })
  })

  it('treats a missing or invalid timestamp as stale and clamps future timestamps to zero age', () => {
    const rows = byKey(sourceFreshness({ sources: { analytics: { timestamp: 'soon' }, ci_health: { timestamp: minutesAgo(-10) } } }, null, NOW))
    expect(rows.analytics).toMatchObject({ timestamp: null, state: 'stale' })
    expect(rows.ci_health).toMatchObject({ ageMs: 0, state: 'fresh' })
    expect(rows.workload_mapping).toMatchObject({ timestamp: null, state: 'stale', path: null })
  })
})

describe('group freshness', () => {
  it('reports core data as old as its oldest core source', () => {
    const rows = sourceFreshness(opsAt(10, { workload_mapping: 160 }), { generated_at: minutesAgo(30) }, NOW)
    expect(coreFreshness(rows)).toMatchObject({ timestamp: minutesAgo(160), ageMs: 160 * 60_000, state: 'warn' })
    expect(coreFreshness(rows)!.sources.map((row) => row.key).sort()).toEqual([...CONFIG.core.sources, ...CONFIG.core.main_derived_sources].sort())
  })

  it('holds the all-main cohort clock to the core targets, so a stuck cohort makes core data stale', () => {
    const rows = sourceFreshness(opsAt(10, { main_ci: CONFIG.core.stale_minutes + 1 }), { generated_at: minutesAgo(10) }, NOW)
    expect(byKey(rows).main_ci).toMatchObject({ warnMinutes: CONFIG.core.warn_minutes, staleMinutes: CONFIG.core.stale_minutes, state: 'stale' })
    expect(coreFreshness(rows)).toMatchObject({ ageMs: (CONFIG.core.stale_minutes + 1) * 60_000, state: 'stale' })
  })

  it('keeps the age of the dated core sources and names the missing one as stale', () => {
    const rows = sourceFreshness(opsAt(10, { analytics: 30 }), null, NOW)
    const core = coreFreshness(rows)!
    expect(core).toMatchObject({ timestamp: minutesAgo(30), ageMs: 30 * 60_000, state: 'stale' })
    expect(core.missing.map((row) => row.key)).toEqual(['perf_eval'])
    expect(coreFreshness(sourceFreshness({ sources: {} }, null, NOW))).toMatchObject({ timestamp: null, ageMs: null, state: 'stale' })
  })

  it('carries why a producer published a source without a timestamp', () => {
    const ops = { sources: { ...opsAt(10).sources, main_ci: { path: 'analytics.json', timestamp: null, unavailable_reason: 'strict_all_main_reliability_unavailable' } } }
    const rows = sourceFreshness(ops, { generated_at: minutesAgo(10) }, NOW)
    expect(byKey(rows).main_ci).toMatchObject({ timestamp: null, state: 'stale', unavailableReason: 'strict_all_main_reliability_unavailable' })
    const core = coreFreshness(rows)!
    expect(core).toMatchObject({ ageMs: 10 * 60_000, state: 'stale' })
    const chip = freshnessChip(core, NOW, 'Core data')!
    expect(chip.text).toBe('Core data 10m ago · 1 missing')
    expect(chip.detail).toContain('Main CI builds unavailable: strict all main reliability unavailable')
  })

  it('summarizes the queue and each area, leaving upstream activity informational', () => {
    const rows = sourceFreshness(opsAt(10, { queue_timeseries: 45, amd_test_signal: 5000 }), { generated_at: minutesAgo(10) }, NOW)
    expect(queueFreshness(rows)).toMatchObject({ ageMs: 45 * 60_000, state: 'warn' })
    const health = freshnessOf(rows, areaSources('health'))!
    expect(health.sources.map((row) => row.key)).not.toContain('amd_test_signal')
    expect(health).toMatchObject({ ageMs: 10 * 60_000, state: 'fresh' })
    expect(byKey(rows).amd_test_signal).toMatchObject({ informational: true, state: 'stale' })
    expect(areaSources('perf')).toEqual(['perf_eval'])
    expect(freshnessOf(rows, areaSources('perf'))).toMatchObject({ ageMs: 10 * 60_000 })
    expect(freshnessOf(rows, areaSources('nowhere'))).toBeNull()
  })

  it('reads the published fixture: core age is the oldest core collection', () => {
    const now = Date.parse('2026-10-17T09:38:09Z')
    const rows = sourceFreshness(MANIFEST.shell, { generated_at: '2026-10-17T09:00:00Z' }, now)
    const core = coreFreshness(rows)!
    expect(core.timestamp).toBe('2026-10-17T05:39:01Z')
    expect(core.state).toBe('stale')
    expect(queueFreshness(rows)).toMatchObject({ timestamp: '2026-10-17T09:29:14Z', state: 'fresh' })
  })
})

describe('data freshness sheet rows', () => {
  it('lists the worst state first, then by label, with the classic drawer fields', () => {
    const rows = freshnessRows(MANIFEST.shell, { generated_at: '2026-10-17T09:00:00Z' }, Date.parse('2026-10-17T09:38:09Z'))
    const severity = { fresh: 0, warn: 1, stale: 2 }
    for (let index = 1; index < rows.length; index += 1) {
      const [left, right] = [rows[index - 1]!, rows[index]!]
      expect(severity[left.state] > severity[right.state] || (left.state === right.state && left.label.localeCompare(right.label) <= 0)).toBe(true)
    }
    const signal = rows.find((row) => row.key === 'amd_test_signal')!
    expect(signal).toMatchObject({
      timestamp: '2026-10-15T06:00:02.875000Z',
      path: 'analytics.json',
      timestampSource: 'amd_test_health.summary.latest_observed_at',
      staleMinutes: 2160,
      published: true,
      state: 'stale',
    })
    expect(rows.find((row) => row.key === 'agent_health')!.published).toBe(false)
    expect(rows.find((row) => row.key === 'perf_eval')!.path).toBe('perf_eval/perf_eval.json')
  })
})

describe('page freshness chip', () => {
  const row = (over: Partial<SourceFreshness>): SourceFreshness => ({
    key: 'dns',
    label: 'DNS observations',
    area: 'analytics',
    informational: false,
    timestamp: minutesAgo(90),
    ageMs: 90 * 60_000,
    state: 'warn',
    warnMinutes: 60,
    staleMinutes: 180,
    path: 'data/vllm/ci/dns_failures.json',
    timestampSource: 'payload',
    published: true,
    unavailableReason: null,
    ...over,
  })

  it('states the age, the tone of the state, and the target the source is held to', () => {
    const chip = freshnessChip(freshnessOf([row({})], ['dns']), NOW)!
    expect(chip.tone).toBe('soft')
    expect(chip.text).toBe('Collected 1h ago')
    expect(chip.detail).toBe('DNS observations 1h ago (target 1h / 3h).')
    expect([freshnessChip(freshnessOf([row({ state: 'fresh' })], ['dns']), NOW)!.tone, freshnessChip(freshnessOf([row({ state: 'stale' })], ['dns']), NOW)!.tone]).toEqual(['pass', 'hard'])
  })

  it('names a single limit once and an unknown collection time plainly', () => {
    const chip = freshnessChip(freshnessOf([row({ timestamp: null, ageMs: null, state: 'stale', warnMinutes: 180, path: null })], ['dns']), NOW)!
    expect(chip.text).toBe('Collected at an unknown time')
    expect(chip.detail).toBe('DNS observations without a timestamp (target 3h).')
  })
})
