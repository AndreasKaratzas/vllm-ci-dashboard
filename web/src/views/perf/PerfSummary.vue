<script setup lang="ts">
import { useElementSize } from '@vueuse/core'
import { computed, ref } from 'vue'
import type { Detail } from '@/components/evidence/detail'
import BarChart from '@/components/charts/BarChart.vue'
import ProportionBar from '@/components/charts/ProportionBar.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import DataTable from '@/components/table/DataTable.vue'
import { link, text, type ColumnSpec } from '@/components/table/columns'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { integer } from '@/domain/format'
import {
  perfHardwareLabel,
  perfLatestBuildLabel,
  perfModelTrend,
  perfShortModel,
  type PerfChange,
  type PerfHardwareRow,
  type PerfModelRow,
} from '@/domain/perf'
import { hardwareColor, statusVar } from '@/lib/palette'
import { changeColumns, changeHistory, changesDetail, deviceColor } from './details'

const props = defineProps<{ rows: PerfModelRow[]; changes: PerfChange[]; hardware: PerfHardwareRow[]; device: string }>()
const emit = defineEmits<{ select: [model: string]; drill: [Detail]; device: [id: string] }>()

const SERIES = [
  { key: 'improvements', label: 'Improvements', color: statusVar('pass') },
  { key: 'regressions', label: 'Regressions', color: statusVar('hard') },
]
const chartCard = ref<HTMLElement | null>(null)
const { width } = useElementSize(chartCard)
// Unovis keeps the first tick formatter, so the chart is re-created when the label width changes.
const narrow = computed(() => width.value > 0 && width.value < 520)
// Horizontal bars list the first model at the bottom; reversing keeps the table order top-down.
const bars = computed(() =>
  [...props.rows].reverse().map((row) => ({ model: row.model.model, short: perfShortModel(row.model.model), improvements: row.improvements, regressions: row.regressions })),
)
type Bar = (typeof bars.value)[number]
const totals = computed(() => ({
  regressions: props.rows.reduce((sum, row) => sum + row.regressions, 0),
  improvements: props.rows.reduce((sum, row) => sum + row.improvements, 0),
}))

const movers = computed(() => props.changes.slice(0, 8))
const segments = computed(() => props.hardware.map((row) => ({ key: row.id, label: row.label, count: row.configs, color: hardwareColor(row.id) })))

function modelChanges(row: PerfModelRow, tone: 'hard' | 'pass') {
  const changes = props.changes.filter((change) => change.model === row.model && change.tone === tone)
  const what = tone === 'hard' ? 'Regressions' : 'Improvements'
  return changesDetail(`perf-changes:${tone}:${row.model.model}`, `${what} · ${perfShortModel(row.model.model)}`, row.model.model, changes)
}
const countCell = (row: PerfModelRow, count: number, tone: 'hard' | 'pass') =>
  count ? { text: integer(count), tone, detail: () => modelChanges(row, tone) } : text(integer(count))

const columns = computed<ColumnSpec<PerfModelRow>[]>(() => [
  { id: 'model', label: 'Model', value: (row) => row.model.model, cell: (row) => ({ text: row.model.model }), hideable: false },
  { id: 'hardware', label: 'Hardware', value: (row) => perfHardwareLabel(row.model) },
  {
    id: 'trend',
    label: 'Trend',
    sortable: false,
    value: () => '',
    cell: (row) => {
      const trend = perfModelTrend(row.model, props.device)
      return { text: '', spark: trend ? { values: trend.values, label: trend.label, color: deviceColor(trend.device) } : undefined }
    },
  },
  { id: 'nightlies', label: 'Nightlies', numeric: true, value: (row) => Number(row.model.nightly_count || 0), cell: (row) => text(integer(row.model.nightly_count)) },
  { id: 'performance', label: 'Perf metrics', numeric: true, value: (row) => row.performanceMetrics, cell: (row) => text(integer(row.performanceMetrics)) },
  { id: 'accuracy', label: 'Accuracy', numeric: true, value: (row) => row.accuracyMetrics, cell: (row) => text(integer(row.accuracyMetrics)) },
  { id: 'regressions', label: 'Regressions', numeric: true, value: (row) => row.regressions, cell: (row) => countCell(row, row.regressions, 'hard') },
  { id: 'improvements', label: 'Improvements', numeric: true, value: (row) => row.improvements, cell: (row) => countCell(row, row.improvements, 'pass') },
  {
    id: 'latest',
    label: 'Latest build',
    value: (row) => Number((row.model.latest || {}).build_number || 0),
    cell: (row) => link(perfLatestBuildLabel(row.model), (row.model.latest || {}).build_url, true),
  },
])
</script>

<template>
  <div class="flex min-w-0 flex-col gap-4 md:gap-6">
    <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="perf-changes-chart">
      <CardHeader>
        <CardTitle>Latest nightly changes by model</CardTitle>
        <CardDescription>
          {{ integer(totals.improvements) }} improved and {{ integer(totals.regressions) }} regressed metrics versus each model's previous nightly
        </CardDescription>
      </CardHeader>
      <CardContent class="px-2 sm:px-6"><div ref="chartCard" class="min-w-0">
        <BarChart
          v-if="totals.improvements || totals.regressions"
          :rows="bars"
          :key="String(narrow)"
          :category="(row: Bar) => (narrow && row.short.length > 14 ? `${row.short.slice(0, 13)}…` : row.short)"
          :series="SERIES"
          :diverging="['regressions']"
          orientation="horizontal"
          label="Regressed and improved metrics per model"
          hint="Click to open this model"
          :on-select="(row: Bar) => emit('select', row.model)"
        />
        <EmptyState v-else title="No metric moved beyond its threshold in the latest nightly." />
      </div></CardContent>
    </Card>

    <div class="grid grid-cols-1 items-start gap-4 md:gap-6 @4xl/main:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] *:min-w-0">
      <Card data-testid="perf-movers">
        <CardHeader>
          <CardTitle>Largest changes</CardTitle>
          <CardDescription>Biggest moves versus the previous nightly; select one for its history</CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable
            v-if="movers.length"
            :columns="changeColumns()"
            :rows="movers"
            :page-size="false"
            :searchable="false"
            label="Largest changes"
            :on-row-click="(change: PerfChange) => emit('drill', changeHistory(change))"
            @drill="(detail) => emit('drill', detail)"
          />
          <EmptyState v-else title="No metric changed beyond its threshold." />
        </CardContent>
      </Card>
      <Card data-testid="perf-hardware-coverage">
        <CardHeader>
          <CardTitle>Hardware coverage</CardTitle>
          <CardDescription>Performance configurations per AMD GPU; select one to filter</CardDescription>
        </CardHeader>
        <CardContent class="flex flex-col gap-4">
          <ProportionBar :segments="segments" label="Configurations per GPU" :on-select="(segment) => emit('device', segment.key)" />
          <ul class="flex flex-col divide-y rounded-lg border text-sm">
            <li v-for="row in hardware" :key="row.id" class="flex items-center gap-3 px-3 py-2.5">
              <span class="size-2.5 shrink-0 rounded-full" :style="{ backgroundColor: hardwareColor(row.id) }" aria-hidden="true" />
              <span class="font-medium">{{ row.label }}</span>
              <span class="ml-auto text-right text-muted-foreground tabular-nums">
                {{ integer(row.models) }} models · {{ integer(row.metrics) }} metrics
              </span>
            </li>
          </ul>
        </CardContent>
      </Card>
    </div>

    <Card data-testid="perf-overview">
      <CardHeader>
        <CardTitle>AMD model overview</CardTitle>
        <CardDescription>{{ integer(rows.length) }} retained model families · select a model to open its performance histories</CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          :columns="columns"
          :rows="rows"
          :page-size="25"
          :search-text="(row: PerfModelRow) => perfShortModel(row.model.model)"
          search-placeholder="Filter model or hardware"
          empty-text="No model matches the selected filters."
          label="AMD model overview"
          :on-row-click="(row: PerfModelRow) => emit('select', row.model.model)"
          @drill="(detail) => emit('drill', detail)"
        />
      </CardContent>
    </Card>
  </div>
</template>
