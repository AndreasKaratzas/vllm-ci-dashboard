<script setup lang="ts" generic="T extends ChartRow">
import { VisArea, VisAxis, VisLine, VisXYContainer } from '@unovis/vue'
import { useId } from 'reka-ui'
import { computed, ref } from 'vue'
import { ChartContainer, ChartCrosshair, ChartTooltip } from '@/components/ui/chart'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { cn } from '@/lib/utils'
import { chartConfig, plainNumber, seriesValue, shortDay, thinTicks, useSeriesInteraction, useTickCount, type ChartRow, type SeriesSpec } from './chart'
import ChartInspect from './ChartInspect.vue'
import Legend from './Legend.vue'

export interface RangeOption {
  /** Number of trailing points to show; 0 shows every point. */
  value: number
  label: string
}

const props = withDefaults(
  defineProps<{
    rows: T[]
    x: (row: T) => number | Date
    series: SeriesSpec[]
    label: string
    rowLabel?: (row: T) => string
    yDomain?: [number, number]
    yFormat?: (value: number) => string
    ranges?: RangeOption[]
    hint?: string
    class?: string
    onSelect?: (row: T, index: number) => void
  }>(),
  { yFormat: plainNumber },
)
const range = defineModel<number>('range', { default: 0 })

const host = ref<HTMLElement | null>(null)
const ticks = useTickCount(host)
const uid = useId().replace(/[^a-zA-Z0-9-_]/g, '')
const gradientId = (key: string) => `${uid}-${key}`

const visible = computed(() => (range.value > 0 ? props.rows.slice(-range.value) : props.rows))
const xValue = (row: T) => Number(props.x(row))
const titleOf = (row: T) => (props.rowLabel ? props.rowLabel(row) : shortDay(xValue(row)))
const svgDefs = computed(() =>
  props.series
    .map(
      (s) =>
        `<linearGradient id="${gradientId(s.key)}" x1="0" y1="0" x2="0" y2="1">` +
        `<stop offset="5%" style="stop-color:${s.color};stop-opacity:0.5" />` +
        `<stop offset="95%" style="stop-color:${s.color};stop-opacity:0.05" /></linearGradient>`,
    )
    .join(''),
)
const rangeText = computed(() => props.ranges?.find((r) => r.value === range.value)?.label ?? '')
const rangeModel = computed({
  get: () => String(range.value),
  set: (value: string) => {
    if (value) range.value = Number(value)
  },
})

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
  <div class="flex min-w-0 flex-col gap-2" data-slot="area-chart">
    <div v-if="ranges?.length || onSelect" class="flex items-center justify-end gap-2">
      <template v-if="ranges?.length">
        <ToggleGroup v-model="rangeModel" type="single" variant="outline" size="sm" class="hidden sm:flex" :aria-label="`${label} range`">
          <ToggleGroupItem v-for="option in ranges" :key="option.value" :value="String(option.value)">{{ option.label }}</ToggleGroupItem>
        </ToggleGroup>
        <Select v-model="rangeModel">
          <SelectTrigger size="sm" class="w-36 sm:hidden" :aria-label="`${label} range`">
            <SelectValue>{{ rangeText }}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem v-for="option in ranges" :key="option.value" :value="String(option.value)">{{ option.label }}</SelectItem>
          </SelectContent>
        </Select>
      </template>
      <ChartInspect v-if="onSelect" :items="inspectItems(visible)" :label="label" @select="(i) => select(visible[i])" />
    </div>
    <div ref="host" role="img" :aria-label="label" :class="onSelect && 'cursor-pointer'" @click="selectHovered">
      <ChartContainer :config="chartConfig(series)" :class="cn('aspect-auto h-[250px] w-full', props.class)" cursor>
        <VisXYContainer :data="visible" :svg-defs="svgDefs" :margin="{ left: 4, right: 12, top: 8 }" :y-domain="yDomain">
          <VisArea
            v-for="s in series"
            :key="`area-${s.key}`"
            :x="xValue"
            :y="(row: T) => seriesValue(row, s.key)"
            :color="`url(#${gradientId(s.key)})`"
            curve-type="monotoneX"
            :duration="300"
          />
          <VisLine
            v-for="s in series"
            :key="`line-${s.key}`"
            :x="xValue"
            :y="(row: T) => seriesValue(row, s.key)"
            :color="s.color"
            :line-width="2"
            curve-type="monotoneX"
            :duration="300"
          />
          <VisAxis type="x" :x="xValue" :tick-values="thinTicks(visible.map(xValue), ticks)" :tick-format="shortDay" :tick-line="false" :domain-line="false" :grid-line="false" />
          <VisAxis type="y" :num-ticks="4" :tick-format="yFormat" :tick-line="false" :domain-line="false" />
          <ChartTooltip />
          <ChartCrosshair :template="tooltip" :on-crosshair-move="track" :color="series.map((s) => s.color)" />
        </VisXYContainer>
      </ChartContainer>
    </div>
    <Legend v-if="series.length > 1" :items="series" />
  </div>
</template>
