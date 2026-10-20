<script setup lang="ts">
import { ChevronDown, ChevronRight } from '@lucide/vue'
import { computed, ref } from 'vue'
import { Card } from '@/components/ui/card'
import type { DnsBarSegment, DnsCoverage, DnsNodeBar, DnsQueueCard } from '@/domain/dns'
import { hardwareColor, statusVar, type PaletteColor } from '@/lib/palette'
import { nodeSummary, queueSummary } from './details'

const props = defineProps<{ card: DnsQueueCard; coverage: DnsCoverage; visible?: number }>()
defineEmits<{ open: [DnsNodeBar]; queue: [] }>()

// Final job outcomes are job state; an unresolved outcome is drawn as unknown.
const SEGMENT: Record<DnsBarSegment['kind'], PaletteColor> = {
  passed: statusVar('pass'),
  soft: statusVar('soft'),
  hard: statusVar('hard'),
  observed: statusVar('unknown'),
}
const expanded = ref(false)
const limit = computed(() => props.visible ?? 4)
const hidden = computed(() => Math.max(0, props.card.bars.length - limit.value))
const stats = computed(() => queueSummary(props.card, props.coverage))
const zero = (text: string) => /^0 /.test(text)
</script>

<template>
  <Card class="gap-3 py-4" data-testid="dns-queue-card" :data-queue="card.queue.queue">
    <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-4">
      <h3 class="flex min-w-0 items-center gap-2 font-mono text-sm font-semibold" data-slot="card-title">
        <span class="size-2 shrink-0 rounded-full" :style="{ backgroundColor: hardwareColor(card.queue.queue) }" aria-hidden="true" />
        <button
          type="button"
          class="min-w-0 truncate rounded-sm text-left underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
          :aria-label="`${card.queue.queue}: open the queue's nodes`"
          @click="$emit('queue')"
        >{{ card.queue.queue }}</button>
      </h3>
      <div class="flex flex-wrap gap-1.5 text-xs tabular-nums" data-testid="dns-queue-stats">
        <span v-for="stat in stats" :key="stat" class="rounded-md border px-1.5 py-0.5" :class="zero(stat) ? 'text-muted-foreground' : 'text-foreground'">{{ stat }}</span>
      </div>
    </div>
    <ul class="flex flex-col px-2">
      <li v-for="(bar, index) in card.bars" v-show="expanded || index < limit" :key="bar.node.node">
        <button
          type="button"
          class="group/bar flex w-full flex-col gap-1.5 rounded-md px-2 py-2 text-left transition-colors outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 active:bg-accent/80"
          :aria-label="bar.ariaLabel"
          data-testid="dns-node-bar"
          @click="$emit('open', bar)"
        >
          <span class="flex w-full min-w-0 items-baseline gap-2">
            <span class="min-w-0 flex-1 truncate font-mono text-xs font-medium" data-testid="dns-node-name">{{ bar.node.node }}</span>
            <strong class="text-sm font-semibold tabular-nums" data-testid="dns-node-count">{{ bar.renderedCount }}</strong>
            <ChevronRight class="size-3.5 shrink-0 self-center text-muted-foreground transition-transform group-hover/bar:translate-x-0.5 group-hover/bar:text-foreground" aria-hidden="true" />
          </span>
          <span class="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <span class="flex h-full gap-px overflow-hidden rounded-full motion-safe:transition-[width] motion-safe:duration-300" :style="{ width: `${bar.fillPct}%` }">
              <span
                v-for="(segment, i) in bar.segments"
                :key="i"
                class="h-full"
                :data-segment="segment.kind"
                :style="{ backgroundColor: SEGMENT[segment.kind], width: segment.widthPct === null ? '100%' : `${segment.widthPct}%` }"
              />
            </span>
          </span>
          <span class="flex flex-wrap gap-x-2 text-[11px] text-muted-foreground tabular-nums" data-testid="dns-node-meta">
            <span v-for="part in nodeSummary(bar, coverage)" :key="part" data-part>{{ part }}</span>
          </span>
        </button>
      </li>
    </ul>
    <button
      v-if="hidden"
      type="button"
      class="mx-4 inline-flex w-fit items-center gap-1 rounded-sm text-xs font-medium text-muted-foreground underline-offset-4 outline-none hover:text-foreground hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <ChevronDown class="size-3.5 transition-transform" :class="expanded && 'rotate-180'" aria-hidden="true" />
      {{ expanded ? 'Show fewer nodes' : `Show ${hidden} more ${hidden === 1 ? 'node' : 'nodes'}` }}
    </button>
  </Card>
</template>
