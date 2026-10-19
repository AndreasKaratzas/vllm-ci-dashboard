<script setup lang="ts">
import { StackedBar } from '@unovis/ts'
import { VisAxis, VisStackedBar, VisTooltip, VisXYContainer } from '@unovis/vue'
import { computed } from 'vue'
import {
  ChartContainer,
  ChartCrosshair,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  componentToString,
  type ChartConfig,
} from '@/components/ui/chart'

export interface ChartSeries {
  key: string
  label: string
  color: string
}
export type ChartRow = { label: string } & Record<string, number | string>

// Stacked bars (vertical by build, or horizontal by category); a bar click selects its row.
const props = withDefaults(
  defineProps<{
    rows: ChartRow[]
    series: ChartSeries[]
    horizontal?: boolean
    height?: number
    valueLabel?: string
    absolute?: boolean
  }>(),
  { horizontal: false, height: 260, valueLabel: '', absolute: false },
)
const emit = defineEmits<{ select: [index: number] }>()

const config = computed<ChartConfig>(() => Object.fromEntries(props.series.map((s) => [s.key, { label: s.label, color: s.color }])))
const data = computed(() => props.rows.map((row, index) => ({ ...row, index }) as ChartRow & { index: number }))
type Row = (typeof data.value)[number]
const accessors = computed(() => props.series.map((s) => (d: Row) => Number(d[s.key] ?? 0)))
const colors = computed(() => props.series.map((s) => s.color))
const tooltip = computed(() =>
  componentToString(config.value, ChartTooltipContent, {
    labelFormatter: (index: number | Date) => props.rows[Number(index)]?.label ?? '',
  }),
)
const barTooltip = computed(() => ({
  [StackedBar.selectors.bar]: (d: Row) => tooltip.value?.(d, d.index) ?? '',
}))
const categoryDomain = computed<[number, number]>(() => [-0.5, Math.max(0, props.rows.length - 1) + 0.5])
const format = (v: number) => `${props.absolute ? Math.abs(v) : v}`
const tickLabel = (i: number) => {
  const label = props.rows[i]?.label ?? ''
  return label.length > 34 ? `${label.slice(0, 31)}...` : label
}
</script>

<template>
  <ChartContainer :config="config" class="aspect-auto w-full" :style="{ height: `${height}px` }" cursor>
    <VisXYContainer
      v-if="!horizontal"
      :data="data"
      :x-domain="categoryDomain"
      :margin="{ left: 8, right: 8 }"
    >
      <VisStackedBar
        :x="(d: Row) => d.index"
        :y="accessors"
        :color="colors"
        :rounded-corners="3"
        :bar-max-width="40"
        :events="{ [StackedBar.selectors.bar]: { click: (d: Row) => emit('select', d.index) } }"
      />
      <VisAxis
        type="x"
        :x="(d: Row) => d.index"
        :tick-line="false"
        :domain-line="false"
        :grid-line="false"
        :tick-values="data.map((r) => r.index)"
        :tick-format="tickLabel"
      />
      <VisAxis type="y" :num-ticks="4" :tick-line="false" :domain-line="false" :tick-format="format" :label="valueLabel || undefined" />
      <ChartTooltip />
      <ChartCrosshair :template="tooltip" :color="colors" />
    </VisXYContainer>
    <VisXYContainer v-else :data="data" :y-domain="categoryDomain" y-direction="south" :margin="{ left: 8, right: 16 }">
      <VisStackedBar
        orientation="horizontal"
        :x="(d: Row) => d.index"
        :y="accessors"
        :color="colors"
        :rounded-corners="3"
        :bar-max-width="28"
        :events="{ [StackedBar.selectors.bar]: { click: (d: Row) => emit('select', d.index) } }"
      />
      <VisAxis
        type="y"
        :tick-line="false"
        :domain-line="false"
        :grid-line="false"
        :tick-values="data.map((r) => r.index)"
        :tick-format="tickLabel"
      />
      <VisAxis type="x" :num-ticks="5" :tick-line="false" :domain-line="false" :tick-format="format" :label="valueLabel || undefined" />
      <VisTooltip :triggers="barTooltip" />
    </VisXYContainer>
    <ChartLegendContent />
  </ChartContainer>
</template>
