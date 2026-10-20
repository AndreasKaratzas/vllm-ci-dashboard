<script setup lang="ts" generic="T extends ChartRow">
import { GroupedBar, StackedBar } from '@unovis/ts'
import { VisAxis, VisGroupedBar, VisStackedBar, VisTooltip, VisXYContainer } from '@unovis/vue'
import { computed, ref } from 'vue'
import { ChartContainer } from '@/components/ui/chart'
import { cn } from '@/lib/utils'
import { chartConfig, plainNumber, seriesValue, thinTicks, useSeriesInteraction, useTickCount, type ChartRow, type SeriesSpec } from './chart'
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
    legend?: boolean
    hint?: string
    class?: string
    onSelect?: (row: T, index: number) => void
  }>(),
  { stacked: true, orientation: 'vertical', diverging: () => [], valueFormat: plainNumber, legend: true },
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
  title: (row) => props.category(row),
  format: (value) => props.valueFormat(value),
  hint: () => props.hint,
  onSelect: () => props.onSelect,
})

/** The smallest 1/2/5 x 10^n step that splits `max` into at most `parts` intervals. */
function niceStep(max: number, parts: number) {
  const raw = Math.max(max, 1) / Math.max(1, parts)
  const power = 10 ** Math.floor(Math.log10(raw))
  return ([1, 2, 5, 10].find((m) => m * power >= raw) ?? 10) * power
}
// Diverging charts share one scale on both sides of zero so their lengths compare honestly.
const domain = computed<[number, number] | undefined>(() => {
  if (props.valueDomain) return props.valueDomain
  if (!props.diverging.length) return undefined
  const extent = Math.max(1, ...props.rows.flatMap((row) => props.series.map((s) => Math.abs(seriesValue(row, s.key)))))
  const step = niceStep(extent, 4)
  const edge = Math.ceil(extent / step) * step
  return [-edge, edge]
})
const valueTicks = computed(() => {
  const range = domain.value
  if (!range) return undefined
  const step = niceStep(Math.max(Math.abs(range[0]), Math.abs(range[1])), ticks.value > 6 ? 4 : 2)
  const values: number[] = []
  for (let v = Math.ceil(range[0] / step) * step; v <= range[1] + 1e-9; v += step) values.push(Math.round(v * 1e6) / 1e6)
  return values
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
const ROW = computed(() => (props.categoryWidth ? 44 : 36))
const height = computed(() => (horizontal.value ? `${props.rows.length * ROW.value + 36}px` : undefined))
const barSelector = computed(() => (props.stacked ? StackedBar.selectors.bar : GroupedBar.selectors.bar))
const events = computed(() => ({ [barSelector.value]: { click: (d: Indexed) => select(d.row) } }))
const triggers = computed(() => ({ [barSelector.value]: (d: Indexed) => tooltip(d.row) }))
</script>

<template>
  <div class="flex min-w-0 flex-col gap-2" data-slot="bar-chart">
    <div v-if="onSelect || $slots.header" class="flex min-h-7 flex-wrap items-center justify-between gap-2">
      <div class="min-w-0"><slot name="header" /></div>
      <ChartInspect v-if="onSelect" :items="inspectItems(rows)" :label="label" @select="(i) => select(rows[i])" />
    </div>
    <div
      ref="host"
      role="img"
      :aria-label="label"
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
            :num-ticks="valueTicks ? undefined : 4"
            :tick-values="valueTicks"
            :tick-format="(v: number) => valueFormat(Math.abs(v))"
            :tick-line="false"
            :domain-line="false"
          />
          <VisTooltip :triggers="triggers" />
        </VisXYContainer>
      </ChartContainer>
    </div>
    <Legend v-if="legend && series.length > 1" :items="series" />
  </div>
</template>
