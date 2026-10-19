<script setup lang="ts">
import { computed } from 'vue'
import type { PaletteColor } from '@/lib/palette'
import { cn } from '@/lib/utils'
import { plainNumber } from './chart'

export interface ProportionSegment {
  key: string
  label: string
  count: number
  color: PaletteColor
}

const props = withDefaults(
  defineProps<{
    segments: ProportionSegment[]
    label: string
    /** Denominator when the segments do not cover the whole (the remainder stays empty track). */
    max?: number
    legend?: boolean
    valueFormat?: (value: number) => string
    onSelect?: (segment: ProportionSegment) => void
  }>(),
  { legend: true, valueFormat: plainNumber },
)

const total = computed(() => Math.max(1, props.max ?? props.segments.reduce((sum, s) => sum + Math.max(0, s.count), 0)))
const filled = computed(() => props.segments.filter((s) => s.count > 0))
const summary = computed(() => `${props.label}: ${props.segments.map((s) => `${s.label} ${props.valueFormat(s.count)}`).join(', ')}`)
</script>

<template>
  <div class="flex min-w-0 flex-col gap-3" data-slot="proportion-bar">
    <div class="flex h-2.5 w-full gap-0.5 overflow-hidden rounded-full bg-muted" role="img" :aria-label="summary">
      <span
        v-for="segment in filled"
        :key="segment.key"
        class="h-full first:rounded-l-full last:rounded-r-full motion-safe:transition-[width] motion-safe:duration-500"
        :style="{ width: `${(segment.count / total) * 100}%`, backgroundColor: segment.color }"
        :title="`${segment.label}: ${valueFormat(segment.count)}`"
      />
    </div>
    <ul v-if="legend" class="flex flex-wrap gap-x-1 gap-y-1 text-sm">
      <li v-for="segment in segments" :key="segment.key">
        <component
          :is="onSelect ? 'button' : 'div'"
          :type="onSelect ? 'button' : undefined"
          :class="cn('flex items-center gap-1.5 rounded-md px-2 py-1', onSelect && 'transition-colors hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none')"
          @click="onSelect?.(segment)"
        >
          <span class="size-2 shrink-0 rounded-full" :class="segment.count === 0 && 'opacity-40'" :style="{ backgroundColor: segment.color }" aria-hidden="true" />
          <span class="font-medium tabular-nums" :class="segment.count === 0 && 'text-muted-foreground'">{{ valueFormat(segment.count) }}</span>
          <span class="text-muted-foreground">{{ segment.label }}</span>
        </component>
      </li>
    </ul>
  </div>
</template>
