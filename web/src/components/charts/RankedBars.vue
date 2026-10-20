<script setup lang="ts">
import { ChevronRight } from '@lucide/vue'
import { computed } from 'vue'
import type { PaletteColor } from '@/lib/palette'
import { cn } from '@/lib/utils'
import { plainNumber } from './chart'

export interface RankedSegment {
  value: number
  color: PaletteColor
  label: string
}

export interface RankedItem {
  key: string
  label: string
  /** The full name when `label` is shortened. */
  title?: string
  value: number
  /** The value split into parts (e.g. hard and soft), drawn as one stacked bar; one part without it. */
  segments?: RankedSegment[]
  meta?: string
  /** Identity dot before the label (hardware), never a status. */
  dot?: PaletteColor
}

// A ranked list of labelled bars on one shared scale, worst first; long labels truncate instead of wrapping.
const props = withDefaults(
  defineProps<{
    items: RankedItem[]
    label: string
    color?: PaletteColor
    mono?: boolean
    valueFormat?: (value: number) => string
    /** What selecting an item opens, read out after its value. */
    action?: string
    onSelect?: (item: RankedItem) => void
  }>(),
  { valueFormat: plainNumber, action: 'open it' },
)

const max = computed(() => Math.max(1, ...props.items.map((item) => item.value)))
const parts = (item: RankedItem) => (item.segments ?? [{ value: item.value, color: props.color!, label: item.label }]).filter((s) => s.value > 0)
const readOut = (item: RankedItem) =>
  `${item.title ?? item.label}: ${props.valueFormat(item.value)}${item.segments ? ` (${item.segments.map((s) => `${props.valueFormat(s.value)} ${s.label}`).join(', ')})` : ''}${item.meta ? `, ${item.meta}` : ''}`
</script>

<template>
  <ul class="flex min-w-0 flex-col gap-0.5" :aria-label="label" data-slot="ranked-bars">
    <li v-for="item in items" :key="item.key">
      <component
        :is="onSelect ? 'button' : 'div'"
        :type="onSelect ? 'button' : undefined"
        :class="cn(
          'group/rank flex w-full flex-col gap-1.5 rounded-lg px-2.5 py-2 text-left',
          onSelect && 'transition-colors outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 active:bg-accent/80',
        )"
        :aria-label="onSelect ? `${readOut(item)}; ${action}` : undefined"
        :title="item.title ?? item.label"
        data-slot="ranked-item"
        @click="onSelect?.(item)"
      >
        <span class="flex w-full min-w-0 items-baseline gap-2">
          <span v-if="item.dot" class="size-2 shrink-0 self-center rounded-full" :style="{ backgroundColor: item.dot }" aria-hidden="true" />
          <span :class="cn('min-w-0 flex-1 truncate text-xs font-medium', mono && 'font-mono')">{{ item.label }}</span>
          <span class="shrink-0 text-sm font-semibold tabular-nums" :class="!item.value && 'text-muted-foreground'">{{ valueFormat(item.value) }}</span>
          <ChevronRight
            v-if="onSelect"
            class="size-3.5 shrink-0 self-center text-muted-foreground transition-transform group-hover/rank:translate-x-0.5 group-hover/rank:text-foreground"
            aria-hidden="true"
          />
        </span>
        <span class="flex h-1.5 w-full overflow-hidden rounded-full bg-muted" aria-hidden="true">
          <span class="flex h-full gap-px motion-safe:transition-[width] motion-safe:duration-500" :style="{ width: `${(item.value / max) * 100}%` }">
            <span v-for="part in parts(item)" :key="part.label" class="h-full first:rounded-l-full last:rounded-r-full" :style="{ flexGrow: part.value, backgroundColor: part.color }" />
          </span>
        </span>
        <span v-if="item.meta" class="truncate text-[11px] text-muted-foreground tabular-nums">{{ item.meta }}</span>
      </component>
    </li>
  </ul>
</template>
