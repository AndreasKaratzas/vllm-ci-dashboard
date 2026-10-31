<script setup lang="ts">
import { computed } from 'vue'
import type { Detail } from '@/components/evidence/detail'
import BarChart from '@/components/charts/BarChart.vue'
import { plainNumber, shortDay } from '@/components/charts/chart'
import type { AgentNodeHistory, AgentOptions, AgentRun } from '@/domain/agentHealth'
import { STATE_SERIES, nodeDayDetail } from './details'

// One node's signal failures per UTC day; a day opens that node's runs on it.
const props = defineProps<{ history: AgentNodeHistory; runs: AgentRun[]; options: AgentOptions }>()
const emit = defineEmits<{ drill: [Detail] }>()

type DayRow = { day: string; hard: number; soft: number }
const rows = computed<DayRow[]>(() => props.history.cells.map((cell) => ({ day: cell.day, hard: cell.signalHard, soft: cell.signalSoft })))
const series = computed(() => {
  const used = STATE_SERIES.filter((s) => rows.value.some((row) => row[s.key as 'hard' | 'soft'] > 0))
  return used.length ? used : STATE_SERIES.slice(0, 1)
})
const label = (row: DayRow) => shortDay(Date.parse(`${row.day}T00:00:00Z`))
</script>

<template>
  <BarChart
    :rows="rows"
    :category="label"
    :series="series"
    :label="`Failures per day on ${history.node}`"
    :value-format="plainNumber"
    class="h-[180px]"
    legend
    inspect-placement="legend"
    @select="(row: DayRow) => emit('drill', nodeDayDetail(history, row.day, runs, options))"
  />
</template>
