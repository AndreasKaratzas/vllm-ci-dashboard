<script setup lang="ts">
import { ExternalLink } from '@lucide/vue'
import { computed } from 'vue'
import ProportionBar from '@/components/charts/ProportionBar.vue'
import Callout from '@/components/feedback/Callout.vue'
import DataTable from '@/components/table/DataTable.vue'
import type { FilterSpec } from '@/components/table/columns'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useClock } from '@/composables/useClock'
import type { SheetStack } from '@/composables/useSheetStack'
import { age, integer } from '@/domain/format'
import { eventLabel, mainTimeline, type MainIncident, type TimelineEvent } from '@/domain/mainCi'
import { incidentColumns, incidentDetail, incidentSearch, mainBuildDetail, timelineColumns } from '@/features/main-ci/details'
import type { MainCi } from '@/features/main-ci/useMainCi'
import { hardwareColor, hardwareFamily, statusVar } from '@/lib/palette'

const props = defineProps<{ mainCi: MainCi; sheets: SheetStack }>()
const now = useClock()
const ctx = computed(() => props.mainCi.ctx.value)
const incidents = computed(() => props.mainCi.incidents.value)
const timeline = computed(() => mainTimeline(ctx.value.record))
const open = (row: MainIncident) => props.sheets.open(incidentDetail(row, ctx.value, incidents.value.issue))

const severitySegments = computed(() => [
  { key: 'hard', label: 'Hard', count: incidents.value.active.filter((row) => row.severity === 'hard').length, color: statusVar('hard') },
  { key: 'soft', label: 'Soft', count: incidents.value.active.filter((row) => row.severity === 'soft').length, color: statusVar('soft') },
  { key: 'pending', label: 'Pending soft', count: incidents.value.pending.length, color: statusVar('unknown') },
])
// Hardware is identity, so its segments use the hardware ramp, never a status color.
const hardwareSegments = computed(() => {
  const counts = new Map<string, number>()
  for (const row of incidents.value.active) {
    const family = hardwareFamily(row.hardware) ?? hardwareFamily(row.queue) ?? 'Other'
    counts.set(family, (counts.get(family) ?? 0) + 1)
  }
  return [...counts].sort((a, b) => a[0].localeCompare(b[0])).map(([family, count]) => ({ key: family, label: family, count, color: hardwareColor(family) }))
})

const EVENTS = computed(() => [...new Set(timeline.value.map((row) => row.event))].sort())
const timelineFilters = computed<FilterSpec<TimelineEvent>[]>(() => [
  {
    id: 'event',
    label: 'Event',
    allLabel: 'All events',
    options: EVENTS.value.map((value) => ({ value, label: eventLabel(value) })),
    predicate: (row, value) => row.event === value,
  },
])
// An event of a group that is still open leads to its incident, any other to the build it happened in.
const openEvent = (row: TimelineEvent) => {
  const match = [...incidents.value.active, ...incidents.value.pending].find((incident) => incident.groupId === row.groupId)
  if (match) return open(match)
  const build = ctx.value.builds.find((candidate) => candidate.number === row.build)
  if (build) props.sheets.open(mainBuildDetail(build, ctx.value))
  else if (row.buildUrl) window.open(row.buildUrl, '_blank', 'noopener')
}
</script>

<template>
  <Callout v-if="!incidents.available" tone="soft" title="Watcher state unavailable">
    The main-failure watcher's state is not in this snapshot, so incidents are not listed.
  </Callout>
  <Card v-else class="gap-4" data-testid="main-incident-summary">
    <CardHeader>
      <CardTitle class="text-base">{{ integer(incidents.active.length) }} active {{ incidents.active.length === 1 ? 'incident' : 'incidents' }}</CardTitle>
      <CardDescription>
        <template v-if="incidents.watcherLastRun">Watcher last ran {{ age(incidents.watcherLastRun, now) }}</template>
        <template v-if="incidents.issue">
          · tracked in
          <a :href="incidents.issue.url" target="_blank" rel="noopener" class="inline-flex items-center gap-1 font-medium text-foreground underline-offset-4 hover:underline">
            issue #{{ incidents.issue.number }} <ExternalLink class="size-3" aria-hidden="true" />
          </a>
        </template>
        <template v-if="incidents.suppressed"> · issue updates are suppressed</template>
      </CardDescription>
    </CardHeader>
    <CardContent class="grid grid-cols-1 gap-5 px-4 sm:px-6 @3xl/main:grid-cols-2">
      <div class="flex flex-col gap-2">
        <h3 class="text-sm font-medium">By severity</h3>
        <ProportionBar :segments="severitySegments" label="Incidents by severity" />
      </div>
      <div v-if="hardwareSegments.length" class="flex flex-col gap-2">
        <h3 class="text-sm font-medium">By hardware</h3>
        <ProportionBar :segments="hardwareSegments" label="Active incidents by hardware" />
      </div>
    </CardContent>
  </Card>

  <Card class="gap-4" data-testid="main-incidents">
    <CardHeader>
      <CardTitle class="text-base">Active incidents</CardTitle>
      <CardDescription>Open one for its good and bad commits, bisect command and failing job</CardDescription>
    </CardHeader>
    <CardContent class="px-4 sm:px-6">
      <DataTable
        :columns="incidentColumns(ctx)"
        :rows="incidents.active"
        :search-text="incidentSearch"
        search-placeholder="Filter group, hardware or build…"
        label="Active main incidents"
        empty-text="No confirmed failure on main."
        :on-row-click="open"
      />
    </CardContent>
  </Card>

  <Card v-if="incidents.pending.length" class="gap-4" data-testid="main-pending">
    <CardHeader>
      <CardTitle class="text-base">Pending soft failures</CardTitle>
      <CardDescription>Soft failures seen once; a second failing build confirms them</CardDescription>
    </CardHeader>
    <CardContent class="px-4 sm:px-6">
      <DataTable :columns="incidentColumns(ctx)" :rows="incidents.pending" :search-text="incidentSearch" label="Pending soft failures" :on-row-click="open" />
    </CardContent>
  </Card>

  <Card class="gap-4" data-testid="main-timeline">
    <CardHeader>
      <CardTitle class="text-base">Incident timeline</CardTitle>
      <CardDescription>{{ integer(timeline.length) }} recent lifecycle events, newest first</CardDescription>
    </CardHeader>
    <CardContent class="px-4 sm:px-6">
      <DataTable
        :columns="timelineColumns(ctx)"
        :rows="timeline"
        :filters="timelineFilters"
        :page-size="15"
        :search-text="(row: TimelineEvent) => [row.name, row.hardware, row.queue, row.build].join(' ')"
        search-placeholder="Filter group or build…"
        label="Incident timeline"
        empty-text="No incident events in this snapshot."
        :on-row-click="openEvent"
      />
    </CardContent>
  </Card>
</template>
