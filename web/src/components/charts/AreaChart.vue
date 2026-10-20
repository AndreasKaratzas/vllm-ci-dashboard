<script setup lang="ts" generic="T extends ChartRow">
import { StackedBar } from '@unovis/ts'
import { VisArea, VisAxis, VisLine, VisPlotband, VisStackedBar, VisTooltip, VisXYContainer } from '@unovis/vue'
import { useId } from 'reka-ui'
import { computed, ref } from 'vue'
import SegmentedSelect from '@/components/layout/SegmentedSelect.vue'
import { ChartContainer, ChartCrosshair, ChartTooltip } from '@/components/ui/chart'
import { CHART_GAP } from '@/lib/palette'
import { cn } from '@/lib/utils'
import {
  chartConfig,
  niceTicks,
  plainNumber,
  seriesValue,
  shortDay,
  thinTicks,
  timeTicks,
  useSeriesInteraction,
  useTickCount,
  type ChartRow,
  type SeriesSpec,
} from './chart'
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
    /** Explicit y-axis ticks, e.g. to label the bottom of a zoomed domain. */
    yTicks?: number[]
    ranges?: RangeOption[]
    /** Ticks on round UTC times (hours, midnights) instead of on the rows themselves. */
    timeAxis?: boolean
    /** Rows farther apart than this (in x units) are a collection gap: the line breaks and the gap is shaded. */
    maxGap?: number
    bars?: CompanionBars
    /**
     * Where the keyboard Inspect menu sits: a toolbar row above the plot, the end of the legend row,
     * or nowhere when the page already offers a keyboard path to every point (e.g. an evidence list).
     */
    inspectPlacement?: 'top' | 'legend' | 'none'
    hint?: string
    class?: string
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
// Areas stack on their baseline, so a domain that excludes zero fills from its floor rather than from zero.
const floor = computed(() => (props.yDomain && props.yDomain[0] > 0 ? props.yDomain[0] : 0))
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
const rangeItems = computed(() => (props.ranges ?? []).map((option) => ({ value: String(option.value), label: option.label })))
const rangeModel = computed({
  get: () => String(range.value),
  set: (value: string) => {
    if (value) range.value = Number(value)
  },
})

const barFormat = (value: number) => (props.bars?.yFormat ?? plainNumber)(value)
const barKeys = computed(() => new Set((props.bars?.series ?? []).map((s) => s.key)))

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
const inspectInLegend = computed(() => !!props.onSelect && props.inspectPlacement === 'legend')
const legendItems = computed(() => [...props.series, ...(props.bars?.series ?? [])])

// With companion bars both plots share fixed margins and an x domain padded by half a step,
// so every bar sits exactly under its point on the area.
const SHARED_MARGIN = { left: 44, right: 12 }
const xDomain = computed<[number, number] | undefined>(() => {
  if (!props.bars) return undefined
  const xs = visible.value.map(xValue)
  if (!xs.length) return undefined
  const steps = xs.slice(1).map((x, i) => Math.abs(x - xs[i]!)).filter((step) => step > 0)
  const half = (steps.length ? Math.min(...steps) : 1) / 2
  return [Math.min(...xs) - half, Math.max(...xs) + half]
})

// What the area and line draw. A collection gap is bracketed by two value-less points just inside
// it, so the area drops to the baseline and the line breaks instead of interpolating through it.
// With companion bars the first and last rows also repeat at the padded domain edges, so the area
// fills the plot; every point carries its row, so hover and click still resolve to it.
interface Plotted {
  row: T
  x: number
  gap?: true
}
const plotted = computed<Plotted[]>(() => {
  const out: Plotted[] = []
  visible.value.forEach((row, i) => {
    const x = xValue(row)
    const previous = visible.value[i - 1]
    if (previous && props.maxGap && x - xValue(previous) > props.maxGap) {
      const edge = (x - xValue(previous)) / 200
      out.push({ row: previous, x: xValue(previous) + edge, gap: true }, { row, x: x - edge, gap: true })
    }
    out.push({ row, x })
  })
  const domain = xDomain.value
  if (!domain || !out.length) return out
  return [{ row: out[0]!.row, x: domain[0] }, ...out, { row: out.at(-1)!.row, x: domain[1] }]
})
// Only a gap wide enough to hold its label is labelled.
const gaps = computed(() => {
  const span = (plotted.value.at(-1)?.x ?? 0) - (plotted.value[0]?.x ?? 0) || 1
  return plotted.value.flatMap((d, i) => {
    const next = plotted.value[i + 1]
    if (!d.gap || !next?.gap) return []
    return [{ from: d.x, to: next.x, label: (next.x - d.x) / span > 0.08 ? 'No data' : '' }]
  })
})
// Without a given domain the axis ends on a round tick at or above the highest point, so no peak rises past the top label.
const valueTicks = computed(() => {
  if (props.yTicks) return props.yTicks
  if (props.yDomain) return undefined
  const peak = Math.max(0, ...visible.value.flatMap((row) => props.series.map((s) => seriesValue(row, s.key))))
  const whole = visible.value.every((row) => props.series.every((s) => Number.isInteger(seriesValue(row, s.key))))
  return niceTicks(0, peak, 4, whole ? 1 : 0)
})
const valueDomain = computed<[number, number] | undefined>(() =>
  props.yDomain ?? (valueTicks.value ? [valueTicks.value[0]!, valueTicks.value.at(-1)!] : undefined),
)
const value = (key: string) => (d: Plotted) => (d.gap ? undefined : seriesValue(d.row, key))
const areaValue = (key: string) => (d: Plotted) => (d.gap ? undefined : seriesValue(d.row, key) - floor.value)

const xAxis = computed(() => {
  const xs = visible.value.map(xValue)
  if (props.timeAxis && xs.length) return timeTicks([xs[0]!, xs.at(-1)!], ticks.value)
  return { values: thinTicks(xs, ticks.value), label: props.xFormat }
})

// The sheet a click opens covers the plot without a pointer move (and a resize would redraw the
// crosshair where the pointer was), so the crosshair and its tooltip are retired here.
const crosshairRef = ref<{ component?: { hide: () => void } } | null>(null)
function openHovered() {
  selectHovered()
  crosshairRef.value?.component?.hide()
}

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
const barTicks = computed(() =>
  niceTicks(0, Math.max(0, ...visible.value.map((row) => barAccessors.value.reduce((sum, accessor) => sum + accessor(row), 0))), 2, 1),
)
const barEvents = { [StackedBar.selectors.bar]: { click: (row: T) => bar.select(row) } }
// Stacked bars bind a stack record, not the row, to each path.
const barTriggers = { [StackedBar.selectors.bar]: (d: { datum: T }) => bar.tooltip(d.datum) }
</script>

<template>
  <div class="flex min-w-0 flex-col gap-2" data-slot="area-chart">
    <div v-if="ranges?.length || inspectTop || $slots.header" class="flex min-h-7 flex-wrap items-center justify-end gap-2">
      <div v-if="$slots.header" class="mr-auto min-w-0"><slot name="header" /></div>
      <SegmentedSelect v-if="ranges?.length" v-model="rangeModel" :items="rangeItems" :label="`${label} range`" :show-label="false" class="w-36 sm:w-auto" />
      <ChartInspect v-if="inspectTop" :items="inspectItems(visible)" :label="label" @select="(i) => select(visible[i])" />
    </div>
    <div ref="host" role="img" :aria-label="label" :class="onSelect && 'cursor-pointer [&_svg_*]:cursor-pointer'" @click="openHovered">
      <ChartContainer :config="chartConfig(series)" :class="cn('aspect-auto h-[250px] w-full', props.class)" cursor>
        <VisXYContainer
          :data="plotted"
          :svg-defs="svgDefs"
          :margin="bars ? { ...SHARED_MARGIN, top: 8, bottom: 4 } : { left: 4, right: 12, top: 8 }"
          :auto-margin="!bars"
          :x-domain="xDomain"
          :y-domain="valueDomain"
        >
          <VisPlotband
            v-for="(gap, i) in gaps"
            :key="`gap-${i}`"
            axis="x"
            :from="gap.from"
            :to="gap.to"
            :color="CHART_GAP.fill"
            :label-text="gap.label"
            label-position="top-inside"
            :label-color="CHART_GAP.label"
            :label-size="11"
            :duration="0"
          />
          <VisArea
            v-for="s in series"
            :key="`area-${s.key}`"
            :x="(d: Plotted) => d.x"
            :y="areaValue(s.key)"
            :baseline="() => floor"
            :color="`url(#${gradientId(s.key)})`"
            curve-type="monotoneX"
            :duration="300"
          />
          <VisLine
            v-for="s in series"
            :key="`line-${s.key}`"
            :x="(d: Plotted) => d.x"
            :y="value(s.key)"
            :color="s.color"
            :line-width="2"
            curve-type="monotoneX"
            :duration="300"
          />
          <VisAxis
            v-if="!bars"
            type="x"
            :x="(d: Plotted) => d.x"
            :tick-values="xAxis.values"
            :tick-format="xAxis.label"
            :tick-line="false"
            :domain-line="false"
            :grid-line="false"
          />
          <VisAxis type="y" :num-ticks="valueTicks ? undefined : 4" :tick-values="valueTicks" :tick-format="yFormat" :tick-line="false" :domain-line="false" />
          <ChartTooltip />
          <ChartCrosshair
            ref="crosshairRef"
            :x="(d: Plotted) => d.x"
            :y="series.map((s) => value(s.key))"
            :template="(d: Plotted) => tooltip(d.row)"
            :onCrosshairMove="(x: number | Date | undefined, d: Plotted | undefined) => track(x, d?.row)"
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
        <VisXYContainer
          :data="visible"
          :margin="{ ...SHARED_MARGIN, top: 4, bottom: 24 }"
          :auto-margin="false"
          :x-domain="xDomain"
          :y-domain="[barTicks[0]!, barTicks.at(-1)!]"
        >
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
          <VisAxis type="x" :x="xValue" :tick-values="xAxis.values" :tick-format="xAxis.label" :tick-line="false" :domain-line="false" :grid-line="false" />
          <VisAxis type="y" :tick-values="barTicks" :tick-format="barFormat" :tick-line="false" :domain-line="false" />
          <VisTooltip :triggers="barTriggers" />
        </VisXYContainer>
      </ChartContainer>
    </div>
    <div v-if="legendItems.length > 1 || inspectInLegend" class="flex flex-wrap items-center justify-between gap-2">
      <Legend v-if="legendItems.length > 1" :items="legendItems" />
      <div v-if="inspectInLegend" class="ml-auto">
        <ChartInspect :items="inspectItems(visible)" :label="label" @select="(i) => select(visible[i])" />
      </div>
    </div>
  </div>
</template>
