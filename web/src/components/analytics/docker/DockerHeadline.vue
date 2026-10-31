<script setup lang="ts">
import { CircleDashed } from '@lucide/vue'
import { computed } from 'vue'
import ProportionBar, { type ProportionSegment } from '@/components/charts/ProportionBar.vue'
import DeltaBadge, { type Delta } from '@/components/stat/DeltaBadge.vue'
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card'
import { BASE_STEP, hitText, type StepStats } from '@/domain/dockerBuilds'
import { agentHours, integer } from '@/domain/format'
import { SERIES } from '@/lib/palette'

const props = defineProps<{ stats: StepStats; delta?: Delta; reference: number | null; windowLabel: string; footnote?: string }>()

const cache = computed(() => props.stats.cache ?? { reused: 0, partial: 0, full: 0 })
const story = computed(() => {
  const { decided, rebuilding, pending, rebuildS } = props.stats
  const { reused, partial, full } = cache.value
  const parts = [partial && `${integer(partial)} partial`, full && `${integer(full)} full`].filter(Boolean).join(' and ')
  let text = !parts && !rebuilding
    ? `Every one of the ${integer(decided)} base runs reused the cached image.`
    : `${integer(reused)} of ${integer(decided)} base runs reused the cached image.` +
      (parts ? ` ${parts} ${partial + full === 1 ? 'rebuild' : 'rebuilds'} cost ${agentHours(rebuildS)} of agent time.` : '')
  if (rebuilding) text += ` ${integer(rebuilding)} of the ${integer(decided)} ${rebuilding === 1 ? 'was' : 'were'} still rebuilding at collection and count as misses.`
  if (pending) text += ` ${integer(pending)} had not reached 10 minutes yet and are not counted.`
  return text
})
const segments = computed<ProportionSegment[]>(() => [
  { key: 'reused', label: 'Reused', count: cache.value.reused, color: SERIES.muted },
  { key: 'partial', label: 'Partial rebuild (10 min–2 h) or still running', count: cache.value.partial + props.stats.rebuilding, color: SERIES.primary },
  { key: 'full', label: 'Full rebuild (2 h or more)', count: cache.value.full, color: SERIES.secondary },
])
</script>

<template>
  <Card class="gap-4 py-5" data-testid="docker-headline">
    <CardHeader class="px-4 sm:px-6">
      <CardDescription>Base image cache hit rate · {{ windowLabel.toLowerCase() }}</CardDescription>
    </CardHeader>
    <CardContent class="flex flex-col gap-4 px-4 sm:px-6">
      <template v-if="stats.decided">
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span class="text-5xl font-semibold tracking-tight tabular-nums" data-slot="kpi-value">{{ hitText(stats.hitRate) }}</span>
          <DeltaBadge v-if="delta" :delta="delta" />
          <span v-if="reference !== null" class="text-sm text-muted-foreground tabular-nums" data-slot="docker-reference">30-day rate {{ hitText(reference) }}</span>
        </div>
        <p class="max-w-prose text-sm" data-testid="docker-headline-story">{{ story }}</p>
        <ProportionBar :segments="segments" label="Base step runs by cache outcome" />
      </template>
      <template v-else>
        <span class="inline-flex w-fit items-center gap-2 rounded-md border border-dashed px-2.5 py-1.5 text-sm font-medium text-muted-foreground" data-slot="kpi-empty">
          <CircleDashed class="size-4 shrink-0" aria-hidden="true" />No finished base runs
        </span>
        <p class="text-sm text-muted-foreground" data-testid="docker-headline-story">No {{ BASE_STEP }} run finished in this window.</p>
      </template>
      <p v-if="footnote" class="text-xs text-muted-foreground" data-slot="kpi-footnote">{{ footnote }}</p>
    </CardContent>
  </Card>
</template>
