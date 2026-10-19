<script setup lang="ts">
import { computed } from 'vue'
import { TONE_LABEL, toneClasses } from '@/lib/palette'
import type { Tone } from '@/lib/tone'

const props = defineProps<{ tone: Tone; label?: string }>()

const text = computed(() => props.label || TONE_LABEL[props.tone])
</script>

<template>
  <span class="inline-flex items-center gap-1.5 text-sm" :data-tone="tone">
    <span class="size-2 shrink-0 rounded-full" :class="toneClasses(tone, 'fill')" aria-hidden="true" />
    <span :class="tone === 'pass' ? 'text-foreground' : toneClasses(tone, 'text')">{{ text }}</span>
  </span>
</template>
