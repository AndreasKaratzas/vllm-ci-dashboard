<script setup lang="ts">
import { useDocumentVisibility, useIntervalFn } from '@vueuse/core'
import { TriangleAlert } from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { dataUrl } from '@/data/fetch'
import { statusDetails, statusUrl, viewFor } from '@/data/publicationStatus'

const REFRESH_INTERVAL_MS = 5 * 60 * 1000
const REQUEST_TIMEOUT_MS = 30 * 1000
const payload = ref<unknown>(undefined)
let serial = 0

async function load() {
  const request = ++serial
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(dataUrl(statusUrl()), { cache: 'no-store', signal: controller.signal })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const body = await response.json()
    if (request === serial) payload.value = body
  } catch {
    if (request === serial) payload.value = { mode: 'unavailable', status: 'unknown' }
  } finally {
    clearTimeout(timeout)
  }
}

const visibility = useDocumentVisibility()
onMounted(load)
useIntervalFn(load, REFRESH_INTERVAL_MS)
watch(visibility, (state) => state === 'visible' && load())

const view = computed(() => (payload.value === undefined ? null : viewFor(payload.value)))
const details = computed(() => statusDetails(payload.value))
const REPO = 'https://github.com/AndreasKaratzas/vllm-ci-dashboard'
</script>

<template>
  <Alert
    v-if="view"
    :variant="view.tone === 'critical' ? 'destructive' : 'default'"
    :role="view.tone === 'critical' ? 'alert' : 'status'"
    :class="view.tone === 'warning' ? 'border-warning/50 text-warning' : ''"
    data-testid="publication-banner"
  >
    <TriangleAlert />
    <AlertTitle>{{ view.title }}</AlertTitle>
    <AlertDescription>
      <p>{{ view.message }}</p>
      <p v-if="details.length" class="text-xs opacity-80">{{ details.join(' ') }}</p>
      <p class="text-xs">
        <a class="underline" :href="`${REPO}/issues?q=is%3Aissue%20is%3Aopen%20%22Hourly%20validation%20failure%22`">Refresh failure details</a>
        ·
        <a class="underline" :href="`${REPO}/actions/workflows/hourly-master.yml`">Refresh history</a>
      </p>
    </AlertDescription>
  </Alert>
</template>
