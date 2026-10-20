import { describe, expect, it } from 'vitest'
import { dayDetail, eventDetail, loadDetail, nodeDayDetail, nodeDetail, nodeOfDetail } from '@/components/analytics/agent-health/details'
import NodeDays from '@/components/analytics/agent-health/NodeDays.vue'
import NodeTimeline from '@/components/analytics/agent-health/NodeTimeline.vue'
import { agentDailySeries, agentHealthModel, agentLoadBy, agentNodeDayMatrix, agentOptions, computeAgentView, type AgentRouteState } from '@/domain/agentHealth'
import type { Rec } from '@/domain/format'
import { pinClock, readFixture } from './support/fixtures'

const fixture = readFixture<Rec>('agent_health_live.json')
pinClock(fixture.generated_at)

function page(route: AgentRouteState) {
  const model = agentHealthModel(fixture)
  const options = agentOptions(model, route)
  const view = computeAgentView(model, options)
  return { model, options, view, matrix: agentNodeDayMatrix(model, view, options), days: agentDailySeries(model, view, options) }
}

describe('agent-health sheets', () => {
  it('give a node its days, its timeline, its failing groups, its co-failure events and its runs', () => {
    const { model, options, view, matrix } = page({ window: '14d', signal: 'all' })
    const history = matrix.nodes.find((node) => node.cofailEvents > 0)!
    const agent = view.agents.find((a) => a.node_raw === history.node_raw)!
    const detail = nodeDetail(model, view, options, agent, history)
    expect(detail.title).toBe(agent.node)
    expect(detail.sections!.map((s) => [s.title, s.component])).toEqual([
      ['Failures per day', NodeDays],
      ['Run timeline', NodeTimeline],
    ])
    expect(detail.stats!.find((s) => s.label === 'Days with failures')).toEqual({ label: 'Days with failures', value: String(history.activeDays), meta: 'of 15 days' })
    expect(detail.tables!.map((t) => t.title)).toEqual(['Test groups failing on this node', 'Co-failure events', 'Retained all failing runs'])
    const [groups, events, runs] = detail.tables!
    expect(groups!.rows.reduce((total: number, g) => total + (g as { failures: number }).failures, 0)).toBe(agent._runs.length)
    expect(events!.rows).toHaveLength(agent.cofailure_event_count)
    // An event row opens the event with this node's timeline around it.
    const event = events!.onRowClick!(events!.rows[0]) as ReturnType<typeof eventDetail>
    expect(event.sections![0]!.props).toMatchObject({ runs: agent._runs })
    expect(runs!.rows).toHaveLength(agent._runs.length)
    // Without its daily history the node keeps its timeline.
    expect(nodeDetail(model, view, options, agent, null).sections!.map((s) => s.title)).toEqual(['Run timeline'])
  })

  it('list the nodes failing on a day and drill into that node on that day', () => {
    const { options, view, matrix, days } = page({ window: '14d', signal: 'all' })
    const day = days.find((d) => d.failingNodes > 1)!
    const detail = dayDetail(day, matrix, view.fRuns, options)
    const [nodes, runs] = detail.tables!
    expect(nodes!.rows).toHaveLength(day.failingNodes)
    expect(runs!.rows.every((run) => (run as { day: string }).day === day.day)).toBe(true)
    const at = matrix.days.indexOf(day.day)
    const counts = nodes!.rows.map((n) => (n as (typeof matrix.nodes)[number]).cells[at]!.signal)
    expect(counts.reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(day.signal)
    const nodeDay = nodes!.onRowClick!(nodes!.rows[0]) as ReturnType<typeof nodeDayDetail>
    expect(nodeDay.title).toMatch(/ on [A-Z][a-z]{2} \d+$/)
    expect(nodeDay.tables![0]!.rows.every((run) => (run as { day: string; node_raw: string }).day === day.day)).toBe(true)
  })

  it('open a queue by node and test group, and name long test groups without the AMD prefix', () => {
    const { options, view, model } = page({ window: '14d', signal: 'all' })
    const [queue] = agentLoadBy(view.fRuns, (run) => run.queue)
    const opened: string[] = []
    const detail = loadDetail('queue', queue!, options, (node) => {
      opened.push(node)
      const agent = view.agents.find((a) => a.node_raw === node)
      return agent ? nodeDetail(model, view, options, agent, null) : undefined
    })
    expect(detail.tables!.map((t) => t.title)).toEqual(['Nodes', 'Test groups', 'Retained failing runs'])
    expect(detail.tables![0]!.onRowClick!(detail.tables![0]!.rows[0])).toMatchObject({ id: `agent-node:${opened[0]}` })
    const [group] = agentLoadBy(view.fRuns, (run) => run.group)
    expect(loadDetail('group', group!, options, () => undefined).tables!.map((t) => t.title)).toEqual(['Nodes', 'Retained failing runs'])
    // agent_node follows node sheets only, whichever list opened them.
    expect([detail, detail.tables![0]!.onRowClick!(detail.tables![0]!.rows[0]) as typeof detail].map(nodeOfDetail)).toEqual(['', opened[0]])
  })
})
