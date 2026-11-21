<script setup lang="ts">
import { computed } from 'vue'
import { observationState, observationTimestamp, isIncidentObservation } from '@/domain/ciHealth'
import { shortDate, type Rec } from '@/domain/format'
import { exactPipelineEvidenceUrl } from '@/domain/urls'
import { statusVar } from '@/lib/palette'

const props = withDefaults(defineProps<{ observations: Rec[]; pipeline?: string }>(), { pipeline: 'ci' })

const SOFT = ['soft', 'soft_fail', 'soft_failed']

const cells = computed(() =>
  props.observations.map((observation) => {
    const state = observationState(observation)
    const tone = state === 'passed' ? 'pass' : SOFT.includes(state) ? 'soft' : isIncidentObservation(observation) ? 'hard' : 'neutral'
    const when = shortDate(observationTimestamp(observation))
    const build = observation.build_number ? `#${observation.build_number}` : ''
    return {
      url: exactPipelineEvidenceUrl(observation, props.pipeline) || undefined,
      color: statusVar(tone),
      label: [build, state.replaceAll('_', ' '), when].filter(Boolean).join(' · '),
    }
  }),
)
</script>

<template>
  <div class="flex flex-wrap gap-1" data-testid="outcome-strip">
    <component
      :is="cell.url ? 'a' : 'span'"
      v-for="(cell, index) in cells"
      :key="index"
      :href="cell.url"
      :target="cell.url ? '_blank' : undefined"
      :rel="cell.url ? 'noopener' : undefined"
      :aria-label="cell.url ? `Open job ${cell.label}` : cell.label"
      :title="cell.label"
      :role="cell.url ? undefined : 'img'"
      class="size-4 rounded-[3px] outline-none transition-transform focus-visible:ring-3 focus-visible:ring-ring/50 motion-safe:hover:scale-125"
      :style="{ backgroundColor: cell.color }"
    />
  </div>
</template>
