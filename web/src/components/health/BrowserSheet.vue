<script setup lang="ts" generic="T">
// Classic openTableBrowser as a Sheet: normalized substring search over a
// per-row search text, optional select filters, and 50-row pages. DataTable's
// per-column global filter cannot express classic's joined search text.
import { FlexRender, getCoreRowModel, useVueTable, type ColumnDef } from '@tanstack/vue-table'
import { ChevronLeft, ChevronRight } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { integer } from '@/domain/format'
import { browseRows } from '@/domain/healthHardware'

export interface BrowserFilter<R> {
  label: string
  options: { value: string; label: string }[]
  initial: string
  predicate: (row: R, selected: string) => boolean
}

const props = withDefaults(
  defineProps<{
    title: string
    subtitle?: string
    rows: T[]
    columns: ColumnDef<T, unknown>[]
    searchText: (row: T) => string
    searchPlaceholder?: string
    filters?: BrowserFilter<T>[]
    pageSize?: number
    wide?: boolean
  }>(),
  { searchPlaceholder: 'Filter evidence', filters: () => [], pageSize: 50, wide: true },
)
const open = defineModel<boolean>('open', { required: true })

const query = ref('')
const selected = ref<string[]>([])
const page = ref(0)
watch(
  () => [open.value, props.rows, props.filters] as const,
  ([isOpen]) => {
    if (!isOpen) return
    query.value = ''
    selected.value = props.filters.map((f) => f.initial || 'all')
    page.value = 0
  },
  { immediate: true },
)
watch([query, selected], () => (page.value = 0), { deep: true })

const filtered = computed(() =>
  browseRows(
    props.rows,
    query.value,
    props.searchText,
    props.filters.map((f, i) => ({ selected: selected.value[i] ?? 'all', predicate: f.predicate })),
  ),
)
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / props.pageSize)))
const visible = computed(() => {
  const current = Math.max(0, Math.min(page.value, pageCount.value - 1))
  return filtered.value.slice(current * props.pageSize, current * props.pageSize + props.pageSize)
})

const table = useVueTable({
  get data() {
    return visible.value
  },
  get columns() {
    return props.columns
  },
  getCoreRowModel: getCoreRowModel(),
})
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto" :class="wide ? 'sm:max-w-6xl' : 'sm:max-w-4xl'">
      <SheetHeader class="px-6 pr-12">
        <SheetTitle>{{ title }}</SheetTitle>
        <SheetDescription v-if="subtitle">{{ subtitle }}</SheetDescription>
      </SheetHeader>
      <div class="flex flex-col gap-3 px-6 pb-6">
        <div class="flex flex-wrap items-center gap-2">
          <Input v-model="query" type="search" :placeholder="searchPlaceholder" :aria-label="searchPlaceholder" class="h-8 max-w-sm" />
          <Select v-for="(filter, index) in filters" :key="filter.label" v-model="selected[index]">
            <SelectTrigger size="sm" :aria-label="filter.label" data-testid="browser-filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem v-for="option in filter.options" :key="option.value" :value="option.value">{{ option.label }}</SelectItem>
            </SelectContent>
          </Select>
          <span class="ml-auto text-sm text-muted-foreground tabular-nums" data-testid="browser-count">
            {{ integer(filtered.length) }} of {{ integer(rows.length) }} rows
          </span>
        </div>
        <div class="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader class="sticky top-0 bg-muted">
              <TableRow v-for="group in table.getHeaderGroups()" :key="group.id">
                <TableHead v-for="header in group.headers" :key="header.id">
                  <FlexRender :render="header.column.columnDef.header" :props="header.getContext()" />
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-for="row in table.getRowModel().rows" :key="row.id">
                <TableCell v-for="cell in row.getVisibleCells()" :key="cell.id" class="whitespace-normal">
                  <FlexRender :render="cell.column.columnDef.cell" :props="cell.getContext()" />
                </TableCell>
              </TableRow>
              <TableRow v-if="!visible.length">
                <TableCell :colspan="columns.length" class="h-20 text-center text-muted-foreground">No matching evidence</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
        <div v-if="filtered.length > pageSize" class="flex items-center justify-end gap-2 text-sm text-muted-foreground">
          <span>Page {{ integer(Math.min(page, pageCount - 1) + 1) }} of {{ integer(pageCount) }}</span>
          <Button variant="outline" size="icon" class="size-8" :disabled="page === 0" aria-label="Previous page" @click="page -= 1"><ChevronLeft /></Button>
          <Button variant="outline" size="icon" class="size-8" :disabled="page >= pageCount - 1" aria-label="Next page" @click="page += 1"><ChevronRight /></Button>
        </div>
        <slot />
      </div>
    </SheetContent>
  </Sheet>
</template>
