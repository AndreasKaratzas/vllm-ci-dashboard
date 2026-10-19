<script setup lang="ts">
import type { ColumnDef } from '@tanstack/vue-table'
import { computed, h } from 'vue'
import TableSheet from '@/components/ops/TableSheet.vue'
import { historyPointLabel } from '@/domain/analytics'
import { shortDate, value } from '@/domain/format'

export interface EvidencePoint {
  label: string
  timestamp?: unknown
  url?: string | null
  valueSummary: string
}

// Classic openHistoryEvidence: chart points newest first, each linking its source or its drill-down.
const props = defineProps<{ title: string; points: EvidencePoint[] }>()
const emit = defineEmits<{ inspect: [index: number] }>()
const open = defineModel<boolean>('open', { required: true })

const rows = computed(() => props.points.map((point, index) => ({ point, index })).reverse())
type Row = (typeof rows.value)[number]
const inspect = (row: Row) => emit('inspect', row.index)
const columns: ColumnDef<Row, unknown>[] = [
  {
    id: 'observation',
    header: 'Observation',
    accessorFn: (row) => historyPointLabel(row.point),
    cell: ({ row }) =>
      row.original.point.url
        ? h('a', { href: row.original.point.url, target: '_blank', rel: 'noopener', class: 'font-mono hover:underline' }, historyPointLabel(row.original.point))
        : h('button', { type: 'button', class: 'text-left font-medium hover:underline', onClick: () => inspect(row.original) }, historyPointLabel(row.original.point)),
    enableSorting: false,
  },
  { id: 'observed', header: 'Observed', accessorFn: (row) => shortDate(row.point.timestamp), enableSorting: false },
  { id: 'value', header: 'Value', accessorFn: (row) => value(row.point.valueSummary), enableSorting: false },
  {
    id: 'evidence',
    header: 'Evidence',
    accessorFn: () => '',
    cell: ({ row }) =>
      row.original.point.url
        ? h('a', { href: row.original.point.url, target: '_blank', rel: 'noopener', class: 'hover:underline' }, 'Open source ↗')
        : h('button', { type: 'button', class: 'font-medium hover:underline', onClick: () => inspect(row.original) }, 'Inspect'),
    enableSorting: false,
  },
]
</script>

<template>
  <TableSheet
    v-model:open="open"
    :title="title"
    subtitle="Chart values and their retained source evidence"
    :columns="columns"
    :rows="rows"
    search-placeholder="Filter observation, value, date, or source"
  />
</template>
