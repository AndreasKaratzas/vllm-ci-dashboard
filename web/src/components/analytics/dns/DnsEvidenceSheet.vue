<script setup lang="ts">
import type { ColumnDef } from '@tanstack/vue-table'
import { computed, h } from 'vue'
import DataTable from '@/components/ops/DataTable.vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import {
  DNS_FALLBACK_ASSET,
  queueDnsEvidenceSearchText,
  queueDnsEvidenceUrl,
  queueDnsOutcomePresentation,
  queueDnsSignatureLabels,
  queueDnsTargetLabels,
  type DnsEvidenceDrawer,
} from '@/domain/dns'
import { integer, shortDate, value, type Rec } from '@/domain/format'

const props = defineProps<{ drawer: DnsEvidenceDrawer | null; sourceAsset?: string }>()
const open = defineModel<boolean>('open', { required: true })

const NOTE_CLASS = {
  'is-warning': 'border-warning/40 bg-warning/10 text-warning',
  'is-info': 'border-info/40 bg-info/10 text-info',
} as Record<string, string>

const fields = computed(() => (props.drawer?.fields ?? []).filter((field) => field.value !== null && field.value !== ''))
const rows = computed(() => (props.drawer?.rows ?? []).map((row) => ({ ...row, _search: queueDnsEvidenceSearchText(row) })))

const link = (label: string, url: string, cls = 'font-medium hover:underline') =>
  url ? h('a', { href: url, target: '_blank', rel: 'noopener', class: cls }, label) : h('span', { class: 'text-muted-foreground' }, label)

const columns: ColumnDef<Rec, unknown>[] = [
  {
    id: 'outcome',
    header: 'Job outcome',
    accessorFn: (row) => queueDnsOutcomePresentation(row.state).label,
    cell: ({ row }) => {
      const outcome = queueDnsOutcomePresentation(row.original.state)
      const badge = h(ToneBadge, { tone: outcome.tone }, () => outcome.label)
      const url = queueDnsEvidenceUrl(row.original)
      return url ? h('a', { href: url, target: '_blank', rel: 'noopener', 'aria-label': `Inspect result: ${outcome.label}` }, [badge]) : badge
    },
    enableHiding: false,
  },
  {
    id: 'evidence',
    header: 'Evidence',
    accessorFn: (row) => row._search,
    cell: ({ row }) => {
      const url = queueDnsEvidenceUrl(row.original)
      return url ? link('Open exact log', url, 'font-medium text-primary hover:underline') : h('span', { class: 'text-muted-foreground' }, 'Exact link unavailable')
    },
    enableSorting: false,
  },
  {
    id: 'observed',
    header: 'Observed',
    accessorFn: (row) => row.first_at,
    cell: ({ row }) =>
      shortDate(row.original.first_at) + (row.original.last_at && row.original.last_at !== row.original.first_at ? ' → ' + shortDate(row.original.last_at) : ''),
  },
  { id: 'basis', header: 'Time basis', accessorFn: (row) => value(String(row.time_basis || '').replace(/_/g, ' ')) },
  { id: 'hardware', header: 'Hardware', accessorFn: (row) => value(row.hardware) },
  {
    id: 'build',
    header: 'Build',
    accessorFn: (row) => Number(row.build_number) || 0,
    cell: ({ row }) => link(value(row.original.pipeline) + ' #' + value(row.original.build_number), queueDnsEvidenceUrl(row.original), 'font-mono hover:underline'),
  },
  { id: 'episodes', header: 'Episodes', accessorFn: (row) => Number(row.episodes) || 0, cell: ({ row }) => integer(row.original.episodes) },
  { id: 'matches', header: 'Raw matches', accessorFn: (row) => Number(row.match_count) || 0, cell: ({ row }) => integer(row.original.match_count) },
  { id: 'targets', header: 'Targets', accessorFn: (row) => value(queueDnsTargetLabels(row).join(', ')) },
  { id: 'signatures', header: 'DNS signatures', accessorFn: (row) => value(queueDnsSignatureLabels(row).join(', ')) },
]
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto sm:max-w-6xl">
      <SheetHeader>
        <SheetTitle class="font-mono">{{ drawer?.title }}</SheetTitle>
        <SheetDescription>{{ drawer?.subtitle }}</SheetDescription>
      </SheetHeader>
      <div v-if="drawer" class="flex flex-col gap-4 px-4 pb-6">
        <p class="text-sm text-muted-foreground">
          Each row is one distinct Buildkite job attempt with a DNS-specific resolver signature. Repeated lines within an attempt do not inflate the
          count. A passing job is an observation in a job that ultimately passed, not an incident.
        </p>
        <dl class="grid gap-3 sm:grid-cols-3 lg:grid-cols-4" data-testid="dns-evidence-fields">
          <div v-for="field in fields" :key="field.label" class="rounded-lg border bg-muted/30 px-3 py-2">
            <dt class="text-xs text-muted-foreground">{{ field.label }}</dt>
            <dd class="font-mono text-sm font-semibold tabular-nums">{{ field.value }}</dd>
          </div>
        </dl>
        <Button variant="outline" size="sm" as="a" class="w-fit" :href="sourceAsset || DNS_FALLBACK_ASSET" target="_blank" rel="noopener">
          Open published DNS observations ↗
        </Button>
        <div class="rounded-md border border-info/40 bg-info/10 p-3 text-sm text-info" data-testid="dns-evidence-note">
          <strong>DNS observation is not the job outcome. </strong>
          <span>
            Passed means the final Buildkite job outcome was passed after a resolver signature was observed. Soft- and hard-failed outcomes are shown
            separately; none establishes DNS as the cause.
          </span>
        </div>
        <p v-if="drawer.note" class="rounded-md border p-3 text-sm" :class="NOTE_CLASS[drawer.note.tone]" data-testid="dns-evidence-note">
          {{ drawer.note.text }}
        </p>
        <div class="flex items-baseline justify-between gap-3">
          <h3 class="font-semibold">Exact Buildkite log evidence</h3>
          <span class="text-sm text-muted-foreground" data-testid="dns-evidence-meta">{{ drawer.tableMeta }}</span>
        </div>
        <DataTable
          :columns="columns"
          :data="rows"
          :page-size="30"
          search-placeholder="Filter job, build, target, or signature"
          empty-text="No exact log links were retained for this node."
        />
      </div>
    </SheetContent>
  </Sheet>
</template>
