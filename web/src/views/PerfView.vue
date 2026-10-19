<script setup lang="ts">
import type { ColumnDef } from '@tanstack/vue-table'
import { ArrowLeft } from '@lucide/vue'
import { computed, h, ref, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import KpiCard from '@/components/ops/KpiCard.vue'
import TableSheet from '@/components/ops/TableSheet.vue'
import ViewState from '@/components/ops/ViewState.vue'
import PerfDetailSheet from '@/components/perf/PerfDetailSheet.vue'
import PerfHistorySheet, { type PerfHistoryTarget } from '@/components/perf/PerfHistorySheet.vue'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { useOperations } from '@/composables/useOperations'
import { clearDataCache, fetchJSON } from '@/data/fetch'
import { age, integer, shortDate, type Rec } from '@/domain/format'
import {
  PERF_SOURCE,
  accuracyBlock,
  perfMetricDetail,
  perfModelDetail,
  perfModelFamilies,
  perfModelRows,
  perfModels,
  perfPage,
  perfPipelineUrl,
  perfQuery,
  perfState,
  type PerfDetail,
  type PerfState,
  type PerfView as PerfViewId,
} from '@/domain/perf'
import PerfAccuracy from './perf/PerfAccuracy.vue'
import PerfModelOverview from './perf/PerfModelOverview.vue'
import PerfModelSections from './perf/PerfModelSections.vue'

const route = useRoute()
const router = useRouter()
const { ops } = useOperations()

const state = computed(() => perfState(route.query))
function setState(next: Partial<PerfState>) {
  void router.replace({ query: perfQuery({ ...state.value, ...next }) })
}
function selectModel(model: string, view?: PerfViewId) {
  setState(view ? { model, view } : { model })
}

const perf = shallowRef<Rec | null>(null)
const error = ref<string | null>(null)
const loading = ref(true)
async function load(force = false) {
  if (force) clearDataCache()
  loading.value = true
  error.value = null
  try {
    perf.value = await fetchJSON<Rec>(PERF_SOURCE)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}
void load()

const page = computed(() => (perf.value ? perfPage(perf.value, state.value) : null))
const observedAt = computed(() => perf.value?.generated_at || ops.value?.generated_at)
const pipelineUrl = computed(() => (perf.value ? perfPipelineUrl(perf.value) : ''))
const totals = computed(() => {
  const rows = perfModelRows(perfModels(perf.value))
  return { regressions: rows.reduce((sum, row) => sum + row.regressions, 0), improvements: rows.reduce((sum, row) => sum + row.improvements, 0) }
})

const viewTab = computed<PerfViewId>({ get: () => state.value.view, set: (view) => setState({ view }) })
const modelSelect = computed<string>({ get: () => state.value.model, set: (model) => selectModel(String(model || 'all')) })
const deviceToggle = computed<string>({
  get: () => state.value.device,
  // A single toggle group clears its value when the active item is pressed again; classic keeps it.
  set: (device) => device && setState({ device: String(device) }),
})

// Drill-downs
const detail = ref<{ open: boolean; detail: PerfDetail | null }>({ open: false, detail: null })
function openDetail(next: PerfDetail) {
  detail.value = { open: true, detail: next }
}
const history = ref<{ open: boolean; target: PerfHistoryTarget | null }>({ open: false, target: null })
function openHistory(model: Rec, config: Rec, metricName: string, block: Rec) {
  history.value = { open: true, target: { model, config, metricName, block } }
}
function openAccuracy(row: { model: Rec; task: Rec }) {
  openHistory(row.model, { label: 'Accuracy' }, row.task.metric, accuracyBlock(row.task))
}

type Family = ReturnType<typeof perfModelFamilies>[number]
const families = ref({ open: false, rows: [] as Family[] })
const familyColumns: ColumnDef<Family, unknown>[] = [
  {
    id: 'label',
    header: 'Observation',
    accessorFn: (row) => row.label,
    cell: ({ row }) =>
      row.original.url
        ? h('a', { href: row.original.url, target: '_blank', rel: 'noopener', class: 'font-mono font-medium hover:underline' }, row.original.label)
        : h('span', { class: 'font-mono' }, row.original.label),
    enableHiding: false,
  },
  { id: 'observed', header: 'Observed', accessorFn: () => '-' },
  { id: 'value', header: 'Value', accessorFn: (row) => row.valueSummary },
  {
    id: 'evidence',
    header: 'Evidence',
    accessorFn: (row) => row.url || '',
    cell: ({ row }) =>
      row.original.url
        ? h('a', { href: row.original.url, target: '_blank', rel: 'noopener', class: 'hover:underline' }, 'Open source ↗')
        : h('span', { class: 'text-muted-foreground' }, 'Open published source data'),
    enableSorting: false,
  },
]
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 class="text-2xl font-semibold tracking-tight">Performance &amp; Evaluation</h2>
        <p class="max-w-3xl text-sm text-muted-foreground">
          Artifact-backed AMD nightly throughput, latency, and accuracy with build and commit provenance.
        </p>
      </div>
      <div v-if="page && page.kind !== 'unavailable'" class="flex items-center gap-3">
        <span v-if="observedAt" class="text-xs text-muted-foreground" :title="observedAt" data-testid="perf-observed">
          Observed {{ shortDate(observedAt) }} · {{ age(observedAt) }}
        </span>
        <Button variant="outline" size="sm" as="a" :href="pipelineUrl" target="_blank" rel="noopener">Open perf-eval pipeline ↗</Button>
      </div>
    </div>

    <ViewState :loading="loading && !perf" :error="error" @retry="load(true)">
      <p
        v-if="page?.kind === 'unavailable'"
        class="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning"
        data-testid="perf-unavailable"
      >
        AMD MI performance data is unavailable. Current MI GPU metric records are required.
      </p>
      <template v-else-if="page">
        <div class="grid grid-cols-1 gap-4 @xl/main:grid-cols-2 @5xl/main:grid-cols-4">
          <KpiCard
            data-testid="kpi-perf-models"
            label="AMD models"
            :value="page.kpis.models"
            :badge="`${integer(totals.regressions)} regressed`"
            :badge-tone="totals.regressions ? 'is-danger' : 'is-success'"
            headline="Inspect model families"
            detail="nightly model families"
            @open="families = { open: true, rows: perfModelFamilies(perf!) }"
          />
          <KpiCard
            data-testid="kpi-perf-nightlies"
            label="Nightlies tracked"
            :value="page.kpis.nightlies"
            :badge="`${integer(totals.improvements)} improved`"
            :badge-tone="totals.improvements ? 'is-success' : 'is-neutral'"
            headline="Open perf-eval pipeline"
            :href="pipelineUrl"
            detail="across retained series"
          />
          <KpiCard
            data-testid="kpi-perf-points"
            label="Metric points"
            :value="page.kpis.points"
            badge="retained"
            badge-tone="is-info"
            headline="Inspect retained points"
            :detail="page.kpis.pointsMeta"
            @open="
              openDetail(
                perfMetricDetail({
                  label: 'Retained metric points',
                  value: page.kpis.pointsRaw,
                  meta: 'Performance and accuracy points are inspectable from their model histories.',
                  provenance: 'perf_eval.json',
                }),
              )
            "
          />
          <KpiCard
            data-testid="kpi-perf-hardware"
            label="AMD hardware"
            :value="page.kpis.hardware"
            badge="ROCm"
            badge-tone="is-info"
            headline="Inspect hardware"
            detail="ROCm only"
            @open="
              openDetail(
                perfMetricDetail({
                  label: 'AMD performance hardware',
                  value: page.kpis.hardwareDetail,
                  meta: 'Hardware declared by retained AMD workload records.',
                }),
              )
            "
          />
        </div>

        <Card class="py-3">
          <CardContent class="flex flex-wrap items-center gap-3 px-3">
            <Button v-if="page.showBack" variant="outline" size="sm" aria-label="Back to all performance models" @click="selectModel('all')">
              <ArrowLeft /> All models
            </Button>
            <Tabs v-model="viewTab">
              <TabsList aria-label="Performance and accuracy view">
                <TabsTrigger value="performance">Performance</TabsTrigger>
                <TabsTrigger value="accuracy">Accuracy</TabsTrigger>
              </TabsList>
            </Tabs>
            <div class="ml-auto flex flex-wrap items-center gap-3">
              <label class="flex items-center gap-2 text-sm text-muted-foreground">
                Model
                <Select v-model="modelSelect">
                  <SelectTrigger size="sm" class="w-72" data-testid="perf-model-select" aria-label="Model">
                    <SelectValue placeholder="Unknown model" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All models</SelectItem>
                    <SelectItem v-for="name in page.modelNames" :key="name" :value="name">{{ name }}</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <div v-if="page.devices.length" class="flex items-center gap-2 text-sm text-muted-foreground">
                Hardware
                <ToggleGroup v-model="deviceToggle" type="single" variant="outline" size="sm" aria-label="Performance hardware filter">
                  <ToggleGroupItem value="all">All</ToggleGroupItem>
                  <ToggleGroupItem v-for="device in page.devices" :key="device.id" :value="device.id">{{ device.label }}</ToggleGroupItem>
                </ToggleGroup>
              </div>
            </div>
          </CardContent>
        </Card>

        <p v-if="page.kind === 'empty'" class="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground" data-testid="perf-empty">
          No model matches the selected filters.
        </p>
        <template v-else-if="page.kind === 'summary'">
          <p class="rounded-md border border-info/40 bg-info/10 p-3 text-sm">
            <strong>Model summary.</strong> Choose one model to render its detailed metric histories; the dashboard does not create every chart at once.
          </p>
          <PerfModelOverview :rows="page.rows" @select="selectModel" />
        </template>
        <PerfAccuracy v-else-if="page.kind === 'accuracy'" :rows="page.rows" @history="openAccuracy" />
        <PerfModelSections
          v-else-if="page.kind === 'performance'"
          :sections="page.sections"
          @model="(model) => openDetail(perfModelDetail(model))"
          @history="openHistory"
        />
      </template>
    </ViewState>

    <TableSheet
      v-model:open="families.open"
      title="AMD model families"
      :subtitle="`Select an observation to inspect its exact evidence · ${integer(families.rows.length)} source-backed observations`"
      :columns="familyColumns"
      :rows="families.rows"
      search-placeholder="Filter model"
    />
    <PerfDetailSheet v-model:open="detail.open" :detail="detail.detail" />
    <PerfHistorySheet v-model:open="history.open" :target="history.target" />
  </div>
</template>
