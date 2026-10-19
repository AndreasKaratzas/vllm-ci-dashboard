<script setup lang="ts">
import ToneBadge from '@/components/ops/ToneBadge.vue'
import PerfMetricTile from '@/components/perf/PerfMetricTile.vue'
import { Badge } from '@/components/ui/badge'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { Rec } from '@/domain/format'
import type { PerfModelSection } from '@/domain/perf'

defineProps<{ sections: PerfModelSection[] }>()
defineEmits<{ model: [Rec]; history: [Rec, Rec, string, Rec]; point: [Rec, unknown] }>()
</script>

<template>
  <div class="flex flex-col gap-6">
    <section v-for="section in sections" :key="section.model.model" class="flex flex-col gap-4" data-testid="perf-model-section">
      <Card>
        <CardHeader>
          <CardTitle class="flex flex-wrap items-center gap-2 text-xl">
            <button type="button" class="text-left hover:underline" data-testid="perf-model-title" @click="$emit('model', section.model)">
              {{ section.model.model }}
            </button>
            <ToneBadge v-for="device in section.devices" :key="device" tone="is-info">{{ device }}</ToneBadge>
          </CardTitle>
          <CardDescription class="flex flex-wrap items-center gap-x-3 gap-y-1" data-testid="perf-provenance">
            <a v-if="section.provenance.commit" :href="section.provenance.commit.url" target="_blank" rel="noopener" class="font-mono hover:underline">{{ section.provenance.commit.label }}</a>
            <a v-if="section.provenance.build && section.provenance.build.url" :href="section.provenance.build.url" target="_blank" rel="noopener" class="hover:underline">{{ section.provenance.build.label }}</a>
            <span v-else-if="section.provenance.build">{{ section.provenance.build.label }}</span>
            <span v-if="section.provenance.date">{{ section.provenance.date }}</span>
            <code v-if="section.provenance.image" class="rounded bg-muted px-1.5 py-0.5 font-mono text-xs break-all">{{ section.provenance.image }}</code>
          </CardDescription>
          <CardAction>
            <Badge variant="outline" data-testid="perf-nightlies">{{ section.nightlies }}</Badge>
          </CardAction>
        </CardHeader>
      </Card>
      <p v-if="!section.configs.length" class="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
        No performance configuration matches this hardware filter.
      </p>
      <section v-for="(config, index) in section.configs" :key="index" class="flex flex-col gap-3" data-testid="perf-config">
        <header class="flex flex-wrap items-baseline justify-between gap-2">
          <h3 class="text-base font-semibold" data-testid="perf-config-label">{{ config.label }}</h3>
          <span class="text-xs text-muted-foreground" data-testid="perf-config-meta">{{ config.meta }}</span>
        </header>
        <div class="grid grid-cols-1 gap-3 @2xl/main:grid-cols-2 @4xl/main:grid-cols-3 @5xl/main:grid-cols-4">
          <PerfMetricTile
            v-for="tile in config.tiles"
            :key="tile.name"
            :tile="tile"
            @open="$emit('history', section.model, config.config, tile.name, tile.block)"
            @point="(point) => $emit('point', point, tile.block.unit)"
          />
        </div>
      </section>
    </section>
  </div>
</template>
