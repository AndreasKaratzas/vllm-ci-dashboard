<script setup lang="ts">
import { ChevronRight, SearchX } from '@lucide/vue'
import { computed } from 'vue'
import ProportionBar, { type ProportionSegment } from '@/components/charts/ProportionBar.vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { SheetStack } from '@/composables/useSheetStack'
import { parityGapAreas, testGroupParityRows, type GapArea } from '@/domain/ciHealth'
import { integer, type Rec } from '@/domain/format'
import { currentTestGroupParity } from '@/domain/health'
import { statusVar } from '@/lib/palette'
import { parityInventoryDetail, parityTone } from './details'

const props = defineProps<{ ops: Rec; sheets: SheetStack }>()

const parity = computed(() => currentTestGroupParity(props.ops.test_group_parity))
const summary = computed<Rec>(() => parity.value.summary || {})
const source = computed<Rec>(() => parity.value.source || {})
const bounded = computed(() => (parity.value.operations_publication_retention || {}).complete_relative_to_source === false)
const allRows = computed(() => testGroupParityRows(parity.value, 'all', 'all'))
const available = computed(() => allRows.value.length > 0 && Number(summary.value.upstream_logical_groups || 0) > 0)
const gapAreas = computed(() => parityGapAreas(parity.value))

const covered = computed(() => Number(summary.value.main_complete_groups || 0))
const applicable = computed(() => Number(summary.value.applicable_groups || 0))
const missing = computed(() => Number(summary.value.main_missing_groups || 0))
const requiredRate = computed(() => {
  const rate = Number(summary.value.main_required_rate_pct)
  return summary.value.main_required_rate_pct === undefined || !Number.isFinite(rate) ? null : rate
})

const INVENTORIES = {
  applicable: { title: 'Applicable upstream test groups', rows: () => allRows.value.filter((row) => row.state !== 'unsupported') },
  existing: { title: 'Covered on main', rows: () => testGroupParityRows(parity.value, 'existing', 'all') },
  action: { title: 'Potential open gaps', rows: () => testGroupParityRows(parity.value, 'action', 'all') },
  unsupported: { title: 'Not targeted / unsupported', rows: () => testGroupParityRows(parity.value, 'unsupported', 'all') },
  all: { title: 'Complete current upstream inventory', rows: () => allRows.value },
} as const
type InventoryKey = keyof typeof INVENTORIES

function openInventory(key: InventoryKey) {
  const inventory = INVENTORIES[key]
  props.sheets.open(parityInventoryDetail(key, inventory.title, inventory.rows(), source.value))
}
function openArea(area: GapArea) {
  props.sheets.open(parityInventoryDetail(`area-${area.label}`, `${area.label} potential open gaps`, area.rows, source.value))
}

const segments = computed<ProportionSegment[]>(() => [
  { key: 'existing', label: 'covered on main', count: covered.value, color: statusVar('pass') },
  { key: 'action', label: 'potential open gaps', count: Number(summary.value.action_groups || 0), color: statusVar(parityTone('hard')) },
  { key: 'unsupported', label: 'not targeted', count: Number(summary.value.unsupported_groups || 0), color: statusVar('not-targeted') },
])
const areaSegments = (area: GapArea): ProportionSegment[] =>
  area.segments.map((segment) => ({ key: segment.label, label: segment.label, count: segment.count, color: statusVar(parityTone(segment.tone)) }))

const inventoryLinks = computed(() => [
  { key: 'action' as const, label: 'Potential open gaps', count: Number(summary.value.action_groups || 0), hint: 'Upstream groups with no configured AMD route' },
  { key: 'unsupported' as const, label: 'Not-targeted groups', count: Number(summary.value.unsupported_groups || 0), hint: 'Hardware- or backend-specific; outside the denominator' },
  { key: 'all' as const, label: 'Complete inventory', count: Number(summary.value.upstream_logical_groups || 0), hint: 'Every current upstream logical test group' },
])
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <Callout v-if="bounded" tone="soft" title="Storage-bounded snapshot">Parity rows are storage-bounded; counts are lower bounds.</Callout>
    <Card v-if="!available">
      <CardContent>
        <EmptyState :icon="SearchX" title="Upstream parity is unavailable" description="The current main CI test-group parity inventory is missing from this snapshot." />
      </CardContent>
    </Card>
    <template v-else>
      <StatGrid :cols="4">
        <KpiCard
          variant="ring"
          label="Upstream parity"
          :value="`${integer(covered)} covered`"
          :ratio="{ current: covered, total: applicable }"
          :status="missing ? { tone: 'soft', label: `${integer(missing)} open` } : { tone: 'pass', label: 'Complete' }"
          :footnote="`${integer(missing)} potential open gaps remain`"
          headline="Inspect applicable groups"
          :action="{ label: 'Inspect applicable groups', onOpen: () => openInventory('applicable') }"
          data-testid="parity-ring"
        />
        <KpiCard
          label="Potential open gaps"
          :value="Number(summary.action_groups || 0)"
          :status="Number(summary.action_groups || 0) ? { tone: 'soft', label: 'Attention' } : undefined"
          :footnote="`Across ${integer(gapAreas.length)} test areas`"
          headline="Inspect open gaps"
          :action="{ label: 'Inspect potential open gaps', onOpen: () => openInventory('action') }"
          data-testid="kpi-parity-gaps"
        />
        <KpiCard
          label="Required route coverage"
          :value="requiredRate"
          :format="(v: number) => `${v.toFixed(1)}%`"
          :footnote="`${integer(summary.main_required_groups)} required · ${integer(summary.main_optional_only_groups)} optional only · ${integer(summary.main_soft_fail_only_groups)} soft fail only`"
          data-testid="kpi-parity-required"
        />
        <KpiCard
          label="Not targeted"
          :value="Number(summary.unsupported_groups || 0)"
          footnote="Hardware- or backend-specific groups outside the denominator"
          headline="Inspect not-targeted groups"
          :action="{ label: 'Inspect not-targeted groups', onOpen: () => openInventory('unsupported') }"
          data-testid="kpi-parity-unsupported"
        />
      </StatGrid>

      <div class="grid grid-cols-1 items-start gap-4 md:gap-6 @5xl/main:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] *:min-w-0">
        <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="parity-distribution">
          <CardHeader>
            <CardTitle as="h2">Current main CI · {{ integer(summary.upstream_logical_groups) }} logical groups</CardTitle>
            <CardDescription>{{ integer(summary.unsupported_groups) }} hardware- or backend-specific groups are classified outside the parity denominator.</CardDescription>
          </CardHeader>
          <CardContent class="flex flex-col gap-4">
            <ProportionBar :segments="segments" label="Upstream logical groups by configured coverage" :on-select="(s) => openInventory(s.key as InventoryKey)" />
          </CardContent>
        </Card>

        <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300">
          <CardHeader>
            <CardTitle as="h2">Inventories</CardTitle>
            <CardDescription>Searchable tables; each test group opens its assessment.</CardDescription>
          </CardHeader>
          <CardContent class="flex flex-col gap-1 px-3">
            <button
              v-for="item in inventoryLinks"
              :key="item.key"
              type="button"
              class="group/link flex items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none active:bg-accent/80"
              @click="openInventory(item.key)"
            >
              <span class="flex min-w-0 flex-1 flex-col">
                <span class="text-sm font-medium">{{ item.label }}</span>
                <span class="text-xs text-muted-foreground">{{ item.hint }}</span>
              </span>
              <span class="text-sm font-semibold tabular-nums" :class="!item.count && 'text-muted-foreground'">{{ integer(item.count) }}</span>
              <ChevronRight class="size-4 text-muted-foreground transition-transform group-hover/link:translate-x-0.5" aria-hidden="true" />
            </button>
          </CardContent>
        </Card>
      </div>

      <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300">
        <CardHeader>
          <CardTitle as="h2">Potential open gaps by test area</CardTitle>
          <CardDescription>Coverage gaps are configuration attention items, not runtime failures. Select an area for its groups.</CardDescription>
        </CardHeader>
        <CardContent>
          <EmptyState v-if="!gapAreas.length" title="No potential open gaps" description="Every applicable upstream group has a configured AMD route." />
          <div v-else class="grid grid-cols-1 gap-3 @3xl/main:grid-cols-2 @6xl/main:grid-cols-3">
            <button
              v-for="(area, index) in gapAreas"
              :key="area.label"
              type="button"
              class="group/area flex min-w-0 flex-col gap-3 rounded-lg border bg-linear-to-t from-status-soft/5 to-card p-4 text-left transition-[border-color,box-shadow,transform] duration-150 hover:border-foreground/20 hover:shadow-sm focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:active:scale-[.99]"
              :style="{ animationDelay: `${index * 30}ms` }"
              data-testid="gap-area"
              @click="openArea(area)"
            >
              <span class="flex items-start justify-between gap-2">
                <strong class="font-semibold" data-testid="gap-area-label">{{ area.label }}</strong>
                <span class="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-status-soft-fg" data-testid="gap-area-count">
                  {{ area.countLabel }}<ChevronRight class="size-3.5 transition-transform group-hover/area:translate-x-0.5" aria-hidden="true" />
                </span>
              </span>
              <ProportionBar :segments="areaSegments(area)" :max="area.total" :label="`${area.label} coverage`" :legend="false" />
              <ul class="flex flex-col gap-0.5 text-xs text-muted-foreground">
                <li v-for="group in area.rows.slice(0, 3)" :key="group.id" class="truncate">
                  <span class="font-mono">#{{ integer(group.id) }}</span> {{ group.title }}
                </li>
                <li v-if="area.rows.length > 3">+{{ integer(area.rows.length - 3) }} more</li>
              </ul>
            </button>
          </div>
        </CardContent>
      </Card>
    </template>
  </div>
</template>
