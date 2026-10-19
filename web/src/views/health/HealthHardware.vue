<script setup lang="ts">
import { ChevronRight, ServerOff } from '@lucide/vue'
import { computed } from 'vue'
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
import { integer, percent, type Rec } from '@/domain/format'
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
import { hardwareColor, rateTone, statusVar, toneClasses } from '@/lib/palette'
import { architectureDetail, groupHistoryDetail, matrixBrowserDetail, matrixColumns, matrixUnavailableDetail } from './details'

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

const noSignal = computed(() => Number(policy.value.waiting_groups || 0) + Number(policy.value.unknown_groups || 0))
const included = computed(() => Number(policy.value.included_groups || 0))
const STAT_COUNT: Record<string, () => number> = {
  all: () => included.value,
  passing: () => Number(policy.value.passing_groups || 0),
  failing: () => Number(policy.value.failing_groups || 0),
  'no-signal': () => noSignal.value,
}
const segments = computed<ProportionSegment[]>(() => [
  { key: 'passing', label: 'Passing', count: STAT_COUNT.passing!(), color: statusVar('pass') },
  { key: 'failing', label: 'Failing', count: STAT_COUNT.failing!(), color: statusVar('hard') },
  { key: 'no-signal', label: 'No signal', count: noSignal.value, color: statusVar('neutral') },
])

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
        rowDetail: (row: Rec) => groupHistoryDetail(row, props.ops),
        pageSize: 50,
      },
    ],
    width: 'xl',
  })
}

const routeSegments = (health: ArchitectureHealth): ProportionSegment[] => [
  { key: 'passed', label: 'passing', count: health.passed, color: statusVar('pass') },
  { key: 'incident', label: 'non-passing', count: health.incident, color: statusVar('hard') },
  { key: 'unknown', label: 'unobserved', count: health.unknown, color: statusVar('neutral') },
]
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
        <template v-else>
          <StatGrid :cols="5">
            <KpiCard
              label="Pass rate"
              :value="overview.rate === '-' ? null : overview.rate"
              :status="{ tone: rateTone(policy.pass_percentage), label: `${overview.stats[1]!.count} / ${overview.stats[0]!.count}` }"
              footnote="One fixed best-hardware policy"
              :headline="overview.browseLabel"
              :action="{ label: overview.browseLabel, onOpen: () => openBrowser('all') }"
              data-testid="hardware-rate"
            />
            <KpiCard
              v-for="stat in overview.stats"
              :key="stat.mode"
              :label="stat.label"
              :value="STAT_COUNT[stat.mode]!()"
              :status="stat.mode === 'failing' && STAT_COUNT.failing!() ? { tone: 'hard', label: percent(STAT_COUNT.failing!(), included) } : undefined"
              :footnote="stat.mode === 'all' ? 'Deduplicated by the best-hardware policy' : `${percent(STAT_COUNT[stat.mode]!(), included)} of configured groups`"
              :headline="`Inspect ${stat.label.toLowerCase()}`"
              :action="{ label: `Inspect ${stat.label.toLowerCase()}`, onOpen: () => openBrowser(stat.mode) }"
              :data-testid="`hardware-stat-${stat.mode}`"
            />
          </StatGrid>
        </template>
      </section>

      <Callout v-if="viewState === 'bounded'" tone="soft" title="Storage-bounded snapshot" data-testid="hardware-bounded">
        AMD detail is storage-bounded; architecture rates and routes are hidden.
      </Callout>

      <div class="grid grid-cols-1 items-start gap-4 md:gap-6 @5xl/main:grid-cols-2 *:min-w-0">
        <Card v-if="!overview.unavailable" class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="hardware-distribution">
          <CardHeader>
            <CardTitle as="h2">Best-hardware outcome</CardTitle>
            <CardDescription data-testid="hardware-policy-meta">{{ overview.meta }}</CardDescription>
          </CardHeader>
          <CardContent class="flex flex-col gap-4">
            <div class="flex items-baseline gap-2">
              <span class="text-4xl font-semibold tracking-tight tabular-nums">{{ integer(STAT_COUNT.passing!()) }}</span>
              <span class="text-sm text-muted-foreground">of {{ integer(included) }} configured test groups pass on their best AMD route</span>
            </div>
            <ProportionBar :segments="segments" label="Best-hardware outcome" :on-select="(s) => openBrowser(s.key as MatrixBrowserMode)" />
            <p class="text-xs text-muted-foreground">{{ overview.facts }}</p>
          </CardContent>
        </Card>

        <Card v-if="viewState !== 'bounded'" class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="architecture-routes">
          <CardHeader>
            <CardTitle as="h2">AMD architecture routes</CardTitle>
            <CardDescription>Hardware routes per architecture; the best-hardware policy deduplicates them into test groups</CardDescription>
          </CardHeader>
          <CardContent class="flex flex-col gap-1 px-3">
            <EmptyState v-if="!archHealth.length" title="No architectures are configured in this matrix" />
            <button
              v-for="health in archHealth"
              :key="health.architecture.id"
              type="button"
              class="group/arch grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 rounded-lg px-3 py-3 text-left transition-colors hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none active:bg-accent/80"
              :aria-label="`Inspect ${health.architecture.label}: ${integer(health.passed)} passing routes, ${integer(health.incident)} non-passing routes, ${integer(health.unknown)} unobserved routes`"
              data-testid="architecture-row"
              @click="openArchitecture(health)"
            >
              <span class="flex min-w-0 items-center gap-2.5">
                <span class="h-8 w-1 shrink-0 rounded-full" :style="{ backgroundColor: hardwareColor(health.architecture.id) }" aria-hidden="true" />
                <span class="flex min-w-0 flex-col">
                  <strong class="font-semibold" data-testid="architecture-label">{{ health.architecture.label }}</strong>
                  <span class="text-xs text-muted-foreground" data-testid="architecture-configured">{{ integer(architectureRate(health).configured) }} configured routes</span>
                </span>
              </span>
              <span class="flex min-w-0 flex-col gap-1.5">
                <ProportionBar :segments="routeSegments(health)" :max="architectureRate(health).configured" :label="`${health.architecture.label} routes`" :legend="false" />
                <span class="flex flex-wrap gap-x-3 text-xs text-muted-foreground" data-testid="architecture-metrics">
                  <span :class="health.passed ? 'text-foreground' : undefined">{{ integer(health.passed) }} passing</span>
                  <span :class="health.incident ? toneClasses('hard', 'text') : undefined">{{ integer(health.incident) }} non-passing</span>
                  <span>{{ integer(health.unknown) }} unobserved</span>
                </span>
              </span>
              <span class="flex items-center gap-2">
                <span class="flex flex-col items-end">
                  <strong class="text-lg font-semibold tabular-nums" :class="architectureRate(health).tone === 'pass' ? 'text-foreground' : toneClasses(architectureRate(health).tone, 'text')" data-testid="architecture-rate">
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
          <CardDescription data-testid="matrix-sort-description">{{ amdMatrixSortDescription(sort) }}</CardDescription>
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
