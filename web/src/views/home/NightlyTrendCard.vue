<script setup lang="ts">
import { computed } from 'vue'
import AreaChart, { type CompanionBars } from '@/components/charts/AreaChart.vue'
import { rateAxis, ratePercent, shortDay, type SeriesSpec } from '@/components/charts/chart'
import EmptyState from '@/components/feedback/EmptyState.vue'
import SegmentedSelect, { type SegmentedItem } from '@/components/layout/SegmentedSelect.vue'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useQueryState } from '@/composables/useQueryState'
import type { NightlyCounts } from '@/domain/home'
import { nightlyCountLabel } from '@/domain/nightly'
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
  label: 'Hard- and soft-failed job variants per nightly',
  series: [
    { key: 'hard', label: 'Hard-failed variants', color: statusVar('hard') },
    { key: 'soft', label: 'Soft-failed variants', color: statusVar('soft') },
  ],
}

const RANGE_KEYS = ['all', '30', '14', '7'] as const
type RangeKey = (typeof RANGE_KEYS)[number]
// A window only appears when it would hide some of the retained nightlies.
const ranges = computed<SegmentedItem[]>(() =>
  RANGE_KEYS.filter((key) => key === 'all' || Number(key) < rows.value.length).map((key) => ({ value: key, label: key === 'all' ? 'All' : `Last ${key}` })),
)
const rangeParam = useQueryState<RangeKey>('range', { default: 'all', allowed: RANGE_KEYS })
const range = computed({
  get: () => (rangeParam.value === 'all' ? 0 : Number(rangeParam.value)),
  set: (value: number) => (rangeParam.value = value ? (String(value) as RangeKey) : 'all'),
})
const shown = computed(() => (range.value ? Math.min(range.value, rows.value.length) : rows.value.length))
const axis = computed(() => rateAxis(props.series.map((row) => row.passRate * 100)))

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
        <span>{{ shown === rows.length ? `All ${nightlyCountLabel(shown, 'builds')}` : `Last ${shown} of ${nightlyCountLabel(rows.length, 'builds')}` }}</span>
        <span class="hidden @3xl/main:inline"> · hard and soft failures below</span>
      </CardDescription>
      <CardAction v-if="ranges.length > 1">
        <SegmentedSelect v-model="rangeParam" :items="ranges" label="Nightly range" :show-label="false" class="w-32 sm:w-auto" />
      </CardAction>
    </CardHeader>
    <CardContent class="px-3 sm:px-6">
      <EmptyState
        v-if="!rows.length"
        title="No nightly with test signal"
        description="No AMD nightly build summary has parsed test-group results yet."
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
        :y-domain="axis.domain"
        :y-ticks="axis.ticks"
        :y-format="ratePercent"
        inspect-placement="legend"
        label="AMD nightly pass rate"
        hint="Click for build evidence"
        :on-select="(row: Row) => emit('select', row.number)"
      />
    </CardContent>
  </Card>
</template>
