<script setup lang="ts">
import { ChartNoAxesColumn, DatabaseZap, ListTree, Rows3 } from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import AreaChart from '@/components/charts/AreaChart.vue'
import BarChart from '@/components/charts/BarChart.vue'
import Legend, { type LegendItem } from '@/components/charts/Legend.vue'
import ProportionBar, { type ProportionSegment } from '@/components/charts/ProportionBar.vue'
import type { Detail } from '@/components/evidence/detail'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import InfoPopover from '@/components/feedback/InfoPopover.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import type { Delta } from '@/components/stat/DeltaBadge.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import DataTable from '@/components/table/DataTable.vue'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { integer, type Rec, type Tone } from '@/domain/format'
import { OMNI_REPOSITORIES, omniMappingBucketLabel, omniMappingBuckets, type MappingBucket, type OmniDetail, type OmniView } from '@/domain/omni'
import { hardwareColor, hardwareFamily, SERIES } from '@/lib/palette'
import { countLabel, firstSentence, kpiValue, sentenceCase, timeAxisFormat, toColumns, toDetail } from './present'

const props = defineProps<{ view: OmniView }>()
const emit = defineEmits<{ show: [Detail] }>()
const open = (detail: OmniDetail) => emit('show', toDetail(detail))

const mapping = computed(() => props.view.mappingView)
const windowLabel = computed(() => mapping.value.selected.label)
const buckets = computed(() => omniMappingBuckets(mapping.value))
// The domain withholds rates, shares, and deltas unless the selected coverage is complete.
const ratesAvailable = computed(() => mapping.value.available && Boolean(mapping.value.retainedComplete))

type BucketMeasure = (bucket: MappingBucket) => number
const omniMapped: BucketMeasure = (b) => Number(b.workloads.omni.mapped_jobs || 0)
const omniSlots: BucketMeasure = (b) => Number(b.workloads.omni.mapped_gpu_slots || 0)
const omniShare: BucketMeasure = (b) => {
  const total = omniMapped(b) + Number(b.workloads.main.mapped_jobs || 0)
  return total ? (omniMapped(b) / total) * 100 : 0
}
const omniRetiring: BucketMeasure = (b) =>
  Object.entries(b.workloads.omni.by_queue).reduce((sum, [name, stats]) => sum + (name.startsWith('amd_mi325_') ? Number(stats.mapped_jobs || 0) : 0), 0)

// Each KPI trends one per-bucket measure; its delta compares the newer half of the closed
// buckets with the older half, so a single noisy bucket does not swing it.
const shareOf = (part: number, whole: number) => (whole ? (part / whole) * 100 : 0)
const sum = (rows: MappingBucket[], measure: BucketMeasure) => rows.reduce((total, b) => total + measure(b), 0)
const omniMain: BucketMeasure = (b) => omniMapped(b) + Number(b.workloads.main.mapped_jobs || 0)
const relative = (newer: number, older: number): Delta | undefined =>
  older ? { value: Math.round(shareOf(newer - older, older) * 10) / 10, text: `${newer >= older ? '+' : ''}${shareOf(newer - older, older).toFixed(0)}%` } : undefined
const points = (newer: number, older: number): Delta => {
  const value = Math.round((newer - older) * 10) / 10
  return { value, text: `${value > 0 ? '+' : ''}${value.toFixed(1)} pp` }
}
interface KpiTrend {
  measure: BucketMeasure
  delta: (newer: MappingBucket[], older: MappingBucket[]) => Delta | undefined
}
const TRENDS: Record<string, KpiTrend> = {
  'omni-mapped-jobs': { measure: omniMapped, delta: (n, o) => relative(sum(n, omniMapped), sum(o, omniMapped)) },
  'omni-gpu-demand': { measure: omniSlots, delta: (n, o) => relative(sum(n, omniSlots), sum(o, omniSlots)) },
  'omni-mapped-share': {
    measure: omniShare,
    delta: (n, o) => points(shareOf(sum(n, omniMapped), sum(n, omniMain)), shareOf(sum(o, omniMapped), sum(o, omniMain))),
  },
  'omni-retiring-share': {
    measure: omniRetiring,
    delta: (n, o) =>
      sum(n, omniRetiring) || sum(o, omniRetiring)
        ? points(shareOf(sum(n, omniRetiring), sum(n, omniMapped)), shareOf(sum(o, omniRetiring), sum(o, omniMapped)))
        : undefined,
  },
}
const ACTIONS: Record<string, string> = {
  'omni-mapped-jobs': 'Mapping breakdown',
  'omni-gpu-demand': 'GPU demand by queue',
  'omni-mapped-share': 'Traffic share',
  'omni-retiring-share': 'MI325 exposure',
}

function spark(id: string): number[] | undefined {
  const values = buckets.value.map(TRENDS[id]!.measure)
  return values.length > 1 && values.some((v) => v !== 0) ? values : undefined
}
/** Only when coverage supports rates, as the domain decides for shares and deltas. */
function delta(id: string): Delta | undefined {
  if (!ratesAvailable.value) return undefined
  const closed = buckets.value.filter((b) => b.complete)
  if (closed.length < 2) return undefined
  const half = Math.floor(closed.length / 2)
  return TRENDS[id]!.delta(closed.slice(-half), closed.slice(0, half))
}
// Work landing on retiring hardware is the one KPI state that asks for attention.
const status = (id: string, tone: Tone | '') => (id === 'omni-retiring-share' && tone === 'soft' ? { tone, label: 'Exposed' } : undefined)

const note = computed(() => props.view.scopeNote)

const volume = computed(() => props.view.volume)
const volumeRows = computed(() =>
  volume.value.available
    ? buckets.value.map((bucket, index) => ({ start: bucket.start, mapped: omniMapped(bucket), open: volume.value.available ? volume.value.bars[index]!.open : null }))
    : [],
)
type VolumeRow = (typeof volumeRows.value)[number]
const VOLUME_SERIES = [{ key: 'mapped', label: 'Mapped jobs', color: SERIES.primary }]
const volumeAxis = computed(() => timeAxisFormat(mapping.value.resolution === 'hourly' ? mapping.value.selected.hours : Infinity))
const bucketTitle = (row: VolumeRow) => `${omniMappingBucketLabel(row, mapping.value.resolution)} UTC`

const impact = computed(() => props.view.impact)
const impactRows = computed(() => (impact.value.available ? impact.value.bars.map((bar) => ({ ...bar })) : []))
const impactPreview = computed(() => (impact.value.available ? impact.value.preview : null))
const IMPACT_SERIES = [{ key: 'value', label: 'Mapped jobs', color: SERIES.primary }]
const impactLegend = computed<LegendItem[]>(() => {
  const seen = new Map<string, LegendItem>()
  for (const bar of impactRows.value) {
    const family = hardwareFamily(bar.label) ?? 'Other'
    const retiring = bar.color === 'retiring'
    seen.set(family, { key: family, label: retiring ? `${family} (retiring)` : family, color: hardwareColor(bar.label) })
  }
  return [...seen.values()]
})
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const openFirstCell = (table: { columns: { cell: (row: any) => { open?: () => OmniDetail } }[] }, row: unknown) => {
  const action = table.columns[0]!.cell(row).open
  if (action) open(action())
}

const comparison = computed(() => props.view.comparison)
const shares = computed<ProportionSegment[]>(() =>
  comparison.value.table.rows.map((row: Rec, index: number) => ({
    key: row.id,
    label: row.repository,
    count: Number(row.stats.mapped_jobs || 0),
    color: index ? SERIES.secondary : SERIES.primary,
  })),
)
const openRepository = (segment: ProportionSegment) =>
  openFirstCell(
    comparison.value.table,
    comparison.value.table.rows.find((row: Rec) => row.id === segment.key),
  )
</script>

<template>
  <div v-if="!mapping.available" class="flex flex-col gap-4 md:gap-6">
    <EmptyState
      :icon="DatabaseZap"
      :title="`No mapping history for ${windowLabel.toLowerCase()}`"
      :description="mapping.reason"
      :cause="`${integer(mapping.rows.length)} retained mapping buckets`"
      class="bg-card motion-safe:animate-in motion-safe:fade-in-0"
      data-testid="omni-mapping-empty"
    >
      <template #action>
        <div class="flex flex-wrap justify-center gap-2">
          <Button variant="outline" size="sm" @click="open(view.methodology())"><ListTree /> Scope &amp; coverage</Button>
          <Button variant="ghost" size="sm" as-child>
            <RouterLink :to="{ query: { ...$route.query, view: 'live' } }">See live queues</RouterLink>
          </Button>
        </div>
      </template>
    </EmptyState>
  </div>

  <div v-else class="flex flex-col gap-4 md:gap-6" data-testid="omni-workload">
    <StatGrid>
      <KpiCard
        v-for="(kpi, index) in view.kpis"
        :key="kpi.id"
        :label="sentenceCase(kpi.label)"
        :value="kpiValue(kpi.value)"
        :spark="spark(kpi.id)"
        :delta="delta(kpi.id)"
        :status="status(kpi.id, kpi.tone)"
        :footnote="kpi.meta"
        :action="{ label: ACTIONS[kpi.id]!, onOpen: () => open(kpi.open()) }"
        :style="{ animationDelay: `${index * 30}ms` }"
        :data-testid="`omni-kpi-${kpi.id}`"
        :data-tone="kpi.tone"
      />
    </StatGrid>

    <Callout v-if="note.tone !== 'info'" :tone="note.tone" :title="note.heading.trim()" data-testid="omni-scope-note">
      {{ firstSentence(note.text) }}
      <template #action>
        <InfoPopover title="Coverage details" label="Coverage details">
          <p data-testid="omni-scope-text">{{ note.text }}</p>
          <div class="flex flex-wrap gap-2 pt-1">
            <Button variant="outline" size="sm" class="text-foreground" @click="open(note.methodology())">Inspect methodology</Button>
            <Button v-if="note.excluded" variant="outline" size="sm" class="text-foreground" @click="open(note.excluded!())">Inspect excluded stale jobs</Button>
          </div>
        </InfoPopover>
      </template>
    </Callout>

    <div class="grid grid-cols-1 items-start gap-4 md:gap-6 @5xl/main:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] *:min-w-0">
      <Card class="min-w-0 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1" data-testid="omni-volume">
        <CardHeader>
          <CardTitle>{{ volume.title }}</CardTitle>
          <CardDescription>{{ volume.meta }}</CardDescription>
          <CardAction>
            <Button variant="outline" size="sm" aria-label="Time buckets" @click="open(comparison.browseBuckets())"><Rows3 /> <span class="hidden sm:inline">Time buckets</span></Button>
          </CardAction>
        </CardHeader>
        <CardContent class="px-2 sm:px-6">
          <AreaChart
            v-if="volume.available"
            :rows="volumeRows"
            :x="(row: VolumeRow) => row.start"
            :series="VOLUME_SERIES"
            :label="`Observed ${OMNI_REPOSITORIES.omni} mappings per bucket, ${windowLabel}`"
            :row-label="bucketTitle"
            :x-format="volumeAxis"
            :y-format="countLabel"
            hint="Click for bucket evidence"
            :on-select="(row: VolumeRow) => row.open && open(row.open())"
          />
          <EmptyState v-else :icon="ChartNoAxesColumn" title="No incoming workload in this window" :description="volume.empty" data-testid="omni-volume-empty" />
        </CardContent>
      </Card>

      <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1" data-testid="omni-impact">
        <CardHeader>
          <CardTitle>{{ impact.title }}</CardTitle>
          <CardDescription>{{ impact.meta }}</CardDescription>
          <CardAction v-if="comparison.browseQueues">
            <Button variant="outline" size="sm" @click="open(comparison.browseQueues!())">All queues</Button>
          </CardAction>
        </CardHeader>
        <CardContent v-if="impact.available" class="flex flex-col gap-4">
          <BarChart
            :rows="impactRows"
            :category="(row) => row.label"
            :series="IMPACT_SERIES"
            orientation="horizontal"
            :bar-color="(row) => hardwareColor(row.label)"
            :label="`Observed Omni mappings by AMD queue, ${windowLabel}`"
            hint="Click for queue impact"
            :value-format="countLabel"
            :on-select="(row) => open(row.open())"
          />
          <Legend :items="impactLegend" />
          <DataTable
            v-if="impactPreview"
            :columns="toColumns(impactPreview, true)"
            :rows="impactPreview.rows"
            :searchable="false"
            :page-size="false"
            label="Top queues by Omni mappings"
            :on-row-click="(row) => openFirstCell(impactPreview!, row)"
            data-testid="omni-impact-preview"
            @drill="(detail) => emit('show', detail)"
          />
        </CardContent>
        <CardContent v-else>
          <EmptyState title="No Omni mappings on monitored queues" :description="impact.empty" data-testid="omni-impact-empty" />
        </CardContent>
      </Card>
    </div>

    <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1" data-testid="omni-comparison">
      <CardHeader>
        <CardTitle>{{ comparison.title }}</CardTitle>
        <CardDescription>{{ comparison.meta }}</CardDescription>
      </CardHeader>
      <CardContent class="flex flex-col gap-4">
        <ProportionBar :segments="shares" label="Observed mappings by repository" :value-format="(v: number) => integer(v)" :on-select="openRepository" />
        <DataTable
          :columns="toColumns(comparison.table, true)"
          :rows="comparison.table.rows"
          :searchable="false"
          :page-size="false"
          label="Repository comparison"
          :on-row-click="(row) => openFirstCell(comparison.table, row)"
          @drill="(detail) => emit('show', detail)"
        />
      </CardContent>
    </Card>
  </div>
</template>
