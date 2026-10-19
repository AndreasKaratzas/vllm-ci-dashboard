<script setup lang="ts">
import type { ColumnDef } from '@tanstack/vue-table'
import { h } from 'vue'
import DataTable from '@/components/ops/DataTable.vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { integer, type Tone } from '@/domain/format'
import { perfHardwareLabel, perfLatestBuildLabel, type PerfModelRow, type PerfView } from '@/domain/perf'

defineProps<{ rows: PerfModelRow[] }>()
const emit = defineEmits<{ select: [string, PerfView | undefined] }>()

const linkButton = (label: string, onClick: () => void, cls = '') =>
  h('button', { type: 'button', class: `font-medium hover:underline ${cls}`, onClick }, label)
const outLink = (label: string, url: unknown, child?: ReturnType<typeof h>) =>
  url
    ? h('a', { href: String(url), target: '_blank', rel: 'noopener', class: 'font-mono hover:underline' }, child ?? label)
    : child ?? h('span', { class: 'font-mono text-muted-foreground' }, label)
const badge = (label: string, url: unknown, tone: Tone) => outLink(label, url, h(ToneBadge, { tone }, () => label))

const columns: ColumnDef<PerfModelRow, unknown>[] = [
  {
    id: 'model',
    header: 'Model',
    accessorFn: (row) => row.model.model,
    cell: ({ row }) => linkButton(row.original.model.model, () => emit('select', row.original.model.model, undefined), 'text-left'),
    enableHiding: false,
  },
  { id: 'hardware', header: 'Hardware', accessorFn: (row) => perfHardwareLabel(row.model) },
  {
    id: 'nightlies',
    header: 'Nightlies',
    accessorFn: (row) => Number(row.model.nightly_count || 0),
    cell: ({ row }) => linkButton(integer(row.original.model.nightly_count), () => emit('select', row.original.model.model, undefined), 'tabular-nums'),
  },
  {
    id: 'performance',
    header: 'Performance metrics',
    accessorFn: (row) => row.performanceMetrics,
    cell: ({ row }) => linkButton(integer(row.original.performanceMetrics), () => emit('select', row.original.model.model, 'performance'), 'tabular-nums'),
  },
  {
    id: 'accuracy',
    header: 'Accuracy metrics',
    accessorFn: (row) => row.accuracyMetrics,
    cell: ({ row }) => linkButton(integer(row.original.accuracyMetrics), () => emit('select', row.original.model.model, 'accuracy'), 'tabular-nums'),
  },
  {
    id: 'regressions',
    header: 'Regressions',
    accessorFn: (row) => row.regressions,
    cell: ({ row }) => badge(integer(row.original.regressions), (row.original.model.latest || {}).build_url, row.original.regressions ? 'hard' : 'pass'),
  },
  {
    id: 'improvements',
    header: 'Improvements',
    accessorFn: (row) => row.improvements,
    cell: ({ row }) => badge(integer(row.original.improvements), (row.original.model.latest || {}).build_url, row.original.improvements ? 'pass' : 'neutral'),
  },
  {
    id: 'latest',
    header: 'Latest build',
    accessorFn: (row) => Number((row.model.latest || {}).build_number || 0),
    cell: ({ row }) => outLink(perfLatestBuildLabel(row.original.model), (row.original.model.latest || {}).build_url),
  },
]
</script>

<template>
  <Card data-testid="perf-overview">
    <CardHeader>
      <CardTitle>AMD model overview</CardTitle>
      <CardDescription>
        {{ integer(rows.length) }} retained model families · select a model to open its detailed performance or accuracy histories
      </CardDescription>
    </CardHeader>
    <CardContent>
      <DataTable :columns="columns" :data="rows" :page-size="25" search-placeholder="Filter model or hardware" empty-text="No model matches the selected filters." />
    </CardContent>
  </Card>
</template>
