<script setup lang="ts">
import { VisAxis, VisPlotline, VisScatter, VisXYContainer } from '@unovis/vue'
import { computed, ref } from 'vue'
import { CLICKABLE_MARKS, niceTicks, shortDayTime, timeTicks, useTickCount, type InspectItem } from '@/components/charts/chart'
import ChartInspect from '@/components/charts/ChartInspect.vue'
import Legend, { type LegendItem } from '@/components/charts/Legend.vue'
import { tooltipHtml } from '@/components/charts/tooltip'
import { ChartContainer, ChartCrosshair, ChartTooltip } from '@/components/ui/chart'
import { minutes, sampleUrl, type Slowdown } from '@/domain/slowdowns'
import { SERIES } from '@/lib/palette'

interface Point {
  x: number
  minutes: number
  build: number
  slow: boolean
}

const props = defineProps<{ row: Slowdown }>()

const host = ref<HTMLElement | null>(null)
const ticks = useTickCount(host, 90)
const points = computed<Point[]>(() =>
  props.row.samples
    .map((sample) => ({ x: Date.parse(sample.createdAt), minutes: sample.minutes, build: sample.build, slow: sample.slow }))
    .filter((point) => Number.isFinite(point.x)),
)
const xDomain = computed<[number, number]>(() => {
  const xs = points.value.map((point) => point.x)
  return xs.length ? [Math.min(...xs), Math.max(...xs)] : [0, 1]
})
const xTicks = computed(() => timeTicks(xDomain.value, Math.min(ticks.value, 6)))
const yTicks = computed(() => niceTicks(0, Math.max(props.row.recentMins, ...points.value.map((point) => point.minutes)) * 1.05, 4))
const yDomain = computed<[number, number]>(() => [0, yTicks.value.at(-1)!])
const onsetX = computed(() => Date.parse(props.row.onset.createdAt))
const riseX = computed(() => {
  const rise = props.row.lastRise
  return rise && rise.number !== props.row.onset.number ? Date.parse(rise.createdAt) : null
})

const color = (point: Point) => (point.slow ? SERIES.secondary : SERIES.primary)
const LEGEND: LegendItem[] = [
  { key: 'before', label: 'Before the slowdown', color: SERIES.primary },
  { key: 'since', label: 'Since the slowdown', color: SERIES.secondary },
]
const describe = (point: Point) => ({
  title: `#${point.build} · ${shortDayTime(point.x)} UTC`,
  rows: [{ label: point.slow ? 'Since the slowdown' : 'Before the slowdown', value: minutes(point.minutes), color: color(point) }],
  hint: 'Click to open the build on Buildkite',
})

let hovered: Point | null = null
const track = (x: number | Date | undefined, point: Point | undefined) => {
  hovered = x === undefined ? null : (point ?? null)
}
const tooltip = (point: Point) => tooltipHtml(describe(point))
function open(point: Point | null | undefined) {
  const url = point && sampleUrl(props.row, point.build)
  if (url) window.open(url, '_blank', 'noopener')
}
const inspectItems = computed<InspectItem[]>(() => points.value.map((point) => ({ label: describe(point).title, detail: minutes(point.minutes) })))
</script>

<template>
  <div class="flex min-w-0 flex-col gap-2" data-slot="slowdown-chart">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <Legend :items="LEGEND" />
      <ChartInspect :items="inspectItems" label="Sampled builds" @select="(i) => open(points[i])" />
    </div>
    <div
      ref="host"
      role="img"
      :aria-label="`${row.name}: ${minutes(row.baselineMins)} before build #${row.onset.number}, ${minutes(row.recentMins)} since`"
      :class="CLICKABLE_MARKS"
      data-clickable
      data-slot="slowdown-plot"
      @click="open(hovered)"
    >
      <ChartContainer :config="{ before: { label: 'Before', color: SERIES.primary }, since: { label: 'Since', color: SERIES.secondary } }" class="aspect-auto h-[220px] w-full" cursor>
        <VisXYContainer :data="points" :margin="{ left: 4, right: 16, top: 20 }" :x-domain="xDomain" :y-domain="yDomain">
          <VisPlotline axis="y" :value="row.baselineMins" :color="SERIES.primary" :line-width="1.5" :line-style="[4, 4]" />
          <VisPlotline axis="y" :value="row.recentMins" :color="SERIES.secondary" :line-width="1.5" :line-style="[4, 4]" />
          <VisPlotline axis="x" :value="onsetX" :line-width="1" label-text="Slower from here" label-position="top-right" :label-offset-y="-12" :label-offset-x="6" :label-size="11" />
          <VisPlotline v-if="riseX !== null" axis="x" :value="riseX" :line-width="1" :line-style="[2, 3]" label-text="Latest rise" label-position="top-right" :label-offset-y="-12" :label-offset-x="6" :label-size="11" />
          <VisScatter :x="(p: Point) => p.x" :y="(p: Point) => p.minutes" :color="color" :size="7" stroke-color="var(--card)" :stroke-width="1.5" />
          <VisAxis type="x" :x="(p: Point) => p.x" :tick-values="xTicks.values" :tick-format="xTicks.label" :tick-line="false" :domain-line="false" :grid-line="false" />
          <VisAxis type="y" :tick-values="yTicks" :tick-format="(v: number) => `${v}m`" :tick-line="false" :domain-line="false" />
          <ChartTooltip />
          <ChartCrosshair :x="(p: Point) => p.x" :y="(p: Point) => p.minutes" :template="tooltip" :on-crosshair-move="track" :color="color" />
        </VisXYContainer>
      </ChartContainer>
    </div>
    <p class="text-xs text-muted-foreground">
      Dashed lines: the level before ({{ minutes(row.baselineMins) }}) and since ({{ minutes(row.recentMins) }}). Each dot is one completed main build where every job of the step passed.
    </p>
  </div>
</template>
