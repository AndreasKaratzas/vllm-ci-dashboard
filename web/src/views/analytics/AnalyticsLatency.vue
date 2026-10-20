<script setup lang="ts">
import { computed, h } from 'vue'
import BarChart from '@/components/charts/BarChart.vue'
import { plainNumber, type SeriesSpec } from '@/components/charts/chart'
import SheetStack from '@/components/evidence/SheetStack.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import type { ColumnSpec } from '@/components/table/columns'
import DataTable from '@/components/table/DataTable.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useOperationSections } from '@/composables/useOperations'
import { useSheetStack } from '@/composables/useSheetStack'
import { useSubviewHeader } from '@/composables/useSubviewHeader'
import { analyticsObservedAt, latencyComparison, latencyLabel, latencyModel, latencySideDuration, observedNightliesLabel } from '@/domain/analytics'
import { duration, integer, shortDate, type Rec } from '@/domain/format'
import { SERIES } from '@/lib/palette'
import AnalyticsAbout from './AnalyticsAbout.vue'
import { latencyDetail, latencyTitle } from './latencyDetail'

const { data: ops, error, loading, retry } = useOperationSections(['comparison'])
const sheets = useSheetStack()

const comparison = computed(() => latencyComparison(ops.value))
const model = computed(() => latencyModel(comparison.value))
const open = (row: Rec) => sheets.open(latencyDetail(row, comparison.value))

useSubviewHeader(() => ({
  observedAt: ops.value ? analyticsObservedAt('latency', ops.value, {}) : undefined,
  about: () => h(AnalyticsAbout, { view: 'latency' }),
}))

const minutes = (row: Rec) => Number(row.amd?.median_duration_mins)
const cohortRange = computed(() => {
  const builds = model.value.builds
  return builds.length ? `${shortDate(builds.at(-1)!.created_at)} – ${shortDate(builds[0]!.created_at)}` : 'No recent nightly observations'
})
const medianOfMedians = computed(() => {
  const values = model.value.timed.map(minutes).sort((a, b) => a - b)
  if (!values.length) return null
  const mid = Math.floor(values.length / 2)
  return values.length % 2 ? values[mid]! : (values[mid - 1]! + values[mid]!) / 2
})
const slowest = computed(() => model.value.timed[0])

const CHART_SERIES: SeriesSpec[] = [{ key: 'minutes', label: 'AMD MI median', color: SERIES.primary }]
const chartRows = computed(() => model.value.top.map((row) => ({ row, minutes: minutes(row) })))
const minutesLabel = (value: number) => `${plainNumber(value)} min`

const columns: ColumnSpec<Rec>[] = [
  {
    id: 'label',
    label: 'Main CI test group',
    value: (row) => latencyLabel(row),
    cell: (row) => ({ text: latencyLabel(row) || '—', onOpen: () => open(row) }),
    hideable: false,
  },
  {
    id: 'median',
    label: 'Median',
    numeric: true,
    value: (row) => (row.amd ? minutes(row) : -1),
    cell: (row) => ({ text: latencySideDuration(row.amd), muted: !row.amd }),
  },
  {
    id: 'observed',
    label: 'Observed nightlies',
    numeric: true,
    value: (row) => Number(row.amd?.sample_count ?? 0),
    cell: (row) => ({ text: observedNightliesLabel(row, model.value.builds), muted: !row.amd }),
    class: 'hidden sm:table-cell',
  },
]
</script>

<template>
  <AsyncBoundary :loading="loading && !ops" :error="error" @retry="retry">
    <div class="flex flex-col gap-4 md:gap-6" data-testid="analytics-latency">
      <EmptyState
        v-if="!comparison.available"
        title="AMD MI nightly latency unavailable"
        :description="`${comparison.unavailable_reason}. Recent AMD MI timing data is required.`"
        data-testid="latency-unavailable"
      />
      <template v-else>
        <StatGrid :cols="4" data-testid="latency-kpis">
          <KpiCard
            label="Main CI nightlies"
            :value="`${integer(model.builds.length)} / 5`"
            :footnote="cohortRange"
            data-testid="kpi-latency-nightlies"
          />
          <KpiCard
            label="AMD groups timed"
            :value="model.timed.length"
            :footnote="`${integer(model.rows.length - model.timed.length)} not observed in this cohort`"
            data-testid="kpi-latency-timed"
          />
          <KpiCard
            label="Slowest group"
            :value="slowest ? duration(minutes(slowest)) : null"
            :footnote="slowest ? latencyTitle(slowest) : 'No timed group'"
            :action="slowest ? { label: 'Inspect its nightlies', onOpen: () => open(slowest!) } : undefined"
            data-testid="kpi-latency-slowest"
          />
          <KpiCard
            label="Typical group"
            :value="medianOfMedians === null ? null : duration(medianOfMedians)"
            :footnote="`Median of ${integer(model.timed.length)} group medians`"
            data-testid="kpi-latency-typical"
          />
        </StatGrid>

        <Card v-if="model.timed.length" class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="latency-chart">
          <CardHeader>
            <CardTitle as="h2">Slowest AMD MI test groups</CardTitle>
            <CardDescription>
              Median group wall completion over nightlies <span class="font-mono" data-testid="latency-cohort">{{ model.context }}</span>
            </CardDescription>
          </CardHeader>
          <CardContent class="px-2 sm:px-6">
            <BarChart
              :rows="chartRows"
              :category="(point: { row: Rec }) => latencyLabel(point.row)"
              :row-label="(point: { row: Rec }) => latencyTitle(point.row)"
              :series="CHART_SERIES"
              orientation="horizontal"
              :category-width="150"
              :value-format="minutesLabel"
              label="Slowest AMD MI test groups"
              hint="Click for its nightly evidence"
              inspect-placement="legend"
              :on-select="(point: { row: Rec }) => open(point.row)"
            />
          </CardContent>
        </Card>

        <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="latency-table">
          <CardHeader>
            <CardTitle as="h2">Recent AMD MI nightly latency</CardTitle>
            <CardDescription>{{ model.tableMeta }}</CardDescription>
          </CardHeader>
          <CardContent>
            <DataTable
              :columns="columns"
              :rows="model.rows"
              :page-size="12"
              :on-row-click="open"
              search-placeholder="Filter current main CI test group"
              label="Recent AMD MI nightly latency"
              empty-text="No current main CI test groups."
            />
          </CardContent>
        </Card>
      </template>
    </div>
    <SheetStack :sheets="sheets" />
  </AsyncBoundary>
</template>
