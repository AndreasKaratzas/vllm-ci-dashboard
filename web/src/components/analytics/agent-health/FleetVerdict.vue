<script setup lang="ts">
import { computed } from 'vue'
import BarChart from '@/components/charts/BarChart.vue'
import StatusBadge from '@/components/feedback/StatusBadge.vue'
import Sparkline from '@/components/stat/Sparkline.vue'
import { Card } from '@/components/ui/card'
import { signalNoun, type AgentFleetVerdict, type AgentNodeDayMatrix, type AgentNodeHistory, type AgentSignal } from '@/domain/agentHealth'
import { integer } from '@/domain/format'
import { cn } from '@/lib/utils'
import { STATE_SERIES } from './details'

// The page's answer first: is the fleet healthy, and which nodes are hurting CI right now.
const props = defineProps<{
  verdict: AgentFleetVerdict
  matrix: AgentNodeDayMatrix
  signal: AgentSignal
  windowId: string
  /** Fleet signal failures per day of the window, oldest first. */
  trend: number[]
  span: string
}>()
defineEmits<{ node: [AgentNodeHistory] }>()

const NODES = 4
// Hurting, then uncorroborated node-suspect nodes count their recent days; without either, the window's worst nodes count the whole window.
const recentNodes = computed(() => (props.verdict.hotNodes.length ? props.verdict.hotNodes : props.verdict.watchNodes))
type NodeRow = { node: AgentNodeHistory; hard: number; soft: number }
const nodes = computed<NodeRow[]>(() =>
  (recentNodes.value.length ? recentNodes.value : props.matrix.nodes).slice(0, NODES).map((node) => {
    const cells = recentNodes.value.length ? node.cells.slice(-props.matrix.recentDays) : node.cells
    return { node, hard: cells.reduce((sum, cell) => sum + cell.signalHard, 0), soft: cells.reduce((sum, cell) => sum + cell.signalSoft, 0) }
  }),
)
const series = computed(() => STATE_SERIES.filter((s) => nodes.value.some((row) => row[s.key as 'hard' | 'soft'] > 0)))
const nodesTitle = computed(() =>
  props.verdict.hotNodes.length ? 'Failing jobs themselves' : props.verdict.watchNodes.length ? 'Node-suspect, uncorroborated' : `Worst nodes in ${props.windowId}`,
)
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
          <Sparkline :values="trend" :label="`Fleet ${signalNoun(signal)} per day, ${span}`" />
          <span class="flex justify-between text-[11px] text-muted-foreground"><span>Fleet {{ signalNoun(signal) }} per day</span><span>{{ span }}</span></span>
        </div>
      </div>

      <section class="flex min-w-0 flex-col gap-2" aria-labelledby="agent-hot-title" data-testid="agent-hot-nodes">
        <h3 id="agent-hot-title" class="text-xs font-medium text-muted-foreground">{{ nodesTitle }}</h3>
        <p v-if="!nodes.length" class="text-sm text-muted-foreground">No node failed in this window.</p>
        <BarChart
          v-else
          :rows="nodes"
          :category="(row: NodeRow) => row.node.node_raw"
          :series="series"
          orientation="horizontal"
          :category-width="150"
          :label="`${nodesTitle}: ${signalNoun(signal)} per node`"
          :value-format="integer"
          legend
          inspect-placement="legend"
          hint="Click for the node"
          @select="(row: NodeRow) => $emit('node', row.node)"
        />
      </section>
    </div>
  </Card>
</template>
