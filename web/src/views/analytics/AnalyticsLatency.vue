<script setup lang="ts">
import type { ColumnDef } from '@tanstack/vue-table'
import { computed, h, ref } from 'vue'
import ChartEvidenceSheet, { type EvidencePoint } from '@/components/analytics/ChartEvidenceSheet.vue'
import LatencyEvidenceSheet from '@/components/analytics/LatencyEvidenceSheet.vue'
import StackedBarChart, { type ChartSeries } from '@/components/analytics/StackedBarChart.vue'
import StatCard from '@/components/analytics/StatCard.vue'
import DataTable from '@/components/ops/DataTable.vue'
import ViewState from '@/components/ops/ViewState.vue'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useOperationSections } from '@/composables/useOperations'
import { analyticsObservedAt, latencyComparison, latencyLabel, latencyModel, latencySideDuration, observedNightliesLabel } from '@/domain/analytics'
import { age, shortDate, type Rec } from '@/domain/format'

const { data: ops, error, loading, retry } = useOperationSections(['comparison'])

const comparison = computed(() => latencyComparison(ops.value))
const model = computed(() => latencyModel(comparison.value))
const observedAt = computed(() => (ops.value ? analyticsObservedAt('latency', ops.value, {}) : null))

const evidence = ref<{ open: boolean; row: Rec | null }>({ open: false, row: null })
const openEvidence = (row: Rec) => (evidence.value = { open: true, row })

const SERIES: ChartSeries[] = [{ key: 'amd', label: 'AMD', color: 'var(--warning)' }]
const chartRows = computed(() => model.value.top.map((row) => ({ label: latencyLabel(row), amd: Number(row.amd.median_duration_mins) })))
const points = computed<EvidencePoint[]>(() =>
  model.value.top.map((row) => ({ label: latencyLabel(row), valueSummary: `${latencySideDuration(row.amd)} AMD MI` })),
)
const chartEvidence = ref(false)

const columns = computed<ColumnDef<Rec, unknown>[]>(() => [
  {
    id: 'label',
    header: 'Main CI test group',
    accessorFn: (row) => [row.label, row.id].join(' '),
    cell: ({ row }) => h('button', { type: 'button', class: 'text-left font-medium hover:underline', onClick: () => openEvidence(row.original) }, latencyLabel(row.original)),
    enableHiding: false,
    enableSorting: false,
  },
  {
    id: 'median',
    header: 'AMD median',
    accessorFn: (row) => latencySideDuration(row.amd),
    cell: ({ row, getValue }) =>
      h('button', { type: 'button', class: 'tabular-nums hover:underline', onClick: () => openEvidence(row.original) }, String(getValue())),
    enableSorting: false,
  },
  {
    id: 'observed',
    header: 'Observed nightlies',
    accessorFn: (row) => observedNightliesLabel(row, model.value.builds),
    cell: ({ getValue }) => h('span', { class: 'tabular-nums' }, String(getValue())),
    enableSorting: false,
  },
])
</script>

<template>
  <ViewState :loading="loading && !ops" :error="error" @retry="retry">
    <div class="flex flex-col gap-4 md:gap-6" data-testid="analytics-latency">
      <p v-if="observedAt" class="-mt-2 text-xs text-muted-foreground" :title="String(observedAt)">
        Recent main CI AMD MI test-group timing · observed {{ shortDate(observedAt) }} · {{ age(observedAt) }}
      </p>
      <p v-if="!comparison.available" class="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning" data-testid="latency-unavailable">
        <strong>AMD MI nightly latency unavailable. </strong>{{ comparison.unavailable_reason }}. Recent AMD MI timing data is required.
      </p>
      <template v-else>
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <strong>Latest five completed main CI nightlies</strong>
          <span class="font-mono text-muted-foreground" data-testid="latency-cohort">{{ model.context }}</span>
        </div>
        <p class="rounded-md border border-info/40 bg-info/10 p-3 text-sm text-info">
          <strong>Current CI gating jobs only. </strong>One wall-completion observation per test group per nightly, then the median across
          up to five observations. Parallel shards contribute their maximum completion time. Missing recent observations stay unavailable;
          older builds are not substituted.
        </p>
        <div class="grid grid-cols-1 gap-4 @xl/main:grid-cols-2" data-testid="latency-kpis">
          <StatCard v-for="item in model.strip" :key="item.label" :item="item" />
        </div>

        <Card v-if="model.timed.length" data-testid="latency-chart">
          <CardHeader>
            <CardTitle>Recent AMD MI completion time</CardTitle>
            <CardDescription>Median of per-nightly group wall completion in minutes · select a bar for its nightly evidence</CardDescription>
            <CardAction>
              <Button variant="outline" size="sm" @click="chartEvidence = true">Inspect {{ points.length }} observations</Button>
            </CardAction>
          </CardHeader>
          <CardContent class="px-2 sm:px-6">
            <StackedBarChart
              :rows="chartRows"
              :series="SERIES"
              horizontal
              :height="Math.max(220, chartRows.length * 30 + 70)"
              value-label="Median group wall completion (minutes)"
              @select="(i) => model.top[i] && openEvidence(model.top[i])"
            />
          </CardContent>
        </Card>

        <Card data-testid="latency-table">
          <CardHeader>
            <CardTitle>Recent AMD MI nightly latency</CardTitle>
            <CardDescription>{{ model.tableMeta }}</CardDescription>
          </CardHeader>
          <CardContent>
            <DataTable
              :columns="columns"
              :data="model.rows"
              :page-size="12"
              search-placeholder="Filter current main CI test group"
              empty-text="No current main CI test groups."
            />
          </CardContent>
        </Card>

        <LatencyEvidenceSheet v-model:open="evidence.open" :row="evidence.row" :comparison="comparison" />
        <ChartEvidenceSheet
          v-model:open="chartEvidence"
          title="Recent gating-job timing evidence"
          :points="points"
          @inspect="(i) => model.top[i] && openEvidence(model.top[i])"
        />
      </template>
    </div>
  </ViewState>
</template>
