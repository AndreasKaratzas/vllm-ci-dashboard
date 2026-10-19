<script setup lang="ts">
import { Orientation, StackedBar } from '@unovis/ts'
import { VisAxis, VisStackedBar, VisTooltip, VisXYContainer } from '@unovis/vue'
import { computed } from 'vue'
import { ChartContainer, ChartLegendContent, type ChartConfig } from '@/components/ui/chart'
import { integer } from '@/domain/format'
import type { ChartBar } from '@/domain/omni'

const props = withDefaults(defineProps<{ bars: ChartBar[]; horizontal?: boolean; valueLabel: string; legend?: boolean }>(), {
  horizontal: false,
  legend: false,
})
const emit = defineEmits<{ select: [ChartBar] }>()

const config = {
  omni: { label: 'Active queue', color: 'var(--chart-2)' },
  retiring: { label: 'Retiring MI325 queue', color: 'var(--warning)' },
} satisfies ChartConfig

const rows = computed(() => props.bars.map((bar, index) => ({ index, bar })))
type Row = (typeof rows.value)[number]
const colorOf = (d: Row) => config[d.bar.color].color
const height = computed(() => (props.horizontal ? Math.max(160, rows.value.length * 34 + 48) : 280))
// Thin long bucket axes so labels stay readable.
const tickValues = computed(() => {
  const step = Math.max(1, Math.ceil(rows.value.length / (props.horizontal ? 40 : 8)))
  return rows.value.filter((r) => r.index % step === 0 || r.index === rows.value.length - 1).map((r) => r.index)
})
// Horizontal bars put categories on the y scale (Unovis StackedBar dataScale).
const categoryDomain = computed<[number, number]>(() => [-0.5, rows.value.length - 0.5])
const valueDomain = computed<[number, number]>(() => [0, Math.max(1, ...props.bars.map((b) => b.value))])
const escape = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
const tooltip = (d: Row) =>
  `<div class="text-xs"><div class="font-medium">${escape(d.bar.label)}</div><div>${integer(d.bar.value)} ${escape(props.valueLabel)}</div></div>`
</script>

<template>
  <ChartContainer :config="config" class="aspect-auto w-full" :style="{ height: `${height}px` }" cursor>
    <VisXYContainer
      :data="rows"
      :margin="{ left: 8, right: 16 }"
      :x-domain="horizontal ? valueDomain : categoryDomain"
      :y-domain="horizontal ? categoryDomain : valueDomain"
      :y-direction="horizontal ? 'south' : 'north'"
    >
      <VisStackedBar
        :x="(d: Row) => d.index"
        :y="[(d: Row) => d.bar.value]"
        :color="colorOf"
        :orientation="horizontal ? Orientation.Horizontal : Orientation.Vertical"
        :rounded-corners="4"
        :bar-max-width="horizontal ? 24 : 44"
        :events="{ [StackedBar.selectors.bar]: { click: (d: Row) => emit('select', d.bar) } }"
      />
      <VisAxis
        :type="horizontal ? 'y' : 'x'"
        :x="(d: Row) => d.index"
        :tick-line="false"
        :domain-line="false"
        :grid-line="false"
        :num-ticks="tickValues.length"
        :tick-values="tickValues"
        :tick-format="(i: number) => rows[i]?.bar.label ?? ''"
      />
      <VisAxis :type="horizontal ? 'x' : 'y'" :num-ticks="4" :tick-line="false" :domain-line="false" :label="valueLabel" />
      <VisTooltip :triggers="{ [StackedBar.selectors.bar]: tooltip }" />
    </VisXYContainer>
    <ChartLegendContent v-if="legend" />
  </ChartContainer>
</template>
