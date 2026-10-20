<script setup lang="ts">
import { ChevronDown, History } from '@lucide/vue'
import { computed, ref } from 'vue'
import HwDot from '@/components/feedback/HwDot.vue'
import DeltaBadge from '@/components/stat/DeltaBadge.vue'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import type { Rec } from '@/domain/format'
import { perfConfigTitle, type PerfConfigSection } from '@/domain/perf'
import { toneClasses } from '@/lib/palette'
import { blockDelta, deviceColor } from './details'
import PerfMetricTile from './PerfMetricTile.vue'
import PerfSeriesChart from './PerfSeriesChart.vue'

const props = defineProps<{ section: PerfConfigSection; defaultOpen?: boolean }>()
defineEmits<{ history: [config: Rec, metric: string, block: Rec]; point: [point: Rec, block: Rec] }>()

const open = ref(props.defaultOpen ?? false)
const device = computed(() => String(props.section.config.device || ''))
const color = computed(() => deviceColor(device.value))
const title = computed(() => perfConfigTitle(props.section.config))
const picked = ref<string | null>(null)
const selected = computed(() => props.section.tiles.find((tile) => tile.name === picked.value) ?? props.section.tiles[0])
const regressed = computed(() => props.section.tiles.filter((tile) => tile.tone === 'hard').length)
const improved = computed(() => props.section.tiles.filter((tile) => tile.tone === 'pass').length)
const contentId = computed(() => `perf-config-${props.section.config.device}-${props.section.config.conc}-${props.section.config.isl}-${props.section.config.osl}`.replace(/\W+/g, '-'))
</script>

<template>
  <Card class="gap-0 py-0 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300" data-testid="perf-config" :data-open="open">
    <button
      type="button"
      class="group/config flex w-full flex-wrap items-center gap-x-3 gap-y-2 rounded-xl px-4 py-4 text-left transition-colors outline-none hover:bg-muted/40 focus-visible:ring-3 focus-visible:ring-ring/50 sm:px-6"
      :aria-expanded="open"
      :aria-controls="contentId"
      @click="open = !open"
    >
      <span class="flex min-w-0 basis-full flex-col gap-0.5 sm:basis-0 sm:flex-1">
        <span class="font-semibold" data-testid="perf-config-label">{{ title }}</span>
        <span class="text-sm text-muted-foreground" data-testid="perf-config-meta">{{ section.meta }}</span>
      </span>
      <span class="flex w-full flex-wrap items-center gap-2 sm:w-auto">
        <Badge v-if="regressed" variant="outline" :class="toneClasses('hard', 'badge')">{{ regressed }} regressed</Badge>
        <Badge v-if="improved" variant="outline" :class="toneClasses('pass', 'text')">{{ improved }} improved</Badge>
        <span class="text-xs text-muted-foreground tabular-nums">{{ section.tiles.length }} metrics</span>
        <HwDot v-if="device" :family="device" :label="device.toUpperCase()" />
        <ChevronDown class="ml-auto size-4 text-muted-foreground transition-transform group-hover/config:text-foreground" :class="open && 'rotate-180'" aria-hidden="true" />
      </span>
    </button>
    <CardContent v-show="open" :id="contentId" class="flex flex-col gap-5 border-t px-4 pt-4 pb-5 sm:px-6">
      <section v-if="selected && open" class="flex min-w-0 flex-col gap-2 rounded-lg border bg-muted/20 p-3 sm:p-4" :aria-label="`${selected.label} history`">
        <PerfSeriesChart
          :series="selected.block.series || []"
          :unit="selected.block.unit"
          :label="selected.label"
          :color="color"
          class="h-[200px]"
          @select="(point) => $emit('point', point, selected!.block)"
        >
          <template #header>
            <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span class="text-sm font-medium">{{ selected.label }}</span>
              <span class="text-lg font-semibold tabular-nums">{{ selected.value }}</span>
              <DeltaBadge :delta="blockDelta(selected.block)" />
              <span class="text-xs text-muted-foreground">{{ selected.direction }}</span>
              <Button variant="outline" size="sm" class="h-7" @click="$emit('history', section.config, selected.name, selected.block)">
                <History /> Open history
              </Button>
            </div>
          </template>
        </PerfSeriesChart>
      </section>
      <div class="grid grid-cols-2 gap-3 @3xl/main:grid-cols-3 @5xl/main:grid-cols-4 @7xl/main:grid-cols-6" role="group" :aria-label="`${title} metrics`">
        <PerfMetricTile
          v-for="tile in section.tiles"
          :key="tile.name"
          :tile="tile"
          :color="color"
          :selected="tile.name === selected?.name"
          @select="picked = tile.name"
        />
      </div>
    </CardContent>
  </Card>
</template>
