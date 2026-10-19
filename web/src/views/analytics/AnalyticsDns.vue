<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DnsEvidenceSheet from '@/components/analytics/dns/DnsEvidenceSheet.vue'
import DnsQueueCard from '@/components/analytics/dns/DnsQueueCard.vue'
import { useDnsData } from '@/components/analytics/dns/useDnsData'
import KpiCard from '@/components/ops/KpiCard.vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import {
  DNS_FALLBACK_ASSET,
  DNS_LIVE_ASSET,
  QUEUE_DNS_WINDOW_IDS,
  QUEUE_DNS_WINDOW_OPTIONS,
  dnsPresentation,
  queueDnsNodeEvidence,
  queueDnsSourceLabel,
  type DnsEvidenceDrawer,
  type DnsNodeBar,
  type DnsQueueCard as QueueCard,
} from '@/domain/dns'
import { age, shortDate, value, type Tone } from '@/domain/format'

const route = useRoute()
const router = useRouter()
const { payload, error, loading, retry } = useDnsData()

function setQuery(key: string, next: string, fallback: string) {
  void router.replace({ query: { ...route.query, [key]: next === fallback ? undefined : next } })
}
// Classic syncRouteState: unknown values fall back to the defaults.
const requestedWindow = computed(() => {
  const raw = String(route.query.dns_window ?? '')
  return QUEUE_DNS_WINDOW_IDS.includes(raw) ? raw : '24h'
})
const scope = computed({
  get: () => (route.query.dns_scope === 'canonical' ? 'canonical' : 'amd'),
  set: (next: string) => next && setQuery('dns_scope', next, 'amd'),
})

const view = computed(() => (payload.value ? dnsPresentation(payload.value, requestedWindow.value, scope.value) : null))
// Classic rewrites the window when the published aggregate lacks the requested one.
watch(view, (current) => {
  if (current?.block && current.windowId !== requestedWindow.value) setQuery('dns_window', current.windowId, '24h')
})
const windowModel = computed({
  get: () => view.value?.windowId ?? requestedWindow.value,
  set: (next: string) => setQuery('dns_window', next, '24h'),
})
const windowLabel = computed(() => QUEUE_DNS_WINDOW_OPTIONS.find((o) => o.id === view.value?.windowId)?.label ?? view.value?.windowId)

const KPI_IDS = ['jobs', 'queues', 'nodes', 'hf', 'outcomes']
const KPI_TITLES = ['Jobs with DNS observations', 'Affected queues', 'Physical nodes', 'Hugging Face jobs', 'Passed / nonpassing']
// Classic tones only the first and last summary items; the badge names that tone.
const BADGES: Record<string, Partial<Record<Tone, string>>> = {
  jobs: { soft: 'observed', pass: 'none observed' },
  outcomes: { hard: 'hard-failed', soft: 'soft-failed', pass: 'all passed' },
}
const kpis = computed(() =>
  (view.value?.summary ?? []).map((item, index) => {
    const id = KPI_IDS[index]!
    return {
      ...item,
      id,
      title: KPI_TITLES[index]!,
      badge: item.tone ? BADGES[id]?.[item.tone] : undefined,
      badgeTone: (item.tone || 'neutral') as Tone,
    }
  }),
)

const drawer = ref<{ open: boolean; data: DnsEvidenceDrawer | null }>({ open: false, data: null })
function openEvidence(card: QueueCard, bar: DnsNodeBar) {
  if (!payload.value || !view.value) return
  drawer.value = { open: true, data: queueDnsNodeEvidence(payload.value, view.value.windowId, card.queue, bar.resolved, view.value.coverage) }
}

const LEGEND = [
  ['bg-success', 'Passed after observation'],
  ['bg-warning', 'Soft-failed after observation'],
  ['bg-destructive', 'Hard-failed after observation'],
]
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6" data-testid="analytics-dns">
    <p class="max-w-3xl text-sm text-muted-foreground">
      DNS resolver observations by AMD queue and physical node, with final job outcomes and exact Buildkite evidence.
      <span v-if="payload?.generated_at" class="whitespace-nowrap" :title="payload.generated_at">Updated {{ age(payload.generated_at) }}.</span>
    </p>

    <div v-if="loading && !payload" class="flex flex-col gap-4" aria-busy="true">
      <p class="text-sm text-muted-foreground">Loading DNS observations...</p>
      <div class="grid gap-4 md:grid-cols-5"><Skeleton v-for="i in 5" :key="i" class="h-28" /></div>
      <Skeleton class="h-64" />
    </div>
    <div v-else-if="error" class="flex flex-col items-start gap-3 rounded-lg border border-destructive/40 p-6" role="alert">
      <h3 class="text-lg font-semibold">DNS observation data is unavailable.</h3>
      <p class="font-mono text-sm text-destructive">{{ error }}</p>
      <div class="flex flex-wrap gap-2">
        <Button @click="retry">Retry</Button>
        <Button variant="outline" as="a" :href="DNS_LIVE_ASSET" target="_blank" rel="noopener">Open live DNS asset</Button>
        <Button variant="outline" as="a" :href="DNS_FALLBACK_ASSET" target="_blank" rel="noopener">Open Pages DNS fallback</Button>
      </div>
    </div>
    <p v-else-if="view && !view.block" class="rounded-md border border-destructive/40 p-4 text-sm text-destructive" role="alert">
      The selected DNS observation window is not present in the published aggregate.
    </p>

    <template v-else-if="view && view.block && payload">
      <div class="flex flex-col gap-2">
        <div class="flex flex-wrap items-end gap-3">
          <div class="flex flex-col gap-1.5">
            <span class="text-xs font-medium text-muted-foreground">Queue scope</span>
            <ToggleGroup
              v-model="scope"
              type="single"
              variant="outline"
              size="sm"
              aria-label="DNS queue scope"
              aria-describedby="v2-dns-scope-help"
            >
              <ToggleGroupItem value="canonical">Canonical AMD (12)</ToggleGroupItem>
              <ToggleGroupItem value="amd">All active AMD GPU</ToggleGroupItem>
            </ToggleGroup>
          </div>
          <div class="flex flex-col gap-1.5">
            <span class="text-xs font-medium text-muted-foreground">Window</span>
            <Select v-model="windowModel">
              <SelectTrigger size="sm" class="w-40" aria-label="DNS observation window" data-testid="dns-window">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem v-for="option in QUEUE_DNS_WINDOW_OPTIONS" :key="option.id" :value="option.id">{{ option.label }}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <ToneBadge class="ml-auto" :tone="view.freshness.stale ? 'soft' : 'pass'" data-testid="dns-freshness">
            {{ (view.freshness.stale ? 'Stale - ' : 'Updated ') + age(view.block.end_exclusive) }}
          </ToneBadge>
        </div>
        <p id="v2-dns-scope-help" class="max-w-4xl text-xs text-muted-foreground">
          Canonical AMD is the 12 standard MI250, MI300, and MI355 queues at widths 1, 2, 4, and 8. All active AMD GPU also includes other amd_mi*
          models and widths, such as MI325; retired MI355B queues are excluded.
        </p>
      </div>

      <div v-if="view.freshness.stale" class="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning" role="alert" data-testid="dns-stale">
        <strong>DNS observations are stale. </strong>
        <span>{{ value(windowLabel, view.windowId) }} ended {{ shortDate(view.block.end_exclusive) }}. Treat these as historical observations, not the current window.</span>
      </div>
      <div v-if="!view.coverage.complete" class="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning" role="status" data-testid="dns-coverage">
        <strong>Partial coverage - counts are lower bounds. </strong>
        <span>{{ (view.coverage.facts.length ? view.coverage.facts.join(' - ') + '. ' : '') + 'Missing observations never mean zero.' }}</span>
      </div>

      <section
        class="grid grid-cols-1 gap-4 @xl/main:grid-cols-2 @4xl/main:grid-cols-6"
        aria-label="DNS observation summary"
        data-testid="dns-summary"
      >
        <KpiCard
          v-for="(item, index) in kpis"
          :key="item.id"
          :class="index > 2 ? '@4xl/main:col-span-3' : '@4xl/main:col-span-2'"
          :data-testid="`dns-kpi-${item.id}`"
          :label="item.title"
          :value="item.value"
          :badge="item.badge"
          :badge-tone="item.badgeTone"
          :detail="item.meta"
        />
      </section>

      <Card>
        <CardHeader>
          <CardTitle>DNS observations by queue and physical node</CardTitle>
          <CardDescription>Affected queues only. Select any node bar to open the exact retained Buildkite logs in the right-side sheet.</CardDescription>
        </CardHeader>
        <CardContent class="flex flex-col gap-4">
          <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label="Job outcome legend">
            <span v-for="[cls, label] in LEGEND" :key="label" class="inline-flex items-center gap-1.5">
              <span class="size-2.5 rounded-sm" :class="cls" />{{ label }}
            </span>
            <span class="italic">Outcome is correlation, not proof DNS caused the result.</span>
          </div>
          <p v-if="!view.cards.length" class="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground" data-testid="dns-empty">
            {{ view.emptyText }}
          </p>
          <div v-else class="grid gap-4 @3xl/main:grid-cols-2 @7xl/main:grid-cols-3">
            <DnsQueueCard v-for="card in view.cards" :key="card.queue.queue" :card="card" @open="(bar) => openEvidence(card, bar)" />
          </div>
        </CardContent>
      </Card>

      <details class="group rounded-xl border bg-card px-6 py-4 text-sm">
        <summary class="cursor-pointer font-medium">Counting method and data provenance</summary>
        <div class="mt-3 flex flex-col gap-2 text-muted-foreground" data-testid="dns-method">
          <p>
            One job attempt counts once when its complete log contains a DNS-specific resolver signature. Repeated matching lines collapse into
            episodes; retries remain distinct attempts. Generic connection, TLS, timeout, and HTTP failures do not count.
          </p>
          <p>
            Selected window: {{ shortDate(view.block.start) }} to {{ shortDate(view.block.end_exclusive) }}. Schema v{{ value(payload.schema_version) }},
            generated {{ shortDate(payload.generated_at) }}, source: {{ queueDnsSourceLabel(payload.__sourceAsset) }}.
          </p>
          <p v-if="view.coverage.notes.length">{{ view.coverage.notes.join('; ') }}.</p>
          <div class="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" as="a" :href="payload.__sourceAsset || DNS_FALLBACK_ASSET" target="_blank" rel="noopener">Open selected DNS data</Button>
            <Button variant="outline" size="sm" as="a" :href="DNS_FALLBACK_ASSET" target="_blank" rel="noopener">Open Pages DNS fallback</Button>
          </div>
        </div>
      </details>
    </template>

    <DnsEvidenceSheet v-model:open="drawer.open" :drawer="drawer.data" :source-asset="payload?.__sourceAsset" />
  </div>
</template>
