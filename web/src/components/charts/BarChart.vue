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
    /** Whole-number value ticks, for counts. */
    integerTicks?: boolean
    hint?: string
    class?: string
    onSelect?: (row: T, index: number) => void
  }>(),
  { stacked: true, orientation: 'vertical', diverging: () => [], valueFormat: plainNumber },
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

// Horizontal bars get a row each; vertical labels thin out on narrow plots instead of colliding.
const tickValues = computed(() => {
  const indices = data.value.map((d) => d.index)
  return horizontal.value ? indices : thinTicks(indices, ticks.value)
})
const categoryLabel = (index: number) => {
  const row = props.rows[index]
  return row ? props.category(row) : ''
}
const valueTicks = computed(() => {
  if (!props.integerTicks) return undefined
  const sums = data.value.map((d) => accessors.value.map((accessor) => accessor(d)))
  const max = Math.max(0, ...sums.map((values) => (props.stacked ? values.filter((v) => v > 0).reduce((a, b) => a + b, 0) : Math.max(0, ...values))))
  const min = Math.min(0, ...sums.map((values) => (props.stacked ? values.filter((v) => v < 0).reduce((a, b) => a + b, 0) : Math.min(0, ...values))))
  const step = Math.max(1, Math.ceil((max - min) / 4))
  const ticks: number[] = []
  for (let tick = Math.ceil(min / step) * step; tick <= max; tick += step) ticks.push(tick)
  return ticks
})
const height = computed(() => (horizontal.value ? `${Math.max(160, props.rows.length * 32 + 40)}px` : undefined))
const barSelector = computed(() => (props.stacked ? StackedBar.selectors.bar : GroupedBar.selectors.bar))
const events = computed(() => ({ [barSelector.value]: { click: (d: Indexed) => select(d.row) } }))
const triggers = computed(() => ({ [barSelector.value]: (d: Indexed) => tooltip(d.row) }))
</script>

<template>
  <div class="flex min-w-0 flex-col gap-2" data-slot="bar-chart">
    <div v-if="onSelect" class="flex justify-end">
      <ChartInspect :items="inspectItems(rows)" :label="label" @select="(i) => select(rows[i])" />
    </div>
    <div ref="host" role="img" :aria-label="label" :class="onSelect && '[&_[data-vis-xy-container]_rect]:cursor-pointer'">
      <ChartContainer :config="chartConfig(series)" :class="cn('aspect-auto w-full', !horizontal && 'h-[250px]', props.class)" :style="{ height }">
        <VisXYContainer :data="data" :margin="{ left: 4, right: 12, top: 8 }">
          <VisStackedBar
            v-if="stacked"
            :x="(d: Indexed) => d.index"
            :y="accessors"
            :color="colors"
            :orientation="orientation"
            :rounded-corners="4"
            :bar-max-width="28"
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
          />
          <VisAxis :type="horizontal ? 'x' : 'y'" :num-ticks="4" :tick-values="valueTicks" :tick-format="(v: number) => valueFormat(Math.abs(v))" :tick-line="false" :domain-line="false" />
          <VisTooltip :triggers="triggers" />
        </VisXYContainer>
      </ChartContainer>
    </div>
    <Legend v-if="series.length > 1" :items="series" />
  </div>
</template>
