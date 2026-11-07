<script setup lang="ts">
import { Donut } from '@unovis/ts'
import { VisDonut, VisSingleContainer, VisTooltip } from '@unovis/vue'
import { computed } from 'vue'
import { ChartContainer } from '@/components/ui/chart'
import type { PaletteColor } from '@/lib/palette'
import { cn } from '@/lib/utils'
import { chartConfig, plainNumber } from './chart'
import { tooltipHtml } from './tooltip'

export interface DonutSlice {
  key: string
  label: string
  value: number
  color: PaletteColor
}

const props = withDefaults(
  defineProps<{
    slices: DonutSlice[]
    label: string
    centerValue?: string
    centerLabel?: string
    valueFormat?: (value: number) => string
    /** 'sm' is the compact ring for KPI cards. */
    size?: 'sm' | 'md'
    legend?: boolean
    onSelect?: (slice: DonutSlice) => void
  }>(),
  { valueFormat: plainNumber, size: 'md', legend: true },
)

const total = computed(() => props.slices.reduce((sum, s) => sum + Math.max(0, s.value), 0))
const share = (slice: DonutSlice) => (total.value ? `${Math.round((Math.max(0, slice.value) / total.value) * 100)}%` : '0%')
const config = computed(() => chartConfig(props.slices))
const triggers = {
  [Donut.selectors.segment]: (d: { data: DonutSlice }) =>
    tooltipHtml({ title: d.data.label, rows: [{ label: share(d.data), value: props.valueFormat(d.data.value), color: d.data.color }] }),
}
const events = computed(() => ({ [Donut.selectors.segment]: { click: (d: { data: DonutSlice }) => props.onSelect?.(d.data) } }))
</script>

<template>
  <div :class="cn('min-w-0', legend ? '@container/donut w-full' : 'shrink-0')" data-slot="donut">
    <div :class="cn('flex min-w-0 flex-col items-center', size === 'sm' ? 'gap-3 @[13rem]/donut:flex-row' : 'gap-4 @[22rem]/donut:flex-row')">
      <div role="img" :aria-label="label" :class="cn('shrink-0', size === 'sm' ? 'size-20' : 'size-40', onSelect && '[&_path]:cursor-pointer')">
        <ChartContainer :config="config" class="aspect-square size-full">
          <VisSingleContainer :data="slices">
            <VisDonut
              :value="(d: DonutSlice) => Math.max(0, d.value)"
              :color="(d: DonutSlice) => d.color"
              :arc-width="size === 'sm' ? 10 : 22"
              :pad-angle="0.02"
              :corner-radius="4"
              :central-label="centerValue"
              :central-sub-label="centerLabel"
              :duration="300"
              :events="events"
            />
            <VisTooltip :triggers="triggers" />
          </VisSingleContainer>
        </ChartContainer>
      </div>
      <ul v-if="legend" class="flex w-full min-w-0 flex-col gap-1 text-sm">
        <li v-for="slice in slices" :key="slice.key" data-slot="donut-legend-item">
          <component
            :is="onSelect ? 'button' : 'div'"
            :type="onSelect ? 'button' : undefined"
            :class="cn('flex w-full items-center gap-2 rounded-md px-2 py-1 text-left', onSelect && 'transition-colors hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none')"
            @click="onSelect?.(slice)"
          >
            <span class="size-2.5 shrink-0 rounded-[2px]" :class="slice.value === 0 && 'opacity-40'" :style="{ backgroundColor: slice.color }" aria-hidden="true" />
            <span class="min-w-0 flex-1 truncate" data-slot="legend-label">{{ slice.label }}</span>
            <span class="font-medium tabular-nums" :class="slice.value === 0 && 'text-muted-foreground'" data-slot="legend-value">{{ valueFormat(slice.value) }}</span>
            <span v-if="size === 'md'" class="w-10 text-right text-xs text-muted-foreground tabular-nums">{{ share(slice) }}</span>
          </component>
        </li>
      </ul>
    </div>
  </div>
</template>
