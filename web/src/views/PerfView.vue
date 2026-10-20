<script setup lang="ts">
import { ArrowLeft, DatabaseZap, ExternalLink, History, SearchX } from '@lucide/vue'
import { computed } from 'vue'
import SheetStack from '@/components/evidence/SheetStack.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import { SUB_VIEWS } from '@/components/layout/navigation'
import SubNav from '@/components/layout/SubNav.vue'
import type { Delta } from '@/components/stat/DeltaBadge.vue'
import KpiCard, { type KpiAction } from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import { lastDelta } from '@/components/stat/trend'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { useClock } from '@/composables/useClock'
import { useDataset } from '@/composables/useDataset'
import { useOperations } from '@/composables/useOperations'
import { useQueryState } from '@/composables/useQueryState'
import { useSheetStack } from '@/composables/useSheetStack'
import { age, integer, shortDate, type Rec } from '@/domain/format'
import { areaFreshness, sourceFreshness } from '@/domain/freshness'
import { freshnessChip } from '@/features/freshness/freshness'
import {
  PERF_VIEWS,
  accuracyBlock,
  accuracyRows,
  perfAccuracyOnly,
  perfChanges,
  perfHardwareLabel,
  perfHardwareRows,
  perfLatestBuild,
  perfModelRows,
  perfModels,
  perfNightlyActivity,
  perfPage,
  perfPipelineUrl,
  perfScopedModels,
  perfShortModel,
  type PerfChange,
  type PerfView as PerfViewId,
} from '@/domain/perf'
import { hardwareColor } from '@/lib/palette'
import {
  blockDelta,
  changesDetail,
  changeWhere,
  familiesDetail,
  hardwareDetail,
  historyDetail,
  isHistoryDetail,
  modelDetail,
  nightliesDetail,
  pointDetail,
  type PointContext,
} from './perf/details'
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
const thresholds = computed<Rec>(() => perf.value?.thresholds ?? {})
const allModels = computed(() => perfModels(perf.value))
const filterActive = computed(() => state.value.model !== 'all' || state.value.device !== 'all')
const inFilter = computed(() => (filterActive.value ? ' in filter' : ''))
const accuracyView = computed(() => view.value === 'accuracy')

// Everything below follows the model and hardware filters; the Accuracy view counts accuracy tasks only.
const scoped = computed(() => perfScopedModels(allModels.value, state.value))
const counted = computed(() => (accuracyView.value ? perfAccuracyOnly(scoped.value) : scoped.value))
const modelRows = computed(() => perfModelRows(scoped.value))
const changes = computed(() => perfChanges(counted.value))
// Models with at least one counted metric.
const measured = computed(() => counted.value.filter((m) => (m.perf_configs || []).length || (m.accuracy_tasks || []).length).length)
const regressions = computed(() => changes.value.filter((change) => change.tone === 'hard'))
const improvements = computed(() => changes.value.filter((change) => change.tone === 'pass'))
const activity = computed(() => perfNightlyActivity(counted.value, thresholds.value))
const recent = computed(() => activity.value.slice(-30))
const hardware = computed(() => (perf.value ? perfHardwareRows(perf.value) : []))
const latestBuild = computed(() => perfLatestBuild(allModels.value))
const pipelineUrl = computed(() => (perf.value ? perfPipelineUrl(perf.value) : ''))

const freshness = computed(() => (perf.value ? freshnessChip(areaFreshness(sourceFreshness(ops.value, perf.value, now.value), 'perf'), now.value) : undefined))

// KPI strings are integer()-formatted; '-' means the summary has no value.
const count = (formatted: string) => (/\d/.test(formatted) ? Number(formatted.replace(/\D/g, '')) : null)
const PRECEDING = { label: 'vs the preceding nightly' }
const spark = (pick: (nightly: (typeof recent.value)[number]) => number) => recent.value.map(pick)

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

const openHistory = (owner: Rec, config: Rec, metricName: string, block: Rec) => sheets.open(historyDetail(owner, config, metricName, block, thresholds.value))
const openPoint = (point: Rec, context: PointContext) => sheets.open(pointDetail(point, context, thresholds.value))
const scopeLabel = computed(() => [accuracyView.value ? 'accuracy' : null, filterActive.value ? 'in filter' : null].filter(Boolean).join(', '))
const openNightlies = () =>
  sheets.open(nightliesDetail(activity.value, `${integer(activity.value.length)} retained nightlies${scopeLabel.value ? ` (${scopeLabel.value})` : ''}`))
const openChanges = (tone: 'hard' | 'pass') =>
  sheets.open(
    changesDetail(
      tone === 'hard' ? 'perf-regressions' : 'perf-improvements',
      `${tone === 'hard' ? 'Regressed' : 'Improved'} ${accuracyView.value ? 'accuracy ' : ''}metrics`,
      `${tone === 'hard' ? 'Worse' : 'Better'} than the previous nightly beyond the threshold${inFilter.value}`,
      tone === 'hard' ? regressions.value : improvements.value,
      thresholds.value,
    ),
  )
const openHardware = () =>
  sheets.open(hardwareDetail(hardware.value, ready.value?.kpis.hardware ?? '', perfChanges(allModels.value).filter((c) => c.kind === 'accuracy').length, selectDevice))

interface Kpi {
  id: string
  label: string
  value: number | string | null
  spark?: number[]
  delta?: Delta
  headline?: string
  footnote?: string
  action?: KpiAction
}

function changeKpi(tone: 'hard' | 'pass'): Kpi {
  const list = tone === 'hard' ? regressions.value : improvements.value
  const largest: PerfChange | undefined = list[0]
  const pick = tone === 'hard' ? (n: (typeof recent.value)[number]) => n.regressions : (n: (typeof recent.value)[number]) => n.improvements
  return {
    id: tone === 'hard' ? 'regressions' : 'improvements',
    label: tone === 'hard' ? 'Regressions' : 'Improvements',
    value: list.length,
    spark: spark(pick),
    delta: lastDelta(spark(pick), tone === 'hard' ? 'down' : 'up', PRECEDING),
    headline: largest ? `Largest: ${largest.label}, ${perfShortModel(largest.model.model)}` : `None${inFilter.value} vs previous nightly`,
    footnote: largest ? `${blockDelta(largest.block).text} · ${changeWhere(largest).split(' · ').slice(1).join(' · ')}` : `Across ${integer(measured.value)} models${inFilter.value}`,
    action: { label: `Inspect ${tone === 'hard' ? 'regressed' : 'improved'} metrics`, onOpen: () => openChanges(tone) },
  }
}

const nightliesKpi = computed<Kpi>(() => {
  const points = activity.value.reduce((sum, n) => sum + n.points, 0)
  const base = { id: 'nightlies', spark: spark((n) => n.points), action: { label: 'Inspect nightlies', onOpen: openNightlies } }
  if (accuracyView.value) {
    const latest = activity.value.at(-1)
    return { ...base, label: 'Accuracy nightlies', value: activity.value.length, headline: `${integer(points)} accuracy points`, footnote: latest ? `Latest ${shortDate(latest.date)}` : undefined }
  }
  if (state.value.model !== 'all') {
    const latest = scoped.value[0]?.latest || {}
    return {
      ...base,
      label: 'Nightlies tracked',
      value: scoped.value.reduce((sum, m) => sum + Number(m.nightly_count || 0), 0),
      headline: latest.build_number ? `Latest build #${latest.build_number}` : `${integer(points)} metric points`,
      footnote: `${integer(points)} metric points${latest.date ? ` · ${shortDate(latest.date)}` : ''}`,
    }
  }
  const kpis = ready.value!.kpis
  return {
    ...base,
    label: 'Nightlies tracked',
    value: filterActive.value ? activity.value.length : count(kpis.nightlies),
    headline: `${filterActive.value ? integer(points) : kpis.points} metric points`,
    footnote: filterActive.value ? 'across retained series in filter' : `${kpis.pointsMeta} · across retained series`,
  }
})

const scopeKpi = computed<Kpi>(() => {
  if (accuracyView.value) {
    const rows = accuracyRows(scoped.value)
    const families = new Set(rows.map((row) => row.model.model)).size
    return { id: 'accuracy', label: 'Accuracy metrics', value: rows.length, headline: `${integer(families)} ${families === 1 ? 'model' : 'models'} with lm-eval results`, footnote: 'higher is better' }
  }
  if (state.value.model !== 'all') {
    const configs = scoped.value.flatMap((m) => m.perf_configs || [])
    const metrics = configs.reduce((sum, config) => sum + Object.keys(config.metrics || {}).length, 0)
    const target = scoped.value[0]
    return {
      id: 'configs',
      label: 'Configurations',
      value: configs.length,
      headline: `${integer(metrics)} performance metrics`,
      footnote: target ? `on ${perfHardwareLabel(target)}` : undefined,
      action: target ? { label: 'Inspect model', onOpen: () => sheets.open(modelDetail(target)) } : undefined,
    }
  }
  const latest = recent.value.at(-1)
  return {
    id: 'models',
    label: 'AMD models',
    value: filterActive.value ? scoped.value.length : count(ready.value!.kpis.models),
    spark: spark((n) => n.models),
    delta: lastDelta(spark((n) => n.models), 'up', PRECEDING),
    headline: latest ? `${integer(latest.models)} in the latest nightly` : undefined,
    footnote: 'nightly model families',
    action: { label: 'Inspect model families', onOpen: () => perf.value && sheets.open(familiesDetail(perf.value, selectModel)) },
  }
})

const kpis = computed<Kpi[]>(() => (ready.value ? [scopeKpi.value, nightliesKpi.value, changeKpi('hard'), changeKpi('pass')] : []))
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
          {{ thresholds.accuracy_abs !== undefined ? `${(thresholds.accuracy_abs * 100).toFixed(1)} points` : 'the absolute band' }} for accuracy.
          The regression and improvement trends apply the same rule to every retained nightly.</p>
        <p>Only AMD Instinct (ROCm) records are shown. If any record names a non-MI device or no GPU, the page withholds all data rather than mixing hardware.</p>
        <p>The hardware filter keeps the configurations that ran on that GPU; accuracy tasks are not tied to a device.</p>
      </template>
      <template #actions>
        <Button
          v-if="latestBuild?.url"
          variant="outline"
          size="sm"
          as="a"
          :href="latestBuild.url"
          target="_blank"
          rel="noopener"
          class="font-normal text-muted-foreground"
          :title="latestBuild.date ? `Upstream activity: ${shortDate(latestBuild.date)}` : undefined"
          data-testid="perf-latest-build"
        >
          <History /> Latest perf build #{{ latestBuild.number }}<template v-if="latestBuild.date"> · {{ age(latestBuild.date, now) }}</template>
        </Button>
        <Button v-if="pipelineUrl" variant="outline" size="sm" as="a" :href="pipelineUrl" target="_blank" rel="noopener">
          Pipeline <ExternalLink />
        </Button>
      </template>
    </PageHeader>

    <SubNav :items="SUB_VIEWS.perf.items" :default-value="SUB_VIEWS.perf.default" label="Performance views" />

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

        <StatGrid :cols="4">
          <KpiCard
            v-for="kpi in kpis"
            :key="kpi.id"
            :label="kpi.label"
            :value="kpi.value"
            :spark="kpi.spark"
            :delta="kpi.delta"
            :headline="kpi.headline"
            :footnote="kpi.footnote"
            :action="kpi.action"
            :data-testid="`kpi-perf-${kpi.id}`"
          />
        </StatGrid>

        <EmptyState v-if="ready.kind === 'empty'" :icon="SearchX" title="No model matches the selected filters." data-testid="perf-empty">
          <template #action>
            <Button variant="outline" size="sm" @click="selectDevice('all')">Show all hardware</Button>
          </template>
        </EmptyState>
        <PerfAccuracy
          v-else-if="accuracyView"
          :rows="accuracyRows(scoped)"
          @history="(row) => openHistory(row.model, { label: 'Accuracy' }, row.task.metric, accuracyBlock(row.task))"
          @point="openPoint"
        />
        <PerfSummary
          v-else-if="ready.kind === 'summary'"
          :rows="modelRows"
          :changes="changes"
          :hardware="hardware"
          :hardware-label="ready.kpis.hardware"
          :device="device"
          :thresholds="thresholds"
          @select="selectModel"
          @device="selectDevice"
          @drill="sheets.open"
          @hardware="openHardware"
        />
        <template v-else-if="ready.kind === 'performance'">
          <PerfModel
            v-for="section in ready.sections"
            :key="section.model.model"
            :section="section"
            @model="(m) => sheets.open(modelDetail(m))"
            @history="openHistory"
            @point="openPoint"
          />
        </template>
      </template>
    </AsyncBoundary>

    <SheetStack v-slot="{ detail }" :sheets="sheets">
      <div v-if="isHistoryDetail(detail)" class="rounded-lg border p-3 sm:p-4">
        <PerfSeriesChart
          :series="detail.chart.series"
          :unit="detail.chart.context.block.unit"
          :label="detail.chart.label"
          :color="detail.chart.color"
          class="h-[240px]"
          @select="(point) => sheets.push(pointDetail(point, detail.chart.context, thresholds))"
        >
          <template #header>
            <span class="text-sm text-muted-foreground">{{ perfShortModel(detail.chart.context.model.model) }} · by nightly</span>
          </template>
        </PerfSeriesChart>
      </div>
    </SheetStack>
  </div>
</template>
