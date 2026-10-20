<script setup lang="ts">
import { computed, h } from 'vue'
import SheetStack from '@/components/evidence/SheetStack.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import Callout from '@/components/feedback/Callout.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import { kpiTrend } from '@/components/stat/trend'
import { badge, link, type CellSpec, type ColumnSpec } from '@/components/table/columns'
import DataTable from '@/components/table/DataTable.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useOperationSections } from '@/composables/useOperations'
import { useSheetStack } from '@/composables/useSheetStack'
import { useClock } from '@/composables/useClock'
import { useSubviewHeader } from '@/composables/useSubviewHeader'
import { analyticsObservedAt, nightliesModel } from '@/domain/analytics'
import { movementBuilds } from '@/domain/ciHealth'
import { integer, shortDate, toneForState, value, type Rec } from '@/domain/format'
import { currentAmdHealth } from '@/domain/health'
import { amdNightlyPresentation, hasTestSignal, latestAmd, nightlyCountLabel, nightlyFailureCount, pendingNightly, signalNightly } from '@/domain/nightly'
import { exactPipelineBuildUrl } from '@/domain/urls'
import NightlyMovementChart from '@/features/nightly/NightlyMovementChart.vue'
import { buildEvidenceDetail, pendingEvidenceDetail } from '@/features/nightly/buildEvidence'
import PendingNightlyCallout from '@/features/nightly/PendingNightlyCallout.vue'
import AnalyticsAbout from './AnalyticsAbout.vue'

type Movement = 'new' | 'recurring' | 'fixed'

const { data: ops, error, loading, retry } = useOperationSections(['nightly', 'amd_test_health'])
const sheets = useSheetStack()

const amd = computed(() => latestAmd(ops.value))
const model = computed(() => nightliesModel(amd.value))
const latest = computed(() => model.value.latest)
const movement = computed(() => movementBuilds(model.value.rows))
// The latest nightly may still be running; counts come from the newest nightly with test results.
const signal = computed<Rec | undefined>(() => signalNightly(model.value.rows))
const pending = computed(() => pendingNightly(model.value.rows))
const now = useClock()
const presentation = computed(() =>
  amdNightlyPresentation(latest.value, currentAmdHealth(ops.value?.amd_test_health).summary || {}, ops.value?.generated_at, now.value),
)
const open = (build: Rec, scope?: Movement) => sheets.open(buildEvidenceDetail(build, { scope }))

useSubviewHeader(() => ({
  observedAt: ops.value ? analyticsObservedAt('nightlies', ops.value, {}) : undefined,
  about: () => h(AnalyticsAbout, { view: 'nightlies' }),
}))

// Oldest first, for the sparklines: the nightlies with test results, so each ends at the signal nightly.
const signalHistory = computed(() => model.value.rows.filter(hasTestSignal).reverse())
const trend = (key: Movement, goodWhen: 'up' | 'down') => kpiTrend(signalHistory.value.map((b) => nightlyFailureCount(b, key)), goodWhen)
const variantsTrend = computed(() => kpiTrend(signalHistory.value.map((b) => Number(b.total_groups))))
const MOVEMENT: { key: Movement; label: string; column: string; goodWhen: 'up' | 'down'; meta: string }[] = [
  { key: 'new', label: 'New failures', column: 'New', goodWhen: 'down', meta: 'Not failing in the preceding observed nightly' },
  { key: 'recurring', label: 'Recurring failures', column: 'Recurring', goodWhen: 'down', meta: 'Failed in the preceding observed nightly too' },
  { key: 'fixed', label: 'Fixed', column: 'Fixed', goodWhen: 'up', meta: 'Failed previously and passed now' },
]
const latestCount = (key: Movement) => (signal.value ? nightlyFailureCount(signal.value, key) : null)
const stateLabel = (state: unknown) => {
  const text = value(state, 'unknown')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

const countCell = (build: Rec, key: Movement): CellSpec => {
  const count = nightlyFailureCount(build, key)
  if (count === null) return { text: '—' }
  return count ? { text: integer(count), onOpen: () => open(build, key) } : { text: '0', muted: true }
}
const columns = computed<ColumnSpec<Rec>[]>(() => [
  {
    id: 'number',
    label: 'Nightly',
    value: (row) => Number(row.number || 0),
    cell: (row) => link(`#${value(row.number)}`, exactPipelineBuildUrl(row, 'ci'), true),
    hideable: false,
  },
  {
    id: 'state',
    label: 'State',
    value: (row) => value(row.state, 'unknown'),
    cell: (row) => badge(stateLabel(row.state), toneForState(row.state)),
    class: 'hidden sm:table-cell',
  },
  { id: 'variants', label: 'Job variants', numeric: true, value: (row) => Number(row.total_groups || 0), cell: (row) => ({ text: integer(row.total_groups) }), class: 'hidden md:table-cell' },
  ...MOVEMENT.map(
    (m): ColumnSpec<Rec> => ({
      id: m.key,
      label: m.column,
      numeric: true,
      value: (row) => nightlyFailureCount(row, m.key) ?? -1,
      cell: (row) => countCell(row, m.key),
      class: m.key === 'fixed' ? 'hidden sm:table-cell' : undefined,
    }),
  ),
  { id: 'started', label: 'Started', value: (row) => String(row.created_at || ''), cell: (row) => ({ text: shortDate(row.created_at), nowrap: true }), class: 'hidden md:table-cell' },
])
</script>

<template>
  <AsyncBoundary :loading="loading && !ops" :error="error" @retry="retry">
    <div class="flex flex-col gap-4 md:gap-6" data-testid="analytics-nightlies">
      <Callout v-if="!movement.length" tone="soft" title="Failure movement unavailable" data-testid="nightlies-note">
        No retained nightly publishes failure movement; refresh the operations snapshot to publish observed-failure-movement-v1 data.
      </Callout>
      <PendingNightlyCallout v-if="pending" :pending="pending" :on-open="() => sheets.open(pendingEvidenceDetail(pending!))" />

      <StatGrid :cols="5" data-testid="nightlies-kpis">
        <KpiCard
          label="Latest nightly"
          :value="latest.number ? `#${latest.number}` : null"
          :status="latest.number ? { tone: presentation.tone, label: presentation.label } : undefined"
          :footnote="latest.created_at ? shortDate(latest.created_at) : 'No completed nightly'"
          :action="latest.number ? { label: 'Inspect nightly evidence', onOpen: () => open(latest) } : undefined"
          data-testid="kpi-nightly-latest"
        />
        <KpiCard
          label="Job variants observed"
          :value="signal?.total_groups === undefined ? null : Number(signal.total_groups)"
          :spark="variantsTrend.spark"
          :delta="variantsTrend.delta"
          :footnote="signal ? `Exact jobs in #${value(signal.number)}` : 'No nightly with test results'"
          data-testid="kpi-nightly-variants"
        />
        <KpiCard
          v-for="m in MOVEMENT"
          :key="m.key"
          :label="m.label"
          :value="latestCount(m.key)"
          :spark="trend(m.key, m.goodWhen).spark"
          :delta="trend(m.key, m.goodWhen).delta"
          :footnote="latestCount(m.key) === null ? (signal ? `No failure movement for #${value(signal.number)}` : 'Unavailable in this snapshot') : m.meta"
          :action="signal && latestCount(m.key) ? { label: `Inspect ${m.label.toLowerCase()}`, onOpen: () => open(signal!, m.key) } : undefined"
          :data-testid="`kpi-nightly-${m.key}`"
        />
      </StatGrid>

      <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="nightlies-chart">
        <CardHeader>
          <CardTitle as="h2">Nightly failure movement</CardTitle>
          <CardDescription>{{ nightlyCountLabel(movement.length, 'builds') }} compared with their predecessor · fixes are drawn below zero</CardDescription>
        </CardHeader>
        <CardContent class="px-2 sm:px-6">
          <NightlyMovementChart :builds="movement" :on-select="(build: Rec) => open(build)" />
        </CardContent>
      </Card>

      <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="nightlies-table">
        <CardHeader>
          <CardTitle as="h2">{{ model.name }} nightlies</CardTitle>
          <CardDescription>{{ nightlyCountLabel(model.rows.length, 'builds') }} · failure movement counts open their exact job variants</CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable
            :columns="columns"
            :rows="model.rows"
            :page-size="15"
            :on-row-click="(row: Rec) => open(row)"
            search-placeholder="Filter nightly, state, or date"
            :label="`${model.name} nightlies`"
            empty-text="No matching nightlies."
          />
        </CardContent>
      </Card>
    </div>
    <SheetStack :sheets="sheets" />
  </AsyncBoundary>
</template>
