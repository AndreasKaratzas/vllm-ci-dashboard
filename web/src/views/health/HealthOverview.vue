<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import Donut, { type DonutSlice } from '@/components/charts/Donut.vue'
import ProportionBar, { type ProportionSegment } from '@/components/charts/ProportionBar.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import Sparkline from '@/components/stat/Sparkline.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import { lastDelta } from '@/components/stat/trend'
import DataTable from '@/components/table/DataTable.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { SheetStack } from '@/composables/useSheetStack'
import {
  amdLatestState,
  amdLogicalInventory,
  jobVariantHistory,
  latestFailureObservations,
  logicalHealthHistory,
  logicalTestGroupPresentation,
  movementBuilds,
  sortLogicalRows,
} from '@/domain/ciHealth'
import { amdHealthModel } from '@/domain/analytics'
import { integer, rateText, value, type Rec } from '@/domain/format'
import { currentAmdHealth, hardwareDisplayLabel } from '@/domain/health'
import { amdMirrorInventoryView, amdMirrorSummary } from '@/domain/mirrors'
import { latestAmd, latestNightly, nightlyCountLabel, nightlyFailureCount } from '@/domain/nightly'
import { exactJobCatalogDetail, exactJobColumns, exactJobDetail, logicalCatalogDetail, type AmdGroupContext } from '@/features/amd-groups/details'
import MainCiSignal from '@/features/main-ci/MainCiSignal.vue'
import NightlyMovementChart from '@/features/nightly/NightlyMovementChart.vue'
import LatestNightlyCallout from '@/features/nightly/LatestNightlyCallout.vue'
import { buildEvidenceDetail, pendingEvidenceDetail } from '@/features/nightly/buildEvidence'
import { hardwareColor, statusVar } from '@/lib/palette'

const props = defineProps<{ ops: Rec; sheets: SheetStack }>()
const route = useRoute()

const amd = computed(() => latestAmd(props.ops))
const health = computed(() => currentAmdHealth(props.ops.amd_test_health))
const summary = computed<Rec>(() => health.value.summary || {})
const latestBuild = computed(() => summary.value.latest_build_number)
const counts = computed<Rec>(() => summary.value.latest_test_group_counts || {})
const logical = computed(() => logicalTestGroupPresentation(counts.value))
const logicalRows = computed<Rec[]>(() => amdLogicalInventory(props.ops.amd_test_health).rows || [])
const notice = computed(() => latestNightly(amd.value.builds))
const context = computed<AmdGroupContext>(() => ({ amdHealth: props.ops.amd_test_health, latestBuild: latestBuild.value }))

// Logical health: the ring plus its trend over retained nightlies.
const logicalTrend = computed(() => logicalHealthHistory(amd.value.builds).map((p) => p.rate))
const passingAny = computed(() => Number(counts.value.passing || 0))

// Exact job variants of the latest signal and their history.
const variants = computed(() => jobVariantHistory(props.ops.amd_test_health))
const variantRate = computed(() => variants.value.map((p) => (p.observed ? (p.passed / p.observed) * 100 : 0)))
const latestVariants = computed(() => variants.value.at(-1))
const failures = computed(() => latestFailureObservations(props.ops.amd_test_health))
const hardFailures = computed(() => failures.value.filter((row) => amdLatestState(row, latestBuild.value) === 'hard').length)
// The failing-job trend is shown only when its latest point is the number on the card.
const failingTrend = computed(() => {
  const trend = variants.value.map((p) => p.hard + p.soft)
  const aligned = latestVariants.value && Number(latestVariants.value.number) === Number(latestBuild.value) && trend.at(-1) === failures.value.length
  return aligned ? trend : undefined
})

const movement = computed(() => movementBuilds(amd.value.builds || []))
const movementTotals = computed(() =>
  (['new', 'recurring', 'fixed'] as const).map((key) => movement.value.reduce((sum, build) => sum + (nightlyFailureCount(build, key) ?? 0), 0)),
)

// Mirror configuration on main: a separate, physical count.
const mirrorInventory = computed<Rec>(() => (props.ops.test_group_parity || {}).mirror_inventory || {})
const mirror = computed(() => amdMirrorSummary(mirrorInventory.value, logical.value.available ? Number(counts.value.total || 0) : null))
const mirrorSlices = computed<DonutSlice[]>(() =>
  amdMirrorInventoryView(mirrorInventory.value).hero.legend.map((row) => ({ key: row.id, label: row.label, value: Number(row.count), color: hardwareColor(row.id) })),
)

// Logical outcomes of the signal build, and how each hardware route fared.
const OUTCOMES = [
  { key: 'passing_all', label: 'pass every route', tone: 'pass', title: 'Logical groups passing every route' },
  { key: 'partial', label: 'pass some routes', tone: 'soft', title: 'Logical groups with mixed hardware outcomes' },
  { key: 'non_passing', label: 'non-passing', tone: 'hard', title: 'Logical groups without a passing route' },
] as const
const outcomeSegments = computed<ProportionSegment[]>(() =>
  OUTCOMES.map((o) => ({ key: o.key, label: o.label, count: Number(counts.value[o.key] || 0), color: statusVar(o.tone) })),
)
const hardwareRoutes = computed(() => {
  const byHardware = new Map<string, { passing: number; failing: number; other: number }>()
  for (const row of logicalRows.value) {
    for (const route of row.hardware_states || []) {
      const key = String(route.hardware || 'unknown').toLowerCase()
      const tally = byHardware.get(key) ?? { passing: 0, failing: 0, other: 0 }
      if (route.state === 'passing') tally.passing += 1
      else if (route.state === 'failing') tally.failing += 1
      else tally.other += 1
      byHardware.set(key, tally)
    }
  }
  return [...byHardware.entries()]
    .sort(([a], [b]) => a.localeCompare(b, 'en', { numeric: true }))
    .map(([hardware, tally]) => ({
      hardware,
      label: hardwareDisplayLabel(hardware),
      total: tally.passing + tally.failing + tally.other,
      segments: [
        { key: 'passing', label: 'passing', count: tally.passing, color: statusVar('pass') },
        { key: 'failing', label: 'failing', count: tally.failing, color: statusVar('hard') },
        { key: 'other', label: 'no signal', count: tally.other, color: statusVar('neutral') },
      ] satisfies ProportionSegment[],
    }))
})

function openCatalog(title: string, rows: Rec[]) {
  props.sheets.open(logicalCatalogDetail(title, sortLogicalRows(rows), context.value))
}
function openOutcome(segment: ProportionSegment) {
  const outcome = OUTCOMES.find((o) => o.key === segment.key)!
  openCatalog(outcome.title, logicalRows.value.filter((row) => row.state === segment.key))
}
function openFailures() {
  props.sheets.open({
    id: 'amd-latest-failures',
    title: 'Latest AMD failure observations',
    subtitle: `Non-passing exact job variants in AMD test signal #${value(latestBuild.value)}, hardest first`,
    tables: [
      {
        rows: failures.value,
        columns: exactJobColumns(latestBuild.value),
        onRowClick: (row: Rec) => exactJobDetail(row, latestBuild.value),
        emptyText: 'No non-passing AMD job variants in the latest signal.',
      },
    ],
    width: 'lg',
  })
}
const failureColumns = computed(() => exactJobColumns(latestBuild.value))
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <MainCiSignal />
    <LatestNightlyCallout v-if="notice" :nightly="notice" :on-open="() => sheets.open(pendingEvidenceDetail(notice!))" />

    <StatGrid :cols="4">
      <KpiCard
        variant="ring"
        label="Logical runtime health"
        :value="logical.available ? `${integer(passingAny)} passing` : null"
        :ratio="logical.available ? { current: passingAny, total: Number(counts.total || 0) } : undefined"
        :delta="lastDelta(logicalTrend, 'up', { unit: ' pts' })"
        :footnote="logical.meta"
        :action="{ label: 'Inspect all logical test groups', onOpen: () => openCatalog('Latest AMD logical test groups', logicalRows) }"
        headline="Inspect groups"
        data-testid="logical-ring"
      />
      <KpiCard
        label="Exact job pass rate"
        :value="latestVariants && latestVariants.observed ? (latestVariants.passed / latestVariants.observed) * 100 : null"
        :format="rateText"
        :spark="variantRate"
        :delta="lastDelta(variantRate, 'up', { unit: ' pts' })"
        :headline="latestVariants ? `${integer(latestVariants.passed)} of ${integer(latestVariants.observed)} job variants passed` : undefined"
        :footnote="`Across ${nightlyCountLabel(variants.length, 'jobs')}`"
        :action="{ label: 'Inspect latest job variants', onOpen: () => sheets.open(exactJobCatalogDetail(amdHealthModel(health).catalogs.variants, latestBuild)) }"
        data-testid="kpi-job-rate"
      />
      <KpiCard
        label="Failing exact jobs"
        :value="failures.length"
        :spark="failingTrend"
        :delta="failingTrend ? lastDelta(failingTrend, 'down') : undefined"
        :footnote="`${integer(hardFailures)} hard fail · ${integer(failures.length - hardFailures)} soft fail in signal #${value(latestBuild)}`"
        :action="{ label: 'Inspect failing exact jobs', onOpen: openFailures }"
        headline="Inspect failing jobs"
        data-testid="kpi-failing-jobs"
      />
      <KpiCard
        label="AMD mirror groups"
        :value="mirror.unavailable ? null : mirror.countLabel"
        :footnote="mirror.meta"
        :action="{ label: mirror.ariaLabel, to: { path: route.path, query: { ...route.query, view: 'mirrors' } } }"
        headline="Open AMD mirrors"
        data-testid="mirror-summary"
      >
        <div class="flex flex-col gap-2">
          <Donut
            v-if="mirrorSlices.length"
            :slices="mirrorSlices"
            size="sm"
            :label="`Mirror groups by GPU family: ${mirrorSlices.map((s) => `${s.label} ${s.value}`).join(', ')}`"
          />
          <p class="text-xs text-muted-foreground" data-testid="mirror-summary-facts">{{ mirror.facts }}</p>
        </div>
      </KpiCard>
    </StatGrid>

    <div class="grid grid-cols-1 items-start gap-4 md:gap-6 @5xl/main:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] *:min-w-0">
      <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="movement-chart">
        <CardHeader>
          <CardTitle as="h2">Nightly failure movement</CardTitle>
          <CardDescription>
            {{ nightlyCountLabel(movement.length, 'builds') }} compared with their predecessor · {{ movementTotals[0] }} new, {{ movementTotals[1] }} recurring,
            {{ movementTotals[2] }} fixed · fixes are drawn below zero
          </CardDescription>
        </CardHeader>
        <CardContent class="px-2 sm:px-6">
          <NightlyMovementChart
            :builds="movement"
            :empty-description="`Latest signal #${value(latestBuild)}`"
            :on-select="(build: Rec) => sheets.open(buildEvidenceDetail(build))"
          />
        </CardContent>
      </Card>

      <Card data-testid="logical-outcomes" class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300">
        <CardHeader>
          <CardTitle as="h2">Logical group outcomes</CardTitle>
          <CardDescription>Signal #{{ value(latestBuild) }} · a group passes when one hardware route passes</CardDescription>
        </CardHeader>
        <CardContent class="flex flex-col gap-6">
          <ProportionBar :segments="outcomeSegments" label="Logical group outcomes" :on-select="openOutcome" />
          <div v-if="hardwareRoutes.length" class="flex flex-col gap-3 border-t pt-5">
            <div class="flex items-baseline justify-between gap-2">
              <h3 class="text-sm font-medium">Routes by hardware</h3>
              <span class="text-xs text-muted-foreground">passing / configured routes</span>
            </div>
            <div v-for="row in hardwareRoutes" :key="row.hardware" class="grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 text-sm" data-testid="hardware-route">
              <span class="inline-flex items-center gap-1.5 font-medium">
                <span class="size-2 rounded-full" :style="{ backgroundColor: hardwareColor(row.hardware) }" aria-hidden="true" />{{ row.label }}
              </span>
              <ProportionBar :segments="row.segments" :label="`${row.label} routes`" :legend="false" />
              <span class="w-10 text-right text-xs text-muted-foreground tabular-nums">{{ integer(row.segments[0]!.count) }}/{{ integer(row.total) }}</span>
            </div>
          </div>
          <div v-if="logicalTrend.length > 1" class="flex flex-col gap-2 border-t pt-5" data-testid="logical-trend">
            <div class="flex items-baseline justify-between gap-2">
              <h3 class="text-sm font-medium">Logical pass rate across nightlies</h3>
              <span class="text-xs text-muted-foreground tabular-nums">{{ logicalTrend[0]!.toFixed(1) }}% → {{ logicalTrend.at(-1)!.toFixed(1) }}%</span>
            </div>
            <Sparkline :values="logicalTrend" :label="`Logical pass rate over ${nightlyCountLabel(logicalTrend.length, 'builds')}`" />
          </div>
        </CardContent>
      </Card>
    </div>

    <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="latest-failures">
      <CardHeader>
        <CardTitle as="h2">Latest AMD failure observations</CardTitle>
        <CardDescription>Non-passing exact job variants in signal #{{ value(latestBuild) }}, hard failures first</CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          :columns="failureColumns"
          :rows="failures"
          :page-size="8"
          :on-row-click="(row: Rec) => sheets.open(exactJobDetail(row, latestBuild))"
          @drill="sheets.open"
          search-placeholder="Filter job variant, hardware, or queue"
          label="Latest AMD failure observations"
          empty-text="No non-passing AMD job variants in the latest signal."
        />
      </CardContent>
    </Card>
  </div>
</template>
