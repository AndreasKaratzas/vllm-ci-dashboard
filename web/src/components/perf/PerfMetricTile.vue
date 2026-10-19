<script setup lang="ts">
import ToneBadge from '@/components/ops/ToneBadge.vue'
import type { Rec } from '@/domain/format'
import type { PerfTile } from '@/domain/perf'
import { cn } from '@/lib/utils'
import PerfLineChart from './PerfLineChart.vue'

const props = defineProps<{ tile: PerfTile }>()
defineEmits<{ open: []; point: [Rec] }>()

const ACCENT = { pass: 'border-t-success', hard: 'border-t-destructive' } as Record<string, string>
const accent = () => cn('border-t-2', ACCENT[props.tile.tone] ?? 'border-t-border')
</script>

<template>
  <article :class="cn('flex flex-col gap-2 rounded-xl border bg-card p-4 shadow-xs', accent())" data-testid="perf-tile" :data-tone="tile.tone">
    <div class="flex items-start justify-between gap-2">
      <h4 class="line-clamp-2 text-sm font-medium text-muted-foreground" :title="tile.label" data-testid="perf-tile-label">{{ tile.label }}</h4>
      <ToneBadge :tone="tile.delta.tone" :title="tile.delta.title ?? undefined" class="shrink-0" data-testid="perf-tile-delta">{{ tile.delta.label }}</ToneBadge>
    </div>
    <div class="text-2xl font-semibold tabular-nums" data-testid="perf-tile-value">{{ tile.value }}</div>
    <div class="-mx-1 rounded-md hover:bg-muted/50" data-testid="perf-tile-spark">
      <PerfLineChart
        :series="tile.block.series || []"
        :status="tile.block.status"
        :unit="tile.block.unit"
        compact
        :height="48"
        @point="(point) => $emit('point', point)"
      />
    </div>
    <div class="flex items-center justify-between gap-2 text-xs">
      <span class="text-muted-foreground" data-testid="perf-tile-direction">{{ tile.direction }}</span>
      <button type="button" class="font-medium text-primary hover:underline" @click="$emit('open')">Inspect history</button>
    </div>
  </article>
</template>
