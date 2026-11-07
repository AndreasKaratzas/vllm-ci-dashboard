<script setup lang="ts">
import type { PaletteColor } from '@/lib/palette'

export interface LegendItem {
  key: string
  label: string
  color: PaletteColor
  /** A dashed line swatch instead of a square, matching a dashed series. */
  dash?: number[]
}

defineProps<{ items: LegendItem[]; lines?: boolean }>()
</script>

<template>
  <ul class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground" data-slot="chart-legend">
    <li v-for="item in items" :key="item.key" class="flex items-center gap-1.5">
      <svg v-if="lines" class="h-2 w-4 shrink-0" viewBox="0 0 16 8" aria-hidden="true">
        <line x1="0" y1="4" x2="16" y2="4" :stroke="item.color" stroke-width="2" :stroke-dasharray="item.dash?.join(' ')" />
      </svg>
      <span v-else class="size-2 shrink-0 rounded-[2px]" :style="{ backgroundColor: item.color }" aria-hidden="true" />
      {{ item.label }}
    </li>
  </ul>
</template>
