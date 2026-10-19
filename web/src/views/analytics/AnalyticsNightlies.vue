<script setup lang="ts">
import type { ColumnDef } from '@tanstack/vue-table'
import { computed, h, ref } from 'vue'
import ChartEvidenceSheet, { type EvidencePoint } from '@/components/analytics/ChartEvidenceSheet.vue'
import DetailSheet, { type DetailRequest } from '@/components/analytics/DetailSheet.vue'
import StackedBarChart, { type ChartSeries } from '@/components/analytics/StackedBarChart.vue'
import StatCard from '@/components/analytics/StatCard.vue'
import BuildDetailSheet from '@/components/ops/BuildDetailSheet.vue'
import DataTable from '@/components/ops/DataTable.vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import ViewState from '@/components/ops/ViewState.vue'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useOperationSections } from '@/composables/useOperations'
import {
  analyticsObservedAt,
  nightliesModel,
  nightlyCountLabel,
  nightlyMovementSummary,
  type NightlyStripItem,
} from '@/domain/analytics'
import { age, integer, shortDate, toneForState, value, type Rec } from '@/domain/format'
import { latestAmd, nightlyFailureCount, nightlyFailureMovement } from '@/domain/nightly'
import { exactPipelineBuildUrl } from '@/domain/urls'

const OPERATIONS_ASSET = 'data/vllm/ci/operations_v2_manifest.json'
const { data: ops, error, loading, retry } = useOperationSections(['nightly'])

const model = computed(() => nightliesModel(latestAmd(ops.value)))
const observedAt = computed(() => (ops.value ? analyticsObservedAt('nightlies', ops.value, {}) : null))

const build = ref<{ open: boolean; build: Rec | null; scope?: string }>({ open: false, build: null })
function openBuild(row: Rec, scope?: string) {
  build.value = { open: true, build: row, scope }
}
const buildTitle = computed(() => `${model.value.name} build #${value(build.value.build?.number)}`)
function openStat(item: NightlyStripItem) {
  if (item.opensBuild && model.value.latest.number) openBuild(model.value.latest, item.scope)
}
const statBadge = (item: NightlyStripItem) => (item.label.startsWith('LATEST') && model.value.latest.number ? value(model.value.latest.state) : undefined)

// Fixes are drawn below zero, as on the classic chart.
const SERIES: ChartSeries[] = [
  { key: 'new', label: 'New failure', color: 'var(--destructive)' },
  { key: 'recurring', label: 'Recurring failure', color: 'var(--warning)' },
  { key: 'fixed', label: 'Fixed', color: 'var(--success)' },
]
const chartRows = computed(() =>
  model.value.chartBuilds.map((b) => ({
    label: `#${b.number}`,
    new: nightlyFailureCount(b, 'new') ?? 0,
    recurring: nightlyFailureCount(b, 'recurring') ?? 0,
    fixed: -Number(nightlyFailureCount(b, 'fixed') || 0),
  })),
)
const points = computed<EvidencePoint[]>(() =>
  model.value.chartBuilds.map((b) => ({
    label: `#${b.number}`,
    timestamp: b.created_at,
    url: exactPipelineBuildUrl(b, 'ci') || null,
    valueSummary: nightlyMovementSummary(b),
  })),
)
const chartEvidence = ref(false)
const detail = ref<{ open: boolean; detail: DetailRequest | null }>({ open: false, detail: null })
function inspectPoint(index: number) {
  const row = model.value.chartBuilds[index]
  if (!row) return
  const movement = nightlyFailureMovement(row)!
  const url = exactPipelineBuildUrl(row, 'ci')
  detail.value = {
    open: true,
    detail: {
      title: `#${row.number}`,
      subtitle: url ? 'Source-backed chart observation' : 'Retained chart observation',
      fields: [
        ['state', row.state],
        ['new failure', movement.new.length],
        ['recurring failure', movement.recurring.length],
        ['fixed', movement.fixed.length],
      ],
      sources: [url ? { label: 'Open exact source', url } : { label: 'Open published source data', url: OPERATIONS_ASSET }],
    },
  }
}

const countCell = (key: 'new' | 'recurring' | 'fixed') => ({
  id: key,
  header: { new: 'New failure', recurring: 'Recurring failure', fixed: 'Fixed' }[key],
  accessorFn: (row: Rec) => nightlyCountLabel(row, key),
  cell: ({ row, getValue }: { row: { original: Rec }; getValue: () => unknown }) =>
    h('button', { type: 'button', class: 'tabular-nums hover:underline', onClick: () => openBuild(row.original, key) }, String(getValue())),
  enableSorting: false,
})
const columns = computed<ColumnDef<Rec, unknown>[]>(() => [
  {
    id: 'number',
    header: `${model.value.name} nightly`,
    accessorFn: (row) => `#${row.number}`,
    cell: ({ row, getValue }) => {
      const url = exactPipelineBuildUrl(row.original, 'ci')
      return url
        ? h('a', { href: url, target: '_blank', rel: 'noopener', class: 'font-mono hover:underline' }, String(getValue()))
        : h('span', { class: 'font-mono' }, String(getValue()))
    },
    enableHiding: false,
    enableSorting: false,
  },
  {
    id: 'state',
    header: 'State',
    accessorFn: (row) => value(row.state, 'unknown'),
    cell: ({ row, getValue }) => {
      const url = exactPipelineBuildUrl(row.original, 'ci')
      const badge = h(ToneBadge, { tone: toneForState(row.original.state) }, () => String(getValue()))
      return url ? h('a', { href: url, target: '_blank', rel: 'noopener' }, [badge]) : badge
    },
    enableSorting: false,
  },
  {
    id: 'variants',
    header: 'Job variants observed',
    accessorFn: (row) => integer(row.total_groups),
    cell: ({ row, getValue }) =>
      h('button', { type: 'button', class: 'tabular-nums hover:underline', onClick: () => openBuild(row.original) }, String(getValue())),
    enableSorting: false,
  },
  countCell('new') as ColumnDef<Rec, unknown>,
  countCell('recurring') as ColumnDef<Rec, unknown>,
  countCell('fixed') as ColumnDef<Rec, unknown>,
  { id: 'started', header: 'Started', accessorFn: (row) => shortDate(row.created_at), enableSorting: false },
])
</script>

<template>
  <ViewState :loading="loading && !ops" :error="error" @retry="retry">
    <div class="flex flex-col gap-4 md:gap-6" data-testid="analytics-nightlies">
      <p v-if="observedAt" class="-mt-2 text-xs text-muted-foreground" :title="String(observedAt)">
        Main CI AMD MI nightly outcomes · observed {{ shortDate(observedAt) }} · {{ age(observedAt) }}
      </p>
      <div class="grid grid-cols-1 gap-4 @xl/main:grid-cols-2 @5xl/main:grid-cols-5" data-testid="nightlies-kpis">
        <StatCard
          v-for="item in model.strip"
          :key="item.label"
          :item="item"
          :badge="statBadge(item)"
          :action="item.opensBuild && model.latest.number ? 'Open evidence →' : undefined"
          @open="openStat(item)"
        />
      </div>
      <p
        class="rounded-md border p-3 text-sm"
        :class="model.latestMovement ? 'border-info/40 bg-info/10 text-info' : 'border-warning/40 bg-warning/10 text-warning'"
        data-testid="nightlies-note"
      >
        <template v-if="model.latestMovement">
          <strong>Main CI AMD MI job history. </strong>Every current hard or soft failure is counted once as new or recurring. A previous
          failure that passes is fixed. Missing or skipped jobs are omitted.
        </template>
        <template v-else>
          <strong>Failure movement unavailable. </strong>Refresh the operations snapshot to publish observed-failure-movement-v1 data.
        </template>
      </p>

      <Card data-testid="nightlies-chart">
        <CardHeader>
          <CardTitle>{{ model.name }} nightly failure movement</CardTitle>
          <CardDescription>New and recurring failures are above zero; fixes are below. Missing or skipped jobs are omitted.</CardDescription>
          <CardAction v-if="points.length">
            <Button variant="outline" size="sm" @click="chartEvidence = true">Inspect {{ integer(points.length) }} observations</Button>
          </CardAction>
        </CardHeader>
        <CardContent class="px-2 sm:px-6">
          <p v-if="!chartRows.length" class="py-12 text-center text-sm text-muted-foreground">No nightly with comparable failure movement is retained.</p>
          <StackedBarChart v-else :rows="chartRows" :series="SERIES" absolute @select="inspectPoint" />
        </CardContent>
      </Card>

      <Card data-testid="nightlies-table">
        <CardHeader>
          <CardTitle>{{ model.name }} AMD MI nightly failure movement</CardTitle>
          <CardDescription>{{ integer(model.rows.length) }} retained nightlies · counts open their exact job variants</CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable
            :columns="columns"
            :data="model.rows"
            :page-size="15"
            search-placeholder="Filter nightly, state, or date"
            empty-text="No matching observations."
          />
        </CardContent>
      </Card>

      <BuildDetailSheet v-model:open="build.open" :build="build.build" :title="buildTitle" :scope="build.scope" />
      <ChartEvidenceSheet v-model:open="chartEvidence" :title="`${model.name} nightly failure movement`" :points="points" @inspect="inspectPoint" />
      <DetailSheet v-model:open="detail.open" :detail="detail.detail" />
    </div>
  </ViewState>
</template>
