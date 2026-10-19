<script setup lang="ts">
import type { ColumnDef } from '@tanstack/vue-table'
import { computed, h, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AgentTimelineChart from '@/components/analytics/agent-health/AgentTimelineChart.vue'
import CofailureCard from '@/components/analytics/agent-health/CofailureCard.vue'
import SegmentField from '@/components/analytics/agent-health/SegmentField.vue'
import DataTable from '@/components/ops/DataTable.vue'
import KpiCard from '@/components/ops/KpiCard.vue'
import TableSheet from '@/components/ops/TableSheet.vue'
import ViewState from '@/components/ops/ViewState.vue'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useOperationSections } from '@/composables/useOperations'
import {
  AGENT_UNAVAILABLE_TEXT,
  AMD_AGENT_HEALTH_ASSET,
  agentCells,
  agentCofailLabel,
  agentEmptyText,
  agentHealthModel,
  agentHealthScopeValid,
  agentKpis,
  agentOptions,
  agentTableDescription,
  agentTimeline,
  clampTimelineRange,
  computeAgentView,
  effectiveAgentSort,
  eventKey,
  eventsSection,
  eventTimelineRange,
  nextAgentSort,
  nodeEvidence,
  nodeOptionLabel,
  resolveSelectedNode,
  signalCaption,
  signalColumnLabel,
  signalTerm,
  sortedAgents,
  timelineAgents,
  zoomTimeline,
  type AgentRow,
  type AgentSort,
  type CofailEvent,
  type TimelineRange,
} from '@/domain/agentHealth'
import { age, shortDate, value, type Rec } from '@/domain/format'

const route = useRoute()
const router = useRouter()
const { data: ops, error, loading, retry } = useOperationSections(['amd_agent_health'])

const health = computed<Rec>(() => (ops.value || {}).amd_agent_health || {})
const valid = computed(() => agentHealthScopeValid(health.value))
const model = computed(() => (valid.value ? agentHealthModel(health.value) : null))

// Route state mirrors classic ?ops_agent_*; defaults stay out of the URL.
const DEFAULTS: Record<string, string> = {
  agent_window: '7d',
  agent_gpu: 'all',
  agent_node: '',
  agent_cofail: '180',
  agent_excl_cancel: '1',
  agent_nightly: '0',
  agent_signal: 'infra',
}
const q = (key: string) => (typeof route.query[key] === 'string' ? (route.query[key] as string) : null)
function setQuery(key: string, next: string) {
  void router.replace({ query: { ...route.query, [key]: next === DEFAULTS[key] || next === '' ? undefined : next } })
}

const searchInput = ref('')
const search = ref('')
let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(searchInput, (next) => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => (search.value = next.trim()), 200)
})

const options = computed(() =>
  model.value
    ? agentOptions(
        model.value,
        {
          window: q('agent_window'),
          gpu: q('agent_gpu'),
          node: q('agent_node'),
          cofail: q('agent_cofail'),
          exclCancel: q('agent_excl_cancel'),
          nightly: q('agent_nightly'),
          signal: q('agent_signal'),
        },
        search.value,
      )
    : null,
)
const view = computed(() => (model.value && options.value ? computeAgentView(model.value, options.value) : null))

const signal = computed({ get: () => options.value?.signal ?? 'infra', set: (v: string) => setQuery('agent_signal', v) })
const windowId = computed({ get: () => options.value?.windowId ?? '7d', set: (v: string) => setQuery('agent_window', v) })
const gpu = computed({ get: () => options.value?.gpu ?? 'all', set: (v: string) => setQuery('agent_gpu', v) })
const nightly = computed({ get: () => (options.value?.nightlyOnly ? '1' : '0'), set: (v: string) => setQuery('agent_nightly', v) })
const cancelled = computed({ get: () => (options.value?.excludeCancelled ? '1' : '0'), set: (v: string) => setQuery('agent_excl_cancel', v) })
const cofail = computed({ get: () => String(options.value?.cofailMins ?? 180), set: (v: string) => setQuery('agent_cofail', v) })

const windowItems = computed(() => (model.value?.windowOptions ?? []).map((d) => ({ id: d + 'd', label: d + 'd' })))
const gpuItems = computed(() => [{ id: 'all', label: 'All GPUs' }, ...(model.value?.hardwareTypes ?? []).map((hw) => ({ id: hw, label: hw }))])
const cofailItems = computed(() => (model.value?.cofailOptions ?? []).map((m) => ({ id: String(m), label: agentCofailLabel(m) })))

const kpis = computed(() => (model.value && view.value && options.value ? agentKpis(model.value, view.value, options.value) : []))
const KPI_TITLES: Record<string, string> = {
  'agent-nodes': 'Identified AMD nodes',
  'agent-unreliable': 'Nodes with failures',
  'agent-coverage': 'Node coverage',
  'agent-cofail': 'Co-failure events',
}
const KPI_BADGES: Record<string, Record<string, string>> = {
  'agent-unreliable': { soft: 'attention', pass: 'clear' },
  'agent-coverage': { pass: 'identified', soft: 'partial', hard: 'unavailable' },
  'agent-cofail': { hard: 'clustered', pass: 'none' },
}
const emptyText = computed(() => (model.value && view.value ? agentEmptyText(model.value, view.value) : null))

// Table: classic sort semantics (signal default until a header is chosen).
const sortState = ref<{ sort: AgentSort; explicit: boolean }>({ sort: { key: 'infra_suspect', dir: 'desc' }, explicit: false })
const sort = computed(() => effectiveAgentSort(sortState.value.sort, sortState.value.explicit, options.value?.signal ?? 'infra'))
function onSort(key: string) {
  sortState.value = { sort: nextAgentSort(sort.value, key), explicit: true }
}
const rows = computed(() => (view.value ? sortedAgents(view.value.agents, sort.value) : []))

function sortHeader(label: string, key: string, numeric = false) {
  return () => {
    const active = sort.value.key === key
    return h(
      'button',
      {
        type: 'button',
        class: 'inline-flex w-full items-center gap-1 hover:text-foreground' + (active ? ' text-foreground' : '') + (numeric ? ' justify-end' : ''),
        'aria-label': 'Sort by ' + label,
        'aria-sort': active ? (sort.value.dir === 'asc' ? 'ascending' : 'descending') : 'none',
        onClick: () => onSort(key),
      },
      label + (active ? (sort.value.dir === 'asc' ? ' ▲' : ' ▼') : ''),
    )
  }
}
const numericCell = (text: string) => h('span', { class: 'block text-right font-mono tabular-nums' }, text)

const columns = computed<ColumnDef<AgentRow, unknown>[]>(() => {
  const m = model.value
  const v = view.value
  const o = options.value
  if (!m || !v || !o) return []
  const cells = (row: AgentRow) => agentCells(m, v, row)
  const cols: ColumnDef<AgentRow, unknown>[] = [
    {
      id: 'Physical node',
      header: sortHeader('Physical node', 'node'),
      accessorFn: (row) => row.node + ' ' + row.hardware,
      cell: ({ row }) =>
        h(
          'button',
          {
            type: 'button',
            class: 'text-left font-mono text-xs font-medium hover:underline',
            title: 'Show this node in the timeline and co-failure events',
            'aria-label': 'Show ' + value(row.original.node) + ' in the timeline and co-failure events',
            onClick: () => focusNode(row.original.node_raw),
          },
          value(row.original.node),
        ),
      enableSorting: false,
      enableHiding: false,
    },
    { id: 'GPU', header: sortHeader('GPU', 'hardware'), accessorFn: (row) => value(row.hardware), enableSorting: false },
    { id: 'Runs', header: sortHeader('Runs', 'runs', true), accessorFn: (row) => cells(row).runs, cell: ({ getValue }) => numericCell(String(getValue())), enableSorting: false },
    { id: 'Fail %', header: sortHeader('Fail %', 'incident_rate', true), accessorFn: (row) => cells(row).failRate, cell: ({ getValue }) => numericCell(String(getValue())), enableSorting: false },
    {
      id: 'Test group failures',
      header: sortHeader('Test group failures', 'failures', true),
      accessorFn: (row) => cells(row).failures,
      cell: ({ row }) => {
        const c = cells(row.original)
        return h('span', { class: 'flex flex-col items-end' }, [
          h('span', { class: 'font-mono tabular-nums' }, c.failures),
          c.split ? h('span', { class: 'text-[11px] text-muted-foreground' }, c.split) : null,
        ])
      },
      enableSorting: false,
    },
  ]
  if (o.signal !== 'all') {
    const label = signalColumnLabel(o.signal)
    cols.push({ id: label, header: sortHeader(label, 'infra_suspect', true), accessorFn: (row) => cells(row).signal, cell: ({ getValue }) => numericCell(String(getValue())), enableSorting: false })
  }
  cols.push(
    { id: 'Groups', header: sortHeader('Groups', 'distinct_groups', true), accessorFn: (row) => cells(row).groups, cell: ({ getValue }) => numericCell(String(getValue())), enableSorting: false },
    { id: 'Co-fail', header: sortHeader('Co-fail', 'cofailure_event_count', true), accessorFn: (row) => cells(row).cofail, cell: ({ getValue }) => numericCell(String(getValue())), enableSorting: false },
    {
      id: 'Evidence',
      header: 'Evidence',
      cell: ({ row }) =>
        row.original._runs.length
          ? h(
              'button',
              {
                type: 'button',
                class: 'text-primary hover:underline',
                title: 'Open retained ' + signalTerm(o.signal) + ' failing runs and BuildKite logs for ' + value(row.original.node),
                'aria-label': 'Open retained ' + signalTerm(o.signal) + ' failing runs and BuildKite logs for ' + value(row.original.node),
                onClick: () => openNodeEvidence(row.original),
              },
              'runs ↗',
            )
          : h('span', { class: 'text-muted-foreground' }, 'not retained'),
      enableSorting: false,
    },
  )
  return cols
})
const tableDescription = computed(() => (model.value && view.value && options.value ? agentTableDescription(model.value, view.value, options.value) : ''))

// Timeline node and zoom.
const pickedNode = ref<string | null>(null)
const selectedNode = computed(() => (view.value ? resolveSelectedNode(view.value, pickedNode.value ?? options.value?.selectedNode ?? '') : ''))
const nodeChoices = computed(() => (view.value ? timelineAgents(view.value) : []))
const timelineRange = ref<TimelineRange | null>(null)
const selectedAgent = computed(() => view.value?.agents.find((a) => a.node_raw === selectedNode.value) ?? null)
const timeline = computed(() => agentTimeline(selectedAgent.value ? selectedAgent.value._runs : [], timelineRange.value))
const timelineCard = ref<HTMLElement | null>(null)
const eventsCard = ref<HTMLElement | null>(null)

function selectNode(nodeRaw: string, scroll: boolean, keepZoom = false) {
  pickedNode.value = nodeRaw
  setQuery('agent_node', nodeRaw)
  if (!keepZoom) timelineRange.value = null
  if (scroll) void nextTick(() => timelineCard.value?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
}
const nodeModel = computed({ get: () => selectedNode.value, set: (v: string) => selectNode(v, false) })
function zoomBy(factor: number) {
  timelineRange.value = zoomTimeline(timelineRange.value, timeline.value.bounds, factor)
}

// Co-failure events.
const eventNodeFilter = ref('')
const selectedEventKey = ref('')
const events = computed(() => (view.value && options.value ? eventsSection(view.value, options.value, eventNodeFilter.value) : null))
function focusNode(nodeRaw: string) {
  eventNodeFilter.value = nodeRaw
  selectNode(nodeRaw, true)
}
function selectEvent(event: CofailEvent, scroll: boolean) {
  selectedEventKey.value = eventKey(event)
  pickedNode.value = event.node_raw
  setQuery('agent_node', event.node_raw)
  timelineRange.value = eventTimelineRange(event)
  if (scroll) void nextTick(() => timelineCard.value?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
}

// Node evidence sheet.
const evidence = ref<{ open: boolean; title: string; subtitle: string; rows: Rec[] }>({ open: false, title: '', subtitle: '', rows: [] })
function openNodeEvidence(agent: AgentRow) {
  if (!options.value) return
  selectNode(agent.node_raw, false)
  const drawer = nodeEvidence(agent, options.value)
  evidence.value = { open: true, title: drawer.title, subtitle: drawer.subtitle, rows: drawer.rows }
}
const evidenceColumns: ColumnDef<Rec, unknown>[] = [
  {
    id: 'Observation',
    header: 'Observation',
    accessorFn: (row) => row.label,
    cell: ({ row }) =>
      row.original.url
        ? h('a', { href: row.original.url, target: '_blank', rel: 'noopener', class: 'font-mono text-xs font-medium hover:underline' }, row.original.label)
        : h('span', { class: 'font-mono text-xs' }, row.original.label),
    enableHiding: false,
  },
  { id: 'Observed', header: 'Observed', accessorFn: (row) => row.timestamp, cell: ({ row }) => shortDate(row.original.timestamp) },
  { id: 'Value', header: 'Value', accessorFn: (row) => row.valueSummary },
  { id: 'Queue', header: 'Queue', accessorFn: (row) => value(row.run.queue) },
  { id: 'Build', header: 'Build', accessorFn: (row) => '#' + value(row.run.build_number) },
  {
    id: 'Evidence',
    header: 'Evidence',
    cell: ({ row }) =>
      row.original.url
        ? h('a', { href: row.original.url, target: '_blank', rel: 'noopener', class: 'text-primary hover:underline' }, 'Open source')
        : h('a', { href: AMD_AGENT_HEALTH_ASSET, target: '_blank', rel: 'noopener', class: 'text-muted-foreground hover:underline' }, 'Open source data'),
    enableSorting: false,
  },
]

const NOTE = {
  info: 'border-info/40 bg-info/10 text-info',
  warning: 'border-warning/40 bg-warning/10 text-warning',
  neutral: 'border-border bg-muted/40 text-foreground',
}
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6" data-testid="analytics-agent-health">
    <p class="max-w-3xl text-sm text-muted-foreground">
      Current main CI AMD MI health, nightly outcomes, and recent test-group timing.
      <span v-if="health.generated_at" class="block whitespace-nowrap sm:inline" :title="health.generated_at">
        <span class="hidden sm:inline">· </span>Observed {{ shortDate(health.generated_at) }} - {{ age(health.generated_at) }}
      </span>
    </p>
    <ViewState :loading="loading && !ops" :error="error" @retry="retry">
      <p v-if="!valid" class="rounded-md border p-3 text-sm" :class="NOTE.warning" data-testid="agent-unavailable">{{ AGENT_UNAVAILABLE_TEXT }}</p>
      <template v-else-if="model && view && options">
        <div class="grid gap-3 @5xl/main:grid-cols-2">
          <div class="rounded-md border p-3 text-sm" :class="NOTE.info" data-testid="agent-note">
            <strong>AMD physical CI main agent health. </strong>
            <span>
              Terminal runs from CI main builds created in the selected UTC window on AMD MI GPU hardware are attributed to the physical node from its
              Buildkite agent tag. The timeline and co-failure clustering use the selected Failure signal: "infra-suspect" (the default — anomalous,
              node-attributable failures), all hard failures, or all failures. Toggle the signal, nightly subset, cancelled-job handling, and co-failure
              window; click any node or event to load its timeline.
            </span>
          </div>
          <div v-if="model.completionScopeNote" class="rounded-md border p-3 text-sm" :class="NOTE.info" data-testid="agent-completion-scope">
            {{ model.completionScopeNote }}
          </div>
          <div v-if="model.retentionNote" class="rounded-md border p-3 text-sm @5xl/main:col-span-2" :class="NOTE.warning" data-testid="agent-retention">
            <strong>{{ model.retentionNote.title }}</strong><span>{{ model.retentionNote.body }}</span>
          </div>
          <div v-if="options.signal === 'infra'" class="rounded-md border p-3 text-sm @5xl/main:col-span-2" :class="NOTE.neutral" data-testid="agent-criteria">
            <strong>What counts as an anomalous (infra-suspect) failure? </strong>
            <span>
              When the Failure signal is set to Infra-suspect (the default), a soft or hard failure is treated as node-attributable when its exact test
              group, on that same {{ model.criteria.dayLabel }}, both:
            </span>
            <ol class="my-1 list-decimal pl-6">
              <li>
                passed at least {{ model.criteria.pct }} of its gradable runs (needs ≥ {{ model.criteria.samples }} graded samples), so the group
                demonstrably works; and
              </li>
              <li>passed on at least one other physical node that day, so the failure is isolated to this box rather than a broad code break.</li>
            </ol>
            <span class="text-muted-foreground">
              Groups that fail broadly (real code bugs) and failures inside auto-cancelled / superseded builds are filtered out by default, isolating the
              signal that points at a specific machine.
            </span>
          </div>
        </div>

        <Card class="gap-4 py-4">
          <CardContent class="flex flex-col gap-4 px-4">
            <SegmentField
              v-model="signal"
              label="Failure signal"
              :items="[
                { id: 'infra', label: 'Infra-suspect failures' },
                { id: 'hard', label: 'Hard failures' },
                { id: 'all', label: 'All failures (hard + soft)' },
              ]"
            />
            <div class="flex flex-wrap items-end gap-x-6 gap-y-3" data-testid="agent-controls">
              <SegmentField v-model="windowId" label="Window" aria-label="Date range" :items="windowItems" />
              <SegmentField v-model="gpu" label="GPU" aria-label="GPU type" :items="gpuItems" />
              <SegmentField
                v-model="nightly"
                label="Scope"
                aria-label="Build scope"
                :items="[
                  { id: '0', label: 'All main builds' },
                  { id: '1', label: 'Nightlies' },
                ]"
              />
              <SegmentField
                v-model="cancelled"
                label="Cancelled"
                aria-label="Cancelled jobs"
                :items="[
                  { id: '1', label: 'Exclude' },
                  { id: '0', label: 'Include' },
                ]"
              />
              <SegmentField v-model="cofail" label="Co-failure window" :items="cofailItems" />
              <div class="flex flex-col gap-1.5">
                <span class="text-xs font-medium text-muted-foreground">Node</span>
                <Input v-model="searchInput" type="search" class="h-8 w-56" placeholder="Filter nodes by name or GPU type" aria-label="Filter nodes by name or GPU type" />
              </div>
            </div>
          </CardContent>
        </Card>

        <section class="grid grid-cols-1 gap-4 @xl/main:grid-cols-2 @5xl/main:grid-cols-4" aria-label="Agent health summary">
          <KpiCard
            v-for="kpi in kpis"
            :key="kpi.id"
            :data-testid="`kpi-${kpi.id}`"
            :label="KPI_TITLES[kpi.id]!"
            :value="kpi.value"
            :badge="kpi.value === 'Unavailable' ? undefined : KPI_BADGES[kpi.id]?.[kpi.tone]"
            :badge-tone="kpi.tone"
            :headline="kpi.id === 'agent-cofail' ? 'Jump to co-failure events' : undefined"
            :detail="kpi.meta"
            @open="eventsCard?.scrollIntoView({ behavior: 'smooth', block: 'start' })"
          />
        </section>

        <p v-if="emptyText" class="rounded-md border p-3 text-sm" :class="NOTE.warning" data-testid="agent-empty">{{ emptyText }}</p>

        <Card>
          <CardHeader>
            <CardTitle>AMD nodes by reliability</CardTitle>
            <CardDescription data-testid="agent-table-description">{{ tableDescription }}</CardDescription>
          </CardHeader>
          <CardContent>
            <DataTable
              :columns="columns"
              :data="rows"
              :page-size="15"
              search-placeholder="Filter table rows"
              :empty-text="`Per-node AMD reliability in ${options.windowId}. No matching observations.`"
            />
          </CardContent>
        </Card>

        <div ref="timelineCard">
          <Card>
            <CardHeader>
              <CardTitle>Per-node failure timeline</CardTitle>
              <CardDescription>
                Each bar is one failing run on the selected node (which failures are shown depends on the Failure signal toggle above); bars that
                overlap or cluster point to shared-node contention, an ephemeral host/network fault, or a node left in an unclean state. Click a bar to
                open its BuildKite log.
              </CardDescription>
            </CardHeader>
            <CardContent class="flex flex-col gap-3">
              <div class="flex flex-wrap items-center gap-3">
                <Select v-model="nodeModel" :disabled="!nodeChoices.length">
                  <SelectTrigger size="sm" class="w-[32rem] max-w-full" aria-label="Physical node for run timeline" data-testid="agent-node-select">
                    <SelectValue placeholder="No node with retained runs" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem v-for="agent in nodeChoices" :key="agent.node_raw" :value="agent.node_raw">{{ nodeOptionLabel(agent, options.signal) }}</SelectItem>
                  </SelectContent>
                </Select>
                <div class="flex items-center gap-1">
                  <span class="text-xs text-muted-foreground">Zoom</span>
                  <Button variant="outline" size="sm" aria-label="Zoom out (widen the time window)" title="Zoom out (widen the time window)" @click="zoomBy(1.6)">−</Button>
                  <Button variant="outline" size="sm" aria-label="Zoom in (narrow the time window)" title="Zoom in (narrow the time window)" @click="zoomBy(0.625)">+</Button>
                  <Button variant="outline" size="sm" aria-label="Reset the timeline to the full window" title="Reset the timeline to the full window" @click="timelineRange = null">
                    Reset
                  </Button>
                  <span class="text-xs text-muted-foreground">drag to select a range</span>
                </div>
              </div>
              <p class="text-sm text-muted-foreground" data-testid="agent-signal-caption">{{ signalCaption(options.signal) }}</p>
              <AgentTimelineChart
                :rows="timeline.rows"
                :x-min="timeline.xMin"
                :x-max="timeline.xMax"
                @select="(min: number, max: number) => (timelineRange = clampTimelineRange(min, max, timeline.bounds))"
              />
              <div class="flex gap-4 text-xs text-muted-foreground">
                <span class="inline-flex items-center gap-1.5"><span class="size-2.5 rounded-sm" style="background: #e3a63a" />Soft fail</span>
                <span class="inline-flex items-center gap-1.5"><span class="size-2.5 rounded-sm" style="background: #e06464" />Hard fail</span>
              </div>
            </CardContent>
          </Card>
        </div>

        <div ref="eventsCard">
          <Card v-if="events">
            <CardHeader>
              <CardTitle data-testid="agent-events-title">{{ events.title }}</CardTitle>
              <CardDescription data-testid="agent-events-description">{{ events.description }}</CardDescription>
              <CardAction v-if="events.scoped">
                <Button variant="outline" size="sm" aria-label="Clear the node filter on co-failure events" @click="eventNodeFilter = ''">Show all nodes ✕</Button>
              </CardAction>
            </CardHeader>
            <CardContent>
              <p v-if="!events.events.length" class="rounded-md border border-success/40 bg-success/10 p-3 text-sm text-success" data-testid="agent-events-empty">
                {{ events.empty }}
              </p>
              <div v-else class="grid items-start gap-3 @3xl/main:grid-cols-2 @7xl/main:grid-cols-3">
                <CofailureCard
                  v-for="event in events.events"
                  :key="eventKey(event)"
                  :event="event"
                  :selected="eventKey(event) === selectedEventKey"
                  @select="(scroll) => selectEvent(event, scroll)"
                />
              </div>
            </CardContent>
          </Card>
        </div>

        <TableSheet
          v-model:open="evidence.open"
          :title="evidence.title"
          :subtitle="evidence.subtitle"
          :columns="evidenceColumns"
          :rows="evidence.rows"
          search-placeholder="Filter observation, value, date, or source"
        />
      </template>
    </ViewState>
  </div>
</template>
