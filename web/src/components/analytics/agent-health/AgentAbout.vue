<script setup lang="ts">
import type { AgentModel } from '@/domain/agentHealth'

defineProps<{ model: AgentModel }>()
</script>

<template>
  <p>
    Terminal runs from CI main builds on AMD MI GPU hardware are attributed to the physical node from the Buildkite agent tag. The timeline and
    co-failure clustering follow the selected failure signal.
  </p>
  <p v-if="model.completionScopeNote" data-testid="agent-completion-scope">{{ model.completionScopeNote }}</p>
  <p class="font-medium text-foreground">What counts as an infra-suspect failure?</p>
  <p>A soft or hard failure is node-attributable when its exact test group, on that same {{ model.criteria.dayLabel }}, both:</p>
  <ol class="list-decimal pl-5">
    <li>passed at least {{ model.criteria.pct }} of its gradable runs (needs ≥ {{ model.criteria.samples }} graded samples), so the group works; and</li>
    <li>passed on at least one other physical node that day, so the failure is isolated to this machine rather than a broad code break.</li>
  </ol>
  <p>Groups that fail broadly and failures inside auto-cancelled or superseded builds are filtered out by default.</p>
</template>
