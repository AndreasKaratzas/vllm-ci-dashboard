<script setup lang="ts">
import { computed } from 'vue'
import BarChart from '@/components/charts/BarChart.vue'
import type { SeriesSpec } from '@/components/charts/chart'
import EmptyState from '@/components/feedback/EmptyState.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { Rec } from '@/domain/format'
import { nightlyFailureCount } from '@/domain/nightly'
import { statusVar } from '@/lib/palette'

const props = defineProps<{ builds: Rec[]; latestSignal: unknown }>()
const emit = defineEmits<{ select: [Rec] }>()

interface Row extends Rec {
  build: Rec
  new: number
  recurring: number
  fixed: number
}

const rows = computed<Row[]>(() =>
  props.builds.map((build) => ({
    build,
    new: nightlyFailureCount(build, 'new') ?? 0,
    recurring: nightlyFailureCount(build, 'recurring') ?? 0,
    fixed: nightlyFailureCount(build, 'fixed') ?? 0,
  })),
)

const ALL_SERIES: SeriesSpec[] = [
  { key: 'new', label: 'New failure', color: statusVar('hard') },
  { key: 'recurring', label: 'Recurring failure', color: statusVar('soft') },
  { key: 'fixed', label: 'Fixed', color: statusVar('pass') },
]
// Only movements that happened are drawn and listed in the legend.
const series = computed(() => ALL_SERIES.filter((s) => rows.value.some((row) => Number(row[s.key]) > 0)))
const totals = computed(() => Object.fromEntries(ALL_SERIES.map((s) => [s.key, rows.value.reduce((sum, row) => sum + Number(row[s.key]), 0)])))
</script>

<template>
  <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="movement-chart">
    <CardHeader>
      <CardTitle as="h2">Nightly failure movement</CardTitle>
      <CardDescription>
        {{ rows.length }} retained nightlies · {{ totals.new }} new, {{ totals.recurring }} recurring, {{ totals.fixed }} fixed · fixes are drawn below zero
      </CardDescription>
    </CardHeader>
    <CardContent class="px-2 sm:px-6">
      <EmptyState v-if="!rows.length || !series.length" title="No failure movement in the retained nightlies" :description="`Latest signal #${latestSignal ?? '—'}`" />
      <BarChart
        v-else
        :rows="rows"
        :category="(row: Row) => `#${row.build.number}`"
        :series="series"
        :diverging="['fixed']"
        label="Nightly failure movement"
        :on-select="(row: Row) => emit('select', row.build)"
      />
    </CardContent>
  </Card>
</template>
