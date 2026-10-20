<script setup lang="ts" generic="T extends ChartRow">
import { VisAxis, VisLine, VisScatter, VisXYContainer } from '@unovis/vue'
import { ref } from 'vue'
import { ChartContainer, ChartCrosshair, ChartTooltip } from '@/components/ui/chart'
import { cn } from '@/lib/utils'
import { chartConfig, niceTicks, plainNumber, seriesValue, shortDay, thinTicks, useSeriesInteraction, useTickCount, type ChartRow, type SeriesSpec } from './chart'
import ChartInspect from './ChartInspect.vue'
import Legend from './Legend.vue'

const props = withDefaults(
  defineProps<{
    rows: T[]
    x: (row: T) => number | Date
    series: SeriesSpec[]
    label: string
    xFormat?: (value: number) => string
    rowLabel?: (row: T) => string
    yDomain?: [number, number]
    yFormat?: (value: number) => string
    /** Explicit y-axis ticks, e.g. a zoomed rate axis; defaults to round ticks over the domain. */
    yTicks?: number[]
    compact?: boolean
    hint?: string
    class?: string
    onSelect?: (row: T, index: number) => void
  }>(),
  { xFormat: shortDay, yFormat: plainNumber },
)

const host = ref<HTMLElement | null>(null)
const ticks = useTickCount(host)
const xValue = (row: T) => Number(props.x(row))
const titleOf = (row: T) => (props.rowLabel ? props.rowLabel(row) : props.xFormat(xValue(row)))

const { track, tooltip, select, selectHovered, inspectItems } = useSeriesInteraction<T>({
  rows: () => props.rows,
  series: () => props.series,
  title: titleOf,
  format: (value) => props.yFormat(value),
  hint: () => props.hint,
  onSelect: () => props.onSelect,
})
</script>

<template>
  <div class="flex min-w-0 flex-col gap-2" data-slot="line-chart">
    <div v-if="onSelect" class="flex justify-end">
      <ChartInspect :items="inspectItems(rows)" :label="label" @select="(i) => select(rows[i])" />
    </div>
    <div ref="host" role="img" :aria-label="label" :class="onSelect && 'cursor-pointer'" @click="selectHovered">
      <ChartContainer :config="chartConfig(series)" :class="cn('aspect-auto w-full', compact ? 'h-16' : 'h-[220px]', props.class)" cursor>
        <VisXYContainer :data="rows" :margin="compact ? { top: 4, bottom: 4, left: 4, right: 4 } : { left: 4, right: 12, top: 8 }" :y-domain="yDomain">
          <template v-for="s in series" :key="s.key">
            <VisLine :x="xValue" :y="(row: T) => seriesValue(row, s.key)" :color="s.color" :line-width="2" :line-dash-array="s.dash" curve-type="monotoneX" :duration="300" />
            <VisScatter v-if="!compact && rows.length <= 40" :x="xValue" :y="(row: T) => seriesValue(row, s.key)" :color="s.color" :size="6" stroke-color="var(--card)" :stroke-width="2" />
          </template>
          <template v-if="!compact">
            <VisAxis type="x" :x="xValue" :tick-values="thinTicks(rows.map(xValue), ticks)" :tick-format="xFormat" :tick-line="false" :domain-line="false" :grid-line="false" />
            <VisAxis type="y" :num-ticks="yDomain ? undefined : 4" :tick-values="yTicks ?? (yDomain ? niceTicks(yDomain[0], yDomain[1], 4) : undefined)" :tick-format="yFormat" :tick-line="false" :domain-line="false" />
          </template>
          <ChartTooltip />
          <ChartCrosshair
            :x="xValue"
            :y="series.map((s) => (row: T) => seriesValue(row, s.key))"
            :template="tooltip"
            :onCrosshairMove="track"
            :color="series.map((s) => s.color)"
          />
        </VisXYContainer>
      </ChartContainer>
    </div>
    <Legend v-if="series.length > 1 && !compact" :items="series" lines />
  </div>
</template>
