<script setup lang="ts">
import { StackedBar } from '@unovis/ts'
import { VisArea, VisAxis, VisLine, VisStackedBar, VisXYContainer } from '@unovis/vue'
import { computed, ref } from 'vue'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ChartContainer,
  ChartCrosshair,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  componentToString,
  type ChartConfig,
} from '@/components/ui/chart'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import type { NightlyCounts } from '@/domain/home'

const props = defineProps<{ series: NightlyCounts[] }>()
const emit = defineEmits<{ select: [number] }>()

type Metric = 'failures' | 'passRate'
const metric = ref<Metric>('failures')

const FAILURE_CONFIG = {
  hard: { label: 'Hard failures', color: 'var(--destructive)' },
  soft: { label: 'Soft failures', color: 'var(--warning)' },
} satisfies ChartConfig
const PASS_CONFIG = {
  passRate: { label: 'Pass rate %', color: 'var(--chart-2)' },
} satisfies ChartConfig
const config = computed<ChartConfig>(() => (metric.value === 'failures' ? FAILURE_CONFIG : PASS_CONFIG))
const svgDefs = `
  <linearGradient id="fillPassRate" x1="0" y1="0" x2="0" y2="1">
    <stop offset="5%" stop-color="var(--chart-2)" stop-opacity="0.8" />
    <stop offset="95%" stop-color="var(--chart-2)" stop-opacity="0.1" />
  </linearGradient>
`

const rows = computed(() =>
  props.series.map((row, index) => ({
    index,
    number: row.number,
    hard: row.hard,
    soft: row.soft,
    fixed: row.fixed ?? 0,
    passRate: Math.round(row.passRate * 1000) / 10,
  })),
)
type Row = (typeof rows.value)[number]
const tooltip = computed(() =>
  componentToString(config.value, ChartTooltipContent, {
    labelFormatter: (index: number | Date) => `Nightly #${rows.value[Number(index)]?.number ?? ''}`,
  }),
)
</script>

<template>
  <Card>
    <CardHeader>
      <CardTitle>AMD nightly trend</CardTitle>
      <CardDescription>
        {{ series.length }} retained nightlies with test signal · click a bar for its evidence
      </CardDescription>
      <CardAction>
        <ToggleGroup v-model="metric" type="single" variant="outline" size="sm">
          <ToggleGroupItem value="failures">Failures</ToggleGroupItem>
          <ToggleGroupItem value="passRate">Pass rate</ToggleGroupItem>
        </ToggleGroup>
      </CardAction>
    </CardHeader>
    <CardContent class="px-2 sm:px-6">
      <p v-if="!rows.length" class="py-12 text-center text-sm text-muted-foreground">No nightly with test signal is retained in this snapshot.</p>
      <ChartContainer v-else :config="config" class="aspect-auto h-[260px] w-full" cursor>
        <VisXYContainer
          :key="metric"
          :data="rows"
          :svg-defs="svgDefs"
          :margin="{ left: 8, right: 8 }"
          :x-domain="[-0.5, rows.length - 0.5]"
          :y-domain="metric === 'passRate' ? [0, 100] : undefined"
        >
          <template v-if="metric === 'failures'">
            <VisStackedBar
              :x="(d: Row) => d.index"
              :y="[(d: Row) => d.hard, (d: Row) => d.soft]"
              :color="[FAILURE_CONFIG.hard.color, FAILURE_CONFIG.soft.color]"
              :rounded-corners="4"
              :bar-max-width="40"
              :events="{ [StackedBar.selectors.bar]: { click: (d: Row) => emit('select', d.number) } }"
            />
          </template>
          <template v-else>
            <VisArea :x="(d: Row) => d.index" :y="(d: Row) => d.passRate" color="url(#fillPassRate)" :opacity="0.6" />
            <VisLine :x="(d: Row) => d.index" :y="(d: Row) => d.passRate" :color="PASS_CONFIG.passRate.color" :line-width="2" />
          </template>
          <VisAxis
            type="x"
            :x="(d: Row) => d.index"
            :tick-line="false"
            :domain-line="false"
            :grid-line="false"
            :num-ticks="rows.length"
            :tick-values="rows.map((r) => r.index)"
            :tick-format="(i: number) => (rows[i] ? `#${rows[i].number}` : '')"
          />
          <VisAxis type="y" :num-ticks="4" :tick-line="false" :domain-line="false" />
          <ChartTooltip />
          <ChartCrosshair
            :template="tooltip"
            :color="metric === 'failures' ? [FAILURE_CONFIG.hard.color, FAILURE_CONFIG.soft.color] : [PASS_CONFIG.passRate.color]"
          />
        </VisXYContainer>
        <ChartLegendContent />
      </ChartContainer>
    </CardContent>
  </Card>
</template>
