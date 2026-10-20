<script setup lang="ts">
import { ChevronDown, Search, ServerCrash, SlidersHorizontal } from '@lucide/vue'
import { refDebounced } from '@vueuse/core'
import { computed, h, ref, watch } from 'vue'
import AgentAbout from '@/components/analytics/agent-health/AgentAbout.vue'
import CofailureCard from '@/components/analytics/agent-health/CofailureCard.vue'
import {
  FAILURE_SERIES,
  dayDetail,
  eventDetail,
  failingNodesDetail,
  loadDetail,
  loadItems,
  nodeDayDetail,
  nodeDetail,
} from '@/components/analytics/agent-health/details'
import FleetVerdict from '@/components/analytics/agent-health/FleetVerdict.vue'
import { shortDay } from '@/components/charts/chart'
import RankedBars, { type RankedItem } from '@/components/charts/RankedBars.vue'
import Heatmap, { type HeatmapColumn, type HeatmapRow } from '@/components/charts/Heatmap.vue'
import SheetStack from '@/components/evidence/SheetStack.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import InfoPopover from '@/components/feedback/InfoPopover.vue'
import SegmentedSelect from '@/components/layout/SegmentedSelect.vue'
import KpiCard, { type KpiAction } from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import { kpiTrend, type KpiTrend } from '@/components/stat/trend'
import DataTable from '@/components/table/DataTable.vue'
import type { ColumnSpec } from '@/components/table/columns'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useOperationSections } from '@/composables/useOperations'
import { useQueryState } from '@/composables/useQueryState'
import { useSheetStack } from '@/composables/useSheetStack'
import { useSubviewHeader } from '@/composables/useSubviewHeader'
import {
  AGENT_UNAVAILABLE_TEXT,
  agentCells,
  agentCofailLabel,
  agentDailySeries,
  agentEmptyText,
  agentFleetVerdict,
  agentHealthModel,
  agentHealthScopeValid,
  agentKpis,
  agentLoadBy,
  agentNodeDayMatrix,
  agentOptions,
  agentTableDescription,
  computeAgentView,
  eventKey,
  eventsSection,
  signalCaption,
  signalColumnLabel,
  signalNoun,
  signalTerm,
  type AgentDay,
  type AgentLoad,
  type AgentNodeHistory,
  type AgentRow,
  type CofailEvent,
} from '@/domain/agentHealth'
import { integer, value, type Rec } from '@/domain/format'
import { hardwareColor, heatColor } from '@/lib/palette'

const { data: ops, error, loading, retry } = useOperationSections(['amd_agent_health'])
const sheets = useSheetStack()

const health = computed<Rec>(() => (ops.value || {}).amd_agent_health || {})
const valid = computed(() => agentHealthScopeValid(health.value))
const model = computed(() => (valid.value ? agentHealthModel(health.value) : null))

// The domain validates every raw value; defaults stay out of the URL.
const query = {
  window: useQueryState<string>('agent_window', { default: '7d' }),
  gpu: useQueryState<string>('agent_gpu', { default: 'all' }),
  node: useQueryState<string>('agent_node', { default: '' }),
  cofail: useQueryState<string>('agent_cofail', { default: '180' }),
  exclCancel: useQueryState<string>('agent_excl_cancel', { default: '1' }),
  nightly: useQueryState<string>('agent_nightly', { default: '0' }),
  signal: useQueryState<string>('agent_signal', { default: 'infra' }),
}
const searchInput = ref('')
const search = refDebounced(computed(() => searchInput.value.trim()), 200)

const options = computed(() =>
  model.value
    ? agentOptions(
        model.value,
        {
          window: query.window.value,
          gpu: query.gpu.value,
          node: query.node.value,
          cofail: query.cofail.value,
          exclCancel: query.exclCancel.value,
          nightly: query.nightly.value,
          signal: query.signal.value,
        },
        search.value,
      )
    : null,
)
const view = computed(() => (model.value && options.value ? computeAgentView(model.value, options.value) : null))

const signal = computed({ get: () => options.value?.signal ?? 'infra', set: (v: string) => (query.signal.value = v) })
const windowId = computed({ get: () => options.value?.windowId ?? '7d', set: (v: string) => (query.window.value = v) })
const gpu = computed({ get: () => options.value?.gpu ?? 'all', set: (v: string) => (query.gpu.value = v) })
const nightly = computed({ get: () => (options.value?.nightlyOnly ? '1' : '0'), set: (v: string) => (query.nightly.value = v) })
const cancelled = computed({ get: () => (options.value?.excludeCancelled ? '1' : '0'), set: (v: string) => (query.exclCancel.value = v) })
const cofail = computed({ get: () => String(options.value?.cofailMins ?? 180), set: (v: string) => (query.cofail.value = v) })

const SIGNALS = [
  { value: 'infra', label: 'Infra-suspect' },
  { value: 'hard', label: 'Hard' },
  { value: 'all', label: 'All', title: 'All (hard + soft)' },
]
const SCOPES = [
  { value: '0', label: 'All main builds' },
  { value: '1', label: 'Nightlies' },
]
const CANCELLED = [
  { value: '1', label: 'Exclude', title: 'Exclude cancelled builds' },
  { value: '0', label: 'Include', title: 'Include cancelled builds' },
]
const windowItems = computed(() => (model.value?.windowOptions ?? []).map((d) => ({ value: `${d}d`, label: `${d}d`, title: `Last ${d} days` })))
const gpuItems = computed(() => [{ value: 'all', label: 'All GPUs' }, ...(model.value?.hardwareTypes ?? []).map((hw) => ({ value: hw, label: hw }))])
const cofailItems = computed(() => (model.value?.cofailOptions ?? []).map((m) => ({ value: String(m), label: agentCofailLabel(m) })))
// GPU, build scope, cancelled builds and co-failure window live behind one Filters button.
const activeFilters = computed(() => [gpu.value !== 'all', nightly.value !== '0', cancelled.value !== '1', cofail.value !== '180'].filter(Boolean).length)

useSubviewHeader(() => {
  const current = model.value
  return current ? { about: () => h(AgentAbout, { model: current }) } : {}
})

// ---- Per-day and per-node-day accounting ----
const days = computed<AgentDay[]>(() => (model.value && view.value && options.value ? agentDailySeries(model.value, view.value, options.value) : []))
const matrix = computed(() => (model.value && view.value && options.value ? agentNodeDayMatrix(model.value, view.value, options.value) : null))
const verdict = computed(() => (matrix.value && view.value && options.value ? agentFleetVerdict(matrix.value, view.value, options.value) : null))
const queues = computed(() => (view.value ? agentLoadBy(view.value.fRuns, (run) => run.queue) : []))
const groups = computed(() => (view.value ? agentLoadBy(view.value.fRuns, (run) => run.group) : []))
const agentsByNode = computed(() => new Map((view.value?.agents ?? []).map((agent) => [agent.node_raw, agent])))
const historyByNode = computed(() => new Map((matrix.value?.nodes ?? []).map((node) => [node.node_raw, node])))

// ---- Drill-downs ----
const noun = computed(() => signalNoun(options.value?.signal ?? 'infra'))
function nodeSheet(agent: AgentRow) {
  return nodeDetail(model.value!, view.value!, options.value!, agent, historyByNode.value.get(agent.node_raw) ?? null)
}
function openNode(agent: AgentRow) {
  if (!model.value || !view.value || !options.value) return
  query.node.value = agent.node_raw
  sheets.open(nodeSheet(agent))
}
const openNodeRaw = (nodeRaw: string) => {
  const agent = agentsByNode.value.get(nodeRaw)
  if (agent) openNode(agent)
}
const nodeSheetRaw = (nodeRaw: string) => {
  const agent = agentsByNode.value.get(nodeRaw)
  return agent ? nodeSheet(agent) : undefined
}
function openDay(index: number) {
  const day = days.value[index]
  if (day && matrix.value && view.value && options.value) sheets.open(dayDetail(day, matrix.value, view.value.fRuns, options.value))
}
function openLoad(kind: 'queue' | 'group', load: AgentLoad) {
  if (options.value) sheets.open(loadDetail(kind, load, options.value, nodeSheetRaw))
}
function openEvent(event: CofailEvent) {
  selectedEventKey.value = eventKey(event)
  sheets.open(eventDetail(event, agentsByNode.value.get(event.node_raw)?._runs))
}
// agent_node names the node whose sheet is open, so a link reopens it; closing every sheet clears it.
watch(
  () => [view.value, query.node.value] as const,
  ([current, node]) => {
    if (current && node && !sheets.stack.value.length && agentsByNode.value.has(node)) openNode(agentsByNode.value.get(node)!)
  },
  { immediate: true },
)
watch(
  () => sheets.stack.value.length,
  (length) => {
    if (!length) query.node.value = ''
  },
)

// ---- KPIs with their daily history ----
const fullDays = computed(() => days.value.slice(0, -1))
const heatCard = ref<HTMLElement | null>(null)
const tableCard = ref<HTMLElement | null>(null)
const eventsCard = ref<HTMLElement | null>(null)
const scrollTo = (target: { value: HTMLElement | null }) => target.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })

const KPI_LABELS: Record<string, string> = {
  'agent-nodes': 'Identified AMD nodes',
  'agent-unreliable': 'Nodes with failures',
  'agent-coverage': 'Node coverage',
  'agent-cofail': 'Co-failure events',
}
const KPI_ORDER = ['agent-unreliable', 'agent-cofail', 'agent-nodes', 'agent-coverage']
interface Kpi {
  id: string
  label: string
  value: number | string | null
  empty?: string
  footnote: string
  trend: KpiTrend
  action?: KpiAction
}
const asNumber = (raw: string) => (/^[\d,]+$/.test(raw) ? Number(raw.replace(/,/g, '')) : raw)
const kpis = computed(() => {
  const m = model.value
  const v = view.value
  const o = options.value
  if (!m || !v || !o) return []
  const series = (pick: (day: AgentDay) => number | null) => fullDays.value.map(pick)
  // Fleet size moves with scheduling, so its change is shown without a good or bad direction.
  const trends: Record<string, KpiTrend> = {
    'agent-nodes': kpiTrend(series((d) => d.nodes)),
    'agent-unreliable': kpiTrend(series((d) => d.failingNodes), 'down'),
    'agent-coverage': kpiTrend(series((d) => (d.runs ? (100 * d.identifiedRuns) / d.runs : null)), 'up', ' pts'),
    'agent-cofail': kpiTrend(series((d) => d.cofailEvents), 'down'),
  }
  const actions: Record<string, KpiAction> = {
    'agent-nodes': { label: 'Jump to the node table', onOpen: () => scrollTo(tableCard) },
    'agent-unreliable': {
      label: 'Open the failing nodes',
      onOpen: () => sheets.open(failingNodesDetail(o, v, (agent) => agentCells(m, v, agent).signal, (agent) => nodeSheet(agent))),
    },
    'agent-coverage': { label: 'See failures by node and day', onOpen: () => scrollTo(heatCard) },
    'agent-cofail': { label: 'Jump to co-failure events', onOpen: () => scrollTo(eventsCard) },
  }
  return agentKpis(m, v, o)
    .map((kpi): Kpi => {
      const available = kpi.value !== 'Unavailable'
      return {
        id: kpi.id,
        label: KPI_LABELS[kpi.id]!,
        value: available ? asNumber(kpi.value) : null,
        empty: available ? undefined : 'Incomplete history',
        footnote: kpi.meta,
        trend: available ? trends[kpi.id]! : {},
        action: actions[kpi.id],
      }
    })
    .sort((a, b) => KPI_ORDER.indexOf(a.id) - KPI_ORDER.indexOf(b.id))
})

// ---- Node x day heatmap ----
const TOP_NODES = 12
const showAllNodes = ref(false)
const dayOfMonth = (day: string) => String(Number(day.slice(8, 10)))
const heatColumns = computed<HeatmapColumn[]>(() =>
  (matrix.value?.days ?? []).map((day, index, all) => {
    const name = shortDay(Date.parse(`${day}T00:00:00Z`))
    // Long windows label every other day; the corner names the months.
    const labelled = all.length <= 31 || index % 2 === 0 || index === all.length - 1
    return { id: day, label: labelled ? dayOfMonth(day) : '', title: name }
  }),
)
const heatSpan = computed(() => {
  const days = matrix.value?.days ?? []
  const name = (day: string) => shortDay(Date.parse(`${day}T00:00:00Z`))
  return days.length ? `${name(days[0]!)} – ${name(days.at(-1)!)}` : ''
})
const heatRow = (node: AgentNodeHistory): HeatmapRow => ({
  id: node.node_raw,
  label: node.node_raw,
  dot: hardwareColor(node.hardware),
  meta: [
    value(node.hardware, ''),
    `${node.activeDays} ${node.activeDays === 1 ? 'day' : 'days'}`,
    node.cofailEvents ? `${node.cofailEvents} co-fail` : '',
  ]
    .filter(Boolean)
    .join(' · '),
  cells: node.cells.map((cell) => ({
    value: cell.signal,
    marker: cell.cofailEvents > 0,
    rows: [
      { label: 'Hard', value: cell.signalHard },
      { label: 'Soft', value: cell.signalSoft },
      { label: 'Runs that day', value: cell.runs },
    ],
  })),
  total: node.signal,
  segments: [
    { value: node.signalHard, color: FAILURE_SERIES[0]!.color, label: 'Hard' },
    { value: node.signalSoft, color: FAILURE_SERIES[1]!.color, label: 'Soft' },
  ],
})
const heatRows = computed(() => (matrix.value?.nodes ?? []).slice(0, showAllNodes.value ? undefined : TOP_NODES).map(heatRow))
const fleetRow = computed<HeatmapRow>(() => ({
  id: 'fleet',
  label: 'All nodes',
  cells: days.value.map((day) => ({
    value: day.signal,
    rows: [
      { label: 'Failing nodes', value: day.failingNodes },
      { label: 'Runs', value: day.runs },
    ],
  })),
  total: days.value.reduce((sum, day) => sum + day.signal, 0),
}))
function openCell(row: HeatmapRow, index: number) {
  const history = historyByNode.value.get(row.id)
  const day = matrix.value?.days[index]
  if (row.id === 'fleet') openDay(index)
  else if (history && day && view.value && options.value) sheets.open(nodeDayDetail(history, day, view.value.fRuns, options.value))
}

// ---- Queues and test groups ----
const LOAD_ROWS = 8

// ---- Node table ----
const textOrder = (pick: (row: AgentRow) => unknown) => (a: AgentRow, b: AgentRow) => {
  const x = String(pick(a) || '').toLowerCase()
  const y = String(pick(b) || '').toLowerCase()
  return x < y ? -1 : x > y ? 1 : 0
}
const columns = computed<ColumnSpec<AgentRow>[]>(() => {
  const m = model.value
  const v = view.value
  const o = options.value
  if (!m || !v || !o) return []
  const cells = (row: AgentRow) => agentCells(m, v, row)
  const count = (id: keyof AgentRow, label: string, pick: (row: AgentRow) => string): ColumnSpec<AgentRow> => ({
    id,
    label,
    value: (row) => Number(row[id]) || 0,
    cell: (row) => ({ text: pick(row), muted: !Number(row[id]) }),
    numeric: true,
  })
  const signalMax = Math.max(1, ...v.agents.map((row) => row.infra_suspect))
  const failureMax = Math.max(1, ...v.agents.map((row) => row.failures))
  // The ranking column carries an inline bar so the worst nodes read at a glance.
  const ranked = o.signal === 'all' ? 'failures' : 'infra_suspect'
  const bar = (id: 'failures' | 'infra_suspect', shown: string) => (row: AgentRow) => ({
    text: shown,
    muted: !row[id],
    ...(id === ranked && row[id] > 0 ? { bar: { value: row[id], max: id === 'failures' ? failureMax : signalMax, color: heatColor(4) } } : {}),
  })
  return [
    {
      id: 'node',
      label: 'Physical node',
      value: (row) => row.node,
      compare: textOrder((row) => row.node),
      cell: (row) => ({ text: row.node_raw, mono: true, nowrap: true, dots: row.identified ? [hardwareColor(row.hardware)] : undefined }),
      hideable: false,
    },
    { id: 'hardware', label: 'GPU', value: (row) => value(row.hardware), compare: textOrder((row) => row.hardware), class: 'hidden md:table-cell' },
    { ...count('runs', 'Runs', (row) => cells(row).runs), class: 'hidden md:table-cell' },
    ...(failRateKnown.value ? [{ ...count('incident_rate', 'Fail %', (row) => cells(row).failRate), class: 'hidden md:table-cell' }] : []),
    {
      id: 'failures',
      label: 'Test group failures',
      value: (row) => row.failures,
      cell: (row) => ({ ...bar('failures', cells(row).failures)(row), sub: cells(row).split ?? undefined }),
      numeric: true,
      class: o.signal === 'all' ? undefined : 'hidden sm:table-cell',
    },
    ...(o.signal === 'all'
      ? []
      : [{ id: 'infra_suspect', label: signalColumnLabel(o.signal), value: (row: AgentRow) => row.infra_suspect, cell: (row: AgentRow) => bar('infra_suspect', cells(row).signal)(row), numeric: true }]),
    { ...count('distinct_groups', 'Groups', (row) => cells(row).groups), class: 'hidden lg:table-cell' },
    count('cofailure_event_count', 'Co-fail', (row) => cells(row).cofail),
  ]
})
// Without node-specific accounting every Fail % cell would read "Unavailable", so the column is left out.
const failRateKnown = computed(() => {
  const m = model.value
  const v = view.value
  return !!m && !!v && v.agents.some((row) => agentCells(m, v, row).failRate !== 'Unavailable')
})
const tableSummary = computed(() => {
  const v = view.value
  const o = options.value
  if (!v || !o) return ''
  const order = o.signal === 'all' ? 'test group failures' : `${signalTerm(o.signal)} failures`
  return `${integer(v.agents.length)} ${v.agents.length === 1 ? 'node' : 'nodes'} in ${o.windowId}, sorted by ${order}. Select a node for its days, runs and co-failures.`
})
const defaultSort = computed(() => ({ id: options.value?.signal === 'all' ? 'failures' : 'infra_suspect', desc: true }))
const tableDescription = computed(() => (model.value && view.value && options.value ? agentTableDescription(model.value, view.value, options.value) : ''))

// ---- Co-failure events ----
const EVENTS_SHOWN = 6
const showAllEvents = ref(false)
const selectedEventKey = ref('')
const events = computed(() => (view.value && options.value ? eventsSection(view.value, options.value, '') : null))
const shownEvents = computed(() => (events.value?.events ?? []).slice(0, showAllEvents.value ? undefined : EVENTS_SHOWN))
const eventsSummary = computed(() => {
  const o = options.value
  if (!o || !view.value) return ''
  const count = view.value.events.length
  return `${integer(count)} ${count === 1 ? 'event' : 'events'} in ${o.windowId}: two or more ${signalTerm(o.signal)} failures on one node within ${agentCofailLabel(o.cofailMins)}. Select one for its runs.`
})

const emptyText = computed(() => (model.value && view.value ? agentEmptyText(model.value, view.value) : null))
const ENTER = 'motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300'
</script>

<template>
  <div class="flex min-w-0 flex-col gap-4 md:gap-6" data-testid="analytics-agent-health">
    <AsyncBoundary :loading="loading && !ops" :error="error" @retry="retry">
      <EmptyState v-if="!valid" :title="AGENT_UNAVAILABLE_TEXT" :icon="ServerCrash" data-testid="agent-unavailable" />
      <template v-else-if="model && view && options">
        <div class="grid grid-cols-2 items-end gap-x-4 gap-y-3 sm:flex sm:flex-wrap" data-testid="agent-controls">
          <SegmentedSelect v-model="signal" label="Failure signal" :items="SIGNALS" />
          <SegmentedSelect v-model="windowId" label="Window" :items="windowItems" />
          <Popover>
            <PopoverTrigger as-child>
              <Button variant="outline" size="sm" class="w-full sm:w-auto" data-testid="agent-filters">
                <SlidersHorizontal /> Filters
                <Badge v-if="activeFilters" variant="secondary" class="h-5 min-w-5 rounded-full px-1.5 tabular-nums">{{ activeFilters }}</Badge>
              </Button>
            </PopoverTrigger>
            <PopoverContent align="start" class="flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-4">
              <SegmentedSelect v-model="gpu" label="GPU" :items="gpuItems" />
              <SegmentedSelect v-model="nightly" label="Build scope" :items="SCOPES" />
              <SegmentedSelect v-model="cancelled" label="Cancelled builds" :items="CANCELLED" />
              <SegmentedSelect v-model="cofail" label="Co-failure window" :items="cofailItems" />
            </PopoverContent>
          </Popover>
          <div class="relative w-full sm:ml-auto sm:w-56">
            <Search class="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input v-model="searchInput" type="search" class="h-8 pl-8" placeholder="Node or GPU" aria-label="Filter nodes by name or GPU type" />
          </div>
        </div>

        <Callout v-if="model.retentionNote" tone="soft" :title="model.retentionNote.title.trim()" data-testid="agent-retention">
          Counts outside the complete interval are retained-evidence lower bounds.
          <template #action>
            <InfoPopover label="Retention details" title="Retained history">{{ model.retentionNote.body }}</InfoPopover>
          </template>
        </Callout>

        <FleetVerdict
          v-if="verdict && matrix && view.agents.length"
          :verdict="verdict"
          :matrix="matrix"
          :queues="queues"
          :signal="options.signal"
          :window-id="options.windowId"
          :trend="days.map((day) => day.signal)"
          :span="heatSpan"
          @node="(node: AgentNodeHistory) => openNodeRaw(node.node_raw)"
          @queue="(load: AgentLoad) => openLoad('queue', load)"
        />

        <StatGrid :cols="4">
          <KpiCard
            v-for="(kpi, index) in kpis"
            :key="kpi.id"
            :data-testid="`kpi-${kpi.id}`"
            :style="{ animationDelay: `${index * 30}ms` }"
            :label="kpi.label"
            :value="kpi.value"
            :empty="kpi.empty"
            :footnote="kpi.footnote"
            :spark="kpi.trend.spark"
            :delta="kpi.trend.delta"
            :action="kpi.action"
          />
        </StatGrid>

        <EmptyState v-if="emptyText" :title="emptyText" :icon="ServerCrash" data-testid="agent-empty" />

        <div v-if="view.agents.length && matrix" ref="heatCard" class="scroll-mt-20">
          <Card :class="['gap-4', ENTER]" data-testid="agent-heatmap-card">
            <CardHeader>
              <CardTitle>{{ noun.charAt(0).toUpperCase() + noun.slice(1) }} by node and day</CardTitle>
              <CardDescription data-testid="agent-signal-caption">{{ signalCaption(options.signal) }}</CardDescription>
              <CardAction>
                <InfoPopover label="About the node and day grid" title="Reading the grid">
                  <p>
                    Each row is a physical node, worst first by {{ noun }} in {{ options.windowId }}; each column is a UTC day{{ model.buildCreatedCohort ? ' of build creation' : '' }}.
                    Darker cells hold more failures. The top row adds up every node on its own scale. A ringed cell had a co-failure event that day.
                  </p>
                </InfoPopover>
              </CardAction>
            </CardHeader>
            <CardContent class="flex flex-col gap-3">
              <EmptyState v-if="!matrix.nodes.length" :title="`No node had ${noun} in ${options.windowId}.`" data-testid="agent-heatmap-empty" />
              <Heatmap
                v-else
                :columns="heatColumns"
                :rows="heatRows"
                :summary="fleetRow"
                :label="`${noun} by node and UTC day`"
                :unit="[noun.replace(/failures$/, 'failure'), noun]"
                :total-label="options.windowId"
                :corner="heatSpan"
                marker-label="Co-failure event"
                row-action="open the node"
                cell-action="open the runs"
                :selected="query.node.value"
                data-testid="agent-heatmap"
                @cell="openCell"
                @row="(row: HeatmapRow) => openNodeRaw(row.id)"
                @column="openDay"
              />
              <Button
                v-if="matrix.nodes.length > TOP_NODES"
                variant="ghost"
                size="sm"
                class="self-start text-muted-foreground"
                :aria-expanded="showAllNodes"
                data-testid="agent-heatmap-more"
                @click="showAllNodes = !showAllNodes"
              >
                <ChevronDown class="transition-transform" :class="showAllNodes && 'rotate-180'" />
                {{ showAllNodes ? `Show the top ${TOP_NODES}` : `Show all ${integer(matrix.nodes.length)} failing nodes` }}
              </Button>
            </CardContent>
          </Card>
        </div>

        <div v-if="view.agents.length && (queues.length || groups.length)" class="grid grid-cols-1 items-start gap-4 *:min-w-0 md:gap-6 @4xl/main:grid-cols-2">
          <Card :class="['gap-4', ENTER]">
            <CardHeader>
              <CardTitle>Failures by queue</CardTitle>
              <CardDescription>Retained {{ noun }} per Buildkite queue in {{ options.windowId }}. Select a queue for its nodes and runs.</CardDescription>
            </CardHeader>
            <CardContent>
              <RankedBars
                :items="loadItems(queues.slice(0, LOAD_ROWS), 'queue')"
                label="Failures by queue"
                mono
                action="open the queue's nodes and runs"
                data-testid="agent-queues"
                @select="(item: RankedItem) => openLoad('queue', queues.find((q) => q.key === item.key)!)"
              />
            </CardContent>
          </Card>
          <Card :class="['gap-4', ENTER]">
            <CardHeader>
              <CardTitle>Test groups hit most</CardTitle>
              <CardDescription>Retained {{ noun }} per test group in {{ options.windowId }}. Select a group for the nodes it failed on.</CardDescription>
            </CardHeader>
            <CardContent>
              <RankedBars
                :items="loadItems(groups.slice(0, LOAD_ROWS), 'group')"
                label="Failures by test group"
                action="open the nodes it failed on"
                data-testid="agent-groups"
                @select="(item: RankedItem) => openLoad('group', groups.find((g) => g.key === item.key)!)"
              />
            </CardContent>
          </Card>
        </div>

        <div ref="tableCard" class="scroll-mt-20">
          <Card :class="['gap-4', ENTER]">
            <CardHeader>
              <CardTitle>AMD nodes by reliability</CardTitle>
              <CardDescription data-testid="agent-table-summary">{{ tableSummary }}</CardDescription>
              <CardAction>
                <InfoPopover label="About the node table" title="How the node table is counted">
                  <p data-testid="agent-table-description">{{ tableDescription }}</p>
                </InfoPopover>
              </CardAction>
            </CardHeader>
            <CardContent>
              <DataTable
                :key="`${options.signal}:${failRateKnown}`"
                :columns="columns"
                :rows="view.agents"
                :default-sort="defaultSort"
                :searchable="false"
                :page-size="15"
                label="AMD nodes by reliability"
                :empty-text="`Per-node AMD reliability in ${options.windowId}. No matching observations.`"
                @row-click="(agent: AgentRow) => openNode(agent)"
              />
            </CardContent>
          </Card>
        </div>

        <div ref="eventsCard" class="scroll-mt-20">
          <Card v-if="events" :class="['gap-4', ENTER]">
            <CardHeader>
              <CardTitle data-testid="agent-events-title">{{ events.title }}</CardTitle>
              <CardDescription data-testid="agent-events-summary">{{ eventsSummary }}</CardDescription>
              <CardAction>
                <InfoPopover label="About co-failure events" title="Co-failure events">
                  <p data-testid="agent-events-description">{{ events.description }}</p>
                </InfoPopover>
              </CardAction>
            </CardHeader>
            <CardContent class="flex flex-col gap-3">
              <EmptyState v-if="!events.events.length" :title="events.empty" data-testid="agent-events-empty" />
              <div v-else class="grid grid-cols-1 items-start gap-3 *:min-w-0 @3xl/main:grid-cols-2 @7xl/main:grid-cols-3">
                <CofailureCard
                  v-for="event in shownEvents"
                  :key="eventKey(event)"
                  :event="event"
                  :selected="eventKey(event) === selectedEventKey"
                  @open="openEvent(event)"
                />
              </div>
              <Button
                v-if="events.events.length > EVENTS_SHOWN"
                variant="ghost"
                size="sm"
                class="self-start text-muted-foreground"
                :aria-expanded="showAllEvents"
                data-testid="agent-events-more"
                @click="showAllEvents = !showAllEvents"
              >
                <ChevronDown class="transition-transform" :class="showAllEvents && 'rotate-180'" />
                {{ showAllEvents ? 'Show fewer events' : `Show all ${integer(events.events.length)} events` }}
              </Button>
            </CardContent>
          </Card>
        </div>
      </template>
    </AsyncBoundary>
    <SheetStack :sheets="sheets" />
  </div>
</template>
