<script setup lang="ts">
import { ChevronRight } from '@lucide/vue'
import { computed } from 'vue'
import RankedBars, { type RankedItem } from '@/components/charts/RankedBars.vue'
import StatusBadge from '@/components/feedback/StatusBadge.vue'
import Sparkline from '@/components/stat/Sparkline.vue'
import { Card } from '@/components/ui/card'
import { signalNoun, type AgentFleetVerdict, type AgentLoad, type AgentNodeDayMatrix, type AgentNodeHistory, type AgentSignal } from '@/domain/agentHealth'
import { integer } from '@/domain/format'
import { hardwareColor, heatColor, heatLevel } from '@/lib/palette'
import { cn } from '@/lib/utils'
import { loadItems } from './details'

// The page's answer first: is the fleet healthy, and which nodes and queues are hurting CI right now.
const props = defineProps<{
  verdict: AgentFleetVerdict
  matrix: AgentNodeDayMatrix
  queues: AgentLoad[]
  signal: AgentSignal
  windowId: string
  /** Fleet signal failures per day of the window, oldest first. */
  trend: number[]
  span: string
}>()
defineEmits<{ node: [AgentNodeHistory]; queue: [AgentLoad] }>()

const STRIP = 7
const hot = computed(() => props.verdict.hotNodes.length > 0)
const nodes = computed(() => (hot.value ? props.verdict.hotNodes : props.matrix.nodes).slice(0, 4))
const queues = computed(() => props.queues.slice(0, 3))
const strip = (node: AgentNodeHistory) => node.cells.slice(-STRIP)
const GLOW = { hard: 'from-status-hard/12', soft: 'from-status-soft/12', pass: 'from-brand/6' } as const
</script>

<template>
  <Card
    :class="cn('relative gap-0 overflow-hidden bg-linear-to-br to-card to-60% p-0 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300', GLOW[verdict.tone])"
    data-testid="agent-verdict"
    :data-tone="verdict.tone"
  >
    <div class="grid gap-6 p-5 md:p-6 @4xl/main:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] @4xl/main:gap-8">
      <div class="flex min-w-0 flex-col gap-3">
        <div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <StatusBadge :tone="verdict.tone" :label="verdict.label" data-testid="agent-verdict-badge" />
          <span>Last {{ matrix.recentDays === 1 ? 'day' : `${matrix.recentDays} days` }} · {{ signalNoun(signal) }}</span>
        </div>
        <h2 class="text-xl font-semibold tracking-tight text-balance md:text-2xl" data-testid="agent-verdict-headline">{{ verdict.headline }}</h2>
        <p class="max-w-prose text-sm text-muted-foreground" data-testid="agent-verdict-detail">{{ verdict.detail }}</p>
        <dl class="mt-1 flex flex-wrap gap-x-8 gap-y-3">
          <div class="flex flex-col">
            <dt class="text-xs text-muted-foreground">Recent failures</dt>
            <dd class="text-2xl font-semibold tabular-nums" :class="!verdict.recentFailures && 'text-muted-foreground'">{{ integer(verdict.recentFailures) }}</dd>
          </div>
          <div class="flex flex-col">
            <dt class="text-xs text-muted-foreground">Nodes failing recently</dt>
            <dd class="text-2xl font-semibold tabular-nums" :class="!verdict.recentNodes && 'text-muted-foreground'">{{ integer(verdict.recentNodes) }}</dd>
          </div>
          <div v-if="verdict.topShare !== null" class="flex flex-col">
            <dt class="text-xs text-muted-foreground">Share on the 5 worst nodes</dt>
            <dd class="text-2xl font-semibold tabular-nums">{{ Math.round(verdict.topShare * 100) }}%</dd>
          </div>
        </dl>
        <div v-if="trend.some((v) => v > 0)" class="mt-auto flex max-w-md flex-col gap-1 pt-2" data-testid="agent-verdict-trend">
          <Sparkline :values="trend" :label="`Fleet ${signalNoun(signal)} per day, ${span}`" :color="heatColor(4)" />
          <span class="flex justify-between text-[11px] text-muted-foreground"><span>Fleet {{ signalNoun(signal) }} per day</span><span>{{ span }}</span></span>
        </div>
      </div>

      <div class="grid min-w-0 gap-5 sm:grid-cols-2 @4xl/main:grid-cols-1 @6xl/main:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <section class="flex min-w-0 flex-col gap-2" aria-labelledby="agent-hot-title">
          <h3 id="agent-hot-title" class="text-xs font-medium text-muted-foreground">{{ hot ? 'Hurting CI now' : `Worst nodes in ${windowId}` }}</h3>
          <p v-if="!nodes.length" class="text-sm text-muted-foreground">No node failed in this window.</p>
          <ul v-else class="flex flex-col gap-1">
            <li v-for="node in nodes" :key="node.node_raw">
              <button
                type="button"
                class="group/hot flex w-full items-center gap-2.5 rounded-lg border bg-card/60 px-2.5 py-2 text-left transition-colors outline-none hover:border-foreground/20 hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50"
                :aria-label="`${node.node}: ${integer(node.recent)} recent, ${integer(node.signal)} in ${windowId}; open the node`"
                data-testid="agent-hot-node"
                @click="$emit('node', node)"
              >
                <span class="size-2 shrink-0 rounded-full" :style="{ backgroundColor: hardwareColor(node.hardware) }" aria-hidden="true" />
                <span class="min-w-0 flex-1 truncate font-mono text-xs font-medium">{{ node.node_raw }}</span>
                <span class="hidden shrink-0 gap-[2px] sm:flex" aria-hidden="true">
                  <span v-for="cell in strip(node)" :key="cell.day" class="size-2.5 rounded-[2px]" :style="{ backgroundColor: heatColor(heatLevel(cell.signal, matrix.max)) }" />
                </span>
                <span class="w-8 shrink-0 text-right text-sm font-semibold tabular-nums">{{ integer(hot ? node.recent : node.signal) }}</span>
                <ChevronRight class="size-3.5 shrink-0 text-muted-foreground transition-transform group-hover/hot:translate-x-0.5 group-hover/hot:text-foreground" aria-hidden="true" />
              </button>
            </li>
          </ul>
        </section>
        <section class="flex min-w-0 flex-col gap-2" aria-labelledby="agent-queue-title">
          <h3 id="agent-queue-title" class="text-xs font-medium text-muted-foreground">Hardest-hit queues in {{ windowId }}</h3>
          <p v-if="!queues.length" class="text-sm text-muted-foreground">No retained failing runs.</p>
          <RankedBars
            v-else
            :items="loadItems(queues, 'queue')"
            label="Hardest-hit queues"
            mono
            action="open the queue"
            class="-mx-2.5"
            data-testid="agent-hot-queues"
            @select="(item: RankedItem) => $emit('queue', queues.find((q) => q.key === item.key)!)"
          />
        </section>
      </div>
    </div>
  </Card>
</template>
