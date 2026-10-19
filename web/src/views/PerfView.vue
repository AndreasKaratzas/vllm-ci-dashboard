<script setup lang="ts">
import { ArrowLeft, DatabaseZap, ExternalLink, History, SearchX } from '@lucide/vue'
import { computed } from 'vue'
import SheetStack from '@/components/evidence/SheetStack.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import PageHeader, { type Freshness } from '@/components/layout/PageHeader.vue'
import SubNav from '@/components/layout/SubNav.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { useClock } from '@/composables/useClock'
import { useDataset } from '@/composables/useDataset'
import { useOperations } from '@/composables/useOperations'
import { useQueryState } from '@/composables/useQueryState'
import { useSheetStack } from '@/composables/useSheetStack'
import { age, integer, shortDate, type Rec } from '@/domain/format'
import { areaFreshness, sourceFreshness, type FreshnessState } from '@/domain/freshness'
import {
  PERF_VIEWS,
  accuracyBlock,
  accuracyRows,
  filterPerfModels,
  perfChanges,
  perfDelta,
  perfHardwareRows,
  perfLatestBuild,
  perfModelRows,
  perfModels,
  perfNightlyActivity,
  perfPage,
  perfPipelineUrl,
  perfShortModel,
  type PerfChange,
  type PerfView as PerfViewId,
} from '@/domain/perf'
import { hardwareColor } from '@/lib/palette'
import type { Tone } from '@/lib/tone'
import { changesDetail, familiesDetail, hardwareDetail, historyDetail, isHistoryDetail, modelDetail, pointDetail } from './perf/details'
import PerfAccuracy from './perf/PerfAccuracy.vue'
import PerfModel from './perf/PerfModel.vue'
import PerfSeriesChart from './perf/PerfSeriesChart.vue'
import PerfSummary from './perf/PerfSummary.vue'

const view = useQueryState<PerfViewId>('view', { default: 'performance', allowed: PERF_VIEWS, history: 'push' })
const model = useQueryState<string>('model', { default: 'all', history: 'push' })
const device = useQueryState<string>('device', { default: 'all', history: 'push' })
const state = computed(() => ({ view: view.value, model: model.value, device: device.value }))

const dataset = useDataset('perf_eval')
const perf = dataset.data
const { ops } = useOperations()
const now = useClock()
const sheets = useSheetStack()

const page = computed(() => (perf.value ? perfPage(perf.value, state.value) : null))
const ready = computed(() => (page.value && page.value.kind !== 'unavailable' ? page.value : null))
const allModels = computed(() => perfModels(perf.value))
const filtered = computed(() => filterPerfModels(allModels.value, state.value))
const filterActive = computed(() => state.value.model !== 'all' || state.value.device !== 'all')
const modelRows = computed(() => perfModelRows(filtered.value))
const changes = computed(() => perfChanges(filtered.value))
const regressions = computed(() => changes.value.filter((change) => change.tone === 'hard'))
const improvements = computed(() => changes.value.filter((change) => change.tone === 'pass'))
const hardware = computed(() => (perf.value ? perfHardwareRows(perf.value) : []))
const nightlies = computed(() => perfNightlyActivity(allModels.value).slice(-30))
const latestBuild = computed(() => perfLatestBuild(allModels.value))
const pipelineUrl = computed(() => (perf.value ? perfPipelineUrl(perf.value) : ''))
const thresholds = computed<Rec>(() => perf.value?.thresholds ?? {})

const VIEWS = [
  { value: 'performance', label: 'Performance' },
  { value: 'accuracy', label: 'Accuracy' },
]

const FRESHNESS_TONE: Record<FreshnessState, Tone> = { fresh: 'pass', warn: 'soft', stale: 'hard' }
const freshness = computed<Freshness | undefined>(() => {
  if (!perf.value) return undefined
  const summary = areaFreshness(sourceFreshness(ops.value, perf.value, now.value), 'perf')
  if (!summary) return undefined
  const source = summary.sources[0]
  const target = source ? ` Target: collected within ${Math.round(source.warnMinutes / 60)} h.` : ''
  return {
    text: summary.timestamp ? `Collected ${age(summary.timestamp, now.value)}` : 'Collection time unknown',
    tone: FRESHNESS_TONE[summary.state],
    detail: summary.timestamp ? `perf_eval.json was generated ${shortDate(summary.timestamp)}.${target}` : `perf_eval.json carries no generation time.${target}`,
  }
})

// KPI strings are integer()-formatted; '-' means the summary has no value.
const count = (formatted: string) => (/\d/.test(formatted) ? Number(formatted.replace(/\D/g, '')) : null)
function spark(pick: (nightly: (typeof nightlies.value)[number]) => number) {
  return nightlies.value.map(pick)
}
function lastDelta(values: number[], unit = '') {
  if (values.length < 2) return undefined
  const value = values.at(-1)! - values.at(-2)!
  return { value, text: `${value > 0 ? '+' : ''}${integer(value)}${unit}`, goodWhen: 'up' as const }
}
function changeDelta(change: PerfChange | undefined) {
  if (!change || !Number.isFinite(change.deltaPct)) return undefined
  return { value: change.deltaPct, text: perfDelta(change.block).label, goodWhen: change.block.direction === 'lower' ? ('down' as const) : ('up' as const) }
}
const changeWhere = (change: PerfChange | undefined) => (change ? `${perfShortModel(change.model.model)} · ${change.config.label}` : undefined)
const inFilter = computed(() => (filterActive.value ? ' in filter' : ''))

function selectModel(name: string) {
  sheets.close()
  model.value = name
}
function selectDevice(id: string) {
  sheets.close()
  device.value = id
}
const modelSelect = computed({ get: () => model.value, set: (value: string) => selectModel(String(value || 'all')) })
// A single toggle group clears its value when the active item is pressed again; the filter stays.
const deviceToggle = computed({ get: () => device.value, set: (value: string) => value && selectDevice(String(value)) })
const deviceLabel = computed(() => ready.value?.devices.find((d) => d.id === device.value)?.label ?? 'All hardware')

function openHistory(owner: Rec, config: Rec, metricName: string, block: Rec) {
  sheets.open(historyDetail(owner, config, metricName, block))
}
const openRegressions = () =>
  sheets.open(changesDetail('perf-regressions', 'Regressed metrics', `Worse than the previous nightly beyond the threshold${inFilter.value}`, regressions.value))
const openImprovements = () =>
  sheets.open(changesDetail('perf-improvements', 'Improved metrics', `Better than the previous nightly beyond the threshold${inFilter.value}`, improvements.value))
</script>

<template>
  <div class="flex min-w-0 flex-col gap-4 md:gap-6">
    <PageHeader
      title="Performance & Evaluation"
      description="AMD nightly throughput, latency and accuracy from perf-eval artifacts, with build and commit provenance."
      :freshness="freshness"
    >
      <template #about>
        <p>Each metric compares its latest nightly with the previous one. A change counts as a regression or an improvement only past the threshold:
          {{ thresholds.perf_rel !== undefined ? `${(thresholds.perf_rel * 100).toFixed(1)}% relative` : 'the relative band' }} for performance,
          {{ thresholds.accuracy_abs !== undefined ? `${(thresholds.accuracy_abs * 100).toFixed(1)} points` : 'the absolute band' }} for accuracy.</p>
        <p>Only AMD Instinct (ROCm) records are shown. If any record names a non-MI device or no GPU, the page withholds all data rather than mixing hardware.</p>
        <p>The model overview summarizes every model; choose a model to chart its metric histories one configuration at a time.</p>
      </template>
      <template #actions>
        <Badge v-if="latestBuild" variant="outline" class="h-8 gap-1.5 px-2.5 font-normal text-muted-foreground" :title="latestBuild.date ? shortDate(latestBuild.date) : undefined" data-testid="perf-latest-build">
          <History aria-hidden="true" /> Latest perf build #{{ latestBuild.number }}<template v-if="latestBuild.date"> · {{ age(latestBuild.date, now) }}</template>
        </Badge>
        <Button v-if="pipelineUrl" variant="outline" size="sm" as="a" :href="pipelineUrl" target="_blank" rel="noopener">
          Pipeline <ExternalLink />
        </Button>
      </template>
    </PageHeader>

    <SubNav :items="VIEWS" default-value="performance" label="Performance views" />

    <AsyncBoundary
      :loading="dataset.loading.value && !perf"
      :error="perf ? null : dataset.error.value"
      :refreshing="dataset.refreshing.value"
      @retry="dataset.refresh()"
    >
      <EmptyState
        v-if="page?.kind === 'unavailable'"
        :icon="DatabaseZap"
        title="AMD MI performance data is unavailable."
        description="Current MI GPU metric records are required."
        data-testid="perf-unavailable"
      />
      <template v-else-if="ready">
        <div class="flex flex-wrap items-center gap-2" data-testid="perf-toolbar">
          <Button v-if="ready.showBack" variant="outline" size="sm" aria-label="Back to all performance models" @click="selectModel('all')">
            <ArrowLeft /> All models
          </Button>
          <Select v-model="modelSelect">
            <SelectTrigger size="sm" class="w-full sm:w-72" data-testid="perf-model-select" aria-label="Model">
              <SelectValue placeholder="Unknown model" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All models</SelectItem>
              <SelectItem v-for="name in ready.modelNames" :key="name" :value="name">{{ name }}</SelectItem>
            </SelectContent>
          </Select>
          <template v-if="ready.devices.length">
            <ToggleGroup v-model="deviceToggle" type="single" variant="outline" size="sm" class="ml-auto hidden sm:flex" aria-label="Performance hardware filter">
              <ToggleGroupItem value="all">All</ToggleGroupItem>
              <ToggleGroupItem v-for="d in ready.devices" :key="d.id" :value="d.id" class="gap-1.5">
                <span class="size-2 rounded-full" :style="{ backgroundColor: hardwareColor(d.id) }" aria-hidden="true" />{{ d.label }}
              </ToggleGroupItem>
            </ToggleGroup>
            <Select v-model="deviceToggle">
              <SelectTrigger size="sm" class="w-full sm:hidden" aria-label="Performance hardware filter">
                <SelectValue>{{ deviceLabel }}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All hardware</SelectItem>
                <SelectItem v-for="d in ready.devices" :key="d.id" :value="d.id">{{ d.label }}</SelectItem>
              </SelectContent>
            </Select>
          </template>
        </div>

        <StatGrid :cols="5">
          <KpiCard
            label="AMD models"
            :value="count(ready.kpis.models)"
            :spark="spark((n) => n.models)"
            :delta="lastDelta(spark((n) => n.models))"
            :headline="nightlies.length ? `${integer(nightlies.at(-1)!.models)} in the latest nightly` : undefined"
            footnote="nightly model families"
            :action="{ label: 'Inspect model families', onOpen: () => perf && sheets.open(familiesDetail(perf, selectModel)) }"
            data-testid="kpi-perf-models"
          />
          <KpiCard
            label="Nightlies tracked"
            :value="count(ready.kpis.nightlies)"
            :spark="spark((n) => n.points)"
            :delta="lastDelta(spark((n) => n.points), ' pts')"
            :headline="`${ready.kpis.points} metric points`"
            :footnote="`${ready.kpis.pointsMeta} · across retained series`"
            :action="{ label: 'Open perf-eval pipeline', href: pipelineUrl }"
            data-testid="kpi-perf-nightlies"
          />
          <KpiCard
            label="Regressions"
            :value="regressions.length"
            :delta="changeDelta(regressions[0])"
            :headline="regressions[0] ? `Largest: ${regressions[0].label}` : `None${inFilter} vs previous nightly`"
            :footnote="changeWhere(regressions[0]) ?? `Across ${integer(modelRows.length)} models${inFilter}`"
            :action="{ label: 'Inspect regressed metrics', onOpen: openRegressions }"
            data-testid="kpi-perf-regressions"
          />
          <KpiCard
            label="Improvements"
            :value="improvements.length"
            :delta="changeDelta(improvements[0])"
            :headline="improvements[0] ? `Largest: ${improvements[0].label}` : `None${inFilter} vs previous nightly`"
            :footnote="changeWhere(improvements[0]) ?? `Across ${integer(modelRows.length)} models${inFilter}`"
            :action="{ label: 'Inspect improved metrics', onOpen: openImprovements }"
            data-testid="kpi-perf-improvements"
          />
          <KpiCard
            label="AMD hardware"
            :value="ready.kpis.hardware"
            :headline="`${integer(hardware.reduce((sum, row) => sum + row.configs, 0))} configurations`"
            footnote="ROCm only"
            :action="{ label: 'Inspect hardware', onOpen: () => sheets.open(hardwareDetail(hardware, ready!.kpis.hardwareDetail, selectDevice)) }"
            data-testid="kpi-perf-hardware"
          />
        </StatGrid>

        <EmptyState v-if="ready.kind === 'empty'" :icon="SearchX" title="No model matches the selected filters." data-testid="perf-empty">
          <template #action>
            <Button variant="outline" size="sm" @click="selectDevice('all')">Show all hardware</Button>
          </template>
        </EmptyState>
        <PerfAccuracy
          v-else-if="view === 'accuracy'"
          :rows="accuracyRows(filtered)"
          @history="(row) => sheets.open(historyDetail(row.model, { label: 'Accuracy' }, row.task.metric, accuracyBlock(row.task)))"
          @point="(point, unit) => sheets.open(pointDetail(point, unit))"
        />
        <PerfSummary
          v-else-if="ready.kind === 'summary'"
          :rows="ready.rows"
          :changes="changes"
          :hardware="hardware"
          :device="device"
          @select="selectModel"
          @device="selectDevice"
          @drill="sheets.open"
        />
        <template v-else-if="ready.kind === 'performance'">
          <PerfModel
            v-for="section in ready.sections"
            :key="section.model.model"
            :section="section"
            @model="(m) => sheets.open(modelDetail(m))"
            @history="openHistory"
            @point="(point, unit) => sheets.open(pointDetail(point, unit))"
          />
        </template>
      </template>
    </AsyncBoundary>

    <SheetStack v-slot="{ detail }" :sheets="sheets">
      <PerfSeriesChart
        v-if="isHistoryDetail(detail)"
        :series="detail.chart.series"
        :unit="detail.chart.unit"
        :label="detail.chart.label"
        :color="detail.chart.color"
        class="h-[240px]"
        @select="(point) => sheets.push(pointDetail(point, detail.chart.unit))"
      />
    </SheetStack>
  </div>
</template>
