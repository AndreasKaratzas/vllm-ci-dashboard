<script setup lang="ts">
import { ExternalLink, Radar } from '@lucide/vue'
import { computed, h, watch } from 'vue'
import DnsAbout from '@/components/analytics/dns/DnsAbout.vue'
import DnsQueueCard from '@/components/analytics/dns/DnsQueueCard.vue'
import { evidenceDetail, nodeDetail, nodesDetail, queueDetail, type DnsContext } from '@/components/analytics/dns/details'
import BarChart from '@/components/charts/BarChart.vue'
import type { SeriesSpec } from '@/components/charts/chart'
import Legend from '@/components/charts/Legend.vue'
import SheetStack from '@/components/evidence/SheetStack.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import SegmentedSelect from '@/components/layout/SegmentedSelect.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
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
  dnsPresentation,
  queueDnsSourceLabel,
  type DnsQueueCard as QueueCard,
} from '@/domain/dns'
import { age, integer, shortDate, value } from '@/domain/format'
import { statusVar } from '@/lib/palette'
import type { Tone } from '@/lib/tone'

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
const windowLabel = computed(() => QUEUE_DNS_WINDOW_OPTIONS.find((o) => o.id === view.value?.windowId)?.label ?? view.value?.windowId)

useSubviewHeader(() => {
  const data = payload.value
  const current = view.value
  if (!data || !current) return {}
  const stale = current.freshness.stale
  return {
    freshness: {
      text: `Collected ${age(data.generated_at, now.value)}`,
      tone: stale ? 'hard' : 'pass',
      detail: `DNS observations were generated ${shortDate(data.generated_at)} from the ${queueDnsSourceLabel(data.__sourceAsset)}. They are stale after 12 hours.`,
    },
    about: () => h(DnsAbout, { payload: data, view: current }),
  }
})

// The domain names a tone only for the first and last summary items.
const KPI_IDS = ['jobs', 'queues', 'nodes', 'hf', 'outcomes'] as const
const KPI_LABELS = ['Jobs with DNS observations', 'Affected queues', 'Physical nodes', 'Hugging Face jobs', 'Passed / nonpassing']
const OUTCOME_LABEL: Partial<Record<Tone, string>> = { hard: 'Hard-failed', soft: 'Soft-failed', pass: 'All passed' }
const kpis = computed(() => {
  const current = view.value
  const context = ctx.value
  if (!current || !context) return []
  const open = { jobs: () => sheets.open(evidenceDetail(context)), nodes: () => sheets.open(nodesDetail(context)) } as Record<string, () => void>
  return current.summary.map((item, index) => {
    const id = KPI_IDS[index]!
    return {
      id,
      label: KPI_LABELS[index]!,
      value: item.value === '-' ? null : item.value,
      footnote: item.meta,
      status: id === 'outcomes' && item.tone && OUTCOME_LABEL[item.tone] ? { tone: item.tone, label: OUTCOME_LABEL[item.tone] } : undefined,
      action: open[id] ? { label: id === 'jobs' ? 'Open the retained job logs' : 'Open every affected node', onOpen: open[id] } : undefined,
    }
  })
})

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
// Horizontal bars stack upwards from the first row; the busiest queue goes on top.
const chartRows = computed(() => [...queueRows.value].reverse())
const OUTCOME_SERIES: SeriesSpec[] = [
  { key: 'passed', label: 'Passed after observation', color: statusVar('pass') },
  { key: 'soft', label: 'Soft-failed after observation', color: statusVar('soft') },
  { key: 'hard', label: 'Hard-failed after observation', color: statusVar('hard') },
  { key: 'unknown', label: 'Outcome unavailable', color: statusVar('unknown') },
]
const queueSeries = computed(() => OUTCOME_SERIES.filter((s) => queueRows.value.some((row) => row[s.key as keyof QueueRow])))
const nodeLegend = computed(() => OUTCOME_SERIES.filter((s) => s.key !== 'unknown' || queueRows.value.some((row) => row.unknown)))

function openQueue(card: QueueCard) {
  if (ctx.value) sheets.open(queueDetail(ctx.value, card))
}
function openNode(card: QueueCard, bar: QueueCard['bars'][number]) {
  if (ctx.value) sheets.open(nodeDetail(ctx.value, card, bar))
}
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
        <div class="flex flex-wrap items-end gap-x-4 gap-y-3" data-testid="dns-controls">
          <SegmentedSelect v-model="scope" label="DNS queue scope" :items="SCOPES" class="w-full sm:w-auto" />
          <SegmentedSelect v-model="windowModel" label="DNS observation window" :items="WINDOWS" class="w-full sm:w-auto" />
        </div>

        <EmptyState v-if="!view.block" title="The selected DNS observation window is not present in the published aggregate." :icon="Radar" />
        <template v-else>
          <Callout v-if="view.freshness.stale" tone="soft" title="DNS observations are stale." data-testid="dns-stale">
            {{ value(windowLabel, view.windowId) }} ended {{ shortDate(view.block.end_exclusive) }}. Treat these as historical observations, not the
            current window.
          </Callout>
          <Callout v-if="!view.coverage.complete" tone="soft" title="Partial coverage: counts are lower bounds" data-testid="dns-coverage">
            <span class="flex flex-wrap items-center gap-1.5">
              <span v-for="fact in view.coverage.facts" :key="fact" class="rounded-md bg-background/70 px-1.5 py-0.5 text-xs tabular-nums" data-fact>{{ fact }}</span>
              <span class="text-xs">Missing observations never mean zero.</span>
            </span>
          </Callout>

          <EmptyState
            v-if="!view.cards.length"
            :title="view.emptyText"
            :description="`${scope === 'canonical' ? 'Canonical AMD' : 'All active AMD GPU'} queues, ${value(windowLabel, view.windowId).toLowerCase()}.`"
            :cause="view.coverage.complete ? undefined : `Coverage ${view.coverage.status}`"
            :icon="Radar"
            data-testid="dns-empty"
          />
          <template v-else>
            <div class="grid grid-cols-2 gap-3 *:min-w-0 md:gap-4 @xl/main:grid-cols-6" data-testid="dns-summary">
              <KpiCard
                v-for="(kpi, index) in kpis"
                :key="kpi.id"
                :data-testid="`dns-kpi-${kpi.id}`"
                :class="index < 3 ? '@xl/main:col-span-2' : index === 4 ? 'col-span-2 @xl/main:col-span-3' : '@xl/main:col-span-3'"
                :style="{ animationDelay: `${index * 30}ms` }"
                :label="kpi.label"
                :value="kpi.value"
                :status="kpi.status"
                :footnote="kpi.footnote"
                :action="kpi.action"
              />
            </div>

            <Card class="gap-4 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300">
              <CardHeader>
                <CardTitle>Observations by queue</CardTitle>
                <CardDescription>
                  Jobs with a DNS resolver signature, split by their final outcome. Select a queue for its nodes.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <BarChart
                  :rows="chartRows"
                  :category="(row) => row.card.queue.queue"
                  :series="queueSeries"
                  orientation="horizontal"
                  label="DNS observations by queue and final job outcome"
                  :value-format="integer"
                  :hint="view.coverage.complete ? 'Click for the queue\'s nodes' : 'Lower bounds. Click for the queue\'s nodes'"
                  @select="(row: QueueRow) => openQueue(row.card)"
                />
              </CardContent>
            </Card>

            <Card class="gap-4 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300">
              <CardHeader>
                <CardTitle>Nodes by queue</CardTitle>
                <CardDescription>
                  Affected physical nodes, scaled to the busiest node. Select a node for its exact Buildkite logs.
                </CardDescription>
              </CardHeader>
              <CardContent class="flex flex-col gap-4">
                <Legend :items="nodeLegend" />
                <div class="grid grid-cols-1 items-start gap-4 *:min-w-0 @3xl/main:grid-cols-2 @7xl/main:grid-cols-3">
                  <DnsQueueCard
                    v-for="card in view.cards"
                    :key="card.queue.queue"
                    :card="card"
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
