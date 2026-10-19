<script setup lang="ts">
import type { PaletteColor } from '@/lib/palette'

export interface LegendItem {
  key: string
  label: string
  color: PaletteColor
}

defineProps<{ items: LegendItem[] }>()
</script>

<template>
  <ul class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground" data-slot="chart-legend">
    <li v-for="item in items" :key="item.key" class="flex items-center gap-1.5">
      <span class="size-2 shrink-0 rounded-[2px]" :style="{ backgroundColor: item.color }" aria-hidden="true" />
      {{ item.label }}
    </li>
  </ul>
</template>
