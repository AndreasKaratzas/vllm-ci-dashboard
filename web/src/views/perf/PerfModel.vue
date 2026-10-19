<script setup lang="ts">
import { ExternalLink, ServerOff } from '@lucide/vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import HwDot from '@/components/feedback/HwDot.vue'
import { Badge } from '@/components/ui/badge'
import { Card, CardAction, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { Rec } from '@/domain/format'
import type { PerfModelSection } from '@/domain/perf'
import PerfConfigCard from './PerfConfigCard.vue'

defineProps<{ section: PerfModelSection }>()
defineEmits<{ model: [Rec]; history: [Rec, Rec, string, Rec]; point: [Rec, unknown] }>()
</script>

<template>
  <section class="flex min-w-0 flex-col gap-4 md:gap-6" data-testid="perf-model-section">
    <Card class="bg-linear-to-t from-brand/5 to-card">
      <CardHeader>
        <CardTitle class="flex flex-wrap items-center gap-2 text-xl">
          <button
            type="button"
            class="min-w-0 text-left break-words underline-offset-4 decoration-muted-foreground/50 hover:underline focus-visible:underline focus-visible:outline-none"
            data-testid="perf-model-title"
            @click="$emit('model', section.model)"
          >
            {{ section.model.model }}
          </button>
          <HwDot v-for="device in section.devices" :key="device" :family="device" :label="device" />
        </CardTitle>
        <CardDescription class="flex flex-wrap items-center gap-x-3 gap-y-1" data-testid="perf-provenance">
          <a v-if="section.provenance.commit" :href="section.provenance.commit.url" target="_blank" rel="noopener" class="inline-flex items-center gap-1 font-mono text-xs text-foreground underline-offset-4 hover:underline">{{ section.provenance.commit.label }}<ExternalLink class="size-3 text-muted-foreground" aria-hidden="true" /></a>
          <a v-if="section.provenance.build && section.provenance.build.url" :href="section.provenance.build.url" target="_blank" rel="noopener" class="inline-flex items-center gap-1 text-foreground underline-offset-4 hover:underline">{{ section.provenance.build.label }}<ExternalLink class="size-3 text-muted-foreground" aria-hidden="true" /></a>
          <span v-else-if="section.provenance.build">{{ section.provenance.build.label }}</span>
          <span v-if="section.provenance.date">{{ section.provenance.date }}</span>
          <code v-if="section.provenance.image" class="rounded bg-muted px-1.5 py-0.5 font-mono text-xs break-all text-foreground">{{ section.provenance.image }}</code>
        </CardDescription>
        <CardAction>
          <Badge variant="outline" class="tabular-nums" data-testid="perf-nightlies">{{ section.nightlies }}</Badge>
        </CardAction>
      </CardHeader>
    </Card>
    <EmptyState v-if="!section.configs.length" :icon="ServerOff" title="No performance configuration matches this hardware filter." />
    <PerfConfigCard
      v-for="(config, index) in section.configs"
      :key="index"
      :section="config"
      :style="{ animationDelay: `${index * 40}ms` }"
      @history="(cfg, metric, block) => $emit('history', section.model, cfg, metric, block)"
      @point="(point, unit) => $emit('point', point, unit)"
    />
  </section>
</template>
