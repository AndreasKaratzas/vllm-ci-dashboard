<script setup lang="ts">
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { DnsNodeBar, DnsQueueCard } from '@/domain/dns'

defineProps<{ card: DnsQueueCard }>()
defineEmits<{ open: [DnsNodeBar] }>()

const SEGMENT: Record<string, string> = {
  passed: 'bg-success',
  soft: 'bg-warning',
  hard: 'bg-destructive',
  observed: 'bg-info/70 w-full',
}
</script>

<template>
  <Card class="gap-3 py-4" data-testid="dns-queue-card" :data-queue="card.queue.queue">
    <CardHeader class="px-4">
      <CardTitle class="font-mono text-sm">{{ card.queue.queue }}</CardTitle>
      <CardAction class="flex gap-2 text-xs text-muted-foreground tabular-nums" data-testid="dns-queue-stats">
        <span v-for="stat in card.stats" :key="stat" class="rounded-md bg-muted px-1.5 py-0.5">{{ stat }}</span>
      </CardAction>
    </CardHeader>
    <CardContent class="flex max-h-[30rem] flex-col gap-1 overflow-y-auto px-2">
      <button
        v-for="bar in card.bars"
        :key="bar.node.node"
        type="button"
        class="grid grid-cols-[minmax(0,11rem)_minmax(0,1fr)_3.5rem] items-center gap-3 rounded-md px-2 py-1.5 text-left hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        :aria-label="bar.ariaLabel"
        data-testid="dns-node-bar"
        @click="$emit('open', bar)"
      >
        <span class="flex min-w-0 flex-col">
          <span class="truncate font-mono text-xs font-medium" data-testid="dns-node-name">{{ bar.node.node }}</span>
          <span class="truncate text-[11px] text-muted-foreground" data-testid="dns-node-meta" :title="bar.meta">{{ bar.meta }}</span>
        </span>
        <span class="h-2.5 overflow-hidden rounded-full bg-muted">
          <span class="flex h-full overflow-hidden rounded-full" :style="{ width: `${bar.fillPct}%` }">
            <span
              v-for="(segment, index) in bar.segments"
              :key="index"
              :class="SEGMENT[segment.kind]"
              :data-segment="segment.kind"
              :style="segment.widthPct === null ? undefined : { width: `${segment.widthPct}%` }"
            />
          </span>
        </span>
        <strong class="text-right text-sm tabular-nums" data-testid="dns-node-count">{{ bar.renderedCount }}</strong>
      </button>
    </CardContent>
  </Card>
</template>
