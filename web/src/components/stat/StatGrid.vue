<script setup lang="ts">
import { cn } from '@/lib/utils'

const props = withDefaults(defineProps<{ cols?: 2 | 3 | 4 | 5 }>(), { cols: 4 })

const COLS = {
  2: '@xl/main:grid-cols-2',
  3: '@xl/main:grid-cols-3',
  4: '@xl/main:grid-cols-2 @5xl/main:grid-cols-4',
  5: '@xl/main:grid-cols-3 @5xl/main:grid-cols-5',
} as const
</script>

<template>
  <div :class="cn('grid grid-cols-2 gap-3 md:gap-4 *:min-w-0', COLS[props.cols])" data-slot="stat-grid">
    <slot />
  </div>
</template>
