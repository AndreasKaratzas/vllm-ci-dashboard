<script setup lang="ts">
import { ExternalLink } from '@lucide/vue'
import { computed } from 'vue'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { OMNI_RANGE_WINDOWS, type OmniDetail, type OmniView } from '@/domain/omni'

const props = defineProps<{ view: OmniView }>()
const emit = defineEmits<{ open: [OmniDetail]; range: [string] }>()

const range = computed({
  get: () => props.view.omniRange,
  set: (id) => id && emit('range', String(id)),
})
// Classic marks warning and success facts with a coloured top rule.
const ACCENT: Record<string, string> = {
  'is-warning': 'border-t-2 border-t-warning',
  'is-success': 'border-t-2 border-t-success',
  '': '',
}
</script>

<template>
  <Card data-testid="omni-live">
    <CardHeader>
      <CardTitle>{{ view.live.title }}</CardTitle>
      <CardDescription>{{ view.live.meta }}</CardDescription>
    </CardHeader>
    <CardContent class="flex flex-col gap-4">
      <div class="grid grid-cols-1 gap-3 @xl/main:grid-cols-2 @5xl/main:grid-cols-4">
        <button
          v-for="fact in view.live.facts"
          :key="fact.label"
          type="button"
          class="flex flex-col gap-1 rounded-lg border bg-card p-4 text-left transition-colors hover:bg-accent"
          :class="ACCENT[fact.tone]"
          :data-tone="fact.tone"
          data-testid="omni-live-fact"
          :aria-label="`${fact.label}: ${fact.value}. ${fact.meta}`"
          @click="emit('open', fact.open())"
        >
          <span class="text-xs font-medium tracking-wide text-muted-foreground">{{ fact.label }}</span>
          <strong class="text-2xl font-semibold tabular-nums">{{ fact.value }}</strong>
          <span class="text-xs text-muted-foreground">{{ fact.meta }}</span>
        </button>
      </div>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <ToggleGroup v-model="range" type="single" variant="outline" size="sm" aria-label="Filter live Omni occupancy context by time window" data-testid="omni-range">
          <ToggleGroupItem v-for="w in OMNI_RANGE_WINDOWS" :key="w.id" :value="w.id" :aria-label="w.label">{{ w.label }}</ToggleGroupItem>
        </ToggleGroup>
        <div class="flex flex-wrap gap-2" data-testid="omni-live-actions">
          <Button v-for="item in view.live.actions" :key="item.label" variant="outline" size="sm" @click="emit('open', item.open())">{{ item.label }}</Button>
          <Button variant="ghost" size="sm" as="a" :href="view.live.queueHistoryUrl" target="_blank" rel="noopener">
            Inspect published queue history <ExternalLink />
          </Button>
        </div>
      </div>
    </CardContent>
  </Card>
</template>
