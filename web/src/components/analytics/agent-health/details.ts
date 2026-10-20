import type { LegendItem } from '@/components/charts/Legend.vue'
import type { RankedItem } from '@/components/charts/RankedBars.vue'
import { shortDay, type SeriesSpec } from '@/components/charts/chart'
import type { Detail } from '@/components/evidence/detail'
import { link, mono, text, type CellSpec, type ColumnSpec } from '@/components/table/columns'
import {
  AMD_AGENT_HEALTH_ASSET,
  agentCells,
  agentCofailLabel,
  agentLoadBy,
  cofailureCard,
  eventTimelineRange,
  nodeEvidence,
  signalColumnLabel,
  signalNoun,
  signalTerm,
  type AgentDay,
  type AgentLoad,
  type AgentModel,
  type AgentNodeDayMatrix,
  type AgentNodeHistory,
  type AgentOptions,
  type AgentRow,
  type AgentRun,
  type AgentView,
  type CofailEvent,
} from '@/domain/agentHealth'
import { duration, integer, shortDate, value } from '@/domain/format'
import { hardwareColor, heatColor, statusVar } from '@/lib/palette'
import type { Tone } from '@/lib/tone'
import NodeDays from './NodeDays.vue'
import NodeTimeline from './NodeTimeline.vue'

const SOURCES = [{ label: 'Agent-health data', url: AMD_AGENT_HEALTH_ASSET }]

const minutes = (run: AgentRun) => (run._end !== null && run._start !== null ? (run._end - run._start) / 60000 : null)
const STATE_LABEL: Record<string, string> = { hard: 'Hard fail', soft: 'Soft fail', pass: 'Passed', passed: 'Passed', canceled: 'Canceled' }
const STATE_TONE: Record<string, Tone> = { hard: 'hard', soft: 'soft', pass: 'pass', passed: 'pass', canceled: 'not-targeted' }
export const stateLabel = (state: string) => STATE_LABEL[String(state || '').toLowerCase()] ?? value(state, 'Unknown')
/** The run states agentStateColor distinguishes, as tones. */
export const runTone = (state: string): Tone => STATE_TONE[String(state || '').toLowerCase()] ?? 'neutral'

/** Signal failures split by run state; hard and soft are test state, so they wear the status colors. */
export const FAILURE_SERIES: SeriesSpec[] = [
  { key: 'signalHard', label: 'Hard fail', color: statusVar('hard') },
  { key: 'signalSoft', label: 'Soft fail', color: statusVar('soft') },
]
export const RUN_LEGEND: LegendItem[] = [
  { key: 'hard', label: 'Hard fail', color: statusVar('hard') },
  { key: 'soft', label: 'Soft fail', color: statusVar('soft') },
]

/** Retained failures per queue or test group as ranked bars split into hard and soft. */
export function loadItems(loads: AgentLoad[], kind: 'queue' | 'group'): RankedItem[] {
  return loads.map((load) => ({
    key: load.key,
    // Group names repeat the AMD prefix of every AMD job; the full name stays in the tooltip and sheet.
    label: kind === 'group' ? load.key.replace(/^(AMD:\s*)?:amd:\s*/, '') : load.key,
    title: load.key,
    value: load.failures,
    segments: [
      { value: load.hard, color: FAILURE_SERIES[0]!.color, label: 'hard' },
      { value: load.soft, color: FAILURE_SERIES[1]!.color, label: 'soft' },
    ],
    meta: kind === 'queue' ? `${plural(load.nodes, 'node')} · ${plural(load.groups, 'group')}` : plural(load.nodes, 'node'),
  }))
}

const capitalized = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)
const plural = (count: number, word: string) => `${integer(count)} ${word}${count === 1 ? '' : 's'}`
const dayName = (day: string) => shortDay(Date.parse(`${day}T00:00:00Z`))
/** A failure count with an inline bar against the largest count in its table. */
const countBar = (count: number, max: number): CellSpec => ({ text: integer(count), muted: count === 0, bar: { value: count, max, color: heatColor(4) } })
const nodeCell = (label: string, hardware: string): CellSpec => ({ text: label, mono: true, dots: [hardwareColor(hardware)] })

/** When a co-failure event started, how long it spanned, and how many test groups failed in it. */
export function eventMeta(event: CofailEvent): string {
  return `${shortDate(event.started_at)} · ${duration(event.span_mins)} span · ${plural(event.group_count, 'group')}`
}

/** Failing runs, newest first; the test group links to its Buildkite log. */
function runColumns(withNode: boolean): ColumnSpec<AgentRun>[] {
  return [
    { id: 'group', label: 'Test group', value: (run) => value(run.group), cell: (run) => link(run.group, run.url), hideable: false },
    { id: 'started', label: 'Started', value: (run) => run._start ?? 0, cell: (run) => text(shortDate(run.started_at)) },
    { id: 'state', label: 'State', value: (run) => run.state, cell: (run) => ({ text: stateLabel(run.state) }) },
    ...(withNode ? [{ id: 'node', label: 'Physical node', value: (run: AgentRun) => value(run.node_raw), cell: (run: AgentRun) => nodeCell(run.node_raw, run.hardware) }] : []),
    { id: 'queue', label: 'Queue', value: (run) => value(run.queue), cell: (run) => mono(run.queue) },
    { id: 'build', label: 'Build', value: (run) => Number(run.build_number) || 0, cell: (run) => mono(`${value(run.pipeline)} #${value(run.build_number)}`) },
    { id: 'duration', label: 'Duration', value: (run) => minutes(run) ?? -1, cell: (run) => text(duration(minutes(run))), numeric: true },
  ]
}

const newestFirst = (runs: AgentRun[]) => runs.slice().sort((a, b) => (b._start ?? 0) - (a._start ?? 0))

/** Retained failures grouped by test group, queue or node: the count with its bar and the hard/soft split. */
function loadColumns(id: string, label: string, rows: AgentLoad[], keyCell: (load: AgentLoad) => CellSpec, extra: ColumnSpec<AgentLoad>[] = []): ColumnSpec<AgentLoad>[] {
  const max = Math.max(1, ...rows.map((row) => row.failures))
  return [
    { id, label, value: (load) => load.key, cell: keyCell, hideable: false },
    { id: 'failures', label: 'Failures', value: (load) => load.failures, cell: (load) => countBar(load.failures, max), numeric: true },
    { id: 'hard', label: 'Hard', value: (load) => load.hard, cell: (load) => ({ text: integer(load.hard), muted: !load.hard }), numeric: true },
    { id: 'soft', label: 'Soft', value: (load) => load.soft, cell: (load) => ({ text: integer(load.soft), muted: !load.soft }), numeric: true },
    ...extra,
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
      { label: 'Physical node', value: run.node_raw, mono: true },
      { label: 'GPU', value: run.hardware },
      { label: 'Pipeline', value: run.pipeline },
      { label: 'Queue', value: run.queue, mono: true },
      { label: 'Started', value: shortDate(run.started_at) },
      { label: 'Nightly build', value: run.nightly ? 'Yes' : 'No' },
      { label: 'Build canceled', value: run.build_canceled ? 'Yes' : 'No' },
    ],
    sources: run.url ? [{ label: 'Buildkite log', url: run.url }] : SOURCES,
    empty: run.url ? undefined : 'The exact Buildkite log is unavailable for this run.',
    width: 'md',
  }
}

/** Co-failure events, newest first; each opens its runs. */
function eventsTable(events: CofailEvent[], nodeRuns: AgentRun[]) {
  return {
    title: 'Co-failure events',
    meta: plural(events.length, 'event'),
    columns: [
      { id: 'started', label: 'Started', value: (event: CofailEvent) => event._startMs, cell: (event: CofailEvent) => text(shortDate(event.started_at)), hideable: false },
      { id: 'pattern', label: 'Pattern', value: (event: CofailEvent) => event.pattern, cell: (event: CofailEvent) => text(capitalized(event.pattern)) },
      { id: 'runs', label: 'Runs', value: (event: CofailEvent) => event.run_count, numeric: true },
      { id: 'groups', label: 'Groups', value: (event: CofailEvent) => event.group_count, numeric: true },
      { id: 'span', label: 'Span', value: (event: CofailEvent) => event.span_mins, cell: (event: CofailEvent) => text(duration(event.span_mins)), numeric: true },
    ] as ColumnSpec<CofailEvent>[],
    rows: events.slice().sort((a, b) => b._startMs - a._startMs),
    pageSize: 10 as const,
    onRowClick: (event: CofailEvent) => eventDetail(event, nodeRuns),
  }
}

/** One node's reliability: its days, its runs in time, the groups that fail on it, and its co-failure events. */
export function nodeDetail(
  model: AgentModel,
  view: AgentView,
  options: AgentOptions,
  agent: AgentRow,
  history: AgentNodeHistory | null,
): Detail {
  const evidence = nodeEvidence(agent, options)
  const cells = agentCells(model, view, agent)
  const groups = agentLoadBy(agent._runs, (run) => run.group)
  const events = view.events.filter((event) => event.node_raw === agent.node_raw)
  return {
    id: `agent-node:${agent.node_raw}`,
    title: agent.node,
    subtitle: `${capitalized(evidence.title)} over ${options.windowId}; each run links to its Buildkite log`,
    stats: [
      ...(options.signal === 'all' ? [] : [{ label: signalColumnLabel(options.signal), value: cells.signal }]),
      { label: 'Test group failures', value: cells.failures, meta: cells.split ?? undefined },
      ...(history ? [{ label: 'Days with failures', value: integer(history.activeDays), meta: `of ${plural(history.cells.length, 'day')}` }] : []),
      { label: 'Co-failure events', value: cells.cofail },
      { label: 'Runs', value: cells.runs },
      ...(cells.failRate === 'Unavailable' ? [] : [{ label: 'Fail %', value: cells.failRate }]),
      { label: 'Groups', value: cells.groups },
    ],
    fields: [
      { label: 'Physical node', value: agent.node_raw, mono: true },
      { label: 'GPU', value: agent.hardware },
      { label: 'Canceled runs', value: integer(agent.canceled) },
    ],
    sources: SOURCES,
    sections: [
      ...(history ? [{ title: `${capitalized(signalNoun(options.signal))} per day`, meta: 'Select a day for its runs', component: NodeDays, props: { history, runs: agent._runs, options } }] : []),
      { title: 'Run timeline', meta: `${plural(agent._runs.length, 'retained run')}`, component: NodeTimeline, props: { runs: agent._runs, node: agent.node } },
    ],
    tables: [
      {
        title: 'Test groups failing on this node',
        meta: plural(groups.length, 'group'),
        columns: loadColumns('group', 'Test group', groups, (load) => text(load.key)),
        rows: groups,
        pageSize: 10,
        emptyText: 'No retained failing runs on this node in the selected window.',
      },
      ...(events.length ? [eventsTable(events, agent._runs)] : []),
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

/** One node on one UTC day: its counts from the accounting and the retained runs behind them. */
export function nodeDayDetail(history: AgentNodeHistory, day: string, runs: AgentRun[], options: Pick<AgentOptions, 'signal'>): Detail {
  const cell = history.cells.find((c) => c.day === day)
  const rows = newestFirst(runs.filter((run) => run.node_raw === history.node_raw && run.day === day))
  return {
    id: `agent-node-day:${history.node_raw}@${day}`,
    title: `${history.node} on ${dayName(day)}`,
    subtitle: `UTC day ${day}. Counts come from the compact failure accounting; the table lists the retained runs.`,
    stats: [
      { label: capitalized(signalNoun(options.signal)), value: integer(cell?.signal ?? 0) },
      { label: 'All hard failures', value: integer(cell?.hard ?? 0) },
      { label: 'All soft failures', value: integer(cell?.soft ?? 0) },
      { label: 'Co-failure events', value: integer(cell?.cofailEvents ?? 0) },
    ],
    fields: [
      { label: 'Physical node', value: history.node_raw, mono: true },
      { label: 'GPU', value: history.hardware },
      { label: 'Runs that day', value: integer(cell?.runs ?? 0) },
    ],
    sources: SOURCES,
    tables: [{ columns: runColumns(false), rows, emptyText: 'No failing runs are retained for this node on this day.', pageSize: 25 }],
    width: 'xl',
  }
}

/** A co-failure event: every run in the cluster with its timing. */
export function eventDetail(event: CofailEvent, nodeRuns: AgentRun[] = []): Detail {
  const card = cofailureCard(event, true)
  return {
    id: `agent-event:${event.node_raw}@${event._startMs}`,
    title: card.node,
    subtitle: `${card.pattern === 'concurrent' ? 'Concurrent' : 'Sequential'} co-failure · ${eventMeta(event)}`,
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
      { label: 'Physical node', value: event.node_raw, mono: true },
      { label: 'Started', value: shortDate(event.started_at) },
      { label: 'Test groups', value: integer(event.group_count) },
      { label: 'Pipelines', value: event.pipelines.join(', ') },
    ],
    notes: event.cross_pipeline ? [{ tone: 'neutral', text: 'The failures span more than one pipeline.' }] : [],
    sources: SOURCES,
    sections: nodeRuns.length
      ? [{ title: 'Node timeline around the event', meta: 'Zoom out for the rest of the window', component: NodeTimeline, props: { runs: nodeRuns, node: card.node, range: eventTimelineRange(event) } }]
      : [],
    tables: [{ title: 'Runs in this event', columns: runColumns(false), rows: event.runs, pageSize: false }],
    width: 'xl',
  }
}

/** The retained failing runs of one UTC day and the nodes they failed on. */
export function dayDetail(day: AgentDay, matrix: AgentNodeDayMatrix, runs: AgentRun[], options: AgentOptions): Detail {
  const signal = options.signal === 'all' ? 'Failures' : signalColumnLabel(options.signal)
  const at = matrix.days.indexOf(day.day)
  const nodes = at < 0 ? [] : matrix.nodes.filter((node) => node.cells[at]!.signal > 0)
  const max = Math.max(1, ...nodes.map((node) => node.cells[at]!.signal))
  return {
    id: `agent-day:${day.day}`,
    title: `${capitalized(signalNoun(options.signal))} on ${dayName(day.day)}`,
    subtitle: `UTC day ${day.day}. Counts come from the compact failure accounting; the tables list the nodes and the retained runs.`,
    stats: [
      { label: signal, value: integer(day.signal) },
      { label: 'Failing nodes', value: integer(day.failingNodes) },
      { label: 'All hard failures', value: integer(day.hard) },
      { label: 'All soft failures', value: integer(day.soft) },
    ],
    fields: [
      { label: 'Runs', value: integer(day.runs) },
      { label: 'Co-failure events', value: integer(day.cofailEvents) },
    ],
    sources: SOURCES,
    tables: [
      {
        title: 'Nodes failing on this day',
        meta: plural(nodes.length, 'node'),
        columns: [
          { id: 'node', label: 'Physical node', value: (node: AgentNodeHistory) => node.node_raw, cell: (node: AgentNodeHistory) => nodeCell(node.node_raw, node.hardware), hideable: false },
          { id: 'signal', label: signal, value: (node: AgentNodeHistory) => node.cells[at]!.signal, cell: (node: AgentNodeHistory) => countBar(node.cells[at]!.signal, max), numeric: true },
          { id: 'runs', label: 'Runs', value: (node: AgentNodeHistory) => node.cells[at]!.runs, numeric: true },
        ],
        rows: nodes,
        pageSize: 10,
        emptyText: `No node had ${signalNoun(options.signal)} on this day.`,
        onRowClick: (node: AgentNodeHistory) => nodeDayDetail(node, day.day, runs, options),
      },
      {
        title: 'Retained failing runs',
        columns: runColumns(true),
        rows: newestFirst(runs.filter((run) => run.day === day.day)),
        emptyText: 'No failing runs are retained for this day.',
        pageSize: 25,
      },
    ],
    width: 'xl',
  }
}

/** Retained signal failures on one queue or test group, by node, with the runs behind them. */
export function loadDetail(kind: 'queue' | 'group', load: AgentLoad, options: AgentOptions, node: (nodeRaw: string) => Detail | undefined): Detail {
  const nodes = agentLoadBy(load.runs, (run) => run.node_raw)
  const hardware = new Map(load.runs.map((run) => [run.node_raw, run.hardware]))
  const groups = kind === 'queue' ? agentLoadBy(load.runs, (run) => run.group) : []
  return {
    id: `agent-${kind}:${load.key}`,
    title: load.key,
    subtitle: `Retained ${signalNoun(options.signal)} ${kind === 'queue' ? 'on this queue' : 'of this test group'} in ${options.windowId}`,
    stats: [
      { label: capitalized(signalNoun(options.signal)), value: integer(load.failures) },
      { label: 'Nodes', value: integer(load.nodes) },
      { label: 'Hard', value: integer(load.hard) },
      { label: 'Soft', value: integer(load.soft) },
    ],
    sources: SOURCES,
    tables: [
      {
        title: 'Nodes',
        meta: plural(nodes.length, 'node'),
        columns: loadColumns('node', 'Physical node', nodes, (row) => nodeCell(row.key, hardware.get(row.key) ?? ''), [
          { id: 'groups', label: 'Groups', value: (row) => row.groups, numeric: true },
        ]),
        rows: nodes,
        pageSize: 10,
        onRowClick: (row: AgentLoad) => node(row.key),
      },
      ...(groups.length
        ? [{ title: 'Test groups', meta: plural(groups.length, 'group'), columns: loadColumns('group', 'Test group', groups, (row) => text(row.key)), rows: groups, pageSize: 10 as const }]
        : []),
      { title: 'Retained failing runs', columns: runColumns(true), rows: load.runs, pageSize: 25 },
    ],
    width: 'xl',
  }
}

/** Nodes with at least one failure matching the signal, each drilling into its node sheet. */
export function failingNodesDetail(options: AgentOptions, view: AgentView, signalText: (agent: AgentRow) => string, node: (agent: AgentRow) => Detail): Detail {
  const rows = view.agents.filter((agent) => agent.identified && agent.infra_suspect > 0).sort((a, b) => b.infra_suspect - a.infra_suspect)
  const label = options.signal === 'all' ? 'Test group failures' : signalColumnLabel(options.signal)
  const max = Math.max(1, ...rows.map((agent) => agent.infra_suspect))
  return {
    id: 'agent-failing-nodes',
    title: 'Nodes with failures',
    subtitle: `${integer(rows.length)} nodes with ${signalTerm(options.signal)} failures in ${options.windowId}; co-failure window ${agentCofailLabel(options.cofailMins)}`,
    sources: SOURCES,
    tables: [
      {
        columns: [
          { id: 'node', label: 'Physical node', value: (agent: AgentRow) => agent.node, cell: (agent: AgentRow) => nodeCell(agent.node, agent.hardware), hideable: false },
          {
            id: 'signal',
            label,
            value: (agent: AgentRow) => agent.infra_suspect,
            cell: (agent: AgentRow) => ({ ...countBar(agent.infra_suspect, max), text: signalText(agent) }),
            numeric: true,
          },
          { id: 'cofail', label: 'Co-failure events', value: (agent: AgentRow) => agent.cofailure_event_count, numeric: true },
        ],
        rows,
        pageSize: 25,
        onRowClick: node,
      },
    ],
    width: 'lg',
  }
}
