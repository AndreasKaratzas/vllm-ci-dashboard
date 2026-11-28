<script setup lang="ts">
import { Container, ExternalLink } from '@lucide/vue'
import { computed, h } from 'vue'
import DockerAbout from '@/components/analytics/docker/DockerAbout.vue'
import DockerHeadline from '@/components/analytics/docker/DockerHeadline.vue'
import DockerSpread, { type SpreadRow } from '@/components/analytics/docker/DockerSpread.vue'
import { episodeColumns, episodeDetail, jobColumns, jobDetail, jobFilters, jobSearch } from '@/components/analytics/docker/details'
import AreaChart from '@/components/charts/AreaChart.vue'
import BarChart from '@/components/charts/BarChart.vue'
import { shortDay, shortDayTime, timeTicks, type SeriesSpec } from '@/components/charts/chart'
import SheetStack from '@/components/evidence/SheetStack.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import SegmentedSelect from '@/components/layout/SegmentedSelect.vue'
import type { Delta } from '@/components/stat/DeltaBadge.vue'
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
  DAY_MS,
  DOCKER_DEFAULT_WINDOW,
  DOCKER_PIPELINES,
  DOCKER_SCOPES,
  DOCKER_WINDOWS,
  DOCKER_WINDOW_IDS,
  HOUR_MS,
  IMAGE_STEP,
  MIN_COMPARE_RUNS,
  bucketNoun,
  compareCount,
  comparePercent,
  comparePoints,
  decodeJobs,
  dockerBuckets,
  dockerCollected,
  dockerFreshness,
  dockerRange,
  elapsedS,
  filterJobs,
  hitText,
  previousRange,
  rebuildEpisodes,
  rebuildingAt,
  runSpread,
  stepStats,
  type BucketPoint,
  type DockerJob,
  type DockerPipeline,
  type DockerScope,
  type DockerWindow,
  type StepStats,
} from '@/domain/dockerBuilds'
import { age, agentHours, counted, integer, seconds, shortDate } from '@/domain/format'
import { SERIES } from '@/lib/palette'

const docker = useDataset('docker_builds')
const payload = docker.data
const now = useClock()
const sheets = useSheetStack()
const [LIVE_ASSET, PAGES_ASSET] = DATASETS.docker_builds.paths

const windowId = useQueryState<DockerWindow>('docker_window', { default: DOCKER_DEFAULT_WINDOW, allowed: DOCKER_WINDOW_IDS })
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
const spec = computed(() => DOCKER_WINDOWS.find((w) => w.id === windowId.value)!)
const asOf = computed(() => (payload.value ? Date.parse(payload.value.generated_at) : 0))
const range = computed(() => (payload.value ? dockerRange(payload.value, windowId.value) : null))
const prevRange = computed(() => (payload.value && range.value ? previousRange(payload.value, range.value) : null))
const slice = (r: typeof range.value) =>
  payload.value && r ? filterJobs(decodeJobs(payload.value), r, { scope: scope.value, pipeline: pipeline.value }) : []
const jobs = computed<DockerJob[]>(() => slice(range.value))
const prevJobs = computed<DockerJob[]>(() => slice(prevRange.value))
const image = computed(() => stepStats(jobs.value, IMAGE_STEP, asOf.value))
const base = computed(() => stepStats(jobs.value, BASE_STEP, asOf.value))
const prevImage = computed(() => stepStats(prevJobs.value, IMAGE_STEP, asOf.value))
const prevBase = computed(() => stepStats(prevJobs.value, BASE_STEP, asOf.value))
const monthRate = computed(() =>
  payload.value && windowId.value !== '30d' ? stepStats(slice(dockerRange(payload.value, '30d')), BASE_STEP, asOf.value).hitRate : null,
)

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

// ---- Deltas against the equal-length period before the window, only with enough runs in both ----
const baseComparable = computed(() => !!prevRange.value && Math.min(base.value.decided, prevBase.value.decided) >= MIN_COMPARE_RUNS)
const imageComparable = computed(() => !!prevRange.value && Math.min(image.value.runs, prevImage.value.runs) >= MIN_COMPARE_RUNS)
const noDelta = (comparable: boolean) => (!prevRange.value ? 'No earlier period in the 30-day data' : comparable ? '' : 'Too few runs to compare')
const deltaLabel = computed(() => `vs the previous ${spec.value.span}`)
const signed = (value: number, unit = '') => `${value > 0 ? '+' : value < 0 ? '−' : ''}${Math.abs(value)}${unit}`
function delta(comparable: boolean, value: number | null, goodWhen: 'up' | 'down', unit = ''): Delta | undefined {
  return comparable && value !== null ? { value, text: signed(value, unit), goodWhen, label: deltaLabel.value } : undefined
}
const withNote = (foot: string, shown: Delta | undefined, comparable: boolean, note = noDelta(comparable)) => (shown ? foot : [foot, note].filter(Boolean).join(' · '))

const headlineDelta = computed(() => delta(baseComparable.value, comparePoints(base.value.hitRate, prevBase.value.hitRate), 'up', ' pts'))
const rebuildCount = (stats: StepStats) => (stats.cache ? stats.cache.partial + stats.cache.full : 0) + stats.rebuilding
const rebuildsDelta = computed(() => delta(baseComparable.value, compareCount(rebuildCount(base.value), rebuildCount(prevBase.value)), 'down'))
const timeDelta = computed(() => delta(baseComparable.value, comparePercent(base.value.rebuildS, prevBase.value.rebuildS), 'down', '%'))
const imageDelta = computed(() => delta(imageComparable.value, comparePercent(image.value.p50, prevImage.value.p50), 'down', '%'))
const failedDelta = computed(() =>
  delta(baseComparable.value && imageComparable.value, compareCount(image.value.failed + base.value.failed, prevImage.value.failed + prevBase.value.failed), 'down'),
)

const rebuildsFoot = computed(() => {
  const { cache, rebuilding } = base.value
  if (!rebuildCount(base.value)) return 'None, every run reused the cache'
  return `${integer(cache?.partial ?? 0)} partial · ${integer(cache?.full ?? 0)} full` + (rebuilding ? ` · ${integer(rebuilding)} still running` : '')
})
const timeFoot = computed(
  () =>
    (base.value.fullP50 === null ? 'No full rebuild' : `Median full rebuild ${seconds(base.value.fullP50)}`) +
    (base.value.rebuilding ? ` · ${integer(base.value.rebuilding)} still running (${agentHours(inFlightS.value)} so far), not counted` : ''),
)
type RunCounts = Pick<StepStats, 'jobs' | 'runs' | 'retried' | 'failed'>
const runsFoot = (stats: RunCounts) =>
  [stats.retried && `${integer(stats.retried)} retried`, stats.failed && `${integer(stats.failed)} failed`, stats.jobs - stats.runs - stats.retried > 0 && `${integer(stats.jobs - stats.runs - stats.retried)} unfinished or canceled`]
    .filter(Boolean)
    .join(' · ') || 'Every job finished on its first attempt'
const bothSteps = computed<RunCounts>(() => ({
  jobs: image.value.jobs + base.value.jobs,
  runs: image.value.runs + base.value.runs,
  retried: image.value.retried + base.value.retried,
  failed: image.value.failed + base.value.failed,
}))

// ---- Buckets: base outcomes and the test image's run time ----
const noun = computed(() => (range.value ? bucketNoun(range.value.bucketMs) : 'day'))
const tickLabel = computed(() => (range.value ? timeTicks([range.value.startMs, range.value.endMs], range.value.buckets).label : shortDay))
const clock = (ms: number) => shortDayTime(ms).split(', ').at(-1)
function bucketTitle(point: { start: number; end: number }) {
  if (!range.value || range.value.bucketMs >= DAY_MS) return shortDay(point.start)
  return point.end >= range.value.endMs
    ? `${shortDayTime(point.start)} UTC – collection`
    : `${shortDayTime(point.start)}–${clock(point.end)} UTC`
}
type OutcomeRow = BucketPoint & { partialOrRunning: number; decided: number; [key: string]: unknown }
const baseBuckets = computed(() => (range.value ? dockerBuckets(jobs.value, range.value, BASE_STEP, asOf.value) : []))
const outcomeRows = computed<OutcomeRow[]>(() => baseBuckets.value.map((p) => ({ ...p, partialOrRunning: p.partial + p.rebuilding, decided: p.runs + p.rebuilding })))
const OUTCOME_SERIES: SeriesSpec[] = [
  { key: 'full', label: 'Full rebuild (2 h or more)', color: SERIES.secondary },
  { key: 'partialOrRunning', label: 'Partial rebuild (10 min–2 h) or still running', color: SERIES.primary },
  { key: 'reused', label: 'Reused (under 10 min)', color: SERIES.muted },
]
const outcomeLabel = (row: OutcomeRow) =>
  [bucketTitle(row), counted(row.decided, 'run'), row.hitRate !== null && `${hitText(row.hitRate)} reused`, row.rebuilding && `${integer(row.rebuilding)} still running`]
    .filter(Boolean)
    .join(' · ')
const rebuildSpark = computed(() => baseBuckets.value.map((p) => p.partial + p.full + p.rebuilding))
const timeSpark = computed(() => baseBuckets.value.map((p) => p.rebuildS))

interface ImageRow {
  start: number
  end: number
  runs: number
  p50: number
  p90: number
  [key: string]: unknown
}
const imageBuckets = computed(() => (range.value ? dockerBuckets(jobs.value, range.value, IMAGE_STEP, asOf.value) : []))
const imageRows = computed<ImageRow[]>(() =>
  imageBuckets.value.flatMap((p) => (p.p50 === null || p.p90 === null ? [] : [{ start: p.start, end: p.end, runs: p.runs, p50: p.p50 / 60, p90: p.p90 / 60 }])),
)
const IMAGE_SERIES: SeriesSpec[] = [
  { key: 'p90', label: 'p90 run time', color: SERIES.secondary },
  { key: 'p50', label: 'Median run time', color: SERIES.primary },
]
const minutesLabel = (minutes: number) => seconds(minutes * 60)
const MINUTE_STEPS = [5, 10, 15, 30, 60, 120, 240]
const imageTicks = computed(() => {
  const peak = Math.max(1, ...imageRows.value.map((row) => row.p90))
  const step = MINUTE_STEPS.find((s) => peak / s <= 4) ?? 480
  return Array.from({ length: Math.ceil(peak / step) + 1 }, (_, i) => i * step)
})
const imageLabel = (row: ImageRow) => `${bucketTitle(row)} · ${counted(row.runs, 'build')}`
const imageSpark = computed(() => imageRows.value.map((row) => row.p50))

// ---- Spread of run times and rebuild episodes ----
const spreadRows = computed<SpreadRow[]>(() => {
  const rows: [string, string, string, 'reused' | 'rebuilt' | undefined][] = [
    ['reused', 'Base · reused', BASE_STEP, 'reused'],
    ['rebuilt', 'Base · rebuilt', BASE_STEP, 'rebuilt'],
    ['image', 'Test image', IMAGE_STEP, undefined],
  ]
  return rows.map(([key, label, step, cls]) => {
    const prev = prevRange.value ? runSpread(prevJobs.value, step, cls) : null
    return {
      key,
      label,
      spread: runSpread(jobs.value, step, cls),
      previousP50: prev && prev.n >= 5 ? prev.p50 : null,
      note: key === 'rebuilt' && base.value.rebuilding ? `${integer(base.value.rebuilding)} still rebuilding, not timed` : undefined,
    }
  })
})
const episodes = computed(() =>
  range.value ? rebuildEpisodes(slice({ ...range.value, startMs: 0 }), asOf.value).filter((ep) => ep.lastMs >= range.value!.startMs) : [],
)
const inFlightS = computed(() => jobs.value.reduce((sum, job) => sum + (rebuildingAt(job, asOf.value) ? elapsedS(job, asOf.value) : 0), 0))
const EPISODE_COLUMNS = episodeColumns()

const tableColumns = jobColumns()
const tableFilters = computed(() => jobFilters(jobs.value))
const windowLabel = computed(() => spec.value.label.toLowerCase())
const shortWindow = computed(() => range.value !== null && range.value.bucketMs < HOUR_MS)
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
          <div class="flex flex-col gap-2">
            <div class="grid grid-cols-2 items-end gap-x-4 gap-y-3 sm:flex sm:flex-wrap" data-testid="docker-controls">
              <SegmentedSelect v-model="windowId" label="Window" :items="WINDOWS" />
              <SegmentedSelect v-model="scope" label="Builds" :items="SCOPES" />
              <SegmentedSelect v-model="pipeline" label="Pipeline" :items="PIPELINES" />
            </div>
            <p class="text-xs text-muted-foreground tabular-nums" data-testid="docker-window-span">
              {{ shortDayTime(range.startMs) }} – {{ shortDayTime(range.endMs) }} UTC · ends at the last collection ({{ age(payload.generated_at, now) }})
            </p>
          </div>

          <Callout v-if="freshness.stale" tone="soft" title="Docker build jobs are stale." data-testid="docker-stale">
            <p>
              Collected {{ age(payload.generated_at, now) }}, at {{ shortDate(payload.generated_at) }}. Jobs that started since then are missing.
              <template v-if="shortWindow">Short windows end at the last collection, so they show the hours before it, not the hours before now.</template>
            </p>
          </Callout>

          <EmptyState
            v-if="!jobs.length"
            title="No docker build jobs in this window."
            :description="`No ${sliceLabel} were created in the ${windowLabel}.`"
            :icon="Container"
            data-testid="docker-empty"
          />
          <template v-else>
            <DockerHeadline
              :class="ENTER"
              :stats="base"
              :delta="headlineDelta"
              :reference="monthRate"
              :window-label="spec.label"
              :footnote="headlineDelta || !base.decided ? undefined : noDelta(baseComparable) || undefined"
            />

            <StatGrid :cols="4" data-testid="docker-kpis">
              <KpiCard
                label="Base rebuilds"
                :value="rebuildCount(base)"
                :footnote="withNote(rebuildsFoot, rebuildsDelta, baseComparable)"
                :delta="rebuildsDelta"
                :spark="rebuildSpark"
                data-testid="docker-kpi-base-rebuilds"
              />
              <KpiCard
                label="Agent time rebuilding"
                :value="agentHours(base.rebuildS)"
                :footnote="withNote(timeFoot, timeDelta, baseComparable, baseComparable ? `No rebuild time in the previous ${spec.span}` : undefined)"
                :delta="timeDelta"
                :spark="timeSpark"
                data-testid="docker-kpi-rebuild-time"
              />
              <KpiCard
                label="Test image · median build"
                :value="seconds(image.p50)"
                :footnote="withNote(`p90 ${seconds(image.p90)} · ${counted(image.runs, 'finished build')}`, imageDelta, imageComparable)"
                :delta="imageDelta"
                :spark="imageSpark"
                data-testid="docker-kpi-image-p50"
              />
              <KpiCard
                label="Failed jobs"
                :value="image.failed + base.failed"
                :footnote="withNote(runsFoot(bothSteps), failedDelta, baseComparable && imageComparable)"
                :delta="failedDelta"
                data-testid="docker-kpi-failed"
              />
            </StatGrid>

            <div class="grid grid-cols-1 items-start gap-4 *:min-w-0 md:gap-6 @5xl/main:grid-cols-2">
              <Card :class="['gap-4', ENTER]" data-testid="docker-outcomes">
                <CardHeader>
                  <CardTitle as="h2">Base step outcomes per {{ noun }}</CardTitle>
                  <CardDescription>Every base run by what it did. Rebuilds sit on the baseline; gray runs reused the cached image.</CardDescription>
                </CardHeader>
                <CardContent>
                  <BarChart
                    v-if="base.decided"
                    :rows="outcomeRows"
                    :category="(row: OutcomeRow) => tickLabel(row.start)"
                    :series="OUTCOME_SERIES"
                    :value-format="integer"
                    :row-label="outcomeLabel"
                    :label="`Base step runs per ${noun} by cache outcome`"
                  />
                  <p v-else class="text-sm text-muted-foreground">No base step run in this window.</p>
                </CardContent>
              </Card>
              <Card :class="['gap-4', ENTER]" data-testid="docker-spread">
                <CardHeader>
                  <CardTitle as="h2">How long builds take</CardTitle>
                  <CardDescription>
                    Fastest, quartiles, p90 and slowest finished final attempt on a log time scale. Base runs are summarised apart for cache hits and rebuilds.
                    <template v-if="prevRange">The caret marks the median of the previous {{ spec.span }}.</template>
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <DockerSpread :rows="spreadRows" />
                </CardContent>
              </Card>
            </div>

            <div class="grid grid-cols-1 items-start gap-4 *:min-w-0 md:gap-6 @5xl/main:grid-cols-2">
              <Card :class="['gap-4', ENTER]" data-testid="docker-daily">
                <CardHeader>
                  <CardTitle as="h2">ROCm test image build time per {{ noun }}</CardTitle>
                  <CardDescription>Median and p90 of finished final attempts per {{ noun }}. Spans without one are left blank.</CardDescription>
                </CardHeader>
                <CardContent>
                  <AreaChart
                    v-if="imageRows.length > 1"
                    :rows="imageRows"
                    :x="(row: ImageRow) => row.start"
                    :row-label="imageLabel"
                    :series="IMAGE_SERIES"
                    :label="`ROCm test image median and p90 run time per ${noun}`"
                    :y-format="minutesLabel"
                    :y-ticks="imageTicks"
                    :y-domain="[0, imageTicks.at(-1)!]"
                    :max-gap="range.bucketMs * 1.5"
                    :time-axis="range.bucketMs < DAY_MS"
                    inspect-placement="none"
                  />
                  <p v-else-if="imageRows.length" class="text-sm text-muted-foreground">
                    Only one span has finished builds: {{ imageLabel(imageRows[0]!) }}, median {{ minutesLabel(imageRows[0]!.p50) }} and p90 {{ minutesLabel(imageRows[0]!.p90) }}.
                  </p>
                  <p v-else class="text-sm text-muted-foreground">No finished test image builds in this window.</p>
                </CardContent>
              </Card>
              <Card :class="['gap-4', ENTER]" data-testid="docker-episodes">
                <CardHeader>
                  <CardTitle as="h2">Rebuild episodes</CardTitle>
                  <CardDescription>
                    Base rebuilds that started within an hour of each other, newest first. Each rebuild holds an amd-cpu agent for its whole run. Select an episode for its
                    jobs.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <DataTable
                    v-if="episodes.length"
                    :columns="EPISODE_COLUMNS"
                    :rows="episodes"
                    :page-size="10"
                    :searchable="false"
                    :on-row-click="(ep) => sheets.open(episodeDetail(ep))"
                    label="Base rebuild episodes"
                  />
                  <p v-else class="text-sm text-muted-foreground" data-testid="docker-episodes-empty">
                    No base rebuilds in the {{ windowLabel }}. Every finished run reused the cached image.
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
