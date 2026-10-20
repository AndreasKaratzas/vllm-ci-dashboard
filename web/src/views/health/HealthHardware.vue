<script setup lang="ts">
import { ChevronRight, ServerOff } from '@lucide/vue'
import { computed } from 'vue'
import LineChart from '@/components/charts/LineChart.vue'
import { rateAxis, shortDay, type SeriesSpec } from '@/components/charts/chart'
import ProportionBar, { type ProportionSegment } from '@/components/charts/ProportionBar.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import DataTable from '@/components/table/DataTable.vue'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { useDataset } from '@/composables/useDataset'
import { useQueryState } from '@/composables/useQueryState'
import type { SheetStack } from '@/composables/useSheetStack'
import { hardwareVariantHistory } from '@/domain/ciHealth'
import { integer, percent, type Rec } from '@/domain/format'
import { nightlyCountLabel } from '@/domain/nightly'
import {
  MATRIX_SORT_MODES,
  amdMatrixSortDescription,
  architectureHealth,
  architectureRate,
  coverageBrowserSubtitle,
  hardwareViewState,
  matrixBrowserState,
  matrixHealthOverviewPresentation,
  matrixHealthPolicy,
  runtimeMatrixSummary,
  sortAmdMatrixRows,
  type ArchitectureHealth,
  type MatrixBrowserMode,
  type MatrixSortMode,
} from '@/domain/healthHardware'
import { hardwareColor, hardwareFamily, rateTone, statusVar, toneClasses } from '@/lib/palette'
import type { Tone } from '@/lib/tone'
import { architectureDetail, count, groupHistoryDetail, matrixBrowserDetail, matrixColumns, matrixUnavailableDetail } from './details'

const props = defineProps<{ ops: Rec; sheets: SheetStack }>()

// An unreadable matrix reads as "unavailable", never as a page error.
const matrixSet = useDataset('amd_test_matrix')
const matrix = computed<Rec | null>(() => matrixSet.data.value ?? (matrixSet.error.value ? {} : null))

const sort = useQueryState<MatrixSortMode>('sort', { default: 'platform', allowed: MATRIX_SORT_MODES.map((m) => m.id) })
const sortModel = computed({
  get: () => sort.value,
  set: (mode: string) => {
    if (mode) sort.value = mode as MatrixSortMode
  },
})

const amdHealth = computed<Rec>(() => props.ops.amd_test_health ?? {})
const runtime = computed(() => runtimeMatrixSummary(amdHealth.value))
const policy = computed(() => matrixHealthPolicy(runtime.value))
const overview = computed(() =>
  matrixHealthOverviewPresentation(runtime.value, policy.value, runtime.value.latest_build_number || ((amdHealth.value.summary || {}) as Rec).latest_build_number),
)
const viewState = computed(() => hardwareViewState(matrix.value))
const architectures = computed<Rec[]>(() => matrix.value?.architectures || [])
const coverageRows = computed(() => sortAmdMatrixRows(matrix.value?.rows || [], sort.value))
const archHealth = computed(() => architectureHealth(architectures.value, coverageRows.value))
const columns = computed(() => matrixColumns(architectures.value, props.ops))

const passing = computed(() => Number(policy.value.passing_groups || 0))
const failing = computed(() => Number(policy.value.failing_groups || 0))
const noSignal = computed(() => Number(policy.value.waiting_groups || 0) + Number(policy.value.unknown_groups || 0))
const included = computed(() => Number(policy.value.included_groups || 0))
const definitionCount = computed(() => coverageRows.value.length)
const COUNT_CARDS = computed(() => [
  { mode: 'passing' as const, label: 'Passing', count: passing.value, tone: 'pass' as Tone },
  { mode: 'failing' as const, label: 'Failing', count: failing.value, tone: 'hard' as Tone },
  { mode: 'no-signal' as const, label: 'No signal', count: noSignal.value, tone: 'neutral' as Tone },
])
const segments = computed<ProportionSegment[]>(() => COUNT_CARDS.value.map((c) => ({ key: c.mode, label: c.label, count: c.count, color: statusVar(c.tone) })))

// Exact-job pass rate per architecture across the retained nightlies.
const history = computed(() => hardwareVariantHistory(amdHealth.value))
const trendSeries = computed<SeriesSpec[]>(() =>
  architectures.value
    .filter((arch) => history.value.has(String(arch.id).toLowerCase()))
    .map((arch) => ({ key: String(arch.id).toLowerCase(), label: String(arch.label), color: hardwareColor(arch.id), dash: FAMILY_DASH[hardwareFamily(arch.id) ?? ''] })),
)
// The hardware ramp is one hue; older families also get a dash pattern so lines stay apart.
const FAMILY_DASH: Record<string, number[] | undefined> = { MI250: [2, 3], MI300: [6, 3], MI325: [10, 3] }
interface TrendRow extends Rec {
  number: number
  at: number
}
// The shared pass-rate zoom, so differences of a point stay visible as on Home.
const trendAxis = computed(() => rateAxis(trendRows.value.flatMap((row) => trendSeries.value.map((s) => Number(row[s.key])))))
// Only nightlies that every charted architecture ran, so a missing route never reads as 0 %.
const trendRows = computed<TrendRow[]>(() => {
  const keys = trendSeries.value.map((s) => s.key)
  const builds = new Map<number, TrendRow>()
  for (const key of keys) {
    for (const point of history.value.get(key) ?? []) {
      const row = builds.get(point.number) ?? { number: point.number, at: Date.parse(point.observedAt || '') || 0 }
      row[key] = (point.passed / point.observed) * 100
      builds.set(point.number, row)
    }
  }
  return [...builds.values()].filter((row) => keys.every((key) => key in row)).sort((a, b) => a.number - b.number)
})

function openBrowser(mode: MatrixBrowserMode) {
  const state = matrixBrowserState(matrix.value)
  if (!state.available) props.sheets.open(matrixUnavailableDetail(state.label, state.description))
  else props.sheets.open(matrixBrowserDetail(mode, state.groups, state.subtitle, matrix.value))
}
function openArchitecture(health: ArchitectureHealth) {
  props.sheets.open(architectureDetail(health, coverageRows.value, props.ops))
}
function openMatrix() {
  props.sheets.open({
    id: 'route-matrix',
    title: 'Complete AMD test matrix',
    subtitle: coverageBrowserSubtitle(coverageRows.value, architectures.value, sort.value),
    tables: [
      {
        rows: coverageRows.value,
        columns: columns.value,
        searchText: (row: Rec) => [row.title, row.area].join(' '),
        searchPlaceholder: 'Filter test group or area',
        showTotal: true,
        onRowClick: (row: Rec) => groupHistoryDetail(row, props.ops),
        pageSize: 50,
      },
    ],
    width: 'xl',
  })
}

const routeSegments = (health: ArchitectureHealth): ProportionSegment[] => [
  { key: 'passed', label: 'passing', count: health.passed, color: statusVar('pass') },
  { key: 'incident', label: 'failing', count: health.incident, color: statusVar('hard') },
  { key: 'unknown', label: 'no signal', count: health.unknown, color: statusVar('neutral') },
]
const archTone = (health: ArchitectureHealth) => rateTone(architectureRate(health).passRate)
</script>

<template>
  <AsyncBoundary :loading="matrix === null" :error="null" skeleton="cards">
    <Card v-if="viewState === 'unavailable'" data-testid="hardware-unavailable">
      <CardContent>
        <EmptyState :icon="ServerOff" title="Current main CI AMD hardware evidence is unavailable" description="No older AMD pipeline result is substituted." />
      </CardContent>
    </Card>
    <div v-else class="flex flex-col gap-4 md:gap-6">
      <section class="flex flex-col gap-4 md:gap-6" data-testid="hardware-policy" aria-label="Configured AMD test groups">
        <Callout v-if="overview.unavailable" tone="soft" title="Best-hardware metric unavailable">
          Complete best-hardware detail is unavailable or storage-bounded; rates and drill-downs stay hidden.
        </Callout>
        <StatGrid v-else :cols="4">
          <KpiCard
            variant="ring"
            label="Best-hardware pass rate"
            :value="`${integer(passing)} passing`"
            :ratio="{ current: passing, total: included }"
            :footnote="`${count(included, 'configured test group')}; a group passes on its best AMD route`"
            headline="Browse test groups"
            :action="{ label: overview.browseLabel, onOpen: () => openBrowser('all') }"
            data-testid="hardware-rate"
          />
          <KpiCard
            v-for="card in COUNT_CARDS"
            :key="card.mode"
            :label="card.label"
            :value="card.count"
            :footnote="`${percent(card.count, included)} of configured groups`"
            :headline="`Inspect ${card.label.toLowerCase()}`"
            :action="{ label: `Inspect ${card.label.toLowerCase()} test groups`, onOpen: () => openBrowser(card.mode) }"
            :data-testid="`hardware-stat-${card.mode}`"
          >
            <ProportionBar
              :segments="[{ key: card.mode, label: card.label, count: card.count, color: statusVar(card.tone) }]"
              :max="included || 1"
              :label="`${card.label} share of configured test groups`"
              :legend="false"
            />
          </KpiCard>
        </StatGrid>
      </section>

      <Callout v-if="viewState === 'bounded'" tone="soft" title="Storage-bounded snapshot" data-testid="hardware-bounded">
        AMD detail is storage-bounded; architecture rates and routes are hidden.
      </Callout>

      <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="hardware-trend">
        <CardHeader>
          <CardTitle as="h2">Exact-job pass rate by architecture</CardTitle>
          <CardDescription>Share of exact main CI AMD jobs that passed per GPU family · {{ nightlyCountLabel(trendRows.length, 'jobs') }}</CardDescription>
        </CardHeader>
        <CardContent class="px-2 sm:px-6">
          <EmptyState v-if="trendRows.length < 2" title="Not enough nightlies for a trend" description="The chart appears once two nightlies have per-job results." />
          <LineChart
            v-else
            :rows="trendRows"
            :x="(row: TrendRow) => row.at"
            :row-label="(row: TrendRow) => `Nightly #${row.number} · ${shortDay(row.at)}`"
            :series="trendSeries"
            :y-domain="trendAxis.domain"
            :y-ticks="trendAxis.ticks"
            :y-format="(v: number) => `${Math.round(v)}%`"
            label="Exact-job pass rate by architecture"
          />
        </CardContent>
      </Card>

      <div class="grid grid-cols-1 gap-4 md:gap-6 @5xl/main:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] items-start *:min-w-0">
        <Card v-if="!overview.unavailable" class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="hardware-distribution">
          <CardHeader>
            <CardTitle as="h2">Best-hardware outcome</CardTitle>
            <CardDescription data-testid="hardware-policy-meta">{{ overview.meta }}</CardDescription>
          </CardHeader>
          <CardContent class="flex flex-col gap-4">
            <ProportionBar :segments="segments" label="Best-hardware outcome" :on-select="(s) => openBrowser(s.key as MatrixBrowserMode)" />
            <p class="text-xs text-muted-foreground">{{ overview.facts }}</p>
          </CardContent>
        </Card>

        <Card v-if="viewState !== 'bounded'" class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="architecture-routes">
          <CardHeader>
            <CardTitle as="h2">AMD architecture routes</CardTitle>
            <CardDescription>Latest result of every configured route per architecture</CardDescription>
          </CardHeader>
          <CardContent class="flex flex-col gap-1 px-3">
            <EmptyState v-if="!archHealth.length" title="No architectures are configured in this matrix" />
            <button
              v-for="health in archHealth"
              :key="health.architecture.id"
              type="button"
              class="group/arch grid grid-cols-[minmax(0,6.5rem)_minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 rounded-lg px-3 py-3 text-left transition-colors hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none active:bg-accent/80"
              :aria-label="`Inspect ${health.architecture.label}: ${integer(health.passed)} passing, ${integer(health.incident)} failing, ${integer(health.unknown)} no-signal routes`"
              data-testid="architecture-row"
              @click="openArchitecture(health)"
            >
              <span class="flex min-w-0 items-center gap-2.5">
                <span class="h-8 w-1 shrink-0 rounded-full" :style="{ backgroundColor: hardwareColor(health.architecture.id) }" aria-hidden="true" />
                <span class="flex min-w-0 flex-col">
                  <strong class="font-semibold" data-testid="architecture-label">{{ health.architecture.label }}</strong>
                  <span class="text-xs text-muted-foreground" data-testid="architecture-configured">{{ count(architectureRate(health).configured, 'configured route') }}</span>
                </span>
              </span>
              <span class="flex min-w-0 flex-col gap-1.5">
                <ProportionBar :segments="routeSegments(health)" :max="architectureRate(health).configured" :label="`${health.architecture.label} routes`" :legend="false" />
                <span class="flex flex-wrap gap-x-3 text-xs text-muted-foreground" data-testid="architecture-metrics">
                  <span :class="health.passed ? 'text-foreground' : undefined">{{ integer(health.passed) }} passing</span>
                  <span :class="health.incident ? toneClasses('hard', 'text') : undefined">{{ integer(health.incident) }} failing</span>
                  <span>{{ integer(health.unknown) }} no signal</span>
                </span>
              </span>
              <span class="flex items-center gap-2">
                <span class="flex flex-col items-end">
                  <strong class="text-lg font-semibold tabular-nums" :class="archTone(health) === 'pass' ? 'text-foreground' : toneClasses(archTone(health), 'text')" data-testid="architecture-rate">
                    {{ architectureRate(health).label }}
                  </strong>
                  <span class="text-xs text-muted-foreground">passing</span>
                </span>
                <ChevronRight class="size-4 text-muted-foreground transition-transform group-hover/arch:translate-x-0.5" aria-hidden="true" />
              </span>
            </button>
          </CardContent>
        </Card>
      </div>

      <Card v-if="viewState !== 'bounded'" class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="route-matrix">
        <CardHeader class="@max-2xl/main:has-data-[slot=card-action]:grid-cols-1">
          <CardTitle as="h2">Test-group routes by architecture</CardTitle>
          <CardDescription>
            <span data-testid="matrix-sort-description">{{ amdMatrixSortDescription(sort) }}</span>
            <template v-if="definitionCount !== included">
              · {{ count(definitionCount, 'configured definition') }}; command-equivalent duplicates collapse to the {{ count(included, 'health group') }} counted above
            </template>
          </CardDescription>
          <CardAction class="@max-2xl/main:col-start-1 @max-2xl/main:row-span-1 @max-2xl/main:row-start-3 @max-2xl/main:justify-self-start">
            <ToggleGroup v-model="sortModel" type="single" variant="outline" size="sm" aria-label="Sort AMD test matrix">
              <ToggleGroupItem v-for="mode in MATRIX_SORT_MODES" :key="mode.id" :value="mode.id">{{ mode.label }}</ToggleGroupItem>
            </ToggleGroup>
          </CardAction>
        </CardHeader>
        <CardContent>
          <DataTable
            :key="sort"
            :columns="columns"
            :rows="coverageRows"
            :page-size="8"
            :on-row-click="(row: Rec) => sheets.open(groupHistoryDetail(row, ops))"
            search-placeholder="Filter test group or area"
            label="Test-group routes by architecture"
            empty-text="No configured AMD test groups."
          />
        </CardContent>
        <CardFooter v-if="coverageRows.length > 8">
          <Button variant="outline" size="sm" @click="openMatrix">Browse complete route matrix <ChevronRight /></Button>
        </CardFooter>
      </Card>
    </div>
  </AsyncBoundary>
</template>
