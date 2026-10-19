<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { Button } from '@/components/ui/button'
import type { NavTab } from '@/router'

const route = useRoute()
const tab = computed(() => route.meta.tab as NavTab)
</script>

<template>
  <div class="flex flex-col items-start gap-3 rounded-lg border p-6">
    <h1 class="text-2xl font-semibold tracking-tight">{{ tab.label }}</h1>
    <p class="text-sm text-muted-foreground">This view has not moved to the new dashboard yet. It is still available on the classic dashboard.</p>
    <Button as="a" :href="`../#${tab.classicTab}`">Open {{ tab.label }} on the classic dashboard</Button>
  </div>
</template>
