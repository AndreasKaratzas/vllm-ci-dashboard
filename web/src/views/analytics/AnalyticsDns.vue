<script setup lang="ts">
import { ChevronDown, ExternalLink, Radar } from '@lucide/vue'
import { computed, h, watch } from 'vue'
import DnsAbout from '@/components/analytics/dns/DnsAbout.vue'
import DnsQueueCard from '@/components/analytics/dns/DnsQueueCard.vue'
import { evidenceDetail, nodeDetail, nodesDetail, queueDetail, shownCount, windowLabel, type DnsContext } from '@/components/analytics/dns/details'
import AreaChart from '@/components/charts/AreaChart.vue'
import BarChart from '@/components/charts/BarChart.vue'
import { shortDay, shortDayTime, type SeriesSpec } from '@/components/charts/chart'
import SheetStack from '@/components/evidence/SheetStack.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import SegmentedSelect from '@/components/layout/SegmentedSelect.vue'
import KpiCard, { type KpiAction } from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import { kpiTrend, type KpiTrend } from '@/components/stat/trend'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useClock } from '@/composables/useClock'
import { useDataset } from '@/composables/useDataset'
import { useQueryState } from '@/composables/useQueryState'
import { useSheetStack } from '@/composables/useSheetStack'
import { useSubviewHeader } from '@/composables/useSubviewHeader'
import {
  DNS_FALLBACK_ASSET,
  DNS_LIVE_ASSET,
  QUEUE_DNS_WINDOW_IDS,
  QUEUE_DNS_WINDOW_OPTIONS,
  dnsActivity,
  dnsPresentation,
  queueDnsDisplayCount,
  queueDnsSourceLabel,
  type DnsActivityPoint,
  type DnsQueueCard as QueueCard,
} from '@/domain/dns'
import { age, integer, shortDate } from '@/domain/format'
import { SERIES, statusVar } from '@/lib/palette'

const dns = useDataset('dns')
const payload = dns.data
const now = useClock()
const sheets = useSheetStack()

const scope = useQueryState<'amd' | 'canonical'>('dns_scope', { default: 'amd', allowed: ['amd', 'canonical'] })
const requestedWindow = useQueryState<string>('dns_window', { default: '24h', allowed: QUEUE_DNS_WINDOW_IDS })

const view = computed(() => (payload.value ? dnsPresentation(payload.value, requestedWindow.value, scope.value, now.value) : null))
// The published aggregate may lack the requested window; the URL follows the window shown.
watch(view, (current) => {
  if (current?.block && current.windowId !== requestedWindow.value) requestedWindow.value = current.windowId
})
const windowModel = computed({
  get: () => view.value?.windowId ?? requestedWindow.value,
  set: (next: string) => (requestedWindow.value = next),
})
const ctx = computed<DnsContext | null>(() => (payload.value && view.value ? { payload: payload.value, view: view.value } : null))

const SCOPES = [
  { value: 'canonical', label: 'Canonical AMD (12)' },
  { value: 'amd', label: 'All active AMD GPU' },
]
const SHORT_WINDOW: Record<string, string> = { '1h': '1h', '3h': '3h', '12h': '12h', '24h': '24h', '72h': '3d', '168h': '7d', '720h': '30d' }
const WINDOWS = QUEUE_DNS_WINDOW_OPTIONS.map((option) => ({ value: option.id, label: SHORT_WINDOW[option.id] ?? option.id, title: option.label }))

useSubviewHeader(() => {
  const data = payload.value
  const current = view.value
  if (!data || !current) return {}
  return {
    freshness: {
      text: `Collected ${age(data.generated_at, now.value)}`,
      tone: current.freshness.stale ? 'hard' : 'pass',
      detail: `DNS observations were generated ${shortDate(data.generated_at)} from the ${queueDnsSourceLabel(data.__sourceAsset)}. They are stale after 12 hours.`,
    },
    about: () => h(DnsAbout, { payload: data, view: current }),
  }
})

// ---- Activity over the window, from the retained exact evidence ----
const activity = computed(() => (payload.value && view.value?.block ? dnsActivity(payload.value, view.value) : null))
type ActivityRow = DnsActivityPoint & { nonpassing: number }
const activityRows = computed<ActivityRow[]>(() => (activity.value?.points ?? []).map((p) => ({ ...p, nonpassing: p.soft + p.hard })))
const daily = computed(() => (activity.value?.bucketMs ?? 0) >= 86_400_000)
const bucketLabel = (row: ActivityRow) =>
  daily.value ? shortDay(row.start) : `${shortDayTime(row.start)} – ${shortDayTime(row.start + (activity.value?.bucketMs ?? 0)).split(', ').at(-1)}`
const bucketWord = computed(() => {
  const hours = (activity.value?.bucketMs ?? 0) / 3_600_000
  return hours >= 24 ? 'day' : hours >= 1 ? `${hours} h` : `${hours * 60} min`
})
const ACTIVITY_SERIES: SeriesSpec[] = [
  { key: 'jobs', label: 'Jobs with DNS observations', color: SERIES.primary },
  { key: 'nonpassing', label: 'Nonpassing after observation', color: SERIES.secondary },
]
function openBucket(row: ActivityRow) {
  if (ctx.value && activity.value) sheets.open(evidenceDetail(ctx.value, { start: row.start, end: row.start + activity.value.bucketMs, label: bucketLabel(row) }))
}

// ---- KPIs: the published totals, trended by the retained evidence ----
interface Kpi {
  id: string
  label: string
  value: string | number | null
  footnote: string
  empty?: string
  trend: KpiTrend
  action?: KpiAction
}
const trendOf = (pick: (row: ActivityRow) => number, goodWhen?: 'up' | 'down'): KpiTrend =>
  activity.value?.retained ? kpiTrend(activityRows.value.map(pick), goodWhen) : {}
const asValue = (text: string) => (text === '-' ? 0 : text)
const kpis = computed<Kpi[]>(() => {
  const current = view.value
  const context = ctx.value
  if (!current || !context) return []
  const [jobs, queues, nodes, hf, outcomes] = current.summary as [typeof current.summary[number], ...typeof current.summary]
  const count = (raw: number) => queueDnsDisplayCount(raw, current.coverage)
  const known = current.totals.outcomesAvailable
  const outcome = (raw: number) => (known ? asValue(count(raw)) : null)
  return [
    {
      id: 'jobs',
      label: 'Jobs with DNS observations',
      value: asValue(jobs.value),
      footnote: jobs.meta,
      trend: trendOf((r) => r.jobs, 'down'),
      action: { label: 'Open the retained job logs', onOpen: () => sheets.open(evidenceDetail(context)) },
    },
    { id: 'queues', label: 'Affected queues', value: asValue(queues!.value), footnote: queues!.meta, trend: trendOf((r) => r.queues, 'down') },
    {
      id: 'nodes',
      label: 'Physical nodes',
      value: asValue(nodes!.value),
      footnote: nodes!.meta,
      trend: trendOf((r) => r.nodes, 'down'),
      action: { label: 'Open every affected node', onOpen: () => sheets.open(nodesDetail(context)) },
    },
    { id: 'hf', label: 'Hugging Face jobs', value: asValue(hf!.value), footnote: hf!.meta, trend: trendOf((r) => r.huggingface, 'down') },
    {
      id: 'passed',
      label: 'Passed after observation',
      value: outcome(current.totals.passedJobs),
      footnote: outcomes!.meta,
      empty: known ? undefined : 'Outcomes unavailable',
      trend: known ? trendOf((r) => r.passed) : {},
    },
    {
      id: 'nonpassing',
      label: 'Nonpassing after observation',
      value: outcome(current.totals.softFailedJobs + current.totals.hardFailedJobs),
      footnote: known ? 'soft or hard failed after observation' : outcomes!.meta,
      empty: known ? undefined : 'Outcomes unavailable',
      trend: known ? trendOf((r) => r.nonpassing, 'down') : {},
    },
  ]
})

// ---- Queues and their nodes ----
type QueueRow = { card: QueueCard; passed: number; soft: number; hard: number; unknown: number }
const queueRows = computed<QueueRow[]>(() =>
  (view.value?.cards ?? []).map((card) => {
    const q = card.queue
    return {
      card,
      passed: q.passedJobs,
      soft: q.softFailedJobs,
      hard: q.hardFailedJobs,
      unknown: Math.max(0, q.affectedJobs - q.passedJobs - q.softFailedJobs - q.hardFailedJobs),
    }
  }),
)
const OUTCOME_SERIES: SeriesSpec[] = [
  { key: 'passed', label: 'Passed after observation', color: statusVar('pass') },
  { key: 'soft', label: 'Soft-failed after observation', color: statusVar('soft') },
  { key: 'hard', label: 'Hard-failed after observation', color: statusVar('hard') },
  { key: 'unknown', label: 'Outcome unavailable', color: statusVar('unknown') },
]
const queueSeries = computed(() => OUTCOME_SERIES.filter((s) => queueRows.value.some((row) => row[s.key as keyof QueueRow])))

function openQueue(card: QueueCard) {
  if (ctx.value) sheets.open(queueDetail(ctx.value, card))
}
function openNode(card: QueueCard, bar: QueueCard['bars'][number]) {
  if (ctx.value) sheets.open(nodeDetail(ctx.value, card, bar))
}

const ENTER = 'motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300'
</script>

<template>
  <div class="flex min-w-0 flex-col gap-4 md:gap-6" data-testid="analytics-dns">
    <AsyncBoundary :loading="dns.loading.value && !payload" :error="payload ? null : dns.error.value" :refreshing="dns.refreshing.value && !!payload" @retry="dns.refresh">
      <template #error>
        <div class="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" as="a" :href="DNS_LIVE_ASSET" target="_blank" rel="noopener">Live DNS data <ExternalLink /></Button>
          <Button variant="outline" size="sm" as="a" :href="DNS_FALLBACK_ASSET" target="_blank" rel="noopener">Pages fallback <ExternalLink /></Button>
        </div>
      </template>

      <template v-if="view && ctx">
        <div class="grid grid-cols-2 items-end gap-x-4 gap-y-3 sm:flex sm:flex-wrap" data-testid="dns-controls">
          <SegmentedSelect v-model="scope" label="DNS queue scope" :items="SCOPES" />
          <SegmentedSelect v-model="windowModel" label="DNS observation window" :items="WINDOWS" />
        </div>

        <EmptyState v-if="!view.block" title="The selected DNS observation window is not present in the published aggregate." :icon="Radar" />
        <template v-else>
          <Callout
            v-if="view.freshness.stale || !view.coverage.complete"
            tone="soft"
            :title="
              view.freshness.stale && !view.coverage.complete
                ? 'Stale, partial observations: counts are lower bounds'
                : view.freshness.stale
                  ? 'DNS observations are stale.'
                  : 'Partial coverage: counts are lower bounds'
            "
            data-testid="dns-notice"
          >
            <p>
              <span v-if="view.freshness.stale" data-testid="dns-stale">
                {{ windowLabel(view.windowId) }} ended {{ shortDate(view.block.end_exclusive) }}. Treat these as historical observations, not the current window.
              </span>
              <span v-if="!view.coverage.complete" data-testid="dns-coverage"> Missing observations never mean zero.</span>
            </p>
            <details v-if="!view.coverage.complete && view.coverage.facts.length" class="group/facts mt-1.5">
              <summary class="inline-flex items-center gap-1 rounded-sm text-xs font-medium text-foreground outline-none select-none focus-visible:ring-3 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
                <ChevronDown class="size-3.5 -rotate-90 transition-transform group-open/facts:rotate-0" aria-hidden="true" />
                Collection coverage
              </summary>
              <span class="mt-2 flex flex-wrap gap-1.5">
                <span v-for="fact in view.coverage.facts" :key="fact" class="rounded-md border bg-background/70 px-1.5 py-0.5 text-xs text-foreground tabular-nums" data-fact>{{ fact }}</span>
              </span>
            </details>
          </Callout>

          <EmptyState
            v-if="!view.cards.length"
            :title="view.emptyText"
            :description="`${scope === 'canonical' ? 'Canonical AMD' : 'All active AMD GPU'} queues, ${windowLabel(view.windowId).toLowerCase()}.`"
            :cause="view.coverage.complete ? undefined : `Coverage ${view.coverage.status}`"
            :icon="Radar"
            data-testid="dns-empty"
          />
          <template v-else>
            <StatGrid :cols="3" data-testid="dns-summary">
              <KpiCard
                v-for="(kpi, index) in kpis"
                :key="kpi.id"
                :data-testid="`dns-kpi-${kpi.id}`"
                :style="{ animationDelay: `${index * 30}ms` }"
                :label="kpi.label"
                :value="kpi.value"
                :empty="kpi.empty"
                :footnote="kpi.footnote"
                :spark="kpi.trend.spark"
                :delta="kpi.trend.delta"
                :action="kpi.action"
              />
            </StatGrid>

            <div class="grid grid-cols-1 items-start gap-4 *:min-w-0 md:gap-6 @5xl/main:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
              <Card v-if="activity" :class="['gap-4', ENTER]" data-testid="dns-activity">
                <CardHeader>
                  <CardTitle>Observations over time</CardTitle>
                  <CardDescription>
                    Jobs first observed with a DNS resolver signature per {{ bucketWord }}, and how many did not pass. Select a point for its logs.
                  </CardDescription>
                </CardHeader>
                <CardContent class="flex flex-col gap-2">
                  <AreaChart
                    :rows="activityRows"
                    :x="(row: ActivityRow) => row.start"
                    :x-format="daily ? shortDay : shortDayTime"
                    :row-label="bucketLabel"
                    :series="ACTIVITY_SERIES"
                    label="DNS observations over time"
                    :y-format="integer"
                    hint="Click for this interval's logs"
                    @select="(row: ActivityRow) => openBucket(row)"
                  />
                  <p v-if="!activity.complete" class="text-xs text-muted-foreground" data-testid="dns-activity-retained">
                    Drawn from the {{ integer(activity.retained) }} retained exact logs of {{ shownCount(view.summary[0]!.value) }} jobs, so the trend is a lower bound.
                  </p>
                </CardContent>
              </Card>
              <Card :class="['gap-4', ENTER]">
                <CardHeader>
                  <CardTitle>Observations by queue</CardTitle>
                  <CardDescription>Final job outcome after the observation. Select a queue for its nodes.</CardDescription>
                </CardHeader>
                <CardContent>
                  <BarChart
                    :rows="queueRows"
                    :category="(row) => row.card.queue.queue"
                    :series="queueSeries"
                    orientation="horizontal"
                    label="DNS observations by queue and final job outcome"
                    :value-format="integer"
                    legend
                    :hint="view.coverage.complete ? 'Click for the queue\'s nodes' : 'Lower bounds. Click for the queue\'s nodes'"
                    @select="(row: QueueRow) => openQueue(row.card)"
                  />
                </CardContent>
              </Card>
            </div>

            <Card :class="['gap-4', ENTER]">
              <CardHeader>
                <CardTitle>Most affected nodes</CardTitle>
                <CardDescription>Per queue, scaled to the busiest node. Select a node for its exact Buildkite logs.</CardDescription>
              </CardHeader>
              <CardContent>
                <div class="grid grid-cols-1 items-start gap-3 *:min-w-0 @3xl/main:grid-cols-2 @6xl/main:grid-cols-3">
                  <DnsQueueCard
                    v-for="card in view.cards"
                    :key="card.queue.queue"
                    :card="card"
                    :coverage="view.coverage"
                    :visible="3"
                    @queue="openQueue(card)"
                    @open="(bar) => openNode(card, bar)"
                  />
                </div>
              </CardContent>
            </Card>
          </template>
        </template>
      </template>
    </AsyncBoundary>
    <SheetStack :sheets="sheets" />
  </div>
</template>
