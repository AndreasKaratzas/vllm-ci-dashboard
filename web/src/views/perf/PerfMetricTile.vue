<script setup lang="ts">
import { computed } from 'vue'
import DeltaBadge from '@/components/stat/DeltaBadge.vue'
import Sparkline from '@/components/stat/Sparkline.vue'
import { perfSparkRows, type PerfTile } from '@/domain/perf'
import type { PaletteColor } from '@/lib/palette'
import { blockDelta, deltaText } from './details'

const props = defineProps<{ tile: PerfTile; color: PaletteColor; selected: boolean }>()
defineEmits<{ select: [] }>()

const values = computed(() => perfSparkRows(props.tile.block.series).map((point) => Number(point.value)))
const STATE: Partial<Record<string, string>> = { hard: 'regressed', pass: 'improved' }
const name = computed(
  () => `${props.tile.label}: ${props.tile.value}, ${deltaText(props.tile.delta.label)}${STATE[props.tile.tone] ? ` ${STATE[props.tile.tone]}` : ''}. ${props.tile.direction}.`,
)
</script>

<template>
  <button
    type="button"
    :aria-pressed="selected"
    :aria-label="name"
    class="group/tile flex min-w-0 flex-col gap-2 rounded-lg border bg-card p-3 text-left transition-[border-color,box-shadow,background-color] duration-150 outline-none hover:border-foreground/20 hover:shadow-sm focus-visible:ring-3 focus-visible:ring-ring/50 aria-pressed:border-brand/60 aria-pressed:bg-linear-to-t aria-pressed:from-brand/8 aria-pressed:to-card aria-pressed:shadow-sm motion-safe:active:scale-[.99]"
    data-testid="perf-tile"
    :data-tone="tile.tone"
    @click="$emit('select')"
  >
    <span class="line-clamp-2 min-h-8 text-xs font-medium text-muted-foreground group-hover/tile:text-foreground" :title="tile.label" data-testid="perf-tile-label">{{ tile.label }}</span>
    <span class="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
      <span class="text-xl font-semibold tabular-nums" data-testid="perf-tile-value">{{ tile.value }}</span>
      <DeltaBadge :delta="blockDelta(tile.block)" data-testid="perf-tile-delta" />
    </span>
    <Sparkline v-if="values.length > 1" :values="values" :label="`${tile.label} trend`" :color="color" class="h-8!" />
    <span class="text-xs text-muted-foreground" data-testid="perf-tile-direction">{{ tile.direction }}</span>
  </button>
</template>
