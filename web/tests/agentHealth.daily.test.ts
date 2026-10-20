import { describe, expect, it } from 'vitest'
import {
  accountingSignal,
  agentDailySeries,
  agentFleetVerdict,
  agentHealthModel,
  agentLoadBy,
  agentNodeDayMatrix,
  agentOptions,
  computeAgentView,
  signalMatch,
  signalNoun,
  type AgentModel,
  type AgentNodeDayMatrix,
  type AgentNodeHistory,
  type AgentRouteState,
  type AgentSignal,
  type AgentView,
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

describe('agentNodeDayMatrix', () => {
  for (const signal of ['infra', 'hard', 'all']) {
    it(`splits each identified node's ${signal} failures by day and agrees with the node table`, () => {
      const { days, view, model, options } = series({ window: '14d', signal })
      const matrix = agentNodeDayMatrix(model, view, options)
      expect(matrix.days).toEqual(days.map((d) => d.day))
      expect(matrix.nodes.length).toBeGreaterThan(0)
      for (const node of matrix.nodes) {
        const agent = view.agents.find((a) => a.node_raw === node.node_raw)!
        expect(node.signal).toBe(agent.infra_suspect)
        expect(node.signal).toBe(node.signalHard + node.signalSoft)
        expect(node.cofailEvents).toBe(agent.cofailure_event_count)
        expect(node.activeDays).toBe(node.cells.filter((c) => c.signal > 0).length)
        expect(node.node).toBe(agent.node)
      }
      // Every failing identified node is listed, and nothing else.
      const failing = view.agents.filter((a) => a.identified && (a.infra_suspect > 0 || a.cofailure_event_count > 0))
      expect(matrix.nodes.map((n) => n.node_raw).sort()).toEqual(failing.map((a) => a.node_raw).sort())
      // Per day, the nodes add up to the fleet minus unidentified runs.
      matrix.days.forEach((day, i) => {
        const nodes = sum(matrix.nodes.map((n) => n.cells[i]!.signal))
        expect(nodes).toBeLessThanOrEqual(days[i]!.signal)
        expect(matrix.nodes.filter((n) => n.cells[i]!.signal > 0).length).toBe(days[i]!.failingNodes)
        expect(Math.max(0, ...matrix.nodes.map((n) => n.cells[i]!.signal))).toBeLessThanOrEqual(matrix.max)
      })
    })
  }

  it('ranks by failures, then days failed on, then co-failure events, then name', () => {
    const { view, model, options } = series({ window: '14d', signal: 'all' })
    const nodes = agentNodeDayMatrix(model, view, options).nodes
    for (let i = 1; i < nodes.length; i++) {
      const [a, b] = [nodes[i - 1]!, nodes[i]!]
      const key = (n: typeof a) => [n.signal, n.activeDays, n.cofailEvents]
      const order = key(a).map((v, k) => v - key(b)[k]!).find((d) => d !== 0)
      expect(order === undefined ? a.node_raw < b.node_raw : order > 0).toBe(true)
    }
  })

  it('counts the last three days as recent and the node_days runs per cell', () => {
    const { view, model, options } = series({ window: '14d', signal: 'all' })
    const matrix = agentNodeDayMatrix(model, view, options)
    expect(matrix.recentDays).toBe(3)
    for (const node of matrix.nodes) {
      expect(node.recent).toBe(sum(node.cells.slice(-3).map((c) => c.signal)))
      expect(node.recentCofail).toBe(sum(node.cells.slice(-3).map((c) => c.cofailEvents)))
      for (const cell of node.cells) {
        const runs = model.nodeDays.filter((nd) => nd.nd === node.node_raw && nd.d === cell.day).reduce((total, nd) => total + (nd.a?.[0] ?? 0), 0)
        expect(cell.runs).toBe(runs)
      }
    }
    const day = series({ window: '1d' })
    expect(agentNodeDayMatrix(day.model, day.view, day.options).recentDays).toBe(2)
  })
})

describe('agentFleetVerdict', () => {
  const cell = (signal: number, cofailEvents = 0) => ({ day: '', runs: 1, hard: signal, soft: 0, signalHard: signal, signalSoft: 0, signal, cofailEvents })
  const node = (raw: string, cells: ReturnType<typeof cell>[]): AgentNodeHistory => {
    const signal = sum(cells.map((c) => c.signal))
    const recent = cells.slice(-3)
    return {
      node: raw,
      node_raw: raw,
      hardware: 'MI355',
      cells,
      signal,
      signalHard: signal,
      signalSoft: 0,
      activeDays: cells.filter((c) => c.signal).length,
      recent: sum(recent.map((c) => c.signal)),
      recentCofail: sum(recent.map((c) => c.cofailEvents)),
      cofailEvents: sum(cells.map((c) => c.cofailEvents)),
    }
  }
  const matrix = (nodes: AgentNodeHistory[]): AgentNodeDayMatrix => ({ days: ['a', 'b', 'c', 'd'], nodes, max: 3, recentDays: 3 })
  const complete = { failureDetailIncomplete: false } as AgentView
  const options = { signal: 'infra', windowId: '7d' } as const

  it('is hurting when a node repeats a failure or clusters one recently', () => {
    const verdict = agentFleetVerdict(
      matrix([node('a', [cell(0), cell(0), cell(1), cell(1)]), node('b', [cell(0), cell(0), cell(0), cell(1, 1)]), node('c', [cell(3), cell(0), cell(0), cell(1)])]),
      complete,
      options,
    )
    expect(verdict).toMatchObject({ tone: 'hard', label: 'Hurting', headline: '2 nodes are hurting CI right now', recentFailures: 4, recentNodes: 3 })
    expect(verdict.hotNodes.map((n) => n.node_raw)).toEqual(['a', 'b'])
    expect(verdict.detail).toBe('4 infra-suspect failures on 3 nodes in the last 3 days; these repeat on one node or cluster into co-failure events.')
  })

  it('watches isolated recent failures and names the share of the worst nodes', () => {
    const nodes = ['a', 'b', 'c', 'd', 'e', 'f'].map((raw, i) => node(raw, [cell(i === 0 ? 5 : 1), cell(0), cell(0), cell(i === 0 ? 1 : 0)]))
    const verdict = agentFleetVerdict(matrix(nodes), { failureDetailIncomplete: true } as AgentView, options)
    expect(verdict).toMatchObject({ tone: 'soft', label: 'Watch', recentFailures: 1, recentNodes: 1, hotNodes: [] })
    expect(verdict.topShare).toBeCloseTo(10 / 11)
    expect(verdict.detail).toBe('At least 1 infra-suspect failure on 1 node in the last 3 days, none repeating on one node. The 5 worst nodes carry 91% of the 7d infra-suspect failures.')
  })

  it('is healthy without recent failures and says what happened earlier', () => {
    expect(agentFleetVerdict(matrix([node('a', [cell(2), cell(0), cell(0), cell(0)])]), complete, { signal: 'all', windowId: '7d' })).toMatchObject({
      tone: 'pass',
      headline: 'No node is hurting CI right now',
      detail: 'No failures in the last 3 days; 2 earlier in 7d.',
    })
    expect(agentFleetVerdict(matrix([]), complete, { signal: 'hard', windowId: '1d' })).toMatchObject({
      tone: 'pass',
      detail: 'No hard failures in the last 3 days or earlier in 1d.',
      topShare: null,
    })
  })
})

describe('agentLoadBy', () => {
  it('groups retained runs by a key, worst first, with the hard and soft split', () => {
    const { view } = series({ window: '14d', signal: 'all' })
    const queues = agentLoadBy(view.fRuns, (run) => run.queue)
    expect(sum(queues.map((q) => q.failures))).toBe(view.fRuns.length)
    for (const queue of queues) {
      expect(queue.hard + queue.soft).toBeLessThanOrEqual(queue.failures)
      expect(queue.nodes).toBe(new Set(queue.runs.map((r) => r.node_raw)).size)
      expect(queue.runs.every((r) => r.queue === queue.key)).toBe(true)
    }
    expect(queues.map((q) => q.failures)).toEqual([...queues.map((q) => q.failures)].sort((a, b) => b - a))
    const run = view.fRuns[0]!
    expect(agentLoadBy([{ ...run, queue: '' }], (r) => r.queue)[0]!.key).toBe('(unknown)')
  })

  it('names the signal as a plural noun', () => {
    expect(['infra', 'hard', 'all'].map((s) => signalNoun(s as AgentSignal))).toEqual(['infra-suspect failures', 'hard failures', 'failures'])
  })
})
