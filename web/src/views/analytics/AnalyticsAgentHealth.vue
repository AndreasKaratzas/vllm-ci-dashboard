<script setup lang="ts">
import { ChevronRight, RotateCcw, Search, ServerCrash, SlidersHorizontal, X, ZoomIn, ZoomOut } from '@lucide/vue'
import { refDebounced, useMediaQuery } from '@vueuse/core'
import { computed, h, nextTick, ref } from 'vue'
import AgentAbout from '@/components/analytics/agent-health/AgentAbout.vue'
import CofailureCard from '@/components/analytics/agent-health/CofailureCard.vue'
import { dayDetail, eventDetail, failingNodesDetail, nodeDetail, runDetail, runTone, stateLabel } from '@/components/analytics/agent-health/details'
import BarChart from '@/components/charts/BarChart.vue'
import { plainNumber, shortDay, type SeriesSpec } from '@/components/charts/chart'
import Legend from '@/components/charts/Legend.vue'
import Timeline, { type TimelineSegment } from '@/components/charts/Timeline.vue'
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
import { text, type ColumnSpec } from '@/components/table/columns'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
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
  agentHealthModel,
  agentHealthScopeValid,
  agentKpis,
  agentOptions,
  agentTableDescription,
  agentTimeline,
  clampTimelineRange,
  computeAgentView,
  eventKey,
  eventsSection,
  eventTimelineRange,
  nodeOptionLabel,
  resolveSelectedNode,
  signalCaption,
  signalColumnLabel,
  signalTerm,
  timelineAgents,
  zoomTimeline,
  type AgentDay,
  type AgentRow,
  type CofailEvent,
  type TimelineRange,
} from '@/domain/agentHealth'
import { duration, integer, shortDate, value, type Rec } from '@/domain/format'
import { statusVar } from '@/lib/palette'

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

// ---- KPIs with their daily history ----
const days = computed<AgentDay[]>(() => (model.value && view.value && options.value ? agentDailySeries(model.value, view.value, options.value) : []))
// The day the data was generated is still in progress, so trends end at the last full day.
const fullDays = computed(() => days.value.slice(0, -1))
const tableCard = ref<HTMLElement | null>(null)
const timelineCard = ref<HTMLElement | null>(null)
const eventsCard = ref<HTMLElement | null>(null)
const scrollTo = (target: { value: HTMLElement | null }) => target.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })

const KPI_LABELS: Record<string, string> = {
  'agent-nodes': 'Identified AMD nodes',
  'agent-unreliable': 'Nodes with failures',
  'agent-coverage': 'Node coverage',
  'agent-cofail': 'Co-failure events',
}
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
  const trends: Record<string, KpiTrend> = {
    'agent-nodes': kpiTrend(series((d) => d.nodes), 'up'),
    'agent-unreliable': kpiTrend(series((d) => d.failingNodes), 'down'),
    'agent-coverage': kpiTrend(series((d) => (d.runs ? (100 * d.identifiedRuns) / d.runs : null)), 'up', ' pts'),
    'agent-cofail': kpiTrend(series((d) => d.cofailEvents), 'down'),
  }
  const actions: Record<string, KpiAction> = {
    'agent-nodes': { label: 'Jump to the node table', onOpen: () => scrollTo(tableCard) },
    'agent-unreliable': { label: 'Open the failing nodes', onOpen: () => sheets.open(failingNodesDetail(m, v, o)) },
    'agent-cofail': { label: 'Jump to co-failure events', onOpen: () => scrollTo(eventsCard) },
  }
  return agentKpis(m, v, o).map((kpi): Kpi => {
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
})

// ---- Daily failures chart ----
const dayLabel = (day: AgentDay) => shortDay(Date.parse(`${day.day}T00:00:00Z`))
const FAIL_SERIES: SeriesSpec[] = [
  { key: 'signalHard', label: 'Hard fail', color: statusVar('hard') },
  { key: 'signalSoft', label: 'Soft fail', color: statusVar('soft') },
]
// Only states that occur are drawn and listed.
const daySeries = computed(() => FAIL_SERIES.filter((s) => days.value.some((day) => day[s.key as 'signalHard' | 'signalSoft'] > 0)))
function openDay(day: AgentDay) {
  if (view.value && options.value) sheets.open(dayDetail(day, view.value.fRuns, options.value))
}

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
    cell: (row) => text(pick(row)),
    numeric: true,
  })
  return [
    {
      id: 'node',
      label: 'Physical node',
      value: (row) => row.node,
      compare: textOrder((row) => row.node),
      cell: (row) => ({ text: value(row.node), mono: true, onOpen: () => focusNode(row.node_raw) }),
      hideable: false,
    },
    { id: 'hardware', label: 'GPU', value: (row) => value(row.hardware), compare: textOrder((row) => row.hardware) },
    count('runs', 'Runs', (row) => cells(row).runs),
    ...(failRateKnown.value ? [count('incident_rate', 'Fail %', (row) => cells(row).failRate)] : []),
    {
      id: 'failures',
      label: 'Test group failures',
      value: (row) => row.failures,
      cell: (row) => ({ text: cells(row).failures, sub: cells(row).split ?? undefined }),
      numeric: true,
    },
    ...(o.signal === 'all' ? [] : [count('infra_suspect', signalColumnLabel(o.signal), (row) => cells(row).signal)]),
    count('distinct_groups', 'Groups', (row) => cells(row).groups),
    count('cofailure_event_count', 'Co-fail', (row) => cells(row).cofail),
    {
      id: 'evidence',
      label: 'Evidence',
      value: (row) => row._runs.length,
      cell: (row) => (row._runs.length ? { text: `${row._runs.length} ${row._runs.length === 1 ? 'run' : 'runs'}`, onOpen: () => openNode(row) } : text('not retained')),
      sortable: false,
    },
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
  return `${integer(v.agents.length)} ${v.agents.length === 1 ? 'node' : 'nodes'} in ${o.windowId}, sorted by ${order}. Select a node for its runs.`
})
const defaultSort = computed(() => ({ id: options.value?.signal === 'all' ? 'failures' : 'infra_suspect', desc: true }))
const tableDescription = computed(() => (model.value && view.value && options.value ? agentTableDescription(model.value, view.value, options.value) : ''))

function openNode(agent: AgentRow) {
  if (!model.value || !view.value || !options.value) return
  selectNode(agent.node_raw, false)
  sheets.open(nodeDetail(model.value, view.value, options.value, agent))
}

// ---- Timeline ----
const selectedNode = computed(() => (view.value ? resolveSelectedNode(view.value, options.value?.selectedNode ?? '') : ''))
const nodeChoices = computed(() => (view.value ? timelineAgents(view.value) : []))
const timelineRange = ref<TimelineRange | null>(null)
const selectedAgent = computed(() => view.value?.agents.find((a) => a.node_raw === selectedNode.value) ?? null)
const timeline = computed(() => agentTimeline(selectedAgent.value ? selectedAgent.value._runs : [], timelineRange.value))
const segments = computed<TimelineSegment[]>(() =>
  timeline.value.rows.map((row, index) => ({
    id: String(index),
    row: row.label,
    start: row.start,
    end: row.end,
    color: statusVar(runTone(row.run.state)),
    title: row.run.group,
    details: [
      { label: 'State', value: stateLabel(row.run.state) },
      { label: 'Queue', value: value(row.run.queue) },
      { label: 'Build', value: `${value(row.run.pipeline)} #${value(row.run.build_number)}` },
      { label: 'Started', value: shortDate(row.run.started_at) },
      { label: 'Duration', value: duration(row.mins) },
    ],
  })),
)
const timelineLegend = computed(() =>
  FAIL_SERIES.filter((s) => timeline.value.rows.some((row) => runTone(row.run.state) === (s.key === 'signalHard' ? 'hard' : 'soft'))),
)
const MOBILE_RUNS = 25
// Below sm the lanes would be too narrow to read, so the same runs are listed newest first instead.
const wide = useMediaQuery('(min-width: 40rem)')
const openRun = (index: number) => timeline.value.rows[index] && sheets.open(runDetail(timeline.value.rows[index]!.run))

function selectNode(nodeRaw: string, scroll: boolean, keepZoom = false) {
  query.node.value = nodeRaw
  if (!keepZoom) timelineRange.value = null
  if (scroll) void nextTick(() => timelineCard.value?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
}
const nodeModel = computed({ get: () => selectedNode.value, set: (v: string) => selectNode(v, false) })
function zoomBy(factor: number) {
  timelineRange.value = zoomTimeline(timelineRange.value, timeline.value.bounds, factor)
}

// ---- Co-failure events ----
const eventNodeFilter = ref('')
const selectedEventKey = ref('')
const events = computed(() => (view.value && options.value ? eventsSection(view.value, options.value, eventNodeFilter.value) : null))
const eventsSummary = computed(() => {
  const o = options.value
  const section = events.value
  if (!o || !section || !view.value) return ''
  const count = section.scoped ? section.events.length : view.value.events.length
  const counted = `${integer(count)} ${count === 1 ? 'event' : 'events'}`
  return section.scoped
    ? `${counted} on this node in ${o.windowId}. Select one for its runs.`
    : `${counted} in ${o.windowId}: two or more ${signalTerm(o.signal)} failures on one node within ${agentCofailLabel(o.cofailMins)}. Select one for its runs.`
})
function focusNode(nodeRaw: string) {
  eventNodeFilter.value = nodeRaw
  selectNode(nodeRaw, true)
}
function openEvent(event: CofailEvent) {
  selectedEventKey.value = eventKey(event)
  selectNode(event.node_raw, false)
  timelineRange.value = eventTimelineRange(event)
  sheets.open(eventDetail(event))
}

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
          <div class="relative w-full sm:w-56">
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

        <Card v-if="view.agents.length" :class="['gap-4', ENTER]">
          <CardHeader>
            <CardTitle>{{ options.signal === 'all' ? 'All failures' : signalColumnLabel(options.signal) }} per day</CardTitle>
            <CardDescription>
              Compact failure accounting per UTC day{{ model.buildCreatedCohort ? ' of build creation' : '' }}. Select a day for its retained runs.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <BarChart
              :rows="days"
              :category="dayLabel"
              :series="daySeries"
              label="Failures per day"
              :value-format="plainNumber"
              legend
              @select="(day: AgentDay) => openDay(day)"
            />
          </CardContent>
        </Card>

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
                @row-click="openNode"
              />
            </CardContent>
          </Card>
        </div>

        <div ref="timelineCard" class="scroll-mt-20">
          <Card :class="['gap-4', ENTER]">
            <CardHeader>
              <CardTitle>Run timeline</CardTitle>
              <CardDescription data-testid="agent-signal-caption">{{ signalCaption(options.signal) }}</CardDescription>
            </CardHeader>
            <CardContent class="flex flex-col gap-3">
              <div class="flex flex-wrap items-center gap-2">
                <Select v-model="nodeModel" :disabled="!nodeChoices.length">
                  <SelectTrigger
                    size="sm"
                    class="h-auto min-h-8 w-full max-w-full text-left whitespace-normal sm:w-[30rem] *:data-[slot=select-value]:line-clamp-2"
                    aria-label="Physical node for run timeline"
                    data-testid="agent-node-select"
                  >
                    <SelectValue placeholder="No node with retained runs" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem v-for="agent in nodeChoices" :key="agent.node_raw" :value="agent.node_raw">{{ nodeOptionLabel(agent, options.signal) }}</SelectItem>
                  </SelectContent>
                </Select>
                <div class="hidden items-center gap-1 sm:flex">
                  <Button variant="outline" size="icon-sm" aria-label="Zoom out (widen the time window)" title="Zoom out" @click="zoomBy(1.6)"><ZoomOut /></Button>
                  <Button variant="outline" size="icon-sm" aria-label="Zoom in (narrow the time window)" title="Zoom in" @click="zoomBy(0.625)"><ZoomIn /></Button>
                  <Button variant="outline" size="sm" aria-label="Reset the timeline to the full window" @click="timelineRange = null"><RotateCcw /> Reset</Button>
                </div>
                <span class="hidden text-xs text-muted-foreground sm:inline">drag to select a range</span>
              </div>
              <div v-if="wide" class="flex flex-col gap-3">
                <Timeline
                  :segments="segments"
                  :x-domain="timeline.xMin !== null && timeline.xMax !== null ? [timeline.xMin, timeline.xMax] : undefined"
                  :label="`Failing runs on ${selectedAgent?.node ?? 'the selected node'}`"
                  hint="Click for the run"
                  @select="(segment: TimelineSegment) => openRun(Number(segment.id))"
                  @range="(min: number, max: number) => (timelineRange = clampTimelineRange(min, max, timeline.bounds))"
                >
                  <template #empty>
                    <EmptyState title="No retained failing runs for this node in the selected window and zoom." data-testid="agent-timeline-empty" />
                  </template>
                </Timeline>
                <Legend v-if="segments.length" :items="timelineLegend" />
              </div>
              <!-- Below sm the lanes would be too narrow to read, so the same runs are listed newest first. -->
              <ul v-if="timeline.rows.length" class="flex flex-col divide-y rounded-lg border sm:hidden" data-testid="agent-run-list">
                <li v-for="index in [...timeline.rows.keys()].reverse().slice(0, MOBILE_RUNS)" :key="index">
                  <button
                    type="button"
                    class="flex w-full items-center gap-3 px-3 py-2 text-left transition-colors outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 active:bg-accent/80"
                    @click="openRun(index)"
                  >
                    <span class="size-2 shrink-0 rounded-full" :style="{ backgroundColor: statusVar(runTone(timeline.rows[index]!.run.state)) }" aria-hidden="true" />
                    <span class="flex min-w-0 flex-1 flex-col">
                      <span class="truncate text-sm font-medium">{{ timeline.rows[index]!.run.group }}</span>
                      <span class="text-xs text-muted-foreground">
                        {{ stateLabel(timeline.rows[index]!.run.state) }} · {{ shortDate(timeline.rows[index]!.run.started_at) }} · {{ duration(timeline.rows[index]!.mins) }}
                      </span>
                    </span>
                    <ChevronRight class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  </button>
                </li>
                <li v-if="timeline.rows.length > MOBILE_RUNS" class="px-3 py-2 text-xs text-muted-foreground">
                  +{{ timeline.rows.length - MOBILE_RUNS }} older runs in the node's sheet
                </li>
              </ul>
              <EmptyState v-else title="No retained failing runs for this node in the selected window." />
            </CardContent>
          </Card>
        </div>

        <div ref="eventsCard" class="scroll-mt-20">
          <Card v-if="events" :class="['gap-4', ENTER]">
            <CardHeader>
              <CardTitle data-testid="agent-events-title">{{ events.title }}</CardTitle>
              <CardDescription data-testid="agent-events-summary">{{ eventsSummary }}</CardDescription>
              <CardAction class="flex items-center gap-1">
                <Button v-if="events.scoped" variant="outline" size="sm" aria-label="Clear the node filter on co-failure events" @click="eventNodeFilter = ''"><X /> All nodes</Button>
                <InfoPopover label="About co-failure events" title="Co-failure events">
                  <p data-testid="agent-events-description">{{ events.description }}</p>
                </InfoPopover>
              </CardAction>
            </CardHeader>
            <CardContent>
              <EmptyState v-if="!events.events.length" :title="events.empty" data-testid="agent-events-empty" />
              <div v-else class="grid grid-cols-1 items-start gap-3 *:min-w-0 @3xl/main:grid-cols-2 @7xl/main:grid-cols-3">
                <CofailureCard
                  v-for="event in events.events"
                  :key="eventKey(event)"
                  :event="event"
                  :selected="eventKey(event) === selectedEventKey"
                  @open="openEvent(event)"
                />
              </div>
            </CardContent>
          </Card>
        </div>
      </template>
    </AsyncBoundary>
    <SheetStack :sheets="sheets" />
  </div>
</template>
