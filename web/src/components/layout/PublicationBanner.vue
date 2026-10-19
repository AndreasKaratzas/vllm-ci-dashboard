<script setup lang="ts">
import { ExternalLink } from '@lucide/vue'
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
const LINKS = [
  { label: 'Refresh failure details', url: `${REPO}/issues?q=is%3Aissue%20is%3Aopen%20%22Hourly%20validation%20failure%22` },
  { label: 'Refresh history', url: `${REPO}/actions/workflows/hourly-master.yml` },
]
</script>

<template>
  <Callout
    v-if="view"
    tone="soft"
    :title="view.title"
    :role="view.tone === 'critical' ? 'alert' : 'status'"
    class="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-top-1 motion-safe:duration-300"
    data-testid="publication-banner"
  >
    <p>{{ view.message }}</p>
    <p v-if="details.length" class="hidden text-xs sm:block">{{ details.join(' ') }}</p>
    <p class="flex flex-wrap gap-x-4 gap-y-1 text-xs">
      <a
        v-for="link in LINKS"
        :key="link.label"
        :href="link.url"
        target="_blank"
        rel="noopener"
        class="inline-flex items-center gap-1 underline decoration-current/40 underline-offset-4 hover:decoration-current"
      >
        {{ link.label }} <ExternalLink class="size-3" aria-hidden="true" />
      </a>
    </p>
  </Callout>
</template>
