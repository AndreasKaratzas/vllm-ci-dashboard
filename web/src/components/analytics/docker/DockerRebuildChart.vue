<script setup lang="ts">
import { Scale } from '@unovis/ts'
import { VisAxis, VisPlotline, VisScatter, VisXYContainer } from '@unovis/vue'
import { computed, ref } from 'vue'
import { CLICKABLE_MARKS, shortDayTime, timeTicks, useTickCount, type InspectItem } from '@/components/charts/chart'
import ChartInspect from '@/components/charts/ChartInspect.vue'
import Legend, { type LegendItem } from '@/components/charts/Legend.vue'
import { tooltipHtml } from '@/components/charts/tooltip'
import { ChartContainer, ChartCrosshair, ChartTooltip } from '@/components/ui/chart'
import { CACHE_LABELS, FULL_REBUILD_MIN_S, REUSED_MAX_S, type DockerJob, type DockerRange, type RebuildPoint } from '@/domain/dockerBuilds'
import { seconds } from '@/domain/format'
import { SERIES } from '@/lib/palette'
import { sourceLabel } from './details'

const props = defineProps<{ points: RebuildPoint[]; range: DockerRange }>()
const emit = defineEmits<{ open: [job: DockerJob] }>()

const host = ref<HTMLElement | null>(null)
const ticks = useTickCount(host, 90)
const xDomain = computed<[number, number]>(() => [props.range.startMs, props.range.endMs])
const xTicks = computed(() => timeTicks(xDomain.value, Math.min(ticks.value, 7)))

// Rebuilds span ten minutes to several hours, so minutes go on a log axis.
const MINUTE_TICKS = [10, 20, 30, 60, 120, 240, 360, 480, 720, 1440]
const floorMinutes = REUSED_MAX_S / 60
const yDomain = computed<[number, number]>(() => {
  const peak = Math.max(0, ...props.points.map((point) => point.runS / 60))
  return [floorMinutes, MINUTE_TICKS.find((tick) => tick >= peak && tick >= 240) ?? peak * 1.1]
})
const yTicks = computed(() => MINUTE_TICKS.filter((tick) => tick <= yDomain.value[1]))
const yScale = Scale.scaleLog()

const color = (point: RebuildPoint) => (point.cls === 'full' ? SERIES.secondary : SERIES.primary)
const LEGEND: LegendItem[] = [
  { key: 'partial', label: `${CACHE_LABELS.partial} (10 min to 2 h)`, color: SERIES.primary },
  { key: 'full', label: `${CACHE_LABELS.full} (2 h or more)`, color: SERIES.secondary },
]
const describe = (point: RebuildPoint) => ({
  title: `#${point.job.build} · ${shortDayTime(point.job.createdMs)} UTC`,
  rows: [
    { label: CACHE_LABELS[point.cls], value: seconds(point.runS), color: color(point) },
    { label: 'Source', value: `${sourceLabel(point.job.source)} · ${point.job.pipeline}` },
  ],
  hint: 'Click for the job',
})

let hovered: RebuildPoint | null = null
const track = (x: number | Date | undefined, point: RebuildPoint | undefined) => {
  hovered = x === undefined ? null : (point ?? null)
}
const tooltip = (point: RebuildPoint) => tooltipHtml(describe(point))
function open(point: RebuildPoint | null | undefined) {
  if (point) emit('open', point.job)
}
const inspectItems = computed<InspectItem[]>(() => props.points.map((point) => ({ label: describe(point).title, detail: `${CACHE_LABELS[point.cls]}, ${seconds(point.runS)}` })))
const fullLine = FULL_REBUILD_MIN_S / 60
</script>

<template>
  <div class="flex min-w-0 flex-col gap-2" data-slot="docker-rebuild-chart">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <Legend :items="LEGEND" />
      <ChartInspect :items="inspectItems" label="Base image rebuilds" @select="(i) => open(points[i])" />
    </div>
    <div
      ref="host"
      role="img"
      :aria-label="`${points.length} base image rebuilds over the window, run time on a log scale`"
      :class="CLICKABLE_MARKS"
      data-clickable
      data-slot="docker-rebuild-plot"
      @click="open(hovered)"
    >
      <ChartContainer :config="{ partial: { label: CACHE_LABELS.partial, color: SERIES.primary }, full: { label: CACHE_LABELS.full, color: SERIES.secondary } }" class="aspect-auto h-[260px] w-full" cursor>
        <VisXYContainer :data="points" :margin="{ left: 4, right: 16, top: 12 }" :x-domain="xDomain" :y-domain="yDomain" :y-scale="yScale">
          <VisPlotline axis="y" :value="fullLine" :line-width="1" :line-style="[4, 4]" label-text="Full rebuild from 2 h" label-position="bottom-left" :label-size="11" :label-offset-y="6" :label-offset-x="4" />
          <VisScatter :x="(p: RebuildPoint) => p.job.createdMs" :y="(p: RebuildPoint) => p.runS / 60" :color="color" :size="8" stroke-color="var(--card)" :stroke-width="2" :duration="0" />
          <VisAxis type="x" :x="(p: RebuildPoint) => p.job.createdMs" :tick-values="xTicks.values" :tick-format="xTicks.label" :tick-line="false" :domain-line="false" :grid-line="false" />
          <VisAxis type="y" :tick-values="yTicks" :tick-format="(v: number) => seconds(v * 60)" :tick-line="false" :domain-line="false" />
          <ChartTooltip />
          <ChartCrosshair :x="(p: RebuildPoint) => p.job.createdMs" :y="(p: RebuildPoint) => p.runS / 60" :template="tooltip" :on-crosshair-move="track" :color="color" />
        </VisXYContainer>
      </ChartContainer>
    </div>
  </div>
</template>
