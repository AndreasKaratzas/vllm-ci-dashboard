<script setup lang="ts">
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'

defineProps<{ loading: boolean; error: string | null }>()
defineEmits<{ retry: [] }>()
</script>

<template>
  <div v-if="loading" class="flex flex-col gap-4" aria-busy="true">
    <Skeleton class="h-10 w-72" />
    <div class="grid gap-4 md:grid-cols-2"><Skeleton class="h-32" /><Skeleton class="h-32" /></div>
    <Skeleton class="h-64" />
  </div>
  <div v-else-if="error" class="flex flex-col items-start gap-3 rounded-lg border border-destructive/40 p-6" role="alert">
    <h2 class="text-lg font-semibold">The requested operational data could not be loaded.</h2>
    <p class="font-mono text-sm text-destructive">{{ error }}</p>
    <Button @click="$emit('retry')">Retry</Button>
  </div>
  <slot v-else />
</template>
