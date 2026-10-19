<script setup lang="ts">
import InfoPopover from '@/components/feedback/InfoPopover.vue'
import { Badge } from '@/components/ui/badge'
import { toneClasses } from '@/lib/palette'
import type { Tone } from '@/lib/tone'

export interface Freshness {
  text: string
  tone: Tone
  detail?: string
}

defineProps<{ title: string; description?: string; freshness?: Freshness }>()
</script>

<template>
  <header class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between" data-slot="page-header">
    <div class="flex min-w-0 flex-col gap-1">
      <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h1 class="text-2xl font-semibold tracking-tight">{{ title }}</h1>
        <InfoPopover v-if="$slots.about" :title="`About ${title}`">
          <slot name="about" />
        </InfoPopover>
      </div>
      <p v-if="description" class="max-w-3xl text-sm text-muted-foreground">{{ description }}</p>
    </div>
    <div v-if="freshness || $slots.actions" class="flex shrink-0 flex-wrap items-center gap-2">
      <Badge v-if="freshness" variant="outline" class="h-8 gap-1.5 px-2.5 font-normal text-muted-foreground" :title="freshness.detail" data-slot="freshness" :data-tone="freshness.tone">
        <span class="size-2 rounded-full" :class="toneClasses(freshness.tone, 'fill')" aria-hidden="true" />
        {{ freshness.text }}
      </Badge>
      <slot name="actions" />
    </div>
  </header>
</template>
