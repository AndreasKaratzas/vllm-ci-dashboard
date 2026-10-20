<script setup lang="ts">
import { Activity, ChevronRight, ExternalLink, ListChecks } from '@lucide/vue'
import { computed } from 'vue'
import AreaChart from '@/components/charts/AreaChart.vue'
import type { Detail } from '@/components/evidence/detail'
import EmptyState from '@/components/feedback/EmptyState.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import { num, type ColumnSpec } from '@/components/table/columns'
import DataTable from '@/components/table/DataTable.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { integer, type Rec } from '@/domain/format'
import { OMNI_RANGE_WINDOWS, type HistoryPoint, type LiveFact, type LiveQueue, type OmniDetail, type OmniView } from '@/domain/omni'
import { hardwareColor, SERIES } from '@/lib/palette'
import { PHONE_COLUMNS, collectionGap, countLabel, kpiValue, sentenceCase, toColumns, toDetail } from './present'

const props = defineProps<{ view: OmniView }>()
const emit = defineEmits<{ show: [Detail] }>()
const open = (detail: OmniDetail) => emit('show', toDetail(detail))

const live = computed(() => props.view.live)
const range = computed(() => OMNI_RANGE_WINDOWS.find((w) => w.id === props.view.omniRange)!)

const trend = (values: (number | null)[]) => {
  const kept = values.filter((v): v is number => v !== null)
  return kept.length > 1 && kept.some((v) => v !== 0) ? kept : undefined
}
const running = computed(() => trend(live.value.points.map((p) => (p.runningSupported ? p.allRunning : null))))
const waiting = computed(() => trend(live.value.points.map((p) => (p.waitingSupported ? p.allWaiting : null))))
const occupied = computed(() => trend(live.value.points.map((p) => (p.runningSupported ? (p.allRunning ?? 0) + (p.allWaiting ?? 0) : null))))
// Active jobs and attributed snapshots trend with the occupancy history; the queue-depth facts with waiting.
const FACTS = [
  { action: 'Active jobs', spark: () => occupied.value },
  { action: 'Latest snapshot', spark: () => waiting.value },
  { action: 'Queued snapshots', spark: () => waiting.value },
  { action: 'Attribution coverage', spark: () => running.value },
]
// Partial attribution makes the snapshot count a lower bound, which the card says.
const factStatus = (fact: LiveFact, index: number) =>
  index === 3 && fact.tone === 'soft' && live.value.points.length ? { tone: 'soft' as const, label: 'Partial' } : undefined

const occupancy = computed(() =>
  live.value.points.map((point) => ({
    time: point.time,
    running: point.allRunning ?? 0,
    waiting: point.allWaiting ?? 0,
    point,
  })),
)
type OccupancyRow = (typeof occupancy.value)[number]
const occupancySeries = computed(() => {
  const series = [{ key: 'running', label: 'Running', color: SERIES.primary }]
  // Waiting is drawn only when every snapshot attributes it; a partial line would read as zero queueing.
  if (live.value.points.length && live.value.points.every((p) => p.waitingSupported)) series.push({ key: 'waiting', label: 'Waiting', color: SERIES.secondary })
  return series
})
const occupancyGap = computed(() => collectionGap(occupancy.value.map((row) => row.time), 2))
const pointLabel = (row: OccupancyRow) => new Date(row.time).toISOString().slice(0, 16).replace('T', ' ') + ' UTC'
const inspectPoint = (point: HistoryPoint) => open(live.value.inspectPoint(point))

const maxRunning = computed(() => Math.max(0, ...live.value.queues.map((q) => q.running)))
const QUEUE_COLUMNS = computed<ColumnSpec<LiveQueue>[]>(() => [
  {
    id: 'queue',
    label: 'Queue',
    value: (q) => q.name,
    cell: (q) => ({ text: q.name, mono: true }),
  },
  {
    id: 'running',
    label: 'Running',
    numeric: true,
    hideable: false,
    value: (q) => q.running,
    cell: (q) => ({
      ...num(q.running),
      bar: {
        value: q.running,
        max: maxRunning.value,
        color: hardwareColor(q.name),
      },
    }),
  },
  {
    id: 'waiting',
    label: 'Waiting',
    numeric: true,
    hideable: false,
    value: (q) => q.waiting,
    cell: (q) => num(q.waiting),
  },
])

const jobs = computed(() => live.value.jobs)
const JOB_COLUMNS = { compact: true, minWidth: PHONE_COLUMNS }
const openJob = (row: Rec) => row.url && window.open(String(row.url), '_blank', 'noopener')
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6" data-testid="omni-live-view">
    <StatGrid>
      <KpiCard
        v-for="(fact, index) in live.facts"
        :key="fact.label"
        :label="sentenceCase(fact.label)"
        :value="kpiValue(fact.value)"
        :spark="FACTS[index]!.spark()"
        :status="factStatus(fact, index)"
        badges="inline"
        :footnote="fact.meta"
        :action="{
          label: FACTS[index]!.action,
          onOpen: () => open(fact.open()),
        }"
        :style="{ animationDelay: `${index * 30}ms` }"
        :data-tone="fact.tone"
        data-testid="omni-live-fact"
      />
    </StatGrid>

    <Card class="min-w-0 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1" data-testid="omni-live">
      <CardHeader>
        <CardTitle>{{ live.title }}</CardTitle>
        <CardDescription>{{ live.meta }}</CardDescription>
      </CardHeader>
      <CardContent class="px-2 sm:px-6">
        <AreaChart
          v-if="occupancy.length > 1"
          :rows="occupancy"
          :x="(row: OccupancyRow) => row.time"
          :series="occupancySeries"
          :label="`Observed Omni jobs on monitored AMD queues, last ${range.label}`"
          :row-label="pointLabel"
          :y-format="countLabel"
          time-axis
          :max-gap="occupancyGap"
          inspect-placement="none"
          hint="Click for the snapshot evidence"
          :on-select="(row: OccupancyRow) => inspectPoint(row.point)"
        />
        <EmptyState
          v-else
          :icon="Activity"
          title="Not enough occupancy snapshots to draw a trend"
          :description="`${integer(occupancy.length)} workload-attributed snapshot${occupancy.length === 1 ? '' : 's'} in the last ${range.label}.`"
          data-testid="omni-occupancy-empty"
        />
      </CardContent>
    </Card>

    <div class="grid grid-cols-1 items-start gap-4 md:gap-6 @5xl/main:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] *:min-w-0">
      <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1" data-testid="omni-live-queues">
        <CardHeader>
          <CardTitle>Current queues</CardTitle>
          <CardDescription>Exact Omni jobs on each monitored AMD queue now</CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable
            v-if="live.queues.length"
            :columns="QUEUE_COLUMNS"
            :rows="live.queues"
            :searchable="false"
            :page-size="false"
            label="Omni jobs per AMD queue now"
            :on-row-click="(queue: LiveQueue) => open(queue.open())"
          />
          <EmptyState v-else title="No Omni jobs on monitored AMD queues right now" />
        </CardContent>
      </Card>

      <Card class="gap-4 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1">
        <CardHeader>
          <CardTitle>Evidence</CardTitle>
          <CardDescription>Source-backed jobs, queues and snapshots</CardDescription>
        </CardHeader>
        <CardContent>
          <ul class="flex flex-col divide-y rounded-lg border" data-testid="omni-live-actions">
            <li v-for="item in live.actions" :key="item.label">
              <button
                type="button"
                class="group/item flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-sm font-medium transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
                @click="open(item.open())"
              >
                <span>{{ item.label }}</span>
                <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-hover/item:translate-x-0.5" aria-hidden="true" />
              </button>
            </li>
            <li>
              <a
                :href="live.queueHistoryUrl"
                target="_blank"
                rel="noopener"
                class="group/item flex w-full items-center justify-between gap-3 px-4 py-3 text-sm font-medium transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
              >
                <span>Published queue history</span>
                <ExternalLink class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              </a>
            </li>
          </ul>
        </CardContent>
      </Card>
    </div>

    <Card class="min-w-0 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1" data-testid="omni-live-jobs">
      <CardHeader>
        <CardTitle>Active jobs</CardTitle>
        <CardDescription>Each row opens its exact Buildkite job; the longest waiting first</CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          v-if="jobs.rows.length"
          :columns="toColumns(jobs, JOB_COLUMNS)"
          :rows="jobs.rows"
          :search-text="jobs.searchText"
          :searchable="jobs.rows.length > 10"
          :search-placeholder="jobs.searchPlaceholder"
          label="Active Omni jobs"
          :on-row-click="openJob"
          @drill="(detail) => emit('show', detail)"
        />
        <EmptyState v-else :icon="ListChecks" title="No active Omni jobs on monitored AMD queues" />
      </CardContent>
    </Card>
  </div>
</template>
