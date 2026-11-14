<script setup lang="ts">
import { ChevronDown, ChevronRight, ExternalLink, Timer } from '@lucide/vue'
import { useElementSize } from '@vueuse/core'
import { computed, h, ref } from 'vue'
import BarChart from '@/components/charts/BarChart.vue'
import { plainNumber, type SeriesSpec } from '@/components/charts/chart'
import SheetStack from '@/components/evidence/SheetStack.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import DataTable from '@/components/table/DataTable.vue'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useClock } from '@/composables/useClock'
import { useSheetStack } from '@/composables/useSheetStack'
import { useSubviewHeader } from '@/composables/useSubviewHeader'
import { counted, integer } from '@/domain/format'
import { addedMinutes, increaseLabel, minutes, rulesText, slowdowns, unavailableText, type Slowdown } from '@/domain/slowdowns'
import { DATA_COLLECTION_RUNS_URL } from '@/domain/urls'
import { useMainCi } from '@/features/main-ci/useMainCi'
import { slowdownColumns, slowdownDetail, slowdownName, slowdownSearch } from '@/features/slowdowns/details'
import { SERIES } from '@/lib/palette'

const mainCi = useMainCi()
const sheets = useSheetStack()
const now = useClock()

const data = computed(() => slowdowns(mainCi.ctx.value.record))
const active = computed(() => data.value.active)
const settled = computed(() => data.value.settled)
const largest = computed(() => active.value[0])
const open = (row: Slowdown) => sheets.open(slowdownDetail(row, data.value, now.value))
const columns = computed(() => slowdownColumns(open, now.value))
const showSettled = ref(false)


useSubviewHeader(() => ({
  observedAt: data.value.watcherLastRun ?? undefined,
  about: () =>
    (data.value.rules ? rulesText(data.value.rules) : ['AMD MI test steps on main whose job minutes rose to a new, sustained level.']).map((text) => h('p', text)),
}))

const CHART_SERIES: SeriesSpec[] = [
  { key: 'before', label: 'Before', color: SERIES.primary },
  { key: 'added', label: 'Added since', color: SERIES.secondary },
]
const chartRows = computed(() => active.value.map((row) => ({ row, before: row.baselineMins, added: row.increaseMins })))
const chartHost = ref<HTMLElement | null>(null)
const { width } = useElementSize(chartHost)
const narrow = computed(() => !!width.value && width.value < 520)
const categoryWidth = computed(() => (narrow.value ? 124 : 168))
const minutesTick = (value: number) => (narrow.value ? `${plainNumber(value)}m` : `${plainNumber(value)} min`)
</script>

<template>
  <AsyncBoundary :loading="!mainCi.loaded.value && !mainCi.error.value && mainCi.published.value" :error="mainCi.error.value" @retry="mainCi.retry">
    <div class="flex flex-col gap-4 md:gap-6" data-testid="analytics-slowdowns">
      <EmptyState
        v-if="!data.available"
        :icon="Timer"
        title="Slowdowns are unavailable"
        :description="`${unavailableText(data.reason)} Slowdowns return once a Data Collection run's duration watcher step evaluates the newest main builds; that step's log says why it did not.`"
        :cause="data.reason ?? undefined"
        data-testid="slowdowns-unavailable"
      >
        <template #action>
          <Button variant="outline" size="sm" as="a" :href="DATA_COLLECTION_RUNS_URL" target="_blank" rel="noopener">Data Collection runs <ExternalLink /></Button>
        </template>
      </EmptyState>
      <template v-else>
        <StatGrid :cols="3" data-testid="slowdowns-kpis">
          <KpiCard
            label="Active slowdowns"
            :value="active.length"
            :unit="active.length === 1 ? 'step' : 'steps'"
            :status="active.length ? { tone: 'soft', label: 'Slower' } : undefined"
            :footnote="active.length ? `+${minutes(addedMinutes(active))} job minutes per main build that runs them all; they run in parallel, so not wall time` : 'Every AMD MI test step runs at its usual level'"
            data-testid="kpi-slowdowns-active"
          />
          <KpiCard
            label="Largest increase"
            :value="largest ? increaseLabel(largest) : null"
            :footnote="largest ? slowdownName(largest) : 'No active slowdown'"
            :action="largest ? { label: 'Open its builds', onOpen: () => open(largest!) } : undefined"
            data-testid="kpi-slowdowns-largest"
          />
          <KpiCard
            label="Settled"
            :value="settled.length"
            :unit="settled.length === 1 ? 'step' : 'steps'"
            :footnote="data.rules ? `No further rise for ${data.rules.settleAfterDays} days; the new normal` : 'No further rise; the new normal'"
            :action="settled.length ? { label: showSettled ? 'Hide settled slowdowns' : 'Show settled slowdowns', onOpen: () => (showSettled = !showSettled) } : undefined"
            data-testid="kpi-slowdowns-settled"
          />
        </StatGrid>

        <Card v-if="active.length" class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="slowdowns-chart">
          <CardHeader>
            <CardTitle as="h2">Job minutes before and since</CardTitle>
            <CardDescription>Each step's job minutes per build, summed across its shards: the earlier level and what it added</CardDescription>
          </CardHeader>
          <CardContent class="px-2 sm:px-6">
            <div ref="chartHost" class="min-w-0">
              <BarChart
                :rows="chartRows"
                :category="(point: { row: Slowdown }) => slowdownName(point.row)"
                :series="CHART_SERIES"
                orientation="horizontal"
                :category-width="categoryWidth"
                :value-format="minutesTick"
                label="Active slowdowns: minutes before and added since"
                hint="Click for its builds"
                inspect-placement="legend"
                :on-select="(point: { row: Slowdown }) => open(point.row)"
              />
            </div>
          </CardContent>
        </Card>

        <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="slowdowns-active">
          <CardHeader>
            <CardTitle as="h2">Active slowdowns</CardTitle>
            <CardDescription>AMD MI test steps on main still slower than before, largest increase first · open one for its builds</CardDescription>
          </CardHeader>
          <CardContent>
            <EmptyState v-if="!active.length" :icon="Timer" title="No active slowdowns" description="Every AMD MI test step on main runs at its usual level." />
            <DataTable
              v-else
              :columns="columns"
              :rows="active"
              :page-size="15"
              :search-text="slowdownSearch"
              :on-row-click="open"
              search-placeholder="Filter step, hardware, queue or commit…"
              label="Active slowdowns"
            />
          </CardContent>
        </Card>

        <Card v-if="settled.length" class="gap-4" data-testid="slowdowns-settled">
          <CardHeader>
            <CardTitle as="h2">
              <Button variant="ghost" size="sm" class="-ml-2 gap-1.5 text-base font-semibold" :aria-expanded="showSettled" aria-controls="slowdowns-settled-table" @click="showSettled = !showSettled">
                <component :is="showSettled ? ChevronDown : ChevronRight" aria-hidden="true" />
                Settled · {{ integer(settled.length) }}
              </Button>
            </CardTitle>
            <CardDescription>{{ counted(settled.length, 'step') }} with no further rise {{ data.rules ? `for ${data.rules.settleAfterDays} days` : 'since' }}, now their usual level</CardDescription>
          </CardHeader>
          <CardContent v-if="showSettled" id="slowdowns-settled-table">
            <DataTable :columns="columns" :rows="settled" :page-size="10" :search-text="slowdownSearch" :on-row-click="open" label="Settled slowdowns" />
          </CardContent>
        </Card>
      </template>
    </div>
    <SheetStack :sheets="sheets" />
  </AsyncBoundary>
</template>
