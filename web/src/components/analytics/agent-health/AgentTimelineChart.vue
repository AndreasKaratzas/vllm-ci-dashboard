<script setup lang="ts">
import { Axis, Timeline, Tooltip, XYContainer } from '@unovis/ts'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ChartContainer, type ChartConfig } from '@/components/ui/chart'
import type { TimelineRow } from '@/domain/agentHealth'
import { duration, shortDate, value } from '@/domain/format'

// @unovis/vue 1.7.1 ships VisTimeline without declared props, so the timeline
// is driven through the @unovis/ts classes directly.
const props = defineProps<{ rows: TimelineRow[]; xMin: number | null; xMax: number | null }>()
const emit = defineEmits<{ select: [min: number, max: number] }>()

const config = {
  soft: { label: 'Soft fail', color: '#e3a63a' },
  hard: { label: 'Hard fail', color: '#e06464' },
} satisfies ChartConfig

const ROW_HEIGHT = 26
const height = computed(() => Math.max(180, props.rows.length * ROW_HEIGHT + 48))
const host = ref<HTMLElement | null>(null)
let chart: XYContainer<TimelineRow> | null = null
let timeline: Timeline<TimelineRow> | null = null

function escape(text: unknown) {
  return String(text ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)
}
function tooltip(d: TimelineRow) {
  return (
    `<div style="display:flex;flex-direction:column;gap:2px;font-size:12px"><strong>${escape(d.run.group)}</strong>` +
    [
      'State: ' + d.run.state,
      'Pipeline: ' + d.run.pipeline,
      'Queue: ' + value(d.run.queue),
      'Start: ' + shortDate(d.run.started_at),
      'Duration: ' + duration(d.mins),
      d.run.url ? 'Click to open the BuildKite log' : 'Exact log unavailable',
    ]
      .map((line) => `<span>${escape(line)}</span>`)
      .join('') +
    '</div>'
  )
}

function containerConfig() {
  return {
    height: height.value,
    margin: { left: 4, right: 12 },
    xDomain: [props.xMin ?? 0, props.xMax ?? 1] as [number, number],
    components: [
      (timeline = new Timeline<TimelineRow>({
        x: (d) => d.start,
        lineDuration: (d) => d.end - d.start,
        lineRow: (d) => d.label,
        color: (d) => d.color,
        lineWidth: 14,
        lineCap: false,
        rowHeight: ROW_HEIGHT,
        showRowLabels: true,
        rowMaxLabelWidth: 260,
        rowLabelTextAlign: 'left',
        showEmptySegments: true,
        lineCursor: (d) => (d.run.url ? 'pointer' : 'default'),
        events: {
          [Timeline.selectors.line]: {
            click: (d: TimelineRow) => {
              if (d.run.url) window.open(d.run.url, '_blank', 'noopener')
            },
          },
        },
      })),
    ],
    xAxis: new Axis<TimelineRow>({
      numTicks: 5,
      tickLine: false,
      domainLine: false,
      tickFormat: (v) => shortDate(new Date(Number(v)).toISOString()),
    }),
    tooltip: new Tooltip({ triggers: { [Timeline.selectors.line]: tooltip } }),
  }
}

function render() {
  if (!props.rows.length) {
    chart?.destroy()
    chart = null
    return
  }
  if (!host.value) return
  if (chart) chart.updateContainer(containerConfig())
  else chart = new XYContainer<TimelineRow>(host.value, containerConfig(), props.rows)
  chart.setData(props.rows)
}

// Drag across the plot to zoom to that range, like classic; a plain click still opens the run log.
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
  if (Number.isFinite(a) && Number.isFinite(b) && b > a) emit('select', a, b)
}
function onDown(e: MouseEvent) {
  if (e.button !== 0 || !plotX(e.clientX)) return
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

watch(
  () => [props.rows, props.xMin, props.xMax],
  () => void nextTick(render),
  { immediate: true },
)
onBeforeUnmount(() => {
  endDrag()
  chart?.destroy()
})
</script>

<template>
  <p v-if="!rows.length" class="py-12 text-center text-sm text-muted-foreground" data-testid="agent-timeline-empty">
    No retained failing runs for this node in the selected window and zoom.
  </p>
  <ChartContainer v-else :config="config" class="aspect-auto w-full" :style="{ height: `${height}px` }">
    <div
      ref="host"
      class="relative h-full w-full select-none"
      data-testid="agent-timeline"
      :data-rows="rows.length"
      @mousedown="onDown"
      @click.capture="onClickCapture"
    >
      <div
        v-if="selection"
        class="pointer-events-none absolute top-0 z-10 h-full border-x border-primary/60 bg-primary/15"
        :style="{ left: `${selection.left}px`, width: `${selection.width}px` }"
        data-testid="agent-timeline-selection"
      />
    </div>
  </ChartContainer>
</template>
