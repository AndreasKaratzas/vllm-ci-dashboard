<script setup lang="ts">
import { Axis, Timeline, Tooltip, XYContainer } from '@unovis/ts'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ChartContainer } from '@/components/ui/chart'
import type { PaletteColor } from '@/lib/palette'
import { shortDay, type InspectItem } from './chart'
import ChartInspect from './ChartInspect.vue'
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
const props = withDefaults(
  defineProps<{
    segments: TimelineSegment[]
    label: string
    xDomain?: [number, number]
    xFormat?: (value: number) => string
    hint?: string
    onSelect?: (segment: TimelineSegment) => void
    /** Drag across the plot to choose a time range; a plain click still selects a segment. */
    onRange?: (min: number, max: number) => void
  }>(),
  { xFormat: shortDay },
)

const ROW_HEIGHT = 26
const rowCount = computed(() => new Set(props.segments.map((s) => s.row)).size)
const height = computed(() => Math.max(160, rowCount.value * ROW_HEIGHT + 48))
const host = ref<HTMLElement | null>(null)
let chart: XYContainer<TimelineSegment> | null = null
let timeline: Timeline<TimelineSegment> | null = null

function tooltip(d: TimelineSegment) {
  return tooltipHtml({ title: d.title, rows: d.details, hint: props.onSelect ? (props.hint ?? 'Click for evidence') : props.hint })
}

function containerConfig() {
  const starts = props.segments.map((s) => s.start)
  const ends = props.segments.map((s) => s.end)
  return {
    height: height.value,
    margin: { left: 4, right: 12 },
    xDomain: props.xDomain ?? ([Math.min(...starts), Math.max(...ends)] as [number, number]),
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
        rowMaxLabelWidth: 220,
        rowLabelTextAlign: 'left',
        showEmptySegments: true,
        lineCursor: () => (props.onSelect ? 'pointer' : 'default'),
        events: { [Timeline.selectors.line]: { click: (d: TimelineSegment) => props.onSelect?.(d) } },
      })),
    ],
    xAxis: new Axis<TimelineSegment>({ numTicks: Math.max(2, Math.floor((host.value?.clientWidth ?? 400) / 90)), tickLine: false, domainLine: false, tickFormat: (v) => props.xFormat(Number(v)) }),
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

const selection = ref<{ left: number; width: number } | null>(null)
let dragStart: number | null = null
let dragMoved = false
let suppressClick = false

function plotX(clientX: number) {
  const g = timeline?.element as SVGGraphicsElement | undefined
  const ctm = g?.getScreenCTM()
  if (!timeline || !ctm || !host.value) return null
  const [r0, r1] = timeline.xScale.range() as [number, number]
  const local = Math.max(r0, Math.min(r1, (clientX - ctm.e) / ctm.a))
  return { local, hostPx: local * ctm.a + ctm.e - host.value.getBoundingClientRect().left }
}
function onMove(e: MouseEvent) {
  if (dragStart === null) return
  const start = plotX(dragStart)
  const end = plotX(e.clientX)
  if (!start || !end) return
  if (Math.abs(e.clientX - dragStart) > 4) dragMoved = true
  if (dragMoved) selection.value = { left: Math.min(start.hostPx, end.hostPx), width: Math.abs(end.hostPx - start.hostPx) }
}
function endDrag() {
  selection.value = null
  window.removeEventListener('mousemove', onMove)
  window.removeEventListener('mouseup', onUp)
}
function onUp(e: MouseEvent) {
  if (dragStart === null) return
  const start = plotX(dragStart)
  const end = plotX(e.clientX)
  dragStart = null
  endDrag()
  if (!dragMoved || !start || !end || !timeline) return
  suppressClick = true
  const a = Number(timeline.xScale.invert(Math.min(start.local, end.local)))
  const b = Number(timeline.xScale.invert(Math.max(start.local, end.local)))
  if (Number.isFinite(a) && Number.isFinite(b) && b > a) props.onRange?.(a, b)
}
function onDown(e: MouseEvent) {
  if (!props.onRange || e.button !== 0 || !plotX(e.clientX)) return
  dragStart = e.clientX
  dragMoved = false
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}
function onClickCapture(e: MouseEvent) {
  if (!suppressClick) return
  suppressClick = false
  e.stopPropagation()
  e.preventDefault()
}

const inspectItems = computed<InspectItem[]>(() => props.segments.map((s) => ({ label: s.title, detail: s.row })))

watch(
  () => [props.segments, props.xDomain],
  () => void nextTick(render),
  { immediate: true },
)
onBeforeUnmount(() => {
  endDrag()
  chart?.destroy()
})
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
