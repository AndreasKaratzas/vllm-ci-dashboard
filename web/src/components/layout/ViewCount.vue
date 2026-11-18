<script setup lang="ts">
import { Eye } from '@lucide/vue'
import { onMounted, ref } from 'vue'
import { fetchWeeklyViews, VIEW_COUNTER } from '@/data/viewCounter'

const props = defineProps<{ origin?: string | null }>()
const count = ref<string | null>(null)

onMounted(async () => {
  const origin = props.origin === undefined ? VIEW_COUNTER : props.origin
  if (origin) count.value = await fetchWeeklyViews(origin)
})
</script>

<template>
  <span
    v-if="count"
    class="hidden h-8 shrink-0 items-center gap-1.5 rounded-md border bg-background px-2.5 text-sm text-muted-foreground shadow-xs lg:inline-flex dark:border-input dark:bg-input/30"
    data-slot="view-count"
    title="Page views over the last 7 days, counted by GoatCounter (no cookies); refreshed every few hours"
  >
    <Eye class="size-3.5 shrink-0" aria-hidden="true" />
    <span><span class="text-foreground tabular-nums">{{ count }}</span> views this week</span>
  </span>
</template>
