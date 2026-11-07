<script setup lang="ts">
import { CircleX, RefreshCw } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'

withDefaults(
  defineProps<{
    loading: boolean
    error: string | null
    refreshing?: boolean
    stale?: boolean
    skeleton?: 'page' | 'cards' | 'table' | 'chart'
  }>(),
  { skeleton: 'page' },
)
defineEmits<{ retry: []; refresh: [] }>()
</script>

<template>
  <div v-if="loading" class="flex flex-col gap-4" aria-busy="true" aria-live="polite" data-slot="async-loading">
    <span class="sr-only">Loading</span>
    <Skeleton v-if="skeleton === 'page'" class="h-10 w-72 max-w-full" />
    <div v-if="skeleton === 'page' || skeleton === 'cards'" class="grid grid-cols-2 gap-4 @xl/main:grid-cols-4">
      <Skeleton v-for="i in 4" :key="i" class="h-28" />
    </div>
    <Skeleton v-if="skeleton === 'page' || skeleton === 'chart'" class="h-64" />
    <div v-if="skeleton === 'table'" class="flex flex-col gap-2">
      <Skeleton v-for="i in 6" :key="i" class="h-9" />
    </div>
  </div>
  <div v-else-if="error" role="alert" class="flex flex-col items-start gap-3 rounded-lg border border-status-hard/30 bg-status-hard/8 p-6" data-slot="async-error">
    <p class="flex items-center gap-2 font-medium text-status-hard-fg">
      <CircleX class="size-4" aria-hidden="true" /> The data could not be loaded.
    </p>
    <p class="font-mono text-xs break-all text-muted-foreground">{{ error }}</p>
    <slot name="error" />
    <Button variant="outline" size="sm" @click="$emit('retry')"><RefreshCw /> Retry</Button>
  </div>
  <template v-else>
    <p v-if="refreshing" class="flex items-center gap-2 self-end text-xs text-muted-foreground" role="status" data-slot="async-refreshing">
      <RefreshCw class="size-3 motion-safe:animate-spin" aria-hidden="true" /> Refreshing
    </p>
    <p v-else-if="stale" class="flex items-center gap-2 self-end text-xs text-muted-foreground" role="status" data-slot="async-stale">
      Newer data is available.
      <Button variant="link" size="xs" class="h-auto p-0" @click="$emit('refresh')"><RefreshCw /> Update</Button>
    </p>
    <slot />
  </template>
</template>
