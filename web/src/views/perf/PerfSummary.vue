<script setup lang="ts">
import { Check, ChevronRight } from '@lucide/vue'
import { useElementSize } from '@vueuse/core'
import { computed, ref } from 'vue'
import type { Detail } from '@/components/evidence/detail'
import BarChart from '@/components/charts/BarChart.vue'
import ProportionBar from '@/components/charts/ProportionBar.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import DataTable from '@/components/table/DataTable.vue'
import { link, text, type CellSpec, type ColumnSpec } from '@/components/table/columns'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { integer, type Rec } from '@/domain/format'
import { perfHardwareLabel, perfLatestBuildLabel, perfModelTrend, perfShortModel, type PerfChange, type PerfHardwareRow, type PerfModelRow } from '@/domain/perf'
import { hardwareColor, SERIES } from '@/lib/palette'
import { changeColumns, changeHistory, changesDetail, deviceColor, deviceDots } from './details'

const props = defineProps<{
  rows: PerfModelRow[]
  changes: PerfChange[]
  hardware: PerfHardwareRow[]
  hardwareLabel: string
  device: string
  thresholds: Rec
}>()
const emit = defineEmits<{ select: [model: string]; drill: [Detail]; device: [id: string]; hardware: [] }>()

// Counts of moved metrics are volumes, so they take series colors; direction carries the verdict.
const SERIES_SPEC = [
  { key: 'regressions', label: 'Regressions', color: SERIES.secondary },
  { key: 'improvements', label: 'Improvements', color: SERIES.primary },
]
const chartHost = ref<HTMLElement | null>(null)
const { width } = useElementSize(chartHost)
// Horizontal bars list the first model at the bottom; reversing keeps the table order top-down.
const bars = computed(() =>
  [...props.rows].reverse().map((row) => ({ model: row.model.model, short: perfShortModel(row.model.model), improvements: row.improvements, regressions: row.regressions })),
)
type Bar = (typeof bars.value)[number]
const totals = computed(() => ({
  regressions: props.rows.reduce((sum, row) => sum + row.regressions, 0),
  improvements: props.rows.reduce((sum, row) => sum + row.improvements, 0),
}))
const categoryWidth = computed(() => (width.value && width.value < 520 ? 92 : 168))

const movers = computed(() => props.changes.slice(0, 8))
const deviceOptions = computed(() => [
  { id: 'all', label: 'All hardware', color: SERIES.muted, meta: `${integer(props.hardware.reduce((sum, row) => sum + row.configs, 0))} configs` },
  ...props.hardware.map((row) => ({
    id: row.id,
    label: row.label,
    color: hardwareColor(row.id),
    meta: `${integer(row.configs)} configs · ${integer(row.models)} models · ${integer(row.metrics)} metrics`,
  })),
])
const segments = computed(() => props.hardware.map((row) => ({ key: row.id, label: row.label, count: row.configs, color: hardwareColor(row.id) })))

function modelChanges(row: PerfModelRow, tone: 'hard' | 'pass') {
  const changes = props.changes.filter((change) => change.model === row.model && change.tone === tone)
  const what = tone === 'hard' ? 'Regressions' : 'Improvements'
  const verdict = tone === 'hard' ? 'Worse' : 'Better'
  return changesDetail(`perf-changes:${tone}:${row.model.model}`, `${what} · ${perfShortModel(row.model.model)}`, `${verdict} than the previous nightly beyond the threshold`, changes, props.thresholds)
}
const countCell = (row: PerfModelRow, count: number, tone: 'hard' | 'pass'): CellSpec => (count ? { text: integer(count), detail: () => modelChanges(row, tone) } : text(integer(count)))

const columns = computed<ColumnSpec<PerfModelRow>[]>(() => [
  { id: 'model', label: 'Model', value: (row) => row.model.model, cell: (row) => ({ text: row.model.model, nowrap: true }), hideable: false },
  { id: 'regressions', label: 'Regressions', numeric: true, value: (row) => row.regressions, cell: (row) => countCell(row, row.regressions, 'hard') },
  { id: 'improvements', label: 'Improvements', numeric: true, value: (row) => row.improvements, cell: (row) => countCell(row, row.improvements, 'pass') },
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
  {
    id: 'hardware',
    label: 'Hardware',
    value: (row) => perfHardwareLabel(row.model),
    cell: (row) => ({ text: perfHardwareLabel(row.model), dots: deviceDots(row.model.devices || []) }),
  },
  { id: 'nightlies', label: 'Nightlies', numeric: true, value: (row) => Number(row.model.nightly_count || 0), cell: (row) => text(integer(row.model.nightly_count)) },
  { id: 'performance', label: 'Perf metrics', numeric: true, value: (row) => row.performanceMetrics, cell: (row) => text(integer(row.performanceMetrics)) },
  { id: 'accuracy', label: 'Accuracy', numeric: true, value: (row) => row.accuracyMetrics, cell: (row) => text(integer(row.accuracyMetrics)) },
  {
    id: 'latest',
    label: 'Build',
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
      <CardContent class="px-3 sm:px-6">
        <div ref="chartHost" class="min-w-0">
          <BarChart
            v-if="totals.improvements || totals.regressions"
            :rows="bars"
            :category="(row: Bar) => row.short"
            :category-width="categoryWidth"
            :series="SERIES_SPEC"
            :diverging="['regressions']"
            :legend="false"
            orientation="horizontal"
            label="Regressed and improved metrics per model"
            hint="Click to open this model"
            :on-select="(row: Bar) => emit('select', row.model)"
          >
            <template #header>
              <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground" data-slot="chart-legend">
                <span class="flex items-center gap-1.5"><span class="size-2 rounded-[2px]" :style="{ backgroundColor: SERIES_SPEC[0]!.color }" aria-hidden="true" />← Regressions</span>
                <span class="flex items-center gap-1.5"><span class="size-2 rounded-[2px]" :style="{ backgroundColor: SERIES_SPEC[1]!.color }" aria-hidden="true" />Improvements →</span>
              </div>
            </template>
          </BarChart>
          <EmptyState v-else title="No metric moved beyond its threshold in the latest nightly." />
        </div>
      </CardContent>
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
            :on-row-click="(change: PerfChange) => emit('drill', changeHistory(change, thresholds))"
          />
          <EmptyState v-else title="No metric changed beyond its threshold." />
        </CardContent>
      </Card>
      <Card data-testid="perf-hardware-coverage">
        <CardHeader>
          <CardTitle>AMD hardware</CardTitle>
          <CardDescription>ROCm only · select a GPU to filter the page</CardDescription>
          <CardAction>
            <Button variant="outline" size="sm" data-testid="perf-hardware-details" @click="emit('hardware')">Details <ChevronRight /></Button>
          </CardAction>
        </CardHeader>
        <CardContent class="flex flex-col gap-4">
          <p class="text-2xl font-semibold tracking-tight tabular-nums" data-testid="perf-hardware-label">{{ hardwareLabel }}</p>
          <ProportionBar :segments="segments" label="Configurations per GPU" :legend="false" />
          <ul class="flex flex-col gap-1.5" aria-label="Hardware filter">
            <li v-for="option in deviceOptions" :key="option.id">
              <button
                type="button"
                class="flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left text-sm transition-colors outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 aria-pressed:border-foreground/40 aria-pressed:shadow-xs"
                :aria-pressed="device === option.id"
                @click="emit('device', option.id)"
              >
                <span class="size-2.5 shrink-0 rounded-full" :style="{ backgroundColor: option.color }" aria-hidden="true" />
                <span class="font-medium">{{ option.label }}</span>
                <span class="ml-auto text-right text-xs text-muted-foreground tabular-nums">{{ option.meta }}</span>
                <Check class="size-4 shrink-0" :class="device === option.id ? 'text-foreground' : 'invisible'" aria-hidden="true" />
              </button>
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
