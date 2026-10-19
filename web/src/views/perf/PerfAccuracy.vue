<script setup lang="ts">
import type { ColumnDef } from '@tanstack/vue-table'
import { h } from 'vue'
import DataTable from '@/components/ops/DataTable.vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { integer, type Rec } from '@/domain/format'
import { accuracyStatusLabel, perfDelta, perfLatestBuildLabel, perfStatusTone, perfValue } from '@/domain/perf'

type Row = { model: Rec; task: Rec }
defineProps<{ rows: Row[] }>()
const emit = defineEmits<{ history: [Row] }>()

const open = (row: Row, label: string, cls = '') =>
  h('button', { type: 'button', class: `text-left font-medium hover:underline ${cls}`, onClick: () => emit('history', row) }, label)
const buildUrl = (row: Row) => (row.model.latest || {}).build_url

const columns: ColumnDef<Row, unknown>[] = [
  { id: 'model', header: 'Model', accessorFn: (row) => row.model.model, cell: ({ row }) => open(row.original, row.original.model.model), enableHiding: false },
  { id: 'task', header: 'Task', accessorFn: (row) => String(row.task.task ?? ''), cell: ({ row }) => open(row.original, row.original.task.task) },
  { id: 'metric', header: 'Metric', accessorFn: (row) => String(row.task.metric ?? ''), cell: ({ row }) => open(row.original, row.original.task.metric, 'font-mono text-xs') },
  {
    id: 'latest',
    header: 'Latest',
    accessorFn: (row) => Number(row.task.latest),
    cell: ({ row }) => open(row.original, perfValue(row.original.task.latest, 'ratio'), 'tabular-nums'),
  },
  {
    id: 'delta',
    header: 'vs previous',
    accessorFn: (row) => Number(row.task.delta_pct),
    cell: ({ row }) => open(row.original, perfDelta(row.original.task).label, 'tabular-nums'),
  },
  {
    id: 'status',
    header: 'Status',
    accessorFn: (row) => accuracyStatusLabel(row.task.status),
    cell: ({ row }) => {
      const badge = h(ToneBadge, { tone: perfStatusTone(row.original.task.status) }, () => accuracyStatusLabel(row.original.task.status))
      const url = buildUrl(row.original)
      return url ? h('a', { href: url, target: '_blank', rel: 'noopener' }, badge) : badge
    },
  },
  {
    id: 'build',
    header: 'Latest build',
    accessorFn: (row) => Number((row.model.latest || {}).build_number || 0),
    cell: ({ row }) => {
      const url = buildUrl(row.original)
      const label = perfLatestBuildLabel(row.original.model)
      return url ? h('a', { href: url, target: '_blank', rel: 'noopener', class: 'font-mono hover:underline' }, label) : h('span', { class: 'font-mono text-muted-foreground' }, label)
    },
  },
  {
    id: 'history',
    header: 'History',
    accessorFn: (row) => (row.task.series || []).length,
    cell: ({ row }) => open(row.original, `${integer((row.original.task.series || []).length)} points`, 'text-primary'),
  },
]
</script>

<template>
  <Card data-testid="perf-accuracy">
    <CardHeader>
      <CardTitle>Accuracy observations</CardTitle>
      <CardDescription>lm-eval tasks; higher is better unless the source payload says otherwise · {{ integer(rows.length) }} AMD accuracy task metrics</CardDescription>
    </CardHeader>
    <CardContent>
      <DataTable :columns="columns" :data="rows" :page-size="25" search-placeholder="Filter model, task, or metric" :empty-text="`${integer(rows.length)} AMD accuracy task metrics. No matching observations.`" />
    </CardContent>
  </Card>
</template>
