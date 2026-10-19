<script setup lang="ts">
import { age, shortDate } from '@/domain/format'

defineProps<{ title: string; description: string; generatedAt?: string }>()
</script>

<template>
  <div class="flex flex-wrap items-end justify-between gap-2">
    <div>
      <h1 class="text-2xl font-semibold tracking-tight">{{ title }}</h1>
      <p class="text-sm text-muted-foreground">{{ description }}</p>
    </div>
    <p v-if="generatedAt" class="text-xs text-muted-foreground" :title="generatedAt">
      Snapshot {{ shortDate(generatedAt) }} · {{ age(generatedAt) }}
    </p>
  </div>
</template>
