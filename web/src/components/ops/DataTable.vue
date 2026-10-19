<script setup lang="ts" generic="T">
import {
  FlexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useVueTable,
  type ColumnDef,
  type SortingState,
  type VisibilityState,
} from '@tanstack/vue-table'
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, Columns3 } from '@lucide/vue'
import { ref } from 'vue'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

const props = withDefaults(
  defineProps<{
    columns: ColumnDef<T, unknown>[]
    data: T[]
    searchPlaceholder?: string
    pageSize?: number
    emptyText?: string
  }>(),
  { searchPlaceholder: 'Filter…', pageSize: 10, emptyText: 'No rows.' },
)

const sorting = ref<SortingState>([])
const visibility = ref<VisibilityState>({})
const globalFilter = ref('')

const table = useVueTable({
  get data() {
    return props.data
  },
  get columns() {
    return props.columns
  },
  state: {
    get sorting() {
      return sorting.value
    },
    get columnVisibility() {
      return visibility.value
    },
    get globalFilter() {
      return globalFilter.value
    },
  },
  initialState: { pagination: { pageSize: props.pageSize } },
  onSortingChange: (updater) => (sorting.value = typeof updater === 'function' ? updater(sorting.value) : updater),
  onColumnVisibilityChange: (updater) =>
    (visibility.value = typeof updater === 'function' ? updater(visibility.value) : updater),
  onGlobalFilterChange: (value) => (globalFilter.value = String(value ?? '')),
  getCoreRowModel: getCoreRowModel(),
  getSortedRowModel: getSortedRowModel(),
  getFilteredRowModel: getFilteredRowModel(),
  getPaginationRowModel: getPaginationRowModel(),
})
</script>

<template>
  <div class="flex flex-col gap-3">
    <div class="flex items-center gap-2">
      <Input v-model="globalFilter" :placeholder="searchPlaceholder" class="h-8 max-w-sm" />
      <slot name="toolbar" />
      <DropdownMenu>
        <DropdownMenuTrigger as-child>
          <Button variant="outline" size="sm" class="ml-auto"><Columns3 /> <span class="hidden lg:inline">Columns</span></Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuCheckboxItem
            v-for="column in table.getAllColumns().filter((c) => c.getCanHide())"
            :key="column.id"
            :model-value="column.getIsVisible()"
            @update:model-value="(v) => column.toggleVisibility(!!v)"
          >
            {{ typeof column.columnDef.header === 'string' ? column.columnDef.header : column.id }}
          </DropdownMenuCheckboxItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
    <div class="overflow-hidden rounded-lg border">
      <Table>
        <TableHeader class="sticky top-0 bg-muted">
          <TableRow v-for="group in table.getHeaderGroups()" :key="group.id">
            <TableHead v-for="header in group.headers" :key="header.id">
              <button
                v-if="header.column.getCanSort()"
                type="button"
                class="inline-flex items-center gap-1 hover:text-foreground"
                @click="header.column.toggleSorting(header.column.getIsSorted() === 'asc')"
              >
                <FlexRender :render="header.column.columnDef.header" :props="header.getContext()" />
                <ArrowUp v-if="header.column.getIsSorted() === 'asc'" class="size-3.5" />
                <ArrowDown v-else-if="header.column.getIsSorted() === 'desc'" class="size-3.5" />
                <ArrowUpDown v-else class="size-3.5 opacity-40" />
              </button>
              <FlexRender v-else :render="header.column.columnDef.header" :props="header.getContext()" />
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow v-for="row in table.getRowModel().rows" :key="row.id">
            <TableCell v-for="cell in row.getVisibleCells()" :key="cell.id" class="whitespace-normal">
              <FlexRender :render="cell.column.columnDef.cell" :props="cell.getContext()" />
            </TableCell>
          </TableRow>
          <TableRow v-if="!table.getRowModel().rows.length">
            <TableCell :colspan="table.getVisibleLeafColumns().length" class="h-20 text-center text-muted-foreground">
              {{ emptyText }}
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </div>
    <div class="flex items-center justify-between text-sm text-muted-foreground">
      <span>{{ table.getFilteredRowModel().rows.length }} rows</span>
      <div class="flex items-center gap-2">
        <span>Page {{ table.getState().pagination.pageIndex + 1 }} of {{ Math.max(1, table.getPageCount()) }}</span>
        <Button variant="outline" size="icon" class="size-8" :disabled="!table.getCanPreviousPage()" aria-label="Previous page" @click="table.previousPage()">
          <ChevronLeft />
        </Button>
        <Button variant="outline" size="icon" class="size-8" :disabled="!table.getCanNextPage()" aria-label="Next page" @click="table.nextPage()">
          <ChevronRight />
        </Button>
      </div>
    </div>
  </div>
</template>
