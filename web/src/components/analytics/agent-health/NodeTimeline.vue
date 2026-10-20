<script setup lang="ts">
import { ChevronRight, RotateCcw, ZoomIn, ZoomOut } from '@lucide/vue'
import { useMediaQuery } from '@vueuse/core'
import { computed, ref } from 'vue'
import type { Detail } from '@/components/evidence/detail'
import EmptyState from '@/components/feedback/EmptyState.vue'
import Legend from '@/components/charts/Legend.vue'
import Timeline, { type TimelineSegment } from '@/components/charts/Timeline.vue'
import { Button } from '@/components/ui/button'
import { agentTimeline, clampTimelineRange, zoomTimeline, type AgentRun, type TimelineRange } from '@/domain/agentHealth'
import { duration, shortDate, value } from '@/domain/format'
import { statusVar } from '@/lib/palette'
import { RUN_LEGEND, runDetail, runTone, stateLabel } from './details'

// One node's failing runs in time; drag or zoom to narrow the window, select a run for its log.
const props = defineProps<{ runs: AgentRun[]; node: string; range?: TimelineRange | null }>()
const emit = defineEmits<{ drill: [Detail] }>()

const zoom = ref<TimelineRange | null>(props.range ?? null)
const timeline = computed(() => agentTimeline(props.runs, zoom.value))
const segments = computed<TimelineSegment[]>(() =>
  timeline.value.rows.map((row, index) => ({
    id: String(index),
    row: row.label,
    start: row.start,
    end: row.end,
    color: statusVar(runTone(row.run.state)),
    title: row.run.group,
    details: [
      { label: 'State', value: stateLabel(row.run.state) },
      { label: 'Queue', value: value(row.run.queue) },
      { label: 'Build', value: `${value(row.run.pipeline)} #${value(row.run.build_number)}` },
      { label: 'Started', value: shortDate(row.run.started_at) },
      { label: 'Duration', value: duration(row.mins) },
    ],
  })),
)
const legend = computed(() => RUN_LEGEND.filter((item) => timeline.value.rows.some((row) => runTone(row.run.state) === item.key)))
const open = (index: number) => timeline.value.rows[index] && emit('drill', runDetail(timeline.value.rows[index]!.run))
const zoomBy = (factor: number) => (zoom.value = zoomTimeline(zoom.value, timeline.value.bounds, factor))
// Below sm the lanes would be too narrow to read, so the same runs are listed newest first.
const wide = useMediaQuery('(min-width: 40rem)')
const MOBILE_RUNS = 25
const newest = computed(() => [...timeline.value.rows.keys()].reverse().slice(0, MOBILE_RUNS))
</script>

<template>
  <div class="flex min-w-0 flex-col gap-3" data-testid="agent-node-timeline">
    <template v-if="wide">
      <div class="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="icon-sm" aria-label="Zoom out (widen the time window)" title="Zoom out" @click="zoomBy(1.6)"><ZoomOut /></Button>
        <Button variant="outline" size="icon-sm" aria-label="Zoom in (narrow the time window)" title="Zoom in" @click="zoomBy(0.625)"><ZoomIn /></Button>
        <Button variant="outline" size="sm" aria-label="Reset the timeline to the full window" @click="zoom = null"><RotateCcw /> Reset</Button>
        <span class="text-xs text-muted-foreground">drag to select a range</span>
      </div>
      <Timeline
        :segments="segments"
        :x-domain="timeline.xMin !== null && timeline.xMax !== null ? [timeline.xMin, timeline.xMax] : undefined"
        :label="`Failing runs on ${node}`"
        hint="Click for the run"
        @select="(segment: TimelineSegment) => open(Number(segment.id))"
        @range="(min: number, max: number) => (zoom = clampTimelineRange(min, max, timeline.bounds))"
      >
        <template #empty>
          <EmptyState title="No retained failing runs for this node in the selected window and zoom." data-testid="agent-timeline-empty" />
        </template>
      </Timeline>
      <Legend v-if="segments.length" :items="legend" />
    </template>
    <template v-else>
      <ul v-if="timeline.rows.length" class="flex flex-col divide-y rounded-lg border" data-testid="agent-run-list">
        <li v-for="index in newest" :key="index">
          <button
            type="button"
            class="flex w-full items-center gap-3 px-3 py-2 text-left transition-colors outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 active:bg-accent/80"
            @click="open(index)"
          >
            <span class="size-2 shrink-0 rounded-full" :style="{ backgroundColor: statusVar(runTone(timeline.rows[index]!.run.state)) }" aria-hidden="true" />
            <span class="flex min-w-0 flex-1 flex-col">
              <span class="truncate text-sm font-medium">{{ timeline.rows[index]!.run.group }}</span>
              <span class="text-xs text-muted-foreground">
                {{ stateLabel(timeline.rows[index]!.run.state) }} · {{ shortDate(timeline.rows[index]!.run.started_at) }} · {{ duration(timeline.rows[index]!.mins) }}
              </span>
            </span>
            <ChevronRight class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          </button>
        </li>
        <li v-if="timeline.rows.length > MOBILE_RUNS" class="px-3 py-2 text-xs text-muted-foreground">+{{ timeline.rows.length - MOBILE_RUNS }} older runs in the table below</li>
      </ul>
      <EmptyState v-else title="No retained failing runs for this node in the selected window." />
    </template>
  </div>
</template>
