<script setup lang="ts">
import { StackedBar } from '@unovis/ts'
import { VisAxis, VisStackedBar, VisXYContainer } from '@unovis/vue'
import { computed } from 'vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ChartContainer,
  ChartCrosshair,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  componentToString,
  type ChartConfig,
} from '@/components/ui/chart'
import type { Rec } from '@/domain/format'
import { nightlyFailureCount } from '@/domain/nightly'

const props = defineProps<{ builds: Rec[]; latestSignal: unknown }>()
const emit = defineEmits<{ select: [Rec] }>()

const config = {
  new: { label: 'New failure', color: 'var(--destructive)' },
  recurring: { label: 'Recurring failure', color: 'var(--warning)' },
  fixed: { label: 'Fixed', color: 'var(--success)' },
} satisfies ChartConfig

// Fixes are drawn below zero so failures and recoveries read as one movement.
const rows = computed(() =>
  props.builds.map((build, index) => ({
    index,
    build,
    new: nightlyFailureCount(build, 'new') ?? 0,
    recurring: nightlyFailureCount(build, 'recurring') ?? 0,
    fixed: -(nightlyFailureCount(build, 'fixed') ?? 0),
  })),
)
type Row = (typeof rows.value)[number]
const tooltip = computed(() =>
  componentToString(config, ChartTooltipContent, {
    labelFormatter: (index: number | Date) => `Nightly #${rows.value[Number(index)]?.build.number ?? ''}`,
  }),
)
</script>

<template>
  <Card>
    <CardHeader>
      <CardTitle>Nightly failure movement</CardTitle>
      <CardDescription>
        New and recurring failures are above zero; fixes are below. Latest signal #{{ latestSignal ?? '-' }} · click a bar for its evidence.
      </CardDescription>
    </CardHeader>
    <CardContent class="px-2 sm:px-6">
      <p v-if="!rows.length" class="py-12 text-center text-sm text-muted-foreground">No nightly with comparable failure movement is retained.</p>
      <ChartContainer v-else :config="config" class="aspect-auto h-[260px] w-full" cursor>
        <VisXYContainer :data="rows" :x-domain="[-0.5, rows.length - 0.5]" :margin="{ left: 8, right: 8 }">
          <VisStackedBar
            :x="(d: Row) => d.index"
            :y="[(d: Row) => d.new, (d: Row) => d.recurring, (d: Row) => d.fixed]"
            :color="[config.new.color, config.recurring.color, config.fixed.color]"
            :rounded-corners="3"
            :bar-max-width="36"
            :events="{ [StackedBar.selectors.bar]: { click: (d: Row) => emit('select', d.build) } }"
          />
          <VisAxis
            type="x"
            :x="(d: Row) => d.index"
            :tick-line="false"
            :domain-line="false"
            :grid-line="false"
            :tick-values="rows.map((r) => r.index)"
            :tick-format="(i: number) => (rows[i] ? `#${rows[i].build.number}` : '')"
          />
          <VisAxis type="y" :num-ticks="4" :tick-line="false" :domain-line="false" :tick-format="(v: number) => `${Math.abs(v)}`" />
          <ChartTooltip />
          <ChartCrosshair :template="tooltip" :color="[config.new.color, config.recurring.color, config.fixed.color]" />
        </VisXYContainer>
        <ChartLegendContent />
      </ChartContainer>
    </CardContent>
  </Card>
</template>
