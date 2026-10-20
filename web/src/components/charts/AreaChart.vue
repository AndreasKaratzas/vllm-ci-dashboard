<script setup lang="ts" generic="T extends ChartRow">
import { VisArea, VisAxis, VisLine, VisPlotband, VisXYContainer } from '@unovis/vue'
import { useId } from 'reka-ui'
import { computed, ref } from 'vue'
import WindowToggle from '@/components/layout/WindowToggle.vue'
import { ChartContainer, ChartCrosshair, ChartTooltip } from '@/components/ui/chart'
import { CHART_GAP } from '@/lib/palette'
import { cn } from '@/lib/utils'
import {
  chartConfig,
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

const props = withDefaults(
  defineProps<{
    rows: T[]
    x: (row: T) => number | Date
    series: SeriesSpec[]
    label: string
    rowLabel?: (row: T) => string
    yDomain?: [number, number]
    yFormat?: (value: number) => string
    ranges?: RangeOption[]
    /** Ticks on round UTC times (hours, midnights) instead of on the rows themselves. */
    timeAxis?: boolean
    /** Rows farther apart than this (in x units) are a collection gap: the line breaks and the gap is shaded. */
    maxGap?: number
    /** Set false when the page already offers the keyboard path to every point (e.g. an evidence list). */
    inspectable?: boolean
    hint?: string
    class?: string
    onSelect?: (row: T, index: number) => void
  }>(),
  { yFormat: plainNumber, inspectable: true },
)
const range = defineModel<number>('range', { default: 0 })

const host = ref<HTMLElement | null>(null)
const ticks = useTickCount(host)
const uid = useId().replace(/[^a-zA-Z0-9-_]/g, '')
const gradientId = (key: string) => `${uid}-${key}`

const visible = computed(() => (range.value > 0 ? props.rows.slice(-range.value) : props.rows))
const xValue = (row: T) => Number(props.x(row))
const titleOf = (row: T) => (props.rowLabel ? props.rowLabel(row) : shortDay(xValue(row)))
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

// A gap is bracketed by two value-less points just inside it, so the area drops to the
// baseline across the gap and the line breaks instead of interpolating through it.
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
  return out
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
const value = (key: string) => (d: Plotted) => (d.gap ? undefined : seriesValue(d.row, key))

const axis = computed(() => {
  const xs = visible.value.map(xValue)
  if (props.timeAxis && xs.length) return timeTicks([xs[0]!, xs.at(-1)!], ticks.value)
  return {
    values: thinTicks(xs, ticks.value),
    label: (v: number) => shortDay(v),
  }
})

const { track, tooltip, select, selectHovered, inspectItems } = useSeriesInteraction<T>({
  rows: () => props.rows,
  series: () => props.series,
  title: titleOf,
  format: (v) => props.yFormat(v),
  hint: () => props.hint,
  onSelect: () => props.onSelect,
})
// The sheet a click opens covers the plot without a pointer move (and a resize would redraw the
// crosshair where the pointer was), so the crosshair and its tooltip are retired here.
const crosshairRef = ref<{ component?: { hide: () => void } } | null>(null)
function openHovered() {
  selectHovered()
  crosshairRef.value?.component?.hide()
}
const rangeOptions = computed(() => (props.ranges ?? []).map((r) => ({ value: String(r.value), label: r.label })))
const rangeValue = computed({
  get: () => String(range.value),
  set: (v: string) => (range.value = Number(v)),
})
</script>

<template>
  <div class="flex min-w-0 flex-col gap-2" data-slot="area-chart">
    <div v-if="ranges?.length || (onSelect && inspectable)" class="flex items-center justify-end gap-2">
      <WindowToggle v-if="ranges?.length" v-model="rangeValue" :options="rangeOptions" :label="`${label} range`" />
      <ChartInspect v-if="onSelect && inspectable" :items="inspectItems(visible)" :label="label" @select="(i) => select(visible[i])" />
    </div>
    <div ref="host" role="img" :aria-label="label" :class="onSelect && 'cursor-pointer [&_svg_*]:cursor-pointer'" @click="openHovered">
      <ChartContainer :config="chartConfig(series)" :class="cn('aspect-auto h-[250px] w-full', props.class)" cursor>
        <VisXYContainer :data="plotted" :svg-defs="svgDefs" :margin="{ left: 4, right: 12, top: 8 }" :y-domain="yDomain">
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
            :y="value(s.key)"
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
            type="x"
            :x="(d: Plotted) => d.x"
            :tick-values="axis.values"
            :tick-format="axis.label"
            :tick-line="false"
            :domain-line="false"
            :grid-line="false"
          />
          <VisAxis type="y" :num-ticks="4" :tick-format="yFormat" :tick-line="false" :domain-line="false" />
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
    <Legend v-if="series.length > 1" :items="series" />
  </div>
</template>
