<script setup lang="ts">
import { History, RefreshCw } from '@lucide/vue'
import { computed, effectScope, onBeforeUnmount, ref, shallowRef } from 'vue'
import LineChart from '@/components/charts/LineChart.vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import StatusBadge from '@/components/feedback/StatusBadge.vue'
import DataTable from '@/components/table/DataTable.vue'
import { badge, link, num, text, type ColumnSpec, type FilterSpec } from '@/components/table/columns'
import { Button } from '@/components/ui/button'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { useDataset, type Dataset } from '@/composables/useDataset'
import { isIncidentObservation, observationState, observationTimestamp } from '@/domain/ciHealth'
import { duration, integer, shortDate, toneForState, type Rec } from '@/domain/format'
import {
  evidenceResultLabel,
  filterEvidenceRows,
  groupDetailState,
  groupPublicationHistoryComplete,
  isNightlyObservation,
  mixedOutcomeNotice,
  mixedOutcomeObservations,
  mixedOutcomeSummary,
  observationDurationMinutes,
  observationWaitMinutes,
  outcomeChartCaption,
  trailingPassStats,
} from '@/domain/healthHardware'
import { exactPipelineEvidenceUrl } from '@/domain/urls'
import { SERIES } from '@/lib/palette'
import OutcomeStrip from './OutcomeStrip.vue'

// The 30-day ledger is loaded only on request and is never refreshed in the background.
const props = defineProps<{ group: Rec; ops: Rec | null }>()

const scope = effectScope()
onBeforeUnmount(() => scope.stop())
const ledger = shallowRef<Dataset | null>(null)
function load() {
  if (ledger.value) {
    void ledger.value.refresh()
    return
  }
  const dataset = scope.run(() => useDataset({ sections: ['reliability'] }))!
  if (dataset.data.value !== null && !dataset.loading.value) void dataset.refresh()
  ledger.value = dataset
}
const loading = computed(() => !!ledger.value && (ledger.value.loading.value || ledger.value.refreshing.value))
const failed = computed(() => !!ledger.value?.error.value && !loading.value)
// A loaded ledger that still has no runs for this group ends the request instead of offering it again.
const loadedWithout = computed(() => {
  const data = ledger.value?.data.value
  if (!data || loading.value || failed.value) return null
  const reliability = data.reliability
  return reliability && reliability.available !== false
    ? 'The 30-day run ledger has no exact runs for this test group.'
    : 'The 30-day run ledger is not published in this snapshot.'
})
const expanded = computed<Rec | null>(() => {
  const data = ledger.value?.data.value
  return data ? { ...(props.ops ?? {}), reliability: data.reliability } : null
})

const state = computed(() => groupDetailState(props.group, expanded.value ?? props.ops))
const candidate = computed<Rec | null>(() => (state.value.kind === 'evidence' ? state.value.row : null))
const pipeline = computed(() => candidate.value?.source_pipeline || 'ci')
const complete = computed(() => groupPublicationHistoryComplete(candidate.value))
const all = computed(() => (candidate.value ? mixedOutcomeObservations(candidate.value) : []))
const notice = computed(() => mixedOutcomeNotice(all.value, complete.value, pipeline.value))

const mode = ref<'main' | 'nightly'>('main')
const modeModel = computed({
  get: () => mode.value,
  set: (next: string) => {
    if (next === 'main' || next === 'nightly') mode.value = next
  },
})
const observations = computed(() => all.value.filter((row) => mode.value === 'main' || isNightlyObservation(row)))
const summary = computed(() => mixedOutcomeSummary(observations.value, complete.value, mode.value))
const rolling = computed(() => trailingPassStats(observations.value, 10))

interface Point extends Rec {
  index: number
  label: string
  rate: number | null
  completion: number | null
  wait: number | null
}
const points = computed<Point[]>(() =>
  observations.value.map((row, index) => ({
    index,
    label: row.build_number ? `#${row.build_number}` : shortDate(observationTimestamp(row)),
    rate: complete.value ? (rolling.value[index]?.rate ?? null) : null,
    completion: observationDurationMinutes(row),
    wait: observationWaitMinutes(row),
  })),
)
const hasWait = computed(() => points.value.some((p) => p.wait !== null))
const hasDuration = computed(() => hasWait.value || points.value.some((p) => p.completion !== null))
const durationSeries = computed(() => [
  { key: 'completion', label: 'Completion (min)', color: SERIES.primary },
  ...(hasWait.value ? [{ key: 'wait', label: 'Queue wait (min)', color: SERIES.secondary }] : []),
])
const pointLabel = (i: number) => points.value[i]?.label ?? ''

const retried = (row: Rec) => {
  const retry = row.retry_evidence || row
  return Boolean(retry.retried || retry.retried_in_job_id || Number(retry.retries_count || 0))
}
const columns: ColumnSpec<Rec>[] = [
  { id: 'build', label: 'Build', value: (row) => Number(row.build_number || 0), cell: (row) => link(row.build_number ? `#${row.build_number}` : 'Build', exactPipelineEvidenceUrl(row, pipeline.value), true) },
  { id: 'cohort', label: 'Cohort', value: (row) => (isNightlyObservation(row) ? 'nightly' : 'main'), cell: (row) => text(isNightlyObservation(row) ? 'nightly' : 'main') },
  { id: 'observed', label: 'Observed', value: (row) => String(observationTimestamp(row) || ''), cell: (row) => text(shortDate(observationTimestamp(row))) },
  { id: 'result', label: 'Result', value: evidenceResultLabel, cell: (row) => badge(evidenceResultLabel(row), toneForState(evidenceResultLabel(row))) },
  {
    id: 'variant',
    label: 'Variant',
    value: (row) => [row.variant_hardware, (row.variant_queues || []).join(', '), row.variant_id ? `id ${row.variant_id}` : null].filter(Boolean).join(' · ') || String(row.group_id || ''),
    cell: (row) => ({ text: [row.variant_hardware, (row.variant_queues || []).join(', '), row.variant_id ? `id ${row.variant_id}` : null].filter(Boolean).join(' · ') || String(row.group_id || '—'), mono: true }),
  },
  { id: 'queue', label: 'Queue', value: (row) => String(row.queue || ''), cell: (row) => ({ text: String(row.queue || '—'), mono: true }) },
  { id: 'completion', label: 'Completion', numeric: true, value: (row) => observationDurationMinutes(row) ?? -1, cell: (row) => num(observationDurationMinutes(row), duration) },
  { id: 'wait', label: 'Queue wait', numeric: true, value: (row) => observationWaitMinutes(row) ?? -1, cell: (row) => num(observationWaitMinutes(row), duration) },
  {
    id: 'retry',
    label: 'Retry evidence',
    value: (row) => (retried(row) ? 1 : 0),
    cell: (row) => {
      if (!retried(row)) return text(null)
      const count = Number((row.retry_evidence || row).retries_count || 0)
      return text(count ? `${count} retries` : 'retried')
    },
  },
]
const filters: FilterSpec<Rec>[] = [
  {
    id: 'result',
    label: 'Result',
    allLabel: 'All results',
    options: [
      { value: 'passing', label: 'Passing only' },
      { value: 'incident', label: 'Failures only' },
    ],
    predicate: (row, selected) => filterEvidenceRows([row], '', selected).length > 0,
  },
]
const evidenceRows = computed(() => filterEvidenceRows(observations.value, '', 'all'))
const evidenceSearch = (row: Rec) => [row.build_number, row.queue, row.raw_name, row.name, observationState(row)].join(' ')
const incidents = computed(() => observations.value.filter(isIncidentObservation).length)
</script>

<template>
  <div class="flex flex-col gap-4" data-testid="group-history">
    <template v-if="state.kind === 'aggregate'">
      <EmptyState
        :icon="History"
        title="No exact per-run observations in this snapshot"
        :description="
          loadedWithout ??
          (state.canLoadHistory ? 'The aggregate is available. Load the 30-day ledger for outcome, completion and queue-wait history.' : 'The retained ledger has no exact runs for this group.')
        "
        data-testid="group-history-empty"
      >
        <template v-if="state.canLoadHistory && !loadedWithout" #action>
          <Button size="sm" :disabled="loading" @click="load">
            <RefreshCw :class="loading && 'motion-safe:animate-spin'" />
            {{ loading ? 'Loading run history…' : failed ? 'Retry loading run history' : 'Load 30-day run history' }}
          </Button>
        </template>
      </EmptyState>
    </template>

    <template v-else>
      <Callout :tone="notice.tone" :title="notice.strong.trim()">{{ notice.text }}</Callout>
      <EmptyState v-if="!all.length" title="This snapshot predates per-run evidence" description="Exact job links appear once the operations snapshot carries per-run observations." />
      <template v-else>
        <div class="flex flex-wrap items-center gap-3">
          <ToggleGroup v-model="modeModel" type="single" variant="outline" size="sm" aria-label="Test-group history cohort">
            <ToggleGroupItem value="main">All main</ToggleGroupItem>
            <ToggleGroupItem value="nightly">Nightly only</ToggleGroupItem>
          </ToggleGroup>
          <span class="text-xs text-muted-foreground">
            {{ complete ? 'The complete branch=main cohort or its nightly subset.' : 'Published branch=main evidence or its nightly subset; omitted rows are not inferred.' }}
          </span>
        </div>
        <EmptyState v-if="!observations.length" title="No exact nightly observations are retained for this test-group variant." />
        <template v-else>
          <dl class="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <div v-for="stat in summary" :key="stat.label" class="flex min-w-0 flex-col gap-1 rounded-lg border bg-linear-to-t from-brand/5 to-card p-3">
              <dt class="text-xs text-muted-foreground">{{ stat.label }}</dt>
              <dd class="flex flex-wrap items-center gap-2">
                <span class="text-xl font-semibold tabular-nums">{{ stat.value }}</span>
                <StatusBadge v-if="stat.tone === 'hard' || stat.tone === 'soft'" :tone="stat.tone" />
              </dd>
            </div>
          </dl>

          <section class="flex flex-col gap-2 rounded-lg border p-4">
            <h4 class="text-sm font-semibold">Outcome trend</h4>
            <p class="text-xs text-muted-foreground">{{ outcomeChartCaption(observations, complete) }}</p>
            <OutcomeStrip :observations="observations" :pipeline="pipeline" />
            <LineChart
              v-if="complete"
              :rows="points"
              :x="(p: Point) => p.index"
              :x-format="pointLabel"
              :row-label="(p: Point) => p.label"
              :series="[{ key: 'rate', label: 'Trailing 10-run pass rate', color: SERIES.primary }]"
              :y-domain="[0, 100]"
              :y-format="(v: number) => `${v}%`"
              label="Trailing 10-run pass rate per exact job observation"
            />
          </section>

          <section v-if="hasDuration" class="flex flex-col gap-2 rounded-lg border p-4">
            <h4 class="text-sm font-semibold">Completion and queue wait</h4>
            <p class="text-xs text-muted-foreground">Minutes per exact Buildkite job observation</p>
            <LineChart
              :rows="points"
              :x="(p: Point) => p.index"
              :x-format="pointLabel"
              :row-label="(p: Point) => p.label"
              :series="durationSeries"
              label="Completion and queue wait per exact job observation"
            />
          </section>

          <section class="flex flex-col gap-2">
            <div class="flex flex-wrap items-baseline justify-between gap-2">
              <h4 class="text-sm font-semibold">Exact observations</h4>
              <span class="text-xs text-muted-foreground">
                {{ integer(incidents) }} non-passing of {{ integer(observations.length) }} retained {{ mode === 'main' ? 'main' : 'nightly' }} observations
              </span>
            </div>
            <DataTable
              :columns="columns"
              :rows="evidenceRows"
              :filters="filters"
              :search-text="evidenceSearch"
              search-placeholder="Filter build, queue, or result"
              :page-size="25"
              show-total
              label="Exact observations"
              empty-text="No matching observations."
            />
            <p class="text-xs text-muted-foreground">
              Outcomes combine Buildkite job state with parsed test-result summaries. Completion is job wall time; queue wait appears only when the collector
              retained it. Retry labels need explicit Buildkite retry metadata; mixed outcomes alone are not labeled as confirmed flakes.
            </p>
          </section>
        </template>
      </template>
    </template>
  </div>
</template>
