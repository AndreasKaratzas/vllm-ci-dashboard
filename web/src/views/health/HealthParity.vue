<script setup lang="ts">
import { ChevronRight, SearchX } from '@lucide/vue'
import { computed } from 'vue'
import BarChart from '@/components/charts/BarChart.vue'
import type { SeriesSpec } from '@/components/charts/chart'
import ProportionBar, { type ProportionSegment } from '@/components/charts/ProportionBar.vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import type { SheetStack } from '@/composables/useSheetStack'
import { parityConfigNote, parityGapAreas, testGroupParityRows, type GapArea } from '@/domain/ciHealth'
import { integer, type Rec } from '@/domain/format'
import { currentTestGroupParity } from '@/domain/health'
import { statusVar } from '@/lib/palette'
import { ROUTE_MODE_COLOR, count, parityInventoryDetail, parityTone } from './details'

const props = defineProps<{ ops: Rec; sheets: SheetStack }>()

const parity = computed(() => currentTestGroupParity(props.ops.test_group_parity))
const summary = computed<Rec>(() => parity.value.summary || {})
const source = computed<Rec>(() => parity.value.source || {})
const bounded = computed(() => (parity.value.operations_publication_retention || {}).complete_relative_to_source === false)
const allRows = computed(() => testGroupParityRows(parity.value, 'all', 'all'))
const available = computed(() => allRows.value.length > 0 && Number(summary.value.upstream_logical_groups || 0) > 0)
const gapAreas = computed(() => parityGapAreas(parity.value))
const note = computed(() => parityConfigNote(summary.value, source.value))

const covered = computed(() => Number(summary.value.main_complete_groups || 0))
const applicable = computed(() => Number(summary.value.applicable_groups || 0))
const missing = computed(() => Number(summary.value.main_missing_groups || 0))
const gaps = computed(() => Number(summary.value.action_groups || 0))
const unsupported = computed(() => Number(summary.value.unsupported_groups || 0))
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
function openArea(area: string, gapsOnly: boolean) {
  const rows = testGroupParityRows(parity.value, gapsOnly ? 'action' : 'all', area)
  props.sheets.open(parityInventoryDetail(`area-${area}`, gapsOnly ? `${area} potential open gaps` : `${area} upstream test groups`, rows, source.value))
}

const segments = computed<ProportionSegment[]>(() => [
  { key: 'existing', label: 'covered on main', count: covered.value, color: statusVar('pass') },
  { key: 'action', label: 'potential open gaps', count: gaps.value, color: statusVar(parityTone('hard')) },
  { key: 'unsupported', label: 'not targeted', count: unsupported.value, color: statusVar('not-targeted') },
])
const areaSegments = (area: GapArea): ProportionSegment[] =>
  area.segments.map((segment) => ({ key: segment.label, label: segment.label, count: segment.count, color: statusVar(parityTone(segment.tone)) }))
const modeSegments = computed<ProportionSegment[]>(() => [
  { key: 'required', label: 'required', count: Number(summary.value.main_required_groups || 0), color: ROUTE_MODE_COLOR.required },
  { key: 'optional', label: 'optional only', count: Number(summary.value.main_optional_only_groups || 0), color: ROUTE_MODE_COLOR.optional },
  { key: 'soft fail', label: 'soft fail only', count: Number(summary.value.main_soft_fail_only_groups || 0), color: ROUTE_MODE_COLOR['soft fail'] },
])

// Coverage per upstream test area: where the gaps are first, then the largest areas.
const AREA_LIMIT = 12
const AREA_SERIES: SeriesSpec[] = [
  { key: 'existing', label: 'Covered on main', color: statusVar('pass') },
  { key: 'action', label: 'Potential open gaps', color: statusVar(parityTone('hard')) },
  { key: 'unsupported', label: 'Not targeted', color: statusVar('not-targeted') },
]
const areaRows = computed<Rec[]>(() =>
  (Array.isArray(parity.value.areas) ? (parity.value.areas as Rec[]) : [])
    .map((row) => ({ area: String(row.area), existing: Number(row.existing || 0), action: Number(row.action || 0), unsupported: Number(row.unsupported || 0), total: Number(row.total || 0) }))
    .sort((a, b) => b.action - a.action || b.total - a.total || a.area.localeCompare(b.area)),
)
const shownAreas = computed(() => areaRows.value.slice(0, AREA_LIMIT))

const inventoryLinks = computed(() => [
  { key: 'action' as const, label: 'Potential open gaps', count: gaps.value, hint: 'Upstream groups with no configured AMD route' },
  { key: 'unsupported' as const, label: 'Not-targeted groups', count: unsupported.value, hint: 'Hardware- or backend-specific; outside the denominator' },
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
          :footnote="`${count(missing, 'potential open gap')} on main`"
          headline="Inspect applicable groups"
          :action="{ label: 'Inspect applicable groups', onOpen: () => openInventory('applicable') }"
          data-testid="parity-ring"
        />
        <KpiCard
          label="Potential open gaps"
          :value="gaps"
          :status="gaps ? { tone: 'soft', label: 'Attention' } : undefined"
          :footnote="`Across ${count(gapAreas.length, 'test area')}`"
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
        >
          <ProportionBar :segments="modeSegments" :max="covered || undefined" label="Covered groups by configured route mode" :legend="false" />
        </KpiCard>
        <KpiCard
          label="Not targeted"
          :value="unsupported"
          footnote="Hardware- or backend-specific; outside the denominator"
          headline="Inspect not-targeted groups"
          :action="{ label: 'Inspect not-targeted groups', onOpen: () => openInventory('unsupported') }"
          data-testid="kpi-parity-unsupported"
        />
      </StatGrid>

      <div class="grid grid-cols-1 items-start gap-4 md:gap-6 @5xl/main:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] *:min-w-0">
        <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="parity-distribution">
          <CardHeader>
            <CardTitle as="h2">Current main CI · {{ integer(summary.upstream_logical_groups) }} logical groups</CardTitle>
            <CardDescription>
              Configured AMD coverage per upstream test area<template v-if="areaRows.length > AREA_LIMIT">, top {{ AREA_LIMIT }} of {{ areaRows.length }}</template>.
              Select an area for its groups.
            </CardDescription>
          </CardHeader>
          <CardContent class="flex flex-col gap-5">
            <ProportionBar :segments="segments" label="Upstream logical groups by configured coverage" :on-select="(s) => openInventory(s.key as InventoryKey)" />
            <BarChart
              v-if="shownAreas.length"
              :rows="shownAreas"
              :category="(row: Rec) => row.area"
              :series="AREA_SERIES"
              orientation="horizontal"
              label="Upstream logical groups per test area by configured coverage"
              :on-select="(row: Rec) => openArea(row.area, false)"
            />
          </CardContent>
          <CardFooter>
            <p class="text-xs text-muted-foreground" data-testid="parity-config-note">{{ note }}</p>
          </CardFooter>
        </Card>

        <div class="flex min-w-0 flex-col gap-4 md:gap-6">
          <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="parity-gap-areas">
            <CardHeader>
              <CardTitle as="h2">Potential open gaps by test area</CardTitle>
              <CardDescription>Configuration attention, not runtime failures</CardDescription>
            </CardHeader>
            <CardContent class="flex flex-col gap-3">
              <EmptyState v-if="!gapAreas.length" title="No potential open gaps" description="Every applicable upstream group has a configured AMD route." />
              <button
                v-for="(area, index) in gapAreas.slice(0, 6)"
                :key="area.label"
                type="button"
                class="group/area flex min-w-0 flex-col gap-2.5 rounded-lg border bg-linear-to-t from-status-soft/6 to-card p-3.5 text-left transition-[border-color,box-shadow,transform] duration-150 hover:border-foreground/20 hover:shadow-sm focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:active:scale-[.99]"
                :style="{ animationDelay: `${index * 30}ms` }"
                data-testid="gap-area"
                @click="openArea(area.label, true)"
              >
                <div class="flex items-start justify-between gap-2">
                  <strong class="min-w-0 font-semibold" data-testid="gap-area-label">{{ area.label }}</strong>
                  <span class="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-status-soft-fg" data-testid="gap-area-count">{{ area.countLabel }}<ChevronRight class="size-3.5 transition-transform group-hover/area:translate-x-0.5" aria-hidden="true" /></span>
                </div>
                <ProportionBar :segments="areaSegments(area)" :max="area.total" :label="`${area.label} coverage`" :legend="false" />
                <p class="truncate text-xs text-muted-foreground">
                  {{ area.rows.slice(0, 2).map((group) => group.title).join(' · ') }}<template v-if="area.rows.length > 2"> · +{{ integer(area.rows.length - 2) }} more</template>
                </p>
              </button>
              <p v-if="gapAreas.length > 6" class="text-xs text-muted-foreground">+{{ count(gapAreas.length - 6, 'more area') }} in the open-gap inventory</p>
            </CardContent>
          </Card>

          <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300">
            <CardHeader>
              <CardTitle as="h2">Inventories</CardTitle>
              <CardDescription>Each test group opens its ROCm assessment</CardDescription>
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
      </div>
    </template>
  </div>
</template>
