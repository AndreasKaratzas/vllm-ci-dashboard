<script setup lang="ts">
import { computed } from 'vue'
import Callout from '@/components/feedback/Callout.vue'
import { useDataset } from '@/composables/useDataset'
import { statusDetails, viewFor } from '@/data/publicationStatus'

const { data, error } = useDataset('publication_status')
// An unreadable status file is itself a reason to warn, even when an older status loaded.
const payload = computed<unknown>(() => (error.value ? { mode: 'unavailable', status: 'unknown' } : (data.value ?? undefined)))

const view = computed(() => (payload.value === undefined ? null : viewFor(payload.value)))
const details = computed(() => statusDetails(payload.value))
const REPO = 'https://github.com/AndreasKaratzas/vllm-ci-dashboard'
</script>

<template>
  <Callout
    v-if="view"
    :tone="view.tone === 'critical' ? 'hard' : 'soft'"
    :title="view.title"
    :role="view.tone === 'critical' ? 'alert' : 'status'"
    class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-top-1 motion-safe:duration-300"
    data-testid="publication-banner"
  >
    <p>{{ view.message }}</p>
    <p v-if="details.length" class="text-xs">{{ details.join(' ') }}</p>
    <p class="flex flex-wrap gap-x-3 gap-y-1 text-xs">
      <a :href="`${REPO}/issues?q=is%3Aissue%20is%3Aopen%20%22Hourly%20validation%20failure%22`">Refresh failure details</a>
      <a :href="`${REPO}/actions/workflows/hourly-master.yml`">Refresh history</a>
    </p>
  </Callout>
</template>
