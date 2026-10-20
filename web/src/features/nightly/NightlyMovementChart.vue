<script setup lang="ts">
import { computed } from 'vue'
import BarChart from '@/components/charts/BarChart.vue'
import { shortDay, type SeriesSpec } from '@/components/charts/chart'
import EmptyState from '@/components/feedback/EmptyState.vue'
import type { Rec } from '@/domain/format'
import { nightlyFailureCount } from '@/domain/nightly'
import { statusVar, tint } from '@/lib/palette'

const props = withDefaults(
  defineProps<{
    /** Nightlies newest first, as published; the chart reads oldest to newest. */
    builds: Rec[]
    label?: string
    class?: string
    onSelect?: (build: Rec) => void
  }>(),
  { label: 'Nightly failure movement' },
)

// Both failure buckets are failures (red); recurring is the lighter shade so new ones stand out.
// Fixes are drawn below zero so failures and recoveries read as one movement.
const SERIES: SeriesSpec[] = [
  { key: 'new', label: 'New failure', color: statusVar('hard') },
  { key: 'recurring', label: 'Recurring failure', color: tint(statusVar('hard'), 60) },
  { key: 'fixed', label: 'Fixed', color: statusVar('pass') },
]

interface Row extends Record<string, unknown> {
  build: Rec
  new: number
  recurring: number
  fixed: number
}

const rows = computed<Row[]>(() =>
  [...props.builds].reverse().map((build) => ({
    build,
    new: nightlyFailureCount(build, 'new') ?? 0,
    recurring: nightlyFailureCount(build, 'recurring') ?? 0,
    fixed: nightlyFailureCount(build, 'fixed') ?? 0,
  })),
)
// Dates on the axis match the pass-rate chart; the tooltip and Inspect name the build.
const day = (row: Row) => {
  const ms = Date.parse(String(row.build.created_at || ''))
  return Number.isFinite(ms) ? shortDay(ms) : `#${row.build.number}`
}
const rowLabel = (row: Row) => `Nightly #${row.build.number} · ${day(row)}`
const select = computed(() => (props.onSelect ? (row: Row) => props.onSelect!(row.build) : undefined))
</script>

<template>
  <EmptyState
    v-if="!rows.length"
    title="No failure movement retained"
    description="No retained nightly has a comparable preceding nightly yet."
  />
  <BarChart
    v-else
    :rows="rows"
    :category="day"
    :row-label="rowLabel"
    inspect-placement="legend"
    :series="SERIES"
    :diverging="['fixed']"
    :label="label"
    :class="props.class"
    hint="Click for build evidence"
    :on-select="select"
    data-testid="nightly-movement-chart"
  />
</template>
