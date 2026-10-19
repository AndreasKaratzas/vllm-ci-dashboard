<script setup lang="ts">
import { History } from '@lucide/vue'
import { computed, ref } from 'vue'
import HwDot from '@/components/feedback/HwDot.vue'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { Rec } from '@/domain/format'
import type { PerfConfigSection } from '@/domain/perf'
import { deviceColor } from './details'
import PerfDeltaBadge from './PerfDeltaBadge.vue'
import PerfMetricTile from './PerfMetricTile.vue'
import PerfSeriesChart from './PerfSeriesChart.vue'

const props = defineProps<{ section: PerfConfigSection }>()
defineEmits<{ history: [config: Rec, metric: string, block: Rec]; point: [point: Rec, unit: unknown] }>()

const device = computed(() => String(props.section.config.device || ''))
const color = computed(() => deviceColor(device.value))
const picked = ref<string | null>(null)
const selected = computed(() => props.section.tiles.find((tile) => tile.name === picked.value) ?? props.section.tiles[0])
</script>

<template>
  <Card class="gap-5 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300" data-testid="perf-config">
    <CardHeader>
      <CardTitle class="text-base" data-testid="perf-config-label">{{ section.label }}</CardTitle>
      <CardDescription data-testid="perf-config-meta">{{ section.meta }}</CardDescription>
      <CardAction v-if="device"><HwDot :family="device" :label="device.toUpperCase()" /></CardAction>
    </CardHeader>
    <CardContent class="flex flex-col gap-5">
      <section v-if="selected" class="flex min-w-0 flex-col gap-2 rounded-lg border bg-muted/20 p-3 sm:p-4" :aria-label="`${selected.label} history`">
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div class="flex min-w-0 flex-col gap-0.5">
            <span class="text-sm font-medium">{{ selected.label }}</span>
            <span class="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <span class="text-lg font-semibold text-foreground tabular-nums">{{ selected.value }}</span>
              <PerfDeltaBadge :delta="selected.delta" />
              {{ selected.direction }}
            </span>
          </div>
          <Button variant="outline" size="sm" @click="$emit('history', section.config, selected.name, selected.block)">
            <History /> Open history
          </Button>
        </div>
        <PerfSeriesChart
          :series="selected.block.series || []"
          :unit="selected.block.unit"
          :label="selected.label"
          :color="color"
          class="h-[200px]"
          @select="(point) => $emit('point', point, selected?.block.unit)"
        />
      </section>
      <div class="grid grid-cols-2 gap-3 @3xl/main:grid-cols-3 @5xl/main:grid-cols-4 @7xl/main:grid-cols-6" role="group" :aria-label="`${section.label} metrics`">
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
