<script setup lang="ts">
import { TONE_ICON, toneClasses } from '@/lib/palette'
import type { Tone } from '@/lib/tone'
import { cn } from '@/lib/utils'

withDefaults(defineProps<{ tone?: Tone; title?: string }>(), { tone: 'neutral' })
</script>

<template>
  <div role="note" :data-tone="tone" :class="cn('flex items-start gap-3 rounded-lg border px-4 py-3 text-sm', toneClasses(tone, 'surface'))">
    <component :is="TONE_ICON[tone]" class="mt-0.5 size-4 shrink-0" :class="toneClasses(tone, 'icon')" aria-hidden="true" />
    <div class="flex min-w-0 flex-1 flex-col gap-1">
      <p v-if="title" class="font-medium" :class="tone === 'soft' || tone === 'hard' ? toneClasses(tone, 'text') : 'text-foreground'">{{ title }}</p>
      <div class="text-muted-foreground [&_a]:font-medium [&_a]:text-foreground [&_a]:underline-offset-4 [&_a:hover]:underline"><slot /></div>
    </div>
    <div v-if="$slots.action" class="shrink-0 self-center"><slot name="action" /></div>
  </div>
</template>
