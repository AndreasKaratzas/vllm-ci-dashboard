<script setup lang="ts">
// CI Health "AMD mirrors" (classic ops_health_view=mirrors, amd-mirror-inventory.js render).
import type { ColumnDef } from '@tanstack/vue-table'
import { VisDonut, VisSingleContainer } from '@unovis/vue'
import { ChevronDown } from '@lucide/vue'
import { computed, h, ref } from 'vue'
import BrowserSheet from '@/components/health/BrowserSheet.vue'
import MirrorDetailSheet from '@/components/health/MirrorDetailSheet.vue'
import MirrorModeTrack, { MODE_FILL } from '@/components/health/MirrorModeTrack.vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import ViewState from '@/components/ops/ViewState.vue'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { useOperationSections } from '@/composables/useOperations'
import { integer, value, type Rec } from '@/domain/format'
import { hardwareDisplayLabel } from '@/domain/health'
import {
  amdMirrorGateMode,
  amdMirrorInventoryView,
  amdMirrorLabel,
  amdMirrorModeTone,
  amdMirrorSourceUrl,
  mirrorAreaBreakdown,
  mirrorAreaSearchText,
  mirrorDevicesLabel,
  mirrorInventorySearchText,
  mirrorPreviewMeta,
} from '@/domain/mirrors'

const { data: ops, error, loading, retry } = useOperationSections(['nightly', 'amd_test_health', 'test_group_parity'])
const inventory = computed<Rec>(() => (ops.value?.test_group_parity || {}).mirror_inventory || {})
const view = computed(() => amdMirrorInventoryView(inventory.value))

// Classic mirrorPalette: five chart colors in hardware-row order.
const PALETTE = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)']
const paletteFor = (index: number) => PALETTE[index % 5]!
type Slice = { label: string; count: number; index: number }
const slices = computed<Slice[]>(() =>
  view.value.hero.breakdownComplete ? view.value.hardwareRows.map((row, index) => ({ label: row.label, count: row.count, index })) : [],
)
const donutData = computed<Slice[]>(() => (slices.value.length ? slices.value : [{ label: 'unavailable', count: 1, index: -1 }]))

const detail = ref<{ open: boolean; row: Rec | null }>({ open: false, row: null })
const openDetail = (row: Rec) => (detail.value = { open: true, row })
const areaBrowserOpen = ref(false)
const inventoryBrowserOpen = ref(false)

const external = (label: string, url: string) => h('a', { href: url, target: '_blank', rel: 'noopener', class: 'font-medium hover:underline' }, label)
const areaColumns: ColumnDef<Rec, unknown>[] = [
  {
    id: 'file',
    header: 'Test-area file',
    cell: ({ row }) =>
      row.original.source_url
        ? external(`${row.original.yaml_file.split('/').pop()} ↗`, row.original.source_url)
        : h('span', { class: 'font-medium' }, row.original.yaml_file),
  },
  { id: 'area', header: 'Test area', cell: ({ row }) => value(row.original.area) },
  { id: 'count', header: 'Mirror groups', cell: ({ row }) => h('span', { class: 'tabular-nums' }, integer(row.original.count)) },
  { id: 'required', header: 'Required', cell: ({ row }) => h('span', { class: 'tabular-nums' }, integer(row.original.required)) },
  { id: 'optional', header: 'Optional', cell: ({ row }) => h('span', { class: 'tabular-nums' }, integer(row.original.optional)) },
  { id: 'soft', header: 'Soft fail', cell: ({ row }) => h('span', { class: 'tabular-nums' }, integer(row.original.soft_fail)) },
  { id: 'devices', header: 'AMD devices', cell: ({ row }) => mirrorDevicesLabel(row.original) },
]
const inventoryColumns = computed<ColumnDef<Rec, unknown>[]>(() => [
  {
    id: 'label',
    header: 'Source test group',
    cell: ({ row }) =>
      h('button', { type: 'button', class: 'text-left font-medium hover:underline', onClick: () => openDetail(row.original) }, amdMirrorLabel(row.original)),
  },
  { id: 'area', header: 'Test area', cell: ({ row }) => value(row.original.area) },
  { id: 'device', header: 'AMD device', cell: ({ row }) => h(ToneBadge, { tone: 'info' }, () => hardwareDisplayLabel(row.original.device)) },
  {
    id: 'width',
    header: 'GPU width',
    cell: ({ row }) => (row.original.num_devices === null || row.original.num_devices === undefined ? '—' : integer(row.original.num_devices)),
  },
  { id: 'mode', header: 'Mode', cell: ({ row }) => h(ToneBadge, { tone: amdMirrorModeTone(row.original) }, () => amdMirrorGateMode(row.original)) },
  {
    id: 'source',
    header: 'Source',
    cell: ({ row }) => {
      const url = amdMirrorSourceUrl(inventory.value, row.original)
      return url ? external('Pinned YAML ↗', url) : h('span', { class: 'text-muted-foreground' }, '-')
    },
  },
])
const maxAreaCount = computed(() => Math.max(...(view.value.areaChart?.rows ?? []).map((row) => Number(row.count || 0)), 1))
const LEGEND = [
  { key: 'required', label: 'Required' },
  { key: 'optional', label: 'Optional' },
  { key: 'soft_fail', label: 'Soft fail' },
]
const methodOpen = ref(false)
</script>

<template>
  <ViewState :loading="loading && !ops" :error="error" @retry="retry">
    <div v-if="ops" class="flex flex-col gap-4 md:gap-6">
      <div class="grid gap-4 md:gap-6 @4xl/main:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <!-- Count hero with the hardware-family donut. -->
        <Card data-testid="mirror-hero">
          <CardContent class="flex flex-col items-center gap-6 sm:flex-row">
            <div class="relative size-40 shrink-0" role="img" :aria-label="view.hero.ariaLabel">
              <VisSingleContainer :data="donutData" class="size-full">
                <VisDonut
                  :value="(d: Slice) => d.count"
                  :color="(d: Slice) => (d.index < 0 ? 'var(--muted)' : paletteFor(d.index))"
                  :arc-width="18"
                  :pad-angle="slices.length > 1 ? 0.02 : 0"
                  :corner-radius="3"
                />
              </VisSingleContainer>
              <div class="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                <strong class="text-3xl font-semibold tabular-nums" data-testid="mirror-total">{{ view.hero.totalDisplay }}</strong>
                <span class="text-xs text-muted-foreground">mirror groups</span>
              </div>
            </div>
            <div class="flex min-w-0 flex-col gap-2">
              <span class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">AMD mirror configuration on main</span>
              <h3 class="text-xl font-semibold">Configured AMD mirror groups</h3>
              <p class="text-sm text-muted-foreground" data-testid="mirror-hero-meta">{{ view.hero.meta }}</p>
              <div v-if="view.hero.legend.length" class="flex flex-wrap gap-2" data-testid="mirror-hardware-legend">
                <span v-for="(row, index) in view.hero.legend" :key="row.id" class="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-sm" data-testid="mirror-hardware">
                  <span class="size-2.5 rounded-full" :style="{ background: paletteFor(index) }" />
                  <strong class="tabular-nums">{{ integer(row.count) }}</strong>
                  <span class="text-muted-foreground">{{ row.label }}</span>
                </span>
              </div>
            </div>
          </CardContent>
        </Card>

        <!-- Configuration shape. -->
        <Card data-testid="mirror-configuration">
          <CardHeader>
            <CardDescription class="text-xs font-semibold tracking-wide uppercase">Configuration shape</CardDescription>
          </CardHeader>
          <CardContent class="flex flex-col gap-4">
            <div class="grid grid-cols-3 gap-3">
              <div v-for="item in [{ key: 'required', label: 'required', value: view.configuration.required }, { key: 'optional', label: 'optional', value: view.configuration.optional }, { key: 'soft_fail', label: 'soft fail', value: view.configuration.softFail }]" :key="item.key" class="flex flex-col" data-testid="mirror-mode">
                <strong class="text-2xl font-semibold tabular-nums">{{ item.value }}</strong>
                <span class="flex items-center gap-1.5 text-sm text-muted-foreground"><span class="size-2 rounded-full" :class="MODE_FILL[item.key]" />{{ item.label }}</span>
              </div>
            </div>
            <MirrorModeTrack v-if="view.configuration.track" :segments="view.configuration.track" :aria-label="view.configuration.trackLabel" />
            <div class="grid grid-cols-2 gap-3 border-t pt-3">
              <div class="flex flex-col" data-testid="mirror-fact">
                <strong class="text-lg tabular-nums">{{ view.configuration.files }}</strong><span class="text-sm text-muted-foreground">source files</span>
              </div>
              <div class="flex flex-col" data-testid="mirror-fact">
                <strong class="text-lg tabular-nums">{{ view.configuration.families }}</strong><span class="text-sm text-muted-foreground">GPU families</span>
              </div>
            </div>
            <p v-if="view.configuration.lowerBound" class="text-xs text-muted-foreground">File, mode, and hardware breakdowns reflect published detail rows.</p>
          </CardContent>
        </Card>
      </div>

      <Alert v-if="view.coverageNote" class="border-warning/40 bg-warning/10" data-testid="mirror-coverage-note">
        <AlertDescription class="text-warning">{{ view.coverageNote }}</AlertDescription>
      </Alert>

      <div class="grid gap-4 md:gap-6 @4xl/main:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <Card v-if="view.areaChart" data-testid="mirror-areas">
          <CardHeader>
            <CardTitle>Where the mirror groups live</CardTitle>
            <CardDescription data-testid="mirror-areas-subtitle">{{ view.areaChart.subtitle }}</CardDescription>
          </CardHeader>
          <CardContent class="flex flex-col gap-3">
            <div class="flex gap-4 text-xs text-muted-foreground">
              <span v-for="item in LEGEND" :key="item.key" class="inline-flex items-center gap-1.5"><span class="size-2 rounded-full" :class="MODE_FILL[item.key]" />{{ item.label }}</span>
            </div>
            <component
              :is="row.source_url ? 'a' : 'div'"
              v-for="row in view.areaChart.rows"
              :key="row.yaml_file"
              :href="row.source_url || undefined"
              :target="row.source_url ? '_blank' : undefined"
              :rel="row.source_url ? 'noopener' : undefined"
              class="grid grid-cols-[minmax(0,11rem)_minmax(0,1fr)_auto] items-center gap-3 rounded-md px-1 py-1 hover:bg-accent/50"
              :aria-label="`${row.area}: ${integer(row.count)} mirror groups, ${integer(row.required)} required and ${integer(row.optional)} optional and ${integer(row.soft_fail)} soft fail${row.source_url ? '. Opens pinned YAML in a new tab.' : ''}`"
              data-testid="mirror-area"
            >
              <span class="flex min-w-0 flex-col">
                <strong class="truncate text-sm" data-testid="mirror-area-name">{{ row.area }}</strong>
                <small class="truncate font-mono text-xs text-muted-foreground" data-testid="mirror-area-file">{{ row.yaml_file.split('/').pop() }}</small>
              </span>
              <span class="flex h-2.5 overflow-hidden rounded-full bg-muted">
                <span class="flex h-full" :style="{ width: `${(Number(row.count || 0) / maxAreaCount) * 100}%` }">
                  <span v-for="part in [['required', row.required], ['optional', row.optional], ['soft_fail', row.soft_fail]]" :key="part[0]" :class="MODE_FILL[part[0]]" :style="{ width: `${(Number(part[1] || 0) / Math.max(1, Number(row.count || 0))) * 100}%` }" />
                </span>
              </span>
              <span class="flex items-baseline gap-2">
                <span class="hidden font-mono text-xs forced-colors:inline" aria-hidden="true" data-testid="mirror-area-breakdown">{{ mirrorAreaBreakdown(row) }}</span>
                <strong class="w-8 text-right tabular-nums" data-testid="mirror-area-count">{{ integer(row.count) }}</strong>
              </span>
            </component>
          </CardContent>
          <CardFooter>
            <Button variant="outline" size="sm" @click="areaBrowserOpen = true">{{ view.areaChart.browseLabel }}</Button>
          </CardFooter>
        </Card>

        <Card data-testid="mirror-inventory">
          <CardHeader>
            <CardTitle>Configured mirror groups</CardTitle>
            <CardDescription data-testid="mirror-inventory-subtitle">{{ view.preview.subtitle }}</CardDescription>
          </CardHeader>
          <CardContent class="flex flex-col divide-y">
            <button
              v-for="row in view.preview.rows"
              :key="`${row.yaml_file}-${row.yaml_index}-${row.key}`"
              type="button"
              class="grid grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-3 py-2.5 text-left hover:bg-accent/50"
              :aria-label="`Inspect ${amdMirrorLabel(row)}, ${hardwareDisplayLabel(row.device)}, ${amdMirrorGateMode(row)}`"
              data-testid="mirror-preview"
              @click="openDetail(row)"
            >
              <ToneBadge tone="info">{{ hardwareDisplayLabel(row.device) }}</ToneBadge>
              <span class="flex min-w-0 flex-col">
                <strong class="truncate text-sm" data-testid="mirror-preview-label">{{ amdMirrorLabel(row) }}</strong>
                <small class="truncate text-xs text-muted-foreground" data-testid="mirror-preview-meta">{{ mirrorPreviewMeta(row) }}</small>
              </span>
              <ToneBadge :tone="amdMirrorModeTone(row)" data-testid="mirror-preview-mode">{{ amdMirrorGateMode(row) }}</ToneBadge>
              <span class="text-muted-foreground" aria-hidden="true">→</span>
            </button>
            <p v-if="!view.groups.length" class="py-6 text-center text-sm text-muted-foreground">No published AMD mirror groups are available.</p>
          </CardContent>
          <CardFooter v-if="view.preview.browseLabel">
            <Button size="sm" @click="inventoryBrowserOpen = true">{{ view.preview.browseLabel }}</Button>
          </CardFooter>
        </Card>
      </div>

      <Card class="py-4">
        <CardContent>
          <button type="button" class="flex w-full items-center justify-between text-left text-sm font-medium" :aria-expanded="methodOpen" @click="methodOpen = !methodOpen">
            How this live count is built
            <ChevronDown class="size-4 transition-transform" :class="methodOpen ? 'rotate-180' : ''" />
          </button>
          <div v-if="methodOpen" class="mt-3 flex flex-col gap-2 text-sm text-muted-foreground">
            <p>The dashboard pins vLLM main to one commit and scans every .buildkite/test_areas/*.yaml file.</p>
            <p>One top-level YAML step with a non-empty mirror.amd mapping counts once. Optional and soft-fail mirrors are included; parallelism and shard templates do not multiply the total.</p>
            <p>This physical configuration count is separate from runtime logical groups and current upstream logical route coverage.</p>
            <p v-if="view.commit">
              <a :href="view.commitUrl" target="_blank" rel="noopener" class="font-medium text-foreground hover:underline">Open scanned commit {{ String(view.commit).slice(0, 12) }} ↗</a>
            </p>
          </div>
        </CardContent>
      </Card>

      <BrowserSheet
        v-model:open="areaBrowserOpen"
        title="AMD mirrors by test-area file"
        subtitle="Physical mirror.amd declarations grouped by their pinned source YAML file"
        :rows="view.areas"
        :columns="areaColumns"
        :search-text="mirrorAreaSearchText"
        search-placeholder="Filter test area, YAML file, device, or queue"
      />
      <BrowserSheet
        v-model:open="inventoryBrowserOpen"
        title="AMD mirror inventory"
        :subtitle="view.inventoryBrowserSubtitle"
        :rows="view.groups"
        :columns="inventoryColumns"
        :search-text="mirrorInventorySearchText"
        search-placeholder="Filter test group, area, YAML file, device, mode, or key"
      />
      <MirrorDetailSheet v-model:open="detail.open" :row="detail.row" :inventory="inventory" />
    </div>
  </ViewState>
</template>
