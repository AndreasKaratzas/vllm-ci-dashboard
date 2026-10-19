<script setup lang="ts">
import { ChevronRight } from '@lucide/vue'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { DnsBarSegment, DnsNodeBar, DnsQueueCard } from '@/domain/dns'
import { hardwareColor, statusVar, type PaletteColor } from '@/lib/palette'

defineProps<{ card: DnsQueueCard }>()
defineEmits<{ open: [DnsNodeBar]; queue: [] }>()

// Final job outcomes are job state; an unresolved outcome is drawn as unknown.
const SEGMENT: Record<DnsBarSegment['kind'], PaletteColor> = {
  passed: statusVar('pass'),
  soft: statusVar('soft'),
  hard: statusVar('hard'),
  observed: statusVar('unknown'),
}
</script>

<template>
  <Card class="gap-3 py-4" data-testid="dns-queue-card" :data-queue="card.queue.queue">
    <CardHeader class="px-4">
      <CardTitle class="flex min-w-0 items-center gap-2 font-mono text-sm">
        <span class="size-2 shrink-0 rounded-full" :style="{ backgroundColor: hardwareColor(card.queue.queue) }" aria-hidden="true" />
        <button
          type="button"
          class="truncate rounded-sm text-left underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
          :aria-label="`${card.queue.queue}: open the queue's nodes`"
          @click="$emit('queue')"
        >{{ card.queue.queue }}</button>
      </CardTitle>
      <CardAction class="flex flex-wrap justify-end gap-1.5 text-xs text-muted-foreground tabular-nums" data-testid="dns-queue-stats">
        <span v-for="stat in card.stats" :key="stat" class="rounded-md border px-1.5 py-0.5">{{ stat }}</span>
      </CardAction>
    </CardHeader>
    <CardContent class="flex max-h-[26rem] flex-col gap-0.5 overflow-y-auto px-2">
      <button
        v-for="bar in card.bars"
        :key="bar.node.node"
        type="button"
        class="group/bar grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)_3.25rem_1rem] items-center gap-3 rounded-md px-2 py-1.5 text-left transition-colors outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 active:bg-accent/80"
        :aria-label="bar.ariaLabel"
        data-testid="dns-node-bar"
        @click="$emit('open', bar)"
      >
        <span class="flex min-w-0 flex-col">
          <span class="truncate font-mono text-xs font-medium" data-testid="dns-node-name">{{ bar.node.node }}</span>
          <span class="truncate text-[11px] text-muted-foreground" data-testid="dns-node-meta" :title="bar.meta">{{ bar.meta }}</span>
        </span>
        <span class="h-2 overflow-hidden rounded-full bg-muted">
          <span class="flex h-full gap-px overflow-hidden rounded-full motion-safe:transition-[width] motion-safe:duration-300" :style="{ width: `${bar.fillPct}%` }">
            <span
              v-for="(segment, index) in bar.segments"
              :key="index"
              class="h-full"
              :data-segment="segment.kind"
              :style="{ backgroundColor: SEGMENT[segment.kind], width: segment.widthPct === null ? '100%' : `${segment.widthPct}%` }"
            />
          </span>
        </span>
        <strong class="text-right text-sm font-semibold tabular-nums" data-testid="dns-node-count">{{ bar.renderedCount }}</strong>
        <ChevronRight class="size-4 text-muted-foreground opacity-0 transition-[opacity,transform] group-hover/bar:translate-x-0.5 group-hover/bar:opacity-100 group-focus-visible/bar:opacity-100" aria-hidden="true" />
      </button>
    </CardContent>
  </Card>
</template>
