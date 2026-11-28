<script setup lang="ts" generic="T">
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, Columns3, Search } from '@lucide/vue'
import { computed, reactive, ref, watch } from 'vue'
import type { Detail } from '@/components/evidence/detail'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'
import Cell from './Cell.vue'
import type { CellSpec, ColumnSpec, FilterSpec } from './columns'

const ALL = '__all__'
const props = withDefaults(
  defineProps<{
    columns: ColumnSpec<T>[]
    rows: T[]
    searchText?: (row: T) => string
    filters?: FilterSpec<T>[]
    pageSize?: number | false
    searchable?: boolean
    searchPlaceholder?: string
    emptyText?: string
    label?: string
    /** Show the column visibility menu; defaults to on whenever a column is hideable. */
    columnMenu?: boolean
    /** Count rows as "shown of total" so filtered views keep the population visible. */
    showTotal?: boolean
    /** The ordering before any header is clicked. */
    defaultSort?: { id: string; desc: boolean }
    onRowClick?: (row: T) => void
  }>(),
  { filters: () => [], pageSize: 10, searchable: true, columnMenu: true, searchPlaceholder: 'Filter rows…', emptyText: 'No rows.' },
)
const emit = defineEmits<{ drill: [Detail] }>()

const query = ref('')
const sort = ref<{ id: string; desc: boolean } | null>(props.defaultSort ? { ...props.defaultSort } : null)
const hidden = ref(new Set<string>())
const page = ref(0)
const filterValues = reactive<Record<string, string>>(Object.fromEntries(props.filters.map((f) => [f.id, f.initial ?? ALL])))

const visibleColumns = computed(() => props.columns.filter((c) => !hidden.value.has(c.id)))
const hideable = computed(() => (props.columnMenu ? props.columns.filter((c, i) => (c.hideable ?? i > 0)) : []))

const filtered = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return props.rows.filter((row) => {
    for (const filter of props.filters) {
      const value = filterValues[filter.id]
      if (value && value !== ALL && !filter.predicate(row, value)) return false
    }
    if (!needle) return true
    const haystack = props.columns.map((c) => String(c.value(row))).join(' ') + (props.searchText ? ` ${props.searchText(row)}` : '')
    return haystack.toLowerCase().includes(needle)
  })
})

const sorted = computed(() => {
  const active = sort.value
  const column = active && props.columns.find((c) => c.id === active.id)
  if (!active || !column) return filtered.value
  const direction = active.desc ? -1 : 1
  return [...filtered.value].sort((a, b) => {
    if (column.compare) return column.compare(a, b) * direction
    const x = column.value(a)
    const y = column.value(b)
    const order = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), 'en', { numeric: true })
    return order * direction
  })
})

const pageCount = computed(() => (props.pageSize ? Math.max(1, Math.ceil(sorted.value.length / props.pageSize)) : 1))
const pageRows = computed(() => (props.pageSize ? sorted.value.slice(page.value * props.pageSize, (page.value + 1) * props.pageSize) : sorted.value))
watch([query, filterValues, () => props.rows], () => (page.value = 0))

function toggleSort(id: string) {
  const current = sort.value
  sort.value = current?.id !== id ? { id, desc: false } : current.desc ? null : { id, desc: true }
}
function ariaSort(id: string) {
  return sort.value?.id === id ? (sort.value.desc ? 'descending' : 'ascending') : 'none'
}
function setHidden(id: string, visible: boolean) {
  const next = new Set(hidden.value)
  if (visible) next.delete(id)
  else next.add(id)
  hidden.value = next
}
function cellOf(column: ColumnSpec<T>, row: T): CellSpec {
  return column.cell ? column.cell(row) : { text: String(column.value(row)) }
}
// Links and buttons inside a clickable row keep their own action.
function activate(row: T, event: Event) {
  const target = event.target as HTMLElement
  if (!props.onRowClick || (target !== event.currentTarget && target.closest('a, button'))) return
  event.preventDefault()
  props.onRowClick(row)
}
</script>

<template>
  <div class="flex min-w-0 flex-col gap-3" data-slot="data-table">
    <div v-if="searchable || filters.length || hideable.length || $slots.toolbar" class="flex flex-wrap items-center gap-2">
      <div v-if="searchable" class="relative min-w-40 flex-1 sm:max-w-xs">
        <Search class="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
        <Input v-model="query" :placeholder="searchPlaceholder" :aria-label="searchPlaceholder" class="h-8 pl-8" />
      </div>
      <Select v-for="filter in filters" :key="filter.id" v-model="filterValues[filter.id]">
        <SelectTrigger size="sm" class="min-w-32" :aria-label="filter.label">
          <SelectValue :placeholder="filter.label" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem :value="ALL">{{ filter.allLabel ?? `All ${filter.label.toLowerCase()}` }}</SelectItem>
          <SelectItem v-for="option in filter.options" :key="option.value" :value="option.value">{{ option.label }}</SelectItem>
        </SelectContent>
      </Select>
      <slot name="toolbar" />
      <DropdownMenu v-if="hideable.length">
        <DropdownMenuTrigger as-child>
          <Button variant="outline" size="sm" class="ml-auto"><Columns3 /> <span class="sr-only lg:not-sr-only">Columns</span></Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuCheckboxItem
            v-for="column in hideable"
            :key="column.id"
            :model-value="!hidden.has(column.id)"
            @select.prevent
            @update:model-value="(visible: boolean) => setHidden(column.id, visible)"
          >
            {{ column.label }}
          </DropdownMenuCheckboxItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>

    <div class="overflow-hidden rounded-lg border">
      <Table :aria-label="label">
        <TableHeader class="bg-muted">
          <TableRow class="hover:bg-transparent">
            <TableHead
              v-for="column in visibleColumns"
              :key="column.id"
              :aria-sort="column.sortable === false ? undefined : ariaSort(column.id)"
              :class="cn(column.numeric && 'text-right', column.class)"
            >
              <button
                v-if="column.sortable !== false"
                type="button"
                class="-mx-1 inline-flex items-center gap-1 rounded-sm px-1 transition-colors hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
                @click="toggleSort(column.id)"
              >
                {{ column.label }}
                <ArrowUp v-if="sort?.id === column.id && !sort.desc" class="size-3.5" aria-hidden="true" />
                <ArrowDown v-else-if="sort?.id === column.id" class="size-3.5" aria-hidden="true" />
                <ArrowUpDown v-else class="size-3.5 opacity-40" aria-hidden="true" />
              </button>
              <template v-else>{{ column.label }}</template>
            </TableHead>
            <TableHead v-if="onRowClick" class="w-8"><span class="sr-only">Open</span></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow
            v-for="(row, index) in pageRows"
            :key="index"
            :tabindex="onRowClick ? 0 : undefined"
            :class="cn(onRowClick ? 'group/row cursor-pointer focus-visible:bg-muted/50 focus-visible:outline-none active:bg-accent/80' : 'hover:bg-transparent')"
            :data-clickable="onRowClick ? '' : undefined"
            @click="(e: MouseEvent) => activate(row, e)"
            @keydown.enter="(e: KeyboardEvent) => activate(row, e)"
            @keydown.space="(e: KeyboardEvent) => activate(row, e)"
          >
            <TableCell v-for="column in visibleColumns" :key="column.id" :class="cn('whitespace-normal', column.numeric && 'text-right tabular-nums', column.class)">
              <Cell :cell="cellOf(column, row)" @drill="(d) => emit('drill', d)" />
            </TableCell>
            <TableCell v-if="onRowClick" class="w-8 pr-3 text-muted-foreground">
              <ChevronRight class="size-4 opacity-50 transition-[opacity,transform] group-hover/row:translate-x-0.5 group-hover/row:text-foreground group-hover/row:opacity-100 group-focus-visible/row:opacity-100" aria-hidden="true" />
            </TableCell>
          </TableRow>
          <TableRow v-if="!pageRows.length" class="hover:bg-transparent">
            <TableCell :colspan="visibleColumns.length + (onRowClick ? 1 : 0)" class="h-20 text-center text-muted-foreground">{{ emptyText }}</TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </div>

    <div v-if="pageSize" class="flex items-center justify-between gap-2 text-sm text-muted-foreground">
      <span class="tabular-nums" data-slot="row-count">
        {{ sorted.length.toLocaleString('en-US') }}<template v-if="showTotal"> of {{ rows.length.toLocaleString('en-US') }}</template> {{ !showTotal && sorted.length === 1 ? 'row' : 'rows' }}
      </span>
      <div v-if="pageCount > 1" class="flex items-center gap-2">
        <span class="tabular-nums">Page {{ page + 1 }} of {{ pageCount }}</span>
        <Button variant="outline" size="icon-sm" :disabled="page === 0" aria-label="Previous page" @click="page--"><ChevronLeft /></Button>
        <Button variant="outline" size="icon-sm" :disabled="page >= pageCount - 1" aria-label="Next page" @click="page++"><ChevronRight /></Button>
      </div>
    </div>
  </div>
</template>
