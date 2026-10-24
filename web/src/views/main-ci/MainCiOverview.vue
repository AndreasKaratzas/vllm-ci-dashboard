<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import DataTable from '@/components/table/DataTable.vue'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useClock } from '@/composables/useClock'
import type { SheetStack } from '@/composables/useSheetStack'
import { counted, integer } from '@/domain/format'
import {
  buildsBetween,
  flakinessThresholds,
  flakyGroups,
  isNewIncident,
  lastDayPoint,
  retryGroups,
  suspectRanges,
  type FlakyGroup,
  type MainBuild,
  type MainIncident,
  type RetryGroup,
  type TrendPoint,
} from '@/domain/mainCi'
import BuildStrip from '@/features/main-ci/BuildStrip.vue'
import {
  buildColumns,
  buildSearch,
  flakyColumns,
  groupStatsDetail,
  incidentColumns,
  incidentDetail,
  mainBuildDetail,
  retryColumns,
  trendPointDetail,
} from '@/features/main-ci/details'
import MainCiKpis from '@/features/main-ci/MainCiKpis.vue'
import MainCiTrendCard from '@/features/main-ci/MainCiTrendCard.vue'
import type { MainCi } from '@/features/main-ci/useMainCi'
import { slowdowns, type Slowdown } from '@/domain/slowdowns'
import { slowdownColumns, slowdownDetail } from '@/features/slowdowns/details'

const props = defineProps<{ mainCi: MainCi; sheets: SheetStack }>()
const now = useClock()
const summary = computed(() => props.mainCi.summary.value!)
const ctx = computed(() => props.mainCi.ctx.value)
const builds = computed(() => props.mainCi.builds.value)
const incidents = computed(() => props.mainCi.incidents.value)
const thresholds = computed(() => flakinessThresholds(ctx.value.record))
const windowDays = computed(() => thresholds.value?.windowDays ?? null)

const openBuild = (build: MainBuild) => props.sheets.open(mainBuildDetail(build, ctx.value))
const openPoint = (point: TrendPoint, label: string) => props.sheets.open(trendPointDetail(point, buildsBetween(builds.value, point.from, point.to), label, ctx.value))
const lastDay = computed(() => lastDayPoint(summary.value))
const openIncident = (row: MainIncident) => props.sheets.open(incidentDetail(row, ctx.value, incidents.value.issue, now.value))
const fresh = computed(() => incidents.value.active.filter((row) => isNewIncident(row, now.value)).length)
const sharedRanges = computed(() => suspectRanges(incidents.value.active))
const incidentNote = computed(() => {
  const active = incidents.value.active.length
  if (!active) return 'No confirmed failure on main'
  const parts = [`${counted(active, 'confirmed failure')}, newest first`]
  if (fresh.value) parts.push(`${integer(fresh.value)} new in 24 hours`)
  const shared = sharedRanges.value[0]
  if (shared) parts.push(`${integer(shared.incidents.length)} first failed at one commit`)
  return parts.join(' · ')
})

const alerting = <T extends { alerting: boolean }>(rows: T[]) => rows.filter((row) => row.alerting)
const flaky = computed(() => alerting(flakyGroups(ctx.value.record)))
const retry = computed(() => alerting(retryGroups(ctx.value.record)))
// Secondary panels show the top rows; their tabs hold the rest. The incident list gets the
// height of the two flaky lists beside it.
const PANEL_ROWS = 5
const INCIDENT_ROWS = 10
const incidentCols = computed(() => incidentColumns(ctx.value, incidents.value.active, now.value).filter((column) => ['group', 'severity', 'opened', 'since'].includes(column.id)))
const flakyCols = computed(() => flakyColumns(ctx.value).filter((column) => ['group', 'flips', 'rate'].includes(column.id)))
const retryCols = computed(() => retryColumns(ctx.value).filter((column) => ['group', 'recovered'].includes(column.id)))
const slow = computed(() => slowdowns(ctx.value.record))
const openSlowdown = (row: Slowdown) => props.sheets.open(slowdownDetail(row, slow.value, now.value))
const slowdownCols = computed(() => slowdownColumns(openSlowdown, now.value, { compact: true }))
</script>

<template>
  <MainCiKpis
    :summary="summary"
    :record="ctx.record"
    :builds="builds"
    :actions="{
      latest: builds[0] ? { label: 'Open build evidence', onOpen: () => openBuild(builds[0]!) } : undefined,
      rate: lastDay ? { label: 'Open the last 24 hours', onOpen: () => openPoint(lastDay!, 'last 24 hours') } : undefined,
      incidents: { label: 'Open incidents', to: { name: 'main', query: { view: 'incidents' } } },
      flaky: { label: 'Open flaky groups', to: { name: 'main', query: { view: 'flaky' } } },
    }"
  />

  <MainCiTrendCard :record="ctx.record" @select="openPoint" />

  <Card class="gap-4 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300" data-testid="main-builds">
    <CardHeader>
      <CardTitle class="text-base">Latest main builds</CardTitle>
      <CardDescription>Newest on the right · tile height follows the AMD job count · open a build for its failing groups</CardDescription>
    </CardHeader>
    <CardContent class="flex flex-col gap-6 px-4 sm:px-6">
      <BuildStrip :builds="builds" @select="openBuild" />
      <DataTable
        :columns="buildColumns()"
        :rows="builds"
        :page-size="10"
        :search-text="buildSearch"
        search-placeholder="Filter build, commit or result…"
        label="Main builds"
        empty-text="No completed main build in the cohort."
        :on-row-click="openBuild"
        @drill="sheets.open"
      />
    </CardContent>
  </Card>

  <div class="grid grid-cols-1 items-start gap-4 md:gap-6 @5xl/main:grid-cols-2 *:min-w-0">
    <Card class="gap-4" data-testid="main-incidents-panel">
      <CardHeader>
        <CardTitle class="text-base">Active incidents</CardTitle>
        <CardDescription>{{ incidentNote }}</CardDescription>
        <CardAction v-if="incidents.active.length > INCIDENT_ROWS">
          <Button variant="ghost" size="sm" as-child><RouterLink :to="{ name: 'main', query: { view: 'incidents' } }">All {{ integer(incidents.active.length) }}</RouterLink></Button>
        </CardAction>
      </CardHeader>
      <CardContent class="px-4 sm:px-6">
        <DataTable
          :columns="incidentCols"
          :rows="incidents.active.slice(0, INCIDENT_ROWS)"
          :page-size="false"
          :searchable="false"
          :column-menu="false"
          label="Active main incidents"
          empty-text="Every AMD group that ran on main is passing or resolved."
          :on-row-click="openIncident"
          @drill="sheets.open"
        />
        <p v-if="incidents.active.length" class="mt-3 text-xs text-muted-foreground">An incident stays open until its group passes again, so a passing latest build can still have open incidents.</p>
      </CardContent>
    </Card>
    <Card class="gap-4" data-testid="main-flaky-panel">
      <CardHeader>
        <CardTitle class="text-base">Flaky and retry alerts</CardTitle>
        <CardDescription>Groups over their alert threshold{{ windowDays ? ` in the last ${windowDays} days` : '' }}</CardDescription>
        <CardAction v-if="flaky.length + retry.length > 0">
          <Button variant="ghost" size="sm" as-child><RouterLink :to="{ name: 'main', query: { view: 'flaky' } }">All groups</RouterLink></Button>
        </CardAction>
      </CardHeader>
      <CardContent class="flex flex-col gap-5 px-4 sm:px-6">
        <section class="flex flex-col gap-2">
          <h3 class="text-sm font-medium">
            Outcome flips <span class="font-normal text-muted-foreground">· {{ integer(summary.alertingFlaky ?? flaky.length) }} alerting<template v-if="thresholds"> at {{ thresholds.minFlips }} flips</template></span>
          </h3>
          <DataTable
            :columns="flakyCols"
            :rows="flaky.slice(0, PANEL_ROWS)"
            :page-size="false"
            :searchable="false"
            :column-menu="false"
            label="Alerting flaky groups"
            empty-text="No group flips often enough to alert."
            :on-row-click="(row: FlakyGroup) => sheets.open(groupStatsDetail(row, ctx, thresholds))"
            @drill="sheets.open"
          />
        </section>
        <section class="flex flex-col gap-2">
          <h3 class="text-sm font-medium">
            Passes after a retry
            <span class="font-normal text-muted-foreground">· {{ integer(summary.alertingRetry ?? retry.length) }} alerting<template v-if="thresholds"> at {{ thresholds.minRecoveries }} recoveries</template></span>
          </h3>
          <DataTable
            :columns="retryCols"
            :rows="retry.slice(0, PANEL_ROWS)"
            :page-size="false"
            :searchable="false"
            :column-menu="false"
            label="Alerting retry groups"
            empty-text="No group recovers on retry often enough to alert."
            :on-row-click="(row: RetryGroup) => sheets.open(groupStatsDetail(row, ctx, thresholds))"
            @drill="sheets.open"
          />
        </section>
      </CardContent>
    </Card>
  </div>

  <Card v-if="slow.available" class="gap-4" data-testid="main-slowdowns-panel">
    <CardHeader>
      <CardTitle class="text-base">Slower test steps</CardTitle>
      <CardDescription>
        {{ slow.active.length ? `${counted(slow.active.length, 'step')} still slower than before, largest increase first` : 'Every AMD MI test step on main runs at its usual level' }}
      </CardDescription>
      <CardAction>
        <Button variant="ghost" size="sm" as-child><RouterLink :to="{ name: 'analytics', query: { view: 'slowdowns' } }">All slowdowns</RouterLink></Button>
      </CardAction>
    </CardHeader>
    <CardContent v-if="slow.active.length" class="px-4 sm:px-6">
      <DataTable
        :columns="slowdownCols"
        :rows="slow.active.slice(0, PANEL_ROWS)"
        :page-size="false"
        :searchable="false"
        :column-menu="false"
        label="Active slowdowns"
        :on-row-click="openSlowdown"
      />
    </CardContent>
  </Card>
</template>
