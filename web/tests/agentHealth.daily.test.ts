import { describe, expect, it } from 'vitest'
import {
  accountingSignal,
  agentDailySeries,
  agentHealthModel,
  agentOptions,
  computeAgentView,
  signalMatch,
  type AgentModel,
  type AgentRouteState,
} from '@/domain/agentHealth'
import type { Rec } from '@/domain/format'
import { pinClock, readFixture } from './support/fixtures'

const fixture = readFixture<Rec>('agent_health_live.json')
pinClock(fixture.generated_at)

function series(route: AgentRouteState, edit?: (model: AgentModel) => AgentModel) {
  const base = agentHealthModel(fixture)
  const model = edit ? edit(base) : base
  const options = agentOptions(model, route)
  const view = computeAgentView(model, options)
  return { model, options, view, days: agentDailySeries(model, view, options) }
}
const sum = (values: number[]) => values.reduce((total, value) => total + value, 0)

describe('accountingSignal', () => {
  it('counts infra-suspect rows for infra, hard rows for hard, and every row for all', () => {
    const infraSoft = { s: 'soft', i: 1 }
    const plainHard = { s: 'hard', i: 0 }
    expect([accountingSignal(infraSoft, 'infra'), accountingSignal(plainHard, 'infra')]).toEqual([true, false])
    expect([accountingSignal(infraSoft, 'hard'), accountingSignal(plainHard, 'hard')]).toEqual([false, true])
    expect([accountingSignal(infraSoft, 'all'), accountingSignal(plainHard, 'all')]).toEqual([true, true])
    expect(accountingSignal(plainHard, undefined)).toBe(false)
  })
})

describe('agentDailySeries', () => {
  it('has one entry per UTC day of the window, oldest first', () => {
    const { days, model } = series({ window: '7d' })
    const end = new Date(model.endMs).toISOString().slice(0, 10)
    const start = new Date(model.endMs - 7 * 86400000).toISOString().slice(0, 10)
    expect(days.at(0)!.day).toBe(start)
    expect(days.at(-1)!.day).toBe(end)
    expect(days).toHaveLength(8)
    expect(days.map((d) => d.day)).toEqual([...days.map((d) => d.day)].sort())
  })

  for (const signal of ['infra', 'hard', 'all']) {
    it(`splits the ${signal} signal of the compact accounting by day and agrees with the node totals`, () => {
      const { days, view, model } = series({ window: '14d', signal })
      expect(model.failureAccounting.length).toBeGreaterThan(0)
      expect(sum(days.map((d) => d.signal))).toBeGreaterThan(0)
      expect(sum(days.map((d) => d.signal))).toBe(sum(view.agents.map((a) => a.infra_suspect)))
      expect(sum(days.map((d) => d.hard))).toBe(sum(view.agents.map((a) => a.hard)))
      expect(sum(days.map((d) => d.soft))).toBe(sum(view.agents.map((a) => a.soft)))
      for (const day of days) {
        expect(day.signal).toBe(day.signalHard + day.signalSoft)
        expect(day.signalHard).toBeLessThanOrEqual(day.hard)
        expect(day.signalSoft).toBeLessThanOrEqual(day.soft)
        if (!day.signal) expect(day.failingNodes).toBe(0)
        if (signal === 'hard') expect(day.signalSoft).toBe(0)
      }
    })
  }

  it('counts the node_days runs of the selected build scope, per day', () => {
    for (const nightly of ['0', '1']) {
      const { days, options, model } = series({ window: '7d', nightly })
      const byDay = new Map<string, number>()
      for (const nd of model.nodeDays) {
        const bucket = options.nightlyOnly ? nd.n : nd.a
        if (bucket?.[0]) byDay.set(nd.d, (byDay.get(nd.d) ?? 0) + bucket[0])
      }
      for (const day of days) expect(day.runs).toBe(byDay.get(day.day) ?? 0)
      expect(days.every((d) => d.identifiedRuns <= d.runs)).toBe(true)
    }
  })

  it('places every co-failure event on the day it started', () => {
    const { days, view } = series({ window: '14d', signal: 'all' })
    expect(view.events.length).toBeGreaterThan(0)
    expect(sum(days.map((d) => d.cofailEvents))).toBe(view.events.length)
  })

  it('without compact accounting, counts every retained failing run and the signal subset separately', () => {
    const { days, view, model, options } = series({ window: '14d', signal: 'infra' }, (m) => ({ ...m, failureAccounting: [] }))
    const inWindow = model.failing.filter((run) => days.some((d) => d.day === run.day))
    expect(sum(days.map((d) => d.signal))).toBe(view.fRuns.length)
    expect(sum(days.map((d) => d.hard + d.soft))).toBeGreaterThan(sum(days.map((d) => d.signal)))
    expect(sum(days.map((d) => d.signal))).toBe(inWindow.filter((run) => view.fRuns.includes(run) && signalMatch(options.signal, run)).length)
    // The totals match what the node table counts on the same fallback.
    expect(sum(days.map((d) => d.hard))).toBe(sum(view.agents.map((a) => a.hard)))
    expect(sum(days.map((d) => d.soft))).toBe(sum(view.agents.map((a) => a.soft)))
  })
})
