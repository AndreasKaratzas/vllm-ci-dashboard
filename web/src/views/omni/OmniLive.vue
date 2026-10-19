<script setup lang="ts">
import { Activity, ChevronRight, ExternalLink, ListChecks } from '@lucide/vue'
import { computed } from 'vue'
import AreaChart from '@/components/charts/AreaChart.vue'
import BarChart from '@/components/charts/BarChart.vue'
import type { Detail } from '@/components/evidence/detail'
import EmptyState from '@/components/feedback/EmptyState.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import DataTable from '@/components/table/DataTable.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { integer } from '@/domain/format'
import { OMNI_RANGE_WINDOWS, type HistoryPoint, type OmniDetail, type OmniView } from '@/domain/omni'
import { SERIES } from '@/lib/palette'
import { countLabel, kpiValue, sentenceCase, timeAxisFormat, toColumns, toDetail } from './present'

const props = defineProps<{ view: OmniView }>()
const emit = defineEmits<{ show: [Detail] }>()
const open = (detail: OmniDetail) => emit('show', toDetail(detail))

const live = computed(() => props.view.live)
const range = computed(() => OMNI_RANGE_WINDOWS.find((w) => w.id === props.view.omniRange)!)

const FACT_ACTIONS = ['Active jobs', 'Latest snapshot', 'Queued snapshots', 'Attribution coverage']
// Waiting history doubles as the trend of the two queue-depth facts.
const waitingSeries = computed(() => live.value.points.filter((p) => p.waitingSupported).map((p) => Number(p.allWaiting)))
const sparks = computed(() => [undefined, waitingSeries.value.length > 1 ? waitingSeries.value : undefined, undefined, undefined])

const occupancy = computed(() =>
  live.value.points.map((point) => ({ time: point.time, running: point.allRunning ?? 0, waiting: point.allWaiting ?? 0, point })),
)
type OccupancyRow = (typeof occupancy.value)[number]
const occupancySeries = computed(() => {
  const series = [{ key: 'running', label: 'Running', color: SERIES.primary }]
  // Waiting is drawn only when every snapshot attributes it; a partial line would read as zero queueing.
  if (live.value.points.length && live.value.points.every((p) => p.waitingSupported)) series.push({ key: 'waiting', label: 'Waiting', color: SERIES.secondary })
  return series
})
const axis = computed(() => timeAxisFormat(range.value.hours))
const inspectPoint = (point: HistoryPoint) => open(live.value.inspectPoint(point))

const QUEUE_SERIES = [
  { key: 'running', label: 'Running', color: SERIES.primary },
  { key: 'waiting', label: 'Waiting', color: SERIES.secondary },
]
const queueRows = computed(() => live.value.queues.map((queue) => ({ ...queue })))

const jobs = computed(() => live.value.jobs)
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6" data-testid="omni-live-view">
    <StatGrid>
      <KpiCard
        v-for="(fact, index) in live.facts"
        :key="fact.label"
        :label="sentenceCase(fact.label)"
        :value="kpiValue(fact.value)"
        :spark="sparks[index]"
        :footnote="fact.meta"
        :action="{ label: FACT_ACTIONS[index]!, onOpen: () => open(fact.open()) }"
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
          :row-label="(row: OccupancyRow) => new Date(row.time).toISOString().slice(0, 16).replace('T', ' ') + ' UTC'"
          :x-format="axis"
          :y-format="countLabel"
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
          <BarChart
            v-if="queueRows.length"
            :rows="queueRows"
            :category="(row) => row.name"
            :series="QUEUE_SERIES"
            orientation="horizontal"
            label="Omni jobs per AMD queue now"
            hint="Click for the queue state"
            :value-format="countLabel"
            :on-select="(row) => open(row.open())"
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
        <CardDescription>Every row links to its exact Buildkite job; the longest waiting first</CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          v-if="jobs.rows.length"
          :columns="toColumns(jobs)"
          :rows="jobs.rows"
          :search-text="jobs.searchText"
          :searchable="jobs.rows.length > 10"
          :search-placeholder="jobs.searchPlaceholder"
          label="Active Omni jobs"
          @drill="(detail) => emit('show', detail)"
        />
        <EmptyState v-else :icon="ListChecks" title="No active Omni jobs on monitored AMD queues" />
      </CardContent>
    </Card>
  </div>
</template>
