<script setup lang="ts">
import { Container, ExternalLink } from '@lucide/vue'
import { computed, h } from 'vue'
import DockerAbout from '@/components/analytics/docker/DockerAbout.vue'
import DockerRebuildChart from '@/components/analytics/docker/DockerRebuildChart.vue'
import { jobColumns, jobDetail, jobFilters, jobSearch } from '@/components/analytics/docker/details'
import AreaChart from '@/components/charts/AreaChart.vue'
import { shortDay, type SeriesSpec } from '@/components/charts/chart'
import SheetStack from '@/components/evidence/SheetStack.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import SegmentedSelect from '@/components/layout/SegmentedSelect.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import DataTable from '@/components/table/DataTable.vue'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useClock } from '@/composables/useClock'
import { useDataset } from '@/composables/useDataset'
import { useQueryState } from '@/composables/useQueryState'
import { useSheetStack } from '@/composables/useSheetStack'
import { useSubviewHeader } from '@/composables/useSubviewHeader'
import { DATASETS } from '@/data/sources'
import {
  BASE_STEP,
  DOCKER_PIPELINES,
  DOCKER_SCOPES,
  DOCKER_WINDOWS,
  DOCKER_WINDOW_IDS,
  IMAGE_STEP,
  baseRebuilds,
  decodeJobs,
  dockerCollected,
  dockerDaily,
  dockerFreshness,
  dockerRange,
  filterJobs,
  stepStats,
  type DockerJob,
  type DockerPipeline,
  type DockerScope,
  type DockerWindow,
} from '@/domain/dockerBuilds'
import { age, counted, integer, seconds, shortDate } from '@/domain/format'
import { SERIES } from '@/lib/palette'

const docker = useDataset('docker_builds')
const payload = docker.data
const now = useClock()
const sheets = useSheetStack()
const [LIVE_ASSET, PAGES_ASSET] = DATASETS.docker_builds.paths

const windowId = useQueryState<DockerWindow>('docker_window', { default: '14d', allowed: DOCKER_WINDOW_IDS })
const scope = useQueryState<DockerScope>('docker_scope', { default: 'all', allowed: DOCKER_SCOPES })
const pipeline = useQueryState<DockerPipeline>('docker_pipeline', { default: 'ci', allowed: DOCKER_PIPELINES })

const WINDOWS = DOCKER_WINDOWS.map((w) => ({ value: w.id, label: w.id, title: w.label }))
const SCOPES = [
  { value: 'all', label: 'All builds' },
  { value: 'main', label: 'main' },
  { value: 'pr', label: 'PRs' },
]
const PIPELINES = [
  { value: 'ci', label: 'vllm/ci' },
  { value: 'amd-ci', label: 'vllm/amd-ci' },
  { value: 'all', label: 'Both' },
]

const collected = computed(() => !!payload.value && dockerCollected(payload.value))
const freshness = computed(() => (payload.value ? dockerFreshness(payload.value, now.value) : null))
const range = computed(() => (payload.value ? dockerRange(payload.value, windowId.value) : null))
const jobs = computed<DockerJob[]>(() =>
  payload.value && range.value ? filterJobs(decodeJobs(payload.value), range.value, { scope: scope.value, pipeline: pipeline.value }) : [],
)
const image = computed(() => stepStats(jobs.value, IMAGE_STEP))
const base = computed(() => stepStats(jobs.value, BASE_STEP))
const rebuilds = computed(() => baseRebuilds(jobs.value))

useSubviewHeader(() => {
  const data = payload.value
  const fresh = freshness.value
  if (!data || !fresh) return {}
  return {
    freshness: !collected.value
      ? { text: 'Not collected yet', tone: 'neutral', detail: 'The docker build collector has not published its first complete backfill.' }
      : {
      text: `Collected ${age(data.generated_at, now.value)}`,
      tone: fresh.stale ? 'hard' : 'pass',
      detail: `Docker build jobs were generated ${shortDate(data.generated_at)}. They are stale after 6 hours.`,
    },
    about: () => h(DockerAbout, { payload: data }),
  }
})

// ---- Daily run time of the test image; days without a finished run are a gap, not zero ----
const DAY_MS = 86_400_000
interface DayRow {
  start: number
  runs: number
  p50: number
  p90: number
  [key: string]: unknown
}
const daily = computed(() => (range.value ? dockerDaily(jobs.value, range.value, IMAGE_STEP) : []))
const dayRows = computed<DayRow[]>(() =>
  daily.value.flatMap((day) => (day.p50 === null || day.p90 === null ? [] : [{ start: day.start, runs: day.runs, p50: day.p50 / 60, p90: day.p90 / 60 }])),
)
const DAILY_SERIES: SeriesSpec[] = [
  { key: 'p90', label: 'p90 run time', color: SERIES.secondary },
  { key: 'p50', label: 'Median run time', color: SERIES.primary },
]
const minutesLabel = (minutes: number) => seconds(minutes * 60)
const MINUTE_STEPS = [5, 10, 15, 30, 60, 120, 240]
const dailyTicks = computed(() => {
  const peak = Math.max(1, ...dayRows.value.map((row) => row.p90))
  const step = MINUTE_STEPS.find((s) => peak / s <= 4) ?? 480
  return Array.from({ length: Math.ceil(peak / step) + 1 }, (_, i) => i * step)
})
const dayLabel = (row: DayRow) => `${shortDay(row.start)} · ${counted(row.runs, 'run')}`

const share = (value: number | null) => (value === null ? null : `${(value * 100).toFixed(value > 0 && value < 0.01 ? 1 : 0)}%`)
const runsFoot = (stats: typeof image.value) =>
  [stats.retried && `${integer(stats.retried)} retried`, stats.failed && `${integer(stats.failed)} failed`, stats.jobs - stats.runs - stats.retried > 0 && `${integer(stats.jobs - stats.runs - stats.retried)} unfinished or canceled`]
    .filter(Boolean)
    .join(' · ') || 'Every job finished on its first attempt'
const imageSpark = computed(() => dayRows.value.map((row) => row.p50))

const tableColumns = jobColumns()
const tableFilters = computed(() => jobFilters(jobs.value))
const windowLabel = computed(() => DOCKER_WINDOWS.find((w) => w.id === windowId.value)!.label.toLowerCase())
const sliceLabel = computed(
  () => `${scope.value === 'all' ? 'main and PR builds' : scope.value === 'main' ? 'main builds' : 'PR builds'} of ${pipeline.value === 'all' ? 'vllm/ci and vllm/amd-ci' : `vllm/${pipeline.value}`}`,
)
const open = (job: DockerJob) => sheets.open(jobDetail(job))

const ENTER = 'motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300'
</script>

<template>
  <div class="flex min-w-0 flex-col gap-4 md:gap-6" data-testid="analytics-docker">
    <AsyncBoundary :loading="docker.loading.value && !payload" :error="payload ? null : docker.error.value" :refreshing="docker.refreshing.value && !!payload" @retry="docker.refresh">
      <template #error>
        <div class="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" as="a" :href="LIVE_ASSET" target="_blank" rel="noopener">Live docker build data <ExternalLink /></Button>
          <Button variant="outline" size="sm" as="a" :href="PAGES_ASSET" target="_blank" rel="noopener">Pages fallback <ExternalLink /></Button>
        </div>
      </template>

      <template v-if="payload && range && freshness">
        <EmptyState
          v-if="!collected"
          title="Docker build jobs have not been collected yet."
          description="The collector publishes the ROCm docker build jobs once its first 30-day backfill completes."
          :cause="`Coverage ${payload.coverage?.status ?? 'unknown'}`"
          :icon="Container"
          data-testid="docker-not-collected"
        />
        <template v-else>
          <div class="grid grid-cols-2 items-end gap-x-4 gap-y-3 sm:flex sm:flex-wrap" data-testid="docker-controls">
            <SegmentedSelect v-model="windowId" label="Window" :items="WINDOWS" />
            <SegmentedSelect v-model="scope" label="Builds" :items="SCOPES" />
            <SegmentedSelect v-model="pipeline" label="Pipeline" :items="PIPELINES" />
          </div>

          <Callout v-if="freshness.stale" tone="soft" title="Docker build jobs are stale." data-testid="docker-stale">
            <p>Collected {{ age(payload.generated_at, now) }}, at {{ shortDate(payload.generated_at) }}. Jobs that started since then are missing.</p>
          </Callout>

          <EmptyState
            v-if="!jobs.length"
            title="No docker build jobs in this window."
            :description="`No ${sliceLabel} were created in the ${windowLabel}.`"
            :icon="Container"
            data-testid="docker-empty"
          />
          <template v-else>
            <StatGrid :cols="3" data-testid="docker-kpis">
              <KpiCard
                label="Test image · median run"
                :value="seconds(image.p50)"
                :footnote="`${counted(image.runs, 'finished run')}`"
                :spark="imageSpark"
                data-testid="docker-kpi-image-p50"
              />
              <KpiCard label="Test image · p90 run" :value="seconds(image.p90)" :footnote="`Longest ${seconds(image.max)}`" data-testid="docker-kpi-image-p90" />
              <KpiCard label="Test image · jobs" :value="image.jobs" :footnote="runsFoot(image)" data-testid="docker-kpi-image-jobs" />
              <KpiCard
                label="Base · median run"
                :value="seconds(base.p50)"
                :footnote="`p90 ${seconds(base.p90)} · ${counted(base.runs, 'finished run')}`"
                data-testid="docker-kpi-base-p50"
              />
              <KpiCard
                label="Base · rebuild share"
                :value="share(base.rebuildShare)"
                :footnote="base.cache ? `${integer(base.cache.partial)} partial, ${integer(base.cache.full)} full · ${integer(base.cache.reused)} reused` : ''"
                data-testid="docker-kpi-base-share"
              />
              <KpiCard
                label="Base · full rebuilds"
                :value="base.cache?.full ?? 0"
                :footnote="base.fullP50 === null ? 'No run took 2 hours or more' : `Median ${seconds(base.fullP50)} each`"
                data-testid="docker-kpi-base-full"
              />
            </StatGrid>

            <div class="grid grid-cols-1 items-start gap-4 *:min-w-0 md:gap-6 @5xl/main:grid-cols-2">
              <Card :class="['gap-4', ENTER]" data-testid="docker-daily">
                <CardHeader>
                  <CardTitle as="h2">ROCm test image run time</CardTitle>
                  <CardDescription>Median and p90 per UTC day of finished final attempts. Days without one are left blank.</CardDescription>
                </CardHeader>
                <CardContent>
                  <AreaChart
                    v-if="dayRows.length"
                    :rows="dayRows"
                    :x="(row: DayRow) => row.start"
                    :row-label="dayLabel"
                    :series="DAILY_SERIES"
                    label="ROCm test image daily median and p90 run time"
                    :y-format="minutesLabel"
                    :y-ticks="dailyTicks"
                    :y-domain="[0, dailyTicks.at(-1)!]"
                    :max-gap="DAY_MS * 1.5"
                    inspect-placement="none"
                  />
                  <p v-else class="text-sm text-muted-foreground">No finished test image builds in this window.</p>
                </CardContent>
              </Card>
              <Card :class="['gap-4', ENTER]" data-testid="docker-rebuilds">
                <CardHeader>
                  <CardTitle as="h2">ROCm base rebuilds</CardTitle>
                  <CardDescription>
                    Every base step run of 10 minutes or more, on a log scale.
                    <span data-testid="docker-reused">{{ integer(rebuilds.reused) }} of {{ integer(base.runs) }} runs reused the cached image and are not drawn.</span>
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <DockerRebuildChart v-if="rebuilds.points.length" :points="rebuilds.points" :range="range" @open="open" />
                  <p v-else class="text-sm text-muted-foreground">Every base step run in this window reused the cached image.</p>
                  <p v-if="rebuilds.total > rebuilds.points.length" class="mt-2 text-xs text-muted-foreground">
                    Showing the newest {{ integer(rebuilds.points.length) }} of {{ integer(rebuilds.total) }} rebuilds.
                  </p>
                </CardContent>
              </Card>
            </div>

            <Card :class="['gap-4', ENTER]" data-testid="docker-jobs">
              <CardHeader>
                <CardTitle as="h2">Jobs</CardTitle>
                <CardDescription>{{ integer(jobs.length) }} docker build jobs of {{ sliceLabel }}, newest first. Select a job for its details.</CardDescription>
              </CardHeader>
              <CardContent>
                <DataTable
                  :columns="tableColumns"
                  :rows="jobs"
                  :filters="tableFilters"
                  :search-text="jobSearch"
                  :page-size="25"
                  :on-row-click="open"
                  show-total
                  search-placeholder="Filter step, build or job id…"
                  empty-text="No jobs match these filters."
                  label="Docker build jobs"
                />
              </CardContent>
            </Card>
          </template>
        </template>
      </template>
    </AsyncBoundary>
    <SheetStack :sheets="sheets" />
  </div>
</template>
