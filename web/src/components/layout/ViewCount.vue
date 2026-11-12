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
  <p
    v-if="count"
    class="flex items-center gap-2 px-2 text-xs text-muted-foreground group-data-[collapsible=icon]:hidden"
    data-slot="view-count"
    title="Page views over the last 7 days, counted by GoatCounter (no cookies); refreshed every few hours"
  >
    <Eye class="size-3.5" aria-hidden="true" />
    <span class="tabular-nums">{{ count }} views this week</span>
  </p>
</template>
