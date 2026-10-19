<script setup lang="ts">
// Classic openMatrixGroupEvidence: one configured test group's status and exact AMD routes.
import type { ColumnDef } from '@tanstack/vue-table'
import { computed, h } from 'vue'
import DataTable from '@/components/ops/DataTable.vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { toneForState, value, type Rec } from '@/domain/format'
import { matrixGroupDetail, matrixHealthStatusLabel, matrixHealthTone } from '@/domain/healthHardware'
import DetailFields from './DetailFields.vue'

const props = defineProps<{ group: Rec | null; matrix: Rec | null }>()
const open = defineModel<boolean>('open', { required: true })

const detail = computed(() => (props.group ? matrixGroupDetail(props.group, props.matrix) : null))
const NOTE_CLASSES: Record<string, string> = {
  'is-success': 'border-success/40 bg-success/10',
  'is-danger': 'border-destructive/40 bg-destructive/10',
  'is-warning': 'border-warning/40 bg-warning/10',
  'is-info': 'border-info/40 bg-info/10',
  'is-neutral': '',
}

const link = (label: string, url: string, cls = 'font-medium hover:underline') => h('a', { href: url, target: '_blank', rel: 'noopener', class: cls }, label)
const columns: ColumnDef<Rec, unknown>[] = [
  {
    id: 'definition',
    header: 'Definition',
    accessorFn: (row) => row.definition,
    cell: ({ row }) => (row.original.url ? link(String(row.original.definition), row.original.url) : h('span', { class: 'font-medium' }, String(row.original.definition))),
    enableHiding: false,
  },
  {
    id: 'hardware',
    header: 'Hardware',
    accessorFn: (row) => String(row.architecture).toUpperCase(),
    cell: ({ getValue }) => h(ToneBadge, { tone: 'is-neutral' }, () => String(getValue())),
  },
  { id: 'queue', header: 'Agent pool', accessorFn: (row) => value(row.queue), cell: ({ getValue }) => h('span', { class: 'font-mono text-xs' }, String(getValue())) },
  {
    id: 'result',
    header: 'Latest result',
    accessorFn: (row) => String(row.result),
    cell: ({ row }) => {
      const badge = h(ToneBadge, { tone: toneForState(row.original.result) }, () => String(row.original.result))
      return row.original.url ? h('a', { href: row.original.url, target: '_blank', rel: 'noopener' }, [badge]) : badge
    },
  },
  {
    id: 'identity',
    header: 'Command identity',
    accessorFn: (row) => row.commandIdentity || '-',
    cell: ({ row }) => h('span', { class: row.original.commandIdentity ? 'font-mono text-xs' : 'text-muted-foreground' }, row.original.commandIdentity || '-'),
  },
  {
    id: 'evidence',
    header: 'Evidence',
    enableSorting: false,
    cell: ({ row }) => {
      const actions = [row.original.url ? link('Job ↗', row.original.url) : null, row.original.sourceUrl ? link('Source ↗', row.original.sourceUrl) : null].filter(Boolean)
      return actions.length ? h('div', { class: 'flex gap-3' }, actions) : h('span', { class: 'text-muted-foreground' }, '-')
    },
  },
]
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto sm:max-w-5xl">
      <SheetHeader>
        <SheetTitle>{{ detail?.title }}</SheetTitle>
        <SheetDescription>Test-group status and exact AMD route evidence</SheetDescription>
      </SheetHeader>
      <div v-if="group && detail" class="flex flex-col gap-4 px-4 pb-6" data-testid="matrix-group-detail">
        <DetailFields :fields="detail.fields" :sources="detail.sources" />
        <Alert :class="NOTE_CLASSES[matrixHealthTone(group.status)]">
          <AlertDescription class="text-foreground">
            <p><strong>{{ matrixHealthStatusLabel(group.status) }}.</strong> {{ detail.note }}</p>
          </AlertDescription>
        </Alert>
        <p v-if="group.classificationReason" class="text-sm text-muted-foreground">{{ group.classificationReason }}</p>
        <section class="flex flex-col gap-2">
          <h3 class="text-sm font-semibold">{{ detail.evidenceCaption }}</h3>
          <DataTable :columns="columns" :data="detail.evidence" :page-size="10" search-placeholder="Filter definition, pool, or result" empty-text="No matching observations." />
        </section>
        <section v-if="detail.commandsText" class="flex flex-col gap-2">
          <h3 class="text-sm font-semibold">Executed commands</h3>
          <p class="text-xs text-muted-foreground">{{ detail.commandsCaption }}</p>
          <pre class="max-h-80 overflow-auto rounded-lg border bg-muted p-3 font-mono text-xs whitespace-pre-wrap">{{ detail.commandsText }}</pre>
        </section>
      </div>
    </SheetContent>
  </Sheet>
</template>
