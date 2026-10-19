<script setup lang="ts">
import { computed } from 'vue'
import AreaChart, { type CompanionBars, type RangeOption } from '@/components/charts/AreaChart.vue'
import { shortDay, type SeriesSpec } from '@/components/charts/chart'
import EmptyState from '@/components/feedback/EmptyState.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useQueryState } from '@/composables/useQueryState'
import { passRateDomain, type NightlyCounts } from '@/domain/home'
import { SERIES, statusVar } from '@/lib/palette'

const props = defineProps<{ series: NightlyCounts[] }>()
const emit = defineEmits<{ select: [number] }>()

interface Row extends Record<string, unknown> {
  index: number
  number: number
  createdAt: string
  passRate: number
  hard: number
  soft: number
}

const rows = computed<Row[]>(() =>
  props.series.map((row, index) => ({
    index,
    number: row.number,
    createdAt: row.createdAt,
    passRate: Math.round(row.passRate * 1000) / 10,
    hard: row.hard,
    soft: row.soft,
  })),
)

const PASS_RATE: SeriesSpec[] = [{ key: 'passRate', label: 'Pass rate', color: SERIES.primary }]
const FAILURES: CompanionBars = {
  label: 'Hard and soft failures per nightly',
  series: [
    { key: 'hard', label: 'Hard failures', color: statusVar('hard') },
    { key: 'soft', label: 'Soft failures', color: statusVar('soft') },
  ],
}

const RANGE_KEYS = ['all', '14', '7'] as const
const rangeParam = useQueryState<(typeof RANGE_KEYS)[number]>('range', { default: 'all', allowed: RANGE_KEYS })
const ranges = computed<RangeOption[]>(() => {
  const count = rows.value.length
  const options = [{ value: 0, label: `All ${count}` }, ...[14, 7].filter((n) => n < count).map((n) => ({ value: n, label: `Last ${n}` }))]
  return options.length > 1 ? options : []
})
const range = computed({
  get: () => (rangeParam.value === 'all' || Number(rangeParam.value) >= rows.value.length ? 0 : Number(rangeParam.value)),
  set: (value: number) => (rangeParam.value = value ? (String(value) as '14' | '7') : 'all'),
})
const shown = computed(() => (range.value ? Math.min(range.value, rows.value.length) : rows.value.length))

const percent = (value: number) => `${Number(value.toFixed(1))}%`
const dayOf = (index: number) => {
  const row = rows.value[Math.round(index)]
  return row ? shortDay(Date.parse(row.createdAt)) : ''
}
const rowLabel = (row: Row) => `Nightly #${row.number} · ${dayOf(row.index)}`
</script>

<template>
  <Card class="gap-4 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300" data-testid="nightly-trend">
    <CardHeader>
      <CardTitle class="text-base">AMD nightly pass rate</CardTitle>
      <CardDescription>
        <span>Job variants passed in the last {{ shown }} retained nightlies</span>
        <span class="hidden @3xl/main:inline">, with hard and soft failures below</span>
      </CardDescription>
    </CardHeader>
    <CardContent class="px-3 sm:px-6">
      <EmptyState
        v-if="!rows.length"
        title="No nightly with test signal"
        description="No retained AMD nightly has parsed test-group results yet."
      />
      <AreaChart
        v-else
        v-model:range="range"
        :rows="rows"
        :x="(row: Row) => row.index"
        :x-format="dayOf"
        :row-label="rowLabel"
        :series="PASS_RATE"
        :bars="FAILURES"
        :y-domain="passRateDomain(series)"
        :y-format="percent"
        :ranges="ranges"
        label="AMD nightly pass rate"
        hint="Click for build evidence"
        :on-select="(row: Row) => emit('select', row.number)"
      />
    </CardContent>
  </Card>
</template>
