<script setup lang="ts">
import { FlaskConical } from '@lucide/vue'
import { computed } from 'vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import DataTable from '@/components/table/DataTable.vue'
import { link, mono, text, type ColumnSpec } from '@/components/table/columns'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { integer, type Rec } from '@/domain/format'
import { accuracyBlock, accuracyStatusLabel, perfDelta, perfLatestBuildLabel, perfShortModel, perfStatusTone, perfValue } from '@/domain/perf'
import { SERIES } from '@/lib/palette'
import PerfDeltaBadge from './PerfDeltaBadge.vue'
import PerfSeriesChart from './PerfSeriesChart.vue'

type Row = { model: Rec; task: Rec }
const props = defineProps<{ rows: Row[] }>()
const emit = defineEmits<{ history: [Row]; point: [Rec, unknown] }>()

// One chart per task while they fit on a screen; the table always lists every task.
const charted = computed(() => (props.rows.length <= 6 ? props.rows : []))

const columns: ColumnSpec<Row>[] = [
  { id: 'model', label: 'Model', value: (row) => row.model.model, cell: (row) => text(row.model.model), hideable: false },
  { id: 'task', label: 'Task', value: (row) => String(row.task.task ?? ''), cell: (row) => text(row.task.task) },
  { id: 'metric', label: 'Metric', value: (row) => String(row.task.metric ?? ''), cell: (row) => mono(row.task.metric) },
  { id: 'latest', label: 'Latest', numeric: true, value: (row) => Number(row.task.latest), cell: (row) => text(perfValue(row.task.latest, 'ratio')) },
  { id: 'delta', label: 'vs previous', numeric: true, value: (row) => Number(row.task.delta_pct), cell: (row) => text(perfDelta(row.task).label) },
  {
    id: 'status',
    label: 'Status',
    value: (row) => accuracyStatusLabel(row.task.status),
    cell: (row) => ({ text: accuracyStatusLabel(row.task.status), tone: perfStatusTone(row.task.status) }),
  },
  {
    id: 'build',
    label: 'Latest build',
    value: (row) => Number((row.model.latest || {}).build_number || 0),
    cell: (row) => link(perfLatestBuildLabel(row.model), (row.model.latest || {}).build_url, true),
  },
  { id: 'history', label: 'History', numeric: true, value: (row) => (row.task.series || []).length, cell: (row) => text(`${integer((row.task.series || []).length)} points`) },
]
</script>

<template>
  <div class="flex min-w-0 flex-col gap-4 md:gap-6">
    <EmptyState
      v-if="!rows.length"
      :icon="FlaskConical"
      title="No accuracy observations"
      :description="`${integer(0)} AMD accuracy task metrics. No matching observations.`"
    />
    <div v-if="charted.length" class="grid grid-cols-1 items-start gap-4 md:gap-6 @4xl/main:grid-cols-2 *:min-w-0">
      <Card v-for="(row, index) in charted" :key="index" class="gap-4" data-testid="perf-accuracy-chart">
        <CardHeader>
          <CardTitle class="text-base">{{ row.task.task }} · <span class="font-mono text-sm font-medium">{{ row.task.metric }}</span></CardTitle>
          <CardDescription class="flex flex-wrap items-center gap-2">
            {{ perfShortModel(row.model.model) }} · latest
            <span class="font-semibold text-foreground tabular-nums">{{ perfValue(row.task.latest, 'ratio') }}</span>
          </CardDescription>
          <CardAction><PerfDeltaBadge :delta="perfDelta(row.task)" /></CardAction>
        </CardHeader>
        <CardContent class="px-2 sm:px-6">
          <PerfSeriesChart
            :series="row.task.series || []"
            unit="ratio"
            :label="accuracyBlock(row.task).label"
            :color="SERIES.primary"
            class="h-[180px]"
            @select="(point) => emit('point', point, 'ratio')"
          />
        </CardContent>
      </Card>
    </div>
    <Card v-if="rows.length" data-testid="perf-accuracy">
      <CardHeader>
        <CardTitle>Accuracy observations</CardTitle>
        <CardDescription>lm-eval tasks; higher is better unless the source payload says otherwise · {{ integer(rows.length) }} AMD accuracy task metrics</CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          :columns="columns"
          :rows="rows"
          :page-size="25"
          search-placeholder="Filter model, task, or metric"
          :empty-text="`${integer(rows.length)} AMD accuracy task metrics. No matching observations.`"
          label="Accuracy observations"
          :on-row-click="(row: Row) => emit('history', row)"
        />
      </CardContent>
    </Card>
  </div>
</template>
