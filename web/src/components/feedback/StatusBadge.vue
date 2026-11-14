<script setup lang="ts">
import { computed } from 'vue'
import { Badge } from '@/components/ui/badge'
import { TONE_ICON, TONE_LABEL, toneClasses } from '@/lib/palette'
import type { Tone } from '@/lib/tone'
import { cn } from '@/lib/utils'

const props = defineProps<{ tone: Tone; label?: string }>()

const text = computed(() => props.label || TONE_LABEL[props.tone])
</script>

<template>
  <Badge variant="outline" :class="cn('gap-1 font-medium', toneClasses(tone, 'badge'))" :data-tone="tone">
    <component :is="TONE_ICON[tone]" :class="toneClasses(tone, 'icon')" aria-hidden="true" />
    <span>{{ text }}</span>
  </Badge>
</template>
