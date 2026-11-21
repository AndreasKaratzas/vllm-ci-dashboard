<script setup lang="ts">
import { ChartNoAxesColumn, ChevronRight, DatabaseZap, ListTree, Rows3 } from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import AreaChart from '@/components/charts/AreaChart.vue'
import Legend, { type LegendItem } from '@/components/charts/Legend.vue'
import ProportionBar, { type ProportionSegment } from '@/components/charts/ProportionBar.vue'
import type { Detail, DetailTrend } from '@/components/evidence/detail'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import DataTable from '@/components/table/DataTable.vue'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { integer, type Rec } from '@/domain/format'
import {
  OMNI_REPOSITORIES,
  omniKpiTrends,
  omniMappingBuckets,
  omniQueueSeries,
  type Kpi,
  type OmniDetail,
  type OmniView,
  type VolumePoint,
} from '@/domain/omni'
import { hardwareColor, hardwareFamily, SERIES } from '@/lib/palette'
import { PHONE_COLUMNS, collectionGap, countLabel, firstSentence, kpiValue, rowDetail, sentenceCase, toColumns, toDelta, toDetail, WITHHELD, WITHHELD_LABEL } from './present'

const props = defineProps<{ view: OmniView }>()
const emit = defineEmits<{ show: [Detail] }>()
const open = (detail: OmniDetail, trend?: DetailTrend) => emit('show', toDetail(detail, trend))

const mapping = computed(() => props.view.mappingView)
const windowLabel = computed(() => mapping.value.selected.label)
const buckets = computed(() => omniMappingBuckets(mapping.value))
const ratesAvailable = computed(() => mapping.value.available && Boolean(mapping.value.retainedComplete))
const trends = computed(() => omniKpiTrends(buckets.value, ratesAvailable.value))
const range = computed<[string, string] | undefined>(() => {
  const points = volume.value.available ? volume.value.points : []
  return points.length > 1 ? [points[0]!.label, points.at(-1)!.label] : undefined
})

const ACTIONS: Record<string, string> = {
  'omni-mapped-jobs': 'Mapping breakdown',
  'omni-gpu-demand': 'GPU demand by queue',
  'omni-mapped-share': 'Traffic share',
  'omni-retiring-share': 'MI325 exposure',
}
const series = (kpi: Kpi) => trends.value[kpi.id]?.series ?? undefined
// Work landing on retiring hardware is the one KPI state that asks for attention.
const status = (kpi: Kpi) => (kpi.id === 'omni-retiring-share' && kpi.tone === 'soft' ? { tone: kpi.tone, label: 'Exposed' } : undefined)
function openKpi(kpi: Kpi) {
  const values = series(kpi)
  open(
    kpi.open(),
    values
      ? {
          title: `${sentenceCase(kpi.label)} per bucket`,
          meta: windowLabel.value,
          values,
          range: range.value,
        }
      : undefined,
  )
}

const volume = computed(() => props.view.volume)
const closedPoints = computed(() => (volume.value.available ? volume.value.points.filter((p) => !p.current) : []))
const currentPoint = computed(() => (volume.value.available ? volume.value.points.find((p) => p.current) : undefined))
const VOLUME_SERIES = [{ key: 'value', label: 'Mapped jobs', color: SERIES.primary }]
const volumeGap = computed(() =>
  collectionGap(
    closedPoints.value.map((p) => p.start),
    1.5,
  ),
)

const impact = computed(() => props.view.impact)
const impactPreview = computed(() => (impact.value.available ? impact.value.preview : null))
const impactLegend = computed<LegendItem[]>(() => {
  const seen = new Map<string, LegendItem>()
  for (const bar of impact.value.available ? impact.value.bars : []) {
    const family = hardwareFamily(bar.label) ?? 'Other'
    seen.set(family, {
      key: family,
      label: bar.color === 'retiring' ? `${family} (retiring)` : family,
      color: hardwareColor(bar.label),
    })
  }
  return [...seen.values()]
})
function queueTrend(queue: string): DetailTrend | undefined {
  const values = omniQueueSeries(buckets.value, queue)
  return values.some((v) => v)
    ? {
        title: `${OMNI_REPOSITORIES.omni} mappings on this queue`,
        meta: windowLabel.value,
        values,
        range: range.value,
      }
    : undefined
}
const openQueueRow = (row: Rec) => {
  const detail = rowDetail(impactPreview.value!)?.(row)
  if (detail) emit('show', { ...detail, trend: queueTrend(String(row.name)) })
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
const openRepositoryRow = (row: Rec) => {
  const detail = rowDetail(comparison.value.table)?.(row)
  if (detail) emit('show', detail)
}
const openRepository = (segment: ProportionSegment) => openRepositoryRow(comparison.value.table.rows.find((row: Rec) => row.id === segment.key))
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
    <Callout v-if="!ratesAvailable" tone="soft" title="Rates and shares are withheld for this window" data-testid="omni-withheld">
      {{ mapping.reason ? firstSentence(mapping.reason) : 'Some buckets in this window were not fully collected.' }}
      Counts are lower bounds; a shorter window may be complete.
    </Callout>

    <StatGrid>
      <KpiCard
        v-for="(kpi, index) in view.kpis"
        :key="kpi.id"
        :label="sentenceCase(kpi.label)"
        :value="kpiValue(kpi.value)"
        :empty="kpi.value === WITHHELD ? WITHHELD_LABEL : undefined"
        :spark="series(kpi)"
        :delta="toDelta(trends[kpi.id]?.change ?? null)"
        :status="status(kpi)"
        badges="inline"
        :footnote="kpi.meta"
        :action="{ label: ACTIONS[kpi.id]!, onOpen: () => openKpi(kpi) }"
        :style="{ animationDelay: `${index * 30}ms` }"
        :data-testid="`omni-kpi-${kpi.id}`"
        :data-tone="kpi.tone"
      />
    </StatGrid>

    <Card class="min-w-0 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1" data-testid="omni-volume">
      <CardHeader>
        <CardTitle>{{ volume.title }}</CardTitle>
        <CardDescription>{{ volume.meta }}</CardDescription>
        <CardAction>
          <Button variant="outline" size="sm" aria-label="Time buckets" @click="open(comparison.browseBuckets())"
            ><Rows3 /> <span class="hidden sm:inline">Time buckets</span></Button
          >
        </CardAction>
      </CardHeader>
      <CardContent class="flex flex-col gap-3 px-2 sm:px-6">
        <template v-if="volume.available">
          <AreaChart
            v-if="closedPoints.length > 1"
            :rows="closedPoints"
            :x="(p: VolumePoint) => p.start"
            :series="VOLUME_SERIES"
            :label="`Observed ${OMNI_REPOSITORIES.omni} mappings per bucket, ${windowLabel}`"
            :row-label="(p: VolumePoint) => p.label"
            :y-format="countLabel"
            time-axis
            :max-gap="volumeGap"
            inspect-placement="none"
            hint="Click for bucket evidence"
            :on-select="(p: VolumePoint) => open(p.open())"
          />
          <EmptyState
            v-else
            :icon="ChartNoAxesColumn"
            title="Not enough closed buckets to draw a trend"
            :description="`Pick a longer window than ${windowLabel.toLowerCase()}.`"
          />
          <button
            v-if="currentPoint"
            type="button"
            class="group/current flex items-center justify-between gap-3 rounded-lg border border-dashed px-3 py-2 text-left text-sm transition-colors hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none sm:mx-0"
            data-testid="omni-volume-current"
            @click="open(currentPoint.open())"
          >
            <span class="min-w-0">
              <span class="font-medium tabular-nums">{{ integer(currentPoint.value) }}</span>
              <span class="text-muted-foreground"> mapped so far in the open bucket {{ currentPoint.label }}, not drawn until it closes</span>
            </span>
            <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-hover/current:translate-x-0.5" aria-hidden="true" />
          </button>
        </template>
        <EmptyState v-else :icon="ChartNoAxesColumn" title="No incoming workload in this window" :description="volume.empty" data-testid="omni-volume-empty" />
      </CardContent>
    </Card>

    <div class="grid grid-cols-1 items-start gap-4 md:gap-6 @7xl/main:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] *:min-w-0">
      <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1" data-testid="omni-impact">
        <CardHeader>
          <CardTitle>{{ impact.title }}</CardTitle>
          <CardDescription>{{ impact.meta }}</CardDescription>
          <CardAction v-if="comparison.browseQueues">
            <Button variant="outline" size="sm" @click="open(comparison.browseQueues!())">All queues</Button>
          </CardAction>
        </CardHeader>
        <CardContent v-if="impactPreview" class="flex flex-col gap-3">
          <DataTable
            :columns="
              toColumns(impactPreview, {
                compact: true,
                rowClick: true,
                mono: ['Queue'],
                bar: {
                  label: 'Mapped',
                  value: (row: Rec) => Number(row.omni.mapped_jobs || 0),
                  color: (row: Rec) => hardwareColor(row.name),
                },
              })
            "
            :rows="impactPreview.rows"
            :searchable="false"
            :page-size="false"
            label="Top queues by Omni mappings"
            :on-row-click="openQueueRow"
            data-testid="omni-impact-preview"
            @drill="(detail) => emit('show', detail)"
          />
          <Legend :items="impactLegend" />
        </CardContent>
        <CardContent v-else>
          <EmptyState title="No Omni mappings on monitored queues" :description="impact.available ? undefined : impact.empty" data-testid="omni-impact-empty" />
        </CardContent>
      </Card>

      <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1" data-testid="omni-comparison">
        <CardHeader>
          <CardTitle>{{ comparison.title }}</CardTitle>
          <CardDescription>{{ comparison.meta }}</CardDescription>
        </CardHeader>
        <CardContent class="flex flex-col gap-4">
          <ProportionBar :segments="shares" label="Observed mappings by repository" :value-format="(v: number) => integer(v)" :on-select="openRepository" />
          <DataTable
            :columns="
              toColumns(comparison.table, {
                compact: true,
                rowClick: true,
                minWidth: { ...PHONE_COLUMNS, 'Start rate': 'sm' },
              })
            "
            :rows="comparison.table.rows"
            :searchable="false"
            :page-size="false"
            label="Repository comparison"
            :on-row-click="openRepositoryRow"
            @drill="(detail) => emit('show', detail)"
          />
        </CardContent>
      </Card>
    </div>
  </div>
</template>
