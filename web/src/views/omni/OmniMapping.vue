<script setup lang="ts">
import { Info, TriangleAlert } from '@lucide/vue'
import { computed } from 'vue'
import OmniBarChart from '@/components/omni/OmniBarChart.vue'
import OmniTable from '@/components/omni/OmniTable.vue'
import KpiCard from '@/components/ops/KpiCard.vue'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import type { Tone } from '@/domain/format'
import { OMNI_MAPPING_WINDOWS, type OmniDetail, type OmniView } from '@/domain/omni'

const props = defineProps<{ view: OmniView }>()
const emit = defineEmits<{ open: [OmniDetail]; range: [string] }>()

const KPI_ACTIONS: Record<string, string> = {
  'omni-mapped-jobs': 'Inspect mapping breakdown',
  'omni-gpu-demand': 'Inspect GPU demand',
  'omni-mapped-share': 'Inspect traffic share',
  'omni-retiring-share': 'Inspect MI325 exposure',
}
const windowLabel = computed(() => OMNI_MAPPING_WINDOWS.find((w) => w.id === props.view.mappingRange)!.label)
const range = computed({
  get: () => props.view.mappingRange,
  set: (id) => id && emit('range', String(id)),
})
const noteClass = computed(() =>
  props.view.scopeNote.tone === 'info' ? 'border-info/40 bg-info/10' : 'border-warning/40 bg-warning/10',
)
const kpiTone = (tone: Tone | '') => (tone || 'neutral') as Tone
</script>

<template>
  <section class="flex flex-col gap-4 md:gap-6" aria-label="Incoming Omni workload">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="flex max-w-full min-w-0 flex-wrap items-center gap-3">
        <span class="text-sm font-medium">Incoming workload</span>
        <div class="max-w-full overflow-x-auto">
          <ToggleGroup v-model="range" type="single" variant="outline" size="sm" aria-label="Filter unique Omni mappings by time window" data-testid="omni-mapping-range">
            <ToggleGroupItem v-for="w in OMNI_MAPPING_WINDOWS" :key="w.id" :value="w.id" :aria-label="w.label">{{ w.label }}</ToggleGroupItem>
          </ToggleGroup>
        </div>
      </div>
      <Button variant="outline" size="sm" @click="emit('open', view.methodology())">Scope &amp; coverage</Button>
    </div>

    <div class="grid grid-cols-1 gap-4 @xl/main:grid-cols-2 @5xl/main:grid-cols-4">
      <KpiCard
        v-for="kpi in view.kpis"
        :key="kpi.id"
        :label="kpi.label"
        :value="kpi.value"
        :badge="windowLabel"
        :badge-tone="kpiTone(kpi.tone)"
        :headline="KPI_ACTIONS[kpi.id]"
        :detail="kpi.meta"
        :data-testid="`omni-kpi-${kpi.id}`"
        :data-tone="kpi.tone"
        @open="emit('open', kpi.open())"
      />
    </div>

    <div class="flex gap-3 rounded-lg border p-4 text-sm" :class="noteClass" :data-tone="view.scopeNote.tone" data-testid="omni-scope-note">
      <Info v-if="view.scopeNote.tone === 'info'" class="mt-0.5 size-4 shrink-0 text-info" />
      <TriangleAlert v-else class="mt-0.5 size-4 shrink-0 text-warning" />
      <div class="flex flex-col gap-1.5">
        <strong class="font-semibold">{{ view.scopeNote.heading.trim() }}</strong>
        <span class="text-muted-foreground">{{ view.scopeNote.text }}</span>
        <div class="flex flex-wrap gap-4">
          <button type="button" class="w-fit font-medium underline underline-offset-4 hover:text-foreground/80" @click="emit('open', view.scopeNote.methodology())">Inspect methodology</button>
          <button v-if="view.scopeNote.excluded" type="button" class="w-fit font-medium underline underline-offset-4 hover:text-foreground/80" @click="emit('open', view.scopeNote.excluded!())">
            Inspect excluded stale jobs
          </button>
        </div>
      </div>
    </div>

    <div class="grid gap-4 md:gap-6 @5xl/main:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
      <Card data-testid="omni-volume">
        <CardHeader>
          <CardTitle>{{ view.volume.title }}</CardTitle>
          <CardDescription>{{ view.volume.meta }}</CardDescription>
          <CardAction v-if="view.volume.available">
            <Button variant="ghost" size="sm" @click="view.volume.available && emit('open', view.volume.evidence())">
              Inspect {{ view.volume.bars.length }} observations
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent class="px-2 sm:px-6">
          <OmniBarChart v-if="view.volume.available" :bars="view.volume.bars" value-label="Observed mapped jobs" @select="(bar) => emit('open', bar.open())" />
          <p v-else class="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground" data-testid="omni-volume-empty">{{ view.volume.empty }}</p>
        </CardContent>
      </Card>

      <Card data-testid="omni-impact">
        <CardHeader>
          <CardTitle>{{ view.impact.title }}</CardTitle>
          <CardDescription>{{ view.impact.meta }}</CardDescription>
          <CardAction v-if="view.impact.available">
            <Button variant="ghost" size="sm" @click="view.impact.available && emit('open', view.impact.evidence())">
              Inspect {{ view.impact.bars.length }} observations
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent v-if="view.impact.available" class="flex flex-col gap-4">
          <OmniBarChart horizontal legend :bars="view.impact.bars" value-label="Observed mapped jobs" @select="(bar) => emit('open', bar.open())" />
          <OmniTable :table="view.impact.preview" data-testid="omni-impact-preview" @open="(d) => emit('open', d)" />
        </CardContent>
        <CardContent v-else>
          <p class="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground" data-testid="omni-impact-empty">{{ view.impact.empty }}</p>
        </CardContent>
      </Card>
    </div>

    <Card data-testid="omni-comparison">
      <CardHeader>
        <CardTitle>{{ view.comparison.title }}</CardTitle>
        <CardDescription>{{ view.comparison.meta }}</CardDescription>
        <CardAction class="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" @click="emit('open', view.comparison.browseBuckets())">Inspect time buckets</Button>
          <Button v-if="view.comparison.browseQueues" variant="outline" size="sm" @click="emit('open', view.comparison.browseQueues!())">Browse all queues</Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        <OmniTable :table="view.comparison.table" @open="(d) => emit('open', d)" />
      </CardContent>
    </Card>
  </section>
</template>
