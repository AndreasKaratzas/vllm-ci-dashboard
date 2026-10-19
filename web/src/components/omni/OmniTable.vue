<script setup lang="ts">
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import type { Rec } from '@/domain/format'
import type { DetailTable, OmniDetail } from '@/domain/omni'
import OmniCell from './OmniCell.vue'

// Compact tables that classic shows in full (no search or paging).
defineProps<{ table: DetailTable<Rec> }>()
const emit = defineEmits<{ open: [OmniDetail] }>()
</script>

<template>
  <div class="overflow-x-auto rounded-lg border">
    <Table>
      <TableHeader class="bg-muted">
        <TableRow>
          <TableHead v-for="column in table.columns" :key="column.label" :class="{ 'text-right': column.numeric }">{{ column.label }}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow v-for="(row, index) in table.rows" :key="index">
          <TableCell v-for="column in table.columns" :key="column.label" :class="{ 'text-right tabular-nums': column.numeric }">
            <OmniCell :cell="column.cell(row)" @open="(d) => emit('open', d)" />
          </TableCell>
        </TableRow>
      </TableBody>
    </Table>
  </div>
</template>
