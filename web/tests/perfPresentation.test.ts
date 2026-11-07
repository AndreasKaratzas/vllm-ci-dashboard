import { describe, expect, it } from 'vitest'
import type { Rec } from '@/domain/format'
import {
  perfAccuracyOnly,
  perfChanges,
  perfConfigTitle,
  perfHardwareRows,
  perfLatestBuild,
  perfModelRows,
  perfModels,
  perfModelTrend,
  perfNightlyActivity,
  perfPointVerdict,
  perfScopedModels,
  perfShortModel,
} from '@/domain/perf'
import { readFixture } from './support/fixtures'

const perf = readFixture<Rec>('perf_eval.json')
const models = perfModels(perf)
// The collector's thresholds (collect_perf_eval.py); the fixture predates their publication.
const THRESHOLDS = perf.thresholds ?? { perf_rel: 0.02, accuracy_abs: 0.005 }

describe('Perf Eval presentation helpers', () => {
  it('lists exactly the regressions and improvements the overview counts, largest first', () => {
    const rows = perfModelRows(models)
    const changes = perfChanges(models)
    expect(changes.filter((c) => c.tone === 'hard')).toHaveLength(rows.reduce((sum, row) => sum + row.regressions, 0))
    expect(changes.filter((c) => c.tone === 'pass')).toHaveLength(rows.reduce((sum, row) => sum + row.improvements, 0))
    const sizes = changes.map((c) => Math.abs(c.deltaPct))
    expect(sizes).toEqual([...sizes].sort((a, b) => b - a))
  })

  it('groups every retained point into its nightly, oldest first', () => {
    const nightlies = perfNightlyActivity(models)
    const points = models.flatMap((model) => [
      ...(model.perf_configs || []).flatMap((config: Rec) => Object.values(config.metrics || {}).flatMap((block) => (block as Rec).series || [])),
      ...(model.accuracy_tasks || []).flatMap((task: Rec) => task.series || []),
    ])
    expect(nightlies).toHaveLength(new Set(points.map((point) => point.build_number)).size)
    expect(nightlies.reduce((sum, n) => sum + n.points, 0)).toBe(points.length)
    expect(nightlies.map((n) => n.date)).toEqual(nightlies.map((n) => n.date).sort())
    expect(nightlies.every((n) => n.models >= 1 && n.models <= models.length)).toBe(true)
  })

  it('splits configurations and metrics by device', () => {
    const rows = perfHardwareRows(perf)
    const configs = models.reduce((sum, model) => sum + (model.perf_configs || []).length, 0)
    expect(rows.map((row) => row.label)).toEqual(['MI300X', 'MI355X'])
    expect(rows.reduce((sum, row) => sum + row.configs, 0)).toBe(configs)
  })

  it('names the newest build, short model names and per-device trends', () => {
    expect(perfLatestBuild(models)?.number).toBe(Math.max(...models.map((m) => Number(m.latest.build_number))))
    expect(perfLatestBuild([])).toBeNull()
    expect(perfShortModel('openai/gpt-oss-120b')).toBe('gpt-oss-120b')
    expect(perfShortModel('plain')).toBe('plain')
    const dual = models.find((m) => m.devices.length > 1)!
    expect(perfModelTrend(dual, 'mi355x')?.device).toBe('mi355x')
    expect(perfModelTrend(dual, 'mi250')).toBeNull()
  })

  it('applies the collector threshold rule, matching every latest status, to each nightly', () => {
    const blocks = models.flatMap((model) => [
      ...(model.perf_configs || []).flatMap((config: Rec) => Object.values(config.metrics || {}).map((block) => ({ block: block as Rec, relative: true }))),
      ...(model.accuracy_tasks || []).map((task: Rec) => ({ block: { ...task, direction: task.direction || 'higher' }, relative: false })),
    ])
    for (const { block, relative } of blocks) {
      expect(perfPointVerdict(block.previous, block.latest, block.direction, relative, THRESHOLDS)).toBe(block.status)
    }
    // The newest nightly's counts are every series' last step, as the latest statuses are.
    const nightlies = perfNightlyActivity(models, THRESHOLDS)
    const steps = blocks.map(({ block, relative }) => {
      const series = (block.series as Rec[]).filter((point) => Number.isFinite(Number(point.value)))
      return series.length < 2 ? 'neutral' : perfPointVerdict(series.at(-2)!.value, series.at(-1)!.value, block.direction, relative, THRESHOLDS)
    })
    expect(nightlies.at(-1)!.regressions).toBe(steps.filter((step) => step === 'bad').length)
    expect(nightlies.at(-1)!.improvements).toBe(steps.filter((step) => step === 'good').length)
    expect(nightlies.some((n) => n.regressions !== nightlies.at(-1)!.regressions)).toBe(true)
    expect(perfNightlyActivity(models).every((n) => n.regressions === 0 && n.improvements === 0)).toBe(true)
    expect(perfPointVerdict(100, 103, 'higher', true, { perf_rel: 0.02 })).toBe('good')
    expect(perfPointVerdict(100, 101, 'lower', true, { perf_rel: 0.02 })).toBe('neutral')
    expect(perfPointVerdict(0.8, 0.79, 'higher', false, { accuracy_abs: 0.005 })).toBe('bad')
  })

  it('scopes models to the hardware filter and titles configurations without placeholders', () => {
    const dual = models.find((m) => m.devices.length > 1)!
    const scoped = perfScopedModels(models, { model: 'all', device: 'mi300x' })
    expect(scoped.map((m) => m.model)).toContain(dual.model)
    expect(scoped.flatMap((m) => m.perf_configs).every((c: Rec) => c.device === 'mi300x')).toBe(true)
    // Accuracy runs per device too: a dual-device model keeps only the filtered GPU's tasks.
    const tasks = scoped.flatMap((m) => m.accuracy_tasks || [])
    expect(tasks.length).toBeGreaterThan(0)
    expect(tasks.every((t: Rec) => t.device === 'mi300x')).toBe(true)
    expect(scoped.find((m) => m.model === dual.model)!.accuracy_tasks).toHaveLength(
      dual.accuracy_tasks.filter((t: Rec) => t.device === 'mi300x').length,
    )
    expect(perfAccuracyOnly(scoped).every((m) => m.perf_configs.length === 0)).toBe(true)
    expect(perfConfigTitle({ label: '? in / ? out @ conc 128 (MI300X)' })).toBe('Concurrency 128 (MI300X)')
    expect(perfConfigTitle({ label: '8K in / 1K out @ conc 16 (MI355X)' })).toBe('8K in / 1K out @ conc 16 (MI355X)')
    expect(perfConfigTitle({ label: '? in / 1K out @ conc 1 (MI355X)' })).toBe('1K out @ conc 1 (MI355X)')
  })
})
