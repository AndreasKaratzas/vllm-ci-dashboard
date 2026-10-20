<script setup lang="ts">
import { ChevronDown, ChevronRight, Timer } from '@lucide/vue'
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
import { counted, integer, reasonText } from '@/domain/format'
import { addedMinutes, increaseLabel, minutes, rulesText, slowdowns, type Slowdown } from '@/domain/slowdowns'
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

const UNAVAILABLE: Record<string, string> = {
  duration_watcher_state_unavailable: 'The duration watcher has not published its state for this collection.',
  duration_watcher_state_stale: 'The duration watcher did not evaluate the builds of this collection.',
  slowdowns_unpublished: 'This snapshot does not publish slowdowns yet.',
}

useSubviewHeader(() => ({
  observedAt: data.value.watcherLastRun ?? undefined,
  about: () =>
    (data.value.rules ? rulesText(data.value.rules) : ['AMD MI test steps on main whose wall time rose to a new, sustained level.']).map((text) => h('p', text)),
}))

const CHART_SERIES: SeriesSpec[] = [
  { key: 'before', label: 'Before', color: SERIES.muted },
  { key: 'added', label: 'Added since', color: SERIES.secondary },
]
const chartRows = computed(() => active.value.map((row) => ({ row, before: row.baselineMins, added: row.increaseMins })))
const chartHost = ref<HTMLElement | null>(null)
const { width } = useElementSize(chartHost)
const categoryWidth = computed(() => (width.value && width.value < 520 ? 92 : 168))
</script>

<template>
  <AsyncBoundary :loading="!mainCi.loaded.value && !mainCi.error.value && mainCi.published.value" :error="mainCi.error.value" @retry="mainCi.retry">
    <div class="flex flex-col gap-4 md:gap-6" data-testid="analytics-slowdowns">
      <EmptyState
        v-if="!data.available"
        :icon="Timer"
        title="Slowdowns are unavailable"
        :description="UNAVAILABLE[data.reason ?? ''] ?? `${reasonText(data.reason)}.`"
        :cause="data.reason ?? undefined"
        data-testid="slowdowns-unavailable"
      />
      <template v-else>
        <StatGrid :cols="4" data-testid="slowdowns-kpis">
          <KpiCard
            label="Active slowdowns"
            :value="active.length"
            :unit="active.length === 1 ? 'step' : 'steps'"
            :status="active.length ? { tone: 'soft', label: 'Slower' } : undefined"
            :footnote="active.length ? `+${minutes(addedMinutes(active))} of agent time per main build that runs them all` : 'Every AMD MI test step runs at its usual level'"
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
          <KpiCard
            label="Tracking issue"
            :value="data.issue ? `#${data.issue.number}` : null"
            :status="data.suppressed ? { tone: 'neutral', label: 'Closed by hand' } : undefined"
            :footnote="
              data.issue
                ? data.suppressed
                  ? 'Stays closed until the set of slowdowns changes'
                  : 'Lists every active slowdown'
                : 'No issue is open'
            "
            :action="data.issue ? { label: `Open issue #${data.issue.number}`, href: data.issue.url } : undefined"
            data-testid="kpi-slowdowns-issue"
          />
        </StatGrid>

        <Card v-if="active.length" class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="slowdowns-chart">
          <CardHeader>
            <CardTitle as="h2">Wall time before and since</CardTitle>
            <CardDescription>Each step's summed shard minutes per build: the earlier level and what it added</CardDescription>
          </CardHeader>
          <CardContent class="px-2 sm:px-6">
            <div ref="chartHost" class="min-w-0">
              <BarChart
                :rows="chartRows"
                :category="(point: { row: Slowdown }) => slowdownName(point.row)"
                :series="CHART_SERIES"
                orientation="horizontal"
                :category-width="categoryWidth"
                :value-format="(value: number) => `${plainNumber(value)} min`"
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
            <CardDescription>{{ counted(settled.length, 'step') }} with no further rise for {{ data.rules?.settleAfterDays ?? 14 }} days, now their usual level</CardDescription>
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
