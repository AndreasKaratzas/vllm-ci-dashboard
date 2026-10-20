<script setup lang="ts">
import { computed } from 'vue'
import AreaChart, { type CompanionBars } from '@/components/charts/AreaChart.vue'
import { rateAxis, shortDay, shortDayTime, type SeriesSpec } from '@/components/charts/chart'
import EmptyState from '@/components/feedback/EmptyState.vue'
import SegmentedSelect from '@/components/layout/SegmentedSelect.vue'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useQueryState } from '@/composables/useQueryState'
import { integer, type Rec } from '@/domain/format'
import { TREND_WINDOWS, trendPoints, type TrendPoint, type TrendWindow } from '@/domain/mainCi'
import { SERIES, statusVar } from '@/lib/palette'

const props = defineProps<{ record: Rec | null }>()
const emit = defineEmits<{ select: [TrendPoint, string] }>()

const WINDOW_LABEL: Record<TrendWindow, string> = { '24h': '24 hours', '7d': '7 days', '30d': '30 days' }
const STEP: Record<TrendWindow, string> = { '24h': 'per hour', '7d': 'per 6 hours', '30d': 'per day' }
const items = TREND_WINDOWS.map((value) => ({ value, label: value, title: `Last ${WINDOW_LABEL[value]}` }))
const range = useQueryState<TrendWindow>('window', { default: '7d', allowed: TREND_WINDOWS })

interface Row extends Record<string, unknown> {
  point: TrendPoint
  at: number
  passRate: number
  hard: number
  soft: number
}
const points = computed(() => trendPoints(props.record, range.value))
// Intervals without a verdict (no main build ran AMD jobs) are gaps, not 0 %.
const rows = computed<Row[]>(() =>
  points.value.flatMap((point) => (point.passRate === null ? [] : [{ point, at: point.from, passRate: point.passRate, hard: point.hard, soft: point.soft }])),
)
const step = computed(() => (points.value[0] ? points.value[0].to - points.value[0].from : 1))
const axis = computed(() => rateAxis(rows.value.map((row) => row.passRate)))
const totals = computed(() => points.value.reduce((sum, point) => ({ builds: sum.builds + point.builds, hard: sum.hard + point.hard }), { builds: 0, hard: 0 }))

const PASS_RATE: SeriesSpec[] = [{ key: 'passRate', label: 'AMD pass rate', color: SERIES.primary }]
const FAILURES: CompanionBars = {
  label: 'Hard and soft AMD job failures per interval',
  series: [
    { key: 'hard', label: 'Hard failures', color: statusVar('hard') },
    { key: 'soft', label: 'Soft failures', color: statusVar('soft') },
  ],
}

function intervalLabel(point: TrendPoint): string {
  if (range.value === '30d') return shortDay(point.from)
  const end = new Date(point.to).toISOString().slice(11, 16)
  return `${shortDayTime(point.from)}–${end === '00:00' ? '24:00' : end} UTC`
}
const percent = (value: number) => `${Number(value.toFixed(1))}%`
const rowLabel = (row: Row) => `${intervalLabel(row.point)} · ${integer(row.point.builds)} builds`
</script>

<template>
  <Card class="gap-4 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300" data-testid="main-trend">
    <CardHeader>
      <CardTitle class="text-base">AMD pass rate on main</CardTitle>
      <CardDescription>
        <span>Last {{ WINDOW_LABEL[range] }} · {{ integer(totals.builds) }} builds, {{ STEP[range] }}</span>
        <span class="hidden @3xl/main:inline"> · hard and soft job failures below</span>
      </CardDescription>
      <CardAction>
        <SegmentedSelect v-model="range" :items="items" label="Main CI window" :show-label="false" />
      </CardAction>
    </CardHeader>
    <CardContent class="px-3 sm:px-6">
      <EmptyState v-if="!rows.length" title="No AMD verdict in this window" description="No completed main build ran AMD jobs in this window." />
      <AreaChart
        v-else
        :rows="rows"
        :x="(row: Row) => row.at"
        :row-label="rowLabel"
        :series="PASS_RATE"
        :bars="FAILURES"
        :y-domain="axis.domain"
        :y-ticks="axis.ticks"
        :y-format="percent"
        :max-gap="step * 1.5"
        time-axis
        inspect-placement="legend"
        label="AMD pass rate on main"
        hint="Click for the builds"
        :on-select="(row: Row) => emit('select', row.point, intervalLabel(row.point))"
      />
    </CardContent>
  </Card>
</template>
