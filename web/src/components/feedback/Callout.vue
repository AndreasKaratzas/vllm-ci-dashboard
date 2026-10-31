<script setup lang="ts">
import { TONE_ICON, toneClasses } from '@/lib/palette'
import type { Tone } from '@/lib/tone'
import { cn } from '@/lib/utils'

withDefaults(defineProps<{ tone?: Tone; title?: string }>(), { tone: 'neutral' })
</script>

<template>
  <div role="note" :data-tone="tone" :class="cn('flex flex-wrap items-start gap-x-3 gap-y-2 rounded-lg border px-4 py-3 text-sm sm:flex-nowrap', toneClasses(tone, 'surface'))">
    <component :is="TONE_ICON[tone]" class="mt-0.5 size-4 shrink-0" :class="toneClasses(tone, 'icon')" aria-hidden="true" />
    <div class="flex min-w-0 flex-1 flex-col gap-1">
      <p v-if="title" class="font-medium" :class="tone === 'soft' || tone === 'hard' ? toneClasses(tone, 'text') : 'text-foreground'">{{ title }}</p>
      <div class="text-foreground/80 [&_a]:font-medium [&_a]:text-foreground [&_a]:underline-offset-4 [&_a:hover]:underline"><slot /></div>
    </div>
    <div v-if="$slots.action" class="w-full shrink-0 pl-7 sm:w-auto sm:self-center sm:pl-0"><slot name="action" /></div>
  </div>
</template>
