<script setup lang="ts">
import { computed } from 'vue'
import { shortDay } from '@/components/charts/chart'
import LineChart from '@/components/charts/LineChart.vue'
import type { Rec } from '@/domain/format'
import { perfSparkRows, perfValue } from '@/domain/perf'
import type { PaletteColor } from '@/lib/palette'

const props = withDefaults(defineProps<{ series: Rec[]; unit?: unknown; label: string; color: PaletteColor; class?: string }>(), {
  unit: undefined,
  class: undefined,
})
const emit = defineEmits<{ select: [point: Rec] }>()

interface Row extends Record<string, unknown> {
  point: Rec
  index: number
  x: number
  value: number
}

// classic drawPerfSpark: finite points in payload order; fewer than two draw nothing.
const rows = computed<Row[]>(() =>
  perfSparkRows(props.series).map((point, index) => {
    const ms = Date.parse(String(point.date ?? ''))
    return { point, index, x: ms, value: Number(point.value) }
  }),
)
const spec = computed(() => [{ key: 'value', label: props.label, color: props.color }])
const day = (row: Row | undefined) => (row && Number.isFinite(row.x) ? shortDay(row.x) : '')
const rowLabel = (row: Row) => [row.point.build_number ? `Build #${row.point.build_number}` : '', day(row)].filter(Boolean).join(' · ')
// Throughput units would wrap every axis label; the card header names the unit.
const yFormat = (v: number) => perfValue(v, props.unit === 'tok/s' ? undefined : props.unit)
// Nightlies are irregularly spaced; plotting by index keeps evenly spaced, non-colliding date ticks.
const xFormat = (index: number) => day(rows.value[Math.round(index)])
</script>

<template>
  <LineChart
    v-if="rows.length >= 2"
    :rows="rows"
    :x="(row: Row) => row.index"
    :x-format="xFormat"
    :series="spec"
    :label="`${label} by nightly`"
    :row-label="rowLabel"
    :y-format="yFormat"
    :class="props.class"
    hint="Click to inspect this nightly"
    data-testid="perf-chart"
    :on-select="(row: Row) => emit('select', row.point)"
  />
  <p v-else class="flex h-24 items-center justify-center rounded-lg border border-dashed text-sm text-muted-foreground">Fewer than two numeric points</p>
</template>
