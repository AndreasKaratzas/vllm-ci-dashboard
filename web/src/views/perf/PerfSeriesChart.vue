<script setup lang="ts">
import { computed } from 'vue'
import AreaChart from '@/components/charts/AreaChart.vue'
import { distinctTicks, shortDay } from '@/components/charts/chart'
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

// Finite points in payload order; fewer than two draw nothing.
const rows = computed<Row[]>(() =>
  perfSparkRows(props.series).map((point, index) => ({ point, index, x: Date.parse(String(point.date ?? '')), value: Number(point.value) })),
)
const spec = computed(() => [{ key: 'value', label: props.label, color: props.color }])
const day = (row: Row | undefined) => (row && Number.isFinite(row.x) ? shortDay(row.x) : '')
const rowLabel = (row: Row) => [row.point.build_number ? `Build #${row.point.build_number}` : '', day(row)].filter(Boolean).join(' · ')
// Throughput units would wrap every axis label; the card header names the unit.
const yFormat = (v: number) => perfValue(v, props.unit === 'tok/s' ? undefined : props.unit)
// Nightlies are irregularly spaced; plotting by index keeps evenly spaced, non-colliding date ticks.
const xFormat = (index: number) => day(rows.value[Math.round(index)])
// A padded domain around the values keeps small moves visible; ticks never repeat a rounded label.
const yDomain = computed<[number, number]>(() => {
  const values = rows.value.map((row) => row.value)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const pad = (max - min) * 0.15 || Math.abs(max) * 0.05 || 1
  return [min - pad, max + pad]
})
const yTicks = computed(() => distinctTicks(yDomain.value, 4, yFormat))
</script>

<template>
  <AreaChart
    v-if="rows.length >= 2"
    :rows="rows"
    :x="(row: Row) => row.index"
    :x-format="xFormat"
    :series="spec"
    :label="`${label} by nightly`"
    :row-label="rowLabel"
    :y-domain="yDomain"
    :y-ticks="yTicks"
    :y-format="yFormat"
    :class="props.class"
    hint="Click to inspect this nightly"
    data-testid="perf-chart"
    :on-select="(row: Row) => emit('select', row.point)"
  >
    <template v-if="$slots.header" #header><slot name="header" /></template>
  </AreaChart>
  <div v-else class="flex flex-col gap-2">
    <slot name="header" />
    <p class="flex h-24 items-center justify-center rounded-lg border border-dashed text-sm text-muted-foreground">Fewer than two numeric points</p>
  </div>
</template>
