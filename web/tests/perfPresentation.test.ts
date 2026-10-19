import { describe, expect, it } from 'vitest'
import type { Rec } from '@/domain/format'
import {
  perfChanges,
  perfHardwareRows,
  perfLatestBuild,
  perfModelRows,
  perfModels,
  perfModelTrend,
  perfNightlyActivity,
  perfShortModel,
} from '@/domain/perf'
import { readFixture } from './support/fixtures'

const perf = readFixture<Rec>('perf_eval.json')
const models = perfModels(perf)

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
})
