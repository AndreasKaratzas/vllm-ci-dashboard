<script setup lang="ts">
import { createReusableTemplate } from '@vueuse/core'
import { computed } from 'vue'
import InfoPopover from '@/components/feedback/InfoPopover.vue'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { toneClasses } from '@/lib/palette'
import type { Tone } from '@/lib/tone'

export interface Freshness {
  text: string
  tone: Tone
  detail?: string
}

const props = defineProps<{ title: string; description?: string; freshness?: Freshness }>()

const FRESHNESS_STATE: Partial<Record<Tone, string>> = { pass: 'Within target', soft: 'Past target', hard: 'Stale' }
const state = computed(() => (props.freshness && FRESHNESS_STATE[props.freshness.tone]) ?? 'No freshness target')
const [DefineChip, Chip] = createReusableTemplate()
</script>

<template>
  <DefineChip>
    <span class="size-2 rounded-full" :class="freshness && toneClasses(freshness.tone, 'fill')" aria-hidden="true" />
    {{ freshness?.text }}<span class="sr-only">: {{ state }}</span>
  </DefineChip>
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
      <Popover v-if="freshness?.detail">
        <PopoverTrigger as-child>
          <Button variant="outline" size="sm" class="h-8 gap-1.5 px-2.5 font-normal text-muted-foreground" data-slot="freshness" :data-tone="freshness.tone">
            <Chip />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" class="w-[min(20rem,calc(100vw-2rem))] text-sm" data-slot="freshness-detail">
          <p class="mb-1 font-medium" :class="toneClasses(freshness.tone, 'text')">{{ state }}</p>
          <p class="text-muted-foreground">{{ freshness.detail }}</p>
        </PopoverContent>
      </Popover>
      <Badge v-else-if="freshness" variant="outline" class="h-8 gap-1.5 px-2.5 font-normal text-muted-foreground" data-slot="freshness" :data-tone="freshness.tone">
        <Chip />
      </Badge>
      <slot name="actions" />
    </div>
  </header>
</template>
