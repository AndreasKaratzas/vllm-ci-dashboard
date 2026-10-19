<script setup lang="ts">
// CI Health "AMD hardware" (classic ops_health_view=coverage).
import type { ColumnDef } from '@tanstack/vue-table'
import { computed, h, ref, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BrowserSheet, { type BrowserFilter } from '@/components/health/BrowserSheet.vue'
import GroupHistorySheet from '@/components/health/GroupHistorySheet.vue'
import MatrixGroupSheet from '@/components/health/MatrixGroupSheet.vue'
import DataTable from '@/components/ops/DataTable.vue'
import DistributionCard, { SEGMENT_FILL, type Segment } from '@/components/ops/DistributionCard.vue'
import KpiCard from '@/components/ops/KpiCard.vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import ViewState from '@/components/ops/ViewState.vue'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { useOperationSections } from '@/composables/useOperations'
import { fetchJSON } from '@/data/client'
import { integer, percent, toneForState, value, type Rec, type Tone } from '@/domain/format'
import {
  MATRIX_BROWSER_TITLES,
  MATRIX_CLASSIFICATION_OPTIONS,
  MATRIX_SORT_MODES,
  MATRIX_STATUS_OPTIONS,
  amdMatrixSortDescription,
  architectureBrowser,
  architectureCellUrl,
  architectureHealth,
  architectureRate,
  coverageBrowserSubtitle,
  hardwareViewState,
  matrixBrowserFilters,
  matrixBrowserState,
  matrixClassificationPredicate,
  matrixGateKindLabel,
  matrixGroupEvidence,
  matrixGroupSearchText,
  matrixHealthOverviewPresentation,
  matrixHealthPolicy,
  matrixHealthStatusLabel,
  matrixHealthTone,
  matrixSortMode,
  matrixStatusPredicate,
  runtimeMatrixSummary,
  sortAmdMatrixRows,
  type ArchitectureHealth,
  type MatrixBrowserMode,
  type MatrixSortMode,
} from '@/domain/healthHardware'

const MATRIX_PATH = 'data/vllm/ci/amd_test_matrix.json'

// Same section set as the CI Health page, so the request is shared.
const { data: ops, error, loading, retry } = useOperationSections(['nightly', 'amd_test_health', 'test_group_parity'])
const matrix = shallowRef<Rec | null>(null)
const matrixLoaded = ref(false)
// Classic treats an unreadable matrix as "unavailable", not as a page error.
fetchJSON<Rec>(MATRIX_PATH)
  .then((data) => (matrix.value = data || {}))
  .catch(() => (matrix.value = {}))
  .finally(() => (matrixLoaded.value = true))

const route = useRoute()
const router = useRouter()
const sort = computed<MatrixSortMode>({
  get: () => matrixSortMode(route.query.sort),
  set: (mode) => void router.replace({ query: { ...route.query, sort: mode === 'platform' ? undefined : mode } }),
})

const amdHealth = computed<Rec>(() => ops.value?.amd_test_health ?? {})
const runtime = computed(() => runtimeMatrixSummary(amdHealth.value))
const policy = computed(() => matrixHealthPolicy(runtime.value))
const overview = computed(() =>
  matrixHealthOverviewPresentation(
    runtime.value,
    policy.value,
    runtime.value.latest_build_number || ((amdHealth.value.summary || {}) as Rec).latest_build_number,
  ),
)
const viewState = computed(() => hardwareViewState(matrix.value))
const architectures = computed<Rec[]>(() => matrix.value?.architectures || [])
const coverageRows = computed(() => sortAmdMatrixRows(matrix.value?.rows || [], sort.value))
const archHealth = computed(() => architectureHealth(architectures.value, coverageRows.value))

const STAT_TONES: Record<string, Tone> = { all: 'is-neutral', passing: 'is-success', failing: 'is-danger', 'no-signal': 'is-neutral' }
function statCount(mode: string) {
  const p = policy.value
  if (mode === 'passing') return Number(p.passing_groups || 0)
  if (mode === 'failing') return Number(p.failing_groups || 0)
  return Number(p.waiting_groups || 0) + Number(p.unknown_groups || 0)
}
const segments = computed<Segment[]>(() => [
  { key: 'passing', label: 'Passing', count: Number(policy.value.passing_groups || 0), tone: 'is-success' },
  { key: 'failing', label: 'Failing', count: Number(policy.value.failing_groups || 0), tone: 'is-danger' },
  { key: 'no-signal', label: 'No signal', count: Number(policy.value.waiting_groups || 0) + Number(policy.value.unknown_groups || 0), tone: 'is-neutral' },
])

// Configured-group browser (classic openMatrixHealthBrowser).
const groupBrowser = ref({ open: false, mode: 'all' as MatrixBrowserMode })
const unavailable = ref<{ open: boolean; label: string; description: string }>({ open: false, label: '', description: '' })
const browserState = computed(() => matrixBrowserState(matrix.value))
const browserGroups = computed<Rec[]>(() => (browserState.value.available ? browserState.value.groups : []))
function openGroupBrowser(mode: MatrixBrowserMode) {
  const state = browserState.value
  if (!state.available) {
    unavailable.value = { open: true, label: state.label, description: state.description }
    return
  }
  groupBrowser.value = { open: true, mode }
}
const groupFilters = computed<BrowserFilter<Rec>[]>(() => {
  const initial = matrixBrowserFilters(groupBrowser.value.mode)
  return [
    { label: 'Filter configured test-group status', options: MATRIX_STATUS_OPTIONS, initial: initial.status, predicate: matrixStatusPredicate },
    { label: 'Filter configured test-group classification', options: MATRIX_CLASSIFICATION_OPTIONS, initial: initial.classification, predicate: matrixClassificationPredicate },
  ]
})

const matrixGroup = ref<{ open: boolean; group: Rec | null }>({ open: false, group: null })
const openMatrixGroup = (group: Rec) => (matrixGroup.value = { open: true, group })
const groupButton = (label: string, group: Rec, cls = 'text-left font-medium hover:underline') =>
  h('button', { type: 'button', class: cls, onClick: () => openMatrixGroup(group) }, label)

const groupColumns: ColumnDef<Rec, unknown>[] = [
  { id: 'title', header: 'Test group', cell: ({ row }) => groupButton(String(row.original.title ?? ''), row.original) },
  {
    id: 'status',
    header: 'Status',
    cell: ({ row }) =>
      h('button', { type: 'button', onClick: () => openMatrixGroup(row.original) }, [
        h(ToneBadge, { tone: matrixHealthTone(row.original.status) }, () => matrixHealthStatusLabel(row.original.status)),
      ]),
  },
  {
    id: 'classification',
    header: 'Classification',
    cell: ({ row }) =>
      h('button', { type: 'button', onClick: () => openMatrixGroup(row.original) }, [
        h(ToneBadge, { tone: row.original.classification === 'mi355-sensitive' ? 'is-info' : 'is-neutral' }, () => matrixGateKindLabel(row.original)),
      ]),
  },
  { id: 'hardware', header: 'Hardware', cell: ({ row }) => row.original.architectures.map((a: string) => a.toUpperCase()).join(', ') || '-' },
  {
    id: 'reason',
    header: 'Classification reason',
    cell: ({ row }) => groupButton(row.original.classificationReason || '-', row.original, 'text-left text-muted-foreground hover:underline'),
  },
  { id: 'signal', header: 'Route signal', cell: ({ row }) => `${integer(row.original.passedCells)} pass - ${integer(row.original.incidentCells)} non-passing` },
  {
    id: 'evidence',
    header: 'Evidence',
    cell: ({ row }) => groupButton(`${integer(matrixGroupEvidence(row.original).filter((e) => e.url).length)} jobs`, row.original),
  },
]

// Test-group detail (classic openGroupDetailWithEvidence({name, area}, ops)).
const groupDetail = ref<{ open: boolean; group: Rec | null }>({ open: false, group: null })
const openGroupDetail = (row: Rec) => (groupDetail.value = { open: true, group: { name: row.title, area: row.area } })
const detailButton = (row: Rec) =>
  h('button', { type: 'button', class: 'text-left font-medium hover:underline', onClick: () => openGroupDetail(row) }, String(row.title ?? ''))
const stateBadge = (row: Rec, label: string, stateName: unknown) =>
  h('button', { type: 'button', onClick: () => openGroupDetail(row) }, [h(ToneBadge, { tone: toneForState(stateName) }, () => label)])

// Per-architecture route browser (classic openArchitectureHealth).
const archBrowser = ref<{ open: boolean; health: ArchitectureHealth | null }>({ open: false, health: null })
const archBrowserData = computed(() => (archBrowser.value.health ? architectureBrowser(archBrowser.value.health, coverageRows.value) : null))
const archColumns = computed<ColumnDef<Rec, unknown>[]>(() => {
  const id = archBrowser.value.health?.architecture.id
  return [
    { id: 'title', header: 'Test group', cell: ({ row }) => detailButton(row.original) },
    { id: 'area', header: 'Area', cell: ({ row }) => value(row.original.area) },
    {
      id: 'latest',
      header: 'Latest result',
      cell: ({ row }) => {
        const cell = (row.original.cells || {})[id] || {}
        return stateBadge(row.original, cell.latest_state || 'unobserved', cell.latest_state)
      },
    },
    {
      id: 'build',
      header: 'Build',
      cell: ({ row }) => {
        const cell = (row.original.cells || {})[id] || {}
        const url = architectureCellUrl(cell)
        return url
          ? h('a', { href: url, target: '_blank', rel: 'noopener', class: 'font-mono hover:underline' }, `#${value(cell.latest_build_number)}`)
          : h('span', { class: 'text-muted-foreground' }, '-')
      },
    },
  ]
})

// Route matrix: one column per architecture.
const matrixColumns = computed<ColumnDef<Rec, unknown>[]>(() => [
  { id: 'title', header: 'Group', accessorFn: (row) => String(row.title ?? ''), cell: ({ row }) => detailButton(row.original), enableHiding: false, enableSorting: false },
  { id: 'area', header: 'Area', accessorFn: (row) => value(row.area), enableSorting: false },
  ...architectures.value.map(
    (arch): ColumnDef<Rec, unknown> => ({
      id: `arch-${arch.id}`,
      header: arch.label,
      enableSorting: false,
      accessorFn: (row) => {
        const c = (row.cells || {})[arch.id] || {}
        return c.exists ? c.latest_state || 'unknown' : '-'
      },
      cell: ({ row }) => {
        const c = (row.original.cells || {})[arch.id] || {}
        if (!c.exists) return h('span', { class: 'text-muted-foreground' }, '-')
        return stateBadge(row.original, c.latest_state || 'unknown', c.latest_state)
      },
    }),
  ),
])
const matrixBrowserOpen = ref(false)
const matrixSearch = (row: Rec) => [row.title, row.area].join(' ')

const RATE_TEXT: Record<Tone, string> = {
  'is-success': 'text-success',
  'is-warning': 'text-warning',
  'is-danger': 'text-destructive',
  'is-info': 'text-info',
  'is-neutral': 'text-muted-foreground',
  'is-not-targeted': 'text-muted-foreground',
}
</script>

<template>
  <ViewState :loading="(loading && !ops) || !matrixLoaded" :error="error" @retry="retry">
    <div v-if="ops" class="flex flex-col gap-4 md:gap-6">
      <Alert v-if="viewState === 'unavailable'" class="border-warning/40 bg-warning/10" data-testid="hardware-unavailable">
        <AlertDescription class="text-warning">
          Current main CI AMD hardware evidence is unavailable. No older AMD pipeline result is substituted.
        </AlertDescription>
      </Alert>
      <template v-else>
        <!-- Configured AMD test groups: the fixed best-hardware policy. -->
        <section class="flex flex-col gap-3" data-testid="hardware-policy">
          <div class="flex flex-wrap items-end justify-between gap-2">
            <div>
              <h3 class="text-lg font-semibold">Configured AMD test groups</h3>
              <p class="text-sm text-muted-foreground" data-testid="hardware-policy-meta">{{ overview.meta }}</p>
            </div>
            <Button v-if="!overview.unavailable" variant="outline" size="sm" @click="openGroupBrowser('all')">{{ overview.browseLabel }}</Button>
          </div>
          <Alert v-if="overview.unavailable" class="border-warning/40 bg-warning/10">
            <AlertDescription class="text-foreground">
              <p><strong>Best-hardware metric unavailable.</strong> Complete best-hardware detail is unavailable or storage-bounded; rates and drill-downs stay hidden.</p>
            </AlertDescription>
          </Alert>
          <template v-else>
            <div class="grid grid-cols-1 gap-4 @xl/main:grid-cols-2 @5xl/main:grid-cols-5">
              <KpiCard
                label="Pass rate"
                :value="overview.rate"
                :badge="`${overview.stats[1]!.count} / ${overview.stats[0]!.count}`"
                :badge-tone="Number(policy.failing_groups || 0) ? 'is-warning' : 'is-success'"
                headline="Browse all →"
                detail="One fixed best-hardware policy"
                data-testid="hardware-rate"
                @open="openGroupBrowser('all')"
              />
              <KpiCard
                v-for="stat in overview.stats"
                :key="stat.mode"
                :label="stat.label"
                :value="stat.count"
                :headline="`Inspect ${stat.label.toLowerCase()} →`"
                :badge="stat.mode === 'all' ? undefined : percent(statCount(stat.mode), policy.included_groups)"
                :badge-tone="STAT_TONES[stat.mode]"
                :data-testid="`hardware-stat-${stat.mode}`"
                @open="openGroupBrowser(stat.mode)"
              />
            </div>
            <DistributionCard
              title="Best-hardware outcome"
              :subtitle="overview.facts"
              :segments="segments"
              data-testid="hardware-distribution"
              @open="(segment) => openGroupBrowser(segment.key as MatrixBrowserMode)"
            />
          </template>
        </section>

        <Alert v-if="viewState === 'bounded'" class="border-warning/40 bg-warning/10" data-testid="hardware-bounded">
          <AlertDescription class="text-warning">AMD detail is storage-bounded; architecture rates and routes are hidden.</AlertDescription>
        </Alert>
        <template v-else>
          <Alert v-if="!policy.best_hardware_unavailable" class="border-info/40 bg-info/10">
            <AlertDescription class="text-foreground">
              <p>
                <strong>One fixed health policy.</strong> Generic test families pass when any configured AMD architecture passes. Explicit
                MI355-sensitive obligations use their exact MI355 route, so another architecture cannot hide a regression.
              </p>
            </AlertDescription>
          </Alert>

          <Card data-testid="architecture-routes">
            <CardHeader>
              <CardTitle>AMD architecture routes</CardTitle>
              <CardDescription>Architecture counts are hardware routes; the best-hardware policy above deduplicates test groups</CardDescription>
            </CardHeader>
            <CardContent class="flex flex-col divide-y">
              <button
                v-for="health in archHealth"
                :key="health.architecture.id"
                type="button"
                class="grid grid-cols-[7rem_minmax(0,1fr)_auto] items-center gap-4 py-3 text-left hover:bg-accent/50 md:grid-cols-[8rem_minmax(0,1fr)_minmax(0,16rem)_5rem]"
                :aria-label="`Inspect ${health.architecture.label}: ${integer(health.passed)} passing routes, ${integer(health.incident)} non-passing routes, ${integer(health.unknown)} unobserved routes`"
                data-testid="architecture-row"
                @click="archBrowser = { open: true, health }"
              >
                <span class="flex flex-col">
                  <strong data-testid="architecture-label">{{ health.architecture.label }}</strong>
                  <span class="text-xs text-muted-foreground" data-testid="architecture-configured">{{ integer(architectureRate(health).configured) }} configured routes</span>
                </span>
                <span class="flex h-2.5 overflow-hidden rounded-full bg-muted">
                  <template v-for="[tone, count] in ([['is-success', health.passed], ['is-danger', health.incident], ['is-warning', health.unknown]] as [Tone, number][])" :key="tone">
                    <span v-if="count" :class="SEGMENT_FILL[tone]" :style="{ width: `${(count / Math.max(1, architectureRate(health).configured)) * 100}%` }" />
                  </template>
                </span>
                <span class="hidden flex-wrap gap-x-3 text-xs md:flex" data-testid="architecture-metrics">
                  <span class="text-success">{{ integer(health.passed) }} passing</span>
                  <span class="text-destructive">{{ integer(health.incident) }} non-passing</span>
                  <span class="text-warning">{{ integer(health.unknown) }} unobserved</span>
                </span>
                <span class="flex flex-col items-end">
                  <strong class="text-lg tabular-nums" :class="RATE_TEXT[architectureRate(health).tone]" data-testid="architecture-rate">{{ architectureRate(health).label }}</strong>
                  <span class="text-xs text-muted-foreground">passing</span>
                </span>
              </button>
            </CardContent>
          </Card>

          <Card data-testid="route-matrix">
            <CardHeader>
              <CardTitle>Test-group routes by architecture</CardTitle>
              <CardDescription data-testid="matrix-sort-description">{{ amdMatrixSortDescription(sort) }}</CardDescription>
              <CardAction>
                <ToggleGroup v-model="sort" type="single" variant="outline" size="sm" aria-label="Sort AMD test matrix">
                  <ToggleGroupItem v-for="mode in MATRIX_SORT_MODES" :key="mode.id" :value="mode.id">{{ mode.label }}</ToggleGroupItem>
                </ToggleGroup>
              </CardAction>
            </CardHeader>
            <CardContent>
              <DataTable
                :key="sort"
                :columns="matrixColumns"
                :data="coverageRows"
                :page-size="8"
                search-placeholder="Filter test group or area"
                empty-text="No configured AMD test groups."
              />
            </CardContent>
            <CardFooter v-if="coverageRows.length > 8">
              <Button variant="outline" size="sm" @click="matrixBrowserOpen = true">Browse complete route matrix</Button>
            </CardFooter>
          </Card>
        </template>
      </template>

      <BrowserSheet
        v-model:open="groupBrowser.open"
        :title="MATRIX_BROWSER_TITLES[groupBrowser.mode]"
        :subtitle="browserState.available ? browserState.subtitle : ''"
        :rows="browserGroups"
        :columns="groupColumns"
        :search-text="matrixGroupSearchText"
        search-placeholder="Search group, reason, command, hardware, or queue"
        :filters="groupFilters"
      />
      <BrowserSheet
        v-model:open="archBrowser.open"
        :title="archBrowserData?.title ?? ''"
        :subtitle="archBrowserData?.subtitle"
        :rows="archBrowserData?.rows ?? []"
        :columns="archColumns"
        :search-text="archBrowserData?.searchText ?? (() => '')"
        :wide="false"
      />
      <BrowserSheet
        v-model:open="matrixBrowserOpen"
        title="Complete AMD test matrix"
        :subtitle="coverageBrowserSubtitle(coverageRows, architectures, sort)"
        :rows="coverageRows"
        :columns="matrixColumns"
        :search-text="matrixSearch"
        search-placeholder="Filter test group or area"
      />
      <MatrixGroupSheet v-model:open="matrixGroup.open" :group="matrixGroup.group" :matrix="matrix" />
      <GroupHistorySheet v-model:open="groupDetail.open" :group="groupDetail.group" :ops="ops" />
      <Sheet v-model:open="unavailable.open">
        <SheetContent class="w-full sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>{{ unavailable.label }}</SheetTitle>
            <SheetDescription>Operational aggregate</SheetDescription>
          </SheetHeader>
          <div class="flex flex-col gap-3 px-4 pb-6 text-sm">
            <p>{{ unavailable.description }}</p>
            <p><span class="text-muted-foreground">Value:</span> <strong>Unavailable</strong></p>
            <a v-if="unavailable.label === 'Configured AMD test groups'" :href="MATRIX_PATH" target="_blank" rel="noopener" class="font-medium hover:underline">Open published matrix ↗</a>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  </ViewState>
</template>
