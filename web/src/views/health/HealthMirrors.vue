<script setup lang="ts">
import { ChevronRight, ExternalLink, GitFork } from '@lucide/vue'
import { computed } from 'vue'
import Donut, { type DonutSlice } from '@/components/charts/Donut.vue'
import Legend from '@/components/charts/Legend.vue'
import ProportionBar, { type ProportionSegment } from '@/components/charts/ProportionBar.vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import DataTable from '@/components/table/DataTable.vue'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import type { SheetStack } from '@/composables/useSheetStack'
import { integer, type Rec } from '@/domain/format'
import { amdMirrorInventoryView, mirrorAreaBreakdown, mirrorInventorySearchText, type MirrorMode } from '@/domain/mirrors'
import { hardwareColor } from '@/lib/palette'
import { MIRROR_MODE_COLOR, mirrorAreasDetail, mirrorDetail, mirrorInventoryColumns, mirrorInventoryFilters } from './details'

const props = defineProps<{ ops: Rec; sheets: SheetStack }>()

const inventory = computed<Rec>(() => (props.ops.test_group_parity || {}).mirror_inventory || {})
const view = computed(() => amdMirrorInventoryView(inventory.value))

const slices = computed<DonutSlice[]>(() =>
  view.value.hero.legend.map((row) => ({ key: row.id, label: row.label, value: Number(row.count), color: hardwareColor(row.id) })),
)
const MODES: { key: MirrorMode; label: string; field: 'required' | 'optional' | 'softFail'; hint: string }[] = [
  { key: 'required', label: 'Required', field: 'required', hint: 'Block the build when they fail' },
  { key: 'optional', label: 'Optional', field: 'optional', hint: 'Run only when selected' },
  { key: 'soft fail', label: 'Soft fail', field: 'softFail', hint: 'Failures do not block the build' },
]
const modeSegments = computed<ProportionSegment[]>(() =>
  MODES.map((mode) => ({ key: mode.key, label: mode.label.toLowerCase(), count: view.value.modes[mode.key], color: MIRROR_MODE_COLOR[mode.key] })),
)
const modeLegend = MODES.map((mode) => ({ key: mode.key, label: mode.label, color: MIRROR_MODE_COLOR[mode.key] }))
const maxAreaCount = computed(() => Math.max(1, ...(view.value.areaChart?.rows ?? []).map((row) => Number(row.count || 0))))
const areaSegments = (row: Rec): ProportionSegment[] => [
  { key: 'required', label: 'required', count: Number(row.required || 0), color: MIRROR_MODE_COLOR.required },
  { key: 'optional', label: 'optional', count: Number(row.optional || 0), color: MIRROR_MODE_COLOR.optional },
  { key: 'soft fail', label: 'soft fail', count: Number(row.soft_fail || 0), color: MIRROR_MODE_COLOR['soft fail'] },
]

const columns = computed(() => mirrorInventoryColumns(inventory.value))
const filters = computed(() => mirrorInventoryFilters(view.value.groups))
const total = computed(() => view.value.state.total)
const share = (count: number) => (total.value ? `${((count / total.value) * 100).toFixed(0)}% of groups` : '')
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <StatGrid :cols="4">
      <KpiCard
        label="AMD mirror groups"
        :value="total === null ? null : view.hero.totalDisplay"
        :footnote="view.hero.meta"
        data-testid="mirror-hero"
      >
        <ProportionBar v-if="view.configuration.track" :segments="modeSegments" :label="view.configuration.trackLabel" :legend="false" />
      </KpiCard>
      <KpiCard
        v-for="mode in MODES"
        :key="mode.key"
        :label="mode.label"
        :value="view.configuration[mode.field]"
        :footnote="`${mode.hint}${share(view.modes[mode.key]) ? ` · ${share(view.modes[mode.key])}` : ''}`"
        data-testid="mirror-mode"
      >
        <ProportionBar
          :segments="[{ key: mode.key, label: mode.key, count: view.modes[mode.key], color: MIRROR_MODE_COLOR[mode.key] }]"
          :max="view.groups.length || 1"
          :label="`${mode.label} share of published mirror groups`"
          :legend="false"
        />
      </KpiCard>
    </StatGrid>

    <Callout v-if="view.coverageNote" tone="soft" title="Partial detail" data-testid="mirror-coverage-note">{{ view.coverageNote }}</Callout>

    <div class="grid grid-cols-1 items-start gap-4 md:gap-6 @5xl/main:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] *:min-w-0">
      <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="mirror-families">
        <CardHeader>
          <CardTitle as="h2">GPU families</CardTitle>
          <CardDescription>Mirror groups by AMD GPU family; newer generations are stronger</CardDescription>
        </CardHeader>
        <CardContent class="flex flex-col gap-5">
          <EmptyState v-if="!slices.length" :icon="GitFork" title="Hardware breakdown unavailable" :description="view.hero.meta" />
          <div v-else class="flex flex-col items-center gap-4 sm:flex-row" data-testid="mirror-hardware-legend">
            <Donut :slices="slices" size="md" :legend="false" :center-value="view.hero.totalDisplay" center-label="mirror groups" :label="view.hero.ariaLabel" />
            <ul class="flex w-full min-w-0 flex-col gap-1 text-sm">
              <li v-for="slice in slices" :key="slice.key" class="flex items-center gap-2 rounded-md px-2 py-1" data-testid="mirror-hardware">
                <span class="size-2.5 shrink-0 rounded-full" :style="{ backgroundColor: slice.color }" aria-hidden="true" />
                <span class="font-semibold tabular-nums">{{ integer(slice.value) }}</span>
                <span class="min-w-0 flex-1 text-muted-foreground">{{ slice.label }}</span>
              </li>
            </ul>
          </div>
          <dl class="grid grid-cols-2 gap-3 border-t pt-4">
            <div class="flex flex-col" data-testid="mirror-fact">
              <dt class="order-2 text-sm text-muted-foreground">source files</dt>
              <dd class="order-1 text-xl font-semibold tabular-nums"><strong class="font-semibold">{{ view.configuration.files }}</strong></dd>
            </div>
            <div class="flex flex-col" data-testid="mirror-fact">
              <dt class="order-2 text-sm text-muted-foreground">GPU families</dt>
              <dd class="order-1 text-xl font-semibold tabular-nums"><strong class="font-semibold">{{ view.configuration.families }}</strong></dd>
            </div>
          </dl>
          <p v-if="view.configuration.lowerBound" class="text-xs text-muted-foreground">File, mode, and hardware breakdowns reflect published detail rows.</p>
        </CardContent>
      </Card>

      <Card v-if="view.areaChart" class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="mirror-areas">
        <CardHeader>
          <CardTitle as="h2">Where the mirror groups live</CardTitle>
          <CardDescription data-testid="mirror-areas-subtitle">{{ view.areaChart.subtitle }}</CardDescription>
        </CardHeader>
        <CardContent class="flex flex-col gap-2 px-3">
          <Legend :items="modeLegend" class="px-3 pb-1" />
          <component
            :is="row.source_url ? 'a' : 'div'"
            v-for="row in view.areaChart.rows"
            :key="row.yaml_file"
            :href="row.source_url || undefined"
            :target="row.source_url ? '_blank' : undefined"
            :rel="row.source_url ? 'noopener' : undefined"
            class="group/area grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)_auto] items-center gap-3 rounded-lg px-3 py-2 transition-colors hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
            :aria-label="`${row.area}: ${integer(row.count)} mirror groups, ${integer(row.required)} required and ${integer(row.optional)} optional and ${integer(row.soft_fail)} soft fail${row.source_url ? '. Opens pinned YAML in a new tab.' : ''}`"
            data-testid="mirror-area"
          >
            <span class="flex min-w-0 flex-col">
              <strong class="truncate text-sm font-medium" data-testid="mirror-area-name">{{ row.area }}</strong>
              <small class="truncate font-mono text-xs text-muted-foreground" data-testid="mirror-area-file">{{ row.yaml_file.split('/').pop() }}</small>
            </span>
            <ProportionBar :segments="areaSegments(row)" :max="maxAreaCount" :label="`${row.area} mirror modes`" :legend="false" />
            <span class="flex items-center gap-1.5">
              <span class="sr-only" data-testid="mirror-area-breakdown">{{ mirrorAreaBreakdown(row) }}</span>
              <strong class="w-8 text-right font-semibold tabular-nums" data-testid="mirror-area-count">{{ integer(row.count) }}</strong>
              <ExternalLink v-if="row.source_url" class="size-3.5 text-muted-foreground opacity-0 transition-opacity group-hover/area:opacity-100" aria-hidden="true" />
            </span>
          </component>
        </CardContent>
        <CardFooter>
          <Button variant="outline" size="sm" @click="sheets.open(mirrorAreasDetail(view.areas))">{{ view.areaChart.browseLabel }} <ChevronRight /></Button>
        </CardFooter>
      </Card>
    </div>

    <Card class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300" data-testid="mirror-inventory">
      <CardHeader>
        <CardTitle as="h2">AMD mirror inventory</CardTitle>
        <CardDescription data-testid="mirror-inventory-subtitle">{{ view.preview.subtitle }}</CardDescription>
      </CardHeader>
      <CardContent>
        <EmptyState v-if="!view.groups.length" :icon="GitFork" title="No published AMD mirror groups are available." />
        <DataTable
          v-else
          :columns="columns"
          :rows="view.groups"
          :filters="filters"
          :search-text="mirrorInventorySearchText"
          :on-row-click="(row: Rec) => sheets.open(mirrorDetail(row, inventory))"
          search-placeholder="Filter test group, area, YAML file, device, mode, or key"
          label="AMD mirror inventory"
          show-total
        />
      </CardContent>
    </Card>
  </div>
</template>
