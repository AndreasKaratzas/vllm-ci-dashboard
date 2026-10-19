import type { Detail } from '@/components/evidence/detail'
import { link, mono, text, type ColumnSpec } from '@/components/table/columns'
import {
  AMD_AGENT_HEALTH_ASSET,
  agentCells,
  agentCofailLabel,
  cofailureCard,
  nodeEvidence,
  signalColumnLabel,
  signalTerm,
  type AgentDay,
  type AgentModel,
  type AgentOptions,
  type AgentRow,
  type AgentRun,
  type AgentView,
  type CofailEvent,
} from '@/domain/agentHealth'
import { duration, integer, shortDate, value } from '@/domain/format'
import type { Tone } from '@/lib/tone'

const SOURCES = [{ label: 'Agent-health data', url: AMD_AGENT_HEALTH_ASSET }]

const minutes = (run: AgentRun) => (run._end !== null && run._start !== null ? (run._end - run._start) / 60000 : null)
const STATE_LABEL: Record<string, string> = { hard: 'Hard fail', soft: 'Soft fail', pass: 'Passed', passed: 'Passed', canceled: 'Canceled' }
const STATE_TONE: Record<string, Tone> = { hard: 'hard', soft: 'soft', pass: 'pass', passed: 'pass', canceled: 'not-targeted' }
export const stateLabel = (state: string) => STATE_LABEL[String(state || '').toLowerCase()] ?? value(state, 'Unknown')
/** The run states agentStateColor distinguishes, as tones. */
export const runTone = (state: string): Tone => STATE_TONE[String(state || '').toLowerCase()] ?? 'neutral'

/** Failing runs, newest first; the test group links to its Buildkite log. */
function runColumns(withNode: boolean): ColumnSpec<AgentRun>[] {
  return [
    { id: 'group', label: 'Test group', value: (run) => value(run.group), cell: (run) => link(run.group, run.url), hideable: false },
    { id: 'started', label: 'Started', value: (run) => run._start ?? 0, cell: (run) => text(shortDate(run.started_at)) },
    { id: 'state', label: 'State', value: (run) => run.state, cell: (run) => ({ text: stateLabel(run.state), tone: runTone(run.state) }) },
    ...(withNode ? [{ id: 'node', label: 'Physical node', value: (run: AgentRun) => value(run.node_raw), cell: (run: AgentRun) => mono(run.node_raw) }] : []),
    { id: 'queue', label: 'Queue', value: (run) => value(run.queue), cell: (run) => mono(run.queue) },
    { id: 'build', label: 'Build', value: (run) => Number(run.build_number) || 0, cell: (run) => mono(`${value(run.pipeline)} #${value(run.build_number)}`) },
    { id: 'duration', label: 'Duration', value: (run) => minutes(run) ?? -1, cell: (run) => text(duration(minutes(run))), numeric: true },
  ]
}

/** One failing run from the timeline. */
export function runDetail(run: AgentRun): Detail {
  return {
    id: `agent-run:${run.pipeline}:${run.build_number}:${run.group}:${run.started_at}`,
    title: value(run.group),
    subtitle: `${stateLabel(run.state)} on ${value(run.node_raw)}`,
    stats: [
      { label: 'State', value: stateLabel(run.state) },
      { label: 'Duration', value: duration(minutes(run)) },
      { label: 'Build', value: `#${value(run.build_number)}` },
      { label: 'Infra-suspect', value: run.infra_suspect ? 'Yes' : 'No' },
    ],
    fields: [
      { label: 'Physical node', value: run.node_raw },
      { label: 'GPU', value: run.hardware },
      { label: 'Pipeline', value: run.pipeline },
      { label: 'Queue', value: run.queue },
      { label: 'Started', value: shortDate(run.started_at) },
      { label: 'Nightly build', value: run.nightly ? 'Yes' : 'No' },
      { label: 'Build canceled', value: run.build_canceled ? 'Yes' : 'No' },
    ],
    sources: run.url ? [{ label: 'Buildkite log', url: run.url }] : SOURCES,
    empty: run.url ? undefined : 'The exact Buildkite log is unavailable for this run.',
    width: 'md',
  }
}

/** One node's reliability and its retained failing runs. */
export function nodeDetail(model: AgentModel, view: AgentView, options: AgentOptions, agent: AgentRow): Detail {
  const evidence = nodeEvidence(agent, options)
  const cells = agentCells(model, view, agent)
  return {
    id: `agent-node:${agent.node_raw}`,
    title: evidence.title,
    subtitle: evidence.subtitle,
    stats: [
      { label: 'Runs', value: cells.runs },
      { label: 'Fail %', value: cells.failRate },
      { label: 'Test group failures', value: cells.failures, meta: cells.split ?? undefined },
      ...(options.signal === 'all' ? [] : [{ label: signalColumnLabel(options.signal), value: cells.signal }]),
      { label: 'Groups', value: cells.groups },
      { label: 'Co-failure events', value: cells.cofail },
    ],
    fields: [
      { label: 'Physical node', value: agent.node_raw },
      { label: 'GPU', value: agent.hardware },
      { label: 'Canceled runs', value: integer(agent.canceled) },
    ],
    sources: SOURCES,
    tables: [
      {
        title: `Retained ${signalTerm(options.signal)} failing runs`,
        meta: `${integer(evidence.rows.length)} in ${options.windowId}`,
        columns: runColumns(false),
        rows: evidence.rows.map((point) => point.run),
        emptyText: 'No failing runs are retained for this node in the selected window.',
        pageSize: 25,
      },
    ],
    width: 'xl',
  }
}

/** A co-failure event: every run in the cluster with its timing. */
export function eventDetail(event: CofailEvent): Detail {
  const card = cofailureCard(event, true)
  return {
    id: `agent-event:${event.node_raw}@${event._startMs}`,
    title: card.node,
    subtitle: `${card.pattern === 'concurrent' ? 'Concurrent' : 'Sequential'} co-failure · ${card.meta}`,
    description:
      card.pattern === 'concurrent'
        ? 'The runs overlapped in time, which points to contention or an ephemeral host or network fault.'
        : 'The runs failed back to back, which suggests the node was left in an unclean state.',
    stats: [
      { label: 'Runs', value: integer(event.run_count) },
      { label: 'Hard', value: integer(event.hard_failed) },
      { label: 'Soft', value: integer(event.soft_failed) },
      { label: 'Span', value: duration(event.span_mins) },
    ],
    fields: [
      { label: 'Physical node', value: event.node_raw },
      { label: 'Started', value: shortDate(event.started_at) },
      { label: 'Test groups', value: integer(event.group_count) },
      { label: 'Pipelines', value: event.pipelines.join(', ') },
    ],
    notes: event.cross_pipeline ? [{ tone: 'hard', text: 'The failures span more than one pipeline.' }] : [],
    sources: SOURCES,
    tables: [{ title: 'Runs in this event', columns: runColumns(false), rows: event.runs, pageSize: false }],
    width: 'xl',
  }
}

/** The retained failing runs of one UTC day. */
export function dayDetail(day: AgentDay, runs: AgentRun[], options: AgentOptions): Detail {
  return {
    id: `agent-day:${day.day}`,
    title: `${signalTerm(options.signal)} failures on ${day.day}`,
    subtitle: `${integer(day.hard)} hard and ${integer(day.soft)} soft in compact accounting; the table lists the retained runs`,
    stats: [
      { label: 'Signal failures', value: integer(day.signal) },
      { label: 'Failing nodes', value: integer(day.failingNodes) },
      { label: 'Runs', value: integer(day.runs) },
      { label: 'Co-failure events', value: integer(day.cofailEvents) },
    ],
    sources: SOURCES,
    tables: [
      {
        columns: runColumns(true),
        rows: runs.filter((run) => run.day === day.day).sort((a, b) => (b._start ?? 0) - (a._start ?? 0)),
        emptyText: 'No failing runs are retained for this day.',
        pageSize: 25,
      },
    ],
    width: 'xl',
  }
}

/** Nodes with at least one failure matching the signal, each drilling into its runs. */
export function failingNodesDetail(model: AgentModel, view: AgentView, options: AgentOptions): Detail {
  const rows = view.agents.filter((agent) => agent.identified && agent.infra_suspect > 0).sort((a, b) => b.infra_suspect - a.infra_suspect)
  const label = options.signal === 'all' ? 'Test group failures' : signalColumnLabel(options.signal)
  return {
    id: 'agent-failing-nodes',
    title: 'Nodes with failures',
    subtitle: `${integer(rows.length)} nodes with ${signalTerm(options.signal)} failures in ${options.windowId}; co-failure window ${agentCofailLabel(options.cofailMins)}`,
    sources: SOURCES,
    tables: [
      {
        columns: [
          {
            id: 'node',
            label: 'Physical node',
            value: (agent: AgentRow) => agent.node,
            cell: (agent: AgentRow) => ({ text: agent.node, mono: true, detail: () => nodeDetail(model, view, options, agent) }),
            hideable: false,
          },
          { id: 'signal', label, value: (agent: AgentRow) => agent.infra_suspect, cell: (agent: AgentRow) => text(agentCells(model, view, agent).signal), numeric: true },
          { id: 'cofail', label: 'Co-failure events', value: (agent: AgentRow) => agent.cofailure_event_count, numeric: true },
        ],
        rows,
        pageSize: 25,
      },
    ],
    width: 'lg',
  }
}
