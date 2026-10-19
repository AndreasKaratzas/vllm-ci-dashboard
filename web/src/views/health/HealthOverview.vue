<script setup lang="ts">
import type { ColumnDef } from '@tanstack/vue-table'
import { computed, h, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AmdGroupSheet from '@/components/health/AmdGroupSheet.vue'
import MirrorSummaryCard from '@/components/health/MirrorSummaryCard.vue'
import MovementChart from '@/components/health/MovementChart.vue'
import BuildDetailSheet from '@/components/ops/BuildDetailSheet.vue'
import DataTable from '@/components/ops/DataTable.vue'
import DistributionCard, { type Segment } from '@/components/ops/DistributionCard.vue'
import RingCard from '@/components/ops/RingCard.vue'
import TableSheet from '@/components/ops/TableSheet.vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  amdLatestState,
  amdLogicalCatalogGroup,
  amdLogicalInventory,
  amdLogicalSignalLabel,
  amdLogicalSignalTone,
  amdLogicalStateLabel,
  amdLogicalStateTone,
  amdStateLabel,
  latestFailureObservations,
  logicalTestGroupPresentation,
  movementBuilds,
  sortLogicalRows,
} from '@/domain/ciHealth'
import { integer, toneForState, value, type Rec } from '@/domain/format'
import { currentAmdHealth, hardwareDisplayLabel } from '@/domain/health'
import { amdNightlyPresentation, latestAmd } from '@/domain/nightly'

const props = defineProps<{ ops: Rec }>()

const amd = computed(() => latestAmd(props.ops))
const health = computed(() => currentAmdHealth(props.ops.amd_test_health))
const summary = computed<Rec>(() => health.value.summary || {})
const latestBuild = computed(() => summary.value.latest_build_number)
const counts = computed<Rec>(() => summary.value.latest_test_group_counts || {})
const logical = computed(() => logicalTestGroupPresentation(counts.value))
const inventory = computed(() => amdLogicalInventory(props.ops.amd_test_health))
const logicalRows = computed<Rec[]>(() => inventory.value.rows || [])
const nightly = computed(() =>
  amdNightlyPresentation((amd.value.builds || [])[0] || {}, summary.value, props.ops.generated_at),
)
// Classic summaryCard: the runtime group count only when the logical signal is available.
const mirrorInventory = computed<Rec>(() => (props.ops.test_group_parity || {}).mirror_inventory || {})
const route = useRoute()
const router = useRouter()
const openMirrors = () => void router.push({ query: { ...route.query, view: 'mirrors' } })
const failures = computed(() => latestFailureObservations(props.ops.amd_test_health))
const movement = computed(() => movementBuilds(amd.value.builds || []))

const segments = computed<Segment[]>(() => [
  { key: 'passing_all', label: 'pass every route', count: Number(counts.value.passing_all || 0), tone: 'pass' },
  { key: 'partial', label: 'pass some routes', count: Number(counts.value.partial || 0), tone: 'soft' },
  { key: 'non_passing', label: 'non-passing', count: Number(counts.value.non_passing || 0), tone: 'hard' },
])

// Logical catalog sheet
const catalog = ref({ open: false, title: '', rows: [] as Rec[] })
function openCatalog(title: string, rows: Rec[]) {
  catalog.value = { open: true, title, rows: sortLogicalRows(rows) }
}
function openSegment(segment: Segment) {
  openCatalog(
    { passing_all: 'Logical groups passing every route', partial: 'Logical groups with mixed hardware outcomes', non_passing: 'Logical groups without a passing route' }[
      segment.key as string
    ] ?? 'Logical groups',
    logicalRows.value.filter((row) => row.state === segment.key),
  )
}
const catalogColumns: ColumnDef<Rec, unknown>[] = [
  {
    id: 'label',
    header: 'Logical AMD test group',
    accessorFn: (row) => row.label || row.logical_key,
    cell: ({ row }) =>
      h('button', { type: 'button', class: 'text-left font-medium hover:underline', onClick: () => openLogical(row.original) }, row.original.label || row.original.logical_key),
    enableHiding: false,
  },
  {
    id: 'state',
    header: 'Latest result',
    accessorFn: (row) => amdLogicalStateLabel(row.state),
    cell: ({ row }) => h(ToneBadge, { tone: amdLogicalStateTone(row.original.state) }, () => amdLogicalStateLabel(row.original.state)),
  },
  { id: 'hardware', header: 'Hardware routes', accessorFn: (row) => Number(row.hardware_count || 0) },
  { id: 'variants', header: 'Exact job variants', accessorFn: (row) => Number(row.job_variant_count || 0) },
  {
    id: 'routes',
    header: 'Route outcomes',
    accessorFn: (row) => (row.hardware_states || []).map((s: Rec) => `${s.hardware} ${s.state}`).join(' '),
    cell: ({ row }) =>
      h(
        'div',
        { class: 'flex flex-wrap gap-1' },
        (row.original.hardware_states || []).map((s: Rec) =>
          h(ToneBadge, { tone: amdLogicalSignalTone(s.state) }, () => `${hardwareDisplayLabel(s.hardware)} · ${amdLogicalSignalLabel(s.state)}`),
        ),
      ),
    enableSorting: false,
  },
]

// Logical group detail
const logicalDetail = ref<{ open: boolean; row: Rec | null }>({ open: false, row: null })
function openLogical(row: Rec) {
  logicalDetail.value = { open: true, row }
}

// Exact job detail
const groupDetail = ref<{ open: boolean; row: Rec | null }>({ open: false, row: null })
function openGroup(row: Rec) {
  groupDetail.value = { open: true, row }
}
function openVariant(variant: Rec) {
  const group = amdLogicalCatalogGroup(variant, props.ops.amd_test_health)
  if (group) openGroup(group)
  else if (variant.job_url) window.open(variant.job_url, '_blank', 'noopener')
}

const buildDetail = ref<{ open: boolean; build: Rec | null }>({ open: false, build: null })

const failureColumns: ColumnDef<Rec, unknown>[] = [
  {
    id: 'variant',
    header: 'AMD job variant',
    accessorFn: (row) => [row.display_name, row.name, row.hardware_variant, row.queue].join(' '),
    cell: ({ row }) =>
      h('div', { class: 'flex flex-col' }, [
        h('button', { type: 'button', class: 'text-left font-medium hover:underline', onClick: () => openGroup(row.original) }, row.original.display_name || row.original.name || row.original.job_name),
        h('span', { class: 'font-mono text-xs text-muted-foreground' }, `${value(row.original.hardware_variant || row.original.hardware, 'unknown')} - ${value(row.original.queue, 'queue unavailable')}`),
      ]),
    enableHiding: false,
  },
  {
    id: 'latest',
    header: 'Latest',
    accessorFn: (row) => amdLatestState(row, latestBuild.value),
    cell: ({ getValue }) => h(ToneBadge, { tone: toneForState(getValue()) }, () => amdStateLabel(String(getValue()))),
  },
  {
    id: 'build',
    header: 'Build',
    accessorFn: (row) => Number(row.latest_build_number || 0),
    cell: ({ row }) =>
      row.original.latest_url
        ? h('a', { href: row.original.latest_url, target: '_blank', rel: 'noopener', class: 'font-mono hover:underline' }, `#${value(row.original.latest_build_number)}`)
        : h('span', { class: 'text-muted-foreground' }, '-'),
  },
]
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <p v-if="!nightly.hasSignal" class="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning">
      Latest nightly has no test signal. The lists and chart below use the latest observed main CI AMD test signal
      <a v-if="summary.latest_build_url" :href="summary.latest_build_url" target="_blank" rel="noopener" class="underline">#{{ latestBuild }}</a>
      <span v-else>#{{ value(latestBuild) }}</span>.
    </p>

    <div class="grid gap-4 md:gap-6 @4xl/main:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
      <RingCard
        eyebrow="Latest AMD test groups"
        title="Logical runtime health"
        :available="logical.available"
        :current="Number(counts.passing || 0)"
        :total="Number(counts.total || 0)"
        :meta="logical.meta"
        :tone="!logical.available ? 'soft' : Number(counts.passing) === Number(counts.total) ? 'pass' : 'soft'"
        action-label="Inspect all logical test groups →"
        data-testid="logical-ring"
        @open="openCatalog('Latest AMD logical test groups', logicalRows)"
      />
      <DistributionCard
        :title="`Logical group outcomes · ${latestBuild ? '#' + latestBuild : 'unavailable'}`"
        subtitle="Hardware-distinct jobs are combined only when they represent the same source-aligned test group."
        :segments="segments"
        @open="openSegment"
      />
    </div>

    <MirrorSummaryCard
      :inventory="mirrorInventory"
      :runtime-group-count="logical.available ? Number(counts.total || 0) : null"
      @open="openMirrors"
    />

    <div class="grid gap-4 md:gap-6 @5xl/main:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <MovementChart :builds="movement" :latest-signal="latestBuild" @select="(build) => (buildDetail = { open: true, build })" />
      <Card>
        <CardHeader>
          <CardTitle>Latest AMD failure observations</CardTitle>
          <CardDescription>Non-passing exact job variants, hardest results first</CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable :columns="failureColumns" :data="failures" :page-size="6" search-placeholder="Filter job variant, hardware, or queue" empty-text="No non-passing AMD job variants in the latest signal." />
        </CardContent>
      </Card>
    </div>

    <TableSheet
      v-model:open="catalog.open"
      :title="catalog.title"
      :subtitle="`Build-pinned logical test groups from latest observed AMD test signal #${value(latestBuild)}; select a row for every hardware route and exact job`"
      :columns="catalogColumns"
      :rows="catalog.rows"
      search-placeholder="Filter logical test group, result, or hardware"
    />

    <Sheet v-model:open="logicalDetail.open">
      <SheetContent class="w-full overflow-y-auto sm:max-w-4xl">
        <SheetHeader>
          <SheetTitle>{{ logicalDetail.row?.label || logicalDetail.row?.logical_key }}</SheetTitle>
          <SheetDescription>
            {{ amdLogicalStateLabel(logicalDetail.row?.state) }} · {{ integer(logicalDetail.row?.hardware_count) }} hardware routes ·
            {{ integer(logicalDetail.row?.job_variant_count) }} exact job variants
          </SheetDescription>
        </SheetHeader>
        <div class="px-4 pb-6">
          <div class="overflow-x-auto rounded-lg border">
            <Table>
              <TableHeader class="bg-muted">
                <TableRow>
                  <TableHead>Exact AMD job variant</TableHead>
                  <TableHead>Hardware route</TableHead>
                  <TableHead>Test signal</TableHead>
                  <TableHead>Terminal job</TableHead>
                  <TableHead class="text-right">Tests passed</TableHead>
                  <TableHead>Evidence</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow v-for="variant in logicalDetail.row?.job_variants ?? []" :key="variant.id || variant.exact_job_name">
                  <TableCell class="whitespace-normal">
                    <button type="button" class="text-left font-medium hover:underline" @click="openVariant(variant)">
                      {{ variant.display_name || variant.exact_job_name || 'Unnamed variant' }}
                    </button>
                  </TableCell>
                  <TableCell><ToneBadge tone="neutral">{{ hardwareDisplayLabel(variant.hardware_variant || variant.hardware) }}</ToneBadge></TableCell>
                  <TableCell>
                    <ToneBadge :tone="amdLogicalSignalTone(variant.test_signal_state)">{{ amdLogicalSignalLabel(variant.test_signal_state) }}</ToneBadge>
                  </TableCell>
                  <TableCell>
                    <ToneBadge :tone="toneForState(variant.terminal_state)">{{ amdStateLabel(amdLatestState({ latest_state: variant.terminal_state }, null)) }}</ToneBadge>
                  </TableCell>
                  <TableCell class="text-right tabular-nums">{{ integer(variant.passed_tests) }} / {{ integer(variant.tests) }}</TableCell>
                  <TableCell>
                    <a v-if="variant.job_url || variant.build_url" :href="variant.job_url || variant.build_url" target="_blank" rel="noopener" class="hover:underline">Open exact job ↗</a>
                    <span v-else class="text-muted-foreground">Unavailable</span>
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
        </div>
      </SheetContent>
    </Sheet>

    <AmdGroupSheet v-model:open="groupDetail.open" :row="groupDetail.row" :latest-build="latestBuild" />
    <BuildDetailSheet v-model:open="buildDetail.open" :build="buildDetail.build" />
  </div>
</template>
