<script setup lang="ts" generic="T extends ChartRow">
import { GroupedBar, StackedBar } from '@unovis/ts'
import { VisAxis, VisGroupedBar, VisStackedBar, VisTooltip, VisXYContainer } from '@unovis/vue'
import { computed, ref } from 'vue'
import { ChartContainer } from '@/components/ui/chart'
import { cn } from '@/lib/utils'
import { chartConfig, niceTicks, plainNumber, seriesValue, thinTicks, useSeriesInteraction, useTickCount, type ChartRow, type SeriesSpec } from './chart'
import ChartInspect from './ChartInspect.vue'
import Legend from './Legend.vue'

const props = withDefaults(
  defineProps<{
    rows: T[]
    category: (row: T) => string
    series: SeriesSpec[]
    label: string
    stacked?: boolean
    orientation?: 'vertical' | 'horizontal'
    /** Series keys drawn below the baseline (e.g. regressions against improvements). */
    diverging?: string[]
    valueFormat?: (value: number) => string
    /** Fixed value-axis domain; diverging charts default to one symmetric around zero. */
    valueDomain?: [number, number]
    /** Horizontal charts: category labels wrap at this width (px) instead of widening the axis. */
    categoryWidth?: number
    /** Legend visibility; defaults to shown for more than one series (force it when one series' color encodes a state). */
    legend?: boolean
    hint?: string
    class?: string
    /** Tooltip and Inspect title of a row; defaults to its category. */
    rowLabel?: (row: T) => string
    /** 'legend' puts the Inspect menu at the end of the legend row instead of a toolbar row above the plot. */
    inspectPlacement?: 'top' | 'legend'
    onSelect?: (row: T, index: number) => void
  }>(),
  { stacked: true, orientation: 'vertical', diverging: () => [], valueFormat: plainNumber, legend: undefined, inspectPlacement: 'top' },
)

const host = ref<HTMLElement | null>(null)
const horizontal = computed(() => props.orientation === 'horizontal')
const ticks = useTickCount(host, 56)

type Indexed = { row: T; index: number }
const data = computed<Indexed[]>(() => props.rows.map((row, index) => ({ row, index })))
const signed = (key: string) => (d: Indexed) => (props.diverging.includes(key) ? -1 : 1) * seriesValue(d.row, key)
const accessors = computed(() => props.series.map((s) => signed(s.key)))
const colors = computed(() => props.series.map((s) => s.color))

const { tooltip, select, inspectItems } = useSeriesInteraction<T>({
  rows: () => props.rows,
  series: () => props.series,
  title: (row) => (props.rowLabel ? props.rowLabel(row) : props.category(row)),
  format: (value) => props.valueFormat(value),
  hint: () => props.hint,
  onSelect: () => props.onSelect,
})

// Horizontal bars get a row each; vertical labels thin out on narrow plots instead of colliding.
const tickValues = computed(() => {
  const indices = data.value.map((d) => d.index)
  return horizontal.value ? indices : thinTicks(indices, ticks.value)
})
const categoryLabel = (index: number) => {
  const row = props.rows[index]
  return row ? props.category(row) : ''
}
// The value axis ends on a round tick at or beyond the longest bar; diverging charts share one
// scale on both sides of zero so their lengths compare honestly.
const valueTicks = computed(() => {
  if (props.valueDomain) return niceTicks(props.valueDomain[0], props.valueDomain[1], 4)
  const totals = props.rows.map((row) => {
    const values = props.series.map((s) => (props.diverging.includes(s.key) ? -1 : 1) * seriesValue(row, s.key))
    const up = values.filter((v) => v > 0)
    const down = values.filter((v) => v < 0)
    const sum = (vs: number[]) => vs.reduce((a, b) => a + b, 0)
    return props.stacked ? [sum(down), sum(up)] : [Math.min(0, ...down), Math.max(0, ...up)]
  })
  const counts = props.rows.every((row) => props.series.every((s) => Number.isInteger(seriesValue(row, s.key))))
  let lo = Math.min(0, ...totals.map((t) => t[0]!))
  let hi = Math.max(0, ...totals.map((t) => t[1]!))
  if (props.diverging.length) {
    hi = Math.max(1, -lo, hi)
    lo = -hi
  }
  return niceTicks(lo, hi, props.diverging.length && ticks.value <= 6 ? 2 : 4, counts ? 1 : 0)
})
const domain = computed<[number, number]>(() => props.valueDomain ?? [valueTicks.value[0]!, valueTicks.value.at(-1)!])
const inspectTop = computed(() => !!props.onSelect && props.inspectPlacement === 'top')
const showLegend = computed(() => props.legend ?? props.series.length > 1)
const ROW = computed(() => (props.categoryWidth ? 44 : 36))
const height = computed(() => (horizontal.value ? `${props.rows.length * ROW.value + 36}px` : undefined))
const barSelector = computed(() => (props.stacked ? StackedBar.selectors.bar : GroupedBar.selectors.bar))
const events = computed(() => ({ [barSelector.value]: { click: (d: Indexed) => select(d.row) } }))
// Stacked bars bind a stack record ({ datum, stackIndex, ... }) to each path; grouped bars bind the row itself.
const triggers = computed(() => ({ [barSelector.value]: (d: Indexed | { datum: Indexed }) => tooltip(('datum' in d ? d.datum : d).row) }))
</script>

<template>
  <div class="flex min-w-0 flex-col gap-2" data-slot="bar-chart">
    <div v-if="inspectTop || $slots.header" class="flex min-h-7 flex-wrap items-center justify-between gap-2">
      <div class="min-w-0"><slot name="header" /></div>
      <ChartInspect v-if="inspectTop" :items="inspectItems(rows)" :label="label" @select="(i) => select(rows[i])" />
    </div>
    <div
      ref="host"
      role="img"
      :aria-label="label"
      :data-clickable="onSelect ? '' : undefined"
      data-slot="bar-plot"
      :class="onSelect && '[--vis-grouped-bar-cursor:pointer] [--vis-grouped-bar-hover-stroke-color:var(--foreground)] [--vis-grouped-bar-hover-stroke-width:1.5px] [--vis-stacked-bar-cursor:pointer] [--vis-stacked-bar-hover-stroke-color:var(--foreground)] [--vis-stacked-bar-hover-stroke-width:1.5px]'"
    >
      <ChartContainer :config="chartConfig(series)" :class="cn('aspect-auto w-full', !horizontal && 'h-[250px]', props.class)" :style="{ height }">
        <VisXYContainer
          :data="data"
          :margin="{ left: 4, right: 12, top: 8 }"
          :x-domain="horizontal ? domain : undefined"
          :y-domain="horizontal ? undefined : domain"
        >
          <VisStackedBar
            v-if="stacked"
            :x="(d: Indexed) => d.index"
            :y="accessors"
            :color="colors"
            :orientation="orientation"
            :rounded-corners="horizontal ? 6 : 4"
            :bar-max-width="horizontal ? 18 : 28"
            :bar-padding="0.2"
            :duration="300"
            :events="events"
          />
          <VisGroupedBar
            v-else
            :x="(d: Indexed) => d.index"
            :y="accessors"
            :color="colors"
            :orientation="orientation"
            :rounded-corners="4"
            :bar-max-width="28"
            :group-padding="0.2"
            :bar-padding="0.1"
            :duration="300"
            :events="events"
          />
          <VisAxis
            :type="horizontal ? 'y' : 'x'"
            :tick-values="tickValues"
            :tick-format="categoryLabel"
            :tick-line="false"
            :domain-line="false"
            :grid-line="false"
            :tick-text-width="horizontal ? categoryWidth : undefined"
            :tick-text-fit-mode="categoryWidth ? 'wrap' : undefined"
            :tick-text-separator="categoryWidth ? ['-', ' ', '/'] : undefined"
          />
          <VisAxis
            :type="horizontal ? 'x' : 'y'"
            :tick-values="valueTicks"
            :tick-format="(v: number) => valueFormat(Math.abs(v))"
            :tick-line="false"
            :domain-line="false"
          />
          <VisTooltip :triggers="triggers" />
        </VisXYContainer>
      </ChartContainer>
    </div>
    <div v-if="showLegend || (onSelect && !inspectTop)" class="flex flex-wrap items-center justify-between gap-2">
      <Legend v-if="showLegend" :items="series" />
      <div v-if="onSelect && !inspectTop" class="ml-auto">
        <ChartInspect :items="inspectItems(rows)" :label="label" @select="(i) => select(rows[i])" />
      </div>
    </div>
  </div>
</template>
