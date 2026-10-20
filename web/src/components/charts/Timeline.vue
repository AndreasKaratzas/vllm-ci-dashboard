<script setup lang="ts">
import { Axis, Timeline, Tooltip, XYContainer } from '@unovis/ts'
import { useElementSize } from '@vueuse/core'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ChartContainer } from '@/components/ui/chart'
import type { PaletteColor } from '@/lib/palette'
import { distinctTicks, shortDay, shortDayTime, type InspectItem } from './chart'
import ChartInspect from './ChartInspect.vue'
import { useDragRange, type PlotPoint } from './dragRange'
import { tooltipHtml, type TooltipRow } from './tooltip'

export interface TimelineSegment {
  id: string
  row: string
  start: number
  end: number
  color: PaletteColor
  title: string
  details?: TooltipRow[]
}

// @unovis/vue 1.7.1 ships VisTimeline without declared props, so the timeline
// is driven through the @unovis/ts classes directly.
const props = defineProps<{
  segments: TimelineSegment[]
  label: string
  xDomain?: [number, number]
  /** Defaults to day labels, or day and time when the domain spans under two days. */
  xFormat?: (value: number) => string
  hint?: string
  onSelect?: (segment: TimelineSegment) => void
  /** Drag across the plot to choose a time range; a plain click still selects a segment. */
  onRange?: (min: number, max: number) => void
}>()

const ROW_HEIGHT = 26
const DAY_MS = 86_400_000
const rowCount = computed(() => new Set(props.segments.map((s) => s.row)).size)
// One lane per row plus the time axis, so no empty lane is drawn below the last row.
const height = computed(() => Math.max(1, rowCount.value) * ROW_HEIGHT + 40)
const host = ref<HTMLElement | null>(null)
const { width } = useElementSize(host)
let chart: XYContainer<TimelineSegment> | null = null
let timeline: Timeline<TimelineSegment> | null = null

function tooltip(d: TimelineSegment) {
  return tooltipHtml({ title: d.title, rows: d.details, hint: props.onSelect ? (props.hint ?? 'Click for evidence') : props.hint })
}

function containerConfig() {
  const starts = props.segments.map((s) => s.start)
  const ends = props.segments.map((s) => s.end)
  const xDomain = props.xDomain ?? ([Math.min(...starts), Math.max(...ends)] as [number, number])
  const format = props.xFormat ?? (xDomain[1] - xDomain[0] < 2 * DAY_MS ? shortDayTime : shortDay)
  const plotWidth = width.value || host.value?.clientWidth || 600
  const tickCount = Math.max(2, Math.floor(plotWidth / (format === shortDayTime ? 120 : 90)))
  return {
    height: height.value,
    margin: { left: 4, right: 12 },
    xDomain,
    components: [
      (timeline = new Timeline<TimelineSegment>({
        x: (d) => d.start,
        lineDuration: (d) => Math.max(0, d.end - d.start),
        lineRow: (d) => d.row,
        color: (d) => d.color,
        lineWidth: 14,
        lineCap: false,
        rowHeight: ROW_HEIGHT,
        showRowLabels: true,
        // Labels keep at most 40% of a narrow plot.
        rowMaxLabelWidth: Math.min(220, Math.round(plotWidth * 0.4)),
        rowLabelTextAlign: 'left',
        showEmptySegments: true,
        lineCursor: () => (props.onSelect ? 'pointer' : 'default'),
        events: { [Timeline.selectors.line]: { click: (d: TimelineSegment) => props.onSelect?.(d) } },
      })),
    ],
    xAxis: new Axis<TimelineSegment>({ tickValues: distinctTicks(xDomain, tickCount, format), tickLine: false, domainLine: false, tickFormat: (v) => format(Number(v)) }),
    tooltip: new Tooltip({ triggers: { [Timeline.selectors.line]: tooltip } }),
  }
}

function render() {
  if (!props.segments.length) {
    chart?.destroy()
    chart = null
    return
  }
  if (!host.value) return
  if (chart) chart.updateContainer(containerConfig())
  else chart = new XYContainer<TimelineSegment>(host.value, containerConfig(), props.segments)
  chart.setData(props.segments)
}

function toPlot(clientX: number): PlotPoint | null {
  const g = timeline?.element as SVGGraphicsElement | undefined
  const ctm = g?.getScreenCTM()
  if (!timeline || !ctm || !host.value) return null
  const [r0, r1] = timeline.xScale.range() as [number, number]
  const local = Math.max(r0, Math.min(r1, (clientX - ctm.e) / ctm.a))
  return { local, hostPx: local * ctm.a + ctm.e - host.value.getBoundingClientRect().left }
}
const { selection, onDown, onClickCapture } = useDragRange({
  toPlot,
  invert: (local) => Number(timeline?.xScale.invert(local)),
  onRange: () => props.onRange,
})

const inspectItems = computed<InspectItem[]>(() => props.segments.map((s) => ({ label: s.title, detail: s.row })))

watch(
  () => [props.segments, props.xDomain, Math.round(width.value / 40)],
  () => void nextTick(render),
  { immediate: true },
)
onBeforeUnmount(() => chart?.destroy())
</script>

<template>
  <div class="flex min-w-0 flex-col gap-2" data-slot="timeline">
    <div v-if="onSelect && segments.length" class="flex justify-end">
      <ChartInspect :items="inspectItems" :label="label" @select="(i) => segments[i] && onSelect?.(segments[i])" />
    </div>
    <slot v-if="!segments.length" name="empty" />
    <ChartContainer v-else :config="{}" class="aspect-auto w-full" :style="{ height: `${height}px` }">
      <div
        ref="host"
        role="img"
        :aria-label="label"
        class="relative h-full w-full select-none"
        data-testid="timeline"
        :data-rows="rowCount"
        @mousedown="onDown"
        @click.capture="onClickCapture"
      >
        <div
          v-if="selection"
          class="pointer-events-none absolute top-0 z-10 h-full border-x border-brand/60 bg-brand/15"
          :style="{ left: `${selection.left}px`, width: `${selection.width}px` }"
          data-testid="timeline-selection"
        />
      </div>
    </ChartContainer>
  </div>
</template>
