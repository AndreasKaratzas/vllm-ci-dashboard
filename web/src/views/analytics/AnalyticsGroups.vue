<script setup lang="ts">
import type { ColumnDef } from '@tanstack/vue-table'
import { computed, h, ref } from 'vue'
import AmdCatalogSheet from '@/components/analytics/AmdCatalogSheet.vue'
import ChartEvidenceSheet, { type EvidencePoint } from '@/components/analytics/ChartEvidenceSheet.vue'
import DetailSheet, { type DetailRequest } from '@/components/analytics/DetailSheet.vue'
import LogicalGroupSheet from '@/components/analytics/LogicalGroupSheet.vue'
import StackedBarChart, { type ChartSeries } from '@/components/analytics/StackedBarChart.vue'
import StatCard from '@/components/analytics/StatCard.vue'
import AmdGroupSheet from '@/components/health/AmdGroupSheet.vue'
import DataTable from '@/components/ops/DataTable.vue'
import TableSheet from '@/components/ops/TableSheet.vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import ViewState from '@/components/ops/ViewState.vue'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useOperationSections } from '@/composables/useOperations'
import {
  AMD_TEST_HEALTH_ASSET,
  OPERATIONS_ASSET,
  amdHealthModel,
  analyticsObservedAt,
  hardwareValueSummary,
  passRateLabel,
  type AmdCluster,
  type CatalogRequest,
  type StripItem,
} from '@/domain/analytics'
import {
  amdLatestState,
  amdLogicalSignalLabel,
  amdLogicalSignalTone,
  amdLogicalStateLabel,
  amdLogicalStateTone,
  amdStateLabel,
  sortLogicalRows,
} from '@/domain/ciHealth'
import { age, integer, percent, shortDate, toneForState, value, type Rec, type Tone } from '@/domain/format'
import { currentAmdHealth, hardwareDisplayLabel } from '@/domain/health'

const { data: ops, error, loading, retry } = useOperationSections(['amd_test_health'])

const health = computed(() => currentAmdHealth(ops.value?.amd_test_health))
const model = computed(() => amdHealthModel(health.value))
const observedAt = computed(() => (ops.value ? analyticsObservedAt('groups', ops.value, health.value) : null))
const counts = computed<Rec>(() => (health.value.summary || {}).latest_test_group_counts || {})

const SEVERITY_BADGE: Partial<Record<Tone, string>> = { 'is-success': 'Passing', 'is-warning': 'Soft fail', 'is-danger': 'Hard fail' }
function badgeFor(item: StripItem): string | undefined {
  if (item.id === 'amd-health-build' || item.id === 'amd-health-incidents') return SEVERITY_BADGE[item.tone as Tone]
  if (item.id === 'amd-health-test-groups' && model.value.logical.available) return percent(counts.value.passing, counts.value.total)
  return undefined
}

// Drill-downs
const catalog = ref<{ open: boolean; request: CatalogRequest | null }>({ open: false, request: null })
const openCatalog = (request: CatalogRequest) => (catalog.value = { open: true, request })
const group = ref<{ open: boolean; row: Rec | null }>({ open: false, row: null })
const openGroup = (row: Rec) => (group.value = { open: true, row })
const logicalCatalog = ref(false)
const logical = ref<{ open: boolean; row: Rec | null }>({ open: false, row: null })
const openLogical = (row: Rec) => (logical.value = { open: true, row })
const detail = ref<{ open: boolean; detail: DetailRequest | null }>({ open: false, detail: null })

function openStat(item: StripItem) {
  const m = model.value
  if (item.id === 'amd-health-test-groups') {
    if (m.logical.rows.length) logicalCatalog.value = true
    else
      detail.value = {
        open: true,
        detail: {
          title: item.label,
          subtitle: item.scope || 'Operational aggregate',
          description: item.meta,
          fields: [
            ['Value', item.value],
            ['Observed', item.observed ? shortDate(item.observed) : null],
            ['Provenance', item.provenance],
          ],
          sources: [{ label: 'Open published AMD health data', url: AMD_TEST_HEALTH_ASSET }],
        },
      }
  } else if (item.id === 'amd-health-observed') openCatalog(m.catalogs.variants)
  else if (item.id === 'amd-health-incidents') openCatalog(m.catalogs.incidents)
}

// Charts
const HEALTH_SERIES: ChartSeries[] = [
  { key: 'passed', label: 'Passing', color: 'var(--success)' },
  { key: 'soft', label: 'Soft fail', color: 'var(--warning)' },
  { key: 'hard', label: 'Hard fail', color: 'var(--destructive)' },
  { key: 'unknown', label: 'Unknown', color: 'var(--muted-foreground)' },
]
const HARDWARE_SERIES: ChartSeries[] = [...HEALTH_SERIES.slice(0, 3), { key: 'missing', label: 'Not in latest', color: 'var(--muted-foreground)' }]
const buildRows = computed(() =>
  model.value.builds.map((b) => ({ label: b.label, passed: b.passed, soft: b.soft, hard: b.hard, unknown: b.unknown })),
)
const hardwareRows = computed(() =>
  model.value.hardware.map((r) => ({ label: r.label, passed: r.passed, soft: r.soft, hard: r.hard, missing: r.missing })),
)
const buildPoints = computed<EvidencePoint[]>(() =>
  model.value.builds.map((b) => ({ label: b.label, timestamp: b.timestamp, url: b.url ? String(b.url) : null, valueSummary: b.valueSummary })),
)
const hardwarePoints = computed<EvidencePoint[]>(() => model.value.hardware.map((r) => ({ label: r.label, valueSummary: hardwareValueSummary(r) })))
const evidence = ref<{ open: boolean; kind: 'builds' | 'hardware' }>({ open: false, kind: 'builds' })

function inspectBuild(index: number) {
  const point = model.value.builds[index]
  if (!point) return
  detail.value = {
    open: true,
    detail: {
      title: point.label,
      subtitle: point.url ? 'Source-backed chart observation' : 'Retained chart observation',
      fields: point.details,
      sources: [point.url ? { label: 'Open exact source', url: String(point.url) } : { label: 'Open published source data', url: OPERATIONS_ASSET }],
    },
  }
}
function inspectHardware(index: number) {
  const row = model.value.hardware[index]
  if (row) openCatalog({ title: `${row.label} AMD job variants`, subtitle: `Exact job variants assigned to ${row.label}`, rows: row.rows, filter: 'all' })
}

const CLUSTER_RING: Record<string, string> = {
  'is-success': 'before:bg-success',
  'is-warning': 'before:bg-warning',
  'is-danger': 'before:bg-destructive',
  'is-neutral': 'before:bg-muted-foreground/50',
}
const clusterAria = (c: AmdCluster) => `Open ${c.label}: ${c.renderedCount} AMD job variants`

// Tables
const latestBuild = computed(() => model.value.latestBuild)
const identity = (row: Rec) =>
  h('div', { class: 'flex flex-col' }, [
    h('button', { type: 'button', class: 'text-left font-medium hover:underline', onClick: () => openGroup(row) }, value(row.display_name || row.name || row.job_name)),
    h('span', { class: 'font-mono text-xs text-muted-foreground' }, `${value(row.hardware_variant || row.hardware, 'unknown')} - ${value(row.queue, 'queue unavailable')}`),
  ])
const priorityColumns = computed<ColumnDef<Rec, unknown>[]>(() => [
  {
    id: 'variant',
    header: 'AMD job variant',
    accessorFn: (row) => [row.display_name, row.name, row.hardware_variant, row.queue].join(' '),
    cell: ({ row }) => identity(row.original),
    enableHiding: false,
    enableSorting: false,
  },
  { id: 'queue', header: 'Queue', accessorFn: (row) => value(row.queue), cell: ({ getValue }) => h('span', { class: 'font-mono text-xs' }, String(getValue())), enableSorting: false },
  {
    id: 'rate',
    header: 'Retained-run pass rate',
    accessorFn: (row) => passRateLabel(row),
    cell: ({ row }) => h('button', { type: 'button', class: 'tabular-nums hover:underline', onClick: () => openGroup(row.original) }, passRateLabel(row.original)),
    enableSorting: false,
  },
  {
    id: 'latest',
    header: 'Latest',
    accessorFn: (row) => amdStateLabel(amdLatestState(row, latestBuild.value)),
    cell: ({ row }) => {
      const latest = amdLatestState(row.original, latestBuild.value)
      const badge = h(ToneBadge, { tone: toneForState(latest) }, () => amdStateLabel(latest))
      return row.original.latest_url
        ? h('a', { href: row.original.latest_url, target: '_blank', rel: 'noopener' }, [badge])
        : h('button', { type: 'button', onClick: () => openGroup(row.original) }, [badge])
    },
    enableSorting: false,
  },
  {
    id: 'counts',
    header: 'Pass / soft / hard',
    accessorFn: (row) => `${integer(row.passed)} / ${integer(row.soft_failed)} / ${integer(row.hard_failed)}`,
    cell: ({ row, getValue }) => h('button', { type: 'button', class: 'tabular-nums hover:underline', onClick: () => openGroup(row.original) }, String(getValue())),
    enableSorting: false,
  },
  {
    id: 'evidence',
    header: 'Latest evidence',
    accessorFn: (row) => `#${value(row.latest_build_number)}`,
    cell: ({ row, getValue }) =>
      row.original.latest_url
        ? h('a', { href: row.original.latest_url, target: '_blank', rel: 'noopener', class: 'font-mono hover:underline' }, String(getValue()))
        : h('span', { class: 'font-mono text-muted-foreground' }, String(getValue())),
    enableSorting: false,
  },
])

const logicalRows = computed(() => sortLogicalRows(model.value.logical.rows))
const logicalColumns: ColumnDef<Rec, unknown>[] = [
  {
    id: 'label',
    header: 'Logical AMD test group',
    accessorFn: (row) =>
      [row.label, row.logical_key, ...(row.job_variants || []).map((v: Rec) => v.exact_job_name)].join(' '),
    cell: ({ row }) =>
      h('button', { type: 'button', class: 'text-left font-medium hover:underline', onClick: () => openLogical(row.original) }, row.original.label || row.original.logical_key),
    enableHiding: false,
    enableSorting: false,
  },
  {
    id: 'state',
    header: 'Latest result',
    accessorFn: (row) => `${amdLogicalStateLabel(row.state)} ${row.state}`,
    cell: ({ row }) =>
      h('button', { type: 'button', onClick: () => openLogical(row.original) }, [
        h(ToneBadge, { tone: amdLogicalStateTone(row.original.state) }, () => amdLogicalStateLabel(row.original.state)),
      ]),
    enableSorting: false,
  },
  { id: 'hardware', header: 'Hardware routes', accessorFn: (row) => integer(row.hardware_count), enableSorting: false },
  { id: 'variants', header: 'Exact job variants', accessorFn: (row) => integer(row.job_variant_count), enableSorting: false },
  {
    id: 'routes',
    header: 'Route outcomes',
    accessorFn: (row) => (row.hardware_states || []).map((s: Rec) => `${s.hardware} ${s.state}`).join(' '),
    cell: ({ row }) =>
      h(
        'div',
        { class: 'flex flex-wrap gap-1' },
        (row.original.hardware_states || []).map((s: Rec) =>
          h(ToneBadge, { tone: amdLogicalSignalTone(s.state) }, () => `${hardwareDisplayLabel(s.hardware)} · ${amdLogicalSignalLabel(s.state)}`),
        ),
      ),
    enableSorting: false,
  },
]
</script>

<template>
  <ViewState :loading="loading && !ops" :error="error" @retry="retry">
    <div class="flex flex-col gap-4 md:gap-6" data-testid="analytics-groups">
      <p v-if="observedAt" class="-mt-2 text-xs text-muted-foreground" :title="String(observedAt)">
        Current main CI AMD MI health · observed {{ shortDate(observedAt) }} · {{ age(observedAt) }}
      </p>
      <p v-if="!model.available" class="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning" data-testid="groups-unavailable">
        AMD job-variant history is unavailable. No upstream result has been substituted for AMD health.
      </p>
      <template v-else>
        <p v-if="model.retentionNote" class="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning">{{ model.retentionNote }}</p>

        <div
          class="grid grid-cols-1 gap-4 *:data-[slot=card]:shadow-xs @xl/main:grid-cols-2 @5xl/main:grid-cols-4"
          data-testid="groups-kpis"
        >
          <StatCard
            v-for="item in model.strip"
            :key="item.id"
            :item="item"
            :badge="badgeFor(item)"
            :data-testid="`kpi-${item.id}`"
            @open="openStat(item)"
          />
        </div>

        <p class="rounded-md border border-info/40 bg-info/10 p-3 text-sm text-info">
          <strong>Main CI AMD gating-job health.</strong>
          Each row is one exact AMD Buildkite job variant. Soft results are raw warning observations, not confirmed incidents until they
          recur on two distinct completed builds. The latest count is current-only; older names remain available as history and are not
          treated as missing failures. Upstream results are not used as AMD passes.
        </p>

        <div class="grid gap-4 md:gap-6 *:min-w-0 @5xl/main:grid-cols-2">
          <Card data-testid="build-health-chart">
            <CardHeader>
              <CardTitle>AMD health by nightly</CardTitle>
              <CardDescription>Passing, soft-failing, hard-failing, and unknown job variants in each retained AMD build</CardDescription>
              <CardAction v-if="buildPoints.length">
                <Button variant="outline" size="sm" @click="evidence = { open: true, kind: 'builds' }">Inspect {{ integer(buildPoints.length) }} observations</Button>
              </CardAction>
            </CardHeader>
            <CardContent class="px-2 sm:px-6">
              <p v-if="!buildRows.length" class="py-12 text-center text-sm text-muted-foreground">No retained AMD build summaries in this snapshot.</p>
              <StackedBarChart v-else :rows="buildRows" :series="HEALTH_SERIES" value-label="AMD job variants" @select="inspectBuild" />
            </CardContent>
          </Card>
          <Card data-testid="hardware-health-chart">
            <CardHeader>
              <CardTitle>Latest health by hardware variant</CardTitle>
              <CardDescription>Current outcomes for each MI250, MI300, MI325, and MI355 execution variant</CardDescription>
              <CardAction>
                <Button variant="outline" size="sm" @click="evidence = { open: true, kind: 'hardware' }">Inspect {{ integer(hardwarePoints.length) }} observations</Button>
              </CardAction>
            </CardHeader>
            <CardContent class="px-2 sm:px-6">
              <StackedBarChart
                :rows="hardwareRows"
                :series="HARDWARE_SERIES"
                horizontal
                :height="Math.max(220, hardwareRows.length * 34 + 70)"
                value-label="AMD job variants"
                @select="inspectHardware"
              />
            </CardContent>
          </Card>
        </div>

        <section class="flex flex-col gap-3">
          <div class="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h3 class="text-lg font-semibold">Retained AMD job-variant catalog</h3>
              <p class="text-sm text-muted-foreground">Start with the latest signal, then inspect exact nightly evidence for current or historical variants.</p>
            </div>
            <Button variant="outline" data-testid="browse-all" @click="openCatalog(model.catalogs.all)">{{ model.browseAllLabel }}</Button>
          </div>
          <div class="grid gap-4 @xl/main:grid-cols-2 @5xl/main:grid-cols-4">
            <button
              v-for="cluster in model.clusters"
              :key="cluster.label"
              type="button"
              class="relative flex flex-col gap-1 overflow-hidden rounded-xl border bg-card p-4 text-left shadow-xs transition-colors before:absolute before:inset-x-0 before:top-0 before:h-1 hover:bg-accent/40"
              :class="CLUSTER_RING[cluster.tone]"
              :aria-label="clusterAria(cluster)"
              data-testid="cluster"
              @click="openCatalog(cluster.catalog)"
            >
              <div class="flex items-baseline justify-between gap-2">
                <strong data-part="label">{{ cluster.label }}</strong>
                <span class="text-2xl font-semibold tabular-nums" data-part="count">{{ cluster.renderedCount }}</span>
              </div>
              <span class="text-sm" data-part="description">{{ cluster.description }}</span>
              <span class="text-xs text-muted-foreground" data-part="meta">{{ cluster.meta }}</span>
            </button>
          </div>
        </section>

        <Card data-testid="priority">
          <CardHeader>
            <CardTitle>Current AMD failures to inspect</CardTitle>
            <CardDescription>{{ model.priorityMeta }}</CardDescription>
          </CardHeader>
          <CardContent class="flex flex-col gap-2">
            <p class="text-xs text-muted-foreground">{{ model.priorityCaption }}</p>
            <DataTable
              :columns="priorityColumns"
              :data="model.priority"
              :page-size="10"
              search-placeholder="Filter job variant, hardware, or queue"
              empty-text="No current AMD failure observations."
            />
          </CardContent>
        </Card>
      </template>

      <AmdCatalogSheet v-model:open="catalog.open" :request="catalog.request" :latest-build="latestBuild" @inspect="openGroup" />
      <AmdGroupSheet v-model:open="group.open" :row="group.row" :latest-build="latestBuild" />
      <TableSheet
        v-model:open="logicalCatalog"
        title="Latest AMD logical test groups"
        subtitle="Build-pinned logical groups; partial and non-passing groups are listed first"
        :columns="logicalColumns"
        :rows="logicalRows"
        search-placeholder="Filter logical test group, result, hardware, or exact job variant"
      />
      <LogicalGroupSheet
        v-model:open="logical.open"
        :row="logical.row"
        :inventory="model.logical.inventory"
        :amd-health="health"
        @inspect="openGroup"
      />
      <ChartEvidenceSheet
        v-model:open="evidence.open"
        :title="evidence.kind === 'builds' ? 'AMD nightly job-variant health' : 'Latest AMD health by hardware variant'"
        :points="evidence.kind === 'builds' ? buildPoints : hardwarePoints"
        @inspect="(i) => (evidence.kind === 'builds' ? inspectBuild(i) : inspectHardware(i))"
      />
      <DetailSheet v-model:open="detail.open" :detail="detail.detail" />
    </div>
  </ViewState>
</template>
