<script setup lang="ts" generic="T extends ChartRow">
import { StackedBar } from '@unovis/ts'
import { VisArea, VisAxis, VisLine, VisStackedBar, VisTooltip, VisXYContainer } from '@unovis/vue'
import { useId } from 'reka-ui'
import { computed, ref } from 'vue'
import { ChartContainer, ChartCrosshair, ChartTooltip } from '@/components/ui/chart'
import SegmentedSelect from '@/components/layout/SegmentedSelect.vue'
import { cn } from '@/lib/utils'
import { chartConfig, integerTicks, plainNumber, seriesValue, shortDay, thinTicks, useSeriesInteraction, useTickCount, type ChartRow, type SeriesSpec } from './chart'
import ChartInspect from './ChartInspect.vue'
import Legend from './Legend.vue'

export interface RangeOption {
  /** Number of trailing points to show; 0 shows every point. */
  value: number
  label: string
}

/** Stacked bars drawn under the area on the same x positions, e.g. failure counts under a pass rate. */
export interface CompanionBars {
  series: SeriesSpec[]
  label: string
  yFormat?: (value: number) => string
}

const props = withDefaults(
  defineProps<{
    rows: T[]
    x: (row: T) => number | Date
    series: SeriesSpec[]
    label: string
    rowLabel?: (row: T) => string
    xFormat?: (value: number) => string
    yDomain?: [number, number]
    yFormat?: (value: number) => string
    ranges?: RangeOption[]
    hint?: string
    class?: string
    bars?: CompanionBars
    /** Explicit y-axis ticks, e.g. to label the bottom of a zoomed domain. */
    yTicks?: number[]
    /** 'legend' puts the Inspect menu at the end of the legend row instead of a toolbar row above the plot. */
    inspectPlacement?: 'top' | 'legend'
    onSelect?: (row: T, index: number) => void
  }>(),
  { xFormat: shortDay, yFormat: plainNumber, inspectPlacement: 'top' },
)
const range = defineModel<number>('range', { default: 0 })

const host = ref<HTMLElement | null>(null)
const ticks = useTickCount(host)
const uid = useId().replace(/[^a-zA-Z0-9-_]/g, '')
const gradientId = (key: string) => `${uid}-${key}`

const visible = computed(() => (range.value > 0 ? props.rows.slice(-range.value) : props.rows))
const xValue = (row: T) => Number(props.x(row))
const titleOf = (row: T) => (props.rowLabel ? props.rowLabel(row) : props.xFormat(xValue(row)))
const svgDefs = computed(() =>
  props.series
    .map(
      (s) =>
        `<linearGradient id="${gradientId(s.key)}" x1="0" y1="0" x2="0" y2="1">` +
        `<stop offset="5%" style="stop-color:${s.color};stop-opacity:0.5" />` +
        `<stop offset="95%" style="stop-color:${s.color};stop-opacity:0.05" /></linearGradient>`,
    )
    .join(''),
)
const barFormat = (value: number) => (props.bars?.yFormat ?? plainNumber)(value)
const barKeys = computed(() => new Set((props.bars?.series ?? []).map((s) => s.key)))
const rangeItems = computed(() => (props.ranges ?? []).map((option) => ({ value: String(option.value), label: option.label })))
const rangeModel = computed({
  get: () => String(range.value),
  set: (value: string) => {
    if (value) range.value = Number(value)
  },
})

// The crosshair tooltip lists the companion bar values too, since both plots share the x-axis.
const { track, tooltip, select, selectHovered, inspectItems } = useSeriesInteraction<T>({
  rows: () => props.rows,
  series: () => [...props.series, ...(props.bars?.series ?? [])],
  title: titleOf,
  format: (value, s) => (barKeys.value.has(s.key) ? barFormat(value) : props.yFormat(value)),
  hint: () => props.hint,
  onSelect: () => props.onSelect,
})
const inspectTop = computed(() => !!props.onSelect && props.inspectPlacement === 'top')
const legendItems = computed(() => [...props.series, ...(props.bars?.series ?? [])])
const showLegend = computed(() => legendItems.value.length > 1 || (!!props.onSelect && !inspectTop.value))

// With companion bars both plots share fixed margins and an x domain padded by half a step,
// so every bar sits exactly under its point on the area.
const SHARED_MARGIN = { left: 44, right: 12 }
const xDomain = computed<[number, number] | undefined>(() => {
  if (!props.bars) return undefined
  const xs = visible.value.map(xValue)
  if (!xs.length) return undefined
  const min = Math.min(...xs)
  const max = Math.max(...xs)
  const gaps = xs.slice(1).map((x, i) => Math.abs(x - xs[i]!)).filter((gap) => gap > 0)
  const half = (gaps.length ? Math.min(...gaps) : 1) / 2
  return [min - half, max + half]
})
const xTicks = computed(() => thinTicks(visible.value.map(xValue), ticks.value))

// The padded domain would leave the area short of both plot edges, so the first and last rows are
// repeated at the edges; those marks carry their row, so hover and click still resolve to it.
interface Mark {
  x: number
  row: T
}
const marks = computed<Mark[]>(() => {
  const points = visible.value.map((row) => ({ x: xValue(row), row }))
  const domain = xDomain.value
  if (!domain || !points.length) return points
  return [{ x: domain[0], row: points[0]!.row }, ...points, { x: domain[1], row: points.at(-1)!.row }]
})
const bar = useSeriesInteraction<T>({
  rows: () => props.rows,
  series: () => props.bars?.series ?? [],
  title: titleOf,
  format: barFormat,
  hint: () => props.hint,
  onSelect: () => props.onSelect,
})
const barAccessors = computed(() => (props.bars?.series ?? []).map((s) => (row: T) => seriesValue(row, s.key)))
// Failure counts are whole numbers; keep the bar axis off fractional ticks.
const barScale = computed(() =>
  integerTicks(0, Math.max(0, ...visible.value.map((row) => barAccessors.value.reduce((sum, accessor) => sum + accessor(row), 0))), 2),
)
const barEvents = { [StackedBar.selectors.bar]: { click: (row: T) => bar.select(row) } }
const barTriggers = { [StackedBar.selectors.bar]: (row: T) => bar.tooltip(row) }
</script>

<template>
  <div class="flex min-w-0 flex-col gap-2" data-slot="area-chart">
    <div v-if="ranges?.length || inspectTop" class="flex items-center justify-end gap-2">
      <SegmentedSelect v-if="ranges?.length" v-model="rangeModel" :items="rangeItems" :label="`${label} range`" :show-label="false" class="w-36 sm:w-auto" />
      <ChartInspect v-if="inspectTop" :items="inspectItems(visible)" :label="label" @select="(i) => select(visible[i])" />
    </div>
    <div ref="host" role="img" :aria-label="label" :class="onSelect && 'cursor-pointer'" @click="selectHovered">
      <ChartContainer :config="chartConfig(series)" :class="cn('aspect-auto h-[250px] w-full', props.class)" cursor>
        <VisXYContainer
          :data="marks"
          :svg-defs="svgDefs"
          :margin="bars ? { ...SHARED_MARGIN, top: 8, bottom: 4 } : { left: 4, right: 12, top: 8 }"
          :auto-margin="!bars"
          :x-domain="xDomain"
          :y-domain="yDomain"
        >
          <VisArea
            v-for="s in series"
            :key="`area-${s.key}`"
            :x="(m: Mark) => m.x"
            :y="(m: Mark) => seriesValue(m.row, s.key)"
            :color="`url(#${gradientId(s.key)})`"
            curve-type="monotoneX"
            :duration="300"
          />
          <VisLine
            v-for="s in series"
            :key="`line-${s.key}`"
            :x="(m: Mark) => m.x"
            :y="(m: Mark) => seriesValue(m.row, s.key)"
            :color="s.color"
            :line-width="2"
            curve-type="monotoneX"
            :duration="300"
          />
          <VisAxis v-if="!bars" type="x" :tick-values="xTicks" :tick-format="xFormat" :tick-line="false" :domain-line="false" :grid-line="false" />
          <VisAxis type="y" :num-ticks="4" :tick-values="yTicks" :tick-format="yFormat" :tick-line="false" :domain-line="false" />
          <ChartTooltip />
          <ChartCrosshair
            :template="(m: Mark) => tooltip(m.row)"
            :on-crosshair-move="(x: number | Date | undefined, m: Mark | undefined) => track(x, m?.row)"
            :color="series.map((s) => s.color)"
          />
        </VisXYContainer>
      </ChartContainer>
    </div>
    <div
      v-if="bars"
      role="img"
      :aria-label="bars.label"
      :class="cn('mt-1 border-t border-dashed pt-2', onSelect && '[&_[data-vis-xy-container]_path]:cursor-pointer')"
      data-slot="area-chart-bars"
    >
      <ChartContainer :config="chartConfig(bars.series)" class="aspect-auto h-[120px] w-full">
        <VisXYContainer :data="visible" :margin="{ ...SHARED_MARGIN, top: 4, bottom: 24 }" :auto-margin="false" :x-domain="xDomain" :y-domain="barScale.domain">
          <VisStackedBar
            :x="xValue"
            :y="barAccessors"
            :color="bars.series.map((s) => s.color)"
            :rounded-corners="4"
            :bar-max-width="28"
            :bar-padding="0.2"
            :duration="300"
            :events="barEvents"
          />
          <VisAxis type="x" :x="xValue" :tick-values="xTicks" :tick-format="xFormat" :tick-line="false" :domain-line="false" :grid-line="false" />
          <VisAxis type="y" :tick-values="barScale.ticks" :tick-format="bars.yFormat ?? plainNumber" :tick-line="false" :domain-line="false" />
          <VisTooltip :triggers="barTriggers" />
        </VisXYContainer>
      </ChartContainer>
    </div>
    <div v-if="showLegend" class="flex flex-wrap items-center justify-between gap-2">
      <Legend v-if="legendItems.length > 1" :items="legendItems" />
      <div v-if="onSelect && !inspectTop" class="ml-auto">
        <ChartInspect :items="inspectItems(visible)" :label="label" @select="(i) => select(visible[i])" />
      </div>
    </div>
  </div>
</template>
