<script setup lang="ts">
import type { Detail } from '@/components/evidence/detail'
import BarChart from '@/components/charts/BarChart.vue'
import { plainNumber, shortDay } from '@/components/charts/chart'
import type { AgentNodeHistory, AgentOptions, AgentRun } from '@/domain/agentHealth'
import { FAILURE_SERIES, nodeDayDetail } from './details'

// One node's signal failures per UTC day; a day opens that node's runs on it.
const props = defineProps<{ history: AgentNodeHistory; runs: AgentRun[]; options: AgentOptions }>()
const emit = defineEmits<{ drill: [Detail] }>()

const series = FAILURE_SERIES.filter((s) => props.history.cells.some((cell) => cell[s.key as 'signalHard' | 'signalSoft'] > 0))
const label = (cell: AgentNodeHistory['cells'][number]) => shortDay(Date.parse(`${cell.day}T00:00:00Z`))
</script>

<template>
  <BarChart
    :rows="history.cells"
    :category="label"
    :series="series.length ? series : FAILURE_SERIES.slice(0, 1)"
    :label="`Failures per day on ${history.node}`"
    :value-format="plainNumber"
    class="h-[180px]"
    legend
    inspect-placement="legend"
    @select="(cell: AgentNodeHistory['cells'][number]) => emit('drill', nodeDayDetail(history, cell.day, runs, options))"
  />
</template>
