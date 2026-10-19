<script setup lang="ts">
import type { ColumnDef } from '@tanstack/vue-table'
import { computed, h, ref } from 'vue'
import DataTable from '@/components/ops/DataTable.vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { shortDate, value, type Rec } from '@/domain/format'
import { commitUrl, perfHistory, perfHistoryBuildLabel, perfImageDetail, perfPointDetail, perfValue, type PerfDetail } from '@/domain/perf'
import PerfDetailSheet from './PerfDetailSheet.vue'
import PerfLineChart from './PerfLineChart.vue'

export interface PerfHistoryTarget {
  model: Rec
  config: Rec
  metricName: string
  block: Rec
}

const props = defineProps<{ target: PerfHistoryTarget | null }>()
const open = defineModel<boolean>('open', { required: true })

const history = computed(() => (props.target ? perfHistory(props.target.model, props.target.config, props.target.metricName, props.target.block) : null))
const stats = computed(() => {
  const h = history.value
  if (!h) return []
  return [
    { label: 'Latest', value: h.latest, tone: null },
    { label: 'Previous', value: h.previous, tone: null },
    { label: 'Change', value: h.change, tone: h.changeTone },
    { label: 'Points', value: h.points, tone: null },
  ]
})

const image = ref<{ open: boolean; detail: PerfDetail | null }>({ open: false, detail: null })
const link = (label: string, url: unknown, cls = 'font-mono hover:underline') =>
  url ? h('a', { href: String(url), target: '_blank', rel: 'noopener', class: cls }, label) : h('span', { class: 'font-mono text-muted-foreground' }, label)

const columns = computed<ColumnDef<Rec, unknown>[]>(() => [
  {
    id: 'build',
    header: 'Nightly',
    accessorFn: (row) => Number(row.build_number || 0),
    cell: ({ row }) => link(perfHistoryBuildLabel(row.original), row.original.build_url),
    enableHiding: false,
  },
  { id: 'date', header: 'Observed', accessorFn: (row) => String(row.date || ''), cell: ({ row }) => shortDate(row.original.date) },
  {
    id: 'value',
    header: 'Value',
    accessorFn: (row) => (Number.isFinite(Number(row.value)) ? Number(row.value) : -Infinity),
    cell: ({ row }) => h('span', { class: 'tabular-nums font-medium' }, perfValue(row.original.value, props.target?.block.unit)),
  },
  {
    id: 'commit',
    header: 'vLLM commit',
    accessorFn: (row) => String(row.vllm_commit || ''),
    cell: ({ row }) =>
      row.original.vllm_commit ? link(String(row.original.vllm_commit).slice(0, 7), commitUrl(row.original.vllm_commit)) : h('span', { class: 'text-muted-foreground' }, '-'),
  },
  {
    id: 'image',
    header: 'Image',
    accessorFn: (row) => String(row.image || ''),
    cell: ({ row }) =>
      h(
        'button',
        {
          type: 'button',
          class: 'max-w-[22rem] truncate text-left font-mono text-xs hover:underline',
          title: value(row.original.image),
          onClick: () => (image.value = { open: true, detail: perfImageDetail(row.original) }),
        },
        value(row.original.image),
      ),
  },
])
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto sm:max-w-4xl" data-testid="perf-history">
      <SheetHeader>
        <SheetTitle class="break-all">{{ history?.title }}</SheetTitle>
        <SheetDescription>{{ history?.subtitle }}</SheetDescription>
      </SheetHeader>
      <div v-if="history && target" class="flex flex-col gap-4 px-4 pb-6">
        <p class="rounded-md border border-info/40 bg-info/10 p-3 text-sm">
          <strong>{{ history.name }}</strong>{{ history.directionNote }}
        </p>
        <div class="grid grid-cols-2 gap-3 md:grid-cols-4">
          <div v-for="stat in stats" :key="stat.label" class="rounded-lg border p-3" data-testid="perf-history-stat">
            <div class="text-xs font-medium tracking-wide text-muted-foreground uppercase">{{ stat.label }}</div>
            <div class="mt-1 text-xl font-semibold tabular-nums">
              <ToneBadge v-if="stat.tone && stat.tone !== 'neutral'" :tone="stat.tone" class="text-base">{{ stat.value }}</ToneBadge>
              <template v-else>{{ stat.value }}</template>
            </div>
          </div>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Nightly history</CardTitle>
            <CardDescription>Payload order; points without a numeric value are omitted. Select a point to inspect it.</CardDescription>
          </CardHeader>
          <CardContent class="px-2 sm:px-6">
            <PerfLineChart
              :series="target.block.series || []"
              :status="target.block.status"
              :unit="target.block.unit"
              :height="240"
              @point="(point) => (image = { open: true, detail: perfPointDetail(point, target?.block.unit) })"
            />
          </CardContent>
        </Card>
        <div>
          <h3 class="mb-2 text-sm font-medium" data-testid="perf-history-caption">{{ history.tableTitle }}</h3>
          <DataTable :columns="columns" :data="history.series" :page-size="15" search-placeholder="Filter build, commit, or image" empty-text="No matching observations." />
        </div>
      </div>
    </SheetContent>
  </Sheet>
  <PerfDetailSheet v-model:open="image.open" :detail="image.detail" />
</template>
