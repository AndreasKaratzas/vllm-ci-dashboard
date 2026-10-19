<script setup lang="ts">
import { Inbox } from '@lucide/vue'
import type { Component } from 'vue'

withDefaults(defineProps<{ title: string; description?: string; cause?: string; icon?: Component }>(), { icon: () => Inbox })
</script>

<template>
  <div class="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed px-6 py-10 text-center" data-slot="empty-state">
    <div class="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
      <component :is="icon" class="size-5" aria-hidden="true" />
    </div>
    <div class="flex max-w-md flex-col gap-1">
      <p class="text-sm font-medium">{{ title }}</p>
      <p v-if="description" class="text-sm text-muted-foreground">{{ description }}</p>
    </div>
    <span v-if="cause" class="rounded-md bg-muted px-2 py-0.5 font-mono text-xs text-foreground/80">{{ cause }}</span>
    <slot name="action" />
  </div>
</template>
