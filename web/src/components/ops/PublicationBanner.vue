<script setup lang="ts">
import { TriangleAlert } from '@lucide/vue'
import { computed } from 'vue'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
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
  <Alert
    v-if="view"
    :role="view.tone === 'critical' ? 'alert' : 'status'"
    :class="
      view.tone === 'critical'
        ? 'border-status-hard/30 bg-status-hard/8 text-status-hard-fg *:data-[slot=alert-description]:text-status-hard-fg'
        : view.tone === 'warning'
          ? 'border-status-soft/40 bg-status-soft/8 text-status-soft-fg *:data-[slot=alert-description]:text-status-soft-fg'
          : ''
    "
    data-testid="publication-banner"
  >
    <TriangleAlert />
    <AlertTitle>{{ view.title }}</AlertTitle>
    <AlertDescription>
      <p>{{ view.message }}</p>
      <p v-if="details.length" class="text-xs">{{ details.join(' ') }}</p>
      <p class="text-xs">
        <a class="underline" :href="`${REPO}/issues?q=is%3Aissue%20is%3Aopen%20%22Hourly%20validation%20failure%22`">Refresh failure details</a>
        ·
        <a class="underline" :href="`${REPO}/actions/workflows/hourly-master.yml`">Refresh history</a>
      </p>
    </AlertDescription>
  </Alert>
</template>
