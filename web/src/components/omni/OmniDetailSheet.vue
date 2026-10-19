<script setup lang="ts">
import type { ColumnDef } from '@tanstack/vue-table'
import { ArrowLeft, ExternalLink } from '@lucide/vue'
import { computed, h } from 'vue'
import DataTable from '@/components/ops/DataTable.vue'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import type { Rec } from '@/domain/format'
import { detailSubtitle, detailTitle, visibleFields, type DetailTable, type OmniDetail } from '@/domain/omni'
import OmniCell from './OmniCell.vue'

const props = withDefaults(defineProps<{ detail: OmniDetail; depth?: number }>(), { depth: 0 })
const open = defineModel<boolean>('open', { required: true })
const emit = defineEmits<{ drill: [OmniDetail] }>()

const fields = computed(() => visibleFields(props.detail.fields))
const sources = computed(() => props.detail.sources.filter((s) => s && s.url))

function columnsFor(table: DetailTable<Rec>): ColumnDef<Rec, unknown>[] {
  return table.columns.map((column, index) => ({
    id: `${index}-${column.label}`,
    header: column.label,
    // The first column carries the classic search text so the filter matches it.
    accessorFn: (row: Rec) => {
      const cellText = column.cell(row).text
      if (index === 0 && table.searchText) return `${cellText} ${table.searchText(row)}`
      return column.numeric && /^-?[\d,.]+$/.test(cellText) ? Number(cellText.replace(/,/g, '')) : cellText
    },
    cell: ({ row }) =>
      h('div', { class: column.numeric ? 'text-right tabular-nums' : '' }, [
        h(OmniCell, { cell: column.cell(row.original), onOpen: (d: OmniDetail) => emit('drill', d) }),
      ]),
    enableHiding: index !== 0,
  }))
}
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto sm:max-w-4xl" data-testid="omni-detail">
      <SheetHeader class="pr-10">
        <Button v-if="depth > 0" variant="ghost" size="sm" class="-ml-2 w-fit text-muted-foreground" @click="open = false">
          <ArrowLeft /> Back
        </Button>
        <SheetTitle>{{ detailTitle(detail) }}</SheetTitle>
        <SheetDescription>{{ detailSubtitle(detail) }}</SheetDescription>
      </SheetHeader>
      <div class="flex flex-col gap-4 px-4 pb-6">
        <p v-if="detail.description" class="text-sm text-muted-foreground" data-testid="omni-detail-description">{{ detail.description }}</p>

        <div v-if="detail.strip?.length" class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Card v-for="item in detail.strip" :key="item.label" class="gap-2 py-4">
            <CardHeader class="px-4">
              <CardDescription class="text-xs font-medium tracking-wide">{{ item.label }}</CardDescription>
              <CardTitle class="text-2xl font-semibold tabular-nums">{{ item.value }}</CardTitle>
            </CardHeader>
            <CardContent class="px-4 text-xs text-muted-foreground">{{ item.meta }}</CardContent>
          </Card>
        </div>

        <dl v-if="fields.length" class="grid grid-cols-1 gap-x-6 gap-y-2 rounded-lg border p-4 text-sm sm:grid-cols-[minmax(10rem,auto)_1fr]">
          <template v-for="field in fields" :key="field.label">
            <dt class="text-muted-foreground">{{ field.label }}</dt>
            <dd class="font-medium break-words">{{ field.text }}</dd>
          </template>
        </dl>

        <div v-if="sources.length" class="flex flex-wrap gap-2">
          <Button v-for="source in sources" :key="source.url + source.label" variant="outline" size="sm" as="a" :href="source.url" target="_blank" rel="noopener">
            {{ source.label }} <ExternalLink />
          </Button>
        </div>

        <p v-if="detail.empty" class="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">{{ detail.empty }}</p>

        <template v-for="(table, index) in detail.tables" :key="index">
          <div class="flex flex-col gap-2">
            <div v-if="table.title || table.meta" class="flex flex-wrap items-baseline justify-between gap-2">
              <h3 v-if="table.title" class="text-sm font-semibold">{{ table.title }}</h3>
              <span v-if="table.meta" class="text-xs text-muted-foreground">{{ table.meta }}</span>
            </div>
            <DataTable
              :columns="columnsFor(table)"
              :data="table.rows"
              :page-size="25"
              :search-placeholder="table.searchPlaceholder ?? 'Filter rows'"
              empty-text="No matching evidence."
            />
          </div>
        </template>
      </div>
    </SheetContent>
  </Sheet>
</template>
