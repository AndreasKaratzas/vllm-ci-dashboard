<script setup lang="ts">
import type { ColumnDef } from '@tanstack/vue-table'
import { computed, h, ref } from 'vue'
import DistributionCard, { SEGMENT_FILL, type Segment } from '@/components/ops/DistributionCard.vue'
import RingCard from '@/components/ops/RingCard.vue'
import TableSheet from '@/components/ops/TableSheet.vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import {
  ROUTE_MODE_LABELS,
  parityGapAreas,
  testGroupDefinitionState,
  testGroupParityRows,
  testGroupParityState,
  type GapArea,
} from '@/domain/ciHealth'
import { integer, value, type Rec } from '@/domain/format'
import { currentTestGroupParity } from '@/domain/health'

const props = defineProps<{ ops: Rec }>()

const parity = computed(() => currentTestGroupParity(props.ops.test_group_parity))
const summary = computed<Rec>(() => parity.value.summary || {})
const source = computed<Rec>(() => parity.value.source || {})
const retention = computed<Rec>(() => parity.value.operations_publication_retention || {})
const allRows = computed(() => testGroupParityRows(parity.value, 'all', 'all'))
const mainRows = computed(() => testGroupParityRows(parity.value, 'existing', 'all'))
const missingRows = computed(() => testGroupParityRows(parity.value, 'action', 'all'))
const unsupportedRows = computed(() => testGroupParityRows(parity.value, 'unsupported', 'all'))
const applicableRows = computed(() => allRows.value.filter((row) => row.state !== 'unsupported'))
const gapAreas = computed(() => parityGapAreas(parity.value))
const inventoryMain = computed<Rec>(() => (parity.value.rocm_inventory || summary.value.rocm_inventory || {}).main || {})
const available = computed(() => allRows.value.length > 0 && Number(summary.value.upstream_logical_groups || 0) > 0)

const segments = computed<Segment[]>(() => [
  { key: 'existing', label: 'covered on main', count: Number(summary.value.main_complete_groups || 0), tone: 'is-success' },
  { key: 'action', label: 'potential open gaps', count: Number(summary.value.action_groups || 0), tone: 'is-danger' },
  { key: 'unsupported', label: 'not targeted', count: Number(summary.value.unsupported_groups || 0), tone: 'is-not-targeted' },
])

const configNote = computed(() => {
  const s = summary.value
  const required = s.main_required_groups
  const rate = s.main_required_rate_pct
  const commit = source.value.current_definition_commit_sha || source.value.main_commit || ''
  return (
    `Configured AMD routes cover ${integer(s.main_complete_groups)} of ${integer(s.applicable_groups)} applicable current CUDA logical groups. ` +
    (required === undefined
      ? 'Required-route coverage is unavailable. '
      : `${integer(required)} groups have a required source configuration` +
        (Number.isFinite(Number(rate)) ? ` (${Number(rate).toFixed(1)}%)` : '') +
        `; ${integer(s.main_optional_only_groups)} are optional only and ${integer(s.main_soft_fail_only_groups)} are soft fail only. `) +
    'These are source configuration flags; individual nightly scheduling and blocking outcomes are separate.' +
    (/^[0-9a-f]{40}$/.test(commit) ? ` Source: main CI at ${commit.slice(0, 12)}.` : '')
  )
})

const browser = ref({ open: false, title: '', rows: [] as Rec[] })
function openRows(title: string, rows: Rec[]) {
  browser.value = { open: true, title, rows }
}
function openSegment(segment: Segment) {
  const map: Record<string, [string, Rec[]]> = {
    existing: ['Covered on main', mainRows.value],
    action: ['Potential open gaps', missingRows.value],
    unsupported: ['Not targeted / unsupported', unsupportedRows.value],
  }
  const [title, rows] = map[segment.key as string]!
  openRows(title, rows)
}
function openArea(area: GapArea) {
  openRows(`${area.label} potential open gaps`, area.rows)
}

const detail = ref<{ open: boolean; row: Rec | null }>({ open: false, row: null })
function openDetail(row: Rec) {
  detail.value = { open: true, row }
}
const detailFields = computed(() => {
  const row = detail.value.row
  if (!row) return []
  const definition = row.definition_resolution || {}
  return [
    ['Inventory number', value(row.id)],
    ['Test area', value(row.area)],
    ['Configured coverage', testGroupParityState(row.state).label.replace(/^[●■]\s*/, '')],
    ['Current CI definition', testGroupDefinitionState(row)],
    ['Source name', value(row.title)],
    ['AMD route mode', ROUTE_MODE_LABELS[row.gate_kind] || 'Unavailable'],
    ['Current replacement names', (definition.successor_labels || []).join(' · ') || '—'],
    ['Definition changes', definition.note || '—'],
    ['Upstream CUDA variants', value(row.cuda_variants)],
  ]
})
const detailSources = computed(() => {
  const row = detail.value.row
  if (!row) return []
  const definition = row.definition_resolution || {}
  const commit = source.value.main_commit || source.value.commit_sha || ''
  const commitUrl = source.value.main_commit_url || (commit ? `https://github.com/vllm-project/vllm/commit/${commit}` : '')
  return [
    commitUrl ? { label: 'Open current main CI source commit', url: commitUrl } : null,
    /^[0-9a-f]{40}$/.test(definition.commit_sha || '')
      ? { label: 'Open current CI definitions', url: `https://github.com/vllm-project/vllm/tree/${definition.commit_sha}/.buildkite/test_areas` }
      : null,
  ].filter((s): s is { label: string; url: string } => s !== null)
})

const columns: ColumnDef<Rec, unknown>[] = [
  { id: 'id', header: '#', accessorFn: (row) => Number(row.id || 0), cell: ({ getValue }) => h('span', { class: 'font-mono' }, integer(getValue())) },
  {
    id: 'title',
    header: 'Upstream logical test group',
    accessorFn: (row) => [row.title, row.reviewed_title].join(' '),
    cell: ({ row }) => h('button', { type: 'button', class: 'text-left font-medium hover:underline', onClick: () => openDetail(row.original) }, row.original.title),
    enableHiding: false,
  },
  { id: 'area', header: 'Area', accessorFn: (row) => value(row.area) },
  { id: 'cuda', header: 'CUDA variants', accessorFn: (row) => value(row.cuda_variants) },
  {
    id: 'coverage',
    header: 'Configured coverage',
    accessorFn: (row) => testGroupParityState(row.state).label,
    cell: ({ row }) => {
      const p = testGroupParityState(row.original.state)
      return h(ToneBadge, { tone: p.tone }, () => p.label)
    },
  },
  { id: 'definition', header: 'Current CI definition', accessorFn: (row) => testGroupDefinitionState(row) },
  {
    id: 'assessment',
    header: 'ROCm counterpart or assessment',
    accessorFn: (row) => value(row.assessment),
    cell: ({ getValue }) => h('span', { class: 'text-muted-foreground' }, String(getValue())),
    enableSorting: false,
  },
]
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <p v-if="retention.complete_relative_to_source === false" class="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning">
      Parity rows are storage-bounded in this snapshot; counts are lower bounds.
    </p>
    <p v-if="!available" class="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning">
      The current main CI test-group parity inventory is unavailable in this snapshot.
    </p>
    <template v-else>
      <div class="grid gap-4 md:gap-6 @4xl/main:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <RingCard
          eyebrow="Upstream parity on main"
          title="Applicable test groups covered"
          :current="Number(summary.main_complete_groups || 0)"
          :total="Number(summary.applicable_groups || 0)"
          :meta="`${integer(summary.main_missing_groups)} potential open gaps remain`"
          :tone="Number(summary.main_missing_groups) ? 'is-warning' : 'is-success'"
          action-label="Inspect applicable groups →"
          data-testid="parity-ring"
          @open="openRows('Applicable upstream test groups', applicableRows)"
        />
        <DistributionCard
          :title="`Current main CI · ${integer(summary.upstream_logical_groups)} logical groups`"
          :subtitle="`${integer(summary.unsupported_groups)} hardware- or backend-specific groups are classified outside the parity denominator.`"
          :segments="segments"
          @open="openSegment"
        />
      </div>
      <p class="rounded-md border border-info/40 bg-info/10 p-3 text-sm text-info">{{ configNote }}</p>

      <Card>
        <CardHeader>
          <CardTitle>Potential open gaps by test area</CardTitle>
          <CardDescription>Select an area to open its complete table.</CardDescription>
        </CardHeader>
        <CardContent>
          <div class="grid gap-3 @3xl/main:grid-cols-2 @6xl/main:grid-cols-3">
            <button
              v-for="area in gapAreas"
              :key="area.label"
              type="button"
              class="flex flex-col gap-2 rounded-lg border p-3 text-left hover:bg-accent"
              data-testid="gap-area"
              @click="openArea(area)"
            >
              <div class="flex items-baseline justify-between gap-2">
                <strong>{{ area.label }}</strong>
                <span class="text-xs text-destructive">{{ area.countLabel }}</span>
              </div>
              <div class="flex h-2 w-full overflow-hidden rounded-full bg-muted">
                <span
                  v-for="segment in area.segments.filter((s) => s.count)"
                  :key="segment.label"
                  :class="SEGMENT_FILL[segment.tone]"
                  :style="{ width: `${(segment.count / Math.max(1, area.total)) * 100}%` }"
                />
              </div>
              <ul class="text-xs text-muted-foreground">
                <li v-for="group in area.rows.slice(0, 3)" :key="group.id" class="truncate">#{{ integer(group.id) }} {{ group.title }}</li>
              </ul>
            </button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Inspect exact test groups</CardTitle>
          <CardDescription>Tables open in a searchable panel; selecting a test group opens its assessment.</CardDescription>
        </CardHeader>
        <CardContent class="flex flex-wrap gap-2">
          <Button @click="openRows('Potential open gaps', missingRows)">Browse all {{ integer(summary.action_groups) }} potential open gaps</Button>
          <Button variant="outline" @click="openRows('Not targeted / unsupported', unsupportedRows)">
            Browse {{ integer(summary.unsupported_groups) }} not-targeted groups
          </Button>
          <Button variant="outline" @click="openRows('Complete current upstream inventory', allRows)">
            Browse complete {{ integer(summary.upstream_logical_groups) }}-group inventory
          </Button>
        </CardContent>
      </Card>
      <p v-if="inventoryMain.logical_groups || inventoryMain.physical_definitions" class="text-sm text-muted-foreground">
        Separate ROCm inventory on main: {{ integer(inventoryMain.logical_groups) }} logical AMD test groups from
        {{ integer(inventoryMain.physical_definitions) }} YAML definitions. These inventory counts are not the upstream-parity numerator.
      </p>
    </template>

    <TableSheet
      v-model:open="browser.open"
      :title="browser.title"
      :subtitle="`${integer(browser.rows.length)} current upstream logical test groups`"
      :columns="columns"
      :rows="browser.rows"
      search-placeholder="Filter test group, area, CUDA variant, status, or assessment"
    />

    <Sheet v-model:open="detail.open">
      <SheetContent class="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>{{ detail.row?.title || 'Upstream logical test group' }}</SheetTitle>
          <SheetDescription>Configured AMD routes against upstream source definitions</SheetDescription>
        </SheetHeader>
        <div class="flex flex-col gap-4 px-4 pb-6">
          <p class="text-sm">{{ detail.row?.assessment || 'No source-derived assessment is available.' }}</p>
          <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <template v-for="[label, val] in detailFields" :key="label">
              <dt class="text-muted-foreground">{{ label }}</dt>
              <dd class="font-medium">{{ val }}</dd>
            </template>
          </dl>
          <div class="flex flex-wrap gap-3">
            <a v-for="link in detailSources" :key="link.url" :href="link.url" target="_blank" rel="noopener" class="text-sm font-medium hover:underline">
              {{ link.label }} ↗
            </a>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  </div>
</template>
