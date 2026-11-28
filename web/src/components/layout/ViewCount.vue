<script setup lang="ts">
import { Eye } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { fetchWeeklyViews, VIEW_COUNTER } from '@/data/viewCounter'

const props = defineProps<{ origin?: string | null }>()
const origin = computed(() => (props.origin === undefined ? VIEW_COUNTER : props.origin))
const state = ref<'loading' | 'ready' | 'unavailable'>('loading')
const count = ref<string | null>(null)

const title = computed(() => {
  if (state.value === 'loading') return 'Loading page views…'
  if (state.value === 'unavailable')
    return 'Page view count unavailable (goatcounter.com may be blocked by an ad or privacy blocker)'
  return 'Page views over the last 7 days, counted by GoatCounter (no cookies); refreshed every few hours'
})

onMounted(async () => {
  if (!origin.value) return
  count.value = await fetchWeeklyViews(origin.value)
  state.value = count.value ? 'ready' : 'unavailable'
})
</script>

<template>
  <span
    v-if="origin"
    class="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border bg-background px-2.5 text-sm text-muted-foreground shadow-xs dark:border-input dark:bg-input/30"
    data-slot="view-count"
    :data-state="state"
    :title="title"
    :aria-label="title"
  >
    <Eye class="size-3.5 shrink-0" aria-hidden="true" />
    <span>
      <span class="text-foreground tabular-nums">{{ count ?? '—' }}</span>
      <span class="hidden lg:inline"> views this week</span>
    </span>
  </span>
</template>
