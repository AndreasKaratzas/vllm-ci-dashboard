<script setup lang="ts">
import { computed } from 'vue'
import BarChart from '@/components/charts/BarChart.vue'
import type { SeriesSpec } from '@/components/charts/chart'
import DataTable from '@/components/table/DataTable.vue'
import type { FilterSpec } from '@/components/table/columns'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { SheetStack } from '@/composables/useSheetStack'
import { integer } from '@/domain/format'
import { flakinessThresholds, flakyGroups, groupLabel, retryGroups, type FlakyGroup, type RetryGroup } from '@/domain/mainCi'
import { flakyColumns, groupSearch, groupStatsDetail, retryColumns } from '@/features/main-ci/details'
import type { MainCi } from '@/features/main-ci/useMainCi'
import { SERIES } from '@/lib/palette'

const props = defineProps<{ mainCi: MainCi; sheets: SheetStack }>()
const ctx = computed(() => props.mainCi.ctx.value)
const summary = computed(() => props.mainCi.summary.value!)
const thresholds = computed(() => flakinessThresholds(ctx.value.record))
const windowLabel = computed(() => (thresholds.value ? `${thresholds.value.windowDays} days` : 'window'))
const flaky = computed(() => flakyGroups(ctx.value.record))
const retry = computed(() => retryGroups(ctx.value.record))
const open = (row: FlakyGroup | RetryGroup) => props.sheets.open(groupStatsDetail(row, ctx.value, thresholds.value))

// The groups flipping most in the current window, against the window before: a count, so series colors.
const TOP = 10
interface ChartRow extends Record<string, unknown> {
  group: FlakyGroup
  flips: number
  previousFlips: number
}
const chartRows = computed<ChartRow[]>(() =>
  [...flaky.value]
    .sort((a, b) => b.flips - a.flips || b.flips30d - a.flips30d)
    .slice(0, TOP)
    .filter((row) => row.flips > 0)
    .map((group) => ({ group, flips: group.flips, previousFlips: group.previousFlips })),
)
const CHART_SERIES = computed<SeriesSpec[]>(() => [
  { key: 'flips', label: `Last ${windowLabel.value}`, color: SERIES.primary },
  { key: 'previousFlips', label: `${windowLabel.value} before`, color: SERIES.muted },
])

const alertFilter = <T extends { alerting: boolean }>(): FilterSpec<T>[] => [
  {
    id: 'alert',
    label: 'Alert',
    allLabel: 'All groups',
    options: [{ value: 'alerting', label: 'Alerting only' }],
    predicate: (row, value) => value !== 'alerting' || row.alerting,
  },
]
</script>

<template>
  <Card class="gap-4" data-testid="main-flaky-chart">
    <CardHeader>
      <CardTitle class="text-base">Most flips in the last {{ windowLabel }}</CardTitle>
      <CardDescription>
        Pass/fail changes between consecutive main builds that ran the group<template v-if="thresholds"> · alerts at {{ thresholds.minFlips }} flips</template>
      </CardDescription>
    </CardHeader>
    <CardContent class="px-3 sm:px-6">
      <BarChart
        v-if="chartRows.length"
        :rows="chartRows"
        :category="(row: ChartRow) => groupLabel(row.group.name)"
        :series="CHART_SERIES"
        :stacked="false"
        orientation="horizontal"
        :category-width="220"
        label="Outcome flips per group"
        hint="Click for the group"
        :on-select="(row: ChartRow) => open(row.group)"
      />
      <p v-else class="py-6 text-center text-sm text-muted-foreground">No group flipped in the last {{ windowLabel }}.</p>
    </CardContent>
  </Card>

  <Card class="gap-4" data-testid="main-flaky">
    <CardHeader>
      <CardTitle class="text-base">Flaky groups</CardTitle>
      <CardDescription>
        {{ integer(summary.alertingFlaky ?? 0) }} alerting<template v-if="thresholds"> at {{ thresholds.minFlips }} flips</template> of {{ integer(summary.flakyGroups ?? flaky.length) }} groups that flipped in 30 days<template v-if="(summary.flakyGroups ?? 0) > flaky.length">
          · the {{ integer(flaky.length) }} most active are listed</template>
      </CardDescription>
    </CardHeader>
    <CardContent class="px-4 sm:px-6">
      <DataTable
        :columns="flakyColumns(ctx)"
        :rows="flaky"
        :filters="alertFilter<FlakyGroup>()"
        :search-text="groupSearch"
        search-placeholder="Filter group, hardware or queue…"
        label="Flaky groups"
        empty-text="No group flipped between pass and fail on main."
        :on-row-click="open"
        @drill="sheets.open"
      />
    </CardContent>
  </Card>

  <Card class="gap-4" data-testid="main-retry">
    <CardHeader>
      <CardTitle class="text-base">Passes after a retry</CardTitle>
      <CardDescription>
        {{ integer(summary.alertingRetry ?? 0) }} alerting<template v-if="thresholds"> at {{ thresholds.minRecoveries }} recoveries</template> · hard-failed attempts that Buildkite retried and that then passed<template v-if="summary.recoveries !== null">
          · {{ integer(summary.recoveries) }} recoveries of {{ integer(summary.retryAttempts) }} retries in 30 days</template>
      </CardDescription>
    </CardHeader>
    <CardContent class="px-4 sm:px-6">
      <DataTable
        :columns="retryColumns(ctx)"
        :rows="retry"
        :filters="alertFilter<RetryGroup>()"
        :search-text="groupSearch"
        search-placeholder="Filter group, hardware or queue…"
        label="Groups that pass after a retry"
        empty-text="No group passed only after a retry on main."
        :on-row-click="open"
        @drill="sheets.open"
      />
    </CardContent>
  </Card>
</template>
