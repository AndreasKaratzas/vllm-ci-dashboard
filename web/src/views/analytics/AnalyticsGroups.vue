<script setup lang="ts">
import { computed, h } from 'vue'
import BarChart from '@/components/charts/BarChart.vue'
import type { ChartRow, SeriesSpec } from '@/components/charts/chart'
import SheetStack from '@/components/evidence/SheetStack.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import { kpiTrend, lastDelta } from '@/components/stat/trend'
import DataTable from '@/components/table/DataTable.vue'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useOperationSections } from '@/composables/useOperations'
import { useSheetStack } from '@/composables/useSheetStack'
import { useSubviewHeader } from '@/composables/useSubviewHeader'
import { amdHealthModel, analyticsObservedAt, type BuildHealthPoint, type HardwareCluster } from '@/domain/analytics'
import { jobVariantHistory, logicalHealthHistory, sortLogicalRows } from '@/domain/ciHealth'
import { integer, shortDate, type Rec } from '@/domain/format'
import { currentAmdHealth } from '@/domain/health'
import { latestAmd, latestNightly } from '@/domain/nightly'
import { exactJobCatalogColumns, exactJobCatalogDetail, exactJobDetail, logicalCatalogDetail, type AmdGroupContext } from '@/features/amd-groups/details'
import { buildEvidenceDetail, pendingEvidenceDetail } from '@/features/nightly/buildEvidence'
import LatestNightlyCallout from '@/features/nightly/LatestNightlyCallout.vue'
import { statusVar } from '@/lib/palette'
import type { Tone } from '@/lib/tone'
import AnalyticsAbout from './AnalyticsAbout.vue'

const { data: ops, error, loading, retry } = useOperationSections(['amd_test_health', 'nightly'])
const sheets = useSheetStack()

const health = computed(() => currentAmdHealth(ops.value?.amd_test_health))
const model = computed(() => amdHealthModel(health.value))
const summary = computed<Rec>(() => health.value.summary || {})
const latestBuild = computed(() => model.value.latestBuild)
const context = computed<AmdGroupContext>(() => ({ amdHealth: ops.value?.amd_test_health, latestBuild: latestBuild.value }))
const nightlies = computed<Rec[]>(() => latestAmd(ops.value).builds || [])
const notice = computed(() => latestNightly(nightlies.value))

useSubviewHeader(() => ({
  observedAt: ops.value ? analyticsObservedAt('groups', ops.value, health.value) : undefined,
  about: () => h(AnalyticsAbout, { view: 'groups' }),
}))

// ---- KPIs: the latest signal, trended over the AMD test-health history ----
const counts = computed<Rec>(() => summary.value.latest_test_group_counts || {})
const latestCounts = computed<Rec>(() => summary.value.latest_job_variant_state_counts || summary.value.latest_state_counts || {})
const hard = computed(() => Number(latestCounts.value.hard || latestCounts.value.hard_failed || 0))
const soft = computed(() => Number(latestCounts.value.soft || latestCounts.value.soft_failed || 0))
const severity = computed<Tone>(() => (hard.value ? 'hard' : soft.value ? 'soft' : 'pass'))
const history = computed(() => jobVariantHistory(ops.value?.amd_test_health))
const logicalTrend = computed(() => logicalHealthHistory(nightlies.value).map((p) => p.rate))
const failureTrend = computed(() => kpiTrend(history.value.map((p) => p.hard + p.soft), 'down'))
const passRateTrend = computed(() => kpiTrend(history.value.map((p) => (p.passed / p.observed) * 100), 'up', ' pts'))
const latestVariants = computed(() => history.value.find((p) => p.number === Number(latestBuild.value)))
const variantTrend = computed(() => kpiTrend(history.value.map((p) => p.observed)))
const strip = computed(() => Object.fromEntries(model.value.strip.map((item) => [item.id, item])))
const logicalPassing = computed(() => Number(counts.value.passing || 0))
const logicalTotal = computed(() => Number(counts.value.total || 0))

/** A retained AMD build: its nightly's evidence when the nightly history still has it. */
function openBuild(number: unknown, fallback: Rec = {}) {
  const build = nightlies.value.find((b) => Number(b.number) === Number(number)) ?? { number, ...fallback }
  sheets.open(buildEvidenceDetail(build))
}
const openLatest = () => openBuild(latestBuild.value, { created_at: summary.value.latest_observed_at, total_groups: summary.value.latest_job_variant_count })
const openLogical = () => sheets.open(logicalCatalogDetail('Latest AMD logical test groups', sortLogicalRows(model.value.logical.rows), context.value))
const openCatalog = (request: (typeof model.value.catalogs)['all']) => sheets.open(exactJobCatalogDetail(request, latestBuild.value))

// ---- Charts: states per build and per hardware variant ----
const BUILD_SERIES: SeriesSpec[] = [
  { key: 'passed', label: 'Passing', color: statusVar('pass') },
  { key: 'soft', label: 'Soft fail', color: statusVar('soft') },
  { key: 'hard', label: 'Hard fail', color: statusVar('hard') },
  { key: 'unknown', label: 'Unknown', color: statusVar('unknown') },
]
const HARDWARE_SERIES: SeriesSpec[] = [...BUILD_SERIES.slice(0, 3), { key: 'missing', label: 'Not in latest', color: statusVar('canceled') }]
type BuildRow = BuildHealthPoint & ChartRow
type HardwareRow = HardwareCluster & ChartRow
const buildRows = computed(() => model.value.builds.map((point) => ({ ...point }) as BuildRow))
const hardwareRows = computed(() => model.value.hardware.map((row) => ({ ...row }) as HardwareRow))
const buildLabel = (point: BuildHealthPoint) => {
  const ms = Date.parse(String(point.timestamp || ''))
  return Number.isFinite(ms) ? `${point.label} · ${shortDate(point.timestamp)}` : point.label
}
const inspectBuild = (point: BuildHealthPoint) => openBuild(point.number, { created_at: point.timestamp, total_groups: point.passed + point.soft + point.hard + point.unknown })
const inspectHardware = (row: HardwareCluster) =>
  sheets.open(
    exactJobCatalogDetail({ title: `${row.label} AMD job variants`, subtitle: `Exact job variants assigned to ${row.label}`, rows: row.rows, filter: 'all' }, latestBuild.value),
  )

const failureColumns = computed(() => exactJobCatalogColumns(latestBuild.value))
</script>

<template>
  <AsyncBoundary :loading="loading && !ops" :error="error" @retry="retry">
    <div class="flex flex-col gap-4 md:gap-6" data-testid="analytics-groups">
      <EmptyState
        v-if="!model.available"
        title="AMD job-variant history is unavailable"
        description="No upstream result has been substituted for AMD health."
        data-testid="groups-unavailable"
      />
      <template v-else>
        <Callout v-if="model.retentionNote" tone="soft" title="Partial job-variant catalog">{{ model.retentionNote }}</Callout>
        <LatestNightlyCallout v-if="notice" :nightly="notice" :on-open="() => sheets.open(pendingEvidenceDetail(notice!))" />

        <StatGrid :cols="4" data-testid="groups-kpis">
          <KpiCard
            label="Latest AMD nightly"
            :value="latestBuild ? `#${latestBuild}` : null"
            :status="{ tone: severity }"
            :spark="passRateTrend.spark"
            :headline="latestVariants ? `${integer(latestVariants.passed)} of ${integer(latestVariants.observed)} job variants passed` : undefined"
            :footnote="shortDate(summary.latest_observed_at)"
            :action="{ label: 'Inspect nightly evidence', onOpen: openLatest }"
            data-testid="kpi-amd-health-build"
          />
          <KpiCard
            variant="ring"
            label="Logical test groups"
            :value="model.logical.available ? `${integer(logicalPassing)} passing` : null"
            :ratio="model.logical.available ? { current: logicalPassing, total: logicalTotal } : undefined"
            :delta="lastDelta(logicalTrend, 'up', { unit: ' pts' })"
            :footnote="strip['amd-health-test-groups']?.meta"
            :action="model.logical.rows.length ? { label: 'Inspect logical test groups', onOpen: openLogical } : undefined"
            data-testid="kpi-amd-health-test-groups"
          />
          <KpiCard
            label="Latest job variants"
            :value="Number(strip['amd-health-observed']?.value.replace(/\D/g, '') || 0)"
            :spark="variantTrend.spark"
            :delta="variantTrend.delta"
            :footnote="strip['amd-health-observed']?.meta"
            :action="{ label: 'Inspect latest job variants', onOpen: () => openCatalog(model.catalogs.variants) }"
            data-testid="kpi-amd-health-observed"
          />
          <KpiCard
            label="Failure observations"
            :value="hard + soft"
            :status="hard + soft ? { tone: severity } : undefined"
            :spark="failureTrend.spark"
            :delta="failureTrend.delta"
            :footnote="strip['amd-health-incidents']?.meta"
            :action="{ label: 'Inspect failure observations', onOpen: () => openCatalog(model.catalogs.incidents) }"
            data-testid="kpi-amd-health-incidents"
          />
        </StatGrid>

        <div class="grid grid-cols-1 items-start gap-4 md:gap-6 @5xl/main:grid-cols-2 *:min-w-0">
          <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="build-health-chart">
            <CardHeader>
              <CardTitle as="h2">AMD health by nightly</CardTitle>
              <CardDescription>
                Job-variant outcomes in each of the {{ integer(model.builds.length) }} nightlies in the AMD test-health history
              </CardDescription>
            </CardHeader>
            <CardContent class="px-2 sm:px-6">
              <EmptyState v-if="!model.builds.length" title="No retained AMD build summaries in this snapshot" />
              <BarChart
                v-else
                :rows="buildRows"
                :category="(point: BuildRow) => point.label"
                :row-label="buildLabel"
                :series="BUILD_SERIES"
                label="AMD health by nightly"
                hint="Click for nightly evidence"
                inspect-placement="legend"
                :on-select="inspectBuild"
              />
            </CardContent>
          </Card>
          <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="hardware-health-chart">
            <CardHeader>
              <CardTitle as="h2">Latest health by hardware variant</CardTitle>
              <CardDescription>Latest outcome of every retained job variant, by execution variant</CardDescription>
            </CardHeader>
            <CardContent class="px-2 sm:px-6">
              <BarChart
                :rows="hardwareRows"
                :category="(row: HardwareRow) => row.label"
                :series="HARDWARE_SERIES"
                orientation="horizontal"
                label="Latest health by hardware variant"
                hint="Click for its job variants"
                inspect-placement="legend"
                :on-select="inspectHardware"
              />
            </CardContent>
          </Card>
        </div>

        <section class="flex flex-col gap-3" data-testid="catalog">
          <div class="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 class="text-base font-semibold">Retained job-variant catalog</h2>
              <p class="text-sm text-muted-foreground">Start with the latest signal, then inspect exact nightly evidence for current or historical variants.</p>
            </div>
            <Button variant="outline" size="sm" data-testid="browse-all" @click="openCatalog(model.catalogs.all)">{{ model.browseAllLabel }}</Button>
          </div>
          <StatGrid :cols="4">
            <KpiCard
              v-for="cluster in model.clusters"
              :key="cluster.label"
              :label="cluster.label"
              :value="cluster.renderedCount.startsWith('≥') ? cluster.renderedCount : cluster.count"
              :status="cluster.label === 'Needs attention' && cluster.count ? { tone: cluster.tone } : undefined"
              :headline="cluster.description"
              :footnote="cluster.meta"
              :action="{ label: `Open ${cluster.label}: ${cluster.renderedCount} AMD job variants`, onOpen: () => openCatalog(cluster.catalog) }"
              data-testid="cluster"
            />
          </StatGrid>
        </section>

        <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="priority">
          <CardHeader>
            <CardTitle as="h2">Current AMD failures to inspect</CardTitle>
            <CardDescription>{{ model.priorityMeta }}</CardDescription>
          </CardHeader>
          <CardContent>
            <DataTable
              :columns="failureColumns"
              :rows="model.priority"
              :page-size="10"
              :on-row-click="(row: Rec) => sheets.open(exactJobDetail(row, latestBuild))"
              @drill="sheets.open"
              search-placeholder="Filter job variant, hardware, or queue"
              label="Current AMD failures to inspect"
              empty-text="No current AMD failure observations."
            />
            <p class="mt-3 text-xs text-muted-foreground">{{ model.priorityCaption }}</p>
          </CardContent>
        </Card>
      </template>
    </div>
    <SheetStack :sheets="sheets" />
  </AsyncBoundary>
</template>
